#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ADB="${ADB:-adb}"
PKG="${PDOCKER_PACKAGE:-io.github.ryo100794.pdocker.compat}"
CONTAINER="${PDOCKER_LLAMA_CONTAINER:-pdocker-llama-cpp}"
OUT="${PDOCKER_DEVICE_MEMORY_DIAGNOSTICS_OUT:-$ROOT/docs/test/android-device-memory-diagnostics-latest.json}"
PROCESS_LIMIT="${PDOCKER_DEVICE_MEMORY_PROCESS_LIMIT:-32}"

usage() {
  cat <<EOF_USAGE
Usage: $0 [--out PATH] [--package PACKAGE] [--container NAME] [--process-limit N]

Collects a lightweight Android device memory diagnostic JSON report.  This is a
read-only wrapper around adb shell probes: it does not start llama compare, does
not start pdockerd or containers, and does not force-stop user apps.

Environment:
  ADB                                      adb binary/path (default: adb)
  ANDROID_SERIAL                           passed through to adb, if set
  PDOCKER_PACKAGE                          app package for run-as probes
  PDOCKER_LLAMA_CONTAINER                  container name hint
  PDOCKER_DEVICE_MEMORY_DIAGNOSTICS_OUT    default output path
EOF_USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --out) OUT="$2"; shift ;;
    --package) PKG="$2"; shift ;;
    --container) CONTAINER="$2"; shift ;;
    --process-limit) PROCESS_LIMIT="$2"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

if ! [[ "$PROCESS_LIMIT" =~ ^[0-9]+$ ]]; then
  echo "--process-limit must be a non-negative integer" >&2
  exit 2
fi

TMP="${TMPDIR:-/tmp}/pdocker-device-memory-diagnostics.$$"
mkdir -p "$TMP"
cleanup() {
  rm -rf "$TMP"
}
trap cleanup EXIT

quote_for_device_sh() {
  local s
  s="$(printf "%s" "$1" | sed "s/'/'\\\\''/g")"
  printf "'%s'" "$s"
}

capture_shell() {
  local name="$1"
  local command="$2"
  "$ADB" shell "$command" >"$TMP/$name" 2>"$TMP/$name.err" || true
}

capture_run_as() {
  local name="$1"
  local command="$2"
  local quoted_pkg quoted_command
  quoted_pkg="$(quote_for_device_sh "$PKG")"
  quoted_command="$(quote_for_device_sh "$command")"
  "$ADB" shell "run-as $quoted_pkg sh -c $quoted_command" >"$TMP/$name" 2>"$TMP/$name.err" || true
}

capture_shell meminfo 'cat /proc/meminfo 2>/dev/null'
capture_shell free_m 'free -m 2>/dev/null || true'
capture_shell vmstat 'cat /proc/vmstat 2>/dev/null | egrep "^(pswpin|pswpout|pgmajfault|oom|allocstall|compact_|kswapd|pgscan|pgsteal)" || true'
capture_shell psi_memory 'cat /proc/pressure/memory 2>/dev/null || true'
capture_shell swaps_zram 'cat /proc/swaps 2>/dev/null || true; for z in /sys/block/zram*/mm_stat; do [ -e "$z" ] || continue; echo "--- $z"; cat "$z" 2>/dev/null || true; done; for z in /sys/block/zram*/stat; do [ -e "$z" ] || continue; echo "--- $z"; cat "$z" 2>/dev/null || true; done'
capture_shell ps_global 'ps -A -o PID,PPID,RSS,VSZ,NAME,ARGS 2>/dev/null || ps -A 2>/dev/null || true'
capture_shell proc_status 'for p in /proc/[0-9]*; do cmd=$(tr "\0" " " < "$p/cmdline" 2>/dev/null || true); case "$cmd" in *pdocker*|*llama*) rss=$(grep -m1 "^VmRSS:" "$p/status" 2>/dev/null || true); printf "%s %s %s\n" "${p##*/}" "$rss" "$cmd";; esac; done'
capture_run_as app_ps 'ps -A -o PID,PPID,RSS,VSZ,NAME,ARGS 2>/dev/null || ps -A 2>/dev/null || true'
capture_run_as app_socket 'cd files 2>/dev/null && if test -S pdocker/pdockerd.sock; then echo present; else echo absent; fi'

if [[ "$OUT" != "-" ]]; then
  mkdir -p "$(dirname "$OUT")"
fi

python3 - "$TMP" "$OUT" "$PKG" "$CONTAINER" "$PROCESS_LIMIT" <<'PY'
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

base = Path(sys.argv[1])
out = sys.argv[2]
pkg = sys.argv[3]
container = sys.argv[4]
process_limit = int(sys.argv[5])


def read(name: str) -> str:
    try:
        return (base / name).read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""


def kb_to_mb(value: int) -> int:
    return int(value) // 1024


def parse_meminfo(raw: str) -> dict[str, int]:
    fields = {
        "mem_total_mb": 0,
        "mem_free_mb": 0,
        "mem_available_mb": 0,
        "buffers_mb": 0,
        "cached_mb": 0,
        "swap_cached_mb": 0,
        "swap_total_mb": 0,
        "swap_free_mb": 0,
        "active_file_mb": 0,
        "inactive_file_mb": 0,
        "shmem_mb": 0,
        "slab_mb": 0,
        "s_reclaimable_mb": 0,
        "s_unreclaim_mb": 0,
    }
    key_map = {
        "MemTotal": "mem_total_mb",
        "MemFree": "mem_free_mb",
        "MemAvailable": "mem_available_mb",
        "Buffers": "buffers_mb",
        "Cached": "cached_mb",
        "SwapCached": "swap_cached_mb",
        "SwapTotal": "swap_total_mb",
        "SwapFree": "swap_free_mb",
        "Active(file)": "active_file_mb",
        "Inactive(file)": "inactive_file_mb",
        "Shmem": "shmem_mb",
        "Slab": "slab_mb",
        "SReclaimable": "s_reclaimable_mb",
        "SUnreclaim": "s_unreclaim_mb",
    }
    for line in raw.splitlines():
        m = re.match(r"^([A-Za-z_()]+):\s+([0-9]+)\s+kB$", line.strip())
        if not m:
            continue
        target = key_map.get(m.group(1))
        if target:
            fields[target] = kb_to_mb(int(m.group(2)))
    return fields


def parse_free(raw: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for line in raw.splitlines():
        parts = line.split()
        if not parts:
            continue
        label = parts[0].rstrip(":").lower()
        if label == "mem" and len(parts) >= 4:
            result["free_mem_total_mb"] = int(parts[1])
            result["free_mem_used_mb"] = int(parts[2])
            result["free_mem_free_mb"] = int(parts[3])
            if len(parts) >= 7:
                result["free_mem_available_mb"] = int(parts[6])
        elif label == "swap" and len(parts) >= 4:
            result["free_swap_total_mb"] = int(parts[1])
            result["free_swap_used_mb"] = int(parts[2])
            result["free_swap_free_mb"] = int(parts[3])
    return result


def parse_key_values(raw: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for line in raw.splitlines():
        parts = line.strip().split()
        if len(parts) == 2 and re.match(r"^[A-Za-z0-9_]+$", parts[0]):
            try:
                result[parts[0]] = int(parts[1])
            except ValueError:
                pass
    return result


def parse_psi(raw: str) -> dict[str, object]:
    result: dict[str, object] = {"raw": [line.strip() for line in raw.splitlines() if line.strip()]}
    for line in raw.splitlines():
        parts = line.split()
        if not parts:
            continue
        category = parts[0]
        values: dict[str, float | int] = {}
        for item in parts[1:]:
            if "=" not in item:
                continue
            key, value = item.split("=", 1)
            try:
                values[key] = float(value) if "." in value else int(value)
            except ValueError:
                values[key] = value
        if values:
            result[category] = values
    return result


def parse_process_rows(raw: str, source: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    header: list[str] = []
    for line in raw.splitlines():
        clean = line.strip().replace("\r", "")
        if not clean:
            continue
        parts = clean.split()
        upper = [p.upper() for p in parts]
        if "PID" in upper and ("RSS" in upper or "NAME" in upper or "ARGS" in upper):
            header = upper
            continue
        pid = None
        ppid = None
        rss_kb = None
        vsz_kb = None
        name = ""
        args = clean
        if header:
            def at(column: str) -> str | None:
                try:
                    idx = header.index(column)
                except ValueError:
                    return None
                return parts[idx] if len(parts) > idx else None
            pid = at("PID")
            ppid = at("PPID")
            name = at("NAME") or at("CMD") or at("COMMAND") or ""
            if "ARGS" in header:
                idx = header.index("ARGS")
                args = " ".join(parts[idx:]) if len(parts) > idx else clean
            elif "COMMAND" in header:
                idx = header.index("COMMAND")
                args = " ".join(parts[idx:]) if len(parts) > idx else clean
            elif name:
                args = name
            rss_value = at("RSS")
            vsz_value = at("VSZ") or at("VSS")
            try:
                rss_kb = int(re.sub(r"[^0-9]", "", rss_value or "") or "0")
            except ValueError:
                rss_kb = None
            try:
                vsz_kb = int(re.sub(r"[^0-9]", "", vsz_value or "") or "0")
            except ValueError:
                vsz_kb = None
        elif len(parts) >= 5:
            # Common toybox fallback: USER PID PPID VSZ RSS ... NAME
            pid = parts[1]
            ppid = parts[2]
            try:
                vsz_kb = int(re.sub(r"[^0-9]", "", parts[3]) or "0")
                rss_kb = int(re.sub(r"[^0-9]", "", parts[4]) or "0")
            except ValueError:
                rss_kb = None
            name = parts[-1]
            args = clean
        lowered = clean.lower()
        rows.append({
            "source": source,
            "pid": pid,
            "ppid": ppid,
            "name": name,
            "args": args[:500],
            "raw": clean[:500],
            "rss_mb": round(rss_kb / 1024.0, 1) if rss_kb is not None else None,
            "vsz_mb": round(vsz_kb / 1024.0, 1) if vsz_kb is not None else None,
            "pdocker_hint": "pdocker" in lowered or pkg.lower() in lowered,
            "llama_hint": "llama" in lowered or container.lower() in lowered,
        })
    return rows


def parse_proc_status(raw: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for line in raw.splitlines():
        clean = line.strip().replace("\r", "")
        if not clean:
            continue
        m = re.match(r"^(\d+)\s+VmRSS:\s+(\d+)\s+kB\s+(.*)$", clean)
        if m:
            rows.append({
                "source": "proc-status",
                "pid": m.group(1),
                "rss_mb": round(int(m.group(2)) / 1024.0, 1),
                "args": m.group(3)[:500],
                "raw": clean[:500],
                "pdocker_hint": "pdocker" in clean.lower() or pkg.lower() in clean.lower(),
                "llama_hint": "llama" in clean.lower() or container.lower() in clean.lower(),
            })
        else:
            rows.append({"source": "proc-status", "raw": clean[:500]})
    return rows


meminfo = parse_meminfo(read("meminfo"))
free_m = parse_free(read("free_m"))
if free_m:
    meminfo.update({k: v for k, v in free_m.items() if k not in meminfo or not meminfo.get(k)})
if not meminfo.get("mem_available_mb") and meminfo.get("free_mem_available_mb"):
    meminfo["mem_available_mb"] = meminfo["free_mem_available_mb"]
if not meminfo.get("swap_free_mb") and meminfo.get("free_swap_free_mb"):
    meminfo["swap_free_mb"] = meminfo["free_swap_free_mb"]

all_processes = parse_process_rows(read("ps_global"), "adb-ps")
app_processes = parse_process_rows(read("app_ps"), "run-as-ps")
proc_status = parse_proc_status(read("proc_status"))
combined = all_processes + app_processes + proc_status
pdocker_related = [
    row for row in combined
    if bool(row.get("pdocker_hint")) or bool(row.get("llama_hint"))
]
# Preserve unique raw rows while combining adb, run-as, and /proc views.
unique: list[dict[str, object]] = []
seen: set[str] = set()
for row in pdocker_related:
    key = str(row.get("raw") or row.get("args") or row)
    if key in seen:
        continue
    seen.add(key)
    unique.append(row)

def rss_value(row: dict[str, object]) -> float:
    value = row.get("rss_mb")
    return float(value) if isinstance(value, (int, float)) else -1.0

top_memory = [row for row in all_processes if isinstance(row.get("rss_mb"), (int, float))]
top_memory.sort(key=rss_value, reverse=True)
rss_values = [rss_value(row) for row in unique if rss_value(row) >= 0]

report = {
    "schema": "pdocker.android.device-memory-diagnostics.v1",
    "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "package": pkg,
    "container": container,
    "collection_policy": {
        "read_only": True,
        "no_llama_compare_started": True,
        "no_pdockerd_start": True,
        "no_container_start": True,
        "no_force_stop_user_apps": True,
    },
    "memory": meminfo,
    "vmstat": parse_key_values(read("vmstat")),
    "pressure": {"memory": parse_psi(read("psi_memory"))},
    "swap_devices_raw": [line.strip() for line in read("swaps_zram").splitlines() if line.strip()][:80],
    "pdockerd_socket": (read("app_socket").strip().splitlines() or ["unknown"])[0],
    "pdocker_process_count": len(unique),
    "pdocker_process_rss_mb_total": round(sum(rss_values), 1) if rss_values else None,
    "stale_llama_process_hint": any(bool(row.get("llama_hint")) for row in unique),
    "pdocker_process_sample": unique[:process_limit],
    "top_memory_process_sample": top_memory[:process_limit],
    "raw_command_errors": {
        name[:-4]: read(name).strip()[:500]
        for name in sorted(p.name for p in base.glob("*.err"))
        if read(name).strip()
    },
    "next_steps": [
        "Use this standalone diagnostic before launching llama compare when MemAvailable or SwapFree is suspicious.",
        "If stale pdocker llama work is present, stop only pdocker-owned work from the app UI or Engine; do not force-stop user apps from automation.",
        "Low SwapFree on Android zram is advisory by default; wait or reboot only when MemAvailable/PSI/LMK evidence also indicates unsafe pressure, or when a strict swap gate was explicitly configured.",
    ],
}
encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
if out == "-":
    print(encoded, end="")
else:
    Path(out).write_text(encoded, encoding="utf-8")
    print(encoded, end="")
PY

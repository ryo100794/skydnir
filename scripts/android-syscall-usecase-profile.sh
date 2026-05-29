#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ADB="${ADB:-adb}"
PKG="${SKYDNIR_ANDROID_PACKAGE:-${SKYDNIR_PACKAGE:-${PDOCKER_ANDROID_PACKAGE:-io.github.ryo100794.pdocker.compat}}}"
TRACE_MODE="${PDOCKER_DIRECT_TRACE_MODE:-seccomp}"
SMALL_FILES="${PDOCKER_SYSCALL_PROFILE_SMALL_FILES:-64}"
PATH_PROFILE="${PDOCKER_DIRECT_PATH_PROFILE:-0}"
OUT="${PDOCKER_SYSCALL_PROFILE_OUT:-$ROOT/docs/test/syscall-usecase-profile-latest.json}"
MD_OUT="${PDOCKER_SYSCALL_PROFILE_MD:-$ROOT/docs/test/syscall-usecase-profile-latest.md}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REMOTE_BENCH_DIR="files/pdocker/bench"
REMOTE_JSON="syscall-usecase-profile-$STAMP.json"
RAW="$(mktemp "${TMPDIR:-/tmp}/pdocker-syscall-usecase.XXXXXX.log")"
trap 'rm -f "$RAW"' EXIT

usage() {
  cat <<EOF
Usage: scripts/android-syscall-usecase-profile.sh [--trace-mode seccomp|syscall] [--small-files N] [--path-profile] [--out PATH]

Profiles syscall frequency for representative pdocker container workloads.

Trace modes:
  seccomp  production-like: only pdocker-mediated syscalls are traced
  syscall  diagnostic: every syscall entry/exit is traced, much slower but
           useful for total syscall frequency including read/write/close

Environment:
  ADB                              adb executable (default: adb)
  SKYDNIR_ANDROID_PACKAGE         package name (SKYDNIR_PACKAGE/PDOCKER_ANDROID_PACKAGE are still accepted; default: $PKG)
  PDOCKER_SYSCALL_PROFILE_OUT      JSON output path
  PDOCKER_SYSCALL_PROFILE_MD       Markdown output path
  PDOCKER_SYSCALL_PROFILE_SMALL_FILES  workload fanout (default: $SMALL_FILES)
  PDOCKER_DIRECT_PATH_PROFILE      enable rewrite_at_path_arg phase timing
EOF
}

while (($#)); do
  case "$1" in
    --trace-mode)
      shift
      TRACE_MODE="${1:?--trace-mode requires a value}"
      ;;
    --small-files)
      shift
      SMALL_FILES="${1:?--small-files requires a value}"
      ;;
    --path-profile)
      PATH_PROFILE=1
      ;;
    --out)
      shift
      OUT="${1:?--out requires a value}"
      MD_OUT="${OUT%.json}.md"
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

remote_quote() {
  printf "'%s'" "$(printf "%s" "$1" | sed "s/'/'\\\\''/g")"
}

adb_run_as() {
  "$ADB" shell "run-as $PKG sh -c $(remote_quote "$1")"
}

payload_script() {
  cat <<'EOF'
set +e
cd files || exit 1
R=$(find pdocker/containers -mindepth 2 -maxdepth 2 -type d -name rootfs 2>/dev/null | head -1)
if test -z "$R"; then
  R=$(find pdocker/images -mindepth 2 -maxdepth 3 -type d -name rootfs 2>/dev/null | head -1)
fi
if test -z "$R"; then
  echo "__PDSYSCALL_ERROR__:no-rootfs"
  exit 2
fi

export PDOCKER_DIRECT_EXPERIMENTAL_PROCESS_EXEC=1
export PDOCKER_DIRECT_TRACE_MODE="${TRACE_MODE:-seccomp}"
export PDOCKER_DIRECT_TRACE_SYSCALLS=0
export PDOCKER_DIRECT_TRACE_VERBOSE=0
export PDOCKER_DIRECT_TRACE_PATHS=0
export PDOCKER_DIRECT_STATS=1
export PDOCKER_DIRECT_STATS_TOP=80
export PDOCKER_DIRECT_PATH_PROFILE="${PATH_PROFILE:-0}"

echo "__PDSYSCALL_CONTEXT__:rootfs=$R;trace_mode=$PDOCKER_DIRECT_TRACE_MODE;small_files=$SMALL_FILES;path_profile=$PDOCKER_DIRECT_PATH_PROFILE"
DIRECT="pdocker-runtime/docker-bin/pdocker-direct"
if test -x pdocker/bench/pdocker-direct-microbench; then
  DIRECT="pdocker/bench/pdocker-direct-microbench"
fi

run_case() {
  label="$1"
  body="$2"
  echo "__PDSYSCALL_BEGIN__:$label"
  /system/bin/time -p "$DIRECT" run \
    --mode bench \
    --rootfs "$R" \
    --workdir / \
    --env HOME=/root \
    --env PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    --env SMALL_FILES="$SMALL_FILES" \
    -- /bin/sh -lc "$body" 2>&1
  rc=$?
  echo "__PDSYSCALL_END__:$label:rc=$rc"
  return "$rc"
}

fail=0
run_case shell_startup 'true' || fail=1
run_case package_metadata 'if command -v apt-cache >/dev/null 2>&1; then apt-cache policy bash >/tmp/pdocker-profile-apt-cache.log; else ls /etc >/tmp/pdocker-profile-etc.log; fi' || fail=1
run_case source_scan 'rm -rf /tmp/pdocker-profile; mkdir -p /tmp/pdocker-profile/src /tmp/pdocker-profile/out; i=0; while [ $i -lt "$SMALL_FILES" ]; do d=/tmp/pdocker-profile/src/d$((i%8)); mkdir -p "$d"; printf "int unit_%s(void){return %s;}\\n" "$i" "$i" > "$d/u$i.c" || exit 1; i=$((i+1)); done; find /tmp/pdocker-profile/src -type f -name "*.c" | while read f; do grep -q unit_ "$f" || exit 1; done' || fail=1
run_case object_write 'cd /tmp/pdocker-profile || exit 1; find src -type f -name "*.c" | while read f; do b=$(basename "$f" .c); cat "$f" "$f" > "out/$b.o" || exit 1; printf "out/%s.o: %s\\n" "$b" "$f" > "out/$b.d" || exit 1; done; cat out/*.o > libmock.a' || fail=1
run_case overlay_shape 'rm -rf /tmp/pdocker-profile-overlay; mkdir -p /tmp/pdocker-profile-overlay/lower /tmp/pdocker-profile-overlay/upper; i=0; while [ $i -lt "$SMALL_FILES" ]; do printf "lower-%08d\\n" "$i" > "/tmp/pdocker-profile-overlay/lower/f$i" || exit 1; ln "/tmp/pdocker-profile-overlay/lower/f$i" "/tmp/pdocker-profile-overlay/upper/f$i" || exit 1; i=$((i+1)); done; if test -e /.libcow.so; then export LD_PRELOAD=/.libcow.so; fi; i=0; while [ $i -lt "$SMALL_FILES" ]; do printf "upper-%08d\\n" "$i" >> "/tmp/pdocker-profile-overlay/upper/f$i" || exit 1; i=$((i+1)); done; i=0; while [ $i -lt "$SMALL_FILES" ]; do rm -f "/tmp/pdocker-profile-overlay/upper/f$i" || exit 1; i=$((i+1)); done' || fail=1
run_case cleanup 'rm -rf /tmp/pdocker-profile /tmp/pdocker-profile-overlay /tmp/pdocker-profile-apt-cache.log /tmp/pdocker-profile-etc.log' || fail=1
exit "$fail"
EOF
}

device_serial="$("${ADB}" get-serialno 2>&1 || true)"
echo "[pdocker syscall profile] device: $device_serial"
echo "[pdocker syscall profile] trace-mode=$TRACE_MODE small_files=$SMALL_FILES path_profile=$PATH_PROFILE"

set +e
adb_run_as "TRACE_MODE=$(remote_quote "$TRACE_MODE"); SMALL_FILES=$(remote_quote "$SMALL_FILES"); PATH_PROFILE=$(remote_quote "$PATH_PROFILE"); export TRACE_MODE SMALL_FILES PATH_PROFILE; $(payload_script)" 2>&1 | tee "$RAW"
profile_rc=${PIPESTATUS[0]}
set -e
if ((profile_rc != 0)); then
  echo "__PDSYSCALL_HOST_ERROR__:adb-run-as-rc=$profile_rc" | tee -a "$RAW"
fi

mkdir -p "$(dirname "$OUT")" "$(dirname "$MD_OUT")"
python3 - "$RAW" "$OUT" "$MD_OUT" "$TRACE_MODE" "$SMALL_FILES" "$PATH_PROFILE" "$device_serial" "$profile_rc" <<'PY'
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

raw_path, out_path, md_path, trace_mode, small_files, path_profile, device_serial, profile_rc = sys.argv[1:9]
raw = Path(raw_path).read_text(errors="replace").splitlines()
context = {}
events = []
current = None

for line in raw:
    if line.startswith("__PDSYSCALL_CONTEXT__:"):
        payload = line.split(":", 1)[1]
        for item in payload.split(";"):
            if "=" in item:
                key, value = item.split("=", 1)
                context[key] = value
        continue
    if line.startswith("__PDSYSCALL_ERROR__:"):
        events.append({"label": "error", "rc": 2, "raw_tail": [line]})
        continue
    m = re.match(r"__PDSYSCALL_BEGIN__:([^:]+)$", line)
    if m:
        current = {"label": m.group(1), "raw": []}
        continue
    m = re.match(r"__PDSYSCALL_END__:([^:]+):rc=([0-9]+)$", line)
    if m and current:
        current["rc"] = int(m.group(2))
        events.append(current)
        current = None
        continue
    if current is not None:
        current["raw"].append(line)

for event in events:
    stats_text = "\n".join(event.get("raw", []))
    reason = re.search(r"pdocker-direct-stats: reason=([^ ]+) rc=([-0-9]+) elapsed=([0-9.]+)s stops=([0-9]+)", stats_text)
    if reason:
        event["stats_reason"] = reason.group(1)
        event["stats_rc"] = int(reason.group(2))
        event["elapsed_s"] = float(reason.group(3))
        event["stops"] = int(reason.group(4))
    for key in ("real", "user", "sys"):
        m = re.search(rf"^{key} ([0-9.]+)$", stats_text, re.MULTILINE)
        if m:
            event[f"{key}_s"] = float(m.group(1))
    top = []
    for rank, nr, name, count in re.findall(r"pdocker-direct-stats: #([0-9]+) nr=([0-9]+)\(([^)]+)\) count=([0-9]+)", stats_text):
        top.append({"rank": int(rank), "nr": int(nr), "name": name, "count": int(count)})
    event["top_syscalls"] = top
    m = re.search(
        r"pdocker-direct-path-profile: calls=([0-9]+) empty=([0-9]+) relative=([0-9]+) absolute=([0-9]+) no_rewrite=([0-9]+) rewrote=([0-9]+) rootfd=([0-9]+) denied=([0-9]+) total_us=([0-9.]+) avg_us=([0-9.]+)",
        stats_text,
    )
    if m:
        event["path_profile"] = {
            "calls": int(m.group(1)),
            "empty": int(m.group(2)),
            "relative": int(m.group(3)),
            "absolute": int(m.group(4)),
            "no_rewrite": int(m.group(5)),
            "rewrote": int(m.group(6)),
            "rootfd": int(m.group(7)),
            "denied": int(m.group(8)),
            "total_us": float(m.group(9)),
            "avg_us": float(m.group(10)),
        }
    m = re.search(
        r"pdocker-direct-path-profile: phase_us read=([0-9.]+) relative_validate=([0-9.]+) resolve=([0-9.]+) validate=([0-9.]+) write=([0-9.]+)",
        stats_text,
    )
    if m and "path_profile" in event:
        event["path_profile"]["phase_us"] = {
            "read": float(m.group(1)),
            "relative_validate": float(m.group(2)),
            "resolve": float(m.group(3)),
            "validate": float(m.group(4)),
            "write": float(m.group(5)),
        }
    m = re.search(
        r"pdocker-direct-path-profile: validate calls=([0-9]+) avg_us=([0-9.]+) lexical_us=([0-9.]+) realpath_full_us=([0-9.]+) parent_realpath_us=([0-9.]+) parent_loops=([0-9]+)",
        stats_text,
    )
    if m and "path_profile" in event:
        event["path_profile"]["validate"] = {
            "calls": int(m.group(1)),
            "avg_us": float(m.group(2)),
            "lexical_us": float(m.group(3)),
            "realpath_full_us": float(m.group(4)),
            "parent_realpath_us": float(m.group(5)),
            "parent_loops": int(m.group(6)),
        }
    m = re.search(
        r"pdocker-direct-path-profile: cache validation_hits=([0-9]+) validation_misses=([0-9]+) realpath_hits=([0-9]+) realpath_misses=([0-9]+) invalidations=([0-9]+) generation=([0-9]+)",
        stats_text,
    )
    if m and "path_profile" in event:
        event["path_profile"]["cache"] = {
            "validation_hits": int(m.group(1)),
            "validation_misses": int(m.group(2)),
            "realpath_hits": int(m.group(3)),
            "realpath_misses": int(m.group(4)),
            "invalidations": int(m.group(5)),
            "generation": int(m.group(6)),
        }
    total_count = sum(item["count"] for item in top)
    event["reported_syscall_count"] = total_count
    if event.get("stops") and total_count:
        event["reported_count_to_stops_ratio"] = total_count / event["stops"]
    event["raw_tail"] = event.pop("raw", [])[-20:]

aggregate = {}
for event in events:
    for item in event.get("top_syscalls", []):
        entry = aggregate.setdefault(item["name"], {"name": item["name"], "nr": item["nr"], "count": 0})
        entry["count"] += item["count"]
aggregate_rows = sorted(aggregate.values(), key=lambda x: (-x["count"], x["name"]))

artifact = {
    "schema": 1,
    "kind": "pdocker.syscall-usecase-profile",
    "git_commit": subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip(),
    "git_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], text=True).strip()),
    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    "command": "bash scripts/android-syscall-usecase-profile.sh",
    "parameters": {
        "trace_mode": trace_mode,
        "small_files": int(small_files),
        "path_profile": path_profile == "1",
    },
    "device_serial": device_serial,
    "profile_rc": int(profile_rc),
    "context": context,
    "events": events,
    "aggregate_top_syscalls": aggregate_rows,
    "notes": [
        "seccomp mode is production-like and counts only syscalls intercepted by pdocker-direct.",
        "syscall mode is diagnostic and counts all syscall entries seen by ptrace, but it is much slower.",
        "read/write/close frequency is expected to appear only in syscall mode unless a specific fd operation is emulated.",
    ],
}
Path(out_path).write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")

def fmt(value, digits=3):
    return "" if value is None else f"{value:.{digits}f}"

lines = [
    "# Skydnir Syscall Use-Case Profile",
    "",
    f"- Commit: `{artifact['git_commit']}`",
    f"- Dirty tree: `{artifact['git_dirty']}`",
    f"- Timestamp: `{artifact['timestamp_utc']}`",
    f"- Device: `{device_serial}`",
    f"- Trace mode: `{trace_mode}`",
    f"- Small files: {small_files}",
    f"- Path micro profile: `{path_profile}`",
    "",
    "## Use Cases",
    "",
    "| use case | rc | real s | traced stops | reported syscalls | top syscall counts |",
    "|---|---:|---:|---:|---:|---|",
]
for event in events:
    top_text = ", ".join(f"{i['name']}={i['count']}" for i in event.get("top_syscalls", [])[:8])
    lines.append(
        f"| {event.get('label', '')} | {event.get('rc', '')} | {fmt(event.get('real_s'))} | "
        f"{event.get('stops', '')} | {event.get('reported_syscall_count', '')} | {top_text} |"
    )

path_rows = [event for event in events if "path_profile" in event]
if path_rows:
    lines.extend([
        "",
        "## Path Micro Profile",
        "",
        "| use case | calls | avg us | read us | relative validate us | resolve us | validate us | write us | validate calls | validation cache hit % | realpath cache hit % | invalidations | realpath full us | parent realpath us |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for event in path_rows:
        pp = event["path_profile"]
        phase = pp.get("phase_us", {})
        val = pp.get("validate", {})
        cache = pp.get("cache", {})
        validation_total = cache.get("validation_hits", 0) + cache.get("validation_misses", 0)
        realpath_total = cache.get("realpath_hits", 0) + cache.get("realpath_misses", 0)
        validation_hit_pct = (100.0 * cache.get("validation_hits", 0) / validation_total) if validation_total else 0.0
        realpath_hit_pct = (100.0 * cache.get("realpath_hits", 0) / realpath_total) if realpath_total else 0.0
        lines.append(
            f"| {event.get('label', '')} | {pp.get('calls', '')} | {pp.get('avg_us', 0.0):.3f} | "
            f"{phase.get('read', 0.0):.3f} | {phase.get('relative_validate', 0.0):.3f} | "
            f"{phase.get('resolve', 0.0):.3f} | {phase.get('validate', 0.0):.3f} | "
            f"{phase.get('write', 0.0):.3f} | {val.get('calls', '')} | "
            f"{validation_hit_pct:.1f} | {realpath_hit_pct:.1f} | {cache.get('invalidations', '')} | "
            f"{val.get('realpath_full_us', 0.0):.3f} | {val.get('parent_realpath_us', 0.0):.3f} |"
        )

lines.extend([
    "",
    "## Aggregate",
    "",
    "| syscall | nr | count |",
    "|---|---:|---:|",
])
for row in aggregate_rows[:40]:
    lines.append(f"| {row['name']} | {row['nr']} | {row['count']} |")

lines.extend([
    "",
    "## Interpretation",
    "",
    "- `seccomp` mode shows the production mediation surface: path, credential, exec, memory guard, and compatibility syscalls that pdocker-direct actually intercepts.",
    "- `syscall` mode shows the full syscall frequency picture, including fd-only calls such as `read`, `write`, `close`, `fstat`, and `lseek`.",
    "- Compare both modes before optimizing: high frequency in `syscall` mode is harmless when the syscall is not intercepted in `seccomp` mode.",
])
Path(md_path).write_text("\n".join(lines) + "\n")
print(f"[pdocker syscall profile] wrote {out_path}")
print(f"[pdocker syscall profile] wrote {md_path}")
PY

set +e
"$ADB" push "$OUT" "/data/local/tmp/$REMOTE_JSON" >/dev/null
push_rc=$?
if ((push_rc == 0)); then
  adb_run_as "mkdir -p '$REMOTE_BENCH_DIR' && cp '/data/local/tmp/$REMOTE_JSON' '$REMOTE_BENCH_DIR/$REMOTE_JSON' && cp '$REMOTE_BENCH_DIR/$REMOTE_JSON' '$REMOTE_BENCH_DIR/syscall-usecase-profile-latest.json'" >/dev/null
  mirror_rc=$?
else
  mirror_rc=1
fi
set -e
if ((push_rc == 0 && mirror_rc == 0)); then
  echo "[pdocker syscall profile] device artifact: $REMOTE_BENCH_DIR/syscall-usecase-profile-latest.json"
else
  echo "[pdocker syscall profile] device artifact mirror failed: push_rc=$push_rc mirror_rc=$mirror_rc" >&2
fi

exit "$profile_rc"

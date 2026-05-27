#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ADB="${ADB:-adb}"
PKG="${SKYDNIR_PACKAGE:-${PDOCKER_PACKAGE:-io.github.ryo100794.pdocker.compat}}"
CONTAINER="${PDOCKER_LLAMA_CONTAINER:-pdocker-llama-cpp}"
OUT="${PDOCKER_LLAMA_READINESS_OUT:-$ROOT/docs/test/llama-gpu-device-readiness-latest.json}"
MIN_AVAILABLE_MB="${PDOCKER_LLAMA_MIN_FREE_MB:-512}"
MIN_SWAP_FREE_MB="${PDOCKER_LLAMA_MIN_SWAP_FREE_MB:-0}"
SWAP_ADVISORY_MB="${PDOCKER_LLAMA_SWAP_ADVISORY_MB:-1024}"

usage() {
  cat <<EOF
Usage: $0 [--out PATH]

Writes a JSON readiness report for the llama GPU bridge device run.  This is a
low-impact preflight: it does not start pdockerd, does not start containers, and
does not force-stop the browser or VS Code session.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --out) OUT="$2"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

mkdir -p "$(dirname "$OUT")"

ADB_STATE="$("$ADB" get-state 2>/dev/null | tr -d '\r' || true)"
ADB_SERIAL="$("$ADB" get-serialno 2>/dev/null | tr -d '\r' || true)"
RUN_AS_PROBE="$("$ADB" shell "run-as $PKG sh -c 'test -d files >/dev/null 2>&1; echo run_as_ok:\$?; test -d files/pdocker/projects/llama-cpp-gpu >/dev/null 2>&1; echo project_ok:\$?; test -d files/pdocker/projects/llama-cpp-gpu/models >/dev/null 2>&1; echo models_ok:\$?'" 2>/dev/null | tr -d '\r' || true)"
RAW_MEM="$("$ADB" shell 'cat /proc/meminfo 2>/dev/null' 2>/dev/null || true)"
RAW_PS="$("$ADB" shell "ps -A | grep -E 'pdocker|llama|chrome' || true" 2>/dev/null || true)"
SOCKET_STATE="$("$ADB" shell "run-as $PKG sh -c 'cd files 2>/dev/null && if test -S pdocker/pdockerd.sock; then echo present; else echo absent; fi' 2>/dev/null" 2>/dev/null | tr -d '\r' || true)"

python3 - "$OUT" "$MIN_AVAILABLE_MB" "$MIN_SWAP_FREE_MB" "$SWAP_ADVISORY_MB" "$CONTAINER" "$RAW_MEM" "$RAW_PS" "$SOCKET_STATE" "$ADB_STATE" "$ADB_SERIAL" "$RUN_AS_PROBE" <<'PY'
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

out, min_available, min_swap, swap_advisory, container, raw_mem, raw_ps, socket_state, adb_state, adb_serial, run_as_probe = sys.argv[1:12]
min_available = int(min_available)
min_swap = int(min_swap)
swap_advisory = int(swap_advisory)

def kb_to_mb(value):
    return int(value) // 1024

memory = {
    "mem_total_mb": 0,
    "mem_free_mb": 0,
    "mem_available_mb": 0,
    "swap_total_mb": 0,
    "swap_free_mb": 0,
    "swap_cached_mb": 0,
}
for line in raw_mem.splitlines():
    m = re.match(r"^([A-Za-z_()]+):\s+([0-9]+)\s+kB$", line.strip())
    if not m:
        continue
    key, value = m.group(1), int(m.group(2))
    if key == "MemTotal":
        memory["mem_total_mb"] = kb_to_mb(value)
    elif key == "MemFree":
        memory["mem_free_mb"] = kb_to_mb(value)
    elif key == "MemAvailable":
        memory["mem_available_mb"] = kb_to_mb(value)
    elif key == "SwapTotal":
        memory["swap_total_mb"] = kb_to_mb(value)
    elif key == "SwapFree":
        memory["swap_free_mb"] = kb_to_mb(value)
    elif key == "SwapCached":
        memory["swap_cached_mb"] = kb_to_mb(value)

process_lines = [line for line in raw_ps.splitlines() if line.strip()]
stale_target_hint = any(container in line or "llama" in line.lower() for line in process_lines)
pdocker_process_hint = any("pdocker" in line for line in process_lines)
browser_hint = any("chrome" in line.lower() for line in process_lines)
swap_hard_ok = min_swap <= 0 or memory["swap_free_mb"] >= min_swap
swap_advisory_ok = swap_advisory <= 0 or memory["swap_free_mb"] >= swap_advisory
probe_status = {}
for line in run_as_probe.splitlines():
    if ":" not in line:
        continue
    key, value = line.strip().split(":", 1)
    probe_status[key] = value == "0"
adb_connected = adb_state.strip() == "device"
run_as_ok = probe_status.get("run_as_ok", False)
project_ok = probe_status.get("project_ok", False)
connectivity_ok = adb_connected and run_as_ok and project_ok
ready = connectivity_ok and memory["mem_available_mb"] >= min_available and swap_hard_ok
actions = []
if not adb_connected:
    actions.append("Connect exactly one ADB device (or set ANDROID_SERIAL) before collecting ngl=1 Q6_K evidence.")
if adb_connected and not run_as_ok:
    actions.append("Install/start the compat APK and confirm run-as works for the pdocker package before GPU evidence collection.")
if run_as_ok and not project_ok:
    actions.append("Open the llama-cpp-gpu project once so the app project directory exists before running ngl=1 Q6_K collection.")
if not ready:
    actions.append("Do not start the llama GPU compare/benchmark; readiness=false is a hard GPU-run stop.")
    actions.append("Do not classify compare, correctness, or benchmark claims from a run started while readiness=false.")
    if stale_target_hint:
        actions.append("Stop the pdocker llama container from the UI or Engine, then re-check readiness.")
    actions.append("Wait for Android reclaim if MemAvailable is low; low SwapFree is advisory unless a hard swap threshold was explicitly configured.")
    actions.append("Do not force-stop the browser/VS Code session from automation.")
else:
    actions.append("Run scripts/android-llama-gpu-compare.sh with PDOCKER_GPU_CPU_ORACLE=1 and the Q6_K workgroup artifact path.")

report = {
    "schema": "pdocker.llama.gpu.device-readiness.v1",
    "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "ready": ready,
    "gpu_run_allowed": ready,
    "claim_policy": {
        "readiness_false_blocks_gpu_run": True,
        "executor_marker_required_for_compare_claim": True,
        "cpu_comparison_required_for_benchmark_claim": True,
    },
    "required": {
        "mem_available_mb": min_available,
        "swap_free_mb": min_swap,
        "swap_free_hard_gate_enabled": min_swap > 0,
        "swap_free_advisory_mb": swap_advisory,
    },
    "swap_policy": {
        "default": "advisory",
        "hard_gate_enabled": min_swap > 0,
        "hard_min_swap_free_mb": min_swap,
        "advisory_swap_free_mb": swap_advisory,
        "swap_free_advisory_ok": swap_advisory_ok,
        "swap_pressure_advisory": not swap_advisory_ok,
    },
    "memory": memory,
    "adb": {
        "state": adb_state.strip() or "unknown",
        "serial": adb_serial.strip() or "unknown",
        "connected": adb_connected,
    },
    "preconditions": {
        "adb_connected": adb_connected,
        "run_as_ok": run_as_ok,
        "project_dir_ok": project_ok,
        "models_dir_seen": probe_status.get("models_ok", False),
        "q6_ngl1_evidence_collection_allowed": ready,
    },
    "pdockerd_socket": socket_state.strip() or "unknown",
    "process_hints": {
        "pdocker_process_seen": pdocker_process_hint,
        "stale_target_hint": stale_target_hint,
        "browser_hint": browser_hint,
        "sample": process_lines[:24],
    },
    "device_actions": actions,
}
Path(out).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(json.dumps(report, indent=2, sort_keys=True))
raise SystemExit(0 if ready else 20)
PY

#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ADB="${ADB:-adb}"
PKG="${SKYDNIR_PACKAGE:-${PDOCKER_PACKAGE:-io.github.ryo100794.pdocker.compat}}"
CLASS_PREFIX="io.github.ryo100794.pdocker"
ACTION_PREFIX="io.github.ryo100794.pdocker"
CONTAINER="${SKYDNIR_LLAMA_CONTAINER:-${PDOCKER_LLAMA_CONTAINER:-skydnir-llama-cpp}}"
IMAGE="${SKYDNIR_LLAMA_IMAGE:-${PDOCKER_LLAMA_IMAGE:-skydnir/llama-cpp-gpu:latest}}"
PROJECT="${PDOCKER_LLAMA_PROJECT:-files/pdocker/projects/llama-cpp-gpu}"
LOCAL_PORT="${PDOCKER_LLAMA_LOCAL_PORT:-28081}"
REMOTE_PORT="${PDOCKER_LLAMA_REMOTE_PORT:-18081}"
CPU_CTX="${PDOCKER_LLAMA_CPU_CTX:-2048}"
GPU_CTX="${PDOCKER_LLAMA_GPU_CTX:-512}"
GPU_LAYERS="${PDOCKER_LLAMA_GPU_LAYERS:-2}"
PREDICT="${PDOCKER_LLAMA_BENCH_PREDICT:-4}"
REPEAT="${PDOCKER_LLAMA_BENCH_REPEAT:-1}"
WARMUP_DISCARD="${PDOCKER_LLAMA_BENCH_WARMUP_DISCARD:-0}"
OUT="${PDOCKER_LLAMA_COMPARE_OUT:-$ROOT/docs/test/llama-gpu-compare-latest.json}"
MODEL_PATH="${PDOCKER_LLAMA_MODEL_PATH:-/models/model.gguf}"
MODEL_URL="${PDOCKER_LLAMA_MODEL_URL:-https://huggingface.co/Qwen/Qwen3-8B-GGUF/resolve/main/Qwen3-8B-Q4_K_M.gguf}"
RESTORE_CPU=0
RUN_CPU=1
CPU_TPS_OVERRIDE="${PDOCKER_LLAMA_CPU_TPS:-}"
TRACE_ALLOC="${PDOCKER_LLAMA_TRACE_ALLOC:-0}"
CORRECTNESS="${PDOCKER_LLAMA_CORRECTNESS:-1}"
CORRECTNESS_TIMEOUT_SEC="${PDOCKER_LLAMA_CORRECTNESS_TIMEOUT_SEC:-180}"
COMPLETION_READY_TIMEOUT_SEC="${PDOCKER_LLAMA_COMPLETION_READY_TIMEOUT_SEC:-180}"
CPU_WAIT_SERVER_TIMEOUT_SEC="${PDOCKER_LLAMA_CPU_WAIT_SERVER_TIMEOUT_SEC:-90}"
FORCED_VULKAN_WAIT_SERVER_TIMEOUT_SEC="${PDOCKER_LLAMA_FORCED_VULKAN_WAIT_SERVER_TIMEOUT_SEC:-240}"
CPU_RESTORE_WAIT_SERVER_TIMEOUT_SEC="${PDOCKER_LLAMA_CPU_RESTORE_WAIT_SERVER_TIMEOUT_SEC:-90}"
LOG_TAIL_LINES="${PDOCKER_LLAMA_LOG_TAIL_LINES:-2000}"
RESTART_APP_DAEMON="${PDOCKER_LLAMA_RESTART_APP_DAEMON:-1}"
MIN_FREE_MB="${PDOCKER_LLAMA_MIN_FREE_MB:-512}"
# Android devices commonly use zram swap aggressively; SwapFree can stay near
# zero while MemAvailable is still adequate.  Keep swap as an advisory signal by
# default, but allow strict runs to opt into a hard gate by setting
# PDOCKER_LLAMA_MIN_SWAP_FREE_MB/PDOCKER_LLAMA_RUNTIME_MIN_SWAP_FREE_MB.
MIN_SWAP_FREE_MB="${PDOCKER_LLAMA_MIN_SWAP_FREE_MB:-0}"
SWAP_ADVISORY_MB="${PDOCKER_LLAMA_SWAP_ADVISORY_MB:-1024}"
WAIT_FOR_MEMORY_SEC="${PDOCKER_LLAMA_WAIT_FOR_MEMORY_SEC:-0}"
WAIT_FOR_MEMORY_INTERVAL_SEC="${PDOCKER_LLAMA_WAIT_FOR_MEMORY_INTERVAL_SEC:-10}"
RUNTIME_MIN_FREE_MB="${PDOCKER_LLAMA_RUNTIME_MIN_FREE_MB:-384}"
RUNTIME_MIN_SWAP_FREE_MB="${PDOCKER_LLAMA_RUNTIME_MIN_SWAP_FREE_MB:-0}"
RUNTIME_SWAP_ADVISORY_MB="${PDOCKER_LLAMA_RUNTIME_SWAP_ADVISORY_MB:-512}"
STOP_ON_FAILURE="${PDOCKER_LLAMA_STOP_ON_FAILURE:-1}"
ENGINE_START_TIMEOUT_SEC="${PDOCKER_LLAMA_ENGINE_START_TIMEOUT_SEC:-15}"
ENGINE_CREATE_TIMEOUT_SEC="${PDOCKER_LLAMA_ENGINE_CREATE_TIMEOUT_SEC:-120}"
ENGINE_CREATE_SETTLE_TIMEOUT_SEC="${PDOCKER_LLAMA_ENGINE_CREATE_SETTLE_TIMEOUT_SEC:-60}"
ENGINE_CREATE_POLL_INTERVAL_SEC="${PDOCKER_LLAMA_ENGINE_CREATE_POLL_INTERVAL_SEC:-2}"
ENGINE_CLEANUP_TIMEOUT_SEC="${PDOCKER_LLAMA_ENGINE_CLEANUP_TIMEOUT_SEC:-60}"
RUN_AS_TIMEOUT_SEC="${PDOCKER_LLAMA_RUN_AS_TIMEOUT_SEC:-30}"
RUN_AS_CLEANUP_TIMEOUT_SEC="${PDOCKER_LLAMA_RUN_AS_CLEANUP_TIMEOUT_SEC:-5}"
OPERATION_NOTIFY_TIMEOUT_SEC="${PDOCKER_LLAMA_OPERATION_NOTIFY_TIMEOUT_SEC:-3}"
WAIT_SERVER_PROGRESS_INTERVAL_SEC="${PDOCKER_LLAMA_WAIT_SERVER_PROGRESS_INTERVAL_SEC:-10}"
WAIT_SERVER_CURL_TIMEOUT_SEC="${PDOCKER_LLAMA_WAIT_SERVER_CURL_TIMEOUT_SEC:-2}"
COMPARE_ARTIFACT_DIR="${PDOCKER_LLAMA_COMPARE_ARTIFACT_DIR:-}"
STOP_STALE_TARGET_BEFORE_PREFLIGHT="${PDOCKER_LLAMA_STOP_STALE_TARGET_BEFORE_PREFLIGHT:-1}"
ADB_KEEPALIVE="${PDOCKER_ADB_KEEPALIVE:-1}"
ADB_KEEPALIVE_INTERVAL_SEC="${PDOCKER_ADB_KEEPALIVE_INTERVAL_SEC:-8}"
ADB_KEEPALIVE_TIMEOUT_SEC="${PDOCKER_ADB_KEEPALIVE_TIMEOUT_SEC:-5}"
ANDROID_SAME_DEVICE_HTTP="${PDOCKER_ANDROID_SAME_DEVICE_HTTP:-0}"
OP_ID="llama-gpu-compare-$(date -u +%Y%m%dT%H%M%SZ)-$$"
CURRENT_CONTAINER_ID=""
COMPARE_RESULT_READY=0
ADB_KEEPALIVE_PID=""

usage() {
  cat <<EOF
Usage: $0 [--out PATH] [--gpu-layers N] [--gpu-ctx N] [--cpu-ctx N] [--predict N] [--repeat N] [--warmup-discard N] [--model-path PATH] [--model-url URL] [--gpu-only] [--cpu-tps N] [--trace-alloc] [--restore] [--no-restore]

Runs a repeatable Android llama.cpp CPU/GPU comparison scenario without
modifying llama.cpp:
  1. start the project-library llama container in CPU mode and benchmark HTTP;
  2. start the same image in forced Vulkan mode and capture model-load/serve status;
  3. write a JSON report with CPU speed, GPU load evidence, and the 10x gap;
  4. leave the last measured container running by default; pass --restore when
     a CPU fallback server should be started after the measurement.

This script drives pdockerd through the Docker-compatible Engine HTTP API over
the app Unix socket. It does not require staging the upstream Docker CLI into
the APK runtime.

Use --gpu-only during tight GPU bridge tuning loops. It reuses --cpu-tps when
provided, otherwise the CPU value from the existing output JSON. Full CPU/GPU
comparison should still be run at milestone points.

Use --model-url with --model-path to run the same compare flow against a small
GGUF model without replacing the default /models/model.gguf 8B path, for
example --model-path /models/small.gguf --model-url https://.../small.gguf.

Allocation/copy tracing is disabled by default for performance measurements
because the ICD trace stream can dominate short llama runs. Use --trace-alloc
only when diagnosing buffer accounting or chunking failures.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --out) OUT="$2"; shift ;;
    --gpu-layers) GPU_LAYERS="$2"; shift ;;
    --gpu-ctx) GPU_CTX="$2"; shift ;;
    --cpu-ctx) CPU_CTX="$2"; shift ;;
    --predict) PREDICT="$2"; shift ;;
    --repeat) REPEAT="$2"; shift ;;
    --warmup-discard) WARMUP_DISCARD="$2"; shift ;;
    --model-path) MODEL_PATH="$2"; shift ;;
    --model-url) MODEL_URL="$2"; shift ;;
    --gpu-only) RUN_CPU=0 ;;
    --cpu-tps) CPU_TPS_OVERRIDE="$2"; shift ;;
    --trace-alloc) TRACE_ALLOC=1 ;;
    --restore) RESTORE_CPU=1 ;;
    --no-restore) RESTORE_CPU=0 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

if ! [[ "$PREDICT" =~ ^[0-9]+$ ]] || (( PREDICT < 2 )); then
  echo "--predict must be an integer >= 2; llama.cpp reports 0ms eval timing for one-token runs" >&2
  exit 2
fi

mkdir -p "$(dirname "$OUT")"
TMP="$(mktemp -d)"
CURRENT_STAGE="initializing"
RUNTIME_ABORT_JSON="$TMP/runtime-memory-abort.json"
RUNTIME_ENV_RECORD_JSON="$TMP/runtime-env-record.json"

compare_artifact_dir() {
  if [[ -n "$COMPARE_ARTIFACT_DIR" ]]; then
    printf "%s" "$COMPARE_ARTIFACT_DIR"
  else
    printf "%s/%s-artifacts" "$(dirname "$OUT")" "$(basename "${OUT%.json}")"
  fi
}

same_device_http_enabled() {
  [[ "$ANDROID_SAME_DEVICE_HTTP" == "1" ]]
}

llama_base_url() {
  if same_device_http_enabled; then
    printf "http://127.0.0.1:%s" "$REMOTE_PORT"
  else
    printf "http://127.0.0.1:%s" "$LOCAL_PORT"
  fi
}

record_wait_server_event() {
  local phase="$1"
  local elapsed="$2"
  local timeout_sec="$3"
  local http_status="$4"
  local container_status="$5"
  local container_id="${6:-}"
  local curl_exit="${7:-}"
  local curl_http_code="${8:-}"
  local http_probe="${9:-}"
  local port_forward="${10:-}"
  local wait_failure_class="${11:-}"
  local path
  path="$(compare_artifact_dir)/wait-server.jsonl"
  mkdir -p "$(dirname "$path")"
  python3 - "$path" "$phase" "$elapsed" "$timeout_sec" "$http_status" "$container_status" "$container_id" "$curl_exit" "$curl_http_code" "$http_probe" "$port_forward" "$wait_failure_class" <<'PY' || true
import json
import sys
from datetime import datetime, timezone

(
    path,
    phase,
    elapsed,
    timeout_s,
    http_status,
    container_status,
    container_id,
    curl_exit,
    curl_http_code,
    http_probe,
    port_forward,
    wait_failure_class,
) = sys.argv[1:13]
event = {
    "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "phase": phase,
    "elapsed_sec": int(elapsed),
    "timeout_sec": int(timeout_s),
    "http": http_status,
    "container_running": container_status,
    "container_id": container_id,
}
if curl_exit:
    event["curl_exit"] = int(curl_exit) if curl_exit.isdigit() else curl_exit
if curl_http_code:
    event["curl_http_code"] = curl_http_code
if http_probe:
    event["http_probe"] = http_probe
if port_forward:
    event["port_forward"] = port_forward
if wait_failure_class:
    event["wait_failure_class"] = wait_failure_class
with open(path, "a", encoding="utf-8") as f:
    f.write(json.dumps(event, separators=(",", ":")) + "\n")
PY
}

record_manifest_runtime_env() {
  local out="$1"
  python3 - "$ROOT/scripts/llama-gpu-env-manifest.json" "$out" "$GPU_LAYERS" "$GPU_CTX" "$MODEL_PATH" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

manifest_path, out_path, gpu_layers, gpu_ctx, model_path = sys.argv[1:6]
manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
if manifest.get("schema") != "pdocker.llama.gpu.env-manifest.v1":
    raise SystemExit(f"unsupported llama GPU env manifest schema: {manifest_path}")

def string_list(name):
    values = manifest.get(name)
    if not isinstance(values, list) or not all(isinstance(value, str) and value for value in values):
        raise SystemExit(f"invalid {name} in llama GPU env manifest: {manifest_path}")
    return values

def classification_list(name):
    classifications = manifest.get("env_bridge_classifications")
    if not isinstance(classifications, dict):
        raise SystemExit(f"invalid env_bridge_classifications in llama GPU env manifest: {manifest_path}")
    values = classifications.get(name)
    if not isinstance(values, list) or not all(isinstance(value, str) and value for value in values):
        raise SystemExit(f"invalid env_bridge_classifications.{name} in llama GPU env manifest: {manifest_path}")
    return values

forward_keys = string_list("compare_forward_env_keys")
probe_keys = string_list("compare_probe_env_keys")
app_process_only_env_keys = classification_list("app_process_only")
config_fields = manifest.get("config_propagation_env_fields")
if not isinstance(config_fields, list):
    raise SystemExit(f"invalid config_propagation_env_fields in llama GPU env manifest: {manifest_path}")
config_keys = [
    str(item.get("env"))
    for item in config_fields
    if isinstance(item, dict) and isinstance(item.get("env"), str) and item.get("env")
]
manifest_keys = list(dict.fromkeys(forward_keys + config_keys))
host_env = {key: os.environ[key] for key in manifest_keys if key in os.environ}
record = {
    "schema": "pdocker.llama.gpu.runtime-env-record.v1",
    "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "manifest": {
        "schema": manifest["schema"],
        "path": manifest_path,
        "compare_forward_env_keys": forward_keys,
        "compare_probe_env_keys": probe_keys,
        "app_process_only_env_keys": app_process_only_env_keys,
        "config_propagation_env_keys": config_keys,
    },
    "run_settings": {
        "gpu_layers": int(gpu_layers),
        "gpu_ctx": int(gpu_ctx),
        "model_path": model_path,
    },
    "host_requested_env": dict(sorted(host_env.items())),
    "echoed_to_log": True,
}
Path(out_path).write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(
    "[pdocker llama compare] runtime env manifest "
    f"keys={len(manifest_keys)} requested={len(host_env)} record={out_path}",
    file=sys.stderr,
)
for key, value in sorted(host_env.items()):
    print(f"[pdocker llama compare] runtime env {key}={value}", file=sys.stderr)
PY
}

record_planned_container_payload_env() {
  local mode="$1"
  local payload="$2"
  python3 - "$RUNTIME_ENV_RECORD_JSON" "$mode" "$payload" <<'PY' || true
import json
import os
import sys
from pathlib import Path

record_path = Path(sys.argv[1])
mode = sys.argv[2]
payload_raw = sys.argv[3]
try:
    record = json.loads(record_path.read_text(encoding="utf-8"))
except Exception:
    record = {"schema": "pdocker.llama.gpu.runtime-env-record.v1"}
try:
    payload = json.loads(payload_raw)
except Exception:
    payload = {}

env = {}
for entry in payload.get("Env") or []:
    if not isinstance(entry, str) or "=" not in entry:
        continue
    key, value = entry.split("=", 1)
    env[key] = value

# This is not a substitute for executor/ICD runtime markers.  It is a
# separately labelled copy of the Engine create payload so artifacts do not
# lose the requested env surface when the device goes offline before inspect
# or log collection can run.
record["planned_container_mode"] = mode
record["planned_container_env"] = dict(sorted(env.items()))
tmp = record_path.with_suffix(record_path.suffix + ".tmp")
tmp.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.replace(tmp, record_path)
PY
}

cleanup() {
  local status="$?"
  stop_adb_keepalive
  if [[ "$status" -ne 0 ]]; then
    write_failure_artifact "$status" >/dev/null 2>&1 || true
    operation_notify "failed" "$CURRENT_STAGE failed with exit code $status" 1 >/dev/null 2>&1 || true
    if [[ -s "$RUNTIME_ABORT_JSON" && ! -s "$OUT" ]]; then
      mkdir -p "$(dirname "$OUT")"
      cp "$RUNTIME_ABORT_JSON" "$OUT" >/dev/null 2>&1 || true
    fi
    if [[ "$STOP_ON_FAILURE" == "1" && "$COMPARE_RESULT_READY" != "1" ]]; then
      remove_container_after_failure >/dev/null 2>&1 || true
    fi
  fi
  rm -rf "$TMP"
  if ! same_device_http_enabled; then
    "$ADB" forward --remove "tcp:$LOCAL_PORT" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

write_failure_artifact() {
  local status="$1"
  if [[ -s "$OUT" ]]; then
    return 0
  fi
  mkdir -p "$(dirname "$OUT")"
  local mem_json diagnostics_json adb_state_json runtime_env_json
  if declare -F memory_snapshot_json >/dev/null 2>&1; then
    mem_json="$(memory_snapshot_json 2>/dev/null || printf '{}')"
  else
    mem_json='{}'
  fi
  if declare -F pdocker_memory_diagnostics_json >/dev/null 2>&1; then
    diagnostics_json="$(pdocker_memory_diagnostics_json 2>/dev/null || printf '{}')"
  else
    diagnostics_json='{}'
  fi
  adb_state_json="$("$ADB" shell 'printf "{";
    printf "\"adb_wifi_enabled\":\"%s\"," "$(settings get global adb_wifi_enabled 2>/dev/null)";
    printf "\"adb_enabled\":\"%s\"," "$(settings get global adb_enabled 2>/dev/null)";
    printf "\"service_adb_tcp\":\"%s\"," "$(getprop service.adb.tcp.port)";
    printf "\"persist_adb_tcp\":\"%s\"" "$(getprop persist.adb.tcp.port)";
    printf "}"' 2>/dev/null || printf '{}')"
  if [[ -s "${RUNTIME_ENV_RECORD_JSON:-}" ]]; then
    runtime_env_json="$(cat "$RUNTIME_ENV_RECORD_JSON" 2>/dev/null || printf '{}')"
  else
    runtime_env_json='{}'
  fi
  python3 - "$OUT" "$status" "$CURRENT_STAGE" "$PKG" "$CONTAINER" "$mem_json" "$diagnostics_json" "$adb_state_json" "$runtime_env_json" <<'PY'
import json
import sys
import time
from pathlib import Path

out, status, stage, pkg, container = sys.argv[1:6]

def parse(raw):
    try:
        value = json.loads(raw)
        return value if isinstance(value, (dict, list)) else {}
    except Exception as exc:
        return {"parse_error": str(exc), "raw_excerpt": raw[:2000]}

artifact = {
    "schema": "pdocker.llama.gpu.compare.failure.v1",
    "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "exit_code": int(status) if str(status).isdigit() else status,
    "stage": stage,
    "package": pkg,
    "container": container,
    "failure_class": "early_compare_failure",
    "message": f"{stage} failed before the full llama GPU compare artifact was produced",
    "adb_state": parse(sys.argv[8]),
    "memory": parse(sys.argv[6]),
    "pdocker_diagnostics": parse(sys.argv[7]),
    "runtime_env_record": parse(sys.argv[9]),
    "next_action": "inspect adb_state, pdockerd socket/process diagnostics, and host log; rerun without changing Dockerfile/model/prompt after restoring transport stability",
}
Path(out).write_text(json.dumps(artifact, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PY
  local device_name
  device_name="$(basename "$OUT")"
  "$ADB" push "$OUT" "/data/local/tmp/$device_name" >/dev/null 2>&1 || true
  run_as "mkdir -p files/pdocker/bench && cp /data/local/tmp/$(remote_quote "$device_name") files/pdocker/bench/$(remote_quote "$device_name")" >/dev/null 2>&1 || true
}

remote_quote() {
  printf "'%s'" "$(printf "%s" "$1" | sed "s/'/'\\\\''/g")"
}

run_as() {
  # Some Android run-as builds preserve the app cwd for direct exec but reset
  # cwd to / for `sh -c`, which breaks relative access to files/pdocker/*.  Run
  # a short script file through run-as instead; the interpreter opens it via
  # the app cwd and then keeps that cwd for the script body.
  local host_script="$TMP/run-as-$$-$RANDOM.sh"
  local device_script="files/.pdocker-run-as-$$-$RANDOM.sh"
  local device_tmp="/data/local/tmp/$(basename "$host_script")"
  local run_timeout="${RUN_AS_TIMEOUT_SEC:-30}"
  local cleanup_timeout="${RUN_AS_CLEANUP_TIMEOUT_SEC:-5}"
  local rc=0
  printf '%s\n' "$1" > "$host_script"
  if command -v timeout >/dev/null 2>&1 && [[ "$run_timeout" =~ ^[0-9]+$ ]] && (( run_timeout > 0 )); then
    timeout "${run_timeout}s" "$ADB" push "$host_script" "$device_tmp" >/dev/null || rc=$?
    if [[ "$rc" -eq 0 ]]; then
      timeout "${run_timeout}s" "$ADB" shell "run-as $PKG cp $device_tmp $device_script && run-as $PKG sh $device_script" || rc=$?
    fi
    timeout "${cleanup_timeout}s" "$ADB" shell "rm -f $device_tmp; run-as $PKG rm -f $device_script" >/dev/null 2>&1 || true
  else
    "$ADB" push "$host_script" "$device_tmp" >/dev/null || rc=$?
    if [[ "$rc" -eq 0 ]]; then
      "$ADB" shell "run-as $PKG cp $device_tmp $device_script && run-as $PKG sh $device_script" || rc=$?
    fi
    "$ADB" shell "rm -f $device_tmp; run-as $PKG rm -f $device_script" >/dev/null 2>&1 || true
  fi
  return "$rc"
}

adb_transport_state() {
  "$ADB" get-state 2>/dev/null | tr -d '\r' || true
}

adb_transport_ok() {
  [[ "$(adb_transport_state)" == "device" ]]
}

start_adb_keepalive() {
  if [[ "$ADB_KEEPALIVE" != "1" ]]; then
    return 0
  fi
  mkdir -p "$(compare_artifact_dir)"
  local log_path
  log_path="$(compare_artifact_dir)/adb-keepalive.jsonl"
  (
    while true; do
      python3 - "$log_path" "tick" <<'PY' || true
import json, sys, time
with open(sys.argv[1], "a", encoding="utf-8") as f:
    f.write(json.dumps({"ts": time.time(), "event": sys.argv[2]}, separators=(",", ":")) + "\n")
PY
      if command -v timeout >/dev/null 2>&1; then
        timeout "${ADB_KEEPALIVE_TIMEOUT_SEC}s" "$ADB" shell ':' >/dev/null 2>&1 || true
      else
        "$ADB" shell ':' >/dev/null 2>&1 || true
      fi
      sleep "$ADB_KEEPALIVE_INTERVAL_SEC"
    done
  ) &
  ADB_KEEPALIVE_PID="$!"
}

stop_adb_keepalive() {
  if [[ -n "${ADB_KEEPALIVE_PID:-}" ]]; then
    kill "$ADB_KEEPALIVE_PID" >/dev/null 2>&1 || true
    wait "$ADB_KEEPALIVE_PID" >/dev/null 2>&1 || true
    ADB_KEEPALIVE_PID=""
  fi
}

start_daemon_for_test() {
  "$ADB" shell am broadcast \
    -n "$PKG/$CLASS_PREFIX.PdockerdDebugReceiver" \
    -a "$ACTION_PREFIX.action.SMOKE_START" >/dev/null 2>&1 || true
}

restart_app_daemon_for_test() {
  # Native executor binaries are loaded by the long-lived pdockerd process.  A
  # reinstall alone can leave old executors alive, which makes GPU bridge tests
  # appear to run new code while actually exercising stale code.  Kill only
  # app-owned pdocker native children for this repeatable test route; normal UI
  # operation is not changed and this route intentionally avoids force-stopping
  # the app or any user application.
  if [[ "$RESTART_APP_DAEMON" != "1" ]]; then
    return 0
  fi
  "$ADB" shell "run-as $PKG sh -c 'pkill -x pdocker-gpu-executor 2>/dev/null; pkill -x pdocker-media-executor 2>/dev/null; pkill -f pdockerd 2>/dev/null; true'" >/dev/null 2>&1 || true
  sleep 1
  start_daemon_for_test
}

operation_notify() {
  local status="$1"
  local detail="$2"
  local finished="${3:-0}"
  local json len
  json="$(python3 - "$OP_ID" "$status" "$detail" "$finished" <<'PY'
import json
import sys

op_id, status, detail, finished = sys.argv[1:5]
print(json.dumps({
    "Id": op_id,
    "Kind": "llama-gpu-compare",
    "Title": "llama.cpp GPU compare",
    "Status": status,
    "Detail": detail,
    "Finished": finished == "1",
}, separators=(",", ":")))
PY
)"
  len="$(printf "%s" "$json" | wc -c | tr -d ' ')"
  run_as "cd files && { printf 'POST /system/operations HTTP/1.1\r\nHost: pdocker\r\nContent-Type: application/json\r\nContent-Length: $len\r\nConnection: close\r\n\r\n'; printf %s $(remote_quote "$json"); } | toybox nc -U -W $OPERATION_NOTIFY_TIMEOUT_SEC pdocker/pdockerd.sock >/dev/null 2>&1 || true" >/dev/null 2>&1 || true
}

stop_stale_target_if_engine_alive() {
  if [[ "$STOP_STALE_TARGET_BEFORE_PREFLIGHT" != "1" ]]; then
    return 0
  fi
  local encoded
  encoded="$(urlencode "$CONTAINER")"
  # Narrow cleanup only: if a previous compare left the target llama container
  # alive, stop that Engine object before measuring headroom.  Do not start
  # pdockerd here; the preflight must remain a low-impact device safety gate.
  run_as "cd files && test -S pdocker/pdockerd.sock && { printf 'POST /containers/$encoded/stop HTTP/1.1\r\nHost: pdocker\r\nContent-Length: 0\r\nConnection: close\r\n\r\n'; } | toybox nc -U -W 3 pdocker/pdockerd.sock >/dev/null 2>&1 || true" >/dev/null 2>&1 || true
}

wait_for_engine() {
  local i
  if ! adb_transport_ok; then
    echo "[pdocker llama compare] adb transport lost before pdockerd startup: state=$(adb_transport_state)" >&2
    return 111
  fi
  start_daemon_for_test
  for i in $(seq 1 45); do
    if ! adb_transport_ok; then
      echo "[pdocker llama compare] adb transport lost while waiting for pdockerd: state=$(adb_transport_state) attempt=$i/45" >&2
      return 111
    fi
    if run_as 'cd files && test -S pdocker/pdockerd.sock && { printf "GET /_ping HTTP/1.1\r\nHost: pdocker\r\nConnection: close\r\n\r\n"; } | toybox nc -U -W 3 pdocker/pdockerd.sock | grep -q OK' >/dev/null 2>&1; then
      return 0
    fi
    if (( i % 5 == 0 )); then
      start_daemon_for_test
    fi
    sleep 1
  done
  echo "pdockerd socket did not appear" >&2
  return 1
}

stage_probe_artifact_for_container() {
  local env_name="$1"
  local source_path="${!env_name:-}"
  if [[ -z "$source_path" ]]; then
    return 0
  fi
  case "$source_path" in
    /workspace/*)
      return 0
      ;;
  esac
  if [[ -z "${DEVICE_WORKSPACE_HOST:-}" ]]; then
    echo "[pdocker llama compare] cannot stage $env_name before DEVICE_WORKSPACE_HOST is resolved" >&2
    return 1
  fi
  local base staged_host staged_container
  base="$(basename "$source_path")"
  staged_host="$DEVICE_WORKSPACE_HOST/.pdocker-probes/$base"
  staged_container="/workspace/.pdocker-probes/$base"
  if [[ -f "$source_path" ]]; then
    # Host-side probe paths such as /tmp/q6write10-bundle/*.spv are not visible
    # from Android run-as.  Push them through /data/local/tmp first, then copy
    # into the app-owned workspace that is mounted as /workspace in the
    # container view.  Device/app-private paths still use the original run-as
    # copy below.
    local device_tmp="/data/local/tmp/pdocker-probe-$$-$RANDOM-$base"
    "$ADB" push "$source_path" "$device_tmp" >/dev/null
    run_as "mkdir -p $(remote_quote "$DEVICE_WORKSPACE_HOST/.pdocker-probes") && cp -f $(remote_quote "$device_tmp") $(remote_quote "$staged_host") && chmod 0644 $(remote_quote "$staged_host")" >/dev/null
    "$ADB" shell "rm -f $(remote_quote "$device_tmp")" >/dev/null 2>&1 || true
  else
    run_as "mkdir -p $(remote_quote "$DEVICE_WORKSPACE_HOST/.pdocker-probes") && cp -f $(remote_quote "$source_path") $(remote_quote "$staged_host") && chmod 0644 $(remote_quote "$staged_host")" >/dev/null
  fi
  export "$env_name=$staged_container"
  echo "[pdocker llama compare] staged $env_name for container: $source_path -> $staged_container" >&2
}

stage_spirv_probe_artifacts_for_container() {
  # The Vulkan ICD runs inside the container view.  Android app-private
  # absolute paths such as /data/user/0/... are valid to run-as but are
  # intentionally not a stable container ABI path after direct-executor path
  # rewriting.  Stage probe inputs through the already-mounted /workspace
  # volume so the shader replay path stays container-native and reproducible.
  stage_probe_artifact_for_container "PDOCKER_GPU_SPIRV_PROBE_MANIFEST"
  stage_probe_artifact_for_container "PDOCKER_GPU_SPIRV_PROBE_SHADER"
}

memory_snapshot_json() {
  local raw
  raw="$("$ADB" shell 'cat /proc/meminfo 2>/dev/null; echo ---FREE-M---; free -m 2>/dev/null' 2>/dev/null || true)"
  python3 - "$raw" <<'PY'
import json
import re
import sys

raw = sys.argv[1] if len(sys.argv) > 1 else ""
snap = {
    "valid": False,
    "raw_bytes": len(raw.encode("utf-8", errors="ignore")),
    "mem_total_mb": 0,
    "mem_used_mb": 0,
    "mem_free_mb": 0,
    "mem_available_mb": 0,
    "mem_preflight_free_mb": 0,
    "swap_total_mb": 0,
    "swap_used_mb": 0,
    "swap_free_mb": 0,
    "swap_cached_mb": 0,
}
def kb_to_mb(value):
    return int(value) // 1024

for line in raw.splitlines():
    m = re.match(r"^([A-Za-z_()]+):\s+([0-9]+)\s+kB$", line.strip())
    if not m:
        continue
    key, value = m.group(1), int(m.group(2))
    if key == "MemTotal":
        snap["mem_total_mb"] = kb_to_mb(value)
    elif key == "MemFree":
        snap["mem_free_mb"] = kb_to_mb(value)
    elif key == "MemAvailable":
        snap["mem_available_mb"] = kb_to_mb(value)
    elif key == "SwapTotal":
        snap["swap_total_mb"] = kb_to_mb(value)
    elif key == "SwapFree":
        snap["swap_free_mb"] = kb_to_mb(value)
    elif key == "SwapCached":
        snap["swap_cached_mb"] = kb_to_mb(value)

for line in raw.splitlines():
    parts = line.split()
    if not parts:
        continue
    if parts[0].startswith("Mem:") and len(parts) >= 4:
        snap["mem_total_mb"] = snap["mem_total_mb"] or int(parts[1])
        snap["mem_used_mb"] = int(parts[2])
        snap["mem_free_mb"] = snap["mem_free_mb"] or int(parts[3])
    elif parts[0].startswith("Swap:") and len(parts) >= 4:
        snap["swap_total_mb"] = snap["swap_total_mb"] or int(parts[1])
        snap["swap_used_mb"] = int(parts[2])
        snap["swap_free_mb"] = snap["swap_free_mb"] or int(parts[3])
if not snap["mem_used_mb"] and snap["mem_total_mb"]:
    snap["mem_used_mb"] = max(0, snap["mem_total_mb"] - snap["mem_free_mb"])
snap["mem_preflight_free_mb"] = snap["mem_available_mb"] or snap["mem_free_mb"]
snap["valid"] = snap["mem_total_mb"] > 0 and snap["mem_preflight_free_mb"] > 0
print(json.dumps(snap, separators=(",", ":")))
PY
}

memory_snapshot_is_valid() {
  local snap="$1"
  python3 - "$snap" <<'PY'
import json
import sys

try:
    data = json.loads(sys.argv[1])
except Exception:
    data = {}
valid = bool(data.get("valid"))
mem_total = int(data.get("mem_total_mb") or 0)
preflight_free = int(
    data.get("mem_preflight_free_mb")
    or data.get("mem_available_mb")
    or data.get("mem_free_mb")
    or 0
)
# Treat only a complete memory sample as authoritative.  A disconnected adb
# read, an empty /proc/meminfo, or the separator-only output from the shell
# command must not be interpreted as Android OOM pressure.
sys.exit(0 if (valid or (mem_total > 0 and preflight_free > 0)) else 1)
PY
}

memory_diagnostic_commands_json() {
  python3 - "$ADB" "${ANDROID_SERIAL:-}" "$PKG" "$CONTAINER" <<'PY'
import json
import shlex
import sys

adb, serial, pkg, container = sys.argv[1:5]

def adb_prefix() -> str:
    prefix = shlex.quote(adb)
    if serial:
        prefix = f"ANDROID_SERIAL={shlex.quote(serial)} {prefix}"
    return prefix

def adb_shell(command: str) -> str:
    return f"{adb_prefix()} shell {shlex.quote(command)}"

process_check = (
    "ps -A -o PID,PPID,RSS,VSZ,NAME,ARGS 2>/dev/null "
    f"| grep -E '(pdocker|llama|{pkg})' || true"
)
proc_status_check = (
    "for p in /proc/[0-9]*; do "
    "cmd=$(tr '\\0' ' ' < \"$p/cmdline\" 2>/dev/null || true); "
    "case \"$cmd\" in *pdocker*|*llama*) "
    "rss=$(grep -m1 '^VmRSS:' \"$p/status\" 2>/dev/null | awk '{print $2\" \"$3}'); "
    "printf '%s rss=%s cmd=%s\\n' \"${p##*/}\" \"$rss\" \"$cmd\";; esac; "
    "done"
)
commands = [
    adb_shell("cat /proc/meminfo | egrep 'MemAvailable|SwapFree|SwapTotal'"),
    adb_shell(f"run-as {shlex.quote(pkg)} sh -c {shlex.quote(process_check)}"),
    adb_shell(f"run-as {shlex.quote(pkg)} sh -c {shlex.quote(proc_status_check)}"),
]
print(json.dumps(commands, separators=(",", ":")))
PY
}

memory_cleanup_commands_json() {
  python3 - "$ADB" "${ANDROID_SERIAL:-}" "$PKG" "$CONTAINER" "$LOCAL_PORT" <<'PY'
import json
import shlex
import sys
import urllib.parse

adb, serial, pkg, container, local_port = sys.argv[1:6]

def adb_prefix() -> str:
    prefix = shlex.quote(adb)
    if serial:
        prefix = f"ANDROID_SERIAL={shlex.quote(serial)} {prefix}"
    return prefix

def adb_shell(command: str) -> str:
    return f"{adb_prefix()} shell {shlex.quote(command)}"

encoded_container = urllib.parse.quote(container, safe="")
engine_stop = (
    "cd files && test -S pdocker/pdockerd.sock && "
    "printf 'POST /containers/%s/stop HTTP/1.1\\r\\nHost: pdocker\\r\\n"
    "Content-Length: 0\\r\\nConnection: close\\r\\n\\r\\n' "
    "| toybox nc -U -W 3 pdocker/pdockerd.sock >/dev/null || true"
) % encoded_container
engine_remove = (
    "cd files && test -S pdocker/pdockerd.sock && "
    "printf 'DELETE /containers/%s?force=true HTTP/1.1\\r\\nHost: pdocker\\r\\n"
    "Content-Length: 0\\r\\nConnection: close\\r\\n\\r\\n' "
    "| toybox nc -U -W 3 pdocker/pdockerd.sock >/dev/null || true"
) % encoded_container
executor_cleanup = (
    "pkill -x pdocker-gpu-executor 2>/dev/null; "
    "pkill -x pdocker-media-executor 2>/dev/null; "
    "true"
)
commands = [
    adb_shell(f"run-as {shlex.quote(pkg)} sh -c {shlex.quote(engine_stop)}"),
    adb_shell(f"run-as {shlex.quote(pkg)} sh -c {shlex.quote(engine_remove)}"),
    adb_shell(f"run-as {shlex.quote(pkg)} sh -c {shlex.quote(executor_cleanup)}"),
    f"{adb_prefix()} forward --remove tcp:{shlex.quote(str(local_port))}",
]
print(json.dumps(commands, separators=(",", ":")))
PY
}

memory_threshold_state_json() {
  local snap="$1"
  local min_free="$2"
  local min_swap="$3"
  local mem_key="${4:-mem_preflight_free_mb}"
  local advisory_swap="${5:-0}"
  python3 - "$snap" "$min_free" "$min_swap" "$mem_key" "$advisory_swap" <<'PY'
import json
import sys

snap_s, min_free_s, min_swap_s, mem_key, advisory_swap_s = sys.argv[1:6]
try:
    snap = json.loads(snap_s)
except Exception:
    snap = {}
try:
    min_free = int(min_free_s)
except Exception:
    min_free = 0
try:
    min_swap = int(min_swap_s)
except Exception:
    min_swap = 0
try:
    advisory_swap = int(advisory_swap_s)
except Exception:
    advisory_swap = 0

def observed(*keys: str) -> int:
    for key in keys:
        try:
            value = int(snap.get(key) or 0)
        except Exception:
            value = 0
        if value:
            return value
    return 0

mem_observed = observed(mem_key, "mem_preflight_free_mb", "mem_available_mb", "mem_free_mb")
swap_observed = observed("swap_free_mb")
swap_hard_ok = min_swap <= 0 or swap_observed >= min_swap
swap_advisory_ok = advisory_swap <= 0 or swap_observed >= advisory_swap
state = {
    "summary": "pass" if mem_observed >= min_free and swap_hard_ok else "fail",
    mem_key: {
        "observed_mb": mem_observed,
        "required_min_mb": min_free,
        "ok": mem_observed >= min_free,
    },
    "swap_free_mb": {
        "observed_mb": swap_observed,
        "hard_required_min_mb": min_swap,
        "advisory_min_mb": advisory_swap,
        "hard_gate_enabled": min_swap > 0,
        "ok": swap_hard_ok,
        "advisory_ok": swap_advisory_ok,
        "state": "ok" if swap_hard_ok else "below-hard-threshold",
        "advisory_state": "ok" if swap_advisory_ok else "below-advisory-threshold",
    },
    "swap_policy": {
        "default": "advisory",
        "hard_gate_enabled": min_swap > 0,
        "hard_min_swap_free_mb": min_swap,
        "advisory_swap_free_mb": advisory_swap,
        "swap_pressure_advisory": not swap_advisory_ok,
    },
}
print(json.dumps(state, separators=(",", ":")))
PY
}

pdocker_memory_diagnostics_json() {
  local raw_ps app_ps socket_state commands cleanup_commands
  raw_ps="$("$ADB" shell "ps -A -o PID,PPID,RSS,VSZ,NAME,ARGS 2>/dev/null || ps -A 2>/dev/null" 2>/dev/null || true)"
  app_ps="$(run_as "ps -A -o PID,PPID,RSS,VSZ,NAME,ARGS 2>/dev/null || ps -A 2>/dev/null" 2>/dev/null || true)"
  socket_state="$(run_as "cd files 2>/dev/null && if test -S pdocker/pdockerd.sock; then echo present; else echo absent; fi" 2>/dev/null | tr -d '\r' || true)"
  commands="$(memory_diagnostic_commands_json || printf '[]')"
  cleanup_commands="$(memory_cleanup_commands_json || printf '[]')"
  python3 - "$PKG" "$CONTAINER" "$raw_ps" "$app_ps" "$socket_state" "$commands" "$cleanup_commands" <<'PY'
import json
import re
import sys
from datetime import datetime, timezone

pkg, container, raw_ps, app_ps, socket_state, commands_s, cleanup_commands_s = sys.argv[1:8]
try:
    commands = json.loads(commands_s)
except Exception:
    commands = []
try:
    cleanup_commands = json.loads(cleanup_commands_s)
except Exception:
    cleanup_commands = []

tokens = ("pdocker", "llama", pkg.lower(), container.lower())

def parse_rows(raw: str, source: str) -> list[dict[str, object]]:
    rows = []
    header = []
    rss_index = None
    for line in raw.splitlines():
        clean = line.strip().replace("\r", "")
        if not clean:
            continue
        parts = clean.split()
        if parts and any(name in parts for name in ("PID", "RSS", "ARGS", "NAME")):
            header = parts
            try:
                rss_index = header.index("RSS")
            except ValueError:
                rss_index = None
            continue
        lowered = clean.lower()
        if not any(token and token in lowered for token in tokens):
            continue
        rss_kb = None
        if rss_index is not None and len(parts) > rss_index:
            try:
                rss_kb = int(re.sub(r"[^0-9]", "", parts[rss_index]) or "0")
            except ValueError:
                rss_kb = None
        elif len(parts) >= 5:
            # Common Android toybox ps fallback: USER PID PPID VSZ RSS ...
            try:
                rss_kb = int(re.sub(r"[^0-9]", "", parts[4]) or "0")
            except ValueError:
                rss_kb = None
        pid = None
        ppid = None
        name = None
        args = None
        if header:
            values = {key: parts[index] for index, key in enumerate(header) if index < len(parts)}
            for key in ("PID", "Pid", "pid"):
                if key in values:
                    try:
                        pid = int(re.sub(r"[^0-9]", "", values[key]) or "0")
                    except ValueError:
                        pid = None
                    break
            for key in ("PPID", "Ppid", "ppid"):
                if key in values:
                    try:
                        ppid = int(re.sub(r"[^0-9]", "", values[key]) or "0")
                    except ValueError:
                        ppid = None
                    break
            name = values.get("NAME") or values.get("COMM") or values.get("CMD")
            if "ARGS" in header:
                args_index = header.index("ARGS")
                if len(parts) > args_index:
                    args = " ".join(parts[args_index:])
        elif len(parts) >= 2:
            try:
                pid = int(re.sub(r"[^0-9]", "", parts[1]) or "0")
            except ValueError:
                pid = None
            if len(parts) >= 3:
                try:
                    ppid = int(re.sub(r"[^0-9]", "", parts[2]) or "0")
                except ValueError:
                    ppid = None
            name = parts[-1]
        rows.append(
            {
                "source": source,
                "pid": pid,
                "ppid": ppid,
                "name": name,
                "args": args,
                "raw": clean[:500],
                "rss_mb": round(rss_kb / 1024.0, 1) if rss_kb is not None else None,
                "stale_llama_hint": "llama" in lowered or container.lower() in lowered,
            }
        )
    return rows

processes = parse_rows(raw_ps, "adb-ps")
seen = {item["raw"] for item in processes}
for item in parse_rows(app_ps, "run-as-ps"):
    if item["raw"] not in seen:
        seen.add(item["raw"])
        processes.append(item)

top_rss_processes = sorted(
    processes,
    key=lambda item: (
        float(item.get("rss_mb")) if isinstance(item.get("rss_mb"), (int, float)) else -1.0,
        str(item.get("raw") or ""),
    ),
    reverse=True,
)
rss_values = [item["rss_mb"] for item in processes if isinstance(item.get("rss_mb"), (int, float))]
report = {
    "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "package": pkg,
    "container": container,
    "pdockerd_socket": (socket_state.strip() or "unknown"),
    "process_count": len(processes),
    "process_rss_mb_total": round(sum(rss_values), 1) if rss_values else None,
    "stale_llama_process_hint": any(bool(item.get("stale_llama_hint")) for item in processes),
    "top_rss_processes": top_rss_processes[:10],
    "process_sample": top_rss_processes[:32],
    "diagnostic_commands": commands,
    "cleanup_commands": cleanup_commands,
    "note": "Best-effort snapshot only; the compare preflight did not force-stop user apps.",
}
print(json.dumps(report, separators=(",", ":")))
PY
}

ensure_memory_headroom() {
  local phase="$1"
  local snap free_mb swap_free_mb swap_hard_block diagnostics thresholds cleanup_commands
  snap="$(memory_snapshot_json || printf '{}')"
  if ! memory_snapshot_is_valid "$snap"; then
    echo "[pdocker llama compare] runtime memory sample unavailable during $phase; continuing without treating missing /proc/meminfo as OOM: $snap" >&2
    return 0
  fi
  free_mb="$(python3 - "$snap" <<'PY'
import json, sys
try:
    data = json.loads(sys.argv[1])
    print(int(data.get("mem_preflight_free_mb") or data.get("mem_available_mb") or data.get("mem_free_mb") or 0))
except Exception:
    print(0)
PY
)"
  swap_free_mb="$(python3 - "$snap" <<'PY'
import json, sys
try:
    print(int(json.loads(sys.argv[1]).get("swap_free_mb") or 0))
except Exception:
    print(0)
PY
)"
  swap_hard_block=0
  if (( MIN_SWAP_FREE_MB > 0 && swap_free_mb < MIN_SWAP_FREE_MB )); then
    swap_hard_block=1
  fi
  if (( free_mb < MIN_FREE_MB || swap_hard_block )); then
    diagnostics="$(pdocker_memory_diagnostics_json || printf '{}')"
    thresholds="$(memory_threshold_state_json "$snap" "$MIN_FREE_MB" "$MIN_SWAP_FREE_MB" "mem_preflight_free_mb" "$SWAP_ADVISORY_MB" || printf '{}')"
    operation_notify "failed" "Insufficient memory before $phase: $snap" 1 >/dev/null 2>&1 || true
    python3 - "$OUT" "$phase" "$snap" "$MIN_FREE_MB" "$MIN_SWAP_FREE_MB" "$SWAP_ADVISORY_MB" "$diagnostics" "$thresholds" <<'PY' || true
import json
import sys
from pathlib import Path

out, phase, snap_s, min_free, min_swap, advisory_swap, diagnostics_s, thresholds_s = sys.argv[1:9]
try:
    snap = json.loads(snap_s)
except Exception:
    snap = {}
try:
    diagnostics = json.loads(diagnostics_s)
except Exception:
    diagnostics = {}
try:
    thresholds = json.loads(thresholds_s)
except Exception:
    thresholds = {}
commands = diagnostics.get("diagnostic_commands") if isinstance(diagnostics, dict) else []
if not isinstance(commands, list):
    commands = []
cleanup_commands = diagnostics.get("cleanup_commands") if isinstance(diagnostics, dict) else []
if not isinstance(cleanup_commands, list):
    cleanup_commands = []
swap_state = thresholds.get("swap_free_mb") if isinstance(thresholds, dict) else {}
if not isinstance(swap_state, dict):
    swap_state = {
        "observed_mb": int(snap.get("swap_free_mb") or 0),
        "hard_required_min_mb": int(min_swap),
        "advisory_min_mb": int(advisory_swap),
        "hard_gate_enabled": int(min_swap) > 0,
        "ok": int(min_swap) <= 0 or int(snap.get("swap_free_mb") or 0) >= int(min_swap),
    }
swap_state.setdefault("state", "ok" if bool(swap_state.get("ok")) else "below-hard-threshold")
swap_policy = thresholds.get("swap_policy") if isinstance(thresholds, dict) else {}
if not isinstance(swap_policy, dict):
    swap_policy = {
        "default": "advisory",
        "hard_gate_enabled": int(min_swap) > 0,
        "hard_min_swap_free_mb": int(min_swap),
        "advisory_swap_free_mb": int(advisory_swap),
        "swap_pressure_advisory": int(snap.get("swap_free_mb") or 0) < int(advisory_swap),
    }
report = {
    "error": "insufficient_memory",
    "phase": phase,
    "memory": snap,
    "required": {
        "mem_free_mb": int(min_free),
        "swap_free_mb": int(min_swap),
        "swap_free_hard_gate_enabled": int(min_swap) > 0,
        "swap_free_advisory_mb": int(advisory_swap),
    },
    "memory_thresholds": thresholds,
    "swap_policy": swap_policy,
    "swap_free_threshold": swap_state,
    "swap_free_threshold_state": swap_state.get("state"),
    "next_blocker": (
        f"insufficient Android memory before {phase}; "
        f"require mem_free>={min_free}MB"
        + (f" and swap_free>={min_swap}MB" if int(min_swap) > 0 else "; SwapFree is advisory by default")
    ),
    "pdocker_memory_diagnostics": diagnostics,
    "diagnostic_commands": commands,
    "cleanup_commands": cleanup_commands,
    "device_actions": [
        "Do not start or classify the llama GPU compare while this memory blocker is present; this is not a GPU correctness result.",
        "Check MemAvailable with the first diagnostic command; low SwapFree on Android zram is advisory unless a hard swap threshold was explicitly configured.",
        "Use the pdocker process diagnostic commands to identify app-owned pdockerd, executor, or stale llama processes and their RSS before taking action.",
        "If Skydnir-owned stale llama work is present, use cleanup_commands in order: stop/remove only the Skydnir llama container, then clear app-owned executors if needed; do not force-stop apps.",
        "Close memory-heavy foreground apps only with user approval; do not force-stop the browser/VS Code session during automated runs.",
        "Wait until MemAvailable and SwapFree recover, then rerun with PDOCKER_LLAMA_WAIT_FOR_MEMORY_SEC set.",
        "Keep the generated JSON artifact with the APK/build commit for regression evidence.",
    ],
}
Path(out).parent.mkdir(parents=True, exist_ok=True)
Path(out).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
    if (( MIN_SWAP_FREE_MB > 0 )); then
      echo "[pdocker llama compare] insufficient memory before $phase: $snap; require mem_free>=${MIN_FREE_MB}MB swap_free>=${MIN_SWAP_FREE_MB}MB" >&2
    else
      echo "[pdocker llama compare] insufficient memory before $phase: $snap; require mem_free>=${MIN_FREE_MB}MB (swap_free is advisory, warn below ${SWAP_ADVISORY_MB}MB)" >&2
    fi
    return 1
  fi
  if (( SWAP_ADVISORY_MB > 0 && swap_free_mb < SWAP_ADVISORY_MB )); then
    echo "[pdocker llama compare] memory before $phase has low Android zram SwapFree (${swap_free_mb}MB < advisory ${SWAP_ADVISORY_MB}MB); continuing because swap is advisory by default" >&2
  fi
  echo "[pdocker llama compare] memory before $phase: $snap"
}

wait_for_memory_headroom() {
  local phase="$1"
  local waited=0
  while true; do
    if ensure_memory_headroom "$phase"; then
      return 0
    fi
    if (( WAIT_FOR_MEMORY_SEC <= 0 || waited >= WAIT_FOR_MEMORY_SEC )); then
      return 1
    fi
    echo "[pdocker llama compare] waiting for memory headroom (${waited}/${WAIT_FOR_MEMORY_SEC}s)" >&2
    sleep "$WAIT_FOR_MEMORY_INTERVAL_SEC"
    waited=$(( waited + WAIT_FOR_MEMORY_INTERVAL_SEC ))
  done
}

runtime_memory_headroom_ok() {
  local phase="$1"
  local snap free_mb swap_free_mb swap_hard_block diagnostics thresholds
  snap="$(memory_snapshot_json || printf '{}')"
  if ! memory_snapshot_is_valid "$snap"; then
    echo "[pdocker llama compare] runtime memory sample unavailable during $phase; continuing without treating missing /proc/meminfo as OOM: $snap" >&2
    return 0
  fi
  free_mb="$(python3 - "$snap" <<'PY'
import json, sys
try:
    data = json.loads(sys.argv[1])
    print(int(data.get("mem_preflight_free_mb") or data.get("mem_available_mb") or data.get("mem_free_mb") or 0))
except Exception:
    print(0)
PY
)"
  swap_free_mb="$(python3 - "$snap" <<'PY'
import json, sys
try:
    print(int(json.loads(sys.argv[1]).get("swap_free_mb") or 0))
except Exception:
    print(0)
PY
)"
  swap_hard_block=0
  if (( RUNTIME_MIN_SWAP_FREE_MB > 0 && swap_free_mb < RUNTIME_MIN_SWAP_FREE_MB )); then
    swap_hard_block=1
  fi
  if (( free_mb < RUNTIME_MIN_FREE_MB || swap_hard_block )); then
    diagnostics="$(pdocker_memory_diagnostics_json || printf '{}')"
    thresholds="$(memory_threshold_state_json "$snap" "$RUNTIME_MIN_FREE_MB" "$RUNTIME_MIN_SWAP_FREE_MB" "mem_preflight_free_mb" "$RUNTIME_SWAP_ADVISORY_MB" || printf '{}')"
    python3 - "$RUNTIME_ABORT_JSON" "$phase" "$snap" "$RUNTIME_MIN_FREE_MB" "$RUNTIME_MIN_SWAP_FREE_MB" "$RUNTIME_SWAP_ADVISORY_MB" "$diagnostics" "$thresholds" <<'PY' || true
import json
import sys
from pathlib import Path

out, phase, snap_s, min_free, min_swap, advisory_swap, diagnostics_s, thresholds_s = sys.argv[1:9]
try:
    snap = json.loads(snap_s)
except Exception:
    snap = {}
try:
    diagnostics = json.loads(diagnostics_s)
except Exception:
    diagnostics = {}
try:
    thresholds = json.loads(thresholds_s)
except Exception:
    thresholds = {}
commands = diagnostics.get("diagnostic_commands") if isinstance(diagnostics, dict) else []
if not isinstance(commands, list):
    commands = []
cleanup_commands = diagnostics.get("cleanup_commands") if isinstance(diagnostics, dict) else []
if not isinstance(cleanup_commands, list):
    cleanup_commands = []
swap_state = thresholds.get("swap_free_mb") if isinstance(thresholds, dict) else {}
if not isinstance(swap_state, dict):
    swap_state = {
        "observed_mb": int(snap.get("swap_free_mb") or 0),
        "hard_required_min_mb": int(min_swap),
        "advisory_min_mb": int(advisory_swap),
        "hard_gate_enabled": int(min_swap) > 0,
        "ok": int(min_swap) <= 0 or int(snap.get("swap_free_mb") or 0) >= int(min_swap),
    }
swap_state.setdefault("state", "ok" if bool(swap_state.get("ok")) else "below-hard-threshold")
swap_policy = thresholds.get("swap_policy") if isinstance(thresholds, dict) else {}
if not isinstance(swap_policy, dict):
    swap_policy = {
        "default": "advisory",
        "hard_gate_enabled": int(min_swap) > 0,
        "hard_min_swap_free_mb": int(min_swap),
        "advisory_swap_free_mb": int(advisory_swap),
        "swap_pressure_advisory": int(snap.get("swap_free_mb") or 0) < int(advisory_swap),
    }
Path(out).write_text(json.dumps({
    "error": "runtime_memory_pressure",
    "phase": phase,
    "memory": snap,
    "required": {
        "mem_preflight_free_mb": int(min_free),
        "swap_free_mb": int(min_swap),
        "swap_free_hard_gate_enabled": int(min_swap) > 0,
        "swap_free_advisory_mb": int(advisory_swap),
    },
    "memory_thresholds": thresholds,
    "swap_policy": swap_policy,
    "swap_free_threshold": swap_state,
    "swap_free_threshold_state": swap_state.get("state"),
    "next_blocker": (
        f"stopped llama GPU attempt during {phase} before Android LMK/OOM; "
        f"require mem_available>={min_free}MB"
        + (f" and swap_free>={min_swap}MB" if int(min_swap) > 0 else "; SwapFree is advisory by default")
    ),
    "pdocker_memory_diagnostics": diagnostics,
    "diagnostic_commands": commands,
    "cleanup_commands": cleanup_commands,
    "device_actions": [
        "Inspect the generated report before rerunning; it indicates a device-memory failure, not a GPU-correctness result.",
        "Use the pdocker process diagnostic commands to identify app-owned pdockerd, executor, or stale llama processes and their RSS before taking action.",
        "If stale Skydnir llama work is present, use cleanup_commands in order: stop/remove only the Skydnir llama container, then clear app-owned executors if needed; do not force-stop apps.",
        "Treat low SwapFree as Android zram pressure evidence, not a hard failure unless a strict swap threshold was configured.",
        "Rerun with the same APK and output path after memory recovers; do not rebuild the llama image just because this guard fired.",
    ],
}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
    operation_notify "failed" "Runtime memory pressure during $phase: $snap" 1 >/dev/null 2>&1 || true
    echo "[pdocker llama compare] runtime memory pressure during $phase: $snap; stopping container" >&2
    remove_container >/dev/null 2>&1 || true
    return 1
  fi
  if (( RUNTIME_SWAP_ADVISORY_MB > 0 && swap_free_mb < RUNTIME_SWAP_ADVISORY_MB )); then
    echo "[pdocker llama compare] runtime low Android zram SwapFree during $phase (${swap_free_mb}MB < advisory ${RUNTIME_SWAP_ADVISORY_MB}MB); continuing because swap is advisory by default" >&2
  fi
  return 0
}

urlencode() {
  python3 - "$1" <<'PY'
import sys
import urllib.parse
print(urllib.parse.quote(sys.argv[1], safe=""))
PY
}

http_body() {
  python3 -c 'import sys
data = sys.stdin.buffer.read()
split = data.find(b"\r\n\r\n")
if split < 0:
    split = data.find(b"\n\n")
    offset = 2
else:
    offset = 4
body = data[split + offset:] if split >= 0 else data
sys.stdout.buffer.write(body)'
}

engine_request() {
  local method="$1"
  local path="$2"
  local body="${3-}"
  local len=0
  if [[ $# -ge 3 ]]; then
    len="$(printf "%s" "$body" | wc -c | tr -d ' ')"
    run_as "cd files && { printf '%s %s HTTP/1.1\r\nHost: pdocker\r\nContent-Type: application/json\r\nContent-Length: %s\r\nConnection: close\r\n\r\n' $(remote_quote "$method") $(remote_quote "$path") $(remote_quote "$len"); printf %s $(remote_quote "$body"); } | toybox nc -U -W 10 pdocker/pdockerd.sock"
  else
    run_as "cd files && { printf '%s %s HTTP/1.1\r\nHost: pdocker\r\nConnection: close\r\n\r\n' $(remote_quote "$method") $(remote_quote "$path"); } | toybox nc -U -W 10 pdocker/pdockerd.sock"
  fi
}

engine_body() {
  engine_request "$@" | http_body
}

engine_request_with_host_timeout() {
  local timeout_sec="$1"
  shift
  RUN_AS_TIMEOUT_SEC="$timeout_sec" engine_request "$@"
}

parse_engine_id() {
  python3 -c 'import json,sys
body = sys.stdin.read()
try:
    data = json.loads(body)
except Exception as exc:
    print(f"Engine response was not JSON: {exc}: {body[:500]}", file=sys.stderr)
    raise SystemExit(1)
ident = data.get("Id")
if not ident:
    print(f"Engine response did not include Id: {json.dumps(data, ensure_ascii=False)[:500]}", file=sys.stderr)
    raise SystemExit(1)
print(ident)'
}

engine_body_has_id() {
  python3 -c 'import json,sys
body = sys.stdin.read()
try:
    data = json.loads(body)
except Exception:
    raise SystemExit(1)
raise SystemExit(0 if isinstance(data.get("Id"), str) and data.get("Id") else 1)'
}

decode_engine_logs() {
  python3 -c 'import sys
data = sys.stdin.buffer.read()
split = data.find(b"\r\n\r\n")
if split >= 0:
    data = data[split + 4:]
out = bytearray()
idx = 0
while idx + 8 <= len(data):
    size = int.from_bytes(data[idx + 4:idx + 8], "big")
    idx += 8
    if size <= 0 or idx + size > len(data):
        idx -= 8
        break
    out.extend(data[idx:idx + size])
    idx += size
if idx < len(data):
    out.extend(data[idx:])
sys.stdout.buffer.write(out)'
}

container_payload() {
  local mode="$1"
  local ctx="$2"
  local gpu_layers="${3:-}"
  python3 - "$IMAGE" "$DEVICE_PROJECT" "$DEVICE_MODEL_HOST" "$DEVICE_WORKSPACE_HOST" "$mode" "$ctx" "$gpu_layers" "$REMOTE_PORT" "$TRACE_ALLOC" "$MODEL_PATH" "$MODEL_URL" "$ROOT/scripts/llama-gpu-env-manifest.json" <<'PY'
import json
import os
import sys
from pathlib import Path

image, project, model_host, workspace_host, mode, ctx, gpu_layers, port, trace_alloc, model_path, model_url, manifest_path = sys.argv[1:13]

manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
if manifest.get("schema") != "pdocker.llama.gpu.env-manifest.v1":
    raise SystemExit(f"unsupported llama GPU env manifest schema: {manifest_path}")

def string_list(name):
    values = manifest.get(name)
    if not isinstance(values, list) or not all(isinstance(value, str) and value for value in values):
        raise SystemExit(f"invalid {name} in llama GPU env manifest: {manifest_path}")
    return values

forward_env_keys = string_list("compare_forward_env_keys")
mode_profiles = manifest.get("compare_mode_env_profiles")
if not isinstance(mode_profiles, dict):
    raise SystemExit(f"invalid compare_mode_env_profiles in llama GPU env manifest: {manifest_path}")
probe_env_keys = string_list("compare_probe_env_keys")
missing_probe_forward = [key for key in probe_env_keys if key not in forward_env_keys]
if missing_probe_forward:
    raise SystemExit(
        "llama GPU env manifest does not forward all SPIR-V probe env keys: "
        + ",".join(missing_probe_forward)
    )
present_probe_env = [key for key in probe_env_keys if os.environ.get(key) is not None]
if present_probe_env and len(present_probe_env) != len(probe_env_keys):
    missing = [key for key in probe_env_keys if os.environ.get(key) is None]
    raise SystemExit(
        "partial SPIR-V probe env is unsafe; set all or none. missing: "
        + ",".join(missing)
    )

def env_key(item):
    return item.split("=", 1)[0]

def set_env(env, item):
    key = env_key(item)
    for idx, existing in enumerate(env):
        if env_key(existing) == key:
            env[idx] = item
            return
    env.append(item)

def render_template(value):
    return str(value).format(
        workspace_host=workspace_host,
        gpu_layers=gpu_layers,
    )

def manifest_env_item(item):
    if not isinstance(item, dict) or not isinstance(item.get("env"), str) or not item["env"]:
        raise SystemExit(f"invalid compare_mode_env_profiles entry in llama GPU env manifest: {manifest_path}")
    key = item["env"]
    if item.get("host_override") is True and key in os.environ:
        value = os.environ[key]
    elif isinstance(item.get("default_template"), str):
        value = render_template(item["default_template"])
    elif isinstance(item.get("default"), str):
        value = item["default"]
    else:
        raise SystemExit(f"missing default for {key} in llama GPU env manifest: {manifest_path}")
    return f"{key}={value}"

def apply_manifest_mode_env(env, mode, trace_alloc):
    profile = mode_profiles.get(mode)
    if profile is None:
        return
    if not isinstance(profile, dict):
        raise SystemExit(f"invalid compare mode profile for {mode}: {manifest_path}")
    entries = profile.get("env") or []
    if not isinstance(entries, list):
        raise SystemExit(f"invalid env list for compare mode {mode}: {manifest_path}")
    for item in entries:
        set_env(env, manifest_env_item(item))
    trace_entries = profile.get("trace_alloc_env") or []
    if trace_alloc == "1":
        if not isinstance(trace_entries, list):
            raise SystemExit(f"invalid trace_alloc_env for compare mode {mode}: {manifest_path}")
        for item in trace_entries:
            set_env(env, manifest_env_item(item))

env = [
    "PDOCKER_GPU=auto",
    "PDOCKER_GPU_AUTO=1",
    f"PDOCKER_GPU_MODE={mode}",
    f"LLAMA_ARG_MODEL={model_path}",
    f"LLAMA_ARG_CTX={ctx}",
    f"LLAMA_ARG_PORT={port}",
    "LLAMA_LOG_FILE=/workspace/logs/llama-server.log",
    f"PDOCKER_GPU_EXECUTOR_EXPECTED_MARKER={os.environ.get('PDOCKER_GPU_EXECUTOR_EXPECTED_MARKER', 'gpu-executor-llama-q4k-callsite-20260520')}",
    f"PDOCKER_VULKAN_ICD_EXPECTED_MARKER={os.environ.get('PDOCKER_VULKAN_ICD_EXPECTED_MARKER', 'vulkan-icd-feature-chain-marker-20260518')}",
]
if model_url:
    set_env(env, f"LLAMA_MODEL_URL={model_url}")
apply_manifest_mode_env(env, mode, trace_alloc)
for key in forward_env_keys:
    value = os.environ.get(key)
    if value is not None:
        set_env(env, f"{key}={value}")
port_key = f"{port}/tcp"
payload = {
    "Image": image,
    "Env": env,
    "ExposedPorts": {port_key: {}},
    "Labels": {
        "io.pdocker.project": "llama-cpp-gpu",
        "io.pdocker.role": "llama-gpu-compare",
        "io.github.ryo100794.pdocker.project-id": project,
        "io.github.ryo100794.pdocker.project-dir": project,
        "io.github.ryo100794.pdocker.project-name": "llama-cpp-gpu",
        "io.github.ryo100794.pdocker.compose-service": "llama-cpp",
        "com.docker.compose.project": "llama-cpp-gpu",
        "com.docker.compose.service": "llama-cpp",
        "com.docker.compose.oneoff": "False",
        "io.github.ryo100794.pdocker.service-url.18081": "llama.cpp",
    },
    "HostConfig": {
        "Binds": [
            f"{model_host}:/models",
            f"{workspace_host}:/workspace",
            f"{project}/profiles:/profiles",
        ],
        "PortBindings": {
            port_key: [{"HostIp": "127.0.0.1", "HostPort": str(port)}],
        },
        "DeviceRequests": [
            {
                "Driver": "",
                "Count": -1,
                "DeviceIDs": None,
                "Capabilities": [["gpu"]],
                "Options": {},
            },
        ],
    },
}
print(json.dumps(payload, separators=(",", ":")))
PY
}

wait_server() {
  local seconds="$1"
  local phase="${2:-wait-server}"
  local started now elapsed next_progress http_status container_status ref curl_exit curl_http_code http_probe port_forward_state wait_failure_class
  started="$(date +%s)"
  next_progress=0
  if same_device_http_enabled; then
    port_forward_state="same-device-direct"
  else
    "$ADB" forward --remove "tcp:$LOCAL_PORT" >/dev/null 2>&1 || true
    port_forward_state="ok"
    if ! "$ADB" forward "tcp:$LOCAL_PORT" "tcp:$REMOTE_PORT" >/dev/null 2>&1; then
      port_forward_state="failed"
      ref="$(container_ref)"
      if ! adb_transport_ok; then
        record_wait_server_event "$phase" 0 "$seconds" "fail" "not-checked" "$ref" "" "" "adb-transport-lost" "$port_forward_state" "adb-transport-lost"
      else
        record_wait_server_event "$phase" 0 "$seconds" "fail" "not-checked" "$ref" "" "" "curl-error" "$port_forward_state" "port-forward-failed"
      fi
      return 1
    fi
  fi
  curl_exit=""
  curl_http_code=""
  http_probe=""
  wait_failure_class=""
  while true; do
    now="$(date +%s)"
    elapsed=$(( now - started ))
    if (( elapsed >= seconds )); then
      ref="$(container_ref)"
      echo "[pdocker llama compare] waiting for $phase server timed out: elapsed=${elapsed}/${seconds}s id=${ref:0:12}" >&2
      operation_notify "running" "$phase: llama HTTP server wait timed out at ${elapsed}/${seconds}s; collecting logs"
      if container_running; then
        container_status="running"
        wait_failure_class="${wait_failure_class:-timeout}"
      else
        container_status="not-running"
        wait_failure_class="container-not-running"
      fi
      record_wait_server_event "$phase" "$elapsed" "$seconds" "timeout" "$container_status" "$ref" "$curl_exit" "$curl_http_code" "$http_probe" "$port_forward_state" "$wait_failure_class"
      return 1
    fi
    if ! same_device_http_enabled && ! adb_transport_ok; then
      ref="$(container_ref)"
      echo "[pdocker llama compare] adb transport lost while waiting for $phase server: elapsed=${elapsed}/${seconds}s state=$(adb_transport_state)" >&2
      record_wait_server_event "$phase" "$elapsed" "$seconds" "fail" "not-checked" "$ref" "$curl_exit" "$curl_http_code" "adb-transport-lost" "$port_forward_state" "adb-transport-lost"
      return 111
    fi
    if ! same_device_http_enabled; then
      runtime_memory_headroom_ok "wait-server" || return 2
    fi
    http_status="fail"
    container_status="not-checked"
    curl_http_code="$(
      curl -sS -o /dev/null -w '%{http_code}' \
        --max-time "$WAIT_SERVER_CURL_TIMEOUT_SEC" \
        "$(llama_base_url)/v1/models" 2>/dev/null
    )"
    curl_exit="$?"
    case "$curl_exit:$curl_http_code" in
      0:2*) http_probe="ok"; http_status="ok" ;;
      0:503) http_probe="http-503" ;;
      0:*) http_probe="http-error" ;;
      7:*) http_probe="connection-refused" ;;
      28:*) http_probe="curl-timeout" ;;
      *) http_probe="curl-error" ;;
    esac
    wait_failure_class="$http_probe"
    if [[ "$http_probe" == "ok" ]]; then
      http_status="ok"
      if same_device_http_enabled; then
        container_status="http-ready-direct"
        ref="$(container_ref)"
        echo "[pdocker llama compare] $phase server is reachable via same-device HTTP: elapsed=${elapsed}/${seconds}s id=${ref:0:12}" >&2
        operation_notify "running" "$phase: llama HTTP server reachable after ${elapsed}s"
        record_wait_server_event "$phase" "$elapsed" "$seconds" "$http_status" "$container_status" "$ref" "$curl_exit" "$curl_http_code" "$http_probe" "$port_forward_state" "ready"
        return 0
      fi
      if container_running; then
        container_status="running"
        ref="$(container_ref)"
        echo "[pdocker llama compare] $phase server is reachable: elapsed=${elapsed}/${seconds}s id=${ref:0:12}" >&2
        operation_notify "running" "$phase: llama HTTP server reachable after ${elapsed}s"
        record_wait_server_event "$phase" "$elapsed" "$seconds" "$http_status" "$container_status" "$ref" "$curl_exit" "$curl_http_code" "$http_probe" "$port_forward_state" "ready"
        return 0
      fi
      container_status="not-running"
      wait_failure_class="container-not-running"
    fi
    if (( elapsed >= next_progress )); then
      ref="$(container_ref)"
      if [[ "$container_status" == "not-checked" ]]; then
        if container_running; then
          container_status="running"
        else
          container_status="not-running"
          wait_failure_class="container-not-running"
        fi
      fi
      echo "[pdocker llama compare] waiting for $phase server: elapsed=${elapsed}/${seconds}s http=$http_status container=$container_status id=${ref:0:12}" >&2
      operation_notify "running" "$phase: waiting for llama HTTP server ${elapsed}/${seconds}s; http=$http_status; container=$container_status"
      record_wait_server_event "$phase" "$elapsed" "$seconds" "$http_status" "$container_status" "$ref" "$curl_exit" "$curl_http_code" "$http_probe" "$port_forward_state" "$wait_failure_class"
      next_progress=$(( elapsed + WAIT_SERVER_PROGRESS_INTERVAL_SEC ))
    fi
    sleep 1
  done
}

container_ref() {
  printf "%s" "${CURRENT_CONTAINER_ID:-$CONTAINER}"
}

container_logs() {
  local ref raw emitted
  ref="$(container_ref)"
  emitted=0
  raw="$(engine_request GET "/containers/$(urlencode "$ref")/logs?stdout=1&stderr=1&tail=$LOG_TAIL_LINES" | decode_engine_logs || true)"
  if [[ -n "$raw" ]]; then
    printf "%s\n" "--- pdocker engine log: ${ref:0:12} ---"
    printf "%s\n" "$raw"
    emitted=1
  fi
  # Fallback for failure paths where the Engine socket is busy or the server
  # has already disconnected the HTTP client.  Container logs and the llama
  # workspace log are app-owned files; reading them directly through run-as
  # keeps the compare artifact useful even when /containers/{id}/logs cannot
  # answer at the exact crash boundary.
  if run_as "cd files/pdocker 2>/dev/null || exit 0
ref=$(remote_quote "$ref")
tail_lines=$(remote_quote "$LOG_TAIL_LINES")
workspace=$(remote_quote "${DEVICE_WORKSPACE_HOST:-}")
emitted=0
emit_tail() {
  p=\"\$1\"
  label=\"\$2\"
  if test -f \"\$p\"; then
    echo \"--- \$label: \$p ---\"
    tail -n \"\$tail_lines\" \"\$p\" 2>/dev/null || cat \"\$p\" 2>/dev/null || true
    emitted=1
  fi
}
if test -n \"\$ref\"; then
  emit_tail \"logs/\$ref.log\" \"pdocker direct log fallback\"
  for d in containers/\$ref*; do
    test -d \"\$d\" || continue
    emit_tail \"\$d/logs/stdout.log\" \"container stdout fallback\"
    emit_tail \"\$d/logs/stderr.log\" \"container stderr fallback\"
    emit_tail \"\$d/rootfs/workspace/logs/llama-server.log\" \"container rootfs llama log fallback\"
  done
fi
if test -n \"\$workspace\"; then
  emit_tail \"\$workspace/logs/llama-server.log\" \"llama workspace log fallback\"
fi
for p in workspaces/*/logs/llama-server.log; do
  test -f \"\$p\" || continue
  emit_tail \"\$p\" \"llama workspace scan fallback\"
done
if test \"\$emitted\" = 0; then
  echo \"--- pdocker log fallback: no app-owned container/workspace log found ---\"
fi" || true; then
    emitted=1
  fi
  if [[ "$emitted" -eq 0 ]]; then
    printf "%s\n" "--- pdocker log fallback: no app-owned container/workspace log found ---"
  fi
}

container_archive_file() {
  local ctr_path="$1"
  local out="$2"
  local ref tmp_tar
  ref="$(container_ref)"
  tmp_tar="$out.tar"
  rm -f "$out" "$tmp_tar"
  if engine_body GET "/containers/$(urlencode "$ref")/archive?path=$(urlencode "$ctr_path")" > "$tmp_tar"; then
    python3 - "$tmp_tar" "$out" <<'PY' || true
import io
import os
import tarfile
import sys
from pathlib import Path

raw_path = Path(sys.argv[1])
out_path = Path(sys.argv[2])
raw = raw_path.read_bytes()
if not raw:
    raise SystemExit(0)
try:
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as tar:
        members = [m for m in tar.getmembers() if m.isfile()]
        if not members:
            raise SystemExit(0)
        src = tar.extractfile(members[0])
        if src is None:
            raise SystemExit(0)
        tmp_out = out_path.with_suffix(out_path.suffix + ".tmp")
        tmp_out.write_bytes(src.read())
        os.replace(tmp_out, out_path)
except (EOFError, OSError, tarfile.TarError):
    # The Engine may return a JSON error body when the file was never created
    # (for example when llama exits before startup diagnostics are written).
    # Keep artifact collection best-effort and do not pollute device logs with
    # Python tracebacks that hide the real GPU blocker.
    raise SystemExit(0)
PY
  fi
  rm -f "$tmp_tar"
}

container_state() {
  local ref body
  ref="$(container_ref)"
  body="$(engine_body GET "/containers/$(urlencode "$ref")/json" 2>/dev/null || true)"
  if printf "%s" "$body" | engine_body_has_id >/dev/null 2>&1; then
    printf "%s\n" "$body"
    return 0
  fi
  run_as "python3 - $(remote_quote "$ref") <<'PY'
import json
import os
import sys
from pathlib import Path

ref = (sys.argv[1] or '').lstrip('/')
root = Path('files/pdocker')

def state_matches(state: dict, path: Path) -> bool:
    cid = str(state.get('Id') or state.get('ID') or path.parent.name)
    name = str(state.get('Name') or '').lstrip('/')
    return bool(ref and (cid.startswith(ref) or name == ref))

for state_path in (root / 'containers').glob('*/state.json'):
    try:
        state = json.loads(state_path.read_text(encoding='utf-8'))
    except Exception:
        continue
    if not state_matches(state, state_path):
        continue
    cid = str(state.get('Id') or state_path.parent.name)
    out = dict(state)
    out.setdefault('Id', cid)
    out.setdefault('LogPath', str(root / 'logs' / f'{cid}.log'))
    out['PdockerInspectFallback'] = True
    out['PdockerInspectFallbackReason'] = 'engine-inspect-unavailable'
    print(json.dumps(out, ensure_ascii=False, sort_keys=True))
    raise SystemExit(0)
print(json.dumps({
    'Id': ref,
    'PdockerInspectFallback': True,
    'PdockerInspectFallbackReason': 'container-state-not-found',
}, ensure_ascii=False, sort_keys=True))
PY" || true
}

container_running() {
  local ref state_json
  ref="$(container_ref)"
  [[ -n "$ref" ]] || return 1
  state_json="$(engine_body GET "/containers/$(urlencode "$ref")/json" 2>/dev/null || true)"
  [[ -n "$state_json" ]] || return 1
  python3 - "$state_json" "$CURRENT_CONTAINER_ID" <<'PY'
import json
import sys

try:
    data = json.loads(sys.argv[1])
except Exception:
    raise SystemExit(1)
expected = sys.argv[2].strip()
if expected and data.get("Id") != expected:
    raise SystemExit(1)
state = data.get("State") or {}
raise SystemExit(0 if state.get("Running") is True else 1)
PY
}

remove_container() {
  engine_request_with_host_timeout "$ENGINE_CLEANUP_TIMEOUT_SEC" DELETE "/containers/$(urlencode "$CONTAINER")?force=true" >/dev/null || true
  CURRENT_CONTAINER_ID=""
}

inspect_container_body() {
  local ref="$1"
  engine_request_with_host_timeout "$ENGINE_START_TIMEOUT_SEC" GET "/containers/$(urlencode "$ref")/json" | http_body || true
}

poll_container_after_create_timeout() {
  local mode="$1"
  local deadline now body start elapsed=0
  start="$(date +%s)"
  deadline=$(( start + ENGINE_CREATE_SETTLE_TIMEOUT_SEC ))
  while :; do
    body="$(inspect_container_body "$CONTAINER")"
    if [[ -n "$body" ]] && printf "%s" "$body" | engine_body_has_id; then
      echo "[pdocker llama compare] $mode: delayed create became inspectable after ${elapsed}s" >&2
      printf "%s" "$body"
      return 0
    fi
    now="$(date +%s)"
    if (( now >= deadline )); then
      break
    fi
    elapsed=$(( now - start ))
    echo "[pdocker llama compare] $mode: waiting for delayed create visibility (${elapsed}/${ENGINE_CREATE_SETTLE_TIMEOUT_SEC}s)" >&2
    sleep "$ENGINE_CREATE_POLL_INTERVAL_SEC"
  done
  return 1
}

wait_container_absent() {
  local deadline now body start elapsed=0
  start="$(date +%s)"
  deadline=$(( start + ENGINE_CLEANUP_TIMEOUT_SEC ))
  while :; do
    body="$(inspect_container_body "$CONTAINER")"
    if [[ -z "$body" ]] || ! printf "%s" "$body" | engine_body_has_id; then
      return 0
    fi
    now="$(date +%s)"
    if (( now >= deadline )); then
      echo "[pdocker llama compare] stale target container still inspectable after ${ENGINE_CLEANUP_TIMEOUT_SEC}s" >&2
      return 1
    fi
    elapsed=$(( now - start ))
    echo "[pdocker llama compare] waiting for stale target removal (${elapsed}/${ENGINE_CLEANUP_TIMEOUT_SEC}s)" >&2
    sleep "$ENGINE_CREATE_POLL_INTERVAL_SEC"
  done
}

remove_container_after_failure() {
  local deadline now body cid start
  remove_container
  start="$(date +%s)"
  deadline=$(( start + ENGINE_CREATE_SETTLE_TIMEOUT_SEC ))
  while :; do
    body="$(inspect_container_body "$CONTAINER")"
    if [[ -n "$body" ]] && printf "%s" "$body" | engine_body_has_id; then
      cid="$(printf "%s" "$body" | parse_engine_id 2>/dev/null || true)"
      if [[ -n "$cid" ]]; then
        engine_request_with_host_timeout "$ENGINE_CLEANUP_TIMEOUT_SEC" DELETE "/containers/$cid?force=true" >/dev/null || true
      else
        remove_container
      fi
      return 0
    fi
    now="$(date +%s)"
    if (( now >= deadline )); then
      break
    fi
    sleep "$ENGINE_CREATE_POLL_INTERVAL_SEC"
  done
  return 0
}

start_container_mode() {
  local mode="$1"
  local ctx="$2"
  local gpu_layers="${3:-}"
  local payload create_body cid
  echo "[pdocker llama compare] $mode: waiting for engine" >&2
  wait_for_engine
  echo "[pdocker llama compare] $mode: checking memory headroom" >&2
  ensure_memory_headroom "starting $mode container"
  echo "[pdocker llama compare] $mode: removing stale target container" >&2
  remove_container
  wait_container_absent || return 124
  payload="$(container_payload "$mode" "$ctx" "$gpu_layers")"
  record_planned_container_payload_env "$mode" "$payload"
  echo "[pdocker llama compare] $mode: creating container" >&2
  if ! create_body="$(engine_request_with_host_timeout "$ENGINE_CREATE_TIMEOUT_SEC" POST "/containers/create?name=$(urlencode "$CONTAINER")" "$payload" | http_body)"; then
    echo "[pdocker llama compare] $mode: create request did not return within ${ENGINE_CREATE_TIMEOUT_SEC}s; probing named container" >&2
    operation_notify "running" "$mode: container create timed out; probing named container"
    create_body="$(poll_container_after_create_timeout "$mode" || true)"
    if [[ -z "$create_body" ]] || ! printf "%s" "$create_body" | engine_body_has_id; then
      echo "[pdocker llama compare] $mode: create timeout left no inspectable named container" >&2
      return 124
    fi
  fi
  cid="$(printf "%s" "$create_body" | parse_engine_id)"
  CURRENT_CONTAINER_ID="$cid"
  echo "[pdocker llama compare] $mode: starting container ${cid:0:12}" >&2
  if ! engine_request_with_host_timeout "$ENGINE_START_TIMEOUT_SEC" POST "/containers/$cid/start" "" >/dev/null; then
    echo "[pdocker llama compare] Engine start did not return within ${ENGINE_START_TIMEOUT_SEC}s for $cid; continuing with runtime watchdog" >&2
    runtime_memory_headroom_ok "engine-start-timeout" || return 1
  fi
  echo "[pdocker llama compare] $mode: start request returned for ${cid:0:12}" >&2
  printf "%s\n" "$cid"
}

start_cpu() {
  start_container_mode "cpu" "$CPU_CTX"
}

start_gpu() {
  start_container_mode "vulkan-raw" "$GPU_CTX" "$GPU_LAYERS"
}

bench_http() {
  local mode="$1"
  local out="$2"
  PDOCKER_LLAMA_LOCAL_PORT="$LOCAL_PORT" \
    PDOCKER_LLAMA_REMOTE_PORT="$REMOTE_PORT" \
    PDOCKER_LLAMA_BENCH_PREDICT="$PREDICT" \
    PDOCKER_LLAMA_BENCH_REPEAT="$REPEAT" \
    PDOCKER_LLAMA_BENCH_WARMUP_DISCARD="$WARMUP_DISCARD" \
    PDOCKER_LLAMA_BENCH_MODE="$mode" \
    PDOCKER_LLAMA_BENCH_OUT="$out" \
    "$ROOT/scripts/android-llama-bench.sh"
}

probe_http_correctness() {
  local mode="$1"
  local out="$2"
  local base_url
  base_url="$(llama_base_url)"
  if ! same_device_http_enabled; then
    "$ADB" forward --remove "tcp:$LOCAL_PORT" >/dev/null 2>&1 || true
    "$ADB" forward "tcp:$LOCAL_PORT" "tcp:$REMOTE_PORT" >/dev/null
  fi
  python3 - "$base_url" "$mode" "$GPU_LAYERS" "$MODEL_PATH" "$out" "$CORRECTNESS_TIMEOUT_SEC" <<'PY'
import json
import os
import sys
import time
import urllib.request

base_url, mode, gpu_layers, model_path, out_path, timeout_s = sys.argv[1:7]
timeout_sec = max(1, int(timeout_s))
n_probs = max(0, int(os.environ.get("PDOCKER_LLAMA_N_PROBS", "10") or "0"))

def post_json(path, body, timeout):
    data = json.dumps(body, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(
        base_url + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.monotonic()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8", "replace"))
        return resp.status, round((time.monotonic() - started) * 1000.0, 3), payload

probes = [
    {"name": "addition", "prompt": "2+3=", "expected": ["5"], "required": True},
    {"name": "multiplication_prefix", "prompt": "12*7=", "expected": ["84", "8"], "required": False},
    {"name": "identity_text", "prompt": "Repeat exactly: pdocker-ok", "expected": ["pdocker-ok", " pdocker-ok"], "required": False},
]
results = []
for probe in probes:
    item = dict(probe)
    item.update({"passed": False, "content": "", "error": None, "duration_ms": None, "status_code": None})
    print(
        f"[pdocker llama compare] correctness probe {mode}/{probe['name']} "
        f"timeout={timeout_sec}s",
        file=sys.stderr,
        flush=True,
    )
    try:
        status, duration_ms, payload = post_json(
            "/completion",
            {
                "prompt": probe["prompt"],
                "n_predict": 4 if probe["name"] == "identity_text" else 1,
                "temperature": 0,
                "top_k": 1,
                "top_p": 1,
                "cache_prompt": False,
                "stream": False,
                "n_probs": n_probs,
                "completion_probabilities": n_probs > 0,
                "stop": ["\n"],
            },
            timeout=timeout_sec,
        )
        content = str(payload.get("content", ""))
        normalized = content.lstrip()
        probabilities = payload.get("completion_probabilities") or []
        if probabilities and isinstance(probabilities, list) and isinstance(probabilities[0], dict):
            first = probabilities[0]
            top_logprobs = []
            for entry in first.get("top_logprobs") or []:
                if not isinstance(entry, dict):
                    continue
                top_logprobs.append({
                    "id": entry.get("id"),
                    "token": str(entry.get("token", ""))[:96],
                    "logprob": entry.get("logprob"),
                })
            item["selected_token"] = {
                "id": first.get("id"),
                "token": str(first.get("token", ""))[:96],
                "logprob": first.get("logprob"),
            }
            item["top_logprobs"] = top_logprobs[:n_probs]
        item.update({
            "status_code": status,
            "duration_ms": duration_ms,
            "content": content[:256],
            "passed": any(normalized.startswith(prefix) for prefix in probe["expected"]),
        })
    except Exception as exc:
        item["error"] = f"{type(exc).__name__}: {exc}"
        print(
            f"[pdocker llama compare] correctness probe {mode}/{probe['name']} failed: {item['error']}",
            file=sys.stderr,
            flush=True,
        )
    else:
        print(
            f"[pdocker llama compare] correctness probe {mode}/{probe['name']} "
            f"passed={item['passed']} duration_ms={item['duration_ms']}",
            file=sys.stderr,
            flush=True,
        )
    results.append(item)

required_failures = [p for p in results if p["required"] and not p["passed"]]
optional_failures = [p for p in results if not p["required"] and not p["passed"]]
summary = {
    "correctness": "fail" if required_failures else "pass",
    "required_failures": len(required_failures),
    "optional_failures": len(optional_failures),
    "benchmark_claim_allowed": not required_failures,
}

report = {
    "schema": "pdocker.llama.correctness.v1.compare",
    "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "endpoint": base_url,
    "mode": mode,
    "gpu_layers": int(gpu_layers),
    "model_path": model_path,
    "n_probs": n_probs,
    "probes": results,
    "summary": summary,
}
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(report, f, indent=2, ensure_ascii=False)
    f.write("\n")
print(json.dumps(summary, indent=2))
PY
}

probe_service_readiness() {
  local mode="$1"
  local out="$2"
  local base_url
  base_url="$(llama_base_url)"
  if ! same_device_http_enabled; then
    "$ADB" forward --remove "tcp:$LOCAL_PORT" >/dev/null 2>&1 || true
    "$ADB" forward "tcp:$LOCAL_PORT" "tcp:$REMOTE_PORT" >/dev/null
  fi
  python3 - "$base_url" "$mode" "$GPU_LAYERS" "$MODEL_PATH" "$out" "$COMPLETION_READY_TIMEOUT_SEC" <<'PY'
import json
import sys
import time
import urllib.request

base_url, mode, gpu_layers, model_path, out_path, timeout_s = sys.argv[1:7]
timeout_sec = max(1, int(timeout_s))


def get_json(path: str, timeout: int) -> dict:
    started = time.monotonic()
    item = {
        "ok": False,
        "status": "fail",
        "status_code": None,
        "duration_ms": None,
        "error": None,
        "path": path,
    }
    try:
        with urllib.request.urlopen(base_url + path, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
            ok = 200 <= int(resp.status) < 300
            item.update({
                "status_code": resp.status,
                "duration_ms": round((time.monotonic() - started) * 1000.0, 3),
                "ok": ok,
                "status": "pass" if ok else "fail",
            })
            try:
                item["json"] = json.loads(body)
            except Exception:
                item["body_excerpt"] = body[:512]
    except Exception as exc:
        item.update({
            "duration_ms": round((time.monotonic() - started) * 1000.0, 3),
            "error": f"{type(exc).__name__}: {exc}",
        })
    return item


def post_json(path: str, body: dict, timeout: int) -> dict:
    data = json.dumps(body, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(
        base_url + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.monotonic()
    item = {
        "ok": False,
        "status": "fail",
        "status_code": None,
        "duration_ms": None,
        "error": None,
        "path": path,
        "timeout_sec": timeout,
        "prompt": str(body.get("prompt", "")),
        "expected": ["5"],
        "n_predict": body.get("n_predict"),
        "deterministic": (
            body.get("temperature") == 0
            and body.get("top_k") == 1
            and body.get("top_p") == 1
            and body.get("stream") is False
        ),
        "content": "",
        "passed": False,
    }
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read().decode("utf-8", "replace")
            ok = 200 <= int(resp.status) < 300
            item.update({
                "status_code": resp.status,
                "duration_ms": round((time.monotonic() - started) * 1000.0, 3),
                "ok": ok,
                "status": "pass" if ok else "fail",
            })
            try:
                parsed = json.loads(payload)
                content = str(parsed.get("content", "")) if isinstance(parsed, dict) else ""
                item["content"] = content[:256]
                item["content_excerpt"] = content[:128]
                item["passed"] = any(content.lstrip().startswith(prefix) for prefix in item["expected"])
            except Exception:
                item["body_excerpt"] = payload[:512]
    except Exception as exc:
        item.update({
            "duration_ms": round((time.monotonic() - started) * 1000.0, 3),
            "error": f"{type(exc).__name__}: {exc}",
        })
    return item


completion_body = {
    "prompt": "2+3=",
    "n_predict": 1,
    "temperature": 0,
    "top_k": 1,
    "top_p": 1,
    "cache_prompt": False,
    "stream": False,
    "stop": ["\n"],
}
report = {
    "schema": "pdocker.llama.service-readiness.v1",
    "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "endpoint": base_url,
    "mode": mode,
    "gpu_layers": int(gpu_layers),
    "model_path": model_path,
    "completion_timeout_sec": timeout_sec,
    "health": get_json("/health", min(timeout_sec, 10)),
    "models": get_json("/v1/models", min(timeout_sec, 10)),
    "completion": post_json("/completion", completion_body, timeout_sec),
}
if not report["completion"]["ok"]:
    report["post_completion_health"] = get_json("/health", min(timeout_sec, 10))
report["summary"] = {
    "health": "pass" if report["health"]["ok"] else "fail",
    "models": "pass" if report["models"]["ok"] else "fail",
    "liveness": "pass" if report["health"]["ok"] and report["models"]["ok"] else "fail",
    "completion": "pass" if report["completion"]["ok"] else "fail",
    "prompt_sanity": "pass" if report["completion"].get("passed") is True else "fail",
    "server_alive_after_completion": (
        "pass"
        if report.get("post_completion_health", {}).get("ok")
        else "fail"
        if "post_completion_health" in report
        else "not-checked"
    ),
    "ready": bool(
        report["health"]["ok"]
        and report["models"]["ok"]
        and report["completion"]["ok"]
        and report["completion"].get("passed") is True
    ),
}
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(report, f, indent=2, ensure_ascii=False)
    f.write("\n")
print(json.dumps(report["summary"], indent=2))
raise SystemExit(0 if report["summary"]["ready"] else 1)
PY
}

start_adb_keepalive

CURRENT_STAGE="runtime env record"
record_manifest_runtime_env "$RUNTIME_ENV_RECORD_JSON"
CURRENT_STAGE="stale target cleanup"
stop_stale_target_if_engine_alive
CURRENT_STAGE="memory preflight"
wait_for_memory_headroom "preflight before daemon start"
CURRENT_STAGE="app daemon startup"
restart_app_daemon_for_test
CURRENT_STAGE="pdockerd startup"
wait_for_engine
CURRENT_STAGE="project path resolution"
DEVICE_PROJECT="$(run_as "cd $(remote_quote "$PROJECT") && pwd" | tr -d '\r')"
DEVICE_MODEL_HOST="$(run_as "cd $(remote_quote "$PROJECT") && . ./.env >/dev/null 2>&1 && printf '%s' \"\${PDOCKER_MODEL_HOST:-$DEVICE_PROJECT/models}\"" | tr -d '\r')"
DEVICE_WORKSPACE_HOST="$(run_as "cd $(remote_quote "$PROJECT") && . ./.env >/dev/null 2>&1 && printf '%s' \"\${PDOCKER_FAST_WORKSPACE_HOST:-$DEVICE_PROJECT/workspace}\"" | tr -d '\r')"
CURRENT_STAGE="SPIR-V probe staging"
stage_spirv_probe_artifacts_for_container
CPU_JSON="$TMP/cpu.json"
CPU_CORRECTNESS_JSON="$TMP/cpu-correctness.json"
if [[ "$RUN_CPU" -eq 1 ]]; then
  echo "[pdocker llama compare] start CPU baseline"
  CURRENT_STAGE="CPU baseline"
  operation_notify "running" "CPU baseline: starting"
  start_cpu >/dev/null
  if ! wait_server "$CPU_WAIT_SERVER_TIMEOUT_SEC" "CPU baseline"; then
    operation_notify "failed" "CPU server did not become reachable" 1
    echo "CPU server did not become reachable" >&2
    container_state >&2
    container_logs >&2
    exit 1
  fi
  bench_http "cpu-baseline" "$CPU_JSON" >/dev/null
  if [[ "$CORRECTNESS" != "0" ]]; then
    operation_notify "running" "CPU baseline served; checking differential correctness baseline"
    probe_http_correctness "cpu-baseline" "$CPU_CORRECTNESS_JSON" >/dev/null || true
  fi
else
  echo "[pdocker llama compare] reuse CPU baseline"
  CURRENT_STAGE="reuse CPU baseline"
  operation_notify "running" "Reusing CPU baseline; forced Vulkan model load starting"
  python3 - "$OUT" "$CPU_TPS_OVERRIDE" >"$CPU_JSON" <<'PY'
import json
import sys
from pathlib import Path

out_path = Path(sys.argv[1])
override = sys.argv[2].strip()
cpu_tps = float(override) if override else 0.0
summary = {}
if not cpu_tps and out_path.is_file():
    previous = json.loads(out_path.read_text(encoding="utf-8"))
    cpu = previous.get("cpu", {})
    cpu_tps = float(cpu.get("tokens_per_second") or 0.0)
    summary = dict(cpu.get("summary", {}))
if not cpu_tps:
    raise SystemExit("no reusable CPU baseline; run without --gpu-only or pass --cpu-tps")
summary.setdefault("predicted_tokens_per_second_mean", cpu_tps)
summary.setdefault("predicted_tokens_per_second_min", cpu_tps)
summary.setdefault("predicted_tokens_per_second_max", cpu_tps)
print(json.dumps({
    "summary": summary,
    "reused_cpu_baseline": True,
    "source": "override" if override else str(out_path),
}, separators=(",", ":")))
PY
fi

echo "[pdocker llama compare] start forced Vulkan run"
CURRENT_STAGE="forced Vulkan"
operation_notify "running" "Forced Vulkan model load starting"
start_gpu >/dev/null
GPU_LOG="$TMP/gpu.log"
GPU_STATE="$TMP/gpu-state.txt"
GPU_JSON="$TMP/gpu.json"
GPU_POST_READINESS_MEMORY_JSON="$TMP/gpu-post-readiness-memory.json"
CORRECTNESS_JSON="$TMP/correctness.json"
SERVICE_READINESS_JSON="$TMP/service-readiness.json"
STARTUP_JSON="$TMP/llama-startup.json"
if wait_server "$FORCED_VULKAN_WAIT_SERVER_TIMEOUT_SEC" "Forced Vulkan"; then
  operation_notify "running" "Forced Vulkan liveness passed; checking completion readiness"
  if probe_service_readiness "vulkan-forced-ngl-$GPU_LAYERS" "$SERVICE_READINESS_JSON" >/dev/null; then
    operation_notify "running" "Forced Vulkan completion ready; recording HTTP benchmark"
    bench_http "vulkan-forced-ngl-$GPU_LAYERS" "$GPU_JSON" >/dev/null || true
    if [[ "$CORRECTNESS" != "0" ]]; then
      operation_notify "running" "Forced Vulkan served; checking arithmetic correctness"
      probe_http_correctness "vulkan-forced-ngl-$GPU_LAYERS" "$CORRECTNESS_JSON" >/dev/null || true
    fi
  else
    operation_notify "running" "Forced Vulkan liveness passed but completion did not finish; collecting evidence"
  fi
  gpu_served=1
else
  operation_notify "running" "Forced Vulkan did not serve; collecting container logs"
  gpu_served=0
fi
memory_snapshot_json > "$GPU_POST_READINESS_MEMORY_JSON" || true
container_state > "$GPU_STATE"
container_archive_file "/workspace/logs/llama-startup.json" "$STARTUP_JSON"
container_logs > "$GPU_LOG"

PDOCKER_LLAMA_RUNTIME_ABORT_JSON="$RUNTIME_ABORT_JSON" \
PDOCKER_LLAMA_POST_READINESS_MEMORY_JSON="$GPU_POST_READINESS_MEMORY_JSON" \
python3 - "$CPU_JSON" "$CPU_CORRECTNESS_JSON" "$GPU_JSON" "$GPU_LOG" "$GPU_STATE" "$CORRECTNESS_JSON" "$SERVICE_READINESS_JSON" "$STARTUP_JSON" "$RUNTIME_ENV_RECORD_JSON" "$OUT" "$gpu_served" "$GPU_LAYERS" "$GPU_CTX" "$PREDICT" "$REPEAT" "$WARMUP_DISCARD" "$TRACE_ALLOC" "$MODEL_PATH" "$MODEL_URL" "$ROOT/scripts/llama-gpu-env-manifest.json" <<'PY'
import json
import hashlib
import math
import os
import re
import struct
import sys
import time
from pathlib import Path

cpu_path, cpu_correctness_path, gpu_path, gpu_log_path, gpu_state_path, correctness_path, service_readiness_path, startup_path, runtime_env_record_path, out_path, gpu_served_s, gpu_layers, gpu_ctx, predict, repeat, warmup_discard, trace_alloc, model_path, model_url, manifest_path = sys.argv[1:21]
env_manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
if env_manifest.get("schema") != "pdocker.llama.gpu.env-manifest.v1":
    raise SystemExit(f"unsupported llama GPU env manifest schema: {manifest_path}")
cpu = json.load(open(cpu_path, encoding="utf-8"))
gpu = {}
if Path(gpu_path).is_file() and Path(gpu_path).stat().st_size:
    try:
        gpu = json.load(open(gpu_path, encoding="utf-8"))
    except Exception:
        gpu = {}
log = Path(gpu_log_path).read_text(encoding="utf-8", errors="replace") if Path(gpu_log_path).is_file() else ""
state = Path(gpu_state_path).read_text(encoding="utf-8", errors="replace") if Path(gpu_state_path).is_file() else ""
correctness = {}
if Path(correctness_path).is_file() and Path(correctness_path).stat().st_size:
    try:
        correctness = json.load(open(correctness_path, encoding="utf-8"))
    except Exception:
        correctness = {}
service_readiness = {}
if Path(service_readiness_path).is_file() and Path(service_readiness_path).stat().st_size:
    try:
        service_readiness = json.load(open(service_readiness_path, encoding="utf-8"))
    except Exception:
        service_readiness = {}
startup_diagnostics = {}
if Path(startup_path).is_file() and Path(startup_path).stat().st_size:
    try:
        startup_diagnostics = json.load(open(startup_path, encoding="utf-8"))
    except Exception:
        startup_diagnostics = {}
runtime_env_record = {}
if Path(runtime_env_record_path).is_file() and Path(runtime_env_record_path).stat().st_size:
    try:
        runtime_env_record = json.load(open(runtime_env_record_path, encoding="utf-8"))
    except Exception:
        runtime_env_record = {}
runtime_abort = {}
runtime_abort_path = os.environ.get("PDOCKER_LLAMA_RUNTIME_ABORT_JSON", "")
if runtime_abort_path and Path(runtime_abort_path).is_file() and Path(runtime_abort_path).stat().st_size:
    try:
        runtime_abort = json.load(open(runtime_abort_path, encoding="utf-8"))
    except Exception:
        runtime_abort = {}
post_readiness_memory = {}
post_readiness_memory_path = os.environ.get("PDOCKER_LLAMA_POST_READINESS_MEMORY_JSON", "")
if post_readiness_memory_path and Path(post_readiness_memory_path).is_file() and Path(post_readiness_memory_path).stat().st_size:
    try:
        post_readiness_memory = json.load(open(post_readiness_memory_path, encoding="utf-8"))
    except Exception:
        post_readiness_memory = {}
cpu_correctness = {}
if Path(cpu_correctness_path).is_file() and Path(cpu_correctness_path).stat().st_size:
    try:
        cpu_correctness = json.load(open(cpu_correctness_path, encoding="utf-8"))
    except Exception:
        cpu_correctness = {}
cpu_tps = float(cpu.get("summary", {}).get("predicted_tokens_per_second_mean") or 0.0)
gpu_tps = float(gpu.get("summary", {}).get("predicted_tokens_per_second_mean") or 0.0)
target_tps = cpu_tps * 10.0

def parse_container_state(raw):
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}

def container_env_snapshot(state_obj):
    env_values = []
    config = state_obj.get("Config") if isinstance(state_obj.get("Config"), dict) else {}
    for key in ("Env",):
        value = state_obj.get(key)
        if isinstance(value, list):
            env_values.extend(value)
    value = config.get("Env")
    if isinstance(value, list):
        env_values.extend(value)
    prefixes = (
        "LLAMA_",
        "PDOCKER_GPU_",
        "PDOCKER_VULKAN_",
        "GGML_VK_",
        "VK_ICD_FILENAMES",
        "VK_DRIVER_FILES",
        "OCL_ICD_VENDORS",
    )
    snapshot = {}
    for entry in env_values:
        if not isinstance(entry, str) or "=" not in entry:
            continue
        name, value = entry.split("=", 1)
        if any(name == prefix or name.startswith(prefix) for prefix in prefixes):
            snapshot[name] = value
    return dict(sorted(snapshot.items()))

state_obj = parse_container_state(state)
runtime_env = container_env_snapshot(state_obj)
startup_env = startup_diagnostics.get("env") if isinstance(startup_diagnostics.get("env"), dict) else {}
planned_env = runtime_env_record.get("planned_container_env") if isinstance(runtime_env_record, dict) else {}
if not isinstance(planned_env, dict):
    planned_env = {}
effective_runtime_env = dict(runtime_env)
effective_runtime_env.update({str(name): str(value) for name, value in planned_env.items()})
for name, value in startup_env.items():
    effective_runtime_env[str(name)] = str(value)
manifest_forward_env_keys = env_manifest.get("compare_forward_env_keys")
if not isinstance(manifest_forward_env_keys, list):
    manifest_forward_env_keys = []
manifest_probe_env_keys = env_manifest.get("compare_probe_env_keys")
if not isinstance(manifest_probe_env_keys, list):
    manifest_probe_env_keys = []
manifest_app_process_only_env_keys = (
    env_manifest.get("env_bridge_classifications", {}).get("app_process_only")
    if isinstance(env_manifest.get("env_bridge_classifications"), dict)
    else []
)
if not isinstance(manifest_app_process_only_env_keys, list):
    manifest_app_process_only_env_keys = []
manifest_requested_env = runtime_env_record.get("host_requested_env") if isinstance(runtime_env_record, dict) else {}
if not isinstance(manifest_requested_env, dict):
    manifest_requested_env = {}
runtime_forward_env_keys = [
    str(key) for key in manifest_forward_env_keys if isinstance(key, str)
]
runtime_app_process_only_env_keys = [
    str(key) for key in manifest_app_process_only_env_keys if isinstance(key, str)
]
runtime_env_manifest = {
    "schema": "pdocker.llama.gpu.runtime-env-artifact.v1",
    "record_schema": runtime_env_record.get("schema") if isinstance(runtime_env_record, dict) else None,
    "manifest_schema": env_manifest.get("schema"),
    "manifest_path": manifest_path,
    "compare_forward_env_keys": runtime_forward_env_keys,
    "compare_probe_env_keys": [str(key) for key in manifest_probe_env_keys if isinstance(key, str)],
    "app_process_only_env_keys": runtime_app_process_only_env_keys,
    "app_process_only_not_host_container_forwarded_keys": sorted(
        key for key in runtime_app_process_only_env_keys if key not in runtime_forward_env_keys
    ),
    "host_requested_env": {str(k): str(v) for k, v in sorted(manifest_requested_env.items())},
    "host_echo_recorded": bool(runtime_env_record.get("echoed_to_log")) if isinstance(runtime_env_record, dict) else False,
    "planned_container_mode": runtime_env_record.get("planned_container_mode") if isinstance(runtime_env_record, dict) else None,
    "planned_container_env_keys": sorted(str(key) for key in planned_env),
    "runtime_env_observed_keys": sorted(effective_runtime_env),
    "requested_env_observed_keys": sorted(
        key for key in manifest_requested_env if str(key) in effective_runtime_env
    ),
    "requested_env_missing_from_runtime": sorted(
        str(key) for key in manifest_requested_env if str(key) not in effective_runtime_env
    ),
}

def probe_map(report):
    return {
        str(item.get("name")): str(item.get("content", ""))
        for item in report.get("probes", [])
        if isinstance(item, dict)
    }

def probe_probability_map(report):
    mapped = {}
    for item in report.get("probes", []):
        if not isinstance(item, dict):
            continue
        top_logprobs = item.get("top_logprobs") or []
        if not isinstance(top_logprobs, list):
            top_logprobs = []
        selected = item.get("selected_token") or {}
        if not isinstance(selected, dict):
            selected = {}
        mapped[str(item.get("name"))] = {
            "selected_token": selected,
            "top_logprobs": [
                entry for entry in top_logprobs
                if isinstance(entry, dict)
            ],
        }
    return mapped

cpu_probe_outputs = probe_map(cpu_correctness)
gpu_probe_outputs = probe_map(correctness)
shared_probe_names = sorted(set(cpu_probe_outputs) & set(gpu_probe_outputs))
differential_probe_results = [
    {
        "name": name,
        "cpu_content": cpu_probe_outputs[name],
        "gpu_content": gpu_probe_outputs[name],
        "matched": cpu_probe_outputs[name] == gpu_probe_outputs[name],
    }
    for name in shared_probe_names
]
differential_correctness = {
    "enabled": bool(cpu_correctness and correctness),
    "shared_probe_count": len(shared_probe_names),
    "mismatch_count": sum(1 for item in differential_probe_results if not item["matched"]),
    "summary": (
        "pass"
        if differential_probe_results and all(item["matched"] for item in differential_probe_results)
        else "fail"
        if differential_probe_results
        else "not-run"
    ),
    "probes": differential_probe_results,
}

cpu_probe_probabilities = probe_probability_map(cpu_correctness)
gpu_probe_probabilities = probe_probability_map(correctness)
shared_probability_probe_names = sorted(set(cpu_probe_probabilities) & set(gpu_probe_probabilities))
differential_probability_results = []
for name in shared_probability_probe_names:
    cpu_prob = cpu_probe_probabilities[name]
    gpu_prob = gpu_probe_probabilities[name]
    cpu_top = cpu_prob.get("top_logprobs") or []
    gpu_top = gpu_prob.get("top_logprobs") or []
    cpu_top_ids = [entry.get("id") for entry in cpu_top]
    gpu_top_ids = [entry.get("id") for entry in gpu_top]
    cpu_selected = cpu_prob.get("selected_token") or {}
    gpu_selected = gpu_prob.get("selected_token") or {}
    cpu_selected_id = cpu_selected.get("id")
    gpu_selected_id = gpu_selected.get("id")
    differential_probability_results.append({
        "name": name,
        "cpu_selected_token": cpu_selected,
        "gpu_selected_token": gpu_selected,
        "top1_matched": bool(cpu_top_ids and gpu_top_ids and cpu_top_ids[0] == gpu_top_ids[0]),
        "selected_token_matched": cpu_selected_id is not None and cpu_selected_id == gpu_selected_id,
        "cpu_selected_rank_in_gpu_top": gpu_top_ids.index(cpu_selected_id) + 1 if cpu_selected_id in gpu_top_ids else None,
        "gpu_selected_rank_in_cpu_top": cpu_top_ids.index(gpu_selected_id) + 1 if gpu_selected_id in cpu_top_ids else None,
        "shared_top_token_ids": sorted(set(cpu_top_ids) & set(gpu_top_ids), key=lambda token_id: cpu_top_ids.index(token_id)),
        "cpu_top_logprobs": cpu_top,
        "gpu_top_logprobs": gpu_top,
    })
differential_probabilities = {
    "enabled": bool(cpu_probe_probabilities and gpu_probe_probabilities),
    "shared_probe_count": len(shared_probability_probe_names),
    "top1_mismatch_count": sum(1 for item in differential_probability_results if not item["top1_matched"]),
    "selected_token_mismatch_count": sum(1 for item in differential_probability_results if not item["selected_token_matched"]),
    "summary": (
        "pass"
        if differential_probability_results and all(item["top1_matched"] for item in differential_probability_results)
        else "fail"
        if differential_probability_results
        else "not-run"
    ),
    "probes": differential_probability_results,
}

def json_string_field_seen(name, value):
    return re.search(rf'"{re.escape(name)}"\s*:\s*"{re.escape(value)}"', log) is not None

def json_bool_field_seen(name, value):
    return re.search(rf'"{re.escape(name)}"\s*:\s*{str(value).lower()}\b', log) is not None

executor_backends = sorted(set(re.findall(r'"backend_impl"\s*:\s*"([^"]+)"', log)))
executor_errors = sorted(set(re.findall(r'"error"\s*:\s*"([^"]+)"', log)))
spirv_hashes = sorted(set(re.findall(r'"spirv_hash"\s*:\s*"([^"]+)"', log)))

def env_bool(name):
    value = os.environ.get(name)
    if value is None:
        return None
    return str(value).strip().lower() in {"1", "true", "yes", "on"}

def extract_executor_json_events(text):
    events = []
    markers = ("generic dispatch response:", "q6 compact response:")
    starts = []
    for marker in markers:
        search_from = 0
        while True:
            marker_pos = text.find(marker, search_from)
            if marker_pos < 0:
                break
            brace_pos = text.find("{", marker_pos + len(marker))
            if brace_pos < 0:
                break
            starts.append(brace_pos)
            search_from = brace_pos + 1
    offset = 0
    for line in text.splitlines(keepends=True):
        raw = line.strip()
        if raw.startswith("{"):
            starts.append(offset + line.find("{"))
        offset += len(line)
    seen_starts = set()
    seen_events = set()
    for start in starts:
        if start < 0 or start in seen_starts:
            continue
        seen_starts.add(start)
        depth = 0
        in_string = False
        escaped = False
        end = -1
        for pos in range(start, len(text)):
            ch = text[pos]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = pos + 1
                    break
        if end < 0:
            continue
        try:
            event = json.loads(text[start:end])
        except Exception:
            continue
        if event.get("executor") == "pdocker-gpu-executor":
            # The Android collector intentionally merges multiple durable log
            # locations (engine container logs plus workspace/server logs) so a
            # UI or daemon crash cannot erase evidence.  Those sources can
            # contain the exact same executor JSON line.  Deduplicate here, at
            # the evidence ingestion boundary, rather than weakening verifier
            # ambiguity checks for genuinely different duplicate dispatches.
            event_key = json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            if event_key in seen_events:
                continue
            seen_events.add(event_key)
            events.append(event)
    return events

def observed_event_values(events, field):
    values = []
    for event in events:
        if not isinstance(event, dict):
            continue
        current = event
        found = True
        for part in field.split("."):
            if isinstance(current, dict) and part in current:
                current = current.get(part)
            else:
                found = False
                break
        if found:
            values.append(current)
    return values

def extract_dispatch_lifecycle_events(text):
    events = []
    marker = "generic dispatch lifecycle:"
    for line in text.splitlines():
        if marker not in line:
            continue
        payload = line.split(marker, 1)[1].strip()
        try:
            event = json.loads(payload)
        except Exception:
            continue
        if isinstance(event, dict) and event.get("event") in {"begin", "stage", "end"}:
            events.append(event)
    return events

def summarize_dispatch_lifecycle(events):
    begins = [e for e in events if e.get("event") == "begin"]
    ends = [e for e in events if e.get("event") == "end"]
    stages = [e for e in events if e.get("event") == "stage"]
    begin_ids = {str(e.get("dispatch_id")) for e in begins if e.get("dispatch_id") is not None}
    end_ids = {str(e.get("dispatch_id")) for e in ends if e.get("dispatch_id") is not None}
    return {
        "event_count": len(events),
        "begin_count": len(begins),
        "stage_count": len(stages),
        "end_count": len(ends),
        "unmatched_begin_ids": sorted(begin_ids - end_ids)[:16],
        "unmatched_end_ids": sorted(end_ids - begin_ids)[:16],
        "latest_events": events[-12:],
        "components": sorted({str(e.get("component")) for e in events if e.get("component")}),
        "stages": sorted({str(e.get("stage")) for e in events if e.get("stage")}),
    }

def parse_android_feature_trace(text):
    m = re.search(
        r"pdocker-gpu-executor: Android Vulkan features (?:build_marker=([^ ]+) )?api=([0-9]+)\.([0-9]+) .*?"
        r"shaderInt64=([0-9]+) "
        r"storage16=\{ssbo:([0-9]+),ubo_ssbo:([0-9]+),push:([0-9]+),io:([0-9]+)\} "
        r"storage8=\{ssbo:([0-9]+),ubo_ssbo:([0-9]+),push:([0-9]+)\} "
        r"float16=([0-9]+) int8=([0-9]+) "
        r"subgroup=\{size:([0-9]+),stages:0x([0-9a-fA-F]+),ops:0x([0-9a-fA-F]+)\}",
        text,
    )
    if not m:
        return {}
    return {
        "executor_build_marker": m.group(1) or "",
        "api_version": f"{m.group(2)}.{m.group(3)}",
        "shader_int64": bool(int(m.group(4))),
        "storage16": {
            "ssbo": bool(int(m.group(5))),
            "ubo_ssbo": bool(int(m.group(6))),
            "push": bool(int(m.group(7))),
            "io": bool(int(m.group(8))),
        },
        "storage8": {
            "ssbo": bool(int(m.group(9))),
            "ubo_ssbo": bool(int(m.group(10))),
            "push": bool(int(m.group(11))),
        },
        "shader_float16": bool(int(m.group(12))),
        "shader_int8": bool(int(m.group(13))),
        "subgroup": {
            "size": int(m.group(14)),
            "stages": f"0x{int(m.group(15), 16):x}",
            "ops": f"0x{int(m.group(16), 16):x}",
        },
    }

def parse_spirv_traces(text):
    traces = []
    pattern = re.compile(
        r"pdocker-gpu-executor: SPIR-V trace valid=([0-9]+) truncated=([0-9]+) "
        r"hash=(0x[0-9a-fA-F]+) magic=(0x[0-9a-fA-F]+) version=(0x[0-9a-fA-F]+) "
        r"bound=([0-9]+) local_size=([0-9]+),([0-9]+),([0-9]+) "
        r"(?:local_size_id=([0-9]+),([0-9]+),([0-9]+) )?"
        r"dispatch=([0-9]+),([0-9]+),([0-9]+) push=([0-9]+) bindings=([0-9]+) caps=([^\n]+)"
    )
    for m in pattern.finditer(text):
        traces.append({
            "valid": bool(int(m.group(1))),
            "truncated": bool(int(m.group(2))),
            "hash": m.group(3),
            "magic": m.group(4),
            "version": m.group(5),
            "bound": int(m.group(6)),
            "local_size": [int(m.group(7)), int(m.group(8)), int(m.group(9))],
            "local_size_id": [int(m.group(10) or 0), int(m.group(11) or 0), int(m.group(12) or 0)],
            "dispatch": [int(m.group(13)), int(m.group(14)), int(m.group(15))],
            "push_bytes": int(m.group(16)),
            "bindings": int(m.group(17)),
            "capabilities": [cap.strip() for cap in m.group(18).split(",") if cap.strip() and cap.strip() != "none"],
        })
    return traces

executor_events = extract_executor_json_events(log)
dispatch_lifecycle_events = extract_dispatch_lifecycle_events(log)
dispatch_lifecycle = summarize_dispatch_lifecycle(dispatch_lifecycle_events)
executor_backends = sorted(set(executor_backends) | {e.get("backend_impl") for e in executor_events if e.get("backend_impl")})
executor_errors = sorted(set(executor_errors) | {e.get("error") for e in executor_events if e.get("error")})
spirv_hashes = sorted(set(spirv_hashes) | {e.get("spirv_hash") for e in executor_events if e.get("spirv_hash")})
config_expectations_raw = env_manifest.get("config_propagation_env_fields")
if not isinstance(config_expectations_raw, list) or not config_expectations_raw:
    raise SystemExit(f"invalid config_propagation_env_fields in llama GPU env manifest: {manifest_path}")
config_expectations = []
for item in config_expectations_raw:
    if not isinstance(item, dict) or not item.get("env") or not item.get("executor_field"):
        raise SystemExit(f"invalid config_propagation_env_fields entry in llama GPU env manifest: {manifest_path}")
    evidence_policy = str(item.get("evidence_policy") or "always")
    if evidence_policy not in {"always", "callsite_gated", "q4k_callsite_gated", "q6_callsite_gated"}:
        raise SystemExit(f"invalid evidence_policy for {item['env']} in llama GPU env manifest: {manifest_path}")
    config_expectations.append((str(item["env"]), str(item["executor_field"]), evidence_policy))
config_checks = []
for env_name, event_field, evidence_policy in config_expectations:
    expected = env_bool(env_name)
    observed = observed_event_values(executor_events, event_field)
    if expected is None:
        status = "not-requested"
    elif not observed:
        status = "missing-evidence"
    elif evidence_policy in {"callsite_gated", "q6_callsite_gated"} and expected in observed:
        status = "pass"
    elif (
        evidence_policy == "q4k_callsite_gated"
        and expected is True
        and observed
        and not any(bool(value) for value in observed_event_values(executor_events, "q4k_callsite_detected"))
    ):
        # The Q4_K pipeline retry ladder is enabled only after the executor
        # sees a known Q4_K call-site.  Earlier shaders legitimately report
        # q4k_pipeline_retry_ladder=false even though the environment variable
        # was forwarded.  Do not turn "Q4_K not reached" into a false
        # config-propagation mismatch.
        status = "pass"
    elif (
        evidence_policy == "q6_callsite_gated"
        and expected is True
        and observed
        and not any(
            str(value) == "mul-mat-vec-q6-k-large"
            for value in observed_event_values(executor_events, "cpu_oracle.kernel_hint")
        )
    ):
        # Q6_K-only compatibility lowerings must not be reported as env
        # propagation failures when earlier non-Q6 shaders correctly keep
        # strict passthrough and do not apply the Q6 gate.
        status = "pass"
    elif all(value == expected for value in observed):
        status = "pass"
    else:
        status = "mismatch"
    config_checks.append({
        "env": env_name,
        "executor_field": event_field,
        "evidence_policy": evidence_policy,
        "expected": expected,
        "observed_values": observed[-8:],
        "status": status,
    })
config_propagation = {
    "summary": "fail" if any(item["status"] in {"missing-evidence", "mismatch"} for item in config_checks) else "pass",
    "checks": config_checks,
}
expected_executor_marker = os.environ.get("PDOCKER_GPU_EXECUTOR_EXPECTED_MARKER", "gpu-executor-llama-q4k-callsite-20260520")
expected_icd_marker = os.environ.get("PDOCKER_VULKAN_ICD_EXPECTED_MARKER", "vulkan-icd-feature-chain-marker-20260518")
observed_executor_markers = sorted({
    str(e.get("executor_build_marker"))
    for e in executor_events
    if e.get("executor_build_marker")
} | set(re.findall(r"build_marker=([^\s,}]+)", log)))
observed_icd_markers = sorted(set(re.findall(r"runtime_marker=([^\s,}]+)", log)))
runtime_freshness = {
    "summary": (
        "pass"
        if (
            (not expected_executor_marker or expected_executor_marker in observed_executor_markers) and
            (not expected_icd_marker or expected_icd_marker in observed_icd_markers)
        )
        else "not-requested"
        if not expected_executor_marker and not expected_icd_marker
        else "fail"
    ),
    "expected_executor_marker": expected_executor_marker,
    "expected_icd_marker": expected_icd_marker,
    "observed_executor_markers": observed_executor_markers[-8:],
    "observed_icd_markers": observed_icd_markers[-8:],
    "executor_event_count": len(executor_events),
}
api_trace_binding_samples = []
api_trace_missing = 0
api_trace_range_mismatches = 0
api_trace_effective_offset_mismatches = 0
for event_index, event in enumerate(executor_events):
    if event.get("kernel") != "generic_spirv" or event.get("valid") is not True:
        continue
    for detail in event.get("binding_details") or []:
        if not isinstance(detail, dict):
            continue
        if "api_offset" not in detail or "api_range" not in detail:
            api_trace_missing += 1
            continue
        api_offset = int(detail.get("api_offset") or 0)
        api_range = int(detail.get("api_range") or 0)
        api_memory_offset = int(detail.get("api_memory_offset") or 0)
        effective_offset = int(detail.get("offset") or 0)
        effective_size = int(detail.get("size") or 0)
        if api_range and api_range != effective_size:
            api_trace_range_mismatches += 1
        if api_memory_offset + api_offset != effective_offset:
            api_trace_effective_offset_mismatches += 1
        if len(api_trace_binding_samples) < 16:
            api_trace_binding_samples.append({
                "event_index": event_index,
                "binding": int(detail.get("binding") or 0),
                "api_offset": api_offset,
                "api_range": api_range,
                "api_buffer_size": int(detail.get("api_buffer_size") or 0),
                "api_descriptor_type": int(detail.get("api_descriptor_type") or 0),
                "api_dynamic": bool(detail.get("api_dynamic")),
                "api_memory_offset": api_memory_offset,
                "effective_offset": effective_offset,
                "effective_size": effective_size,
            })
api_understanding = {
    "summary": (
        "missing"
        if api_trace_missing and not api_trace_binding_samples
        else "mismatch"
        if api_trace_range_mismatches or api_trace_effective_offset_mismatches
        else "pass"
        if api_trace_binding_samples
        else "not-observed"
    ),
    "missing_binding_details": api_trace_missing,
    "range_mismatch_count": api_trace_range_mismatches,
    "effective_offset_mismatch_count": api_trace_effective_offset_mismatches,
    "binding_samples": api_trace_binding_samples,
}


def build_api_executor_reconciliation(events):
    """Build typed non-promoting API/executor reconciliation evidence.

    This object is intentionally diagnostic until the producer can attach a
    collision-resistant canonical API-output-to-dispatch proof.  It still
    prevents a worse failure mode: a bare `summary: pass` or an untyped FNV
    blob cannot sneak through as correctness evidence.
    """

    def canonical_sha256(value):
        payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        return "sha256:" + hashlib.sha256(payload).hexdigest()

    records = []
    duplicate_dispatch_ids = []
    identical_duplicate_dispatch_ids = []
    seen_dispatch_ids = {}
    for event_index, event in enumerate(events):
        if not isinstance(event, dict):
            continue
        reconciliation = event.get("reconciliation")
        if not isinstance(reconciliation, dict):
            continue
        sender = reconciliation.get("sender") if isinstance(reconciliation.get("sender"), dict) else {}
        received = reconciliation.get("received") if isinstance(reconciliation.get("received"), dict) else {}
        matches = reconciliation.get("matches") if isinstance(reconciliation.get("matches"), dict) else {}
        receive = reconciliation.get("receive") if isinstance(reconciliation.get("receive"), dict) else {}
        dispatch_id = sender.get("dispatch_id") if sender.get("dispatch_id_present") is True else event.get("dispatch_id")
        if dispatch_id in (None, ""):
            dispatch_id = f"event-{event_index}"
        dispatch_id = str(dispatch_id)
        comparable = {
            key: value
            for key, value in matches.items()
            if isinstance(value, bool) and not str(key).endswith("_comparable")
        }
        if comparable and all(comparable.values()):
            match_status = "diagnostic-match"
        elif any(value is False for value in comparable.values()):
            match_status = "mismatch"
        else:
            match_status = "diagnostic-unmatched"
        record = {
            "event_index": event_index,
            "dispatch_id": dispatch_id,
            "match_status": match_status,
            "hash_algorithm": "fnv1a64",
            "proof_strength": "diagnostic",
            "canonical_raw_fields_present": False,
            "transport": {
                "msg_trunc": receive.get("msg_trunc"),
                "msg_ctrunc": receive.get("msg_ctrunc"),
                "scm_rights_fd_count_copied": receive.get("scm_rights_fd_count_copied", receive.get("scm_rights_fd_count")),
                "raw_command_bytes": receive.get("raw_command_bytes"),
                "command_bytes": receive.get("command_bytes"),
                "core_command_bytes": receive.get("core_command_bytes"),
            },
            "sender": {
                "core_command_hash": sender.get("core_command_hash"),
                "spirv_hash": sender.get("spirv_hash"),
                "descriptor_hash": sender.get("descriptor_hash"),
                "push_hash": sender.get("push_hash"),
                "specialization_hash": sender.get("spec_hash"),
                "dispatch_hash": sender.get("dispatch_hash"),
            },
            "received": {
                "core_command_hash": receive.get("core_command_hash"),
                "core_command_hash_comparable": receive.get("core_command_hash_comparable"),
                "spirv_hash": received.get("spirv_hash"),
                "descriptor_hash": received.get("descriptor_hash"),
                "push_hash": received.get("push_hash"),
                "specialization_hash": received.get("specialization_hash"),
                "dispatch_hash": received.get("dispatch_hash"),
            },
            "matches": matches,
        }
        record["diagnostic_record_sha256"] = canonical_sha256(record)
        identity_record = dict(record)
        identity_record.pop("event_index", None)
        identity_record["diagnostic_record_sha256"] = canonical_sha256(identity_record)
        identity_sha256 = identity_record["diagnostic_record_sha256"]
        previous_identity = seen_dispatch_ids.get(dispatch_id)
        if previous_identity is not None:
            if previous_identity == identity_sha256:
                identical_duplicate_dispatch_ids.append(dispatch_id)
                continue
            duplicate_dispatch_ids.append(dispatch_id)
        else:
            seen_dispatch_ids[dispatch_id] = identity_sha256
        records.append(record)
    if not records:
        return {
            "schema": "pdocker.llama.api-executor-reconciliation.v1",
            "summary": "missing",
            "proof_strength": "none",
            "hash_algorithm": "none",
            "canonical_raw_fields_present": False,
            "missing": ["executor reconciliation records"],
            "dispatches": [],
        }
    if duplicate_dispatch_ids:
        summary = "ambiguous"
    elif any(record.get("match_status") == "mismatch" for record in records):
        summary = "mismatch"
    else:
        summary = "diagnostic"
    result = {
        "schema": "pdocker.llama.api-executor-reconciliation.v1",
        "summary": summary,
        "proof_strength": "diagnostic",
        "hash_algorithm": "fnv1a64",
        "canonical_raw_fields_present": False,
        "record_count": len(records),
        "duplicate_dispatch_ids": duplicate_dispatch_ids,
        "identical_duplicate_dispatch_ids": identical_duplicate_dispatch_ids,
        "dispatches": records[-16:],
        "promotion_allowed": False,
        "promotion_blocker": "FNV-1a transport correlation is diagnostic only; require SHA-256/full proof or canonical raw fields before correctness claims",
    }
    result["diagnostic_set_sha256"] = canonical_sha256({
        "schema": result["schema"],
        "summary": result["summary"],
        "record_count": result["record_count"],
        "duplicate_dispatch_ids": result["duplicate_dispatch_ids"],
        "identical_duplicate_dispatch_ids": result["identical_duplicate_dispatch_ids"],
        "dispatches": result["dispatches"],
    })
    return result


api_executor_reconciliation = build_api_executor_reconciliation(executor_events)
android_feature_trace = parse_android_feature_trace(log)
spirv_traces = parse_spirv_traces(log)
generic_spirv_attempted = (
    json_string_field_seen("kernel", "generic_spirv")
    or "generic dispatch response:" in log
    or "generic SPIR-V dispatch failed" in log
)
executor_submit_generic_dispatch_error = json_string_field_seen("error", "submit-generic-dispatch")
generic_spirv_dispatch_failed = "generic SPIR-V dispatch failed" in log
queue_submit_blocker = "vk::Queue::submit: ErrorFeatureNotPresent" in log
pipeline_feature_blocker = any(
    e.get("error") == "create-generic-compute-pipeline" and int(e.get("vk_result") or 0) == -13
    for e in executor_events
)
android_vulkan_dispatch_blocker = (
    json_string_field_seen("backend_impl", "android_vulkan")
    and executor_submit_generic_dispatch_error
)
generic_spirv_dispatch_blocker = (
    generic_spirv_attempted
    and (
        executor_submit_generic_dispatch_error
        or generic_spirv_dispatch_failed
        or queue_submit_blocker
    )
)
executor_feature_mismatches = sorted({
    str(item)
    for event in executor_events
    if event.get("spirv_feature_mismatch") is True
    for item in (event.get("spirv_feature_mismatches") or [])
})
repeating_layer_matches = [int(m.group(1)) for m in re.finditer(r"offloading ([0-9]+) repeating layers to GPU", log)]
offloaded_layer_matches = [
    (int(m.group(1)), int(m.group(2)))
    for m in re.finditer(r"offloaded ([0-9]+)/([0-9]+) layers to GPU", log)
]
gpu_repeating_layers = repeating_layer_matches[-1] if repeating_layer_matches else 0
gpu_offloaded_layers = offloaded_layer_matches[-1][0] if offloaded_layer_matches else 0
gpu_total_layers = offloaded_layer_matches[-1][1] if offloaded_layer_matches else 0
evidence = {
    "vulkan_device_seen": "Vulkan0 (pdocker Vulkan bridge" in log or "Vulkan0 model buffer size" in log,
    "offload_seen": "offloading" in log,
    "model_loaded": "main: model loaded" in log,
    "serve_reachable": bool(int(gpu_served_s)),
    "buffer_allocation_blocker": "unable to allocate Vulkan0 buffer" in log,
    "assert_blocker": "GGML_ASSERT" in log,
    "buffer_range_assert_blocker": "ggml_backend_buffer_get_alloc_size" in log,
    "gpu_model_buffer_seen": "Vulkan0 model buffer size" in log,
    "gpu_repeating_layers": gpu_repeating_layers,
    "gpu_offloaded_layers": gpu_offloaded_layers,
    "gpu_total_layers": gpu_total_layers,
    "gpu_output_only_offload": bool(gpu_offloaded_layers and gpu_repeating_layers == 0),
    "generic_dispatch_response_seen": "generic dispatch response:" in log,
    "generic_spirv_dispatch_attempted": generic_spirv_attempted,
    "generic_spirv_dispatch_seen": json_string_field_seen("kernel", "generic_spirv") and json_bool_field_seen("valid", True),
    "ngl_zero_generic_spirv_dispatch": int(gpu_layers) == 0 and json_string_field_seen("kernel", "generic_spirv") and json_bool_field_seen("valid", True),
    "executor_spirv_feature_mismatch": bool(executor_feature_mismatches),
    "executor_spirv_feature_mismatches": executor_feature_mismatches,
    "generic_spirv_dispatch_failed": generic_spirv_dispatch_failed,
    "pipeline_feature_blocker": pipeline_feature_blocker,
    "executor_spirv_trace_seen": "pdocker-gpu-executor: SPIR-V trace" in log,
    "executor_feature_trace_seen": "pdocker-gpu-executor: Android Vulkan features" in log,
    "android_vulkan_dispatch_blocker": android_vulkan_dispatch_blocker,
    "executor_submit_generic_dispatch_error": executor_submit_generic_dispatch_error,
    "executor_fallback_dispatch_blocker": (
        json_string_field_seen("backend_affinity", "fallback")
        and executor_submit_generic_dispatch_error
    ),
    "queue_submit_blocker": queue_submit_blocker,
    "spirv_dispatch_blocker": (
        "real SPIR-V dispatch is not lowered yet" in log
        or queue_submit_blocker
        or generic_spirv_dispatch_blocker
    ),
}
gpu_correctness_summary = correctness.get("summary", {}).get("correctness")
differential_correctness_summary = differential_correctness.get("summary")
service_summary = service_readiness.get("summary") if isinstance(service_readiness.get("summary"), dict) else {}
service_health = service_readiness.get("health") if isinstance(service_readiness.get("health"), dict) else {}
service_completion = service_readiness.get("completion") if isinstance(service_readiness.get("completion"), dict) else {}
service_models = service_readiness.get("models") if isinstance(service_readiness.get("models"), dict) else {}
completion_ready = service_summary.get("completion") == "pass" or service_completion.get("ok") is True
health_ready = service_summary.get("health") == "pass" or service_health.get("ok") is True
models_ready = service_summary.get("models") == "pass" or service_models.get("ok") is True
completion_timeout = (
    bool(int(gpu_served_s))
    and health_ready
    and models_ready
    and not completion_ready
    and "timed out" in str(service_completion.get("error") or "").lower()
)
service_completion_wrong_output = (
    bool(int(gpu_served_s))
    and health_ready
    and models_ready
    and completion_ready
    and service_completion.get("passed") is False
)
service_prompt_sanity = {
    "summary": (
        "fail"
        if service_completion_wrong_output
        else "pass"
        if bool(int(gpu_served_s)) and health_ready and models_ready and completion_ready
        else "not-run"
    ),
    "health_ready": health_ready,
    "models_ready": models_ready,
    "completion_ready": completion_ready,
    "completion_passed": (
        service_completion.get("passed")
        if isinstance(service_completion.get("passed"), bool)
        else None
    ),
    "prompt": service_completion.get("prompt"),
    "expected": service_completion.get("expected"),
    "content_excerpt": service_completion.get("content_excerpt") or service_completion.get("content"),
    "duration_ms": service_completion.get("duration_ms"),
}
if evidence.get("buffer_range_assert_blocker"):
    blocker_class = "vulkan_buffer_range_accounting"
    blocker_detail = "scheduler warmup hit ggml_backend_buffer_get_alloc_size"
elif evidence.get("buffer_allocation_blocker") or evidence.get("assert_blocker"):
    blocker_class = "vulkan_buffer_allocation"
    blocker_detail = "Vulkan buffer allocation/assertion failed before dispatch"
elif pipeline_feature_blocker:
    blocker_class = "vulkan_pipeline_feature"
    blocker_detail = "Android Vulkan rejected a ggml generic SPIR-V compute pipeline with VK_ERROR_FEATURE_NOT_PRESENT"
elif generic_spirv_dispatch_blocker:
    blocker_class = "vulkan_generic_spirv_dispatch"
    blocker_detail = "generic SPIR-V dispatch reached submit-generic-dispatch / queue submit failure"
elif completion_timeout:
    blocker_class = "llama_completion_timeout"
    blocker_detail = "HTTP /health and /v1/models passed, but deterministic /completion did not finish during readiness probing"
elif service_completion_wrong_output:
    blocker_class = "llama_completion_wrong_output"
    blocker_detail = (
        "HTTP /health, /v1/models, and deterministic /completion returned, "
        "but the required prompt check failed at the service boundary"
    )
elif runtime_freshness["summary"] == "fail":
    blocker_class = "runtime_freshness_mismatch"
    blocker_detail = "expected GPU executor build marker was not observed; test may be running stale native code or missing executor evidence"
elif queue_submit_blocker:
    blocker_class = "vulkan_queue_submit_feature"
    blocker_detail = "llama.cpp submitted a Vulkan workload, but vkQueueSubmit failed with ErrorFeatureNotPresent before the executor trace boundary"
elif executor_feature_mismatches:
    blocker_class = "vulkan_feature_mismatch"
    blocker_detail = "generic SPIR-V dispatch ran while executor feature policy reports missing features: " + ",".join(executor_feature_mismatches)
elif config_propagation["summary"] == "fail":
    blocker_class = "config_propagation_mismatch"
    blocker_detail = "one or more requested bridge tuning options did not appear with the expected value in executor evidence"
elif int(gpu_layers) == 0 and evidence.get("generic_spirv_dispatch_seen") and bool(int(gpu_served_s)) and (
    gpu_correctness_summary == "fail" or
    differential_correctness_summary == "fail"
):
    blocker_class = "vulkan_backend_control_mismatch"
    blocker_detail = "Vulkan mode with n-gpu-layers=0 still executed generic SPIR-V and diverged from the CPU/no-offload control"
elif bool(int(gpu_served_s)) and (
    gpu_correctness_summary == "fail" or
    differential_correctness_summary == "fail"
):
    blocker_class = "gpu_correctness_mismatch"
    blocker_detail = "GPU offload served, but correctness probes do not match the CPU/no-offload control"
elif evidence.get("generic_spirv_dispatch_seen") and bool(int(gpu_served_s)):
    blocker_class = "bridge_dispatch_performance"
    blocker_detail = "generic SPIR-V dispatch served; benchmark throughput is the remaining gap"
elif evidence.get("offload_seen") and gpu_offloaded_layers > 1 and bool(int(gpu_served_s)):
    blocker_class = "bridge_dispatch_performance"
    blocker_detail = "Vulkan offload served with repeating transformer layers, but throughput is still below the 10x target"
elif evidence.get("gpu_output_only_offload") and bool(int(gpu_served_s)):
    blocker_class = "insufficient_gpu_offload_depth"
    blocker_detail = "llama.cpp served, but only the output layer was offloaded; repeating transformer layers stayed on CPU"
else:
    blocker_class = "vulkan_device_discovery"
    blocker_detail = "Vulkan offload evidence was not sufficient to classify a later blocker"
allocations = [
    int(m.group(1))
    for m in re.finditer(r"pdocker-vulkan-icd: allocate ([0-9]+) bytes", log)
]
created_buffers = [
    int(m.group(1))
    for m in re.finditer(r"pdocker-vulkan-icd: create-buffer size=([0-9]+)", log)
]
descriptor_ranges = [
    int(m.group(1))
    for m in re.finditer(r"descriptor storage binding=[0-9]+ buffer_size=[0-9]+ offset=[0-9]+ range=([0-9]+)", log)
]
descriptor_array_layouts = [
    {
        "binding": int(m.group(1)),
        "count": int(m.group(2)),
        "type": int(m.group(3)),
        "flattened_capacity": int(m.group(4)),
    }
    for m in re.finditer(
        r"descriptor array layout binding=([0-9]+) count=([0-9]+) type=([0-9]+) flattened_capacity=([0-9]+)",
        log,
    )
]
cpu_mapped_model_mib = [
    float(m.group(1))
    for m in re.finditer(r"CPU_Mapped model buffer size =\s+([0-9.]+) MiB", log)
]
vulkan_model_mib = [
    float(m.group(1))
    for m in re.finditer(r"Vulkan0 model buffer size =\s+([0-9.]+) MiB", log)
]
def compare_profile_value(name, mode="vulkan-raw"):
    profiles = env_manifest.get("compare_mode_env_profiles")
    if not isinstance(profiles, dict):
        return None
    profile = profiles.get(mode)
    if not isinstance(profile, dict):
        return None
    for section in ("env", "trace_alloc_env"):
        entries = profile.get(section) or []
        if not isinstance(entries, list):
            continue
        for item in entries:
            if not isinstance(item, dict) or item.get("env") != name:
                continue
            if item.get("host_override") is True and name in os.environ:
                return os.environ[name]
            if isinstance(item.get("default"), str):
                return item["default"]
            if isinstance(item.get("default_template"), str):
                return item["default_template"].format(
                    workspace_host="",
                    gpu_layers=gpu_layers,
                )
    return None

def int_runtime_or_profile_env(name):
    value = effective_runtime_env.get(name)
    if value is None:
        value = compare_profile_value(name)
    try:
        return int(str(value), 0)
    except (TypeError, ValueError):
        return 0

configured_clamps = {
    "PDOCKER_VULKAN_MAX_BUFFER_BYTES": int_runtime_or_profile_env("PDOCKER_VULKAN_MAX_BUFFER_BYTES"),
    "GGML_VK_FORCE_MAX_BUFFER_SIZE": int_runtime_or_profile_env("GGML_VK_FORCE_MAX_BUFFER_SIZE"),
    "GGML_VK_FORCE_MAX_ALLOCATION_SIZE": int_runtime_or_profile_env("GGML_VK_FORCE_MAX_ALLOCATION_SIZE"),
    "GGML_VK_SUBALLOCATION_BLOCK_SIZE": int_runtime_or_profile_env("GGML_VK_SUBALLOCATION_BLOCK_SIZE"),
}
bridge_max_buffer_bytes = configured_clamps["PDOCKER_VULKAN_MAX_BUFFER_BYTES"]
largest_allocation = max(allocations) if allocations else 0
largest_created_buffer = max(created_buffers) if created_buffers else 0
model_cpu_mapped_bytes = int(cpu_mapped_model_mib[-1] * 1024 * 1024) if cpu_mapped_model_mib else 0
chunking_pressure = {
    "configured_bridge_max_buffer_bytes": bridge_max_buffer_bytes,
    "largest_allocation_bytes": largest_allocation,
    "largest_created_buffer_bytes": largest_created_buffer,
    "allocation_near_clamp": bool(bridge_max_buffer_bytes and largest_allocation and largest_allocation >= int(bridge_max_buffer_bytes * 0.90)),
    "model_cpu_mapped_bytes": model_cpu_mapped_bytes,
    "model_cpu_mapped_exceeds_bridge_clamp": bool(bridge_max_buffer_bytes and model_cpu_mapped_bytes and model_cpu_mapped_bytes > bridge_max_buffer_bytes),
    "vulkan_model_buffer_mib": vulkan_model_mib[-1] if vulkan_model_mib else 0.0,
    "descriptor_range_max_bytes": max(descriptor_ranges) if descriptor_ranges else 0,
    "descriptor_array_layout_seen": bool(descriptor_array_layouts),
    "descriptor_array_layouts": descriptor_array_layouts[-16:],
}
advertised_limits = {
    "configured_clamps": configured_clamps,
    "icd_advertises_subgroup_arithmetic_by_default": "PDOCKER_VULKAN_DISABLE_SUBGROUP_ARITHMETIC" not in log,
    "executor_android_features": android_feature_trace,
    "spirv_trace_count": len(spirv_traces),
    "last_spirv_trace": spirv_traces[-1] if spirv_traces else {},
}
Q6_K_MATVEC_SPIRV_HASHES = {
    "0x274f68a67dfef210",
    "0x1bf751845c5dce75",
    "0xe38f6a6a906d765c",
    "0xbefdfb97e9734eb3",
    "0x09c4622d92c6acb9",
    "0x498c69a047eb3b2f",
    "0xe5cd19682257a368",
    "0x7ec0292e948c9b41",
}

def normalized_spirv_hash(value):
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    return text

def q6_probe_equivalent_hashes():
    """Map valid-module probe hashes back to the original Q6 source hash.

    Q6 probes deliberately replace the submitted SPIR-V with an instrumented
    still-valid module.  The event therefore carries the probe hash, not the
    original llama.cpp Q6 hash.  Treating that as "not Q6" makes the evidence
    disappear from the Q6 diagnostics even though the manifest explicitly says
    it is a probe for the Q6 source shader.  This mapping is fail-closed: it is
    enabled only when the runtime env records an original hash that is already
    in the Q6 allow-list and a concrete effective probe hash.
    """

    env = globals().get("effective_runtime_env", {})
    if not isinstance(env, dict):
        return {}
    expected = normalized_spirv_hash(env.get("PDOCKER_GPU_SPIRV_PROBE_EXPECTED_HASH"))
    effective = normalized_spirv_hash(env.get("PDOCKER_GPU_SPIRV_PROBE_EFFECTIVE_HASH"))
    if expected in Q6_K_MATVEC_SPIRV_HASHES and effective:
        return {effective: expected}
    return {}

def event_spirv_identity_hashes(event):
    hashes = []
    equivalents = q6_probe_equivalent_hashes()
    for field in ("source_spirv_hash", "spirv_hash", "effective_spirv_hash"):
        value = event.get(field) if isinstance(event, dict) else None
        if value:
            normalized = normalized_spirv_hash(value)
            if normalized:
                hashes.append(normalized)
                if normalized in equivalents:
                    hashes.append(equivalents[normalized])
    return hashes

def event_has_q6_matvec_identity(event):
    return any(value in Q6_K_MATVEC_SPIRV_HASHES for value in event_spirv_identity_hashes(event))

valid_spirv_events = [
    e for e in executor_events
    if e.get("kernel") == "generic_spirv"
    and e.get("backend_impl") == "android_vulkan"
    and e.get("valid") is True
]
q6_valid_spirv_events = [
    e for e in valid_spirv_events
    if event_has_q6_matvec_identity(e)
]
q6_dispatch_lifecycle_events = [
    e for e in dispatch_lifecycle_events
    if isinstance(e, dict)
    and e.get("event") == "begin"
    and event_has_q6_matvec_identity(e)
]

def parse_spirv_probe_icd_events(raw_log):
    events = []
    armed_re = re.compile(
        r"SPIR-V probe replay armed: manifest=(?P<manifest>\\S+) "
        r"source_hash=(?P<source_hash>0x[0-9a-fA-F]+) "
        r"effective_hash=(?P<effective_hash>0x[0-9a-fA-F]+) "
        r"debug_set=(?P<debug_set>\\d+) debug_binding=(?P<debug_binding>\\d+) "
        r"debug_bytes=(?P<debug_bytes>\\d+) transport=(?P<transport>\\S+)"
    )
    skipped_re = re.compile(
        r"SPIR-V probe replay skipped non-target shader expected=(?P<expected>0x[0-9a-fA-F]+) "
        r"actual=(?P<actual>0x[0-9a-fA-F]+)"
    )
    rejected_re = re.compile(r"SPIR-V probe replay rejected: (?P<reason>.*)")
    for line in raw_log.splitlines():
        match = armed_re.search(line)
        if match:
            event = {"event": "armed", **match.groupdict()}
            for field in ("debug_set", "debug_binding", "debug_bytes"):
                event[field] = int(event[field])
            events.append(event)
            continue
        match = skipped_re.search(line)
        if match:
            events.append({"event": "skipped_non_target", **match.groupdict()})
            continue
        match = rejected_re.search(line)
        if match:
            events.append({"event": "rejected", "reason": match.group("reason")[:500]})
    return events

def build_spirv_probe_env_audit():
    keys = [str(key) for key in manifest_probe_env_keys if isinstance(key, str)]
    requested = {str(k): str(v) for k, v in manifest_requested_env.items() if str(k) in keys}
    planned = {str(k): str(v) for k, v in planned_env.items() if str(k) in keys}
    observed = {str(k): str(v) for k, v in effective_runtime_env.items() if str(k) in keys}
    requested_any = bool(requested or planned or observed)
    icd_events = parse_spirv_probe_icd_events(log)
    expected_source = normalized_spirv_hash(observed.get("PDOCKER_GPU_SPIRV_PROBE_EXPECTED_HASH"))
    expected_effective = normalized_spirv_hash(observed.get("PDOCKER_GPU_SPIRV_PROBE_EFFECTIVE_HASH"))
    try:
        expected_debug_binding = int(str(observed.get("PDOCKER_GPU_SPIRV_PROBE_DEBUG_BINDING", "")), 0)
    except ValueError:
        expected_debug_binding = None
    try:
        expected_debug_set = int(str(observed.get("PDOCKER_GPU_SPIRV_PROBE_DEBUG_SET", "")), 0)
    except ValueError:
        expected_debug_set = None
    try:
        expected_debug_bytes = int(str(observed.get("PDOCKER_GPU_SPIRV_PROBE_DEBUG_BYTES", "")), 0)
    except ValueError:
        expected_debug_bytes = None

    def armed_matches(event):
        if event.get("event") != "armed":
            return False
        if expected_source and normalized_spirv_hash(event.get("source_hash")) != expected_source:
            return False
        if expected_effective and normalized_spirv_hash(event.get("effective_hash")) != expected_effective:
            return False
        if expected_debug_binding is not None and event.get("debug_binding") != expected_debug_binding:
            return False
        if expected_debug_set is not None and event.get("debug_set") != expected_debug_set:
            return False
        if expected_debug_bytes is not None and event.get("debug_bytes") != expected_debug_bytes:
            return False
        return True

    matching_armed = [event for event in icd_events if armed_matches(event)]
    executor_probe_events = []
    executor_debug_binding_events = []
    for event_index, event in enumerate(executor_events):
        if not isinstance(event, dict):
            continue
        event_source = normalized_spirv_hash(event.get("source_spirv_hash"))
        event_effective = normalized_spirv_hash(event.get("effective_spirv_hash"))
        source_matches = not expected_source or event_source == expected_source
        effective_matches = not expected_effective or event_effective == expected_effective
        has_debug_binding = False
        for detail in event.get("binding_details") or []:
            if not isinstance(detail, dict):
                continue
            if detail.get("debug_probe_binding") is True:
                has_debug_binding = True
                break
        if source_matches and effective_matches:
            executor_probe_events.append(event_index)
            if has_debug_binding:
                executor_debug_binding_events.append(event_index)
    host_to_container_missing = sorted(key for key in requested if key not in observed)
    summary = (
        "not-requested"
        if not requested_any
        else "fail"
        if host_to_container_missing
        else "pass"
        if matching_armed and executor_debug_binding_events
        else "partial"
    )
    return {
        "schema": "pdocker.llama.spirv-probe-env-audit.v1",
        "summary": summary,
        "keys": keys,
        "transport_path": [
            "host_env",
            "engine_create_payload",
            "container_runtime_env",
            "vulkan_icd_log",
            "vulkan_dispatch_v4_size_option",
            "executor_binding_details",
            "compare_artifact",
        ],
        "debug_binding_transport": "PDOCKER_GPU_SPIRV_PROBE_DEBUG_BINDING is forwarded as a VULKAN_DISPATCH_V4 size option; executor getenv is not trusted for the audit.",
        "host_requested": requested,
        "planned_container": planned,
        "runtime_observed": observed,
        "host_to_container": {
            "summary": "not-requested" if not requested else "fail" if host_to_container_missing else "pass",
            "missing": host_to_container_missing,
        },
        "icd": {
            "summary": "not-requested" if not requested_any else "pass" if matching_armed else "missing-evidence",
            "event_count": len(icd_events),
            "matching_armed_count": len(matching_armed),
            "events": icd_events[-8:],
        },
        "executor": {
            "summary": "not-requested" if not requested_any else "pass" if executor_debug_binding_events else "missing-evidence",
            "matching_source_effective_event_indices": executor_probe_events[-16:],
            "debug_binding_event_indices": executor_debug_binding_events[-16:],
        },
        "artifact": {
            "summary": "pass",
            "runtime_env_manifest_recorded": bool(runtime_env_manifest),
            "runtime_env_record_schema": runtime_env_manifest.get("record_schema"),
        },
    }

spirv_probe_env_audit = build_spirv_probe_env_audit()


def retain_diagnostic_events(priority_events, tail_events, limit=8):
    """Keep bounded executor evidence while never dropping known Q6 candidates.

    The old `[-4:]` tail sample made an ngl=1 run look as if Q6_K was never
    reached when the final-projection dispatch was present in lifecycle logs but
    outside the compact response sample.  This helper keeps the payload small
    and deterministic, but promotes diagnostically important Q6 events first.
    """

    retained = []
    seen = set()

    def key(event):
        if not isinstance(event, dict):
            return None
        return (
            event.get("dispatch_id"),
            event.get("spirv_hash"),
            tuple(event.get("dispatch") or []),
            event.get("descriptor_hash"),
            event.get("push_hash"),
        )

    for event in list(priority_events or []) + list(tail_events or []):
        marker = key(event)
        if marker is None or marker in seen:
            continue
        retained.append(event)
        seen.add(marker)
        if len(retained) >= limit:
            break
    return retained


generic_spirv_dispatch = {
    "attempted": generic_spirv_attempted,
    "valid_android_vulkan_events": retain_diagnostic_events(
        q6_valid_spirv_events,
        valid_spirv_events[-4:],
    ),
    "q6_candidate_events": q6_valid_spirv_events[-8:],
    "q6_dispatch_lifecycle_events": q6_dispatch_lifecycle_events[-8:],
    "largest_shader_events": sorted(
        valid_spirv_events,
        key=lambda event: int(event.get("shader_bytes") or 0),
        reverse=True,
    )[:8],
    "largest_binding_events": sorted(
        valid_spirv_events,
        key=lambda event: sum(int(detail.get("size") or 0) for detail in event.get("binding_details") or []),
        reverse=True,
    )[:8],
    "failed_events": [
        e for e in executor_events
        if e.get("valid") is False and (e.get("kernel") == "generic_spirv" or e.get("error") == "submit-generic-dispatch")
    ][:4],
    "fallback_events": [e for e in executor_events if e.get("backend_affinity") == "fallback"][-4:],
    "llama_throw": "vk::Queue::submit: ErrorFeatureNotPresent" if queue_submit_blocker else "",
}


def compact_pre_q6_failure(generic_dispatch, q6_diagnostics):
    """Record why a run stopped before Q6_K without promoting correctness.

    This is deliberately derived from structured executor JSON, not from prose
    log snippets, so a stale or unrelated llama log line cannot become a GPU
    correctness diagnosis.  The verifier mirrors this shape as
    `pre_http_failure_evidence`.
    """

    if not isinstance(generic_dispatch, dict):
        generic_dispatch = {}
    if not isinstance(q6_diagnostics, dict):
        q6_diagnostics = {}
    failed = [
        event for event in (generic_dispatch.get("failed_events") or [])
        if isinstance(event, dict)
    ]
    event = failed[0] if failed else {}
    pipeline_key = event.get("pipeline_key") if isinstance(event.get("pipeline_key"), dict) else {}

    def pick(source, keys):
        if not isinstance(source, dict):
            return {}
        return {key: source.get(key) for key in keys if key in source}

    return {
        "generic_spirv_attempted": generic_dispatch.get("attempted") is True,
        "failed_event_count": len(failed),
        "failure_event": pick(event, [
            "stage",
            "error",
            "vk_result",
            "spirv_hash",
            "shader_bytes",
            "entry",
            "bindings",
            "dispatch",
            "push_bytes",
            "requested_feature_mask",
            "requested_feature_mask_present",
            "strict_passthrough",
            "spirv_required_feature_mask",
            "spirv_requested_feature_missing_mask",
            "spirv_requested_feature_mismatches",
            "spirv_feature_requirements",
            "spirv_feature_mismatch",
            "spirv_feature_mismatches",
            "android_vulkan_features",
            "android_vulkan_enabled_features",
            "spirv_capabilities",
        ]),
        "pipeline_key": pick(pipeline_key, [
            "spirv_hash",
            "spec_hash",
            "layout_bindings",
            "descriptor_sets",
            "push_bytes",
        ]),
        "llama_throw": generic_dispatch.get("llama_throw") or "",
        "q6_reachability": {
            "event_count": q6_diagnostics.get("event_count", 0),
            "dispatch_seen": q6_diagnostics.get("q6_dispatch_seen") is True,
            "dispatch_event_count": q6_diagnostics.get("q6_dispatch_event_count", 0),
            "blocker_class": q6_diagnostics.get("blocker_class") or "not-reached",
            "diagnostic_interpretation": q6_diagnostics.get("diagnostic_interpretation") or "",
        },
    }


final_projection_candidate = max(
    valid_spirv_events,
    key=lambda event: sum(int(detail.get("size") or 0) for detail in event.get("binding_details") or []),
    default={},
)
f32_samples = []
for detail in final_projection_candidate.get("binding_details") or []:
    if isinstance(detail, dict) and isinstance(detail.get("f32_after_dispatch"), list):
        f32_samples.extend(detail.get("f32_after_dispatch") or [])
finite_f32_sample_count = sum(
    1
    for sample in f32_samples
    if isinstance(sample, dict)
    and isinstance(sample.get("value"), (int, float))
    and math.isfinite(float(sample.get("value")))
)
readonly_binding_hash_mismatches = []
primary_readonly_upload_hash_mismatches = []
primary_readonly_dispatch_mutations = []
for event in valid_spirv_events:
    for detail in event.get("binding_details") or []:
        if not isinstance(detail, dict):
            continue
        if not detail.get("readable") or detail.get("writable"):
            continue
        before_hash = detail.get("fd_before_hash")
        upload_hash = detail.get("gpu_after_upload_hash")
        gpu_hash = detail.get("gpu_after_dispatch_hash")
        if before_hash and gpu_hash and before_hash != gpu_hash:
            readonly_binding_hash_mismatches.append({
                "spirv_hash": event.get("spirv_hash"),
                "dispatch": event.get("dispatch"),
                "binding": detail.get("binding"),
                "index": detail.get("index"),
                "alias_rep": detail.get("alias_rep"),
                "offset": detail.get("offset"),
                "size": detail.get("size"),
                "fd_before_hash": before_hash,
                "gpu_after_upload_hash": upload_hash,
                "gpu_after_dispatch_hash": gpu_hash,
            })
            if detail.get("alias_rep") == detail.get("index"):
                if upload_hash and before_hash != upload_hash:
                    primary_readonly_upload_hash_mismatches.append(readonly_binding_hash_mismatches[-1])
                elif upload_hash and upload_hash != gpu_hash:
                    primary_readonly_dispatch_mutations.append(readonly_binding_hash_mismatches[-1])
                elif not upload_hash:
                    primary_readonly_upload_hash_mismatches.append(readonly_binding_hash_mismatches[-1])
q6_oracle_events = [
    e for e in valid_spirv_events
    if isinstance(e.get("cpu_oracle"), dict)
    and e.get("cpu_oracle", {}).get("kernel_hint") == "mul-mat-vec-q6-k-large"
]
q6_probe_events = [
    e for e in q6_valid_spirv_events
    if isinstance(e.get("cpu_oracle"), dict)
    and e.get("cpu_oracle", {}).get("status") == "unsupported-shader-hash"
]
q6_latest = (
    q6_oracle_events[-1]
    if q6_oracle_events
    else q6_probe_events[-1]
    if q6_probe_events
    else {}
)
q6_latest_dispatch_event = (
    q6_valid_spirv_events[-1]
    if q6_valid_spirv_events
    else q6_dispatch_lifecycle_events[-1]
    if q6_dispatch_lifecycle_events
    else {}
)
q6_dispatch_seen = bool(q6_valid_spirv_events or q6_dispatch_lifecycle_events)
q6_latest_oracle = q6_latest.get("cpu_oracle") if isinstance(q6_latest.get("cpu_oracle"), dict) else {}
q6_latest_partial = (
    q6_latest_oracle.get("partial_diagnostic")
    if isinstance(q6_latest_oracle.get("partial_diagnostic"), dict)
    else {}
)
Q6_DESCRIPTOR_INVARIANT_FIELDS = (
    "offset_equals_memory_plus_api_offset",
    "gpu_offset_equals_memory_plus_api_offset",
    "descriptor_offset_equals_api_offset",
    "descriptor_range_matches_api_range",
)


def compact_q6_binding_detail(detail):
    return {
        "index": detail.get("index"),
        "binding": detail.get("binding"),
        "alias_rep": detail.get("alias_rep"),
        "offset": detail.get("offset"),
        "size": detail.get("size"),
        "api_offset": detail.get("api_offset"),
        "api_range": detail.get("api_range"),
        "api_memory_offset": detail.get("api_memory_offset"),
        "api_buffer_size": detail.get("api_buffer_size"),
        "api_memory_id": detail.get("api_memory_id"),
        "api_buffer_id": detail.get("api_buffer_id"),
        "binding_gpu_offset": detail.get("binding_gpu_offset"),
        "binding_descriptor_offset": detail.get("binding_descriptor_offset"),
        "offset_equals_memory_plus_api_offset": detail.get("offset_equals_memory_plus_api_offset"),
        "gpu_offset_equals_memory_plus_api_offset": detail.get("gpu_offset_equals_memory_plus_api_offset"),
        "descriptor_offset_equals_api_offset": detail.get("descriptor_offset_equals_api_offset"),
        "descriptor_range_matches_api_range": detail.get("descriptor_range_matches_api_range"),
        "descriptor_range_mismatch": detail.get("descriptor_range_mismatch"),
        "readable": detail.get("readable"),
        "writable": detail.get("writable"),
        "resident": detail.get("resident"),
        "cache_hit": detail.get("cache_hit"),
        "fd_before_hash": detail.get("fd_before_hash"),
        "gpu_after_upload_hash": detail.get("gpu_after_upload_hash"),
        "gpu_after_dispatch_hash": detail.get("gpu_after_dispatch_hash"),
        "fd_after_hash": detail.get("fd_after_hash"),
        "writeback_offset": detail.get("writeback_offset"),
        "writeback_bytes": detail.get("writeback_bytes"),
        "device_local_staged": detail.get("device_local_staged"),
        "writeback_verified": detail.get("writeback_verified"),
        "writeback_mismatch": detail.get("writeback_mismatch"),
        "q6_row_indexed": detail.get("q6_row_indexed"),
        "q6_sample_indices": detail.get("q6_sample_indices"),
        "f32_after_dispatch": detail.get("f32_after_dispatch"),
        "f32_after_writeback": detail.get("f32_after_writeback"),
    }


def numeric_close_to_zero(value, tolerance=1.0e-3):
    try:
        return abs(float(value)) <= tolerance
    except (TypeError, ValueError):
        return False


def hash_evidence_present(value):
    return bool(value) and value != "0x0000000000000000"


def f32_sample_values(samples):
    if not isinstance(samples, list):
        return None
    values = []
    for sample in samples:
        if not isinstance(sample, dict):
            return None
        values.append((sample.get("index"), sample.get("value")))
    return values


Q6_FINAL_STORE_TRACE_EXPECTED_RECORDS = (
    {"probe": 4, "slot_base": 56, "phase": "tail", "candidate_id": 64, "role_code": 4},
    {"probe": 9, "slot_base": 116, "phase": "full", "candidate_id": 130, "role_code": 4},
)


def q6_u32_samples_to_map(samples):
    result = {}
    if not isinstance(samples, list):
        return result
    for item in samples:
        if not isinstance(item, dict):
            continue
        index = item.get("index")
        value = item.get("value")
        if isinstance(index, int):
            result[index] = value if isinstance(value, int) else None
    return result


def q6_bits_to_f32(bits):
    if not isinstance(bits, int):
        return None
    return struct.unpack("<f", struct.pack("<I", bits & 0xFFFFFFFF))[0]


def parse_q6_final_store_trace_v2(bindings):
    parsed_bindings = []
    failures = []
    for binding in bindings:
        if not isinstance(binding, dict):
            continue
        if binding.get("debug_probe_binding") is not True:
            continue
        dispatch = q6_u32_samples_to_map(binding.get("u32_after_dispatch"))
        writeback = q6_u32_samples_to_map(binding.get("u32_after_writeback"))
        if not dispatch:
            continue
        records = []
        for expected in Q6_FINAL_STORE_TRACE_EXPECTED_RECORDS:
            base = int(expected["slot_base"])
            candidate = dispatch.get(base)
            role_code = dispatch.get(base + 1)
            value_bits = dispatch.get(base + 2)
            output_index = dispatch.get(base + 3)
            workgroup_id = [dispatch.get(base + offset) for offset in (4, 5, 6)]
            local_invocation_id = [dispatch.get(base + offset) for offset in (7, 8, 9)]
            record_schema_version = dispatch.get(base + 10)
            unexecuted = candidate in (None, 0) and role_code in (None, 0) and value_bits in (None, 0)
            status = "not-executed" if unexecuted else "pass"
            record_failures = []
            trace_writeback_mismatch_fields = []
            if not unexecuted and candidate != expected["candidate_id"]:
                status = "fail"
                record_failures.append("candidate-id")
            if not unexecuted and role_code != expected["role_code"]:
                status = "fail"
                record_failures.append("role-code")
            if unexecuted:
                trace_status = "not-executed"
            elif (
                record_schema_version == 2
                and isinstance(output_index, int)
                and all(isinstance(value, int) for value in workgroup_id)
                and all(isinstance(value, int) for value in local_invocation_id)
            ):
                trace_status = "pass"
            else:
                trace_status = "fail"
                record_failures.append("trace-v2-metadata")
            if not unexecuted:
                if not writeback:
                    trace_writeback_mismatch_fields.append("u32_after_writeback")
                else:
                    scalar_checks = (
                        ("candidate_id", candidate, writeback.get(base)),
                        ("role_code", role_code, writeback.get(base + 1)),
                        ("value_bits", value_bits, writeback.get(base + 2)),
                        ("output_index", output_index, writeback.get(base + 3)),
                        ("record_schema_version", record_schema_version, writeback.get(base + 10)),
                    )
                    for field_name, dispatch_value, writeback_value in scalar_checks:
                        if dispatch_value != writeback_value:
                            trace_writeback_mismatch_fields.append(field_name)
                    if workgroup_id != [writeback.get(base + offset) for offset in (4, 5, 6)]:
                        trace_writeback_mismatch_fields.append("workgroup_id")
                    if local_invocation_id != [writeback.get(base + offset) for offset in (7, 8, 9)]:
                        trace_writeback_mismatch_fields.append("local_invocation_id")

            record = {
                **expected,
                "observed_candidate_id": candidate,
                "observed_role_code": role_code,
                "value_bits": value_bits,
                "value_f32": q6_bits_to_f32(value_bits),
                "output_index": output_index,
                "workgroup_id": workgroup_id,
                "local_invocation_id": local_invocation_id,
                "record_schema_version": record_schema_version,
                "status": status,
                "trace_status": trace_status,
                "trace_writeback_verified": (
                    None if unexecuted else not trace_writeback_mismatch_fields
                ),
                "trace_writeback_mismatch": (
                    None if unexecuted else bool(trace_writeback_mismatch_fields)
                ),
                "trace_writeback_mismatch_fields": trace_writeback_mismatch_fields,
                "failures": record_failures,
            }
            if writeback:
                record.update({
                    "writeback_candidate_id": writeback.get(base),
                    "writeback_role_code": writeback.get(base + 1),
                    "writeback_value_bits": writeback.get(base + 2),
                    "writeback_value_f32": q6_bits_to_f32(writeback.get(base + 2)),
                    "writeback_output_index": writeback.get(base + 3),
                    "writeback_workgroup_id": [writeback.get(base + offset) for offset in (4, 5, 6)],
                    "writeback_local_invocation_id": [writeback.get(base + offset) for offset in (7, 8, 9)],
                    "writeback_record_schema_version": writeback.get(base + 10),
                })
            if trace_writeback_mismatch_fields and not unexecuted:
                failures.append(
                    "binding %s probe %s final-store trace writeback mismatch: %s"
                    % (binding.get("binding"), expected["probe"], ",".join(trace_writeback_mismatch_fields))
                )
            if record_failures and not unexecuted:
                failures.append(
                    "binding %s probe %s final-store trace failed: %s"
                    % (binding.get("binding"), expected["probe"], ",".join(record_failures))
                )
            records.append(record)
        parsed_bindings.append({
            "binding": binding.get("binding"),
            "set": binding.get("set"),
            "size": binding.get("size"),
            "records": records,
            "executed_final_trace_v2_count": sum(
                1 for record in records
                if record.get("status") == "pass" and record.get("trace_status") == "pass"
            ),
            "summary": (
                "pass"
                if any(
                    record.get("status") == "pass" and record.get("trace_status") == "pass"
                    for record in records
                )
                else "fail"
            ),
        })
    if parsed_bindings and not any(item["summary"] == "pass" for item in parsed_bindings):
        failures.append("no executed Q6 final-store trace-v2 record was found")
    return {
        "schema": "pdocker.q6k.final-store-trace.v2",
        "debug_binding_count": len(parsed_bindings),
        "executed_final_trace_v2_count": sum(
            item.get("executed_final_trace_v2_count", 0) for item in parsed_bindings
        ),
        "bindings": parsed_bindings[:8],
        "summary": "pass" if parsed_bindings and not failures else "fail" if parsed_bindings else "not-run",
        "failures": failures[:8],
    }


def q6_oracle_sample_indices(oracle):
    indices = []

    def add(value):
        try:
            value = int(value)
        except (TypeError, ValueError):
            return
        if value not in indices:
            indices.append(value)

    first_mismatch = oracle.get("first_mismatch") if isinstance(oracle.get("first_mismatch"), dict) else {}
    add(first_mismatch.get("dst_index"))
    for section in ("row_window", "samples"):
        rows = oracle.get(section)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict):
                add(row.get("dst_index"))
    return indices


def q6_row_indexed_samples_match_oracle(detail, oracle_indices):
    if detail.get("q6_row_indexed") is not True:
        return False
    detail_indices = detail.get("q6_sample_indices")
    if not isinstance(detail_indices, list) or not detail_indices:
        return False
    try:
        detail_indices = [int(v) for v in detail_indices]
    except (TypeError, ValueError):
        return False
    if oracle_indices and not set(detail_indices).intersection(set(oracle_indices)):
        return False
    dispatch_values = f32_sample_values(detail.get("f32_after_dispatch"))
    writeback_values = f32_sample_values(detail.get("f32_after_writeback"))
    return dispatch_values is not None and writeback_values is not None


q6_oracle_row_indexed_sample_indices = q6_oracle_sample_indices(q6_latest_oracle)
q6_row_indexed_writeback_evidence = []


q6_binding_details = [
    detail
    for detail in (q6_latest.get("binding_details") or [])
    if isinstance(detail, dict)
]

q6_writable_binding_details = [
    compact_q6_binding_detail(detail)
    for detail in q6_binding_details
    if detail.get("writable")
]
q6_descriptor_range_mismatches = []
q6_descriptor_invariant_mismatches = []
q6_readonly_upload_hash_mismatches = []
q6_readonly_dispatch_mutations = []
q6_readonly_dispatch_alias_side_effects = []
q6_unexpected_readonly_dispatch_mutations = []
q6_writable_writeback_mismatches = []
q6_writable_writeback_unknown = []


def same_q6_storage_window(left, right):
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False

    def parse_int(value):
        if value is None:
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return None
            return int(value, 0)
        return int(value)

    try:
        left_buffer = parse_int(left.get("api_buffer_id"))
        right_buffer = parse_int(right.get("api_buffer_id"))
        left_memory = parse_int(left.get("api_memory_id"))
        right_memory = parse_int(right.get("api_memory_id"))
        if (left_buffer is not None or right_buffer is not None) and left_buffer != right_buffer:
            return False
        if (left_memory is not None or right_memory is not None) and left_memory != right_memory:
            return False

        left_offset = parse_int(left.get("binding_descriptor_offset"))
        right_offset = parse_int(right.get("binding_descriptor_offset"))
        if left_offset is None:
            left_offset = parse_int(left.get("offset"))
        if right_offset is None:
            right_offset = parse_int(right.get("offset"))

        return (
            parse_int(left.get("alias_rep")) == parse_int(right.get("alias_rep"))
            and left_offset == right_offset
            and parse_int(left.get("size")) == parse_int(right.get("size"))
        )
    except (TypeError, ValueError):
        return False


def q6_readonly_mutation_is_alias_side_effect(readonly_detail):
    """Separate expected alias visibility from true read-only mutation.

    llama.cpp may bind the same VkBuffer range through a writable output
    descriptor and read-only descriptor views.  A post-dispatch hash change on
    such read-only views is expected visibility of the same backing storage,
    not proof that the shader wrote through a read-only descriptor.
    """
    if not isinstance(readonly_detail, dict):
        return False
    for writable_detail in q6_writable_binding_details:
        if not same_q6_storage_window(readonly_detail, writable_detail):
            continue
        if (
            hash_evidence_present(readonly_detail.get("gpu_after_dispatch_hash"))
            and hash_evidence_present(writable_detail.get("gpu_after_dispatch_hash"))
            and readonly_detail.get("gpu_after_dispatch_hash")
            == writable_detail.get("gpu_after_dispatch_hash")
        ):
            return True
        if (
            hash_evidence_present(readonly_detail.get("fd_after_hash"))
            and hash_evidence_present(writable_detail.get("fd_after_hash"))
            and readonly_detail.get("fd_after_hash") == writable_detail.get("fd_after_hash")
        ):
            return True
    return False


for detail in q6_binding_details:
    compact_detail = compact_q6_binding_detail(detail)
    if detail.get("descriptor_range_mismatch") is True:
        q6_descriptor_range_mismatches.append(compact_detail)
    for invariant_field in Q6_DESCRIPTOR_INVARIANT_FIELDS:
        invariant_value = detail.get(invariant_field)
        if invariant_value is not True:
            q6_descriptor_invariant_mismatches.append({
                **compact_detail,
                "failed_invariant": invariant_field,
                "reason": "missing-or-not-true",
                "value": invariant_value,
            })
    if detail.get("writable"):
        dispatch_hash = detail.get("gpu_after_dispatch_hash")
        after_hash = detail.get("fd_after_hash")
        compact = compact_q6_binding_detail(detail)
        dispatch_f32 = f32_sample_values(detail.get("f32_after_dispatch"))
        writeback_f32 = f32_sample_values(detail.get("f32_after_writeback"))
        is_q6_output_binding = detail.get("binding") == 2
        has_q6_row_indexed_evidence = q6_row_indexed_samples_match_oracle(
            detail, q6_oracle_row_indexed_sample_indices
        )
        if is_q6_output_binding:
            evidence = dict(compact)
            evidence["row_indexed_samples_match_oracle"] = has_q6_row_indexed_evidence
            q6_row_indexed_writeback_evidence.append(evidence)
        if detail.get("writeback_mismatch") is True or (
            hash_evidence_present(dispatch_hash)
            and hash_evidence_present(after_hash)
            and dispatch_hash != after_hash
        ) or (
            dispatch_f32 is not None
            and writeback_f32 is not None
            and dispatch_f32 != writeback_f32
        ):
            q6_writable_writeback_mismatches.append(compact)
        elif (
            is_q6_output_binding
            and q6_oracle_row_indexed_sample_indices
            and not has_q6_row_indexed_evidence
        ):
            # Fail closed: a Q6 writeback correctness claim needs exact
            # row-indexed post-dispatch/post-writeback samples at oracle
            # row_window/q6_first_mismatch dst indices, not generic samples.
            q6_writable_writeback_unknown.append(compact)
        elif detail.get("writeback_verified") is not True and not (
            hash_evidence_present(dispatch_hash)
            and hash_evidence_present(after_hash)
            and dispatch_hash == after_hash
        ) and not (
            dispatch_f32 is not None
            and writeback_f32 is not None
            and dispatch_f32 == writeback_f32
        ):
            q6_writable_writeback_unknown.append(compact)
    if not detail.get("readable") or detail.get("writable"):
        continue
    before_hash = detail.get("fd_before_hash")
    upload_hash = detail.get("gpu_after_upload_hash")
    dispatch_hash = detail.get("gpu_after_dispatch_hash")
    compact = compact_q6_binding_detail(detail)
    if before_hash and upload_hash and before_hash != upload_hash:
        q6_readonly_upload_hash_mismatches.append(compact)
    elif upload_hash and dispatch_hash and upload_hash != dispatch_hash:
        q6_readonly_dispatch_mutations.append(compact)
        if q6_readonly_mutation_is_alias_side_effect(compact):
            q6_readonly_dispatch_alias_side_effects.append(compact)
        else:
            q6_unexpected_readonly_dispatch_mutations.append(compact)
q6_row_indexed_writeback_verified = bool(q6_row_indexed_writeback_evidence) and all(
    item.get("row_indexed_samples_match_oracle") is True
    for item in q6_row_indexed_writeback_evidence
)
q6_writeback_verified_all = (
    bool(q6_writable_binding_details)
    and not q6_writable_writeback_mismatches
    and not q6_writable_writeback_unknown
    and (not q6_oracle_row_indexed_sample_indices or q6_row_indexed_writeback_verified)
)
q6_first_mismatch = (
    q6_latest_oracle.get("first_mismatch")
    if isinstance(q6_latest_oracle.get("first_mismatch"), dict)
    else {}
)

def collect_q6_debug_probe_bindings(events):
    bindings = []
    seen = set()
    for event in events:
        if not isinstance(event, dict):
            continue
        for detail in event.get("binding_details") or []:
            if not isinstance(detail, dict):
                continue
            if detail.get("debug_probe_binding") is not True:
                continue
            if "u32_after_dispatch" not in detail:
                continue
            key = (
                event.get("dispatch_id"),
                detail.get("set"),
                detail.get("binding"),
                detail.get("gpu_after_dispatch_hash"),
                detail.get("fd_after_hash"),
            )
            if key in seen:
                continue
            seen.add(key)
            bindings.append(detail)
    return bindings


q6_debug_u32_probe = parse_q6_final_store_trace_v2(
    collect_q6_debug_probe_bindings(q6_valid_spirv_events)
)


def classify_q6_debug_u32_probe_blocker(report):
    if not isinstance(report, dict):
        return ""
    if report.get("summary") in {"pass", "not-run"}:
        return ""
    failures = "\n".join(str(item) for item in report.get("failures") or [])
    if "candidate-id" in failures or "role-code" in failures:
        return "q6-debug-u32-probe-metadata-mismatch"
    if "writeback" in failures:
        return "q6-debug-u32-writeback-mismatch"
    if (
        "trace-v2-metadata" in failures
        or "no executed Q6 final-store trace-v2 record" in failures
        or report.get("executed_final_trace_v2_count") == 0
    ):
        return "q6-debug-u32-final-store-trace-missing"
    if report.get("debug_binding_count") == 0:
        return "q6-debug-u32-probe-missing"
    return "q6-debug-u32-probe-invalid"


q6_debug_u32_probe_blocker = classify_q6_debug_u32_probe_blocker(q6_debug_u32_probe)
q6_output_layout_probe = (
    q6_latest_oracle.get("q6_output_layout_probe")
    if isinstance(q6_latest_oracle.get("q6_output_layout_probe"), dict)
    else {}
)
q6_row_provenance_probe = (
    q6_latest_oracle.get("q6_row_provenance_probe")
    if isinstance(q6_latest_oracle.get("q6_row_provenance_probe"), dict)
    else {}
)
q6_partial_signature_probe = (
    q6_latest_oracle.get("q6_partial_signature_probe")
    if isinstance(q6_latest_oracle.get("q6_partial_signature_probe"), dict)
    else {}
)
q6_output_layout_probe_summary = q6_output_layout_probe.get("summary") or "not-run"
q6_row_provenance_probe_summary = q6_row_provenance_probe.get("summary") or "not-run"
q6_partial_signature_probe_summary = q6_partial_signature_probe.get("summary") or "not-run"
try:
    q6_output_layout_mismatch_count = int(q6_output_layout_probe.get("mismatch_count") or 0)
except (TypeError, ValueError):
    q6_output_layout_mismatch_count = 0
try:
    q6_output_layout_found_elsewhere_count = int(q6_output_layout_probe.get("found_elsewhere_count") or 0)
except (TypeError, ValueError):
    q6_output_layout_found_elsewhere_count = 0
q6_output_layout_fixed_offset_rejected = (
    q6_output_layout_probe_summary == "canonical-mismatch-inconclusive"
    and q6_output_layout_mismatch_count >= 16
    and q6_output_layout_found_elsewhere_count > 0
    and q6_output_layout_probe.get("consistent_relative_offset") is False
)
q6_output_layout_samples = q6_output_layout_probe.get("samples")


def q6_store_index_model_has_full_evidence(q6, samples):
    if not isinstance(q6, dict):
        return False
    if q6.get("q6_store_index_model_valid") is not True:
        return False
    if q6.get("q6_store_index_full_coverage") is not True:
        return False
    if q6.get("q6_store_index_sampled_nonzero_j") is not True:
        return False
    if q6.get("q6_store_index_sampled_nonzero_y") is not True:
        return False
    groups = q6.get("q6_dispatch_groups")
    if not (
        isinstance(groups, list)
        and len(groups) == 3
        and all(isinstance(value, int) and value > 0 for value in groups)
    ):
        return False
    for key in ("q6_block_size", "q6_num_rows", "q6_num_cols",
                "q6_store_window_begin", "q6_store_window_end"):
        if not isinstance(q6.get(key), int):
            return False
    if not isinstance(samples, list) or not samples:
        return False
    sampled_nonzero_j = False
    sampled_nonzero_y = False
    for sample in samples:
        if not isinstance(sample, dict) or sample.get("store_formula_valid") is not True:
            return False
        if not isinstance(sample.get("expected_store_index"), int):
            return False
        workgroup = sample.get("store_workgroup")
        if not (
            isinstance(workgroup, list)
            and len(workgroup) == 3
            and all(isinstance(value, int) and value >= 0 for value in workgroup)
        ):
            return False
        for key in ("store_j", "store_row_in_group", "store_row"):
            if not isinstance(sample.get(key), int):
                return False
        sampled_nonzero_j = sampled_nonzero_j or int(sample.get("store_j")) != 0
        sampled_nonzero_y = sampled_nonzero_y or int(workgroup[1]) != 0
    if q6.get("q6_num_cols") > 1 and not sampled_nonzero_j:
        return False
    if groups[1] > 1 and not sampled_nonzero_y:
        return False
    return True


q6_store_index_model_valid = q6_store_index_model_has_full_evidence(
    q6_latest_partial,
    q6_output_layout_samples,
)
q6_native_spirv_identity = {
    "source_spirv_hash": q6_latest.get("source_spirv_hash"),
    "effective_spirv_hash": q6_latest.get("effective_spirv_hash"),
    "pipeline_spirv_hash": (q6_latest.get("pipeline_key") or {}).get("spirv_hash")
    if isinstance(q6_latest.get("pipeline_key"), dict)
    else None,
}


def finite_number(value):
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def values_close(a, b, tolerance=1.0e-3):
    if not finite_number(a) or not finite_number(b):
        return False
    return abs(float(a) - float(b)) <= tolerance


def samples_by_index(samples):
    result = {}
    if not isinstance(samples, list):
        return result
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        try:
            index = int(sample.get("index"))
        except (TypeError, ValueError):
            continue
        result[index] = sample.get("value")
    return result


def build_q6_native_vs_writeback_split():
    if not q6_oracle_events:
        return {
            "summary": "not-reached",
            "oracle_writeback": bool(q6_latest_oracle.get("oracle_writeback")),
            "samples": [],
        }
    if q6_latest_oracle.get("oracle_writeback") is True:
        return {
            "summary": "masked-by-oracle-writeback",
            "oracle_writeback": True,
            "samples": [],
        }
    layout_samples = q6_output_layout_probe.get("samples")
    if not isinstance(layout_samples, list):
        return {
            "summary": "inconclusive",
            "oracle_writeback": False,
            "reason": "missing-q6-output-layout-samples",
            "samples": [],
        }

    fd_after_by_index = {}
    for evidence in q6_row_indexed_writeback_evidence:
        if not isinstance(evidence, dict):
            continue
        fd_after_by_index.update(samples_by_index(evidence.get("f32_after_writeback")))

    joined = []
    class_counts = {
        "native-final-store-or-readback": 0,
        "executor-final-writeback": 0,
        "pass": 0,
        "mixed-or-inconclusive": 0,
    }
    for sample in layout_samples:
        if not isinstance(sample, dict):
            continue
        try:
            dst_index = int(sample.get("dst_index"))
        except (TypeError, ValueError):
            continue
        if dst_index not in fd_after_by_index:
            continue
        expected = sample.get("expected")
        native_gpu_at_dst = sample.get("gpu_at_dst")
        fd_after = fd_after_by_index.get(dst_index)
        native_matches_expected = values_close(native_gpu_at_dst, expected)
        writeback_matches_native = values_close(fd_after, native_gpu_at_dst)
        writeback_matches_expected = values_close(fd_after, expected)
        if not (
            finite_number(expected)
            and finite_number(native_gpu_at_dst)
            and finite_number(fd_after)
        ):
            sample_class = "mixed-or-inconclusive"
        elif native_matches_expected and writeback_matches_expected:
            sample_class = "pass"
        elif (not native_matches_expected) and writeback_matches_native:
            sample_class = "native-final-store-or-readback"
        elif native_matches_expected and (not writeback_matches_native):
            sample_class = "executor-final-writeback"
        else:
            sample_class = "mixed-or-inconclusive"
        class_counts[sample_class] = class_counts.get(sample_class, 0) + 1
        joined.append({
            "dst_index": dst_index,
            "expected": expected,
            "native_gpu_at_dst": native_gpu_at_dst,
            "fd_after_writeback": fd_after,
            "native_matches_expected": native_matches_expected,
            "writeback_matches_native": writeback_matches_native,
            "writeback_matches_expected": writeback_matches_expected,
            "sample_class": sample_class,
            "trace_writeback_verified": record.get("trace_writeback_verified"),
            "trace_writeback_mismatch": record.get("trace_writeback_mismatch"),
            "trace_writeback_mismatch_fields": record.get("trace_writeback_mismatch_fields"),
            "source_spirv_hash": q6_native_spirv_identity.get("source_spirv_hash"),
            "effective_spirv_hash": q6_native_spirv_identity.get("effective_spirv_hash"),
        })

    if not joined:
        summary = "inconclusive"
        reason = "no-joined-q6-layout-and-writeback-samples"
    elif class_counts["native-final-store-or-readback"] == len(joined):
        summary = "native-final-store-or-readback"
        reason = None
    elif class_counts["executor-final-writeback"] == len(joined):
        summary = "executor-final-writeback"
        reason = None
    elif class_counts["pass"] == len(joined):
        summary = "pass"
        reason = None
    else:
        summary = "inconclusive"
        reason = "mixed-sample-classes"
    result = {
        "summary": summary,
        "oracle_writeback": False,
        "joined_sample_count": len(joined),
        "class_counts": class_counts,
        "samples": joined[:32],
    }
    if reason:
        result["reason"] = reason
    return result


q6_native_vs_writeback_split = build_q6_native_vs_writeback_split()


def build_q6_final_store_boundary():
    """Join final-store trace records to output/writeback samples.

    This keeps the next device run fail-closed at a narrower boundary:
    - final-store value differs from the oracle, and writeback preserved it:
      native Q6 final-store/device execution.
    - final-store value matches the oracle, but post-writeback output differs:
      executor writeback.
    It is diagnostic-only and does not rewrite llama.cpp, shaders, prompts, or
    model files.
    """
    records = []
    for binding in q6_debug_u32_probe.get("bindings") or []:
        if not isinstance(binding, dict):
            continue
        for record in binding.get("records") or []:
            if not isinstance(record, dict):
                continue
            if (
                record.get("status") == "pass"
                and record.get("trace_status") == "pass"
                and record.get("trace_writeback_verified") is True
            ):
                records.append({
                    **record,
                    "binding": binding.get("binding"),
                    "set": binding.get("set"),
                })
    if not records:
        return {
            "schema": "pdocker.q6k.final-store-boundary.v1",
            "summary": "not-run",
            "reason": "missing-executed-final-store-trace",
            "joined_sample_count": 0,
            "class_counts": {},
            "samples": [],
        }

    layout_by_dst = {}
    layout_by_expected_store = {}
    if isinstance(q6_output_layout_samples, list):
        for sample in q6_output_layout_samples:
            if not isinstance(sample, dict):
                continue
            if isinstance(sample.get("dst_index"), int):
                layout_by_dst[int(sample["dst_index"])] = sample
            if isinstance(sample.get("expected_store_index"), int):
                layout_by_expected_store[int(sample["expected_store_index"])] = sample

    fd_after_by_index = {}
    for evidence in q6_row_indexed_writeback_evidence:
        if not isinstance(evidence, dict):
            continue
        fd_after_by_index.update(samples_by_index(evidence.get("f32_after_writeback")))

    joined = []
    class_counts = {
        "native-final-store-mismatch": 0,
        "executor-writeback-mismatch": 0,
        "pass": 0,
        "mixed-or-inconclusive": 0,
    }
    for record in records:
        output_index = record.get("output_index")
        if not isinstance(output_index, int):
            continue
        layout = layout_by_dst.get(output_index) or layout_by_expected_store.get(output_index)
        if not isinstance(layout, dict):
            continue
        expected = layout.get("expected")
        final_value = record.get("value_f32")
        fd_after = fd_after_by_index.get(output_index)
        expected_store_index = layout.get("expected_store_index")
        dst_index = layout.get("dst_index")
        final_matches_expected = values_close(final_value, expected)
        writeback_matches_final_store = values_close(fd_after, final_value)
        writeback_matches_expected = values_close(fd_after, expected)
        if not (
            finite_number(expected)
            and finite_number(final_value)
            and finite_number(fd_after)
        ):
            sample_class = "mixed-or-inconclusive"
        elif final_matches_expected and writeback_matches_expected:
            sample_class = "pass"
        elif (not final_matches_expected) and writeback_matches_final_store:
            sample_class = "native-final-store-mismatch"
        elif final_matches_expected and (not writeback_matches_final_store):
            sample_class = "executor-writeback-mismatch"
        else:
            sample_class = "mixed-or-inconclusive"
        class_counts[sample_class] = class_counts.get(sample_class, 0) + 1
        joined.append({
            "probe": record.get("probe"),
            "candidate_id": record.get("candidate_id"),
            "binding": record.get("binding"),
            "output_index": output_index,
            "expected_store_index": expected_store_index,
            "dst_index": dst_index,
            "final_store_value_f32": final_value,
            "expected": expected,
            "fd_after_writeback": fd_after,
            "final_store_matches_expected": final_matches_expected,
            "writeback_matches_final_store": writeback_matches_final_store,
            "writeback_matches_expected": writeback_matches_expected,
            "sample_class": sample_class,
            "trace_writeback_verified": record.get("trace_writeback_verified"),
            "trace_writeback_mismatch": record.get("trace_writeback_mismatch"),
            "trace_writeback_mismatch_fields": record.get("trace_writeback_mismatch_fields"),
            "source_spirv_hash": q6_native_spirv_identity.get("source_spirv_hash"),
            "effective_spirv_hash": q6_native_spirv_identity.get("effective_spirv_hash"),
        })

    if not joined:
        summary = "inconclusive"
        reason = "no-joined-final-store-layout-and-writeback-samples"
    elif class_counts["native-final-store-mismatch"] == len(joined):
        summary = "native-final-store-mismatch"
        reason = None
    elif class_counts["executor-writeback-mismatch"] == len(joined):
        summary = "executor-writeback-mismatch"
        reason = None
    elif class_counts["pass"] == len(joined):
        summary = "pass"
        reason = None
    else:
        summary = "inconclusive"
        reason = "mixed-sample-classes"
    result = {
        "schema": "pdocker.q6k.final-store-boundary.v1",
        "summary": summary,
        "joined_sample_count": len(joined),
        "class_counts": class_counts,
        "samples": joined[:32],
        "store_index_model_valid": q6_store_index_model_valid,
    }
    if reason:
        result["reason"] = reason
    return result


q6_final_store_boundary = build_q6_final_store_boundary()
q6_store_index_model_required = (
    q6_output_layout_probe_summary.startswith("canonical-mismatch")
    or q6_row_provenance_probe_summary == "other-row-match"
    or q6_partial_signature_probe_summary in {"local-y-partial", "lane-partial"}
    or q6_native_vs_writeback_split.get("summary") in {
        "executor-final-writeback",
        "native-final-store-or-readback",
    }
    or q6_final_store_boundary.get("summary") in {
        "executor-writeback-mismatch",
        "native-final-store-mismatch",
    }
)
q6_safe_kernel_used = q6_latest.get("q6k_safe_kernel") is True
q6_expected_local_size = [1, 1, 1] if q6_safe_kernel_used else [32, 1, 1]
q6_workgroup_shape_blocker = bool(
    q6_latest
    and (
        q6_latest.get("spirv_local_size_consistent") is False
        or q6_latest.get("spirv_local_size_resolved") != q6_expected_local_size
        or (
            isinstance(q6_latest_partial.get("q6_local_size"), list)
            and q6_latest_partial.get("q6_local_size") != q6_latest.get("spirv_local_size_resolved")
        )
    )
)
q6_local_size_resolved = q6_latest.get("spirv_local_size_resolved")
q6_shader_like_64_required = (not q6_safe_kernel_used) and q6_local_size_resolved != [32, 1, 1]
q6_shader_like_64_interpretation = (
    "diagnostic-only-for-q6k-safe-kernel; single-invocation replacement is an explicit bridge diagnostic"
    if q6_safe_kernel_used
    else
    "diagnostic-only-for-32x1x1-num-rows; constant_id=1 is NUM_ROWS, not WorkGroupSizeY"
    if not q6_shader_like_64_required
    else "required-for-non-32x1x1-local-size"
)
q6_shader_like_oracle_cleared = (
    q6_latest_oracle.get("status") == "mismatch"
    and numeric_close_to_zero(q6_latest_partial.get("q6_shader_like_abs_delta"))
    and (
        not q6_shader_like_64_required
        or numeric_close_to_zero(q6_latest_partial.get("q6_shader_like_64_abs_delta"))
    )
)
q6_shader_like_clear_basis = []
if numeric_close_to_zero(q6_latest_partial.get("q6_shader_like_abs_delta")):
    q6_shader_like_clear_basis.append("q6_shader_like_abs_delta")
if not q6_shader_like_64_required:
    if q6_safe_kernel_used:
        q6_shader_like_clear_basis.extend([
            "q6k_safe_kernel=true",
            "local_size_resolved=[1,1,1]",
            "q6_shader_like_64_abs_delta=diagnostic-only",
        ])
    else:
        q6_shader_like_clear_basis.extend([
            "local_size_resolved=[32,1,1]",
            "q6_shader_like_64_abs_delta=diagnostic-only",
        ])
elif numeric_close_to_zero(q6_latest_partial.get("q6_shader_like_64_abs_delta")):
    q6_shader_like_clear_basis.append("q6_shader_like_64_abs_delta")

q6_final_store_boundary["native_reduction_cleared"] = q6_shader_like_oracle_cleared


def classify_q6_output_index_probe(probe, native_reduction_cleared):
    samples = probe.get("samples") if isinstance(probe, dict) else None
    if not isinstance(samples, list) or not samples:
        return "not-run"
    mismatch_samples = [
        sample for sample in samples
        if isinstance(sample, dict) and sample.get("canonical_match") is not True
    ]
    if not mismatch_samples:
        return "canonical-match"
    found_elsewhere = [
        sample for sample in mismatch_samples
        if sample.get("found_elsewhere") is True
    ]
    if len(found_elsewhere) == len(mismatch_samples):
        in_window = [
            sample for sample in found_elsewhere
            if sample.get("best_index_in_store_window") is True
        ]
        if len(in_window) == len(found_elsewhere):
            deltas = {
                sample.get("best_store_row_delta")
                for sample in in_window
                if sample.get("best_store_row_delta") is not None
            }
            if len(deltas) == 1:
                return "fixed-offset"
            return "scatter"
        return "elsewhere-outside-store-window"
    if not found_elsewhere and native_reduction_cleared:
        return "final-store-value"
    if found_elsewhere:
        return "mixed-found-and-missing"
    return "inconclusive"


q6_output_index_probe_summary = classify_q6_output_index_probe(
    q6_output_layout_probe, q6_shader_like_oracle_cleared
)
q6_blocker_class = (
    "q6-probe-writeback-cleared-oracle-missing"
    if (not q6_oracle_events and q6_probe_events and q6_writeback_verified_all)
    else "q6-oracle-capture-missing"
    if not q6_oracle_events and q6_dispatch_seen
    else "not-reached"
    if not q6_oracle_events
    else "workgroup-shape"
    if q6_workgroup_shape_blocker
    else "cleared"
    if q6_latest_oracle.get("status") == "match"
    else "descriptor-effective-range-or-upload"
    if q6_readonly_upload_hash_mismatches or q6_descriptor_range_mismatches or q6_descriptor_invariant_mismatches
    else q6_debug_u32_probe_blocker
    if q6_debug_u32_probe_blocker
    else "shader-readonly-mutation-or-barrier-scope"
    if q6_unexpected_readonly_dispatch_mutations
    else "writeback"
    if q6_writable_writeback_mismatches
    else "q6-store-index-model-incomplete"
    if q6_store_index_model_required and not q6_store_index_model_valid
    else "executor-final-writeback"
    if q6_native_vs_writeback_split.get("summary") == "executor-final-writeback"
    else "native-q6-final-store-or-readback"
    if q6_native_vs_writeback_split.get("summary") == "native-final-store-or-readback"
    else "native-q6-output-layout"
    if q6_output_layout_probe_summary == "canonical-mismatch-found-elsewhere" and q6_writeback_verified_all and q6_store_index_model_valid
    else "native-q6-other-row-output-layout"
    if q6_row_provenance_probe_summary == "other-row-match" and q6_writeback_verified_all and q6_store_index_model_valid
    else "native-q6-local-y-partial-store"
    if q6_partial_signature_probe_summary == "local-y-partial" and q6_writeback_verified_all
    else "native-q6-lane-partial-store"
    if q6_partial_signature_probe_summary == "lane-partial" and q6_writeback_verified_all
    else "native-q6-device-execution-or-final-store"
    if q6_output_layout_fixed_offset_rejected and q6_shader_like_oracle_cleared and q6_writeback_verified_all and q6_store_index_model_valid
    else "native-q6-output-layout-inconclusive"
    if q6_output_layout_probe_summary == "canonical-mismatch-inconclusive" and q6_writeback_verified_all and q6_store_index_model_valid
    else "native-q6-reduction-or-device-execution"
    if (
        q6_output_layout_probe_summary == "canonical-mismatch-not-found"
        and q6_shader_like_oracle_cleared
        and q6_writeback_verified_all
    )
    else "vulkan-device-execution"
    if q6_shader_like_oracle_cleared and q6_writeback_verified_all
    else "vulkan-device-execution-or-writeback"
    if q6_shader_like_oracle_cleared
    else "q6-arithmetic-reduction-or-output-layout"
    if q6_latest_oracle.get("status") == "mismatch"
    else "inconclusive"
)
q6_workgroup_diagnostics = {
    "event_count": len(q6_oracle_events),
    "q6_probe_event_count": len(q6_probe_events),
    "q6_dispatch_seen": q6_dispatch_seen,
    "q6_dispatch_event_count": len(q6_valid_spirv_events) + len(q6_dispatch_lifecycle_events),
    "q6_dispatch_latest": {
        "spirv_hash": q6_latest_dispatch_event.get("spirv_hash"),
        "dispatch": q6_latest_dispatch_event.get("dispatch"),
        "dispatch_id": q6_latest_dispatch_event.get("dispatch_id"),
        "source": (
            "executor-response"
            if q6_valid_spirv_events
            else "dispatch-lifecycle"
            if q6_dispatch_lifecycle_events
            else None
        ),
        "has_cpu_oracle": isinstance(q6_latest_dispatch_event.get("cpu_oracle"), dict),
    },
    "q6_oracle_capture_missing": bool(q6_dispatch_seen and not q6_oracle_events),
    "latest_spirv_hash": q6_latest.get("spirv_hash"),
    "latest_status": q6_latest_oracle.get("status"),
    "latest_mismatch_count": q6_latest_oracle.get("mismatch_count"),
    "q6k_safe_kernel": q6_safe_kernel_used,
    "expected_local_size": q6_expected_local_size,
    "local_size": q6_latest.get("spirv_local_size"),
    "local_size_resolved": q6_latest.get("spirv_local_size_resolved"),
    "local_size_consistent": q6_latest.get("spirv_local_size_consistent"),
    "q6_local_size": q6_latest_partial.get("q6_local_size"),
    "q6_local_invocations": q6_latest_partial.get("q6_local_invocations"),
    "q6_accum_mask": q6_latest_partial.get("q6_accum_mask"),
    "q6_base_work_group_y": q6_latest_partial.get("q6_base_work_group_y"),
    "q6_output_base_index": q6_latest_partial.get("q6_output_base_index"),
    "q6_stride_d": q6_latest_partial.get("q6_stride_d"),
    "q6_batch_stride_d": q6_latest_partial.get("q6_batch_stride_d"),
    "q6_dispatch_groups": q6_latest_partial.get("q6_dispatch_groups"),
    "q6_block_size": q6_latest_partial.get("q6_block_size"),
    "q6_num_rows": q6_latest_partial.get("q6_num_rows"),
    "q6_num_cols": q6_latest_partial.get("q6_num_cols"),
    "q6_store_index_model_valid": q6_store_index_model_valid,
    "q6_store_index_sampled_nonzero_j": q6_latest_partial.get("q6_store_index_sampled_nonzero_j"),
    "q6_store_index_sampled_nonzero_y": q6_latest_partial.get("q6_store_index_sampled_nonzero_y"),
    "q6_store_index_full_coverage": q6_latest_partial.get("q6_store_index_full_coverage"),
    "q6_store_window_begin": q6_latest_partial.get("q6_store_window_begin"),
    "q6_store_window_end": q6_latest_partial.get("q6_store_window_end"),
    "q6_weight_base_blocks": q6_latest_partial.get("q6_weight_base_blocks"),
    "q6_accumulator_sum": q6_latest_partial.get("q6_accumulator_sum"),
    "q6_shader_like_abs_delta": q6_latest_partial.get("q6_shader_like_abs_delta"),
    "q6_shader_like_64_abs_delta": q6_latest_partial.get("q6_shader_like_64_abs_delta"),
    "q6_native_reduction_tree_sum": q6_latest_partial.get("q6_native_reduction_tree_sum"),
    "q6_native_reduction_tree_abs_delta": q6_latest_partial.get("q6_native_reduction_tree_abs_delta"),
    "q6_native_reduction_tree_gpu_abs_error": q6_latest_partial.get("q6_native_reduction_tree_gpu_abs_error"),
    "q6_native_spirv_identity": q6_native_spirv_identity,
    "q6_native_vs_writeback_split": q6_native_vs_writeback_split,
    "q6_final_store_boundary": q6_final_store_boundary,
    "q6_debug_u32_probe": q6_debug_u32_probe,
    "q6_debug_u32_probe_blocker": q6_debug_u32_probe_blocker,
    "q6_output_layout_probe": q6_output_layout_probe,
    "q6_output_layout_probe_summary": q6_output_layout_probe_summary,
    "q6_output_index_probe_summary": q6_output_index_probe_summary,
    "q6_output_layout_fixed_offset_rejected": q6_output_layout_fixed_offset_rejected,
    "q6_row_provenance_probe": q6_row_provenance_probe,
    "q6_row_provenance_probe_summary": q6_row_provenance_probe_summary,
    "q6_partial_signature_probe": q6_partial_signature_probe,
    "q6_partial_signature_probe_summary": q6_partial_signature_probe_summary,
    "q6_shader_like_oracle_cleared": q6_shader_like_oracle_cleared,
    "q6_shader_like_64_required": q6_shader_like_64_required,
    "q6_shader_like_clear_basis": q6_shader_like_clear_basis,
    "q6_shader_like_64_interpretation": q6_shader_like_64_interpretation,
    "q6_first_mismatch": q6_first_mismatch,
    "q6_row_indexed_sample_indices": q6_oracle_row_indexed_sample_indices[:48],
    "q6_row_indexed_writeback_evidence": q6_row_indexed_writeback_evidence[:8],
    "q6_row_indexed_writeback_verified": q6_row_indexed_writeback_verified,
    "q6_writable_bindings": q6_writable_binding_details[:8],
    "q6_readonly_upload_hash_mismatches": q6_readonly_upload_hash_mismatches[:8],
    "q6_readonly_dispatch_mutations": q6_readonly_dispatch_mutations[:8],
    "q6_readonly_dispatch_alias_side_effects": q6_readonly_dispatch_alias_side_effects[:8],
    "q6_unexpected_readonly_dispatch_mutations": q6_unexpected_readonly_dispatch_mutations[:8],
    "q6_descriptor_range_mismatches": q6_descriptor_range_mismatches[:8],
    "q6_descriptor_invariant_mismatches": q6_descriptor_invariant_mismatches[:8],
    "q6_writable_writeback_mismatches": q6_writable_writeback_mismatches[:8],
    "q6_writable_writeback_unknown": q6_writable_writeback_unknown[:8],
    "q6_writeback_verified_all": q6_writeback_verified_all,
    "workgroup_shape_blocker": q6_workgroup_shape_blocker,
    "blocker_class": q6_blocker_class,
    "diagnostic_interpretation": (
        "q6-probe-writeback-cleared-but-source-oracle-not-available-for-instrumented-module"
        if q6_blocker_class == "q6-probe-writeback-cleared-oracle-missing"
        else
        "q6-dispatch-seen-without-oracle-response"
        if not q6_oracle_events and q6_dispatch_seen
        else "no-q6-oracle-event"
        if not q6_oracle_events
        else "workgroup-shape-inconsistent"
        if q6_workgroup_shape_blocker
        else "q6-oracle-matches"
        if q6_latest_oracle.get("status") == "match"
        else "q6-mismatch-at-%s-boundary" % q6_blocker_class
        if q6_latest_oracle.get("status") == "mismatch"
        else "q6-inconclusive"
    ),
}
generic_spirv_dispatch["pre_q6_failure"] = compact_pre_q6_failure(
    generic_spirv_dispatch,
    q6_workgroup_diagnostics,
)
diagnostic_bisection = {
    "method": "binary-search fault isolation over API, graph, ICD, executor, and readback boundaries",
    "nodes": [
        {
            "id": "api_cpu_baseline",
            "question": "Does the same model and server API produce deterministic CPU/no-offload answers?",
            "state": "pass" if cpu_correctness.get("summary", {}).get("correctness") == "pass" else "fail" if cpu_correctness else "not-run",
            "routes": {"pass": "gpu_server_output", "fail": "llama_api_or_model_input"},
        },
        {
            "id": "gpu_server_output",
            "question": "Does GPU/offload output match CPU/no-offload at the HTTP completion boundary?",
            "state": "pass" if differential_correctness.get("summary") == "pass" else "fail" if differential_correctness.get("summary") == "fail" else "not-run",
            "routes": {"pass": "performance_only", "fail": "token_probability_boundary"},
        },
        {
            "id": "service_prompt_sanity",
            "question": "Does the forced Vulkan service return the required deterministic API prompt before any deeper oracle evidence is considered?",
            "state": service_prompt_sanity["summary"],
            "routes": {
                "pass": "gpu_server_output",
                "fail": "numeric_layout_or_readback",
                "not-run": "collect_service_boundary_evidence",
            },
        },
        {
            "id": "token_probability_boundary",
            "question": "Do CPU and GPU agree on selected/top token probabilities before sampling policy can hide the error?",
            "state": "pass" if differential_probabilities.get("summary") == "pass" else "fail" if differential_probabilities.get("summary") == "fail" else "not-run",
            "routes": {"pass": "sampler_or_decoding", "fail": "logits_or_final_projection"},
        },
        {
            "id": "executor_dispatch_boundary",
            "question": "Did generic SPIR-V reach the Android Vulkan executor and complete successfully?",
            "state": "pass" if valid_spirv_events else "fail" if generic_spirv_attempted else "not-reached",
            "routes": {"pass": "post_dispatch_logits", "fail": "icd_or_executor_submit"},
        },
        {
            "id": "post_dispatch_logits",
            "question": "Does the largest/final-projection-like writable binding contain finite float samples after dispatch?",
            "state": "pass" if finite_f32_sample_count else "not-instrumented" if final_projection_candidate else "not-reached",
            "routes": {"pass": "numeric_layout_or_readback", "not-instrumented": "enable_f32_samples", "not-reached": "dispatch_boundary"},
        },
        {
            "id": "readonly_input_integrity",
            "question": "Do non-aliased read-only descriptor bindings match immediately after upload, then remain stable through dispatch?",
            "state": (
                "upload-fail"
                if primary_readonly_upload_hash_mismatches
                else "dispatch-mutated"
                if primary_readonly_dispatch_mutations
                else "pass"
                if valid_spirv_events
                else "not-reached"
            ),
            "routes": {
                "pass": "output_layout_or_shader_math",
                "upload-fail": "upload_offset_descriptor_or_hash_scope",
                "dispatch-mutated": "shader_access_or_barrier_scope",
            },
        },
        {
            "id": "q6_workgroup_shape",
            "question": "Does Q6_K execute with the same three-dimensional local size that llama.cpp specialized?",
            "state": (
                "inconsistent"
                if q6_workgroup_diagnostics["workgroup_shape_blocker"]
                else "match"
                if q6_workgroup_diagnostics["latest_status"] == "match"
                else "mismatch"
                if q6_workgroup_diagnostics["latest_status"] == "mismatch"
                else "not-reached"
            ),
            "routes": {
                "inconsistent": "fix_local_size_materialization",
                "match": "next_ngl_shader_or_performance",
                "mismatch": "q6_descriptor_memory_or_math",
            },
        },
        {
            "id": "env_propagation",
            "question": "Did requested bridge tuning environment variables reach the executor as dispatch evidence?",
            "state": config_propagation["summary"],
            "routes": {"pass": "trust_tuning_experiments", "fail": "fix_icd_to_executor_option_transport_first"},
        },
    ],
    "current_focus": (
        "upload_offset_descriptor_or_hash_scope"
        if primary_readonly_upload_hash_mismatches
        else "shader_access_or_barrier_scope"
        if primary_readonly_dispatch_mutations
        else "output_layout_or_shader_math"
        if finite_f32_sample_count and gpu_correctness_summary == "fail"
        else
        "numeric_layout_or_readback"
        if service_prompt_sanity["summary"] == "fail"
        else "numeric_layout_or_readback"
        if finite_f32_sample_count and differential_probabilities.get("summary") == "fail"
        else "icd_or_executor_submit"
        if generic_spirv_attempted and not valid_spirv_events
        else "llama_api_or_model_input"
        if cpu_correctness and cpu_correctness.get("summary", {}).get("correctness") != "pass"
        else "config_propagation"
        if config_propagation["summary"] == "fail"
        else "collect_more_boundaries"
    ),
    "finite_f32_sample_count": finite_f32_sample_count,
    "readonly_binding_hash_mismatch_count": len(readonly_binding_hash_mismatches),
    "readonly_binding_hash_mismatches": readonly_binding_hash_mismatches[-16:],
    "primary_readonly_upload_hash_mismatch_count": len(primary_readonly_upload_hash_mismatches),
    "primary_readonly_upload_hash_mismatches": primary_readonly_upload_hash_mismatches[-16:],
    "primary_readonly_dispatch_mutation_count": len(primary_readonly_dispatch_mutations),
    "primary_readonly_dispatch_mutations": primary_readonly_dispatch_mutations[-16:],
    "final_projection_candidate": {
        "spirv_hash": final_projection_candidate.get("spirv_hash"),
        "dispatch": final_projection_candidate.get("dispatch"),
        "push_bytes": final_projection_candidate.get("push_bytes"),
        "binding_count": final_projection_candidate.get("bindings"),
    } if final_projection_candidate else {},
    "q6_workgroup_diagnostics": q6_workgroup_diagnostics,
    "service_prompt_sanity": service_prompt_sanity,
}
failure_axes = {
    "advertised_limits": {
        "state": (
            "suspect"
            if queue_submit_blocker and (android_feature_trace or spirv_traces)
            else "untraced"
            if queue_submit_blocker
            else "not_front_blocker"
        ),
        "reason": (
            "Android vkQueueSubmit rejected the generic SPIR-V dispatch after ICD-advertised Vulkan features/limits were accepted"
            if queue_submit_blocker and (android_feature_trace or spirv_traces)
            else "queue submit failed before Android feature/SPIR-V trace was captured"
            if queue_submit_blocker
            else "no feature-limit submit failure in this run"
        ),
    },
    "chunking": {
        "state": (
            "pressure"
            if chunking_pressure["allocation_near_clamp"] or chunking_pressure["model_cpu_mapped_exceeds_bridge_clamp"]
            else "not_observed"
        ),
        "reason": (
            "large model/buffer allocations are at or above the 512MiB bridge clamp; future failures should distinguish splitting/chunk transport from shader lowering"
            if chunking_pressure["allocation_near_clamp"] or chunking_pressure["model_cpu_mapped_exceeds_bridge_clamp"]
            else "no allocation/chunking pressure found in the captured log"
        ),
    },
    "generic_spirv_dispatch": {
        "state": "front_blocker" if generic_spirv_dispatch_blocker or pipeline_feature_blocker else "passed" if generic_spirv_dispatch["valid_android_vulkan_events"] else "not_reached",
        "reason": blocker_detail if generic_spirv_dispatch_blocker or pipeline_feature_blocker else "generic SPIR-V dispatch was not the failing axis in this run",
    },
}
dispatch_upload_ms = [float(m.group(1)) for m in re.finditer(r'"upload_ms":([0-9.]+)', log)]
dispatch_ms = [float(m.group(1)) for m in re.finditer(r'"dispatch_ms":([0-9.]+)', log)]
dispatch_download_ms = [float(m.group(1)) for m in re.finditer(r'"download_ms":([0-9.]+)', log)]
copy_buffer_bytes = [
    int(m.group(1))
    for m in re.finditer(r"pdocker-vulkan-icd: copy-buffer .* bytes=([0-9]+) ok=1", log)
]
copy_submit_summaries = [
    {
        "ops": int(m.group(1)),
        "alias_ops": int(m.group(2)),
        "memmove_ops": int(m.group(3)),
        "skipped_ops": int(m.group(4)),
        "alias_bytes": int(m.group(5)),
        "memmove_bytes": int(m.group(6)),
        "skipped_bytes": int(m.group(7)),
    }
    for m in re.finditer(
        r"pdocker-vulkan-icd: copy-submit summary ops=([0-9]+) alias_ops=([0-9]+) memmove_ops=([0-9]+) skipped_ops=([0-9]+) alias_bytes=([0-9]+) memmove_bytes=([0-9]+) skipped_bytes=([0-9]+)",
        log,
    )
]
guarded_bindings = [
    {
        "binding": int(m.group(1)),
        "range": int(m.group(2)),
        "allocation": int(m.group(3)),
        "resident_bytes": int(m.group(4)),
        "dirty_bytes": int(m.group(5)),
    }
    for m in re.finditer(
        r"pdocker-vulkan-icd: guarded-binding binding=([0-9]+) offset=[0-9]+ range=([0-9]+) allocation=([0-9]+) page_size=[0-9]+ resident_pages=[0-9]+ dirty_pages=[0-9]+ resident_bytes=([0-9]+) dirty_bytes=([0-9]+)",
        log,
    )
]
binding_timing_samples = []
for event_index, event in enumerate(executor_events):
    if event.get("kernel") != "generic_spirv" or event.get("valid") is not True:
        continue
    for detail in event.get("binding_details") or []:
        if not isinstance(detail, dict):
            continue
        binding_timing_samples.append({
            "event_index": event_index,
            "binding": int(detail.get("binding") or 0),
            "size": int(detail.get("size") or 0),
            "readable": bool(detail.get("readable")),
            "writable": bool(detail.get("writable")),
            "resident": bool(detail.get("resident")),
            "cache_hit": bool(detail.get("cache_hit")),
            "mutable_reused": bool(detail.get("mutable_reused")),
            "mutable_cache_hit": bool(detail.get("mutable_cache_hit")),
            "upload_ms": float(detail.get("upload_ms") or 0.0),
            "download_ms": float(detail.get("download_ms") or 0.0),
            "dirty_probe_pages": int(detail.get("dirty_probe_pages") or 0),
            "dirty_probe_bytes": int(detail.get("dirty_probe_bytes") or 0),
            "dirty_probe_ms": float(detail.get("dirty_probe_ms") or 0.0),
            "dirty_writeback_cached": bool(detail.get("dirty_writeback_cached")),
            "dirty_writeback_bytes": int(detail.get("dirty_writeback_bytes") or 0),
        })
top_binding_uploads = sorted(
    binding_timing_samples,
    key=lambda item: item["upload_ms"],
    reverse=True,
)[:8]
top_binding_downloads = sorted(
    binding_timing_samples,
    key=lambda item: item["download_ms"],
    reverse=True,
)[:8]
top_dirty_probe_bindings = sorted(
    [item for item in binding_timing_samples if item["dirty_probe_pages"] > 0],
    key=lambda item: item["dirty_probe_bytes"],
    reverse=True,
)[:8]
bridge_dispatch_profile = {
    "samples": len(dispatch_ms),
    "upload_ms_mean": (sum(dispatch_upload_ms) / len(dispatch_upload_ms)) if dispatch_upload_ms else 0.0,
    "dispatch_ms_mean": (sum(dispatch_ms) / len(dispatch_ms)) if dispatch_ms else 0.0,
    "download_ms_mean": (sum(dispatch_download_ms) / len(dispatch_download_ms)) if dispatch_download_ms else 0.0,
    "copy_buffer_ops_in_log": len(copy_buffer_bytes),
    "copy_buffer_bytes_in_log": sum(copy_buffer_bytes),
    "copy_submit_count": len(copy_submit_summaries),
    "copy_submit_ops": sum(item["ops"] for item in copy_submit_summaries),
    "copy_submit_alias_ops": sum(item["alias_ops"] for item in copy_submit_summaries),
    "copy_submit_memmove_ops": sum(item["memmove_ops"] for item in copy_submit_summaries),
    "copy_submit_skipped_ops": sum(item["skipped_ops"] for item in copy_submit_summaries),
    "copy_submit_alias_bytes": sum(item["alias_bytes"] for item in copy_submit_summaries),
    "copy_submit_memmove_bytes": sum(item["memmove_bytes"] for item in copy_submit_summaries),
    "copy_submit_skipped_bytes": sum(item["skipped_bytes"] for item in copy_submit_summaries),
    "guarded_binding_samples": len(guarded_bindings),
    "guarded_binding_max_resident_bytes": max((item["resident_bytes"] for item in guarded_bindings), default=0),
    "guarded_binding_max_dirty_bytes": max((item["dirty_bytes"] for item in guarded_bindings), default=0),
    "guarded_binding_max_range_bytes": max((item["range"] for item in guarded_bindings), default=0),
    "binding_timing_samples": len(binding_timing_samples),
    "binding_upload_ms_max": max((item["upload_ms"] for item in binding_timing_samples), default=0.0),
    "binding_download_ms_max": max((item["download_ms"] for item in binding_timing_samples), default=0.0),
    "dirty_probe_binding_samples": sum(1 for item in binding_timing_samples if item["dirty_probe_pages"] > 0),
    "dirty_probe_max_bytes": max((item["dirty_probe_bytes"] for item in binding_timing_samples), default=0),
    "dirty_probe_total_bytes": sum(item["dirty_probe_bytes"] for item in binding_timing_samples),
    "dirty_probe_ms_max": max((item["dirty_probe_ms"] for item in binding_timing_samples), default=0.0),
    "dirty_writeback_cached_samples": sum(1 for item in binding_timing_samples if item["dirty_writeback_cached"]),
    "dirty_writeback_total_bytes": sum(item["dirty_writeback_bytes"] for item in binding_timing_samples),
    "top_binding_uploads": top_binding_uploads,
    "top_binding_downloads": top_binding_downloads,
    "top_dirty_probe_bindings": top_dirty_probe_bindings,
}
speedup = (gpu_tps / cpu_tps) if cpu_tps and gpu_tps else 0.0
target_met = bool(cpu_tps and gpu_tps >= target_tps)
next_action = (
    "free Android memory or lower the llama runtime memory footprint, then rerun; the watchdog stopped before LMK/OOM"
    if runtime_abort
    else
    "fix Q6_K local-size/NUM_ROWS separation before interpreting numeric mismatch"
    if q6_workgroup_diagnostics["workgroup_shape_blocker"]
    else
    "continue Q6_K strict-passthrough split at the %s boundary" % q6_workgroup_diagnostics["blocker_class"]
    if q6_workgroup_diagnostics["latest_status"] == "mismatch"
    else
    "fix Vulkan buffer base/range accounting for scheduler warmup"
    if evidence.get("buffer_range_assert_blocker")
    else
    "split 4GiB+ Vulkan buffers / pinned host-buffer path"
    if evidence.get("buffer_allocation_blocker") or evidence.get("assert_blocker")
    else "map failed SPIR-V capabilities to Android Vulkan feature bits, then clamp or translate the advertised feature set"
    if blocker_class == "vulkan_pipeline_feature"
    else "inspect ICD/executor dispatch begin/end evidence; liveness passed but /completion timed out before a benchmarkable token"
    if blocker_class == "llama_completion_timeout"
    else "inspect GPU numeric/layout/readback evidence; liveness and /completion returned but the deterministic prompt result was wrong"
    if blocker_class == "llama_completion_wrong_output"
    else "lower generic SPIR-V dispatch into the Android Vulkan executor or clamp advertised capabilities"
    if blocker_class in {"vulkan_generic_spirv_dispatch", "vulkan_queue_submit_feature"}
    else "inspect traced Android Vulkan feature/SPIR-V mismatch"
    if evidence.get("android_vulkan_dispatch_blocker") and evidence.get("executor_spirv_trace_seen")
    else "clamp or translate llama.cpp storage8/int8 final-projection shaders before accepting performance results"
    if blocker_class == "vulkan_feature_mismatch"
    else "lower llama.cpp SPIR-V dispatch into the Android GPU executor"
    if evidence.get("spirv_dispatch_blocker") or evidence.get("queue_submit_blocker")
    else "trace final-projection descriptor aliases and feature requirements until GPU output matches CPU/no-offload"
    if blocker_class == "gpu_correctness_mismatch"
    else "treat n-gpu-layers as an insufficient isolation knob; bisect by first generic SPIR-V shader hash under Vulkan mode"
    if blocker_class == "vulkan_backend_control_mismatch"
    else "increase n-gpu-layers until repeating transformer layers are offloaded"
    if blocker_class == "insufficient_gpu_offload_depth"
    else "reduce bridge upload/copy overhead with persistent registered buffers; rerun with larger n_predict"
    if blocker_class == "bridge_dispatch_performance"
    else "make Vulkan device discovery reliable"
)
if blocker_class == "bridge_dispatch_performance":
    blocker_detail = "served through generic SPIR-V, but bridge upload/copy overhead keeps GPU below CPU throughput"
if runtime_abort:
    blocker_detail = runtime_abort.get("next_blocker") or "runtime memory pressure stopped the llama GPU attempt before Android LMK/OOM"
    blocker_class = "runtime_memory_pressure"
result = {
    "schema": "pdocker.llama.gpu.compare.v1",
    "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "policy": {
        "llama_cpp_modified": False,
        "gpu_entry": "standard Vulkan loader through the Skydnir Vulkan ICD",
        "target_speedup": 10.0,
    },
    "settings": {
        "gpu_layers": int(gpu_layers),
        "gpu_ctx": int(gpu_ctx),
        "predict": int(predict),
        "repeat": int(repeat),
        "warmup_discard": int(warmup_discard),
        "gpu_summary_scope": gpu.get("summary_scope", "all_runs"),
        "trace_alloc": bool(int(trace_alloc)),
        "cpu_reused": bool(cpu.get("reused_cpu_baseline")),
        "model_path": model_path,
        "model_url_set": bool(model_url),
    },
    "cpu": {
        "tokens_per_second": cpu_tps,
        "summary": cpu.get("summary", {}),
        "correctness": cpu_correctness,
    },
    "gpu": {
        "tokens_per_second": gpu_tps,
        "summary": gpu.get("summary", {}),
        "served": bool(int(gpu_served_s)),
        "state_excerpt": state[:2000],
        "log_excerpt": log[-12000:],
        "runtime_env": effective_runtime_env,
        "runtime_env_manifest": runtime_env_manifest,
        "container_config_env": runtime_env,
        "planned_container_env": planned_env,
        "startup_diagnostics": startup_diagnostics,
        "service_readiness": service_readiness,
        "post_readiness_memory": post_readiness_memory,
        "evidence": evidence,
        "runtime_abort": runtime_abort,
        "allocation_trace_bytes": allocations[-32:],
        "bridge_dispatch_profile": bridge_dispatch_profile,
        "diagnostics": {
            "blocker_class": blocker_class,
            "blocker_detail": blocker_detail,
            "dispatch_lifecycle": dispatch_lifecycle,
            "failure_axes": failure_axes,
            "advertised_limits": advertised_limits,
            "chunking_pressure": chunking_pressure,
            "generic_spirv_dispatch": generic_spirv_dispatch,
            "executor_backends": executor_backends,
            "executor_errors": executor_errors,
            "spirv_hashes": spirv_hashes[-4:],
            "runtime_freshness": runtime_freshness,
            "config_propagation": config_propagation,
            "spirv_probe_env_audit": spirv_probe_env_audit,
            "api_executor_reconciliation": api_executor_reconciliation,
            "api_understanding": api_understanding,
            "service_prompt_sanity": service_prompt_sanity,
            "diagnostic_bisection": diagnostic_bisection,
            "q6_workgroup_diagnostics": q6_workgroup_diagnostics,
        },
        "correctness": correctness,
    },
    "differential_correctness": differential_correctness,
    "differential_probabilities": differential_probabilities,
    "comparison": {
        "speedup": speedup,
        "target_tokens_per_second": target_tps,
        "target_met": target_met,
    },
    "bridge_overhead_phase": {
        "phase": (
            "served_but_transfer_bound"
            if blocker_class == "bridge_dispatch_performance"
            else "served_output_only_offload"
            if blocker_class == "insufficient_gpu_offload_depth"
            else "served_without_bridge_profile"
            if bool(int(gpu_served_s))
            else "not_yet_served"
        ),
        "served": bool(int(gpu_served_s)),
        "gpu_layers": int(gpu_layers),
        "cpu_tokens_per_second": cpu_tps,
        "gpu_tokens_per_second": gpu_tps,
        "speedup": speedup,
        "target_speedup": 10.0,
        "target_tokens_per_second": target_tps,
        "target_met": target_met,
        "blocker": blocker_detail,
        "next_action": next_action,
        "bridge_dispatch_profile": bridge_dispatch_profile,
    },
    "operation": {
        "kind": "llama-gpu-compare",
        "ui_surface": "Overview daemon operation/progress card",
        "container_surface": "skydnir-llama-cpp remains the container shown by Engine container listing",
        "cleanup": "remove adb port forward and mark failed operation on nonzero exit; CPU restore is opt-in with --restore because the next run recreates the required mode",
    },
    "next_blocker": blocker_detail,
    "next_action": next_action,
}
Path(out_path).write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(json.dumps(result["comparison"], indent=2))
print("next_blocker:", result["next_blocker"])
PY
COMPARE_RESULT_READY=1

SUMMARY="$(python3 - "$OUT" <<'PY'
import json
import sys

d = json.load(open(sys.argv[1], encoding="utf-8"))
print(
    f"CPU {d['cpu']['tokens_per_second']:.3f} tok/s; "
    f"GPU {d['gpu']['tokens_per_second']:.3f} tok/s; "
    f"GPU served={str(d['gpu']['served']).lower()}; "
    f"speedup {d['comparison']['speedup']:.2f}x; "
    f"target_met={str(d['comparison']['target_met']).lower()}; "
    f"gpu_layers={d['settings']['gpu_layers']}; "
    f"correctness={d['gpu'].get('correctness', {}).get('summary', {}).get('correctness', 'not-run')}; "
    f"next: {d['next_blocker']}"
)
PY
)"
operation_notify "running" "$SUMMARY"

DEVICE_NAME="$(basename "$OUT")"
"$ADB" push "$OUT" "/data/local/tmp/$DEVICE_NAME" >/dev/null || true
run_as "mkdir -p files/pdocker/bench && cp /data/local/tmp/$DEVICE_NAME files/pdocker/bench/$DEVICE_NAME" || true
if [[ -s "$CORRECTNESS_JSON" ]]; then
  CORRECTNESS_DEVICE_NAME="llama-correctness-$(date -u +%Y%m%dT%H%M%SZ).json"
  "$ADB" push "$CORRECTNESS_JSON" "/data/local/tmp/$CORRECTNESS_DEVICE_NAME" >/dev/null || true
  run_as "mkdir -p files/pdocker/bench && cp /data/local/tmp/$CORRECTNESS_DEVICE_NAME files/pdocker/bench/$CORRECTNESS_DEVICE_NAME" || true
fi

if [[ "$RESTORE_CPU" -eq 1 ]]; then
  echo "[pdocker llama compare] restore CPU server"
  CURRENT_STAGE="restore CPU server"
  operation_notify "running" "Restoring CPU llama server"
  if start_cpu >/dev/null; then
    wait_server "$CPU_RESTORE_WAIT_SERVER_TIMEOUT_SEC" "CPU restore" >/dev/null || true
  else
    operation_notify "failed" "$SUMMARY; CPU restore failed; compare artifact preserved" 1
    echo "[pdocker llama compare] CPU restore failed; compare artifact preserved" >&2
  fi
fi

CURRENT_STAGE="complete"
if [[ "$RESTORE_CPU" -eq 1 ]]; then
  operation_notify "done" "$SUMMARY; CPU server restored" 1
else
  operation_notify "done" "$SUMMARY; last compare mode left running; next run recreates its required container" 1
fi
echo "[pdocker llama compare] local: $OUT"
echo "[pdocker llama compare] device: files/pdocker/bench/$DEVICE_NAME"

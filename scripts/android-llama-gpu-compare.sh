#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ADB="${ADB:-adb}"
PKG="${PDOCKER_PACKAGE:-io.github.ryo100794.pdocker.compat}"
CLASS_PREFIX="io.github.ryo100794.pdocker"
ACTION_PREFIX="io.github.ryo100794.pdocker"
CONTAINER="${PDOCKER_LLAMA_CONTAINER:-pdocker-llama-cpp}"
IMAGE="${PDOCKER_LLAMA_IMAGE:-pdocker/llama-cpp-gpu:latest}"
PROJECT="${PDOCKER_LLAMA_PROJECT:-files/pdocker/projects/llama-cpp-gpu}"
LOCAL_PORT="${PDOCKER_LLAMA_LOCAL_PORT:-28081}"
REMOTE_PORT="${PDOCKER_LLAMA_REMOTE_PORT:-18081}"
CPU_CTX="${PDOCKER_LLAMA_CPU_CTX:-2048}"
GPU_CTX="${PDOCKER_LLAMA_GPU_CTX:-512}"
GPU_LAYERS="${PDOCKER_LLAMA_GPU_LAYERS:-2}"
PREDICT="${PDOCKER_LLAMA_BENCH_PREDICT:-4}"
REPEAT="${PDOCKER_LLAMA_BENCH_REPEAT:-1}"
OUT="${PDOCKER_LLAMA_COMPARE_OUT:-$ROOT/docs/test/llama-gpu-compare-latest.json}"
MODEL_PATH="${PDOCKER_LLAMA_MODEL_PATH:-/models/model.gguf}"
MODEL_URL="${PDOCKER_LLAMA_MODEL_URL:-https://huggingface.co/Qwen/Qwen3-8B-GGUF/resolve/main/Qwen3-8B-Q4_K_M.gguf}"
RESTORE_CPU=0
RUN_CPU=1
CPU_TPS_OVERRIDE="${PDOCKER_LLAMA_CPU_TPS:-}"
TRACE_ALLOC="${PDOCKER_LLAMA_TRACE_ALLOC:-0}"
OP_ID="llama-gpu-compare-$(date -u +%Y%m%dT%H%M%SZ)-$$"
CURRENT_CONTAINER_ID=""

usage() {
  cat <<EOF
Usage: $0 [--out PATH] [--gpu-layers N] [--gpu-ctx N] [--cpu-ctx N] [--predict N] [--repeat N] [--model-path PATH] [--model-url URL] [--gpu-only] [--cpu-tps N] [--trace-alloc] [--restore] [--no-restore]

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

cleanup() {
  local status="$?"
  if [[ "$status" -ne 0 ]]; then
    operation_notify "failed" "$CURRENT_STAGE failed with exit code $status" 1 >/dev/null 2>&1 || true
  fi
  rm -rf "$TMP"
  "$ADB" forward --remove "tcp:$LOCAL_PORT" >/dev/null 2>&1 || true
}
trap cleanup EXIT

remote_quote() {
  printf "'%s'" "$(printf "%s" "$1" | sed "s/'/'\\\\''/g")"
}

run_as() {
  "$ADB" shell "run-as $PKG sh -c $(remote_quote "$1")"
}

start_daemon_for_test() {
  "$ADB" shell am broadcast \
    -n "$PKG/$CLASS_PREFIX.PdockerdDebugReceiver" \
    -a "$ACTION_PREFIX.action.SMOKE_START" >/dev/null 2>&1 || true
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
  run_as "cd files && { printf 'POST /system/operations HTTP/1.1\r\nHost: pdocker\r\nContent-Type: application/json\r\nContent-Length: $len\r\nConnection: close\r\n\r\n'; printf %s $(remote_quote "$json"); } | toybox nc -U pdocker/pdockerd.sock >/dev/null 2>&1 || true" >/dev/null 2>&1 || true
}

wait_for_engine() {
  local i
  start_daemon_for_test
  for i in $(seq 1 45); do
    if run_as 'cd files && test -S pdocker/pdockerd.sock && { printf "GET /_ping HTTP/1.1\r\nHost: pdocker\r\nConnection: close\r\n\r\n"; } | toybox nc -U pdocker/pdockerd.sock | grep -q OK' >/dev/null 2>&1; then
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
  python3 - "$IMAGE" "$DEVICE_PROJECT" "$DEVICE_MODEL_HOST" "$DEVICE_WORKSPACE_HOST" "$mode" "$ctx" "$gpu_layers" "$REMOTE_PORT" "$TRACE_ALLOC" "$MODEL_PATH" "$MODEL_URL" <<'PY'
import json
import os
import sys

image, project, model_host, workspace_host, mode, ctx, gpu_layers, port, trace_alloc, model_path, model_url = sys.argv[1:12]
env = [
    "PDOCKER_GPU=auto",
    "PDOCKER_GPU_AUTO=1",
    f"PDOCKER_GPU_MODE={mode}",
    f"LLAMA_ARG_MODEL={model_path}",
    f"LLAMA_ARG_CTX={ctx}",
    f"LLAMA_ARG_PORT={port}",
    "LLAMA_LOG_FILE=/workspace/logs/llama-server.log",
]
if model_url:
    env.append(f"LLAMA_MODEL_URL={model_url}")
if mode == "vulkan-raw":
    env.extend([
        "PDOCKER_VULKAN_MAX_BUFFER_BYTES=536870912",
        "PDOCKER_VULKAN_ALIAS_COPIES=1",
        "PDOCKER_VULKAN_DUMP_SPIRV_DIR=/workspace/logs",
        "PDOCKER_GPU_DISPATCH_PROFILE_LOG=1",
        "GGML_VK_FORCE_MAX_BUFFER_SIZE=536870912",
        "GGML_VK_FORCE_MAX_ALLOCATION_SIZE=536870912",
        "GGML_VK_SUBALLOCATION_BLOCK_SIZE=536870912",
        f"LLAMA_ARG_N_GPU_LAYERS={gpu_layers}",
    ])
    if trace_alloc == "1":
        env.append("PDOCKER_VULKAN_ICD_TRACE_ALLOC=1")
        env.append("PDOCKER_GPU_DISPATCH_PROFILE_RESPONSE=1")
for key in [
    "PDOCKER_GPU_DISABLE_PIPELINE_OPTIMIZATION",
    "PDOCKER_GPU_MUTABLE_BUFFER_CACHE_MAX_BYTES",
    "PDOCKER_GPU_RESIDENT_CACHE_MIN_BYTES",
    "PDOCKER_GPU_SKIP_UNUSED_DESCRIPTOR_TRANSFERS",
    "PDOCKER_GPU_USE_SPIRV_DESCRIPTOR_ACCESS",
    "PDOCKER_GPU_WRITEONLY_BUFFER_CACHE",
    "PDOCKER_GPU_WRITEONLY_DIRTY_PROBE",
    "PDOCKER_GPU_WRITEONLY_DIRTY_PROBE_MIN_BYTES",
    "PDOCKER_GPU_WRITEONLY_DIRTY_WRITEBACK",
    "PDOCKER_GPU_DISPATCH_PROFILE_LOG",
]:
    value = os.environ.get(key)
    if value is not None:
        env.append(f"{key}={value}")
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
  "$ADB" forward --remove "tcp:$LOCAL_PORT" >/dev/null 2>&1 || true
  "$ADB" forward "tcp:$LOCAL_PORT" "tcp:$REMOTE_PORT" >/dev/null
  for _ in $(seq 1 "$seconds"); do
    if curl -fsS --max-time 2 "http://127.0.0.1:$LOCAL_PORT/v1/models" >/dev/null 2>&1 && container_running; then
      return 0
    fi
    sleep 1
  done
  return 1
}

container_ref() {
  printf "%s" "${CURRENT_CONTAINER_ID:-$CONTAINER}"
}

container_logs() {
  local ref
  ref="$(container_ref)"
  engine_request GET "/containers/$(urlencode "$ref")/logs?stdout=1&stderr=1&tail=320" | decode_engine_logs || true
}

container_state() {
  local ref
  ref="$(container_ref)"
  engine_body GET "/containers/$(urlencode "$ref")/json" || true
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
  engine_request DELETE "/containers/$(urlencode "$CONTAINER")?force=true" >/dev/null || true
  CURRENT_CONTAINER_ID=""
}

start_container_mode() {
  local mode="$1"
  local ctx="$2"
  local gpu_layers="${3:-}"
  local payload create_body cid
  wait_for_engine
  remove_container
  payload="$(container_payload "$mode" "$ctx" "$gpu_layers")"
  create_body="$(engine_body POST "/containers/create?name=$(urlencode "$CONTAINER")" "$payload")"
  cid="$(printf "%s" "$create_body" | parse_engine_id)"
  engine_request POST "/containers/$cid/start" "" >/dev/null
  CURRENT_CONTAINER_ID="$cid"
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
    PDOCKER_LLAMA_BENCH_MODE="$mode" \
    PDOCKER_LLAMA_BENCH_OUT="$out" \
    "$ROOT/scripts/android-llama-bench.sh"
}

wait_for_engine
DEVICE_PROJECT="$(run_as "cd $(remote_quote "$PROJECT") && pwd" | tr -d '\r')"
DEVICE_MODEL_HOST="$(run_as "cd $(remote_quote "$PROJECT") && . ./.env >/dev/null 2>&1 && printf '%s' \"\${PDOCKER_MODEL_HOST:-$DEVICE_PROJECT/models}\"" | tr -d '\r')"
DEVICE_WORKSPACE_HOST="$(run_as "cd $(remote_quote "$PROJECT") && . ./.env >/dev/null 2>&1 && printf '%s' \"\${PDOCKER_FAST_WORKSPACE_HOST:-$DEVICE_PROJECT/workspace}\"" | tr -d '\r')"
CPU_JSON="$TMP/cpu.json"
if [[ "$RUN_CPU" -eq 1 ]]; then
  echo "[pdocker llama compare] start CPU baseline"
  CURRENT_STAGE="CPU baseline"
  operation_notify "running" "CPU baseline: starting"
  start_cpu >/dev/null
  if ! wait_server 90; then
    operation_notify "failed" "CPU server did not become reachable" 1
    echo "CPU server did not become reachable" >&2
    container_state >&2
    container_logs >&2
    exit 1
  fi
  bench_http "cpu-baseline" "$CPU_JSON" >/dev/null
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
if wait_server 120; then
  operation_notify "running" "Forced Vulkan served; recording HTTP benchmark"
  bench_http "vulkan-forced-ngl-$GPU_LAYERS" "$GPU_JSON" >/dev/null || true
  gpu_served=1
else
  operation_notify "running" "Forced Vulkan did not serve; collecting container logs"
  gpu_served=0
fi
container_state > "$GPU_STATE"
container_logs > "$GPU_LOG"

python3 - "$CPU_JSON" "$GPU_JSON" "$GPU_LOG" "$GPU_STATE" "$OUT" "$gpu_served" "$GPU_LAYERS" "$GPU_CTX" "$PREDICT" "$REPEAT" "$TRACE_ALLOC" "$MODEL_PATH" "$MODEL_URL" <<'PY'
import json
import re
import sys
import time
from pathlib import Path

cpu_path, gpu_path, gpu_log_path, gpu_state_path, out_path, gpu_served_s, gpu_layers, gpu_ctx, predict, repeat, trace_alloc, model_path, model_url = sys.argv[1:14]
cpu = json.load(open(cpu_path, encoding="utf-8"))
gpu = {}
if Path(gpu_path).is_file() and Path(gpu_path).stat().st_size:
    try:
        gpu = json.load(open(gpu_path, encoding="utf-8"))
    except Exception:
        gpu = {}
log = Path(gpu_log_path).read_text(encoding="utf-8", errors="replace") if Path(gpu_log_path).is_file() else ""
state = Path(gpu_state_path).read_text(encoding="utf-8", errors="replace") if Path(gpu_state_path).is_file() else ""
cpu_tps = float(cpu.get("summary", {}).get("predicted_tokens_per_second_mean") or 0.0)
gpu_tps = float(gpu.get("summary", {}).get("predicted_tokens_per_second_mean") or 0.0)
target_tps = cpu_tps * 10.0

def json_string_field_seen(name, value):
    return re.search(rf'"{re.escape(name)}"\s*:\s*"{re.escape(value)}"', log) is not None

def json_bool_field_seen(name, value):
    return re.search(rf'"{re.escape(name)}"\s*:\s*{str(value).lower()}\b', log) is not None

executor_backends = sorted(set(re.findall(r'"backend_impl"\s*:\s*"([^"]+)"', log)))
executor_errors = sorted(set(re.findall(r'"error"\s*:\s*"([^"]+)"', log)))
spirv_hashes = sorted(set(re.findall(r'"spirv_hash"\s*:\s*"([^"]+)"', log)))

def extract_executor_json_events(text):
    events = []
    for line in text.splitlines():
        raw = line.strip()
        marker = "generic dispatch response:"
        if marker in raw:
            raw = raw.split(marker, 1)[1].strip()
        if not raw.startswith("{"):
            continue
        try:
            event = json.loads(raw)
        except Exception:
            continue
        if event.get("executor") == "pdocker-gpu-executor":
            events.append(event)
    return events

def parse_android_feature_trace(text):
    m = re.search(
        r"pdocker-gpu-executor: Android Vulkan features api=([0-9]+)\.([0-9]+) "
        r"shaderInt64=([0-9]+) "
        r"storage16=\{ssbo:([0-9]+),ubo_ssbo:([0-9]+),push:([0-9]+),io:([0-9]+)\} "
        r"float16=([0-9]+) int8=([0-9]+) "
        r"subgroup=\{size:([0-9]+),stages:0x([0-9a-fA-F]+),ops:0x([0-9a-fA-F]+)\}",
        text,
    )
    if not m:
        return {}
    return {
        "api_version": f"{m.group(1)}.{m.group(2)}",
        "shader_int64": bool(int(m.group(3))),
        "storage16": {
            "ssbo": bool(int(m.group(4))),
            "ubo_ssbo": bool(int(m.group(5))),
            "push": bool(int(m.group(6))),
            "io": bool(int(m.group(7))),
        },
        "shader_float16": bool(int(m.group(8))),
        "shader_int8": bool(int(m.group(9))),
        "subgroup": {
            "size": int(m.group(10)),
            "stages": f"0x{int(m.group(11), 16):x}",
            "ops": f"0x{int(m.group(12), 16):x}",
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
executor_backends = sorted(set(executor_backends) | {e.get("backend_impl") for e in executor_events if e.get("backend_impl")})
executor_errors = sorted(set(executor_errors) | {e.get("error") for e in executor_events if e.get("error")})
spirv_hashes = sorted(set(spirv_hashes) | {e.get("spirv_hash") for e in executor_events if e.get("spirv_hash")})
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
if evidence["buffer_range_assert_blocker"]:
    blocker_class = "vulkan_buffer_range_accounting"
    blocker_detail = "scheduler warmup hit ggml_backend_buffer_get_alloc_size"
elif evidence["buffer_allocation_blocker"] or evidence["assert_blocker"]:
    blocker_class = "vulkan_buffer_allocation"
    blocker_detail = "Vulkan buffer allocation/assertion failed before dispatch"
elif pipeline_feature_blocker:
    blocker_class = "vulkan_pipeline_feature"
    blocker_detail = "Android Vulkan rejected a ggml generic SPIR-V compute pipeline with VK_ERROR_FEATURE_NOT_PRESENT"
elif generic_spirv_dispatch_blocker:
    blocker_class = "vulkan_generic_spirv_dispatch"
    blocker_detail = "generic SPIR-V dispatch reached submit-generic-dispatch / queue submit failure"
elif queue_submit_blocker:
    blocker_class = "vulkan_queue_submit_feature"
    blocker_detail = "llama.cpp submitted a Vulkan workload, but vkQueueSubmit failed with ErrorFeatureNotPresent before the executor trace boundary"
elif evidence["generic_spirv_dispatch_seen"] and bool(int(gpu_served_s)):
    blocker_class = "bridge_dispatch_performance"
    blocker_detail = "generic SPIR-V dispatch served; benchmark throughput is the remaining gap"
elif evidence["offload_seen"] and gpu_offloaded_layers > 1 and bool(int(gpu_served_s)):
    blocker_class = "bridge_dispatch_performance"
    blocker_detail = "Vulkan offload served with repeating transformer layers, but throughput is still below the 10x target"
elif evidence["gpu_output_only_offload"] and bool(int(gpu_served_s)):
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
cpu_mapped_model_mib = [
    float(m.group(1))
    for m in re.finditer(r"CPU_Mapped model buffer size =\s+([0-9.]+) MiB", log)
]
vulkan_model_mib = [
    float(m.group(1))
    for m in re.finditer(r"Vulkan0 model buffer size =\s+([0-9.]+) MiB", log)
]
bridge_max_buffer_bytes = 536870912
largest_allocation = max(allocations) if allocations else 0
largest_created_buffer = max(created_buffers) if created_buffers else 0
model_cpu_mapped_bytes = int(cpu_mapped_model_mib[-1] * 1024 * 1024) if cpu_mapped_model_mib else 0
chunking_pressure = {
    "configured_bridge_max_buffer_bytes": bridge_max_buffer_bytes,
    "largest_allocation_bytes": largest_allocation,
    "largest_created_buffer_bytes": largest_created_buffer,
    "allocation_near_clamp": bool(largest_allocation and largest_allocation >= int(bridge_max_buffer_bytes * 0.90)),
    "model_cpu_mapped_bytes": model_cpu_mapped_bytes,
    "model_cpu_mapped_exceeds_bridge_clamp": bool(model_cpu_mapped_bytes and model_cpu_mapped_bytes > bridge_max_buffer_bytes),
    "vulkan_model_buffer_mib": vulkan_model_mib[-1] if vulkan_model_mib else 0.0,
    "descriptor_range_max_bytes": max(descriptor_ranges) if descriptor_ranges else 0,
}
advertised_limits = {
    "configured_clamps": {
        "PDOCKER_VULKAN_MAX_BUFFER_BYTES": bridge_max_buffer_bytes,
        "GGML_VK_FORCE_MAX_BUFFER_SIZE": bridge_max_buffer_bytes,
        "GGML_VK_FORCE_MAX_ALLOCATION_SIZE": bridge_max_buffer_bytes,
        "GGML_VK_SUBALLOCATION_BLOCK_SIZE": bridge_max_buffer_bytes,
    },
    "icd_advertises_subgroup_arithmetic_by_default": "PDOCKER_VULKAN_DISABLE_SUBGROUP_ARITHMETIC" not in log,
    "executor_android_features": android_feature_trace,
    "spirv_trace_count": len(spirv_traces),
    "last_spirv_trace": spirv_traces[-1] if spirv_traces else {},
}
generic_spirv_dispatch = {
    "attempted": generic_spirv_attempted,
    "valid_android_vulkan_events": [e for e in executor_events if e.get("kernel") == "generic_spirv" and e.get("backend_impl") == "android_vulkan" and e.get("valid") is True][-4:],
    "failed_events": [
        e for e in executor_events
        if e.get("valid") is False and (e.get("kernel") == "generic_spirv" or e.get("error") == "submit-generic-dispatch")
    ][-4:],
    "fallback_events": [e for e in executor_events if e.get("backend_affinity") == "fallback"][-4:],
    "llama_throw": "vk::Queue::submit: ErrorFeatureNotPresent" if queue_submit_blocker else "",
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
    "fix Vulkan buffer base/range accounting for scheduler warmup"
    if evidence["buffer_range_assert_blocker"]
    else
    "split 4GiB+ Vulkan buffers / pinned host-buffer path"
    if evidence["buffer_allocation_blocker"] or evidence["assert_blocker"]
    else "map failed SPIR-V capabilities to Android Vulkan feature bits, then clamp or translate the advertised feature set"
    if blocker_class == "vulkan_pipeline_feature"
    else "lower generic SPIR-V dispatch into the Android Vulkan executor or clamp advertised capabilities"
    if blocker_class in {"vulkan_generic_spirv_dispatch", "vulkan_queue_submit_feature"}
    else "inspect traced Android Vulkan feature/SPIR-V mismatch"
    if evidence["android_vulkan_dispatch_blocker"] and evidence["executor_spirv_trace_seen"]
    else "lower llama.cpp SPIR-V dispatch into the Android GPU executor"
    if evidence["spirv_dispatch_blocker"] or evidence["queue_submit_blocker"]
    else "increase n-gpu-layers until repeating transformer layers are offloaded"
    if blocker_class == "insufficient_gpu_offload_depth"
    else "reduce bridge upload/copy overhead with persistent registered buffers; rerun with larger n_predict"
    if blocker_class == "bridge_dispatch_performance"
    else "make Vulkan device discovery reliable"
)
if blocker_class == "bridge_dispatch_performance":
    blocker_detail = "served through generic SPIR-V, but bridge upload/copy overhead keeps GPU below CPU throughput"
result = {
    "schema": "pdocker.llama.gpu.compare.v1",
    "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "policy": {
        "llama_cpp_modified": False,
        "gpu_entry": "standard Vulkan loader through pdocker-vulkan-icd.so",
        "target_speedup": 10.0,
    },
    "settings": {
        "gpu_layers": int(gpu_layers),
        "gpu_ctx": int(gpu_ctx),
        "predict": int(predict),
        "repeat": int(repeat),
        "trace_alloc": bool(int(trace_alloc)),
        "cpu_reused": bool(cpu.get("reused_cpu_baseline")),
        "model_path": model_path,
        "model_url_set": bool(model_url),
    },
    "cpu": {
        "tokens_per_second": cpu_tps,
        "summary": cpu.get("summary", {}),
    },
    "gpu": {
        "tokens_per_second": gpu_tps,
        "summary": gpu.get("summary", {}),
        "served": bool(int(gpu_served_s)),
        "state_excerpt": state[:2000],
        "log_excerpt": log[-12000:],
        "evidence": evidence,
        "allocation_trace_bytes": allocations[-32:],
        "bridge_dispatch_profile": bridge_dispatch_profile,
        "diagnostics": {
            "blocker_class": blocker_class,
            "blocker_detail": blocker_detail,
            "failure_axes": failure_axes,
            "advertised_limits": advertised_limits,
            "chunking_pressure": chunking_pressure,
            "generic_spirv_dispatch": generic_spirv_dispatch,
            "executor_backends": executor_backends,
            "executor_errors": executor_errors,
            "spirv_hashes": spirv_hashes[-4:],
        },
    },
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
        "container_surface": "pdocker-llama-cpp remains the container shown by Engine container listing",
        "cleanup": "remove adb port forward and mark failed operation on nonzero exit; CPU restore is opt-in with --restore because the next run recreates the required mode",
    },
    "next_blocker": blocker_detail,
    "next_action": next_action,
}
Path(out_path).write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(json.dumps(result["comparison"], indent=2))
print("next_blocker:", result["next_blocker"])
PY

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
    f"next: {d['next_blocker']}"
)
PY
)"
operation_notify "running" "$SUMMARY"

DEVICE_NAME="$(basename "$OUT")"
"$ADB" push "$OUT" "/data/local/tmp/$DEVICE_NAME" >/dev/null
run_as "mkdir -p files/pdocker/bench && cp /data/local/tmp/$DEVICE_NAME files/pdocker/bench/$DEVICE_NAME"

if [[ "$RESTORE_CPU" -eq 1 ]]; then
  echo "[pdocker llama compare] restore CPU server"
  CURRENT_STAGE="restore CPU server"
  operation_notify "running" "Restoring CPU llama server"
  if start_cpu >/dev/null; then
    wait_server 90 >/dev/null || true
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

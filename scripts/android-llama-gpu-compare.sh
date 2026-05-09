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
WARMUP_DISCARD="${PDOCKER_LLAMA_BENCH_WARMUP_DISCARD:-0}"
OUT="${PDOCKER_LLAMA_COMPARE_OUT:-$ROOT/docs/test/llama-gpu-compare-latest.json}"
MODEL_PATH="${PDOCKER_LLAMA_MODEL_PATH:-/models/model.gguf}"
MODEL_URL="${PDOCKER_LLAMA_MODEL_URL:-https://huggingface.co/Qwen/Qwen3-8B-GGUF/resolve/main/Qwen3-8B-Q4_K_M.gguf}"
RESTORE_CPU=0
RUN_CPU=1
CPU_TPS_OVERRIDE="${PDOCKER_LLAMA_CPU_TPS:-}"
TRACE_ALLOC="${PDOCKER_LLAMA_TRACE_ALLOC:-0}"
CORRECTNESS="${PDOCKER_LLAMA_CORRECTNESS:-1}"
LOG_TAIL_LINES="${PDOCKER_LLAMA_LOG_TAIL_LINES:-2000}"
OP_ID="llama-gpu-compare-$(date -u +%Y%m%dT%H%M%SZ)-$$"
CURRENT_CONTAINER_ID=""

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
    "PDOCKER_GPU_MATERIALIZE_DESCRIPTOR_ALIASES",
    "PDOCKER_GPU_MATERIALIZE_SPIRV_SPECIALIZATION_CONSTANTS",
    "PDOCKER_GPU_MUTABLE_BUFFER_CACHE_MAX_BYTES",
    "PDOCKER_GPU_REWRITE_DUPLICATE_DESCRIPTOR_BINDINGS",
    "PDOCKER_GPU_DISABLE_OVERLAP_ALIASING",
    "PDOCKER_GPU_CPU_ORACLE",
    "PDOCKER_GPU_RESIDENT_CACHE_MIN_BYTES",
    "PDOCKER_GPU_SKIP_UNUSED_DESCRIPTOR_TRANSFERS",
    "PDOCKER_GPU_USE_SPIRV_DESCRIPTOR_ACCESS",
    "PDOCKER_GPU_WRITEONLY_BUFFER_CACHE",
    "PDOCKER_GPU_WRITEONLY_DIRTY_PROBE",
    "PDOCKER_GPU_WRITEONLY_DIRTY_PROBE_MIN_BYTES",
    "PDOCKER_GPU_WRITEONLY_DIRTY_WRITEBACK",
    "PDOCKER_GPU_DISPATCH_PROFILE_LOG",
    "PDOCKER_GPU_DISPATCH_PROFILE_RESPONSE",
    "PDOCKER_VULKAN_ALIAS_COPIES",
    "PDOCKER_VULKAN_DISABLE_8BIT_STORAGE",
    "PDOCKER_VULKAN_DISABLE_16BIT_STORAGE",
    "PDOCKER_VULKAN_DISABLE_SUBGROUP_ARITHMETIC",
    "PDOCKER_VULKAN_ENABLE_8BIT_STORAGE",
    "PDOCKER_VULKAN_ENABLE_16BIT_STORAGE",
    "PDOCKER_VULKAN_ENABLE_INT64",
    "PDOCKER_VULKAN_ENABLE_SUBGROUP_ARITHMETIC",
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
  engine_request GET "/containers/$(urlencode "$ref")/logs?stdout=1&stderr=1&tail=$LOG_TAIL_LINES" | decode_engine_logs || true
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
    PDOCKER_LLAMA_BENCH_WARMUP_DISCARD="$WARMUP_DISCARD" \
    PDOCKER_LLAMA_BENCH_MODE="$mode" \
    PDOCKER_LLAMA_BENCH_OUT="$out" \
    "$ROOT/scripts/android-llama-bench.sh"
}

probe_http_correctness() {
  local mode="$1"
  local out="$2"
  "$ADB" forward --remove "tcp:$LOCAL_PORT" >/dev/null 2>&1 || true
  "$ADB" forward "tcp:$LOCAL_PORT" "tcp:$REMOTE_PORT" >/dev/null
  python3 - "http://127.0.0.1:$LOCAL_PORT" "$mode" "$GPU_LAYERS" "$MODEL_PATH" "$out" <<'PY'
import json
import os
import sys
import time
import urllib.request

base_url, mode, gpu_layers, model_path, out_path = sys.argv[1:6]
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
            timeout=180,
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

wait_for_engine
DEVICE_PROJECT="$(run_as "cd $(remote_quote "$PROJECT") && pwd" | tr -d '\r')"
DEVICE_MODEL_HOST="$(run_as "cd $(remote_quote "$PROJECT") && . ./.env >/dev/null 2>&1 && printf '%s' \"\${PDOCKER_MODEL_HOST:-$DEVICE_PROJECT/models}\"" | tr -d '\r')"
DEVICE_WORKSPACE_HOST="$(run_as "cd $(remote_quote "$PROJECT") && . ./.env >/dev/null 2>&1 && printf '%s' \"\${PDOCKER_FAST_WORKSPACE_HOST:-$DEVICE_PROJECT/workspace}\"" | tr -d '\r')"
CPU_JSON="$TMP/cpu.json"
CPU_CORRECTNESS_JSON="$TMP/cpu-correctness.json"
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
CORRECTNESS_JSON="$TMP/correctness.json"
if wait_server 120; then
  operation_notify "running" "Forced Vulkan served; recording HTTP benchmark"
  bench_http "vulkan-forced-ngl-$GPU_LAYERS" "$GPU_JSON" >/dev/null || true
  if [[ "$CORRECTNESS" != "0" ]]; then
    operation_notify "running" "Forced Vulkan served; checking arithmetic correctness"
    probe_http_correctness "vulkan-forced-ngl-$GPU_LAYERS" "$CORRECTNESS_JSON" >/dev/null || true
  fi
  gpu_served=1
else
  operation_notify "running" "Forced Vulkan did not serve; collecting container logs"
  gpu_served=0
fi
container_state > "$GPU_STATE"
container_logs > "$GPU_LOG"

python3 - "$CPU_JSON" "$CPU_CORRECTNESS_JSON" "$GPU_JSON" "$GPU_LOG" "$GPU_STATE" "$CORRECTNESS_JSON" "$OUT" "$gpu_served" "$GPU_LAYERS" "$GPU_CTX" "$PREDICT" "$REPEAT" "$WARMUP_DISCARD" "$TRACE_ALLOC" "$MODEL_PATH" "$MODEL_URL" <<'PY'
import json
import math
import os
import re
import sys
import time
from pathlib import Path

cpu_path, cpu_correctness_path, gpu_path, gpu_log_path, gpu_state_path, correctness_path, out_path, gpu_served_s, gpu_layers, gpu_ctx, predict, repeat, warmup_discard, trace_alloc, model_path, model_url = sys.argv[1:17]
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
cpu_correctness = {}
if Path(cpu_correctness_path).is_file() and Path(cpu_correctness_path).stat().st_size:
    try:
        cpu_correctness = json.load(open(cpu_correctness_path, encoding="utf-8"))
    except Exception:
        cpu_correctness = {}
cpu_tps = float(cpu.get("summary", {}).get("predicted_tokens_per_second_mean") or 0.0)
gpu_tps = float(gpu.get("summary", {}).get("predicted_tokens_per_second_mean") or 0.0)
target_tps = cpu_tps * 10.0

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
    marker = "generic dispatch response:"
    starts = []
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
    for line_start, line in enumerate(text.splitlines()):
        raw = line.strip()
        if raw.startswith("{"):
            starts.append(text.find(line, 0 if line_start == 0 else 0))
    seen_starts = set()
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
            events.append(event)
    return events

def observed_event_values(events, field):
    values = []
    for event in events:
        if isinstance(event, dict) and field in event:
            values.append(event.get(field))
    return values

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
config_expectations = [
    ("PDOCKER_GPU_REWRITE_DUPLICATE_DESCRIPTOR_BINDINGS", "duplicate_descriptor_rewrite"),
    ("PDOCKER_GPU_MATERIALIZE_SPIRV_SPECIALIZATION_CONSTANTS", "materialize_specialization"),
    ("PDOCKER_GPU_DISABLE_PIPELINE_OPTIMIZATION", "disable_pipeline_optimization"),
    ("PDOCKER_GPU_SKIP_UNUSED_DESCRIPTOR_TRANSFERS", "skip_unused_descriptor_transfers"),
    ("PDOCKER_GPU_USE_SPIRV_DESCRIPTOR_ACCESS", "spirv_descriptor_access"),
    ("PDOCKER_GPU_DISABLE_OVERLAP_ALIASING", "disable_overlap_aliasing"),
    ("PDOCKER_GPU_CPU_ORACLE", "cpu_oracle_requested"),
]
config_checks = []
for env_name, event_field in config_expectations:
    expected = env_bool(env_name)
    observed = observed_event_values(executor_events, event_field)
    if expected is None:
        status = "not-requested"
    elif not observed:
        status = "missing-evidence"
    elif all(value == expected for value in observed):
        status = "pass"
    else:
        status = "mismatch"
    config_checks.append({
        "env": env_name,
        "executor_field": event_field,
        "expected": expected,
        "observed_values": observed[-8:],
        "status": status,
    })
config_propagation = {
    "summary": "fail" if any(item["status"] in {"missing-evidence", "mismatch"} for item in config_checks) else "pass",
    "checks": config_checks,
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
elif executor_feature_mismatches:
    blocker_class = "vulkan_feature_mismatch"
    blocker_detail = "generic SPIR-V dispatch ran while executor feature policy reports missing features: " + ",".join(executor_feature_mismatches)
elif config_propagation["summary"] == "fail":
    blocker_class = "config_propagation_mismatch"
    blocker_detail = "one or more requested bridge tuning options did not appear with the expected value in executor evidence"
elif int(gpu_layers) == 0 and evidence["generic_spirv_dispatch_seen"] and bool(int(gpu_served_s)) and (
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
    "descriptor_array_layout_seen": bool(descriptor_array_layouts),
    "descriptor_array_layouts": descriptor_array_layouts[-16:],
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
    "largest_shader_events": sorted(
        [
            e for e in executor_events
            if e.get("kernel") == "generic_spirv"
            and e.get("backend_impl") == "android_vulkan"
            and e.get("valid") is True
        ],
        key=lambda event: int(event.get("shader_bytes") or 0),
        reverse=True,
    )[:8],
    "largest_binding_events": sorted(
        [
            e for e in executor_events
            if e.get("kernel") == "generic_spirv"
            and e.get("backend_impl") == "android_vulkan"
            and e.get("valid") is True
        ],
        key=lambda event: sum(int(detail.get("size") or 0) for detail in event.get("binding_details") or []),
        reverse=True,
    )[:8],
    "failed_events": [
        e for e in executor_events
        if e.get("valid") is False and (e.get("kernel") == "generic_spirv" or e.get("error") == "submit-generic-dispatch")
    ][-4:],
    "fallback_events": [e for e in executor_events if e.get("backend_affinity") == "fallback"][-4:],
    "llama_throw": "vk::Queue::submit: ErrorFeatureNotPresent" if queue_submit_blocker else "",
}

valid_spirv_events = [
    e for e in executor_events
    if e.get("kernel") == "generic_spirv"
    and e.get("backend_impl") == "android_vulkan"
    and e.get("valid") is True
]
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
    else "clamp or translate llama.cpp storage8/int8 final-projection shaders before accepting performance results"
    if blocker_class == "vulkan_feature_mismatch"
    else "lower llama.cpp SPIR-V dispatch into the Android GPU executor"
    if evidence["spirv_dispatch_blocker"] or evidence["queue_submit_blocker"]
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
            "config_propagation": config_propagation,
            "api_understanding": api_understanding,
            "diagnostic_bisection": diagnostic_bisection,
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
    f"correctness={d['gpu'].get('correctness', {}).get('summary', {}).get('correctness', 'not-run')}; "
    f"next: {d['next_blocker']}"
)
PY
)"
operation_notify "running" "$SUMMARY"

DEVICE_NAME="$(basename "$OUT")"
"$ADB" push "$OUT" "/data/local/tmp/$DEVICE_NAME" >/dev/null
run_as "mkdir -p files/pdocker/bench && cp /data/local/tmp/$DEVICE_NAME files/pdocker/bench/$DEVICE_NAME"
if [[ -s "$CORRECTNESS_JSON" ]]; then
  CORRECTNESS_DEVICE_NAME="llama-correctness-$(date -u +%Y%m%dT%H%M%SZ).json"
  "$ADB" push "$CORRECTNESS_JSON" "/data/local/tmp/$CORRECTNESS_DEVICE_NAME" >/dev/null
  run_as "mkdir -p files/pdocker/bench && cp /data/local/tmp/$CORRECTNESS_DEVICE_NAME files/pdocker/bench/$CORRECTNESS_DEVICE_NAME"
fi

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

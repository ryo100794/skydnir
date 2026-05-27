#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ADB="${ADB:-adb}"
PKG="${SKYDNIR_PACKAGE:-${PDOCKER_PACKAGE:-io.github.ryo100794.pdocker.compat}}"
CONTAINER="${PDOCKER_LLAMA_CONTAINER:-pdocker-llama-cpp}"
MODEL="${PDOCKER_LLAMA_MODEL:-/models/model.gguf}"
PROMPT_TOKENS="${PDOCKER_LLAMA_TOOL_PROMPT:-16}"
GEN_TOKENS="${PDOCKER_LLAMA_TOOL_GEN:-8}"
REPEAT="${PDOCKER_LLAMA_TOOL_REPEAT:-3}"
THREADS="${PDOCKER_LLAMA_THREADS:-8}"
GPU_LAYERS="${PDOCKER_LLAMA_GPU_LAYERS:-0}"
OUT="${PDOCKER_LLAMA_TOOL_OUT:-$ROOT/docs/test/llama-bench-tool-cpu-p16-n8-r3.json}"
DEVICE_BENCH_DIR="${PDOCKER_LLAMA_DEVICE_BENCH_DIR:-files/pdocker/bench}"

usage() {
  cat <<EOF
Usage: $0 [--container NAME] [--model PATH] [--prompt-tokens N] [--gen-tokens N] [--repeat N] [--threads N] [--gpu-layers N] [--out PATH]

Runs the official llama.cpp llama-bench tool inside the running pdocker llama
container. If the image was built with only llama-server, this script builds
the llama-bench target in the existing llama.cpp build directory first.

Defaults match the recorded CPU fallback baseline:
  prompt tokens: $PROMPT_TOKENS
  generated tokens: $GEN_TOKENS
  repetitions: $REPEAT
  threads: $THREADS
  gpu layers: $GPU_LAYERS
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --container) CONTAINER="$2"; shift ;;
    --model) MODEL="$2"; shift ;;
    --prompt-tokens) PROMPT_TOKENS="$2"; shift ;;
    --gen-tokens) GEN_TOKENS="$2"; shift ;;
    --repeat) REPEAT="$2"; shift ;;
    --threads) THREADS="$2"; shift ;;
    --gpu-layers) GPU_LAYERS="$2"; shift ;;
    --out) OUT="$2"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

mkdir -p "$(dirname "$OUT")"

remote_quote() {
  printf "'%s'" "$(printf "%s" "$1" | sed "s/'/'\\\\''/g")"
}

run_as() {
  "$ADB" shell "run-as $PKG sh -c $(remote_quote "$1")"
}

docker_cmd_prefix='cd files && export PATH="$PWD/pdocker-runtime/docker-bin:$PATH" DOCKER_CONFIG="$PWD/pdocker-runtime/docker-bin" DOCKER_HOST="unix://$PWD/pdocker/pdockerd.sock"'

echo "[pdocker llama-bench] ensuring llama-bench target"
run_as "$docker_cmd_prefix; docker exec $(printf "%q" "$CONTAINER") sh -lc 'test -x /opt/llama.cpp/build/bin/llama-bench || (cd /opt/llama.cpp && cmake --build build --target llama-bench --parallel 2)'"

echo "[pdocker llama-bench] running official llama-bench"
run_as "$docker_cmd_prefix; docker exec $(printf "%q" "$CONTAINER") sh -lc '/opt/llama.cpp/build/bin/llama-bench -m $(printf "%q" "$MODEL") -p $(printf "%q" "$PROMPT_TOKENS") -n $(printf "%q" "$GEN_TOKENS") -r $(printf "%q" "$REPEAT") -ngl $(printf "%q" "$GPU_LAYERS") -t $(printf "%q" "$THREADS") -o json'" > "$OUT"

DEVICE_NAME="$(basename "$OUT")"
"$ADB" push "$OUT" "/data/local/tmp/$DEVICE_NAME" >/dev/null
run_as "mkdir -p \"$DEVICE_BENCH_DIR\" && cp \"/data/local/tmp/$DEVICE_NAME\" \"$DEVICE_BENCH_DIR/$DEVICE_NAME\""

python3 - "$OUT" <<'PY'
import json
import sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
for row in data:
    label = "prompt" if row.get("n_prompt", 0) else "generation"
    print(f"{label}: avg_ts={row.get('avg_ts')} stddev_ts={row.get('stddev_ts')} samples={row.get('samples_ts')}")
PY

echo "[pdocker llama-bench] local: $OUT"
echo "[pdocker llama-bench] device: $DEVICE_BENCH_DIR/$DEVICE_NAME"

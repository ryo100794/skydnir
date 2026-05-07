#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ADB="${ADB:-adb}"
PKG="${PDOCKER_PACKAGE:-io.github.ryo100794.pdocker.compat}"
LOCAL_PORT="${PDOCKER_LLAMA_LOCAL_PORT:-28081}"
REMOTE_PORT="${PDOCKER_LLAMA_REMOTE_PORT:-18081}"
PREDICT="${PDOCKER_LLAMA_BENCH_PREDICT:-8}"
REPEAT="${PDOCKER_LLAMA_BENCH_REPEAT:-1}"
WARMUP_DISCARD="${PDOCKER_LLAMA_BENCH_WARMUP_DISCARD:-0}"
PROMPT="${PDOCKER_LLAMA_BENCH_PROMPT:-Repeat exactly: pdocker-ok}"
OUT="${PDOCKER_LLAMA_BENCH_OUT:-$ROOT/docs/test/llama-bench-latest.json}"
MODE="${PDOCKER_LLAMA_BENCH_MODE:-server-current}"
DEVICE_BENCH_DIR="${PDOCKER_LLAMA_DEVICE_BENCH_DIR:-files/pdocker/bench}"

usage() {
  cat <<EOF
Usage: $0 [--predict N] [--repeat N] [--warmup-discard N] [--prompt TEXT] [--mode LABEL] [--local-port PORT] [--remote-port PORT] [--out PATH]

Benchmarks the currently running pdocker llama.cpp server over adb forward.
The benchmark intentionally works against the UI/Engine-started container so
CPU fallback and future GPU passthrough runs can be compared directly.

Environment:
  ADB                         adb executable (default: adb)
  PDOCKER_PACKAGE             package name (default: $PKG)
  PDOCKER_LLAMA_LOCAL_PORT    host forward port (default: $LOCAL_PORT)
  PDOCKER_LLAMA_REMOTE_PORT   device server port (default: $REMOTE_PORT)
  PDOCKER_LLAMA_BENCH_PREDICT generated tokens per run (default: $PREDICT)
  PDOCKER_LLAMA_BENCH_REPEAT  number of measured runs (default: $REPEAT)
  PDOCKER_LLAMA_BENCH_WARMUP_DISCARD
                              leading runs to exclude from primary summary
  PDOCKER_LLAMA_BENCH_PROMPT  prompt text
  PDOCKER_LLAMA_BENCH_MODE    result mode label (default: $MODE)
  PDOCKER_LLAMA_BENCH_OUT     local JSON result path
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --predict) PREDICT="$2"; shift ;;
    --repeat) REPEAT="$2"; shift ;;
    --warmup-discard) WARMUP_DISCARD="$2"; shift ;;
    --prompt) PROMPT="$2"; shift ;;
    --mode) MODE="$2"; shift ;;
    --local-port) LOCAL_PORT="$2"; shift ;;
    --remote-port) REMOTE_PORT="$2"; shift ;;
    --out) OUT="$2"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

mkdir -p "$(dirname "$OUT")"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"; "$ADB" forward --remove "tcp:'"$LOCAL_PORT"'" >/dev/null 2>&1 || true' EXIT

"$ADB" forward --remove "tcp:$LOCAL_PORT" >/dev/null 2>&1 || true
"$ADB" forward "tcp:$LOCAL_PORT" "tcp:$REMOTE_PORT" >/dev/null

BASE_URL="http://127.0.0.1:$LOCAL_PORT"
if ! curl -fsS --max-time 15 "$BASE_URL/v1/models" >/dev/null; then
  echo "llama.cpp server is not reachable at $BASE_URL; start compose first" >&2
  exit 1
fi

PROFILE_JSON="$TMP/profile.json"
"$ADB" shell "run-as $PKG sh -c 'cat files/pdocker/projects/llama-cpp-gpu/profiles/pdocker-gpu-diagnostics.json 2>/dev/null || true'" > "$PROFILE_JSON" || true

python3 - "$BASE_URL" "$PREDICT" "$REPEAT" "$WARMUP_DISCARD" "$PROMPT" "$OUT" "$MODE" "$PROFILE_JSON" <<'PY'
import json
import statistics
import sys
import time
import urllib.error
import urllib.request

base_url, predict_s, repeat_s, warmup_discard_s, prompt, out_path, mode, profile_path = sys.argv[1:9]
predict = int(predict_s)
repeat = int(repeat_s)
warmup_discard = int(warmup_discard_s)
if warmup_discard < 0:
    raise SystemExit("warmup discard must be >= 0")
try:
    gpu_profile = json.load(open(profile_path, encoding="utf-8"))
except Exception:
    gpu_profile = {}


def post_json(path, body, timeout):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        base_url + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = resp.read()
        status = resp.status
    elapsed = time.perf_counter() - started
    return status, elapsed, json.loads(payload.decode("utf-8"))


def get_json(path, timeout=10):
    with urllib.request.urlopen(base_url + path, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


models = get_json("/v1/models")
runs = []
for i in range(repeat):
    status, elapsed, payload = post_json(
        "/completion",
        {
            "prompt": prompt,
            "n_predict": predict,
            "temperature": 0,
            "cache_prompt": False,
            "stream": False,
        },
        timeout=max(120, predict * 45),
    )
    timings = payload.get("timings") or {}
    predicted_n = int(timings.get("predicted_n") or payload.get("tokens_predicted") or 0)
    predicted_s = float(timings.get("predicted_ms") or 0.0) / 1000.0
    predicted_per_second = float(timings.get("predicted_per_second") or 0.0)
    if not predicted_per_second and predicted_n and predicted_s:
        predicted_per_second = predicted_n / predicted_s
    runs.append(
        {
            "index": i,
            "http_status": status,
            "wall_seconds": elapsed,
            "prompt_tokens": int(timings.get("prompt_n") or payload.get("tokens_evaluated") or 0),
            "prompt_seconds": float(timings.get("prompt_ms") or 0.0) / 1000.0,
            "prompt_tokens_per_second": float(timings.get("prompt_per_second") or 0.0),
            "predicted_tokens": predicted_n,
            "predicted_seconds": predicted_s,
            "predicted_tokens_per_second": predicted_per_second,
            "content_preview": str(payload.get("content", ""))[:160],
        }
    )

def summarize(selected_runs):
    speeds = [r["predicted_tokens_per_second"] for r in selected_runs if r["predicted_tokens_per_second"]]
    return {
        "predicted_tokens_per_second_mean": statistics.mean(speeds) if speeds else 0.0,
        "predicted_tokens_per_second_min": min(speeds) if speeds else 0.0,
        "predicted_tokens_per_second_max": max(speeds) if speeds else 0.0,
        "wall_seconds_mean": statistics.mean(r["wall_seconds"] for r in selected_runs) if selected_runs else 0.0,
        "runs": len(selected_runs),
    }

steady_runs = runs[warmup_discard:] if warmup_discard < len(runs) else []
if not steady_runs and runs:
    steady_runs = runs
summary_scope = "steady_state" if warmup_discard and warmup_discard < len(runs) else "all_runs"
result = {
    "schema": "pdocker.llama.bench.v1",
    "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "endpoint": base_url,
    "mode": mode,
    "gpu_profile": gpu_profile,
    "prompt": prompt,
    "n_predict": predict,
    "repeat": repeat,
    "warmup_discard": warmup_discard,
    "summary_scope": summary_scope,
    "models": models,
    "summary": summarize(steady_runs),
    "all_runs_summary": summarize(runs),
    "runs": runs,
}
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(result, f, indent=2, ensure_ascii=False)
    f.write("\n")
print(json.dumps(result["summary"], indent=2))
PY

DEVICE_NAME="$(basename "$OUT")"
"$ADB" push "$OUT" "/data/local/tmp/$DEVICE_NAME" >/dev/null
"$ADB" shell "run-as $PKG sh -c 'mkdir -p \"$DEVICE_BENCH_DIR\" && cp \"/data/local/tmp/$DEVICE_NAME\" \"$DEVICE_BENCH_DIR/$DEVICE_NAME\"'" >/dev/null

echo "[pdocker llama bench] local: $OUT"
echo "[pdocker llama bench] device: $DEVICE_BENCH_DIR/$DEVICE_NAME"

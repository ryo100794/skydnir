#!/usr/bin/env bash
# Capture host-side Android CPU/Vulkan executor timings without requiring a
# running container. This is the native GPU baseline used before container
# bridge and llama.cpp GPU comparisons.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ADB="${ADB:-adb}"
PKG="${SKYDNIR_PACKAGE:-${PDOCKER_PACKAGE:-io.github.ryo100794.pdocker.compat}}"
CLASS_PREFIX="${PDOCKER_CLASS_PREFIX:-io.github.ryo100794.pdocker}"
RUNS="${PDOCKER_GPU_HOST_RUNS:-5}"
OUT_JSON="${PDOCKER_GPU_HOST_OUT:-$ROOT/docs/test/gpu-host-native-latest.json}"
OUT_MD="${PDOCKER_GPU_HOST_MD:-$ROOT/docs/test/gpu-host-native-latest.md}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

usage() {
  cat <<'USAGE'
Usage: android-gpu-host-bench.sh [--runs N] [--out PATH] [--markdown PATH]

Environment:
  ADB                         adb executable, default: adb
  ANDROID_SERIAL              adb serial, honored by adb
  PDOCKER_PACKAGE             package name, default: io.github.ryo100794.pdocker.compat
  PDOCKER_GPU_HOST_RUNS       default run count
  PDOCKER_GPU_HOST_OUT        default JSON output path
  PDOCKER_GPU_HOST_MD         default Markdown output path
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --runs)
      RUNS="$2"
      shift 2
      ;;
    --out)
      OUT_JSON="$2"
      shift 2
      ;;
    --markdown)
      OUT_MD="$2"
      shift 2
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
done

remote_quote() {
  printf "'%s'" "$(printf "%s" "$1" | sed "s/'/'\\\\''/g")"
}

run_as() {
  "$ADB" shell "run-as $PKG sh -c $(remote_quote "$1")"
}

mkdir -p "$(dirname "$OUT_JSON")" "$(dirname "$OUT_MD")"
"$ADB" shell am start -n "$PKG/$CLASS_PREFIX.MainActivity" >/dev/null

bench_cmd='cd files && ./pdocker-runtime/gpu/pdocker-gpu-executor'
run_as "$bench_cmd --bench-cpu-matmul256 $RUNS" >"$TMP/cpu_matmul.jsonl"
run_as "$bench_cmd --bench-vulkan-matmul256-resident $RUNS" >"$TMP/vulkan_matmul_resident.jsonl"
run_as "$bench_cmd --bench-cpu-vector-add $RUNS" >"$TMP/cpu_vector.jsonl"
run_as "$bench_cmd --bench-vulkan-vector-add-resident $RUNS" >"$TMP/vulkan_vector_resident.jsonl"

python3 - "$TMP" "$OUT_JSON" "$OUT_MD" "$RUNS" "$STAMP" <<'PY'
import json
import statistics
import sys
from pathlib import Path

tmp = Path(sys.argv[1])
out_json = Path(sys.argv[2])
out_md = Path(sys.argv[3])
runs = int(sys.argv[4])
stamp = sys.argv[5]

labels = [
    ("Host CPU matmul256", "cpu_matmul"),
    ("Host Vulkan matmul256 resident", "vulkan_matmul_resident"),
    ("Host CPU vector-add", "cpu_vector"),
    ("Host Vulkan vector-add resident", "vulkan_vector_resident"),
]

def rows(label):
    out = []
    for line in (tmp / f"{label}.jsonl").read_text(errors="replace").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out

def median(values):
    return statistics.median(values) if values else None

def mean(values):
    return statistics.fmean(values) if values else None

def summarize(items):
    valid = [r for r in items if r.get("valid") is True]
    totals = [float(r.get("total_ms", 0.0)) for r in valid]
    warm = [
        float(r.get("total_ms", 0.0))
        for r in valid
        if float(r.get("init_ms", 0.0)) == 0.0
        and float(r.get("upload_ms", 0.0)) == 0.0
    ]
    dispatch = [float(r.get("dispatch_ms", 0.0)) for r in valid]
    warm_dispatch = [
        float(r.get("dispatch_ms", 0.0))
        for r in valid
        if float(r.get("init_ms", 0.0)) == 0.0
        and float(r.get("upload_ms", 0.0)) == 0.0
    ]
    steady = warm if warm else totals
    steady_dispatch = warm_dispatch if warm_dispatch else dispatch
    return {
        "samples": len(items),
        "valid_samples": len(valid),
        "backend_impl": next((r.get("backend_impl") for r in items if r.get("backend_impl")), None),
        "backend_affinity": next((r.get("backend_affinity") for r in items if r.get("backend_affinity")), None),
        "transport": next((r.get("transport") for r in items if r.get("transport")), None),
        "kernel": next((r.get("kernel") for r in items if r.get("kernel")), None),
        "problem_size": next((r.get("problem_size") for r in items if r.get("problem_size")), None),
        "total_ms_median": median(totals),
        "total_ms_mean": mean(totals),
        "steady_total_ms_median": median(steady),
        "steady_total_ms_mean": mean(steady),
        "steady_dispatch_ms_median": median(steady_dispatch),
        "steady_dispatch_ms_mean": mean(steady_dispatch),
    }

all_rows = {key: rows(key) for _, key in labels}
summaries = {key: summarize(value) for key, value in all_rows.items()}

cpu_matmul = summaries["cpu_matmul"]["steady_total_ms_median"]
gpu_matmul = summaries["vulkan_matmul_resident"]["steady_total_ms_median"]
cpu_vector = summaries["cpu_vector"]["steady_total_ms_median"]
gpu_vector = summaries["vulkan_vector_resident"]["steady_total_ms_median"]

doc = {
    "schema": "pdocker.gpu.host_native.v1",
    "timestamp_utc": stamp,
    "runs_requested": runs,
    "summary": summaries,
    "ratios": {
        "host_cpu_matmul_over_host_vulkan_matmul_resident": (cpu_matmul / gpu_matmul) if cpu_matmul and gpu_matmul else None,
        "host_vulkan_matmul_resident_over_host_cpu_matmul": (gpu_matmul / cpu_matmul) if cpu_matmul and gpu_matmul else None,
        "host_cpu_vector_over_host_vulkan_vector_resident": (cpu_vector / gpu_vector) if cpu_vector and gpu_vector else None,
        "host_vulkan_vector_resident_over_host_cpu_vector": (gpu_vector / cpu_vector) if cpu_vector and gpu_vector else None,
    },
    "samples": all_rows,
}
out_json.write_text(json.dumps(doc, indent=2) + "\n")

def fmt(v):
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.4f}"
    return str(v)

lines = [
    "# GPU Host-Native Baseline",
    "",
    f"- Date: {stamp} UTC.",
    f"- Runs: {runs}.",
    "- Scope: Android native executor inside the APK app process domain.",
    "- This is not CPU emulation; Vulkan samples use the Android Vulkan backend.",
    "",
    "| Probe | Backend | Valid | Steady median ms | Steady mean ms | Dispatch median ms | Transport |",
    "| --- | --- | ---: | ---: | ---: | ---: | --- |",
]
for title, key in labels:
    s = summaries[key]
    lines.append(
        f"| {title} | {s.get('backend_impl') or '-'} | {s.get('valid_samples')}/{s.get('samples')} | "
        f"{fmt(s.get('steady_total_ms_median'))} | {fmt(s.get('steady_total_ms_mean'))} | "
        f"{fmt(s.get('steady_dispatch_ms_median'))} | {s.get('transport') or '-'} |"
    )
lines += [
    "",
    "## Ratios",
    "",
    f"- Host CPU matmul256 / host Vulkan resident matmul256: {fmt(doc['ratios']['host_cpu_matmul_over_host_vulkan_matmul_resident'])}x.",
    f"- Host Vulkan resident matmul256 / host CPU matmul256: {fmt(doc['ratios']['host_vulkan_matmul_resident_over_host_cpu_matmul'])}x.",
    f"- Host CPU vector-add / host Vulkan resident vector-add: {fmt(doc['ratios']['host_cpu_vector_over_host_vulkan_vector_resident'])}x.",
    f"- Host Vulkan resident vector-add / host CPU vector-add: {fmt(doc['ratios']['host_vulkan_vector_resident_over_host_cpu_vector'])}x.",
    "",
    "Interpretation: matmul is the useful LLM-shaped probe. Vector-add is intentionally retained as a transfer/dispatch overhead canary, and CPU may win there.",
]
out_md.write_text("\n".join(lines) + "\n")
print("\n".join(lines))
PY

echo "[pdocker gpu host bench] json: $OUT_JSON"
echo "[pdocker gpu host bench] markdown: $OUT_MD"

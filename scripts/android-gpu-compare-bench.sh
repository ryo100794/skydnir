#!/usr/bin/env bash
# Capture comparable host/container CPU/GPU timings plus bridge overhead.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ADB="${ADB:-adb}"
PKG="${SKYDNIR_PACKAGE:-${PDOCKER_PACKAGE:-io.github.ryo100794.pdocker.compat}}"
CLASS_PREFIX="${SKYDNIR_CLASS_PREFIX:-${PDOCKER_CLASS_PREFIX:-io.github.ryo100794.pdocker}}"
RUNS="${1:-${PDOCKER_GPU_COMPARE_RUNS:-10}}"
WARMUP_DISCARD="${PDOCKER_GPU_COMPARE_WARMUP_DISCARD:-3}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_JSON="${PDOCKER_GPU_COMPARE_OUT:-$ROOT/docs/test/gpu-host-container-comparison-latest.json}"
OUT_MD="${PDOCKER_GPU_COMPARE_MD:-$ROOT/docs/test/gpu-host-container-comparison-latest.md}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

remote_quote() {
  printf "'%s'" "$(printf "%s" "$1" | sed "s/'/'\\\\''/g")"
}

run_as() {
  "$ADB" shell "run-as $PKG sh -c $(remote_quote "$1")"
}

stage_test_cli() {
  local docker_bin="$ROOT/docker-proot-setup/docker-bin/docker"
  local compose_bin="$ROOT/vendor/lib/docker-compose"
  if [[ ! -x "$docker_bin" || ! -x "$compose_bin" ]]; then
    echo "test Docker CLI/Compose binaries missing; run backend fetch/build first" >&2
    return 1
  fi
  "$ADB" push "$docker_bin" /data/local/tmp/pdocker-test-docker >/dev/null
  "$ADB" push "$compose_bin" /data/local/tmp/pdocker-test-docker-compose >/dev/null
  run_as "mkdir -p files/pdocker-runtime/docker-bin/cli-plugins && cp /data/local/tmp/pdocker-test-docker files/pdocker-runtime/docker-bin/docker && cp /data/local/tmp/pdocker-test-docker-compose files/pdocker-runtime/docker-bin/cli-plugins/docker-compose && chmod 755 files/pdocker-runtime/docker-bin/docker files/pdocker-runtime/docker-bin/cli-plugins/docker-compose"
}

docker_cmd() {
  local cmd="$1"
  run_as "cd files && export PATH=\"\$PWD/pdocker-runtime/docker-bin:\$PATH\" DOCKER_CONFIG=\"\$PWD/pdocker-runtime/docker-bin\" DOCKER_HOST=\"unix://\$PWD/pdocker/pdockerd.sock\" DOCKER_BUILDKIT=0 COMPOSE_DOCKER_CLI_BUILD=0 BUILDKIT_PROGRESS=plain COMPOSE_PROGRESS=plain COMPOSE_MENU=false && $cmd"
}

wait_for_runtime() {
  local i
  for i in $(seq 1 45); do
    if run_as 'test -S files/pdocker/pdockerd.sock && test -S files/pdocker-runtime/gpu/pdocker-gpu.sock' >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  echo "pdockerd/gpu executor sockets did not appear" >&2
  return 1
}

pick_container() {
  if [[ -n "${PDOCKER_GPU_COMPARE_CONTAINER:-}" ]]; then
    printf '%s\n' "$PDOCKER_GPU_COMPARE_CONTAINER"
    return 0
  fi
  docker_cmd "docker ps --format '{{.Names}}'" | awk 'NF {print; exit}'
}

container_rootfs() {
  local container="$1"
  local inspect="$TMP/container-inspect.json"
  docker_cmd "docker inspect $(printf "%q" "$container")" >"$inspect"
  python3 - "$inspect" <<'PY'
import json
import sys
doc = json.load(open(sys.argv[1]))
if not doc:
    raise SystemExit(1)
cid = doc[0].get("Id") or ""
rootfs = ((doc[0].get("Storage") or {}).get("Rootfs") or "")
if not rootfs and cid:
    rootfs = f"pdocker/containers/{cid}/rootfs"
if not rootfs:
    raise SystemExit(1)
print(rootfs)
PY
}

direct_container_cmd() {
  local rootfs="$1"
  local argv="$2"
  run_as "cd files && ROOTFS=$(remote_quote "$rootfs") && RUNTIME=\$PWD/pdocker-runtime && export PDOCKER_DIRECT_EXPERIMENTAL_PROCESS_EXEC=1 PDOCKER_DIRECT_TRACE_MODE=seccomp PDOCKER_DIRECT_TRACE_SYSCALLS=0 PDOCKER_DIRECT_TRACE_VERBOSE=0; pdocker-runtime/docker-bin/pdocker-direct run --mode bench --rootfs \"\$ROOTFS\" --workdir / --env HOME=/root --env PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin --env PDOCKER_GPU_QUEUE_SOCKET=\"\$RUNTIME/gpu/pdocker-gpu.sock\" --bind \"\$RUNTIME/gpu:/run/pdocker-gpu\" -- /bin/sh -lc \"/usr/local/bin/pdocker-gpu-shim $argv\""
}

install_container_helpers() {
  local rootfs="$1"
  run_as "cd files && ROOTFS=$(remote_quote "$rootfs") && mkdir -p \"\$ROOTFS/usr/local/bin\" && cp pdocker-runtime/lib/pdocker-gpu-shim \"\$ROOTFS/usr/local/bin/pdocker-gpu-shim\" && chmod 755 \"\$ROOTFS/usr/local/bin/pdocker-gpu-shim\""
}

mkdir -p "$(dirname "$OUT_JSON")"
"$ADB" shell am start -n "$PKG/$CLASS_PREFIX.MainActivity" >/dev/null
wait_for_runtime
stage_test_cli

CONTAINER="$(pick_container || true)"
if [[ -z "$CONTAINER" ]]; then
  echo "no running container found; start llama/dev compose first or set PDOCKER_GPU_COMPARE_CONTAINER" >&2
  exit 2
fi
ROOTFS="$(container_rootfs "$CONTAINER")"
install_container_helpers "$ROOTFS"

echo "[pdocker gpu compare] container=$CONTAINER rootfs=$ROOTFS runs=$RUNS"

measure() {
  local label="$1"; shift
  local start end rc=0
  start="$(date +%s%N)"
  "$@" >"$TMP/$label.jsonl" 2>"$TMP/$label.err" || rc=$?
  end="$(date +%s%N)"
  python3 - "$label" "$start" "$end" "$RUNS" "$rc" >>"$TMP/wall.tsv" <<'PY'
import sys
label, start, end, runs, rc = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5])
ms = (end - start) / 1_000_000.0
print(f"{label}\t{ms:.6f}\t{ms / runs:.6f}\t{rc}")
PY
  cat "$TMP/$label.err" >&2
  return "$rc"
}

measure host_cpu run_as "files/pdocker-runtime/gpu/pdocker-gpu-executor --bench-cpu-vector-add '$RUNS'"
measure host_gpu_vulkan run_as "files/pdocker-runtime/gpu/pdocker-gpu-executor --bench-vulkan-vector-add '$RUNS'"
measure host_gpu_vulkan_resident run_as "files/pdocker-runtime/gpu/pdocker-gpu-executor --bench-vulkan-vector-add-resident '$RUNS'"
measure host_cpu_matmul run_as "files/pdocker-runtime/gpu/pdocker-gpu-executor --bench-cpu-matmul256 '$RUNS'"
measure host_gpu_vulkan_matmul_resident run_as "files/pdocker-runtime/gpu/pdocker-gpu-executor --bench-vulkan-matmul256-resident '$RUNS'"
measure container_cpu direct_container_cmd "$ROOTFS" "--bench-cpu-vector-add $(printf "%q" "$RUNS")"
measure bridge_noop direct_container_cmd "$ROOTFS" "--bench-noop-persistent $(printf "%q" "$RUNS")"
measure container_gpu_vulkan direct_container_cmd "$ROOTFS" "--bench-vulkan-vector-add-3fd-persistent $(printf "%q" "$RUNS")"

python3 - "$TMP" "$OUT_JSON" "$OUT_MD" "$RUNS" "$CONTAINER" "$STAMP" "$WARMUP_DISCARD" <<'PY'
import json
import statistics
import sys
from pathlib import Path

tmp = Path(sys.argv[1])
out_json = Path(sys.argv[2])
out_md = Path(sys.argv[3])
runs = int(sys.argv[4])
container = sys.argv[5]
stamp = sys.argv[6]
warmup_discard_requested = int(sys.argv[7])
labels = [
    "host_cpu",
    "host_gpu_vulkan",
    "host_gpu_vulkan_resident",
    "host_cpu_matmul",
    "host_gpu_vulkan_matmul_resident",
    "container_cpu",
    "bridge_noop",
    "container_gpu_vulkan",
]

def load(label):
    rows = []
    path = tmp / f"{label}.jsonl"
    if not path.exists():
        return rows
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return rows

def summary(rows):
    valid = [r for r in rows if r.get("valid") is True]
    totals = [float(r.get("total_ms", 0.0)) for r in valid]
    dispatch = [float(r.get("dispatch_ms", 0.0)) for r in valid]
    warm = totals[1:] if len(totals) > 1 else totals
    discard = min(warmup_discard_requested, max(0, len(totals) - 1))
    steady = totals[discard:] if discard else totals
    steady_dispatch = dispatch[discard:] if discard else dispatch
    return {
        "samples": len(rows),
        "valid_samples": len(valid),
        "warmup_discarded": discard,
        "backend_impl": next((r.get("backend_impl") for r in rows if r.get("backend_impl")), None),
        "transport": next((r.get("transport") for r in rows if r.get("transport")), None),
        "cached_samples": sum(1 for r in rows if r.get("backend_cached") is True),
        "total_ms_mean": statistics.fmean(totals) if totals else None,
        "total_ms_min": min(totals) if totals else None,
        "warm_total_ms_mean": statistics.fmean(warm) if warm else None,
        "warm_total_ms_median": statistics.median(warm) if warm else None,
        "steady_total_ms_mean": statistics.fmean(steady) if steady else None,
        "steady_total_ms_median": statistics.median(steady) if steady else None,
        "steady_total_ms_min": min(steady) if steady else None,
        "steady_total_ms_max": max(steady) if steady else None,
        "dispatch_ms_mean": statistics.fmean(dispatch) if dispatch else None,
        "steady_dispatch_ms_mean": statistics.fmean(steady_dispatch) if steady_dispatch else None,
    }

wall = {}
wall_path = tmp / "wall.tsv"
if wall_path.exists():
    for line in wall_path.read_text().splitlines():
        label, total, per_run, rc = line.split("\t")
        wall[label] = {"wall_ms": float(total), "wall_ms_per_run": float(per_run), "rc": int(rc)}

rows = {label: load(label) for label in labels}
summaries = {label: summary(rows[label]) for label in labels}
host_gpu = summaries["host_gpu_vulkan"].get("steady_total_ms_median")
host_gpu_resident = summaries["host_gpu_vulkan_resident"].get("steady_total_ms_median")
container_gpu = summaries["container_gpu_vulkan"].get("steady_total_ms_median")
host_cpu = summaries["host_cpu"].get("steady_total_ms_median")
host_cpu_matmul = summaries["host_cpu_matmul"].get("steady_total_ms_median")
host_gpu_matmul = summaries["host_gpu_vulkan_matmul_resident"].get("steady_total_ms_median")
container_cpu = summaries["container_cpu"].get("steady_total_ms_median")
bridge_noop = wall.get("bridge_noop", {}).get("wall_ms_per_run")
bridge_noop_roundtrip = summaries["bridge_noop"].get("steady_total_ms_median")

doc = {
    "timestamp_utc": stamp,
    "runs_requested": runs,
    "warmup_discard_requested": warmup_discard_requested,
    "container": container,
    "summaries": summaries,
    "wall": wall,
    "ratios": {
        "container_gpu_over_host_gpu_steady_median_total": (container_gpu / host_gpu) if host_gpu and container_gpu else None,
        "container_gpu_over_host_gpu_resident_steady_median_total": (container_gpu / host_gpu_resident) if host_gpu_resident and container_gpu else None,
        "host_gpu_resident_over_host_gpu_transfer_total": (host_gpu_resident / host_gpu) if host_gpu and host_gpu_resident else None,
        "host_gpu_matmul_resident_over_host_cpu_matmul": (host_gpu_matmul / host_cpu_matmul) if host_cpu_matmul and host_gpu_matmul else None,
        "host_cpu_matmul_over_host_gpu_matmul_resident": (host_cpu_matmul / host_gpu_matmul) if host_cpu_matmul and host_gpu_matmul else None,
        "container_cpu_over_host_cpu_steady_median_total": (container_cpu / host_cpu) if host_cpu and container_cpu else None,
        "bridge_noop_roundtrip_steady_median_total_ms": bridge_noop_roundtrip,
        "direct_executor_bridge_noop_wall_ms_per_call": bridge_noop,
    },
    "samples": rows,
}
out_json.write_text(json.dumps(doc, indent=2) + "\n")

def fmt(v):
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.4f}"
    return str(v)

table = [
    ("Host CPU", "host_cpu"),
    ("Host GPU Vulkan transfer", "host_gpu_vulkan"),
    ("Host GPU Vulkan resident", "host_gpu_vulkan_resident"),
    ("Host CPU matmul256", "host_cpu_matmul"),
    ("Host GPU Vulkan matmul256 resident", "host_gpu_vulkan_matmul_resident"),
    ("Container CPU", "container_cpu"),
    ("Bridge NOOP", "bridge_noop"),
    ("Container GPU Vulkan bridge", "container_gpu_vulkan"),
]
lines = [
    "# GPU Host/Container Comparison",
    "",
    f"- Date: {stamp} UTC.",
    f"- Container: `{container}`.",
    f"- Runs: {runs}.",
    f"- Warmup samples discarded per series: {warmup_discard_requested}.",
    "",
    "| Scope | Backend | Valid | Steady median ms | Steady mean ms | Steady dispatch mean ms | Wall ms/call | Transport |",
    "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
]
for title, label in table:
    s = summaries[label]
    w = wall.get(label, {})
    lines.append(
        f"| {title} | {s.get('backend_impl') or '-'} | {s.get('valid_samples')}/{s.get('samples')} | "
        f"{fmt(s.get('steady_total_ms_median'))} | {fmt(s.get('steady_total_ms_mean'))} | "
        f"{fmt(s.get('steady_dispatch_ms_mean'))} | "
        f"{fmt(w.get('wall_ms_per_run'))} | {s.get('transport') or '-'} |"
    )
lines += [
    "",
    "## Ratios",
    "",
    f"- Container GPU / host GPU steady median total: {fmt(doc['ratios']['container_gpu_over_host_gpu_steady_median_total'])}x.",
    f"- Container GPU / host resident GPU steady median total: {fmt(doc['ratios']['container_gpu_over_host_gpu_resident_steady_median_total'])}x.",
    f"- Host resident Vulkan / host transfer Vulkan steady median total: {fmt(doc['ratios']['host_gpu_resident_over_host_gpu_transfer_total'])}x.",
    f"- Host GPU resident matmul256 / host CPU matmul256 steady median total: {fmt(doc['ratios']['host_gpu_matmul_resident_over_host_cpu_matmul'])}x.",
    f"- Host CPU matmul256 / host GPU resident matmul256 steady median total: {fmt(doc['ratios']['host_cpu_matmul_over_host_gpu_matmul_resident'])}x.",
    f"- Container CPU / host CPU steady median total: {fmt(doc['ratios']['container_cpu_over_host_cpu_steady_median_total'])}x.",
    f"- Bridge NOOP round trip inside container process: {fmt(doc['ratios']['bridge_noop_roundtrip_steady_median_total_ms'])} ms/call.",
    f"- Direct-executor wall time for the bridge NOOP measurement: {fmt(doc['ratios']['direct_executor_bridge_noop_wall_ms_per_call'])} ms/call.",
    "",
    "The direct-executor wall time includes starting and tracing the benchmark process; use the bridge NOOP round-trip row for the command-queue crossing cost.",
]
out_md.write_text("\n".join(lines) + "\n")
print("\n".join(lines))
PY

echo "[pdocker gpu compare] json: $OUT_JSON"
echo "[pdocker gpu compare] markdown: $OUT_MD"

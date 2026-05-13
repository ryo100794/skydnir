#!/usr/bin/env bash
# Lightweight libcow microbenchmarks for repeated tuning.
#
# The default output is a stable JSON artifact so regressions can be compared
# across commits and devices.  Keep this script dependency-light: it is used as
# the local gate for the materialized-rootfs COW fallback and must not depend on
# SAF, GPU, Android services, or Docker.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
LIB="${LIB:-$HERE/libcow.so}"
ROUNDS="${ROUNDS:-1}"
OPS="${COW_BENCH_OPS:-1000}"
COPY_UP_FILES="${COW_BENCH_COPY_UP_FILES:-32}"
COPY_UP_BYTES="${COW_BENCH_COPY_UP_BYTES:-4096}"
LAYER_COUNT="${COW_BENCH_LAYER_COUNT:-16}"
OUT="${COW_BENCH_JSON:-$ROOT/docs/test/cow-overlay-bench-latest.json}"

if [[ ! -f "$LIB" ]]; then
  echo "libcow not found: $LIB" >&2
  echo "Run: make -C $HERE all" >&2
  exit 1
fi

mkdir -p "$(dirname "$OUT")"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

cat > "$TMP/cowbench.py" <<'PY'
import json
import os
import shutil
import statistics
import sys
import tempfile
import time


def percentile(samples, pct):
    if not samples:
        return 0
    ordered = sorted(samples)
    idx = int(round((len(ordered) - 1) * (pct / 100.0)))
    return ordered[max(0, min(idx, len(ordered) - 1))]


def metric(name, samples_ns, notes=None, metadata=None):
    total = sum(samples_ns)
    ops = len(samples_ns)
    return {
        "name": name,
        "ops": ops,
        "total_ns": total,
        "ns_per_op": round(total / ops, 1) if ops else 0,
        "p50_ns": percentile(samples_ns, 50),
        "p95_ns": percentile(samples_ns, 95),
        "p99_ns": percentile(samples_ns, 99),
        "notes": notes or [],
        "metadata": metadata or {},
    }


def run_timed(count, fn):
    samples = []
    for i in range(count):
        t0 = time.perf_counter_ns()
        fn(i)
        samples.append(time.perf_counter_ns() - t0)
    return samples


def prepare_tree(root, copy_up_files, copy_up_bytes):
    lower = os.path.join(root, "lower")
    upper = os.path.join(root, "upper")
    os.mkdir(lower)
    os.mkdir(upper)
    with open(os.path.join(upper, "static.txt"), "wb") as f:
        f.write(b"x" * 64)
    payload = b"a" * copy_up_bytes
    for i in range(copy_up_files):
        lp = os.path.join(lower, f"copyup-{i}")
        up = os.path.join(upper, f"copyup-{i}")
        with open(lp, "wb") as f:
            f.write(payload)
        os.link(lp, up)
    return lower, upper


def bench_open_close(upper, ops):
    path = os.path.join(upper, "static.txt")

    def one(_):
        fd = os.open(path, os.O_RDONLY)
        os.close(fd)

    return metric("open_close", run_timed(ops, one))


def bench_stat(upper, ops):
    path = os.path.join(upper, "static.txt")
    return metric("stat", run_timed(ops, lambda _i: os.stat(path)))


def bench_create(work, ops):
    d = os.path.join(work, "create")
    os.mkdir(d)

    def one(i):
        fd = os.open(os.path.join(d, f"f-{i}"),
                     os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        os.close(fd)

    return metric("create", run_timed(ops, one))


def bench_unlink(work, ops):
    d = os.path.join(work, "unlink")
    os.mkdir(d)
    for i in range(ops):
        with open(os.path.join(d, f"f-{i}"), "wb") as f:
            f.write(b"x")

    def one(i):
        os.unlink(os.path.join(d, f"f-{i}"))

    return metric("unlink", run_timed(ops, one))


def bench_rename(work, ops):
    d = os.path.join(work, "rename")
    os.mkdir(d)
    for i in range(ops):
        with open(os.path.join(d, f"a-{i}"), "wb") as f:
            f.write(b"x")

    def one(i):
        os.rename(os.path.join(d, f"a-{i}"), os.path.join(d, f"b-{i}"))

    return metric("rename", run_timed(ops, one))


def bench_copy_up(lower, upper, files, copy_up_bytes):
    # Mutating a hardlinked upper file should trigger libcow copy-up.  The
    # post-check verifies that the benchmark measured an isolated COW path, not
    # a lower-layer write-through regression.
    def one(i):
        fd = os.open(os.path.join(upper, f"copyup-{i}"), os.O_WRONLY)
        os.write(fd, b"Z")
        os.close(fd)

    samples = run_timed(files, one)
    leaked = []
    still_hardlinked = []
    for i in range(files):
        lp = os.path.join(lower, f"copyup-{i}")
        up = os.path.join(upper, f"copyup-{i}")
        with open(lp, "rb") as f:
            if f.read(1) != b"a":
                leaked.append(i)
        if os.stat(lp).st_ino == os.stat(up).st_ino:
            still_hardlinked.append(i)
    status = "pass" if not leaked and not still_hardlinked else "fail"
    return metric(
        "copy_up",
        samples,
        notes=[
            "mutates hardlinked upper files with libcow loaded",
            "checks lower payload preservation and inode split",
        ],
        metadata={
            "files": files,
            "bytes_per_file": copy_up_bytes,
            "status": status,
            "leaked_indices": leaked[:10],
            "still_hardlinked_indices": still_hardlinked[:10],
        },
    )


def build_layers(work, layer_count):
    layers = []
    for idx in range(layer_count):
        d = os.path.join(work, "layers", f"layer-{idx}")
        os.makedirs(d)
        with open(os.path.join(d, f"hit-{idx}.txt"), "wb") as f:
            f.write(b"x")
        layers.append(d)
    return layers


def layer_lookup(layers, rel):
    for layer in reversed(layers):
        path = os.path.join(layer, rel)
        if os.path.exists(path):
            return path
    return None


def bench_layer_lookup(work, ops, layer_count):
    layers = build_layers(work, layer_count)
    hits = [f"hit-{i % layer_count}.txt" for i in range(ops)]

    def one(i):
        found = layer_lookup(layers, hits[i])
        if found is None:
            raise AssertionError(f"missing layer lookup for {hits[i]}")

    return metric(
        "layer_lookup",
        run_timed(ops, one),
        notes=["linear lower-layer lookup scaffold; future cache gate compares this"],
        metadata={"layer_count": layer_count},
    )


def main():
    ops = int(os.environ["COW_BENCH_OPS"])
    rounds = int(os.environ["ROUNDS"])
    copy_up_files = int(os.environ["COW_BENCH_COPY_UP_FILES"])
    copy_up_bytes = int(os.environ["COW_BENCH_COPY_UP_BYTES"])
    layer_count = int(os.environ["COW_BENCH_LAYER_COUNT"])
    out = os.environ["COW_BENCH_JSON"]
    lib = os.environ["LIB"]

    artifact = {
        "SchemaVersion": 1,
        "Kind": "cow-overlay-bench",
        "Status": "pass",
        "Config": {
            "Rounds": rounds,
            "Ops": ops,
            "CopyUpFiles": copy_up_files,
            "CopyUpBytes": copy_up_bytes,
            "LayerCount": layer_count,
            "Libcow": lib,
        },
        "Metrics": [],
        "Notes": [
            "All timings are host-local microbenchmarks.",
            "Container/device lanes should compare the same metric names.",
            "copy_up is a correctness+timing metric; it fails if lower changes.",
        ],
    }

    root = tempfile.mkdtemp(prefix="cowbench-")
    try:
        for round_no in range(1, rounds + 1):
            work = os.path.join(root, f"round-{round_no}")
            os.mkdir(work)
            lower, upper = prepare_tree(work, copy_up_files, copy_up_bytes)
            for m in (
                bench_open_close(upper, ops),
                bench_stat(upper, ops),
                bench_create(work, ops),
                bench_unlink(work, ops),
                bench_rename(work, ops),
                bench_copy_up(lower, upper, copy_up_files, copy_up_bytes),
                bench_layer_lookup(work, ops, layer_count),
            ):
                m["round"] = round_no
                artifact["Metrics"].append(m)
                if m.get("metadata", {}).get("status") == "fail":
                    artifact["Status"] = "fail"
    finally:
        shutil.rmtree(root, ignore_errors=True)

    with open(out, "w", encoding="utf-8") as f:
        json.dump(artifact, f, indent=2, sort_keys=True)
        f.write("\n")
    print(json.dumps({
        "artifact": out,
        "kind": artifact["Kind"],
        "metrics": len(artifact["Metrics"]),
        "status": artifact["Status"],
    }, sort_keys=True))
    if artifact["Status"] != "pass":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
PY

ROUNDS="$ROUNDS" \
COW_BENCH_OPS="$OPS" \
COW_BENCH_COPY_UP_FILES="$COPY_UP_FILES" \
COW_BENCH_COPY_UP_BYTES="$COPY_UP_BYTES" \
COW_BENCH_LAYER_COUNT="$LAYER_COUNT" \
COW_BENCH_JSON="$OUT" \
LIB="$LIB" \
LD_PRELOAD="$LIB" \
python3 "$TMP/cowbench.py"

#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EXECUTOR="${PDOCKER_DIRECT_EXECUTOR:-$ROOT/docker-proot-setup/docker-bin/pdocker-direct}"
ROOTFS="${ROOTFS:-}"
WORKDIR="${WORKDIR:-/}"
COW_UPPER="${COW_UPPER:-}"
COW_LOWER="${COW_LOWER:-}"
COW_GUEST="${COW_GUEST:-/}"
MEMORY_GUARD=0
LARGE_MB="${PDOCKER_CONTAINER_PROBE_LARGE_MB:-128}"
PROBE_DIR="${PDOCKER_CONTAINER_PROBE_DIR:-$ROOT/app/src/main/assets/project-library/direct-runtime-probe/scripts}"
MODE="${PDOCKER_CONTAINER_PROBE_MODE:-exec}"

usage() {
  cat <<EOF
Usage: $0 --rootfs PATH [options]

Runs the direct-runtime container probe through a pdocker-direct compatible
executor. This is the generic build/test entry point; Android adb staging is a
separate adapter.

Options:
  --executor PATH       pdocker-direct executable
  --rootfs PATH         container/image rootfs to run against
  --workdir PATH        guest working directory (default: /)
  --cow-upper PATH      cow_bind upperdir
  --cow-lower PATH      cow_bind lowerdir
  --cow-guest PATH      cow_bind guest path (default: /)
  --probe-dir PATH      host directory mounted as /pdocker-test
  --memory-guard        force large allocations to return ENOMEM
  --large-mb N          allocation size for the memory probe (default: $LARGE_MB)

Environment equivalents:
  PDOCKER_DIRECT_EXECUTOR, ROOTFS, WORKDIR, COW_UPPER, COW_LOWER,
  PDOCKER_CONTAINER_PROBE_DIR, PDOCKER_CONTAINER_PROBE_LARGE_MB
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --executor)
      EXECUTOR="${2:?--executor requires a path}"
      shift 2
      ;;
    --rootfs)
      ROOTFS="${2:?--rootfs requires a path}"
      shift 2
      ;;
    --workdir)
      WORKDIR="${2:?--workdir requires a path}"
      shift 2
      ;;
    --cow-upper)
      COW_UPPER="${2:?--cow-upper requires a path}"
      shift 2
      ;;
    --cow-lower)
      COW_LOWER="${2:?--cow-lower requires a path}"
      shift 2
      ;;
    --cow-guest)
      COW_GUEST="${2:?--cow-guest requires a path}"
      shift 2
      ;;
    --probe-dir)
      PROBE_DIR="${2:?--probe-dir requires a path}"
      shift 2
      ;;
    --memory-guard)
      MEMORY_GUARD=1
      shift
      ;;
    --large-mb)
      LARGE_MB="${2:?--large-mb requires a number}"
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

[[ -n "$ROOTFS" ]] || {
  echo "ROOTFS is required" >&2
  usage >&2
  exit 2
}
[[ -x "$EXECUTOR" ]] || {
  echo "executor is not executable: $EXECUTOR" >&2
  exit 1
}
[[ -f "$PROBE_DIR/pdocker-container-probe.sh" ]] || {
  echo "probe payload missing: $PROBE_DIR/pdocker-container-probe.sh" >&2
  exit 1
}

export PDOCKER_DIRECT_EXPERIMENTAL_PROCESS_EXEC=1
export PDOCKER_DIRECT_TRACE_EXEC="${PDOCKER_DIRECT_TRACE_EXEC:-1}"
export PDOCKER_DIRECT_TRACE_MEMORY="${PDOCKER_DIRECT_TRACE_MEMORY:-1}"
export PDOCKER_DIRECT_TRACE_MEMORY_THRESHOLD="${PDOCKER_DIRECT_TRACE_MEMORY_THRESHOLD:-1M}"

EXPECT_GUARD=0
if [[ "$MEMORY_GUARD" = "1" ]]; then
  export PDOCKER_DIRECT_MEMORY_GUARD=1
  export PDOCKER_DIRECT_MEMORY_GUARD_MIN_REQUEST="${PDOCKER_DIRECT_MEMORY_GUARD_MIN_REQUEST:-64M}"
  export PDOCKER_DIRECT_MEMORY_GUARD_MIN_AVAILABLE="${PDOCKER_DIRECT_MEMORY_GUARD_MIN_AVAILABLE:-64G}"
  export PDOCKER_DIRECT_MEMORY_GUARD_MIN_SWAP="${PDOCKER_DIRECT_MEMORY_GUARD_MIN_SWAP:-64G}"
  EXPECT_GUARD=1
fi

cmd=(
  "$EXECUTOR" run
  --mode "$MODE"
  --rootfs "$ROOTFS"
  --workdir "$WORKDIR"
  --env PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
  --env "PDOCKER_CONTAINER_PROBE_EXPECT_GUARD=$EXPECT_GUARD"
  --env "PDOCKER_CONTAINER_PROBE_LARGE_MB=$LARGE_MB"
  --bind "$PROBE_DIR:/pdocker-test:ro"
)

if [[ -n "$COW_UPPER" && -n "$COW_LOWER" ]]; then
  cmd+=(--cow-upper "$COW_UPPER" --cow-lower "$COW_LOWER" --cow-guest "$COW_GUEST")
fi

cmd+=(-- /bin/sh /pdocker-test/pdocker-container-probe.sh)

printf '[pdocker container probe] executor=%s rootfs=%s workdir=%s\n' "$EXECUTOR" "$ROOTFS" "$WORKDIR"
"${cmd[@]}"

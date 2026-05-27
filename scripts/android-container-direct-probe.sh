#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ADB="${ADB:-adb}"
if [[ -n "${ADB_SERIAL:-}" ]]; then
  ADB_ARGS=(-s "$ADB_SERIAL")
else
  ADB_ARGS=()
fi
PKG="${SKYDNIR_PACKAGE:-${PDOCKER_PACKAGE:-io.github.ryo100794.pdocker.compat}}"
CONTAINER="${SKYDNIR_LLAMA_CONTAINER:-${PDOCKER_LLAMA_CONTAINER:-skydnir-llama-cpp}}"
ROOTFS=""
MEMORY_GUARD=0
LARGE_MB="${PDOCKER_CONTAINER_PROBE_LARGE_MB:-128}"

usage() {
  cat <<EOF
Usage: $0 [--container NAME] [--rootfs PATH] [--memory-guard] [--large-mb N]

Runs repeatable test code inside an existing pdocker container rootfs through
pdocker-direct. This avoids APK rebuilds and large image rebuilds.

Environment:
  ADB_SERIAL        adb serial, for example 10.8.135.134:37669
  ADB               adb executable (default: adb)
  SKYDNIR_PACKAGE   Android package (PDOCKER_PACKAGE is still accepted; default: $PKG)

Examples:
  ADB_SERIAL=10.8.135.134:37669 $0 --container skydnir-llama-cpp --memory-guard
  ROOTFS=/data/user/0/$PKG/files/pdocker/containers/build_x/rootfs $0 --rootfs "\$ROOTFS"
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --container)
      CONTAINER="${2:?--container requires a name or id}"
      shift 2
      ;;
    --rootfs)
      ROOTFS="${2:?--rootfs requires a path}"
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

adb_cmd() {
  "$ADB" "${ADB_ARGS[@]}" "$@"
}

remote_quote() {
  printf "'%s'" "$(printf "%s" "$1" | sed "s/'/'\\\\''/g")"
}

run_as() {
  adb_cmd shell "run-as $PKG sh -c $(remote_quote "$1")"
}

find_container_id() {
  local target="$1"
  run_as "cd files/pdocker/containers 2>/dev/null || exit 1
for d in *; do
  [ -f \"\$d/state.json\" ] || continue
  if [ \"\$d\" = \"$target\" ] || grep -q '\"Name\": \"/$target\"' \"\$d/state.json\"; then
    printf '%s\n' \"\$d\"
    exit 0
  fi
done
exit 1"
}

state_shell_vars() {
  local cid="$1"
  local state_json
  state_json="$(run_as "cat files/pdocker/containers/$cid/state.json")"
  STATE_JSON="$state_json" python3 - <<'PY'
import json
import os
import shlex

state = json.loads(os.environ["STATE_JSON"])
storage = state.get("Storage") or {}
mode = storage.get("Mode") or "libcow"
cid = state["Id"]
pkg = os.environ.get("SKYDNIR_PACKAGE") or os.environ.get("PDOCKER_PACKAGE") or "io.github.ryo100794.pdocker.compat"
base = f"/data/user/0/{pkg}/files/pdocker/containers/{cid}"
if mode == "cow_bind":
    rootfs = storage.get("LowerDir") or f"{base}/rootfs"
    upper = storage.get("UpperDir") or ""
    lower = storage.get("LowerDir") or ""
else:
    rootfs = storage.get("Rootfs") or f"{base}/rootfs"
    upper = ""
    lower = ""
workdir = (state.get("Config") or {}).get("WorkingDir") or "/"
if not workdir:
    workdir = "/"
print(f"TARGET_ROOTFS={shlex.quote(rootfs)}")
print(f"TARGET_COW_UPPER={shlex.quote(upper)}")
print(f"TARGET_COW_LOWER={shlex.quote(lower)}")
print(f"TARGET_WORKDIR={shlex.quote(workdir)}")
PY
}

stage_probe() {
  local src="$ROOT/app/src/main/assets/project-library/direct-runtime-probe/scripts/pdocker-container-probe.sh"
  [ -f "$src" ] || {
    echo "missing probe script: $src" >&2
    exit 1
  }
  adb_cmd push "$src" /data/local/tmp/pdocker-container-probe.sh >/dev/null
  run_as "mkdir -p files/pdocker/test-container/direct-runtime &&
cp /data/local/tmp/pdocker-container-probe.sh files/pdocker/test-container/direct-runtime/pdocker-container-probe.sh &&
chmod 755 files/pdocker/test-container/direct-runtime/pdocker-container-probe.sh"
}

if [[ -z "$ROOTFS" ]]; then
  CID="$(find_container_id "$CONTAINER")" || {
    echo "container not found: $CONTAINER" >&2
    exit 1
  }
  eval "$(state_shell_vars "$CID")"
else
  TARGET_ROOTFS="$ROOTFS"
  TARGET_COW_UPPER=""
  TARGET_COW_LOWER=""
  TARGET_WORKDIR="/"
fi

stage_probe

PROBE_HOST="/data/user/0/$PKG/files/pdocker/test-container/direct-runtime"
DIRECT_ENV="PDOCKER_DIRECT_EXPERIMENTAL_PROCESS_EXEC=1 PDOCKER_DIRECT_TRACE_EXEC=1 PDOCKER_DIRECT_TRACE_MEMORY=1 PDOCKER_DIRECT_TRACE_MEMORY_THRESHOLD=1M"
EXPECT_GUARD=0
if [[ "$MEMORY_GUARD" = "1" ]]; then
  DIRECT_ENV="$DIRECT_ENV PDOCKER_DIRECT_MEMORY_GUARD=1 PDOCKER_DIRECT_MEMORY_GUARD_MIN_REQUEST=64M PDOCKER_DIRECT_MEMORY_GUARD_MIN_AVAILABLE=64G PDOCKER_DIRECT_MEMORY_GUARD_MIN_SWAP=64G"
  EXPECT_GUARD=1
fi

COW_ARGS=""
if [[ -n "${TARGET_COW_UPPER:-}" && -n "${TARGET_COW_LOWER:-}" ]]; then
  COW_ARGS="--cow-upper $(remote_quote "$TARGET_COW_UPPER") --cow-lower $(remote_quote "$TARGET_COW_LOWER") --cow-guest /"
fi

echo "[pdocker container probe] package=$PKG container=${CID:-manual-rootfs} rootfs=$TARGET_ROOTFS"
run_as "cd files &&
$DIRECT_ENV pdocker-runtime/docker-bin/pdocker-direct run --mode exec \
  --rootfs $(remote_quote "$TARGET_ROOTFS") \
  --workdir $(remote_quote "$TARGET_WORKDIR") \
  --env PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  --env PDOCKER_CONTAINER_PROBE_EXPECT_GUARD=$EXPECT_GUARD \
  --env PDOCKER_CONTAINER_PROBE_LARGE_MB=$LARGE_MB \
  --bind $(remote_quote "$PROBE_HOST:/pdocker-test:ro") \
  $COW_ARGS \
  -- /bin/sh /pdocker-test/pdocker-container-probe.sh"

#!/usr/bin/env bash
# Device gate runner for the glibc Vulkan ICD -> app GPU executor socket path.
# This intentionally does not use the host Vulkan loader. It stages a tiny
# storage-image smoke into an already-running Skydnir container and requires the
# guest loader/ICD/socket path to be observable from inside that container.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ADB="${ADB:-adb}"
PKG="${SKYDNIR_PACKAGE:-${PDOCKER_PACKAGE:-io.github.ryo100794.pdocker.compat}}"
CLASS_PREFIX="${SKYDNIR_CLASS_PREFIX:-${PDOCKER_CLASS_PREFIX:-io.github.ryo100794.pdocker}}"
OUT="${SKYDNIR_VULKAN_ICD_DEVICE_SOCKET_OUT:-$ROOT/docs/test/vulkan-icd-device-socket-latest.json}"
CONTAINER="${SKYDNIR_VULKAN_ICD_CONTAINER:-${PDOCKER_VULKAN_ICD_CONTAINER:-}}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$(dirname "$OUT")"

json_artifact() {
  local success="$1" reason="$2" rc="$3"
  python3 - "$OUT" "$success" "$reason" "$rc" "$TMP" "$PKG" "$CONTAINER" <<'PY'
import json
import os
import sys
from pathlib import Path
out = Path(sys.argv[1])
success = sys.argv[2] == "true"
reason = sys.argv[3]
rc = int(sys.argv[4])
tmp = Path(sys.argv[5])
pkg = sys.argv[6]
container = sys.argv[7]
def read(name):
    p = tmp / name
    return p.read_text(errors="replace") if p.exists() else ""
artifact = {
    "schema": "skydnir.vulkan.icd.device-socket.v1",
    "success": success,
    "reason": reason,
    "package": pkg,
    "container": container,
    "adb_serial": read("adb-serial.txt").strip(),
    "uses_host_vulkan_loader": False,
    "required_icd_json": "/etc/vulkan/icd.d/pdocker-android.json",
    "required_socket": "/run/pdocker-gpu/pdocker-gpu.sock",
    "app_socket": "files/pdocker-runtime/gpu/pdocker-gpu.sock",
    "checks": {
        "adb_devices": read("adb-devices.txt"),
        "app_socket": read("app-socket.txt").strip(),
        "direct_preflight": read("direct-preflight.txt"),
        "docker_ps": read("docker-ps.txt"),
        "guest_prereq": read("guest-prereq.txt"),
        "guest_run_stdout": read("guest-run.out"),
        "guest_run_stderr": read("guest-run.err"),
    },
    "promotion_requirements": [
        "guest/container glibc Vulkan loader, not host -lvulkan",
        "VK_ICD_FILENAMES=/etc/vulkan/icd.d/pdocker-android.json",
        "PDOCKER_GPU_QUEUE_SOCKET=/run/pdocker-gpu/pdocker-gpu.sock",
        "pdocker-vulkan-icd bridge log observed",
        "storageImageMaxErr within tolerance",
        "executor backend_impl android_vulkan valid true",
    ],
    "exit_code": rc,
}
out.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
PY
}

fail_artifact() {
  local reason="$1" rc="${2:-1}"
  json_artifact false "$reason" "$rc"
  echo "android-vulkan-icd-device-socket-smoke: $reason" >&2
  exit "$rc"
}

remote_quote() {
  printf "'%s'" "$(printf "%s" "$1" | sed "s/'/'\\\\''/g")"
}

run_adb() {
  "$ADB" "$@"
}

run_as() {
  run_adb shell "run-as $PKG sh -c $(remote_quote "$1")"
}

docker_cmd() {
  local cmd="$1"
  run_as "cd files && export PATH=\"\$PWD/pdocker-runtime/docker-bin:\$PATH\" DOCKER_CONFIG=\"\$PWD/pdocker-runtime/docker-bin\" DOCKER_HOST=\"unix://\$PWD/pdocker/pdockerd.sock\" DOCKER_BUILDKIT=0 COMPOSE_DOCKER_CLI_BUILD=0 BUILDKIT_PROGRESS=plain COMPOSE_PROGRESS=plain COMPOSE_MENU=false && $cmd"
}

stage_test_cli_if_needed() {
  if run_as 'test -x files/pdocker-runtime/docker-bin/docker' >/dev/null 2>&1; then
    return 0
  fi
  local docker_bin="$ROOT/docker-proot-setup/docker-bin/docker"
  local compose_bin="$ROOT/vendor/lib/docker-compose"
  [[ -x "$docker_bin" ]] || fail_artifact "test Docker CLI missing and not staged in app files" 2
  run_adb push "$docker_bin" /data/local/tmp/skydnir-test-docker >/dev/null
  if [[ -x "$compose_bin" ]]; then
    run_adb push "$compose_bin" /data/local/tmp/skydnir-test-docker-compose >/dev/null
    run_as 'mkdir -p files/pdocker-runtime/docker-bin/cli-plugins; cp /data/local/tmp/skydnir-test-docker files/pdocker-runtime/docker-bin/docker; cp /data/local/tmp/skydnir-test-docker-compose files/pdocker-runtime/docker-bin/cli-plugins/docker-compose; chmod 755 files/pdocker-runtime/docker-bin/docker files/pdocker-runtime/docker-bin/cli-plugins/docker-compose'
  else
    run_as 'mkdir -p files/pdocker-runtime/docker-bin; cp /data/local/tmp/skydnir-test-docker files/pdocker-runtime/docker-bin/docker; chmod 755 files/pdocker-runtime/docker-bin/docker'
  fi
}

generate_client_source() {
  python3 - "$ROOT" "$TMP/skydnir-vk-storage-image-smoke.c" <<'PY'
import re
import sys
from pathlib import Path
root = Path(sys.argv[1])
out = Path(sys.argv[2])
script = (root / "scripts/test/smoke-vulkan-icd-storage-image.sh").read_text()
match = re.search(r"cat >\"\$TMP/pdocker-vk-storage-image-smoke\.c\" <<'C'\n(?P<body>.*?)\nC\n\npython3 -", script, re.S)
if not match:
    raise SystemExit("storage image smoke C heredoc not found")
source = match.group("body")
executor = (root / "app/src/main/cpp/pdocker_gpu_executor.c").read_text()
spv = re.search(r"static const uint32_t kStorageImageRoundtripSpv\[\] = \{(?P<body>.*?)\n\};", executor, re.S)
if not spv:
    raise SystemExit("kStorageImageRoundtripSpv not found")
source = source.replace('#include "pdocker-storage-image-roundtrip-spv.inc"', spv.group("body").strip())
out.write_text(source + "\n")
PY
}

run_adb devices >"$TMP/adb-devices.txt" 2>&1 || fail_artifact "adb devices failed" 1
run_adb get-serialno >"$TMP/adb-serial.txt" 2>/dev/null || true
if ! run_adb get-state >/dev/null 2>&1; then
  fail_artifact "no connected adb device" 1
fi
run_adb shell am start -n "$PKG/$CLASS_PREFIX.MainActivity" >/dev/null 2>&1 || true

for _ in $(seq 1 30); do
  if run_as 'test -S files/pdocker-runtime/gpu/pdocker-gpu.sock' >/dev/null 2>&1; then
    echo present >"$TMP/app-socket.txt"
    break
  fi
  sleep 1
done
[[ -s "$TMP/app-socket.txt" ]] || fail_artifact "app GPU executor socket missing" 1

run_as 'files/pdocker-runtime/gpu/pdocker-gpu-executor --bench-vulkan-storage-image-roundtrip' >"$TMP/direct-preflight.txt" 2>&1 || \
  fail_artifact "direct Android Vulkan storage-image preflight failed" 1

stage_test_cli_if_needed

docker_cmd 'docker ps --format "{{.ID}} {{.Names}} {{.Status}}"' >"$TMP/docker-ps.txt" 2>&1 || \
  fail_artifact "docker ps failed through app runtime" 1
if [[ -z "$CONTAINER" ]]; then
  CONTAINER="$(awk 'NF {print $1; exit}' "$TMP/docker-ps.txt")"
fi
[[ -n "$CONTAINER" ]] || fail_artifact "no running container available for guest ICD smoke" 1

generate_client_source
run_adb push "$TMP/skydnir-vk-storage-image-smoke.c" /data/local/tmp/skydnir-vk-storage-image-smoke.c >/dev/null
run_adb push "$ROOT/docker-proot-setup/lib/pdocker-vulkan-icd.so" /data/local/tmp/skydnir-pdocker-vulkan-icd.so >/dev/null
run_as 'mkdir -p files/pdocker/tmp/vulkan-icd-device-socket; cp /data/local/tmp/skydnir-vk-storage-image-smoke.c files/pdocker/tmp/vulkan-icd-device-socket/client.c; cp /data/local/tmp/skydnir-pdocker-vulkan-icd.so files/pdocker/tmp/vulkan-icd-device-socket/pdocker-vulkan-icd.so; chmod 644 files/pdocker/tmp/vulkan-icd-device-socket/client.c files/pdocker/tmp/vulkan-icd-device-socket/pdocker-vulkan-icd.so'

docker_cmd "docker cp pdocker/tmp/vulkan-icd-device-socket/client.c $CONTAINER:/tmp/skydnir-vk-storage-image-smoke.c" >/dev/null 2>"$TMP/docker-cp-client.err" || \
  fail_artifact "docker cp smoke source into container failed" 1
docker_cmd "docker exec $CONTAINER sh -lc 'mkdir -p /usr/local/lib /etc/vulkan/icd.d /run/pdocker-gpu'" >/dev/null 2>"$TMP/guest-prereq.txt" || \
  fail_artifact "guest setup directories failed" 1
docker_cmd "docker cp pdocker/tmp/vulkan-icd-device-socket/pdocker-vulkan-icd.so $CONTAINER:/usr/local/lib/pdocker-vulkan-icd.so" >/dev/null 2>"$TMP/docker-cp-icd.err" || \
  fail_artifact "docker cp ICD into container failed" 1

docker_cmd "docker exec $CONTAINER sh -lc 'command -v cc; test -e /usr/include/vulkan/vulkan.h; test -e /usr/lib/aarch64-linux-gnu/libvulkan.so.1 -o -e /usr/lib/libvulkan.so.1 -o -e /lib/aarch64-linux-gnu/libvulkan.so.1; test -S /run/pdocker-gpu/pdocker-gpu.sock'" >"$TMP/guest-prereq.txt" 2>&1 || \
  fail_artifact "guest lacks cc/vulkan headers/libvulkan/socket prerequisites" 1

docker_cmd "docker exec $CONTAINER sh -lc 'printf %s '\''{\"file_format_version\":\"1.0.0\",\"ICD\":{\"library_path\":\"/usr/local/lib/pdocker-vulkan-icd.so\",\"api_version\":\"1.2.0\"}}'\'' > /etc/vulkan/icd.d/pdocker-android.json; chmod 755 /usr/local/lib/pdocker-vulkan-icd.so; cc /tmp/skydnir-vk-storage-image-smoke.c -o /tmp/skydnir-vk-storage-image-smoke -lvulkan -lm; VK_ICD_FILENAMES=/etc/vulkan/icd.d/pdocker-android.json PDOCKER_GPU_QUEUE_SOCKET=/run/pdocker-gpu/pdocker-gpu.sock PDOCKER_VULKAN_ICD_TRACE_ALLOC=1 PDOCKER_GPU_DISPATCH_PROFILE_LOG=1 /tmp/skydnir-vk-storage-image-smoke'" >"$TMP/guest-run.out" 2>"$TMP/guest-run.err" || \
  fail_artifact "guest Vulkan ICD storage-image smoke failed" 1

if ! grep -q 'storageImageMaxErr=' "$TMP/guest-run.out"; then
  fail_artifact "guest smoke missing storageImageMaxErr" 1
fi
if ! grep -q 'pdocker-vulkan-icd' "$TMP/guest-run.err"; then
  fail_artifact "guest smoke missing pdocker-vulkan-icd bridge stderr" 1
fi
json_artifact true "passed" 0
echo "wrote $OUT"

#!/usr/bin/env bash
# Device gate runner for the glibc Vulkan ICD -> app GPU executor socket path.
# This intentionally does not use the host Vulkan loader. It stages a tiny
# storage-image workload and a generic P0 API smoke into an already-running
# Skydnir container, then requires the guest loader/ICD/socket path and exact
# executor response stages to be observable from inside that container.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ADB="${ADB:-adb}"
PKG="${SKYDNIR_PACKAGE:-${PDOCKER_PACKAGE:-io.github.ryo100794.pdocker.compat}}"
CLASS_PREFIX="${SKYDNIR_CLASS_PREFIX:-${PDOCKER_CLASS_PREFIX:-io.github.ryo100794.pdocker}}"
OUT="${SKYDNIR_VULKAN_ICD_DEVICE_SOCKET_OUT:-$ROOT/docs/test/vulkan-icd-device-socket-latest.json}"
OUT="$(python3 -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).expanduser().resolve(strict=False))' "$OUT")"
CONTAINER="${SKYDNIR_VULKAN_ICD_CONTAINER:-${PDOCKER_VULKAN_ICD_CONTAINER:-}}"
P0_SOURCE="$ROOT/tests/device/skydnir-vulkan-p0-smoke.c"
RUNNER_SOURCE="$ROOT/scripts/test/android-vulkan-icd-device-socket-smoke.sh"
ICD_SOURCE="$ROOT/docker-proot-setup/lib/pdocker-vulkan-icd.so"
TIMEOUT_SECONDS="${SKYDNIR_VULKAN_ICD_TIMEOUT_SECONDS:-180}"
TIMEOUT_KILL_AFTER_SECONDS="${SKYDNIR_VULKAN_ICD_TIMEOUT_KILL_AFTER_SECONDS:-10}"
CONTROL_TIMEOUT_SECONDS="${SKYDNIR_VULKAN_ICD_CONTROL_TIMEOUT_SECONDS:-30}"
RUN_ID="$(python3 -c 'import uuid; print(uuid.uuid4())')"
GIT_COMMIT="$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || true)"
TMP="$(mktemp -d)"
mkdir -p "$(dirname "$OUT")"
CANDIDATE_OUT="$(dirname "$OUT")/.${OUT##*/}.candidate.$RUN_ID"
RUNNER_EVIDENCE="$TMP/runner-source.sh"
P0_EVIDENCE="$TMP/p0-source.c"
ICD_EVIDENCE="$TMP/pdocker-vulkan-icd.so"
STORAGE_SOURCE="$TMP/skydnir-vk-storage-image-smoke.c"
LOCK_FILE="${TMPDIR:-/tmp}/skydnir-vulkan-device-gate.lock"
ARTIFACT_TERMINAL=0
CURRENT_PHASE="initialization"

write_json_artifact() {
  local target="$1" success="$2" reason="$3" rc="$4"
  python3 - "$target" "$success" "$reason" "$rc" "$TMP" "$PKG" "$CONTAINER" \
    "$RUN_ID" "$GIT_COMMIT" "$RUNNER_EVIDENCE" "$P0_EVIDENCE" "$ICD_EVIDENCE" \
    "$STORAGE_SOURCE" "$TIMEOUT_SECONDS" "$TIMEOUT_KILL_AFTER_SECONDS" \
    "$CONTROL_TIMEOUT_SECONDS" <<'PY'
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
out = Path(sys.argv[1])
success = sys.argv[2] == "true"
reason = sys.argv[3]
rc = int(sys.argv[4])
tmp = Path(sys.argv[5])
pkg = sys.argv[6]
container = sys.argv[7]
run_id = sys.argv[8]
git_commit = sys.argv[9]
runner = Path(sys.argv[10])
p0_source = Path(sys.argv[11])
icd_source = Path(sys.argv[12])
storage_source = Path(sys.argv[13])
def parse_positive_int(value):
    try:
        parsed = int(value)
    except ValueError:
        return value
    return parsed

timeout_seconds = parse_positive_int(sys.argv[14])
timeout_kill_after_seconds = parse_positive_int(sys.argv[15])
control_timeout_seconds = parse_positive_int(sys.argv[16])

def read(name):
    p = tmp / name
    return p.read_text(errors="replace") if p.exists() else ""

def sha256(path):
    path = Path(path)
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def recorded_sha256(name):
    fields = read(name).strip().split()
    if not fields:
        return None
    value = fields[0].lower()
    if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
        return None
    return value

artifact = {
    "schema": "skydnir.vulkan.icd.device-socket.v1",
    "success": success,
    "state": "passed" if success else ("in_progress" if reason == "in_progress" else "failed"),
    "reason": reason,
    "run_id": run_id,
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
        "p0_compile_stdout": read("p0-compile.out"),
        "p0_compile_stderr": read("p0-compile.err"),
        "p0_run_stdout": read("p0-run.out"),
        "p0_run_stderr": read("p0-run.err"),
        "guest_run_timeout": read("guest-run.timeout"),
        "p0_compile_timeout": read("p0-compile.timeout"),
        "p0_run_timeout": read("p0-run.timeout"),
        "promotion_verifier_stdout": read("promotion-verifier.out"),
        "promotion_verifier_stderr": read("promotion-verifier.err"),
        "product_icd_manifest": read("product-icd-manifest.json"),
        "guest_icd_manifest": read("guest-icd-manifest-readback.json"),
        "package_path": read("package-path.txt"),
        "app_apk_sha256": read("app-apk-sha256.txt"),
        "app_gpu_executor_sha256": read("app-gpu-executor-sha256.txt"),
        "container_inspect": read("container-inspect.json"),
        "container_image_id": read("container-image-id.txt"),
        "guest_icd_sha256": read("guest-icd-sha256.txt"),
        "guest_p0_source_sha256": read("guest-p0-source-sha256.txt"),
        "guest_storage_source_sha256": read("guest-storage-source-sha256.txt"),
    },
    "provenance": {
        "git_commit": git_commit,
        "hash_algorithm": "sha256",
        "runner_sha256": sha256(runner),
        "p0_source_sha256": sha256(p0_source),
        "staged_icd_sha256": sha256(icd_source),
        "storage_source_sha256": sha256(storage_source),
        "product_icd_manifest_sha256": sha256(tmp / "product-icd-manifest.json"),
        "guest_icd_manifest_sha256": sha256(tmp / "guest-icd-manifest-readback.json"),
        "app_apk_sha256": recorded_sha256("app-apk-sha256.txt"),
        "app_gpu_executor_sha256": recorded_sha256("app-gpu-executor-sha256.txt"),
        "guest_staged_icd_sha256": recorded_sha256("guest-icd-sha256.txt"),
        "guest_p0_source_sha256": recorded_sha256("guest-p0-source-sha256.txt"),
        "guest_storage_source_sha256": recorded_sha256("guest-storage-source-sha256.txt"),
        "container_image_id": read("container-image-id.txt").strip(),
    },
    "timeouts": {
        "command_seconds": timeout_seconds,
        "kill_after_seconds": timeout_kill_after_seconds,
        "control_seconds": control_timeout_seconds,
    },
    "promotion_requirements": [
        "guest/container glibc Vulkan loader, not host -lvulkan",
        "VK_ICD_FILENAMES=/etc/vulkan/icd.d/pdocker-android.json",
        "PDOCKER_GPU_QUEUE_SOCKET=/run/pdocker-gpu/pdocker-gpu.sock",
        "pdocker-vulkan-icd bridge log observed",
        "storageImageMaxErr within tolerance",
        "generic P0 query/synchronization2/idle/WSI JSON success true",
        "executor backend_impl android_vulkan valid true",
    ],
    "exit_code": rc,
}
payload = json.dumps(artifact, indent=2, sort_keys=True) + "\n"
out.parent.mkdir(parents=True, exist_ok=True)
temp_name = None
try:
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=out.parent,
        prefix=f".{out.name}.{run_id}.",
        suffix=".tmp",
        delete=False,
    ) as stream:
        temp_name = stream.name
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp_name, out)
    temp_name = None
    directory_fd = os.open(out.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
finally:
    if temp_name is not None:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
PY
}

json_artifact() {
  write_json_artifact "$OUT" "$1" "$2" "$3"
}

fail_artifact() {
  local reason="$1" rc="${2:-1}"
  json_artifact false "$reason" "$rc"
  ARTIFACT_TERMINAL=1
  echo "android-vulkan-icd-device-socket-smoke: $reason" >&2
  exit "$rc"
}

remote_group_cleanup_script() {
  cat <<'SH'
groups=""
for stat in /proc/[0-9]*/stat; do
  pid="${stat#/proc/}"
  pid="${pid%/stat}"
  cmdline="/proc/$pid/cmdline"
  [ -r "$cmdline" ] || continue
  command_line="$(tr '\000' ' ' <"$cmdline" 2>/dev/null)" || continue
  case "$command_line" in
    *[s]kydnir-gate-*)
      stat_line="$(cat "$stat" 2>/dev/null)" || continue
      stat_tail="${stat_line##*) }"
      set -- $stat_tail
      pgid="${3:-}"
      case "$pgid" in
        ""|*[!0-9]*|0|1) continue ;;
      esac
      groups="$groups $pgid"
      ;;
  esac
done
[ -n "$groups" ] || exit 0
for pgid in $groups; do
  kill -TERM "-$pgid" 2>/dev/null || true
done
sleep 1
for pgid in $groups; do
  kill -KILL "-$pgid" 2>/dev/null || true
done
true
SH
}

cleanup_app_gate_processes() {
  local cleanup_command
  cleanup_command="$(remote_group_cleanup_script)"
  timeout --signal=KILL 5s "$ADB" shell \
    "run-as $PKG sh -c $(remote_quote "$cleanup_command")" \
    >/dev/null 2>&1
}

cleanup_container_gate_processes() {
  [[ -n "$CONTAINER" ]] || return 0
  local cleanup_command payload
  cleanup_command="$(remote_group_cleanup_script)"
  payload="$(docker_payload "docker exec $CONTAINER sh -lc $(remote_quote "$cleanup_command")")"
  timeout --signal=KILL 5s "$ADB" shell \
    "run-as $PKG sh -c $(remote_quote "$payload")" \
    >/dev/null 2>&1
}

cleanup_on_exit() {
  local rc=$?
  local final_rc="$rc"
  trap - EXIT
  local cleanup_ok=1
  cleanup_container_gate_processes || cleanup_ok=0
  cleanup_app_gate_processes || cleanup_ok=0
  if [[ "$final_rc" -eq 0 && "$cleanup_ok" -ne 1 ]]; then
    final_rc=1
    ARTIFACT_TERMINAL=0
    CURRENT_PHASE="remote process-group cleanup"
  fi
  if [[ "$ARTIFACT_TERMINAL" -ne 1 ]]; then
    if [[ "$final_rc" -eq 0 ]]; then
      final_rc=1
    fi
    write_json_artifact "$OUT" false \
      "unexpected runner exit during $CURRENT_PHASE" "$final_rc" >/dev/null 2>&1 || true
  fi
  rm -rf "$TMP"
  rm -f "$CANDIDATE_OUT"
  exit "$final_rc"
}

remote_quote() {
  printf "'%s'" "$(printf "%s" "$1" | sed "s/'/'\\\\''/g")"
}

run_adb() {
  timeout --foreground --signal=TERM --kill-after="${TIMEOUT_KILL_AFTER_SECONDS}s" \
    "${CONTROL_TIMEOUT_SECONDS}s" "$ADB" "$@"
}

run_adb_operation() {
  timeout --foreground --signal=TERM --kill-after="${TIMEOUT_KILL_AFTER_SECONDS}s" \
    "${HOST_OPERATION_TIMEOUT_SECONDS}s" "$ADB" "$@"
}

marked_control_payload() {
  printf '%s' "$1; skydnir_gate_rc=\$?; :; exit \$skydnir_gate_rc"
}

run_as() {
  local payload
  payload="$(marked_control_payload "$1")"
  run_adb shell \
    "run-as $PKG setsid sh -c $(remote_quote "$payload") $(remote_quote "skydnir-gate-$RUN_ID")"
}

run_as_operation() {
  local payload
  payload="$(marked_control_payload "$1")"
  run_adb_operation shell \
    "run-as $PKG setsid sh -c $(remote_quote "$payload") $(remote_quote "skydnir-gate-$RUN_ID")"
}

docker_payload() {
  local cmd="$1"
  printf '%s' "cd files && export PATH=\"\$PWD/pdocker-runtime/docker-bin:\$PATH\" DOCKER_CONFIG=\"\$PWD/pdocker-runtime/docker-bin\" DOCKER_HOST=\"unix://\$PWD/pdocker/pdockerd.sock\" DOCKER_BUILDKIT=0 COMPOSE_DOCKER_CLI_BUILD=0 BUILDKIT_PROGRESS=plain COMPOSE_PROGRESS=plain COMPOSE_MENU=false && $cmd"
}

docker_cmd() {
  run_as "$(docker_payload "$1")"
}

docker_cmd_with_timeout() {
  local payload
  payload="$(marked_control_payload "$(docker_payload "$1")")"
  run_adb_operation shell \
    "run-as $PKG setsid sh -c $(remote_quote "$payload") $(remote_quote "skydnir-gate-$RUN_ID")"
}

run_timed_docker_capture() {
  local label="$1" stdout="$2" stderr="$3" cmd="$4" rc timed_out=false
  docker_cmd_with_timeout "$cmd" >"$stdout" 2>"$stderr"
  rc=$?
  if [[ "$rc" -eq 124 || "$rc" -eq 137 ]]; then
    timed_out=true
  fi
  printf 'timed_out=%s exit_code=%s timeout_seconds=%s kill_after_seconds=%s\n' \
    "$timed_out" "$rc" "$TIMEOUT_SECONDS" "$TIMEOUT_KILL_AFTER_SECONDS" >"$TMP/$label.timeout"
  return "$rc"
}

stage_test_cli_if_needed() {
  if run_as 'test -x files/pdocker-runtime/docker-bin/docker' >/dev/null 2>&1; then
    return 0
  fi
  local docker_bin="$ROOT/docker-proot-setup/docker-bin/docker"
  local compose_bin="$ROOT/vendor/lib/docker-compose"
  [[ -x "$docker_bin" ]] || fail_artifact "test Docker CLI missing and not staged in app files" 2
  run_adb push "$docker_bin" /data/local/tmp/skydnir-test-docker >/dev/null || return 1
  if [[ -x "$compose_bin" ]]; then
    run_adb push "$compose_bin" /data/local/tmp/skydnir-test-docker-compose >/dev/null || return 1
    run_as 'mkdir -p files/pdocker-runtime/docker-bin/cli-plugins; cp /data/local/tmp/skydnir-test-docker files/pdocker-runtime/docker-bin/docker; cp /data/local/tmp/skydnir-test-docker-compose files/pdocker-runtime/docker-bin/cli-plugins/docker-compose; chmod 755 files/pdocker-runtime/docker-bin/docker files/pdocker-runtime/docker-bin/cli-plugins/docker-compose' || return 1
  else
    run_as 'mkdir -p files/pdocker-runtime/docker-bin; cp /data/local/tmp/skydnir-test-docker files/pdocker-runtime/docker-bin/docker; chmod 755 files/pdocker-runtime/docker-bin/docker' || return 1
  fi
}

generate_client_source() {
  python3 - "$ROOT" "$STORAGE_SOURCE" <<'PY'
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


command -v flock >/dev/null 2>&1 || {
  echo "android-vulkan-icd-device-socket-smoke: host flock command missing" >&2
  rm -rf "$TMP"
  exit 2
}
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "android-vulkan-icd-device-socket-smoke: another run owns $OUT" >&2
  rm -rf "$TMP"
  exit 75
fi
trap cleanup_on_exit EXIT

# Invalidate any prior published success before touching the device. Every later
# state transition also uses a same-directory temporary file plus os.replace().
json_artifact false "in_progress" 0
[[ "$TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]] || fail_artifact "invalid SKYDNIR_VULKAN_ICD_TIMEOUT_SECONDS" 2
[[ "$TIMEOUT_KILL_AFTER_SECONDS" =~ ^[1-9][0-9]*$ ]] || fail_artifact "invalid SKYDNIR_VULKAN_ICD_TIMEOUT_KILL_AFTER_SECONDS" 2
[[ "$CONTROL_TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]] || fail_artifact "invalid SKYDNIR_VULKAN_ICD_CONTROL_TIMEOUT_SECONDS" 2
command -v timeout >/dev/null 2>&1 || fail_artifact "host timeout command missing" 2
[[ -f "$P0_SOURCE" ]] || fail_artifact "generic Vulkan P0 smoke source missing" 2
[[ -f "$ICD_SOURCE" ]] || fail_artifact "staged Vulkan ICD missing" 2
HOST_OPERATION_TIMEOUT_SECONDS=$((TIMEOUT_SECONDS + TIMEOUT_KILL_AFTER_SECONDS + 5))

CURRENT_PHASE="snapshot gate inputs"
cp "$RUNNER_SOURCE" "$RUNNER_EVIDENCE" || fail_artifact "cannot snapshot gate runner" 2
cp "$P0_SOURCE" "$P0_EVIDENCE" || fail_artifact "cannot snapshot generic P0 source" 2
cp "$ICD_SOURCE" "$ICD_EVIDENCE" || fail_artifact "cannot snapshot staged Vulkan ICD" 2
generate_client_source || fail_artifact "cannot generate storage-image smoke source" 2

CURRENT_PHASE="discover device"
run_adb devices >"$TMP/adb-devices.txt" 2>&1 || fail_artifact "adb devices failed" 1
run_adb get-serialno >"$TMP/adb-serial.txt" 2>/dev/null || true
if ! run_adb get-state >/dev/null 2>&1; then
  fail_artifact "no connected adb device" 1
fi
run_adb shell "run-as $PKG sh -c 'command -v setsid >/dev/null'"   >/dev/null 2>&1 || fail_artifact "app run-as context lacks setsid" 1
run_adb shell am start -n "$PKG/$CLASS_PREFIX.MainActivity" >/dev/null 2>&1 || true

CURRENT_PHASE="wait for app GPU socket"
for _ in $(seq 1 30); do
  if run_as 'test -S files/pdocker-runtime/gpu/pdocker-gpu.sock' >/dev/null 2>&1; then
    echo present >"$TMP/app-socket.txt"
    break
  fi
  sleep 1
done
[[ -s "$TMP/app-socket.txt" ]] || fail_artifact "app GPU executor socket missing" 1

# Recover a prior run whose local ADB watchdog fired before its remote shell
# observed disconnect. The global flock ensures this cannot target a live gate.
cleanup_app_gate_processes

CURRENT_PHASE="capture installed runtime provenance"
run_as 'command -v timeout >/dev/null && command -v sha256sum >/dev/null && sha256sum files/pdocker-runtime/gpu/pdocker-gpu-executor' \
  >"$TMP/app-gpu-executor-sha256.txt" 2>"$TMP/app-gpu-executor-sha256.err" || \
  fail_artifact "installed GPU executor provenance unavailable" 1
run_adb shell pm path "$PKG" >"$TMP/package-path.txt" 2>"$TMP/package-path.err" || \
  fail_artifact "installed package path unavailable" 1
APK_PATH="$(python3 - "$TMP/package-path.txt" <<'PY'
import sys
from pathlib import Path
for line in Path(sys.argv[1]).read_text(errors="replace").splitlines():
    if line.startswith("package:"):
        print(line.removeprefix("package:").strip())
        break
PY
)"
[[ -n "$APK_PATH" ]] || fail_artifact "installed APK path missing" 1
run_adb shell "sha256sum $(remote_quote "$APK_PATH")" \
  >"$TMP/app-apk-sha256.txt" 2>"$TMP/app-apk-sha256.err" || \
  fail_artifact "installed APK provenance unavailable" 1

CURRENT_PHASE="direct Android Vulkan preflight"
run_as_operation "timeout -s KILL ${TIMEOUT_SECONDS}s sh -c 'exec files/pdocker-runtime/gpu/pdocker-gpu-executor --bench-vulkan-storage-image-roundtrip' skydnir-gate-$RUN_ID" >"$TMP/direct-preflight.txt" 2>&1 || \
  fail_artifact "direct Android Vulkan storage-image preflight failed" 1

CURRENT_PHASE="stage test CLI"
stage_test_cli_if_needed || fail_artifact "test Docker CLI staging failed" 1

CURRENT_PHASE="select running container"
docker_cmd 'docker ps --format "{{.ID}} {{.Names}} {{.Status}}"' >"$TMP/docker-ps.txt" 2>&1 || \
  fail_artifact "docker ps failed through app runtime" 1
if [[ -z "$CONTAINER" ]]; then
  CONTAINER="$(awk 'NF {print $1; exit}' "$TMP/docker-ps.txt")"
fi
[[ -n "$CONTAINER" ]] || fail_artifact "no running container available for guest ICD smoke" 1

# Long guest commands carry the same marker in argv, so a subsequent locked run
# can reap work left behind by a lost ADB connection.
cleanup_container_gate_processes

docker_cmd "docker inspect $CONTAINER" >"$TMP/container-inspect.json" 2>"$TMP/container-inspect.err" || \
  fail_artifact "container inspect failed" 1
python3 - "$TMP/container-inspect.json" "$TMP/container-image-id.txt" <<'PY' || \
  fail_artifact "container image identity missing from inspect" 1
import json
import sys
from pathlib import Path
data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if isinstance(data, list):
    if len(data) != 1:
        raise SystemExit("expected one inspected container")
    data = data[0]
if not isinstance(data, dict):
    raise SystemExit("inspect root must be an object")
image = data.get("Image")
if not isinstance(image, str) or not image.strip():
    raise SystemExit("inspect Image is missing")
Path(sys.argv[2]).write_text(image.strip() + "\n", encoding="utf-8")
PY

# The running product container owns the manifest contract. Do not synthesize an
# API version here: capture the product-generated manifest and change only the
# guest library path.
docker_cmd "docker exec $CONTAINER sh -lc 'test -f /etc/vulkan/icd.d/pdocker-android.json'" \
  >/dev/null 2>"$TMP/product-icd-manifest-test.err" || \
  fail_artifact "product Vulkan ICD manifest missing" 1
docker_cmd "docker exec $CONTAINER sh -lc 'cat /etc/vulkan/icd.d/pdocker-android.json'" \
  >"$TMP/product-icd-manifest.json" 2>"$TMP/product-icd-manifest.err" || \
  fail_artifact "product Vulkan ICD manifest unreadable" 1
python3 - "$TMP/product-icd-manifest.json" "$TMP/guest-icd-manifest.json" \
  2>"$TMP/product-icd-manifest-validate.err" <<'PY' || \
  fail_artifact "product Vulkan ICD manifest is invalid" 1
import json
import sys
from pathlib import Path
source = Path(sys.argv[1])
target = Path(sys.argv[2])
data = json.loads(source.read_text(encoding="utf-8"))
if not isinstance(data, dict) or not isinstance(data.get("file_format_version"), str):
    raise SystemExit("missing file_format_version")
icd = data.get("ICD")
if not isinstance(icd, dict):
    raise SystemExit("missing ICD object")
if icd.get("library_path") != "/usr/local/lib/pdocker-vulkan-icd.so":
    raise SystemExit("unexpected product ICD.library_path")
if not isinstance(icd.get("api_version"), str) or not icd["api_version"]:
    raise SystemExit("missing ICD.api_version")
# Preserve every product field and its api_version. This deterministic assignment
# documents that the only path eligible for rewriting is the established guest path.
icd["library_path"] = "/usr/local/lib/pdocker-vulkan-icd.so"
target.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

CURRENT_PHASE="stage immutable gate inputs"
run_adb push "$STORAGE_SOURCE" /data/local/tmp/skydnir-vk-storage-image-smoke.c >/dev/null || fail_artifact "storage-image source push failed" 1
run_adb push "$P0_EVIDENCE" /data/local/tmp/skydnir-vulkan-p0-smoke.c >/dev/null || fail_artifact "generic P0 source push failed" 1
run_adb push "$ICD_EVIDENCE" /data/local/tmp/skydnir-pdocker-vulkan-icd.so >/dev/null || fail_artifact "Vulkan ICD push failed" 1
run_adb push "$TMP/guest-icd-manifest.json" /data/local/tmp/skydnir-product-vulkan-icd.json >/dev/null || fail_artifact "ICD manifest push failed" 1
run_as 'mkdir -p files/pdocker/tmp/vulkan-icd-device-socket; cp /data/local/tmp/skydnir-vk-storage-image-smoke.c files/pdocker/tmp/vulkan-icd-device-socket/client.c; cp /data/local/tmp/skydnir-vulkan-p0-smoke.c files/pdocker/tmp/vulkan-icd-device-socket/p0.c; cp /data/local/tmp/skydnir-pdocker-vulkan-icd.so files/pdocker/tmp/vulkan-icd-device-socket/pdocker-vulkan-icd.so; cp /data/local/tmp/skydnir-product-vulkan-icd.json files/pdocker/tmp/vulkan-icd-device-socket/pdocker-android.json; chmod 644 files/pdocker/tmp/vulkan-icd-device-socket/client.c files/pdocker/tmp/vulkan-icd-device-socket/p0.c files/pdocker/tmp/vulkan-icd-device-socket/pdocker-vulkan-icd.so files/pdocker/tmp/vulkan-icd-device-socket/pdocker-android.json' || fail_artifact "app-private gate staging failed" 1

docker_cmd "docker cp pdocker/tmp/vulkan-icd-device-socket/client.c $CONTAINER:/tmp/skydnir-vk-storage-image-smoke.c" >/dev/null 2>"$TMP/docker-cp-client.err" || \
  fail_artifact "docker cp storage-image smoke source into container failed" 1
docker_cmd "docker cp pdocker/tmp/vulkan-icd-device-socket/p0.c $CONTAINER:/tmp/skydnir-vulkan-p0-smoke.c" >/dev/null 2>"$TMP/docker-cp-p0.err" || \
  fail_artifact "docker cp generic P0 smoke source into container failed" 1
docker_cmd "docker exec $CONTAINER sh -lc 'mkdir -p /usr/local/lib /etc/vulkan/icd.d /run/pdocker-gpu'" >/dev/null 2>"$TMP/guest-prereq.txt" || \
  fail_artifact "guest setup directories failed" 1
docker_cmd "docker cp pdocker/tmp/vulkan-icd-device-socket/pdocker-vulkan-icd.so $CONTAINER:/usr/local/lib/pdocker-vulkan-icd.so" >/dev/null 2>"$TMP/docker-cp-icd.err" || \
  fail_artifact "docker cp ICD into container failed" 1
docker_cmd "docker cp pdocker/tmp/vulkan-icd-device-socket/pdocker-android.json $CONTAINER:/etc/vulkan/icd.d/pdocker-android.json" >/dev/null 2>"$TMP/docker-cp-manifest.err" || \
  fail_artifact "docker cp product ICD manifest into container failed" 1
docker_cmd "docker exec $CONTAINER cat /etc/vulkan/icd.d/pdocker-android.json" \
  >"$TMP/guest-icd-manifest-readback.json" 2>"$TMP/guest-icd-manifest-readback.err" || \
  fail_artifact "guest ICD manifest readback failed" 1

docker_cmd "docker exec $CONTAINER sh -lc 'command -v cc; command -v timeout; command -v sha256sum; command -v setsid; test -e /usr/include/vulkan/vulkan.h; test -e /usr/lib/aarch64-linux-gnu/libvulkan.so.1 -o -e /usr/lib/libvulkan.so.1 -o -e /lib/aarch64-linux-gnu/libvulkan.so.1; test -S /run/pdocker-gpu/pdocker-gpu.sock'" >"$TMP/guest-prereq.txt" 2>&1 || \
  fail_artifact "guest lacks cc/timeout/sha256sum/setsid/vulkan headers/libvulkan/socket prerequisites" 1
docker_cmd "docker exec $CONTAINER sha256sum /usr/local/lib/pdocker-vulkan-icd.so" >"$TMP/guest-icd-sha256.txt" 2>"$TMP/guest-icd-sha256.err" || fail_artifact "guest ICD hash capture failed" 1
docker_cmd "docker exec $CONTAINER sha256sum /tmp/skydnir-vulkan-p0-smoke.c" >"$TMP/guest-p0-source-sha256.txt" 2>"$TMP/guest-p0-source-sha256.err" || fail_artifact "guest P0 source hash capture failed" 1
docker_cmd "docker exec $CONTAINER sha256sum /tmp/skydnir-vk-storage-image-smoke.c" >"$TMP/guest-storage-source-sha256.txt" 2>"$TMP/guest-storage-source-sha256.err" || fail_artifact "guest storage source hash capture failed" 1

CURRENT_PHASE="run storage-image guest gate"
GUEST_SMOKE_CMD="docker exec $CONTAINER setsid timeout -s KILL ${TIMEOUT_SECONDS}s sh -c 'chmod 755 /usr/local/lib/pdocker-vulkan-icd.so; cc /tmp/skydnir-vk-storage-image-smoke.c -o /tmp/skydnir-vk-storage-image-smoke -lvulkan -lm; exec env VK_ICD_FILENAMES=/etc/vulkan/icd.d/pdocker-android.json PDOCKER_GPU_QUEUE_SOCKET=/run/pdocker-gpu/pdocker-gpu.sock PDOCKER_VULKAN_ICD_TRACE_ALLOC=1 PDOCKER_GPU_DISPATCH_PROFILE_LOG=1 /tmp/skydnir-vk-storage-image-smoke' skydnir-gate-$RUN_ID"
if run_timed_docker_capture "guest-run" "$TMP/guest-run.out" "$TMP/guest-run.err" "$GUEST_SMOKE_CMD"; then
  :
else
  rc=$?
  if [[ "$rc" -eq 124 || "$rc" -eq 137 ]]; then
    fail_artifact "guest Vulkan ICD storage-image smoke timed out" "$rc"
  fi
  fail_artifact "guest Vulkan ICD storage-image smoke failed" "$rc"
fi

CURRENT_PHASE="compile generic Vulkan P0 gate"
P0_COMPILE_CMD="docker exec $CONTAINER setsid timeout -s KILL ${TIMEOUT_SECONDS}s sh -c 'exec cc -std=gnu11 -Wall -Wextra -Werror /tmp/skydnir-vulkan-p0-smoke.c -o /tmp/skydnir-vulkan-p0-smoke -lvulkan' skydnir-gate-$RUN_ID"
if run_timed_docker_capture "p0-compile" "$TMP/p0-compile.out" "$TMP/p0-compile.err" "$P0_COMPILE_CMD"; then
  :
else
  rc=$?
  if [[ "$rc" -eq 124 || "$rc" -eq 137 ]]; then
    fail_artifact "guest generic Vulkan P0 smoke compile timed out" "$rc"
  fi
  fail_artifact "guest generic Vulkan P0 smoke compile failed" "$rc"
fi

CURRENT_PHASE="run generic Vulkan P0 gate"
P0_RUN_CMD="docker exec $CONTAINER setsid timeout -s KILL ${TIMEOUT_SECONDS}s sh -c 'exec env VK_ICD_FILENAMES=/etc/vulkan/icd.d/pdocker-android.json PDOCKER_GPU_QUEUE_SOCKET=/run/pdocker-gpu/pdocker-gpu.sock PDOCKER_VULKAN_ADVERTISEMENT_SOURCE=executor PDOCKER_VULKAN_ICD_DEBUG=1 PDOCKER_VULKAN_ICD_TRACE_ALLOC=1 PDOCKER_GPU_DISPATCH_PROFILE_LOG=1 /tmp/skydnir-vulkan-p0-smoke' skydnir-gate-$RUN_ID"
if run_timed_docker_capture "p0-run" "$TMP/p0-run.out" "$TMP/p0-run.err" "$P0_RUN_CMD"; then
  :
else
  rc=$?
  if [[ "$rc" -eq 124 || "$rc" -eq 137 ]]; then
    fail_artifact "guest generic Vulkan P0 smoke timed out" "$rc"
  fi
  fail_artifact "guest generic Vulkan P0 smoke failed" "$rc"
fi

# The published target stays success:false/in_progress while the verifier checks
# a private same-directory candidate. Only an accepted candidate is promoted.
CURRENT_PHASE="verify and promote artifact"
write_json_artifact "$CANDIDATE_OUT" true "passed" 0
if python3 "$ROOT/scripts/test/verify-vulkan-icd-device-socket-artifact.py" "$CANDIDATE_OUT" \
  >"$TMP/promotion-verifier.out" 2>"$TMP/promotion-verifier.err"; then
  # Rebuild the candidate with the verifier's own first-pass output, then verify
  # that exact final byte set before the atomic publication.
  write_json_artifact "$CANDIDATE_OUT" true "passed" 0
  python3 "$ROOT/scripts/test/verify-vulkan-icd-device-socket-artifact.py" \
    "$CANDIDATE_OUT" >"$TMP/promotion-verifier-final.out" \
    2>"$TMP/promotion-verifier-final.err" || \
    fail_artifact "final generated artifact failed the promotion verifier" 1
  python3 - "$CANDIDATE_OUT" "$OUT" <<'PY'
import os
import sys
from pathlib import Path
source = Path(sys.argv[1])
target = Path(sys.argv[2])
os.replace(source, target)
directory_fd = os.open(target.parent, os.O_RDONLY)
try:
    os.fsync(directory_fd)
finally:
    os.close(directory_fd)
PY
  ARTIFACT_TERMINAL=1
else
  rc=$?
  fail_artifact "generated artifact failed the promotion verifier" "$rc"
fi
echo "wrote $OUT"

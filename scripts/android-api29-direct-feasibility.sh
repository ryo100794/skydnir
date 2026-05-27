#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PKG="${SKYDNIR_PACKAGE:-${PDOCKER_PACKAGE:-io.github.ryo100794.pdocker}}"
APK="${SKYDNIR_APK:-${PDOCKER_APK:-$ROOT/app/build/outputs/apk/debug/app-debug.apk}}"
ADB="${ADB:-adb}"
INSTALL=1
SERIAL="${PDOCKER_ADB_SERIAL:-}"

usage() {
  cat <<EOF
Usage: $0 [--no-install]

Runs an API29+ direct-execution feasibility probe on a connected Android device.

This test deliberately separates run-as controls from the real app-domain
Dockerfile RUN path. run-as may execute app-data files even when the app domain
cannot, so only the Docker build result is treated as the product feasibility
signal.

Environment:
  ADB                 adb executable (default: adb)
  PDOCKER_ADB_SERIAL  optional adb serial
  SKYDNIR_PACKAGE     package name (PDOCKER_PACKAGE is still accepted; default: $PKG)
  SKYDNIR_APK         debug APK path (PDOCKER_APK is still accepted; default: $APK)
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-install) INSTALL=0 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

adb_cmd() {
  if [[ -n "$SERIAL" ]]; then
    "$ADB" -s "$SERIAL" "$@"
  else
    "$ADB" "$@"
  fi
}

remote_quote() {
  printf "'%s'" "$(printf "%s" "$1" | sed "s/'/'\\\\''/g")"
}

run_as() {
  adb_cmd shell "run-as $PKG sh -c $(remote_quote "$1")"
}

main_activity() {
  local resolved
  resolved="$(adb_cmd shell cmd package resolve-activity --brief "$PKG" 2>/dev/null | tail -1 | tr -d '\r')"
  if [[ "$resolved" == "$PKG/"* && "$resolved" != *"No activity found"* ]]; then
    printf '%s\n' "$resolved"
  else
    printf '%s/io.github.ryo100794.pdocker.MainActivity\n' "$PKG"
  fi
}

wait_for_socket() {
  local i
  for i in $(seq 1 45); do
    if run_as 'test -S files/pdocker/pdockerd.sock' >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  echo "pdockerd socket did not appear" >&2
  return 1
}

docker_cmd() {
  local cmd="$1"
  run_as "cd files && export PATH=\"\$PWD/pdocker-runtime/docker-bin:\$PATH\" DOCKER_CONFIG=\"\$PWD/pdocker-runtime/docker-bin\" DOCKER_HOST=\"unix://\$PWD/pdocker/pdockerd.sock\" DOCKER_BUILDKIT=0 COMPOSE_DOCKER_CLI_BUILD=0 BUILDKIT_PROGRESS=plain COMPOSE_PROGRESS=plain COMPOSE_MENU=false && $cmd"
}

section() {
  printf '\n== %s ==\n' "$1"
}

section "device"
adb_cmd get-serialno
adb_cmd shell getprop ro.build.version.sdk
adb_cmd shell getprop ro.build.version.release
adb_cmd shell getprop ro.product.model

section "package"
if [[ "$INSTALL" -eq 1 ]]; then
  if [[ ! -f "$APK" ]]; then
    echo "APK not found: $APK" >&2
    exit 1
  fi
  adb_cmd install -r "$APK" >/dev/null
fi
adb_cmd shell dumpsys package "$PKG" | grep -E "targetSdk|seInfo|legacyNativeLibraryDir|codePath" || true

section "start app daemon"
adb_cmd logcat -c >/dev/null 2>&1 || true
adb_cmd shell am force-stop "$PKG" >/dev/null 2>&1 || true
run_as 'rm -f files/pdocker/pdockerd.sock' >/dev/null 2>&1 || true
adb_cmd shell am start -n "$(main_activity)" -a "$PKG.action.SMOKE_START" >/dev/null
wait_for_socket
adb_cmd shell ps -AZ | grep "$PKG" || true

section "engine metadata"
docker_cmd 'docker version'

section "run-as controls"
run_as 'files/pdocker-runtime/docker-bin/pdocker-direct --pdocker-direct-probe'
run_as 'ROOT=$(find files/pdocker/containers -maxdepth 2 -name rootfs -type d | tail -1); echo rootfs=$ROOT; test -n "$ROOT"; ls -l "$ROOT/bin/sh"; readelf -l "$ROOT/bin/sh" 2>/dev/null | grep interpreter || true'
run_as 'rm -f files/pdocker-exec-probe; cp /system/bin/sh files/pdocker-exec-probe; chmod 700 files/pdocker-exec-probe; files/pdocker-exec-probe -c "echo run_as_app_data_exec_ok"; echo run_as_app_data_exec_rc=$?'
run_as 'ROOT=$(find files/pdocker/containers -maxdepth 2 -name rootfs -type d | tail -1); files/pdocker-runtime/docker-bin/pdocker-direct run --mode exec --rootfs "$ROOT" --workdir / --env HOME=/root --env TERM=xterm -- /bin/sh -c "echo helper_loader_ok; /bin/ls / | head -5"; echo helper_loader_rc=$?'
run_as 'ROOT=$(find files/pdocker/containers -maxdepth 2 -name rootfs -type d | tail -1); files/pdocker-runtime/docker-bin/pdocker-direct run --mode exec --rootfs "$ROOT" --workdir / --env PDOCKER_ROOTFS_DEBUG=1 -- /bin/sh -c "ls /data >/dev/null 2>&1; echo rootfs_data_ls_rc=\$?; ls /proc >/dev/null 2>&1; echo proc_ls_rc=\$?"; echo rootfs_mediation_rc=$?'

section "app-domain docker build"
TMP_PROJECT="$(mktemp -d)"
trap 'rm -rf "$TMP_PROJECT"' EXIT
cat > "$TMP_PROJECT/Dockerfile" <<'DOCKERFILE'
FROM ubuntu:22.04
RUN printf 'api29-direct-feasibility\n' > /pdocker-api29.txt
CMD ["/bin/sh", "-lc", "cat /pdocker-api29.txt"]
DOCKERFILE
REMOTE_PROJECT="/data/local/tmp/pdocker-api29-feasibility"
adb_cmd shell "rm -rf '$REMOTE_PROJECT' && mkdir -p '$REMOTE_PROJECT'"
adb_cmd push "$TMP_PROJECT/." "$REMOTE_PROJECT/" >/dev/null
run_as "rm -rf files/pdocker/projects/api29-feasibility && mkdir -p files/pdocker/projects/api29-feasibility && cp -R $REMOTE_PROJECT/. files/pdocker/projects/api29-feasibility/"

set +e
BUILD_OUTPUT="$(docker_cmd "cd pdocker/projects/api29-feasibility && docker build -t local/pdocker-api29-feasibility:latest ." 2>&1)"
BUILD_RC=$?
set -e
printf '%s\n' "$BUILD_OUTPUT"
echo "app_domain_docker_build_rc=$BUILD_RC"
if [[ "$BUILD_RC" -eq 0 ]]; then
  echo "API29_DIRECT_EXEC_FEASIBILITY=PASS"
else
  echo "API29_DIRECT_EXEC_FEASIBILITY=FAIL"
fi

section "recent logs"
adb_cmd logcat -d -t 300 | grep -Ei "sigsys|seccomp|pdocker|pdocker-direct|avc|denied|audit|bad system call|exit code -31" || true

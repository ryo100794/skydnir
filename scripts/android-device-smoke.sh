#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FLAVOR="${PDOCKER_ANDROID_FLAVOR:-compat}"
case "$FLAVOR" in
  compat)
    DEFAULT_PKG="io.github.ryo100794.pdocker.compat"
    DEFAULT_APK="$ROOT/app/build/outputs/apk/compat/debug/app-compat-debug.apk"
    ;;
  modern)
    DEFAULT_PKG="io.github.ryo100794.pdocker"
    DEFAULT_APK="$ROOT/app/build/outputs/apk/modern/debug/app-modern-debug.apk"
    ;;
  *)
    echo "PDOCKER_ANDROID_FLAVOR must be 'compat' or 'modern' (got '$FLAVOR')" >&2
    exit 2
    ;;
esac
PKG="${PDOCKER_PACKAGE:-$DEFAULT_PKG}"
APK="${PDOCKER_APK:-$DEFAULT_APK}"
CLASS_PREFIX="io.github.ryo100794.pdocker"
ACTION_PREFIX="io.github.ryo100794.pdocker"
PROJECT="device-smoke"
MODE="full"
GPU_BENCH=0

usage() {
  cat <<EOF
Usage: $0 [--quick] [--gpu-bench] [--no-install]

Runs a repeatable pdocker Android device smoke through adb + run-as.

Environment:
  ADB               adb executable (default: adb)
  PDOCKER_PACKAGE   Android package (default: $PKG)
  PDOCKER_APK       debug APK path (default: $APK)
  PDOCKER_STAGE_TEST_CLI
                    stage repository Docker CLI/Compose into app files for
                    compatibility tests (default: 1)
  PDOCKER_KEEP_TEST_CLI
                    keep staged test CLI/Compose after the smoke run
                    (default: 0)
  PDOCKER_SMOKE_FORCE_STOP
                    force-stop the app before the smoke run. This kills any
                    running pdocker containers, so it is opt-in (default: 0)

Modes:
  --quick       only install/start pdockerd and run docker version
  --gpu-bench   also run debug-only android-gpu-bench and verify artifacts
  --no-install  skip adb install; useful when the same debug APK is present
EOF
}

ADB="${ADB:-adb}"
INSTALL=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --quick) MODE="quick" ;;
    --gpu-bench) GPU_BENCH=1 ;;
    --no-install) INSTALL=0 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

run_adb() {
  "$ADB" "$@"
}

remote_quote() {
  printf "'%s'" "$(printf "%s" "$1" | sed "s/'/'\\\\''/g")"
}

run_as() {
  run_adb shell "run-as $PKG sh -c $(remote_quote "$1")"
}

stage_test_cli() {
  [[ "${PDOCKER_STAGE_TEST_CLI:-1}" != "0" ]] || return 0
  local docker_bin="$ROOT/docker-proot-setup/docker-bin/docker"
  local compose_bin="$ROOT/vendor/lib/docker-compose"
  if [[ ! -x "$docker_bin" || ! -x "$compose_bin" ]]; then
    echo "test Docker CLI/Compose binaries missing; run backend fetch/build first" >&2
    return 1
  fi
  echo "[pdocker smoke] staging test-only Docker CLI/Compose outside APK"
  run_adb push "$docker_bin" /data/local/tmp/pdocker-test-docker >/dev/null
  run_adb push "$compose_bin" /data/local/tmp/pdocker-test-docker-compose >/dev/null
  run_as "mkdir -p files/pdocker-runtime/docker-bin/cli-plugins && cp /data/local/tmp/pdocker-test-docker files/pdocker-runtime/docker-bin/docker && cp /data/local/tmp/pdocker-test-docker-compose files/pdocker-runtime/docker-bin/cli-plugins/docker-compose && chmod 755 files/pdocker-runtime/docker-bin/docker files/pdocker-runtime/docker-bin/cli-plugins/docker-compose"
}

cleanup_test_cli() {
  [[ "${PDOCKER_STAGE_TEST_CLI:-1}" != "0" ]] || return 0
  [[ "${PDOCKER_KEEP_TEST_CLI:-0}" != "1" ]] || return 0
  run_as "rm -f files/pdocker-runtime/docker-bin/docker files/pdocker-runtime/docker-bin/cli-plugins/docker-compose" >/dev/null 2>&1 || true
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
  run_as "cd files && export PATH=\"\$PWD/pdocker-runtime/docker-bin:\$PATH\" DOCKER_CONFIG=\"\$PWD/pdocker-runtime/docker-bin\" DOCKER_HOST=\"unix://\$PWD/pdocker/pdockerd.sock\" DOCKER_BUILDKIT=0 COMPOSE_DOCKER_CLI_BUILD=0 BUILDKIT_PROGRESS=plain COMPOSE_PROGRESS=plain COMPOSE_MENU=false PDOCKER_DIRECT_TRACE_MODE=seccomp && $cmd"
}

engine_exec_it_smoke() {
  local container_ref="$1"
  run_as "cd files/pdocker || exit 1
create_body='{\"AttachStdin\":true,\"AttachStdout\":true,\"AttachStderr\":true,\"Tty\":true,\"Env\":[\"ENV=\",\"BASH_ENV=\"],\"Cmd\":[\"/bin/sh\",\"-i\"]}'
create_len=\${#create_body}
create_resp=\$({ printf 'POST /containers/$container_ref/exec HTTP/1.1\r\nHost: docker\r\nContent-Type: application/json\r\nContent-Length: %s\r\nConnection: close\r\n\r\n' \"\$create_len\"; printf '%s' \"\$create_body\"; } | nc -U -W 5 pdockerd.sock | tr -d '\r')
exec_id=\$(printf '%s\n' \"\$create_resp\" | sed -n 's/.*\"Id\": \"\([0-9a-f]*\)\".*/\1/p')
test -n \"\$exec_id\"
start_body='{\"Detach\":false,\"Tty\":true}'
start_len=\${#start_body}
exec_out=\$({ printf 'POST /exec/%s/start HTTP/1.1\r\nHost: docker\r\nConnection: Upgrade\r\nUpgrade: tcp\r\nContent-Type: application/json\r\nContent-Length: %s\r\n\r\n' \"\$exec_id\" \"\$start_len\"; printf '%s' \"\$start_body\"; sleep 1; printf '[ \"x\" = \"x\" ]; /usr/bin/[ \"x\" = \"x\" ]; echo pdocker-it-bracket-ok; echo pdocker-it-ok; pwd; exit\r'; } | nc -U -W 10 pdockerd.sock | tr -d '\r')
printf '%s\n' \"\$exec_out\"
printf '%s\n' \"\$exec_out\" | grep -q 'pdocker-it-bracket-ok'
printf '%s\n' \"\$exec_out\" | grep -q 'pdocker-it-ok'
! printf '%s\n' \"\$exec_out\" | grep -Eq '(/usr/bin/)?\[: extra argument'
! printf '%s\n' \"\$exec_out\" | grep -q 'exit\\\\r'
"
}

run_gpu_bench() {
  local bench_dir="files/pdocker/bench"
  echo "[pdocker smoke] android-gpu-bench"
  run_as "rm -rf '$bench_dir' && mkdir -p '$bench_dir'" >/dev/null 2>&1 || true
  run_adb shell am broadcast \
    -n "$PKG/$CLASS_PREFIX.PdockerdDebugReceiver" \
    -a "$ACTION_PREFIX.action.SMOKE_GPU_BENCH" >/dev/null
  local i
  for i in $(seq 1 20); do
    if run_as "ls '$bench_dir'/android-gpu-bench-*.jsonl >/dev/null 2>&1 && ls '$bench_dir'/android-gpu-bench-*.csv >/dev/null 2>&1"; then
      run_as "tail -n 7 '$bench_dir'/android-gpu-bench-*.jsonl"
      return 0
    fi
    sleep 1
  done
  echo "android-gpu-bench artifacts did not appear in $bench_dir" >&2
  return 1
}

run_gpu_executor_bench() {
  echo "[pdocker smoke] gpu executor same-api probes"
  run_as 'files/pdocker-runtime/gpu/pdocker-gpu-executor --bench-vulkan-vector-add 1' | tee /tmp/pdocker-smoke-vulkan-executor.json
  grep -q '"backend_impl":"android_vulkan"' /tmp/pdocker-smoke-vulkan-executor.json
  grep -q '"valid":true' /tmp/pdocker-smoke-vulkan-executor.json
  run_as 'files/pdocker-runtime/gpu/pdocker-gpu-executor --bench-opencl-vector-add 1 || true' | tee /tmp/pdocker-smoke-opencl-executor.json
}

echo "[pdocker smoke] device: $(run_adb get-serialno)"

if [[ "$INSTALL" -eq 1 ]]; then
  if [[ ! -f "$APK" ]]; then
    echo "APK not found: $APK" >&2
    echo "Run ./gradlew :app:assembleDebug first." >&2
    exit 1
  fi
  echo "[pdocker smoke] installing $APK"
  run_adb install -r "$APK" >/dev/null
fi

trap 'cleanup_test_cli || true' EXIT

if [[ "${PDOCKER_SMOKE_FORCE_STOP:-0}" == "1" ]]; then
  echo "[pdocker smoke] force-stopping app; running containers will stop"
  run_adb shell am force-stop "$PKG" >/dev/null 2>&1 || true
  run_as 'rm -f files/pdocker/pdockerd.sock' >/dev/null 2>&1 || true
fi
run_adb shell pm grant "$PKG" android.permission.POST_NOTIFICATIONS >/dev/null 2>&1 || true
run_adb shell am start \
  -n "$PKG/$CLASS_PREFIX.MainActivity" \
  -a "$ACTION_PREFIX.action.SMOKE_START" >/dev/null

wait_for_socket
stage_test_cli

echo "[pdocker smoke] docker version"
docker_cmd 'docker version'

echo "[pdocker smoke] direct executor probe"
run_as 'files/pdocker-runtime/docker-bin/pdocker-direct --pdocker-direct-probe | grep -q "pdocker-direct-executor:1"'
run_as 'files/pdocker-runtime/docker-bin/pdocker-direct --pdocker-direct-probe | grep -Eq "process-exec=(0|1)"'
if [[ "$FLAVOR" == "compat" ]]; then
  echo "[pdocker smoke] compat direct process probe"
  run_as 'PDOCKER_DIRECT_EXPERIMENTAL_PROCESS_EXEC=1 files/pdocker-runtime/docker-bin/pdocker-direct --pdocker-direct-probe | grep -q "process-exec=1"'
  echo "[pdocker smoke] compat memory pager syscall probe"
  run_as 'files/pdocker-runtime/docker-bin/pdocker-direct --pdocker-memory-pager-probe | grep -q "pager-probe:ptrace_path=ok"'
  echo "[pdocker smoke] compat memory pager poc"
  run_as 'files/pdocker-runtime/docker-bin/pdocker-direct --pdocker-memory-pager-poc | grep -q "pager-poc:result=ok"'
  run_as '! test -e files/pdocker-runtime/docker-bin/proot'
else
  run_as 'files/pdocker-runtime/docker-bin/pdocker-direct --pdocker-direct-probe | grep -q "process-exec=0"'
fi

if [[ "$GPU_BENCH" -eq 1 ]]; then
  run_gpu_executor_bench
  run_gpu_bench
fi

if [[ "$MODE" == "quick" ]]; then
  echo "[pdocker smoke] quick mode passed"
  exit 0
fi

echo "[pdocker smoke] creating tiny project"
TMP_PROJECT="$(mktemp -d)"
trap 'rm -rf "$TMP_PROJECT"; cleanup_test_cli || true' EXIT
cat > "$TMP_PROJECT/Dockerfile" <<'EOF'
FROM ubuntu:22.04
RUN printf 'pdocker-smoke-build\n' > /pdocker-smoke.txt
CMD ["/bin/sh", "-lc", "cat /pdocker-smoke.txt && sleep 300"]
EOF
cat > "$TMP_PROJECT/compose.yaml" <<'EOF'
services:
  app:
    build: .
    command: ["/bin/sh", "-lc", "cat /pdocker-smoke.txt && sleep 300"]
EOF
REMOTE_PROJECT="/data/local/tmp/pdocker-$PROJECT"
run_adb shell "rm -rf '$REMOTE_PROJECT' && mkdir -p '$REMOTE_PROJECT'"
run_adb push "$TMP_PROJECT/." "$REMOTE_PROJECT/" >/dev/null
run_as "rm -rf files/pdocker/projects/$PROJECT && mkdir -p files/pdocker/projects/$PROJECT && cp -R $REMOTE_PROJECT/. files/pdocker/projects/$PROJECT/"

echo "[pdocker smoke] docker build"
docker_cmd "cd pdocker/projects/$PROJECT && docker build -t local/pdocker-device-smoke:latest ."

echo "[pdocker smoke] compose up/down"
docker_cmd "cd pdocker/projects/$PROJECT && docker compose down >/dev/null 2>&1 || true && docker rm -f device-smoke-app-1 >/dev/null 2>&1 || true && docker compose up --detach --build && CID=\$(docker compose ps -q app) && test -n \"\$CID\" && printf '%s' \"\$CID\" > .smoke-cid && for i in \$(seq 1 10); do docker compose logs --tail=80 | grep -q pdocker-smoke-build && break; sleep 1; done && docker compose logs --tail=80 | grep -q pdocker-smoke-build && EXEC_OUT=\$(docker exec \"\$CID\" sh -lc 'echo pdocker-exec-ok' 2>&1) && echo \"\$EXEC_OUT\" && echo \"\$EXEC_OUT\" | grep -q pdocker-exec-ok && ! echo \"\$EXEC_OUT\" | grep -q '/vendor/xbin' && BRACKET_OUT=\$(docker exec \"\$CID\" sh -lc '[ \"x\" = \"x\" ]; /usr/bin/[ \"x\" = \"x\" ]; echo pdocker-bracket-ok' 2>&1) && echo \"\$BRACKET_OUT\" && echo \"\$BRACKET_OUT\" | grep -q pdocker-bracket-ok && docker compose ps -a"
CID="$(run_as "cat files/pdocker/projects/$PROJECT/.smoke-cid" | tr -d '\r')"
echo "[pdocker smoke] docker ps filters"
docker_cmd "FILTERED=\$(docker ps -a --filter name=device-smoke-app-1 -q) && test -n \"\$FILTERED\" && case \"$CID\" in \"\$FILTERED\"*) true ;; *) case \"\$FILTERED\" in \"$CID\"*) true ;; *) echo \"filter mismatch expected=$CID actual=\$FILTERED\"; false ;; esac ;; esac && test -z \"\$(docker ps -a --filter name=pdocker-smoke-filter-miss -q)\""
echo "[pdocker smoke] engine exec -it"
engine_exec_it_smoke "$CID"
docker_cmd "cd pdocker/projects/$PROJECT && docker compose down"

echo "[pdocker smoke] checking UI-visible job state path"
run_as 'ls -l files/pdocker/jobs.json >/dev/null 2>&1 || true; ls -ld files/pdocker/projects/device-smoke files/pdocker-runtime'

echo "[pdocker smoke] passed"

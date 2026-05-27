#!/usr/bin/env bash
set -euo pipefail

PKG="${SKYDNIR_ANDROID_PACKAGE:-${SKYDNIR_PACKAGE:-${PDOCKER_ANDROID_PACKAGE:-io.github.ryo100794.pdocker.compat}}}"
ADB="${ADB:-adb}"
PROJECT_ROOT="files"

usage() {
  cat <<'EOF'
Usage: scripts/android-runtime-bench.sh [--apt-update] [--apt-upgrade-dry-run] [--npm-dry-run] [--untraced-stat] [--trace-mode MODE] [--proot-cmd CMD]

Runs repeatable Android-side runtime benchmarks without installing external
runtime code. The direct backend benchmark uses the app's staged
pdocker-direct helper and reports syscall stop counts when supported. Results
are also written under files/pdocker/bench on the Android device.

Options:
  --apt-update       also run slow apt-get update wall-clock benchmark
  --apt-upgrade-dry-run
                     also run apt-get -s upgrade without changing the rootfs
  --npm-dry-run      also run Node/npm filesystem-heavy dry-run benchmark
  --untraced-stat    experimental: let newfstatat/statx run without ptrace
                    rewriting to quantify the stat-path bottleneck
  --trace-mode MODE  direct backend mode: syscall (default) or seccomp
  --proot-cmd CMD    optional existing proot-compatible command to compare;
                    the script does not download or bundle proot
EOF
}

RUN_APT_UPDATE=0
RUN_APT_UPGRADE_DRY_RUN=0
RUN_NPM_DRY_RUN=0
UNTRACED_STAT=0
TRACE_MODE="${PDOCKER_DIRECT_TRACE_MODE:-seccomp}"
PROOT_CMD=""
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REMOTE_BENCH_DIR="pdocker/bench"
REMOTE_ARTIFACT="$REMOTE_BENCH_DIR/android-runtime-bench-$STAMP-$TRACE_MODE.log"
STOP_WARN="${PDOCKER_BENCH_STOP_WARN:-5000}"
ELAPSED_WARN="${PDOCKER_BENCH_ELAPSED_WARN:-15}"
while (($#)); do
  case "$1" in
    --apt-update) RUN_APT_UPDATE=1 ;;
    --apt-upgrade-dry-run) RUN_APT_UPGRADE_DRY_RUN=1 ;;
    --npm-dry-run) RUN_NPM_DRY_RUN=1 ;;
    --untraced-stat) UNTRACED_STAT=1 ;;
    --trace-mode)
      shift
      TRACE_MODE="${1:-}"
      ;;
    --proot-cmd)
      shift
      PROOT_CMD="${1:-}"
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
  shift
done

remote_quote() {
  printf "'%s'" "$(printf "%s" "$1" | sed "s/'/'\\\\''/g")"
}

run_as() {
  "$ADB" shell "run-as $PKG sh -c $(remote_quote "$1")"
}

bench_direct() {
  local body="$1"
  local label="$2"
  local stat_env=""
  if [[ "$UNTRACED_STAT" == "1" ]]; then
    stat_env="PDOCKER_DIRECT_UNTRACED_STAT_PATHS=1"
  fi
  run_as "cd $PROJECT_ROOT; mkdir -p '$REMOTE_BENCH_DIR'; R=\$(find pdocker/containers -mindepth 2 -maxdepth 2 -type d -name rootfs 2>/dev/null | head -1); if test -z \"\$R\"; then R=\$(find pdocker/images -mindepth 2 -maxdepth 3 -type d -name rootfs 2>/dev/null | head -1); fi; if test -z \"\$R\"; then echo '[pdocker bench] ERROR no rootfs is available; run docker pull/build first' | tee -a '$REMOTE_ARTIFACT'; exit 2; fi; TMP=\"pdocker/bench/.bench-$label.tmp\"; export PDOCKER_DIRECT_EXPERIMENTAL_PROCESS_EXEC=1 PDOCKER_DIRECT_TRACE_SYSCALLS=0 PDOCKER_DIRECT_TRACE_VERBOSE=0 PDOCKER_DIRECT_TRACE_PATHS=0 PDOCKER_DIRECT_STATS=1 PDOCKER_DIRECT_TRACE_MODE='$TRACE_MODE' $stat_env; { echo '[pdocker bench] label=$label trace-mode=$TRACE_MODE untraced-stat=$UNTRACED_STAT rootfs='\"\$R\"' artifact=$REMOTE_ARTIFACT'; /system/bin/time -p pdocker-runtime/docker-bin/pdocker-direct run --mode bench --rootfs \"\$R\" --workdir / --env HOME=/root --env DEBIAN_FRONTEND=noninteractive --env PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin -- /bin/sh -lc \"$body\"; } >\"\$TMP\" 2>&1; RC=\$?; cat \"\$TMP\" | tee -a '$REMOTE_ARTIFACT'; rm -f \"\$TMP\"; exit \$RC"
}

echo "[pdocker bench] direct apt-cache (trace-mode=$TRACE_MODE)"
bench_direct "command -v apt-cache >/dev/null; apt-cache policy nodejs >/tmp/pdocker-bench-apt-cache.log; head -5 /tmp/pdocker-bench-apt-cache.log" "apt-cache"

if [[ "$RUN_APT_UPDATE" == "1" ]]; then
  echo
  echo "[pdocker bench] direct apt-get update"
  bench_direct "command -v apt-get >/dev/null; apt-get update >/tmp/pdocker-bench-apt-update.log; tail -1 /tmp/pdocker-bench-apt-update.log" "apt-get-update"
fi

if [[ "$RUN_APT_UPGRADE_DRY_RUN" == "1" ]]; then
  echo
  echo "[pdocker bench] direct apt-get -s upgrade"
  bench_direct "command -v apt-get >/dev/null; apt-get -s upgrade >/tmp/pdocker-bench-apt-upgrade-dry-run.log; tail -5 /tmp/pdocker-bench-apt-upgrade-dry-run.log" "apt-upgrade-dry-run"
fi

if [[ "$RUN_NPM_DRY_RUN" == "1" ]]; then
  echo
  echo "[pdocker bench] direct npm codex dry-run"
  bench_direct "command -v npm >/dev/null; npm install -g @openai/codex --dry-run" "npm-codex-dry-run"
fi

if [[ -n "$PROOT_CMD" ]]; then
  echo
  echo "[pdocker bench] external proot command"
  echo "command: $PROOT_CMD"
  "$ADB" shell "$PROOT_CMD"
fi

echo
echo "[pdocker bench] artifact: files/$REMOTE_ARTIFACT"
echo "[pdocker bench] thresholds: stops<=$STOP_WARN elapsed<=${ELAPSED_WARN}s for lightweight apt-cache"
run_as "cd $PROJECT_ROOT; LOG='$REMOTE_ARTIFACT'; STOPS=\$(grep 'pdocker-direct-stats: reason=' \"\$LOG\" | sed -n 's/.*stops=\\([0-9][0-9]*\\).*/\\1/p' | head -1); ELAPSED=\$(grep 'pdocker-direct-stats: reason=' \"\$LOG\" | sed -n 's/.*elapsed=\\([0-9.][0-9.]*\\)s.*/\\1/p' | head -1); if test -z \"\$STOPS\" || test -z \"\$ELAPSED\"; then echo '[pdocker bench] FAIL missing pdocker-direct-stats; benchmark did not execute a traced rootfs'; exit 1; fi; WARN=0; if test \"\$STOPS\" -gt '$STOP_WARN'; then echo \"[pdocker bench] WARN stops=\$STOPS exceeds $STOP_WARN\"; WARN=1; fi; if awk 'BEGIN { exit !('$ELAPSED_WARN' < '\$ELAPSED') }'; then echo \"[pdocker bench] WARN elapsed=\${ELAPSED}s exceeds ${ELAPSED_WARN}s\"; WARN=1; fi; if test \"\$WARN\" = 0; then echo \"[pdocker bench] PASS lightweight threshold stops=\$STOPS elapsed=\${ELAPSED}s\"; fi"

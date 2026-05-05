#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONDONTWRITEBYTECODE=1

MODE="backend-quick"
INSTALL_FLAG=()

usage() {
  cat <<'EOF'
Usage: scripts/verify-heavy.sh [mode] [--no-install]

Modes:
  --backend-quick   run backend verify_all.sh --quick through compat-audit (default)
  --backend-full    run backend verify_all.sh --full through compat-audit
  --container-probe run direct-runtime probe against ROOTFS/PDOCKER_DIRECT_EXECUTOR
  --android-quick   run Android device quick smoke through adb
  --android-full    run Android device full smoke through adb

Environment:
  BACKEND_QUICK_TIMEOUT  timeout seconds for backend quick regression (default: 900)
  BACKEND_FULL_TIMEOUT   timeout seconds for backend full regression (default: 1800)
  ADB                    adb executable for Android modes
  ROOTFS                 rootfs path for --container-probe
  PDOCKER_DIRECT_EXECUTOR
                         pdocker-direct path for --container-probe
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --backend-quick|--backend-full|--container-probe|--android-quick|--android-full)
      MODE="${1#--}"
      ;;
    --no-install)
      INSTALL_FLAG+=(--no-install)
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

case "$MODE" in
  backend-quick)
    python3 scripts/compat-audit.py \
      --backend-quick \
      --backend-quick-timeout "${BACKEND_QUICK_TIMEOUT:-900}"
    ;;
  backend-full)
    python3 scripts/compat-audit.py \
      --full \
      --full-timeout "${BACKEND_FULL_TIMEOUT:-1800}"
    ;;
  container-probe)
    bash scripts/container-direct-probe.sh --memory-guard
    ;;
  android-quick)
    bash scripts/android-device-smoke.sh --quick "${INSTALL_FLAG[@]}"
    ;;
  android-full)
    bash scripts/android-device-smoke.sh "${INSTALL_FLAG[@]}"
    ;;
esac

printf '\nverify-heavy (%s): PASS\n' "$MODE"

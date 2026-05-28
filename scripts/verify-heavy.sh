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
  --android-dev-workspace
                    run the default dev-workspace device lane through the
                    canonical test driver
  --android-documents
                    run the SAF/Documents direct-output device lane through
                    the canonical test driver
  --android-runtime-teardown
                    run the runtime teardown planned-gap device lane through
                    the canonical test driver
  --android-service-truth
                    run the service truth planned-gap device lane through
                    the canonical test driver
  --android-storage-metrics-sequence
                    run the storage metrics device-sequence lane through the
                    canonical test driver
  --android-single-container
                    run the focused single-container echo-hi device lane
                    through the canonical test driver
  --android-modern-runtime-truth
                    run the modern/no-PRoot runtime-truth lane through the
                    canonical test driver

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
    --backend-quick|--backend-full|--container-probe|--android-quick|--android-full|--android-dev-workspace|--android-documents|--android-runtime-teardown|--android-service-truth|--android-storage-metrics-sequence|--android-single-container|--android-modern-runtime-truth)
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
  android-dev-workspace)
    python3 scripts/skydnir-test-driver.py --lane android-dev-workspace
    ;;
  android-documents)
    python3 scripts/skydnir-test-driver.py --lane android-documents
    ;;
  android-runtime-teardown)
    python3 scripts/skydnir-test-driver.py --lane android-runtime-teardown
    ;;
  android-service-truth)
    python3 scripts/skydnir-test-driver.py --lane android-service-truth
    ;;
  android-storage-metrics-sequence)
    python3 scripts/skydnir-test-driver.py --lane android-storage-metrics-sequence
    ;;
  android-single-container)
    python3 scripts/skydnir-test-driver.py --lane android-single-container-echo-hi
    ;;
  android-modern-runtime-truth)
    python3 scripts/skydnir-test-driver.py --lane android-modern-runtime-truth
    ;;
esac

printf '\nverify-heavy (%s): PASS\n' "$MODE"

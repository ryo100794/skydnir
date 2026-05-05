#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONDONTWRITEBYTECODE=1

INCLUDE_DEVICE=0
INCLUDE_LONG_DEVICE=0
INSTALL_FLAG=(--no-install)

usage() {
  cat <<'EOF'
Usage: scripts/verify-scenarios.sh [--include-device] [--include-long-device] [--install]

Runs the feature scenario ledger as one ordered automated test flow.

Default:
  Host-only fast/local gates that should be safe after normal code edits.

Options:
  --include-device       also run short Android device smokes that need ADB.
  --include-long-device  also run long GPU/llama/device benchmark scenarios.
  --install              allow Android smoke scripts to install/reinstall APKs.

Environment:
  ADB_SERIAL             Android device serial, default inherited by scripts.
  PACKAGE                Android package, default io.github.ryo100794.pdocker.compat.
  ROOTFS                 rootfs path for container-direct-probe if used directly.
EOF
}

run() {
  printf '\n==> %s\n' "$*"
  "$@"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --include-device)
      INCLUDE_DEVICE=1
      ;;
    --include-long-device)
      INCLUDE_DEVICE=1
      INCLUDE_LONG_DEVICE=1
      ;;
    --install)
      INSTALL_FLAG=()
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

run python3 scripts/verify-feature-scenarios.py
run python3 scripts/verify-abnormal-events.py
run python3 scripts/verify-refactor-resilience.py
run python3 scripts/verify-test-design-criteria.py
run python3 scripts/verify-input-grammar-coverage.py
run python3 scripts/verify-input-validation.py
run python3 scripts/verify-stress-regression.py
run python3 scripts/verify-blackbox-requirements.py
run bash scripts/verify-fast.sh
run python3 -m unittest discover -s tests -p 'test_*.py'
run python3 scripts/verify-project-library.py
run python3 scripts/verify-release-readiness.py

if [[ "$INCLUDE_DEVICE" == 1 ]]; then
  run bash scripts/android-documents-mediator-smoke.sh
  run bash scripts/android-device-smoke.sh --quick "${INSTALL_FLAG[@]}"
fi

if [[ "$INCLUDE_LONG_DEVICE" == 1 ]]; then
  run bash scripts/android-gpu-host-bench.sh
  run bash scripts/android-gpu-compare-bench.sh
  run bash scripts/android-llama-bench.sh
  run bash scripts/android-llama-gpu-compare.sh --gpu-only
fi

printf '\nverify-scenarios: PASS\n'

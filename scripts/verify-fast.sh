#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONDONTWRITEBYTECODE=1
export PDOCKER_ANDROID_FLAVOR="${PDOCKER_ANDROID_FLAVOR:-compat}"
case "$PDOCKER_ANDROID_FLAVOR" in
  compat|modern) ;;
  *)
    echo "verify-fast: PDOCKER_ANDROID_FLAVOR must be 'compat' or 'modern' (got '$PDOCKER_ANDROID_FLAVOR')" >&2
    exit 2
    ;;
esac

run() {
  printf '\n==> %s\n' "$*"
  "$@"
}

run python3 -m py_compile \
  scripts/compat-audit.py \
  scripts/verify-abnormal-events.py \
  scripts/verify-build-profile.py \
  scripts/verify-blackbox-requirements.py \
  scripts/verify-dockerfile-standard.py \
  scripts/verify-feature-scenarios.py \
  scripts/verify-input-grammar-coverage.py \
  scripts/verify-input-validation.py \
  scripts/verify-stress-regression.py \
  scripts/verify-test-design-criteria.py \
  scripts/verify_direct_syscall_contracts.py \
  scripts/verify-memory-pager-design.py \
  scripts/verify-metadata-index.py \
  scripts/run_direct_syscall_scenarios.py \
  scripts/verify-project-library.py \
  scripts/verify-refactor-resilience.py \
  scripts/verify-storage-metrics.py \
  scripts/verify-ui-actions.py \
  scripts/verify_terminal_editor_contracts.py \
  scripts/update-showcase.py \
  docker-proot-setup/scripts/verify_runtime_contract.py

run python3 docker-proot-setup/scripts/verify_runtime_contract.py
run python3 scripts/verify_direct_syscall_contracts.py
run python3 scripts/verify-memory-pager-design.py
run python3 scripts/verify-metadata-index.py
run python3 -m unittest discover -s tests/metadata_index -p 'test_*.py'
run python3 scripts/run_direct_syscall_scenarios.py --lane local
run cmp -s docker-proot-setup/bin/pdockerd app/src/main/assets/pdockerd/pdockerd
run python3 scripts/verify-build-profile.py
run python3 scripts/verify-abnormal-events.py
run python3 scripts/verify-refactor-resilience.py
run python3 scripts/verify-test-design-criteria.py
run python3 scripts/verify-input-grammar-coverage.py
run python3 scripts/verify-input-validation.py
run python3 scripts/verify-stress-regression.py
run python3 scripts/verify-blackbox-requirements.py
run python3 scripts/verify-feature-scenarios.py
run python3 scripts/verify-dockerfile-standard.py
run python3 scripts/verify-project-library.py
run python3 scripts/verify-storage-metrics.py
run python3 scripts/verify-ui-actions.py
run python3 scripts/verify_terminal_editor_contracts.py
run bash scripts/smoke-vulkan-llama-init.sh
run bash scripts/smoke-vulkan-icd-bridge.sh
run python3 scripts/compat-audit.py
run python3 scripts/update-showcase.py --check

printf '\nverify-fast: PASS\n'

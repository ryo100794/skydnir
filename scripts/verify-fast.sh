#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONDONTWRITEBYTECODE=1
export PDOCKER_ANDROID_FLAVOR="${PDOCKER_ANDROID_FLAVOR:-compat}"
tmp_storage_sequence=""
cleanup_verify_fast() {
  if [ -n "$tmp_storage_sequence" ]; then
    rm -f "$tmp_storage_sequence"
  fi
}
trap cleanup_verify_fast EXIT
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
  scripts/verify-archive-api-compat.py \
  scripts/verify-build-profile.py \
  scripts/verify-build-context-tar-compat.py \
  scripts/verify-blackbox-requirements.py \
  scripts/verify-dockerfile-standard.py \
  scripts/verify-feature-scenarios.py \
  scripts/verify-image-pull-crash-safety.py \
  scripts/verify-input-grammar-coverage.py \
  scripts/verify-input-validation.py \
  scripts/verify-llama-gpu-artifact.py \
  scripts/verify-release-readiness.py \
  scripts/verify-stress-regression.py \
  scripts/verify-test-design-criteria.py \
  scripts/verify_direct_syscall_contracts.py \
  scripts/verify-memory-pager-contract.py \
  scripts/verify-memory-pager-design.py \
  scripts/verify-metadata-index.py \
  scripts/verify-native-payloads.py \
  scripts/verify-no-proot-runtime-truth-artifact.py \
  scripts/verify-oom-lmk-survival-gate.py \
  scripts/run_direct_syscall_scenarios.py \
  scripts/verify-project-library.py \
  scripts/verify-refactor-resilience.py \
  scripts/verify-service-truth-plan.py \
  scripts/verify-self-debug-bundle.py \
  scripts/verify-storage-metrics.py \
  scripts/verify-script-inventory.py \
  scripts/verify-dev-workspace-compose-artifact.py \
  scripts/verify-docs-maintenance.py \
  scripts/verify-runtime-teardown-artifact.py \
  scripts/verify-runtime-single-container-artifact.py \
  scripts/verify-saf-direct-output-artifact.py \
  scripts/verify-ui-actions.py \
  scripts/verify-terminal-exec-it-artifact.py \
  scripts/verify_terminal_editor_contracts.py \
  scripts/verify-cow-overlay-bench-recovery.py \
  scripts/verify/runner/cow_overlay_kill_at_step_device.py \
  scripts/maintenance/summarize-llama-gpu-artifacts.py \
  scripts/update-showcase.py \
  docker-proot-setup/scripts/verify_runtime_contract.py

run bash -n \
  scripts/android-selfdebug.sh \
  scripts/android-service-truth-capture.sh \
  scripts/android-device-smoke.sh \
  scripts/android-dev-workspace-compose-smoke.sh \
  scripts/android-documents-mediator-smoke.sh \
  scripts/android-llama-gpu-readiness.sh \
  scripts/android-llama-gpu-compare.sh \
  scripts/android-storage-metrics-sequence.sh \
  scripts/test/smoke-opencl-bridge.sh \
  scripts/test/smoke-vulkan-llama-init.sh \
  scripts/test/verify-device-llama-template.sh \
  scripts/verify-native-rebuild-release.sh \
  scripts/verify-heavy.sh

run env PDOCKER_NATIVE_REBUILD_UTC=verify-fast-dry-run \
  bash scripts/verify-native-rebuild-release.sh
run env PDOCKER_NATIVE_REBUILD_UTC=verify-fast-fdroid-no-crane-dry-run \
  PDOCKER_FDROID_NO_CRANE=1 \
  bash scripts/verify-native-rebuild-release.sh

run python3 docker-proot-setup/scripts/verify_runtime_contract.py
run python3 scripts/verify_direct_syscall_contracts.py
run python3 scripts/verify-memory-pager-contract.py
run python3 scripts/verify-memory-pager-design.py
run python3 scripts/verify-oom-lmk-survival-gate.py
run python3 scripts/verify-metadata-index.py
run python3 -m unittest discover -s tests/metadata_index -p 'test_*.py'
run python3 scripts/run_direct_syscall_scenarios.py --lane local
run cmp -s docker-proot-setup/bin/pdockerd app/src/main/assets/pdockerd/pdockerd
run python3 scripts/verify-build-profile.py
run python3 scripts/verify-abnormal-events.py
run python3 scripts/verify-refactor-resilience.py
printf '\n==> governance lane note\n'
printf '%s\n' 'strict test-design criteria is a known-failing governance lane; run scripts/pdocker-test-driver.py --lane governance to refresh docs/test/test-design-criteria-latest.json.'
run python3 scripts/verify-input-grammar-coverage.py
run python3 scripts/verify-input-validation.py
run python3 scripts/verify-stress-regression.py
run python3 scripts/verify-blackbox-requirements.py
run python3 scripts/verify-feature-scenarios.py
run python3 scripts/verify-dockerfile-standard.py
run python3 scripts/verify-archive-api-compat.py
run python3 scripts/verify-build-context-tar-compat.py
run python3 scripts/verify-image-pull-crash-safety.py
run python3 scripts/verify-cow-overlay-bench-recovery.py --run-local
run python3 scripts/verify/runner/cow_overlay_kill_at_step_device.py --validate-artifact docs/test/cow-overlay-kill-at-step-latest.json
run python3 scripts/verify-llama-gpu-artifact.py docs/test/llama-gpu-workgroup3d-preflight-20260513.json --allow-memory-blocker
run python3 scripts/verify-no-proot-runtime-truth-artifact.py docs/test/no-proot-runtime-truth-latest.json
run python3 scripts/verify-project-library.py
run python3 scripts/verify-storage-metrics.py
run python3 scripts/verify-script-inventory.py
native_payload_apk="app/build/outputs/apk/${PDOCKER_ANDROID_FLAVOR}/debug/app-${PDOCKER_ANDROID_FLAVOR}-debug.apk"
if [ "$PDOCKER_ANDROID_FLAVOR" = "compat" ] && [ -f "$native_payload_apk" ]; then
  printf '\n==> existing compat APK found; validating packaged payload freshness\n'
  printf '%s\n' "If this fails after native/runtime source changes, rebuild with './gradlew :app:assembleCompatDebug --no-daemon' or remove the stale APK for host-only verification."
  run python3 scripts/verify-native-payloads.py \
    --apk "$native_payload_apk" \
    --apk-arm64-only \
    --write-artifact docs/test/native-payloads-latest.json
else
  # Keep checked-in APK evidence stable when fast verification is run before an APK exists.
  run python3 scripts/verify-native-payloads.py \
    --write-artifact /tmp/pdocker-native-payloads-no-apk.json
fi
run python3 scripts/verify-docs-maintenance.py
run python3 scripts/verify-release-readiness.py
tmp_storage_sequence="$(mktemp)"
printf '\n==> python3 scripts/verify-storage-metrics.py --print-sequence-fixture > %s\n' "$tmp_storage_sequence"
python3 scripts/verify-storage-metrics.py --print-sequence-fixture > "$tmp_storage_sequence"
run python3 scripts/verify-storage-metrics.py --sequence "$tmp_storage_sequence"
rm -f "$tmp_storage_sequence"
tmp_storage_sequence=""
run python3 scripts/verify-service-truth-plan.py
run python3 scripts/verify-ui-actions.py
run python3 scripts/verify_terminal_editor_contracts.py
run python3 -m unittest \
  tests.test_terminal_exec_it_contract \
  tests.test_android_selfdebug_helper \
  tests.test_self_debug_bundle_verifier \
  tests.test_release_readiness_notice_audit \
  tests.test_script_inventory_audit \
  tests.test_docs_maintenance \
  tests.test_native_payload_verifier_synthetic \
  tests.test_terminal_exec_it_artifact_verifier \
  tests.test_service_truth_device_gate \
  tests.test_runtime_teardown_device_gate \
  tests.test_runtime_teardown_artifact_verifier \
  tests.test_dev_workspace_smoke_contract \
  tests.test_dev_workspace_compose_artifact_verifier \
  tests.test_dockerfile_run_changed_paths \
  tests.test_image_pull_crash_safety_verifier \
  tests.test_runtime_single_container_artifact_verifier \
  tests.test_no_proot_runtime_truth_artifact_verifier \
  tests.test_android_storage_metrics_sequence \
  tests.test_memory_pager_contract \
  tests.test_apk_memory_pager_contract \
  tests.test_oom_lmk_survival_gate \
  tests.test_media_bridge_contract \
  tests.test_service_truth_artifact_contract \
  tests.test_saf_direct_output_contract \
  tests.storage_metrics.test_verify_storage_metrics \
  tests.test_llama_startup_logging_contract \
  tests.test_llama_gpu_env_parity \
  tests.test_llama_gpu_readiness_contract \
  tests.test_llama_gpu_artifact_verifier \
  tests.test_llama_gpu_artifact_sweep \
  tests.test_llama_gpu_q6k_workflow \
  tests.test_vulkan_icd_feature_chain \
  tests.test_ci_gate_ledger \
  tests.test_test_driver
run bash scripts/test/smoke-vulkan-llama-init.sh
run bash scripts/test/smoke-vulkan-icd-bridge.sh
run python3 scripts/compat-audit.py
run python3 scripts/update-showcase.py --check

printf '\nverify-fast: PASS\n'

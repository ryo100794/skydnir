# Script Inventory

Snapshot date: 2026-05-19.

This directory is intentionally kept with stable top-level entrypoints while the implementation is reorganized.  The machine-readable source of truth is [`script-inventory.json`](script-inventory.json).
Every inventory entry now also carries a `migration` object with a proposed destination path, migration phase, action, and compatibility-wrapper policy.  This is a planning ledger only; no top-level script was moved in this stage.

## Policy

- **top level paths are stable**: True
- **move rule**: Do not move top-level scripts directly. Add subfolder implementations only behind stable wrapper shims and update this inventory first.
- **delete rule**: obsolete-suspect entries require a focused audit commit before deletion.
- **package rule**: Root scripts are host-side unless category is runtime-package-needed; APK-bundled runtime scripts live under app/src/main/assets/**/scripts.
- **compat wrapper rule**: Future moves keep the existing top-level path as a thin wrapper until all repository references and documented commands are migrated.
- **first stage rule**: This inventory records categories and move candidates only; it must not rename or move files by itself.

## Category Summary

| Category | Count | Meaning |
|---|---:|---|
| `runtime-package-needed` | 1 | Needed to stage runtime/APK payloads or otherwise part of packaging flow. |
| `build-developer` | 9 | Build, setup, fetch, or developer environment helper. |
| `test-verification` | 75 | Test, smoke, benchmark, contract, or device verification helper. |
| `generated-maintenance` | 3 | Generated-doc/evidence maintenance or manifest data. |
| `obsolete-suspect` | 3 | Unreferenced or weakly referenced candidate; not deleted without audit. |

## Move Candidate Targets

| Category | Proposed target dir | Compatibility wrapper policy |
|---|---|---|
| `runtime-package-needed` | `scripts/runtime/` | Keep stable top-level wrappers; migrate Gradle/package callers before any wrapper removal. |
| `build-developer` | `scripts/build/` | Stable entrypoints keep wrappers; developer helpers keep temporary wrappers while docs/CI references migrate. |
| `test-verification` | `scripts/test/` | Stable verification entrypoints keep wrappers; helper wrappers remain until test manifests and docs stop referencing top-level paths. |
| `generated-maintenance` | `scripts/maintenance/` | Top-level wrapper or symlink shim allowed after docs and generated-artifact references migrate. |
| `obsolete-suspect` | `scripts/obsolete-candidates/` | Do not move first; audit for deletion or archive in a focused follow-up commit. |

## Phase-1 wrapper migrations

The following entries have already moved their implementation behind a stable
top-level wrapper.  Keep both the wrapper and the inventory entry until all
documented commands, test manifests, and CI lanes explicitly point at the new
implementation path.
`scripts/verify-script-inventory.py` and `tests/test_script_inventory_audit.py`
verify that each migrated top-level wrapper points at its recorded
`candidate_path`, keeps an executable bit, follows the expected thin
shell/python wrapper shape, and is no longer referenced from docs,
`.github/workflows`, or test manifests outside the explicit
inventory/README/verifier allowlist.

| Stable wrapper | Implementation path |
|---|---|
| `scripts/smoke-opencl-bridge.sh` | `scripts/test/smoke-opencl-bridge.sh` |
| `scripts/smoke-vulkan-llama-init.sh` | `scripts/test/smoke-vulkan-llama-init.sh` |
| `scripts/verify-device-llama-template.sh` | `scripts/test/verify-device-llama-template.sh` |
| `scripts/summarize-llama-gpu-artifacts.py` | `scripts/maintenance/summarize-llama-gpu-artifacts.py` |

## Subtree Entries

These non-top-level runner helpers are tracked in `subtree_entries` so
verification inventory audits classify them without changing stable public
entrypoint counts. This classification landed in `ed7cddd`; bytecode caches
remain gitignored and intentionally absent from the inventory.

| Path | Category | Stability |
|---|---|---|
| `scripts/verify/runner/cow-overlay-kill-at-step-device.sh` | `test-verification` | `device-side-runner` |
| `scripts/verify/runner/cow_overlay_kill_at_step_device.py` | `test-verification` | `device-runner` |
| `scripts/verify/runner/image-pull-crash-safety-device.sh` | `test-verification` | `device-side-runner` |
| `scripts/verify/runner/image_pull_crash_safety_device.py` | `test-verification` | `device-runner` |

## Stable Public Entrypoints

- `scripts/build-all.sh` — full build orchestration.
- `scripts/build-apk.sh` — APK build wrapper.
- `scripts/verify-fast.sh` — fast host regression lane.
- `scripts/verify-heavy.sh` — heavier/device-oriented lane wrapper.
- `scripts/pdocker-test-driver.py` — canonical test-driver manifest executor.
- `scripts/android-selfdebug.sh` — Android single-device localhost Wireless debugging helper.

`scripts/android-service-truth-capture.sh` is intentionally not a stable public entrypoint; it is a device-helper wrapper for service-truth evidence capture and belongs to the `test-verification` migration bucket.

## Entries

### runtime-package-needed

| Path | Stability | Role |
|---|---|---|
| `scripts/copy-native.sh` | `stable-entrypoint` | Stages APK/runtime payloads consumed by Gradle packaging. |

### build-developer

| Path | Stability | Role |
|---|---|---|
| `scripts/build-all.sh` | `stable-entrypoint` | Build, setup, or developer environment helper. |
| `scripts/build-apk.sh` | `stable-entrypoint` | Build, setup, or developer environment helper. |
| `scripts/build-gpu-shim.sh` | `stable-entrypoint` | Build, setup, or developer environment helper. |
| `scripts/build-native-termux.sh` | `stable-entrypoint` | Build, setup, or developer environment helper. |
| `scripts/fetch-xterm.sh` | `developer-helper` | Build, setup, or developer environment helper. |
| `scripts/git-preflight.sh` | `developer-helper` | Build, setup, or developer environment helper. |
| `scripts/setup-env.sh` | `developer-helper` | Build, setup, or developer environment helper. |
| `scripts/setup-git-worktree.sh` | `developer-helper` | Build, setup, or developer environment helper. |

### test-verification

| Path | Stability | Role |
|---|---|---|
| `scripts/android-api29-direct-feasibility.sh` | `device-helper` | Android/device, GPU, llama, or runtime benchmark/smoke helper. |
| `scripts/android-blas-cmake-build-smoke.sh` | `device-helper` | Android/device, GPU, llama, or runtime benchmark/smoke helper. |
| `scripts/android-container-direct-probe.sh` | `device-helper` | Android/device, GPU, llama, or runtime benchmark/smoke helper. |
| `scripts/android-dev-workspace-compose-smoke.sh` | `device-helper` | Android/device, GPU, llama, or runtime benchmark/smoke helper. |
| `scripts/android-device-memory-diagnostics.sh` | `device-helper` | Android/device, GPU, llama, or runtime benchmark/smoke helper. |
| `scripts/android-device-smoke.sh` | `device-helper` | Android/device, GPU, llama, or runtime benchmark/smoke helper. |
| `scripts/android-direct-path-boundary-probe.sh` | `device-helper` | Android/device, GPU, llama, or runtime benchmark/smoke helper. |
| `scripts/android-documents-mediator-smoke.sh` | `device-helper` | Android/device, GPU, llama, or runtime benchmark/smoke helper. |
| `scripts/android-file-io-bench.sh` | `device-helper` | Android/device, GPU, llama, or runtime benchmark/smoke helper. |
| `scripts/android-file-io-microbench.sh` | `device-helper` | Android/device, GPU, llama, or runtime benchmark/smoke helper. |
| `scripts/android-gpu-compare-bench.sh` | `device-helper` | Android/device, GPU, llama, or runtime benchmark/smoke helper. |
| `scripts/android-gpu-host-bench.sh` | `device-helper` | Android/device, GPU, llama, or runtime benchmark/smoke helper. |
| `scripts/android-llama-bench.sh` | `device-helper` | Android/device, GPU, llama, or runtime benchmark/smoke helper. |
| `scripts/android-llama-gpu-compare.sh` | `device-helper` | Android/device, GPU, llama, or runtime benchmark/smoke helper. |
| `scripts/android-llama-gpu-q6k-run.py` | `device-helper` | Android/device, GPU, llama, or runtime benchmark/smoke helper. |
| `scripts/android-llama-gpu-readiness.sh` | `device-helper` | Android/device, GPU, llama, or runtime benchmark/smoke helper. |
| `scripts/android-llama-tool-bench.sh` | `device-helper` | Android/device, GPU, llama, or runtime benchmark/smoke helper. |
| `scripts/android-memory-pager-managed-poc.sh` | `device-helper` | Android/device, GPU, llama, or runtime benchmark/smoke helper. |
| `scripts/android-memory-pager-transparent-poc.sh` | `device-helper` | Android/device, GPU, llama, or runtime benchmark/smoke helper. |
| `scripts/android-no-proot-runtime-truth-gate.sh` | `device-helper` | Android/device no-proot runtime truth gate wrapper and evidence capture helper. |
| `scripts/android-runtime-bench.sh` | `device-helper` | Android/device, GPU, llama, or runtime benchmark/smoke helper. |
| `scripts/android-selfdebug.sh` | `stable-entrypoint` | Android/device, GPU, llama, or runtime benchmark/smoke helper. |
| `scripts/android-storage-metrics-sequence.sh` | `device-helper` | Android/device, GPU, llama, or runtime benchmark/smoke helper. |
| `scripts/android-syscall-usecase-profile.sh` | `device-helper` | Android/device, GPU, llama, or runtime benchmark/smoke helper. |
| `scripts/android-test-suite-container-exec.sh` | `device-helper` | Android/device, GPU, llama, or runtime benchmark/smoke helper. |
| `scripts/bench-gpu-bridge.sh` | `device-helper` | Android/device, GPU, llama, or runtime benchmark/smoke helper. |
| `scripts/compat-audit.py` | `test-helper` | Host-side verification/test driver or static contract gate. |
| `scripts/container-direct-probe.sh` | `device-helper` | Android/device, GPU, llama, or runtime benchmark/smoke helper. |
| `scripts/pdocker-test-driver.py` | `stable-entrypoint` | Host-side verification/test driver or static contract gate. |
| `scripts/run-python-coverage.sh` | `test-helper` | Host-side verification/test driver or static contract gate. |
| `scripts/run_direct_syscall_scenarios.py` | `test-helper` | Host-side verification/test driver or static contract gate. |
| `scripts/smoke-opencl-bridge.sh` | `device-helper` | Android/device, GPU, llama, or runtime benchmark/smoke helper. |
| `scripts/smoke-vulkan-icd-bridge.sh` | `device-helper` | Android/device, GPU, llama, or runtime benchmark/smoke helper. |
| `scripts/smoke-vulkan-llama-init.sh` | `device-helper` | Android/device, GPU, llama, or runtime benchmark/smoke helper. |
| `scripts/verify-abnormal-events.py` | `test-helper` | Host-side verification/test driver or static contract gate. |
| `scripts/verify-archive-api-compat.py` | `test-helper` | Host-side verification/test driver or static contract gate. |
| `scripts/verify-blackbox-requirements.py` | `test-helper` | Host-side verification/test driver or static contract gate. |
| `scripts/verify-build-profile.py` | `test-helper` | Host-side verification/test driver or static contract gate. |
| `scripts/verify-build-context-tar-compat.py` | `test-helper` | Host-side verifier for Android build-context tar metadata, symlink, PAX, and dockerignore compatibility. |
| `scripts/verify-cow-overlay-bench-recovery.py` | `test-helper` | Host-side verification/test driver or static contract gate. |
| `scripts/verify-docs-maintenance.py` | `test-helper` | Host-side verifier for documentation maintenance backlog, canonical owners, and local Markdown links. |
| `scripts/verify-dev-workspace-compose-artifact.py` | `test-helper` | Host-side verification/test driver or static contract gate. |
| `scripts/verify-device-llama-template.sh` | `test-helper` | Host-side verification/test driver or static contract gate. |
| `scripts/verify-dockerfile-standard.py` | `test-helper` | Host-side verification/test driver or static contract gate. |
| `scripts/verify-fast.sh` | `stable-entrypoint` | Host-side verification/test driver or static contract gate. |
| `scripts/verify-feature-scenarios.py` | `test-helper` | Host-side verification/test driver or static contract gate. |
| `scripts/verify-heavy.sh` | `stable-entrypoint` | Host-side verification/test driver or static contract gate. |
| `scripts/verify-image-pull-crash-safety.py` | `test-helper` | Host-side verification/test driver or static contract gate. |
| `scripts/verify-input-grammar-coverage.py` | `test-helper` | Host-side verification/test driver or static contract gate. |
| `scripts/verify-input-validation.py` | `test-helper` | Host-side verification/test driver or static contract gate. |
| `scripts/verify-llama-gpu-artifact.py` | `test-helper` | Host-side verification/test driver or static contract gate. |
| `scripts/verify-memory-pager-contract.py` | `test-helper` | Host-side verification/test driver or static contract gate. |
| `scripts/verify-memory-pager-design.py` | `test-helper` | Host-side verification/test driver or static contract gate. |
| `scripts/verify-metadata-index.py` | `test-helper` | Host-side verification/test driver or static contract gate. |
| `scripts/verify-no-proot-runtime-truth-artifact.py` | `test-helper` | Host-side verifier for no-proot runtime truth gate artifacts. |
| `scripts/verify-oom-lmk-survival-gate.py` | `test-helper` | Host-side verification/test driver or static contract gate. |
| `scripts/verify-project-library.py` | `test-helper` | Host-side verification/test driver or static contract gate. |
| `scripts/verify-refactor-resilience.py` | `test-helper` | Host-side verification/test driver or static contract gate. |
| `scripts/verify-release-readiness.py` | `test-helper` | Host-side verification/test driver or static contract gate. |
| `scripts/verify-runtime-single-container-artifact.py` | `test-helper` | Host-side verification/test driver or static contract gate. |
| `scripts/verify-runtime-teardown-artifact.py` | `test-helper` | Host-side verification/test driver or static contract gate. |
| `scripts/verify-saf-direct-output-artifact.py` | `test-helper` | Host-side verification/test driver or static contract gate. |
| `scripts/verify-scenarios.sh` | `test-helper` | Host-side verification/test driver or static contract gate. |
| `scripts/verify-self-debug-bundle.py` | `test-helper` | Host-side verifier for APK-generated ADB-free self-debug bundle JSON artifacts. |
| `scripts/verify-script-inventory.py` | `test-helper` | Host-side verification/test driver or static contract gate. |
| `scripts/verify-service-truth-plan.py` | `test-helper` | Host-side verification/test driver or static contract gate. |
| `scripts/verify-storage-metrics.py` | `test-helper` | Host-side verification/test driver or static contract gate. |
| `scripts/verify-stress-regression.py` | `test-helper` | Host-side verification/test driver or static contract gate. |
| `scripts/verify-terminal-exec-it-artifact.py` | `test-helper` | Host-side verification/test driver or static contract gate. |
| `scripts/verify-test-design-criteria.py` | `test-helper` | Host-side verification/test driver or static contract gate. |
| `scripts/verify-ui-actions.py` | `test-helper` | Host-side verification/test driver or static contract gate. |
| `scripts/verify_direct_syscall_contracts.py` | `test-helper` | Host-side verification/test driver or static contract gate. |
| `scripts/verify_terminal_editor_contracts.py` | `test-helper` | Host-side verification/test driver or static contract gate. |

### generated-maintenance

| Path | Stability | Role |
|---|---|---|
| `scripts/llama-gpu-env-manifest.json` | `maintenance-helper` | Maintains generated docs, GPU environment manifests, or evidence summaries. |
| `scripts/summarize-llama-gpu-artifacts.py` | `maintenance-helper` | Maintains generated docs, GPU environment manifests, or evidence summaries. |
| `scripts/update-showcase.py` | `stable-entrypoint` | Maintains generated docs, GPU environment manifests, or evidence summaries. |

### obsolete-suspect

| Path | Stability | Reference scan | Replacement command | Decision |
|---|---|---|---|---|
| `scripts/android-terminal-it-repro.sh` | `legacy-audit` | No runtime callers found outside inventory/README/verifier allowlist and the script itself; pycache-only hits ignored. | `python3 scripts/pdocker-test-driver.py --lane android-terminal-exec-it` | Keep for now; do not delete until paired UI self-test and Engine exec-input JSONL artifacts fully replace the ad-hoc repro. |
| `scripts/verify-llama-startup-logging.py` | `legacy-audit` | No active caller found; llama startup contract is covered by `scripts/verify-project-library.py` static checks and `tests/test_gpu_abi_contract.py` markers. | `python3 scripts/verify-project-library.py` | Keep for now; deletion is acceptable only after its early-tee/startup-json assertions are represented in maintained tests. |
| `scripts/wrap-ndk-box64.sh` | `legacy-audit` | No active caller found; current build wrappers use `scripts/build-native-termux.sh` instead of invoking x86_64 NDK tools through box64. | `bash scripts/build-native-termux.sh` | Keep for now; deletion is acceptable after confirming no supported aarch64-host build path depends on mutating the NDK with box64 shims. |

## Cleanup Plan

1. Keep top-level script names stable; add wrappers before any future move.
2. Introduce shared helpers such as `scripts/lib/android-adb.sh` before migrating callers.
3. Migrate small device scripts first; leave `android-device-smoke.sh` and `android-llama-gpu-compare.sh` until helper behavior is proven.
4. Audit `obsolete-suspect` entries in focused commits before deletion.
5. Move `verify-fast.sh` toward `tests/test_driver_manifest.json` gradually rather than merging all verifiers at once.

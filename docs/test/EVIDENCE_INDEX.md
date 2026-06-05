# Test Evidence Index

Snapshot date: 2026-05-20.

This index explains how to read the high-churn `docs/test/*latest*` evidence
files.  A `latest` artifact is a convenient pointer, not a stable checkpoint by
itself.  Promotion requires the owning gate document, CI gate ledger, or release
record to say that the artifact is promotable.

## Evidence Classes

| Class | Meaning | Promotion rule |
|---|---|---|
| Host/static verifier | Runs without ADB or device state. | Can close host-only contracts, docs drift, parser behavior, schema validation, or synthetic negative cases. Cannot close device/runtime behavior. |
| Device-gated artifact | Requires connected Android device, app state, or ADB/run-as evidence. | Promotes only when the owning gate says `device-pass` or equivalent and the artifact satisfies that gate's verifier. |
| Planned-gap / non-promoting artifact | Proves a gap is visible, skipped, blocked, or intentionally not promoted. | Never counts as stable success. Keep it to prevent hidden regressions and stale UI claims. |
| Historical release/build evidence | Immutable evidence under `docs/release/builds/` or dated docs. | Describes that build only; does not override the current live TODO/gate state. |

## High-Churn Artifact Families

| Family | Representative latest files | Canonical owner |
|---|---|---|
| Compatibility/API | `compat-audit-latest.md`, `build-context-tar-compat-latest.json` | [`COMPATIBILITY.md`](COMPATIBILITY.md), [`../design/DOCKER_COMPAT_SCOPE.md`](../design/DOCKER_COMPAT_SCOPE.md) |
| Llama/GPU | `llama-bench-latest.json`, `llama-build-route-apk-latest.log`, `llama-build-route-apk-sha256-latest.txt`, `llama-device-template-latest.log`, `llama-gpu-compare-latest.json`, `llama-gpu-compare-latest-artifacts`, `llama-gpu-compare-20260508-ngl1-no-dup-latest.json`, `llama-gpu-device-readiness-latest.json`, `llama-gpu-q6-preflight-plan-latest.json`, `llama-gpu-q6-preflight-plan-adb39229-latest.json`, `llama-gpu-q6-preflight-plan-adb34135-latest.json`, `llama-gpu-readiness-latest.json`, `llama-gpu-readiness-adb32987-latest.json`, `llama-gpu-readiness-adb33619-latest.json`, `llama-gpu-readiness-adb34135-latest.json`, `llama-gpu-readiness-adb34483-latest.json`, `llama-gpu-readiness-adb34761-latest.json`, `llama-gpu-readiness-adb39229-latest.json`, `llama-gpu-readiness-adb40309-latest.json`, `llama-gpu-readiness-adb44443-latest.json`, `llama-gpu-readiness-adb45055-latest.json`, `llama-gpu-readiness-adb45761-latest.json`, `llama-gpu-readiness-adb46015-latest.json`, `llama-gpu-readiness-q6-workgroup-latest.json`, `llama-gpu-artifact-sweep-latest.json`, `llama-gpu-q6k-row-indexed-latest.json`, `llama-gpu-q6k-row-indexed-latest-artifacts`, `llama-gpu-q6k-workflow-latest.json`, `llama-gpu-startup-diagnosis-latest.json`, `llama-correctness-latest.json`, `q6-workgroup-lowering-preflight-latest.json`, `q6k-evidence-inventory-latest.json` | [`LLAMA_GPU_DEVICE_RUNBOOK_20260513.md`](LLAMA_GPU_DEVICE_RUNBOOK_20260513.md), [`LLAMA_BENCHMARKS.md`](LLAMA_BENCHMARKS.md), [`LLAMA_GPU_NON_PROMOTING_ARTIFACT_NEGATIVES.md`](LLAMA_GPU_NON_PROMOTING_ARTIFACT_NEGATIVES.md) |
| Runtime / no-PRoot / terminal | `no-proot-runtime-truth-latest.json`, `test-run-latest.json` | [`NO_PROOT_RUNTIME_TRUTH_GATE.md`](NO_PROOT_RUNTIME_TRUTH_GATE.md), [`TERMINAL_EXEC_IT_DEVICE_GATE.md`](TERMINAL_EXEC_IT_DEVICE_GATE.md), [`RUNTIME_SINGLE_CONTAINER_GATE.md`](RUNTIME_SINGLE_CONTAINER_GATE.md) |
| Storage / COW / archive | `cow-overlay-recovery-latest.json`, `cow-overlay-bench-latest.json`, `cow-overlay-kill-at-step-latest.json`, `file-io-bench-latest.json`, `file-io-microbench-latest.json` | [`STORAGE_EVIDENCE_INDEX.md`](STORAGE_EVIDENCE_INDEX.md), [`COW_OVERLAY_BENCH_RECOVERY.md`](COW_OVERLAY_BENCH_RECOVERY.md), [`COW_OVERLAY_KILL_AT_STEP_DEVICE_GATE.md`](COW_OVERLAY_KILL_AT_STEP_DEVICE_GATE.md), [`STORAGE_METRICS.md`](STORAGE_METRICS.md) |
| Memory / OOM / pager | `apk-memory-pager-managed-latest.json`, `apk-memory-pager-transparent-latest.json`, `oom-lmk-survival-latest.json` | [`APK_MEMORY_PAGER_PROBE.md`](APK_MEMORY_PAGER_PROBE.md), [`OOM_LMK_SURVIVAL_GATE.md`](OOM_LMK_SURVIVAL_GATE.md) |
| Test-design quality | `test-design-criteria-latest.json`, `input-validation-latest.json`, `abnormal-events-latest.json`, `refactor-resilience-latest.json`, `stress-regression-latest.json`, `python-coverage-latest.json` | [`TEST_DESIGN_STANDARD.md`](TEST_DESIGN_STANDARD.md), [`SCENARIOS.md`](SCENARIOS.md), [`REFACTOR_RESILIENCE.md`](REFACTOR_RESILIENCE.md) |
| Performance profiles | `syscall-usecase-profile-latest.json`, `syscall-usecase-profile-syscall-latest.json`, `path-micro-profile-latest.json`, `path-micro-profile-latest.md`, `path-micro-profile-cached-latest.json`, `path-micro-profile-cached-latest.md`, `gpu-bridge-bench-latest.json`, `gpu-host-native-latest.json`, `gpu-host-container-comparison-latest.json` | [`DIRECT_SYSCALL_COVERAGE.md`](DIRECT_SYSCALL_COVERAGE.md), [`LLAMA_BENCHMARKS.md`](LLAMA_BENCHMARKS.md), [`STORAGE_METRICS.md`](STORAGE_METRICS.md) |
| Native / release hygiene | `native-payloads-latest.json`, `android-blas-cmake-build-latest.json`, `android-blas-cmake-build-latest.log` | [`../release/RELEASE_READINESS.md`](../release/RELEASE_READINESS.md), [`../build/NATIVE_BUILD_ENVIRONMENT.md`](../build/NATIVE_BUILD_ENVIRONMENT.md) |

Additional Llama/GPU diagnostic latest evidence owned by the same runbook remains non-promoting unless the runbook gate explicitly promotes it: `llama-gpu-ngl1-q6-latest-event-join-adb46015-20260531T073827Z.json`, `llama-gpu-ngl1-q6-latest-event-join-adb46015-20260531T073827Z-plan-verdict.json`, `llama-gpu-ngl1-q6-latest-event-join-adb46015-20260531T073827Z-artifacts`, `llama-gpu-q6-debug-alias-guard-verdict-latest.json`, `llama-gpu-q6-debug-alias-marker-verdict-latest.json`, `llama-gpu-q6-debug-alias-skip-nontarget-verdict-latest.json`, `llama-gpu-readiness-192_168_179_21_36763-latest.json`, and `q6-stage-trace-static-analysis-latest.json`.

## Rules For New Evidence

1. Prefer immutable run directories under `docs/test/runs/<run-id>/` for test
   driver executions and release records under `docs/release/builds/` for fixed
   builds.
2. If a `latest` file or `latest` artifact directory is committed, link it
   from this index, the owning gate document, or the test README.
3. If an artifact is `planned-gap`, `blocked`, `skip`, `success=false`, or
   host-only evidence for a device gate, describe the residual blocker instead
   of calling the run green.
4. Do not add a new standalone launcher with a new artifact convention. Route
   automated execution through `scripts/skydnir-test-driver.py` or register the
   exception in the owning gate.

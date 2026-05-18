# Test Documents

Snapshot date: 2026-05-15.

## Purpose

This category contains repeatable test procedures, compatibility audits, debug
workflows, and recorded test outputs. It should answer what is checked, how to
run it, and where the latest result is stored.

## Contents

| Document | Scope |
|---|---|
| [`COMPATIBILITY.md`](COMPATIBILITY.md) | Docker API, data exchange, protocol, APK payload, and UI compatibility coverage |
| [`CI_GATE_LEDGER.md`](CI_GATE_LEDGER.md) | P0/P1 service truth, teardown, image-pull, OOM/LMK, terminal `-it`, and llama GPU gate classification |
| [`TEST_DESIGN_STANDARD.md`](TEST_DESIGN_STANDARD.md) | Minimum test design criteria and the automated gate that enforces them |
| [`SCENARIOS.md`](SCENARIOS.md) | Feature-level scenario ledger and combined test runner |
| [`COW_OVERLAY_KILL_AT_STEP_DEVICE_GATE.md`](COW_OVERLAY_KILL_AT_STEP_DEVICE_GATE.md) | Android device-gated COW/overlay daemon/helper kill-at-step evidence contract |
| [`compat-audit-latest.md`](compat-audit-latest.md) | Latest recorded compatibility audit result |
| [`ANDROID_SELFDEBUG.md`](ANDROID_SELFDEBUG.md) | Android single-device localhost Wireless debugging and self-debug workflow, including `scripts/android-selfdebug.sh` |
| [`DIRECT_SYSCALL_COVERAGE.md`](DIRECT_SYSCALL_COVERAGE.md) | Direct runtime syscall hook inventory and fast/static coverage gate |
| [`APK_MEMORY_PAGER_PROBE.md`](APK_MEMORY_PAGER_PROBE.md) | SDK28 compat APK syscall probe for the opt-in memory pager |
| [`SECRET_AUDIT.md`](SECRET_AUDIT.md) | Repeatable secret, signing material, and remote URL audit before publication |
| [`gpu-host-native-latest.md`](gpu-host-native-latest.md) | Latest Android native CPU/Vulkan executor baseline, independent of container state |
| [`gpu-host-container-comparison-latest.md`](gpu-host-container-comparison-latest.md) | Latest host/container bridge overhead comparison |
| [`LLAMA_BENCHMARKS.md`](LLAMA_BENCHMARKS.md) | llama.cpp CPU/GPU benchmark history and current blockers |
| `scripts/verify-archive-api-compat.py` | Host-only fail-closed Docker archive API / `docker cp` compatibility gate |
| `scripts/smoke-vulkan-llama-init.sh` | Lightweight llama.cpp-oriented Vulkan ICD initialization smoke |
| `scripts/smoke-vulkan-icd-bridge.sh` | Lightweight Vulkan ICD dispatch smoke through the pdocker GPU executor socket; planned-skip when the local executor Vulkan preflight is unavailable |

## Canonical Sources

- Use `scripts/pdocker-test-driver.py` and `tests/test_driver_manifest.json` as
  the single automated test entrypoint and lane manifest. Every automated run
  must write one run manifest to `docs/test/test-run-latest.json` and an
  immutable copy under `docs/test/runs/<run-id>/manifest.json`.
- Use [`COMPATIBILITY.md`](COMPATIBILITY.md) as the canonical repeatable
  compatibility procedure and matrix.
- Use [`CI_GATE_LEDGER.md`](CI_GATE_LEDGER.md) as the compact P0/P1 gate
  classification table for planned gaps, lightweight gates, and device gates.
  Planned-gap and device-gated artifacts are non-promoting for stable
  checkpoints until their ledger promotion condition passes with device
  evidence.
- Use [`SCENARIOS.md`](SCENARIOS.md) and `tests/feature_scenarios.json` as the
  feature-level test ledger.
- Use [`TEST_DESIGN_STANDARD.md`](TEST_DESIGN_STANDARD.md) and
  `tests/test_design_criteria.json` as the automated quality bar for test
  design, check density, random/stress process, and build-set artifacts.
- Use [`compat-audit-latest.md`](compat-audit-latest.md) as the latest generated
  compatibility snapshot.
- Use [`LLAMA_BENCHMARKS.md`](LLAMA_BENCHMARKS.md) as the human-readable
  benchmark history, with JSON files kept as machine-readable artifacts.
- Fixed release-candidate build evidence lives under
  [`../release/builds/`](../release/builds/) so immutable release records do not
  mix with mutable `latest` test outputs.
- Link to [`../design/DOCKER_COMPAT_SCOPE.md`](../design/DOCKER_COMPAT_SCOPE.md)
  for product boundaries and to [`../design/GPU_COMPAT.md`](../design/GPU_COMPAT.md)
  for GPU design rules.

## Maintenance

- Keep command examples reproducible from the repository root.
- Keep generated or recorded results in this category, but register every
  automated result through the test driver run manifest. Do not introduce a new
  standalone test launcher with its own artifact convention.
- Evidence artifacts must have an explicit retention path: index them from this
  README, the relevant gate/runbook, the test-driver manifest, or a release
  record; retain immutable run/release artifacts that are referenced; or mark
  producer-owned scratch output as generated/cache-excluded instead of treating
  it as maintained prose.
- Do not describe a host-only planned-gap verifier pass, `release-honesty` pass,
  skipped device lane, or `status=planned-gap` / `success=false` artifact as a
  stable checkpoint. Link the residual blocker instead.
- Move product boundary decisions to [`../design/README.md`](../design/README.md).
- Move active implementation tasks to [`../plan/TODO.md`](../plan/TODO.md).

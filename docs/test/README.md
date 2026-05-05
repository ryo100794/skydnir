# Test Documents

Snapshot date: 2026-05-04.

## Purpose

This category contains repeatable test procedures, compatibility audits, debug
workflows, and recorded test outputs. It should answer what is checked, how to
run it, and where the latest result is stored.

## Contents

| Document | Scope |
|---|---|
| [`COMPATIBILITY.md`](COMPATIBILITY.md) | Docker API, data exchange, protocol, APK payload, and UI compatibility coverage |
| [`TEST_DESIGN_STANDARD.md`](TEST_DESIGN_STANDARD.md) | Minimum test design criteria and the automated gate that enforces them |
| [`SCENARIOS.md`](SCENARIOS.md) | Feature-level scenario ledger and combined test runner |
| [`compat-audit-latest.md`](compat-audit-latest.md) | Latest recorded compatibility audit result |
| [`ANDROID_SELFDEBUG.md`](ANDROID_SELFDEBUG.md) | Android Wi-Fi ADB and self-debug workflow |
| [`DIRECT_SYSCALL_COVERAGE.md`](DIRECT_SYSCALL_COVERAGE.md) | Direct runtime syscall hook inventory and fast/static coverage gate |
| [`APK_MEMORY_PAGER_PROBE.md`](APK_MEMORY_PAGER_PROBE.md) | SDK28 compat APK syscall probe for the opt-in memory pager |
| [`SECRET_AUDIT.md`](SECRET_AUDIT.md) | Repeatable secret, signing material, and remote URL audit before publication |
| [`gpu-host-native-latest.md`](gpu-host-native-latest.md) | Latest Android native CPU/Vulkan executor baseline, independent of container state |
| [`gpu-host-container-comparison-latest.md`](gpu-host-container-comparison-latest.md) | Latest host/container bridge overhead comparison |
| [`LLAMA_BENCHMARKS.md`](LLAMA_BENCHMARKS.md) | llama.cpp CPU/GPU benchmark history and current blockers |
| `scripts/smoke-vulkan-llama-init.sh` | Lightweight llama.cpp-oriented Vulkan ICD initialization smoke |
| `scripts/smoke-vulkan-icd-bridge.sh` | Lightweight Vulkan ICD dispatch smoke through the pdocker GPU executor socket |

## Canonical Sources

- Use [`COMPATIBILITY.md`](COMPATIBILITY.md) as the canonical repeatable
  compatibility procedure and matrix.
- Use [`SCENARIOS.md`](SCENARIOS.md) and `tests/feature_scenarios.json` as the
  feature-level test ledger.
- Use [`TEST_DESIGN_STANDARD.md`](TEST_DESIGN_STANDARD.md) and
  `tests/test_design_criteria.json` as the automated quality bar for test
  design, check density, random/stress process, and build-set artifacts.
- Use [`compat-audit-latest.md`](compat-audit-latest.md) as the latest generated
  compatibility snapshot.
- Use [`LLAMA_BENCHMARKS.md`](LLAMA_BENCHMARKS.md) as the human-readable
  benchmark history, with JSON files kept as machine-readable artifacts.
- Link to [`../design/DOCKER_COMPAT_SCOPE.md`](../design/DOCKER_COMPAT_SCOPE.md)
  for product boundaries and to [`../design/GPU_COMPAT.md`](../design/GPU_COMPAT.md)
  for GPU design rules.

## Maintenance

- Keep command examples reproducible from the repository root.
- Keep generated or recorded results in this category.
- Move product boundary decisions to [`../design/README.md`](../design/README.md).
- Move active implementation tasks to [`../plan/TODO.md`](../plan/TODO.md).

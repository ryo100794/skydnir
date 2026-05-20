# ADB-Off Task Queue: 2026-05-20

This queue lists work that can move while no Android device is connected.  It
is intentionally separate from device promotion gates: host-only work may
prepare verifiers, docs, negative tests, inventories, and generated evidence
summaries, but it must not claim runtime, terminal, service-health, GPU, SAF,
or teardown promotion without fresh device artifacts.

## Operating Rules

- Assume ADB is off until the user provides a fresh endpoint.
- Prefer host-only tasks with deterministic commands, synthetic fixtures, or
  static checks.
- Keep device-gated lanes visible as `planned-gap` or non-promoting.
- Convert every useful agent result into source, tests, focused docs, TODO, or
  `AGENT_COORDINATION.md`; do not rely on chat transcript memory.
- Run `python3 scripts/verify-docs-maintenance.py`, relevant unit tests, and
  `git diff --check` before committing docs/test maintenance.

## Completed ADB-Off Landing Ledger

ADBOFF-001 through ADBOFF-039 have landed. Keep this table as the
completion ledger for the 2026-05-20 ADB-off maintenance burst; append new
rows only when fresh host-only work is intentionally queued.

| ID | Priority | Task | Host-only acceptance | Status |
|---|---:|---|---|---|
| ADBOFF-001 | P0 | Separate historical device evidence from current promotion claims. | Docs use historical/non-promoting wording, and docs maintenance rejects `Good`/`PASS` rows whose notes say a gate remains open. | Done in `3b151a6`; static guard added in the next maintenance slice. |
| ADBOFF-002 | P0 | Keep design/TODO/API boundaries synchronized. | `docs/plan/TODO.md`, compatibility docs, and design docs list Docker API subset, pdocker extensions, unsupported OCI features, and Bluetooth/BLE/GPS future broker scope. | Done in `eefae1d`. |
| ADBOFF-003 | P0 | Preserve llama GPU Q6 probe details in committed sweep evidence. | `tests.test_llama_gpu_artifact_sweep` and `tests.test_llama_gpu_artifact_verifier` pass; sweep JSON exposes Q6 output-layout, row-provenance, partial-signature, and native-reduction fields. | Done in `eefae1d`. |
| ADBOFF-004 | P0 | Add a static stale-evidence guard to prevent repeat docs regressions. | `tests.test_docs_maintenance` covers forbidden current-evidence phrases and compatibility rows that combine promoting status with open/non-promoting notes. | Done in `5946442`. |
| ADBOFF-005 | P1 | Source marker audit while device is unavailable. | Explorer output says app UI source has no uncovered actionable TODO/FIXME/HACK markers; native/runtime findings are either covered or low-risk naming cleanup. | Done; recorded in `AGENT_COORDINATION.md` in this slice. |
| ADBOFF-006 | P1 | Maintain this ADB-off queue and plan index. | This document remains linked from `docs/plan/README.md` and referenced by the coordination ledger. | Ongoing maintenance; current queue items ADBOFF-001 through ADBOFF-039 have landed. |
| ADBOFF-007 | P1 | Keep release/readiness checks green after docs maintenance. | `verify-docs-maintenance`, `verify-release-readiness`, `tests.test_docs_maintenance`, and `git diff --check` pass. | Done for `5946442`; rerun after each maintenance slice. |
| ADBOFF-008 | P1 | Add static pdocker extension API boundary guard. | Docs distinguish Docker-standard `GET /system/df` and `POST /system/prune` from pdocker-only `/system/*` routes, and every public `Pdocker*` field observed in pdockerd is documented. | Done in `50edf2f`. |
| ADBOFF-009 | P1 | Review Engine route method strictness from static audit. | Host-only follow-up should document or test broad base-route methods and the generic `POST /networks/{name}` fallback before any runtime behavior change. | Done: base routes are method-scoped and unsupported network subroutes fail closed in host protocol smoke. |
| ADBOFF-010 | P1 | Add a high-churn test evidence index. | `docs/test/EVIDENCE_INDEX.md` classifies `latest` artifacts as host/static, device-gated, non-promoting, or historical and links each family to canonical gate docs. | Done in `09da6a5`. |
| ADBOFF-011 | P1 | Guard script inventory against duplicate implementation drift. | `verify-script-inventory.py` fails when a candidate subfolder implementation exists while the top-level entry is still marked as a planned move instead of a migrated compatibility wrapper; unit tests cover current, failing, and allowed cases. | Done in this slice. |
| ADBOFF-012 | P1 | Migrate the Vulkan ICD smoke helper behind the categorized test layout. | The top-level Vulkan ICD smoke entrypoint remains a stable wrapper, the implementation lives at `scripts/test/smoke-vulkan-icd-bridge.sh`, repo-local docs/manifests use the implementation path, and `verify-ui-actions.py` reads the migrated body. | Done in this slice. |
| ADBOFF-013 | P1 | Refresh or rebuild stale APK-native payload evidence before using `verify-fast` as a full green signal. | `verify-fast` validates existing compat APK payload freshness when the APK exists and now prints the rebuild/removal remedy before the fail-closed native-payload check; the local compat APK was rebuilt and `docs/test/native-payloads-latest.json` refreshed. | Done in this slice. |
| ADBOFF-014 | P1 | Pin the script inventory surface budget. | `verify-script-inventory.py` fails when top-level script count, subtree entry count, or category counts change without a focused verifier/README update; unit tests cover current and failing budgets. | Done in this slice. |
| ADBOFF-015 | P1 | Improve root docs map discoverability. | Root `README.md` links every docs category README listed by `docs/README.md`, including release, showcase, and maintenance, without duplicating status claims. | Done in this slice. |
| ADBOFF-016 | P1 | Fill test/showcase README index gaps. | `docs/test/README.md` links maintained test docs such as COW overlay bench recovery, direct syscall phase-2 coverage, and llama GPU root-cause/performance/correctness notes; generated `docs/showcase/README.md` now lists the curated news timeline through `scripts/update-showcase.py`. | Done. |
| ADBOFF-017 | P1 | Guard root docs map against category drift. | `verify-docs-maintenance.py` compares `docs/README.md` category indexes with root `README.md` Documentation map entries after normalizing directory links to category READMEs; tests cover matching, missing, and extra entries. | Done in this slice. |
| ADBOFF-018 | P1 | Add a small synthetic fixture for APK native-payload packaging policy. | `tests.test_native_payload_verifier_synthetic` exercises required asset presence, source-byte freshness, and forbidden `__pycache__` packaging without depending on a built APK artifact; `verify-fast.sh` runs it with the host-only suite. | Done in this slice. |
| ADBOFF-019 | P2 | Clean stale docs wording now that the shared test evidence index exists. | Maintenance docs link `docs/test/EVIDENCE_INDEX.md`, describe duplicate/latest records as representative pointer/evidence patterns, and `docs/test/README.md` keeps raw script paths in a related-script section instead of the document table. | Done in this slice. |
| ADBOFF-020 | P1 | Guard committed `docs/test/*latest*` evidence discoverability. | `verify-docs-maintenance.py` fails if any top-level committed latest artifact lacks an owner reference in `EVIDENCE_INDEX.md`, the test README, CI gate ledger, or registered scenario/test manifests; unit tests cover indexed and unowned fixtures. | Done in this slice. |
| ADBOFF-021 | P2 | Guard script maintenance triage against inventory drift. | `verify-script-inventory.py` now checks that `docs/maintenance/SCRIPT_DOC_INVENTORY.md` carries the current category counts and obsolete-suspect candidates from `scripts/script-inventory.json`; unit tests cover stale counts and names. | Done in this slice. |
| ADBOFF-022 | P2 | Retire the obsolete llama startup logging helper after maintained unittest coverage. | `tests.test_llama_startup_logging_contract` now owns the fake-profile, early-tee, startup-json, resolved env, memory, and KV-offload guard checks; `scripts/verify-llama-startup-logging.py` is deleted and script inventory/docs counts are updated. | Done in this slice. |
| ADBOFF-023 | P2 | Retire the obsolete box64 NDK wrapper. | `scripts/wrap-ndk-box64.sh` is deleted because the supported native-build path is `scripts/build-native-android-ndk.sh` with host-clang/aarch64 coverage and no box64 mutation; inventory/docs counts now leave only the terminal repro obsolete suspect. | Done in this slice. |
| ADBOFF-024 | P1 | Guard nested `docs/test/**latest*` evidence ownership. | `verify-docs-maintenance.py` scans latest files recursively, accepts documented latest-artifact directories, and unit tests cover nested owned/unowned artifacts; the ADB-off queue is reframed as a completed maintenance ledger. | Done in this slice. |
| ADBOFF-025 | P1 | Promote obsolete-suspect audit metadata to an executable inventory guard. | `verify-script-inventory.py` now rejects obsolete-suspect entries without dated reference-scan evidence, a delete/archive/retire decision, and a replacement or retirement condition; unit tests cover missing audit metadata, vague scans, missing replacements, and condition-free decisions. | Done in this slice. |
| ADBOFF-026 | P1 | Make latest-evidence ownership matching exact instead of substring-based. | `verify-docs-maintenance.py` extracts normalized owner tokens from evidence owner docs/manifests, refuses accidental suffix/directory substring matches, and rejects generic nested child basename ownership unless the full path or latest-artifact directory is documented. | Done in this slice. |
| ADBOFF-027 | P1 | Guard ADB-off ledger and obsolete-suspect count wording against drift. | `verify-docs-maintenance.py` checks the highest ADBOFF row against the completion prose and ADBOFF-006 status, and rejects stale numeric obsolete-suspect wording in `AGENT_COORDINATION.md` when it disagrees with `scripts/script-inventory.json`. | Done in this slice. |
| ADBOFF-028 | P1 | Guard release-readiness CI trigger coverage. | `.github/workflows/release-readiness.yml` runs on release docs, notices, metadata, verifier, workflow, and native/staged payload input changes; `tests.test_release_readiness_notice_audit` checks each required path is present for both pull request and main push triggers. | Done in `83e775e` and tightened in `199c394`. |
| ADBOFF-029 | P1 | Require docs-facing ownership for committed latest evidence. | `verify-docs-maintenance.py` rejects `docs/test/**latest*` artifacts that are only manifest-owned, unit tests cover the failure mode, and `docs/test/EVIDENCE_INDEX.md` indexes `android-blas-cmake-build-latest.log` under Native / release hygiene. | Done in `199c394`. |
| ADBOFF-030 | P1 | Wire the APK memory pager contract into the fast host gate. | `scripts/verify-fast.sh` now py-compiles and runs `scripts/verify-memory-pager-contract.py` and includes `tests.test_apk_memory_pager_contract`, matching the Task H TODO claim that this verifier guards the pager evidence contract. | Done in this slice. |
| ADBOFF-031 | P0 | Wire OOM/LMK survival static gate into the fast host gate. | `scripts/verify-fast.sh` py-compiles and runs `scripts/verify-oom-lmk-survival-gate.py` and includes `tests.test_oom_lmk_survival_gate`, keeping large-allocation/backend-death evidence non-promoting but always checked. | Done in this slice. |
| ADBOFF-032 | P1 | Wire media bridge readiness contract into the fast host gate. | `scripts/verify-fast.sh` includes `tests.test_media_bridge_contract` so audio/video descriptor/socket/env readiness stays fail-closed until real capture/playback device evidence exists. | Done in this slice. |
| ADBOFF-033 | P1 | Wire no-PRoot runtime-truth artifact checks into the fast host gate. | `scripts/verify-fast.sh` py-compiles `scripts/verify-no-proot-runtime-truth-artifact.py` and includes `tests.test_no_proot_runtime_truth_artifact_verifier`, preserving the current non-promoting runtime-truth evidence contract. | Done in this slice. |
| ADBOFF-034 | P1 | Wire COW overlay kill-at-step static artifact gate into the fast host gate. | `scripts/verify-fast.sh` py-compiles and validates `docs/test/cow-overlay-kill-at-step-latest.json` with `scripts/verify/runner/cow_overlay_kill_at_step_device.py --validate-artifact`, preserving the planned-gap artifact contract without rewriting evidence during fast verification. | Done in this slice. |
| ADBOFF-035 | P1 | Wire llama GPU blocker artifact classification into the fast host gate. | `scripts/verify-fast.sh` py-compiles `scripts/verify-llama-gpu-artifact.py` and validates `docs/test/llama-gpu-workgroup3d-preflight-20260513.json --allow-memory-blocker`, so memory-blocker artifacts stay classified and non-promoting. | Done in this slice. |
| ADBOFF-036 | P1 | Add missing exact CI-ledger contract unit tests to the fast host gate. | `scripts/verify-fast.sh` includes `tests.test_memory_pager_contract` and `tests.test_service_truth_artifact_contract`, matching the OOM/LMK CI ledger fast/static contract row. | Done in this slice. |
| ADBOFF-037 | P1 | Mirror terminal session-neutrality in the lightweight terminal/editor contract script. | `scripts/verify_terminal_editor_contracts.py` rejects Docker/Engine/PTY/session-routing tokens in the generic xterm surface and allows only generic bridge calls, matching the existing focused unittest boundary. | Done in this slice. |
| ADBOFF-038 | P2 | Normalize Showcase category wording in hand-maintained docs indexes. | Root `README.md` and `docs/README.md` describe Showcase as generated or curated dashboard, roadmap, news, and Wiki seed pages while leaving generated `docs/showcase/README.md` untouched. | Done in this slice. |
| ADBOFF-039 | P1 | Add CI ledger to fast-gate parity guard. | `tests.test_ci_gate_ledger` parses the CI gate table's fast/static commands and fails when script paths or unittest modules are not represented in `scripts/verify-fast.sh`, unless a reasoned exemption is added. | Done in this slice. |

## Deferred Until ADB Returns

These remain intentionally blocked from promotion:

- terminal `exec -it` UI/JSONL evidence;
- service truth same-container-ID evidence;
- runtime teardown proof;
- live image-pull interruption/crash-safety evidence;
- SAF direct output evidence;
- VS Code health proof against the current Engine container ID;
- llama GPU correctness/performance device compare and native Q6_K final-store
  investigation.

## Future Host-Only Maintenance Candidates

Use these when the current queue drains and ADB is still off:

1. Add narrow synthetic fixtures for any remaining verifier that currently
   relies on a large generated artifact.
2. Keep docs/readme discoverability under the existing maintenance guards and
   add only focused link/index updates when new maintained docs appear.
3. Extend API extension boundary checks only when new `/system/*` routes or
   `Pdocker*` fields are introduced.
4. Review low-risk naming noise such as GPU `temporary` scratch buffers, but
   avoid runtime behavior changes unless a focused host test already covers the
   path.

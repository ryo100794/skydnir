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

## Active ADB-Off Task List

| ID | Priority | Task | Host-only acceptance | Status |
|---|---:|---|---|---|
| ADBOFF-001 | P0 | Separate historical device evidence from current promotion claims. | Docs use historical/non-promoting wording, and docs maintenance rejects `Good`/`PASS` rows whose notes say a gate remains open. | Done in `3b151a6`; static guard added in the next maintenance slice. |
| ADBOFF-002 | P0 | Keep design/TODO/API boundaries synchronized. | `docs/plan/TODO.md`, compatibility docs, and design docs list Docker API subset, pdocker extensions, unsupported OCI features, and Bluetooth/BLE/GPS future broker scope. | Done in `eefae1d`. |
| ADBOFF-003 | P0 | Preserve llama GPU Q6 probe details in committed sweep evidence. | `tests.test_llama_gpu_artifact_sweep` and `tests.test_llama_gpu_artifact_verifier` pass; sweep JSON exposes Q6 output-layout, row-provenance, partial-signature, and native-reduction fields. | Done in `eefae1d`. |
| ADBOFF-004 | P0 | Add a static stale-evidence guard to prevent repeat docs regressions. | `tests.test_docs_maintenance` covers forbidden current-evidence phrases and compatibility rows that combine promoting status with open/non-promoting notes. | Done in `5946442`. |
| ADBOFF-005 | P1 | Source marker audit while device is unavailable. | Explorer output says app UI source has no uncovered actionable TODO/FIXME/HACK markers; native/runtime findings are either covered or low-risk naming cleanup. | Done; recorded in `AGENT_COORDINATION.md` in this slice. |
| ADBOFF-006 | P1 | Maintain this ADB-off queue and plan index. | This document is linked from `docs/plan/README.md` and referenced by the coordination ledger. | In progress. |
| ADBOFF-007 | P1 | Keep release/readiness checks green after docs maintenance. | `verify-docs-maintenance`, `verify-release-readiness`, `tests.test_docs_maintenance`, and `git diff --check` pass. | Done for `5946442`; rerun after each maintenance slice. |
| ADBOFF-008 | P1 | Add static pdocker extension API boundary guard. | Docs distinguish Docker-standard `GET /system/df` and `POST /system/prune` from pdocker-only `/system/*` routes, and every public `Pdocker*` field observed in pdockerd is documented. | Done in `50edf2f`. |
| ADBOFF-009 | P1 | Review Engine route method strictness from static audit. | Host-only follow-up should document or test broad base-route methods and the generic `POST /networks/{name}` fallback before any runtime behavior change. | Done: base routes are method-scoped and unsupported network subroutes fail closed in host protocol smoke. |
| ADBOFF-010 | P1 | Add a high-churn test evidence index. | `docs/test/EVIDENCE_INDEX.md` classifies `latest` artifacts as host/static, device-gated, non-promoting, or historical and links each family to canonical gate docs. | Done in `09da6a5`. |
| ADBOFF-011 | P1 | Guard script inventory against duplicate implementation drift. | `verify-script-inventory.py` fails when a candidate subfolder implementation exists while the top-level entry is still marked as a planned move instead of a migrated compatibility wrapper; unit tests cover current, failing, and allowed cases. | Done in this slice. |

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

## Next Host-Only Candidates

Use these when the current queue drains and ADB is still off:

1. Add narrow synthetic fixtures for any verifier that currently relies on a
   large generated artifact.
2. Improve docs/readme discoverability and reduce duplicate status wording.
3. Add static checks for API extension boundaries: `/system/*` and `Pdocker*`
   fields must remain documented as pdocker-only diagnostics.
4. Review low-risk naming noise such as GPU `temporary` scratch buffers, but
   avoid runtime behavior changes unless a focused host test already covers the
   path.

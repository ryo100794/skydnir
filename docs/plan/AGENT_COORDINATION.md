# Agent Coordination Ledger

Last updated: 2026-05-19

This ledger records active delegation lanes and integration risks for the main
agent. The main agent owns waiting on agents, closing lanes, and integrating
final changes. The coordinator only records current expectations and flags
risks; it does not edit implementation files or resolve sibling work.

## Durable Workflow Memory

This file is the durable memory for the multi-agent development workflow. When
session context is compacted or moved, the next main agent must read this file
before launching new work.

The standing workflow is:

1. The main agent acts as manager, integrator, git owner, and final reviewer.
2. Keep several small, narrowly scoped agents active when delegation is useful.
3. Prefer small tasks with explicit ownership and disjoint write scopes.
4. Recover completed agents quickly, close them, integrate their output, then
   launch the next small task if the backlog still has independent work.
5. Use explorer agents for audits and design review; use worker agents for
   bounded code or documentation patches.
6. If several work streams need reconciliation, delegate a short integration or
   review pass rather than letting conflicts accumulate silently.
7. Preserve all important agent output in code, tests, `docs/plan/TODO.md`,
   focused design/test documents, or this coordination ledger.
8. Commit and push only after integration review, focused tests, `git diff
   --check`, and an explicit check that untracked moved files are staged.

## Compaction Handoff Snapshot

- Latest committed base before the current local docs-only follow-up:
  `be93398` ("Refresh GPU artifact sweep") as observed locally on
  2026-05-19; preceding pushed slice includes `ed7cddd`, `3b8eecb`,
  `e1a806d`, `e0612a3`, `753670c`, and `5dc330a`.
- Last known green external gate: CI Showcase succeeded for `2ce8396`.
- Last known green local default gate before this slice: `bash
  scripts/verify-fast.sh`.
- Default operating loop: read this ledger, inspect repo status, recover/close
  sibling lanes, integrate only narrow owned changes, run focused checks plus
  `git diff --check`, then commit/push only reviewed work.
- Future agents must open this file before relying on chat history, especially
  after compaction or handoff.

## Context Hygiene Rule

The main agent must not keep raw agent transcripts, long command logs, or large
diffs in conversational context. Keep only compact facts needed for the next
decision:

- active lane, owner, write scope, and blocker;
- files changed and validation commands;
- commit SHA or artifact path after the result is landed;
- next action and acceptance gate.

Everything else must be reduced into source files, tests, focused docs, or this
ledger. If a future session needs details, it should open the referenced file or
artifact instead of relying on retained chat history.

## Active Lanes

| Lane | Owner | Write scope | Expected deliverable | Integration risks |
| --- | --- | --- | --- | --- |
| Llama GPU Q6 classifier/oracle boundary | Main agent | GPU bridge code plus llama GPU compare/verifier artifacts | Strict safe-kernel diagnostic enabled/classified in `5dc330a`/`e1a806d`; `3b8eecb` added the native Q6_K reduction/output-layout probe and `be93398` refreshed the sweep. The 2026-05-19 strict device artifact reached native Q6_K with verified writeback and canonical reduction-tree math, then classified `q6-native-output-layout-inconclusive`; the active blocker is native final-store/output-layout/device execution, not bridge writeback/descriptors | Must keep llama.cpp unmodified; diagnostics remain non-promoting until native Q6_K matches and benchmark claims are allowed |
| Connected Android device status | Goodall | read-only ADB at `192.168.179.26:39565` | Device connected; package `io.github.ryo100794.pdocker.compat` installed; `pdockerd`, `pdocker-gpu-executor`, and `pdocker-media-executor` observed running | Do not force-stop, reinstall, or start long builds unless explicitly assigned |
| Service truth same-container-ID | Assigned P0 worker | Issue #6 implementation/tests/artifacts only | Produce the same-current-Engine-container-ID service truth artifact across UI, docker ps/API, state, process table, listener owner, and logs | Coordinate around `scripts/android-device-smoke.sh`; do not overlap with runtime teardown or terminal exec-it edits |
| Image-pull crash safety | Assigned P0 worker | Issue #11 implementation/tests/artifacts only | Produce scenario-owned interrupted pull/restart evidence without publishing partial/user tags | Keep live registry interruption non-promoting unless the fixture is explicitly isolated and owned |
| COW/overlay mutation safety | Assigned P0 worker | Issue #12 implementation/tests/artifacts only | Produce daemon/helper kill-at-step restart evidence for copy-up, rename, whiteout, archive, and metadata checkpoints | Coordinate with runtime smoke ownership before touching shared device-smoke helpers |
| Runtime teardown | Committed gate slice | Runtime teardown docs/scripts/tests/artifacts already landed outside this docs-only follow-up | Gate hardening committed as `2ce8396`; CI Showcase succeeded; next deliverable is real adb/run-as teardown evidence before promotion | Future edits still conflict on `scripts/android-device-smoke.sh`; serialize with service/image/COW workers |
| Terminal exec-it | Committed gate slice | Terminal exec/UI/session docs/scripts/tests/artifacts already landed outside this docs-only follow-up | Gate hardening committed as `2ce8396`; CI Showcase succeeded; next deliverable is raw JSONL plus UI artifact evidence before promotion | Future edits still conflict on `scripts/android-device-smoke.sh`; serialize with runtime/service-smoke coordination |
| Modern/no-PRoot runtime truth | Committed gate slice | `docs/test/NO_PROOT_RUNTIME_TRUTH_GATE.md`, `docs/test/no-proot-runtime-truth-latest.json`, and verifier/scripts already landed outside this docs-only follow-up | Truth gate added and pushed in `e0612a3`; ledger sync landed in `753670c`; next deliverable is real no-PRoot executor evidence or explicit runtime capability-error evidence before promotion | Do not let metadata-only flavors claim RUN, Compose service health, or published-port success without executor evidence |
| Low-conflict docs backlog | Pauli-derived queue | docs-only scopes under release/test/plan/maintenance README ownership | Delegate release dedup, GPU/storage evidence indexes, memory/terminal link cleanup, F-Droid consistency, test evidence retention, and plan/status cross-link hygiene as independent tasks | Avoid touching GPU/runtime implementation while these docs lanes run |

## Recently Recovered Agent Results

| Result | Owner | Landed as | Follow-up |
| --- | --- | --- | --- |
| Llama GPU readiness artifacts | Linnaeus | `/completion` readiness probe, runtime GPU env snapshot, and timeout classifier | Rerun Android compare to populate device artifacts; do not claim benchmark success on timeout |
| Llama startup evidence | Wegener | early profile logging and `llama-startup.json` contract | Confirm startup JSON appears in `docker logs`/workspace during next device run |
| TODO decomposition audit | Harvey | integrated into audit/TODO triage | Keep device-gated lanes open until fresh Android artifacts promote them |
| Placeholder/Documents cleanup | Averroes | bounded dev-workspace repair comment, static guard, Documents placeholder wording | Device SAF direct-output gate remains open |
| Build environment consolidation | Boyle | `scripts/build-all.sh`, `PDOCKER_SKIP_NATIVE_BUILD`, build docs | Treat as local convenience, not the full release/F-Droid process |
| TODO lane decomposition | Sartre | Used to assign non-overlapping work | Keep GPU implementation local; delegate low-conflict test/docs lanes |
| Direct syscall coverage lane | Banach | `scripts/run_direct_syscall_scenarios.py --lane local`, `tests/direct_syscall/`, `docs/test/DIRECT_SYSCALL_COVERAGE.md` | Included in `scripts/verify-fast.sh` |
| Storage metrics validation lane | Leibniz | `scripts/verify-storage-metrics.py`, `docs/test/STORAGE_METRICS.md` | Included in `scripts/verify-fast.sh`; device metric checks remain TODO |
| Terminal / `-it` investigation | Hypatia | Root cause captured in TODO: direct executor argv rewrite and readonly selection IME | Implement after GPU-safe checkpoint; add direct `/usr/bin/[` smoke |
| F-Droid/reproducible-build readiness | Carver | `docs/release/FDROID_RELEASE_PROCESS.md`, `metadata/fdroid/README.md` | Keep runtime container downloads documented as user-directed product behavior |
| Docs maintenance verifier | Linnaeus | `scripts/verify-docs-maintenance.py`, `tests/test_docs_maintenance.py`, fast gate hook | Run with docs/script cleanup commit |
| Terminal exec doc canonicalization | Poincare | `docs/test/TERMINAL_EXEC_IT_DEVICE_GATE.md`, `docs/test/SCENARIOS.md` | Keep architecture doc as stream-boundary source of truth |
| Memory/OOM gate canonicalization | Halley | `docs/test/APK_MEMORY_PAGER_PROBE.md`, `docs/test/OOM_LMK_SURVIVAL_GATE.md` | Keep design docs as policy source of truth |
| Roadmap/agent-state hygiene | Gibbs/Franklin | `scripts/verify-docs-maintenance.py`, `tests/test_docs_maintenance.py`, TODO/showcase docs | Landed in `a3325bd`; active TODO entries now require evidence cues and stale historical `running` rows are rejected |
| Script runner inventory | Hume | `scripts/script-inventory.json`, `tests/test_script_inventory_audit.py`, `scripts/README.md` | Integrated locally: `scripts/verify/runner/*` registered as subtree entries |
| Pycache cleanup policy | Curie | `.gitignore`, single TODO wording update | Integrated locally: `__pycache__` remains ignored/local and outside script inventory |
| Wrapper migration audit | Descartes | read-only | Reference migration follow-up committed as `0e9b33e`; next slice is wrapper retirement only after the compatibility window |
| CI release-readiness clean-checkout payload | Helmholtz/Archimedes | `scripts/verify-release-readiness.py`, `tests/test_release_readiness_notice_audit.py`, `metadata/fdroid/generated-binary-inventory.md` | Local fix distinguishes gitignored generated/staged payload rows from missing source-tree payloads; next check is GitHub Release readiness rerun after push |
| Llama GPU next-step audit | Anscombe | read-only | Next GPU action: fresh APK/readiness/Q6_K row-indexed artifact before further C changes |
| Script/doc drift guard | Main agent | `f053df0` | Pushed guard for script/doc maintenance drift; continue using docs-maintenance and script-inventory checks before broad cleanup |
| Runtime teardown / terminal evidence gates | Main agent | `2ce8396` | CI Showcase succeeded; both gates remain non-promoting until fresh device evidence lands |
| No-PRoot runtime truth gate | Main agent/Herschel | `e0612a3` then ledger sync `753670c` | Gate and planned-gap artifact pushed; promotion still requires no-PRoot executor evidence or explicit capability-error artifact |
| Llama Q6 safe-kernel/native layout diagnostics | Main agent | `5dc330a` / `e1a806d` / `3b8eecb` / `be93398` plus 2026-05-19 device artifact | Strict safe-kernel diagnostic is enabled and classified; `3b8eecb` adds the native Q6_K reduction/output-layout probe and `be93398` refreshes the sweep. Local artifact `docs/test/llama-gpu-ngl1-q6-native-output-layout-20260519.json` is ignored/generated evidence but classified the native lane as `q6-native-output-layout-inconclusive`; next code work should bisect native final-store/output-layout versus device execution |

## Intake Rule

The main agent waits for sibling agents, closes their work, resolves conflicts,
and integrates final changes before commit or push. The coordinator only records
lane state, ownership, write scopes, expected deliverables, and risks in this
ledger.

Agent output is durable only after it is moved into one of these places:

1. Implementation or test files, when the result is directly landed.
2. `docs/plan/TODO.md`, when the result creates unfinished work or acceptance
   criteria.
3. A focused design/test document, when the result is background knowledge that
   should survive context compaction.
4. `docs/plan/AGENT_COORDINATION.md`, when the result affects delegation,
   ownership, or integration risk.

## Timeline Rule

The public roadmap is generated from `docs/plan/TODO.md` by
`scripts/update-showcase.py`. To put an agent result on the timeline:

1. Convert it into a `- [doing]`, `- [next]`, `- [blocked]`, or `- [done]`
   entry in the active TODO board or the relevant detailed section.
2. Add an acceptance check or artifact path when the item is testable.
3. Run `python3 scripts/update-showcase.py`.
4. Verify `docs/showcase/ROADMAP_TIMELINE.md` changed as expected.

## Conflict Risks

- Scripts/docs/template overlap is the main risk. New agents should be given
  narrow write scopes and should avoid GPU files unless they own a GPU lane.
- Generated docs/showcase updates should be reviewed against their source
  commands or templates so generated churn is not mistaken for hand-authored
  intent.
- Fast-test additions can change the default developer gate; keep them
  lightweight and ADB-free unless explicitly marked heavy.
- F-Droid/release-process docs must not claim readiness before source-built
  native payload and reproducibility checks exist.
- Terminal `-it` fixes touch direct executor behavior and should be tested with
  package-manager and shell expression cases, not only UI copy/paste tests.

## Verifier Backlog For Context-Loss Prevention

The following rules are important enough that they should move from prose into
verifiers before the next broad documentation cleanup:

1. Agent coordination drift: guarded by `scripts/verify-docs-maintenance.py`,
   which rejects stale `running` assignments outside this ledger.
2. Timeline source quality: guarded by `scripts/verify-docs-maintenance.py`,
   which requires active TODO entries to carry an issue link, artifact path,
   verifier, or acceptance cue before they can remain on the roadmap.
3. Script migration completion: `scripts/verify-script-inventory.py` should
   eventually scan docs, `.github/`, and test manifests before any migrated
   top-level wrapper can be removed. Wrapper reference migration landed as
   `0e9b33e`; script/doc drift guard landed as `f053df0`; auto-add wrapper
   retirement only after the compatibility window.
4. Documentation discoverability: every new `docs/**/*.md` should be reachable
   through its category README or the maintenance backlog owner map.
5. Issue workflow parity: major active TODO items should include `[#N]` unless
   the TODO entry documents why it is local-only or historical.
6. Release clean-checkout guard: before near-complete release readiness, auto-add
   a verifier task that fails when generated payloads appear in a clean checkout.

## Main Agent Pre-Commit Checklist

- Confirm each sibling lane has either delivered final changes or been explicitly
  deferred by the main agent.
- Review changed files by owner/write scope and flag any cross-lane edits
  before staging.
- Run the recovered lane validations plus llama GPU bridge checks.
- Re-run any generated docs/showcase commands needed to make source and output
  agree.
- Inspect `git diff --check` for the full repo before commit/push, not only this
  ledger.
- Confirm commit contents exclude unrelated local edits and coordinator-only
  changes remain limited to this file.

# Agent Coordination Ledger

Last updated: 2026-05-20

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

## Compaction-Safe Handoff Protocol

Use this protocol whenever the next step would be detailed, risky, or broad and
the remaining conversation context is no longer enough to carry the plan, diff,
validation output, and rollback notes without compaction. Treat device/runtime,
GPU, Dockerfile, llama.cpp, and cleanup work as risky by default.

1. **Stop before opening a large new seam.** Do not begin a new multi-file patch,
   long device run, refactor, or ambiguous investigation when a compaction notice
   has appeared, when raw logs/diffs are crowding out decision context, or when
   you cannot still reserve room for a clear handoff and validation summary.
2. **Summarize first.** Update this ledger or the canonical task document with:
   lane, owner, write scope, current diff/commit, commands already run, failing
   evidence, blockers, and the next smallest safe action.
3. **Checkpoint deliberately.** If you are the integrator and the slice is
   reviewed, commit the small completed checkpoint after focused validation. If
   the slice is not ready to commit, leave the tree in the smallest reviewable
   state and record exact changed paths plus unfinished commands; do not start
   another risky seam to “make it worth committing.”
4. **Delegate instead of expanding context.** Split follow-up work into a narrow
   agent lane with disjoint write ownership and an acceptance artifact, then let
   the next session recover from this ledger rather than from raw chat history.

### Low-Context Patch Budget Rule

No large new patch may start when context budget is low. “Large” means any
change that spans multiple ownership surfaces, touches runtime/GPU/Dockerfile or
device-gated behavior, requires long logs to understand, or cannot be described
with rollback notes in ten concise bullets. In that state, only do one of these:

- write or refresh a handoff/checklist entry in `docs/plan/AGENT_COORDINATION.md`,
  `docs/plan/TODO.md`, or the relevant canonical gate doc;
- run/read a short verifier whose output can be summarized in a few lines;
- land an already-reviewed small checkpoint;
- delegate a scoped follow-up instead of continuing locally.

### Concise Agent Artifact Reporting

Agents should not flood the main context with transcripts. Report a compact
artifact bundle instead:

- changed paths, commit SHA if any, and one-sentence purpose;
- exact validation commands and PASS/FAIL/blocked status;
- durable artifact paths such as `docs/test/*latest*.json`, focused run
  summaries, or task-ledger rows;
- at most three blockers/next actions.

Raw logs, giant diffs, screenshots, and exploratory notes belong in durable
artifacts or focused docs only when they are needed for repeatability; otherwise
record the path or command that can reproduce them.

## Active Lanes

| Lane | Owner | Write scope | Expected deliverable | Integration risks |
| --- | --- | --- | --- | --- |
| Llama GPU Q6 classifier/oracle boundary | Main agent | GPU bridge code plus llama GPU compare/verifier artifacts | Current classifier is `q6-native-device-execution-or-final-store` from non-promoting diagnostic evidence. Next work is native final-store versus executor/Vulkan device-execution bisection while preserving synchronized diagnostic env propagation. | Must keep llama.cpp unmodified; diagnostics remain non-promoting until native Q6_K matches and benchmark claims are allowed |
| Connected Android device status | Goodall | read-only ADB when the user provides a fresh endpoint | Last observed on 2026-05-19 at `192.168.179.26:39565` with package `io.github.ryo100794.pdocker.compat` installed and `pdockerd`, `pdocker-gpu-executor`, and `pdocker-media-executor` running. Current status is unverified; assume ADB is off unless a new endpoint is provided. | Do not force-stop, reinstall, or start long builds unless explicitly assigned |
| Service truth same-container-ID | Assigned P0 worker | Issue #6 implementation/tests/artifacts only | Produce the same-current-Engine-container-ID service truth artifact across UI, docker ps/API, state, process table, listener owner, and logs | Coordinate around `scripts/android-device-smoke.sh`; do not overlap with runtime teardown or terminal exec-it edits |
| Image-pull crash safety | Assigned P0 worker | Issue #11 implementation/tests/artifacts only | Produce scenario-owned interrupted pull/restart evidence without publishing partial/user tags | Keep live registry interruption non-promoting unless the fixture is explicitly isolated and owned |
| COW/overlay mutation safety | Assigned P0 worker | Issue #12 implementation/tests/artifacts only | Produce daemon/helper kill-at-step restart evidence for copy-up, rename, whiteout, archive, and metadata checkpoints | Coordinate with runtime smoke ownership before touching shared device-smoke helpers |
| Runtime teardown | Committed gate slice | Runtime teardown docs/scripts/tests/artifacts already landed outside this docs-only follow-up | Gate hardening committed as `2ce8396`; CI Showcase succeeded; next deliverable is real adb/run-as teardown evidence before promotion | Future edits still conflict on `scripts/android-device-smoke.sh`; serialize with service/image/COW workers |
| Terminal exec-it | Committed gate slice | Terminal exec/UI/session docs/scripts/tests/artifacts already landed outside this docs-only follow-up | Gate hardening committed as `2ce8396`; CI Showcase succeeded; next deliverable is raw JSONL plus UI artifact evidence before promotion | Future edits still conflict on `scripts/android-device-smoke.sh`; serialize with runtime/service-smoke coordination |
| Modern/no-PRoot runtime truth | Committed gate slice | `docs/test/NO_PROOT_RUNTIME_TRUTH_GATE.md`, `docs/test/no-proot-runtime-truth-latest.json`, and verifier/scripts already landed outside this docs-only follow-up | Truth gate added and pushed in `e0612a3`; ledger sync landed in `753670c`; next deliverable is real no-PRoot executor evidence or explicit runtime capability-error evidence before promotion | Do not let metadata-only flavors claim RUN, Compose service health, or published-port success without executor evidence |
| Low-conflict docs backlog | Pauli-derived queue | docs-only scopes under release/test/plan/maintenance README ownership | Delegate release dedup, GPU/storage evidence indexes, memory/terminal link cleanup, F-Droid consistency, test evidence retention, and plan/status cross-link hygiene as independent tasks | Avoid touching GPU/runtime implementation while these docs lanes run |
| ADB-off host-only queue | Main agent plus short explorer agents | `docs/plan/ADB_OFF_TASK_QUEUE_20260520.md`, docs-maintenance verifier/tests, docs wording | Continue deterministic docs/test/verifier work while ADB is unavailable, without promoting device-gated runtime/terminal/GPU/SAF claims | Must keep historical evidence separate from current promotion evidence |

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
| Llama GPU next-step audit | Anscombe | read-only | Historical audit entry only; current classifier and next action live in Active Lanes and `docs/plan/LLAMA_GPU_BRIDGE_NEXT_STEPS.md`. |
| Script/doc drift guard | Main agent | `f053df0` | Pushed guard for script/doc maintenance drift; continue using docs-maintenance and script-inventory checks before broad cleanup |
| Runtime teardown / terminal evidence gates | Main agent | `2ce8396` | CI Showcase succeeded; both gates remain non-promoting until fresh device evidence lands |
| No-PRoot runtime truth gate | Main agent/Herschel | `e0612a3` then ledger sync `753670c` | Gate and planned-gap artifact pushed; promotion still requires no-PRoot executor evidence or explicit capability-error artifact |
| Llama Q6 safe-kernel/native layout diagnostics | Main agent | `5dc330a` / `e1a806d` / `3b8eecb` / `be93398` plus 2026-05-19 artifacts | Historical landed-slice bookkeeping for the safe-kernel, native Q6_K probe, and sweep refresh commits. Current classifier and next action live in Active Lanes and `docs/plan/LLAMA_GPU_BRIDGE_NEXT_STEPS.md`. |
| Design/TODO/API reconciliation | Bernoulli/Tesla/Hubble/Linnaeus/Russell | `eefae1d` | Docker API inventory, Skydnir extension boundary, unsupported OCI scope, media Bluetooth/BLE/GPS planning, and design/TODO gaps landed. Q6 sweep now preserves native probe details. | Keep API extension fields diagnostic-only; unsupported OCI/runtime gaps must stay visible |
| Stale evidence wording audit | Euclid | `3b151a6` plus static guard follow-up | Historical 2026-05-05 smoke evidence no longer reads as current terminal/service-truth/teardown/image-pull promotion. Static docs guard now rejects the same class of contradiction. | Do not regress release/showcase copy into current-promotion language without new artifacts |
| Source marker audit under ADB-off | Hubble/Confucius | read-only, recorded in `ADB_OFF_TASK_QUEUE_20260520.md` | App UI source had no uncovered actionable TODO/FIXME/HACK markers. Native/runtime findings were covered or low-risk: disabled merged-usr legacy body, temp context extraction, storage wrapper temp file, GPU scratch buffer naming. | Treat as backlog hygiene; do not change runtime semantics without focused host tests |
| Extension/API route audit | Halley/Tesla/Popper | static/docs follow-up | Halley found all documented Engine inventory routes implemented, plus broad base-route method handling and a generic `POST /networks/{name}` fallback to review later. Tesla found `/system/df` and `/system/prune` need standard-Docker exceptions, and `Pdocker*` fields need documentation/static guard. Popper proposed the next ADB-off queue. | Runtime route behavior changes need focused host tests first; docs/static guard is safe while ADB is off |
| Network route fail-closed audit | Boyle | `compat-audit.py`, pdockerd network route, ADB-off queue | Boyle confirmed Compose/Docker should use explicit network inspect/create/connect/disconnect routes and does not need a generic `POST /networks/{name}` success. Unsupported network POST/subroutes now return JSON 404 and are covered by host protocol smoke. | Keep positive Compose network paths covered before any further route tightening |
| Test evidence index | Main agent | `docs/test/EVIDENCE_INDEX.md`, `docs/test/README.md`, ADB-off queue | Added a host-only index that classifies `latest` artifacts and points each high-churn family at its canonical gate/runbook owner. | Keep this as an index; do not duplicate gate status text that belongs in TODO/CI ledger/gate docs |
| Script inventory duplicate drift audit | Kant/Faraday/Main agent | `scripts/verify-script-inventory.py`, `tests/test_script_inventory_audit.py`, ADB-off queue | Kant recommended a host-only script surface guard; Faraday confirmed four wrappers are already migrated and `smoke-vulkan-icd-bridge.sh` was the next safe migration candidate. The first landed follow-up is a duplicate-candidate guard so copied implementations cannot silently diverge from inventory state. | Current slice migrates the Vulkan ICD smoke implementation behind `scripts/test/`; next script-cleanup slice is wrapper retirement only after the compatibility window |
| Documentation discoverability audit | Ptolemy | read-only, ADB-off queue | Ptolemy found two safe docs-only follow-ups: root `README.md` should link every category README listed by `docs/README.md`, and `docs/test/README.md` should index maintained test docs currently omitted from the category landing page. | Queue as ADBOFF-015/016; keep entries link-only and avoid copying status claims |
| ADB-off synthetic fixture and cleanup audit | Ampere/Kierkegaard/Main agent | `tests/test_native_payload_verifier_synthetic.py`, `scripts/verify-fast.sh`, ADB-off queue | Ampere recommended adding the synthetic native-payload verifier to `verify-fast` and filling any remaining `latest` evidence discoverability gaps. Kierkegaard confirmed script/docs verifiers are green, recommended local pycache cleanup, and identified future low-risk docs/script cleanup candidates. The landed follow-up is a small synthetic APK fixture that tests required asset presence, source-byte freshness, and forbidden `__pycache__` packaging without requiring a built APK. | Evidence-index coverage should keep improving, but do not convert historical or device-gated artifacts into promoting claims without fresh ADB evidence |
| Test evidence docs cleanup | Dewey/Darwin/Main agent | `docs/test/README.md`, `docs/maintenance/*`, ADB-off queue | Dewey moved raw script paths out of the test README document table into a related-script section and added the OpenCL smoke peer. Darwin confirmed stale “add evidence index” wording should point at the existing shared evidence index instead. | Keep docs maintenance link-only; future path moves need producer/verifier changes in the same commit |
| Latest evidence ownership guard | Newton/Main agent | `docs/test/EVIDENCE_INDEX.md`, docs-maintenance verifier/tests, ADB-off queue | Newton recommended a host-only guard for committed `docs/test/*latest*` pointers. The landed follow-up makes docs maintenance fail when a top-level latest artifact is not referenced by the evidence index, test README, CI gate ledger, or registered scenario/test manifests. | Keep the owner corpus explicit; add topic-specific indexes before artifact path moves |
| Script maintenance triage sync | Maxwell/Main agent | `scripts/verify-script-inventory.py`, `tests/test_script_inventory_audit.py`, ADB-off queue | Maxwell recommended guarding `docs/maintenance/SCRIPT_DOC_INVENTORY.md` against script inventory count/name drift. The landed follow-up checks category-count phrases and obsolete-suspect names against the inventory source of truth. | Keep the guard read-only; do not auto-generate triage prose |
| Llama startup obsolete helper retirement | Pasteur/Main agent | `tests/test_llama_startup_logging_contract.py`, script inventory/docs, ADB-off queue | Pasteur confirmed the old ad-hoc llama startup helper was host-only and safe to retire once its early-tee/startup-json assertions moved into maintained unittest coverage. The landed follow-up deletes the script and updates script inventory/docs for that retirement; the later box64 wrapper retirement leaves only one obsolete suspect, the terminal repro script. | Keep the replacement test in `verify-fast`; no runtime/device promotion is implied |
| Obsolete script retirement audit | Feynman/Parfit/Main agent | script inventory/docs, ADB-off queue | Parfit found `scripts/wrap-ndk-box64.sh` safe to delete because the supported native path is `scripts/build-native-android-ndk.sh` plus native ABI tests. Feynman found `scripts/android-terminal-it-repro.sh` should remain until paired real-device terminal artifacts replace it. | Only one obsolete suspect remains; do not delete terminal repro without fresh ADB evidence |
| Nested latest evidence ownership | Hooke/Fermat/Main agent | `scripts/verify-docs-maintenance.py`, `tests/test_docs_maintenance.py`, `docs/test/EVIDENCE_INDEX.md`, ADB-off docs | Hooke found that top-level latest evidence was guarded but nested latest-artifact directories could escape ownership checks; Fermat found the ADB-off queue wording still looked active after ADBOFF-001 through ADBOFF-023 landed. The landed follow-up scans latest paths recursively, documents nested llama artifact directories, and reframes the queue as a completed maintenance ledger. | Host-only only; nested artifacts are discoverability evidence, not device promotion |
| Obsolete-suspect audit guard | Kuhn/Main agent | `scripts/verify-script-inventory.py`, `tests/test_script_inventory_audit.py`, ADB-off queue | Kuhn promoted the remaining obsolete-suspect retention rule into the inventory verifier: obsolete candidates now need dated reference-scan evidence, an actionable delete/archive/retire decision, and either a maintained replacement or explicit retirement condition. | Keeps `scripts/android-terminal-it-repro.sh` retained but prevents its ADB-gated reason from silently decaying |
| Exact evidence/ledger drift guards | Chandrasekhar/Hypatia/Main agent | `scripts/verify-docs-maintenance.py`, `tests/test_docs_maintenance.py`, ADB-off queue, script inventory | Hypatia found no current latest artifact was accidentally owned, but the recursive guard still used substring matching; Chandrasekhar found stale obsolete-suspect count prose and manually synchronized ADBOFF completion ranges. The landed follow-up makes latest ownership exact-token based, checks ADBOFF range prose, and rejects obsolete-suspect count wording that disagrees with the inventory. | Device gates remain blocked; these are context-loss and docs-drift guards |
| Release readiness CI/evidence drift guards | Russell/Bernoulli/Main agent | `83e775e` / `199c394`, ADB-off queue | Release-readiness workflow path filters now cover release docs/notices, metadata, verifier/workflow edits, and native/staged payload inputs; docs maintenance now fails committed latest evidence that is only manifest-owned, and the native release hygiene index includes the CMake build log pointer. | Host-only CI/docs guard only; does not promote F-Droid/source-built payload readiness or device runtime evidence |
| Fast gate CI-ledger parity pass | James/Curie/Plato/Main agent | `scripts/verify-fast.sh`, ADB-off queue | James found several CI-ledger fast/static claims were not explicit in `verify-fast`; Curie confirmed adjacent host-only candidates; Plato then identified exact missing ledger commands. The landed follow-up wires `scripts/verify-memory-pager-contract.py`, OOM/LMK survival, media bridge readiness, no-PRoot runtime-truth, COW overlay kill-at-step, llama GPU memory-blocker classification, and exact pager/service contract unit tests into the fast gate without promoting their device lanes. | Host-only static/synthetic coverage; no ADB/device success claim |
| Terminal/docs static cleanup pass | Banach/Ramanujan/Main agent | `scripts/verify_terminal_editor_contracts.py`, root/docs indexes, ADB-off queue | Banach confirmed the generic xterm surface must remain session-neutral; the landed follow-up mirrors that focused unittest into the lightweight terminal/editor script. Ramanujan found Showcase wording drift in hand-maintained indexes; root `README.md` and `docs/README.md` now include curated news without touching generated Showcase output. | Host-only static/docs normalization only |
| CI ledger parity guard | Locke/Main agent | `tests/test_ci_gate_ledger.py`, ADB-off queue | Locke proposed a small guard so future CI ledger fast/static commands cannot drift away from `scripts/verify-fast.sh`. The landed test parses the gate table, extracts backticked fast/static script and unittest commands, and requires their paths/modules to appear in the fast gate or in a reasoned exemption map. | Host-only guard; path/module representation only, not device promotion |
| Llama Q6_K workflow argv guard | Poincare/Main agent | `tests/test_llama_gpu_q6k_workflow.py`, `scripts/verify-fast.sh`, ADB-off queue | Poincare found the Q6_K workflow compare path needed an exact argv guard before the next device artifact run. The landed follow-up exercises the readiness-green path with faked host-only steps and fails if the compare wrapper regresses to duplicate shell argv such as `bash bash scripts/android-llama-gpu-compare.sh`. | Host-only workflow guard; no Dockerfile/model/prompt change and no GPU correctness promotion |
| Recovered fast-gate contract wiring | Nietzsche/Planck/Main agent | `scripts/verify-fast.sh`, `docs/test/CI_GATE_LEDGER.md`, ADB-off queue | Nietzsche identified several documented host-only contracts missing from the explicit fast gate; Planck reduced the list to non-duplicated low-risk additions. The landed follow-up wires memory-layer UI, COW kill-at-step contract, GPU ABI, and COW runner shell syntax checks into `verify-fast`, while keeping all device-promotion lanes non-promoting. | Adds about 20 seconds of host-only coverage; no ADB/device success claim |

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

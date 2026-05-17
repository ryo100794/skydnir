# Agent Coordination Ledger

Last updated: 2026-05-04

This ledger records active delegation lanes and integration risks for the main
agent. The main agent owns waiting on agents, closing lanes, and integrating
final changes. The coordinator only records current expectations and flags
risks; it does not edit implementation files or resolve sibling work.

## Active Lanes

| Lane | Owner | Write scope | Expected deliverable | Integration risks |
| --- | --- | --- | --- | --- |
| Llama GPU bridge implementation | Main agent | `docker-proot-setup/src/gpu/`, `app/src/main/cpp/pdocker_gpu_executor.c`, GPU smoke/benchmark artifacts | Working llama GPU bridge plus final integrated repo state | Must keep llama.cpp unmodified and avoid broad docs/script churn while GPU ABI is moving |

## Recently Recovered Agent Results

| Result | Owner | Landed as | Follow-up |
| --- | --- | --- | --- |
| Build environment consolidation | Boyle | `scripts/build-all.sh`, `PDOCKER_SKIP_NATIVE_BUILD`, build docs | Treat as local convenience, not the full release/F-Droid process |
| TODO lane decomposition | Sartre | Used to assign non-overlapping work | Keep GPU implementation local; delegate low-conflict test/docs lanes |
| Direct syscall coverage lane | Banach | `scripts/run_direct_syscall_scenarios.py --lane local`, `tests/direct_syscall/`, `docs/test/DIRECT_SYSCALL_COVERAGE.md` | Included in `scripts/verify-fast.sh` |
| Storage metrics validation lane | Leibniz | `scripts/verify-storage-metrics.py`, `docs/test/STORAGE_METRICS.md` | Included in `scripts/verify-fast.sh`; device metric checks remain TODO |
| Terminal / `-it` investigation | Hypatia | Root cause captured in TODO: direct executor argv rewrite and readonly selection IME | Implement after GPU-safe checkpoint; add direct `/usr/bin/[` smoke |
| F-Droid/reproducible-build readiness | Carver | `docs/release/FDROID_RELEASE_PROCESS.md`, `metadata/fdroid/README.md` | Keep runtime container downloads documented as user-directed product behavior |

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

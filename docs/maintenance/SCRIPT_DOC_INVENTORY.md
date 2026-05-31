# Script and Documentation Inventory Triage

Snapshot date: 2026-05-19.
Status: low-risk triage ledger only; no scripts, docs, app code, tests, or native/GPU code are moved by this document.

This note connects the stable script inventory in [`../../scripts/README.md`](../../scripts/README.md) with the documentation cleanup backlog in [`DOCUMENTATION_DEDUP_BACKLOG.md`](DOCUMENTATION_DEDUP_BACKLOG.md). It is intentionally small and records where the flat script layout and fragmented planning/test docs need follow-up.

## Script categories and next actions

| Category | Current inventory | Triage notes | Next actions |
|---|---:|---|---|
| Runtime packaging | 1 top-level script | `scripts/copy-native.sh` is the only runtime/package staging entrypoint. Keep it stable because packaging callers may depend on the exact path. | Before any move, add a wrapper-backed implementation under `scripts/runtime/`, update Gradle/package callers in the same change, and rerun `python3 scripts/verify-script-inventory.py`. |
| Build | 8 top-level scripts | Build and setup helpers are flat but understandable. Public entrypoints are already named in `scripts/README.md`. The standard packaged native helper path is `scripts/build-native-android-ndk.sh`; the legacy Termux native-build entrypoint has been retired. | Keep `build-all.sh`, `build-apk.sh`, `build-gpu-shim.sh`, and `build-native-android-ndk.sh` stable. Move only helper implementations after docs and CI references point at wrappers or new paths. |
| Test | 88 top-level scripts | Most flat files are verification gates, smoke scripts, benchmark runners, artifact validators, or GPU/llama diagnostic planners. The native payload verifier and native rebuild release verifier are explicit release-hygiene gates, including the F-Droid no-crane dry-run path. | Prefer manifest-backed lanes through `scripts/skydnir-test-driver.py`. Add new verifiers to `scripts/script-inventory.json` first, then decide whether they remain top-level wrappers or move under `scripts/test/`. |
| Device diagnostics | subset of test scripts | Android, GPU, llama, storage, memory, service-truth, self-debug, and terminal repro helpers are mixed at top level. Several are ad-hoc device evidence producers rather than stable public commands. | Introduce shared Android/ADB helper libraries before moving callers. Migrate small, single-purpose device helpers first; keep broad smoke/compare wrappers stable until runbooks and evidence producers are updated. |
| Generated maintenance | 3 entries plus generated outputs | Showcase and llama/GPU artifact summarizers are maintenance producers. Generated/cached outputs such as Python `__pycache__` are not manual source files. | Keep generated outputs out of durable docs. If cache files appear in the worktree, treat them as cleanup candidates only after confirming they are untracked and not referenced by tooling. |
| Unused or legacy candidates | 1 tracked candidate | Current candidates remain `android-terminal-it-repro.sh`. It is weakly referenced but not safe to delete in a broad cleanup pass. The retired llama startup helper is now covered by `tests/test_llama_startup_logging_contract.py`; the retired box64 NDK wrapper is replaced by `scripts/build-native-android-ndk.sh` plus native-build ABI tests. | Audit each candidate in a focused change. Delete or archive only after replacement commands, docs, and test coverage are confirmed. |

## Duplicate or flat-script observations

- Stable top-level names are intentional compatibility surfaces; do not move them directly.
- Wrapper migrations already exist for the OpenCL/Vulkan smoke helpers, the device llama template helper, and the llama GPU artifact summarizer.
- The main duplication risk is not identical code; it is repeated one-off Android/GPU/device command setup across many small scripts. A shared helper should come before path reshuffling.
- Inventory drift should be fixed in `scripts/script-inventory.json` and reflected in `scripts/README.md` before any script rename, move, or deletion.
- As of `ed7cddd`, `scripts/verify/runner/*` is classified through
  `subtree_entries`; `__pycache__` bytecode remains ignored and outside the
  durable script inventory. The Vulkan ICD smoke wrapper/layout migration is
  complete; the remaining directory-cleanup follow-up is later
  wrapper-retirement automation after the compatibility window.
- Subdirectory READMEs now document non-top-level script pockets without
  changing the stable script surface:
  [`../../scripts/git-hooks/README.md`](../../scripts/git-hooks/README.md),
  [`../../scripts/maintenance/README.md`](../../scripts/maintenance/README.md),
  [`../../scripts/test/README.md`](../../scripts/test/README.md),
  [`../../scripts/verify/README.md`](../../scripts/verify/README.md), and
  [`../../scripts/verify/runner/README.md`](../../scripts/verify/runner/README.md).

## Fragmented docs triage

| Area | Current owner | Fragmentation risk | Next action |
|---|---|---|---|
| Planning and status | [`../plan/STATUS.md`](../plan/STATUS.md), [`../plan/TODO.md`](../plan/TODO.md), and [`../plan/AGENT_COORDINATION.md`](../plan/AGENT_COORDINATION.md) | Historical timeline, queue, and replan files can look active if copied forward. | Keep live state only in status/TODO/agent coordination; update older plan docs only for link fixes or explicit historical notes. |
| Test runbooks and evidence | [`../test/README.md`](../test/README.md), [`../test/EVIDENCE_INDEX.md`](../test/EVIDENCE_INDEX.md), and topic gate docs | `*-latest.*`, large llama/GPU artifacts, and run directories are easy to mistake for duplicates. | Keep the shared evidence index current; add topic-specific indexes only before pruning or moving artifacts, and preserve producer/consumer contracts for latest pointers. |
| GPU and llama work | [`../design/GPU_COMPAT.md`](../design/GPU_COMPAT.md), [`../plan/LLAMA_GPU_BRIDGE_NEXT_STEPS.md`](../plan/LLAMA_GPU_BRIDGE_NEXT_STEPS.md), and [`../test/LLAMA_BENCHMARKS.md`](../test/LLAMA_BENCHMARKS.md) | Design rationale, active plans, and benchmark evidence overlap. | Keep design rules in design docs, active next steps in plan docs, and measured evidence in test docs. Link rather than copy summaries. |
| Runtime and direct execution | [`../design/RUNTIME_STRATEGY.md`](../design/RUNTIME_STRATEGY.md) and direct syscall test docs | Runtime direction, API29 feasibility, and device gates can duplicate command blocks. | Keep command invocations in build/test owners and link from design or plan docs. |

## Immediate follow-up checklist

1. Keep `scripts/script-inventory.json`, `scripts/README.md`, subdirectory
   READMEs, and this triage note in sync when adding or classifying scripts.
2. Run `python3 scripts/verify-script-inventory.py` after script inventory edits.
3. Run `python3 scripts/verify-docs-maintenance.py` after adding durable documentation.
4. Do not delete weakly referenced scripts or test evidence without a focused audit, replacement command, and link/producer check.

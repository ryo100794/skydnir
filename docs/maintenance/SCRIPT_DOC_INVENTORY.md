# Script and Documentation Inventory Triage

Snapshot date: 2026-05-19.
Status: low-risk triage ledger only; no scripts, docs, app code, tests, or native/GPU code are moved by this document.

This note connects the stable script inventory in [`../../scripts/README.md`](../../scripts/README.md) with the documentation cleanup backlog in [`DOCUMENTATION_DEDUP_BACKLOG.md`](DOCUMENTATION_DEDUP_BACKLOG.md). It is intentionally small and records where the flat script layout and fragmented planning/test docs need follow-up. Development artifact cleanup decisions are recorded here instead of creating another one-off cleanup note.

## Script categories and next actions

| Category | Current inventory | Triage notes | Next actions |
|---|---:|---|---|
| Runtime packaging | 1 top-level script | `scripts/copy-native.sh` is the only runtime/package staging entrypoint. Keep it stable because packaging callers may depend on the exact path. | Before any move, add a wrapper-backed implementation under `scripts/runtime/`, update Gradle/package callers in the same change, and rerun `python3 scripts/verify-script-inventory.py`. |
| Build | 8 top-level scripts | Build and setup helpers are flat but understandable. Public entrypoints are already named in `scripts/README.md`. The standard packaged native helper path is `scripts/build-native-android-ndk.sh`; the legacy Termux native-build entrypoint has been retired. | Keep `build-all.sh`, `build-apk.sh`, `build-gpu-shim.sh`, and `build-native-android-ndk.sh` stable. Move only helper implementations after docs and CI references point at wrappers or new paths. |
| Test | 91 top-level scripts | Most flat files are verification gates, smoke scripts, benchmark runners, artifact validators, or GPU/llama diagnostic planners. The native payload verifier and native rebuild release verifier are explicit release-hygiene gates, including the F-Droid no-crane dry-run path. | Prefer manifest-backed lanes through `scripts/skydnir-test-driver.py`. Add new verifiers to `scripts/script-inventory.json` first, then decide whether they remain top-level wrappers or move under `scripts/test/`. |
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

## 2026-07-20 development-artifact cleanup pass

Scope: repository-local developer artifacts, redundant scratch files, and
generated work files. This pass intentionally did not touch active Vulkan bridge
source changes, staged APK/runtime payloads, tracked evidence, or machine-local
configuration. The cleanup check was re-run after commit `bf9c7fa8` with the
guarded cleanup helper, `git clean -nd`, `git clean -ndX`, size scans, docs
maintenance verification, native-payload verification, and script-inventory
verification.

### Current classification

| Class | Paths | Action |
|---|---|---|
| Disposable ignored scratch | `__pycache__/`, `*.pyc`, `.pytest_cache/`, `.gradle/`, `build/`, `app/.cxx/`, `app/.externalNativeBuild/`, `captures/`, `tmp/`, ignored generated `docs/test/llama-gpu-*` and `docs/test/llama-cpu-gpu-compare-*` files | Safe cleanup target when untracked. The latest guarded cleanup removed only untracked Python bytecode cache directories and the post-cleanup dry run found no remaining disposable candidates. |
| Ignored but build/runtime-staged | `app/src/main/assets/pdockerd/`, `app/src/main/assets/xterm/xterm*.{js,css}`, `app/src/main/jniLibs/*/libcow.so`, `libcrane.so`, `libpdockerpty.so`, `local.properties` | Keep. `git clean -ndX` currently lists only these staged payloads and machine config. They are ignored because they are local/staged payloads or machine config, not because they are always disposable. Deleting them can break the next APK build until restaged. |
| Tracked evidence | `docs/test/*-latest.*`, `docs/test/runs/**`, `docs/test/device-logs/**`, `docs/test/spirv-q6k-*`, llama GPU comparison artifacts | Keep until a focused evidence-pruning policy exists. These files are generated-looking but are tracked regression/evidence records. |
| Tracked large development payloads | `vendor/lib/docker-compose`, `docker-proot-setup/docker-bin/docker`, `docker-proot-setup/docker-bin/crane` | Review separately. They are development/test compatibility payloads or staging sources and must not be removed in a broad cleanup pass. |
| Active generated native outputs | `app/src/main/jniLibs/*/libpdockervulkanicd.so`, `docker-proot-setup/lib/pdocker-vulkan-icd.so` | Keep with the matching source change until the Vulkan bridge commit is validated. |

### 2026-07-20 follow-up cleanup result

- Initial `scripts/maintenance/clean-development-artifacts.py --json` reported
  three guarded disposable candidates:
  `scripts/__pycache__/`, `scripts/maintenance/__pycache__/`, and
  `tests/__pycache__/`.
- `scripts/maintenance/clean-development-artifacts.py --apply --json` removed
  those three untracked Python bytecode cache directories.
- The post-cleanup dry run reported `mode: dry-run` and `count: 0`; there are
  no remaining guarded disposable candidates.
- `git clean -nd` reported no untracked non-ignored repository files.
- `git clean -ndX` reported only ignored staged payloads and machine-local
  configuration: `app/src/main/assets/pdockerd/`, `app/src/main/assets/xterm/`,
  selected staged native `.so` files, and `local.properties`.
- After cleanup, no repository-local `__pycache__/`, `*.pyc`,
  `.pytest_cache/`, `captures/`, Gradle build output, or CMake build output
  remains as a guarded cleanup candidate.
- `/root/tl` contains unrelated root-workspace files outside this repository;
  they are not part of this cleanup policy and must not be removed by a
  repository cleanup pass.

### 2026-07-20 local Git metadata cleanup

- A second guarded dry run found only two regenerated Python bytecode cache
  directories. They were removed, and the post-cleanup dry run reported zero
  candidates.
- `git worktree prune` removed one stale worktree record whose target directory
  no longer existed. The active `rename/skydnir` worktree was unchanged.
- `docker-proot-setup/` is tracked as ordinary repository content, but the local
  Git database still contained obsolete submodule configuration and metadata.
  The obsolete configuration included a credential-bearing URL; the local
  configuration entries were removed without recording the credential value.
  Any credential that may have been exposed through local configuration must be
  revoked or rotated outside this repository.
- Before deleting the obsolete submodule object database, commits `8c5ea17` and
  `d1906d3` were audited. Their executable changes are present in the integrated
  `docker-proot-setup/bin/pdockerd`; the former also contained a stale
  `CLAUDE.md` note that is not part of the integrated documentation tree. Both
  patches were preserved locally as a two-message, credential-scanned mbox at
  `.git/legacy-patches/docker-proot-setup-20260501.mbox`, then the approximately
  24 MiB obsolete `.git/modules/docker-proot-setup/` database was removed.
- Tracked evidence, staged APK payloads, active Vulkan outputs, compatibility
  wrappers, and large Docker/Compose test payloads were retained. Their removal
  requires a focused consumer and retention-policy change rather than a broad
  artifact cleanup.

### 2026-07-21 redundant development payload cleanup

- Removed the tracked `vendor/lib/docker` stripped binary. It duplicated the
  same upstream Docker CLI build already retained at
  `docker-proot-setup/docker-bin/docker` (matching Go and ELF build IDs), while
  all active test callers use the latter path.
- Kept `vendor/lib/docker-compose`; it is the active test-only Compose plugin
  paired with the retained Docker CLI and has no duplicate replacement.
- Kept ignored APK staging payloads, tracked regression evidence, and all active
  Vulkan bridge source and native outputs.
- The guarded cleanup apply pass found no untracked caches or scratch files to
  remove.

### Size notes

- Repository size after the current cleanup recheck: approximately `346M`.
- Git object store after local metadata cleanup: approximately `163M`. `git gc` was not
  run as part of this cleanup because object pruning is a separate repository
  maintenance action.
- Tracked test evidence: `docs/test` is approximately `40M`.
- Other size anchors: `vendor` approximately `60M`, `docker-proot-setup`
  approximately `52M`, `app` approximately `17M`, `tests` approximately
  `3.6M`, and `scripts` approximately `2.6M`.
- Large tracked files remaining by design:
  - `vendor/lib/docker-compose` - approximately `59.3 MiB`.
  - `docker-proot-setup/docker-bin/docker` - approximately `38.1 MiB`.
  - `docker-proot-setup/docker-bin/crane` and staged `libcrane.so` - approximately `9.9 MiB` each.

### Follow-up cleanup rules

1. Do not run blanket `git clean -Xdf` in this repository. It would remove
   ignored payloads that are still needed for local APK staging. Use
   `python3 scripts/maintenance/clean-development-artifacts.py` first; add
   `--apply` only after reviewing the dry-run output.
2. Prefer targeted deletion of ignored scratch directories only:
   `__pycache__`, `.pytest_cache`, Gradle/CMake build directories, local
   captures, and ignored ad-hoc evidence files.
3. Treat tracked `docs/test` artifacts as evidence, not trash. Pruning requires
   an evidence-retention change that updates producers, consumers, and indexes
   in the same commit.
4. Treat root-level script moves as compatibility changes. Move implementation
   files only behind stable wrapper shims and update `scripts/script-inventory.json`
   before changing paths.

### Maintenance helper

- `scripts/maintenance/clean-development-artifacts.py` provides a guarded
  dry-run/apply path for repository-local caches and scratch files. It never
  removes ignored APK/runtime staging payloads or `local.properties`; ignored
  generated evidence is opt-in via `--include-ignored-evidence`.

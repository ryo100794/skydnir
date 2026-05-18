# Documentation Deduplication Backlog

Snapshot date: 2026-05-18.
Status: active inventory; no content moves or deletions in this pass.

This backlog supports conservative documentation cleanup while scripts and
runtime work continue in parallel. It records where related material is
scattered, which file should remain canonical, and what safe follow-up work is
left. Prefer cross-links and category README updates before renames or deletion.

## Target category tree

Existing documents are gradually migrated and classified into this tree. Until a
file moves, keep its current path stable and add category-owner links first.

| Category | Canonical index | Ownership boundary |
|---|---|---|
| Manual | [`../manual/README.md`](../manual/README.md) | User/operator workflows and public-maintainer procedures |
| Design | [`../design/README.md`](../design/README.md) | Architecture, compatibility boundaries, feasibility decisions, and non-goals |
| Build | [`../build/README.md`](../build/README.md) | Local setup, APK packaging, signing, install commands, and build gates |
| Test | [`../test/README.md`](../test/README.md) | Repeatable procedures, compatibility audits, device gates, and recorded evidence |
| Plan | [`../plan/README.md`](../plan/README.md) | Current status, active TODOs, coordination ledgers, and historical steering snapshots |
| Release | [`../release/README.md`](../release/README.md) | Release readiness, fixed build evidence, distribution process, and announcements |
| Maintenance | [`README.md`](README.md) | Documentation inventory, deduplication backlog, and safe cleanup sequencing |
| License/notice | root [`../../LICENSE`](../../LICENSE), [`../../THIRD_PARTY_NOTICES.md`](../../THIRD_PARTY_NOTICES.md), [`../../SECURITY.md`](../../SECURITY.md), [`../../CONTRIBUTING.md`](../../CONTRIBUTING.md) | Repository-level compliance, notices, and contributor policy; no duplicate docs source of truth |
| Showcase | [`../showcase/README.md`](../showcase/README.md) | Generated or curated GitHub-facing dashboard, roadmap, news, and Wiki seed pages |

## Completion-state audit model

Use these states when auditing a document, directory, or evidence cluster:

| State | Meaning |
|---|---|
| `canonical/active` | Current source of truth for maintained content |
| `artifact/evidence` | Immutable or producer-owned test, build, or release evidence |
| `historical` | Retained timeline, decision, or release history |
| `planned-gap` | Known missing document or index planned for later |
| `duplicate-to-merge` | Overlapping prose that should collapse into links plus a short summary |
| `generated/cache-excluded` | Generated output, cache, or pointer excluded from manual edits and normal prose deduplication |

## Duplicate and scatter hotspots

### 1. Release, publication, and announcement facts

Canonical owners:

- Release posture and blockers: [`../release/RELEASE_READINESS.md`](../release/RELEASE_READINESS.md)
- Fixed build evidence: [`../release/builds/20260505.1/README.md`](../release/builds/20260505.1/README.md)
- Distribution process: [`../release/FDROID_RELEASE_PROCESS.md`](../release/FDROID_RELEASE_PROCESS.md)
- Public workflow: [`../manual/NEWSFLOW.md`](../manual/NEWSFLOW.md)
- Reusable public-message template: [`../manual/PROMOTION.md`](../manual/PROMOTION.md)

Scattered or repeated in:

- [`../release/RELEASE_NOTES_20260505.1.md`](../release/RELEASE_NOTES_20260505.1.md)
- [`../release/announcements/20260505.md`](../release/announcements/20260505.md)
- [`../showcase/PROJECT_DASHBOARD.md`](../showcase/PROJECT_DASHBOARD.md)
- [`../showcase/NEWS_TIMELINE.md`](../showcase/NEWS_TIMELINE.md)
- Root [`../../README.md`](../../README.md)

Backlog:

- Convert repeated build `20260505.1` facts into links to fixed build evidence.
- Keep release notes immutable; update release-readiness/current blocker text only
  in the release readiness file.
- Regenerate showcase pages instead of editing generated sections by hand.

### 2. Llama / GPU bridge material

Canonical owners:

- GPU design boundary: [`../design/GPU_COMPAT.md`](../design/GPU_COMPAT.md)
- Active bridge plan: [`../plan/LLAMA_GPU_BRIDGE_NEXT_STEPS.md`](../plan/LLAMA_GPU_BRIDGE_NEXT_STEPS.md)
- Benchmark history: [`../test/LLAMA_BENCHMARKS.md`](../test/LLAMA_BENCHMARKS.md)
- Device procedure: [`../test/LLAMA_GPU_DEVICE_RUNBOOK_20260513.md`](../test/LLAMA_GPU_DEVICE_RUNBOOK_20260513.md)

Scattered or repeated in:

- [`../test/LLAMA_GPU_CORRECTNESS_20260507.md`](../test/LLAMA_GPU_CORRECTNESS_20260507.md)
- [`../test/LLAMA_GPU_PERFORMANCE_20260507.md`](../test/LLAMA_GPU_PERFORMANCE_20260507.md)
- [`../test/llama-gpu-bridge-root-cause-20260509.md`](../test/llama-gpu-bridge-root-cause-20260509.md)
- `../test/llama-*.json`, `../test/gpu-*.json`, and `../test/device-logs/llama-*`
- [`../showcase/ROADMAP_TIMELINE.md`](../showcase/ROADMAP_TIMELINE.md)

Backlog:

- Add a llama/GPU evidence index before moving artifacts into subdirectories.
- Keep `*-latest.*` pointers stable until producers and verifier paths are
  updated together.

### 3. Memory, OOM, and APK pager material

Canonical owners:

- Pager design: [`../design/APK_MEMORY_PAGER.md`](../design/APK_MEMORY_PAGER.md)
- OOM policy: [`../design/RUNTIME_OOM_SURVIVAL.md`](../design/RUNTIME_OOM_SURVIVAL.md)
- Native ownership rules: [`../design/MEMORY_OWNERSHIP.md`](../design/MEMORY_OWNERSHIP.md)
- Probe procedure: [`../test/APK_MEMORY_PAGER_PROBE.md`](../test/APK_MEMORY_PAGER_PROBE.md)
- Survival gate: [`../test/OOM_LMK_SURVIVAL_GATE.md`](../test/OOM_LMK_SURVIVAL_GATE.md)

Backlog:

- Replace repeated design summaries in test gates with links back to the design
  files.
- Keep live implementation TODOs in [`../plan/TODO.md`](../plan/TODO.md).

### 4. Storage, COW, SAF, UnixFS, and metadata material

Canonical owners:

- COW/overlay behavior: [`../design/COW_OVERLAY_STORAGE.md`](../design/COW_OVERLAY_STORAGE.md)
- Storage architecture: [`../design/STORAGE_LAYER_ARCHITECTURE.md`](../design/STORAGE_LAYER_ARCHITECTURE.md)
- SAF sidecar: [`../design/SAF_UNIXFS_METADATA_SIDECAR.md`](../design/SAF_UNIXFS_METADATA_SIDECAR.md)
- Metadata index: [`../design/PROJECT_METADATA_INDEX.md`](../design/PROJECT_METADATA_INDEX.md)
- Storage test gates: [`../test/COW_OVERLAY_BENCH_RECOVERY.md`](../test/COW_OVERLAY_BENCH_RECOVERY.md), [`../test/COW_OVERLAY_KILL_AT_STEP_DEVICE_GATE.md`](../test/COW_OVERLAY_KILL_AT_STEP_DEVICE_GATE.md), [`../test/SAF_DIRECT_OUTPUT_GATE.md`](../test/SAF_DIRECT_OUTPUT_GATE.md), [`../test/STORAGE_METRICS.md`](../test/STORAGE_METRICS.md)

Backlog:

- Introduce a storage evidence index before moving file I/O, path profile, and
  syscall profile artifacts.
- Keep storage design constraints out of generated benchmark result files.

### 5. Runtime, direct syscall, API29, and Android self-debug material

Canonical owners:

- Runtime direction: [`../design/RUNTIME_STRATEGY.md`](../design/RUNTIME_STRATEGY.md)
- API29 feasibility: [`../design/API29_DIRECT_EXEC_FEASIBILITY.md`](../design/API29_DIRECT_EXEC_FEASIBILITY.md)
- Direct syscall coverage: [`../test/DIRECT_SYSCALL_COVERAGE.md`](../test/DIRECT_SYSCALL_COVERAGE.md)
- Phase 2 coverage: [`../test/DIRECT_SYSCALL_PHASE2_COVERAGE.md`](../test/DIRECT_SYSCALL_PHASE2_COVERAGE.md)
- Self-debug procedure: [`../test/ANDROID_SELFDEBUG.md`](../test/ANDROID_SELFDEBUG.md)

Backlog:

- Keep build commands in [`../build/README.md`](../build/README.md); link from
  runtime/test docs instead of copying command blocks.
- Keep current product state in [`../plan/STATUS.md`](../plan/STATUS.md).

### 6. Terminal, exec/attach, and UI stream material

Canonical owners:

- Terminal architecture: [`../design/TERMINAL_STREAM_ARCHITECTURE.md`](../design/TERMINAL_STREAM_ARCHITECTURE.md)
- Terminal device gate: [`../test/TERMINAL_EXEC_IT_DEVICE_GATE.md`](../test/TERMINAL_EXEC_IT_DEVICE_GATE.md)
- Scenario ledger: [`../test/SCENARIOS.md`](../test/SCENARIOS.md)

Backlog:

- Ensure test gate files link to the architecture doc for stream-boundary rules.
- Avoid copying feature scenario rows into plan/status documents.

### 7. Test evidence and generated/latest artifacts

Canonical owners:

- Test category rules: [`../test/README.md`](../test/README.md)
- Compatibility matrix: [`../test/COMPATIBILITY.md`](../test/COMPATIBILITY.md)
- CI gate classification: [`../test/CI_GATE_LEDGER.md`](../test/CI_GATE_LEDGER.md)
- Test design quality bar: [`../test/TEST_DESIGN_STANDARD.md`](../test/TEST_DESIGN_STANDARD.md)

Exact duplicate payloads observed on 2026-05-18:

- [`../test/llama-bench-latest.json`](../test/llama-bench-latest.json) and [`../test/llama-bench-cpu-repeat3.json`](../test/llama-bench-cpu-repeat3.json)
- [`../test/test-run-latest.json`](../test/test-run-latest.json) and [`../test/runs/20260515T040154Z-90aa939-host-smoke/manifest.json`](../test/runs/20260515T040154Z-90aa939-host-smoke/manifest.json)
- Repeated host-smoke step logs under `../test/runs/*/` for stable host-only
  checks; these are immutable run evidence and should not be hand-deleted.

Backlog:

- Treat `*-latest.*` files as mutable pointers, not accidental duplicates.
- Do not prune evidence merely because payloads repeat: first ensure each
  artifact is indexed by the test README, gate/runbook, run manifest, or release
  record; otherwise retain it or classify the path as generated/cache-excluded.
- Before pruning repeated logs, confirm the test-driver retention policy and
  whether release evidence references a specific run directory.

### 8. GitHub, collaboration, agent coordination, and planning material

Canonical owners:

- Git workflow: [`../manual/GIT_COLLABORATION.md`](../manual/GIT_COLLABORATION.md)
- Publishing workflow: [`../manual/NEWSFLOW.md`](../manual/NEWSFLOW.md)
- Issue promotion: [`../plan/ISSUE_WORKFLOW.md`](../plan/ISSUE_WORKFLOW.md)
- Multi-agent ledger: [`../plan/AGENT_COORDINATION.md`](../plan/AGENT_COORDINATION.md)
- Current TODOs: [`../plan/TODO.md`](../plan/TODO.md)

Backlog:

- Keep historical timeline/queue files immutable except for link fixes.
- Mirror only issue-linked summaries into TODO/status; do not copy whole
  workflow sections between manual and plan documents.

## Safe cleanup sequence

1. Keep category README files complete and current.
2. Add topic indexes for large evidence clusters before moving files.
3. Update links and generated-page producers before any path changes.
4. Preserve `*-latest.*` producer/consumer contracts.
5. Collapse duplicated prose into short summaries plus links.
6. Delete only after link checks, producer checks, and release-reference checks
   confirm the file is no longer needed.

## Open backlog count

There are 8 active deduplication backlog groups in this inventory. None were
closed in this pass; this change only adds routing and canonical-owner records.

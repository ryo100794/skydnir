# Documentation Reorganization Plan

Date: 2026-05-17
Status: historical
Owner: Documentation maintenance backlog
Historical as of: 2026-05-20

Historical note: this file is retained as the 2026-05-17 cleanup planning
snapshot. Do not refresh it with live documentation routing, active ownership,
or current artifact status. Use
[`../maintenance/DOCUMENTATION_DEDUP_BACKLOG.md`](../maintenance/DOCUMENTATION_DEDUP_BACKLOG.md)
for current deduplication routing and canonical owners, and use category README
files for current indexes. Keep `*-latest.*` benchmark/artifact paths stable
until their producers and verifiers are updated in the same change.

This historical plan records the documentation cleanup findings captured on
2026-05-17 so the work would not get lost while implementation work continued.
The conservative rule remains useful background: move and index first, then
remove duplicated prose only after links and generated showcase files have been
checked.

## Current documentation families

The repository currently uses these top-level documentation families:

- `docs/manual/` — user and maintainer workflows.
- `docs/design/` — architecture, compatibility boundaries, and technical policy.
- `docs/build/` — build, packaging, signing, install, and release mechanics.
- `docs/test/` — test procedures, evidence, ledgers, and generated artifacts.
- `docs/plan/` — active status, TODO, timeline, coordination, and readiness.
- `docs/showcase/` — public-facing generated dashboard/wiki/news material.

License and notice material is intentionally rooted at repository top level:

- `LICENSE`
- `THIRD_PARTY_NOTICES.md`
- `SECURITY.md`
- `CONTRIBUTING.md`

Do not create a second `docs/license-notice/` source of truth unless the root
files remain canonical and the docs page is only an index.

## Duplication and scatter hotspots

### Release, publication, and announcement material

Scattered files:

- `docs/release/FDROID_RELEASE_PROCESS.md`
- `docs/release/RELEASE_READINESS.md`
- `docs/release/RELEASE_NOTES_20260505.1.md`
- `docs/release/builds/20260505.1/README.md`
- `docs/manual/NEWSFLOW.md`
- `docs/manual/PROMOTION.md`
- `docs/release/announcements/20260505.md`
- `docs/showcase/NEWS_TIMELINE.md`
- `docs/showcase/PROJECT_DASHBOARD.md`
- release/build links in root `README.md`

Repeated facts include build `20260505.1`, Android full smoke status, unsigned
release APK notes, test-density gate state, direct executor mismatch state, and
current llama GPU blockers.

### Llama / GPU material

- Design: `docs/design/GPU_COMPAT.md`
- Plan: `docs/plan/LLAMA_GPU_BRIDGE_NEXT_STEPS.md`, `docs/plan/TODO.md`
- Test/evidence: `docs/test/LLAMA_BENCHMARKS.md`,
  `docs/test/LLAMA_GPU_CORRECTNESS_20260507.md`,
  `docs/test/LLAMA_GPU_PERFORMANCE_20260507.md`,
  `docs/test/LLAMA_GPU_DEVICE_RUNBOOK_20260513.md`,
  `docs/test/llama-gpu-bridge-root-cause-20260509.md`,
  `docs/test/llama-*.json`, and `docs/test/device-logs/llama-*`.
- Public material: `docs/showcase/PROJECT_DASHBOARD.md`,
  `docs/showcase/ROADMAP_TIMELINE.md`, `docs/manual/PROMOTION.md`,
  `docs/manual/NEWSFLOW.md`.

### Memory / OOM / APK pager material

- `docs/design/APK_MEMORY_PAGER.md`
- `docs/design/RUNTIME_OOM_SURVIVAL.md`
- `docs/design/MEMORY_OWNERSHIP.md`
- `docs/test/APK_MEMORY_PAGER_PROBE.md`
- `docs/test/OOM_LMK_SURVIVAL_GATE.md`
- `docs/test/apk-memory-pager-*.json`
- `docs/test/oom-lmk-survival-latest.json`
- `docs/plan/TODO.md`

### Storage / COW / SAF / UnixFS material

- `docs/design/COW_OVERLAY_STORAGE.md`
- `docs/design/SAF_UNIXFS_METADATA_SIDECAR.md`
- `docs/design/PROJECT_METADATA_INDEX.md`
- `docs/test/COW_OVERLAY_BENCH_RECOVERY.md`
- `docs/test/COW_OVERLAY_KILL_AT_STEP_DEVICE_GATE.md`
- `docs/test/SAF_DIRECT_OUTPUT_GATE.md`
- `docs/test/STORAGE_METRICS.md`
- `docs/test/file-io-*.md`
- `docs/test/path-micro-profile-*.md`
- `docs/test/syscall-usecase-profile-*.md`

### Runtime / direct syscall / API29 material

- `docs/design/RUNTIME_STRATEGY.md`
- `docs/design/API29_DIRECT_EXEC_FEASIBILITY.md`
- `docs/test/DIRECT_SYSCALL_COVERAGE.md`
- `docs/test/DIRECT_SYSCALL_PHASE2_COVERAGE.md`
- `docs/test/ANDROID_SELFDEBUG.md`
- `docs/build/README.md`
- `docs/plan/STATUS.md`

### GitHub / collaboration / agent operation material

- `docs/manual/GIT_COLLABORATION.md`
- `docs/manual/NEWSFLOW.md`
- `docs/plan/ISSUE_WORKFLOW.md`
- `docs/plan/AGENT_COORDINATION.md`
- `docs/plan/EXECUTION_TIMELINE_20260513.md`
- `docs/plan/GOAL_EXECUTION_QUEUE_20260513.md`

## Proposed target layout

### Release

Create `docs/release/`:

```text
docs/release/
  README.md
  FDROID_RELEASE_PROCESS.md
  RELEASE_READINESS.md
  RELEASE_NOTES_20260505.1.md
  builds/20260505.1/README.md
  announcements/20260505.md
```

Move release-specific material there, while keeping `docs/manual/NEWSFLOW.md` as
an operational workflow and `docs/manual/PROMOTION.md` as reusable messaging
patterns.

### Test evidence

Introduce category directories under `docs/test/`:

```text
docs/test/llama/
docs/test/runtime/
docs/test/storage/
docs/test/memory/
docs/test/terminal/
docs/test/artifacts/
docs/test/runs/
```

Examples:

- `docs/test/LLAMA_BENCHMARKS.md` -> `docs/test/llama/BENCHMARKS.md`
- `docs/test/LLAMA_GPU_DEVICE_RUNBOOK_20260513.md` ->
  `docs/test/llama/GPU_DEVICE_RUNBOOK_20260513.md`
- `docs/test/llama-*.json` -> `docs/test/llama/artifacts/`
- `docs/test/COW_OVERLAY_*.md` and `docs/test/SAF_DIRECT_OUTPUT_GATE.md` ->
  `docs/test/storage/`
- `docs/test/APK_MEMORY_PAGER_PROBE.md` and `docs/test/OOM_LMK_SURVIVAL_GATE.md`
  -> `docs/test/memory/`
- `docs/test/DIRECT_SYSCALL*.md` -> `docs/test/runtime/`
- `docs/test/TERMINAL_EXEC_IT_DEVICE_GATE.md` -> `docs/test/terminal/`

### Design

Introduce category directories under `docs/design/`:

```text
docs/design/compat/
docs/design/runtime/
docs/design/storage/
docs/design/gpu/
docs/design/memory/
docs/design/ui-terminal/
```

Move by topic after README/index files exist and link checks are ready.

## Exact duplicate handling

No fully identical Markdown files were found.  Exact duplicate generated/evidence
files were found and should be handled by policy rather than hand deletion:

- `docs/test/test-run-latest.json`
- `docs/test/runs/20260515T040154Z-90aa939-host-smoke/manifest.json`
- `docs/test/llama-bench-latest.json`
- `docs/test/llama-bench-cpu-repeat3.json`

Keep `*-latest.*` as explicit mutable pointers if scripts expect them.  If they
are moved, update the producers and verifier paths in the same commit.

## Safe work order

1. Decide canonical owners:
   - release readiness: `docs/release/RELEASE_READINESS.md`.
   - build evidence: `docs/release/builds/20260505.1/README.md`.
   - compatibility: `docs/test/COMPATIBILITY.md`.
   - Docker scope: `docs/design/DOCKER_COMPAT_SCOPE.md`.
   - TODO: `docs/plan/TODO.md`.
2. Add or update category README files before moving content.
3. Move files with history-preserving `git mv`.
4. Update root `README.md`, `docs/README.md`, category READMEs,
   `CONTRIBUTING.md`, and generated showcase inputs.
5. Run link checks / grep checks for old paths.
6. Only then collapse duplicated prose into cross-links.
7. Regenerate showcase files through their generator rather than hand-editing
   generated output.

## README and index files to update

- `README.md`
- `docs/README.md`
- `docs/build/README.md`
- `docs/plan/README.md`
- `docs/test/README.md`
- `docs/design/README.md`
- `docs/manual/README.md`
- `docs/showcase/README.md`
- `CONTRIBUTING.md`

## Next actions

- Add the new category README skeletons in a dedicated documentation-only commit.
- Move release documents first, because they have the highest prose duplication.
- Move llama test evidence second, but keep `docs/test/llama-gpu-compare-latest.json`
  and other `latest` artifacts stable until producers are updated.

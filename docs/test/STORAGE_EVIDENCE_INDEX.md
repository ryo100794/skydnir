# Storage Evidence Index

Snapshot date: 2026-05-20.

This topic index groups storage, COW/overlay, SAF, archive, and file-I/O
evidence before any future artifact moves or pruning. It is an index only: it
does not promote device-gated artifacts, rename `latest` pointers, or change
producer paths.

## Promotion Rules

- Host/local COW recovery and benchmark artifacts can close only host-side
  contracts.
- Android daemon/helper kill-at-step, SAF direct-output, storage sequence, and
  `docker cp` end-to-end claims require connected-device evidence accepted by
  their owning gate documents.
- `planned-gap`, `blocked-device`, fallback, mirror-only, skipped, or
  `success=false` artifacts are retained as diagnostics and never count as
  stable storage success.
- `*-latest.*` files are mutable pointers. Keep them stable until the producer,
  verifier, README, CI gate ledger, and test-driver manifest are updated in the
  same focused change.

## Artifact Families

| Area | Representative latest artifacts | Owning gate |
|---|---|---|
| COW local recovery and microbench | `cow-overlay-recovery-latest.json`, `cow-overlay-bench-latest.json` | [`COW_OVERLAY_BENCH_RECOVERY.md`](COW_OVERLAY_BENCH_RECOVERY.md) |
| COW external kill-at-step | `cow-overlay-kill-at-step-latest.json` | [`COW_OVERLAY_KILL_AT_STEP_DEVICE_GATE.md`](COW_OVERLAY_KILL_AT_STEP_DEVICE_GATE.md) |
| Storage metrics and storage UI accounting | `storage-metrics-sequence-latest.json`, captured `/system/df` fixtures when present | [`STORAGE_METRICS.md`](STORAGE_METRICS.md), [`STORAGE_METRICS_SEQUENCE_RUNNER.md`](STORAGE_METRICS_SEQUENCE_RUNNER.md) |
| SAF/Documents direct output | `saf-direct-output-latest.json` | [`SAF_DIRECT_OUTPUT_GATE.md`](SAF_DIRECT_OUTPUT_GATE.md) |
| File-I/O and syscall/path profiles | `file-io-bench-latest.json`, `file-io-microbench-latest.json`, `path-micro-profile-latest.json`, `path-micro-profile-cached-latest.json`, `syscall-usecase-profile-latest.json`, `syscall-usecase-profile-syscall-latest.json` | [`STORAGE_METRICS.md`](STORAGE_METRICS.md), [`DIRECT_SYSCALL_COVERAGE.md`](DIRECT_SYSCALL_COVERAGE.md) |
| Archive / `docker cp` | Future `docker-cp-e2e-latest.json` or device diagnostics under `files/pdocker/diagnostics/` | [`DOCKER_CP_E2E_DEVICE_GATE.md`](DOCKER_CP_E2E_DEVICE_GATE.md) |

## Layer Boundaries

- The COW/overlay layer owns copy-up, whiteout, hardlink-ring metadata, and
  merged-view recovery semantics.
- The SAF mediator owns Android Documents provider access, direct-output
  fallback classification, and sidecar metadata for Unix-like behavior on FAT or
  provider-backed storage.
- Archive/runtime code must use the filesystem backend contract and must not
  bypass lower layers to depend on SAF implementation details.
- Storage metrics report both apparent view sizes and de-duplicated backing
  sizes; shared bytes must not be double-counted as unique usage.

## Maintenance Checklist

Before deleting, moving, or pruning any storage evidence:

1. Confirm the artifact is indexed here or in [`EVIDENCE_INDEX.md`](EVIDENCE_INDEX.md).
2. Confirm the owning gate document names the producer and verifier.
3. Preserve mutable `latest` pointers unless every producer/consumer path is
   migrated in the same change.
4. Keep non-promoting planned-gap evidence when it prevents stale UI, daemon, or
   release claims from looking green.

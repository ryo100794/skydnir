# Android Storage Metrics Sequence Runner

Snapshot date: 2026-05-17.

`scripts/android-storage-metrics-sequence.sh` is a non-destructive scaffold for
collecting the storage metrics sequence evidence described in
`docs/test/STORAGE_METRICS.md`.

## Stale Artifact Policy

The runner removes the previous output before doing any work:

```sh
rm -f docs/test/storage-metrics-sequence-latest.json
```

This is intentional. A pre-existing JSON file with `success=true` must never be
able to satisfy the Android sequence gate. The script always creates a fresh
artifact for the current invocation and keeps `success=false` while any device
phase is missing or only planned.

## Basic Invocation

From the repository root:

```sh
scripts/android-storage-metrics-sequence.sh \
  --adb adb \
  --package io.github.ryo100794.pdocker.compat \
  --out docs/test/storage-metrics-sequence-latest.json
```

The runner requires an `adb` executable and an Android package name. It uses the
existing verifier capture path when available:

```sh
python3 scripts/verify-storage-metrics.py --capture-device --output <baseline>
```

After writing the fresh sequence artifact, it invokes:

```sh
python3 scripts/verify-storage-metrics.py --sequence <artifact>
```

The current scaffold is expected to exit non-zero because only the baseline
read-only capture is attempted. The generated artifact records the sequence
validator return code and remains a planned gap with `success=false`.

## Planned Device Phases

The artifact records these required phases in order:

1. `baseline` - read-only capture through `verify-storage-metrics.py
   --capture-device`.
2. `after-build` - planned build of a scenario-owned image that reuses an
   existing lower layer.
3. `after-rebuild` - planned unchanged rebuild of the same Dockerfile, proving
   `UniqueBytes` and `SharedLayerBytes` do not grow from reused layers.
4. `after-edit` - planned container file edit/copy-up, proving `SizeRw` and
   `ContainerUpperBytes` increase.
5. `after-prune` - planned prune of only scenario-owned unused objects, proving
   cleanup does not increase `UniqueBytes`.

The scaffold does not run Docker build, edit, or prune commands. Those phases
must be implemented as scenario-owned device steps before the storage metrics
sequence can be promoted.

## Artifact Contract

The output JSON uses `schema: pdocker.storage.metrics.sequence.v1` so it can be
handed to `verify-storage-metrics.py --sequence`. Extra runner fields record:

- `status: planned-gap`
- `success: false`
- whether a previous artifact was removed
- the baseline capture command, return code, log path, and completeness flag
- the planned build/rebuild/edit/prune evidence requirements
- the sequence verifier command, return code, and log path
- remaining gaps that must be closed before any future `success=true`

A passing device artifact requires fresh real snapshots for every phase and a
zero return code from `verify-storage-metrics.py --sequence`. Until then,
`success=false` is the only valid result.

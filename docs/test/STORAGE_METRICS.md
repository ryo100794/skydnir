# Storage Metrics Verification

Snapshot date: 2026-05-04.

## Purpose

This is the focused verification lane for pdocker storage metrics. It keeps the
storage accounting contract separate from broad compatibility and UI checks, so
the expectations can run quickly from JSON and then be repeated on an Android
device when storage behavior changes.

## Fast Fixture Check

Run the offline fixture validator from the repository root:

```sh
python3 scripts/verify-storage-metrics.py
```

To inspect the expected JSON shape:

```sh
python3 scripts/verify-storage-metrics.py --print-fixture
```

To validate captured device or daemon output, store it as a JSON object with
`system_df`, `images`, and `containers` sections, then run:

```sh
python3 scripts/verify-storage-metrics.py --fixture /path/to/storage-snapshot.json
```

The fixture mode intentionally does not start pdockerd, build an APK, or require
ADB. It validates only the storage-metric contract.

## Device Capture

The verifier can also capture the three Docker-compatible endpoints from a
running Android debug install:

```sh
python3 scripts/verify-storage-metrics.py \
  --capture-device \
  --package io.github.ryo100794.pdocker.compat \
  --output /tmp/pdocker-storage-snapshot.json
```

This reads:

- `/system/df`
- `/images/json`
- `/containers/json?all=1&size=1`

The capture path shells through `adb run-as`, sends simple HTTP GET requests to
`files/pdocker/pdockerd.sock`, combines the JSON responses into the fixture
shape, writes `--output` when provided, and validates the result immediately.
Use `--adb`, `--package`, `--socket`, and `--timeout` when the device setup
differs from the defaults.

To confirm the exact commands without requiring a connected device or running
ADB:

```sh
python3 scripts/verify-storage-metrics.py --capture-device --dry-run
```

## Accounting Contract

- `SharedLayerBytes`, `ContainerUpperBytes`, `UniqueBytes`, free space, totals,
  image sizes, and container sizes must be numeric and nonnegative. Optional
  view counters such as `RootfsViewBytes` must also be numeric and
  nonnegative when present.
- `SharedLayerBytes` is the deduplicated layer pool. A layer used by two images
  is stored once and counted once in that pool, even though both image rows may
  report apparent size that includes the same layer.
- `ImageViewBytes` and image `VirtualSize` values are apparent merged views over
  lower layer data. They are useful for per-image display, but they overlap
  each other and overlap the shared layer pool, so they must not be added to
  `UniqueBytes`, `SharedLayerBytes`, or device totals.
- `ContainerUpperBytes` is private writable upperdir data owned by containers.
  It is the same concept Docker exposes as writable container size (`SizeRw`):
  files copied up, created, edited, or deleted in a running container live here.
- `RootfsViewBytes`, when present, is an apparent merged rootfs view. It can be
  larger than `ContainerUpperBytes` because it includes lower image data, and it
  must not be used as a unique storage bucket.
- `UniqueBytes` is the sum of unique on-disk components: shared layer pool plus
  container private upperdir bytes, and any explicit future unique buckets such
  as volumes or build cache.
- Per-image `SharedSize + UniqueSize` must equal `VirtualSize` for the pdocker
  image metric rows.
- Container `SizeRw` is private upper storage. When `SizeRootFs` is present, it
  must be at least `SizeRw`.

## Manual Device Acceptance

Use this lane after changes that affect layer storage, image listing,
container copy-up/edit behavior, prune behavior, or Android storage refresh.

1. Install or start a fresh debug build and confirm the app has started
   pdockerd. The capture helper expects the pdockerd socket at
   `files/pdocker/pdockerd.sock` under the app data directory.
2. Preview the capture commands with `python3
   scripts/verify-storage-metrics.py --capture-device --dry-run`. This is the
   repeatable record of the endpoint set used for the device note.
3. Capture a baseline snapshot with `python3
   scripts/verify-storage-metrics.py --capture-device --output
   /tmp/pdocker-storage-baseline.json`. The command validates the captured
   `system_df`, `images`, and `containers` sections before exiting.
4. Build an image from a Dockerfile that reuses an existing base layer. Refresh
   metrics by running the capture command again. Confirm layer pool bytes remain
   deduplicated while the new image row can still show an apparent
   `VirtualSize` that includes already-counted lower data.
5. Rebuild the same Dockerfile without changes. Refresh metrics and confirm the
   shared layer pool is not counted twice and `UniqueBytes` does not grow from
   reused layers.
6. Create a container, edit or copy a file inside it so copy-up/private storage
   is created in the container upperdir, then capture
   `/containers/json?all=1&size=1`. Confirm `SizeRw` increases, remains
   nonnegative, and is reflected in `ContainerUpperBytes`.
7. Run image/container prune for unused objects. Refresh metrics and confirm
   removed unique layers or upper directories reduce the relevant unique bucket,
   while layers still referenced by remaining images stay in the shared pool.
8. Repeat one build or edit flow after pruning. Confirm metrics recover to a
   coherent nonnegative state and still validate with the capture or fixture
   script.

Record the device model, APK flavor, build SHA, commands used, and the final
validated JSON snapshot in the test note or PR that exercised this lane.

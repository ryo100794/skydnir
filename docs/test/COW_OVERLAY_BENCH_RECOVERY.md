# COW Overlay Bench and Recovery Gate

Snapshot date: 2026-05-13.

This gate covers the materialized-rootfs `libcow` fallback.  It is deliberately
separate from SAF direct mediator work and from GPU work.

## Scope

The local gate records two JSON artifacts:

- `docs/test/cow-overlay-bench-latest.json`
- `docs/test/cow-overlay-recovery-latest.json`

The benchmark artifact includes one metric row per round for:

- `open_close`
- `stat`
- `create`
- `unlink`
- `rename`
- `copy_up`
- `layer_lookup`

Each metric records:

- operation count;
- total nanoseconds;
- average nanoseconds per operation;
- p50/p95/p99 nanoseconds;
- metric-specific metadata.

The `copy_up` metric is both a performance measurement and a correctness
check.  It mutates hardlinked upper files while `libcow` is loaded and fails the
artifact if the lower payload changes or the upper remains hardlinked.

## Recovery coverage

`test_cow.sh` writes `cow-overlay-recovery-latest.json` after local correctness
checks pass.  The artifact records:

- copy-up failure fail-closed behavior;
- truncate failure fail-closed behavior;
- metadata mutation failure fail-closed behavior;
- corrupt hardlink ring metadata rebuild from the payload tree.

The hardlink ring metadata is treated as a rebuildable accelerator only.  The
payload tree remains the source of truth.  If the accelerator is corrupt or
stale after OOM, LMK, ENOSPC, or partial writes, startup repair must be able to
discard it and rebuild from the payload tree.

## Planned external kill-at-step cases

The current local gate does not kill the daemon/helper process mid-mutation.
Those cases are recorded as `planned-gap`, never as success:

- copy-up temp payload write;
- copy-up rename publication;
- whiteout creation;
- hardlink ring metadata write.

These cases require a device or process-control harness that can terminate the
runtime at deterministic mutation checkpoints, restart it, and verify that no
partial upper, whiteout, metadata, or cache state is trusted as complete.

## Commands

Static and schema contract:

```bash
python3 scripts/verify-cow-overlay-bench-recovery.py
```

Local executable gate:

```bash
python3 scripts/verify-cow-overlay-bench-recovery.py --run-local
```

Direct benchmark:

```bash
make -C docker-proot-setup/src/overlay all
COW_BENCH_OPS=1000 \
  COW_BENCH_JSON=docs/test/cow-overlay-bench-latest.json \
  bash docker-proot-setup/src/overlay/bench_cow.sh
```

Direct recovery:

```bash
COW_TEST_JSON=docs/test/cow-overlay-recovery-latest.json \
  bash docker-proot-setup/src/overlay/test_cow.sh
```

## Remaining gap

This scaffold does not yet prove Android process death recovery at exact
mutation checkpoints.  The next gate must add device-side kill/restart
automation and promote the `planned-gap` kill-at-step cases to executable
evidence before release claims can be made for crash-safe overlay mutation.

# COW Overlay Bench and Recovery Gate

Snapshot date: 2026-05-15.

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

Each metric records operation count, total nanoseconds, average nanoseconds per
operation, p50/p95/p99 nanoseconds, and metric-specific metadata.  The
`copy_up` metric is both a performance measurement and a correctness check: it
mutates hardlinked upper files while `libcow` is loaded and fails if the lower
payload changes or the upper remains hardlinked.

## Recovery artifact schema

`test_cow.sh` writes `cow-overlay-recovery-latest.json` after local correctness
checks pass.  The artifact has:

- `Checks`: summary pass/planned-gap statuses.
- `CaseResults`: executable fail-closed cases, each with `Id`, `Operation`,
  `Fault`, `ExpectedRecovery`, `Status`, and `Evidence`.
- `NegativeCases`: the same injected-fault cases, making explicit that these
  are negative paths where mutation must not continue after the fault.
- `KillAtStepConcreteCases`: deterministic kill-at-step cases that now have
  executable local evidence.  The current required concrete case is
  `copy_up.kill_before_rename_recovery`; it must also appear in `CaseResults`
  and `NegativeCases` with matching evidence.
- `KillAtStepPlannedCases`: remaining external daemon/helper process-kill
  checkpoints that stay `planned-gap` until a device/process-control harness
  exists.
- `OOMForcedKillConsistencyCases`: restart/LMK/forced-kill consistency oracles.
  These remain `planned-gap` in the host-local gate, but the verifier requires
  explicit expected consistency, failure oracle, and gap reason so they cannot
  be silently represented as success evidence.

Required executable case ids are:

- `copy_up.before_rename`
- `copy_up.kill_before_rename_recovery`
- `copy_up.truncate_before_rename`
- `metadata.chmod_before_rename`
- `rename.destination_copyup_fail_closed`
- `renameat.destination_copyup_fail_closed`
- `whiteout.before_publish`
- `rename.before_publish`
- `archive_put.stage_failure`
- `hardlink_metadata.corrupt_rebuild`
- `hardlink_metadata.truncated_rebuild`
- `low_space.copy_up_enospc`

## Recovery coverage

The executable host-local gate now records fail-closed evidence for:

- copy-up write/truncate failures injected before rename publication;
- local copy-up kill-step recovery with orphan `.cow*` cleanup;
- hardlink metadata mutation failure for `chmod`;
- `rename()`/`renameat()` destination copy-up fail-closed behavior before
  replacing a hardlinked lower object;
- whiteout marker creation failure before marker publication;
- rename/replace staging failure before destination publication;
- archive PUT stage failure before live upperdir publication;
- simulated low-space/`ENOSPC` during temp payload write;
- corrupt and truncated hardlink ring metadata rebuild from the payload tree.

The hardlink ring metadata is treated as a rebuildable accelerator only.  The
payload tree remains the source of truth.  If the accelerator is corrupt or
stale after OOM, LMK, ENOSPC, or partial writes, startup repair must discard it
and rebuild from the payload tree.

## OOM and forced-kill consistency oracles

The host-local gate does not create real Android LMK pressure or kill/restart
`pdockerd`, but the recovery artifact now records the required external oracles:

- `oom_or_lmk.restart_reconciliation`: after restart, a missing live pid must
  classify the operation/container as `interrupted-or-lmk-suspected`, attach the
  last memory evidence, and suppress stale `Up`/`running` UI state.
- `forced_kill.daemon_during_overlay_mutation`: daemon death during copy-up,
  whiteout, rename, or hardlink-cache publication must leave either old
  committed state or a complete published upper entry after reconciliation.
- `forced_kill.helper_during_archive_put`: helper death during archive PUT must
  discard staged extraction and preserve the live upperdir.

These entries are not pass evidence until an Android/device or process-control
harness kills the correct process at deterministic checkpoints, restarts it,
and validates the persisted operation/container identity plus merged-view state.

## Kill-at-step evidence requirements

The local gate now promotes the `copyup.before_rename` kill path to concrete
artifact evidence.  A valid recovery artifact must prove both:

- `copy_up.before_rename`: deterministic fail injection before the copy-up
  rename returns failure, leaves lower and upper payloads unchanged, and leaves
  no `.cow` temp behind.
- `copy_up.kill_before_rename_recovery`: deterministic kill injection at
  `copyup.before_rename` leaves an orphan `.cow` temp that startup cleanup
  removes while lower and upper payloads remain unchanged.

The current local gate still does not kill the Android daemon/helper process
mid-mutation.  Those external cases are recorded as `planned-gap`, never as
success:

- copy-up temp payload write;
- copy-up rename publication;
- whiteout creation;
- archive PUT stage publication;
- rename destination publication;
- hardlink ring metadata write.

These cases require a device or process-control harness that can terminate the
runtime at deterministic mutation checkpoints, restart it, and verify that no
partial upper, whiteout, metadata, archive stage, rename stage, or cache state is
trusted as complete.

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

This scaffold proves the host-local `copyup.before_rename` fail/kill evidence
above, but it does not yet prove Android daemon/helper process death recovery
at every mutation checkpoint.  The next gate must add device-side kill/restart
automation and promote the remaining external `planned-gap` kill-at-step cases
to executable evidence before release claims can be made for crash-safe overlay
mutation.

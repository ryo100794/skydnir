# COW/Overlay Android Kill-at-Step Device Gate

Snapshot date: 2026-05-15.

Status: planned-gap.  `success=false` and
`stable_checkpoint_eligible=false` are required until a connected Android
adb/run-as execution captures real daemon/helper kill evidence.

## Purpose

This gate is the external Android counterpart to the host-local
`COW_OVERLAY_BENCH_RECOVERY.md` checks.  It must not pass from static review,
HTTP acknowledgement, shell exit zero, or synthetic JSON.  A passing artifact
must show that the harness:

1. reaches a deterministic mutation checkpoint inside the APK/daemon/helper;
2. records the operation id, process target, pid, and pre-kill state;
3. kills that exact daemon/helper process at the checkpoint;
4. restarts/reconciles pdocker; and
5. verifies the merged view and residue state after restart.

## Required artifact

Latest path:

- `docs/test/cow-overlay-kill-at-step-latest.json`

Schema:

- `schema=pdocker.cow-overlay-kill-at-step-device.v1`
- `scenario_id=cow.overlay.external-daemon-helper-kill-at-step`
- non-passing records: `status=planned-gap` or `status=blocked-device`,
  `success=false`, `stable_checkpoint_eligible=false`,
  `device_promotion_evidence=false`
- passing records: `status=pass`, `success=true`,
  `device_promotion_evidence=true`, `collected_via_adb_run_as=true`, and all
  required cases below set to `Status=pass`

## Required kill-at-step cases

| Case | Target | Required recovery proof |
|---|---|---|
| `copy_up.daemon_kill_before_publish` | daemon | lower bytes unchanged; no trusted `.cow` temp; merged view is old state or complete published upper |
| `rename.daemon_kill_before_destination_publish` | daemon | destination is old committed state or complete renamed state; staged rename residue is not trusted |
| `metadata.daemon_kill_before_metadata_publish` | daemon | mode/sidecar metadata is atomic with payload state; metadata-only commits are rejected |
| `whiteout.daemon_kill_before_marker_publish` | daemon | lower entry remains visible unless a complete marker was published; partial marker is ignored |
| `hardlink_ring.daemon_kill_during_cache_publish` | daemon | payload tree is authoritative; corrupt/truncated ring cache is discarded and rebuilt |
| `hardlink_ring.helper_kill_during_cache_rebuild` | helper | helper death cannot promote a partial ring cache; restart rebuilds from payload tree |

Each passing case must include `OperationId`, `CheckpointToken` equal to the
case `Step`, `CheckpointReached=true`, `CheckpointAckFile`,
`PreKillStateFile`, numeric `KilledPid`, `KilledProcessName`, `KillSignal`
(`TERM`/`KILL` or `SIGTERM`/`SIGKILL`), `KillDelivered=true`,
`RestartCompleted=true`, `RestartEvidenceFile`, `MergedViewVerified=true`,
`FailureOracleMatched=true`, `PostRestartStateFile`, case-specific `Proof`
booleans, and non-empty relative `EvidenceFiles`.  Absolute paths and `..`
evidence paths are rejected because host-side static JSON cannot prove pulled
device evidence.

## Device helper checkpoint contract

The only promoted execution route is adb + `run-as` into the debuggable app and
the APK-provided helper at:

- `files/pdocker/tools/pdocker-cow-kill-at-step`

For each required case the device-side runner writes a token-scoped request and
invokes the helper in two phases:

1. `prepare --checkpoint <step>` starts the operation, stops exactly at the
   checkpoint, records pre-kill state, and writes `checkpoint.pid` whose first
   line is the exact daemon/helper pid to kill.
2. The shell runner sends `TERM` to that exact numeric pid only.  It never uses
   `pkill`, `killall`, process-name matching, or a process list as kill proof.
3. `verify --checkpoint <step>` restarts/reconciles pdocker and writes
   post-restart evidence proving the merged view and residue oracle.

The host artifact may be promoted only after the pulled evidence is summarized
into `status=pass`, `success=true`, `stable_checkpoint_eligible=true`,
`device_promotion_evidence=true`, and all required per-case fields above.
Device-side shell exit zero, helper stdout, or HTTP/CLI acknowledgement alone is
not a pass condition.

## Commands

Write a non-promoting planned-gap artifact when adb/device evidence is absent:

```bash
python3 scripts/verify/runner/cow_overlay_kill_at_step_device.py \
  --artifact docs/test/cow-overlay-kill-at-step-latest.json
```

Validate an existing artifact:

```bash
python3 scripts/verify/runner/cow_overlay_kill_at_step_device.py \
  --validate-artifact docs/test/cow-overlay-kill-at-step-latest.json
```

Attempting device execution is explicit and still fail-closed until the APK
exports the deterministic COW checkpoint helper:

```bash
python3 scripts/verify/runner/cow_overlay_kill_at_step_device.py \
  --execute-device \
  --artifact docs/test/cow-overlay-kill-at-step-latest.json
```

Without adb or without the future checkpoint helper, the command writes
`blocked-device`/`planned-gap` with `success=false`; it does not fabricate pass
status.

## Negative cases that must remain rejected

- `success=true` on any non-passing artifact.
- `status=pass` without adb serial, run-as collection, operation id, exact pid,
  checkpoint acknowledgement, kill delivery, restart proof, merged-view proof,
  and evidence files for every required case.
- `status=pass` with `stable_checkpoint_eligible=false` or with missing
  `artifact_contract.pass_requires_exact_checkpoint_pid=true`.
- Per-case evidence that omits `CheckpointAckFile`, `PreKillStateFile`,
  `RestartEvidenceFile`, or `PostRestartStateFile`.
- Treating HTTP/CLI acknowledgement, helper exit status, or a clean process list
  alone as recovery proof.
- Trusting partial `.cow`, rename stage, metadata stage, whiteout marker, or
  hardlink-ring cache residue as committed state.

## Residual gap

The host/static gate now defines the device artifact contract and a fail-closed
runner, but the APK/daemon does not yet expose deterministic COW checkpoint
control.  Until that protocol exists and the required cases pass on a connected
Android device, COW/overlay external daemon/helper kill-at-step recovery remains
planned-gap and non-promoting.

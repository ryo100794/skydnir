# Runtime Teardown Device Gate

Snapshot date: 2026-05-13.

This gate defines the device evidence required before `docker stop`,
`docker kill`, and `docker rm` can be treated as real runtime teardown. The
focused lane fails closed as **non-passing evidence** unless both stop-rm and
kill-rm proof files reduce to promoted same-container-ID evidence. The fallback
remains `Status: planned-gap` and `Success: false`; `device-pass` is accepted
only when the host verifier reloads the external proof files and all promoted
reducer fields are complete.

## Entry point

Run through the Android smoke script:

```bash
scripts/android-device-smoke.sh --runtime-teardown <default-workspace|llama>
```

The focused planned-gap lane is:

```bash
scripts/verify-heavy.sh --android-runtime-teardown
```

The promoted device-pass reducer is:

```bash
scripts/verify-runtime-teardown-artifact.py \
  --expect-device-pass \
  --evidence-root docs/test/runtime-teardown \
  docs/test/runtime-teardown-latest.json
```

The device artifact is:

```text
files/pdocker/diagnostics/runtime-teardown-latest.json
```

The detailed evidence directory is:

```text
files/pdocker/diagnostics/runtime-teardown/
```

If no real Android device/runtime is available, this gate must still be treated
as non-success: `Success: false` remains the only valid result.

## Same-container-ID artifact schema

`runtime-teardown-latest.json` names the schema as
`same-container-id-teardown-artifact` and points to one per-container proof file
for each lifecycle path:

- `same-container-id-stop-rm.json`
- `same-container-id-kill-rm.json`

Each per-container proof file has `Kind: same-container-id-teardown-proof`,
the exact Engine `ContainerId`, and a `BeforeAfterEvidence` object. It remains
`Status: planned-gap` / `Success: false` until its own reducer can promote it to
`Status: device-pass` / `Success: true`. The gate is only allowed to pass after all
evidence agrees on the **same Engine container ID**.

The Android smoke now writes a fail-closed `VerifierReduction` object for each
proof. It records the reducer-visible status of `ReducedEngineContainerId`,
`SourceContainerIds`, `EngineInspectSameContainerId`, `ProcessTreeClear`,
`EngineContainersAfterIdAbsent`, `DirectChildAbsence`, `ListenerAbsence`,
`StalePidAbsence`, `StaleNameAbsence`, `GpuMediaExecutorResidueAbsence`,
`PersistedStateCleared`, `LifecycleLogsBound`, and `ContainerLogsBound`, plus
`LivePreOperationIdentitySameContainerId`,
`StalePidAnchoredToLiveIdentity`,
`DirectChildProofAnchoredToLiveIdentity`, `ListenerOwnerSameContainerId`,
`GpuMediaExecutorResidueSameContainerId`,
`PersistedStateJsonSameContainerId`, `LifecycleLogsSameContainerId`, and
`ContainerLogsSameContainerId`. Companion reduction artifacts
(`*-gap-reasons.txt`, `*-fail-reasons.txt`,
`*-mismatched-container-ids.txt`, and `*-survivors.txt`) explain why the proof
is still planned-gap or what concrete residue was observed. For a promoted
`device-pass`, the reducer must also bind `/containers/json`, Engine inspect,
`state.json`, process table/process tree, listener owner, GPU/media-executor
residue, lifecycle logs, and container logs to the proof `ContainerId` through
`SourceContainerIds`; any missing, prefix-only, or mismatched Engine container
ID is a hard failure. Promotion additionally requires a `LiveIdentity` object
proving the live pre-stop/pre-kill inspect PID was running and present before
the operation;
stale-PID and direct-child absence must be anchored to that identity, not to an
after-operation `State.Pid=0`. A `ListenerReduction` object must prove no
listener remains for that live PID, `PersistedStateTeardownFields` must prove
all active PID/launcher/process-group fields are cleared and `PdockerTeardown`
has `NoOrphanProcesses: true` with no survivors, and `LogBinding` must prove
lifecycle and container logs are complete and bound to the same exact Engine
container ID. These fields are diagnostic only until every required flag is true
and both per-container proofs plus the top-level artifact are explicitly
promoted to `device-pass`.

Required before/after evidence for that same ID:

- Engine create/start/stop/kill/rm outputs.
- Engine inspect before operation, after operation, and after remove.
- Engine `/containers/json?all=1` before and after the probe.
- Process tree snapshots before start, after start, after stop/kill, and after
  remove.
- Listener absence from `/proc/net/tcp`, `/proc/net/tcp6`, `ss -ltnp`, and
  `netstat -ltnp` snapshots.
- Stale PID checks based on the live pre-stop/pre-kill inspect `State.Pid` and
  the post-operation process table. After-operation PID clearing alone is not
  accepted.
- `DirectChildAbsence` checks for the live pre-stop/pre-kill inspect
  `State.Pid` in the post-operation and post-remove process tables. A clean
  after-operation parent PID is not enough if any direct child spawned by the
  runtime launcher remains alive.
- GPU/media executor residue scans for pdocker GPU, Vulkan, media, camera,
  audio, and executor helper processes.
- `StaleName` and duplicate-name checks against `/containers/json?all=1` after
  remove. Reused names and previous-container logs cannot stand in for the
  current Engine container ID.
- Persisted `state.json` snapshots before/start/after operation/after remove.
  After successful stop/kill, `State.Pid`, `PidStartTime`,
  `PdockerKnownPids`, `PdockerLauncherPid`, and
  `PdockerLauncherPidStartTime`, `PdockerLauncherPgid`, and
  `PdockerProcessGroupId` must be cleared; `PdockerTeardown` must record
  `NoOrphanProcesses: true` and an empty `Survivors` list.
- Container logs and lifecycle command logs. The verifier records these as
  container logs evidence, not as proof by themselves.

## Device-pass guard

The artifact includes a `DeviceGate` object with `RequiresAdb: true`,
`CollectedViaAdbRunAs: true`, `HostStaticVerifierCannotPromote: true`, and
`DoNotClaimDevicePassWithoutAdb: true`. Host/static tests may verify schema and
negative cases, but they must not promote the artifact to `device-pass`.

## Negative cases that must remain non-success

The smoke writes explicit negative-case artifacts so host static tests can catch
accidental weakening:

- `negative-http-204-only.json`: HTTP 204 or any Engine API acknowledgement
  alone is not sufficient.
- `negative-cli-exit-zero-only.json`: CLI exit 0 alone is not sufficient.
- `negative-name-only.json`: a matching container name without the same Engine
  container ID is not sufficient.
- `negative-stale-state-json.json`: stale `state.json` that still names a
  container after the process is gone is not sufficient.
- `negative-listener-only.json`: listener absence without process-tree and
  stale-PID proof is not sufficient.
- `negative-process-only.json`: a clean process table without Engine inspect and
  logs for the same container ID is not sufficient.
- `negative-previous-container-logs.json`: previous-container logs, reused
  names, or duplicate names are not sufficient.
- `negative-wrong-container-id.json`: mixed evidence from a different container
  ID is not sufficient.

Until `scripts/verify-runtime-teardown-artifact.py --expect-device-pass`
reduces all collected files to one same-container-ID proof, the artifact must
remain:

```text
Status: planned-gap
Success: false
```

This is deliberate. The scaffold prevents fake success while making the
remaining device proof explicit and repeatable.

## Current implementation and remaining gap

`pdockerd` stop/kill/rm now treats teardown as a no-orphan operation: it scans
known PIDs, descendants, launcher PIDs, and container-path-referencing runtime
processes, tracks the runtime launcher's process group for late direct children
that no longer reference the container path, signals those processes, refuses to
mark the container stopped if survivors remain, and clears stale active PID and
process-group fields only after the survivor set is empty.

The smoke script collects raw before/after evidence, same-container-ID proof
schemas, and negative-case artifacts for process tree, listener absence, stale
PID, GPU/media executor residue, Engine inspect, logs, and persisted state. The
host-side reducer now reads the top-level artifact plus the referenced
`same-container-id-*.json` and negative-case JSON files and rejects missing or
fake proof. It also rejects promoted artifacts where listener ownership,
GPU/media executor residue, or persisted-state teardown fields are missing,
stale, or bound to a different Engine container ID. The device smoke performs
the reduction pass for exact container IDs, create/inspect binding,
`/containers/json` after-rm absence, stale-name
absence, stale PID and direct-child evidence anchored to live pre-stop/pre-kill
identity, listener absence for that live PID, GPU/media executor residue,
persisted-state teardown fields, and lifecycle/container log binding. It still
fails closed: any missing, stale, mismatched, or prefix-only evidence keeps the
proof and top-level artifact at planned-gap. Promotion occurs only when stop-rm
and kill-rm both show no surviving process tree, no surviving listener, no
stale PID reference, no GPU/media executor residue, and no stale state/log
confusion for that exact Engine container ID.

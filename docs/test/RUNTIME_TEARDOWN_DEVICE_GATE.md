# Runtime Teardown Device Gate

Snapshot date: 2026-05-13.

This gate defines the device evidence required before `docker stop`,
`docker kill`, and `docker rm` can be treated as real runtime teardown. The
current implementation is intentionally a **non-passing scaffold**: it writes
`Status: planned-gap` and `Success: false` until a device verifier proves the
complete same-container-ID teardown chain.

## Entry point

Run through the Android smoke script:

```bash
scripts/android-device-smoke.sh --runtime-teardown <default-workspace|llama>
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
`Status: planned-gap`, `Success: false`, the exact Engine `ContainerId`, and a
`BeforeAfterEvidence` object. The gate is only allowed to pass after all
evidence agrees on the **same Engine container ID**.

Required before/after evidence for that same ID:

- Engine create/start/stop/kill/rm outputs.
- Engine inspect before operation, after operation, and after remove.
- Engine `/containers/json?all=1` before and after the probe.
- Process tree snapshots before start, after start, after stop/kill, and after
  remove.
- Listener absence from `/proc/net/tcp`, `/proc/net/tcp6`, `ss -ltnp`, and
  `netstat -ltnp` snapshots.
- Stale PID checks based on inspect `State.Pid` and the post-operation process
  table.
- GPU/media executor residue scans for pdocker GPU, Vulkan, media, camera,
  audio, and executor helper processes.
- Persisted `state.json` snapshots before/start/after operation/after remove.
  After successful stop/kill, `State.Pid`, `PidStartTime`,
  `PdockerKnownPids`, `PdockerLauncherPid`, and
  `PdockerLauncherPidStartTime` must be cleared; `PdockerTeardown` must record
  `NoOrphanProcesses: true` and an empty `Survivors` list.
- Container logs and lifecycle command logs. The verifier records these as
  container logs evidence, not as proof by themselves.

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

Until the verifier reduces all collected files to one same-container-ID proof,
the artifact must remain:

```text
Status: planned-gap
Success: false
```

This is deliberate. The scaffold prevents fake success while making the
remaining device proof explicit and repeatable.

## Current implementation and remaining gap

`pdockerd` stop/kill/rm now treats teardown as a no-orphan operation: it scans
known PIDs, descendants, launcher PIDs, and container-path-referencing runtime
processes, signals those processes, refuses to mark the container stopped if
survivors remain, and clears stale active PID fields only after the survivor
set is empty.

The smoke script collects raw before/after evidence, same-container-ID proof
schemas, and negative-case artifacts for process tree, listener absence, stale
PID, GPU/media executor residue, Engine inspect, logs, and persisted state. The
remaining work is the device verifier that reads those files and proves that
each stopped/killed/removed container has no surviving process tree, no
surviving listener, no stale PID reference, no GPU/media executor residue, and
no stale state/log confusion for that exact Engine container ID before
promoting the artifact from planned-gap to device-pass.

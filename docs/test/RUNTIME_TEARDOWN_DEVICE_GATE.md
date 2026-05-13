# Runtime Teardown Device Gate

Snapshot date: 2026-05-13.

This gate defines the device evidence required before `docker stop`,
`docker kill`, and `docker rm` can be treated as real runtime teardown.  The
current implementation is intentionally a **non-passing scaffold**: it collects
the evidence, but it still writes `Status: planned-gap` and `Success: false`
until a device verifier proves the complete teardown chain.

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

## Required evidence

The gate is only allowed to pass after all evidence agrees on the **same Engine
container ID**:

- Engine create/start/stop/kill/rm outputs.
- Engine inspect before and after each lifecycle operation.
- Engine `/containers/json?all=1` before and after the probe.
- Process tree snapshots before start, after start, after stop/kill, and after
  remove.
- Listener absence from `/proc/net/tcp`, `/proc/net/tcp6`, `ss -ltnp`, and
  `netstat -ltnp` snapshots.
- Stale PID checks based on inspect `State.Pid` and the post-operation process
  table.
- GPU/media executor residue scans for pdocker GPU, Vulkan, media, camera,
  audio, and executor helper processes.
- Persisted `state.json` snapshots.
- Container logs and lifecycle command logs.  The verifier records these as
  container logs evidence, not as proof by themselves.

## Non-success contract

The following signals are **not sufficient**:

- HTTP 204 or any other Engine API acknowledgement alone.
- CLI exit 0 alone.
- A matching container name without the same Engine container ID.
- Stale `state.json` that still names a container after the process is gone.
- Listener absence without process-tree and stale-PID proof.
- Clean process table without Engine inspect and logs for the same container ID.

Until the verifier reduces all collected files to one same-container-ID proof,
the artifact must remain:

```text
Status: planned-gap
Success: false
```

This is deliberate.  The scaffold prevents fake success while making the
remaining device proof explicit and repeatable.

## Current remaining gap

The smoke script now collects the required raw evidence, including process tree,
listener absence, stale PID, GPU/media executor residue, Engine inspect, logs,
and same-container-ID evidence JSON files.  The remaining work is the device
verifier that reads those files and proves that each stopped/killed/removed
container has no surviving process tree, no surviving listener, no stale PID
reference, and no GPU/media executor residue for that exact Engine container ID.

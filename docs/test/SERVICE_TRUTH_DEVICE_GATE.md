# Service truth same-container-ID device gate

Status: planned-gap until complete device proof; device-pass when all seven sources match
Host gate: `python3 scripts/verify-service-truth-plan.py`
Device smoke: `bash scripts/android-device-smoke.sh --no-install --service-truth <target>`
Device artifact: `files/pdocker/diagnostics/service-truth-latest.json` copied to `docs/test/service-truth-latest.json` only after a real device run.

This gate exists to prevent UI service cards from claiming healthy/running from
configured ports, stale names, stale state, or background-job success.  Until a
real Android device artifact proves one current Engine container ID across every
truth source, the device artifact must remain `Status: planned-gap` and
`Success: false`. When, and only when, the device runner proves all seven
sources below are current/proven for one exact 64-hex ID, it may emit
`Status: device-pass`, `Success: true`, and exit 0.

## Required same-ID proof

A passing future artifact must reduce all of these independently captured
sources to the exact same Engine container ID:

1. `UICard` - rendered card export from
   `files/pdocker/diagnostics/ui-rendered-service-truth-latest.json`; the card
   must have `TruthState: current` and `ContainerIdSource` from current Engine
   truth, not stale `state.json` only. It must also expose explicit
   `CurrentReason`, `StaleReason`, and `UnknownReason` fields so
   `EngineSnapshotMissing`, `EngineSnapshotOld`, and
   `EngineContainerIdMismatch` cases are machine-readable non-success reasons.
2. `DockerPs` - `docker ps --no-trunc` / `docker ps -a --no-trunc` evidence for
   the running Engine container ID.
3. `EngineApiContainersJson` - Engine API `/containers/json?all=1` evidence.
4. `PersistedStateJson` - current `state.json` container ID comparison.
5. `ProcessTable` - process-table owner/PID evidence for the selected Engine
   container ID.
6. `ListenerProbe` - listener socket evidence, including `/proc/net/tcp`, for
   the service port owner. A port declaration or prefix match is not proof;
   `OwnerEngineContainerId` must be the exact same 64-hex ID as
   `Proof.EngineContainerId`, and `SelectedPidOwnsListener` must be true.
7. `ContainerLogs` - current logs from the same Engine container ID with a fresh
   `pdocker-service-truth-marker` entry containing the selected container ID.

Names, labels, configured ports, and candidate scores are hints only.  They may
select a candidate for debugging, but they are never sufficient acceptance
proof.

## Device artifact schema

`service-truth-latest.json` is a device artifact, not a host-generated pass.  The
host verifier and tests validate the schema, but they do not manufacture a pass:
missing, stale, ambiguous, prefix-only, or mismatched evidence stays
`planned-gap`/`Success: false` and exits nonzero.

Required top-level shape:

```json
{
  "SchemaVersion": 1,
  "Kind": "service-truth",
  "Status": "planned-gap",
  "Success": false,
  "Target": "default-workspace",
  "StartedAt": "2026-05-13T00:00:00Z",
  "CompletedAt": "2026-05-13T00:00:01Z",
  "DeviceProofAttempted": true,
  "TruthContract": {
    "RequiredSameContainerId": [
      "UICard",
      "DockerPs",
      "EngineApiContainersJson",
      "PersistedStateJson",
      "ProcessTable",
      "ListenerProbe",
      "ContainerLogs"
    ],
    "AcceptanceRule": "Success may become true only when every source names the same current Engine container ID."
  },
  "Proof": {
    "EngineContainerId": null,
    "SameEngineContainerId": false,
    "MismatchedSources": [],
    "MissingSources": ["ProcessTable", "ListenerProbe", "ContainerLogs"]
  },
  "Sources": {
    "UICard": {"ContainerId": null, "TruthState": "unknown", "CurrentReason": null, "StaleReason": null, "UnknownReason": "EngineSnapshotMissing", "Proven": false, "Artifacts": []},
    "DockerPs": {"ContainerId": null, "Proven": false, "Artifacts": []},
    "EngineApiContainersJson": {"ContainerId": null, "Proven": false, "Artifacts": []},
    "PersistedStateJson": {"ContainerId": null, "Proven": false, "Artifacts": []},
    "ProcessTable": {"ContainerId": null, "Pid": null, "Proven": false, "Artifacts": []},
    "ListenerProbe": {"ContainerId": null, "OwnerEngineContainerId": null, "Pid": null, "SelectedPidOwnsListener": false, "ExactEngineContainerIdRequired": true, "Proven": false, "Artifacts": []},
    "ContainerLogs": {"ContainerId": null, "Proven": false, "CurrentServiceMarker": false, "Artifacts": []}
  },
  "Evidence": {
    "UICard": ["files/pdocker/diagnostics/service-truth/ui-rendered-service-truth-latest.json"],
    "DockerPs": ["files/pdocker/diagnostics/service-truth/engine-ps.out"],
    "EngineApiContainersJson": ["files/pdocker/diagnostics/service-truth/engine-containers-json.http"],
    "PersistedStateJson": ["files/pdocker/diagnostics/service-truth/state-id-comparison.json"],
    "ProcessTable": ["files/pdocker/diagnostics/service-truth/process-table.txt"],
    "ListenerProbe": ["files/pdocker/diagnostics/service-truth/listener-probe.json", "files/pdocker/diagnostics/service-truth/proc-net-tcp.txt"],
    "ContainerLogs": ["files/pdocker/diagnostics/service-truth/logs-<container-id>.out"]
  },
  "CandidateSelection": {
    "SelectedEngineContainerId": null,
    "Artifacts": ["files/pdocker/diagnostics/service-truth/engine-candidates.json"]
  },
  "StateIdComparison": {
    "AnyStateIdMatchesSelected": false,
    "Artifacts": ["files/pdocker/diagnostics/service-truth/state-id-comparison.json"]
  },
  "ListenerProcNetTcpEvidence": {
    "Artifacts": ["files/pdocker/diagnostics/service-truth/listener-probe.json", "files/pdocker/diagnostics/service-truth/proc-net-tcp.txt"]
  },
  "Unresolved": ["planned device proof gap"]
}
```

The passing form must set `Success: true` only when:

- `Status` is exactly `device-pass`.
- `Proof.SameEngineContainerId` is `true`.
- `Proof.EngineContainerId` is a non-empty exact Engine container ID.
- `TruthContract.RequiredSameContainerId` contains all seven required sources.
- Every required `Sources.<name>.Proven` is `true`.
- Every required `Sources.<name>.ContainerId` exactly equals
  `Proof.EngineContainerId`; prefix-only matches are not enough.
- `UICard.TruthState` is `current`; `unknown`, `stale`, and `ambiguous` are
  explicit non-success states, with `CurrentReason`, `StaleReason`, and
  `UnknownReason` carrying reason codes such as `EngineSnapshotMissing`,
  `EngineSnapshotOld`, and `EngineContainerIdMismatch`.
- `ListenerProbe.OwnerEngineContainerId` is an exact 64-hex match to
  `Proof.EngineContainerId`, and `SelectedPidOwnsListener` is `true`;
  `configured-ports.txt`, `/proc/net/tcp`, or a 12-character prefix alone is
  never enough.
- Every required source names at least one raw artifact path under
  `files/pdocker/diagnostics/service-truth/`.

## Negative cases that must not pass

- Status is `planned-gap`, `skip`, or `skipped`; those states must keep
  `Success: false` and are never counted as a pass.
- Configured port exists in compose metadata, but there is no listener.
- A listener exists, but its PID/process tree maps to a different Engine
  container ID.
- `state.json` points to an exited or stale duplicate name.
- UI rendered card is missing, `unknown`, `stale`, or `ambiguous`.
- `docker ps` and Engine API disagree, or only one of them has the container.
- Current logs are missing, or log markers come from a previous Engine
  container ID.
- Compose/build/background job success exists without the same-container-ID
  proof above.

The current runner is therefore expected to be both a useful diagnostic collector
and a real device gate: complete same-container-ID proof returns
`device-pass`/`Success: true`/exit 0; every incomplete case remains
`planned-gap`/`Success: false`/nonzero. Host tests enforce that fake success
cannot be introduced silently.

## Current diagnostic collection detail

`--service-truth` now writes an additional aggregation artifact at
`files/pdocker/diagnostics/service-truth/same-id-source-summary.json`.  The
summary is used by the promoted gate, but it is only a pass signal when the
seven source objects also prove the same exact 64-hex Engine container ID.

The diagnostic collector attempts to reduce each source to the selected exact
Engine container ID as follows:

- UI card: copies `ui-rendered-service-truth-latest.json` and records
  `EngineContainerId`, `ContainerIdSource`, `TruthState`, `CurrentReason`,
  `StaleReason`, `UnknownReason`, `EngineSnapshotStatus`,
  `EngineSnapshotAgeMs`, and `EngineSnapshotIdMismatch`.
- Docker ps: records `engine-ps.out`, `engine-ps-running.out`,
  `engine-candidates.tsv`, and `engine-candidates.json`; the selected row must
  be an exact ID match, not a prefix match.
- Engine API: records `/containers/json?all=1`, `inspect-selected.http`, and
  `docker-inspect-selected.out` for the selected ID.
- Persisted state: records every discovered `state.json` and
  `state-id-comparison.json`; matches are exact selected-ID matches only.
- Process table: extracts the selected container inspect PID and searches it in
  `process-table.txt`.
- Listener: records `/proc/net/tcp`, `listener-probe.json`, and the best-effort
  socket-inode-to-PID map `listener-owner-map.json` / `.tsv`; pass promotion
  requires `OwnerEngineContainerId` to equal the exact selected 64-hex Engine
  ID and `SelectedPidOwnsListener` to be true, not merely a configured port.
- Logs: records per-running-container logs and `logs-selected.out` for the
  selected Engine container ID.

These artifacts make device failures actionable. They become a pass signal only
through the strict same-ID branch; otherwise the runner preserves the old
planned-gap failure behavior.

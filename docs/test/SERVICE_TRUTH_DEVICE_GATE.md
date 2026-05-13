# Service truth same-container-ID device gate

Status: planned-gap
Host gate: `python3 scripts/verify-service-truth-plan.py`
Device smoke: `bash scripts/android-device-smoke.sh --no-install --service-truth <target>`
Planned artifact: `files/pdocker/diagnostics/service-truth-latest.json` copied to `docs/test/service-truth-latest.json` only after a real device run.

This gate exists to prevent UI service cards from claiming healthy/running from
configured ports, stale names, stale state, or background-job success.  Until a
real Android device artifact proves one current Engine container ID across every
truth source, the device artifact must remain `Status: planned-gap` and
`Success: false`.

## Required same-ID proof

A passing future artifact must reduce all of these independently captured
sources to the exact same Engine container ID:

1. `UICard` - rendered card export from
   `files/pdocker/diagnostics/ui-rendered-service-truth-latest.json`; the card
   must have `TruthState: current` and `ContainerIdSource` from current Engine
   truth, not stale `state.json` only.
2. `DockerPs` - `docker ps --no-trunc` / `docker ps -a --no-trunc` evidence for
   the running Engine container ID.
3. `EngineApiContainersJson` - Engine API `/containers/json?all=1` evidence.
4. `PersistedStateJson` - current `state.json` container ID comparison.
5. `ProcessTable` - process-table owner/PID evidence for the selected Engine
   container ID.
6. `ListenerProbe` - listener socket evidence, including `/proc/net/tcp`, for
   the service port owner.
7. `ContainerLogs` - current logs from the same Engine container ID with a fresh
   service marker.

Names, labels, configured ports, and candidate scores are hints only.  They may
select a candidate for debugging, but they are never sufficient acceptance
proof.

## Device artifact schema

`service-truth-latest.json` is a device artifact, not a host-generated pass.  The
planned-gap implementation may collect partial evidence, but must not report
success.

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
    "UICard": {"ContainerId": null, "TruthState": "unknown", "Proven": false, "Artifacts": []},
    "DockerPs": {"ContainerId": null, "Proven": false, "Artifacts": []},
    "EngineApiContainersJson": {"ContainerId": null, "Proven": false, "Artifacts": []},
    "PersistedStateJson": {"ContainerId": null, "Proven": false, "Artifacts": []},
    "ProcessTable": {"ContainerId": null, "Pid": null, "Proven": false, "Artifacts": []},
    "ListenerProbe": {"ContainerId": null, "Pid": null, "Proven": false, "Artifacts": []},
    "ContainerLogs": {"ContainerId": null, "Proven": false, "Artifacts": []}
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

Promotion from `planned-gap` requires changing both the implementation and this
contract.  The first passing form must set `Success: true` only when:

- `Status` is no longer `planned-gap`.
- `Proof.SameEngineContainerId` is `true`.
- `Proof.EngineContainerId` is a non-empty exact Engine container ID.
- `TruthContract.RequiredSameContainerId` contains all seven required sources.
- Every required `Sources.<name>.Proven` is `true`.
- Every required `Sources.<name>.ContainerId` exactly equals
  `Proof.EngineContainerId`; prefix-only matches are not enough.
- `UICard.TruthState` is `current`; `unknown`, `stale`, and `ambiguous` are
  explicit non-success states.
- Every required source names at least one raw artifact path under
  `files/pdocker/diagnostics/service-truth/`.

## Negative cases that must not pass

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

The current runner is therefore expected to be a useful diagnostic collector and
a failing gate: real-device-required work remains represented as
`planned-gap`/`Success: false`, while host tests enforce that fake success cannot
be introduced silently.

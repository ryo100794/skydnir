#!/usr/bin/env python3
"""Validate the executable acceptance plan for service truth and teardown.

This is intentionally a static gate while the Android/runtime implementation is
still a planned gap.  It prevents the gap from drifting back to vague prose: the
ledger must spell out runnable smoke commands, required evidence sources,
negative cases, and exit criteria before the gap may be closed.
"""

from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "tests" / "feature_scenarios.json"
DOCS = [
    ROOT / "docs" / "test" / "COMPATIBILITY.md",
    ROOT / "docs" / "test" / "SERVICE_TRUTH_DEVICE_GATE.md",
    ROOT / "docs" / "plan" / "TODO.md",
    ROOT / "docs" / "plan" / "GOAL_EXECUTION_QUEUE_20260513.md",
]
ANDROID_SMOKE = ROOT / "scripts" / "android-device-smoke.sh"

SERVICE_ID = "service.health.listener-container-proof"
TEARDOWN_ID = "runtime.stop-process-tree-teardown"

SERVICE_SOURCES = {
    "ui_card",
    "engine_api_containers_json",
    "persisted_state_json",
    "process_table",
    "listener_probe",
    "container_logs",
}
TEARDOWN_SOURCES = {
    "engine_api_containers_json",
    "persisted_state_json",
    "process_table",
    "lifecycle_logs",
    "container_logs",
}


SERVICE_ARTIFACT_SOURCES = [
    "UICard",
    "DockerPs",
    "EngineApiContainersJson",
    "PersistedStateJson",
    "ProcessTable",
    "ListenerProbe",
    "ContainerLogs",
]
ENGINE_CONTAINER_ID_RE = re.compile(r"^[0-9a-fA-F]{64}$")
SOURCE_ARTIFACT_TERMS = {
    "UICard": ["ui-rendered-service-truth-latest.json"],
    "DockerPs": ["engine-ps"],
    "EngineApiContainersJson": ["engine-containers-json", "inspect-selected"],
    "PersistedStateJson": ["state-id-comparison"],
    "ProcessTable": ["process-table", "inspect-selected"],
    "ListenerProbe": ["configured-ports", "listener-probe", "listener-owner-map", "proc-net-tcp"],
    "ContainerLogs": ["logs-selected"],
}


def fail(message: str) -> None:
    print(f"verify-service-truth-plan: FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def ok(message: str) -> None:
    print(f"ok: {message}")


def load_ledger() -> dict[str, Any]:
    try:
        return json.loads(LEDGER.read_text())
    except OSError as exc:
        fail(f"could not read {LEDGER.relative_to(ROOT)}: {exc}")
    except json.JSONDecodeError as exc:
        fail(f"{LEDGER.relative_to(ROOT)} is not valid JSON: {exc}")


def scenario_by_id(data: dict[str, Any], sid: str) -> dict[str, Any]:
    for scenario in data.get("scenarios", []):
        if scenario.get("id") == sid:
            return scenario
    fail(f"missing scenario {sid}")


def require_terms(name: str, text: str, terms: list[str]) -> None:
    missing = [term for term in terms if term.lower() not in text.lower()]
    if missing:
        fail(f"{name} is missing required terms: {', '.join(missing)}")


def require_list(scenario: dict[str, Any], key: str, min_len: int) -> list[str]:
    value = scenario.get(key)
    if not isinstance(value, list) or len(value) < min_len or not all(isinstance(item, str) and item.strip() for item in value):
        fail(f"{scenario.get('id')} must define {key} with at least {min_len} non-empty strings")
    return value


def validate_command_tokens(scenario: dict[str, Any], commands: list[str]) -> None:
    for command in commands:
        try:
            tokens = shlex.split(command)
        except ValueError as exc:
            fail(f"{scenario.get('id')} has non-tokenizable command {command!r}: {exc}")
        if not tokens:
            fail(f"{scenario.get('id')} has an empty command")
        for token in tokens:
            if token.startswith(("scripts/", "tests/", "docs/", "docker-proot-setup/")):
                path = ROOT / token
                if not path.exists():
                    fail(f"{scenario.get('id')} command references missing path {token}")


def validate_planned_acceptance(scenario: dict[str, Any], *, required_sources: set[str], evidence_suffix: str) -> None:
    sid = scenario.get("id")
    if scenario.get("status") != "planned-gap":
        fail(f"{sid} must remain planned-gap until device evidence exists")
    if scenario.get("plan_gate") != "python3 scripts/verify-service-truth-plan.py":
        fail(f"{sid} must point plan_gate at this verifier")
    evidence_target = str(scenario.get("evidence_target") or "")
    if not evidence_target.startswith("docs/test/") or not evidence_target.endswith(evidence_suffix):
        fail(f"{sid} must define docs/test/*{evidence_suffix} evidence_target")

    smoke_commands = require_list(scenario, "smoke_commands", 2)
    validate_command_tokens(scenario, smoke_commands)

    sources = set(require_list(scenario, "required_truth_sources", len(required_sources)))
    missing_sources = sorted(required_sources - sources)
    if missing_sources:
        fail(f"{sid} is missing truth sources: {', '.join(missing_sources)}")

    negative_cases = require_list(scenario, "negative_cases", 3)
    exit_criteria = require_list(scenario, "exit_criteria", 4)
    combined = "\n".join([str(scenario.get("checks", "")), *negative_cases, *exit_criteria])
    require_terms(
        sid,
        combined,
        ["Engine container ID", "state", "process", "logs", "stale", "mismatch" if sid == SERVICE_ID else "204"],
    )



def artifact_error(message: str) -> ValueError:
    return ValueError(f"service truth artifact contract violation: {message}")


def require_artifact_paths(source_name: str, source: dict[str, Any]) -> list[str]:
    artifacts = source.get("Artifacts")
    if not isinstance(artifacts, list) or not artifacts or not all(isinstance(item, str) and item for item in artifacts):
        raise artifact_error(f"{source_name} must name raw artifact paths")
    joined = "\n".join(artifacts).lower()
    for term in SOURCE_ARTIFACT_TERMS[source_name]:
        if term.lower() not in joined:
            raise artifact_error(f"{source_name} artifacts must include {term}")
    if source_name != "UICard" and not any(path.startswith("files/pdocker/diagnostics/service-truth/") for path in artifacts):
        raise artifact_error(f"{source_name} artifacts must be under files/pdocker/diagnostics/service-truth/")
    if source_name == "UICard" and not any(path.startswith("files/pdocker/diagnostics/") for path in artifacts):
        raise artifact_error("UICard artifacts must be under files/pdocker/diagnostics/")
    return artifacts


def validate_service_truth_artifact(artifact: dict[str, Any]) -> None:
    """Validate the future device success shape for same-container-ID proof.

    Planned-gap artifacts are allowed to carry partial diagnostics, but they are
    never successful.  Any promoted success must prove one exact 64-hex Engine
    container ID across UI card, Docker/Engine API, persisted state, process
    table, listener/port ownership, and current logs.
    """

    if artifact.get("SchemaVersion") != 1:
        raise artifact_error("SchemaVersion must be 1")
    if artifact.get("Kind") != "service-truth":
        raise artifact_error("Kind must be service-truth")

    if artifact.get("Status") in {"planned-gap", "skip", "skipped"}:
        if artifact.get("Success") is not False:
            raise artifact_error("planned-gap/skip artifacts must set Success false and are never a pass")
        return

    if artifact.get("Status") != "device-pass":
        raise artifact_error("successful service truth artifact must set Status device-pass")
    if artifact.get("Success") is not True:
        raise artifact_error("non-planned service truth artifact is not a success")

    proof = artifact.get("Proof")
    if not isinstance(proof, dict):
        raise artifact_error("Proof object is required")
    expected = proof.get("EngineContainerId")
    if not isinstance(expected, str) or not ENGINE_CONTAINER_ID_RE.fullmatch(expected):
        raise artifact_error("Proof.EngineContainerId must be an exact 64-hex Engine container ID")
    if proof.get("SameEngineContainerId") is not True:
        raise artifact_error("Proof.SameEngineContainerId must be true")
    if proof.get("MismatchedSources"):
        raise artifact_error("success cannot include mismatched sources")
    if proof.get("MissingSources"):
        raise artifact_error("success cannot include missing sources")

    contract_sources = artifact.get("TruthContract", {}).get("RequiredSameContainerId")
    if not isinstance(contract_sources, list) or not set(SERVICE_ARTIFACT_SOURCES).issubset(set(contract_sources)):
        raise artifact_error("TruthContract.RequiredSameContainerId is incomplete")

    sources = artifact.get("Sources")
    if not isinstance(sources, dict):
        raise artifact_error("Sources object is required")

    for source_name in SERVICE_ARTIFACT_SOURCES:
        source = sources.get(source_name)
        if not isinstance(source, dict):
            raise artifact_error(f"missing source {source_name}")
        if source.get("Proven") is not True:
            raise artifact_error(f"{source_name} must be proven")
        if source.get("ContainerId") != expected:
            raise artifact_error(f"{source_name} must exactly match Proof.EngineContainerId")
        require_artifact_paths(source_name, source)

    ui = sources["UICard"]
    if ui.get("TruthState") != "current":
        raise artifact_error("UICard.TruthState must be current")
    if str(ui.get("ContainerIdSource", "")).lower() in {"", "unknown", "state.json", "persistedstatejson"}:
        raise artifact_error("UICard.ContainerIdSource must not be unknown or stale state-only")

    listener = sources["ListenerProbe"]
    if not listener.get("Ports") and not listener.get("ProcNetTcpMatchedPorts"):
        raise artifact_error("ListenerProbe must bind at least one configured/listening port")
    if listener.get("Pid") in (None, "", 0, "0"):
        raise artifact_error("ListenerProbe must include a listener owner PID")
    owner_id = listener.get("OwnerEngineContainerId") or listener.get("ListenerOwnerEngineContainerId")
    if not isinstance(owner_id, str) or not ENGINE_CONTAINER_ID_RE.fullmatch(owner_id):
        raise artifact_error("ListenerProbe.OwnerEngineContainerId must be an exact 64-hex Engine container ID, not a prefix or port declaration")
    if owner_id != expected:
        raise artifact_error("ListenerProbe owner must exactly match Proof.EngineContainerId")
    if listener.get("SelectedPidOwnsListener") is not True:
        raise artifact_error("ListenerProbe must prove the selected PID owns the listener, not only declare a port")

    process = sources["ProcessTable"]
    if process.get("Pid") in (None, "", 0, "0"):
        raise artifact_error("ProcessTable must include selected container PID")
    if str(process.get("Pid")) != str(listener.get("Pid")):
        raise artifact_error("listener PID must map to the same selected container process")

    logs = sources["ContainerLogs"]
    if logs.get("CurrentServiceMarker") is not True:
        raise artifact_error("ContainerLogs must include a current service log marker")


def build_success_fixture() -> dict[str, Any]:
    cid = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    pid = 4242
    sources = {
        "UICard": {
            "ContainerId": cid,
            "ContainerIdSource": "EngineApiContainersJson",
            "TruthState": "current",
            "Proven": True,
            "Artifacts": ["files/pdocker/diagnostics/service-truth/ui-rendered-service-truth-latest.json"],
        },
        "DockerPs": {
            "ContainerId": cid,
            "Proven": True,
            "Artifacts": ["files/pdocker/diagnostics/service-truth/engine-ps.out"],
        },
        "EngineApiContainersJson": {
            "ContainerId": cid,
            "Proven": True,
            "Artifacts": [
                "files/pdocker/diagnostics/service-truth/engine-containers-json.http",
                "files/pdocker/diagnostics/service-truth/inspect-selected.http",
            ],
        },
        "PersistedStateJson": {
            "ContainerId": cid,
            "Proven": True,
            "Artifacts": ["files/pdocker/diagnostics/service-truth/state-id-comparison.json"],
        },
        "ProcessTable": {
            "ContainerId": cid,
            "Pid": pid,
            "Proven": True,
            "Artifacts": [
                "files/pdocker/diagnostics/service-truth/process-table.txt",
                "files/pdocker/diagnostics/service-truth/inspect-selected.http",
            ],
        },
        "ListenerProbe": {
            "ContainerId": cid,
            "OwnerEngineContainerId": cid,
            "SelectedEngineContainerId": cid,
            "Pid": pid,
            "Ports": [18080, 18081],
            "SelectedPidOwnsListener": True,
            "ExactEngineContainerIdRequired": True,
            "Proven": True,
            "Artifacts": [
                "files/pdocker/diagnostics/service-truth/configured-ports.txt",
                "files/pdocker/diagnostics/service-truth/listener-probe.json",
                "files/pdocker/diagnostics/service-truth/listener-owner-map.json",
                "files/pdocker/diagnostics/service-truth/proc-net-tcp.txt",
            ],
        },
        "ContainerLogs": {
            "ContainerId": cid,
            "CurrentServiceMarker": True,
            "Proven": True,
            "Artifacts": ["files/pdocker/diagnostics/service-truth/logs-selected.out"],
        },
    }
    return {
        "SchemaVersion": 1,
        "Kind": "service-truth",
        "Status": "device-pass",
        "Success": True,
        "TruthContract": {"RequiredSameContainerId": SERVICE_ARTIFACT_SOURCES},
        "Proof": {"EngineContainerId": cid, "SameEngineContainerId": True, "MismatchedSources": [], "MissingSources": []},
        "Sources": sources,
    }


def validate_service_truth_fixture_contract() -> None:
    fixture = build_success_fixture()
    validate_service_truth_artifact(fixture)

    mutations = [
        lambda a: a.update({"Status": "planned-gap"}),
        lambda a: a["Proof"].update({"EngineContainerId": a["Proof"]["EngineContainerId"][:12]}),
        lambda a: a["Proof"].update({"SameEngineContainerId": False}),
        lambda a: a["Sources"]["UICard"].update({"TruthState": "stale"}),
        lambda a: a["Sources"]["PersistedStateJson"].update({"ContainerId": "f" * 64}),
        lambda a: a["Sources"]["ListenerProbe"].update({"Ports": [], "ProcNetTcpMatchedPorts": ""}),
        lambda a: a["Sources"]["ListenerProbe"].update({"Pid": 9999}),
        lambda a: a["Sources"]["ListenerProbe"].update({"OwnerEngineContainerId": a["Proof"]["EngineContainerId"][:12]}),
        lambda a: a["Sources"]["ListenerProbe"].update({"SelectedPidOwnsListener": False}),
        lambda a: a["Sources"]["ContainerLogs"].update({"CurrentServiceMarker": False}),
        lambda a: a["Sources"]["ContainerLogs"].update({"Artifacts": []}),
    ]
    for mutate in mutations:
        candidate = json.loads(json.dumps(fixture))
        mutate(candidate)
        try:
            validate_service_truth_artifact(candidate)
        except ValueError:
            continue
        fail("service truth fixture self-check accepted a fake success")

def validate_docs() -> None:
    combined = "\n".join(path.read_text() for path in DOCS)
    require_terms(
        "service truth docs",
        combined,
        [
            "python3 scripts/verify-service-truth-plan.py",
            "docs/test/service-truth-latest.json",
            "docs/test/runtime-teardown-latest.json",
            "UI card",
            "DockerPs",
            "docker ps",
            "/containers/json",
            "state.json",
            "process table",
            "listener probe",
            "logs",
            "Engine container ID",
            "engine-candidates.json",
            "state-id-comparison.json",
            "listener-probe.json",
            "ui-rendered-service-truth-latest.json",
            "TruthState",
            "/proc/net/tcp",
            "planned-gap/Success: false",
            "same-container-ID",
            "ContainerLogs.CurrentServiceMarker",
            "device-pass",
        ],
    )


def validate_android_smoke_entrypoints() -> None:
    smoke = ANDROID_SMOKE.read_text()
    require_terms(
        "android smoke service truth entrypoints",
        smoke,
        [
            "--service-truth",
            "--runtime-teardown",
            "service_truth_acceptance_entrypoint",
            "runtime_teardown_acceptance_entrypoint",
            "service-truth-latest.json",
            "runtime-teardown-latest.json",
            "TruthContract",
            "RequiredSameContainerId",
            "UICard",
            "EngineApiContainersJson",
            "PersistedStateJson",
            "ProcessTable",
            "ListenerProbe",
            "CandidateSelection",
            "engine-candidates.json",
            "state-id-comparison.json",
            "listener-probe.json",
            "ui-rendered-service-truth-latest.json",
            "/proc/net/tcp",
            "LifecycleLogs",
            "ContainerLogs",
            "device-pass",
            "SERVICE_TRUTH_EXIT=0",
            "SERVICE_TRUTH_EXIT=2",
            "\"Success\": false",
        ],
    )


def main() -> int:
    data = load_ledger()
    service = scenario_by_id(data, SERVICE_ID)
    teardown = scenario_by_id(data, TEARDOWN_ID)
    validate_planned_acceptance(service, required_sources=SERVICE_SOURCES, evidence_suffix="service-truth-latest.json")
    validate_planned_acceptance(teardown, required_sources=TEARDOWN_SOURCES, evidence_suffix="runtime-teardown-latest.json")
    validate_docs()
    validate_android_smoke_entrypoints()
    validate_service_truth_fixture_contract()
    ok("service truth fixture rejects fake success without listener/port/log/UI/state same-container-ID proof")
    ok("service truth planned gap has runnable commands, evidence contract, negative cases, and exit criteria")
    ok("runtime teardown planned gap has runnable commands, evidence contract, negative cases, and exit criteria")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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
    ROOT / "docs" / "test" / "RUNTIME_TEARDOWN_DEVICE_GATE.md",
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

SERVICE_REDUCTION_SOURCES = {
    "UICard": "UI card",
    "DockerPs": "docker ps",
    "EngineApiContainersJson": "/containers/json",
    "PersistedStateJson": "state.json",
    "ProcessTable": "process table",
    "ListenerProbe": "listener owner",
    "ContainerLogs": "logs",
}
SERVICE_REQUIRED_REDUCTION_FLAGS = [
    "UICardSameContainerId",
    "DockerPsSameContainerId",
    "EngineApiContainersJsonSameContainerId",
    "PersistedStateJsonSameContainerId",
    "ProcessTableSameContainerId",
    "ListenerOwnerSameContainerId",
    "ContainerLogsSameContainerId",
]

SOURCE_ARTIFACT_TERMS = {
    "UICard": ["ui-rendered-service-truth-latest.json"],
    "DockerPs": ["engine-ps"],
    "EngineApiContainersJson": ["engine-containers-json", "inspect-selected"],
    "PersistedStateJson": ["state-id-comparison"],
    "ProcessTable": ["process-table", "inspect-selected"],
    "ListenerProbe": ["configured-ports", "listener-probe", "listener-owner-map", "proc-net-tcp"],
    "ContainerLogs": ["logs-selected"],
}
TEARDOWN_ARTIFACT_SOURCES = [
    "EngineApiContainersJson",
    "EngineApiInspect",
    "ProcessTable",
    "ProcessTree",
    "DirectChildAbsence",
    "ListenerAbsence",
    "StalePid",
    "StaleName",
    "GpuMediaExecutorResidue",
    "SameContainerId",
    "PersistedStateJson",
    "LifecycleLogs",
    "ContainerLogs",
]
TEARDOWN_REQUIRED_NEGATIVE_CASES = [
    "negative-http-204-only",
    "negative-cli-exit-zero-only",
    "negative-name-only",
    "negative-stale-state-json",
    "negative-listener-only",
    "negative-process-only",
    "negative-previous-container-logs",
    "negative-wrong-container-id",
]
TEARDOWN_REQUIRED_PROOF_SOURCES = [
    "EngineCreateOutput",
    "EngineInspectBefore",
    "EngineInspectAfterOperation",
    "EngineInspectAfterRemove",
    "ProcessTreeBeforeAfter",
    "DirectChildAbsence",
    "ListenerAbsenceBeforeAfter",
    "StalePidAbsence",
    "GpuMediaExecutorResidueBeforeAfter",
    "PersistedStateJsonBeforeAfter",
    "LifecycleLogs",
    "ContainerLogs",
]
TEARDOWN_REDUCTION_SOURCE_IDS = [
    "EngineApiContainersJson",
    "EngineApiInspect",
    "PersistedStateJson",
    "ProcessTable",
    "ProcessTree",
    "ListenerOwner",
    "ContainerLogs",
    "LifecycleLogs",
]

TEARDOWN_REQUIRED_REDUCTION_FLAGS = [
    "EngineInspectSameContainerId",
    "ProcessTreeClear",
    "DirectChildAbsence",
    "ListenerAbsence",
    "StalePidAbsence",
    "StaleNameAbsence",
    "GpuMediaExecutorResidueAbsence",
    "PersistedStateCleared",
    "LifecycleLogsBound",
    "ContainerLogsBound",
]


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


def teardown_artifact_error(message: str) -> ValueError:
    return ValueError(f"runtime teardown artifact contract violation: {message}")


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

    reduction = artifact.get("VerifierReduction")
    if not isinstance(reduction, dict):
        raise artifact_error("VerifierReduction object is required to reduce UI card/docker ps//containers/json/state.json/process table/listener owner/logs")
    if _exact_service_container_id(reduction.get("ReducedEngineContainerId"), "VerifierReduction.ReducedEngineContainerId") != expected:
        raise artifact_error("VerifierReduction.ReducedEngineContainerId must exactly match Proof.EngineContainerId")
    reduction_sources = reduction.get("RequiredSources")
    if not isinstance(reduction_sources, list) or not set(SERVICE_ARTIFACT_SOURCES).issubset(set(reduction_sources)):
        raise artifact_error("VerifierReduction.RequiredSources must include UI card, docker ps, /containers/json, state.json, process table, listener owner, and logs")
    source_ids = reduction.get("SourceContainerIds")
    if not isinstance(source_ids, dict):
        raise artifact_error("VerifierReduction.SourceContainerIds object is required")
    for source_name, human_name in SERVICE_REDUCTION_SOURCES.items():
        if _exact_service_container_id(source_ids.get(source_name), f"VerifierReduction.SourceContainerIds.{source_name}") != expected:
            raise artifact_error(f"VerifierReduction must reduce {human_name} to Proof.EngineContainerId")
    for flag in SERVICE_REQUIRED_REDUCTION_FLAGS:
        if reduction.get(flag) is not True:
            raise artifact_error(f"VerifierReduction.{flag} must be true")
    if reduction.get("MismatchedSources"):
        raise artifact_error("VerifierReduction cannot promote with mismatched sources")
    if reduction.get("MissingSources"):
        raise artifact_error("VerifierReduction cannot promote with missing sources")

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
        if source.get("ExactEngineContainerIdRequired") is not True:
            raise artifact_error(f"{source_name} must require an exact 64-hex Engine container ID")
        require_artifact_paths(source_name, source)

    ui = sources["UICard"]
    if ui.get("TruthState") != "current":
        raise artifact_error("UICard.TruthState must be current")
    if str(ui.get("ContainerIdSource", "")).lower() in {"", "unknown", "state.json", "persistedstatejson"}:
        raise artifact_error("UICard.ContainerIdSource must not be unknown or stale state-only")
    if not ui.get("CurrentReason"):
        raise artifact_error("UICard.CurrentReason must explain why the rendered card is current")

    docker_ps = sources["DockerPs"]
    if docker_ps.get("Running") is not True:
        raise artifact_error("DockerPs.Running must be true for the selected exact Engine container ID")

    engine_api = sources["EngineApiContainersJson"]
    if engine_api.get("CurrentContainerFound") is not True:
        raise artifact_error("EngineApiContainersJson.CurrentContainerFound must be true")
    if engine_api.get("InspectStateRunning") is not True:
        raise artifact_error("EngineApiContainersJson.InspectStateRunning must be true")

    state = sources["PersistedStateJson"]
    if state.get("MatchesSelectedEngineContainerId") is not True:
        raise artifact_error("PersistedStateJson.MatchesSelectedEngineContainerId must be true")

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
    if process.get("SelectedPidPresent") is not True:
        raise artifact_error("ProcessTable.SelectedPidPresent must be true")
    if str(process.get("Pid")) != str(listener.get("Pid")):
        raise artifact_error("listener PID must map to the same selected container process")

    logs = sources["ContainerLogs"]
    if logs.get("CurrentServiceMarker") is not True:
        raise artifact_error("ContainerLogs must include a current service log marker")
    if logs.get("MarkerEngineContainerId") != expected:
        raise artifact_error("ContainerLogs.MarkerEngineContainerId must exactly match Proof.EngineContainerId")


def _require_teardown(condition: bool, message: str) -> None:
    if not condition:
        raise teardown_artifact_error(message)


def _require_service(condition: bool, message: str) -> None:
    if not condition:
        raise artifact_error(message)


def _exact_service_container_id(value: Any, field_name: str) -> str:
    _require_service(isinstance(value, str) and bool(ENGINE_CONTAINER_ID_RE.fullmatch(value)), f"{field_name} must be an exact 64-hex Engine container ID")
    return value


def validate_service_truth_device_pass_artifact(artifact: dict[str, Any]) -> None:
    """Require a promoted device-pass service-truth artifact, not a planned-gap scaffold."""

    if artifact.get("Status") != "device-pass" or artifact.get("Success") is not True:
        raise artifact_error("expected Status=device-pass and Success=true for service truth pass")
    validate_service_truth_artifact(artifact)


def _exact_container_id(value: Any, field_name: str) -> str:
    _require_teardown(isinstance(value, str) and bool(ENGINE_CONTAINER_ID_RE.fullmatch(value)), f"{field_name} must be an exact 64-hex Engine container ID")
    return value


def _require_non_empty_pathish(value: Any, field_name: str) -> None:
    if isinstance(value, str):
        _require_teardown(bool(value.strip()), f"{field_name} must name an artifact path")
    elif isinstance(value, list):
        _require_teardown(bool(value), f"{field_name} must name at least one artifact path")
        for index, item in enumerate(value):
            _require_non_empty_pathish(item, f"{field_name}[{index}]")
    elif isinstance(value, dict):
        _require_teardown(bool(value), f"{field_name} must not be empty")
        for key, item in value.items():
            _require_non_empty_pathish(item, f"{field_name}.{key}")
    else:
        raise teardown_artifact_error(f"{field_name} must name artifact paths")


def validate_runtime_teardown_negative_case(name: str, artifact: dict[str, Any]) -> None:
    """Validate a teardown negative-case artifact remains non-promoting."""

    if artifact.get("Kind") != "runtime-teardown-negative-case":
        raise teardown_artifact_error(f"{name} Kind must be runtime-teardown-negative-case")
    if artifact.get("SchemaVersion") != 1:
        raise teardown_artifact_error(f"{name} SchemaVersion must be 1")
    if artifact.get("Success") is not False:
        raise teardown_artifact_error(f"{name} must never report Success true")
    if artifact.get("ExpectedAccepted") is not False:
        raise teardown_artifact_error(f"{name} must declare ExpectedAccepted=false")
    if not artifact.get("RejectedSignal"):
        raise teardown_artifact_error(f"{name} must record the rejected fake-success signal")
    if not artifact.get("Reason"):
        raise teardown_artifact_error(f"{name} must record why the signal is insufficient")
    required_proof = str(artifact.get("RequiredProof", "")).lower()
    for term in ["same engine container id", "process tree", "listener", "stale pid", "logs"]:
        if term not in required_proof:
            raise teardown_artifact_error(f"{name} RequiredProof must mention {term}")


def validate_runtime_teardown_proof(
    name: str,
    proof: dict[str, Any],
    *,
    expected_cid: str,
    expected_operation: str,
) -> None:
    """Validate one future device-pass same-container-ID teardown proof.

    The smoke script already emits planned-gap proof scaffolds.  A promoted
    artifact must be stricter: every before/after evidence group is tied to one
    exact Engine container ID and the device-side reducer must explicitly
    reduce raw paths into absence/bound-log verdicts.
    """

    if proof.get("Kind") != "same-container-id-teardown-proof":
        raise teardown_artifact_error(f"{name} Kind must be same-container-id-teardown-proof")
    if proof.get("SchemaVersion") != 2:
        raise teardown_artifact_error(f"{name} SchemaVersion must be 2")
    if proof.get("Status") != "device-pass":
        raise teardown_artifact_error(f"{name} must be device-pass before it can promote runtime teardown")
    if proof.get("Success") is not True:
        raise teardown_artifact_error(f"{name} must set Success=true only after device proof reduction")
    cid = _exact_container_id(proof.get("ContainerId"), f"{name}.ContainerId")
    if cid != expected_cid:
        raise teardown_artifact_error(f"{name} ContainerId must exactly match the created Engine container ID")
    if proof.get("Operation") != expected_operation:
        raise teardown_artifact_error(f"{name} Operation must be {expected_operation}")

    required = proof.get("RequiredSameContainerId")
    _require_teardown(isinstance(required, list), f"{name}.RequiredSameContainerId must be a list")
    missing_required = [source for source in TEARDOWN_REQUIRED_PROOF_SOURCES if source not in required]
    if missing_required:
        raise teardown_artifact_error(f"{name} missing RequiredSameContainerId sources: {', '.join(missing_required)}")

    evidence = proof.get("Evidence")
    _require_teardown(isinstance(evidence, dict), f"{name}.Evidence must be an object")
    for source in TEARDOWN_REQUIRED_PROOF_SOURCES:
        _require_teardown(source in evidence, f"{name}.Evidence missing {source}")
        _require_non_empty_pathish(evidence[source], f"{name}.Evidence.{source}")

    before_after = proof.get("BeforeAfterEvidence")
    _require_teardown(isinstance(before_after, dict), f"{name}.BeforeAfterEvidence must be an object")
    for source in [
        "EngineInspect",
        "ProcessTree",
        "DirectChildAbsence",
        "ListenerAbsence",
        "StalePid",
        "GpuMediaExecutorResidue",
        "PersistedStateJson",
        "LifecycleLogs",
        "ContainerLogs",
    ]:
        _require_teardown(source in before_after, f"{name}.BeforeAfterEvidence missing {source}")
        _require_non_empty_pathish(before_after[source], f"{name}.BeforeAfterEvidence.{source}")

    reduction = proof.get("VerifierReduction")
    _require_teardown(isinstance(reduction, dict), f"{name}.VerifierReduction must be present for device-pass")
    reduced_cid = _exact_container_id(reduction.get("ReducedEngineContainerId"), f"{name}.VerifierReduction.ReducedEngineContainerId")
    if reduced_cid != expected_cid:
        raise teardown_artifact_error(f"{name}.VerifierReduction.ReducedEngineContainerId must exactly match ContainerId")
    source_ids = reduction.get("SourceContainerIds")
    _require_teardown(isinstance(source_ids, dict), f"{name}.VerifierReduction.SourceContainerIds must reduce /containers/json/state.json/process table/listener owner/logs to one Engine container ID")
    for source in TEARDOWN_REDUCTION_SOURCE_IDS:
        source_cid = _exact_container_id(source_ids.get(source), f"{name}.VerifierReduction.SourceContainerIds.{source}")
        if source_cid != expected_cid:
            raise teardown_artifact_error(f"{name}.VerifierReduction.SourceContainerIds.{source} must exactly match ContainerId")
    for flag in TEARDOWN_REQUIRED_REDUCTION_FLAGS:
        if reduction.get(flag) is not True:
            raise teardown_artifact_error(f"{name}.VerifierReduction.{flag} must be true")
    if reduction.get("MismatchedContainerIds"):
        raise teardown_artifact_error(f"{name} cannot promote with mismatched container IDs")
    if reduction.get("Survivors"):
        raise teardown_artifact_error(f"{name} cannot promote with surviving processes/listeners/executors")


def _extract_teardown_proof_artifacts(artifact: dict[str, Any], proof_artifacts: dict[str, dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    if proof_artifacts is not None:
        return proof_artifacts
    embedded = artifact.get("ProofArtifacts")
    if isinstance(embedded, dict):
        return embedded
    raise teardown_artifact_error("device-pass runtime teardown requires loaded same-container-ID proof artifacts")


def _extract_teardown_negative_artifacts(artifact: dict[str, Any], negative_artifacts: dict[str, dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    if negative_artifacts is not None:
        return negative_artifacts
    embedded = artifact.get("NegativeCaseArtifacts")
    if isinstance(embedded, dict):
        return embedded
    raise teardown_artifact_error("device-pass runtime teardown requires loaded negative-case artifacts")


def validate_runtime_teardown_artifact(
    artifact: dict[str, Any],
    proof_artifacts: dict[str, dict[str, Any]] | None = None,
    negative_artifacts: dict[str, dict[str, Any]] | None = None,
) -> None:
    """Validate the future device-pass shape for runtime teardown.

    Planned-gap/skip artifacts are valid evidence but must never promote.  A
    device-pass has to survive a fake-success reducer: HTTP 204, CLI exit 0,
    name matching, process absence, listener absence, stale state, or old logs
    are insufficient unless all sources agree for the exact 64-hex Engine ID.
    """

    if artifact.get("SchemaVersion") != 1:
        raise teardown_artifact_error("SchemaVersion must be 1")
    if artifact.get("Kind") != "runtime-teardown":
        raise teardown_artifact_error("Kind must be runtime-teardown")

    if artifact.get("Status") in {"planned-gap", "skip", "skipped"}:
        if artifact.get("Success") is not False:
            raise teardown_artifact_error("planned-gap/skip runtime teardown artifacts must set Success false and are never a pass")
        return

    if artifact.get("Status") != "device-pass":
        raise teardown_artifact_error("successful runtime teardown artifact must set Status device-pass")
    if artifact.get("Success") is not True:
        raise teardown_artifact_error("non-planned runtime teardown artifact is not a success")

    container_ids = artifact.get("ContainerIds")
    _require_teardown(isinstance(container_ids, dict), "ContainerIds object is required")
    stop_cid = _exact_container_id(container_ids.get("StopRm"), "ContainerIds.StopRm")
    kill_cid = _exact_container_id(container_ids.get("KillRm"), "ContainerIds.KillRm")
    if stop_cid == kill_cid:
        raise teardown_artifact_error("StopRm and KillRm must be independent exact Engine container IDs")

    evidence = artifact.get("Evidence")
    _require_teardown(isinstance(evidence, dict), "Evidence object is required")
    for source in TEARDOWN_ARTIFACT_SOURCES:
        _require_teardown(source in evidence, f"Evidence missing {source}")
        _require_non_empty_pathish(evidence[source], f"Evidence.{source}")

    proof_refs = artifact.get("SameContainerIdTeardownArtifacts")
    _require_teardown(isinstance(proof_refs, dict), "SameContainerIdTeardownArtifacts object is required")
    for key, cid, operation in [("StopRm", stop_cid, "stop-rm"), ("KillRm", kill_cid, "kill-rm")]:
        ref = proof_refs.get(key)
        _require_teardown(isinstance(ref, dict), f"SameContainerIdTeardownArtifacts.{key} must be an object")
        if ref.get("ContainerId") != cid:
            raise teardown_artifact_error(f"SameContainerIdTeardownArtifacts.{key}.ContainerId must match ContainerIds.{key}")
        if ref.get("Operation") != operation:
            raise teardown_artifact_error(f"SameContainerIdTeardownArtifacts.{key}.Operation must be {operation}")
        _require_non_empty_pathish(ref.get("Artifact"), f"SameContainerIdTeardownArtifacts.{key}.Artifact")

    proofs = _extract_teardown_proof_artifacts(artifact, proof_artifacts)
    validate_runtime_teardown_proof("same-container-id-stop-rm", proofs.get("same-container-id-stop-rm", {}), expected_cid=stop_cid, expected_operation="stop-rm")
    validate_runtime_teardown_proof("same-container-id-kill-rm", proofs.get("same-container-id-kill-rm", {}), expected_cid=kill_cid, expected_operation="kill-rm")

    negatives = _extract_teardown_negative_artifacts(artifact, negative_artifacts)
    for name in TEARDOWN_REQUIRED_NEGATIVE_CASES:
        validate_runtime_teardown_negative_case(name, negatives.get(name, {}))


def build_success_fixture() -> dict[str, Any]:
    cid = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    pid = 4242
    sources = {
        "UICard": {
            "ContainerId": cid,
            "ContainerIdSource": "EngineApiContainersJson",
            "TruthState": "current",
            "CurrentReason": "EngineSnapshotCurrent",
            "ExactEngineContainerIdRequired": True,
            "Proven": True,
            "Artifacts": ["files/pdocker/diagnostics/service-truth/ui-rendered-service-truth-latest.json"],
        },
        "DockerPs": {
            "ContainerId": cid,
            "Running": True,
            "ExactEngineContainerIdRequired": True,
            "Proven": True,
            "Artifacts": ["files/pdocker/diagnostics/service-truth/engine-ps.out"],
        },
        "EngineApiContainersJson": {
            "ContainerId": cid,
            "CurrentContainerFound": True,
            "InspectStateRunning": True,
            "ExactEngineContainerIdRequired": True,
            "Proven": True,
            "Artifacts": [
                "files/pdocker/diagnostics/service-truth/engine-containers-json.http",
                "files/pdocker/diagnostics/service-truth/inspect-selected.http",
            ],
        },
        "PersistedStateJson": {
            "ContainerId": cid,
            "MatchesSelectedEngineContainerId": True,
            "ExactEngineContainerIdRequired": True,
            "Proven": True,
            "Artifacts": ["files/pdocker/diagnostics/service-truth/state-id-comparison.json"],
        },
        "ProcessTable": {
            "ContainerId": cid,
            "Pid": pid,
            "SelectedPidPresent": True,
            "ExactEngineContainerIdRequired": True,
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
            "MarkerEngineContainerId": cid,
            "ExactEngineContainerIdRequired": True,
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
        "VerifierReduction": {
            "ReducedEngineContainerId": cid,
            "RequiredSources": SERVICE_ARTIFACT_SOURCES,
            "SourceContainerIds": {source: cid for source in SERVICE_ARTIFACT_SOURCES},
            "UICardSameContainerId": True,
            "DockerPsSameContainerId": True,
            "EngineApiContainersJsonSameContainerId": True,
            "PersistedStateJsonSameContainerId": True,
            "ProcessTableSameContainerId": True,
            "ListenerOwnerSameContainerId": True,
            "ContainerLogsSameContainerId": True,
            "MismatchedSources": [],
            "MissingSources": [],
        },
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
        lambda a: a["Sources"]["ContainerLogs"].update({"MarkerEngineContainerId": "f" * 64}),
        lambda a: a["Sources"]["DockerPs"].update({"Running": False}),
        lambda a: a["Sources"]["EngineApiContainersJson"].update({"InspectStateRunning": False}),
        lambda a: a["Sources"]["ProcessTable"].update({"SelectedPidPresent": False}),
        lambda a: a["Sources"]["UICard"].pop("ExactEngineContainerIdRequired"),
        lambda a: a["Sources"]["ContainerLogs"].update({"Artifacts": []}),
        lambda a: a.pop("VerifierReduction"),
        lambda a: a["VerifierReduction"]["SourceContainerIds"].update({"DockerPs": "f" * 64}),
        lambda a: a["VerifierReduction"].update({"ListenerOwnerSameContainerId": False}),
        lambda a: a["VerifierReduction"].update({"MismatchedSources": ["state.json"]}),
    ]
    for mutate in mutations:
        candidate = json.loads(json.dumps(fixture))
        mutate(candidate)
        try:
            validate_service_truth_artifact(candidate)
        except ValueError:
            continue
        fail("service truth fixture self-check accepted a fake success")


def _teardown_proof_fixture(cid: str, operation: str, prefix: str) -> dict[str, Any]:
    before_after = {
        "EngineInspect": {
            "Before": f"files/pdocker/diagnostics/runtime-teardown/{prefix}-inspect-before.http",
            "AfterOperation": f"files/pdocker/diagnostics/runtime-teardown/{prefix}-inspect-after.http",
            "AfterRemove": f"files/pdocker/diagnostics/runtime-teardown/{prefix}-inspect-after-rm.http",
        },
        "ProcessTree": {
            "Before": "files/pdocker/diagnostics/runtime-teardown/process-before.txt",
            "AfterStart": f"files/pdocker/diagnostics/runtime-teardown/process-after-{prefix}-start.txt",
            "AfterOperation": f"files/pdocker/diagnostics/runtime-teardown/process-after-{prefix}.txt",
            "AfterRemove": f"files/pdocker/diagnostics/runtime-teardown/process-after-rm-{prefix}.txt",
        },
        "DirectChildAbsence": {
            "AfterOperation": f"files/pdocker/diagnostics/runtime-teardown/{prefix}-process-tree-after-operation.json",
            "AfterRemove": f"files/pdocker/diagnostics/runtime-teardown/{prefix}-process-tree-after-rm.json",
        },
        "ListenerAbsence": {
            "Before": "files/pdocker/diagnostics/runtime-teardown/listeners-before.txt",
            "AfterStart": f"files/pdocker/diagnostics/runtime-teardown/listeners-after-{prefix}-start.txt",
            "AfterOperation": f"files/pdocker/diagnostics/runtime-teardown/listeners-after-{prefix}.txt",
            "AfterRemove": f"files/pdocker/diagnostics/runtime-teardown/listeners-after-rm-{prefix}.txt",
        },
        "StalePid": {
            "AfterOperation": f"files/pdocker/diagnostics/runtime-teardown/{prefix}-stale-pid-after-operation.json",
            "AfterRemove": f"files/pdocker/diagnostics/runtime-teardown/{prefix}-stale-pid-after-rm.json",
        },
        "GpuMediaExecutorResidue": {
            "Before": "files/pdocker/diagnostics/runtime-teardown/executor-residue-before.txt",
            "AfterStart": f"files/pdocker/diagnostics/runtime-teardown/executor-residue-after-{prefix}-start.txt",
            "AfterOperation": f"files/pdocker/diagnostics/runtime-teardown/executor-residue-after-{prefix}.txt",
            "AfterRemove": f"files/pdocker/diagnostics/runtime-teardown/executor-residue-after-rm-{prefix}.txt",
        },
        "PersistedStateJson": {
            "Before": "files/pdocker/diagnostics/runtime-teardown/persisted-state-before.txt",
            "AfterStart": f"files/pdocker/diagnostics/runtime-teardown/persisted-state-after-{prefix}-start.txt",
            "AfterOperation": f"files/pdocker/diagnostics/runtime-teardown/persisted-state-after-{prefix}.txt",
            "AfterRemove": f"files/pdocker/diagnostics/runtime-teardown/persisted-state-after-rm-{prefix}.txt",
        },
        "LifecycleLogs": {
            "OperationOut": f"files/pdocker/diagnostics/runtime-teardown/{prefix}.out",
            "OperationErr": f"files/pdocker/diagnostics/runtime-teardown/{prefix}.err",
            "RemoveOut": f"files/pdocker/diagnostics/runtime-teardown/rm-{prefix}.out",
            "RemoveErr": f"files/pdocker/diagnostics/runtime-teardown/rm-{prefix}.err",
        },
        "ContainerLogs": {
            "BeforeOperation": f"files/pdocker/diagnostics/runtime-teardown/logs-{prefix}-before.out",
            "AfterOperation": f"files/pdocker/diagnostics/runtime-teardown/logs-{prefix}-after.out",
        },
    }
    return {
        "SchemaVersion": 2,
        "Kind": "same-container-id-teardown-proof",
        "ContainerId": cid,
        "Operation": operation,
        "Status": "device-pass",
        "Success": True,
        "RequiredSameContainerId": TEARDOWN_REQUIRED_PROOF_SOURCES,
        "BeforeAfterEvidence": before_after,
        "Evidence": {
            "EngineCreateOutput": f"files/pdocker/diagnostics/runtime-teardown/create-{prefix}.out",
            "EngineInspectBefore": before_after["EngineInspect"]["Before"],
            "EngineInspectAfterOperation": before_after["EngineInspect"]["AfterOperation"],
            "EngineInspectAfterRemove": before_after["EngineInspect"]["AfterRemove"],
            "ProcessTreeBeforeAfter": list(before_after["ProcessTree"].values()),
            "DirectChildAbsence": list(before_after["DirectChildAbsence"].values()),
            "ListenerAbsenceBeforeAfter": list(before_after["ListenerAbsence"].values()),
            "StalePidAbsence": list(before_after["StalePid"].values()),
            "GpuMediaExecutorResidueBeforeAfter": list(before_after["GpuMediaExecutorResidue"].values()),
            "PersistedStateJsonBeforeAfter": list(before_after["PersistedStateJson"].values()),
            "LifecycleLogs": list(before_after["LifecycleLogs"].values()),
            "ContainerLogs": list(before_after["ContainerLogs"].values()),
        },
        "VerifierReduction": {
            "ReducedEngineContainerId": cid,
            "SourceContainerIds": {source: cid for source in TEARDOWN_REDUCTION_SOURCE_IDS},
            "EngineInspectSameContainerId": True,
            "ProcessTreeClear": True,
            "DirectChildAbsence": True,
            "ListenerAbsence": True,
            "StalePidAbsence": True,
            "StaleNameAbsence": True,
            "GpuMediaExecutorResidueAbsence": True,
            "PersistedStateCleared": True,
            "LifecycleLogsBound": True,
            "ContainerLogsBound": True,
            "MismatchedContainerIds": [],
            "Survivors": [],
        },
    }


def _teardown_negative_fixture(name: str) -> dict[str, Any]:
    return {
        "SchemaVersion": 1,
        "Kind": "runtime-teardown-negative-case",
        "Status": "planned-gap",
        "Success": False,
        "ExpectedAccepted": False,
        "RejectedSignal": name.replace("-", " "),
        "Reason": "This signal is insufficient without same-container-ID before/after proof.",
        "RequiredProof": "same Engine container ID plus before/after process tree, listener absence, stale PID absence, GPU/media executor residue, persisted state, Engine inspect, lifecycle logs, and container logs",
    }


def build_runtime_teardown_success_fixture() -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    stop_cid = "1" * 64
    kill_cid = "2" * 64
    evidence = {
        "EngineApiContainersJson": [
            "files/pdocker/diagnostics/runtime-teardown/engine-containers-before.http",
            "files/pdocker/diagnostics/runtime-teardown/engine-containers-after.http",
        ],
        "EngineApiInspect": [
            "files/pdocker/diagnostics/runtime-teardown/stop-inspect-before.http",
            "files/pdocker/diagnostics/runtime-teardown/stop-inspect-after.http",
            "files/pdocker/diagnostics/runtime-teardown/stop-inspect-after-rm.http",
            "files/pdocker/diagnostics/runtime-teardown/kill-inspect-before.http",
            "files/pdocker/diagnostics/runtime-teardown/kill-inspect-after.http",
            "files/pdocker/diagnostics/runtime-teardown/kill-inspect-after-rm.http",
        ],
        "ProcessTable": [
            "files/pdocker/diagnostics/runtime-teardown/process-before.txt",
            "files/pdocker/diagnostics/runtime-teardown/process-after-stop.txt",
            "files/pdocker/diagnostics/runtime-teardown/process-after-rm-stopped.txt",
            "files/pdocker/diagnostics/runtime-teardown/process-after-kill.txt",
            "files/pdocker/diagnostics/runtime-teardown/process-after-rm-killed.txt",
        ],
        "ProcessTree": [
            "files/pdocker/diagnostics/runtime-teardown/same-container-id-stop-rm.json",
            "files/pdocker/diagnostics/runtime-teardown/same-container-id-kill-rm.json",
            "files/pdocker/diagnostics/runtime-teardown/stop-process-tree-after-stop.json",
            "files/pdocker/diagnostics/runtime-teardown/kill-process-tree-after-kill.json",
        ],
        "DirectChildAbsence": [
            "files/pdocker/diagnostics/runtime-teardown/stop-process-tree-after-stop.json",
            "files/pdocker/diagnostics/runtime-teardown/stop-process-tree-after-rm.json",
            "files/pdocker/diagnostics/runtime-teardown/kill-process-tree-after-kill.json",
            "files/pdocker/diagnostics/runtime-teardown/kill-process-tree-after-rm.json",
        ],
        "ListenerAbsence": [
            "files/pdocker/diagnostics/runtime-teardown/listeners-before.txt",
            "files/pdocker/diagnostics/runtime-teardown/listeners-after-stop.txt",
            "files/pdocker/diagnostics/runtime-teardown/listeners-after-rm-stopped.txt",
            "files/pdocker/diagnostics/runtime-teardown/listeners-after-kill.txt",
            "files/pdocker/diagnostics/runtime-teardown/listeners-after-rm-killed.txt",
        ],
        "StalePid": [
            "files/pdocker/diagnostics/runtime-teardown/stop-stale-pid-after-stop.json",
            "files/pdocker/diagnostics/runtime-teardown/kill-stale-pid-after-kill.json",
        ],
        "StaleName": [
            "files/pdocker/diagnostics/runtime-teardown/stop-stale-name-after-rm.json",
            "files/pdocker/diagnostics/runtime-teardown/kill-stale-name-after-rm.json",
        ],
        "GpuMediaExecutorResidue": [
            "files/pdocker/diagnostics/runtime-teardown/executor-residue-before.txt",
            "files/pdocker/diagnostics/runtime-teardown/executor-residue-after-stop.txt",
            "files/pdocker/diagnostics/runtime-teardown/executor-residue-after-kill.txt",
        ],
        "SameContainerId": [
            "files/pdocker/diagnostics/runtime-teardown/same-container-id-stop-rm.json",
            "files/pdocker/diagnostics/runtime-teardown/same-container-id-kill-rm.json",
        ],
        "PersistedStateJson": [
            "files/pdocker/diagnostics/runtime-teardown/persisted-state-before.txt",
            "files/pdocker/diagnostics/runtime-teardown/persisted-state-after-stop.txt",
            "files/pdocker/diagnostics/runtime-teardown/persisted-state-after-kill.txt",
        ],
        "LifecycleLogs": [
            "files/pdocker/diagnostics/runtime-teardown/stop.out",
            "files/pdocker/diagnostics/runtime-teardown/kill.out",
            "files/pdocker/diagnostics/runtime-teardown/rm-stopped.out",
            "files/pdocker/diagnostics/runtime-teardown/rm-killed.out",
        ],
        "ContainerLogs": [
            "files/pdocker/diagnostics/runtime-teardown/logs-stop-before.out",
            "files/pdocker/diagnostics/runtime-teardown/logs-stop-after.out",
            "files/pdocker/diagnostics/runtime-teardown/logs-kill-before.out",
            "files/pdocker/diagnostics/runtime-teardown/logs-kill-after.out",
        ],
    }
    artifact = {
        "SchemaVersion": 1,
        "Kind": "runtime-teardown",
        "Status": "device-pass",
        "Success": True,
        "ContainerIds": {"StopRm": stop_cid, "KillRm": kill_cid},
        "Evidence": evidence,
        "SameContainerIdTeardownArtifacts": {
            "StopRm": {
                "ContainerId": stop_cid,
                "Artifact": "files/pdocker/diagnostics/runtime-teardown/same-container-id-stop-rm.json",
                "Operation": "stop-rm",
            },
            "KillRm": {
                "ContainerId": kill_cid,
                "Artifact": "files/pdocker/diagnostics/runtime-teardown/same-container-id-kill-rm.json",
                "Operation": "kill-rm",
            },
        },
    }
    proof_artifacts = {
        "same-container-id-stop-rm": _teardown_proof_fixture(stop_cid, "stop-rm", "stop"),
        "same-container-id-kill-rm": _teardown_proof_fixture(kill_cid, "kill-rm", "kill"),
    }
    negative_artifacts = {name: _teardown_negative_fixture(name) for name in TEARDOWN_REQUIRED_NEGATIVE_CASES}
    return artifact, proof_artifacts, negative_artifacts


def validate_runtime_teardown_fixture_contract() -> None:
    artifact, proofs, negatives = build_runtime_teardown_success_fixture()
    validate_runtime_teardown_artifact(artifact, proofs, negatives)

    mutations = [
        lambda a, p, n: a.update({"Status": "planned-gap"}),
        lambda a, p, n: a["ContainerIds"].update({"StopRm": a["ContainerIds"]["StopRm"][:12]}),
        lambda a, p, n: a["Evidence"].pop("DirectChildAbsence"),
        lambda a, p, n: a["SameContainerIdTeardownArtifacts"]["StopRm"].update({"ContainerId": "3" * 64}),
        lambda a, p, n: p["same-container-id-stop-rm"].update({"Status": "planned-gap"}),
        lambda a, p, n: p["same-container-id-stop-rm"].update({"ContainerId": a["ContainerIds"]["StopRm"][:12]}),
        lambda a, p, n: p["same-container-id-stop-rm"]["Evidence"].pop("ListenerAbsenceBeforeAfter"),
        lambda a, p, n: p["same-container-id-stop-rm"]["VerifierReduction"].update({"DirectChildAbsence": False}),
        lambda a, p, n: p["same-container-id-stop-rm"]["VerifierReduction"]["SourceContainerIds"].update({"ProcessTable": "3" * 64}),
        lambda a, p, n: p["same-container-id-kill-rm"]["VerifierReduction"].update({"ReducedEngineContainerId": "3" * 64}),
        lambda a, p, n: p["same-container-id-kill-rm"]["VerifierReduction"].update({"Survivors": ["pid 99"]}),
        lambda a, p, n: n["negative-http-204-only"].update({"Success": True}),
        lambda a, p, n: n["negative-wrong-container-id"].update({"ExpectedAccepted": True}),
    ]
    for mutate in mutations:
        candidate = json.loads(json.dumps(artifact))
        proof_candidate = json.loads(json.dumps(proofs))
        negative_candidate = json.loads(json.dumps(negatives))
        mutate(candidate, proof_candidate, negative_candidate)
        try:
            validate_runtime_teardown_artifact(candidate, proof_candidate, negative_candidate)
        except ValueError:
            continue
        fail("runtime teardown fixture self-check accepted a fake success")


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
            "ExactEngineContainerIdRequired",
            "DockerPs.Running",
            "EngineApiContainersJson.InspectStateRunning",
            "ProcessTable.SelectedPidPresent",
            "ContainerLogs.MarkerEngineContainerId",
            "device-pass",
            "RequiresAdb",
            "DoNotClaimDevicePassWithoutAdb",
            "DirectChildAbsence",
            "StaleName",
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
            "DeviceGate",
            "RequiresAdb",
            "DoNotClaimDevicePassWithoutAdb",
            "DirectChildAbsence",
            "StaleName",
            "write_process_tree_evidence",
            "write_name_residue_evidence",
            "ExactEngineContainerIdRequired",
            "CurrentContainerFound",
            "InspectStateRunning",
            "SelectedPidPresent",
            "MarkerEngineContainerId",
            "device-pass",
            "SERVICE_TRUTH_EXIT=0",
            "SERVICE_TRUTH_EXIT=2",
            "\"Success\": false",
        ],
    )
    try:
        service_body = smoke.split("service_truth_acceptance_entrypoint()", 1)[1].split(
            "runtime_teardown_acceptance_entrypoint()", 1
        )[0]
    except IndexError:
        fail("android smoke is missing an extractable service_truth_acceptance_entrypoint body")
    require_terms(
        "android smoke service truth VerifierReduction",
        service_body,
        [
            "\"VerifierReduction\"",
            "ReducedEngineContainerId",
            "RequiredSources",
            "SourceContainerIds",
            "UICardSameContainerId",
            "DockerPsSameContainerId",
            "EngineApiContainersJsonSameContainerId",
            "PersistedStateJsonSameContainerId",
            "ProcessTableSameContainerId",
            "ListenerOwnerSameContainerId",
            "ContainerLogsSameContainerId",
            "REDUCTION_MISSING_SOURCES",
            "REDUCTION_MISMATCHED_SOURCES",
            "SERVICE_TRUTH_EXIT=0",
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
    validate_runtime_teardown_fixture_contract()
    ok("service truth fixture rejects fake success without listener/port/log/UI/state same-container-ID proof")
    ok("runtime teardown fixture rejects fake success without same-container-ID process/listener/stale/executor/state/log proof")
    ok("service truth planned gap has runnable commands, evidence contract, negative cases, and exit criteria")
    ok("runtime teardown planned gap has runnable commands, evidence contract, negative cases, and exit criteria")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

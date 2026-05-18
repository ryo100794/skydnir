from __future__ import annotations

from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs/test/SERVICE_TRUTH_DEVICE_GATE.md"
SMOKE = ROOT / "scripts/android-device-smoke.sh"
CAPTURE = ROOT / "scripts/android-service-truth-capture.sh"
VERIFIER = ROOT / "scripts/verify-service-truth-plan.py"

_spec = importlib.util.spec_from_file_location("verify_service_truth_plan", VERIFIER)
assert _spec and _spec.loader
verify_service_truth_plan = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(verify_service_truth_plan)

REQUIRED_SOURCES = [
    "UICard",
    "DockerPs",
    "EngineApiContainersJson",
    "PersistedStateJson",
    "ProcessTable",
    "ListenerProbe",
    "ContainerLogs",
]


def validate_same_container_id_contract(artifact: dict) -> None:
    """Host-side contract for future device artifacts.

    This delegates to the static verifier so fixture tests and the executable
    gate reject the same fake-success shapes.
    """

    try:
        verify_service_truth_plan.validate_service_truth_artifact(artifact)
    except ValueError as exc:
        raise AssertionError(str(exc)) from exc


def validate_device_pass_contract(artifact: dict) -> None:
    try:
        verify_service_truth_plan.validate_service_truth_device_pass_artifact(artifact)
    except ValueError as exc:
        raise AssertionError(str(exc)) from exc


def passing_artifact() -> dict:
    return deepcopy(verify_service_truth_plan.build_success_fixture())


class ServiceTruthDeviceGateTest(unittest.TestCase):
    def test_service_truth_device_gate_doc_defines_schema_and_non_success_gap(self):
        text = DOC.read_text()
        for term in [
            "Status: planned-gap",
            "Status: device-pass",
            "Success: false",
            "Success: true",
            "files/pdocker/diagnostics/service-truth-latest.json",
            "docs/test/service-truth-latest.json",
            "TruthContract",
            "RequiredSameContainerId",
            "Proof.SameEngineContainerId",
            "VerifierReduction",
            "ReducedEngineContainerId",
            "SourceContainerIds",
            "UICardSameContainerId",
            "DockerPsSameContainerId",
            "EngineApiContainersJsonSameContainerId",
            "PersistedStateJsonSameContainerId",
            "ProcessTableSameContainerId",
            "ListenerOwnerSameContainerId",
            "ContainerLogsSameContainerId",
            "EngineContainerId",
            "logs-selected.out",
            "docker ps --no-trunc",
            "/containers/json?all=1",
            "state.json",
            "process-table",
            "/proc/net/tcp",
            "logs-<container-id>.out",
            "same-id-source-summary.json",
            "listener-owner-map.json",
            "OwnerEngineContainerId",
            "SelectedPidOwnsListener",
            "ExactEngineContainerIdRequired",
            "Running: true",
            "CurrentContainerFound",
            "InspectStateRunning",
            "SelectedPidPresent",
            "MarkerEngineContainerId",
            "inspect-selected.http",
            "logs-selected.out",
            "exact ID match",
            "prefix-only matches are not enough",
            "unknown",
            "stale",
            "ambiguous",
            "CurrentReason",
            "StaleReason",
            "UnknownReason",
            "EngineSnapshotMissing",
            "EngineSnapshotOld",
            "EngineContainerIdMismatch",
            "fake success",
        ]:
            self.assertIn(term, text)
        for source in REQUIRED_SOURCES:
            self.assertIn(source, text)

    def test_planned_gap_artifact_is_never_success_even_with_collected_evidence(self):
        artifact = passing_artifact()
        artifact["Status"] = "planned-gap"
        artifact["Success"] = True
        with self.assertRaises(AssertionError):
            validate_same_container_id_contract(artifact)

        artifact["Success"] = False
        validate_same_container_id_contract(artifact)

        artifact = passing_artifact()
        artifact["Status"] = "skip"
        artifact["Success"] = True
        with self.assertRaises(AssertionError):
            validate_same_container_id_contract(artifact)

        artifact["Success"] = False
        validate_same_container_id_contract(artifact)

    def test_fake_success_is_rejected_for_missing_mismatched_or_stale_sources(self):
        cases = [
            lambda a: a["TruthContract"].update({"RequiredSameContainerId": REQUIRED_SOURCES[:-1]}),
            lambda a: a.update({"Status": "host-pass"}),
            lambda a: a["Proof"].update({"SameEngineContainerId": False}),
            lambda a: a["Sources"]["DockerPs"].update({"ContainerId": "different-container-id"}),
            lambda a: a["Sources"]["DockerPs"].update({"Running": False}),
            lambda a: a["Sources"]["DockerPs"].update({"ExactEngineContainerIdRequired": False}),
            lambda a: a["Sources"]["EngineApiContainersJson"].update({"CurrentContainerFound": False}),
            lambda a: a["Sources"]["EngineApiContainersJson"].update({"InspectStateRunning": False}),
            lambda a: a["Sources"]["PersistedStateJson"].update({"MatchesSelectedEngineContainerId": False}),
            lambda a: a["Sources"]["ProcessTable"].update({"SelectedPidPresent": False}),
            lambda a: a["Sources"]["ListenerProbe"].update({"Proven": False}),
            lambda a: a["Sources"]["ContainerLogs"].update({"Artifacts": []}),
            lambda a: a["Sources"]["ContainerLogs"].update({"CurrentServiceMarker": False}),
            lambda a: a["Sources"]["ContainerLogs"].update({"MarkerEngineContainerId": "f" * 64}),
            lambda a: a["Sources"]["ListenerProbe"].update({"Ports": [], "ProcNetTcpMatchedPorts": ""}),
            lambda a: a["Sources"]["ListenerProbe"].update({"Pid": 9999}),
            lambda a: a["Sources"]["ListenerProbe"].update({"OwnerEngineContainerId": a["Proof"]["EngineContainerId"][:12]}),
            lambda a: a["Sources"]["ListenerProbe"].update({"SelectedPidOwnsListener": False}),
            lambda a: a["Sources"]["UICard"].update({"TruthState": "stale"}),
            lambda a: a["Sources"]["UICard"].update({"ContainerIdSource": "state.json"}),
            lambda a: a["Sources"]["UICard"].update({"CurrentReason": ""}),
            lambda a: a["Proof"].update({"EngineContainerId": a["Proof"]["EngineContainerId"][:12]}),
        ]
        for mutate in cases:
            with self.subTest(mutate=mutate):
                artifact = passing_artifact()
                artifact["Sources"]["UICard"]["TruthState"] = "current"
                mutate(artifact)
                with self.assertRaises(AssertionError):
                    validate_same_container_id_contract(artifact)

    def test_complete_same_container_id_artifact_is_the_only_success_shape(self):
        artifact = passing_artifact()
        artifact["Sources"]["UICard"]["TruthState"] = "current"
        validate_same_container_id_contract(artifact)
        validate_device_pass_contract(artifact)

    def test_planned_gap_is_not_accepted_by_device_pass_contract(self):
        artifact = passing_artifact()
        artifact["Status"] = "planned-gap"
        artifact["Success"] = False
        validate_same_container_id_contract(artifact)
        with self.assertRaises(AssertionError):
            validate_device_pass_contract(artifact)

    def test_reducer_summary_must_bind_all_truth_sources_to_one_engine_id(self):
        for mutate in [
            lambda a: a.pop("VerifierReduction"),
            lambda a: a["VerifierReduction"]["SourceContainerIds"].update({"UICard": "f" * 64}),
            lambda a: a["VerifierReduction"].update({"DockerPsSameContainerId": False}),
            lambda a: a["VerifierReduction"].update({"MismatchedSources": ["/containers/json"]}),
        ]:
            with self.subTest(mutate=mutate):
                artifact = passing_artifact()
                mutate(artifact)
                with self.assertRaises(AssertionError):
                    validate_device_pass_contract(artifact)

    def test_device_smoke_entrypoint_only_passes_with_complete_same_id_proof(self):
        smoke = SMOKE.read_text()
        body = smoke.split("service_truth_acceptance_entrypoint()", 1)[1].split(
            "runtime_teardown_acceptance_entrypoint()", 1
        )[0]
        self.assertIn('SERVICE_TRUTH_STATUS="planned-gap"', body)
        self.assertIn('SERVICE_TRUTH_STATUS="device-pass"', body)
        self.assertIn('SERVICE_TRUTH_SUCCESS=true', body)
        self.assertIn('SERVICE_TRUTH_EXIT=0', body)
        self.assertIn('SERVICE_TRUTH_EXIT=2', body)
        self.assertIn('exit "$SERVICE_TRUTH_EXIT"', body)
        self.assertIn("docker ps -a --no-trunc", body)
        self.assertIn("docker ps -q --no-trunc", body)
        for source in [s for s in REQUIRED_SOURCES if s != "DockerPs"]:
            self.assertIn(source, body)
        self.assertIn('"DockerPs"', body)
        self.assertIn('SAME_ENGINE_CONTAINER_ID=true', body)
        self.assertIn('"SameEngineContainerId": $(json_bool "$SAME_ENGINE_CONTAINER_ID")', body)
        self.assertIn('"VerifierReduction": {', body)
        self.assertIn('"ReducedEngineContainerId": $( [ "$SELECTED_ID_EXACT" = true ]', body)
        self.assertIn('"SourceContainerIds": {', body)
        self.assertIn('"RequiredSources": ["UICard", "DockerPs", "EngineApiContainersJson", "PersistedStateJson", "ProcessTable", "ListenerProbe", "ContainerLogs"]', body)
        self.assertIn('"UICardSameContainerId": $(json_bool "$UI_CARD_SAME_CONTAINER_ID")', body)
        self.assertIn('"DockerPsSameContainerId": $(json_bool "$DOCKER_PS_SAME_CONTAINER_ID")', body)
        self.assertIn('"EngineApiContainersJsonSameContainerId": $(json_bool "$ENGINE_API_CONTAINERS_JSON_SAME_CONTAINER_ID")', body)
        self.assertIn('"PersistedStateJsonSameContainerId": $(json_bool "$PERSISTED_STATE_JSON_SAME_CONTAINER_ID")', body)
        self.assertIn('"ProcessTableSameContainerId": $(json_bool "$PROCESS_TABLE_SAME_CONTAINER_ID")', body)
        self.assertIn('"ListenerOwnerSameContainerId": $(json_bool "$LISTENER_OWNER_SAME_CONTAINER_ID")', body)
        self.assertIn('"ContainerLogsSameContainerId": $(json_bool "$CONTAINER_LOGS_SAME_CONTAINER_ID")', body)
        self.assertIn('printf \'%s\' "$REDUCTION_MISSING_SOURCES" | tr -d', body)
        self.assertIn("$2 == id", body)
        self.assertNotIn("index(id,$2)==1", body)
        for term in [
            "engine-candidates.json",
            "state-id-comparison.json",
            "listener-probe.json",
            "listener-owner-map.json",
            "OwnerEngineContainerId",
            "SelectedPidOwnsListener",
            "ExactEngineContainerIdRequired",
            "same-id-source-summary.json",
            "inspect-selected.http",
            "docker-inspect-selected.out",
            "logs-selected.out",
            "CurrentServiceMarker",
            "MarkerEngineContainerId",
            "CurrentContainerFound",
            "InspectStateRunning",
            "SelectedPidPresent",
            "Running",
            "pdocker-service-truth-marker",
            "ui-rendered-service-truth-latest.json",
            "/proc/net/tcp",
            "missing or stale UI export is not success",
        ]:
            self.assertIn(term, body)

    def test_capture_wrapper_documents_adb_free_plan_and_real_device_command_path(self):
        capture = CAPTURE.read_text()
        doc = DOC.read_text()
        self.assertIn("android-service-truth-capture.sh --print-plan", doc)
        self.assertIn("android-service-truth-capture.sh --target <default-workspace|llama> --no-install", doc)
        for text in [capture, doc]:
            for term in [
                "--print-plan",
                "android-device-smoke.sh",
                "--service-truth",
                "UI card",
                "docker ps",
                "/containers/json?all=1",
                "state.json",
                "process table",
                "listener owner",
                "logs-selected.out",
                "logs-<container-id>.out",
                "same 64-hex",
                "planned-gap",
                "prefix-only",
                "configured-port-only",
            ]:
                self.assertIn(term, text)
        for term in [
            "command -v",
            "get-state",
            "adb executable not found",
            "no connected adb device is ready",
            "never manufactures",
            "never promotes",
            "exec",
        ]:
            self.assertIn(term, capture)
        # The wrapper is an execution/plan conduit only; pass promotion remains
        # inside android-device-smoke.sh's strict same-container-ID branch.
        self.assertNotIn('SERVICE_TRUTH_STATUS="device-pass"', capture)
        self.assertNotIn('SERVICE_TRUTH_SUCCESS=true', capture)

    def test_static_verifier_includes_service_truth_device_gate_doc(self):
        verifier = VERIFIER.read_text()
        self.assertIn("SERVICE_TRUTH_DEVICE_GATE.md", verifier)
        self.assertIn("GOAL_EXECUTION_QUEUE_20260513.md", verifier)
        self.assertIn("DockerPs", verifier)
        self.assertIn("docker ps", verifier)
        self.assertIn("ContainerLogs.CurrentServiceMarker", verifier)
        self.assertIn("DockerPs.Running must be true", verifier)
        self.assertIn("EngineApiContainersJson.InspectStateRunning must be true", verifier)
        self.assertIn("ProcessTable.SelectedPidPresent must be true", verifier)
        self.assertIn("ContainerLogs.MarkerEngineContainerId must exactly match", verifier)
        self.assertIn("must require an exact 64-hex Engine container ID", verifier)
        self.assertIn("Proof.EngineContainerId must be an exact 64-hex", verifier)
        self.assertIn("ListenerProbe must bind at least one configured/listening port", verifier)
        self.assertIn("ListenerProbe.OwnerEngineContainerId must be an exact 64-hex", verifier)
        self.assertIn("planned-gap/skip artifacts must set Success false", verifier)
        self.assertIn("successful service truth artifact must set Status device-pass", verifier)
        self.assertIn("VerifierReduction object is required", verifier)
        self.assertIn("VerifierReduction.ReducedEngineContainerId must exactly match", verifier)
        self.assertIn("VerifierReduction.SourceContainerIds object is required", verifier)

    def test_static_verifier_fixture_rejects_missing_same_id_edges(self):
        verify_service_truth_plan.validate_service_truth_fixture_contract()
        artifact = passing_artifact()
        artifact["Sources"]["PersistedStateJson"]["Artifacts"] = [
            "files/pdocker/diagnostics/service-truth/state.json"
        ]
        with self.assertRaises(AssertionError):
            validate_same_container_id_contract(artifact)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs/test/SERVICE_TRUTH_DEVICE_GATE.md"
SMOKE = ROOT / "scripts/android-device-smoke.sh"
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


def passing_artifact() -> dict:
    return deepcopy(verify_service_truth_plan.build_success_fixture())


class ServiceTruthDeviceGateTest(unittest.TestCase):
    def test_service_truth_device_gate_doc_defines_schema_and_non_success_gap(self):
        text = DOC.read_text()
        for term in [
            "Status: planned-gap",
            "Success: false",
            "files/pdocker/diagnostics/service-truth-latest.json",
            "docs/test/service-truth-latest.json",
            "TruthContract",
            "RequiredSameContainerId",
            "Proof.SameEngineContainerId",
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
            "inspect-selected.http",
            "logs-selected.out",
            "exact ID match",
            "prefix-only matches are not enough",
            "unknown",
            "stale",
            "ambiguous",
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

    def test_fake_success_is_rejected_for_missing_mismatched_or_stale_sources(self):
        cases = [
            lambda a: a["TruthContract"].update({"RequiredSameContainerId": REQUIRED_SOURCES[:-1]}),
            lambda a: a["Proof"].update({"SameEngineContainerId": False}),
            lambda a: a["Sources"]["DockerPs"].update({"ContainerId": "different-container-id"}),
            lambda a: a["Sources"]["ListenerProbe"].update({"Proven": False}),
            lambda a: a["Sources"]["ContainerLogs"].update({"Artifacts": []}),
            lambda a: a["Sources"]["ContainerLogs"].update({"CurrentServiceMarker": False}),
            lambda a: a["Sources"]["ListenerProbe"].update({"Ports": [], "ProcNetTcpMatchedPorts": ""}),
            lambda a: a["Sources"]["ListenerProbe"].update({"Pid": 9999}),
            lambda a: a["Sources"]["UICard"].update({"TruthState": "stale"}),
            lambda a: a["Sources"]["UICard"].update({"ContainerIdSource": "state.json"}),
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

    def test_existing_device_smoke_entrypoint_stays_non_passing_planned_gap(self):
        smoke = SMOKE.read_text()
        body = smoke.split("service_truth_acceptance_entrypoint()", 1)[1].split(
            "runtime_teardown_acceptance_entrypoint()", 1
        )[0]
        self.assertIn('"Status": "planned-gap"', body)
        self.assertIn('"Success": false', body)
        self.assertIn("exit 2", body)
        self.assertIn("docker ps -a --no-trunc", body)
        self.assertIn("docker ps -q --no-trunc", body)
        for source in [s for s in REQUIRED_SOURCES if s != "DockerPs"]:
            self.assertIn(source, body)
        self.assertIn('"DockerPs"', body)
        self.assertIn('"SameEngineContainerId": false', body)
        self.assertIn("$2 == id", body)
        self.assertNotIn("index(id,$2)==1", body)
        for term in [
            "engine-candidates.json",
            "state-id-comparison.json",
            "listener-probe.json",
            "listener-owner-map.json",
            "same-id-source-summary.json",
            "inspect-selected.http",
            "docker-inspect-selected.out",
            "logs-selected.out",
            "CurrentServiceMarker",
            "pdocker-service-truth-marker",
            "ui-rendered-service-truth-latest.json",
            "/proc/net/tcp",
            "missing or stale UI export is not success",
        ]:
            self.assertIn(term, body)

    def test_static_verifier_includes_service_truth_device_gate_doc(self):
        verifier = VERIFIER.read_text()
        self.assertIn("SERVICE_TRUTH_DEVICE_GATE.md", verifier)
        self.assertIn("GOAL_EXECUTION_QUEUE_20260513.md", verifier)
        self.assertIn("DockerPs", verifier)
        self.assertIn("docker ps", verifier)
        self.assertIn("ContainerLogs.CurrentServiceMarker", verifier)
        self.assertIn("Proof.EngineContainerId must be an exact 64-hex", verifier)
        self.assertIn("ListenerProbe must bind at least one configured/listening port", verifier)

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

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs/test/SERVICE_TRUTH_DEVICE_GATE.md"
SMOKE = ROOT / "scripts/android-device-smoke.sh"
VERIFIER = ROOT / "scripts/verify-service-truth-plan.py"

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
    """Small host-side contract for future device artifacts.

    This deliberately rejects fake success.  Planned device work may collect
    evidence, but planned-gap artifacts are never successful; future success
    must prove exact same Engine container ID across every independent source.
    """

    assert artifact.get("SchemaVersion") == 1
    assert artifact.get("Kind") == "service-truth"
    if artifact.get("Status") == "planned-gap":
        assert artifact.get("Success") is False
        return

    assert artifact.get("Success") is True
    proof = artifact.get("Proof") or {}
    expected = proof.get("EngineContainerId")
    assert isinstance(expected, str) and len(expected) >= 12
    assert proof.get("SameEngineContainerId") is True

    contract_sources = artifact.get("TruthContract", {}).get("RequiredSameContainerId")
    assert set(REQUIRED_SOURCES).issubset(set(contract_sources or []))

    sources = artifact.get("Sources") or {}
    for source_name in REQUIRED_SOURCES:
        source = sources.get(source_name)
        assert isinstance(source, dict), source_name
        assert source.get("Proven") is True, source_name
        assert source.get("ContainerId") == expected, source_name
        artifacts = source.get("Artifacts")
        assert isinstance(artifacts, list) and artifacts, source_name

    assert sources["UICard"].get("TruthState") == "current"


def passing_artifact() -> dict:
    cid = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    return {
        "SchemaVersion": 1,
        "Kind": "service-truth",
        "Status": "device-pass",
        "Success": True,
        "TruthContract": {"RequiredSameContainerId": REQUIRED_SOURCES},
        "Proof": {"EngineContainerId": cid, "SameEngineContainerId": True},
        "Sources": {
            name: {
                "ContainerId": cid,
                "Proven": True,
                "Artifacts": [f"files/pdocker/diagnostics/service-truth/{name}.json"],
            }
            for name in REQUIRED_SOURCES
        },
    }


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
            "docker ps --no-trunc",
            "/containers/json?all=1",
            "state.json",
            "process-table",
            "/proc/net/tcp",
            "logs-<container-id>.out",
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
            lambda a: a["Sources"]["UICard"].update({"TruthState": "stale"}),
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
        for term in [
            "engine-candidates.json",
            "state-id-comparison.json",
            "listener-probe.json",
            "ui-rendered-service-truth-latest.json",
            "/proc/net/tcp",
            "missing or stale UI export is not success",
        ]:
            self.assertIn(term, body)

    def test_static_verifier_includes_service_truth_device_gate_doc(self):
        verifier = VERIFIER.read_text()
        self.assertIn("SERVICE_TRUTH_DEVICE_GATE.md", verifier)
        self.assertIn("DockerPs", verifier)
        self.assertIn("docker ps", verifier)


if __name__ == "__main__":
    unittest.main()

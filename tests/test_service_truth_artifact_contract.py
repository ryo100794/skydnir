from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / "scripts/android-device-smoke.sh"
VERIFIER = ROOT / "scripts/verify-service-truth-plan.py"
COMPAT = ROOT / "docs/test/COMPATIBILITY.md"


def test_service_truth_device_artifact_pass_branch_requires_same_id_proof():
    smoke = SMOKE.read_text()
    body = smoke.split("service_truth_acceptance_entrypoint()", 1)[1].split(
        "runtime_teardown_acceptance_entrypoint()", 1
    )[0]
    assert 'SERVICE_TRUTH_STATUS="planned-gap"' in body
    assert 'SERVICE_TRUTH_STATUS="device-pass"' in body
    assert 'SERVICE_TRUTH_SUCCESS=true' in body
    assert 'SERVICE_TRUTH_EXIT=0' in body
    assert 'SERVICE_TRUTH_EXIT=2' in body
    assert "TruthContract" in body
    assert "RequiredSameContainerId" in body
    for source in [
        "UICard",
        "EngineApiContainersJson",
        "PersistedStateJson",
        "ProcessTable",
        "ListenerProbe",
        "ContainerLogs",
    ]:
        assert source in body
        assert f'"{source}":' in body or source in body.split("RequiredSameContainerId", 1)[1]
    for term in [
        "CandidateSelection",
        "engine-candidates.json",
        "state-id-comparison.json",
        "listener-probe.json",
        "SelectedEngineContainerId",
        "OwnerEngineContainerId",
        "SelectedPidOwnsListener",
        "ui-rendered-service-truth-latest.json",
        "ContainerIdSource",
        "TruthState",
        "/proc/net/tcp",
    ]:
        assert term in body
    assert "fake success" not in body.lower()
    assert "missing or stale UI export is not success" in body


def test_static_verifier_and_docs_require_concrete_service_truth_schema():
    verifier = VERIFIER.read_text()
    compat = COMPAT.read_text()
    for term in [
        "service_truth_acceptance_entrypoint",
        "TruthContract",
        "RequiredSameContainerId",
        "ListenerProbe",
        "CandidateSelection",
        "engine-candidates.json",
        "state-id-comparison.json",
        "listener-probe.json",
        "ui-rendered-service-truth-latest.json",
    ]:
        assert term in verifier
    for term in [
        "files/pdocker/diagnostics/service-truth-latest.json",
        "files/pdocker/diagnostics/ui-rendered-service-truth-latest.json",
        "Status: planned-gap",
        "Success: false",
        "RequiredSameContainerId",
        "fake success",
        "engine-candidates.json",
        "state-id-comparison.json",
        "listener-probe.json",
        "TruthState",
        "unknown",
        "stale",
        "/proc/net/tcp",
    ]:
        assert term in compat

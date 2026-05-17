from __future__ import annotations

from copy import deepcopy
import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / "scripts" / "android-device-smoke.sh"
DOC = ROOT / "docs" / "test" / "RUNTIME_TEARDOWN_DEVICE_GATE.md"
VERIFIER = ROOT / "scripts" / "verify-service-truth-plan.py"

_spec = importlib.util.spec_from_file_location("verify_service_truth_plan", VERIFIER)
assert _spec and _spec.loader
verify_service_truth_plan = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(verify_service_truth_plan)


def _shell_function_body(source: str, name: str) -> str:
    start = source.index(f"{name}() {{")
    depth = 0
    for idx in range(start, len(source)):
        ch = source[idx]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return source[start : idx + 1]
    raise AssertionError(f"function not closed: {name}")


def runtime_teardown_fixture():
    artifact, proofs, negatives = verify_service_truth_plan.build_runtime_teardown_success_fixture()
    return deepcopy(artifact), deepcopy(proofs), deepcopy(negatives)


def validate_runtime_teardown_contract(artifact: dict, proofs: dict | None = None, negatives: dict | None = None) -> None:
    try:
        verify_service_truth_plan.validate_runtime_teardown_artifact(artifact, proofs, negatives)
    except ValueError as exc:
        raise AssertionError(str(exc)) from exc


class RuntimeTeardownDeviceGateTest(unittest.TestCase):
    def setUp(self):
        self.smoke = SMOKE.read_text()
        self.body = _shell_function_body(self.smoke, "runtime_teardown_acceptance_entrypoint")

    def test_runtime_teardown_mode_is_structured_non_passing_scaffold(self):
        self.assertIn("--runtime-teardown TARGET", self.smoke)
        self.assertIn('"Kind": "runtime-teardown"', self.body)
        self.assertIn('"Status": "planned-gap"', self.body)
        self.assertIn('"Success": false', self.body)
        self.assertIn("HTTP/CLI acknowledgement is recorded but not accepted as proof", self.body)
        self.assertIn("fake success", self.body.lower())
        self.assertNotIn('"Success": true', self.body)

    def test_runtime_teardown_collects_required_evidence_sources(self):
        for required in [
            "snapshot_ps",
            "snapshot_listeners",
            "snapshot_executor_residue",
            "snapshot_state_json",
            "write_pid_evidence",
            "write_process_tree_evidence",
            "write_name_residue_evidence",
            "write_same_id_evidence",
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
            "PdockerTeardown",
            "NoOrphanProcesses",
            "Survivors",
        ]:
            self.assertIn(required, self.body)

    def test_runtime_teardown_records_stop_kill_rm_paths_for_same_container_id(self):
        for required in [
            "STOP_CID",
            "KILL_CID",
            "create-stop.out",
            "create-kill.out",
            "stop-inspect-before.http",
            "stop-inspect-after.http",
            "stop-inspect-after-rm.http",
            "kill-inspect-before.http",
            "kill-inspect-after.http",
            "kill-inspect-after-rm.http",
            "same-container-id-stop-rm.json",
            "same-container-id-kill-rm.json",
            "RequiredSameContainerId",
            "StalePidAbsence",
            "StaleNameAbsence",
        ]:
            self.assertIn(required, self.body)

    def test_runtime_teardown_same_container_artifact_schema_is_explicit(self):
        for required in [
            '"Name": "same-container-id-teardown-artifact"',
            '"Kind": "same-container-id-teardown-proof"',
            '"SchemaVersion": 2',
            '"SameContainerIdTeardownArtifacts"',
            '"BeforeAfterEvidence"',
            '"ProcessTreeBeforeAfter"',
            '"DirectChildAbsence"',
            '"ListenerAbsenceBeforeAfter"',
            '"GpuMediaExecutorResidueBeforeAfter"',
            '"PersistedStateJsonBeforeAfter"',
            '"AfterStart"',
            '"AfterOperation"',
            '"AfterRemove"',
            '"SuccessInvariant"',
            '"VerifierReduction"',
            "json_id_field_equals",
            '"GapReasons"',
            '"FailReasons"',
            '"ReductionArtifacts"',
            '"MismatchedContainerIds"',
            '"Survivors"',
            '$label-gap-reasons.txt',
            '$label-fail-reasons.txt',
        ]:
            self.assertIn(required, self.body)

    def test_runtime_teardown_reduces_stale_name_after_container_list_snapshot(self):
        ordered = [
            "http_get engine-containers-after '/containers/json?all=1'",
            "write_name_residue_evidence stop-stale-name-after-rm",
            "write_name_residue_evidence kill-stale-name-after-rm",
            "write_same_id_evidence same-container-id-stop-rm",
            "write_same_id_evidence same-container-id-kill-rm",
        ]
        positions = [self.body.index(token) for token in ordered]
        self.assertEqual(positions, sorted(positions))
        for required in [
            "engine-containers-after.http",
            '"EngineContainersAfterIdAbsent"',
            "artifact_contains \"$cid\" \"$containers_after\"",
            "stop-stale-name-after-rm.json",
            "kill-stale-name-after-rm.json",
            '"Kind": "runtime-teardown-stale-name-proof"',
            '"NameStillPresentAfterRemove"',
            '"StaleNameAbsence"',
            '"StaleName"',
        ]:
            self.assertIn(required, self.body)

    def test_runtime_teardown_collects_listener_and_executor_absence_evidence(self):
        for required in [
            "/proc/net/tcp",
            "/proc/net/tcp6",
            "ss -ltnp",
            "netstat -ltnp",
            "listeners-before.txt",
            "listeners-after-stop-start.txt",
            "listeners-after-stop.txt",
            "listeners-after-rm-stopped.txt",
            "listeners-after-kill-start.txt",
            "listeners-after-kill.txt",
            "listeners-after-rm-killed.txt",
            "executor-residue-before.txt",
            "executor-residue-after-stop-start.txt",
            "executor-residue-after-rm-stopped.txt",
            "executor-residue-after-kill-start.txt",
            "executor-residue-after-rm-killed.txt",
            "pdocker.*(gpu|media|camera|audio|vulkan|executor)",
        ]:
            self.assertIn(required, self.body)

    def test_runtime_teardown_requires_direct_child_and_stale_name_absence(self):
        for required in [
            '"Kind": "runtime-teardown-process-tree-proof"',
            '"DirectChildrenArtifact"',
            '"DirectChildrenPresent"',
            "stop-process-tree-after-stop.json",
            "stop-process-tree-after-rm.json",
            "kill-process-tree-after-kill.json",
            "kill-process-tree-after-rm.json",
            "stop-process-tree-after-stop-direct-children.txt",
            "kill-process-tree-after-kill-direct-children.txt",
            '"Kind": "runtime-teardown-stale-name-proof"',
            '"NameStillPresentAfterRemove"',
            "stop-stale-name-after-rm.json",
            "kill-stale-name-after-rm.json",
            "duplicate-name",
            "previous-container-log",
            "no live State.Pid and no direct children",
        ]:
            self.assertIn(required, self.body)

    def test_runtime_teardown_device_pass_is_adb_gated(self):
        for required in [
            '"DeviceGate"',
            '"RequiresAdb": true',
            '"CollectedViaAdbRunAs": true',
            '"HostStaticVerifierCannotPromote": true',
            '"DoNotClaimDevicePassWithoutAdb": true',
            "adb device serial and package run-as context",
            "no pdocker-direct, service child, listener, GPU executor, media executor",
        ]:
            self.assertIn(required, self.body)

    def test_runtime_teardown_collects_full_evidence_directory_for_reducer(self):
        self.assertIn('collect_device_dir "files/pdocker/diagnostics/runtime-teardown" "runtime-teardown"', self.smoke)
        self.assertIn('collect_device_file "files/pdocker/diagnostics/runtime-teardown-latest.json"', self.smoke)
        self.assertIn("tar cf -", self.smoke)

    def test_runtime_teardown_negative_cases_are_host_detectable(self):
        for required in [
            "write_negative_case_evidence",
            '"Kind": "runtime-teardown-negative-case"',
            '"ExpectedAccepted": false',
            '"NegativeCases"',
            "negative-http-204-only.json",
            "negative-cli-exit-zero-only.json",
            "negative-name-only.json",
            "negative-stale-state-json.json",
            "negative-listener-only.json",
            "negative-process-only.json",
            "negative-previous-container-logs.json",
            "negative-wrong-container-id.json",
            "HTTP 204 or Engine API acknowledgement alone",
            "CLI exit 0 alone",
            "matching container name without same Engine container ID",
            "stale state.json still names a removed container",
            "listener absence without process-tree and stale-PID proof",
            "clean process table without Engine inspect and logs",
            "previous-container logs or reused names",
            "mixed evidence from a different container ID",
        ]:
            self.assertIn(required, self.body)

    def test_runtime_teardown_planned_gap_is_never_success_even_with_evidence(self):
        artifact, proofs, negatives = runtime_teardown_fixture()
        artifact["Status"] = "planned-gap"
        artifact["Success"] = True
        with self.assertRaises(AssertionError):
            validate_runtime_teardown_contract(artifact, proofs, negatives)

        artifact["Success"] = False
        validate_runtime_teardown_contract(artifact, proofs, negatives)

        artifact, proofs, negatives = runtime_teardown_fixture()
        artifact["Status"] = "skip"
        artifact["Success"] = True
        with self.assertRaises(AssertionError):
            validate_runtime_teardown_contract(artifact, proofs, negatives)

        artifact["Success"] = False
        validate_runtime_teardown_contract(artifact, proofs, negatives)

    def test_runtime_teardown_success_requires_same_container_id_proofs(self):
        artifact, proofs, negatives = runtime_teardown_fixture()
        validate_runtime_teardown_contract(artifact, proofs, negatives)

        with self.assertRaises(AssertionError):
            validate_runtime_teardown_contract(artifact, None, negatives)

        bad = deepcopy(proofs)
        bad["same-container-id-stop-rm"]["ContainerId"] = artifact["ContainerIds"]["StopRm"][:12]
        with self.assertRaises(AssertionError):
            validate_runtime_teardown_contract(artifact, bad, negatives)

        bad = deepcopy(proofs)
        bad["same-container-id-kill-rm"]["Operation"] = "stop-rm"
        with self.assertRaises(AssertionError):
            validate_runtime_teardown_contract(artifact, bad, negatives)

    def test_runtime_teardown_fake_success_rejects_http_or_cli_only(self):
        artifact, proofs, negatives = runtime_teardown_fixture()
        for mutate in [
            lambda a, p, n: a["Evidence"].pop("ProcessTree"),
            lambda a, p, n: a["Evidence"].pop("EngineApiInspect"),
            lambda a, p, n: p["same-container-id-stop-rm"]["Evidence"].pop("ProcessTreeBeforeAfter"),
            lambda a, p, n: p["same-container-id-stop-rm"]["VerifierReduction"].update({"ProcessTreeClear": False}),
            lambda a, p, n: p["same-container-id-stop-rm"]["VerifierReduction"].update({"ContainerLogsBound": False}),
            lambda a, p, n: p["same-container-id-kill-rm"].update({"Success": False}),
        ]:
            with self.subTest(mutate=mutate):
                candidate = deepcopy(artifact)
                proof_candidate = deepcopy(proofs)
                negative_candidate = deepcopy(negatives)
                mutate(candidate, proof_candidate, negative_candidate)
                with self.assertRaises(AssertionError):
                    validate_runtime_teardown_contract(candidate, proof_candidate, negative_candidate)

    def test_runtime_teardown_fake_success_rejects_missing_absence_sources(self):
        artifact, proofs, negatives = runtime_teardown_fixture()
        for mutate in [
            lambda a, p, n: a["Evidence"].pop("DirectChildAbsence"),
            lambda a, p, n: a["Evidence"].pop("ListenerAbsence"),
            lambda a, p, n: a["Evidence"].pop("StalePid"),
            lambda a, p, n: a["Evidence"].pop("StaleName"),
            lambda a, p, n: a["Evidence"].pop("GpuMediaExecutorResidue"),
            lambda a, p, n: p["same-container-id-stop-rm"]["BeforeAfterEvidence"].pop("DirectChildAbsence"),
            lambda a, p, n: p["same-container-id-kill-rm"]["VerifierReduction"].update({"ListenerAbsence": False}),
            lambda a, p, n: p["same-container-id-kill-rm"]["VerifierReduction"].update({"GpuMediaExecutorResidueAbsence": False}),
            lambda a, p, n: p["same-container-id-kill-rm"]["VerifierReduction"].update({"Survivors": ["listener 18080"]}),
        ]:
            with self.subTest(mutate=mutate):
                candidate = deepcopy(artifact)
                proof_candidate = deepcopy(proofs)
                negative_candidate = deepcopy(negatives)
                mutate(candidate, proof_candidate, negative_candidate)
                with self.assertRaises(AssertionError):
                    validate_runtime_teardown_contract(candidate, proof_candidate, negative_candidate)

    def test_runtime_teardown_negative_cases_must_remain_non_success(self):
        artifact, proofs, negatives = runtime_teardown_fixture()
        validate_runtime_teardown_contract(artifact, proofs, negatives)
        for name in verify_service_truth_plan.TEARDOWN_REQUIRED_NEGATIVE_CASES:
            with self.subTest(name=name):
                candidate = deepcopy(negatives)
                candidate[name]["Success"] = True
                with self.assertRaises(AssertionError):
                    validate_runtime_teardown_contract(artifact, proofs, candidate)

        candidate = deepcopy(negatives)
        candidate["negative-wrong-container-id"]["ExpectedAccepted"] = True
        with self.assertRaises(AssertionError):
            validate_runtime_teardown_contract(artifact, proofs, candidate)

    def test_static_verifier_self_checks_runtime_teardown_fixture_contract(self):
        verify_service_truth_plan.validate_runtime_teardown_fixture_contract()

    def test_runtime_teardown_device_gate_doc_matches_artifact_contract(self):
        doc = DOC.read_text()
        for required in [
            "runtime-teardown-latest.json",
            "same-container-id-teardown-artifact",
            "same-container-id-teardown-proof",
            "Status: planned-gap",
            "Success: false",
            "same Engine container ID",
            "process tree",
            "listener absence",
            "stale PID",
            "GPU/media executor residue",
            "PdockerTeardown",
            "NoOrphanProcesses",
            "Engine inspect",
            "container logs",
            "HTTP 204",
            "CLI exit 0",
            "negative-http-204-only.json",
            "negative-wrong-container-id.json",
            "not sufficient",
        ]:
            self.assertIn(required, doc)


if __name__ == "__main__":
    unittest.main()

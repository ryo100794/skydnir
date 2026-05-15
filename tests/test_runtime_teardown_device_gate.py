import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / "scripts" / "android-device-smoke.sh"
DOC = ROOT / "docs" / "test" / "RUNTIME_TEARDOWN_DEVICE_GATE.md"


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

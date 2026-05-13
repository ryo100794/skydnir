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
            "write_pid_evidence",
            "write_same_id_evidence",
            "EngineApiContainersJson",
            "EngineApiInspect",
            "ProcessTable",
            "ProcessTree",
            "ListenerAbsence",
            "StalePid",
            "GpuMediaExecutorResidue",
            "SameContainerId",
            "PersistedStateJson",
            "LifecycleLogs",
            "ContainerLogs",
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
        ]:
            self.assertIn(required, self.body)

    def test_runtime_teardown_collects_listener_and_executor_absence_evidence(self):
        for required in [
            "/proc/net/tcp",
            "/proc/net/tcp6",
            "ss -ltnp",
            "netstat -ltnp",
            "listeners-before.txt",
            "listeners-after-stop.txt",
            "listeners-after-rm-stopped.txt",
            "listeners-after-kill.txt",
            "listeners-after-rm-killed.txt",
            "executor-residue-before.txt",
            "executor-residue-after-rm-stopped.txt",
            "executor-residue-after-rm-killed.txt",
            "pdocker.*(gpu|media|camera|audio|vulkan|executor)",
        ]:
            self.assertIn(required, self.body)

    def test_runtime_teardown_device_gate_doc_matches_artifact_contract(self):
        doc = DOC.read_text()
        for required in [
            "runtime-teardown-latest.json",
            "Status: planned-gap",
            "Success: false",
            "same Engine container ID",
            "process tree",
            "listener absence",
            "stale PID",
            "GPU/media executor residue",
            "Engine inspect",
            "container logs",
            "HTTP 204",
            "CLI exit 0",
            "not sufficient",
        ]:
            self.assertIn(required, doc)


if __name__ == "__main__":
    unittest.main()

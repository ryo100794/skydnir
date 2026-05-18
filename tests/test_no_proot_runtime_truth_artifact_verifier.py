import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERIFIER_PATH = ROOT / "scripts" / "verify-no-proot-runtime-truth-artifact.py"

spec = importlib.util.spec_from_file_location("no_proot_runtime_truth_verifier", VERIFIER_PATH)
verifier = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(verifier)


def good_artifact():
    diagnostic = (
        "pdocker runtime error: the configured no-PRoot/direct Android executor "
        "is installed, but it does not advertise process-exec=1 yet"
    )
    return {
        "schema": "pdocker.android.no-proot-runtime-truth.v1",
        "status": "planned-gap",
        "success": False,
        "promotion": "non-promoting runtime truth gate",
        "direct_executor_probe": {
            "probe_ok": True,
            "process_exec": False,
            "advertises_process_exec_1": False,
            "output": "pdocker-direct-executor:1\nprocess-exec=0\n",
            "diagnostic": diagnostic,
        },
        "operations": {
            "docker_run": {
                "attempted": True,
                "success": False,
                "exit_code": 126,
                "capability_error": True,
                "forbidden_success_claim": False,
                "diagnostic": diagnostic,
            },
            "docker_exec": {
                "attempted": True,
                "success": False,
                "exit_code": 126,
                "capability_error": True,
                "forbidden_success_claim": False,
                "diagnostic": diagnostic,
            },
            "dockerfile_run": {
                "attempted": True,
                "success": False,
                "exit_code": 1,
                "capability_error": True,
                "forbidden_success_claim": False,
                "diagnostic": "RUN requires a real container process executor; build stopped without recording a fake layer",
            },
        },
        "health": {
            "final_status": "unhealthy",
            "running": False,
            "cannot_become_healthy": True,
            "has_healthy_claim": False,
            "diagnostic": diagnostic,
        },
        "ports": {
            "active_count": 0,
            "planned_or_inactive_only": True,
            "summary": {"Active": 0, "Planned": 1, "Inactive": 1, "Conflict": 0},
            "status_cases": [
                {"case": "failed-start", "State": "planned", "Active": False},
                {"case": "metadata-only-running-control", "State": "inactive", "Active": False},
            ],
        },
        "evidence": {
            "mode": "host-contract-probe",
            "script": "scripts/android-no-proot-runtime-truth-gate.sh",
        },
    }


class NoProotRuntimeTruthArtifactVerifierTest(unittest.TestCase):
    def write_case(self, artifact):
        tmp = tempfile.TemporaryDirectory()
        path = Path(tmp.name) / "no-proot-runtime-truth-latest.json"
        path.write_text(json.dumps(artifact), encoding="utf-8")
        return tmp, path

    def assert_rejected(self, artifact, pattern):
        tmp, path = self.write_case(artifact)
        with tmp:
            with self.assertRaisesRegex(verifier.VerificationError, pattern):
                verifier.verify(path)

    def test_accepts_fail_closed_truth_artifact(self):
        tmp, path = self.write_case(good_artifact())
        with tmp:
            self.assertEqual(verifier.verify(path)["status"], "planned-gap")

    def test_rejects_fake_promoting_success(self):
        artifact = good_artifact()
        artifact["status"] = "pass"
        artifact["success"] = True
        self.assert_rejected(artifact, "status must be one of")

    def test_rejects_process_exec_advertisement(self):
        artifact = good_artifact()
        artifact["direct_executor_probe"]["process_exec"] = True
        artifact["direct_executor_probe"]["advertises_process_exec_1"] = True
        artifact["direct_executor_probe"]["output"] = "pdocker-direct-executor:1\nprocess-exec=1\n"
        self.assert_rejected(artifact, "process_exec must be false")

    def test_rejects_operation_success_without_capability_error(self):
        artifact = good_artifact()
        artifact["operations"]["docker_run"]["success"] = True
        artifact["operations"]["docker_run"]["capability_error"] = False
        self.assert_rejected(artifact, "operations.docker_run.success must be false")

    def test_rejects_missing_dockerfile_run_diagnostic(self):
        artifact = good_artifact()
        artifact["operations"]["dockerfile_run"]["diagnostic"] = "built layer sha256:fake"
        self.assert_rejected(artifact, "operations.dockerfile_run must include")

    def test_rejects_healthy_claim(self):
        artifact = good_artifact()
        artifact["health"]["final_status"] = "healthy"
        artifact["health"]["has_healthy_claim"] = True
        self.assert_rejected(artifact, "has_healthy_claim must be false")

    def test_rejects_active_port_claim(self):
        artifact = good_artifact()
        artifact["ports"] = copy.deepcopy(artifact["ports"])
        artifact["ports"]["active_count"] = 1
        artifact["ports"]["status_cases"][0]["State"] = "active"
        artifact["ports"]["status_cases"][0]["Active"] = True
        self.assert_rejected(artifact, "ports.active_count must be 0")

    def test_rejects_missing_evidence_script_reference(self):
        artifact = good_artifact()
        artifact["evidence"]["script"] = ""
        self.assert_rejected(artifact, "evidence must reference")


if __name__ == "__main__":
    unittest.main()

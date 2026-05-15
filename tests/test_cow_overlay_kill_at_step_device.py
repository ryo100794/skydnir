import importlib.machinery
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "verify" / "runner" / "cow_overlay_kill_at_step_device.py"
DEVICE_SIDE = ROOT / "scripts" / "verify" / "runner" / "cow-overlay-kill-at-step-device.sh"
DOC = ROOT / "docs" / "test" / "COW_OVERLAY_KILL_AT_STEP_DEVICE_GATE.md"
MANIFEST = ROOT / "tests" / "test_driver_manifest.json"
LEDGER = ROOT / "docs" / "test" / "CI_GATE_LEDGER.md"


def load_runner_module():
    name = "cow_overlay_kill_at_step_device_test"
    loader = importlib.machinery.SourceFileLoader(name, str(RUNNER))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class CowOverlayKillAtStepDeviceGateTest(unittest.TestCase):
    def run_runner(self, *args, check=False):
        return subprocess.run(
            [sys.executable, str(RUNNER), *args],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=check,
        )

    def synthetic_pass_artifact(self):
        mod = load_runner_module()
        cases = []
        for required in mod.REQUIRED_CASES:
            proof = {key: True for key in required["ProofKeys"]}
            if "MergedViewVerified" not in proof:
                proof["MergedViewVerified"] = True
            cases.append(
                {
                    "Id": required["Id"],
                    "Operation": required["Operation"],
                    "ProcessTarget": required["ProcessTarget"],
                    "Step": required["Step"],
                    "KillSignal": "TERM",
                    "Status": "pass",
                    "ExpectedRecovery": required["ExpectedRecovery"],
                    "FailureOracle": "matched post-restart merged-view oracle",
                    "OperationId": "op-" + required["Id"],
                    "KilledPid": 12345,
                    "KilledProcessName": "pdockerd" if required["ProcessTarget"] == "daemon" else "pdocker-cow-helper",
                    "CheckpointReached": True,
                    "KillDelivered": True,
                    "RestartCompleted": True,
                    "MergedViewVerified": True,
                    "FailureOracleMatched": True,
                    "Proof": proof,
                    "EvidenceFiles": ["device-evidence/" + required["Id"] + ".json"],
                }
            )
        return {
            "schema": mod.SCHEMA,
            "scenario_id": mod.SCENARIO_ID,
            "status": "pass",
            "success": True,
            "stable_checkpoint_eligible": True,
            "device_promotion_evidence": True,
            "requires_adb": True,
            "collected_via_adb_run_as": True,
            "host_static_verifier_cannot_promote": True,
            "device": {"adb_present": True, "state": "device", "serial": "unit-test-serial", "fingerprint": "unit/test"},
            "coverage": {operation: True for operation in mod.REQUIRED_OPERATION_COVERAGE},
            "kill_at_step_cases": cases,
        }

    def validate_artifact(self, artifact):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "artifact.json"
            path.write_text(json.dumps(artifact), encoding="utf-8")
            return self.run_runner("--validate-artifact", str(path))

    def test_planned_gap_without_adb_never_fakes_success(self):
        with tempfile.TemporaryDirectory() as td:
            artifact = Path(td) / "cow.json"
            result = self.run_runner("--adb", "__missing_adb_for_cow_unit_test__", "--artifact", str(artifact), check=True)
            data = json.loads(artifact.read_text())
        self.assertIn("status=planned-gap", result.stdout)
        self.assertEqual(data["schema"], "pdocker.cow-overlay-kill-at-step-device.v1")
        self.assertEqual(data["scenario_id"], "cow.overlay.external-daemon-helper-kill-at-step")
        self.assertEqual(data["status"], "planned-gap")
        self.assertFalse(data["success"])
        self.assertFalse(data["stable_checkpoint_eligible"])
        self.assertFalse(data["device_promotion_evidence"])
        self.assertTrue(data["requires_adb"])
        self.assertTrue(data["host_static_verifier_cannot_promote"])
        self.assertEqual({case["Status"] for case in data["kill_at_step_cases"]}, {"planned-gap"})
        self.assertEqual(
            {case["Operation"] for case in data["kill_at_step_cases"]},
            {"copy-up", "rename", "metadata", "whiteout", "hardlink-ring"},
        )
        self.assertIn("daemon", {case["ProcessTarget"] for case in data["kill_at_step_cases"]})
        self.assertIn("helper", {case["ProcessTarget"] for case in data["kill_at_step_cases"]})

    def test_execute_without_device_is_blocked_device_not_success(self):
        with tempfile.TemporaryDirectory() as td:
            artifact = Path(td) / "cow.json"
            result = self.run_runner(
                "--adb",
                "__missing_adb_for_cow_unit_test__",
                "--execute-device",
                "--artifact",
                str(artifact),
            )
            data = json.loads(artifact.read_text())
        self.assertEqual(result.returncode, 2)
        self.assertEqual(data["status"], "blocked-device")
        self.assertFalse(data["success"])
        self.assertFalse(data["device_promotion_evidence"])
        self.assertEqual({case["Status"] for case in data["kill_at_step_cases"]}, {"blocked-device"})

    def test_validator_accepts_complete_device_pass_evidence(self):
        result = self.validate_artifact(self.synthetic_pass_artifact())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_validator_rejects_pass_without_kill_delivery_or_case_proof(self):
        artifact = self.synthetic_pass_artifact()
        artifact["kill_at_step_cases"][0]["KillDelivered"] = False
        artifact["kill_at_step_cases"][0]["Proof"]["NoCowTempResidue"] = False
        result = self.validate_artifact(artifact)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("kill delivery", result.stderr)
        self.assertIn("NoCowTempResidue", result.stderr)

    def test_validator_rejects_non_passing_success_true(self):
        with tempfile.TemporaryDirectory() as td:
            artifact = Path(td) / "cow.json"
            self.run_runner("--adb", "__missing_adb_for_cow_unit_test__", "--artifact", str(artifact), check=True)
            data = json.loads(artifact.read_text())
            data["success"] = True
            artifact.write_text(json.dumps(data), encoding="utf-8")
            result = self.run_runner("--validate-artifact", str(artifact))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("non-passing artifacts must set success=false", result.stderr)

    def test_device_side_runner_is_fail_closed_and_exact_pid_only(self):
        text = DEVICE_SIDE.read_text()
        for required in [
            "copyup.before_publish_rename",
            "rename.before_destination_publish",
            "metadata.before_chmod_or_sidecar_publish",
            "whiteout.before_marker_publish",
            "hardlink_ring.before_cache_publish",
            "hardlink_ring.helper_rebuild_before_publish",
            "kill_exact_checkpoint_pid",
            "refusing non-numeric checkpoint pid",
            "missing files/pdocker/tools/pdocker-cow-kill-at-step",
            "planned-gap",
            '"success": false',
        ]:
            self.assertIn(str(required), text)
        self.assertNotIn("pkill", text)
        self.assertNotIn("killall", text)
        self.assertNotIn("rm -rf files/pdocker", text)
        self.assertNotIn("rm -rf /data", text)

    def test_docs_manifest_and_ledger_wire_non_promoting_device_gate(self):
        doc = DOC.read_text()
        for required in [
            "Status: planned-gap",
            "success=false",
            "stable_checkpoint_eligible=false",
            "copy_up.daemon_kill_before_publish",
            "rename.daemon_kill_before_destination_publish",
            "metadata.daemon_kill_before_metadata_publish",
            "whiteout.daemon_kill_before_marker_publish",
            "hardlink_ring.daemon_kill_during_cache_publish",
            "hardlink_ring.helper_kill_during_cache_rebuild",
            "HTTP/CLI acknowledgement",
            "not fabricate pass",
        ]:
            self.assertIn(required, doc)
        manifest = json.loads(MANIFEST.read_text())
        self.assertIn("android-cow-overlay-kill-at-step", manifest["lanes"])
        lane = manifest["lanes"]["android-cow-overlay-kill-at-step"]
        self.assertFalse(lane["stable_checkpoint_eligible"])
        self.assertIn("cow-overlay-kill-at-step-device", {cmd["id"] for cmd in lane["commands"]})
        ledger = LEDGER.read_text()
        self.assertIn("COW/overlay external kill-at-step", ledger)
        self.assertIn("docs/test/cow-overlay-kill-at-step-latest.json", ledger)


if __name__ == "__main__":
    unittest.main()

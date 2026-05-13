import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERIFY = ROOT / "scripts" / "verify-image-pull-crash-safety.py"
RUNNER = ROOT / "scripts" / "verify" / "runner" / "image_pull_crash_safety_device.py"
DEVICE_RUNNER = ROOT / "scripts" / "verify" / "runner" / "image-pull-crash-safety-device.sh"


class ImagePullCrashSafetyVerifierTest(unittest.TestCase):
    def test_static_verifier_passes(self):
        subprocess.run([sys.executable, str(VERIFY)], cwd=ROOT, check=True)

    def test_device_runner_writes_planned_gap_without_adb(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "artifact.json"
            subprocess.run(
                [
                    sys.executable,
                    str(RUNNER),
                    "--adb",
                    "__missing_adb_for_unit_test__",
                    "--artifact",
                    str(artifact),
                ],
                cwd=ROOT,
                check=True,
            )
            data = json.loads(artifact.read_text())

        self.assertEqual(data["scenario_id"], "image.pull.interrupted-kill-restart")
        self.assertEqual(data["schema_version"], 2)
        self.assertEqual(data["status"], "planned-gap")
        self.assertFalse(data["success"])
        self.assertIn("artifact_schema", data)
        self.assertEqual(data["phases"], ["prepare-residue", "kill-daemon", "restart-and-probe", "cleanup"])
        self.assertFalse(data["coverage"]["live_interrupted_network_pull"])
        self.assertIn("remaining_gap", data)
        self.assertIn("negative_expected_conditions", data)
        self.assertIn("cleanup_policy", data)
        self.assertGreaterEqual(len(data["commands"]), 8)
        joined_negative = "\n".join(data["negative_expected_conditions"])
        self.assertIn(".pull-", joined_negative)
        self.assertIn(".tmp-", joined_negative)
        self.assertIn("old tag", joined_negative)

    def test_device_runner_execute_without_device_is_blocked_not_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "artifact.json"
            result = subprocess.run(
                [
                    sys.executable,
                    str(RUNNER),
                    "--adb",
                    "__missing_adb_for_unit_test__",
                    "--artifact",
                    str(artifact),
                    "--execute-device",
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            data = json.loads(artifact.read_text())

        self.assertEqual(result.returncode, 2)
        self.assertEqual(data["status"], "blocked")
        self.assertFalse(data["success"])
        self.assertEqual(data["phase_results"], [])
        self.assertIsNone(data["assertions"]["old_tag_restored"])

    def test_device_side_runner_is_scenario_scoped(self):
        text = DEVICE_RUNNER.read_text()
        for marker in ["prepare-residue", "kill-daemon", "restart-and-probe", "cleanup"]:
            self.assertIn(marker, text)
        for marker in [".pull-$TOKEN", ".old-$TOKEN", ".tmp-$TOKEN", "inspect-restored.raw", "inspect-never.raw"]:
            self.assertIn(marker, text)
        self.assertIn("pkill -TERM -f pdockerd", text)
        self.assertIn("rm -rf \\", text)
        self.assertIn("$IMG_BASE", text)
        self.assertIn("$NEVER_BASE", text)
        self.assertIn("$TOKEN", text)
        for forbidden in [
            "rm -rf files/pdocker",
            "rm -rf pdocker/images",
            "rm -rf pdocker/layers",
            "rm -rf /data",
            "rm -rf /sdcard",
        ]:
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()

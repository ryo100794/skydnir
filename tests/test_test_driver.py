import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TestDriverManifestTest(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((ROOT / "tests" / "test_driver_manifest.json").read_text())
        self.driver = (ROOT / "scripts" / "pdocker-test-driver.py").read_text()

    def test_canonical_driver_and_manifest_are_declared(self):
        self.assertEqual(self.manifest["schema"], "pdocker.test-driver.v1")
        self.assertEqual(self.manifest["policy"]["canonical_driver"], "scripts/pdocker-test-driver.py")
        self.assertEqual(self.manifest["artifact_manifest"], "docs/test/test-run-latest.json")
        self.assertIn("def run_command", self.driver)
        self.assertIn("manifest.json", self.driver)

    def test_every_command_has_stable_id_and_executable_form(self):
        lanes = self.manifest["lanes"]
        self.assertIn("host-smoke", lanes)
        self.assertIn("android-test-suite", lanes)
        self.assertIn("android-file-io-microbench", lanes)
        ids = set()
        for lane_name, lane in lanes.items():
            self.assertGreater(len(lane.get("commands", [])), 0, lane_name)
            for command in lane["commands"]:
                cid = command.get("id")
                self.assertIsInstance(cid, str)
                self.assertNotIn(cid, ids)
                ids.add(cid)
                self.assertTrue(
                    ("argv" in command) ^ ("shell" in command),
                    f"{lane_name}/{cid} must use exactly one command form",
                )

    def test_artifact_management_is_single_manifest_based(self):
        policy = self.manifest["policy"]
        self.assertIn("one run manifest", policy["artifact_rule"])
        self.assertIn("sha256", self.driver)
        self.assertIn('"artifacts"', self.driver)
        self.assertNotIn("docs/test/*-latest.json", policy["artifact_rule"])

    def test_benchmark_lane_exports_documents_evidence(self):
        lane = self.manifest["lanes"]["android-file-io-microbench"]
        command = lane["commands"][0]
        self.assertEqual(command["id"], "android-file-io-microbench")
        self.assertIn("docs/test/file-io-microbench-latest.json", command["artifacts"])
        self.assertEqual(command["env"]["PDOCKER_FILE_IO_MICRO_EXPORT_DOCUMENTS"], "1")

    def test_device_artifact_lanes_chain_strict_verifiers(self):
        lanes = self.manifest["lanes"]
        dev_cmd = lanes["android-dev-workspace"]["commands"][0]
        self.assertIn("verify-dev-workspace-compose-artifact.py", dev_cmd["shell"])
        self.assertIn("rm -f docs/test/dev-workspace-compose-latest.json", dev_cmd["shell"])
        self.assertFalse(lanes["android-dev-workspace"]["stable_checkpoint_eligible"])

        docs_cmd = lanes["android-documents"]["commands"][0]
        self.assertIn("verify-saf-direct-output-artifact.py", docs_cmd["shell"])
        self.assertIn("verify-dev-workspace-compose-artifact.py", docs_cmd["shell"])
        self.assertIn("rm -f docs/test/saf-direct-output-latest.json", docs_cmd["shell"])
        self.assertFalse(lanes["android-documents"]["stable_checkpoint_eligible"])

    def test_focused_p0_device_lanes_are_non_promoting(self):
        lanes = self.manifest["lanes"]
        for lane_name in [
            "android-runtime-teardown",
            "android-storage-metrics-sequence",
            "android-single-container-echo-hi",
            "android-modern-runtime-truth",
        ]:
            self.assertIn(lane_name, lanes)
            self.assertFalse(lanes[lane_name]["stable_checkpoint_eligible"], lane_name)

        single_cmd = lanes["android-single-container-echo-hi"]["commands"][0]["shell"]
        self.assertIn("--single-container-echo-hi", single_cmd)
        self.assertNotIn("--quick", single_cmd)
        storage_cmd = lanes["android-storage-metrics-sequence"]["commands"][0]["shell"]
        self.assertIn("rm -f docs/test/storage-metrics-sequence-latest.json", storage_cmd)
        self.assertIn("exit 2", storage_cmd)

    def test_verify_heavy_exposes_focused_device_lanes(self):
        heavy = (ROOT / "scripts" / "verify-heavy.sh").read_text(encoding="utf-8")
        for mode in [
            "--android-dev-workspace",
            "--android-documents",
            "--android-runtime-teardown",
            "--android-storage-metrics-sequence",
            "--android-single-container",
            "--android-modern-runtime-truth",
        ]:
            self.assertIn(mode, heavy)
        for lane in [
            "android-dev-workspace",
            "android-documents",
            "android-runtime-teardown",
            "android-storage-metrics-sequence",
            "android-single-container-echo-hi",
            "android-modern-runtime-truth",
        ]:
            self.assertIn(f"--lane {lane}", heavy)


if __name__ == "__main__":
    unittest.main()

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "docs" / "test" / "CI_GATE_LEDGER.md"
MANIFEST = ROOT / "tests" / "test_driver_manifest.json"


class CiGateLedgerTest(unittest.TestCase):
    def setUp(self):
        self.ledger = LEDGER.read_text(encoding="utf-8")
        self.manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    def test_p0_p1_gate_table_names_all_current_focus_areas(self):
        for term in [
            "Service truth",
            "Runtime teardown",
            "Image pull crash safety",
            "OOM/LMK",
            "Single-container execution",
            "Modern/no-PRoot runtime truth",
            "Direct `linkat` hardlink semantics",
            "Docker CLI `docker cp` end-to-end",
            "Storage graph/layer maintenance UI",
            "Media bridge capture/playback",
            "Terminal `-it`",
            "llama GPU correctness",
            "status=planned-gap",
            "success=false",
        ]:
            self.assertIn(term, self.ledger)

    def test_host_smoke_keeps_planned_gap_contracts_visible(self):
        host_ids = {command["id"] for command in self.manifest["lanes"]["host-smoke"]["commands"]}
        for command_id in [
            "verify-service-truth-plan",
            "verify-image-pull-crash-safety",
            "unittest-all",
        ]:
            self.assertIn(command_id, host_ids)
        for ledger_only in [
            "verify-memory-pager-design",
            "verify-llama-gpu-artifact.py",
        ]:
            self.assertIn(ledger_only, self.ledger)

    def test_device_gates_require_non_passing_artifacts_until_proven(self):
        for artifact in [
            "docs/test/service-truth-latest.json",
            "docs/test/runtime-teardown-latest.json",
            "docs/test/image-pull-crash-safety-latest.json",
            "docs/test/test-run-latest.json",
            "docs/test/no-proot-runtime-truth-latest.json",
            "docs/test/linkat-hardlink-semantics-latest.json",
            "files/pdocker/diagnostics/docker-cp-e2e-latest.json",
            "docs/test/storage-layer-maintenance-ui-latest.json",
            "docs/test/media-bridge-capture-playback-latest.json",
            "docs/test/dev-workspace-compose-latest.json",
            "docs/test/saf-direct-output-latest.json",
            "docs/test/storage-metrics-sequence-latest.json",
            "docs/test/llama-gpu-q6k-workflow-latest.json",
        ]:
            self.assertIn(artifact, self.ledger)
        self.assertIn("must not silently pass", self.ledger)

    def test_planned_gap_lanes_are_non_promoting_for_stable_checkpoint(self):
        policy = self.manifest["policy"]
        self.assertIn("stable-checkpoint eligible only", policy["stable_checkpoint_rule"])
        self.assertIn("planned-gap", policy["non_promoting_statuses"])
        self.assertIn("success=false", self.ledger)
        self.assertIn("stable checkpoint", self.ledger)

        for lane_name in [
            "host-smoke",
            "cow-overlay-bench-recovery",
            "android-test-suite",
            "android-documents",
            "android-dev-workspace",
            "android-memory-pager",
            "governance",
            "release-honesty",
        ]:
            self.assertFalse(
                self.manifest["lanes"][lane_name]["stable_checkpoint_eligible"],
                lane_name,
            )

        release_lane = self.manifest["lanes"]["release-honesty"]
        self.assertEqual(release_lane["checkpoint_class"], "release-blocker-ledger-only")
        self.assertEqual(release_lane["commands"][0]["id"], "verify-release-readiness-honesty")


if __name__ == "__main__":
    unittest.main()

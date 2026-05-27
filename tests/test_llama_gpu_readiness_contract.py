import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
READINESS = ROOT / "scripts" / "android-llama-gpu-readiness.sh"


class LlamaGpuReadinessContractTest(unittest.TestCase):
    def setUp(self):
        self.source = READINESS.read_text()

    def test_readiness_is_low_impact_and_json_based(self):
        self.assertIn("pdocker.llama.gpu.device-readiness.v1", self.source)
        self.assertIn("MemAvailable", self.source)
        self.assertIn("SwapFree", self.source)
        self.assertIn('"ready": ready', self.source)
        self.assertIn('"gpu_run_allowed": ready', self.source)
        self.assertIn('"readiness_false_blocks_gpu_run": True', self.source)
        self.assertIn('"executor_marker_required_for_compare_claim": True', self.source)
        self.assertIn('"cpu_comparison_required_for_benchmark_claim": True', self.source)
        self.assertIn('MIN_SWAP_FREE_MB="${PDOCKER_LLAMA_MIN_SWAP_FREE_MB:-0}"', self.source)
        self.assertIn('SWAP_ADVISORY_MB="${PDOCKER_LLAMA_SWAP_ADVISORY_MB:-1024}"', self.source)
        self.assertIn('"swap_policy"', self.source)
        self.assertIn('"swap_pressure_advisory"', self.source)
        self.assertIn("low SwapFree is advisory", self.source)
        self.assertIn('"device_actions": actions', self.source)
        self.assertIn('"preconditions"', self.source)
        self.assertIn('"q6_ngl1_evidence_collection_allowed": ready', self.source)
        self.assertIn('"adb_connected": adb_connected', self.source)
        self.assertIn('"run_as_ok": run_as_ok', self.source)
        self.assertIn('"project_dir_ok": project_ok', self.source)
        self.assertIn("raise SystemExit(0 if ready else 20)", self.source)

    def test_readiness_does_not_start_or_kill_user_processes(self):
        forbidden = [
            "am force-stop",
            "SMOKE_START",
            "containers/create",
            "/containers/",
            "pkill",
            "killall",
        ]
        for needle in forbidden:
            self.assertNotIn(needle, self.source)
        self.assertIn("does not start pdockerd", self.source)
        self.assertIn("does not force-stop the browser", self.source)

    def test_readiness_reports_stale_target_and_browser_hints(self):
        self.assertIn("stale_target_hint", self.source)
        self.assertIn("browser_hint", self.source)
        self.assertIn("Stop the Skydnir llama container", self.source)
        self.assertIn("readiness=false is a hard GPU-run stop", self.source)
        self.assertIn("Do not classify compare, correctness, or benchmark claims", self.source)
        self.assertIn("Do not force-stop the browser/VS Code session", self.source)
        self.assertIn("Connect exactly one ADB device", self.source)
        self.assertIn("before collecting ngl=1 Q6_K evidence", self.source)
        self.assertIn("Open the llama-cpp-gpu project once", self.source)


if __name__ == "__main__":
    unittest.main()

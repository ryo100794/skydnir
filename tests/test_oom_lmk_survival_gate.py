import json, subprocess, sys, tempfile, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify-oom-lmk-survival-gate.py"
MANIFEST = ROOT / "tests" / "test_driver_manifest.json"
LEDGER = ROOT / "docs" / "test" / "CI_GATE_LEDGER.md"
DOC = ROOT / "docs" / "test" / "OOM_LMK_SURVIVAL_GATE.md"
class OomLmkSurvivalGateTest(unittest.TestCase):
    def test_host_static_gate_runs(self):
        result = subprocess.run([sys.executable, str(SCRIPT)], cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("backend SIGKILL/137", result.stdout)
        self.assertIn("planned-gap as non-promoting", result.stdout)
    def test_device_plan_artifact_is_non_passing_and_non_promoting(self):
        with tempfile.TemporaryDirectory() as td:
            artifact = Path(td) / "oom-lmk-survival.json"
            result = subprocess.run([sys.executable, str(SCRIPT), "--device-plan-artifact", str(artifact)], cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            data = json.loads(artifact.read_text())
            self.assertEqual(data["status"], "planned-gap")
            self.assertFalse(data["success"])
            self.assertFalse(data["stable_checkpoint_eligible"])
            self.assertFalse(data["proof"]["backend_death_replay_diagnosed"])
    def test_manifest_and_ledger_wire_survival_gate_as_non_promoting(self):
        manifest = json.loads(MANIFEST.read_text())
        self.assertIn("verify-oom-lmk-survival-gate", {cmd["id"] for cmd in manifest["lanes"]["host-smoke"]["commands"]})
        self.assertIn("oom-lmk-survival-device-gate", {cmd["id"] for cmd in manifest["lanes"]["android-memory-pager"]["commands"]})
        self.assertFalse(manifest["lanes"]["android-memory-pager"]["stable_checkpoint_eligible"])
        self.assertIn("planned-gap", manifest["policy"]["non_promoting_statuses"])
        ledger = LEDGER.read_text()
        self.assertIn("verify-oom-lmk-survival-gate", ledger)
        self.assertIn("docs/test/oom-lmk-survival-latest.json", ledger)
        self.assertIn("backend death must not be masked", ledger)
    def test_runbook_documents_non_promoting_planned_gap(self):
        text = DOC.read_text()
        for token in ["Status: planned-gap", "success=false", "stable_checkpoint_eligible=false", "backend death", "large allocation denial"]:
            self.assertIn(token, text)
if __name__ == "__main__": unittest.main()

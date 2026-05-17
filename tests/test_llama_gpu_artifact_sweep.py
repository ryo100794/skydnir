import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SWEEP = ROOT / "scripts" / "summarize-llama-gpu-artifacts.py"
LATEST = ROOT / "docs" / "test" / "llama-gpu-artifact-sweep-latest.json"


class LlamaGpuArtifactSweepTest(unittest.TestCase):
    def test_sweep_handles_memory_blocker_and_non_object_artifact(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            memory = root / "memory.json"
            memory.write_text(
                json.dumps(
                    {
                        "error": "insufficient_memory",
                        "next_blocker": "recover Android memory and rerun unchanged",
                        "memory": {"mem_available_mb": 64, "swap_free_mb": 128},
                        "required": {"mem_preflight_free_mb": 4096},
                    }
                ),
                encoding="utf-8",
            )
            non_object = root / "executor-events.json"
            non_object.write_text(json.dumps([{"executor": "pdocker-gpu-executor"}]), encoding="utf-8")

            result = subprocess.run(
                [
                    str(SWEEP),
                    "--snapshot-date",
                    "2026-05-17",
                    str(memory),
                    str(non_object),
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["schema"], "pdocker.llama.gpu.artifact-sweep.v1")
            self.assertEqual(report["artifact_count"], 2)
            self.assertEqual(report["classification_counts"]["insufficient_memory"], 1)
            self.assertEqual(report["classification_counts"]["invalid-root"], 1)
            self.assertIn("row-indexed Q6_K", "\n".join(report["next_device_run_checklist"]))

    def test_committed_sweep_records_current_blocker_inventory(self):
        report = json.loads(LATEST.read_text(encoding="utf-8"))
        self.assertEqual(report["schema"], "pdocker.llama.gpu.artifact-sweep.v1")
        self.assertGreater(report["artifact_count"], 0)
        self.assertIn("classification_counts", report)
        self.assertIn("q6_classification_counts", report)
        self.assertIn("next_device_run_checklist", report)
        paths = {entry["path"] for entry in report["artifacts"]}
        self.assertNotIn("docs/test/llama-gpu-artifact-sweep-latest.json", paths)
        checklist = "\n".join(report["next_device_run_checklist"])
        self.assertIn("config_propagation.summary == pass", checklist)
        self.assertIn("q6_writeback_verified_all", checklist)


if __name__ == "__main__":
    unittest.main()

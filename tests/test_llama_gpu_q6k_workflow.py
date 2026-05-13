import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / "scripts" / "android-llama-gpu-q6k-run.py"


class LlamaGpuQ6KWorkflowTest(unittest.TestCase):
    def test_workflow_is_policy_gated_and_manifest_based(self):
        source = WORKFLOW.read_text()
        self.assertIn("pdocker.llama.gpu.q6k-workflow.v1", source)
        self.assertIn("scripts/android-llama-gpu-readiness.sh", source)
        self.assertIn("scripts/android-llama-gpu-compare.sh", source)
        self.assertIn("scripts/verify-llama-gpu-artifact.py", source)
        self.assertIn("local-contract-checks", source)
        self.assertIn("blocked-local-contract", source)
        self.assertIn("git_capture", source)
        self.assertIn('"git": git_capture()', source)
        self.assertIn("--require-q6-workgroup-clear", source)
        self.assertIn('"browser_force_stop_allowed": False', source)
        self.assertIn('"benchmark_requires_correctness": True', source)
        self.assertIn('"llama_cpp_modified": False', source)
        self.assertIn('"dockerfile_modified": False', source)
        self.assertIn('"model_or_prompt_modified": False', source)

    def test_dry_run_writes_manifest_without_device(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = Path(tmpdir) / "workflow.json"
            readiness = Path(tmpdir) / "readiness.json"
            compare = Path(tmpdir) / "compare.json"
            result = subprocess.run(
                [
                    str(WORKFLOW),
                    "--dry-run",
                    "--manifest-out",
                    str(manifest),
                    "--readiness-out",
                    str(readiness),
                    "--out",
                    str(compare),
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(manifest.read_text())
            self.assertEqual(data["schema"], "pdocker.llama.gpu.q6k-workflow.v1")
            self.assertEqual(data["status"], "dry-run")
            self.assertEqual(data["steps"][0]["id"], "local-contract-checks")
            self.assertEqual(data["steps"][0]["status"], "dry-run")
            self.assertEqual(data["steps"][1]["id"], "readiness")
            self.assertIn("git", data)


if __name__ == "__main__":
    unittest.main()

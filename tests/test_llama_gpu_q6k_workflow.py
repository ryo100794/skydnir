import json
import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / "scripts" / "android-llama-gpu-q6k-run.py"


def load_workflow_module():
    spec = importlib.util.spec_from_file_location("android_llama_gpu_q6k_run", WORKFLOW)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {WORKFLOW}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LlamaGpuQ6KWorkflowTest(unittest.TestCase):
    def test_workflow_is_policy_gated_and_manifest_based(self):
        source = WORKFLOW.read_text()
        self.assertIn("pdocker.llama.gpu.q6k-workflow.v1", source)
        self.assertIn("scripts/android-llama-gpu-readiness.sh", source)
        self.assertIn("scripts/android-llama-gpu-compare.sh", source)
        self.assertIn("scripts/verify-llama-gpu-artifact.py", source)
        self.assertIn("extract_json_object", source)
        self.assertIn("verifier.stdout", source)
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

    def test_verifier_json_is_extracted_from_full_stdout_not_tail(self):
        workflow = load_workflow_module()
        prefix = "diagnostic line\n" + ("x" * 9000) + "\n"
        payload = {
            "status": "fail",
            "classification": {"responsibility": "q6-native-device-execution"},
            "next_action": "keep probing final store",
        }
        suffix = "\ntrailing diagnostic\n" + ("y" * 9000)
        full_output = prefix + json.dumps(payload) + suffix
        extracted = workflow.extract_json_object(full_output)
        self.assertEqual(extracted["classification"]["responsibility"], "q6-native-device-execution")
        self.assertEqual(extracted["next_action"], "keep probing final store")
        self.assertEqual(workflow.extract_json_object(full_output[-8000:]), {})

    def test_run_step_can_persist_full_verifier_stdout(self):
        workflow = load_workflow_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "verifier.stdout"
            payload = {"classification": {"responsibility": "q6-native-device-execution"}}
            script = (
                "import json; "
                "print('x' * 9000); "
                f"print(json.dumps({payload!r}))"
            )
            record = workflow.run_step(
                "synthetic-verifier",
                ["python3", "-c", script],
                {},
                False,
                stdout_path=out,
            )
            self.assertEqual(record["exit_code"], 0)
            self.assertTrue(out.is_file())
            self.assertGreater(record["stdout_size"], 9000)
            extracted = workflow.extract_json_object(out.read_text())
            self.assertEqual(extracted["classification"]["responsibility"], "q6-native-device-execution")


if __name__ == "__main__":
    unittest.main()

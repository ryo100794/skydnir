import json
import contextlib
import importlib.util
import io
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
        self.assertIn("load_q6_required_env_overlay", source)
        self.assertIn("q6_required_env_overlay", source)
        self.assertIn("q6_compare_env", source)
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
            overlay = json.loads((ROOT / "scripts" / "llama-gpu-env-manifest.json").read_text())["q6_required_env_overlay"]
            self.assertEqual(data["q6_required_env_overlay"], overlay)
            self.assertEqual(data["q6_compare_env"], overlay)

    def test_q6_required_env_overlay_is_loaded_from_manifest(self):
        workflow = load_workflow_module()
        manifest = json.loads((ROOT / "scripts" / "llama-gpu-env-manifest.json").read_text())
        overlay = workflow.load_q6_required_env_overlay()
        self.assertEqual(overlay, manifest["q6_required_env_overlay"])
        self.assertLessEqual(set(overlay), set(manifest["compare_forward_env_keys"]))
        self.assertTrue(overlay)

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

    def test_ready_workflow_invokes_compare_script_without_argv_drift(self):
        workflow = load_workflow_module()
        captured: list[tuple[str, list[str]]] = []
        captured_env: dict[str, dict[str, str]] = {}
        original_run_step = workflow.run_step
        original_load_json = workflow.load_json

        def fake_run_step(step_id, argv, env, dry_run, stdout_path=None):
            captured.append((step_id, list(argv)))
            captured_env[step_id] = dict(env)
            if stdout_path is not None:
                stdout_path.parent.mkdir(parents=True, exist_ok=True)
                stdout_path.write_text(
                    json.dumps(
                        {
                            "status": "pass",
                            "next_action": "q6k compare command captured",
                        }
                    ),
                    encoding="utf-8",
                )
            return {
                "id": step_id,
                "argv": list(argv),
                "command": " ".join(argv),
                "exit_code": 0,
                "status": "pass",
                "stdout_tail": "",
            }

        def fake_load_json(path):
            if Path(path) == workflow.ENV_MANIFEST:
                return original_load_json(path)
            return {"ready": True}

        workflow.run_step = fake_run_step
        workflow.load_json = fake_load_json
        workflow_data = None
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                workflow_manifest = Path(tmpdir) / "workflow.json"
                with contextlib.redirect_stdout(io.StringIO()):
                    rc = workflow.main(
                        [
                            "--skip-local-checks",
                            "--manifest-out",
                            str(workflow_manifest),
                            "--readiness-out",
                            str(Path(tmpdir) / "readiness.json"),
                            "--out",
                            str(Path(tmpdir) / "compare.json"),
                        ]
                    )
                workflow_data = json.loads(workflow_manifest.read_text())
        finally:
            workflow.run_step = original_run_step
            workflow.load_json = original_load_json

        self.assertEqual(rc, 0)
        compare_argv = dict(captured)["compare-ngl1-q6k-workgroup"]
        self.assertEqual(compare_argv[:2], ["bash", "scripts/android-llama-gpu-compare.sh"])
        self.assertNotEqual(compare_argv[:3], ["bash", "bash", "scripts/android-llama-gpu-compare.sh"])
        overlay = workflow.load_q6_required_env_overlay()
        compare_env = captured_env["compare-ngl1-q6k-workgroup"]
        for key, value in overlay.items():
            self.assertEqual(compare_env.get(key), value, key)
        self.assertIsNotNone(workflow_data)
        self.assertEqual(workflow_data["q6_required_env_overlay"], overlay)
        self.assertEqual(workflow_data["q6_compare_env"], overlay)

    def test_invalid_q6_required_env_overlay_blocks_before_device_steps(self):
        workflow = load_workflow_module()
        original_env_manifest = workflow.ENV_MANIFEST
        original_run_step = workflow.run_step
        captured_steps = []

        def fake_run_step(step_id, argv, env, dry_run, stdout_path=None):
            captured_steps.append(step_id)
            return {"id": step_id, "argv": list(argv), "exit_code": 0, "status": "pass"}

        workflow.run_step = fake_run_step
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                bad_manifest = Path(tmpdir) / "bad-env-manifest.json"
                bad_manifest.write_text(json.dumps({"schema": "pdocker.llama.gpu.env-manifest.v1"}))
                workflow.ENV_MANIFEST = bad_manifest
                workflow_manifest = Path(tmpdir) / "workflow.json"
                with contextlib.redirect_stdout(io.StringIO()):
                    rc = workflow.main(
                        [
                            "--skip-local-checks",
                            "--manifest-out",
                            str(workflow_manifest),
                            "--readiness-out",
                            str(Path(tmpdir) / "readiness.json"),
                            "--out",
                            str(Path(tmpdir) / "compare.json"),
                        ]
                    )
                data = json.loads(workflow_manifest.read_text())
        finally:
            workflow.ENV_MANIFEST = original_env_manifest
            workflow.run_step = original_run_step

        self.assertEqual(rc, 30)
        self.assertEqual(data["status"], "blocked-env-manifest")
        self.assertEqual(captured_steps, [])
        self.assertNotIn("readiness", [step["id"] for step in data["steps"]])
        self.assertNotIn("compare-ngl1-q6k-workgroup", [step["id"] for step in data["steps"]])


if __name__ == "__main__":
    unittest.main()

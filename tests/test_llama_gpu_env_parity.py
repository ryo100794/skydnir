import importlib.util
import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "scripts" / "llama-gpu-env-manifest.json"
VERIFIER = ROOT / "scripts" / "verify-llama-gpu-artifact.py"
COMPARE = ROOT / "scripts" / "android-llama-gpu-compare.sh"
PDOCKERD = ROOT / "app" / "src" / "main" / "assets" / "pdockerd" / "pdockerd"
LLAMA_COMPOSE = ROOT / "app" / "src" / "main" / "assets" / "project-library" / "llama-cpp-gpu" / "compose.yaml"


def load_verifier():
    spec = importlib.util.spec_from_file_location("llama_gpu_artifact_verifier", VERIFIER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_manifest():
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


class LlamaGpuEnvParityTest(unittest.TestCase):
    def test_manifest_env_groups_are_ordered_unique_and_subset_consistent(self):
        manifest = load_manifest()
        self.assertEqual(manifest["schema"], "pdocker.llama.gpu.env-manifest.v1")
        for key in [
            "ui_runtime_env_keys",
            "pdockerd_runtime_env_keys",
            "ui_compose_runtime_env_keys",
            "compare_diagnostic_env_keys",
            "compare_forward_env_keys",
        ]:
            values = manifest[key]
            self.assertTrue(values, key)
            self.assertEqual(len(values), len(set(values)), key)
            self.assertTrue(all(isinstance(value, str) and value for value in values), key)

        ui_runtime = set(manifest["ui_runtime_env_keys"])
        self.assertEqual(manifest["ui_runtime_env_keys"], manifest["pdockerd_runtime_env_keys"])
        self.assertLessEqual(set(manifest["ui_compose_runtime_env_keys"]), ui_runtime)
        self.assertLessEqual(set(manifest["compare_diagnostic_env_keys"]), set(manifest["compare_forward_env_keys"]))
        self.assertLessEqual(ui_runtime, set(manifest["compare_forward_env_keys"]))

    def test_verifier_constants_are_loaded_from_the_same_manifest(self):
        manifest = load_manifest()
        verifier = load_verifier()
        self.assertEqual(tuple(manifest["ui_runtime_env_keys"]), verifier.LLAMA_GPU_UI_RUNTIME_ENV_KEYS)
        self.assertEqual(tuple(manifest["pdockerd_runtime_env_keys"]), verifier.LLAMA_GPU_PDOCKERD_RUNTIME_ENV_KEYS)
        self.assertEqual(tuple(manifest["ui_compose_runtime_env_keys"]), verifier.LLAMA_GPU_UI_COMPOSE_RUNTIME_ENV_KEYS)
        self.assertEqual(tuple(manifest["compare_diagnostic_env_keys"]), verifier.LLAMA_GPU_COMPARE_DIAGNOSTIC_ENV_KEYS)
        self.assertEqual(tuple(manifest["compare_forward_env_keys"]), verifier.LLAMA_GPU_COMPARE_FORWARD_ENV_KEYS)

    def test_compare_pdockerd_and_ui_compose_env_surfaces_match_manifest(self):
        manifest = load_manifest()
        compare = COMPARE.read_text(encoding="utf-8")
        pdockerd = PDOCKERD.read_text(encoding="utf-8")
        compose = LLAMA_COMPOSE.read_text(encoding="utf-8")

        # The compare script must stay manifest-driven instead of carrying a
        # second hand-maintained diagnostic env list.
        self.assertIn("llama-gpu-env-manifest.json", compare)
        self.assertIn("compare_forward_env_keys", compare)
        self.assertIn('set_env(env, f"{key}={value}")', compare)
        self.assertIn("record_manifest_runtime_env", compare)
        self.assertIn("pdocker.llama.gpu.runtime-env-record.v1", compare)
        self.assertIn("[pdocker llama compare] runtime env", compare)
        self.assertIn('"runtime_env_manifest": runtime_env_manifest', compare)
        self.assertIn("requested_env_missing_from_runtime", compare)

        for key in manifest["pdockerd_runtime_env_keys"]:
            self.assertRegex(
                pdockerd,
                rf'"{re.escape(key)}"\s*:\s*os\.environ\.get\("{re.escape(key)}"',
                key,
            )

        for key in manifest["ui_compose_runtime_env_keys"]:
            self.assertIn(f"{key}:", compose, key)
            self.assertIn(f"${{{key}:-", compose, key)

        # Compare-only diagnostics must remain absent from the UI compose
        # template until promoted to ordinary runtime behavior.
        for key in sorted(set(manifest["compare_diagnostic_env_keys"]) - set(manifest["ui_compose_runtime_env_keys"])):
            self.assertNotIn(f"{key}:", compose, key)

    def test_compare_echoes_and_records_manifest_based_runtime_env(self):
        compare = COMPARE.read_text(encoding="utf-8")
        verifier = VERIFIER.read_text(encoding="utf-8")

        self.assertIn('string_list("compare_forward_env_keys")', compare)
        self.assertIn('string_list("compare_diagnostic_env_keys")', compare)
        self.assertIn('"config_propagation_env_keys": config_keys', compare)
        self.assertIn('"host_requested_env": dict(sorted(host_env.items()))', compare)
        self.assertIn('"host_echo_recorded": bool(runtime_env_record.get("echoed_to_log"))', compare)
        self.assertIn('"runtime_env_observed_keys": sorted(effective_runtime_env)', compare)
        self.assertIn('"requested_env_observed_keys"', compare)

        self.assertIn("def _runtime_env_manifest_record", verifier)
        self.assertIn('"runtime_env_manifest": runtime_env_manifest', verifier)

    def test_compare_collects_app_owned_logs_when_engine_log_api_is_unavailable(self):
        compare = COMPARE.read_text(encoding="utf-8")

        self.assertIn("pdocker direct log fallback", compare)
        self.assertIn("llama workspace log fallback", compare)
        self.assertIn("engine-inspect-unavailable", compare)
        self.assertIn("container-state-not-found", compare)
        self.assertIn("root / 'logs'", compare)
        self.assertIn("llama-server.log", compare)
        self.assertIn("post_readiness_memory", compare)
        self.assertIn("post_completion_health", compare)
        self.assertIn("server_alive_after_completion", compare)
        self.assertIn("COMPARE_RESULT_READY=1", compare)
        self.assertIn('"$COMPARE_RESULT_READY" != "1"', compare)


if __name__ == "__main__":
    unittest.main()

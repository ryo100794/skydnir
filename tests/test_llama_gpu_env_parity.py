import importlib.util
import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "scripts" / "llama-gpu-env-manifest.json"
VERIFIER = ROOT / "scripts" / "verify-llama-gpu-artifact.py"
COMPARE = ROOT / "scripts" / "android-llama-gpu-compare.sh"
PDOCKERD = ROOT / "docker-proot-setup" / "bin" / "pdockerd"
PDOCERD_RUNTIME = ROOT / "app" / "src" / "main" / "kotlin" / "io" / "github" / "ryo100794" / "pdocker" / "PdockerdRuntime.kt"
BUILD_GRADLE = ROOT / "app" / "build.gradle.kts"
COPY_NATIVE = ROOT / "scripts" / "copy-native.sh"
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
        ui_compose_defaults = manifest["ui_compose_runtime_env_defaults"]
        self.assertTrue(ui_compose_defaults)
        ui_compose_default_keys = [item["env"] for item in ui_compose_defaults]
        self.assertEqual(ui_compose_default_keys, manifest["ui_compose_runtime_env_keys"])
        for item in ui_compose_defaults:
            self.assertIsInstance(item.get("default"), str)
        defaults = manifest["pdockerd_runtime_env_defaults"]
        self.assertTrue(defaults)
        default_keys = [item["env"] for item in defaults]
        self.assertEqual(len(default_keys), len(set(default_keys)))
        self.assertLessEqual(set(manifest["pdockerd_runtime_env_keys"]), set(default_keys))
        for item in defaults:
            self.assertIsInstance(item.get("env"), str)
            self.assertIsInstance(item.get("default"), str)
            self.assertIn("vulkan", item.get("modes", []))
        profiles = manifest["compare_mode_env_profiles"]
        self.assertIsInstance(profiles, dict)
        vulkan_raw = profiles["vulkan-raw"]
        profile_keys = [item["env"] for item in vulkan_raw["env"]]
        self.assertEqual(len(profile_keys), len(set(profile_keys)))
        for key in [
            "PDOCKER_VULKAN_MAX_BUFFER_BYTES",
            "GGML_VK_FORCE_MAX_BUFFER_SIZE",
            "GGML_VK_FORCE_MAX_ALLOCATION_SIZE",
            "GGML_VK_SUBALLOCATION_BLOCK_SIZE",
            "LLAMA_ARG_N_GPU_LAYERS",
        ]:
            self.assertIn(key, profile_keys)
        trace_keys = [item["env"] for item in vulkan_raw["trace_alloc_env"]]
        self.assertIn("PDOCKER_VULKAN_ICD_TRACE_ALLOC", trace_keys)
        self.assertIn("PDOCKER_GPU_DISPATCH_PROFILE_RESPONSE", trace_keys)

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
        self.assertIn("compare_mode_env_profiles", compare)
        self.assertIn("apply_manifest_mode_env(env, mode, trace_alloc)", compare)
        self.assertNotIn('"PDOCKER_VULKAN_MAX_BUFFER_BYTES=536870912"', compare)
        self.assertNotIn('"GGML_VK_FORCE_MAX_BUFFER_SIZE=536870912"', compare)
        self.assertIn("record_manifest_runtime_env", compare)
        self.assertIn("pdocker.llama.gpu.runtime-env-record.v1", compare)
        self.assertIn("def build_api_executor_reconciliation", compare)
        self.assertIn('"api_executor_reconciliation": api_executor_reconciliation', compare)
        self.assertIn('"proof_strength": "diagnostic"', compare)
        self.assertIn('"hash_algorithm": "fnv1a64"', compare)
        self.assertIn('"diagnostic_record_sha256"', compare)
        self.assertIn('"diagnostic_set_sha256"', compare)
        self.assertIn("[pdocker llama compare] runtime env", compare)
        self.assertIn("record_planned_container_payload_env", compare)
        self.assertIn('"planned_container_env": planned_env', compare)
        self.assertIn('"planned_container_env_keys": sorted(str(key) for key in planned_env)', compare)
        self.assertIn('"runtime_env_manifest": runtime_env_manifest', compare)
        self.assertIn("requested_env_missing_from_runtime", compare)

        self.assertIn("load_gpu_env_manifest", pdockerd)
        self.assertIn("gpu_runtime_env_defaults", pdockerd)
        self.assertIn("pdockerd_runtime_env_defaults", pdockerd)
        self.assertIn("llama-gpu-env-manifest.json", pdockerd)
        for key in manifest["pdockerd_runtime_env_keys"]:
            self.assertIn(key, json.dumps(manifest["pdockerd_runtime_env_defaults"]), key)

        for key in manifest["ui_compose_runtime_env_keys"]:
            self.assertIn(f"{key}:", compose, key)
            self.assertIn(f"${{{key}:-", compose, key)
        self.assertIn("pdocker.llama-gpu-env-manifest: begin ui_compose_runtime_env_defaults", compose)
        self.assertIn("pdocker.llama-gpu-env-manifest: end", compose)
        for item in manifest["ui_compose_runtime_env_defaults"]:
            expected = f'{item["env"]}: "${{{item["env"]}:-{item["default"]}}}"'
            self.assertIn(expected, compose, item["env"])

        # Compare-only diagnostics must remain absent from the UI compose
        # template until promoted to ordinary runtime behavior.
        for key in sorted(set(manifest["compare_diagnostic_env_keys"]) - set(manifest["ui_compose_runtime_env_keys"])):
            self.assertNotIn(f"{key}:", compose, key)

    def test_packaged_pdockerd_runtime_carries_gpu_env_manifest(self):
        runtime = PDOCERD_RUNTIME.read_text(encoding="utf-8")
        build_gradle = BUILD_GRADLE.read_text(encoding="utf-8")
        copy_native = COPY_NATIVE.read_text(encoding="utf-8")
        for source in [runtime, build_gradle, copy_native]:
            self.assertIn("llama-gpu-env-manifest.json", source)
        self.assertIn('extractAsset(ctx, "pdockerd/llama-gpu-env-manifest.json"', runtime)
        self.assertIn('rootProject.file("scripts/llama-gpu-env-manifest.json")', build_gradle)
        self.assertIn('$ROOT/scripts/llama-gpu-env-manifest.json', copy_native)

    def test_compare_echoes_and_records_manifest_based_runtime_env(self):
        compare = COMPARE.read_text(encoding="utf-8")
        verifier = VERIFIER.read_text(encoding="utf-8")

        self.assertIn('string_list("compare_forward_env_keys")', compare)
        self.assertIn('string_list("compare_diagnostic_env_keys")', compare)
        self.assertIn('"config_propagation_env_keys": config_keys', compare)
        self.assertIn('"host_requested_env": dict(sorted(host_env.items()))', compare)
        self.assertIn('"host_echo_recorded": bool(runtime_env_record.get("echoed_to_log"))', compare)
        self.assertIn('runtime_env_record.get("planned_container_env")', compare)
        self.assertIn('effective_runtime_env.update({str(name): str(value) for name, value in planned_env.items()})', compare)
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

    def test_compare_prioritizes_wrong_completion_output_over_stale_marker(self):
        compare = COMPARE.read_text(encoding="utf-8")

        self.assertIn("service_completion_wrong_output", compare)
        self.assertIn("service_prompt_sanity", compare)
        self.assertIn('blocker_class = "llama_completion_wrong_output"', compare)
        self.assertIn("deterministic /completion returned", compare)
        self.assertIn('"prompt_sanity": "pass" if report["completion"].get("passed") is True else "fail"', compare)
        self.assertIn('and report["completion"].get("passed") is True', compare)
        self.assertIn('"id": "service_prompt_sanity"', compare)
        self.assertIn('"service_prompt_sanity": service_prompt_sanity', compare)
        self.assertLess(
            compare.index('blocker_class = "llama_completion_wrong_output"'),
            compare.index('blocker_class = "runtime_freshness_mismatch"'),
        )


if __name__ == "__main__":
    unittest.main()

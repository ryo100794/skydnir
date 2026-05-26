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
MAIN_ACTIVITY = ROOT / "app" / "src" / "main" / "kotlin" / "io" / "github" / "ryo100794" / "pdocker" / "MainActivity.kt"
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
            "compare_forward_env_keys",
            "compare_probe_env_keys",
        ]:
            values = manifest[key]
            self.assertTrue(values, key)
            self.assertEqual(len(values), len(set(values)), key)
            self.assertTrue(all(isinstance(value, str) and value for value in values), key)

        ui_runtime = set(manifest["ui_runtime_env_keys"])
        self.assertLessEqual(ui_runtime, set(manifest["compare_forward_env_keys"]))
        self.assertLessEqual(set(manifest["compare_probe_env_keys"]), set(manifest["compare_forward_env_keys"]))
        self.assertEqual(
            manifest["compare_probe_env_keys"],
            manifest["env_bridge_classifications"]["spirv_probe_transport"],
        )
        ui_compose_defaults = manifest["ui_compose_runtime_env_defaults"]
        self.assertTrue(ui_compose_defaults)
        ui_compose_default_keys = [item["env"] for item in ui_compose_defaults]
        self.assertLessEqual(set(ui_compose_default_keys), ui_runtime)
        for item in ui_compose_defaults:
            self.assertIsInstance(item.get("default"), str)
        defaults = manifest["pdockerd_runtime_env_defaults"]
        self.assertTrue(defaults)
        default_keys = [item["env"] for item in defaults]
        self.assertEqual(len(default_keys), len(set(default_keys)))
        self.assertLessEqual(ui_runtime, set(default_keys))
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
        self.assertEqual(tuple(manifest["compare_forward_env_keys"]), verifier.LLAMA_GPU_COMPARE_FORWARD_ENV_KEYS)

    def test_manifest_env_bridge_classification_is_exhaustive(self):
        manifest = load_manifest()

        env_keys = set()
        for key in [
            "ui_runtime_env_keys",
            "compare_forward_env_keys",
            "compare_probe_env_keys",
        ]:
            env_keys.update(manifest[key])
        for key in [
            "pdockerd_runtime_env_defaults",
            "ui_compose_runtime_env_defaults",
            "config_propagation_env_fields",
            "abi_dispatch_option_env_fields",
        ]:
            env_keys.update(item["env"] for item in manifest[key])
        for profile in manifest["compare_mode_env_profiles"].values():
            for key in ["env", "trace_alloc_env"]:
                env_keys.update(item["env"] for item in profile.get(key, []))

        classifications = manifest["env_bridge_classifications"]
        expected_classes = {
            "container_env_only",
            "icd_to_executor_bool_option",
            "icd_to_executor_size_option",
            "icd_to_executor_string_option",
            "app_process_only",
            "deprecated_or_invalid",
            "needs_bridge",
            "spirv_probe_transport",
        }
        self.assertEqual(expected_classes, set(classifications))
        classified = set()
        for name, values in classifications.items():
            self.assertIsInstance(values, list, name)
            values = set(values)
            overlap = classified & values
            if name == "spirv_probe_transport":
                overlap -= set(classifications["icd_to_executor_size_option"])
            self.assertFalse(overlap, f"{name} overlaps: {sorted(overlap)}")
            classified.update(values)

        self.assertLessEqual(env_keys, classified)
        self.assertEqual(
            set(classifications["app_process_only"]),
            classified - env_keys,
        )
        self.assertFalse(set(classifications["app_process_only"]) & set(manifest["compare_forward_env_keys"]))
        self.assertEqual(
            {
                item["env"] for item in manifest["abi_dispatch_option_env_fields"]
                if item["type"] == "bool"
            },
            set(classifications["icd_to_executor_bool_option"]),
        )
        self.assertEqual(
            {
                item["env"] for item in manifest["abi_dispatch_option_env_fields"]
                if item["type"] == "size"
            },
            set(classifications["icd_to_executor_size_option"]),
        )

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
        self.assertNotIn("bridge_max_buffer_bytes = 536870912", compare)
        self.assertIn("def compare_profile_value", compare)
        self.assertIn("def int_runtime_or_profile_env", compare)
        self.assertIn('configured_clamps["PDOCKER_VULKAN_MAX_BUFFER_BYTES"]', compare)
        self.assertIn("record_manifest_runtime_env", compare)
        self.assertIn("pdocker.llama.gpu.runtime-env-record.v1", compare)
        self.assertIn("def build_api_executor_reconciliation", compare)
        self.assertIn('"api_executor_reconciliation": api_executor_reconciliation', compare)
        self.assertIn('"proof_strength": "diagnostic"', compare)
        self.assertIn('"hash_algorithm": "fnv1a64"', compare)
        self.assertIn('not str(key).endswith("_comparable")', compare)
        self.assertIn('"diagnostic_record_sha256"', compare)
        self.assertIn('"diagnostic_set_sha256"', compare)
        self.assertIn("identical_duplicate_dispatch_ids", compare)
        self.assertIn("previous_identity == identity_sha256", compare)
        self.assertIn('"core_command_hash_comparable"', compare)
        self.assertIn('"core_command_hash": receive.get("core_command_hash")', compare)
        self.assertIn("[pdocker llama compare] runtime env", compare)
        self.assertIn("record_planned_container_payload_env", compare)
        self.assertIn('"planned_container_env": planned_env', compare)
        self.assertIn('"planned_container_env_keys": sorted(str(key) for key in planned_env)', compare)
        self.assertIn('"runtime_env_manifest": runtime_env_manifest', compare)
        self.assertIn("requested_env_missing_from_runtime", compare)
        self.assertIn('CURRENT_STAGE="pdockerd startup"', compare)
        self.assertIn('CURRENT_STAGE="SPIR-V probe staging"', compare)
        self.assertIn('probe_env_keys = string_list("compare_probe_env_keys")', compare)
        self.assertIn('"compare_probe_env_keys": [str(key) for key in manifest_probe_env_keys', compare)
        self.assertIn('"spirv_probe_env_audit": spirv_probe_env_audit', compare)
        self.assertIn("def build_spirv_probe_env_audit", compare)
        self.assertIn("vulkan_dispatch_v4_size_option", compare)
        self.assertIn("executor getenv is not trusted for the audit", compare)
        self.assertIn("partial SPIR-V probe env is unsafe; set all or none", compare)
        for key in [
            "PDOCKER_GPU_SPIRV_PROBE_MANIFEST",
            "PDOCKER_GPU_SPIRV_PROBE_SHADER",
            "PDOCKER_GPU_SPIRV_PROBE_EXPECTED_HASH",
            "PDOCKER_GPU_SPIRV_PROBE_EFFECTIVE_HASH",
            "PDOCKER_GPU_SPIRV_PROBE_DEBUG_BYTES",
            "PDOCKER_GPU_SPIRV_PROBE_DEBUG_SET",
            "PDOCKER_GPU_SPIRV_PROBE_DEBUG_BINDING",
            "PDOCKER_GPU_SPIRV_PROBE_TARGET_ONLY",
        ]:
            self.assertIn(key, manifest["compare_probe_env_keys"])

        self.assertIn("load_gpu_env_manifest", pdockerd)
        self.assertIn("gpu_runtime_env_defaults", pdockerd)
        self.assertIn("pdockerd_runtime_env_defaults", pdockerd)
        self.assertIn("llama-gpu-env-manifest.json", pdockerd)
        self.assertIn('gpu_runtime_env_defaults("vulkan")', pdockerd)
        self.assertNotIn('gpu_runtime_env_defaults("vulkan", {', pdockerd)
        self.assertNotIn('"PDOCKER_VULKAN_MAX_BUFFER_BYTES": os.environ.get("PDOCKER_VULKAN_MAX_BUFFER_BYTES", "2147483648")', pdockerd)
        for key in manifest["ui_runtime_env_keys"]:
            self.assertIn(key, json.dumps(manifest["pdockerd_runtime_env_defaults"]), key)

        ui_compose_keys = [item["env"] for item in manifest["ui_compose_runtime_env_defaults"]]
        for key in ui_compose_keys:
            self.assertIn(f"{key}:", compose, key)
            self.assertIn(f"${{{key}:-", compose, key)
            self.assertNotIn(f'LlamaComposeEnvDefault("{key}",', MAIN_ACTIVITY.read_text(encoding="utf-8"))
        self.assertIn("pdocker.llama-gpu-env-manifest: begin ui_compose_runtime_env_defaults", compose)
        self.assertIn("pdocker.llama-gpu-env-manifest: end", compose)
        self.assertIn("fallbackLlamaComposeEnvDefaultsFromBundledCompose", MAIN_ACTIVITY.read_text(encoding="utf-8"))
        for item in manifest["ui_compose_runtime_env_defaults"]:
            expected = f'{item["env"]}: "${{{item["env"]}:-{item["default"]}}}"'
            self.assertIn(expected, compose, item["env"])

        # Compare-only diagnostics must remain absent from the UI compose
        # template until promoted to ordinary runtime behavior.
        compare_only_keys = set(manifest["q6_required_env_overlay"]) | {
            item["env"] for item in manifest["config_propagation_env_fields"]
        }
        for key in sorted(compare_only_keys - set(ui_compose_keys)):
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
        self.assertNotIn('string_list("compare_diagnostic_env_keys")', compare)
        self.assertIn('string_list("compare_probe_env_keys")', compare)
        self.assertIn('"compare_probe_env_keys": probe_keys', compare)
        self.assertIn('"app_process_only_env_keys": app_process_only_env_keys', compare)
        self.assertIn('"config_propagation_env_keys": config_keys', compare)
        self.assertIn('"host_requested_env": dict(sorted(host_env.items()))', compare)
        self.assertIn('"host_echo_recorded": bool(runtime_env_record.get("echoed_to_log"))', compare)
        self.assertIn('runtime_env_record.get("planned_container_env")', compare)
        self.assertIn('effective_runtime_env.update({str(name): str(value) for name, value in planned_env.items()})', compare)
        self.assertIn('"runtime_env_observed_keys": sorted(effective_runtime_env)', compare)
        self.assertIn('"requested_env_observed_keys"', compare)
        self.assertIn('"app_process_only_not_host_container_forwarded_keys"', compare)

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

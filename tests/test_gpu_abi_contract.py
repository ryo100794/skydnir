import json
import importlib.util
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_HEADER = ROOT / "app" / "src" / "main" / "cpp" / "pdocker_gpu_abi.h"
CONTAINER_HEADER = ROOT / "docker-proot-setup" / "src" / "gpu" / "pdocker_gpu_abi.h"
GPU_EXECUTOR = ROOT / "app" / "src" / "main" / "cpp" / "pdocker_gpu_executor.c"
VULKAN_ICD = ROOT / "docker-proot-setup" / "src" / "gpu" / "pdocker_vulkan_icd.c"
LLAMA_COMPARE = ROOT / "scripts" / "android-llama-gpu-compare.sh"
PDOCKERD = ROOT / "docker-proot-setup" / "bin" / "pdockerd"
PDOCKERD_SERVICE = ROOT / "app" / "src" / "main" / "kotlin" / "io" / "github" / "ryo100794" / "pdocker" / "PdockerdService.kt"
MAIN_ACTIVITY = ROOT / "app" / "src" / "main" / "kotlin" / "io" / "github" / "ryo100794" / "pdocker" / "MainActivity.kt"
LLAMA_GPU_NEXT_STEPS = ROOT / "docs" / "plan" / "LLAMA_GPU_BRIDGE_NEXT_STEPS.md"
LLAMA_GPU_DEVICE_RUNBOOK = ROOT / "docs" / "test" / "LLAMA_GPU_DEVICE_RUNBOOK_20260513.md"
LLAMA_GPU_CORRECTNESS = ROOT / "docs" / "test" / "LLAMA_GPU_CORRECTNESS_20260507.md"
ROPE_YARN_ARTIFACT = ROOT / "docs" / "test" / "llama-gpu-ngl1-rope-yarn-oracle-20260509.json"
LLAMA_GPU_ARTIFACT_VERIFIER = ROOT / "scripts" / "verify-llama-gpu-artifact.py"
LLAMA_GPU_ENV_MANIFEST = ROOT / "scripts" / "llama-gpu-env-manifest.json"


def load_llama_gpu_artifact_verifier():
    spec = importlib.util.spec_from_file_location("llama_gpu_artifact_verifier", LLAMA_GPU_ARTIFACT_VERIFIER)
    verifier = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(verifier)
    return verifier


def defines(path):
    result = {}
    for line in path.read_text().splitlines():
        match = re.match(r"#define\s+(PDOCKER_GPU_[A-Z0-9_]+)\s+(.+)", line)
        if match:
            result[match.group(1)] = match.group(2).strip()
    return result


class GpuAbiContractTest(unittest.TestCase):
    def test_container_and_apk_gpu_abi_headers_stay_in_sync(self):
        self.assertEqual(defines(CONTAINER_HEADER), defines(APP_HEADER))

    def test_gpu_abi_remains_backend_neutral(self):
        values = "\n".join(defines(APP_HEADER).values()).lower()
        for forbidden in ["android.hardware", "bionic", "libvulkan.so", "libopencl.so"]:
            self.assertNotIn(forbidden, values)
        self.assertIn("pdocker-gpu-command-v1", values)
        self.assertIn("glibc-shim-command-queue", values)

    def test_vulkan_dispatch_reports_binding_diagnostics(self):
        source = GPU_EXECUTOR.read_text()
        self.assertIn("PDOCKER_GPU_EXECUTOR_BUILD_MARKER", source)
        self.assertIn('\\"executor_build_marker\\":\\"%s\\"', source)
        self.assertIn("build_marker=%s", source)
        self.assertIn('\\"binding_details\\":[', source)
        self.assertIn("profile_response", source)
        self.assertIn("PDOCKER_GPU_DISPATCH_PROFILE_RESPONSE", source)
        self.assertIn("if (profile_response) {", source)
        for field in [
            '\\"binding\\":%u',
            '\\"offset\\":%lld',
            '\\"size\\":%zu',
            '\\"alias_rep\\":%zu',
            '\\"active\\":%s',
            '\\"readable\\":%s',
            '\\"writable\\":%s',
            '\\"resident\\":%s',
            '\\"cache_hit\\":%s',
            '\\"mutable_reused\\":%s',
            '\\"mutable_cache_hit\\":%s',
            '\\"upload_ms\\":%.4f',
            '\\"download_ms\\":%.4f',
            '\\"dirty_probe_pages\\":%zu',
            '\\"dirty_probe_bytes\\":%zu',
            '\\"dirty_probe_ms\\":%.4f',
            '\\"dirty_writeback_cached\\":%s',
            '\\"dirty_writeback_bytes\\":%zu',
            '\\"writeback_offset\\":%lld',
            '\\"writeback_bytes\\":%zu',
            '\\"device_local_staged\\":%s',
            '\\"fd_before_hash\\":\\"0x%016llx\\"',
            '\\"gpu_after_upload_hash\\":\\"0x%016llx\\"',
            '\\"gpu_after_dispatch_hash\\":\\"0x%016llx\\"',
            '\\"fd_after_hash\\":\\"0x%016llx\\"',
        ]:
            self.assertIn(field, source)
        self.assertGreaterEqual(source.count("write_vulkan_binding_report(json_out()"), 2)
        self.assertIn("binding_upload_ms[PDOCKER_GPU_MAX_VULKAN_BINDINGS]", source)
        self.assertIn("binding_download_ms[PDOCKER_GPU_MAX_VULKAN_BINDINGS]", source)
        self.assertIn("binding_dirty_probe_pages[PDOCKER_GPU_MAX_VULKAN_BINDINGS]", source)
        self.assertIn("binding_dirty_probe_bytes[PDOCKER_GPU_MAX_VULKAN_BINDINGS]", source)
        self.assertIn("binding_fd_before_hash[PDOCKER_GPU_MAX_VULKAN_BINDINGS]", source)
        self.assertIn("binding_gpu_after_dispatch_hash[PDOCKER_GPU_MAX_VULKAN_BINDINGS]", source)
        self.assertIn("binding_alias_rep[PDOCKER_GPU_MAX_VULKAN_BINDINGS]", source)
        self.assertIn("binding_group_read_needed[PDOCKER_GPU_MAX_VULKAN_BINDINGS]", source)
        self.assertIn("binding_group_base[PDOCKER_GPU_MAX_VULKAN_BINDINGS]", source)
        self.assertIn("binding_group_end[PDOCKER_GPU_MAX_VULKAN_BINDINGS]", source)
        self.assertIn("binding_gpu_offset[PDOCKER_GPU_MAX_VULKAN_BINDINGS]", source)
        self.assertIn("write_vulkan_descriptor_write_report", source)
        self.assertIn("write_vulkan_descriptor_alias_report", source)
        self.assertIn("write_vulkan_binding_compact_report", source)
        self.assertIn('\\"f32_after_dispatch\\":', source)
        self.assertIn('\\"f32_after_writeback\\":', source)
        self.assertIn('\\"q6_row_indexed\\":true', source)
        self.assertIn('\\"q6_sample_indices\\":[', source)
        self.assertIn("collect_q6_row_indexed_sample_indices", source)
        self.assertIn("write_q6_row_indexed_f32_evidence", source)
        self.assertIn("q6_first_mismatch", source)
        self.assertIn("row_window", source)
        self.assertIn("Q6OutputLayoutProbeSample", source)
        self.assertIn("PDOCKER_GPU_Q6_OUTPUT_LAYOUT_PROBE_MAX_SAMPLES 32u", source)
        self.assertIn("PDOCKER_GPU_Q6_OUTPUT_LAYOUT_PROBE_MAX_FLOATS 4096u", source)
        self.assertIn(
            "q6_output_layout_probe_samples[\n"
            "        PDOCKER_GPU_Q6_OUTPUT_LAYOUT_PROBE_MAX_SAMPLES]",
            source,
        )
        self.assertIn("q6k_record_output_layout_probe_sample", source)
        self.assertIn("q6k_finalize_output_layout_probe", source)
        self.assertIn("write_q6_row_provenance_probe", source)
        self.assertIn('\\"q6_row_provenance_probe\\":', source)
        self.assertIn("row-provenance-inconsistent", source)
        self.assertIn("Q6PartialSignatureProbeSample", source)
        self.assertIn("PDOCKER_GPU_Q6_PARTIAL_SIGNATURE_PROBE_MAX_SAMPLES 32u", source)
        self.assertIn("q6k_record_partial_signature_probe_sample", source)
        self.assertIn("q6k_finalize_partial_signature_probe", source)
        self.assertIn("write_q6_partial_signature_probe", source)
        self.assertIn('\\"q6_partial_signature_probe\\":', source)
        self.assertIn('\\"native_reduction_tree_with_accumulator\\":%.9g', source)
        self.assertIn('\\"native_reduction_tree_gpu_abs_error\\":%.9g', source)
        self.assertIn('\\"expected_gpu_abs_error\\":%.9g', source)
        self.assertIn("local-y-partial", source)
        self.assertIn("lane-partial", source)
        self.assertIn("q6k_decode_batch_index(base_work_group_y, ne02, ne12, broadcast2, broadcast3", source)
        self.assertIn("weight_batch_stride_elements = load_le_u32(push, push_size, 4)", source)
        self.assertIn("const uint32_t ne02 = load_le_u32(push, push_size, 9)", source)
        self.assertIn("const uint32_t ne12 = load_le_u32(push, push_size, 10)", source)
        self.assertIn("const uint32_t broadcast2 = load_le_u32(push, push_size, 11)", source)
        self.assertIn("const uint32_t broadcast3 = load_le_u32(push, push_size, 12)", source)
        self.assertIn("llama.cpp's push-constant contract exactly", source)
        self.assertIn("canonical-mismatch-inconclusive", source)
        self.assertIn('\\"consistent_relative_offset\\":%s', source)
        self.assertIn("q6k_native_reduction_tree_sum32", source)
        self.assertIn('\\"q6_output_layout_probe\\":', source)
        self.assertIn('\\"source_spirv_hash\\":\\"0x%016llx\\"', source)
        self.assertIn('\\"effective_spirv_hash\\":\\"0x%016llx\\"', source)
        self.assertIn("write_f32_sample_array", source)
        self.assertIn("write_f32_fd_sample_array", source)
        self.assertIn("write_f32_sample_array_at_indices", source)
        self.assertIn("write_f32_fd_sample_array_at_indices", source)
        self.assertIn('\\"compact_summary\\":true', source)
        self.assertIn("write_spirv_feature_report(json_out(), &spirv_summary, &effective_rt, options);", source)
        self.assertIn("write_spirv_execution_report(json_out(),", source)
        self.assertIn('\\"spirv_local_size_resolved\\":[', source)
        self.assertIn('\\"specialization_entries\\":[', source)
        self.assertIn('\\"duplicate_descriptor_rewrite\\":%s', source)
        self.assertIn('\\"materialize_specialization\\":%s', source)
        self.assertIn('\\"descriptor_writes\\":[', source)
        self.assertIn('\\"descriptor_alias_map\\":[', source)
        self.assertIn('\\"target_id\\":%u', source)
        self.assertIn('\\"dst_binding\\":%u', source)
        self.assertIn('\\"source_index\\":%zu', source)
        self.assertIn('\\"source_binding\\":%u', source)
        self.assertIn('\\"alias_write\\":%s', source)
        self.assertIn("binding_group_span_seen[PDOCKER_GPU_MAX_VULKAN_BINDINGS]", source)
        self.assertIn("binding_alias_rep[i] != i", source)
        self.assertIn("i0 < j1 && j0 < i1", source)
        self.assertIn("!binding_group_span_seen[rep] || start < binding_group_base[rep]", source)
        self.assertIn("binding_descriptor_offset[PDOCKER_GPU_MAX_VULKAN_BINDINGS]", source)
        self.assertIn("infos[write_count].offset = (VkDeviceSize)binding_descriptor_offset[i];", source)
        self.assertIn("PDOCKER_GPU_STRICT_DEVICE_LOCAL_STAGING", source)
        self.assertIn("has_strict_device_local_staging", source)
        self.assertIn('"strict_device_local_staging", &options->has_strict_device_local_staging', source)
        self.assertIn("device_local_staged", source)
        self.assertIn("vkCmdCopyBuffer(command_buffer,", source)
        self.assertIn("device_local_staging_requested", source)
        self.assertIn("staging_upload_copies", source)
        self.assertIn("staging_download_copies", source)
        self.assertIn("PDOCKER_GPU_DISPATCH_TIMEOUT_MS", source)
        self.assertIn("vulkan-dispatch-timeout", source)
        self.assertIn("(strict_passthrough && !strict_device_local_staging) ? 0", source)
        self.assertIn("!binding_read_needed[i] || !vk_buffers[i]", source)
        self.assertIn("only the union of descriptor", source)
        self.assertIn("vulkan_vector_staging_offset", source)
        self.assertIn("sample_fd_hash(", source)
        self.assertIn("sample_memory_hash(", source)
        self.assertIn("binding_upload_ms[i] = now_ms() - binding_start;", source)
        self.assertIn("binding_download_ms[i] = now_ms() - binding_start;", source)

    def test_llama_gpu_compare_restarts_stale_daemon_and_checks_runtime_marker(self):
        compare = LLAMA_COMPARE.read_text()
        service = PDOCKERD_SERVICE.read_text()
        self.assertIn("RESTART_APP_DAEMON", compare)
        self.assertIn("restart_app_daemon_for_test", compare)
        self.assertIn("pkill -x pdocker-gpu-executor", compare)
        self.assertIn("avoids force-stopping", compare)
        self.assertNotIn("am force-stop", compare)
        self.assertIn("EXPECTED_GPU_EXECUTOR_MARKER", compare)
        self.assertIn("PDOCKER_GPU_EXECUTOR_EXPECTED_MARKER", compare)
        self.assertIn("EXPECTED_VULKAN_ICD_MARKER", compare)
        self.assertIn("PDOCKER_VULKAN_ICD_EXPECTED_MARKER", compare)
        self.assertIn("observed_icd_markers", compare)
        self.assertIn("runtime_freshness", compare)
        self.assertIn("runtime_freshness_mismatch", compare)
        self.assertIn(
            "(not expected_executor_marker or expected_executor_marker in observed_executor_markers) and",
            compare,
        )
        self.assertIn(
            "(not expected_icd_marker or expected_icd_marker in observed_icd_markers)",
            compare,
        )
        self.assertIn("executor_build_marker", compare)
        self.assertIn("runtime_marker", VULKAN_ICD.read_text())
        self.assertIn("while helper executors are gone", service)
        self.assertIn("killStaleSidecar", service)
        self.assertIn("pkill -f '$processName'", service)
        self.assertIn("@Synchronized\n    private fun startGpuExecutor", service)
        self.assertIn("startGpuExecutor(runtime)", service)
        self.assertIn("startMediaExecutor(runtime)", service)

    def test_llama_gpu_compare_tracks_critical_env_propagation(self):
        compare = LLAMA_COMPARE.read_text()
        verifier = load_llama_gpu_artifact_verifier()
        config_fields = dict(verifier.LLAMA_GPU_CONFIG_PROPAGATION_ENV_FIELDS)
        forward_envs = set(verifier.LLAMA_GPU_COMPARE_FORWARD_ENV_KEYS)
        for env_name, field_name in [
            ("PDOCKER_GPU_CPU_ORACLE", "cpu_oracle_requested"),
            ("PDOCKER_GPU_STRICT_PASSTHROUGH", "strict_passthrough"),
            ("PDOCKER_GPU_STRICT_DEVICE_LOCAL_STAGING", "strict_object_graph.device_local_staging_requested"),
            ("PDOCKER_GPU_Q6K_SAFE_KERNEL", "q6k_safe_kernel"),
            ("PDOCKER_GPU_Q6K_ORACLE_WRITEBACK", "cpu_oracle.oracle_writeback"),
            ("PDOCKER_GPU_Q4K_SAFE_KERNEL", "q4k_safe_kernel"),
            ("PDOCKER_GPU_Q4K_TARGETED_SPECIALIZATION", "q4k_targeted_specialization_materialized"),
            ("PDOCKER_GPU_Q4K_PIPELINE_RETRY_LADDER", "q4k_pipeline_retry_ladder"),
            ("PDOCKER_GPU_ADD_FLOAT16_CAPABILITY_FOR_STORAGE16", "float16_capability_added"),
            ("PDOCKER_GPU_MUTABLE_BUFFER_CACHE", "mutable_buffer_cache.enabled"),
            ("PDOCKER_GPU_MATERIALIZE_SPIRV_SPECIALIZATION_CONSTANTS", "materialize_specialization"),
            ("PDOCKER_GPU_USE_SPIRV_DESCRIPTOR_ACCESS", "spirv_descriptor_access"),
        ]:
            self.assertEqual(field_name, config_fields[env_name])
            self.assertIn(env_name, forward_envs)
        self.assertIn("llama-gpu-env-manifest.json", compare)
        self.assertIn("config_propagation_env_fields", compare)
        source = GPU_EXECUTOR.read_text()
        self.assertIn("strict_vulkan_passthrough_requested", source)
        self.assertIn("strict_vulkan_reconciliation_requested", source)
        self.assertIn("PDOCKER_GPU_STRICT_PASSTHROUGH", source)
        self.assertIn("PDOCKER_GPU_STRICT_RECONCILIATION", source)
        self.assertIn('\\"strict_passthrough\\":%s', source)
        self.assertIn('\\"requested_feature_mask\\":\\"0x%016llx\\"', source)
        self.assertIn("requested_feature_mask=", source)
        self.assertIn("spirv_required_feature_mask", source)
        self.assertIn("spirv_requested_feature_missing_mask", source)
        self.assertIn("spirv-feature-not-requested", source)
        self.assertIn("#define PDOCKER_GPU_PIPELINE_CACHE_SLOTS 256", source)
        self.assertIn("last_used = g_vulkan_pipeline_cache_clock++", source)
        self.assertIn('\\"pipeline_key\\":{\\"spirv_hash\\":\\"0x%016llx\\"', source)
        self.assertIn("only the mapped constant IDs and their referenced bytes participate", source)
        self.assertIn('env_truthy("PDOCKER_GPU_DISABLE_PIPELINE_OPTIMIZATION", 0)', source)
        self.assertIn('\\"mutable_buffer_cache\\":{\\"enabled\\":%s,\\"max_bytes\\":%zu}', source)
        self.assertIn('\\"q4k_targeted_specialization_materialized\\":%s', source)
        self.assertIn('\\"float16_capability_added\\":%s', source)
        self.assertIn("add_spirv_capability(&shader_code, &shader_size, 9)", source)
        icd_source = (ROOT / "docker-proot-setup/src/gpu/pdocker_vulkan_icd.c").read_text()
        self.assertIn("PDOCKER_GPU_ADD_FLOAT16_CAPABILITY_FOR_STORAGE16", icd_source)
        self.assertIn('"PDOCKER_GPU_ADD_FLOAT16_CAPABILITY_FOR_STORAGE16", "add_float16_capability_for_storage16"', icd_source)
        self.assertIn("pipeline->requested_feature_mask |= PDOCKER_VK_FEATURE_SHADER_FLOAT16", icd_source)
        self.assertIn('\\"q4k_safe_kernel\\":%s', source)
        self.assertIn("kQ4kSafeSpv", source)
        self.assertIn("kQ6kSafeSpv", source)
        self.assertIn("PDOCKER_GPU_Q6K_SAFE_KERNEL is an explicit diagnostic override", source)
        self.assertNotIn(
            "const int q6k_safe_kernel_requested =\n        strict_passthrough ? 0 :",
            source,
        )
        self.assertIn("PDOCKER_GPU_Q4K_SAFE_KERNEL is an explicit diagnostic override", source)
        self.assertNotIn(
            "const int q4k_safe_kernel_requested =\n        strict_passthrough ? 0 :",
            source,
        )
        q4_body = re.search(
            r"static int is_q4k_matvec_hash\(uint64_t spirv_hash\) \{(?P<body>.*?)\n}\n\nstatic const char \*cpu_oracle_kernel_hint",
            source,
            re.S,
        ).group("body")
        q6_body = re.search(
            r"static int is_q6k_matvec_hash\(uint64_t spirv_hash\) \{(?P<body>.*?)\n}\n\nstatic int is_q4k_matvec_hash",
            source,
            re.S,
        ).group("body")
        for q4_hash in [
            "0xf3cd7d18f0276b42ull",
            "0x853c49b4900eed3cull",
            "0x22ab0152b230e983ull",
        ]:
            self.assertIn(q4_hash, q4_body)
            self.assertNotIn(q4_hash, q6_body)
        self.assertIn('return "mul-mat-vec-q4-k-large";', source)
        self.assertIn("q4k_safe_kernel_used || is_q4k_matvec_hash(original_spirv_hash)", source)
        q4_safe_block = re.search(
            r"const int q4k_safe_kernel_requested =(?P<body>.*?)if \(q4k_safe_kernel_requested && is_q4k_matvec_hash",
            source,
            re.S,
        ).group("body")
        self.assertNotIn("strict_passthrough", q4_safe_block)
        self.assertIn('env_truthy("PDOCKER_GPU_Q4K_SAFE_KERNEL", 0)', q4_safe_block)
        self.assertIn("q4k_pipeline_retry_enabled = q4k_callsite_detected &&", source)
        self.assertIn('env_truthy("PDOCKER_GPU_Q4K_PIPELINE_RETRY_LADDER", 1)', source)
        self.assertGreaterEqual(source.count('\\"q4k_pipeline_retry_ladder\\":%s'), 3)
        self.assertGreaterEqual(source.count('\\"q4k_pipeline_retry_attempted\\":%s'), 3)
        q4_retry_block = re.search(
            r"if \(rc != VK_SUCCESS &&(?P<body>.*?)materialize_spirv_specialization_constants",
            source,
            re.S,
        ).group("body")
        self.assertIn("(!strict_passthrough || q4k_pipeline_retry_enabled)", q4_retry_block)
        self.assertIn("q4k_pipeline_retry_enabled", q4_retry_block)
        self.assertIn("strict passthrough", q4_retry_block)
        self.assertIn("Q4_K", q4_retry_block)
        self.assertIn("callsite_gated_config_envs", compare)
        self.assertIn('"PDOCKER_GPU_Q4K_PIPELINE_RETRY_LADDER"', compare)
        self.assertIn('observed_event_values(executor_events, "q4k_callsite_detected")', compare)
        self.assertIn("FORCED_VULKAN_WAIT_SERVER_TIMEOUT_SEC", compare)
        self.assertIn("PDOCKER_LLAMA_FORCED_VULKAN_WAIT_SERVER_TIMEOUT_SEC", compare)
        self.assertIn('wait_server "$FORCED_VULKAN_WAIT_SERVER_TIMEOUT_SEC" "Forced Vulkan"', compare)
        self.assertIn('"PDOCKER_GPU_DISABLE_PIPELINE_OPTIMIZATION": os.environ.get("PDOCKER_GPU_DISABLE_PIPELINE_OPTIMIZATION", "0")', PDOCKERD.read_text())

    def test_llama_gpu_lane_marker_and_scope_are_pinned(self):
        source = GPU_EXECUTOR.read_text()
        marker = re.search(
            r'#define PDOCKER_GPU_EXECUTOR_BUILD_MARKER "([^"]+)"',
            source,
        ).group(1)
        self.assertEqual(marker, "gpu-executor-llama-q4k-callsite-20260520")
        stale = "gpu-executor-" + "float16-cap-diagnostic-20260520"
        for path in [
            GPU_EXECUTOR,
            LLAMA_COMPARE,
            ROOT / "tests" / "test_gpu_abi_contract.py",
            ROOT / "tests" / "test_llama_gpu_artifact_verifier.py",
            ROOT / "tests" / "test_llama_gpu_artifact_sweep.py",
            LLAMA_GPU_NEXT_STEPS,
        ]:
            text = path.read_text()
            self.assertIn(marker, text)
            self.assertNotIn(stale, text)
        compare = LLAMA_COMPARE.read_text()
        self.assertIn(f'EXPECTED_GPU_EXECUTOR_MARKER="${{PDOCKER_GPU_EXECUTOR_EXPECTED_MARKER:-{marker}}}"', compare)
        self.assertIn(f"PDOCKER_GPU_EXECUTOR_EXPECTED_MARKER={{os.environ.get('PDOCKER_GPU_EXECUTOR_EXPECTED_MARKER', '{marker}')}}", compare)
        self.assertIn(f'expected_executor_marker = os.environ.get("PDOCKER_GPU_EXECUTOR_EXPECTED_MARKER", "{marker}")', compare)
        self.assertIn('"llama_cpp_modified": False', compare)
        self.assertIn('"gpu_entry": "standard Vulkan loader through pdocker-vulkan-icd.so"', compare)

        diff = subprocess.run(
            ["git", "diff", "--name-only"],
            cwd=ROOT,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.splitlines()
        forbidden_changed_paths = [
            path for path in diff
            if "llama.cpp" in path
            or path.endswith("Dockerfile")
            or "/Dockerfile" in path
        ]
        self.assertEqual([], forbidden_changed_paths)

    def test_q4k_callsite_handoff_records_required_evidence(self):
        doc = LLAMA_GPU_NEXT_STEPS.read_text()
        for evidence in [
            "gpu-executor-llama-q4k-callsite-20260520",
            "mul_mat_vec_q4_k_f32_f32",
            "vulkan-shaders/mul_mat_vec_q4_k.comp",
            "vk_mat_vec_push_constants",
            "A/B/D/Fuse0/Fuse1",
            "0xf3cd7d18f0276b42",
            "0x853c49b4900eed3c",
            "0x22ab0152b230e983",
            "explicit diagnostic override",
            "not a benchmarkable product optimization",
            "without changing llama.cpp",
            "Dockerfile",
        ]:
            self.assertIn(evidence, doc)

    def test_spirv_local_size_patch_uses_execution_mode_id_not_workgroup_builtin(self):
        source = GPU_EXECUTOR.read_text()
        self.assertIn("local_size_spec_id[3]", source)
        self.assertIn("local_size_spec_id_valid[3]", source)
        self.assertIn("op == 331 && word_count >= 6 && code[i + 2] == 38", source)
        self.assertIn("op == 71 && word_count >= 4 && code[i + 2] == 1", source)
        self.assertIn("summary->local_size_spec_id_valid[i]", source)
        self.assertIn("summary->local_size_spec_id[i]", source)
        self.assertIn('\\"spirv_local_size_spec_id\\":[%u,%u,%u]', source)
        function = re.search(
            r"static int patch_spirv_literal_local_size_from_spec\(.*?\n}\n\nstatic int rewrite_duplicate_descriptor_bindings",
            source,
            re.S,
        )
        self.assertIsNotNone(function)
        body = function.group(0)
        self.assertIn("summarize_spirv(code, bytes)", body)
        self.assertIn("summary.local_size_spec_id_valid[dim]", body)
        self.assertIn("summary.local_size_spec_id[dim]", body)
        self.assertNotIn("code[i + 2] == 11 && code[i + 3] == 25", body)
        self.assertNotIn("dim,", body)
        self.assertIn("constant_id=1 for NUM_ROWS, not LocalSizeY", source)
        summarize = re.search(
            r"static SpirvTraceSummary summarize_spirv\(.*?\n}\n\nstatic void log_vulkan_feature_trace",
            source,
            re.S,
        )
        self.assertIsNotNone(summarize)
        self.assertNotIn("calloc", summarize.group(0))
        self.assertNotIn("free(", summarize.group(0))

    def test_vulkan_duplicate_binding_rewrite_avoids_passed_bindings(self):
        source = GPU_EXECUTOR.read_text()
        self.assertIn("const VulkanDispatchBinding *bindings", source)
        self.assertIn("size_t binding_count", source)
        self.assertIn("used[bindings[i].binding] = 1;", source)
        self.assertIn("descriptor_sets[code[i + 1]] = code[i + 3];", source)
        self.assertIn("has_descriptor_set[code[i + 1]] = 1;", source)
        self.assertIn("!has_descriptor_set[code[i + 1]] || descriptor_sets[code[i + 1]] != 0", source)
        self.assertIn("rewrite_duplicate_descriptor_bindings(\n                shader_code,\n                shader_size,\n                bindings,\n                binding_count,", source)
        self.assertIn("strict_passthrough ? 0 :\n        options && options->has_rewrite_duplicate_descriptors", source)

    def test_vulkan_feature_contract_matches_android_subset(self):
        icd = VULKAN_ICD.read_text()
        executor = GPU_EXECUTOR.read_text()
        self.assertIn('env_truthy_default("PDOCKER_VULKAN_ENABLE_8BIT_STORAGE", true)', icd)
        self.assertIn("p->uniformAndStorageBuffer16BitAccess = VK_FALSE;", icd)
        self.assertIn("p->storagePushConstant16 = VK_FALSE;", icd)
        self.assertIn("p->shaderInt8 = storage8;", icd)
        self.assertIn("spirv_required_feature_mask", executor)
        self.assertIn("PDOCKER_VK_FEATURE_STORAGE_BUFFER_8", executor)
        self.assertIn("PDOCKER_VK_FEATURE_UNIFORM_STORAGE_BUFFER_8", executor)
        self.assertIn("PDOCKER_VK_FEATURE_STORAGE_PUSH_CONSTANT_8", executor)
        self.assertIn("PDOCKER_VK_FEATURE_SHADER_INT8", executor)
        self.assertIn("requires_storage16_uniform", executor)
        self.assertIn("requires_storage8_push_constant", executor)

    def test_fused_rms_rope_hashes_do_not_use_plain_rms_oracle(self):
        source = GPU_EXECUTOR.read_text()
        self.assertIn("rms-norm-rope-fused", source)
        self.assertIn("fused-rms-rope-oracle-pending", source)
        self.assertIn("0x4f37d4d51dd83526ull", source)
        self.assertIn("0x53c67d2aebf48739ull", source)

    def test_required_pending_or_unsupported_oracles_fail_closed(self):
        source = GPU_EXECUTOR.read_text()
        self.assertIn("cpu_oracle_status_is_unsupported", source)
        self.assertIn("cpu_oracle_required_fail_closed", source)
        self.assertIn('strstr(status, "oracle-pending")', source)
        self.assertIn('strstr(status, "not-implemented")', source)
        self.assertIn('strncmp(status, "unsupported", 11) == 0', source)
        self.assertIn("fail_stage = \"cpu-oracle-required\";", source)
        self.assertIn("rc = VK_ERROR_FEATURE_NOT_PRESENT;", source)
        self.assertIn("ret = 76;", source)
        self.assertIn('\\"oracle_fail_closed\\":%s', source)
        self.assertIn("write_cpu_oracle_report(json_out(), &cpu_oracle_report);", source)

    def test_rope_yarn_oracle_is_hash_gated_and_evidence_backed(self):
        source = GPU_EXECUTOR.read_text()
        self.assertIn("0xac41e8033a67af4aull", source)
        self.assertIn("cpu_oracle_known_llama_hash", source)
        self.assertIn('return "rope-yarn";', source)
        self.assertIn("static void run_cpu_oracle_rope_yarn", source)
        self.assertIn("init_cpu_oracle_report(report, report->requested, 0xac41e8033a67af4aull);", source)
        self.assertIn("rope_yarn_ramp(corr_low, corr_high", source)
        self.assertIn("push_size < 27 * sizeof(uint32_t)", source)
        self.assertIn("report->executed = 1;", source)
        self.assertIn('report->mismatch_count ? "mismatch" : "match"', source)
        self.assertIn("if (spirv_summary.hash == 0xac41e8033a67af4aull) {", source)
        self.assertIn("run_cpu_oracle_rope_yarn(&cpu_oracle_report", source)
        for report_field in [
            '\\"kernel_hint\\":\\"%s\\"',
            '\\"executed\\":%s',
            '\\"compared_floats\\":%zu',
            '\\"mismatch_count\\":%zu',
            '\\"first_mismatch\\":{\\"dst_index\\":%llu',
            '\\"samples\\":[',
        ]:
            self.assertIn(report_field, source)

        artifact = json.loads(ROPE_YARN_ARTIFACT.read_text())
        cpu_oracles = []

        def collect(obj):
            if isinstance(obj, dict):
                oracle = obj.get("cpu_oracle")
                if isinstance(oracle, dict) and oracle.get("kernel_hint") == "rope-yarn":
                    cpu_oracles.append(oracle)
                for value in obj.values():
                    collect(value)
            elif isinstance(obj, list):
                for value in obj:
                    collect(value)

        collect(artifact)
        self.assertTrue(cpu_oracles, "RoPE/Yarn evidence artifact must contain a rope-yarn oracle event")
        matched = [oracle for oracle in cpu_oracles if oracle.get("executed") is True and oracle.get("status") == "match"]
        self.assertTrue(matched, "RoPE/Yarn oracle must have executed match evidence")
        self.assertGreater(matched[0].get("compared_floats", 0), 0)
        self.assertEqual(0, matched[0].get("mismatch_count"))

        next_steps = LLAMA_GPU_NEXT_STEPS.read_text()
        correctness = LLAMA_GPU_CORRECTNESS.read_text()
        self.assertIn("Stage 3: RoPE/Yarn oracle for `0xac41e8033a67af4a` (completed)", next_steps)
        self.assertIn("docs/test/llama-gpu-ngl1-rope-yarn-oracle-20260509.json", next_steps)
        self.assertIn("0x274f68a67dfef210", next_steps)
        self.assertIn("Q6_K strict passthrough", next_steps)
        self.assertIn("memory readiness", next_steps)
        self.assertIn("RoPE/Yarn oracle is evidence-backed", correctness)

    def test_vulkan_specialization_constants_can_be_materialized(self):
        source = GPU_EXECUTOR.read_text()
        self.assertIn("materialize_spirv_specialization_constants", source)
        self.assertIn("PDOCKER_GPU_MATERIALIZE_SPIRV_SPECIALIZATION_CONSTANTS", source)
        self.assertIn('env_truthy("PDOCKER_GPU_MATERIALIZE_SPIRV_SPECIALIZATION_CONSTANTS", 0)', source)
        self.assertIn("vk_spec_ptr = specialization_materialized ? NULL : &vk_spec_info;", source)
        self.assertIn('\\"specialization_materialized\\":%s', source)
        self.assertIn("BuiltIn WorkgroupSize", source)
        self.assertIn("skip_spec_materialization", source)
        self.assertIn("code[i + 2] == 11 && code[i + 3] == 25", source)

    def test_vulkan_dispatch_can_skip_unused_descriptor_transfers(self):
        source = GPU_EXECUTOR.read_text()
        self.assertIn("collect_spirv_descriptor_bindings", source)
        self.assertIn("PDOCKER_GPU_SKIP_UNUSED_DESCRIPTOR_TRANSFERS", source)
        self.assertIn("PDOCKER_GPU_USE_SPIRV_DESCRIPTOR_ACCESS", source)
        self.assertIn("collect_spirv_descriptor_accesses", source)
        self.assertIn("type_non_readable", source)
        self.assertIn("type_non_writable", source)
        self.assertIn("op == 72", source)
        self.assertIn("pointer_target_by_id", source)
        self.assertIn("variable_type_by_id", source)
        self.assertIn("uint8_t active_bindings[PDOCKER_GPU_MAX_VULKAN_BINDINGS]", source)
        self.assertIn("uint8_t binding_read_needed[PDOCKER_GPU_MAX_VULKAN_BINDINGS]", source)
        self.assertIn("uint8_t binding_write_needed[PDOCKER_GPU_MAX_VULKAN_BINDINGS]", source)
        self.assertIn("if (!active_bindings[i]) continue;", source)
        self.assertIn("if (!binding_write_needed[i]) continue;", source)
        self.assertIn('\\"descriptor_usage\\":{\\"active_bindings\\":%zu,', source)
        self.assertIn('\\"read_bindings\\":%zu,\\"write_bindings\\":%zu,', source)
        self.assertIn('\\"skipped_upload_bytes\\":%zu,\\"skipped_download_bytes\\":%zu}', source)
        self.assertIn('\\"fail_binding_index\\":%d,\\"io_result\\":%d,', source)
        self.assertIn("if (profile_response) {", source)
        self.assertIn("write_vulkan_binding_report(json_out(), bindings, binding_count,", source)
        self.assertIn("active_bindings", source)
        self.assertIn("binding_read_needed, binding_write_needed", source)
        self.assertIn("cache_hits, cache_resident", source)

    def test_vulkan_copy_submit_profile_is_recorded(self):
        source = VULKAN_ICD.read_text()
        self.assertIn("copy-submit summary ops=%zu alias_ops=%zu memmove_ops=%zu skipped_ops=%zu", source)
        for field in [
            "alias_bytes",
            "memmove_bytes",
            "skipped_bytes",
            "copy_alias_candidate(alias_memory)",
        ]:
            self.assertIn(field, source)
        compare = LLAMA_COMPARE.read_text()
        for field in [
            "copy_submit_alias_ops",
            "copy_submit_memmove_ops",
            "copy_submit_skipped_ops",
            "copy_submit_alias_bytes",
            "copy_submit_memmove_bytes",
        ]:
            self.assertIn(field, compare)

    def test_vulkan_descriptor_range_is_scoped_to_vkbuffer_not_allocation(self):
        source = VULKAN_ICD.read_text()
        self.assertIn("descriptor ranges are scoped to the VkBuffer", source)
        self.assertIn("binding->offset > binding->buffer->size", source)
        self.assertIn("available_in_buffer = binding->buffer->size - (size_t)binding->offset", source)
        self.assertIn("if (binding->range == VK_WHOLE_SIZE) return available_in_buffer;", source)
        descriptor_size = source.split("static size_t descriptor_binding_size(const PdockerVkDescriptorBinding *binding) {", 1)[1].split("\n}", 1)[0]
        self.assertNotIn("buffer_available(binding->buffer, binding->offset)", descriptor_size)

    def test_strict_passthrough_preserves_vkbuffer_coordinate_space(self):
        source = GPU_EXECUTOR.read_text() + "\n" + VULKAN_ICD.read_text()
        for marker in [
            "Strict passthrough keeps the application's VkBuffer coordinate",
            "VULKAN_DISPATCH_V3",
            "VULKAN_DISPATCH_V4",
            "api_memory_sizes[binding_count]",
            "api_memory_ids[binding_count]",
            "api_buffer_ids[binding_count]",
            "api_descriptor_sets[binding_count]",
            "bindings[i].descriptor_set",
            "VulkanStrictMemoryObject",
            "VulkanStrictBufferObject",
            "create_strict_vulkan_object_graph",
            "vkBindBufferMemory(device, buffers[b].buffer, memory->memory, buffers[b].memory_offset)",
            "strict_object_graph",
            "unsupported_descriptor_set_layout",
            "descriptor-set-index-out-of-range",
            "VkDescriptorSetLayout set_layouts[PDOCKER_GPU_MAX_VULKAN_DESCRIPTOR_SETS]",
            "VkDescriptorSet descriptor_sets[PDOCKER_GPU_MAX_VULKAN_DESCRIPTOR_SETS]",
            ".setLayoutCount = descriptor_set_count",
            ".pSetLayouts = set_layouts",
            ".descriptorSetCount = descriptor_set_count",
            "vkAllocateDescriptorSets(rt->device, &dsai, descriptor_sets)",
            "writes[write_count].dstSet = descriptor_sets[bindings[i].descriptor_set]",
            "descriptor_set_count,\n                            descriptor_sets",
            "binding_object_base[PDOCKER_GPU_MAX_VULKAN_BINDINGS]",
            "binding_object_end[PDOCKER_GPU_MAX_VULKAN_BINDINGS]",
            "object_base = bindings[i].api_memory_offset;",
            "object_end = bindings[i].api_memory_offset + (off_t)bindings[i].api_buffer_size;",
            "const off_t i0 = strict_passthrough ? binding_object_base[i] : bindings[i].offset;",
            "const off_t j0 = strict_passthrough ? binding_object_base[j] : bindings[j].offset;",
            "const off_t start = strict_passthrough ? binding_object_base[i] : bindings[i].offset;",
            "descriptor_absolute = bindings[i].api_memory_offset + bindings[i].api_offset;",
            "binding_gpu_offset[i] = (size_t)(descriptor_absolute - binding_group_base[rep]);",
            ": (size_t)descriptor_absolute;",
            "binding_descriptor_offset[i] = (size_t)bindings[i].api_offset;",
            "VkDescriptorBufferInfo.offset",
            "object-graph coordinate fidelity",
        ]:
            self.assertIn(marker, source)

    def test_vulkan_command_buffer_replays_all_recorded_dispatches(self):
        source = VULKAN_ICD.read_text()
        for marker in [
            "PDOCKER_VK_MAX_DISPATCH_OPS",
            "PDOCKER_VK_MAX_COMMAND_OPS",
            "PdockerVkDispatchOp dispatch_ops[PDOCKER_VK_MAX_DISPATCH_OPS]",
            "PdockerVkCommandOp command_ops[PDOCKER_VK_MAX_COMMAND_OPS]",
            "cmd->dispatch_op_count = 0;",
            "clear_recorded_command_ops(cmd)",
            "append_command_op(cmd, &command_op)",
            "append_command_op(cmd, &op)",
            "PdockerVkDispatchOp *op = &cmd->dispatch_ops[op_index];",
            "send_generic_vulkan_dispatch_op",
            "for (uint32_t op_index = 0; op_index < cmd->dispatch_op_count; ++op_index)",
            "for (uint32_t op_index = 0; op_index < cmd->command_op_count; ++op_index)",
            "execute_recorded_copy_op(&cmd->copy_ops[op->index], &stats)",
            "execute_recorded_fill_op(op)",
            "execute_recorded_update_op(op)",
            "queue-submit replayed ordered ops=%u dispatches=%u",
            "queue-submit replayed dispatch ops=%u",
        ]:
            self.assertIn(marker, source)
        submit_body = source.split("VKAPI_ATTR VkResult VKAPI_CALL vkQueueSubmit", 1)[1].split("VKAPI_ATTR VkResult VKAPI_CALL vkWaitForFences", 1)[0]
        self.assertIn("cmd->command_op_count > 0", submit_body)
        self.assertIn("send_generic_vulkan_dispatch_op(dispatch)", submit_body)
        self.assertIn("cmd->dispatch_op_count > 0", submit_body)
        self.assertIn("send_generic_vulkan_dispatch_op(op)", submit_body)

    def test_vulkan_guarded_memory_profile_is_recorded(self):
        source = VULKAN_ICD.read_text()
        for marker in [
            "guarded_sigsegv_handler",
            "guarded_page_count",
            "trace_guarded_binding",
            "resident_pages=%zu dirty_pages=%zu",
            "trace_guarded_binding(i, dispatch_memory, dispatch_offset, bytes)",
        ]:
            self.assertIn(marker, source)
        compare = LLAMA_COMPARE.read_text()
        for field in [
            "guarded_binding_samples",
            "guarded_binding_max_resident_bytes",
            "guarded_binding_max_dirty_bytes",
            "guarded_binding_max_range_bytes",
        ]:
            self.assertIn(field, compare)

    def test_llama_compare_summarizes_binding_timing(self):
        compare = LLAMA_COMPARE.read_text()
        for field in [
            "binding_timing_samples",
            "binding_upload_ms_max",
            "binding_download_ms_max",
            "top_binding_uploads",
            "top_binding_downloads",
            "largest_shader_events",
            "largest_binding_events",
            "gpu_correctness_mismatch",
        ]:
            self.assertIn(field, compare)
        self.assertIn('detail.get("upload_ms")', compare)
        self.assertIn('detail.get("download_ms")', compare)

    def test_llama_compare_records_server_token_probabilities(self):
        compare = LLAMA_COMPARE.read_text()
        for marker in [
            "PDOCKER_LLAMA_N_PROBS",
            '"completion_probabilities": n_probs > 0',
            '"n_probs": n_probs',
            '"selected_token"',
            '"top_logprobs"',
            "def probe_probability_map(report):",
            "differential_probabilities",
            "cpu_selected_rank_in_gpu_top",
            "gpu_selected_rank_in_cpu_top",
            "shared_top_token_ids",
            "top1_mismatch_count",
            "selected_token_mismatch_count",
        ]:
            self.assertIn(marker, compare)

    def test_llama_compare_records_bisection_and_config_propagation(self):
        compare = LLAMA_COMPARE.read_text()
        compare_and_manifest = compare + "\n" + LLAMA_GPU_ENV_MANIFEST.read_text()
        for marker in [
            "config_propagation",
            "config_expectations",
            "config_propagation_mismatch",
            "observed_event_values(executor_events",
            "disable_pipeline_optimization",
            "skip_unused_descriptor_transfers",
            "spirv_descriptor_access",
            "disable_overlap_aliasing",
            "diagnostic_bisection",
            "binary-search fault isolation",
            "api_cpu_baseline",
            "gpu_server_output",
            "token_probability_boundary",
            "executor_dispatch_boundary",
            "post_dispatch_logits",
            "readonly_input_integrity",
            "readonly_binding_hash_mismatches",
            "primary_readonly_upload_hash_mismatches",
            "primary_readonly_dispatch_mutations",
            "shader_access_or_barrier_scope",
            "alias_rep",
            "upload_offset_descriptor_or_hash_scope",
            "output_layout_or_shader_math",
            "numeric_layout_or_readback",
            "q6_blocker_class",
            "q6_shader_like_oracle_cleared",
            "q6_first_mismatch",
            "q6_row_indexed_sample_indices",
            "q6_row_indexed_writeback_evidence",
            "q6_row_indexed_writeback_verified",
            "row_indexed_samples_match_oracle",
            "q6_writable_bindings",
            "q6_readonly_upload_hash_mismatches",
            "q6_readonly_dispatch_mutations",
            "writeback_offset",
            "writeback_bytes",
            "device_local_staged",
            "vulkan-device-execution-or-writeback",
            "descriptor-effective-range-or-upload",
        ]:
            self.assertIn(marker, compare_and_manifest)
        source = GPU_EXECUTOR.read_text()
        self.assertIn('\\"disable_pipeline_optimization\\":%s', source)
        self.assertIn('\\"skip_unused_descriptor_transfers\\":%s', source)
        self.assertIn('\\"spirv_descriptor_access\\":%s', source)
        self.assertIn('\\"disable_overlap_aliasing\\":%s', source)
        self.assertIn("uint64_t policy_hash", source)
        self.assertIn("vulkan_pipeline_policy_hash", source)
        self.assertIn("entry->policy_hash == policy_hash", source)
        self.assertIn('\\"pipeline_policy_hash\\":\\"0x%016llx\\"', source)
        self.assertIn('\\"api_offset\\":%lld', source)
        self.assertIn('\\"api_range\\":%zu', source)
        self.assertIn('\\"api_descriptor_type\\":%u', source)
        self.assertIn("write_spirv_binding_reflection_report", source)
        self.assertIn("write_cpu_oracle_report", source)
        self.assertIn("triage must not require turning on every descriptor dump", source)
        self.assertIn("cpu_oracle_known_llama_hash", source)
        self.assertIn("write_vulkan_rw_alias_hazard_report", source)
        self.assertIn('\\"rw_alias_hazards\\":{\\"count\\":%zu', source)
        self.assertIn('\\"push_u32\\":[', source)
        self.assertIn("run_cpu_oracle_q6k_matvec_sample", source)
        self.assertIn("mul-mat-vec-q6-k-large", source)
        self.assertIn("0xbefdfb97e9734eb3ull", source)
        self.assertIn('\\"partial_diagnostic\\":', source)
        self.assertIn("patch_spirv_literal_local_size_from_spec", source)
        self.assertIn("legalizes the execution", source)
        self.assertIn("found_local_size_spec", source)
        self.assertIn("local_size[dim] = value;", source)
        self.assertIn("const uint64_t invocation_count", source)
        self.assertIn('\\"local_size_patched\\":%s', source)
        self.assertIn('\\"spirv_local_size\\":[%u,%u,%u]', source)
        self.assertIn('\\"spirv_local_size_resolved\\":[%llu,%llu,%llu]', source)
        self.assertIn('\\"spirv_local_size_consistent\\":%s', source)
        self.assertIn("spirv_local_size_consistent(", source)
        self.assertIn("spirv-local-size-inconsistent", source)
        self.assertIn("strict passthrough refused", source)
        self.assertIn("spirv_local_invocation_count", source)
        self.assertIn("product > UINT64_MAX / local_size[i]", source)
        self.assertIn("invalid-q6-local-size", source)
        self.assertIn('\\"q6_local_size\\":[%llu,%llu,%llu]', source)
        self.assertIn('\\"q6_local_invocations\\":%llu', source)
        self.assertIn('\\"q6_accum_mask\\":%llu', source)
        self.assertIn('\\"q6_base_work_group_y\\":%llu', source)
        self.assertIn('\\"q6_output_base_index\\":%llu', source)
        self.assertIn('\\"q6_weight_base_blocks\\":%llu', source)
        self.assertIn('\\"q6_accumulator_sum\\":%.9g', source)
        self.assertIn("load_le_u32(push, push_size, 7)", source)
        self.assertIn("load_le_u32(push, push_size, 8)", source)
        self.assertIn("q6k_read_accumulator_value", source)
        self.assertIn('\\"q6_shader_like_64_sum\\":%.9g', source)
        self.assertIn("q6_resolved_local_size", source)
        self.assertIn("q6_diag_lanes", source)
        self.assertIn('\\"cpu_oracle_requested\\":%s', source)
        self.assertIn('\\"pre_barriers\\":%u,\\"post_barriers\\":%u', source)
        self.assertIn("vkCmdPipelineBarrier(command_buffer,", source)
        self.assertIn("VK_ACCESS_HOST_WRITE_BIT", source)
        self.assertIn("VK_ACCESS_SHADER_WRITE_BIT", source)
        self.assertIn('\\"gpu_after_upload_hash\\":\\"0x%016llx\\"', source)
        self.assertIn("const int disable_pipeline_optimization =", source)

    def test_llama_gpu_artifact_gate_decision_tree_is_documented(self):
        verifier = LLAMA_GPU_ARTIFACT_VERIFIER.read_text()
        runbook = LLAMA_GPU_DEVICE_RUNBOOK.read_text()
        next_steps = LLAMA_GPU_NEXT_STEPS.read_text()
        for marker in [
            "oracle-fail-closed",
            "api-prompt-sanity-missing",
            "speedup-fields-missing",
            "REQUIRED_API_PROMPT_PROBES",
            "oracle_fail_closed",
            "cpu-oracle-required",
            "comparison.target_tokens_per_second",
            "bridge_overhead_phase",
            "q6_row_indexed_sample_indices",
            "q6_row_indexed_writeback_evidence",
            "q6_row_indexed_writeback_verified",
            "row_indexed_samples_match_oracle",
            "row_window/q6_first_mismatch dst indices",
            "Generic or exact-index f32 samples",
        ]:
            self.assertIn(marker, verifier + "\n" + runbook + "\n" + next_steps)
        for marker in [
            "Artifact Gate Decision Tree",
            "CPU baseline rule",
            "Web/API prompt sanity",
            "Speedup fields",
            "37 oracle fail-closed",
            "38 API prompt sanity missing",
            "39 speedup fields missing",
        ]:
            self.assertIn(marker, runbook)

    def test_row_indexed_q6_next_blocker_decision_tree_is_documented(self):
        docs = LLAMA_GPU_NEXT_STEPS.read_text() + "\n" + LLAMA_GPU_CORRECTNESS.read_text()
        for marker in [
            "Row-indexed Q6_K device-run decision tree",
            "Row-indexed Q6_K Next-Blocker Decision Tree",
            "memory-blocked",
            "insufficient_memory",
            "runtime_memory_pressure",
            "device_memory_blocked:true",
            "does not justify a C-side Q6 change",
            "q6_row_indexed_writeback_evidence",
            "q6_row_indexed_writeback_verified",
            "q6_writeback_verified_all",
            "f32_after_dispatch",
            "f32_after_writeback",
            "q6_row_indexed_sample_indices",
            "classify the next blocker as `writeback`",
            "writeback is verified + the Q6 oracle still mismatches",
            "q6_writeback_verified_all == true",
            "q6_row_indexed_writeback_verified == true",
            "latest_status == \"mismatch\"",
            "workgroup_shape_blocker == true",
            "spirv_local_size_consistent",
            "spirv_local_size_resolved",
            "[32,2,1]",
            "workgroup-shape",
            "q6_shader_like_64_abs_delta",
            "Vulkan device-execution",
            "Q6 arithmetic/reduction/output-layout",
            "A sampled mismatch without row-indexed writeback evidence is not progress.",
        ]:
            self.assertIn(marker, docs)

    def test_llama_gpu_compare_can_forward_bridge_tuning_env(self):
        compare = LLAMA_COMPARE.read_text()
        self.assertIn("import os", compare)
        self.assertIn("def set_env(env, item):", compare)
        self.assertIn("env[idx] = item", compare)
        self.assertIn("set_env(env, f\"{key}={value}\")", compare)

    def test_llama_gpu_compare_memory_preflight_uses_available_memory(self):
        compare = LLAMA_COMPARE.read_text()
        self.assertIn("MemAvailable", compare)
        self.assertIn('"mem_available_mb"', compare)
        self.assertIn('"mem_preflight_free_mb"', compare)
        self.assertIn('data.get("mem_preflight_free_mb")', compare)
        self.assertIn('wait_for_memory_headroom "preflight before daemon start"', compare)
        preflight_call = compare.index('wait_for_memory_headroom "preflight before daemon start"')
        self.assertLess(
            preflight_call,
            compare.index("restart_app_daemon_for_test", preflight_call),
        )
        self.assertIn("runtime_memory_headroom_ok", compare)
        self.assertIn('CORRECTNESS_TIMEOUT_SEC="${PDOCKER_LLAMA_CORRECTNESS_TIMEOUT_SEC:-180}"', compare)
        self.assertIn("correctness probe", compare)
        self.assertIn("timeout={timeout_sec}s", compare)
        self.assertIn("RUNTIME_MIN_SWAP_FREE_MB", compare)
        self.assertIn('MIN_SWAP_FREE_MB="${PDOCKER_LLAMA_MIN_SWAP_FREE_MB:-0}"', compare)
        self.assertIn('SWAP_ADVISORY_MB="${PDOCKER_LLAMA_SWAP_ADVISORY_MB:-1024}"', compare)
        self.assertIn('WAIT_FOR_MEMORY_SEC="${PDOCKER_LLAMA_WAIT_FOR_MEMORY_SEC:-0}"', compare)
        self.assertIn("wait_for_memory_headroom", compare)
        self.assertIn('RUNTIME_MIN_SWAP_FREE_MB="${PDOCKER_LLAMA_RUNTIME_MIN_SWAP_FREE_MB:-0}"', compare)
        self.assertIn('RUNTIME_SWAP_ADVISORY_MB="${PDOCKER_LLAMA_RUNTIME_SWAP_ADVISORY_MB:-512}"', compare)
        self.assertIn("hard_gate_enabled", compare)
        self.assertIn("swap_pressure_advisory", compare)
        self.assertIn("SwapFree is advisory by default", compare)
        self.assertIn('STOP_ON_FAILURE="${PDOCKER_LLAMA_STOP_ON_FAILURE:-1}"', compare)
        self.assertIn('ENGINE_START_TIMEOUT_SEC="${PDOCKER_LLAMA_ENGINE_START_TIMEOUT_SEC:-15}"', compare)
        self.assertIn('ENGINE_CREATE_TIMEOUT_SEC="${PDOCKER_LLAMA_ENGINE_CREATE_TIMEOUT_SEC:-120}"', compare)
        self.assertIn('ENGINE_CREATE_SETTLE_TIMEOUT_SEC="${PDOCKER_LLAMA_ENGINE_CREATE_SETTLE_TIMEOUT_SEC:-60}"', compare)
        self.assertIn('ENGINE_CREATE_POLL_INTERVAL_SEC="${PDOCKER_LLAMA_ENGINE_CREATE_POLL_INTERVAL_SEC:-2}"', compare)
        self.assertIn('ENGINE_CLEANUP_TIMEOUT_SEC="${PDOCKER_LLAMA_ENGINE_CLEANUP_TIMEOUT_SEC:-60}"', compare)
        self.assertIn('RUN_AS_TIMEOUT_SEC="${PDOCKER_LLAMA_RUN_AS_TIMEOUT_SEC:-30}"', compare)
        self.assertIn('RUN_AS_CLEANUP_TIMEOUT_SEC="${PDOCKER_LLAMA_RUN_AS_CLEANUP_TIMEOUT_SEC:-5}"', compare)
        self.assertIn('timeout "${run_timeout}s" "$ADB" push', compare)
        self.assertIn('timeout "${cleanup_timeout}s" "$ADB" shell', compare)
        self.assertIn("|| rc=$?", compare)
        self.assertIn('RUN_AS_TIMEOUT_SEC="$timeout_sec" engine_request "$@"', compare)
        self.assertNotIn("timeout \"${timeout_sec}s\" bash -c 'engine_request", compare)
        self.assertIn('OPERATION_NOTIFY_TIMEOUT_SEC="${PDOCKER_LLAMA_OPERATION_NOTIFY_TIMEOUT_SEC:-3}"', compare)
        self.assertIn('WAIT_SERVER_PROGRESS_INTERVAL_SEC="${PDOCKER_LLAMA_WAIT_SERVER_PROGRESS_INTERVAL_SEC:-10}"', compare)
        self.assertIn('WAIT_SERVER_CURL_TIMEOUT_SEC="${PDOCKER_LLAMA_WAIT_SERVER_CURL_TIMEOUT_SEC:-2}"', compare)
        self.assertIn('COMPARE_ARTIFACT_DIR="${PDOCKER_LLAMA_COMPARE_ARTIFACT_DIR:-}"', compare)
        self.assertIn("record_wait_server_event", compare)
        self.assertIn("wait-server.jsonl", compare)
        self.assertIn("toybox nc -U -W $OPERATION_NOTIFY_TIMEOUT_SEC", compare)
        self.assertIn('FORCED_VULKAN_WAIT_SERVER_TIMEOUT_SEC="${PDOCKER_LLAMA_FORCED_VULKAN_WAIT_SERVER_TIMEOUT_SEC:-240}"', compare)
        self.assertIn('wait_server "$FORCED_VULKAN_WAIT_SERVER_TIMEOUT_SEC" "Forced Vulkan"', compare)
        self.assertIn("waiting for $phase server", compare)
        self.assertIn("$mode: creating container", compare)
        self.assertIn('engine_request_with_host_timeout "$ENGINE_CLEANUP_TIMEOUT_SEC" DELETE', compare)
        self.assertIn('engine_request_with_host_timeout "$ENGINE_CREATE_TIMEOUT_SEC" POST "/containers/create', compare)
        self.assertIn("create request did not return", compare)
        self.assertIn('inspect_container_body "$CONTAINER"', compare)
        self.assertIn("poll_container_after_create_timeout", compare)
        self.assertIn("delayed create became inspectable", compare)
        self.assertIn("waiting for delayed create visibility", compare)
        self.assertIn("wait_container_absent", compare)
        self.assertIn("stale target container still inspectable", compare)
        self.assertIn("remove_container_after_failure", compare)
        self.assertIn('STOP_STALE_TARGET_BEFORE_PREFLIGHT="${PDOCKER_LLAMA_STOP_STALE_TARGET_BEFORE_PREFLIGHT:-1}"', compare)
        self.assertIn("stop_stale_target_if_engine_alive", compare)
        self.assertIn("Do not start", compare)
        self.assertIn("engine_request_with_host_timeout", compare)
        self.assertIn("continuing with runtime watchdog", compare)
        self.assertIn("engine-start-timeout", compare)
        self.assertIn('"runtime_memory_pressure"', compare)
        self.assertIn('"runtime_abort": runtime_abort', compare)
        self.assertIn('"device_actions"', compare)
        self.assertIn("do not force-stop the browser/VS Code session", compare)
        self.assertIn("do not rebuild the llama image", compare)
        self.assertIn('cp "$RUNTIME_ABORT_JSON" "$OUT"', compare)
        self.assertIn("remove_container >/dev/null 2>&1 || true", compare)
        self.assertIn("q6_workgroup_diagnostics", compare)
        self.assertIn("workgroup_shape_blocker", compare)
        self.assertIn("fix Q6_K three-dimensional workgroup shape propagation", compare)
        self.assertIn("q6_accum_mask", compare)
        self.assertIn("q6_base_work_group_y", compare)
        self.assertIn("q6_output_base_index", compare)
        self.assertIn("q6_weight_base_blocks", compare)
        self.assertIn("q6_shader_like_64_abs_delta", compare)
        self.assertIn("q6_native_reduction_tree_abs_delta", compare)
        self.assertIn("q6_native_reduction_tree_gpu_abs_error", compare)
        self.assertIn("q6_output_layout_probe", compare)
        self.assertIn("q6_native_spirv_identity", compare)
        self.assertIn("native-q6-output-layout", compare)
        self.assertIn("native-q6-output-layout-inconclusive", compare)
        self.assertIn("native-q6-reduction-or-device-execution", compare)
        self.assertIn("q6_writable_writeback_mismatches", compare)
        self.assertIn("f32_after_writeback", compare)
        self.assertIn("f32_sample_values", compare)
        self.assertIn("q6_oracle_sample_indices", compare)
        self.assertIn("q6_row_indexed_samples_match_oracle", compare)
        self.assertIn("row_window/q6_first_mismatch dst indices", compare)
        self.assertIn("q6_writeback_verified_all", compare)
        self.assertIn("vulkan-device-execution\"", compare)
        self.assertIn("writeback_verified", GPU_EXECUTOR.read_text())
        self.assertIn("writeback_mismatch", GPU_EXECUTOR.read_text())
        forward_envs = set(load_llama_gpu_artifact_verifier().LLAMA_GPU_COMPARE_FORWARD_ENV_KEYS)
        for key in [
            "PDOCKER_GPU_DISABLE_PIPELINE_OPTIMIZATION",
            "PDOCKER_GPU_MUTABLE_BUFFER_CACHE_MAX_BYTES",
            "PDOCKER_GPU_DISABLE_OVERLAP_ALIASING",
            "PDOCKER_GPU_CPU_ORACLE",
            "PDOCKER_GPU_RESIDENT_CACHE_MIN_BYTES",
            "PDOCKER_GPU_SKIP_UNUSED_DESCRIPTOR_TRANSFERS",
            "PDOCKER_GPU_USE_SPIRV_DESCRIPTOR_ACCESS",
            "PDOCKER_GPU_WRITEONLY_BUFFER_CACHE",
            "PDOCKER_GPU_WRITEONLY_DIRTY_PROBE",
            "PDOCKER_GPU_WRITEONLY_DIRTY_PROBE_MIN_BYTES",
            "PDOCKER_GPU_WRITEONLY_DIRTY_WRITEBACK",
            "PDOCKER_GPU_UNSAFE_DIRTY_WRITEBACK_CACHE",
            "PDOCKER_GPU_DISPATCH_PROFILE_RESPONSE",
            "PDOCKER_GPU_MUTABLE_BUFFER_CACHE",
            "PDOCKER_GPU_VIRTUAL_MEMORY",
            "PDOCKER_GPU_VIRTUAL_MEMORY_MIN_BYTES",
            "PDOCKER_GPU_DISABLE_ANDROID_VULKAN",
            "PDOCKER_GPU_DISABLE_ANDROID_OPENCL",
            "PDOCKER_GPU_CHAIN_COMPAT_FEATURE_STRUCTS",
            "PDOCKER_GPU_RETRY_MATERIALIZE_SPECIALIZATION",
            "PDOCKER_GPU_LEGALIZE_WORKGROUP_SIZE_FROM_SPEC",
            "PDOCKER_GPU_STRICT_DEVICE_LOCAL_STAGING",
            "PDOCKER_ANDROID_OPENCL_LIBRARY",
            "PDOCKER_VULKAN_HEAP_BYTES",
            "PDOCKER_VULKAN_ICD_DEBUG",
            "PDOCKER_VULKAN_SUBGROUP_SIZE",
        ]:
            self.assertIn(key, forward_envs)
        self.assertIn("compare_forward_env_keys", compare)
        self.assertIn("value = os.environ.get(key)", compare)
        self.assertIn("PDOCKER_LLAMA_MIN_FREE_MB", compare)
        self.assertIn("PDOCKER_LLAMA_MIN_SWAP_FREE_MB", compare)
        self.assertIn("ensure_memory_headroom", compare)
        self.assertIn("insufficient memory before", compare)
        self.assertIn("toybox nc -U -W 3 pdocker/pdockerd.sock", compare)
        source = GPU_EXECUTOR.read_text()
        for marker in [
            "apply_vulkan_feature_policy",
            "effective_vulkan_runtime_for_dispatch",
            "append_vulkan_device_extension",
            "VK_KHR_8BIT_STORAGE_EXTENSION_NAME",
            "VK_KHR_SHADER_FLOAT16_INT8_EXTENSION_NAME",
            "PDOCKER_VULKAN_DISABLE_8BIT_STORAGE",
            "PDOCKER_VULKAN_DISABLE_16BIT_STORAGE",
            "PDOCKER_VULKAN_DISABLE_SUBGROUP_ARITHMETIC",
        ]:
            self.assertIn(marker, source)
        icd = VULKAN_ICD.read_text()
        self.assertIn('#define PDOCKER_VULKAN_ICD_BUILD_MARKER "vulkan-icd-feature-chain-marker-20260518"', icd)
        self.assertIn("trace_icd_runtime_marker_once", icd)
        self.assertIn('trace_icd_runtime_marker_once("create-instance");', icd)
        self.assertIn('trace_icd_runtime_marker_once("create-device");', icd)
        self.assertIn("rc=0", icd)
        self.assertIn("requested_feature_mask_from_device_create_info", icd)
        self.assertIn("feature_mask_from_pnext_chain", icd)
        self.assertIn("VkDeviceCreateInfo::pNext and hang the actual 1.1/1.2/extension feature", icd)
        self.assertIn("mask |= feature_mask_from_pnext_chain(pCreateInfo->pNext);", icd)
        self.assertIn("pipeline->requested_feature_mask", icd)
        self.assertIn("requested_feature_mask=%llu", icd)
        for extension in [
            "VK_KHR_8BIT_STORAGE_EXTENSION_NAME",
            "VK_KHR_SHADER_FLOAT16_INT8_EXTENSION_NAME",
            "VK_KHR_STORAGE_BUFFER_STORAGE_CLASS_EXTENSION_NAME",
            "ADD_DEVICE_EXTENSION(VK_KHR_8BIT_STORAGE_EXTENSION_NAME",
            "ADD_DEVICE_EXTENSION(VK_KHR_SHADER_FLOAT16_INT8_EXTENSION_NAME",
            "ADD_DEVICE_EXTENSION(VK_KHR_STORAGE_BUFFER_STORAGE_CLASS_EXTENSION_NAME",
        ]:
            self.assertIn(extension, icd)
        for env_name, option_name in [
            ("PDOCKER_GPU_REWRITE_DUPLICATE_DESCRIPTOR_BINDINGS", "rewrite_duplicate_descriptors"),
            ("PDOCKER_GPU_MUTABLE_BUFFER_CACHE", "mutable_cache"),
            ("PDOCKER_GPU_Q4K_SAFE_KERNEL", "q4k_safe_kernel"),
            ("PDOCKER_GPU_Q4K_TARGETED_SPECIALIZATION", "q4k_targeted_specialization"),
            ("PDOCKER_GPU_STRICT_PASSTHROUGH", "strict_passthrough"),
            ("PDOCKER_GPU_STRICT_RECONCILIATION", "strict_reconciliation"),
            ("PDOCKER_GPU_STRICT_DEVICE_LOCAL_STAGING", "strict_device_local_staging"),
            ("PDOCKER_GPU_MATERIALIZE_SPIRV_SPECIALIZATION_CONSTANTS", "materialize_specialization"),
            ("PDOCKER_GPU_DISABLE_PIPELINE_OPTIMIZATION", "disable_pipeline_optimization"),
            ("PDOCKER_GPU_SKIP_UNUSED_DESCRIPTOR_TRANSFERS", "skip_unused_descriptor_transfers"),
            ("PDOCKER_GPU_USE_SPIRV_DESCRIPTOR_ACCESS", "use_spirv_descriptor_access"),
            ("PDOCKER_GPU_DISABLE_OVERLAP_ALIASING", "disable_overlap_aliasing"),
            ("PDOCKER_GPU_CPU_ORACLE", "cpu_oracle"),
            ("PDOCKER_VULKAN_DISABLE_8BIT_STORAGE", "disable_storage8"),
            ("PDOCKER_VULKAN_DISABLE_16BIT_STORAGE", "disable_storage16"),
            ("PDOCKER_VULKAN_DISABLE_SUBGROUP_ARITHMETIC", "disable_subgroup_arithmetic"),
        ]:
            self.assertIn(f'"{env_name}", "{option_name}"', icd)
        self.assertIn("api_offsets[binding_count]", icd)
        self.assertIn("api_descriptor_types[binding_count]", icd)
        self.assertIn("PDOCKER_GPU_DISPATCH_PROFILE_LOG", icd)
        self.assertIn("const size_t max_response = 1024 * 1024", icd)
        self.assertIn("char stack_line[16384]", icd)
        self.assertIn("char *heap_line = NULL", icd)
        self.assertIn("memcpy(next, line, line_off)", icd)
        self.assertIn("free(heap_line)", icd)
        self.assertIn("per-call/per-thread state, never shared globally", icd)
        self.assertIn("growth is geometric and capped", icd)
        self.assertIn("if (next_cap < line_cap)", icd)
        self.assertIn("rc = -EOVERFLOW", icd)
        self.assertIn("advertised_subgroup_size", icd)
        self.assertIn("PDOCKER_VULKAN_SUBGROUP_SIZE", icd)
        self.assertIn("subgroupSize = advertised_subgroup_size()", icd)

    def test_llama_gpu_compare_classifies_executor_feature_mismatches(self):
        compare = LLAMA_COMPARE.read_text()
        for marker in [
            "executor_feature_mismatches = sorted({",
            'event.get("spirv_feature_mismatch") is True',
            '"executor_spirv_feature_mismatch": bool(executor_feature_mismatches)',
            '"executor_spirv_feature_mismatches": executor_feature_mismatches',
            'blocker_class = "vulkan_feature_mismatch"',
            "generic SPIR-V dispatch ran while executor feature policy reports missing features",
            "clamp or translate llama.cpp storage8/int8 final-projection shaders before accepting performance results",
        ]:
            self.assertIn(marker, compare)

    def test_gpu_executor_has_opt_in_writeonly_buffer_cache(self):
        source = GPU_EXECUTOR.read_text()
        for marker in [
            "PDOCKER_GPU_WRITEONLY_BUFFER_CACHE",
            "writeonly_buffer_cache_enabled",
            "if (!initialize_from_fd)",
            "find_writeonly_scratch_cache_entry",
            "entry->scratch = 1;",
            "entry->valid && !entry->scratch",
            "*mutable_cache_hit = 1;",
        ]:
            self.assertIn(marker, source)

    def test_gpu_executor_has_opt_in_writeonly_dirty_probe(self):
        source = GPU_EXECUTOR.read_text()
        for marker in [
            "PDOCKER_GPU_WRITEONLY_DIRTY_PROBE",
            "PDOCKER_GPU_WRITEONLY_DIRTY_PROBE_MIN_BYTES",
            "PDOCKER_GPU_WRITEONLY_DIRTY_WRITEBACK",
            "PDOCKER_GPU_UNSAFE_DIRTY_WRITEBACK_CACHE",
            "PDOCKER_GPU_WRITEONLY_DIRTY_PROBE_SENTINEL",
            "writeonly_dirty_probe_enabled",
            "writeonly_dirty_writeback_enabled",
            "Dirty writeback is a performance optimization, not a correctness",
            "has_writeonly_buffer_cache",
            "has_mutable_buffer_cache_max_bytes",
            "mutable_buffer_cache_candidate_with_max",
            "VulkanDirtyMaskCacheEntry",
            "find_dirty_mask_cache_entry",
            "update_dirty_mask_cache",
            "write_dirty_pages_exact",
            "count_dirty_probe_pages",
            "parse_vulkan_dispatch_option",
            "binding_dirty_probe_pages",
            "!binding_read_needed[i]",
        ]:
            self.assertIn(marker, source)
        icd = VULKAN_ICD.read_text()
        for marker in [
            "PdockerVkBoolBridgeOption",
            "bool_bridge_options[]",
            '"PDOCKER_GPU_WRITEONLY_DIRTY_PROBE", "dirty_probe"',
            '"PDOCKER_GPU_WRITEONLY_DIRTY_WRITEBACK", "dirty_writeback"',
            '"PDOCKER_GPU_WRITEONLY_BUFFER_CACHE", "writeonly_cache"',
            '"PDOCKER_GPU_MUTABLE_BUFFER_CACHE_MAX_BYTES", "mutable_cache_max"',
            "profile=1",
            '"PDOCKER_GPU_WRITEONLY_DIRTY_PROBE_MIN_BYTES", "dirty_probe_min"',
            "PDOCKER_GPU_DISPATCH_PROFILE_RESPONSE",
            "PDOCKER_GPU_DISPATCH_PROFILE_LOG",
        ]:
            self.assertIn(marker, icd)
        compare = LLAMA_COMPARE.read_text()
        self.assertIn("PDOCKER_GPU_DISPATCH_PROFILE_RESPONSE=1", compare)
        self.assertIn("PDOCKER_GPU_DISPATCH_PROFILE_LOG=1", compare)

    def test_llama_gpu_compare_forwards_native_tuning_envs_by_default(self):
        compare = LLAMA_COMPARE.read_text()
        verifier = load_llama_gpu_artifact_verifier()
        forward_envs = set(verifier.LLAMA_GPU_COMPARE_FORWARD_ENV_KEYS)
        native_sources = GPU_EXECUTOR.read_text() + "\n" + VULKAN_ICD.read_text()
        native_envs = set(re.findall(
            r'getenv\("((?:PDOCKER_GPU|PDOCKER_VULKAN|PDOCKER_ANDROID)_[A-Z0-9_]+)"\)',
            native_sources,
        ))
        native_envs |= set(re.findall(
            r'env_truthy(?:_default)?\("((?:PDOCKER_GPU|PDOCKER_VULKAN|PDOCKER_ANDROID)_[A-Z0-9_]+)"',
            native_sources,
        ))
        internal_only = {
            "PDOCKER_GPU_QUEUE_SOCKET",
            "PDOCKER_GPU_SHARED_DIR",
        }
        missing = sorted(
            key for key in native_envs - internal_only
            if key not in forward_envs and f"{key}=" not in compare
        )
        self.assertEqual([], missing)

        self.assertIn("descriptor_array_layout_seen", compare)
        self.assertIn("descriptor_array_layouts", compare)
        self.assertIn("ngl_zero_generic_spirv_dispatch", compare)
        self.assertIn("vulkan_backend_control_mismatch", compare)
        self.assertIn("n-gpu-layers as an insufficient isolation knob", compare)
        self.assertIn("api_understanding", compare)
        self.assertIn("effective_offset_mismatch_count", compare)
        self.assertIn(("PDOCKER_GPU_CPU_ORACLE", "cpu_oracle_requested"), verifier.LLAMA_GPU_CONFIG_PROPAGATION_ENV_FIELDS)
        self.assertIn("PDOCKER_LLAMA_BENCH_WARMUP_DISCARD", compare)
        self.assertIn("gpu_summary_scope", compare)
        for field in [
            "dirty_probe_binding_samples",
            "dirty_probe_max_bytes",
            "dirty_probe_total_bytes",
            "dirty_probe_ms_max",
            "dirty_writeback_cached_samples",
            "dirty_writeback_total_bytes",
            "top_dirty_probe_bindings",
        ]:
            self.assertIn(field, compare)

    def test_gpu_env_propagation_parity_is_documented_and_guarded(self):
        compare = LLAMA_COMPARE.read_text()
        pdockerd = PDOCKERD.read_text()
        main_activity = MAIN_ACTIVITY.read_text()
        next_steps = LLAMA_GPU_NEXT_STEPS.read_text()
        correctness = LLAMA_GPU_CORRECTNESS.read_text()
        verifier = load_llama_gpu_artifact_verifier()
        manifest = json.loads(LLAMA_GPU_ENV_MANIFEST.read_text())

        self.assertEqual("pdocker.llama.gpu.env-manifest.v1", manifest["schema"])
        self.assertIn("llama-gpu-env-manifest.json", compare)
        self.assertIn("compare_forward_env_keys", compare)
        self.assertIn("config_propagation_env_fields", compare)

        ui_compose_runtime_keys = verifier.LLAMA_GPU_UI_RUNTIME_ENV_KEYS
        for key in ui_compose_runtime_keys:
            self.assertTrue(
                f'"{key}": os.environ.get' in pdockerd
                or any(key.startswith(prefix) for prefix in ("PDOCKER_GPU_", "PDOCKER_VULKAN_", "GGML_VK_"))
                and "GPU_RUNTIME_ENV_PREFIXES" in pdockerd,
                key,
            )
            self.assertIn(key, verifier.LLAMA_GPU_COMPARE_FORWARD_ENV_KEYS)
            self.assertIn(key, next_steps)

        ui_compose_template_staleness_keys = [
            "PDOCKER_GPU_DISABLE_PIPELINE_OPTIMIZATION",
            "PDOCKER_VULKAN_MAX_BUFFER_BYTES",
            "GGML_VK_FORCE_MAX_BUFFER_SIZE",
            "GGML_VK_FORCE_MAX_ALLOCATION_SIZE",
            "GGML_VK_SUBALLOCATION_BLOCK_SIZE",
        ]
        for key in ui_compose_template_staleness_keys:
            self.assertIn(key, main_activity)

        diagnostic_keys = verifier.LLAMA_GPU_COMPARE_DIAGNOSTIC_ENV_KEYS
        for key in diagnostic_keys:
            self.assertIn(key, verifier.LLAMA_GPU_COMPARE_FORWARD_ENV_KEYS)
            self.assertIn(key, next_steps)

        manifest_config_pairs = [
            (item["env"], item["executor_field"])
            for item in manifest["config_propagation_env_fields"]
        ]
        self.assertEqual(list(verifier.LLAMA_GPU_CONFIG_PROPAGATION_ENV_FIELDS), manifest_config_pairs)
        for env_name, _field_name in verifier.LLAMA_GPU_CONFIG_PROPAGATION_ENV_FIELDS:
            self.assertIn(env_name, verifier.LLAMA_GPU_COMPARE_FORWARD_ENV_KEYS)

        self.assertIn("LLAMA_GPU_UI_RUNTIME_ENV_KEYS", LLAMA_GPU_ARTIFACT_VERIFIER.read_text())
        self.assertIn("LLAMA_GPU_COMPARE_DIAGNOSTIC_ENV_KEYS", LLAMA_GPU_ARTIFACT_VERIFIER.read_text())
        self.assertIn("LLAMA_GPU_COMPARE_FORWARD_ENV_KEYS", LLAMA_GPU_ARTIFACT_VERIFIER.read_text())
        self.assertIn("LLAMA_GPU_CONFIG_PROPAGATION_ENV_FIELDS", LLAMA_GPU_ARTIFACT_VERIFIER.read_text())
        self.assertIn("UNSUPPORTED_GPU_WORK_TOKENS", LLAMA_GPU_ARTIFACT_VERIFIER.read_text())
        self.assertIn("unsupported-gpu-work-accepted", LLAMA_GPU_ARTIFACT_VERIFIER.read_text())
        self.assertIn("UI/compose runtime defaults and compare-only diagnostics", next_steps)
        self.assertIn("Artifact verifier manifest guard", next_steps)
        self.assertIn("Unsupported GPU work gate", next_steps)
        self.assertIn("Environment propagation parity", correctness)

    def test_llama_gpu_artifact_verifier_blocks_env_reflection_misses(self):
        verifier = load_llama_gpu_artifact_verifier()

        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "diagnostics": {
                    "runtime_freshness": {
                        "summary": "pass",
                        "expected_executor_marker": "gpu-executor-llama-q4k-callsite-20260520",
                        "observed_executor_markers": ["gpu-executor-llama-q4k-callsite-20260520"],
                    },
                    "config_propagation": {
                        "summary": "fail",
                        "checks": [
                            {
                                "env": "PDOCKER_GPU_Q6K_SAFE_KERNEL",
                                "executor_field": "q6k_safe_kernel",
                                "expected": True,
                                "observed_values": [],
                                "status": "missing-evidence",
                            }
                        ],
                    },
                    "q6_workgroup_diagnostics": {
                        "workgroup_shape_blocker": False,
                        "latest_status": "match",
                        "q6_writeback_verified_all": True,
                    },
                },
                "correctness": {"summary": {"correctness": "pass"}},
            },
            "cpu": {"tokens_per_second": 0.1},
            "comparison": {"speedup": 3.0, "target_met": True},
        }
        report = verifier.classify(payload)
        self.assertEqual("config-propagation-mismatch", report["classification"])
        self.assertFalse(report["correctness_claim_allowed"])
        self.assertFalse(report["benchmark_claim_allowed"])
        self.assertIn("PDOCKER_GPU_Q6K_SAFE_KERNEL", json.dumps(report["config_propagation"]))

    def test_llama_compare_records_completion_readiness_and_runtime_env(self):
        compare = LLAMA_COMPARE.read_text()
        start_script = (ROOT / "app/src/main/assets/project-library/llama-cpp-gpu/scripts/start-llama-server.sh").read_text()
        verifier_source = LLAMA_GPU_ARTIFACT_VERIFIER.read_text()

        for marker in [
            "PDOCKER_LLAMA_COMPLETION_READY_TIMEOUT_SEC",
            "probe_service_readiness",
            "engine_body_has_id",
            "create timeout left no inspectable named container",
            '"schema": "pdocker.llama.service-readiness.v1"',
            '"prompt": "2+3="',
            '"n_predict": 1',
            '"expected": ["5"]',
            '"status": "pass"',
            '"health": "pass"',
            '"models": "pass"',
            "service_readiness",
            "runtime_env",
            "startup_diagnostics",
            "container_config_env",
            "container_archive_file",
            "except (EOFError, OSError, tarfile.TarError)",
            "container_env_snapshot",
            "LLAMA_",
            "PDOCKER_GPU_",
            "PDOCKER_VULKAN_",
            "GGML_VK_",
            "VK_ICD_FILENAMES",
            "VK_DRIVER_FILES",
            "OCL_ICD_VENDORS",
            "llama_completion_timeout",
        ]:
            self.assertIn(marker, compare)

        for marker in [
            "LLAMA_STARTUP_JSON",
            "pdocker.llama.startup.v1",
            "profile_refresh_rc",
            "kv_offload_guarded",
            "kv_offload_env",
            "kv_offload_disabled_effective",
            "pdocker llama startup diagnostics",
        ]:
            self.assertIn(marker, start_script)

        self.assertIn("llama-completion-timeout", verifier_source)
        self.assertIn("_service_completion_timeout", verifier_source)

    def test_llama_gpu_artifact_verifier_classifies_completion_timeout(self):
        verifier = load_llama_gpu_artifact_verifier()
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "served": True,
                "runtime_env": {
                    "LLAMA_GPU_BACKEND": "vulkan",
                    "PDOCKER_VULKAN_ICD_KIND": "pdocker-bridge-minimal",
                },
                "service_readiness": {
                    "schema": "pdocker.llama.service-readiness.v1",
                    "summary": {
                        "health": "pass",
                        "models": "pass",
                        "liveness": "pass",
                        "completion": "fail",
                        "ready": False,
                    },
                    "health": {"ok": True, "status": "pass", "status_code": 200, "duration_ms": 3},
                    "models": {"ok": True, "status": "pass", "status_code": 200, "duration_ms": 4},
                    "completion": {
                        "ok": False,
                        "status": "fail",
                        "error": "TimeoutError: timed out",
                        "timeout_sec": 180,
                        "duration_ms": 180000,
                    },
                },
                "diagnostics": {
                    "runtime_freshness": {
                        "summary": "fail",
                        "expected_executor_marker": "gpu-executor-llama-q4k-callsite-20260520",
                        "observed_executor_markers": [],
                    },
                },
            },
        }
        report = verifier.classify(payload)
        self.assertEqual("llama-completion-timeout", report["classification"])
        self.assertFalse(report["correctness_claim_allowed"])
        self.assertFalse(report["benchmark_claim_allowed"])
        self.assertIn("LLAMA_GPU_BACKEND", report["runtime_env"])
        self.assertTrue(report["service_readiness"]["health_ok"])
        self.assertTrue(report["service_readiness"]["models_ok"])
        self.assertEqual("fail", report["service_readiness"]["completion_status"])

    def test_llama_gpu_artifact_verifier_requires_health_and_models_for_completion_timeout(self):
        verifier = load_llama_gpu_artifact_verifier()
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "served": True,
                "service_readiness": {
                    "schema": "pdocker.llama.service-readiness.v1",
                    "summary": {
                        "health": "fail",
                        "models": "pass",
                        "liveness": "fail",
                        "completion": "fail",
                        "ready": False,
                    },
                    "health": {"ok": False, "status": "fail", "error": "HTTP Error 503"},
                    "models": {"ok": True, "status": "pass", "status_code": 200},
                    "completion": {
                        "ok": False,
                        "status": "fail",
                        "error": "TimeoutError: timed out",
                        "timeout_sec": 180,
                    },
                },
                "diagnostics": {
                    "runtime_freshness": {
                        "summary": "fail",
                        "expected_executor_marker": "gpu-executor-llama-q4k-callsite-20260520",
                        "observed_executor_markers": [],
                    },
                },
            },
        }
        readiness = verifier._service_completion_timeout(payload)
        self.assertFalse(readiness["timeout"])
        self.assertFalse(readiness["health_ok"])
        self.assertTrue(readiness["models_ok"])
        self.assertNotEqual("llama-completion-timeout", verifier.classify(payload)["classification"])

    def test_llama_gpu_artifact_verifier_prefers_pre_http_gpu_blocker(self):
        verifier = load_llama_gpu_artifact_verifier()
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "served": False,
                "diagnostics": {
                    "blocker_class": "vulkan_pipeline_feature",
                    "blocker_detail": "Android Vulkan rejected a ggml generic SPIR-V compute pipeline with VK_ERROR_FEATURE_NOT_PRESENT",
                    "runtime_freshness": {
                        "summary": "pass",
                        "expected_executor_marker": "gpu-executor-llama-q4k-callsite-20260520",
                        "observed_executor_markers": ["gpu-executor-llama-q4k-callsite-20260520"],
                    },
                    "config_propagation": {"summary": "pass", "checks": []},
                },
            },
        }
        report = verifier.classify(payload)
        self.assertEqual("vulkan-pipeline-feature", report["classification"])
        self.assertFalse(report["correctness_claim_allowed"])
        self.assertFalse(report["benchmark_claim_allowed"])
        self.assertEqual("vulkan_pipeline_feature", report["gpu_blocker_class"])
        self.assertIn("VK_ERROR_FEATURE_NOT_PRESENT", report["gpu_blocker_detail"])
        self.assertIn("pre_http_failure_evidence", report)
        self.assertIn("q6_reachability", report["pre_http_failure_evidence"])

    def test_llama_gpu_compare_records_pre_q6_failure_summary(self):
        compare = LLAMA_COMPARE.read_text()
        for marker in [
            "def compact_pre_q6_failure",
            'generic_spirv_dispatch["pre_q6_failure"]',
            '"q6_reachability"',
            '"failed_event_count"',
            '"failure_event"',
            '"requested_feature_mask"',
            '"spirv_required_feature_mask"',
            '"spirv_requested_feature_missing_mask"',
            '"spirv_requested_feature_mismatches"',
            '"spirv_feature_requirements"',
            '"android_vulkan_features"',
            '"android_vulkan_enabled_features"',
            "][:4]",
            "event = failed[0] if failed else {}",
        ]:
            self.assertIn(marker, compare)

    def test_gpu_executor_reports_enabled_vulkan_features_in_json(self):
        source = GPU_EXECUTOR.read_text()
        for marker in [
            "write_android_vulkan_enabled_features_report",
            '\\"android_vulkan_enabled_features\\":{',
            '\\"chain_compat_feature_structs\\":%u',
            "enabled_ext_16bit_storage",
            "enabled_ext_8bit_storage",
            "enabled_ext_shader_float16_int8",
            "enabled_ext_storage_buffer_storage_class",
            "#define PDOCKER_GPU_EXECUTOR_BUILD_MARKER \"gpu-executor-llama-q4k-callsite-20260520\"",
        ]:
            self.assertIn(marker, source)
        failure_body = source.split("if (ret != 0) {", 1)[1].split("if (fence) vkDestroyFence", 1)[0]
        failure_features = failure_body.index('\\"android_vulkan_features\\":{')
        failure_enabled = failure_body.index("write_android_vulkan_enabled_features_report(json_out(), rt);")
        failure_close = failure_body.index('fprintf(json_out(), "}\\n");', failure_enabled)
        self.assertLess(failure_features, failure_enabled)
        self.assertLess(failure_enabled, failure_close)
        capabilities_body = source.split("static void print_capabilities", 1)[1].split(
            "static void print_noop", 1
        )[0]
        capabilities_features = capabilities_body.index('\\"android_vulkan_features\\":{')
        capabilities_enabled = capabilities_body.index("write_android_vulkan_enabled_features_report(json_out(), rt);")
        capabilities_close = capabilities_body.index('fprintf(json_out(), ",\\\"process_exec\\\":true}\\n");')
        self.assertLess(capabilities_features, capabilities_enabled)
        self.assertLess(capabilities_enabled, capabilities_close)

    def test_llama_gpu_dispatch_lifecycle_logs_are_recorded(self):
        compare = LLAMA_COMPARE.read_text()
        icd = VULKAN_ICD.read_text()
        executor = GPU_EXECUTOR.read_text()

        for marker in [
            "generic dispatch lifecycle:",
            "dispatch_lifecycle_log_enabled",
            "g_generic_dispatch_sequence",
        ]:
            self.assertIn(marker, icd)
        self.assertIn('\\"event\\":\\"begin\\"', icd)
        self.assertIn('\\"event\\":\\"end\\"', icd)
        self.assertIn('component', icd)
        self.assertIn('icd', icd)

        for marker in [
            "generic dispatch lifecycle:",
            "g_vulkan_dispatch_lifecycle_sequence",
        ]:
            self.assertIn(marker, executor)
        self.assertIn('\\"event\\":\\"stage\\"', executor)
        self.assertIn('\\"stage\\":\\"submit\\"', executor)
        self.assertIn('\\"stage\\":\\"wait-complete\\"', executor)
        self.assertIn('component', executor)
        self.assertIn('executor', executor)

        for marker in [
            "extract_dispatch_lifecycle_events",
            "summarize_dispatch_lifecycle",
            "unmatched_begin_ids",
            "dispatch_lifecycle",
        ]:
            self.assertIn(marker, compare)


if __name__ == "__main__":
    unittest.main()

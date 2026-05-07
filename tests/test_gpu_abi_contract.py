import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_HEADER = ROOT / "app" / "src" / "main" / "cpp" / "pdocker_gpu_abi.h"
CONTAINER_HEADER = ROOT / "docker-proot-setup" / "src" / "gpu" / "pdocker_gpu_abi.h"
GPU_EXECUTOR = ROOT / "app" / "src" / "main" / "cpp" / "pdocker_gpu_executor.c"
VULKAN_ICD = ROOT / "docker-proot-setup" / "src" / "gpu" / "pdocker_vulkan_icd.c"
LLAMA_COMPARE = ROOT / "scripts" / "android-llama-gpu-compare.sh"


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
        self.assertIn('\\"binding_details\\":[', source)
        self.assertIn("profile_response", source)
        self.assertIn("PDOCKER_GPU_DISPATCH_PROFILE_RESPONSE", source)
        self.assertIn("if (profile_response) {", source)
        for field in [
            '\\"binding\\":%u',
            '\\"offset\\":%lld',
            '\\"size\\":%zu',
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
        ]:
            self.assertIn(field, source)
        self.assertGreaterEqual(source.count("write_vulkan_binding_report(json_out()"), 2)
        self.assertIn("binding_upload_ms[PDOCKER_GPU_MAX_VULKAN_BINDINGS]", source)
        self.assertIn("binding_download_ms[PDOCKER_GPU_MAX_VULKAN_BINDINGS]", source)
        self.assertIn("binding_dirty_probe_pages[PDOCKER_GPU_MAX_VULKAN_BINDINGS]", source)
        self.assertIn("binding_dirty_probe_bytes[PDOCKER_GPU_MAX_VULKAN_BINDINGS]", source)
        self.assertIn("binding_upload_ms[i] = now_ms() - binding_start;", source)
        self.assertIn("binding_download_ms[i] = now_ms() - binding_start;", source)

    def test_vulkan_duplicate_binding_rewrite_avoids_passed_bindings(self):
        source = GPU_EXECUTOR.read_text()
        self.assertIn("const VulkanDispatchBinding *bindings", source)
        self.assertIn("size_t binding_count", source)
        self.assertIn("used[bindings[i].binding] = 1;", source)
        self.assertIn("rewrite_duplicate_descriptor_bindings(\n                shader_code,\n                shader_size,\n                bindings,\n                binding_count,", source)

    def test_vulkan_specialization_constants_can_be_materialized(self):
        source = GPU_EXECUTOR.read_text()
        self.assertIn("materialize_spirv_specialization_constants", source)
        self.assertIn("PDOCKER_GPU_MATERIALIZE_SPIRV_SPECIALIZATION_CONSTANTS", source)
        self.assertIn("vk_spec_ptr = specialization_materialized ? NULL : &vk_spec_info;", source)
        self.assertIn('\\"specialization_materialized\\":%s', source)

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
        ]:
            self.assertIn(field, compare)
        self.assertIn('detail.get("upload_ms")', compare)
        self.assertIn('detail.get("download_ms")', compare)

    def test_llama_gpu_compare_can_forward_bridge_tuning_env(self):
        compare = LLAMA_COMPARE.read_text()
        self.assertIn("import os", compare)
        for key in [
            "PDOCKER_GPU_DISABLE_PIPELINE_OPTIMIZATION",
            "PDOCKER_GPU_MUTABLE_BUFFER_CACHE_MAX_BYTES",
            "PDOCKER_GPU_RESIDENT_CACHE_MIN_BYTES",
            "PDOCKER_GPU_SKIP_UNUSED_DESCRIPTOR_TRANSFERS",
            "PDOCKER_GPU_USE_SPIRV_DESCRIPTOR_ACCESS",
            "PDOCKER_GPU_WRITEONLY_BUFFER_CACHE",
            "PDOCKER_GPU_WRITEONLY_DIRTY_PROBE",
            "PDOCKER_GPU_WRITEONLY_DIRTY_PROBE_MIN_BYTES",
            "PDOCKER_GPU_WRITEONLY_DIRTY_WRITEBACK",
        ]:
            self.assertIn(f'"{key}"', compare)
        self.assertIn("value = os.environ.get(key)", compare)

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
            "PDOCKER_GPU_WRITEONLY_DIRTY_PROBE_SENTINEL",
            "writeonly_dirty_probe_enabled",
            "writeonly_dirty_writeback_enabled",
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
            "dirty_probe=%u",
            "dirty_writeback=%u",
            "writeonly_cache=%u",
            "mutable_cache_max=%llu",
            "profile=1",
            "dirty_probe_min=%llu",
            "PDOCKER_GPU_WRITEONLY_DIRTY_PROBE",
            "PDOCKER_GPU_WRITEONLY_DIRTY_PROBE_MIN_BYTES",
            "PDOCKER_GPU_WRITEONLY_DIRTY_WRITEBACK",
            "PDOCKER_GPU_WRITEONLY_BUFFER_CACHE",
            "PDOCKER_GPU_MUTABLE_BUFFER_CACHE_MAX_BYTES",
            "PDOCKER_GPU_DISPATCH_PROFILE_RESPONSE",
            "PDOCKER_GPU_DISPATCH_PROFILE_LOG",
        ]:
            self.assertIn(marker, icd)
        compare = LLAMA_COMPARE.read_text()
        self.assertIn("PDOCKER_GPU_DISPATCH_PROFILE_RESPONSE=1", compare)
        self.assertIn("PDOCKER_GPU_DISPATCH_PROFILE_LOG=1", compare)
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


if __name__ == "__main__":
    unittest.main()

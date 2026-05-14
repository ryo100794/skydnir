import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIRECT = ROOT / "app" / "src" / "main" / "cpp" / "pdocker_direct_exec.c"
SMOKE = ROOT / "scripts" / "android-device-smoke.sh"
DOC = ROOT / "docs" / "design" / "APK_MEMORY_PAGER.md"
PROBE_DOC = ROOT / "docs" / "test" / "APK_MEMORY_PAGER_PROBE.md"
DEVICE_SCRIPT = ROOT / "scripts" / "android-memory-pager-managed-poc.sh"
TRANSPARENT_DEVICE_SCRIPT = ROOT / "scripts" / "android-memory-pager-transparent-poc.sh"


class MemoryPagerContractTest(unittest.TestCase):
    def setUp(self):
        self.source = DIRECT.read_text()

    def test_managed_pager_is_opt_in_and_exposed_by_direct_executor(self):
        self.assertIn("--pdocker-memory-pager-managed-poc", self.source)
        self.assertIn("run_memory_pager_managed_poc", self.source)
        self.assertIn("pager-managed-poc:result=%s", self.source)
        self.assertIn("PDOCKER_MEMORY_PAGER=managed", DOC.read_text())

    def test_managed_pager_keeps_fixed_resident_window_and_dirty_writeback(self):
        for token in [
            "resident_limit",
            "resident_count",
            "max_resident_count",
            "while (pager->resident_count >= pager->resident_limit)",
            "managed_pager_evict_one",
            "pwrite(pager->backing_fd",
            "pread(pager->backing_fd",
            "pager->dirty[index] = 1",
            "pager->writable[index]",
            "mprotect(addr, pager->page_size, PROT_NONE)",
            "mprotect(addr, pager->page_size, PROT_READ)",
            "mprotect(addr, pager->page_size, PROT_READ | PROT_WRITE)",
        ]:
            self.assertIn(token, self.source)

    def test_managed_pager_emits_replayable_metrics(self):
        for token in [
            "pager-managed-poc:max_resident_pages=%llu",
            "pager-managed-poc:page_ins=%llu",
            "pager-managed-poc:page_outs=%llu",
            "pager-managed-poc:dirty_page_outs=%llu",
            "pager-managed-poc:bytes_in=%llu",
            "pager-managed-poc:bytes_out=%llu",
            "pager-managed-poc:elapsed_ns=%llu",
        ]:
            self.assertIn(token, self.source)

    def test_managed_pager_has_opt_in_transparent_mmap_fault_path(self):
        for token in [
            "PDOCKER_DIRECT_MEMORY_PAGER",
            "PDOCKER_MEMORY_PAGER",
            "g_managed_memory_pager",
            "ManagedPagerAdmissionStats",
            "managed_trace_is_candidate_mmap",
            "maybe_prepare_managed_mmap",
            "maybe_finish_managed_mmap",
            "handle_managed_memory_fault",
            "run_memory_pager_transparent_poc",
            "register_managed_mmap_region",
            "MAP_ANONYMOUS",
            "MAP_PRIVATE",
            "PROT_NONE",
            "sig == SIGSEGV && g_managed_memory_pager",
            "PTRACE_GETSIGINFO",
            "__NR_mprotect",
        ]:
            self.assertIn(token, self.source)

    def test_managed_pager_records_admission_and_backing_telemetry(self):
        for token in [
            "pdocker.memory-pager.admission.v1",
            "record_managed_pager_admission",
            "print_managed_pager_admission_stats",
            "rejected_below_threshold",
            "rejected_too_large",
            "rejected_fixed_address",
            "rejected_flags",
            "rejected_file_backed",
            "rejected_protection",
            "register_failed",
            "denied_enomem",
            "cleanup_munmap_failed",
            "allocation_denied_enomem",
            "last_request_bytes",
            "threshold_bytes",
            "max_region_bytes",
            "last_classification",
            "backing_op",
            "backing_errno",
            '"unsupported-protection"',
            "prot & PROT_EXEC",
        ]:
            self.assertIn(token, self.source)

    def test_managed_pager_register_failure_fails_closed_as_enomem(self):
        for token in [
            "__NR_munmap",
            'regs->regs[0] = (unsigned long long)-ENOMEM',
            "fail-closed cleanup munmap failed",
            "fail-closed ENOMEM setregs failed",
            "denied=-ENOMEM classification=allocation_denied_enomem",
            "g_memory_stats.denied++",
            "g_memory_stats.last_denied_bytes = len",
        ]:
            self.assertIn(token, self.source)
        self.assertNotIn("restored_prot=0x%x", self.source)

    def test_pdockerd_propagates_memory_pager_labels_to_direct_executor(self):
        pdockerd = (ROOT / "docker-proot-setup" / "bin" / "pdockerd").read_text()
        asset_path = ROOT / "app" / "src" / "main" / "assets" / "pdockerd" / "pdockerd"
        sources = [pdockerd]
        if asset_path.exists():
            sources.append(asset_path.read_text())
        for source in sources:
            self.assertIn("def _apply_memory_pager_env", source)
            self.assertIn("io.pdocker.memory-pager", source)
            self.assertIn("PDOCKER_DIRECT_MEMORY_PAGER", source)
            self.assertIn("PDOCKER_DIRECT_MEMORY_PAGER_MAX_REGION", source)
            self.assertIn("PDOCKER_DIRECT_MEMORY_PAGER_RESIDENT_PAGES", source)
            self.assertIn("_apply_memory_pager_env(state, env)", source)
            self.assertIn("_apply_memory_pager_env(state, e)", source)

    def test_managed_pager_rejects_invalid_sizes_and_cleans_up_on_init_failure(self):
        for token in [
            "page_count == 0",
            "errno = EINVAL",
            "pager->page_count > SIZE_MAX / pager->page_size",
            "errno = EOVERFLOW",
            "goto fail",
            "managed_pager_destroy(pager)",
            "pager->backing_fd = -1",
        ]:
            self.assertIn(token, self.source)

    def test_backing_file_selection_is_apk_scoped_before_debug_paths(self):
        self.assertIn("managed_pager_open_backing_file", self.source)
        self.assertIn('getenv("TMPDIR")', self.source)
        self.assertIn('tmpdir && tmpdir[0] ? tmpdir : ""', self.source)
        self.assertIn('cwd_tmp[0] ? cwd_tmp : ""', self.source)
        self.assertIn("if (!dir[0]) continue", self.source)
        self.assertIn("managed_pager_mkdir_p", self.source)
        self.assertIn('"files/pdocker/tmp"', self.source)
        self.assertIn('"files/tmp"', self.source)
        self.assertIn('"."', self.source)
        self.assertIn('"files"', self.source)
        self.assertIn('"cache"', self.source)
        self.assertIn("backing_errno", self.source)
        self.assertIn("backing_dir", self.source)
        self.assertIn('"/data/local/tmp"', self.source)
        self.assertIn('"/tmp"', self.source)
        self.assertIn("unlink(tmpl)", self.source)

    def test_device_smoke_and_manifest_include_managed_pager_gate(self):
        smoke = SMOKE.read_text()
        self.assertIn("compat managed anonymous pager poc", smoke)
        self.assertIn("mkdir -p files/pdocker/tmp cache", smoke)
        self.assertIn('TMPDIR="$APP_DATA/files/pdocker/tmp" files/pdocker-runtime/docker-bin/pdocker-direct --pdocker-memory-pager-managed-poc', smoke)
        self.assertIn('TMPDIR="$APP_DATA/files/pdocker/tmp" files/pdocker-runtime/docker-bin/pdocker-direct --pdocker-memory-pager-transparent-poc', smoke)
        self.assertIn("pager-managed-poc:result=ok", smoke)
        self.assertIn("Managed Anonymous Pager PoC Command", PROBE_DOC.read_text())

    def test_device_managed_pager_script_records_json_artifact(self):
        script = DEVICE_SCRIPT.read_text()
        self.assertIn("pdocker.apk-memory-pager-managed.v1", script)
        self.assertIn("--pdocker-memory-pager-managed-poc", script)
        self.assertIn("force_stops_app", script)
        self.assertIn("REMOTE_PREAMBLE", script)
        self.assertIn("files/pdocker/tmp", script)
        self.assertIn("page_ins", script)
        self.assertIn("apk-memory-pager-managed-latest.json", script)

    def test_device_transparent_pager_script_records_json_artifact(self):
        script = TRANSPARENT_DEVICE_SCRIPT.read_text()
        self.assertIn("pdocker.apk-memory-pager-transparent.v1", script)
        self.assertIn("--pdocker-memory-pager-transparent-poc", script)
        self.assertIn("force_stops_app", script)
        self.assertIn("REMOTE_PREAMBLE", script)
        self.assertIn("files/pdocker/tmp", script)
        self.assertIn("page_ins", script)
        self.assertIn("apk-memory-pager-transparent-latest.json", script)

    def test_oom_lmk_diagnostics_contract_records_pressure_process_and_progress(self):
        doc = DOC.read_text()
        probe = PROBE_DOC.read_text()
        for token in [
            "OOM/LMK Diagnostics Contract",
            "Planned gap",
            "MemAvailable",
            "SwapFree",
            "last large allocation request",
            "per-process RSS",
            "PSS",
            "pss_unavailable",
            "last known progress",
            "lmk_suspected=true",
            "interrupted-or-lmk-suspected",
        ]:
            self.assertIn(token, doc)
        for token in [
            "pdocker.memory-oom-lmk-diagnostics.v1",
            "last_large_allocation",
            "rss_bytes",
            "pss_bytes",
            "last_known_progress",
            "lmk_suspected_classifier",
        ]:
            self.assertIn(token, probe)

    def test_oom_lmk_diagnostics_contract_keeps_artifacts_bounded_and_ui_stale_safe(self):
        doc = DOC.read_text()
        probe = PROBE_DOC.read_text()
        for token in [
            "Artifact retention must be bounded",
            "redact environment values",
            "cap total memory-diagnostic artifact bytes",
            "must not show `running`, `Up`, or an active spinner solely",
            "engine snapshot",
            "pid liveness",
            "last known progress",
        ]:
            self.assertIn(token, doc)
        for token in [
            "artifact_retention_policy",
            "stale-running guard evidence",
            "UI is allowed to show a live running state",
            "bounded byte cap",
        ]:
            self.assertIn(token, probe)


if __name__ == "__main__":
    unittest.main()

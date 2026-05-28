import os
import stat
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class ContainerProbeAssetsTest(unittest.TestCase):
    def test_probe_runner_and_payload_exist(self):
        generic = ROOT / "scripts" / "container-direct-probe.sh"
        runner = ROOT / "scripts" / "android-container-direct-probe.sh"
        payload = ROOT / "app" / "src" / "main" / "assets" / "project-library" / "direct-runtime-probe" / "scripts" / "pdocker-container-probe.sh"
        self.assertTrue(generic.is_file(), generic)
        self.assertTrue(runner.is_file(), runner)
        self.assertTrue(payload.is_file(), payload)
        self.assertTrue(os.access(generic, os.X_OK), generic)
        self.assertTrue(os.access(runner, os.X_OK), runner)
        self.assertTrue(os.access(payload, os.X_OK), payload)

    def test_probe_payload_covers_large_alloc_and_argv(self):
        text = (ROOT / "app" / "src" / "main" / "assets" / "project-library" / "direct-runtime-probe" / "scripts" / "pdocker-container-probe.sh").read_text()
        for marker in [
            "test_argv_preservation",
            "test_linker_argv_preservation",
            "flash_attn_mask_opt.comp.cpp.o",
            "flash_attn_split_k_reduce.comp.cpp.o",
            "generated_flash_attn_95.comp.cpp.o",
            "-DGGML_BLAS=ON",
            "-DGGML_BLAS_VENDOR=OpenBLAS",
            "argc=108",
            "test_large_allocation_guard",
            "large_allocation_guard_ok",
            "/usr/bin/[",
            "readlink /proc/self/exe",
        ]:
            self.assertIn(marker, text)

    def test_library_compose_up_runs_probe_and_exports_documents_logs(self):
        root = ROOT / "app" / "src" / "main" / "assets" / "project-library" / "direct-runtime-probe"
        compose = (root / "compose.yaml").read_text()
        start = (root / "scripts" / "start-direct-runtime-probe.sh").read_text()
        dockerfile = (root / "Dockerfile").read_text()
        self.assertIn('command: ["/usr/local/bin/start-direct-runtime-probe"]', compose)
        self.assertIn("image: skydnir/direct-runtime-probe:latest", compose)
        self.assertIn("container_name: skydnir-direct-runtime-probe", compose)
        self.assertNotIn("image: pdocker/direct-runtime-probe:latest", compose)
        self.assertNotIn("container_name: pdocker-direct-runtime-probe", compose)
        self.assertIn("${PDOCKER_DOCUMENTS_HOST:-./documents}:${PDOCKER_DOCUMENTS_MOUNT:-/documents}", compose)
        self.assertIn("pdocker-container-probe > \"$log\" 2>&1", start)
        self.assertIn("/documents/skydnir-exports", start)
        self.assertIn('export_dir="${PDOCKER_EXPORT_DIR:-/documents/skydnir-exports}/direct-runtime-probe"', start)
        self.assertIn('export_latest="$export_dir/latest.log"', start)
        self.assertIn('"schema": "pdocker.direct-runtime-probe.v1"', start)
        self.assertIn('grep -q \'"status": "pass"\' /reports/latest.json', dockerfile)

    def test_test_suite_container_runs_scenarios_by_exec_and_exports_documents_logs(self):
        root = ROOT / "app" / "src" / "main" / "assets" / "project-library" / "pdocker-test-suite"
        compose = (root / "compose.yaml").read_text()
        dockerfile = (root / "Dockerfile").read_text()
        start = (root / "scripts" / "start-pdocker-test-suite.sh").read_text()
        runner = (root / "scripts" / "run-pdocker-test-suite.sh").read_text()
        probe = (root / "scripts" / "pdocker-container-probe.sh").read_text()
        self.assertIn("image: skydnir/test-suite:latest", compose)
        self.assertIn("container_name: skydnir-test-suite", compose)
        self.assertNotIn("image: pdocker/test-suite:latest", compose)
        self.assertNotIn("container_name: pdocker-test-suite", compose)
        self.assertIn('command: ["/usr/local/bin/start-skydnir-test-suite"]', compose)
        self.assertIn("${PDOCKER_DOCUMENTS_HOST:-./documents}:${PDOCKER_DOCUMENTS_MOUNT:-/documents}", compose)
        self.assertIn("COPY scripts/pdocker-container-probe.sh", dockerfile)
        self.assertIn("docker exec skydnir-test-suite run-skydnir-test-suite", start)
        self.assertIn("--scenario all|smoke|direct|io|archive|documents", runner)
        self.assertIn("run_selected_case direct_runtime_probe direct", runner)
        self.assertIn("run_selected_case linker_argv_preservation direct", runner)
        self.assertIn("run_selected_case file_io_smoke io", runner)
        self.assertIn("run_selected_case archive_roundtrip archive", runner)
        self.assertIn("/documents/skydnir-exports", runner)
        self.assertIn('"schema": "pdocker.test-suite.v1"', runner)
        self.assertIn("test_argv_preservation", probe)
        self.assertIn("test_linker_argv_preservation", probe)
        self.assertIn("generated_flash_attn_95.comp.cpp.o", probe)
        self.assertNotIn("for name in", runner)
        self.assertNotIn("for name in", probe)

    def test_generic_runner_does_not_require_adb_or_apk_build(self):
        text = (ROOT / "scripts" / "container-direct-probe.sh").read_text()
        self.assertIn("pdocker-direct compatible", text)
        self.assertIn("docker-proot-setup/docker-bin/pdocker-direct", text)
        self.assertIn('"$EXECUTOR" run', text)
        self.assertNotIn("adb shell", text)
        self.assertNotIn("adb_cmd", text)
        self.assertNotIn("gradlew", text)
        self.assertNotIn("install -r", text)

    def test_android_adapter_is_only_a_staging_adapter(self):
        text = (ROOT / "scripts" / "android-container-direct-probe.sh").read_text()
        self.assertIn("adb_cmd push", text)
        self.assertNotIn("gradlew", text)
        self.assertNotIn("install -r", text)


if __name__ == "__main__":
    unittest.main()

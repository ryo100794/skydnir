import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class NativeBuildAbiContractTest(unittest.TestCase):
    def test_gradle_packages_64_and_32_bit_arm_abis(self):
        gradle = (ROOT / "app" / "build.gradle.kts").read_text()
        self.assertRegex(gradle, r"abiFilters \+= listOf\([^\n]*\"arm64-v8a\"")
        self.assertNotRegex(gradle, r"abiFilters \+= listOf\([^\n]*\"armeabi-v7a\"")
        self.assertIn('listOf("arm64-v8a", "armeabi-v7a")', gradle)

    def test_ndk_script_has_no_termux_or_box64_dependency_in_standard_path(self):
        script = (ROOT / "scripts" / "build-native-android-ndk.sh").read_text()
        self.assertIn("arm64-v8a armeabi-v7a", script)
        self.assertIn("aarch64-linux-android", script)
        self.assertIn("armv7a-linux-androideabi", script)
        self.assertIn("pdocker_direct_unsupported.c", script)
        forbidden_runtime_paths = [
            "/data/data/com.termux",
            "box64",
        ]
        for needle in forbidden_runtime_paths:
            self.assertNotIn(needle, script)

    def test_armeabi_v7a_direct_is_explicit_unsupported_stub(self):
        stub = (ROOT / "app" / "src" / "main" / "cpp" / "pdocker_direct_unsupported.c").read_text()
        self.assertIn("process-exec is not implemented for this Android ABI yet", stub)
        self.assertIn("return 126", stub)
        self.assertIn("use arm64-v8a", stub)

    def test_checked_in_arm_elf_payloads_exist_for_both_abis(self):
        expected = {
            "arm64-v8a": {
                "ELF 64-bit",
                "ARM aarch64",
            },
            "armeabi-v7a": {
                "ELF 32-bit",
                "ARM",
            },
        }
        for abi, markers in expected.items():
            with self.subTest(abi=abi):
                abi_dir = ROOT / "app" / "src" / "main" / "jniLibs" / abi
                for name in [
                    "libpdockerpty.so",
                    "libpdockerdirect.so",
                    "libpdockergpuexecutor.so",
                    "libpdockermediaexecutor.so",
                    "libpdockergpushim.so",
                    "libpdockervulkanicd.so",
                    "libpdockeropenclicd.so",
                ]:
                    path = abi_dir / name
                    self.assertTrue(path.is_file(), f"missing {path}")
                    result = subprocess.run(
                        ["file", str(path)],
                        check=True,
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                    )
                    for marker in markers:
                        self.assertIn(marker, result.stdout)

    def test_documentation_declares_32_bit_direct_executor_as_unpromoted(self):
        doc = (ROOT / "docs" / "build" / "NATIVE_BUILD_ENVIRONMENT.md").read_text()
        todo = (ROOT / "docs" / "plan" / "TODO.md").read_text()
        self.assertIn("armeabi-v7a", doc)
        self.assertIn("explicit unsupported-ABI", doc)
        self.assertIn("Port `pdocker-direct` to 32-bit ARM", doc)
        self.assertIn("unsupported-ABI executable", todo)
        self.assertIn("armhf Vulkan ICD", doc)

    def test_gpu_shim_script_builds_explicit_arm64_and_armhf_outputs(self):
        script = (ROOT / "scripts" / "build-gpu-shim.sh").read_text()
        self.assertIn("PDOCKER_GLIBC_ARCHES:-arm64 armhf", script)
        self.assertIn("aarch64-linux-gnu-gcc", script)
        self.assertIn("arm-linux-gnueabihf-gcc", script)
        self.assertIn("verify_elf_arch", script)
        self.assertIn("ELF 32-bit", script)
        self.assertIn("ELF 64-bit", script)

    def test_default_apk_does_not_overclaim_incomplete_32_bit_runtime(self):
        apk = ROOT / "app" / "build" / "outputs" / "apk" / "compat" / "debug" / "app-compat-debug.apk"
        if not apk.is_file():
            self.skipTest(f"APK not built: {apk}")
        result = subprocess.run(
            ["zipinfo", "-1", str(apk)],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        entries = set(result.stdout.splitlines())
        self.assertIn("lib/arm64-v8a/libpdockerdirect.so", entries)
        self.assertNotIn("lib/armeabi-v7a/libpdockerdirect.so", entries)
        self.assertFalse(
            any(entry.startswith("lib/armeabi-v7a/") for entry in entries),
            "default APK must stay arm64-only until the 32-bit runtime is complete",
        )


if __name__ == "__main__":
    unittest.main()

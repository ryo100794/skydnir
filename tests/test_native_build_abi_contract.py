import json
import os
import pathlib
import subprocess
import tempfile
import unittest
import zipfile

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

    def test_native_payload_verifier_covers_runtime_assets_and_forbidden_caches(self):
        verifier = (ROOT / "scripts" / "verify-native-payloads.py").read_text()
        for needle in [
            "lib/arm64-v8a/libcrane.so",
            "lib/arm64-v8a/libcow.so",
            "assets/pdockerd/pdockerd",
            "assets/pdockerd/__pycache__/",
            "fdroid_no_crane",
            "--fdroid-no-crane",
            "same_bytes_as_source",
            "source_mirror_same_bytes",
        ]:
            self.assertIn(needle, verifier)

    def test_runtime_prepare_refreshes_same_version_debug_payloads(self):
        runtime_path = (
            ROOT
            / "app"
            / "src"
            / "main"
            / "kotlin"
            / "io"
            / "github"
            / "ryo100794"
            / "pdocker"
            / "PdockerdRuntime.kt"
        )
        runtime = runtime_path.read_text()
        self.assertIn("Debug/dev installs often reuse the same versionCode", runtime)
        self.assertIn('extractAsset(ctx, "pdockerd/pdockerd", File(bin, "pdockerd"), force = true)', runtime)
        self.assertIn(
            'extractAsset(ctx, "pdockerd/llama-gpu-env-manifest.json", File(bin, "llama-gpu-env-manifest.json"), force = true)',
            runtime,
        )
        self.assertIn('optionalLinkTo(File(nativeDir, "libpdockergpuexecutor.so"), File(gpuBin, "pdocker-gpu-executor"))', runtime)
        self.assertIn("java.nio.file.Files.deleteIfExists(link.toPath())", runtime)
        self.assertIn("createSymbolicLink(link.toPath(), target.toPath())", runtime)
        self.assertLess(
            runtime.index("java.nio.file.Files.deleteIfExists(link.toPath())"),
            runtime.index("createSymbolicLink(link.toPath(), target.toPath())"),
        )

    def test_selfdebug_doc_allows_same_version_reinstall_payload_refresh(self):
        doc = (ROOT / "docs" / "test" / "ANDROID_SELFDEBUG.md").read_text()
        self.assertIn("Same-version debug reinstalls are supported", doc)
        self.assertIn("force-refreshes pdockerd assets", doc)
        self.assertIn("runtime symlinks", doc)
        stale_claims = [
            "only refreshes staged assets when versionCode changes",
            "same versionCode can leave the old pdockerd",
            "old asset was reused",
            "bump `versionCode`",
        ]
        for claim in stale_claims:
            self.assertNotIn(claim, doc)

    def test_fdroid_no_crane_gate_is_wired_without_changing_normal_apk(self):
        copy_native = (ROOT / "scripts" / "copy-native.sh").read_text()
        gradle = (ROOT / "app" / "build.gradle.kts").read_text()
        release_verifier = (ROOT / "scripts" / "verify-native-rebuild-release.sh").read_text()
        runtime = (
            ROOT
            / "app"
            / "src"
            / "main"
            / "kotlin"
            / "io"
            / "github"
            / "ryo100794"
            / "pdocker"
            / "PdockerdRuntime.kt"
        ).read_text()
        release_doc = (ROOT / "docs" / "release" / "FDROID_RELEASE_PROCESS.md").read_text()
        self.assertIn("PDOCKER_FDROID_NO_CRANE", copy_native)
        self.assertIn("rm -f \"$JNI_DIR/libcrane.so\"", copy_native)
        self.assertIn("PDOCKER_FDROID_NO_CRANE", gradle)
        self.assertIn("F-Droid no-crane build must not stage libcrane.so", gradle)
        self.assertIn("FDROID_NO_CRANE=\"${PDOCKER_FDROID_NO_CRANE:-0}\"", release_verifier)
        self.assertIn("fdroid_no_crane: %s", release_verifier)
        self.assertIn("PDOCKER_FDROID_NO_CRANE=%q bash scripts/copy-native.sh", release_verifier)
        self.assertIn("PDOCKER_FDROID_NO_CRANE=%q ./gradlew %q --no-daemon", release_verifier)
        self.assertIn("VERIFY_NATIVE_PAYLOADS_ARGS+=(--fdroid-no-crane)", release_verifier)
        self.assertIn("export PDOCKER_FDROID_NO_CRANE=\"$FDROID_NO_CRANE\"", release_verifier)
        self.assertIn('optionalLinkTo(File(nativeDir, "libcrane.so"), File(dockerBin, "crane"))', runtime)
        self.assertIn("PDOCKER_FDROID_NO_CRANE=1", release_doc)
        verify_fast = (ROOT / "scripts" / "verify-fast.sh").read_text()
        self.assertIn("verify-fast-fdroid-no-crane-dry-run", verify_fast)

    def test_native_rebuild_dry_run_records_no_crane_mode(self):
        env = {
            **os.environ,
            "PDOCKER_NATIVE_REBUILD_UTC": "unit-fdroid-no-crane",
            "PDOCKER_FDROID_NO_CRANE": "1",
        }
        report = ROOT / "build" / "reports" / "native-rebuild-unit-fdroid-no-crane"
        subprocess.run(
            ["rm", "-rf", str(report)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        result = subprocess.run(
            ["bash", "scripts/verify-native-rebuild-release.sh"],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        self.assertIn("DRY-RUN", result.stdout)
        plan = (report / "plan.log").read_text()
        environment = json.loads((report / "environment.json").read_text())
        self.assertIn("fdroid_no_crane: 1", plan)
        self.assertIn("PDOCKER_FDROID_NO_CRANE: 1", plan)
        self.assertIn("PDOCKER_FDROID_NO_CRANE=1 bash scripts/copy-native.sh", plan)
        self.assertIn("--fdroid-no-crane", plan)
        self.assertTrue(environment["fdroid_no_crane"])
        self.assertEqual(environment["android"]["PDOCKER_FDROID_NO_CRANE_effective"], "1")

    def test_native_rebuild_dry_run_default_omits_no_crane_verify_flag(self):
        env = {
            **os.environ,
            "PDOCKER_NATIVE_REBUILD_UTC": "unit-normal-crane",
        }
        env.pop("PDOCKER_FDROID_NO_CRANE", None)
        report = ROOT / "build" / "reports" / "native-rebuild-unit-normal-crane"
        subprocess.run(
            ["rm", "-rf", str(report)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        subprocess.run(
            ["bash", "scripts/verify-native-rebuild-release.sh"],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        plan = (report / "plan.log").read_text()
        environment = json.loads((report / "environment.json").read_text())
        self.assertIn("fdroid_no_crane: 0", plan)
        self.assertIn("PDOCKER_FDROID_NO_CRANE=0 bash scripts/copy-native.sh", plan)
        self.assertNotIn("--fdroid-no-crane --write-artifact", plan)
        self.assertFalse(environment["fdroid_no_crane"])
        self.assertEqual(environment["android"]["PDOCKER_FDROID_NO_CRANE_effective"], "0")

    def test_built_apk_payloads_match_runtime_sources(self):
        apk = ROOT / "app" / "build" / "outputs" / "apk" / "compat" / "debug" / "app-compat-debug.apk"
        if not apk.is_file():
            self.skipTest(f"APK not built: {apk}")
        pairs = {
            "lib/arm64-v8a/libcrane.so": ROOT / "docker-proot-setup" / "docker-bin" / "crane",
            "lib/arm64-v8a/libcow.so": ROOT / "docker-proot-setup" / "lib" / "libcow.so",
            "assets/pdockerd/pdockerd": ROOT / "docker-proot-setup" / "bin" / "pdockerd",
        }
        with zipfile.ZipFile(apk) as zf:
            names = set(zf.namelist())
            for entry, source in pairs.items():
                with self.subTest(entry=entry):
                    self.assertIn(entry, names)
                    self.assertEqual(zf.read(entry), source.read_bytes())
            self.assertFalse(
                any(name.startswith("assets/pdockerd/__pycache__/") for name in names),
                "APK must not package pdockerd Python cache artifacts",
            )

    def test_native_payload_verifier_enforces_no_crane_apk_policy(self):
        arm64_dir = ROOT / "app" / "src" / "main" / "jniLibs" / "arm64-v8a"
        required_libs = [
            "libpdockerpty.so",
            "libpdockerdirect.so",
            "libpdockergpuexecutor.so",
            "libpdockermediaexecutor.so",
            "libpdockergpushim.so",
            "libpdockervulkanicd.so",
            "libpdockeropenclicd.so",
            "libcow.so",
        ]
        if not all((arm64_dir / name).is_file() for name in required_libs):
            self.skipTest("native payloads are not built")
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            no_crane_apk = tmp_path / "no-crane.apk"
            with zipfile.ZipFile(no_crane_apk, "w") as zf:
                for name in required_libs:
                    zf.write(arm64_dir / name, f"lib/arm64-v8a/{name}")
                zf.write(
                    ROOT / "docker-proot-setup" / "bin" / "pdockerd",
                    "assets/pdockerd/pdockerd",
                )
            artifact = tmp_path / "no-crane.json"
            ok = subprocess.run(
                [
                    "python3",
                    "scripts/verify-native-payloads.py",
                    "--apk",
                    str(no_crane_apk),
                    "--apk-arm64-only",
                    "--fdroid-no-crane",
                    "--write-artifact",
                    str(artifact),
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(ok.returncode, 0, ok.stderr + ok.stdout)
            data = json.loads(artifact.read_text())
            self.assertTrue(data["apk"]["fdroid_no_crane"])
            self.assertEqual(data["apk"]["forbidden_crane_entries"], [])

            with_crane_apk = tmp_path / "with-crane.apk"
            with zipfile.ZipFile(no_crane_apk) as src, zipfile.ZipFile(with_crane_apk, "w") as dst:
                for info in src.infolist():
                    dst.writestr(info, src.read(info.filename))
                dst.write(arm64_dir / "libcrane.so", "lib/arm64-v8a/libcrane.so")
            bad = subprocess.run(
                [
                    "python3",
                    "scripts/verify-native-payloads.py",
                    "--apk",
                    str(with_crane_apk),
                    "--apk-arm64-only",
                    "--fdroid-no-crane",
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(bad.returncode, 2)
            self.assertIn("forbidden crane payload", bad.stderr)


if __name__ == "__main__":
    unittest.main()

import os
import importlib.util
import importlib.machinery
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "docker-proot-setup" / "bin"
PDOCKER = BIN / "pdocker"
SKYDNIR = BIN / "skydnir"
PDOCKERD = BIN / "pdockerd"
SKYDNIRD = BIN / "skydnird"
BRIDGE = ROOT / "app" / "src" / "main" / "python" / "pdockerd_bridge.py"
MAIN_ACTIVITY = ROOT / "app" / "src" / "main" / "kotlin" / "io" / "github" / "ryo100794" / "pdocker" / "MainActivity.kt"
APP_GRADLE = ROOT / "app" / "build.gradle.kts"
BUILD_APK = ROOT / "scripts" / "build-apk.sh"
BUILD_ALL = ROOT / "scripts" / "build-all.sh"
VERIFY_FAST = ROOT / "scripts" / "verify-fast.sh"
COMPAT_AUDIT = ROOT / "scripts" / "compat-audit.py"
ANDROID_SMOKE = ROOT / "scripts" / "android-device-smoke.sh"
ANDROID_SELFDEBUG = ROOT / "scripts" / "android-selfdebug.sh"
OPENCL_ICD = ROOT / "docker-proot-setup" / "src" / "gpu" / "pdocker_opencl_icd.c"
MIGRATION_DOC = ROOT / "docs" / "manual" / "SKYDNIR_MIGRATION.md"


class SkydnirAliasContractTest(unittest.TestCase):
    def run_cli(self, *args, env=None):
        merged_env = os.environ.copy()
        merged_env.update(env or {})
        with tempfile.TemporaryDirectory() as tmp:
            merged_env["HOME"] = tmp
            merged_env["PDOCKER_HOME"] = str(Path(tmp) / "runtime-home")
            return subprocess.run(
                [str(args[0]), *args[1:]],
                cwd=ROOT,
                env=merged_env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

    def run_cli_without_forced_home(self, executable, home_dir, *args, env=None):
        merged_env = os.environ.copy()
        merged_env.update(env or {})
        merged_env["HOME"] = str(home_dir)
        merged_env.pop("PDOCKER_HOME", None)
        merged_env.pop("SKYDNIR_HOME", None)
        return subprocess.run(
            [str(executable), *args],
            cwd=home_dir,
            env=merged_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_skydnir_cli_alias_is_thin_and_non_warning(self):
        self.assertTrue(os.access(SKYDNIR, os.X_OK))
        wrapper = SKYDNIR.read_text(encoding="utf-8")
        self.assertIn('exec "$SCRIPT_DIR/pdocker" "$@"', wrapper)
        self.assertIn("PDOCKER_SUPPRESS_DEPRECATION_WARNING=1", wrapper)
        self.assertIn("SKYDNIR_CLI_NAME=skydnir", wrapper)

        proc = self.run_cli(SKYDNIR, "version")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("skydnir", proc.stdout)
        self.assertNotIn("deprecated", proc.stderr.lower())

    def test_legacy_pdockerd_and_pdocker_warn_only_on_external_entrypoints(self):
        proc = self.run_cli(PDOCKER, "version")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("pdocker is deprecated. Use skydnir instead.", proc.stderr)

        daemon_help = subprocess.run(
            [str(PDOCKERD), "--help"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(daemon_help.returncode, 0, daemon_help.stderr)
        self.assertIn("pdockerd is deprecated. Use skydnird instead.", daemon_help.stderr)

    def test_skydnird_alias_suppresses_legacy_daemon_warning(self):
        self.assertTrue(os.access(SKYDNIRD, os.X_OK))
        wrapper = SKYDNIRD.read_text(encoding="utf-8")
        self.assertIn('exec "$SCRIPT_DIR/pdockerd" "$@"', wrapper)
        self.assertIn("PDOCKER_SUPPRESS_DEPRECATION_WARNING=1", wrapper)
        self.assertIn("SKYDNIR_DAEMON_NAME=skydnird", wrapper)

        daemon_help = subprocess.run(
            [str(SKYDNIRD), "--help"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(daemon_help.returncode, 0, daemon_help.stderr)
        self.assertIn("skydnird", daemon_help.stdout)
        self.assertNotIn("deprecated", daemon_help.stderr.lower())

    def test_android_bridge_uses_skydnir_daemon_identity_without_renaming_storage(self):
        bridge = BRIDGE.read_text(encoding="utf-8")
        self.assertIn('os.environ.setdefault("SKYDNIR_DAEMON_NAME", "skydnird")', bridge)
        self.assertIn('os.environ.setdefault("PDOCKER_SUPPRESS_DEPRECATION_WARNING", "1")', bridge)
        self.assertIn('sys.argv = ["skydnird", "--socket", sock_path]', bridge)
        self.assertIn('os.environ["PDOCKER_HOME"] = home', bridge)

    def test_new_cli_defaults_to_skydnir_home_without_abandoning_legacy_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)

            new_home = self.run_cli_without_forced_home(SKYDNIR, home, "version")
            self.assertEqual(new_home.returncode, 0, new_home.stderr)
            self.assertIn(f"home:    {home / '.skydnir'}", new_home.stdout)

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            legacy_dir = home / ".pdocker"
            legacy_dir.mkdir()
            preserve_legacy = self.run_cli_without_forced_home(SKYDNIR, home, "version")
            self.assertEqual(preserve_legacy.returncode, 0, preserve_legacy.stderr)
            self.assertIn(f"home:    {legacy_dir}", preserve_legacy.stdout)

    def test_top_level_config_files_are_dual_read_for_cli_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "pdocker.yml").write_text("runtime_home: ./legacy-home\n", encoding="utf-8")
            (work / "skydnir.yml").write_text("runtime_home: ./skydnir-home\n", encoding="utf-8")

            proc = self.run_cli_without_forced_home(SKYDNIR, work, "version")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("home:    ./skydnir-home", proc.stdout)

    def test_pdockerd_home_selection_accepts_skydnir_home_alias(self):
        def load_home(argv0, env):
            saved_env = os.environ.copy()
            saved_argv = sys.argv[:]
            try:
                os.environ.clear()
                os.environ.update(env)
                sys.argv = [argv0]
                module_name = f"pdockerd_skydnir_home_{os.getpid()}_{len(sys.modules)}"
                loader = importlib.machinery.SourceFileLoader(module_name, str(PDOCKERD))
                spec = importlib.util.spec_from_loader(module_name, loader)
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                assert spec.loader is not None
                spec.loader.exec_module(module)
                return module.PDOCKER_HOME
            finally:
                sys.argv = saved_argv
                os.environ.clear()
                os.environ.update(saved_env)

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self.assertEqual(load_home("skydnird", {"HOME": str(home)}), str(home / ".skydnir"))
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self.assertEqual(load_home("pdockerd", {"HOME": str(home)}), str(home / ".pdocker"))
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self.assertEqual(
                load_home("pdockerd", {"HOME": str(home), "SKYDNIR_HOME": str(home / "custom-skydnir")}),
                str(home / "custom-skydnir"),
            )
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self.assertEqual(
                load_home(
                    "skydnird",
                    {
                        "HOME": str(home),
                        "SKYDNIR_HOME": str(home / "custom-skydnir"),
                        "PDOCKER_HOME": str(home / "explicit-legacy"),
                    },
                ),
                str(home / "explicit-legacy"),
            )

    def test_pdockerd_engine_identity_prefers_skydnir_daemon_name(self):
        def load_identity(argv0, env):
            saved_env = os.environ.copy()
            saved_argv = sys.argv[:]
            try:
                os.environ.clear()
                os.environ.update(env)
                sys.argv = [argv0]
                module_name = f"pdockerd_skydnir_identity_{os.getpid()}_{len(sys.modules)}"
                loader = importlib.machinery.SourceFileLoader(module_name, str(PDOCKERD))
                spec = importlib.util.spec_from_loader(module_name, loader)
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                assert spec.loader is not None
                spec.loader.exec_module(module)
                return module.DAEMON_PRODUCT_NAME, module.DAEMON_SERVER_VERSION, Path(PDOCKERD).read_text(encoding="utf-8")
            finally:
                sys.argv = saved_argv
                os.environ.clear()
                os.environ.update(saved_env)

        with tempfile.TemporaryDirectory() as tmp:
            name, version, source = load_identity(
                "skydnird",
                {"HOME": tmp, "SKYDNIR_DAEMON_NAME": "skydnird"},
            )

        self.assertEqual(name, "skydnird")
        self.assertEqual(version, "skydnird/0.1")
        self.assertIn('"Platform": {"Name": DAEMON_PRODUCT_NAME}', source)
        self.assertIn('"GitCommit": "skydnir"', source)
        self.assertIn('"ServerVersion": "24.0.0-skydnir"', source)

    def test_pdockerd_dual_reads_top_level_config_files_for_home(self):
        saved_env = os.environ.copy()
        saved_argv = sys.argv[:]
        saved_cwd = os.getcwd()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                work = Path(tmp)
                (work / "pdocker.yml").write_text("home: ./legacy\n", encoding="utf-8")
                (work / "skydnir.yml").write_text("home: ./new\n", encoding="utf-8")
                os.chdir(work)
                os.environ.clear()
                os.environ.update({"HOME": tmp})
                sys.argv = ["skydnird"]
                module_name = f"pdockerd_skydnir_top_config_{os.getpid()}_{len(sys.modules)}"
                loader = importlib.machinery.SourceFileLoader(module_name, str(PDOCKERD))
                spec = importlib.util.spec_from_loader(module_name, loader)
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                assert spec.loader is not None
                spec.loader.exec_module(module)

                self.assertEqual(module.PDOCKER_HOME, "./new")
                self.assertEqual(
                    module._read_runtime_home_config_value([
                        str(work / "pdocker.yml"),
                        str(work / "skydnir.yml"),
                    ]),
                    "./new",
                )
        finally:
            os.chdir(saved_cwd)
            sys.argv = saved_argv
            os.environ.clear()
            os.environ.update(saved_env)

    def test_pdockerd_reads_legacy_and_skydnir_common_env_files(self):
        saved_env = os.environ.copy()
        saved_argv = sys.argv[:]
        try:
            with tempfile.TemporaryDirectory() as tmp:
                home = Path(tmp) / "home"
                projects = home / "projects"
                projects.mkdir(parents=True)
                (projects / ".pdocker-common.env").write_text(
                    "PDOCKER_DOCUMENTS_ACCESS=legacy\nSHARED=old\n", encoding="utf-8"
                )
                (projects / ".skydnir-common.env").write_text(
                    "SKYDNIR_DOCUMENTS_ACCESS=skydnir\nSKYDNIR_ONLY=yes\n", encoding="utf-8"
                )
                os.environ.clear()
                os.environ.update({"HOME": tmp, "PDOCKER_HOME": str(home)})
                sys.argv = ["skydnird"]
                module_name = f"pdockerd_skydnir_env_{os.getpid()}_{len(sys.modules)}"
                loader = importlib.machinery.SourceFileLoader(module_name, str(PDOCKERD))
                spec = importlib.util.spec_from_loader(module_name, loader)
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                assert spec.loader is not None
                spec.loader.exec_module(module)

                common = module._project_common_env()
                self.assertEqual(common["SHARED"], "old")
                self.assertEqual(common["SKYDNIR_ONLY"], "yes")
                self.assertEqual(common["PDOCKER_DOCUMENTS_ACCESS"], "legacy")
                self.assertEqual(common["SKYDNIR_DOCUMENTS_ACCESS"], "skydnir")
                status = module.collect_documents_environment()
                self.assertEqual(status["Access"], "skydnir")
        finally:
            sys.argv = saved_argv
            os.environ.clear()
            os.environ.update(saved_env)

    def test_release_signing_env_prefers_skydnir_with_pdocker_fallback(self):
        gradle = APP_GRADLE.read_text(encoding="utf-8")

        self.assertIn('providers.environmentVariable("SKYDNIR_${name}").orNull', gradle)
        self.assertIn('?: providers.environmentVariable("PDOCKER_${name}").orNull', gradle)
        self.assertIn('create("skydnirRelease")', gradle)
        self.assertIn('signingConfigs.findByName("skydnirRelease")', gradle)

    def test_android_build_env_prefers_skydnir_with_pdocker_fallback(self):
        build_apk = BUILD_APK.read_text(encoding="utf-8")
        build_all = BUILD_ALL.read_text(encoding="utf-8")
        build_gpu_shim = (ROOT / "scripts" / "build-gpu-shim.sh").read_text(encoding="utf-8")
        build_native = (ROOT / "scripts" / "build-native-android-ndk.sh").read_text(encoding="utf-8")
        verify_fast = VERIFY_FAST.read_text(encoding="utf-8")
        compat_audit = COMPAT_AUDIT.read_text(encoding="utf-8")
        gradle = APP_GRADLE.read_text(encoding="utf-8")

        self.assertIn('PDOCKER_ANDROID_FLAVOR:=${SKYDNIR_ANDROID_FLAVOR:-compat}', build_apk)
        self.assertIn('PDOCKER_ANDROID_BUILD_TYPE:=${SKYDNIR_ANDROID_BUILD_TYPE:-debug}', build_apk)
        self.assertIn('PDOCKER_SKIP_NATIVE_BUILD:=${SKYDNIR_SKIP_NATIVE_BUILD:-0}', build_apk)
        self.assertIn('BUILD_TYPE="${SKYDNIR_ANDROID_BUILD_TYPE:-${PDOCKER_ANDROID_BUILD_TYPE:-debug}}"', build_all)
        self.assertIn('SKYDNIR_ANDROID_FLAVOR="$FLAVOR"', build_all)
        self.assertIn('SKYDNIR_GLIBC_ARCHES:-${PDOCKER_GLIBC_ARCHES:-arm64 armhf}', build_all)
        self.assertIn('ARCHES="${SKYDNIR_GLIBC_ARCHES:-${PDOCKER_GLIBC_ARCHES:-arm64 armhf}}"', build_gpu_shim)
        self.assertIn('SKYDNIR_NATIVE_STRIP:-${PDOCKER_NATIVE_STRIP:-0}', build_native)
        self.assertIn('System.getenv("SKYDNIR_FDROID_NO_CRANE") ?: System.getenv("PDOCKER_FDROID_NO_CRANE")', gradle)
        self.assertIn('System.getenv("SKYDNIR_GLIBC_LOADER") ?: System.getenv("PDOCKER_GLIBC_LOADER")', gradle)
        self.assertIn('nonBlankEnv("SKYDNIR_BUILD_TIME_UTC", "PDOCKER_BUILD_TIME_UTC")', gradle)
        self.assertIn('nonBlankEnv("SKYDNIR_BUILD_COMMIT", "PDOCKER_BUILD_COMMIT")', gradle)
        self.assertIn('nonBlankEnv("SKYDNIR_BUILD_NUMBER", "PDOCKER_BUILD_NUMBER")', gradle)
        self.assertIn('PDOCKER_ANDROID_FLAVOR="${SKYDNIR_ANDROID_FLAVOR:-${PDOCKER_ANDROID_FLAVOR:-compat}}"', verify_fast)
        self.assertIn('FLAVOR_ENV = "SKYDNIR_ANDROID_FLAVOR"', compat_audit)
        self.assertIn('LEGACY_FLAVOR_ENV = "PDOCKER_ANDROID_FLAVOR"', compat_audit)

    def test_android_device_env_prefers_skydnir_with_pdocker_fallback(self):
        android_smoke = ANDROID_SMOKE.read_text(encoding="utf-8")
        android_selfdebug = ANDROID_SELFDEBUG.read_text(encoding="utf-8")

        self.assertIn('PKG="${SKYDNIR_PACKAGE:-${PDOCKER_PACKAGE:-$DEFAULT_PKG}}"', android_smoke)
        self.assertIn('APK="${SKYDNIR_APK:-${PDOCKER_APK:-$DEFAULT_APK}}"', android_smoke)
        self.assertIn('SMOKE_ARTIFACT_DIR_RESOLVED="${SKYDNIR_SMOKE_ARTIFACT_DIR:-${PDOCKER_SMOKE_ARTIFACT_DIR:-', android_smoke)
        self.assertIn("SKYDNIR_PACKAGE", android_smoke)
        self.assertIn("SKYDNIR_APK", android_smoke)
        self.assertIn("SKYDNIR_SMOKE_ARTIFACT_DIR", android_smoke)
        self.assertIn('PKG="${SKYDNIR_PACKAGE:-${PDOCKER_PACKAGE:-$DEFAULT_PKG}}"', android_selfdebug)
        self.assertIn('APK="${SKYDNIR_APK:-${PDOCKER_APK:-$DEFAULT_APK}}"', android_selfdebug)
        self.assertIn("export SKYDNIR_PACKAGE", android_selfdebug)

        gpu_compare = (ROOT / "scripts" / "android-gpu-compare-bench.sh").read_text(encoding="utf-8")
        gpu_host = (ROOT / "scripts" / "android-gpu-host-bench.sh").read_text(encoding="utf-8")
        self.assertIn('CLASS_PREFIX="${SKYDNIR_CLASS_PREFIX:-${PDOCKER_CLASS_PREFIX:-io.github.ryo100794.pdocker}}"', gpu_compare)
        self.assertIn('CLASS_PREFIX="${SKYDNIR_CLASS_PREFIX:-${PDOCKER_CLASS_PREFIX:-io.github.ryo100794.pdocker}}"', gpu_host)

    def test_opencl_public_identity_uses_skydnir_with_debug_fallback(self):
        opencl = OPENCL_ICD.read_text(encoding="utf-8")
        self.assertIn('"Skydnir OpenCL bridge"', opencl)
        self.assertIn('"Skydnir GPU bridge (OpenCL)"', opencl)
        self.assertIn('"OpenCL 1.2 Skydnir"', opencl)
        self.assertIn('"Skydnir"', opencl)
        self.assertIn('getenv("SKYDNIR_OPENCL_ICD_DEBUG") || getenv("PDOCKER_OPENCL_ICD_DEBUG")', opencl)

    def test_documents_env_dual_writes_skydnir_and_pdocker_aliases(self):
        main = MAIN_ACTIVITY.read_text(encoding="utf-8")
        pdockerd = PDOCKERD.read_text(encoding="utf-8")

        for key in [
            "DOCUMENTS_HOST",
            "DOCUMENTS_MOUNT",
            "SHARED_DOCUMENTS_HOST",
            "DOCUMENTS_ACCESS",
            "DOCUMENTS_SAF_MIRROR_HOST",
            "PROJECT_VOLUME_HOST",
            "MODEL_HOST",
        ]:
            self.assertIn(f'"PDOCKER_{key}" to "SKYDNIR_{key}"', main)
        self.assertIn("applySkydnirEnvAliases(env)", main)
        self.assertIn("applySkydnirEnvAliases(updates)", main)
        self.assertIn("existingEnvValue(existing, skydnir)", main)
        self.assertIn('return "SKYDNIR_" + key[len("PDOCKER_"):] if key.startswith("PDOCKER_") else key', pdockerd)
        self.assertIn("os.environ.get(alias)", pdockerd)
        self.assertIn("common_env.get(alias)", pdockerd)

    def test_migration_doc_records_service_and_no_rename_boundaries(self):
        text = MIGRATION_DOC.read_text(encoding="utf-8")
        self.assertIn("skydnird.service", text)
        self.assertIn("`PDOCKER_HOME` wins for compatibility", text)
        self.assertIn("`SKYDNIR_HOME` is accepted", text)
        self.assertIn("Android package ID", text)
        self.assertIn("Existing JSON artifact schemas", text)


if __name__ == "__main__":
    unittest.main()

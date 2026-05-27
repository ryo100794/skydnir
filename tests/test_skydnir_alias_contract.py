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
            cwd=ROOT,
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

    def test_migration_doc_records_service_and_no_rename_boundaries(self):
        text = MIGRATION_DOC.read_text(encoding="utf-8")
        self.assertIn("skydnird.service", text)
        self.assertIn("`PDOCKER_HOME` wins for compatibility", text)
        self.assertIn("`SKYDNIR_HOME` is accepted", text)
        self.assertIn("Android package ID", text)
        self.assertIn("Existing JSON artifact schemas", text)


if __name__ == "__main__":
    unittest.main()

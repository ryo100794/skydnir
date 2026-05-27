import os
import subprocess
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


if __name__ == "__main__":
    unittest.main()

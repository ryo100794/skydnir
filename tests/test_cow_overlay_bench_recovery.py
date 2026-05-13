import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERIFY = ROOT / "scripts" / "verify-cow-overlay-bench-recovery.py"
BENCH = ROOT / "docker-proot-setup" / "src" / "overlay" / "bench_cow.sh"
RECOVERY = ROOT / "docker-proot-setup" / "src" / "overlay" / "test_cow.sh"
LIB = ROOT / "docker-proot-setup" / "src" / "overlay" / "libcow.so"


class CowOverlayBenchRecoveryTests(unittest.TestCase):
    def run_cmd(self, argv, **kwargs):
        return subprocess.run(
            argv,
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            **kwargs,
        )

    def test_static_contract_verifier_passes(self):
        out = self.run_cmd(["python3", str(VERIFY)]).stdout
        self.assertEqual(json.loads(out)["status"], "pass")

    def test_scripts_are_executable_and_shell_clean(self):
        for script in (BENCH, RECOVERY):
            self.assertTrue(os.access(script, os.X_OK), f"{script} must be executable")
            self.run_cmd(["bash", "-n", str(script)])

    def test_local_json_artifacts_when_libcow_is_available(self):
        if os.environ.get("PDOCKER_RUN_COW_OVERLAY_LOCAL_TESTS") != "1":
            self.skipTest("set PDOCKER_RUN_COW_OVERLAY_LOCAL_TESTS=1 for executable libcow gate")
        if not LIB.exists():
            self.skipTest("libcow.so is not built in this checkout")
        with tempfile.TemporaryDirectory(prefix="cow-overlay-test-") as td:
            tmp = Path(td)
            bench_json = tmp / "bench.json"
            recovery_json = tmp / "recovery.json"
            env = os.environ.copy()
            env.update(
                {
                    "COW_BENCH_OPS": "8",
                    "COW_BENCH_COPY_UP_FILES": "2",
                    "COW_BENCH_JSON": str(bench_json),
                    "COW_TEST_JSON": str(recovery_json),
                }
            )
            self.run_cmd(["bash", str(BENCH)], env=env)
            self.run_cmd(["bash", str(RECOVERY)], env=env)
            self.run_cmd(
                [
                    "python3",
                    str(VERIFY),
                    "--bench-artifact",
                    str(bench_json),
                    "--recovery-artifact",
                    str(recovery_json),
                ]
            )
            bench = json.loads(bench_json.read_text())
            recovery = json.loads(recovery_json.read_text())
            metric_names = {m["name"] for m in bench["Metrics"]}
            self.assertIn("open_close", metric_names)
            self.assertIn("layer_lookup", metric_names)
            self.assertEqual(recovery["Checks"]["hardlink_ring_corruption_rebuild"], "pass")
            self.assertEqual(recovery["Checks"]["kill_at_step_external_harness"], "planned-gap")


if __name__ == "__main__":
    unittest.main()

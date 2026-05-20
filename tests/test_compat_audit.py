import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "compat-audit.py"

spec = importlib.util.spec_from_file_location("compat_audit", SCRIPT)
compat_audit = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = compat_audit
spec.loader.exec_module(compat_audit)


class BlockingStderr:
    def read(self):
        raise AssertionError("stderr.read() must not be used for live daemon failures")


class LiveFailingProcess:
    stderr = BlockingStderr()

    def __init__(self):
        self.terminated = False
        self.killed = False
        self.communicate_calls = 0

    def poll(self):
        return None if not self.terminated and not self.killed else 1

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def communicate(self, timeout=None):
        self.communicate_calls += 1
        return "", "daemon failed after bind setup"


class StubbornProcess(LiveFailingProcess):
    def communicate(self, timeout=None):
        self.communicate_calls += 1
        if self.communicate_calls == 1:
            raise subprocess.TimeoutExpired(["pdockerd"], timeout)
        return "", "daemon ignored terminate"


class StopProcessAndCollectStderrTest(unittest.TestCase):
    def test_live_daemon_is_terminated_before_collecting_stderr(self):
        proc = LiveFailingProcess()

        stderr = compat_audit.stop_process_and_collect_stderr(proc, timeout=0.01)

        self.assertTrue(proc.terminated)
        self.assertFalse(proc.killed)
        self.assertEqual(proc.communicate_calls, 1)
        self.assertEqual(stderr, "daemon failed after bind setup")

    def test_process_is_killed_when_terminate_does_not_finish(self):
        proc = StubbornProcess()

        stderr = compat_audit.stop_process_and_collect_stderr(proc, timeout=0.01)

        self.assertTrue(proc.terminated)
        self.assertTrue(proc.killed)
        self.assertEqual(proc.communicate_calls, 2)
        self.assertEqual(stderr, "daemon ignored terminate")


class EngineBaseRouteContractTest(unittest.TestCase):
    def setUp(self):
        self.pdockerd = (ROOT / "docker-proot-setup" / "bin" / "pdockerd").read_text(encoding="utf-8")
        self.asset = (ROOT / "app" / "src" / "main" / "assets" / "pdockerd" / "pdockerd").read_text(encoding="utf-8")

    def test_staged_asset_matches_pdockerd_source(self):
        self.assertEqual(self.pdockerd, self.asset)

    def test_base_engine_routes_are_method_scoped(self):
        self.assertIn('path == "/_ping" and method in ("GET", "HEAD")', self.pdockerd)
        self.assertIn('if method == "GET":\n                self.wfile.write(b"OK")', self.pdockerd)
        self.assertIn('path == "/version" and method == "GET"', self.pdockerd)
        self.assertIn('path == "/info" and method == "GET"', self.pdockerd)

    def test_network_dynamic_routes_fail_closed_for_unsupported_post(self):
        self.assertIn('if sub and method != "POST":', self.pdockerd)
        self.assertIn('unsupported network route: {path}', self.pdockerd)
        self.assertNotIn('if method == "POST":\n                self.send_response(200); self.end_headers()', self.pdockerd)


if __name__ == "__main__":
    unittest.main()

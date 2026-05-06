import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DIRECT_EXEC = ROOT / "app" / "src" / "main" / "cpp" / "pdocker_direct_exec.c"
PDOCKERD = ROOT / "docker-proot-setup" / "bin" / "pdockerd"
PROBE = (
    ROOT
    / "app"
    / "src"
    / "main"
    / "assets"
    / "project-library"
    / "direct-runtime-probe"
    / "scripts"
    / "pdocker-container-probe.sh"
)


class MemoryGuardContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.direct = DIRECT_EXEC.read_text()
        cls.pdockerd = PDOCKERD.read_text()
        cls.probe = PROBE.read_text()

    def test_build_runtime_enables_large_allocation_guard_by_default(self):
        self.assertIn('"PDOCKER_DIRECT_MEMORY_GUARD"', self.pdockerd)
        self.assertIn('os.environ.get("PDOCKER_BUILD_MEMORY_GUARD", "1")', self.pdockerd)
        self.assertIn('os.environ.get("PDOCKER_BUILD_MEMORY_GUARD_MIN_REQUEST", "64M")', self.pdockerd)
        self.assertIn('os.environ.get("PDOCKER_BUILD_MEMORY_GUARD_MIN_AVAILABLE", "384M")', self.pdockerd)
        self.assertIn('os.environ.get("PDOCKER_BUILD_MEMORY_GUARD_MIN_SWAP", "128M")', self.pdockerd)

    def test_guard_denies_before_kernel_allocation_syscall_runs(self):
        guard = self._function_body("maybe_guard_memory_syscall")
        self.assertIn("memory_guard_would_deny", guard)
        self.assertIn("complete_emulated_syscall", guard)
        self.assertIn("completed_in_userland", guard)
        self.assertRegex(guard, r"state->emulated_nr\s*=\s*state->last_nr")

    def test_guard_returns_linux_compatible_failure_shapes(self):
        guard = self._function_body("maybe_guard_memory_syscall")
        self.assertRegex(
            guard,
            r"state->last_nr\s*==\s*214\s*&&\s*state->last_brk\s*!=\s*0",
            "brk must not return raw -ENOMEM; Linux reports failure by keeping the old program break",
        )
        self.assertRegex(guard, r"state->emulated_result\s*=\s*state->last_brk")
        self.assertRegex(guard, r"state->emulated_result\s*=\s*\(unsigned long long\)-ENOMEM")

    def test_guard_threshold_uses_available_memory_and_swap_floor(self):
        guard = self._function_body("memory_guard_would_deny")
        for marker in [
            "requested_bytes < g_memory_guard_min_request",
            "read_meminfo_bytes",
            "avail < g_memory_guard_min_available",
            "swap < g_memory_guard_min_swap",
            "avail < requested_bytes + g_memory_guard_min_available",
        ]:
            self.assertIn(marker, guard)

    def test_probe_observes_enomem_as_expected_failure(self):
        for marker in [
            "except OSError as exc",
            "exc.errno == errno.ENOMEM",
            "large_allocation_guard_ok",
            "expected ENOMEM from memory guard",
        ]:
            self.assertIn(marker, self.probe)

    def _function_body(self, name):
        match = re.search(
            rf"static [^{{;]+ {name}\([^)]*\) \{{(?P<body>.*?)\n\}}",
            self.direct,
            flags=re.S,
        )
        self.assertIsNotNone(match, f"{name} not found")
        return match.group("body")


if __name__ == "__main__":
    unittest.main()

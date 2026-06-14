import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DIRECT_EXEC = ROOT / "app" / "src" / "main" / "cpp" / "pdocker_direct_exec.c"


class DirectExecChrootErrnoContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = DIRECT_EXEC.read_text(encoding="utf-8")

    def test_chroot_syscall_is_named_for_diagnostics(self):
        self.assertIn('case 51: return "chroot";', self.src)

    def test_chroot_fails_like_unprivileged_linux_not_bad_syscall(self):
        emulate = re.search(
            r"static int syscall_emulate_errno\(long nr, int \*err\) \{(?P<body>.*?)\n\}",
            self.src,
            re.S,
        )
        self.assertIsNotNone(emulate)
        body = emulate.group("body")
        self.assertIn("nr == 51", body)
        self.assertIn("*err = EPERM", body)
        self.assertIn("return 1;", body)

    def test_selective_seccomp_returns_eperm_without_ptrace_roundtrip(self):
        self.assertIn("ADD_ERRNO_SYSCALL(51, EPERM)", self.src)
        self.assertNotIn("ADD_TRACE_SYSCALL(51)", self.src)


if __name__ == "__main__":
    unittest.main()

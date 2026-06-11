import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DIRECT_EXEC = ROOT / "app" / "src" / "main" / "cpp" / "pdocker_direct_exec.c"


class UnixSocketRewriteContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.direct = DIRECT_EXEC.read_text()
        match = re.search(
            r"static int rewrite_unix_sockaddr_arg\([^)]*\) \{(?P<body>.*?)\n\}",
            cls.direct,
            flags=re.S,
        )
        if match is None:
            raise AssertionError("rewrite_unix_sockaddr_arg not found")
        cls.body = match.group("body")

    def test_af_unix_rewrite_prefers_original_sockaddr_buffer(self):
        # connect_queue() passes a mutable sockaddr_un with sizeof(sockaddr_un).
        # Rewriting it in-place avoids arbitrary scratch-stack writes that can
        # hit unmapped guard pages and prevent the GPU bridge socket connect.
        self.assertIn("if (len >= rewritten_len)", self.body)
        self.assertIn("write_tracee_data(pid, addr_ptr, &rewritten_addr, write_len)", self.body)
        self.assertIn("rewrite-in-place", self.body)
        self.assertLess(
            self.body.index("write_tracee_data(pid, addr_ptr, &rewritten_addr, write_len)"),
            self.body.index("unsigned long long scratch"),
        )

    def test_af_unix_scratch_fallback_uses_standard_small_offset(self):
        self.assertIn("regs->sp - 8192u", self.body)
        self.assertNotIn("regs->sp - 24576u", self.body)
        self.assertIn("regs->regs[2] = (unsigned long long)rewritten_len", self.body)

    def test_af_unix_rewrite_keeps_sun_path_capacity_guard(self):
        self.assertIn("strlen(rewritten) >= sizeof(addr.sun_path)", self.body)
        self.assertIn("AF_UNIX path too long", self.body)


if __name__ == "__main__":
    unittest.main()

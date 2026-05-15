import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "tests" / "direct_syscall_coverage.json"
TODO = ROOT / "docs" / "plan" / "TODO.md"


REQUIRED_PHASE2 = {
    "attach-pty-signals": ["attach", "PTY", "SIGINT", "128+signal"],
    "syscall-errno-parity": ["errno", "ENOSYS", "openat2"],
    "path-mediation-binds-volumes": ["bind", "named volume", "AF_UNIX"],
    "linkat-hardlink-semantics": ["linkat", "hardlink"],
    "proc-self-exe-no-mutation": ["/proc/self/exe", "readlink", "mutating"],
    "run-changed-path-manifest": ["RUN", "changed-path", "manifest"],
}


class DirectSyscallPhase2ContractsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(MANIFEST.read_text())
        cls.todo = TODO.read_text()
        cls.heavy_cases = {case["id"]: case for case in cls.manifest["heavy_cases"]}
        cls.phase2 = {entry["id"]: entry for entry in cls.manifest["phase2_contracts"]}

    def test_phase2_contracts_pin_every_open_todo_lane(self):
        self.assertFalse(set(REQUIRED_PHASE2) - set(self.phase2))
        for contract_id, markers in REQUIRED_PHASE2.items():
            entry = self.phase2[contract_id]
            haystack = json.dumps(entry, sort_keys=True)
            with self.subTest(contract=contract_id):
                self.assertNotEqual(entry.get("status"), "closed")
                self.assertTrue(entry.get("close_requires"))
                self.assertTrue(entry.get("fast_gate"))
                self.assertTrue(entry.get("cases"))
                missing = [marker for marker in markers if marker not in haystack]
                self.assertFalse(missing, f"{contract_id} missing marker(s): {missing}")

    def test_phase2_contract_cases_exist_and_are_explicit_about_planned_gaps(self):
        planned_or_partial = set()
        for entry in self.phase2.values():
            for case_id in entry["cases"]:
                self.assertIn(case_id, self.heavy_cases, entry["id"])
                case = self.heavy_cases[case_id]
                if case.get("runnable") is False:
                    planned_or_partial.add(entry["id"])
                    text = f"{case['command']}\n{case['checks']}".lower()
                    self.assertTrue(
                        "planned" in text or "device" in text or "artifact" in text,
                        f"{case_id} must explain its planned device evidence",
                    )
        self.assertIn("attach-pty-signals", planned_or_partial)
        self.assertIn("path-mediation-binds-volumes", planned_or_partial)
        self.assertIn("run-changed-path-manifest", planned_or_partial)

    def test_todo_ledger_still_names_phase2_source_items(self):
        for entry in self.phase2.values():
            with self.subTest(contract=entry["id"]):
                for marker in entry["todo_markers"]:
                    self.assertIn(marker, self.todo)

    def test_known_gaps_are_tied_to_phase2_contracts(self):
        known = self.manifest["known_gaps"]
        refs = {gap["phase2_contract"] for gap in known}
        for required in [
            "attach-pty-signals",
            "linkat-hardlink-semantics",
            "proc-self-exe-no-mutation",
            "run-changed-path-manifest",
        ]:
            self.assertIn(required, refs)
        for gap in known:
            self.assertIn(gap["phase2_contract"], self.phase2)
            self.assertGreaterEqual(len(gap.get("reason", "")), 24)


if __name__ == "__main__":
    unittest.main()

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "tests" / "direct_syscall_coverage.json"


REQUIRED_PATH_VARIANTS = {
    "path.absolute-rootfs-rewrite",
    "path.relative-dirfd-preserve",
    "path.rootfs-already-host-path",
    "path.proc-dev-sys-no-rewrite",
    "path.parent-segment-scratch-fallback",
    "path.bind-resolution",
    "path.dual-path-operations",
    "path.af-unix-socket",
    "path.exec-script-and-long-argv",
    "path.rootfs-fd-lifecycle",
}


REQUIRED_BOUNDARY_VALUES = {
    "boundary.read-tracee-string-null-and-cap",
    "boundary.path-max-and-enametoolong",
    "boundary.getcwd-erange",
    "boundary.sockaddr-min-and-sun-path-limit",
    "boundary.exec-argc-and-scratch-limit",
    "boundary.memory-guard-threshold",
    "boundary.uid-gid-minus-one",
    "boundary.wait-exit-signal-code",
}


REQUIRED_BRANCH_DECISIONS = {
    "branch.should-rewrite-path",
    "branch.resolve-guest-host-path",
    "branch.rewrite-at-path-arg",
    "branch.rewrite-unix-sockaddr",
    "branch.rewrite-execve-arg",
    "branch.memory-guard",
    "branch.credentials-minus-one",
    "branch.getcwd-emulation",
    "branch.tracee-lifecycle",
}


class DirectSyscallPathBoundaryMatrixTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(MANIFEST.read_text())
        cls.heavy_case_ids = {
            case["id"] for case in cls.manifest["heavy_cases"]
        }
        cls.coverage_by_heavy_case = {}
        for entry in cls.manifest["coverage"]:
            cls.coverage_by_heavy_case.setdefault(entry["heavy_case"], set()).update(entry["syscalls"])

    def assert_matrix_complete(self, name, required_ids):
        matrix = self.manifest.get(name)
        self.assertIsInstance(matrix, list)
        self.assertTrue(matrix, f"{name} must not be empty")
        seen = {entry["id"] for entry in matrix}
        self.assertFalse(required_ids - seen, f"{name} missing {sorted(required_ids - seen)}")
        for entry in matrix:
            with self.subTest(matrix=name, entry=entry["id"]):
                self.assertTrue(
                    entry.get("applies_to") or entry.get("function"),
                    f"{entry['id']} needs applies_to or function",
                )
                self.assertTrue(entry.get("cases"), f"{entry['id']} needs cases")
                self.assertGreaterEqual(len(entry.get("contract", "")), 24)
                missing_cases = set(entry["cases"]) - self.heavy_case_ids
                self.assertFalse(missing_cases, f"{entry['id']} references missing cases {missing_cases}")

    def test_path_variant_matrix_covers_all_required_routes(self):
        self.assert_matrix_complete("path_variant_matrix", REQUIRED_PATH_VARIANTS)

    def test_boundary_value_matrix_covers_required_edges(self):
        self.assert_matrix_complete("boundary_value_matrix", REQUIRED_BOUNDARY_VALUES)

    def test_branch_decision_matrix_covers_required_functions(self):
        self.assert_matrix_complete("branch_decision_matrix", REQUIRED_BRANCH_DECISIONS)
        for entry in self.manifest["branch_decision_matrix"]:
            with self.subTest(branch=entry["id"]):
                self.assertTrue(entry.get("function"))
                self.assertGreaterEqual(len(entry.get("branches", [])), 2)

    def test_branch_matrix_has_explicit_false_and_success_routes(self):
        expected_markers = {
            "branch.should-rewrite-path": ["no rewrite", "rewrite"],
            "branch.resolve-guest-host-path": ["negative errno", "success"],
            "branch.rewrite-at-path-arg": ["no rewrite", "rootfs-fd", "scratch"],
            "branch.rewrite-unix-sockaddr": ["no rewrite", "too-long", "success"],
            "branch.rewrite-execve-arg": ["no rewrite", "script", "success"],
            "branch.memory-guard": ["allow", "ENOMEM"],
            "branch.credentials-minus-one": ["preserve", "set"],
            "branch.getcwd-emulation": ["fallback", "ERANGE"],
            "branch.tracee-lifecycle": ["normal", "signaled", "fork"],
        }
        entries = {entry["id"]: entry for entry in self.manifest["branch_decision_matrix"]}
        for entry_id, markers in expected_markers.items():
            branch_text = "\n".join(entries[entry_id]["branches"])
            with self.subTest(branch=entry_id):
                missing = [marker for marker in markers if marker not in branch_text]
                self.assertFalse(missing, f"{entry_id} missing branch marker(s) {missing}")

    def test_every_path_syscall_contract_has_a_path_or_boundary_case(self):
        path_syscalls = {
            "setxattr", "lsetxattr", "getxattr", "lgetxattr", "listxattr",
            "llistxattr", "removexattr", "lremovexattr", "mknodat",
            "mkdirat", "unlinkat", "renameat", "renameat2", "symlinkat",
            "linkat", "readlinkat", "getcwd", "statfs", "faccessat",
            "chdir", "fchmodat", "fchownat", "openat", "newfstatat",
            "utimensat", "name_to_handle_at", "statx", "openat2",
            "faccessat2", "execve", "execveat", "bind", "connect",
        }
        covered = set()
        for entry in self.manifest["coverage"]:
            if any(area in entry.get("areas", []) for area in (
                "file_path_open_stat_access_getcwd",
                "exec_argv_rootfs_resolution",
                "socket_unix_path_rewrite",
                "cow_path_operations",
            )):
                covered.update(entry["syscalls"])
        self.assertFalse(path_syscalls - covered, f"path syscall coverage missing {sorted(path_syscalls - covered)}")

    def test_boundary_scenarios_are_not_all_planned(self):
        runnable_cases = {
            case["id"] for case in self.manifest["heavy_cases"]
            if case.get("runnable") is True
        }
        for entry in self.manifest["boundary_value_matrix"]:
            with self.subTest(boundary=entry["id"]):
                self.assertTrue(
                    runnable_cases.intersection(entry["cases"]),
                    f"{entry['id']} must be tied to at least one runnable heavy/local case",
                )


if __name__ == "__main__":
    unittest.main()

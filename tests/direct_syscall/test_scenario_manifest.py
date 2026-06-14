import importlib.util
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "run_direct_syscall_scenarios.py"
MANIFEST = ROOT / "tests" / "direct_syscall_coverage.json"

spec = importlib.util.spec_from_file_location("run_direct_syscall_scenarios", RUNNER)
runner = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = runner
spec.loader.exec_module(runner)


class DirectSyscallScenarioManifestTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(MANIFEST.read_text())
        cls.cases = {case["id"]: case for case in cls.manifest["heavy_cases"]}

    def assert_case_covers(self, case_id, needles):
        case = self.cases[case_id]
        haystack = f"{case['command']}\n{case['checks']}"
        missing = [needle for needle in needles if needle not in haystack]
        self.assertEqual(missing, [], f"{case_id} is missing scenario marker(s)")

    def test_local_acceptance_lane_never_requires_adb(self):
        commands = runner.lane_commands(runner.LANE_LOCAL)

        self.assertGreaterEqual(len(commands), 3)
        for _label, command in commands:
            rendered = " ".join(command)
            self.assertNotIn(" adb ", f" {rendered} ")
        self.assertTrue(any("--dry-run" in command for _label, command in commands))

    def test_manifest_enumerates_exec_path_and_stat_open_scenarios(self):
        self.assert_case_covers(
            "android.direct.path-open-stat-access-cwd",
            ["pwd", "test -r /etc/passwd", "stat /etc/passwd", "os.getcwd()", "ERANGE"],
        )
        self.assert_case_covers(
            "android.direct.exec-argv-rootfs",
            ["PDOCKER_DIRECT_TRACE_EXEC=1", "/bin/echo", "pdocker-argv-probe", "PATH=/tmp"],
        )

    def test_manifest_enumerates_mutation_and_metadata_scenarios(self):
        self.assert_case_covers(
            "android.direct.path-mutation",
            ["mkdir -p", "mv ", "rm ", "openat2", "name_to_handle_at"],
        )
        self.assert_case_covers(
            "android.direct.path-metadata",
            ["chmod", "touch -m", "os.utime", "setxattr"],
        )

    def test_manifest_enumerates_link_proc_and_af_unix_scenarios(self):
        self.assert_case_covers(
            "android.direct.proc-exe-and-links",
            ["readlink /proc/self/exe", "ln ", "ln -s", "/bin/sh"],
        )
        self.assert_case_covers(
            "android.direct.unix-socket-connect",
            ["AF_UNIX", "connect", "rewritten host path"],
        )
        self.assertEqual(runner.case_status(self.cases["android.direct.unix-socket-connect"]), runner.STATUS_PLANNED)

    def test_manifest_enumerates_proc_mount_chroot_isolation_gap(self):
        self.assert_case_covers(
            "android.direct.proc-mount-chroot-isolation",
            [
                "/proc/self/root",
                "/proc/self/status",
                "/proc/self/mountinfo",
                "/proc/mounts",
                "Android app-private",
                "chroot syscall 51",
                "EPERM",
                "ENOSYS",
                "Bad system call",
                "mount output",
                "synthetic",
                "denied",
                "sleep 3",
                "ps -e",
                "sleep child",
            ],
        )
        self.assertEqual(
            runner.case_status(self.cases["android.direct.proc-mount-chroot-isolation"]),
            runner.STATUS_PLANNED,
        )

    def test_manifest_marks_device_only_scenarios_explicitly(self):
        for case_id, case in self.cases.items():
            if case["tier"] != "heavy-android":
                continue
            status = runner.case_status(case)
            command = case["command"]
            if status == runner.STATUS_RUNNABLE:
                self.assertIn("adb shell", command, case_id)
                self.assertIn("ROOTFS", command, case_id)
            else:
                self.assertNotIn("adb shell", command, case_id)
                self.assertNotEqual(command.strip(), "", case_id)

    def test_manifest_has_generic_container_probe(self):
        self.assert_case_covers(
            "container.direct.runtime-probe",
            [
                "scripts/container-direct-probe.sh",
                "without adb",
                "flash_attn",
                "large allocation guard",
            ],
        )
        self.assertEqual(
            runner.case_status(self.cases["container.direct.runtime-probe"]),
            runner.STATUS_RUNNABLE,
        )


if __name__ == "__main__":
    unittest.main()

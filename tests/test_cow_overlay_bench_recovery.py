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

    def recovery_case(self, case_id, fault="injected fault", evidence=None):
        evidence = evidence or f"{case_id} lower upper .cow unchanged orphan removed evidence"
        return {
            "Id": case_id,
            "Operation": case_id,
            "Fault": fault,
            "ExpectedRecovery": f"{case_id} expected fail-closed recovery",
            "Status": "pass",
            "Evidence": evidence,
        }

    def valid_recovery_artifact(self):
        required_cases = [
            "copy_up.before_rename",
            "copy_up.kill_before_rename_recovery",
            "copy_up.truncate_before_rename",
            "metadata.chmod_before_rename",
            "rename.destination_copyup_fail_closed",
            "renameat.destination_copyup_fail_closed",
            "whiteout.before_publish",
            "rename.before_publish",
            "archive_put.stage_failure",
            "hardlink_metadata.corrupt_rebuild",
            "low_space.copy_up_enospc",
        ]
        cases = []
        for cid in required_cases:
            fault = "injected fault"
            evidence = f"{cid} fail-closed evidence"
            if cid == "copy_up.before_rename":
                fault = "PDOCKER_COW_FAIL_BEFORE_RENAME injection"
                evidence = "write returned failure; lower and upper payload stayed unchanged; no .cow temp remained"
            elif cid == "copy_up.kill_before_rename_recovery":
                fault = "PDOCKER_COW_FAIL_STEP=kill:copyup.before_rename"
                evidence = "killed copy-up left an orphan .cow temp; startup cleanup removed it and lower/upper payload stayed unchanged"
            cases.append(self.recovery_case(cid, fault=fault, evidence=evidence))
        kill_case = next(case for case in cases if case["Id"] == "copy_up.kill_before_rename_recovery")
        return {
            "SchemaVersion": 1,
            "Kind": "cow-overlay-recovery",
            "Status": "pass",
            "Checks": {
                "copy_up_fail_closed": "pass",
                "copy_up_kill_step_recovery": "pass",
                "truncate_fail_closed": "pass",
                "metadata_fail_closed": "pass",
                "rename_destination_copyup_fail_closed": "pass",
                "whiteout_fail_closed": "pass",
                "rename_fail_closed": "pass",
                "archive_put_fail_closed": "pass",
                "low_space_fail_closed": "pass",
                "hardlink_ring_corruption_rebuild": "pass",
                "kill_at_step_external_harness": "planned-gap",
            },
            "CaseResults": cases,
            "NegativeCases": list(cases),
            "KillAtStepConcreteCases": [
                {
                    "Id": kill_case["Id"],
                    "Step": "copyup.before_rename",
                    "Fault": kill_case["Fault"],
                    "ExpectedRecovery": kill_case["ExpectedRecovery"],
                    "Status": kill_case["Status"],
                    "Evidence": kill_case["Evidence"],
                }
            ],
            "KillAtStepPlannedCases": [
                {"Step": "copy-up temp payload write", "ExpectedRecovery": "external harness recovery", "Status": "planned-gap"},
                {"Step": "copy-up rename publication", "ExpectedRecovery": "external harness recovery", "Status": "planned-gap"},
                {"Step": "whiteout creation", "ExpectedRecovery": "external harness recovery", "Status": "planned-gap"},
                {"Step": "archive PUT stage publication", "ExpectedRecovery": "external harness recovery", "Status": "planned-gap"},
                {"Step": "rename destination publication", "ExpectedRecovery": "external harness recovery", "Status": "planned-gap"},
            ],
        }

    def run_recovery_artifact_verifier(self, artifact):
        with tempfile.TemporaryDirectory(prefix="cow-overlay-artifact-test-") as td:
            path = Path(td) / "recovery.json"
            path.write_text(json.dumps(artifact), encoding="utf-8")
            return subprocess.run(
                ["python3", str(VERIFY), "--recovery-artifact", str(path)],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

    def test_static_contract_verifier_passes(self):
        out = self.run_cmd(["python3", str(VERIFY)]).stdout
        self.assertEqual(json.loads(out)["status"], "pass")

    def test_recovery_artifact_accepts_concrete_copyup_kill_step_evidence(self):
        result = self.run_recovery_artifact_verifier(self.valid_recovery_artifact())
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_recovery_artifact_rejects_missing_copyup_fail_evidence(self):
        artifact = self.valid_recovery_artifact()
        for case in artifact["CaseResults"]:
            if case["Id"] == "copy_up.before_rename":
                case["Evidence"] = "write copy-up failure checks passed before artifact emission"
        result = self.run_recovery_artifact_verifier(artifact)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("copy_up.before_rename evidence", result.stderr)

    def test_recovery_artifact_rejects_missing_copyup_kill_evidence(self):
        artifact = self.valid_recovery_artifact()
        for case in artifact["CaseResults"]:
            if case["Id"] == "copy_up.kill_before_rename_recovery":
                case["Evidence"] = "planned external kill case"
        artifact["KillAtStepConcreteCases"][0]["Evidence"] = "planned external kill case"
        result = self.run_recovery_artifact_verifier(artifact)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("copy_up.kill_before_rename_recovery evidence", result.stderr)

    def test_recovery_artifact_rejects_missing_concrete_kill_case_promotion(self):
        artifact = self.valid_recovery_artifact()
        artifact["KillAtStepConcreteCases"] = []
        result = self.run_recovery_artifact_verifier(artifact)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("concrete kill-at-step cases missing", result.stderr)

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
            self.assertEqual(recovery["Checks"]["copy_up_kill_step_recovery"], "pass")
            self.assertEqual(recovery["Checks"]["rename_destination_copyup_fail_closed"], "pass")
            self.assertEqual(recovery["Checks"]["whiteout_fail_closed"], "pass")
            self.assertEqual(recovery["Checks"]["rename_fail_closed"], "pass")
            self.assertEqual(recovery["Checks"]["archive_put_fail_closed"], "pass")
            self.assertEqual(recovery["Checks"]["low_space_fail_closed"], "pass")
            self.assertEqual(recovery["Checks"]["kill_at_step_external_harness"], "planned-gap")
            case_ids = {case["Id"] for case in recovery["CaseResults"]}
            for required in {
                "copy_up.before_rename",
                "copy_up.kill_before_rename_recovery",
                "metadata.chmod_before_rename",
                "rename.destination_copyup_fail_closed",
                "renameat.destination_copyup_fail_closed",
                "whiteout.before_publish",
                "rename.before_publish",
                "archive_put.stage_failure",
                "hardlink_metadata.corrupt_rebuild",
                "low_space.copy_up_enospc",
            }:
                self.assertIn(required, case_ids)
            concrete_ids = {case["Id"] for case in recovery["KillAtStepConcreteCases"]}
            self.assertIn("copy_up.kill_before_rename_recovery", concrete_ids)
            negative_ids = {case["Id"] for case in recovery["NegativeCases"]}
            self.assertGreaterEqual(negative_ids, case_ids)


if __name__ == "__main__":
    unittest.main()

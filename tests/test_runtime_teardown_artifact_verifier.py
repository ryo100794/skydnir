from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERIFIER = ROOT / "scripts" / "verify-runtime-teardown-artifact.py"
PLAN = ROOT / "scripts" / "verify-service-truth-plan.py"

_spec = importlib.util.spec_from_file_location("verify_service_truth_plan", PLAN)
assert _spec and _spec.loader
verify_service_truth_plan = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(verify_service_truth_plan)


def runtime_teardown_fixture():
    artifact, proofs, negatives = verify_service_truth_plan.build_runtime_teardown_success_fixture()
    return deepcopy(artifact), deepcopy(proofs), deepcopy(negatives)


class RuntimeTeardownArtifactVerifierTest(unittest.TestCase):
    def run_verifier(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(VERIFIER), *args],
            cwd=cwd or ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def write_device_pass_fixture(self, root: Path, mutate=None) -> Path:
        artifact, proofs, negatives = runtime_teardown_fixture()
        if mutate:
            mutate(artifact, proofs, negatives)
        evidence = root / "runtime-teardown"
        evidence.mkdir(parents=True)
        for name, proof in proofs.items():
            (evidence / f"{name}.json").write_text(json.dumps(proof), encoding="utf-8")
        artifact["NegativeCases"] = {}
        for name, negative in negatives.items():
            (evidence / f"{name}.json").write_text(json.dumps(negative), encoding="utf-8")
            artifact["NegativeCases"][name] = f"files/pdocker/diagnostics/runtime-teardown/{name}.json"
        artifact_path = root / "runtime-teardown-latest.json"
        artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
        return artifact_path

    def test_expect_planned_gap_accepts_only_non_promoting_scaffold(self):
        artifact, _proofs, _negatives = runtime_teardown_fixture()
        artifact["Status"] = "planned-gap"
        artifact["Success"] = False
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runtime-teardown-latest.json"
            path.write_text(json.dumps(artifact), encoding="utf-8")
            ok = self.run_verifier("--expect-planned-gap", str(path))
            self.assertEqual(ok.returncode, 0, ok.stderr)
            self.assertIn("planned-gap non-promoting", ok.stdout)

            artifact["Success"] = True
            path.write_text(json.dumps(artifact), encoding="utf-8")
            bad = self.run_verifier("--expect-planned-gap", str(path))
            self.assertNotEqual(bad.returncode, 0)

    def test_device_pass_requires_external_same_id_and_negative_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact_path = self.write_device_pass_fixture(root)
            ok = self.run_verifier(str(artifact_path), "--evidence-root", str(root / "runtime-teardown"))
            self.assertEqual(ok.returncode, 0, ok.stderr)
            self.assertIn("device-pass teardown proof", ok.stdout)

    def test_device_pass_rejects_missing_proof_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact_path = self.write_device_pass_fixture(root)
            (root / "runtime-teardown" / "same-container-id-stop-rm.json").unlink()
            bad = self.run_verifier(str(artifact_path), "--evidence-root", str(root / "runtime-teardown"))
            self.assertNotEqual(bad.returncode, 0)
            self.assertIn("not found", bad.stderr)

    def test_device_pass_rejects_fake_reduction_success(self):
        def mutate(_artifact, proofs, _negatives):
            proofs["same-container-id-stop-rm"]["VerifierReduction"]["DirectChildAbsence"] = False

        with tempfile.TemporaryDirectory() as tmp:
            artifact_path = self.write_device_pass_fixture(Path(tmp), mutate=mutate)
            bad = self.run_verifier(str(artifact_path), "--evidence-root", str(Path(tmp) / "runtime-teardown"))
            self.assertNotEqual(bad.returncode, 0)
            self.assertIn("DirectChildAbsence", bad.stderr)

    def test_planned_gap_is_not_device_pass(self):
        artifact, _proofs, _negatives = runtime_teardown_fixture()
        artifact["Status"] = "planned-gap"
        artifact["Success"] = False
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runtime-teardown-latest.json"
            path.write_text(json.dumps(artifact), encoding="utf-8")
            bad = self.run_verifier(str(path))
            self.assertEqual(bad.returncode, 2)
            self.assertIn("expected Status=device-pass", bad.stderr)


if __name__ == "__main__":
    unittest.main()

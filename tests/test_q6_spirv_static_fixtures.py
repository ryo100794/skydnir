import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERIFIER = ROOT / "scripts" / "verify-q6-spirv-static-fixtures.py"


class Q6SpirvStaticFixtureVerifierTest(unittest.TestCase):
    def test_q6_spirv_static_fixture_verifier_passes_and_records_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "q6-static-fixtures.json"
            result = subprocess.run(
                ["python3", str(VERIFIER), "--json-out", str(out)],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            payload = json.loads(out.read_text(encoding="utf-8"))
            stdout_payload = json.loads(result.stdout)

        self.assertTrue(payload["valid"], payload)
        self.assertTrue(stdout_payload["valid"], result.stdout)
        self.assertEqual(payload["schema"], "skydnir.q6-spirv-static-fixtures.v1")
        self.assertEqual(payload["comparisons"]["safe_self"]["q6_static_boundary"]["summary"], "q6-static-match")
        self.assertEqual(
            payload["comparisons"]["native_effective"]["q6_static_boundary"]["summary"],
            "q6-final-store-execution-shape",
        )
        self.assertFalse(payload["comparisons"]["safe_native"]["all_match"])
        self.assertNotEqual(
            payload["comparisons"]["safe_native"]["q6_static_boundary"]["summary"],
            "q6-static-match",
        )
        self.assertIn("safe", payload["fixtures"])
        self.assertIn("native", payload["fixtures"])
        self.assertIn("effective", payload["fixtures"])


if __name__ == "__main__":
    unittest.main()

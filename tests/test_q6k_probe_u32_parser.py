import json
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PARSER = ROOT / "scripts" / "parse-q6k-probe-u32.py"


def f32_bits(value):
    return struct.unpack("<I", struct.pack("<f", value))[0]


def sample(index, value):
    return {"index": index, "value": value}


class Q6KProbeU32ParserTest(unittest.TestCase):
    def run_parser(self, payload):
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact = Path(tmpdir) / "artifact.json"
            report = Path(tmpdir) / "report.json"
            artifact.write_text(json.dumps(payload), encoding="utf-8")
            result = subprocess.run(
                [str(PARSER), str(artifact), "--json-out", str(report)],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            parsed = json.loads(report.read_text(encoding="utf-8")) if report.exists() else None
            return result, parsed

    def test_parser_accepts_expected_q6_write_probe_records(self):
        values = {}
        for base, candidate, role, value in [
            (8, 39, 1, 1.25),
            (20, 49, 2, 2.5),
            (32, 64, 4, 3.75),
            (44, 105, 1, 4.25),
            (56, 115, 2, 5.5),
            (68, 130, 4, 6.75),
        ]:
            values[base] = candidate
            values[base + 1] = role
            values[base + 2] = f32_bits(value)
        payload = {
            "gpu": {
                "diagnostics": {
                    "q6": {
                        "binding_details": [
                            {
                                "set": 0,
                                "binding": 5,
                                "debug_probe_binding": True,
                                "u32_after_dispatch": [
                                    sample(index, values.get(index, 0))
                                    for index in range(96)
                                ],
                                "u32_after_writeback": [
                                    sample(index, values.get(index, 0))
                                    for index in range(96)
                                ],
                            }
                        ]
                    }
                }
            }
        }
        result, report = self.run_parser(payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(report["summary"], "pass")
        self.assertEqual(report["debug_binding_count"], 1)
        records = report["bindings"][0]["records"]
        self.assertEqual([record["candidate_id"] for record in records], [39, 49, 64, 105, 115, 130])
        self.assertEqual([record["role_code"] for record in records], [1, 2, 4, 1, 2, 4])
        self.assertAlmostEqual(records[0]["value_f32"], 1.25)
        self.assertAlmostEqual(records[-1]["writeback_value_f32"], 6.75)

    def test_parser_fails_closed_when_probe_metadata_is_missing(self):
        payload = {
            "binding_details": [
                {
                    "binding": 5,
                    "debug_probe_binding": True,
                    "u32_after_dispatch": [sample(index, 0) for index in range(96)],
                }
            ]
        }
        result, report = self.run_parser(payload)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(report["summary"], "fail")
        self.assertIn("candidate mismatch", "\n".join(report["failures"]))


if __name__ == "__main__":
    unittest.main()

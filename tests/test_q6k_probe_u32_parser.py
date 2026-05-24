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
            (32, 61, 3, 3.25),
            (44, 63, 3, 3.5),
            (56, 64, 4, 3.75),
            (68, 105, 1, 4.25),
            (80, 115, 2, 5.5),
            (92, 127, 3, 6.0),
            (104, 129, 3, 6.25),
            (116, 130, 4, 6.75),
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
                                    for index in range(144)
                                ],
                                "u32_after_writeback": [
                                    sample(index, values.get(index, 0))
                                    for index in range(144)
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
        self.assertEqual(
            [record["candidate_id"] for record in records],
            [39, 49, 61, 63, 64, 105, 115, 127, 129, 130],
        )
        self.assertEqual([record["role_code"] for record in records], [1, 2, 3, 3, 4, 1, 2, 3, 3, 4])
        self.assertAlmostEqual(records[0]["value_f32"], 1.25)
        self.assertAlmostEqual(records[-1]["writeback_value_f32"], 6.75)

    def test_parser_fails_closed_when_probe_metadata_is_missing(self):
        payload = {
            "binding_details": [
                {
                    "binding": 5,
                    "debug_probe_binding": True,
                    "u32_after_dispatch": [sample(index, 0) for index in range(144)],
                }
            ]
        }
        result, report = self.run_parser(payload)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(report["summary"], "fail")
        self.assertIn("no executed final-output Q6 probe record", "\n".join(report["failures"]))

    def test_parser_accepts_one_executed_phase_and_ignores_unexecuted_branch(self):
        values = {index: 0 for index in range(96)}
        for base, candidate, role, value in [
            (68, 105, 1, -0.25),
            (80, 115, 2, 0.5),
            (92, 127, 3, 0.5),
            (104, 129, 3, 0.5),
            (116, 130, 4, 0.5),
        ]:
            values[base] = candidate
            values[base + 1] = role
            values[base + 2] = f32_bits(value)
        payload = {
            "binding_details": [
                {
                    "binding": 5,
                    "debug_probe_binding": True,
                    "u32_after_dispatch": [sample(index, values.get(index, 0)) for index in range(144)],
                    "u32_after_writeback": [sample(index, values.get(index, 0)) for index in range(144)],
                }
            ]
        }
        result, report = self.run_parser(payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(report["summary"], "pass")
        binding = report["bindings"][0]
        self.assertEqual(binding["executed_record_count"], 5)
        self.assertEqual(binding["executed_final_record_count"], 1)
        self.assertEqual([record["status"] for record in binding["records"][:5]], ["not-executed"] * 5)
        self.assertEqual([record["status"] for record in binding["records"][5:]], ["pass"] * 5)


if __name__ == "__main__":
    unittest.main()

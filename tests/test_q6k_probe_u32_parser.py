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
        for base, output_index, workgroup, local in [
            (56, 1234, [10, 20, 30], [1, 2, 3]),
            (116, 5678, [40, 50, 60], [4, 5, 6]),
        ]:
            values[base + 3] = output_index
            values[base + 4] = workgroup[0]
            values[base + 5] = workgroup[1]
            values[base + 6] = workgroup[2]
            values[base + 7] = local[0]
            values[base + 8] = local[1]
            values[base + 9] = local[2]
            values[base + 10] = 2
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
        self.assertEqual(report["schema"], "pdocker.q6k.debug-u32-probe-report.v2")
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
        self.assertEqual(records[4]["record_schema_version"], 2)
        self.assertEqual(records[4]["output_index"], 1234)
        self.assertEqual(records[4]["workgroup_id"], [10, 20, 30])
        self.assertEqual(records[4]["local_invocation_id"], [1, 2, 3])
        self.assertEqual(records[4]["trace_status"], "pass")
        self.assertEqual(records[9]["record_schema_version"], 2)
        self.assertEqual(records[9]["output_index"], 5678)
        self.assertEqual(records[9]["workgroup_id"], [40, 50, 60])
        self.assertEqual(records[9]["local_invocation_id"], [4, 5, 6])
        self.assertEqual(records[9]["trace_status"], "pass")

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
        values[116 + 3] = 2468
        values[116 + 4] = 4
        values[116 + 5] = 5
        values[116 + 6] = 6
        values[116 + 7] = 7
        values[116 + 8] = 8
        values[116 + 9] = 9
        values[116 + 10] = 2
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
        self.assertEqual(binding["records"][9]["trace_status"], "pass")
        self.assertEqual(binding["records"][9]["output_index"], 2468)
        self.assertEqual(binding["records"][9]["workgroup_id"], [4, 5, 6])
        self.assertEqual(binding["records"][9]["local_invocation_id"], [7, 8, 9])

    def test_parser_decodes_final_store_trace_v2_fields(self):
        values = {index: 0 for index in range(144)}
        base = 56
        values[base] = 64
        values[base + 1] = 4
        values[base + 2] = f32_bits(7.5)
        values[base + 3] = 151936
        values[base + 4] = 3
        values[base + 5] = 9
        values[base + 6] = 0
        values[base + 7] = 0
        values[base + 8] = 1
        values[base + 9] = 2
        values[base + 10] = 2
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
        binding = report["bindings"][0]
        self.assertEqual(binding["executed_final_trace_v2_count"], 1)
        final_records = [
            record
            for record in binding["records"]
            if record["role"] == "final_output_store" and record["status"] == "pass"
        ]
        self.assertEqual(len(final_records), 1)
        record = final_records[0]
        self.assertTrue(record["final_store_trace_v2"])
        self.assertEqual(record["record_schema_version"], 2)
        self.assertEqual(record["workgroup_id"], [3, 9, 0])
        self.assertEqual(record["local_invocation_id"], [0, 1, 2])
        self.assertEqual(record["output_index"], 151936)

    def test_parser_decodes_lane_trace_v1_fields(self):
        values = {index: 0 for index in range(704)}
        values[128] = 1
        values[129] = 32
        values[130] = 8
        values[131] = 144
        values[132] = 400
        pre_base = 144 + 3 * 8
        red_base = 400 + 3 * 8
        values[pre_base] = 3
        values[pre_base + 1] = f32_bits(1.25)
        values[pre_base + 2] = 1186
        values[pre_base + 3] = 0
        values[pre_base + 4] = 63
        values[pre_base + 5] = 105
        values[pre_base + 6] = 0
        values[pre_base + 7] = 1
        values[red_base] = 3
        values[red_base + 1] = f32_bits(2.5)
        values[red_base + 2] = 1186
        values[red_base + 3] = 0
        values[red_base + 4] = 63
        values[red_base + 5] = 115
        values[red_base + 6] = 0
        values[red_base + 7] = 1
        # Keep the fixed final record valid so the whole parser succeeds.
        values[116] = 130
        values[117] = 4
        values[118] = f32_bits(2.5)
        values[119] = 151935
        values[120] = 1186
        values[121] = 0
        values[122] = 63
        values[123] = 0
        values[124] = 0
        values[125] = 0
        values[126] = 2
        payload = {
            "binding_details": [
                {
                    "binding": 5,
                    "debug_probe_binding": True,
                    "u32_after_dispatch": [sample(index, values.get(index, 0)) for index in range(704)],
                    "u32_after_writeback": [sample(index, values.get(index, 0)) for index in range(704)],
                }
            ]
        }
        result, report = self.run_parser(payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        lane_trace = report["bindings"][0]["lane_trace_v1"]
        self.assertEqual(lane_trace["summary"], "pass")
        self.assertEqual(lane_trace["header"]["lane_count"], 32)
        self.assertEqual(lane_trace["header"]["words_per_lane"], 8)
        pre_phase = lane_trace["phases"][0]
        red_phase = lane_trace["phases"][1]
        self.assertEqual(pre_phase["records"][3]["local_x"], 3)
        self.assertAlmostEqual(pre_phase["records"][3]["value_f32"], 1.25)
        self.assertEqual(pre_phase["records"][3]["workgroup_id"], [1186, 0, 63])
        self.assertEqual(pre_phase["records"][3]["col"], 0)
        self.assertEqual(pre_phase["records"][3]["row"], 1)
        self.assertAlmostEqual(red_phase["records"][3]["writeback_value_f32"], 2.5)

    def test_parser_fails_closed_when_final_trace_metadata_is_missing(self):
        values = {index: 0 for index in range(144)}
        base = 56
        values[base] = 64
        values[base + 1] = 4
        values[base + 2] = f32_bits(7.5)
        payload = {
            "binding_details": [
                {
                    "binding": 5,
                    "debug_probe_binding": True,
                    "u32_after_dispatch": [sample(index, values.get(index, 0)) for index in range(144)],
                }
            ]
        }
        result, report = self.run_parser(payload)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(report["summary"], "fail")
        self.assertIn("final-output trace metadata", "\n".join(report["failures"]))


if __name__ == "__main__":
    unittest.main()

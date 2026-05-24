import importlib.util
import struct
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "maintenance" / "verify-q6-workgroup-lowering-preflight.py"


def load_module():
    spec = importlib.util.spec_from_file_location("q6_workgroup_preflight", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def inst(opcode, *operands):
    return [((1 + len(operands)) << 16) | opcode, *operands]


def write_spv(path, body):
    words = [
        0x07230203,
        0x00010500,
        0,
        100,
        0,
        *body,
    ]
    path.write_bytes(struct.pack("<%dI" % len(words), *words))


class Q6WorkgroupLoweringPreflightTest(unittest.TestCase):
    def test_accepts_q6_literal_local_size_with_workgroup_specid0(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "q6.spv"
            write_spv(
                path,
                [
                    *inst(16, 4, 17, 1, 1, 1),
                    *inst(71, 10, 1, 0),
                    *inst(71, 11, 1, 0),
                    *inst(71, 20, 11, 25),
                    *inst(50, 6, 10, 1),
                    *inst(50, 6, 11, 32),
                    *inst(43, 6, 12, 1),
                    *inst(43, 6, 13, 1),
                    *inst(51, 54, 20, 10, 12, 13),
                ],
            )
            report = module.analyze_q6_workgroup_lowering(path, 0, 32)
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["literal_local_sizes"], [[1, 1, 1]])
        self.assertEqual(report["workgroup_size_x_spec_id"], 0)
        self.assertIn(32, report["duplicate_spec0_defaults"])
        self.assertEqual(report["patch_policy"]["post_lowering_local_size"], [32, 1, 1])

    def test_rejects_localsizeid_general_shader(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "local-size-id.spv"
            write_spv(
                path,
                [
                    *inst(16, 4, 17, 1, 1, 1),
                    *inst(331, 4, 38, 10, 12, 13),
                    *inst(71, 10, 1, 0),
                    *inst(71, 20, 11, 25),
                    *inst(50, 6, 10, 1),
                    *inst(43, 6, 12, 1),
                    *inst(43, 6, 13, 1),
                    *inst(51, 54, 20, 10, 12, 13),
                ],
            )
            report = module.analyze_q6_workgroup_lowering(path, 0, 32)
        self.assertFalse(report["ok"])
        self.assertIn("LocalSizeId is present", " ".join(report["errors"]))

    def test_rejects_non_q6_runtime_value(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "q6.spv"
            write_spv(
                path,
                [
                    *inst(16, 4, 17, 1, 1, 1),
                    *inst(71, 10, 1, 0),
                    *inst(71, 11, 1, 0),
                    *inst(71, 20, 11, 25),
                    *inst(50, 6, 10, 1),
                    *inst(50, 6, 11, 32),
                    *inst(43, 6, 12, 1),
                    *inst(43, 6, 13, 1),
                    *inst(51, 54, 20, 10, 12, 13),
                ],
            )
            report = module.analyze_q6_workgroup_lowering(path, 0, 16)
        self.assertFalse(report["ok"])
        self.assertIn("expected 32 for Q6_K", " ".join(report["errors"]))


if __name__ == "__main__":
    unittest.main()

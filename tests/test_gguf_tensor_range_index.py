import json
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEXER = ROOT / "scripts" / "gguf-tensor-range-index.py"


def gguf_string(value: str) -> bytes:
    data = value.encode("utf-8")
    return struct.pack("<Q", len(data)) + data


def align_up(value: int, alignment: int) -> int:
    return ((value + alignment - 1) // alignment) * alignment


def synthetic_gguf() -> bytes:
    metadata = (
        gguf_string("general.alignment")
        + struct.pack("<I", 4)  # UINT32
        + struct.pack("<I", 32)
    )
    tensors = []
    offset = 0
    for name, shape, ggml_type, nbytes in [
        ("blk.0.ffn_gate_exps.3.weight", [256, 128], 12, 128 * 144),
        ("blk.0.attn_q.weight", [32, 32], 0, 32 * 32 * 4),
    ]:
        tensors.append(
            gguf_string(name)
            + struct.pack("<I", len(shape))
            + b"".join(struct.pack("<Q", dim) for dim in shape)
            + struct.pack("<I", ggml_type)
            + struct.pack("<Q", offset)
        )
        offset += nbytes
    header = (
        b"GGUF"
        + struct.pack("<I", 3)
        + struct.pack("<Q", len(tensors))
        + struct.pack("<Q", 1)
        + metadata
        + b"".join(tensors)
    )
    data_start = align_up(len(header), 32)
    payload = b"\0" * offset
    return header + (b"\0" * (data_start - len(header))) + payload


class GgufTensorRangeIndexTest(unittest.TestCase):
    def test_indexer_extracts_tensor_ranges_and_expert_hints(self):
        with tempfile.TemporaryDirectory() as td:
            model = Path(td) / "tiny.gguf"
            out = Path(td) / "index.json"
            model.write_bytes(synthetic_gguf())
            result = subprocess.run(
                [sys.executable, str(INDEXER), str(model), "--out", str(out)],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            data = json.loads(out.read_text())

        self.assertEqual(data["schema"], "pdocker.gguf-range-index.v1")
        self.assertTrue(data["diagnostic_only"])
        self.assertEqual(data["tensor_count"], 2)
        self.assertTrue(data["expert_groups_inferred"])
        expert = data["tensors"][0]
        self.assertEqual(expert["name"], "blk.0.ffn_gate_exps.3.weight")
        self.assertEqual(expert["layer"], 0)
        self.assertEqual(expert["expert_id"], 3)
        self.assertEqual(expert["expert_group"], "ffn_gate_exps")
        self.assertEqual(expert["ggml_type"], "Q4_K")
        self.assertGreaterEqual(expert["absolute_offset"], data["data_start"])
        self.assertEqual(expert["absolute_end"], expert["absolute_offset"] + expert["nbytes"])

    def test_indexer_fails_closed_for_non_gguf(self):
        with tempfile.TemporaryDirectory() as td:
            model = Path(td) / "bad.gguf"
            model.write_bytes(b"not gguf")
            result = subprocess.run(
                [sys.executable, str(INDEXER), str(model)],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=20,
            )
            self.assertNotEqual(result.returncode, 0)
            data = json.loads(result.stdout)
        self.assertEqual(data["schema"], "pdocker.gguf-range-index.v1")
        self.assertFalse(data["success"])
        self.assertIn("not a GGUF file", data["error"])


if __name__ == "__main__":
    unittest.main()

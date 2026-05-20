#!/usr/bin/env python3
"""Create a pdocker-owned GGUF tensor/range index.

The indexer is intentionally read-only: it parses the GGUF header, metadata,
and tensor table, then emits byte ranges without reading tensor payloads.  It is
the first building block for MoE out-of-core residency: pdocker can later map
page faults or GPU descriptor ranges back to tensor/expert-like ranges while
leaving llama.cpp and the model file unchanged.
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCHEMA = "pdocker.gguf-range-index.v1"

GGUF_VALUE_TYPES = {
    0: "UINT8",
    1: "INT8",
    2: "UINT16",
    3: "INT16",
    4: "UINT32",
    5: "INT32",
    6: "FLOAT32",
    7: "BOOL",
    8: "STRING",
    9: "ARRAY",
    10: "UINT64",
    11: "INT64",
    12: "FLOAT64",
}

# ggml_type -> (name, block elements, bytes per block)
GGML_TYPES: dict[int, tuple[str, int, int]] = {
    0: ("F32", 1, 4),
    1: ("F16", 1, 2),
    2: ("Q4_0", 32, 18),
    3: ("Q4_1", 32, 20),
    6: ("Q5_0", 32, 22),
    7: ("Q5_1", 32, 24),
    8: ("Q8_0", 32, 34),
    9: ("Q8_1", 32, 40),
    10: ("Q2_K", 256, 84),
    11: ("Q3_K", 256, 110),
    12: ("Q4_K", 256, 144),
    13: ("Q5_K", 256, 176),
    14: ("Q6_K", 256, 210),
    15: ("Q8_K", 256, 292),
    16: ("IQ2_XXS", 256, 66),
    17: ("IQ2_XS", 256, 74),
    18: ("IQ3_XXS", 256, 98),
    19: ("IQ1_S", 256, 50),
    20: ("IQ4_NL", 32, 18),
    21: ("IQ3_S", 256, 110),
    22: ("IQ2_S", 256, 82),
    23: ("IQ4_XS", 256, 136),
    24: ("I8", 1, 1),
    25: ("I16", 1, 2),
    26: ("I32", 1, 4),
    27: ("I64", 1, 8),
    28: ("F64", 1, 8),
    29: ("IQ1_M", 256, 56),
    30: ("BF16", 1, 2),
}

EXPERT_PATTERNS = [
    re.compile(r"(?:^|[._/])blk[._](?P<layer>\d+).*?(?:expert|exps?)[._](?P<expert>\d+)(?:[._/]|$)"),
    re.compile(r"(?:^|[._/])layers?[._](?P<layer>\d+).*?(?:expert|exps?)[._](?P<expert>\d+)(?:[._/]|$)"),
    re.compile(r"(?:^|[._/])(?:expert|exps?)[._](?P<expert>\d+)(?:[._/]|$)"),
]
LAYER_RE = re.compile(r"(?:^|[._/])(?:blk|layer|layers)[._](?P<layer>\d+)(?:[._/]|$)")


class GgufError(ValueError):
    pass


@dataclass
class Reader:
    data: bytes
    pos: int = 0

    def read(self, size: int) -> bytes:
        if size < 0 or self.pos + size > len(self.data):
            raise GgufError("unexpected EOF while reading GGUF")
        out = self.data[self.pos:self.pos + size]
        self.pos += size
        return out

    def unpack(self, fmt: str) -> tuple[Any, ...]:
        size = struct.calcsize(fmt)
        return struct.unpack(fmt, self.read(size))

    def u32(self) -> int:
        return self.unpack("<I")[0]

    def u64(self) -> int:
        return self.unpack("<Q")[0]

    def string(self) -> str:
        size = self.u64()
        if size > 256 * 1024 * 1024:
            raise GgufError(f"unreasonable GGUF string length: {size}")
        return self.read(size).decode("utf-8", "replace")


def align_up(value: int, alignment: int) -> int:
    if alignment <= 0:
        alignment = 32
    return ((value + alignment - 1) // alignment) * alignment


def parse_value(reader: Reader, value_type: int) -> Any:
    if value_type == 0:
        return reader.unpack("<B")[0]
    if value_type == 1:
        return reader.unpack("<b")[0]
    if value_type == 2:
        return reader.unpack("<H")[0]
    if value_type == 3:
        return reader.unpack("<h")[0]
    if value_type == 4:
        return reader.u32()
    if value_type == 5:
        return reader.unpack("<i")[0]
    if value_type == 6:
        return reader.unpack("<f")[0]
    if value_type == 7:
        return bool(reader.unpack("<?")[0])
    if value_type == 8:
        return reader.string()
    if value_type == 9:
        item_type = reader.u32()
        count = reader.u64()
        if count > 1_000_000:
            raise GgufError(f"unreasonable GGUF array length: {count}")
        return {
            "type": GGUF_VALUE_TYPES.get(item_type, f"UNKNOWN_{item_type}"),
            "values": [parse_value(reader, item_type) for _ in range(count)],
        }
    if value_type == 10:
        return reader.u64()
    if value_type == 11:
        return reader.unpack("<q")[0]
    if value_type == 12:
        return reader.unpack("<d")[0]
    raise GgufError(f"unsupported GGUF metadata value type: {value_type}")


def tensor_nbytes(shape: list[int], ggml_type: int) -> int:
    info = GGML_TYPES.get(ggml_type)
    if not info:
        return 0
    _, block, block_bytes = info
    elements = 1
    for dim in shape:
        elements *= max(1, int(dim))
    blocks = (elements + block - 1) // block
    return blocks * block_bytes


def infer_expert(name: str) -> dict[str, Any]:
    layer = None
    expert = None
    for pattern in EXPERT_PATTERNS:
        match = pattern.search(name)
        if match:
            if match.groupdict().get("layer") is not None:
                layer = int(match.group("layer"))
            expert = int(match.group("expert"))
            break
    if layer is None:
        match = LAYER_RE.search(name)
        if match:
            layer = int(match.group("layer"))
    group = None
    for token in ("ffn_gate_exps", "ffn_up_exps", "ffn_down_exps", "experts", "expert", "router", "gate"):
        if token in name:
            group = token
            break
    return {
        "layer": layer,
        "expert_id": expert,
        "expert_group": group,
        "expert_like": expert is not None or (group is not None and "expert" in group),
    }


def build_index(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    r = Reader(data)
    magic = r.read(4)
    if magic != b"GGUF":
        raise GgufError("not a GGUF file")
    version = r.u32()
    if version not in (2, 3):
        raise GgufError(f"unsupported GGUF version: {version}")
    tensor_count = r.u64()
    metadata_count = r.u64()
    if tensor_count > 2_000_000 or metadata_count > 1_000_000:
        raise GgufError("unreasonable GGUF table counts")

    metadata: dict[str, Any] = {}
    for _ in range(metadata_count):
        key = r.string()
        value_type = r.u32()
        metadata[key] = parse_value(r, value_type)

    tensors = []
    for _ in range(tensor_count):
        name = r.string()
        n_dims = r.u32()
        if n_dims > 16:
            raise GgufError(f"unreasonable tensor dimension count for {name}: {n_dims}")
        shape = [r.u64() for _ in range(n_dims)]
        ggml_type = r.u32()
        offset = r.u64()
        nbytes = tensor_nbytes(shape, ggml_type)
        type_name = GGML_TYPES.get(ggml_type, (f"UNKNOWN_{ggml_type}", 1, 0))[0]
        expert = infer_expert(name)
        tensors.append({
            "name": name,
            "offset": offset,
            "nbytes": nbytes,
            "ggml_type": type_name,
            "ggml_type_id": ggml_type,
            "shape": shape,
            **expert,
        })

    alignment = int(metadata.get("general.alignment") or 32)
    data_start = align_up(r.pos, alignment)
    for tensor in tensors:
        tensor["absolute_offset"] = data_start + int(tensor["offset"])
        tensor["absolute_end"] = tensor["absolute_offset"] + int(tensor["nbytes"])

    expert_count = sum(1 for tensor in tensors if tensor["expert_like"])
    return {
        "schema": SCHEMA,
        "diagnostic_only": True,
        "model_path": str(path),
        "model_size": len(data),
        "gguf_version": version,
        "alignment": alignment,
        "tensor_count": tensor_count,
        "metadata_count": metadata_count,
        "data_start": data_start,
        "expert_like_tensor_count": expert_count,
        "expert_groups_inferred": expert_count > 0,
        "metadata_keys": sorted(metadata.keys())[:256],
        "tensors": tensors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    try:
        index = build_index(args.model)
    except Exception as exc:
        error = {
            "schema": SCHEMA,
            "diagnostic_only": True,
            "model_path": str(args.model),
            "error": str(exc),
            "success": False,
        }
        text = json.dumps(error, indent=2, sort_keys=True) + "\n"
        if args.out:
            args.out.write_text(text, encoding="utf-8")
        else:
            sys.stdout.write(text)
        return 1
    text = json.dumps(index, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Preflight-check the narrow Q6_K WorkgroupSize compatibility lowering.

This script is intentionally small and dependency-free.  It does not try to be
a general SPIR-V validator.  It checks only the structural contract needed
before running the Android device lane that explicitly lowers llama.cpp Q6_K's
literal `OpExecutionMode LocalSize 1 1 1` to the already-requested
`BuiltIn WorkgroupSize.x == 32` specialization.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path


SPIRV_MAGIC = 0x07230203
OP_EXECUTION_MODE = 16
OP_DECORATE = 71
OP_SPEC_CONSTANT = 50
OP_CONSTANT = 43
OP_CONSTANT_COMPOSITE = 44
OP_SPEC_CONSTANT_COMPOSITE = 51
OP_SPEC_CONSTANT_OP = 52
OP_EXECUTION_MODE_ID = 331

EXECUTION_MODE_LOCAL_SIZE = 17
EXECUTION_MODE_LOCAL_SIZE_ID = 38
DECORATION_SPEC_ID = 1
DECORATION_BUILTIN = 11
BUILTIN_WORKGROUP_SIZE = 25
SPEC_OP_COMPOSITE_CONSTRUCT = 80


def _decode_words(path: Path) -> list[int]:
    data = path.read_bytes()
    if len(data) < 20 or len(data) % 4:
        raise ValueError("not a word-aligned SPIR-V module")
    words = list(struct.unpack("<%dI" % (len(data) // 4), data))
    if words[0] != SPIRV_MAGIC:
        raise ValueError("invalid SPIR-V magic")
    return words


def _iter_instructions(words: list[int]):
    i = 5
    while i < len(words):
        inst = words[i]
        word_count = inst >> 16
        opcode = inst & 0xFFFF
        if word_count == 0 or i + word_count > len(words):
            raise ValueError(f"truncated instruction at word {i}")
        yield i, opcode, words[i : i + word_count]
        i += word_count


def analyze_q6_workgroup_lowering(path: Path, expect_spec_id: int, expect_value: int) -> dict:
    words = _decode_words(path)
    literal_local_sizes: list[list[int]] = []
    local_size_id_modes: list[list[int]] = []
    spec_ids: dict[int, int] = {}
    scalar_defaults: dict[int, int] = {}
    workgroup_size_object = None
    composites: dict[int, list[int]] = {}

    for _, opcode, inst in _iter_instructions(words):
        if opcode == OP_EXECUTION_MODE and len(inst) >= 6 and inst[2] == EXECUTION_MODE_LOCAL_SIZE:
            literal_local_sizes.append(inst[3:6])
        elif opcode == OP_EXECUTION_MODE_ID and len(inst) >= 6 and inst[2] == EXECUTION_MODE_LOCAL_SIZE_ID:
            local_size_id_modes.append(inst[3:6])
        elif opcode == OP_DECORATE and len(inst) >= 4 and inst[2] == DECORATION_SPEC_ID:
            spec_ids[inst[1]] = inst[3]
        elif opcode == OP_DECORATE and len(inst) >= 4 and inst[2] == DECORATION_BUILTIN and inst[3] == BUILTIN_WORKGROUP_SIZE:
            workgroup_size_object = inst[1]
        elif opcode in (OP_CONSTANT, OP_SPEC_CONSTANT) and len(inst) >= 4:
            # Only u32 constants are needed for this preflight.
            scalar_defaults[inst[2]] = inst[3]
        elif opcode in (OP_CONSTANT_COMPOSITE, OP_SPEC_CONSTANT_COMPOSITE) and len(inst) >= 6:
            composites[inst[2]] = inst[3:6]
        elif opcode == OP_SPEC_CONSTANT_OP and len(inst) >= 7 and inst[3] == SPEC_OP_COMPOSITE_CONSTRUCT:
            composites[inst[2]] = inst[4:7]

    errors: list[str] = []
    if len(literal_local_sizes) != 1:
        errors.append(f"expected exactly one literal LocalSize, found {len(literal_local_sizes)}")
    elif literal_local_sizes[0] != [1, 1, 1]:
        errors.append(f"literal LocalSize is {literal_local_sizes[0]}, expected [1, 1, 1]")
    if local_size_id_modes:
        errors.append("LocalSizeId is present; this lowering must not run")
    if workgroup_size_object is None:
        errors.append("BuiltIn WorkgroupSize object is missing")

    wg_components = composites.get(workgroup_size_object or -1)
    if wg_components is None:
        errors.append("BuiltIn WorkgroupSize composite is missing")
        wg_components = [None, None, None]

    x_spec_id = spec_ids.get(wg_components[0]) if wg_components[0] is not None else None
    x_default = scalar_defaults.get(wg_components[0]) if wg_components[0] is not None else None
    y_default = scalar_defaults.get(wg_components[1]) if wg_components[1] is not None else None
    z_default = scalar_defaults.get(wg_components[2]) if wg_components[2] is not None else None

    if x_spec_id != expect_spec_id:
        errors.append(f"WorkgroupSize.x SpecId is {x_spec_id}, expected {expect_spec_id}")
    if x_default != 1:
        errors.append(f"WorkgroupSize.x default is {x_default}, expected 1")
    if y_default != 1 or z_default != 1:
        errors.append(f"WorkgroupSize.y/z defaults are {y_default}/{z_default}, expected 1/1")
    if expect_value != 32:
        errors.append(f"runtime specialization value is {expect_value}, expected 32 for Q6_K")

    # The native Q6_K module also has another SpecId 0 defaulting to 32 for the
    # workgroup/reduction array width.  This duplicate SpecId is why the
    # lowering is constrained to the Q6_K 32-lane lane, not a general rule.
    duplicate_spec0_defaults = [
        scalar_defaults[result_id]
        for result_id, spec_id in spec_ids.items()
        if spec_id == expect_spec_id
    ]
    has_q6_width_spec = 32 in duplicate_spec0_defaults
    if not has_q6_width_spec:
        errors.append("no duplicate SpecId 0 scalar default 32 found for Q6_K width evidence")

    return {
        "schema": "pdocker.q6-workgroup-lowering-preflight.v1",
        "path": str(path),
        "ok": not errors,
        "errors": errors,
        "literal_local_sizes": literal_local_sizes,
        "local_size_id_modes": local_size_id_modes,
        "workgroup_size_object": workgroup_size_object,
        "workgroup_size_components": wg_components,
        "workgroup_size_x_spec_id": x_spec_id,
        "workgroup_size_x_default": x_default,
        "workgroup_size_y_default": y_default,
        "workgroup_size_z_default": z_default,
        "expected_runtime_specialization": {
            "constant_id": expect_spec_id,
            "value": expect_value,
        },
        "duplicate_spec0_defaults": duplicate_spec0_defaults,
        "patch_policy": {
            "allowed_change": "OpExecutionMode LocalSize operands only",
            "post_lowering_local_size": [expect_value, 1, 1],
            "descriptor_push_specialization_data_rewrite": False,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("spv", type=Path)
    parser.add_argument("--expect-spec-id", type=int, default=0)
    parser.add_argument("--expect-value", type=int, default=32)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args(argv)

    try:
        report = analyze_q6_workgroup_lowering(args.spv, args.expect_spec_id, args.expect_value)
    except Exception as exc:  # pragma: no cover - CLI error path
        report = {
            "schema": "pdocker.q6-workgroup-lowering-preflight.v1",
            "path": str(args.spv),
            "ok": False,
            "errors": [str(exc)],
        }
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.json_out:
        args.json_out.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0 if report.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())

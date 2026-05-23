#!/usr/bin/env python3
"""Analyze dumped Vulkan SPIR-V modules without hash-targeted assumptions.

This is intentionally a structural tool: it reads the SPIR-V module that the
application passed through Vulkan, emits a JSON summary, and optionally writes a
`spirv-dis` assembly listing when the tool is installed.  The report is meant to
support correctness triage and later performance tuning without baking in a
single llama.cpp shader hash.
"""

from __future__ import annotations

import argparse
import json
import shutil
import struct
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable


SPIRV_MAGIC = 0x07230203

OP_NAMES = {
    15: "OpEntryPoint",
    16: "OpExecutionMode",
    17: "OpCapability",
    19: "OpTypeVoid",
    21: "OpTypeInt",
    22: "OpTypeFloat",
    23: "OpTypeVector",
    25: "OpTypeMatrix",
    27: "OpTypeArray",
    28: "OpTypeRuntimeArray",
    29: "OpTypeStruct",
    30: "OpTypeOpaque",
    32: "OpTypePointer",
    43: "OpConstant",
    44: "OpConstantComposite",
    45: "OpSpecConstantTrue",
    46: "OpSpecConstantFalse",
    47: "OpSpecConstant",
    51: "OpSpecConstantComposite",
    52: "OpSpecConstantOp",
    54: "OpFunction",
    56: "OpFunctionEnd",
    59: "OpVariable",
    61: "OpLoad",
    62: "OpStore",
    65: "OpAccessChain",
    66: "OpInBoundsAccessChain",
    71: "OpDecorate",
    72: "OpMemberDecorate",
    80: "OpCopyMemory",
    81: "OpCopyMemorySized",
    124: "OpIAdd",
    125: "OpFAdd",
    126: "OpISub",
    127: "OpFSub",
    128: "OpIMul",
    129: "OpFMul",
    132: "OpUDiv",
    133: "OpSDiv",
    136: "OpFDiv",
    139: "OpUMod",
    140: "OpSRem",
    141: "OpSMod",
    142: "OpFRem",
    143: "OpFMod",
    145: "OpVectorTimesScalar",
    146: "OpMatrixTimesScalar",
    147: "OpVectorTimesMatrix",
    148: "OpMatrixTimesVector",
    149: "OpMatrixTimesMatrix",
    150: "OpOuterProduct",
    151: "OpDot",
    154: "OpShiftRightLogical",
    155: "OpShiftRightArithmetic",
    156: "OpShiftLeftLogical",
    157: "OpBitwiseOr",
    158: "OpBitwiseXor",
    159: "OpBitwiseAnd",
    160: "OpNot",
    164: "OpLogicalEqual",
    170: "OpIEqual",
    171: "OpINotEqual",
    172: "OpUGreaterThan",
    173: "OpSGreaterThan",
    174: "OpUGreaterThanEqual",
    175: "OpSGreaterThanEqual",
    176: "OpULessThan",
    177: "OpSLessThan",
    178: "OpULessThanEqual",
    179: "OpSLessThanEqual",
    180: "OpFOrdEqual",
    184: "OpFOrdLessThan",
    190: "OpDPdx",
    224: "OpControlBarrier",
    225: "OpMemoryBarrier",
    245: "OpLoopMerge",
    246: "OpSelectionMerge",
    247: "OpLabel",
    248: "OpBranch",
    249: "OpBranchConditional",
    250: "OpSwitch",
    253: "OpReturn",
    254: "OpReturnValue",
    255: "OpUnreachable",
    331: "OpExecutionModeId",
}

CAPABILITY_NAMES = {
    1: "Shader",
    9: "Float16",
    10: "Float64",
    11: "Int64",
    22: "Int16",
    39: "Int8",
    61: "GroupNonUniform",
    63: "GroupNonUniformArithmetic",
    4433: "StorageBuffer16BitAccess",
    4434: "UniformAndStorageBuffer16BitAccess",
    4435: "StoragePushConstant16",
    4448: "StorageBuffer8BitAccess",
    4449: "UniformAndStorageBuffer8BitAccess",
    4450: "StoragePushConstant8",
}

DECORATION_NAMES = {
    1: "SpecId",
    2: "Block",
    24: "NonWritable",
    25: "NonReadable",
    33: "Binding",
    34: "DescriptorSet",
}

STORAGE_CLASS_NAMES = {
    0: "UniformConstant",
    1: "Input",
    2: "Uniform",
    3: "Output",
    4: "Workgroup",
    5: "CrossWorkgroup",
    7: "Function",
    12: "StorageBuffer",
    13: "PhysicalStorageBuffer",
}


def fnv1a64(data: bytes) -> int:
    value = 1469598103934665603
    for byte in data:
        value ^= byte
        value = (value * 1099511628211) & ((1 << 64) - 1)
    return value


def iter_instructions(words: list[int]) -> Iterable[tuple[int, int, list[int]]]:
    index = 5
    while index < len(words):
        first = words[index]
        word_count = first >> 16
        opcode = first & 0xFFFF
        if word_count == 0 or index + word_count > len(words):
            raise ValueError(f"truncated SPIR-V instruction at word {index}")
        yield index, opcode, words[index : index + word_count]
        index += word_count


def analyze_spirv(path: Path) -> dict:
    data = path.read_bytes()
    if len(data) < 20 or len(data) % 4:
        raise ValueError(f"{path}: SPIR-V size must be a 4-byte aligned module")
    words = list(struct.unpack("<%dI" % (len(data) // 4), data))
    if words[0] != SPIRV_MAGIC:
        raise ValueError(f"{path}: bad SPIR-V magic 0x{words[0]:08x}")

    op_hist = Counter()
    capabilities: list[int] = []
    decorations: dict[int, dict[str, int | bool]] = defaultdict(dict)
    member_decorations: list[dict] = []
    type_pointer: dict[int, dict] = {}
    variable: dict[int, dict] = {}
    type_scalar: dict[int, dict] = {}
    access_chain_count = 0
    workgroup_variable_count = 0
    storage_variables = []
    local_size = [0, 0, 0]
    local_size_id = [0, 0, 0]
    loads = stores = barriers = arithmetic = control = 0

    for _index, opcode, inst in iter_instructions(words):
        op_hist[opcode] += 1
        if opcode in (61,):
            loads += 1
        elif opcode in (62,):
            stores += 1
        elif opcode in (65, 66):
            access_chain_count += 1
        elif opcode in (224, 225):
            barriers += 1
        elif 124 <= opcode <= 190:
            arithmetic += 1
        elif 245 <= opcode <= 255:
            control += 1

        if opcode == 17 and len(inst) >= 2:
            capabilities.append(inst[1])
        elif opcode == 16 and len(inst) >= 6 and inst[2] == 17:
            local_size = [inst[3], inst[4], inst[5]]
        elif opcode == 331 and len(inst) >= 6 and inst[2] == 38:
            local_size_id = [inst[3], inst[4], inst[5]]
        elif opcode == 21 and len(inst) >= 4:
            type_scalar[inst[2]] = {"kind": "int", "bits": inst[3], "signed": inst[4] if len(inst) > 4 else None}
        elif opcode == 22 and len(inst) >= 4:
            type_scalar[inst[2]] = {"kind": "float", "bits": inst[3]}
        elif opcode == 32 and len(inst) >= 4:
            type_pointer[inst[1]] = {
                "storage_class": inst[2],
                "storage_class_name": STORAGE_CLASS_NAMES.get(inst[2], str(inst[2])),
                "pointee_type": inst[3],
            }
        elif opcode == 59 and len(inst) >= 4:
            result_type, result_id, storage_class = inst[1], inst[2], inst[3]
            variable[result_id] = {
                "result_type": result_type,
                "storage_class": storage_class,
                "storage_class_name": STORAGE_CLASS_NAMES.get(storage_class, str(storage_class)),
            }
            if storage_class == 4:
                workgroup_variable_count += 1
            if storage_class in (2, 12):
                storage_variables.append(result_id)
        elif opcode == 71 and len(inst) >= 3:
            target, decoration = inst[1], inst[2]
            name = DECORATION_NAMES.get(decoration, str(decoration))
            if decoration in (1, 33, 34) and len(inst) >= 4:
                decorations[target][name] = inst[3]
            else:
                decorations[target][name] = True
        elif opcode == 72 and len(inst) >= 4:
            member_decorations.append(
                {
                    "target": inst[1],
                    "member": inst[2],
                    "decoration": DECORATION_NAMES.get(inst[3], str(inst[3])),
                    "operands": inst[4:],
                }
            )

    descriptor_variables = []
    bindings_seen: dict[tuple[int, int], list[int]] = defaultdict(list)
    for var_id, var in variable.items():
        dec = decorations.get(var_id, {})
        if "Binding" not in dec:
            continue
        descriptor_set = int(dec.get("DescriptorSet", 0))
        binding = int(dec["Binding"])
        bindings_seen[(descriptor_set, binding)].append(var_id)
        descriptor_variables.append(
            {
                "id": var_id,
                "set": descriptor_set,
                "binding": binding,
                "storage_class": var["storage_class_name"],
                "non_readable": bool(dec.get("NonReadable", False)),
                "non_writable": bool(dec.get("NonWritable", False)),
            }
        )

    duplicate_bindings = [
        {"set": set_id, "binding": binding, "variable_ids": ids}
        for (set_id, binding), ids in sorted(bindings_seen.items())
        if len(ids) > 1
    ]

    op_hist_named = {
        OP_NAMES.get(opcode, f"Op{opcode}"): count
        for opcode, count in sorted(op_hist.items(), key=lambda kv: (-kv[1], kv[0]))
    }
    capability_names = [CAPABILITY_NAMES.get(cap, str(cap)) for cap in capabilities]

    risk_notes = []
    if any(cap in capabilities for cap in (4448, 4449, 4450)):
        risk_notes.append("uses 8-bit storage; verify Android driver feature enablement and byte-address interpretation")
    if any(cap in capabilities for cap in (4433, 4434, 4435)):
        risk_notes.append("uses 16-bit storage; verify storage16 feature chain and alignment")
    if 63 in capabilities:
        risk_notes.append("uses subgroup arithmetic; verify subgroup operation support and local-size assumptions")
    if workgroup_variable_count or barriers:
        risk_notes.append("uses workgroup/shared-memory synchronization; correctness can depend on workgroup size specialization")
    if duplicate_bindings:
        risk_notes.append("multiple variables share descriptor set/binding; bridge must preserve API descriptor view exactly")
    if local_size_id != [0, 0, 0]:
        risk_notes.append("uses specialization-controlled workgroup size; cache keys and validation must include specialization data")

    return {
        "schema": "pdocker.spirv.analysis.v1",
        "path": str(path),
        "hash": f"0x{fnv1a64(data):016x}",
        "bytes": len(data),
        "words": len(words),
        "version": f"0x{words[1]:08x}",
        "generator": words[2],
        "bound": words[3],
        "instruction_count": sum(op_hist.values()),
        "op_class_counts": {
            "load": loads,
            "store": stores,
            "access_chain": access_chain_count,
            "arithmetic": arithmetic,
            "control": control,
            "barrier": barriers,
        },
        "local_size": local_size,
        "local_size_id": local_size_id,
        "capabilities": capability_names,
        "descriptor_variables": descriptor_variables,
        "duplicate_bindings": duplicate_bindings,
        "workgroup_variable_count": workgroup_variable_count,
        "op_histogram": op_hist_named,
        "risk_notes": risk_notes,
    }


def maybe_disassemble(path: Path, output_dir: Path | None) -> str | None:
    tool = shutil.which("spirv-dis")
    if not tool or output_dir is None:
        return None
    output_dir.mkdir(parents=True, exist_ok=True)
    asm_path = output_dir / (path.name + ".spvasm")
    subprocess.run([tool, str(path), "-o", str(asm_path)], check=True)
    return str(asm_path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spirv", nargs="+", type=Path, help="SPIR-V .spv file(s)")
    parser.add_argument("--json-out", type=Path, help="write combined JSON report")
    parser.add_argument("--disassemble-dir", type=Path, help="write spirv-dis output into this directory")
    args = parser.parse_args()

    reports = []
    for path in args.spirv:
        report = analyze_spirv(path)
        asm = maybe_disassemble(path, args.disassemble_dir)
        if asm:
            report["disassembly_path"] = asm
        reports.append(report)

    payload = {"schema": "pdocker.spirv.analysis.bundle.v1", "modules": reports}
    text = json.dumps(payload, indent=2, sort_keys=True)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(text + "\n")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

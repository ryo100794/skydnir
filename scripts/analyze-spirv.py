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
    5: "OpName",
    6: "OpMemberName",
    11: "OpExtInstImport",
    12: "OpExtInst",
    15: "OpEntryPoint",
    16: "OpExecutionMode",
    17: "OpCapability",
    19: "OpTypeVoid",
    21: "OpTypeInt",
    22: "OpTypeFloat",
    23: "OpTypeVector",
    25: "OpTypeMatrix",
    28: "OpTypeArray",
    29: "OpTypeRuntimeArray",
    30: "OpTypeStruct",
    31: "OpTypeOpaque",
    32: "OpTypePointer",
    43: "OpConstant",
    44: "OpConstantComposite",
    45: "OpSpecConstantTrue",
    46: "OpSpecConstantFalse",
    50: "OpSpecConstant",
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
    63: "OpCopyMemory",
    64: "OpCopyMemorySized",
    80: "OpCompositeConstruct",
    81: "OpCompositeExtract",
    82: "OpCompositeInsert",
    83: "OpVectorShuffle",
    84: "OpVectorExtractDynamic",
    86: "OpVectorTimesScalar",
    128: "OpIAdd",
    129: "OpFAdd",
    130: "OpISub",
    131: "OpFSub",
    132: "OpIMul",
    133: "OpFMul",
    134: "OpUDiv",
    135: "OpSDiv",
    136: "OpFDiv",
    137: "OpUMod",
    138: "OpSRem",
    139: "OpSMod",
    140: "OpFRem",
    141: "OpFMod",
    142: "OpVectorTimesScalar",
    143: "OpMatrixTimesScalar",
    144: "OpVectorTimesMatrix",
    145: "OpMatrixTimesVector",
    146: "OpMatrixTimesMatrix",
    147: "OpOuterProduct",
    148: "OpDot",
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
    245: "OpPhi",
    246: "OpLoopMerge",
    247: "OpSelectionMerge",
    248: "OpLabel",
    249: "OpBranch",
    250: "OpBranchConditional",
    251: "OpSwitch",
    252: "OpKill",
    253: "OpReturn",
    254: "OpReturnValue",
    255: "OpUnreachable",
    331: "OpExecutionModeId",
    350: "OpGroupNonUniformFAdd",
}

GLSL_STD_450_NAMES = {
    50: "Fma",
}

GROUP_OPERATION_NAMES = {
    0: "Reduce",
    1: "InclusiveScan",
    2: "ExclusiveScan",
    3: "ClusteredReduce",
}

TERMINATOR_OPS = {249, 250, 251, 252, 253, 254, 255}

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
    3: "BufferBlock",
    4: "RowMajor",
    5: "ColMajor",
    6: "ArrayStride",
    7: "MatrixStride",
    11: "BuiltIn",
    24: "NonWritable",
    25: "NonReadable",
    33: "Binding",
    34: "DescriptorSet",
    35: "Offset",
}

BUILTIN_NAMES = {
    24: "NumWorkgroups",
    25: "WorkgroupSize",
    26: "WorkgroupId",
    27: "LocalInvocationId",
    28: "GlobalInvocationId",
    29: "LocalInvocationIndex",
}

STORAGE_CLASS_NAMES = {
    0: "UniformConstant",
    1: "Input",
    2: "Uniform",
    3: "Output",
    4: "Workgroup",
    5: "CrossWorkgroup",
    7: "Function",
    9: "PushConstant",
    12: "StorageBuffer",
    13: "PhysicalStorageBuffer",
}

EXECUTION_MODEL_NAMES = {
    0: "Vertex",
    4: "Fragment",
    5: "GLCompute",
}

SCOPE_NAMES = {
    0: "CrossDevice",
    1: "Device",
    2: "Workgroup",
    3: "Subgroup",
    4: "Invocation",
    5: "QueueFamily",
}

MEMORY_SEMANTICS_FLAGS = [
    (0x0002, "Acquire"),
    (0x0004, "Release"),
    (0x0008, "AcquireRelease"),
    (0x0010, "SequentiallyConsistent"),
    (0x0040, "UniformMemory"),
    (0x0080, "SubgroupMemory"),
    (0x0100, "WorkgroupMemory"),
    (0x0200, "CrossWorkgroupMemory"),
    (0x0400, "AtomicCounterMemory"),
    (0x0800, "ImageMemory"),
    (0x1000, "OutputMemory"),
    (0x2000, "MakeAvailable"),
    (0x4000, "MakeVisible"),
    (0x10000, "Volatile"),
]


def decode_scope_name(value: int | None) -> str | None:
    if value is None:
        return None
    return SCOPE_NAMES.get(value, str(value))


def decode_memory_semantics_names(value: int | None) -> list[str]:
    if value is None:
        return []
    if value == 0:
        return ["None"]
    return [name for bit, name in MEMORY_SEMANTICS_FLAGS if value & bit]


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


def decode_spirv_string(words: list[int], start: int) -> str:
    data = bytearray()
    for word in words[start:]:
        for shift in (0, 8, 16, 24):
            byte = (word >> shift) & 0xFF
            if byte == 0:
                return data.decode("utf-8", errors="replace")
            data.append(byte)
    return data.decode("utf-8", errors="replace")


def branch_targets(opcode: int, inst: list[int]) -> list[int]:
    if opcode == 249 and len(inst) >= 2:
        return [inst[1]]
    if opcode == 250 and len(inst) >= 4:
        return [inst[2], inst[3]]
    if opcode == 251 and len(inst) >= 3:
        targets = [inst[2]]
        for i in range(4, len(inst), 2):
            targets.append(inst[i])
        return targets
    return []


def summarize_cfg(words: list[int]) -> dict:
    functions = []
    current_function: dict | None = None
    current_block: dict | None = None
    block_by_label: dict[int, dict] = {}

    for word_index, opcode, inst in iter_instructions(words):
        op_name = OP_NAMES.get(opcode, f"Op{opcode}")
        if opcode == 54 and len(inst) >= 5:
            current_function = {
                "id": inst[2],
                "result_type": inst[1],
                "function_control": inst[3],
                "function_type": inst[4],
                "word_index": word_index,
                "blocks": [],
            }
            current_block = None
            functions.append(current_function)
            continue
        if opcode == 56:
            current_function = None
            current_block = None
            continue
        if current_function is None:
            continue
        if opcode == 248 and len(inst) >= 2:
            current_block = {
                "label": inst[1],
                "word_index": word_index,
                "instruction_count": 0,
                "op_histogram": Counter(),
                "instruction_word_indices": [],
                "load_count": 0,
                "store_count": 0,
                "access_chain_count": 0,
                "arithmetic_count": 0,
                "barrier_count": 0,
                "phi_count": 0,
                "loop_merge": None,
                "selection_merge": None,
                "first_non_phi_word_index": None,
                "pre_merge_word_index": None,
                "terminator": None,
                "terminator_instruction": None,
                "successors": [],
                "predecessors": [],
                "store_candidates": [],
            }
            current_function["blocks"].append(current_block)
            block_by_label[inst[1]] = current_block
            continue
        if current_block is None:
            continue
        current_block["instruction_count"] += 1
        current_block["instruction_word_indices"].append(word_index)
        current_block["op_histogram"][op_name] += 1
        if opcode != 245 and current_block["first_non_phi_word_index"] is None:
            current_block["first_non_phi_word_index"] = word_index
        if opcode == 61:
            current_block["load_count"] += 1
        elif opcode == 62:
            current_block["store_count"] += 1
            current_block["store_candidates"].append(
                {
                    "word_index": word_index,
                    "pointer_id": inst[1] if len(inst) > 1 else None,
                    "object_id": inst[2] if len(inst) > 2 else None,
                }
            )
        elif opcode in (63, 64):
            current_block["store_count"] += 1
            current_block["store_candidates"].append(
                {
                    "word_index": word_index,
                    "pointer_id": inst[1] if len(inst) > 1 else None,
                    "object_id": inst[2] if len(inst) > 2 else None,
                    "kind": op_name,
                }
            )
        elif opcode in (65, 66):
            current_block["access_chain_count"] += 1
        elif opcode == 245:
            current_block["phi_count"] += 1
        elif 124 <= opcode <= 190:
            current_block["arithmetic_count"] += 1
        elif opcode in (224, 225):
            current_block["barrier_count"] += 1
        elif opcode == 246:
            if current_block["pre_merge_word_index"] is None:
                current_block["pre_merge_word_index"] = word_index
            current_block["loop_merge"] = {
                "merge_block": inst[1] if len(inst) > 1 else None,
                "continue_target": inst[2] if len(inst) > 2 else None,
                "control": inst[3] if len(inst) > 3 else None,
            }
        elif opcode == 247:
            if current_block["pre_merge_word_index"] is None:
                current_block["pre_merge_word_index"] = word_index
            current_block["selection_merge"] = {
                "merge_block": inst[1] if len(inst) > 1 else None,
                "control": inst[2] if len(inst) > 2 else None,
            }
        if opcode in TERMINATOR_OPS:
            if current_block["pre_merge_word_index"] is None:
                current_block["pre_merge_word_index"] = word_index
            current_block["terminator"] = op_name
            current_block["successors"] = branch_targets(opcode, inst)
            current_block["terminator_instruction"] = {
                "op": op_name,
                "word_index": word_index,
            }
            if opcode == 249 and len(inst) >= 2:
                current_block["terminator_instruction"]["target"] = inst[1]
            elif opcode == 250 and len(inst) >= 4:
                current_block["terminator_instruction"].update(
                    {
                        "condition_id": inst[1],
                        "true_label": inst[2],
                        "false_label": inst[3],
                    }
                )
            elif opcode == 251 and len(inst) >= 3:
                current_block["terminator_instruction"].update(
                    {
                        "selector_id": inst[1],
                        "default_label": inst[2],
                        "case_labels": inst[4::2],
                    }
                )

    pred_map: dict[int, list[dict]] = defaultdict(list)
    for function in functions:
        for ordinal, block in enumerate(function["blocks"]):
            for successor in block.get("successors", []):
                pred_map[int(successor)].append(
                    {
                        "function_id": function["id"],
                        "block_label": block["label"],
                        "block_ordinal": ordinal,
                        "terminator": block["terminator"],
                        "terminator_instruction": block["terminator_instruction"],
                    }
                )
    for function in functions:
        for block in function["blocks"]:
            block["predecessors"] = pred_map.get(int(block["label"]), [])

    probe_candidates = []
    for function in functions:
        for ordinal, block in enumerate(function["blocks"]):
            probe_candidates.append(
                {
                    "candidate_id": len(probe_candidates),
                    "function_id": function["id"],
                    "block_label": block["label"],
                    "block_ordinal": ordinal,
                    "word_index": block["word_index"],
                    "block_entry_insert_after_phi_word_index": block["first_non_phi_word_index"],
                    "block_exit_insert_before_word_index": block["pre_merge_word_index"],
                    "reason": "store" if block["store_count"] else "arithmetic" if block["arithmetic_count"] else "control",
                    "store_count": block["store_count"],
                    "arithmetic_count": block["arithmetic_count"],
                    "barrier_count": block["barrier_count"],
                }
            )

    bisect_rounds = []
    pending_ranges = [(0, len(probe_candidates))]
    while pending_ranges:
        next_ranges = []
        round_groups = []
        for start, end in pending_ranges:
            if end - start <= 1:
                round_groups.append(
                    {
                        "range": [start, end],
                        "candidate_count": end - start,
                        "leaf": True,
                        "candidate_indices": list(range(start, end)),
                    }
                )
                continue
            mid = start + (end - start) // 2
            left = {"range": [start, mid], "candidate_count": mid - start, "candidate_indices": list(range(start, mid))}
            right = {"range": [mid, end], "candidate_count": end - mid, "candidate_indices": list(range(mid, end))}
            round_groups.extend([left, right])
            next_ranges.extend([(start, mid), (mid, end)])
        if round_groups:
            bisect_rounds.append(round_groups)
        if all(end - start <= 1 for start, end in next_ranges):
            if next_ranges:
                bisect_rounds.append([
                    {
                        "range": [start, end],
                        "candidate_count": end - start,
                        "leaf": True,
                        "candidate_indices": list(range(start, end)),
                    }
                    for start, end in next_ranges
                ])
            break
        pending_ranges = next_ranges

    def json_block(block: dict) -> dict:
        return {
            "label": block["label"],
            "word_index": block["word_index"],
            "instruction_word_indices": block["instruction_word_indices"],
            "instruction_count": block["instruction_count"],
            "op_histogram": dict(block["op_histogram"].most_common()),
            "load_count": block["load_count"],
            "store_count": block["store_count"],
            "access_chain_count": block["access_chain_count"],
            "arithmetic_count": block["arithmetic_count"],
            "barrier_count": block["barrier_count"],
            "phi_count": block["phi_count"],
            "loop_merge": block["loop_merge"],
            "selection_merge": block["selection_merge"],
            "block_entry_insert_after_phi_word_index": block["first_non_phi_word_index"],
            "block_exit_insert_before_word_index": block["pre_merge_word_index"],
            "terminator": block["terminator"],
            "terminator_instruction": block["terminator_instruction"],
            "successors": block["successors"],
            "predecessors": block["predecessors"],
            "store_candidates": block["store_candidates"],
        }

    return {
        "function_count": len(functions),
        "block_count": sum(len(function["blocks"]) for function in functions),
        "edge_count": sum(len(block["successors"]) for function in functions for block in function["blocks"]),
        "functions": [
            {
                "id": function["id"],
                "word_index": function["word_index"],
                "block_count": len(function["blocks"]),
                "blocks": [json_block(block) for block in function["blocks"]],
            }
            for function in functions
        ],
        "probe_plan": {
            "method": "instrument-valid-module-not-arbitrary-fragment",
            "binary_search_supported": bool(probe_candidates),
            "candidate_count": len(probe_candidates),
            "candidates": probe_candidates,
            "bisect_rounds": bisect_rounds,
            "notes": [
                "SPIR-V fragments cannot be submitted to Vulkan directly; probes must keep a valid entry point.",
                "Use block boundary or store-site instrumentation, then compare GPU probe output with the CPU oracle.",
                "Static block order is not dynamic execution order; bisect candidate ranges, then confirm the final site with dynamic probe output.",
            ],
        },
    }


def choose_debug_descriptor(descriptor_variables: list[dict], max_sets: int = 8, max_bindings: int = 16) -> dict:
    used = {
        (int(item["set"]), int(item["binding"]))
        for item in descriptor_variables
        if "set" in item and "binding" in item
    }
    used_binding_numbers = {
        int(item["binding"])
        for item in descriptor_variables
        if "binding" in item
    }
    preferred_sets = sorted({set_id for set_id, _binding in used}) or [0]
    for set_id in preferred_sets + [set_id for set_id in range(max_sets) if set_id not in preferred_sets]:
        for binding in range(max_bindings):
            if (set_id, binding) not in used and binding not in used_binding_numbers:
                return {
                    "available": True,
                    "set": set_id,
                    "binding": binding,
                    "strategy": "first-unused-existing-set-or-fallback-set-and-globally-unused-binding-number",
                    "max_sets": max_sets,
                    "max_bindings_per_set": max_bindings,
                }
    return {
        "available": False,
        "reason": "no free descriptor set/binding for diagnostic SSBO",
        "max_sets": max_sets,
        "max_bindings_per_set": max_bindings,
    }


def build_q6_probe_targets(module: dict, debug_descriptor: dict | None = None) -> dict:
    """Describe Q6-like final-output and workgroup stores for valid-module probes.

    This is intentionally structural rather than hash-targeted: it looks for
    writes to the runtime output descriptor and the preceding Workgroup stores
    that feed those writes.  The result is a probe *plan*, not an executable
    instrumentation fragment; the runtime still has to submit a full, validated
    SPIR-V module.
    """

    debug_probe_set = (
        int(debug_descriptor.get("set"))
        if isinstance(debug_descriptor, dict) and isinstance(debug_descriptor.get("set"), int)
        else 0
    )
    debug_probe_binding = (
        int(debug_descriptor.get("binding"))
        if isinstance(debug_descriptor, dict) and isinstance(debug_descriptor.get("binding"), int)
        else 5
    )
    descriptor_by_id = {
        int(item["id"]): item
        for item in module.get("descriptor_variables", [])
        if isinstance(item, dict) and isinstance(item.get("id"), int)
    }

    def block_for_word(word_index: int) -> dict | None:
        for function in module.get("control_flow", {}).get("functions", []):
            for ordinal, block in enumerate(function.get("blocks", [])):
                indices = list(block.get("instruction_word_indices") or [])
                if not indices:
                    continue
                start = int(block.get("word_index", min(indices)))
                end = max(indices + [start])
                if start <= word_index <= end:
                    return {
                        "function_id": function.get("id"),
                        "block_label": block.get("label"),
                        "block_ordinal": ordinal,
                        "block_word_index": block.get("word_index"),
                        "block_entry_insert_after_phi_word_index": block.get("block_entry_insert_after_phi_word_index"),
                        "block_exit_insert_before_word_index": block.get("block_exit_insert_before_word_index"),
                    }
        return None

    candidate_by_block: dict[tuple[int | None, int | None], dict] = {}
    for candidate in module.get("control_flow", {}).get("probe_plan", {}).get("candidates", []):
        candidate_by_block[(candidate.get("function_id"), candidate.get("block_label"))] = candidate

    conditional_predecessors_by_block: dict[tuple[int | None, int | None], list[dict]] = defaultdict(list)
    for function in module.get("control_flow", {}).get("functions", []):
        function_id = function.get("id")
        for block in function.get("blocks", []):
            block_label = block.get("label")
            for predecessor in block.get("predecessors", []):
                terminator = predecessor.get("terminator_instruction") or {}
                if terminator.get("op") != "OpBranchConditional":
                    continue
                branch_side = None
                if terminator.get("true_label") == block_label:
                    branch_side = "true"
                elif terminator.get("false_label") == block_label:
                    branch_side = "false"
                conditional_predecessors_by_block[(function_id, block_label)].append(
                    {
                        "predecessor_block_label": predecessor.get("block_label"),
                        "predecessor_block_ordinal": predecessor.get("block_ordinal"),
                        "condition_id": terminator.get("condition_id"),
                        "branch_side": branch_side,
                    }
                )

    def annotate_store(store: dict, phase: str, role: str, output_store_word_index: int | None = None) -> dict:
        word_index = int(store.get("word_index", -1))
        block = block_for_word(word_index) or {}
        candidate = candidate_by_block.get((block.get("function_id"), block.get("block_label")), {})
        pointer_origin = store.get("pointer_origin") or {}
        base = pointer_origin.get("base") or pointer_origin
        item = {
            "phase": phase,
            "role": role,
            "word_index": word_index,
            "object_id": store.get("object_id"),
            "pointer_id": store.get("pointer_id"),
            "base": {
                "kind": base.get("kind"),
                "id": base.get("id"),
                "set": base.get("set"),
                "binding": base.get("binding"),
                "storage_class": base.get("storage_class"),
                "built_in": base.get("built_in"),
            },
            "index_expr": pointer_origin.get("indices", []),
            "block": block,
            "candidate": {
                "candidate_id": candidate.get("candidate_id"),
                "word_index": candidate.get("word_index"),
                "block_entry_insert_after_phi_word_index": candidate.get("block_entry_insert_after_phi_word_index"),
                "block_exit_insert_before_word_index": candidate.get("block_exit_insert_before_word_index"),
            },
            "control_dependencies": conditional_predecessors_by_block.get(
                (block.get("function_id"), block.get("block_label")),
                [],
            ),
            "capture": [
                "local_invocation_id",
                "workgroup_id",
                "computed_output_index",
                "stored_value_bits",
                "candidate_id",
            ],
        }
        if output_store_word_index is not None:
            item["related_output_store_word_index"] = output_store_word_index
        return item

    def collect_workgroup_bases(expr: object, out: set[int]) -> None:
        if isinstance(expr, dict):
            pointer = expr.get("pointer")
            if isinstance(pointer, dict):
                base = pointer.get("base") or pointer
                if base.get("kind") == "variable" and base.get("storage_class") == "Workgroup":
                    base_id = base.get("id")
                    if isinstance(base_id, int):
                        out.add(base_id)
                collect_workgroup_bases(pointer, out)
            for value in expr.values():
                collect_workgroup_bases(value, out)
        elif isinstance(expr, list):
            for value in expr:
                collect_workgroup_bases(value, out)

    def compact_base(base: object) -> dict:
        if not isinstance(base, dict):
            return {}
        return {
            key: base.get(key)
            for key in ("kind", "id", "name", "set", "binding", "storage_class", "built_in")
            if key in base
        }

    def pointer_base(pointer: object) -> dict:
        if not isinstance(pointer, dict):
            return {}
        base = pointer.get("base")
        if isinstance(base, dict):
            return base
        return pointer

    def expression_signature(expr: object) -> tuple:
        if not isinstance(expr, dict):
            return (type(expr).__name__,)
        kind = expr.get("kind")
        if kind == "constant":
            return ("constant", expr.get("value_u32"))
        if kind == "spec_constant":
            return ("spec_constant", expr.get("spec_id"), expr.get("default_u32"))
        if kind == "variable":
            return ("variable", expr.get("id"), expr.get("storage_class"), expr.get("built_in"))
        if kind == "load":
            return ("load", expr.get("id"), pointer_signature(expr.get("pointer")))
        if kind == "access_chain":
            return (
                "access_chain",
                expr.get("id"),
                compact_base(expr.get("base")),
                tuple(expression_signature(item) for item in expr.get("indices") or []),
            )
        if kind == "op":
            return (
                "op",
                expr.get("id"),
                expr.get("op"),
                tuple(expression_signature(item) for item in expr.get("operands") or []),
            )
        return tuple(
            (key, expr.get(key))
            for key in ("kind", "id", "op", "value_u32", "default_u32", "spec_id")
            if key in expr
        )

    def pointer_signature(pointer: object) -> tuple:
        if not isinstance(pointer, dict):
            return ("none",)
        base = pointer_base(pointer)
        indices = pointer.get("indices") if isinstance(pointer.get("indices"), list) else []
        return (
            base.get("kind"),
            base.get("id"),
            base.get("set"),
            base.get("binding"),
            base.get("storage_class"),
            tuple(expression_signature(index.get("expr", index) if isinstance(index, dict) else index) for index in indices),
        )

    def compact_pointer_signature(pointer: object) -> dict:
        base = pointer_base(pointer)
        indices = pointer.get("indices") if isinstance(pointer, dict) and isinstance(pointer.get("indices"), list) else []
        return {
            "base": compact_base(base),
            "index_count": len(indices),
            "index_signatures": [
                list(expression_signature(index.get("expr", index) if isinstance(index, dict) else index))
                for index in indices[:4]
            ],
            "truncated_index_count": max(0, len(indices) - 4),
        }

    def index_static_u32(index: object) -> int | None:
        if not isinstance(index, dict):
            return None
        if isinstance(index.get("constant_u32"), int):
            return int(index["constant_u32"])
        expr = index.get("expr")
        if isinstance(expr, dict) and expr.get("kind") == "constant" and isinstance(expr.get("value_u32"), int):
            return int(expr["value_u32"])
        if index.get("kind") == "constant" and isinstance(index.get("value_u32"), int):
            return int(index["value_u32"])
        return None

    def compact_type_leaf(type_info: object) -> dict:
        if not isinstance(type_info, dict):
            return {"kind": type(type_info).__name__}
        kind = type_info.get("kind")
        item = {"kind": kind, "id": type_info.get("id")}
        for key in ("bits", "signed", "component_count", "length_u32"):
            if key in type_info:
                item[key] = type_info.get(key)
        if kind == "vector" and isinstance(type_info.get("component"), dict):
            item["component"] = compact_type_leaf(type_info.get("component"))
        return item

    def descriptor_access_from_pointer(
        pointer: object,
        access_word_index: int | None,
        op: str,
        *,
        result_id: int | None = None,
        result_type: int | None = None,
        pointer_id: int | None = None,
    ) -> dict | None:
        if not isinstance(pointer, dict):
            return None
        base = pointer_base(pointer)
        if base.get("kind") != "descriptor" or not isinstance(base.get("id"), int):
            return None
        descriptor = descriptor_by_id.get(int(base["id"]), {})
        current = descriptor.get("pointee_layout")
        indices = pointer.get("indices") if isinstance(pointer.get("indices"), list) else []
        member_path: list[dict] = []
        member_indices: list[int] = []
        static_member_offsets: list[int] = []
        array_strides: list[int] = []
        dynamic_indices: list[dict] = []
        dynamic_terms: list[dict] = []
        static_byte_offset = 0
        static_byte_offset_known = True
        for ordinal, index in enumerate(indices):
            value = index_static_u32(index)
            index_id = index.get("id") if isinstance(index, dict) and isinstance(index.get("id"), int) else None
            if not isinstance(current, dict):
                dynamic_indices.append({"ordinal": ordinal, "id": index_id, "reason": "unknown-layout"})
                static_byte_offset_known = False
                continue
            kind = current.get("kind")
            if kind == "struct":
                members = current.get("members") if isinstance(current.get("members"), list) else []
                if isinstance(value, int) and 0 <= value < len(members) and isinstance(members[value], dict):
                    member = members[value]
                    offset = member.get("offset")
                    member_indices.append(value)
                    member_path.append(
                        {
                            "kind": "struct_member",
                            "index": value,
                            "index_id": index_id,
                            "offset": offset,
                            "type_id": member.get("type_id"),
                        }
                    )
                    if isinstance(offset, int):
                        static_member_offsets.append(offset)
                        if static_byte_offset_known:
                            static_byte_offset += offset
                    else:
                        static_byte_offset_known = False
                    current = member.get("type")
                    continue
                dynamic_indices.append({"ordinal": ordinal, "id": index_id, "reason": "dynamic-struct-member"})
                static_byte_offset_known = False
                continue
            if kind in {"array", "runtime_array"}:
                stride = current.get("array_stride")
                path_item = {
                    "kind": "runtime_array_element" if kind == "runtime_array" else "array_element",
                    "index_id": index_id,
                    "array_stride": stride,
                    "element_type_id": current.get("element_type"),
                }
                if isinstance(value, int):
                    path_item["static_index"] = value
                member_path.append(path_item)
                if isinstance(stride, int):
                    array_strides.append(stride)
                    if isinstance(value, int) and static_byte_offset_known:
                        static_byte_offset += value * stride
                    elif value is None:
                        dynamic_terms.append({"index_id": index_id, "scale": stride})
                else:
                    static_byte_offset_known = False
                if value is None:
                    dynamic_indices.append({"ordinal": ordinal, "id": index_id, "reason": f"dynamic-{kind}-index"})
                current = current.get("element")
                continue
            if kind == "vector":
                path_item = {"kind": "vector_component", "index_id": index_id}
                if isinstance(value, int):
                    path_item["static_index"] = value
                member_path.append(path_item)
                if value is None:
                    dynamic_indices.append({"ordinal": ordinal, "id": index_id, "reason": "dynamic-vector-index"})
                    static_byte_offset_known = False
                current = current.get("component")
                continue
            dynamic_indices.append({"ordinal": ordinal, "id": index_id, "reason": f"unhandled-{kind}"})
            static_byte_offset_known = False
        leaf = compact_type_leaf(current)
        descriptor_ref = {
            "set": descriptor.get("set", base.get("set")),
            "binding": descriptor.get("binding", base.get("binding")),
            "variable_id": descriptor.get("id", base.get("id")),
            "storage_class": descriptor.get("storage_class", base.get("storage_class")),
        }
        byte_offset = {
            "static": static_byte_offset if static_byte_offset_known else None,
            "dynamic_terms": dynamic_terms,
            "unit": "bytes",
        }
        return {
            "op": op,
            "access_word_index": access_word_index,
            "load_word_index": access_word_index if op == "OpLoad" else None,
            "result_id": result_id,
            "result_type": result_type,
            "pointer_id": pointer_id,
            "set": descriptor_ref.get("set"),
            "binding": descriptor_ref.get("binding"),
            "variable_id": descriptor_ref.get("variable_id"),
            "descriptor": descriptor_ref,
            "access_chain_result_id": pointer.get("access_chain_result_id"),
            "member_path": member_path,
            "member_indices": member_indices,
            "static_member_offsets": static_member_offsets,
            "array_strides": array_strides,
            "dynamic_indices": dynamic_indices,
            "byte_offset": byte_offset,
            "static_byte_offset": byte_offset["static"],
            "element": leaf,
            "terminal_type": leaf,
            "layout_fingerprint": {
                **descriptor_ref,
                "member_indices": member_indices,
                "member_path": member_path,
                "offsets": static_member_offsets,
                "strides": array_strides,
                "terminal_type": leaf,
            },
        }

    def summarize_expr_node(expr: dict) -> dict:
        item = {
            key: expr.get(key)
            for key in ("kind", "id", "op", "value_u32", "default_u32", "spec_id")
            if key in expr
        }
        if expr.get("kind") == "load":
            item["pointer_base"] = compact_base(pointer_base(expr.get("pointer")))
        elif expr.get("kind") == "access_chain":
            item["base"] = compact_base(expr.get("base"))
            item["index_count"] = len(expr.get("indices") or [])
        return item

    def collect_expression_dependencies(
        expr: object,
        max_nodes: int = 64,
        max_depth: int = 12,
        store_word_limit: int | None = None,
    ) -> dict:
        # The analyzer already builds a bounded expression tree.  This pass keeps
        # only the dependency facts needed for Q6 final-store safety gates.
        nodes: list[dict] = []
        workgroup_loads: list[dict] = []
        function_loads: list[dict] = []
        function_store_expansions: list[dict] = []
        spec_constant_dependencies: dict[int, dict] = {}
        constant_dependencies: dict[int, dict] = {}
        builtin_dependencies: dict[tuple[int | None, str | None, str | None], dict] = {}
        push_constant_dependencies: dict[tuple[int | None, int | None, int | None], dict] = {}
        descriptor_dependencies: set[tuple[int | None, int]] = set()
        descriptor_load_leaves: list[dict] = []
        unresolved_id_leaves: list[dict] = []
        op_histogram: Counter[str] = Counter()
        ext_inst_histogram: Counter[str] = Counter()
        named_arithmetic_histogram: Counter[str] = Counter()
        group_nonuniform_histogram: Counter[str] = Counter()
        load_count = 0
        truncated_nodes = 0
        truncated_depth = 0
        truncated_function_store_expansions = 0
        depends_on_debug_probe_binding = False
        seen_workgroup_loads: set[tuple[int | None, int | None]] = set()
        seen_function_loads: set[tuple[int | None, int | None, int | None]] = set()
        seen_function_store_expansions: set[tuple[int, int | None, int]] = set()
        seen_descriptor_load_leaves: set[tuple] = set()
        seen_unresolved_id_leaves: set[tuple[int | None, str | None]] = set()

        def record_descriptor(base: dict) -> None:
            nonlocal depends_on_debug_probe_binding
            if base.get("kind") != "descriptor" or not isinstance(base.get("binding"), int):
                return
            descriptor_set = base.get("set") if isinstance(base.get("set"), int) else None
            binding = int(base["binding"])
            descriptor_dependencies.add((descriptor_set, binding))
            if binding == debug_probe_binding and (descriptor_set is None or descriptor_set == debug_probe_set):
                depends_on_debug_probe_binding = True

        def record_base_dependency(base: object) -> None:
            if not isinstance(base, dict):
                return
            built_in = base.get("built_in")
            if isinstance(built_in, str):
                key = (
                    base.get("id") if isinstance(base.get("id"), int) else None,
                    built_in,
                    base.get("storage_class") if isinstance(base.get("storage_class"), str) else None,
                )
                builtin_dependencies[key] = {
                    "id": key[0],
                    "built_in": built_in,
                    "storage_class": key[2],
                    "kind": base.get("kind"),
                }

        def record_pointer_dependency(pointer: object) -> None:
            if not isinstance(pointer, dict):
                return
            base = pointer_base(pointer)
            record_base_dependency(base)
            if base.get("kind") == "push_constant":
                member = pointer.get("push_member") if isinstance(pointer.get("push_member"), dict) else {}
                member_index = member.get("index") if isinstance(member.get("index"), int) else None
                member_offset = member.get("offset") if isinstance(member.get("offset"), int) else None
                key = (
                    base.get("id") if isinstance(base.get("id"), int) else None,
                    member_index,
                    member_offset,
                )
                push_constant_dependencies[key] = {
                    "variable_id": key[0],
                    "variable_name": base.get("name", ""),
                    "member_index": member_index,
                    "member_name": member.get("name", "") if isinstance(member, dict) else "",
                    "member_offset": member_offset,
                    "member_type": member.get("type") if isinstance(member, dict) else None,
                    "pointer": compact_pointer_signature(pointer),
                }

        def record_descriptor_load_leaf(value: dict, pointer: object) -> None:
            access = descriptor_access_from_pointer(
                pointer,
                value.get("word_index") if isinstance(value.get("word_index"), int) else None,
                "OpLoad",
                result_id=value.get("id") if isinstance(value.get("id"), int) else None,
                result_type=value.get("result_type") if isinstance(value.get("result_type"), int) else None,
                pointer_id=value.get("pointer_id") if isinstance(value.get("pointer_id"), int) else None,
            )
            if not access:
                return
            key = (
                access.get("access_word_index"),
                access.get("set"),
                access.get("binding"),
                access.get("variable_id"),
                json.dumps(access.get("member_path") or [], sort_keys=True),
                tuple(access.get("array_strides") or []),
                json.dumps(access.get("terminal_type"), sort_keys=True),
            )
            if key in seen_descriptor_load_leaves:
                return
            seen_descriptor_load_leaves.add(key)
            descriptor_load_leaves.append(access)

        def record_unresolved_id_leaf(value: dict) -> None:
            kind = value.get("kind")
            if kind not in {"id", "max-depth", "cycle"}:
                return
            key = (value.get("id") if isinstance(value.get("id"), int) else None, kind)
            if key in seen_unresolved_id_leaves:
                return
            seen_unresolved_id_leaves.add(key)
            unresolved_id_leaves.append({"id": key[0], "kind": kind, "name": value.get("name", "")})

        def record_scalar_dependency(value: dict) -> None:
            kind = value.get("kind")
            value_id = value.get("id") if isinstance(value.get("id"), int) else None
            if kind == "spec_constant" and value_id is not None:
                spec_constant_dependencies[value_id] = {
                    "id": value_id,
                    "spec_id": value.get("spec_id") if isinstance(value.get("spec_id"), int) else None,
                    "default_u32": value.get("default_u32") if isinstance(value.get("default_u32"), int) else None,
                }
            elif kind == "constant" and value_id is not None:
                constant_dependencies[value_id] = {
                    "id": value_id,
                    "value_u32": value.get("value_u32") if isinstance(value.get("value_u32"), int) else None,
                }
            if kind == "variable":
                record_base_dependency(value)

        def record_node(value: dict) -> None:
            nonlocal truncated_nodes
            if len(nodes) >= max_nodes:
                truncated_nodes += 1
                return
            summary = summarize_expr_node(value)
            if summary:
                nodes.append(summary)

        def matching_function_stores(pointer: object, current_word_limit: int | None, max_matches: int = 8) -> tuple[str, list[dict]]:
            if current_word_limit is None:
                return "no-store-word-limit", []
            base = pointer_base(pointer)
            base_id = base.get("id")
            if base.get("kind") != "variable" or base.get("storage_class") != "Function" or not isinstance(base_id, int):
                return "not-function-load", []
            load_sig = pointer_signature(pointer)
            base_matches = [
                store
                for store in stores
                if int(store.get("word_index", -1)) < current_word_limit
                and ((store.get("pointer_origin") or {}).get("base") or store.get("pointer_origin") or {}).get("kind") == "variable"
                and ((store.get("pointer_origin") or {}).get("base") or store.get("pointer_origin") or {}).get("storage_class") == "Function"
                and ((store.get("pointer_origin") or {}).get("base") or store.get("pointer_origin") or {}).get("id") == base_id
            ]
            exact_matches = [
                store
                for store in base_matches
                if pointer_signature(store.get("pointer_origin")) == load_sig
            ]
            selected = exact_matches if exact_matches else base_matches
            strategy = "pointer-signature" if exact_matches else "function-base-conservative"
            if len(selected) > max_matches:
                selected = selected[-max_matches:]
                strategy += "-latest-window"
            return strategy, selected

        def expand_function_load(value: dict, pointer: object, base: dict, depth: int, current_word_limit: int | None) -> None:
            nonlocal truncated_function_store_expansions
            base_id = base.get("id") if isinstance(base.get("id"), int) else None
            load_word_limit = value.get("word_index") if isinstance(value.get("word_index"), int) else current_word_limit
            load_key = (
                value.get("id") if isinstance(value.get("id"), int) else None,
                base_id,
                load_word_limit,
            )
            if load_key not in seen_function_loads:
                seen_function_loads.add(load_key)
                function_loads.append(
                    {
                        "id": value.get("id"),
                        "pointer": compact_pointer_signature(pointer),
                        "load_word_index": value.get("word_index"),
                        "store_word_limit": load_word_limit,
                    }
                )
            strategy, candidate_stores = matching_function_stores(pointer, load_word_limit)
            for store in candidate_stores:
                store_word = int(store.get("word_index", -1))
                expansion_key = (store_word, value.get("id") if isinstance(value.get("id"), int) else None, depth)
                if expansion_key in seen_function_store_expansions:
                    continue
                if len(function_store_expansions) >= 24:
                    truncated_function_store_expansions += 1
                    continue
                seen_function_store_expansions.add(expansion_key)
                function_store_expansions.append(
                    {
                        "load_id": value.get("id"),
                        "matched_store_word_index": store_word,
                        "match_strategy": strategy,
                        "store_pointer_id": store.get("pointer_id"),
                        "store_object_id": store.get("object_id"),
                        "store_pointer": compact_pointer_signature(store.get("pointer_origin")),
                        "store_object_root": summarize_expr_node(store.get("object_expr"))
                        if isinstance(store.get("object_expr"), dict)
                        else {"kind": type(store.get("object_expr")).__name__},
                    }
                )
                visit(store.get("object_expr"), depth + 1, store_word)

        def visit(value: object, depth: int = 0, current_word_limit: int | None = store_word_limit) -> None:
            nonlocal load_count, truncated_depth
            if depth > max_depth:
                truncated_depth += 1
                return
            if isinstance(value, list):
                for child in value:
                    visit(child, depth + 1, current_word_limit)
                return
            if not isinstance(value, dict):
                return

            op = value.get("op")
            if isinstance(op, str):
                op_histogram[op] += 1
                qualified_op = value.get("qualified_op") if isinstance(value.get("qualified_op"), str) else None
                if op == "OpExtInst" and qualified_op:
                    ext_inst_histogram[qualified_op] += 1
                    named_arithmetic_histogram[qualified_op] += 1
                if op.startswith("OpGroupNonUniform"):
                    group_nonuniform_histogram[op] += 1
                    named_arithmetic_histogram[op] += 1
            if any(key in value for key in ("kind", "id", "op")):
                record_node(value)
                record_scalar_dependency(value)
                record_unresolved_id_leaf(value)

            base = value.get("base")
            if isinstance(base, dict):
                record_descriptor(base)
                record_base_dependency(base)
            if value.get("kind") == "access_chain":
                record_pointer_dependency(value)

            if value.get("kind") == "load":
                load_count += 1
                pointer = value.get("pointer")
                base = pointer_base(pointer)
                record_descriptor(base)
                record_pointer_dependency(pointer)
                if base.get("kind") == "descriptor":
                    record_descriptor_load_leaf(value, pointer)
                if base.get("kind") == "variable" and base.get("storage_class") == "Workgroup":
                    key = (value.get("id") if isinstance(value.get("id"), int) else None, base.get("id"))
                    if key not in seen_workgroup_loads:
                        seen_workgroup_loads.add(key)
                        workgroup_loads.append(
                            {
                                "id": value.get("id"),
                                "pointer_base": compact_base(base),
                                "access_chain_result_id": (
                                    pointer.get("access_chain_result_id")
                                    if isinstance(pointer, dict)
                                    else None
                                ),
                            }
                        )
                elif base.get("kind") == "variable" and base.get("storage_class") == "Function":
                    expand_function_load(value, pointer, base, depth, current_word_limit)

            for key in ("pointer", "indices", "expr", "operands"):
                child = value.get(key)
                if child is not None:
                    visit(child, depth + 1, current_word_limit)

        visit(expr)
        return {
            "root": summarize_expr_node(expr) if isinstance(expr, dict) else {"kind": type(expr).__name__},
            "load_count": load_count,
            "op_histogram": dict(op_histogram.most_common()),
            "ext_inst_histogram": dict(ext_inst_histogram.most_common()),
            "named_arithmetic_histogram": dict(named_arithmetic_histogram.most_common()),
            "group_nonuniform_histogram": dict(group_nonuniform_histogram.most_common()),
            "producer_chain": nodes,
            "truncated_node_count": truncated_nodes,
            "truncated_depth_count": truncated_depth,
            "function_loads": function_loads,
            "function_store_expansions": function_store_expansions,
            "truncated_function_store_expansion_count": truncated_function_store_expansions,
            "workgroup_loads": workgroup_loads,
            "reaches_workgroup_load": bool(workgroup_loads),
            "descriptor_load_leaves": descriptor_load_leaves,
            "descriptor_load_leaf_count": len(descriptor_load_leaves),
            "unresolved_id_leaves": unresolved_id_leaves,
            "slice_complete": not (
                truncated_nodes
                or truncated_depth
                or truncated_function_store_expansions
                or unresolved_id_leaves
            ),
            "truncation_boundaries": {
                "truncated_node_count": truncated_nodes,
                "truncated_depth_count": truncated_depth,
                "truncated_function_store_expansion_count": truncated_function_store_expansions,
            },
            "spec_constant_dependencies": [
                spec_constant_dependencies[key]
                for key in sorted(
                    spec_constant_dependencies,
                    key=lambda item: (
                        -1
                        if spec_constant_dependencies[item].get("spec_id") is None
                        else spec_constant_dependencies[item].get("spec_id"),
                        item,
                    ),
                )
            ],
            "constant_dependencies": [
                constant_dependencies[key]
                for key in sorted(
                    constant_dependencies,
                    key=lambda item: (
                        -1
                        if constant_dependencies[item].get("value_u32") is None
                        else constant_dependencies[item].get("value_u32"),
                        item,
                    ),
                )
            ],
            "builtin_dependencies": [
                builtin_dependencies[key]
                for key in sorted(
                    builtin_dependencies,
                    key=lambda item: (
                        "" if item[1] is None else item[1],
                        -1 if item[0] is None else item[0],
                    ),
                )
            ],
            "push_constant_dependencies": [
                push_constant_dependencies[key]
                for key in sorted(
                    push_constant_dependencies,
                    key=lambda item: (
                        -1 if item[0] is None else item[0],
                        -1 if item[1] is None else item[1],
                        -1 if item[2] is None else item[2],
                    ),
                )
            ],
            "descriptor_dependencies": [
                {"set": descriptor_set, "binding": binding}
                for descriptor_set, binding in sorted(
                    descriptor_dependencies,
                    key=lambda item: (-1 if item[0] is None else item[0], item[1]),
                )
            ],
            "depends_on_debug_probe_binding": depends_on_debug_probe_binding,
        }

    stores = sorted(module.get("store_events", []), key=lambda item: int(item.get("word_index", -1)))
    final_output_stores = []
    workgroup_stores = []
    for store in stores:
        pointer_origin = store.get("pointer_origin") or {}
        base = pointer_origin.get("base") or pointer_origin
        if base.get("kind") == "descriptor" and base.get("binding") == 2:
            final_output_stores.append(store)
        if base.get("kind") == "variable" and base.get("storage_class") == "Workgroup":
            workgroup_stores.append(store)

    phases = []
    previous_output_word = -1
    for phase_index, output_store in enumerate(final_output_stores):
        output_word = int(output_store.get("word_index", -1))
        phase_name = "tail" if phase_index == 0 and len(final_output_stores) > 1 else "full" if len(final_output_stores) > 1 else "single"
        source_workgroup_base_ids: set[int] = set()
        collect_workgroup_bases(output_store.get("object_expr"), source_workgroup_base_ids)
        preceding = [
            store
            for store in workgroup_stores
            if previous_output_word < int(store.get("word_index", -1)) < output_word
            and (
                not source_workgroup_base_ids
                or ((store.get("pointer_origin") or {}).get("base") or {}).get("id") in source_workgroup_base_ids
            )
        ]
        priority_roles = []
        if len(preceding) >= 1:
            priority_roles.append("partial_to_workgroup_candidate")
        if len(preceding) >= 2:
            priority_roles.append("reduction_candidate")
        while len(priority_roles) < len(preceding):
            priority_roles.append("post_reduction_workgroup_candidate")
        phases.append(
            {
                "name": phase_name,
                "source_workgroup_base_ids": sorted(source_workgroup_base_ids),
                "output_store": annotate_store(output_store, phase_name, "final_output_store"),
                "preceding_workgroup_stores": [
                    annotate_store(store, phase_name, priority_roles[index], output_word)
                    for index, store in enumerate(preceding)
                ],
            }
        )
        previous_output_word = output_word

    priority_targets = []
    for phase in phases:
        priority_targets.extend(phase["preceding_workgroup_stores"])
        priority_targets.append(phase["output_store"])

    phase_by_output_word = {
        int(phase["output_store"]["word_index"]): phase.get("name")
        for phase in phases
        if isinstance((phase.get("output_store") or {}).get("word_index"), int)
    }

    final_store_flows = []
    for output_store in final_output_stores:
        pointer_origin = output_store.get("pointer_origin") or {}
        base = pointer_origin.get("base") or pointer_origin
        output_word = int(output_store.get("word_index", -1))
        stored_value = collect_expression_dependencies(output_store.get("object_expr"), store_word_limit=output_word)
        output_index = collect_expression_dependencies([
            index.get("expr", index) if isinstance(index, dict) else index
            for index in pointer_origin.get("indices", [])
        ])
        stored_depends_on_debug = bool(stored_value.get("depends_on_debug_probe_binding"))
        index_depends_on_debug = bool(output_index.get("depends_on_debug_probe_binding"))
        final_store_flows.append(
            {
                "phase": phase_by_output_word.get(output_word),
                "word_index": output_word,
                "pointer_id": output_store.get("pointer_id"),
                "object_id": output_store.get("object_id"),
                "output_store": {
                    "base": compact_base(base),
                    "required_binding": 2,
                    "binding_matches_required": base.get("kind") == "descriptor" and base.get("binding") == 2,
                },
                "stored_value": stored_value,
                "output_index": {
                    **output_index,
                    "index_ids": [
                        index.get("id")
                        for index in pointer_origin.get("indices", [])
                        if isinstance(index, dict) and isinstance(index.get("id"), int)
                    ],
                },
                "debug_probe_exclusion": {
                    "set": debug_probe_set,
                    "binding": debug_probe_binding,
                    "stored_value_depends_on_debug_probe": stored_depends_on_debug,
                    "output_index_depends_on_debug_probe": index_depends_on_debug,
                    "passed": not stored_depends_on_debug and not index_depends_on_debug,
                },
                "valid": (
                    base.get("kind") == "descriptor"
                    and base.get("binding") == 2
                    and bool(stored_value.get("reaches_workgroup_load"))
                    and not stored_depends_on_debug
                    and not index_depends_on_debug
                ),
            }
        )

    barrier_events = sorted(
        [barrier for barrier in module.get("barrier_events", []) if isinstance(barrier, dict)],
        key=lambda item: int(item.get("word_index", -1)),
    )

    def compact_barrier_event(barrier: dict | None) -> dict | None:
        if not isinstance(barrier, dict):
            return None
        return {
            key: barrier.get(key)
            for key in (
                "word_index",
                "opcode",
                "op",
                "word_count",
                "raw_operands",
                "execution_scope_id",
                "execution_scope_value",
                "execution_scope_name",
                "memory_scope_id",
                "memory_scope_value",
                "memory_scope_name",
                "memory_semantics_id",
                "memory_semantics_value",
                "memory_semantics_names",
                "block",
            )
            if key in barrier
        }

    ext_inst_events = sorted(
        [event for event in module.get("ext_inst_events", []) if isinstance(event, dict)],
        key=lambda item: int(item.get("word_index", -1)),
    )
    group_nonuniform_events = sorted(
        [event for event in module.get("group_nonuniform_events", []) if isinstance(event, dict)],
        key=lambda item: int(item.get("word_index", -1)),
    )

    def arithmetic_window_counts(start_exclusive: int, end_exclusive: int) -> dict:
        ext_hist = Counter(
            event.get("qualified_op")
            for event in ext_inst_events
            if start_exclusive < int(event.get("word_index", -1)) < end_exclusive
            and isinstance(event.get("qualified_op"), str)
        )
        group_hist = Counter(
            event.get("op")
            for event in group_nonuniform_events
            if start_exclusive < int(event.get("word_index", -1)) < end_exclusive
            and isinstance(event.get("op"), str)
        )
        named = Counter()
        named.update(ext_hist)
        named.update(group_hist)
        return {
            "ext_inst_histogram": dict(ext_hist.most_common()),
            "group_nonuniform_histogram": dict(group_hist.most_common()),
            "named_arithmetic_histogram": dict(named.most_common()),
        }

    q6_barrier_windows = []
    q6_arithmetic_windows = []
    previous_output_for_barrier_window = -1
    for phase in phases:
        output_store = phase.get("output_store") if isinstance(phase.get("output_store"), dict) else {}
        output_word = output_store.get("word_index")
        if not isinstance(output_word, int):
            continue
        workgroup_store_words = [
            store.get("word_index")
            for store in phase.get("preceding_workgroup_stores", [])
            if isinstance(store, dict) and isinstance(store.get("word_index"), int)
        ]
        barriers_in_window = [
            barrier
            for barrier in barrier_events
            if previous_output_for_barrier_window < int(barrier.get("word_index", -1)) < output_word
        ]
        pairs = []
        for workgroup_word in workgroup_store_words:
            following = next(
                (
                    barrier
                    for barrier in barriers_in_window
                    if int(barrier.get("word_index", -1)) > workgroup_word
                ),
                None,
            )
            pairs.append(
                {
                    "workgroup_store_word_index": workgroup_word,
                    "barrier_word_index": following.get("word_index") if isinstance(following, dict) else None,
                }
            )
        arithmetic_counts = arithmetic_window_counts(previous_output_for_barrier_window, output_word)
        q6_barrier_windows.append(
            {
                "phase": phase.get("name"),
                "window_start_exclusive_word_index": previous_output_for_barrier_window,
                "output_store_word_index": output_word,
                "workgroup_store_word_indices": workgroup_store_words,
                "barrier_word_indices": [barrier.get("word_index") for barrier in barriers_in_window],
                "barriers": [compact_barrier_event(barrier) for barrier in barriers_in_window],
                "workgroup_store_barrier_pairs": pairs,
                "all_workgroup_stores_have_following_barrier": all(
                    pair.get("barrier_word_index") is not None for pair in pairs
                ),
                "all_barriers_are_workgroup_acquire_release": all(
                    barrier.get("op") == "OpControlBarrier"
                    and barrier.get("execution_scope_value") == 2
                    and barrier.get("memory_scope_value") == 2
                    and "AcquireRelease" in (barrier.get("memory_semantics_names") or [])
                    and "WorkgroupMemory" in (barrier.get("memory_semantics_names") or [])
                    for barrier in barriers_in_window
                ),
                "arithmetic_window": arithmetic_counts,
            }
        )
        q6_arithmetic_windows.append(
            {
                "phase": phase.get("name"),
                "window_start_exclusive_word_index": previous_output_for_barrier_window,
                "output_store_word_index": output_word,
                **arithmetic_counts,
            }
        )
        previous_output_for_barrier_window = output_word

    q6_barrier_window_evidence = {
        "schema": "pdocker.spirv.q6-barrier-window-evidence.v1",
        "method": "static-word-order-window-between-workgroup-stores-and-final-output-stores",
        "available": bool(q6_barrier_windows),
        "window_count": len(q6_barrier_windows),
        "barrier_event_count": len(barrier_events),
        "windows": q6_barrier_windows,
        "notes": [
            "This is static word-order evidence, not a proof of dynamic execution order.",
            "Barrier semantics are decoded as SPIR-V bit flags; WorkgroupMemory plus AcquireRelease is required for the Q6 workgroup windows.",
        ],
    }

    q6_arithmetic_window_evidence = {
        "schema": "pdocker.spirv.q6-arithmetic-window-evidence.v1",
        "method": "static-word-order-window-between-final-output-stores",
        "available": bool(q6_arithmetic_windows),
        "window_count": len(q6_arithmetic_windows),
        "windows": q6_arithmetic_windows,
        "notes": [
            "This is static word-order evidence for named SPIR-V arithmetic instructions in each Q6 phase window.",
            "It complements bounded backward slices, which may intentionally truncate deep producer graphs.",
        ],
    }

    final_store_value_flow = {
        "schema": "pdocker.spirv.q6-final-store-value-flow.v1",
        "method": "backward-slice-stored-value-and-output-index",
        "required_output_descriptor_binding": 2,
        "debug_probe_descriptor": {
            "set": debug_probe_set,
            "binding": debug_probe_binding,
        },
        "final_store_count": len(final_store_flows),
        "valid_store_count": sum(1 for item in final_store_flows if item.get("valid") is True),
        "available": bool(final_store_flows) and all(item.get("valid") is True for item in final_store_flows),
        "stores": final_store_flows,
        "notes": [
            "Each final output OpStore must target descriptor binding 2.",
            "The stored value producer chain is expected to reach a Workgroup load for native Q6_K.",
            "The debug/probe descriptor must not be a dependency of either the stored value or output index.",
        ],
    }

    return {
        "available": bool(final_output_stores and workgroup_stores),
        "method": "structural-output-descriptor-and-workgroup-store-chain",
        "output_descriptor_binding": 2,
        "final_output_store_count": len(final_output_stores),
        "workgroup_store_count": len(workgroup_stores),
        "final_store_value_flow": final_store_value_flow,
        "q6_barrier_window_evidence": q6_barrier_window_evidence,
        "q6_arithmetic_window_evidence": q6_arithmetic_window_evidence,
        "phases": phases,
        "priority_targets": priority_targets,
        "notes": [
            "Targets are derived from descriptor/workgroup dataflow, not from a shader hash.",
            "Use priority targets to distinguish partial accumulation, reduction, and final output store.",
            "Probe execution still requires full-module instrumentation plus spirv-val success.",
        ],
    }


def validation_target_env_for_spirv_version(version_hex: str) -> str:
    """Return the minimum Vulkan target-env that can validate this SPIR-V module."""
    try:
        version = int(version_hex, 16)
    except (TypeError, ValueError):
        return "vulkan1.2"
    if version >= 0x00010600:
        return "vulkan1.3"
    if version >= 0x00010500:
        return "vulkan1.2"
    if version >= 0x00010300:
        return "vulkan1.1"
    return "vulkan1.0"


def build_probe_manifest(module: dict, source_path: Path, probe_range: tuple[int, int] | None = None) -> dict:
    control_flow = module.get("control_flow", {})
    probe_plan = control_flow.get("probe_plan", {})
    candidates = list(probe_plan.get("candidates", []))
    if probe_range is None:
        selected_range = [0, len(candidates)]
    else:
        start, end = probe_range
        start = max(0, min(start, len(candidates)))
        end = max(start, min(end, len(candidates)))
        selected_range = [start, end]
    selected_candidates = candidates[selected_range[0]:selected_range[1]]
    descriptor_choice = choose_debug_descriptor(module.get("descriptor_variables", []))
    candidate_ranges = []
    for round_index, groups in enumerate(probe_plan.get("bisect_rounds", [])):
        for group_index, group in enumerate(groups):
            candidate_ranges.append(
                {
                    "round": round_index,
                    "range_id": f"r{round_index}-{group_index}",
                    "candidate_index_range": group.get("range", [0, 0]),
                    "candidate_indices": group.get("candidate_indices", []),
                    "candidate_count": group.get("candidate_count", 0),
                    "leaf": bool(group.get("leaf", False)),
                    "activation": "instrument_all_candidates_in_range",
                }
            )
    first_function_word_index = None
    functions = control_flow.get("functions", [])
    if functions:
        first_function_word_index = min(function.get("word_index", 0) for function in functions)
    validation_gate_messages = [
        "input module must pass spirv-val before instrumentation",
        "instrumented module must pass spirv-val after instrumentation",
        "probe insertion must preserve OpPhi ordering",
        "probe insertion must occur before OpLoopMerge/OpSelectionMerge when probing block exit",
        "debug descriptor must not collide with existing descriptor set/binding",
        "probe output slots must be deterministic; avoid multiple invocations writing the same slot",
        "original/effective SPIR-V hash and probe policy must be recorded with the artifact",
    ]
    return {
        "schema": "pdocker.spirv.probe-manifest.v1",
        "basis": {
            "analysis_schema": module.get("schema"),
            "source_spirv": str(source_path),
            "module_hash": module.get("hash"),
            "module_bytes": module.get("bytes"),
            "module_words": module.get("words"),
            "module_bound": module.get("bound"),
            "module_instruction_count": module.get("instruction_count"),
            "instrumentation_basis": "effective-pre-debug",
            "prior_transforms": [],
        },
        "entry": {
            "name": "main",
            "local_size": module.get("local_size", [0, 0, 0]),
            "local_size_id": module.get("local_size_id", [0, 0, 0]),
            "specialization_entries": [],
        },
        "descriptors": {
            "declared": module.get("descriptor_variables", []),
            "runtime_writes": [],
            "aliases": [],
            "duplicate_bindings": module.get("duplicate_bindings", []),
        },
        "policy": {
            "submission_model": "valid-module-instrumentation",
            "fragment_submission_allowed": False,
            "llama_cpp_modified": False,
            "dockerfile_model_prompt_modified": False,
            "static_order_is_dynamic_order": False,
        },
        "insertion_layout": {
            "first_function_word_index": first_function_word_index,
            "annotation_insert_before_word_index": first_function_word_index,
            "type_global_insert_before_word_index": first_function_word_index,
            "old_bound": module.get("bound"),
            "reserved_id_range": [module.get("bound"), module.get("bound")],
            "new_bound": module.get("bound"),
        },
        "debug_ssbo": {
            "descriptor": descriptor_choice,
            "descriptor_type": "storage_buffer",
            "access": "write_only",
            "dispatch_transport": "append-as-normal-vulkan-dispatch-v4-binding",
            "record_layout": {
                "magic": "PDBG",
                "version": 1,
                "header_u32": 8,
                "record_u32": 12,
                "slot_policy": "probe_id_times_sample_count_plus_sample_index",
                "atomics_required": False,
            },
        },
        "probe_selection": {
            "method": "instrument-valid-module-not-arbitrary-fragment",
            "candidate_range": selected_range,
            "selected_candidate_count": len(selected_candidates),
            "selected_candidates": selected_candidates,
            "bisect_rounds": probe_plan.get("bisect_rounds", []),
            "candidate_ranges": candidate_ranges,
        },
        "q6_probe_targets": build_q6_probe_targets(module, descriptor_choice),
        "insertion_rules": {
            "block_entry": "insert after contiguous OpPhi instructions",
            "block_exit": "insert before OpLoopMerge/OpSelectionMerge if present, otherwise before terminator",
            "store_site": "insert around OpStore/OpCopyMemory sites after type and pointer-origin analysis",
        },
        "collision_checks": {
            "basis": "effective-pre-debug",
            "proposed": {
                "set": descriptor_choice.get("set"),
                "binding": descriptor_choice.get("binding"),
            } if descriptor_choice.get("available") else None,
            "static_declared_collision": False if descriptor_choice.get("available") else None,
            "static_binding_number_collision": False if descriptor_choice.get("available") else None,
            "runtime_write_collision": "unknown-until-dispatch-metadata",
            "alias_collision": "unknown-until-dispatch-metadata",
            "duplicate_binding_collision": False if descriptor_choice.get("available") else None,
            "binding_count_limit": "must-satisfy-original-plus-debug <= PDOCKER_GPU_MAX_VULKAN_BINDINGS",
            "fd_count_limit": "must-satisfy-shader-plus-original-bindings-plus-debug <= PDOCKER_GPU_MAX_PASSED_FDS",
            "within_static_tool_limits": bool(descriptor_choice.get("available")),
            "decision": "pass" if descriptor_choice.get("available") else "fail",
        },
        "validation_gates": {
            "spirv_val_required": True,
            "target_env": validation_target_env_for_spirv_version(module.get("version", "")),
            "pre_instrumentation": {
                "status": "required-before-instrumentation",
                "hash": module.get("hash"),
            },
            "post_instrumentation": {
                "status": "required-before-dispatch",
                "hash": None,
                "stderr_tail": "",
            },
            "dispatch_allowed": False,
            "messages": validation_gate_messages,
        },
        "next_implementation_step": "generate instrumented full SPIR-V module and validate with spirv-val",
    }


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
    names: dict[int, str] = {}
    member_names: dict[int, dict[int, str]] = defaultdict(dict)
    entry_points: list[dict] = []
    type_pointer: dict[int, dict] = {}
    type_struct: dict[int, dict] = {}
    type_vector: dict[int, dict] = {}
    type_matrix: dict[int, dict] = {}
    type_array: dict[int, dict] = {}
    type_runtime_array: dict[int, dict] = {}
    variable: dict[int, dict] = {}
    type_scalar: dict[int, dict] = {}
    constants: dict[int, dict] = {}
    constant_composites: dict[int, dict] = {}
    spec_constants: dict[int, dict] = {}
    spec_constant_composites: dict[int, dict] = {}
    id_defs: dict[int, dict] = {}
    ext_inst_imports_by_id: dict[int, str] = {}
    ext_inst_events_raw: list[dict] = []
    group_nonuniform_events_raw: list[dict] = []
    access_chains_raw: list[dict] = []
    load_events_raw: list[dict] = []
    store_events_raw: list[dict] = []
    barrier_events_raw: list[dict] = []
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
            if len(inst) >= 4:
                id_defs[inst[2]] = {
                    "op": "OpLoad",
                    "result_type": inst[1],
                    "pointer_id": inst[3],
                    "word_index": _index,
                }
                load_events_raw.append(
                    {
                        "word_index": _index,
                        "result_type": inst[1],
                        "result_id": inst[2],
                        "pointer_id": inst[3],
                    }
                )
        elif opcode in (62,):
            stores += 1
            if len(inst) >= 3:
                store_events_raw.append(
                    {
                        "word_index": _index,
                        "pointer_id": inst[1],
                        "object_id": inst[2],
                    }
                )
        elif opcode in (63, 64):
            stores += 1
        elif opcode in (65, 66):
            access_chain_count += 1
        elif opcode in (224, 225):
            barriers += 1
            barrier_events_raw.append(
                {
                    "word_index": _index,
                    "opcode": opcode,
                    "op": OP_NAMES.get(opcode, f"Op{opcode}"),
                    "word_count": len(inst),
                    "raw_operands": inst[1:],
                }
            )
        elif 124 <= opcode <= 190 or opcode in (350,):
            arithmetic += 1
        elif 245 <= opcode <= 255:
            control += 1

        if opcode == 5 and len(inst) >= 3:
            names[inst[1]] = decode_spirv_string(inst, 2)
        elif opcode == 6 and len(inst) >= 4:
            member_names[inst[1]][inst[2]] = decode_spirv_string(inst, 3)
        elif opcode == 11 and len(inst) >= 3:
            ext_inst_imports_by_id[inst[1]] = decode_spirv_string(inst, 2)
        elif opcode == 15 and len(inst) >= 4:
            entry_points.append(
                {
                    "execution_model": inst[1],
                    "execution_model_name": EXECUTION_MODEL_NAMES.get(inst[1], str(inst[1])),
                    "id": inst[2],
                    "name": decode_spirv_string(inst, 3),
                }
            )
        elif opcode == 17 and len(inst) >= 2:
            capabilities.append(inst[1])
        elif opcode == 16 and len(inst) >= 6 and inst[2] == 17:
            local_size = [inst[3], inst[4], inst[5]]
        elif opcode == 331 and len(inst) >= 6 and inst[2] == 38:
            local_size_id = [inst[3], inst[4], inst[5]]
        elif opcode == 21 and len(inst) >= 4:
            type_scalar[inst[1]] = {"kind": "int", "bits": inst[2], "signed": inst[3]}
        elif opcode == 22 and len(inst) >= 3:
            type_scalar[inst[1]] = {"kind": "float", "bits": inst[2]}
        elif opcode == 23 and len(inst) >= 4:
            type_vector[inst[1]] = {
                "component_type": inst[2],
                "component_count": inst[3],
            }
        elif opcode == 25 and len(inst) >= 4:
            type_matrix[inst[1]] = {
                "column_type": inst[2],
                "column_count": inst[3],
            }
        elif opcode == 28 and len(inst) >= 4:
            type_array[inst[1]] = {
                "element_type": inst[2],
                "length_id": inst[3],
            }
        elif opcode == 29 and len(inst) >= 3:
            type_runtime_array[inst[1]] = {
                "element_type": inst[2],
            }
        elif opcode == 30 and len(inst) >= 2:
            type_struct[inst[1]] = {"member_types": inst[2:]}
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
        elif opcode == 43 and len(inst) >= 4:
            constants[inst[2]] = {
                "result_type": inst[1],
                "words": inst[3:],
            }
        elif opcode == 44 and len(inst) >= 4:
            constant_composites[inst[2]] = {
                "result_type": inst[1],
                "constituents": inst[3:],
            }
        elif opcode == 50 and len(inst) >= 4:
            spec_constants[inst[2]] = {
                "result_type": inst[1],
                "words": inst[3:],
            }
        elif opcode == 51 and len(inst) >= 4:
            spec_constant_composites[inst[2]] = {
                "result_type": inst[1],
                "constituents": inst[3:],
            }
        elif opcode == 52 and len(inst) >= 4:
            spec_constants[inst[2]] = {
                "result_type": inst[1],
                "opcode": inst[3],
                "operands": inst[4:],
            }
        elif opcode in (65, 66) and len(inst) >= 4:
            id_defs[inst[2]] = {
                "op": OP_NAMES.get(opcode, f"Op{opcode}"),
                "result_type": inst[1],
                "base_id": inst[3],
                "index_ids": inst[4:],
            }
            access_chains_raw.append(
                {
                    "word_index": _index,
                    "op": OP_NAMES.get(opcode, f"Op{opcode}"),
                    "result_type": inst[1],
                    "result_id": inst[2],
                    "base_id": inst[3],
                    "index_ids": inst[4:],
                }
            )
        elif opcode == 71 and len(inst) >= 3:
            target, decoration = inst[1], inst[2]
            name = DECORATION_NAMES.get(decoration, str(decoration))
            if decoration in (1, 6, 7, 11, 33, 34, 35) and len(inst) >= 4:
                decorations[target][name] = inst[3]
                if decoration == 11:
                    decorations[target]["BuiltInName"] = BUILTIN_NAMES.get(inst[3], str(inst[3]))
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
        elif opcode == 12 and len(inst) >= 6:
            set_name = ext_inst_imports_by_id.get(inst[3], str(inst[3]))
            ext_inst_name = GLSL_STD_450_NAMES.get(inst[4], str(inst[4])) if set_name == "GLSL.std.450" else str(inst[4])
            qualified_op = f"{set_name}.{ext_inst_name}"
            id_defs[inst[2]] = {
                "op": "OpExtInst",
                "result_type": inst[1],
                "ext_inst_set_id": inst[3],
                "ext_inst_set_name": set_name,
                "set_id": inst[3],
                "instruction": inst[4],
                "ext_inst_instruction": inst[4],
                "ext_inst_name": ext_inst_name,
                "qualified_op": qualified_op,
                "operands": inst[5:],
                "word_index": _index,
            }
            ext_inst_events_raw.append(
                {
                    "word_index": _index,
                    "result_id": inst[2],
                    "result_type": inst[1],
                    "ext_inst_set_id": inst[3],
                    "ext_inst_set_name": set_name,
                    "ext_inst_instruction": inst[4],
                    "ext_inst_name": ext_inst_name,
                    "qualified_op": qualified_op,
                    "operand_ids": inst[5:],
                }
            )
        elif 124 <= opcode <= 190 and len(inst) >= 4:
            id_defs[inst[2]] = {
                "op": OP_NAMES.get(opcode, f"Op{opcode}"),
                "result_type": inst[1],
                "operands": inst[3:],
                "word_index": _index,
            }
        elif opcode == 350 and len(inst) >= 6:
            group_operation = inst[4]
            id_defs[inst[2]] = {
                "op": "OpGroupNonUniformFAdd",
                "result_type": inst[1],
                "execution_scope_id": inst[3],
                "group_operation": group_operation,
                "group_operation_name": GROUP_OPERATION_NAMES.get(group_operation, str(group_operation)),
                "value_id": inst[5],
                "operands": inst[5:],
                "word_index": _index,
            }
            group_nonuniform_events_raw.append(
                {
                    "word_index": _index,
                    "op": "OpGroupNonUniformFAdd",
                    "result_id": inst[2],
                    "result_type": inst[1],
                    "execution_scope_id": inst[3],
                    "group_operation": group_operation,
                    "group_operation_name": GROUP_OPERATION_NAMES.get(group_operation, str(group_operation)),
                    "operand_ids": inst[5:],
                }
            )
        elif opcode in (80, 81, 82, 83, 84, 86) and len(inst) >= 4:
            id_defs[inst[2]] = {
                "op": OP_NAMES.get(opcode, f"Op{opcode}"),
                "result_type": inst[1],
                "operands": inst[3:],
                "word_index": _index,
            }
        elif opcode == 245 and len(inst) >= 4:
            id_defs[inst[2]] = {
                "op": "OpPhi",
                "result_type": inst[1],
                "operands": inst[3::2],
                "incoming_labels": inst[4::2],
                "word_index": _index,
            }

    member_offsets: dict[int, dict[int, int]] = defaultdict(dict)
    member_layout: dict[int, dict[int, dict]] = defaultdict(dict)
    for item in member_decorations:
        decoration = item.get("decoration")
        operands = item.get("operands") or []
        target = int(item["target"])
        member = int(item["member"])
        layout = member_layout[target].setdefault(member, {})
        if decoration == "Offset" and operands:
            member_offsets[target][member] = int(operands[0])
        if operands:
            layout[str(decoration)] = int(operands[0])
        else:
            layout[str(decoration)] = True

    def constant_u32(value_id: int) -> int | None:
        value = constants.get(value_id)
        if not value or len(value.get("words", [])) != 1:
            return None
        return int(value["words"][0])

    def spec_constant_default_u32(value_id: int) -> int | None:
        value = spec_constants.get(value_id)
        if not value or "opcode" in value or len(value.get("words", [])) != 1:
            return None
        return int(value["words"][0])

    def decode_barrier_event(raw: dict) -> dict:
        item = dict(raw)
        operands = list(raw.get("raw_operands") or [])
        if raw.get("opcode") == 224 and len(operands) >= 3:
            execution_scope_id, memory_scope_id, memory_semantics_id = operands[:3]
            execution_scope_value = constant_u32(execution_scope_id)
            memory_scope_value = constant_u32(memory_scope_id)
            memory_semantics_value = constant_u32(memory_semantics_id)
            item.update(
                {
                    "execution_scope_id": execution_scope_id,
                    "execution_scope_value": execution_scope_value,
                    "execution_scope_name": decode_scope_name(execution_scope_value),
                    "memory_scope_id": memory_scope_id,
                    "memory_scope_value": memory_scope_value,
                    "memory_scope_name": decode_scope_name(memory_scope_value),
                    "memory_semantics_id": memory_semantics_id,
                    "memory_semantics_value": memory_semantics_value,
                    "memory_semantics_names": decode_memory_semantics_names(memory_semantics_value),
                }
            )
        elif raw.get("opcode") == 225 and len(operands) >= 2:
            memory_scope_id, memory_semantics_id = operands[:2]
            memory_scope_value = constant_u32(memory_scope_id)
            memory_semantics_value = constant_u32(memory_semantics_id)
            item.update(
                {
                    "memory_scope_id": memory_scope_id,
                    "memory_scope_value": memory_scope_value,
                    "memory_scope_name": decode_scope_name(memory_scope_value),
                    "memory_semantics_id": memory_semantics_id,
                    "memory_semantics_value": memory_semantics_value,
                    "memory_semantics_names": decode_memory_semantics_names(memory_semantics_value),
                }
            )
        return item

    def describe_scalar_id(value_id: int) -> dict:
        item: dict = {
            "id": value_id,
            "name": names.get(value_id, ""),
        }
        const_value = constant_u32(value_id)
        if const_value is not None:
            item.update({"kind": "constant", "value_u32": const_value})
            return item
        spec_value = spec_constant_default_u32(value_id)
        if spec_value is not None:
            item.update({
                "kind": "spec_constant",
                "default_u32": spec_value,
                "spec_id": decorations.get(value_id, {}).get("SpecId"),
            })
            return item
        if value_id in spec_constants and "opcode" in spec_constants[value_id]:
            item.update({
                "kind": "spec_constant_op",
                "opcode": spec_constants[value_id].get("opcode"),
                "operands": spec_constants[value_id].get("operands", []),
                "spec_id": decorations.get(value_id, {}).get("SpecId"),
            })
            return item
        if value_id in constant_composites:
            item.update({
                "kind": "constant_composite",
                "constituents": [
                    describe_scalar_id(int(member_id))
                    for member_id in constant_composites[value_id].get("constituents", [])
                ],
            })
            return item
        if value_id in spec_constant_composites:
            item.update({
                "kind": "spec_constant_composite",
                "constituents": spec_constant_composites[value_id].get("constituents", []),
            })
            return item
        item["kind"] = "id"
        return item

    def describe_type(type_id: int, depth: int = 0) -> dict:
        if depth > 8:
            return {"id": type_id, "kind": "max-depth"}
        if type_id in type_scalar:
            return {"id": type_id, **type_scalar[type_id]}
        if type_id in type_pointer:
            pointee = type_pointer[type_id]["pointee_type"]
            return {
                "id": type_id,
                "kind": "pointer",
                "storage_class": type_pointer[type_id]["storage_class_name"],
                "pointee_type": pointee,
                "pointee": describe_type(pointee, depth + 1),
            }
        if type_id in type_vector:
            component = type_vector[type_id]["component_type"]
            return {
                "id": type_id,
                "kind": "vector",
                "component_type": component,
                "component_count": type_vector[type_id]["component_count"],
                "component": describe_type(component, depth + 1),
            }
        if type_id in type_matrix:
            column = type_matrix[type_id]["column_type"]
            return {
                "id": type_id,
                "kind": "matrix",
                "column_type": column,
                "column_count": type_matrix[type_id]["column_count"],
                "column": describe_type(column, depth + 1),
                "matrix_stride": decorations.get(type_id, {}).get("MatrixStride"),
            }
        if type_id in type_array:
            element = type_array[type_id]["element_type"]
            return {
                "id": type_id,
                "kind": "array",
                "element_type": element,
                "length_id": type_array[type_id]["length_id"],
                "length_u32": constant_u32(type_array[type_id]["length_id"]),
                "array_stride": decorations.get(type_id, {}).get("ArrayStride"),
                "element": describe_type(element, depth + 1),
            }
        if type_id in type_runtime_array:
            element = type_runtime_array[type_id]["element_type"]
            return {
                "id": type_id,
                "kind": "runtime_array",
                "element_type": element,
                "array_stride": decorations.get(type_id, {}).get("ArrayStride"),
                "element": describe_type(element, depth + 1),
            }
        if type_id in type_struct:
            return {
                "id": type_id,
                "kind": "struct",
                "member_count": len(type_struct[type_id]["member_types"]),
                "block": bool(decorations.get(type_id, {}).get("Block", False)),
                "buffer_block": bool(decorations.get(type_id, {}).get("BufferBlock", False)),
                "members": [
                    {
                        "index": index,
                        "name": member_names.get(type_id, {}).get(index, ""),
                        "type_id": member_type,
                        "offset": member_offsets.get(type_id, {}).get(index),
                        "layout": member_layout.get(type_id, {}).get(index, {}),
                        "type": describe_type(member_type, depth + 1),
                    }
                    for index, member_type in enumerate(type_struct[type_id]["member_types"])
                ],
            }
        return {"id": type_id, "kind": "unknown"}

    descriptor_variables = []
    bindings_seen: dict[tuple[int, int], list[int]] = defaultdict(list)
    for var_id, var in variable.items():
        dec = decorations.get(var_id, {})
        if "Binding" not in dec:
            continue
        descriptor_set = int(dec.get("DescriptorSet", 0))
        binding = int(dec["Binding"])
        pointer_type = var["result_type"]
        pointer = type_pointer.get(pointer_type, {})
        pointee_type = pointer.get("pointee_type")
        bindings_seen[(descriptor_set, binding)].append(var_id)
        descriptor_variables.append(
            {
                "id": var_id,
                "name": names.get(var_id, ""),
                "set": descriptor_set,
                "binding": binding,
                "storage_class": var["storage_class_name"],
                "pointer_type": pointer_type,
                "pointee_type": pointee_type,
                "pointee_layout": describe_type(pointee_type) if pointee_type is not None else None,
                "non_readable": bool(dec.get("NonReadable", False)),
                "non_writable": bool(dec.get("NonWritable", False)),
            }
        )

    push_constant_blocks = []
    for var_id, var in sorted(variable.items()):
        if var.get("storage_class") != 9:
            continue
        pointer = type_pointer.get(var["result_type"], {})
        struct_id = pointer.get("pointee_type")
        struct_info = type_struct.get(struct_id, {})
        members = []
        for index, member_type in enumerate(struct_info.get("member_types", [])):
            members.append(
                {
                    "index": index,
                    "name": member_names.get(struct_id, {}).get(index, ""),
                    "offset": member_offsets.get(struct_id, {}).get(index),
                    "type": describe_type(member_type),
                }
            )
        push_constant_blocks.append(
            {
                "variable_id": var_id,
                "name": names.get(var_id, ""),
                "pointer_type": var["result_type"],
                "struct_type": struct_id,
                "struct_name": names.get(struct_id, ""),
                "members": members,
            }
        )

    spec_constant_list = []
    for const_id, spec in sorted(spec_constants.items()):
        item = {
            "id": const_id,
            "name": names.get(const_id, ""),
            "spec_id": decorations.get(const_id, {}).get("SpecId"),
            "result_type": spec.get("result_type"),
        }
        if "opcode" in spec:
            item["opcode"] = spec.get("opcode")
            item["operands"] = spec.get("operands", [])
        else:
            item["words"] = spec.get("words", [])
        spec_constant_list.append(item)
    for const_id, composite in sorted(spec_constant_composites.items()):
        spec_constant_list.append(
            {
                "id": const_id,
                "name": names.get(const_id, ""),
                "kind": "spec_constant_composite",
                "result_type": composite.get("result_type"),
                "constituents": [
                    describe_scalar_id(int(member_id))
                    for member_id in composite.get("constituents", [])
                ],
            }
        )

    workgroup_size_builtin = None
    for value_id, dec in sorted(decorations.items()):
        if dec.get("BuiltInName") != "WorkgroupSize":
            continue
        if value_id in variable:
            var = variable[value_id]
            workgroup_size_builtin = {
                "kind": "variable",
                "id": value_id,
                "name": names.get(value_id, ""),
                "storage_class": var.get("storage_class_name"),
                "result_type": var.get("result_type"),
            }
            break
        if value_id in constant_composites:
            constituents = constant_composites[value_id].get("constituents", [])
            workgroup_size_builtin = {
                "kind": "constant_composite",
                "id": value_id,
                "name": names.get(value_id, ""),
                "components": [describe_scalar_id(int(member_id)) for member_id in constituents],
            }
            break
        if value_id in spec_constant_composites:
            constituents = spec_constant_composites[value_id].get("constituents", [])
            workgroup_size_builtin = {
                "kind": "spec_constant_composite",
                "id": value_id,
                "name": names.get(value_id, ""),
                "components": [describe_scalar_id(int(member_id)) for member_id in constituents],
            }
            break

    def workgroup_component_default_u32(component: object) -> int | None:
        if not isinstance(component, dict):
            return None
        if isinstance(component.get("value_u32"), int):
            return int(component["value_u32"])
        if isinstance(component.get("default_u32"), int):
            return int(component["default_u32"])
        return None

    def build_workgroup_execution_shape() -> dict:
        component_defaults = None
        component_kinds = []
        has_specialized_workgroup_builtin = False
        if isinstance(workgroup_size_builtin, dict):
            components = workgroup_size_builtin.get("components")
            if isinstance(components, list):
                defaults = [workgroup_component_default_u32(component) for component in components]
                if len(defaults) == 3 and all(isinstance(value, int) for value in defaults):
                    component_defaults = defaults
                component_kinds = [
                    component.get("kind") if isinstance(component, dict) else type(component).__name__
                    for component in components
                ]
                has_specialized_workgroup_builtin = any(
                    isinstance(component, dict) and component.get("kind") in {"spec_constant", "spec_constant_op", "spec_constant_composite"}
                    for component in components
                )
        literal_local_size = local_size if local_size != [0, 0, 0] else None
        literal_matches_workgroup_default = None
        if literal_local_size is not None and component_defaults is not None:
            literal_matches_workgroup_default = literal_local_size == component_defaults
        statically_consistent = literal_matches_workgroup_default
        if literal_local_size is None or component_defaults is None:
            statically_consistent = None
        return {
            "local_size": local_size,
            "local_size_id": local_size_id,
            "workgroup_size_builtin_kind": (
                workgroup_size_builtin.get("kind") if isinstance(workgroup_size_builtin, dict) else None
            ),
            "workgroup_size_default": component_defaults,
            "workgroup_size_component_kinds": component_kinds,
            "has_specialized_workgroup_builtin": has_specialized_workgroup_builtin,
            "literal_matches_workgroup_default": literal_matches_workgroup_default,
            "statically_consistent": statically_consistent,
        }

    workgroup_execution_shape = build_workgroup_execution_shape()

    descriptor_by_id = {int(item["id"]): item for item in descriptor_variables}
    push_constant_by_id = {int(item["variable_id"]): item for item in push_constant_blocks}

    def describe_base(base_id: int) -> dict:
        if base_id in descriptor_by_id:
            item = descriptor_by_id[base_id]
            return {
                "kind": "descriptor",
                "id": base_id,
                "name": names.get(base_id, ""),
                "set": item.get("set"),
                "binding": item.get("binding"),
                "storage_class": item.get("storage_class"),
                "non_readable": item.get("non_readable"),
                "non_writable": item.get("non_writable"),
            }
        if base_id in push_constant_by_id:
            item = push_constant_by_id[base_id]
            return {
                "kind": "push_constant",
                "id": base_id,
                "name": item.get("name", ""),
                "struct_type": item.get("struct_type"),
                "struct_name": item.get("struct_name", ""),
            }
        if base_id in variable:
            item = variable[base_id]
            dec = decorations.get(base_id, {})
            return {
                "kind": "variable",
                "id": base_id,
                "name": names.get(base_id, ""),
                "storage_class": item.get("storage_class_name"),
                "built_in": dec.get("BuiltInName"),
                "result_type": item.get("result_type"),
            }
        return {
            "kind": "id",
            "id": base_id,
            "name": names.get(base_id, ""),
        }

    access_chain_by_result: dict[int, dict] = {}
    access_chains = []
    for raw in access_chains_raw:
        base = describe_base(raw["base_id"])
        indices = []
        for index_id in raw["index_ids"]:
            indices.append(
                {
                    "id": index_id,
                    "name": names.get(index_id, ""),
                    "constant_u32": constant_u32(index_id),
                }
            )
        resolved = {
            **raw,
            "base": base,
            "indices": indices,
        }
        if base.get("kind") == "push_constant" and indices and indices[0].get("constant_u32") is not None:
            member_index = indices[0]["constant_u32"]
            members = push_constant_by_id.get(raw["base_id"], {}).get("members", [])
            if isinstance(member_index, int) and 0 <= member_index < len(members):
                resolved["push_member"] = members[member_index]
        access_chains.append(resolved)
        access_chain_by_result[int(raw["result_id"])] = resolved

    def pointer_origin(pointer_id: int) -> dict:
        if pointer_id in access_chain_by_result:
            chain = access_chain_by_result[pointer_id]
            origin = {
                "kind": "access_chain",
                "access_chain_result_id": pointer_id,
                "base": chain.get("base"),
                "indices": chain.get("indices", []),
            }
            if "push_member" in chain:
                origin["push_member"] = chain["push_member"]
            return origin
        return describe_base(pointer_id)

    def describe_id_expr(value_id: int, depth: int = 0, seen: set[int] | None = None) -> dict:
        if seen is None:
            seen = set()
        if depth > 6:
            return {"kind": "max-depth", "id": value_id}
        if value_id in seen:
            return {"kind": "cycle", "id": value_id}
        seen.add(value_id)
        const_value = constant_u32(value_id)
        if const_value is not None:
            return {"kind": "constant", "id": value_id, "value_u32": const_value}
        spec_value = spec_constant_default_u32(value_id)
        if spec_value is not None:
            return {
                "kind": "spec_constant",
                "id": value_id,
                "default_u32": spec_value,
                "spec_id": decorations.get(value_id, {}).get("SpecId"),
            }
        if value_id in variable:
            dec = decorations.get(value_id, {})
            return {
                "kind": "variable",
                "id": value_id,
                "name": names.get(value_id, ""),
                "storage_class": variable[value_id].get("storage_class_name"),
                "built_in": dec.get("BuiltInName"),
            }
        definition = id_defs.get(value_id)
        if not definition:
            return {"kind": "id", "id": value_id, "name": names.get(value_id, "")}
        op = definition.get("op")
        if op == "OpLoad":
            return {
                "kind": "load",
                "id": value_id,
                "result_type": definition.get("result_type"),
                "pointer_id": definition.get("pointer_id"),
                "word_index": definition.get("word_index"),
                "pointer": pointer_origin(int(definition.get("pointer_id"))),
            }
        if op in ("OpAccessChain", "OpInBoundsAccessChain"):
            origin = pointer_origin(value_id)
            return {
                "kind": "access_chain",
                "id": value_id,
                "base": origin.get("base") or describe_base(int(definition.get("base_id"))),
                "indices": [
                    describe_id_expr(int(index_id), depth + 1, set(seen))
                    for index_id in definition.get("index_ids", [])
                ],
                **({"push_member": origin["push_member"]} if isinstance(origin, dict) and "push_member" in origin else {}),
            }
        operands = definition.get("operands", [])
        result = {
            "kind": "op",
            "id": value_id,
            "op": op,
            "operands": [
                describe_id_expr(int(operand), depth + 1, set(seen))
                for operand in operands[:6]
            ],
            "truncated_operands": max(0, len(operands) - 6),
        }
        for key in (
            "qualified_op",
            "ext_inst_set_id",
            "ext_inst_set_name",
            "ext_inst_instruction",
            "ext_inst_name",
            "execution_scope_id",
            "group_operation",
            "group_operation_name",
            "value_id",
        ):
            if key in definition:
                result[key] = definition[key]
        if isinstance(definition.get("execution_scope_id"), int):
            scope_value = constant_u32(int(definition["execution_scope_id"]))
            result["execution_scope_value"] = scope_value
            result["execution_scope_name"] = decode_scope_name(scope_value)
        return result

    for chain in access_chains:
        for index in chain.get("indices", []):
            index["expr"] = describe_id_expr(int(index["id"]))

    load_events = [
        {
            **event,
            "pointer_origin": pointer_origin(event["pointer_id"]),
        }
        for event in load_events_raw
    ]
    store_events = [
        {
            **event,
            "pointer_origin": pointer_origin(event["pointer_id"]),
            "object_expr": describe_id_expr(int(event["object_id"])),
        }
        for event in store_events_raw
    ]

    barrier_events = [decode_barrier_event(raw) for raw in barrier_events_raw]
    ext_inst_events = list(ext_inst_events_raw)
    group_nonuniform_events = []
    for raw in group_nonuniform_events_raw:
        event = dict(raw)
        scope_value = constant_u32(int(raw["execution_scope_id"])) if isinstance(raw.get("execution_scope_id"), int) else None
        event["execution_scope_value"] = scope_value
        event["execution_scope_name"] = decode_scope_name(scope_value)
        group_nonuniform_events.append(event)

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
    ext_inst_imports = [
        {"id": import_id, "name": name}
        for import_id, name in sorted(ext_inst_imports_by_id.items())
    ]
    ext_inst_histogram = dict(Counter(event.get("qualified_op") for event in ext_inst_events if event.get("qualified_op")).most_common())
    group_nonuniform_histogram = dict(Counter(event.get("op") for event in group_nonuniform_events if event.get("op")).most_common())

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
    if workgroup_size_builtin and workgroup_size_builtin.get("kind") == "spec_constant_composite":
        risk_notes.append("declares BuiltIn WorkgroupSize through specialization constants; executor must reconcile literal LocalSize with specialized WorkgroupSize")
    if workgroup_execution_shape.get("statically_consistent") is False:
        risk_notes.append("literal LocalSize differs from BuiltIn WorkgroupSize default; executor must materialize or specialize WorkgroupSize consistently before replay")

    control_flow = summarize_cfg(words)

    def block_for_word_in_cfg(word_index: int) -> dict | None:
        for function in control_flow.get("functions", []):
            for ordinal, block in enumerate(function.get("blocks", [])):
                indices = list(block.get("instruction_word_indices") or [])
                if not indices:
                    continue
                start = int(block.get("word_index", min(indices)))
                end = max(indices + [start])
                if start <= word_index <= end:
                    return {
                        "function_id": function.get("id"),
                        "block_label": block.get("label"),
                        "block_ordinal": ordinal,
                        "block_word_index": block.get("word_index"),
                    }
        return None

    for barrier in barrier_events:
        word_index = barrier.get("word_index")
        if isinstance(word_index, int):
            barrier["block"] = block_for_word_in_cfg(word_index) or {}

    report = {
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
        "workgroup_size_builtin": workgroup_size_builtin,
        "workgroup_execution_shape": workgroup_execution_shape,
        "entry_points": entry_points,
        "capabilities": capability_names,
        "ext_inst_imports": ext_inst_imports,
        "ext_inst_events": ext_inst_events,
        "ext_inst_histogram": ext_inst_histogram,
        "group_nonuniform_events": group_nonuniform_events,
        "group_nonuniform_histogram": group_nonuniform_histogram,
        "descriptor_variables": descriptor_variables,
        "push_constant_blocks": push_constant_blocks,
        "spec_constants": spec_constant_list,
        "access_chains": access_chains,
        "load_events": load_events,
        "store_events": store_events,
        "barrier_events": barrier_events,
        "duplicate_bindings": duplicate_bindings,
        "workgroup_variable_count": workgroup_variable_count,
        "control_flow": control_flow,
        "op_histogram": op_hist_named,
        "risk_notes": risk_notes,
    }
    report["q6_probe_targets"] = build_q6_probe_targets(report)
    return report


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
    parser.add_argument("--probe-plan-out", type=Path, help="write a probe manifest for a single SPIR-V module")
    parser.add_argument("--probe-range", help="candidate range for the probe manifest, formatted start:end")
    parser.add_argument("--disassemble-dir", type=Path, help="write spirv-dis output into this directory")
    args = parser.parse_args()
    if args.probe_plan_out and len(args.spirv) != 1:
        parser.error("--probe-plan-out requires exactly one SPIR-V input")
    probe_range = None
    if args.probe_range:
        try:
            start_text, end_text = args.probe_range.split(":", 1)
            probe_range = (int(start_text), int(end_text))
        except Exception as exc:
            parser.error(f"--probe-range must be start:end: {exc}")

    reports = []
    for path in args.spirv:
        report = analyze_spirv(path)
        asm = maybe_disassemble(path, args.disassemble_dir)
        if asm:
            report["disassembly_path"] = asm
        reports.append(report)
    if args.probe_plan_out:
        manifest = build_probe_manifest(reports[0], args.spirv[0], probe_range)
        args.probe_plan_out.parent.mkdir(parents=True, exist_ok=True)
        args.probe_plan_out.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

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

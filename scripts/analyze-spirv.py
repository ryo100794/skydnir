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
    63: "OpCopyMemory",
    64: "OpCopyMemorySized",
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
                "successors": [],
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
            "successors": block["successors"],
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
            "candidate_range": selected_range,
            "selected_candidate_count": len(selected_candidates),
            "selected_candidates": selected_candidates,
            "bisect_rounds": probe_plan.get("bisect_rounds", []),
            "candidate_ranges": candidate_ranges,
        },
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
            "target_env": "vulkan1.1",
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
        elif opcode in (63, 64):
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
            type_scalar[inst[1]] = {"kind": "int", "bits": inst[2], "signed": inst[3]}
        elif opcode == 22 and len(inst) >= 3:
            type_scalar[inst[1]] = {"kind": "float", "bits": inst[2]}
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
        "control_flow": summarize_cfg(words),
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

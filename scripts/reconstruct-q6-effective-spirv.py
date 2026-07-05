#!/usr/bin/env python3
"""Reconstruct the executor-effective Q6 SPIR-V module from static evidence.

This mirrors the Skydnir Android executor's narrow Q6 compatibility sequence:

1. patch literal LocalSize from the WorkgroupSize specialization,
2. materialize supported specialization constants,
3. lower exact Q6 duplicate-view storage16 ushort loads to storage8 byte loads,
4. insert the Q6 final-store pre-barrier compatibility guard,
5. apply strict duplicate descriptor binding normalization.

It is a static/offline evidence tool.  It does not run ADB, llama.cpp, or a
Vulkan driver, and it does not change shader semantics beyond the executor's
already-recorded compatibility lowerings.
"""

from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path
from typing import Any, Iterable

FNV1A64_OFFSET = 1469598103934665603
FNV1A64_PRIME = 1099511628211
SPIRV_MAGIC = 0x07230203
MAX_BINDINGS = 64


def fnv1a64(data: bytes) -> int:
    value = FNV1A64_OFFSET
    for byte in data:
        value ^= byte
        value = (value * FNV1A64_PRIME) & 0xFFFFFFFFFFFFFFFF
    return value


def read_words(path: Path) -> list[int]:
    data = path.read_bytes()
    if len(data) < 20 or len(data) % 4 != 0:
        raise ValueError(f"{path} is not a complete SPIR-V word stream")
    words = list(struct.unpack(f"<{len(data) // 4}I", data))
    if words[0] != SPIRV_MAGIC:
        raise ValueError(f"{path} has invalid SPIR-V magic 0x{words[0]:08x}")
    return words


def words_to_bytes(words: list[int]) -> bytes:
    return struct.pack(f"<{len(words)}I", *words)


def hash_words(words: list[int]) -> str:
    return f"0x{fnv1a64(words_to_bytes(words)):016x}"


def iter_instructions(words: list[int]) -> Iterable[tuple[int, int, int, list[int]]]:
    index = 5
    while index < len(words):
        inst = words[index]
        word_count = inst >> 16
        opcode = inst & 0xFFFF
        if word_count == 0 or index + word_count > len(words):
            raise ValueError(f"truncated SPIR-V instruction at word {index}")
        yield index, opcode, word_count, words[index:index + word_count]
        index += word_count


def _spec_value(entries: list[dict[str, Any]], constant_id: int | None, default: int) -> int:
    if constant_id is None:
        return default & 0xFFFFFFFF
    for entry in entries:
        if int(entry.get("constant_id", -1)) == constant_id:
            return int(entry.get("value_u64", default)) & 0xFFFFFFFF
    return default & 0xFFFFFFFF


def _specialization_entry_value(
    entries: list[dict[str, Any]],
    constant_id: int | None,
    default: int,
) -> tuple[int, bool]:
    if constant_id is None:
        return default & 0xFFFFFFFF, False
    for entry in entries:
        if int(entry.get("constant_id", -1)) == constant_id:
            return int(entry.get("value_u64", default)) & 0xFFFFFFFF, True
    return default & 0xFFFFFFFF, False


def _spec_id_decorations(words: list[int]) -> dict[int, int]:
    spec_ids: dict[int, int] = {}
    for _, opcode, word_count, inst in iter_instructions(words):
        if opcode == 71 and word_count >= 4 and inst[2] == 1:  # OpDecorate SpecId
            spec_ids[inst[1]] = inst[3]
    return spec_ids


def _workgroup_size_composite_members(words: list[int]) -> list[int] | None:
    workgroup_ids: set[int] = set()
    for _, opcode, word_count, inst in iter_instructions(words):
        if opcode == 71 and word_count >= 4 and inst[2] == 11 and inst[3] == 25:
            workgroup_ids.add(inst[1])  # BuiltIn WorkgroupSize
    for _, opcode, word_count, inst in iter_instructions(words):
        if opcode == 51 and word_count >= 6 and inst[2] in workgroup_ids:
            return list(inst[3:6])
    return None


def patch_literal_local_size_from_spec(
    words: list[int],
    specialization_entries: list[dict[str, Any]],
) -> tuple[list[int], dict[str, Any]]:
    local_size = [0, 0, 0]
    local_size_id = [0, 0, 0]
    literal_local_size_word_index: int | None = None
    local_size_id_count = 0
    literal_local_size_count = 0
    for index, opcode, word_count, inst in iter_instructions(words):
        if opcode == 16 and word_count >= 6 and inst[2] == 17:  # OpExecutionMode LocalSize
            literal_local_size_count += 1
            local_size = list(inst[3:6])
            literal_local_size_word_index = index
        elif opcode == 331 and word_count >= 6 and inst[2] == 38:  # OpExecutionModeId LocalSizeId
            local_size_id_count += 1
            local_size_id = list(inst[3:6])

    spec_ids = _spec_id_decorations(words)
    members = _workgroup_size_composite_members(words)
    resolved = list(local_size)
    found_local_size_spec = False
    if members:
        for dim, member_id in enumerate(members[:3]):
            value, found = _specialization_entry_value(
                specialization_entries,
                spec_ids.get(member_id),
                resolved[dim] or 1,
            )
            if found:
                resolved[dim] = value
                found_local_size_spec = True

    invocation_count = resolved[0] * resolved[1] * resolved[2]
    valid_resolved_local_size = (
        found_local_size_spec
        and all(1 <= value <= 1024 for value in resolved)
        and 1 < invocation_count <= 1024
    )
    eligible = (
        literal_local_size_count == 1
        and local_size_id_count == 0
        and local_size == [1, 1, 1]
        and local_size_id == [0, 0, 0]
        and resolved != local_size
        and valid_resolved_local_size
        and literal_local_size_word_index is not None
    )
    if not eligible:
        return list(words), {
            "phase": "local-size-legalized",
            "changed": False,
            "local_size": local_size,
            "resolved": resolved,
            "reason": "not-eligible",
        }

    out = list(words)
    out[literal_local_size_word_index + 3:literal_local_size_word_index + 6] = resolved
    return out, {
        "phase": "local-size-legalized",
        "changed": True,
        "local_size": local_size,
        "resolved": resolved,
    }


def materialize_specialization_constants(
    words: list[int],
    specialization_entries: list[dict[str, Any]],
) -> tuple[list[int], dict[str, Any]]:
    bound = words[3]
    spec_ids = [0] * bound
    has_spec_id = [False] * bound
    skip = [False] * bound
    workgroup_size_id = [False] * bound
    scalars: dict[int, int] = {}
    composites: dict[int, list[int]] = {}

    local_size = [0, 0, 0]
    local_size_id = [0, 0, 0]
    for _, opcode, word_count, inst in iter_instructions(words):
        if opcode == 16 and word_count >= 6 and inst[2] == 17:
            local_size = list(inst[3:6])
        elif opcode == 331 and word_count >= 6 and inst[2] == 38:
            local_size_id = list(inst[3:6])
        elif opcode == 71 and word_count >= 4 and inst[1] < bound:
            if inst[2] == 1:
                has_spec_id[inst[1]] = True
                spec_ids[inst[1]] = inst[3]
            elif inst[2] == 11 and inst[3] == 25:
                workgroup_size_id[inst[1]] = True

    resolved = list(local_size)
    for _, opcode, word_count, inst in iter_instructions(words):
        if opcode == 51 and word_count >= 6 and inst[2] < bound and workgroup_size_id[inst[2]]:
            for dim, member_id in enumerate(inst[3:6]):
                if member_id < bound and has_spec_id[member_id]:
                    resolved[dim] = _spec_value(specialization_entries, spec_ids[member_id], resolved[dim] or 1)

    preserve_workgroup_size_subtree = local_size_id != [0, 0, 0] or local_size != resolved
    if preserve_workgroup_size_subtree:
        for object_id, is_workgroup_size in enumerate(workgroup_size_id):
            if is_workgroup_size:
                skip[object_id] = True
        for _, opcode, word_count, inst in iter_instructions(words):
            if opcode == 51 and word_count >= 3 and inst[2] < bound and workgroup_size_id[inst[2]]:
                for operand in inst[3:]:
                    if operand < bound:
                        skip[operand] = True

    changed = True
    while changed:
        changed = False
        for _, opcode, word_count, inst in iter_instructions(words):
            if opcode == 52 and word_count >= 5 and inst[2] < bound and not skip[inst[2]]:
                if any(operand < bound and skip[operand] for operand in inst[4:]):
                    skip[inst[2]] = True
                    changed = True

    out = words[:5]
    changed = False
    counts = {
        "spec_constants_folded": 0,
        "spec_composites_folded": 0,
        "spec_ops_folded": 0,
        "preserve_workgroup_size_spec_subtree": preserve_workgroup_size_subtree,
        "pre_local_size": local_size,
        "resolved_local_size": resolved,
    }
    for _, opcode, word_count, inst in iter_instructions(words):
        result_id = inst[2] if word_count >= 3 else None
        if opcode == 71 and word_count >= 4 and inst[2] == 1 and inst[1] < bound and not skip[inst[1]]:
            changed = True
            continue
        if opcode == 43 and word_count >= 4 and result_id is not None and result_id < bound:
            scalars[result_id] = inst[3]
        elif opcode == 44 and word_count >= 3 and result_id is not None and result_id < bound:
            values = []
            for operand in inst[3:]:
                if operand not in scalars:
                    values = []
                    break
                values.append(scalars[operand])
            if values:
                composites[result_id] = values[:4]
        elif opcode == 50 and word_count >= 4 and result_id is not None and result_id < bound and not skip[result_id]:
            value = inst[3]
            if has_spec_id[result_id]:
                value = _spec_value(specialization_entries, spec_ids[result_id], value)
            out += [(4 << 16) | 43, inst[1], result_id, value]
            scalars[result_id] = value
            changed = True
            counts["spec_constants_folded"] += 1
            continue
        elif opcode == 51 and word_count >= 3 and result_id is not None and result_id < bound and not skip[result_id]:
            out += [(word_count << 16) | 44] + inst[1:]
            values = []
            for operand in inst[3:]:
                if operand >= bound or skip[operand] or operand not in scalars:
                    values = []
                    break
                values.append(scalars[operand])
            if values:
                composites[result_id] = values[:4]
            changed = True
            counts["spec_composites_folded"] += 1
            continue
        elif opcode == 52 and word_count >= 5 and result_id is not None and result_id < bound:
            uses_skipped = skip[result_id] or any(operand < bound and skip[operand] for operand in inst[4:])
            if not uses_skipped:
                spec_op = inst[3]
                folded = False
                value = 0
                if spec_op == 134 and word_count == 6:  # OpUDiv
                    left, right = inst[4], inst[5]
                    if left in scalars and right in scalars and scalars[right] != 0:
                        value = scalars[left] // scalars[right]
                        folded = True
                elif spec_op == 81 and word_count == 6:  # OpCompositeExtract
                    composite, index = inst[4], inst[5]
                    if composite in composites and index < len(composites[composite]):
                        value = composites[composite][index]
                        folded = True
                if not folded:
                    raise ValueError(f"unsupported OpSpecConstantOp {spec_op} at result id {result_id}")
                out += [(4 << 16) | 43, inst[1], result_id, value]
                scalars[result_id] = value
                changed = True
                counts["spec_ops_folded"] += 1
                continue
        out += inst

    if not changed or len(out) > len(words):
        return list(words), {"phase": "specialization-materialized", "changed": False, **counts}
    return out, {"phase": "specialization-materialized", "changed": True, **counts}


def _find_q6k_duplicate_binding0_views(words: list[int]) -> dict[str, int] | None:
    OP_TYPE_INT = 21
    OP_TYPE_ARRAY = 28
    OP_TYPE_RUNTIME_ARRAY = 29
    OP_TYPE_STRUCT = 30
    OP_TYPE_POINTER = 32
    OP_VARIABLE = 59
    OP_LOAD = 61
    OP_ACCESS_CHAIN = 65
    OP_IN_BOUNDS_ACCESS_CHAIN = 66
    OP_DECORATE = 71
    STORAGE_CLASS_STORAGE_BUFFER = 12
    DECORATION_BINDING = 33
    DECORATION_DESCRIPTOR_SET = 34
    if len(words) < 5 or words[0] != SPIRV_MAGIC:
        return None
    bound = words[3]
    if bound <= 0 or bound > 65536:
        return None
    is_uint8_type = [False] * bound
    is_any_int8_type = [False] * bound
    is_uint16_type = [False] * bound
    binding_by_id = [-1] * bound
    set_by_id = [-1] * bound
    pointer_storage_by_id = [-1] * bound
    pointer_pointee_by_id = [-1] * bound
    variable_storage_by_id = [-1] * bound
    variable_pointer_type_by_id = [-1] * bound
    array_element_by_id = [-1] * bound
    runtime_array_element_by_id = [-1] * bound
    struct_member0_by_id = [-1] * bound
    struct_member1_by_id = [-1] * bound
    pointer_load_use_count = [0] * bound
    for _, opcode, word_count, inst in iter_instructions(words):
        if opcode == OP_TYPE_INT and word_count >= 4 and inst[1] < bound:
            if inst[2] == 8:
                is_any_int8_type[inst[1]] = True
                if inst[3] == 0:
                    is_uint8_type[inst[1]] = True
            elif inst[2] == 16 and inst[3] == 0:
                is_uint16_type[inst[1]] = True
        elif opcode == OP_TYPE_ARRAY and word_count >= 4 and inst[1] < bound:
            array_element_by_id[inst[1]] = inst[2] if inst[2] < bound else -1
        elif opcode == OP_TYPE_RUNTIME_ARRAY and word_count >= 3 and inst[1] < bound:
            runtime_array_element_by_id[inst[1]] = inst[2] if inst[2] < bound else -1
        elif opcode == OP_TYPE_STRUCT and word_count >= 3 and inst[1] < bound:
            struct_member0_by_id[inst[1]] = inst[2] if inst[2] < bound else -1
            if word_count >= 4:
                struct_member1_by_id[inst[1]] = inst[3] if inst[3] < bound else -1
        elif opcode == OP_DECORATE and word_count >= 4 and inst[1] < bound:
            if inst[2] == DECORATION_BINDING:
                binding_by_id[inst[1]] = inst[3]
            elif inst[2] == DECORATION_DESCRIPTOR_SET:
                set_by_id[inst[1]] = inst[3]
        elif opcode == OP_TYPE_POINTER and word_count >= 4 and inst[1] < bound:
            pointer_storage_by_id[inst[1]] = inst[2]
            pointer_pointee_by_id[inst[1]] = inst[3] if inst[3] < bound else -1
        elif opcode == OP_VARIABLE and word_count >= 4 and inst[2] < bound:
            result_type = inst[1]
            if result_type < bound:
                variable_pointer_type_by_id[inst[2]] = result_type
                variable_storage_by_id[inst[2]] = pointer_storage_by_id[result_type]
        elif opcode == OP_LOAD and word_count >= 4 and inst[3] < bound:
            pointer_load_use_count[inst[3]] += 1
    binding0_vars = [
        obj_id for obj_id in range(bound)
        if set_by_id[obj_id] == 0
        and binding_by_id[obj_id] == 0
        and variable_storage_by_id[obj_id] == STORAGE_CLASS_STORAGE_BUFFER
    ]
    if len(binding0_vars) != 2:
        return None
    uint8_types = [obj_id for obj_id, is_type in enumerate(is_uint8_type) if is_type]
    uint16_types = [obj_id for obj_id, is_type in enumerate(is_uint16_type) if is_type]
    if len(uint8_types) != 1 or len(uint16_types) != 1:
        return None
    uint8_type = uint8_types[0]
    uint16_type = uint16_types[0]

    def first_two_element_types(var_id: int) -> tuple[int, int] | None:
        ptr_type = variable_pointer_type_by_id[var_id]
        if not (0 <= ptr_type < bound):
            return None
        wrapper_type = pointer_pointee_by_id[ptr_type]
        if not (0 <= wrapper_type < bound):
            return None
        runtime_array = struct_member0_by_id[wrapper_type]
        if not (0 <= runtime_array < bound):
            return None
        element_struct = runtime_array_element_by_id[runtime_array]
        if not (0 <= element_struct < bound):
            return None
        member0 = struct_member0_by_id[element_struct]
        member1 = struct_member1_by_id[element_struct]
        if not (0 <= member0 < bound and 0 <= member1 < bound):
            return None
        elem0 = array_element_by_id[member0]
        elem1 = array_element_by_id[member1]
        if not (0 <= elem0 < bound and 0 <= elem1 < bound):
            return None
        return elem0, elem1

    layout_byte_candidates = []
    layout_ushort_candidates = []
    for idx, var_id in enumerate(binding0_vars):
        elems = first_two_element_types(var_id)
        if elems == (uint8_type, uint8_type):
            layout_byte_candidates.append(idx)
        if elems == (uint16_type, uint16_type):
            layout_ushort_candidates.append(idx)
    if len(layout_byte_candidates) != 1 or len(layout_ushort_candidates) != 1:
        return None
    byte_var_index = layout_byte_candidates[0]
    ushort_var_index = layout_ushort_candidates[0]
    if byte_var_index == ushort_var_index:
        return None

    ushort_access_count = [0, 0]
    ptr_byte_type = 0
    for _, opcode, word_count, inst in iter_instructions(words):
        if opcode not in (OP_ACCESS_CHAIN, OP_IN_BOUNDS_ACCESS_CHAIN) or word_count < 4:
            continue
        result_type, result_id, base = inst[1], inst[2], inst[3]
        if result_type >= bound or result_id >= bound:
            continue
        try:
            var_index = binding0_vars.index(base)
        except ValueError:
            continue
        if pointer_storage_by_id[result_type] != STORAGE_CLASS_STORAGE_BUFFER:
            continue
        if pointer_load_use_count[result_id] <= 0:
            continue
        pointee = pointer_pointee_by_id[result_type]
        if var_index == ushort_var_index and 0 <= pointee < bound and is_uint16_type[pointee]:
            ushort_access_count[var_index] += 1
        if var_index == byte_var_index and 0 <= pointee < bound and is_uint8_type[pointee]:
            ptr_byte_type = result_type
    return {
        "storage8_var_id": binding0_vars[byte_var_index],
        "storage16_var_id": binding0_vars[ushort_var_index],
        "byte_type_id": uint8_type,
        "ptr_byte_type_id": ptr_byte_type,
    }


def _spirv_count_id_uses(words: list[int], target_id: int, defining_instruction_index: int) -> int:
    count = 0
    for index, _, word_count, inst in iter_instructions(words):
        for operand_index, operand in enumerate(inst[1:], start=1):
            if index == defining_instruction_index and operand_index == 2:
                continue
            if operand == target_id:
                count += 1
    return count


def lower_q6k_storage16_loads_to_storage8(words: list[int]) -> tuple[list[int], dict[str, Any]]:
    """Mirror the executor's structural Q6 storage16 duplicate-view lowering."""
    OP_TYPE_INT = 21
    OP_TYPE_POINTER = 32
    OP_CONSTANT = 43
    OP_ACCESS_CHAIN = 65
    OP_LOAD = 61
    OP_U_CONVERT = 113
    OP_I_ADD = 128
    OP_I_MUL = 132
    OP_SHIFT_LEFT_LOGICAL = 196
    OP_BITWISE_OR = 197
    STORAGE_CLASS_STORAGE_BUFFER = 12
    Q6_STORAGE16_EXPECTED_LOADS = 24

    if len(words) < 5 or words[0] != SPIRV_MAGIC:
        return list(words), {"phase": "q6-storage16-loads-lowered", "changed": False, "reason": "invalid-spv"}
    bound = words[3]
    if bound <= 0 or bound > 65536:
        return list(words), {"phase": "q6-storage16-loads-lowered", "changed": False, "reason": "id-bound-out-of-range"}
    q6_shape = _find_q6k_duplicate_binding0_views(words)
    if not q6_shape:
        return list(words), {"phase": "q6-storage16-loads-lowered", "changed": False, "reason": "q6-duplicate-view-topology-not-found"}
    q6_storage8_var_id = q6_shape["storage8_var_id"]
    q6_storage16_var_id = q6_shape["storage16_var_id"]
    structural_byte_type = q6_shape["byte_type_id"]
    structural_ptr_byte_type = q6_shape["ptr_byte_type_id"]

    uint_type = uchar_type = ushort_type = 0
    ptr_ushort_type = ptr_uchar_type = 0
    uint_1 = uint_2 = uint_8 = 0
    int32_type_ids: set[int] = set()
    constant_value_by_id: dict[int, int] = {}
    uchar_type_end: int | None = None
    first_function = 0

    for index, opcode, word_count, inst in iter_instructions(words):
        if opcode == 54:  # OpFunction
            first_function = index
            break
        if opcode == OP_TYPE_INT and word_count >= 4:
            if inst[2] == 32:
                int32_type_ids.add(inst[1])
            if inst[2] == 32 and inst[3] == 0:
                uint_type = inst[1]
            elif inst[1] == structural_byte_type and inst[2] == 8:
                uchar_type = inst[1]
                uchar_type_end = index + word_count
            elif inst[2] == 16 and inst[3] == 0:
                ushort_type = inst[1]
        elif opcode == OP_TYPE_POINTER and word_count >= 4 and inst[2] == STORAGE_CLASS_STORAGE_BUFFER:
            if ushort_type and inst[3] == ushort_type:
                ptr_ushort_type = inst[1]
            if inst[1] == structural_ptr_byte_type:
                ptr_uchar_type = inst[1]
        elif opcode == OP_CONSTANT and word_count == 4:
            if inst[1] in int32_type_ids:
                constant_value_by_id[inst[2]] = inst[3]
            if uint_type and inst[1] == uint_type:
                if inst[3] == 1:
                    uint_1 = inst[2]
                elif inst[3] == 2:
                    uint_2 = inst[2]
                elif inst[3] == 8:
                    uint_8 = inst[2]

    if not all([first_function, uint_type, uchar_type, ushort_type, ptr_ushort_type, uint_1, uint_2, uint_8]) or uchar_type_end is None:
        return list(words), {"phase": "q6-storage16-loads-lowered", "changed": False, "reason": "missing-required-types-or-constants"}

    pattern_count = 0
    for index, opcode, word_count, inst in iter_instructions(words):
        if opcode != OP_ACCESS_CHAIN or word_count != 8:
            continue
        if inst[1] != ptr_ushort_type or inst[3] != q6_storage16_var_id:
            continue
        member = inst[6]
        if constant_value_by_id.get(member) not in (0, 1):
            continue
        if _spirv_count_id_uses(words, inst[2], index) != 1:
            continue
        load_i = index + word_count
        if load_i >= len(words):
            continue
        load_inst_word = words[load_i]
        load_wc = load_inst_word >> 16
        load_opcode = load_inst_word & 0xFFFF
        load_inst = words[load_i:load_i + load_wc]
        if load_wc == 4 and load_i + load_wc <= len(words) and load_opcode == OP_LOAD and load_inst[1] == ushort_type and load_inst[3] == inst[2]:
            pattern_count += 1
    if pattern_count != Q6_STORAGE16_EXPECTED_LOADS:
        return list(words), {"phase": "q6-storage16-loads-lowered", "changed": False, "pattern_count": pattern_count, "expected_count": Q6_STORAGE16_EXPECTED_LOADS}

    add_ptr_uchar_type = ptr_uchar_type == 0
    new_ptr_uchar_type = bound if add_ptr_uchar_type else ptr_uchar_type
    next_id = bound + (1 if add_ptr_uchar_type else 0)
    new_bound = next_id + pattern_count * 10
    if new_bound <= bound or new_bound > 65536:
        return list(words), {"phase": "q6-storage16-loads-lowered", "changed": False, "reason": "new-bound-out-of-range", "pattern_count": pattern_count}

    out = words[:5]
    lowered = 0
    i = 5
    while i < len(words):
        inst_word = words[i]
        word_count = inst_word >> 16
        opcode = inst_word & 0xFFFF
        if word_count == 0 or i + word_count > len(words):
            raise ValueError(f"truncated SPIR-V instruction at word {i}")
        inst = words[i:i + word_count]
        if add_ptr_uchar_type and i == uchar_type_end:
            out += [(4 << 16) | OP_TYPE_POINTER, new_ptr_uchar_type, STORAGE_CLASS_STORAGE_BUFFER, uchar_type]
        if opcode == OP_ACCESS_CHAIN and word_count == 8 and inst[1] == ptr_ushort_type and inst[3] == q6_storage16_var_id:
            member = inst[6]
            load_i = i + word_count
            load_inst_word = words[load_i] if load_i < len(words) else 0
            load_wc = load_inst_word >> 16
            load_opcode = load_inst_word & 0xFFFF
            load_inst = words[load_i:load_i + load_wc]
            if (constant_value_by_id.get(member) in (0, 1)
                    and _spirv_count_id_uses(words, inst[2], i) == 1
                    and load_wc == 4
                    and load_i + load_wc <= len(words)
                    and load_opcode == OP_LOAD
                    and load_inst[1] == ushort_type
                    and load_inst[3] == inst[2]):
                index0, block, member, ushort_index = inst[4], inst[5], inst[6], inst[7]
                load_result = load_inst[2]
                b0_idx = next_id; next_id += 1
                b0_ptr = next_id; next_id += 1
                b0_u8 = next_id; next_id += 1
                b0_u32 = next_id; next_id += 1
                b1_idx = next_id; next_id += 1
                b1_ptr = next_id; next_id += 1
                b1_u8 = next_id; next_id += 1
                b1_u32 = next_id; next_id += 1
                hi32 = next_id; next_id += 1
                combined32 = next_id; next_id += 1
                out += [
                    (5 << 16) | OP_I_MUL, uint_type, b0_idx, ushort_index, uint_2,
                    (8 << 16) | OP_ACCESS_CHAIN, new_ptr_uchar_type, b0_ptr, q6_storage8_var_id, index0, block, member, b0_idx,
                    (4 << 16) | OP_LOAD, uchar_type, b0_u8, b0_ptr,
                    (4 << 16) | OP_U_CONVERT, uint_type, b0_u32, b0_u8,
                    (5 << 16) | OP_I_ADD, uint_type, b1_idx, b0_idx, uint_1,
                    (8 << 16) | OP_ACCESS_CHAIN, new_ptr_uchar_type, b1_ptr, q6_storage8_var_id, index0, block, member, b1_idx,
                    (4 << 16) | OP_LOAD, uchar_type, b1_u8, b1_ptr,
                    (4 << 16) | OP_U_CONVERT, uint_type, b1_u32, b1_u8,
                    (5 << 16) | OP_SHIFT_LEFT_LOGICAL, uint_type, hi32, b1_u32, uint_8,
                    (5 << 16) | OP_BITWISE_OR, uint_type, combined32, b0_u32, hi32,
                    (4 << 16) | OP_U_CONVERT, ushort_type, load_result, combined32,
                ]
                lowered += 1
                i = load_i + load_wc
                continue
        out += inst
        i += word_count

    if lowered != pattern_count or next_id != new_bound:
        raise ValueError("internal Q6 storage16 lowering accounting mismatch")
    out[3] = new_bound
    return out, {
        "phase": "q6-storage16-loads-lowered",
        "changed": True,
        "structural": True,
        "lowered_count": lowered,
        "pattern_count": pattern_count,
        "storage8_var_id": q6_storage8_var_id,
        "storage16_var_id": q6_storage16_var_id,
        "added_ptr_uchar_type": add_ptr_uchar_type,
    }


def lower_q6k_u32_to_u8vec4_bitcasts(words: list[int]) -> tuple[list[int], dict[str, Any]]:
    """Mirror executor Q6 scalar-u32 to u8vec4 bitcast legalization."""
    OP_TYPE_INT = 21
    OP_TYPE_VECTOR = 23
    OP_CONSTANT = 43
    OP_BITCAST = 124
    OP_U_CONVERT = 113
    OP_SHIFT_RIGHT_LOGICAL = 194
    OP_COMPOSITE_CONSTRUCT = 80
    Q6_U32_TO_U8VEC4_EXPECTED_BITCASTS = 16
    if len(words) < 5 or words[0] != SPIRV_MAGIC:
        return list(words), {"phase": "q6-u32-to-u8vec4-bitcasts-lowered", "changed": False, "reason": "invalid-spv"}
    bound = words[3]
    if bound <= 0 or bound > 65536:
        return list(words), {"phase": "q6-u32-to-u8vec4-bitcasts-lowered", "changed": False, "reason": "id-bound-out-of-range"}
    q6_shape = _find_q6k_duplicate_binding0_views(words)
    if not q6_shape:
        return list(words), {"phase": "q6-u32-to-u8vec4-bitcasts-lowered", "changed": False, "reason": "q6-duplicate-view-topology-not-found"}
    structural_byte_type = q6_shape["byte_type_id"]
    uint_type = uchar_type = uchar4_type = 0
    uint_8 = uint_16 = uint_24 = 0
    for _, opcode, word_count, inst in iter_instructions(words):
        if opcode == OP_TYPE_INT and word_count >= 4:
            if inst[2] == 32 and inst[3] == 0:
                uint_type = inst[1]
            elif inst[1] == structural_byte_type and inst[2] == 8 and inst[3] == 0:
                uchar_type = inst[1]
        elif opcode == OP_TYPE_VECTOR and word_count >= 4 and uchar_type and inst[2] == uchar_type and inst[3] == 4:
            uchar4_type = inst[1]
        elif opcode == OP_CONSTANT and word_count >= 4 and uint_type and inst[1] == uint_type:
            if inst[3] == 8:
                uint_8 = inst[2]
            elif inst[3] == 16:
                uint_16 = inst[2]
            elif inst[3] == 24:
                uint_24 = inst[2]
    if not all([uint_type, uchar_type, uchar4_type, uint_8, uint_16, uint_24]):
        return list(words), {"phase": "q6-u32-to-u8vec4-bitcasts-lowered", "changed": False, "reason": "missing-required-types-or-constants"}
    type_by_id = [0] * bound
    for _, _, word_count, inst in iter_instructions(words):
        if word_count >= 3 and inst[2] < bound and inst[1] in (uint_type, uchar_type, uchar4_type):
            type_by_id[inst[2]] = inst[1]
    pattern_count = sum(
        1 for _, opcode, word_count, inst in iter_instructions(words)
        if opcode == OP_BITCAST
        and word_count == 4
        and inst[1] == uchar4_type
        and inst[3] < bound
        and type_by_id[inst[3]] == uint_type
    )
    if pattern_count != Q6_U32_TO_U8VEC4_EXPECTED_BITCASTS:
        return list(words), {
            "phase": "q6-u32-to-u8vec4-bitcasts-lowered",
            "changed": False,
            "pattern_count": pattern_count,
            "expected_count": Q6_U32_TO_U8VEC4_EXPECTED_BITCASTS,
        }
    next_id = bound
    new_bound = bound + pattern_count * 7
    out = words[:5]
    lowered = 0
    i = 5
    while i < len(words):
        inst_word = words[i]
        word_count = inst_word >> 16
        opcode = inst_word & 0xFFFF
        if word_count == 0 or i + word_count > len(words):
            raise ValueError(f"truncated SPIR-V instruction at word {i}")
        inst = words[i:i + word_count]
        if opcode == OP_BITCAST and word_count == 4 and inst[1] == uchar4_type and inst[3] < bound and type_by_id[inst[3]] == uint_type:
            result, source = inst[2], inst[3]
            b0 = next_id; next_id += 1
            s1 = next_id; next_id += 1
            b1 = next_id; next_id += 1
            s2 = next_id; next_id += 1
            b2 = next_id; next_id += 1
            s3 = next_id; next_id += 1
            b3 = next_id; next_id += 1
            out += [
                (4 << 16) | OP_U_CONVERT, uchar_type, b0, source,
                (5 << 16) | OP_SHIFT_RIGHT_LOGICAL, uint_type, s1, source, uint_8,
                (4 << 16) | OP_U_CONVERT, uchar_type, b1, s1,
                (5 << 16) | OP_SHIFT_RIGHT_LOGICAL, uint_type, s2, source, uint_16,
                (4 << 16) | OP_U_CONVERT, uchar_type, b2, s2,
                (5 << 16) | OP_SHIFT_RIGHT_LOGICAL, uint_type, s3, source, uint_24,
                (4 << 16) | OP_U_CONVERT, uchar_type, b3, s3,
                (7 << 16) | OP_COMPOSITE_CONSTRUCT, uchar4_type, result, b0, b1, b2, b3,
            ]
            lowered += 1
            i += word_count
            continue
        out += inst
        i += word_count
    if lowered != pattern_count or next_id != new_bound:
        raise ValueError("internal Q6 u8vec4 bitcast lowering accounting mismatch")
    out[3] = new_bound
    return out, {
        "phase": "q6-u32-to-u8vec4-bitcasts-lowered",
        "changed": True,
        "structural": True,
        "lowered_count": lowered,
        "pattern_count": pattern_count,
    }


def _spirv_resolve_access_base_id(
    object_id: int,
    access_base_by_id: list[int],
    bound: int,
) -> int:
    for _ in range(32):
        if not 0 <= object_id < bound:
            break
        base = access_base_by_id[object_id]
        if base < 0 or base >= bound or base == object_id:
            break
        object_id = base
    return object_id


def insert_q6k_final_store_pre_barrier(words: list[int]) -> tuple[list[int], dict[str, Any]]:
    """Mirror the executor's structural Q6 final-store pre-barrier pass.

    The executor no longer gates this pass on one source hash or fixed SSA IDs.
    It fails closed on the Q6_K final-store topology: descriptor set 0 binding 2
    output stores, lane-0 input compare, staged Function/Workgroup load source,
    an input binding-0 view, and subgroup-reduction operations.  This keeps the
    offline reconstruction authoritative for instrumented/probe SPIR-V modules
    whose hashes and ids differ from the original native module.
    """
    OP_TYPE_BOOL = 20
    OP_TYPE_INT = 21
    OP_TYPE_POINTER = 32
    OP_CONSTANT = 43
    OP_VARIABLE = 59
    OP_LOAD = 61
    OP_STORE = 62
    OP_ACCESS_CHAIN = 65
    OP_IN_BOUNDS_ACCESS_CHAIN = 66
    OP_DECORATE = 71
    OP_LABEL = 248
    OP_I_EQUAL = 170
    OP_CONTROL_BARRIER = 224
    OP_GROUP_NON_UNIFORM_F_ADD = 350
    STORAGE_CLASS_INPUT = 1
    STORAGE_CLASS_WORKGROUP = 4
    STORAGE_CLASS_FUNCTION = 7
    DECORATION_BINDING = 33
    DECORATION_DESCRIPTOR_SET = 34
    Q6_FINAL_REDUCTION_EXIT_LABEL_ID = 1806
    Q6_FINAL_LANE0_COMPARE_ID = 1807
    Q6_LOCAL_INVOCATION_X_ID = 915
    Q6_OUTPUT_BINDING = 2
    Q6_MIN_GROUP_REDUCTIONS = 2
    Q6_EXPECTED_FINAL_OUTPUT_STORES = 2
    Q6_MAX_FINAL_STORE_INSERTS = 4

    if len(words) < 5 or words[0] != SPIRV_MAGIC:
        return list(words), {"phase": "q6-final-store-pre-barrier", "changed": False, "reason": "invalid-spv"}
    bound = words[3]
    if bound <= 0 or bound > 65536:
        return list(words), {"phase": "q6-final-store-pre-barrier", "changed": False, "reason": "id-bound-out-of-range"}

    bool_type = uint_type = 0
    uint_0 = uint_2 = uint_264 = 0
    binding_by_id = [-1] * bound
    set_by_id = [-1] * bound
    pointer_storage_by_id = [-1] * bound
    variable_storage_by_id = [-1] * bound
    access_base_by_id = [-1] * bound
    load_pointer_by_id = [-1] * bound
    group_fadd_count = 0

    for _, opcode, word_count, inst in iter_instructions(words):
        if opcode == OP_TYPE_BOOL and word_count >= 2:
            bool_type = inst[1]
        elif opcode == OP_TYPE_INT and word_count >= 4 and inst[2] == 32 and inst[3] == 0:
            uint_type = inst[1]
        elif opcode == OP_CONSTANT and word_count >= 4 and uint_type and inst[1] == uint_type:
            if inst[3] == 0 and not uint_0:
                uint_0 = inst[2]
            elif inst[3] == 2 and not uint_2:
                uint_2 = inst[2]
            elif inst[3] == 264 and not uint_264:
                uint_264 = inst[2]
        elif opcode == OP_DECORATE and word_count >= 4 and inst[1] < bound:
            if inst[2] == DECORATION_BINDING:
                binding_by_id[inst[1]] = inst[3]
            elif inst[2] == DECORATION_DESCRIPTOR_SET:
                set_by_id[inst[1]] = inst[3]
        elif opcode == OP_TYPE_POINTER and word_count >= 4 and inst[1] < bound:
            pointer_storage_by_id[inst[1]] = inst[2]
        elif opcode == OP_VARIABLE and word_count >= 4 and inst[2] < bound:
            result_type = inst[1]
            if result_type < bound:
                variable_storage_by_id[inst[2]] = pointer_storage_by_id[result_type]
        elif opcode in (OP_ACCESS_CHAIN, OP_IN_BOUNDS_ACCESS_CHAIN) and word_count >= 4 and inst[2] < bound:
            access_base_by_id[inst[2]] = inst[3]
        elif opcode == OP_LOAD and word_count >= 4 and inst[2] < bound:
            load_pointer_by_id[inst[2]] = inst[3]
        elif opcode == OP_GROUP_NON_UNIFORM_F_ADD:
            group_fadd_count += 1

    if not all([bool_type, uint_0, uint_2, uint_264]):
        return list(words), {"phase": "q6-final-store-pre-barrier", "changed": False, "reason": "missing-ids"}
    binding0_variable_count = sum(1 for set_id, binding in zip(set_by_id, binding_by_id) if set_id == 0 and binding == 0)
    binding2_variable_count = sum(1 for set_id, binding in zip(set_by_id, binding_by_id) if set_id == 0 and binding == Q6_OUTPUT_BINDING)
    if binding0_variable_count == 0 or binding2_variable_count == 0 or group_fadd_count < Q6_MIN_GROUP_REDUCTIONS:
        return list(words), {
            "phase": "q6-final-store-pre-barrier",
            "changed": False,
            "reason": "topology-not-q6-final-store",
            "binding0_variable_count": binding0_variable_count,
            "binding2_variable_count": binding2_variable_count,
            "group_fadd_count": group_fadd_count,
        }

    insert_points: list[int] = []
    final_output_store_count = 0
    last_lane0_compare_index: int | None = None
    instructions = list(iter_instructions(words))
    for pos, (index, opcode, word_count, inst) in enumerate(instructions):
        if opcode == OP_LABEL and word_count == 2 and inst[1] == Q6_FINAL_REDUCTION_EXIT_LABEL_ID:
            if pos + 1 < len(instructions):
                next_index, next_opcode, next_wc, next_inst = instructions[pos + 1]
                if (
                    next_opcode == OP_I_EQUAL
                    and next_wc == 5
                    and next_inst[1] == bool_type
                    and next_inst[2] == Q6_FINAL_LANE0_COMPARE_ID
                    and next_inst[3] == Q6_LOCAL_INVOCATION_X_ID
                    and next_inst[4] == uint_0
                ):
                    last_lane0_compare_index = next_index
        elif opcode == OP_I_EQUAL and word_count == 5 and inst[1] == bool_type and (inst[3] == uint_0 or inst[4] == uint_0):
            other = inst[4] if inst[3] == uint_0 else inst[3]
            if 0 <= other < bound and load_pointer_by_id[other] >= 0:
                pointer_id = load_pointer_by_id[other]
                base_id = _spirv_resolve_access_base_id(pointer_id, access_base_by_id, bound)
                if 0 <= base_id < bound and variable_storage_by_id[base_id] == STORAGE_CLASS_INPUT:
                    last_lane0_compare_index = index
        elif opcode == OP_STORE and word_count >= 3 and last_lane0_compare_index is not None:
            pointer_base = _spirv_resolve_access_base_id(inst[1], access_base_by_id, bound)
            object_id = inst[2]
            staged_value = False
            if (
                0 <= pointer_base < bound
                and set_by_id[pointer_base] == 0
                and binding_by_id[pointer_base] == Q6_OUTPUT_BINDING
                and 0 <= object_id < bound
                and load_pointer_by_id[object_id] >= 0
            ):
                object_pointer = load_pointer_by_id[object_id]
                object_base = _spirv_resolve_access_base_id(object_pointer, access_base_by_id, bound)
                staged_value = (
                    0 <= object_base < bound
                    and variable_storage_by_id[object_base] in (STORAGE_CLASS_FUNCTION, STORAGE_CLASS_WORKGROUP)
                )
            if staged_value:
                final_output_store_count += 1
                if last_lane0_compare_index not in insert_points and len(insert_points) < Q6_MAX_FINAL_STORE_INSERTS:
                    insert_points.append(last_lane0_compare_index)

    if final_output_store_count != Q6_EXPECTED_FINAL_OUTPUT_STORES or not insert_points:
        return list(words), {
            "phase": "q6-final-store-pre-barrier",
            "changed": False,
            "reason": "final-store-topology-not-found",
            "final_output_store_count": final_output_store_count,
            "insert_count": len(insert_points),
        }

    barrier = [(4 << 16) | OP_CONTROL_BARRIER, uint_2, uint_2, uint_264]
    active_insert_points = [
        point for point in insert_points
        if not (point >= 4 and words[point - 4:point] == barrier)
    ]
    if not active_insert_points:
        return list(words), {
            "phase": "q6-final-store-pre-barrier",
            "changed": False,
            "reason": "already-present",
            "final_output_store_count": final_output_store_count,
            "insert_count": 0,
        }

    out: list[int] = []
    cursor = 0
    for point in active_insert_points:
        if point < cursor or point > len(words):
            raise ValueError("Q6 final-store pre-barrier insertion points are not monotonic")
        out += words[cursor:point]
        out += barrier
        cursor = point
    out += words[cursor:]
    return out, {
        "phase": "q6-final-store-pre-barrier",
        "changed": True,
        "final_output_store_count": final_output_store_count,
        "insert_count": len(active_insert_points),
        "structural": True,
    }


def rewrite_duplicate_descriptor_bindings(
    words: list[int],
    binding_details: list[dict[str, Any]],
) -> tuple[list[int], dict[str, Any]]:
    bound = words[3]
    used = [False] * MAX_BINDINGS
    first_seen = [False] * MAX_BINDINGS
    has_descriptor_set = [False] * bound
    descriptor_sets = [0] * bound
    for detail in binding_details:
        binding = int(detail.get("binding", -1))
        if 0 <= binding < MAX_BINDINGS:
            used[binding] = True

    for _, opcode, word_count, inst in iter_instructions(words):
        if opcode == 71 and word_count >= 4 and inst[1] < bound and inst[2] == 34:  # DescriptorSet
            has_descriptor_set[inst[1]] = True
            descriptor_sets[inst[1]] = inst[3]
    for _, opcode, word_count, inst in iter_instructions(words):
        if opcode == 71 and word_count >= 4 and inst[1] < bound and inst[2] == 33:  # Binding
            if has_descriptor_set[inst[1]] and descriptor_sets[inst[1]] == 0:
                binding = inst[3]
                if binding >= MAX_BINDINGS:
                    raise ValueError(f"descriptor binding {binding} exceeds local reconstruction limit")
                used[binding] = True

    out = list(words)
    aliases = []
    for index, opcode, word_count, inst in iter_instructions(out):
        if opcode != 71 or word_count < 4 or inst[1] >= bound or inst[2] != 33:  # Binding
            continue
        if not has_descriptor_set[inst[1]] or descriptor_sets[inst[1]] != 0:
            continue
        binding = inst[3]
        if binding >= MAX_BINDINGS:
            raise ValueError(f"descriptor binding {binding} exceeds local reconstruction limit")
        if not first_seen[binding]:
            first_seen[binding] = True
            continue
        alias = next((candidate for candidate in range(MAX_BINDINGS) if not used[candidate]), None)
        if alias is None:
            raise ValueError("no free descriptor binding for duplicate rewrite")
        used[alias] = True
        out[index + 3] = alias
        aliases.append({"target_id": inst[1], "original_binding": binding, "rewritten_binding": alias})

    return out, {
        "phase": "duplicate-descriptor-rewritten",
        "changed": bool(aliases),
        "aliases": aliases,
    }


def find_q6_event(artifact: dict[str, Any], event_index: int) -> dict[str, Any]:
    events = (((artifact.get("gpu") or {}).get("diagnostics") or {}).get("generic_spirv_dispatch") or {}).get("q6_candidate_events")
    if not isinstance(events, list) or not events:
        raise ValueError("artifact has no gpu.diagnostics.generic_spirv_dispatch.q6_candidate_events")
    return events[event_index]


def _event_flag(event: dict[str, Any], name: str, default: bool) -> bool:
    value = event.get(name)
    if value is None:
        return default
    return bool(value)


def _skipped_step(phase: str, words: list[int], reason: str) -> tuple[list[int], dict[str, Any]]:
    return list(words), {"phase": phase, "changed": False, "reason": reason}


def reconstruct(source_words: list[int], event: dict[str, Any]) -> tuple[list[int], list[dict[str, Any]]]:
    entries = event.get("specialization_entries") or []
    binding_details = event.get("binding_details") or []
    steps: list[dict[str, Any]] = [{"phase": "source", "hash": hash_words(source_words), "words": len(source_words)}]
    words1, step = patch_literal_local_size_from_spec(source_words, entries)
    step.update({"hash": hash_words(words1), "words": len(words1)})
    steps.append(step)
    words2, step = materialize_specialization_constants(words1, entries)
    step.update({"hash": hash_words(words2), "words": len(words2)})
    steps.append(step)

    if _event_flag(event, "q6_storage16_loads_lowered", True):
        words3, step = lower_q6k_storage16_loads_to_storage8(words2)
    else:
        words3, step = _skipped_step("q6-storage16-loads-lowered", words2, "not-enabled-by-event")
    step.update({"hash": hash_words(words3), "words": len(words3)})
    steps.append(step)

    if _event_flag(event, "q6_u32_to_u8vec4_bitcasts_lowered", True):
        words4, step = lower_q6k_u32_to_u8vec4_bitcasts(words3)
    else:
        words4, step = _skipped_step("q6-u32-to-u8vec4-bitcasts-lowered", words3, "not-enabled-by-event")
    step.update({"hash": hash_words(words4), "words": len(words4)})
    steps.append(step)

    if _event_flag(event, "q6_final_store_pre_barrier_inserted", True):
        words5, step = insert_q6k_final_store_pre_barrier(words4)
    else:
        words5, step = _skipped_step("q6-final-store-pre-barrier", words4, "not-enabled-by-event")
    step.update({"hash": hash_words(words5), "words": len(words5)})
    steps.append(step)

    if _event_flag(event, "duplicate_descriptor_rewrite", bool(binding_details)):
        words6, step = rewrite_duplicate_descriptor_bindings(words5, binding_details)
    else:
        words6, step = _skipped_step("duplicate-descriptor-rewritten", words5, "not-enabled-by-event")
    step.update({"hash": hash_words(words6), "words": len(words6)})
    steps.append(step)
    return words6, steps


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_spv", type=Path)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--event-index", type=int, default=0)
    parser.add_argument("--out-spv", type=Path)
    parser.add_argument("--out-json", type=Path, required=True)
    args = parser.parse_args()

    artifact = json.loads(args.artifact.read_text(encoding="utf-8"))
    event = find_q6_event(artifact, args.event_index)
    effective_words, steps = reconstruct(read_words(args.source_spv), event)
    if args.out_spv:
        args.out_spv.write_bytes(words_to_bytes(effective_words))
    result = {
        "schema": "skydnir.llama.q6.effective_spirv_lineage.v1",
        "source_spv": str(args.source_spv),
        "artifact": str(args.artifact),
        "event_index": args.event_index,
        "source_spirv_hash": event.get("source_spirv_hash"),
        "expected_effective_spirv_hash": event.get("effective_spirv_hash"),
        "reconstructed_effective_spirv_hash": hash_words(effective_words),
        "matches_expected_effective_hash": hash_words(effective_words) == event.get("effective_spirv_hash"),
        "steps": steps,
    }
    args.out_json.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["matches_expected_effective_hash"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

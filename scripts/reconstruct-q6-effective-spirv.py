#!/usr/bin/env python3
"""Reconstruct the executor-effective Q6 SPIR-V module from static evidence.

This mirrors the Skydnir Android executor's narrow Q6 compatibility sequence:

1. patch literal LocalSize from the WorkgroupSize specialization,
2. materialize supported specialization constants,
3. lower exact Q6 duplicate-view storage16 ushort loads to storage8 byte loads,
4. apply strict duplicate descriptor binding normalization.

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
    if members:
        for dim, member_id in enumerate(members[:3]):
            resolved[dim] = _spec_value(specialization_entries, spec_ids.get(member_id), resolved[dim] or 1)

    eligible = (
        literal_local_size_count == 1
        and local_size_id_count == 0
        and local_size == [1, 1, 1]
        and local_size_id == [0, 0, 0]
        and resolved == [32, 1, 1]
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


def lower_q6k_storage16_loads_to_storage8(words: list[int]) -> tuple[list[int], dict[str, Any]]:
    """Mirror the executor's Q6 storage16 duplicate-view lowering.

    The executor applies this before duplicate descriptor normalization.  It
    rewrites exact ushort loads from the Q6 duplicate storage16 block view
    (variable id 371) into two uchar loads from the byte-identical storage8
    view (variable id 346), reconstructing the same little-endian ushort.
    No descriptors, push constants, specialization values, dispatch dimensions,
    or llama.cpp code are changed.
    """
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
    Q6_STORAGE8_VAR_ID = 346
    Q6_STORAGE16_VAR_ID = 371

    if len(words) < 5 or words[0] != SPIRV_MAGIC:
        return list(words), {"phase": "q6-storage16-loads-lowered", "changed": False, "reason": "invalid-spv"}
    bound = words[3]
    if bound <= Q6_STORAGE16_VAR_ID or bound > 65536:
        return list(words), {"phase": "q6-storage16-loads-lowered", "changed": False, "reason": "id-bound-out-of-range"}

    uint_type = uchar_type = ushort_type = 0
    ptr_ushort_type = ptr_uchar_type = 0
    uint_1 = uint_2 = uint_8 = 0
    uchar_type_end: int | None = None

    for index, opcode, word_count, inst in iter_instructions(words):
        if opcode == 54:  # OpFunction
            break
        if opcode == OP_TYPE_INT and word_count >= 4:
            if inst[2] == 32 and inst[3] == 0:
                uint_type = inst[1]
            elif inst[2] == 8 and inst[3] == 0:
                uchar_type = inst[1]
                uchar_type_end = index + word_count
            elif inst[2] == 16 and inst[3] == 0:
                ushort_type = inst[1]
        elif opcode == OP_TYPE_POINTER and word_count >= 4 and inst[2] == STORAGE_CLASS_STORAGE_BUFFER:
            if ushort_type and inst[3] == ushort_type:
                ptr_ushort_type = inst[1]
            if uchar_type and inst[3] == uchar_type:
                ptr_uchar_type = inst[1]
        elif opcode == OP_CONSTANT and word_count >= 4 and uint_type and inst[1] == uint_type:
            if inst[3] == 1:
                uint_1 = inst[2]
            elif inst[3] == 2:
                uint_2 = inst[2]
            elif inst[3] == 8:
                uint_8 = inst[2]

    if not all([uint_type, uchar_type, ushort_type, ptr_ushort_type, uint_1, uint_2, uint_8]) or uchar_type_end is None:
        return list(words), {"phase": "q6-storage16-loads-lowered", "changed": False, "reason": "missing-required-types-or-constants"}

    instructions = list(iter_instructions(words))
    pattern_count = 0
    for pos, (index, opcode, word_count, inst) in enumerate(instructions):
        if opcode != OP_ACCESS_CHAIN or word_count != 8:
            continue
        if inst[1] != ptr_ushort_type or inst[3] != Q6_STORAGE16_VAR_ID:
            continue
        if pos + 1 >= len(instructions):
            continue
        _, load_opcode, load_wc, load_inst = instructions[pos + 1]
        if load_opcode == OP_LOAD and load_wc == 4 and load_inst[1] == ushort_type and load_inst[3] == inst[2]:
            pattern_count += 1
    if pattern_count == 0 or pattern_count > 256:
        return list(words), {"phase": "q6-storage16-loads-lowered", "changed": False, "pattern_count": pattern_count}

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
        if opcode == OP_ACCESS_CHAIN and word_count == 8 and inst[1] == ptr_ushort_type and inst[3] == Q6_STORAGE16_VAR_ID and i + word_count < len(words):
            load_i = i + word_count
            load_inst_word = words[load_i]
            load_wc = load_inst_word >> 16
            load_opcode = load_inst_word & 0xFFFF
            load_inst = words[load_i:load_i + load_wc]
            if load_wc == 4 and load_i + load_wc <= len(words) and load_opcode == OP_LOAD and load_inst[1] == ushort_type and load_inst[3] == inst[2]:
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
                    (8 << 16) | OP_ACCESS_CHAIN, new_ptr_uchar_type, b0_ptr, Q6_STORAGE8_VAR_ID, index0, block, member, b0_idx,
                    (4 << 16) | OP_LOAD, uchar_type, b0_u8, b0_ptr,
                    (4 << 16) | OP_U_CONVERT, uint_type, b0_u32, b0_u8,
                    (5 << 16) | OP_I_ADD, uint_type, b1_idx, b0_idx, uint_1,
                    (8 << 16) | OP_ACCESS_CHAIN, new_ptr_uchar_type, b1_ptr, Q6_STORAGE8_VAR_ID, index0, block, member, b1_idx,
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
        "lowered_count": lowered,
        "pattern_count": pattern_count,
        "added_ptr_uchar_type": add_ptr_uchar_type,
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
    words3, step = lower_q6k_storage16_loads_to_storage8(words2)
    step.update({"hash": hash_words(words3), "words": len(words3)})
    steps.append(step)
    words4, step = rewrite_duplicate_descriptor_bindings(words3, binding_details)
    step.update({"hash": hash_words(words4), "words": len(words4)})
    steps.append(step)
    return words4, steps


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

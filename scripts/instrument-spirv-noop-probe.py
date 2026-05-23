#!/usr/bin/env python3
"""Add a debug SSBO declaration, optionally with Q6K probe writes, to SPIR-V.

The output is still a complete SPIR-V module.  This intentionally does not
submit fragments, does not change the pdocker VULKAN_DISPATCH_V4 ABI, and does
not edit llama.cpp.  The default mode is the perturbation guard step: add only
descriptor plumbing and prove that it validates.  ``--probe-writes`` extends the
same whole-module instrumentation by writing selected Q6K target values into the
debug SSBO.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any


def fnv1a64(data: bytes) -> int:
    value = 1469598103934665603
    for byte in data:
        value ^= byte
        value = (value * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return value


def hex64(data: bytes) -> str:
    return f"0x{fnv1a64(data):016x}"


def require_tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise SystemExit(f"required SPIR-V tool is missing: {name}")
    return path


def read_debug_descriptor(manifest_path: Path | None, debug_set: int | None, debug_binding: int | None) -> tuple[int, int]:
    if manifest_path:
        payload = json.loads(manifest_path.read_text())
        descriptor = ((payload.get("debug_ssbo") or {}).get("descriptor") or {})
        manifest_set = descriptor.get("set")
        manifest_binding = descriptor.get("binding")
        if not isinstance(manifest_set, int) or not isinstance(manifest_binding, int):
            raise SystemExit("manifest debug_ssbo.descriptor.set/binding must be integers")
        if debug_set is not None and debug_set != manifest_set:
            raise SystemExit("debug set flag does not match manifest")
        if debug_binding is not None and debug_binding != manifest_binding:
            raise SystemExit("debug binding flag does not match manifest")
        return manifest_set, manifest_binding
    if debug_set is None or debug_binding is None:
        raise SystemExit("--debug-set and --debug-binding are required without --manifest-in")
    return debug_set, debug_binding


def replace_bound(lines: list[str], old_bound: int, new_bound: int) -> list[str]:
    old = f"; Bound: {old_bound}"
    new = f"; Bound: {new_bound}"
    replaced = False
    out = []
    for line in lines:
        if line.strip() == old:
            out.append(line.replace(old, new))
            replaced = True
        else:
            out.append(line)
    if not replaced:
        raise SystemExit("could not find SPIR-V Bound header")
    return out


def parse_bound(lines: list[str]) -> int:
    for line in lines:
        match = re.match(r"\s*;\s*Bound:\s*(\d+)\s*$", line)
        if match:
            return int(match.group(1))
    raise SystemExit("could not parse SPIR-V Bound header")


def find_first_type_index(lines: list[str]) -> int:
    for index, line in enumerate(lines):
        if " OpType" in line:
            return index
    raise SystemExit("could not find first OpType instruction")


def find_first_function_index(lines: list[str]) -> int:
    for index, line in enumerate(lines):
        if " OpFunction" in line:
            return index
    raise SystemExit("could not find first OpFunction instruction")


def find_uint32_type_id(lines: list[str]) -> str:
    for line in lines:
        match = re.match(r"\s*(%\d+)\s*=\s*OpTypeInt\s+32\s+0\s*$", line)
        if match:
            return match.group(1)
    raise SystemExit("could not find OpTypeInt 32 0 for debug SSBO words")


def append_entry_interface(lines: list[str], var_id: int) -> list[str]:
    out = []
    changed = False
    entry_pattern = re.compile(r'^(?P<prefix>\s*OpEntryPoint\s+GLCompute\s+%\d+\s+"main"(?P<rest>.*))$')
    for line in lines:
        if not changed and entry_pattern.match(line):
            out.append(line.rstrip("\n") + f" %{var_id}\n")
            changed = True
        else:
            out.append(line)
    if not changed:
        raise SystemExit('could not find GLCompute "main" OpEntryPoint')
    return out


ROLE_CODES = {
    "partial_to_workgroup_candidate": 1,
    "reduction_candidate": 2,
    "post_reduction_workgroup_candidate": 3,
    "final_output_store": 4,
}


def collect_probe_targets(manifest_path: Path | None, enabled: bool) -> list[dict[str, Any]]:
    if not enabled:
        return []
    if manifest_path is None:
        raise SystemExit("--probe-writes requires --manifest-in")
    payload = json.loads(manifest_path.read_text())
    targets = ((payload.get("q6_probe_targets") or {}).get("priority_targets") or [])
    if not isinstance(targets, list) or not targets:
        raise SystemExit("manifest q6_probe_targets.priority_targets is empty")
    out: list[dict[str, Any]] = []
    for target in targets:
        if not isinstance(target, dict):
            continue
        pointer_id = target.get("pointer_id")
        object_id = target.get("object_id")
        role = target.get("role")
        candidate = ((target.get("candidate") or {}).get("candidate_id"))
        if not all(isinstance(v, int) for v in (pointer_id, object_id, candidate)):
            continue
        if not isinstance(role, str):
            continue
        out.append({
            "pointer_id": pointer_id,
            "object_id": object_id,
            "candidate_id": candidate,
            "role": role,
            "role_code": ROLE_CODES.get(role, 0),
            "phase": target.get("phase") if isinstance(target.get("phase"), str) else "",
        })
    if not out:
        raise SystemExit("manifest has no usable Q6 probe targets")
    return out


def result_type_by_id(lines: list[str]) -> dict[int, str]:
    result_types: dict[int, str] = {}
    pattern = re.compile(r"^\s*%(?P<id>\d+)\s*=\s*Op\w+\s+(?P<type>%\d+)\b")
    for line in lines:
        match = pattern.match(line)
        if match:
            result_types[int(match.group("id"))] = match.group("type")
    return result_types


def insert_probe_writes(
        lines: list[str],
        targets: list[dict[str, Any]],
        uint32_type: str,
        ptr_u32_id: int,
        var_id: int,
        next_id: int) -> tuple[list[str], list[dict[str, Any]], int]:
    if not targets:
        return lines, [], next_id
    types = result_type_by_id(lines)
    slot_constants: dict[int, int] = {}
    value_constants: dict[int, int] = {}
    constants: list[str] = []

    def const_id(value: int) -> int:
        nonlocal next_id
        if value not in value_constants:
            value_constants[value] = next_id
            constants.append(f"      %{next_id} = OpConstant {uint32_type} {value}\n")
            next_id += 1
        return value_constants[value]

    def slot_id(slot: int) -> int:
        nonlocal next_id
        if slot not in slot_constants:
            slot_constants[slot] = next_id
            constants.append(f"      %{next_id} = OpConstant {uint32_type} {slot}\n")
            next_id += 1
        return slot_constants[slot]

    for target_index, target in enumerate(targets):
        base = 8 + target_index * 12
        target["slot_base"] = base
        for offset in range(4):
            slot_id(base + offset)
        const_id(int(target["candidate_id"]))
        const_id(int(target["role_code"]))

    insert_at = find_first_function_index(lines)
    lines[insert_at:insert_at] = constants

    emitted: list[dict[str, Any]] = []
    target_by_store: dict[tuple[int, int], dict[str, Any]] = {
        (int(t["pointer_id"]), int(t["object_id"])): t for t in targets
    }
    store_pattern = re.compile(r"^(?P<indent>\s*)OpStore\s+%(?P<pointer>\d+)\s+%(?P<object>\d+)\b")
    out: list[str] = []
    for line in lines:
        out.append(line)
        match = store_pattern.match(line)
        if not match:
            continue
        pointer = int(match.group("pointer"))
        obj = int(match.group("object"))
        target = target_by_store.get((pointer, obj))
        if not target:
            continue
        obj_type = types.get(obj)
        if obj_type not in {"%6", "%14"}:
            raise SystemExit(f"unsupported Q6 probe object type for %{obj}: {obj_type}")
        indent = match.group("indent")
        base = int(target["slot_base"])
        value_id = obj
        bitcast_id = None
        if obj_type == "%14":
            bitcast_id = next_id
            next_id += 1
            value_id = bitcast_id
            out.append(f"{indent}%{bitcast_id} = OpBitcast {uint32_type} %{obj}\n")
        fields = [
            (0, const_id(int(target["candidate_id"]))),
            (1, const_id(int(target["role_code"]))),
            (2, value_id),
        ]
        for slot_offset, source_id in fields:
            ptr_id = next_id
            next_id += 1
            out.append(
                f"{indent}%{ptr_id} = OpAccessChain %{ptr_u32_id} %{var_id} %52 %{slot_id(base + slot_offset)}\n"
            )
            out.append(f"{indent}OpStore %{ptr_id} %{source_id}\n")
        emitted.append({
            "candidate_id": int(target["candidate_id"]),
            "role": target["role"],
            "phase": target["phase"],
            "pointer_id": pointer,
            "object_id": obj,
            "value_bitcast": bool(bitcast_id),
            "slot_base": base,
        })
    missing = [
        f"{t['role']}:{t['candidate_id']} %{t['pointer_id']} %{t['object_id']}"
        for t in targets
        if not any(e["pointer_id"] == t["pointer_id"] and e["object_id"] == t["object_id"] for e in emitted)
    ]
    if missing:
        raise SystemExit("failed to instrument Q6 probe target stores: " + ", ".join(missing))
    return out, emitted, next_id


def instrument_assembly(
        lines: list[str],
        debug_set: int,
        debug_binding: int,
        probe_targets: list[dict[str, Any]] | None = None) -> tuple[list[str], dict[str, Any]]:
    old_bound = parse_bound(lines)
    array_id = old_bound
    struct_id = old_bound + 1
    ptr_id = old_bound + 2
    var_id = old_bound + 3
    ptr_u32_id = old_bound + 4
    next_id = old_bound + 5
    uint32_type = find_uint32_type_id(lines)

    lines = replace_bound(lines, old_bound, next_id)
    lines = append_entry_interface(lines, var_id)

    annotation_index = find_first_type_index(lines)
    annotations = [
        f"               OpDecorate %{array_id} ArrayStride 4\n",
        f"               OpMemberDecorate %{struct_id} 0 Offset 0\n",
        f"               OpDecorate %{struct_id} Block\n",
        f"               OpDecorate %{var_id} DescriptorSet {debug_set}\n",
        f"               OpDecorate %{var_id} Binding {debug_binding}\n",
    ]
    lines[annotation_index:annotation_index] = annotations

    type_index = find_first_function_index(lines)
    declarations = [
        f"      %{array_id} = OpTypeRuntimeArray {uint32_type}\n",
        f"      %{struct_id} = OpTypeStruct %{array_id}\n",
        f"      %{ptr_id} = OpTypePointer StorageBuffer %{struct_id}\n",
        f"      %{ptr_u32_id} = OpTypePointer StorageBuffer {uint32_type}\n",
        f"      %{var_id} = OpVariable %{ptr_id} StorageBuffer\n",
    ]
    lines[type_index:type_index] = declarations
    emitted_probe_writes: list[dict[str, Any]] = []
    lines, emitted_probe_writes, next_id = insert_probe_writes(
        lines,
        probe_targets or [],
        uint32_type,
        ptr_u32_id,
        var_id,
        next_id,
    )
    lines = replace_bound(lines, old_bound + 5, next_id)

    instrumentation = {
        "kind": "q6-debug-ssbo-probe-writes" if emitted_probe_writes else "noop-debug-ssbo-declaration",
        "old_bound": old_bound,
        "new_bound": next_id,
        "reserved_ids": {
            "runtime_array": array_id,
            "struct": struct_id,
            "pointer": ptr_id,
            "u32_pointer": ptr_u32_id,
            "variable": var_id,
        },
        "debug_descriptor": {
            "set": debug_set,
            "binding": debug_binding,
            "descriptor_type": "storage_buffer",
        },
        "executable_probe_writes": len(emitted_probe_writes),
        "probe_writes": emitted_probe_writes,
        "dispatch_transport": "append-as-normal-vulkan-dispatch-v4-binding",
    }
    return lines, instrumentation


def update_manifest(manifest_in: Path, manifest_out: Path, instrumentation: dict[str, Any], output_hash: str) -> None:
    payload = json.loads(manifest_in.read_text())
    payload["instrumentation"] = instrumentation
    payload["instrumented_spirv_hash"] = output_hash
    payload["effective_probe_shader_hash"] = output_hash
    payload.setdefault("basis", {}).setdefault("prior_transforms", [])
    payload["basis"]["prior_transforms"] = [instrumentation["kind"]]
    payload.setdefault("validation_gates", {}).setdefault("post_instrumentation", {})
    payload["validation_gates"]["post_instrumentation"] = {
        "status": "pass",
        "hash": output_hash,
        "tool": "spirv-val",
    }
    manifest_out.parent.mkdir(parents=True, exist_ok=True)
    manifest_out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_spv", type=Path)
    parser.add_argument("output_spv", type=Path)
    parser.add_argument("--manifest-in", type=Path)
    parser.add_argument("--manifest-out", type=Path)
    parser.add_argument("--debug-set", type=int)
    parser.add_argument("--debug-binding", type=int)
    parser.add_argument("--target-env", default="vulkan1.2")
    parser.add_argument("--asm-out", type=Path)
    parser.add_argument("--probe-writes", action="store_true")
    args = parser.parse_args()

    spirv_dis = require_tool("spirv-dis")
    spirv_as = require_tool("spirv-as")
    spirv_val = require_tool("spirv-val")
    debug_set, debug_binding = read_debug_descriptor(args.manifest_in, args.debug_set, args.debug_binding)

    source_bytes = args.input_spv.read_bytes()
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        source_asm = tmp_path / "source.spvasm"
        instrumented_asm = args.asm_out or (tmp_path / "instrumented.spvasm")
        subprocess.run(
            [spirv_dis, "--raw-id", str(args.input_spv), "-o", str(source_asm)],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        lines = source_asm.read_text().splitlines(keepends=True)
        probe_targets = collect_probe_targets(args.manifest_in, args.probe_writes)
        out_lines, instrumentation = instrument_assembly(lines, debug_set, debug_binding, probe_targets)
        instrumented_asm.parent.mkdir(parents=True, exist_ok=True)
        instrumented_asm.write_text("".join(out_lines))
        args.output_spv.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                spirv_as,
                "--preserve-numeric-ids",
                "--target-env",
                "spv1.5",
                str(instrumented_asm),
                "-o",
                str(args.output_spv),
            ],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    subprocess.run(
        [spirv_val, "--target-env", args.target_env, str(args.output_spv)],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    output_bytes = args.output_spv.read_bytes()
    source_hash = hex64(source_bytes)
    output_hash = hex64(output_bytes)
    if source_hash == output_hash:
        raise SystemExit("instrumentation did not change SPIR-V hash")
    if args.manifest_in and args.manifest_out:
        update_manifest(args.manifest_in, args.manifest_out, instrumentation, output_hash)
    result = {
        "schema": "pdocker.spirv.noop-instrumentation.v1",
        "input": str(args.input_spv),
        "output": str(args.output_spv),
        "source_spirv_hash": source_hash,
        "instrumented_spirv_hash": output_hash,
        "target_env": args.target_env,
        "instrumentation": instrumentation,
        "valid": True,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

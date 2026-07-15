#!/usr/bin/env python3
"""Compare SPIR-V dataflow summaries produced by analyze-spirv.py.

The comparison is intentionally structural, not hash-targeted.  It compares
entry points, local size, descriptor declarations, push constant layout, and
load/store pointer origins so native llama.cpp kernels can be checked against a
known-safe diagnostic kernel without changing llama.cpp, Dockerfiles, models, or
prompts.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


SCHEMA = "pdocker.spirv.dataflow-compare.v1"


def load_module(path: Path, module_index: int = 0) -> dict[str, Any]:
    payload = json.loads(path.read_text())
    if payload.get("schema") == "pdocker.spirv.analysis.bundle.v1":
        modules = payload.get("modules") or []
        if module_index >= len(modules):
            raise SystemExit(f"{path}: module index {module_index} out of range")
        return modules[module_index]
    if payload.get("schema") == "pdocker.spirv.analysis.v1":
        return payload
    raise SystemExit(f"{path}: unsupported analysis schema {payload.get('schema')!r}")


def type_layout_signature(type_info: Any) -> Any:
    if not isinstance(type_info, dict):
        return None
    kind = type_info.get("kind")
    if kind in {"int", "float"}:
        result: dict[str, Any] = {"kind": kind, "bits": type_info.get("bits")}
        if kind == "int":
            result["signed"] = type_info.get("signed")
        return result
    if kind == "pointer":
        return {
            "kind": "pointer",
            "storage_class": type_info.get("storage_class"),
            "pointee": type_layout_signature(type_info.get("pointee")),
        }
    if kind == "vector":
        return {
            "kind": "vector",
            "component_count": type_info.get("component_count"),
            "component": type_layout_signature(type_info.get("component")),
        }
    if kind == "matrix":
        return {
            "kind": "matrix",
            "column_count": type_info.get("column_count"),
            "matrix_stride": type_info.get("matrix_stride"),
            "column": type_layout_signature(type_info.get("column")),
        }
    if kind in {"array", "runtime_array"}:
        result = {
            "kind": kind,
            "array_stride": type_info.get("array_stride"),
            "element": type_layout_signature(type_info.get("element")),
        }
        if kind == "array":
            result["length_u32"] = type_info.get("length_u32")
        return result
    if kind == "struct":
        return {
            "kind": "struct",
            "block": bool(type_info.get("block")),
            "buffer_block": bool(type_info.get("buffer_block")),
            "members": [
                {
                    "index": member.get("index"),
                    "offset": member.get("offset"),
                    "layout": {
                        key: value
                        for key, value in sorted((member.get("layout") or {}).items())
                        if key in {"Offset", "ArrayStride", "MatrixStride", "RowMajor", "ColMajor", "NonReadable", "NonWritable"}
                    },
                    "type": type_layout_signature(member.get("type")),
                }
                for member in type_info.get("members", [])
            ],
        }
    return {"kind": kind or "unknown"}


def descriptor_signature(module: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted(
        [
            {
                "set": item.get("set"),
                "binding": item.get("binding"),
                "storage_class": item.get("storage_class"),
                "layout": type_layout_signature(item.get("pointee_layout")),
                "non_readable": bool(item.get("non_readable")),
                "non_writable": bool(item.get("non_writable")),
            }
            for item in module.get("descriptor_variables", [])
        ],
        key=lambda item: (item.get("set", -1), item.get("binding", -1)),
    )


def push_signature(module: dict[str, Any]) -> list[dict[str, Any]]:
    blocks = module.get("push_constant_blocks") or []
    if not blocks:
        return []
    members = blocks[0].get("members") or []
    return [
        {
            "index": member.get("index"),
            "name": member.get("name"),
            "offset": member.get("offset"),
            "type": member.get("type", {}).get("kind"),
            "bits": member.get("type", {}).get("bits"),
        }
        for member in members
    ]

def q6_descriptor_leaf_signature(summary: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(summary, dict):
        return []
    leaves = []
    for leaf in summary.get("descriptor_load_leaves") or []:
        if not isinstance(leaf, dict):
            continue
        descriptor = leaf.get("descriptor") if isinstance(leaf.get("descriptor"), dict) else {}
        byte_offset = leaf.get("byte_offset") if isinstance(leaf.get("byte_offset"), dict) else {}
        terminal = leaf.get("terminal_type") if isinstance(leaf.get("terminal_type"), dict) else leaf.get("element")
        leaves.append(
            {
                "descriptor": {
                    "set": descriptor.get("set", leaf.get("set")),
                    "binding": descriptor.get("binding", leaf.get("binding")),
                    "variable_id": descriptor.get("variable_id", leaf.get("variable_id")),
                },
                "member_path": [
                    {
                        "kind": item.get("kind"),
                        "index": item.get("index"),
                        "offset": item.get("offset"),
                        "array_stride": item.get("array_stride"),
                        "element_type_id": item.get("element_type_id"),
                    }
                    for item in leaf.get("member_path") or []
                    if isinstance(item, dict)
                ],
                "byte_offset": {
                    "static": byte_offset.get("static"),
                    "dynamic_terms": byte_offset.get("dynamic_terms") or [],
                },
                "terminal_type": terminal,
            }
        )
    return leaves

def q6_control_dependency_signature(dependencies: Any) -> list[dict[str, Any]]:
    if not isinstance(dependencies, list):
        return []
    out: list[dict[str, Any]] = []
    for dependency in dependencies:
        if not isinstance(dependency, dict):
            continue
        item: dict[str, Any] = {
            "predecessor_block_label": dependency.get("predecessor_block_label"),
            "condition_id": dependency.get("condition_id"),
            "branch_side": dependency.get("branch_side"),
        }
        condition = dependency.get("condition_dependencies")
        if isinstance(condition, dict):
            item["condition_dependencies"] = q6_dependency_signature(condition)
            item["condition_op_histogram"] = condition.get("op_histogram") or {}
            item["condition_slice_complete"] = condition.get("slice_complete")
        out.append(item)
    return out


def q6_dependency_signature(summary: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(summary, dict):
        return {}
    return {
        "spec_constants": [
            {
                "id": item.get("id"),
                "spec_id": item.get("spec_id"),
                "default_u32": item.get("default_u32"),
            }
            for item in summary.get("spec_constant_dependencies") or []
            if isinstance(item, dict)
        ],
        "constants": [
            {"id": item.get("id"), "value_u32": item.get("value_u32")}
            for item in summary.get("constant_dependencies") or []
            if isinstance(item, dict)
        ],
        "builtins": [
            {
                "id": item.get("id"),
                "built_in": item.get("built_in"),
                "storage_class": item.get("storage_class"),
            }
            for item in summary.get("builtin_dependencies") or []
            if isinstance(item, dict)
        ],
        "push_constants": [
            {
                "variable_id": item.get("variable_id"),
                "member_index": item.get("member_index"),
                "member_offset": item.get("member_offset"),
            }
            for item in summary.get("push_constant_dependencies") or []
            if isinstance(item, dict)
        ],
        "descriptors": [
            {"set": item.get("set"), "binding": item.get("binding")}
            for item in summary.get("descriptor_dependencies") or []
            if isinstance(item, dict)
        ],
        "descriptor_load_leaves": q6_descriptor_leaf_signature(summary),
        "descriptor_load_leaf_count": summary.get("descriptor_load_leaf_count"),
        "ext_inst_histogram": summary.get("ext_inst_histogram") or {},
        "group_nonuniform_histogram": summary.get("group_nonuniform_histogram") or {},
        "named_arithmetic_histogram": summary.get("named_arithmetic_histogram") or {},
        "slice_complete": summary.get("slice_complete"),
        "truncation_boundaries": summary.get("truncation_boundaries") or {},
    }

def q6_final_store_execution_shape_signature(module: dict[str, Any]) -> dict[str, Any] | None:
    q6 = module.get("q6_probe_targets")
    if not isinstance(q6, dict):
        return None
    flow = q6.get("final_store_value_flow")
    shape = module.get("workgroup_execution_shape")
    if not isinstance(flow, dict) or not isinstance(shape, dict):
        return None
    stores = flow.get("stores") if isinstance(flow.get("stores"), list) else []
    return {
        "available": flow.get("available"),
        "final_store_count": flow.get("final_store_count"),
        "valid_store_count": flow.get("valid_store_count"),
        "all_final_stores_valid": all(
            isinstance(store, dict) and store.get("valid") is True
            for store in stores
        ) if stores else False,
        "local_size_consistent_with_workgroup_size": shape.get("statically_consistent"),
        "literal_matches_workgroup_default": shape.get("literal_matches_workgroup_default"),
    }


def q6_stage_target_signature(module: dict[str, Any]) -> dict[str, Any] | None:
    q6 = module.get("q6_probe_targets")
    if not isinstance(q6, dict):
        return None
    phases = []
    for phase in q6.get("phases") or []:
        if not isinstance(phase, dict):
            continue
        targets = []
        for target in phase.get("preceding_workgroup_stores") or []:
            if not isinstance(target, dict):
                continue
            base = target.get("base") if isinstance(target.get("base"), dict) else {}
            role_static = target.get("role_static_support") if isinstance(target.get("role_static_support"), dict) else {}
            stored_value = target.get("stored_value") if isinstance(target.get("stored_value"), dict) else {}
            targets.append(
                {
                    "role": target.get("role"),
                    "base": compact_base_signature(base),
                    "workgroup_base_id": base.get("id") if base.get("storage_class") == "Workgroup" else None,
                    "support_kind": role_static.get("support_kind"),
                    "supported": role_static.get("supported"),
                    "requires_workgroup_load": role_static.get("requires_workgroup_load"),
                    "stored_value_reaches_workgroup_load": role_static.get("stored_value_reaches_workgroup_load"),
                    "same_workgroup_base_id": role_static.get("same_workgroup_base_id"),
                    "role_static_workgroup_load_base_ids": role_static.get("workgroup_load_base_ids") or [],
                    "role_static_store_workgroup_base_id": role_static.get("store_workgroup_base_id"),
                    "stored_value_workgroup_load_count": len(stored_value.get("workgroup_loads") or []),
                    "stored_value_workgroup_load_bases": [
                        {
                            "base": compact_base_signature(load.get("pointer_base") if isinstance(load, dict) else None),
                            "id": (load.get("pointer_base") or {}).get("id")
                            if isinstance(load, dict) and isinstance(load.get("pointer_base"), dict)
                            else None,
                        }
                        for load in stored_value.get("workgroup_loads") or []
                    ],
                    "stored_value_descriptor_load_leaf_count": stored_value.get("descriptor_load_leaf_count"),
                    "stored_value_descriptor_load_leaves": q6_descriptor_leaf_signature(stored_value),
                    "stored_value_op_histogram": stored_value.get("op_histogram") or {},
                    "stored_value_named_arithmetic_histogram": stored_value.get("named_arithmetic_histogram") or {},
                    "stored_value_slice_complete": stored_value.get("slice_complete"),
                    "depends_on_debug_probe_binding": stored_value.get("depends_on_debug_probe_binding"),
                    "control_dependencies": q6_control_dependency_signature(target.get("control_dependencies")),
                }
            )
        output_store = phase.get("output_store") if isinstance(phase.get("output_store"), dict) else {}
        output_base = output_store.get("base") if isinstance(output_store.get("base"), dict) else {}
        phases.append(
            {
                "name": phase.get("name"),
                "source_workgroup_base_count": len(phase.get("source_workgroup_base_ids") or []),
                "output_store_binding": output_base.get("binding"),
                "preceding_workgroup_store_count": len(targets),
                "preceding_workgroup_stores": targets,
            }
        )
    return {
        "available": q6.get("available"),
        "phase_count": len(phases),
        "workgroup_store_count": q6.get("workgroup_store_count"),
        "final_output_store_count": q6.get("final_output_store_count"),
        "phases": phases,
    }


def q6_arithmetic_window_signature(module: dict[str, Any]) -> dict[str, Any] | None:
    q6 = module.get("q6_probe_targets")
    if not isinstance(q6, dict):
        return None
    evidence = q6.get("q6_arithmetic_window_evidence")
    if not isinstance(evidence, dict):
        return None
    windows = []
    for window in evidence.get("windows") or []:
        if not isinstance(window, dict):
            continue
        windows.append(
            {
                "phase": window.get("phase"),
                "ext_inst_histogram": window.get("ext_inst_histogram") or {},
                "group_nonuniform_histogram": window.get("group_nonuniform_histogram") or {},
                "named_arithmetic_histogram": window.get("named_arithmetic_histogram") or {},
            }
        )
    return {
        "available": evidence.get("available"),
        "window_count": evidence.get("window_count"),
        "windows": windows,
    }

def q6_barrier_window_signature(module: dict[str, Any]) -> dict[str, Any] | None:
    q6 = module.get("q6_probe_targets")
    if not isinstance(q6, dict):
        return None
    evidence = q6.get("q6_barrier_window_evidence")
    if not isinstance(evidence, dict):
        return None
    windows = []
    for window in evidence.get("windows") or []:
        if not isinstance(window, dict):
            continue
        windows.append(
            {
                "phase": window.get("phase"),
                "output_store_word_index": window.get("output_store_word_index"),
                "workgroup_store_word_indices": window.get("workgroup_store_word_indices") or [],
                "barrier_word_indices": window.get("barrier_word_indices") or [],
                "workgroup_store_barrier_pairs": window.get("workgroup_store_barrier_pairs") or [],
                "all_workgroup_stores_have_following_barrier": window.get("all_workgroup_stores_have_following_barrier"),
                "all_barriers_are_workgroup_acquire_release": window.get("all_barriers_are_workgroup_acquire_release"),
                "barrier_semantics": [
                    {
                        "op": barrier.get("op"),
                        "execution_scope": barrier.get("execution_scope_name"),
                        "memory_scope": barrier.get("memory_scope_name"),
                        "memory_semantics": barrier.get("memory_semantics_names") or [],
                    }
                    for barrier in window.get("barriers") or []
                    if isinstance(barrier, dict)
                ],
            }
        )
    return {
        "available": evidence.get("available"),
        "window_count": evidence.get("window_count"),
        "barrier_event_count": evidence.get("barrier_event_count"),
        "windows": windows,
    }

def q6_ssa_value_path_signature(path: Any) -> dict[str, Any]:
    if not isinstance(path, dict):
        return {}
    frontier = path.get("frontier") if isinstance(path.get("frontier"), dict) else {}
    truncation = path.get("truncation") if isinstance(path.get("truncation"), dict) else {}
    return {
        "available": path.get("available"),
        "complete": path.get("complete"),
        "node_count": path.get("node_count"),
        "captured_node_count": path.get("captured_node_count"),
        "function_store_expansion_count": path.get("function_store_expansion_count"),
        "captured_function_store_expansion_count": path.get("captured_function_store_expansion_count"),
        "incomplete_reasons": path.get("incomplete_reasons") or [],
        "nodes": [
            {
                key: node.get(key)
                for key in (
                    "kind",
                    "id",
                    "op",
                    "value_u32",
                    "default_u32",
                    "spec_id",
                    "index_count",
                    "pointer_base",
                    "base",
                )
                if isinstance(node, dict) and key in node
            }
            for node in (path.get("nodes") or [])
            if isinstance(node, dict)
        ],
        "function_store_expansions": [
            {
                key: expansion.get(key)
                for key in (
                    "load_id",
                    "matched_store_word_index",
                    "match_strategy",
                    "store_pointer_id",
                    "store_object_id",
                    "store_object_root",
                    "store_pointer",
                )
                if isinstance(expansion, dict) and key in expansion
            }
            for expansion in (path.get("function_store_expansions") or [])
            if isinstance(expansion, dict)
        ],
        "frontier": {
            "workgroup_loads": frontier.get("workgroup_loads") or [],
            "descriptor_load_leaves": q6_descriptor_leaf_signature(
                {"descriptor_load_leaves": frontier.get("descriptor_load_leaves") or []}
            ),
            "descriptor_load_leaf_count": frontier.get("descriptor_load_leaf_count"),
            "descriptor_dependencies": frontier.get("descriptor_dependencies") or [],
            "push_constant_dependencies": frontier.get("push_constant_dependencies") or [],
            "builtin_dependencies": frontier.get("builtin_dependencies") or [],
            "unresolved_id_leaves": frontier.get("unresolved_id_leaves") or [],
        },
        "truncation": truncation,
    }


def q6_final_store_value_flow_signature(module: dict[str, Any]) -> dict[str, Any] | None:
    q6 = module.get("q6_probe_targets")
    if not isinstance(q6, dict):
        return None
    flow = q6.get("final_store_value_flow")
    if not isinstance(flow, dict):
        return None
    stores = []
    for store in flow.get("stores") or []:
        if not isinstance(store, dict):
            continue
        output_store = store.get("output_store") if isinstance(store.get("output_store"), dict) else {}
        base = output_store.get("base") if isinstance(output_store.get("base"), dict) else {}
        stored_value = store.get("stored_value") if isinstance(store.get("stored_value"), dict) else {}
        output_index = store.get("output_index") if isinstance(store.get("output_index"), dict) else {}
        debug_exclusion = store.get("debug_probe_exclusion") if isinstance(store.get("debug_probe_exclusion"), dict) else {}
        stores.append(
            {
                "phase": store.get("phase"),
                "output_binding": base.get("binding"),
                "binding_matches_required": output_store.get("binding_matches_required"),
                "stored_value_reaches_workgroup_load": stored_value.get("reaches_workgroup_load"),
                "stored_value_workgroup_load_count": len(stored_value.get("workgroup_loads") or []),
                "stored_value_op_histogram": stored_value.get("op_histogram") or {},
                "output_index_op_histogram": output_index.get("op_histogram") or {},
                "stored_value_dependencies": q6_dependency_signature(stored_value),
                "ssa_value_path": q6_ssa_value_path_signature(store.get("ssa_value_path")),
                "output_index_dependencies": q6_dependency_signature(output_index),
                "control_dependencies": q6_control_dependency_signature(store.get("control_dependencies")),
                "debug_probe_exclusion_passed": debug_exclusion.get("passed"),
                "valid": store.get("valid"),
            }
        )
    return {
        "available": flow.get("available"),
        "final_store_count": flow.get("final_store_count"),
        "valid_store_count": flow.get("valid_store_count"),
        "stores": stores,
    }


def scalar_id_signature(value: Any) -> Any:
    if not isinstance(value, dict):
        return None
    kind = value.get("kind")
    if kind == "constant":
        return {"kind": kind, "value_u32": value.get("value_u32")}
    if kind == "spec_constant":
        return {
            "kind": kind,
            "default_u32": value.get("default_u32"),
            "spec_id": value.get("spec_id"),
        }
    if kind == "spec_constant_op":
        return {
            "kind": kind,
            "opcode": value.get("opcode"),
            "operands": value.get("operands", []),
            "spec_id": value.get("spec_id"),
        }
    if kind == "spec_constant_composite":
        return {
            "kind": kind,
            "constituents": value.get("constituents", []),
        }
    return {"kind": kind or "id"}


def workgroup_size_signature(module: dict[str, Any]) -> dict[str, Any] | None:
    workgroup = module.get("workgroup_size_builtin")
    if not isinstance(workgroup, dict):
        return None
    kind = workgroup.get("kind")
    if kind == "spec_constant_composite":
        return {
            "kind": kind,
            "components": [
                scalar_id_signature(component)
                for component in workgroup.get("components", [])
            ],
        }
    if kind == "variable":
        return {
            "kind": kind,
            "storage_class": workgroup.get("storage_class"),
        }
    return {"kind": kind or "unknown"}


def origin_key(origin: dict[str, Any]) -> str:
    if not isinstance(origin, dict):
        return "unknown"
    if origin.get("push_member"):
        member = origin["push_member"]
        return f"push[{member.get('index')}:{member.get('name')}@{member.get('offset')}]"
    base = origin.get("base") if origin.get("kind") == "access_chain" else origin
    if isinstance(base, dict) and base.get("kind") == "descriptor":
        indices = origin.get("indices") or []
        idx = ",".join(
            str(item.get("constant_u32")) if item.get("constant_u32") is not None else f"id:{item.get('id')}:{item.get('name','')}"
            for item in indices
        )
        return f"descriptor[{base.get('set')},{base.get('binding')}]({idx})"
    if isinstance(base, dict) and base.get("kind"):
        return f"{base.get('kind')}:{base.get('id')}:{base.get('name','')}"
    return "unknown"


def compact_base_signature(base: Any) -> dict[str, Any]:
    if not isinstance(base, dict):
        return {"kind": "unknown"}
    kind = base.get("kind")
    if kind == "descriptor":
        return {
            "kind": "descriptor",
            "set": base.get("set"),
            "binding": base.get("binding"),
            "storage_class": base.get("storage_class"),
        }
    if kind == "variable":
        return {
            "kind": "variable",
            "storage_class": base.get("storage_class"),
            "built_in": base.get("built_in"),
        }
    if kind:
        return {"kind": kind, "storage_class": base.get("storage_class")}
    return {"kind": "unknown"}


def expression_signature(value: Any, depth: int = 0, max_depth: int = 8) -> Any:
    if depth > max_depth:
        return {"kind": "truncated"}
    if isinstance(value, list):
        return [expression_signature(item, depth + 1, max_depth) for item in value]
    if not isinstance(value, dict):
        return value
    kind = value.get("kind")
    op = value.get("op")
    if kind == "constant":
        return {"kind": "constant", "value_u32": value.get("value_u32")}
    if kind == "spec_constant":
        return {
            "kind": "spec_constant",
            "default_u32": value.get("default_u32"),
            "spec_id": value.get("spec_id"),
        }
    if kind == "load":
        return {
            "kind": "load",
            "pointer": pointer_path_signature(value.get("pointer"), depth + 1, max_depth),
        }
    if kind == "access_chain":
        return pointer_path_signature(value, depth + 1, max_depth)
    result: dict[str, Any] = {"kind": kind or "expr"}
    if isinstance(op, str):
        result["op"] = op
    operands = value.get("operands")
    if isinstance(operands, list):
        result["operands"] = [expression_signature(item, depth + 1, max_depth) for item in operands]
    expr = value.get("expr")
    if expr is not None:
        result["expr"] = expression_signature(expr, depth + 1, max_depth)
    pointer = value.get("pointer")
    if pointer is not None:
        result["pointer"] = pointer_path_signature(pointer, depth + 1, max_depth)
    indices = value.get("indices")
    if isinstance(indices, list):
        result["indices"] = [index_path_signature(item, depth + 1, max_depth) for item in indices]
    if len(result) == 1 and isinstance(value.get("value_u32"), int):
        result["value_u32"] = value.get("value_u32")
    return result


def index_path_signature(index: Any, depth: int = 0, max_depth: int = 8) -> Any:
    if not isinstance(index, dict):
        return {"kind": "literal", "value": index}
    if index.get("constant_u32") is not None:
        return {"kind": "constant", "value_u32": index.get("constant_u32")}
    if "expr" in index:
        return expression_signature(index.get("expr"), depth + 1, max_depth)
    return {"kind": "dynamic"}


def pointer_path_signature(origin: Any, depth: int = 0, max_depth: int = 8) -> Any:
    if depth > max_depth:
        return {"kind": "truncated"}
    if not isinstance(origin, dict):
        return {"kind": "unknown"}
    if origin.get("push_member"):
        member = origin.get("push_member") or {}
        member_type = member.get("type") if isinstance(member.get("type"), dict) else {}
        return {
            "kind": "push",
            "member_index": member.get("index"),
            "offset": member.get("offset"),
            "type": member_type.get("kind"),
        }
    if origin.get("kind") == "access_chain":
        return {
            "kind": "access_chain",
            "base": compact_base_signature(origin.get("base")),
            "indices": [index_path_signature(item, depth + 1, max_depth) for item in origin.get("indices") or []],
        }
    return compact_base_signature(origin)


def event_path_signature(event: dict[str, Any], event_key: str) -> dict[str, Any]:
    event_kind = event_key[:-7] if event_key.endswith("_events") else event_key
    result: dict[str, Any] = {
        "event": event_kind,
        "pointer": pointer_path_signature(event.get("pointer_origin")),
    }
    if event_key == "store_events":
        result["object"] = expression_signature(event.get("object_expr", {"kind": "unavailable"}))
    return result


def canonical_path_key(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def event_summary(module: dict[str, Any], event_key: str) -> dict[str, Any]:
    events = module.get(event_key) or []
    counts = Counter(origin_key(event.get("pointer_origin", {})) for event in events)
    path_counts: Counter[str] = Counter()
    descriptor_counts: Counter[str] = Counter()
    push_counts: Counter[str] = Counter()
    for event in events:
        origin = event.get("pointer_origin", {})
        key = origin_key(origin)
        path_counts[canonical_path_key(event_path_signature(event, event_key))] += 1
        if key.startswith("descriptor["):
            descriptor_counts[key] += 1
        elif key.startswith("push["):
            push_counts[key] += 1
    return {
        "count": len(events),
        "by_origin": dict(sorted(counts.items())),
        "by_path": dict(sorted(path_counts.items())),
        "descriptor_origins": dict(sorted(descriptor_counts.items())),
        "push_origins": dict(sorted(push_counts.items())),
    }


def path_to_string(path: tuple[Any, ...]) -> str:
    out = ""
    for part in path:
        if isinstance(part, int):
            out += f"[{part}]"
        elif out:
            out += f".{part}"
        else:
            out = str(part)
    return out or "$"


def diff_values(left: Any, right: Any, path: tuple[Any, ...] = (), max_diffs: int = 64) -> list[dict[str, Any]]:
    diffs: list[dict[str, Any]] = []

    def add(kind: str, current_path: tuple[Any, ...], left_value: Any, right_value: Any) -> None:
        if len(diffs) >= max_diffs:
            return
        diffs.append(
            {
                "path": path_to_string(current_path),
                "kind": kind,
                "left": left_value,
                "right": right_value,
            }
        )

    def walk(a: Any, b: Any, current_path: tuple[Any, ...]) -> None:
        if len(diffs) >= max_diffs:
            return
        if type(a) is not type(b):
            add("type", current_path, type(a).__name__, type(b).__name__)
            return
        if isinstance(a, dict):
            keys = sorted(set(a) | set(b), key=str)
            for key in keys:
                if len(diffs) >= max_diffs:
                    return
                if key not in a:
                    add("missing-left", current_path + (key,), None, b.get(key))
                elif key not in b:
                    add("missing-right", current_path + (key,), a.get(key), None)
                else:
                    walk(a[key], b[key], current_path + (key,))
            return
        if isinstance(a, list):
            common = min(len(a), len(b))
            for index in range(common):
                if len(diffs) >= max_diffs:
                    return
                walk(a[index], b[index], current_path + (index,))
            if len(a) != len(b):
                add("length", current_path, len(a), len(b))
            return
        if a != b:
            add("value", current_path, a, b)

    walk(left, right, path)
    return diffs


def comparison_diff_summary(left: Any, right: Any, root: str) -> dict[str, Any]:
    diffs = diff_values(left, right, (root,))
    return {
        "diff_paths": [item["path"] for item in diffs],
        "first_mismatch_path": diffs[0]["path"] if diffs else None,
        "path_diffs": diffs,
        "diff_truncated": len(diffs) >= 64,
    }


def compare_lists(name: str, left: list[Any], right: list[Any]) -> dict[str, Any]:
    result = {
        "name": name,
        "match": left == right,
        "left": left,
        "right": right,
    }
    result.update(comparison_diff_summary(left, right, name))
    return result


def compare_values(name: str, left: Any, right: Any) -> dict[str, Any]:
    result = {
        "name": name,
        "match": left == right,
        "left": left,
        "right": right,
    }
    result.update(comparison_diff_summary(left, right, name))
    return result


def compare_counts(name: str, left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    left_counts = left.get("by_origin", {})
    right_counts = right.get("by_origin", {})
    keys = sorted(set(left_counts) | set(right_counts))
    diffs = [
        {"origin": key, "left": left_counts.get(key, 0), "right": right_counts.get(key, 0)}
        for key in keys
        if left_counts.get(key, 0) != right_counts.get(key, 0)
    ]
    result = {
        "name": name,
        "match": not diffs,
        "left_count": left.get("count"),
        "right_count": right.get("count"),
        "diffs": diffs,
    }
    result.update(comparison_diff_summary(left_counts, right_counts, f"{name}.by_origin"))
    return result


def compare_path_counts(name: str, left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    left_counts = left.get("by_path", {})
    right_counts = right.get("by_path", {})
    keys = sorted(set(left_counts) | set(right_counts))
    diffs = []
    for key in keys:
        left_count = left_counts.get(key, 0)
        right_count = right_counts.get(key, 0)
        if left_count == right_count:
            continue
        try:
            signature = json.loads(key)
        except json.JSONDecodeError:
            signature = key
        diffs.append({"path": signature, "left": left_count, "right": right_count})
    return {
        "name": name,
        "match": not diffs,
        "left_count": left.get("count"),
        "right_count": right.get("count"),
        "diffs": diffs,
        "diff_paths": [canonical_path_key(item["path"]) for item in diffs],
        "first_mismatch_path": canonical_path_key(diffs[0]["path"]) if diffs else None,
    }


def comparison_by_name(comparisons: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(item.get("name")): item for item in comparisons if isinstance(item, dict)}


def q6_static_boundary(comparisons: list[dict[str, Any]]) -> dict[str, Any]:
    by_name = comparison_by_name(comparisons)
    ordered = [
        ("q6_final_store_execution_shape", "q6-final-store-execution-shape"),
        ("q6_stage_targets", "q6-stage-targets"),
        ("q6_final_store_value_flow", "q6-final-store-value-flow"),
        ("q6_barrier_window_evidence", "q6-barrier-window"),
        ("q6_arithmetic_window_evidence", "q6-arithmetic-window"),
    ]
    mismatches = []
    for name, boundary in ordered:
        item = by_name.get(name)
        if item and item.get("match") is False:
            mismatches.append(
                {
                    "comparison": name,
                    "boundary": boundary,
                    "first_mismatch_path": item.get("first_mismatch_path"),
                    "diff_paths": (item.get("diff_paths") or [])[:16],
                }
            )
    if not mismatches:
        summary = "q6-static-match"
    elif len(mismatches) == 1:
        summary = mismatches[0]["boundary"]
    elif mismatches[0]["boundary"] == "q6-final-store-execution-shape":
        # This is the most actionable static boundary because final-store value
        # flow can appear identical while the shader's LocalSize/BuiltIn
        # WorkgroupSize contract is inconsistent.  Keep the remaining mismatches
        # as context instead of hiding them.
        summary = "q6-final-store-execution-shape"
    else:
        summary = "q6-static-mixed"
    return {
        "schema": "pdocker.spirv.q6-static-boundary.v1",
        "summary": summary,
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
    }


def summarize(module: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": module.get("schema"),
        "path": module.get("path"),
        "hash": module.get("hash"),
        "bytes": module.get("bytes"),
        "instruction_count": module.get("instruction_count"),
        "entry_points": module.get("entry_points", []),
        "local_size": module.get("local_size"),
        "local_size_id": module.get("local_size_id"),
        "workgroup_size_builtin": workgroup_size_signature(module),
        "workgroup_execution_shape": module.get("workgroup_execution_shape"),
        "ext_inst_imports": module.get("ext_inst_imports", []),
        "ext_inst_histogram": module.get("ext_inst_histogram", {}),
        "group_nonuniform_histogram": module.get("group_nonuniform_histogram", {}),
        "descriptors": descriptor_signature(module),
        "push_constants": push_signature(module),
        "q6_final_store_value_flow": q6_final_store_value_flow_signature(module),
        "q6_final_store_execution_shape": q6_final_store_execution_shape_signature(module),
        "q6_stage_targets": q6_stage_target_signature(module),
        "q6_barrier_window_evidence": q6_barrier_window_signature(module),
        "q6_arithmetic_window_evidence": q6_arithmetic_window_signature(module),
        "loads": event_summary(module, "load_events"),
        "stores": event_summary(module, "store_events"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("left", type=Path)
    parser.add_argument("right", type=Path)
    parser.add_argument("--left-index", type=int, default=0)
    parser.add_argument("--right-index", type=int, default=0)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()

    left_module = load_module(args.left, args.left_index)
    right_module = load_module(args.right, args.right_index)
    left = summarize(left_module)
    right = summarize(right_module)
    comparisons = [
        compare_lists("entry_points", left["entry_points"], right["entry_points"]),
        compare_lists("local_size", left["local_size"], right["local_size"]),
        compare_lists("local_size_id", left["local_size_id"], right["local_size_id"]),
        {
            "name": "workgroup_size_builtin",
            "match": left["workgroup_size_builtin"] == right["workgroup_size_builtin"],
            "left": left["workgroup_size_builtin"],
            "right": right["workgroup_size_builtin"],
            **comparison_diff_summary(
                left["workgroup_size_builtin"],
                right["workgroup_size_builtin"],
                "workgroup_size_builtin",
            ),
        },
        compare_values(
            "workgroup_execution_shape",
            left["workgroup_execution_shape"],
            right["workgroup_execution_shape"],
        ),
        compare_lists("ext_inst_imports", left["ext_inst_imports"], right["ext_inst_imports"]),
        compare_values("ext_inst_histogram", left["ext_inst_histogram"], right["ext_inst_histogram"]),
        compare_values(
            "group_nonuniform_histogram",
            left["group_nonuniform_histogram"],
            right["group_nonuniform_histogram"],
        ),
        compare_lists("descriptors", left["descriptors"], right["descriptors"]),
        compare_lists("push_constants", left["push_constants"], right["push_constants"]),
        compare_values(
            "q6_final_store_value_flow",
            left["q6_final_store_value_flow"],
            right["q6_final_store_value_flow"],
        ),
        compare_values(
            "q6_final_store_execution_shape",
            left["q6_final_store_execution_shape"],
            right["q6_final_store_execution_shape"],
        ),
        compare_values(
            "q6_stage_targets",
            left["q6_stage_targets"],
            right["q6_stage_targets"],
        ),
        compare_values(
            "q6_barrier_window_evidence",
            left["q6_barrier_window_evidence"],
            right["q6_barrier_window_evidence"],
        ),
        compare_values(
            "q6_arithmetic_window_evidence",
            left["q6_arithmetic_window_evidence"],
            right["q6_arithmetic_window_evidence"],
        ),
        compare_counts("load_origins", left["loads"], right["loads"]),
        compare_path_counts("load_paths", left["loads"], right["loads"]),
        compare_counts("store_origins", left["stores"], right["stores"]),
        compare_path_counts("store_paths", left["stores"], right["stores"]),
    ]
    payload = {
        "schema": SCHEMA,
        "left": left,
        "right": right,
        "comparisons": comparisons,
        "q6_static_boundary": q6_static_boundary(comparisons),
        "all_match": all(item.get("match") for item in comparisons),
    }
    text = json.dumps(payload, indent=2, sort_keys=True)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(text + "\n")
    else:
        print(text)
    return 0 if payload["all_match"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

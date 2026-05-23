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


def descriptor_signature(module: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted(
        [
            {
                "set": item.get("set"),
                "binding": item.get("binding"),
                "storage_class": item.get("storage_class"),
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


def event_summary(module: dict[str, Any], event_key: str) -> dict[str, Any]:
    events = module.get(event_key) or []
    counts = Counter(origin_key(event.get("pointer_origin", {})) for event in events)
    descriptor_counts: Counter[str] = Counter()
    push_counts: Counter[str] = Counter()
    for event in events:
        origin = event.get("pointer_origin", {})
        key = origin_key(origin)
        if key.startswith("descriptor["):
            descriptor_counts[key] += 1
        elif key.startswith("push["):
            push_counts[key] += 1
    return {
        "count": len(events),
        "by_origin": dict(sorted(counts.items())),
        "descriptor_origins": dict(sorted(descriptor_counts.items())),
        "push_origins": dict(sorted(push_counts.items())),
    }


def compare_lists(name: str, left: list[Any], right: list[Any]) -> dict[str, Any]:
    return {
        "name": name,
        "match": left == right,
        "left": left,
        "right": right,
    }


def compare_counts(name: str, left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    left_counts = left.get("by_origin", {})
    right_counts = right.get("by_origin", {})
    keys = sorted(set(left_counts) | set(right_counts))
    diffs = [
        {"origin": key, "left": left_counts.get(key, 0), "right": right_counts.get(key, 0)}
        for key in keys
        if left_counts.get(key, 0) != right_counts.get(key, 0)
    ]
    return {
        "name": name,
        "match": not diffs,
        "left_count": left.get("count"),
        "right_count": right.get("count"),
        "diffs": diffs,
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
        "descriptors": descriptor_signature(module),
        "push_constants": push_signature(module),
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
        compare_lists("descriptors", left["descriptors"], right["descriptors"]),
        compare_lists("push_constants", left["push_constants"], right["push_constants"]),
        compare_counts("load_origins", left["loads"], right["loads"]),
        compare_counts("store_origins", left["stores"], right["stores"]),
    ]
    payload = {
        "schema": SCHEMA,
        "left": left,
        "right": right,
        "comparisons": comparisons,
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

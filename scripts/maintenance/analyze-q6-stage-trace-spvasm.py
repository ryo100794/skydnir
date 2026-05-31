#!/usr/bin/env python3
"""Statically inspect Q6_K debug stage trace stores in SPIR-V assembly.

The script does not execute Vulkan. It reads `spirv-dis` output and finds stores
into the Q6 debug SSBO (binding 5). It is used to verify that the instrumented
module contains the expected staged trace records before an ADB run.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

EXPECTED_RECORDS = [
    {"probe": 0, "slot_base": 8, "phase": "tail", "stage": "pre-reduction-store", "candidate_id": 39, "role_code": 1},
    {"probe": 1, "slot_base": 20, "phase": "tail", "stage": "reduction-store", "candidate_id": 49, "role_code": 2},
    {"probe": 2, "slot_base": 32, "phase": "tail", "stage": "accumulator-a-store", "candidate_id": 61, "role_code": 3},
    {"probe": 3, "slot_base": 44, "phase": "tail", "stage": "accumulator-b-store", "candidate_id": 63, "role_code": 3},
    {"probe": 4, "slot_base": 56, "phase": "tail", "stage": "final-store", "candidate_id": 64, "role_code": 4},
    {"probe": 5, "slot_base": 68, "phase": "full", "stage": "pre-reduction-store", "candidate_id": 105, "role_code": 1},
    {"probe": 6, "slot_base": 80, "phase": "full", "stage": "reduction-store", "candidate_id": 115, "role_code": 2},
    {"probe": 7, "slot_base": 92, "phase": "full", "stage": "accumulator-a-store", "candidate_id": 127, "role_code": 3},
    {"probe": 8, "slot_base": 104, "phase": "full", "stage": "accumulator-b-store", "candidate_id": 129, "role_code": 3},
    {"probe": 9, "slot_base": 116, "phase": "full", "stage": "final-store", "candidate_id": 130, "role_code": 4},
]

CONST_RE = re.compile(r"^\s*(%\S+)\s*=\s*OpConstant\s+%uint\s+(\d+)\b")
BINDING_RE = re.compile(r"^\s*OpDecorate\s+(%\S+)\s+Binding\s+(\d+)\b")
ACCESS_RE = re.compile(r"^\s*(%\S+)\s*=\s*OpAccessChain\s+%\S+\s+(%\S+)\s+(.*)$")
STORE_RE = re.compile(r"^\s*OpStore\s+(%\S+)\s+(%\S+)\b")
RESULT_RE = re.compile(r"^\s*(%\S+)\s*=\s*(Op\S+)\s*(.*)$")


def parse_constant_token(token: str, constants: dict[str, int]) -> int | None:
    token = token.strip()
    if token in constants:
        return constants[token]
    if token.startswith("%uint_"):
        tail = token[len("%uint_") :]
        # Disassembly may create duplicate names such as %uint_2_3.
        head = tail.split("_", 1)[0]
        if head.isdigit():
            return int(head)
    if token.startswith("%int_"):
        tail = token[len("%int_") :]
        head = tail.split("_", 1)[0]
        if head.isdigit():
            return int(head)
    return None


def parse_spvasm(text: str) -> dict[str, Any]:
    constants: dict[str, int] = {}
    bindings: dict[str, int] = {}
    access_slots: dict[str, dict[str, Any]] = {}
    stores: dict[int, dict[str, Any]] = {}
    producers: dict[str, dict[str, Any]] = {}

    lines = text.splitlines()
    for lineno, line in enumerate(lines, 1):
        if m := CONST_RE.match(line):
            constants[m.group(1)] = int(m.group(2))
        if m := BINDING_RE.match(line):
            bindings[m.group(1)] = int(m.group(2))
        if m := RESULT_RE.match(line):
            result_id, opcode, rest = m.groups()
            producers[result_id] = {
                "id": result_id,
                "line": lineno,
                "opcode": opcode,
                "operands": rest.split(),
                "text": line.strip(),
            }

    def producer_snapshot(value_id: str | None, depth: int = 0) -> dict[str, Any] | None:
        if not value_id or depth > 2:
            return None
        producer = producers.get(value_id)
        if not producer:
            return None
        snapshot: dict[str, Any] = {
            "id": producer["id"],
            "line": producer["line"],
            "opcode": producer["opcode"],
            "operands": producer["operands"],
            "text": producer["text"],
        }
        if producer["opcode"] == "OpBitcast" and producer["operands"]:
            snapshot["source"] = producer_snapshot(producer["operands"][-1], depth + 1)
        elif producer["opcode"] == "OpLoad" and producer["operands"]:
            snapshot["pointer"] = producer_snapshot(producer["operands"][-1], depth + 1)
        return snapshot

    def value_origin(producer: dict[str, Any] | None) -> dict[str, Any] | None:
        if not producer:
            return None
        source = producer.get("source")
        if isinstance(source, dict):
            return source
        return producer

    def context_lines(start: int | None, end: int | None, radius: int = 4) -> list[dict[str, Any]]:
        if not start or not end:
            return []
        lo = max(1, min(start, end) - radius)
        hi = min(len(lines), max(start, end) + radius)
        return [
            {"line": lineno, "text": lines[lineno - 1].strip()}
            for lineno in range(lo, hi + 1)
        ]

    for lineno, line in enumerate(lines, 1):
        if m := ACCESS_RE.match(line):
            result_id, var_id, rest = m.groups()
            if bindings.get(var_id) != 5:
                continue
            tokens = rest.split()
            if not tokens:
                continue
            slot = parse_constant_token(tokens[-1], constants)
            if slot is None:
                continue
            access_slots[result_id] = {"slot": slot, "line": lineno, "var_id": var_id}
            continue
        if m := STORE_RE.match(line):
            ptr_id, value_id = m.groups()
            access = access_slots.get(ptr_id)
            if not access:
                continue
            value = parse_constant_token(value_id, constants)
            stores[int(access["slot"])] = {
                "slot": int(access["slot"]),
                "value": value,
                "access_line": access["line"],
                "store_line": lineno,
                "value_id": value_id,
                "value_producer": producer_snapshot(value_id),
            }

    records = []
    for expected in EXPECTED_RECORDS:
        base = int(expected["slot_base"])
        candidate = stores.get(base)
        role = stores.get(base + 1)
        value_store = stores.get(base + 2)
        value_producer = value_store.get("value_producer") if value_store else None
        origin = value_origin(value_producer)
        schema = stores.get(base + 10)
        schema_required = int(expected["role_code"]) == 4
        status = "pass"
        failures: list[str] = []
        if not candidate:
            status = "missing"
            failures.append("candidate-store-missing")
        elif candidate.get("value") != expected["candidate_id"]:
            status = "fail"
            failures.append("candidate-id")
        if not role:
            status = "missing"
            failures.append("role-store-missing")
        elif role.get("value") != expected["role_code"]:
            status = "fail"
            failures.append("role-code")
        if schema_required and not schema:
            status = "missing"
            failures.append("schema-store-missing")
        elif schema_required and schema.get("value") != 2:
            status = "fail"
            failures.append("schema-version")
        records.append({
            **expected,
            "observed_candidate_id": candidate.get("value") if candidate else None,
            "observed_role_code": role.get("value") if role else None,
            "observed_record_schema_version": schema.get("value") if schema else None,
            "candidate_store_line": candidate.get("store_line") if candidate else None,
            "role_store_line": role.get("store_line") if role else None,
            "value_store_line": value_store.get("store_line") if value_store else None,
            "value_source_id": value_store.get("value_id") if value_store else None,
            "value_source_producer": value_producer,
            "value_origin_id": origin.get("id") if origin else None,
            "value_origin_opcode": origin.get("opcode") if origin else None,
            "value_origin_line": origin.get("line") if origin else None,
            "value_origin_operands": origin.get("operands") if origin else None,
            "value_flow_context": context_lines(
                origin.get("line") if origin else None,
                value_store.get("store_line") if value_store else None,
            ),
            "schema_store_line": schema.get("store_line") if schema else None,
            "status": status,
            "failures": failures,
        })

    return {
        "schema": "skydnir.q6.stage-trace-spvasm.v1",
        "debug_binding_variable_ids": sorted(k for k, v in bindings.items() if v == 5),
        "debug_store_count": len(stores),
        "record_count": len(records),
        "passed_record_count": sum(1 for r in records if r["status"] == "pass"),
        "summary": "pass" if all(r["status"] == "pass" for r in records) else "fail",
        "records": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spvasm", type=Path)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()
    report = parse_spvasm(args.spvasm.read_text(encoding="utf-8"))
    payload = json.dumps(report, indent=2, sort_keys=True)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if report["summary"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

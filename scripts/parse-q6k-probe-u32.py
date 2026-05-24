#!/usr/bin/env python3
"""Parse Q6_K debug-SSBO u32 probe records from a llama GPU artifact.

The probe write module is a valid-module SPIR-V diagnostic: it appends one
debug storage buffer through the existing VULKAN_DISPATCH_V4 path and writes a
small fixed u32 record at selected Q6_K store sites.  This parser keeps the
device evidence interpretation deterministic so humans do not have to eyeball
large binding diagnostics.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path
from typing import Any, Iterable


EXPECTED_RECORDS = (
    {
        "probe": 0,
        "slot_base": 8,
        "phase": "tail",
        "candidate_id": 39,
        "role_code": 1,
        "role": "partial_to_workgroup",
        "word_index": 3334,
    },
    {
        "probe": 1,
        "slot_base": 20,
        "phase": "tail",
        "candidate_id": 49,
        "role_code": 2,
        "role": "reduction",
        "word_index": 3487,
    },
    {
        "probe": 2,
        "slot_base": 32,
        "phase": "tail",
        "candidate_id": 64,
        "role_code": 4,
        "role": "final_output_store",
        "word_index": 3789,
    },
    {
        "probe": 3,
        "slot_base": 44,
        "phase": "full",
        "candidate_id": 105,
        "role_code": 1,
        "role": "partial_to_workgroup",
        "word_index": 6198,
    },
    {
        "probe": 4,
        "slot_base": 56,
        "phase": "full",
        "candidate_id": 115,
        "role_code": 2,
        "role": "reduction",
        "word_index": 6351,
    },
    {
        "probe": 5,
        "slot_base": 68,
        "phase": "full",
        "candidate_id": 130,
        "role_code": 4,
        "role": "final_output_store",
        "word_index": 6653,
    },
)


def load_json(path: Path) -> Any:
    try:
        if str(path) == "-":
            return json.load(sys.stdin)
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SystemExit(f"artifact missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"artifact is not valid JSON: {path}: {exc}") from exc


def walk_objects(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_objects(child)


def u32_samples_to_map(samples: Any) -> dict[int, int | None]:
    result: dict[int, int | None] = {}
    if not isinstance(samples, list):
        return result
    for item in samples:
        if not isinstance(item, dict):
            continue
        index = item.get("index")
        value = item.get("value")
        if isinstance(index, int):
            result[index] = value if isinstance(value, int) else None
    return result


def bits_to_f32(bits: int | None) -> float | None:
    if bits is None:
        return None
    return struct.unpack("<f", struct.pack("<I", bits & 0xFFFFFFFF))[0]


def find_debug_bindings(data: Any) -> list[dict[str, Any]]:
    bindings: list[dict[str, Any]] = []
    for obj in walk_objects(data):
        if obj.get("debug_probe_binding") is True and "u32_after_dispatch" in obj:
            bindings.append(obj)
    return bindings


def parse_binding(binding: dict[str, Any]) -> dict[str, Any]:
    dispatch = u32_samples_to_map(binding.get("u32_after_dispatch"))
    writeback = u32_samples_to_map(binding.get("u32_after_writeback"))
    records: list[dict[str, Any]] = []
    failures: list[str] = []
    for expected in EXPECTED_RECORDS:
        base = int(expected["slot_base"])
        candidate = dispatch.get(base)
        role_code = dispatch.get(base + 1)
        value_bits = dispatch.get(base + 2)
        status = "pass"
        if candidate != expected["candidate_id"]:
            status = "fail"
            failures.append(
                f"probe {expected['probe']} candidate mismatch: expected "
                f"{expected['candidate_id']} got {candidate}"
            )
        if role_code != expected["role_code"]:
            status = "fail"
            failures.append(
                f"probe {expected['probe']} role mismatch: expected "
                f"{expected['role_code']} got {role_code}"
            )
        record = {
            **expected,
            "observed_candidate_id": candidate,
            "observed_role_code": role_code,
            "value_bits": value_bits,
            "value_f32": bits_to_f32(value_bits),
            "status": status,
        }
        if writeback:
            wb_candidate = writeback.get(base)
            wb_role_code = writeback.get(base + 1)
            wb_value_bits = writeback.get(base + 2)
            record.update(
                {
                    "writeback_candidate_id": wb_candidate,
                    "writeback_role_code": wb_role_code,
                    "writeback_value_bits": wb_value_bits,
                    "writeback_value_f32": bits_to_f32(wb_value_bits),
                    "writeback_status": (
                        "pass"
                        if wb_candidate == expected["candidate_id"]
                        and wb_role_code == expected["role_code"]
                        else "fail"
                    ),
                }
            )
            if record["writeback_status"] != "pass":
                failures.append(
                    f"probe {expected['probe']} writeback metadata mismatch: "
                    f"candidate={wb_candidate} role={wb_role_code}"
                )
        records.append(record)

    expected_slots = set(range(0, 8))
    for expected in EXPECTED_RECORDS:
        base = int(expected["slot_base"])
        expected_slots.update((base, base + 1, base + 2))
    unexpected_nonzero = [
        {"index": index, "value": value}
        for index, value in sorted(dispatch.items())
        if index not in expected_slots and isinstance(value, int) and value != 0
    ]
    return {
        "binding": binding.get("binding"),
        "set": binding.get("set"),
        "size": binding.get("size"),
        "records": records,
        "unexpected_nonzero_slots": unexpected_nonzero,
        "summary": "pass" if not failures else "fail",
        "failures": failures,
    }


def parse_artifact(data: Any) -> dict[str, Any]:
    bindings = find_debug_bindings(data)
    parsed = [parse_binding(binding) for binding in bindings]
    failures = [
        f"binding[{idx}]: {failure}"
        for idx, binding in enumerate(parsed)
        for failure in binding["failures"]
    ]
    if not bindings:
        failures.append("no debug_probe_binding with u32_after_dispatch was found")
    return {
        "schema": "pdocker.q6k.debug-u32-probe-report.v1",
        "debug_binding_count": len(bindings),
        "expected_record_count": len(EXPECTED_RECORDS),
        "bindings": parsed,
        "summary": "pass" if not failures else "fail",
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path, help="llama GPU artifact JSON, or '-' for stdin")
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()

    report = parse_artifact(load_json(args.artifact))
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.json_out:
        args.json_out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0 if report["summary"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

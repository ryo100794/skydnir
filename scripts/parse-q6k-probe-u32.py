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
        "candidate_id": 61,
        "role_code": 3,
        "role": "post_reduction_workgroup",
        "word_index": 3673,
    },
    {
        "probe": 3,
        "slot_base": 44,
        "phase": "tail",
        "candidate_id": 63,
        "role_code": 3,
        "role": "post_reduction_workgroup",
        "word_index": 3745,
    },
    {
        "probe": 4,
        "slot_base": 56,
        "phase": "tail",
        "candidate_id": 64,
        "role_code": 4,
        "role": "final_output_store",
        "word_index": 3789,
    },
    {
        "probe": 5,
        "slot_base": 68,
        "phase": "full",
        "candidate_id": 105,
        "role_code": 1,
        "role": "partial_to_workgroup",
        "word_index": 6198,
    },
    {
        "probe": 6,
        "slot_base": 80,
        "phase": "full",
        "candidate_id": 115,
        "role_code": 2,
        "role": "reduction",
        "word_index": 6351,
    },
    {
        "probe": 7,
        "slot_base": 92,
        "phase": "full",
        "candidate_id": 127,
        "role_code": 3,
        "role": "post_reduction_workgroup",
        "word_index": 6537,
    },
    {
        "probe": 8,
        "slot_base": 104,
        "phase": "full",
        "candidate_id": 129,
        "role_code": 3,
        "role": "post_reduction_workgroup",
        "word_index": 6609,
    },
    {
        "probe": 9,
        "slot_base": 116,
        "phase": "full",
        "candidate_id": 130,
        "role_code": 4,
        "role": "final_output_store",
        "word_index": 6653,
    },
)

LANE_TRACE_SCHEMA_VERSION = 1
LANE_TRACE_HEADER_BASE = 128
LANE_TRACE_LANE_COUNT = 32
LANE_TRACE_WORDS_PER_LANE = 8
LANE_TRACE_PRE_REDUCTION_BASE = 144
LANE_TRACE_REDUCTION_BASE = (
    LANE_TRACE_PRE_REDUCTION_BASE
    + LANE_TRACE_LANE_COUNT * LANE_TRACE_WORDS_PER_LANE
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


def parse_lane_trace(dispatch: dict[int, int | None], writeback: dict[int, int | None]) -> dict[str, Any]:
    header = {
        "schema_version": dispatch.get(LANE_TRACE_HEADER_BASE),
        "lane_count": dispatch.get(LANE_TRACE_HEADER_BASE + 1),
        "words_per_lane": dispatch.get(LANE_TRACE_HEADER_BASE + 2),
        "pre_reduction_base": dispatch.get(LANE_TRACE_HEADER_BASE + 3),
        "reduction_base": dispatch.get(LANE_TRACE_HEADER_BASE + 4),
    }
    header_valid = (
        header["schema_version"] == LANE_TRACE_SCHEMA_VERSION
        and header["lane_count"] == LANE_TRACE_LANE_COUNT
        and header["words_per_lane"] == LANE_TRACE_WORDS_PER_LANE
        and header["pre_reduction_base"] == LANE_TRACE_PRE_REDUCTION_BASE
        and header["reduction_base"] == LANE_TRACE_REDUCTION_BASE
    )
    phases: list[dict[str, Any]] = []
    failures: list[str] = []
    header_present = any(value not in (None, 0) for value in header.values())
    for name, slot_base, expected_candidate in [
        ("pre-reduction-lanes", LANE_TRACE_PRE_REDUCTION_BASE, 105),
        ("reduction-lanes", LANE_TRACE_REDUCTION_BASE, 115),
    ]:
        records: list[dict[str, Any]] = []
        observed_lane_count = 0
        for lane in range(LANE_TRACE_LANE_COUNT):
            base = slot_base + lane * LANE_TRACE_WORDS_PER_LANE
            local_x = dispatch.get(base)
            value_bits = dispatch.get(base + 1)
            candidate_id = dispatch.get(base + 5)
            unexecuted = (
                local_x in (None, 0)
                and value_bits in (None, 0)
                and candidate_id in (None, 0)
            )
            status = "not-executed" if unexecuted else "pass"
            if not unexecuted:
                observed_lane_count += 1
            record_failures: list[str] = []
            if not unexecuted and local_x != lane:
                status = "fail"
                record_failures.append("local-x")
            if not unexecuted and candidate_id != expected_candidate:
                status = "fail"
                record_failures.append("candidate-id")
            record: dict[str, Any] = {
                "lane": lane,
                "slot_base": base,
                "local_x": local_x,
                "value_bits": value_bits,
                "value_f32": bits_to_f32(value_bits),
                "workgroup_id": [dispatch.get(base + offset) for offset in (2, 3, 4)],
                "candidate_id": candidate_id,
                "col": dispatch.get(base + 6),
                "row": dispatch.get(base + 7),
                "status": status,
                "failures": record_failures,
            }
            if writeback:
                record.update(
                    {
                        "writeback_local_x": writeback.get(base),
                        "writeback_value_bits": writeback.get(base + 1),
                        "writeback_value_f32": bits_to_f32(writeback.get(base + 1)),
                        "writeback_workgroup_id": [writeback.get(base + offset) for offset in (2, 3, 4)],
                        "writeback_candidate_id": writeback.get(base + 5),
                    "writeback_col": writeback.get(base + 6),
                    "writeback_row": writeback.get(base + 7),
                    }
                )
            if record_failures:
                failures.append(f"{name} lane {lane}: {','.join(record_failures)}")
            records.append(record)
        phases.append(
            {
                "name": name,
                "slot_base": slot_base,
                "expected_candidate_id": expected_candidate,
                "observed_lane_count": observed_lane_count,
                "executed_lane_count": sum(1 for record in records if record["status"] == "pass"),
                "records": records,
            }
        )
    if not header_valid and not header_present and not any(phase["observed_lane_count"] for phase in phases):
        return {
            "schema": "pdocker.q6k.lane-trace.v1",
            "summary": "not-run",
            "header": header,
            "phases": phases,
        }
    if not header_valid:
        failures.append("lane trace header missing or invalid")
    return {
        "schema": "pdocker.q6k.lane-trace.v1",
        "summary": "pass" if not failures else "fail",
        "header": header,
        "phases": phases,
        "failures": failures,
    }


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
        output_index = dispatch.get(base + 3)
        workgroup_id = [dispatch.get(base + offset) for offset in (4, 5, 6)]
        local_invocation_id = [dispatch.get(base + offset) for offset in (7, 8, 9)]
        record_schema_version = dispatch.get(base + 10)
        unexecuted = candidate in (None, 0) and role_code in (None, 0) and value_bits in (None, 0)
        status = "not-executed" if unexecuted else "pass"
        if not unexecuted and candidate != expected["candidate_id"]:
            status = "fail"
            failures.append(
                f"probe {expected['probe']} candidate mismatch: expected "
                f"{expected['candidate_id']} got {candidate}"
            )
        if not unexecuted and role_code != expected["role_code"]:
            status = "fail"
            failures.append(
                f"probe {expected['probe']} role mismatch: expected "
                f"{expected['role_code']} got {role_code}"
            )
        is_final_output = expected["role"] == "final_output_store"
        trace_status = "not-applicable"
        if is_final_output:
            if unexecuted:
                trace_status = "not-executed"
            elif (
                record_schema_version == 2
                and isinstance(output_index, int)
                and all(isinstance(value, int) for value in workgroup_id)
                and all(isinstance(value, int) for value in local_invocation_id)
            ):
                trace_status = "pass"
            else:
                trace_status = "fail"
                failures.append(
                    f"probe {expected['probe']} final-output trace metadata missing or invalid"
                )
        record = {
            **expected,
            "observed_candidate_id": candidate,
            "observed_role_code": role_code,
            "value_bits": value_bits,
            "value_f32": bits_to_f32(value_bits),
            "record_schema_version": record_schema_version,
            "output_index": output_index,
            "workgroup_id": workgroup_id,
            "local_invocation_id": local_invocation_id,
            "trace_status": trace_status,
            "final_store_trace_v2": trace_status == "pass",
            "status": status,
        }
        if writeback:
            wb_candidate = writeback.get(base)
            wb_role_code = writeback.get(base + 1)
            wb_value_bits = writeback.get(base + 2)
            wb_output_index = writeback.get(base + 3)
            wb_workgroup_id = [writeback.get(base + offset) for offset in (4, 5, 6)]
            wb_local_invocation_id = [writeback.get(base + offset) for offset in (7, 8, 9)]
            wb_record_schema_version = writeback.get(base + 10)
            record.update(
                {
                    "writeback_candidate_id": wb_candidate,
                    "writeback_role_code": wb_role_code,
                    "writeback_value_bits": wb_value_bits,
                    "writeback_value_f32": bits_to_f32(wb_value_bits),
                    "writeback_record_schema_version": wb_record_schema_version,
                    "writeback_output_index": wb_output_index,
                    "writeback_workgroup_id": wb_workgroup_id,
                    "writeback_local_invocation_id": wb_local_invocation_id,
                    "writeback_status": (
                        "pass"
                        if wb_candidate == expected["candidate_id"]
                        and wb_role_code == expected["role_code"]
                        else "fail"
                    ),
                }
            )
            if unexecuted:
                record["writeback_status"] = "not-executed"
            if record["writeback_status"] == "fail":
                failures.append(
                    f"probe {expected['probe']} writeback metadata mismatch: "
                    f"candidate={wb_candidate} role={wb_role_code}"
                )
        records.append(record)

    expected_slots = set(range(0, 8))
    for expected in EXPECTED_RECORDS:
        base = int(expected["slot_base"])
        expected_slots.update(range(base, base + 11))
    expected_slots.update(range(LANE_TRACE_HEADER_BASE, LANE_TRACE_HEADER_BASE + 5))
    expected_slots.update(
        range(
            LANE_TRACE_PRE_REDUCTION_BASE,
            LANE_TRACE_REDUCTION_BASE + LANE_TRACE_LANE_COUNT * LANE_TRACE_WORDS_PER_LANE,
        )
    )
    unexpected_nonzero = [
        {"index": index, "value": value}
        for index, value in sorted(dispatch.items())
        if index not in expected_slots and isinstance(value, int) and value != 0
    ]
    lane_trace = parse_lane_trace(dispatch, writeback)
    if lane_trace["summary"] == "fail":
        lane_failures = lane_trace.get("failures") or ["lane trace failed"]
        failures.extend(f"lane_trace_v1: {failure}" for failure in lane_failures)
    return {
        "binding": binding.get("binding"),
        "set": binding.get("set"),
        "size": binding.get("size"),
        "records": records,
        "lane_trace_v1": lane_trace,
        "executed_record_count": sum(1 for record in records if record["status"] == "pass"),
        "executed_final_record_count": sum(
            1
            for record in records
            if record["status"] == "pass" and record["role"] == "final_output_store"
        ),
        "executed_final_trace_v2_count": sum(
            1
            for record in records
            if record["status"] == "pass"
            and record["role"] == "final_output_store"
            and record.get("trace_status") == "pass"
        ),
        "unexpected_nonzero_slots": unexpected_nonzero,
        "summary": "pass"
        if not failures and any(
            record["status"] == "pass"
            and record["role"] == "final_output_store"
            and record.get("trace_status") == "pass"
            for record in records
        )
        else "fail",
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
    if bindings and not any(binding["summary"] == "pass" for binding in parsed):
        failures.append("no executed final-output Q6 probe record was found")
    if not bindings:
        failures.append("no debug_probe_binding with u32_after_dispatch was found")
    return {
        "schema": "pdocker.q6k.debug-u32-probe-report.v2",
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

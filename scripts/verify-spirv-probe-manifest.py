#!/usr/bin/env python3
"""Fail-closed verifier for pdocker SPIR-V probe manifests.

The probe workflow must never turn into "submit arbitrary SPIR-V fragments" or
silently mutate the Vulkan dispatch ABI.  This verifier checks the manifest that
precedes instrumentation/replay and exits non-zero if the plan is not safe
enough to dispatch later.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


SCHEMA = "pdocker.spirv.probe-manifest.v1"
ANALYSIS_SCHEMA = "pdocker.spirv.analysis.v1"
EXPECTED_TRANSPORT = "append-as-normal-vulkan-dispatch-v4-binding"
EXPECTED_PROBE_METHOD = "instrument-valid-module-not-arbitrary-fragment"
MAX_VULKAN_BINDINGS = 16
MAX_VULKAN_DESCRIPTOR_SETS = 8


def fnv1a64(data: bytes) -> int:
    value = 1469598103934665603
    for byte in data:
        value ^= byte
        value = (value * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return value


def hex64(value: int) -> str:
    return f"0x{value:016x}"


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def verify_manifest(payload: dict) -> list[str]:
    errors: list[str] = []
    if payload.get("schema") != SCHEMA:
        fail(errors, f"schema must be {SCHEMA}")

    basis = payload.get("basis") or {}
    module_words = basis.get("module_words")
    module_bytes = basis.get("module_bytes")
    if basis.get("analysis_schema") != ANALYSIS_SCHEMA:
        fail(errors, f"basis.analysis_schema must be {ANALYSIS_SCHEMA}")
    if basis.get("instrumentation_basis") != "effective-pre-debug":
        fail(errors, "basis.instrumentation_basis must be effective-pre-debug")
    if not isinstance(module_words, int) or module_words <= 0:
        fail(errors, "basis.module_words must be a positive integer")
    if not isinstance(module_bytes, int) or module_bytes <= 0 or module_bytes % 4 != 0:
        fail(errors, "basis.module_bytes must be a positive 4-byte aligned integer")
    elif isinstance(module_words, int) and module_bytes != module_words * 4:
        fail(errors, "basis.module_bytes must equal basis.module_words * 4")
    if basis.get("prior_transforms") not in ([], None):
        fail(errors, "basis.prior_transforms must be empty before debug instrumentation")
    source_spirv = basis.get("source_spirv")
    if isinstance(source_spirv, str) and source_spirv:
        source_path = Path(source_spirv)
        if not source_path.exists():
            fail(errors, f"basis.source_spirv does not exist: {source_spirv}")
        else:
            data = source_path.read_bytes()
            if isinstance(module_bytes, int) and len(data) != module_bytes:
                fail(errors, "basis.module_bytes does not match source SPIR-V file size")
            if isinstance(module_words, int) and len(data) // 4 != module_words:
                fail(errors, "basis.module_words does not match source SPIR-V word count")
            expected_hash = basis.get("module_hash")
            actual_hash = hex64(fnv1a64(data))
            if expected_hash != actual_hash:
                fail(errors, f"basis.module_hash mismatch: expected {expected_hash}, actual {actual_hash}")

    policy = payload.get("policy") or {}
    if policy.get("submission_model") != "valid-module-instrumentation":
        fail(errors, "submission_model must be valid-module-instrumentation")
    if policy.get("fragment_submission_allowed") is not False:
        fail(errors, "fragment submission must be explicitly disabled")
    if policy.get("llama_cpp_modified") is not False:
        fail(errors, "llama.cpp modification must be false")
    if policy.get("dockerfile_model_prompt_modified") is not False:
        fail(errors, "Dockerfile/model/prompt modification must be false")
    if policy.get("static_order_is_dynamic_order") is not False:
        fail(errors, "static candidate order must not be treated as dynamic execution order")

    debug = payload.get("debug_ssbo") or {}
    descriptor = debug.get("descriptor") or {}
    if debug.get("dispatch_transport") != EXPECTED_TRANSPORT:
        fail(errors, "debug SSBO must use ordinary VULKAN_DISPATCH_V4 binding transport")
    if debug.get("descriptor_type") != "storage_buffer":
        fail(errors, "debug descriptor_type must be storage_buffer")
    if debug.get("access") != "write_only":
        fail(errors, "debug SSBO access must be write_only")
    if descriptor.get("available") is not True:
        fail(errors, "debug descriptor must be available")
    if not isinstance(descriptor.get("set"), int) or not isinstance(descriptor.get("binding"), int):
        fail(errors, "debug descriptor set/binding must be integers")
    if descriptor.get("set", -1) < 0 or descriptor.get("binding", -1) < 0:
        fail(errors, "debug descriptor set/binding must be non-negative")
    if isinstance(descriptor.get("set"), int) and descriptor.get("set") >= MAX_VULKAN_DESCRIPTOR_SETS:
        fail(errors, "debug descriptor set exceeds V4 descriptor set limit")
    if isinstance(descriptor.get("binding"), int) and descriptor.get("binding") >= MAX_VULKAN_BINDINGS:
        fail(errors, "debug descriptor binding exceeds V4 binding limit")

    collision = payload.get("collision_checks") or {}
    if collision.get("decision") != "pass":
        fail(errors, "collision decision must be pass")
    proposed = collision.get("proposed") or {}
    if proposed.get("set") != descriptor.get("set") or proposed.get("binding") != descriptor.get("binding"):
        fail(errors, "collision proposed descriptor must match debug descriptor")
    for key in ("static_declared_collision", "static_binding_number_collision", "duplicate_binding_collision"):
        if collision.get(key) is not False:
            fail(errors, f"{key} must be false")
    if collision.get("within_static_tool_limits") is not True:
        fail(errors, "debug descriptor must be within static tool limits")

    declared = (payload.get("descriptors") or {}).get("declared") or []
    duplicate_bindings = (payload.get("descriptors") or {}).get("duplicate_bindings") or []
    if duplicate_bindings:
        fail(errors, "declared descriptor bindings must not contain duplicates")
    seen_declared_pairs: set[tuple[int, int]] = set()
    seen_binding_numbers: set[int] = set()
    for item in declared:
        if not isinstance(item, dict):
            fail(errors, "declared descriptor entries must be objects")
            continue
        dset = item.get("set")
        dbinding = item.get("binding")
        if not isinstance(dset, int) or not isinstance(dbinding, int):
            fail(errors, "declared descriptor set/binding must be integers")
            continue
        pair = (dset, dbinding)
        if pair in seen_declared_pairs:
            fail(errors, f"duplicate declared descriptor pair set={dset} binding={dbinding}")
        seen_declared_pairs.add(pair)
        if dbinding in seen_binding_numbers:
            fail(errors, f"declared descriptor binding number is not globally unique: {dbinding}")
        seen_binding_numbers.add(dbinding)
        if pair == (descriptor.get("set"), descriptor.get("binding")):
            fail(errors, "debug descriptor collides with declared descriptor pair")
        if dbinding == descriptor.get("binding"):
            fail(errors, "debug descriptor binding number collides with declared descriptor")

    gates = payload.get("validation_gates") or {}
    if gates.get("spirv_val_required") is not True:
        fail(errors, "spirv-val must be required")
    if gates.get("dispatch_allowed") is not False:
        fail(errors, "probe manifest alone must not allow dispatch before post-instrumentation validation")
    messages = gates.get("messages") or []
    for required in (
        "input module must pass spirv-val before instrumentation",
        "instrumented module must pass spirv-val after instrumentation",
        "debug descriptor must not collide with existing descriptor set/binding",
    ):
        if required not in messages:
            fail(errors, f"missing validation message: {required}")

    selection = payload.get("probe_selection") or {}
    candidates = selection.get("selected_candidates") or []
    if selection.get("method") != EXPECTED_PROBE_METHOD:
        fail(errors, f"probe_selection.method must be {EXPECTED_PROBE_METHOD}")
    candidate_range = selection.get("candidate_range")
    if not (
        isinstance(candidate_range, list)
        and len(candidate_range) == 2
        and all(isinstance(v, int) for v in candidate_range)
        and 0 <= candidate_range[0] < candidate_range[1]
    ):
        fail(errors, "candidate_range must be a non-empty non-negative half-open range")
    else:
        expected_width = candidate_range[1] - candidate_range[0]
        if selection.get("selected_candidate_count") != len(candidates):
            fail(errors, "selected_candidate_count must equal selected_candidates length")
        if selection.get("selected_candidate_count") != expected_width:
            fail(errors, "selected_candidate_count must equal candidate_range width")

    seen_ids: set[int] = set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            fail(errors, "selected candidates must be objects")
            continue
        cid = candidate.get("candidate_id")
        if not isinstance(cid, int):
            fail(errors, "candidate_id must be an integer")
            continue
        if cid in seen_ids:
            fail(errors, f"duplicate candidate_id {cid}")
        seen_ids.add(cid)
        if isinstance(candidate_range, list) and len(candidate_range) == 2 and all(isinstance(v, int) for v in candidate_range):
            if cid < candidate_range[0] or cid >= candidate_range[1]:
                fail(errors, f"candidate_id {cid} is outside selected candidate_range")
        for key in (
            "word_index",
            "block_entry_insert_after_phi_word_index",
            "block_exit_insert_before_word_index",
            "function_id",
            "block_label",
            "block_ordinal",
        ):
            value = candidate.get(key)
            if not isinstance(value, int):
                fail(errors, f"candidate {cid} {key} must be an integer")
                continue
            if key.endswith("word_index") and isinstance(module_words, int):
                if value < 0 or value >= module_words:
                    fail(errors, f"candidate {cid} {key} is outside module word range")
        entry_index = candidate.get("block_entry_insert_after_phi_word_index")
        exit_index = candidate.get("block_exit_insert_before_word_index")
        if isinstance(entry_index, int) and isinstance(exit_index, int) and entry_index > exit_index:
            fail(errors, f"candidate {cid} entry insertion point is after exit insertion point")

    if isinstance(candidate_range, list) and len(candidate_range) == 2 and all(isinstance(v, int) for v in candidate_range):
        expected_ids = set(range(candidate_range[0], candidate_range[1]))
        if seen_ids and seen_ids != expected_ids:
            fail(errors, "selected candidate ids must exactly match candidate_range")

    for item in selection.get("candidate_ranges") or []:
        rng = item.get("candidate_index_range")
        indices = item.get("candidate_indices") or []
        if not (
            isinstance(rng, list)
            and len(rng) == 2
            and all(isinstance(v, int) for v in rng)
            and 0 <= rng[0] <= rng[1]
        ):
            fail(errors, "candidate_ranges entries must use half-open non-negative ranges")
            continue
        if indices != list(range(rng[0], rng[1])):
            fail(errors, f"candidate range {rng} indices do not match half-open range")
        if item.get("candidate_count") != rng[1] - rng[0]:
            fail(errors, f"candidate range {rng} has wrong candidate_count")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()

    payload = json.loads(args.manifest.read_text())
    errors = verify_manifest(payload)
    result = {
        "schema": "pdocker.spirv.probe-manifest-verification.v1",
        "manifest": str(args.manifest),
        "valid": not errors,
        "errors": errors,
    }
    text = json.dumps(result, indent=2, sort_keys=True)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(text + "\n")
    else:
        print(text)
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())

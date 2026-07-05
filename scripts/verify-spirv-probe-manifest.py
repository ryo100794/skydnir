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
Q6_LANE_TRACE_SCHEMA_VERSION = 1
Q6_LANE_TRACE_HEADER_BASE = 128
Q6_LANE_TRACE_LANE_COUNT = 64
Q6_LANE_TRACE_WORDS_PER_LANE = 8
Q6_LANE_TRACE_PRE_REDUCTION_BASE = 144
Q6_LANE_TRACE_REDUCTION_BASE = (
    Q6_LANE_TRACE_PRE_REDUCTION_BASE
    + Q6_LANE_TRACE_LANE_COUNT * Q6_LANE_TRACE_WORDS_PER_LANE
)
Q6_LANE_TRACE_RECORD_LAYOUT = {
    "local_x": 0,
    "stored_value_bits": 1,
    "workgroup_x": 2,
    "workgroup_y": 3,
    "workgroup_z": 4,
    "candidate_id": 5,
    "col": 6,
    "row": 7,
}
ROLE_CODES = {
    "partial_to_workgroup_candidate": 1,
    "reduction_candidate": 2,
    "post_reduction_workgroup_candidate": 3,
    "final_output_store": 4,
}
PHASE_CODES = {
    "tail": 1,
    "full": 2,
}


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


def verify_q6_probe_write_layout(payload: dict, errors: list[str]) -> None:
    instrumentation = payload.get("instrumentation")
    if not isinstance(instrumentation, dict):
        return
    if instrumentation.get("kind") != "q6-debug-ssbo-probe-writes":
        return
    probe_writes = instrumentation.get("probe_writes")
    if not isinstance(probe_writes, list):
        fail(errors, "instrumentation.probe_writes must be a list for q6-debug-ssbo-probe-writes")
        return
    priority_targets = (((payload.get("q6_probe_targets") or {}).get("priority_targets")) or [])
    priority_keys = set()
    if isinstance(priority_targets, list):
        for target in priority_targets:
            if not isinstance(target, dict):
                continue
            candidate = target.get("candidate") if isinstance(target.get("candidate"), dict) else {}
            key = (
                target.get("pointer_id"),
                target.get("object_id"),
                candidate.get("candidate_id"),
                target.get("role"),
                target.get("phase"),
            )
            if all(value is not None for value in key):
                priority_keys.add(key)
    expected_by_role = {
        "partial_to_workgroup_candidate": Q6_LANE_TRACE_PRE_REDUCTION_BASE,
        "reduction_candidate": Q6_LANE_TRACE_REDUCTION_BASE,
    }
    seen_roles = set()
    for index, item in enumerate(probe_writes):
        if not isinstance(item, dict):
            fail(errors, f"instrumentation.probe_writes[{index}] must be an object")
            continue
        context = f"instrumentation.probe_writes[{index}]"
        role = item.get("role")
        if role not in ROLE_CODES:
            fail(errors, f"{context}.role must be one of {sorted(ROLE_CODES)}")
        elif item.get("role_code") != ROLE_CODES[role]:
            fail(errors, f"{context}.role_code must match role {role}")
        phase = item.get("phase")
        expected_phase_code = PHASE_CODES.get(str(phase or ""), 0)
        if item.get("phase_code") != expected_phase_code:
            fail(errors, f"{context}.phase_code must match phase {phase}")
        key = (
            item.get("pointer_id"),
            item.get("object_id"),
            item.get("candidate_id"),
            role,
            phase,
        )
        if priority_keys and key not in priority_keys:
            fail(errors, f"{context} does not match q6_probe_targets.priority_targets")
        layout = item.get("lane_trace_layout")
        if layout is None:
            continue
        layout_context = f"{context}.lane_trace_layout"
        if role not in expected_by_role or phase != "full":
            fail(errors, f"{layout_context} is only allowed on Q6 full partial/reduction roles")
            continue
        seen_roles.add(str(role))
        expected_slot = expected_by_role[str(role)]
        expected = {
            "schema_version": Q6_LANE_TRACE_SCHEMA_VERSION,
            "header_base": Q6_LANE_TRACE_HEADER_BASE,
            "lane_count": Q6_LANE_TRACE_LANE_COUNT,
            "words_per_lane": Q6_LANE_TRACE_WORDS_PER_LANE,
            "slot_base": expected_slot,
        }
        if not isinstance(layout, dict):
            fail(errors, f"{layout_context} must be an object")
            continue
        for key, expected_value in expected.items():
            actual = layout.get(key)
            if actual != expected_value:
                fail(errors, f"q6 lane trace layout stale: {layout_context}.{key} expected {expected_value} got {actual}")
        record_layout = layout.get("record_layout")
        if record_layout != Q6_LANE_TRACE_RECORD_LAYOUT:
            fail(errors, f"q6 lane trace layout stale: {layout_context}.record_layout must match the current Q6 lane trace ABI")
    for role in expected_by_role:
        if role not in seen_roles:
            fail(errors, f"q6 lane trace layout stale: missing {role} lane_trace_layout in q6-debug probe writes")


def verify_manifest(payload: dict) -> list[str]:
    errors: list[str] = []
    verify_q6_probe_write_layout(payload, errors)
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
    prior_transforms = basis.get("prior_transforms")
    allowed_prior_transforms = (
        [],
        None,
        ["noop-debug-ssbo-declaration"],
        ["q6-debug-ssbo-probe-writes"],
    )
    if prior_transforms not in allowed_prior_transforms:
        fail(errors, "basis.prior_transforms must be empty or contain an approved whole-module debug SSBO transform")
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
    if not isinstance(duplicate_bindings, list):
        fail(errors, "descriptors.duplicate_bindings must be a list")
    seen_declared_pairs: set[tuple[int, int]] = set()
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
            # Some real shaders expose multiple variables for the same API
            # descriptor binding.  That is a shader fact to preserve, not a
            # probe collision, as long as the debug SSBO uses a distinct
            # binding number.
            pass
        seen_declared_pairs.add(pair)
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

    q6_targets = payload.get("q6_probe_targets")
    if not isinstance(q6_targets, dict):
        fail(errors, "q6_probe_targets must be present as an object")
    else:
        if q6_targets.get("method") != "structural-output-descriptor-and-workgroup-store-chain":
            fail(errors, "q6_probe_targets.method must be structural and non-hash-targeted")
        if q6_targets.get("output_descriptor_binding") != 2:
            fail(errors, "q6_probe_targets.output_descriptor_binding must be 2 for Q6 output probes")
        available = q6_targets.get("available")
        if not isinstance(available, bool):
            fail(errors, "q6_probe_targets.available must be boolean")
        for count_key in ("final_output_store_count", "workgroup_store_count"):
            if not isinstance(q6_targets.get(count_key), int) or q6_targets.get(count_key) < 0:
                fail(errors, f"q6_probe_targets.{count_key} must be a non-negative integer")
        phases = q6_targets.get("phases")
        priority_targets = q6_targets.get("priority_targets")
        if not isinstance(phases, list):
            fail(errors, "q6_probe_targets.phases must be a list")
            phases = []
        if not isinstance(priority_targets, list):
            fail(errors, "q6_probe_targets.priority_targets must be a list")
            priority_targets = []

        allowed_roles = {
            "final_output_store",
            "partial_to_workgroup_candidate",
            "reduction_candidate",
            "post_reduction_workgroup_candidate",
        }

        def validate_target(target: object, context: str) -> None:
            if not isinstance(target, dict):
                fail(errors, f"{context} must be an object")
                return
            role = target.get("role")
            if role not in allowed_roles:
                fail(errors, f"{context}.role must be one of {sorted(allowed_roles)}")
            word_index = target.get("word_index")
            if not isinstance(word_index, int):
                fail(errors, f"{context}.word_index must be an integer")
            elif isinstance(module_words, int) and not (0 <= word_index < module_words):
                fail(errors, f"{context}.word_index is outside module word range")
            block = target.get("block")
            if not isinstance(block, dict):
                fail(errors, f"{context}.block must be present")
            else:
                for key in ("function_id", "block_label", "block_ordinal"):
                    if not isinstance(block.get(key), int):
                        fail(errors, f"{context}.block.{key} must be an integer")
            candidate = target.get("candidate")
            if not isinstance(candidate, dict):
                fail(errors, f"{context}.candidate must be present")
            elif not isinstance(candidate.get("candidate_id"), int):
                fail(errors, f"{context}.candidate.candidate_id must be an integer")
            capture = target.get("capture")
            if not isinstance(capture, list) or "stored_value_bits" not in capture or "computed_output_index" not in capture:
                fail(errors, f"{context}.capture must include stored_value_bits and computed_output_index")
            base = target.get("base") if isinstance(target.get("base"), dict) else {}
            if role == "final_output_store":
                if base.get("kind") != "descriptor" or base.get("binding") != 2:
                    fail(errors, f"{context}.base must be descriptor binding 2 for final_output_store")
            elif role in {
                "partial_to_workgroup_candidate",
                "reduction_candidate",
                "post_reduction_workgroup_candidate",
            }:
                if base.get("kind") != "variable" or base.get("storage_class") != "Workgroup":
                    fail(errors, f"{context}.base must be a Workgroup variable for {role}")
                if not isinstance(target.get("related_output_store_word_index"), int):
                    fail(errors, f"{context}.related_output_store_word_index must be present for Workgroup probes")

        def descriptor_dependency_has_debug_binding(summary: dict, context: str, debug_set: int | None, debug_binding: int | None) -> bool:
            matched = False
            deps = summary.get("descriptor_dependencies")
            if not isinstance(deps, list):
                fail(errors, f"{context}.descriptor_dependencies must be a list")
                return False
            for dep_index, dep in enumerate(deps):
                if not isinstance(dep, dict):
                    fail(errors, f"{context}.descriptor_dependencies[{dep_index}] must be an object")
                    continue
                dep_binding = dep.get("binding")
                dep_set = dep.get("set")
                if not isinstance(dep_binding, int):
                    fail(errors, f"{context}.descriptor_dependencies[{dep_index}].binding must be an integer")
                    continue
                if debug_binding is not None and dep_binding == debug_binding and (dep_set is None or debug_set is None or dep_set == debug_set):
                    matched = True
            return matched

        def validate_dependency_summary(summary: object, context: str, require_workgroup_load: bool, debug_set: int | None, debug_binding: int | None) -> None:
            if not isinstance(summary, dict):
                fail(errors, f"{context} must be an object")
                return
            if not isinstance(summary.get("producer_chain"), list):
                fail(errors, f"{context}.producer_chain must be a list")
            if summary.get("depends_on_debug_probe_binding") is not False:
                fail(errors, f"{context}.depends_on_debug_probe_binding must be false")
            if descriptor_dependency_has_debug_binding(summary, context, debug_set, debug_binding):
                fail(errors, f"{context}.descriptor_dependencies must not include debug/probe descriptor")
            workgroup_loads = summary.get("workgroup_loads")
            if require_workgroup_load:
                if summary.get("reaches_workgroup_load") is not True:
                    fail(errors, f"{context}.reaches_workgroup_load must be true when Q6 targets are available")
                if not isinstance(workgroup_loads, list) or not workgroup_loads:
                    fail(errors, f"{context}.workgroup_loads must be non-empty when Q6 targets are available")
            if isinstance(workgroup_loads, list):
                for load_index, load in enumerate(workgroup_loads):
                    if not isinstance(load, dict):
                        fail(errors, f"{context}.workgroup_loads[{load_index}] must be an object")
                        continue
                    base = load.get("pointer_base")
                    if not isinstance(base, dict) or base.get("storage_class") != "Workgroup":
                        fail(errors, f"{context}.workgroup_loads[{load_index}].pointer_base must be Workgroup")

        def validate_final_store_value_flow(flow: object) -> None:
            context = "q6_probe_targets.final_store_value_flow"
            if not isinstance(flow, dict):
                fail(errors, f"{context} must be an object")
                return
            if flow.get("schema") != "pdocker.spirv.q6-final-store-value-flow.v1":
                fail(errors, f"{context}.schema must be pdocker.spirv.q6-final-store-value-flow.v1")
            if flow.get("method") != "backward-slice-stored-value-and-output-index":
                fail(errors, f"{context}.method must be backward-slice-stored-value-and-output-index")
            if flow.get("required_output_descriptor_binding") != 2:
                fail(errors, f"{context}.required_output_descriptor_binding must be 2")
            debug_flow = flow.get("debug_probe_descriptor")
            debug_set = descriptor.get("set") if isinstance(descriptor.get("set"), int) else None
            debug_binding = descriptor.get("binding") if isinstance(descriptor.get("binding"), int) else None
            if not isinstance(debug_flow, dict):
                fail(errors, f"{context}.debug_probe_descriptor must be an object")
            else:
                if debug_flow.get("set") != debug_set or debug_flow.get("binding") != debug_binding:
                    fail(errors, f"{context}.debug_probe_descriptor must match debug_ssbo.descriptor")
                if available is True and debug_flow.get("binding") != descriptor.get("binding"):
                    fail(errors, f"{context}.debug_probe_descriptor.binding must match collision-free debug descriptor")
            stores = flow.get("stores")
            if not isinstance(stores, list):
                fail(errors, f"{context}.stores must be a list")
                stores = []
            if flow.get("final_store_count") != len(stores):
                fail(errors, f"{context}.final_store_count must equal stores length")
            if available is True:
                if flow.get("available") is not True:
                    fail(errors, f"{context}.available must be true when Q6 targets are available")
                if flow.get("valid_store_count") != q6_targets.get("final_output_store_count"):
                    fail(errors, f"{context}.valid_store_count must equal q6_targets.final_output_store_count when available")
            phase_output_words = {
                ((phase.get("output_store") or {}).get("word_index"))
                for phase in phases
                if isinstance(phase, dict)
            }
            for store_index, store in enumerate(stores):
                store_context = f"{context}.stores[{store_index}]"
                if not isinstance(store, dict):
                    fail(errors, f"{store_context} must be an object")
                    continue
                word_index = store.get("word_index")
                if not isinstance(word_index, int):
                    fail(errors, f"{store_context}.word_index must be an integer")
                elif isinstance(module_words, int) and not (0 <= word_index < module_words):
                    fail(errors, f"{store_context}.word_index is outside module word range")
                if available is True and word_index not in phase_output_words:
                    fail(errors, f"{store_context}.word_index must match a phase output_store")
                output_store = store.get("output_store")
                if not isinstance(output_store, dict):
                    fail(errors, f"{store_context}.output_store must be an object")
                else:
                    base = output_store.get("base")
                    if output_store.get("required_binding") != 2 or output_store.get("binding_matches_required") is not True:
                        fail(errors, f"{store_context}.output_store must require and match binding 2")
                    if not isinstance(base, dict) or base.get("kind") != "descriptor" or base.get("binding") != 2:
                        fail(errors, f"{store_context}.output_store.base must be descriptor binding 2")
                exclusion = store.get("debug_probe_exclusion")
                if not isinstance(exclusion, dict):
                    fail(errors, f"{store_context}.debug_probe_exclusion must be an object")
                else:
                    if exclusion.get("set") != debug_set or exclusion.get("binding") != debug_binding:
                        fail(errors, f"{store_context}.debug_probe_exclusion must match debug_ssbo.descriptor")
                    if exclusion.get("stored_value_depends_on_debug_probe") is not False:
                        fail(errors, f"{store_context}.debug_probe_exclusion.stored_value_depends_on_debug_probe must be false")
                    if exclusion.get("output_index_depends_on_debug_probe") is not False:
                        fail(errors, f"{store_context}.debug_probe_exclusion.output_index_depends_on_debug_probe must be false")
                    if exclusion.get("passed") is not True:
                        fail(errors, f"{store_context}.debug_probe_exclusion.passed must be true")
                require_workgroup = available is True
                validate_dependency_summary(store.get("stored_value"), f"{store_context}.stored_value", require_workgroup, debug_set, debug_binding)
                validate_dependency_summary(store.get("output_index"), f"{store_context}.output_index", False, debug_set, debug_binding)
                if available is True and store.get("valid") is not True:
                    fail(errors, f"{store_context}.valid must be true when Q6 targets are available")

        for phase_index, phase in enumerate(phases):
            if not isinstance(phase, dict):
                fail(errors, f"q6_probe_targets.phases[{phase_index}] must be an object")
                continue
            if phase.get("name") not in ("tail", "full", "single"):
                fail(errors, f"q6_probe_targets.phases[{phase_index}].name is invalid")
            source_ids = phase.get("source_workgroup_base_ids")
            if not isinstance(source_ids, list) or not all(isinstance(value, int) for value in source_ids):
                fail(errors, f"q6_probe_targets.phases[{phase_index}].source_workgroup_base_ids must be integer ids")
            validate_target(phase.get("output_store"), f"q6_probe_targets.phases[{phase_index}].output_store")
            for store_index, target in enumerate(phase.get("preceding_workgroup_stores") or []):
                validate_target(target, f"q6_probe_targets.phases[{phase_index}].preceding_workgroup_stores[{store_index}]")

        value_flow = q6_targets.get("final_store_value_flow")
        if value_flow is not None:
            validate_final_store_value_flow(value_flow)

        for target_index, target in enumerate(priority_targets):
            validate_target(target, f"q6_probe_targets.priority_targets[{target_index}]")

        if available is True:
            roles = [
                target.get("role")
                for target in priority_targets
                if isinstance(target, dict)
            ]
            if "final_output_store" not in roles:
                fail(errors, "q6_probe_targets.priority_targets must include a final_output_store when available")
            if "reduction_candidate" not in roles:
                fail(errors, "q6_probe_targets.priority_targets must include a reduction_candidate when available")
            if not phases:
                fail(errors, "q6_probe_targets.phases must be non-empty when available")

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

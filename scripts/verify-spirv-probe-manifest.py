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


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def verify_manifest(payload: dict) -> list[str]:
    errors: list[str] = []
    if payload.get("schema") != SCHEMA:
        fail(errors, f"schema must be {SCHEMA}")

    policy = payload.get("policy") or {}
    if policy.get("submission_model") != "valid-module-instrumentation":
        fail(errors, "submission_model must be valid-module-instrumentation")
    if policy.get("fragment_submission_allowed") is not False:
        fail(errors, "fragment submission must be explicitly disabled")
    if policy.get("llama_cpp_modified") is not False:
        fail(errors, "llama.cpp modification must be false")
    if policy.get("dockerfile_model_prompt_modified") is not False:
        fail(errors, "Dockerfile/model/prompt modification must be false")

    debug = payload.get("debug_ssbo") or {}
    descriptor = debug.get("descriptor") or {}
    if debug.get("dispatch_transport") != "append-as-normal-vulkan-dispatch-v4-binding":
        fail(errors, "debug SSBO must use ordinary VULKAN_DISPATCH_V4 binding transport")
    if descriptor.get("available") is not True:
        fail(errors, "debug descriptor must be available")
    if not isinstance(descriptor.get("set"), int) or not isinstance(descriptor.get("binding"), int):
        fail(errors, "debug descriptor set/binding must be integers")
    if descriptor.get("set", -1) < 0 or descriptor.get("binding", -1) < 0:
        fail(errors, "debug descriptor set/binding must be non-negative")

    collision = payload.get("collision_checks") or {}
    if collision.get("decision") != "pass":
        fail(errors, "collision decision must be pass")
    for key in ("static_declared_collision", "static_binding_number_collision", "duplicate_binding_collision"):
        if collision.get(key) is not False:
            fail(errors, f"{key} must be false")

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
    candidate_range = selection.get("candidate_range")
    if not (
        isinstance(candidate_range, list)
        and len(candidate_range) == 2
        and all(isinstance(v, int) for v in candidate_range)
        and 0 <= candidate_range[0] <= candidate_range[1]
    ):
        fail(errors, "candidate_range must be a non-negative half-open range")
    elif selection.get("selected_candidate_count") != len(candidates):
        fail(errors, "selected_candidate_count must equal selected_candidates length")

    seen_ids: set[int] = set()
    for candidate in candidates:
        cid = candidate.get("candidate_id")
        if not isinstance(cid, int):
            fail(errors, "candidate_id must be an integer")
            continue
        if cid in seen_ids:
            fail(errors, f"duplicate candidate_id {cid}")
        seen_ids.add(cid)
        if candidate.get("block_entry_insert_after_phi_word_index") is None:
            fail(errors, f"candidate {cid} lacks block entry insertion point")
        if candidate.get("block_exit_insert_before_word_index") is None:
            fail(errors, f"candidate {cid} lacks block exit insertion point")

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

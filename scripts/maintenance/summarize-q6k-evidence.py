#!/usr/bin/env python3
"""Inventory Q6_K GPU bridge evidence from existing llama GPU artifacts.

This is deliberately host-only.  It does not contact ADB, rebuild containers,
modify llama.cpp, touch Dockerfiles, change models, or change prompts.  The
goal is to keep the Q6_K miscompute investigation from becoming folklore:
every next task should be derived from visible artifact gaps.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Iterable


Q6_NATIVE_HASH = "0x1bf751845c5dce75"
Q6_SAFE_HASH = "0x7ec0292e948c9b41"
DEFAULT_GLOB = "docs/test/llama-gpu-*.json"
MAX_EVENTS_PER_ARTIFACT = 4
MAX_LIST_ITEMS = 8
DEFAULT_MAX_ARTIFACTS = 32


def find_repo_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (candidate / "scripts").is_dir() and (candidate / "docs").is_dir():
            return candidate
    raise SystemExit(f"could not locate repository root from {start}")


ROOT = find_repo_root(Path(__file__).resolve().parent)


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def load_json(path: Path) -> tuple[Any | None, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except OSError as exc:
        return None, f"read-error: {exc}"
    except json.JSONDecodeError as exc:
        return None, f"json-error: {exc}"


def iter_dicts(value: Any, path: str = "$") -> Iterable[tuple[str, dict[str, Any]]]:
    if isinstance(value, dict):
        yield path, value
        for key, child in value.items():
            child_path = f"{path}.{key}" if key.isidentifier() else f"{path}[{key!r}]"
            yield from iter_dicts(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from iter_dicts(child, f"{path}[{index}]")


def contains_hash(value: Any, target_hashes: set[str]) -> bool:
    if isinstance(value, str):
        return value in target_hashes
    if isinstance(value, dict):
        return any(contains_hash(child, target_hashes) for child in value.values())
    if isinstance(value, list):
        return any(contains_hash(child, target_hashes) for child in value)
    return False


def compact_list(value: Any, limit: int = MAX_LIST_ITEMS) -> Any:
    if not isinstance(value, list):
        return value
    if len(value) <= limit:
        return value
    return value[:limit] + [{"truncated_count": len(value) - limit}]


def compact_binding(binding: Any) -> Any:
    if not isinstance(binding, dict):
        return binding
    keys = [
        "index",
        "set",
        "binding",
        "offset",
        "size",
        "api_offset",
        "api_range",
        "api_buffer_size",
        "api_memory_offset",
        "api_memory_size",
        "api_memory_id",
        "api_buffer_id",
        "alias_rep",
        "active",
        "readable",
        "writable",
        "writeback_verified",
        "writeback_mismatch",
        "gpu_after_upload_hash",
        "gpu_after_dispatch_hash",
        "fd_after_hash",
    ]
    return {key: binding.get(key) for key in keys if key in binding}


def compact_cpu_oracle(oracle: Any) -> Any:
    if not isinstance(oracle, dict):
        return oracle
    keys = [
        "requested",
        "candidate",
        "executed",
        "skipped",
        "status",
        "kernel_hint",
        "compared_floats",
        "mismatch_count",
        "max_abs_error",
        "max_rel_error",
        "expected_hash",
        "gpu_hash",
        "scope",
        "first_mismatch",
    ]
    result = {key: oracle.get(key) for key in keys if key in oracle}
    if "row_window" in oracle:
        result["row_window"] = compact_list(oracle.get("row_window"), 8)
    if "samples" in oracle:
        result["samples"] = compact_list(oracle.get("samples"), 8)
    return result


def compact_event(path: str, event: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "source_spirv_hash",
        "effective_spirv_hash",
        "spirv_hash",
        "shader_bytes",
        "entry",
        "dispatch",
        "push_bytes",
        "spirv_local_size",
        "spirv_local_size_id",
        "spirv_local_size_resolved",
        "spirv_local_size_consistent",
        "local_size_patched",
        "q6k_safe_kernel",
        "safe_kernel_reflection_transfer_pruning",
        "effective_skip_unused_descriptor_transfers",
        "effective_spirv_descriptor_access",
        "valid",
        "stage",
        "error",
        "vk_result",
        "blocker_class",
        "classification",
    ]
    result: dict[str, Any] = {"json_path": path}
    result.update({key: event.get(key) for key in keys if key in event})
    if "push_u32" in event:
        result["push_u32_prefix"] = compact_list(event.get("push_u32"), 16)
    if "specialization_entries" in event:
        result["specialization_entries"] = compact_list(event.get("specialization_entries"), 8)
    if "descriptor_writes" in event:
        result["descriptor_writes"] = compact_list(event.get("descriptor_writes"), 8)
    if "spirv_binding_reflection" in event:
        result["spirv_binding_reflection"] = compact_list(event.get("spirv_binding_reflection"), 8)
    if "binding_details" in event:
        result["binding_details"] = compact_list(
            [compact_binding(item) for item in event.get("binding_details") or []],
            8,
        )
    if "cpu_oracle" in event:
        result["cpu_oracle"] = compact_cpu_oracle(event.get("cpu_oracle"))
    return result


def q6_diagnostics(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    gpu = payload.get("gpu")
    diagnostics = gpu.get("diagnostics") if isinstance(gpu, dict) else None
    q6 = diagnostics.get("q6_workgroup_diagnostics") if isinstance(diagnostics, dict) else None
    return q6 if isinstance(q6, dict) else {}


def artifact_summary(path: Path, target_hashes: set[str]) -> dict[str, Any]:
    payload, error = load_json(path)
    result: dict[str, Any] = {"path": rel(path)}
    if error:
        return result | {"error": error, "q6_event_count": 0}
    if not isinstance(payload, dict):
        return result | {"error": f"root-type-{type(payload).__name__}", "q6_event_count": 0}

    q6 = q6_diagnostics(payload)
    events: list[dict[str, Any]] = []
    for json_path, item in iter_dicts(payload):
        direct_hashes = {
            str(item.get(name))
            for name in ("spirv_hash", "source_spirv_hash", "effective_spirv_hash")
            if item.get(name) is not None
        }
        is_q6_oracle = isinstance(item.get("cpu_oracle"), dict) and (
            "q6" in str(item["cpu_oracle"].get("kernel_hint", "")).lower()
        )
        # Keep this event-level.  Do not match every ancestor object that merely
        # contains a Q6 child somewhere below it; that makes the inventory noisy
        # and hides the actual dispatch/oracle record we need to inspect.
        if direct_hashes & target_hashes or is_q6_oracle:
            events.append(compact_event(json_path, item))
            if len(events) >= MAX_EVENTS_PER_ARTIFACT:
                break

    source_hashes = sorted(
        {
            str(event.get(name))
            for event in events
            for name in ("spirv_hash", "source_spirv_hash", "effective_spirv_hash")
            if event.get(name)
        }
    )
    return result | {
        "schema": payload.get("schema"),
        "q6_event_count": len(events),
        "q6_source_hashes": source_hashes,
        "q6_diagnostics": {
            key: q6.get(key)
            for key in [
                "latest_status",
                "blocker_class",
                "q6_dispatch_seen",
                "q6_dispatch_event_count",
                "local_size_resolved",
                "q6_writeback_verified_all",
                "q6_row_indexed_writeback_verified",
                "q6_output_layout_fixed_offset_rejected",
                "q6_native_reduction_tree_abs_delta",
                "q6_native_reduction_tree_gpu_abs_error",
            ]
            if key in q6
        },
        "q6_events": events,
    }


def derive_next_tasks(artifact_summaries: list[dict[str, Any]], native_spv_files: list[str]) -> list[dict[str, Any]]:
    has_native_hash = any(Q6_NATIVE_HASH in item.get("q6_source_hashes", []) for item in artifact_summaries)
    has_safe_hash = any(Q6_SAFE_HASH in item.get("q6_source_hashes", []) for item in artifact_summaries)
    has_native_spv = any(Q6_NATIVE_HASH in path for path in native_spv_files)
    has_native_mismatch = any(
        (event.get("cpu_oracle") or {}).get("status") == "mismatch"
        for item in artifact_summaries
        for event in item.get("q6_events", [])
    )
    has_native_match = any(
        event.get("source_spirv_hash") == Q6_NATIVE_HASH
        and (event.get("cpu_oracle") or {}).get("status") == "match"
        for item in artifact_summaries
        for event in item.get("q6_events", [])
    )

    tasks: list[dict[str, Any]] = []
    if not has_native_spv:
        tasks.append(
            {
                "id": "q6k-native-spv-dump",
                "status": "open",
                "goal": "Collect the real native Q6 SPIR-V module for source hash 0x1bf751845c5dce75 with PDOCKER_GPU_SPIRV_DUMP_DIR.",
                "acceptance": "A tracked or preserved diagnostic path contains a .spv whose FNV hash is 0x1bf751845c5dce75, plus analysis JSON from scripts/analyze-spirv.py.",
            }
        )
    if has_safe_hash and not has_native_spv:
        tasks.append(
            {
                "id": "q6k-safe-vs-native-static-compare-blocked",
                "status": "blocked",
                "blocked_by": "q6k-native-spv-dump",
                "goal": "Compare safe Q6 dataflow against native Q6 dataflow.",
                "acceptance": "scripts/compare-spirv-dataflow.py emits a report naming descriptor/push/store differences or declaring static ABI match.",
            }
        )
    if has_native_hash and has_native_mismatch:
        tasks.append(
            {
                "id": "q6k-native-mismatch-classify",
                "status": "open",
                "goal": "Classify the native Q6 mismatch into descriptor/range, push/spec, local-size, reduction/arithmetic, synchronization, or final-store.",
                "acceptance": "The latest artifact contains exactly one blocker class and the fields proving why the other classes were eliminated.",
            }
        )
    if has_native_match and not has_native_mismatch:
        tasks.append(
            {
                "id": "q6k-native-correctness-promotion",
                "status": "open",
                "goal": "Promote native Q6 correctness only if prompt sanity, writeback, config propagation, and runtime freshness also pass.",
                "acceptance": "benchmark_claim_allowed=true and no safe-kernel substitution is required.",
            }
        )
    tasks.append(
        {
            "id": "q6k-probe-bisect-ready",
            "status": "open" if has_native_spv else "blocked",
            "blocked_by": None if has_native_spv else "q6k-native-spv-dump",
            "goal": "Prepare valid-module instrumentation bisection for native Q6 store/reduction blocks without changing V4 ABI.",
            "acceptance": "probe manifest verifies fail-closed; post-instrumentation spirv-val passes; debug SSBO binding has no static/runtime collision.",
        }
    )
    return tasks


def expand_artifacts(args: argparse.Namespace) -> list[Path]:
    if args.artifacts:
        return [Path(item) if Path(item).is_absolute() else ROOT / item for item in args.artifacts]
    return sorted(ROOT.glob(DEFAULT_GLOB), key=lambda item: str(item))


def find_spv_files() -> list[str]:
    paths = []
    for path in ROOT.glob("docs/test/**/*.spv"):
        paths.append(rel(path))
    return sorted(paths)


def artifact_priority(item: dict[str, Any]) -> tuple[int, str]:
    hashes = set(item.get("q6_source_hashes") or [])
    events = item.get("q6_events") or []
    has_native_mismatch = any((event.get("cpu_oracle") or {}).get("status") == "mismatch" for event in events)
    has_native_match = any(
        event.get("source_spirv_hash") == Q6_NATIVE_HASH
        and (event.get("cpu_oracle") or {}).get("status") == "match"
        for event in events
    )
    if has_native_mismatch:
        bucket = 0
    elif Q6_NATIVE_HASH in hashes:
        bucket = 1
    elif has_native_match:
        bucket = 2
    elif Q6_SAFE_HASH in hashes:
        bucket = 3
    elif item.get("q6_diagnostics"):
        bucket = 4
    else:
        bucket = 5
    return (bucket, item.get("path", ""))


def summarize(paths: list[Path], *, max_artifacts: int = DEFAULT_MAX_ARTIFACTS) -> dict[str, Any]:
    target_hashes = {Q6_NATIVE_HASH, Q6_SAFE_HASH}
    artifacts = [artifact_summary(path, target_hashes) for path in paths]
    q6_artifacts_all = [item for item in artifacts if item.get("q6_event_count", 0) > 0 or item.get("q6_diagnostics")]
    q6_artifacts_sorted = sorted(q6_artifacts_all, key=artifact_priority)
    if max_artifacts > 0:
        q6_artifacts = q6_artifacts_sorted[:max_artifacts]
    else:
        q6_artifacts = q6_artifacts_sorted
    spv_files = find_spv_files()
    return {
        "schema": "pdocker.q6k.evidence-inventory.v1",
        "native_q6_hash": Q6_NATIVE_HASH,
        "safe_q6_hash": Q6_SAFE_HASH,
        "artifact_count": len(artifacts),
        "q6_artifact_count": len(q6_artifacts_all),
        "included_q6_artifact_count": len(q6_artifacts),
        "omitted_q6_artifact_count": max(0, len(q6_artifacts_all) - len(q6_artifacts)),
        "spv_files": spv_files,
        "next_task_queue": derive_next_tasks(q6_artifacts_all, spv_files),
        "artifacts": q6_artifacts,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifacts", nargs="*", help="Specific llama GPU artifacts to scan.")
    parser.add_argument("--out", type=Path, help="Write inventory JSON to this path.")
    parser.add_argument(
        "--max-artifacts",
        type=int,
        default=DEFAULT_MAX_ARTIFACTS,
        help="Maximum detailed Q6 artifacts to include in output. Use 0 for all.",
    )
    parser.add_argument("--compact", action="store_true", help="Write compact single-line JSON.")
    args = parser.parse_args(argv)

    report = summarize(expand_artifacts(args), max_artifacts=args.max_artifacts)
    if args.compact:
        text = json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    else:
        text = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if args.out:
        out = args.out if args.out.is_absolute() else ROOT / args.out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

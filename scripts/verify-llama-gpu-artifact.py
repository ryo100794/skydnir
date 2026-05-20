#!/usr/bin/env python3
"""Classify a pdocker llama GPU compare artifact.

This verifier is intentionally small and deterministic.  It does not run the
device, rebuild the image, or inspect llama.cpp.  It turns the JSON evidence
written by scripts/android-llama-gpu-compare.sh into a stable pass/blocker
classification that can be used by humans, CI, and future refactors.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any


MEMORY_ERRORS = {"insufficient_memory", "runtime_memory_pressure"}
DEFAULT_MEMORY_DEVICE_ACTIONS = (
    "Do not start or classify the llama GPU compare while this memory blocker is present; this is not a GPU correctness result.",
    "Check MemAvailable first; low SwapFree is Android zram pressure evidence and is advisory unless the artifact enabled a hard swap threshold.",
    "Identify pdocker-owned pdockerd, executor, or stale llama processes and their RSS before taking action.",
    "If pdocker-owned stale llama work is present, run cleanup_commands in order to stop/remove only the pdocker llama container and app-owned pdocker executors; do not force-stop apps.",
    "Wait for Android reclaim or reboot the test device only when MemAvailable remains below the hard threshold or strict swap gating was explicitly configured.",
)
DEFAULT_MEMORY_DIAGNOSTIC_COMMANDS = (
    "adb shell 'cat /proc/meminfo | egrep \"MemAvailable|SwapFree|SwapTotal\"'",
    "adb shell \"run-as io.github.ryo100794.pdocker.compat sh -c 'ps -A -o PID,PPID,RSS,VSZ,NAME,ARGS 2>/dev/null | grep -E \\\"(pdocker|llama|io.github.ryo100794.pdocker.compat)\\\" || true'\"",
)
DEFAULT_MEMORY_CLEANUP_COMMANDS = (
    "adb shell \"run-as io.github.ryo100794.pdocker.compat sh -c 'cd files && test -S pdocker/pdockerd.sock && printf '\\''POST /containers/pdocker-llama-cpp/stop HTTP/1.1\\r\\nHost: pdocker\\r\\nContent-Length: 0\\r\\nConnection: close\\r\\n\\r\\n'\\'' | toybox nc -U -W 3 pdocker/pdockerd.sock >/dev/null || true'\"",
    "adb shell \"run-as io.github.ryo100794.pdocker.compat sh -c 'cd files && test -S pdocker/pdockerd.sock && printf '\\''DELETE /containers/pdocker-llama-cpp?force=true HTTP/1.1\\r\\nHost: pdocker\\r\\nContent-Length: 0\\r\\nConnection: close\\r\\n\\r\\n'\\'' | toybox nc -U -W 3 pdocker/pdockerd.sock >/dev/null || true'\"",
    "adb shell \"run-as io.github.ryo100794.pdocker.compat sh -c 'pkill -x pdocker-gpu-executor 2>/dev/null; pkill -x pdocker-media-executor 2>/dev/null; true'\"",
)
ENV_MANIFEST_PATH = Path(__file__).resolve().with_name("llama-gpu-env-manifest.json")
COMPACT_HASH_RE = re.compile(r"^0x[0-9a-fA-F]{16}$")
ZERO_COMPACT_HASH = "0x0000000000000000"
Q6_WRITEBACK_REQUIRED_FIELDS = (
    "gpu.diagnostics.q6_workgroup_diagnostics.q6_writeback_verified_all",
    "gpu.diagnostics.q6_workgroup_diagnostics.q6_row_indexed_sample_indices",
    "gpu.diagnostics.q6_workgroup_diagnostics.q6_row_indexed_writeback_evidence",
    "gpu.diagnostics.q6_workgroup_diagnostics.q6_row_indexed_writeback_verified",
    "gpu.diagnostics.q6_workgroup_diagnostics.q6_row_indexed_writeback_evidence[].q6_row_indexed",
    "gpu.diagnostics.q6_workgroup_diagnostics.q6_row_indexed_writeback_evidence[].q6_sample_indices",
    "gpu.diagnostics.q6_workgroup_diagnostics.q6_row_indexed_writeback_evidence[].f32_after_dispatch",
    "gpu.diagnostics.q6_workgroup_diagnostics.q6_row_indexed_writeback_evidence[].f32_after_writeback",
    "gpu.diagnostics.q6_workgroup_diagnostics.q6_row_indexed_writeback_evidence[].row_indexed_samples_match_oracle",
    "gpu.diagnostics.q6_workgroup_diagnostics.q6_writable_bindings[].index",
    "gpu.diagnostics.q6_workgroup_diagnostics.q6_writable_bindings[].binding",
    "gpu.diagnostics.q6_workgroup_diagnostics.q6_writable_bindings[].writable",
    "gpu.diagnostics.q6_workgroup_diagnostics.q6_writable_bindings[].gpu_after_dispatch_hash",
    "gpu.diagnostics.q6_workgroup_diagnostics.q6_writable_bindings[].fd_after_hash",
    "gpu.diagnostics.q6_workgroup_diagnostics.q6_writable_bindings[].writeback_verified",
    "gpu.diagnostics.q6_workgroup_diagnostics.q6_writable_bindings[].writeback_mismatch",
)


def _load_env_manifest() -> dict[str, Any]:
    try:
        manifest = json.loads(ENV_MANIFEST_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"llama GPU env manifest missing: {ENV_MANIFEST_PATH}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"llama GPU env manifest is invalid JSON: {ENV_MANIFEST_PATH}: {exc}") from exc
    if manifest.get("schema") != "pdocker.llama.gpu.env-manifest.v1":
        raise RuntimeError(f"llama GPU env manifest has unsupported schema: {ENV_MANIFEST_PATH}")
    return manifest


def _manifest_string_tuple(manifest: dict[str, Any], key: str) -> tuple[str, ...]:
    values = manifest.get(key)
    if not isinstance(values, list) or not all(isinstance(value, str) and value for value in values):
        raise RuntimeError(f"llama GPU env manifest field {key!r} must be a non-empty string list")
    if len(set(values)) != len(values):
        raise RuntimeError(f"llama GPU env manifest field {key!r} contains duplicate entries")
    return tuple(values)


def _manifest_env_field_tuple(manifest: dict[str, Any], key: str) -> tuple[tuple[str, str], ...]:
    values = manifest.get(key)
    if not isinstance(values, list) or not values:
        raise RuntimeError(f"llama GPU env manifest field {key!r} must be a non-empty list")
    fields: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in values:
        if not isinstance(item, dict):
            raise RuntimeError(f"llama GPU env manifest field {key!r} contains a non-object entry")
        env_name = item.get("env")
        executor_field = item.get("executor_field")
        if not isinstance(env_name, str) or not env_name:
            raise RuntimeError(f"llama GPU env manifest field {key!r} contains an invalid env")
        if not isinstance(executor_field, str) or not executor_field:
            raise RuntimeError(f"llama GPU env manifest field {key!r} contains an invalid executor_field")
        if env_name in seen:
            raise RuntimeError(f"llama GPU env manifest field {key!r} repeats env {env_name}")
        seen.add(env_name)
        fields.append((env_name, executor_field))
    return tuple(fields)


LLAMA_GPU_ENV_MANIFEST = _load_env_manifest()

# Shared llama GPU environment manifest.  The compare driver and verifier both
# load scripts/llama-gpu-env-manifest.json so diagnostic toggles cannot silently
# diverge while still leaving the executor, Dockerfiles, llama.cpp, and UI
# untouched.
LLAMA_GPU_UI_RUNTIME_ENV_KEYS = _manifest_string_tuple(LLAMA_GPU_ENV_MANIFEST, "ui_runtime_env_keys")
LLAMA_GPU_PDOCKERD_RUNTIME_ENV_KEYS = _manifest_string_tuple(
    LLAMA_GPU_ENV_MANIFEST, "pdockerd_runtime_env_keys"
)
LLAMA_GPU_UI_COMPOSE_RUNTIME_ENV_KEYS = _manifest_string_tuple(
    LLAMA_GPU_ENV_MANIFEST, "ui_compose_runtime_env_keys"
)
LLAMA_GPU_COMPARE_DIAGNOSTIC_ENV_KEYS = _manifest_string_tuple(
    LLAMA_GPU_ENV_MANIFEST, "compare_diagnostic_env_keys"
)
LLAMA_GPU_COMPARE_FORWARD_ENV_KEYS = _manifest_string_tuple(
    LLAMA_GPU_ENV_MANIFEST, "compare_forward_env_keys"
)
LLAMA_GPU_CONFIG_PROPAGATION_ENV_FIELDS = _manifest_env_field_tuple(
    LLAMA_GPU_ENV_MANIFEST, "config_propagation_env_fields"
)
UNSUPPORTED_GPU_WORK_TOKENS = _manifest_string_tuple(LLAMA_GPU_ENV_MANIFEST, "unsupported_gpu_work_tokens")
REQUIRED_API_PROMPT_PROBES = {"addition": {"prompt": "2+3=", "expected_prefixes": ("5",)}}


def _is_compare_artifact(data: dict[str, Any]) -> bool:
    return data.get("schema") == "pdocker.llama.gpu.compare.v1"



def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"llama artifact missing: {path}")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"llama artifact is not valid JSON: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"llama artifact root must be a JSON object: {path}")
    return data


def nested(data: dict[str, Any], *keys: str) -> Any:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, (str, int, float)) and str(item)]


def _append_unique(base: list[str], additions: tuple[str, ...] | list[str]) -> list[str]:
    seen = set(base)
    for item in additions:
        if item not in seen:
            base.append(item)
            seen.add(item)
    return base


def _memory_diagnostics(data: dict[str, Any]) -> dict[str, Any]:
    value = data.get("pdocker_memory_diagnostics")
    return value if isinstance(value, dict) else {}


def _memory_cleanup_commands(data: dict[str, Any]) -> list[str]:
    commands = _string_list(data.get("cleanup_commands"))
    diagnostics = _memory_diagnostics(data)
    commands = _append_unique(commands, _string_list(diagnostics.get("cleanup_commands")))
    if not commands:
        commands = list(DEFAULT_MEMORY_CLEANUP_COMMANDS)
    return commands


def _memory_diagnostic_commands(data: dict[str, Any]) -> list[str]:
    commands = _string_list(data.get("diagnostic_commands"))
    diagnostics = _memory_diagnostics(data)
    commands = _append_unique(commands, _string_list(diagnostics.get("diagnostic_commands")))
    if not commands:
        commands = list(DEFAULT_MEMORY_DIAGNOSTIC_COMMANDS)
    return commands


def _memory_device_actions(data: dict[str, Any]) -> list[str]:
    actions = _string_list(data.get("device_actions"))
    return _append_unique(actions, list(DEFAULT_MEMORY_DEVICE_ACTIONS))


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _memory_thresholds(data: dict[str, Any]) -> dict[str, Any]:
    value = data.get("memory_thresholds")
    if isinstance(value, dict):
        return value
    memory = data.get("memory")
    required = data.get("required")
    if not isinstance(memory, dict) or not isinstance(required, dict):
        return {}
    mem_required = _safe_int(required.get("mem_preflight_free_mb") or required.get("mem_free_mb"))
    swap_required = _safe_int(required.get("swap_free_mb"))
    swap_hard_gate_value = required.get("swap_free_hard_gate_enabled")
    swap_hard_gate_enabled = (
        bool(swap_hard_gate_value)
        if isinstance(swap_hard_gate_value, bool)
        else swap_required > 0
    )
    swap_advisory = _safe_int(required.get("swap_free_advisory_mb"))
    mem_observed = _safe_int(
        memory.get("mem_preflight_free_mb") or memory.get("mem_available_mb") or memory.get("mem_free_mb")
    )
    swap_observed = _safe_int(memory.get("swap_free_mb"))
    if not mem_required and not swap_required:
        return {}
    mem_key = "mem_preflight_free_mb"
    swap_ok = (not swap_hard_gate_enabled) or swap_observed >= swap_required
    swap_advisory_ok = (not swap_advisory) or swap_observed >= swap_advisory
    legacy_state = "ok" if swap_ok else "below-threshold"
    return {
        "summary": "pass" if mem_observed >= mem_required and swap_ok else "fail",
        mem_key: {
            "observed_mb": mem_observed,
            "required_min_mb": mem_required,
            "ok": mem_observed >= mem_required,
        },
        "swap_free_mb": {
            "observed_mb": swap_observed,
            "required_min_mb": swap_required,
            "hard_required_min_mb": swap_required,
            "advisory_min_mb": swap_advisory,
            "hard_gate_enabled": swap_hard_gate_enabled,
            "ok": swap_ok,
            "advisory_ok": swap_advisory_ok,
            "state": legacy_state,
            "advisory_state": "ok" if swap_advisory_ok else "below-advisory-threshold",
        },
        "swap_policy": {
            "default": "advisory",
            "hard_gate_enabled": swap_hard_gate_enabled,
            "hard_min_swap_free_mb": swap_required,
            "advisory_swap_free_mb": swap_advisory,
            "swap_pressure_advisory": not swap_advisory_ok,
        },
    }


def _swap_free_threshold(data: dict[str, Any]) -> dict[str, Any]:
    value = data.get("swap_free_threshold")
    if isinstance(value, dict):
        return value
    thresholds = _memory_thresholds(data)
    value = thresholds.get("swap_free_mb") if isinstance(thresholds, dict) else {}
    return value if isinstance(value, dict) else {}


def _swap_policy(data: dict[str, Any]) -> dict[str, Any]:
    value = data.get("swap_policy")
    if isinstance(value, dict):
        return value
    thresholds = _memory_thresholds(data)
    value = thresholds.get("swap_policy") if isinstance(thresholds, dict) else {}
    return value if isinstance(value, dict) else {}


def _runtime_freshness(data: dict[str, Any]) -> dict[str, Any]:
    value = data.get("runtime_freshness") or nested(data, "gpu", "diagnostics", "runtime_freshness") or {}
    return value if isinstance(value, dict) else {}


def _observed_executor_marker_ok(runtime_freshness: dict[str, Any]) -> bool:
    markers = runtime_freshness.get("observed_executor_markers") or []
    if not isinstance(markers, list):
        markers = []
    markers = [str(marker) for marker in markers if str(marker)]
    expected = str(runtime_freshness.get("expected_executor_marker") or "")
    if expected:
        return expected in markers
    return bool(markers)


def _observed_icd_marker_ok(runtime_freshness: dict[str, Any]) -> bool:
    markers = runtime_freshness.get("observed_icd_markers") or []
    if not isinstance(markers, list):
        markers = []
    markers = [str(marker) for marker in markers if str(marker)]
    expected = str(runtime_freshness.get("expected_icd_marker") or "")
    if expected:
        return expected in markers
    return bool(markers) if markers else True


def _fresh_feature_chain_icd(runtime_freshness: dict[str, Any]) -> bool:
    markers = runtime_freshness.get("observed_icd_markers") or []
    if not isinstance(markers, list):
        markers = []
    values = [str(runtime_freshness.get("expected_icd_marker") or "")]
    values.extend(str(marker) for marker in markers if str(marker))
    return "vulkan-icd-feature-chain-marker-20260518" in values


def _readiness_false(data: dict[str, Any]) -> bool:
    readiness = data.get("readiness")
    if isinstance(readiness, dict) and readiness.get("ready") is False:
        return True
    if data.get("schema") == "pdocker.llama.gpu.device-readiness.v1" and data.get("ready") is False:
        return True
    return False


def _cpu_comparison_available(data: dict[str, Any]) -> bool:
    cpu = data.get("cpu") or {}
    comparison = data.get("comparison") or {}
    if not isinstance(cpu, dict) or not isinstance(comparison, dict):
        return False
    if cpu.get("reused_cpu_baseline") is True:
        return True
    if cpu.get("tokens_per_second") not in (None, ""):
        try:
            if float(cpu.get("tokens_per_second") or 0) > 0:
                return True
        except (TypeError, ValueError):
            pass
    if comparison.get("cpu_tokens_per_second") not in (None, ""):
        try:
            if float(comparison.get("cpu_tokens_per_second") or 0) > 0:
                return True
        except (TypeError, ValueError):
            pass
    return False


def _config_propagation(data: dict[str, Any]) -> dict[str, Any]:
    value = data.get("config_propagation") or nested(data, "gpu", "diagnostics", "config_propagation") or {}
    return value if isinstance(value, dict) else {}


def _runtime_env_manifest_record(data: dict[str, Any]) -> dict[str, Any]:
    value = nested(data, "gpu", "runtime_env_manifest") or data.get("runtime_env_manifest") or {}
    return value if isinstance(value, dict) else {}


def _config_propagation_missing(data: dict[str, Any], config_propagation: dict[str, Any]) -> bool:
    if not _is_compare_artifact(data):
        return False
    checks = config_propagation.get("checks")
    return not isinstance(checks, list) or not checks


def _config_propagation_manifest_misses(config_propagation: dict[str, Any]) -> list[str]:
    checks = config_propagation.get("checks") or []
    if not isinstance(checks, list):
        return []
    observed = {str(check.get("env")) for check in checks if isinstance(check, dict)}
    return sorted(
        env_name
        for env_name, _field_name in LLAMA_GPU_CONFIG_PROPAGATION_ENV_FIELDS
        if env_name not in observed
    )


def _config_propagation_failed(config_propagation: dict[str, Any]) -> bool:
    if config_propagation.get("summary") == "fail":
        return True
    checks = config_propagation.get("checks") or []
    if not isinstance(checks, list):
        return False
    if config_propagation and _config_propagation_manifest_misses(config_propagation):
        return True
    for check in checks:
        if not isinstance(check, dict):
            return True
        if not check.get("env") or not check.get("executor_field"):
            return True
        if check.get("status") in {"missing-evidence", "mismatch"}:
            return True
        if check.get("expected") is not None:
            observed = check.get("observed_values")
            if not isinstance(observed, list) or not observed:
                return True
            if check.get("status") != "pass":
                return True
    return False


def _unsupported_gpu_work_evidence(data: Any, path: str = "$") -> list[dict[str, str]]:
    """Return bounded structured evidence for unsupported GPU work.

    The compare artifact contains raw log excerpts with human prose, so this
    intentionally looks only at structured status/error/classification fields.
    Unsupported executor/oracle statuses must fail closed instead of being
    accepted by a later passing q6 summary or benchmark section.
    """

    evidence: list[dict[str, str]] = []
    interesting_keys = {
        "status",
        "latest_status",
        "error",
        "blocker_class",
        "classification",
        "diagnostic_interpretation",
    }

    def visit(value: Any, value_path: str) -> None:
        if len(evidence) >= 16:
            return
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = f"{value_path}.{key}"
                if key in interesting_keys and isinstance(child, str):
                    lowered = child.lower()
                    if any(token in lowered for token in UNSUPPORTED_GPU_WORK_TOKENS):
                        evidence.append({"path": child_path, "value": child})
                        if len(evidence) >= 16:
                            return
                visit(child, child_path)
                if len(evidence) >= 16:
                    return
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{value_path}[{index}]")
                if len(evidence) >= 16:
                    return

    visit(data, path)
    return evidence


def _oracle_fail_closed_evidence(data: Any, path: str = "$") -> list[dict[str, str]]:
    """Return bounded evidence that the executor intentionally fail-closed an oracle.

    A post-fail-closed artifact must not be treated as a valid correctness or
    benchmark artifact if any known llama shader required an oracle but the
    executor stopped at cpu-oracle-required.  Look only at structured JSON keys;
    raw log excerpts are intentionally ignored to avoid prose false positives.
    """

    evidence: list[dict[str, str]] = []
    interesting_string_keys = {
        "status",
        "latest_status",
        "stage",
        "fail_stage",
        "error",
        "blocker_class",
        "classification",
        "diagnostic_interpretation",
    }
    fail_closed_tokens = (
        "cpu-oracle-required",
        "oracle_fail_closed",
        "oracle-fail-closed",
        "oracle-pending",
    )

    def add(path: str, value: Any) -> None:
        if len(evidence) < 16:
            evidence.append({"path": path, "value": str(value)})

    def visit(value: Any, value_path: str) -> None:
        if len(evidence) >= 16:
            return
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = f"{value_path}.{key}"
                if key == "oracle_fail_closed" and child is True:
                    add(child_path, child)
                elif key in interesting_string_keys and isinstance(child, str):
                    lowered = child.lower()
                    if any(token in lowered for token in fail_closed_tokens):
                        add(child_path, child)
                if len(evidence) >= 16:
                    return
                visit(child, child_path)
                if len(evidence) >= 16:
                    return
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{value_path}[{index}]")
                if len(evidence) >= 16:
                    return

    visit(data, path)
    return evidence


def _is_finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _speedup_field_status(data: dict[str, Any]) -> dict[str, Any]:
    if not _is_compare_artifact(data):
        return {"required": False, "summary": "not-required", "missing": []}
    missing: list[str] = []
    comparison = data.get("comparison")
    if not isinstance(comparison, dict):
        comparison = {}
        missing.append("comparison")
    for field in ("speedup", "target_tokens_per_second"):
        if not _is_finite_number(comparison.get(field)):
            missing.append(f"comparison.{field}")
    if not isinstance(comparison.get("target_met"), bool):
        missing.append("comparison.target_met")

    bridge = data.get("bridge_overhead_phase")
    if not isinstance(bridge, dict):
        bridge = {}
        missing.append("bridge_overhead_phase")
    for field in ("cpu_tokens_per_second", "gpu_tokens_per_second", "speedup", "target_speedup"):
        if not _is_finite_number(bridge.get(field)):
            missing.append(f"bridge_overhead_phase.{field}")
    if not isinstance(bridge.get("target_met"), bool):
        missing.append("bridge_overhead_phase.target_met")

    return {
        "required": True,
        "summary": "fail" if missing else "pass",
        "missing": sorted(set(missing)),
    }


def _valid_compact_hash(value: Any) -> bool:
    return isinstance(value, str) and bool(COMPACT_HASH_RE.fullmatch(value)) and value.lower() != ZERO_COMPACT_HASH


def _compact_binding_identity(binding: dict[str, Any], path: str) -> dict[str, Any]:
    return {
        "path": path,
        "index": binding.get("index"),
        "binding": binding.get("binding"),
        "alias_rep": binding.get("alias_rep"),
        "offset": binding.get("offset"),
        "size": binding.get("size"),
    }


def _integer_list(value: Any) -> list[int] | None:
    if not isinstance(value, list) or not value:
        return None
    result: list[int] = []
    for item in value:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            return None
    return result


def _f32_samples_by_index(value: Any) -> dict[int, float] | None:
    if not isinstance(value, list) or not value:
        return None
    result: dict[int, float] = {}
    for sample in value:
        if not isinstance(sample, dict):
            return None
        try:
            index = int(sample.get("index"))
            sample_value = sample.get("value")
            if not _is_finite_number(sample_value):
                return None
            result[index] = float(sample_value)
        except (TypeError, ValueError):
            return None
    return result


def _q6_writeback_evidence(q6: Any) -> dict[str, Any]:
    """Validate Q6_K compact writable-binding writeback hash evidence.

    The compare summarizer emits compact binding diagnostics for the Q6_K oracle
    event.  A Q6_K oracle match is only claimable when every writable output
    binding has a non-zero hash after GPU dispatch, a non-zero hash after
    host/container writeback, the hashes match, and the executor explicitly
    marked the writeback as verified.  It must also include row-indexed
    post-dispatch/post-writeback f32 samples tied to the Q6 oracle
    row_window/q6_first_mismatch dst indices.  Generic or exact-index f32 samples
    on q6_writable_bindings alone cannot promote correctness because they do not
    prove the executor sampled the oracle-requested rows.  Missing compact or
    row-indexed fields fail closed as unverified; present mismatches fail closed
    as writeback mismatches.
    """

    required_fields = list(Q6_WRITEBACK_REQUIRED_FIELDS)
    if not isinstance(q6, dict):
        return {
            "summary": "unverified",
            "required_fields": required_fields,
            "missing": ["gpu.diagnostics.q6_workgroup_diagnostics"],
            "mismatches": [],
            "unknown": [],
            "verified_bindings": [],
            "verified_binding_count": 0,
        }

    missing: list[dict[str, Any]] = []
    mismatches: list[dict[str, Any]] = []
    unknown: list[dict[str, Any]] = []
    verified_bindings: list[dict[str, Any]] = []
    row_indexed_details: list[dict[str, Any]] = []

    if q6.get("q6_writeback_verified_all") is not True:
        missing.append({
            "path": "q6_writeback_verified_all",
            "reason": "expected true",
            "value": q6.get("q6_writeback_verified_all"),
        })

    required_row_indices = _integer_list(q6.get("q6_row_indexed_sample_indices"))
    if not required_row_indices:
        missing.append({
            "path": "q6_row_indexed_sample_indices",
            "reason": "expected non-empty oracle row-indexed dst indices",
            "value": q6.get("q6_row_indexed_sample_indices"),
        })

    if q6.get("q6_row_indexed_writeback_verified") is not True:
        missing.append({
            "path": "q6_row_indexed_writeback_verified",
            "reason": "expected true",
            "value": q6.get("q6_row_indexed_writeback_verified"),
        })

    row_evidence = q6.get("q6_row_indexed_writeback_evidence")
    if not isinstance(row_evidence, list) or not row_evidence:
        missing.append({
            "path": "q6_row_indexed_writeback_evidence",
            "reason": "expected non-empty row-indexed writeback diagnostics",
            "value": row_evidence,
        })
        row_evidence = []

    for index, item in enumerate(row_evidence):
        path = f"q6_row_indexed_writeback_evidence[{index}]"
        if not isinstance(item, dict):
            missing.append({"path": path, "reason": "expected object", "value": item})
            continue
        identity = _compact_binding_identity(item, path)
        sample_indices = _integer_list(item.get("q6_sample_indices"))
        if item.get("q6_row_indexed") is not True:
            missing.append(identity | {
                "field": "q6_row_indexed",
                "reason": "expected true",
                "value": item.get("q6_row_indexed"),
            })
        if not sample_indices:
            missing.append(identity | {
                "field": "q6_sample_indices",
                "reason": "expected non-empty row-indexed sample indices",
                "value": item.get("q6_sample_indices"),
            })
        elif required_row_indices and not (set(sample_indices) & set(required_row_indices)):
            missing.append(identity | {
                "field": "q6_sample_indices",
                "reason": "expected overlap with oracle row-indexed dst indices",
                "value": item.get("q6_sample_indices"),
                "required_indices": required_row_indices[:48],
            })
        if item.get("row_indexed_samples_match_oracle") is not True:
            missing.append(identity | {
                "field": "row_indexed_samples_match_oracle",
                "reason": "expected true",
                "value": item.get("row_indexed_samples_match_oracle"),
            })
        dispatch_samples = _f32_samples_by_index(item.get("f32_after_dispatch"))
        writeback_samples = _f32_samples_by_index(item.get("f32_after_writeback"))
        if dispatch_samples is None:
            missing.append(identity | {
                "field": "f32_after_dispatch",
                "reason": "expected non-empty finite row-indexed f32 samples",
                "value": item.get("f32_after_dispatch"),
            })
        if writeback_samples is None:
            missing.append(identity | {
                "field": "f32_after_writeback",
                "reason": "expected non-empty finite row-indexed f32 samples",
                "value": item.get("f32_after_writeback"),
            })
        if sample_indices and dispatch_samples is not None and writeback_samples is not None:
            missing_sample_indices = [
                sample_index
                for sample_index in sample_indices
                if sample_index not in dispatch_samples or sample_index not in writeback_samples
            ]
            if missing_sample_indices:
                missing.append(identity | {
                    "field": "f32_after_dispatch/f32_after_writeback",
                    "reason": "row-indexed sample index missing from dispatch or writeback f32 evidence",
                    "missing_sample_indices": missing_sample_indices[:48],
                })
            for sample_index in sample_indices:
                if sample_index in dispatch_samples and sample_index in writeback_samples:
                    if dispatch_samples[sample_index] != writeback_samples[sample_index]:
                        mismatches.append(identity | {
                            "field": "f32_after_dispatch/f32_after_writeback",
                            "sample_index": sample_index,
                            "dispatch_value": dispatch_samples[sample_index],
                            "writeback_value": writeback_samples[sample_index],
                        })
        row_indexed_details.append(identity | {
            "q6_row_indexed": item.get("q6_row_indexed"),
            "q6_sample_indices": item.get("q6_sample_indices"),
            "row_indexed_samples_match_oracle": item.get("row_indexed_samples_match_oracle"),
        })

    for item in q6.get("q6_writable_writeback_mismatches") or []:
        if isinstance(item, dict):
            mismatches.append(_compact_binding_identity(item, "q6_writable_writeback_mismatches[]") | {
                "gpu_after_dispatch_hash": item.get("gpu_after_dispatch_hash"),
                "fd_after_hash": item.get("fd_after_hash"),
                "writeback_mismatch": item.get("writeback_mismatch"),
            })
        else:
            mismatches.append({"path": "q6_writable_writeback_mismatches[]", "value": item})

    for item in q6.get("q6_writable_writeback_unknown") or []:
        if isinstance(item, dict):
            unknown.append(_compact_binding_identity(item, "q6_writable_writeback_unknown[]") | {
                "gpu_after_dispatch_hash": item.get("gpu_after_dispatch_hash"),
                "fd_after_hash": item.get("fd_after_hash"),
                "writeback_verified": item.get("writeback_verified"),
            })
        else:
            unknown.append({"path": "q6_writable_writeback_unknown[]", "value": item})

    writable_bindings = q6.get("q6_writable_bindings")
    if not isinstance(writable_bindings, list) or not writable_bindings:
        missing.append({
            "path": "q6_writable_bindings",
            "reason": "expected non-empty compact writable binding diagnostics",
        })
        writable_bindings = []

    for index, item in enumerate(writable_bindings):
        path = f"q6_writable_bindings[{index}]"
        if not isinstance(item, dict):
            missing.append({"path": path, "reason": "expected object", "value": item})
            continue
        identity = _compact_binding_identity(item, path)
        if item.get("index") is None:
            missing.append(identity | {"field": "index", "reason": "missing"})
        if item.get("binding") is None:
            missing.append(identity | {"field": "binding", "reason": "missing"})
        if item.get("writable") is not True:
            missing.append(identity | {"field": "writable", "reason": "expected true", "value": item.get("writable")})

        dispatch_hash = item.get("gpu_after_dispatch_hash")
        after_hash = item.get("fd_after_hash")
        dispatch_hash_valid = _valid_compact_hash(dispatch_hash)
        after_hash_valid = _valid_compact_hash(after_hash)
        if not dispatch_hash_valid:
            missing.append(identity | {
                "field": "gpu_after_dispatch_hash",
                "reason": "missing, zero, or invalid compact hash",
                "value": dispatch_hash,
            })
        if not after_hash_valid:
            missing.append(identity | {
                "field": "fd_after_hash",
                "reason": "missing, zero, or invalid compact hash",
                "value": after_hash,
            })
        if item.get("writeback_verified") is not True:
            missing.append(identity | {
                "field": "writeback_verified",
                "reason": "expected true",
                "value": item.get("writeback_verified"),
            })
        if item.get("writeback_mismatch") is True:
            mismatches.append(identity | {
                "gpu_after_dispatch_hash": dispatch_hash,
                "fd_after_hash": after_hash,
                "writeback_mismatch": True,
            })
        elif item.get("writeback_mismatch") not in (False, None):
            missing.append(identity | {
                "field": "writeback_mismatch",
                "reason": "expected false",
                "value": item.get("writeback_mismatch"),
            })
        if dispatch_hash_valid and after_hash_valid and str(dispatch_hash).lower() != str(after_hash).lower():
            mismatches.append(identity | {
                "gpu_after_dispatch_hash": dispatch_hash,
                "fd_after_hash": after_hash,
                "writeback_mismatch": item.get("writeback_mismatch"),
            })
        if (
            item.get("index") is not None
            and item.get("binding") is not None
            and item.get("writable") is True
            and dispatch_hash_valid
            and after_hash_valid
            and str(dispatch_hash).lower() == str(after_hash).lower()
            and item.get("writeback_verified") is True
            and item.get("writeback_mismatch") in (False, None)
        ):
            verified_bindings.append(identity | {
                "gpu_after_dispatch_hash": dispatch_hash,
                "fd_after_hash": after_hash,
            })

    summary = "mismatch" if mismatches else "unverified" if missing or unknown else "pass"
    return {
        "summary": summary,
        "required_fields": required_fields,
        "missing": missing[:16],
        "mismatches": mismatches[:16],
        "unknown": unknown[:16],
        "verified_bindings": verified_bindings[:16],
        "verified_binding_count": len(verified_bindings),
        "row_indexed_required_indices": required_row_indices[:48] if required_row_indices else [],
        "row_indexed_evidence": row_indexed_details[:16],
    }


def _api_prompt_sanity(data: dict[str, Any]) -> dict[str, Any]:
    if not _is_compare_artifact(data):
        return {"required": False, "summary": "not-required", "missing": []}
    missing: list[str] = []
    correctness = nested(data, "gpu", "correctness")
    if not isinstance(correctness, dict) or not correctness:
        return {
            "required": True,
            "summary": "fail",
            "missing": ["gpu.correctness"],
            "required_probe_count": 0,
        }
    if correctness.get("schema") != "pdocker.llama.correctness.v1.compare":
        missing.append("gpu.correctness.schema")
    if not correctness.get("endpoint"):
        missing.append("gpu.correctness.endpoint")
    summary = correctness.get("summary")
    if not isinstance(summary, dict):
        missing.append("gpu.correctness.summary")
        summary = {}
    if summary.get("correctness") not in {"pass", "fail"}:
        missing.append("gpu.correctness.summary.correctness")
    if not isinstance(summary.get("required_failures"), int):
        missing.append("gpu.correctness.summary.required_failures")

    probes = correctness.get("probes")
    if not isinstance(probes, list) or not probes:
        return {
            "required": True,
            "summary": "fail",
            "missing": sorted(set(missing + ["gpu.correctness.probes"])),
            "required_probe_count": 0,
        }
    probe_by_name = {
        str(probe.get("name")): probe
        for probe in probes
        if isinstance(probe, dict) and probe.get("name") is not None
    }
    required_probe_count = sum(1 for probe in probes if isinstance(probe, dict) and probe.get("required") is True)
    if required_probe_count == 0:
        missing.append("gpu.correctness.probes.required")

    for name, expected in REQUIRED_API_PROMPT_PROBES.items():
        probe = probe_by_name.get(name)
        base = f"gpu.correctness.probes[{name}]"
        if not isinstance(probe, dict):
            missing.append(base)
            continue
        if probe.get("required") is not True:
            missing.append(f"{base}.required")
        if probe.get("prompt") != expected["prompt"]:
            missing.append(f"{base}.prompt")
        expected_prefixes = probe.get("expected")
        if not isinstance(expected_prefixes, list) or not all(
            prefix in expected_prefixes for prefix in expected["expected_prefixes"]
        ):
            missing.append(f"{base}.expected")
        status_code = probe.get("status_code")
        if not isinstance(status_code, int) or status_code < 200 or status_code >= 300:
            missing.append(f"{base}.status_code")
        if not isinstance(probe.get("passed"), bool):
            missing.append(f"{base}.passed")
        if not isinstance(probe.get("content"), str):
            missing.append(f"{base}.content")

    return {
        "required": True,
        "summary": "fail" if missing else "pass",
        "missing": sorted(set(missing)),
        "required_probe_count": required_probe_count,
        "correctness": summary.get("correctness"),
        "required_failures": summary.get("required_failures"),
    }


def _service_completion_timeout(data: dict[str, Any]) -> dict[str, Any]:
    readiness = nested(data, "gpu", "service_readiness")
    if not isinstance(readiness, dict) or not readiness:
        return {"summary": "not-recorded", "timeout": False}
    if data.get("schema") == "pdocker.llama.gpu.compare.v1" and nested(data, "gpu", "served") is not True:
        return {"summary": "not-served", "timeout": False}
    if readiness.get("schema") != "pdocker.llama.service-readiness.v1":
        return {"summary": "invalid-schema", "timeout": False, "schema": readiness.get("schema")}
    summary = readiness.get("summary") if isinstance(readiness.get("summary"), dict) else {}
    health = readiness.get("health") if isinstance(readiness.get("health"), dict) else {}
    models = readiness.get("models") if isinstance(readiness.get("models"), dict) else {}
    completion = readiness.get("completion") if isinstance(readiness.get("completion"), dict) else {}
    post_completion_health = (
        readiness.get("post_completion_health")
        if isinstance(readiness.get("post_completion_health"), dict)
        else {}
    )
    health_ok = summary.get("health") == "pass" or health.get("ok") is True
    models_ok = summary.get("models") == "pass" or models.get("ok") is True
    completion_ok = summary.get("completion") == "pass" or completion.get("ok") is True
    completion_passed = completion.get("passed")
    error = str(completion.get("error") or "")
    timed_out = "timed out" in error.lower() or "timeouterror" in error.lower()
    disconnected = "remotedisconnected" in error.lower() or "closed connection" in error.lower()
    completion_failed_after_liveness = bool(health_ok and models_ok and not completion_ok)
    timeout = bool(completion_failed_after_liveness and timed_out)
    summary_value = (
        "timeout" if timeout
        else "disconnected" if completion_failed_after_liveness and disconnected
        else "failed" if completion_failed_after_liveness
        else "ready" if completion_ok
        else "not-ready"
    )
    return {
        "summary": summary_value,
        "timeout": timeout,
        "completion_failed_after_liveness": completion_failed_after_liveness,
        "disconnected": disconnected,
        "health_ok": bool(health_ok),
        "models_ok": bool(models_ok),
        "completion_ok": bool(completion_ok),
        "completion_passed": completion_passed if isinstance(completion_passed, bool) else None,
        "completion_content_excerpt": completion.get("content_excerpt") or completion.get("content"),
        "health_status": health.get("status") or summary.get("health"),
        "health_duration_ms": health.get("duration_ms"),
        "health_error": health.get("error"),
        "models_status": models.get("status") or summary.get("models"),
        "models_duration_ms": models.get("duration_ms"),
        "models_error": models.get("error"),
        "completion_error": error,
        "completion_status": completion.get("status") or summary.get("completion"),
        "completion_duration_ms": completion.get("duration_ms"),
        "completion_timeout_sec": completion.get("timeout_sec") or readiness.get("completion_timeout_sec"),
        "post_completion_health_ok": post_completion_health.get("ok"),
        "post_completion_health_status": post_completion_health.get("status"),
        "post_completion_health_error": post_completion_health.get("error"),
        "runtime_freshness": _runtime_freshness(data),
    }


def _api_executor_reconciliation(data: dict[str, Any]) -> dict[str, Any]:
    """Validate API/output-to-executor dispatch reconciliation evidence.

    Wrong deterministic /completion output is only actionable as GPU
    correctness after the artifact proves the HTTP/API prompt response was
    reconciled to the executor dispatch evidence.  Missing evidence fails
    closed; duplicate or unmatched evidence is ambiguous; explicit failures,
    dispatch hash disagreements, or mismatch statuses are mismatches.
    """

    reconciliation = nested(data, "gpu", "diagnostics", "api_executor_reconciliation")
    if not isinstance(reconciliation, dict) or not reconciliation:
        return {
            "summary": "missing",
            "missing": ["gpu.diagnostics.api_executor_reconciliation"],
            "ambiguous": [],
            "mismatches": [],
        }

    raw_summary = reconciliation.get("summary")
    if raw_summary in (None, ""):
        return {
            "summary": "missing",
            "missing": ["gpu.diagnostics.api_executor_reconciliation.summary"],
            "ambiguous": [],
            "mismatches": [],
            "evidence": reconciliation,
        }
    summary = str(raw_summary).lower()

    ambiguous: list[dict[str, Any]] = []
    mismatches: list[dict[str, Any]] = []

    def add_ambiguous(path: str, reason: str, value: Any) -> None:
        if len(ambiguous) < 16:
            ambiguous.append({"path": path, "reason": reason, "value": value})

    def add_mismatch(path: str, reason: str, value: Any) -> None:
        if len(mismatches) < 16:
            mismatches.append({"path": path, "reason": reason, "value": value})

    def truthy_evidence(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, int) and not isinstance(value, bool):
            return value > 0
        if isinstance(value, float):
            return value > 0
        if isinstance(value, (list, tuple, set, dict)):
            return len(value) > 0
        if isinstance(value, str):
            lowered = value.strip().lower()
            return bool(lowered and lowered not in {"0", "false", "none", "no", "not-recorded"})
        return value is not None

    explicit_hash_pairs = (
        ("api_canonical_hash", "executor_canonical_hash"),
        ("api_dispatch_canonical_hash", "executor_dispatch_canonical_hash"),
        ("api_canonical_request_hash", "executor_canonical_request_hash"),
        ("api_output_canonical_hash", "executor_output_canonical_hash"),
        ("api_completion_canonical_hash", "executor_completion_canonical_hash"),
        ("api_canonical_dispatch_hash", "executor_canonical_dispatch_hash"),
        ("api_canonical_hash", "dispatch_canonical_hash"),
        ("api_canonical_hash", "canonical_dispatch_hash"),
        ("api_canonical_hash", "executor_dispatch_canonical_hash"),
        ("completion_canonical_hash", "dispatch_canonical_hash"),
        ("api_completion_canonical_hash", "dispatch_canonical_hash"),
    )

    def reconciliation_has_hash_pair(value: Any) -> bool:
        if isinstance(value, dict):
            for left_key, right_key in explicit_hash_pairs:
                left = value.get(left_key)
                right = value.get(right_key)
                if isinstance(left, str) and left and isinstance(right, str) and right:
                    return True
            return any(reconciliation_has_hash_pair(child) for child in value.values())
        if isinstance(value, list):
            return any(reconciliation_has_hash_pair(child) for child in value)
        return False

    def reconciliation_has_promoting_proof(value: Any) -> bool:
        if isinstance(value, dict):
            proof_strength = str(value.get("proof_strength") or "").strip().lower()
            hash_algorithm = str(value.get("hash_algorithm") or "").strip().lower()
            raw_fields = value.get("canonical_raw_fields_present")
            if proof_strength in {"full", "sha256", "sha-256", "collision-resistant"}:
                return True
            if hash_algorithm in {"sha256", "sha-256"}:
                return True
            if raw_fields is True:
                return True
            return any(reconciliation_has_promoting_proof(child) for child in value.values())
        if isinstance(value, list):
            return any(reconciliation_has_promoting_proof(child) for child in value)
        return False

    def compare_hash_pair(path: str, item: dict[str, Any], left_key: str, right_key: str) -> None:
        left = item.get(left_key)
        right = item.get(right_key)
        if isinstance(left, str) and left and isinstance(right, str) and right:
            if left.lower() != right.lower():
                add_mismatch(
                    path,
                    "canonical hash mismatch",
                    {left_key: left, right_key: right},
                )

    def inspect(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                lowered_key = str(key).lower()
                child_path = f"{path}.{key}"
                if ("duplicate" in lowered_key or "unmatched" in lowered_key) and truthy_evidence(child):
                    add_ambiguous(child_path, "duplicate or unmatched reconciliation evidence", child)
                if lowered_key == "match_status" or lowered_key.endswith("_match_status"):
                    lowered_value = str(child).lower()
                    if lowered_value in {"mismatch", "hash-mismatch", "canonical-mismatch"}:
                        add_mismatch(child_path, "match_status mismatch", child)
                inspect(child, child_path)

            for left_key, right_key in explicit_hash_pairs:
                compare_hash_pair(path, value, left_key, right_key)

        elif isinstance(value, list):
            for index, child in enumerate(value):
                inspect(child, f"{path}[{index}]")

    inspect(reconciliation, "gpu.diagnostics.api_executor_reconciliation")

    if summary == "pass" and not reconciliation_has_hash_pair(reconciliation):
        add_ambiguous(
            "gpu.diagnostics.api_executor_reconciliation",
            "summary pass lacks substantive one-to-one reconciliation evidence",
            reconciliation,
        )
    if summary == "pass" and reconciliation_has_hash_pair(reconciliation) and not reconciliation_has_promoting_proof(reconciliation):
        add_ambiguous(
            "gpu.diagnostics.api_executor_reconciliation",
            "summary pass is diagnostic-only; promoting reconciliation requires SHA-256/full proof or canonical raw fields",
            reconciliation,
        )

    if summary in {"ambiguous", "inconclusive", "duplicate", "unmatched"}:
        add_ambiguous("gpu.diagnostics.api_executor_reconciliation.summary", "ambiguous summary", raw_summary)
    if summary in {"fail", "failed", "mismatch", "hash-mismatch", "canonical-mismatch"}:
        add_mismatch("gpu.diagnostics.api_executor_reconciliation.summary", "failing summary", raw_summary)

    if ambiguous:
        result_summary = "ambiguous"
    elif mismatches:
        result_summary = "mismatch"
    elif summary == "pass":
        result_summary = "pass"
    else:
        result_summary = "ambiguous"
        add_ambiguous("gpu.diagnostics.api_executor_reconciliation.summary", "unrecognized summary", raw_summary)

    return {
        "summary": result_summary,
        "missing": [],
        "ambiguous": ambiguous[:16],
        "mismatches": mismatches[:16],
        "evidence": reconciliation,
    }


PRE_HTTP_GPU_BLOCKER_CLASSIFICATIONS = {
    "vulkan_pipeline_feature": "vulkan-pipeline-feature",
    "vulkan_queue_submit_feature": "vulkan-queue-submit-feature",
    "vulkan_generic_spirv_dispatch": "vulkan-generic-spirv-dispatch",
    "vulkan_buffer_allocation": "vulkan-buffer-allocation",
    "vulkan_buffer_range_accounting": "vulkan-buffer-range-accounting",
    "vulkan_device_discovery": "vulkan-device-discovery",
    "runtime_memory_pressure": "runtime-memory-pressure",
}


def _pre_http_gpu_blocker(data: dict[str, Any], diagnostics: dict[str, Any]) -> dict[str, Any]:
    """Classify structured GPU setup blockers before requiring HTTP probes.

    When the forced Vulkan container exits before the llama server opens HTTP,
    /completion prompt evidence is necessarily absent.  The artifact should
    report the earlier structured GPU blocker instead of a misleading
    api-prompt-sanity-missing classification.
    """

    if data.get("schema") != "pdocker.llama.gpu.compare.v1":
        return {}
    if nested(data, "gpu", "served") is True:
        return {}
    q6 = diagnostics.get("q6_workgroup_diagnostics")
    if isinstance(q6, dict):
        try:
            if int(q6.get("event_count", 0)) > 0:
                return {}
        except (TypeError, ValueError):
            pass
    blocker_class = str(diagnostics.get("blocker_class") or "")
    classification = PRE_HTTP_GPU_BLOCKER_CLASSIFICATIONS.get(blocker_class)
    if not classification:
        return {}
    return {
        "classification": classification,
        "gpu_blocker_class": blocker_class,
        "gpu_blocker_detail": diagnostics.get("blocker_detail") or data.get("next_blocker") or "",
        "next_action": data.get("next_action") or data.get("next_blocker") or "fix the structured GPU setup blocker and rerun",
    }


def _pre_http_failure_evidence(diagnostics: dict[str, Any]) -> dict[str, Any]:
    """Return bounded first-failure evidence for pre-HTTP GPU blockers.

    A `vulkan-pipeline-feature` report is only useful if it names the exact
    executor event that stopped the run.  Keep this payload small and stable so
    CI, humans, and future agents can tell whether Q6_K was never reached or a
    later correctness gate actually failed.
    """

    generic = diagnostics.get("generic_spirv_dispatch") if isinstance(diagnostics, dict) else {}
    if not isinstance(generic, dict):
        generic = {}
    failed_events = generic.get("failed_events")
    if not isinstance(failed_events, list):
        failed_events = []
    failed_dicts = [event for event in failed_events if isinstance(event, dict)]
    event = failed_dicts[0] if failed_dicts else {}
    q6 = diagnostics.get("q6_workgroup_diagnostics") if isinstance(diagnostics, dict) else {}
    if not isinstance(q6, dict):
        q6 = {}

    def pick(source: dict[str, Any], *keys: str) -> dict[str, Any]:
        return {key: source.get(key) for key in keys if key in source}

    pipeline_key = event.get("pipeline_key")
    if not isinstance(pipeline_key, dict):
        pipeline_key = {}

    return {
        "generic_spirv_attempted": generic.get("attempted") is True,
        "failed_event_count": len(failed_dicts),
        "failure_event": pick(
            event,
            "stage",
            "error",
            "vk_result",
            "spirv_hash",
            "shader_bytes",
            "entry",
            "bindings",
            "dispatch",
            "push_bytes",
            "requested_feature_mask",
            "requested_feature_mask_present",
            "strict_passthrough",
            "spirv_required_feature_mask",
            "spirv_requested_feature_missing_mask",
            "spirv_requested_feature_mismatches",
            "spirv_feature_requirements",
            "spirv_feature_mismatch",
            "spirv_feature_mismatches",
            "android_vulkan_features",
            "android_vulkan_enabled_features",
            "spirv_capabilities",
        ),
        "pipeline_key": pick(
            pipeline_key,
            "spirv_hash",
            "spec_hash",
            "layout_bindings",
            "descriptor_sets",
            "push_bytes",
        ),
        "llama_throw": generic.get("llama_throw") or "",
        "q6_reachability": {
            "event_count": q6.get("event_count", 0),
            "blocker_class": q6.get("blocker_class") or "not-reached",
            "diagnostic_interpretation": q6.get("diagnostic_interpretation") or "",
        },
    }


def _numeric_close_to_zero(value: Any, tolerance: float = 1.0e-3) -> bool:
    return _is_finite_number(value) and abs(float(value)) <= tolerance


def _q6_local_size_resolved(q6: Any) -> list[int] | None:
    if not isinstance(q6, dict):
        return None
    return _integer_list(q6.get("local_size_resolved") or q6.get("local_size"))


def _q6_safe_kernel_enabled(q6: Any) -> bool:
    return (
        isinstance(q6, dict)
        and (
            q6.get("q6k_safe_kernel") is True
            or str(q6.get("latest_spirv_hash") or "").lower() == "0x7ec0292e948c9b41"
        )
    )


def _q6_expected_local_size(q6: Any) -> list[int]:
    return [1, 1, 1] if _q6_safe_kernel_enabled(q6) else [32, 2, 1]


def _q6_required_local_size_clear(q6: Any) -> bool:
    return _q6_local_size_resolved(q6) == _q6_expected_local_size(q6)


def _q6_workgroup_shape_blocked(q6: Any) -> bool:
    if not isinstance(q6, dict):
        return False
    if q6.get("local_size_consistent") is False:
        return True
    local_size = _q6_local_size_resolved(q6)
    q6_local_size = _integer_list(q6.get("q6_local_size"))
    if q6_local_size is not None and local_size is not None and q6_local_size != local_size:
        return True
    return not _q6_required_local_size_clear(q6)


def _q6_shader_like_interpretation(q6: Any) -> dict[str, Any]:
    """Explain whether the Q6 shader-like CPU oracle cleared.

    For the observed Q6_K kernel with local_size=[32,2,1], the 32-lane
    shader-like CPU oracle is the proven same-row arithmetic check.  The
    flattened 64-lane diagnostic spans the Y dimension and is useful context,
    but it is not a fail-closed requirement for clearing CPU-side arithmetic.
    """

    if not isinstance(q6, dict):
        return {
            "q6_shader_like_oracle_cleared": False,
            "q6_shader_like_64_required": True,
            "q6_shader_like_clear_basis": [],
            "q6_shader_like_64_interpretation": "no-q6-diagnostics",
        }
    local_size = _q6_local_size_resolved(q6)
    safe_kernel = _q6_safe_kernel_enabled(q6)
    sixty_four_required = (not safe_kernel) and local_size != [32, 2, 1]
    thirty_two_clear = _numeric_close_to_zero(q6.get("q6_shader_like_abs_delta"))
    sixty_four_clear = _numeric_close_to_zero(q6.get("q6_shader_like_64_abs_delta"))
    cleared = q6.get("latest_status") == "mismatch" and thirty_two_clear and (
        not sixty_four_required or sixty_four_clear
    )
    basis = ["q6_shader_like_abs_delta"] if thirty_two_clear else []
    if not sixty_four_required:
        if safe_kernel:
            basis.extend([
                "q6k_safe_kernel=true",
                "local_size_resolved=[1,1,1]",
                "q6_shader_like_64_abs_delta=diagnostic-only",
            ])
        else:
            basis.extend([
                "local_size_resolved=[32,2,1]",
                "q6_shader_like_64_abs_delta=diagnostic-only",
            ])
    elif sixty_four_clear:
        basis.append("q6_shader_like_64_abs_delta")
    return {
        "q6_shader_like_oracle_cleared": cleared,
        "q6_shader_like_64_required": sixty_four_required,
        "q6_shader_like_clear_basis": basis,
        "q6_shader_like_64_interpretation": (
            "diagnostic-only-for-q6k-safe-kernel; single-invocation replacement is an explicit bridge diagnostic"
            if safe_kernel
            else
            "diagnostic-only-for-32x2x1; flattened 64 tids are not required same-row oracle lanes"
            if not sixty_four_required
            else "required-for-non-32x2x1-local-size"
        ),
    }


def _q6_output_layout_probe(q6: Any) -> dict[str, Any]:
    if not isinstance(q6, dict):
        return {"summary": "not-run", "samples": []}
    probe = q6.get("q6_output_layout_probe")
    if not isinstance(probe, dict):
        return {
            "summary": q6.get("q6_output_layout_probe_summary") or "not-run",
            "samples": [],
        }
    samples = probe.get("samples")
    if not isinstance(samples, list):
        samples = []
    return {
        "summary": str(probe.get("summary") or q6.get("q6_output_layout_probe_summary") or "not-run"),
        "samples": samples,
        "canonical_match_count": probe.get("canonical_match_count"),
        "found_elsewhere_count": probe.get("found_elsewhere_count"),
        "mismatch_count": probe.get("mismatch_count"),
        "consistent_relative_offset": probe.get("consistent_relative_offset"),
        "relative_offset": probe.get("relative_offset"),
        "search_base_index": probe.get("search_base_index"),
        "search_float_count": probe.get("search_float_count"),
    }


def _q6_output_layout_fixed_offset_rejected(probe: dict[str, Any]) -> bool:
    """Return True when a broad probe weakens the fixed-layout hypothesis.

    A few value-only nearest-neighbor hits can occur by chance in a 4096-float
    output scan.  Treat the output-layout hypothesis as rejected only after a
    broad probe covers many mismatched rows, finds at least one elsewhere value,
    and those hits do not share one relative offset.  This keeps the classifier
    fail-closed: single-hit or short probes remain inconclusive.
    """
    if probe.get("summary") != "canonical-mismatch-inconclusive":
        return False
    try:
        mismatch_count = int(probe.get("mismatch_count") or 0)
        found_elsewhere_count = int(probe.get("found_elsewhere_count") or 0)
    except (TypeError, ValueError):
        return False
    return (
        mismatch_count >= 16
        and found_elsewhere_count > 0
        and probe.get("consistent_relative_offset") is False
    )


def _q6_row_provenance_probe(q6: Any) -> dict[str, Any]:
    if not isinstance(q6, dict):
        return {"summary": "not-run", "samples": []}
    probe = q6.get("q6_row_provenance_probe")
    if not isinstance(probe, dict):
        return {
            "summary": q6.get("q6_row_provenance_probe_summary") or "not-run",
            "samples": [],
        }
    samples = probe.get("samples")
    if not isinstance(samples, list):
        samples = []
    return {
        "summary": str(probe.get("summary") or q6.get("q6_row_provenance_probe_summary") or "not-run"),
        "samples": samples,
        "same_row_match_count": probe.get("same_row_match_count"),
        "other_row_match_count": probe.get("other_row_match_count"),
        "mismatch_count": probe.get("mismatch_count"),
        "consistent_row_delta": probe.get("consistent_row_delta"),
        "row_delta": probe.get("row_delta"),
        "search_row_base": probe.get("search_row_base"),
        "search_row_count": probe.get("search_row_count"),
    }


def _q6_partial_signature_probe(q6: Any) -> dict[str, Any]:
    if not isinstance(q6, dict):
        return {"summary": "not-run", "samples": []}
    probe = q6.get("q6_partial_signature_probe")
    if not isinstance(probe, dict):
        return {
            "summary": q6.get("q6_partial_signature_probe_summary") or "not-run",
            "samples": [],
        }
    samples = probe.get("samples")
    if not isinstance(samples, list):
        samples = []
    return {
        "summary": str(probe.get("summary") or q6.get("q6_partial_signature_probe_summary") or "not-run"),
        "samples": samples,
        "mismatch_count": probe.get("mismatch_count"),
        "local_y_partial_match_count": probe.get("local_y_partial_match_count"),
        "lane_partial_match_count": probe.get("lane_partial_match_count"),
    }


def _pre_http_feature_evidence_missing(
    blocker: dict[str, Any],
    evidence: dict[str, Any],
    runtime_freshness: dict[str, Any],
) -> list[str]:
    if blocker.get("classification") != "vulkan-pipeline-feature":
        return []
    if not _fresh_feature_chain_icd(runtime_freshness):
        return []
    failure_event = evidence.get("failure_event")
    if not isinstance(failure_event, dict):
        failure_event = {}
    required = [
        "spirv_required_feature_mask",
        "spirv_requested_feature_missing_mask",
        "spirv_requested_feature_mismatches",
        "android_vulkan_features",
        "android_vulkan_enabled_features",
    ]
    return [key for key in required if key not in failure_event]


def _claim_base(
    classification: str,
    *,
    next_action: str,
    device_memory_blocked: bool = False,
    device_actions: list[Any] | None = None,
    diagnostic_commands: list[Any] | None = None,
    cleanup_commands: list[Any] | None = None,
    pdocker_memory_diagnostics: dict[str, Any] | None = None,
    memory: dict[str, Any] | None = None,
    memory_thresholds: dict[str, Any] | None = None,
    swap_free_threshold: dict[str, Any] | None = None,
    swap_policy: dict[str, Any] | None = None,
    runtime_freshness: dict[str, Any] | None = None,
    runtime_env_manifest: dict[str, Any] | None = None,
    responsibility_boundary: str = "pre-q6",
) -> dict[str, Any]:
    swap_threshold = swap_free_threshold or {}
    return {
        "classification": classification,
        "terminal": False,
        "device_memory_blocked": device_memory_blocked,
        "correctness_claim_allowed": False,
        "benchmark_claim_allowed": False,
        "next_action": next_action,
        "device_actions": device_actions or [],
        "diagnostic_commands": diagnostic_commands or [],
        "cleanup_commands": cleanup_commands or [],
        "pdocker_memory_diagnostics": pdocker_memory_diagnostics or {},
        "memory": memory or {},
        "memory_thresholds": memory_thresholds or {},
        "swap_free_threshold": swap_threshold,
        "swap_free_threshold_state": swap_threshold.get("state") if isinstance(swap_threshold, dict) else None,
        "swap_policy": swap_policy or {},
        "runtime_freshness": runtime_freshness or {},
        "runtime_env_manifest": runtime_env_manifest or {},
        "responsibility_boundary": responsibility_boundary,
    }


def classify(data: dict[str, Any]) -> dict[str, Any]:
    error = str(data.get("error") or "")
    if error in MEMORY_ERRORS:
        return _claim_base(
            error,
            device_memory_blocked=True,
            next_action=data.get("next_blocker") or "recover Android memory and rerun",
            device_actions=_memory_device_actions(data),
            diagnostic_commands=_memory_diagnostic_commands(data),
            cleanup_commands=_memory_cleanup_commands(data),
            pdocker_memory_diagnostics=_memory_diagnostics(data),
            memory=data.get("memory") or {},
            memory_thresholds=_memory_thresholds(data),
            swap_free_threshold=_swap_free_threshold(data),
            swap_policy=_swap_policy(data),
            responsibility_boundary="device-memory-readiness",
        )

    if _readiness_false(data):
        return _claim_base(
            "readiness-blocked",
            next_action="do not start or accept a GPU run until android-llama-gpu-readiness reports ready=true",
            device_actions=nested(data, "readiness", "device_actions") or data.get("device_actions") or [],
            memory=nested(data, "readiness", "memory") or data.get("memory") or {},
            responsibility_boundary="device-memory-readiness",
        )

    diagnostics = nested(data, "gpu", "diagnostics") or {}
    q6 = diagnostics.get("q6_workgroup_diagnostics") or {}
    correctness_summary = nested(data, "gpu", "correctness", "summary") or {}
    correctness = correctness_summary.get("correctness", "not-run")
    comparison = data.get("comparison") or {}
    runtime_freshness = _runtime_freshness(data)
    runtime_env_manifest = _runtime_env_manifest_record(data)

    completion_readiness = _service_completion_timeout(data)
    if completion_readiness.get("completion_failed_after_liveness") is True:
        completion_classification = (
            "llama-completion-timeout"
            if completion_readiness.get("timeout") is True
            else "llama-completion-disconnected"
            if completion_readiness.get("disconnected") is True
            else "llama-completion-failed"
        )
        return _claim_base(
            completion_classification,
            next_action="inspect container log, llama workspace log, and executor dispatch evidence; HTTP /health and /v1/models passed but deterministic /completion did not return a valid response",
            runtime_freshness=runtime_freshness,
            runtime_env_manifest=runtime_env_manifest,
            responsibility_boundary="service-readiness",
        ) | {
            "service_readiness": completion_readiness,
            "runtime_env": nested(data, "gpu", "runtime_env") or {},
        }

    if (
        completion_readiness.get("health_ok") is True
        and completion_readiness.get("models_ok") is True
        and completion_readiness.get("completion_ok") is True
        and completion_readiness.get("completion_passed") is False
    ):
        api_executor_reconciliation = _api_executor_reconciliation(data)
        reconciliation_summary = api_executor_reconciliation.get("summary")
        if reconciliation_summary == "missing":
            return _claim_base(
                "api-executor-reconciliation-missing",
                next_action="rerun compare with API-to-executor reconciliation evidence before assigning wrong deterministic /completion output to GPU correctness",
                runtime_freshness=runtime_freshness,
                runtime_env_manifest=runtime_env_manifest,
                responsibility_boundary="api-executor-reconciliation",
            ) | {
                "service_readiness": completion_readiness,
                "api_executor_reconciliation": api_executor_reconciliation,
                "runtime_env": nested(data, "gpu", "runtime_env") or {},
            }
        if reconciliation_summary == "ambiguous":
            return _claim_base(
                "api-executor-reconciliation-ambiguous",
                next_action="rerun compare until API prompt/output evidence maps to exactly one executor dispatch with no duplicate or unmatched reconciliation evidence",
                runtime_freshness=runtime_freshness,
                runtime_env_manifest=runtime_env_manifest,
                responsibility_boundary="api-executor-reconciliation",
            ) | {
                "service_readiness": completion_readiness,
                "api_executor_reconciliation": api_executor_reconciliation,
                "runtime_env": nested(data, "gpu", "runtime_env") or {},
            }
        if reconciliation_summary == "mismatch":
            return _claim_base(
                "api-executor-reconciliation-mismatch",
                next_action="fix API-to-executor dispatch reconciliation before interpreting the wrong deterministic /completion output as GPU correctness",
                runtime_freshness=runtime_freshness,
                runtime_env_manifest=runtime_env_manifest,
                responsibility_boundary="api-executor-reconciliation",
            ) | {
                "service_readiness": completion_readiness,
                "api_executor_reconciliation": api_executor_reconciliation,
                "runtime_env": nested(data, "gpu", "runtime_env") or {},
            }
        if not _observed_executor_marker_ok(runtime_freshness):
            return _claim_base(
                "executor-marker-not-observed",
                next_action="rerun compare with fresh GPU executor evidence; reconciled wrong-output claims require the expected executor marker",
                runtime_freshness=runtime_freshness,
                runtime_env_manifest=runtime_env_manifest,
                responsibility_boundary="runtime-freshness",
            ) | {
                "observed_service_failure": "llama-completion-wrong-output",
                "service_readiness": completion_readiness,
                "api_executor_reconciliation": api_executor_reconciliation,
                "runtime_env": nested(data, "gpu", "runtime_env") or {},
            }
        if not _observed_icd_marker_ok(runtime_freshness):
            return _claim_base(
                "icd-marker-not-observed",
                next_action="rerun compare after installing an APK with the expected Vulkan ICD marker; reconciled wrong-output claims require fresh ICD evidence",
                runtime_freshness=runtime_freshness,
                runtime_env_manifest=runtime_env_manifest,
                responsibility_boundary="runtime-freshness",
            ) | {
                "observed_service_failure": "llama-completion-wrong-output",
                "service_readiness": completion_readiness,
                "api_executor_reconciliation": api_executor_reconciliation,
                "runtime_env": nested(data, "gpu", "runtime_env") or {},
            }
        return _claim_base(
            "llama-completion-wrong-output",
            next_action="keep the current image/model/prompt fixed and inspect GPU numeric/layout/readback evidence; deterministic /completion returned an HTTP response but failed the required prompt check and API-to-executor reconciliation passed",
            runtime_freshness=runtime_freshness,
            runtime_env_manifest=runtime_env_manifest,
            responsibility_boundary="reconciled-gpu-correctness",
        ) | {
            "service_readiness": completion_readiness,
            "api_executor_reconciliation": api_executor_reconciliation,
            "runtime_env": nested(data, "gpu", "runtime_env") or {},
        }

    if not _observed_executor_marker_ok(runtime_freshness):
        return _claim_base(
            "executor-marker-not-observed",
            next_action="rerun compare with fresh GPU executor evidence; compare/benchmark claims require the expected executor marker",
            runtime_freshness=runtime_freshness,
            runtime_env_manifest=runtime_env_manifest,
            responsibility_boundary="runtime-freshness",
        )

    if not _observed_icd_marker_ok(runtime_freshness):
        return _claim_base(
            "icd-marker-not-observed",
            next_action="rerun compare after installing an APK with the expected Vulkan ICD marker; pre-Q6 and Q6 conclusions require fresh ICD evidence",
            runtime_freshness=runtime_freshness,
            runtime_env_manifest=runtime_env_manifest,
            responsibility_boundary="runtime-freshness",
        )

    pre_http_gpu_blocker = _pre_http_gpu_blocker(data, diagnostics)
    if pre_http_gpu_blocker:
        evidence = _pre_http_failure_evidence(diagnostics)
        feature_evidence_missing = _pre_http_feature_evidence_missing(
            pre_http_gpu_blocker,
            evidence,
            runtime_freshness,
        )
        if feature_evidence_missing:
            return _claim_base(
                "vulkan-pipeline-feature-evidence-missing",
                next_action=(
                    "rerun compare with fresh ICD/executor evidence that includes SPIR-V required/requested "
                    "feature masks and Android enabled feature bits before accepting a pre-Q6 feature conclusion"
                ),
                runtime_freshness=runtime_freshness,
                runtime_env_manifest=runtime_env_manifest,
                responsibility_boundary="gpu-setup-evidence",
            ) | {
                "missing_pre_http_feature_evidence": feature_evidence_missing,
                "pre_http_failure_evidence": evidence,
            }
        return _claim_base(
            pre_http_gpu_blocker["classification"],
            next_action=str(pre_http_gpu_blocker["next_action"]),
            runtime_freshness=runtime_freshness,
            runtime_env_manifest=runtime_env_manifest,
            responsibility_boundary="gpu-setup",
        ) | {
            "gpu_blocker_class": pre_http_gpu_blocker["gpu_blocker_class"],
            "gpu_blocker_detail": pre_http_gpu_blocker["gpu_blocker_detail"],
            "pre_http_failure_evidence": evidence,
            "config_propagation": _config_propagation(data),
        }

    config_propagation = _config_propagation(data)
    config_propagation_missing = _config_propagation_missing(data, config_propagation)
    if config_propagation_missing or _config_propagation_failed(config_propagation):
        manifest_misses = _config_propagation_manifest_misses(config_propagation)
        return _claim_base(
            "config-propagation-mismatch",
            next_action=(
                data.get("next_action")
                or "fix GPU diagnostic environment propagation before accepting compare, correctness, or benchmark claims"
            ),
            runtime_freshness=runtime_freshness,
            runtime_env_manifest=runtime_env_manifest,
            responsibility_boundary="env-propagation",
        ) | {
            "config_propagation": config_propagation,
            "config_propagation_missing": config_propagation_missing,
            "config_propagation_manifest_misses": manifest_misses,
            "required_config_propagation_envs": [
                env_name for env_name, _field_name in LLAMA_GPU_CONFIG_PROPAGATION_ENV_FIELDS
            ],
        }

    oracle_fail_closed_evidence = _oracle_fail_closed_evidence(data)
    if oracle_fail_closed_evidence:
        return _claim_base(
            "oracle-fail-closed",
            next_action=(
                data.get("next_action")
                or "fix the required CPU oracle coverage or disable the unsafe GPU work before accepting compare, correctness, or benchmark claims"
            ),
            runtime_freshness=runtime_freshness,
            runtime_env_manifest=runtime_env_manifest,
            responsibility_boundary="oracle-coverage",
        ) | {
            "oracle_fail_closed_evidence": oracle_fail_closed_evidence,
            "config_propagation": config_propagation,
        }

    unsupported_evidence = _unsupported_gpu_work_evidence(data)
    if unsupported_evidence:
        return _claim_base(
            "unsupported-gpu-work-accepted",
            next_action=(
                data.get("next_action")
                or "fail or gate unsupported GPU executor/oracle work before accepting correctness or benchmark claims"
            ),
            runtime_freshness=runtime_freshness,
            runtime_env_manifest=runtime_env_manifest,
            responsibility_boundary="unsupported-gpu-work",
        ) | {
            "unsupported_gpu_work_evidence": unsupported_evidence,
            "config_propagation": config_propagation,
        }

    q6_evidence_reached = False
    if isinstance(q6, dict):
        try:
            q6_evidence_reached = int(q6.get("event_count", 0)) > 0
        except (TypeError, ValueError):
            q6_evidence_reached = False
    q6_oracle_evidence = q6_evidence_reached and q6.get("latest_status") in {"match", "mismatch"}

    api_prompt_sanity = _api_prompt_sanity(data)
    if api_prompt_sanity.get("summary") == "fail" and not q6_oracle_evidence:
        return _claim_base(
            "api-prompt-sanity-missing",
            next_action=(
                data.get("next_action")
                or "rerun the standard /completion prompt probes unchanged; do not accept GPU claims without HTTP/API prompt evidence"
            ),
            runtime_freshness=runtime_freshness,
            runtime_env_manifest=runtime_env_manifest,
            responsibility_boundary="api-prompt-sanity",
        ) | {
            "api_prompt_sanity": api_prompt_sanity,
            "config_propagation": config_propagation,
        }

    speedup_fields = _speedup_field_status(data)
    if speedup_fields.get("summary") == "fail" and not q6_oracle_evidence:
        return _claim_base(
            "speedup-fields-missing",
            next_action=(
                data.get("next_action")
                or "rerun compare so comparison and bridge_overhead_phase speedup fields are present before claiming correctness or performance"
            ),
            runtime_freshness=runtime_freshness,
            runtime_env_manifest=runtime_env_manifest,
            responsibility_boundary="speedup-evidence",
        ) | {
            "speedup_fields": speedup_fields,
            "api_prompt_sanity": api_prompt_sanity,
            "config_propagation": config_propagation,
        }

    q6_writeback_evidence = _q6_writeback_evidence(q6)
    q6_shader_like = _q6_shader_like_interpretation(q6)
    q6_output_layout = _q6_output_layout_probe(q6)
    q6_row_provenance = _q6_row_provenance_probe(q6)
    q6_partial_signature = _q6_partial_signature_probe(q6)
    q6_native_vs_writeback_split = (
        q6.get("q6_native_vs_writeback_split")
        if isinstance(q6.get("q6_native_vs_writeback_split"), dict)
        else {}
    )
    q6_blocker_class = None
    if not q6:
        classification = "q6-not-reached"
        responsibility_boundary = "q6-not-reached"
        next_action = data.get("next_action") or "collect an ngl=1 artifact with Q6_K oracle enabled"
    elif _q6_workgroup_shape_blocked(q6):
        classification = "q6-workgroup-shape-blocker"
        responsibility_boundary = "q6-local-size"
        next_action = (
            "fix Q6_K local-size propagation/materialization to the expected "
            f"{_q6_expected_local_size(q6)} workgroup shape"
        )
    elif q6_writeback_evidence.get("summary") == "mismatch":
        classification = "q6-writeback-mismatch"
        responsibility_boundary = "q6-writeback"
        q6_blocker_class = "writeback"
        next_action = "fix Q6_K writable output writeback before accepting correctness or benchmark claims"
    elif q6_writeback_evidence.get("summary") != "pass":
        classification = "q6-writeback-unverified"
        responsibility_boundary = "q6-writeback"
        next_action = (
            data.get("next_action")
            or "rerun with PDOCKER_GPU_DISPATCH_PROFILE_RESPONSE=1 so Q6_K compact writable output hashes and row-indexed before/after writeback samples are present and verified"
        )
    elif q6.get("latest_status") == "match":
        classification = "q6-workgroup-cleared-and-oracle-match"
        responsibility_boundary = "q6-oracle-match"
        next_action = "advance to ngl=2 or performance tuning"
    elif q6.get("latest_status") == "mismatch":
        classification = "q6-workgroup-cleared-but-oracle-mismatch"
        responsibility_boundary = "q6-oracle"
        q6_blocker_class = str(q6.get("blocker_class") or "descriptor-memory-synchronization-or-q6-arithmetic")
        if q6_writeback_evidence.get("summary") == "pass":
            if q6_native_vs_writeback_split.get("summary") == "executor-final-writeback":
                classification = "q6-writeback-mismatch"
                responsibility_boundary = "q6-writeback"
                q6_blocker_class = "executor-final-writeback"
            elif q6_native_vs_writeback_split.get("summary") == "native-final-store-or-readback":
                classification = "q6-native-final-store-or-readback"
                responsibility_boundary = "q6-native-final-store-readback"
                q6_blocker_class = "native-q6-final-store-or-readback"
            elif q6_output_layout.get("summary") == "canonical-mismatch-found-elsewhere":
                classification = "q6-native-output-layout"
                responsibility_boundary = "q6-output-layout"
                q6_blocker_class = "native-q6-output-layout"
            elif q6_row_provenance.get("summary") == "other-row-match":
                classification = "q6-native-other-row-output-layout"
                responsibility_boundary = "q6-output-layout"
                q6_blocker_class = "native-q6-other-row-output-layout"
            elif q6_partial_signature.get("summary") == "local-y-partial":
                classification = "q6-native-local-y-partial-store"
                responsibility_boundary = "q6-native-partial-store"
                q6_blocker_class = "native-q6-local-y-partial-store"
            elif q6_partial_signature.get("summary") == "lane-partial":
                classification = "q6-native-lane-partial-store"
                responsibility_boundary = "q6-native-partial-store"
                q6_blocker_class = "native-q6-lane-partial-store"
            elif (
                _q6_output_layout_fixed_offset_rejected(q6_output_layout)
                and q6_shader_like["q6_shader_like_oracle_cleared"] is True
            ):
                classification = "q6-native-device-execution-or-final-store"
                responsibility_boundary = "q6-native-device-execution"
                q6_blocker_class = "native-q6-device-execution-or-final-store"
            elif q6_output_layout.get("summary") == "canonical-mismatch-inconclusive":
                classification = "q6-native-output-layout-inconclusive"
                responsibility_boundary = "q6-output-layout"
                q6_blocker_class = "native-q6-output-layout-inconclusive"
            elif (
                q6_output_layout.get("summary") == "canonical-mismatch-not-found"
                and q6_shader_like["q6_shader_like_oracle_cleared"] is True
            ):
                classification = "q6-native-reduction-or-device-execution"
                responsibility_boundary = "q6-native-reduction"
                q6_blocker_class = "native-q6-reduction-or-device-execution"
            elif (
                q6_blocker_class == "q6-arithmetic-reduction-or-output-layout"
                and q6_shader_like["q6_shader_like_oracle_cleared"] is True
            ):
                q6_blocker_class = "vulkan-device-execution"
        next_action = f"continue Q6_K strict-passthrough split at the {q6_blocker_class} boundary"
    else:
        classification = "q6-inconclusive"
        responsibility_boundary = "q6-oracle"
        next_action = data.get("next_action") or "rerun with PDOCKER_GPU_CPU_ORACLE=1"

    correctness_claim_allowed = correctness == "pass" and classification == "q6-workgroup-cleared-and-oracle-match"
    cpu_comparison_available = _cpu_comparison_available(data)
    benchmark_claim_allowed = (
        correctness_claim_allowed
        and cpu_comparison_available
        and bool(comparison.get("speedup"))
    )
    return {
        "classification": classification,
        "terminal": classification == "q6-workgroup-cleared-and-oracle-match",
        "device_memory_blocked": False,
        "correctness": correctness,
        "correctness_claim_allowed": correctness_claim_allowed,
        "benchmark_claim_allowed": benchmark_claim_allowed,
        "cpu_comparison_available": cpu_comparison_available,
        "speedup": comparison.get("speedup", 0.0),
        "target_met": comparison.get("target_met", False),
        "next_action": next_action,
        "q6_workgroup_diagnostics": q6,
        "q6_shader_like_interpretation": q6_shader_like,
        "q6_output_layout_probe": q6_output_layout,
        "q6_row_provenance_probe": q6_row_provenance,
        "q6_partial_signature_probe": q6_partial_signature,
        "q6_native_vs_writeback_split": q6_native_vs_writeback_split,
        "q6_effective_blocker_class": (
            q6_blocker_class
            if classification in {
                "q6-workgroup-cleared-but-oracle-mismatch",
                "q6-writeback-mismatch",
                "q6-native-output-layout",
                "q6-native-output-layout-inconclusive",
                "q6-native-other-row-output-layout",
                "q6-native-local-y-partial-store",
                "q6-native-lane-partial-store",
                "q6-native-final-store-or-readback",
                "q6-native-device-execution-or-final-store",
                "q6-native-reduction-or-device-execution",
            }
            else None
        ),
        "q6_writeback_evidence": q6_writeback_evidence,
        "runtime_freshness": runtime_freshness,
        "runtime_env_manifest": runtime_env_manifest,
        "config_propagation": config_propagation,
        "api_prompt_sanity": api_prompt_sanity,
        "speedup_fields": speedup_fields,
        "oracle_fail_closed_evidence": [],
        "unsupported_gpu_work_evidence": [],
        "responsibility_boundary": responsibility_boundary,
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument(
        "--allow-memory-blocker",
        action="store_true",
        help="Treat insufficient-memory/runtime-memory-pressure artifacts as an expected blocked state.",
    )
    parser.add_argument(
        "--require-q6-workgroup-clear",
        action="store_true",
        help="Fail unless Q6 local-size is clear, even if the Q6 numeric oracle still mismatches.",
    )
    parser.add_argument(
        "--require-q6-match",
        action="store_true",
        help="Fail unless Q6 local-size is clear and the Q6 oracle matches.",
    )
    args = parser.parse_args(argv)

    report = classify(load_json(args.artifact))
    print(json.dumps(report, indent=2, sort_keys=True))

    classification = report["classification"]
    if report.get("device_memory_blocked"):
        return 0 if args.allow_memory_blocker else 20
    if classification == "readiness-blocked":
        return 21
    if classification in {
        "llama-completion-timeout",
        "llama-completion-disconnected",
        "llama-completion-failed",
        "llama-completion-wrong-output",
    }:
        return 22
    if classification == "api-executor-reconciliation-missing":
        return 44
    if classification == "api-executor-reconciliation-ambiguous":
        return 45
    if classification == "api-executor-reconciliation-mismatch":
        return 46
    if classification == "executor-marker-not-observed":
        return 34
    if classification == "icd-marker-not-observed":
        return 42
    if classification == "vulkan-pipeline-feature-evidence-missing":
        return 43
    if classification == "config-propagation-mismatch":
        return 35
    if classification == "unsupported-gpu-work-accepted":
        return 36
    if classification == "oracle-fail-closed":
        return 37
    if classification == "api-prompt-sanity-missing":
        return 38
    if classification == "speedup-fields-missing":
        return 39
    if classification == "q6-writeback-mismatch":
        return 40
    if classification == "q6-writeback-unverified":
        return 41
    if args.require_q6_match:
        return 0 if classification == "q6-workgroup-cleared-and-oracle-match" else 30
    if args.require_q6_workgroup_clear:
        return 0 if classification in {
            "q6-workgroup-cleared-and-oracle-match",
            "q6-workgroup-cleared-but-oracle-mismatch",
            "q6-native-output-layout",
            "q6-native-output-layout-inconclusive",
            "q6-native-other-row-output-layout",
            "q6-native-local-y-partial-store",
            "q6-native-lane-partial-store",
            "q6-native-final-store-or-readback",
            "q6-native-device-execution-or-final-store",
            "q6-native-reduction-or-device-execution",
        } else 31
    if classification == "q6-workgroup-shape-blocker":
        return 32
    if classification in {"q6-not-reached", "q6-inconclusive"}:
        return 33
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

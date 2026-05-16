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
ENV_MANIFEST_PATH = Path(__file__).resolve().with_name("llama-gpu-env-manifest.json")
COMPACT_HASH_RE = re.compile(r"^0x[0-9a-fA-F]{16}$")
ZERO_COMPACT_HASH = "0x0000000000000000"
Q6_WRITEBACK_REQUIRED_FIELDS = (
    "gpu.diagnostics.q6_workgroup_diagnostics.q6_writeback_verified_all",
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


def _q6_writeback_evidence(q6: Any) -> dict[str, Any]:
    """Validate Q6_K compact writable-binding writeback hash evidence.

    The compare summarizer emits compact binding diagnostics for the Q6_K oracle
    event.  A Q6_K oracle match is only claimable when every writable output
    binding has a non-zero hash after GPU dispatch, a non-zero hash after
    host/container writeback, the hashes match, and the executor explicitly
    marked the writeback as verified.  Missing compact fields fail closed as
    unverified; present mismatches fail closed as writeback mismatches.
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

    if q6.get("q6_writeback_verified_all") is not True:
        missing.append({
            "path": "q6_writeback_verified_all",
            "reason": "expected true",
            "value": q6.get("q6_writeback_verified_all"),
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


def _claim_base(
    classification: str,
    *,
    next_action: str,
    device_memory_blocked: bool = False,
    device_actions: list[Any] | None = None,
    memory: dict[str, Any] | None = None,
    runtime_freshness: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "classification": classification,
        "terminal": False,
        "device_memory_blocked": device_memory_blocked,
        "correctness_claim_allowed": False,
        "benchmark_claim_allowed": False,
        "next_action": next_action,
        "device_actions": device_actions or [],
        "memory": memory or {},
        "runtime_freshness": runtime_freshness or {},
    }


def classify(data: dict[str, Any]) -> dict[str, Any]:
    error = str(data.get("error") or "")
    if error in MEMORY_ERRORS:
        return _claim_base(
            error,
            device_memory_blocked=True,
            next_action=data.get("next_blocker") or "recover Android memory and rerun",
            device_actions=data.get("device_actions") or [],
            memory=data.get("memory") or {},
        )

    if _readiness_false(data):
        return _claim_base(
            "readiness-blocked",
            next_action="do not start or accept a GPU run until android-llama-gpu-readiness reports ready=true",
            device_actions=nested(data, "readiness", "device_actions") or data.get("device_actions") or [],
            memory=nested(data, "readiness", "memory") or data.get("memory") or {},
        )

    diagnostics = nested(data, "gpu", "diagnostics") or {}
    q6 = diagnostics.get("q6_workgroup_diagnostics") or {}
    correctness_summary = nested(data, "gpu", "correctness", "summary") or {}
    correctness = correctness_summary.get("correctness", "not-run")
    comparison = data.get("comparison") or {}
    runtime_freshness = _runtime_freshness(data)

    if not _observed_executor_marker_ok(runtime_freshness):
        return _claim_base(
            "executor-marker-not-observed",
            next_action="rerun compare with fresh GPU executor evidence; compare/benchmark claims require the expected executor marker",
            runtime_freshness=runtime_freshness,
        )

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
        ) | {
            "unsupported_gpu_work_evidence": unsupported_evidence,
            "config_propagation": config_propagation,
        }

    api_prompt_sanity = _api_prompt_sanity(data)
    if api_prompt_sanity.get("summary") == "fail":
        return _claim_base(
            "api-prompt-sanity-missing",
            next_action=(
                data.get("next_action")
                or "rerun the standard /completion prompt probes unchanged; do not accept GPU claims without HTTP/API prompt evidence"
            ),
            runtime_freshness=runtime_freshness,
        ) | {
            "api_prompt_sanity": api_prompt_sanity,
            "config_propagation": config_propagation,
        }

    speedup_fields = _speedup_field_status(data)
    if speedup_fields.get("summary") == "fail":
        return _claim_base(
            "speedup-fields-missing",
            next_action=(
                data.get("next_action")
                or "rerun compare so comparison and bridge_overhead_phase speedup fields are present before claiming correctness or performance"
            ),
            runtime_freshness=runtime_freshness,
        ) | {
            "speedup_fields": speedup_fields,
            "api_prompt_sanity": api_prompt_sanity,
            "config_propagation": config_propagation,
        }

    q6_writeback_evidence = _q6_writeback_evidence(q6)
    if not q6:
        classification = "q6-not-reached"
        next_action = data.get("next_action") or "collect an ngl=1 artifact with Q6_K oracle enabled"
    elif q6.get("workgroup_shape_blocker") is True:
        classification = "q6-workgroup-shape-blocker"
        next_action = "fix Q6_K local-size propagation/materialization"
    elif q6.get("latest_status") == "match" and q6_writeback_evidence.get("summary") == "mismatch":
        classification = "q6-writeback-mismatch"
        next_action = "fix Q6_K writable output writeback before accepting correctness or benchmark claims"
    elif q6.get("latest_status") == "match" and q6_writeback_evidence.get("summary") != "pass":
        classification = "q6-writeback-unverified"
        next_action = (
            data.get("next_action")
            or "rerun with PDOCKER_GPU_DISPATCH_PROFILE_RESPONSE=1 so Q6_K compact writable output hashes before/after writeback are present and verified"
        )
    elif q6.get("latest_status") == "match":
        classification = "q6-workgroup-cleared-and-oracle-match"
        next_action = "advance to ngl=2 or performance tuning"
    elif q6.get("latest_status") == "mismatch":
        classification = "q6-workgroup-cleared-but-oracle-mismatch"
        q6_blocker_class = str(q6.get("blocker_class") or "descriptor-memory-synchronization-or-q6-arithmetic")
        next_action = f"continue Q6_K strict-passthrough split at the {q6_blocker_class} boundary"
    else:
        classification = "q6-inconclusive"
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
        "q6_writeback_evidence": q6_writeback_evidence,
        "runtime_freshness": runtime_freshness,
        "config_propagation": config_propagation,
        "api_prompt_sanity": api_prompt_sanity,
        "speedup_fields": speedup_fields,
        "oracle_fail_closed_evidence": [],
        "unsupported_gpu_work_evidence": [],
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
    if classification == "executor-marker-not-observed":
        return 34
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
        } else 31
    if classification == "q6-workgroup-shape-blocker":
        return 32
    if classification in {"q6-not-reached", "q6-inconclusive"}:
        return 33
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

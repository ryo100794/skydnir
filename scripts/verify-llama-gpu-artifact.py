#!/usr/bin/env python3
"""Classify a pdocker llama GPU compare artifact.

This verifier is intentionally small and deterministic.  It does not run the
device, rebuild the image, or inspect llama.cpp.  It turns the JSON evidence
written by scripts/android-llama-gpu-compare.sh into a stable pass/blocker
classification that can be used by humans, CI, and future refactors.
"""

from __future__ import annotations

import argparse
import hashlib
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
    "Identify Skydnir-owned pdockerd, executor, or stale llama processes and their RSS before taking action.",
    "If Skydnir-owned stale llama work is present, run cleanup_commands in order to stop/remove only the Skydnir llama container and app-owned executors; do not force-stop apps.",
    "Wait for Android reclaim or reboot the test device only when MemAvailable remains below the hard threshold or strict swap gating was explicitly configured.",
)
DEFAULT_MEMORY_DIAGNOSTIC_COMMANDS = (
    "adb shell 'cat /proc/meminfo | egrep \"MemAvailable|SwapFree|SwapTotal\"'",
    "adb shell \"run-as io.github.ryo100794.pdocker.compat sh -c 'ps -A -o PID,PPID,RSS,VSZ,NAME,ARGS 2>/dev/null | grep -E \\\"(pdocker|llama|io.github.ryo100794.pdocker.compat)\\\" || true'\"",
)
DEFAULT_MEMORY_CLEANUP_COMMANDS = (
    "adb shell \"run-as io.github.ryo100794.pdocker.compat sh -c 'cd files && test -S pdocker/pdockerd.sock && printf '\\''POST /containers/skydnir-llama-cpp/stop HTTP/1.1\\r\\nHost: pdocker\\r\\nContent-Length: 0\\r\\nConnection: close\\r\\n\\r\\n'\\'' | toybox nc -U -W 3 pdocker/pdockerd.sock >/dev/null || true'\"",
    "adb shell \"run-as io.github.ryo100794.pdocker.compat sh -c 'cd files && test -S pdocker/pdockerd.sock && printf '\\''DELETE /containers/skydnir-llama-cpp?force=true HTTP/1.1\\r\\nHost: pdocker\\r\\nContent-Length: 0\\r\\nConnection: close\\r\\n\\r\\n'\\'' | toybox nc -U -W 3 pdocker/pdockerd.sock >/dev/null || true'\"",
    "adb shell \"run-as io.github.ryo100794.pdocker.compat sh -c 'pkill -x pdocker-gpu-executor 2>/dev/null; pkill -x pdocker-media-executor 2>/dev/null; true'\"",
)
ROOT = Path(__file__).resolve().parents[1]
ENV_MANIFEST_PATH = Path(__file__).resolve().with_name("llama-gpu-env-manifest.json")
COMPACT_HASH_RE = re.compile(r"^0x[0-9a-fA-F]{16}$")
ZERO_COMPACT_HASH = "0x0000000000000000"
Q6_DEBUG_U32_BLOCKERS = {
    "q6-debug-binding-alias",
    "q6-debug-binding-alias-evidence-missing",
    "q6-debug-u32-probe-layout-stale",
    "q6-debug-u32-probe-metadata-mismatch",
    "q6-debug-u32-writeback-mismatch",
    "q6-debug-u32-final-store-trace-missing",
    "q6-debug-u32-probe-missing",
    "q6-debug-u32-probe-invalid",
    "q6-stage-divergence-evidence-missing",
}
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


def _manifest_env_policy_map(manifest: dict[str, Any], key: str) -> dict[str, str]:
    values = manifest.get(key)
    if not isinstance(values, list) or not values:
        raise RuntimeError(f"llama GPU env manifest field {key!r} must be a non-empty list")
    policies: dict[str, str] = {}
    for item in values:
        if not isinstance(item, dict):
            raise RuntimeError(f"llama GPU env manifest field {key!r} contains a non-object entry")
        env_name = item.get("env")
        if not isinstance(env_name, str) or not env_name:
            raise RuntimeError(f"llama GPU env manifest field {key!r} contains an invalid env")
        policy = str(item.get("evidence_policy") or "always")
        if policy not in {"always", "callsite_gated", "q4k_callsite_gated", "q6_callsite_gated"}:
            raise RuntimeError(
                f"llama GPU env manifest field {key!r} contains invalid evidence_policy for {env_name}"
            )
        policies[env_name] = policy
    return policies


LLAMA_GPU_ENV_MANIFEST = _load_env_manifest()

# Shared llama GPU environment manifest.  The compare driver and verifier both
# load scripts/llama-gpu-env-manifest.json so diagnostic toggles cannot silently
# diverge while still leaving the executor, Dockerfiles, llama.cpp, and UI
# untouched.
LLAMA_GPU_UI_RUNTIME_ENV_KEYS = _manifest_string_tuple(LLAMA_GPU_ENV_MANIFEST, "ui_runtime_env_keys")
LLAMA_GPU_COMPARE_FORWARD_ENV_KEYS = _manifest_string_tuple(
    LLAMA_GPU_ENV_MANIFEST, "compare_forward_env_keys"
)
LLAMA_GPU_CONFIG_PROPAGATION_ENV_FIELDS = _manifest_env_field_tuple(
    LLAMA_GPU_ENV_MANIFEST, "config_propagation_env_fields"
)
LLAMA_GPU_CONFIG_PROPAGATION_EVIDENCE_POLICIES = _manifest_env_policy_map(
    LLAMA_GPU_ENV_MANIFEST, "config_propagation_env_fields"
)
Q6_CALLSITE_GATED_CONFIG_ENVS = frozenset(
    env_name
    for env_name, policy in LLAMA_GPU_CONFIG_PROPAGATION_EVIDENCE_POLICIES.items()
    if policy == "q6_callsite_gated" or env_name.startswith("PDOCKER_GPU_Q6K_")
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


BRIDGE_BINARY_COMPONENTS = {
    "gpu_executor": "libpdockergpuexecutor.so",
    "vulkan_icd": "libpdockervulkanicd.so",
}


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _expected_bridge_binary_hashes(abi: str) -> dict[str, dict[str, Any]]:
    if abi not in {"arm64-v8a", "armeabi-v7a"}:
        abi = "arm64-v8a"
    result: dict[str, dict[str, Any]] = {}
    for component, filename in BRIDGE_BINARY_COMPONENTS.items():
        path = ROOT / "app" / "src" / "main" / "jniLibs" / abi / filename
        result[component] = {
            "path": str(path.relative_to(ROOT)),
            "sha256": _sha256_file(path) if path.is_file() else "",
            "size": path.stat().st_size if path.is_file() else 0,
        }
    return result


def _bridge_binary_identity(runtime_freshness: dict[str, Any]) -> dict[str, Any]:
    value = runtime_freshness.get("bridge_binary_identity")
    return value if isinstance(value, dict) else {}


def _bridge_binary_identity_problems(runtime_freshness: dict[str, Any]) -> list[dict[str, Any]]:
    identity = _bridge_binary_identity(runtime_freshness)
    problems: list[dict[str, Any]] = []
    if not identity:
        return [{"scope": "bridge_binary_identity", "reason": "missing"}]
    if identity.get("schema") != "pdocker.llama.gpu.bridge-binary-identity.v1":
        problems.append({"scope": "bridge_binary_identity", "reason": "unsupported-schema", "value": identity.get("schema")})
    if identity.get("hash_algorithm") != "sha256":
        problems.append({"scope": "bridge_binary_identity", "reason": "unsupported-hash", "value": identity.get("hash_algorithm")})
    if identity.get("summary") != "pass":
        problems.append({"scope": "bridge_binary_identity", "reason": "summary-not-pass", "value": identity.get("summary")})
    abi = str(identity.get("abi") or "arm64-v8a")
    expected = _expected_bridge_binary_hashes(abi)
    checked_out = identity.get("checked_out_jni") if isinstance(identity.get("checked_out_jni"), dict) else {}
    installed = identity.get("installed") if isinstance(identity.get("installed"), dict) else {}
    runtime = identity.get("runtime") if isinstance(identity.get("runtime"), dict) else {}
    for component, exp in expected.items():
        exp_sha = str(exp.get("sha256") or "")
        if not exp_sha:
            problems.append({"component": component, "scope": "checkout", "reason": "expected-jni-missing", "path": exp.get("path")})
            continue
        co = checked_out.get(component) if isinstance(checked_out.get(component), dict) else {}
        co_sha = str(co.get("sha256") or "")
        if co_sha != exp_sha:
            problems.append({"component": component, "scope": "checked_out_jni", "reason": "sha256-mismatch", "expected_sha256": exp_sha, "observed_sha256": co_sha})
        inst = installed.get(component) if isinstance(installed.get(component), dict) else {}
        inst_sha = str(inst.get("sha256") or "")
        if inst_sha != exp_sha:
            problems.append({"component": component, "scope": "installed", "reason": "sha256-mismatch", "expected_sha256": exp_sha, "observed_sha256": inst_sha})
        runtime_entries = runtime.get(component) if isinstance(runtime.get(component), list) else []
        for entry in runtime_entries:
            if not isinstance(entry, dict):
                problems.append({"component": component, "scope": "runtime", "reason": "malformed-entry"})
                continue
            run_sha = str(entry.get("sha256") or "")
            if run_sha and run_sha != exp_sha:
                problems.append({"component": component, "scope": "runtime", "reason": "sha256-mismatch", "expected_sha256": exp_sha, "observed_sha256": run_sha, "path": entry.get("path"), "target": entry.get("target")})
    for key in ("missing", "mismatches", "unparsed"):
        value = identity.get(key)
        if isinstance(value, list) and value:
            problems.append({"scope": "bridge_binary_identity", "reason": key, "items": value[:8]})
    return problems


def _bridge_binary_identity_ok(runtime_freshness: dict[str, Any]) -> bool:
    return not _bridge_binary_identity_problems(runtime_freshness)


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


def _q6_callsite_reached(q6: Any) -> bool:
    if not isinstance(q6, dict):
        return False
    for key in ("event_count", "q6_probe_event_count", "q6_dispatch_event_count"):
        try:
            if int(q6.get(key, 0) or 0) > 0:
                return True
        except (TypeError, ValueError):
            pass
    return (
        q6.get("q6_dispatch_seen") is True
        or q6.get("q6_oracle_capture_missing") is True
        or str(q6.get("latest_status") or "") in {"match", "mismatch"}
        or str(q6.get("blocker_class") or "")
        in {"q6-oracle-capture-missing", "q6-probe-writeback-cleared-oracle-missing"}
    )


def _config_check_is_q6_callsite_gated(check: dict[str, Any]) -> bool:
    env_name = str(check.get("env") or "")
    policy = str(
        check.get("evidence_policy")
        or LLAMA_GPU_CONFIG_PROPAGATION_EVIDENCE_POLICIES.get(env_name, "always")
    )
    return policy == "q6_callsite_gated" or env_name in Q6_CALLSITE_GATED_CONFIG_ENVS


def _config_propagation_manifest_misses(
    config_propagation: dict[str, Any],
    *,
    q6_callsite_reached: bool = True,
) -> list[str]:
    checks = config_propagation.get("checks") or []
    if not isinstance(checks, list):
        return []
    observed = {str(check.get("env")) for check in checks if isinstance(check, dict)}
    return sorted(
        env_name
        for env_name, _field_name in LLAMA_GPU_CONFIG_PROPAGATION_ENV_FIELDS
        if env_name not in observed
        and (q6_callsite_reached or env_name not in Q6_CALLSITE_GATED_CONFIG_ENVS)
    )


def _config_propagation_failed(
    config_propagation: dict[str, Any],
    *,
    q6_callsite_reached: bool = True,
) -> bool:
    checks = config_propagation.get("checks") or []
    if not isinstance(checks, list):
        return config_propagation.get("summary") == "fail"
    if config_propagation and _config_propagation_manifest_misses(
        config_propagation,
        q6_callsite_reached=q6_callsite_reached,
    ):
        return True
    summary_failed = config_propagation.get("summary") == "fail"
    saw_deferred_check = False
    for check in checks:
        if not isinstance(check, dict):
            return True
        q6_deferred = (not q6_callsite_reached) and _config_check_is_q6_callsite_gated(check)
        if q6_deferred:
            saw_deferred_check = True
            continue
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
    return summary_failed and not saw_deferred_check


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


def _generic_spirv_cpu_oracle_mismatch_evidence(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Return evidence for generic SPIR-V CPU oracle mismatches.

    This gate is intentionally not Q6_K-specific.  Any known generic SPIR-V
    dispatch CPU oracle that was both a candidate and actually executed, then
    reported ``status == "mismatch"``, must fail closed before correctness or
    benchmark claims can be accepted.
    """

    generic = nested(data, "gpu", "diagnostics", "generic_spirv_dispatch")
    evidence: list[dict[str, Any]] = []

    def add(path: str, oracle: dict[str, Any], parent: dict[str, Any]) -> None:
        if len(evidence) >= 16:
            return
        item: dict[str, Any] = {
            "path": path,
            "candidate": oracle.get("candidate"),
            "executed": oracle.get("executed"),
            "status": oracle.get("status"),
        }
        for key in ("kernel_hint", "scope", "reason"):
            if key in oracle:
                item[key] = oracle.get(key)
        pipeline_key = parent.get("pipeline_key")
        if isinstance(pipeline_key, dict) and pipeline_key.get("spirv_hash"):
            item["spirv_hash"] = pipeline_key.get("spirv_hash")
        elif parent.get("spirv_hash"):
            item["spirv_hash"] = parent.get("spirv_hash")
        evidence.append(item)

    def visit(value: Any, path: str, parent: dict[str, Any] | None = None) -> None:
        if len(evidence) >= 16:
            return
        if isinstance(value, dict):
            oracle = value.get("cpu_oracle")
            if (
                isinstance(oracle, dict)
                and oracle.get("candidate") is True
                and oracle.get("executed") is True
                and str(oracle.get("status") or "").lower() == "mismatch"
            ):
                add(f"{path}.cpu_oracle", oracle, value)
                if len(evidence) >= 16:
                    return
            for key, child in value.items():
                visit(child, f"{path}.{key}", value)
                if len(evidence) >= 16:
                    return
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{path}[{index}]", parent)
                if len(evidence) >= 16:
                    return

    visit(generic, "gpu.diagnostics.generic_spirv_dispatch")
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


VULKAN_SHADER_REWRITE_BOOL_FIELDS = {
    "duplicate_descriptor_rewrite",
    "strict_duplicate_descriptor_normalization",
    "specialization_materialized",
    "q4k_targeted_specialization_materialized",
    "local_size_patched",
    "float16_capability_added",
    "q6_storage16_loads_lowered",
    "q6_u32_to_u8vec4_bitcasts_lowered",
    "q6_final_store_pre_barrier_inserted",
    "q4k_safe_kernel",
    "q6k_safe_kernel",
    "q6_probe_effective_replay",
}


def _normalized_compact_hash(value: Any) -> str | None:
    if not _valid_compact_hash(value):
        return None
    return str(value).lower()


def _pipeline_spirv_hash(value: dict[str, Any]) -> str | None:
    pipeline_key = value.get("pipeline_key")
    if not isinstance(pipeline_key, dict):
        return None
    return _normalized_compact_hash(pipeline_key.get("spirv_hash"))


def _vulkan_shader_passthrough_rewrite_evidence(data: Any, path: str = "$") -> list[dict[str, Any]]:
    """Return evidence that a claimed Vulkan run was not shader pass-through.

    This is intentionally generic. llama/Q6-specific compatibility rewrites,
    safe-kernel replacements, specialization folding, descriptor-decoration
    rewrites, and source/effective/executable SPIR-V hash splits may be useful
    diagnostics, but they cannot support a "Vulkan pass-through works" claim.
    """

    evidence: list[dict[str, Any]] = []

    def add(item: dict[str, Any]) -> None:
        if len(evidence) < 16:
            evidence.append(item)

    def scan_hash_identity(value: dict[str, Any], value_path: str) -> None:
        source_hash = _normalized_compact_hash(value.get("source_spirv_hash"))
        original_hash = _normalized_compact_hash(value.get("original_spirv_hash"))
        effective_hash = _normalized_compact_hash(value.get("effective_spirv_hash"))
        executable_hash = _pipeline_spirv_hash(value)
        baseline_hash = original_hash or source_hash
        if baseline_hash and effective_hash and baseline_hash != effective_hash:
            add({
                "path": value_path,
                "kind": "source-effective-spirv-hash-mismatch",
                "source_spirv_hash": baseline_hash,
                "effective_spirv_hash": effective_hash,
            })
        if effective_hash and executable_hash and effective_hash != executable_hash:
            add({
                "path": f"{value_path}.pipeline_key",
                "kind": "effective-executable-spirv-hash-mismatch",
                "effective_spirv_hash": effective_hash,
                "pipeline_spirv_hash": executable_hash,
            })
        elif baseline_hash and executable_hash and baseline_hash != executable_hash:
            add({
                "path": f"{value_path}.pipeline_key",
                "kind": "source-executable-spirv-hash-mismatch",
                "source_spirv_hash": baseline_hash,
                "pipeline_spirv_hash": executable_hash,
            })

    def visit(value: Any, value_path: str) -> None:
        if len(evidence) >= 16:
            return
        if isinstance(value, dict):
            if value.get("strict_transport_identity_eligible") is False:
                add({
                    "path": f"{value_path}.strict_transport_identity_eligible",
                    "kind": "strict-transport-identity-ineligible",
                    "reason": value.get("strict_transport_identity_reason"),
                })
                if len(evidence) >= 16:
                    return
            for field in sorted(VULKAN_SHADER_REWRITE_BOOL_FIELDS):
                if value.get(field) is True:
                    add({
                        "path": f"{value_path}.{field}",
                        "kind": "shader-rewrite-or-diagnostic-replacement",
                        "field": field,
                        "value": True,
                    })
                    if len(evidence) >= 16:
                        return
            cpu_oracle = value.get("cpu_oracle")
            if isinstance(cpu_oracle, dict) and cpu_oracle.get("oracle_writeback") is True:
                add({
                    "path": f"{value_path}.cpu_oracle.oracle_writeback",
                    "kind": "cpu-oracle-writeback",
                    "field": "cpu_oracle.oracle_writeback",
                    "value": True,
                })
                if len(evidence) >= 16:
                    return
            scan_hash_identity(value, value_path)
            if len(evidence) >= 16:
                return
            for key, child in value.items():
                visit(child, f"{value_path}.{key}")
                if len(evidence) >= 16:
                    return
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{value_path}[{index}]")
                if len(evidence) >= 16:
                    return

    visit(data, path)
    return evidence


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



def _integer_list_exact(value: Any, length: int = 3) -> list[int] | None:
    result = _integer_list(value)
    if result is None or len(result) != length:
        return None
    return result


def _u64_from_entry(entry: dict[str, Any]) -> int | None:
    for key in ("value_u64", "value", "u64"):
        if key not in entry:
            continue
        try:
            value = int(entry.get(key))
        except (TypeError, ValueError):
            continue
        if value >= 0:
            return value
    return None


def _q6_specialization_entry_value(entries: Any, constant_id: int) -> int | None:
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            candidate = int(entry.get("constant_id", entry.get("spec_id")))
        except (TypeError, ValueError):
            continue
        if candidate == constant_id:
            return _u64_from_entry(entry)
    return None

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


Q6_DESCRIPTOR_INVARIANT_FIELDS = (
    "offset_equals_memory_plus_api_offset",
    "gpu_offset_equals_memory_plus_api_offset",
    "descriptor_offset_equals_api_offset",
    "descriptor_range_matches_api_range",
)


def _q6_descriptor_invariant_mismatches(q6: Any) -> list[dict[str, Any]]:
    if not isinstance(q6, dict):
        return []
    result: list[dict[str, Any]] = []
    for item in q6.get("q6_descriptor_invariant_mismatches") or []:
        if isinstance(item, dict):
            result.append(dict(item))
        else:
            result.append({"path": "q6_descriptor_invariant_mismatches[]", "value": item})
    for collection_name in ("q6_writable_bindings", "q6_readonly_upload_hash_mismatches"):
        collection = q6.get(collection_name)
        if not isinstance(collection, list):
            continue
        for index, item in enumerate(collection):
            if not isinstance(item, dict):
                continue
            for field in Q6_DESCRIPTOR_INVARIANT_FIELDS:
                invariant_value = item.get(field)
                if invariant_value is not True:
                    result.append(_compact_binding_identity(
                        item, f"{collection_name}[{index}]"
                    ) | {
                        "failed_invariant": field,
                        "reason": "missing-or-not-true",
                        "value": invariant_value,
                    })
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


def _q6_debug_u32_probe(q6: Any) -> dict[str, Any]:
    if not isinstance(q6, dict):
        return {}
    probe = q6.get("q6_debug_u32_probe")
    return probe if isinstance(probe, dict) else {}


def _q6_debug_binding_alias_safety(q6: Any) -> dict[str, Any]:
    if not isinstance(q6, dict):
        return {}
    guard = q6.get("q6_debug_binding_alias_safety")
    if not isinstance(guard, dict):
        return {}
    summary = guard.get("summary")
    if summary not in {"pass", "fail", "not-run", "missing-evidence"}:
        summary = "missing-evidence"
    return {**guard, "summary": summary}


def _q6_debug_alias_evidence_missing(
    safety: dict[str, Any],
    debug_probe: dict[str, Any],
    debug_probe_blocker: str,
    final_store_boundary: dict[str, Any],
) -> bool:
    if safety.get("summary") not in {"not-run", "missing-evidence"}:
        return False
    probe_summary = debug_probe.get("summary")
    boundary_summary = final_store_boundary.get("summary")
    return (
        bool(debug_probe_blocker)
        or probe_summary not in {None, "", "not-run"}
        or boundary_summary in {
            "pass",
            "executor-writeback-mismatch",
            "native-final-store-mismatch",
        }
    )


def _q6_final_store_sample_has_latest_event_identity(sample: dict[str, Any]) -> bool:
    """Return true when a final-store sample is tied to the latest Q6 event.

    Some executor events do not carry a stable dispatch_id in the compare
    artifact, so dispatch_id is useful but not authoritative by itself.  The
    fail-closed identity is matching source and effective SPIR-V compact hashes
    between the sample event annotation and the boundary sample identity; a
    present dispatch id must not bypass that hash lineage check.
    """
    source = sample.get("source_spirv_hash")
    event_source = sample.get("q6_event_source_spirv_hash")
    effective = sample.get("effective_spirv_hash")
    event_effective = sample.get("q6_event_effective_spirv_hash")
    hashes_match = (
        _valid_compact_hash(source)
        and _valid_compact_hash(event_source)
        and str(source).lower() == str(event_source).lower()
        and _valid_compact_hash(effective)
        and _valid_compact_hash(event_effective)
        and str(effective).lower() == str(event_effective).lower()
    )
    return hashes_match


def _q6_native_vs_writeback_sample_class(sample: Any) -> str:
    if not isinstance(sample, dict):
        return "mixed-or-inconclusive"
    native_matches_expected = sample.get("native_matches_expected")
    writeback_matches_native = sample.get("writeback_matches_native")
    writeback_matches_expected = sample.get("writeback_matches_expected")
    if (
        native_matches_expected is True
        and writeback_matches_native is True
        and writeback_matches_expected is True
    ):
        return "pass"
    if native_matches_expected is False and writeback_matches_native is True:
        return "native-final-store-or-readback"
    if native_matches_expected is True and writeback_matches_native is False:
        return "executor-final-writeback"
    return "mixed-or-inconclusive"


def _q6_native_vs_writeback_split(q6: Any) -> dict[str, Any]:
    if not isinstance(q6, dict):
        return {"summary": "not-run", "samples": []}
    raw = q6.get("q6_native_vs_writeback_split")
    if not isinstance(raw, dict):
        return {"summary": "not-run", "samples": []}
    summary = raw.get("summary")
    if summary not in {
        "pass",
        "native-final-store-or-readback",
        "executor-final-writeback",
        "inconclusive",
        "not-reached",
        "masked-by-oracle-writeback",
    }:
        summary = "inconclusive"
    samples = raw.get("samples")
    if not isinstance(samples, list):
        samples = []
    computed_class_counts = {
        "native-final-store-or-readback": 0,
        "executor-final-writeback": 0,
        "pass": 0,
        "mixed-or-inconclusive": 0,
    }
    for sample in samples:
        sample_class = _q6_native_vs_writeback_sample_class(sample)
        computed_class_counts[sample_class] = computed_class_counts.get(sample_class, 0) + 1
    if summary in {"pass", "native-final-store-or-readback", "executor-final-writeback"}:
        if raw.get("oracle_writeback") is not False or not samples:
            summary = "inconclusive"
    if summary == "pass" and computed_class_counts["pass"] != len(samples):
        summary = "inconclusive"
    if summary == "native-final-store-or-readback" and computed_class_counts["native-final-store-or-readback"] != len(samples):
        summary = "inconclusive"
    if summary == "executor-final-writeback" and computed_class_counts["executor-final-writeback"] != len(samples):
        summary = "inconclusive"
    return {
        **raw,
        "summary": summary,
        "computed_class_counts": computed_class_counts,
        "samples": samples,
    }


def _q6_final_store_boundary_sample_class(sample: Any) -> str:
    if not isinstance(sample, dict):
        return "mixed-or-inconclusive"
    final_matches_expected = sample.get("final_store_matches_expected")
    writeback_matches_final_store = sample.get("writeback_matches_final_store")
    writeback_matches_expected = sample.get("writeback_matches_expected")
    if (
        final_matches_expected is True
        and writeback_matches_final_store is True
        and writeback_matches_expected is True
    ):
        return "pass"
    if final_matches_expected is False and writeback_matches_final_store is True:
        return "native-final-store-mismatch"
    if final_matches_expected is True and writeback_matches_final_store is False:
        return "executor-writeback-mismatch"
    return "mixed-or-inconclusive"


def _q6_final_store_boundary(q6: Any) -> dict[str, Any]:
    if not isinstance(q6, dict):
        return {"summary": "not-run", "samples": []}
    boundary = q6.get("q6_final_store_boundary")
    if not isinstance(boundary, dict):
        return {"summary": "not-run", "samples": []}
    summary = boundary.get("summary")
    if summary not in {
        "pass",
        "native-final-store-mismatch",
        "executor-writeback-mismatch",
        "inconclusive",
        "not-run",
    }:
        summary = "inconclusive"
    samples = boundary.get("samples")
    if not isinstance(samples, list):
        samples = []
    requires_samples = summary in {
        "pass",
        "native-final-store-mismatch",
        "executor-writeback-mismatch",
    }
    if requires_samples and not samples:
        summary = "inconclusive"
    requires_trace_writeback = summary in {
        "pass",
        "native-final-store-mismatch",
        "executor-writeback-mismatch",
    }
    if requires_trace_writeback and not all(
        isinstance(sample, dict) and sample.get("trace_writeback_verified") is True
        for sample in samples
    ):
        summary = "inconclusive"
    if (
        summary in {"pass", "native-final-store-mismatch", "executor-writeback-mismatch"}
        and boundary.get("correlation_scope") == "latest-q6-event"
        and not all(
            isinstance(sample, dict)
            and sample.get("layout_from_final_store_trace") is True
            and sample.get("layout_sample_source") == "final-store-trace"
            and _q6_final_store_sample_has_latest_event_identity(sample)
            for sample in samples
        )
    ):
        summary = "inconclusive"
    computed_class_counts = {
        "native-final-store-mismatch": 0,
        "executor-writeback-mismatch": 0,
        "pass": 0,
        "mixed-or-inconclusive": 0,
    }
    for sample in samples:
        sample_class = _q6_final_store_boundary_sample_class(sample)
        computed_class_counts[sample_class] = computed_class_counts.get(sample_class, 0) + 1
    if summary == "pass" and computed_class_counts["pass"] != len(samples):
        summary = "inconclusive"
    if summary == "native-final-store-mismatch" and not (
        computed_class_counts["native-final-store-mismatch"] > 0
        and computed_class_counts["executor-writeback-mismatch"] == 0
        and computed_class_counts["mixed-or-inconclusive"] == 0
    ):
        summary = "inconclusive"
    if summary == "executor-writeback-mismatch" and not (
        computed_class_counts["executor-writeback-mismatch"] > 0
        and computed_class_counts["native-final-store-mismatch"] == 0
        and computed_class_counts["mixed-or-inconclusive"] == 0
    ):
        summary = "inconclusive"
    return {
        **boundary,
        "summary": summary,
        "joined_sample_count": boundary.get("joined_sample_count"),
        "computed_class_counts": computed_class_counts,
        "samples": samples,
    }


def _q6_debug_u32_probe_blocker(q6: Any) -> str:
    if not isinstance(q6, dict):
        return ""
    explicit = str(q6.get("q6_debug_u32_probe_blocker") or "")
    if explicit in Q6_DEBUG_U32_BLOCKERS:
        return explicit
    report = _q6_debug_u32_probe(q6)
    if not report:
        return ""
    if report.get("summary") in {"pass", "not-run"}:
        return ""
    failures = "\n".join(str(item) for item in report.get("failures") or []).lower()
    if (
        "lane trace layout stale" in failures
        or "q6-lane-trace-layout-stale" in failures
        or "lane-trace-layout-overlap" in failures
        or "layout-overlap" in failures
    ):
        return "q6-debug-u32-probe-layout-stale"
    if (
        "candidate-id" in failures
        or "candidate id" in failures
        or "candidate mismatch" in failures
        or "role-code" in failures
        or "role code" in failures
        or "role mismatch" in failures
    ):
        return "q6-debug-u32-probe-metadata-mismatch"
    if "writeback" in failures:
        return "q6-debug-u32-writeback-mismatch"
    trace_count = report.get("executed_final_trace_v2_count")
    if trace_count is None:
        trace_count = 0
        bindings = report.get("bindings")
        if isinstance(bindings, list):
            for binding in bindings:
                if not isinstance(binding, dict):
                    continue
                try:
                    trace_count += int(binding.get("executed_final_trace_v2_count") or 0)
                except (TypeError, ValueError):
                    pass
    if (
        "trace-v2-metadata" in failures
        or "final-output trace metadata" in failures
        or "no executed q6 final-store trace-v2 record" in failures
        or "no executed final-output q6 probe record" in failures
        or trace_count == 0
    ):
        return "q6-debug-u32-final-store-trace-missing"
    try:
        if int(report.get("debug_binding_count") or 0) == 0:
            return "q6-debug-u32-probe-missing"
    except (TypeError, ValueError):
        return "q6-debug-u32-probe-invalid"
    return "q6-debug-u32-probe-invalid"


def _q6_not_reached(q6: Any) -> bool:
    if not isinstance(q6, dict) or not q6:
        return True
    if str(q6.get("blocker_class") or "") != "not-reached":
        return False
    try:
        event_count = int(q6.get("event_count") or 0)
        probe_event_count = int(q6.get("q6_probe_event_count") or 0)
        dispatch_event_count = int(q6.get("q6_dispatch_event_count") or 0)
    except (TypeError, ValueError):
        return False
    return (
        event_count == 0
        and probe_event_count == 0
        and dispatch_event_count == 0
        and q6.get("q6_dispatch_seen") is not True
    )


def _q6_stage_divergence_evidence(q6: Any) -> dict[str, Any]:
    """Validate the evidence needed to claim the first Q6 divergence stage.

    A final-store boundary mismatch proves that Android Vulkan produced a value
    that was faithfully written back to the container. It does not, by itself,
    prove whether the first bad value appeared before reduction, during
    reduction, or only at the lane-0 final store. That stronger claim requires
    a manifest-backed stage trace whose pre-reduction and reduction values have
    been compared before accepting a final-lane-0 classification.
    """
    if not isinstance(q6, dict):
        return {"schema": "pdocker.q6k.stage-divergence.v1", "summary": "not-run"}
    raw = q6.get("q6_stage_divergence")
    if not isinstance(raw, dict):
        return {
            "schema": "pdocker.q6k.stage-divergence.v1",
            "summary": "missing-evidence",
            "reason": "missing q6_stage_divergence",
        }
    summary = str(raw.get("summary") or "")
    valid_summaries = {
        "pass",
        "missing-evidence",
        "reduction-mismatch",
        "final-lane0-store-mismatch",
    }
    if summary not in valid_summaries:
        return {
            **raw,
            "summary": "missing-evidence",
            "reason": "unsupported q6_stage_divergence.summary",
        }
    required_true = (
        "manifest_sourced",
        "lane_trace_verified",
        "trace_writeback_verified",
    )
    missing = [field for field in required_true if raw.get(field) is not True]
    expectations = q6.get("q6_debug_probe_expectations")
    if not isinstance(expectations, dict) or expectations.get("source") != "manifest":
        missing.append("q6_debug_probe_expectations.source")
    probe = q6.get("q6_debug_u32_probe")
    if not isinstance(probe, dict) or probe.get("summary") != "pass":
        missing.append("q6_debug_u32_probe.summary")
    else:
        try:
            stage_count = int(probe.get("executed_stage_trace_v2_count") or 0)
            final_count = int(probe.get("executed_final_trace_v2_count") or 0)
        except (TypeError, ValueError):
            stage_count = 0
            final_count = 0
        if stage_count <= 0:
            missing.append("q6_debug_u32_probe.executed_stage_trace_v2_count")
        if final_count <= 0:
            missing.append("q6_debug_u32_probe.executed_final_trace_v2_count")
    if summary == "reduction-mismatch":
        required_reduction_true = (
            "pre_reduction_compared",
            "pre_reduction_matches",
            "reduction_compared",
        )
        missing.extend(field for field in required_reduction_true if raw.get(field) is not True)
        if raw.get("reduction_matches") is not False:
            missing.append("reduction_matches")
        if raw.get("first_divergent_stage") != "reduction":
            missing.append("first_divergent_stage")
    if summary == "final-lane0-store-mismatch":
        required_final_true = (
            "pre_reduction_compared",
            "pre_reduction_matches",
            "reduction_compared",
            "reduction_matches",
            "final_store_compared",
        )
        missing.extend(field for field in required_final_true if raw.get(field) is not True)
        if raw.get("final_record_role_code") != 4:
            missing.append("final_record_role_code")
        if raw.get("final_record_local_invocation_id") != [0, 0, 0]:
            missing.append("final_record_local_invocation_id")
        if raw.get("final_store_matches_expected") is not False:
            missing.append("final_store_matches_expected")
        if raw.get("first_divergent_stage") not in {"final-store", "final-lane0-store"}:
            missing.append("first_divergent_stage")
    if missing:
        return {
            **raw,
            "summary": "missing-evidence",
            "missing": missing[:16],
            "reason": "stage divergence evidence is incomplete",
        }
    return raw


def _api_prompt_sanity(data: dict[str, Any]) -> dict[str, Any]:
    if not _is_compare_artifact(data):
        return {"required": False, "summary": "not-required", "missing": []}
    readiness = nested(data, "gpu", "service_readiness")
    if nested(data, "gpu", "served") is False and isinstance(readiness, dict) and readiness:
        summary = readiness.get("summary") if isinstance(readiness.get("summary"), dict) else {}
        if summary.get("ready") is not True:
            return {
                "required": True,
                "summary": "fail",
                "missing": ["gpu.service_readiness.summary.ready"],
                "required_probe_count": 0,
                "service_not_ready": True,
                "service_readiness": _service_readiness_summary(data),
            }
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


def _completion_timeout_diagnostics_summary(data: dict[str, Any]) -> dict[str, Any]:
    readiness = nested(data, "gpu", "service_readiness")
    if not isinstance(readiness, dict) or not readiness:
        return {"present": False}
    diagnostics = readiness.get("completion_timeout_diagnostics")
    if not isinstance(diagnostics, dict) or not diagnostics:
        return {"present": False}
    files = diagnostics.get("files") if isinstance(diagnostics.get("files"), dict) else {}
    port_listener = (
        diagnostics.get("port_listener")
        if isinstance(diagnostics.get("port_listener"), dict)
        else {}
    )
    process_summary = (
        diagnostics.get("process_summary")
        if isinstance(diagnostics.get("process_summary"), dict)
        else {}
    )
    file_summary: dict[str, Any] = {}
    for key in (
        "container_state",
        "container_logs",
        "memory",
        "processes",
        "port_listener",
        "engine_inspect",
        "engine_stats",
        "memory_pressure",
    ):
        item = files.get(key) if isinstance(files, dict) else None
        if isinstance(item, dict):
            file_summary[key] = {
                "path": item.get("path"),
                "exists": item.get("exists"),
                "bytes": item.get("bytes"),
            }
    return {
        "present": True,
        "schema": diagnostics.get("schema"),
        "artifact_dir": diagnostics.get("artifact_dir"),
        "container_ref": diagnostics.get("container_ref"),
        "files": file_summary,
        "port_listener": {
            "port": port_listener.get("port"),
            "listener_count": port_listener.get("listener_count"),
            "owner_count": port_listener.get("owner_count"),
            "owners": (
                port_listener.get("owners")[:4]
                if isinstance(port_listener.get("owners"), list)
                else []
            ),
        },
        "process_summary": {
            "process_count": process_summary.get("process_count"),
            "process_rss_mb_total": process_summary.get("process_rss_mb_total"),
            "pdockerd_socket": process_summary.get("pdockerd_socket"),
            "wchan_samples": (
                process_summary.get("wchan_samples")[:8]
                if isinstance(process_summary.get("wchan_samples"), list)
                else []
            ),
        },
        "container_state_summary": diagnostics.get("container_state_summary"),
        "log_tail": diagnostics.get("log_tail"),
        "next_checks": diagnostics.get("next_checks"),
    }


def _service_readiness_summary(data: dict[str, Any]) -> dict[str, Any]:
    readiness = nested(data, "gpu", "service_readiness")
    if not isinstance(readiness, dict) or not readiness:
        return {"summary": "not-recorded", "ready": None}
    summary = readiness.get("summary") if isinstance(readiness.get("summary"), dict) else {}
    health = readiness.get("health") if isinstance(readiness.get("health"), dict) else {}
    models = readiness.get("models") if isinstance(readiness.get("models"), dict) else {}
    completion = readiness.get("completion") if isinstance(readiness.get("completion"), dict) else {}
    return {
        "summary": str(summary.get("reason") or "recorded"),
        "ready": summary.get("ready") if isinstance(summary.get("ready"), bool) else None,
        "health": summary.get("health") or health.get("status"),
        "models": summary.get("models") or models.get("status"),
        "completion": summary.get("completion") or completion.get("status"),
        "liveness": summary.get("liveness"),
        "reason": summary.get("reason"),
        "completion_error": completion.get("error"),
        "completion_passed": completion.get("passed") if isinstance(completion.get("passed"), bool) else None,
    }


def _state_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
    return None


def _container_exit_evidence(data: dict[str, Any]) -> dict[str, Any]:
    raw = nested(data, "gpu", "container_exit")
    if isinstance(raw, dict) and raw:
        evidence = dict(raw)
    else:
        state = nested(data, "gpu", "state")
        if not isinstance(state, dict):
            state_excerpt = nested(data, "gpu", "state_excerpt")
            if isinstance(state_excerpt, str) and state_excerpt.strip().startswith("{"):
                try:
                    parsed = json.loads(state_excerpt)
                    state = parsed if isinstance(parsed, dict) else {}
                except Exception:
                    state = {}
            else:
                state = {}
        state_info = state.get("State") if isinstance(state.get("State"), dict) else {}
        evidence = {
            "schema": "pdocker.llama.container-exit.v1",
            "container_id": state.get("Id") or state.get("ID"),
            "name": str(state.get("Name") or "").lstrip("/") or None,
            "running": state_info.get("Running") if isinstance(state_info.get("Running"), bool) else None,
            "status": state_info.get("Status"),
            "exit_code": state_info.get("ExitCode") if isinstance(state_info.get("ExitCode"), int) else None,
            "error": state_info.get("Error"),
            "oom_killed": state_info.get("OOMKilled") if isinstance(state_info.get("OOMKilled"), bool) else None,
            "started_at": state_info.get("StartedAt"),
            "finished_at": state_info.get("FinishedAt"),
        }
    running = _state_bool(evidence.get("running"))
    status = str(evidence.get("status") or "").lower()
    exit_code = evidence.get("exit_code")
    exited = (
        evidence.get("exited_before_readiness") is True
        or running is False
        or status in {"exited", "dead", "removing"}
        or isinstance(exit_code, int)
    )
    served = nested(data, "gpu", "served")
    readiness = nested(data, "gpu", "service_readiness")
    readiness_ready = None
    if isinstance(readiness, dict):
        summary = readiness.get("summary") if isinstance(readiness.get("summary"), dict) else {}
        readiness_ready = summary.get("ready") if isinstance(summary.get("ready"), bool) else None
    evidence["exited_before_readiness"] = bool(served is False and exited and readiness_ready is not True)
    return evidence


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

    def reconciliation_has_strict_transport_match(value: Any) -> bool:
        if not isinstance(value, dict):
            return False
        if value.get("summary") != "diagnostic":
            return False
        if value.get("proof_strength") != "diagnostic":
            return False
        dispatches = value.get("dispatches")
        if not isinstance(dispatches, list) or not dispatches:
            return False
        if value.get("duplicate_dispatch_ids"):
            return False
        if value.get("unmatched_api_outputs"):
            return False
        for index, dispatch in enumerate(dispatches):
            if not isinstance(dispatch, dict):
                add_ambiguous(
                    f"gpu.diagnostics.api_executor_reconciliation.dispatches[{index}]",
                    "dispatch reconciliation record is not an object",
                    dispatch,
                )
                return False
            if dispatch.get("match_status") != "diagnostic-match":
                return False
            matches = dispatch.get("matches")
            if not isinstance(matches, dict) or not matches:
                return False
            required = (
                "core_command_hash_comparable",
                "core_command_hash",
                "spirv_hash",
                "descriptor_hash",
                "push_hash",
                "spec_hash",
                "dispatch_hash",
            )
            missing_or_false = [key for key in required if matches.get(key) is not True]
            if missing_or_false:
                add_ambiguous(
                    f"gpu.diagnostics.api_executor_reconciliation.dispatches[{index}].matches",
                    "strict transport match is missing required true matches",
                    missing_or_false,
                )
                return False
            transport = dispatch.get("transport")
            if isinstance(transport, dict):
                if transport.get("msg_trunc") is True or transport.get("msg_ctrunc") is True:
                    add_mismatch(
                        f"gpu.diagnostics.api_executor_reconciliation.dispatches[{index}].transport",
                        "transport was truncated",
                        transport,
                    )
                    return False
        return True

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

    strict_transport_match = reconciliation_has_strict_transport_match(reconciliation)

    if summary in {"ambiguous", "inconclusive", "duplicate", "unmatched"}:
        add_ambiguous("gpu.diagnostics.api_executor_reconciliation.summary", "ambiguous summary", raw_summary)
    if summary in {"fail", "failed", "mismatch", "hash-mismatch", "canonical-mismatch"}:
        add_mismatch("gpu.diagnostics.api_executor_reconciliation.summary", "failing summary", raw_summary)

    if ambiguous:
        result_summary = "ambiguous"
    elif mismatches:
        result_summary = "mismatch"
    elif summary == "pass" or strict_transport_match:
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
    # Native Q6 classification must not infer the executor-resolved workgroup
    # shape from the literal SPIR-V LocalSize.  A missing resolved value is
    # evidence loss, not a value to synthesize.
    return _integer_list(q6.get("local_size_resolved"))


def _q6_safe_kernel_enabled(q6: Any) -> bool:
    if not isinstance(q6, dict):
        return False
    safe_hash = "0x7ec0292e948c9b41"
    candidates = [
        q6.get("latest_spirv_hash"),
        q6.get("effective_spirv_hash"),
        q6.get("source_spirv_hash"),
    ]
    latest_dispatch = q6.get("q6_dispatch_latest")
    if isinstance(latest_dispatch, dict):
        candidates.extend([
            latest_dispatch.get("spirv_hash"),
            latest_dispatch.get("effective_spirv_hash"),
            (latest_dispatch.get("pipeline_key") or {}).get("spirv_hash")
            if isinstance(latest_dispatch.get("pipeline_key"), dict) else None,
        ])
    pipeline_key = q6.get("pipeline_key")
    if isinstance(pipeline_key, dict):
        candidates.append(pipeline_key.get("spirv_hash"))
    return q6.get("q6k_safe_kernel") is True or any(
        str(value or "").lower() == safe_hash for value in candidates
    )


def _q6_expected_local_size(q6: Any) -> list[int] | None:
    if not isinstance(q6, dict):
        return None
    if _q6_safe_kernel_enabled(q6):
        return [1, 1, 1]
    q6_local_size = _integer_list(q6.get("q6_local_size"))
    if q6_local_size is not None:
        return q6_local_size
    # For native Q6, the expected runtime workgroup shape must come from the
    # captured Q6 tuple.  Falling back to local_size_resolved would make a
    # missing q6_local_size self-consistent and hide evidence loss.
    return None


def _q6_required_local_size_clear(q6: Any) -> bool:
    local_size = _q6_local_size_resolved(q6)
    expected = _q6_expected_local_size(q6)
    return local_size is not None and expected is not None and local_size == expected


def _q6_local_size_looks_contaminated_by_q6_shape(q6: Any) -> bool:
    if not isinstance(q6, dict) or _q6_safe_kernel_enabled(q6):
        return False
    local_size = _q6_local_size_resolved(q6)
    if not (isinstance(local_size, list) and len(local_size) == 3):
        return False
    q6_num_rows = q6.get("q6_num_rows")
    q6_num_cols = q6.get("q6_num_cols")
    rows_match_y = (
        isinstance(q6_num_rows, int)
        and q6_num_rows > 1
        and local_size[1] == q6_num_rows
    )
    cols_match_z = (
        isinstance(q6_num_cols, int)
        and q6_num_cols > 1
        and local_size[2] == q6_num_cols
    )
    return bool(rows_match_y or cols_match_z)


def _q6_workgroup_shape_blocked(q6: Any) -> bool:
    if not isinstance(q6, dict):
        return False
    if q6.get("local_size_consistent") is False:
        return True
    local_size = _q6_local_size_resolved(q6)
    if local_size is None:
        return True
    if _q6_local_size_looks_contaminated_by_q6_shape(q6):
        return True
    q6_local_size = _integer_list(q6.get("q6_local_size"))
    if q6_local_size is not None and local_size is not None and q6_local_size != local_size:
        return True
    return not _q6_required_local_size_clear(q6)


def _q6_workgroup_evidence_status(q6: Any) -> dict[str, Any]:
    required_fields = [
        "local_size_resolved",
        "q6_local_size",
        "local_size_consistent",
        "q6_workgroup_specialization_interpretation",
        "local_size|spirv_local_size",
        "spirv_local_size_id",
        "spirv_workgroup_size_spec_id",
        "specialization_entries",
    ]
    if not isinstance(q6, dict):
        return {
            "summary": "fail",
            "evidence_failure": True,
            "missing": ["gpu.diagnostics.q6_workgroup_diagnostics"],
            "contradictions": [],
            "evidence_contradictions": [],
            "required_fields": required_fields,
        }

    missing: list[str] = []
    contradictions: list[dict[str, Any]] = []
    native_q6_workgroup = not _q6_safe_kernel_enabled(q6)

    local_size = _integer_list_exact(q6.get("local_size_resolved"))
    q6_local_size = _integer_list_exact(q6.get("q6_local_size"))
    expected = _q6_expected_local_size(q6)
    literal_local_size = _integer_list_exact(q6.get("spirv_local_size"))
    if literal_local_size is None:
        literal_local_size = _integer_list_exact(q6.get("local_size"))
    local_size_id = _integer_list_exact(q6.get("spirv_local_size_id"))
    workgroup_size_spec_id = _integer_list_exact(q6.get("spirv_workgroup_size_spec_id"))
    specialization_entries = q6.get("specialization_entries")
    interpretation = q6.get("q6_workgroup_specialization_interpretation")

    if "local_size_resolved" not in q6:
        missing.append("local_size_resolved")
    elif local_size is None:
        contradictions.append({
            "field": "local_size_resolved",
            "reason": "not-length-3-integer-list",
            "value": q6.get("local_size_resolved"),
        })
    if q6_local_size is None:
        missing.append("q6_local_size")
    if not isinstance(q6.get("local_size_consistent"), bool):
        missing.append("local_size_consistent")
    if not isinstance(interpretation, dict):
        missing.append("q6_workgroup_specialization_interpretation")
    if "spirv_local_size" not in q6 and "local_size" not in q6:
        missing.append("local_size|spirv_local_size")
    elif literal_local_size is None:
        contradictions.append({
            "field": "local_size|spirv_local_size",
            "reason": "not-length-3-integer-list",
            "spirv_local_size": q6.get("spirv_local_size"),
            "local_size": q6.get("local_size"),
        })
    if "spirv_local_size_id" not in q6:
        missing.append("spirv_local_size_id")
    elif local_size_id is None:
        contradictions.append({
            "field": "spirv_local_size_id",
            "reason": "not-length-3-integer-list",
            "value": q6.get("spirv_local_size_id"),
        })
    if "spirv_workgroup_size_spec_id" not in q6:
        missing.append("spirv_workgroup_size_spec_id")
    elif workgroup_size_spec_id is None:
        contradictions.append({
            "field": "spirv_workgroup_size_spec_id",
            "reason": "not-length-3-integer-list",
            "value": q6.get("spirv_workgroup_size_spec_id"),
        })
    if "specialization_entries" not in q6:
        missing.append("specialization_entries")
    elif not isinstance(specialization_entries, list):
        contradictions.append({
            "field": "specialization_entries",
            "reason": "not-list",
            "value": specialization_entries,
        })

    for key in ("local_size", "spirv_local_size", "spirv_local_size_resolved"):
        value = q6.get(key)
        if value is not None and _integer_list_exact(value) is None:
            contradictions.append({"field": key, "reason": "not-length-3-integer-list", "value": value})
    if isinstance(specialization_entries, list) and any(not isinstance(item, dict) for item in specialization_entries):
        contradictions.append({
            "field": "specialization_entries",
            "reason": "contains-non-object",
        })

    if q6.get("local_size_consistent") is False:
        contradictions.append({
            "field": "local_size_consistent",
            "reason": "reported-false",
            "value": False,
        })
    if local_size is not None and q6_local_size is not None and local_size != q6_local_size:
        contradictions.append({
            "field": "q6_local_size",
            "reason": "differs-from-resolved-local-size",
            "local_size_resolved": local_size,
            "q6_local_size": q6_local_size,
        })
    if local_size is not None and expected is not None and local_size != expected:
        contradictions.append({
            "field": "local_size_resolved",
            "reason": "differs-from-expected-local-size",
            "local_size_resolved": local_size,
            "expected_local_size": expected,
        })
    if _q6_local_size_looks_contaminated_by_q6_shape(q6):
        contradictions.append({
            "field": "local_size_resolved",
            "reason": "contaminated-by-q6-row-or-column-count",
            "local_size_resolved": local_size,
            "q6_num_rows": q6.get("q6_num_rows"),
            "q6_num_cols": q6.get("q6_num_cols"),
        })

    if isinstance(interpretation, dict):
        if interpretation.get("do_not_patch_local_size_y_from_spec_id_1") is not True:
            contradictions.append({
                "field": "q6_workgroup_specialization_interpretation",
                "reason": "missing-spec-id-1-not-workgroup-y-guard",
            })
        if interpretation.get("do_not_patch_local_size_z_from_spec_id_2") is not True:
            contradictions.append({
                "field": "q6_workgroup_specialization_interpretation",
                "reason": "missing-spec-id-2-not-workgroup-z-guard",
            })

    spec0_value = _q6_specialization_entry_value(specialization_entries, 0)
    if native_q6_workgroup:
        if workgroup_size_spec_id is not None and workgroup_size_spec_id[0] != 0:
            contradictions.append({
                "field": "spirv_workgroup_size_spec_id",
                "reason": "workgroup-x-spec-id-is-not-zero",
                "spirv_workgroup_size_spec_id": workgroup_size_spec_id,
            })
        if isinstance(specialization_entries, list) and spec0_value is None:
            missing.append("specialization_entries.constant_id_0")
        elif spec0_value is not None and local_size is not None and spec0_value != local_size[0]:
            contradictions.append({
                "field": "specialization_entries",
                "reason": "constant-id-0-value-does-not-match-workgroup-x",
                "constant_id": 0,
                "value_u64": spec0_value,
                "expected_value_u64": local_size[0],
            })

    evidence_contradictions = [
        item for item in contradictions
        if item.get("reason") in {
            "not-length-3-integer-list",
            "contains-non-object",
            "not-list",
            "differs-from-resolved-local-size",
            "missing-spec-id-1-not-workgroup-y-guard",
            "missing-spec-id-2-not-workgroup-z-guard",
            "workgroup-x-spec-id-is-not-zero",
            "constant-id-0-value-does-not-match-workgroup-x",
        }
    ]
    return {
        "summary": "fail" if missing or contradictions else "pass",
        "evidence_failure": bool(missing or evidence_contradictions),
        "missing": missing,
        "contradictions": contradictions,
        "evidence_contradictions": evidence_contradictions,
        "required_fields": required_fields,
        "local_size_resolved": local_size,
        "literal_local_size": literal_local_size,
        "spirv_local_size_id": local_size_id,
        "spirv_workgroup_size_spec_id": workgroup_size_spec_id,
        "specialization_constant_0_value": spec0_value,
        "q6_local_size": q6_local_size,
        "expected_local_size": expected,
    }


def _q6_workgroup_env_gap(
    runtime_env_manifest: dict[str, Any],
    config_propagation: dict[str, Any],
) -> dict[str, Any]:
    required = [
        "PDOCKER_GPU_LEGALIZE_WORKGROUP_SIZE_FROM_SPEC",
        "PDOCKER_GPU_MATERIALIZE_SPIRV_SPECIALIZATION_CONSTANTS",
    ]
    host_requested = runtime_env_manifest.get("host_requested_env")
    if not isinstance(host_requested, dict):
        host_requested = {}
    planned = runtime_env_manifest.get("planned_container_env")
    if not isinstance(planned, dict):
        planned = {}
    requested_or_planned = runtime_env_manifest.get("requested_or_planned_env")
    if not isinstance(requested_or_planned, dict):
        requested_or_planned = {}
    intended = runtime_env_manifest.get("intended_runtime_env")
    if not isinstance(intended, dict):
        intended = {}
    def env_requested_true(name: str) -> bool:
        for mapping in (intended, requested_or_planned, planned, host_requested):
            value = mapping.get(name)
            if str(value).strip().lower() in {"1", "true", "yes", "on"}:
                return True
        return False
    missing_requested = [
        name for name in required if not env_requested_true(name)
    ]
    checks = config_propagation.get("checks")
    if not isinstance(checks, list):
        checks = []
    statuses = {
        str(check.get("env")): str(check.get("status"))
        for check in checks
        if isinstance(check, dict) and check.get("env")
    }
    not_requested = [name for name in required if statuses.get(name) == "not-requested"]
    return {
        "required_envs": required,
        "requested_env_sources": ["intended_runtime_env", "requested_or_planned_env", "planned_container_env", "host_requested_env"],
        "missing_requested_envs": missing_requested,
        "not_requested_checks": not_requested,
        "summary": "fail" if missing_requested or not_requested else "pass",
    }


def _q6_compat_rewrite_used(q6: Any) -> bool:
    if not isinstance(q6, dict):
        return False
    return any(
        q6.get(field) is True
        for field in (
            "q6_storage16_loads_lowered",
            "q6_u32_to_u8vec4_bitcasts_lowered",
            "q6_final_store_pre_barrier_inserted",
        )
    )


def _shader_mutation_evidence_has_field(
        evidence: list[dict[str, Any]],
        *fields: str) -> bool:
    field_set = set(fields)
    return any(item.get("field") in field_set for item in evidence)


def _q6_dispatch_seen_without_oracle(q6: Any) -> bool:
    if not isinstance(q6, dict):
        return False
    try:
        event_count = int(q6.get("event_count", 0))
    except (TypeError, ValueError):
        event_count = 0
    if event_count > 0:
        return False
    try:
        dispatch_count = int(q6.get("q6_dispatch_event_count", 0))
    except (TypeError, ValueError):
        dispatch_count = 0
    return (
        q6.get("q6_dispatch_seen") is True
        or q6.get("q6_oracle_capture_missing") is True
        or dispatch_count > 0
        or str(q6.get("blocker_class") or "") == "q6-oracle-capture-missing"
    )




def _q6_probe_writeback_cleared_oracle_missing(q6: Any, q6_writeback_evidence: dict[str, Any]) -> bool:
    if not isinstance(q6, dict):
        return False
    try:
        event_count = int(q6.get("event_count", 0))
    except (TypeError, ValueError):
        event_count = 0
    try:
        probe_event_count = int(q6.get("q6_probe_event_count", 0))
    except (TypeError, ValueError):
        probe_event_count = 0
    return (
        event_count == 0
        and q6_writeback_evidence.get("summary") == "pass"
        and (
            probe_event_count > 0
            or str(q6.get("blocker_class") or "") == "q6-probe-writeback-cleared-oracle-missing"
        )
    )

def _q6_shader_like_interpretation(q6: Any) -> dict[str, Any]:
    """Explain whether the Q6 shader-like CPU oracle cleared.

    For observed llama.cpp Q6_K kernels, WorkGroupSize may be a literal or
    may be materialized from specialization constants. The verifier must use
    the resolved local size reported by the executor instead of assuming a
    fixed [32,1,1] shape; specialization constant_id=1 is NUM_ROWS, not
    WorkGroupSizeY.
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
    sixty_four_required = (not safe_kernel) and local_size != [32, 1, 1]
    thirty_two_clear = _numeric_close_to_zero(q6.get("q6_shader_like_abs_delta"))
    sixty_four_clear = _numeric_close_to_zero(q6.get("q6_shader_like_64_abs_delta"))
    if sixty_four_required:
        cleared = q6.get("latest_status") == "mismatch" and sixty_four_clear
    else:
        cleared = q6.get("latest_status") == "mismatch" and thirty_two_clear
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
                "local_size_resolved=reported-q6-local-size",
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
            "diagnostic-only-for-32x1x1-num-rows; constant_id=1 is NUM_ROWS, not WorkGroupSizeY"
            if not sixty_four_required
            else "required-for-non-32x1x1-local-size"
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


def _q6_output_index_probe(q6: Any) -> dict[str, Any]:
    if not isinstance(q6, dict):
        return {"summary": "not-run", "samples": []}
    probe = q6.get("q6_output_index_probe")
    if not isinstance(probe, dict):
        probe = {}
    layout_probe = q6.get("q6_output_layout_probe")
    nested_summary = (
        layout_probe.get("q6_output_index_probe_summary")
        if isinstance(layout_probe, dict)
        else None
    )
    summary = (
        probe.get("summary")
        or q6.get("q6_output_index_probe_summary")
        or nested_summary
        or "not-run"
    )
    if not isinstance(summary, str) or not summary:
        summary = "not-run"
    samples = probe.get("samples")
    if not isinstance(samples, list):
        samples = []
    return {
        **probe,
        "summary": summary,
        "samples": samples,
        "sample_count": probe.get("sample_count"),
        "matched_count": probe.get("matched_count"),
        "fixed_offset_count": probe.get("fixed_offset_count"),
        "scatter_count": probe.get("scatter_count"),
        "outside_store_window_count": probe.get("outside_store_window_count"),
        "missing_count": probe.get("missing_count"),
        "store_window_begin": probe.get("store_window_begin"),
        "store_window_end": probe.get("store_window_end"),
    }


def _q6_output_index_probe_summary(q6: Any) -> str:
    return str(_q6_output_index_probe(q6).get("summary") or "not-run")


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


def _q6_store_index_model_valid(q6: Any, probe: dict[str, Any]) -> bool:
    if not isinstance(q6, dict) or q6.get("q6_store_index_model_valid") is not True:
        return False
    if q6.get("q6_store_index_full_coverage") is not True:
        return False
    if q6.get("q6_store_index_sampled_nonzero_j") is not True:
        return False
    if q6.get("q6_store_index_sampled_nonzero_y") is not True:
        return False
    groups = q6.get("q6_dispatch_groups")
    if not (
        isinstance(groups, list)
        and len(groups) == 3
        and all(isinstance(value, int) and value > 0 for value in groups)
    ):
        return False
    for key in ("q6_block_size", "q6_num_rows", "q6_num_cols",
                "q6_store_window_begin", "q6_store_window_end"):
        if not isinstance(q6.get(key), int):
            return False
    samples = probe.get("samples")
    if not isinstance(samples, list):
        return False
    if not samples:
        return False
    sampled_nonzero_j = False
    sampled_nonzero_y = False
    for sample in samples:
        if not isinstance(sample, dict) or sample.get("store_formula_valid") is not True:
            return False
        if not isinstance(sample.get("expected_store_index"), int):
            return False
        workgroup = sample.get("store_workgroup")
        if not (
            isinstance(workgroup, list)
            and len(workgroup) == 3
            and all(isinstance(value, int) and value >= 0 for value in workgroup)
        ):
            return False
        for key in ("store_j", "store_row_in_group", "store_row"):
            if not isinstance(sample.get(key), int):
                return False
        sampled_nonzero_j = sampled_nonzero_j or int(sample.get("store_j")) != 0
        sampled_nonzero_y = sampled_nonzero_y or int(workgroup[1]) != 0
    if q6.get("q6_num_cols") > 1 and not sampled_nonzero_j:
        return False
    if groups[1] > 1 and not sampled_nonzero_y:
        return False
    return True


def _q6_store_index_model_required(
        q6_output_layout: dict[str, Any],
        q6_row_provenance: dict[str, Any],
        q6_partial_signature: dict[str, Any],
        q6_native_vs_writeback_split: dict[str, Any]) -> bool:
    layout_summary = str(q6_output_layout.get("summary") or "")
    return (
        layout_summary.startswith("canonical-mismatch")
        or q6_row_provenance.get("summary") == "other-row-match"
        or q6_partial_signature.get("summary") in {"local-y-partial", "lane-partial"}
        or q6_native_vs_writeback_split.get("summary") in {
            "executor-final-writeback",
            "native-final-store-or-readback",
        }
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
    if data.get("schema") == "pdocker.llama.gpu.compare.failure.v1":
        exit_code = data.get("exit_code")
        classification = "early-compare-timeout" if exit_code == 124 else "early-compare-failure"
        return _claim_base(
            classification,
            next_action=(
                data.get("next_action")
                or "inspect the compare failure artifact, daemon socket state, Android memory, and adb state before rerunning"
            ),
            memory=data.get("memory") or {},
            pdocker_memory_diagnostics=data.get("pdocker_diagnostics") or {},
            responsibility_boundary="compare-driver",
        ) | {
            "schema": data.get("schema"),
            "exit_code": exit_code,
            "stage": data.get("stage") or "unknown",
            "failure_class": data.get("failure_class") or "early_compare_failure",
            "message": data.get("message") or "compare failed before a full artifact was produced",
            "adb_state": data.get("adb_state") or {},
            "runtime_env_record": data.get("runtime_env_record") or {},
        }

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
    q6_callsite_reached = _q6_callsite_reached(q6)
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
            "completion_timeout_diagnostics": _completion_timeout_diagnostics_summary(data),
            "runtime_env": nested(data, "gpu", "runtime_env") or {},
        }

    if (
        completion_readiness.get("health_ok") is True
        and completion_readiness.get("models_ok") is True
        and completion_readiness.get("completion_ok") is True
        and completion_readiness.get("completion_passed") is False
    ):
        completion_q6_writeback_evidence = _q6_writeback_evidence(q6)
        if _q6_probe_writeback_cleared_oracle_missing(q6, completion_q6_writeback_evidence):
            return _claim_base(
                "q6-probe-writeback-cleared-oracle-missing",
                next_action=(
                    "fix compare/executor source-oracle retention for the instrumented Q6_K probe; "
                    "writeback is verified, but the source-module CPU oracle is missing"
                ),
                runtime_freshness=runtime_freshness,
                runtime_env_manifest=runtime_env_manifest,
                responsibility_boundary="q6-diagnostic-evidence",
            ) | {
                "observed_service_failure": "llama-completion-wrong-output",
                "service_readiness": completion_readiness,
                "q6_workgroup_diagnostics": q6,
                "q6_effective_blocker_class": "q6-probe-writeback-cleared-oracle-missing",
                "q6_writeback_evidence": completion_q6_writeback_evidence,
                "runtime_env": nested(data, "gpu", "runtime_env") or {},
            }
        if _q6_dispatch_seen_without_oracle(q6):
            return _claim_base(
                "q6-oracle-capture-missing",
                next_action=(
                    "fix compare/executor evidence retention so every observed Q6_K/final-projection "
                    "dispatch carries CPU-oracle, local-size, binding, and writeback diagnostics before "
                    "interpreting the wrong deterministic /completion output"
                ),
                runtime_freshness=runtime_freshness,
                runtime_env_manifest=runtime_env_manifest,
                responsibility_boundary="q6-diagnostic-evidence",
            ) | {
                "observed_service_failure": "llama-completion-wrong-output",
                "service_readiness": completion_readiness,
                "q6_workgroup_diagnostics": q6,
                "runtime_env": nested(data, "gpu", "runtime_env") or {},
            }
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
        bridge_binary_problems = _bridge_binary_identity_problems(runtime_freshness)
        if bridge_binary_problems:
            return _claim_base(
                "gpu-bridge-binary-freshness-mismatch",
                next_action="install the APK built from the current checkout and rerun compare; reconciled wrong-output claims require matching GPU executor and Vulkan ICD binary hashes",
                runtime_freshness=runtime_freshness,
                runtime_env_manifest=runtime_env_manifest,
                responsibility_boundary="runtime-freshness",
            ) | {
                "observed_service_failure": "llama-completion-wrong-output",
                "service_readiness": completion_readiness,
                "api_executor_reconciliation": api_executor_reconciliation,
                "bridge_binary_identity": _bridge_binary_identity(runtime_freshness),
                "bridge_binary_identity_problems": bridge_binary_problems,
                "runtime_env": nested(data, "gpu", "runtime_env") or {},
            }
        completion_q6_final_store_boundary = _q6_final_store_boundary(q6)
        completion_q6_native_vs_writeback_split = _q6_native_vs_writeback_split(q6)
        completion_q6_effective_blocker_class = str(q6.get("blocker_class") or "")
        if completion_q6_native_vs_writeback_split.get("summary") == "native-final-store-or-readback":
            completion_q6_effective_blocker_class = "native-q6-final-store-or-readback"
        elif completion_q6_final_store_boundary.get("reason") == "missing-executed-final-store-trace":
            completion_q6_effective_blocker_class = "q6-final-store-trace-missing"
        return _claim_base(
            "llama-completion-wrong-output",
            next_action="keep the current image/model/prompt fixed and inspect GPU numeric/layout/readback evidence; deterministic /completion returned an HTTP response but failed the required prompt check and API-to-executor reconciliation passed",
            runtime_freshness=runtime_freshness,
            runtime_env_manifest=runtime_env_manifest,
            responsibility_boundary="reconciled-gpu-correctness",
        ) | {
            "service_readiness": completion_readiness,
            "api_executor_reconciliation": api_executor_reconciliation,
            "observed_service_failure": "llama-completion-wrong-output",
            "q6_workgroup_diagnostics": q6,
            "q6_final_store_boundary": completion_q6_final_store_boundary,
            "q6_native_vs_writeback_split": completion_q6_native_vs_writeback_split,
            "q6_effective_blocker_class": completion_q6_effective_blocker_class,
            "runtime_env": nested(data, "gpu", "runtime_env") or {},
        }

    container_exit = _container_exit_evidence(data)
    if container_exit.get("exited_before_readiness") is True:
        api_prompt_sanity = _api_prompt_sanity(data)
        return _claim_base(
            "container-exited-before-readiness",
            next_action=(
                data.get("next_action")
                or "inspect container exit status, llama startup log, and GPU executor diagnostics before accepting Q6 or benchmark claims"
            ),
            runtime_freshness=runtime_freshness,
            runtime_env_manifest=runtime_env_manifest,
            responsibility_boundary="service-readiness",
        ) | {
            "container_exit": container_exit,
            "service_readiness": _service_readiness_summary(data),
            "api_prompt_sanity": api_prompt_sanity,
            "q6_workgroup_diagnostics": q6,
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

    bridge_binary_problems = _bridge_binary_identity_problems(runtime_freshness)
    if bridge_binary_problems:
        return _claim_base(
            "gpu-bridge-binary-freshness-mismatch",
            next_action="install the APK built from the current checkout and rerun compare; compare, correctness, and benchmark claims require matching GPU executor and Vulkan ICD binary hashes",
            runtime_freshness=runtime_freshness,
            runtime_env_manifest=runtime_env_manifest,
            responsibility_boundary="runtime-freshness",
        ) | {
            "bridge_binary_identity": _bridge_binary_identity(runtime_freshness),
            "bridge_binary_identity_problems": bridge_binary_problems,
        }

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
    if config_propagation_missing or _config_propagation_failed(
        config_propagation,
        q6_callsite_reached=q6_callsite_reached,
    ):
        manifest_misses = _config_propagation_manifest_misses(
            config_propagation,
            q6_callsite_reached=q6_callsite_reached,
        )
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
            "config_propagation_q6_callsite_reached": q6_callsite_reached,
            "q6_callsite_gated_config_envs": sorted(Q6_CALLSITE_GATED_CONFIG_ENVS),
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

    generic_cpu_oracle_mismatches = _generic_spirv_cpu_oracle_mismatch_evidence(data)
    if generic_cpu_oracle_mismatches:
        return _claim_base(
            "generic-spirv-cpu-oracle-mismatch",
            next_action=(
                data.get("next_action")
                or "fix the generic SPIR-V CPU oracle mismatch before accepting correctness or benchmark claims"
            ),
            runtime_freshness=runtime_freshness,
            runtime_env_manifest=runtime_env_manifest,
            responsibility_boundary="generic-spirv-cpu-oracle",
        ) | {
            "generic_spirv_cpu_oracle_mismatches": generic_cpu_oracle_mismatches,
            "config_propagation": config_propagation,
        }

    vulkan_passthrough_rewrite_evidence = _vulkan_shader_passthrough_rewrite_evidence(data)

    q6_evidence_reached = False
    if isinstance(q6, dict):
        try:
            q6_evidence_reached = int(q6.get("event_count", 0)) > 0
        except (TypeError, ValueError):
            q6_evidence_reached = False
    q6_oracle_evidence = q6_evidence_reached and q6.get("latest_status") in {"match", "mismatch"}

    api_prompt_sanity = _api_prompt_sanity(data)
    api_prompt_blocks_q6 = api_prompt_sanity.get("service_not_ready") is True
    if api_prompt_sanity.get("summary") == "fail" and (not q6_oracle_evidence or api_prompt_blocks_q6):
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
            "service_readiness": _service_readiness_summary(data),
            "q6_workgroup_diagnostics": q6,
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
    q6_descriptor_invariant_mismatches = _q6_descriptor_invariant_mismatches(q6)
    q6_shader_like = _q6_shader_like_interpretation(q6)
    q6_output_layout = _q6_output_layout_probe(q6)
    q6_row_provenance = _q6_row_provenance_probe(q6)
    q6_partial_signature = _q6_partial_signature_probe(q6)
    q6_debug_binding_alias_safety = _q6_debug_binding_alias_safety(q6)
    q6_debug_u32_probe = _q6_debug_u32_probe(q6)
    q6_debug_u32_probe_blocker = _q6_debug_u32_probe_blocker(q6)
    q6_final_store_boundary = _q6_final_store_boundary(q6)
    q6_stage_divergence = _q6_stage_divergence_evidence(q6)
    q6_output_index_probe = _q6_output_index_probe(q6)
    q6_output_index_probe_summary = str(q6_output_index_probe.get("summary") or "not-run")
    q6_workgroup_env_gap = _q6_workgroup_env_gap(runtime_env_manifest, config_propagation)
    q6_workgroup_evidence_status = _q6_workgroup_evidence_status(q6)
    q6_native_vs_writeback_split = _q6_native_vs_writeback_split(q6)
    q6_unexpected_readonly_dispatch_mutations = (
        q6.get("q6_unexpected_readonly_dispatch_mutations")
        if isinstance(q6.get("q6_unexpected_readonly_dispatch_mutations"), list)
        else []
    )
    q6_blocker_class = None
    if _q6_not_reached(q6):
        classification = "q6-not-reached"
        responsibility_boundary = "q6-not-reached"
        next_action = data.get("next_action") or "collect an ngl=1 artifact with Q6_K oracle enabled"
    elif _q6_probe_writeback_cleared_oracle_missing(q6, q6_writeback_evidence):
        classification = "q6-probe-writeback-cleared-oracle-missing"
        responsibility_boundary = "q6-diagnostic-evidence"
        q6_blocker_class = "q6-probe-writeback-cleared-oracle-missing"
        next_action = (
            data.get("next_action")
            or "fix compare/executor source-oracle retention for the instrumented Q6_K probe; writeback is verified, but the source-module CPU oracle is missing"
        )
    elif _q6_dispatch_seen_without_oracle(q6):
        classification = "q6-oracle-capture-missing"
        responsibility_boundary = "q6-diagnostic-evidence"
        next_action = (
            data.get("next_action")
            or "fix compare/executor evidence retention so every observed Q6_K/final-projection dispatch carries CPU-oracle diagnostics"
        )
    elif _q6_safe_kernel_enabled(q6) and q6.get("latest_status") == "match":
        classification = "q6-safe-kernel-diagnostic-only"
        responsibility_boundary = "q6-diagnostic-evidence"
        q6_blocker_class = "q6-safe-kernel-diagnostic-only"
        next_action = (
            data.get("next_action")
            or "rerun with the native Q6_K kernel; q6k_safe_kernel is diagnostic-only and cannot support native Q6 correctness or benchmark claims"
        )
    elif (
        _q6_workgroup_shape_blocked(q6)
        and not q6_descriptor_invariant_mismatches
        and q6_writeback_evidence.get("summary") == "pass"
    ):
        classification = "q6-workgroup-shape-blocker"
        responsibility_boundary = "q6-local-size"
        if q6_workgroup_evidence_status.get("evidence_failure"):
            if q6_workgroup_evidence_status.get("missing"):
                q6_blocker_class = "q6-workgroup-evidence-missing"
                next_action = (
                    "rerun or fix artifact emission so Q6 workgroup evidence fields are present before "
                    "interpreting arithmetic/reduction: "
                    + ",".join(q6_workgroup_evidence_status["missing"])
                )
            else:
                q6_blocker_class = "q6-workgroup-evidence-contradictory"
                next_action = (
                    "fix contradictory Q6 workgroup/local-size evidence before interpreting arithmetic/reduction"
                )
        elif q6_workgroup_env_gap.get("summary") == "fail":
            q6_blocker_class = "q6-workgroup-env-not-requested"
            next_action = (
                "rerun via scripts/android-llama-gpu-q6-workgroup-run.sh or the "
                "Q6 compare overlay; current artifact did not request "
                + "/".join(q6_workgroup_env_gap["missing_requested_envs"])
            )
        else:
            next_action = (
                "fix Q6_K local-size propagation/materialization to the expected "
                f"{_q6_expected_local_size(q6)} workgroup shape"
            )
    elif q6_descriptor_invariant_mismatches:
        classification = "q6-descriptor-invariant-mismatch"
        responsibility_boundary = "q6-descriptor-object-graph"
        q6_blocker_class = "descriptor-invariant-mismatch"
        next_action = "fix Vulkan descriptor/object-graph offset and range reconstruction before native Q6 shader classification"
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
    elif q6.get("q6_probe_effective_replay") is True:
        classification = "q6-probe-effective-replay-diagnostic-only"
        responsibility_boundary = "q6-diagnostic-evidence"
        q6_blocker_class = "q6-probe-effective-replay-diagnostic-only"
        next_action = (
            data.get("next_action")
            or "rerun the native Q6_K module without probe-effective replay before accepting correctness or benchmark claims"
        )
    elif _q6_compat_rewrite_used(q6) and q6.get("latest_status") == "match":
        classification = "q6-compat-rewrite-diagnostic-only"
        responsibility_boundary = "q6-diagnostic-evidence"
        q6_blocker_class = "q6-compat-rewrite-diagnostic-only"
        next_action = (
            data.get("next_action")
            or "rerun with native Q6_K SPIR-V and no Q6 compatibility rewrites before accepting correctness or benchmark claims"
        )
    elif (
        _shader_mutation_evidence_has_field(vulkan_passthrough_rewrite_evidence, "q4k_safe_kernel")
        and q6.get("latest_status") == "match"
    ):
        classification = "q4-safe-kernel-diagnostic-only"
        responsibility_boundary = "q4-diagnostic-evidence"
        q6_blocker_class = "q4-safe-kernel-diagnostic-only"
        next_action = (
            data.get("next_action")
            or "rerun without the Q4_K safe-kernel diagnostic replacement before accepting correctness or benchmark claims"
        )
    elif vulkan_passthrough_rewrite_evidence and q6.get("latest_status") == "match":
        classification = "vulkan-shader-mutation-diagnostic-only"
        responsibility_boundary = "vulkan-shader-identity"
        q6_blocker_class = "vulkan-shader-mutation-diagnostic-only"
        next_action = (
            data.get("next_action")
            or "rerun with original/effective/executable SPIR-V identity preserved; shader mutations cannot support pass-through correctness or benchmark claims"
        )
    elif q6_workgroup_evidence_status.get("evidence_failure"):
        classification = "q6-workgroup-shape-blocker"
        responsibility_boundary = "q6-local-size"
        if q6_workgroup_evidence_status.get("missing"):
            q6_blocker_class = "q6-workgroup-evidence-missing"
            next_action = (
                "rerun or fix artifact emission so Q6 workgroup evidence fields are present before "
                "interpreting arithmetic/reduction: "
                + ",".join(q6_workgroup_evidence_status["missing"])
            )
        else:
            q6_blocker_class = "q6-workgroup-evidence-contradictory"
            next_action = "fix contradictory Q6 workgroup/local-size evidence before interpreting arithmetic/reduction"
    elif q6.get("latest_status") == "match":
        classification = "q6-workgroup-cleared-and-oracle-match"
        responsibility_boundary = "q6-oracle-match"
        next_action = "advance to ngl=2 or performance tuning"
    elif q6.get("latest_status") == "mismatch":
        classification = "q6-workgroup-cleared-but-oracle-mismatch"
        responsibility_boundary = "q6-oracle"
        q6_blocker_class = str(q6.get("blocker_class") or "descriptor-memory-synchronization-or-q6-arithmetic")
        if q6_writeback_evidence.get("summary") == "pass":
            if q6_debug_binding_alias_safety.get("summary") == "fail":
                classification = "q6-debug-binding-alias"
                responsibility_boundary = "q6-debug-binding-alias"
                q6_blocker_class = "q6-debug-binding-alias"
            elif _q6_debug_alias_evidence_missing(
                q6_debug_binding_alias_safety,
                q6_debug_u32_probe,
                q6_debug_u32_probe_blocker,
                q6_final_store_boundary,
            ):
                classification = "q6-debug-binding-alias-evidence-missing"
                responsibility_boundary = "q6-debug-binding-alias"
                q6_blocker_class = "q6-debug-binding-alias-evidence-missing"
            elif q6_debug_u32_probe_blocker:
                classification = q6_debug_u32_probe_blocker
                responsibility_boundary = "q6-debug-u32-probe"
                q6_blocker_class = q6_debug_u32_probe_blocker
            elif q6_unexpected_readonly_dispatch_mutations:
                classification = "q6-readonly-dispatch-mutation"
                responsibility_boundary = "q6-readonly-dispatch-mutation"
                q6_blocker_class = "shader-readonly-mutation-or-barrier-scope"
            elif q6_blocker_class == "shader-readonly-mutation-or-barrier-scope":
                classification = "q6-readonly-dispatch-mutation-evidence-missing"
                responsibility_boundary = "q6-readonly-dispatch-mutation"
                q6_blocker_class = "shader-readonly-mutation-or-barrier-scope"
            elif (
                (
                    _q6_store_index_model_required(
                        q6_output_layout,
                        q6_row_provenance,
                        q6_partial_signature,
                        q6_native_vs_writeback_split,
                    )
                    or q6_final_store_boundary.get("summary") in {
                        "executor-writeback-mismatch",
                        "native-final-store-mismatch",
                    }
                )
                and not _q6_store_index_model_valid(q6, q6_output_layout)
            ):
                classification = "q6-store-index-model-incomplete"
                responsibility_boundary = "q6-oracle"
                q6_blocker_class = "q6-store-index-model-incomplete"
            elif q6_final_store_boundary.get("summary") == "executor-writeback-mismatch":
                classification = "q6-writeback-mismatch"
                responsibility_boundary = "q6-writeback"
                q6_blocker_class = "executor-final-writeback"
            elif q6_final_store_boundary.get("summary") == "native-final-store-mismatch":
                if q6_stage_divergence.get("summary") != "final-lane0-store-mismatch":
                    classification = "q6-stage-divergence-evidence-missing"
                    responsibility_boundary = "q6-stage-divergence"
                    q6_blocker_class = "q6-stage-divergence-evidence-missing"
                else:
                    classification = "q6-native-final-store"
                    responsibility_boundary = "q6-native-final-store"
                    q6_blocker_class = "native-q6-final-store"
            elif (
                q6_output_index_probe_summary == "final-store-value"
                and q6_shader_like["q6_shader_like_oracle_cleared"] is True
            ):
                if q6_stage_divergence.get("summary") != "final-lane0-store-mismatch":
                    classification = "q6-stage-divergence-evidence-missing"
                    responsibility_boundary = "q6-stage-divergence"
                    q6_blocker_class = "q6-stage-divergence-evidence-missing"
                else:
                    classification = "q6-native-final-store"
                    responsibility_boundary = "q6-native-final-store"
                    q6_blocker_class = "native-q6-final-store"
            elif q6_native_vs_writeback_split.get("summary") == "executor-final-writeback":
                classification = "q6-writeback-mismatch"
                responsibility_boundary = "q6-writeback"
                q6_blocker_class = "executor-final-writeback"
            elif q6_native_vs_writeback_split.get("summary") == "native-final-store-or-readback":
                classification = "q6-native-final-store-or-readback"
                responsibility_boundary = "q6-native-final-store-readback"
                q6_blocker_class = "native-q6-final-store-or-readback"
            elif (
                q6_output_layout.get("summary") == "canonical-mismatch-found-elsewhere"
                and _q6_store_index_model_valid(q6, q6_output_layout)
            ):
                classification = "q6-native-output-layout"
                responsibility_boundary = "q6-output-layout"
                q6_blocker_class = "native-q6-output-layout"
            elif q6_row_provenance.get("summary") == "other-row-match":
                if _q6_store_index_model_valid(q6, q6_output_layout):
                    classification = "q6-native-other-row-output-layout"
                    responsibility_boundary = "q6-output-layout"
                    q6_blocker_class = "native-q6-other-row-output-layout"
                else:
                    classification = "q6-store-index-model-incomplete"
                    responsibility_boundary = "q6-oracle"
                    q6_blocker_class = "q6-store-index-model-incomplete"
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
                and _q6_store_index_model_valid(q6, q6_output_layout)
            ):
                classification = "q6-native-device-execution-or-final-store"
                responsibility_boundary = "q6-native-device-execution"
                q6_blocker_class = "native-q6-device-execution-or-final-store"
            elif (
                q6_output_index_probe_summary in {
                    "elsewhere-outside-store-window",
                    "mixed-found-and-missing",
                    "inconclusive",
                }
                and _q6_store_index_model_valid(q6, q6_output_layout)
            ):
                classification = "q6-native-output-layout-inconclusive"
                responsibility_boundary = "q6-output-layout"
                q6_blocker_class = "native-q6-output-layout-inconclusive"
            elif (
                q6_output_layout.get("summary") == "canonical-mismatch-inconclusive"
                and _q6_store_index_model_valid(q6, q6_output_layout)
            ):
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
        "q6_output_index_probe": q6_output_index_probe,
        "q6_output_index_probe_summary": q6_output_index_probe_summary,
        "q6_workgroup_env_gap": q6_workgroup_env_gap,
        "q6_workgroup_evidence_status": q6_workgroup_evidence_status,
        "q6_row_provenance_probe": q6_row_provenance,
        "q6_partial_signature_probe": q6_partial_signature,
        "q6_debug_binding_alias_safety": q6_debug_binding_alias_safety,
        "q6_debug_u32_probe": q6_debug_u32_probe,
        "q6_debug_u32_probe_blocker": q6_debug_u32_probe_blocker,
        "q6_final_store_boundary": q6_final_store_boundary,
        "q6_stage_divergence": q6_stage_divergence,
        "q6_native_vs_writeback_split": q6_native_vs_writeback_split,
        "q6_unexpected_readonly_dispatch_mutations": q6_unexpected_readonly_dispatch_mutations[:8],
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
                "q6-native-final-store",
                "q6-native-final-store-or-readback",
                "q6-native-device-execution-or-final-store",
                "q6-native-reduction-or-device-execution",
                "q6-readonly-dispatch-mutation",
                "q6-readonly-dispatch-mutation-evidence-missing",
                "q6-probe-writeback-cleared-oracle-missing",
                "q6-workgroup-shape-blocker",
                "q6-safe-kernel-diagnostic-only",
                "q6-probe-effective-replay-diagnostic-only",
                "q6-compat-rewrite-diagnostic-only",
                "q4-safe-kernel-diagnostic-only",
                "vulkan-shader-mutation-diagnostic-only",
                "q6-descriptor-invariant-mismatch",
                "q6-debug-binding-alias-evidence-missing",
                *Q6_DEBUG_U32_BLOCKERS,
            }
            else None
        ),
        "q6_descriptor_invariant_mismatches": q6_descriptor_invariant_mismatches,
        "q6_writeback_evidence": q6_writeback_evidence,
        "vulkan_shader_passthrough_rewrite_evidence": vulkan_passthrough_rewrite_evidence,
        "runtime_freshness": runtime_freshness,
        "runtime_env_manifest": runtime_env_manifest,
        "spirv_raw_dump_evidence": diagnostics.get("spirv_raw_dump_evidence") or {},
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
    if classification in {"early-compare-failure", "early-compare-timeout"}:
        return 23
    if classification in {
        "container-exited-before-readiness",
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
    if classification == "gpu-bridge-binary-freshness-mismatch":
        return 51
    if classification == "vulkan-pipeline-feature-evidence-missing":
        return 43
    if classification == "config-propagation-mismatch":
        return 35
    if classification == "unsupported-gpu-work-accepted":
        return 36
    if classification == "oracle-fail-closed":
        return 37
    if classification == "generic-spirv-cpu-oracle-mismatch":
        return 47
    if classification in {"q6-oracle-capture-missing", "q6-probe-writeback-cleared-oracle-missing"}:
        return 48
    if classification in Q6_DEBUG_U32_BLOCKERS:
        return 49
    if classification == "api-prompt-sanity-missing":
        return 38
    if classification == "speedup-fields-missing":
        return 39
    if classification == "q6-descriptor-invariant-mismatch":
        return 50
    if classification == "q6-writeback-mismatch":
        return 40
    if classification == "q6-writeback-unverified":
        return 41
    if classification == "q6-readonly-dispatch-mutation-evidence-missing":
        return 49
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
            "q6-native-final-store",
            "q6-native-final-store-or-readback",
            "q6-native-device-execution-or-final-store",
            "q6-native-reduction-or-device-execution",
            "q6-readonly-dispatch-mutation",
        } else 31
    if classification == "q6-workgroup-shape-blocker":
        return 32
    if classification in {"q6-not-reached", "q6-inconclusive"}:
        return 33
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

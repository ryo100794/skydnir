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
import sys
from pathlib import Path
from typing import Any


MEMORY_ERRORS = {"insufficient_memory", "runtime_memory_pressure"}


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


def _config_propagation_failed(config_propagation: dict[str, Any]) -> bool:
    if config_propagation.get("summary") == "fail":
        return True
    checks = config_propagation.get("checks") or []
    if not isinstance(checks, list):
        return False
    for check in checks:
        if isinstance(check, dict) and check.get("status") in {"missing-evidence", "mismatch"}:
            return True
    return False


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
    if _config_propagation_failed(config_propagation):
        return _claim_base(
            "config-propagation-mismatch",
            next_action=(
                data.get("next_action")
                or "fix GPU diagnostic environment propagation before accepting compare, correctness, or benchmark claims"
            ),
            runtime_freshness=runtime_freshness,
        ) | {"config_propagation": config_propagation}

    if not q6:
        classification = "q6-not-reached"
        next_action = data.get("next_action") or "collect an ngl=1 artifact with Q6_K oracle enabled"
    elif q6.get("workgroup_shape_blocker") is True:
        classification = "q6-workgroup-shape-blocker"
        next_action = "fix Q6_K local-size propagation/materialization"
    elif q6.get("latest_status") == "match":
        classification = "q6-workgroup-cleared-and-oracle-match"
        next_action = "advance to ngl=2 or performance tuning"
    elif q6.get("latest_status") == "mismatch":
        classification = "q6-workgroup-cleared-but-oracle-mismatch"
        next_action = "continue with descriptor identity, memory residency, synchronization, or Q6_K arithmetic interpretation"
    else:
        classification = "q6-inconclusive"
        next_action = data.get("next_action") or "rerun with PDOCKER_GPU_CPU_ORACLE=1"

    correctness_claim_allowed = correctness == "pass" and classification != "q6-workgroup-shape-blocker"
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
        "runtime_freshness": runtime_freshness,
        "config_propagation": config_propagation,
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

#!/usr/bin/env python3
"""Verify the no-PRoot/direct runtime truth-gate artifact.

This verifier intentionally accepts the current non-promoting fail-closed
contract: when the direct executor does not advertise ``process-exec=1``,
container process operations must fail with capability diagnostics, health must
not become healthy, and published ports must remain planned/inactive rather
than active.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REQUIRED_SCHEMA = "pdocker.android.no-proot-runtime-truth.v1"
REQUIRED_OPERATIONS = ("docker_run", "docker_exec", "dockerfile_run")
CAPABILITY_DIAGNOSTIC_NEEDLES = (
    "process-exec=1",
    "no-proot/direct",
    "direct android executor",
    "cannot execute container processes",
    "real container process executor",
    "runtimebackend executor implementation",
)
ACCEPTED_NON_PROMOTING_STATUSES = {
    "planned-gap",
    "fail-closed",
    "blocked-runtime-gap",
}
ACCEPTED_PORT_STATES = {"planned", "inactive"}


class VerificationError(ValueError):
    pass


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise VerificationError(f"missing no-PRoot runtime truth artifact: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise VerificationError(f"invalid no-PRoot runtime truth JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise VerificationError("no-PRoot runtime truth artifact must be a JSON object")
    return data


def _status(data: dict[str, Any]) -> str:
    value = data.get("status", data.get("Status", ""))
    return str(value).strip().lower() if value is not None else ""


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True)


def _has_capability_diagnostic(value: Any) -> bool:
    text = _text(value).lower()
    return any(needle in text for needle in CAPABILITY_DIAGNOSTIC_NEEDLES)


def _require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def _direct_probe(data: dict[str, Any]) -> dict[str, Any]:
    probe = data.get("direct_executor_probe") or data.get("process_exec_probe")
    return probe if isinstance(probe, dict) else {}


def _operation(data: dict[str, Any], name: str) -> dict[str, Any]:
    operations = data.get("operations")
    if not isinstance(operations, dict):
        return {}
    value = operations.get(name)
    return value if isinstance(value, dict) else {}


def _port_statuses(ports: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("status_cases", "statuses", "PortMappingStatus"):
        value = ports.get(key)
        if isinstance(value, list):
            return [entry for entry in value if isinstance(entry, dict)]
    return []


def validate_artifact_data(data: dict[str, Any]) -> list[str]:
    """Return all truth-gate validation errors for an artifact."""
    errors: list[str] = []

    status = _status(data)
    _require(data.get("schema") == REQUIRED_SCHEMA, f"schema must be {REQUIRED_SCHEMA}", errors)
    _require(status in ACCEPTED_NON_PROMOTING_STATUSES,
             f"status must be one of {sorted(ACCEPTED_NON_PROMOTING_STATUSES)} for the current non-promoting gate", errors)
    _require(data.get("success") is False, "success must be false while process-exec=1 is absent", errors)
    _require("non-promoting" in _text(data.get("promotion")).lower(), "promotion must explicitly be non-promoting", errors)

    probe = _direct_probe(data)
    _require(isinstance(probe, dict) and bool(probe), "direct_executor_probe must be an object", errors)
    _require(probe.get("process_exec") is False, "direct_executor_probe.process_exec must be false", errors)
    _require(probe.get("advertises_process_exec_1") is False,
             "direct_executor_probe.advertises_process_exec_1 must be false", errors)
    _require("process-exec=1" not in _text(probe.get("output")),
             "direct executor probe output must not advertise process-exec=1", errors)
    _require(
        _has_capability_diagnostic(probe) or "process-exec=0" in _text(probe.get("output")),
        "direct executor probe must show the missing process-exec=1 capability",
        errors,
    )

    operations = data.get("operations")
    _require(isinstance(operations, dict), "operations must be an object", errors)
    for name in REQUIRED_OPERATIONS:
        op = _operation(data, name)
        _require(bool(op), f"operations.{name} must be present", errors)
        _require(op.get("attempted") is True, f"operations.{name}.attempted must be true", errors)
        _require(op.get("success") is False, f"operations.{name}.success must be false", errors)
        _require(op.get("capability_error") is True, f"operations.{name}.capability_error must be true", errors)
        _require(op.get("forbidden_success_claim") is False,
                 f"operations.{name}.forbidden_success_claim must be false", errors)
        _require(_has_capability_diagnostic(op.get("diagnostic") or op.get("stderr") or op.get("output")),
                 f"operations.{name} must include a runtime capability diagnostic", errors)

    health = data.get("health")
    _require(isinstance(health, dict), "health must be an object", errors)
    if isinstance(health, dict):
        final_status = str(health.get("final_status") or health.get("Status") or "").strip().lower()
        _require(health.get("cannot_become_healthy") is True, "health.cannot_become_healthy must be true", errors)
        _require(health.get("has_healthy_claim") is False, "health.has_healthy_claim must be false", errors)
        _require(final_status != "healthy", "health final_status must not be healthy", errors)
        _require(health.get("running") is False, "health.running must be false after capability failure", errors)
        _require(_has_capability_diagnostic(health.get("diagnostic") or health.get("log")),
                 "health must include the process capability failure diagnostic", errors)

    ports = data.get("ports")
    _require(isinstance(ports, dict), "ports must be an object", errors)
    if isinstance(ports, dict):
        _require(ports.get("planned_or_inactive_only") is True,
                 "ports.planned_or_inactive_only must be true", errors)
        _require(ports.get("active_count") == 0, "ports.active_count must be 0", errors)
        statuses = _port_statuses(ports)
        _require(bool(statuses), "ports must include status_cases/statuses", errors)
        seen_states: set[str] = set()
        for index, entry in enumerate(statuses):
            state = str(entry.get("State") or entry.get("state") or "").strip().lower()
            seen_states.add(state)
            _require(state in ACCEPTED_PORT_STATES,
                     f"ports status {index} must be planned/inactive, got {state or '<missing>'}", errors)
            _require(entry.get("Active") is False or entry.get("active") is False,
                     f"ports status {index} Active must be false", errors)
        _require(bool(seen_states & ACCEPTED_PORT_STATES), "ports must prove planned/inactive states", errors)
        summary = ports.get("summary")
        if isinstance(summary, dict) and "Active" in summary:
            _require(summary.get("Active") == 0, "ports.summary.Active must be 0", errors)

    evidence = data.get("evidence")
    _require(isinstance(evidence, dict), "evidence must be an object", errors)
    if isinstance(evidence, dict):
        _require(bool(evidence.get("mode") or evidence.get("source")), "evidence must name a mode/source", errors)
        _require("scripts/android-no-proot-runtime-truth-gate.sh" in _text(evidence),
                 "evidence must reference scripts/android-no-proot-runtime-truth-gate.sh", errors)

    return errors


def verify(path: Path) -> dict[str, Any]:
    data = _read_json(path)
    errors = validate_artifact_data(data)
    if errors:
        raise VerificationError("; ".join(errors))
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path, help="docs/test/no-proot-runtime-truth-latest.json artifact to verify")
    args = parser.parse_args(argv)
    try:
        verify(args.artifact)
    except VerificationError as exc:
        print(f"no-PRoot runtime truth artifact invalid: {exc}", file=sys.stderr)
        return 2
    print(f"ok: {args.artifact}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

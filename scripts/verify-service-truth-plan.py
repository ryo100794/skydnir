#!/usr/bin/env python3
"""Validate the executable acceptance plan for service truth and teardown.

This is intentionally a static gate while the Android/runtime implementation is
still a planned gap.  It prevents the gap from drifting back to vague prose: the
ledger must spell out runnable smoke commands, required evidence sources,
negative cases, and exit criteria before the gap may be closed.
"""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "tests" / "feature_scenarios.json"
DOCS = [ROOT / "docs" / "test" / "COMPATIBILITY.md", ROOT / "docs" / "plan" / "TODO.md"]
ANDROID_SMOKE = ROOT / "scripts" / "android-device-smoke.sh"

SERVICE_ID = "service.health.listener-container-proof"
TEARDOWN_ID = "runtime.stop-process-tree-teardown"

SERVICE_SOURCES = {
    "ui_card",
    "engine_api_containers_json",
    "persisted_state_json",
    "process_table",
    "listener_probe",
    "container_logs",
}
TEARDOWN_SOURCES = {
    "engine_api_containers_json",
    "persisted_state_json",
    "process_table",
    "lifecycle_logs",
    "container_logs",
}


def fail(message: str) -> None:
    print(f"verify-service-truth-plan: FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def ok(message: str) -> None:
    print(f"ok: {message}")


def load_ledger() -> dict[str, Any]:
    try:
        return json.loads(LEDGER.read_text())
    except OSError as exc:
        fail(f"could not read {LEDGER.relative_to(ROOT)}: {exc}")
    except json.JSONDecodeError as exc:
        fail(f"{LEDGER.relative_to(ROOT)} is not valid JSON: {exc}")


def scenario_by_id(data: dict[str, Any], sid: str) -> dict[str, Any]:
    for scenario in data.get("scenarios", []):
        if scenario.get("id") == sid:
            return scenario
    fail(f"missing scenario {sid}")


def require_terms(name: str, text: str, terms: list[str]) -> None:
    missing = [term for term in terms if term.lower() not in text.lower()]
    if missing:
        fail(f"{name} is missing required terms: {', '.join(missing)}")


def require_list(scenario: dict[str, Any], key: str, min_len: int) -> list[str]:
    value = scenario.get(key)
    if not isinstance(value, list) or len(value) < min_len or not all(isinstance(item, str) and item.strip() for item in value):
        fail(f"{scenario.get('id')} must define {key} with at least {min_len} non-empty strings")
    return value


def validate_command_tokens(scenario: dict[str, Any], commands: list[str]) -> None:
    for command in commands:
        try:
            tokens = shlex.split(command)
        except ValueError as exc:
            fail(f"{scenario.get('id')} has non-tokenizable command {command!r}: {exc}")
        if not tokens:
            fail(f"{scenario.get('id')} has an empty command")
        for token in tokens:
            if token.startswith(("scripts/", "tests/", "docs/", "docker-proot-setup/")):
                path = ROOT / token
                if not path.exists():
                    fail(f"{scenario.get('id')} command references missing path {token}")


def validate_planned_acceptance(scenario: dict[str, Any], *, required_sources: set[str], evidence_suffix: str) -> None:
    sid = scenario.get("id")
    if scenario.get("status") != "planned-gap":
        fail(f"{sid} must remain planned-gap until device evidence exists")
    if scenario.get("plan_gate") != "python3 scripts/verify-service-truth-plan.py":
        fail(f"{sid} must point plan_gate at this verifier")
    evidence_target = str(scenario.get("evidence_target") or "")
    if not evidence_target.startswith("docs/test/") or not evidence_target.endswith(evidence_suffix):
        fail(f"{sid} must define docs/test/*{evidence_suffix} evidence_target")

    smoke_commands = require_list(scenario, "smoke_commands", 2)
    validate_command_tokens(scenario, smoke_commands)

    sources = set(require_list(scenario, "required_truth_sources", len(required_sources)))
    missing_sources = sorted(required_sources - sources)
    if missing_sources:
        fail(f"{sid} is missing truth sources: {', '.join(missing_sources)}")

    negative_cases = require_list(scenario, "negative_cases", 3)
    exit_criteria = require_list(scenario, "exit_criteria", 4)
    combined = "\n".join([str(scenario.get("checks", "")), *negative_cases, *exit_criteria])
    require_terms(
        sid,
        combined,
        ["Engine container ID", "state", "process", "logs", "stale", "mismatch" if sid == SERVICE_ID else "204"],
    )


def validate_docs() -> None:
    combined = "\n".join(path.read_text() for path in DOCS)
    require_terms(
        "service truth docs",
        combined,
        [
            "python3 scripts/verify-service-truth-plan.py",
            "docs/test/service-truth-latest.json",
            "docs/test/runtime-teardown-latest.json",
            "UI card",
            "/containers/json",
            "state.json",
            "process table",
            "listener probe",
            "logs",
            "Engine container ID",
            "engine-candidates.json",
            "state-id-comparison.json",
            "listener-probe.json",
            "ui-rendered-service-truth-latest.json",
            "TruthState",
            "/proc/net/tcp",
        ],
    )


def validate_android_smoke_entrypoints() -> None:
    smoke = ANDROID_SMOKE.read_text()
    require_terms(
        "android smoke service truth entrypoints",
        smoke,
        [
            "--service-truth",
            "--runtime-teardown",
            "service_truth_acceptance_entrypoint",
            "runtime_teardown_acceptance_entrypoint",
            "service-truth-latest.json",
            "runtime-teardown-latest.json",
            "TruthContract",
            "RequiredSameContainerId",
            "UICard",
            "EngineApiContainersJson",
            "PersistedStateJson",
            "ProcessTable",
            "ListenerProbe",
            "CandidateSelection",
            "engine-candidates.json",
            "state-id-comparison.json",
            "listener-probe.json",
            "ui-rendered-service-truth-latest.json",
            "/proc/net/tcp",
            "LifecycleLogs",
            "ContainerLogs",
            "\"Success\": false",
        ],
    )


def main() -> int:
    data = load_ledger()
    service = scenario_by_id(data, SERVICE_ID)
    teardown = scenario_by_id(data, TEARDOWN_ID)
    validate_planned_acceptance(service, required_sources=SERVICE_SOURCES, evidence_suffix="service-truth-latest.json")
    validate_planned_acceptance(teardown, required_sources=TEARDOWN_SOURCES, evidence_suffix="runtime-teardown-latest.json")
    validate_docs()
    validate_android_smoke_entrypoints()
    ok("service truth planned gap has runnable commands, evidence contract, negative cases, and exit criteria")
    ok("runtime teardown planned gap has runnable commands, evidence contract, negative cases, and exit criteria")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

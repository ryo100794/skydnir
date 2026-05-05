#!/usr/bin/env python3
"""Validate the feature-level test scenario ledger."""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "tests" / "feature_scenarios.json"


def fail(message: str) -> None:
    print(f"verify-feature-scenarios: FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def ok(message: str) -> None:
    print(f"ok: {message}")


def load() -> dict:
    try:
        data = json.loads(LEDGER.read_text())
    except OSError as exc:
        fail(f"could not read {LEDGER.relative_to(ROOT)}: {exc}")
    except json.JSONDecodeError as exc:
        fail(f"{LEDGER.relative_to(ROOT)} is not valid JSON: {exc}")
    if data.get("schema") != 1:
        fail("scenario ledger schema must be 1")
    return data


def command_paths(command: str) -> list[Path]:
    try:
        parts = shlex.split(command)
    except ValueError as exc:
        fail(f"command is not shell-tokenizable: {command!r}: {exc}")
    paths: list[Path] = []
    for part in parts:
        if part.startswith(("scripts/", "tests/", "docs/", "docker-proot-setup/")):
            paths.append(ROOT / part)
    return paths


def main() -> int:
    data = load()
    lanes = data.get("lanes")
    required_areas = set(data.get("required_areas", []))
    scenarios = data.get("scenarios")
    if not isinstance(lanes, dict) or not lanes:
        fail("lanes must be a non-empty object")
    if not required_areas:
        fail("required_areas must be non-empty")
    if not isinstance(scenarios, list) or not scenarios:
        fail("scenarios must be a non-empty list")

    ids: set[str] = set()
    areas: set[str] = set()
    lanes_seen: set[str] = set()
    statuses_seen: set[str] = set()
    runnable_fast = 0
    device_count = 0
    planned_gap_count = 0
    for scenario in scenarios:
        if not isinstance(scenario, dict):
            fail("scenario entries must be objects")
        sid = str(scenario.get("id") or "")
        area = str(scenario.get("area") or "")
        lane = str(scenario.get("lane") or "")
        command = str(scenario.get("command") or "")
        status = str(scenario.get("status") or "")
        checks = str(scenario.get("checks") or "")
        docs = scenario.get("docs")
        if not sid or not area or not lane or not command or not status or not checks:
            fail(f"scenario is incomplete: {scenario!r}")
        if sid in ids:
            fail(f"duplicate scenario id: {sid}")
        ids.add(sid)
        if lane not in lanes:
            fail(f"{sid} uses unknown lane {lane!r}")
        if area not in required_areas:
            fail(f"{sid} uses area {area!r} not listed in required_areas")
        if not isinstance(docs, list) or not docs:
            fail(f"{sid} must link at least one doc")
        for doc in docs:
            path = ROOT / str(doc)
            if not path.exists():
                fail(f"{sid} references missing doc/evidence {doc}")
        for path in command_paths(command):
            if not path.exists():
                fail(f"{sid} command references missing path {path.relative_to(ROOT)}")
        areas.add(area)
        lanes_seen.add(lane)
        statuses_seen.add(status)
        if lane == "fast-local" and status == "runnable":
            runnable_fast += 1
        if lane.startswith("android"):
            device_count += 1
        if status == "planned-gap":
            planned_gap_count += 1

    missing_areas = sorted(required_areas - areas)
    if missing_areas:
        fail("required areas missing scenarios: " + ", ".join(missing_areas))
    for required_lane in ("fast-local", "heavy-container", "android-quick", "android-documents", "android-gpu", "android-llama", "release"):
        if required_lane not in lanes_seen:
            fail(f"lane {required_lane!r} has no scenario")
    if runnable_fast < 10:
        fail("fast-local runnable coverage is too thin")
    if device_count < 5:
        fail("device scenario coverage is too thin")
    if "planned-gap" not in statuses_seen or planned_gap_count < 1:
        fail("scenario ledger must explicitly track at least one planned compatibility gap")

    verify_fast = (ROOT / "scripts" / "verify-fast.sh").read_text()
    for command in (
        "python3 scripts/verify-feature-scenarios.py",
        "python3 scripts/verify-abnormal-events.py",
        "python3 scripts/verify-refactor-resilience.py",
        "python3 scripts/verify-test-design-criteria.py",
        "python3 scripts/verify-input-grammar-coverage.py",
        "python3 scripts/verify-input-validation.py",
        "python3 scripts/verify-stress-regression.py",
        "python3 scripts/verify-blackbox-requirements.py",
        "python3 scripts/verify-ui-actions.py",
        "python3 scripts/verify-project-library.py",
        "python3 scripts/verify-storage-metrics.py",
    ):
        if command not in verify_fast:
            fail(f"verify-fast.sh must include {command}")

    ok(f"feature scenario ledger covers {len(areas)} areas with {len(scenarios)} scenarios")
    ok(f"feature scenario ledger has {runnable_fast} fast runnable scenarios and {device_count} device scenarios")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

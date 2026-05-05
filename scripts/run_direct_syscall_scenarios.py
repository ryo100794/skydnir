#!/usr/bin/env python3
"""Run or list direct-runtime syscall scenarios from the coverage manifest.

The manifest in tests/direct_syscall_coverage.json is the source of truth for
coverage. This runner turns it into an execution plan so fast/local cases can
run in CI while Android/device cases stay explicit and repeatable.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests" / "direct_syscall_coverage.json"

PLACEHOLDER_PREFIXES = (
    "Run ",
    "Create ",
)
STATUS_RUNNABLE = "runnable"
STATUS_PLANNED = "planned"
LANE_LOCAL = "local"


def repo_command(*args: str) -> list[str]:
    return [sys.executable if args[0] == "python3" else args[0], *args[1:]]


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except OSError as exc:
        fail(f"could not read {path}: {exc}")
    except json.JSONDecodeError as exc:
        fail(f"{path} is not valid JSON: {exc}")
    if data.get("schema") != 1:
        fail("direct syscall scenario manifest schema must be 1")
    return data


def scenario_cases(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    raw = manifest.get("heavy_cases")
    if not isinstance(raw, list):
        fail("manifest must define heavy_cases")
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            fail("heavy_cases entries must be objects")
        case_id = str(item.get("id") or "")
        tier = str(item.get("tier") or "")
        command = str(item.get("command") or "")
        checks = str(item.get("checks") or "")
        if not case_id or not tier or not command or not checks:
            fail(f"scenario case is incomplete: {item!r}")
        out.append(item)
    return out


def is_runnable(case: dict[str, Any]) -> bool:
    if case.get("runnable") is True:
        return True
    command = str(case.get("command") or "")
    if case.get("runnable") is False:
        return False
    if command.startswith(PLACEHOLDER_PREFIXES):
        return False
    if str(case.get("tier")) == "heavy-android" and not command.startswith("adb "):
        return False
    return True


def case_status(case: dict[str, Any]) -> str:
    return STATUS_RUNNABLE if is_runnable(case) else STATUS_PLANNED


def filter_cases(
    all_cases: list[dict[str, Any]],
    tier: str | None,
    case_id: str | None,
    status: str | None,
) -> list[dict[str, Any]]:
    if case_id and not any(str(case.get("id")) == case_id for case in all_cases):
        fail(f"unknown direct syscall scenario case: {case_id}")
    selected = []
    for case in all_cases:
        if tier and str(case.get("tier")) != tier:
            continue
        if case_id and str(case.get("id")) != case_id:
            continue
        if status and case_status(case) != status:
            continue
        selected.append(case)
    return selected


def print_case(case: dict[str, Any], verbose: bool = False) -> None:
    marker = "run" if case_status(case) == STATUS_RUNNABLE else "plan"
    print(f"{marker:4} {case['tier']:13} {case['id']}")
    if verbose:
        print(f"      command: {case['command']}")
        print(f"      checks: {case['checks']}")


def json_case(case: dict[str, Any]) -> dict[str, Any]:
    out = {
        "id": case["id"],
        "tier": case["tier"],
        "status": case_status(case),
        "runnable": is_runnable(case),
        "command": case["command"],
        "checks": case["checks"],
    }
    if "notes" in case:
        out["notes"] = case["notes"]
    return out


def print_json(cases: list[dict[str, Any]]) -> None:
    print(json.dumps({"schema": 1, "cases": [json_case(case) for case in cases]}, indent=2))


def lane_commands(lane: str) -> list[tuple[str, list[str]]]:
    if lane != LANE_LOCAL:
        fail(f"unknown direct syscall scenario lane: {lane}")
    return [
        (
            "static direct-executor syscall inventory",
            repo_command("python3", "scripts/verify_direct_syscall_contracts.py"),
        ),
        (
            "isolated scenario manifest contracts",
            repo_command(
                "python3",
                "-m",
                "unittest",
                "discover",
                "-s",
                "tests/direct_syscall",
                "-p",
                "test_*.py",
            ),
        ),
        (
            "fast-local scenario list",
            repo_command(
                "python3",
                "scripts/run_direct_syscall_scenarios.py",
                "--tier",
                "fast-local",
                "--list",
                "--verbose",
            ),
        ),
        (
            "generic container scenario dry-run",
            repo_command(
                "python3",
                "scripts/run_direct_syscall_scenarios.py",
                "--tier",
                "heavy-container",
                "--execute",
                "--dry-run",
            ),
        ),
        (
            "Android scenario dry-run",
            repo_command(
                "python3",
                "scripts/run_direct_syscall_scenarios.py",
                "--tier",
                "heavy-android",
                "--execute",
                "--dry-run",
            ),
        ),
    ]


def run_lane(lane: str, dry_run: bool) -> int:
    rc = 0
    for label, command in lane_commands(lane):
        printable = " ".join(command)
        print(f"lane {lane}: {label}")
        print(f"      exec: {printable}")
        if dry_run:
            print("      dry-run: command not executed")
            continue
        sys.stdout.flush()
        env = os.environ.copy()
        env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
        result = subprocess.run(command, cwd=ROOT, env=env, text=True)
        if result.returncode != 0:
            print(f"      FAIL: lane step exited {result.returncode}", file=sys.stderr)
        rc = max(rc, result.returncode)
    return rc


def run_case(case: dict[str, Any], dry_run: bool) -> int:
    command = str(case["command"])
    print_case(case, verbose=False)
    if not is_runnable(case):
        print("      skip: scenario is still an implementation plan")
        return 0
    print(f"      exec: {command}")
    sys.stdout.flush()
    if dry_run:
        print("      dry-run: command not executed")
        return 0
    env = os.environ.copy()
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    result = subprocess.run(command, cwd=ROOT, env=env, shell=True, text=True)
    if result.returncode != 0:
        print(f"      FAIL: {case['id']} exited {result.returncode}", file=sys.stderr)
    return result.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--tier", choices=["fast-local", "heavy-container", "heavy-android"])
    parser.add_argument("--case", dest="case_id")
    parser.add_argument("--status", choices=[STATUS_RUNNABLE, STATUS_PLANNED], help="filter by execution status")
    parser.add_argument("--list", action="store_true", help="list selected scenarios")
    parser.add_argument("--json", action="store_true", help="write selected scenarios as JSON")
    parser.add_argument("--execute", action="store_true", help="execute runnable selected scenarios")
    parser.add_argument("--dry-run", action="store_true", help="print selected executable commands without running them")
    parser.add_argument("--verbose", action="store_true", help="include commands and checks when listing")
    parser.add_argument(
        "--lane",
        choices=[LANE_LOCAL],
        help="run an isolated syscall-coverage acceptance lane; local never requires adb",
    )
    args = parser.parse_args(argv)

    if args.lane:
        return run_lane(args.lane, dry_run=args.dry_run)

    manifest = load_manifest(args.manifest)
    selected = filter_cases(scenario_cases(manifest), args.tier, args.case_id, args.status)

    if args.json:
        print_json(selected)
        return 0

    if args.list or not args.execute:
        for case in selected:
            print_case(case, verbose=args.verbose)
        return 0

    rc = 0
    for case in selected:
        rc = max(rc, run_case(case, dry_run=args.dry_run))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())

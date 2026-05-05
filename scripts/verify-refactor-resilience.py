#!/usr/bin/env python3
"""Validate refactor-resilience test design for external contracts."""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "tests" / "refactor_resilience_cases.json"
REQUIRED_AXES = {
    "engine-api-golden",
    "compose-dockerfile-fixtures",
    "archive-round-trip",
    "state-machine-contract",
    "abnormal-replay",
    "artifact-diff",
}
ALLOWED_STATUSES = {"runnable", "planned-gap"}
ALLOWED_CONTRACT_CLASSES = {"intended", "documented-limitation", "known-bug-blocker"}
IMPLEMENTATION_TOKENS = (
    "pdocker_direct_exec",
    "TraceeState",
    "rewrite_",
    "g_rootfs_fd",
    "libcow",
    "libpdockerdirect",
    "function",
)


def fail(message: str) -> None:
    print(f"verify-refactor-resilience: FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def ok(message: str) -> None:
    print(f"ok: {message}")


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except OSError as exc:
        fail(f"could not read {path.relative_to(ROOT)}: {exc}")
    except json.JSONDecodeError as exc:
        fail(f"{path.relative_to(ROOT)} is not valid JSON: {exc}")
    if data.get("schema") != 1:
        fail(f"{path.relative_to(ROOT)} schema must be 1")
    return data


def relative_repo_path(raw: str, context: str) -> Path:
    path = Path(raw)
    if path.is_absolute() or ".." in path.parts:
        fail(f"{context} must be a repository-relative path without '..': {raw}")
    return ROOT / path


def command_paths(command: str) -> list[Path]:
    try:
        parts = shlex.split(command)
    except ValueError as exc:
        fail(f"command is not shell-tokenizable: {command!r}: {exc}")
    paths: list[Path] = []
    for part in parts:
        if part.startswith(("scripts/", "tests/", "docs/", "docker-proot-setup/")):
            paths.append(relative_repo_path(part, "command path"))
    return paths


def validate_case(case: dict[str, Any]) -> None:
    case_id = str(case.get("id") or "")
    if not case_id:
        fail("refactor-resilience case is missing id")
    for key in (
        "axis",
        "status",
        "lane",
        "command",
        "contract_class",
        "observable_contract",
        "invariant",
        "refactor_risk",
        "evidence_artifact",
        "evidence_required_fast",
    ):
        if key not in case or case.get(key) in ("", None):
            fail(f"{case_id} is missing {key}")
    if case["axis"] not in REQUIRED_AXES:
        fail(f"{case_id} uses unknown axis {case['axis']!r}")
    if case["status"] not in ALLOWED_STATUSES:
        fail(f"{case_id} uses unknown status {case['status']!r}")
    if case["contract_class"] not in ALLOWED_CONTRACT_CLASSES:
        fail(f"{case_id} uses unknown contract_class {case['contract_class']!r}")
    if case["status"] == "runnable" and case["contract_class"] == "known-bug-blocker":
        fail(f"{case_id} must not freeze a known bug as a passing refactor contract")
    if case["contract_class"] == "known-bug-blocker" and not case.get("gap_reason"):
        fail(f"{case_id} known-bug-blocker must include gap_reason")
    if type(case["evidence_required_fast"]) is not bool:
        fail(f"{case_id} evidence_required_fast must be a boolean")
    for path in command_paths(str(case["command"])):
        if not path.exists():
            fail(f"{case_id} command references missing path {path.relative_to(ROOT)}")
    artifact = relative_repo_path(str(case["evidence_artifact"]), f"{case_id} evidence_artifact")
    if case["status"] == "runnable" and case["evidence_required_fast"] and not artifact.exists():
        fail(f"{case_id} runnable evidence artifact is missing: {case['evidence_artifact']}")
    if case["status"] == "planned-gap" and not case.get("gap_reason"):
        fail(f"{case_id} planned-gap must include gap_reason")
    public_text = "\n".join(str(case.get(key) or "") for key in ("observable_contract", "invariant", "refactor_risk"))
    leaked = [token for token in IMPLEMENTATION_TOKENS if token.lower() in public_text.lower()]
    if leaked:
        fail(f"{case_id} observable contract leaks implementation token(s): {', '.join(leaked)}")


def validate_ledger(data: dict[str, Any]) -> list[dict[str, Any]]:
    policy = data.get("policy")
    artifact_policy = data.get("artifact_policy")
    cases = data.get("cases")
    if not isinstance(policy, dict):
        fail("policy must be an object")
    if not policy.get("bug_fossilization_guard"):
        fail("policy must include bug_fossilization_guard")
    axes = set(str(axis) for axis in policy.get("required_axes", []))
    missing_axes = sorted(REQUIRED_AXES - axes)
    if missing_axes:
        fail("policy missing required axes: " + ", ".join(missing_axes))
    if not isinstance(artifact_policy, dict) or not artifact_policy.get("required_fields"):
        fail("artifact_policy.required_fields must be present")
    if not isinstance(cases, list) or not cases:
        fail("cases must be a non-empty list")
    ids: set[str] = set()
    seen_axes: set[str] = set()
    runnable = 0
    planned = 0
    for case in cases:
        if not isinstance(case, dict):
            fail("cases must contain objects")
        case_id = str(case.get("id") or "")
        if case_id in ids:
            fail(f"duplicate refactor-resilience case id: {case_id}")
        ids.add(case_id)
        validate_case(case)
        seen_axes.add(str(case["axis"]))
        if case["status"] == "runnable":
            runnable += 1
        if case["status"] == "planned-gap":
            planned += 1
    missing_cases = sorted(REQUIRED_AXES - seen_axes)
    if missing_cases:
        fail("missing refactor-resilience axes: " + ", ".join(missing_cases))
    if runnable < 3:
        fail("refactor-resilience needs at least three fast runnable external-contract checks")
    if planned < 1:
        fail("refactor-resilience planned gaps must stay explicit until executable")
    ok(f"refactor-resilience ledger records {len(cases)} cases across {len(seen_axes)} axes")
    return cases


def git_commit() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--short=12", "HEAD"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def build_artifact(cases: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema": 1,
        "kind": "refactor-resilience",
        "git_commit": git_commit(),
        "timestamp_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "command": "python3 scripts/verify-refactor-resilience.py --write-artifact docs/test/refactor-resilience-latest.json",
        "summary": {
            "case_count": len(cases),
            "runnable_count": sum(1 for case in cases if case["status"] == "runnable"),
            "planned_gap_count": sum(1 for case in cases if case["status"] == "planned-gap"),
            "axes": sorted({str(case["axis"]) for case in cases}),
        },
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-artifact", type=Path, help="Write a JSON refactor-resilience artifact.")
    args = parser.parse_args()

    cases = validate_ledger(load_json(LEDGER))
    if args.write_artifact:
        args.write_artifact.parent.mkdir(parents=True, exist_ok=True)
        args.write_artifact.write_text(json.dumps(build_artifact(cases), indent=2, sort_keys=True) + "\n")
        ok(f"wrote refactor-resilience artifact: {args.write_artifact}")
    ok("refactor-resilience external-contract test design is recorded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

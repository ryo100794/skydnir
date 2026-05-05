#!/usr/bin/env python3
"""Verify abnormal-event test cases and structured evidence artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "tests" / "abnormal_event_cases.json"
REQUIRED_CATEGORIES = {
    "input",
    "runtime",
    "build",
    "storage",
    "ui",
    "documents",
    "gpu",
    "network",
    "test-governance",
}
REQUIRED_EVENT_FIELDS = {
    "schema",
    "kind",
    "git_commit",
    "timestamp_utc",
    "case_id",
    "category",
    "severity",
    "surface",
    "status",
    "trigger",
    "expected_signal",
    "failure_oracle",
    "expected_exit_code",
    "evidence_source",
    "evidence",
    "reproduction_command",
    "retention",
}
REQUIRED_EVIDENCE_FIELDS = {
    "artifact_path",
    "artifact_exists",
    "artifact_sha256",
    "artifact_required_in_fast_lane",
}
ALLOWED_STATUSES = {"runnable", "runnable-with-device", "planned-gap"}
ALLOWED_SEVERITIES = {"info", "warning", "error", "blocker"}


def fail(message: str) -> None:
    print(f"verify-abnormal-events: FAIL: {message}", file=sys.stderr)
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


def command_paths(command: str) -> list[Path]:
    try:
        parts = shlex.split(command)
    except ValueError as exc:
        fail(f"reproduction command is not shell-tokenizable: {command!r}: {exc}")
    paths: list[Path] = []
    for part in parts:
        if part.startswith(("scripts/", "tests/", "docs/", "docker-proot-setup/")):
            paths.append(ROOT / part)
    return paths


def relative_repo_path(raw: str, context: str) -> Path:
    path = Path(raw)
    if path.is_absolute() or ".." in path.parts:
        fail(f"{context} must be a repository-relative path without '..': {raw}")
    return ROOT / path


def validate_ledger(data: dict[str, Any]) -> list[dict[str, Any]]:
    event_schema = data.get("event_schema")
    if not isinstance(event_schema, dict):
        fail("event_schema must be an object")
    fields = set(str(field) for field in event_schema.get("required_fields", []))
    missing_fields = sorted(REQUIRED_EVENT_FIELDS - fields)
    if missing_fields:
        fail("event schema missing fields: " + ", ".join(missing_fields))
    evidence_fields = set(str(field) for field in event_schema.get("evidence_required_fields", []))
    missing_evidence_fields = sorted(REQUIRED_EVIDENCE_FIELDS - evidence_fields)
    if missing_evidence_fields:
        fail("event evidence schema missing fields: " + ", ".join(missing_evidence_fields))
    categories = set(str(category) for category in data.get("categories", []))
    missing_categories = sorted(REQUIRED_CATEGORIES - categories)
    if missing_categories:
        fail("abnormal event ledger missing categories: " + ", ".join(missing_categories))
    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        fail("abnormal event cases must be a non-empty list")

    ids: set[str] = set()
    planned = 0
    runnable = 0
    device = 0
    for case in cases:
        if not isinstance(case, dict):
            fail("abnormal event cases must be objects")
        case_id = str(case.get("id") or "")
        if not case_id:
            fail("abnormal event case is missing id")
        if case_id in ids:
            fail(f"duplicate abnormal event case id: {case_id}")
        ids.add(case_id)
        for key in (
            "category",
            "severity",
            "surface",
            "status",
            "trigger",
            "expected_signal",
            "failure_oracle",
            "evidence_source",
            "reproduction_command",
            "retention",
        ):
            if not case.get(key):
                fail(f"{case_id} is missing {key}")
        if case["category"] not in categories:
            fail(f"{case_id} uses unknown category {case['category']!r}")
        if case["severity"] not in ALLOWED_SEVERITIES:
            fail(f"{case_id} uses unknown severity {case['severity']!r}")
        if case["status"] not in ALLOWED_STATUSES:
            fail(f"{case_id} uses unknown status {case['status']!r}")
        if case["status"] == "planned-gap":
            planned += 1
            if not case.get("gap_reason"):
                fail(f"{case_id} planned-gap must include gap_reason")
            if "expected_exit_code" not in case:
                fail(f"{case_id} planned-gap must declare expected_exit_code, even when null")
        elif case["status"] == "runnable":
            runnable += 1
            if type(case.get("expected_exit_code")) is not int:
                fail(f"{case_id} runnable case must include integer expected_exit_code")
            evidence_path = relative_repo_path(str(case["evidence_source"]), f"{case_id} evidence_source")
            if not evidence_path.is_file():
                fail(f"{case_id} runnable evidence_source is missing: {case['evidence_source']}")
        elif case["status"] == "runnable-with-device":
            device += 1
            if type(case.get("expected_exit_code")) is not int:
                fail(f"{case_id} device case must include integer expected_exit_code")
            relative_repo_path(str(case["evidence_source"]), f"{case_id} evidence_source")
        for path in command_paths(str(case["reproduction_command"])):
            if not path.exists():
                fail(f"{case_id} reproduction command references missing path {path.relative_to(ROOT)}")
        if "fail" not in str(case["failure_oracle"]).lower() and "reject" not in str(case["failure_oracle"]).lower():
            fail(f"{case_id} failure_oracle must clearly fail or reject")
    if runnable < 5:
        fail("abnormal event runnable coverage is too thin")
    if planned < 1:
        fail("abnormal event ledger must keep unsupported abnormal paths as planned gaps")
    if device < 1:
        fail("abnormal event ledger must include at least one device-visible abnormal path")
    ok(f"abnormal event ledger records {len(cases)} cases, {runnable} fast runnable, {device} device, {planned} planned")
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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def evidence_status(case: dict[str, Any]) -> dict[str, Any]:
    rel = str(case["evidence_source"])
    path = relative_repo_path(rel, f"{case['id']} evidence_source")
    exists = path.exists()
    return {
        "artifact_path": rel,
        "artifact_exists": exists,
        "artifact_sha256": sha256_file(path) if exists and path.is_file() else None,
        "artifact_required_in_fast_lane": case["status"] == "runnable",
    }


def event_record(case: dict[str, Any]) -> dict[str, Any]:
    evidence = evidence_status(case)
    return {
        "schema": 1,
        "kind": "abnormal-event-case",
        "git_commit": git_commit(),
        "timestamp_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "case_id": case["id"],
        "category": case["category"],
        "severity": case["severity"],
        "surface": case["surface"],
        "status": case["status"],
        "trigger": case["trigger"],
        "expected_signal": case["expected_signal"],
        "failure_oracle": case["failure_oracle"],
        "expected_exit_code": case.get("expected_exit_code"),
        "actual_exit_code": None,
        "observed_signal": None,
        "evidence_source": case["evidence_source"],
        "evidence": evidence,
        "reproduction_command": case["reproduction_command"],
        "retention": case["retention"],
        "gap_reason": case.get("gap_reason"),
    }


def observed_gate_failures() -> list[dict[str, Any]]:
    path = ROOT / "docs" / "test" / "test-design-criteria-latest.json"
    if not path.exists():
        return []
    data = load_json(path)
    failures = data.get("failures")
    if not isinstance(failures, list):
        return []
    return [
        {
            "source": str(path.relative_to(ROOT)),
            "message": str(failure),
            "category": "test-governance",
            "severity": "blocker",
        }
        for failure in failures
    ]


def build_artifact(cases: list[dict[str, Any]]) -> dict[str, Any]:
    records = [event_record(case) for case in cases]
    planned = [record for record in records if record["status"] == "planned-gap"]
    return {
        "schema": 1,
        "kind": "abnormal-events",
        "git_commit": git_commit(),
        "timestamp_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "command": "python3 scripts/verify-abnormal-events.py --write-artifact docs/test/abnormal-events-latest.json",
        "ledger": "tests/abnormal_event_cases.json",
        "summary": {
            "case_count": len(records),
            "runnable_count": sum(1 for record in records if record["status"] == "runnable"),
            "device_count": sum(1 for record in records if record["status"] == "runnable-with-device"),
            "planned_gap_count": len(planned),
            "planned_gap_ids": [record["case_id"] for record in planned],
            "observed_gate_failure_count": len(observed_gate_failures()),
        },
        "events": records,
        "observed_gate_failures": observed_gate_failures(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-artifact", type=Path, help="Write a JSON abnormal-event artifact.")
    args = parser.parse_args()

    data = load_json(LEDGER)
    cases = validate_ledger(data)
    if args.write_artifact:
        args.write_artifact.parent.mkdir(parents=True, exist_ok=True)
        args.write_artifact.write_text(json.dumps(build_artifact(cases), indent=2, sort_keys=True) + "\n")
        ok(f"wrote abnormal event artifact: {args.write_artifact}")
    ok("abnormal event cases have structured reproduction and evidence records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

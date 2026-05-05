#!/usr/bin/env python3
"""Verify randomized, monkey, stress, and repeatability test process contracts."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import random
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "tests" / "stress_regression_cases.json"
REQUIRED_CATEGORIES = {"random", "monkey", "stress", "variance"}
REQUIRED_ARTIFACT_FIELDS = {
    "schema",
    "kind",
    "git_commit",
    "build_flavor",
    "timestamp_utc",
    "seed",
    "command",
    "case_fingerprint",
    "summary",
}


def fail(message: str) -> None:
    print(f"verify-stress-regression: FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def ok(message: str) -> None:
    print(f"ok: {message}")


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        fail(f"could not import {path.relative_to(ROOT)}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


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


def validate_ledger(data: dict[str, Any]) -> int:
    seed_policy = data.get("seed_policy")
    artifact_policy = data.get("artifact_policy")
    variance_policy = data.get("variance_policy")
    scenarios = data.get("scenarios")
    if not isinstance(seed_policy, dict) or not isinstance(seed_policy.get("default_seed"), int):
        fail("seed_policy.default_seed must be an integer")
    if not isinstance(artifact_policy, dict):
        fail("artifact_policy must be an object")
    fields = set(str(field) for field in artifact_policy.get("required_fields", []))
    missing_fields = sorted(REQUIRED_ARTIFACT_FIELDS - fields)
    if missing_fields:
        fail("artifact policy missing fields: " + ", ".join(missing_fields))
    if not isinstance(variance_policy, dict) or int(variance_policy.get("default_repeats", 0)) < 2:
        fail("variance_policy.default_repeats must be at least 2")
    if not isinstance(scenarios, list) or not scenarios:
        fail("scenarios must be a non-empty list")

    categories: set[str] = set()
    ids: set[str] = set()
    lanes: set[str] = set()
    artifact_count = 0
    for scenario in scenarios:
        if not isinstance(scenario, dict):
            fail("scenario entries must be objects")
        sid = str(scenario.get("id") or "")
        category = str(scenario.get("category") or "")
        lane = str(scenario.get("lane") or "")
        command = str(scenario.get("command") or "")
        if not sid or not category or not lane or not command or not scenario.get("checks"):
            fail(f"scenario is incomplete: {scenario!r}")
        if sid in ids:
            fail(f"duplicate scenario id: {sid}")
        ids.add(sid)
        categories.add(category)
        lanes.add(lane)
        if scenario.get("artifact"):
            artifact_count += 1
        if category in {"random", "monkey"} and scenario.get("seed") is None:
            fail(f"{sid} must record an explicit seed")

    missing_categories = sorted(REQUIRED_CATEGORIES - categories)
    if missing_categories:
        fail("missing stress categories: " + ", ".join(missing_categories))
    for lane in ("fast-local", "android-full", "android-gpu", "android-llama", "heavy-container"):
        if lane not in lanes:
            fail(f"stress ledger missing lane {lane!r}")
    if artifact_count != len(scenarios):
        fail("every stress scenario must declare an artifact path or explicit optional artifact policy")
    ok(f"stress regression ledger covers {len(scenarios)} scenarios in {len(categories)} categories")
    return int(seed_policy["default_seed"])


def generated_api_cases(seed: int, count: int) -> list[dict[str, str]]:
    rng = random.Random(seed)
    templates = [
        ("GET", "/_ping", ""),
        ("GET", "/version", ""),
        ("GET", "/containers/json?all=1", ""),
        ("GET", "/system/diagnostics?limit={limit}", ""),
        ("GET", "/images/get", ""),
        ("POST", "/networks/create", "{{"),
        ("POST", "/networks/create", "[]"),
        ("POST", "/networks/create", '{{"Name":"fuzz-net-{n}","Labels":{{"seed":"{seed}"}}}}'),
    ]
    cases: list[dict[str, str]] = []
    for index in range(count):
        method, path, body = rng.choice(templates)
        limit = rng.choice(["0", "1", "5", "100", "101", "-1", "not-int"])
        name_suffix = f"{index}-{rng.randrange(1_000_000):06d}"
        cases.append(
            {
                "method": method,
                "path": path.format(limit=limit),
                "body": body.format(n=name_suffix, seed=seed),
            }
        )
    return cases


def case_fingerprint(cases: list[dict[str, str]]) -> str:
    payload = json.dumps(cases, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def response_class(status: int, body: Any) -> dict[str, Any]:
    if isinstance(body, dict):
        message = str(body.get("message") or body.get("error") or "")
    elif isinstance(body, str):
        message = body
    else:
        message = ""
    return {
        "status": status,
        "message_class": message.split(":", 1)[0][:80],
    }


def run_api_sequence(seed: int, count: int) -> tuple[str, list[dict[str, Any]]]:
    input_validation = load_module(ROOT / "scripts" / "verify-input-validation.py", "verify_input_validation_stress")
    cases = generated_api_cases(seed, count)
    fingerprint = case_fingerprint(cases)
    summary: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as raw_tmp:
        tmpdir = Path(raw_tmp)
        proc, socket_path = input_validation.start_daemon(tmpdir)
        try:
            for case in cases:
                status, body = input_validation.request_unix(
                    socket_path,
                    case["method"],
                    case["path"],
                    case["body"].encode("utf-8"),
                )
                if status >= 500:
                    fail(f"seeded API fuzz produced server error for {case}: {body!r}")
                summary.append(response_class(status, body))
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
    return fingerprint, summary


def git_commit() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--short=12", "HEAD"],
        cwd=str(ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def build_artifact(seed: int, command: str, fingerprint: str, summary: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema": 1,
        "kind": "stress-regression-fast",
        "git_commit": git_commit(),
        "build_flavor": os.environ.get("PDOCKER_ANDROID_FLAVOR", "compat"),
        "timestamp_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "seed": seed,
        "command": command,
        "case_fingerprint": fingerprint,
        "summary": {
            "case_count": len(summary),
            "status_vector": [entry["status"] for entry in summary],
            "message_classes": [entry["message_class"] for entry in summary],
        },
    }


def validate_artifact_shape(artifact: dict[str, Any], required_fields: set[str]) -> None:
    missing = sorted(required_fields - set(artifact))
    if missing:
        fail("stress artifact missing fields: " + ", ".join(missing))
    summary = artifact.get("summary")
    if not isinstance(summary, dict) or not summary.get("status_vector"):
        fail("stress artifact summary must include a status_vector")
    if artifact.get("case_fingerprint") != case_fingerprint(generated_api_cases(int(artifact["seed"]), len(summary["status_vector"]))):
        fail("stress artifact case_fingerprint is not reproducible from seed")
    ok("stress artifact shape is reproducible and tied to the build set")


def run_seeded_repeatability(seed: int, count: int, repeats: int) -> dict[str, Any]:
    cases_a = generated_api_cases(seed, count)
    cases_b = generated_api_cases(seed, count)
    if cases_a != cases_b:
        fail("seeded random generator is not deterministic")
    expected_fingerprint = case_fingerprint(cases_a)
    summaries: list[list[dict[str, Any]]] = []
    for _ in range(repeats):
        fingerprint, summary = run_api_sequence(seed, count)
        if fingerprint != expected_fingerprint:
            fail("API fuzz case fingerprint drifted between repeats")
        summaries.append(summary)
    baseline = summaries[0]
    for index, summary in enumerate(summaries[1:], start=2):
        if summary != baseline:
            fail(f"repeat {index} drifted from repeat 1: {summary!r} != {baseline!r}")
    ok(f"seeded random API fuzz is repeatable across {repeats} runs")
    return build_artifact(
        seed,
        f"python3 scripts/verify-stress-regression.py --seed {seed} --count {count} --repeats {repeats}",
        expected_fingerprint,
        baseline,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, help="Seed for the fast randomized API fuzz.")
    parser.add_argument("--count", type=int, default=18, help="Number of generated API cases per repeat.")
    parser.add_argument("--repeats", type=int, default=2, help="Number of fast randomized repeats.")
    parser.add_argument("--write-artifact", type=Path, help="Write a build-set stress artifact JSON.")
    args = parser.parse_args(argv)

    data = load_json(LEDGER)
    default_seed = validate_ledger(data)
    seed = args.seed if args.seed is not None else default_seed
    if args.count < 8:
        fail("--count must be at least 8")
    if args.repeats < 2:
        fail("--repeats must be at least 2")

    artifact = run_seeded_repeatability(seed, args.count, args.repeats)
    required_fields = set(str(field) for field in data["artifact_policy"]["required_fields"])
    validate_artifact_shape(artifact, required_fields)
    if args.write_artifact:
        args.write_artifact.parent.mkdir(parents=True, exist_ok=True)
        args.write_artifact.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
        ok(f"wrote stress regression artifact: {args.write_artifact}")
    ok("random, monkey, stress, artifact, and variance process contracts are covered")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

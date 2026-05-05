#!/usr/bin/env python3
"""Validate requirement-level blackbox scenarios and negative oracles.

This checker intentionally treats requirements as externally observable
contracts. Implementation names are allowed in evidence commands, but not in
the user-facing requirement text or failure expectations.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "tests" / "blackbox_requirements.json"
FEATURE_LEDGER = ROOT / "tests" / "feature_scenarios.json"

ALLOWED_STATUSES = {"runnable", "runnable-with-device", "runnable-with-env", "planned-gap"}
NEGATIVE_WORDS = ("fail", "fails", "reject", "error", "does not", "do not", "never", "absent", "forbidden")
IMPLEMENTATION_TOKENS = (
    "pdocker_direct_exec",
    "TraceeState",
    "rewrite_",
    "g_rootfs_fd",
    "ptrace",
    "seccomp",
    "libcow",
    "libpdockerdirect",
    "function",
)


def fail(message: str) -> None:
    print(f"verify-blackbox-requirements: FAIL: {message}", file=sys.stderr)
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


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        fail(f"could not import {path.relative_to(ROOT)}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def text_has_negative_oracle(text: str) -> bool:
    folded = text.lower()
    return any(word in folded for word in NEGATIVE_WORDS)


def requirement_text(entry: dict[str, Any]) -> str:
    positive = entry.get("positive") if isinstance(entry.get("positive"), dict) else {}
    negative = entry.get("negative") if isinstance(entry.get("negative"), dict) else {}
    fields = [
        entry.get("user_story", ""),
        positive.get("given", ""),
        positive.get("when", ""),
        positive.get("then", ""),
        negative.get("given", ""),
        negative.get("when", ""),
        negative.get("then", ""),
        negative.get("failure_oracle", ""),
    ]
    return "\n".join(str(field) for field in fields)


def validate_ledger(data: dict[str, Any], feature_data: dict[str, Any] | None = None) -> list[str]:
    errors: list[str] = []
    surfaces = data.get("surfaces")
    requirements = data.get("requirements")
    if not isinstance(surfaces, list) or not surfaces:
        errors.append("surfaces must be a non-empty list")
        return errors
    surface_set = {str(surface) for surface in surfaces}
    if not isinstance(requirements, list) or not requirements:
        errors.append("requirements must be a non-empty list")
        return errors

    feature_areas = set()
    if feature_data:
        feature_areas = set(str(area) for area in feature_data.get("required_areas", []))

    seen: set[str] = set()
    negative_count = 0
    device_count = 0
    for entry in requirements:
        if not isinstance(entry, dict):
            errors.append("requirement entries must be objects")
            continue
        rid = str(entry.get("id") or "")
        area = str(entry.get("area") or "")
        status = str(entry.get("status") or "")
        user_story = str(entry.get("user_story") or "")
        observable = entry.get("observable_surface")
        positive = entry.get("positive")
        negative = entry.get("negative")
        if not rid or not area or not status or not user_story:
            errors.append(f"requirement is incomplete: {entry!r}")
            continue
        if rid in seen:
            errors.append(f"duplicate requirement id: {rid}")
        seen.add(rid)
        if status not in ALLOWED_STATUSES:
            errors.append(f"{rid} uses unknown status {status!r}")
        if feature_areas and area not in feature_areas:
            errors.append(f"{rid} area {area!r} is not in feature scenario required_areas")
        if not isinstance(observable, list) or not observable:
            errors.append(f"{rid} must declare observable_surface")
        else:
            unknown = sorted(str(surface) for surface in observable if str(surface) not in surface_set)
            if unknown:
                errors.append(f"{rid} uses unknown observable surface(s): {', '.join(unknown)}")
        if not isinstance(positive, dict) or not all(positive.get(key) for key in ("given", "when", "then", "evidence")):
            errors.append(f"{rid} must include positive given/when/then/evidence")
        if not isinstance(negative, dict) or not all(negative.get(key) for key in ("given", "when", "then", "failure_oracle")):
            errors.append(f"{rid} must include negative given/when/then/failure_oracle")
        else:
            negative_count += 1
            negative_text = f"{negative.get('then', '')}\n{negative.get('failure_oracle', '')}"
            if not text_has_negative_oracle(negative_text):
                errors.append(f"{rid} negative oracle does not clearly fail/reject/error")
        if "device" in status:
            device_count += 1
        public_text = requirement_text(entry)
        leaked = [token for token in IMPLEMENTATION_TOKENS if token.lower() in public_text.lower()]
        if leaked:
            errors.append(f"{rid} requirement text leaks implementation token(s): {', '.join(leaked)}")

    if len(requirements) < 6:
        errors.append("blackbox requirements coverage is too thin")
    if negative_count != len(requirements):
        errors.append("every blackbox requirement must include a negative scenario")
    if device_count < 2:
        errors.append("blackbox requirements must include device-visible behavior")
    return errors


def expect_errors(label: str, errors: list[str]) -> None:
    if not errors:
        fail(f"negative self-test did not fail: {label}")
    ok(f"negative self-test fails as expected: {label}")


def run_negative_self_tests(data: dict[str, Any]) -> None:
    feature_data = load_json(FEATURE_LEDGER)

    missing_negative = deepcopy(data)
    missing_negative["requirements"][0].pop("negative", None)
    expect_errors("missing negative scenario", validate_ledger(missing_negative, feature_data))

    implementation_leak = deepcopy(data)
    implementation_leak["requirements"][0]["user_story"] += " This depends on rewrite_execve_arg."
    expect_errors("implementation leak in requirement text", validate_ledger(implementation_leak, feature_data))

    weak_oracle = deepcopy(data)
    weak_oracle["requirements"][0]["negative"]["then"] = "The request is observed."
    weak_oracle["requirements"][0]["negative"]["failure_oracle"] = "The response is recorded."
    expect_errors("weak negative oracle", validate_ledger(weak_oracle, feature_data))

    storage = load_module(ROOT / "scripts" / "verify-storage-metrics.py", "verify_storage_metrics_blackbox")
    bad_storage = deepcopy(storage.FIXTURE)
    bad_storage["system_df"]["SharedLayerBytes"] = -1
    bad_storage["system_df"]["TotalBytes"] = (
        bad_storage["system_df"]["UniqueBytes"] + bad_storage["system_df"]["ImageViewBytes"]
    )
    expect_errors("corrupt storage metrics fixture", storage.validate(bad_storage))

    dockerfile = load_module(ROOT / "scripts" / "verify-dockerfile-standard.py", "verify_dockerfile_standard_blackbox")
    with tempfile.TemporaryDirectory() as tmpdir:
        sample = Path(tmpdir) / "Dockerfile"
        sample.write_text("FROM ubuntu:22.04\nPDOCKER_RUN echo nope\n")
        rejected = []
        for _lineno, line in dockerfile.logical_lines(sample):
            instr = line.split(None, 1)[0].upper()
            if instr not in dockerfile.ALLOWED or instr.startswith("PDOCKER"):
                rejected.append(instr)
        if not rejected:
            fail("negative self-test did not fail: non-standard Dockerfile instruction")
        ok("negative self-test fails as expected: non-standard Dockerfile instruction")


def main() -> int:
    data = load_json(LEDGER)
    feature_data = load_json(FEATURE_LEDGER)
    errors = validate_ledger(data, feature_data)
    if errors:
        fail("; ".join(errors))
    run_negative_self_tests(data)
    ok(f"blackbox requirements cover {len(data['requirements'])} externally observable requirements")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

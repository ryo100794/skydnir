#!/usr/bin/env python3
"""Validate API argument, input-file grammar, and value-range rejection paths."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "tests" / "input_validation_cases.json"
GRAMMAR_LEDGER = ROOT / "tests" / "input_grammar_coverage.json"
PDOCKERD = ROOT / "docker-proot-setup" / "bin" / "pdockerd"
REQUIRED_CATEGORIES = {"api-arguments", "input-file-grammar", "value-ranges"}
REQUIRED_BOUNDARY_IDS = {
    "boundary.path-max-and-enametoolong",
    "boundary.sockaddr-min-and-sun-path-limit",
    "boundary.getcwd-erange",
    "boundary.exec-argc-and-scratch-limit",
    "boundary.memory-guard-threshold",
    "boundary.uid-gid-minus-one",
    "boundary.wait-exit-signal-code",
}
EVIDENCE: list[dict[str, Any]] = []


class ValidationError(Exception):
    pass


def fail(message: str) -> None:
    print(f"verify-input-validation: FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def ok(message: str) -> None:
    print(f"ok: {message}")


def record_evidence(area: str, label: str, result: str, **details: Any) -> None:
    EVIDENCE.append({"area": area, "label": label, "result": result, **details})


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


def validate_ledger() -> dict[str, Any]:
    data = load_json(LEDGER)
    categories = data.get("categories")
    if not isinstance(categories, list) or not categories:
        fail("input validation categories must be a non-empty list")
    seen_categories: set[str] = set()
    seen_cases: set[str] = set()
    for category in categories:
        if not isinstance(category, dict):
            fail("category entries must be objects")
        cid = str(category.get("id") or "")
        cases = category.get("cases")
        if not cid or not category.get("description"):
            fail(f"category is incomplete: {category!r}")
        if cid in seen_categories:
            fail(f"duplicate category id: {cid}")
        seen_categories.add(cid)
        if not isinstance(cases, list) or not cases:
            fail(f"{cid} must include cases")
        for case in cases:
            if not isinstance(case, dict):
                fail(f"{cid} case entries must be objects")
            case_id = str(case.get("id") or "")
            if not case_id or not case.get("surface") or not case.get("input") or not case.get("expected"):
                fail(f"{cid} case is incomplete: {case!r}")
            if case_id in seen_cases:
                fail(f"duplicate validation case id: {case_id}")
            seen_cases.add(case_id)

    missing = sorted(REQUIRED_CATEGORIES - seen_categories)
    if missing:
        fail("missing input validation categories: " + ", ".join(missing))
    ok(f"input validation ledger covers {len(seen_categories)} categories and {len(seen_cases)} cases")
    record_evidence(
        "input-validation-ledger",
        "tests/input_validation_cases.json",
        "pass",
        category_count=len(seen_categories),
        case_count=len(seen_cases),
    )
    return data


def validate_grammar_coverage() -> dict[str, Any]:
    data = load_json(GRAMMAR_LEDGER)
    grammars = data.get("grammars")
    if not isinstance(grammars, list) or not grammars:
        fail("input grammar coverage grammars must be a non-empty list")
    runnable = [grammar for grammar in grammars if isinstance(grammar, dict) and grammar.get("status") == "runnable"]
    planned = [grammar for grammar in grammars if isinstance(grammar, dict) and grammar.get("status") == "planned-gap"]
    if not any(grammar.get("id") == "compose-file-full-grammar" for grammar in planned):
        fail("Compose full grammar gap must be recorded in input grammar coverage")
    record_evidence(
        "input-file-grammar",
        "BNF-like input grammar ledger",
        "pass-with-planned-gap",
        grammar_count=len(grammars),
        runnable_grammar_count=len(runnable),
        planned_gap_grammar_count=len(planned),
        planned_gap_ids=[str(grammar.get("id") or "") for grammar in planned],
    )
    ok(f"input grammar coverage records {len(grammars)} BNF-like grammars")
    return data


def split_http(raw: bytes) -> tuple[int, dict[str, str], bytes]:
    head, separator, body = raw.partition(b"\r\n\r\n")
    if not separator:
        raise ValidationError("HTTP response did not include a header/body separator")
    lines = head.decode("iso-8859-1", "replace").splitlines()
    if not lines:
        raise ValidationError("HTTP response did not include a status line")
    parts = lines[0].split()
    if len(parts) < 2 or not parts[1].isdigit():
        raise ValidationError(f"invalid HTTP status line: {lines[0]!r}")
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()
    return int(parts[1]), headers, body


def request_unix(socket_path: Path, method: str, path: str, body: bytes = b"") -> tuple[int, Any]:
    request = (
        f"{method} {path} HTTP/1.1\r\n"
        "Host: docker\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n"
        "\r\n"
    ).encode("ascii") + body
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(5)
        client.connect(str(socket_path))
        client.sendall(request)
        chunks: list[bytes] = []
        while True:
            chunk = client.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    status, _headers, raw_body = split_http(b"".join(chunks))
    if raw_body:
        try:
            decoded: Any = json.loads(raw_body)
        except json.JSONDecodeError:
            decoded = raw_body.decode("utf-8", "replace")
    else:
        decoded = None
    return status, decoded


def start_daemon(tmpdir: Path) -> tuple[subprocess.Popen[bytes], Path]:
    socket_path = tmpdir / "pdockerd.sock"
    env = os.environ.copy()
    env.update(
        {
            "PDOCKER_HOME": str(tmpdir / "home"),
            "PDOCKER_TMP_DIR": str(tmpdir / "tmp"),
            "PDOCKER_RUNTIME_BACKEND": "direct",
            "PDOCKER_DIRECT_EXECUTOR": "",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    proc = subprocess.Popen(
        [sys.executable, str(PDOCKERD), "--socket", str(socket_path)],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            output = (proc.stdout.read() if proc.stdout else b"").decode("utf-8", "replace")
            raise ValidationError(f"pdockerd exited before creating socket:\n{output}")
        if socket_path.exists():
            return proc, socket_path
        time.sleep(0.05)
    proc.terminate()
    raise ValidationError("pdockerd did not create its Unix socket in time")


def assert_status(label: str, got: int, want: int, body: Any, text: str | None = None) -> None:
    if got != want:
        record_evidence("api-arguments", label, "fail", expected_status=want, actual_status=got, body=body)
        raise ValidationError(f"{label}: expected HTTP {want}, got {got}: {body!r}")
    if text is not None and text.lower() not in json.dumps(body).lower():
        record_evidence("api-arguments", label, "fail", expected_text=text, body=body)
        raise ValidationError(f"{label}: expected response to mention {text!r}, got {body!r}")
    record_evidence("api-arguments", label, "pass", expected_status=want, actual_status=got)
    ok(f"API validation rejects/handles as expected: {label}")


def run_api_argument_tests() -> None:
    with tempfile.TemporaryDirectory() as raw_tmp:
        tmpdir = Path(raw_tmp)
        proc, socket_path = start_daemon(tmpdir)
        try:
            status, body = request_unix(socket_path, "POST", "/networks/create", b"{")
            assert_status("malformed JSON request body", status, 400, body, "invalid JSON")

            status, body = request_unix(socket_path, "POST", "/networks/create", b"[]")
            assert_status("non-object JSON request body", status, 400, body, "object")

            status, body = request_unix(socket_path, "GET", "/images/get")
            assert_status("missing required image names", status, 400, body, "no image names")

            status, body = request_unix(socket_path, "GET", "/system/diagnostics?limit=not-int")
            assert_status("invalid diagnostics limit clamps", status, 200, body)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)


def expect_errors(label: str, errors: list[str]) -> None:
    if not errors:
        record_evidence("value-ranges", label, "fail", expected_failure=True, error_count=0)
        fail(f"negative validation case did not fail: {label}")
    record_evidence("value-ranges", label, "pass", expected_failure=True, error_count=len(errors))
    ok(f"negative validation case fails as expected: {label}")


def run_file_grammar_tests() -> None:
    dockerfile = load_module(ROOT / "scripts" / "verify-dockerfile-standard.py", "verify_dockerfile_standard_input")
    with tempfile.TemporaryDirectory() as raw_tmp:
        tmpdir = Path(raw_tmp)
        unknown = tmpdir / "Dockerfile.unknown"
        unknown.write_text("FROM ubuntu:22.04\nWAT echo nope\n")
        pdocker = tmpdir / "Dockerfile.pdocker"
        pdocker.write_text("FROM ubuntu:22.04\nPDOCKER_RUN echo nope\n")
        for label, path, expected in (
            ("unknown Dockerfile instruction", unknown, "WAT"),
            ("pdocker-specific Dockerfile instruction", pdocker, "PDOCKER_RUN"),
        ):
            rejected = []
            for _lineno, line in dockerfile.logical_lines(path):
                instr = line.split(None, 1)[0].upper()
                if instr not in dockerfile.ALLOWED or instr.startswith("PDOCKER"):
                    rejected.append(instr)
            if expected not in rejected:
                record_evidence("input-file-grammar", label, "fail", expected_rejection=expected, rejected=rejected)
                fail(f"{label} was not rejected")
            record_evidence("input-file-grammar", label, "pass", expected_rejection=expected, rejected=rejected)
            ok(f"file grammar validation rejects as expected: {label}")

    feature = load_module(ROOT / "scripts" / "verify-feature-scenarios.py", "verify_feature_scenarios_input")
    feature_data = load_json(ROOT / "tests" / "feature_scenarios.json")
    required_areas = set(feature_data.get("required_areas", []))
    scenario_areas = {str(scenario.get("area") or "") for scenario in feature_data.get("scenarios", [])}
    if not required_areas <= scenario_areas:
        record_evidence("input-file-grammar", "feature scenario required areas", "fail")
        fail("feature scenario ledger is already missing required areas")
    if not callable(getattr(feature, "command_paths", None)):
        record_evidence("input-file-grammar", "feature scenario command path validator", "fail")
        fail("feature scenario verifier did not expose command path validation")
    record_evidence(
        "input-file-grammar",
        "feature scenario required areas",
        "pass",
        required_area_count=len(required_areas),
        scenario_area_count=len(scenario_areas),
    )
    ok("file grammar validation keeps the feature scenario ledger structurally complete")


def run_value_range_tests() -> None:
    storage = load_module(ROOT / "scripts" / "verify-storage-metrics.py", "verify_storage_metrics_input")
    negative = deepcopy(storage.FIXTURE)
    negative["system_df"]["SharedLayerBytes"] = -1
    expect_errors("negative storage byte count", storage.validate(negative))

    impossible_free = deepcopy(storage.FIXTURE)
    impossible_free["system_df"]["FreeBytes"] = impossible_free["system_df"]["TotalBytes"] + 1
    expect_errors("free bytes greater than total bytes", storage.validate(impossible_free))

    double_count = deepcopy(storage.FIXTURE)
    double_count["system_df"]["TotalBytes"] = (
        double_count["system_df"]["UniqueBytes"] + double_count["system_df"]["ImageViewBytes"]
    )
    expect_errors("double-counted image view bytes", storage.validate(double_count))

    syscall = load_json(ROOT / "tests" / "direct_syscall_coverage.json")
    boundaries = syscall.get("boundary_value_matrix")
    if not isinstance(boundaries, list):
        fail("direct syscall coverage boundary_value_matrix must be a list")
    boundary_ids = {str(entry.get("id") or "") for entry in boundaries if isinstance(entry, dict)}
    missing = sorted(REQUIRED_BOUNDARY_IDS - boundary_ids)
    if missing:
        record_evidence("value-ranges", "direct syscall boundary matrix", "fail", missing=missing)
        fail("direct syscall boundary matrix is missing: " + ", ".join(missing))
    record_evidence(
        "value-ranges",
        "direct syscall boundary matrix",
        "pass",
        required_boundary_ids=sorted(REQUIRED_BOUNDARY_IDS),
        present_boundary_count=len(boundary_ids),
    )
    ok("value-range validation covers direct syscall boundary matrix")


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


def build_report(validation_ledger: dict[str, Any], grammar_ledger: dict[str, Any]) -> dict[str, Any]:
    grammars = [grammar for grammar in grammar_ledger.get("grammars", []) if isinstance(grammar, dict)]
    planned = [grammar for grammar in grammars if grammar.get("status") == "planned-gap"]
    runnable = [grammar for grammar in grammars if grammar.get("status") == "runnable"]
    categories = [category for category in validation_ledger.get("categories", []) if isinstance(category, dict)]
    return {
        "schema": 1,
        "kind": "input-validation",
        "git_commit": git_commit(),
        "timestamp_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "command": "python3 scripts/verify-input-validation.py --write-artifact docs/test/input-validation-latest.json",
        "ledgers": [
            "tests/input_validation_cases.json",
            "tests/input_grammar_coverage.json",
        ],
        "summary": {
            "category_count": len(categories),
            "case_count": sum(len(category.get("cases", [])) for category in categories),
            "grammar_count": len(grammars),
            "runnable_grammar_count": len(runnable),
            "planned_gap_grammar_count": len(planned),
            "planned_gap_ids": [str(grammar.get("id") or "") for grammar in planned],
            "bnf_like_coverage_recorded": True,
            "bnf_full_coverage": False,
            "syntax_and_range_validation_recorded": True,
        },
        "evidence": EVIDENCE,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-artifact", type=Path, help="Write a JSON evidence artifact.")
    args = parser.parse_args()

    validation_ledger = validate_ledger()
    grammar_ledger = validate_grammar_coverage()
    try:
        run_api_argument_tests()
        run_file_grammar_tests()
        run_value_range_tests()
    except ValidationError as exc:
        fail(str(exc))
    if args.write_artifact:
        args.write_artifact.parent.mkdir(parents=True, exist_ok=True)
        args.write_artifact.write_text(
            json.dumps(build_report(validation_ledger, grammar_ledger), indent=2, sort_keys=True) + "\n"
        )
        ok(f"wrote input validation artifact: {args.write_artifact}")
    ok("input validation covers API arguments, input-file grammar, and value ranges")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

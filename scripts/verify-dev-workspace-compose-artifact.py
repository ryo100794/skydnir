#!/usr/bin/env python3
"""Host-side verifier for dev-workspace compose smoke artifacts.

A top-level ``success: true`` is not enough.  Promotion requires the concrete
checks emitted by scripts/android-dev-workspace-compose-smoke.sh to prove the
build/run flow, current Engine state, listener, code-server HTTP, configured
extensions, and UI service-truth match.  Planned/failed artifacts are valid only
as non-promoting evidence and are rejected by this verifier.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REQUIRED_SCHEMA = "pdocker.android.dev-workspace.compose-smoke.v1"
REQUIRED_CHECKS = [
    "build_run",
    "engine_state",
    "listener",
    "code_server_http",
    "extensions",
    "ui_truth",
]
NON_PROMOTING_STATUSES = {
    "fail",
    "failed",
    "planned",
    "planned-gap",
    "planned-skip",
    "skip",
    "skipped",
    "blocked",
    "blocked-device",
}


class VerificationError(ValueError):
    pass


def _require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise VerificationError(f"missing dev workspace artifact: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise VerificationError(f"invalid dev workspace artifact JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise VerificationError("dev workspace artifact must be a JSON object")
    return data


def _check(data: dict[str, Any], name: str) -> dict[str, Any]:
    checks = data.get("checks")
    if not isinstance(checks, dict):
        return {}
    value = checks.get(name)
    return value if isinstance(value, dict) else {}


def validate_artifact_data(data: dict[str, Any]) -> list[str]:
    """Return all promotion-blocking validation errors for an artifact."""
    errors: list[str] = []
    status = str(data.get("status") or data.get("Status") or "").strip().lower()
    success = data.get("success", data.get("Success"))

    _require(data.get("schema") == REQUIRED_SCHEMA, f"schema must be {REQUIRED_SCHEMA}", errors)
    _require(status == "pass", f"status must be pass for promotion, got {status or '<missing>'}", errors)
    _require(success is True, "success must be true for promotion", errors)

    if status in NON_PROMOTING_STATUSES:
        _require(success is False, f"non-promoting status {status} must set success=false", errors)
        errors.append(f"non-promoting status {status} is not promotion eligible")
    elif status and status != "pass":
        errors.append(f"unknown non-pass status {status} is not promotion eligible")

    failures = data.get("failures", [])
    _require(isinstance(failures, list), "failures must be a list", errors)
    if isinstance(failures, list):
        _require(not failures, "pass artifact must not contain failures", errors)

    flow_exit_code = data.get("flow_exit_code")
    _require(flow_exit_code == 0, f"flow_exit_code must be 0 for promotion, got {flow_exit_code!r}", errors)

    checks = data.get("checks")
    _require(isinstance(checks, dict), "checks must be an object", errors)
    for name in REQUIRED_CHECKS:
        check = _check(data, name)
        _require(bool(check), f"missing check: {name}", errors)
        if name == "extensions" and check.get("configured") is False:
            required = data.get("required_extensions")
            _require(not required, "extensions check is unconfigured but required_extensions is non-empty", errors)
            _require(check.get("ok") is True, "unconfigured extensions check must still report ok=true", errors)
        else:
            _require(check.get("ok") is True, f"check {name} must be ok=true", errors)

    build_run = _check(data, "build_run")
    _require(build_run.get("build_started_observed") is True, "build_run must observe build start", errors)
    _require(build_run.get("build_completed_observed") is True, "build_run must observe build completion/cache", errors)
    _require(build_run.get("container_create_observed") is True, "build_run must observe container create", errors)
    _require(build_run.get("container_start_observed") is True, "build_run must observe container start", errors)
    _require(build_run.get("container_started_observed") is True, "build_run must observe container started", errors)
    _require(build_run.get("build_failed_marker_observed") is False, "build_run must not contain failed markers", errors)
    _require(bool(build_run.get("running_container_id")), "build_run must include running_container_id", errors)

    engine_state = _check(data, "engine_state")
    _require(engine_state.get("inspect_running") is True, "engine_state must prove inspect_running=true", errors)
    _require(bool(engine_state.get("running_container_id")), "engine_state must include running_container_id", errors)
    _require(bool(engine_state.get("inspect_id")), "engine_state must include inspect_id", errors)

    listener = _check(data, "listener")
    _require(str(listener.get("http_probe_exit_code")) == "0", "listener probe exit code must be 0", errors)
    _require(bool(listener.get("http_status_line")), "listener must include an HTTP status line", errors)

    http = _check(data, "code_server_http")
    code = http.get("http_status_code")
    _require(isinstance(code, int) and 200 <= code <= 399, f"code_server_http status must be 2xx/3xx, got {code!r}", errors)

    extensions = _check(data, "extensions")
    if extensions.get("configured") is not False:
        present = extensions.get("present")
        required = extensions.get("required") or data.get("required_extensions") or []
        _require(isinstance(required, list), "extensions.required must be a list", errors)
        _require(isinstance(present, dict), "extensions.present must be an object", errors)
        if isinstance(required, list) and isinstance(present, dict):
            missing = [ext for ext in required if present.get(ext) is not True]
            _require(not missing, f"required extensions missing: {', '.join(missing)}", errors)
        _require(extensions.get("exec_exit_code") == 0, "extensions exec_exit_code must be 0 when configured", errors)

    ui_truth = _check(data, "ui_truth")
    _require(ui_truth.get("current_match") is True, "ui_truth must have current_match=true", errors)
    _require((ui_truth.get("target_card_count") or 0) > 0, "ui_truth must include at least one target card", errors)
    _require((ui_truth.get("stale_or_unknown_target_card_count") or 0) == 0, "ui_truth must not include stale/unknown/ambiguous target cards", errors)

    return errors


def verify(path: Path) -> dict[str, Any]:
    data = _read_json(path)
    errors = validate_artifact_data(data)
    if errors:
        raise VerificationError("; ".join(errors))
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path, help="dev-workspace-compose-latest.json artifact to verify")
    args = parser.parse_args(argv)
    try:
        verify(args.artifact)
    except VerificationError as exc:
        print(f"not promotion eligible: {exc}", file=sys.stderr)
        return 2
    print(f"ok: {args.artifact}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

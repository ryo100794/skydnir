#!/usr/bin/env python3
"""Verify the focused Android single-container docker-run artifact.

This verifier is intentionally host-side and promotion-strict: a top-level
``success: true`` is not enough.  A passing artifact must prove the exact
``docker run --rm ubuntu:22.04 echo hi`` command, stdout truth, exit-code truth,
a real Engine container id from ``--cidfile``, no host-shell fallback, and device
evidence links for the raw stdout/stderr/log/cidfile diagnostics.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

REQUIRED_SCHEMA = "pdocker.android.runtime-single-container-echo-hi.v1"
REQUIRED_COMMAND = "docker run --rm ubuntu:22.04 echo hi"
REQUIRED_EFFECTIVE_COMMAND = "docker run --cidfile <diagnostic-cidfile> --rm ubuntu:22.04 echo hi"
EXPECTED_STDOUT = "hi"
REQUIRED_EVIDENCE_LINKS = ("stdout", "stderr", "combined_log", "cidfile")
CONTAINER_ID_RE = re.compile(r"^[0-9a-f]{64}$")
NON_PROMOTING_STATUSES = {
    "blocked",
    "blocked-device",
    "fail",
    "failed",
    "planned",
    "planned-gap",
    "planned-skip",
    "skip",
    "skipped",
}


class VerificationError(ValueError):
    pass


def _require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise VerificationError(f"missing runtime single-container artifact: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise VerificationError(f"invalid runtime single-container artifact JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise VerificationError("runtime single-container artifact must be a JSON object")
    return data


def _status(data: dict[str, Any]) -> str:
    value = data.get("status", data.get("Status", ""))
    return str(value).strip().lower() if value is not None else ""


def _success(data: dict[str, Any]) -> Any:
    return data.get("success", data.get("Success"))


def _is_int_zero(value: Any) -> bool:
    return type(value) is int and value == 0


def _valid_container_id(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    return bool(CONTAINER_ID_RE.fullmatch(value)) and value != "0" * 64


def _evidence_link_ok(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    return bool(value) and value == value.strip() and value.startswith("files/") and "\x00" not in value


def validate_artifact_data(data: dict[str, Any]) -> list[str]:
    """Return all promotion-blocking validation errors for an artifact."""
    errors: list[str] = []

    status = _status(data)
    success = _success(data)

    _require(data.get("schema") == REQUIRED_SCHEMA, f"schema must be {REQUIRED_SCHEMA}", errors)
    _require(status == "pass", f"status must be pass for promotion, got {status or '<missing>'}", errors)
    _require(success is True, "success must be true for promotion", errors)

    if status in NON_PROMOTING_STATUSES:
        _require(success is False, f"non-promoting status {status} must set success=false", errors)
        errors.append(f"non-promoting status {status} is not promotion eligible")
    elif status and status != "pass":
        errors.append(f"unknown non-pass status {status} is not promotion eligible")

    _require(
        data.get("command") == REQUIRED_COMMAND,
        f"command must be exactly {REQUIRED_COMMAND!r} (stale/missing command evidence)",
        errors,
    )
    _require(
        data.get("effective_command") == REQUIRED_EFFECTIVE_COMMAND,
        f"effective_command must be exactly {REQUIRED_EFFECTIVE_COMMAND!r} (stale/missing command evidence)",
        errors,
    )

    _require(_is_int_zero(data.get("exit_code")), f"exit_code must be numeric 0, got {data.get('exit_code')!r}", errors)
    _require(data.get("stdout_exact") == EXPECTED_STDOUT, "stdout_exact must be exactly 'hi'", errors)
    _require(data.get("stdout_exact_match") is True, "stdout_exact_match must be true", errors)
    _require(data.get("stderr_empty") is True, "stderr_empty must be true", errors)

    _require(_valid_container_id(data.get("container_id")), "container_id must be a real lowercase 64-hex Engine container ID", errors)
    _require(data.get("container_id_source") == "docker --cidfile", "container_id_source must be docker --cidfile", errors)
    _require(data.get("host_shell_fallback") is False, "host_shell_fallback must be false", errors)

    evidence = data.get("evidence")
    if not isinstance(evidence, dict):
        errors.append("evidence must be an object with stdout/stderr/combined_log/cidfile links")
    else:
        missing = [name for name in REQUIRED_EVIDENCE_LINKS if not _evidence_link_ok(evidence.get(name))]
        _require(not missing, "missing device evidence links: " + ", ".join(missing), errors)
        for name in REQUIRED_EVIDENCE_LINKS:
            link = evidence.get(name)
            if isinstance(link, str) and link.strip() and "docker-run-rm-ubuntu-echo-hi" not in link:
                errors.append(f"evidence link {name} must point at docker-run-rm-ubuntu-echo-hi diagnostics")

    return errors


def verify(path: Path) -> dict[str, Any]:
    data = _read_json(path)
    errors = validate_artifact_data(data)
    if errors:
        raise VerificationError("; ".join(errors))
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path, help="runtime-single-container-echo-hi-latest.json artifact to verify")
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

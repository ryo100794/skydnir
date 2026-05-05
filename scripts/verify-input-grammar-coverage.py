#!/usr/bin/env python3
"""Validate the BNF-like input grammar coverage ledger."""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "tests" / "input_grammar_coverage.json"
REQUIRED_GRAMMARS = {
    "engine-api-json-body",
    "dockerfile-standard-instruction-surface",
    "feature-scenario-ledger-json",
    "blackbox-requirements-json",
    "storage-metrics-json",
    "direct-syscall-boundary-ledger-json",
    "compose-file-full-grammar",
}
VALID_STATUSES = {"runnable", "planned-gap"}


def fail(message: str) -> None:
    print(f"verify-input-grammar-coverage: FAIL: {message}", file=sys.stderr)
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
        fail(f"evidence command is not shell-tokenizable: {command!r}: {exc}")
    paths: list[Path] = []
    for part in parts:
        if part.startswith(("scripts/", "tests/", "docs/", "docker-proot-setup/")):
            paths.append(ROOT / part)
    return paths


def require_nonempty_list(grammar_id: str, grammar: dict[str, Any], key: str) -> list[Any]:
    value = grammar.get(key)
    if not isinstance(value, list) or not value:
        fail(f"{grammar_id} must include non-empty {key}")
    return value


def validate_grammar(grammar: dict[str, Any]) -> str:
    grammar_id = str(grammar.get("id") or "")
    status = str(grammar.get("status") or "")
    if not grammar_id:
        fail("grammar entry is missing id")
    if not grammar.get("surface"):
        fail(f"{grammar_id} is missing surface")
    if status not in VALID_STATUSES:
        fail(f"{grammar_id} has invalid status {status!r}")
    bnf = require_nonempty_list(grammar_id, grammar, "bnf")
    for production in bnf:
        if not isinstance(production, str) or not production.strip().startswith("<") or "::=" not in production:
            fail(f"{grammar_id} has a non-BNF production: {production!r}")
    positive = require_nonempty_list(grammar_id, grammar, "positive_cases")
    negative = require_nonempty_list(grammar_id, grammar, "negative_cases")
    ranges = require_nonempty_list(grammar_id, grammar, "value_ranges")
    command = str(grammar.get("evidence_command") or "")
    if not command:
        fail(f"{grammar_id} must include evidence_command")
    for path in command_paths(command):
        if not path.exists():
            fail(f"{grammar_id} evidence command references missing path {path.relative_to(ROOT)}")
    if status == "runnable" and (not positive or not negative or not ranges):
        fail(f"{grammar_id} runnable grammar must include positive, negative, and range cases")
    if status == "planned-gap" and not grammar.get("gap_reason"):
        fail(f"{grammar_id} planned-gap must include gap_reason")
    return grammar_id


def main() -> int:
    data = load_json(LEDGER)
    policy = data.get("coverage_policy")
    if not isinstance(policy, dict) or not policy.get("artifact") or not policy.get("rule"):
        fail("coverage_policy must include rule and artifact")
    if data.get("notation") != "bnf-like":
        fail("input grammar coverage notation must be bnf-like")
    grammars = data.get("grammars")
    if not isinstance(grammars, list) or not grammars:
        fail("grammars must be a non-empty list")

    ids = [validate_grammar(grammar) for grammar in grammars if isinstance(grammar, dict)]
    if len(ids) != len(grammars):
        fail("grammar entries must be objects")
    duplicates = sorted({grammar_id for grammar_id in ids if ids.count(grammar_id) > 1})
    if duplicates:
        fail("duplicate grammar ids: " + ", ".join(duplicates))
    missing = sorted(REQUIRED_GRAMMARS - set(ids))
    if missing:
        fail("missing required input grammar ids: " + ", ".join(missing))
    planned = [grammar for grammar in grammars if grammar.get("status") == "planned-gap"]
    if not planned:
        fail("full or unsupported grammar gaps must be explicit planned gaps")
    if "compose-file-full-grammar" not in {str(grammar.get("id") or "") for grammar in planned}:
        fail("Compose full grammar must remain explicit until upstream-compatible parsing is implemented")

    ok(f"input grammar coverage records {len(grammars)} BNF-like grammars")
    ok(f"input grammar coverage has {len(planned)} planned full-grammar gap(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

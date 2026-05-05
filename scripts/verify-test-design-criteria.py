#!/usr/bin/env python3
"""Verify that the repository's test design meets the documented criteria."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CRITERIA = ROOT / "tests" / "test_design_criteria.json"
FEATURES = ROOT / "tests" / "feature_scenarios.json"
BLACKBOX = ROOT / "tests" / "blackbox_requirements.json"
INPUT_VALIDATION = ROOT / "tests" / "input_validation_cases.json"
ABNORMAL = ROOT / "tests" / "abnormal_event_cases.json"
REFACTOR = ROOT / "tests" / "refactor_resilience_cases.json"
STRESS = ROOT / "tests" / "stress_regression_cases.json"
SYSCALL = ROOT / "tests" / "direct_syscall_coverage.json"
STANDARD_DOC = ROOT / "docs" / "test" / "TEST_DESIGN_STANDARD.md"
DESIGN_AXIS_ALIASES = {
    "feature-ledger": ("feature scenario", "scenario ledger", "feature design"),
    "blackbox-positive-negative": ("blackbox", "positive/negative", "negative-oracles"),
    "abnormal-event-evidence": ("abnormal event", "abnormal-event", "failure oracle"),
    "refactor-resilience": ("refactor", "external-contract", "contract stability"),
    "input-api-arguments": ("api argument", "malformed json"),
    "input-file-grammar": ("input file grammar", "dockerfile syntax"),
    "input-value-ranges": ("value range", "numeric range", "boundary"),
    "syscall-path-variants": ("path variant", "path rewrite"),
    "syscall-boundaries": ("boundary", "boundary value"),
    "syscall-branches": ("branch", "branch decision", "branch decisions"),
    "seeded-random": ("seeded random", "random api fuzz"),
    "build-set-artifacts": ("build-set artifact", "build set artifact"),
    "device-lanes": ("device", "android"),
}
REQUIRED_MINIMUM_KEYS = {
    "literal_check_count_to_code_token_multiplier",
    "feature_scenarios",
    "fast_runnable_scenarios",
    "device_scenarios",
    "blackbox_requirements",
    "abnormal_event_cases",
    "refactor_resilience_cases",
    "input_validation_cases",
    "stress_regression_scenarios",
    "direct_syscall_coverage_entries",
    "direct_syscall_boundary_entries",
    "direct_syscall_branch_entries",
    "static_assertion_checks",
    "c0_statement_items",
    "c1_branch_outcome_items",
    "c2_condition_outcome_items",
    "implementation_code_tokens",
}
ADVANCED_METHOD_AREAS = {
    "mutation-testing",
    "property-based-testing",
    "differential-testing",
    "stateful-model-testing",
    "concurrency-race-testing",
    "crash-recovery-testing",
    "fault-injection",
    "golden-compatibility-corpus",
    "security-adversarial-testing",
    "performance-regression-gates",
}

STATIC_CHECK_SCRIPTS = (
    "scripts/verify-ui-actions.py",
    "scripts/verify_terminal_editor_contracts.py",
    "scripts/verify-memory-pager-design.py",
    "scripts/verify-project-library.py",
)
CODE_TOKEN_EXTENSIONS = {".c", ".cpp", ".h", ".hpp", ".java", ".kt", ".py", ".sh"}
TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+|[^\sA-Za-z0-9_]")
BRANCH_RE = re.compile(r"\b(if|elif|for|while|when|case|catch|except)\b|\?|&&|\|\|")
CONDITION_RE = re.compile(r"==|!=|<=|>=|<|>|&&|\|\||\band\b|\bor\b")
STATEMENT_RE = re.compile(
    r"[;{}]|\b(return|raise|throw|if|elif|for|while|when|case|try|catch|except|def|fun|class)\b"
)


def fail(message: str) -> None:
    print(f"verify-test-design-criteria: FAIL: {message}")
    raise SystemExit(1)


def ok(message: str) -> None:
    print(f"ok: {message}")


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text())
    if data.get("schema") != 1:
        fail(f"{path.relative_to(ROOT)} schema must be 1")
    return data


def validate_criteria_schema(criteria: dict[str, Any]) -> None:
    minimums = criteria.get("minimums")
    if not isinstance(minimums, dict) or not minimums:
        fail("test design criteria minimums must be a non-empty object")
    missing = sorted(REQUIRED_MINIMUM_KEYS - set(minimums))
    if missing:
        fail("test design criteria minimums missing key(s): " + ", ".join(missing))
    commands = criteria.get("required_fast_commands")
    if not isinstance(commands, list) or not commands:
        fail("test design criteria required_fast_commands must be a non-empty list")
    if len(set(str(command) for command in commands)) != len(commands):
        fail("test design criteria required_fast_commands must not contain duplicates")
    axes = criteria.get("required_design_axes")
    if not isinstance(axes, list) or not axes:
        fail("test design criteria required_design_axes must be a non-empty list")


def count_static_assertions() -> int:
    total = 0
    for rel in STATIC_CHECK_SCRIPTS:
        text = (ROOT / rel).read_text()
        total += len(re.findall(r"\brequire\(", text))
        total += len(re.findall(r"\bok\(", text))
    return total


def iter_implementation_files(criteria: dict[str, Any]) -> list[Path]:
    files: list[Path] = []
    for rel in criteria.get("implementation_token_scope", []):
        path = ROOT / str(rel)
        if path.is_file():
            files.append(path)
            continue
        if path.is_dir():
            for child in path.rglob("*"):
                if child.is_file() and child.suffix in CODE_TOKEN_EXTENSIONS:
                    files.append(child)
            continue
        fail(f"implementation token scope path is missing: {rel}")
    return sorted(set(files))


def token_count(paths: list[Path]) -> int:
    total = 0
    for path in paths:
        total += len(TOKEN_RE.findall(path.read_text(errors="ignore")))
    return total


def structural_coverage_items(paths: list[Path]) -> dict[str, int]:
    return structural_coverage_evidence(paths)["totals"]


def structural_coverage_evidence(paths: list[Path]) -> dict[str, Any]:
    c0_statements = 0
    c1_branches = 0
    c2_conditions = 0
    files: list[dict[str, Any]] = []
    for path in paths:
        text = path.read_text(errors="ignore")
        file_c0 = 0
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith(("#", "//", "/*", "*")):
                continue
            if STATEMENT_RE.search(stripped):
                file_c0 += 1
        file_c1 = len(BRANCH_RE.findall(text))
        file_c2 = len(CONDITION_RE.findall(text))
        c0_statements += file_c0
        c1_branches += file_c1
        c2_conditions += file_c2
        files.append(
            {
                "path": str(path.relative_to(ROOT)),
                "c0_statement_items": file_c0,
                "c1_branch_decisions": file_c1,
                "c1_branch_outcome_items": file_c1 * 2,
                "c2_condition_atoms": file_c2,
                "c2_condition_outcome_items": file_c2 * 2,
            }
        )
    totals = {
        "c0_statement_items": c0_statements,
        "c1_branch_decisions": c1_branches,
        "c1_branch_outcome_items": c1_branches * 2,
        "c2_condition_atoms": c2_conditions,
        "c2_condition_outcome_items": c2_conditions * 2,
    }
    return {
        "method": {
            "c0_statement_items": "Statement-like source lines matched by STATEMENT_RE, excluding blank and comment-leading lines.",
            "c1_branch_outcome_items": "Two outcomes for each branch decision matched by BRANCH_RE.",
            "c2_condition_outcome_items": "Two outcomes for each condition atom matched by CONDITION_RE.",
            "note": "This is structural design evidence, not runtime line/branch coverage from an instrumented runner.",
        },
        "totals": totals,
        "files": files,
    }


def direct_syscall_hook_count(data: dict[str, Any]) -> int:
    hooks: set[str] = set()
    for entry in data.get("coverage", []):
        if isinstance(entry, dict):
            hooks.update(str(name) for name in entry.get("syscalls", []))
    return len(hooks)


def count_input_validation_cases(data: dict[str, Any]) -> int:
    categories = data.get("categories", [])
    return sum(len(category.get("cases", [])) for category in categories if isinstance(category, dict))


def count_abnormal_event_cases(data: dict[str, Any]) -> int:
    cases = data.get("cases", [])
    return len([case for case in cases if isinstance(case, dict)])


def count_check_phrases(features: dict[str, Any]) -> int:
    total = 0
    for scenario in features.get("scenarios", []):
        if not isinstance(scenario, dict):
            continue
        checks = str(scenario.get("checks") or "")
        parts = [part.strip() for part in re.split(r",|\band\b", checks) if part.strip()]
        total += max(1, len(parts))
    return total


def validate_docs(criteria: dict[str, Any]) -> None:
    for rel in criteria.get("required_docs", []):
        path = ROOT / str(rel)
        if not path.exists():
            fail(f"required test design doc is missing: {rel}")
    text = STANDARD_DOC.read_text().lower()
    required_terms = (
        "implementation-code token",
        "c0",
        "c1",
        "c2",
        "blackbox",
        "negative",
        "input validation",
        "boundary",
        "seed",
        "monkey",
        "stress",
        "artifact",
        "variance",
        "build set",
        "literal",
        "mutation",
        "property-based",
        "differential",
        "stateful",
        "concurrency",
        "crash",
        "fault injection",
        "golden",
        "security",
        "performance regression",
    )
    missing = [term for term in required_terms if term not in text]
    if missing:
        fail("TEST_DESIGN_STANDARD.md is missing required term(s): " + ", ".join(missing))
    ok("test design standard documents the required quality gates")


def validate_feature_design(features: dict[str, Any]) -> tuple[int, int, int]:
    scenarios = features.get("scenarios")
    required_areas = set(features.get("required_areas", []))
    if not isinstance(scenarios, list) or not scenarios:
        fail("feature scenario ledger must contain scenarios")
    scenario_areas = {str(scenario.get("area") or "") for scenario in scenarios if isinstance(scenario, dict)}
    missing = sorted(required_areas - scenario_areas)
    if missing:
        fail("feature scenario ledger misses required areas: " + ", ".join(missing))
    fast_runnable = 0
    device = 0
    planned_gap = 0
    for scenario in scenarios:
        if not isinstance(scenario, dict):
            fail("feature scenario entries must be objects")
        sid = scenario.get("id")
        for key in ("area", "lane", "command", "status", "docs", "checks"):
            if not scenario.get(key):
                fail(f"{sid or '<unknown>'} is missing {key}")
        if scenario["lane"] == "fast-local" and scenario["status"] == "runnable":
            fast_runnable += 1
        if str(scenario["lane"]).startswith("android"):
            device += 1
        if scenario["status"] == "planned-gap":
            planned_gap += 1
    if planned_gap < 1:
        fail("at least one explicit planned compatibility gap must remain tracked")
    ok(f"feature design has {len(scenarios)} scenarios, {fast_runnable} fast runnable, {device} device")
    return len(scenarios), fast_runnable, device


def validate_required_design_axes(criteria: dict[str, Any], features: dict[str, Any]) -> int:
    required_axes = [str(axis) for axis in criteria.get("required_design_axes", [])]
    if not required_axes:
        fail("test design criteria must declare required_design_axes")
    scenarios = features.get("scenarios", [])
    haystack = "\n".join(
        " ".join(
            str(scenario.get(key, ""))
            for key in ("id", "area", "command", "status", "checks")
        ).lower()
        for scenario in scenarios
        if isinstance(scenario, dict)
    )
    missing = []
    for axis in required_axes:
        aliases = (axis.lower(), *DESIGN_AXIS_ALIASES.get(axis, ()))
        if not any(alias in haystack for alias in aliases):
            missing.append(axis)
    if missing:
        fail("required design axes are not represented in feature scenarios: " + ", ".join(missing))
    ok(f"feature scenarios represent {len(required_axes)} required design axes")
    return len(required_axes)


def validate_advanced_method_scenarios(features: dict[str, Any]) -> int:
    scenarios = [scenario for scenario in features.get("scenarios", []) if isinstance(scenario, dict)]
    by_area: dict[str, list[dict[str, Any]]] = {area: [] for area in ADVANCED_METHOD_AREAS}
    for scenario in scenarios:
        area = str(scenario.get("area") or "")
        if area in by_area:
            by_area[area].append(scenario)
    missing = sorted(area for area, entries in by_area.items() if not entries)
    if missing:
        fail("advanced test method scenarios missing: " + ", ".join(missing))
    for entries in by_area.values():
        for scenario in entries:
            sid = scenario.get("id")
            status = scenario.get("status")
            if status not in ("planned-gap", "runnable", "runnable-with-device", "runnable-with-env"):
                fail(f"{sid} has invalid advanced method status {status!r}")
            if status == "planned-gap":
                for key in ("evidence_target", "reason", "acceptance_scope", "exit_criteria"):
                    if not scenario.get(key):
                        fail(f"{sid} planned-gap must include {key}")
                if not isinstance(scenario["acceptance_scope"], list) or len(scenario["acceptance_scope"]) < 2:
                    fail(f"{sid} planned-gap acceptance_scope must list concrete subscopes")
                if not isinstance(scenario["exit_criteria"], list) or len(scenario["exit_criteria"]) < 2:
                    fail(f"{sid} planned-gap exit_criteria must list closure checks")
    ok(f"advanced method scenarios track {len(ADVANCED_METHOD_AREAS)} required methods")
    return sum(len(entries) for entries in by_area.values())


def validate_blackbox(data: dict[str, Any]) -> int:
    requirements = data.get("requirements")
    if not isinstance(requirements, list) or not requirements:
        fail("blackbox requirements must be a non-empty list")
    for entry in requirements:
        if not isinstance(entry, dict):
            fail("blackbox requirement entries must be objects")
        rid = entry.get("id")
        positive = entry.get("positive")
        negative = entry.get("negative")
        if not isinstance(positive, dict) or not positive.get("evidence"):
            fail(f"{rid} must include positive evidence")
        if not isinstance(negative, dict) or not negative.get("failure_oracle"):
            fail(f"{rid} must include a negative failure oracle")
    ok(f"blackbox design has {len(requirements)} positive/negative requirements")
    return len(requirements)


def validate_abnormal_events(data: dict[str, Any]) -> int:
    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        fail("abnormal event cases must be a non-empty list")
    required = {"input", "runtime", "build", "storage", "ui", "documents", "gpu", "network", "test-governance"}
    categories = {str(case.get("category") or "") for case in cases if isinstance(case, dict)}
    missing = sorted(required - categories)
    if missing:
        fail("abnormal event cases missing categories: " + ", ".join(missing))
    for case in cases:
        if not isinstance(case, dict):
            fail("abnormal event entries must be objects")
        cid = case.get("id")
        for key in ("severity", "trigger", "expected_signal", "failure_oracle", "evidence_source", "reproduction_command"):
            if not case.get(key):
                fail(f"{cid or '<unknown>'} abnormal event case must include {key}")
    ok(f"abnormal event design has {len(cases)} structured cases")
    return len(cases)


def validate_refactor_resilience(data: dict[str, Any]) -> int:
    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        fail("refactor-resilience cases must be a non-empty list")
    required = {
        "engine-api-golden",
        "compose-dockerfile-fixtures",
        "archive-round-trip",
        "state-machine-contract",
        "abnormal-replay",
        "artifact-diff",
    }
    axes = {str(case.get("axis") or "") for case in cases if isinstance(case, dict)}
    missing = sorted(required - axes)
    if missing:
        fail("refactor-resilience cases missing axes: " + ", ".join(missing))
    for case in cases:
        if not isinstance(case, dict):
            fail("refactor-resilience entries must be objects")
        cid = case.get("id")
        for key in ("observable_contract", "invariant", "refactor_risk", "evidence_artifact", "command", "contract_class"):
            if not case.get(key):
                fail(f"{cid or '<unknown>'} refactor-resilience case must include {key}")
        if case.get("status") == "runnable" and case.get("contract_class") == "known-bug-blocker":
            fail(f"{cid or '<unknown>'} must not freeze a known bug as a passing refactor contract")
        if case.get("status") == "planned-gap" and not case.get("gap_reason"):
            fail(f"{cid or '<unknown>'} refactor-resilience planned-gap must include gap_reason")
    ok(f"refactor-resilience design has {len(cases)} external-contract cases")
    return len(cases)


def validate_input_validation(data: dict[str, Any]) -> int:
    categories = {str(category.get("id") or "") for category in data.get("categories", []) if isinstance(category, dict)}
    required = {"api-arguments", "input-file-grammar", "value-ranges"}
    missing = sorted(required - categories)
    if missing:
        fail("input validation ledger missing categories: " + ", ".join(missing))
    count = count_input_validation_cases(data)
    ok(f"input validation design has {count} API/file/range cases")
    return count


def validate_stress(data: dict[str, Any]) -> int:
    scenarios = data.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        fail("stress regression ledger must contain scenarios")
    categories = {str(scenario.get("category") or "") for scenario in scenarios if isinstance(scenario, dict)}
    missing = sorted({"random", "monkey", "stress", "variance"} - categories)
    if missing:
        fail("stress regression ledger missing categories: " + ", ".join(missing))
    fields = set(str(field) for field in data.get("artifact_policy", {}).get("required_fields", []))
    missing_fields = sorted({"git_commit", "build_flavor", "timestamp_utc", "command", "summary"} - fields)
    if missing_fields:
        fail("stress artifact policy missing fields: " + ", ".join(missing_fields))
    for scenario in scenarios:
        sid = scenario.get("id")
        if scenario.get("category") in ("random", "monkey") and scenario.get("seed") is None:
            fail(f"{sid} must record a seed")
        if not scenario.get("artifact"):
            fail(f"{sid} must declare an artifact policy/path")
    ok(f"stress design has {len(scenarios)} random/monkey/stress/variance scenarios")
    return len(scenarios)


def parsed_runner_commands(text: str) -> set[str]:
    commands: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("run "):
            commands.add(line[4:].strip())
    return commands


def validate_fast_wiring_text(criteria: dict[str, Any], verify_fast: str, verify_scenarios: str) -> None:
    fast_commands = parsed_runner_commands(verify_fast)
    scenario_commands = parsed_runner_commands(verify_scenarios)
    for command in criteria.get("required_fast_commands", []):
        if command not in fast_commands:
            fail(f"verify-fast.sh must include {command}")
        if command not in scenario_commands:
            fail(f"verify-scenarios.sh must include {command}")


def validate_fast_wiring(criteria: dict[str, Any]) -> None:
    verify_fast = (ROOT / "scripts" / "verify-fast.sh").read_text()
    verify_scenarios = (ROOT / "scripts" / "verify-scenarios.sh").read_text()
    validate_fast_wiring_text(criteria, verify_fast, verify_scenarios)
    ok("fast and scenario runners enforce the test design gates")


def expect_failure(label: str, func, *args) -> None:
    try:
        func(*args)
    except SystemExit as exc:
        if exc.code:
            ok(f"negative self-test fails as expected: {label}")
            return
        fail(f"negative self-test exited successfully: {label}")
    fail(f"negative self-test did not fail: {label}")


def run_negative_self_tests(
    criteria: dict[str, Any],
    features: dict[str, Any],
    blackbox: dict[str, Any],
    input_validation: dict[str, Any],
    abnormal: dict[str, Any],
    refactor: dict[str, Any],
    stress: dict[str, Any],
) -> None:
    empty_minimums = deepcopy(criteria)
    empty_minimums["minimums"] = {}
    expect_failure("empty minimums", validate_criteria_schema, empty_minimums)

    missing_literal_minimum = deepcopy(criteria)
    missing_literal_minimum["minimums"].pop("literal_check_count_to_code_token_multiplier", None)
    expect_failure("missing literal token minimum", validate_criteria_schema, missing_literal_minimum)

    empty_fast_commands = deepcopy(criteria)
    empty_fast_commands["required_fast_commands"] = []
    expect_failure("empty required fast commands", validate_criteria_schema, empty_fast_commands)

    missing_axis = deepcopy(criteria)
    missing_axis["required_design_axes"] = ["definitely-missing-axis"]
    expect_failure("missing required design axis", validate_required_design_axes, missing_axis, features)

    missing_fast_command = deepcopy(criteria)
    missing_fast_command["required_fast_commands"] = ["python3 scripts/definitely-missing-test-gate.py"]
    expect_failure("missing fast runner wiring", validate_fast_wiring, missing_fast_command)

    commented_runner = "# run python3 scripts/verify-test-design-criteria.py\n"
    expect_failure(
        "commented-out runner command",
        validate_fast_wiring_text,
        criteria,
        commented_runner,
        commented_runner,
    )

    weak_advanced = deepcopy(features)
    for scenario in weak_advanced["scenarios"]:
        if scenario.get("area") == "mutation-testing":
            scenario.pop("evidence_target", None)
            break
    expect_failure("advanced planned-gap without evidence target", validate_advanced_method_scenarios, weak_advanced)

    weak_blackbox = deepcopy(blackbox)
    weak_blackbox["requirements"][0].pop("negative", None)
    expect_failure("blackbox requirement without negative oracle", validate_blackbox, weak_blackbox)

    weak_input = deepcopy(input_validation)
    weak_input["categories"] = [
        category for category in weak_input["categories"] if category.get("id") != "value-ranges"
    ]
    expect_failure("input validation missing value ranges", validate_input_validation, weak_input)

    weak_abnormal = deepcopy(abnormal)
    weak_abnormal["cases"][0].pop("failure_oracle", None)
    expect_failure("abnormal event without failure oracle", validate_abnormal_events, weak_abnormal)

    weak_refactor = deepcopy(refactor)
    weak_refactor["cases"][0].pop("observable_contract", None)
    expect_failure("refactor-resilience without observable contract", validate_refactor_resilience, weak_refactor)

    weak_stress = deepcopy(stress)
    for scenario in weak_stress["scenarios"]:
        if scenario.get("category") == "monkey":
            scenario["seed"] = None
            break
    expect_failure("monkey scenario without seed", validate_stress, weak_stress)


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


def build_report(metrics: dict[str, Any]) -> dict[str, Any]:
    summary_metrics = dict(metrics)
    structural_coverage_evidence = summary_metrics.pop("structural_coverage_evidence", None)
    failures = summary_metrics.pop("failures", [])
    return {
        "schema": 1,
        "kind": "test-design-criteria",
        "git_commit": git_commit(),
        "timestamp_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "metrics": summary_metrics,
        "structural_coverage_evidence": structural_coverage_evidence,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-artifact", type=Path, help="Write a JSON report for the current build set.")
    args = parser.parse_args()

    criteria = load_json(CRITERIA)
    features = load_json(FEATURES)
    blackbox = load_json(BLACKBOX)
    input_validation = load_json(INPUT_VALIDATION)
    abnormal = load_json(ABNORMAL)
    refactor = load_json(REFACTOR)
    stress = load_json(STRESS)
    syscall = load_json(SYSCALL)

    validate_criteria_schema(criteria)
    validate_docs(criteria)
    feature_count, fast_runnable, device_count = validate_feature_design(features)
    required_design_axes = validate_required_design_axes(criteria, features)
    advanced_method_count = validate_advanced_method_scenarios(features)
    blackbox_count = validate_blackbox(blackbox)
    abnormal_count = validate_abnormal_events(abnormal)
    refactor_count = validate_refactor_resilience(refactor)
    input_count = validate_input_validation(input_validation)
    stress_count = validate_stress(stress)
    validate_fast_wiring(criteria)
    run_negative_self_tests(criteria, features, blackbox, input_validation, abnormal, refactor, stress)

    direct_hooks = direct_syscall_hook_count(syscall)
    direct_coverage = len(syscall.get("coverage", []))
    direct_required_areas = len(syscall.get("required_areas", []))
    direct_path_variants = len(syscall.get("path_variant_matrix", []))
    direct_boundaries = len(syscall.get("boundary_value_matrix", []))
    direct_branches = len(syscall.get("branch_decision_matrix", []))
    direct_non_syscall = len(syscall.get("non_syscall_contracts", []))
    direct_heavy_cases = len(syscall.get("heavy_cases", []))
    static_assertions = count_static_assertions()
    feature_check_phrases = count_check_phrases(features)
    implementation_files = iter_implementation_files(criteria)
    implementation_tokens = token_count(implementation_files)
    structural_evidence = structural_coverage_evidence(implementation_files)
    structural_items = structural_evidence["totals"]

    implementation_steps = feature_count
    semantic_checks = (
        feature_count
        + feature_check_phrases
        + blackbox_count * 2
        + abnormal_count
        + refactor_count
        + input_count
        + stress_count
        + direct_hooks
        + direct_required_areas
        + direct_coverage
        + direct_path_variants
        + direct_boundaries
        + direct_branches
        + direct_non_syscall
        + direct_heavy_cases
        + required_design_axes
        + advanced_method_count
        + static_assertions
    )
    selected_checks = (
        semantic_checks
        + structural_items["c0_statement_items"]
        + structural_items["c1_branch_outcome_items"]
        + structural_items["c2_condition_outcome_items"]
    )
    ratio = selected_checks / implementation_steps
    literal_token_multiplier = selected_checks / implementation_tokens

    metrics = {
        "implementation_steps_proxy": implementation_steps,
        "implementation_code_files": len(implementation_files),
        "implementation_code_tokens": implementation_tokens,
        "selected_check_count": selected_checks,
        "semantic_check_count": semantic_checks,
        **structural_items,
        "check_to_step_ratio": round(ratio, 2),
        "literal_check_count_to_token_multiplier": round(literal_token_multiplier, 6),
        "literal_check_count_at_2x_token_target": implementation_tokens * 2,
        "literal_check_count_at_2x_token_target_met": selected_checks >= implementation_tokens * 2,
        "feature_scenarios": feature_count,
        "feature_check_phrases": feature_check_phrases,
        "fast_runnable_scenarios": fast_runnable,
        "device_scenarios": device_count,
        "blackbox_requirements": blackbox_count,
        "abnormal_event_cases": abnormal_count,
        "refactor_resilience_cases": refactor_count,
        "input_validation_cases": input_count,
        "stress_regression_scenarios": stress_count,
        "direct_syscall_hooks": direct_hooks,
        "direct_syscall_required_areas": direct_required_areas,
        "direct_syscall_coverage_entries": direct_coverage,
        "direct_syscall_path_variant_entries": direct_path_variants,
        "direct_syscall_boundary_entries": direct_boundaries,
        "direct_syscall_branch_entries": direct_branches,
        "direct_syscall_non_syscall_contracts": direct_non_syscall,
        "direct_syscall_heavy_cases": direct_heavy_cases,
        "required_design_axes": required_design_axes,
        "advanced_method_scenarios": advanced_method_count,
        "static_assertion_checks": static_assertions,
        "structural_coverage_evidence": structural_evidence,
    }

    minimums = criteria.get("minimums", {})
    minimum_map = {
        "literal_check_count_to_code_token_multiplier": literal_token_multiplier,
        "feature_scenarios": feature_count,
        "fast_runnable_scenarios": fast_runnable,
        "device_scenarios": device_count,
        "blackbox_requirements": blackbox_count,
        "abnormal_event_cases": abnormal_count,
        "refactor_resilience_cases": refactor_count,
        "input_validation_cases": input_count,
        "stress_regression_scenarios": stress_count,
        "direct_syscall_coverage_entries": direct_coverage,
        "direct_syscall_boundary_entries": direct_boundaries,
        "direct_syscall_branch_entries": direct_branches,
        "static_assertion_checks": static_assertions,
        "c0_statement_items": structural_items["c0_statement_items"],
        "c1_branch_outcome_items": structural_items["c1_branch_outcome_items"],
        "c2_condition_outcome_items": structural_items["c2_condition_outcome_items"],
        "implementation_code_tokens": implementation_tokens,
    }
    failures: list[str] = []
    for key, actual in minimum_map.items():
        expected = minimums.get(key)
        if expected is not None and actual < expected:
            failures.append(f"{key} below minimum: {actual} < {expected}")
    metrics["failures"] = failures

    ok(f"test design check density is {selected_checks}/{implementation_steps} = {ratio:.2f}x by feature-step proxy")
    ok(
        "test design literal token ratio is "
        f"{selected_checks}/{implementation_tokens} = {literal_token_multiplier:.3f}x "
        "(semantic + C0 + C1 + C2 check items)"
    )
    if args.write_artifact:
        args.write_artifact.parent.mkdir(parents=True, exist_ok=True)
        args.write_artifact.write_text(json.dumps(build_report(metrics), indent=2, sort_keys=True) + "\n")
        ok(f"wrote test design artifact: {args.write_artifact}")
    if failures:
        fail("; ".join(failures))
    ok("test design criteria are enforced automatically")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

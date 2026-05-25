#!/usr/bin/env python3
"""Select the next Q6 GPU action from a pre-flight plan and run artifact."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_VERIFIER = ROOT / "scripts" / "verify-llama-gpu-artifact.py"


def load_artifact_verifier():
    spec = importlib.util.spec_from_file_location("llama_gpu_artifact_verifier", ARTIFACT_VERIFIER)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load verifier: {ARTIFACT_VERIFIER}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"missing JSON file: {path}")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid JSON file: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"JSON root must be an object: {path}")
    return data


def walk_values(value: Any):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from walk_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_values(child)


def evidence_field_present(data: dict[str, Any], field: str) -> bool:
    if field in data:
        return True
    for value in walk_values(data):
        if isinstance(value, dict) and field in value:
            return True
    return False


def missing_evidence_fields(data: dict[str, Any], plan: dict[str, Any]) -> list[str]:
    required = plan.get("required_evidence_fields")
    if not isinstance(required, list):
        return ["<plan.required_evidence_fields>"]
    missing: list[str] = []
    for field in required:
        if not isinstance(field, str) or not field:
            missing.append("<invalid-required-field>")
        elif not evidence_field_present(data, field):
            missing.append(field)
    return missing


def select_branch(report: dict[str, Any], artifact: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    classification = str(report.get("classification") or "")
    q6 = report.get("q6_workgroup_diagnostics")
    if not isinstance(q6, dict):
        q6 = {}
    materialize_report = None
    for value in walk_values(artifact):
        if isinstance(value, dict) and isinstance(value.get("specialization_materialize_report"), dict):
            materialize_report = value["specialization_materialize_report"]
            break
    if materialize_report is None:
        materialize_report = {}

    if classification == "q6-workgroup-cleared-and-oracle-match":
        branch = plan.get("pass_branch")
        return branch if isinstance(branch, dict) else {
            "condition": "q6 oracle/prompt correctness passes",
            "action": "advance to correctness-gated performance measurement",
        }

    reason = str(materialize_report.get("failure_reason") or "")
    changed = materialize_report.get("changed")
    if reason == "unsupported-spec-expression":
        return {
            "condition": "specialization_materialize_report.failure_reason == unsupported-spec-expression",
            "action": "extend the SPIR-V materializer only for the reported specialization expression",
            "owner": "app/src/main/cpp/pdocker_gpu_executor.c",
        }
    if reason == "no-changes":
        return {
            "condition": "specialization_materialize_report.failure_reason == no-changes",
            "action": "inspect skip counts and WorkgroupSize subtree evidence before another device run",
            "owner": "materialize_spirv_specialization_constants",
        }
    if classification in {"q6-writeback-mismatch", "q6-writeback-unverified"}:
        return {
            "condition": "writeback verification is false or missing",
            "action": "fix fd/writeback integrity before judging shader arithmetic",
            "owner": "Vulkan writeback and binding report path",
        }
    if changed is True and classification.startswith("q6-"):
        return {
            "condition": "changed == true but Q6 oracle still mismatches",
            "action": "compare final-store dataflow, descriptor coordinates, and synchronization evidence",
            "owner": "SPIR-V final-store map and strict object graph",
        }
    if classification in {
        "vulkan-pipeline-feature-evidence-missing",
        "vulkan-generic-spirv-dispatch",
        "q6-not-reached",
    }:
        return {
            "condition": "pipeline/device-lost before Q6 evidence",
            "action": "identify the offending non-Q6 source/effective hash and keep materialization scoped",
            "owner": "pipeline creation policy and hash scope",
        }
    return {
        "condition": f"verifier classification == {classification or 'unknown'}",
        "action": str(report.get("next_action") or "inspect verifier report and add a pre-flight branch before rerun"),
        "owner": str(report.get("responsibility_boundary") or "unknown"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)

    plan = load_json(args.plan)
    artifact = load_json(args.artifact)
    if plan.get("schema") != "pdocker.llama.gpu.q6.preflight-plan.v1":
        raise SystemExit(f"unsupported pre-flight plan schema: {args.plan}")

    verifier = load_artifact_verifier()
    report = verifier.classify(artifact)
    missing = missing_evidence_fields(artifact, plan)
    branch = select_branch(report, artifact, plan)
    result = {
        "schema": "pdocker.llama.gpu.q6.plan-verdict.v1",
        "plan": str(args.plan),
        "artifact": str(args.artifact),
        "artifact_matches_plan_path": str(args.artifact) == str(plan.get("artifact_path")),
        "classification": report.get("classification"),
        "terminal": report.get("terminal"),
        "correctness_claim_allowed": report.get("correctness_claim_allowed"),
        "benchmark_claim_allowed": report.get("benchmark_claim_allowed"),
        "missing_required_evidence_fields": missing,
        "selected_branch": branch,
        "next_action": branch.get("action"),
        "verifier_report": report,
    }
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    print(text, end="")
    if missing:
        return 12
    return 0 if report.get("terminal") is True else 10


if __name__ == "__main__":
    raise SystemExit(main())

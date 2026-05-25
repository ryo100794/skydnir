#!/usr/bin/env python3
"""Create a Q6 Vulkan bridge pre-flight run plan without touching ADB."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "docs" / "design" / "VULKAN_BRIDGE_PROBE_MATRIX.md"
RUNNER = ROOT / "scripts" / "android-llama-gpu-q6-workgroup-run.sh"
ENV_MANIFEST = ROOT / "scripts" / "llama-gpu-env-manifest.json"


REQUIRED_EVIDENCE_FIELDS = [
    "executor_build_marker",
    "source_spirv_hash",
    "effective_spirv_hash",
    "oracle_spirv_hash",
    "specialization_materialize_report",
    "specialization_materialized",
    "local_size_patched",
    "spirv_local_size",
    "spirv_local_size_resolved",
    "spirv_local_size_consistent",
    "strict_object_graph",
    "reconciliation",
    "binding_details",
    "descriptor_usage",
    "cpu_oracle",
    "q6_row_indexed",
    "pre_barriers",
    "post_barriers",
    "upload_ms",
    "dispatch_ms",
    "download_ms",
]

def load_q6_required_env_overlay() -> dict[str, str]:
    manifest = json.loads(ENV_MANIFEST.read_text(encoding="utf-8"))
    overlay = manifest.get("q6_required_env_overlay")
    if not isinstance(overlay, dict) or not overlay:
        raise SystemExit(f"missing q6_required_env_overlay in {ENV_MANIFEST}")
    result: dict[str, str] = {}
    for key, value in overlay.items():
        if not isinstance(key, str) or not key.startswith("PDOCKER_"):
            raise SystemExit(f"invalid Q6 env key in {ENV_MANIFEST}: {key!r}")
        if not isinstance(value, str):
            raise SystemExit(f"invalid Q6 env value for {key} in {ENV_MANIFEST}")
        result[key] = value
    return result


Q6_REQUIRED_ENV_OVERLAY = load_q6_required_env_overlay()

RUNNER_STEP_CONTRACT = [
    {
        "name": "plan",
        "touches_adb": False,
        "command": "scripts/plan-llama-gpu-q6-run.py",
        "required_outputs": ["preflight_plan"],
    },
    {
        "name": "spv-preflight",
        "touches_adb": False,
        "command": "scripts/maintenance/verify-q6-workgroup-lowering-preflight.py",
        "required_outputs": ["q6_workgroup_lowering_preflight"],
    },
    {
        "name": "device-readiness",
        "touches_adb": True,
        "command": "scripts/android-llama-gpu-readiness.sh",
        "required_outputs": ["llama_gpu_readiness"],
    },
    {
        "name": "compare",
        "touches_adb": True,
        "command": "scripts/android-llama-gpu-compare.sh",
        "required_env_overlay": Q6_REQUIRED_ENV_OVERLAY,
        "required_flags": [
            "--gpu-only",
            "--cpu-tps",
            "--cpu-ctx",
            "--gpu-ctx",
            "--gpu-layers",
            "--predict",
            "--repeat",
            "--out",
        ],
        "required_outputs": ["compare_artifact"],
    },
    {
        "name": "artifact-verifier",
        "touches_adb": False,
        "command": "scripts/verify-llama-gpu-artifact.py",
        "required_flags": ["--require-q6-workgroup-clear"],
        "required_outputs": ["verifier_report_stdout"],
    },
    {
        "name": "plan-verdict",
        "touches_adb": False,
        "command": "scripts/verify-llama-gpu-q6-run-against-plan.py",
        "required_flags": ["--plan", "--artifact", "--out", "--allow-nonterminal"],
        "required_outputs": ["plan_verdict_artifact"],
    },
]


PASS_BRANCH = {
    "condition": (
        "specialization_materialize_report.changed == true and "
        "q6 oracle/prompt correctness passes"
    ),
    "action": "promote this run to correctness-gated performance measurement",
}


FAIL_BRANCHES = [
    {
        "condition": "specialization_materialize_report.failure_reason == unsupported-spec-expression",
        "action": "extend the SPIR-V materializer only for the reported specialization expression",
        "owner": "app/src/main/cpp/pdocker_gpu_executor.c",
    },
    {
        "condition": "specialization_materialize_report.failure_reason == no-changes",
        "action": "inspect skip counts and WorkgroupSize subtree evidence before another device run",
        "owner": "materialize_spirv_specialization_constants",
    },
    {
        "condition": "writeback verification is false or missing",
        "action": "fix fd/writeback integrity before judging shader arithmetic",
        "owner": "Vulkan writeback and binding report path",
    },
    {
        "condition": "changed == true but Q6 oracle still mismatches",
        "action": "compare final-store dataflow, descriptor coordinates, and synchronization evidence",
        "owner": "SPIR-V final-store map and strict object graph",
    },
    {
        "condition": "pipeline/device-lost before Q6 evidence",
        "action": "identify the offending non-Q6 source/effective hash and keep materialization scoped",
        "owner": "pipeline creation policy and hash scope",
    },
]


def default_artifact(serial: str, gpu_layers: int, now: datetime) -> str:
    safe_serial = re.sub(r"[^A-Za-z0-9]+", "", serial.split(":")[-1] if serial else "device")
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    return f"docs/test/llama-gpu-ngl{gpu_layers}-q6-probe-{safe_serial}-{stamp}.json"


def build_plan(args: argparse.Namespace) -> dict:
    now = datetime.now(timezone.utc)
    artifact = args.artifact or default_artifact(args.serial or "device", args.gpu_layers, now)
    runner_cmd = [
        "ANDROID_SERIAL=<prepared-device>",
        "scripts/android-llama-gpu-q6-workgroup-run.sh",
        "--out",
        artifact,
        "--cpu-tps",
        str(args.cpu_tps),
        "--cpu-ctx",
        str(args.cpu_ctx),
        "--gpu-ctx",
        str(args.gpu_ctx),
        "--gpu-layers",
        str(args.gpu_layers),
        "--predict",
        str(args.predict),
        "--repeat",
        str(args.repeat),
    ]
    if args.serial:
        runner_cmd[0] = f"ANDROID_SERIAL={args.serial}"
    return {
        "schema": "pdocker.llama.gpu.q6.preflight-plan.v1",
        "created_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git_commit_expected_at_run_time": "record with git rev-parse HEAD immediately before run",
        "adb_policy": "do not connect until the user says the Android device is prepared",
        "inputs": {
            "serial": args.serial or "<prepared-device>",
            "cpu_tps": args.cpu_tps,
            "cpu_ctx": args.cpu_ctx,
            "gpu_ctx": args.gpu_ctx,
            "gpu_layers": args.gpu_layers,
            "predict": args.predict,
            "repeat": args.repeat,
            "llama_cpp_may_change": False,
            "dockerfile_may_change": False,
            "model_may_change": False,
            "prompt_may_change": False,
        },
        "artifact_path": artifact,
        "probe_matrix": str(MATRIX.relative_to(ROOT)),
        "runner": str(RUNNER.relative_to(ROOT)),
        "runner_command": " ".join(runner_cmd),
        "runner_step_contract": RUNNER_STEP_CONTRACT,
        "q6_required_env_overlay": Q6_REQUIRED_ENV_OVERLAY,
        "required_evidence_fields": REQUIRED_EVIDENCE_FIELDS,
        "pass_branch": PASS_BRANCH,
        "fail_branches": FAIL_BRANCHES,
        "must_not_report_complete_until": [
            "artifact exists",
            "all required evidence fields are present or explicitly marked unavailable",
            "verifier classification is recorded",
            "next action is selected from pass_branch or fail_branches",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", default="", help="ADB serial to record in the plan; does not connect")
    parser.add_argument("--artifact", default="", help="planned output artifact path")
    parser.add_argument("--cpu-tps", default="0.04702448956650603")
    parser.add_argument("--cpu-ctx", type=int, default=512)
    parser.add_argument("--gpu-ctx", type=int, default=512)
    parser.add_argument("--gpu-layers", type=int, default=1)
    parser.add_argument("--predict", type=int, default=4)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--out", type=Path, default=Path("docs/test/llama-gpu-q6-preflight-plan-latest.json"))
    args = parser.parse_args(argv)
    if args.gpu_layers < 1:
        parser.error("--gpu-layers must be >= 1 for Q6 GPU probing")
    if args.cpu_ctx < 1:
        parser.error("--cpu-ctx must be >= 1")
    if args.gpu_ctx < 1:
        parser.error("--gpu-ctx must be >= 1")
    if args.predict < 2:
        parser.error("--predict must be >= 2")
    if args.repeat < 1:
        parser.error("--repeat must be >= 1")
    plan = build_plan(args)
    out = args.out
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(out.relative_to(ROOT) if out.is_relative_to(ROOT) else out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

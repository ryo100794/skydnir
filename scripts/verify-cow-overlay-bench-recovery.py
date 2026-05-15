#!/usr/bin/env python3
"""Validate the COW/overlay benchmark and recovery gate scaffold.

Default mode is static+schema validation.  Use --run-local to execute the host
libcow scripts with small iteration counts and validate their generated JSON.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / "docker-proot-setup" / "src" / "overlay"
BENCH = OVERLAY / "bench_cow.sh"
RECOVERY = OVERLAY / "test_cow.sh"

REQUIRED_METRICS = {
    "open_close",
    "stat",
    "create",
    "unlink",
    "rename",
    "copy_up",
    "layer_lookup",
}

REQUIRED_RECOVERY_CASES = {
    "copy_up.before_rename",
    "copy_up.kill_before_rename_recovery",
    "copy_up.truncate_before_rename",
    "metadata.chmod_before_rename",
    "rename.destination_copyup_fail_closed",
    "renameat.destination_copyup_fail_closed",
    "whiteout.before_publish",
    "rename.before_publish",
    "archive_put.stage_failure",
    "hardlink_metadata.corrupt_rebuild",
    "low_space.copy_up_enospc",
}

REQUIRED_RECOVERY_CHECKS = {
    "copy_up_fail_closed",
    "copy_up_kill_step_recovery",
    "truncate_fail_closed",
    "metadata_fail_closed",
    "rename_destination_copyup_fail_closed",
    "whiteout_fail_closed",
    "rename_fail_closed",
    "archive_put_fail_closed",
    "low_space_fail_closed",
}


def run(argv: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
        **kwargs,
    )


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def validate_bench_artifact(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    require(data.get("SchemaVersion") == 1, "bench schema version must be 1")
    require(data.get("Kind") == "cow-overlay-bench", "bench kind mismatch")
    require(data.get("Status") == "pass", "bench status must be pass")
    metrics = data.get("Metrics")
    require(isinstance(metrics, list) and metrics, "bench metrics missing")
    names = {m.get("name") for m in metrics}
    missing = REQUIRED_METRICS - names
    require(not missing, f"bench metrics missing: {sorted(missing)}")
    for metric in metrics:
        name = metric.get("name")
        require(metric.get("ops", 0) > 0, f"{name} ops must be positive")
        require("ns_per_op" in metric, f"{name} ns_per_op missing")
        require("p50_ns" in metric, f"{name} p50_ns missing")
        require("p95_ns" in metric, f"{name} p95_ns missing")
        require("p99_ns" in metric, f"{name} p99_ns missing")
    copy_up = [m for m in metrics if m.get("name") == "copy_up"]
    require(copy_up, "copy_up metric missing")
    for metric in copy_up:
        require(metric.get("metadata", {}).get("status") == "pass", "copy_up correctness failed")
    return data


def validate_recovery_artifact(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    require(data.get("SchemaVersion") == 1, "recovery schema version must be 1")
    require(data.get("Kind") == "cow-overlay-recovery", "recovery kind mismatch")
    require(data.get("Status") == "pass", "recovery status must be pass")
    checks = data.get("Checks", {})
    for check in sorted(REQUIRED_RECOVERY_CHECKS):
        require(checks.get(check) == "pass", f"{check} must pass")
    require(checks.get("hardlink_ring_corruption_rebuild") == "pass", "hardlink ring rebuild must pass")
    require(checks.get("kill_at_step_external_harness") == "planned-gap", "kill-at-step must remain planned-gap")
    case_results = data.get("CaseResults")
    require(isinstance(case_results, list) and case_results, "recovery CaseResults missing")
    case_ids = {case.get("Id") for case in case_results}
    missing = REQUIRED_RECOVERY_CASES - case_ids
    require(not missing, f"recovery cases missing: {sorted(missing)}")
    for case in case_results:
        cid = case.get("Id", "<missing>")
        require(case.get("Status") == "pass", f"{cid} must pass")
        require(case.get("Fault"), f"{cid} fault description missing")
        require(case.get("ExpectedRecovery"), f"{cid} expected recovery missing")
        require(case.get("Evidence"), f"{cid} evidence missing")
    negative = data.get("NegativeCases")
    require(isinstance(negative, list) and len(negative) >= len(REQUIRED_RECOVERY_CASES), "negative cases missing")
    require({case.get("Id") for case in negative} >= REQUIRED_RECOVERY_CASES, "negative cases must cover required recovery cases")
    cases = data.get("KillAtStepPlannedCases")
    require(isinstance(cases, list) and len(cases) >= 5, "kill-at-step planned cases missing")
    require(all(case.get("Status") == "planned-gap" for case in cases), "planned cases cannot be success")
    return data


def static_contract() -> None:
    for script in (BENCH, RECOVERY):
        require(script.exists(), f"missing script: {script}")
        require(os.access(script, os.X_OK), f"script is not executable: {script}")
        run(["bash", "-n", str(script)])
    bench_text = BENCH.read_text(encoding="utf-8")
    recovery_text = RECOVERY.read_text(encoding="utf-8")
    for name in sorted(REQUIRED_METRICS):
        require(f'"{name}"' in bench_text or f"({name}" in bench_text, f"bench script lacks metric {name}")
    require("COW_BENCH_JSON" in bench_text, "bench JSON output env missing")
    require("cow-overlay-bench" in bench_text, "bench artifact kind missing")
    require("COW_TEST_JSON" in recovery_text, "recovery JSON output env missing")
    require("hardlink_ring_corruption_rebuild" in recovery_text, "hardlink ring recovery check missing")
    require("CaseResults" in recovery_text, "recovery case results missing")
    require("NegativeCases" in recovery_text, "recovery negative cases missing")
    for case_id in sorted(REQUIRED_RECOVERY_CASES):
        require(case_id in recovery_text, f"recovery script lacks case {case_id}")
    require("KillAtStepPlannedCases" in recovery_text, "kill-at-step planned cases missing")


def run_local(output_dir: Path | None = None) -> tuple[Path, Path]:
    run(["make", "-C", str(OVERLAY), "all"])
    temp_ctx = None
    if output_dir is None:
        temp_ctx = tempfile.TemporaryDirectory(prefix="cow-overlay-gate-")
        tmp = Path(temp_ctx.name)
    else:
        tmp = output_dir
        tmp.mkdir(parents=True, exist_ok=True)
    try:
        bench_json = tmp / "bench.json"
        recovery_json = tmp / "recovery.json"
        if output_dir is not None:
            bench_json = tmp / "cow-overlay-bench-latest.json"
            recovery_json = tmp / "cow-overlay-recovery-latest.json"
        env = os.environ.copy()
        env.update(
            {
                "COW_BENCH_OPS": "20",
                "COW_BENCH_COPY_UP_FILES": "4",
                "COW_BENCH_JSON": str(bench_json),
                "COW_TEST_JSON": str(recovery_json),
            }
        )
        run(["bash", str(BENCH)], env=env)
        run(["bash", str(RECOVERY)], env=env)
        validate_bench_artifact(bench_json)
        validate_recovery_artifact(recovery_json)
        if output_dir is not None:
            return bench_json, recovery_json
        # Preserve copies under /tmp for human inspection when invoked manually.
        keep = Path(tempfile.mkdtemp(prefix="cow-overlay-gate-artifacts-"))
        kept_bench = keep / "cow-overlay-bench.json"
        kept_recovery = keep / "cow-overlay-recovery.json"
        kept_bench.write_text(bench_json.read_text(encoding="utf-8"), encoding="utf-8")
        kept_recovery.write_text(recovery_json.read_text(encoding="utf-8"), encoding="utf-8")
        return kept_bench, kept_recovery
    finally:
        if temp_ctx is not None:
            temp_ctx.cleanup()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-local", action="store_true", help="execute local libcow bench/recovery scripts")
    parser.add_argument("--output-dir", type=Path, help="write run-local artifacts to this directory")
    parser.add_argument("--bench-artifact", type=Path, help="validate an existing bench JSON artifact")
    parser.add_argument("--recovery-artifact", type=Path, help="validate an existing recovery JSON artifact")
    args = parser.parse_args(argv)

    static_contract()
    if args.bench_artifact:
        validate_bench_artifact(args.bench_artifact)
    if args.recovery_artifact:
        validate_recovery_artifact(args.recovery_artifact)
    if args.run_local:
        bench, recovery = run_local(args.output_dir)
        print(json.dumps({
            "status": "pass",
            "bench_artifact": str(bench),
            "recovery_artifact": str(recovery),
        }, sort_keys=True))
    else:
        print(json.dumps({"status": "pass", "mode": "static"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"cow overlay verification failed: {exc}", file=sys.stderr)
        raise SystemExit(1)

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
DEVICE_KILL_AT_STEP_RUNNER = ROOT / "scripts" / "verify" / "runner" / "cow_overlay_kill_at_step_device.py"
DEVICE_KILL_AT_STEP_SIDE = ROOT / "scripts" / "verify" / "runner" / "cow-overlay-kill-at-step-device.sh"
DEVICE_KILL_AT_STEP_DOC = ROOT / "docs" / "test" / "COW_OVERLAY_KILL_AT_STEP_DEVICE_GATE.md"

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
    "hardlink_metadata.truncated_rebuild",
    "low_space.copy_up_enospc",
}

REQUIRED_KILL_AT_STEP_CASES = {
    "copy_up.kill_before_rename_recovery",
}

REQUIRED_OOM_FORCED_KILL_CASES = {
    "oom_or_lmk.restart_reconciliation",
    "forced_kill.daemon_during_overlay_mutation",
    "forced_kill.helper_during_archive_put",
}

COPYUP_BEFORE_RENAME_EVIDENCE_REQUIREMENTS = {
    "copy_up.before_rename": {
        "fault_any": ("PDOCKER_COW_FAIL_BEFORE_RENAME", "copyup.before_rename"),
        "evidence_all": ("lower", "upper", ".cow", "unchanged"),
    },
    "copy_up.kill_before_rename_recovery": {
        "fault_any": ("kill:copyup.before_rename", "copyup.before_rename:kill"),
        "evidence_all": ("orphan", ".cow", "removed", "lower", "upper"),
    },
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
    "hardlink_ring_truncated_rebuild",
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


def require_text_any(text: str, needles: tuple[str, ...], message: str) -> None:
    folded = text.lower()
    require(any(needle.lower() in folded for needle in needles), message)


def require_text_all(text: str, needles: tuple[str, ...], message: str) -> None:
    folded = text.lower()
    missing = [needle for needle in needles if needle.lower() not in folded]
    require(not missing, f"{message}: missing {missing}")


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
    require(
        checks.get("kill_at_step_external_harness") == "planned-gap",
        "external kill-at-step harness must remain planned-gap until device evidence exists",
    )
    case_results = data.get("CaseResults")
    require(isinstance(case_results, list) and case_results, "recovery CaseResults missing")
    by_id = {case.get("Id"): case for case in case_results}
    case_ids = set(by_id)
    missing = REQUIRED_RECOVERY_CASES - case_ids
    require(not missing, f"recovery cases missing: {sorted(missing)}")
    for case in case_results:
        cid = case.get("Id", "<missing>")
        require(case.get("Status") == "pass", f"{cid} must pass")
        require(case.get("Fault"), f"{cid} fault description missing")
        require(case.get("ExpectedRecovery"), f"{cid} expected recovery missing")
        require(case.get("Evidence"), f"{cid} evidence missing")
    for cid, requirements in COPYUP_BEFORE_RENAME_EVIDENCE_REQUIREMENTS.items():
        case = by_id[cid]
        require_text_any(
            case.get("Fault", ""),
            requirements["fault_any"],
            f"{cid} fault must name the deterministic copyup.before_rename injection",
        )
        require_text_all(
            case.get("Evidence", ""),
            requirements["evidence_all"],
            f"{cid} evidence must prove fail/kill copy-up recovery",
        )
    negative = data.get("NegativeCases")
    require(isinstance(negative, list) and len(negative) >= len(REQUIRED_RECOVERY_CASES), "negative cases missing")
    negative_ids = {case.get("Id") for case in negative}
    require(negative_ids >= REQUIRED_RECOVERY_CASES, "negative cases must cover required recovery cases")
    require(negative_ids >= REQUIRED_KILL_AT_STEP_CASES, "negative cases must cover concrete kill-at-step cases")
    concrete = data.get("KillAtStepConcreteCases")
    require(isinstance(concrete, list) and concrete, "concrete kill-at-step cases missing")
    concrete_ids = {case.get("Id") for case in concrete}
    require(
        concrete_ids >= REQUIRED_KILL_AT_STEP_CASES,
        f"concrete kill-at-step cases missing: {sorted(REQUIRED_KILL_AT_STEP_CASES - concrete_ids)}",
    )
    for cid in sorted(REQUIRED_KILL_AT_STEP_CASES):
        case = by_id[cid]
        concrete_case = next(item for item in concrete if item.get("Id") == cid)
        require(concrete_case.get("Status") == "pass", f"{cid} concrete kill-at-step status must pass")
        require(
            concrete_case.get("Evidence") == case.get("Evidence"),
            f"{cid} concrete kill-at-step evidence must match CaseResults",
        )
    cases = data.get("KillAtStepPlannedCases")
    require(isinstance(cases, list) and len(cases) >= 5, "external kill-at-step planned cases missing")
    require(all(case.get("Status") == "planned-gap" for case in cases), "external planned cases cannot be success")
    oom_cases = data.get("OOMForcedKillConsistencyCases")
    require(isinstance(oom_cases, list) and oom_cases, "OOM/forced-kill consistency cases missing")
    oom_by_id = {case.get("Id"): case for case in oom_cases}
    missing_oom = REQUIRED_OOM_FORCED_KILL_CASES - set(oom_by_id)
    require(not missing_oom, f"OOM/forced-kill consistency cases missing: {sorted(missing_oom)}")
    for cid in sorted(REQUIRED_OOM_FORCED_KILL_CASES):
        case = oom_by_id[cid]
        status = case.get("Status")
        require(status in {"planned-gap", "pass"}, f"{cid} invalid status {status!r}")
        require(case.get("ExpectedConsistency"), f"{cid} expected consistency missing")
        require(case.get("FailureOracle"), f"{cid} failure oracle missing")
        if status == "planned-gap":
            require(case.get("GapReason"), f"{cid} planned-gap must explain gap reason")
        else:
            require(case.get("Evidence"), f"{cid} pass must include evidence")
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
    require("KillAtStepConcreteCases" in recovery_text, "concrete kill-at-step cases missing")
    require("KillAtStepPlannedCases" in recovery_text, "external kill-at-step planned cases missing")
    require("OOMForcedKillConsistencyCases" in recovery_text, "OOM/forced-kill consistency cases missing")
    for path in (DEVICE_KILL_AT_STEP_RUNNER, DEVICE_KILL_AT_STEP_SIDE, DEVICE_KILL_AT_STEP_DOC):
        require(path.exists(), f"missing external kill-at-step device gate file: {path}")
    runner_text = DEVICE_KILL_AT_STEP_RUNNER.read_text(encoding="utf-8")
    side_text = DEVICE_KILL_AT_STEP_SIDE.read_text(encoding="utf-8")
    doc_text = DEVICE_KILL_AT_STEP_DOC.read_text(encoding="utf-8")
    for token in (
        "pdocker.cow-overlay-kill-at-step-device.v1",
        "copy_up.daemon_kill_before_publish",
        "rename.daemon_kill_before_destination_publish",
        "metadata.daemon_kill_before_metadata_publish",
        "whiteout.daemon_kill_before_marker_publish",
        "hardlink_ring.daemon_kill_during_cache_publish",
        "hardlink_ring.helper_kill_during_cache_rebuild",
        "success",
        "planned-gap",
        "collected_via_adb_run_as",
    ):
        require(token in runner_text, f"device kill-at-step runner lacks {token}")
    require("pkill" not in side_text and "killall" not in side_text, "device kill-at-step side runner must not kill by process name")
    for token in ("Status: planned-gap", "success=false", "stable_checkpoint_eligible=false", "copy_up", "rename", "metadata", "whiteout", "hardlink_ring"):
        require(token in doc_text, f"device kill-at-step doc lacks {token}")
    with tempfile.TemporaryDirectory(prefix="cow-kill-at-step-static-") as td:
        planned = Path(td) / "planned.json"
        run([sys.executable, str(DEVICE_KILL_AT_STEP_RUNNER), "--adb", "__missing_adb_for_static_gate__", "--artifact", str(planned)])
        planned_data = json.loads(planned.read_text(encoding="utf-8"))
        require(planned_data.get("status") == "planned-gap", "missing-adb kill-at-step artifact must be planned-gap")
        require(planned_data.get("success") is False, "missing-adb kill-at-step artifact must not report success")
        require(planned_data.get("device_promotion_evidence") is False, "missing-adb artifact must not promote device evidence")


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

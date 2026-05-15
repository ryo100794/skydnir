#!/usr/bin/env python3
"""Android device-gated COW/overlay daemon/helper kill-at-step runner.

Default mode is non-executing and writes a planned-gap artifact.  A host without
adb, without a debuggable installed APK, or without the future APK kill-step
protocol must never be reported as success.  Passing artifacts are accepted only
when they contain per-case evidence that adb/run-as reached a deterministic
checkpoint, killed the named daemon/helper process, restarted/reconciled, and
verified the merged overlay state.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ARTIFACT = ROOT / "docs" / "test" / "cow-overlay-kill-at-step-latest.json"
DEFAULT_DEVICE_OUT = "/data/local/tmp/pdocker-cow-overlay-kill-at-step"
DEVICE_SIDE_RUNNER = ROOT / "scripts" / "verify" / "runner" / "cow-overlay-kill-at-step-device.sh"
SCHEMA = "pdocker.cow-overlay-kill-at-step-device.v1"
SCENARIO_ID = "cow.overlay.external-daemon-helper-kill-at-step"
PLAN_GATE = "python3 scripts/verify-cow-overlay-bench-recovery.py"

NON_PASSING = {"planned-gap", "blocked-device", "blocked", "failed", "fail", "skip", "skipped"}
VALID_STATUS = NON_PASSING | {"pass"}

REQUIRED_CASES: list[dict[str, Any]] = [
    {
        "Id": "copy_up.daemon_kill_before_publish",
        "Operation": "copy-up",
        "ProcessTarget": "daemon",
        "Step": "copyup.before_publish_rename",
        "ExpectedRecovery": "old committed lower/upper view or a complete atomically published upper entry; no trusted .cow temp",
        "ProofKeys": ["LowerSha256Unchanged", "NoCowTempResidue", "MergedViewVerified"],
    },
    {
        "Id": "rename.daemon_kill_before_destination_publish",
        "Operation": "rename",
        "ProcessTarget": "daemon",
        "Step": "rename.before_destination_publish",
        "ExpectedRecovery": "destination is either the old committed file or the complete renamed file; no staged rename is trusted",
        "ProofKeys": ["DestinationStateAtomic", "NoRenameStageResidueTrusted", "MergedViewVerified"],
    },
    {
        "Id": "metadata.daemon_kill_before_metadata_publish",
        "Operation": "metadata",
        "ProcessTarget": "daemon",
        "Step": "metadata.before_chmod_or_sidecar_publish",
        "ExpectedRecovery": "mode/metadata state is atomic with payload state; metadata-only commits are rejected",
        "ProofKeys": ["MetadataStateAtomic", "NoMetadataOnlyCommit", "MergedViewVerified"],
    },
    {
        "Id": "whiteout.daemon_kill_before_marker_publish",
        "Operation": "whiteout",
        "ProcessTarget": "daemon",
        "Step": "whiteout.before_marker_publish",
        "ExpectedRecovery": "lower entry remains visible unless a complete whiteout marker was published; partial markers are ignored",
        "ProofKeys": ["WhiteoutStateAtomic", "NoPartialWhiteoutTrusted", "MergedViewVerified"],
    },
    {
        "Id": "hardlink_ring.daemon_kill_during_cache_publish",
        "Operation": "hardlink-ring",
        "ProcessTarget": "daemon",
        "Step": "hardlink_ring.before_cache_publish",
        "ExpectedRecovery": "payload tree remains authoritative and hardlink-ring cache is discarded/rebuilt if interrupted",
        "ProofKeys": ["PayloadTreeAuthoritative", "RingCacheRebuilt", "CorruptOrTruncatedCacheDiscarded"],
    },
    {
        "Id": "hardlink_ring.helper_kill_during_cache_rebuild",
        "Operation": "hardlink-ring",
        "ProcessTarget": "helper",
        "Step": "hardlink_ring.helper_rebuild_before_publish",
        "ExpectedRecovery": "helper death cannot promote a partial hardlink-ring cache; restart rebuilds from payload tree",
        "ProofKeys": ["PayloadTreeAuthoritative", "RingCacheRebuilt", "HelperStageDiscarded"],
    },
]

REQUIRED_OPERATION_COVERAGE = {"copy-up", "rename", "metadata", "whiteout", "hardlink-ring"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def run_cmd(argv: list[str], timeout: int = 20) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)


def detect_device(adb: str) -> dict[str, Any]:
    info: dict[str, Any] = {
        "adb_present": False,
        "state": None,
        "serial": None,
        "fingerprint": None,
        "detection_error": None,
    }
    if shutil.which(adb) is None:
        info["detection_error"] = f"ADB executable {adb!r} was not found"
        return info
    info["adb_present"] = True
    state = run_cmd([adb, "get-state"])
    if state.returncode != 0:
        info["detection_error"] = "adb get-state failed: " + (state.stderr or state.stdout).strip()
        return info
    info["state"] = state.stdout.strip()
    serial = run_cmd([adb, "get-serialno"])
    if serial.returncode == 0:
        info["serial"] = serial.stdout.strip() or None
    fp = run_cmd([adb, "shell", "getprop", "ro.build.fingerprint"])
    if fp.returncode == 0:
        info["fingerprint"] = fp.stdout.strip() or None
    return info


def planned_case(case: dict[str, Any], status: str, reason: str) -> dict[str, Any]:
    return {
        "Id": case["Id"],
        "Operation": case["Operation"],
        "ProcessTarget": case["ProcessTarget"],
        "Step": case["Step"],
        "KillSignal": "TERM-or-KILL after checkpoint acknowledgement",
        "Status": status,
        "ExpectedRecovery": case["ExpectedRecovery"],
        "FailureOracle": failure_oracle(case["Operation"]),
        "GapReason": reason,
        "Proof": {key: False for key in case["ProofKeys"]},
        "EvidenceFiles": [],
    }


def failure_oracle(operation: str) -> str:
    by_operation = {
        "copy-up": "fail if lower bytes change, a .cow temp is trusted, or upper shows a partial payload after restart",
        "rename": "fail if destination mixes old/new content or a staged rename is accepted as committed",
        "metadata": "fail if mode/sidecar metadata is published without the matching payload state",
        "whiteout": "fail if a partial whiteout hides a lower entry or survives as trusted state",
        "hardlink-ring": "fail if corrupt/truncated ring cache is trusted instead of rebuilding from the payload tree",
    }
    return by_operation[operation]


def scenario_commands(adb: str, package: str, token: str, device_out: str) -> list[str]:
    remote = "/data/local/tmp/pdocker-cow-overlay-kill-at-step-device.sh"
    commands = [
        f"{PLAN_GATE}",
        f"{adb} get-state",
        f"{adb} shell run-as {package} sh -c 'cd files && test -S pdocker/pdockerd.sock'",
        f"{adb} push scripts/verify/runner/cow-overlay-kill-at-step-device.sh {remote}",
        f"{adb} shell chmod 755 {remote}",
        f"{adb} shell sh {remote} --phase preflight --package {package} --token {token} --out-dir {device_out}",
        f"{adb} shell sh {remote} --phase execute --package {package} --token {token} --out-dir {device_out}",
        f"{adb} pull {device_out} docs/test/device-evidence/cow-overlay-kill-at-step-{token}",
        f"{adb} shell sh {remote} --phase cleanup --package {package} --token {token} --out-dir {device_out}",
    ]
    return commands


def base_artifact(args: argparse.Namespace, status: str, reason: str, device: dict[str, Any] | None = None) -> dict[str, Any]:
    token = safe_token(args.token)
    case_status = "planned-gap" if status == "planned-gap" else "blocked-device"
    cases = [planned_case(case, case_status, reason) for case in REQUIRED_CASES]
    return {
        "schema": SCHEMA,
        "scenario_id": SCENARIO_ID,
        "status": status,
        "success": False,
        "stable_checkpoint_eligible": False,
        "device_promotion_evidence": False,
        "generated_at": utc_now(),
        "plan_gate": PLAN_GATE,
        "requires_adb": True,
        "collected_via_adb_run_as": False,
        "host_static_verifier_cannot_promote": True,
        "device": device or {"adb_present": False, "state": None, "serial": None, "fingerprint": None},
        "inputs": {
            "package": args.package,
            "token": token,
            "device_out_dir": args.device_out,
            "execute_device_requested": bool(args.execute_device),
            "allowed_process_targets": ["daemon", "helper"],
        },
        "coverage": {operation: False for operation in sorted(REQUIRED_OPERATION_COVERAGE)},
        "phases": ["preflight", "execute", "pull-evidence", "cleanup"],
        "required_cases": REQUIRED_CASES,
        "kill_at_step_cases": cases,
        "negative_expected_conditions": [
            "success=true without adb/run-as checkpoint and kill evidence",
            "HTTP or CLI acknowledgement without post-restart merged-view verification",
            "partial .cow payload trusted as complete copy-up",
            "partial rename, metadata, or whiteout stage accepted as committed state",
            "corrupt or truncated hardlink-ring cache trusted instead of rebuilt",
            "helper exit status alone treated as recovery proof",
        ],
        "cleanup_policy": [
            "collect evidence before cleanup",
            "remove only token-scoped files under the device output directory",
            "leave app payload stores and unrelated containers untouched",
        ],
        "commands": scenario_commands(args.adb, args.package, token, args.device_out),
        "notes": [reason, "Non-passing artifacts are non-promoting and must not be counted as stable checkpoint evidence."],
    }


def safe_token(raw: str | None) -> str:
    base = raw or f"cowkill-{time.strftime('%Y%m%d%H%M%S', time.gmtime())}-{os.getpid()}"
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in base)[:64]
    return cleaned or "cowkill"


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def validate_artifact_data(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    status = data.get("status")
    require(data.get("schema") == SCHEMA, f"schema must be {SCHEMA}", errors)
    require(data.get("scenario_id") == SCENARIO_ID, f"scenario_id must be {SCENARIO_ID}", errors)
    require(status in VALID_STATUS, f"invalid status {status!r}", errors)
    require(data.get("requires_adb") is True, "requires_adb must be true", errors)
    require(data.get("host_static_verifier_cannot_promote") is True, "host static verifier cannot promote device evidence", errors)
    cases = data.get("kill_at_step_cases")
    require(isinstance(cases, list), "kill_at_step_cases must be a list", errors)
    cases = cases if isinstance(cases, list) else []
    by_id = {case.get("Id"): case for case in cases if isinstance(case, dict)}
    required_ids = {case["Id"] for case in REQUIRED_CASES}
    missing = required_ids - set(by_id)
    require(not missing, f"missing required kill-at-step cases: {sorted(missing)}", errors)
    operations = {case.get("Operation") for case in cases if case.get("Status") == "pass"}

    if status == "pass":
        require(data.get("success") is True, "pass artifacts must set success=true", errors)
        require(data.get("device_promotion_evidence") is True, "pass artifacts require device_promotion_evidence=true", errors)
        require(data.get("collected_via_adb_run_as") is True, "pass artifacts require collected_via_adb_run_as=true", errors)
        device = data.get("device") or {}
        require(bool(device.get("adb_present")), "pass artifacts require adb_present=true", errors)
        require(device.get("state") == "device", "pass artifacts require adb state=device", errors)
        require(bool(device.get("serial")), "pass artifacts require device serial", errors)
        require(REQUIRED_OPERATION_COVERAGE <= operations, f"pass artifacts must cover operations {sorted(REQUIRED_OPERATION_COVERAGE)}", errors)
        for required in REQUIRED_CASES:
            case = by_id.get(required["Id"], {})
            prefix = required["Id"]
            require(case.get("Status") == "pass", f"{prefix} must pass in a pass artifact", errors)
            require(case.get("ProcessTarget") == required["ProcessTarget"], f"{prefix} process target mismatch", errors)
            require(case.get("Operation") == required["Operation"], f"{prefix} operation mismatch", errors)
            require(case.get("Step") == required["Step"], f"{prefix} step mismatch", errors)
            require(case.get("OperationId"), f"{prefix} missing OperationId", errors)
            require(isinstance(case.get("KilledPid"), int) and case.get("KilledPid") > 0, f"{prefix} missing killed pid", errors)
            require(case.get("KilledProcessName"), f"{prefix} missing killed process name", errors)
            require(case.get("CheckpointReached") is True, f"{prefix} checkpoint was not proven", errors)
            require(case.get("KillDelivered") is True, f"{prefix} kill delivery was not proven", errors)
            require(case.get("RestartCompleted") is True, f"{prefix} restart/reconciliation was not proven", errors)
            require(case.get("MergedViewVerified") is True, f"{prefix} merged view was not verified", errors)
            require(case.get("FailureOracleMatched") is True, f"{prefix} failure oracle was not matched", errors)
            require(bool(case.get("EvidenceFiles")), f"{prefix} missing evidence files", errors)
            proof = case.get("Proof") or {}
            for key in required["ProofKeys"]:
                require(proof.get(key) is True, f"{prefix} proof.{key} must be true", errors)
    else:
        require(data.get("success") is False, "non-passing artifacts must set success=false", errors)
        require(data.get("stable_checkpoint_eligible") is False, "non-passing artifacts must set stable_checkpoint_eligible=false", errors)
        require(data.get("device_promotion_evidence") is False, "non-passing artifacts must set device_promotion_evidence=false", errors)
        for required in REQUIRED_CASES:
            case = by_id.get(required["Id"], {})
            case_status = case.get("Status")
            require(case_status in NON_PASSING, f"{required['Id']} non-pass case cannot have status {case_status!r}", errors)
            require(bool(case.get("GapReason") or case.get("BlockedReason")), f"{required['Id']} non-pass case must explain gap/blocker", errors)
    return errors


def validate_artifact_path(path: Path) -> None:
    errors = validate_artifact_data(json.loads(path.read_text(encoding="utf-8")))
    if errors:
        raise SystemExit("FAIL: " + "; ".join(errors))


def write_planned_gap(args: argparse.Namespace, artifact: Path) -> int:
    device = detect_device(args.adb)
    if device.get("adb_present") is False:
        reason = "adb unavailable; external Android daemon/helper kill-at-step evidence remains planned-gap"
    elif device.get("state") != "device":
        reason = "no ready adb device; external Android daemon/helper kill-at-step evidence remains planned-gap"
    else:
        reason = "device detected but --execute-device was not requested; no kill-at-step proof was collected"
    data = base_artifact(args, "planned-gap", reason, device)
    write_json(artifact, data)
    validate_artifact_path(artifact)
    print(f"status=planned-gap artifact={artifact}")
    return 0


def write_blocked_device(args: argparse.Namespace, artifact: Path, reason: str, device: dict[str, Any] | None = None) -> int:
    data = base_artifact(args, "blocked-device", reason, device)
    write_json(artifact, data)
    validate_artifact_path(artifact)
    print(f"status=blocked-device artifact={artifact}")
    return 2


def execute_device(args: argparse.Namespace, artifact: Path) -> int:
    device = detect_device(args.adb)
    if device.get("adb_present") is False or device.get("state") != "device":
        return write_blocked_device(args, artifact, "adb device unavailable; no kill-at-step execution attempted", device)
    if not DEVICE_SIDE_RUNNER.exists():
        return write_blocked_device(args, artifact, "device-side COW kill-at-step runner is missing", device)
    return write_blocked_device(
        args,
        artifact,
        "APK deterministic COW kill-at-step debug protocol is not implemented/promoted yet; refusing to synthesize success",
        device,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=Path, default=DEFAULT_ARTIFACT)
    parser.add_argument("--validate-artifact", type=Path)
    parser.add_argument("--adb", default=os.environ.get("ADB", "adb"))
    parser.add_argument("--package", default=os.environ.get("PDOCKER_ANDROID_PACKAGE", "io.github.ryo100794.pdocker.compat"))
    parser.add_argument("--device-out", default=DEFAULT_DEVICE_OUT)
    parser.add_argument("--token")
    parser.add_argument("--execute-device", action="store_true", help="attempt device execution; without this only planned-gap is written")
    args = parser.parse_args(argv)

    if args.validate_artifact:
        validate_artifact_path(args.validate_artifact)
        print(f"ok: {args.validate_artifact}")
        return 0
    if args.execute_device:
        return execute_device(args, args.artifact)
    return write_planned_gap(args, args.artifact)


if __name__ == "__main__":
    raise SystemExit(main())

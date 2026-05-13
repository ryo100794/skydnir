#!/usr/bin/env python3
"""Device runner for interrupted image-pull crash-safety evidence.

The host-only verifier proves the static publication contract.  This runner adds
an Android-device lane that exercises the daemon restart recovery path with
scenario-owned pull residue: staged image directories, old-tag backups, tmp
layers, and malformed partial layers.  It is intentionally conservative: it
never deletes broad stores, never reports success without pulled-back evidence,
and keeps the live network-pull kill as an explicit remaining gap until timing is
safe enough for routine automation.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ARTIFACT = ROOT / "docs" / "test" / "image-pull-crash-safety-latest.json"
DEFAULT_DEVICE_OUT = "/data/local/tmp/pdocker-image-pull-crash-safety"
SCENARIO_ID = "image.pull.interrupted-kill-restart"
PLAN_GATE = "python3 scripts/verify-image-pull-crash-safety.py"
DEVICE_RUNNER = ROOT / "scripts" / "verify" / "runner" / "image-pull-crash-safety-device.sh"

ARTIFACT_SCHEMA: dict[str, Any] = {
    "schema_version": 2,
    "scenario_id": SCENARIO_ID,
    "status": "planned-gap|blocked|failed|passed",
    "success": False,
    "generated_at": "RFC3339 UTC timestamp",
    "device": {
        "adb_present": "boolean",
        "serial": "string|null",
        "state": "string|null",
        "fingerprint": "string|null",
    },
    "inputs": {
        "image": "registry reference used by the future live-pull lane",
        "package": "Android package id",
        "token": "scenario-owned suffix used for all device paths",
        "device_out_dir": "device artifact directory",
    },
    "coverage": {
        "residue_recovery": "boolean",
        "daemon_kill_restart": "boolean",
        "engine_negative_probe": "boolean",
        "live_interrupted_network_pull": "boolean",
    },
    "phases": ["prepare-residue", "kill-daemon", "restart-and-probe", "cleanup"],
    "phase_results": "per-phase return code/stdout/stderr summary",
    "evidence": {
        "device_evidence_dir": "local pulled evidence directory|null",
        "prepare_summary": "path|null",
        "kill_summary": "path|null",
        "restart_summary": "path|null",
        "cleanup_summary": "path|null",
        "store_listing_before_kill": "path|null",
        "store_listing_after_restart": "path|null",
        "image_inspect_after_restart": "path|null",
        "never_image_inspect_after_restart": "path|null",
        "daemon_log_before_kill": "path|null",
        "daemon_log_after_restart": "path|null",
        "container_run_after_restart": "path|null",
    },
    "assertions": {
        "old_tag_restored": "boolean|null",
        "pull_stage_pruned": "boolean|null",
        "tmp_layer_pruned": "boolean|null",
        "partial_layer_pruned": "boolean|null",
        "never_published_tag_rejected": "boolean|null",
        "restored_tag_inspectable": "boolean|null",
        "cleanup_removed_only_scenario_owned_paths": "boolean|null",
    },
    "negative_expected_conditions": ["strings that must not appear in evidence"],
    "cleanup_policy": ["cleanup steps safe to run after pass/fail/interrupt"],
    "remaining_gap": ["items not yet covered by this concrete runner"],
    "notes": ["operator-readable notes"],
}

NEGATIVE_EXPECTED_CONDITIONS = [
    "partial .pull-* image stage is accepted as a tag after restart",
    "partial .tmp-* layer directory is accepted as a complete layer",
    "missing tree/ for a malformed partial layer is treated as reusable cache",
    "old tag backup is lost when replacement pull is killed before publish",
    "docker image inspect succeeds for a never-published interrupted tag",
    "docker run succeeds from a tag whose pull was killed before atomic publish",
    "cleanup deletes unrelated images, layers, containers, app data, or other workers' files",
]

CLEANUP_POLICY = [
    "Always collect daemon log, image store listing, and layer store listing before cleanup.",
    "Run the backend startup recovery path after restart; do not manually delete evidence first.",
    "Remove only scenario-owned test tags, stage directories, layer residues, and device artifacts after evidence capture.",
    "Leave unrelated images, layers, containers, app data, and other workers' files untouched.",
    "If cleanup itself fails, keep success=false and record the remaining paths in notes.",
]

REMAINING_GAP = [
    "Live registry pull interruption is not killed mid-download by default; this runner currently injects scenario-owned residue and proves restart recovery.",
    "Container run is not attempted for the never-published tag because create would auto-pull a missing public reference; inspect/listing rejection is the safe negative probe.",
]

PHASES = ["prepare-residue", "kill-daemon", "restart-and-probe", "cleanup"]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def host_command(adb: str, serial: str | None, *args: str) -> str:
    base = [adb]
    if serial:
        base += ["-s", serial]
    base += list(args)
    return shlex.join(base)


def scenario_commands(adb: str, serial: str | None, package: str, image: str, artifact: Path, device_out: str, token: str) -> list[str]:
    device_runner = "/data/local/tmp/pdocker-image-pull-crash-safety.sh"
    commands = [
        shlex.join(["python3", "scripts/verify-image-pull-crash-safety.py"]),
        host_command(adb, serial, "get-state"),
        host_command(adb, serial, "shell", "getprop", "ro.build.fingerprint"),
        host_command(adb, serial, "push", "scripts/verify/runner/image-pull-crash-safety-device.sh", device_runner),
        host_command(adb, serial, "shell", "chmod", "755", device_runner),
    ]
    for phase in PHASES:
        commands.append(host_command(adb, serial, "shell", "sh", device_runner, "--package", package, "--image", image, "--token", token, "--out-dir", device_out, "--phase", phase))
    commands.append(host_command(adb, serial, "pull", device_out, str(artifact.parent / "image-pull-crash-safety-device")))
    return commands


def run_cmd(cmd: list[str], *, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)


def detect_device(adb: str, serial: str | None) -> tuple[dict[str, Any], list[str]]:
    notes: list[str] = []
    present = shutil.which(adb) is not None
    device: dict[str, Any] = {"adb_present": present, "serial": serial, "state": None, "fingerprint": None}
    if not present:
        notes.append(f"ADB executable {adb!r} was not found; device evidence remains planned-gap.")
        return device, notes

    cmd = [adb]
    if serial:
        cmd += ["-s", serial]
    cmd += ["get-state"]
    try:
        state = run_cmd(cmd, timeout=10)
    except Exception as exc:  # pragma: no cover - depends on host adb/device state
        notes.append(f"ADB get-state failed: {exc}")
        return device, notes
    device["state"] = state.stdout.strip() or None
    if state.returncode != 0 or device["state"] != "device":
        notes.append("No ready Android device was available; not executing interrupted-pull scenario.")
        if state.stderr.strip():
            notes.append(state.stderr.strip())
        return device, notes

    fcmd = [adb]
    if serial:
        fcmd += ["-s", serial]
    fcmd += ["shell", "getprop", "ro.build.fingerprint"]
    fp = run_cmd(fcmd, timeout=10)
    device["fingerprint"] = fp.stdout.strip() or None
    notes.append("Device detected; concrete synthetic residue kill/restart runner is available with --execute-device.")
    return device, notes


def safe_token(raw: str | None = None) -> str:
    if not raw:
        raw = time.strftime("%Y%m%d%H%M%S", time.gmtime()) + f"-{os.getpid()}"
    token = re.sub(r"[^A-Za-z0-9_.-]", "-", raw).strip(".-")
    return token[:64] or "scenario"


def phase_result(phase: str, cp: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    return {
        "phase": phase,
        "returncode": cp.returncode,
        "stdout_tail": cp.stdout[-4000:],
        "stderr_tail": cp.stderr[-4000:],
    }


def push_device_runner(args: argparse.Namespace) -> subprocess.CompletedProcess[str]:
    remote = "/data/local/tmp/pdocker-image-pull-crash-safety.sh"
    cmd = [args.adb]
    if args.serial:
        cmd += ["-s", args.serial]
    cmd += ["push", str(DEVICE_RUNNER), remote]
    pushed = run_cmd(cmd, timeout=30)
    if pushed.returncode != 0:
        return pushed
    chmod = [args.adb]
    if args.serial:
        chmod += ["-s", args.serial]
    chmod += ["shell", "chmod", "755", remote]
    return run_cmd(chmod, timeout=10)


def run_device_phase(args: argparse.Namespace, phase: str, token: str) -> subprocess.CompletedProcess[str]:
    remote = "/data/local/tmp/pdocker-image-pull-crash-safety.sh"
    cmd = [args.adb]
    if args.serial:
        cmd += ["-s", args.serial]
    cmd += [
        "shell",
        "sh",
        remote,
        "--package",
        args.package,
        "--image",
        args.image,
        "--token",
        token,
        "--out-dir",
        args.device_out_dir,
        "--phase",
        phase,
    ]
    return run_cmd(cmd, timeout=180 if phase == "restart-and-probe" else 60)


def pull_device_evidence(args: argparse.Namespace, local_dir: Path) -> subprocess.CompletedProcess[str]:
    if local_dir.exists():
        shutil.rmtree(local_dir)
    local_dir.parent.mkdir(parents=True, exist_ok=True)
    cmd = [args.adb]
    if args.serial:
        cmd += ["-s", args.serial]
    cmd += ["pull", args.device_out_dir, str(local_dir)]
    return run_cmd(cmd, timeout=60)


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def relative_or_none(path: Path | None) -> str | None:
    if path is None or not path.exists():
        return None
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def evaluate_device_evidence(local_dir: Path) -> tuple[dict[str, bool | None], list[str], dict[str, str | None]]:
    restart = read_json(local_dir / "restart-summary.json") or {}
    cleanup = read_json(local_dir / "cleanup-summary.json") or {}
    assertions: dict[str, bool | None] = {
        "old_tag_restored": restart.get("old_tag_restored"),
        "pull_stage_pruned": restart.get("pull_stage_pruned"),
        "tmp_layer_pruned": restart.get("tmp_layer_pruned"),
        "partial_layer_pruned": restart.get("partial_layer_pruned"),
        "never_published_tag_rejected": restart.get("never_published_tag_rejected"),
        "restored_tag_inspectable": restart.get("restored_tag_inspectable"),
        "daemon_restarted": restart.get("daemon_restarted"),
        "cleanup_removed_only_scenario_owned_paths": cleanup.get("cleanup_removed_only_scenario_owned_paths"),
    }
    failures = [name for name, value in assertions.items() if value is not True]
    evidence = {
        "device_evidence_dir": relative_or_none(local_dir),
        "prepare_summary": relative_or_none(local_dir / "prepare-summary.json"),
        "kill_summary": relative_or_none(local_dir / "kill-summary.json"),
        "restart_summary": relative_or_none(local_dir / "restart-summary.json"),
        "cleanup_summary": relative_or_none(local_dir / "cleanup-summary.json"),
        "store_listing_before_kill": relative_or_none(local_dir / "store-before-kill.txt"),
        "store_listing_after_restart": relative_or_none(local_dir / "store-after-restart.txt"),
        "image_inspect_after_restart": relative_or_none(local_dir / "inspect-restored.raw"),
        "never_image_inspect_after_restart": relative_or_none(local_dir / "inspect-never.raw"),
        "daemon_log_before_kill": relative_or_none(local_dir / "ps-before-kill.txt"),
        "daemon_log_after_restart": relative_or_none(local_dir / "ps-after-restart.txt"),
        "container_run_after_restart": None,
    }
    return assertions, failures, evidence


def execute_device(args: argparse.Namespace, artifact_path: Path, token: str) -> tuple[str, bool, dict[str, Any], list[str], list[dict[str, Any]]]:
    notes: list[str] = []
    phase_results: list[dict[str, Any]] = []
    pushed = push_device_runner(args)
    phase_results.append(phase_result("push-runner", pushed))
    if pushed.returncode != 0:
        notes.append("Failed to push/chmod the device-side runner.")
        return "failed", False, {}, notes, phase_results

    for phase in PHASES:
        cp = run_device_phase(args, phase, token)
        phase_results.append(phase_result(phase, cp))
        if cp.returncode != 0:
            notes.append(f"Device phase {phase} failed with rc={cp.returncode}.")
            # Continue to pull evidence if possible; cleanup failures also need recording.
            if phase != "cleanup":
                break

    evidence_dir = artifact_path.parent / "image-pull-crash-safety-device"
    pulled = pull_device_evidence(args, evidence_dir)
    phase_results.append(phase_result("pull-evidence", pulled))
    if pulled.returncode != 0:
        notes.append("Failed to pull device evidence directory.")
        return "failed", False, {"device_evidence_dir": None}, notes, phase_results

    assertions, failures, evidence = evaluate_device_evidence(evidence_dir)
    if failures:
        notes.append("Device evidence assertions failed: " + ", ".join(failures))
        return "failed", False, {"assertions": assertions, "evidence": evidence}, notes, phase_results
    notes.append("Concrete synthetic residue kill/restart crash-safety lane passed; live network-pull interruption remains a separate gap.")
    return "passed", True, {"assertions": assertions, "evidence": evidence}, notes, phase_results


def build_artifact(args: argparse.Namespace) -> dict[str, Any]:
    artifact_path = Path(args.artifact).resolve()
    token = safe_token(args.token)
    device, notes = detect_device(args.adb, args.serial)
    status = "planned-gap"
    success = False
    phase_results: list[dict[str, Any]] = []
    assertions: dict[str, bool | None] = {
        "old_tag_restored": None,
        "pull_stage_pruned": None,
        "tmp_layer_pruned": None,
        "partial_layer_pruned": None,
        "never_published_tag_rejected": None,
        "restored_tag_inspectable": None,
        "daemon_restarted": None,
        "cleanup_removed_only_scenario_owned_paths": None,
    }
    evidence = {
        "device_evidence_dir": None,
        "prepare_summary": None,
        "kill_summary": None,
        "restart_summary": None,
        "cleanup_summary": None,
        "store_listing_before_kill": None,
        "store_listing_after_restart": None,
        "image_inspect_after_restart": None,
        "never_image_inspect_after_restart": None,
        "daemon_log_before_kill": None,
        "daemon_log_after_restart": None,
        "container_run_after_restart": None,
    }

    if args.execute_device and device.get("state") != "device":
        status = "blocked"
    elif args.execute_device and device.get("state") == "device":
        status, success, extra, exec_notes, phase_results = execute_device(args, artifact_path, token)
        notes.extend(exec_notes)
        if "assertions" in extra:
            assertions.update(extra["assertions"])
        if "evidence" in extra:
            evidence.update(extra["evidence"])
    elif device.get("state") == "device":
        notes.append("Device is ready, but --execute-device was not requested; keeping planned-gap and command plan only.")

    return {
        "schema_version": 2,
        "scenario_id": SCENARIO_ID,
        "plan_gate": PLAN_GATE,
        "status": status,
        "success": success,
        "generated_at": utc_now(),
        "device": device,
        "inputs": {
            "image": args.image,
            "package": args.package,
            "token": token,
            "device_out_dir": args.device_out_dir,
        },
        "coverage": {
            "residue_recovery": success,
            "daemon_kill_restart": success,
            "engine_negative_probe": success,
            "live_interrupted_network_pull": False,
        },
        "phases": PHASES,
        "phase_results": phase_results,
        "commands": scenario_commands(args.adb, args.serial, args.package, args.image, artifact_path, args.device_out_dir, token),
        "artifact_schema": ARTIFACT_SCHEMA,
        "evidence": evidence,
        "assertions": assertions,
        "negative_expected_conditions": NEGATIVE_EXPECTED_CONDITIONS,
        "cleanup_policy": CLEANUP_POLICY,
        "remaining_gap": REMAINING_GAP,
        "notes": notes,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", default=str(DEFAULT_ARTIFACT), help="JSON artifact to write")
    parser.add_argument("--adb", default="adb", help="adb executable name/path")
    parser.add_argument("--serial", default=None, help="adb serial to target")
    parser.add_argument("--package", default="io.github.ryo100794.pdocker.compat", help="Android package id under test")
    parser.add_argument("--image", default="busybox:latest", help="future live-pull image; synthetic lane does not pull it")
    parser.add_argument("--device-out-dir", default=DEFAULT_DEVICE_OUT, help="device-side evidence directory")
    parser.add_argument("--token", default=None, help="scenario-owned token for deterministic tests")
    parser.add_argument("--execute-device", action="store_true", help="run concrete device kill/restart phases; never fakes success")
    parser.add_argument("--print-schema", action="store_true", help="print the artifact schema and exit")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.print_schema:
        print(json.dumps(ARTIFACT_SCHEMA, indent=2, sort_keys=True))
        return 0

    artifact = build_artifact(args)
    out = Path(args.artifact)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
    print(f"wrote {out}: status={artifact['status']} success={artifact['success']}")
    if args.execute_device:
        return 0 if artifact["success"] else 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

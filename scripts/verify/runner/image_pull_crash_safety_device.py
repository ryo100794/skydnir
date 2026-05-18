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
        "image": "registry reference used by the synthetic residue lane",
        "package": "Android package id",
        "token": "scenario-owned suffix used for all device paths",
        "device_out_dir": "device artifact directory",
        "live_image": "scenario-owned registry reference or isolated fixture for timed live interruption|null",
        "live_image_safe": "boolean safety classification for live_image",
        "live_image_safety_reason": "why live_image is or is not safe for interruption",
        "safe_image_requirements": "rules for accepting a live interruption image reference",
        "live_fixture_owned": "boolean operator assertion that live_image is safe to interrupt",
        "live_interrupt_after_seconds": "delay before daemon kill in the future timed live-pull phase",
        "live_timeout_seconds": "maximum duration for the future live-pull phase",
    },
    "coverage": {
        "residue_recovery": "boolean",
        "daemon_kill_restart": "boolean",
        "engine_negative_probe": "boolean",
        "live_interrupted_network_pull": "boolean",
        "timed_live_interruption_artifact": "boolean",
    },
    "phases": ["prepare-residue", "kill-daemon", "restart-and-probe", "cleanup"],
    "live_pull_interruption": "gated timed live registry pull interruption plan/status/results",
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
        "partial_image_inspect_after_restart": "path|null",
        "partial_image_create_after_restart": "path|null",
        "daemon_log_before_kill": "path|null",
        "daemon_log_after_restart": "path|null",
        "container_run_after_restart": "path|null",
        "post_restart_survivors": "list of scenario-owned interrupted pull/cache paths still present after restart",
        "live_pull_summary": "path|null",
        "live_pull_output": "path|null",
        "live_store_listing_before_kill": "path|null",
        "live_store_listing_after_restart": "path|null",
        "live_image_inspect_after_restart": "path|null",
    },
    "assertions": {
        "old_tag_restored": "boolean|null",
        "pull_stage_pruned": "boolean|null",
        "tmp_layer_pruned": "boolean|null",
        "partial_layer_pruned": "boolean|null",
        "partial_image_pruned_or_rejected": "boolean|null",
        "partial_image_inspect_rejected": "boolean|null",
        "partial_image_create_rejected": "boolean|null",
        "never_published_tag_rejected": "boolean|null",
        "restored_tag_inspectable": "boolean|null",
        "cleanup_removed_only_scenario_owned_paths": "boolean|null",
        "no_partial_or_corrupt_image_cache_survivors": "boolean|null",
        "live_pull_started_before_kill": "boolean|null",
        "live_daemon_killed_and_restarted": "boolean|null",
        "live_partial_tag_not_published": "boolean|null",
        "live_pull_stage_pruned": "boolean|null",
        "live_tmp_layers_pruned": "boolean|null",
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
    "partial image directory with incomplete layers is inspectable after restart",
    "docker run/create succeeds from a partial image or layer cache entry after restart",
    "scenario-owned interrupted pull/cache residue survives in the post-restart store listing",
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
    "Timed live-pull interruption runs only on a ready device when --execute-device, --execute-live-pull-interruption, --live-image, and --live-fixture-owned are all supplied.",
    "Without a ready device or safe scenario-owned/isolated fixture image, the artifact remains fail-closed as planned-gap/blocked and never promotes success.",
    "Container run/create is attempted only for an existing scenario-owned partial local tag so missing public references are not auto-pulled.",
]

PHASES = ["prepare-residue", "kill-daemon", "restart-and-probe", "cleanup"]
LIVE_PULL_PHASE = "timed-live-pull-interruption"

SAFE_LIVE_IMAGE_REQUIREMENTS = [
    "The image reference must be scenario-owned, e.g. it contains pdocker-crash-safety or pdocker-live-pull-fixture.",
    "Or the image reference must point at an isolated local fixture registry such as 127.0.0.1:<port>/...",
    "Common public/user tags such as ubuntu:latest, busybox:latest, alpine:latest, or library/* are rejected.",
    "The operator must also pass --live-fixture-owned before any future live interruption implementation may run.",
]


def classify_live_image_safety(ref: str | None) -> tuple[bool, str]:
    """Return whether a live-pull reference is safe to interrupt.

    The future timed live-pull lane will kill pdockerd while a registry transfer
    is active.  That must never target a user's ordinary image/tag.  Keep this
    classifier deliberately conservative: scenario markers or isolated local
    fixture registries are accepted; broad public tags are not.
    """
    if not ref:
        return False, "missing --live-image"
    normalized = ref.strip().lower()
    if not normalized:
        return False, "empty --live-image"
    unsafe_names = {
        "ubuntu",
        "ubuntu:latest",
        "busybox",
        "busybox:latest",
        "alpine",
        "alpine:latest",
        "debian",
        "debian:latest",
    }
    if normalized in unsafe_names or normalized.startswith("library/"):
        return False, "common public/user image references are not scenario-owned"
    if not re.fullmatch(r"[a-z0-9][a-z0-9._:/@-]{0,255}", normalized):
        return False, "image reference contains characters outside the safe runner allow-list"
    if re.match(r"^(127\.0\.0\.1|localhost|\[::1\])(?::[0-9]+)?/", normalized):
        return True, "isolated local fixture registry"
    if any(marker in normalized for marker in ("pdocker-crash-safety", "pdocker-live-pull-fixture", "pdocker-interrupt-fixture")):
        return True, "scenario-owned fixture marker"
    return False, "image reference lacks a scenario-owned or isolated-fixture marker"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def host_command(adb: str, serial: str | None, *args: str) -> str:
    base = [adb]
    if serial:
        base += ["-s", serial]
    base += list(args)
    return shlex.join(base)


def scenario_commands(
    adb: str,
    serial: str | None,
    package: str,
    image: str,
    artifact: Path,
    device_out: str,
    token: str,
    live_image: str | None = None,
    live_fixture_owned: bool = False,
) -> list[str]:
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
    live_cmd = [
        "python3",
        "scripts/verify/runner/image_pull_crash_safety_device.py",
        "--execute-device",
        "--execute-live-pull-interruption",
        "--live-image",
        live_image or "<scenario-owned-or-isolated-fixture-ref>",
    ]
    if live_fixture_owned:
        live_cmd.append("--live-fixture-owned")
    commands.append(shlex.join(live_cmd))
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
    if phase == LIVE_PULL_PHASE:
        cmd += [
            "--live-image",
            args.live_image or "",
            "--live-interrupt-after-seconds",
            str(args.live_interrupt_after_seconds),
            "--live-timeout-seconds",
            str(args.live_timeout_seconds),
        ]
    timeout = max(int(args.live_timeout_seconds) + 90, 180) if phase == LIVE_PULL_PHASE else (180 if phase == "restart-and-probe" else 60)
    return run_cmd(cmd, timeout=timeout)


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


def post_restart_survivors(local_dir: Path) -> list[str]:
    """Return scenario-owned interrupted pull/cache paths that survived restart.

    The device shell writes boolean assertions, but the host evaluator also
    checks the raw store listing so a buggy device-side summary cannot turn
    stale ``.pull-*``, ``.tmp-*``, or malformed layer residue
    into a pass.  A restored base image is allowed; scenario-owned interrupted
    stages, never-published tags, and incomplete layer/cache entries are not.
    """
    context = read_json(local_dir / "context.json") or {}
    listing_path = local_dir / "store-after-restart.txt"
    try:
        lines = [line.strip() for line in listing_path.read_text().splitlines() if line.strip()]
    except OSError:
        return ["missing:store-after-restart.txt"]

    token = str(context.get("token") or "")
    image_base = str(context.get("image_base") or "")
    never_base = str(context.get("never_base") or "")
    tmp_layer = str(context.get("tmp_layer") or "")
    partial_layer = str(context.get("partial_layer") or "")

    survivors: list[str] = []
    for line in lines:
        name = line.rsplit("/", 1)[-1]
        token_owned = bool(token and token in name)
        forbidden = False
        if token_owned and (".pull-" in name or ".tmp-" in name or ".old-" in name):
            forbidden = True
        if never_base and name == never_base:
            forbidden = True
        if tmp_layer and name.startswith(f"{tmp_layer}.tmp-"):
            forbidden = True
        if partial_layer and name == partial_layer:
            forbidden = True
        if forbidden:
            survivors.append(line)
    return survivors


def evaluate_device_evidence(local_dir: Path) -> tuple[dict[str, bool | None], list[str], dict[str, Any]]:
    restart = read_json(local_dir / "restart-summary.json") or {}
    cleanup = read_json(local_dir / "cleanup-summary.json") or {}
    survivors = post_restart_survivors(local_dir)
    assertions: dict[str, bool | None] = {
        "old_tag_restored": restart.get("old_tag_restored"),
        "pull_stage_pruned": restart.get("pull_stage_pruned"),
        "tmp_layer_pruned": restart.get("tmp_layer_pruned"),
        "partial_layer_pruned": restart.get("partial_layer_pruned"),
        "partial_image_pruned_or_rejected": restart.get("partial_image_pruned_or_rejected"),
        "partial_image_inspect_rejected": restart.get("partial_image_inspect_rejected"),
        "partial_image_create_rejected": restart.get("partial_image_create_rejected"),
        "never_published_tag_rejected": restart.get("never_published_tag_rejected"),
        "restored_tag_inspectable": restart.get("restored_tag_inspectable"),
        "daemon_restarted": restart.get("daemon_restarted"),
        "cleanup_removed_only_scenario_owned_paths": cleanup.get("cleanup_removed_only_scenario_owned_paths"),
        "no_partial_or_corrupt_image_cache_survivors": not survivors,
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
        "partial_image_inspect_after_restart": relative_or_none(local_dir / "inspect-partial.raw"),
        "partial_image_create_after_restart": relative_or_none(local_dir / "create-partial.raw"),
        "daemon_log_before_kill": relative_or_none(local_dir / "ps-before-kill.txt"),
        "daemon_log_after_restart": relative_or_none(local_dir / "ps-after-restart.txt"),
        "container_run_after_restart": relative_or_none(local_dir / "create-partial.raw"),
        "post_restart_survivors": survivors,
    }
    return assertions, failures, evidence


def evaluate_live_pull_evidence(local_dir: Path) -> tuple[dict[str, bool | None], list[str], dict[str, Any], dict[str, Any]]:
    summary = read_json(local_dir / "live-pull-summary.json") or {}
    assertions: dict[str, bool | None] = {
        "live_pull_started_before_kill": summary.get("pull_started_before_kill"),
        "live_daemon_killed_and_restarted": bool(summary.get("daemon_killed")) and bool(summary.get("daemon_restarted")),
        "live_partial_tag_not_published": summary.get("partial_tag_not_published"),
        "live_pull_stage_pruned": summary.get("pull_stage_pruned"),
        "live_tmp_layers_pruned": summary.get("tmp_layers_pruned"),
    }
    failures = [name for name, value in assertions.items() if value is not True]
    evidence = {
        "live_pull_summary": relative_or_none(local_dir / "live-pull-summary.json"),
        "live_pull_output": relative_or_none(local_dir / "live-pull.raw"),
        "live_store_listing_before_kill": relative_or_none(local_dir / "live-store-before-kill.txt"),
        "live_store_listing_after_restart": relative_or_none(local_dir / "live-store-after-restart.txt"),
        "live_image_inspect_after_restart": relative_or_none(local_dir / "live-inspect.raw"),
    }
    return assertions, failures, evidence, summary


def execute_device(args: argparse.Namespace, artifact_path: Path, token: str) -> tuple[str, bool, dict[str, Any], list[str], list[dict[str, Any]]]:
    notes: list[str] = []
    phase_results: list[dict[str, Any]] = []
    pushed = push_device_runner(args)
    phase_results.append(phase_result("push-runner", pushed))
    if pushed.returncode != 0:
        notes.append("Failed to push/chmod the device-side runner.")
        return "failed", False, {}, notes, phase_results

    cleanup_attempted = False
    for phase in PHASES:
        cp = run_device_phase(args, phase, token)
        phase_results.append(phase_result(phase, cp))
        cleanup_attempted = cleanup_attempted or phase == "cleanup"
        if cp.returncode != 0:
            notes.append(f"Device phase {phase} failed with rc={cp.returncode}.")
            # Always try scoped cleanup after evidence-producing phases fail so
            # interrupted verification does not strand scenario-owned residue on
            # shared devices.  Pull evidence afterward even if cleanup fails.
            if phase != "cleanup" and not cleanup_attempted:
                cleanup_cp = run_device_phase(args, "cleanup", token)
                phase_results.append(phase_result("cleanup-after-failure", cleanup_cp))
                cleanup_attempted = True
                if cleanup_cp.returncode != 0:
                    notes.append(f"Scoped cleanup after failed {phase} also failed with rc={cleanup_cp.returncode}.")
            break

    live_requested = bool(args.execute_live_pull_interruption)
    live_safe, live_reason = classify_live_image_safety(args.live_image)
    if live_requested and cleanup_attempted and not any(r["returncode"] != 0 for r in phase_results if r["phase"] in PHASES):
        if not (args.live_image and args.live_fixture_owned and live_safe):
            notes.append(f"Timed live pull interruption was requested but is not safe/runnable: {live_reason}.")
        else:
            cp = run_device_phase(args, LIVE_PULL_PHASE, token)
            phase_results.append(phase_result(LIVE_PULL_PHASE, cp))
            if cp.returncode != 0:
                notes.append(f"Device phase {LIVE_PULL_PHASE} failed with rc={cp.returncode}.")

    evidence_dir = artifact_path.parent / "image-pull-crash-safety-device"
    pulled = pull_device_evidence(args, evidence_dir)
    phase_results.append(phase_result("pull-evidence", pulled))
    if pulled.returncode != 0:
        notes.append("Failed to pull device evidence directory.")
        return "failed", False, {"device_evidence_dir": None}, notes, phase_results

    assertions, failures, evidence = evaluate_device_evidence(evidence_dir)
    live_summary: dict[str, Any] | None = None
    if live_requested and args.live_image and args.live_fixture_owned and live_safe:
        live_assertions, live_failures, live_evidence, live_summary = evaluate_live_pull_evidence(evidence_dir)
        assertions.update(live_assertions)
        evidence.update(live_evidence)
        failures.extend(live_failures)
    elif live_requested:
        failures.append("live_pull_safe_fixture")
    if failures:
        notes.append("Device evidence assertions failed: " + ", ".join(failures))
        extra: dict[str, Any] = {"assertions": assertions, "evidence": evidence}
        if live_summary is not None:
            extra["live_summary"] = live_summary
        return "failed", False, extra, notes, phase_results
    if live_summary is not None:
        notes.append("Concrete synthetic residue and timed live registry-pull interruption lanes passed.")
    else:
        notes.append("Concrete synthetic residue kill/restart crash-safety lane passed; timed live-pull interruption was not requested/runnable.")
    extra = {"assertions": assertions, "evidence": evidence}
    if live_summary is not None:
        extra["live_summary"] = live_summary
    return "passed", True, extra, notes, phase_results



def live_pull_interruption_plan(args: argparse.Namespace) -> tuple[dict[str, Any], list[str]]:
    """Return metadata for the intentionally gated timed live-pull lane.

    This is design scaffolding only.  It must not start a registry pull until a
    later device-side implementation exists and the caller opts in with a
    scenario-owned reference or isolated registry fixture.
    """
    live_image_safe, live_image_safety_reason = classify_live_image_safety(args.live_image)
    missing: list[str] = []
    if not args.execute_live_pull_interruption:
        missing.append("--execute-live-pull-interruption")
    if not args.live_image:
        missing.append("--live-image")
    if not args.live_fixture_owned:
        missing.append("--live-fixture-owned")
    if args.live_image and not live_image_safe:
        missing.append("safe scenario-owned --live-image")

    notes: list[str] = []
    if missing:
        notes.append(
            "Timed live registry pull interruption is planned but not runnable: missing "
            + ", ".join(missing)
            + "."
        )
    else:
        notes.append(
            "Timed live registry pull interruption was explicitly requested with a safe scenario-owned/isolated fixture; "
            "it will run only together with --execute-device on a ready Android device."
        )

    plan = {
        "phase": LIVE_PULL_PHASE,
        "requested": bool(args.execute_live_pull_interruption),
        "runnable": not missing,
        "success": False,
        "status": "ready" if not missing else "planned-gap",
        "live_image": args.live_image,
        "live_image_safe": live_image_safe,
        "live_image_safety_reason": live_image_safety_reason,
        "safe_image_requirements": SAFE_LIVE_IMAGE_REQUIREMENTS,
        "fixture_owned_or_isolated": bool(args.live_fixture_owned),
        "interrupt_after_seconds": args.live_interrupt_after_seconds,
        "timeout_seconds": args.live_timeout_seconds,
        "required_cli": [
            "--execute-device",
            "--execute-live-pull-interruption",
            "--live-image <scenario-owned-or-isolated-fixture-ref>",
            "--live-fixture-owned",
        ],
        "safety_contract": [
            "Do not run against user images or shared mutable tags.",
            "Use a scenario-owned registry reference or an isolated disposable registry fixture.",
            "Reject live_image unless live_image_safe is true.",
            "Capture pull stdout/stderr, daemon process evidence, post-restart store listings, and negative inspect probes before cleanup.",
            "Cleanup may remove only scenario-token-owned tags, stages, layer residues, and fixture artifacts.",
        ],
        "planned_steps": [
            "start timed /images/create for live_image",
            "sleep interrupt_after_seconds",
            "kill pdockerd while transfer is active",
            "restart daemon and wait for socket",
            "assert partial tag/layer residue is pruned and never published",
            "cleanup only scenario-owned fixture paths",
        ],
        "blocked_reason": "missing " + ", ".join(missing) if missing else None,
    }
    return plan, notes

def build_artifact(args: argparse.Namespace) -> dict[str, Any]:
    artifact_path = Path(args.artifact).resolve()
    token = safe_token(args.token)
    device, notes = detect_device(args.adb, args.serial)
    live_plan, live_notes = live_pull_interruption_plan(args)
    live_image_safe = bool(live_plan.get("live_image_safe"))
    live_image_safety_reason = str(live_plan.get("live_image_safety_reason") or "")
    notes.extend(live_notes)
    status = "planned-gap"
    success = False
    phase_results: list[dict[str, Any]] = []
    assertions: dict[str, bool | None] = {
        "old_tag_restored": None,
        "pull_stage_pruned": None,
        "tmp_layer_pruned": None,
        "partial_layer_pruned": None,
        "partial_image_pruned_or_rejected": None,
        "partial_image_inspect_rejected": None,
        "partial_image_create_rejected": None,
        "never_published_tag_rejected": None,
        "restored_tag_inspectable": None,
        "daemon_restarted": None,
        "cleanup_removed_only_scenario_owned_paths": None,
        "no_partial_or_corrupt_image_cache_survivors": None,
        "live_pull_started_before_kill": None,
        "live_daemon_killed_and_restarted": None,
        "live_partial_tag_not_published": None,
        "live_pull_stage_pruned": None,
        "live_tmp_layers_pruned": None,
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
        "partial_image_inspect_after_restart": None,
        "partial_image_create_after_restart": None,
        "daemon_log_before_kill": None,
        "daemon_log_after_restart": None,
        "container_run_after_restart": None,
        "post_restart_survivors": [],
        "live_pull_summary": None,
        "live_pull_output": None,
        "live_store_listing_before_kill": None,
        "live_store_listing_after_restart": None,
        "live_image_inspect_after_restart": None,
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
        if "live_summary" in extra:
            live_plan.update(extra["live_summary"])
            live_plan["requested"] = True
            live_plan["runnable"] = True
            live_plan["success"] = bool(extra["live_summary"].get("success"))
            live_plan["status"] = "passed" if live_plan["success"] else "failed"
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
            "live_image": args.live_image,
            "live_image_safe": live_image_safe,
            "live_image_safety_reason": live_image_safety_reason,
            "safe_image_requirements": SAFE_LIVE_IMAGE_REQUIREMENTS,
            "live_fixture_owned": bool(args.live_fixture_owned),
            "live_interrupt_after_seconds": args.live_interrupt_after_seconds,
            "live_timeout_seconds": args.live_timeout_seconds,
        },
        "coverage": {
            "residue_recovery": success,
            "daemon_kill_restart": success,
            "engine_negative_probe": success,
            "live_interrupted_network_pull": bool(live_plan.get("success")),
            "timed_live_interruption_artifact": bool(evidence.get("live_pull_summary")),
        },
        "phases": PHASES,
        "live_pull_interruption": live_plan,
        "phase_results": phase_results,
        "commands": scenario_commands(
            args.adb,
            args.serial,
            args.package,
            args.image,
            artifact_path,
            args.device_out_dir,
            token,
            args.live_image,
            bool(args.live_fixture_owned),
        ),
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
    parser.add_argument("--execute-device", action="store_true", help="run concrete synthetic-residue device kill/restart phases; never fakes success")
    parser.add_argument("--execute-live-pull-interruption", action="store_true", help="request the future timed live registry pull interruption lane; remains planned-gap until safe device-side implementation exists")
    parser.add_argument("--live-image", default=None, help="scenario-owned registry ref or isolated fixture ref required for timed live-pull interruption")
    parser.add_argument("--live-fixture-owned", action="store_true", help="acknowledge --live-image is scenario-owned or isolated and safe to interrupt/cleanup")
    parser.add_argument("--live-interrupt-after-seconds", type=float, default=3.0, help="planned delay before killing pdockerd during future live pull")
    parser.add_argument("--live-timeout-seconds", type=int, default=120, help="planned timeout for future live pull interruption phase")
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

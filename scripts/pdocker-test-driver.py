#!/usr/bin/env python3
"""Canonical pdocker test driver and artifact manifest writer."""

from __future__ import annotations

import argparse
import datetime as _dt
import glob
import hashlib
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "tests" / "test_driver_manifest.json"


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path}: invalid JSON: {exc}") from exc


def git_value(*args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", *args],
            cwd=ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def artifact_records(patterns: list[str], producer: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for pattern in patterns:
        matches = [Path(p) for p in glob.glob(str(ROOT / pattern), recursive=True)]
        if not matches and (ROOT / pattern).exists():
            matches = [ROOT / pattern]
        for path in sorted(matches):
            if path in seen or not path.is_file():
                continue
            seen.add(path)
            rel = path.relative_to(ROOT).as_posix()
            stat = path.stat()
            records.append(
                {
                    "path": rel,
                    "size": stat.st_size,
                    "sha256": sha256_file(path),
                    "producer": producer,
                }
            )
    return records


def write_summary(run_manifest: dict[str, Any], path: Path) -> None:
    lines = [
        f"# pdocker Test Run {run_manifest['run_id']}",
        "",
        f"- Status: `{run_manifest['status']}`",
        f"- Git: `{run_manifest['git'].get('commit', '')}`",
        f"- Branch: `{run_manifest['git'].get('branch', '')}`",
        f"- Lanes: `{', '.join(run_manifest.get('lanes', []))}`",
        f"- Commands: `{len(run_manifest.get('commands', []))}`",
        f"- Artifacts: `{len(run_manifest.get('artifacts', []))}`",
        "",
        "## Commands",
        "",
        "| Lane | Command | Status | Seconds | Log |",
        "|---|---|---:|---:|---|",
    ]
    for item in run_manifest.get("commands", []):
        lines.append(
            "| {lane} | `{command}` | {status} | {duration} | `{log}` |".format(
                lane=item.get("lane", ""),
                command=str(item.get("command", "")).replace("|", "\\|"),
                status=item.get("status", ""),
                duration=item.get("duration_seconds", 0),
                log=item.get("log", ""),
            )
        )
    lines.extend(["", "## Artifacts", ""])
    for artifact in run_manifest.get("artifacts", []):
        lines.append(
            f"- `{artifact.get('path')}` ({artifact.get('size')} bytes, sha256 `{artifact.get('sha256')}`)"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def export_to_documents(run_manifest: dict[str, Any], manifest_out: Path, summary_out: Path, documents_out: str) -> dict[str, Any] | None:
    if not documents_out:
        return None
    root = Path(documents_out).expanduser()
    if not root.is_absolute():
        root = ROOT / root
    dest = root / "pdocker" / "test-runs" / run_manifest["run_id"]
    artifacts_dir = dest / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(manifest_out, dest / "manifest.json")
    shutil.copy2(summary_out, dest / "summary.md")
    copied = []
    for artifact in run_manifest.get("artifacts", []):
        src = ROOT / artifact["path"]
        if not src.is_file():
            continue
        target = artifacts_dir / artifact["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
        copied.append(str(target.relative_to(root)))
    latest = root / "pdocker" / "test-runs" / "latest"
    latest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(manifest_out, latest / "manifest.json")
    shutil.copy2(summary_out, latest / "summary.md")
    return {
        "root": str(root),
        "run_dir": str(dest),
        "latest_dir": str(latest),
        "copied_artifacts": copied,
    }


def command_label(command: dict[str, Any]) -> str:
    if "argv" in command:
        return shlex.join(str(part) for part in command["argv"])
    return str(command.get("shell", ""))


def run_command(command: dict[str, Any], log_path: Path) -> dict[str, Any]:
    env = os.environ.copy()
    env.update({str(k): str(v) for k, v in command.get("env", {}).items()})
    started = time.time()
    label = command_label(command)
    with log_path.open("wb") as log:
        log.write(f"$ {label}\n".encode())
        log.flush()
        if "argv" in command:
            proc = subprocess.Popen(
                [str(part) for part in command["argv"]],
                cwd=ROOT,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
        else:
            proc = subprocess.Popen(
                str(command["shell"]),
                cwd=ROOT,
                env=env,
                shell=True,
                executable="/bin/bash",
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
        assert proc.stdout is not None
        for chunk in iter(lambda: proc.stdout.readline(), b""):
            sys.stdout.buffer.write(chunk)
            sys.stdout.buffer.flush()
            log.write(chunk)
            log.flush()
        rc = proc.wait()
    ended = time.time()
    return {
        "id": command["id"],
        "command": label,
        "exit_code": rc,
        "status": "pass" if rc == 0 else "fail",
        "started_at": _dt.datetime.fromtimestamp(started, _dt.UTC).isoformat(),
        "ended_at": _dt.datetime.fromtimestamp(ended, _dt.UTC).isoformat(),
        "duration_seconds": round(ended - started, 3),
        "log": log_path.relative_to(ROOT).as_posix(),
        "artifacts": artifact_records(command.get("artifacts", []), command["id"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--lane", action="append", dest="lanes", help="Lane to run. Can be repeated.")
    parser.add_argument("--list", action="store_true", help="List available lanes.")
    parser.add_argument("--run-id", help="Stable run identifier for reproducible build sets.")
    parser.add_argument("--continue-on-fail", action="store_true", help="Run remaining commands after a failure.")
    parser.add_argument(
        "--documents-out",
        default=os.environ.get("PDOCKER_TEST_DOCUMENTS_OUT", ""),
        help="Directory that represents the user Documents exchange folder. Copies manifest, summary, and referenced artifacts under pdocker/test-runs/.",
    )
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    if not manifest_path.is_absolute():
        manifest_path = ROOT / manifest_path
    manifest = load_json(manifest_path)
    lanes = manifest.get("lanes") or {}
    if args.list:
        for name, lane in lanes.items():
            print(f"{name}\t{lane.get('description', '')}")
        return 0

    selected = args.lanes or ["host-smoke"]
    missing = [lane for lane in selected if lane not in lanes]
    if missing:
        raise SystemExit(f"unknown lane(s): {', '.join(missing)}")

    now = _dt.datetime.now(_dt.UTC)
    short_sha = git_value("rev-parse", "--short", "HEAD") or "nogit"
    run_id = args.run_id or f"{now.strftime('%Y%m%dT%H%M%SZ')}-{short_sha}-{'-'.join(selected)}"
    run_dir = ROOT / manifest.get("run_directory", "docs/test/runs") / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    status = "pass"
    for lane_name in selected:
        lane = lanes[lane_name]
        print(f"\n== lane: {lane_name} ==")
        for index, command in enumerate(lane.get("commands", []), start=1):
            log_path = run_dir / f"{index:03d}-{lane_name}-{command['id']}.log"
            print(f"\n==> {command_label(command)}")
            result = run_command(command, log_path)
            result["lane"] = lane_name
            results.append(result)
            if result["exit_code"] != 0:
                status = "fail"
                if not args.continue_on_fail:
                    break
        if status == "fail" and not args.continue_on_fail:
            break

    run_manifest = {
        "schema": manifest.get("schema", "pdocker.test-driver.v1") + ".run",
        "run_id": run_id,
        "status": status,
        "started_at": now.isoformat(),
        "ended_at": _dt.datetime.now(_dt.UTC).isoformat(),
        "lanes": selected,
        "driver": "scripts/pdocker-test-driver.py",
        "manifest": manifest_path.relative_to(ROOT).as_posix(),
        "git": {
            "commit": git_value("rev-parse", "HEAD"),
            "branch": git_value("rev-parse", "--abbrev-ref", "HEAD"),
            "dirty": bool(git_value("status", "--porcelain")),
        },
        "environment": {
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "android_flavor": os.environ.get("SKYDNIR_ANDROID_FLAVOR") or os.environ.get("PDOCKER_ANDROID_FLAVOR", "compat"),
            "adb_serial": os.environ.get("ADB_SERIAL", ""),
        },
        "commands": results,
        "artifacts": [artifact for result in results for artifact in result.get("artifacts", [])],
    }
    manifest_out = run_dir / "manifest.json"
    manifest_out.write_text(json.dumps(run_manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary_out = run_dir / "summary.md"
    write_summary(run_manifest, summary_out)
    documents_export = export_to_documents(run_manifest, manifest_out, summary_out, args.documents_out)
    if documents_export:
        run_manifest["documents_export"] = documents_export
        manifest_out.write_text(json.dumps(run_manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        write_summary(run_manifest, summary_out)
        export_to_documents(run_manifest, manifest_out, summary_out, args.documents_out)
    latest = ROOT / manifest.get("artifact_manifest", "docs/test/test-run-latest.json")
    latest.parent.mkdir(parents=True, exist_ok=True)
    latest.write_text(json.dumps(run_manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"\npdocker-test-driver: {status.upper()} {run_id}")
    print(f"artifact manifest: {latest.relative_to(ROOT)}")
    if documents_export:
        print(f"documents export: {documents_export['latest_dir']}")
    return 0 if status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

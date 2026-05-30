#!/usr/bin/env python3
"""Deterministic device workflow for the llama.cpp Q6_K GPU bridge gate.

The purpose of this script is to avoid ad-hoc cut-and-try device operation.  It
always runs the same layers in order:

1. local contract checks that must pass before touching the device;
2. low-impact device readiness;
3. guarded llama GPU compare only when readiness is green;
4. artifact classification with the Q6_K workgroup acceptance gate;
5. one workflow manifest that records commands, exit codes, and artifact paths.

It does not modify llama.cpp, the llama Dockerfile, the model, or prompt probes.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ENV_MANIFEST = ROOT / "scripts" / "llama-gpu-env-manifest.json"


def load_q6_required_env_overlay() -> dict[str, str]:
    data = load_json(ENV_MANIFEST)
    if data.get("schema") != "pdocker.llama.gpu.env-manifest.v1":
        raise ValueError(f"unsupported llama GPU env manifest: {ENV_MANIFEST}")
    overlay = data.get("q6_required_env_overlay")
    if not isinstance(overlay, dict) or not overlay:
        raise ValueError("llama GPU env manifest is missing q6_required_env_overlay")
    result: dict[str, str] = {}
    for key, value in overlay.items():
        if not isinstance(key, str) or not key or not isinstance(value, str):
            raise ValueError("invalid q6_required_env_overlay entry")
        result[key] = value
    return result


def git_capture() -> dict[str, Any]:
    def run_git(*args: str) -> str:
        proc = subprocess.run(
            ["git", *args],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return proc.stdout.strip()

    return {
        "commit": run_git("rev-parse", "--short=12", "HEAD"),
        "branch": run_git("branch", "--show-current"),
        "log_oneline_5": run_git("log", "--oneline", "-5").splitlines(),
        "status_short": run_git("status", "--short").splitlines(),
    }


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def run_step(
    step_id: str,
    argv: list[str],
    env: dict[str, str],
    dry_run: bool,
    stdout_path: Path | None = None,
) -> dict[str, Any]:
    started = time.time()
    label = shlex.join(argv)
    if dry_run:
        record = {
            "id": step_id,
            "argv": argv,
            "command": label,
            "exit_code": 0,
            "status": "dry-run",
            "started_at": dt.datetime.fromtimestamp(started, dt.UTC).isoformat(),
            "ended_at": dt.datetime.fromtimestamp(started, dt.UTC).isoformat(),
            "duration_seconds": 0.0,
            "stdout_tail": "",
        }
        if stdout_path is not None:
            record["stdout_path"] = rel(stdout_path)
            record["stdout_size"] = 0
        return record
    proc = subprocess.run(
        argv,
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    ended = time.time()
    record = {
        "id": step_id,
        "argv": argv,
        "command": label,
        "exit_code": proc.returncode,
        "status": "pass" if proc.returncode == 0 else "fail",
        "started_at": dt.datetime.fromtimestamp(started, dt.UTC).isoformat(),
        "ended_at": dt.datetime.fromtimestamp(ended, dt.UTC).isoformat(),
        "duration_seconds": round(ended - started, 3),
        "stdout_tail": proc.stdout[-8000:],
    }
    if stdout_path is not None:
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stdout_path.write_text(proc.stdout, encoding="utf-8")
        record["stdout_path"] = rel(stdout_path)
        record["stdout_size"] = len(proc.stdout.encode("utf-8"))
    return record


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def extract_json_object(text: str) -> dict[str, Any]:
    """Return the first JSON object embedded in command output.

    The verifier normally prints a single JSON document, but future diagnostics
    may add log lines before or after it.  The workflow must not parse only the
    tail of stdout: long verifier output can truncate the opening brace and
    silently erase the classification from the manifest.
    """

    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return {}


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", default=os.environ.get("ANDROID_SERIAL", ""))
    parser.add_argument("--out", default="docs/test/llama-gpu-workgroup3d-ngl1-latest.json")
    parser.add_argument("--readiness-out", default="docs/test/llama-gpu-device-readiness-latest.json")
    parser.add_argument("--manifest-out", default="docs/test/llama-gpu-q6k-workflow-latest.json")
    parser.add_argument("--cpu-tps", default="0.04702448956650603")
    parser.add_argument("--gpu-layers", default="1")
    parser.add_argument("--gpu-ctx", default="512")
    parser.add_argument("--predict", default="4")
    parser.add_argument("--repeat", default="1")
    parser.add_argument("--wait-memory-sec", default="600")
    parser.add_argument("--skip-local-checks", action="store_true")
    parser.add_argument("--allow-memory-blocker", action="store_true", default=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    env = os.environ.copy()
    if args.serial:
        env["ANDROID_SERIAL"] = args.serial

    out = ROOT / args.out
    readiness_out = ROOT / args.readiness_out
    manifest_out = ROOT / args.manifest_out
    manifest: dict[str, Any] = {
        "schema": "pdocker.llama.gpu.q6k-workflow.v1",
        "timestamp_utc": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "policy": {
            "llama_cpp_modified": False,
            "dockerfile_modified": False,
            "model_or_prompt_modified": False,
            "browser_force_stop_allowed": False,
            "benchmark_requires_correctness": True,
        },
        "git": git_capture(),
        "device": {"serial": args.serial},
        "artifacts": {
            "readiness": rel(readiness_out),
            "compare": rel(out),
            "manifest": rel(manifest_out),
        },
        "steps": [],
        "status": "running",
        "next_action": "",
    }

    try:
        q6_required_env_overlay = load_q6_required_env_overlay()
    except ValueError as exc:
        manifest["status"] = "blocked-env-manifest"
        manifest["next_action"] = str(exc)
        write_manifest(manifest_out, manifest)
        print(json.dumps(manifest, indent=2, sort_keys=True))
        return 30
    manifest["q6_required_env_overlay"] = q6_required_env_overlay
    manifest["q6_compare_env"] = {
        key: q6_required_env_overlay[key] for key in sorted(q6_required_env_overlay)
    }

    if not args.skip_local_checks:
        local_step = run_step(
            "local-contract-checks",
            [
                "python3",
                "-m",
                "unittest",
                "tests.test_gpu_abi_contract",
                "tests.test_llama_gpu_artifact_verifier",
                "tests.test_llama_gpu_readiness_contract",
                "tests.test_llama_gpu_q6k_workflow",
            ],
            env,
            args.dry_run,
        )
        manifest["steps"].append(local_step)
        if local_step["exit_code"] != 0:
            manifest["status"] = "blocked-local-contract"
            manifest["next_action"] = (
                "Fix the local contract failure before running device GPU validation; "
                "do not begin cut-and-try device runs."
            )
            write_manifest(manifest_out, manifest)
            print(json.dumps(manifest, indent=2, sort_keys=True))
            return 10

    readiness_step = run_step(
        "readiness",
        [
            "bash",
            "scripts/android-llama-gpu-readiness.sh",
            "--out",
            rel(readiness_out),
        ],
        env,
        args.dry_run,
    )
    manifest["steps"].append(readiness_step)
    readiness = load_json(readiness_out)
    ready = bool(readiness.get("ready")) if not args.dry_run else False
    if readiness_step["exit_code"] != 0 or not ready:
        manifest["status"] = "blocked-memory" if not args.dry_run else "dry-run"
        manifest["readiness"] = readiness
        manifest["next_action"] = (
            "Wait for readiness.ready=true, then rerun this workflow. "
            "Do not interpret this as GPU correctness evidence."
        )
        write_manifest(manifest_out, manifest)
        print(json.dumps(manifest, indent=2, sort_keys=True))
        return 0 if args.allow_memory_blocker or args.dry_run else 20

    compare_env = env.copy()
    compare_env.update(q6_required_env_overlay)
    compare_env.update(
        {
            "PDOCKER_LLAMA_WAIT_FOR_MEMORY_SEC": str(args.wait_memory_sec),
            "PDOCKER_LLAMA_COMPARE_OUT": rel(out),
        }
    )
    compare_step = run_step(
        "compare-ngl1-q6k-workgroup",
        [
            "bash",
            "scripts/android-llama-gpu-compare.sh",
            "--gpu-only",
            "--cpu-tps",
            str(args.cpu_tps),
            "--gpu-ctx",
            str(args.gpu_ctx),
            "--gpu-layers",
            str(args.gpu_layers),
            "--predict",
            str(args.predict),
            "--repeat",
            str(args.repeat),
            "--out",
            rel(out),
        ],
        compare_env,
        args.dry_run,
    )
    manifest["steps"].append(compare_step)

    verify_stdout = manifest_out.with_suffix(".verifier.stdout")
    verify_step = run_step(
        "verify-q6k-workgroup-artifact",
        [
            "python3",
            "scripts/verify-llama-gpu-artifact.py",
            rel(out),
            "--require-q6-workgroup-clear",
        ],
        env,
        args.dry_run,
        stdout_path=verify_stdout,
    )
    manifest["steps"].append(verify_step)
    classification = extract_json_object(verify_stdout.read_text(encoding="utf-8")) if verify_stdout.is_file() else {}
    if not classification and verify_step.get("stdout_tail"):
        classification = extract_json_object(verify_step["stdout_tail"])
    manifest["classification"] = classification
    manifest["status"] = "pass" if compare_step["exit_code"] == 0 and verify_step["exit_code"] == 0 else "fail"
    manifest["next_action"] = classification.get("next_action") or (
        "Inspect compare artifact and verifier output; do not change Dockerfile/model/prompt."
    )
    write_manifest(manifest_out, manifest)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0 if manifest["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

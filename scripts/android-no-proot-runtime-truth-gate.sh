#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/docs/test/no-proot-runtime-truth-latest.json"
MODE="host-probe"

usage() {
  cat <<EOF
Usage: $0 [--host-probe] [--out PATH]

Writes a non-promoting no-PRoot/direct runtime truth artifact. The current gate
is a host-contract probe of pdockerd's Android direct backend with a direct
executor helper that probes successfully but advertises process-exec=0. It proves
that docker run/start, docker exec, and Dockerfile RUN fail closed with runtime
capability diagnostics, health never becomes healthy, and port mappings remain
planned/inactive rather than active.

Options:
  --host-probe   run the host-side pdockerd contract probe (default)
  --out PATH     artifact path (default: docs/test/no-proot-runtime-truth-latest.json)
  -h, --help     show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host-probe) MODE="host-probe" ;;
    --out)
      [[ $# -ge 2 ]] || { echo "--out requires a path" >&2; exit 2; }
      OUT="$2"
      shift
      ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

if [[ "$MODE" != "host-probe" ]]; then
  echo "unsupported mode: $MODE" >&2
  exit 2
fi

mkdir -p "$(dirname "$OUT")"
python3 - "$ROOT" "$OUT" <<'PY'
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import socket
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

root = Path(sys.argv[1])
out = Path(sys.argv[2])
pdockerd_path = root / "docker-proot-setup" / "bin" / "pdockerd"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def load_pdockerd(home: Path, helper: Path):
    env = os.environ.copy()
    env.update({
        "PDOCKER_HOME": str(home),
        "PDOCKER_TMP_DIR": str(home / "tmp"),
        "PDOCKER_RUNTIME_BACKEND": "no-proot",
        "PDOCKER_DIRECT_EXECUTOR": str(helper),
        "PDOCKER_RUNTIME_PREFLIGHT": "0",
        "LD_LIBRARY_PATH": str((root / "docker-proot-setup" / "lib").resolve()),
    })
    old_env = os.environ.copy()
    os.environ.clear()
    os.environ.update(env)
    try:
        module_name = f"pdockerd_no_proot_truth_{os.getpid()}_{int(time.time() * 1000)}"
        loader = importlib.machinery.SourceFileLoader(module_name, str(pdockerd_path))
        spec = importlib.util.spec_from_loader(module_name, loader)
        if spec is None or spec.loader is None:
            raise RuntimeError("failed to create pdockerd import spec")
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        return mod
    finally:
        os.environ.clear()
        os.environ.update(old_env)


def seed_legacy_image(mod: Any, image: str) -> None:
    img_dir = Path(mod.image_dir(mod.normalize_image(image)))
    rootfs = img_dir / "rootfs"
    (rootfs / "bin").mkdir(parents=True, exist_ok=True)
    (rootfs / "bin" / "sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (img_dir / "config.json").write_text(json.dumps({"config": {"Env": []}}), encoding="utf-8")
    (img_dir / "image_ref").write_text(mod.normalize_image(image), encoding="utf-8")
    mod._save_image_manifest(str(img_dir), [], {"config": {"Env": []}})


def diag_has_capability(text: str) -> bool:
    lower = text.lower()
    return any(
        needle in lower
        for needle in (
            "process-exec=1",
            "no-proot/direct",
            "direct android executor",
            "cannot execute container processes",
            "real container process executor",
            "runtimebackend executor implementation",
        )
    )


def op_record(*, diagnostic: str, exit_code: int = 126) -> dict[str, Any]:
    return {
        "attempted": True,
        "success": False,
        "exit_code": exit_code,
        "capability_error": diag_has_capability(diagnostic),
        "forbidden_success_claim": False,
        "diagnostic": diagnostic,
    }


with tempfile.TemporaryDirectory(prefix="pdocker-no-proot-truth-") as td:
    home = Path(td) / "home"
    helper = Path(td) / "pdocker-direct-helper"
    helper.write_text(
        "#!/bin/sh\n"
        "if [ \"${1:-}\" = \"--pdocker-direct-probe\" ]; then\n"
        "  echo pdocker-direct-executor:1\n"
        "  echo process-exec=0\n"
        "  exit 0\n"
        "fi\n"
        "echo 'process execution is intentionally unavailable in this truth gate' >&2\n"
        "exit 126\n",
        encoding="utf-8",
    )
    helper.chmod(0o755)
    mod = load_pdockerd(home, helper)
    seed_legacy_image(mod, "ubuntu:22.04")

    probe = mod.direct_executor_probe()

    def sanitize(value: Any) -> Any:
        if isinstance(value, str):
            return value.replace(str(helper), "<truth-gate-helper>").replace(str(home), "<truth-gate-home>")
        if isinstance(value, list):
            return [sanitize(item) for item in value]
        if isinstance(value, dict):
            return {key: sanitize(item) for key, item in value.items()}
        return value

    process_msg = sanitize(mod.runtime_process_unavailable_message())

    port = free_port()
    run_state = mod.create_container(
        {
            "Image": "ubuntu:22.04",
            "Cmd": ["/bin/sh", "-c", "echo should-not-run"],
            "ExposedPorts": {"8080/tcp": {}},
            "HostConfig": {
                "PortBindings": {"8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(port)}]},
            },
            "Healthcheck": {
                "Test": ["CMD-SHELL", "echo should-not-be-healthy"],
                "Interval": 1000000000,
                "Timeout": 1000000000,
                "Retries": 1,
            },
            "Labels": {
                "com.docker.compose.project": "no-proot-truth",
                "com.docker.compose.service": "web",
            },
        },
        name="no-proot-truth-web-1",
    )
    planned_before_start = (run_state.get("PdockerNetwork") or {}).get("PortMappingStatus") or []

    try:
        mod.start_container(run_state["Id"])
        run_diag = "unexpected success"
    except RuntimeError as exc:
        run_diag = sanitize(str(exc))
    run_after = mod.load_container_state(run_state["Id"]) or run_state

    try:
        mod.exec_in_container(run_state["Id"], ["/bin/sh", "-c", "echo should-not-exec"])
        exec_diag = "unexpected success"
    except RuntimeError as exc:
        exec_diag = sanitize(str(exc))

    ctx = Path(td) / "ctx"
    ctx.mkdir()
    dockerfile = ctx / "Dockerfile"
    dockerfile.write_text("FROM ubuntu:22.04\nRUN echo should-not-be-faked\n", encoding="utf-8")
    build_output: list[str] = []
    build_result = mod.execute_dockerfile_build(
        str(dockerfile), str(ctx), "local/no-proot-truth:latest", {}, build_output.append
    )
    build_diag = sanitize("\n".join(build_output) or ("unexpected success" if build_result is not None else ""))

    inactive_control = json.loads(json.dumps(run_after))
    inactive_control.setdefault("State", {})["Running"] = True
    inactive_control["State"]["Status"] = "running"
    inactive_control["State"]["PdockerKnownPids"] = []
    inactive_control["State"]["Pid"] = 0
    mod._refresh_port_mapping_status(inactive_control, peer_states=[])
    inactive_status = (inactive_control.get("PdockerNetwork") or {}).get("PortMappingStatus") or []

    final_state = run_after.get("State") or {}
    health = final_state.get("Health") or {}
    health_log = health.get("Log") or []
    port_after_start = (run_after.get("PdockerNetwork") or {}).get("PortMappingStatus") or []
    status_cases: list[dict[str, Any]] = []
    for case, rows in (
        ("created-before-start", planned_before_start),
        ("failed-start", port_after_start),
        ("metadata-only-running-control", inactive_status),
    ):
        for row in rows:
            status_cases.append({
                "case": case,
                "HostIp": row.get("HostIp"),
                "HostPort": row.get("HostPort"),
                "Protocol": row.get("Protocol"),
                "State": row.get("State"),
                "Active": row.get("Active"),
                "Evidence": row.get("Evidence") or [],
                "Message": row.get("Message"),
            })

    counts = {"Active": 0, "Planned": 0, "Inactive": 0, "Conflict": 0}
    for row in status_cases:
        state_name = str(row.get("State") or "").lower()
        if state_name == "active":
            counts["Active"] += 1
        elif state_name == "planned":
            counts["Planned"] += 1
        elif state_name == "inactive":
            counts["Inactive"] += 1
        elif state_name == "conflict":
            counts["Conflict"] += 1

    artifact = {
        "schema": "pdocker.android.no-proot-runtime-truth.v1",
        "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "status": "planned-gap",
        "success": False,
        "reason": "no-PRoot/direct executor probe lacks process-exec=1; runtime operations must fail closed instead of faking Docker success",
        "promotion": "non-promoting runtime truth gate until real no-PRoot process execution is implemented",
        "subject": {
            "runtime_backend": mod.runtime_backend_kind(),
            "driver": mod.runtime_driver_name(),
            "operating_system": mod.runtime_operating_system(),
        },
        "direct_executor_probe": {
            "helper": "<truth-gate-helper>",
            "probe_ok": bool(probe.get("probe_ok")),
            "process_exec": bool(probe.get("process_exec")),
            "advertises_process_exec_1": "process-exec=1" in str(probe.get("output") or ""),
            "output": sanitize(probe.get("output") or ""),
            "error": sanitize(probe.get("error") or ""),
            "diagnostic": process_msg,
        },
        "operations": {
            "docker_run": op_record(diagnostic=run_diag, exit_code=int(final_state.get("ExitCode") or 126)),
            "docker_exec": op_record(diagnostic=exec_diag, exit_code=126),
            "dockerfile_run": op_record(diagnostic=build_diag, exit_code=1),
        },
        "health": {
            "final_status": health.get("Status") or "absent",
            "running": bool(final_state.get("Running")),
            "cannot_become_healthy": (health.get("Status") != "healthy" and not final_state.get("Running")),
            "has_healthy_claim": health.get("Status") == "healthy",
            "failing_streak": health.get("FailingStreak", 0),
            "log": sanitize(health_log),
            "diagnostic": sanitize(health_log[-1].get("Output") if health_log and isinstance(health_log[-1], dict) else run_diag),
        },
        "ports": {
            "active_count": counts["Active"],
            "planned_or_inactive_only": counts["Active"] == 0 and counts["Conflict"] == 0 and all(
                str(row.get("State") or "").lower() in {"planned", "inactive"} and row.get("Active") is False
                for row in status_cases
            ),
            "summary": counts,
            "status_cases": status_cases,
        },
        "evidence": {
            "mode": "host-contract-probe",
            "source": "pdockerd imported with PDOCKER_RUNTIME_BACKEND=no-proot and a process-exec=0 direct helper",
            "script": "scripts/android-no-proot-runtime-truth-gate.sh",
            "verifier": "scripts/verify-no-proot-runtime-truth-artifact.py",
            "container_id": run_state.get("Id"),
            "dockerfile_result": build_result,
        },
    }

out.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(out)
PY

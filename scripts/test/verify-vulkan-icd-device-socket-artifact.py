#!/usr/bin/env python3
"""Verify Vulkan ICD device-socket gate artifacts.

By default this is a promotion verifier: success:false artifacts fail. Use
--allow-planned-skip when validating that a disconnected/missing-prerequisite
run still produced a structured non-promoting artifact.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SCHEMA = "skydnir.vulkan.icd.device-socket.v1"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"verify-vulkan-icd-device-socket-artifact: FAIL: {message}")


def load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SystemExit(f"verify-vulkan-icd-device-socket-artifact: FAIL: missing artifact: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"verify-vulkan-icd-device-socket-artifact: FAIL: invalid JSON: {exc}") from exc
    require(isinstance(data, dict), "artifact root must be an object")
    return data


def verify_common(data: dict) -> None:
    require(data.get("schema") == SCHEMA, "schema mismatch")
    require(data.get("uses_host_vulkan_loader") is False, "host Vulkan loader must not be accepted as evidence")
    require(data.get("required_icd_json") == "/etc/vulkan/icd.d/pdocker-android.json", "unexpected ICD JSON path")
    require(data.get("required_socket") == "/run/pdocker-gpu/pdocker-gpu.sock", "unexpected guest socket path")
    require(data.get("app_socket") == "files/pdocker-runtime/gpu/pdocker-gpu.sock", "unexpected app socket path")
    checks = data.get("checks")
    require(isinstance(checks, dict), "checks must be an object")
    for key in ["adb_devices", "app_socket", "direct_preflight", "docker_ps", "guest_prereq", "guest_run_stdout", "guest_run_stderr"]:
        require(key in checks, f"missing checks.{key}")


def verify_success(data: dict) -> None:
    checks = data["checks"]
    require(data.get("success") is True, f"artifact is not a pass: {data.get('reason')}")
    require(bool(str(data.get("adb_serial", "")).strip()), "missing adb serial")
    require(bool(str(data.get("container", "")).strip()), "missing container id/name")
    require("present" in str(checks.get("app_socket", "")), "app socket was not observed")
    direct = str(checks.get("direct_preflight", ""))
    require('"backend_impl":"android_vulkan"' in direct, "direct preflight lacks Android Vulkan backend evidence")
    require('"valid":true' in direct, "direct preflight is not valid")
    stdout = str(checks.get("guest_run_stdout", ""))
    stderr = str(checks.get("guest_run_stderr", ""))
    match = re.search(r"storageImageMaxErr=([0-9]+)", stdout)
    require(match is not None, "guest stdout missing storageImageMaxErr")
    require(int(match.group(1)) <= 1, "storageImageMaxErr exceeds tolerance")
    require("pdocker-vulkan-icd" in stderr, "guest stderr lacks ICD bridge trace")
    require("fallback" not in direct.lower() and "fallback" not in stdout.lower() and "fallback" not in stderr.lower(), "fallback evidence is not accepted")


def verify_planned_skip(data: dict) -> None:
    require(data.get("success") is False, "planned-skip mode expected success:false")
    require(bool(str(data.get("reason", "")).strip()), "planned-skip artifact must include reason")
    require(isinstance(data.get("exit_code"), int) and data["exit_code"] != 0, "planned-skip artifact must keep nonzero exit_code")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--allow-planned-skip", action="store_true")
    args = parser.parse_args(argv)
    data = load(args.artifact)
    verify_common(data)
    if args.allow_planned_skip and data.get("success") is False:
        verify_planned_skip(data)
    else:
        verify_success(data)
    print("verify-vulkan-icd-device-socket-artifact: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

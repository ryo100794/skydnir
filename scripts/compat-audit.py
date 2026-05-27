#!/usr/bin/env python3
"""Reusable Docker-compatibility audit for pdocker-android.

The default mode is intentionally offline and repeatable:
  - inspect pdockerd's Engine API surface
  - start pdockerd and exercise basic HTTP-over-UDS protocol behavior
  - inspect APK/native payloads for expected exchange/protocol features
  - validate the maintained third-party license inventory

Use --backend-quick to chain the shorter backend container smoke, or --full for
the heavyweight docker-proot-setup/scripts/verify_all.sh regression.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "docker-proot-setup"
PDOCKERD = BACKEND / "bin" / "pdockerd"
FLAVOR_ENV = "SKYDNIR_ANDROID_FLAVOR"
LEGACY_FLAVOR_ENV = "PDOCKER_ANDROID_FLAVOR"
FLAVOR_WAS_EXPLICIT = FLAVOR_ENV in os.environ or LEGACY_FLAVOR_ENV in os.environ
FLAVOR = os.environ.get(FLAVOR_ENV) or os.environ.get(LEGACY_FLAVOR_ENV, "compat")
APK_BY_FLAVOR = {
    "compat": ROOT / "app" / "build" / "outputs" / "apk" / "compat" / "debug" / "app-compat-debug.apk",
    "modern": ROOT / "app" / "build" / "outputs" / "apk" / "modern" / "debug" / "app-modern-debug.apk",
}
APK = APK_BY_FLAVOR.get(FLAVOR)
LICENSE_DOC = ROOT / "THIRD_PARTY_NOTICES.md"
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""


def run(cmd: list[str], cwd: Path = ROOT, env: dict[str, str] | None = None,
        timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )


def stop_process_and_collect_stderr(proc: subprocess.Popen, timeout: int = 5) -> str:
    if proc.poll() is None:
        proc.terminate()
    try:
        _, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        _, stderr = proc.communicate(timeout=timeout)
    return stderr or ""


def uds_request(sock_path: Path, method: str, path: str) -> tuple[int, dict[str, str], bytes]:
    req = f"{method} {path} HTTP/1.1\r\nHost: docker\r\nConnection: close\r\n\r\n".encode()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(10)
        deadline = time.monotonic() + 10
        last_error: OSError | None = None
        while True:
            try:
                s.connect(str(sock_path))
                break
            except (ConnectionRefusedError, FileNotFoundError) as exc:
                last_error = exc
                if time.monotonic() >= deadline:
                    raise last_error
                time.sleep(0.05)
        s.sendall(req)
        chunks: list[bytes] = []
        while True:
            data = s.recv(65536)
            if not data:
                break
            chunks.append(data)
    raw = b"".join(chunks)
    head, _, body = raw.partition(b"\r\n\r\n")
    lines = head.decode("iso-8859-1", "replace").split("\r\n")
    status = int(lines[0].split()[1])
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.lower()] = v.strip()
    return status, headers, body


def check_static_api() -> list[Check]:
    src = PDOCKERD.read_text()
    expected = {
        "ping": r'/_ping',
        "version": r'path == "/version"',
        "info": r'path == "/info"',
        "image list/create/save/load/history/inspect/delete": r'/images/(json|create|get|load|.+?/(history|json)|.+)',
        "container lifecycle/inspect/logs/wait/archive/exec/stats/rename": r'/containers/.+?(start|json|logs|wait|archive|exec|stats|rename)',
        "exec start/json": r'/exec/.+?/(start|json)',
        "network list/create/connect/disconnect/inspect/delete": r'/networks',
        "volume list/create/prune/inspect/delete": r'/volumes',
        "build": r'path == "/build"',
        "build dockerignore": r'apply_dockerignore',
        "events": r'path == "/events"',
        "system host diagnostics": r'path == "/system/host"',
    }
    checks = []
    for name, pattern in expected.items():
        ok = re.search(pattern, src) is not None
        checks.append(Check(f"static api: {name}", "PASS" if ok else "FAIL", pattern))
    for token in ("application/vnd.docker.raw-stream", "X-Docker-Container-Path-Stat",
                  "Api-Version", "application/x-tar", "PdockerWarnings",
                  "timeNano", "record_event", "not active yet"):
        checks.append(Check(f"protocol token: {token}", "PASS" if token in src else "FAIL"))
    return checks


def check_protocol_smoke() -> list[Check]:
    checks: list[Check] = []
    tmp = Path(tempfile.mkdtemp(prefix="pdocker-compat-"))
    sock = tmp / "pdockerd.sock"
    env = os.environ.copy()
    env["PDOCKER_HOME"] = str(tmp / "home")
    env["PDOCKER_TMP_DIR"] = str(tmp / "tmp")
    env["PDOCKER_RUNTIME_BACKEND"] = "direct"
    proc = subprocess.Popen(
        [sys.executable, str(PDOCKERD), "--socket", str(sock)],
        cwd=BACKEND,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if sock.exists():
                break
            if proc.poll() is not None:
                break
            time.sleep(0.05)
        if not sock.exists():
            stderr = stop_process_and_collect_stderr(proc)
            state = "alive" if proc.poll() is None else f"exited rc={proc.returncode}"
            detail = stderr[-1000:] or f"socket not created within 30s; process {state}"
            return [Check("protocol: daemon start", "FAIL", detail)]
        checks.append(Check("protocol: daemon start", "PASS", str(sock)))

        probes = [
            ("GET", "/_ping", 200),
            ("HEAD", "/_ping", 200),
            ("GET", "/version", 200),
            ("GET", "/info", 200),
            ("POST", "/version", 404),
            ("POST", "/info", 404),
            ("GET", "/containers/json?all=1", 200),
            ("GET", "/images/json", 200),
            ("GET", "/volumes", 200),
            ("GET", "/networks", 200),
            ("POST", "/networks/bridge", 404),
            ("GET", "/networks/bridge/connect", 404),
            ("POST", "/networks/bridge/unsupported", 404),
            ("GET", "/system/host", 200),
            ("GET", "/events?since=0&until=0", 200),
            ("GET", "/v1.43/version", 200),
        ]
        for method, path, want in probes:
            status, headers, body = uds_request(sock, method, path)
            ok = status == want and "api-version" in headers
            checks.append(Check(f"protocol: {method} {path}", "PASS" if ok else "FAIL",
                                f"status={status}, headers={headers}, body={body[:120]!r}"))

        docker = BACKEND / "docker-bin" / "docker"
        if docker.exists() and os.access(docker, os.X_OK):
            r = run([str(docker), "version"], cwd=BACKEND,
                    env={**env, "DOCKER_HOST": f"unix://{sock}"}, timeout=20)
            checks.append(Check("docker CLI: version negotiation",
                                "PASS" if r.returncode == 0 else "FAIL",
                                (r.stdout + r.stderr)[-1000:]))
        else:
            checks.append(Check("docker CLI: version negotiation", "SKIP", "docker CLI missing"))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)
    return checks


def check_flavor_guard() -> list[Check]:
    if FLAVOR not in APK_BY_FLAVOR:
        return [Check("apk flavor guard", "FAIL",
                      f"{FLAVOR_ENV} must be 'compat' or 'modern' (got {FLAVOR!r})")]
    if FLAVOR == "compat":
        detail = "default process-exec validation flavor"
        if not FLAVOR_WAS_EXPLICIT:
            detail += "; modern APK artifacts are ignored unless SKYDNIR_ANDROID_FLAVOR=modern"
        return [Check("apk flavor: compat process-exec validation", "PASS", detail)]
    return [Check("apk flavor: modern metadata-only opt-in",
                  "PASS" if FLAVOR_WAS_EXPLICIT else "FAIL",
                  f"{FLAVOR_ENV}=modern")]


def check_apk_payload() -> list[Check]:
    checks: list[Check] = []
    if APK is None:
        return [Check("apk: debug artifact", "FAIL",
                      f"{FLAVOR_ENV} must be 'compat' or 'modern' (got {FLAVOR!r})")]
    if not APK.exists():
        detail = f"run scripts/build-apk.sh first for {FLAVOR}: {APK}"
        if FLAVOR == "compat" and APK_BY_FLAVOR["modern"].exists():
            detail += "; existing modern APK ignored by compat fast gate"
        return [Check("apk: debug artifact", "SKIP", detail)]
    checks.append(Check("apk: debug artifact", "PASS", str(APK)))
    with zipfile.ZipFile(APK) as zf:
        names = set(zf.namelist())
        required = [
            "lib/arm64-v8a/libcrane.so",
            "lib/arm64-v8a/libcow.so",
            "lib/arm64-v8a/libpdockerpty.so",
            "lib/arm64-v8a/libpdockerdirect.so",
            "lib/arm64-v8a/libpdockergpuexecutor.so",
            "lib/arm64-v8a/libpdockermediaexecutor.so",
            "lib/arm64-v8a/libpdockergpushim.so",
            "assets/pdockerd/pdockerd",
            "assets/project-library/library.json",
            "assets/project-library/llama-cpp-gpu/compose.yaml",
            "assets/project-library/llama-cpp-gpu/documents/README.md",
            "assets/project-library/llama-cpp-gpu/scripts/pdocker-gpu-profile.sh",
            "assets/project-library/ros2-humble-rviz-novnc/compose.yaml",
            "assets/project-library/ros2-humble-rviz-novnc/documents/README.md",
            "assets/project-library/blender-xvnc-novnc/compose.yaml",
            "assets/project-library/blender-xvnc-novnc/documents/README.md",
            "assets/default-project/compose.yaml",
            "assets/default-project/documents/README.md",
            "assets/xterm/xterm.js",
            "assets/oss-licenses/THIRD_PARTY_NOTICES.md",
        ]
        for name in required:
            checks.append(Check(f"apk payload: {name}", "PASS" if name in names else "FAIL"))
        optional = [
            "lib/arm64-v8a/libpdocker-rootfs-shim.so",
            "lib/arm64-v8a/libpdocker-ld-linux-aarch64.so",
        ]
        for name in optional:
            checks.append(Check(f"apk optional direct payload: {name}", "PASS" if name in names else "SKIP"))
        proot_payload = [
            "lib/arm64-v8a/libproot.so",
            "lib/arm64-v8a/libproot-loader.so",
            "lib/arm64-v8a/libtalloc.so",
        ]
        has_proot = any(name in names for name in proot_payload)
        for name in proot_payload:
            if has_proot:
                checks.append(Check(f"legacy apk payload: {name}", "PASS" if name in names else "FAIL"))
            else:
                checks.append(Check(f"no-proot apk payload omits: {name}", "PASS" if name not in names else "FAIL"))
        cli_payload = [
            "lib/arm64-v8a/libdocker.so",
            "lib/arm64-v8a/libdocker-compose.so",
        ]
        for name in cli_payload:
            checks.append(Check(f"apk payload omits upstream Docker CLI component: {name}",
                                "PASS" if name not in names else "FAIL"))
        if "lib/arm64-v8a/libproot.so" in names:
            data = zf.read("lib/arm64-v8a/libproot.so")
            checks.append(Check("apk payload: proot advertises --cow-bind",
                                "PASS" if b"--cow-bind" in data else "FAIL"))
        else:
            pdockerd_bridge = (ROOT / "app/src/main/python/pdockerd_bridge.py").read_text()
            checks.append(Check("no-proot runtime selector",
                                "PASS" if "PDOCKER_RUNTIME_BACKEND" in pdockerd_bridge else "FAIL"))
    return checks


def check_license_inventory() -> list[Check]:
    checks: list[Check] = []
    if not LICENSE_DOC.exists():
        return [Check("license inventory", "FAIL", "THIRD_PARTY_NOTICES.md missing")]
    text = LICENSE_DOC.read_text()
    required = [
        "Apache-2.0", "go-containerregistry", "xterm.js",
        "MIT", "Chaquopy", "CPython", "Python 3.11", "OpenSSL",
        "SQLite", "certificate", "AndroidX", "Kotlin",
    ]
    for token in required:
        checks.append(Check(f"license token: {token}", "PASS" if token in text else "FAIL"))
    return checks


def check_project_library() -> list[Check]:
    script = ROOT / "scripts" / "verify-project-library.py"
    if not script.exists():
        return [Check("project library", "FAIL", "scripts/verify-project-library.py missing")]
    r = run([sys.executable, str(script)], cwd=ROOT, timeout=30)
    output = (r.stdout + r.stderr)[-2000:]
    return [Check("project library templates", "PASS" if r.returncode == 0 else "FAIL", output)]


def check_ui_actions() -> list[Check]:
    script = ROOT / "scripts" / "verify-ui-actions.py"
    if not script.exists():
        return [Check("ui actions", "FAIL", "scripts/verify-ui-actions.py missing")]
    r = run([sys.executable, str(script)], cwd=ROOT, timeout=30)
    output = (r.stdout + r.stderr)[-2000:]
    return [Check("native UI action wiring", "PASS" if r.returncode == 0 else "FAIL", output)]


def check_gpu_design_doc() -> list[Check]:
    doc = ROOT / "docs" / "design" / "GPU_COMPAT.md"
    if not doc.exists():
        return [Check("gpu compatibility design", "FAIL", "docs/design/GPU_COMPAT.md missing")]
    text = doc.read_text()
    required = [
        "Vulkan-first compatibility stack",
        "cuvk_transpile",
        "compile_ms + upload_ms + dispatch_ms + download_ms = total_ms",
        "vector_add",
        "saxpy",
        "matmul_fp32",
        "native_cuda",
        "non-goals",
    ]
    missing = [token for token in required if token not in text]
    return [Check("gpu compatibility design", "PASS" if not missing else "FAIL",
                  "missing: " + ", ".join(missing) if missing else "cuVK/Vulkan benchmark scope recorded")]


def maybe_run_backend_regression(profile: str, timeout: int) -> list[Check]:
    script = BACKEND / "scripts" / "verify_all.sh"
    if not script.exists():
        return [Check(f"backend {profile} regression", "FAIL", "verify_all.sh missing")]
    if profile not in {"quick", "full"}:
        return [Check(f"backend {profile} regression", "FAIL", f"unknown profile: {profile}")]
    label = f"backend {profile} regression: verify_all.sh --{profile}"
    try:
        r = run(["bash", str(script), f"--{profile}"], cwd=BACKEND, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        stdout = e.stdout.decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        stderr = e.stderr.decode("utf-8", "replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
        output = ANSI_RE.sub("", stdout + "\n" + stderr)[-4000:]
        return [Check(label, "FAIL",
                      f"timed out after {timeout}s; last output: {output}")]
    output = ANSI_RE.sub("", r.stdout + "\n" + r.stderr)[-4000:]
    return [Check(label,
                  "PASS" if r.returncode == 0 else "FAIL",
                  output)]


def write_report(checks: list[Check], path: Path) -> None:
    grouped = {"PASS": 0, "FAIL": 0, "SKIP": 0}
    for c in checks:
        grouped[c.status] = grouped.get(c.status, 0) + 1
    data = {
        "summary": grouped,
        "checks": [c.__dict__ for c in checks],
    }
    if path.suffix == ".json":
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    else:
        lines = [
            "# pdocker compatibility audit result",
            "",
            f"Summary: PASS={grouped.get('PASS', 0)} FAIL={grouped.get('FAIL', 0)} SKIP={grouped.get('SKIP', 0)}",
            "",
            "| status | check | detail |",
            "|---|---|---|",
        ]
        for c in checks:
            detail = ANSI_RE.sub("", c.detail).replace("\n", "<br>").replace("|", "\\|")
            lines.append(f"| {c.status} | {c.name} | {detail} |")
        path.write_text("\n".join(lines) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend-quick", action="store_true",
                    help="also run shorter backend container/API smoke")
    ap.add_argument("--backend-quick-timeout", type=int, default=900,
                    help="timeout in seconds for --backend-quick verify_all.sh --quick")
    ap.add_argument("--full", action="store_true", help="also run heavyweight image/container regression")
    ap.add_argument("--full-timeout", type=int, default=1800,
                    help="timeout in seconds for --full verify_all.sh")
    ap.add_argument("--output", type=Path, help="write .json or markdown report")
    args = ap.parse_args()

    checks: list[Check] = []
    checks += check_static_api()
    checks += check_protocol_smoke()
    checks += check_flavor_guard()
    checks += check_apk_payload()
    checks += check_license_inventory()
    checks += check_project_library()
    checks += check_ui_actions()
    checks += check_gpu_design_doc()
    if args.backend_quick:
        checks += maybe_run_backend_regression("quick", args.backend_quick_timeout)
    if args.full:
        checks += maybe_run_backend_regression("full", args.full_timeout)

    if args.output:
        write_report(checks, args.output)

    for c in checks:
        print(f"{c.status:4} {c.name} {('- ' + c.detail) if c.detail else ''}")
    return 1 if any(c.status == "FAIL" for c in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())

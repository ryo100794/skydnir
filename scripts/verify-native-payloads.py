#!/usr/bin/env python3
"""Verify packaged native payload architecture and APK inclusion.

This host-side verifier is intentionally conservative. It checks Android/Bionic
helper payloads and Linux/glibc container payloads separately so an APK build
cannot silently ship the wrong ELF class or architecture.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "pdocker.native-payloads.v1"


@dataclass(frozen=True)
class PayloadSpec:
    abi: str
    name: str
    role: str
    elf_class: str
    machine_marker: str
    required_in_apk: bool = True


ANDROID_HELPERS = (
    "libpdockerpty.so",
    "libpdockerdirect.so",
    "libpdockergpuexecutor.so",
    "libpdockermediaexecutor.so",
)
GLIBC_GPU = (
    "libpdockergpushim.so",
    "libpdockervulkanicd.so",
    "libpdockeropenclicd.so",
)

SPECS: tuple[PayloadSpec, ...] = tuple(
    PayloadSpec("arm64-v8a", name, "android-bionic", "ELF 64-bit", "ARM aarch64")
    for name in ANDROID_HELPERS
) + tuple(
    PayloadSpec("armeabi-v7a", name, "android-bionic", "ELF 32-bit", "ARM")
    for name in ANDROID_HELPERS
) + tuple(
    PayloadSpec("arm64-v8a", name, "linux-glibc-container", "ELF 64-bit", "ARM aarch64")
    for name in GLIBC_GPU
) + tuple(
    PayloadSpec("armeabi-v7a", name, "linux-glibc-container-experimental", "ELF 32-bit", "ARM")
    for name in GLIBC_GPU
)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def run_text(argv: list[str]) -> str:
    proc = subprocess.run(argv, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise RuntimeError(f"command failed {argv}: {proc.stderr.strip()}")
    return proc.stdout.strip()


def file_output(path: Path) -> str:
    return run_text(["file", str(path)])


def readelf_header(path: Path) -> str:
    try:
        return run_text(["readelf", "-h", str(path)])
    except (FileNotFoundError, RuntimeError):
        return ""


def readelf_program_headers(path: Path) -> str:
    try:
        return run_text(["readelf", "-l", str(path)])
    except (FileNotFoundError, RuntimeError):
        return ""


def expected_apk_entries() -> set[str]:
    return {f"lib/{spec.abi}/{spec.name}" for spec in SPECS if spec.required_in_apk}


def verify_payloads(apk: Path | None = None, apk_arm64_only: bool = False) -> dict[str, Any]:
    errors: list[str] = []
    payloads: list[dict[str, Any]] = []
    for spec in SPECS:
        path = ROOT / "app" / "src" / "main" / "jniLibs" / spec.abi / spec.name
        entry: dict[str, Any] = {
            "abi": spec.abi,
            "name": spec.name,
            "role": spec.role,
            "path": str(path.relative_to(ROOT)),
            "exists": path.is_file(),
        }
        if not path.is_file():
            errors.append(f"missing payload: {path.relative_to(ROOT)}")
            payloads.append(entry)
            continue
        f = file_output(path)
        header = readelf_header(path)
        ph = readelf_program_headers(path)
        entry.update(
            {
                "size": path.stat().st_size,
                "sha256": sha256(path),
                "file": f,
                "readelf_header_has_machine": "Machine:" in header,
                "has_android_linker": "/system/bin/linker" in f or "/system/bin/linker" in ph,
                "has_glibc_loader": "/lib/ld-linux" in f or "/lib/ld-linux" in ph,
            }
        )
        if spec.elf_class not in f:
            errors.append(f"{path.relative_to(ROOT)} expected {spec.elf_class}: {f}")
        if spec.machine_marker not in f:
            errors.append(f"{path.relative_to(ROOT)} expected machine marker {spec.machine_marker}: {f}")
        if spec.role == "android-bionic":
            if spec.name != "libpdockerpty.so" and not entry["has_android_linker"]:
                errors.append(f"{path.relative_to(ROOT)} executable helper must use Android linker")
            if entry["has_glibc_loader"]:
                errors.append(f"{path.relative_to(ROOT)} Android helper must not use glibc loader")
        if spec.role.startswith("linux-glibc"):
            if spec.name == "libpdockergpushim.so" and not entry["has_glibc_loader"]:
                errors.append(f"{path.relative_to(ROOT)} glibc executable shim must use glibc loader")
            if entry["has_android_linker"]:
                errors.append(f"{path.relative_to(ROOT)} glibc payload must not use Android linker")
        payloads.append(entry)

    apk_info: dict[str, Any] | None = None
    if apk is not None:
        apk_path = apk if apk.is_absolute() else ROOT / apk
        apk_info = {"path": str(apk_path), "exists": apk_path.is_file()}
        if not apk_path.is_file():
            errors.append(f"missing APK: {apk_path}")
        else:
            with zipfile.ZipFile(apk_path) as zf:
                names = set(zf.namelist())
            expected = expected_apk_entries()
            if apk_arm64_only:
                expected = {entry for entry in expected if entry.startswith("lib/arm64-v8a/")}
            missing = sorted(expected - names)
            forbidden_32 = sorted(name for name in names if name.startswith("lib/armeabi-v7a/")) if apk_arm64_only else []
            apk_info["missing_entries"] = missing
            apk_info["forbidden_armeabi_v7a_entries"] = forbidden_32
            apk_info["checked_entries"] = sorted(expected)
            apk_info["policy"] = "arm64-only" if apk_arm64_only else "all-built-abis"
            if missing:
                errors.append("APK missing native payload entries: " + ", ".join(missing))
            if forbidden_32:
                errors.append("APK includes incomplete armeabi-v7a runtime entries: " + ", ".join(forbidden_32))

    return {
        "schema": SCHEMA,
        "success": not errors,
        "errors": errors,
        "payloads": payloads,
        "apk": apk_info,
        "notes": [
            "armeabi-v7a pdocker-direct is an explicit unsupported-ABI stub until ARM32 ptrace/syscall support is implemented.",
            "armeabi-v7a glibc GPU payloads are packaged experimental evidence until Vulkan handle storage is pointer-width clean.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apk", type=Path, default=None, help="Optional APK to check for native payload entries")
    parser.add_argument("--apk-arm64-only", action="store_true", help="Require the APK to omit armeabi-v7a entries until the 32-bit runtime is complete")
    parser.add_argument("--write-artifact", type=Path, default=None, help="Write JSON verification artifact")
    args = parser.parse_args(argv)
    data = verify_payloads(args.apk, apk_arm64_only=args.apk_arm64_only)
    if args.write_artifact:
        out = args.write_artifact if args.write_artifact.is_absolute() else ROOT / args.write_artifact
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {out}")
    if not data["success"]:
        for err in data["errors"]:
            print(f"error: {err}", file=sys.stderr)
        return 2
    print("verify-native-payloads: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Validate pdocker storage metric snapshots without requiring a device."""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
from pathlib import Path
from typing import Any, Sequence


FIXTURE: dict[str, Any] = {
    "system_df": {
        "SharedLayerBytes": 60,
        "ImageViewBytes": 90,
        "ContainerUpperBytes": 7,
        "UniqueBytes": 67,
        "TotalBytes": 4096,
        "FreeBytes": 2048,
        "PdockerStorage": {
            "SharedLayerPool": "Layer bytes are counted once across all image views.",
            "Overlap": "Image virtual sizes overlap the shared layer pool and must not be added to UniqueBytes.",
            "ContainerUpper": "ContainerUpperBytes is private writable upperdir data for containers.",
        },
    },
    "images": [
        {
            "RepoTags": ["local/one:latest"],
            "VirtualSize": 30,
            "SharedSize": 10,
            "UniqueSize": 20,
        },
        {
            "RepoTags": ["local/two:latest"],
            "VirtualSize": 40,
            "SharedSize": 10,
            "UniqueSize": 30,
        },
    ],
    "containers": [
        {
            "Id": "c" * 64,
            "Names": ["/storage-test"],
            "SizeRw": 7,
            "SizeRootFs": 37,
        }
    ],
}

SUMMARY_KEYS = (
    "SharedLayerBytes",
    "ImageViewBytes",
    "ContainerUpperBytes",
    "UniqueBytes",
    "TotalBytes",
    "FreeBytes",
    "RootfsViewBytes",
    "VolumeBytes",
    "BuildCacheBytes",
)
IMAGE_KEYS = ("VirtualSize", "SharedSize", "UniqueSize")
CONTAINER_KEYS = ("SizeRw", "SizeRootFs")
UNIQUE_COMPONENT_KEYS = (
    "SharedLayerBytes",
    "ContainerUpperBytes",
    "VolumeBytes",
    "BuildCacheBytes",
)
CAPTURE_ENDPOINTS = (
    ("system_df", "/system/df"),
    ("images", "/images/json"),
    ("containers", "/containers/json?all=1&size=1"),
)


class ValidationError(Exception):
    pass


def fail(errors: list[str], msg: str) -> None:
    errors.append(f"FAIL: {msg}")


def require_mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError(f"{name} must be a JSON object")
    return value


def require_list(value: Any, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValidationError(f"{name} must be a JSON array")
    return value


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def check_nonnegative_number(errors: list[str], owner: str, key: str, value: Any) -> None:
    if not is_number(value):
        fail(errors, f"{owner}.{key} must be numeric, got {value!r}")
    elif value < 0:
        fail(errors, f"{owner}.{key} must be nonnegative, got {value!r}")


def check_summary(snapshot: dict[str, Any], errors: list[str]) -> None:
    summary = require_mapping(snapshot.get("system_df"), "system_df")
    for key in SUMMARY_KEYS:
        if key in summary:
            check_nonnegative_number(errors, "system_df", key, summary[key])

    for key in ("SharedLayerBytes", "ContainerUpperBytes", "UniqueBytes"):
        if key not in summary:
            fail(errors, f"system_df.{key} is required")

    notes = require_mapping(summary.get("PdockerStorage", {}), "system_df.PdockerStorage")
    note_text = " ".join(f"{key} {value}" for key, value in notes.items()).lower()
    if "shared" not in note_text or "layer" not in note_text:
        fail(errors, "PdockerStorage notes must describe the shared layer pool")
    if "must not be added" not in note_text and "do not add" not in note_text:
        fail(errors, "PdockerStorage notes must say image views are not additive")
    if "upper" not in note_text and "private" not in note_text:
        fail(errors, "PdockerStorage notes must describe container upper/private storage")

    required_unique_components = ("SharedLayerBytes", "ContainerUpperBytes")
    optional_unique_components = ("VolumeBytes", "BuildCacheBytes")
    if all(key in summary and is_number(summary[key]) for key in required_unique_components):
        expected = sum(summary[key] for key in required_unique_components)
        expected += sum(
            summary[key]
            for key in optional_unique_components
            if key in summary and is_number(summary[key])
        )
        if summary.get("UniqueBytes") != expected:
            fail(
                errors,
                "system_df.UniqueBytes must equal unique storage components "
                f"({expected}), excluding overlapping image views",
            )

    if all(key in summary and is_number(summary[key]) for key in ("UniqueBytes", "ImageViewBytes")):
        additive_wrong = summary["UniqueBytes"] + summary["ImageViewBytes"]
        if summary.get("TotalBytes") == additive_wrong:
            fail(errors, "system_df.TotalBytes appears to double count ImageViewBytes")

    if all(key in summary and is_number(summary[key]) for key in ("TotalBytes", "FreeBytes")):
        if summary["FreeBytes"] > summary["TotalBytes"]:
            fail(errors, "system_df.FreeBytes must not exceed TotalBytes")

    if all(key in summary and is_number(summary[key]) for key in ("RootfsViewBytes", "ContainerUpperBytes")):
        if summary["RootfsViewBytes"] < summary["ContainerUpperBytes"]:
            fail(errors, "system_df.RootfsViewBytes must be at least ContainerUpperBytes")


def check_images(snapshot: dict[str, Any], errors: list[str]) -> None:
    images = require_list(snapshot.get("images", []), "images")
    if not images:
        fail(errors, "images must include at least one image metric row")
    for index, raw in enumerate(images):
        image = require_mapping(raw, f"images[{index}]")
        owner = f"images[{index}]"
        for key in IMAGE_KEYS:
            if key in image:
                check_nonnegative_number(errors, owner, key, image[key])
            else:
                fail(errors, f"{owner}.{key} is required")
        if all(key in image and is_number(image[key]) for key in IMAGE_KEYS):
            if image["SharedSize"] + image["UniqueSize"] != image["VirtualSize"]:
                fail(errors, f"{owner}.SharedSize + UniqueSize must equal VirtualSize")


def check_containers(snapshot: dict[str, Any], errors: list[str]) -> None:
    containers = require_list(snapshot.get("containers", []), "containers")
    for index, raw in enumerate(containers):
        container = require_mapping(raw, f"containers[{index}]")
        owner = f"containers[{index}]"
        if "SizeRw" not in container:
            fail(errors, f"{owner}.SizeRw is required for size=true snapshots")
        for key in CONTAINER_KEYS:
            if key in container:
                check_nonnegative_number(errors, owner, key, container[key])
        if all(key in container and is_number(container[key]) for key in CONTAINER_KEYS):
            if container["SizeRootFs"] < container["SizeRw"]:
                fail(errors, f"{owner}.SizeRootFs must be at least SizeRw")


def load_snapshot(path: Path | None) -> dict[str, Any]:
    if path is None:
        return FIXTURE
    return require_mapping(json.loads(path.read_text()), str(path))


def docker_socket_get_command(
    endpoint: str,
    *,
    package: str,
    socket: str,
    timeout: int,
) -> list[str]:
    request = (
        f"GET {endpoint} HTTP/1.1\\r\\n"
        "Host: docker\\r\\n"
        "Connection: close\\r\\n"
        "\\r\\n"
    )
    remote = (
        "cd files/pdocker && "
        f"printf %b {shlex.quote(request)} | "
        f"nc -U -W {timeout} {shlex.quote(socket)}"
    )
    return ["shell", f"run-as {shlex.quote(package)} sh -c {shlex.quote(remote)}"]


def format_command(argv: Sequence[str]) -> str:
    return " ".join(shlex.quote(part) for part in argv)


def split_http_body(raw: str) -> str:
    _, separator, body = raw.partition("\r\n\r\n")
    if not separator:
        _, separator, body = raw.partition("\n\n")
    if not separator:
        return raw.strip()
    return body.strip()


def capture_endpoint(
    endpoint: str,
    *,
    adb: str,
    package: str,
    socket: str,
    timeout: int,
) -> Any:
    command = [adb, *docker_socket_get_command(endpoint, package=package, socket=socket, timeout=timeout)]
    result = subprocess.run(command, check=False, text=True, capture_output=True)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ValidationError(
            f"capture failed for {endpoint} with exit {result.returncode}: {detail}"
        )
    body = split_http_body(result.stdout)
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"capture for {endpoint} did not return JSON: {exc}") from exc


def capture_snapshot(
    *,
    adb: str,
    package: str,
    socket: str,
    timeout: int,
) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    for section, endpoint in CAPTURE_ENDPOINTS:
        snapshot[section] = capture_endpoint(
            endpoint,
            adb=adb,
            package=package,
            socket=socket,
            timeout=timeout,
        )
    return snapshot


def print_capture_dry_run(
    *,
    adb: str,
    package: str,
    socket: str,
    timeout: int,
) -> None:
    for section, endpoint in CAPTURE_ENDPOINTS:
        command = [adb, *docker_socket_get_command(endpoint, package=package, socket=socket, timeout=timeout)]
        print(f"{section}: {format_command(command)}")


def validate(snapshot: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    try:
        check_summary(snapshot, errors)
        check_images(snapshot, errors)
        check_containers(snapshot, errors)
    except ValidationError as exc:
        errors.append(f"FAIL: {exc}")
    return errors


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate storage metric JSON snapshots or the built-in fixture."
    )
    parser.add_argument(
        "--fixture",
        type=Path,
        help="JSON snapshot with system_df, images, and containers sections.",
    )
    parser.add_argument(
        "--print-fixture",
        action="store_true",
        help="Print the built-in example snapshot and exit.",
    )
    parser.add_argument(
        "--capture-device",
        action="store_true",
        help="Capture metrics from an Android device through adb run-as and the pdockerd socket.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="With --capture-device, print the adb/nc commands without executing them.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Write the captured JSON snapshot to this path before validation.",
    )
    parser.add_argument(
        "--adb",
        default="adb",
        help="adb executable to use for --capture-device (default: adb).",
    )
    parser.add_argument(
        "--package",
        default="io.github.ryo100794.pdocker.compat",
        help="Android package for adb run-as capture.",
    )
    parser.add_argument(
        "--socket",
        default="pdockerd.sock",
        help="pdockerd socket path relative to files/pdocker on device.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=5,
        help="nc socket timeout in seconds for each device endpoint.",
    )
    args = parser.parse_args(argv)

    if args.print_fixture:
        print(json.dumps(FIXTURE, indent=2, sort_keys=True))
        return 0

    if args.fixture and args.capture_device:
        parser.error("--fixture and --capture-device are mutually exclusive")
    if args.dry_run and not args.capture_device:
        parser.error("--dry-run requires --capture-device")
    if args.output and not args.capture_device:
        parser.error("--output requires --capture-device")

    if args.capture_device:
        if args.dry_run:
            print_capture_dry_run(
                adb=args.adb,
                package=args.package,
                socket=args.socket,
                timeout=args.timeout,
            )
            return 0
        try:
            snapshot = capture_snapshot(
                adb=args.adb,
                package=args.package,
                socket=args.socket,
                timeout=args.timeout,
            )
        except ValidationError as exc:
            print(f"FAIL: {exc}")
            return 1
        if args.output:
            args.output.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
    else:
        snapshot = load_snapshot(args.fixture)

    errors = validate(snapshot)
    if errors:
        print("\n".join(errors))
        return 1
    if args.capture_device:
        source = args.output if args.output else "device capture"
    else:
        source = args.fixture if args.fixture else "built-in fixture"
    print(f"verify-storage-metrics: PASS ({source})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Locate the runtime native Q6 source SPIR-V dump for probe generation.

This is a host-only glue tool.  It does not touch ADB, llama.cpp, Dockerfiles,
models, or prompts.  It consumes executor dumps produced by
PDOCKER_GPU_SPIRV_DUMP_DIR and emits the exact arguments needed by
prepare-q6k-noop-probe.sh and android-llama-gpu-q6-workgroup-run.sh.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "pdocker.q6k.source-spirv-dump-locator.v1"
DUMP_RE = re.compile(
    r"^pdocker-spirv-(?P<phase>[A-Za-z0-9_-]+)-(?P<dispatch_id>\d+)-(?P<hash>0x[0-9a-fA-F]+)\.spv$"
)
SPIRV_MAGIC_LE = b"\x03\x02\x23\x07"
Q6_KERNEL_HINT = "mul-mat-vec-q6-k-large"


def fnv1a64(data: bytes) -> int:
    value = 1469598103934665603
    for byte in data:
        value ^= byte
        value = (value * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return value


def hex64(data: bytes) -> str:
    return f"0x{fnv1a64(data):016x}"


def normalize_hash(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    if not text.startswith("0x"):
        return None
    if not re.fullmatch(r"0x[0-9a-f]+", text):
        return None
    return text


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"missing JSON file: {path}")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid JSON file: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"JSON root must be an object: {path}")
    return data


def nested(value: Any, *keys: str) -> Any:
    cur = value
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def walk(value: Any):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def unique_hashes(values: list[Any]) -> list[str]:
    return sorted({h for h in (normalize_hash(v) for v in values) if h})


def artifact_target_hash(artifact: dict[str, Any]) -> tuple[str | None, str, list[dict[str, Any]]]:
    q6 = nested(artifact, "gpu", "diagnostics", "q6_workgroup_diagnostics")
    if not isinstance(q6, dict):
        q6 = nested(artifact, "q6_workgroup_diagnostics")
    if not isinstance(q6, dict):
        q6 = {}

    priorities: list[tuple[str, list[Any]]] = [
        (
            "artifact:q6_native_spirv_identity.source_spirv_hash",
            [nested(q6, "q6_native_spirv_identity", "source_spirv_hash")],
        ),
        ("artifact:q6_workgroup_diagnostics.source_spirv_hash", [q6.get("source_spirv_hash")]),
        (
            "artifact:q6_native_vs_writeback_split.samples.source_spirv_hash",
            [
                sample.get("source_spirv_hash")
                for sample in (nested(q6, "q6_native_vs_writeback_split", "samples") or [])
                if isinstance(sample, dict)
            ],
        ),
        (
            "artifact:generic_spirv_dispatch.cpu_oracle.kernel_hint.source_spirv_hash",
            [
                event.get("source_spirv_hash")
                for event in (nested(artifact, "gpu", "diagnostics", "generic_spirv_dispatch") or [])
                if isinstance(event, dict)
                and isinstance(event.get("cpu_oracle"), dict)
                and event["cpu_oracle"].get("kernel_hint") == Q6_KERNEL_HINT
            ],
        ),
        ("artifact:q6_workgroup_diagnostics.latest_spirv_hash", [q6.get("latest_spirv_hash")]),
    ]

    checked: list[dict[str, Any]] = []
    for source, raw_values in priorities:
        hashes = unique_hashes(raw_values)
        checked.append({"source": source, "hashes": hashes})
        if len(hashes) == 1:
            return hashes[0], source, checked
        if len(hashes) > 1:
            return None, f"ambiguous:{source}", checked
    return None, "missing", checked


def read_meta(meta_path: Path | None) -> dict[str, Any]:
    if not meta_path or not meta_path.exists():
        return {}
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"_invalid_json": str(exc)}
    return data if isinstance(data, dict) else {"_invalid_json": "root-not-object"}


def inspect_spv(path: Path) -> dict[str, Any]:
    record: dict[str, Any] = {
        "spv": str(path),
        "valid": False,
        "errors": [],
    }
    match = DUMP_RE.match(path.name)
    if match:
        record["filename_phase"] = match.group("phase")
        record["filename_dispatch_id"] = int(match.group("dispatch_id"))
        record["filename_hash"] = normalize_hash(match.group("hash"))
    meta_path = path.with_suffix(".json")
    if meta_path.exists():
        record["meta"] = str(meta_path)
    meta = read_meta(meta_path if meta_path.exists() else None)
    if meta:
        record["meta_schema"] = meta.get("schema")
        record["meta_phase"] = meta.get("phase")
        record["meta_hash"] = normalize_hash(meta.get("spirv_hash"))
        record["meta_shader_bytes"] = meta.get("shader_bytes")
        if meta.get("_invalid_json"):
            record["errors"].append(f"invalid-meta-json:{meta['_invalid_json']}")
    try:
        data = path.read_bytes()
    except OSError as exc:
        record["errors"].append(f"read-failed:{exc}")
        return record
    record["file_size"] = len(data)
    record["sha256"] = "sha256:" + hashlib.sha256(data).hexdigest()
    if len(data) < 4:
        record["errors"].append("too-small")
    if len(data) % 4 != 0:
        record["errors"].append("size-not-4-byte-aligned")
    if data[:4] != SPIRV_MAGIC_LE:
        record["errors"].append("bad-spirv-magic")
    fnv_hash = hex64(data)
    record["fnv1a64"] = fnv_hash
    phase = record.get("meta_phase") or record.get("filename_phase")
    if phase:
        record["phase"] = str(phase)
    dispatch_id = record.get("filename_dispatch_id")
    if dispatch_id is not None:
        record["dispatch_id"] = dispatch_id
    filename_hash = record.get("filename_hash")
    if filename_hash and filename_hash != fnv_hash:
        record["errors"].append(f"filename-hash-mismatch:{filename_hash}!={fnv_hash}")
    meta_hash = record.get("meta_hash")
    if meta_hash and meta_hash != fnv_hash:
        record["errors"].append(f"meta-hash-mismatch:{meta_hash}!={fnv_hash}")
    meta_bytes = record.get("meta_shader_bytes")
    if isinstance(meta_bytes, int) and meta_bytes != len(data):
        record["errors"].append(f"meta-size-mismatch:{meta_bytes}!={len(data)}")
    record["valid"] = not record["errors"]
    return record


def candidate_sort_key(record: dict[str, Any]) -> tuple[int, float, str]:
    dispatch = record.get("dispatch_id")
    try:
        dispatch_value = int(dispatch)
    except (TypeError, ValueError):
        dispatch_value = -1
    try:
        mtime = Path(str(record["spv"])).stat().st_mtime
    except OSError:
        mtime = 0.0
    return (dispatch_value, mtime, str(record.get("spv") or ""))


def locate_dump(dump_dir: Path, target_hash: str, allow_effective: bool = False) -> tuple[dict[str, Any] | None, list[dict[str, Any]], list[dict[str, Any]], str]:
    candidates: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    allowed_phases = {"original"}
    if allow_effective:
        allowed_phases.add("effective")
    for spv in sorted(dump_dir.rglob("*.spv")):
        record = inspect_spv(spv)
        phase = str(record.get("phase") or "")
        if record.get("valid") and record.get("fnv1a64") == target_hash and phase in allowed_phases:
            candidates.append(record)
        else:
            if record.get("fnv1a64") == target_hash or phase in {"original", "effective"}:
                rejected.append(record)
    if not candidates:
        return None, candidates, rejected[:64], "no-matching-original-dump"
    sha_set = {str(c.get("sha256")) for c in candidates}
    if len(sha_set) > 1:
        return None, candidates, rejected[:64], "ambiguous-distinct-byte-content"
    selected = sorted(candidates, key=candidate_sort_key)[-1]
    return selected, candidates, rejected[:64], "ok"


def build_prepare_args(selected: dict[str, Any], target_hash: str, prepare_out_dir: str, probe_writes: bool) -> list[str]:
    args = [
        "scripts/prepare-q6k-noop-probe.sh",
        "--spv",
        str(selected["spv"]),
        "--out-dir",
        prepare_out_dir,
        "--expected-hash",
        target_hash,
    ]
    if probe_writes:
        args.append("--probe-writes")
    return args


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dump-dir", type=Path, required=True)
    parser.add_argument("--artifact", type=Path)
    parser.add_argument("--hash", dest="target_hash")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--prepare-out-dir", default="/tmp/q6write10-bundle")
    parser.add_argument("--probe-writes", action="store_true")
    parser.add_argument("--allow-effective", action="store_true", help="Allow effective-phase dumps as an explicit diagnostic fallback.")
    parser.add_argument("--print-prepare-command", action="store_true")
    parser.add_argument("--run-prepare", action="store_true")
    args = parser.parse_args(argv)

    dump_dir = args.dump_dir
    if not dump_dir.is_dir():
        raise SystemExit(f"dump dir does not exist or is not a directory: {dump_dir}")

    checked_hash_sources: list[dict[str, Any]] = []
    target_hash = normalize_hash(args.target_hash)
    target_hash_source = "--hash" if target_hash else ""
    if args.target_hash and not target_hash:
        raise SystemExit(f"invalid --hash value: {args.target_hash!r}")
    if not target_hash:
        if not args.artifact:
            raise SystemExit("--hash or --artifact is required; refusing to guess a stale Q6 hash")
        artifact = load_json(args.artifact)
        target_hash, target_hash_source, checked_hash_sources = artifact_target_hash(artifact)
        if not target_hash:
            result = {
                "schema": SCHEMA,
                "valid": False,
                "reason": "target-hash-not-resolved",
                "target_hash_source": target_hash_source,
                "checked_hash_sources": checked_hash_sources,
            }
            text = json.dumps(result, indent=2, sort_keys=True) + "\n"
            if args.out:
                args.out.parent.mkdir(parents=True, exist_ok=True)
                args.out.write_text(text, encoding="utf-8")
            print(text, end="")
            return 2

    selected, candidates, rejected, reason = locate_dump(dump_dir, target_hash, args.allow_effective)
    result: dict[str, Any] = {
        "schema": SCHEMA,
        "valid": selected is not None,
        "reason": reason,
        "dump_dir": str(dump_dir),
        "target_hash": target_hash,
        "target_hash_source": target_hash_source,
        "checked_hash_sources": checked_hash_sources,
        "allow_effective": bool(args.allow_effective),
        "candidate_count": len(candidates),
        "candidates": candidates,
        "rejected_sample": rejected,
    }
    if selected:
        prepare_args = build_prepare_args(selected, target_hash, args.prepare_out_dir, args.probe_writes)
        runner_args = [
            "--probe-source-spv",
            str(selected["spv"]),
            "--probe-source-hash",
            target_hash,
        ]
        result.update({
            "selected": selected,
            "prepare_args": prepare_args,
            "prepare_command": " ".join(shlex.quote(part) for part in prepare_args),
            "runner_args": runner_args,
        })
        if args.run_prepare:
            subprocess.run(prepare_args, cwd=ROOT, check=True)
            result["prepare_executed"] = True
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    print(text, end="")
    if args.print_prepare_command and selected:
        print(result["prepare_command"], file=sys.stderr)
    return 0 if selected else 1


if __name__ == "__main__":
    raise SystemExit(main())

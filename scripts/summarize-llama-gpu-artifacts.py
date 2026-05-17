#!/usr/bin/env python3
"""Summarize existing llama GPU compare artifacts with the current verifier.

This is a local, non-device inventory pass.  It intentionally does not rebuild
llama.cpp, touch Dockerfiles, change prompts, or contact a device.  The goal is
to make old evidence searchable under today's stricter gates so the next
device run starts from a known blocker class instead of folklore.
"""

from __future__ import annotations

import argparse
from collections import Counter
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GLOB = "docs/test/llama-gpu-*.json"
DEFAULT_EXCLUDED_NAMES = {"llama-gpu-artifact-sweep-latest.json"}
VERIFIER = ROOT / "scripts" / "verify-llama-gpu-artifact.py"


def load_verifier():
    spec = importlib.util.spec_from_file_location("llama_gpu_artifact_verifier", VERIFIER)
    if not spec or not spec.loader:  # pragma: no cover - defensive import guard
        raise SystemExit(f"could not load {VERIFIER}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def load_artifact(path: Path) -> tuple[Any, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except OSError as exc:
        return None, f"read-error: {exc}"
    except json.JSONDecodeError as exc:
        return None, f"json-error: {exc}"


def artifact_digest(path: Path, verifier: Any) -> dict[str, Any]:
    payload, error = load_artifact(path)
    base: dict[str, Any] = {"path": rel(path)}
    if error:
        return base | {"classification": "invalid-json", "error": error}
    if not isinstance(payload, dict):
        return base | {
            "root_type": type(payload).__name__,
            "classification": "invalid-root",
            "next_action": "inspect this non-object artifact before using it as compare evidence",
        }

    try:
        report = verifier.classify(payload)
    except Exception as exc:  # pragma: no cover - exercised through CLI fixtures if needed
        return base | {
            "schema": payload.get("schema"),
            "classification": "verifier-error",
            "error": f"{type(exc).__name__}: {exc}",
        }

    diagnostics = (((payload.get("gpu") or {}).get("diagnostics") or {}) if isinstance(payload.get("gpu"), dict) else {})
    q6 = diagnostics.get("q6_workgroup_diagnostics") if isinstance(diagnostics, dict) else None
    q6 = q6 if isinstance(q6, dict) else {}
    correctness = (((payload.get("gpu") or {}).get("correctness") or {}) if isinstance(payload.get("gpu"), dict) else {})
    correctness_summary = correctness.get("summary") if isinstance(correctness, dict) else {}
    correctness_summary = correctness_summary if isinstance(correctness_summary, dict) else {}

    return base | {
        "schema": payload.get("schema"),
        "classification": report.get("classification"),
        "correctness": report.get("correctness") or correctness_summary.get("correctness"),
        "correctness_claim_allowed": bool(report.get("correctness_claim_allowed")),
        "benchmark_claim_allowed": bool(report.get("benchmark_claim_allowed")),
        "device_memory_blocked": bool(report.get("device_memory_blocked")),
        "speedup": report.get("speedup"),
        "next_action": report.get("next_action"),
        "q6_latest_status": q6.get("latest_status"),
        "q6_blocker_class": q6.get("blocker_class"),
        "q6_writeback_summary": (report.get("q6_writeback_evidence") or {}).get("summary"),
        "q6_writeback_verified_all": q6.get("q6_writeback_verified_all"),
        "q6_row_indexed_writeback_verified": q6.get("q6_row_indexed_writeback_verified"),
        "runtime_freshness": (report.get("runtime_freshness") or {}).get("summary"),
    }


def summarize(paths: list[Path], *, snapshot_date: str | None = None) -> dict[str, Any]:
    verifier = load_verifier()
    entries = [artifact_digest(path, verifier) for path in sorted(paths, key=lambda item: str(item))]
    classifications = Counter(str(entry.get("classification")) for entry in entries)
    q6_entries = [
        entry
        for entry in entries
        if str(entry.get("classification", "")).startswith("q6-")
        or entry.get("q6_latest_status")
        or "q6" in entry["path"].lower()
    ]
    q6_classifications = Counter(str(entry.get("classification")) for entry in q6_entries)
    return {
        "schema": "pdocker.llama.gpu.artifact-sweep.v1",
        "snapshot_date": snapshot_date,
        "source_glob": DEFAULT_GLOB,
        "artifact_count": len(entries),
        "classification_counts": dict(sorted(classifications.items())),
        "q6_artifact_count": len(q6_entries),
        "q6_classification_counts": dict(sorted(q6_classifications.items())),
        "next_device_run_checklist": [
            "Keep llama.cpp, Dockerfile, model, and prompt probes unchanged.",
            "Run the next strict ngl=1 compare with row-indexed Q6_K writeback evidence enabled.",
            "Require gpu.diagnostics.config_propagation.summary == pass before interpreting Q6_K evidence.",
            "Require q6_row_indexed_writeback_evidence to be non-empty and q6_row_indexed_writeback_verified == true.",
            "If q6_writeback_verified_all is not true, classify the blocker as writeback before touching shader math.",
            "If writeback is verified and latest_status is mismatch, classify exactly one remaining blocker: workgroup-shape, Vulkan device-execution, or Q6 arithmetic/reduction/output-layout.",
            "Do not use served=true, speedup, or HTTP liveness as correctness evidence.",
        ],
        "artifacts": entries,
    }


def expand_artifacts(args: argparse.Namespace) -> list[Path]:
    if args.artifacts:
        return [Path(item) if Path(item).is_absolute() else ROOT / item for item in args.artifacts]
    return [
        path
        for path in ROOT.glob(DEFAULT_GLOB)
        if path.name not in DEFAULT_EXCLUDED_NAMES
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifacts", nargs="*", help="Specific artifacts to summarize. Defaults to docs/test/llama-gpu-*.json.")
    parser.add_argument("--out", type=Path, help="Write the JSON summary to this path instead of stdout.")
    parser.add_argument("--snapshot-date", help="Optional stable snapshot date to record in the summary.")
    args = parser.parse_args(argv)

    report = summarize(expand_artifacts(args), snapshot_date=args.snapshot_date)
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.out:
        out = args.out if args.out.is_absolute() else ROOT / args.out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

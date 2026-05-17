#!/usr/bin/env python3
"""Validate/reduce runtime teardown device artifacts.

The Android smoke route intentionally writes non-promoting planned-gap
artifacts until the device evidence has been reduced.  This verifier has two
explicit modes:

* ``--expect-planned-gap``: accept only ``Status=planned-gap`` /
  ``Success=false``.  This is the current focused lane.
* default / ``--expect-device-pass``: require ``Status=device-pass`` and load
  the referenced same-container-ID proof and negative-case artifacts from disk.

The verifier is intentionally strict about external proof files for a promoted
device pass.  A top-level JSON alone must not be enough to close teardown.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "scripts" / "verify-service-truth-plan.py"

_spec = importlib.util.spec_from_file_location("verify_service_truth_plan", CONTRACT)
if not _spec or not _spec.loader:  # pragma: no cover - defensive import guard
    raise SystemExit(f"could not load {CONTRACT}")
verify_service_truth_plan = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(verify_service_truth_plan)


def fail(message: str, code: int = 1) -> None:
    print(f"verify-runtime-teardown-artifact: FAIL: {message}", file=sys.stderr)
    raise SystemExit(code)


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        fail(f"could not read {path}: {exc}")
    except json.JSONDecodeError as exc:
        fail(f"{path}: invalid JSON: {exc}")
    if not isinstance(data, dict):
        fail(f"{path}: expected a JSON object")
    return data


def runtime_teardown_subpath(ref: str) -> Path | None:
    marker = "runtime-teardown/"
    if marker not in ref:
        return None
    return Path(ref.split(marker, 1)[1])


def candidate_paths(ref: str, *, artifact_path: Path, evidence_root: Path | None) -> list[Path]:
    raw = Path(ref)
    subpath = runtime_teardown_subpath(ref)
    candidates: list[Path] = []
    if evidence_root is not None:
        if subpath is not None:
            candidates.append(evidence_root / subpath)
        candidates.append(evidence_root / raw.name)
    if subpath is not None:
        candidates.append(artifact_path.parent / "runtime-teardown" / subpath)
    candidates.append(artifact_path.parent / raw.name)
    if not raw.is_absolute():
        candidates.append(ROOT / raw)
    else:
        candidates.append(raw)

    deduped: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        normalized = path.resolve() if path.exists() else path
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(path)
    return deduped


def resolve_ref(ref: Any, *, artifact_path: Path, evidence_root: Path | None, field: str) -> Path:
    if not isinstance(ref, str) or not ref.strip():
        fail(f"{field} must be a non-empty artifact path")
    tried = candidate_paths(ref, artifact_path=artifact_path, evidence_root=evidence_root)
    for path in tried:
        if path.is_file():
            return path
    fail(f"{field} not found; tried: " + ", ".join(str(path) for path in tried))


def load_proof_artifacts(
    artifact: dict[str, Any],
    *,
    artifact_path: Path,
    evidence_root: Path | None,
) -> dict[str, dict[str, Any]]:
    refs = artifact.get("SameContainerIdTeardownArtifacts")
    if not isinstance(refs, dict):
        fail("device-pass artifact must name SameContainerIdTeardownArtifacts")

    loaded: dict[str, dict[str, Any]] = {}
    for key, label in [("StopRm", "same-container-id-stop-rm"), ("KillRm", "same-container-id-kill-rm")]:
        ref = refs.get(key)
        if not isinstance(ref, dict):
            fail(f"SameContainerIdTeardownArtifacts.{key} must be an object")
        path = resolve_ref(
            ref.get("Artifact"),
            artifact_path=artifact_path,
            evidence_root=evidence_root,
            field=f"SameContainerIdTeardownArtifacts.{key}.Artifact",
        )
        loaded[label] = load_json(path)
    return loaded


def load_negative_artifacts(
    artifact: dict[str, Any],
    *,
    artifact_path: Path,
    evidence_root: Path | None,
) -> dict[str, dict[str, Any]]:
    refs = artifact.get("NegativeCases")
    if not isinstance(refs, dict):
        fail("device-pass artifact must name NegativeCases")

    loaded: dict[str, dict[str, Any]] = {}
    for display_key, ref in refs.items():
        path = resolve_ref(
            ref,
            artifact_path=artifact_path,
            evidence_root=evidence_root,
            field=f"NegativeCases.{display_key}",
        )
        loaded[path.stem] = load_json(path)

    required = set(verify_service_truth_plan.TEARDOWN_REQUIRED_NEGATIVE_CASES)
    missing = sorted(required - set(loaded))
    if missing:
        fail("missing required negative-case artifacts: " + ", ".join(missing))
    return loaded


def validate_planned_gap(artifact: dict[str, Any]) -> None:
    try:
        verify_service_truth_plan.validate_runtime_teardown_artifact(artifact)
    except ValueError as exc:
        fail(str(exc))
    if artifact.get("Status") != "planned-gap" or artifact.get("Success") is not False:
        fail("expected Status=planned-gap and Success=false")
    print("verify-runtime-teardown-artifact: PASS planned-gap non-promoting evidence")


def validate_device_pass(artifact: dict[str, Any], *, artifact_path: Path, evidence_root: Path | None) -> None:
    if artifact.get("Status") != "device-pass":
        fail("expected Status=device-pass with reduced teardown proof", code=2)
    proofs = load_proof_artifacts(artifact, artifact_path=artifact_path, evidence_root=evidence_root)
    negatives = load_negative_artifacts(artifact, artifact_path=artifact_path, evidence_root=evidence_root)
    try:
        verify_service_truth_plan.validate_runtime_teardown_artifact(artifact, proofs, negatives)
    except ValueError as exc:
        fail(str(exc))
    print("verify-runtime-teardown-artifact: PASS device-pass teardown proof")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", help="runtime-teardown-latest.json")
    parser.add_argument(
        "--evidence-root",
        help="Directory containing same-container-ID and negative-case artifacts. Defaults to artifact-dir/runtime-teardown or artifact dir.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--expect-planned-gap", action="store_true", help="Require non-promoting planned-gap evidence.")
    mode.add_argument("--expect-device-pass", action="store_true", help="Require a promoted device-pass proof.")
    args = parser.parse_args(argv)

    artifact_path = Path(args.artifact)
    if not artifact_path.is_absolute():
        artifact_path = ROOT / artifact_path
    evidence_root = Path(args.evidence_root) if args.evidence_root else None
    if evidence_root is not None and not evidence_root.is_absolute():
        evidence_root = ROOT / evidence_root

    artifact = load_json(artifact_path)
    if args.expect_planned_gap:
        validate_planned_gap(artifact)
    else:
        validate_device_pass(artifact, artifact_path=artifact_path, evidence_root=evidence_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

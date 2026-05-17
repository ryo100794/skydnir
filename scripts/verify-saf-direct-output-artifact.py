#!/usr/bin/env python3
"""Host-side verifier for SAF direct-output device artifacts.

The device smoke script can emit a JSON artifact, but a top-level
``Success=true`` is not enough for promotion.  This verifier re-checks the
contract encoded in the artifact and only accepts proof that a real container
wrote through ``/documents`` and that the selected Documents/SAF backend (not
only the app-private mirror) contains the write/rename/unlink/sidecar/path
validation evidence.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

FALLBACK_PAYLOAD_STATES = {"mirror-fallback-after-saf-error"}
DIRECT_PAYLOAD_STATES = {"saf-synced-mirror-evicted", "mirror-present"}
NON_PROMOTING_STATUSES = {"planned-skip", "planned-gap", "skipped", "skip", "blocked", "fallback"}
REQUIRED_CASES = (
    "container_documents_write",
    "direct_saf_payload",
    "mirror_not_accepted_as_direct",
    "sidecar_metadata",
    "rename_stat",
    "unlink",
    "direct_write_path_validation",
)


class VerificationError(ValueError):
    pass


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise VerificationError(f"missing SAF direct-output artifact: {path}")
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise VerificationError(f"invalid SAF direct-output JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise VerificationError("SAF direct-output artifact must be a JSON object")
    return data


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def _case(artifact: dict[str, Any], name: str) -> dict[str, Any]:
    cases = artifact.get("Cases")
    _require(isinstance(cases, dict), "artifact Cases must be an object")
    value = cases.get(name)
    _require(isinstance(value, dict), f"artifact missing case object: {name}")
    return value


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _bool(value: Any) -> bool:
    return value is True


def _sidecar_has_provider_evidence(sidecar: Any, expected_relative: str) -> bool:
    if not isinstance(sidecar, dict):
        return False
    if sidecar.get("unixMetadata") != "sidecar" or sidecar.get("relativePath") != expected_relative:
        return False
    # Older smoke artifacts at least expose providerEvidence/conflictState on
    # current sidecars.  The verifier makes that evidence promotion-critical so
    # sidecar-only fake success cannot hide provider divergence.
    provider = sidecar.get("providerEvidence")
    return isinstance(provider, dict) and bool(provider.get("sha256")) and bool(sidecar.get("conflictState"))


def _verify_promoting_status(artifact: dict[str, Any]) -> None:
    status = _text(artifact.get("Status")).lower()
    _require(status not in NON_PROMOTING_STATUSES, f"non-promoting SAF direct-output status is not a pass: {status or '<missing>'}")
    _require(artifact.get("Success") is True, "SAF direct-output artifact does not report Success=true")
    _require(status == "pass", f"SAF direct-output artifact Status must be pass, got {status or '<missing>'}")
    _require(artifact.get("NoFakeSuccess") is True, "SAF direct-output artifact missing NoFakeSuccess=true marker")
    failures = artifact.get("Failures")
    _require(failures in ([], None), f"SAF direct-output artifact lists failures: {failures}")


def _verify_container(artifact: dict[str, Any], require_container: bool) -> None:
    container = _text(artifact.get("Container"))
    if require_container or artifact.get("RequireContainer") is True:
        _require(container, "real container is required for promoted SAF direct-output evidence")
    case = _case(artifact, "container_documents_write")
    _require(_bool(case.get("Attempted")), "container /documents case was not attempted")
    _require(_bool(case.get("Success")), "container /documents case did not succeed")
    _require(_text(case.get("Container")) == container and bool(container), "container case does not match a real artifact Container")
    _require(_text(case.get("DocumentsMount")) == _text(artifact.get("DocumentsMount")) == "/documents", "container case must target /documents")
    _require(case.get("ExitCode") == 0, "container /documents exec did not exit 0")


def _verify_direct_payload(artifact: dict[str, Any]) -> None:
    selected_host = _text(artifact.get("SelectedHostPath"))
    _require(selected_host.startswith(("/storage/", "/sdcard/")), f"invalid SelectedHostPath for direct SAF evidence: {selected_host or '<missing>'}")
    case = _case(artifact, "direct_saf_payload")
    _require(_bool(case.get("Attempted")), "direct SAF payload case was not attempted")
    _require(_bool(case.get("Success")), "direct SAF payload case did not succeed")
    _require(_bool(case.get("DirectPayloadObserved")), "payload was not observed under the selected Documents/SAF host path")
    _require(_text(case.get("SelectedHostPath")) == selected_host, "direct payload case SelectedHostPath mismatches artifact")
    state = _text(case.get("PayloadState"))
    _require(state in DIRECT_PAYLOAD_STATES, f"direct payload has non-promoting payloadState: {state or '<missing>'}")
    _require(state not in FALLBACK_PAYLOAD_STATES, "fallback payloadState cannot promote SAF direct-output evidence")
    mirror_only = _bool(case.get("MirrorPayloadPresent")) and not _bool(case.get("DirectPayloadObserved"))
    _require(not mirror_only, "mirror-only payload evidence is not direct SAF output")


def _verify_mirror_policy(artifact: dict[str, Any]) -> None:
    case = _case(artifact, "mirror_not_accepted_as_direct")
    _require(_bool(case.get("Success")), "mirror rejection policy case did not succeed")
    _require(case.get("MirrorOnlyRejected") is False, "artifact contains mirror-only fake success evidence")
    policy = artifact.get("FallbackPolicy")
    _require(isinstance(policy, dict), "FallbackPolicy must be present")
    _require(policy.get("AllowedOnlyWhenExplicitlyRecorded") is True, "FallbackPolicy must require explicit fallback recording")
    _require(policy.get("FallbackRecorded") is not True, "fallback was recorded; fallback evidence is non-promoting")
    _require(policy.get("MirrorOnlyRejected") is not True, "mirror-only evidence was observed; not promotable")


def _verify_sidecars_and_file_ops(artifact: dict[str, Any]) -> None:
    sidecar_case = _case(artifact, "sidecar_metadata")
    _require(_bool(sidecar_case.get("Attempted")), "sidecar metadata case was not attempted")
    _require(_bool(sidecar_case.get("Success")), "sidecar metadata case did not succeed")
    write_rel = _text(_case(artifact, "direct_saf_payload").get("RelativePath"))
    rename_rel = _text(_case(artifact, "rename_stat").get("RelativePath"))
    write_sidecar = sidecar_case.get("WriteSidecar")
    rename_sidecar = sidecar_case.get("RenameSidecar")
    _require(_sidecar_has_provider_evidence(write_sidecar, write_rel), "write sidecar is missing Unix/provider/hash/conflict evidence")
    _require(_sidecar_has_provider_evidence(rename_sidecar, rename_rel), "rename sidecar is missing Unix/provider/hash/conflict evidence")

    rename = _case(artifact, "rename_stat")
    _require(_bool(rename.get("Attempted")), "rename/stat case was not attempted")
    _require(_bool(rename.get("Success")), "rename/stat case did not succeed with direct payload evidence")
    rename_state = _text(rename.get("PayloadState"))
    _require(rename_state in DIRECT_PAYLOAD_STATES, f"rename/stat has non-promoting payloadState: {rename_state or '<missing>'}")

    unlink = _case(artifact, "unlink")
    _require(_bool(unlink.get("Attempted")), "unlink case was not attempted")
    _require(_bool(unlink.get("Success")), "unlink absence was not proven")
    unlink_sidecar = unlink.get("UnlinkSidecar")
    _require(isinstance(unlink_sidecar, dict) and unlink_sidecar.get("relativePath") == _text(unlink.get("RelativePath")), "unlink sidecar evidence is missing or for the wrong path")


def _verify_path_validation(artifact: dict[str, Any]) -> None:
    validation = _case(artifact, "direct_write_path_validation")
    _require(_bool(validation.get("Attempted")), "direct-write path validation case was not attempted")
    _require(_bool(validation.get("Success")), "unsafe direct-write path was not rejected fail-closed")
    _require(_text(validation.get("RejectedTarget")) and ".." in _text(validation.get("RejectedTarget")), "path validation case must include traversal target")
    _require(validation.get("PathValidationPolicy") == "fail-closed", "path validation policy must be fail-closed")
    result = validation.get("Result")
    _require(isinstance(result, dict), "path validation Result must be present")
    _require(result.get("Success") is False, "invalid direct-write target must report Success=false")
    _require(result.get("Fallback") is False, "invalid direct-write target must not fall back")
    _require(result.get("PathValidationPolicy") == "fail-closed", "invalid direct-write target must record fail-closed policy")
    _require("invalid target path" in _text(result.get("Error")), "invalid direct-write target must record path validation error")


def verify(path: Path, *, require_container: bool = True) -> None:
    artifact = _read_json(path)
    _require(artifact.get("Kind") == "saf-direct-output-gate", "artifact Kind must be saf-direct-output-gate")
    cases = artifact.get("Cases")
    _require(isinstance(cases, dict), "artifact Cases must be an object")
    missing = [name for name in REQUIRED_CASES if name not in cases]
    _require(not missing, "artifact missing required SAF direct-output cases: " + ", ".join(missing))
    _verify_promoting_status(artifact)
    _verify_container(artifact, require_container=require_container)
    _verify_direct_payload(artifact)
    _verify_mirror_policy(artifact)
    _verify_sidecars_and_file_ops(artifact)
    _verify_path_validation(artifact)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path, help="Path to docs/test/saf-direct-output-latest.json or equivalent")
    parser.add_argument("--no-require-container", action="store_true", help="Do not require top-level RequireContainer=true, but still require real container evidence for pass")
    args = parser.parse_args(argv)
    try:
        verify(args.artifact, require_container=not args.no_require_container)
    except VerificationError as exc:
        print(f"SAF direct-output artifact verification failed: {exc}", file=sys.stderr)
        return 1
    print(f"SAF direct-output artifact verification passed: {args.artifact}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

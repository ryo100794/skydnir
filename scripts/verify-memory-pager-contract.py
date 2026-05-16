#!/usr/bin/env python3
"""Host/static feasibility gate for app-level virtual memory pager promotion.

This verifier deliberately does not implement a pager.  It only prevents
planned-gap or dry-run evidence from being interpreted as a promoted APK-level
virtual-memory feature.  A future promoted artifact must prove every syscall and
fallback capability listed in REQUIRED_CAPABILITIES.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
TODO = ROOT / "docs/plan/TODO.md"
PROBE_DOC = ROOT / "docs/test/APK_MEMORY_PAGER_PROBE.md"
MANAGED_ARTIFACT = ROOT / "docs/test/apk-memory-pager-managed-latest.json"
TRANSPARENT_ARTIFACT = ROOT / "docs/test/apk-memory-pager-transparent-latest.json"

SCHEMA = "pdocker.memory-pager.feasibility-gate.v1"
NON_PROMOTING_STATUSES = {
    "planned-gap",
    "dry-run",
    "blocked",
    "blocked-device",
    "failed",
    "fail",
    "skip",
    "skipped",
}
PASS_STATUSES = {"pass", "promoted"}

REQUIRED_CAPABILITIES = {
    "mmap_fixed_mapping": (
        "proof that the APK process can reserve/remap the exact managed virtual "
        "address range, for example MAP_FIXED_NOREPLACE or an equivalent same-address replay"
    ),
    "mprotect": "proof that page protections can be changed for exact managed pages",
    "fault_event": "proof of either SIGSEGV handler/ptrace stop semantics or usable userfaultfd",
    "file_backed_spill": "proof that evicted pages are written to and restored from app-private file backing",
    "unsupported_kernel_fallback": (
        "proof that unsupported Android kernels/devices stay disabled or fail closed without fake success"
    ),
}

TODO_TOKENS = [
    "Task H virtual memory feasibility gate",
    "planned-gap",
    "non-promoting",
    "mmap fixed mapping",
    "mprotect",
    "SIGSEGV handler or userfaultfd",
    "file-backed spill",
    "safe fallback on unsupported Android kernels",
    "No native pager code is promoted by this gate",
]

PROBE_TOKENS = [
    "pager-probe:mmap_prot_none=ok",
    "pager-probe:mprotect_rw=ok",
    "pager-probe:ptrace_sigsegv_stop=ok",
    "pager-probe:ptrace_getsiginfo=ok",
    "pager-probe:userfaultfd=blocked",
]


def fail(message: str) -> None:
    raise SystemExit(f"FAIL: {message}")


def ok(message: str) -> None:
    print(f"ok: {message}")


def require(name: str, condition: bool) -> None:
    if not condition:
        fail(name)
    ok(name)


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        fail(f"missing artifact: {path.relative_to(ROOT)}")
    except json.JSONDecodeError as exc:
        fail(f"invalid json in {path.relative_to(ROOT)}: {exc}")
    if not isinstance(data, dict):
        fail(f"artifact must be a JSON object: {path.relative_to(ROOT)}")
    return data


def _bool_at(data: dict[str, Any], dotted: str) -> bool:
    current: Any = data
    for part in dotted.split("."):
        if not isinstance(current, dict):
            return False
        current = current.get(part)
    return current is True


def _capability_ok(evidence: dict[str, Any], name: str) -> bool:
    value = evidence.get(name)
    if value is True:
        return True
    if not isinstance(value, dict):
        return False
    # Accept a few explicit shapes so future device artifacts can be clear
    # without forcing one native design today.
    if value.get("supported") is True or value.get("available") is True:
        return True
    if value.get("result") in {"ok", "pass", "supported", "available"}:
        return True
    if value.get("ok") is True or value.get("proven") is True:
        return True
    return False


def _fault_event_ok(evidence: dict[str, Any]) -> bool:
    if _capability_ok(evidence, "fault_event"):
        return True
    sigsegv = evidence.get("sigsegv_handler") or evidence.get("sigsegv") or evidence.get("ptrace_sigsegv")
    userfaultfd = evidence.get("userfaultfd")
    return _capability_ok({"sigsegv": sigsegv}, "sigsegv") or _capability_ok({"userfaultfd": userfaultfd}, "userfaultfd")


def validate_artifact(data: dict[str, Any]) -> list[str]:
    """Return validation errors for a feasibility-gate artifact."""

    errors: list[str] = []
    schema = data.get("schema")
    status = data.get("status")
    success = data.get("success")
    stable = data.get("stable_checkpoint_eligible")
    promotes = bool(data.get("promotes_app_virtual_memory")) or bool(data.get("promotes_memory_pager"))
    promoted_claim = status in PASS_STATUSES or success is True or stable is True or promotes

    if schema != SCHEMA:
        errors.append(f"schema must be {SCHEMA}")
    if status not in PASS_STATUSES | NON_PROMOTING_STATUSES:
        errors.append(f"invalid status {status!r}")

    if status in NON_PROMOTING_STATUSES:
        if success is not False:
            errors.append("non-promoting artifacts must set success=false")
        if stable is not False:
            errors.append("non-promoting artifacts must set stable_checkpoint_eligible=false")
        if promotes:
            errors.append("non-promoting artifacts must not set promotes_app_virtual_memory/promotes_memory_pager")
        # Planned-gap records are allowed to lack syscall proof because their job
        # is to preserve risk, not to promote support.
        return errors

    if promoted_claim:
        if success is not True:
            errors.append("promoted artifacts must set success=true")
        if stable is not True:
            errors.append("promoted artifacts must set stable_checkpoint_eligible=true")
        evidence = data.get("syscall_capability_evidence")
        if not isinstance(evidence, dict):
            errors.append("promoted artifacts require syscall_capability_evidence object")
            evidence = {}
        for name in REQUIRED_CAPABILITIES:
            if name == "fault_event":
                if not _fault_event_ok(evidence):
                    errors.append("missing syscall_capability_evidence.fault_event: need SIGSEGV handler/ptrace or userfaultfd proof")
            elif not _capability_ok(evidence, name):
                errors.append(f"missing syscall_capability_evidence.{name}: {REQUIRED_CAPABILITIES[name]}")
        if not _bool_at(data, "fallback.unsupported_android_kernel.safe") and not _capability_ok(evidence, "unsupported_kernel_fallback"):
            errors.append("missing fallback.unsupported_android_kernel.safe=true or equivalent unsupported_kernel_fallback proof")
    return errors


def validate_artifact_path(path: Path) -> None:
    errors = validate_artifact(read_json(path))
    if errors:
        fail(f"{path}: " + "; ".join(errors))
    ok(f"{path} cannot create an unsupported virtual-memory promotion")


def check_current_artifact_is_non_promoting(path: Path) -> None:
    data = read_json(path)
    status = data.get("status")
    require(
        f"{path.relative_to(ROOT)} is non-promoting planned-gap/dry-run evidence",
        status in NON_PROMOTING_STATUSES
        and data.get("stable_checkpoint_eligible", False) is not True
        and data.get("promotes_app_virtual_memory", False) is not True
        and data.get("promotes_memory_pager", False) is not True,
    )


def run_negative_self_tests() -> None:
    incomplete_promotion = {
        "schema": SCHEMA,
        "status": "pass",
        "success": True,
        "stable_checkpoint_eligible": True,
        "promotes_app_virtual_memory": True,
        "syscall_capability_evidence": {
            "mprotect": True,
            "sigsegv_handler": {"supported": True},
        },
    }
    errors = validate_artifact(incomplete_promotion)
    for token in ["mmap_fixed_mapping", "file_backed_spill", "unsupported_kernel_fallback"]:
        require(f"negative self-test rejects promotion without {token}", any(token in error for error in errors))

    planned_gap = {
        "schema": SCHEMA,
        "status": "planned-gap",
        "success": False,
        "stable_checkpoint_eligible": False,
        "summary": "No native pager implementation is promoted by this host/static gate.",
    }
    require("planned-gap fixture stays valid without syscall proof", not validate_artifact(planned_gap))


def run_static_checks() -> None:
    todo = TODO.read_text(encoding="utf-8")
    probe = PROBE_DOC.read_text(encoding="utf-8")
    missing_todo = [token for token in TODO_TOKENS if token not in todo]
    require("TODO records Task H as a non-promoting planned-gap feasibility gate", not missing_todo)
    missing_probe = [token for token in PROBE_TOKENS if token not in probe]
    require("existing probe evidence covers only the current ptrace/mprotect baseline", not missing_probe)
    check_current_artifact_is_non_promoting(MANAGED_ARTIFACT)
    check_current_artifact_is_non_promoting(TRANSPARENT_ARTIFACT)
    run_negative_self_tests()
    print("required promotion capabilities:")
    for name, description in REQUIRED_CAPABILITIES.items():
        print(f"- {name}: {description}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validate-artifact", type=Path, help="validate a future feasibility/promotion artifact")
    args = parser.parse_args()
    if args.validate_artifact:
        validate_artifact_path(args.validate_artifact)
        return 0
    run_static_checks()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

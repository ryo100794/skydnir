#!/usr/bin/env python3
"""Host/static and device-gated OOM/LMK survival verification."""
from __future__ import annotations

import argparse, importlib.machinery, importlib.util, json, os, tempfile, uuid
from pathlib import Path
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
DIRECT = ROOT / "app/src/main/cpp/pdocker_direct_exec.c"
PDOCKERD = ROOT / "docker-proot-setup/bin/pdockerd"
ASSET_PDOCKERD = ROOT / "app/src/main/assets/pdockerd/pdockerd"
MANAGED_SCRIPT = ROOT / "scripts/android-memory-pager-managed-poc.sh"
TRANSPARENT_SCRIPT = ROOT / "scripts/android-memory-pager-transparent-poc.sh"
PAGER_DOC = ROOT / "docs/design/APK_MEMORY_PAGER.md"
OOM_DOC = ROOT / "docs/design/RUNTIME_OOM_SURVIVAL.md"
PROBE_DOC = ROOT / "docs/test/APK_MEMORY_PAGER_PROBE.md"
LEDGER = ROOT / "docs/test/CI_GATE_LEDGER.md"
MANIFEST = ROOT / "tests/test_driver_manifest.json"
NON_PROMOTING = {"planned-gap", "blocked", "blocked-device", "failed", "fail", "skip", "skipped"}

def fail(message: str) -> None: raise SystemExit(f"FAIL: {message}")
def ok(message: str) -> None: print(f"ok: {message}")
def require(name: str, condition: bool) -> None:
    if not condition: fail(name)
    ok(name)
def require_tokens(name: str, text: str, tokens: list[str]) -> None:
    missing = [t for t in tokens if t not in text]
    require(f"{name}: {', '.join(missing)}" if missing else name, not missing)

def load_pdockerd(home: Path):
    name = f"pdockerd_oom_lmk_gate_{uuid.uuid4().hex}"
    loader = importlib.machinery.SourceFileLoader(name, str(PDOCKERD))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    env = {"PDOCKER_HOME": str(home), "PDOCKER_TMP_DIR": str(home / "tmp"), "PDOCKER_RUNTIME_BACKEND": "direct", "PDOCKER_DIRECT_EXECUTOR": ""}
    with mock.patch.dict(os.environ, env, clear=False): loader.exec_module(module)
    return module

def run_pdockerd_backend_death_fixture() -> None:
    with tempfile.TemporaryDirectory() as td:
        mod = load_pdockerd(Path(td) / "pdocker")
        cid = "oomlmkfixture"; cdir = Path(mod.CONTAINERS_DIR) / cid; cdir.mkdir(parents=True)
        state: dict[str, Any] = {"Id": cid, "Name": "/oomlmkfixture", "Config": {"Env": []}, "NetworkSettings": {"Ports": {}}, "State": {"Running": False, "Status": "exited", "ExitCode": 137, "PdockerRawReturnCode": -9, "PdockerSignal": 9}}
        evidence = mod._classify_container_memory_exit(state)
        require("backend SIGKILL/137 is classified as suspected LMK/backend death", evidence["ExitClassification"] == "sigkill-or-lmk-suspected")
        require("backend death evidence disables live UI state", evidence["UiLiveStateAllowed"] is False and state["State"]["OOMKilled"] is True)
        state["State"].update({"ExitCode": 12, "PdockerRawReturnCode": 12, "PdockerSignal": 0})
        (cdir / "memory-summary.json").write_text(json.dumps({"summary_schema": "pdocker.memory-telemetry-summary.v1", "classification": "allocation_denied_enomem", "classifier_reason": "guard-denial", "ui_live_state_allowed": False}), encoding="utf-8")
        evidence = mod._classify_container_memory_exit(state)
        require("guard ENOMEM summary remains diagnosable and not LMK", evidence["ExitClassification"] == "allocation_denied_enomem" and evidence["LmkSuspected"] is False and state["State"]["OOMKilled"] is False)
        state["State"].update({"ExitCode": 137, "PdockerRawReturnCode": -9, "PdockerSignal": 9})
        evidence = mod._classify_container_memory_exit(state)
        require("actual backend SIGKILL wins over stale allocation summary", evidence["ExitClassification"] == "sigkill-or-lmk-suspected" and evidence["LmkSuspected"] is True)

def validate_gate_artifact(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []; status = data.get("status"); success = data.get("success")
    if data.get("schema") != "pdocker.oom-lmk-survival-gate.v1": errors.append("schema must be pdocker.oom-lmk-survival-gate.v1")
    if status not in {"pass", "planned-gap", "blocked-device", "failed", "fail"}: errors.append(f"invalid status {status!r}")
    if status == "pass":
        if success is not True: errors.append("pass artifacts must set success=true")
        proof = data.get("proof") or {}
        for key in ["large_allocation_denial_diagnosed", "backend_death_replay_diagnosed", "stale_running_guard_proven", "bounded_artifacts_verified"]:
            if proof.get(key) is not True: errors.append(f"pass artifact missing proof.{key}=true")
        if data.get("stable_checkpoint_eligible") is True and data.get("device_promotion_evidence") is not True: errors.append("stable_checkpoint_eligible=true requires explicit device_promotion_evidence=true")
    else:
        if success is not False: errors.append("non-passing OOM/LMK artifacts must set success=false")
        if data.get("stable_checkpoint_eligible") is not False: errors.append("planned-gap/blocked/fail artifacts must set stable_checkpoint_eligible=false")
    return errors

def validate_artifact_path(path: Path) -> None:
    errors = validate_gate_artifact(json.loads(path.read_text(encoding="utf-8")))
    if errors: fail(f"{path}: " + "; ".join(errors))
    ok(f"{path} artifact cannot create false success")

def run_negative_artifact_self_tests() -> None:
    require("negative self-test rejects planned-gap success=true", bool(validate_gate_artifact({"schema":"pdocker.oom-lmk-survival-gate.v1","status":"planned-gap","success":True,"stable_checkpoint_eligible":False})))
    require("negative self-test rejects pass without backend-death proof", bool(validate_gate_artifact({"schema":"pdocker.oom-lmk-survival-gate.v1","status":"pass","success":True,"stable_checkpoint_eligible":True,"proof":{"large_allocation_denial_diagnosed":True}})))

def write_device_plan_artifact(path: Path) -> None:
    record = {"schema":"pdocker.oom-lmk-survival-gate.v1","status":"planned-gap","success":False,"stable_checkpoint_eligible":False,"device_promotion_evidence":False,"non_promoting_statuses":sorted(NON_PROMOTING),"summary":"Host/static contracts exist, but controlled connected-device LMK/backend-death replay is not implemented/promoted yet.","proof":{"large_allocation_denial_diagnosed":False,"backend_death_replay_diagnosed":False,"stale_running_guard_proven":False,"bounded_artifacts_verified":False},"host_static_gate":"python3 scripts/verify-oom-lmk-survival-gate.py","device_commands":["bash scripts/android-memory-pager-managed-poc.sh","bash scripts/android-memory-pager-transparent-poc.sh","future controlled LMK/backend death replay from inside the APK without force-stop during the sample window"],"artifacts":["docs/test/apk-memory-pager-managed-latest.json","docs/test/apk-memory-pager-transparent-latest.json","docs/test/oom-lmk-survival-latest.json"]}
    path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(record, indent=2, sort_keys=True)+"\n", encoding="utf-8"); validate_artifact_path(path); print(path)

def run_static_checks() -> None:
    direct = DIRECT.read_text(encoding="utf-8"); pdockerd = PDOCKERD.read_text(encoding="utf-8"); asset = ASSET_PDOCKERD.read_text(encoding="utf-8") if ASSET_PDOCKERD.exists() else ""
    managed = MANAGED_SCRIPT.read_text(encoding="utf-8"); transparent = TRANSPARENT_SCRIPT.read_text(encoding="utf-8")
    docs = PAGER_DOC.read_text(encoding="utf-8") + "\n" + OOM_DOC.read_text(encoding="utf-8") + "\n" + PROBE_DOC.read_text(encoding="utf-8")
    ledger = LEDGER.read_text(encoding="utf-8"); manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    require_tokens("direct executor records diagnosable large allocation decisions", direct, ["pdocker.memory-telemetry-ring.v1","pdocker.memory-telemetry-summary.v1","last_large_allocation","syscall","requested_bytes","accepted","errno","threshold_bytes","mem_available_at_decision_bytes","swap_free_at_decision_bytes","classification","guard_denial_count","allocation_denied_enomem","telemetry_persistence_failed","summary_write_degraded"])
    require_tokens("pdockerd classifies backend death stale-safe", pdockerd, ["def _classify_container_memory_exit","sigkill-or-lmk-suspected","sigkill-or-exit-137-without-kernel-prekill-notice","UiLiveStateAllowed",'"/system/memory-pressure"'])
    require("asset pdockerd mirror matches source", not asset or asset == pdockerd)
    require_tokens("managed pager device script cannot pass without exact rc and metrics", managed, ["exact_rc=","missing_metrics","status = (",'"force_stops_app": False','"blocked-device"'])
    require_tokens("transparent pager device script requires bounded memory artifacts", transparent, ["exact_rc=","__PDOCKER_MEMORY_RING_BEGIN__","__PDOCKER_MEMORY_SUMMARY_BEGIN__","pdocker.memory-telemetry-ring.v1","pdocker.memory-telemetry-summary.v1","artifact_errors",'"force_stops_app": False','"blocked-device"'])
    require_tokens("OOM/LMK docs keep planned gap explicit and evidence-shaped", docs, ["Planned gap","pdocker.memory-oom-lmk-diagnostics.v1","last_large_allocation","requested bytes","MemAvailable","SwapFree","rss_bytes","pss_bytes","last_known_progress","lmk_suspected_classifier","ui_live_state_allowed","must not show `running`, `Up`, or an active spinner solely"])
    require_tokens("CI ledger says OOM/LMK evidence is non-promoting until device replay", ledger, ["OOM/LMK","Unmet planned gap for LMK replay","success=false","stable checkpoint","must not silently pass"])
    require("manifest treats planned-gap as non-promoting", "planned-gap" in set(manifest.get("policy",{}).get("non_promoting_statuses") or []))
    require("android memory pager lane is not stable-checkpoint eligible", manifest["lanes"]["android-memory-pager"].get("stable_checkpoint_eligible") is False)
    require("host-smoke runs OOM/LMK survival static gate", "verify-oom-lmk-survival-gate" in {c["id"] for c in manifest["lanes"]["host-smoke"].get("commands", [])})
    require("android memory pager lane contains non-promoting OOM/LMK device gate", "oom-lmk-survival-device-gate" in {c["id"] for c in manifest["lanes"]["android-memory-pager"].get("commands", [])})

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--validate-artifact", type=Path); parser.add_argument("--device-plan-artifact", type=Path); args = parser.parse_args()
    run_static_checks(); run_pdockerd_backend_death_fixture(); run_negative_artifact_self_tests()
    if args.validate_artifact: validate_artifact_path(args.validate_artifact)
    if args.device_plan_artifact: write_device_plan_artifact(args.device_plan_artifact); return 2
    return 0
if __name__ == "__main__": raise SystemExit(main())

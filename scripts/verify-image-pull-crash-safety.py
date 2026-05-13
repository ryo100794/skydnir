#!/usr/bin/env python3
"""Static crash-safety contract for pdockerd image pull publication.

This verifier is intentionally host-only: it does not pull from a registry or
fake a successful image. It checks that the daemon source keeps the pull/layer
write path staged, content-verified, atomically published, and startup-pruned so
crash residue is not later treated as a valid image or layer.
"""

from __future__ import annotations

import ast
import json
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PDOCKERD = ROOT / "docker-proot-setup/bin/pdockerd"
ASSET_PDOCKERD = ROOT / "app/src/main/assets/pdockerd/pdockerd"
TODO = ROOT / "docs/plan/TODO.md"
COMPAT = ROOT / "docs/test/COMPATIBILITY.md"
DEVICE_RUNNER = ROOT / "scripts/verify/runner/image_pull_crash_safety_device.py"
DEVICE_SIDE_RUNNER = ROOT / "scripts/verify/runner/image-pull-crash-safety-device.sh"
DEVICE_GATE_DOC = ROOT / "docs/test/IMAGE_PULL_CRASH_SAFETY_DEVICE_GATE.md"


def fail(message: str) -> None:
    raise SystemExit(f"FAIL: {message}")


def require(name: str, condition: bool) -> None:
    if not condition:
        fail(name)
    print(f"ok: {name}")


def function_source(tree: ast.Module, source: str, name: str) -> str:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    fail(f"missing function {name}")
    return ""


def ordered(text: str, *needles: str) -> bool:
    pos = -1
    for needle in needles:
        idx = text.find(needle, pos + 1)
        if idx < 0:
            return False
        pos = idx
    return True


def check_source(path: Path) -> None:
    source = path.read_text()
    tree = ast.parse(source, filename=str(path))

    pull = function_source(tree, source, "pull_image")
    extract = function_source(tree, source, "_extract_layer_tar")
    prune = function_source(tree, source, "prune_build_artifacts")
    main = function_source(tree, source, "main")

    require(f"{path.name}: image pull uses .pull staging", 'stage = f"{d}.pull-' in pull)
    require(f"{path.name}: existing tag is moved to .old backup before publish",
            'backup = f"{d}.old-' in pull and ordered(pull, "if os.path.exists(d):", "os.replace(d, backup)", "os.replace(stage, d)"))
    require(f"{path.name}: failed replacement restores old tag",
            ordered(pull, "except Exception:", "if os.path.exists(backup) and not os.path.exists(d):", "os.replace(backup, d)"))
    require(f"{path.name}: pull stage is removed on failure",
            "shutil.rmtree(stage, ignore_errors=True)" in pull)
    require(f"{path.name}: layer diff_id is verified before extract",
            ordered(pull, "actual = _sha256_file(tmp_tar)", "if actual != bare:", "_extract_layer_tar(tmp_tar, bare)"))
    require(f"{path.name}: tag metadata is written before atomic publish",
            ordered(pull, "_save_image_manifest(stage, diff_ids_bare, config)", "merge_layers_into", 'open(os.path.join(stage, "image_ref")', 'open(os.path.join(stage, "pulled_at")', "os.replace(stage, d)"))

    require(f"{path.name}: layer extraction short-circuits only complete layers",
            "if _layer_exists(diff_id):" in extract)
    require(f"{path.name}: layer extraction uses .tmp staging",
            'tmp_ldir = f"{ldir}.tmp-' in extract)
    require(f"{path.name}: layer tree publishes atomically after meta.json",
            ordered(extract, 'open(os.path.join(tmp_ldir, "meta.json")', "json.dump(meta", "os.replace(tmp_ldir, ldir)"))
    require(f"{path.name}: failed layer extraction removes tmp stage",
            ordered(extract, "except Exception as e:", "shutil.rmtree(tmp_ldir, ignore_errors=True)"))

    require(f"{path.name}: startup/prune removes .pull image residue",
            'if ".pull-" in name:' in prune and "image-stage" in prune)
    require(f"{path.name}: startup/prune restores or discards .old image backups",
            'if ".old-" in name:' in prune and "os.replace(path, base_path)" in prune and "image-restore" in prune)
    require(f"{path.name}: startup/prune removes .tmp layer residue",
            'if ".tmp-" in name:' in prune and "layer-stage" in prune)
    require(f"{path.name}: startup/prune removes malformed partial layer dirs",
            "not _layer_exists(name)" in prune and "layer-partial" in prune)
    require(f"{path.name}: tmp blob/load/save residue is covered",
            "pdblob_" in prune and "pdload_" in prune and "pdsave_" in prune)
    require(f"{path.name}: daemon startup invokes crash-residue recovery",
            "prune_build_artifacts(min_age_seconds=30, skip_active=True)" in main)


def check_device_scenario_runner() -> None:
    require("interrupted-pull device scenario runner exists", DEVICE_RUNNER.exists())
    require("interrupted-pull device-side runner exists", DEVICE_SIDE_RUNNER.exists())
    with tempfile.TemporaryDirectory() as tmp:
        artifact = Path(tmp) / "image-pull-crash-safety.json"
        subprocess.run(
            [sys.executable, str(DEVICE_RUNNER), "--adb", "__missing_adb_for_static_gate__", "--artifact", str(artifact)],
            cwd=ROOT,
            check=True,
        )
        data = json.loads(artifact.read_text())

    require("device scenario artifact never fakes success without evidence",
            data.get("status") == "planned-gap" and data.get("success") is False)
    require("device scenario records schema version and id",
            data.get("schema_version") == 2 and data.get("scenario_id") == "image.pull.interrupted-kill-restart")
    require("device scenario points back to static plan gate",
            data.get("plan_gate") == "python3 scripts/verify-image-pull-crash-safety.py")
    require("device scenario records command plan",
            isinstance(data.get("commands"), list) and len(data["commands"]) >= 8)
    require("device scenario records concrete phases",
            data.get("phases") == ["prepare-residue", "kill-daemon", "restart-and-probe", "cleanup"])
    coverage = data.get("coverage") or {}
    require("device scenario separates synthetic recovery from live network-pull coverage",
            coverage.get("live_interrupted_network_pull") is False
            and {"residue_recovery", "daemon_kill_restart", "engine_negative_probe"} <= set(coverage))
    assertions = set((data.get("assertions") or {}).keys())
    require("device scenario records crash-safety assertions",
            {"old_tag_restored", "pull_stage_pruned", "tmp_layer_pruned",
             "partial_layer_pruned", "never_published_tag_rejected",
             "restored_tag_inspectable", "cleanup_removed_only_scenario_owned_paths"} <= assertions)
    for command in data["commands"]:
        tokens = shlex.split(command)
        require(f"device scenario command is tokenizable: {command}", bool(tokens))
        for token in tokens:
            if token.startswith(("scripts/", "tests/", "docs/", "docker-proot-setup/")):
                require(f"device scenario command path exists: {token}", (ROOT / token).exists())
    required_evidence = {
        "prepare_summary",
        "kill_summary",
        "restart_summary",
        "cleanup_summary",
        "daemon_log_before_kill",
        "daemon_log_after_restart",
        "store_listing_before_kill",
        "store_listing_after_restart",
        "image_inspect_after_restart",
        "never_image_inspect_after_restart",
    }
    require("device scenario artifact schema records required evidence fields",
            required_evidence <= set(data.get("artifact_schema", {}).get("evidence", {}).keys()))
    negative = "\n".join(data.get("negative_expected_conditions", []))
    require("device scenario records negative expected conditions",
            all(term in negative for term in [".pull-", ".tmp-", "old tag", "inspect", "run"]))
    cleanup = "\n".join(data.get("cleanup_policy", []))
    require("device scenario records cleanup policy",
            all(term in cleanup.lower() for term in ["collect", "unrelated", "success=false"]))
    remaining = "\n".join(data.get("remaining_gap", []))
    require("device scenario records remaining live-pull gap",
            "Live registry pull interruption" in remaining)

    side = DEVICE_SIDE_RUNNER.read_text()
    require("device-side runner prepares scenario-owned pull/old/tmp residues",
            all(term in side for term in [".pull-$TOKEN", ".old-$TOKEN", ".tmp-$TOKEN", "prepare-residue"]))
    require("device-side runner has kill and restart phases",
            "kill-daemon" in side and "restart-and-probe" in side and "pkill -TERM -f pdockerd" in side)
    require("device-side runner probes restored and never-published tags",
            "inspect-restored.raw" in side and "inspect-never.raw" in side)
    require("device-side cleanup is scenario-token scoped",
            "rm -rf \\" in side and "$IMG_BASE" in side and "$NEVER_BASE" in side and "$TOKEN" in side)
    forbidden_cleanup = [
        "rm -rf files/pdocker",
        "rm -rf pdocker/images",
        "rm -rf pdocker/layers",
        "rm -rf /data",
        "rm -rf /sdcard",
    ]
    require("device-side runner avoids destructive broad cleanup",
            not any(term in side for term in forbidden_cleanup))

    require("image pull crash-safety device gate doc exists", DEVICE_GATE_DOC.exists())
    doc = DEVICE_GATE_DOC.read_text()
    require("device gate doc records concrete phases and remaining gap",
            all(term in doc for term in ["prepare-residue", "kill-daemon", "restart-and-probe", "cleanup", "remaining gap"]))


def main() -> int:
    check_source(PDOCKERD)
    if ASSET_PDOCKERD.exists():
        require("asset pdockerd is synchronized with setup pdockerd",
                ASSET_PDOCKERD.read_text() == PDOCKERD.read_text())
        check_source(ASSET_PDOCKERD)
    todo = TODO.read_text()
    compat = COMPAT.read_text()
    require("TODO records image pull crash-safety verifier",
            "verify-image-pull-crash-safety.py" in todo)
    require("compatibility doc records remaining interrupted-pull device gap",
            "Interrupted-pull device kill" in compat)
    check_device_scenario_runner()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Static crash-safety contract for pdockerd image pull publication.

This verifier is intentionally host-only: it does not pull from a registry or
fake a successful image. It checks that the daemon source keeps the pull/layer
write path staged, content-verified, atomically published, and startup-pruned so
crash residue is not later treated as a valid image or layer.
"""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PDOCKERD = ROOT / "docker-proot-setup/bin/pdockerd"
ASSET_PDOCKERD = ROOT / "app/src/main/assets/pdockerd/pdockerd"
TODO = ROOT / "docs/plan/TODO.md"
COMPAT = ROOT / "docs/test/COMPATIBILITY.md"


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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Generate the Skydnir rename inventory.

The Skydnir rename intentionally starts with an inventory instead of a broad
text replacement.  This tool scans tracked text files for the pdocker-family
tokens that carry different compatibility meanings and emits a deterministic
JSON/Markdown ledger for the migration plan.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
TOKENS = (
    "pdocker-android",
    "pdockerd",
    "PDocker",
    "PDOCKER",
    "pDocker",
    "pdocker",
)
TOKEN_RE = re.compile("|".join(re.escape(token) for token in TOKENS))
SELF_OUTPUT_NAMES = {
    "docs/maintenance/skydnir-rename-inventory-latest.json",
    "docs/maintenance/skydnir-rename-inventory-latest.md",
}


def git_lines(*args: str) -> list[str]:
    proc = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    return [line for line in proc.stdout.splitlines() if line]


def tracked_files() -> list[Path]:
    paths: list[Path] = []
    for raw in git_lines("ls-files"):
        if raw in SELF_OUTPUT_NAMES:
            continue
        path = ROOT / raw
        if path.is_file():
            paths.append(path)
    return paths


def read_text(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def classify(path: str, token: str, line: str) -> dict[str, Any]:
    lower_line = line.lower()
    suffix = Path(path).suffix
    category = "internal_reference"
    phase = "phase-5-internal-namespace"
    alias_required = True
    migration_required = False
    change_allowed_now = False
    rationale = "internal name; classify manually before changing"

    if path.startswith("docs/test/") or path.startswith("docs/release/builds/") or "/runs/" in path:
        category = "historical_evidence"
        phase = "do-not-rewrite-history"
        alias_required = False
        rationale = "committed evidence/history should remain readable"
    elif path == "docs/manual/SKYDNIR_MIGRATION.md":
        category = "documentation_reference"
        phase = "phase-1-or-historical-context"
        alias_required = False
        rationale = "migration guide intentionally names the legacy project and aliases"
    elif path.startswith("tests/"):
        category = "test_fixture"
        phase = "phase-0-guard"
        rationale = "tests should be updated only with compatibility expectations"
    elif token == "PDOCKER":
        category = "environment_variable"
        phase = "phase-5-dual-read-required"
        migration_required = True
        rationale = "PDOCKER_* env names require SKYDNIR_* dual-read before rename"
    elif "schema" in lower_line and "pdocker" in lower_line:
        category = "artifact_schema"
        phase = "do-not-rewrite-history"
        rationale = "schema names are compatibility contracts"
    elif "pdocker." in lower_line or "io.pdocker" in lower_line or "pdocker-prefixed" in lower_line:
        category = "artifact_schema"
        phase = "do-not-rewrite-history"
        rationale = "extension schema, label, or response field name; preserve until schema migration exists"
    elif "pdockerd.sock" in lower_line or "files/pdocker" in lower_line or "filesdir/pdocker" in lower_line or "/run/pdocker" in lower_line:
        category = "socket_or_storage_path"
        phase = "phase-4-or-later-migration-required"
        migration_required = True
        rationale = "socket/storage paths need compatibility and data migration"
    elif any(marker in lower_line for marker in ("pdocker/projects", "pdocker-exports", "pdocker/diagnostics")):
        category = "socket_or_storage_path"
        phase = "phase-4-or-later-migration-required"
        migration_required = True
        rationale = "project/export storage paths need compatibility and data migration"
    elif any(
        marker in lower_line
        for marker in (
            "pdocker-direct",
            "libpdocker",
            "pdocker-gpu-",
            "pdocker-media-",
            "pdocker-opencl-",
            "pdocker-vulkan-",
            "pdocker_gpu_",
            "pdocker_vulkan_",
            "pdocker_trace_",
            "pdocker_direct_",
            "scripts/pdocker",
            "docker.io/pdocker/",
            "pdocker-dev",
            "pdocker-smoke",
            "pdocker smoke",
            "pdocker-llama",
            "pdocker-service-",
            "pdocker-new-project",
            "pdocker-ui-it-ok",
            "/pdocker-",
            "/usr/local/bin/pdocker",
        )
    ):
        category = "internal_reference"
        phase = "phase-5-internal-namespace"
        rationale = "concrete helper/library/symbol name; defer until binary and ABI aliases exist"
    elif ".pdocker" in lower_line or "pdocker.yml" in lower_line:
        category = "config_path"
        phase = "phase-3-config-migration"
        migration_required = True
        rationale = "config path rename needs copy/backup migration"
    elif token == "pdockerd":
        category = "daemon_binary_or_service"
        phase = "phase-2-daemon-alias"
        rationale = "daemon rename needs skydnird alias and pdockerd wrapper"
    elif re.search(r"(^|[\\s`\"'])pdocker([\\s`\"']|$)", line):
        category = "cli_command"
        phase = "phase-2-cli-alias"
        rationale = "CLI rename needs skydnir command and pdocker wrapper"
    elif "io/github/ryo100794/pdocker" in lower_line:
        category = "android_ui_or_package_surface"
        phase = "phase-1-ui-copy-or-phase-4-package"
        migration_required = True
        rationale = "Android source path follows the current package namespace"
    elif path == "README.md" or path.startswith("docs/manual/") or path.startswith("docs/release/") or path.startswith("docs/showcase/"):
        category = "public_branding"
        phase = "phase-1-public-branding"
        alias_required = False
        change_allowed_now = True
        rationale = "public-facing copy can move to Skydnir first"
    elif path.startswith("app/src/") and suffix in {".kt", ".xml"}:
        category = "android_ui_or_package_surface"
        phase = "phase-1-ui-copy-or-phase-4-package"
        migration_required = "package" in lower_line or "io.github" in lower_line
        rationale = "UI copy may change; package/data identifiers need migration"
    elif path.startswith("docs/"):
        category = "documentation_reference"
        phase = "phase-1-or-historical-context"
        alias_required = False
        change_allowed_now = True
        rationale = "documentation reference; preserve historical context when applicable"

    return {
        "category": category,
        "phase": phase,
        "alias_required": alias_required,
        "migration_required": migration_required,
        "change_allowed_now": change_allowed_now,
        "rationale": rationale,
    }


def build_inventory(snapshot_date: str) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    skipped_binary = 0
    for path in tracked_files():
        rel = path.relative_to(ROOT).as_posix()
        text = read_text(path)
        if text is None:
            skipped_binary += 1
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            for match in TOKEN_RE.finditer(line):
                token = match.group(0)
                meta = classify(rel, token, line)
                entries.append({
                    "path": rel,
                    "line": line_number,
                    "column": match.start() + 1,
                    "token": token,
                    "category": meta["category"],
                    "phase": meta["phase"],
                    "alias_required": meta["alias_required"],
                    "migration_required": meta["migration_required"],
                    "change_allowed_now": meta["change_allowed_now"],
                })
    by_token = Counter(entry["token"] for entry in entries)
    by_category = Counter(entry["category"] for entry in entries)
    by_phase = Counter(entry["phase"] for entry in entries)
    return {
        "schema": "skydnir.rename.inventory.v1",
        "snapshot_date": snapshot_date,
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "git ls-files tracked text files",
        "tokens": list(TOKENS),
        "entry_count": len(entries),
        "skipped_binary_file_count": skipped_binary,
        "counts": {
            "by_token": dict(sorted(by_token.items())),
            "by_category": dict(sorted(by_category.items())),
            "by_phase": dict(sorted(by_phase.items())),
        },
        "entries": entries,
    }


def write_markdown(inventory: dict[str, Any], path: Path) -> None:
    counts = inventory["counts"]
    lines = [
        "# Skydnir Rename Inventory",
        "",
        f"Snapshot date: {inventory['snapshot_date']}.",
        "",
        "This generated ledger classifies tracked `pdocker`-family names before",
        "any public Skydnir rename work proceeds.  It is intentionally an",
        "inventory, not a replacement script.",
        "",
        f"- Entries: `{inventory['entry_count']}`",
        f"- Skipped binary files: `{inventory['skipped_binary_file_count']}`",
        "",
        "## Counts by Token",
        "",
        "| Token | Count |",
        "|---|---:|",
    ]
    for token, count in counts["by_token"].items():
        lines.append(f"| `{token}` | {count} |")
    lines.extend([
        "",
        "## Counts by Category",
        "",
        "| Category | Count |",
        "|---|---:|",
    ])
    for category, count in counts["by_category"].items():
        lines.append(f"| `{category}` | {count} |")
    lines.extend([
        "",
        "## Counts by Phase",
        "",
        "| Phase | Count |",
        "|---|---:|",
    ])
    for phase, count in counts["by_phase"].items():
        lines.append(f"| `{phase}` | {count} |")
    lines.extend([
        "",
        "## Next Action",
        "",
        "Start with `phase-1-public-branding` and `documentation_reference` rows.",
        "Do not rename `environment_variable`, `artifact_schema`,",
        "`socket_or_storage_path`, or Android package/data surfaces until the",
        "Skydnir compatibility aliases and migration tests exist.",
        "",
    ])
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-date", default="2026-05-27")
    parser.add_argument("--json-out", type=Path, default=Path("docs/maintenance/skydnir-rename-inventory-latest.json"))
    parser.add_argument("--md-out", type=Path, default=Path("docs/maintenance/skydnir-rename-inventory-latest.md"))
    args = parser.parse_args(argv)

    inventory = build_inventory(args.snapshot_date)
    json_out = args.json_out if args.json_out.is_absolute() else ROOT / args.json_out
    md_out = args.md_out if args.md_out.is_absolute() else ROOT / args.md_out
    json_out.parent.mkdir(parents=True, exist_ok=True)
    md_out.parent.mkdir(parents=True, exist_ok=True)
    # Keep the committed artifact readable by tools but compact enough for
    # branch pushes.  The Markdown summary carries the human-facing tables.
    json_out.write_text(
        json.dumps(inventory, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_markdown(inventory, md_out)
    print(json_out.relative_to(ROOT) if json_out.is_relative_to(ROOT) else json_out)
    print(md_out.relative_to(ROOT) if md_out.is_relative_to(ROOT) else md_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

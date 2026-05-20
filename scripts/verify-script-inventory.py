#!/usr/bin/env python3
"""Validate the top-level scripts inventory.

The inventory is deliberately separate from any future directory reshuffle:
top-level script paths remain stable public entrypoints until a wrapper/shim
exists and all references are migrated.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
MANIFEST = SCRIPTS / "script-inventory.json"
README = SCRIPTS / "README.md"
IGNORED_TOP_LEVEL = {
    "README.md",
    "script-inventory.json",
    "__pycache__",
}
REQUIRED_CATEGORIES = {
    "runtime-package-needed",
    "build-developer",
    "test-verification",
    "generated-maintenance",
    "obsolete-suspect",
}
REQUIRED_CATEGORY_TARGETS = {
    "runtime-package-needed": "scripts/runtime",
    "build-developer": "scripts/build",
    "test-verification": "scripts/test",
    "generated-maintenance": "scripts/maintenance",
    "obsolete-suspect": "scripts/obsolete-candidates",
}
REQUIRED_STABLE_ENTRYPOINTS = {
    "scripts/build-all.sh",
    "scripts/build-apk.sh",
    "scripts/verify-fast.sh",
    "scripts/verify-heavy.sh",
    "scripts/pdocker-test-driver.py",
    "scripts/android-selfdebug.sh",
}
ALLOWED_MIGRATION_ACTIONS = {
    "audit-delete-or-archive",
    "candidate-move-behind-wrapper",
    "migrated-behind-wrapper",
}
KNOWN_OBSOLETE_SUSPECTS = {
    "scripts/android-terminal-it-repro.sh",
    "scripts/wrap-ndk-box64.sh",
}
EXPECTED_TOP_LEVEL_SCRIPT_COUNT = 91
EXPECTED_SUBTREE_ENTRY_COUNT = 4
EXPECTED_CATEGORY_COUNTS = {
    "runtime-package-needed": 1,
    "build-developer": 9,
    "test-verification": 76,
    "generated-maintenance": 3,
    "obsolete-suspect": 2,
}

REFERENCE_SCAN_PREFIXES = (
    "docs/",
    ".github/workflows/",
)
REFERENCE_SCAN_TEST_SUFFIXES = (".json",)
REFERENCE_SCAN_TEST_NAME_FRAGMENT = "manifest"
REFERENCE_SCAN_ALLOWLIST = {
    "scripts/README.md",
    "scripts/script-inventory.json",
    "scripts/verify-script-inventory.py",
    "tests/test_script_inventory_audit.py",
}
SCRIPT_DOC_INVENTORY = ROOT / "docs" / "maintenance" / "SCRIPT_DOC_INVENTORY.md"


def is_reference_scan_path(path: str) -> bool:
    if path in REFERENCE_SCAN_ALLOWLIST:
        return False
    if path.startswith(REFERENCE_SCAN_PREFIXES):
        return True
    if path.startswith("tests/"):
        name = Path(path).name
        return (
            name.endswith(REFERENCE_SCAN_TEST_SUFFIXES)
            or REFERENCE_SCAN_TEST_NAME_FRAGMENT in name
        )
    return False


def tracked_reference_scan_files() -> list[Path]:
    try:
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=ROOT,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        candidates = (
            list((ROOT / "docs").rglob("*"))
            + list((ROOT / ".github" / "workflows").rglob("*"))
            + list((ROOT / "tests").rglob("*"))
        )
        return sorted(
            path
            for path in candidates
            if path.is_file()
            and is_reference_scan_path(path.relative_to(ROOT).as_posix())
        )

    paths: list[Path] = []
    for line in result.stdout.splitlines():
        if is_reference_scan_path(line):
            path = ROOT / line
            if path.is_file():
                paths.append(path)
    return paths


def find_script_references(
    script_paths: Iterable[str],
    scan_files: Iterable[Path],
) -> dict[str, list[str]]:
    script_path_list = sorted(set(script_paths))
    references = {path: [] for path in script_path_list}
    for scan_file in scan_files:
        rel = scan_file.relative_to(ROOT).as_posix()
        try:
            text = scan_file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = scan_file.read_text(encoding="utf-8", errors="ignore")
        for line_no, line in enumerate(text.splitlines(), start=1):
            for script_path in script_path_list:
                if script_path in line:
                    references[script_path].append(f"{rel}:{line_no}: {line.strip()}")
    return {path: hits for path, hits in references.items() if hits}


def validate_migrated_wrapper_reference_scan(migrated_paths: Iterable[str]) -> None:
    references = find_script_references(migrated_paths, tracked_reference_scan_files())
    if references:
        details = []
        for path, hits in sorted(references.items()):
            formatted_hits = "; ".join(hits[:5])
            extra = "" if len(hits) <= 5 else f"; ... +{len(hits) - 5} more"
            details.append(f"{path} -> {formatted_hits}{extra}")
        fail(
            "migrated wrappers still referenced outside inventory/README/verifier allowlist; "
            "migrate docs, workflows, or test manifests before retiring wrappers: "
            + " | ".join(details)
        )


def validate_candidate_duplicate_consistency(entries: Iterable[dict[str, Any]]) -> None:
    """Fail when a planned move already has a duplicate implementation.

    A top-level script and its candidate path may coexist only when the
    inventory marks the top-level file as a compatibility wrapper
    (`migrated-behind-wrapper`).  Otherwise duplicate implementations can drift
    silently: callers may hit the old top-level file while maintainers edit the
    new categorized path, or vice versa.
    """

    duplicates: list[str] = []
    for entry in entries:
        path = entry.get("path")
        migration = entry.get("migration") if isinstance(entry, dict) else None
        if not isinstance(path, str) or not isinstance(migration, dict):
            continue
        candidate_path = migration.get("candidate_path")
        action = migration.get("action")
        if not isinstance(candidate_path, str):
            continue
        if (ROOT / candidate_path).is_file() and action != "migrated-behind-wrapper":
            duplicates.append(f"{path} -> {candidate_path} action={action!r}")
    if duplicates:
        fail(
            "candidate implementation exists but inventory does not mark the "
            "top-level file as a migrated compatibility wrapper: "
            + "; ".join(sorted(duplicates))
        )


def validate_script_surface_budget(
    entries: list[dict[str, Any]],
    subtree_entries: Any,
    categories: Counter[str],
) -> None:
    """Fail when the script inventory surface changes without a focused update.

    This is intentionally a small, explicit budget rather than an automatic
    count.  Adding a new top-level entrypoint, moving helpers into subtrees, or
    reclassifying categories is allowed, but the same commit must update this
    budget and the scripts README so reviewers see the changed public surface.
    """

    if len(entries) != EXPECTED_TOP_LEVEL_SCRIPT_COUNT:
        fail(
            "top-level script inventory count changed without a verifier "
            f"budget update: expected={EXPECTED_TOP_LEVEL_SCRIPT_COUNT} "
            f"observed={len(entries)}"
        )
    if not isinstance(subtree_entries, list):
        fail("subtree_entries must be a list")
    if len(subtree_entries) != EXPECTED_SUBTREE_ENTRY_COUNT:
        fail(
            "subtree script inventory count changed without a verifier "
            f"budget update: expected={EXPECTED_SUBTREE_ENTRY_COUNT} "
            f"observed={len(subtree_entries)}"
        )
    observed = {category: categories.get(category, 0) for category in REQUIRED_CATEGORIES}
    if observed != EXPECTED_CATEGORY_COUNTS:
        fail(
            "script category budget changed without a verifier update: "
            f"expected={EXPECTED_CATEGORY_COUNTS} observed={observed}"
        )


def display_path(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def validate_maintenance_doc_sync(
    entries: list[dict[str, Any]],
    categories: Counter[str],
) -> None:
    """Keep the maintenance triage doc aligned with inventory drift.

    The maintenance note is intentionally prose, but it contains counts and
    obsolete-candidate names that reviewers use during cleanup.  If the public
    script surface changes, stale triage wording is almost as confusing as stale
    executable wrappers, so make the coupling explicit.
    """

    if not SCRIPT_DOC_INVENTORY.is_file():
        fail(f"missing maintenance script inventory doc: {display_path(SCRIPT_DOC_INVENTORY)}")
    text = SCRIPT_DOC_INVENTORY.read_text(encoding="utf-8")
    expected_phrases = {
        "runtime-package-needed": f"{categories.get('runtime-package-needed', 0)} top-level script",
        "build-developer": f"{categories.get('build-developer', 0)} top-level scripts",
        "test-verification": f"{categories.get('test-verification', 0)} top-level scripts",
        "generated-maintenance": f"{categories.get('generated-maintenance', 0)} entries",
        "obsolete-suspect": f"{categories.get('obsolete-suspect', 0)} tracked candidates",
    }
    missing_phrases = sorted(
        f"{category}: {phrase}"
        for category, phrase in expected_phrases.items()
        if phrase not in text
    )
    if missing_phrases:
        fail(
            f"{display_path(SCRIPT_DOC_INVENTORY)} category count wording is stale: "
            + "; ".join(missing_phrases)
        )

    obsolete_paths = [
        entry["path"]
        for entry in entries
        if entry.get("category") == "obsolete-suspect"
    ]
    missing_obsolete = sorted(
        path
        for path in obsolete_paths
        if Path(path).name not in text and path not in text
    )
    if missing_obsolete:
        fail(
            f"{display_path(SCRIPT_DOC_INVENTORY)} omits obsolete-suspect "
            "candidate(s): " + ", ".join(missing_obsolete)
        )


def is_executable(path: Path) -> bool:
    return path.stat().st_mode & 0o111 != 0


def validate_wrapper(path: str, candidate_path: str) -> None:
    wrapper_path = ROOT / path
    implementation_path = ROOT / candidate_path
    if not wrapper_path.is_file():
        fail(f"{path} migrated wrapper missing")
    if not implementation_path.is_file():
        fail(f"{path} migrated implementation missing: {candidate_path}")
    if not is_executable(wrapper_path):
        fail(f"{path} migrated wrapper is not executable")
    try:
        wrapper_text = wrapper_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        fail(f"{path} migrated wrapper is not UTF-8 text")
    if candidate_path not in wrapper_text:
        fail(f"{path} wrapper does not reference migrated implementation {candidate_path}")

    first_line = wrapper_text.splitlines()[0] if wrapper_text.splitlines() else ""
    if path.endswith(".sh"):
        required_snippets = [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            'ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"',
            f'exec "$ROOT/{candidate_path}" "$@"',
        ]
        missing = [snippet for snippet in required_snippets if snippet not in wrapper_text]
        if missing:
            fail(f"{path} shell wrapper is missing required structure: {missing}")
    elif path.endswith(".py"):
        if first_line != "#!/usr/bin/env python3":
            fail(f"{path} python wrapper must start with a python3 shebang")
        required_snippets = [
            "import runpy",
            "import sys",
            f'TARGET_REL = "{candidate_path}"',
            "sys.argv[0] = str(TARGET)",
            'runpy.run_path(str(TARGET), run_name="__main__")',
        ]
        missing = [snippet for snippet in required_snippets if snippet not in wrapper_text]
        if missing:
            fail(f"{path} python wrapper is missing required structure: {missing}")
    else:
        fail(f"{path} migrated wrapper has unsupported extension")


def load_manifest() -> dict[str, Any]:
    try:
        data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"missing manifest: {MANIFEST}")
    if data.get("schema") != "pdocker.script-inventory.v1":
        raise SystemExit("script inventory schema mismatch")
    return data


def top_level_script_paths() -> set[str]:
    result: set[str] = set()
    for path in SCRIPTS.iterdir():
        if path.name in IGNORED_TOP_LEVEL or path.is_dir():
            continue
        result.add(f"scripts/{path.name}")
    return result


def fail(message: str) -> None:
    raise SystemExit(f"verify-script-inventory: FAIL: {message}")


def main() -> int:
    data = load_manifest()
    entries = data.get("entries")
    if not isinstance(entries, list):
        fail("entries must be a list")
    subtree_entries = data.get("subtree_entries", [])
    category_targets = data.get("category_targets")
    if not isinstance(category_targets, dict):
        fail("category_targets must describe planned move destinations")
    observed_targets: dict[str, str] = {}
    for category, expected_dir in REQUIRED_CATEGORY_TARGETS.items():
        target = category_targets.get(category)
        if not isinstance(target, dict):
            fail(f"category_targets missing object for {category}")
        target_dir = target.get("target_dir")
        wrapper_policy = target.get("wrapper_policy")
        if target_dir != expected_dir:
            fail(f"{category} target_dir mismatch: expected {expected_dir!r}, got {target_dir!r}")
        if not isinstance(wrapper_policy, str) or not wrapper_policy:
            fail(f"{category} missing wrapper_policy")
        observed_targets[category] = target_dir

    paths: list[str] = []
    categories: Counter[str] = Counter()
    stable_paths: set[str] = set()
    obsolete_paths: set[str] = set()
    migrated_wrapper_paths: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            fail(f"entry {index} is not an object")
        path = entry.get("path")
        category = entry.get("category")
        stability = entry.get("stability")
        role = entry.get("role")
        if not isinstance(path, str) or not path.startswith("scripts/"):
            fail(f"entry {index} has invalid path {path!r}")
        if category not in REQUIRED_CATEGORIES:
            fail(f"{path} has unknown category {category!r}")
        if not isinstance(stability, str) or not stability:
            fail(f"{path} has missing stability")
        if not isinstance(role, str) or not role:
            fail(f"{path} has missing role")
        migration = entry.get("migration")
        if not isinstance(migration, dict):
            fail(f"{path} has missing migration plan")
        target_dir = migration.get("target_dir")
        candidate_path = migration.get("candidate_path")
        phase = migration.get("phase")
        action = migration.get("action")
        compat_wrapper = migration.get("compat_wrapper")
        if target_dir != observed_targets[category]:
            fail(f"{path} migration target {target_dir!r} does not match category {category}")
        expected_candidate = f"{target_dir}/{Path(path).name}"
        if candidate_path != expected_candidate:
            fail(f"{path} candidate_path mismatch: expected {expected_candidate!r}, got {candidate_path!r}")
        if action not in ALLOWED_MIGRATION_ACTIONS:
            fail(f"{path} has unknown migration action {action!r}")
        if category == "obsolete-suspect":
            if action != "audit-delete-or-archive":
                fail(f"{path} obsolete-suspect action must be audit-delete-or-archive")
        elif action == "audit-delete-or-archive":
            fail(f"{path} non-obsolete action must not be audit-delete-or-archive")
        if action == "migrated-behind-wrapper":
            validate_wrapper(path, candidate_path)
            migrated_wrapper_paths.add(path)
        for field_name, value in {
            "phase": phase,
            "compat_wrapper": compat_wrapper,
        }.items():
            if not isinstance(value, str) or not value:
                fail(f"{path} has missing migration {field_name}")
        paths.append(path)
        categories[category] += 1
        if stability == "stable-entrypoint":
            stable_paths.add(path)
        if category == "obsolete-suspect":
            obsolete_paths.add(path)

    path_set = set(paths)
    if len(path_set) != len(paths):
        duplicates = sorted(path for path in path_set if paths.count(path) > 1)
        fail(f"duplicate entries: {duplicates}")

    actual = top_level_script_paths()
    missing = sorted(actual - path_set)
    stale = sorted(path_set - actual)
    if missing or stale:
        fail(f"inventory drift missing={missing} stale={stale}")

    missing_stable = sorted(REQUIRED_STABLE_ENTRYPOINTS - stable_paths)
    if missing_stable:
        fail(f"stable entrypoints not marked stable: {missing_stable}")

    if obsolete_paths != KNOWN_OBSOLETE_SUSPECTS:
        fail(
            "obsolete-suspect set changed without verifier update: "
            f"expected={sorted(KNOWN_OBSOLETE_SUSPECTS)} observed={sorted(obsolete_paths)}"
        )

    if "runtime-package-needed" not in categories:
        fail("runtime package staging category is empty")

    validate_script_surface_budget(entries, subtree_entries, categories)
    validate_candidate_duplicate_consistency(entries)
    validate_migrated_wrapper_reference_scan(migrated_wrapper_paths)
    validate_maintenance_doc_sync(entries, categories)

    readme = README.read_text(encoding="utf-8")
    for category, count in categories.items():
        row = f"| `{category}` | {count} |"
        if row not in readme:
            fail(f"README category count is stale for {category}: expected row prefix {row!r}")
    for path in REQUIRED_STABLE_ENTRYPOINTS:
        if f"`{path}`" not in readme:
            fail(f"README omits stable entrypoint {path}")

    print("verify-script-inventory: PASS")
    for category in sorted(REQUIRED_CATEGORIES):
        print(f"ok: {category} = {categories.get(category, 0)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

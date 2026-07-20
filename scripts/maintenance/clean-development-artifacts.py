#!/usr/bin/env python3
"""Safely clean repository-local development artifacts.

This script intentionally avoids `git clean -Xdf` because this repository keeps
some APK/runtime staging payloads ignored but still required for local builds.
By default it performs a dry run and deletes only untracked, reproducible local
caches or scratch files when `--apply` is supplied.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


SAFE_DIR_NAMES = {
    "__pycache__",
    ".pytest_cache",
}
SAFE_EXACT_DIRS = {
    ".gradle",
    ".cxx",
    ".externalNativeBuild",
    "app/build",
    "app/.cxx",
    "app/.externalNativeBuild",
    "build",
    "captures",
    "tmp",
}
SAFE_FILE_NAMES = {
    ".DS_Store",
    "Thumbs.db",
}
SAFE_FILE_SUFFIXES = (
    ".pyc",
    ".pyo",
    ".tmp",
    ".bak",
    ".orig",
    ".rej",
    "~",
)
# Generated device/GPU evidence is ignored by .gitignore, but it can be useful
# while debugging. Keep it out of the default deletion set and require an
# explicit opt-in.
IGNORED_EVIDENCE_PREFIXES = (
    "docs/test/device-logs/",
    "docs/test/device-captures/",
    "docs/test/llama-gpu-",
    "docs/test/llama-cpu-gpu-compare-",
    "docs/test/llama-api-prompt-",
    "docs/test/apk-memory-pager-",
    "docs/test/q6-runtime-spv-",
)
# These ignored paths are local build/runtime payloads or machine config. They
# are never cleanup candidates for this script.
PROTECTED_IGNORED_PREFIXES = (
    "app/src/main/assets/pdockerd/",
    "app/src/main/assets/xterm/",
    "app/src/main/jniLibs/",
)
PROTECTED_IGNORED_EXACT = {
    "local.properties",
}


@dataclass(frozen=True)
class Candidate:
    path: str
    kind: str
    reason: str


def run_git(root: Path, args: list[str]) -> list[str]:
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return [line for line in completed.stdout.splitlines() if line]


def tracked_paths(root: Path) -> set[str]:
    return set(run_git(root, ["ls-files"]))


def tracked_parent_dirs(paths: Iterable[str]) -> set[str]:
    """Return every directory that contains at least one tracked file.

    Directory cleanup candidates must be rejected when any tracked descendant is
    present. Git tracks files, not directories, so checking only `rel in
    tracked` is insufficient for generated-looking evidence directories such as
    `docs/test/device-logs/`.
    """

    result: set[str] = set()
    for rel in paths:
        parts = rel.split("/")
        for index in range(1, len(parts)):
            result.add("/".join(parts[:index]))
    return result


def repository_root(start: Path) -> Path:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=start,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f"not inside a git repository: {start}: {exc}") from exc
    return Path(completed.stdout.strip()).resolve()


def is_under(path: str, prefixes: Iterable[str]) -> bool:
    return any(path == prefix.rstrip("/") or path.startswith(prefix) for prefix in prefixes)


def classify(
    path: Path,
    root: Path,
    tracked: set[str],
    tracked_dirs: set[str],
    include_evidence: bool,
) -> Candidate | None:
    rel = path.relative_to(root).as_posix()
    if rel == ".git" or rel.startswith(".git/"):
        return None
    if rel in tracked:
        return None
    if path.is_dir() and rel in tracked_dirs:
        return None
    if rel in PROTECTED_IGNORED_EXACT or is_under(rel, PROTECTED_IGNORED_PREFIXES):
        return None

    name = path.name
    if path.is_dir() and (name in SAFE_DIR_NAMES or rel in SAFE_EXACT_DIRS):
        return Candidate(rel, "dir", "reproducible local cache/scratch directory")
    if path.is_file() or path.is_symlink():
        if name in SAFE_FILE_NAMES or name.endswith(SAFE_FILE_SUFFIXES):
            return Candidate(rel, "file", "reproducible local temporary file")
        if include_evidence and is_under(rel, IGNORED_EVIDENCE_PREFIXES):
            return Candidate(rel, "file", "ignored generated evidence explicitly requested")
    if include_evidence and path.is_dir() and is_under(rel + "/", IGNORED_EVIDENCE_PREFIXES):
        return Candidate(rel, "dir", "ignored generated evidence directory explicitly requested")
    return None


def collect_candidates(root: Path, include_evidence: bool) -> list[Candidate]:
    tracked = tracked_paths(root)
    tracked_dirs = tracked_parent_dirs(tracked)
    candidates: dict[str, Candidate] = {}
    for path in root.rglob("*"):
        candidate = classify(path, root, tracked, tracked_dirs, include_evidence)
        if candidate is not None:
            candidates[candidate.path] = candidate
    # If a parent directory is already a candidate, suppress children from the
    # output/deletion list so deletion order is deterministic and concise.
    result: list[Candidate] = []
    for candidate in sorted(candidates.values(), key=lambda item: item.path):
        prefix = candidate.path + "/"
        if any(candidate.path.startswith(existing.path + "/") for existing in result if existing.kind == "dir"):
            continue
        # Replace children already collected if this parent appears later.
        result = [existing for existing in result if not existing.path.startswith(prefix)]
        result.append(candidate)
    return result


def delete_candidate(root: Path, candidate: Candidate) -> None:
    path = root / candidate.path
    if not path.exists() and not path.is_symlink():
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="delete safe candidates instead of printing a dry run")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument(
        "--include-ignored-evidence",
        action="store_true",
        help="also include ignored generated docs/test evidence; never enabled by default",
    )
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="repository path, default: current directory")
    args = parser.parse_args(argv)

    root = repository_root(args.root.resolve())
    candidates = collect_candidates(root, args.include_ignored_evidence)

    if args.apply:
        for candidate in sorted(candidates, key=lambda item: item.path, reverse=True):
            delete_candidate(root, candidate)

    if args.json:
        print(json.dumps({
            "root": root.as_posix(),
            "mode": "apply" if args.apply else "dry-run",
            "count": len(candidates),
            "candidates": [candidate.__dict__ for candidate in candidates],
        }, indent=2, sort_keys=True))
    else:
        print(f"clean-development-artifacts: {'APPLY' if args.apply else 'DRY-RUN'}")
        print(f"root: {root}")
        if not candidates:
            print("no safe development artifacts found")
        for candidate in candidates:
            action = "remove" if args.apply else "would remove"
            print(f"{action}: {candidate.path} ({candidate.kind}; {candidate.reason})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

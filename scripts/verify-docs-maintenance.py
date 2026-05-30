#!/usr/bin/env python3
"""Verify documentation maintenance inventory and local Markdown links."""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
BACKLOG = ROOT / "docs" / "maintenance" / "DOCUMENTATION_DEDUP_BACKLOG.md"
AGENT_COORDINATION = "docs/plan/AGENT_COORDINATION.md"

GROUP_RE = re.compile(r"^###\s+(\d+)\.\s+(.+?)\s*$", re.MULTILINE)
INLINE_LINK_RE = re.compile(r"!?\[[^\]\n]*\]\(([^)\s]+)(?:\s+[^)]*)?\)")
REFERENCE_LINK_RE = re.compile(r"^\s{0,3}\[[^\]]+\]:\s+(\S+)", re.MULTILINE)
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
ACTIVE_TODO_RE = re.compile(r"^- \[(doing|next|blocked)\]\s+(.*)")
ANY_TODO_RE = re.compile(r"^- \[[a-z]+\]\s+")
ROADMAP_CUE_RE = re.compile(
    r"(\[#\d+\]|issues/\d+|docs/test/|latest\.json|artifact|verifier|"
    r"scripts/verify-|Acceptance:|non-promoting|planned-gap)",
    re.IGNORECASE,
)
TABLE_SEPARATOR_RE = re.compile(r"^\|\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$")
MARKDOWN_TABLE_ROW_RE = re.compile(r"^\|(.+)\|$")
MERMAID_BLOCK_RE = re.compile(r"```mermaid\n(?P<body>.*?)\n```", re.DOTALL)
OPEN_GAP_RE = re.compile(r"\b(remains open|remain open|non-promoting|planned-gap|still need|still needs)\b", re.IGNORECASE)
PROMOTING_STATUS_RE = re.compile(r"^\s*(good|pass|green)\s*$", re.IGNORECASE)
FORBIDDEN_CURRENT_EVIDENCE_PHRASES = (
    "current release-blocking device smoke evidence",
    "current device release-blocking smoke artifact",
)
STANDARD_DOCKER_SYSTEM_ENDPOINTS = {
    ("GET", "/system/df"),
    ("POST", "/system/prune"),
}
SYSTEM_ROUTE_RE = re.compile(
    r'path\s*==\s*"(?P<path>/system/[^"]+)"\s+and\s+method\s*==\s*"(?P<method>[A-Z]+)"'
)
PDOCKER_FIELD_RE = re.compile(r"\bPdocker[A-Za-z0-9_]*\b")
OWNER_TOKEN_RE = re.compile(
    r"docs/test/[A-Za-z0-9._/@+=:-]+(?:/[A-Za-z0-9._/@+=:-]+)*"
    r"|[A-Za-z0-9._@+=:-]*latest[A-Za-z0-9._@+=:-]*"
    r"(?:/[A-Za-z0-9._@+=:-]+)*"
)
ADBOFF_ROW_RE = re.compile(r"^\|\s*ADBOFF-(?P<id>\d{3})\s*\|.*$", re.MULTILINE)
OBSOLETE_COUNT_RE = re.compile(
    r"\b(?:(?P<word>zero|one|two|three|four|five|six|seven|eight|nine|ten)"
    r"|(?P<number>\d+))\s+(?:tracked\s+)?obsolete[- ]suspects?\b",
    re.IGNORECASE,
)
COUNT_WORDS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}

# Durable Markdown documents must be discoverable from an index.  Test-run
# summaries are addressed by the test-driver manifest convention documented in
# docs/test/README.md; keep this pattern explicit so new durable prose still
# needs an index link or backlog owner.
DISCOVERABILITY_OWNER_PATTERNS = {
    "docs/test/runs/*/summary.md": "docs/test/README.md",
}
LATEST_EVIDENCE_OWNER_FILES = (
    "docs/test/EVIDENCE_INDEX.md",
    "docs/test/README.md",
    "docs/test/CI_GATE_LEDGER.md",
    "tests/test_driver_manifest.json",
    "tests/feature_scenarios.json",
    "tests/stress_regression_cases.json",
    "tests/abnormal_event_cases.json",
    "tests/input_grammar_coverage.json",
)
LATEST_EVIDENCE_DOC_OWNER_FILES = (
    "docs/test/EVIDENCE_INDEX.md",
    "docs/test/README.md",
    "docs/test/CI_GATE_LEDGER.md",
)


class CheckFailure(Exception):
    pass


@dataclass(frozen=True)
class LinkIssue:
    path: Path
    line: int
    target: str
    message: str


def rel(path: Path, root: Path = ROOT) -> str:
    return path.relative_to(root).as_posix()


def fail(message: str) -> None:
    raise CheckFailure(message)


def read_text(path: Path, root: Path = ROOT) -> str:
    if not path.is_file():
        fail(f"missing required file: {rel(path, root)}")
    return path.read_text(encoding="utf-8")


def check_backlog(root: Path = ROOT) -> None:
    backlog = root / "docs" / "maintenance" / "DOCUMENTATION_DEDUP_BACKLOG.md"
    text = read_text(backlog, root)
    groups = list(GROUP_RE.finditer(text))
    numbers = [int(match.group(1)) for match in groups]
    if numbers != list(range(1, 9)):
        fail(
            f"{rel(backlog, root)} must contain exactly 8 numbered backlog groups "
            f"(found {numbers or 'none'})"
        )

    for index, match in enumerate(groups):
        start = match.end()
        end = groups[index + 1].start() if index + 1 < len(groups) else len(text)
        section = text[start:end]
        if "Canonical owners:" not in section:
            fail(
                f"{rel(backlog, root)} group {match.group(1)} "
                f"({match.group(2)}) is missing a 'Canonical owners:' section"
            )
        owner_block = section.split("Canonical owners:", 1)[1].split("Backlog:", 1)[0]
        owner_lines = [line for line in owner_block.splitlines() if line.startswith("- ")]
        if not owner_lines:
            fail(
                f"{rel(backlog, root)} group {match.group(1)} "
                "must list at least one canonical owner"
            )

    if "There are 8 active deduplication backlog groups" not in text:
        fail(f"{rel(backlog, root)} must keep the open backlog count at 8")


def is_external_or_nonlocal(target: str) -> bool:
    parsed = urlparse(target)
    return bool(parsed.scheme) or target.startswith("//")


def normalize_target(raw: str) -> str:
    return raw.strip().strip("<>")


def iter_markdown_link_targets(text: str) -> list[tuple[int, str]]:
    targets: list[tuple[int, str]] = []
    for regex in (INLINE_LINK_RE, REFERENCE_LINK_RE):
        for match in regex.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            targets.append((line, normalize_target(match.group(1))))
    return targets


def check_local_markdown_links(root: Path = ROOT) -> list[LinkIssue]:
    docs = root / "docs"
    issues: list[LinkIssue] = []
    for path in sorted(docs.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        for line, target in iter_markdown_link_targets(text):
            if not target or target.startswith("#") or is_external_or_nonlocal(target):
                continue
            path_part = unquote(target.split("#", 1)[0])
            if not path_part:
                continue
            candidate = (path.parent / path_part).resolve()
            try:
                candidate.relative_to(root.resolve())
            except ValueError:
                issues.append(LinkIssue(path, line, target, "escapes repository root"))
                continue
            if not candidate.exists():
                issues.append(LinkIssue(path, line, target, "target does not exist"))
    return issues


def check_links(root: Path = ROOT) -> None:
    issues = check_local_markdown_links(root)
    if issues:
        rendered = "; ".join(
            f"{rel(issue.path, root)}:{issue.line}: {issue.target} ({issue.message})"
            for issue in issues[:20]
        )
        suffix = "" if len(issues) <= 20 else f"; ... and {len(issues) - 20} more"
        fail(f"local markdown link check failed: {rendered}{suffix}")


def discoverability_index_paths(root: Path = ROOT) -> list[Path]:
    docs = root / "docs"
    paths = sorted(docs.rglob("README.md"))
    backlog = root / "docs" / "maintenance" / "DOCUMENTATION_DEDUP_BACKLOG.md"
    if backlog.is_file():
        paths.append(backlog)
    return paths


def discoverability_links(root: Path = ROOT) -> set[str]:
    root_resolved = root.resolve()
    linked: set[str] = set()
    for path in discoverability_index_paths(root):
        text = path.read_text(encoding="utf-8")
        for _, target in iter_markdown_link_targets(text):
            if not target or target.startswith("#") or is_external_or_nonlocal(target):
                continue
            path_part = unquote(target.split("#", 1)[0])
            if not path_part:
                continue
            candidate = (path.parent / path_part).resolve()
            if candidate.is_dir():
                candidate = candidate / "README.md"
            try:
                relative = candidate.relative_to(root_resolved).as_posix()
            except ValueError:
                continue
            if (
                relative.startswith("docs/")
                and candidate.is_file()
                and candidate.suffix == ".md"
            ):
                linked.add(relative)
    return linked


def has_explicit_discoverability_owner(relative: str, root: Path = ROOT) -> bool:
    for pattern, owner in DISCOVERABILITY_OWNER_PATTERNS.items():
        if fnmatch.fnmatchcase(relative, pattern):
            owner_path = root / owner
            if not owner_path.is_file():
                fail(f"discoverability owner for {pattern} is missing: {owner}")
            return True
    return False


def check_doc_discoverability(root: Path = ROOT) -> None:
    docs = root / "docs"
    linked = discoverability_links(root)
    missing: list[str] = []
    for path in sorted(docs.rglob("*.md")):
        relative = rel(path, root)
        if path.name == "README.md":
            continue
        if relative in linked or has_explicit_discoverability_owner(relative, root):
            continue
        missing.append(relative)

    if missing:
        rendered = ", ".join(missing[:20])
        suffix = "" if len(missing) <= 20 else f", ... and {len(missing) - 20} more"
        fail(
            "docs discoverability check failed; add each durable Markdown file "
            "to its category README or DISCOVERABILITY_OWNER_PATTERNS: "
            f"{rendered}{suffix}"
        )


def latest_evidence_owner_tokens_from_docs(root: Path = ROOT) -> set[str]:
    tokens: set[str] = set()
    for relative in LATEST_EVIDENCE_OWNER_FILES:
        path = root / relative
        if path.is_file():
            for match in OWNER_TOKEN_RE.finditer(path.read_text(encoding="utf-8")):
                tokens.add(match.group(0).strip("`'\".,;:)[]"))
    return tokens


def latest_evidence_doc_owner_tokens(root: Path = ROOT) -> set[str]:
    tokens: set[str] = set()
    for relative in LATEST_EVIDENCE_DOC_OWNER_FILES:
        path = root / relative
        if path.is_file():
            for match in OWNER_TOKEN_RE.finditer(path.read_text(encoding="utf-8")):
                tokens.add(match.group(0).strip("`'\".,;:)[]"))
    return tokens


def latest_evidence_owner_tokens(path: Path, docs_test: Path) -> set[str]:
    """Return evidence-owner tokens that may document a latest artifact."""

    relative_to_docs_test = path.relative_to(docs_test).as_posix()
    tokens = {relative_to_docs_test, f"docs/test/{relative_to_docs_test}"}
    if "/" not in relative_to_docs_test:
        tokens.add(path.name)
    for parent in path.relative_to(docs_test).parents:
        if parent == Path("."):
            continue
        parent_text = parent.as_posix()
        if "latest" in parent_text:
            tokens.add(parent.name)
            tokens.add(parent_text)
            tokens.add(f"docs/test/{parent_text}")
    return tokens


def check_latest_evidence_files_have_owner(root: Path = ROOT) -> None:
    """Ensure committed docs/test/** latest pointers have an owner reference."""

    docs_test = root / "docs" / "test"
    if not docs_test.is_dir():
        return
    owner_tokens = latest_evidence_owner_tokens_from_docs(root)
    missing: list[str] = []
    manifest_only: list[str] = []
    doc_tokens = latest_evidence_doc_owner_tokens(root)
    for path in sorted(docs_test.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(docs_test).as_posix()
        if "latest" not in relative:
            continue
        evidence_tokens = latest_evidence_owner_tokens(path, docs_test)
        if evidence_tokens.isdisjoint(owner_tokens):
            missing.append(rel(path, root))
        elif evidence_tokens.isdisjoint(doc_tokens):
            manifest_only.append(rel(path, root))
    if missing:
        rendered = ", ".join(missing[:20])
        suffix = "" if len(missing) <= 20 else f", ... and {len(missing) - 20} more"
        fail(
            "latest evidence ownership check failed; link each committed "
            "docs/test/** latest file, or its latest-artifact directory, from "
            "EVIDENCE_INDEX.md, docs/test/README.md, CI_GATE_LEDGER.md, or a "
            "registered test manifest: "
            f"{rendered}{suffix}"
        )
    if manifest_only:
        rendered = ", ".join(manifest_only[:20])
        suffix = "" if len(manifest_only) <= 20 else f", ... and {len(manifest_only) - 20} more"
        fail(
            "latest evidence docs-facing ownership check failed; manifest-only "
            "latest artifacts must also be indexed from EVIDENCE_INDEX.md, "
            "docs/test/README.md, or CI_GATE_LEDGER.md: "
            f"{rendered}{suffix}"
        )


def check_adboff_queue_completion_ledger(root: Path = ROOT) -> None:
    queue = root / "docs" / "plan" / "ADB_OFF_TASK_QUEUE_20260520.md"
    if not queue.is_file():
        return
    text = queue.read_text(encoding="utf-8")
    ids = sorted({int(match.group("id")) for match in ADBOFF_ROW_RE.finditer(text)})
    if not ids:
        return
    expected = f"ADBOFF-001 through ADBOFF-{ids[-1]:03d} have landed"
    if expected not in text:
        fail(f"{rel(queue, root)} completion prose must say: {expected}")
    row_006 = next(
        (match.group(0) for match in ADBOFF_ROW_RE.finditer(text) if match.group("id") == "006"),
        "",
    )
    if row_006 and expected not in row_006:
        fail(f"{rel(queue, root)} ADBOFF-006 status must mention: {expected}")


def obsolete_suspect_count(root: Path = ROOT) -> int | None:
    manifest = root / "scripts" / "script-inventory.json"
    if not manifest.is_file():
        return None
    data = json.loads(manifest.read_text(encoding="utf-8"))
    return sum(
        1
        for entry in data.get("entries", [])
        if isinstance(entry, dict) and entry.get("category") == "obsolete-suspect"
    )


def obsolete_count_value(match: re.Match[str]) -> int:
    if match.group("number") is not None:
        return int(match.group("number"))
    return COUNT_WORDS[match.group("word").lower()]


def check_agent_obsolete_suspect_count_language(root: Path = ROOT) -> None:
    count = obsolete_suspect_count(root)
    if count is None:
        return
    path = root / AGENT_COORDINATION
    if not path.is_file():
        return
    text = path.read_text(encoding="utf-8")
    stale = [
        match.group(0)
        for match in OBSOLETE_COUNT_RE.finditer(text)
        if obsolete_count_value(match) != count
    ]
    if stale:
        fail(
            f"{rel(path, root)} obsolete-suspect count wording disagrees with "
            f"scripts/script-inventory.json ({count}): " + ", ".join(stale[:5])
        )


def normalize_docs_category_target(base: Path, target: str, root: Path) -> str | None:
    if not target or target.startswith("#") or is_external_or_nonlocal(target):
        return None
    path_part = unquote(target.split("#", 1)[0])
    if not path_part:
        return None
    candidate = (base / path_part).resolve()
    if candidate.is_dir() or path_part.endswith("/"):
        candidate = candidate / "README.md"
    try:
        relative = candidate.relative_to(root.resolve()).as_posix()
    except ValueError:
        return None
    if re.match(r"^docs/[^/]+/README\.md$", relative) and candidate.is_file():
        return relative
    return None


def docs_readme_category_indexes(root: Path = ROOT) -> set[str]:
    docs_readme = root / "docs" / "README.md"
    text = read_text(docs_readme, root)
    indexes: set[str] = set()
    for _, line in iter_section_lines(text, "Contents"):
        for _, target in iter_markdown_link_targets(line):
            relative = normalize_docs_category_target(docs_readme.parent, target, root)
            if relative:
                indexes.add(relative)
    return indexes


def root_readme_documentation_map_indexes(root: Path = ROOT) -> set[str]:
    readme = root / "README.md"
    text = read_text(readme, root)
    indexes: set[str] = set()
    for _, line in iter_section_lines(text, "Documentation map"):
        for _, target in iter_markdown_link_targets(line):
            relative = normalize_docs_category_target(readme.parent, target, root)
            if relative:
                indexes.add(relative)
    indexes.discard("docs/README.md")
    return indexes


def check_root_documentation_map_matches_docs_categories(root: Path = ROOT) -> None:
    """Keep the root README documentation map aligned with docs/README."""

    expected = docs_readme_category_indexes(root)
    actual = root_readme_documentation_map_indexes(root)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing or extra:
        parts: list[str] = []
        if missing:
            parts.append("missing " + ", ".join(missing))
        if extra:
            parts.append("extra " + ", ".join(extra))
        fail(
            "root README Documentation map must match docs/README.md Contents "
            "category indexes: " + "; ".join(parts)
        )


def normalize_table_cell(text: str) -> str:
    return text.strip().strip("`*_ ").lower()


def iter_section_lines(text: str, title: str) -> list[tuple[int, str]]:
    lines = text.splitlines()
    start_index: int | None = None
    start_level = 0
    for index, line in enumerate(lines):
        match = HEADING_RE.match(line)
        if match and match.group(2).strip() == title:
            start_index = index + 1
            start_level = len(match.group(1))
            break
    if start_index is None:
        return []

    result: list[tuple[int, str]] = []
    for index in range(start_index, len(lines)):
        line = lines[index]
        match = HEADING_RE.match(line)
        if match and len(match.group(1)) <= start_level:
            break
        result.append((index + 1, line))
    return result



def check_agent_compaction_protocol(root: Path = ROOT) -> None:
    """Keep low-context handoff safeguards visible in the canonical agent guide."""

    coordination = root / AGENT_COORDINATION
    text = read_text(coordination, root)
    required = {
        "section": "## Compaction-Safe Handoff Protocol",
        "stop": "Stop before opening a large new seam",
        "summarize": "Summarize first",
        "checkpoint": "Checkpoint deliberately",
        "delegate": "Delegate instead of expanding context",
        "budget": "### Low-Context Patch Budget Rule",
        "no_large_patch": "No large new patch may start when context budget is low",
        "artifacts": "### Concise Agent Artifact Reporting",
        "changed_paths": "changed paths",
        "validation": "validation commands",
        "artifact_paths": "durable artifact paths",
    }
    missing = [name for name, token in required.items() if token not in text]
    if missing:
        fail(
            f"{AGENT_COORDINATION} is missing compaction-safe handoff safeguard "
            f"token(s): {', '.join(missing)}"
        )

def check_historical_agent_assignments(root: Path = ROOT) -> None:
    plan_dir = root / "docs" / "plan"
    if not plan_dir.is_dir():
        return
    for path in sorted(plan_dir.glob("*.md")):
        relative = rel(path, root)
        if relative == AGENT_COORDINATION:
            continue
        text = path.read_text(encoding="utf-8")
        for line_no, line in iter_section_lines(text, "Current Agent Assignments"):
            if not line.startswith("|") or TABLE_SEPARATOR_RE.match(line):
                continue
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if cells and normalize_table_cell(cells[-1]) == "running":
                fail(
                    f"{relative}:{line_no} contains stale running agent assignment "
                    f"outside {AGENT_COORDINATION}; move live state to the ledger "
                    "or mark the row historical"
                )


def active_todo_items(text: str, limit: int | None = None) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    state = ""
    current: list[str] = []
    for line in text.splitlines():
        if ANY_TODO_RE.match(line) and current:
            items.append((state, " ".join(current)))
            if limit is not None and len(items) >= limit:
                return items
            current = []
            state = ""
        match = ACTIVE_TODO_RE.match(line)
        if match:
            state = match.group(1)
            current = [match.group(2).strip()]
            continue
        if current and line.startswith("  ") and line.strip():
            current.append(line.strip())
            continue
        if current and not line.strip():
            items.append((state, " ".join(current)))
            if limit is not None and len(items) >= limit:
                return items
            current = []
            state = ""
    if current and (limit is None or len(items) < limit):
        items.append((state, " ".join(current)))
    return items


def check_todo_roadmap_source_quality(root: Path = ROOT) -> None:
    todo = root / "docs" / "plan" / "TODO.md"
    text = read_text(todo, root)
    for index, (state, item) in enumerate(active_todo_items(text), start=1):
        if not ROADMAP_CUE_RE.search(item):
            fail(
                f"{rel(todo, root)} active roadmap item {index} ({state}) lacks "
                "an issue, artifact/verifier, or acceptance cue: "
                f"{item[:180]}"
            )


def iter_markdown_table_rows(text: str) -> list[tuple[int, list[str]]]:
    rows: list[tuple[int, list[str]]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if not line.startswith("|") or TABLE_SEPARATOR_RE.match(line):
            continue
        match = MARKDOWN_TABLE_ROW_RE.match(line)
        if not match:
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells:
            rows.append((line_no, cells))
    return rows


def check_historical_evidence_language(root: Path = ROOT) -> None:
    """Reject docs that present open/device-gated evidence as current success."""

    for path in sorted((root / "docs").rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        lower = text.lower()
        for phrase in FORBIDDEN_CURRENT_EVIDENCE_PHRASES:
            if phrase in lower:
                fail(
                    f"{rel(path, root)} contains forbidden current-evidence phrase "
                    f"{phrase!r}; describe stale device records as historical/non-promoting"
                )

    compatibility = root / "docs" / "test" / "COMPATIBILITY.md"
    if compatibility.is_file():
        text = compatibility.read_text(encoding="utf-8")
        for line_no, cells in iter_markdown_table_rows(text):
            if len(cells) < 3:
                continue
            if normalize_table_cell(cells[0]) in {"area", "---"}:
                continue
            status = cells[1].strip()
            notes = cells[2]
            if PROMOTING_STATUS_RE.match(status) and OPEN_GAP_RE.search(notes):
                fail(
                    f"{rel(compatibility, root)}:{line_no} marks a row {status!r} "
                    "while its notes describe an open/non-promoting gap"
                )


def implemented_system_routes(source: str) -> set[tuple[str, str]]:
    return {
        (match.group("method"), match.group("path"))
        for match in SYSTEM_ROUTE_RE.finditer(source)
    }


def check_pdocker_extension_surface(root: Path = ROOT) -> None:
    """Keep pdocker-only extension routes and fields documented as extensions."""

    pdockerd = root / "docker-proot-setup" / "bin" / "pdockerd"
    compatibility = root / "docs" / "test" / "COMPATIBILITY.md"
    scope = root / "docs" / "design" / "DOCKER_COMPAT_SCOPE.md"
    source = read_text(pdockerd, root)
    compat_text = read_text(compatibility, root)
    scope_text = read_text(scope, root)

    routes = implemented_system_routes(source)
    if not STANDARD_DOCKER_SYSTEM_ENDPOINTS.issubset(routes):
        missing = sorted(STANDARD_DOCKER_SYSTEM_ENDPOINTS - routes)
        fail(f"{rel(pdockerd, root)} is missing documented standard /system route(s): {missing}")

    for method, path in sorted(routes - STANDARD_DOCKER_SYSTEM_ENDPOINTS):
        token = f"`{method} {path}`"
        if token not in compat_text:
            fail(
                f"{rel(compatibility, root)} must document pdocker extension route {token}"
            )

    for method, path in sorted(STANDARD_DOCKER_SYSTEM_ENDPOINTS):
        token = f"`{method} {path}`"
        if token not in compat_text:
            fail(
                f"{rel(compatibility, root)} must document standard Docker system route {token}"
            )

    normalized_scope = re.sub(r"\s+", " ", scope_text)
    if "excluding Docker-standard `GET /system/df` and `POST /system/prune`" not in normalized_scope:
        fail(
            f"{rel(scope, root)} must distinguish pdocker-only /system extensions "
            "from Docker-standard /system endpoints"
        )

    implemented_fields = set(PDOCKER_FIELD_RE.findall(source))
    documented_fields = set(PDOCKER_FIELD_RE.findall(compat_text))
    missing_fields = sorted(implemented_fields - documented_fields)
    if missing_fields:
        fail(
            f"{rel(compatibility, root)} must document pdocker extension field(s): "
            + ", ".join(missing_fields)
        )


def check_skydnir_compat_docs_public_branding(root: Path = ROOT) -> None:
    """Keep public docs branded as Skydnir while preserving literal identifiers."""

    gpu = read_text(root / "docker-proot-setup" / "docs" / "GPU_COMPAT.md", root)
    network = read_text(root / "docker-proot-setup" / "docs" / "NETWORK_COMPAT.md", root)
    forbidden = (
        "# pdocker GPU compatibility extensions",
        "pdockerd now accepts",
        "pdocker treats Android GPU support",
        "pdocker GPU negotiation",
        "pdocker-owned glibc-facing GPU bridge",
        "The pdocker Vulkan ICD",
        "expected pdocker environment variables",
        "# pdocker network visibility and port truth",
        "pdockerd still executes containers",
        "For each created container, pdockerd now stores",
        "## pdocker extension surface",
        "no pdocker-owned listener",
        "pdockerd verified a live",
    )
    combined = gpu + "\n" + network
    leftovers = [token for token in forbidden if token in combined]
    if leftovers:
        fail("public compatibility docs still expose legacy product prose: " + ", ".join(leftovers))
    for token in (
        "# Skydnir GPU compatibility extensions",
        "The Skydnir daemon now accepts",
        "Skydnir treats Android GPU support",
        "# Skydnir network visibility and port truth",
        "The Skydnir daemon still executes containers",
        "## Compatibility extension surface",
    ):
        if token not in combined:
            fail(f"public compatibility docs missing Skydnir wording: {token}")


def check_mermaid_sequence_safe_subset(root: Path = ROOT) -> None:
    """Keep durable Mermaid sequence diagrams in the conservative subset.

    GitHub and local Markdown renderers do not always accept the same Mermaid
    grammar.  These docs are design evidence, not artwork, so prefer a narrow
    sequence-diagram subset that has rendered reliably across both.
    """

    for path in sorted((root / "docs").rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        for block in MERMAID_BLOCK_RE.finditer(text):
            body = block.group("body")
            if not body.lstrip().startswith("sequenceDiagram"):
                continue
            base_line = text.count("\n", 0, block.start("body")) + 1
            for offset, raw_line in enumerate(body.splitlines()):
                line = raw_line.strip()
                if not line:
                    continue
                line_no = base_line + offset
                if line == "autonumber":
                    fail(
                        f"{rel(path, root)}:{line_no} uses Mermaid autonumber; "
                        "avoid it in durable docs for renderer compatibility"
                    )
                if line.startswith("participant ") and " as " in line:
                    fail(
                        f"{rel(path, root)}:{line_no} uses participant alias syntax; "
                        "use simple participant names in durable docs"
                    )
                if "->>" in line or "-->>" in line:
                    label = line.split(":", 1)[1] if ":" in line else ""
                    if any(token in label for token in ("`", "[", "]", "(", ")", "{", "}")):
                        fail(
                            f"{rel(path, root)}:{line_no} uses punctuation-heavy "
                            "Mermaid message text; move exact code names into prose"
                        )


def run(root: Path = ROOT) -> None:
    check_backlog(root)
    check_links(root)
    check_doc_discoverability(root)
    check_latest_evidence_files_have_owner(root)
    check_adboff_queue_completion_ledger(root)
    check_root_documentation_map_matches_docs_categories(root)
    check_agent_compaction_protocol(root)
    check_historical_agent_assignments(root)
    check_agent_obsolete_suspect_count_language(root)
    check_todo_roadmap_source_quality(root)
    check_historical_evidence_language(root)
    check_pdocker_extension_surface(root)
    check_skydnir_compat_docs_public_branding(root)
    check_mermaid_sequence_safe_subset(root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        run(root)
    except CheckFailure as exc:
        print(f"verify-docs-maintenance: FAIL: {exc}", file=sys.stderr)
        return 1
    print("verify-docs-maintenance: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

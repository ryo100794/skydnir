# pdocker-android Documentation

Snapshot date: 2026-05-04.

## Purpose

This tree contains supporting documentation for pdocker-android. It organizes
operating manuals, architecture notes, build commands, test evidence, planning
records, and generated public-facing summaries without duplicating the
repository-level overview.

## Contents

| Category | Purpose | Index |
|---|---|---|
| Manual | User-facing operation notes and bundled workspace manuals | [`manual/README.md`](manual/README.md) |
| Design | Architecture, compatibility boundaries, and feasibility decisions | [`design/README.md`](design/README.md) |
| Build | Local setup, APK packaging, install commands, and build gates | [`build/README.md`](build/README.md) |
| Test | Repeatable checks, audits, debug workflows, and recorded results | [`test/README.md`](test/README.md) |
| Plan | Current status, TODOs, and historical steering snapshots | [`plan/README.md`](plan/README.md) |
| Release | Release gates, fixed build evidence, distribution process, and announcements | [`release/README.md`](release/README.md) |
| Showcase | GitHub-facing generated dashboard, roadmap timeline, and Wiki seed | [`showcase/README.md`](showcase/README.md) |

## Canonical Sources

Root-level standard files stay at the repository root:

- [`../README.md`](../README.md): project overview and entry points.
- [`../LICENSE`](../LICENSE): license status for original pdocker-android code.
- [`../SECURITY.md`](../SECURITY.md): vulnerability reporting and secret
  handling policy.
- [`../CONTRIBUTING.md`](../CONTRIBUTING.md): issue, pull request, testing, and
  scope guidance for contributors.
- [`../THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md): maintained
  third-party license inventory and distribution notes.

Within `docs/`, use [`plan/STATUS.md`](plan/STATUS.md) for current state,
[`plan/TODO.md`](plan/TODO.md) for unfinished work,
[`test/COMPATIBILITY.md`](test/COMPATIBILITY.md) for measured compatibility,
and [`design/DOCKER_COMPAT_SCOPE.md`](design/DOCKER_COMPAT_SCOPE.md) for
product boundaries.

## Maintenance

- Keep documents in English.
- Put each document in exactly one category.
- Update the category README when adding, moving, or retiring a document.
- Link to canonical documents instead of copying status tables, command lists,
  or TODO blocks between files.
- Keep release/license documents at the repository root unless they are
  generated APK assets.
- Refresh generated showcase pages with `python3 scripts/update-showcase.py`;
  do not hand-edit files marked as generated.

# Release Documents

Snapshot date: 2026-05-17.

## Purpose

This category owns release readiness, release notes, fixed build evidence,
distribution process notes, and announcement drafts. It is intentionally
separate from live test artifacts so a release record stays reproducible after
new `latest` test results are generated.

## Contents

| Document | Scope |
|---|---|
| [`RELEASE_READINESS.md`](RELEASE_READINESS.md) | Release gates, current blockers, and release-candidate checklist |
| [`RELEASE_NOTES_20260505.1.md`](RELEASE_NOTES_20260505.1.md) | Fixed build 20260505.1 release-note summary and remaining gates |
| [`FDROID_RELEASE_PROCESS.md`](FDROID_RELEASE_PROCESS.md) | F-Droid and reproducible-build readiness process |
| [`builds/README.md`](builds/README.md) | Fixed build evidence index |
| [`announcements/README.md`](announcements/README.md) | Public announcement draft index |

## Canonical Sources

- Root-level [`../../README.md`](../../README.md),
  [`../../LICENSE`](../../LICENSE), [`../../SECURITY.md`](../../SECURITY.md),
  [`../../CONTRIBUTING.md`](../../CONTRIBUTING.md), and
  [`../../THIRD_PARTY_NOTICES.md`](../../THIRD_PARTY_NOTICES.md) remain the
  canonical repository-level release and compliance documents.
- Live test outputs remain under [`../test/`](../test/). Do not move
  `*-latest.*` artifacts into this category.
- Current implementation status and active TODOs remain under
  [`../plan/`](../plan/).

## Maintenance

- Keep fixed build evidence immutable once published.
- Add a new build subdirectory for each fixed release candidate instead of
  rewriting an old one.
- Keep public copy in English and link to evidence instead of duplicating logs.

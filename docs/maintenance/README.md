# Documentation Maintenance

Snapshot date: 2026-05-18.

## Purpose

This category records documentation inventory, deduplication backlog, and safe
cleanup sequencing. It is for maintainers coordinating documentation changes;
it is not a second source of truth for product status, release readiness, test
results, or license text.

## Documentation tree

Documentation is gradually migrated and classified into this tree:

- `manual/` - operator and maintainer workflows.
- `design/` - architecture, boundaries, feasibility decisions, and non-goals.
- `build/` - local setup, packaging, signing, install commands, and build gates.
- `test/` - repeatable procedures, compatibility audits, device gates, and
  evidence.
- `plan/` - current status, TODOs, coordination ledgers, and steering history.
- `release/` - release readiness, fixed build evidence, distribution process,
  and announcements.
- `maintenance/` - documentation inventory, deduplication backlog, and cleanup
  sequencing.
- `license/notice` - root compliance and policy files such as `LICENSE`,
  `THIRD_PARTY_NOTICES.md`, `SECURITY.md`, and `CONTRIBUTING.md`.
- `showcase/` - generated or curated GitHub-facing dashboard, roadmap, news,
  and Wiki seed pages.

Until migration is complete, existing documents may remain in their current
paths with category-owner notes and links.

## Completion-state audit

Use one state per document or evidence cluster:

- `canonical/active` - current source of truth for maintained content.
- `artifact/evidence` - immutable or producer-owned test, build, or release
  evidence.
- `historical` - retained timeline, decision, or release history.
- `planned-gap` - known missing document or index planned for later.
- `duplicate-to-merge` - overlapping prose that should collapse into links plus
  a short summary.
- `generated/cache-excluded` - generated output, cache, or pointer excluded from
  manual edits and normal prose deduplication.

## Contents

| Document | Scope |
|---|---|
| [`DOCUMENTATION_DEDUP_BACKLOG.md`](DOCUMENTATION_DEDUP_BACKLOG.md) | Target category tree, completion-state audit model, duplicate/scatter hotspots, canonical owners, and safe cleanup backlog |
| [`SCRIPT_DOC_INVENTORY.md`](SCRIPT_DOC_INVENTORY.md) | Small triage ledger for flat scripts, duplicated or legacy-looking helpers, fragmented planning/test docs, and next actions |
| [`skydnir-rename-inventory-latest.md`](skydnir-rename-inventory-latest.md) | Generated Skydnir rename inventory summary; source JSON is producer-owned evidence for guarded rename/migration work |

## Canonical Sources

- Current implementation state remains in [`../plan/STATUS.md`](../plan/STATUS.md).
- Active work remains in [`../plan/TODO.md`](../plan/TODO.md) and GitHub Issues.
- Compatibility evidence remains in [`../test/COMPATIBILITY.md`](../test/COMPATIBILITY.md).
- Release readiness remains in [`../release/RELEASE_READINESS.md`](../release/RELEASE_READINESS.md).
- Root compliance files remain at the repository root: [`../../LICENSE`](../../LICENSE),
  [`../../THIRD_PARTY_NOTICES.md`](../../THIRD_PARTY_NOTICES.md),
  [`../../SECURITY.md`](../../SECURITY.md), and [`../../CONTRIBUTING.md`](../../CONTRIBUTING.md).

## Maintenance

- Prefer adding index links and canonical-owner notes before moving files.
- Do not hand-edit generated showcase files; regenerate them from their producer.
- Do not delete `*-latest.*` evidence pointers unless producers and consumers are
  updated in the same change.

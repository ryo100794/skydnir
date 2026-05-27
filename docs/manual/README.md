# Manual Documents

Snapshot date: 2026-05-04.

## Purpose

This category contains user-facing operating notes for bundled pdocker
workflows. These documents should explain how a user or operator works with the
app, not why the architecture is shaped a certain way.

## Contents

| Document | Scope |
|---|---|
| [`DEFAULT_DEV_WORKSPACE.md`](DEFAULT_DEV_WORKSPACE.md) | Default VS Code Server, Continue, Codex, Claude Code, and llama.cpp workspace flow |
| [`GIT_COLLABORATION.md`](GIT_COLLABORATION.md) | Multi-machine Git identity, preflight, branch, and integration workflow |
| [`NEWSFLOW.md`](NEWSFLOW.md) | GitHub-facing release, issue, demo, Wiki, and tester-call update workflow |
| [`PROMOTION.md`](PROMOTION.md) | GitHub tagline, repository description, topics, release template, demo checklist, and public messaging |
| [`SKYDNIR_MIGRATION.md`](SKYDNIR_MIGRATION.md) | Public rename, CLI/daemon aliases, runtime-home selection, and service migration |

## Canonical Sources

- Use [`../plan/STATUS.md`](../plan/STATUS.md) for current product state before
  writing user-facing claims.
- Use [`../test/COMPATIBILITY.md`](../test/COMPATIBILITY.md) and
  [`../test/compat-audit-latest.md`](../test/compat-audit-latest.md) for
  compatibility statements.
- Use [`../design/DOCKER_COMPAT_SCOPE.md`](../design/DOCKER_COMPAT_SCOPE.md)
  for Android and Docker boundary language.
- Keep public-message wording in [`PROMOTION.md`](PROMOTION.md) and publishing
  workflow in [`NEWSFLOW.md`](NEWSFLOW.md); do not copy those sections into
  other manual pages.

## Maintenance

- Keep instructions actionable and current with the UI.
- Move architecture rationale to [`../design/README.md`](../design/README.md).
- Move build commands to [`../build/README.md`](../build/README.md).
- Move repeatable test commands to [`../test/README.md`](../test/README.md).

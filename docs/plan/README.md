# Plan Documents

Snapshot date: 2026-05-04.

## Purpose

This category tracks current status, active work, and historical steering
snapshots. It should answer what is done, what is next, what is blocked, and
which temporary accommodations must be replaced.

## Contents

| Document | Scope |
|---|---|
| [`STATUS.md`](STATUS.md) | Current implementation status summary |
| [`TODO.md`](TODO.md) | Live unfinished-work ledger and temporary workaround tracker |
| [`RELEASE_NOTES_20260505.1.md`](RELEASE_NOTES_20260505.1.md) | Fixed build 20260505.1 release-note summary and remaining gates |
| [`ISSUE_WORKFLOW.md`](ISSUE_WORKFLOW.md) | GitHub Issue workflow and TODO/timeline synchronization |
| [`REPLAN_2026-05-01.md`](REPLAN_2026-05-01.md) | Historical replan snapshot after UI/build/GPU steering |

## Canonical Sources

- Use [`STATUS.md`](STATUS.md) for the current implementation summary.
- Use [`TODO.md`](TODO.md) for active work, temporary accommodations, blockers,
  and acceptance checks.
- Use GitHub Issues as the primary tracker for actionable work that can be
  assigned, discussed, validated, and closed. Mirror only short issue-linked
  summaries into [`TODO.md`](TODO.md).
- Use [`ISSUE_WORKFLOW.md`](ISSUE_WORKFLOW.md) for the issue promotion and
  timeline synchronization rules.
- Use [`REPLAN_2026-05-01.md`](REPLAN_2026-05-01.md) only as historical
  context; do not refresh it with live status.
- Link to [`../test/COMPATIBILITY.md`](../test/COMPATIBILITY.md) for test
  evidence and [`../design/README.md`](../design/README.md) for architecture
  decisions.

## Maintenance

- Update [`TODO.md`](TODO.md) whenever a workaround is added or retired.
- Keep historical snapshots stable; update the live plan instead.
- Link to [`../test/COMPATIBILITY.md`](../test/COMPATIBILITY.md) for test
  evidence.
- Link to [`../design/README.md`](../design/README.md) for architectural
  decisions.

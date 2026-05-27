# Git Collaboration Workflow

Snapshot date: 2026-05-04.

## Goal

This repository may be developed from multiple machines while all GitHub access
uses the same owner account. The workflow below keeps each local worktree
identifiable and reduces accidental conflict on shared files.

## One-Time Setup Per Machine

Run this in each clone:

```bash
scripts/setup-git-worktree.sh
```

The setup script creates `.git/info/skydnir-machine-id`, stores the same value
in local Git config as `skydnir.machineId`, and installs a local
`prepare-commit-msg` hook. The machine id is local state, not a tracked file.
Commit messages receive these trailers:

```text
Skydnir-Machine: skydnir-example-1a2b3c
Skydnir-Branch: feature/example
```

If a more explicit name is needed, set it before setup:

```bash
SKYDNIR_MACHINE_ID=skydnir-phone-a scripts/setup-git-worktree.sh
```

## Start-of-Work Preflight

Before editing shared code, run:

```bash
scripts/git-preflight.sh
```

It fetches `origin`, prints the current machine id, branch, ahead/behind
counts, changed files, and `git diff --check`. If the branch is behind or
diverged, synchronize before touching high-conflict files.

## Branch Rules

- Keep `main` releasable and push only verified work there.
- Use short-lived feature branches for other machines:
  `feature/gpu-*`, `feature/ui-terminal-*`, `feature/media-*`,
  `feature/runtime-*`, or `fix/*`.
- Prefer one owner per high-conflict file per task.

High-conflict files:

- `app/src/main/kotlin/io/github/ryo100794/pdocker/MainActivity.kt`
- `docker-proot-setup/bin/pdockerd`
- `app/src/main/assets/pdockerd/pdockerd`
- `scripts/verify-ui-actions.py`
- `docs/plan/TODO.md`

## Integration Rules

- Fetch before integrating.
- Review the diff by ownership area.
- Run focused tests first, then the lightweight suite.
- Push small commits with a clear subject and machine trailer.
- Never resolve conflicts by discarding another machine's work unless that is
  explicitly requested.

## Current Provisional Operator

Until more machines are active, Codex on this worktree acts as the integration
operator. Other machines should push feature branches; this worktree fetches,
reviews, tests, and merges or rebases deliberately.


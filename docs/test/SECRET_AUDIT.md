# Secret Audit

Snapshot date: 2026-05-03.

This document records the repeatable checks used before promoting or publishing
the repository.

## Current Result

The current local audit found no high-confidence secrets in the tracked
Skydnir worktree, Skydnir Git history, or the sibling
docker-proot-setup worktree/history.

One local Git remote URL had embedded GitHub credentials in `.git/config`.
That file is local repository metadata, not a tracked project file, and the
remote URLs have been reset to token-free HTTPS URLs. The exposed credential
should still be revoked because local command output can be copied into logs or
screenshots.

## Commands

Run from the Skydnir repository root.

### Worktree High-Confidence Scan

```sh
rg -I -n --hidden -S \
  -g '!.git' -g '!build' -g '!.gradle' -g '!docs/test/SECRET_AUDIT.md' \
  -e 'ghp_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|gh[ousr]_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{35}|-----BEGIN [A-Z ]*PRIVATE KEY-----|BEGIN PGP PRIVATE KEY' \
  . || true
```

Expected result: no output.

### History High-Confidence Scan

```sh
git grep -I -n -E \
  'ghp_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|gh[ousr]_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{35}|-----BEGIN [A-Z ]*PRIVATE KEY-----|BEGIN PGP PRIVATE KEY' \
  $(git rev-list --all) -- 2>/dev/null \
  | awk -F: '{print $1 ":" $2 ":" $3}' \
  | sort -u || true
```

Expected result: no output.

### Sensitive Filename History Scan

```sh
git log --all --name-only --pretty=format: \
  | rg -i '(^|/)([^/]+\.(jks|keystore|p12|pem|key|crt|env)|keystore\.properties|signing\.properties|release-signing\.properties|local\.properties)$' \
  | sort -u || true
```

Expected result: no output for committed secret-bearing files. If this prints
only intentionally ignored local files that were never tracked, verify with
`git ls-files`.

### Low-Confidence Keyword Review

```sh
rg -I -n --hidden -S \
  -g '!.git' -g '!build' -g '!.gradle' -g '!docs/test/SECRET_AUDIT.md' \
  -e '(password|passwd|token|secret|apikey|api_key|access_key|client_secret|private_key)' \
  . || true
```

Expected result: only placeholders, environment variable names, documentation,
and tests. Review every hit manually before publishing.

### Remote URL Check

```sh
git remote -v
git -C ../docker-proot-setup remote -v
```

Expected result: URLs must not contain usernames, tokens, passwords, or other
credentials.

## When To Use A Clean Repository

Create a new repository, or rewrite history before public release, when any of
these are true:

- a real secret was committed at any point;
- a keystore, signing certificate, or private key was committed;
- large third-party binary payloads were committed and later removed, but
  should not remain downloadable from history;
- the repository history contains license-incompatible code that cannot be
  redistributed;
- the project needs a public history that excludes private steering logs or
  experimental dead ends.

If only `.git/config` contained a credential and the tracked history is clean,
a new repository is not strictly required. Revoke the credential, reset the
remote URL, and continue with this audit recorded.

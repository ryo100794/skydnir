#!/usr/bin/env bash
# Inspect local/remote Git state before starting or integrating work.
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"
cd "$ROOT"

machine_id="$(git config --get skydnir.machineId || true)"
if [[ -z "$machine_id" ]]; then
    machine_id="$(git config --get pdocker.machineId || true)"
fi
if [[ -z "$machine_id" && -f .git/info/skydnir-machine-id ]]; then
    machine_id="$(tr -d '\r\n' < .git/info/skydnir-machine-id)"
fi
if [[ -z "$machine_id" && -f .git/info/pdocker-machine-id ]]; then
    machine_id="$(tr -d '\r\n' < .git/info/pdocker-machine-id)"
fi
if [[ -z "$machine_id" ]]; then
    machine_id="unregistered"
fi

branch="$(git branch --show-current)"
upstream="$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || true)"

echo "Skydnir git preflight"
echo "  machine: $machine_id"
echo "  branch:  ${branch:-detached}"
echo "  upstream:${upstream:+ $upstream}"

if git remote get-url origin >/dev/null 2>&1; then
    echo "  fetch:   origin --prune"
    git fetch --prune origin
fi

if [[ -n "$upstream" ]]; then
    read -r behind ahead < <(git rev-list --left-right --count "$branch...$upstream" | awk '{print $2, $1}')
    echo "  ahead:   $ahead"
    echo "  behind:  $behind"
    if [[ "$ahead" != "0" && "$behind" != "0" ]]; then
        echo "WARN: local and upstream have diverged; rebase or merge before sharing." >&2
    elif [[ "$behind" != "0" ]]; then
        echo "WARN: local branch is behind upstream; pull/rebase before editing shared files." >&2
    fi
fi

echo
echo "changed files:"
git status --short

echo
echo "whitespace check:"
git diff --check

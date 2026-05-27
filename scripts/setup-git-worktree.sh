#!/usr/bin/env bash
# Initialize local Git metadata and hooks for one Skydnir development machine.
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"
cd "$ROOT"

git_dir="$(git rev-parse --git-dir)"
machine_file="$git_dir/info/skydnir-machine-id"
legacy_machine_file="$git_dir/info/pdocker-machine-id"
hook_src="$ROOT/scripts/git-hooks/prepare-commit-msg"
hook_dst="$git_dir/hooks/prepare-commit-msg"

sanitize() {
    tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9._-' '-' | sed 's/^-//; s/-$//'
}

if [[ -n "${SKYDNIR_MACHINE_ID:-}" ]]; then
    machine_id="$(printf "%s" "$SKYDNIR_MACHINE_ID" | sanitize)"
elif [[ -n "${PDOCKER_MACHINE_ID:-}" ]]; then
    machine_id="$(printf "%s" "$PDOCKER_MACHINE_ID" | sanitize)"
elif [[ -f "$machine_file" ]]; then
    machine_id="$(tr -d '\r\n' < "$machine_file")"
elif [[ -f "$legacy_machine_file" ]]; then
    machine_id="$(tr -d '\r\n' < "$legacy_machine_file")"
else
    host="$(hostname 2>/dev/null | sanitize)"
    [[ -n "$host" ]] || host="dev"
    suffix="$(od -An -N3 -tx1 /dev/urandom | tr -d ' \n')"
    machine_id="skydnir-${host}-${suffix}"
fi

printf "%s\n" "$machine_id" > "$machine_file"
printf "%s\n" "$machine_id" > "$legacy_machine_file"
git config skydnir.machineId "$machine_id"
git config pdocker.machineId "$machine_id"

if [[ -f "$hook_dst" ]] && ! cmp -s "$hook_src" "$hook_dst"; then
    backup="$hook_dst.skydnir-backup-$(date -u +%Y%m%dT%H%M%SZ)"
    cp "$hook_dst" "$backup"
    echo "backed up existing prepare-commit-msg hook to $backup"
fi
cp "$hook_src" "$hook_dst"
chmod +x "$hook_dst"

echo "Skydnir Git worktree prepared"
echo "  machine: $machine_id"
echo "  hook:    $hook_dst"
echo
echo "Run scripts/git-preflight.sh before starting a shared work session."

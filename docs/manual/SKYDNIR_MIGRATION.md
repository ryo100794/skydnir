# Skydnir Rename Migration

Snapshot date: 2026-05-27.

## Purpose

This page is the operator-facing migration note for the public rename from
`pdocker-android` to **Skydnir**. It deliberately covers only user-visible
entrypoints and safe compatibility aliases. Internal package IDs, artifact
schemas, sockets, labels, and `PDOCKER_*` environment variables remain
compatibility surfaces until a later, explicitly planned migration.

## What Changed

| Surface | Old | New | Current behavior |
|---|---|---|---|
| Public project name | `pdocker-android` | `Skydnir` | README, issue templates, and release templates use Skydnir. |
| CLI command | `pdocker` | `skydnir` | `skydnir` is available; `pdocker` still works and warns. |
| Daemon launcher | `pdockerd` | `skydnird` | `skydnird` is available; `pdockerd` still works and warns. |
| Host runtime home | `~/.pdocker` | `~/.skydnir` | New Skydnir host invocations use `~/.skydnir` unless old data exists. |
| Android app data | app-private `files/pdocker` | unchanged | Android keeps explicit compatibility paths in this phase. |

## CLI Migration

Use the new command name for new examples:

```sh
skydnir version
skydnir ps
skydnir logs <cell-or-container>
```

The old command remains as a compatibility route:

```sh
pdocker version
```

It emits:

```text
Warning: pdocker is deprecated. Use skydnir instead.
```

Automation that cannot migrate immediately may temporarily suppress the warning
by setting `PDOCKER_SUPPRESS_DEPRECATION_WARNING=1`, but new scripts should use
`skydnir`.

## Daemon Migration

Use the new daemon launcher for host-side development:

```sh
skydnird --socket "$XDG_RUNTIME_DIR/skydnir.sock"
```

The old launcher remains available:

```sh
pdockerd --socket "$XDG_RUNTIME_DIR/skydnir.sock"
```

It emits:

```text
Warning: pdockerd is deprecated. Use skydnird instead.
```

Android does not launch the daemon through a shell command. The app bridge
continues to pass an explicit app-private runtime home and suppresses the
compatibility warning internally.

## Runtime Home Selection

Runtime-home selection is intentionally conservative:

1. `PDOCKER_HOME` wins for compatibility.
2. `SKYDNIR_HOME` is accepted for new host-side Skydnir usage.
3. Existing `~/.skydnir` wins over existing `~/.pdocker`.
4. Existing `~/.pdocker` is reused when `~/.skydnir` does not exist.
5. Fresh `skydnir` / `skydnird` host invocations default to `~/.skydnir`.
6. Fresh legacy `pdocker` / `pdockerd` host invocations default to `~/.pdocker`.

This avoids abandoning old images, layers, and containers during the rename.

## Common Environment Files

Project-wide environment files now use a dual-read transition:

1. `.pdocker-common.env` is read first for compatibility.
2. `.skydnir-common.env` is read second and may override duplicate keys.
3. Per-project `.env` is still read last by the UI Compose path.

The Android UI writes both common files during this transition. Keep
`PDOCKER_*` variable names inside those files until the environment-variable
dual-read migration is explicitly designed; renaming file names and renaming
variable names are separate compatibility steps.

## Service Migration

If you created a host-side user service manually, migrate it by adding a new
`skydnird.service` and stopping the old `pdockerd.service`.

Example user service:

```ini
[Unit]
Description=Skydnir userspace runtime daemon

[Service]
ExecStart=%h/bin/skydnird --socket %t/skydnir.sock
Restart=on-failure
Environment=SKYDNIR_HOME=%h/.skydnir

[Install]
WantedBy=default.target
```

Migration commands:

```sh
systemctl --user stop pdockerd.service || true
systemctl --user disable pdockerd.service || true
systemctl --user daemon-reload
systemctl --user enable skydnird.service
systemctl --user start skydnird.service
```

Do not delete `~/.pdocker` until `skydnird` is confirmed to be using the
expected runtime home and any required data has been migrated or intentionally
left behind.

## Not Changed In This Phase

- Android package ID.
- Android app-private data directory.
- Engine API socket names used inside the app.
- `PDOCKER_*` environment variables.
- Existing JSON artifact schemas.
- Compatibility labels such as `io.pdocker.*`.

These surfaces need dual-read or explicit schema migration work before they can
be renamed safely.

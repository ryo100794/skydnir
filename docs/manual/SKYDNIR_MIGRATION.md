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
| Gradle root project | `pdocker-android` | `skydnir` | Build metadata now uses the public repository name without changing Android package IDs. |
| CLI command | `pdocker` | `skydnir` | `skydnir` is available; `pdocker` still works and warns. |
| Remote Docker helper | `pdocker-remote` | `skydnir-remote` | `skydnir-remote` is available; `pdocker-remote` still works and warns. |
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

For remote Docker-daemon workflows, use the renamed helper:

```sh
export DOCKER_HOST=ssh://user@your-server
skydnir-remote ps
```

The old helper remains as a compatibility route:

```sh
pdocker-remote ps
```

It emits:

```text
Warning: pdocker-remote is deprecated. Use skydnir-remote instead.
```

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
3. Top-level config files are dual-read in this order:
   `./pdocker.yml`, `./skydnir.yml`, `~/.pdocker/config.yml`,
   `~/.skydnir/config.yml`.
4. Those config files may set a simple scalar `home:`, `runtime_home:`, or
   `skydnir_home:` value. The Skydnir file wins when both old and new files
   define the same setting.
5. Existing `~/.skydnir` wins over existing `~/.pdocker`.
6. Existing `~/.pdocker` is reused when `~/.skydnir` does not exist.
7. Fresh `skydnir` / `skydnird` host invocations default to `~/.skydnir`.
8. Fresh legacy `pdocker` / `pdockerd` host invocations default to `~/.pdocker`.

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

## Development Workspace Helpers

The bundled development workspace now presents Skydnir helper commands in
VS Code tasks and startup messages:

```sh
skydnir-paths
skydnir-projects
skydnir-new-project NAME [TEMPLATE]
skydnir-docker version
skydnir-compose -f /pdocker/project/compose.yaml up --detach --build
skydnir-engine-env --check
```

During the transition these commands are thin aliases for the existing helper
implementation. The older `pdocker-*` helper names remain installed for
compatibility with already-created workspaces and user scripts. Existing
default dev-workspace task files are migrated in place when the Android app
repairs the workspace; if a task file is missing, the app restores the bundled
Skydnir task asset rather than a reduced fallback.

The Engine socket itself is still the compatibility socket inside the app data
area. New scripts should call `skydnir-engine-env`; compatibility scripts may
call `pdocker-engine-env`, which resolves the same mounted socket. Both helpers
prefer `SKYDNIR_ENGINE_SOCKET` and then fall back to legacy `PDOCKER_ENGINE_SOCKET`
/ `PDOCKER_DOCKER_SOCK` variables and mounted default paths.

## Service Migration

If you created a host-side user service manually, migrate it by installing the
repository-provided `docker-proot-setup/systemd/skydnird.service` template, or
by adding an equivalent `skydnird.service`, and then stopping the old
`pdockerd.service`.

Example user service:

```ini
[Unit]
Description=Skydnir userspace runtime daemon

[Service]
ExecStart=/usr/bin/env skydnird --socket %t/skydnir.sock
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

# Skydnir Rename Plan

Snapshot date: 2026-05-27.

## Purpose

This plan tracks the public rename from `pdocker-android` to **Skydnir**.
The rename is a branding and trademark-risk reduction project, not a runtime
compatibility rewrite.  The project must keep existing evidence, migration
paths, and compatibility aliases working while the public name moves away from
direct Docker/Android wording.

The rename must not be implemented as a broad one-shot text replacement.
`pdocker`, `pdockerd`, `PDocker`, `PDOCKER`, and `pDocker` each carry different
meanings in code, artifacts, paths, schemas, environment variables, UI strings,
and historical evidence.  Each usage must be classified before it is changed.

## Brand Definition

| Field | Value |
|---|---|
| Public name | Skydnir |
| Reading | Sky-dnir / スカイドニル |
| Origin | Inspired by Skíðblaðnir, the foldable ship of Norse myth. |
| Main tagline | Not a Container. Still Contains. |
| Developer tagline | It's Not Isolation. It's a Vibe. |
| Short description | A zero-kernel userspace runtime for mobile devices. |

The product should use **Skydnir** for public-facing project, app, repository,
documentation, and release names.  Docker compatibility wording is allowed only
where it describes selected API or workflow compatibility.  It must not appear
as the product name, logo-adjacent copy, or a claim of affiliation.

## Public Positioning

Recommended README opening:

```md
# Skydnir

**Not a Container. Still Contains.**

Skydnir is a zero-kernel userspace runtime for mobile devices, inspired by
Skíðblaðnir, the foldable ship of Norse myth.

It does not pretend to provide full kernel-level container isolation. Instead,
it packages, launches, logs, and manages portable runtime cells within the
limits of the host platform.

Developer motto: **It's Not Isolation. It's a Vibe.**
```

Recommended compatibility wording:

```text
Skydnir exposes selected Docker Engine API-compatible endpoints where the host
platform allows it.
```

Avoid these phrases in public branding:

- Docker for Android
- Android Docker
- Docker-compatible Android runtime
- Docker replacement
- Docker clone

Prefer these phrases:

- zero-kernel userspace runtime
- mobile runtime cells
- layered runtime environment
- selected container-tooling compatibility
- portable Linux-like environments

## Trademark and Non-Affiliation Notice

README and release pages should include:

```text
Docker and the Docker logo are trademarks or registered trademarks of Docker,
Inc. Android is a trademark of Google LLC. Skydnir is not affiliated with,
endorsed by, or sponsored by Docker, Inc. or Google LLC.
```

## Target Names

| Surface | Old | Target |
|---|---|---|
| Public project name | pdocker-android | Skydnir |
| GitHub repository | pdocker-android | skydnir |
| CLI | pdocker | skydnir |
| Daemon | pdockerd | skydnird |
| Config file | pdocker.yml | skydnir.yml |
| Config directory | `~/.pdocker` | `~/.skydnir` |
| Service unit | pdockerd.service | skydnird.service |
| Package name | pdocker | skydnir |
| Public API prefix | `/pdocker` | `/skydnir` |
| Log tag | Pdocker | Skydnir |
| Runtime unit name | container-like runtime | cell |
| Persistent storage term | volume/profile/stash variants | stash |

## Compatibility Policy

Initial Skydnir releases must keep thin compatibility aliases for old
`pdocker` names where users or scripts may already depend on them.

Old CLI wrapper behavior:

```text
Warning: pdocker is deprecated and will be removed in a future release. Use
skydnir instead.
```

Do not remove or rename historical artifact schemas only to match the new
brand.  Existing `pdocker.*` JSON schemas, committed evidence, issue links, and
test fixtures should remain stable unless a migration plan explicitly proves
that consumers can read both old and new names.

## Phase 0: Inventory and Guard Rails

Goal: make the rename measurable before changing behavior.

- Search and classify every public/internal occurrence of:
  - `pdocker`
  - `pdockerd`
  - `PDocker`
  - `PDOCKER`
  - `pDocker`
  - `pdocker-android`
- Classify each hit as one of:
  - public branding
  - Android UI string
  - CLI command
  - daemon binary/service
  - config path
  - socket/storage path
  - environment variable
  - API route
  - artifact schema
  - historical evidence
  - test fixture
- Add a rename manifest before broad code changes:
  - old token
  - target token
  - change phase
  - compatibility alias required
  - migration required
  - tests required

Acceptance:

- A generated inventory artifact exists.
- Public branding hits can be changed independently from runtime path/schema
  hits.
- Tests fail if the rename removes a required compatibility alias.

## Phase 1: Public Branding Only

Goal: make the repository present itself as Skydnir while leaving runtime names
stable.

Allowed changes:

- README title and opening copy.
- GitHub About/description text.
- Root documentation summaries.
- Release notes draft.
- App display name and About text if this does not change package id or data
  directories.
- Public non-affiliation notice.

Not allowed in this phase:

- Android package id rename.
- Data directory rename.
- `pdockerd.sock` rename.
- `PDOCKER_*` environment variable rename.
- Artifact schema rename.
- Runtime storage path rename.

Acceptance:

- Existing tests still pass.
- Existing APK install/update path remains the same.
- Existing users do not lose app data.

## Phase 2: CLI and Daemon Aliases

Goal: introduce `skydnir` and `skydnird` without breaking old commands.

Allowed changes:

- Add `skydnir` CLI entrypoint. **Done on `rename/skydnir`:**
  `docker-proot-setup/bin/skydnir` is a thin wrapper over the legacy script.
- Keep `pdocker` as a deprecated wrapper. **Done on `rename/skydnir`:**
  direct `pdocker` invocation emits a deprecation warning unless explicitly
  suppressed for internal routing.
- Add `skydnird` daemon entrypoint. **Done on `rename/skydnir`:**
  `docker-proot-setup/bin/skydnird` is a thin wrapper over `pdockerd`.
- Keep `pdockerd` as a deprecated wrapper or symlink-equivalent launcher.
  **Done on `rename/skydnir`:** direct `pdockerd` invocation emits a
  deprecation warning; the Android bridge suppresses it and presents the
  daemon program name as `skydnird` without renaming storage.
- Update `--help` and `--version` output to prefer Skydnir.
- Update build artifact names where safe.

Acceptance:

- `skydnir ps`, `skydnir logs`, `skydnir shell`, and existing `pdocker`
  aliases both route to the same Engine/API behavior.
- Wrapper warning is present for old names.
- Tests cover both old and new invocations.

## Phase 3: Config Migration

Goal: support new config names while preserving old files.

Current implementation status on `rename/skydnir`:

- `SKYDNIR_HOME` is accepted as a new runtime-home alias.
- `PDOCKER_HOME` remains the highest-priority compatibility override.
- Fresh `skydnir` / `skydnird` invocations default to `~/.skydnir`.
- If an old `~/.pdocker` directory already exists and `~/.skydnir` does not,
  the old directory is reused so existing data is not abandoned.
- Project-wide common env files are dual-read as `.pdocker-common.env` then
  `.skydnir-common.env`; duplicate keys in the Skydnir file override the
  legacy file.
- Android continues to pass an explicit app-private `PDOCKER_HOME`; package
  data and sockets are not renamed in this phase.

Still pending:

- Top-level `pdocker.yml` / `skydnir.yml` and home config files are dual-read
  for the rename-transition runtime-home keys only.  This is intentionally a
  small parser, not a general YAML config system.
- A user-facing migration report under `~/.skydnir/migration/` for desktop
  host usage.

Old paths:

- `~/.pdocker/config.yml`
- `pdocker.yml`

New paths:

- `~/.skydnir/config.yml`
- `skydnir.yml`

Migration rules:

- Never delete old config during migration.
- Copy or transform into the new path.
- Record migration metadata:
  - `~/.skydnir/migration/from-pdocker.txt`
- Keep configuration keys unchanged in the first rename wave.

Acceptance:

- Old config-only setup starts successfully.
- New config-only setup starts successfully.
- Both-present conflict handling is deterministic and documented.

## Phase 4: Service and Android Surface

Goal: expose Skydnir in service names and Android UI without breaking installed
state.

Service target:

- `skydnird.service`

Old service migration guidance:

```bash
systemctl --user stop pdockerd.service
systemctl --user disable pdockerd.service
systemctl --user enable skydnird.service
systemctl --user start skydnird.service
```

Android constraints:

- The package id should not be changed casually after public release because it
  creates a separate app install/data sandbox.
- If the package id is still pre-release, it may be changed only with an
  explicit data migration and rollback note.

Acceptance:

- Foreground notification and app display name use Skydnir.
- Existing internal package/data paths remain compatible unless an explicit
  package migration is approved.

## Phase 5: Internal Namespace and API Prefix

Goal: rename internals only after public aliases and migration are proven.

Candidate changes:

- internal package/namespace names
- API prefix `/skydnir`
- test names and CI workflow names
- generated artifact names

Rules:

- Keep old API prefixes as aliases for at least one transition release.
- Do not rewrite historical evidence or committed test artifacts unless the
  evidence is regenerated with a clear provenance note.
- Do not rename `PDOCKER_*` environment variables until a dual-read
  `SKYDNIR_*`/`PDOCKER_*` compatibility layer exists and is tested.

Acceptance:

- New names work.
- Old names warn but continue.
- Schema/verifier consumers can read old artifacts.

## Branch and Commit Plan

Branch:

```text
rename/skydnir
```

Commit sequence:

1. `docs: add Skydnir rename plan`
2. `docs: rename public project copy to Skydnir`
3. `cli: add skydnir command alias`
4. `daemon: add skydnird launcher alias`
5. `config: add skydnir config migration`
6. `ci: update Skydnir artifact labels`
7. `chore: deprecate public pdocker names`

Each commit must remain bisectable and must not mix branding-only changes with
runtime path or schema changes.

## Release Plan

Suggested pre-1.0 release:

```text
v0.3.0-skydnir
```

Release note:

```md
## Rename notice

This project has been renamed from `pdocker-android` to `Skydnir`.

The old `pdocker` command remains available as a deprecated compatibility
wrapper. New documentation and examples use `skydnir`.
```

## Checklist

- [x] Create `rename/skydnir`.
- [x] Create rename inventory artifact.
- [x] Create GitHub tracking issue.
- [x] Update README opening to Skydnir.
- [x] Add trademark/non-affiliation notice.
- [ ] Update GitHub About/topics.
- [x] Add `skydnir` CLI alias.
- [x] Add `pdocker` deprecation warning.
- [x] Add `skydnird` daemon alias.
- [x] Add runtime home alias migration.
- [x] Add common env file dual-read migration.
- [x] Add top-level config file dual-read migration.
- [x] Add service migration documentation.
- [x] Update CI/Wiki display names that are safe before repository rename.
- [x] Update release note draft.
- [x] Keep old package id/data path until explicitly approved.
- [x] Keep old artifact schemas readable.

## Current Decision

Adopt **Skydnir** as the public name, but do not immediately rename deep
runtime surfaces.  Public branding changes can start after the inventory and
tracking issue exist.  Runtime, config, package, schema, and environment names
move later through compatibility aliases and migration tests.

# Disposable Project Metadata Index

Snapshot date: 2026-05-04.

This document defines the first SQLite metadata index for pdocker projects. It
is a rebuildable index over app-owned files, Engine state, OCI metadata, and
overlay/COW path metadata. SQLite must never become payload storage.

## Goals

- Give the app one queryable index for projects, Compose files, containers,
  images, volumes, jobs, and overlay path metadata.
- Use stable real IDs as truth. Project names, directory basenames, Compose
  service names, image tags, and volume names are display and lookup labels,
  not durable identity.
- Keep the index disposable. If the database is missing, corrupt, stale, or
  inconsistent with filesystem evidence, pdocker can rebuild it.
- Preserve enough replica and snapshot metadata to recover from overlay/COW
  metadata loss, partial writes, and interrupted app updates.

## Non-Goals

- Do not store file payloads, layer tar payloads, project files, container
  rootfs contents, or volume contents in SQLite.
- Do not make SQLite authoritative for Docker object state that already has an
  Engine ID and state record.
- Do not use project names, Compose service names, tags, or filesystem paths as
  primary keys.
- Do not require git. A project may later attach to a git repository, but git
  remote URLs, branches, and worktree paths are optional metadata.

## Filesystem Truth

The database indexes these durable sources:

- `projects/<project-id>/project.json` for project UUID, display name, optional
  git attachment metadata, timestamps, and local settings.
- `projects/<project-id>/compose.yaml` plus optional Compose override files.
- `containers/<container-id>/state.json` and any Engine-maintained container
  metadata.
- OCI image configs, manifests, layer digests, and materialized image rootfs
  trees.
- Container upper directories, whiteout markers, and opaque-directory markers.
- Named volume directories and their metadata.
- Operation/job spool files for long-running imports, builds, pulls, and
  maintenance tasks.

If one of those sources disagrees with SQLite, the source wins unless the source
itself fails validation.

## Identity Model

Every pdocker-owned object gets an immutable ID at creation time:

| Object | ID |
|---|---|
| Project | UUIDv4 or UUIDv7 string |
| Compose file | UUID string |
| Compose service index row | UUID string |
| Container | Docker/Engine container ID |
| Image | OCI image config digest or Engine image ID |
| Layer | OCI layer digest |
| Volume | UUID string, with display name as a mutable label |
| Job | UUID string |
| Overlay path row | UUID string |

Names remain useful for UI, scripts, and Compose compatibility, but names are
not join keys. A renamed project keeps the same project ID. A git-connected
project keeps the same project ID when branch or remote metadata changes.

## Suggested Layout

The exact Android location can be decided by implementation, but the logical
layout should be:

```text
files/pdocker/
  metadata.sqlite
  metadata.sqlite-wal
  metadata.snapshot.sqlite
  metadata.snapshot.json
  projects/<project-id>/
    project.json
    compose.yaml
  containers/<engine-container-id>/
    state.json
    upper/
  volumes/<volume-id>/
    volume.json
    _data/
  images/
    content/
    roots/
```

The snapshot database is a checkpointed copy, not a separate source of truth.
`metadata.snapshot.json` records the source inventory used to accept that
snapshot.

## Schema Contract

The first schema should use `PRAGMA user_version = 1`, `WAL`, foreign keys, and
strict enough checks to reject accidental name-as-ID drift. The host verifier
in `scripts/verify-metadata-index.py` carries the scaffold DDL.

Minimum tables:

- `schema_metadata`: schema version, build ID, and last rebuild/checkpoint
  timestamps.
- `source_snapshots`: source inventory manifests for primary DB and replica
  acceptance.
- `projects`: UUID project identity, display name, project root, optional git
  state, and lifecycle timestamps.
- `compose_files`: one row per indexed Compose file or override.
- `compose_services`: service rows keyed by UUID and linked to a Compose file.
- `images`: image IDs/config digests, repo tags, config paths, rootfs paths,
  and source manifest hashes.
- `image_layers`: layer digests and local layer/rootfs paths.
- `containers`: Engine container IDs linked to projects, services, and images.
- `volumes`: UUID volume identities with mutable display names and `_data`
  paths.
- `jobs`: UUID jobs linked to owning project/object where applicable.
- `overlay_paths`: path-level lower/upper/whiteout/opaque metadata for merged
  views, archive APIs, and rebuild checks.
- Future SAF/SD-card exchange rows should follow the same rule: raw payload
  files may live in a user-selected Documents tree, but emulated Unix metadata
  for FAT32/exFAT-backed files belongs in app-private, rebuildable index or
  sidecar records rather than in the payload directory itself.

Indexes should optimize project-scoped queries, object lookup by real ID,
overlay path lookup by `(container_id, guest_path)`, and reverse source-path
diagnostics.

## Overlay And COW Metadata

`overlay_paths` is an index over representable metadata:

- `container_id`;
- normalized absolute guest path;
- lower image ID, lower layer digest, or lower rootfs source path;
- upper path when materialized;
- whiteout and opaque-directory flags;
- selected mode, uid, gid, size, mtime, xattr digest, and symlink target;
- source evidence hash and last scan timestamp.

Payload bytes remain in lower roots, upper dirs, and volumes. The DB only helps
answer "where would this path resolve?" and "does the index still describe the
filesystem?" If upper metadata is lost, stale, or impossible to verify, pdocker
must rescan lower/upper trees and regenerate these rows.

SAF/SD-card exchange metadata has the same trust model. A sidecar may record
the URI/document identity, display name, size, mtime, content hash evidence,
emulated mode/uid/gid, symlink target, xattr digest, and conflict state for a
payload stored on FAT32/exFAT media. Startup checks must compare that metadata
with `DocumentProvider` enumeration and rescan, quarantine, or report conflicts
when files were edited, renamed, or deleted outside pdocker. These rows support
archive/copy/import/export semantics only; they do not make removable storage a
direct executable rootfs or high-frequency container upperdir.

## Replica And Snapshot Rules

Normal operation uses a primary SQLite database in WAL mode. A background
checkpoint writes:

1. `metadata.snapshot.sqlite.tmp` from a consistent primary DB backup.
2. `metadata.snapshot.json.tmp` with source counts, selected hashes, DB page
   count, schema version, and created timestamp.
3. Atomic rename of the snapshot DB and manifest.

Startup acceptance order:

1. Open the primary DB read-only and run `PRAGMA integrity_check`.
2. Compare `schema_metadata` and latest `source_snapshots` row with current
   filesystem inventory.
3. If primary fails, try `metadata.snapshot.sqlite` plus
   `metadata.snapshot.json`.
4. If both fail, rebuild from filesystem truth.

Snapshot manifests should include source counts and cheap fingerprints for
projects, Compose files, container state files, image manifests, layer roots,
upperdirs, whiteouts, opaque markers, and volume metadata. Large payload files
can use directory counts, mtimes, sizes, and sampled hashes; exact full-tree
hashing is reserved for explicit repair mode.

## Rebuild Algorithm

Rebuild creates a new DB beside the old one and swaps it into place only after
validation.

1. Scan project roots. Require each project to have a valid UUID in
   `project.json`; generate a quarantine report for legacy name-only projects
   instead of treating names as identity.
2. Parse Compose files and assign stable UUIDs to indexed file/service rows.
3. Scan Engine container state and join containers by Engine container ID.
4. Scan OCI image metadata and layers by digest.
5. Scan volume metadata by UUID and verify `_data` directory existence.
6. Scan container upperdirs and lower roots to build overlay path rows,
   including whiteout and opaque-directory evidence.
7. Insert a `source_snapshots` row with counts and fingerprints.
8. Run foreign-key checks, unique identity checks, path normalization checks,
   and `PRAGMA integrity_check`.
9. Replace the primary DB atomically and schedule a replica checkpoint.

Rows that cannot be joined should not be silently dropped. They should become
diagnostics so the UI or maintenance task can offer repair, prune, or import.

## Corruption And Partial-Loss Handling

| Case | Behavior |
|---|---|
| Missing primary DB | Rebuild from filesystem truth |
| Corrupt primary DB | Try replica, otherwise rebuild |
| Missing replica | Continue with primary and schedule checkpoint |
| Stale source manifest | Rebuild affected source class or full DB |
| Missing upperdir metadata | Rescan upperdir and lower roots, then rewrite `overlay_paths` |
| Missing project UUID | Quarantine legacy directory; do not use directory name as truth |
| Duplicate UUID | Quarantine both source roots until user or repair task resolves it |

## Validation

The host-only scaffold verifier checks the proposed DDL and fixture behavior:

```sh
python3 scripts/verify-metadata-index.py
python3 -m unittest tests.metadata_index.test_verify_metadata_index
```

Implementation should later add Android/device validation that creates a
project, renames it, attaches git metadata, creates a container/volume, deletes
the DB, and proves the index rebuilds with the same real IDs.

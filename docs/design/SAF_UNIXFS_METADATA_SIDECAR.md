# SAF UnixFS Metadata Sidecar

Snapshot date: 2026-05-06.

This document defines the planned `saf-unixfs` backend. It is a userspace
filesystem facade that presents Android Storage Access Framework, SD-card, and
FAT/exFAT-backed payloads through a Unix-like storage contract.

The backend sits below the COW/overlay layer as one possible lower or exchange
store. Upper layers must depend on backend contracts, not on Android storage
details.

## Goals

- Let SAF, SD-card, FAT32, and exFAT storage participate in selected pdocker
  storage flows without exposing Android `DocumentProvider` details to overlay,
  archive, container, or UI code.
- Preserve Unix-like metadata that the backing medium cannot represent by
  storing it in app-private sidecar records.
- Make the sidecar rebuildable, checkable, and conflict-aware so external
  Android edits do not silently corrupt container-visible data.
- Keep executable rootfs, hot container uppers, package caches, databases, and
  high-frequency logs on app-private storage unless a later benchmark and
  compatibility gate explicitly allow a narrower SAF-backed mode.
- Keep the backend contract narrow enough that a future non-SAF backend can
  implement the same interfaces.

## Non-Goals

- Do not make SAF or FAT behave like kernel overlayfs, ext4, or a privileged
  Linux mount.
- Do not expose raw tree URIs, document IDs, MIME types, or Android
  `DocumentFile` behavior above the backend boundary.
- Do not store file payload bytes in SQLite. Payload bytes live in the SAF tree
  or an app-private mirror; indexes and sidecars store metadata and evidence.
- Do not claim exact Unix inode identity, hardlink topology, device nodes, all
  xattrs, or complete uid/gid authority on FAT32/exFAT media.
- Do not treat removable storage as a safe default for direct runtime execution
  or high-write container state.

## Accepted Layering

The accepted layering is:

```text
UI / archive API / container runtime mediation
  -> overlay or COW view
    -> FilesystemBackend + UnixMetadataBackend
      -> backend implementation
        -> app-private POSIX tree
        -> saf-unixfs
        -> Android DocumentProvider plus app-private sidecar
```

Upper layers must not branch on `SAF`, `SD card`, `FAT32`, or `exFAT` for
normal path behavior. They ask the backend to open, stat, list, read, write,
rename, delete, and resolve metadata. The backend reports capability and
fidelity limits through abstract flags and diagnostics.

The COW/overlay layer can then compose `saf-unixfs` the same way it composes
other lower stores: as a source of payload bytes and representable metadata.
When the backend reports that an operation cannot be made fail-closed or
Unix-like enough, overlay must return an ordinary storage error or quarantine
diagnostic rather than peeking through to Android-specific APIs.

Separation rules:

- UI code may display backend capability and health summaries, but must not
  implement file semantics by reading SAF sidecar internals.
- Archive API code may preserve tar metadata through `UnixMetadataBackend`, but
  must not special-case FAT, exFAT, or `DocumentProvider` names.
- Overlay/COW code may request lower, upper, whiteout, copy-up, and metadata
  operations through backend contracts, but must not call Android SAF APIs.
- Runtime mediation may receive backend-owned path mappings and capabilities,
  but must not infer semantics from concrete app-private or SAF paths.
- `saf-unixfs` may know about SAF grants, document IDs, FAT/exFAT naming,
  sidecar schema, and mirror/cache state because it is the boundary that hides
  those details.
- Any future backend must pass the same contract tests before upper layers can
  use it.

## Backend Contracts

`FilesystemBackend` is the payload and namespace contract.

Required behavior:

- normalized path lookup under a backend root;
- directory listing with stable logical names;
- regular file read and write;
- create directory and remove entry;
- rename or replace when the backend can make the operation fail-closed;
- symlink payload read/write only when represented by metadata sidecar and
  accepted by the caller;
- traversal protection so `..`, absolute host paths, and provider-specific
  aliases cannot escape the backend root;
- backend capabilities such as case sensitivity, atomic rename support,
  fsync-like durability, max filename length, and writable/read-only state.

`UnixMetadataBackend` is the Unix metadata contract.

Required behavior:

- logical mode bits, file type, uid, gid, size, mtime, and ctime where known;
- symlink target, xattr digest, whiteout marker, and opaque-directory marker
  where representable;
- stable logical file IDs for conflict detection, not as Docker inode promises;
- metadata update transactions that commit only after payload publication is
  durable enough for the backend;
- explicit capability flags for unsupported chmod, chown, xattr, hardlink,
  device-node, and exact executable-bit behavior.

The contracts are intentionally abstract. An app-private ext4-like directory,
an archive-backed read-only tree, and `saf-unixfs` should all be able to satisfy
the same call shape while reporting different capability and fidelity limits.

## Sidecar Records

The sidecar is app-private metadata attached to logical SAF paths. It may be a
SQLite table, a per-tree sidecar file, or both, as long as it follows the same
truth model as the project metadata index: payload bytes are external truth,
and metadata rows are rebuildable evidence unless they record user conflict
resolution.

Each sidecar record should include at least:

- backend root ID and persisted SAF tree URI reference;
- logical normalized path;
- provider document ID and parent document ID when available;
- display name, MIME type, size, and mtime from `DocumentProvider`;
- optional content hash or sampled hash evidence;
- emulated Unix file type, mode, uid, gid, symlink target, and selected xattr
  digest;
- whiteout or opaque-directory state if the record participates in an overlay
  lower or exchange view;
- mirror path when payload is staged in app-private storage;
- `payloadLocation` and `directSafPublished` evidence so app-private mirror
  success cannot be mistaken for publication to the selected SAF/Documents tree;
- explicit fallback fields such as `fallbackRecorded` and `fallbackReason` when
  payload bytes remain only in the app-private mirror after a provider write
  failure;
- last verified timestamp, provider generation evidence when available, and
  conflict state.

For FAT32/exFAT-backed media, the sidecar records metadata the medium cannot
carry. The metadata is part of pdocker's exchange contract, not proof that the
underlying SD card has native Unix semantics.

## Read And Write Semantics

Reads prefer provider payload bytes unless a committed app-private mirror entry
is the authoritative current version. Metadata comes from the sidecar after the
backend verifies that provider evidence still matches the recorded source.

Write rules:

- write payload bytes to a temporary provider document or app-private staging
  file first;
- verify byte count and, when configured, hash evidence;
- publish by the strongest operation the provider supports;
- commit the sidecar metadata only after payload publication succeeds;
- record interrupted operations for startup reconciliation.

When provider rename or replace is not atomic enough, `saf-unixfs` should either
use an app-private mirror as the write target or reject the operation with a
capability error. Upper layers should see a backend error, not Android-specific
partial state.

## Overlay Use

`saf-unixfs` is primarily an exchange and optional lower-source backend. It can
serve as:

- a read-only lower tree for imported or exported project data;
- a `/documents` exchange view for user-visible files;
- a source or sink for archive import/export;
- a cold mirror for project files that are edited through Android Documents
  apps.

It should not be the default backend for:

- direct executable rootfs paths;
- per-container upperdirs;
- package manager caches;
- database files;
- high-frequency logs;
- paths that require exact chmod, chown, xattr, hardlink, or device-node
  semantics.

If a future mode allows a SAF-backed lower under COW, overlay still talks only
to `FilesystemBackend` and `UnixMetadataBackend`. It must not contain
`DocumentProvider`-specific code.

## External Edits And Conflict Handling

SAF payloads can change outside pdocker. Startup, grant changes, and explicit
repair should compare sidecar evidence with provider enumeration.

Required outcomes:

- unchanged provider evidence keeps the sidecar trusted;
- changed content with unchanged logical path marks the record dirty and
  refreshes metadata only after policy checks;
- deleted provider payload marks the logical entry missing or quarantined;
- provider rename creates a rename diagnostic unless provider evidence can join
  it to the previous logical ID;
- simultaneous container-side and Android-side edits preserve both versions or
  quarantine the entry;
- grant revocation degrades the backend to unavailable without deleting payload
  records.

Phase 2 fail-closed gate:

- every backend entry point validates logical relative paths before resolving a
  SAF document or app-private mirror path;
- validation rejects `..`, absolute paths, backslashes, empty segments, dot
  segments, and NUL bytes instead of normalizing them into a different target;
- app-private fallback is not attempted after validation failure and must always
  carry explicit `fallbackRecorded` and `fallbackReason` evidence when used for
  provider-write failures;
- sidecars for published payloads record provider evidence plus optional
  `sha256`, and mediated writes compare that evidence before overwrite;
- provider deletion or changed hash/size records `conflictState =
  external-provider-change` and causes the write to fail closed until repair.

Conflict resolution is user intent and is not safely rebuildable. Once a user
chooses a winner or keep-both result, record that decision before destructive
cleanup.

## Repair And Rebuild

Repair must rebuild from provider truth and sidecar evidence before pruning.

Minimum repair flow:

1. Enumerate the persisted SAF tree through `DocumentProvider`.
2. Normalize provider names into backend logical paths.
3. Join entries to sidecar records by document ID, parent ID, path, size, mtime,
   and optional hash evidence.
4. Mark missing, renamed, externally changed, and duplicate entries.
5. Rebuild derived indexes for overlay/archive lookup.
6. Write a report before deleting stale metadata or mirror payloads.

The repair command must never delete SAF payload bytes solely because a sidecar
row is missing. It may prune app-private cache, mirror, or acceleration state
only when the provider payload and conflict policy make that deletion safe.

## Capability Reporting

The backend should expose capability flags rather than leaking implementation
details. Examples:

| Capability | Meaning |
|---|---|
| `case_sensitive_names` | Logical path comparison can distinguish case |
| `atomic_rename` | Rename/replace can be made fail-closed |
| `durable_fsync` | Backend has a meaningful durability barrier |
| `native_symlink` | Symlink is represented by backing storage, not only sidecar |
| `emulated_unix_metadata` | Mode/uid/gid/xattr-like data comes from sidecar |
| `supports_hardlinks` | Hardlink identity is durable enough to expose |
| `external_mutation_possible` | Backend must rescan for Android-side edits |
| `runtime_hot_path_allowed` | Backend is approved for direct runtime hot paths |

Overlay, archive, container, and UI code should make decisions from these flags
and from normal errors. They should not test for SAF, SD, FAT, or provider
classes directly.

## Relation To Other Design Documents

- [`COW_OVERLAY_STORAGE.md`](COW_OVERLAY_STORAGE.md) defines how this backend
  can be stacked under the pdocker overlay/COW view.
- [`PROJECT_METADATA_INDEX.md`](PROJECT_METADATA_INDEX.md) defines the
  disposable index model that the sidecar should follow.
- Runtime and UI designs should treat SAF-backed paths as backend-provided
  Unix-like views with explicit capability limits, not as special-case Android
  paths.

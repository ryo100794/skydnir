# COW / Overlay-Like Storage Plan

Snapshot date: 2026-05-04.

This document is the storage contract for Skydnir's Docker/OCI rootfs,
container writable state, archive exchange, and Android volume story. The goal
is overlayfs-like behavior that is useful for Docker workloads without
requiring kernel overlayfs, mount namespaces, privileged mounts, or PRoot.
SAF/SD/FAT-backed storage is delegated to the planned
[`saf-unixfs` backend](SAF_UNIXFS_METADATA_SIDECAR.md), which presents
`FilesystemBackend` and `UnixMetadataBackend` contracts below this overlay
layer so overlay, archive, container, and UI code do not need Android storage
special cases.

## Current Truth

Skydnir currently has two storage modes in play:

1. **Materialized merged rootfs.** Image layers are applied into an image
   `rootfs` tree with OCI whiteout handling. Legacy container creation clones
   that tree into a per-container `rootfs` using hardlinks or symlink-copy
   fallback where Android/SELinux disallows hardlinks. This gives simple merged
   reads and a concrete tree for browsing, archive, and older runtime paths.
2. **`cow_bind` lower/upper split.** Newer Android direct paths can create and
   start containers with a read-only image `LowerDir` and per-container
   `UpperDir`. Create/start no longer needs to copy a whole rootfs. Browsing
   already exposes a basic merged view that prefers upper entries and honors
   upper whiteouts.

Neither mode is kernel overlayfs. The accepted design is a Skydnir-owned
snapshotter and path-mediation layer that implements the Docker-visible subset
of overlay semantics in userspace.

## Current Guarantees And Gaps

This section separates shipped behavior from the target contract so recovery
code does not imply semantics that are not implemented yet.

Current guarantees:

- Existing image rootfs payloads and container upperdirs are normal app-owned
  filesystem trees.
- `cow_bind` metadata can point create/start paths at separate lower and upper
  directories.
- Basic merged browsing prefers upper entries and can hide lower entries with
  upper whiteouts.
- Materialized-rootfs `libcow` mode can break many hardlinks before common
  write and metadata-mutation paths.
- Metadata DB work is still a plan. Filesystem state remains the only durable
  truth today.

Current gaps:

- There is no complete overlay repair command that can prove and fix every
  lower/upper/whiteout inconsistency.
- Hardlink identity is not a durable Docker-compatible contract. Android may
  reject app-data hardlinks, and the fallback may lose inode-link topology.
- Any hardlink or ring-tree acceleration must be treated as rebuildable cache
  state, not as payload truth.
- Rename, unlink, replace, chmod, chown, xattr, truncate, hardlink, symlink,
  opaque-directory, and special-file behavior are still partial across runtime,
  archive API, UI browsing, and metadata indexing.
- ENOSPC, OOM, app kill, daemon kill, and partial-write recovery are not yet
  fully implemented for every mutating storage operation.

## Fail-Closed Mutation Contract

Mutating storage operations must fail closed. A failure must leave the merged
view at either the old committed state or a quarantined state that Skydnir will
not present as successful Docker-visible data.

Required rules:

- Copy-up, whiteout creation, rename, replace, chmod/chown/xattr, truncate,
  archive PUT, volume copy, and metadata-index updates must write into temporary
  paths or journaled operation directories first.
- Payload bytes must be fsynced before publishing the upper path, whiteout, or
  metadata row that makes them visible.
- Directory fsync or the closest Android-supported durability barrier should
  follow atomic rename/link publication where available.
- Metadata rows must not be committed until the filesystem operation they
  describe is already durable enough to survive a restart.
- If any step returns `ENOSPC`, `ENOMEM`, `EIO`, `EACCES`, `EPERM`, or an
  unexpected short write, Skydnir must stop the operation, record a diagnostic,
  and avoid exposing a half-applied merged view.
- If Android kills the app, daemon, direct runtime, or helper process during a
  mutation, startup reconciliation must classify the operation as interrupted
  before serving the affected path as healthy.
- OOM/LMK and forced-kill consistency is a storage requirement, not only a
  runtime requirement: after restart, COW/overlay state must be either the old
  committed payload or a complete atomically published upper entry, never a
  trusted temp file, staged archive extraction, partial whiteout, or corrupt
  hardlink/ring accelerator.
- User-facing APIs must not return success for a mutation unless the final
  visible state is present in the upperdir or volume and the operation evidence
  has been recorded.

Current implementation status:

- Some existing writes already use ordinary filesystem atomicity, but there is
  no single storage-wide journal or fail-closed verifier yet.
- Runtime OOM guard and operation-ring work is tracked separately in
  `RUNTIME_OOM_SURVIVAL.md`; this document only defines the storage outcome
  expected when OOM or kill interrupts a COW operation.
- Low-free-space and kill-at-each-step tests are still required before Skydnir
  can claim this contract as implemented.

## `libcow`

`libcow` is an `LD_PRELOAD` libc hook shim. It is not PRoot, does not use
ptrace, and should remain independent from any PRoot-era terminology.

Current role:

- Legacy materialized-rootfs containers install `/.libcow.so`.
- `pdockerd` applies `LD_PRELOAD=/.libcow.so` when a materialized rootfs has
  the shim and no `cow_bind` contract is active.
- The shim breaks hardlinks before writes, truncates, chmod/chown-style
  mutations, and related write paths so one container does not mutate shared
  lower files.
- Fast defaults keep read-only fd tracking and xattr copy-up off unless
  `PDOCKER_COW_TRACK_READONLY_FDS=1` or `PDOCKER_COW_COPY_XATTRS=1` is set.

Target role:

- Keep `libcow` as a compatibility fallback while direct runtime and
  `pdockerd` write-path coverage matures.
- Prefer explicit lower/upper metadata and runtime path mediation for new work.
- Do not extend storage semantics by patching or rebundling PRoot.

## `cow_bind`

`cow_bind` is the planned primary container storage contract.

Current state:

- Container create/start can use storage metadata shaped like
  `Mode=cow_bind`, `LowerDir=<image rootfs>`, and
  `UpperDir=<container upper>`.
- Direct runtime argv supports lower, upper, and guest path parameters.
- Container file reads, writes, `docker cp`, and UI browsing have partial
  lower/upper awareness.
- Basic merged browsing handles upper preference and upper whiteouts.

Still partial:

- Full rename, unlink, replace, chmod, chown, xattr, truncate, hardlink, and
  symlink semantics must be implemented against one storage contract.
- Merged directory iteration must consistently hide whiteouted lower entries
  and expose upper-created entries across UI, archive API, and runtime path
  mediation.
- Opaque directories, hardlink identity, xattrs, ownership mapping, and special
  files need explicit test-backed behavior and documented warnings where
  Android cannot represent Docker state safely.

## Storage Layout

The durable payload should remain file-backed, not SQLite-backed:

- Image/layer payloads live in content-addressed layer storage and materialized
  image rootfs trees.
- Container writes live in per-container upper directories.
- Named volumes live under `filesDir/pdocker/volumes/<name>/_data`.
- Project files and imported files remain app-owned normal files.
- SQLite, when added, stores only indexes and relationships.

## Layer Garbage Collection Audit

Snapshot date: 2026-05-07.

The current layer GC root set is intentionally conservative but incomplete.
`referenced_layer_ids()` reads image manifests under `images/*/manifest.json`
and treats those diff IDs as live. It does not yet include live container
`state.json` records, `Storage.LowerDir`, `Storage.Rootfs`, or build-cache JSON
as roots.

Current implications:

- Removing or retagging an image can make old layer records unreferenced even
  when a container still points at a lower/rootfs derived from that image.
- Materialized container rootfs payloads may survive independently, but
  `cow_bind` lower roots can break if image roots are removed without checking
  container references.
- Build success can auto-prune unreferenced layers when
  `PDOCKER_AUTO_PRUNE_UNREFERENCED_LAYERS=1`, but pull, image delete, and image
  load paths do not all run the same layer-prune/recovery policy.
- `/images/prune` is still effectively a no-op for layer payloads; `/build/prune`
  and `/system/prune` are the paths that prune build artifacts and unreferenced
  layers.
- Partial pull/load directories and temporary archive bodies need startup
  cleanup coverage for prefixes such as `.pull-*`, `.old-*`, `pdblob_*`,
  `pdloadbody_*`, `pdsavebody_*`, `pdarchiveput_*`, `pdbuildctx_*`,
  `pdload_*`, and `pdsave_*`.

Required direction:

- Treat live containers, image manifests, materialized image rootfs trees,
  `cow_bind` lower roots, and active build cache entries as GC roots.
- Make image delete, tag replacement, pull, load, build prune, and system prune
  call the same reachability checker before deleting any layer payload.
- Add kill-recovery tests for staged pull/load directories and stale build
  cache JSON.
- Keep stale build-cache JSON rebuildable and self-healing: missing layer
  payloads must prevent cache use, then cleanup should remove or quarantine the
  stale metadata.

Expected metadata for overlay/COW indexing:

- path;
- lower layer digest or image rootfs source;
- upper path when materialized;
- whiteout or opaque-directory state;
- size, mode, mtime, and selected ownership/xattr fields where supported;
- owning project, image, container, and volume IDs.

File contents must not be stored in SQLite.

## Metadata DB, Replica, And Rebuild

The planned local SQLite database is an index over filesystem truth, not the
source of truth.

Tables should cover at least projects, compose services, containers, images,
jobs, volumes, and overlay path metadata. Container truth remains Engine
ID/state. Image truth remains image IDs, configs, layer digests, and rootfs
payloads. Project truth remains `projects/*/compose.yaml` plus optional
project metadata.

Durability plan:

- Use SQLite WAL for normal operation.
- Store `schema_version` and run startup consistency checks.
- Periodically checkpoint to a replica such as `metadata.snapshot.sqlite`.
- Write a small manifest with source hashes/counts for project files,
  container state, image manifests, layer roots, upperdirs, and volumes.
- On startup, trust the primary DB only when the manifest and consistency
  checks match; otherwise fall back to the replica or rebuild from filesystem
  state.

Rebuild inputs:

- `projects/*/compose.yaml`;
- `containers/*/state.json`;
- image configs and layer manifests;
- materialized image rootfs trees;
- container upperdirs and whiteout markers;
- volume directories and metadata.

Rebuildable metadata:

- overlay path rows derived from lower roots, upperdirs, whiteout markers, and
  opaque-directory markers;
- lower source references derived from image IDs, image configs, layer digests,
  and materialized rootfs locations;
- upper path references derived from container state and upperdir scans;
- selected stat metadata such as size, mode, mtime, symlink target, and
  representable ownership/xattr digests;
- changed-path manifests, touched-path indexes, parent-stack caches, hardlink
  copy-up indexes, and ring-tree/path-resolution accelerators;
- source snapshot counts, cheap fingerprints, and last-scan timestamps.

Metadata that is not safely rebuildable:

- payload bytes that were never committed to a lower root, upperdir, volume, or
  SAF exchange payload;
- exact original hardlink topology when Android rejected hardlinks or a fallback
  copy already broke the topology;
- xattrs, uid/gid, device nodes, and mode bits that the backing filesystem never
  represented;
- the user's intended resolution for a conflict unless it was recorded before
  the failure.

Unimplemented repair items:

- a storage-wide repair command that rebuilds overlay metadata, validates every
  merged path, and rewrites only after producing a user-visible report;
- an interrupted-operation journal for copy-up, whiteout, rename, replace, and
  archive PUT;
- quarantine handling for ambiguous upper/lower conflicts and orphaned
  temporary files;
- automatic reconstruction of hardlink/ring-tree acceleration state after
  cache corruption or partial writes;
- deterministic pruning for dangling upperdirs, stale whiteouts, missing lower
  roots, and DB rows whose filesystem evidence no longer exists;
- SAF sidecar repair that can preserve both Android-edited and
  container-edited versions without silent overwrite.

Repair policy:

- Rebuild metadata by scanning filesystem truth before attempting destructive
  cleanup.
- Prefer quarantine plus diagnostics over guessing when payload identity,
  hardlink topology, or conflict ownership is ambiguous.
- Never delete upperdir, volume, or SAF payload bytes solely because an index
  row is missing.
- Treat missing lower roots as an image integrity problem: affected containers
  may still have upper data, but the merged rootfs is unhealthy until the image
  is restored, repulled, or the container is exported through a degraded path.
- Treat corrupted hardlink/ring-tree indexes as cache loss. Rebuild them from
  lower/upper scans when possible; otherwise disable the acceleration and keep
  serving only the slower verified path.

## Archive API Relation

The archive API is the public data exchange contract for this storage layer. It
powers `docker cp`, UI file browsing, image/container editing, imports, and
exports.

Required behavior:

- `GET /containers/{id}/archive` reads the merged container view.
- `HEAD /containers/{id}/archive` emits Docker-compatible
  `X-Docker-Container-Path-Stat` metadata where representable.
- `PUT /containers/{id}/archive` writes into the writable upper state, not the
  read-only lower image rootfs.
- Path traversal must be rejected before touching app-owned files.
- Tar compatibility tests must cover regular files, directories, symlinks,
  whiteouts, lower reads, upper writes, overwrite, delete, and copy-up edit.

Archive behavior should be storage-mode neutral: callers should not need to
know whether a container is using materialized `libcow` rootfs or `cow_bind`.

## Volumes, Binds, And SAF

Near-term volume stance:

- Named volumes are durable app-owned directories under
  `filesDir/pdocker/volumes`.
- Bind mounts and Compose volume specs are represented in Engine metadata and
  passed to the runtime as backend-owned path mappings.
- Read-only bind enforcement is partial until direct path mediation can enforce
  it; product surfaces should warn rather than pretending kernel mount
  enforcement exists.
- Archive/copy and UI browsing should operate against volume-backed paths using
  the same traversal defenses as rootfs archive paths.

SAF plan:

- Treat Android Storage Access Framework locations as user-granted external
  storage endpoints, not native Linux bind mounts.
- Route SAF/SD/FAT payload and metadata access through the planned
  [`saf-unixfs` backend](SAF_UNIXFS_METADATA_SIDECAR.md). Upper overlay,
  archive, container, and UI layers should consume abstract filesystem and
  Unix-metadata capabilities instead of checking for `DocumentProvider`,
  FAT32, or exFAT details directly.
- Keep persistable URI grants and display names in metadata.
- Project-library templates treat the selected Android Documents folder as the
  workspace root for user-owned project definitions and explicit exchange data.
  Projects live under `pdocker/projects`; containers see the selected folder at
  `/documents` only when an app intentionally writes there. Hot working
  directories, model caches, databases, and high-frequency logs stay in
  app-private storage by default.
- If the selected folder is a removable SD-card tree that rejects normal
  app-UID path writes, Skydnir falls back to the app-private project mirror.
  Full SD-backed project storage then requires the planned SAF mediator rather
  than pretending the Linux path is writable.
- Removable SD media may be FAT32 or exFAT. Those filesystems can carry raw
  file payload bytes well, but they do not preserve Unix ownership, mode bits,
  symlinks, hardlink identity, device nodes, or xattrs with Docker fidelity.
  The supported design is a hybrid exchange store: payload files may live in
  the selected SAF/Documents tree, while app-private metadata records emulate
  the representable Unix fields for archive/copy/import/export flows.
- This hybrid store is for container data exchange, not executable hot paths.
  Runtime rootfs, container uppers, `/workspace`, package caches, model caches,
  databases, and high-frequency logs stay in app-private storage unless a user
  explicitly accepts the compatibility and performance limits of an external
  bind/exchange path.
- SAF paths are mediated through Android `DocumentProvider` operations. Skydnir
  must not issue direct POSIX writes, renames, or chmod/chown/xattr-style
  mutations against removable storage unless a current probe proves the exact
  path is normal app-UID writable and not merely URI-granted.
- App-private sidecar metadata needs rebuild and check rules just like the main
  project index: store source URI/document IDs, display names, size/mtime/hash
  evidence, emulated mode/uid/gid/xattr digests, symlink targets where
  representable, and conflict state. On startup or grant changes, verify the
  sidecar against `DocumentProvider` enumeration and either rescan, quarantine,
  or ask the user to choose the winner for changed/deleted/renamed entries.
- The Android app owns `PDOCKER_DOCUMENTS_HOST`,
  `PDOCKER_SHARED_DOCUMENTS_HOST`, and the SAF URI grant, writes them into each
  project `.env`, and keeps app-private fast storage for hot paths that should
  not constantly write to SD-card/Documents storage.
- Prefer import/export, sync, or mirrored app-owned working copies for runtime
  paths that need POSIX-style open/stat/rename semantics.
- SAF-mediated mode exposes an app-private mirror path to Compose and keeps the
  selected tree behind Android `DocumentProvider` calls. The current mediator
  contract covers lightweight directory creation, listing, existence checks, and
  payload reads/writes with app-private sidecar metadata. Export payloads under
  `pdocker-exports/` are treated as SAF-owned after a successful mediator write:
  the app-private mirror file is evicted and only the sidecar metadata remains
  in app storage. It is suitable for exchange/import/export surfaces, not
  executable rootfs paths, hot upperdirs, package caches, databases, or
  high-frequency logs.
- Conflict handling must be explicit. When the same logical guest path is
  changed through both container exchange metadata and Android/Documents apps,
  Skydnir should preserve both payloads or quarantine the entry rather than
  silently overwriting one side.
- Never claim Docker mount propagation, device mounts, tmpfs, SELinux relabel,
  executable-bit enforcement, uid/gid authority, complete symlink behavior, all
  xattrs, special files, hardlink identity, or exact `:ro` bind parity for SAF
  paths.

## Performance And Tuning

Recent evidence points toward lower/upper sharing as the main performance path:

- Full rootfs materialization for a large dev-workspace container measured
  about 77.35s on SOG15.
- `cow_bind` lower/upper sharing reduced that create path to about 1.10s, with
  a fresh `pdocker-dev` create/start around 0.382s/0.389s.
- Build snapshots now have touched-path and parent-stack cache work that avoids
  full rootfs walks for some COPY/metadata-heavy steps.
- Large apt/npm RUN layers still need a direct-runtime changed-path manifest so
  snapshotting can avoid scanning the whole rootfs.
- `libcow` tuning already skips expensive read-only fd and xattr tracking by
  default and uses cheaper copy-up paths where possible.

Tuning priorities:

1. Make `cow_bind` the default for container create/start when the runtime
   supports it.
2. Add a changed-path manifest from direct runtime write mediation.
3. Keep materialized rootfs creation available as fallback and test oracle.
4. Measure archive operations separately from runtime execution.
5. Track storage metrics for layers, image views, container uppers, volumes,
   total app data, and free space after build, prune, rebuild, and edit flows.

## Test Roadmap

Storage tests should be shared by UI, archive API, and runtime mediation rather
than duplicated per feature.

Minimum matrix:

- lower read;
- upper write;
- lower copy-up edit;
- delete whiteout;
- opaque directory where supported;
- rename over lower and upper;
- file replacing directory and directory replacing file;
- chmod/chown/xattr/truncate behavior;
- symlink traversal and absolute symlink handling inside the rootfs;
- hardlink isolation;
- archive GET/HEAD/PUT metadata;
- volume-backed archive/copy;
- rebuild of metadata DB from filesystem truth;
- prune and dangling-reference recovery.

Failure and recovery matrix:

- kill during lower-to-upper copy-up before temp payload publication;
- kill after temp payload publication but before visible rename;
- kill after visible rename but before metadata-index commit;
- kill during whiteout creation, opaque-directory marker creation, and rename
  over an existing lower path;
- `ENOSPC` during archive PUT, copy-up, rename/replace, metadata snapshot, and
  replica checkpoint;
- guarded `ENOMEM` from direct runtime during a mutating syscall;
- app or daemon restart with an active operation journal and no live worker pid;
- corrupt primary DB with valid replica;
- corrupt primary DB and stale replica, forcing filesystem rebuild;
- missing upperdir metadata with intact upper payloads;
- missing lower root for a container that still has upper payloads;
- stale or corrupt hardlink/ring-tree accelerator with intact payload trees;
- orphaned temporary files and partially written whiteout/opaque markers;
- SAF grant revocation, external edit conflict, and sidecar metadata mismatch.

Assertions for these tests:

- the API reports failure or degraded/quarantined state instead of success;
- the merged view never exposes a partially copied file as complete;
- rebuild recreates overlay rows and acceleration metadata from lower/upper
  truth when enough evidence exists;
- ambiguous payload conflicts are quarantined or preserved under distinct names;
- low-free-space cleanup does not delete user payload bytes merely to make a
  metadata operation pass;
- repeated repair/rebuild runs are idempotent.

Device verification should include Android API levels used by the default and
compat APKs, SOG15-style Android 15 behavior, low-free-space cases, and SAF
grant revocation.

## Non-Goals Until Proven Needed

- Exact kernel overlayfs inode identity, `d_type`, hardlink counts, every xattr,
  and all device-node semantics.
- Mount propagation, privileged mounts, block devices, tmpfs, and cgroup-backed
  storage accounting.
- Storing file contents in SQLite.
- Reintroducing PRoot as the default storage implementation.

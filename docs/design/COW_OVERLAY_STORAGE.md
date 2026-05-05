# COW / Overlay-Like Storage Plan

Snapshot date: 2026-05-04.

This document is the storage contract for pdocker's Docker/OCI rootfs,
container writable state, archive exchange, and Android volume story. The goal
is overlayfs-like behavior that is useful for Docker workloads without
requiring kernel overlayfs, mount namespaces, privileged mounts, or PRoot.

## Current Truth

pdocker currently has two storage modes in play:

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

Neither mode is kernel overlayfs. The accepted design is a pdocker-owned
snapshotter and path-mediation layer that implements the Docker-visible subset
of overlay semantics in userspace.

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
- Keep persistable URI grants and display names in metadata.
- Project-library templates treat the selected Android Documents folder as the
  workspace root for user-owned project definitions and explicit exchange data.
  Projects live under `pdocker/projects`; containers see the selected folder at
  `/documents` only when an app intentionally writes there. Hot working
  directories, model caches, databases, and high-frequency logs stay in
  app-private storage by default.
- If the selected folder is a removable SD-card tree that rejects normal
  app-UID path writes, pdocker falls back to the app-private project mirror.
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
- SAF paths are mediated through Android `DocumentProvider` operations. pdocker
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
  pdocker should preserve both payloads or quarantine the entry rather than
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

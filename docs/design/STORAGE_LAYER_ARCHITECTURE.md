# Storage Layer Architecture

Snapshot date: 2026-05-18.

This document records the storage boundary that the SAF direct-output and
UnixFS mediator host tests enforce. It does not claim a fresh device SAF pass;
it defines the contract that real device artifacts must satisfy before they can
be promoted.

## `/documents` exchange contract

`/documents` is a SAF-backed UnixFS exchange layer. It is not the default home
for hot rootfs data, layer caches, package-manager state, model files, or
private project internals. Those remain app-private unless a Compose volume or
bind explicitly exposes them.

The mediator uses this order:

1. validate the logical relative path fail-closed;
2. attempt the direct SAF write first against the user-selected Documents tree;
3. verify direct-write evidence under the selected host Documents path;
4. commit sidecar metadata only after payload publication evidence exists;
5. use app-private fallback only after a provider write failure, and only when
   `fallbackRecorded`, `fallbackReason`, and provider error evidence are present.

Fallback bytes are diagnostic and non-promoting. A mirror-only payload, even if
complete, is not direct SAF output and must not satisfy the gate.

## UnixFS mediator boundary

Upper layers consume abstract storage contracts:

```text
runtime / archive / UI / overlay
  -> FilesystemBackend
  -> UnixMetadataBackend
  -> saf-unixfs mediator
  -> Android DocumentProvider or app-private mirror/sidecar
```

The `FilesystemBackend` owns payload lookup, open, write, rename, stat, unlink,
and traversal rejection. The `UnixMetadataBackend` owns modes, uid/gid-like
identity, timestamps, symlink/whiteout markers, xattr digests, and conflict
state when the physical medium cannot store them.

The upper layers must not branch on SAF, `DocumentProvider`, tree URI, FAT32,
exFAT, or SD-card implementation details. They should read capability flags and
ordinary backend errors. This keeps COW/overlay, archive import/export, runtime
mediation, and UI flows independent from Android storage mechanics.

## Sidecar metadata for FAT32/exFAT/SD-card

FAT32/exFAT/SD-card backing stores may contain only raw bytes. Unix-like
metadata is emulated by sidecar metadata in app-private storage. Promotion
requires the sidecar to record provider evidence such as logical relative path,
document identity, size, hash when available, conflict state, and Unix metadata
source. The sidecar makes the exchange layer UnixFS-like; it does not imply that
the removable or emulated storage natively supports Unix semantics.

If provider evidence diverges from the sidecar, the mediator records conflict
state and fails closed until repair or explicit user resolution. External edits
must not be silently overwritten.

## Host contract and negative fixtures

The host contract is intentionally stricter than a top-level `Success=true`:

- direct payload cases must include `DirectWriteEvidence` for the selected SAF
  Documents path;
- fallback must be explicitly recorded with a reason and provider/SAF error and
  must stay non-promoting;
- sidecars must include provider/hash/conflict evidence plus Unix metadata and
  capability evidence;
- `LayerBoundary` must prove that upper layers consume `FilesystemBackend` and
  `UnixMetadataBackend` rather than SAF internals;
- path traversal and invalid targets must fail closed before fallback.

`scripts/verify-saf-direct-output-artifact.py` and
`tests/test_saf_direct_output_contract.py` provide host-side negative fixtures
for mirror-only fake success, fallback-as-pass, missing fallback reason, missing
sidecar Unix metadata, missing direct-write evidence, and missing layer boundary
evidence. These fixtures do not replace a real Android run; they prevent
non-promoting artifacts from being described as SAF direct-output passes.

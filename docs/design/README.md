# Design Documents

Snapshot date: 2026-05-04.

## Purpose

This category records architectural choices, compatibility boundaries, and
technical feasibility decisions. Design documents should describe constraints,
tradeoffs, accepted behavior, and non-goals.

## Contents

| Document | Scope |
|---|---|
| [`DOCKER_COMPAT_SCOPE.md`](DOCKER_COMPAT_SCOPE.md) | Docker compatibility scope, non-goals, and replacement strategies |
| [`COW_OVERLAY_STORAGE.md`](COW_OVERLAY_STORAGE.md) | Overlay-like storage plan |
| [`SAF_UNIXFS_METADATA_SIDECAR.md`](SAF_UNIXFS_METADATA_SIDECAR.md) | SAF/SD/FAT Unix-like backend and metadata sidecar |
| [`RUNTIME_STRATEGY.md`](RUNTIME_STRATEGY.md) | Direct runtime direction and PRoot retirement plan |
| [`API29_DIRECT_EXEC_FEASIBILITY.md`](API29_DIRECT_EXEC_FEASIBILITY.md) | API 29+ direct execution feasibility notes |
| [`GPU_COMPAT.md`](GPU_COMPAT.md) | Android GPU, Vulkan, cuVK, and benchmark design direction |
| [`MEDIA_BRIDGE.md`](MEDIA_BRIDGE.md) | Android Camera2/AudioRecord/AudioTrack media bridge contract |
| [`APK_MEMORY_PAGER.md`](APK_MEMORY_PAGER.md) | APK-scoped swap-like memory pager feasibility and page-fault strategy |
| [`RUNTIME_OOM_SURVIVAL.md`](RUNTIME_OOM_SURVIVAL.md) | Runtime OOM survival, large-workload mode, and post-kill evidence strategy |
| [`TERMINAL_STREAM_ARCHITECTURE.md`](TERMINAL_STREAM_ARCHITECTURE.md) | Generic terminal surface, Docker Engine exec/attach stream boundary, and TTY profile split |
| [`../../docker-proot-setup/docs/GPU_COMPAT.md`](../../docker-proot-setup/docs/GPU_COMPAT.md) | Backend GPU request/env contract |
| [`../../docker-proot-setup/docs/NETWORK_COMPAT.md`](../../docker-proot-setup/docs/NETWORK_COMPAT.md) | Backend network metadata and port rewrite plan |

## Canonical Sources

- Treat [`DOCKER_COMPAT_SCOPE.md`](DOCKER_COMPAT_SCOPE.md) as the canonical
  product-boundary document for Docker compatibility, Android limits, and
  unsupported Docker features.
- Treat [`COW_OVERLAY_STORAGE.md`](COW_OVERLAY_STORAGE.md) as the canonical
  storage contract; status summaries should link here instead of restating the
  lower/upper model.
- Treat [`SAF_UNIXFS_METADATA_SIDECAR.md`](SAF_UNIXFS_METADATA_SIDECAR.md) as
  the backend boundary for SAF/SD/FAT payloads and emulated Unix metadata.
- Treat [`GPU_COMPAT.md`](GPU_COMPAT.md) as the Android GPU design entry point,
  with backend request/env details delegated to
  [`../../docker-proot-setup/docs/GPU_COMPAT.md`](../../docker-proot-setup/docs/GPU_COMPAT.md).
- Treat [`MEDIA_BRIDGE.md`](MEDIA_BRIDGE.md) as the media contract; test files
  should record probes and link back here for readiness rules.
- Treat [`APK_MEMORY_PAGER.md`](APK_MEMORY_PAGER.md) as the feasibility boundary
  for swap-like behavior inside the APK. It is a pdocker extension, not Docker
  compatibility.
- Treat [`RUNTIME_OOM_SURVIVAL.md`](RUNTIME_OOM_SURVIVAL.md) as the operational
  policy for low-memory runtime behavior, including early ENOMEM, Large
  Workload Mode, and restart-time evidence.
- Treat [`TERMINAL_STREAM_ARCHITECTURE.md`](TERMINAL_STREAM_ARCHITECTURE.md) as
  the boundary between the generic terminal UI and Docker-compatible Engine
  exec/attach/session transports.

## Maintenance

- Keep product boundaries here, not in test result files.
- Link to [`../test/COMPATIBILITY.md`](../test/COMPATIBILITY.md) for measured
  compatibility status.
- Link to [`../plan/TODO.md`](../plan/TODO.md) for unfinished implementation
  tasks.

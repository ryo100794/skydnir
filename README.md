# Skydnir

**Not a Container. Still Contains.**

Skydnir is a zero-kernel userspace runtime for mobile devices, inspired by
Skíðblaðnir, the foldable ship of Norse myth.

It does not pretend to provide full kernel-level container isolation. Instead,
it packages, launches, logs, and manages portable runtime cells within the
limits of the host platform.

Developer motto: **It's Not Isolation. It's a Vibe.**

Skydnir turns a mobile device into a portable runtime workbench: pull and
inspect layered root filesystems, edit Compose/Dockerfile-style projects, watch
build logs, open `-it`-style terminals, browse runtime cell files, and run
developer templates such as VS Code Server and llama.cpp from a normal app UI.

This is not a Docker product and it does not pretend mobile app sandboxes give
apps Linux host privileges.  Skydnir exposes selected Docker Engine
API-compatible endpoints where the host platform allows it, plus native
Compose/Dockerfile controls, a no-PRoot Android direct executor, userspace
storage, and explicit mobile extensions for GPU, media, networking, and
self-debugging.

If you are interested in mobile development workstations, runtime internals,
mobile sandbox limits, or running real developer environments from a phone or
tablet, this repository is the experiment.

## What You Can Do

- **Manage projects visually**: Compose files, Dockerfiles, images, containers,
  ports, jobs, logs, storage, terminals, and editor tabs live in one Android UI.
- **Use Docker-shaped workflows**: `skydnird` speaks the Docker Engine API over
  a Unix socket, with `pdockerd` retained as a deprecated compatibility alias,
  so compatibility can be tested against real Docker clients while the product
  UI uses native Engine API calls.
- **Inspect without starting a shell**: browse image rootfs trees and container
  lower/upper views, copy files into editable projects, and edit writable
  container layers directly.
- **Keep sessions alive**: grouped terminal/log/editor tabs are designed for
  long-running builds and container consoles instead of disposable shell views.
- **Start real dev templates**: bundled project-library templates cover VS Code
  Server with Continue/Codex/Claude Code, llama.cpp, ROS2/RViz/noVNC, and
  Blender/noVNC experiments.
- **Measure the hard parts**: GPU bridge, syscall mediation, storage reuse,
  runtime overhead, and Docker API parity are tracked with repeatable tests.

## Current Evidence Snapshot

The current fixed verification record is
[`docs/release/builds/20260505.1/README.md`](docs/release/builds/20260505.1/README.md):

- Build `20260505.1` is committed at `dd3ce31` (`Record fixed build
  20260505.1 verification`).
- Build `20260505.1` records compat and modern debug APK builds plus unsigned
  release APK builds as `PASS`.
- The Android full smoke route passed device Dockerfile build, Compose
  up/down, `docker exec`, and Engine API `exec -it`.
- The Android quick route passed install, `docker version`, direct runtime
  probe, and memory-pager probes.
- `verify-fast`, scenario, and test-design gates currently fail on the
  intentional literal test-density threshold, not on APK packaging.
- Host backend quick/full regressions currently fail because the host route
  expects a staged direct-executor helper; the Android APK direct route is the
  release-blocking evidence for this build.

The generated compatibility audit records `69` PASS and `0` FAIL entries, and
APK payload checks confirm the product APK omits upstream Docker CLI/Compose,
PRoot, proot-loader, and talloc. This is still a development preview rather
than a broad stable release: the current release-readiness gate is tracked in
[`docs/release/RELEASE_READINESS.md`](docs/release/RELEASE_READINESS.md), including
P0 blockers for service-health truth, runtime stop cleanup, image/COW crash
safety, OOM/LMK recovery, modern-runtime capability truth, and build/test
checkpoint truth.

## Why It Is Different

Android apps do not get Docker's normal toolbox: no privileged namespaces, no
cgroups, no overlayfs mounts, no bridge network, and no raw host device access.
Skydnir treats that as the design challenge rather than hiding it.

- The product APK does **not** bundle upstream Docker CLI or Compose binaries.
- PRoot/talloc/proot-loader are **not** part of the default product APK.
- The UI tells the truth when a feature is metadata-only, blocked, or still
  experimental.
- Compatibility decisions are documented under `docs/design/` and verified by
  reusable tests under `docs/test/`.
- Android-specific features, such as Vulkan/OpenCL GPU bridging and media
  proxying through Camera2/AudioRecord/AudioTrack, are explicit Skydnir
  extensions rather than disguised raw `/dev` passthrough.

## Current status

| Area | Status |
|---|---|
| APK shell | Native Android UI, foreground daemon, boot/package-replaced restart, notification resident mode |
| Engine API | Docker Engine API-compatible metadata, image, container, build, logs, and lifecycle endpoints |
| Compose up | In-app orchestrator path, persistent job UI, streaming logs, build progress, retry/stop actions; no product Docker CLI dependency |
| Direct execution | SDK28 compat executor under active development; no PRoot in the default APK; syscall mediation and performance profiling are tracked |
| Filesystems | Image rootfs browser, container lower/upper merged view, editable writable layers, build prune |
| TTY/editor UX | xterm.js terminal tabs, compact readonly log terminals, Japanese-friendly input, selection/copy controls, in-app editor |
| GPU/media | Vulkan/OpenCL bridge experiments, llama.cpp GPU comparison workflow, Camera2/AudioRecord/AudioTrack media proxy scaffold; not Docker GPU parity |
| Networking | Host-port style metadata and browser actions; bridge/IP parity is intentionally scoped as limited |
| Licensing | External payloads are audited; PRoot/talloc/proot-loader are not part of the default product APK |

See [`docs/plan/STATUS.md`](docs/plan/STATUS.md) for the detailed
implementation snapshot, [`docs/plan/TODO.md`](docs/plan/TODO.md) for the live
task board, and [`docs/release/RELEASE_READINESS.md`](docs/release/RELEASE_READINESS.md)
for the release checklist and current blocker summary.

For a GitHub-friendly view of the current demo surface, template library,
compatibility counters, and TODO-linked timeline, see the generated
[`Showcase Dashboard`](docs/showcase/PROJECT_DASHBOARD.md) and
[`Roadmap Timeline`](docs/showcase/ROADMAP_TIMELINE.md).

## What To Expect Today

The most useful current workflows are:

1. Install the compat APK on an SDK28-capable test route.
2. Start `skydnird` from the app.
3. Pull or build an image from the native UI.
4. Open image/container files directly from the app.
5. Run Compose-style project actions and watch logs in persistent job cards.
6. Use project templates for VS Code Server or llama.cpp experiments.

The most important current limits are also explicit:

- Android direct execution is still being hardened for broader image coverage;
  current claims are device-tested snapshots, not universal Android guarantees.
- Docker bridge networking is represented as metadata plus host-port behavior,
  not a real Linux bridge namespace.
- GPU acceleration is under active bridge work. llama.cpp CPU/no-offload runs
  have device evidence, and Vulkan offload paths produce diagnostic artifacts,
  but llama.cpp GPU inference is unfinished: `ngl>=1` correctness remains
  blocked and must not be presented as working acceleration.
- Media devices are exposed through an Android API proxy contract, not raw
  `/dev/video*` or `/dev/snd/*` passthrough.

## Screens and Workflows

The main UI is split into an upper control pane and a lower tool pane:

- Upper pane: overview, Compose, Dockerfile, project health, images,
  containers, storage metrics, job cards, and lifecycle controls.
- Lower pane: grouped terminal/log/editor tabs. A container can keep its own
  console and editor tools together without losing the session when the user
  navigates away.

The app exposes Docker-like workflows through widgets first. Terminals are
available when needed, but the normal path is not "drop to host shell and type
commands".

## Runtime model

Android apps do not get the kernel primitives that upstream Docker expects:
namespaces, cgroups, overlayfs, netlink, privileged mounts, and bridge
networking are unavailable or heavily constrained. Skydnir replaces those with
userspace components:

| Upstream Docker piece | Skydnir approach |
|---|---|
| dockerd | `skydnird` / compatibility `pdockerd`, a Python Engine API daemon hosted through Chaquopy |
| containerd image pull | `crane export` to tarball, then controlled extraction |
| overlayfs snapshotter | content-addressed layer pool plus per-container upper data |
| runc namespaces/cgroups | Android direct userspace executor and syscall mediation |
| BuildKit | legacy-compatible builder path in the Skydnir daemon |
| Docker CLI UX | native app actions, persistent job cards, and test-staged CLI only; upstream Docker CLI/Compose are not APK payloads |

The runtime strategy and Android feasibility notes live in
[`docs/design/RUNTIME_STRATEGY.md`](docs/design/RUNTIME_STRATEGY.md) and
[`docs/design/API29_DIRECT_EXEC_FEASIBILITY.md`](docs/design/API29_DIRECT_EXEC_FEASIBILITY.md).

## Build

Build and install commands live in [`docs/build/README.md`](docs/build/README.md).

Short form for the SDK28 direct-exec compatibility APK:

```sh
bash scripts/setup-env.sh
PDOCKER_ANDROID_FLAVOR=compat bash scripts/build-apk.sh
```

For a fixed-signature release APK, keep signing material outside Git and pass
it through environment variables:

```sh
export SKYDNIR_SIGNING_STORE_FILE=$HOME/.skydnir/release.jks
export SKYDNIR_SIGNING_STORE_PASSWORD=...
export SKYDNIR_SIGNING_KEY_ALIAS=skydnir
export SKYDNIR_SIGNING_KEY_PASSWORD=...
PDOCKER_ANDROID_FLAVOR=compat PDOCKER_ANDROID_BUILD_TYPE=release bash scripts/build-apk.sh
```

Signing keys and certificates are intentionally ignored by Git.

## Test gates

Fast checks used during normal development:

```sh
bash scripts/verify-fast.sh
python3 scripts/verify-ui-actions.py
python3 scripts/verify-project-library.py
```

Slower/device checks:

```sh
bash scripts/verify-heavy.sh --backend-quick
ANDROID_SERIAL=<host:port> bash scripts/android-device-smoke.sh --no-install
ANDROID_SERIAL=<host:port> bash scripts/android-runtime-bench.sh
```

Compatibility and compliance records are maintained under
[`docs/test/`](docs/test/README.md).

## Documentation map

- [`docs/README.md`](docs/README.md): documentation index and maintenance rules
- [`docs/manual/`](docs/manual/README.md): user-facing workflows and promotion assets
- [`docs/design/`](docs/design/README.md): architecture, scope, feasibility, GPU design
- [`docs/build/`](docs/build/README.md): local build, signing, install, and APK gates
- [`docs/test/`](docs/test/README.md): repeatable test scenarios and audit outputs
- [`docs/plan/`](docs/plan/README.md): live status, TODOs, and steering records
- [`docs/release/`](docs/release/README.md): release gates, fixed build evidence, and
  distribution process
- [`docs/showcase/`](docs/showcase/README.md): generated or curated
  GitHub-facing dashboard, roadmap, news, and Wiki seed pages
- [`docs/maintenance/`](docs/maintenance/README.md): documentation inventory,
  deduplication backlog, and cleanup sequencing

Root-level standards:

- [`LICENSE`](LICENSE): repository license status
- [`SECURITY.md`](SECURITY.md): vulnerability reporting and secret handling
- [`CONTRIBUTING.md`](CONTRIBUTING.md): issue, PR, testing, and scope guidance
- [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md): third-party inventory

## Suggested GitHub topics

`mobile-runtime`, `userspace-runtime`, `compose`, `docker-engine-api`,
`android-apk`, `vscode-server`, `llama-cpp`, `vulkan`, `syscall`,
`ptrace`, `mobile-development`, `runtime-cells`

## Project posture

Skydnir is a research-heavy product build, not a Docker trademark replacement.
The compatibility target is selected Docker Engine API-compatible behavior
where the host platform allows it, explicit Skydnir extensions where mobile
runtime cells need a different shape, and honest UI feedback when a feature is
metadata-only or still incomplete.

Docker and the Docker logo are trademarks or registered trademarks of Docker,
Inc. Android is a trademark of Google LLC. Skydnir is not affiliated with,
endorsed by, or sponsored by Docker, Inc. or Google LLC.

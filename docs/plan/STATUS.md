# Skydnir: implementation status

Snapshot at v0.5.3 / build 20260505.1. This document covers fixed-build
implementation evidence: (1) what the Skydnir daemon implemented for that build,
(2) what worked on the Android APK end-to-end for recorded evidence, and
(3) the known gaps vs upstream Docker Engine. It is not a live ADB/device
status page.

For the active unfinished-work list, especially temporary accommodations that
must become real implementations later, see [`TODO.md`](TODO.md).

## Latest fixed build

Build `20260505.1` is the current fixed Android build record for announcement
copy and is committed at `dd3ce31`. Evidence is stored under
[`../release/builds/20260505.1/`](../release/builds/20260505.1/), with release-note
summary in [`../release/RELEASE_NOTES_20260505.1.md`](../release/RELEASE_NOTES_20260505.1.md) and
project-news copy in
[`../showcase/NEWS_TIMELINE.md`](../showcase/NEWS_TIMELINE.md). Compat/modern
APK outputs built, Android quick smoke passed, and the historical Android full
smoke passed Dockerfile build, Compose up/down, `docker exec`, and a basic
Engine API `exec -it` path. This is fixed-build evidence, not current
promotion evidence for terminal, service-truth, teardown, image-pull
crash-safety, or release-honesty gates. The public blockers remain those live
device gates, the literal test-density failure, and the host backend
direct-executor lane mismatch. Unsigned release artifacts and warning cleanup
are release-process notes, with signing material kept outside Git.

## At a glance

| layer | size | status |
|---|---|---|
| **Skydnir daemon** (`pdockerd` compatibility binary, docker-proot-setup/bin) | 3500 LOC | Engine API 1.43-compatible, ~30 endpoints |
| **APK** (Skydnir) | 31 MB | install, foreground service, Engine API, image pull/browse/edit flows; SDK28 compat smoke paths have historical device evidence, while modern/API29+, terminal, service truth, and teardown remain gated |
| **Workspace UI** | native widgets + xterm.js 5.3 + JNI pty tabs + editor | Compose, Dockerfile, images, containers, and `-it`-style sessions share one console surface |

## Implementation overview

### 1. The "no kernel features" trick

Kernel namespaces, mount, cgroups, netlink, fuse — none of them are available
inside a regular Android app sandbox. So the stack is built on user-space
replacements:

| upstream Docker bits | what we use instead |
|---|---|
| containerd snapshotter (overlayfs) | content-addressable layer pool + per-container hardlink-clone tree |
| overlayfs CoW | `libcow.so` LD_PRELOAD shim — break_hardlink on `open(O_WRONLY/RDWR/TRUNC)`, `truncate`, `chmod`, etc. |
| runc (kernel ns + cgroups) | Android `pdocker-direct` userspace executor (seccomp/ptrace path mediation) |
| dockerd | skydnird / `pdockerd` compatibility daemon — Python HTTP server speaking Docker Engine API 1.43 over unix socket |
| BuildKit | legacy builder path (FROM/RUN/COPY/ADD/WORKDIR/ENV/ARG/CMD/ENTRYPOINT in the Skydnir daemon) |
| containerd image pull | `crane export` → tarball → Python tarfile extract |

### 2. Endpoint coverage in the Skydnir daemon

Endpoint and protocol coverage is maintained in
[`../test/COMPATIBILITY.md`](../test/COMPATIBILITY.md) and the generated
[`../test/compat-audit-latest.md`](../test/compat-audit-latest.md). Keep those files as the
canonical API compatibility record; this status file only summarizes the system
shape.

### 3. End-to-end Android verification

What was confirmed on the Android 15 test device for this fixed build:

- `adb install Skydnir.apk` → MainActivity launches → "Start skydnird" compatibility action → unix socket binds at `filesDir/pdocker/pdockerd.sock`
- `curl --unix-socket .../pdockerd.sock http://d/_ping` → `OK`
- `docker version` (CLI 29.4 client → Skydnir `pdockerd` compatibility server) → both sides report API 1.43
- `docker pull ubuntu:latest` → 132 MB image landed under `filesDir/pdocker/{images,layers}/` in 52s
- Tiny SDK28 compat `docker build` and `docker compose up --build` execute real
  container processes through `pdocker-direct`; compose logs show service
  stdout and `compose down` stops the service.
- The default VS Code/Codex/Continue workspace has been built and started on
  SOG15 through the direct runtime. The real code-server endpoint responds on
  `127.0.0.1:18080` with HTTP 302 to `./?folder=/workspace`. Next UI/device
  work is to make the service health card prove that listener belongs to the
  current Engine container ID and to open the matching logs.
- The llama.cpp GPU workspace starts through the UI/Engine-compatible compose
  path in CPU fallback mode. The 8B Qwen3 GGUF server responds on
  `127.0.0.1:18081`, `/v1/models` reports the loaded model, and `docker logs`
  streams real llama-server output. GPU attempts remain a measured workflow:
  CPU fallback hides Vulkan devices, forced Vulkan is used only for benchmark
  runs, and completion requires the compare artifact to report speedup,
  `target_met`, GPU layer count, blocker, and device/thermal metadata. GPU
  liveness is not GPU correctness: deterministic `/completion` probes on
  2026-05-07 found wrong first-token output in forced Vulkan mode while CPU
  fallback answered the same addition probe correctly. The project-library
  template now carries `pdocker-llama-correctness`; GPU runs may be described as
  verified only when that JSON report passes.
- xterm.js WebView terminal → spawns sh with `PATH=runtime/docker-bin:...` and `DOCKER_HOST=unix://...` so user can type `docker ps` directly
- Terminal UTF-8 output is decoded through `TextDecoder`, uses an Android/CJK
  monospace font stack, disables IME autocorrect/capitalization, and reports
  resize changes from both window and visual viewport.
- Main UI → tabbed workspace for Overview, Compose, Dockerfile, Images,
  Containers, and Sessions. Tabs show widget-style state, counts, paths, and
  log previews in the native UI instead of immediately dropping into a console.
- Container widgets show `State.Status`, synthetic IP, Docker-visible ports,
  planned port-hook rewrite count, and Skydnir networking warnings such as
  metadata-only port publishing. The next reconciliation pass must treat Engine
  container IDs plus Skydnir project/service labels as truth; names are display
  hints or legacy fallbacks only, especially after interrupted compose runs.
  Widgets also expose direct start/stop/restart, log, file-browser,
  known-service URL, and grouped interactive console actions.
- Main UI action wiring → product actions start the Skydnir daemon and use Engine
  API/native orchestration for image pull, Dockerfile build, Compose-style
  create/start, logs, and lifecycle state. Upstream Docker CLI/Compose binaries
  are not packaged in the APK; they remain host/test compatibility tools only.
- Main UI job tracking → Docker-backed actions also create native upper-pane
  job cards backed by `filesDir/pdocker/jobs.json`. The cards show
  running/done/failed status, elapsed time, command context, parsed
  build/compose/pull progress, and recent output captured from the PTY stream.
  Running jobs can be stopped from the card, and finished jobs can be retried
  or opened as lower-pane log tabs. Persisted jobs that no longer have a live
  PTY tab after app restart are marked interrupted and open their saved log
  instead of leaving a dead card, so build/compose progress remains visible
  when the lower terminal tab is not selected.
- Terminal UI → one screen can host multiple PTY-backed sessions and switch
  between them with tabs. `DOCKER_HOST` is prewired, container terminal actions
  use the Engine exec path with `Tty=true`, and the Android smoke covers a basic
  interactive exec exchange. Back navigation returns to the workspace without
  closing the terminal Activity, so live PTY tabs remain available when reopened.
- Main UI → Compose and Dockerfile tabs can create/edit project files through
  the in-app text editor under `filesDir/pdocker/projects`.
- Main UI → Sessions lists recent editable project/imported files as native
  widgets that open lower-pane editor tabs.
- First launch seeds `filesDir/pdocker/projects/default` from
  `assets/default-project/`, providing a Dockerfile/Compose workspace with
  code-server, Continue, OpenAI Codex CLI, and common dev tools.
- Existing generated project templates are migrated from the former
  `8080`/`8081` service ports to `18080`/`18081` during app startup or template
  installation.
- Main UI → "Browse image files" opens a browser for pulled image rootfs trees
  under `filesDir/pdocker/images/*/rootfs`, and container cards can open
  created container `rootfs`/`upper` trees. cow_bind containers expose a merged
  lower/upper view that prefers upper entries and honors upper-layer whiteouts.
  Users can inspect image/container contents without starting a temporary
  container or invoking the docker CLI. Individual files can be copied into
  `filesDir/pdocker/projects/imports/`; writable container layers can be edited
  directly, and read-only lower-layer files can be copied into the container's
  writable overlay before editing.
- Android direct execution now advertises and uses the Skydnir daemon `cow_bind`
  lower/upper contract for container create/start. Large images are no longer
  copied into every new container: SOG15 dev-workspace create measured about
  77.35s before this path and about 1.10s after it; fresh `pdocker-dev`
  create/start measured about 0.382s/0.389s. Gradle builds also sync the
  current `docker-proot-setup/bin/pdockerd` into APK assets before packaging.
- Successful Android UI rebuilds prune unreferenced layer-store entries after a
  tag is replaced, and Dockerfile `RUN` snapshots now use a parent-stack cache
  so unchanged apt/npm-heavy steps do not repeatedly create new multi-GB
  layers.
- The default VS Code workspace rebuild now has a whole-image cache path for an
  unchanged Dockerfile/context/tag. SOG15 measurements went from 129s, to 62s
  with RUN cache reuse, to 0s at shell-second resolution with direct tagged
  image reuse (`Using image cache for docker.io/pdocker/dev-workspace:latest`).
  Simple metadata-only `RUN chmod ...` uses touched-path snapshotting when this
  full-image cache is invalidated.
- `GET /system/operations` reports active daemon-owned operations, and the
  Overview renders them. This makes ADB/test-triggered builds visible in the UI
  instead of only showing operations launched from Android job cards.
- Offline UI regression check → `python3 scripts/verify-ui-actions.py` records
  the expected native menu/action wiring for persistent Docker terminals,
  image deep-links, editor tab identity, terminal key palette, and editor tools.

Near-term verification queue generated on 2026-05-04:

- Real listener health for `18080` and `18081`, tied to current container IDs,
  container health state, and logs.
- Active port mapping state, moving from requested-port metadata to
  inactive/blocked/active listener or proxy evidence.
- Android storage metrics checks for layer, image-view, container-private,
  total, and free-space values after build, prune, rebuild, and edit/copy-up
  flows.

Current open-risk anchors as of 2026-05-05:

- Android Documents access is an explicit `/documents` exchange mount only when
  the user selects it through SAF. Hot paths such as runtime state, model files,
  caches, layers, and project internals remain app-private unless a Compose
  volume or bind says otherwise.
- Storage reporting must count the shared layer pool once. Image/container
  apparent sizes and merged rootfs views overlap lower-layer bytes, so they are
  inspection values rather than additive unique-usage totals.
- Service health must come from Engine container state, the current Engine
  container ID, a real listener check, and matching logs. Project cards,
  Compose metadata, requested ports, stale names, and completed jobs cannot
  establish healthy status on their own.
- The interactive terminal regression is most likely the PTY fallback/pipe path
  running a noninteractive shell; the observed symptom is
  `/usr/bin/[: extra argument b]`. Fixes should preserve PTY allocation and
  argv semantics instead of changing template scripts to hide the failure.
- A freeze risk remains because one Engine stop returned HTTP 204 while
  `pdocker-direct`/child processes and the GPU executor stayed alive. Runtime
  stop is not complete until process-tree and executor teardown are proven.
- The llama GPU probe with `--gpu-layers 1`/`--n-gpu-layers 1` offloaded only
  the output layer. The next meaningful GPU tests need `--gpu-layers >=2` and
  artifact/log evidence such as `offloading N repeating layers`.

### 4. Android-specific workarounds (how we got here)

| problem | fix |
|---|---|
| crane (pure-Go) reads `/etc/resolv.conf` — Android app sandbox doesn't have it | in-process Python HTTP CONNECT proxy in pdockerd_bridge → `HTTPS_PROXY` env (uses bionic getaddrinfo) |
| Go x509 default certFiles list misses Android | `SSL_CERT_DIR=/system/etc/security/cacerts` |
| Skydnir daemon `/tmp` blob staging EACCES | `PDOCKER_TMP_DIR=runtime/tmp` |
| SELinux denies `link()` on app_data_file | `os.link` probe at startup → `PDOCKER_LINK_MODE=symlink` for tar extraction + clone |
| SELinux denies `setxattr` in security.* | `_copy_no_xattr()` substitute that drops xattr/flag copy |
| Files in app data have `exec_no_trans` SELinux deny | crane/pdocker-direct/libcow shipped via jniLibs/arm64-v8a/lib*.so so they extract to `nativeLibraryDir` (the only exec-allowed location in app sandbox); upstream Docker CLI/Compose stay test-only and are not APK payload |
| Android 15 rejects PRoot tracee memory rewrite during exec | no-PRoot runtime is selected by default; SDK28 compat uses the scratch `pdocker-direct` executor for real process tests |
| bionic-built libcow.so won't load inside ubuntu (libdl.so vs libdl.so.2) | ship the host-glibc libcow build instead — the Skydnir daemon just `shutil.copy`s it into the container, container's own ld.so does the loading. Fast defaults skip read-only fd tracking and xattr copy-up unless `PDOCKER_COW_TRACK_READONLY_FDS=1` or `PDOCKER_COW_COPY_XATTRS=1` is set. |

### 5. Gaps vs upstream Docker

The detailed gap table lives in [`../test/COMPATIBILITY.md`](../test/COMPATIBILITY.md).
The active implementation plan for closing those gaps lives in
[`TODO.md`](TODO.md).

### 6. What it can do that mainline docker can't

- Run as a normal Android app, no root, no Termux — single APK install on Android 8+ (API 26).
- Run in a regular app sandbox without dockerd (no namespace/mount/netlink kernel features).
- Pull and execute aarch64 Linux containers on a phone, with TLS DNS via the system resolver (HTTP CONNECT proxy in the daemon process).

## File map (Android APK)

```
skydnir/
├── app/src/main/
│   ├── AndroidManifest.xml           — INTERNET, ACCESS_NETWORK_STATE,
│   │                                   FOREGROUND_SERVICE_DATA_SYNC,
│   │                                   POST_NOTIFICATIONS, WAKE_LOCK,
│   │                                   REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
│   │                                   RECEIVE_BOOT_COMPLETED
│   ├── kotlin/io/github/ryo100794/pdocker/
│   │   ├── MainActivity.kt           — resizable split workspace + LocalSocket
│   │   │                              /_ping poll; upper pane for Compose,
│   │   │                              Dockerfile, status/control; lower pane
│   │   │                              for grouped console/editor tabs; container
│   │   │                              cards show IP/ports/hook plan
│   │   ├── ImageFilesActivity.kt     — browser/editor handoff for image and container rootfs files
│   │   ├── TextEditorActivity.kt     — Compose/Dockerfile code editor host
│   │   ├── CodeEditorView.kt         — line numbers, visible whitespace,
│   │   │                              highlighting, search/replace,
│   │   │                              tab/space conversion, selected-line
│   │   │                              indent/outdent
│   │   ├── PdockerdService.kt        — resident ForegroundService (dataSync),
│   │   │                              notification action + task-removal restart
│   │   ├── PdockerdBootReceiver.kt   — boot / package-replaced daemon restart
│   │   ├── PdockerdRuntime.kt        — extracts assets/pdockerd, symlinks
│   │   │                              nativeLibraryDir lib*.so into runtime/
│   │   ├── TerminalActivity.kt       — WebView host
│   │   ├── Bridge.kt                 — JS↔ pty bridge with DOCKER_HOST env
│   │   └── PtyNative.kt              — JNI wrapper around libpdockerpty.so
│   ├── cpp/
│   │   ├── pty.c                     — forkpty + TIOCSWINSZ + fd table
│   │   └── CMakeLists.txt            — unused (native helpers build via scripts/build-native-android-ndk.sh)
│   ├── res/values*/strings.xml       — English / Japanese UI localization
│   ├── jniLibs/arm64-v8a/             — auto-generated, .gitignored
│   │   ├── libcow.so                 — host-glibc CoW shim (loaded inside container)
│   │   ├── libcrane.so               — crane 0.20.3 (static Go)
│   │   ├── libpdockerpty.so          — Android NDK-built JNI/PTY helper
│   │   └── libpdockerdirect.so       — Android direct userspace executor
│   ├── python/pdockerd_bridge.py     — Chaquopy entry: env setup + CONNECT proxy + runpy
│   └── assets/
│       ├── pdockerd/pdockerd         — 132 KB Python script (extracted on first launch)
│       ├── xterm/index.html          — terminal UI + shortcut key palette
│       ├── default-project/          — VS Code Server + Continue + Codex template
│       ├── project-library/library.json
│       └── project-library/llama-cpp-gpu/
│                                      — llama.cpp GPU/CPU workspace template
│       └── xterm/{index.html,xterm.js,xterm.css,xterm-addon-fit.js}
├── docker-proot-setup/                — integrated backend source
│   └── bin/pdockerd                   — Skydnir daemon compatibility binary (3500 LOC)
└── scripts/
    ├── build-native-android-ndk.sh    — Android NDK helper build path
    ├── copy-native.sh                 — backend + vendor/ → jniLibs
    ├── fetch-xterm.sh                 — pull xterm.js + FitAddon CDN
    └── build-apk.sh                   — orchestrator
```

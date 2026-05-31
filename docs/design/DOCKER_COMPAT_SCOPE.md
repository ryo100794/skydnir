# Docker Compatibility Scope

Snapshot date: 2026-05-03.

This document defines the intended product boundary for Skydnir as a
Docker-compatible Android app backend. The goal is not to clone every kernel
feature behind Docker Desktop or Moby. The goal is to provide a useful,
repeatable Docker-like workflow on unrooted Android while keeping data formats,
Engine API behavior, Compose files, Dockerfiles, and image/archive exchange as
portable as possible.

Compatibility has three tiers:

1. **Must match Docker data and client contracts**
   - Standard Dockerfile syntax.
   - Compose files accepted by the upstream Docker Compose plugin.
   - Docker image references, image configs, layer tars, save/load archives,
     and container archive API payloads where practical.
   - Docker Engine API over Unix socket and Android-local TCP (`127.0.0.1:2375`
     by default), including API version negotiation and Docker CLI compatibility
     for supported endpoints.

2. **Should behave like Docker for common developer workflows**
   - `docker pull`, `images`, `ps`, `logs`, `build`, `run`, `exec`, `cp`,
     `compose up/down/ps/logs` for the supported runtime subset.
   - Long-running service containers such as code-server and llama.cpp.
   - UI-first workflows where upstream Docker CLI/Compose binaries are not part
     of the shipped APK. Test suites may stage them separately to validate
     Engine API compatibility.

3. **May expose Skydnir-specific limitations explicitly**
   - Unsupported resource isolation, kernel networking, mount propagation, or
     BuildKit features must fail clearly or surface warnings in API/UI state.
   - Skydnir must not silently start fake listeners, mutate Dockerfile syntax,
     or run commands on the Android host when the user asked for a container.

## Skydnir Extension API Boundary

Docker-compatible endpoints and Skydnir extensions are intentionally separate:

- Standard Docker clients should use the Docker Engine API subset documented in
  [`../test/COMPATIBILITY.md`](../test/COMPATIBILITY.md). These endpoints keep
  Docker response shapes where practical and must fail clearly when a Docker or
  OCI feature is unsupported.
- Android UI, diagnostics, and Android-only bridges may use Skydnir extension
  endpoints under selected `/system/*` paths, excluding Docker-standard
  `GET /system/df` and `POST /system/prune`, and pdocker-prefixed response
  fields such as `PdockerGpu`, `PdockerMedia`, `PdockerNetwork`,
  `PdockerStorage`, and `PdockerWarnings`.
- Skydnir-aware clients may also use the `/skydnir/...` extension prefix. The
  daemon normalizes this prefix after optional Engine API version prefixes, so
  `/skydnir/version`, `/skydnir/system/host`, and
  `/v1.43/skydnir/version` resolve to their unprefixed handlers. The
  unprefixed routes remain canonical for standard Docker clients.
- Extension fields must not be required for basic Docker CLI compatibility.
  They exist to make Android-specific truth visible: storage accounting,
  service ownership, GPU/media bridge status, memory pressure, Documents/SAF
  mediation, and long-running operation state.
- OCI/runtime gaps must be stated as unsupported or partial rather than hidden
  behind Skydnir extensions. In particular, Skydnir extensions do not make
  Swarm, BuildKit, OCI runtime hooks, cgroups, namespaces, zstd layers,
  manifest-list preservation, registry push, OCI artifacts/referrers, or
  signatures supported.

## Default Product Line

Skydnir should be positioned as:

- A **Docker Engine API-compatible userspace runtime for Android**, optimized
  for development containers, AI tooling, image inspection, and repeatable
  Compose workspaces.
- A **data-compatible Docker client target** for a practical subset of Docker,
  not a complete replacement for Linux namespaces, cgroups, overlayfs, bridge
  networking, or BuildKit.

This means the app can be useful even when some low-level Docker guarantees are
not implementable without root or kernel features, as long as the boundaries are
honest and test-covered.

## Explicitly Out Of Scope

These are not product goals for the default unrooted Android app:

- Real Linux namespaces (`pid`, `mnt`, `net`, `ipc`, `uts`, `user`) matching
  runc.
- Kernel cgroup resource enforcement.
- Kernel bridge networks, veth pairs, iptables/nftables, or Docker's embedded
  DNS server.
- True kernel bind mounts, mount propagation, tmpfs mounts, or privileged
  container devices.
- Full BuildKit/buildx parity, custom frontends, cache mounts, secrets,
  SSH mounts, remote builders, and multi-node build drivers.
- Exact overlayfs kernel behavior for every inode, xattr, hardlink, whiteout,
  and rename edge case.
- Running arbitrary host daemons as fake container services.
- Bundling PRoot/fakechroot or other external runtime code without explicit
  approval, license review, and a source distribution plan.

## Area Decisions

| Area | Product stance | Implement | Scope out / expose |
|---|---|---|---|
| BuildKit | Legacy builder first, standard Dockerfile syntax only. | Keep `/build` compatible enough for Docker CLI legacy builder. Support common `FROM`, `ARG`, `ENV`, `WORKDIR`, `COPY`, `ADD`, `RUN`, `CMD`, `ENTRYPOINT`, `EXPOSE`, `LABEL`, `USER`, `VOLUME`, `.dockerignore`. Preserve image history/config better over time. | Do not implement BuildKit daemon, buildx drivers, cache mounts, secrets, SSH mounts, custom `# syntax=` frontends, or remote builders in the near term. Reject or clearly fail features that require BuildKit. |
| Network | Host-network-like userspace model with explicit metadata. | Record Compose networks, service aliases, synthetic identity fields, exposed/published ports, and warnings. Inject `/etc/hosts` service aliases. Add container-aware port proxy or syscall-mediated bind/connect rewrite for practical `host:container` port mapping. | No bridge IP claims/isolation, veth, iptables/nftables, embedded DNS server, `macvlan`, `ipvlan`, or true network namespace. Never mark a port healthy unless a real container-owned listener/proxy/rewrite is evidenced. |
| Volumes | Host-directory-backed named volumes and best-effort binds. | Map engine-owned named volumes under app-private storage. Treat the selected Android Documents folder as the user workspace root for project definitions under `pdocker/projects` when it is writable by normal app-UID paths, and as the explicit import/export target through `/documents`. Persist SAF tree URI metadata and expose whether the selected storage is `direct-path-writable` or `saf-mediated`; in mediated mode use an app-private mirror plus sidecar metadata and Android `DocumentProvider` calls for create/list/exists/read/write instead of pretending a URI-backed SD-card tree is a writable POSIX path. Keep hot container homes, workspaces, model caches, databases, and high-frequency logs in app-private storage unless the Compose file explicitly maps them elsewhere. Keep bind mount metadata and mediate paths in direct runtime. Make UI show volume host path and container path. Support archive/copy against volume-backed paths. | No kernel mount propagation, tmpfs, block devices, privileged device mounts, SELinux relabel flags, exact read-only bind enforcement, or removable-SD SAF paths as direct Linux bind mounts; SAF access is a mediated exchange contract, not a direct executable rootfs or hot upperdir. |
| cgroups/resources | Report unsupported honestly; optionally approximate stats. | Parse common resource flags so Compose/CLI does not crash. Store requested limits in metadata. Return predictable warnings. Approximate CPU/memory stats from `/proc` where possible. | No hard enforcement for `--memory`, `--cpus`, pids limit, blkio, cpuset, cgroup namespaces, OOMScoreAdj parity, or Docker Desktop-style resource isolation. |
| overlayfs/storage | Skydnir-owned snapshotter with overlay-like semantics. | Keep content-addressed layers and per-container writable state. Implement whiteouts, copy-up, rename/unlink/chmod/chown/xattr/link semantics in pdockerd/direct runtime. Make `docker cp` and image browsing use merged lower/upper views. | Do not promise exact overlayfs inode identity, d_type, hardlink counts, all xattrs, opaque directory behavior, or mount-level semantics until tested. Avoid patched external overlay runtimes. |
| signals/process supervision | Docker-like lifecycle for common cases. | Track process groups, exit codes, waits, logs, stop timeout, and cleanup. Map `docker stop` to configured signal then kill after timeout. Keep `PTRACE_O_EXITKILL` and tracer signal handling. | No cgroup-wide kill guarantees, PID namespace semantics, init process reaping parity, `--pid=host`, or full `STOPSIGNAL` edge-case parity at first. |
| TTY/attach/exec | Real PTY-backed interactive sessions are in scope. | Connect Engine attach/exec TTY to the same native PTY infrastructure used by the app terminal. Support `docker run -it`, `docker exec -it`, resize, detach keys where practical, and UI tab persistence. The Android app daemon also exposes the Docker-shaped TermPort path on loopback TCP: `GET /_ping`, `GET /version`, `GET /containers/json`, `POST /containers/{id}/exec`, `POST /exec/{id}/start` with HTTP 101 `Upgrade: tcp` raw TTY bytes for `Tty=true`, and `POST /exec/{id}/resize`. The default endpoint is `127.0.0.1:2375`; `SKYDNIR_ENGINE_TCP_HOST` / `PDOCKER_ENGINE_TCP_HOST` can override or disable it for tests/custom launches. | Do not use Android host shell as a fallback for container console. Full Docker hijack edge cases and every detach-key combination can be staged after basic `-it` works. |
| archive API / `docker cp` | Keep Docker tar/header compatibility high. | Maintain `GET/PUT/HEAD /containers/{id}/archive`, `X-Docker-Container-Path-Stat`, tar streaming, path traversal defense, lower/upper merge, writable upper copy-in, and tests. | Sparse files, all device nodes, all xattrs, exact ownership mapping, opaque dirs, and every overlayfs whiteout edge case can be partial with documented warnings. |

## BuildKit Position

Recommended near-term stance: **do not implement BuildKit**. Keep using the
the Engine API legacy builder protocol against pdockerd and make the supported
Dockerfile subset increasingly correct. Upstream Docker CLI can remain a
test-only compatibility client, not an app payload.

Rationale:

- BuildKit is a large solver, cache, frontend, worker, and snapshotter system,
  not just a protocol flag.
- Most Android value today comes from Compose workspaces, `apt-get`, Node,
  code-server, llama.cpp, image browsing, and repeatable basic builds.
- Supporting standard Dockerfiles without Skydnir-specific syntax keeps project
  files portable to real Docker.

Decision point:

- If a future workflow needs BuildKit-only features, prefer translating a small
  explicit subset into pdockerd behavior or failing with a clear diagnostic
  before considering a BuildKit-compatible solver.

## Network Position

Recommended near-term stance: **host-network-compatible with a userspace port
proxy/rewrite layer**.

Minimum acceptable product behavior:

- `docker ps` and UI show requested ports.
- Engine/UI state distinguishes configured/planned, running-but-inactive,
  active runtime/proxy records, and blocked/conflicting ports.
- Compose service names resolve inside containers through generated hosts
  entries or a lightweight userland resolver.
- `ports: ["18080:8080"]` eventually works by rewriting/brokering bind/listen
  behavior, not by launching a fake host process.

Current scaffold:

- `PdockerNetwork.PortMappingStatus` derives visible port state from Docker
  `PortBindings`/legacy `NetworkSettings.Ports`, container running state,
  `/proc/net` listener ownership, runtime/proxy/rewrite evidence, peer
  host-port claims, and host listener conflicts.
- Status terms are deliberately narrow: `planned` means configured while the
  container is not running, `inactive` means the container is running but no
  runtime listener/proxy proof exists, `active` means pdockerd has recorded
  runtime listener/proxy evidence for that mapping, and `conflict` means the
  requested host port is already claimed by another container or listener.
- A mapping is reported as `active` only when pdockerd verifies a
  container-owned listener or runtime/proxy/rewrite code records live evidence.
  A running container with only requested Docker port metadata remains `inactive`.
- The runtime remains `host-network-only`; synthetic IP-like fields are
  compatibility identities only and the status structure does not imply bridge
  networking, iptables NAT, or reachability.

Out of scope:

- Docker bridge networking and per-container bridge IPs with isolation.
- iptables/nftables NAT.
- Rootless Docker's slirp4netns parity unless explicitly chosen later.

## Volume Position

Recommended near-term stance: **volumes are durable app-owned directories with
runtime path mediation**.

This is good enough for developer workspaces, model caches, package caches, and
source trees. It is not the same as kernel mounts. The UI should make the
backing location inspectable so data is not mysterious.

Selected SD-card/Documents storage may be FAT32 or exFAT and may only be
available through SAF `DocumentProvider` calls. Skydnir can use such storage as
an exchange payload area, but Unix metadata must live in app-private sidecar
metadata: emulated mode/uid/gid, symlink targets, xattr digests, hash/mtime
evidence, and conflict state. That sidecar must be rebuildable and checkable
from URI/document enumeration. This does not make removable media a direct
Docker rootfs or hot bind target; executable paths and high-churn state stay in
app-private storage unless direct POSIX writability is proven for that path.

Decision point:

- Enforce read-only binds in the syscall mediator, or initially treat `:ro` as
  metadata plus a warning? The stricter path is more Docker-like but needs more
  path operation coverage.

## cgroup Position

Recommended near-term stance: **metadata and warnings, not enforcement**.

Implement:

- Accept common resource fields from Engine API and Compose.
- Store them in inspect output. The in-app Compose orchestrator accepts
  `mem_limit`, `memswap_limit`, and `deploy.resources.limits.memory` and maps
  them to Docker Engine-style `HostConfig.Memory` and `HostConfig.MemorySwap`.
- Keep Skydnir memory paging separate from Docker cgroup compatibility:
  `PDOCKER_MEMORY_PAGER=managed` or `io.pdocker.memory-pager=managed` may use
  the requested memory budget as a pager policy input, but standard Compose
  memory keys alone must not silently opt a container into Skydnir-specific
  fault handling.
- Warn that Android app sandbox cannot enforce Docker cgroups.
- Provide best-effort stats from `/proc` and Android process APIs.

Scope out:

- Hard limits and cgroup accounting parity.

## Overlayfs Position

Recommended near-term stance: **build a Skydnir snapshotter, not an overlayfs
clone**.

Implement the semantics needed by package managers and `docker cp` first:

- whiteouts for delete;
- copy-up before write;
- rename and replace;
- chmod/chown/xattr/truncate;
- hardlink behavior that does not rely on Android app-data hardlinks;
- merged directory listing for archive and UI browsing.

Scope out:

- exact inode-level overlayfs behavior until there is a test proving it is
  required by real workloads.

## Signals Position

Recommended near-term stance: **Docker-like lifecycle, not namespace-perfect
process control**.

Implement:

- process group ownership;
- signal forwarding;
- `docker stop` timeout;
- wait/exit status;
- log finalization;
- cleanup of tracer/helper leftovers.

Scope out:

- PID namespace behavior and cgroup-wide signal guarantees.

## TTY Position

Recommended near-term stance: **make `-it` real and tab-persistent**.

The UI already has PTY-backed terminal tabs. The next compatibility step is to
wire Engine attach/exec TTY to the same native PTY layer so Docker CLI and UI
sessions converge.

Implement:

- `docker run -it`;
- `docker exec -it`;
- resize events;
- attach stream multiplexing vs raw TTY mode;
- tab persistence across Activity navigation.

Scope out initially:

- every Docker detach-key edge case;
- simultaneous multi-client attach parity.

## Archive API Position

Recommended near-term stance: **treat archive API as a high-priority data
exchange contract**.

The archive API is central because it powers `docker cp`, UI file browsing,
image/container file editing, and data portability.

Implement:

- strong tar compatibility tests;
- lower/upper merged reads;
- writes into upper/writable layer;
- path traversal protection;
- Docker-compatible stat headers.

Scope out initially:

- special files and metadata the Android sandbox cannot represent safely.

## Proposed Priority Order

1. **TTY/exec attach**: unlocks credible `docker run -it`, `exec -it`, and UI
   container consoles.
2. **Overlay/snapshot semantics**: fixes npm/dpkg/package-manager correctness
   and makes builds less fragile.
3. **Archive API completeness**: makes UI file access and Docker data exchange
   reliable.
4. **Signals/lifecycle**: prevents leftovers and improves `compose down`,
   restart, and logs.
5. **Network port proxy/rewrite**: makes service containers feel real without
   pretending to provide bridge networking.
6. **Volumes/read-only enforcement**: useful after path mediation is stronger.
7. **Resource/cgroup metadata**: important for Compose compatibility, but mostly
   warnings/stats rather than enforcement.
8. **BuildKit decision**: defer until the legacy builder subset and runtime are
   stable.

## Discussion Questions

- Should `:ro` volumes fail when they cannot be enforced, or warn and continue?
- Should unsupported cgroup limits be accepted with warnings, or should strict
  mode fail Compose files that request them?
- Should network mode default to host-like behavior for all Compose projects,
  or require explicit acknowledgement when ports are published?
- Should archive API prioritize Docker metadata fidelity or Android-safe file
  editing behavior when those conflict?
- Should BuildKit syntax lines fail immediately, be ignored with warnings, or
  be accepted only when they do not require BuildKit-only behavior?

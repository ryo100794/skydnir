# Docker compatibility audit

Snapshot date: 2026-05-04.

## Purpose

This document is the repeatable compatibility record for pdocker-android and
the `docker-proot-setup` backend. Compatibility here means three layers:

- Surface behavior: Docker CLI commands and Engine API endpoints.
- Definition/data exchange: Dockerfile, image config, save/load tar archives,
  container archive copy, and APK payload shape.
- Protocol: HTTP over Unix domain socket, API version negotiation, hijacked
  raw streams, tar content types, and Docker-specific headers.

For the product boundary, non-goals, and open design decisions around BuildKit,
networking, volumes, cgroups, overlayfs, signals, TTY, and archive APIs, see
[`../design/DOCKER_COMPAT_SCOPE.md`](../design/DOCKER_COMPAT_SCOPE.md). This file records what works
and what is tested; the scope file records what pdocker is choosing to be.

## Canonical Sources

- Product boundaries live in
  [`../design/DOCKER_COMPAT_SCOPE.md`](../design/DOCKER_COMPAT_SCOPE.md).
- Active gaps and acceptance checks live in [`../plan/TODO.md`](../plan/TODO.md).
- Current implementation summary lives in [`../plan/STATUS.md`](../plan/STATUS.md).
- Latest generated audit output lives in
  [`compat-audit-latest.md`](compat-audit-latest.md).

## How to run the audit

Fast offline audit:

```sh
python3 scripts/compat-audit.py --output docs/test/compat-audit-latest.md
```

The default audit flavor is `compat`, matching the product's current
process-exec validation route. Set `PDOCKER_ANDROID_FLAVOR=modern` only when
intentionally auditing the API 29+ metadata-only APK. Stale modern build
artifacts are ignored by the default compat fast gate; rebuild the compat APK
or set `PDOCKER_ANDROID_FLAVOR=modern` explicitly for a modern metadata audit.

Build-time fast gate:

```sh
bash scripts/verify-fast.sh
```

Native UI action wiring only:

```sh
python3 scripts/verify-ui-actions.py
```

Service truth / teardown acceptance-plan gate:

```sh
python3 scripts/verify-service-truth-plan.py
```

This static gate does not claim the Android runtime behavior is implemented.
It keeps the planned gap executable by requiring future device smokes and
evidence artifacts before service health or runtime teardown can be marked
complete.

Full backend regression, including public image pulls and container runs:

```sh
python3 scripts/compat-audit.py --full --output docs/test/compat-audit-latest.md
```

For iterative work, cap the long regression and record timeout as a test result:

```sh
python3 scripts/compat-audit.py --full --full-timeout 90 --output docs/test/compat-audit-latest.md
```

APK packaging verification:

```sh
bash scripts/build-apk.sh
python3 scripts/compat-audit.py --output docs/test/compat-audit-latest.md
```

Backend-only regression remains available in the integrated backend tree:

```sh
bash scripts/verify-heavy.sh --backend-quick
```

Long backend regression (overlayfs/compose/deep layers):

```sh
bash scripts/verify-heavy.sh --backend-full
```

Android device smoke scenarios:

```sh
bash scripts/verify-heavy.sh --android-quick --no-install
bash scripts/verify-heavy.sh --android-full --no-install
```

While a full audit is running, the backend regression daemon usually listens at
`/tmp/pdockerd-verify.sock`. You can inspect it with the repository test Docker
CLI:

```sh
DOCKER_HOST=unix:///tmp/pdockerd-verify.sock docker-proot-setup/docker-bin/docker ps -a
DOCKER_HOST=unix:///tmp/pdockerd-verify.sock docker-proot-setup/docker-bin/docker logs <container-id>
tail -f /tmp/pdockerd-verify.log
```

Most `docker run --rm` test containers are auto-removed quickly, so `docker logs`
is most useful for named or long-running containers created by the compose,
exec, stats, and network parts of the regression.

Latest recorded fast result: [compat-audit-latest.md](compat-audit-latest.md)
records the reusable offline/API/APK/license/UI/GPU design checks. APK payload
checks require that upstream Docker CLI and Docker Compose plugin binaries are
absent from the shipped app; those tools are test-only compatibility aids.

Latest Android device evidence supersedes the earlier helper-gate failure:
the compat direct backend advertises `process-exec=1`, tiny direct
build/compose smoke passes, and the default VS Code/Codex/Continue workspace
has been built and started on SOG15 with the real code-server listener
responding on `127.0.0.1:18080`. The next compatibility queue is to make that
listener evidence repeatable in UI/device health checks, tied to the current
Engine container ID and logs rather than name guesses or job metadata.

Recent focused backend smoke checks also passed for a small Dockerfile build, a
multi-step RUN/COPY/RUN Dockerfile build, and `docker compose up -d --build` /
`compose ps` / `compose down`. The full backend regression remains the slow
suite and should be recorded separately when it is run to completion.

## Image pull crash safety

Host static gate:

```sh
python3 scripts/verify-image-pull-crash-safety.py
```

Interrupted-pull device kill/restart evidence now has a concrete safe residue
recovery lane. The runner creates only scenario-owned `.pull-*`, `.old-*`,
layer `.tmp-*`, and malformed partial-layer paths, kills/restarts `pdockerd`,
and records post-restart Engine API probes. It must not be marked passed unless
the Android device artifact proves daemon restart recovery and all residue
assertions are true. The executable driver is:

```sh
python3 scripts/verify/runner/image_pull_crash_safety_device.py \
  --artifact docs/test/image-pull-crash-safety-latest.json \
  --execute-device
```

When ADB is absent, the driver writes `status=planned-gap` and
`success=false` rather than faking success. The artifact schema includes the
scenario id, device identity, command plan, phase results, evidence paths,
negative expected conditions, assertions, remaining gap, and cleanup policy.
Negative conditions include accepting `.pull-*` image stages, accepting
`.tmp-*` or metadata-mismatched layer directories, losing the old tag backup,
allowing `inspect` from a never-atomically-published interrupted pull, allowing
inspect/create from a partial local image that references incomplete layers, or
leaving scenario-owned partial/corrupt image/cache residue in the raw
post-restart store listing. Cleanup must collect logs/listings first and remove
only scenario-owned tags, containers, and artifacts, leaving unrelated worker
data untouched; the host evaluator attempts scoped cleanup after failed device
phases and still reports `success=false` if evidence is incomplete. The
remaining gap is a timed live registry-pull kill; the current device gate
intentionally avoids overwriting user images while proving startup recovery and
negative inspect/run behavior for partial image/layer residue. Host unit tests
also cover startup pruning of invalid/stale/unreadable build cache entries and
partial temporary blob/load/save files.

## Current compatibility matrix

| Area | Current status | Notes |
|---|---:|---|
| Engine API negotiation | Good | `/_ping`, `/version`, `/info`, API prefix stripping, and `Api-Version` response headers are implemented. |
| Image pull/list/inspect/delete | Good | Pull uses content-addressed layer extraction with staged tag publish, complete-layer cache validation (`tree/` plus matching `meta.json`), and startup cleanup for `.pull-*`, `.old-*`, `.tmp-*`, malformed layer residue, invalid/stale build-cache records, and partial tmp blob/load/save files. `python3 scripts/verify-image-pull-crash-safety.py` covers the static contract. The Android device runner now covers safe synthetic residue kill/restart recovery plus negative inspect/create proof for partial images/layers and host-side post-restart survivor scanning; timed live registry-pull interruption remains open. Private registry auth is not complete. |
| Image save/load | Partial | Docker-style tar exchange works for the implemented flattened image format. Multi-platform indexes, zstd layers, and all OCI edge cases are not complete. |
| Container create/start/stop/kill/wait/rm | Good | Implemented through the Android direct userspace runner and state files. No cgroups or namespaces. Project/UI reconciliation still needs to rely on Engine container IDs plus pdocker labels rather than container names. |
| Logs/attach/exec | Partial | Raw stream and hijack paths exist. Non-TTY exec works, and Android smoke covers a basic Engine `exec` with `Tty=true`. Full Docker attach parity, `docker run -t`, detach-key behavior, resize propagation, and broad interactive terminal cases still need more coverage. |
| `docker cp` archive API | Partial | HEAD/GET/PUT support Docker tar and `X-Docker-Container-Path-Stat`. cow_bind reads prefer upper then lower, writes target upper. Directory merge of lower+upper entries is still incomplete. |
| Stats | Partial | CPU/memory are approximated from `/proc`; network, blkio, and cgroup-limit counters are absent. Android storage metrics for layer, image-view, container-private, total, and free-space values need device refresh verification after build/prune/rebuild/edit flows. |
| Networks | Compose-compatible stub | List/create/connect/disconnect/inspect/delete satisfy common Compose flows. Synthetic IPs, Docker-visible ports, and explicit port-publishing warnings are recorded, but no bridge IPs, DNS server, iptables, or active port forwarding. Next coverage must distinguish requested mappings from active listener/proxy state. |
| Volumes/binds | Partial | Named volumes map to host directories; bind mounts are represented in runtime metadata and direct-run argv. No kernel mount propagation or tmpfs semantics. |
| Dockerfile build | Partial | Dockerfiles use Docker's standard instruction surface only; pdocker-specific Dockerfile instructions are rejected instead of treated as extensions. Legacy builder supports common instructions and a practical `.dockerignore` subset on the backend host. On Android direct mode, real `RUN` works for the current supported subset. BuildKit, buildx, multi-stage edge cases, cache mounts, and advanced frontend syntax are not implemented. |
| Compose | Partial | Product APK uses pdockerd/native orchestration rather than bundled upstream Docker Compose. Test suites may stage upstream Docker CLI/Compose separately to verify Engine API compatibility. Basic up/down flows work when the build/runtime path stays inside the supported subset; the default VS Code/Codex workspace has been built and started on-device through the direct runtime. |
| Events | Partial | `/events` now persists Docker-style JSONL lifecycle events and live-streams new events with basic `since`, `until`, and filter handling. It covers container/image/network/volume/build events, but does not yet reproduce every daemon-internal event emitted by Moby. |
| APK data exchange | Good | APK includes pdockerd, crane, libcow, pdocker-direct, xterm assets, and license notice asset. It omits PRoot, proot-loader, talloc, upstream Docker CLI, and upstream Docker Compose. |

## Protocol coverage

The audit checks these protocol details directly or statically:

- HTTP/1.1 over Unix domain socket.
- Docker API version prefix handling such as `/v1.43/version`.
- `Api-Version` response header.
- `application/vnd.docker.raw-stream` for logs/attach/exec.
- `application/x-tar` for image save/load and container archive exchange.
- `X-Docker-Container-Path-Stat` for `docker cp` stat behavior.
- Docker CLI `docker version` negotiation when the repository test CLI is
  executable on the current host.
- Docker event JSON objects over `/events`, including `Type`, `Action`,
  `Actor`, `time`, `timeNano`, `since`/`until`, and common filters.

## Definition and data exchange coverage

Covered today:

- Image references normalized into the local pdocker store.
- Image config fields used by `docker image inspect` and container create.
- Dockerfile legacy build context upload through `/build`.
- Docker save/load through `/images/get` and `/images/load`.
- Container archive copy through `/containers/{id}/archive`.
- APK asset/native payload expected by the Android runtime.

Known gaps:

- Complete OCI image layout/index fidelity, multi-platform manifest lists, zstd
  layers, and private registry credential flow.
- Full Dockerfile frontend behavior, BuildKit features, complete
  `.dockerignore` parity, and multi-stage/cross-platform build behavior.
- Android execution backend parity: extend the no-PRoot direct runtime beyond
  the current supported smoke paths, keep full ADB smoke as a release blocker,
  and add focused regressions for every syscall/runtime gap found by larger
  build and compose workloads.
  The normal full device smoke now drives the hidden WebView terminal route
  through `ACTION_PREFIX.action.SMOKE_UI_IT_SELFTEST` after compose has produced
  a concrete Engine container ID, and it also accepts
  `PDOCKER_UI_IT_SELFTEST_CONTAINER=<id-or-name>` for pre-existing containers.
  The runner collects `files/pdocker/diagnostics/ui-it-selftest-latest.json`
  and `engine-exec-input-latest.jsonl` into `PDOCKER_SMOKE_ARTIFACT_DIR` (or
  `tmp/device-smoke-artifacts/<timestamp>`).  If quick mode or a caller has no
  real container ID, the runner writes a `Status: planned-skip`,
  `Success: false`, `DeviceProofAttempted: false` artifact instead of reporting
  success; only a real container run whose artifact contains `Success: true`
  can pass the UI exec-it self-test gate.
- Real listener health is not yet a compatibility gate: service health must
  prove the listener belongs to the current Engine container ID, not merely that
  a configured port or stale name exists, and UI cards must not display healthy
  from configured ports, compose metadata, or successful background jobs alone.
  The executable acceptance plan is guarded by
  `python3 scripts/verify-service-truth-plan.py`; implementation evidence must
  be recorded as `docs/test/service-truth-latest.json` and show the same Engine
  container ID across the UI card, `/containers/json`, persisted `state.json`,
  process table, listener probe, and logs.  The app UI now writes its
  rendered-card side of the proof to the app-private
  file `files/pdocker/diagnostics/ui-rendered-service-truth-latest.json`.
  That export has `SchemaVersion: 1`, `Kind: ui-rendered-service-truth`,
  `EngineSnapshot` metadata, and a `RenderedCards[]` array with `Kind`
  (`project-card`, `service-card`, or `container-card`), `ProjectName`,
  `ServiceName`, `ContainerName`, `EngineContainerId`, `ContainerIdSource`,
  `TruthState` (`current`, `unknown`, `stale`, or `ambiguous`),
  `RenderedAtUnixMs`, and `LastEngineSnapshotAtUnixMs`.  If the Engine snapshot
  has not been fetched, the UI marks cards `unknown`; if only persisted
  `state.json` can name an ID, it marks cards `stale`; neither state is success.
  `scripts/android-device-smoke.sh --service-truth <target>` now writes the
  planned-gap artifact `files/pdocker/diagnostics/service-truth-latest.json`
  plus raw files under `files/pdocker/diagnostics/service-truth/`.  Its schema
  is intentionally non-passing (`Status: planned-gap`, `Success: false`) until
  a rendered UI card container ID, Engine API container ID, state.json ID,
  process-tree owner, listener socket owner, and current container log marker
  are all reduced to one `RequiredSameContainerId` proof.  The artifact records
  UI input files, `/containers/json`, `docker ps`, state snapshots, process
  table, configured/listening ports, per-container logs, and explicit
  unresolved gaps so a device run cannot be mistaken for fake success.  The
  current planned-gap runner now also emits machine-readable intermediate
  evidence: `engine-candidates.json` scores Engine container ID candidates from
  labels, names, target hints, and known service ports;
  `state-id-comparison.json` compares the selected Engine candidate against
  IDs found in persisted `state.json`; and `listener-probe.json` records each
  probed listener port alongside `/proc/net/tcp` match counts and TCP connect
  exit codes, and the smoke copies the UI rendered-card export into
  `files/pdocker/diagnostics/service-truth/ui-rendered-service-truth-latest.json`.
  These fields improve device debugging but still cannot promote
  `Success: false` until all truth sources agree on one current Engine
  container ID; missing, `unknown`, `stale`, or `ambiguous` UI cards remain
  explicit non-success evidence.
- Runtime teardown is not yet a compatibility gate: stop/kill/rm must prove
  process-tree and executor cleanup rather than trusting an HTTP 204 response.
  `scripts/android-device-smoke.sh --runtime-teardown <target>` now writes the
  planned-gap device artifact `files/pdocker/diagnostics/runtime-teardown-latest.json`
  plus raw files under `files/pdocker/diagnostics/runtime-teardown/`.  The
  artifact schema records `Status: planned-gap`, `Success: false`, target,
  stop/rm and kill/rm Engine container IDs, CLI exit codes, Engine API
  `/containers/json` and inspect HTTP captures, process-table snapshots,
  persisted `state.json` snapshots, lifecycle command logs, container logs, and
  unresolved proof gaps.  The probe snapshots teardown state before a
  best-effort `docker rm -f` cleanup so the planned-gap test does not
  intentionally poison later smokes with its own residue.  The required
  promoted evidence remains
  `docs/test/runtime-teardown-latest.json`, including listener absence and no
  orphan `pdocker-direct`/service/GPU executor residue for the stopped Engine
  container ID before this can become a passing compatibility gate.  The
  artifact is device-gated with `RequiresAdb: true` and
  `DoNotClaimDevicePassWithoutAdb: true`; host checks can only verify the
  schema.  New raw requirements include direct-child absence for inspect
  `State.Pid`, stale/duplicate container-name absence after `rm`, and cleared
  `PdockerLauncherPgid` / `PdockerProcessGroupId` fields in persisted state.
- Active port publishing remains unimplemented; requested mappings are visible
  metadata until listener/proxy/rewrite state is recorded and verified.
- Android storage metrics still need device verification for nonnegative values
  and refresh behavior after build, prune, rebuild, and container edit/copy-up
  flows.
- Full overlayfs semantics for deletions, rename, metadata operations, and
  merged directory listings in cow_bind mode.
- Strict libcow xattr preservation and fchmod/fchown on read-only file
  descriptors are opt-in performance/compatibility modes
  (`PDOCKER_COW_COPY_XATTRS=1`, `PDOCKER_COW_TRACK_READONLY_FDS=1`) rather than
  default hot-path behavior.

## Additional implementation plan

1. Expand `cow_bind` to overlayfs-like semantics:
   - Implement whiteouts for `unlink` and `unlinkat`.
   - Copy-up and rewrite `rename`, `renameat`, `renameat2`.
   - Copy-up metadata syscalls: `chmod`, `chown`, `setxattr`, `removexattr`,
     and truncate variants.
   - Implement merged directory view for host-side archive reads so `docker cp`
     sees lower-only, upper-only, and upper-overridden entries together.

2. Improve protocol fidelity:
   - Add regression tests for chunked upload bodies and hijacked attach/exec
     across Docker CLI versions.
   - Expand PTY coverage for `docker run -t`, `docker exec -it`, resize,
     detach keys, and UI terminal selection/copy behavior.

3. Improve data exchange:
   - Add OCI manifest-list/index import/export tests.
   - Add zstd layer rejection/handling tests and, later, decoder support.
   - Preserve more image config/history metadata through save/load/build.

4. Improve registry support:
   - Implement `/auth` enough for `docker login`.
   - Wire Docker config credentials into `crane`.
   - Add private registry smoke tests against a local test registry.

5. Improve networking and Compose:
   - Make unsupported port publishing explicit instead of silent.
   - Add active/inactive/blocked port-mapping checks that verify the host
     listener/proxy target, not just Compose metadata.
   - Expand `/etc/hosts` alias tests for Compose service names.
   - Document and test the host-network-only model in Compose examples.

6. Improve stats and resource flags:
   - Return explicit unsupported/zeroed fields for cgroup-only counters.
   - Add tests for `--memory`, `--cpus`, and unsupported resource flags so
     behavior stays predictable.
   - Add Android storage metric refresh checks for layer, image-view,
     container-private, total, and free-space counters.

7. Improve project/container identity and service health:
   - Use Engine container IDs and pdocker project/service labels as truth for
     UI cards, logs, lifecycle actions, and health checks.
   - Treat container names as display hints or legacy fallbacks only.
   - Add real-listener checks for the default workspace and llama service
     ports before marking a service healthy.

## Refactoring status

Completed in backend commit `d1906d3`:

- Shared container runtime path resolution through `_container_runtime`.
- Shared environment construction through `_container_env`.
- Added `_join_under` and `_container_host_path` to prevent archive path
  traversal and to route cow_bind reads/writes through the right lower/upper
  side.
- Removed duplicated env/rootfs/cow_bind setup from start, exec, and spawn
  paths.

Next cleanup candidates:

- Factor archive tar creation/extraction away from the HTTP handler.
- Split Dockerfile build execution from the daemon request handler.
- Centralize Docker API error response shapes and headers.

## Maintenance

- Keep this page as procedure and evidence, not product-positioning copy.
- Link to design docs for scope decisions instead of repeating boundary tables.
- Update [`compat-audit-latest.md`](compat-audit-latest.md) with the audit
  script, not by hand.

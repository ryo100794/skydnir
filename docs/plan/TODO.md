# pdocker TODO ledger

Snapshot date: 2026-05-18.

This is the working TODO list for unfinished items and deliberate temporary
accommodations. Keep this file current whenever a workaround is added so it
does not become product behavior by accident.

## Active Task Board

This board is the operating task list. Keep the detailed sections below as the
source of context, but update this board first when work starts, gets blocked,
or closes.

### Audit Synchronization 2026-05-13

The May 13 multi-agent audits promoted the following order as the current
planning ledger truth.  Keep this order when assigning agents, updating GitHub
issues, and deciding which planned gaps become hard gates.

1. **[#6](https://github.com/ryo100794/pdocker-android/issues/6)
   Service truth same-container-ID** `[P0 doing]`: UI cards,
   `docker ps`, Engine `/containers/json`, persisted state, process table,
   listener probes, and logs must prove the same current Engine container ID.
   Persisted `state.json`, Compose metadata, names, completed jobs, and port
   declarations are hints only. Current slice in progress: the
   `--service-truth` device artifact and host verifier now require all seven
   sources to name the same current 64-hex Engine container ID, including
   docker ps/API running state, state, selected process PID, listener owner
   PID, UI card current reason, and a matching
   `pdocker-service-truth-marker`, before the gate can leave `planned-gap`.
2. **[#10](https://github.com/ryo100794/pdocker-android/issues/10)
   Runtime teardown** `[P0 next]`: stop/kill must prove direct children,
   GPU executor helpers, listeners, logs, and stale PIDs are gone before the
   UI or API reports stopped. Current slice complete: focused
   `--android-runtime-teardown` lane now removes stale evidence, collects the
   detailed runtime-teardown directory, validates either non-promoting
   planned-gap evidence or a future `device-pass` artifact through
   `scripts/verify-runtime-teardown-artifact.py`, and emits conservative
   device-side `VerifierReduction`, `GapReasons`, `FailReasons`,
   `MismatchedContainerIds`, and `Survivors` diagnostics for both stop-rm and
   kill-rm. It now also reduces `/containers/json` after-rm absence and
   stale-name absence into the same-container proof. Host verifier hardening now
   rejects device-pass promotion unless listener owner, GPU/media-executor
   residue, and persisted-state teardown fields are reduced to the same exact
   Engine container ID, with negative fixtures covering those regressions.
   Remaining slice: produce that stricter evidence from a real adb/run-as
   device run before allowing promotion.
3. **[#4](https://github.com/ryo100794/pdocker-android/issues/4)
   llama GPU Q6_K and environment propagation** `[P0 doing]`: continue the
   Q6_K blocker without touching llama.cpp, Dockerfiles, models, or prompts.
   The compare script, pdockerd defaults, UI/compose path, and artifact
   verifier must use an audited GPU diagnostic environment so device results do
   not diverge by launch path. Granular executable units: (a) env manifest
   parity across compare, pdockerd, UI/compose, and verifier; (b) device
   readiness/headroom artifact before model load; (c) NGL=1 Q6_K
   workgroup/writeback oracle run; (d) artifact classification that keeps
   memory blockers and Q6_K mismatches non-promoting; and (e) only after a
   matching oracle, CPU/GPU benchmark reporting.
4. **[#11](https://github.com/ryo100794/pdocker-android/issues/11)
   Image-pull crash safety** `[P0 doing]`: partial pulls, `.pull-*`,
   `.tmp-*`, `.old-*`, interrupted layer extraction, tag publish, and startup
   recovery must be tested with an actual kill/restart device artifact. The
   synthetic residue runner is represented in the manifest; the timed live
   registry-pull interruption remains device-gated, scenario-owned only, and
   non-promoting until it proves no partial/user tag is published.
5. **[#12](https://github.com/ryo100794/pdocker-android/issues/12)
   COW/overlay mutation safety** `[P0 next]`: host-local `libcow` coverage now
   fails closed for copy-up, metadata, `rename()`/`renameat()` over hardlinked
   destinations, whiteout/rename/archive staging models, low-space, corrupt
   hardlink-ring rebuild, and a local copy-up kill-step orphan-temp recovery.
   The artifact verifier now requires concrete `copyup.before_rename` fail and
   kill evidence (`copy_up.before_rename` plus
   `copy_up.kill_before_rename_recovery`) before accepting a recovery JSON.
   Remaining release blocker: device daemon/helper kill-at-step restart
   evidence for the same mutation checkpoints. The COW kill-at-step device lane
   is present but non-promoting until adb/run-as evidence covers each required
   copy-up, rename, whiteout, archive, and metadata checkpoint.
6. **[#5](https://github.com/ryo100794/pdocker-android/issues/5)
   Terminal hard gate** `[P1 next]`: `exec -it` is not closed until an actual
   UI-driven container terminal passes Enter, Ctrl-C, cursor keys, `top`, `q`,
   resize, and IME regression checks. Static or skipped self-tests are not
   enough; the host artifact verifier must be paired with device
   `ui-it-selftest-latest.json` and a fresh
   `engine-exec-input-latest.jsonl` before the gate can promote. The JSONL must
   prove single-Enter submission, isolated ETX with no injected `c` for JP/EN
   IME Ctrl-C, ArrowUp reaching shell history instead of raw escape text, a
   stable `top` refresh, and `q` returning to a usable shell. Work units:
   terminal surface/session-type split; Engine exec/HTTP-upgrade byte capture;
   UI scripted input artifact; raw JSONL verifier with `--require-container`;
   resize/IME regression replay; and an explicit decision on the bundled
   `xterm.css` IME composition-position TODO marker. Host-only verifier results
   remain non-promoting.
7. **[#14](https://github.com/ryo100794/pdocker-android/issues/14)
   VS Code health gate** `[P1 next]`: default workspace success requires
   compose/build/run, `pdocker-dev` current Engine state, port `18080` listener,
   code-server reachability, extension evidence, and UI card truth agreement.
8. **[#15](https://github.com/ryo100794/pdocker-android/issues/15)
   SAF direct output** `[P1 next]`: `/documents` must be a SAF-backed UnixFS
   exchange layer with sidecar metadata and direct-write evidence. Host
   contract coverage now requires `DirectWriteEvidence`, non-promoting
   app-private fallback with explicit `fallbackRecorded`/`fallbackReason`,
   sidecar-backed Unix metadata for FAT/SD-like stores, and `LayerBoundary`
   proof that upper layers consume `FilesystemBackend`/`UnixMetadataBackend`
   instead of SAF internals. The default project `/documents` placeholder and
   `pdocker-new-project` wording must be tied to this gate so SAF fallback UX
   cannot drift from the UnixFS mediator design; real device SAF pass evidence
   remains separate and must not be fabricated.
9. **[#9](https://github.com/ryo100794/pdocker-android/issues/9)
   Release evidence honesty gate** `[P0 doing]`: planned-gap artifacts,
   skipped or unrun device lanes, and host-only checks that merely prove a gap
   is still visible are non-promoting. `tests/test_driver_manifest.json`,
   `docs/test/CI_GATE_LEDGER.md`, `docs/test/SCENARIOS.md`, this TODO, and the
   execution timeline must agree before any build/test run is described as a
   stable checkpoint. The current sync list includes llama GPU Q6_K
   workgroup/writeback correctness, terminal exec-it, APK memory pager/OOM-LMK,
   storage graph/layer maintenance UI, `linkat` hardlink semantics, Docker CLI
   `docker cp`/archive API, COW kill-at-step, and image live-pull interruption
   gates. Each gate stays planned-gap or otherwise non-promoting until its
   named device promotion condition produces a passing artifact. This audit
   splits broad blockers into separately executable units so a passing
   sub-check cannot accidentally promote its parent gate.
10. **Modern/no-PRoot runtime truth** `[P0 next]`: metadata-only flavors must
    not expose execution claims. Either complete the no-PRoot executor or
    hard-disable `RUN`, `docker run`, Compose service start, service health,
    and published-port claims with explicit runtime capability errors and a
    device artifact at `docs/test/no-proot-runtime-truth-latest.json`.

### Next Queue Generated 2026-05-04

- [doing] Cross-project incomplete implementation audit:
  `docs/plan/INCOMPLETE_IMPLEMENTATION_AUDIT_20260513.md` now tracks
  unfinished, partial, temporary, or insufficiently verified work across
  Markdown docs, Kotlin/Android UI, native/direct runtime, GPU bridge, scripts,
  and test ledgers.  Before claiming a feature is complete, reconcile it
  against that audit and either close it with implementation plus evidence or
  keep it visible as an explicit planned gap.
- [doing] Execution timeline and delegated task control:
  `docs/plan/EXECUTION_TIMELINE_20260513.md` converts the audit into staged
  gates, current agent assignments, merge checklists, and decomposition rules.
  Active T0 lanes are Android single-container smoke, memory-layer UI
  source/age telemetry, and service-health executable acceptance criteria.
- [doing] [#4](https://github.com/ryo100794/pdocker-android/issues/4)
  llama GPU bridge ABI: keep llama.cpp unmodified while expanding the
  pdocker Vulkan/OpenCL bridge from device discovery and model-buffer
  allocation toward generic ggml SPIR-V dispatch, persistent command transport,
  and measured CPU-vs-GPU comparison artifacts. Current slice: `VULKAN_DISPATCH_V2`
  carries the compute entry point and specialization constants across the
  container/APK bridge, serves the forced-GPU HTTP probe, and now exposes
  bridge upload/copy overhead as a performance blocker. Current correctness
  gate: NGL=0 default is green, SPIR-V materialization is opt-in, and the
  `small-f32-indexing`, `rope-yarn`, and `rms-norm` oracles match for the
  observed NGL=1 front-blocker hashes. NGL=1 still fails required probes; the
  next primary blocker is `0x274f68a67dfef210`, now classified as a
  `mul_mat_vec_q6_k`-like large quantized matvec. The bounded sample oracle now
  executes and mismatches 8/8 sampled rows, so the next split is Q6_K
  decode/math vs descriptor-view/local-size execution semantics. Current slice:
  the executor preserves three-dimensional specialized local size
  (`32x2x1` instead of a collapsed `32x1x1`), emits Q6_K 64-lane diagnostic
  evidence, and the compare runner refuses llama GPU starts when Android swap
  headroom is unsafe. `scripts/verify-llama-gpu-artifact.py` classifies memory
  blockers, Q6 workgroup-clear evidence, and remaining Q6 numeric mismatches so
  device results are not interpreted ad hoc. Environment propagation is now a
  first-class blocker: diagnostic flags used by the compare script, pdockerd
  defaults, UI/compose launches, and artifact verification must remain
  synchronized before a GPU result can be compared. The Q6_K workgroup and
  writable-output writeback diagnostics are non-promoting blocker evidence until
  the Q6_K oracle matches; they must not be used for benchmark or inference
  claims by themselves. Device-side validation steps are maintained in
  `docs/test/LLAMA_GPU_DEVICE_RUNBOOK_20260513.md`.
  Stage gates and compact-model handoff are maintained in
  `docs/plan/LLAMA_GPU_BRIDGE_NEXT_STEPS.md`.
- [next] [#5](https://github.com/ryo100794/pdocker-android/issues/5)
  Terminal `-it` interactive path: refactor the terminal stack according to
  `docs/design/TERMINAL_STREAM_ARCHITECTURE.md`. The UI must remain a generic
  terminal surface, while Docker exec/attach, local diagnostic PTY, daemon log,
  and job log streams become explicit session types with shared tests. Closure
  still requires the non-promoting device gate in
  `docs/test/TERMINAL_EXEC_IT_DEVICE_GATE.md`: fresh UI artifact plus raw
  Engine exec JSONL proving Enter once, isolated Ctrl-C ETX with no literal
  `c`, cursor-key history, stable `top` repaint, `q` exit, and resize route.
  Audit note: the bundled xterm stylesheet still contains an upstream-style IME
  composition-position TODO marker. Either close it with device IME evidence or
  explicitly exclude the vendored marker from pdocker-owned TODO scans.
- [doing] [#6](https://github.com/ryo100794/pdocker-android/issues/6)
  Service truth same-container-ID device gate: the listener health and
  ID/label truth work are one gate. Probe default workspace `18080` and llama
  `18081`, then accept only when UI card, `docker ps`, Engine
  `/containers/json` plus inspect running state, persisted `state.json`,
  process table, listener owner map, and current logs all agree on the same
  exact 64-hex Engine container ID. Project/service labels help select the
  candidate; names and configured ports remain display/debug hints only.
- [next] [#4](https://github.com/ryo100794/pdocker-android/issues/4)
  llama GPU performance workflow after Vulkan clamp: keep CPU fallback
  hiding Vulkan devices, force Vulkan only for measured GPU attempts, run the
  compare flow after every bridge fix, and report `target_met`, speedup, GPU
  layer count, current blocker, thermal/device metadata, artifact paths, and
  the `pdocker-llama-correctness` result. Benchmark claims are blocked when the
  correctness report fails or is missing.
- [next] [#4](https://github.com/ryo100794/pdocker-android/issues/4)
  MoE-aware GPU residency layer research and design: after the dense llama GPU
  bridge is correct, evaluate a pdocker-owned residency layer for MoE models
  without modifying llama.cpp, Dockerfiles, models, or prompts.  Prior art to
  track: EdgeMoE external-storage expert loading, Cache-Conditional Experts
  mobile routing/cache locality, MoE-Infinity activation-aware expert caching
  and prefetch, HOBBIT mixed-precision expert offload, and llama.cpp/ik_llama
  `--cpu-moe` / `--n-cpu-moe` / tensor override workflows.  pdocker-specific
  goal: observe expert-like tensor/buffer access through the GPU bridge,
  maintain hot expert buffers in a GPU-resident cache, back cold experts with
  app-private mmap/virtual-memory storage or SAF exchange storage when
  explicitly configured, expose cache hit/page-in/page-out/transfer metrics in
  the UI, and fail closed rather than claiming acceleration when correctness or
  residency evidence is missing.
- [done] Active port mapping proof slice: published ports now keep
  `PdockerNetwork.PortMappingStatus` truthful from evidence. Active requires a
  live container-owned `/proc/net` listener or verified runtime proxy/rewrite
  evidence; Docker/Compose metadata and bare active flags remain planned or
  inactive. Foreign listeners and peer host-port claims surface as conflicts,
  and container cards show active/inactive/planned/conflict counts.
- [doing] [#7](https://github.com/ryo100794/pdocker-android/issues/7)
  Android storage metrics verification: add device smoke/manual coverage
  that layer, image-view, container-private, total, and free-space values are
  nonnegative and refresh after build, prune, rebuild, and container edit flows.
  Host-side sequence validation now exists via
  `python3 scripts/verify-storage-metrics.py --sequence ...`; promotion still
  requires a real device sequence artifact for baseline/build/rebuild/edit/prune.
- [next] [#8](https://github.com/ryo100794/pdocker-android/issues/8)
  Reproducible release/F-Droid readiness: turn the local build wrapper
  into a broader pinned CI/release process with source-built native payloads,
  no silent APK self-extension, signing outside Git, license/source audit, and
  explicit user-directed runtime container download policy.
- [doing] [#9](https://github.com/ryo100794/pdocker-android/issues/9)
  First public release candidate gate: define and satisfy the minimum GitHub
  Release criteria for a build that is honest, repeatable, recoverable, and
  safe to test. Current evidence-honesty slice: classify host-only planned-gap
  verifiers, non-passing artifacts, and device-gated lanes without promoted
  artifacts as non-promoting evidence in the test driver manifest and ledgers. A
  `release-honesty` pass proves publication hygiene only; stable checkpoint
  credit still requires the P0 device artifacts listed in
  `docs/test/CI_GATE_LEDGER.md` and the blocker closures in
  `docs/release/RELEASE_READINESS.md`.
- [done] Agent recovery process is recorded in
  `docs/plan/AGENT_COORDINATION.md`: recovered agent results must be moved into
  implementation, focused docs, or TODO before they are considered durable, and
  TODO drives the generated public timeline.
- [done] Local build orchestration now has `scripts/build-all.sh` for compat
  native/GPU/APK builds, `PDOCKER_SKIP_NATIVE_BUILD` for avoiding duplicate
  native rebuilds, and documented dry-run/selective build behavior.
- [done] Direct syscall coverage has an ADB-free local lane:
  `python3 scripts/run_direct_syscall_scenarios.py --lane local`, covering
  static hook inventory, scenario manifest tests, fast-local listing, and
  Android-heavy dry-runs.
- [done] Storage metrics validation has an ADB-free fixture checker:
  `python3 scripts/verify-storage-metrics.py`, documenting shared layer-pool
  accounting, guarding against image-view double counting, and rejecting fake
  rebuild/edit/prune sequences that do not preserve shared-layer accounting.
- [done] Android single-device self-debugging has a localhost Wireless
  debugging helper: `scripts/android-selfdebug.sh` wraps pair/connect,
  install/start, logcat, `run-as`, and Unix-socket probes without enabling ADB
  TCP itself or scanning the LAN. The manual fallback remains
  `docs/test/ANDROID_SELFDEBUG.md`. This route still depends on Android
  Wireless debugging being enabled; normal production devices commonly require
  an active Wi-Fi association before that toggle is available.
- [done] ADB-free self-debug fallback has an APK-owned bundle export: the
  Debug resources panel can write
  `files/pdocker/diagnostics/self-debug-bundle-latest.json` and a
  Documents/SAF copy at `pdocker/diagnostics/self-debug-bundle-latest.json`
  without USB, Wi-Fi ADB, `run-as`, or host shell access. The bundle records
  app/build/device metadata, Engine API ping/version/info/container probes,
  Documents grant/export state, memory/process/fd snapshots, debug resource
  roots, and known diagnostic artifact paths. Localhost Wireless debugging and
  `scripts/android-selfdebug.sh` remain convenience routes only, not a
  no-Wi-Fi substitute.
- [next] ADB-free diagnostics follow-up: add a small fixture/verifier for the
  self-debug bundle schema and extend the in-app route only where needed for
  active operation/job launch state or log excerpts that are not already covered
  by Engine probes, artifact summaries, and the Documents JSON export.
- [done] Root script clutter now has a first-pass inventory gate:
  `scripts/script-inventory.json`, `scripts/README.md`, and
  `scripts/verify-script-inventory.py` classify top-level scripts into
  runtime/package-needed, build/developer, test/verification,
  generated/maintenance, and obsolete-suspect buckets. Future moves must keep
  top-level compatibility wrappers until references migrate. The 2026-05-18
  focused audit found no active callers for `scripts/android-terminal-it-repro.sh`,
  `scripts/verify-llama-startup-logging.py`, or `scripts/wrap-ndk-box64.sh`;
  each remains retained as `obsolete-suspect` with a replacement command and
  explicit deletion precondition in the inventory/README rather than being
  removed in this cleanup slice.
- [done] F-Droid/reproducible-build readiness is captured in
  `docs/release/FDROID_RELEASE_PROCESS.md`, including the distinction between
  user-directed container/image/package downloads and hidden APK self-extension.
- [done] Daemon storage summaries now separate shared layer-pool bytes,
  per-image virtual/shared/unique bytes, container upper/private bytes, and
  merged image/rootfs view bytes with explicit overlap notes so UI totals do
  not double-count hardlinked lower data.
- [done] Default workspace Dockerfile placeholder repair cleanup: the
  `Dockerfile.pdocker-broken-backup` path is intentional bounded compatibility
  repair logic for installs created from the short-lived placeholder template.
  `MainActivity.repairDefaultDevWorkspaceDockerfile` only rewrites the exact
  known placeholder when compose still needs `start-code-server`, keeps the
  original as a one-time operator backup, and `scripts/verify-ui-actions.py`
  statically guards that narrow migration contract.

### Post-Build Conversation Intake 2026-05-06

These items were raised after the last fixed build record and must be closed
with implementation plus verification before the next build is treated as a
stable checkpoint. Planned-gap artifacts, skipped/unrun device gates, and
host-only verifier passes that only preserve a gap are evidence of residual
risk, not stable checkpoint credit.

- [doing] Image pull UI must not hard-code `ubuntu:22.04`. It now needs a
  searchable selection dialog that combines local image refs, Compose
  `image:` refs, Dockerfile `FROM` refs, common defaults, and Docker Hub public
  search results. The selected architecture/platform must come from pdockerd
  host environment (`/system/host` / `PDOCKER_PLATFORM`) with ABI fallback only
  when the daemon is unreachable. Acceptance: static UI wiring check, APK
  build, and device smoke that pulls a user-selected ref without opening a
  Docker shell.
- [doing] Image pull crash safety. `pull_image` and layer extraction must stage
  into temporary directories and atomically publish completed layers/tags so an
  app or daemon kill cannot leave a partial layer/image that is later treated
  as valid. Static verifier added: `python3 scripts/verify-image-pull-crash-safety.py`
  checks `.pull-*`, `.old-*`, layer `.tmp-*`, diff-id verification, atomic
  publish ordering, and startup recovery. Device runner upgraded:
  `python3 scripts/verify/runner/image_pull_crash_safety_device.py --execute-device`
  now performs a safe scenario-owned residue kill/restart recovery lane with
  `.pull-*`, `.old-*`, `.tmp-*`, malformed partial-layer, partial-image
  inspect/create rejection, Engine inspect, scoped cleanup evidence, and
  host-side post-restart survivor scanning that fails on scenario-owned
  partial/corrupt image/cache residue. Host unit coverage also verifies startup
  cleanup of invalid/stale/unreadable build cache entries plus partial
  blob/load/save tmp files. The daemon now rejects incomplete local image
  directories before inspect/list/run and treats layers as cache only when
  `tree/` and matching `meta.json` are present. Remaining acceptance gap: add a
  timed live registry pull interruption lane that cannot overwrite user images.
  The live lane must stay `planned-gap`/non-promoting unless it is invoked with
  `--execute-live-pull-interruption`, a scenario-owned or isolated fixture
  `--live-image`, and `--live-fixture-owned`.
- [doing] Compose/build log progress rendering. The readonly log pane must
  preserve terminal carriage-return progress updates instead of deleting or
  fragmenting text-mode progress bars. Acceptance: xterm readonly log path keeps
  CR progress in one live line, summary timer remains live, no soft keyboard,
  and VS Code workspace build progress is visually checked on device. Static
  checks pass; keep open until a device visual pass confirms the progress line
  does not fragment during a real VS Code build.
- [done] Builder compatibility regression gate. Recent VS Code workspace
  failure was caused by pdockerd builder logic not expanding a valid Dockerfile
  `COPY scripts/pdocker-*` source pattern. This is a backend compatibility
  gap, not a Dockerfile/template change. Closed with unittest coverage for
  Dockerfile COPY wildcard expansion, context-escape rejection, and the bundled
  default workspace's real COPY pattern; red/green evidence for the old failure
  is recorded in `docs/test/COPY_WILDCARD_REGRESSION.md`; the default VS Code
  build log on device showed each expanded `scripts/pdocker-*` source copied
  without `COPY failed`.
- [doing] Default workspace compose-up truth after successful build. Latest
  device evidence shows the default workspace build completed and
  `pdocker-dev` has a persisted running Engine state with the current container
  ID/PID, but project cards, `docker ps`, and service health have regressed in
  this area before. Acceptance: UI card, `/containers/json?all=1`, persisted
  `state.json`, process table, listener probe for `18080`, and job log all
  agree on the same Engine container ID after compose up. The default
  workspace health gate also requires code-server reachability on `18080`,
  extension evidence for the bundled IDE stack, and a UI card whose rendered
  container ID source is Engine API current state rather than stale persisted
  metadata. The executable VS Code health gate is now
  `bash scripts/android-dev-workspace-compose-smoke.sh`; its contract is
  documented in `docs/test/DEV_WORKSPACE_HEALTH_GATE.md` and its required
  artifact is `docs/test/dev-workspace-compose-latest.json`. Host-side artifact
  promotion is guarded by `python3 scripts/verify-dev-workspace-compose-artifact.py`;
  static acceptance-plan guard remains `python3 scripts/verify-service-truth-plan.py`;
  future same-ID service-truth evidence also writes
  `docs/test/service-truth-latest.json`.
- [next] RUN changed-path/snapshot performance. `RUN chmod +x
  /usr/local/bin/pdocker-*` is functionally correct but still triggers an
  expensive broad snapshot in the default workspace build. Acceptance: profile
  the changed-path detection, add regression coverage for wildcard RUN paths,
  and reduce the final no-op-style metadata snapshot without changing Dockerfile
  semantics.
- [done] Image reference graph app-code/static slice. Task G reconnaissance on
  2026-05-16 found actual app-code evidence rather than a planned-only gap:
  `MainActivity.kt` now wires `renderImages()` into
  `renderImageCacheHealth()`/`imageCacheHealth()` and
  `renderImageReferenceTree()`/`imageParentMap()`; `ImageGraphLayout.dispatchDraw`
  draws continuous connector lines; `imageDetail()` and
  `imageReferenceInfos()` add version plus view/unique/shared storage detail;
  and `appendImageReferenceGraphRows()` attaches Files/Delete/Clean
  `ImageGraphAction`s to image nodes while surfacing shared-cache, compose, and
  container references. Host/static coverage is `python3
  scripts/verify-ui-actions.py` plus the `image.layer.maintenance-ui.contract`
  scenario. This is no longer a planned-only source gap, but it remains
  non-promoting release evidence until a connected-device screenshot/manual
  visual pass proves connector rendering and tap actions.
- [next] Storage graph/layer maintenance device evidence: capture
  connected-device screenshot/manual artifact proving connector rendering,
  cache-vs-image references, unique/shared/stale sizes, Files/Delete/Clean-cache
  tap actions, and stale build-cache or unreferenced-layer cleanup after build,
  prune, rebuild, image delete, and stale-cache cleanup.
- [next] Media bridge capture/playback gate: Phase-1 descriptor/socket/env
  negotiation must stay `Ready=false` until Camera2, AudioRecord, and
  AudioTrack executor IPC exist, runtime permissions are requested, and a
  device artifact proves capture/playback commands without raw `/dev`
  passthrough.
- [next] Build context tar compatibility: Kotlin `DockerEngineClient.createTar`
  must preserve Docker build-context semantics for regular files, directories,
  symlinks, executable mode bits, long paths/PAX behavior, mtimes, and
  `.dockerignore` parity before external Dockerfile context parity is claimed.
- [next] Pull/update operation semantics. "Pull image" is an Engine API
  operation, not "open docker pull shell"; if the ref already exists, treat it
  as update/re-pull with old tag preserved until success. Acceptance: wording,
  logs, and crash-safety behavior are consistent.
- [next] Device verification after rebuild. Install the next compat APK and run:
  image pull dialog, Docker Hub search fallback, selected platform display,
  image browse/back behavior, image tree actions, VS Code compose up build log
  progress, and post-kill image-store consistency.

### Current Open Risk Ledger 2026-05-05

Keep these risks visible across context compaction until each has an
implementation change plus a focused verification artifact.

- [next] SAF/Documents boundary: a user-selected Android Documents directory is
  an explicit `/documents` exchange mount only. Hot runtime paths, model files,
  caches, layer data, and project internals stay app-private unless the Compose
  file declares a volume or bind that intentionally exposes them. Phase 1 now
  creates the SAF mediator contract, app-private mirror/sidecar roots, explicit
  UI sync actions, and `/system/documents/status|sync-to-tree|sync-from-tree`
  daemon metadata routes. Sync reports must expose `Success`, `SourceFiles`,
  `SourceBytes`, `Files`, `Directories`, `Bytes`, and `Errors` consistently in
  both directions. Phase 2 is still not implemented: delete propagation, rename
  detection, conflict detection/quarantine/rescan UX, and full Unix metadata
  emulation must remain explicit design work rather than implied behavior.
- [next] SD-card/FAT32/exFAT exchange metadata: external Documents storage may
  hold raw payload bytes, but Unix metadata must be app-private sidecar state
  with rebuild/check evidence, conflict handling, and clear emulation limits for
  symlinks, uid/gid, modes, xattrs, hardlinks, special files, and executable
  semantics. SAF `DocumentProvider` mediation is required unless direct POSIX
  writability is proven for the selected path.
- [next] SAF UnixFS layering: implement `saf-unixfs` as a lower filesystem
  backend with `FilesystemBackend` and `UnixMetadataBackend` contracts.
  Overlay, archive, runtime, and UI code must consume those abstract contracts
  and capability flags instead of branching on SAF, SD-card, FAT32, exFAT,
  `DocumentProvider`, tree URIs, or sidecar internals. Direct-output host
  evidence is now guarded by `python3 scripts/verify-saf-direct-output-artifact.py`,
  which rejects mirror-only/fallback fake success, fallback without explicit
  reason/provider error, missing sidecar Unix metadata, missing direct-write
  evidence, missing layer boundary evidence, and unsafe path fallback.
- [next] Storage metrics accounting: the shared layer pool is counted once for
  total storage. Image apparent sizes, container apparent rootfs sizes, and
  merged views intentionally overlap lower-layer bytes, so UI summaries and
  tests must not add those apparent values together as unique usage.
- [doing] Service health truth source: tracked by active issue #6 above and
  `docs/test/SERVICE_TRUTH_DEVICE_GATE.md`. Health is not accepted until UI
  card, `docker ps`/Engine API, state, process table, listener owner, and logs
  share one current 64-hex Engine container ID; Compose metadata, requested
  ports, old names, and completed jobs are hints only.
- [next] Interactive terminal regression: do not paper over symptoms in the UI
  or scripts. Split terminal surface, session transport, Docker Engine exec API,
  local diagnostic PTY, and log panes per
  `docs/design/TERMINAL_STREAM_ARCHITECTURE.md`, then gate fixes with real
  device Engine exec evidence plus generic terminal input tests. The new
  terminal artifact verifier is a host-side guard only; it becomes promoting
  evidence only with raw device Engine exec JSONL for a real container and
  `--require-container`. Split the work into executable slices:
  1. session-type plumbing that routes container terminals only through Engine
     exec/attach and keeps diagnostic PTY/log panes separate;
  2. byte-level capture for Enter, ETX/Ctrl-C, cursor keys, resize, and `top`
     refresh into `engine-exec-input-latest.jsonl`;
  3. UI self-test artifact capture for JP/EN IME paths and shell usability after
     `q`;
  4. host verifier replay with `--require-container`; and
  5. only then device promotion. Slices 1-4 are regression evidence but remain
     non-promoting without the paired fresh device artifacts.
- [done] Runtime freeze risk: stop/kill/rm now treats teardown as a
  no-orphan operation instead of trusting HTTP 204/API acknowledgement. The
  daemon scans known PIDs, descendants, launcher PIDs, and
  container-path-referencing runtime processes; it records
  `PdockerTeardown.NoOrphanProcesses`, refuses to mark stop/kill complete while
  survivors remain, clears stale active PID fields only after no survivors, and
  keeps the device evidence contract in
  `docs/test/runtime-teardown-latest.json`.
- [next] llama GPU layer evidence: the old `--gpu-layers 1` /
  `--n-gpu-layers 1` probe offloaded only the output layer. The llama template
  and compare script now default to at least `--n-gpu-layers 2`; next device
  evidence must show `offloading N repeating layers` before treating the result
  as meaningful transformer-layer acceleration. Break the device run into:
  readiness/headroom check, env propagation diff, NGL=1 Q6_K oracle,
  NGL>=2 repeating-layer proof, artifact verifier classification, and finally
  benchmark reporting. All readiness-blocked or oracle-mismatch artifacts stay
  non-promoting.

### Runtime / Compose-Up

- [done] Remove upstream Docker CLI/Compose from APK payload. Product UI must
  use Engine API/native orchestration; upstream Docker CLI/Compose are allowed
  only as test-staged compatibility tools.
- [done] Document Docker compatibility scope, non-goals, and discussion points
  for BuildKit, network, volume, cgroup, overlayfs, signals, TTY, and archive
  API in `docs/design/DOCKER_COMPAT_SCOPE.md`.
- [done] Add the host archive API compatibility gate:
  `python3 scripts/verify-archive-api-compat.py` compiles the daemon mirror and
  runs the Docker archive/copy compatibility unit corpus, including the static
  Docker CLI `docker cp` end-to-end plan. This is regression evidence only;
  device-gated COW/archive mutation safety and `docker cp` same-container-ID
  proof remain planned-gap/non-promoting until their device artifacts promote.
  Current host coverage proves pdockerd can observe and serialize already-
  materialized COW hardlinks, including merged lower/upper hardlink peers and
  whiteouted hardlink-source paths, but this is not evidence that the direct
  runtime can create true `linkat` hardlinks.
- [done] Tiny SDK28 compat smoke: `docker build`, `docker compose up`, logs,
  exit code, and `compose down` pass with the scratch direct executor.
- [done] Ubuntu `apt-get update` signature verification works under direct
  runtime path mediation.
- [done] Re-run default dev workspace `compose up --detach --build` on SOG15:
  Ubuntu `apt-get`, NodeSource Node.js, code-server, Codex, Continue/YAML/
  Docker extensions, image tagging, container create, and service start passed.
- [done] Verified default workspace with `docker ps -a`, `docker compose ps -a`,
  `docker logs --tail=120 pdocker-dev`, and `curl -I http://127.0.0.1:18080/`.
  The real code-server endpoint returns HTTP 302 to `./?folder=/workspace`.
- [done] Ensure the compat backend startup path force-enables direct process
  execution when `PDOCKER_RUNTIME_BACKEND=direct`, so the helper probe
  advertises `process-exec=1` and `RUN/docker run/compose up` no longer fail
  at the backend gate.
- [done] Harden llama template env substitution (`LLAMA_MODEL_URL`) to `:-` form
  and add project-library verification coverage so parser incompatibility regressions
  are caught by `scripts/verify-project-library.py`.
- [done] Start llama.cpp GPU workspace on SOG15 through UI/Engine-compatible
  compose path after rebuilding the APK. The 8B Qwen3 GGUF model is present,
  `pdocker-llama-cpp` reaches `Up`, `GET /` returns HTTP 200 after model load,
  `/v1/models` reports the loaded 8.19B GGUF, and `docker logs` streams the
  real llama-server output.
- [done] Record a repeatable llama.cpp CPU fallback baseline with
  `scripts/android-llama-bench.sh`. Latest SOG15 8B Qwen3 Q4_K_M HTTP result:
  3 repetitions of 8 generated tokens averaged about 0.260 tokens/s, with
  the full JSON stored in `docs/test/llama-bench-cpu-repeat3.json` and copied
  to `files/pdocker/bench/llama-bench-cpu-repeat3.json` on device.
- [done] Add and run `scripts/android-llama-tool-bench.sh` for the official
  llama.cpp `llama-bench` binary. Latest CPU fallback tool result with
  `-p 16 -n 8 -r 3 -ngl 0 -t 8`: prompt processing about 2.40 tokens/s,
  generation about 0.228 tokens/s, backend `BLAS`/OpenBLAS CPU.
- [done] Add llama healthcheck support end-to-end. The template now declares a
  Dockerfile `HEALTHCHECK`, pdockerd carries image healthchecks into container
  state, runs a lightweight monitor, and `docker ps` reports `Up (healthy)`.
- [done] Test Vulkan-requested llama mode. `gpus: all` now reaches
  `DeviceRequests`, `PdockerGpu.Modes`, GPU env, and the profile selects
  Vulkan with `-ngl 999`, but measured HTTP generation is slower than CPU
  baseline and `vulkaninfo` fails to load Android's Bionic-dependent
  `libvulkan.so` from the Ubuntu/glibc container. Recorded in
  `docs/test/LLAMA_BENCHMARKS.md`.
- [done] Probe OpenCL after Vulkan. pdocker now records `opencl` mode, injects
  OpenCL ICD metadata, and binds the host OpenCL library when present. The
  library is visible but fails to load because Android/Bionic dependencies
  such as `liblog.so` are not available to the glibc container.
- [next] Add or finish the UI/device health card for the active #6 gate:
  check the real listener for the service port, verify the owning Engine
  container ID and health state, and link to container logs rather than relying
  on placeholder/job state.
- [next] Prevent duplicate container truth after interrupted compose attempts.
  The current device had an old exited `pdocker-llama-cpp` plus the new running
  one, which makes name-based `docker logs` and `docker ps` display ambiguous.
  Compose reconciliation must prefer Engine container IDs plus pdocker
  project/service labels and reserve names for display or legacy fallback.
- [next] If default workspace regresses, capture the first failing syscall or
  package-manager operation and add a focused direct-runtime smoke before
  retrying the full template. Latest focused blocker: npm self-update
  (`npm install -g npm@latest`) retires its own tree then loses
  `promise-retry` during Arborist rebuild. `@openai/codex` install with the
  NodeSource-bundled npm 10.9.7 works, so the template temporarily avoids the
  self-update while the runtime rename/reify parity bug remains open.
- [next] Add `docker run --rm ubuntu:22.04 echo hi` as an Android smoke gate.

### Performance

- [done] Add repeatable Android runtime benchmark split:
  `scripts/android-runtime-bench.sh`, optional `--apt-update`, optional
  existing `--proot-cmd`.
- [done] Establish baseline: all-syscall ptrace `apt-cache policy nodejs`
  took about 22.5s / 15,839 stops.
- [done] Add scratch seccomp-BPF selective tracing and switch default direct
  trace mode to `seccomp`.
- [done] Establish improved baseline: selective tracing `apt-cache policy
  nodejs` took about 4.3s / 1,783 stops.
- [done] Keep default workspace build on `apt-get`; performance fixes belong
  in syscall mediation, not Dockerfile shortcuts.
- [done] Add benchmark output capture to a stable artifact file under
  `files/pdocker/bench` so device runs can be compared over time.
- [done] Add a regression threshold for stop count and wall-clock deltas in the
  lightweight bench, with generous device variance.
- [done] Make the benchmark fail when no traced rootfs/stats are available;
  after build-prune removed transient roots, the old script could report a
  false PASS on `rootfs dynamic loader not found`.
- [done] Add Docker-compatible build/system prune paths for interrupted build
  cleanup. Test-staged upstream Docker CLI can still exercise these paths, but
  product APK UI should use Engine API/native actions.
- [done] Stop repeated Android UI rebuilds from growing the shared layer pool
  with stale copies. Successful tag replacement now prunes unreferenced layer
  store entries by default in the APK, and Dockerfile `RUN` snapshots have a
  parent-layer/build-state cache so an unchanged dev-workspace build can reuse
  the prior apt/npm layer instead of generating another multi-GB layer.
- [done] Add a whole-image rebuild cache for unchanged Dockerfile/context/tag
  pairs. On SOG15, the default VS Code workspace rebuild path measured 129s
  before this pass, then 62s after RUN cache reuse exposed a remaining rootfs
  re-merge bottleneck, and finally 0s wall-clock at shell-second resolution
  once the existing tagged image was reused directly. Simple metadata-only
  `RUN chmod ...` also uses touched-path snapshotting when the full image cache
  is invalidated.
- [done] Expose daemon-owned active operations through
  `GET /system/operations` and render them in the Overview. Builds triggered
  from ADB, tests, or the UI are now visible from the app because the state is
  recorded in pdockerd rather than only in UI job memory.
- [done] Add tracer process cleanup: `PTRACE_O_EXITKILL` where available,
  separate child process group, and SIGINT/SIGTERM/SIGHUP/SIGQUIT handling so
  aborted direct runs do not leave tracee process leftovers.
- [done] Remove `/proc/<pid>/status` ownership validation from the syscall-stop
  hot path. It is now opt-in with `PDOCKER_DIRECT_VALIDATE_TRACEES=1` for
  diagnostics, so normal seccomp/ptrace runs avoid one procfs open/read per
  trapped syscall.
- [next] Run optional PRoot/proot-like comparison only when an existing command
  is supplied; do not download or bundle external PRoot/fakechroot.
- [doing] Prototype APK-scoped memory pager. System swap/zram tuning is blocked
  on production devices (`adb root` unavailable, `swapon` not permitted), so the
  viable path is opt-in managed regions under pdocker control. The SDK28 compat
  APK now proves the ptrace fallback PoC: reserved `PROT_NONE` page, SIGSEGV
  stop, generic aarch64 `svc; brk` syscall injection for tracee `mprotect`,
  page write, register restore, and original-instruction resume. Compose memory
  keys (`mem_limit`, `memswap_limit`, `deploy.resources.limits.memory`) now feed
  Engine metadata, while `PDOCKER_MEMORY_PAGER=managed` or
  `io.pdocker.memory-pager=managed` remains the explicit pager opt-in. The
  direct executor now writes bounded app-private memory telemetry artifacts
  (`memory-ring.jsonl` and `memory-summary.json`) with operation/container IDs,
  atomic rename publication, and partial-record rejection; the transparent APK
  PoC script captures those artifacts and the canonical test driver has a
  dedicated `android-memory-pager` lane. Canonical status: this lane is
  device-evidence-only but non-promoting for stable/release credit until managed
  pager probes and controlled OOM/LMK replay both pass on a connected device.
  Remaining app-level virtual memory/low-memory audit items stay TODO/test-plan
  only until promoted by device evidence:
  - Large allocation guard: define the admission threshold from requested bytes,
    Android low-memory headroom, container/operation identity, and explicit
    large-workload or pager opt-in; unsafe allocations must return an
    `ENOMEM`-style error with `allocation_denied_enomem` diagnostics rather than
    starting work that Android LMK is likely to kill. Docker/Compose memory keys
    remain budgets only and must not imply host swap or pager success.
  - OOM/LMK diagnostics: every suspected backend death must retain the last
    RSS/PSS/swap/headroom sample, last progress marker, pid/exit status or
    signal, classifier reason (`lmk_suspected`, `not_lmk_suspected`, `unknown`),
    and bounded ring/summary paths; UI/API state must fail closed instead of
    showing stale `Up` or successful completion.
  - UI memory visualization evidence: memory cards must display artifact
    source, creation age, status, and whether data came from a past self-test vs
    live `/proc`; promotion needs a replayable artifact/screenshot pair proving
    stale, missing, or planned-gap evidence is not rendered as live success.
  - Future mmap/userfault pager gate: keep `mmap`/`mprotect`/`sigaction`/`munmap`
    interception and any `userfaultfd` backend behind explicit capability checks
    plus opt-in labels/env. Unsupported mappings, executable/shared/stack/GPU
    exclusions, unresolved faults, or missing kernel support must pass through
    unmanaged when safe or fail closed with non-promoting diagnostics.
  - Task H virtual memory feasibility gate: app-level virtual memory and pager
    claims remain a planned-gap/non-promoting result unless a host/static
    verifier plus future connected-device artifact proves every required
    syscall capability: mmap fixed mapping or equivalent same-address replay,
    mprotect on exact managed pages, SIGSEGV handler or userfaultfd fault-event
    availability, file-backed spill/writeback/restore, and safe fallback on unsupported Android kernels.
    `scripts/verify-memory-pager-contract.py`
    rejects promotion artifacts missing any of those proofs; dry-run,
    planned-gap, or host-only artifacts must set `success=false` and
    `stable_checkpoint_eligible=false`. No native pager code is promoted by this gate.
  Next slice: fill the runtime counters from the managed-region table, add
  thread/signal guardrails, and run synthetic fault-latency/stress evidence
  before any llama/container opt-in. Smaller executable proof units:
  1. managed-pager PoC artifact proving explicit opt-in, managed-region table
     counters, page materialization, dirty/writeback accounting, and fallback
     denial fields;
  2. transparent-pager PoC artifact proving unsupported mappings are excluded or
     passed through safely instead of being claimed as managed;
  3. OOM/LMK replay artifact proving allocation denial, backend-death
     classifier, ring/summary retention, and operation/container identity;
  4. UI evidence artifact/screenshot proving stale, missing, blocked-device, or
     planned-gap memory data is not rendered as live success; and
  5. future mmap/userfault capability artifact. Units 1-5 remain
     non-promoting for stable/release credit until the connected-device pager
     plus controlled OOM/LMK replay promotion condition passes.
- [next] Revisit Dockerfile build memory pressure without changing upstream
  Dockerfiles. The managed-region pager remains explicit and opt-in; ordinary
  toolchain heap allocations such as `cc1plus` are not yet under pdocker memory
  ownership.
- [next] [#13](https://github.com/ryo100794/pdocker-android/issues/13)
  Implement Runtime OOM Survival and Large Workload Mode. The default
  path should keep using large-allocation guardrails to return `ENOMEM` before
  Android LMK kills the app, while the opt-in large-workload path should make
  oversized jobs run by combining file-backed mmap/streaming, managed anonymous
  regions, and GPU bridge guarded memory. Required pieces are pdockerd-owned
  memory telemetry rings, structured OOM/LMK evidence, UI/debug-pane memory
  status, synthetic guard-denial tests, controlled restart classification, and
  explicit labels/env such as `io.pdocker.large-workload=enabled` without
  changing ordinary Dockerfile/Compose semantics. The first bounded telemetry
  ring/summary writer is in place; remaining work is device LMK replay,
  stale-running UI rejection, and large-workload opt-in execution proof. Do not
  promote host/static artifacts until a connected-device replay proves: unsafe
  large allocations fail with diagnostics instead of fake success, backend death
  is classified without losing operation identity, and UI memory visualization
  visibly marks stale or planned-gap evidence as non-live.
- [doing] Profile remaining hot trapped syscalls after `newfstatat/openat` and
  decide which can be safely handled with fewer ptrace stops. Current tuning
  adds seccomp errno returns for probe syscalls and uses a blocking
  `waitpid(__WALL)` path with the old 1ms polling wait removed. On SOG15, the
  filesystem-heavy `npm install -g @openai/codex --dry-run` profile improved
  from about 19.4s with the old polling wait loop to about 1.8-2.4s with the
  blocking wait loop at roughly the same 9.9k traced stops.
  A clean lightweight run after pruning uses
  `docker.io/library/ubuntu:22.04` and reports about 0.210s / 1,069 stops,
  with `newfstatat` and `openat` still the top trapped syscalls. After removing
  default per-stop `/proc/<pid>/status` validation, the same lightweight run on
  2026-05-03 reported about 0.141s / 1,069 stops.
- [doing] Decouple daemon lifetime from UI crashes and background ANR paths.
  `PdockerdService` runs in the dedicated `:pdockerd` process, and the boot/debug
  receivers now run there too so daemon-only test starts do not wake the UI
  process. Heavy service startup work (runtime prepare, executor launch, Python
  init) is off the lifecycle main thread. SOG15 debug install on 2026-05-05
  verified receiver-only daemon start, socket ping, no pdocker ANR/FATAL during
  the observation window, and no default UI process after receiver startup.
  Next check: container process reconciliation across UI process death.
- [done] Optimize Python layer diff/snapshot by comparing against a compact
  prior-layer path index and re-hardlinking committed snapshot files. Tiny
  Android `RUN` layer snapshots dropped from about 3.0s to about 1.5-1.9s.
- [done] Tune `libcow` copy-up hot paths. Read-only fd tracking is now opt-in
  with `PDOCKER_COW_TRACK_READONLY_FDS=1`, xattr copy-up is opt-in with
  `PDOCKER_COW_COPY_XATTRS=1`, `O_CREAT|O_EXCL` skips pre-open copy checks,
  `O_TRUNC`/`creat` copy up metadata without copying discarded file content,
  and copy-up uses `copy_file_range` when available. Reusable microbench:
  `docker-proot-setup/src/overlay/bench_cow.sh`.
- [done] Enable direct `cow_bind` container create/start in packaged APKs and
  sync the backend asset during Gradle builds so stale pdockerd code cannot be
  shipped accidentally. On SOG15, dev-workspace container create dropped from
  about 77.35s with full rootfs materialization to about 1.10s with lower/upper
  sharing; a fresh `pdocker-dev` create/start measured about 0.382s/0.389s.
- [done] Make container start idempotent when a live runtime PID already exists.
  This prevents a fast repeated start from launching a second process, having
  the second process fail on an already-bound port, and overwriting state as
  `Exited` while the first service is still serving.
- [done] Keep release builds from exposing debug-only daemon entry points.
  The product still starts the internal pdockerd Engine API for UI-driven
  compose/build/container management, but release APKs do not export the smoke
  broadcast receiver and the normal UI hides host shell/manual daemon/debug
  benchmark actions.
- [done] Keep COW terminology independent from PRoot. `libcow` is an
  LD_PRELOAD libc hook shim; it does not use ptrace or waitpid. PRoot-era COW
  comments and the diagnostic `proot-cow` driver label were renamed.
- [doing] Profile large apt/npm template layers separately from ptrace. Build
  logs now include `build-profile` timings for base materialization, RUN exec,
  COPY work, and snapshot subphases (`prev-index`, `walk`, `stage`, `tar`,
  `digest`, `extract`, `relink`). COPY/ADD snapshots now use touched-path mode
  instead of scanning the whole rootfs, cutting the reusable microbench from
  about 2.1-2.3s per COPY snapshot to about 0.2s. Remaining non-cache large
  RUN layers still need a direct-runtime changed-path manifest so snapshot can
  avoid a full rootfs walk after apt/npm.
- [next] Revisit rootfs-fd path rewriting as an opt-in optimization only after
  fd lifetime handling is proven. A trial that rewrote absolute `*at` paths to
  `openat(rootfs_fd, relative)` made apt resolver cleanup hit
  `getaddrinfo (9: Bad file descriptor)`, so the optimization is currently
  gated behind `PDOCKER_DIRECT_ROOTFD_REWRITE` and off by default.
- [next] Revisit `statx -> ENOSYS` as an optional Node/npm optimization only
  after apt Acquire DNS remains stable; a trial removed `statx` stops but made
  `apt-get update` report `Could not resolve 'ports.ubuntu.com'` despite
  `getent hosts` working.
- [done] Measure whether `newfstatat/statx` can simply bypass ptrace. It cannot:
  `PDOCKER_DIRECT_UNTRACED_STAT_PATHS=1` reduced an apt-cache probe to 73 stops
  but broke PATH resolution (`apt-cache: not found`, `apt-get: not found`).
  Keep it as a benchmark-only negative control, not a runtime default.

### Filesystem / Syscall Semantics

- [done] Add xattr path mediation so `ls`, `find`, and apt-key do not inspect
  Android host paths after `statx`.
- [done] Fix seccomp event emulation so user-space return values survive via a
  one-shot syscall-exit stop.
- [done] `dpkg --configure -a` focused reproduction passes after the previous
  `ca-certificates`/debconf failure; a device run completed in about 145s with
  `newfstatat` and `openat` as the dominant trapped syscalls.
- [doing] Replace permissive syscall answers with Docker/Linux-compatible errno
  behavior where package managers depend on it.
- [done] Treat Android-blocked NUMA policy syscalls (`mbind`,
  `get_mempolicy`, `set_mempolicy`, `migrate_pages`, `move_pages`, and
  `set_mempolicy_home_node`) as unavailable with `ENOSYS`. This unblocks
  llama.cpp/OpenBLAS-style startup on Android app seccomp without granting fake
  NUMA behavior.
- [next] Fix npm self-update rename/reify compatibility so
  `npm install -g npm@latest` works without temporarily relying on the
  NodeSource-bundled npm.
- [next] Replace `linkat` copy fallback with an inode/hardlink/CoW storage
  model. The current compatibility copy remains a fail-closed, non-promoting
  Phase 2 gap: closure requires Android device evidence that linked paths share
  `st_dev/st_ino`, `st_nlink` increases after `linkat` and decrements after
  `unlink`, writes through either name are visible through the other, invalid
  flags/mediated escapes return Linux-compatible errno, and restart recovery
  does not promote partial hardlink/CoW metadata or divergent copy-fallback
  artifacts. The May 16 host slice found reusable pdockerd/archive inode
  evidence for existing hardlinks, but no daemon-side sidecar/index is promoted
  as a source of truth; true creation, mediation, and recovery still require
  direct-runtime C work and device evidence. Execute as separate units:
  1. C runtime syscall contract and errno matrix for `linkat`, `linkat(AT_FDCWD,
     ...)`, invalid flags, missing parents, cross-root escapes, and
     hardlink-to-directory denials;
  2. inode/index or CoW metadata design that records link peers without
     treating copy-fallback bytes as success;
  3. C implementation for creation, unlink decrement, rename/replace over
     hardlinked destinations, copy-up interactions, and write-through from
     either name;
  4. interrupted metadata-update recovery that fails closed after daemon/helper
     kill; and
  5. Android artifact proof for inode identity, link-count transitions,
     write-through, errno parity, and restart recovery. Host/static units are
     non-promoting until unit 5 passes.
- [done] Replace `/proc/self/exe` rootfs temporary symlink mediation with direct
  readlink emulation that does not mutate image state; remaining work is
  Android device evidence for `/proc/self/exe`, `/proc/thread-self/exe`, and
  `/proc/<pid>/exe`.
  This is still tracked as device evidence rather than host-only closure.
- [next] Direct syscall Phase 2 contract work:
  - finish attach/PTY/signals semantics beyond the current tiny start/logs and
    raw signaled-root status paths;
  - replace remaining permissive compatibility answers with syscall-specific
    Linux errno parity tests;
  - add a RUN changed-path manifest from traced filesystem mutations so layer
    snapshots do not require a full post-RUN rootfs walk;
  - harden bind, project volume, and named volume path rewrite as one contract
    across filesystem syscalls and AF_UNIX socket paths;
  - keep `linkat` hardlink semantics fail-closed until the non-promoting
    Android artifact proves inode identity, link-count preservation, write-through
    behavior, errno parity, and recovery from interrupted hardlink/CoW metadata
    updates; split this into C errno-matrix tests, C runtime metadata/index
    implementation, write-through/unlink/rename cases, kill/restart recovery,
    and Android artifact verification so a host copy-fallback check cannot
    promote the gate;
  - collect Android device evidence that `/proc/self/exe`,
    `/proc/thread-self/exe`, and `/proc/<pid>/exe` readlink emulation reports
    the guest executable without mutating image state.
- [next] Remove normal stderr diagnostics from direct runtime logs once default
  workspace start is stable.

### UI / Workflow

- [done] Keep host shell diagnostic-only and keep normal Compose/Dockerfile
  flows on widgets or Engine actions.
- [doing] Keep job/task cards useful for build/compose failures, retries, and
  logs.
- [next] Surface default workspace service health only from the real container
  listener, never from a placeholder process.
- [next] Validate terminal text selection and copy on-device after the runtime
  smoke is stable.

### GPU / Models

- [done] Keep llama.cpp and dev workspace templates standard Dockerfile/
  Compose definitions.
- [done] Add first-pass CPU/GLES GPU benchmark artifacts.
- [next] After the Vulkan clamp, run the llama GPU performance workflow through
  `scripts/android-llama-gpu-compare.sh`: keep CPU fallback hiding Vulkan
  devices, force Vulkan only for measured attempts, capture device/thermal
  metadata, and keep the latest artifact under `docs/test` and
  `files/pdocker/bench`.
- [next] Verify llama.cpp compose after runtime service start works, including
  model download/resume and docker logs.

### Storage / Metrics

- [next] Verify Android storage metrics on device: layer store, image-view,
  container-private, total, and free-space numbers must be nonnegative, must
  preserve the daemon's shared-layer/container-upper overlap distinction, and
  must refresh after build, prune, rebuild, and container file-edit/copy-up
  flows.

### Packaging / License

- [done] Default no-PRoot packaging path exists.
- [done] Fold `docker-proot-setup` into this repository as a normal tracked
  directory instead of a submodule.
- [done] Remove unused bundled `proot`/`proot-runtime` payloads from the
  integrated backend tree; optional proot comparison remains command-supplied
  only.
- [next] Re-run third-party notice audit after packaging changes.

## P0: Real Android Container Execution

Status: **SDK28 compat smoke works for tiny build/compose through scratch
`pdocker-direct`; the default dev workspace now reaches real Ubuntu `apt-get`
execution with signature verification fixed under the selective syscall broker.
The default `compose up --build` command returned successfully on 2026-05-03
and `pdocker-dev` was verified running with real code-server logs and
HTTP 302 from `127.0.0.1:18080`**. This closes the first VS Code server/Codex/
Continue usability gate; llama-server,
PTY attach, port publishing, and broader Docker compatibility.

Temporary behavior:

- `PDOCKER_RUNTIME_BACKEND=no-proot` is metadata/edit/browse mode only.
- The APK stages a native `pdocker-direct` helper and sets
  `PDOCKER_DIRECT_EXECUTOR`; runtime-backed build/run/compose paths must keep
  advertising `process-exec=1` before pdockerd routes process execution to the
  helper.
- Experimental process execution probes must stay gated. The 2026-05-02
  `scripts/android-api29-direct-feasibility.sh --no-install` run on SOG15
  (Android 16 / SDK 36, app targetSdk 34, `untrusted_app`) still failed the real
  app-domain Dockerfile `RUN` path with `exit code -31` even though `run-as`
  controls could execute the helper and rootfs shell.
- The SDK28 compat flavor is now a separate runtime switch point and does not
  include PRoot/talloc/proot-loader. On SOG15 it can run the tiny
  `ubuntu:22.04` build/compose smoke through scratch `pdocker-direct`.
- The syscall-fetch foundation is now proven in scratch `pdocker-direct`:
  ptrace can fetch syscall registers in the app domain, trace fork/vfork/clone
  children, route child `execve()` through the rootfs loader, rewrite common
  absolute path syscalls into the image rootfs, and emulate/suppress known
  Android-blocked startup syscalls.
- `PDOCKER_DIRECT_TRACE_SYSCALLS=0` now disables verbose syscall logging only;
  the syscall broker remains enabled by default. This is required so UI builds
  are not drowned in trace logs while path mediation still happens.
- `io_uring_setup`/`io_uring_enter`/`io_uring_register` currently return
  `ENOSYS` in the direct runtime so Node/libuv falls back to portable polling.
  Replace this with an explicit compatibility policy and tests for runtimes
  that probe io_uring.
- `/proc/self/exe` readlink now uses userland `readlinkat` emulation so Node
  sees `/usr/bin/node` instead of the Android host loader path without creating
  helper symlinks in rootfs state. Android device evidence for self,
  thread-self, and pid-specific proc exe paths remains required before closing
  the Direct syscall Phase 2 device lane.
- `faccessat2` is now handled in user-space mediation for apt-key. Replace the
  current minimal path probing with full flags/errno parity.
- `linkat` currently uses a file-copy fallback, including a dpkg
  `/var/lib/dpkg/status-old` replace case, because Android app data rejects the
  hardlink behavior dpkg expects. This is content-only compatibility, not a
  closable hardlink implementation: the planned Android device gate is
  non-promoting and must fail closed until an artifact proves shared
  `st_dev/st_ino`, `st_nlink` growth/decrement, write-through behavior, Linux
  errno parity for invalid/escaped operations, and restart recovery that leaves
  no partial hardlink index or divergent copy promoted. Replace this with a real
  inode/hardlink/CoW storage model.
- apt archive staging currently has a narrow `/var/cache/apt/archives/*.deb` to
  `/tmp/apt-dpkg-install-*` symlink mediation path. The earlier
  `DPkg::Go (14: Bad address)` blocker is past the current test point, but the
  special case still needs to be replaced with general rootfs symlink handling.
- Absolute symlink normalization inside rootfs is a temporary compatibility
  measure for direct execution without chroot. Replace it with runtime path
  mediation that does not mutate image data.
- `ldconfig`/`ldconfig.real` can be skipped in direct mode to avoid blocking
  Android ptrace execution. Replace this with deterministic ld cache handling
  or a correct executable path.
- Direct tracee cleanup now prunes vanished or detached tracees and includes
  temporary idle diagnostics. Remove normal stderr diagnostics once the default
  dev workspace compose build/start is stable.
- The direct runtime now defaults to a scratch seccomp-BPF selective trace mode
  instead of stopping every syscall. A focused device bench on 2026-05-03
  improved `apt-cache policy nodejs` from about 22.5s / 15,839 stops to about
  4.3s / 1,783 stops. Keep `PDOCKER_DIRECT_TRACE_MODE=syscall` available as a
  diagnostic fallback and continue tuning the selective syscall set.
- Seccomp event emulation must take a one-shot syscall-exit stop before writing
  the final return value. Returning from the seccomp event directly caused
  emulated `faccessat2` to appear as `ENOSYS`, which made apt-key treat Ubuntu
  keyrings as unreadable.
- `setxattr`/`getxattr`/`listxattr` path syscalls are now included in rootfs
  path mediation. Missing xattr path rewrite caused tools like `ls`, `find`,
  and apt-key to partially evaluate `/etc/...` against the Android host
  filesystem.
- Temporary `/bin` -> `/usr/bin` and related `/sbin`/`/lib` path fallback was
  added in `pdocker-direct` only to prove the syscall broker path, then removed.
  Correct behavior is to preserve Docker/OCI rootfs symlinks during layer
  materialization. Any future path fallback of this kind is forbidden unless it
  is explicitly modeled as real symlink resolution from the image rootfs.
- Direct backend start/exec fails with an explicit error instead of starting a
  fake listener.
- Dockerfile `RUN` fails in direct mode instead of recording a fake layer.
- UI `compose up` may create inspection metadata or show runtime-blocked state,
  but must not report a service as running unless a real process is running.

Real implementation needed:

1. Add a direct executor boundary for `start`, `exec`, `wait`, `stop`, `logs`,
   attach, PTY, environment, workdir, and signal handling: **tiny start/logs
   smoke works; attach/PTY/signals still incomplete**.
   - `PDOCKER_DIRECT_EXECUTOR` is now the explicit helper entry point.
   - The helper must pass `--pdocker-direct-probe` by printing
     `pdocker-direct-executor:1`.
   - The helper must also print `process-exec=1` before pdockerd will route
     `RUN`, `docker run`, `docker exec`, or Compose services to it.
   - Without a passing helper and capability, pdockerd refuses process
     execution instead of falling back to `/system/bin/sh`.
2. Harden APK-owned native `fork/exec` helper stdout/stderr capture and remove
   remaining noisy diagnostics from normal container logs.
3. Extend syscall coverage beyond the tiny smoke and replace permissive
   compatibility answers with accurate return values.
4. Complete rootfs path mediation so process paths resolve inside the image
   rootfs, not the Android host filesystem, including symlink and errno
   behavior.
5. Keep merged-usr symlinks (`/bin`, `/sbin`, `/lib`, `/lib64`) as image data.
   Do not flatten them into directories and do not paper over a broken rootfs
   by redirecting hard-coded paths to `/usr/...`.
6. Complete bind/project/named-volume path rewrite parity across filesystem
   syscalls and AF_UNIX socket paths.
7. Add Engine-level attach and TTY plumbing for `docker run -t` and
   `docker exec -it`, including resize and signal forwarding.
8. Add process supervision that survives UI navigation and reports honest exit
   codes.
9. Reduce direct-runtime overhead for apt/npm-heavy Dockerfiles. Current
   selective seccomp/ptrace mediation is correct enough for tiny compose and
   `apt-get update`, but still needs full default workspace confirmation.
   - `build-profile` log lines are the canonical build bottleneck record; keep
     them in UI logs and test artifacts when measuring default workspace
     builds.
   - COPY/ADD now use path-scoped layer snapshots. Extend the same idea to RUN
     by having `pdocker-direct` record mutated guest paths from traced
     filesystem syscalls.
   - Continue tuning the child seccomp-BPF trace filter so path/credential/
     process syscalls trap, while hot syscalls such as `read`, `write`,
     `mmap`, `mprotect`, and `brk` run without ptrace stops.
   - Do not simply untrace `newfstatat/statx`; apt and shell PATH lookup need
     those path checks mediated. Optimize the handler path instead: reduce
     register writes, cache read-only path classifications, and add a RUN
     changed-path manifest so snapshot does not need a full post-RUN scan.
   - Keep path mediation on `openat`, `newfstatat`, `statx`, `execve`,
     `readlinkat`, `linkat`, `symlinkat`, `renameat`, `unlinkat`, and related
     filesystem syscalls.
   - Keep a comparison bench path for existing proot/proot-like commands when
     the user supplies one, but do not download or bundle PRoot/fakechroot.
10. Keep the fast native verification loop documented and working:
   `scripts/build-native-termux.sh`, `adb push libpdockerdirect.so`, then
   replace `files/pdocker-runtime/docker-bin/pdocker-direct` via `run-as`.
   APK rebuilds are for final packaging checks, not every runtime iteration.

Acceptance:

- `docker run --rm ubuntu:22.04 echo hi` prints `hi`.
- `docker build` with a tiny `RUN echo ok > /marker` creates the marker in the
  image. **Passing in SDK28 compat smoke.**
- `docker compose up -d` starts a service process, `compose logs` shows its
  stdout, and `compose down` stops it. **Passing for the tiny SDK28 compat
  smoke.**
- `apt-get update` inside an Ubuntu 22.04 direct rootfs verifies Ubuntu archive
  signatures without apt-key keyring readability warnings. **Passing in the
  2026-05-03 device run after xattr mediation and seccomp return fixes.**
- Opening a container terminal runs inside the container rootfs; `ls /` lists
  container root, not Android host root.

## P0: Dockerfile Semantics Stay Upstream-Compatible

Status: **guarded by tests**.

Temporary behavior:

- Legacy builder supports only a Docker-compatible subset.
- Unsupported standard Docker/BuildKit features must fail clearly.
- pdocker-specific Dockerfile instructions are forbidden.

Real implementation needed:

1. Keep bundled Dockerfiles standard-only.
2. Do not add `PDOCKER_*` Dockerfile instructions or custom frontend syntax.
3. Expand standard Docker support in priority order:
   - multi-stage `FROM ... AS` plus `COPY --from`;
   - `COPY --chown` and `COPY --chmod` metadata;
   - `SHELL`-aware `RUN`;
   - `.dockerignore` parity;
   - BuildKit syntax only after a real BuildKit-compatible path exists.
4. Keep unsupported syntax as explicit failures, not silent skips.

Acceptance:

- `scripts/verify-dockerfile-standard.py` passes.
- Unknown Dockerfile instructions fail the build.
- direct runtime never creates fake `RUN` layers.

## P0: No Fake Service Ports

Status: **guarded by tests**.

Temporary behavior removed:

- The previous direct-runtime placeholder HTTP listener on `127.0.0.1:18080`
  was removed because it was not a container process.

Real implementation needed:

1. Service URLs should become healthy only when the container process actually
   binds the port.
2. UI health checks must distinguish:
   - configured/published port metadata;
   - listener exists but not from container;
   - real container listener.
3. Health checks must bind listener evidence back to the Engine container ID,
   health state, and logs so duplicate names or stale exited containers cannot
   satisfy the service card.
4. `docker ps` should continue to show requested port mappings as metadata, but
   UI must label them as inactive until runtime port rewrite/listen support is
   implemented.

Acceptance:

- `127.0.0.1:18080` is refused until code-server really runs.
- Service health for `18080` and `18081` points to the current running
  container ID and opens the matching logs.
- `compose up` cannot succeed by launching an out-of-container placeholder.

## P1: Port Rewrite and Networking

Status: **active-state proof; forwarding still pending**.

Temporary behavior:

- Synthetic IPs and `PdockerNetwork.PortRewrite` are recorded.
- `PdockerNetwork.PortMappingStatus` records planned, inactive, active, and
  conflict states from requested host ports, container running state, live
  container-owned listener proof, verified proxy/rewrite evidence, foreign
  listeners, and peer host-port claims. It does not mark active from
  Docker/Compose metadata alone.
- Network mode is treated as a Compose-compatible host-network stub with stable
  network IDs, endpoint IDs, service aliases, and `/networks` metadata.
- Port publishing warnings are surfaced.
- API warnings explicitly state the Android runtime has no TUN, namespace,
  bridge, iptables, or embedded DNS yet.

Real implementation needed:

1. Implement bind/connect syscall mediation or a container-aware socket proxy.
2. Support multiple containers wanting the same internal port.
3. Provide container DNS/alias resolution beyond `/etc/hosts` injection.
4. Teach running containers to refresh peer aliases after network connect and
   disconnect without requiring a restart.
5. Expand UI labels from counts to per-port troubleshooting details and
   conflict owner hints where screen space allows.
6. Implement actual forwarding/proxy or syscall rewrite for mappings whose
   host and container ports differ; current proof can only mark active when a
   container already owns the requested host listener or runtime code records
   verified rewrite/proxy evidence.

Acceptance:

- A service listening on container port 80 can be mapped to host `18080`.
- Two services can both listen on internal port 80 with different host ports.
- Container cards distinguish requested mappings from active mappings.
- Compose service names resolve consistently inside containers.

## P1: Filesystem and Overlay Semantics

Status: **partial**.

Temporary behavior:

- Image/container browsing works.
- cow_bind merged browsing is basic.
- `libcow` remains the compatibility CoW shim; PRoot payloads are no longer
  part of the default APK or integrated backend tree.

Real implementation needed:

1. Move lower/upper/whiteout semantics out of patched PRoot.
2. Implement rename, deletion, chmod/chown/xattr, hardlink, symlink, and merged
   directory semantics.
3. Make `docker cp` and UI edits share one storage contract.
4. Promote Docker CLI `docker cp` only after a non-promoting device gate proves
   the same Engine container ID, archive HEAD/GET/PUT behavior,
   `X-Docker-Container-Path-Stat`, byte/sha256 equality, hardlink and symlink
   policy, metadata, xattr, whiteout rejection, and escape-negative cases.
5. Add tests for lower read, upper write, whiteout delete, and copy-back edit.

Acceptance:

- Editing a copied-up lower file affects only that container.
- Deletes create correct whiteout behavior in image/container browse and export.
- `docker cp` preserves expected Docker archive behavior only after the
  same-container-ID device gate passes; host archive unit tests remain
  non-promoting compatibility evidence.

## P1: VS Code Server and Dev Workspace

Status: **quick-start template exists and has reached code-server on device;
promotion is now blocked on same-ID service health evidence, configured
extension proof, first-run credential handling, and the optional full workspace
template.**

Temporary behavior:

- The default Dockerfile is standard-only and intentionally trimmed to the
  first useful path: code-server, Continue, Codex, Docker/YAML editing support,
  Python 3, git, ripgrep, curl, and Vulkan library presence.
- The earlier all-tools default (`pip`, `venv`, vim/nano, Jupyter/Python/ESLint/
  Prettier/GitLens extensions, `vulkan-tools`, etc.) made first on-device
  `compose up` too slow under ptrace. Reintroduce it as a separate full dev
  workspace template or optional install layer after quick-start compose is
  stable.

Real implementation needed:

1. Use only standard Dockerfile/Compose semantics for the template.
2. Keep code-server start covered by the default workspace health artifact and
   require same-ID UI/API/listener truth before promotion.
3. Add first-run credential/password handling.
4. Add a full dev workspace template with the heavier editor extensions and
   CLI tools.
5. Add test that `docker compose up -d` makes the VS Code HTTP endpoint respond.

Acceptance:

- `docker compose logs` shows real code-server logs.
- UI service health reaches healthy from the real container listener.

## P1: llama.cpp and Model Workflow

Status: **llama.cpp server runs on Android direct runtime with CPU fallback**.

Temporary behavior:

- llama.cpp GPU template, model volume, optional model download, and logs script
  exist.
- Current SOG15 run starts the real `llama-server` and serves HTTP on
  `127.0.0.1:18081`. GPU diagnostics can now expose the pdocker Vulkan/OpenCL
  bridge path, but normal product mode must stay on CPU fallback until the
  bridge can serve tokens faster than CPU with validated llama.cpp workloads.

Real implementation needed:

1. Keep Dockerfile standard-only.
2. Add reliable model download/resume and model selection UI.
3. Validate the 8B default model path on-device with storage checks.
4. Keep `docker compose up -d` llama-server start in the Android smoke/manual
   regression loop.
5. Stream real llama logs through `docker logs`.
6. Keep Compose env defaults compatible with the strict parser in runtime-backed
   builds (`${LLAMA_MODEL_URL:-...}`), and add a project-library verify assertion
   so this regression is caught early.

Acceptance:

- A selected GGUF model appears in UI status.
- llama-server responds on the configured port from inside the container.
- GPU mode shows Vulkan/CUDA-compatible evidence before claiming acceleration;
  otherwise the UI must label the run as CPU fallback.

## P2: GPU, Vulkan, and CUDA-Compatible API

Status: **contract and benchmark first pass only**.

### llama.cpp Container GPU 10x Task List

Status: **in progress; llama.cpp source must remain unmodified**.

Goal:

- Load Qwen3 8B Q4_K_M into the llama.cpp container with GPU layers enabled
  through standard Vulkan/OpenCL loader APIs.
- Record CPU and GPU benchmarks from the same container image and model.
- Reach at least `10.0x` GPU generation throughput over the CPU baseline on
  the current Android device, without moving the llama.cpp engine to a host RPC
  process and without patching llama.cpp.

Reusable scenario:

- `scripts/android-llama-gpu-compare.sh` restarts the llama project container
  in CPU mode, records an HTTP benchmark, restarts it in forced Vulkan mode,
  records either a GPU HTTP benchmark or a structured model-load failure, writes
  `docs/test/llama-gpu-compare-latest.json`, copies it to
  `files/pdocker/bench`, and leaves the last measured mode running by default.
  CPU fallback restore is available with `--restore`; the next run always
  recreates the mode it needs before measuring.
- Direct Engine API containers created by the comparison scenario must carry
  the same pdocker project/compose labels used by UI-launched compose services,
  so `docker ps`, container cards, project cards, and service URL shortcuts all
  reconcile against the same state object.
- During tight GPU bridge tuning, pass `--gpu-only` to reuse the latest recorded
  CPU baseline, or `--cpu-tps N` to pin a known baseline. Full CPU/GPU
  comparison remains the milestone gate.
- Device run unit boundaries for issue #4:
  1. local ABI/env contract checks and `scripts/llama-gpu-env-manifest.json`
     parity;
  2. Android readiness/headroom artifact that may block without starting llama;
  3. forced NGL=1 Q6_K workgroup/writeback oracle run;
  4. NGL>=2 transformer-layer proof only after the NGL=1 blocker is classified;
  5. verifier classification of memory blockers, writeback/workgroup evidence,
     and Q6_K numeric mismatch; and
  6. benchmark/performance claim only when correctness and
     `benchmark_claim_allowed=true` pass. Units 1-5 are non-promoting blocker
     evidence when readiness is blocked or the Q6_K oracle mismatches.

Tasks:

1. **[done] CPU baseline is repeatable.**
   `scripts/android-llama-bench.sh` and `scripts/android-llama-gpu-compare.sh`
   record the current HTTP throughput for Qwen3 8B Q4_K_M.
2. **[done] Vulkan device discovery reaches llama.cpp.**
   Forced Vulkan mode now reaches `Vulkan0 (pdocker Vulkan bridge (queue))`
   instead of `ggml_vulkan: No devices found`.
3. **[done] Make the first GPU model-buffer allocation pass.**
   The forced `--n-gpu-layers 1` path now allocates the offloaded output-layer
   Vulkan model buffer through `pdocker-vulkan-icd.so`. The key fix was to
   advertise non-zero storage-buffer alignment from the ICD; llama.cpp remains
   unchanged.
4. **[done] Lower the first real llama.cpp SPIR-V dispatches.**
   `VULKAN_DISPATCH_V2` carries compute entry point and specialization
   constants across the bridge, and the forced `--n-gpu-layers 1` run now
   serves the HTTP benchmark through Android Vulkan generic SPIR-V dispatch.
5. **[done] Fix Vulkan buffer base/range accounting during scheduler warmup.**
   Transfer-only submits now complete and llama.cpp reaches context
   construction, compute-buffer allocation, warmup, and server load without the
   previous `ggml_backend_buffer_get_alloc_size` range assertion. The latest
   front blocker is now the later Android `vkQueueSubmit`
   `VK_ERROR_FEATURE_NOT_PRESENT` path during prompt processing.
6. **[active] Add persistent GPU command-ring transport.**
   Replace per-dispatch socket commands with shared ring descriptors, reusable
   buffer handles, fences, and error records under `/run/pdocker-gpu`. Latest
   8B Qwen3 Q4_K_M resident-cache probe: `served=true`, CPU 0.4153 tok/s,
   GPU 0.3668 tok/s, speedup 0.883x, `target_met=false`. The executor-side
   resident cache now retains one 510,504,960-byte generic-dispatch binding,
   but the dominant cost has moved to `vkCmdCopyBuffer` transfer-only traffic.
   Next action: record copy-buffer operations in `pdocker-vulkan-icd.so`
   command buffers, execute them during `vkQueueSubmit`, then add reusable
   bridge buffer handles for repeated large copy sources. Current slice:
   `PDOCKER_VULKAN_ALIAS_COPIES=1` is opt-in for the llama compare flow and
   lets dispatch binding 0 read from the original copied source fd/offset when
   a copy alias fully covers the descriptor range. The executor also caches
   repeated Vulkan compute pipelines by SPIR-V hash and specialization; the
   latest GPU-only probe improved to 0.939x CPU but is still transfer-bound.
   Hidden optimization slice: the APK-side executor now keeps a mutable Vulkan
   buffer cache for repeated writable fd/offset/size bindings. It still reloads
   from and writes back to the container fd for correctness, but avoids repeated
   `vkCreateBuffer`/`vkAllocateMemory`/`vkMapMemory` churn on hot activation and
   staging buffers. Next, connect the APK memory-pager PoC to GPU transport as
   a virtual buffer table: reserve large logical buffer ranges, materialize
   touched pages or spans lazily, track dirty ranges, and pin bridge-visible
   pages while a command is in flight so huge model buffers do not require
   whole-buffer copies or duplicate OOM-sized allocations.
7. **[next] Establish small-model GPU green path.**
   Use the same unmodified llama.cpp container with a small GGUF model to prove
   model load, first token, and `llama-bench -ngl 1` before returning to 8B.
8. **[next] Optimize to 10x.**
   Measure CPU vs GPU after every dispatch slice; target is GPU
   `tokens/s >= CPU tokens/s * 10`. Prioritize persistent buffers, batched
   command submission, and resident compute over transfer-heavy paths.
9. **[next] UI reporting.**
   Surface `target_met`, speedup, current blocker, GPU layer count, and latest
   compare artifact in the project dashboard.
10. **[done] Distinguish daemon operations from containers in the UI.**
    Long-running compare/build cards are pdockerd operations and intentionally
    do not appear in `docker ps`; container cards are reconciled only from
    Engine API `/containers/json?all=1`. The llama GPU compare operation must
    surface CPU/GPU tokens/s, speedup, `target_met`, GPU layer count, current
    blocker, and artifact paths while cleanup removes ADB forwarding and marks
    failed operations on nonzero exit. CPU restore is opt-in because the next
    compare run recreates the required mode before measurement.

11. **[doing] Rework project/container identity.**
    Stop using project-name prefixes as the primary relationship key. Compose
    launches now label containers with a stable pdocker project ID, project
    directory, project name, and compose service name; UI cards must prefer
    those labels and Engine container IDs over name guesses. Name matching is
    only a legacy fallback for containers created before labels existed.
    Follow-up queue: logs, service health, lifecycle buttons, and duplicate
    cleanup must all operate on the resolved Engine container ID; interrupted
    compose runs with stale exited containers must not make a new running
    container ambiguous.
12. **[next] Add a local SQLite project index.**
    Add an app-owned database for `projects`, `compose_services`,
    `containers`, `images`, and `jobs`. The database is an index and
    relationship layer, not a replacement for Docker-compatible Engine state:
    container truth remains Engine ID/state, image truth remains image ID and
    layer digests, and project truth can later attach git remote/branch/status.
    Do not store file contents in SQLite. For overlay/COW, store metadata only:
    path, lower layer digest, upper path, whiteout state, size, mtime, and the
    owning project/container IDs. File payloads remain in content-addressed
    layers and upperdirs.
    The database must be disposable: store `schema_version`, run consistency
    checks on startup, and rebuild the index from `projects/*/compose.yaml`,
    `containers/*/state.json`, image configs, layer manifests, and upperdirs
    whenever the DB is missing, corrupt, or has dangling references.
    Use SQLite WAL for normal operation, periodically checkpoint to a replica
    such as `metadata.snapshot.sqlite`, and write a small manifest containing
    source hashes/counts so startup can decide whether to trust the primary DB,
    fall back to the replica, or rebuild from the filesystem.

Current 2026-05-04 blocker:

- Qwen3 8B Q4_K_M forced Vulkan can discover the pdocker GPU bridge, allocate
  the first offloaded Vulkan model buffer, complete transfer-only queue
  submits, lower generic SPIR-V dispatch metadata through the Android Vulkan
  executor contract, and load the HTTP server. `VULKAN_DISPATCH_V2` now
  preserves `VkPipelineShaderStageCreateInfo::pName` and bounded
  `VkSpecializationInfo` data without modifying llama.cpp, and
  `scripts/smoke-vulkan-icd-bridge.sh` verifies a minimal ICD dispatch through
  the executor command socket when local executor Vulkan preflight is available
  and reports a planned skip otherwise. It still cannot serve tokens reliably:
  prompt processing reaches a later generic SPIR-V dispatch where Android
  `vkQueueSubmit` returns `VK_ERROR_FEATURE_NOT_PRESENT`. CPU mode is restored
  as the usable path after GPU experiments.

Temporary behavior:

- `--gpus all`, Vulkan env, CUDA-compatible env, and GPU diagnostics are
  negotiation signals, not a complete runtime.
- Current benchmark has CPU/GLES first-pass coverage. Vulkan/OpenCL request
  plumbing exists, but direct Android library exposure is now classified as
  diagnostic-only because it crosses from glibc into Bionic-only dependencies.
  cuVK remains pending.

Real implementation needed:

1. Add Vulkan backend to `android-gpu-bench`.
2. Add device/thermal/driver metadata to benchmark artifacts.
3. Replace raw host-library exposure with a glibc-facing GPU bridge: container
   shim/device ABI, shared-memory command queue, Bionic GPU-executor process,
   fences, error propagation, and lifecycle management. The executor may run
   GPU commands only; llama.cpp and other app engines must stay inside the
   container. The ABI exposed to containers must be device-independent; device
   and backend variation is absorbed by executor capability probing and command
   lowering.
4. Implement minimal container-facing Vulkan/OpenCL validation against that
   bridge, not against directly exposed Android libraries.
5. Implement CUDA-compatible shim API only as a real library/runtime, not just
   env variables.
6. Add UI recommendation based on measured CPU/GPU crossover size.

Scaffold completed:

- APK-side `pdocker-gpu-executor` capability/vector-add probe.
- Container-side Linux/glibc `pdocker-gpu-shim` capability probe injected into
  GPU-requesting containers.
- Container-side Linux/glibc `pdocker-vulkan-icd.so` minimal Vulkan ICD surface
  injected through `/etc/vulkan/icd.d/pdocker-android.json`; this lets
  unmodified apps use the standard Vulkan loader path, but it is still marked
  `PDOCKER_VULKAN_ICD_READY=0`.
- First shared-buffer transport probe: the glibc shim creates a mapped vector
  buffer and passes its FD to the Android/Bionic executor with `SCM_RIGHTS`.
  This proves the bridge can move data without exposing Android GPU libraries
  to the glibc container, but it is still a benchmark scaffold.
- First registered-buffer transport probe: the executor can map a shared
  vector buffer once for a connection and run repeated commands against that
  registered buffer.

Next implementation slice:

- Replace the temporary socket command transport with a persistent shared-memory
  command ring, multi-buffer table, and fence/error protocol. The socket path
  and single registered vector buffer are now useful for measuring and
  debugging, but only as scaffolds.
- Keep persistent transport semantics. Benchmarks show one-connection-per-GPU
  command adds measurable overhead and is the wrong shape for LLM workloads.
- Keep container-visible paths under `/run/pdocker-gpu`; do not expose Android
  app-data absolute paths to container code.
- Add queue lifecycle under pdockerd so container processes never call Android
  vendor libraries directly.
- Add a real reusable buffer/fence protocol and then wire a minimal ggml/llama
  GPU backend path to the bridge.
- Lower minimal Vulkan compute calls from `pdocker-vulkan-icd.so` into the
  bridge before enabling llama.cpp GPU layers; llama.cpp itself must remain
  unmodified.
- Implement the next llama Vulkan bridge blockers found on 2026-05-04 and
  refined by the 2026-05-05 resident-cache probe: split or otherwise support
  4 GiB+ model buffers, handle pinned host-buffer paths without crashing, and
  reduce bridge upload/copy overhead now that real llama.cpp SPIR-V dispatch
  serves HTTP. Current forced-GPU status after `VULKAN_DISPATCH_V2`: Qwen3 8B
  Q4_K_M serves with `--n-gpu-layers 1`, but GPU throughput is still below CPU
  because copy-buffer staging and transfer-heavy bridge dispatches dominate.
- Keep CPU fallback healthy while GPU work is incomplete. CPU mode must hide
  Vulkan devices with `GGML_VK_VISIBLE_DEVICES=""` so llama.cpp does not enter
  Vulkan buffer scheduling with `--n-gpu-layers 0`.
- After each Vulkan clamp or bridge-accounting fix, run the full performance
  workflow rather than a one-off manual probe: CPU fallback baseline, forced
  Vulkan attempt, optional small-model green-path check, 8B retry, artifact
  copy to `docs/test` and `files/pdocker/bench`, and UI-visible reporting of
  speedup, `target_met`, GPU layer count, current blocker, and device/thermal
  metadata.

Acceptance:

- Benchmark report shows when GPU beats CPU on the current Android device.
- Container GPU diagnostics prove Vulkan loader access from inside the runtime.

## P2: UI and Editor Polish

Status: **partial**.

Temporary behavior:

- Some workflows are widgets; some still fall back to terminal/log tabs.
- Terminal/editor pinch zoom and selection are implemented but need device
  edge-case validation.

Real implementation needed:

1. Add feature-status coverage for every menu item so dead-end actions are
   visible in tests.
2. Keep host shell diagnostic-only, outside the normal user path.
3. Finish terminal selection handle behavior across wide ranges and IME cases.
4. Add editor encoding/newline controls and undo-safe whitespace transforms.
5. Add migration UI for project templates.

Acceptance:

- Every visible action either works, is clearly blocked with reason, or is
  hidden from normal flow.
- Terminal sessions survive back navigation and process recreation where
  feasible.

## P2: License and PRoot Retirement

Status: **default no-PRoot packaging exists; integrated backend no longer
tracks bundled PRoot/proot-runtime payloads**.

Temporary behavior:

- Optional proot comparisons are command-supplied diagnostics only.
- PRoot/talloc artifacts may appear locally while experimenting and should not
  be committed accidentally.

Real implementation needed:

1. Keep legacy PRoot out of the default APK and integrated backend tree.
2. Keep third-party notices aligned with actual packaged payloads.
3. Remove stale PRoot-era documentation as direct runtime coverage grows.

Acceptance:

- Default release APK contains no PRoot/talloc binaries.
- License notice asset matches packaged binaries and template-sourced code.

## Required Test Split

Fast tests, run on most builds:

- `bash scripts/verify-fast.sh`
- This already includes Dockerfile standardness, project-library checks,
  terminal/editor contracts, UI action wiring, backend compatibility audit, and
  `docker-proot-setup/scripts/verify_runtime_contract.py`.
- Keep GPU implementation out of the normal fast gate; use static/project
  contract checks here unless a GPU-facing script was changed.

Heavy tests, run before major runtime changes:

- `bash scripts/verify-heavy.sh --backend-quick`
- `bash scripts/verify-heavy.sh --backend-full`
- `bash scripts/android-device-smoke.sh --quick --no-install` for current
  device Engine/helper smoke.
- `bash scripts/android-device-smoke.sh --no-install` as the full Android
  runtime smoke. It should pass the tiny direct build/compose path on the SDK28
  compat flavor, including `compose up`, logs, non-TTY exec, Engine
  `Tty=true` exec, and `compose down`; failures here are release blockers.
- Add `docker run --rm ubuntu:22.04 echo hi` to the Android smoke gate so
  single-container process execution stays covered outside Compose.
- Default dev workspace `docker compose up --detach --build --remove-orphans`
  on device. This is intentionally heavier than the smoke test and is the gate
  for VS Code Server/Codex/Continue usability. Verify `docker compose logs`
  and `curl -I http://127.0.0.1:18080/`.
- llama CPU checks: when the model/server is already present, use
  `bash scripts/android-llama-bench.sh --predict 1 --repeat 1` as a quick
  sanity pass; use `--predict 8 --repeat 3` plus
  `bash scripts/android-llama-tool-bench.sh` for long CPU baseline runs.
- llama GPU completion gate: `bash scripts/android-llama-gpu-compare.sh` must
  write `docs/test/llama-gpu-compare-latest.json`, copy it to
  `files/pdocker/bench`, report `target_met`, current blocker, GPU layer count,
  and leave the last measured mode running unless `--restore` is explicitly
  requested. Do not claim GPU completion until the same unmodified llama.cpp
  image/model beats the CPU baseline by the target ratio.
- Runtime performance bench:
  `bash scripts/android-runtime-bench.sh` for short direct syscall stats, and
  `bash scripts/android-runtime-bench.sh --apt-update` for the slow apt wall
  clock path. Optional existing proot comparison:
  `bash scripts/android-runtime-bench.sh --proot-cmd '<command>'`.
- Storage metrics gate: keep static coverage in `scripts/verify-ui-actions.py`,
  and add Android smoke/manual verification that layer, image-view,
  container-private, total, and free-space metrics are nonnegative and refresh
  after build/prune/rebuild and after container file edit/copy-up flows.
- GPU bridge/executor scenarios after GPU/runtime changes:
  `bash scripts/bench-gpu-bridge.sh 3` for a quick local scaffold check,
  `bash scripts/bench-gpu-bridge.sh 50` for long overhead tracking, and
  `bash scripts/android-device-smoke.sh --quick --gpu-bench --no-install` after
  APK packaging or GPU runtime changes. Track NOOP/control overhead,
  persistent transport, FD/shared-buffer, and registered-buffer ratios.

Never mark a temporary workaround as complete unless the acceptance check for
the real behavior passes.

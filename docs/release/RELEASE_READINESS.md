# Release Readiness Checklist

Snapshot date: 2026-05-16.

This page is the canonical GitHub-facing release gate: it owns public release
posture, blocker scope, and release-candidate cut criteria. It keeps the public
README and showcase copy aligned with the current P0 blockers from
[`../plan/INCOMPLETE_IMPLEMENTATION_AUDIT_20260513.md`](../plan/INCOMPLETE_IMPLEMENTATION_AUDIT_20260513.md).
F-Droid-oriented build, signing, metadata, runtime-download, and generated
payload policy lives in
[`FDROID_RELEASE_PROCESS.md`](FDROID_RELEASE_PROCESS.md) and should be linked
instead of duplicated here. It is intentionally conservative: a feature is not
marked complete until the implementation and the referenced artifact both exist.

## Current release posture

**Posture:** development preview / engineering checkpoint, not a broad stable
release.

The app already demonstrates a useful Android-native Docker-shaped workspace:
image/build/Compose UI, Engine API-compatible daemon routes, persistent logs and
terminals, rootfs browsing, project templates, and measured Android direct
runtime experiments. The next public release should still be described as a
preview until the P0 items below have device evidence and no contradictory stale
documentation remains.

## Current blocker summary

- **llama.cpp GPU correctness — blocked / unfinished**
  - Why it blocks: Vulkan offload for llama.cpp is still not correct for
    `ngl>=1`; current artifacts show wrong-token or oracle mismatches. GPU
    acceleration must not be marketed as working inference.
  - Required evidence: passing CPU-vs-GPU correctness artifact with
    `benchmark_claim_allowed=true`; Q6_K/local-size/descriptor/final-store
    evidence for the active blocker; current blocker details live in
    [`LLAMA_GPU_BRIDGE_NEXT_STEPS.md`](../plan/LLAMA_GPU_BRIDGE_NEXT_STEPS.md).
- **Service health truth — blocked**
  - Why it blocks: cards or metadata can look healthy without proof that the
    current Engine container owns the listener and logs.
  - Required evidence: device artifact where UI card, Engine API, persisted
    state, process table, listener probe for `18080`/`18081`, and logs agree on
    one current container ID.
- **Runtime stop/process cleanup — blocked**
  - Why it blocks: a stop response can be HTTP 204 before child processes or
    GPU executor teardown is proven.
  - Required evidence: device stop/kill smoke with process-tree cleanup,
    executor cleanup, duplicate-name recovery, and logs.
- **Image pull crash safety — blocked**
  - Why it blocks: static checks and the synthetic residue kill/restart runner
    exist, but the timed live registry pull interruption remains a planned gap
    and cannot be counted as stable evidence.
  - Required evidence: kill-in-pull device scenario plus startup recovery
    verifier showing `.pull-*`, `.tmp-*`, and `.old-*` entries are not promoted
    as valid layers/tags, followed by a scenario-owned live-pull interruption
    lane that cannot overwrite user images.
- **COW/overlay mutation safety — blocked**
  - Why it blocks: host-local copy-up/archive checks and the kill-at-step
    device runner are represented, but device daemon/helper restart evidence is
    still non-promoting until the promotion condition passes.
  - Required evidence: fault-injection or replayable recovery artifact plus
    startup repair/check behavior, including archive PUT/whiteout/rename
    coverage and adb/run-as proof for every required kill checkpoint.
- **APK memory pager and OOM/LMK survival — blocked**
  - Why it blocks: Android may kill backend work while UI state survives; the
    managed/transparent pager scripts and host/static classification exist, but
    controlled connected-device pager plus LMK/backend-death replay remains
    non-promoting planned-gap evidence.
  - Required evidence: managed pager and telemetry artifacts from the installed
    APK plus structured memory/OOM events and a replayable LMK suspected device
    test.
- **Terminal `exec -it` device verifier — blocked for demo-ready terminal claims**
  - Why it blocks: host-side verifier logic exists, but a real UI container
    terminal must be paired with raw Engine exec input JSONL before static or
    skipped checks can promote the gate.
  - Required evidence: `ui-it-selftest-latest.json` plus
    `engine-exec-input-latest.jsonl` passing
    `scripts/verify-terminal-exec-it-artifact.py --require-container` on a
    device-run container session.
- **Modern/no-PRoot runtime truth — blocked for modern flavor execution claims**
  - Why it blocks: modern flavor surfaces can expose metadata routes while
    direct execution is incomplete.
  - Required evidence: either complete the no-PRoot executor or hard-disable
    execution actions with explicit runtime capability UI.
- **Direct `linkat` hardlink semantics — blocked for hardlink compatibility claims**
  - Why it blocks: the current Android fallback copies file bytes, so it cannot
    prove shared inode identity, link-count behavior, write-through semantics,
    or recovery from interrupted hardlink/CoW metadata updates.
  - Required evidence: non-promoting Android `linkat` device gate promotes only
    after identical `st_dev/st_ino`, `st_nlink` growth/decrement, write-through,
    Linux errno parity, and restart recovery are all proven.
- **Docker CLI `docker cp` end-to-end — planned-gap / non-promoting**
  - Why it blocks: host archive API unit coverage exists, but it does not prove
    Docker CLI copy behavior against the same current Engine container on a
    device.
  - Required evidence: planned device gate proving same Engine container ID,
    host-to-container and container-to-host copy, archive HEAD/GET/PUT,
    `X-Docker-Container-Path-Stat`, byte/sha256 equality, hardlink/symlink and
    metadata policy, xattr, whiteout rejection, and escape-negative cases.
- **Build/test checkpoint truth — blocked for stable label**
  - Why it blocks: some records include failing gates or scoped PASS results;
    Kotlin/native coverage is still incomplete, and host-only planned-gap
    verifier passes can otherwise look stronger than the evidence supports.
  - Required evidence: release notes and build records separate passed Android
    APK evidence from known failing/scoped gates. `status=planned-gap`,
    `success=false`, skipped device gates, and manifest lanes marked
    `stable_checkpoint_eligible=false` are not counted as stable checkpoints.

## Stable checkpoint exclusion rule

`tests/test_driver_manifest.json` and `docs/test/CI_GATE_LEDGER.md` now carry
the release honesty rule: a run manifest is stable-checkpoint eligible only
after in-scope P0 blockers are closed or explicitly scoped out, and every
device-gated artifact required by the gate ledger is passing with the named
proof. Host-only checks such as `host-smoke` and `release-honesty` are useful
regression/hygiene evidence, but they are non-promoting while they only prove
that planned gaps remain visible.

Current non-promoting gate representations include the llama GPU Q6_K
workgroup/writeback correctness workflow, the terminal exec-it artifact
verifier, APK memory pager plus OOM/LMK survival, storage graph/layer
maintenance UI evidence, direct `linkat` hardlink semantics, Docker CLI
`docker cp` end-to-end/archive API evidence, COW kill-at-step, and image
live-pull interruption. They are release-blocker evidence until their
device-gated promotion conditions produce passing artifacts.

For release notes and build records, treat these as blockers or scoped-out
limitations, not passes:

- `status=planned-gap`, `blocked`, `failed`, `skip`, or `skipped`;
- `success=false`;
- a device lane that was not run on the required installed APK/device;
- a required device artifact that is absent from the checkpoint bundle;
- a host-only verifier pass whose purpose is to keep a planned gap explicit.

Absence of real device artifacts is not a soft pass. If the release/checkpoint
bundle lacks a required artifact named in `docs/test/CI_GATE_LEDGER.md`, record
that row as "missing device artifact" or "planned gap" and keep the stable
checkpoint blocked unless the row is explicitly scoped out with user-visible
unsupported/experimental wording.

## Release readiness checklist

### Public claims and README

- [x] Describe pdocker as a Docker-shaped Android workspace, not Docker Desktop
  parity.
- [x] State that upstream Docker CLI/Compose, PRoot, proot-loader, and talloc
  are not bundled in the default product APK; keep detailed distribution policy
  in [`FDROID_RELEASE_PROCESS.md`](FDROID_RELEASE_PROCESS.md).
- [x] Keep bridge networking, media, and GPU behavior framed as Android-specific
  extensions with explicit limits.
- [x] Link from the README to this checklist and the live TODO/status docs.
- [ ] Remove or qualify any stale copy that implies llama GPU inference is
  correct for `ngl>=1`.
- [ ] Ensure every release note distinguishes fixed build evidence from open P0
  blockers.

### Device evidence gates

- [x] Fixed build `20260505.1` records APK build outputs and Android quick/full
  smoke evidence under [`builds/20260505.1/`](builds/20260505.1/).
- [ ] Service truth device artifact exists and ties UI/API/state/process/listener
  logs to one current container ID.
- [ ] Runtime stop/kill artifact proves direct child and GPU executor cleanup.
- [ ] Interrupted image pull artifact proves recovery does not publish partial
  layers/tags.
- [ ] Image live-pull interruption artifact proves a scenario-owned registry
  pull can be killed mid-transfer without publishing or overwriting user tags.
- [ ] COW/overlay fault-injection artifact proves mutation recovery is
  fail-closed.
- [ ] COW kill-at-step artifact proves adb/run-as daemon/helper interruption
  for every required copy-up, rename, whiteout, archive, and metadata case.
- [ ] OOM/LMK replay artifact proves stale UI/backend state is classified.
- [ ] Terminal exec-it artifact verifier passes with raw device Engine exec
  input evidence for a real container session.
- [ ] Storage metrics device sequence covers build, prune, rebuild, and
  edit/copy-up without double-counting shared layers.
- [ ] Storage graph/layer maintenance UI evidence proves cache-only references
  remain distinct from image references, unique/shared/stale sizes are visible,
  tree rows expose detail/file/delete-with-cache-cleanup actions, and stale
  build-cache or unreferenced-layer garbage cleanup is shown.
- [ ] Direct `linkat` hardlink artifact proves inode identity, link-count
  preservation, write-through behavior, errno parity, and restart recovery.
- [ ] Docker CLI `docker cp` end-to-end artifact proves same-container-ID
  archive HEAD/GET/PUT behavior, byte/sha256 equality, metadata policy,
  hardlink/symlink handling, xattr, and escape/whiteout negative cases.

### llama GPU release gate

- [x] CPU/no-offload llama server route has device evidence.
- [x] GPU compare workflow records correctness data and blocks benchmark claims
  when required probes fail.
- [ ] `ngl=1` passes CPU-vs-GPU deterministic correctness.
- [ ] The current Q6_K blocker is resolved or replaced by newer documented
  blocker evidence in
  [`LLAMA_GPU_BRIDGE_NEXT_STEPS.md`](../plan/LLAMA_GPU_BRIDGE_NEXT_STEPS.md)
  and the artifact sweep.
- [ ] Q6_K workgroup/writeback diagnostics are paired with a matching Q6_K
  oracle before any benchmark or inference claim.
- [ ] Any speedup claim is paired with a passing correctness artifact and device
  metadata.

**Current wording rule:** llama.cpp GPU support is **unfinished**. It may be
mentioned as an active Vulkan bridge experiment with diagnostics, but not as
working accelerated inference.

### Release-candidate cut criteria

A release candidate can be proposed only when:

1. every P0 row in the blocker summary is closed or explicitly scoped out of the
   release with user-visible disabled/experimental wording;
2. device evidence is archived under `docs/test/` and linked from the release
   note;
3. README, showcase, status, and TODO wording agree on what is complete,
   preview-only, blocked, or unsupported;
4. build/test records do not present known failing gates as green;
5. test-driver lanes and scenario entries marked non-promoting are not counted
   as stable checkpoint evidence;
6. signing and distribution steps follow [`FDROID_RELEASE_PROCESS.md`](FDROID_RELEASE_PROCESS.md)
   and keep signing material outside Git.

## Suggested GitHub release framing

Use this framing until the checklist closes:

> pdocker-android is an engineering preview of a Docker-shaped Android
> workspace. It can demonstrate native UI image/build/Compose flows, Engine API
> compatibility work, rootfs browsing, persistent logs/terminals, and direct
> runtime experiments on real devices. Some release-critical correctness and
> recovery gates remain open, especially llama.cpp GPU correctness, service
> health truth, stop cleanup, and crash/LMK recovery.

Avoid these claims for now:

- "Docker Desktop for Android" or full Docker parity.
- "llama.cpp GPU acceleration works" for `ngl>=1`.
- "service is healthy" without current-container listener/log evidence.
- "crash safe" for image/layer/COW mutations without the device recovery
  artifacts listed above.
- "storage cleanup is complete" or "safe to delete cache/images" without the
  storage graph/layer maintenance UI and device cleanup evidence listed above.
- "`linkat` hardlinks work" or "`docker cp` is complete" without their
  specific non-promoting device gates promoting.

# Release Readiness Checklist

Snapshot date: 2026-05-15.

This page is the GitHub-facing release gate. It keeps the public README and
showcase copy aligned with the current P0 blockers from
[`INCOMPLETE_IMPLEMENTATION_AUDIT_20260513.md`](INCOMPLETE_IMPLEMENTATION_AUDIT_20260513.md).
It is intentionally conservative: a feature is not marked complete until the
implementation and the referenced artifact both exist.

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
    `benchmark_claim_allowed=true`; Q6_K/local-size/descriptor evidence for the
    active blocker; update
    [`LLAMA_GPU_BRIDGE_NEXT_STEPS.md`](LLAMA_GPU_BRIDGE_NEXT_STEPS.md).
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
- **OOM/LMK survival — blocked**
  - Why it blocks: Android may kill backend work while UI state survives;
    host/static classification exists, but controlled connected-device
    LMK/backend-death replay is still non-promoting planned-gap evidence.
  - Required evidence: structured memory/OOM events and replayable LMK suspected
    device test.
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

Current non-promoting gate representations include the archive API host
compatibility check, the terminal exec-it artifact verifier, COW kill-at-step
device lane, OOM/LMK survival lane, and image live-pull interruption plan. They
are release-blocker evidence until their device-gated promotion conditions
produce passing artifacts.

For release notes and build records, treat these as blockers or scoped-out
limitations, not passes:

- `status=planned-gap`, `blocked`, `failed`, `skip`, or `skipped`;
- `success=false`;
- a device lane that was not run on the required installed APK/device;
- a host-only verifier pass whose purpose is to keep a planned gap explicit.

## Release readiness checklist

### Public claims and README

- [x] Describe pdocker as a Docker-shaped Android workspace, not Docker Desktop
  parity.
- [x] State that upstream Docker CLI/Compose, PRoot, proot-loader, and talloc
  are not bundled in the default product APK.
- [x] Keep bridge networking, media, and GPU behavior framed as Android-specific
  extensions with explicit limits.
- [x] Link from the README to this checklist and the live TODO/status docs.
- [ ] Remove or qualify any stale copy that implies llama GPU inference is
  correct for `ngl>=1`.
- [ ] Ensure every release note distinguishes fixed build evidence from open P0
  blockers.

### Device evidence gates

- [x] Fixed build `20260505.1` records APK build outputs and Android quick/full
  smoke evidence under [`../test/build-20260505.1/`](../test/build-20260505.1/).
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

### llama GPU release gate

- [x] CPU/no-offload llama server route has device evidence.
- [x] GPU compare workflow records correctness data and blocks benchmark claims
  when required probes fail.
- [ ] `ngl=1` passes CPU-vs-GPU deterministic correctness.
- [ ] Q6_K-like blocker `0x274f68a67dfef210` is resolved or replaced by a newer
  documented blocker with artifact evidence.
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
6. signing and distribution steps follow [`../build/FDROID_RELEASE_PROCESS.md`](../build/FDROID_RELEASE_PROCESS.md)
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

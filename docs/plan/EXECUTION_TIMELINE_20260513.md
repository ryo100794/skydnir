# Execution Timeline - 2026-05-13

This timeline turns the current TODO ledger and incomplete-implementation audit
into small, delegable execution lanes.  It is intentionally evidence-driven:
work is not complete until implementation, test evidence, documentation, and
truthful UI/API behavior agree.

Primary sources:

- [`TODO.md`](TODO.md)
- [`INCOMPLETE_IMPLEMENTATION_AUDIT_20260513.md`](INCOMPLETE_IMPLEMENTATION_AUDIT_20260513.md)
- [`LLAMA_GPU_BRIDGE_NEXT_STEPS.md`](LLAMA_GPU_BRIDGE_NEXT_STEPS.md)

## Operating Rules

1. **No fake success.** A card, API response, health state, benchmark, or log
   may only claim success when the backing evidence exists.
2. **Small slices.** Large P0 work is split into one acceptance gate or one
   implementation seam at a time.
3. **One owner per write surface.** Agents must not edit overlapping files
   unless the manager explicitly reassigns ownership.
4. **Evidence before closure.** Every closure needs a command, artifact, or
   device/manual record that can be repeated.
5. **llama.cpp stays unmodified.** GPU bridge work remains in pdocker
   ICD/executor/transport/tests, not upstream model/runtime sources.

## Release Gates

| Gate | Required evidence | Blocks |
| --- | --- | --- |
| G0: truthful state | `docker ps`, UI cards, Engine state, process table, listener probes, and logs agree on container ID. | Any user-facing release claim. |
| G1: safe stop/recovery | Stop/kill leaves no stale direct child, GPU executor, partial image, or false running state. | Long-running workloads and public APK testing. |
| G2: correct GPU | llama GPU prompt correctness passes before speed is reported. | GPU acceleration marketing/performance claims. |
| G3: storage safety | Pull/build/COW mutation survives interruption and startup recovery. | Image/library workflows. |
| G4: test density honesty | Planned gaps stay visible; build records do not claim stronger evidence than tests provide. | Release candidate tagging. |

## Timeline

### T0 - Now / Stabilize the Board

| Lane | Owner | Scope | Acceptance |
| --- | --- | --- | --- |
| T0-A | manager | Keep this execution timeline and TODO synchronized. | New work appears in this file or `TODO.md`; stale claims are removed. |
| T0-B | agent: Android smoke | Add a single-container `docker run --rm ubuntu:22.04 echo hi` route to Android smoke, or make its failure explicit. | Smoke script contains the route, records stdout/exit code, and does not fake success. |
| T0-C | agent: memory UI | Show pager artifact source/age/status and transparent metrics without confusing them with live `/proc`. | Static contract test covers source/age and transparent metrics. |
| T0-D | agent: service truth plan | Convert service health/runtime teardown planned gaps into executable acceptance checks. | Feature scenario entries contain concrete commands, evidence, and exit criteria. |

### T1 - P0 Truth and Recovery

| Lane | Scope | Deliverables |
| --- | --- | --- |
| T1-A Service truth | Implement current-container listener proof for 18080/18081. | Engine ID correlation, listener probe, log correlation, UI label, device artifact. |
| T1-B Runtime teardown | Prove stop/kill removes direct child and GPU executor state. | Device smoke with process tree before/after, stale PID rejection, logs. |
| T1-C Image pull crash safety | Atomic layer/tag publish and startup recovery for `.pull-*`, `.tmp-*`, `.old-*`. | Local recovery verifier plus interrupted-pull device scenario. |
| T1-D OOM/LMK evidence | Structured memory/down events and classifier. | Reproducible LMK/OOM artifact; UI no longer shows stale running state after backend death. |

### T2 - GPU Correctness Before Speed

| Lane | Scope | Deliverables |
| --- | --- | --- |
| T2-A Q6_K blocker | Resolve or isolate `0x274f68a67dfef210` without llama.cpp changes. | CPU oracle, descriptor/local-size evidence, pass/fail artifact. |
| T2-B NGL correctness ladder | NGL=1 then NGL=2 prompt correctness before tps reports. | `2+3=` or equivalent correctness artifact, then benchmark artifact. |
| T2-C Bridge protocol | Move from temporary/hash-specific behavior toward command-ring/classified operations. | Unsupported traces, no silent CPU/wrong GPU result, transport metrics. |

### T3 - Docker Compatibility Surface

| Lane | Scope | Deliverables |
| --- | --- | --- |
| T3-A Terminal `-it` | Separate generic terminal surface from Engine exec/attach/local PTY/log sessions. | UI self-test plus Engine exec smoke for Enter, Ctrl-C, cursor keys, `top`, and shell history. |
| T3-B Compose parser | Reduce hand-written YAML ambiguity or delegate parsing. | Golden Compose corpus and negative grammar tests. |
| T3-C Build context tar | Support symlink, directory headers, mode, long path/PAX, and `.dockerignore` parity. | Tar corpus and external Dockerfile compatibility cases. |
| T3-D Archive API | GET/PUT/HEAD archive corpus with traversal and lower/upper merge cases. | Golden API fixtures and negative archive tests. |

### T4 - Storage, SAF, and Media

| Lane | Scope | Deliverables |
| --- | --- | --- |
| T4-A COW/overlay safety | Fault-injection verifier for copy-up, whiteout, archive PUT, metadata updates. | Startup repair/check and low-space/kill-at-step evidence. |
| T4-B SAF Phase 2 | Delete, rename, conflict quarantine, Unix metadata sidecar. | Mediator completion artifacts and conflict tests. |
| T4-C Storage metrics device lane | Build/prune/rebuild/edit/copy-up refresh verification. | Device capture artifact with nonnegative and overlap-aware values. |
| T4-D Media bridge | Camera/audio capture/playback executor IPC after Ready=false control plane. | Permission flow, device descriptors, minimal capture/playback artifact. |

### T5 - Release Readiness

| Lane | Scope | Deliverables |
| --- | --- | --- |
| T5-A Coverage honesty | Kotlin/native coverage or explicit release blocker. | Coverage artifact or documented non-release status. |
| T5-B Reproducible build | Pinned build process and source-built native payload policy. | Release script, artifact manifest, source/license audit. |
| T5-C Public docs | Showcase, README, compatibility, and known-limits stay synchronized with actual gates. | Generated dashboard/timeline from TODO and audit. |

## Current Agent Assignments

| Agent | Lane | Write ownership | Manager status |
| --- | --- | --- | --- |
| Locke | T0-B Android smoke single-container run gate | `scripts/android-device-smoke.sh`, verifier/test docs only | running |
| Raman | T0-C Memory-layer UI source/age/transparent metrics | `MainActivity.kt` memory-layer block, memory strings, memory UI test | running |
| Pauli | T0-D Service truth executable acceptance | feature scenarios, service truth verifier/docs only | running |

## Backlog Decomposition Queue

These are too large to implement as one task and must be split before worker
assignment:

1. **Port mapping**: inactive UI truth -> localhost proxy -> conflict detector
   -> syscall rewrite experiment.
2. **COW safety**: mutation inventory -> journal/check design -> one operation
   fault injector -> startup repair -> broad overlay bench.
3. **Terminal**: session type model -> Engine exec transport -> input encoding
   -> VT/xterm rendering -> IME/selection regression.
4. **Compose parser**: unsupported syntax detector -> golden subset -> parser
   replacement/delegation -> upstream differential.
5. **Memory pager**: artifact source display -> telemetry API -> region table
   -> dirty precision -> multi-thread/signal stress -> container opt-in.

## Manager Checklist Per Merge

- [ ] Worker final result reviewed.
- [ ] No overlapping file ownership conflict.
- [ ] Focused tests run.
- [ ] APK build run when Android/Kotlin/native payload changed.
- [ ] TODO/audit/timeline updated.
- [ ] Commit excludes unrelated dirty GPU/runtime artifacts unless explicitly
      part of the lane.
- [ ] Push only after rebase/autostash if remote moved.

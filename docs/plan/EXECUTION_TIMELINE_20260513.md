# Execution Timeline - 2026-05-13

This timeline turns the current TODO ledger and incomplete-implementation audit
into small, delegable execution lanes.  It is intentionally evidence-driven:
work is not complete until implementation, test evidence, documentation, and
truthful UI/API behavior agree.

Update note (2026-05-15): the evidence-honesty gate is now explicit. Planned
gaps, skipped/unrun device lanes, non-passing artifacts, and host-only checks
that merely prove a gap remains visible are non-promoting evidence for stable
checkpoint purposes. The current sync set covers the archive host gate,
terminal exec-it artifact verifier, COW kill-at-step device lane, OOM/LMK
survival lane, and image live-pull interruption plan.

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
6. **Compaction-safe handoff.** Before context exhaustion or compaction, stop detailed risky work, summarize the lane in `AGENT_COORDINATION.md`, checkpoint only reviewed small slices, delegate the next narrow action, and never start a large new patch with low context budget.

## Release Gates

| Gate | Required evidence | Blocks |
| --- | --- | --- |
| G0: service truth same-container-ID | `docker ps`, UI cards, Engine state, persisted state, process table, listener probes, and logs agree on the same current Engine container ID. | Any user-facing running/healthy claim. |
| G1: runtime teardown | Stop/kill leaves no stale direct child, GPU executor, listener, duplicate-name residue, or false running state. | Long-running workloads and public APK testing. |
| G2: correct GPU with synchronized launch environment | llama GPU correctness passes with the same diagnostic/tuning environment through compare script, pdockerd, and UI/compose launches before speed is reported. | GPU acceleration marketing/performance claims. |
| G3: storage crash safety | Image pull and COW/overlay mutation survive interruption, low-space, startup recovery, archive PUT/whiteout/rename, and safe scenario-owned live pull interruption. | Image/library workflows. |
| G4: interactive and workspace hard gates | UI `exec -it` and default VS Code workspace health pass real device gates; terminal host verifiers without raw Engine exec JSONL are non-promoting. | Demo-ready APKs. |
| G5: SAF direct output | `/documents` writes prove SAF-backed payload plus UnixFS sidecar metadata, or record a truthful fallback. | SD-card/Documents workflows. |
| G6: test density honesty | Planned gaps stay visible; build records do not claim stronger evidence than tests provide, and non-promoting manifest lanes cannot be counted as stable checkpoint evidence. | Release candidate tagging. |

## Timeline

### T0 - Now / Stabilize the Board

| Lane | Owner | Scope | Acceptance |
| --- | --- | --- | --- |
| T0-A | manager | Keep this execution timeline and TODO synchronized. | New work appears in this file or `TODO.md`; stale claims are removed. |
| T0-B | agent: Android smoke | Add a single-container `docker run --rm ubuntu:22.04 echo hi` route to Android smoke, or make its failure explicit. | Smoke script contains the route, records stdout/exit code, and does not fake success. |
| T0-C | agent: memory UI | Show pager artifact source/age/status, transparent metrics, and stale/planned-gap state without confusing them with live `/proc` or success. | Static contract test covers source/age, transparent metrics, and no live-success wording without an artifact. |
| T0-D | agent: service truth plan | Convert service health/runtime teardown planned gaps into executable acceptance checks. | Feature scenario entries contain concrete commands, evidence, and exit criteria. |
| T0-E | manager | Align TODO, CI gate ledger, scenario ledger, and test driver manifest so planned-gap/device-gated items are non-promoting. | `release-honesty` lane exists, manifest lanes declare stable-checkpoint exclusion, and release docs link residual blockers instead of counting them as stable. |
| T0-F | manager/agent | Apply the compaction-safe handoff protocol before risky detailed work when context is low. | `AGENT_COORDINATION.md` records stop/summarize/checkpoint/delegate rules, the low-context no-large-patch rule, and concise artifact reporting; `python3 scripts/verify-docs-maintenance.py` guards those terms. |

### T1 - P0 Truth, Recovery, and GPU Correctness

| Lane | Scope | Deliverables |
| --- | --- | --- |
| T1-A Service truth same-container-ID | Implement current-container proof for UI cards, `docker ps`, Engine API, persisted state, process table, listeners, and logs. | Same-ID device artifact, stale/unknown/ambiguous UI states, no healthy claim from metadata alone. |
| T1-B Runtime teardown | Prove stop/kill removes direct children, GPU executor state, listeners, and stale PID/name state. | Device smoke with process tree before/after, stale PID rejection, duplicate-name cleanup, logs. |
| T1-C llama GPU Q6_K/env propagation | Resolve or isolate the Q6_K blocker and prevent compare-script-only environment behavior. | CPU/oracle or pass-through evidence, synchronized env contract, artifact verifier rejection of divergent launch paths. |
| T1-D Image pull crash safety | Atomic layer/tag publish and startup recovery for `.pull-*`, `.tmp-*`, `.old-*`, plus timed live-pull interruption only against scenario-owned/isolated fixtures. | Local recovery verifier, synthetic interrupted-pull kill/restart device scenario, and a non-promoting live-pull plan until the live artifact passes. |
| T1-E COW/overlay mutation safety | Prove copy-up, whiteout, rename, archive PUT, hardlink metadata, low-space, and kill-at-step behavior fail closed. | Host archive/COW verifier, startup repair/check, recovery artifact, and adb/run-as COW kill-at-step evidence before promotion. |
| T1-F OOM/LMK evidence | Structured large-allocation guard decisions, memory/down events, backend death identity, and classifier. | Reproducible LMK/OOM artifact; host/static OOM evidence remains non-promoting until controlled device replay proves unsafe allocations return diagnostic `ENOMEM`, backend death is classified without fake success, and stale UI/memory cards are rejected after backend death. |

### T2 - User-Facing Hard Gates

| Lane | Scope | Deliverables |
| --- | --- | --- |
| T2-A Terminal hard gate | Separate generic terminal surface from Engine exec/attach/local PTY/log sessions and require a real container. | UI self-test plus raw Engine exec input JSONL verified by `scripts/verify-terminal-exec-it-artifact.py --require-container` for Enter, Ctrl-C, cursor keys, `top`, `q`, resize, and IME behavior. |
| T2-B VS Code health gate | Verify default workspace compose/build/run and code-server reachability without relying on stale cards. | Current Engine ID, `18080` listener, HTTP proof, extension evidence, UI card truth artifact. |
| T2-C SAF direct output | Make `/documents` a SAF-backed UnixFS exchange layer with explicit sidecar metadata. | Direct-write artifact, metadata sidecar proof, failure/fallback record, no silent app-private-only success. |

### T3 - GPU Throughput After Correctness

| Lane | Scope | Deliverables |
| --- | --- | --- |
| T3-A NGL correctness ladder | NGL=1 then NGL=2 prompt correctness before tps reports. | `2+3=` or equivalent correctness artifact, then benchmark artifact. |
| T3-B Bridge protocol | Move from temporary/hash-specific behavior toward command-ring/classified operations. | Unsupported traces, no silent CPU/wrong GPU result, transport metrics. |
| T3-C Benchmark comparison | Compare CPU, GPU, and bridge overhead only after correctness and env parity pass. | Target, speedup, thermal/device metadata, artifact paths, and correctness report. |

### T4 - Docker Compatibility Surface

| Lane | Scope | Deliverables |
| --- | --- | --- |
| T4-A Compose parser | Reduce hand-written YAML ambiguity or delegate parsing. | Golden Compose corpus and negative grammar tests. |
| T4-B Build context tar | Support symlink, directory headers, mode, long path/PAX, and `.dockerignore` parity. | Tar corpus and external Dockerfile compatibility cases. |
| T4-C Archive API | GET/PUT/HEAD archive corpus with traversal and lower/upper merge cases. | `scripts/verify-archive-api-compat.py` host corpus plus negative archive tests; release promotion still depends on COW/archive device mutation evidence. |
| T4-D Port mapping | Advance inactive metadata toward localhost proxy/rewrite evidence and conflict tests. | Active/inactive/conflict labels backed by listener/proxy proof. |

### T5 - Storage, SAF, and Media Follow-Through

| Lane | Scope | Deliverables |
| --- | --- | --- |
| T5-A SAF Phase 2 | Delete, rename, conflict quarantine, Unix metadata sidecar. | Mediator completion artifacts and conflict tests. |
| T5-B Storage metrics device lane | Build/prune/rebuild/edit/copy-up refresh verification. | Device capture artifact with nonnegative and overlap-aware values. |
| T5-C Media bridge | Camera/audio capture/playback executor IPC after Ready=false control plane. | Permission flow, device descriptors, minimal capture/playback artifact. |

### T6 - Release Readiness

| Lane | Scope | Deliverables |
| --- | --- | --- |
| T6-A Coverage honesty | Kotlin/native coverage or explicit release blocker. | Coverage artifact or documented non-release status. |
| T6-B Reproducible build | Pinned build process and source-built native payload policy. | Release script, artifact manifest, source/license audit. |
| T6-C Public docs | Showcase, README, compatibility, and known-limits stay synchronized with actual gates. | Generated dashboard/timeline from TODO and audit. |
| T6-D Stable checkpoint honesty | Release notes classify planned-gap, blocked, skipped, and device-unrun evidence as residual gaps. | No stable label until CI gate ledger promotion conditions and release-readiness blocker closures agree. |

## Current Agent Assignments

Historical snapshot note: this table records the assignment state captured on
2026-05-13. Live agent ownership is tracked only in
[`AGENT_COORDINATION.md`](AGENT_COORDINATION.md).

| Agent | Lane | Write ownership | Manager status |
| --- | --- | --- | --- |
| Locke | T0-B Android smoke single-container run gate | `scripts/android-device-smoke.sh`, verifier/test docs only | historical |
| Raman | T0-C Memory-layer UI source/age/transparent metrics | `MainActivity.kt` memory-layer block, memory strings, memory UI test | historical |
| Pauli | T0-D Service truth executable acceptance | feature scenarios, service truth verifier/docs only | historical |

## Backlog Decomposition Queue

These are too large to implement as one task and must be split before worker
assignment:

1. **Service truth**: UI rendered-card export -> Engine/state/process/listener
   join -> stale/unknown labels -> same-ID hard gate.
2. **Runtime teardown**: API stop -> process tree proof -> GPU executor proof
   -> duplicate-name cleanup -> restart artifact.
3. **llama GPU**: RoPE/Yarn baseline lock -> Q6_K split -> env propagation
   parity -> NGL ladder -> benchmark report.
4. **Image pull crash safety**: atomic publish audit -> interrupted-pull kill
   -> restart recovery -> partial-image negative inspect/run.
5. **COW safety**: mutation inventory -> journal/check design -> one operation
   fault injector -> startup repair -> broad overlay bench.
6. **Terminal**: session type model -> Engine exec transport -> input encoding
   -> VT/xterm rendering -> IME/selection regression.
7. **VS Code health**: compose/build run -> listener proof -> extension proof
   -> UI/Engine same-ID artifact.
8. **SAF direct output**: backend abstraction -> direct write -> sidecar
   metadata -> fallback record -> conflict tests.
9. **Port mapping**: inactive UI truth -> localhost proxy -> conflict detector
   -> syscall rewrite experiment.
10. **Compose parser**: unsupported syntax detector -> golden subset -> parser
   replacement/delegation -> upstream differential.
11. **Memory pager / app-level virtual memory**: artifact source display ->
   large-allocation guard thresholds -> OOM/LMK ring/classifier -> telemetry API
   -> region table -> `mmap`/`mprotect`/`sigaction`/`munmap` admission negatives
   -> userfaultfd/capability gate -> dirty precision -> multi-thread/signal
   stress -> container opt-in. Every step is non-promoting until hardware
   evidence proves no unsafe fake success.

## Manager Checklist Per Merge

- [ ] Worker final result reviewed.
- [ ] No overlapping file ownership conflict.
- [ ] Focused tests run.
- [ ] APK build run when Android/Kotlin/native payload changed.
- [ ] TODO/audit/timeline updated.
- [ ] Planned-gap/device-gated artifacts are not counted as stable checkpoint
      evidence unless their promotion condition is satisfied.
- [ ] Commit excludes unrelated dirty GPU/runtime artifacts unless explicitly
      part of the lane.
- [ ] Push only after rebase/autostash if remote moved.

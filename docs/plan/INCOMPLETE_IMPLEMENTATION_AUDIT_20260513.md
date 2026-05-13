# Incomplete Implementation Audit - 2026-05-13

This audit consolidates the current unfinished, partial, temporary, or
insufficiently verified work found across Markdown documentation, Kotlin/Android
code, native/direct-runtime code, scripts, and test ledgers. It is not limited
to the memory-layer UI. The goal is to prevent temporary scaffolding from being
mistaken for product behavior.

## Collection Method

- Static keyword sweep across source, scripts, docs, and test ledgers for:
  `TODO`, `planned-gap`, `not implemented`, `temporary`, `workaround`,
  `placeholder`, `stub`, `unsupported`, and runnable-disabled scenarios.
- Agent review of:
  - Android/Kotlin/UI/service/build areas.
  - Native C/direct executor/GPU/syscall/pdockerd areas.
  - Documentation, test manifests, and planned-gap ledgers.
- Manual consolidation into priority buckets.

## Priority Rules

- **P0**: Can produce false success, wrong computation, stale running/healthy
  state, data corruption, process leaks, or core Docker workflow failure.
- **P1**: Significant compatibility, reliability, or observability gap, but
  usually bounded by explicit warnings, planned-gap status, or opt-in paths.
- **P2**: Important for release quality, broader compatibility, or long-term
  maintainability, but not the next blocker for core workflows.

## P0 Items

| Area | Evidence | Current incomplete state | User impact | Next action |
| --- | --- | --- | --- | --- |
| llama GPU correctness | `docs/plan/TODO.md`, `docs/test/LLAMA_GPU_CORRECTNESS_20260507.md` | NGL=1 still fails required probes; Q6_K-like hash `0x274f68a67dfef210` remains the main blocker; NGL=2 pipeline creation is not correctness. | GPU acceleration cannot be claimed; prompts can return wrong tokens. | Keep llama.cpp unchanged; add/verify Q6_K oracle, descriptor/local-size evidence, and correctness gate before any performance claim. |
| Service health truth | `docs/plan/TODO.md`, `tests/feature_scenarios.json` | 18080/18081 health still needs actual listener proof tied to the current Engine container ID and logs. | UI can show running/healthy while service is unreachable or stale. | Implement device gate: UI card, Engine API, persisted state, process table, listener probe, and logs must agree on one container ID. |
| Runtime stop/process cleanup | `docs/plan/TODO.md`, `tests/feature_scenarios.json` | HTTP 204 stop can still lack proof that direct children and GPU executor are gone. | Orphaned processes, stale Up state, freezes, and resource leaks. | Add stop/kill device smoke with process-tree, executor, logs, and duplicate-name cleanup evidence. |
| Image pull crash safety | `docs/plan/TODO.md` | Atomic `.pull-*`, `.tmp-*`, `.old-*` recovery and interrupted-pull device scenario are not closed. | Partial images/layers may later be treated as valid. | Add startup recovery verifier and kill-in-pull device test. |
| COW/overlay mutation safety | `docs/design/COW_OVERLAY_STORAGE.md` | No storage-wide journal/fail-closed verifier for copy-up, whiteout, archive PUT, metadata updates, low-space, and kill-at-each-step cases. | Layer/upperdir metadata can become inconsistent after crash/OOM. | Add fault-injection verifier and startup repair/check for overlay metadata. |
| OOM/LMK survival | `tests/abnormal_event_cases.json`, `tests/feature_scenarios.json` | Telemetry ring and LMK replay classifier are designed but not executable. | Android may kill backend while UI survives with stale state. | Implement structured memory/OOM events, LMK suspected classification, and replayable device test. |
| Modern/no-PRoot runtime | `app/build.gradle.kts`, `MainActivity.kt` | `modern` flavor still exposes metadata paths while direct process execution is not complete. | Users can reach actions that cannot execute containers. | Either complete no-PRoot executor or hard-disable execution actions with truthful runtime capability UI. |
| Build/test checkpoint truth | `docs/test/build-20260505.1/README.md`, `docs/test/TEST_DESIGN_STANDARD.md` | Some build records include failing gates or PASS with no JVM tests; Kotlin/native coverage is still absent. | A build can look more stable than the evidence supports. | Mark as release blocker until coverage/evidence is complete or intentionally scoped. |

## P1 Items

| Area | Evidence | Current incomplete state | User impact | Next action |
| --- | --- | --- | --- | --- |
| Active port mapping | `pdockerd`, `TODO.md`, `tests/feature_scenarios.json` | Published ports are recorded as planned/inactive metadata; no active proxy/rewrite proof. | `-p`/Compose ports can look Docker-like while unreachable. | Keep inactive wording; implement localhost proxy or syscall rewrite plus conflict tests. |
| Network model | `pdockerd`, `docs/test/COMPATIBILITY.md` | Host-network stub only; bridge IP, DNS server, iptables, and real isolation are absent. | Compose service DNS/network behavior is limited. | Explicit driver capability matrix; add unsupported-mode negative corpus. |
| Terminal `-it` | `TODO.md`, `STATUS.md` | Terminal architecture refactor remains open; session types are not fully separated. | exec/attach/local PTY/log panes regress easily. | Implement design from `TERMINAL_STREAM_ARCHITECTURE.md` with UI + Engine exec smoke. |
| Compose parser | `MainActivity.kt`, input grammar ledgers | Hand-written subset parser; full Compose/YAML grammar and upstream differential validation are planned gaps. | Valid Compose files can be partly ignored or misread. | Delegate to daemon parser or add real parser/golden differential corpus. |
| Build context tar | `DockerEngineClient.kt` | Minimal tar writer handles regular files only; directory/symlink/mode/PAX/long-path behavior is incomplete. | External Dockerfiles can fail or lose metadata. | Add tar compatibility corpus and implement missing tar features. |
| SAF/Documents Phase 2 | `TODO.md`, `pdockerd`, `tests/feature_scenarios.json` | Delete, rename, conflict quarantine, and Unix metadata emulation are not complete. | Documents sync can misrepresent completion or lose conflict intent. | Separate accepted vs completed states; add mediator completion artifacts and conflict tests. |
| Memory pager productization | `APK_MEMORY_PAGER.md`, `pdocker_direct_exec.c` | PoC exists, but managed-region tables, dirty precision, multi-thread/signal guardrails, and latency stress tests are incomplete. | Large-workload memory mode is not safe to enable broadly. | Keep opt-in; add synthetic fault-latency/stress/guardrail tests before container opt-in. |
| Memory-layer UI | `MainActivity.kt`, strings, tests | Overview graph exists, but live pdockerd telemetry, stale-artifact age, per-container memory, and full string localization are incomplete. | UI can mix live `/proc` with stale self-test artifacts. | Add artifact metadata display, pdockerd telemetry API, and per-container breakdown. |
| GPU bridge protocol | `GPU_COMPAT.md`, `pdocker_gpu_executor.c`, Vulkan/OpenCL ICDs | Temporary socket transport, hash-specific or safe-kernel paths, limited OpenCL vector-add coverage, incomplete fence/error protocol. | GPU compatibility remains narrow and hard to generalize. | Move toward persistent command ring, operation classification, and explicit unsupported traces. |
| Media bridge | `pdockerd`, `PdockerdService.kt`, `tests/test_media_bridge_contract.py` | Camera/audio descriptors exist, but capture/playback commands are not implemented. | Containers cannot actually use camera/mic/audio yet. | Keep Ready=false; implement Camera2/AudioRecord/AudioTrack executor IPC later. |
| Dockerfile multi-stage | `pdockerd` | `COPY/ADD --from` is not implemented. | Common multi-stage Dockerfiles fail. | Either implement multi-stage or keep explicit error plus regression corpus. |
| npm rename/reify | `TODO.md`, feature scenarios | npm self-update is still a direct-runtime compatibility gap. | Some Node/npm Dockerfiles fail. | Add focused npm self-update probe and fix rename/symlink parity. |
| Storage metrics device verification | `TODO.md`, `STORAGE_METRICS.md` | Host fixture exists; build/prune/rebuild/edit device sequence is not automated. | UI storage values can regress after real operations. | Add device capture lane and invariant check after each mutation. |

## P2 Items

| Area | Current incomplete state | Next action |
| --- | --- | --- |
| Mutation testing | Engine/corpus not implemented. | Add seed-fixed mutation corpus for path rewrite, storage accounting, and error handling. |
| Property-based testing | Generated API/Compose/Dockerfile/path cases are planned gaps. | Add deterministic generators and shrinking artifacts. |
| Upstream Docker differential runner | Golden corpus and version-pinned upstream comparison are absent. | Add fixed upstream Docker fixtures and classify accepted deviations. |
| Race/crash/recovery harness | Concurrent operation and kill/restart schedules are planned gaps. | Add seed-fixed race schedule and recovery trace artifacts. |
| Fault/security corpus | ENOSPC, EACCES, tar bomb, zip slip, path traversal, huge JSON, malicious archive payloads are planned gaps. | Add payload corpus and failure oracles. |
| Performance regression gate | Bench scripts exist, but p50/p95/p99 thresholds and prior-artifact deltas are not enforced. | Add thresholded performance gate for ptrace, COW, build, GPU, llama, UI logs, and storage scans. |
| SQLite/project metadata index | Disposable/rebuildable metadata DB remains a design item. | Define rebuild-from-files checks and replica/backup policy before using for authoritative overlay metadata. |

## Done-Looking Items That Need Care

| Item | Why it is not fully closed |
| --- | --- |
| Storage metrics fixture | Host fixture is done, but device refresh sequence is still open. |
| Tiny SDK28/full Android smoke | Some routes pass, but single `docker run --rm ubuntu:22.04 echo hi` remains planned-gap. |
| Default workspace verified | A successful device run exists, but ongoing truth reconciliation is still open. |
| llama healthcheck | Healthcheck exists, but it is not the same as current-container listener proof. |
| exec `-it` release note | Past pass evidence exists, but terminal regression remains open. |
| tracer cleanup | Tracee cleanup improved, but stop/process-tree/GPU executor teardown is still open. |

## Immediate Delegation Plan

1. **P0-A: service truth and runtime stop**
   - Implement listener/container-ID proof and process-tree teardown evidence.
   - Good for one worker because it spans Engine state, process table, and UI cards.
2. **P0-B: image pull and COW crash safety**
   - Implement atomic recovery tests and storage mutation fault injection.
   - Good for one worker with filesystem-only ownership.
3. **P0-C: llama GPU correctness**
   - Continue Q6_K blocker without touching llama.cpp/Dockerfile/model/prompt.
   - Keep local to the GPU stream or assign a specialized worker only to artifact analysis.
4. **P1-A: terminal and Compose parser**
   - Split terminal session-type refactor from Compose grammar work.
5. **P1-B: memory pager/UI productization**
   - Add artifact age/source, pdockerd memory telemetry API, and per-container memory breakdown.

## Management Rule

An item can move out of this audit only when it has:

1. an implementation change or an explicit scope decision,
2. an automated or replayable device/manual artifact,
3. a TODO/issue update,
4. no contradictory stale documentation claiming stronger support.

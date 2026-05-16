# Goal Execution Queue - 2026-05-13

This queue is the integration spine for parallel work.  It exists to keep
agent output, local edits, device evidence, and release messaging aligned
without mixing unrelated dirty lanes.

## Ground rules

- Do not modify llama.cpp, bundled model files, prompts, or library Dockerfiles
  unless a task explicitly says so.
- Do not claim a feature is complete without either runnable evidence or a
  `planned-gap` artifact that says what remains unproven.
- Keep GPU/runtime dirty-lane commits separate from documentation, test-ledger,
  and UI-contract commits.
- Device-only gates must fail safe: no fake success when ADB, service state, or
  evidence is unavailable.
- Service truth promotion is blocked unless listener/ports/log/UI card/state
  evidence all reduce to the same exact Engine container ID; otherwise the only
  allowed device result is planned-gap/Success: false, never success.
- Archive compatibility, terminal exec-it artifact verification, COW
  kill-at-step, OOM/LMK survival, and image live-pull interruption must stay
  non-promoting when they are host-only, planned-gap, skipped, or missing
  connected-device proof.
- Every adopted agent change needs a focused test command and a file list before
  commit.

## Active lanes

| Lane | Goal | Current owner | Commit unit | Acceptance gate |
|---|---|---:|---|---|
| P0-A service truth | UI cards, Engine API, persisted state, process table, listener, and logs agree on the same Engine container ID. | Goodall output awaiting integration | `service-truth artifact gate` | `bash -n scripts/android-device-smoke.sh`; `python3 scripts/verify-service-truth-plan.py`; service truth contract tests. |
| P0-B runtime teardown | Stop/kill/rm records process-tree cleanup evidence and never trusts HTTP 204 alone. | integrated T1 baseline | already committed `1c9558a` | Device artifact still planned-gap until real no-orphan evidence is captured. |
| P0-C terminal `exec -it` | UI route uses Engine exec/HTTP upgrade raw stream, not local shell/log path; regressions remain test-visible. | verifier represented; device proof pending | `terminal exec-it contract gate` | `python3 -m unittest tests.test_terminal_exec_it_contract tests.test_terminal_exec_it_artifact_verifier`; Android verifier promotes only with `ui-it-selftest-latest.json` plus `engine-exec-input-latest.jsonl` and `--require-container`. |
| P0-D OOM/LMK diagnostics | Large allocation guard, system pressure, RSS/PSS/swap/headroom, last progress, backend death/exit status, LMK classifier, UI memory artifact source/age/status, retention, and stale UI guard are recorded without fake success. | TODO/test audit sharpened; static gate integrated; device replay pending | `oom-lmk diagnostic contract` | `python3 scripts/verify-oom-lmk-survival-gate.py`; memory pager/UI contract tests; abnormal/stress JSON validators. Device plan artifacts stay non-promoting until controlled LMK/backend-death replay proves unsafe allocation denial, classifier evidence, and stale UI rejection on hardware. |
| P0-E image pull crash safety | Interrupted pull never publishes a partial image/layer as valid after restart. | synthetic residue runner integrated; live pull pending | `image-pull device scenario ledger` | `python3 scripts/verify-image-pull-crash-safety.py`; image pull crash-safety tests. The live registry interruption lane stays planned-gap/non-promoting until run against a scenario-owned fixture. |
| P0-F llama GPU correctness | GPU-backed llama response is correct before performance claims; CPU comparison is retained as evidence. | Bohr audit pending | separate GPU dirty-lane commit only | `tests.test_gpu_abi_contract`; GPU artifact verifier; device runbook evidence. |
| P0-G COW/archive kill-at-step | COW copy-up, rename, whiteout, metadata, and archive PUT fail closed across daemon/helper interruption. | device lane represented; promotion pending | `cow-overlay kill-at-step gate` | `python3 -m unittest tests.test_cow_overlay_kill_at_step_device`; `python3 scripts/verify-cow-overlay-bench-recovery.py --run-local`; adb/run-as artifact required for promotion. |
| P1-A archive API compatibility | Docker archive GET/PUT/HEAD and copy semantics stay compatible with lower/upper merge behavior. | host gate integrated | `archive api compat gate` | `python3 scripts/verify-archive-api-compat.py`; host regression only, not stable release credit without storage device gates. |

## Next integration order

1. Integrate non-GPU contract/test lanes first:
   service truth, terminal, OOM/LMK diagnostics, image pull crash-safety.
2. Run the lightweight combined gate:
   ```bash
   python3 -m unittest \
     tests.test_terminal_exec_it_contract \
     tests.test_memory_pager_contract \
     tests.test_memory_layer_ui_contract \
     tests.test_image_pull_crash_safety_verifier
   python3 -m pytest \
     tests/test_service_truth_ui_contract.py \
     tests/test_service_truth_artifact_contract.py -q
   bash -n scripts/android-device-smoke.sh
   python3 scripts/verify-service-truth-plan.py
   python3 scripts/verify-image-pull-crash-safety.py
   python3 scripts/verify-memory-pager-design.py
   ```
3. Commit only the files for those non-GPU lanes.
4. Rebase with autostash before pushing if the remote advanced.
5. Return to the GPU dirty lane with a clean list of changed native/runtime
   files and device artifacts.

## Device evidence waiting list

These gates are intentionally not complete until a real Android device artifact
is archived:

- service truth: `files/pdocker/diagnostics/service-truth-latest.json`
  - Same-container-ID proof must include `UICard`, `DockerPs`,
    `EngineApiContainersJson`, `PersistedStateJson`, `ProcessTable`,
    `ListenerProbe`, and `ContainerLogs`.
  - `ListenerProbe` must bind configured/listening ports through
    `listener-probe.json`, `listener-owner-map.json`, and `/proc/net/tcp` to the
    same selected process/container ID.
  - `ContainerLogs.CurrentServiceMarker` plus `logs-selected.out` must belong to
    that same Engine container ID.
  - UI card `TruthState: current` and persisted `state.json` comparison are
    required; stale/unknown UI or state-only card IDs keep the artifact
    planned-gap/Success: false.
- runtime teardown: `files/pdocker/diagnostics/runtime-teardown-latest.json`
- interrupted image pull: `docs/test/image-pull-crash-safety-latest.json`
- image live-pull interruption: planned
  `docs/test/image-pull-crash-safety-live-latest.json`; must use a
  scenario-owned/isolated fixture and remain non-promoting until implemented.
- OOM/LMK diagnostics: planned `pdocker.memory-oom-lmk-diagnostics.v1`
  - Required before promotion: large allocation guard decision with requested
    bytes/headroom/operation ID, last RSS/PSS/swap sample, last progress marker,
    backend pid/exit status or signal, classifier reason, memory ring/summary
    retention paths, and UI artifact source/age/status. Missing ADB, missing
    service evidence, missing ring data, or a planned-gap artifact is
    `success=false` and non-promoting; never synthesize a healthy/running card.
- Future mmap/userfault pager: planned non-promoting gate for explicit opt-in,
  kernel capability detection, unsupported mapping pass-through/`ENOMEM`, and
  unresolved-fault fail-closed diagnostics before any large-workload promotion.
- terminal exec-it: `docs/test/ui-it-selftest-latest.json` plus
  `docs/test/engine-exec-input-latest.jsonl`, verified with
  `scripts/verify-terminal-exec-it-artifact.py --require-container`.
- COW kill-at-step: `docs/test/cow-overlay-kill-at-step-latest.json`; planned
  gap or blocked-device artifacts do not promote.
- llama GPU correctness/performance: latest GPU compare artifact plus API prompt
  correctness sample.

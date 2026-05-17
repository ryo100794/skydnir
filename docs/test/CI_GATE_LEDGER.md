# P0/P1 CI Gate Ledger

Snapshot date: 2026-05-15.

This page is the single lightweight ledger for the current P0/P1 planned gaps.
It separates **fast/static gates** from **heavy or Android-device gates** and
keeps planned gaps visible as either verifier failures, non-passing artifacts
(`success=false` / `status=planned-gap`), or explicitly blocked evidence.

## Stable-checkpoint exclusion

Passing `host-smoke`, `release-honesty`, or any single device lane is not a
stable checkpoint while a row below is still `planned-gap`, `blocked`, or
missing promoted device evidence.  `tests/test_driver_manifest.json` mirrors
this rule with `stable_checkpoint_rule`, `non_promoting_statuses`, and
per-lane `stable_checkpoint_eligible=false` metadata.  A device-gated lane may
contribute evidence only after the artifact named in this ledger satisfies its
promotion condition; until then it is non-promoting evidence, even if the
driver command exits zero because it produced the expected planned-gap record.

Missing artifact rule: if the "Required artifact" column names an Android or
device-gated artifact and that artifact is absent from the checkpoint bundle,
the gate state is **missing evidence**, not pass. Missing evidence has the same
release effect as `planned-gap`/`blocked`: it cannot promote a stable
checkpoint, cannot be used to close the row, and must be called out in release
or checkpoint notes. A host/static verifier pass only proves that this ledger
and the planned-gap contract are coherent; it does not substitute for the
missing device artifact.

## Gate table

| Gate | Priority | Current visibility | Fast/static gate | Heavy / Android-device gate | Required artifact | Promotion condition |
|---|---:|---|---|---|---|---|
| Service truth: UI/service health must use the same Engine container ID as runtime state, listener owner, and logs. | P0 | **Unmet planned gap.** Static plan gate passes only if the acceptance contract exists; device smoke must write `Status: planned-gap`, `Success: false` until same-ID proof exists. | `python3 scripts/verify-service-truth-plan.py` | `bash scripts/android-device-smoke.sh --service-truth <default-workspace\|llama>` | `docs/test/service-truth-latest.json` | One proof reduces UI card, `/containers/json`, `state.json`, process tree, listener probe, and logs to the same Engine container ID. |
| Runtime teardown: stop/kill/rm must prove process-tree and executor cleanup, not only HTTP 204. | P0 | **Unmet planned gap.** Covered by the same static verifier; device smoke remains non-passing until teardown residue is disproved. | `python3 scripts/verify-service-truth-plan.py` | `bash scripts/android-device-smoke.sh --runtime-teardown <default-workspace\|llama>` | `docs/test/runtime-teardown-latest.json` | Stopped/removed container ID has no listener, no persisted running state, and no orphan `pdocker-direct` / service / GPU executor residue. |
| Image pull crash safety: killed pull must not publish partial tags or partial layers. | P0 | Static source gate passes; interrupted-pull device runner writes `status=planned-gap`, `success=false` without real kill/restart evidence. | `python3 scripts/verify-image-pull-crash-safety.py` | `python3 scripts/verify/runner/image_pull_crash_safety_device.py --execute-device --artifact docs/test/image-pull-crash-safety-latest.json` | `docs/test/image-pull-crash-safety-latest.json` | After daemon kill/restart, `.pull-*` / `.tmp-*` residue is rejected or pruned, old tag backup is preserved/restored, and `inspect`/`run` cannot use an unpublished interrupted tag. |
| OOM/LMK and large-workload diagnostics: memory pressure must be classified and persisted instead of disappearing as stale running state. | P0 | **Unmet planned gap for LMK replay.** Design/static verifier passes and the survival gate emits only non-passing planned-gap device evidence until a controlled backend-death/LMK replay exists. Device pager PoCs are separate evidence, not full LMK survival proof. | `python3 scripts/verify-memory-pager-design.py`, `python3 scripts/verify-oom-lmk-survival-gate.py`, and `python3 -m unittest tests.test_memory_pager_contract tests.test_service_truth_artifact_contract` | `bash scripts/android-memory-pager-managed-poc.sh`, `bash scripts/android-memory-pager-transparent-poc.sh`, and `python3 scripts/verify-oom-lmk-survival-gate.py --device-plan-artifact docs/test/oom-lmk-survival-latest.json` until future controlled LMK replay is implemented | `docs/test/apk-memory-pager-managed-latest.json`, `docs/test/apk-memory-pager-transparent-latest.json`, `docs/test/oom-lmk-survival-latest.json`, future promoted OOM/LMK diagnostic artifact | Persisted telemetry includes large allocation, RSS/PSS, pressure snapshot, progress marker, and `lmk_suspected` classification; backend death must not be masked by stale allocation summaries; UI must not show running from stale metadata alone. |
| COW/overlay external kill-at-step: copy-up, rename, metadata, whiteout, and hardlink-ring recovery must survive daemon/helper death at deterministic checkpoints. | P0 | **Unmet planned gap.** Host COW recovery covers local fail-closed cases, but external Android daemon/helper kill-at-step remains non-promoting until adb/run-as proof exists. | `python3 scripts/verify-cow-overlay-bench-recovery.py` and `python3 scripts/verify/runner/cow_overlay_kill_at_step_device.py --artifact docs/test/cow-overlay-kill-at-step-latest.json` | `python3 scripts/verify/runner/cow_overlay_kill_at_step_device.py --execute-device --artifact docs/test/cow-overlay-kill-at-step-latest.json` | `docs/test/cow-overlay-recovery-latest.json`, `docs/test/cow-overlay-kill-at-step-latest.json` | Every required case (`copy_up`, `rename`, `metadata`, `whiteout`, daemon hardlink-ring, helper hardlink-ring) has adb/run-as checkpoint, exact pid kill, restart/reconciliation, merged-view proof, residue proof, and hardlink-ring rebuild proof with `status=pass` / `success=true`; no planned-gap artifact promotes. |
| Terminal `-it`: Engine exec terminal must use TTY/stdin raw stream with resize and control-byte behavior. | P1 | Host unittest plus `scripts/verify-terminal-exec-it-artifact.py` reject fake UI success unless the paired device JSONL proves the Engine exec stream, input bytes, Ctrl-C, ArrowUp, `top`/`q`, resize, and matching container/exec ids; broader attach/detach and `docker run -t` parity are still heavy/device coverage. | `python3 -m unittest tests.test_terminal_exec_it_contract tests.test_terminal_exec_it_artifact_verifier` | `bash scripts/verify-heavy.sh --android-full --no-install` or focused UI self-test on installed APK followed by `python3 scripts/verify-terminal-exec-it-artifact.py ui-it-selftest-latest.json engine-exec-input-latest.jsonl --require-container` | Android smoke log / `docs/test/test-run-latest.json` plus `ui-it-selftest-latest.json` and `engine-exec-input-latest.jsonl` | Engine `exec -it` self-test shows tty, bash/sh interactive mode, CR/LF control, Ctrl-C, ArrowUp/history, `top` refresh and `q`, and resize without bracket-argv noise or planned-skip promotion. |
| llama GPU correctness: GPU offload cannot be claimed until Q6/Q4 correctness artifacts clear, and memory blockers remain explicit. | P1 | Current fast gate accepts a memory-blocker artifact only as blocked evidence; strict correctness gates fail unless Q6 workgroup and oracle match. | `python3 scripts/verify-llama-gpu-artifact.py docs/test/llama-gpu-workgroup3d-preflight-20260513.json --allow-memory-blocker`; strict: add `--require-q6-match` to fail until correctness is proven. | `python3 scripts/android-llama-gpu-q6k-run.py` or `bash scripts/android-llama-gpu-compare.sh` on target device | `docs/test/llama-gpu-q6k-workflow-latest.json`, `docs/test/llama-gpu-device-readiness-latest.json`, `docs/test/llama-gpu-workgroup3d-ngl1-latest.json` | Device artifact reports Q6 workgroup clear and oracle match; only then may correctness/performance claims be promoted beyond blocker/readiness status. |
| Build/test checkpoint honesty: release records must not present planned-gap, skipped, blocked, or device-unrun evidence as stable. | P0 | **Blocked for stable label.** The release-honesty lane verifies host publication hygiene and wording only; it is explicitly non-promoting until P0 device gates close or are scoped out. | `python3 scripts/verify-release-readiness.py`; `scripts/pdocker-test-driver.py --lane release-honesty` | Full release cut must include the promoted device artifacts for every in-scope P0 row above. | `docs/plan/RELEASE_READINESS.md`, `docs/test/CI_GATE_LEDGER.md`, `docs/test/test-run-latest.json` | Release notes/build records classify each row as passed, planned-gap, blocked, scoped-out experimental, or unsupported; no `status=planned-gap` / `success=false` artifact is counted as a stable checkpoint. |

## Lightweight lane mapping

`tests/test_driver_manifest.json` keeps the host lane lightweight by running
static/contract verifiers only. The P0/P1 entries represented in host smoke are:

- `verify-service-truth-plan` for service truth and runtime teardown planned-gap
  contracts.
- `verify-image-pull-crash-safety` for source-level pull atomicity and the
  non-passing interrupted-pull device artifact schema.
- `verify-memory-pager-design` for OOM/LMK diagnostic and memory-pager design
  contracts.
- `verify-oom-lmk-survival-gate` for host/static large-allocation telemetry,
  pdockerd backend-death classification, non-passing device artifact shape, and
  stable-checkpoint non-promotion rules.
- `verify-cow-overlay-bench-recovery` and `cow_overlay_kill_at_step_device.py`
  for COW/overlay local recovery plus non-promoting external daemon/helper
  kill-at-step device artifact shape.
- `unittest-all`, which includes terminal `-it`, service-truth artifact, memory
  pager, image-pull, and llama artifact unit contracts.
- `verify-llama-gpu-memory-blocker-artifact` for explicit llama GPU blocked
  evidence without claiming correctness.
- `release-honesty` is available as a separate driver lane for host-only
  release wording/payload hygiene. Its manifest metadata is
  `stable_checkpoint_eligible=false`; it cannot turn the planned-gap/device
  rows above into a stable checkpoint.

## Heavy / device policy

Heavy and Android-device gates must not silently pass when the device or runtime
evidence is absent. They should either produce a passing artifact with the proof
listed above, or a non-passing artifact with `status=planned-gap`, `blocked`, or
`failed` and `success=false`.

If a driver run contains any non-promoting status or only host-side planned-gap
contract checks, the release note may call it a regression/checkpoint run, but
not a stable release checkpoint.

When no real-device artifact was produced, write "missing device artifact" or
"planned gap" in the ledger/checkpoint summary instead of "pass". Do not infer
success from a zero-exit planning verifier, a schema placeholder, stale artifact
path, or unrun device lane.

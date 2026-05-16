# llama GPU Bridge Device Runbook

Date: 2026-05-13.

This runbook is the device-side checklist for continuing the llama.cpp GPU
bridge work without changing llama.cpp, the llama Dockerfile, the model, or the
prompt probes.

## Current Goal

Validate the Q6_K workgroup-shape fix on a real device and then continue from
the next blocker reported by the compare artifact.

The immediate acceptance signal for `ngl=1` is:

- `spirv_local_size_resolved` is `[32,2,1]` for the Q6_K event.
- `spirv_local_size_consistent` is `true`.
- `gpu.diagnostics.q6_workgroup_diagnostics.workgroup_shape_blocker` is
  `false`.
- If Q6_K still mismatches, the artifact includes
  `q6_shader_like_64_abs_delta` so the next split is descriptor/memory/math
  rather than collapsed local size.

## Do Not Do

- Do not modify llama.cpp.
- Do not rebuild the llama image just because the memory guard fires.
- Do not change the Dockerfile, model, or prompt probes for this validation.
- Do not force-stop Chrome, the browser, or the user's VS Code session from the
  automated route.
- Do not claim a speed result unless the correctness report passes.
- Do not accept compare/correctness/benchmark claims unless the expected GPU executor marker is observed in the artifact.
- Do not accept compare/correctness/benchmark claims when
  `gpu.diagnostics.config_propagation.summary` is `fail`.
- Do not accept compare/correctness/benchmark claims when any structured
  executor event reports `oracle_fail_closed: true`,
  `stage: "cpu-oracle-required"`, or an `*-oracle-pending` status.
- Do not accept compare/correctness/benchmark claims when any structured
  status/error/classification field still contains an unsupported or
  not-implemented GPU/oracle marker.
- Do not accept compare/correctness/benchmark claims unless the artifact proves
  the standard `/completion` prompt sanity check ran against the unchanged
  required prompt (`addition`, `2+3=`).
- Do not accept compare/correctness/benchmark claims unless speedup accounting
  fields are present, even when the CPU baseline is reused rather than freshly
  measured.
- Do not start or accept a GPU run while readiness is `false`.
- Do not allow a benchmark claim without a CPU comparison/baseline.

## Preflight

Use the current connected serial:

```bash
ANDROID_SERIAL=10.79.130.150:35389 adb devices
ANDROID_SERIAL=10.79.130.150:35389 adb shell 'cat /proc/meminfo | egrep "MemAvailable|SwapFree|SwapTotal"'
```

Or write a structured readiness artifact without starting pdockerd or stopping
any user-facing browser/VS Code process:

```bash
ANDROID_SERIAL=10.79.130.150:35389 \
bash scripts/android-llama-gpu-readiness.sh \
  --out docs/test/llama-gpu-device-readiness-latest.json
```

The default compare guard requires:

- `MemAvailable >= 512 MiB`
- `SwapFree >= 1024 MiB`

If `SwapFree` stays below 1024 MiB after stopping the llama container, wait for
Android reclaim or reboot the test device.  This is a device-memory blocker, not
a GPU correctness result.  A readiness artifact with `ready: false` or
`gpu_run_allowed: false` is a hard stop: do not launch the GPU compare/benchmark
and do not classify claims from a run that ignored that stop.

## Install Current APK

```bash
cd /root/tl/pdocker-android
python3 -m unittest tests.test_gpu_abi_contract
bash scripts/build-native-termux.sh
./gradlew :app:assembleCompatDebug
ANDROID_SERIAL=10.79.130.150:35389 \
  adb install -r app/build/outputs/apk/compat/debug/app-compat-debug.apk
```

## Q6_K Workgroup Validation Run

Preferred route: use the deterministic workflow wrapper.  It runs readiness,
local contract checks, readiness, guarded compare, artifact verification, and
writes one workflow manifest:

```bash
cd /root/tl/pdocker-android
ANDROID_SERIAL=10.79.130.150:35389 \
python3 scripts/android-llama-gpu-q6k-run.py \
  --manifest-out docs/test/llama-gpu-q6k-workflow-latest.json \
  --readiness-out docs/test/llama-gpu-device-readiness-latest.json \
  --out docs/test/llama-gpu-workgroup3d-ngl1-latest.json
```

Equivalent manual route, if individual phase control is needed.  Run with memory
waiting enabled so the command can sit safely instead of racing low-memory
Android state:

```bash
cd /root/tl/pdocker-android
ANDROID_SERIAL=10.79.130.150:35389 \
PDOCKER_LLAMA_WAIT_FOR_MEMORY_SEC=600 \
PDOCKER_GPU_CPU_ORACLE=1 \
PDOCKER_GPU_DISPATCH_PROFILE_RESPONSE=1 \
bash scripts/android-llama-gpu-compare.sh \
  --gpu-only \
  --cpu-tps 0.04702448956650603 \
  --gpu-ctx 512 \
  --gpu-layers 1 \
  --predict 4 \
  --repeat 1 \
  --out docs/test/llama-gpu-workgroup3d-ngl1-latest.json
```

## Result Triage

First classify the artifact with the repository verifier.  The verifier blocks
claiming success when readiness was false, when the expected executor build
marker was not observed, when requested GPU diagnostic environment variables
were not reflected by executor dispatch evidence, when an oracle fail-closed,
when unsupported/not-implemented GPU work appears in structured evidence, when
the `/completion` prompt sanity evidence is missing, when speedup fields are
missing, or when a benchmark claim lacks a CPU baseline:

```bash
python3 scripts/verify-llama-gpu-artifact.py \
  docs/test/llama-gpu-workgroup3d-ngl1-latest.json \
  --allow-memory-blocker
```

When the memory guard is no longer blocking and the goal is to confirm that the
Q6_K workgroup-shape fix held on device, use:

```bash
python3 scripts/verify-llama-gpu-artifact.py \
  docs/test/llama-gpu-workgroup3d-ngl1-latest.json \
  --require-q6-workgroup-clear
```

### Artifact Gate Decision Tree

Treat this as the pass/fail tree for the next real-device llama GPU attempt:

1. **Memory/readiness:** if `error` is `insufficient_memory` or
   `runtime_memory_pressure`, or readiness has `ready: false`, stop.  This is a
   device state blocker, not a GPU result.
2. **Executor freshness:** if
   `gpu.diagnostics.runtime_freshness.observed_executor_markers` does not
   contain the expected marker, fail the artifact as stale/missing executor
   evidence.
3. **Config propagation:** if `gpu.diagnostics.config_propagation.checks` is
   missing, incomplete relative to `scripts/llama-gpu-env-manifest.json`, or has
   `summary: "fail"`, fail the artifact before reading Q6_K results.
4. **Fail-closed oracle:** if any structured event has
   `oracle_fail_closed: true`, `stage`/`fail_stage: "cpu-oracle-required"`, or
   an `*-oracle-pending` status, fail the artifact.  Do not let a later served
   HTTP response, Q6 summary, or speedup hide this.
5. **Unsupported markers:** if any structured `status`, `latest_status`,
   `error`, `blocker_class`, `classification`, or
   `diagnostic_interpretation` contains `unsupported`, `not-implemented`, or
   `kernel-not-implemented-yet`, fail the artifact.
6. **Web/API prompt sanity:** the GPU `/completion` report must be present
   under `gpu.correctness`, use schema
   `pdocker.llama.correctness.v1.compare`, and include the unchanged required
   `addition` probe with prompt `2+3=`, expected prefix `5`, HTTP 2xx status,
   boolean `passed`, and string `content`.  A failed answer can still be useful
   diagnostic evidence, but missing or mutated prompt evidence fails the
   artifact.
7. **Speedup fields:** `comparison.speedup`,
   `comparison.target_tokens_per_second`, `comparison.target_met`, and the
   matching `bridge_overhead_phase` CPU/GPU/speedup/target fields must exist.
   They may be zero during failure triage, but missing fields fail the
   artifact.
8. **CPU baseline rule:** a fresh CPU run is optional during tight tuning when
   the command uses `--gpu-only --cpu-tps ...` or reuses a prior baseline.
   Without CPU baseline evidence, `correctness_claim_allowed` may be true but
   `benchmark_claim_allowed` must remain false.
9. **Q6_K gate:** only after the checks above pass, read
   `q6_workgroup_diagnostics` and follow the Q6_K sections below.

Verifier exit codes for these gates are stable for runbook use: 20 memory
blocker, 21 readiness blocked, 34 executor marker missing, 35 config
propagation mismatch, 36 unsupported GPU work accepted, 37 oracle fail-closed,
38 API prompt sanity missing, 39 speedup fields missing, 40 Q6 writable
writeback mismatch, 41 Q6 writable writeback unverified, 32 Q6 workgroup shape
blocker, 33 Q6 not reached/inconclusive.

### Memory Guard

If the artifact contains:

```json
{"error": "insufficient_memory"}
```

or:

```json
{"error": "runtime_memory_pressure"}
```

then do not interpret the run as GPU pass/fail.  Follow the `device_actions`
array in the JSON and rerun after memory recovers.  The same rule applies to a
readiness report or embedded readiness object with `ready: false`.

### Executor Marker / Benchmark Claim Guards

A compare artifact must include the expected executor build marker under
`gpu.diagnostics.runtime_freshness.observed_executor_markers`.  ICD markers are
useful supporting evidence, but they are not a substitute for executor evidence
when making compare, correctness, or benchmark claims.

### Environment Propagation Guard

Before interpreting Q6_K blocker evidence, inspect
`gpu.diagnostics.config_propagation`.  If its `summary` is `fail`, or any check
has `status` equal to `missing-evidence` or `mismatch`, the next action is to
fix option transport across compare launch, pdockerd `_gpu_env(state)`, and
executor dispatch reporting.  Do not infer that a Q6_K safe-kernel,
strict-passthrough, specialization, descriptor-transfer, or subgroup
experiment failed until the requested environment values are visible in the
artifact.

A benchmark claim additionally requires:

- GPU correctness claim is allowed;
- `comparison.speedup`, `comparison.target_tokens_per_second`,
  `comparison.target_met`, and the `bridge_overhead_phase` speedup fields are
  present;
- CPU comparison/baseline evidence is present in the artifact.

If CPU comparison is missing, keep the result as diagnostic only.

### Q6_K Workgroup Still Broken

If:

```json
"workgroup_shape_blocker": true
```

then the next code fix remains local-size propagation/materialization.

### Q6_K Workgroup Cleared But Oracle Mismatches

If:

```json
"workgroup_shape_blocker": false,
"latest_status": "mismatch"
```

then the local-size hypothesis is cleared.  Continue with descriptor identity,
memory residency/staging/writeback, synchronization/device-execution, or Q6_K
arithmetic/reduction interpretation.  The next artifact should narrow one of
those classes rather than merely restating that the sampled oracle mismatches.

### Q6_K Matches

If:

```json
"latest_status": "match"
```

then run the same command with `--gpu-layers 2`.  Keep `ngl=1` as the rollback
baseline until `ngl=2` also has correctness evidence.

## Evidence To Preserve

For each device run, keep:

- the JSON artifact under `docs/test/`;
- `git log --oneline -5`;
- `git status --short`;
- APK build result;
- unit-test result;
- whether Chrome/browser/VS Code was left untouched.

The compare script also copies the artifact to:

```text
files/pdocker/bench/
```

inside the app data area when the run reaches the final reporting phase.

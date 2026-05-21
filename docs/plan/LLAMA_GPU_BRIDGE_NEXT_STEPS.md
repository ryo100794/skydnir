# llama.cpp GPU Bridge Next Steps

Snapshot date: 2026-05-20.

This document is the handoff plan for continuing the llama.cpp GPU bridge work
with a smaller or faster coding model.  It assumes the repository is on or
after commit `9cccc81` for the GPU workgroup diagnostics lane, plus any later
non-GPU service-truth or terminal commits, and that llama.cpp itself remains
unmodified.

## Current Ground Truth

The current implementation is a pdocker-owned glibc Vulkan ICD bridge plus an
APK-owned Android Vulkan executor.  The container still owns llama.cpp model
loading, graph construction, sampling, and HTTP serving.  The bridge only
lowers selected Vulkan buffer/descriptor/dispatch work to Android Vulkan.

Confirmed facts:

| Area | Current result | Evidence |
|---|---|---|
| `ngl=0` default route | Required correctness passes | `docs/test/llama-gpu-default-oracle-match-ngl0-20260509.json` |
| unsafe SPIR-V materialization | Disabled by default | commit `02619fd` |
| zero-layer small multiply shader | CPU oracle matches default non-materialized hash | `0x11d5243c43b23a7b`, `mismatch_count=0` |
| `ngl=1` small add shader | CPU oracle matches | `0x11c0523df6c795b8`, `mismatch_count=0` |
| `ngl=1` RoPE/Yarn shader | CPU oracle executes and matches | `0xac41e8033a67af4a`, `docs/test/llama-gpu-ngl1-rope-yarn-oracle-20260509.json` |
| `ngl=1` RMSNorm shader | CPU oracle executes and matches | `0xf2f988b94bd3e0dc`, `docs/test/llama-gpu-ngl1-rms-norm-oracle-20260509.json` |
| `ngl=1` Q6_K/final-projection shader | Row-indexed writeback verified; workgroup shape and native reduction sum clear; final output still mismatches | `docs/test/llama-gpu-ngl1-q6-row-provenance-20260519.json`, `blocker_class=native-q6-device-execution-or-final-store` |
| current device readiness | Heavy compare is memory-gated | readiness requires sufficient `MemAvailable`; low Android zram `SwapFree` is advisory unless a strict swap gate is explicitly configured |
| 2026-05-20 Q6_K workflow | Device workflow reaches the known Q6_K blocker again; create-timeout race is no longer the blocker | `docs/test/llama-gpu-q6k-adb41503-20260520T110352Z.json` (ignored runtime evidence), workflow `classification=q6-native-device-execution-or-final-store` |

Do not claim GPU inference correctness or performance for `ngl>=1` from served
HTTP alone.  The latest strict row-provenance artifact still fails required
correctness and has `benchmark_claim_allowed=false`; Q6_K is narrowed to
`native-q6-device-execution-or-final-store` after row-indexed writeback,
workgroup shape, and a native reduction/shader-like sum were cleared.  The
memory readiness gate is still required before heavy compare or benchmark evidence can
promote anything.

## Non-Negotiable Rules

- Do not modify llama.cpp.
- Do not rebuild the llama image unless the user explicitly allows it.
- Do not add external libraries or copied upstream code without explicit user
  approval.
- Keep Android vendor GPU libraries behind the APK/executor boundary.  Do not
  bind Bionic vendor libraries directly into the glibc image as a product path.
- Benchmark claims require a passing correctness report.  Speed without
  correctness is diagnostic only.
- Commit only focused changes and their directly relevant evidence artifacts.

## Canonical Commands

Use the connected device serial from the user when it changes.  ADB is not a
persistent assumption: if the user says ADB is off, continue host-only checks
and wait for a fresh endpoint before running device readiness or compare jobs.
The latest observed device endpoints are historical evidence only.

Fast local checks:

```bash
cd /root/tl/pdocker-android
python3 -m unittest tests.test_gpu_abi_contract tests.test_llama_gpu_artifact_verifier
python3 -m unittest tests.test_llama_gpu_q6k_workflow
python3 scripts/maintenance/summarize-llama-gpu-artifacts.py \
  --snapshot-date 2026-05-19 \
  --out docs/test/llama-gpu-artifact-sweep-latest.json
bash scripts/build-native-android-ndk.sh
./gradlew :app:assembleCompatDebug
```

The artifact sweep is a local inventory step.  It applies the current
`scripts/verify-llama-gpu-artifact.py` classifier to every
`docs/test/llama-gpu-*.json` file and records the latest blocker distribution,
including row-indexed Q6_K writeback readiness, without touching llama.cpp,
Dockerfiles, models, prompts, or the device.

Install the compat APK:

```bash
ANDROID_SERIAL=192.168.179.26:45443 \
adb install -r app/build/outputs/apk/compat/debug/app-compat-debug.apk

ANDROID_SERIAL=192.168.179.26:45443 \
adb shell am start \
  -n io.github.ryo100794.pdocker.compat/io.github.ryo100794.pdocker.MainActivity
```

Run the tight llama GPU compare loop:

```bash
ANDROID_SERIAL=192.168.179.26:45443 \
bash scripts/android-llama-gpu-readiness.sh \
  --out docs/test/llama-gpu-device-readiness-latest.json

ANDROID_SERIAL=192.168.179.26:45443 \
PDOCKER_GPU_CPU_ORACLE=1 \
PDOCKER_GPU_DISPATCH_PROFILE_LOG=1 \
PDOCKER_GPU_DISPATCH_PROFILE_RESPONSE=1 \
bash scripts/android-llama-gpu-compare.sh \
  --gpu-only \
  --cpu-tps 0.04702448956650603 \
  --cpu-ctx 512 \
  --gpu-ctx 512 \
  --gpu-layers 1 \
  --predict 4 \
  --repeat 1 \
  --out docs/test/llama-gpu-ngl1-<short-name>-20260509.json
```

Do not start the `ngl=1` Q6_K evidence run unless the readiness artifact has
`ready=true` and `preconditions.q6_ngl1_evidence_collection_allowed=true`.
The compare script also writes `gpu.runtime_env_manifest` into the artifact and
echoes manifest-selected runtime environment variables before collection; keep
that record with the Q6_K evidence so env propagation can be audited without
changing llama.cpp, the image, models, or prompts.
If the run stops before Q6_K, the artifact verifier now preserves bounded
`pre_http_failure_evidence` for the first failed generic SPIR-V event
(`fail_stage`/`error`, `vk_result`, SPIR-V hash, pipeline key, feature
requirements, Android feature bits, and `q6_reachability`). Treat that as a
pre-Q6 setup blocker, not as a Q6 correctness result.

2026-05-18 update: the ICD/runtime freshness marker for this lane is now
`vulkan-icd-feature-chain-marker-20260518`.  Re-run device artifacts after
installing an APK with that marker before accepting any new pre-Q6 conclusion.
The ICD now keeps the requested-feature mask tied to the full Vulkan
`VkDeviceCreateInfo`/`VkPhysicalDeviceFeatures2` pNext chain and advertises the
8-bit storage, shader-float16-int8, and storage-buffer-storage-class extension
surface consistently with the feature bits it exposes.  If a pre-Q6
`VK_ERROR_FEATURE_NOT_PRESENT` remains, compare `spirv_required_feature_mask`,
`spirv_requested_feature_missing_mask`, `android_vulkan_features`, and
`android_vulkan_enabled_features` first; do not jump to Q6_K oracle work until
those fields prove the bridge setup is coherent.

2026-05-18 follow-up: commit `5e5f0c7` hardens the ICD pNext traversal used by
that feature-chain path.  The previous generic `VkBaseInStructure` view can
miss nested feature structs under optimized C builds, so the ICD now copies the
header fields before dispatching to concrete Vulkan structs.  Keep
`tests.test_vulkan_icd_feature_chain` in the fast gate; it compiles a tiny
`-O2` C harness and catches regressions where `VkPhysicalDeviceFeatures2 ->
VkPhysicalDeviceVulkan11Features -> VkPhysicalDeviceVulkan12Features` collapses
back to the base feature mask only.

2026-05-18 verifier gate: commit `cdd5f3f` also prevents a stale ICD artifact
from being promoted into a new pre-Q6 conclusion.  When the compare artifact
declares an `expected_icd_marker`, `scripts/verify-llama-gpu-artifact.py`
requires that marker in `observed_icd_markers` before classifying generic
SPIR-V pipeline failures.  If this trips, reinstall the freshly built compat
APK and rerun the same compare; do not infer feature-chain or Q6_K state from
the stale artifact.

2026-05-18 compare hardening: the compare artifact now marks runtime freshness
as `pass` only when both requested runtime markers are observed, and pre-Q6
generic SPIR-V evidence is anchored to the first failed event rather than a
later cleanup or follow-on failure.  Fresh feature-chain ICD artifacts also
fail closed as `vulkan-pipeline-feature-evidence-missing` if a
`VK_ERROR_FEATURE_NOT_PRESENT` blocker lacks required/requested feature masks
or Android enabled-feature evidence.  This keeps the next device run from
turning incomplete setup evidence into a false Q6_K conclusion.

2026-05-19 workflow hardening: `scripts/android-llama-gpu-q6k-run.py` now
persists the verifier stdout next to the workflow manifest as
`*.verifier.stdout` and extracts JSON classification from the full output, not
from the 8 KiB `stdout_tail`.  This prevents long verifier diagnostics from
silently dropping `classification`/`next_action` in
`docs/test/llama-gpu-q6k-workflow-latest.json`.

2026-05-20 device-run hardening: the compare script now treats
`POST /containers/create` as a heavier Engine operation than start/inspect.  A
host-side create timeout no longer immediately becomes a false GPU failure:
the script polls the named container until a delayed create becomes inspectable,
waits for stale targets to disappear before recreating them, and retries
late-created target cleanup on failure.  The first retest on
`192.168.179.26:41503` created and started `3d02cf0782c5`
(`/pdocker-llama-cpp`) and the verifier returned the previous real blocker,
`q6-native-device-execution-or-final-store`; the HTTP server became healthy
after the compare wait window, but a `2+3=` completion probe still timed out.
Treat this as runtime/startup latency plus the existing Q6_K correctness
blocker, not as proof of correct or fast GPU inference.

2026-05-20 llama call-site correlation: the current pre-Q6 pipeline failure
`0xf3cd7d18f0276b42` was matched against upstream llama.cpp sources without
changing llama.cpp.  It is `ggml-vulkan.cpp` creating
`mul_mat_vec_q4_k_f32_f32` from `vulkan-shaders/mul_mat_vec_q4_k.comp` with
`vk_mat_vec_push_constants`, five descriptor buffers
`A/B/D/Fuse0/Fuse1`, and specialization constants
`{ BLOCK_SIZE=32, NUM_ROWS=2, NUM_COLS=1/2 }`.  The shader deliberately
declares three typed views of binding 0 for the same Q4_K block
(`block_q4_K`, `block_q4_K_packed16`, `block_q4_K_packed32`); this is the
llama.cpp Q4_K ABI, not a Q5/Q6 dispatch mix-up.  The pdocker-side
diagnostic classifier now recognizes the original hash, the Float16-capability
insertion hash `0x853c49b4900eed3c`, and the duplicate-descriptor-materialized
hash `0x22ab0152b230e983` as Q4_K matvec variants.  `PDOCKER_GPU_Q4K_SAFE_KERNEL`
remains an explicit diagnostic override and is available under strict
passthrough for isolating driver compilation rejection from descriptor/call-site
ABI correctness; it is not a benchmarkable product optimization.
Fresh APK/device evidence for this lane must show executor marker
`gpu-executor-llama-q4k-callsite-20260520`.

Milestone compare with CPU baseline should be run only after a correctness
blocker changes, not after every small diagnostic edit.

## Stage Plan And Acceptance Criteria

### Stage 1: Keep the known-good `ngl=0` boundary green

Purpose: make sure the bridge did not regress while working on `ngl=1`.

Procedure:

1. Run the tight compare with `--gpu-layers 0`.
2. Inspect `gpu.correctness.summary`.
3. Inspect the first `small-f32-indexing` oracle events.

Pass criteria:

- `gpu.correctness.summary.correctness == "pass"`.
- `gpu.correctness.summary.required_failures == 0`.
- `benchmark_claim_allowed == true`.
- For `0x11d5243c43b23a7b`, `cpu_oracle.status == "match"`.
- For the matching oracle events, `mismatch_count == 0`.
- The event reports `materialize_specialization == false`.

Fail criteria:

- Required correctness fails.
- `PDOCKER_GPU_MATERIALIZE_SPIRV_SPECIALIZATION_CONSTANTS` is accidentally
  defaulting back to true.
- A known small shader hash becomes unsupported or mismatching.

If this fails, stop `ngl=1` work and fix the regression first.

### Stage 2: Classify each `ngl=1` front-blocker shader

Purpose: determine which shader first explains the wrong first token.

Current `ngl=1` front-blocker candidates:

| Hash | Current classification | Current status |
|---|---|---|
| `0xac41e8033a67af4a` | RoPE/Yarn | completed; oracle matches in `docs/test/llama-gpu-ngl1-rope-yarn-oracle-20260509.json` |
| `0xf2f988b94bd3e0dc` | RMSNorm with optional multiply | oracle matches in `docs/test/llama-gpu-ngl1-rms-norm-oracle-20260509.json` |
| `0x274f68a67dfef210` | `mul_mat_vec_q6_k`-like large quantized matvec / final projection | row-indexed writeback verified; current blocker `native-q6-device-execution-or-final-store` |

Procedure:

1. For each candidate, inspect SPIR-V assembly dumped under the llama workspace
   logs, or pull the `.spv` file from the device and run `spirv-dis`.
2. Identify:
   - descriptor binding read/write roles,
   - push constant indices used,
   - specialization constants used,
   - arithmetic operation family,
   - dispatch geometry and local size,
   - output binding index.
3. Add only a hash-gated debug oracle when the operation is small enough to
   emulate safely inside `pdocker_gpu_executor.c`.
4. Record `cpu_oracle.status`, `compared_floats`, `mismatch_count`,
   first mismatch, and sample values.

Pass criteria for a shader:

- The shader has a stable classification in
  `docs/test/LLAMA_GPU_CORRECTNESS_20260507.md`.
- The oracle either:
  - executes and reports `status == "match"` with `mismatch_count == 0`, or
  - executes and reports a precise mismatch with first-mismatch samples, or
  - is explicitly marked too large/unsafe with a documented reason.
- Unsupported hashes are not silently ignored if they are present in the latest
  `ngl=1` correctness-failing run.

Fail criteria:

- A hash is called "fixed" without oracle evidence or a correctness run.
- The oracle reads or writes large buffers without a cap.
- The oracle mutates container buffers; oracle code must remain diagnostic-only.

### Stage 3: RoPE/Yarn oracle for `0xac41e8033a67af4a` (completed)

Purpose: clear the small, deterministic RoPE/Yarn transform before attacking
large final-projection/matmul-like work.

Completed procedure:

1. Use the existing dumped SPIR-V assembly for the hash.
2. Implement a hash-gated CPU oracle only for the exact observed descriptor and
   push layout.
3. Keep memory caps small; this shader's captured binding footprint is under
   about 400 KiB in the zero-layer control.
4. Compare after Vulkan fence and before writeback, same as existing CPU
   oracles.

Evidence-backed pass criteria:

- `cpu_oracle.kernel_hint == "rope-yarn"`.
- `executed == true`.
- `compared_floats > 0`.
- `mismatch_count == 0`.
- `docs/test/llama-gpu-ngl1-rope-yarn-oracle-20260509.json` records
  `compared_floats=4096` and `status=match`.
- If this ever regresses, the first mismatch must include source sample,
  expected value, GPU value, and absolute error.

Regression fail criteria:

- The oracle assumes a different binding order than `spirv_binding_reflection`
  reports.
- The oracle's push constant interpretation is not checked against SPIR-V
  access.
- The run omits `PDOCKER_GPU_CPU_ORACLE=1` but is used as oracle evidence.
- The hash disappears from `cpu_oracle_known_llama_hash()` or no longer maps to
  `kernel_hint == "rope-yarn"`.

### Stage 4: Large candidate split for `0x274f68a67dfef210`

Purpose: decide whether the remaining correctness failure is final-projection,
quantized matmul, descriptor aliasing, or writeback/residency.

Current entry condition: Stage 3 is complete for the observed `ngl=1` run.
Both `0xac41e8033a67af4a` (`rope-yarn`) and `0xf2f988b94bd3e0dc`
(`rms-norm`) execute bounded CPU oracles and report `mismatch_count == 0` in
`docs/test/llama-gpu-ngl1-rms-norm-oracle-20260509.json`.  The model-level
correctness probe still fails, so `0x274f68a67dfef210` is now the next primary
blocker.

Current blocker statement: keep Q6_K strict passthrough as the fidelity
baseline.  The next fix must explain the
`native-q6-device-execution-or-final-store` blocker for
`0x274f68a67dfef210` without changing llama.cpp, the Dockerfile, the model, or
the prompts.  Workgroup shape and row-indexed writeback are currently clear;
focus on executor/Vulkan device execution, also recorded as
`Vulkan device-execution`, versus final output store before any
performance claim.

Procedure:

1. Do not start with a full CPU oracle for the 510 MiB input range.
2. First add metadata classification:
   - descriptor sizes,
   - descriptor aliases,
   - storage format clues from SPIR-V,
   - output binding sample hash before/after,
   - whether output and read-only bindings overlap.
   The current shader dump matches llama.cpp's `mul_mat_vec_q6_k` family:
   it declares multiple binding-0 views for the same quantized weight buffer,
   uses storage8/storage16/int8 features, and specializes
   `BLOCK_SIZE=32`, `NUM_ROWS=2`, `NUM_COLS=1`.
   The compact executor event must also include bounded `push_u32` values so a
   sampled oracle can reproduce row/stride coordinates without copying the
   large weight buffer.
3. Add a sample-window oracle only if a bounded subset can be proven correct.
   This is now implemented for the observed Q6_K layout: it reads only eight
   output rows, `8 * 16 * 210` weight bytes, and the 16 KiB vector input.
4. Compare the sampled output values with CPU/no-offload logits if available.

Pass criteria:

- A clear blocker class is recorded:
  - descriptor alias/rewrite bug,
  - quantized storage decode mismatch,
  - push/specialization interpretation mismatch,
  - copy/upload/writeback/residency bug,
  - or Android Vulkan execution mismatch.
- Any oracle for this hash is bounded by memory and time caps.
- The output includes enough sample coordinates to reproduce the mismatch.
- Current evidence `llama-gpu-ngl1-q6k-sample-oracle-20260509.json` reports a
  bounded oracle mismatch for all eight sampled rows. This shifts the next
  split from "unknown large shader" to "Q6_K decode/math vs descriptor-view
  semantics/local-size execution".
- The no-duplicate-rewrite rerun changes the rewritten shader hash from
  `0x274f68a67dfef210` to `0x1bf751845c5dce75`, but the sampled Q6_K oracle
  still mismatches the same first row. Do not spend the next iteration only on
  duplicate descriptor rewrite; split local-size/specialization execution,
  Q6_K decode layout, and descriptor-view semantics instead.
- The literal-local-size patch changes the active hash to
  `0x09c4622d92c6acb9` and records `spirv_local_size=[32,1,1]`, but the sampled
  oracle still mismatches. Treat local-size patching as a necessary compatibility
  hardening step, not as the current root cause. The next most valuable split is
  a dequant-only check for the same Q6_K blocks before reduction.
- The first decode-variant check rules out the obvious high-bit, signed-scale,
  and zero-point mistakes: none produces the GPU's row-0 value. Continue with a
  descriptor-view/reduction split: verify the byte view and packed16 view
  produce identical per-lane inputs, then inspect whether the shared-memory
  reduction writes the same full sum that the sampled oracle computes.
- The byte-view vs packed16-view Q6_K split has now been executed in
  `llama-gpu-ngl1-q6k-packed16-view-20260509.json`. The packed16-view oracle
  gives the same row-0 sum as the canonical byte view (`abs_delta=0`), while the
  GPU output remains `6.83085108`. This means the Vulkan bridge should not add a
  data-structure conversion for Q6_K blocks. The next split should stay at the
  API/dispatch boundary: descriptor effective range/offset, buffer aliasing,
  specialization-local-size execution, and shared-memory reduction.
- The first 32-lane reduction split is recorded in
  `llama-gpu-ngl1-q6k-partial-lanes-fixed-20260509.json`. Row 0's half-full
  value (`6.93901168`) is close to but not equal to the GPU value
  (`6.83085108`), and the sampled rows do not follow a stable half-reduction
  pattern. Continue by expanding the oracle from sparse sampled rows to a small
  contiguous row window, then compare GPU output indices against expected row
  sums and half/subgroup sums to detect output-layout or workgroup-row mapping
  mistakes.
- The contiguous window is now recorded in
  `llama-gpu-ngl1-q6k-row-window-20260509.json`. All 32 rows still mismatch.
  Some GPU values are close to half sums from nearby rows, but no stable mapping
  emerges. Next, inspect the Q6_K SPIR-V index arithmetic directly: derive the
  exact output index expression from `GlobalInvocationId`, specialization
  constants, and push constants, then update the oracle to follow that mapping
  instead of assuming `dst[row]`.
- The shader-like oracle in
  `llama-gpu-ngl1-q6k-shader-like-oracle-20260509.json` follows the source
  shader's packed 32-bit loads and scale-cache accumulation and still matches
  the canonical oracle within `4.16e-7`. Do not add a data conversion layer.
- The duplicate Binding 0 materialization probe in
  `llama-gpu-ngl1-q6k-materialized-alias-icd-20260509.json` confirms the option
  is propagated through the container ICD and executor, but output is unchanged.
  Same-buffer aliasing is therefore not the sole failure. Next probes should
  reduce the shader execution model itself: specialize/materialize constants
  more completely, then force/disable shared-memory reduction variants or
  emulate the Q6_K shader as a bridge-owned kernel for this hash.
- If a new artifact reports `config_propagation.summary == "fail"`, stop Q6_K
  diagnosis and fix environment propagation first.  A missing diagnostic knob
  can invalidate every Q6_K split, including safe-kernel, strict-passthrough,
  specialization, descriptor-transfer, and subgroup experiments.
- The next Q6_K action after environment propagation is trusted is to preserve
  strict passthrough and collect a workgroup-cleared artifact that names one
  precise blocker class: descriptor effective range/offset, memory
  residency/staging/writeback, synchronization/device-execution, or Q6_K
  arithmetic/reduction.  Do not treat another sampled mismatch as progress
  unless it narrows one of those classes.
- As of 2026-05-15, the compare summarizer records that narrowed class in
  `gpu.diagnostics.q6_workgroup_diagnostics.blocker_class`, plus bounded Q6_K
  evidence (`q6_first_mismatch`, writable output binding hashes, read-only
  upload/dispatch hash mismatches, and whether the shader-like 32/64-lane CPU
  oracle matched the canonical sum).  The artifact verifier now blocks
  correctness and benchmark claims unless Q6_K workgroup shape is clear *and*
  the Q6_K oracle reports `latest_status == "match"`.
- The Q6_K oracle also now decodes the observed push layout for accumulator
  mask (`push_u32[7]`), base workgroup/batch offset (`push_u32[8]`), derived
  output base, derived weight-row block base, and optional accumulator bindings
  3/4.  A nonzero accumulator mask with missing/unreadable accumulator inputs is
  a fail-closed oracle blocker, not a generic arithmetic mismatch.
- The next host-side diagnostic split now records writable-binding writeback
  hash evidence.  Executor binding details include `writeback_verified` and
  `writeback_mismatch`; the compare summary includes
  `q6_writable_writeback_mismatches`, `q6_writable_writeback_unknown`, and
  `q6_writeback_verified_all`.  A strict-passthrough artifact can now narrow the
  previous `vulkan-device-execution-or-writeback` class to `writeback` when the
  fd hash disagrees with the post-dispatch GPU/staging hash, or to
  `vulkan-device-execution` when shader-like Q6 arithmetic is cleared and all
  writable writebacks are hash-verified.
- The verifier now treats a Q6_K oracle match as insufficient unless writable
  output writeback is hash-verified.  `latest_status == "match"` with
  `q6_writable_writeback_mismatches` fails closed as `q6-writeback-mismatch`;
  missing/unknown writable writeback evidence fails closed as
  `q6-writeback-unverified`.  This prevents a pre-writeback oracle match from
  being promoted into a correctness claim when the container-visible fd boundary
  has not been proven.
- The bounded native Q6_K reduction/output-layout probes have now run through
  `docs/test/llama-gpu-ngl1-q6-row-provenance-20260519.json`. Row-indexed
  writeback is verified, workgroup shape is clear, and the native reduction /
  shader-like sum clears, but final output still mismatches. The artifact
  rejects a stable fixed output-layout offset and row-provenance explanation.
  Current blocker: `native-q6-device-execution-or-final-store`; next work should
  narrow executor/Vulkan device execution versus final output store, not
  recollect a generic row-indexed artifact.

#### Row-indexed Q6_K device-run decision tree

For strict `ngl=1` device artifacts with row-indexed Q6_K writeback evidence,
decide the C-side blocker in this order. The latest row-provenance artifact has
already landed past the generic row-indexed gate; use this tree for regressions
or reruns, not as a request to collect another generic row-indexed artifact.

1. **If memory-blocked**: if the artifact reports `insufficient_memory`,
   `runtime_memory_pressure`, `device_memory_blocked:true`, or a runtime abort
   before the Q6_K dispatch, stop Q6 diagnosis.  This is not Q6 evidence and it
   does not justify a C-side Q6 change.  Free Android memory without killing the
   user's browser/VS Code session, keep the same APK/image/prompts, and rerun
   the same compare command.
2. **If row-indexed writeback is absent or differs**: if
   `q6_row_indexed_writeback_evidence` is empty, `q6_row_indexed_writeback_verified`
   is not true, `q6_writeback_verified_all` is not true, or any
   `f32_after_dispatch` / `f32_after_writeback` value differs at the
   `q6_row_indexed_sample_indices`, classify the next blocker as `writeback`.
   Fix only writable-output staging/cache/download/fd propagation before
   revisiting shader math.
3. **If writeback is verified + the Q6 oracle still mismatches**: require
   `q6_writeback_verified_all == true`,
   `q6_row_indexed_writeback_verified == true`, non-empty
   `q6_row_indexed_writeback_evidence`, and `latest_status == "mismatch"`.
   Then use the existing sub-classifier instead of treating "another mismatch"
   as progress:
   - If `workgroup_shape_blocker == true`, `spirv_local_size_consistent` is not
     true, or `spirv_local_size_resolved` is not `[32,2,1]` for the Q6_K event,
     the next C-side blocker is **workgroup-shape**: fix local-size
     propagation/materialization and strict refusal semantics.
   - If workgroup shape is clear, read-only upload/dispatch hashes are clean,
     and `q6_shader_like_64_abs_delta` / shader-like diagnostics clear the
     CPU-side Q6 arithmetic, the next C-side blocker is **Vulkan
     device-execution**: inspect barriers, queue submission, device-local
     staging, and host/device visibility, not the Q6 decode.
   - If workgroup shape and writeback are clear but the shader-like oracle does
     not clear the math, the next C-side blocker is
     **Q6 arithmetic/reduction/output-layout**: inspect the native Q6 SPIR-V
     reduction, lane mapping, accumulator mask/base-workgroup handling, and
     output index expression.  Do not add a Q6 block data conversion layer or
     rebuild llama.cpp unless a bounded artifact proves that exact need.
4. **If writeback is verified + the Q6 oracle matches**: only then may the run
   advance out of this blocker, and only if the normal prompt correctness,
   runtime freshness, config propagation, and speedup fields also pass.

Fail criteria:

- Eagerly reading hundreds of MiB into a diagnostic oracle.
- Treating speed as useful while the required correctness probe fails.
- Hiding a mismatch by lowering `n_predict`, changing prompt probes, or
  rebuilding llama.cpp.

## UI/compose runtime defaults and compare-only diagnostics

Environment propagation has caused repeated false trails, so the current rule
is explicit rather than implicit:

- UI/compose runtime defaults in `docker-proot-setup/bin/pdockerd` must carry
  production-safe Vulkan limits and Q6_K toggles that containers need at normal
  startup, including `PDOCKER_VULKAN_DISABLE_8BIT_STORAGE`,
  `PDOCKER_GPU_REWRITE_DUPLICATE_DESCRIPTOR_BINDINGS`,
  `PDOCKER_GPU_RESIDENT_CACHE`, `PDOCKER_GPU_RESIDENT_CACHE_MIN_BYTES`,
  `PDOCKER_GPU_Q6K_ORACLE_WRITEBACK`, `PDOCKER_GPU_Q6K_SAFE_KERNEL`,
  `PDOCKER_GPU_DISABLE_PIPELINE_OPTIMIZATION`,
  `PDOCKER_VULKAN_HEAP_BYTES`, `PDOCKER_VULKAN_MAX_BUFFER_BYTES`,
  `GGML_VK_FORCE_MAX_BUFFER_SIZE`, `GGML_VK_FORCE_MAX_ALLOCATION_SIZE`, and
  `GGML_VK_SUBALLOCATION_BLOCK_SIZE`.
- The compare driver must additionally forward diagnostic knobs that are too
  experimental or noisy to force into all UI/compose launches:
  `PDOCKER_GPU_CPU_ORACLE`, `PDOCKER_GPU_STRICT_PASSTHROUGH`,
  `PDOCKER_GPU_STRICT_RECONCILIATION`,
  `PDOCKER_GPU_STRICT_DEVICE_LOCAL_STAGING`,
  `PDOCKER_GPU_LEGALIZE_WORKGROUP_SIZE_FROM_SPEC`,
  `PDOCKER_GPU_RETRY_MATERIALIZE_SPECIALIZATION`,
  `PDOCKER_GPU_SKIP_UNUSED_DESCRIPTOR_TRANSFERS`,
  `PDOCKER_GPU_USE_SPIRV_DESCRIPTOR_ACCESS`,
  `PDOCKER_VULKAN_DISABLE_16BIT_STORAGE`, and
  `PDOCKER_VULKAN_SUBGROUP_SIZE`.
- Promotion rule: once a diagnostic knob becomes required for ordinary
  correctness, promote it into `_gpu_env(state)` and keep the compare driver
  forwarding it.  Do not leave correctness-critical behavior only in the
  ad-hoc compare script.
- Regression guard: `scripts/llama-gpu-env-manifest.json` is the single
  manifest for UI/compose runtime defaults, pdockerd runtime defaults,
  compare-only diagnostic forwarding, full compare env forwarding, and executor
  reflection fields.  Since `d5ce2e8`, pdockerd loads the packaged manifest at
  startup (falling back to the old literals only when the manifest is absent),
  and the Android asset/copy path packages the same manifest beside the daemon.
  The compare driver and artifact verifier both load this file;
  `tests.test_gpu_abi_contract` checks the verifier constants derived from it,
  so future edits cannot silently drop one side of the bridge.
- Lightweight env parity guard: `tests.test_llama_gpu_env_parity` checks that
  the manifest's pdockerd runtime env list, UI-compose runtime env list,
  compare diagnostic/forward env lists, and verifier constants stay in sync
  without running a device.  Compare-only Q6_K diagnostic knobs must remain out
  of the UI compose template until explicitly promoted to ordinary runtime
  behavior.
- Artifact guard: `scripts/verify-llama-gpu-artifact.py` treats failed
  `gpu.diagnostics.config_propagation` evidence as
  `config-propagation-mismatch` and blocks correctness/benchmark claims.  This
  catches cases where a compare command requested a diagnostic environment
  variable but executor dispatch evidence did not reflect it.
- Artifact verifier manifest guard: when compare emits config propagation
  checks, the verifier requires those checks to cover every env/field pair in
  `LLAMA_GPU_CONFIG_PROPAGATION_ENV_FIELDS`.  A stale compare script that omits
  a diagnostic env from the artifact is classified as
  `config-propagation-mismatch` even if the remaining checks say `pass`.
- Artifact verifier strictness update: compare artifacts now fail closed if
  `gpu.diagnostics.config_propagation.checks` is missing entirely.  This closes
  the stale-artifact hole where a run with no env reflection evidence could
  still inherit a later Q6_K/pass classification.
- Artifact responsibility-boundary guard: `config-propagation-mismatch` is
  classified before Q6_K local-size, writeback, or oracle evidence and reports
  `responsibility_boundary="env-propagation"`.  Once env propagation is trusted,
  Q6_K classifications keep separate `q6-local-size`, `q6-writeback`, and
  `q6-oracle` boundaries so an env mismatch cannot be mixed with
  oracle/writeback/local-size root-cause work.
- Unsupported GPU work gate: structured executor/oracle fields such as
  `status`, `latest_status`, `error`, `blocker_class`, or `classification`
  containing `unsupported`/`kernel-not-implemented-yet` are classified as
  `unsupported-gpu-work-accepted` and block correctness and benchmark claims.
  This keeps unsupported kernels/layouts from being hidden by served HTTP,
  speedup, or unrelated Q6_K summary fields.
- Executor-side fail-closed oracle gate: when `PDOCKER_GPU_CPU_ORACLE=1` is
  requested for a known llama shader candidate, pending or unsupported oracle
  statuses now stop the generic Vulkan dispatch with
  `stage=cpu-oracle-required`, `oracle_fail_closed=true`, `valid=false`, and
  an attached `cpu_oracle` report.  This specifically prevents the known
  fused RMS/RoPE pending path (`fused-rms-rope-oracle-pending`) and unsupported
  Q4/Q6 layouts from being recorded as `valid=true` bridge work.
- Artifact verifier fail-closed oracle gate: any structured artifact evidence
  containing `oracle_fail_closed: true`, `cpu-oracle-required`, or an
  `*-oracle-pending` status is classified as `oracle-fail-closed` and blocks
  correctness and benchmark claims.  A later HTTP response, Q6 summary, or
  speedup cannot override this.
- Artifact verifier web/API gate: compare artifacts must include the unchanged
  required `/completion` prompt sanity probe (`addition`, `2+3=`, expected
  prefix `5`) with HTTP status and content evidence.  Missing or mutated prompt
  evidence is classified as `api-prompt-sanity-missing`; a wrong answer can
  remain diagnostic but cannot be hidden by performance fields.
- Completion-readiness gate: `/v1/models` liveness is not enough.  The compare
  driver now records `gpu.service_readiness` with `/health`, `/v1/models`, and
  an unchanged one-token `/completion` probe before benchmarking.  If liveness
  passes but completion times out, the artifact is classified as
  `llama-completion-timeout`; it is evidence for ICD/executor dispatch
  boundary investigation, not a correctness or speed claim.
- Runtime-startup evidence gate: the llama entrypoint writes
  `/workspace/logs/llama-startup.json`, and compare artifacts embed it as
  `gpu.startup_diagnostics` while merging its post-profile environment into
  `gpu.runtime_env`.  Use this to detect stale profile/env propagation before
  changing Dockerfile, model, prompt, or llama.cpp.
- Dispatch lifecycle gate: when `PDOCKER_GPU_DISPATCH_PROFILE_LOG=1`, both the
  glibc ICD and Android executor emit compact `generic dispatch lifecycle`
  begin/stage/end records.  Compare artifacts summarize them under
  `gpu.diagnostics.dispatch_lifecycle`, including unmatched begin/end IDs.  If
  `/completion` stalls, inspect this boundary first to decide whether the wait
  is in ICD socket response, executor submit, fence wait, or writeback.
- Artifact verifier speedup-field gate: compare artifacts must carry
  `comparison.speedup`, `comparison.target_tokens_per_second`,
  `comparison.target_met`, plus the matching `bridge_overhead_phase` CPU/GPU
  tokens-per-second and speedup fields.  The CPU run itself may be skipped or
  reused during tuning, but without CPU baseline evidence the verifier keeps
  `benchmark_claim_allowed=false`.

### Stage 5: Correctness gate for `ngl=1`

Purpose: make one real offloaded layer safe before increasing GPU layer count.

Procedure:

1. Run `--gpu-layers 1 --predict 4 --repeat 1`.
2. Keep `PDOCKER_GPU_CPU_ORACLE=1` and profile response enabled.
3. Check deterministic `/completion` probes.
4. Check all known shader oracles.

Pass criteria:

- `gpu.correctness.summary.correctness == "pass"`.
- `required_failures == 0`.
- `benchmark_claim_allowed == true`.
- No known oracle candidate reports `status == "mismatch"`.
- `next_blocker` no longer says correctness probes do not match.

Fail criteria:

- Required `2+3=` probe fails.
- Any known oracle reports mismatch.
- The run is served but reports only performance without correctness.

### Stage 6: Performance work after correctness

Purpose: move from "correct but slow" to useful speedup.

Procedure:

1. Only start after `ngl=1` correctness passes.
2. Measure profile fields:
   - upload/copy/writeback counts and bytes,
   - dispatch count,
   - resident/mutable buffer cache hits,
   - guarded/resident page stats,
   - wall time per prompt.
3. Prefer reducing bridge crossings and copies before adding more kernels.
4. Re-run correctness after each optimization.

Pass criteria:

- Correctness still passes.
- Speedup improves against the same CPU baseline.
- Artifacts record `target_met`, speedup, GPU layers, blocker, and profile
  summary.

Target gates:

| Gate | Required |
|---|---:|
| Early correctness gate | `ngl=1` pass |
| Useful first speed gate | `>= 3x` with correctness pass |
| Project target | `>= 10x` with correctness pass |

## Handoff Notes For GPT-5.3 Codex Spark

Spark should operate as a focused executor, not as a broad replanner.  Use this
loop:

1. Read this file, then read only the latest tail of
   `docs/test/LLAMA_GPU_CORRECTNESS_20260507.md`.
2. Work on exactly one shader hash or one acceptance criterion per turn.
3. Make the smallest code change needed.
4. Run the fast local checks.
5. Install APK and run one device compare.
6. Summarize:
   - commit hash,
   - artifact path,
   - speedup,
   - correctness summary,
   - oracle status per relevant hash,
   - next blocker.

Spark should not:

- edit broad docs unrelated to llama GPU,
- change llama.cpp, Dockerfile, model, or prompt probes to make a test pass,
- add unbounded CPU oracles,
- commit unrelated untracked old evidence files,
- claim success from `served == true` alone.

Suggested first Spark task:

```text
Continue the Q6_K strict-passthrough blocker for 0x274f68a67dfef210.  Do not
modify llama.cpp, Dockerfiles, the model, or prompt probes.  Acceptance:
preserve the row-indexed writeback/workgroup-shape evidence from
docs/test/llama-gpu-ngl1-q6-row-provenance-20260519.json, then narrow
native-q6-device-execution-or-final-store to either executor/Vulkan device
execution or final output store. A rerun that loses row-indexed writeback
verification or workgroup-shape clarity is a setup/regression artifact, not
progress.
```

If Spark gets lost, it should run:

```bash
git log --oneline -5
git status --short
python3 -m unittest tests.test_gpu_abi_contract
```

Then resume from the newest committed artifact listed in this document.

## When Spark Should Escalate To GPT-5.5

Spark may continue while the work is a bounded implementation or evidence
collection loop.  It should explicitly recommend switching to GPT-5.5 when the
task stops being a narrow patch and becomes ambiguous architecture, algorithm
design, or cross-system debugging.

### Stay On Spark

Continue with GPT-5.3 Codex Spark when all of these are true:

- The target is one known file or a small, declared file set.
- The target shader hash and acceptance condition are already named.
- The change is a hash-gated oracle, JSON/report field, docs update, or small
  regression test.
- The next command is obvious from this document.
- Failure is local and reproducible with one compare artifact.

Examples:

- Add a bounded oracle for one known SPIR-V hash.
- Add a JSON field to `cpu_oracle`.
- Update `LLAMA_GPU_CORRECTNESS_20260507.md` with a new artifact.
- Run the next `ngl=1` compare and summarize the blocker.

### Switch To GPT-5.5

Recommend switching to GPT-5.5 before continuing if any of these are true:

- Two consecutive compare artifacts contradict the expected blocker class.
- A fix would require changing the bridge architecture, descriptor ownership
  model, persistent buffer protocol, or command queue design.
- The next step needs a new SPIR-V interpreter subset instead of a single
  hash-gated oracle.
- The suspected bug crosses three or more layers, for example ICD descriptor
  rewrite + executor aliasing + Android Vulkan memory visibility.
- The issue involves large buffers where memory safety, OOM behavior, or
  virtual-memory techniques must be reasoned about.
- The work might relax a correctness gate, alter benchmark prompts, rebuild
  llama.cpp, or change user-visible product semantics.
- Spark cannot explain why a change should fix the observed artifact before
  making the change.
- Spark is about to make broad speculative edits, especially in both
  `docker-proot-setup/src/gpu/` and `app/src/main/cpp/`.

Escalation message template:

```text
Switch to GPT-5.5 recommended.

Reason:
- <specific trigger from the list above>

Current evidence:
- latest artifact: <path>
- correctness: <pass/fail>
- speedup: <value>
- relevant hashes: <hash list>
- suspected layer: <ICD/executor/Vulkan memory/model/prompt/etc.>

Safe resume point:
- last commit: <git hash>
- next decision needed: <precise design question>
```

### Automatic Stop Rule

Spark must stop and ask for a GPT-5.5 handoff if it is considering a change
that could make a failing test pass by weakening the test instead of fixing the
bridge.  Examples include changing prompts, disabling correctness probes,
lowering required checks, hiding a shader hash from diagnostics, or treating
`served=true` as success.

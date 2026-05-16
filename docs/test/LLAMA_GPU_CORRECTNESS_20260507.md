# llama.cpp GPU Correctness Gate - 2026-05-07

## Scope

This note records the correctness gate added to the Android llama.cpp GPU
comparison loop. The llama.cpp source tree and container image were not rebuilt
for these runs. The APK-side benchmark driver now reconnects adb port forwarding
after the throughput probe and then runs short deterministic HTTP completion
probes before a benchmark result can be treated as usable evidence.

## Driver Change

`scripts/android-llama-gpu-compare.sh` now writes a nested
`gpu.correctness` report in each compare artifact when
`PDOCKER_LLAMA_CORRECTNESS` is enabled. The probe records:

- `/completion` result for `2+3=`.
- `/completion` result for `12*7=`.
- `/completion` result for `Repeat exactly: pdocker-ok`.
- `benchmark_claim_allowed=false` when a required probe fails.

The first attempt exposed a test-driver bug: `android-llama-bench.sh` removes
adb port forwarding on exit, so the correctness probe initially saw
`Connection refused`. The compare driver now restores the forward before
probing.

The driver also records an optional `cpu.correctness` report when the CPU
baseline is actually run. The final artifact then includes
`differential_correctness`, comparing CPU/no-offload and GPU/offload probe
outputs by probe name. This makes the gate independent of whether a short
arithmetic prompt is a strong language-model oracle: a GPU result must first
match the same model's CPU/no-offload output for the same prompt.

## Results

| Artifact | NGL | Variant | GPU tok/s | Speedup vs CPU baseline | Correctness | Probe outputs |
| --- | ---: | --- | ---: | ---: | --- | --- |
| `llama-gpu-compare-20260507-ngl0-correctness-control.json` | 0 | Vulkan path, no GPU layers | 0.2373 | 0.66x | fail | `3`, `7`, empty |
| `llama-gpu-compare-20260507-ngl1-correctness-gate.json` | 1 | Default bridge settings | 0.2300 | 0.64x | fail | `!`, `!`, `!!!!` |
| `llama-gpu-compare-20260507-ngl1-no-materialize.json` | 1 | SPIR-V specialization materialization disabled | 0.2858 | 0.79x | fail | `!`, `!`, `!!!!` |
| `llama-gpu-compare-20260507-ngl4-correctness-gate-rerun.json` | 4 | Default bridge settings | 0.1524 | 0.42x | fail | `!`, `!`, `!!!!` |
| `llama-gpu-compare-20260507-ngl4-no-skip-correctness.json` | 4 | Descriptor transfer skipping disabled | 0.1574 | 0.44x | fail | `!`, `!`, `!!!!` |
| `llama-gpu-compare-20260508-ngl1-no-dup-rewrite.json` | 1 | Duplicate descriptor rewrite disabled | 0.1027 | 0.28x | fail | `!`, `!`, `!!!!` |
| `llama-gpu-compare-20260508-ngl1-buffer-range-fix.json` | 1 | ICD clamps `VK_WHOLE_SIZE` to `VkBuffer` size | 0.1640 | 0.45x | fail | `!`, `!`, `!!!!` |
| `llama-gpu-compare-20260508-ngl1-dispatch-replay.json` | 1 | ICD replays recorded dispatch ops | 0.1695 | 0.47x | fail | `!`, `!`, `!!!!` |
| `llama-gpu-compare-20260508-ngl1-ordered-command-buffer.json` | 1 | ICD replays copy/fill/update/barrier/dispatch in command order | 0.1628 | 0.45x | fail | `!`, `!`, `!!!!` |
| `llama-gpu-compare-20260508-ngl1-binding-hash-rerun.json` | 1 | Binding checksum diagnostics enabled | 0.1592 | 0.44x | fail | `!`, `!`, `!!!!` |
| `llama-gpu-compare-20260508-ngl1-overlap-alias.json` | 1 | Overlapping descriptor ranges share one executor buffer | 0.1657 | 0.46x | fail | `!`, `!`, `!!!!` |
| `llama-gpu-compare-20260508-ngl1-all-copy-alias.json` | 1 | ICD copy-alias resolution applies to all descriptor bindings | 0.1316 | 0.36x | fail | `!`, `!`, `!!!!` |
| `llama-gpu-compare-20260508-ngl1-all-transfers.json` | 1 | Transfer skipping and caches disabled after alias fixes | 0.1335 | 0.37x | fail | `!`, `!`, `!!!!` |
| `llama-gpu-compare-20260508-ngl1-descriptor-semantics.json` | 1 | Descriptor array/copy/dynamic-offset hardening | 0.1628 | 0.45x | fail | `!`, `!`, `!!!!` |
| `llama-gpu-compare-20260508-ngl1-descriptor-trace.json` | 1 | Descriptor hardening with allocation trace | 0.1573 | 0.44x | fail | `!`, `!`, `!!!!` |
| `llama-gpu-compare-20260508-ngl1-workgroup-spec-guard.json` | 1 | Keep BuiltIn WorkgroupSize specialization subtree at default during materialization | 0.1353 | 0.37x | fail | `!`, `!`, `!!!!` |
| `llama-gpu-compare-20260508-ngl1-no-copy-alias-real.json` | 1 | Copy-alias fast path disabled by default in the compare driver | 0.1117 | 0.31x | fail | `礼拜`, `羽毛`, `itolitol刊登刊登` |
| `llama-gpu-compare-20260508-ngl1-disable-storage8.json` | 1 | 8-bit storage feature disabled | 0.1529 | 0.42x | fail | `wan`, `羽毛`, `itolitol Jing刊登` |
| `llama-gpu-compare-20260508-ngl1-enable-storage8.json` | 1 | 8-bit storage feature explicitly enabled | 0.1706 | 0.47x | fail | `礼拜`, `羽毛`, `itolitol刊登刊登` |
| `llama-gpu-compare-20260508-ngl1-disable-storage16.json` | 1 | 16-bit storage feature disabled | n/a | n/a | fail | model load crashed with `sig=11` |
| `llama-gpu-compare-20260508-ngl1-push-layout.json` | 1 | Full pipeline-layout push-constant size preserved across the bridge | 0.1813 | 0.50x | fail | `+`, `细细`, empty |
| `llama-gpu-compare-20260508-ngl1-differential-cpu-gpu.json` | 1 | Full CPU/no-offload vs GPU/offload differential correctness gate | 0.1949 | 0.15x | fail | CPU: `5`, `8`, empty; GPU: `+`, `细细`, empty |
| `llama-gpu-compare-20260508-ngl1-no-dup-latest.json` | 1 | Duplicate descriptor rewrite disabled after push-layout fix | 0.1962 | 0.15x | fail | `礼拜`, `羽毛`, `itol Bjitol刊登` |
| `llama-gpu-compare-20260508-ngl1-compact-summary.json` | 1 | Compact per-dispatch descriptor summary for the long final projection event | 0.1973 | 0.15x | fail | `+`, `细细`, empty |
| `llama-gpu-compare-20260508-ngl1-alias-map-set-aware.json` | 1 | Set-aware duplicate descriptor rewrite with alias target-id evidence | 0.2358 | 0.18x | fail | `+`, `细细`, empty |
| `llama-gpu-compare-20260509-ngl1-dispatch-policy-disable-storage8.json` | 1 | Dispatch-scoped executor feature policy with storage8 disabled | 0.1479 | 0.11x | fail | `+`, `细细`, empty; final projection reports `storage8`/`int8` mismatch |
| `llama-gpu-compare-20260509-ngl1-feature-mismatch-blocker.json` | 1 | Feature-mismatch classifier evidence; current service still injected `PDOCKER_VULKAN_DISABLE_8BIT_STORAGE=1` | 0.1441 | 0.11x | fail | blocked as `vulkan_feature_mismatch` for `int8`/`storage8` |
| `llama-gpu-compare-20260509-ngl1-default-storage8-unmasked.json` | 1 | Rebuilt APK with default storage8/int8 feature state preserved | 0.1371 | 0.11x | fail | `+`, `细细`, empty; feature mismatch cleared |
| `llama-gpu-compare-20260509-ngl1-execution-summary.json` | 1 | Compact success events now include SPIR-V hash, push size, local size, and specialization entries | 0.1599 | 0.12x | fail | final projection: hash `0x274f68a67dfef210`, local size `[1,1,1]`, specs `32,2,1` |
| `llama-gpu-compare-20260509-ngl1-device-extensions.json` | 1 | Executor enables supported storage/int8 Vulkan device extensions alongside feature structs | 0.1430 | 0.11x | fail | extension hardening did not change the `+`, `细细`, empty output shape |
| `llama-gpu-compare-20260509-ngl1-no-dup-dispatch-option.json` | 1 | Duplicate descriptor rewrite disabled through the ICD-to-executor dispatch option | 0.1669 | 0.13x | fail | alias map is truly empty; output shape still `+`, `细细`, empty |
| `llama-gpu-compare-20260509-ngl1-no-materialize-dispatch-option.json` | 1 | Specialization materialization disabled through the ICD-to-executor dispatch option | 0.1723 | 0.13x | fail | output changes to ` Marvel`, ` _`, `util dong dong dong` |
| `llama-gpu-compare-20260509-ngl1-pipeline-opt-dispatch-option.json` | 1 | Android pipeline optimization enabled through the dispatch option | 0.1186 | 0.09x | fail | output changes to `" '--"`, `ode`, empty |
| `llama-gpu-compare-20260509-ngl1-f32-samples-fixed.json` | 1 | Writable binding float32 samples recorded after final-projection dispatch | 0.1697 | 0.13x | fail | final binding 2 sample starts `[1.3597, 1.8112, 2.5802, -0.3651]` |
| `llama-cpu-gpu-probs-20260509-ngl1.json` | 1 | CPU/GPU HTTP `/completion` top-token probability capture | 0.1749 | 2.61x | fail | CPU top1: `5`/`8`; GPU top1: `+`/`细细`; no shared top-10 token ids |
| `llama-cpu-gpu-bisection-20260509-ngl1.json` | 1 | Diagnostic bisection tree plus bridge option propagation evidence | 0.1525 | 2.20x | fail | focus: `numeric_layout_or_readback`; env propagation: pass; finite f32 samples: 8 |
| `llama-gpu-bisection-upload-dispatch-20260509-ngl1.json` | 1 | Split read-only input upload from post-dispatch mutation | 0.0984 | 1.42x | fail | upload hash mismatches: 0; primary read-only dispatch mutations: 806 |
| `llama-gpu-bisection-all-readwrite-forwarded-fixed-20260509-ngl1.json` | 1 | Verified `PDOCKER_GPU_USE_SPIRV_DESCRIPTOR_ACCESS=0` propagation; all active descriptors treated conservatively | 0.1722 | 2.49x | fail | env propagation: pass; primary read-only mutations: 0; output still `+`, `细细`, empty |
| `llama-gpu-final-layout-all-readwrite-20260509-ngl1.json` | 1 | All-read/write conservative run with larger log capture | 0.1401 | 2.02x | fail | focus: `output_layout_or_shader_math`; upload/mutation checks clean |
| `llama-gpu-ngl1-matvec-alias-diagnostics-20260509.json` | 1 | Q6_K matvec classification and read/write alias hazard diagnostics for `0x274f68a67dfef210` | n/a | 2.17x | fail | `cpu_oracle.kernel_hint=mul-mat-vec-q6-k-large`; `rw_alias_hazards.count=2` |
| `llama-gpu-ngl1-matvec-push-alias-diagnostics-20260509.json` | 1 | Same Q6_K matvec diagnostic with bounded push-constant capture | n/a | 2.38x | fail | `push_u32=[4096,4096,4096,151936,...]`; `rw_alias_hazards.count=2` |
| `llama-gpu-ngl1-q6k-sample-oracle-20260509.json` | 1 | Bounded CPU oracle for eight Q6_K final-projection rows | n/a | 1.61x | fail | oracle mismatch for 8/8 rows; first expected `13.878`, GPU `6.831` |
| `llama-gpu-ngl1-q6k-sample-oracle-no-dup-20260509-rerun.json` | 1 | Same sampled Q6_K oracle with duplicate descriptor rewrite disabled | n/a | 2.15x | fail | hash changes to `0x1bf751845c5dce75`; same 8/8 oracle mismatch |
| `llama-gpu-ngl1-local-size-patch-oracle-20260509.json` | 1 | Patch literal `LocalSize 1` to specialization value `32` for WorkgroupSize-style shader | n/a | 2.37x | fail | patched hash `0x09c4622d92c6acb9`; local size `[32,1,1]`; same Q6_K oracle mismatch |
| `llama-gpu-ngl1-q6k-decode-variant-20260509.json` | 1 | Q6_K decode-variant split for high bits, signed scales, and zero-point | n/a | 0.94x | fail | canonical full `13.878`; no-high `-1.309`; unsigned-scale `-10.048`; no-center `17.219`; GPU `6.831` |
| `llama-gpu-ngl1-q6k-packed16-view-20260509.json` | 1 | CPU-side byte-view vs packed16-view Q6_K descriptor-view equivalence check | n/a | 2.30x | fail | packed16-view sum `13.8780234`; byte-view delta `0`; GPU still `6.831` |
| `llama-gpu-ngl1-q6k-partial-lanes-fixed-20260509.json` | 1 | Q6_K 32-lane partial-sum diagnostic for reduction/output-layout split | n/a | 2.02x | fail | row0 lane sum `13.878`; first16 `8.507`; second16 `5.371`; half-full `6.939`; GPU `6.831` |
| `llama-gpu-ngl1-q6k-row-window-20260509.json` | 1 | Contiguous 32-row Q6_K oracle window for output-index mapping | n/a | 1.84x | fail | 32/32 rows mismatch; packed16 delta remains `0`; no stable same-row or half-row mapping |
| `llama-gpu-ngl1-q6k-shader-like-oracle-20260509.json` | 1 | Shader-like Q6_K oracle using llama's packed 32-bit load and scale-cache flow | n/a | 2.01x | fail | shader-like sum `13.8780238`; canonical delta `4.16e-7`; GPU still `6.831` |
| `llama-gpu-ngl1-q6k-materialized-alias-icd-20260509.json` | 1 | Duplicate Binding 0 alias materialized as a separate descriptor buffer | n/a | 2.17x | fail | option propagated as `true`; output unchanged, so same-VkBuffer aliasing is not sufficient |
| `llama-gpu-ngl1-q6k-materialized-specialization-20260509.json` | 1 | SPIR-V specialization materialization probe | n/a | 2.65x | fail | Q6_K still mismatches; materializer did not rewrite this shader's Q6 specialization path |

`llama-gpu-compare-20260507-ngl1-no-dup-rewrite.json` is not included in the
evidence table because adb went offline during that run, so the result is
incomplete. The 2026-05-08 rerun completed and confirmed that duplicate
descriptor rewrite is not sufficient to explain the failure.

## Interpretation

The correctness failure is now reproducible and is not explained by adb
forwarding. Disabling descriptor-transfer skipping did not restore correctness,
and disabling SPIR-V specialization materialization did not restore
correctness. Disabling duplicate descriptor rewrite also did not restore
correctness. The NGL=1 result fails even though only the output layer is
offloaded, which points at the generic Vulkan dispatch path for the final
projection/logits path rather than at deeper repeating transformer layers.

Two ICD correctness fixes were added on 2026-05-08:

- `VK_WHOLE_SIZE` descriptor ranges are now clamped to `VkBuffer.size`, not to
  the tail of the backing memory allocation. This removes a real suballocation
  corruption hazard but did not by itself fix the llama output collapse.
- Command buffers now record and replay each generic SPIR-V dispatch instead
  of retaining only the latest dispatch state. This is required for Vulkan
  command-buffer semantics, but the NGL=1 llama correctness probe still fails.
- Command buffers now also replay copy/fill/update/barrier/dispatch operations
  in recorded order. This removes another Vulkan ordering mismatch, but the
  NGL=1 llama correctness probe still fails.
- Binding diagnostics now include bounded hashes before upload, after upload,
  after dispatch, and after writeback. Those hashes showed descriptor bindings
  that referenced overlapping regions of the same fd. The executor now coalesces
  overlapping descriptor ranges into one backing Vulkan buffer and emits
  `alias_rep` in the per-binding report. This preserves descriptor alias
  semantics and reduces redundant upload/download work, but the llama output
  collapse still reproduces.
- The ICD now applies copy-alias resolution to every descriptor binding rather
  than only binding 0, and its advertised storage-buffer descriptor limits now
  match the bridge's implemented capacity. These are correctness hardening fixes;
  they did not restore llama correctness in the NGL=1 probe.
- The ICD now records descriptor array element offsets, descriptor-copy updates,
  and dynamic storage-buffer offsets instead of silently ignoring those Vulkan
  semantics. The traced NGL=1 llama run did not show those paths as the active
  failure trigger, so they remain important compatibility fixes rather than the
  current llama correctness root cause.
- The executor now avoids materializing the `BuiltIn WorkgroupSize`
  specialization subtree when the shader still uses literal `LocalSize`. This
  prevents an invalid mismatch between `gl_WorkGroupSize` and actual local
  invocation count. The NGL=1 probe still fails, so this was another real
  hardening fix but not the final output-collapse cause.
- The compare driver no longer enables the copy-alias fast path by default.
  Copy-aliasing remains available as an opt-in diagnostic/performance knob via
  `PDOCKER_VULKAN_ALIAS_COPIES`, but correctness evidence showed that it changes
  the output-collapse shape before it has been proven safe.
- Storage feature probes showed that disabling 8-bit storage changes the
  incorrect output but does not restore correctness. Disabling 16-bit storage
  crashed during model load on this image, so this path is not a viable fallback
  for the current llama.cpp Vulkan build.
- The ICD now preserves the full push-constant size declared by the pipeline
  layout when serializing a dispatch to the Android bridge. This avoids
  reconstructing a narrower runner-side `VkPipelineLayout` from only the bytes
  written by `vkCmdPushConstants`. The NGL=1 probe output changed from the
  prior collapse shape but still failed, so the remaining issue is deeper than
  push-constant range truncation alone.
- The differential CPU/GPU gate confirms that the current NGL=1 failure is not
  merely prompt ambiguity. On the same image and prompts, CPU/no-offload returns
  `5` and `8` for the arithmetic probes while GPU/offload returns `+` and
  `细细`. Performance claims remain blocked until this differential gate passes.
- Disabling duplicate descriptor rewrite after the push-layout fix again changes
  the wrong output shape. That keeps descriptor identity/aliasing in the active
  suspect set, but it is not sufficient to restore correctness.
- Long final-projection dispatch events exceeded practical log-line parsing
  limits when full binding diagnostics and descriptor writes were emitted on one
  line. The executor now emits a separate compact per-dispatch JSON summary.
  This captured the active final projection dispatch (`shader_bytes=26784`,
  `dispatch=[1187,1,64]`) with the 510 MiB model buffer at binding 0, the
  607 KiB logits/work buffer shared by bindings 2/3/4, and the duplicate binding
  rewrite that maps the second binding-0 SPIR-V variable to descriptor binding 5.
- Duplicate descriptor rewrite is now descriptor-set aware. The latest NGL=1
  evidence confirms that the final projection shader rewrites SPIR-V target id
  `371` from descriptor set 0 binding 0 to descriptor binding 5, and the executor
  writes binding 5 as an alias of the same 510 MiB model-buffer descriptor used
  by binding 0. This hardens the compatibility layer, but correctness still
  fails, so the next probes should focus on storage8/storage16 semantics and
  final-projection input/output bytes rather than on cross-set binding confusion.
- Vulkan feature policy is now passed from the glibc ICD to the persistent
  Android executor on each dispatch, not only through the executor process
  environment. With `PDOCKER_VULKAN_DISABLE_8BIT_STORAGE=1`, the final
  projection shader still requires `storage8` and `int8`, and the compact
  summary correctly records those as feature mismatches. This confirms that
  storage8/int8 support is part of the active final-projection path, but simply
  treating it as unavailable does not restore correctness.
- The compare driver now classifies executor-reported SPIR-V feature
  mismatches as a first-class `vulkan_feature_mismatch` blocker. The latest
  evidence also exposed that `pdockerd` was still injecting
  `PDOCKER_VULKAN_DISABLE_8BIT_STORAGE=1` by default, so the next runtime
  baseline must preserve the Android driver's storage8/int8 feature state by
  default and use the disable flag only as an explicit diagnostic clamp.
- After rebuilding and reinstalling the compat APK with the default changed to
  `PDOCKER_VULKAN_DISABLE_8BIT_STORAGE=0`, the final projection no longer
  reports `storage8`/`int8` as missing. The correctness failure still
  reproduces with the same output shape, so the active blocker has moved back
  from feature advertisement to final-projection data semantics.
- Compact successful dispatch events now carry the same execution metadata that
  failure events already had: SPIR-V hash, push-constant byte count, declared
  and resolved local size, and specialization entries. The current final
  projection dispatch is stable at shader hash `0x274f68a67dfef210`,
  dispatch `[1187,1,64]`, push bytes `116`, local size `[1,1,1]`, and
  specialization values `constant_id 0=32`, `1=2`, `2=1`.
- The Android executor now enables supported Vulkan device extensions for
  8-bit storage, 16-bit storage, shader float16/int8, and storage-buffer storage
  class in addition to the feature-struct pNext chain. The NGL=1 correctness
  failure remains unchanged, so missing extension enablement is no longer the
  leading explanation for the final-projection collapse on this device.
- Bridge tuning options that live in the container environment must cross the
  glibc ICD to APK-executor boundary explicitly. `PDOCKER_GPU_REWRITE_DUPLICATE_DESCRIPTOR_BINDINGS=0`
  previously did not affect the persistent executor. The ICD now forwards a
  `rewrite_duplicate_descriptors=` dispatch option, and the latest no-dup run
  confirms the final projection has `descriptor_aliases=0`. Correctness still
  fails, so duplicate descriptor rewriting is not the sole cause.
- The same dispatch-option fix was added for SPIR-V specialization
  materialization and pipeline optimization. Those toggles now truly reach the
  persistent executor. Both change the wrong output shape, which keeps shader
  specialization / driver compilation behavior in the active suspect set, but
  neither restores CPU-matching logits.
- Compact binding diagnostics now include bounded float32 samples for writable
  buffers after dispatch. The current final-projection output buffer is not all
  zeros or NaNs; binding 2 starts with finite logits-like values
  `1.35967982`, `1.81122828`, `2.580235`, `-0.365148783`. The failure has
  therefore moved from "did the dispatch write anything?" to "are the logits
  numerically correct / interpreted with the expected layout?".
- The current final-projection shader hash `0x274f68a67dfef210` has now been
  classified against the dumped llama.cpp shader sources as a
  `mul_mat_vec_q6_k`-like large quantized matvec. It uses the `block_q6_K`
  weight layout, duplicate binding-0 views for packed 8/16/32-bit access, and
  specialization values `BLOCK_SIZE=32`, `NUM_ROWS=2`, `NUM_COLS=1`. A full CPU
  oracle would require the 510 MiB model range, so the next safe step is
  bounded alias/layout diagnostics before any sampled Q6_K decode oracle.
- Executor diagnostics now include an explicit `rw_alias_hazards` object so the
  compact JSON shows when a writable descriptor overlaps readable descriptors
  through the same bridge alias group. The observed `0x274f68a67dfef210`
  dispatch intentionally or accidentally presents binding 2 as writable while
  bindings 3 and 4 read the same 607 KiB range, so future runs can distinguish
  a legitimate in-place/fuse pattern from a bridge aliasing error.
- Compact execution diagnostics also include bounded `push_u32` values. The
  current Q6_K dispatch reports `[4096,4096,4096,151936,622329856,4096,151936,
  ...]`, which matches the matvec push layout shape (`ncols=4096`,
  `stride_d=151936`, batch strides present) and gives the next sampled oracle a
  stable coordinate system without logging or copying the full push blob
  elsewhere.
- A bounded Q6_K sample oracle now executes for `0x274f68a67dfef210`. It reads
  only the sampled row blocks from the 510 MiB weight range and the 16 KiB input
  vector, then compares eight output rows before writeback. The first run
  mismatches all eight sampled rows (`expected_hash=0x221604951e806c53`,
  `gpu_hash=0x3d9204797e9c4247`), with row 0 expected `13.8780231` and GPU
  `6.83085108`. This proves the remaining blocker is inside Q6_K matvec
  layout/decode/descriptor-view/local-size semantics, not merely sampling or
  HTTP serving.
- Re-running the same sampled oracle with duplicate descriptor rewriting
  disabled changes the shader hash to `0x1bf751845c5dce75` but produces the
  same first-row mismatch shape. Duplicate binding rewrite is therefore not the
  primary cause of the current Q6_K sampled mismatch.
- The executor now patches the specific SPIR-V shape where llama.cpp's shader
  exposes a specialization-backed `BuiltIn WorkgroupSize` but also contains a
  literal `OpExecutionMode LocalSize 1 1 1`. The first version only copied
  `SpecId 0` into `LocalSize.x`, producing `[32,1,1]` for Q6_K even though the
  application supplied `[32,2,1]`. That can silently run only half of the
  intended local invocations. The current implementation copies all available
  `SpecId 0..2` dimensions and rejects invalid or over-large workgroup sizes.
  The next device run must confirm that Q6_K reports `local_size_patched=true`
  with `spirv_local_size=[32,2,1]` and then re-check the sampled oracle.
- The compare driver now refuses to start a llama container when Android memory
  headroom is already unsafe. Defaults are `PDOCKER_LLAMA_MIN_FREE_MB=512` and
  `PDOCKER_LLAMA_MIN_SWAP_FREE_MB=1024`. This prevents GPU tuning runs from
  starving the browser-backed VS Code session or making failures look like GPU
  correctness regressions when they are actually low-memory pressure.
- Decode-variant diagnostics now compare the canonical Q6_K decode against
  common wrong interpretations for the first sampled row. Ignoring high 2-bit
  planes gives `-1.30868773`, treating scales as unsigned gives `-10.0479286`,
  and omitting the `-32` center gives `17.2191929`, while the GPU remains
  `6.83085108`. None of these simple decode mistakes explains the GPU value;
  the next split should inspect descriptor-view aliasing and reduction/shared
  memory behavior rather than only signedness/zero-point mistakes.
- Packed16-view diagnostics now mirror the llama Vulkan Q6_K helper's `uint16_t`
  view against the same 210-byte block layout. The packed16-view sum is
  `13.8780234` and the byte-view delta is `0`, so the bridge is not currently
  failing because the Q6_K bytes need structural conversion between the
  container and Android Vulkan sides. The remaining suspect is how those same
  bytes are exposed and consumed at dispatch time: descriptor offset/range,
  aliasing, specialization-lowered execution, or shared-memory reduction.
- Partial-lane diagnostics now record all 32 row-0 lane sums for the active
  Q6_K shader. The row-0 full sum is still `13.8780231`; `first16_sum` is
  `8.50700955`, `second16_sum` is `5.37101381`, and the half-full value is
  `6.93901168`, close but not equal to the GPU row-0 value `6.83085108`.
  Other sampled rows do not follow a simple "half reduction" rule. This keeps
  the focus on output row/workgroup mapping and shared-memory reduction
  semantics, not a global divide-by-two mistake.
- The contiguous 32-row window shows `32/32` Q6_K row mismatches. GPU row 2 is
  close to row 0's half sum, and several rows are close to half sums from other
  nearby rows, but there is no stable same-row, half-row, even-lane, or odd-lane
  mapping. Treat the current blocker as an execution-indexing/reduction problem:
  the CPU oracle likely needs to mirror the shader's workgroup-to-output layout
  and reduction ordering more exactly before a bridge-side fix can be selected.
- A shader-like Q6_K oracle now mirrors llama.cpp's optimized `mul_mat_vec_q6_k`
  path: packed 32-bit `ql/qh` loads, four-lane vector accumulation, signed
  scale-cache indexing, and `fma` ordering. It matches the canonical oracle
  within `4.16e-7`, so the remaining mismatch is not explained by the oracle
  using a simpler mathematical decode.
- Materializing the duplicate Binding 0 packed16 alias into a separate Vulkan
  buffer now propagates through the container ICD and executor
  (`materialize_descriptor_aliases=true`), but the Q6_K output is unchanged.
  This rules out "same VkBuffer bound to two rewritten descriptors" as the
  primary cause. The next suspect remains the shader execution semantics around
  specialization, shared memory, and reduction/order on the Android Vulkan
  backend.
- The compare driver now requests `completion_probabilities` with bounded
  `n_probs` during correctness probes. This records selected token ids and
  top-logprob lists for both CPU/no-offload and GPU/offload. The latest full
  run shows zero overlap between CPU and GPU top-token sets for the arithmetic
  probes: CPU selects token `5` for `2+3=`, while GPU selects unrelated tokens
  such as `礼拜`; CPU selects token `8` for `12*7=`, while GPU selects `羽毛`.
  The failure is therefore before sampling policy: the GPU logits distribution
  itself is wrong.
- The report now includes a `diagnostic_bisection` tree. The current route is:
  CPU API baseline passes, GPU HTTP output diverges, top-token probabilities
  diverge, Android Vulkan dispatch completes, writable final-projection samples
  are finite, so the active focus is `numeric_layout_or_readback`.
- Bridge tuning environment propagation is now evidence-checked. Explicit
  tuning env vars must appear with the expected values in executor JSON fields
  such as `duplicate_descriptor_rewrite`, `materialize_specialization`, and
  `disable_pipeline_optimization`; otherwise the run is classified as
  `config_propagation_mismatch` before interpreting any performance or
  correctness delta.
- The bisection split for input integrity now distinguishes upload from
  post-dispatch mutation. With compact `gpu_after_upload_hash` evidence,
  primary read-only bindings match immediately after upload, which rules out
  the fd-to-GPU copy path for the current failure. When SPIR-V descriptor access
  analysis is disabled and every active descriptor is handled conservatively,
  primary read-only mutations disappear, but correctness still fails. The next
  focus is therefore output layout / shader math rather than skipped writeback.
- The same run caught and fixed another propagation blind spot:
  `PDOCKER_GPU_USE_SPIRV_DESCRIPTOR_ACCESS=0` initially did not cross the
  glibc ICD to persistent executor boundary. The ICD now forwards
  `skip_unused_descriptor_transfers=` and `use_spirv_descriptor_access=`, and
  the executor records both values in dispatch evidence.

The NGL=0 control also does not satisfy the arithmetic probe, so the absolute
math prompt is not strong enough as the only correctness oracle. However, the
NGL=0 output shape (`3`, `7`, empty) differs sharply from the NGL>=1 output
shape (`!`, `!`, `!!!!`). The next correctness gate should therefore compare
GPU output against a same-model no-offload control for the same prompt, not only
against a hard-coded arithmetic answer.

## Next Actions

- Add bounded binding checksums around the final projection dispatch and compare
  output buffer bytes against the CPU/no-offload control.
- Keep the next blocker on descriptor/byte-level final-projection divergence
  rather than feature policy: storage8/int8 is now unmasked by default and the
  executor reports no feature mismatch for the latest NGL=1 run.
- Use the stable final-projection shader hash and specialization tuple as the
  key for the next byte-level probe, so future optimizations do not compare
  different kernels by accident.
- Treat every container-side bridge knob as suspect unless it appears in the
  executor JSON event; the no-dup probe proved that environment-only toggles
  can silently miss the persistent executor process.
- Use the diagnostic bisection tree for the next investigations: every new
  probe should split one unresolved boundary into two smaller regions, then
  record the chosen branch in the JSON evidence. Avoid one-off knob testing
  unless the knob's actual executor-side value is also captured.
- Compare the final-projection output buffer before sampling, after dispatch,
  and after writeback under the three now-real modes: default, no
  specialization materialization, and pipeline optimization enabled.
- Next split: compare final-projection output layout/math with conservative
  all-read/write descriptor handling. If the output buffer remains finite but
  top-token probabilities are unrelated to CPU, inspect descriptor alias target
  ranges, push constants, and specialization-lowered local size for the stable
  shader hash `0x274f68a67dfef210`.
- Add a CPU/no-offload logits probe for the same token position and compare the
  sampled logits against the GPU binding-2 float samples.
- Inspect the final projection shader itself. The current dump shows duplicate
  `Binding 0` storage-buffer variables with different struct views; descriptor
  rewrite and aliasing are present, but the remaining failure may be in
  specialization/local-size lowering, feature advertisement, or shader memory
  visibility rather than in descriptor delivery.
- Keep performance claims blocked while
  `gpu.correctness.summary.benchmark_claim_allowed` is false.

## 2026-05-09 Follow-up: Response Capture and Descriptor/Barrier Splits

The executor response reader in the Vulkan ICD was hardened after the final
projection trace exceeded the old fixed 4 KiB response buffer.  The current
reader is stack-first, grows geometrically only for large diagnostic JSON, has a
1 MiB cap, and keeps the heap buffer local to the dispatch call.  This prevents
diagnostic truncation without introducing shared mutable state in the ICD.

Recent NGL=1 evidence keeps the same conclusion: GPU offload is real, but
correctness is still blocked before sampling.

| Artifact | Variant | Config propagation | Key observation |
|---|---|---|---|
| `llama-gpu-final-layout-full-response-stack-20260509-ngl1.json` | full final-projection response after stack-first capture | n/a | complete final-projection evidence captured; output remains `+`, `细细`, empty |
| `llama-gpu-final-layout-no-dup-all-readwrite-20260509-ngl1.json` | duplicate descriptor rewrite disabled, conservative descriptor transfers | pass | duplicate rewrite is not the sole cause; output changes but remains wrong |
| `llama-gpu-descriptor-array-probe-20260509-ngl1.json` | descriptor-array layout tracing enabled | pass | `descriptor_array_layout_seen=false` for this path |
| `llama-gpu-no-specialize-no-dup-all-readwrite-20260509-ngl1.json` | specialization materialization disabled | pass | specialization materialization changes output shape but does not restore CPU parity |
| `llama-gpu-no-overlap-no-specialize-20260509-ngl1.json` | overlap aliasing disabled | pass | overlap handling affects logits, but disabling it is not a fix |
| `llama-gpu-barrier-no-overlap-20260509-ngl1.json` | explicit host/compute memory barriers added | pass | barriers are recorded, but correctness still fails in the no-overlap/no-specialize path |

Current active branch: the upload path and simple environment propagation are
now less likely.  The next split should stay around final projection numeric
semantics: quantized storage interpretation, descriptor/push layout, and exact
logit buffer layout against the CPU/no-offload control.

## 2026-05-09 Boundary Control: `n-gpu-layers=0` Is Not a No-GPU Control

The root isolation assumption was rechecked by running Vulkan mode with
`LLAMA_ARG_N_GPU_LAYERS=0` and matching CPU/GPU context sizes (`ctx=512`).
This still diverged from the CPU/no-offload control:

| Artifact | CPU probes | Vulkan mode `ngl=0` probes | Dispatch evidence |
|---|---|---|---|
| `llama-gpu-boundary-ngl0-ctx512-20260509.json` | `5`, `8`, empty | `+`, `2`, empty | `gpu_offloaded_layers=0/37`, `generic_spirv_dispatch_seen=true` |

This means `n-gpu-layers` alone is not a sufficient isolation knob for the
pdocker Vulkan route.  Even with zero model layers reported as offloaded,
llama.cpp still emits generic SPIR-V dispatches through the Vulkan backend.
Future correctness work must therefore bisect by actual dispatched shader hash
and descriptor event, not only by layer count.  The compare driver now records
`ngl_zero_generic_spirv_dispatch` and classifies this case as
`vulkan_backend_control_mismatch` instead of treating it as a clean partial
offload result.

### First `ngl=0` Dispatch Scale

The zero-layer Vulkan control produced small generic SPIR-V workloads, so a CPU
emulation/oracle path is feasible for correctness isolation:

| SPIR-V hash | Likely operation | Dispatch | Local size | Invocations | Binding bytes | Notes |
|---|---|---:|---:|---:|---:|---|
| `0x7bf05c459ac87f2b` | unnamed scalar/indexing shader | `[1,12,1]` / `[1,48,1]` | `[256,1,1]` | 3,072 / 12,288 | 49,664 / 197,120 | 3 storage buffers, f32 arrays, one specialization constant |
| `0xac41e8033a67af4a` | RoPE/Yarn shader | `[48,1,1]` / `[192,1,1]` | `[1,256,1]` | 12,288 / 49,152 | 98,328 / 393,240 | 5 storage buffers, f32/int arrays, no quantized storage requirement |

These are not large matrix-multiply kernels.  The data movement is under 400 KiB
per captured dispatch and the static SPIR-V contains only ordinary Shader
capability with f32/int storage.  That makes them good candidates for a
debug-only CPU oracle:

1. Capture the exact descriptor byte ranges, push constants, specialization
   constants, dispatch geometry, and shader hash.
2. Run the same operation through a CPU reference path inside the executor.
3. Compare CPU-oracle output bytes against Android Vulkan output bytes before
   writing back to the container fd.
4. If the CPU oracle matches llama CPU behavior while Vulkan diverges, the
   bridge/Vulkan execution path is guilty.  If the oracle also diverges, the
   issue is descriptor/push layout interpretation or the test route, not the
   Android GPU driver.

The first implementation should not be a full SPIR-V VM.  Start with a
hash-gated debug oracle for these two kernels, then graduate to a small
interpreter subset only if more shader hashes become front blockers.

### API Understanding Trace

The compare driver now records an `api_understanding` diagnostic block that
checks whether descriptor data captured at the Vulkan API boundary survives the
ICD-to-executor handoff:

- original descriptor offset/range,
- storage buffer size,
- descriptor type,
- dynamic descriptor flag,
- bound memory offset,
- effective executor offset/size.

`llama-gpu-api-understanding-ngl0-20260509.json` shows
`api_understanding.summary=pass` for the zero-layer Vulkan control:

| Check | Result |
|---|---|
| Missing API binding metadata | `0` |
| API range vs executor size mismatches | `0` |
| API memory offset + descriptor offset vs executor offset mismatches | `0` |

This does **not** prove the whole Vulkan API contract is correct, but it rules
out one class of bridge bugs: the executor is no longer blindly reporting only
its own flattened binding view.  The next missing independent check is SPIR-V
reflection: shader-declared binding types and push-constant access must be
compared with the API trace before trusting a CPU oracle.

## 2026-05-09 CPU Oracle Scaffold

A debug-only CPU oracle scaffold is now wired through the container
environment, ICD command, executor parser, and executor JSON:

- set `PDOCKER_GPU_CPU_ORACLE=1` to request the oracle path,
- executor events report `cpu_oracle_requested`,
- known small llama hashes are classified as oracle candidates,
- the current scaffold does **not** execute the kernels yet; it reports
  `executed=false` and `status=scaffold-ready-needs-kernel-implementation`.

`llama-gpu-cpu-oracle-scaffold-ngl0-20260509.json` confirms the request reaches
the executor and that both zero-layer front-blocker hashes are candidates:

| SPIR-V hash | Oracle hint | Current status |
|---|---|---|
| `0x7bf05c459ac87f2b` | `small-f32-indexing` | scaffold only |
| `0xac41e8033a67af4a` | `rope-yarn` | scaffold only |

The same run also records `spirv_binding_reflection`, showing shader-declared
bindings, read/write access, and whether each binding was present at the API
boundary. This is the guardrail against feeding the CPU oracle the same wrong
API interpretation as the GPU path.

## 2026-05-09 First Executing CPU Oracle

The first hash-gated CPU oracle has been implemented for
`0x7bf05c459ac87f2b` (`small-f32-indexing`).  It runs inside the Android GPU
executor after the Vulkan fence is signalled and before container writeback, so
it compares the exact Android Vulkan output against a CPU reconstruction using
the captured push constants, descriptor ranges, and dispatch geometry.  The
oracle is intentionally opt-in (`PDOCKER_GPU_CPU_ORACLE=1`) and remains
diagnostic-only.

| Artifact | Variant | Oracle result | Observation |
|---|---|---|---|
| `llama-gpu-cpu-oracle-exec-ngl0-20260509.json` | default overlap handling, `ngl=0`, `ctx=512`, `predict=4` | `small-f32-indexing`: executed, mismatch (`24,448 / 24,576` and `6,015 / 6,144`), `input_output_overlap=true` | The first small shader does not match the CPU oracle.  The captured event also shows binding 0 and binding 2 sharing the same underlying range, so the next split must distinguish a true arithmetic/indexing bug from in-place descriptor alias semantics. |
| `llama-gpu-cpu-oracle-exec-no-overlap-ngl0-20260509.json` | overlap aliasing disabled | small oracle still mismatches before later queue-submit failure | Disabling overlap aliasing is not a safe global fix; it changes descriptor ownership but the run later fails in the generic SPIR-V path. |

Current interpretation: the front blocker has moved from “does the CPU oracle
request reach the executor?” to “why does the first f32 indexing shader diverge
under the actual descriptor alias layout?”  The next work item is to add an
alias-aware oracle mode: snapshot read-only descriptors before dispatch, emulate
the shader against that snapshot, and separately report whether read/write
descriptor overlap makes the shader order-dependent or undefined.  Only after
that split should the RoPE/Yarn oracle be implemented.

### Specialization Materialization Split

`llama-gpu-cpu-oracle-iter-diagnosis-ngl0-20260509.json` narrowed the
`small-f32-indexing` mismatch further.  With SPIR-V specialization
materialization enabled, the oracle showed:

- `compared_iter0=12288`, `mismatch_iter0=12160`,
- `compared_iter1=12288`, `mismatch_iter1=12288`,
- all mismatches in this shader were zero-valued GPU results.

The first 128 outputs matched, then the GPU behaved as if the broadcast
specialization branch had not been taken.  Running the same route with
`PDOCKER_GPU_MATERIALIZE_SPIRV_SPECIALIZATION_CONSTANTS=0` changed the shader
hash to `0x11d5243c43b23a7b` and restored the required correctness probe:

| Artifact | Materialization | Required correctness | Speedup vs CPU baseline | Blocker |
|---|---:|---:|---:|---|
| `llama-gpu-cpu-oracle-iter-diagnosis-ngl0-20260509.json` | enabled | fail | `2.47x` | specialization-materialized shader writes zeros outside the first broadcast block |
| `llama-gpu-cpu-oracle-no-materialize-ngl0-20260509.json` | disabled | pass | `2.33x` | bridge upload/copy overhead |
| `llama-gpu-default-oracle-match-ngl0-20260509.json` | default disabled | pass | `2.22x` | bridge upload/copy overhead |

Because correctness beats this micro-optimization, executor-side SPIR-V
specialization materialization is now opt-in.  The default path keeps Vulkan
specialization info intact and lets the Android Vulkan driver consume the
original SPIR-V.  The CPU oracle now recognizes the default non-materialized
hash `0x11d5243c43b23a7b` as the same `small-f32-indexing` shader and confirms
that it matches exactly for the captured zero-layer dispatches
(`mismatch_count=0` for both 24,576-float and 6,144-float events).

The next boundary is real layer offload.  `llama-gpu-default-no-materialize-ngl1-20260509.json`
served with `n-gpu-layers=1`, but the required correctness probe failed and the
speedup was only `1.82x`.  That run introduced additional generic SPIR-V hashes
(`0x11c0523df6c795b8`, `0xf2f988b94bd3e0dc`, `0x274f68a67dfef210`) beyond the
zero-layer hashes.  The next correctness split should target those hashes before
raising `n-gpu-layers`.

`llama-gpu-ngl1-small-add-oracle-20260509.json` closes the first of those
hashes.  `0x11c0523df6c795b8` is the same small indexing shader family, but it
uses direct RHS indexing and an f32 add operation instead of the zero-layer
broadcast multiply.  The CPU oracle now matches it exactly:

| Hash | Oracle classification | Compared floats | Mismatches |
|---|---|---:|---:|
| `0x11c0523df6c795b8` | `small-f32-indexing` add/direct-RHS | `4096` | `0` |

The `ngl=1` correctness failure therefore moves past the small indexing shader.
`llama-gpu-ngl1-rope-yarn-oracle-20260509.json` then closes
`0xac41e8033a67af4a`: the executor-side RoPE/Yarn oracle follows the observed
`rope_neox` SPIR-V layout, compares 4,096 floats, and reports
`mismatch_count=0`.

`llama-gpu-ngl1-rms-norm-oracle-20260509.json` closes
`0xf2f988b94bd3e0dc`: the shader is classified as RMSNorm with specialization
`constant_id=1` enabling the optional multiply, compares 4,096 floats, and
reports `mismatch_count=0`.

The `ngl=1` model-level correctness probe still fails (`2+3=` produced
`Marvel` in the latest run), so the remaining primary front blocker is now
`0x274f68a67dfef210`.

### NGL2 Q4K And Fused RMS/RoPE Split (2026-05-10)

`docs/test/llama-gpu-ngl1-q6k-regression-restart-20260510.json` is the current
highest correctness-passing performance evidence: `ngl=1` passes the required
prompt gate and reaches about `2.63x` against the saved CPU baseline.

For `ngl=2`, the Q4_K safe-kernel path has local oracle evidence: sampled
`0x533df01b7fd2ed97` dispatches match the CPU reconstruction.  The next failure
is therefore not currently attributed to Q4_K block decoding.

The next observed front family is `0x4f37d4d51dd83526` / `0x53c67d2aebf48739`.
Disassembly shows these are fused RMSNorm + RoPE/Yarn kernels: the RMSNorm stage
writes into workgroup memory, then the same shader applies the nested RoPE/Yarn
stage before writing the final output.  The earlier plain-RMS sampled oracle was
therefore invalid for these hashes because it compared final output against an
intermediate value.  The executor now classifies them as
`rms-norm-rope-fused` and reports `fused-rms-rope-oracle-pending` instead of a
false mismatch.

Latest log-only evidence is recorded in
`docs/test/llama-gpu-ngl2-fused-rms-rope-classified-20260510-log-summary.json`.
That run timed out before the compare wrapper wrote a final correctness JSON, so
it must not be used as a benchmark claim.  It does establish the next precise
work item: implement a bounded full fused RMSNorm+RoPE oracle, then continue to
classify the following unsupported hashes if the fused oracle matches.

### Strict Vulkan Passthrough Mode (2026-05-10)

A strict passthrough switch was added as `PDOCKER_GPU_STRICT_PASSTHROUGH=1` and
is now the default for `scripts/android-llama-gpu-compare.sh` Vulkan runs unless
explicitly overridden.  This mode is intended to stop the bridge from solving
llama.cpp one shader hash at a time.  Under strict mode the executor defaults
are changed to favor API fidelity over speed:

- no SPIR-V specialization materialization unless explicitly requested,
- no literal local-size patching,
- no duplicate descriptor binding rewrite unless explicitly requested,
- no Q4_K/Q6_K safe-kernel substitution unless explicitly requested,
- no Q4_K targeted specialization materialization,
- no unused-descriptor transfer elision,
- no SPIR-V descriptor-access based transfer trimming,
- no resident/mutable/write-only buffer cache shortcuts,
- no dirty-page probe/writeback optimization,
- no `VK_PIPELINE_CREATE_DISABLE_OPTIMIZATION_BIT` default.

Overlap alias grouping is intentionally still enabled because it preserves the
observable semantics of descriptors that refer to overlapping ranges of the same
container allocation.  This is a bridge fidelity mechanism, not a performance
shortcut.

Strict `ngl=1` has now been exercised on device:

| Evidence | Bridge shape | Correctness | Important result |
|---|---|---|---|
| `llama-gpu-strict-passthrough-forced-ngl1-20260510.json` | descriptor-range staging | fail | Q6_K raw SPIR-V `0x1bf751845c5dce75` mismatches CPU oracle (`13.8780231` expected, `6.83085108` observed at sample 0). |
| `llama-gpu-strict-passthrough-no-storage8-ngl1-20260510.json` | descriptor-range staging, storage8 disabled in bridge policy | fail | The same shader still runs while the executor policy reports missing `int8`/`storage8`; performance evidence is rejected as a feature-policy mismatch. |
| `llama-gpu-strict-passthrough-preserve-buffer-ngl1-20260510.json` | VkBuffer-coordinate staging | fail | Strict mode now stages the full VkBuffer coordinate space and preserves descriptor offsets. RoPE/small sampled oracles match, but Q6_K still mismatches and the run is too slow for a benchmark claim. |

The current conclusion is deliberately narrow: the old descriptor-range staging
was not API-faithful enough, so strict mode now preserves the application's
VkBuffer coordinate space.  That did not close the Q6_K result mismatch, which
means the next pass-through work must preserve more of the Vulkan object graph
and feature contract rather than adding another llama-specific shader
substitution.  The most likely remaining bridge gaps are VkBuffer/VkDeviceMemory
identity, original buffer bind offsets, and feature-gated shader selection for
the `int8`/`storage8` final-projection path.

The next bridge step is now implemented in code as `VULKAN_DISPATCH_V3`.
The container-side ICD includes object identity and sizing metadata for each
storage descriptor:

- `api_memory_id`
- `api_memory_size`
- `api_buffer_id`
- `api_memory_offset`
- `api_buffer_size`
- descriptor `api_offset` / `api_range`

When `PDOCKER_GPU_STRICT_PASSTHROUGH=1`, the Android executor uses this metadata
to create an Android-side Vulkan object graph with one `VkDeviceMemory` per
container memory identity and one `VkBuffer` per container buffer identity,
then binds each buffer at the original `vkBindBufferMemory` offset.  The data
upload is still range-limited to the descriptor ranges for practicality, but
the driver-facing object graph is no longer collapsed to independent
`fd+offset+size` buffers.

This is the intended pass-through direction: transfer Vulkan API semantics and
object identity across the glibc/Bionic process boundary, not llama-specific
shader semantics.

### Descriptor Set V4 and Adreno Pipeline Blocker (2026-05-10)

`VULKAN_DISPATCH_V4` now transports descriptor-set identity in addition to
binding number, buffer identity, memory identity, offsets, and ranges.  The ICD
snapshots multiple descriptor sets from `vkCmdBindDescriptorSets`; the Android
executor allocates matching descriptor set layouts/sets and binds the whole set
array to the pipeline.  It no longer silently flattens all descriptors into set
0.  Multi-set pipelines deliberately bypass the current single-set pipeline
cache until the cache key is extended.

The latest strict test is:

- `docs/test/llama-gpu-strict-passthrough-v4-dump2-ngl2-20260510.json`

Result: the bridge reaches generic SPIR-V dispatch, preserves strict object
graph evidence, but Android Vulkan rejects a ggml Q4_K compute pipeline during
`vkCreateComputePipelines` with `VK_ERROR_UNKNOWN` (`vk_result=-13`).  The
failing upstream shader was identified from the container shader bundle as:

- `mul_mat_vec_q4_k_f32_f32.spv`
- original FNV-1a shader hash: `0xf3cd7d18f0276b42`
- capabilities: `Shader`, `Int8`, `StorageBuffer16BitAccess`,
  `StorageBuffer8BitAccess`

This is now classified as a driver/pipeline-creation blocker, not a container
process, Dockerfile, model, or prompt blocker.  Pure strict pass-through does
not yet survive this Adreno shader.  A compatibility-lowering run with duplicate
descriptor rewrite enabled moves the blocker to the rewritten Q4_K pipeline,
which confirms the next work item is the Vulkan pipeline compatibility layer,
not container startup.

The best currently reproducible GPU-serving run is:

- `docs/test/llama-gpu-safe-ngl1-20260510.json`

That run starts llama.cpp with Vulkan offload (`ngl=1`) and serves HTTP
successfully.  Required prompt correctness passed (`2+3=` returned `5`), but
throughput is only about `0.09 tok/s`, far below the CPU baseline and the 10x
target.  It is evidence that the end-to-end GPU path can serve, not a
performance success.

Next implementation focus:

1. Keep strict V4 as the fidelity baseline.
2. Treat Adreno pipeline rejection separately from API object transport.
3. Implement a generic Vulkan compatibility-lowering layer for driver-rejected
   SPIR-V patterns (starting with Q4_K duplicate binding / int8-storage
   patterns) without changing llama.cpp, Dockerfile, model, or prompt.
4. Keep correctness probes enabled whenever a run reaches HTTP serving.

### Specialization Retry Breakthrough (2026-05-10)

The Q4_K pipeline blocker above was narrowed further.  The same upstream
`mul_mat_vec_q4_k_f32_f32.spv` module can compile for some specialization
tuples and fail for another tuple.  The failing strict case used
`SpecId 2 = 1`; earlier calls using the same module completed successfully.

The executor now retries failed pipeline creation by materializing Vulkan
specialization constants into ordinary SPIR-V constants.  This is intended as a
generic driver-compatibility lowering: Vulkan specialization constants are
pipeline-creation-time constants, so pre-materializing them preserves the API
meaning while avoiding an Adreno specialization compiler failure.  It does not
change llama.cpp, the Dockerfile, the model, the prompt, descriptors, memory
identity, or buffer ranges.

Evidence:

- `docs/test/llama-gpu-strict-v4-specialization-retry-ngl2-20260510.json`

Result: the previous `VK_ERROR_UNKNOWN` pipeline blocker disappeared.  The
container stayed running and the HTTP health endpoint became reachable after
the compare wrapper had already timed out.  Manual probing then reached the
server, but the `2+3=` one-token check returned an incorrect token (`!`) and was
extremely slow.  Therefore this is a pipeline-creation breakthrough, not yet a
correctness or performance success.

Current next blocker:

1. Capture CPU-oracle / descriptor hash evidence for the first incorrect
   `ngl=2` response after specialization retry.
2. Determine whether the wrong token is caused by a specific remaining shader,
   descriptor writeback/readback ordering, or insufficient synchronization
   around materialized-specialization pipelines.
3. Keep `ngl=1` as the current end-to-end serving baseline and use `ngl=2` as
   the active correctness target.

### Strict Object Graph Device-Local Staging Probe (2026-05-10)

ADB is currently unavailable, so the next correctness probe was implemented and
built locally but is not yet device-verified.  The executor now has an opt-in
strict path controlled by `PDOCKER_GPU_STRICT_DEVICE_LOCAL_STAGING=1`.

This path keeps the same Vulkan API object graph and descriptor bytes, but places
the shader-visible storage buffers in device-local memory when the Android Vulkan
driver exposes a suitable memory type.  A host-visible coherent staging buffer is
created per strict memory object for upload/download using `vkCmdCopyBuffer`.
The intent is to test whether the remaining Q6_K mismatch is caused by executing
packed `int8` / `storage8` SSBO shaders directly from host-visible memory on the
Android driver.  It is not a shader replacement, CPU oracle writeback, descriptor
rewrite, or llama.cpp-specific data transformation.

Local evidence completed without ADB:

- `python3 -m unittest tests.test_gpu_abi_contract` passed.
- `bash scripts/build-gpu-shim.sh` passed.
- `bash scripts/build-native-termux.sh` passed.
- `./gradlew :app:assembleCompatDebug` passed.

When ADB is available again, the first device run should be the existing strict
ngl=1 compare with these additional environment variables:

- `PDOCKER_GPU_STRICT_PASSTHROUGH=1`
- `PDOCKER_GPU_STRICT_DEVICE_LOCAL_STAGING=1`
- `PDOCKER_GPU_CPU_ORACLE=1`
- `PDOCKER_GPU_DISPATCH_PROFILE_RESPONSE=1` only if the compact oracle still
  cannot identify the Q6_K transition.

Pass condition: the Q6_K `mul-mat-vec-q6-k-large` CPU oracle changes from
`mismatch` to `match`, and the API prompt check still returns the CPU baseline
answer.  If Q6_K still mismatches with device-local staging, the next target is
honest capability negotiation or a driver-level minimal reproduction for the
native Q6_K SPIR-V module.

### Q6_K Workgroup-Shape Hardening (2026-05-13)

The active Q6_K mismatch was narrowed to a workgroup-shape hazard rather than a
quantization-layout conversion.  Some llama.cpp Vulkan kernels provide
`LocalSize` through specialization constants.  The bridge previously had a
compatibility materialization path that only copied `SpecId 0` into the literal
SPIR-V `LocalSize`, which can collapse a `32x2x1` shader into `32x1x1`.

The executor now treats this as an API contract issue:

- materialized SPIR-V copies `SpecId 0`, `SpecId 1`, and `SpecId 2`;
- strict passthrough refuses to dispatch if the literal and resolved local sizes
  disagree;
- invocation count calculation is overflow-checked and capped at Vulkan's 1024
  local-invocation limit;
- Q6_K oracle evidence records `q6_local_size`, `q6_local_invocations`, and a
  64-lane shader-like diagnostic sum so the next device run can distinguish a
  true math mismatch from a half-workgroup execution.

Local evidence:

- `python3 -m unittest tests.test_gpu_abi_contract` passed.
- `bash scripts/build-native-termux.sh` passed.
- `./gradlew :app:assembleCompatDebug` passed.
- The compat debug APK was installed on `10.79.130.150:35389`.
- Device preflight refused the heavy llama run before daemon startup and wrote
  `docs/test/llama-gpu-workgroup3d-preflight-20260513.json` because the device
  had only about 21 MiB of free swap after the previous model-start attempt.
  No llama container was started by that guarded run.

Device llama comparison was intentionally stopped from this step because the
device was memory-constrained.  A short forced-GPU attempt showed that model
startup can consume the remaining swap before the HTTP server becomes reachable.
The compare script now has a stricter preflight gate (`SwapFree >= 1024 MiB` by
default), stale-target cleanup for `pdocker-llama-cpp`, an Engine start timeout,
and a runtime watchdog.  It will record memory-pressure evidence instead of
pushing Android toward LMK/OOM.  The thresholds remain overridable for
controlled experiments, but benchmark evidence should use the defaults.

Next pass condition: on the next `ngl=1` strict run, the JSON event for
`mul-mat-vec-q6-k-large` must show `spirv_local_size_resolved:[32,2,1]`,
`spirv_local_size_consistent:true`, and either `cpu_oracle_status:"match"` or a
new mismatch whose `q6_shader_like_64_abs_delta` rules out the collapsed
workgroup-shape hypothesis.

### GPU Bridge Documentation/Test Synchronization (2026-05-13)

This synchronization pass did not change llama.cpp, Dockerfiles, the model, the
prompt probes, native C, or the Android runtime.  It only tightened the
documentation and host regression tests around the current evidence.

RoPE/Yarn oracle is evidence-backed:

- hash: `0xac41e8033a67af4a`
- artifact: `docs/test/llama-gpu-ngl1-rope-yarn-oracle-20260509.json`
- `cpu_oracle.kernel_hint`: `rope-yarn`
- `executed`: `true`
- `status`: `match`
- `compared_floats`: `4096`
- `mismatch_count`: `0`

The RoPE/Yarn stage is therefore closed as a regression-protected baseline, not
as the current active blocker.  The active blocker remains Q6_K strict
passthrough / workgroup / Android device-execution semantics for
`0x274f68a67dfef210`.  Any future performance claim must still be blocked until
that path has passing correctness evidence and the device memory-readiness gate
allows a heavy compare run to start.

Environment propagation parity is now explicitly documented and guarded by
`tests.test_gpu_abi_contract`:

- UI/compose runtime defaults must keep production-safe Vulkan/Q6_K settings in
  `_gpu_env(state)` so a normal compose launch does not silently differ from
  the scripted compare route for core limits and Q6_K toggles.
- The compare script may additionally forward diagnostic knobs such as
  `PDOCKER_GPU_CPU_ORACLE`, `PDOCKER_GPU_STRICT_PASSTHROUGH`,
  `PDOCKER_GPU_STRICT_DEVICE_LOCAL_STAGING`,
  `PDOCKER_GPU_LEGALIZE_WORKGROUP_SIZE_FROM_SPEC`,
  `PDOCKER_GPU_RETRY_MATERIALIZE_SPECIALIZATION`,
  `PDOCKER_GPU_SKIP_UNUSED_DESCRIPTOR_TRANSFERS`,
  `PDOCKER_GPU_USE_SPIRV_DESCRIPTOR_ACCESS`,
  `PDOCKER_VULKAN_DISABLE_16BIT_STORAGE`, and
  `PDOCKER_VULKAN_SUBGROUP_SIZE`.
- If one of those diagnostic knobs becomes required for ordinary correctness,
  it must be promoted into `_gpu_env(state)` rather than remaining only in the
  ad-hoc compare script.

### Q6_K Oracle Claim Gate Tightening (2026-05-15)

This synchronization pass changed only pdocker GPU bridge scripts, host tests,
and documentation.  It did not modify llama.cpp, Dockerfiles, the model, prompt
probes, native bridge kernels, or Android runtime binaries.

The compare artifact now carries a bounded Q6_K boundary classification under
`gpu.diagnostics.q6_workgroup_diagnostics.blocker_class`.  The classification
is derived from existing dispatch evidence: Q6_K local-size consistency,
read-only upload/dispatch hash stability, writable output binding hashes, the
first sampled oracle mismatch, and whether the shader-like 32/64-lane CPU
diagnostic agrees with the canonical Q6_K oracle.  This keeps the next strict
passthrough run from reporting an undifferentiated sampled mismatch when the
actual boundary is descriptor effective range/upload, read-only mutation or
barrier scope, Vulkan device execution/writeback, or Q6_K arithmetic/reduction.

The artifact verifier is also stricter: an HTTP prompt pass cannot by itself
authorize a correctness or benchmark claim when the Q6_K oracle still reports
`latest_status: "mismatch"`.  Correctness claims now require both the standard
prompt evidence and a Q6_K workgroup-cleared oracle match.

### Q6_K Push-Layout Oracle Tightening (2026-05-15)

The bounded Q6_K oracle now follows the observed `mul_mat_vec_q6_k` push layout
more closely instead of treating every mismatch as shader/device arithmetic:

- push constant index 7 is decoded as the accumulator mask;
- push constant index 8 is decoded as the base workgroup/batch offset;
- output samples use the derived output base index;
- weight samples use the derived batch-row block base;
- when accumulator bits are set, bindings 3 and/or 4 are included in the
  expected value, or the oracle fails closed with a precise missing/read error.

This does not claim Q6_K correctness.  It removes an oracle-side ambiguity so
the next device artifact can distinguish a true Vulkan execution/writeback or
Q6_K reduction mismatch from a stale push-constant interpretation.

### Q6_K Writeback Boundary Diagnostic (2026-05-15)

The executor binding report now emits `writeback_verified` and
`writeback_mismatch` for writable bindings when profile hash evidence is
available.  The compare summarizer folds those fields into
`gpu.diagnostics.q6_workgroup_diagnostics` as
`q6_writable_writeback_mismatches`, `q6_writable_writeback_unknown`, and
`q6_writeback_verified_all`.

This is diagnostic-only and still does not claim device correctness.  It
narrows the existing `vulkan-device-execution-or-writeback` Q6_K blocker:
hash-stable writable output writeback lets the next artifact name
`vulkan-device-execution`; a writable hash mismatch names `writeback`.

The artifact verifier also consumes this split fail-closed.  A future
`latest_status: "match"` Q6_K oracle is not enough for a correctness or
benchmark claim unless `q6_writeback_verified_all` is true.  Writable
`q6_writable_writeback_mismatches` classify as `q6-writeback-mismatch`; missing
or unknown writable writeback hashes classify as `q6-writeback-unverified`.

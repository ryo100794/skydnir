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

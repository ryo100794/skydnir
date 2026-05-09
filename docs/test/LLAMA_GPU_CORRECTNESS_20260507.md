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
- Compare the final-projection output buffer before sampling, after dispatch,
  and after writeback under the three now-real modes: default, no
  specialization materialization, and pipeline optimization enabled.
- Inspect the final projection shader itself. The current dump shows duplicate
  `Binding 0` storage-buffer variables with different struct views; descriptor
  rewrite and aliasing are present, but the remaining failure may be in
  specialization/local-size lowering, feature advertisement, or shader memory
  visibility rather than in descriptor delivery.
- Keep performance claims blocked while
  `gpu.correctness.summary.benchmark_claim_allowed` is false.

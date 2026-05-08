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

The NGL=0 control also does not satisfy the arithmetic probe, so the absolute
math prompt is not strong enough as the only correctness oracle. However, the
NGL=0 output shape (`3`, `7`, empty) differs sharply from the NGL>=1 output
shape (`!`, `!`, `!!!!`). The next correctness gate should therefore compare
GPU output against a same-model no-offload control for the same prompt, not only
against a hard-coded arithmetic answer.

## Next Actions

- Add bounded binding checksums around the final projection dispatch and compare
  output buffer bytes against the CPU/no-offload control.
- Inspect the final projection shader itself. The current dump shows duplicate
  `Binding 0` storage-buffer variables with different struct views; descriptor
  rewrite and aliasing are present, but the remaining failure may be in
  specialization/local-size lowering, feature advertisement, or shader memory
  visibility rather than in descriptor delivery.
- Keep performance claims blocked while
  `gpu.correctness.summary.benchmark_claim_allowed` is false.

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

## Results

| Artifact | NGL | Variant | GPU tok/s | Speedup vs CPU baseline | Correctness | Probe outputs |
| --- | ---: | --- | ---: | ---: | --- | --- |
| `llama-gpu-compare-20260507-ngl0-correctness-control.json` | 0 | Vulkan path, no GPU layers | 0.2373 | 0.66x | fail | `3`, `7`, empty |
| `llama-gpu-compare-20260507-ngl1-correctness-gate.json` | 1 | Default bridge settings | 0.2300 | 0.64x | fail | `!`, `!`, `!!!!` |
| `llama-gpu-compare-20260507-ngl1-no-materialize.json` | 1 | SPIR-V specialization materialization disabled | 0.2858 | 0.79x | fail | `!`, `!`, `!!!!` |
| `llama-gpu-compare-20260507-ngl4-correctness-gate-rerun.json` | 4 | Default bridge settings | 0.1524 | 0.42x | fail | `!`, `!`, `!!!!` |
| `llama-gpu-compare-20260507-ngl4-no-skip-correctness.json` | 4 | Descriptor transfer skipping disabled | 0.1574 | 0.44x | fail | `!`, `!`, `!!!!` |

`llama-gpu-compare-20260507-ngl1-no-dup-rewrite.json` is not included in the
evidence table because adb went offline during that run, so the result is
incomplete.

## Interpretation

The correctness failure is now reproducible and is not explained by adb
forwarding. Disabling descriptor-transfer skipping did not restore correctness,
and disabling SPIR-V specialization materialization did not restore
correctness. The NGL=1 result fails even though only the output layer is
offloaded, which points at the generic Vulkan dispatch path for the final
projection/logits path rather than at deeper repeating transformer layers.

The NGL=0 control also does not satisfy the arithmetic probe, so the absolute
math prompt is not strong enough as the only correctness oracle. However, the
NGL=0 output shape (`3`, `7`, empty) differs sharply from the NGL>=1 output
shape (`!`, `!`, `!!!!`). The next correctness gate should therefore compare
GPU output against a same-model no-offload control for the same prompt, not only
against a hard-coded arithmetic answer.

## Next Actions

- Add differential CPU/no-offload vs GPU/offload correctness comparison for the
  same prompts and model path.
- Re-run NGL=1 with duplicate descriptor rewrite disabled after adb reconnects.
- If duplicate rewrite is not the cause, trace the final projection dispatch
  with full binding details and compare output buffer bytes against the
  no-offload control.
- Keep performance claims blocked while
  `gpu.correctness.summary.benchmark_claim_allowed` is false.

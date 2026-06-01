# Q6_K Lane Trace Result - 2026-06-01

This note records the current Skydnir llama.cpp Vulkan bridge Q6_K diagnostic result.
It does not modify llama.cpp, the Dockerfile, the model, or the prompt.

## Device run

- Device endpoint used: `192.168.179.21:38235`
- APK variant: `compatDebug`
- Probe bundle: `/tmp/q6-lane-probe-test2`
- Local artifact: `docs/test/llama-gpu-ngl1-q6-lane-colrow-20260601T134026Z.json` (ignored large artifact)
- Probe source hash: `0x1bf751845c5dce75`
- Instrumented probe hash: `0x48243d12c80567dd`
- Effective executor hash: `0xc513b2a26aa63ec5`

## Result

The latest run still fails the deterministic `/completion` prompt check, so the GPU path is not correct yet.
The useful diagnostic conclusion is narrower than before:

1. Final output writeback is not the active fault.
2. Reduction lane 0 equals the per-lane pre-reduction sum for the same `col,row` cell in the captured samples.
3. The per-lane pre-reduction values already differ from the CPU oracle partial values.

Therefore the active fault is before reduction: Q6_K per-lane partial arithmetic/decode, not final store, output writeback, or reduction visibility.

## Representative sample

For `dst_index=151935`, workgroup `[1186,0,63]`, `col=0`, `row=1`:

| Metric | Value |
|---|---:|
| GPU final store | `1.22704947` |
| GPU pre-reduction lane sum | `1.22704935` |
| CPU oracle expected | `6.38452625` |
| CPU oracle partial sum | `6.38452630` |

First lanes:

| Lane | GPU pre-reduction | CPU oracle partial | Delta |
|---:|---:|---:|---:|
| 0 | `-0.18457527` | `2.57123556` | `-2.75581083` |
| 1 | `-0.14672443` | `0.14023365` | `-0.28695808` |
| 2 | `-0.16266681` | `0.02055413` | `-0.18322094` |
| 3 | `-0.13752332` | `0.17304046` | `-0.31056378` |
| 4 | `0.83652103` | `0.22444451` | `0.61207652` |
| 5 | `-0.15021977` | `0.09867637` | `-0.24889614` |
| 6 | `0.42900425` | `-0.66613674` | `1.09514099` |
| 7 | `-0.24013494` | `-1.42141908` | `1.18128414` |

## Next implementation target

Add a static/dataflow-driven probe around the Q6_K partial arithmetic inputs for selected lanes:

- decoded Q6 weight value before multiplication,
- vector input value,
- product term / accumulation contribution,
- enough loop coordinates to map the term back to `block_index` and element lane.

The goal is to classify whether the mismatch comes from Q6_K quantized-weight decode, vector input addressing, or accumulation arithmetic.

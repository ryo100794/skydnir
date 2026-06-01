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

## Evidence contract for lane-trace runs

Lane-trace evidence is only comparable when the debug SSBO and the targeted
SPIR-V probe are pinned explicitly.  The lane trace writes slots `128..655`, so
the minimum required debug SSBO allocation is `2624` bytes (`656 * 4`).  For
actual device runs keep using `65536` bytes to leave headroom for additional
probe fields and to avoid silent truncation when the trace format grows.

Required environment evidence:

| Variable | Required evidence value / meaning |
|---|---|
| `PDOCKER_GPU_SPIRV_PROBE_DEBUG_BYTES` | Must be at least `2624`; use `65536` in operational runs. |
| `PDOCKER_GPU_SPIRV_PROBE_DEBUG_SET` | Descriptor set for the debug SSBO; current lane-colrow run used `0`. |
| `PDOCKER_GPU_SPIRV_PROBE_DEBUG_BINDING` | Descriptor binding for the debug SSBO; current lane-colrow run used `5`. |
| `PDOCKER_GPU_SPIRV_PROBE_TARGET_ONLY` | Must be enabled (`1`) so non-target shaders are not instrumented. |
| `PDOCKER_GPU_SPIRV_PROBE_EXPECTED_HASH` | Must identify the source shader being targeted; current source hash was `0x1bf751845c5dce75`. |
| `PDOCKER_GPU_SPIRV_PROBE_EFFECTIVE_HASH` | Must identify the instrumented/effective probe accepted by the executor; current instrumented probe hash was `0x48243d12c80567dd`. |

Do not treat a lane trace as valid root-cause evidence if any of these values
are missing from the run JSON/runtime environment, if `DEBUG_BYTES < 2624`, or
if the expected/effective hash pair does not match the intended Q6_K probe.

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

## Current conclusion and next implementation target

The current conclusion is that the remaining Q6_K fault is on the
pre-reduction/lane-partial side.  It is not currently attributed to
final-store, reduction, or writeback behavior.

The next compatibility lowering to implement is the scalar `uint32` to
`u8vec4` lowering used by the Q6_K decode path.  Replace the scalar
`uint32 -> u8vec4` `OpBitcast` pattern with an explicit byte extraction
sequence: shift the 32-bit word, truncate each shifted byte to 8 bits, then
construct the `u8vec4` composite.  This keeps the decode byte layout explicit
for drivers that mishandle the scalar-to-vector bitcast form.

After that lowering, rerun the lane trace under the evidence contract above and
compare the same pre-reduction lane partials against the CPU oracle.

## Further diagnostic target

Add a static/dataflow-driven probe around the Q6_K partial arithmetic inputs for selected lanes:

- decoded Q6 weight value before multiplication,
- vector input value,
- product term / accumulation contribution,
- enough loop coordinates to map the term back to `block_index` and element lane.

The goal is to classify whether the mismatch comes from Q6_K quantized-weight decode, vector input addressing, or accumulation arithmetic.

## Follow-up run after `uint32 -> u8vec4` lowering

Artifact: `docs/test/llama-gpu-ngl1-u8vec4-lowered-20260601T143613Z.json` (ignored large artifact).

Result summary:

| Item | Value |
|---|---:|
| GPU served | `true` |
| Container health | `healthy` |
| GPU layers | `1` |
| CPU baseline | `0.36100235915041706 tok/s` |
| GPU measured | `0.05301367782045291 tok/s` |
| CPU-relative speed | `0.1468513334517129x` |
| Q6 latest status | `match` |
| Q6 mismatch count | `0` |
| Q6 storage16 loads lowered | `true` / `24` |
| Q6 `uint32 -> u8vec4` bitcasts lowered | `true` / `16` |
| Q6 safe kernel | `false` |

Correctness probes:

| Probe | Required | Result | Note |
|---|---:|---|---|
| `2+3=` | yes | pass | returned `5` |
| `12*7=` | no | pass | returned prefix `8` |
| `Repeat exactly: pdocker-ok` | no | fail | empty completion; not a required arithmetic correctness failure |

Interpretation:

- The Q6_K native pass-through path now reaches a deterministic `match` for the sampled Q6 oracle evidence while using the generic SPIR-V bridge, not the Q6 safe-kernel fallback.
- The implemented lowering is therefore a correctness fix for the Android driver-sensitive packed-byte decode form.
- Remaining blocker has moved from Q6 arithmetic correctness to performance: upload/copy overhead keeps the GPU path below CPU throughput for this short benchmark.

## Strict read-only resident cache follow-up

Artifact: `docs/test/llama-gpu-ngl1-strict-resident2-20260601T151917Z.json` (ignored large artifact).

Result summary:

| Item | Value |
|---|---:|
| GPU served | `true` |
| Q6 latest status | `match` |
| Q6 mismatch count | `0` |
| Q6 binding 0 resident | `true` |
| Q6 binding 0 cache hit | `true` |
| Q6 binding 0 bytes | `510504960` |
| GPU measured | `0.04785254410802271 tok/s` |
| CPU-relative speed | `0.13255465759458993x` |

Interpretation:

- The strict read-only resident cache now avoids re-reading the large Q6 model/weight binding for repeated dispatches while preserving descriptor offset semantics.
- This did not improve the short `n_predict=4` end-to-end benchmark.  The remaining bottleneck is therefore not only the large binding upload; it is dominated by per-dispatch fixed overhead in the generic SPIR-V bridge path.
- Next target: reduce command/descriptor/object-graph lifecycle overhead and add timing evidence precise enough to separate fixed dispatch overhead from model-buffer transfer overhead.

### Phase timing profiler follow-up (2026-06-01T16:37Z)

Artifact: `docs/test/llama-gpu-ngl1-phase-timing5-20260601T163714Z.json`.

The generic SPIR-V compact response now includes `phase_timing_ms` in both the profile response and the normal response. This closes the previous blind spot where `upload_ms`, `dispatch_ms`, and `download_ms` were visible but the fixed setup cost was hidden.

Observed NGL=1 result:

- Correctness required probes: passed (`addition`, `multiplication_prefix`).
- Optional `identity_text`: still failed with an empty/unsuitable completion, not a benchmark blocker.
- GPU TPS: `0.04815133389426574`.
- CPU baseline TPS: `0.36100235915041706`.
- Speedup: `0.1334323053466443x`.
- Target: not met.

Phase timing summary from the artifact:

| phase | mean ms | max ms | interpretation |
|---|---:|---:|---|
| strict_graph | 151.8354 | 941.3154 | Dominant fixed bridge cost. Per-dispatch strict object graph materialization remains the first optimization target. |
| fence_wait | 6.0104 | 32.8595 | Actual GPU queue wait is small compared with strict graph creation. |
| cpu_oracle | 2.0739 | 230.6380 | Diagnostic correctness oracle cost exists but is not the dominant mean cost. Disable for final benchmark once enough evidence is retained. |
| pipeline_create | 0.5535 | 452.3575 | Cache misses are expensive but mean is low; keep pipeline cache but do not prioritize before strict graph reuse. |
| queue_submit | 0.1336 | 2.9014 | Not the primary blocker. |
| command_record | 0.0750 | 26.3431 | Secondary after strict graph. |
| descriptor_pool | 0.0574 | 5.2413 | Secondary after strict graph. |

Conclusion: Q6 correctness is still intact, and the resident read-only cache alone is not enough. The current first performance blocker is repeated strict object-graph materialization, not shader arithmetic and not queue wait.

Next optimization target: strict object graph reuse/pooling for immutable/read-only memory and stable VkBuffer/VkDeviceMemory skeletons, with descriptor offsets preserved exactly.

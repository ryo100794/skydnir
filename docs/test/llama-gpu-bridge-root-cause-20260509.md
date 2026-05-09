# llama GPU bridge investigation notes - 2026-05-09

## Scope

This note records the current correctness investigation for the built-in `llama-cpp-gpu` Vulkan bridge. The llama.cpp source tree, container Dockerfile, model, and prompt were not changed.

## Latest evidence

| Probe | Evidence file | Result |
| --- | --- | --- |
| Disable resident model/weight cache | `docs/test/llama-gpu-ngl1-q6k-no-resident-cache-20260509.json` | Q6_K still mismatches with `resident=false` and `cache_hit=false` for Binding 0. The stale resident cache hypothesis is rejected. |
| Enable normal pipeline optimization | `docs/test/llama-gpu-ngl1-q6k-no-resident-cache-opt-20260509.json` | Q6_K output is unchanged. `VK_PIPELINE_CREATE_DISABLE_OPTIMIZATION_BIT` is not the cause. |
| Disable overlapping fd/buffer alias coalescing | `docs/test/llama-gpu-ngl1-q6k-disable-overlap-aliasing-20260509.json` | Q6_K output is unchanged and `rw_alias_hazards.count=0`. Read/write alias coalescing is not the cause. |
| Disable advertised storage8/int8 support | `docs/test/llama-gpu-ngl1-q6k-disable-storage8-20260509.json` | The bridge correctly reports missing `int8,storage8`; this is a feature-policy blocker, not a correctness fix. |

## Current narrow point

The first Q6_K dispatch has:

- SPIR-V hash: `0x09c4622d92c6acb9`
- Dispatch: `[1187, 1, 64]`
- Specialization constants: local size 32, `NUM_ROWS=2`, `NUM_COLS=1`
- Binding 0 upload hash equals fd hash after resident cache is disabled
- CPU oracle variants agree with the shader-like Q6_K row oracle within float tolerance
- GPU output still differs, for example row 0 expected `13.8780231` but GPU wrote `6.83085108`

Therefore the mismatch is now isolated past the container/ICD data transfer layer and into the Android Vulkan execution of the Q6_K shader path, most likely one of:

1. local-size / specialization / `gl_WorkGroupSize` semantics as seen by the driver,
2. workgroup shared-memory reduction semantics,
3. 8-bit / 16-bit storage load semantics for the mixed Q6_K byte and packed16 views,
4. a bridge-owned pipeline-creation detail that changes how the driver compiles this shader.

## Next diagnostic step

Add a targeted Q6_K micro-dispatch probe that does not run the full model. It should run the same shader hash and bindings against a captured small row window, then compare:

1. full Q6_K shader output,
2. forced local-size variants,
3. reduction-only shader output,
4. byte-view versus packed16-view loads,
5. a bridge-owned CPU oracle writeback mode for Q6_K only.

The pass/fail condition is simple: if CPU oracle writeback makes the downstream llama comparison pass, the remaining issue is inside this one GPU shader execution path rather than surrounding tensor plumbing.

## Q6_K oracle writeback conclusion

A gated diagnostic was added with `PDOCKER_GPU_Q6K_ORACLE_WRITEBACK=1`. It rewrites the sampled Q6_K output rows with the bridge-side CPU oracle result after the Android Vulkan dispatch and before fd download.

Evidence file:

- `docs/test/llama-gpu-ngl1-q6k-oracle-writeback-20260509.json`

Result:

- All sampled Q6_K oracle events changed from mismatch to match.
- Example event: `oracle_writeback=true`, `oracle_writeback_rows=32`, `status=match`, `mismatch_count=0`.
- The compare script's blocker moved away from correctness and now reports throughput/copy overhead:
  `served through generic SPIR-V, but bridge upload/copy overhead keeps GPU below CPU throughput`.

Conclusion:

The bridge data transport and Q6_K tensor interpretation are sufficient for the sampled rows. The remaining correctness failure is inside the Android Vulkan execution result of the llama.cpp Q6_K shader path, not in model bytes, Dockerfile, fd upload/download, duplicate descriptor binding rewrite, or resident cache reuse.

Next implementation direction:

1. Keep the writeback mode as diagnostic-only and disabled by default.
2. Build a Q6_K micro-dispatch suite to isolate the exact Vulkan primitive: storage8/16 load, local-size specialization, workgroup shared-memory reduction, or barrier semantics.
3. If driver behavior remains incompatible, add a bridge-owned Q6_K safe kernel/fallback behind a hash-gated policy, without modifying llama.cpp.

# llama.cpp GPU Bridge Performance Notes - 2026-05-07

## Scope

This note records the current llama.cpp GPU bridge tuning loop on the Android
compat APK. The upstream llama.cpp sources and the llama container image were
not rebuilt during these runs. Only pdocker APK-side bridge components and the
benchmark driver were changed.

## Device And Mode

- Device connection: `192.168.179.26:37913`.
- Package: `io.github.ryo100794.pdocker.compat`.
- Container image: `pdocker/llama-cpp-gpu:latest`.
- Model: `/models/model.gguf`, Qwen3 8B Q4_K_M.
- GPU path: container Vulkan loader -> `pdocker-vulkan-icd.so` -> APK GPU
  command executor -> Android Vulkan.
- Vulkan clamp values: 512 MiB for max buffer, max allocation, max storage
  range, and ggml suballocation block.

## Changes Under Test

- Generic Vulkan dispatch responses are compact by default.
- Full `binding_details` and SPIR-V feature reports are emitted only when
  `profile=1` or `PDOCKER_GPU_DISPATCH_PROFILE_RESPONSE=1` is selected.
- The llama compare driver enables lightweight
  `PDOCKER_GPU_DISPATCH_PROFILE_LOG=1`, which logs compact dispatch JSON
  without the heavy per-binding profile.

## Results

| Artifact | NGL | Repeat | GPU tok/s | CPU baseline tok/s | Speedup | Upload mean | Dispatch mean | Download mean | Samples |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `llama-gpu-compare-20260507-ngl4-dispatch-profile-log.json` | 4 | 1 | 0.1867 | 0.3610 | 0.52x | 10.664 ms | 3.519 ms | 0.042 ms | 174 |
| `llama-gpu-compare-20260507-ngl4-repeat2-profile-log.json` | 4 | 2 | 0.1896 | 0.3610 | 0.53x | 7.793 ms | 3.386 ms | 0.044 ms | 277 |
| `llama-gpu-compare-20260507-ngl5-profile-log.json` | 5 | 1 | 0.1770 | 0.3610 | 0.49x | 11.306 ms | 4.097 ms | 0.038 ms | 169 |

## Interpretation

The bridge now serves NGL=4 and NGL=5 without correctness or Vulkan submit
failures. NGL=4 offloads 4/37 layers, including 3 repeating transformer layers.
NGL=5 is slower, so the current break-even point on this device is still around
NGL=4.

The compact dispatch profile shows that a single initial upload can dominate
the mean. In the NGL=4 single-repeat run, one dispatch spent about 1.83 seconds
uploading the first large resident input, while the median upload was below
0.1 ms. Steady-state dispatch latency is still material: median dispatch was
about 1.17 ms, with p95 around 10 ms in the captured full log. The remaining
wall time is primarily the CPU side of the model because most layers still run
on CPU.

## Next Tuning Targets

- Add a steady-state benchmark mode that excludes first-request model and
  resident-buffer setup from the throughput summary.
- Keep compact dispatch logging available for compare runs, but avoid enabling
  full binding details unless diagnosing a correctness or allocation failure.
- Investigate persistent registered buffers for large read-only or repeatedly
  reused inputs so the first-dispatch upload cost is amortized across requests.
- Explore NGL 3-5 only for this model/device until the bridge dispatch path is
  faster; higher NGL increases bridge work faster than it reduces CPU work.

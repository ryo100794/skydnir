# llama.cpp Runtime Benchmarks

Snapshot date: 2026-05-09.

## Purpose

This document records repeatable llama.cpp measurements from pdocker Android
runtime runs. Keep the latest machine-readable result in
`docs/test/llama-bench-latest.json` and copy it to the device bench directory
with `scripts/android-llama-bench.sh`.

## Canonical Sources

- GPU bridge design lives in [`../design/GPU_COMPAT.md`](../design/GPU_COMPAT.md).
- Current runtime status lives in [`../plan/STATUS.md`](../plan/STATUS.md).
- Active llama/GPU tasks live in [`../plan/TODO.md`](../plan/TODO.md).
- Machine-readable benchmark artifacts stay in adjacent `*.json` files.
- Latest correctness evidence is recorded in
  `docs/test/llama-correctness-latest.json`.

## How To Run

1. Start the llama.cpp project from the app or Engine compose path.
2. Wait until `http://127.0.0.1:18081/health` returns HTTP 200, and verify
   that `http://127.0.0.1:18081/` serves the upstream llama.cpp browser UI
   rather than a JSON 404.
3. For any GPU-mode result, run `pdocker-llama-correctness` in the running
   container before recording a benchmark claim. The report must show
   `summary.correctness=pass`; `/health` alone is only service liveness.
4. Run:

```sh
bash scripts/android-llama-bench.sh --predict 8 --repeat 1
```

Use the same prompt, token count, and model when comparing CPU fallback with
future Vulkan/CUDA-compatible runs.

For the small-model GPU green path, keep the default 8B file intact by writing
the alternate GGUF to a separate model path:

```sh
SMALL_GGUF_URL=https://.../small.gguf
bash scripts/android-llama-gpu-compare.sh --model-path /models/small.gguf --model-url "$SMALL_GGUF_URL" --gpu-layers 1 --gpu-ctx 512 --predict 2 --repeat 1
```

## 2026-05-09 llama API Smoke Result

A local HTTP API smoke test showed the llama.cpp server was live on
`http://127.0.0.1:18081`, `/health` returned ok, and `/v1/models` reported
`model.gguf` with the expected 8.19B parameter metadata. The test produced the
correct trivial arithmetic result, with prompt processing at 4 tokens in
10709.614 ms and one predicted token. Server-side correctness signals for the
Q6_K safe kernel, RoPE/Yarn, and RMS norm all reported zero mismatches.

The original local note `docs/test/llama-api-prompt-20260509.md` intentionally
remains untracked because it embeds the exact request prompt/body. Keep only
this sanitized summary in versioned docs unless a review explicitly approves
committing prompt-bearing artifacts.

## 2026-05-07 Write-Only Dirty Writeback Probe

Late on 2026-05-07, deterministic `/completion` probes showed that one forced
Vulkan run could serve HTTP while returning incorrect first-token output for
simple prompts (`2+3=` returned `!` at NGL=1 and NGL=4). After restarting the
same built image with 512 MiB Vulkan max-buffer/allocation/suballocation clamps,
the latest NGL=4 probe in `docs/test/llama-correctness-latest.json` returned
`2+3= -> 5` and `12*7= -> 8`. Until `pdocker-llama-correctness` passes for the
current run, GPU artifacts in this section are bridge throughput diagnostics
only and must not be reported as verified inference results.

- Diagnostic probe:
  `docs/test/llama-cpu-gpu-compare-20260507-ngl3-dirty-probe-protocol.json`.
- Partial writeback:
  `docs/test/llama-cpu-gpu-compare-20260507-ngl3-dirty-writeback-cached.json`.
- Partial writeback plus scratch-cache attempt:
  `docs/test/llama-cpu-gpu-compare-20260507-ngl3-dirty-writeback-scratch.json`.
- Scratch-cache protocol forwarding:
  `docs/test/llama-cpu-gpu-compare-20260507-ngl3-scratch-protocol.json`.
- Warm scratch-cache protocol run:
  `docs/test/llama-cpu-gpu-compare-20260507-ngl3-scratch-protocol-warm.json`.
- Warm scratch-cache no-trace run:
  `docs/test/llama-cpu-gpu-compare-20260507-ngl3-scratch-protocol-notrace.json`.
- NGL=4 current scratch protocol:
  `docs/test/llama-cpu-gpu-compare-20260507-ngl4-scratch-protocol.json`.
- NGL=6 current scratch protocol:
  `docs/test/llama-cpu-gpu-compare-20260507-ngl6-scratch-protocol.json`.
- CPU baseline: 0.056159 tok/s.
- Policy: llama.cpp was not modified; all changes are in the pdocker Vulkan ICD,
  APK GPU executor, and benchmark parser.

| Run | NGL | Predict | GPU Speed | Speedup vs CPU | Main Evidence |
| --- | ---: | ---: | ---: | ---: | --- |
| Dirty probe only | 3 | 2 | 0.1331 tok/s | 2.37x | 319,553,536B write-only binding dirtied only 32-96 KiB, but full scan cost 418-498ms |
| Dirty writeback cached | 3 | 4 | 0.0915 tok/s | 1.63x | cached partial writeback reduced repeated 319,553,536B downloads to 0.09-0.20ms |
| Dirty writeback + scratch flag | 3 | 4 | 0.1038 tok/s | 1.85x | downloads stayed low, but 319,553,536B buffer acquisition remained 451-542ms |
| Scratch options forwarded | 3 | 4 | 0.1310 tok/s | 2.33x | ICD now forwards scratch/max-cache options to the APK executor; repeated 319,553,536B upload/download reached ~0.001ms / 0.06-0.08ms |
| Warm scratch options | 3 | 8 | 0.1179 tok/s | 2.10x | same executor process reused dirty and scratch state from the start; 319,553,536B upload/download stayed ~0.001ms / 0.06-0.18ms |
| Warm scratch, no trace | 3 | 8 | 0.1161 tok/s | 2.07x | throughput remains around 2x even without allocation trace logging |
| NGL=4 scratch options | 4 | 4 | 0.0607 tok/s | 1.08x | repeated dispatch count and graph splits increased; no large dirty-writeback wins appeared |
| NGL=6 scratch options | 6 | 4 | 0.0987 tok/s | 1.76x | several 319MiB bindings became true full-range read/write traffic; partial dirty writeback cannot reduce those |

Conclusion: the large write-only transfer path is no longer the dominant
blocker once the executor is warm. The next hotspot has moved toward generic
SPIR-V dispatch and remaining per-dispatch setup overhead. The first large
resident model binding still has a one-time ~1.1-1.6s upload cost, but repeated
319,553,536B scratch/write-only buffers now reuse executor-side state.

For NGL=4 and above, the main limit is not simply the number of layers copied to
Vulkan. The scheduler introduces more GPU/CPU boundary traffic and some
319MiB-class bindings are genuinely full-range readable or full-range dirty.
Those buffers need a device-resident bridge protocol so GPU-produced tensors can
remain GPU-owned across subsequent dispatches instead of being materialized back
through the container-visible file descriptor after each command.

## 2026-05-07 CPU/GPU Comparison After Descriptor Transfer Skip

- Local full comparison: `docs/test/llama-cpu-gpu-compare-20260507-full.json`.
- Local NGL=1 comparison: `docs/test/llama-cpu-gpu-compare-20260507-ngl1.json`.
- Device copies: `files/pdocker/bench/llama-cpu-gpu-compare-20260507-full.json`
  and `files/pdocker/bench/llama-cpu-gpu-compare-20260507-ngl1.json`.
- Model: Qwen3 8B GGUF, Q4_K_M, `/models/model.gguf`, 8.19B parameters.
- Policy: llama.cpp was not modified. The container uses the standard Vulkan
  loader through `pdocker-vulkan-icd.so`.
- Measurement: HTTP `/completion`, prompt `Hello`, `n_predict=4`, repeat 1.
- Driver fix made during this run: the compare script now binds `/models` to
  the same host model directory as the project-library Compose template
  (`files/pdocker/models/llama-cpp-gpu`) instead of the stale project-local
  `models` directory.

| Mode | Served | Offload Evidence | Generation Speed | Speedup vs CPU | Result |
| --- | --- | --- | ---: | ---: | --- |
| CPU baseline | Yes | CPU only | 0.0562 tok/s | 1.00x | Baseline |
| Vulkan NGL=1 | Yes | output layer only, 1/37 layers | 0.1153 tok/s | 2.05x | Functional but far below 10x |
| Vulkan NGL=2 | No | output + 1 repeating layer, 2/37 layers | 0.0000 tok/s | 0.00x | Fails during warmup |

NGL=1 currently proves that the container can load the model through the pdocker
Vulkan bridge and produce a measurable HTTP completion, but it offloads only the
output layer. NGL=2 reaches model load and assigns one repeating transformer
layer to Vulkan, then fails during warmup with `vk::Queue::submit:
ErrorFeatureNotPresent`.

Current blocker: the next layer-depth step is not throughput-bound yet; it is a
Vulkan feature/dispatch correctness issue around the generic SPIR-V submit path.
The next implementation target is to map the failing SPIR-V capabilities and
descriptor alias shape to the Android Vulkan executor, then either lower that
shader correctly or clamp the glibc-facing advertised capabilities so llama.cpp
chooses a supported path.

## 2026-05-07 Member-Decorated Access Fix Result

- Local NGL=2 result:
  `docs/test/llama-cpu-gpu-compare-20260507-ngl2-after-member-access.json`.
- Local NGL=3 result:
  `docs/test/llama-cpu-gpu-compare-20260507-ngl3-after-member-access.json`.
- Local NGL=6 result:
  `docs/test/llama-cpu-gpu-compare-20260507-ngl6-after-member-access.json`.
- Change tested: the APK-side Vulkan executor now recognizes
  `OpMemberDecorate ... NonWritable/NonReadable` on storage-buffer block
  members, not only variable-level `OpDecorate`. This lets the bridge skip
  read-only downloads and write-only uploads for real llama.cpp SPIR-V.

| Mode | Served | Offload Evidence | Generation Speed | Speedup vs CPU |
| --- | --- | --- | ---: | ---: |
| CPU baseline | Yes | CPU only | 0.0562 tok/s | 1.00x |
| Vulkan NGL=1 | Yes | output layer only, 1/37 layers | 0.1153 tok/s | 2.05x |
| Vulkan NGL=2 | Yes | output + 1 repeating layer, 2/37 layers | 0.1168 tok/s | 2.08x |
| Vulkan NGL=3 | Yes | output + 2 repeating layers, 3/37 layers | 0.1222 tok/s | 2.18x |
| Vulkan NGL=6 | Yes | output + 5 repeating layers, 6/37 layers | 0.0950 tok/s | 1.69x |

The previous NGL=2 warmup crash is closed by the member-decoration access fix.
More layers now run, but NGL=6 regresses throughput, so the next priority is
not simply increasing layer count. The bridge needs per-dispatch transfer/copy
profiling with tracing enabled on a short run, then tuning around the layer
depth that minimizes boundary traffic. Current best short result is NGL=3 at
2.18x CPU, still below the 10x target.

Trace run:

- Local path: `docs/test/llama-cpu-gpu-compare-20260507-ngl3-trace.json`.
- Copy-profile path:
  `docs/test/llama-cpu-gpu-compare-20260507-ngl3-copy-profile.json`.
- Scenario: NGL=3, context 2048, `n_predict=2`, trace allocation enabled.
- Result: GPU 0.1909 tok/s, 3.40x vs the current CPU baseline, still below
  the 10x target.
- Bridge profile: 179 generic SPIR-V samples, mean upload 17.56 ms, mean
  dispatch 3.52 ms, mean download 7.01 ms, plus 566 copy-buffer operations
  covering about 772 MB in the captured log excerpt.
- Evidence from the largest sampled dispatch: a 510,504,960-byte binding is
  resident and cache-hit, but copy-buffer traffic and mutable activation
  transfers remain large enough to dominate. The next implementation target is
  to keep repeated copy sources/destinations registered across submissions and
  batch transfer-only command buffers so they stop crossing the APK/container
  boundary every token.
- Follow-up copy profile: GPU 0.1199 tok/s, 2.13x vs CPU with trace enabled.
  The ICD recorded 566 copy submits: 565 were alias-only, 1 performed a real
  16 KiB `memmove`, and 0 were skipped. This rules out host-side
  `vkCmdCopyBuffer` replay as the current dominant cost. The remaining target
  is generic dispatch transfer overhead, especially repeated mutable-buffer
  upload/download and per-dispatch synchronization across the container/APK
  bridge. Newer trace artifacts also record guarded-memory resident/dirty byte
  summaries so the next V3 dispatch protocol can be measured against page-span
  transfer targets instead of whole binding ranges.
- Pipeline optimization probe:
  `docs/test/llama-cpu-gpu-compare-20260507-ngl3-pipeline-opt.json` measured
  0.1436 tok/s, 2.56x vs CPU, with
  `PDOCKER_GPU_DISABLE_PIPELINE_OPTIMIZATION=0`. The traced companion run
  `docs/test/llama-cpu-gpu-compare-20260507-ngl3-pipeline-opt-trace.json`
  measured 0.1253 tok/s, 2.23x vs CPU under trace overhead. The llama project
  template now defaults this setting to `0` while leaving an env opt-out for
  devices where Android pipeline optimization proves unstable.
- Guarded-memory trace:
  `docs/test/llama-cpu-gpu-compare-20260507-ngl3-guarded-trace.json` measured
  0.1668 tok/s, 2.97x vs CPU. The trace recorded 885 guarded binding samples,
  with a maximum guarded range/resident/dirty size of 510,504,960 bytes. Dirty
  bytes currently mean first-touched guarded pages, not precise post-dispatch
  write spans. This confirms the virtual-memory guard is active for the large
  bridge allocation, and also confirms the next speed target is a V3 dispatch
  protocol that sends dirty page spans instead of whole mutable binding ranges.
- Binding timing trace:
  `docs/test/llama-cpu-gpu-compare-20260507-ngl3-binding-timing.json` adds
  per-binding upload/download timing. The heaviest one-time upload is the
  510,504,960-byte resident model binding. The recurring bottleneck is a
  write-only 319,553,536-byte binding: it costs hundreds of milliseconds to
  allocate and then hundreds more to write back as a whole range. An opt-in
  write-only buffer cache probe was also recorded:
  `docs/test/llama-cpu-gpu-compare-20260507-ngl3-writeonly-cache.json` and
  `docs/test/llama-cpu-gpu-compare-20260507-ngl3-writeonly-cache-512m.json`.
  The first version did not improve this llama path because the large
  write-only binding uses changing offsets, so fd/offset cache keys do not
  repeat. A follow-up scratch-key experiment
  `docs/test/llama-cpu-gpu-compare-20260507-ngl3-writeonly-scratch-512m.json`
  measured 0.1535 tok/s, 2.73x vs CPU, and lowered mean upload time from
  4.39 ms to 3.90 ms. That is useful but still below the guarded-memory
  trace's 2.97x result, so the cache remains opt-in. The next useful
  optimization remains dirty-span download for the large write-only binding.

## 2026-05-05 Copy-Buffer Semantics Probe Result

- Local path: `docs/test/llama-gpu-compare-latest.json`.
- Device path: `files/pdocker/bench/llama-gpu-compare-latest.json`.
- Scenario: `scripts/android-llama-gpu-compare.sh --gpu-layers 1 --gpu-ctx 512 --predict 2 --repeat 1`.
- Policy: llama.cpp was not modified; the container used the standard Vulkan
  loader and pdocker's glibc-facing Vulkan ICD.
- Result: CPU 0.4246 tokens/s, GPU 0.3426 tokens/s, speedup 0.807x,
  `target_met=false`.
- Change tested: `pdocker-vulkan-icd.so` now records `vkCmdCopyBuffer`
  operations on the command buffer and replays them at `vkQueueSubmit`, which
  matches Vulkan command-buffer semantics better than immediate `memmove`.
  This did not yet improve throughput; it clarifies that semantics and
  transport reuse have to be solved together.
- Bridge profile: 184 generic SPIR-V samples, mean upload 6.90 ms, mean
  dispatch 4.03 ms, mean download 0.06 ms, plus 558 logged copy-buffer
  operations covering about 528 MB in the captured log excerpt.
- Current blocker: the executor-side resident cache is real but incomplete.
  The dominant path is copy-buffer staging plus repeated bridge-visible memory
  movement, not generic-dispatch binding upload alone.
- Next action: record `vkCmdCopyBuffer` operations in the command buffer and
  execute them during `vkQueueSubmit` is now complete; next, move repeated
  large copy sources onto reusable/resident bridge handles and batch command
  submission so copy-buffer traffic stops crossing the boundary every token.

## 2026-05-05 Pipeline Cache GPU-Only Probe Result

- Scenario: `scripts/android-llama-gpu-compare.sh --gpu-only --gpu-layers 1
  --gpu-ctx 512 --predict 2 --repeat 1`.
- CPU baseline: reused from the latest JSON at 0.4246 tokens/s.
- Result: GPU 0.3989 tokens/s, speedup 0.939x, `target_met=false`.
- Change tested: the Android Vulkan executor now caches repeated generic
  SPIR-V shader modules, pipeline layouts, descriptor set layouts, and compute
  pipelines by shader hash, entry point, specialization data, layout count, and
  push-constant size.
- Evidence: the captured executor responses include
  `pipeline_cache.hit=true`; the large 26,784-byte specialized dispatch dropped
  to 19.57 ms in the log excerpt.
- Current blocker: upload/copy movement is still dominant. The same run still
  records 558 copy-buffer operations covering about 528 MB in the excerpt, and
  mean upload is 8.23 ms. The next high-impact target is to keep repeated
  read-only/staging buffers resident across dispatches and reduce per-dispatch
  bridge traffic.

## 2026-05-05 Resident Cache Probe Result

- Scenario: same short compare flow before command-buffer copy replay.
- Result: CPU 0.4153 tokens/s, GPU 0.3668 tokens/s, speedup 0.883x,
  `target_met=false`.
- Evidence: the APK-side Android Vulkan executor retained the
  510,504,960-byte model-side generic-dispatch binding for at least one
  dispatch (`resident_bindings=1`, `hits=1`), but most traffic still arrived
  through `vkCmdCopyBuffer` transfer-only command buffers.

## 2026-05-04 Vulkan Bridge Execution Result

- Local path: `docs/test/llama-gpu-compare-latest.json`.
- Device path: `files/pdocker/bench/llama-gpu-compare-latest.json`.
- Device: `10.62.90.13:37669`.
- Model: Qwen3 8B GGUF, Q4_K_M, 8.19B parameters.
- Policy: llama.cpp was not modified; it used the standard Vulkan loader and
  the pdocker glibc-facing Vulkan ICD.
- GPU result: forced Vulkan with `--n-gpu-layers 1` now serves HTTP and
  reaches `Up (healthy)`.
- Evidence: `Vulkan0 model buffer size` was present, offload was reported,
  `main: model loaded` was reached, and generic SPIR-V dispatches returned
  `valid=true` from the APK-owned Android Vulkan executor.
- Short benchmark after `VULKAN_DISPATCH_V2`: CPU 0.1559 tokens/s, GPU
  0.1230 tokens/s, speedup 0.789x, `target_met=false`.
- Bridge overhead phase: `served=true`, `gpu_layers=1`,
  `blocker=served through generic SPIR-V, but bridge upload/copy overhead keeps
  GPU below CPU throughput`.
- Bridge profile: 258 generic SPIR-V samples, mean upload 40.38 ms, mean
  dispatch 4.37 ms, mean download 4.57 ms.
- Current blocker: the bridge is functionally alive but slower than CPU because
  each dispatch copies buffer slices through the executor boundary.
- Next action: reduce bridge upload/copy overhead with persistent registered
  buffers, then rerun with larger `n_predict`.

## 2026-05-03 Vulkan-Requested Result

- Local path: `docs/test/llama-bench-vulkan-requested-repeat3.json`.
- Device path: `files/pdocker/bench/llama-bench-vulkan-requested-repeat3.json`.
- Mode: Vulkan requested through Docker-compatible `gpus: all`.
- Engine state: `PdockerGpu.Modes=["cuda-compat","vulkan"]` during the HTTP
  run; subsequent OpenCL wiring expands this to include `opencl`.
- Container health: `docker ps` reports `Up (healthy)` after the healthcheck
  settles.
- HTTP generation speed: 0.171 tokens/s mean, 0.162 min, 0.185 max.
- Mean wall time: 58.58s for 8 generated tokens.

This is slower than the CPU fallback baseline. The request path and raw
diagnostic exposure are working, but acceleration is not active:
`llama-server` still reports CPU model, KV, and compute buffers. After fixing
the direct runtime COW mis-detection, the Vulkan ICD file is visible in the
container, but `vulkaninfo --summary` fails because Android's
`/system/lib64/libvulkan.so` depends on Android/Bionic libraries such as
`android.hardware.configstore@1.0.so` that are not loadable from the
Ubuntu/glibc process.

Conclusion: directly exposing Android host GPU libraries into the image is a
diagnostic dead end, not the production design. The container-facing side must
remain glibc-compatible and talk to an APK-owned Android/Bionic GPU command
executor through a thin pdocker bridge. The executor is not a host-side
llama.cpp RPC inference service; `llama-server`, model loading, tokenization,
sampling, and HTTP serving stay inside the container. The bridge contract must
be device-independent so benchmarks compare the same LLM workload while the
executor absorbs GLES/Vulkan/OpenCL/vendor differences underneath.

Official `llama-bench` with `-p 16 -n 8 -r 2 -ngl 999 -t 8` is stored at
`docs/test/llama-bench-tool-vulkan-requested-p16-n8-r2.json`:

- Prompt processing: 1.76 tokens/s average.
- Token generation: 0.248 tokens/s average.

## 2026-05-03 OpenCL Probe

OpenCL was probed after the Vulkan check. The device exposes
`/vendor/lib64/libOpenCL.so`, and pdocker now maps an OpenCL request into:

- `PdockerGpu.Modes` containing `opencl`
- `PDOCKER_OPENCL_PASSTHROUGH=1`
- `OCL_ICD_VENDORS=/etc/OpenCL/vendors`
- `/etc/OpenCL/vendors/pdocker-android.icd`

The library is visible inside the container, but `ctypes.CDLL` fails with
`liblog.so: cannot open shared object file`. `readelf -d` shows the Android
OpenCL library depends on Bionic/Android libraries (`liblog.so`,
`libcutils.so`, `libc++.so`, `libc.so`, `libm.so`, `libdl.so`). So the OpenCL
result matches Vulkan: passthrough metadata and file exposure work as
diagnostics, but direct loading from a glibc container is not a working GPU
backend. OpenCL also needs the glibc bridge plus Android/Bionic GPU-executor
model.

## 2026-05-04 pdocker OpenCL ICD Bridge

pdocker now packages a glibc-facing OpenCL bridge as
`pdocker-opencl-icd.so`. For GPU-requesting containers, pdockerd bind-mounts
the same binary as:

- `/usr/local/lib/pdocker-opencl-icd.so`
- `/usr/local/lib/libOpenCL.so`
- `/usr/local/lib/libOpenCL.so.1`

The container ICD file points to the pdocker bridge, not to Android vendor
`libOpenCL.so`. The bridge currently proves the standard OpenCL entry point by
lowering a vector-add command sequence through `PDOCKER_GPU_QUEUE_SOCKET` into
the APK-owned GPU executor. This keeps Android/Bionic GPU libraries below the
bridge and avoids exposing them directly to glibc applications.

Reusable local smoke:

```sh
bash scripts/smoke-opencl-bridge.sh
```

Latest local result:

- `maxErr=0.00000000`
- first output: `1.000`
- last output: `128.875`

Latest device `/system/host` result on 2026-05-04:

- `Frameworks.OpenCL.ApiVersion`: `1.2`
- `Frameworks.OpenCL.IcdKind`: `pdocker-bridge-minimal`
- `Frameworks.OpenCL.IcdReady`: `true`
- `Paths.OpenClIcd.Exists`: `true`

This is still a bridge milestone, not a llama.cpp GPU success. The next work is
expanding OpenCL API and kernel lowering coverage without modifying llama.cpp.

## 2026-05-04 APK Executor OpenCL Attempt

The APK-side GPU executor now tries to run the vector-add bridge through
Android's native OpenCL loader before falling back to GLES compute. This keeps
the unmodified container application path as:

```text
glibc app -> libOpenCL.so / OpenCL ICD -> pdocker GPU command queue -> APK GPU executor
```

On the current device, the native OpenCL loader is present on disk but blocked
from the untrusted app linker namespace:

```text
library "/vendor/lib64/libOpenCL.so" ... is not accessible for the namespace
```

The executor therefore falls back to GLES compute while preserving the
container-facing OpenCL ABI. Latest device result:

```json
{"backend_impl":"gles31_compute","kernel":"vector_add","valid":true}
```

This means the current device can validate the OpenCL-to-pdocker bridge and GPU
execution, but not vendor OpenCL execution from the APK process. The next
generalization target is expanding the pdocker OpenCL ICD lowering layer toward
llama/ggml kernels, with unsupported kernels traced rather than silently
mis-executed.

## 2026-05-04 GPU Executor Boundary Probe

The APK now includes `pdocker-gpu-executor`, a Bionic-side command executor
probe for the future device-independent `pdocker-gpu-command-v1` ABI. This is
not an LLM engine and does not move llama.cpp out of the container.

Local executor capability probe:

- API: `pdocker-gpu-command-v1`.
- ABI version: `0.1`.
- Role: `gpu-command-executor`.
- LLM engine location: `container`.
- Current implementation backend: `gles31_compute`.

Local vector-add self-test on 2026-05-04:

- Kernel: `vector_add`, n=262144.
- compile: 35.2674 ms.
- upload: 2.4036 ms.
- dispatch: 6.6698 ms.
- download: 0.9055 ms.
- total: 45.2464 ms.
- max absolute error: 0.0.
- valid: true.

Installed compat APK executor self-test via `run-as` on 2026-05-04:

- Kernel: `vector_add`, n=262144.
- compile: 24.6921 ms.
- upload: 2.4523 ms.
- dispatch: 21.3822 ms.
- download: 1.7867 ms.
- total: 50.3134 ms.
- max absolute error: 0.0.
- valid: true.

This proves the APK-owned executor can run an Android GPU command behind the
neutral ABI. It does not yet prove container llama.cpp acceleration; the next
step is the glibc shim plus shared-memory command queue used by the container
process.

## 2026-05-04 Container Shim Probe

The Linux/glibc container-facing shim is now built as `pdocker-gpu-shim` and
packaged in the APK as `libpdockergpushim.so`. pdockerd bind-mounts it into
GPU-requesting containers at `/usr/local/bin/pdocker-gpu-shim`.

Local capability output:

```json
{"shim":"pdocker-gpu-shim","api":"pdocker-gpu-command-v1","abi_version":"0.1","llm_engine":"container","device_independent":true,"container_contract":"glibc-shim-command-queue","executor_available":false,"executor_role":"apk-bionic-gpu-command-executor","transport":"command-queue-pending","backend_impl_visible_to_container":false}
```

This confirms the container-visible contract remains device-independent. It is
not an acceleration claim until `transport` changes from
`command-queue-pending` to a validated queue implementation.

## 2026-05-04 GPU Bridge Overhead Probe

The first shim-to-executor transport is now measurable with:

```sh
bash scripts/bench-gpu-bridge.sh 50 docs/test/gpu-bridge-bench-repeat50.json
```

This compares:

- direct APK-side executor loop;
- glibc shim bridge with one socket connection per command;
- glibc shim bridge with a persistent socket connection.
- glibc shim bridge passing a shared vector buffer FD with `SCM_RIGHTS`, both
  one-connection-per-command and persistent-connection forms.
- glibc shim bridge registering a shared vector buffer once, then reusing it
  for repeated commands on the same connection.

Important caveat: process-level wall time is not a fair direct-vs-bridge
comparison because the direct path includes Android executable startup while
the bridge path uses a server that is already resident. The useful early signal
is the warm internal `total_ms` reported by the GPU command itself.

Latest local repeat50 result:

- Direct executor warm total mean: 1.3851 ms.
- One-connection-per-command bridge warm total mean: 1.5915 ms, about 1.15x
  direct.
- Persistent bridge warm total mean: 1.2640 ms, within measurement noise of
  direct.
- NOOP wall per run: direct 13.4684 ms, bridge 2.9931 ms, persistent bridge
  2.1316 ms. This is mostly process/socket/stdio measurement overhead, not GPU
  work.

Follow-up repeat50 with explicit NOOP separation:

- Direct executor warm total mean: 1.3851 ms.
- Non-persistent bridge warm total mean: 1.5915 ms, about 1.15x direct.
- Persistent bridge warm total mean: 1.2640 ms, effectively noise-limited for
  this coarse GPU command.
- A later noisy repeat showed direct process wall time larger than bridge wall
  time, which is not a valid acceleration signal. It demonstrates why host
  executable startup and JSON/stdio wall measurements must not be used as the
  primary bridge overhead metric.

Interpretation:

- The direct host benchmark is too coarse if it includes executable startup,
  shader cache warmup, JSON output, or upload/download costs.
- Bridge tuning must therefore track NOOP/control overhead separately from
  upload, dispatch, and download.
- Persistent transport is mandatory; one socket connection per GPU command is
  useful only as a diagnostic worst case.

Conclusion: the current socket bridge is good enough as a tuning scaffold, but
the LLM path must use persistent transport, buffer reuse, and batched commands.
Single-command connection churn is explicitly not acceptable for real ggml
backend work. The next lower-overhead bridge target remains shared memory for
buffer tables plus a small persistent control channel for command submission
and fences.

Follow-up repeat8 after adding the FD-passed shared-buffer vector-add probe:

- Direct executor warm total mean: 2.0198 ms.
- One-connection-per-command bridge warm total mean: 2.7496 ms, about 1.36x
  direct.
- Persistent command bridge warm total mean: 2.3281 ms, about 1.15x direct.
- FD shared-buffer bridge warm total mean: 2.3776 ms, about 1.18x direct.
- Persistent FD shared-buffer bridge warm total mean: 2.2078 ms, about 1.09x
  direct.

This confirms the bridge can pass a container-owned shared buffer into the
Android GPU executor and receive validated output without exposing Android
vendor GPU libraries to the glibc process. It still allocates and maps a fresh
buffer per command, so it is a bridge substrate measurement, not the final
llama.cpp GPU backend.

Follow-up repeat8 after adding a registered shared-buffer probe:

- Direct executor warm total mean: 3.5770 ms.
- Persistent command bridge warm total mean: 1.7593 ms.
- Persistent FD shared-buffer bridge warm total mean: 3.1626 ms.
- Registered shared-buffer bridge warm total mean: 2.6267 ms.
- Registered shared-buffer wall per run: 25.2951 ms.

These short repeat8 values are noisy and the direct process path is still not
a fair wall-clock baseline because it includes process startup. The important
directional improvement is structural: the bridge can now register a buffer
once and submit repeated commands against it. The next optimization step is a
real multi-buffer table plus fences so llama/ggml operations can chain work
without repeated allocation, mapping, or per-operation connection setup.

The container-facing socket path is `/run/pdocker-gpu/pdocker-gpu.sock`.
pdockerd bind-maps the APK runtime GPU directory to `/run/pdocker-gpu`, and the
direct executor rewrites `connect(AF_UNIX)` socket paths through the bind map.
This avoids leaking Android app-data absolute paths into container code and
keeps the shim ABI portable.

## 2026-05-04 pdocker Vulkan ICD Surface

The GPU path is now being moved toward standard GPU application entry points
instead of llama.cpp-specific changes. The APK packages a glibc
`pdocker-vulkan-icd.so`, and pdockerd writes
`/etc/vulkan/icd.d/pdocker-android.json` pointing at
`/usr/local/lib/pdocker-vulkan-icd.so` for GPU-requesting containers.

Local Vulkan-loader probe against the pdocker ICD:

- `vkCreateInstance`: success.
- `vkEnumeratePhysicalDevices`: one device.
- Device name: `pdocker GPU bridge (offline)`.
- Device type: CPU when the bridge queue is not available, integrated GPU when
  the queue is visible.

This is a loader/diagnostic milestone, not a compute claim. pdockerd marks the
surface as `PDOCKER_VULKAN_ICD_KIND=pdocker-bridge-minimal` and
`PDOCKER_VULKAN_ICD_READY=0`, so the llama template continues CPU fallback
unless raw diagnostic mode is selected or the ICD is later marked compute-ready.
The next step is lowering Vulkan compute buffer and command-buffer calls into
the existing pdocker GPU bridge without modifying llama.cpp.

Follow-up Vulkan ICD bridge smoke on 2026-05-04:

- `vkQueueSubmit`: success through the pdocker command queue.
- Device name: `pdocker GPU bridge (queue)`.
- Device type: integrated GPU when the queue is visible.
- Result: `maxErr=0.00000000`, `out0=1.000`, `outLast=128.875`.

This confirms the bridge-style Vulkan path, not raw Android vendor passthrough.
Raw passthrough would mean loading Android/Bionic vendor Vulkan libraries
directly from a glibc container, which remains out of scope. The viable path is
standard Vulkan loader compatibility in the container, backed by pdocker's ICD
and APK-owned executor.

Follow-up APK executor Vulkan compute probe on 2026-05-04:

- Command: `pdocker-gpu-executor --bench-vulkan-vector-add 1`.
- Device: `10.62.90.13:37669`.
- Backend: `android_vulkan`.
- Backend affinity: `same-api`.
- Kernel: `vector_add`.
- Result: `valid=true`, `max_abs_error=0.00000000`.
- Timing: init `121.3690 ms`, compile `25.6011 ms`, upload `2.7919 ms`,
  dispatch `1.2833 ms`, download `1.4367 ms`, total `152.4820 ms`.

This is the first confirmed APK-side Vulkan compute execution path. The
remaining work is connecting the Vulkan ICD bridge to reusable Vulkan executor
objects instead of one-shot vector-add setup, then expanding descriptor,
pipeline, command-buffer, and fence coverage toward llama/ggml workloads.

Follow-up Android device smoke result:

- Command: `ANDROID_SERIAL=10.62.90.13:37669 ADB=adb scripts/android-device-smoke.sh --quick --gpu-bench --no-install`.
- Vulkan executor backend: `android_vulkan`.
- Backend affinity: `same-api`.
- Result: `valid=true`, `max_abs_error=0.00000000`.
- Timing: init `119.2271 ms`, compile `14.6046 ms`, upload `3.1205 ms`,
  dispatch `0.9291 ms`, download `0.4504 ms`, total `138.3317 ms`.
- OpenCL probe: still blocked by Android untrusted-app linker namespace on this
  device.
- GLES benchmark remains healthy: stream vector-add total `1.3796 ms`, SAXPY
  `3.0361 ms`, 64x64 matmul `1.6183 ms`.

Follow-up cached Vulkan executor result on 2026-05-04:

- Command: `pdocker-gpu-executor --bench-vulkan-vector-add 3`.
- Device: `10.62.90.13:37669`.
- Backend: `android_vulkan`.
- Backend affinity: `same-api`.
- Kernel: `vector_add`.
- First run: `backend_cached=false`, init `106.1700 ms`, compile `0.0000 ms`,
  upload `3.3972 ms`, dispatch `0.7480 ms`, download `0.2947 ms`, total
  `110.6100 ms`.
- Warm runs: `backend_cached=true`, init `0.0000 ms`, compile `0.0000 ms`,
  total `4.0728 ms` and `3.8251 ms`.
- Android smoke after reinstall passed with Vulkan same-api executor valid and
  OpenCL still blocked by the Android untrusted-app linker namespace.

This confirms that the Vulkan bridge can now separate one-time runtime setup
from per-dispatch work. The cached result is the baseline for future
container-vs-host overhead comparisons.

Follow-up host/container comparison on 2026-05-04:

- Repeatable script: `scripts/android-gpu-compare-bench.sh 8`.
- Host-only baseline script: `scripts/android-gpu-host-bench.sh --runs 5`.
- Latest host-only JSON: `docs/test/gpu-host-native-latest.json`.
- Latest host-only table: `docs/test/gpu-host-native-latest.md`.
- Latest JSON: `docs/test/gpu-host-container-comparison-latest.json`.
- Latest table: `docs/test/gpu-host-container-comparison-latest.md`.
- Device: `10.62.90.13:37669`.
- Container: `device-smoke-app-1`.
- Warmup samples discarded per series: `3`.
- Latest host-only CPU matmul256 steady median: `34.8216 ms`.
- Latest host-only Vulkan resident-buffer matmul256 steady median:
  `0.6666 ms`.
- Latest host-only CPU matmul256 / Vulkan resident matmul256 ratio:
  `52.2337x`.
- Latest host-only CPU vector-add steady median: `0.0667 ms`.
- Latest host-only Vulkan resident-buffer vector-add steady median:
  `0.5002 ms`.
- Host CPU vector-add steady median: `0.0836 ms`.
- Host Vulkan transfer vector-add steady median: `5.5497 ms`.
- Host Vulkan resident-buffer vector-add steady median: `0.6522 ms`.
- Host CPU matmul256 steady median: `34.9056 ms`.
- Host Vulkan resident-buffer matmul256 steady median: `0.9234 ms`.
- Container CPU vector-add steady median: `0.1042 ms`.
- Container Vulkan bridge vector-add steady median: `3.3530 ms`.
- Container GPU / host transfer GPU steady median ratio: `0.6042x`.
- Container GPU / host resident GPU steady median ratio: `5.1411x`.
- Host resident Vulkan / host transfer Vulkan steady median ratio: `0.1175x`.
- Host CPU matmul256 / host resident Vulkan matmul256 ratio: `37.8012x`.
- Bridge NOOP command-queue round trip from inside the container process:
  `0.1245 ms/call`.

The script also records wall time for the direct-executor benchmark process.
That wall time includes process startup and ptrace/seccomp tracing, so it is
useful for end-to-end runtime overhead but not the pure GPU command-queue
crossing cost. Use the Bridge NOOP row for command-queue overhead.

Interpretation: vector-add is a poor proof of LLM GPU value because it is
transfer-bound and CPU is already very fast. CPU-emulated execution should be
used for this class when it wins; the OpenCL ICD now has this CPU-emulated
path in auto mode, while bridge smokes force GPU explicitly. The host-side
Vulkan path shows useful GPU behavior once buffers are resident and reused:
the resident matmul256 probe is about `37.8x` faster than the CPU scalar
reference in the host/container comparison and about `52.2x` faster in the
host-only native baseline. This confirms the APK-side Android Vulkan executor
is native GPU execution, not CPU emulation. The current container bridge still
behaves like a transfer path for
vector-add, so the next optimization target is persistent registered GPU
buffers across the container/APK boundary before llama GPU mode is enabled by
default.

## Latest HTTP API Result

- Date: 2026-05-04 UTC.
- Local path: `docs/test/llama-gpu-compare-latest.json`.
- Device path: `files/pdocker/bench/llama-gpu-compare-latest.json`.
- Scenario: `scripts/android-llama-gpu-compare.sh --predict 4 --repeat 1 --gpu-layers 1 --gpu-ctx 512 --cpu-ctx 2048`.
- Policy: llama.cpp source unchanged; GPU entry is the standard Vulkan loader
  through `pdocker-vulkan-icd.so`.
- CPU baseline: 0.1559 generated tokens/s for the short HTTP probe.
- 10x target for this baseline: 1.5589 generated tokens/s.
- Forced Vulkan result: `served=true`, GPU 0.1230 generated tokens/s, speedup
  `0.789x`, `target_met=false`, with `gpu_layers=1`.
- GPU evidence: llama.cpp reached `Vulkan0 (pdocker Vulkan bridge (queue))`
  and allocated the offloaded output-layer Vulkan model buffer:
  `Vulkan0 model buffer size = 486.87 MiB`, `offloaded 1/37 layers to GPU`.
- Additional progress: transfer-only queue submits now complete, and the ICD
  now records copy-buffer regions as in-bounds:
  `src_size=510504960 ... dst_off=16384 bytes=510504960 ok=1`. The run reaches
  `llama_context`, KV-cache setup, compute-buffer allocation, and model warmup.
  The Vulkan ICD now exposes separate device-local and host-visible memory
  types, so llama.cpp places the offloaded model/compute buffers in
  device-local memory and staging/output buffers in host-visible memory.
- Bridge ABI progress: `VULKAN_DISPATCH_V2` preserves the compute shader entry
  point and bounded specialization constants across the glibc ICD to Android
  executor boundary. `scripts/smoke-vulkan-icd-bridge.sh` now verifies a
  minimal storage-buffer compute dispatch through the same socket path, and
  `scripts/verify-fast.sh` runs both Vulkan init and bridge smokes.
- Diagnostic progress: allocation pNext tracing, range accounting, generic
  SPIR-V dispatch lowering, and server HTTP handling are past the earlier
  blockers. The current bridge overhead phase is explicit in the JSON report:
  it records `served=true`, CPU/GPU tokens per second, `gpu_layers=1`, the
  10x target, `target_met=false`, and the next action.
- Current blocker: served through generic SPIR-V, but bridge upload/copy
  overhead keeps GPU below CPU throughput. Latest dispatch profile: 258
  generic SPIR-V samples, mean upload 40.38 ms, mean dispatch 4.37 ms, mean
  download 4.57 ms.
- Next action: reduce bridge upload/copy overhead with persistent registered
  buffers, then rerun with larger `n_predict`.
- Recovery: this older run restored CPU mode; current compare runs leave the
  last measured mode running unless `--restore` is explicitly requested.
- UI note: the `llama.cpp GPU compare` card shown while this script runs is a
  daemon operation/progress card, not a container. The container itself is
  `pdocker-llama-cpp` and is the only object expected in `docker ps`. Direct
  Engine API launches now apply the same pdocker project/compose labels as
  UI-launched compose services so project cards and `docker ps` reconcile
  against the same container identity.
- Operation cleanup: the compare operation is marked failed on nonzero exit and
  ADB port forwarding is removed. CPU mode is no longer restored by default;
  pass `--restore` when a post-benchmark CPU fallback server is needed. The
  next compare run recreates the required mode before measuring.

## Previous HTTP API Result

- Date: 2026-05-04 UTC.
- Device: `10.62.90.13:37669`.
- Mode: Vulkan bridge forced experiment, then CPU fallback recovery.
- Build: `pdocker/llama-cpp-gpu:latest` rebuilt with `GGML_VULKAN=ON`.
- Vulkan discovery result: llama.cpp now sees
  `Vulkan0 (pdocker Vulkan bridge (queue))` instead of
  `ggml_vulkan: No devices found`.
- Vulkan memory surface result: the pdocker ICD reports Vulkan 1.2,
  `VK_KHR_maintenance4`, integrated GPU device type, and an 8 GiB advertised
  heap.
- 8B forced GPU result: `--n-gpu-layers 1` reaches llama.cpp model loading and
  reports `offloading output layer to GPU`, but still exits before serving.
- Current blocker: Qwen3 8B Q4_K_M still hits Vulkan buffer/pinned-memory
  allocation paths before a useful token benchmark can run. The next GPU work
  is split-buffer/pinned-host-buffer handling and real SPIR-V dispatch lowering
  in `pdocker-vulkan-icd.so`; llama.cpp remains unmodified.
- Recovery result: CPU fallback now hides Vulkan devices with
  `GGML_VK_VISIBLE_DEVICES=""`, reaches `Up (healthy)`, and
  `GET /v1/models` returns `model.gguf`.
- Recovery probe: 2 generated tokens returned from `/completion`; prompt speed
  `0.659 tok/s`, generation speed `0.350 tok/s`.

## Previous HTTP API Result

- Date: 2026-05-04 UTC.
- Local path: `docs/test/llama-run-current.json`.
- Device path: `files/pdocker/bench/llama-run-current.json`.
- Mode: CPU current server run.
- Model: Qwen3 8B GGUF, Q4_K_M, 8.19B parameters.
- Generated tokens: 8.
- Repetitions: 1.
- Mean wall time: 41.96s.
- Generation speed: 0.225 tokens/s.
- Content preview: `Okay, I need to figure out`.

The server is model-capable today on CPU fallback. During this run the
container still had an old Vulkan ICD JSON pointing at `/system/lib64/libvulkan.so`
and the template GPU profile selected Vulkan too early because
`PDOCKER_CUDA_COMPAT=1` was checked before the unfinished pdocker Vulkan ICD
gate. The source template now gates unfinished pdocker Vulkan before CUDA
compatibility and pdockerd now writes the pdocker ICD JSON with API version
`1.2.0`.

## Previous HTTP API Result

- Date: 2026-05-03 UTC.
- Local path: `docs/test/llama-bench-cpu-repeat3.json`.
- Latest alias: `docs/test/llama-bench-latest.json`.
- Device path: `files/pdocker/bench/llama-bench-cpu-repeat3.json`.
- Mode: CPU fallback baseline.
- Model: Qwen3 8B GGUF, Q4_K_M, 8.19B parameters.
- Prompt tokens: 6.
- Generated tokens: 8.
- Repetitions: 3.
- Mean wall time: 37.32s.
- Generation speed: 0.260 tokens/s mean, 0.239 min, 0.286 max.

Per-run generation speeds:

- Run 1: 0.255 tokens/s, 39.26s wall.
- Run 2: 0.239 tokens/s, 38.58s wall.
- Run 3: 0.286 tokens/s, 34.11s wall.

## Latest llama-bench Tool Result

The official llama.cpp `llama-bench` tool was built inside the existing
container with:

```sh
cmake --build /opt/llama.cpp/build --target llama-bench --parallel 2
```

The repeatable wrapper is:

```sh
bash scripts/android-llama-tool-bench.sh
```

Latest local path: `docs/test/llama-bench-tool-cpu-p16-n8-r3.json`.

Parameters:

- `-m /models/model.gguf`
- `-p 16`
- `-n 8`
- `-r 3`
- `-ngl 0`
- `-t 8`

Results:

- Prompt processing: 2.40 tokens/s average, samples 2.11, 2.58, 2.51.
- Token generation: 0.228 tokens/s average, samples 0.210, 0.260, 0.215.
- Backend reported by llama-bench: BLAS/OpenBLAS, CPU.

GPU status for this run: CPU fallback. The container diagnostics reported no
Vulkan passthrough, no CUDA-compatible signal, no `VK_ICD_FILENAMES`, and no
usable GPU device inside the container. Future GPU-enabled runs should preserve
this same benchmark shape and record the GPU diagnostic evidence next to the
tokens/s result.

## Maintenance

- Keep newest human-readable results near the top and preserve older results as
  history.
- Do not restate GPU architecture decisions here; link to
  [`../design/GPU_COMPAT.md`](../design/GPU_COMPAT.md).
- Keep JSON artifacts machine-readable and summarize them here only enough for
  comparison.

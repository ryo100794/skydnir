# pdocker GPU compatibility extensions

Snapshot date: 2026-05-01.

pdocker has an experimental Docker-compatible GPU request surface. It is
designed for Android devices where native Docker GPU runtimes such as
`nvidia-container-runtime` do not exist.

Canonical split:

- Backend request/env/inspect behavior lives in
  [`docker-proot-setup/docs/GPU_COMPAT.md`](../../docker-proot-setup/docs/GPU_COMPAT.md).
- This document owns the Android benchmark philosophy, cuVK direction, UI
  diagnostics, and device-specific measurement notes.

## Design principle

pdocker treats Android GPU support as a Vulkan-first compatibility stack.
Native NVIDIA CUDA is not expected on ordinary Android phones; it is only an
external baseline for Jetson or NVIDIA Linux devices. The Android path is:

```text
Docker --gpus / HostConfig.DeviceRequests
  -> pdocker GPU negotiation
  -> a glibc-facing GPU bridge owned by pdocker
  -> Android-side Vulkan/OpenCL execution behind that bridge
  -> cuVK, a restricted CUDA-like API lowered to the bridge runtime
```

`cuda-compat` therefore means "CUDA-shaped userspace API backed by Android GPU
compute", not PTX execution and not NVIDIA driver passthrough.

The implementation must keep CPU, direct Vulkan, and CUDA-like compatibility
paths comparable. Any benchmark or runtime probe should use the same input
data, validation rules, problem sizes, and output format across:

- `cpu_scalar`
- `cpu_neon`
- `vulkan_spirv`
- `cuvk_transpile`
- optional `native_cuda` on NVIDIA Linux only

Each result must separate:

```text
compile_ms + upload_ms + dispatch_ms + download_ms = total_ms
```

This matters on Android because upload/download and synchronization overhead
can dominate small workloads.

## Current behavior

Current backend behavior is request parsing, environment negotiation, inspect
metadata, health/test reporting, and diagnostic exposure of Android GPU signals.
The direct exposure experiment proved that Android vendor libraries should not
be treated as a working container GPU backend: Android's Vulkan/OpenCL libraries
are Bionic/Android ABI objects, while the bundled Linux images are glibc
userlands. Exposing `/system/lib64/libvulkan.so` or
`/vendor/lib64/libOpenCL.so` into an Ubuntu image naturally drives the glibc
loader into Android-only dependencies such as `android.hardware.*`,
`liblog.so`, and `libcutils.so`.

The production direction is therefore:

```text
glibc container process
  -> pdocker-owned glibc shim library or device ABI
  -> stable shared-memory command queue or narrow ioctl-like control plane
  -> Android/Bionic GPU executor owned by the APK
  -> Vulkan/OpenCL/NNAPI/other Android GPU API
```

The container must see a glibc-compatible ABI. Android GPU libraries may be
used only behind the APK/sidecar boundary, not as direct glibc `dlopen` targets.
The Android side must be a GPU command executor, not a host-side LLM engine.
For llama.cpp, model loading, tokenization, sampling, HTTP serving, scheduling,
and ggml graph ownership stay in the container process. The bridge may execute
GPU kernels, move buffers, and signal fences, but it must not replace
`llama-server` with a host RPC inference service.

The Engine/API truth surfaces for the GPU bridge are:

- `PdockerGpu` in `GET /containers/{id}/json` for per-container GPU requests,
  requested capabilities, injected environment, and bridge warnings.
- `GET /system/host` for bounded host GPU/framework diagnostics such as
  Vulkan/OpenCL/GL/NNAPI availability, driver/API versions where discoverable,
  and APK-side executor capability state.

Both are pdocker extensions. Docker-compatible request parsing still accepts
common `HostConfig.DeviceRequests`, runtime, and label forms, but correctness
and performance claims require separate llama/GPU artifacts.

## Vulkan Passthrough Terminology

There are two different ideas that can both sound like "Vulkan passthrough":

- **Raw vendor passthrough**: bind Android vendor Vulkan/OpenCL libraries from
  `/vendor/lib64` directly into a glibc container. This is not the pdocker
  target. It crosses the Bionic/glibc boundary, depends on Android linker
  namespaces, and can be blocked for untrusted apps even when the library files
  exist.
- **pdocker ICD bridge**: expose a glibc-facing Vulkan ICD in the container and
  lower Vulkan calls into the APK-owned GPU executor. This is the target path.
  The container process still uses standard Vulkan loader behavior, while the
  executor hides Android GLES/Vulkan/OpenCL/vendor details below the neutral
  `pdocker-gpu-command-v1` ABI.

The current Vulkan ICD bridge already validates a compute-style vector-add
smoke through `vkQueueSubmit`. The APK executor now has a same-API Android
Vulkan path with reusable instance/device/shader/pipeline/command-pool state,
so one-time runtime setup is separated from warm dispatch cost. It is not a
general Vulkan backend yet because only a small descriptor/buffer/dispatch
subset is lowered. This is still more promising than raw passthrough on Android
because the APK can use public NDK Vulkan APIs without asking a glibc process to
load Bionic vendor libraries.

## Backend Affinity Policy

pdocker should preserve the application-facing GPU API as far down the stack as
possible:

- GL/GLES-looking work should run through the Android GL/GLES backend.
- Vulkan-looking work should run through the Android Vulkan backend.
- OpenCL-looking work should run through Android OpenCL when the app linker
  namespace permits it.

Cross-API lowering is a fallback, not the first choice. Translating OpenCL
kernels to GLES, or Vulkan command streams to another API, adds avoidable costs
in shader translation, descriptor/memory model adaptation, synchronization,
and validation. A fallback is acceptable only when the same-device backend is
unavailable or blocked, and the fallback must be explicit in logs and
diagnostics.

The current device demonstrates this rule: Android OpenCL exists on disk but is
blocked for the untrusted app namespace, so OpenCL requests fall back to the
GLES compute executor. Vulkan should follow the same affinity model: container
Vulkan ICD first lowers to Android Vulkan, with GLES or other backends reserved
for diagnostics and limited compatibility gaps.
The visible contract is a device-independent `pdocker-gpu-command-v1` ABI. GPU
backend choices such as GLES compute, Vulkan, OpenCL, NNAPI, or vendor-specific
driver details belong below that ABI and must be absorbed by the APK-owned
executor layer. Container code should not branch on phone model or vendor GPU
library paths.
The container-side and APK-side copies of `pdocker_gpu_abi.h` intentionally
carry the same `PDOCKER_GPU_*` defines; `tests/test_gpu_abi_contract.py` guards
that contract until the ABI header is moved to a single generated source.
The first scaffold now has two binaries:

- `pdocker-gpu-shim`: Linux/glibc container-facing probe, injected as
  `/usr/local/bin/pdocker-gpu-shim` for GPU-requesting containers.
- `pdocker-gpu-executor`: Android/Bionic APK-side executor probe. It owns
  Android GPU APIs and advertises the same neutral command ABI. Its Vulkan
  vector-add path caches the Vulkan runtime state within the executor process,
  and reports `backend_cached` plus separated init/upload/dispatch/download
  timings for host-vs-container overhead comparisons.
- `pdocker-vulkan-icd.so`: Linux/glibc Vulkan ICD surface, injected as
  `/usr/local/lib/pdocker-vulkan-icd.so`. This is the standard Vulkan-loader
  entry point for unmodified container applications. It now exposes the
  baseline that llama.cpp's ggml-vulkan initialization expects: Vulkan 1.2,
  `VK_KHR_16bit_storage`, `storageBuffer16BitAccess`, compute subgroup
  properties, two compute/transfer queue handles, host-visible coherent memory,
  and common buffer/command stubs. It is still marked not compute-ready
  (`PDOCKER_VULKAN_ICD_READY=0`) for real SPIR-V dispatch until shader lowering
  is implemented; unknown real shader modules fail explicitly instead of being
  mapped to a wrong fallback kernel.
- `pdocker-opencl-icd.so`: Linux/glibc OpenCL ICD surface, also injected as
  `/usr/local/lib/libOpenCL.so` and `/usr/local/lib/libOpenCL.so.1` for images
  that link the OpenCL loader directly. This is the preferred first standard
  GPU entry point because the OpenCL API surface is smaller than Vulkan for an
  initial compatibility bridge. The current implementation is a vector-add
  proof that lowers one OpenCL command sequence into the neutral GPU command
  queue; it is not yet a general llama.cpp GPU backend. The APK executor first
  tries Android native OpenCL by `dlopen`, but Android's untrusted-app linker
  namespace may block vendor OpenCL libraries even when the files exist. In
  that case the command remains valid and falls back to the executor's GLES
  compute backend.

The current shim supports capability probing, a temporary Unix-socket command
path, a shared-buffer probe where the glibc shim passes a mapped buffer FD to
the APK executor with `SCM_RIGHTS`, and a first registered-buffer probe where
that FD is mapped once per connection and reused. This validates the intended
direction but is not the final transport; the command ring, reusable buffer
table across real ggml tensors, and fence protocol are still pending. The
temporary socket transport is allowed only as a measurement scaffold.
Benchmarks must separate NOOP/control overhead from upload/dispatch/download
work, and real LLM integration must use persistent transport plus buffer reuse
so bridge overhead is not paid per tiny ggml operation.

Large model buffers must not cross the bridge as one eager copied memory block.
For multi-GB model loads, a naive API that asks the container to allocate the
model, copy it into an API payload, and then allocate/import it again on the APK
side can require more than twice the model size transiently and will trigger
OOM on common phones. The bridge contract for real models is:

- Prefer registered shared buffers or imported file descriptors over payload
  copies.
- Register large buffers once, then reference them by handle in later commands.
- Support chunked upload and page-range dirty tracking for cases where a
  backend cannot import the full buffer at once.
- Keep fallback chunk sizes tunable; slower chunked execution is preferable to
  an unrecoverable OOM.
- Report memory mode in diagnostics, such as `registered-fd`, `chunked-fd`, or
  `copy-fallback`, and include peak allocation estimates when available.

The vector-add probes intentionally use small buffers and are not
representative of model loading. Future llama/ggml work must benchmark bridge
overhead by buffer size and transfer mode before enabling GPU mode by default.

CPU execution is a valid implementation of the neutral GPU command ABI when it
is the fastest correct path. The compatibility layer should expose a
CPU-emulated backend for operations where API overhead, bridge crossings, or
buffer transfer dominate the work. The governor rule is:

- prefer CPU for transfer-bound one-shot kernels and small buffers;
- prefer same-API GPU only when buffers are resident or imported and the
  measured warm path beats CPU by the configured threshold;
- keep the decision explicit in diagnostics with `backend_affinity`,
  `backend_impl`, `buffer_residency`, and a fallback reason;
- never silently translate an unsupported kernel to a different numerical
  operation.

The OpenCL ICD now applies this rule to the current `vector_add` proof: in
`auto` mode it CPU-emulates small/default vector-add submissions inside the
glibc container process because measured CPU execution beats the current
bridge path. Bridge tests set `PDOCKER_GPU_GOVERNOR=force-gpu` so the real GPU
transport remains continuously tested. Operators can force CPU with
`PDOCKER_GPU_GOVERNOR=force-cpu` or tune the auto threshold with
`PDOCKER_GPU_CPU_FALLBACK_MAX_VECTOR_ADD_N`.

Vulkan follows the same correctness rule. The current ICD accepts the
fake-SPIR-V vector-add smoke used for bridge transport testing, but rejects
real shader modules that are not lowered yet. The next llama.cpp Vulkan step is
to record enough SPIR-V/pipeline metadata to recognize ggml matmul-family
shaders and lower those to resident-buffer executor commands without modifying
llama.cpp itself.

GPU runtime paths are exposed to containers under `/run/pdocker-gpu`. pdockerd
binds the APK runtime GPU directory there and direct execution rewrites
`connect(AF_UNIX)` socket paths, so container code never needs Android app-data
absolute paths.
Until the OpenCL/Vulkan bridge covers real ggml/llama kernels and passes
validation, llama.cpp GPU profile selection must stay on CPU fallback unless a
raw diagnostic mode is explicitly requested or a pdocker GPU ICD is explicitly
marked compute-ready.
Unsupported OpenCL kernels must be traced and failed, not mapped to an
incorrect fallback kernel. This prevents silent numerical corruption while the
coverage is expanded.

## CUDA-compatible API

`cuda-compat` is not NVIDIA CUDA passthrough. Android normally does not expose
NVIDIA `/dev/nvidia*` devices or the NVIDIA driver ABI. In pdocker, CUDA means
a planned compatibility API layer which can provide a CUDA-shaped userspace ABI
backed primarily by Vulkan Compute.

The first cuVK runtime scope is intentionally small:

- `cuvkInit` / `cuvkShutdown`
- `cuvkMalloc` / `cuvkFree`
- `cuvkMemcpy`
- `cuvkModuleLoadCudaSource`
- `cuvkModuleLoadSpirv`
- `cuvkKernelGet`
- `cuvkLaunchKernel`
- `cuvkDeviceSynchronize`

The supported CUDA-like subset starts with `__global__`, `threadIdx`,
`blockIdx`, fixed `blockDim`, global pointer arguments, scalar push constants,
and restricted `if`/`for`. Full CUDA C++, PTX, cuBLAS, cuDNN, cuFFT, NCCL,
Unified Memory, and complete stream semantics are non-goals for the first
implementation.

## Benchmark gate

GPU support should not be considered "working" just because a device node or
library is visible inside the container. A backend must validate against a CPU
reference implementation before its speedup is accepted.

The Android UI diagnostics now include a first-pass `android-gpu-bench` action
which writes CPU scalar and OpenGL ES 3.1 compute-shader results as JSON Lines
and CSV under `files/pdocker/bench/` and mirrors them to the app external files
`bench/` directory when Android storage policy allows it. GLES compute is an
Android-side GPU smoke backend; the Docker-facing target remains Vulkan/cuVK,
but the same artifact format and validation rules are used so the results are
comparable later.

Initial benchmark kernels:

- `vector_add`
- `saxpy`
- `matmul_fp32`

Follow-up kernels:

- `reduce_sum`
- `conv2d`
- `matmul_fp16`
- `quantized_matmul`
- `rmsnorm`
- `rope`
- `softmax`

Recommended decision thresholds:

- recommend GPU only when warm total time is at least 1.5x faster than
  `cpu_neon`
- recommend chained GPU execution when chained total time is at least 2.0x
  faster than `cpu_neon`
- defer GPU when transfer overhead dominates, validation fails, or thermal
  throttling erases the gain
- use CPU-emulated execution for compatibility when the measured CPU path is
  faster than the GPU path for the current kernel shape

Latest quick smoke on Sony SOG15, 2026-05-04, using the Android-side
`gles31_compute` backend:

| Kernel | CPU scalar total | GLES 3.1 total | CPU/GLES |
|---|---:|---:|---:|
| `vector_add_cold` n=262144 local_size=256 | 41.69 ms | 48.94 ms | 0.85x |
| `vector_add` stream n=262144 local_size=256 | 41.69 ms | 3.75 ms | 11.12x |
| `saxpy` n=262144 | 47.87 ms | 10.72 ms | 4.47x |
| `matmul_fp32` 64x64 | 45.18 ms | 2.29 ms | 19.70x |

This confirms that the benchmark can observe GPU speedup on this device for
workloads with enough arithmetic intensity. `vector_add_cold` measures the
one-shot path including shader compilation, upload, dispatch, and download.
The tuned `vector_add` stream path reuses the compiled kernel, selects the
fastest tested workgroup size, and still includes upload/dispatch/download
time. On the latest SOG15 smoke run the selected workgroup size is 256 and the
stream path is about 11.12x faster than the CPU scalar baseline. The cold
one-shot `vector_add` path is slower than CPU because it includes shader
compilation and setup; this is why the llama path must reuse kernels, buffers,
and bridge transport instead of paying cold-start cost per operation.

Benchmark outputs should be JSON Lines and CSV under:

```text
/storage/emulated/0/Android/data/<package>/files/bench/
```

Device records should include model, SoC/GPU name, Android/API version, Vulkan
driver/API version, FP16 support, timestamp query support, battery/charging
state, and thermal state when available.

## Implementation roadmap

1. Minimal runner: CPU scalar/NEON `vector_add` and `saxpy`, shared validator,
   JSONL/CSV writer, device info collector.
2. Vulkan baseline: compute context, buffer upload/download, SPIR-V shader
   loading, CPU wall time and GPU timestamp timing when available.
3. Matrix baseline: CPU reference, simple Vulkan matmul, tiled Vulkan matmul,
   GFLOPS and numerical error reporting. The resident Vulkan matmul256 probe is
   now the host-side proof that compute-heavy kernels can beat CPU; bridge work
   should preserve this resident-buffer shape.
4. glibc bridge ABI: container-facing shim, FD-passed shared-buffer transport,
   reusable buffer table, command buffers, fence/error model, and Bionic
   GPU-executor lifecycle. The executor runs GPU commands only; application
   engines such as llama.cpp remain in the container. The container-facing ABI
   must be device-independent; backend differences are handled by executor
   capability probing and command lowering.
5. cuVK runtime: CUDA-shaped allocation/copy/module/kernel launch API backed by
   the bridge runtime.
6. CUDA subset transpiler: parse restricted CUDA-like kernels, emit GLSL/SPIR-V
   plus kernel metadata for buffer bindings and push constants.
7. LLM-oriented kernels: RMSNorm, RoPE, softmax, FP16/quantized matmul, with
   chained execution comparisons.

## Default dev workspace

The default workspace Compose file sets:

```yaml
gpus: all
```

and the default image includes `vulkan-tools` and `libvulkan1` so Vulkan
passthrough can be tested with commands such as:

```sh
vulkaninfo --summary
env | grep -E 'PDOCKER|CUDA|NVIDIA|VK_'
```

## llama.cpp profile diagnostics

The bundled `llama.cpp GPU workspace` template now records the first practical
diagnostic layer before the benchmark runner exists. Its
`scripts/pdocker-gpu-profile.sh` writes both:

- `profiles/pdocker-gpu.env` for the llama-server startup arguments
- `profiles/pdocker-gpu-diagnostics.json` for UI/log inspection

The JSON diagnostic captures the selected backend, the recommendation reason,
thread/context/GPU-layer choices, memory size, and the CUDA/Vulkan/OpenCL
signals that were visible in the container. Visibility is not performance
validation. The default profile now stays on CPU fallback when only raw Android
library signals are present; raw Vulkan exposure requires an explicit
diagnostic mode such as `PDOCKER_GPU_MODE=vulkan-raw`.

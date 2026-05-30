# Skydnir GPU compatibility extensions

Snapshot date: 2026-05-01.

The Skydnir daemon now accepts Docker-style GPU requests and maps them onto
Skydnir's Android user-space runtime model.

## Design principle

Skydnir treats Android GPU support as a Vulkan-first compatibility stack.
Native NVIDIA CUDA is only an optional external baseline on NVIDIA Linux
devices. On ordinary Android devices the intended path is:

```text
Docker --gpus / HostConfig.DeviceRequests
  -> Skydnir GPU negotiation
  -> a Skydnir-owned glibc-facing GPU bridge
  -> Android-side Vulkan/OpenCL execution behind that bridge
  -> cuVK, a restricted CUDA-like API lowered to the bridge runtime
```

`cuda-compat` therefore means a CUDA-shaped userspace contract backed by
Android GPU compute. It does not mean PTX execution or NVIDIA driver
passthrough.

## Request surface

The following inputs enable GPU compatibility handling:

- Docker CLI `--gpus` payloads, represented as `HostConfig.DeviceRequests`.
- `HostConfig.Runtime` values: `nvidia`, `pdocker-gpu`, `vulkan`, or `cuda`.
- Labels `io.pdocker.gpu` or `pdocker.gpu` with values such as `vulkan`,
  `cuda`, or `cuda-compat`.

Example:

```sh
docker run --gpus all ubuntu:22.04 env | grep -E 'PDOCKER|CUDA|NVIDIA|VK_'
```

## Vulkan/OpenCL diagnostics

When Vulkan or OpenCL mode is requested, the Skydnir daemon records
negotiation metadata and may expose diagnostic paths so tests can prove why
direct loading fails.
These paths are not a production GPU backend. Android vendor GPU libraries are
Bionic/Android ABI libraries, while Skydnir Linux images are glibc userlands.
The production bridge must keep the container-facing side glibc-compatible and
call Android GPU APIs from an APK-owned Bionic sidecar.

When Vulkan mode is requested, the Skydnir daemon:

- marks the container with `PdockerGpu.Modes=["vulkan", ...]`;
- injects `PDOCKER_VULKAN_PASSTHROUGH=1`;
- injects `VK_ICD_FILENAMES=/etc/vulkan/icd.d/pdocker-android.json`;
- creates a Vulkan ICD JSON in the container rootfs pointing at Skydnir's
  compatibility glibc ICD, `/usr/local/lib/pdocker-vulkan-icd.so`;
- bind-passes Skydnir's compatibility ICD and bridge paths:
  - `/usr/local/lib/pdocker-vulkan-icd.so`
  - `/usr/local/bin/pdocker-gpu-shim`
  - `/run/pdocker-gpu`
- bind-passes Android GPU device paths when they exist:
  - `/dev/kgsl-3d0`
  - `/dev/dri`
- and currently keeps selected vendor helper directories visible as diagnostic
  raw-mode context:
  - `/vendor/lib64/egl`
  - `/vendor/lib64/hw`

The Skydnir Vulkan ICD is the production-facing direction because it presents a
normal Vulkan-loader surface to unmodified container applications. It is
currently marked `PDOCKER_VULKAN_ICD_KIND=pdocker-bridge-minimal` and
`PDOCKER_VULKAN_ICD_READY=0`: applications can discover the ICD surface, but
Vulkan compute lowering is not complete yet. Raw Android Vulkan library
exposure is diagnostic-only unless `PDOCKER_GPU_MODE=vulkan-raw` is explicitly
selected for an experiment. A successful backend must marshal GPU work to an
Android/Bionic GPU executor. The executor must not become a host-side inference
engine; for llama.cpp, the container keeps model loading, tokenization,
scheduling, sampling, and HTTP serving. The container-facing contract is
`pdocker-gpu-command-v1`, a device-independent command ABI. GLES, Vulkan,
OpenCL, NNAPI, and vendor quirks are executor implementation details below that
ABI. GPU-requesting containers receive a Linux/glibc
`/usr/local/bin/pdocker-gpu-shim` capability probe; the APK owns the Bionic
`pdocker-gpu-executor` and queue socket under `/run/pdocker-gpu`.

When OpenCL mode is requested, the Skydnir daemon:

- marks the container with `PdockerGpu.Modes=["opencl", ...]`;
- injects `PDOCKER_OPENCL_PASSTHROUGH=1`;
- injects `OCL_ICD_VENDORS=/etc/OpenCL/vendors`;
- creates `/etc/OpenCL/vendors/pdocker-android.icd`;
- bind-passes `/vendor/lib64/libOpenCL.so` or
  `/system/vendor/lib64/libOpenCL.so` when present.

This has the same diagnostic-only limitation as raw Vulkan exposure.

## CUDA-compatible API

`cuda-compat` mode is not native NVIDIA CUDA. Android devices normally do not
expose NVIDIA kernel drivers or `/dev/nvidia*`. Skydnir treats CUDA as a
project-owned compatibility API target:

- injects `PDOCKER_CUDA_COMPAT=1`;
- injects `CUDA_VISIBLE_DEVICES=0`;
- injects Docker/NVIDIA compatibility env vars such as
  `NVIDIA_VISIBLE_DEVICES=all` and
  `NVIDIA_DRIVER_CAPABILITIES=compute,utility,graphics`;
- reserves the container contract for a future `libcuda.so` / NVRTC / cuBLAS
  compatibility shim backed by Vulkan compute, OpenCL, NNAPI, or another
  Android GPU compute path.

The current implementation provides request parsing, env negotiation, inspect
state, and diagnostic GPU signal exposure. The CUDA function ABI shim and
glibc-to-Bionic GPU bridge are still pending. The first shim scope is
intentionally small:

- `cuvkInit` / `cuvkShutdown`
- `cuvkMalloc` / `cuvkFree`
- `cuvkMemcpy`
- `cuvkModuleLoadCudaSource`
- `cuvkModuleLoadSpirv`
- `cuvkKernelGet`
- `cuvkLaunchKernel`
- `cuvkDeviceSynchronize`

Full CUDA C++, PTX, cuBLAS, cuDNN, cuFFT, NCCL, Unified Memory, and complete
stream semantics are non-goals for the first implementation.

## Benchmark gate

GPU support is not considered working just because a device node or library is
visible inside the container. A backend must validate against a CPU reference
implementation before its speedup is accepted.

Initial benchmark kernels:

- `vector_add`
- `saxpy`
- `matmul_fp32`

Each backend should report comparable timing:

```text
compile_ms + upload_ms + dispatch_ms + download_ms = total_ms
```

Recommended decision thresholds:

- recommend GPU only when warm total time is at least 1.5x faster than
  `cpu_neon`
- recommend chained GPU execution when chained total time is at least 2.0x
  faster than `cpu_neon`
- defer GPU when transfer overhead dominates, validation fails, or thermal
  throttling erases the gain

## Test coverage

`scripts/verify_all.sh` includes a reusable GPU request parser regression. It
asserts that a Docker `DeviceRequests` payload with `Driver=nvidia` and
`Capabilities=[gpu,compute,utility]` enables both `vulkan` and `cuda-compat`
and injects the expected compatibility environment variables.

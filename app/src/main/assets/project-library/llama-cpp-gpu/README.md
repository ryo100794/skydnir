# Skydnir llama.cpp GPU workspace

This template builds a llama.cpp server workspace for Skydnir.

It includes:

- `llama-server` built from `ggml-org/llama.cpp`.
- The upstream llama.cpp browser UI at `/`, served from
  `/opt/llama.cpp/tools/server/public` with llama.cpp's standard `--path`
  option, in addition to the OpenAI-style HTTP API and `/health` endpoint.
- Vulkan-oriented build flags (`GGML_VULKAN=ON`) for Android GPU passthrough.
- The LLM engine, model loading, tokenizer, HTTP API, sampler, and llama.cpp
  scheduler stay inside the container. This template does not offload the
  llama.cpp engine to a host-side RPC server.
- CPU fallback with OpenMP/OpenBLAS packages available.
- A server-only CMake build target so templates do not spend time compiling
  unrelated llama.cpp tools and examples.
- Ubuntu 24.04 Vulkan headers, which are new enough for the current
  llama.cpp Vulkan backend.
- The `glslc` shader compiler required by llama.cpp's Vulkan CMake checks.
- SPIR-V headers/tools used by llama.cpp's Vulkan backend source generation.
- `scripts/pdocker-gpu-profile.sh`, which writes a local GPU profile and
  JSON diagnostics based on the runtime environment.
- `models/` and `workspace/` bind directories for GGUF models and experiments.
  Model downloads happen at container startup, not during image build. With the
  default Android configuration, `/models` is backed by app-private pdocker
  storage through `PDOCKER_MODEL_HOST` so large GGUF files do not become image
  layers and do not constantly write to SD-card/Documents storage. Copy
  selected model artifacts to `/documents` only when you explicitly want to
  exchange them.
- The selected Android Documents folder is mounted at `/documents` by default.
  Use it only when llama.cpp or helper scripts explicitly need to import,
  export, or exchange files on SD/Documents storage. Project definitions live
  under `pdocker/projects` in the selected Documents root.
- A cross-project shared bind mount at `/shared`. Override
  `PDOCKER_DOCUMENTS_HOST`, `PDOCKER_DOCUMENTS_MOUNT`,
  `PDOCKER_SHARED_DOCUMENTS_HOST`, or `PDOCKER_SHARED_DOCUMENTS_MOUNT` when two
  projects intentionally need the same folder or mount path.

Usage from Skydnir:

1. Open the Library tab.
2. Install the `llama.cpp GPU workspace` template.
3. Run the GPU profile action.
4. Run compose up and let the default Qwen3 8B GGUF download complete.
5. Open the service on port `18081`.

The compose header comment `# skydnir.service-url: 18081=llama.cpp` labels the
local browser shortcut without changing standard Compose behavior. A healthy
server must make both `http://127.0.0.1:18081/health` and the browser UI at
`http://127.0.0.1:18081/` usable.

Server health is only a liveness check. GPU correctness is a separate gate:
run `pdocker-llama-correctness` from an exec session after the service starts.
It sends fixed deterministic `/completion` probes such as `2+3=` and writes
JSON evidence to `/workspace/logs/pdocker-llama-correctness.json` and
`/profiles/pdocker-llama-correctness.json`. Benchmark or UI claims may say the
GPU path is verified only when this report has `summary.correctness=pass`.

By default, first compose up downloads an 8B-class Apache-2.0 model in GGUF
form:

`https://huggingface.co/Qwen/Qwen3-8B-GGUF/resolve/main/Qwen3-8B-Q4_K_M.gguf`

The file is about 5 GB and is stored as `models/model.gguf`. The download uses
`models/model.gguf.part` while in progress so it can resume after interruption.
Set `LLAMA_MODEL_URL` to another direct GGUF URL, or set it to an empty value
and place a GGUF manually. If no model is available after the download attempt,
the container still opens a small status page on port `18081` so the workspace
has a visible running state.

The compose template passes `--path /opt/llama.cpp/tools/server/public --jinja`
by default because the bundled Qwen3 GGUF uses a chat template and the browser
UI should work even when an existing image was built before embedded Web UI
assets were enabled. Override `LLAMA_EXTRA_ARGS` if you need different
llama-server options. The template defaults `PDOCKER_GPU_MODE` to `vulkan-raw` and
`LLAMA_ARG_N_GPU_LAYERS` to `1`. This keeps the first normal Compose run on the
currently validated Skydnir Vulkan bridge path while still proving real
container-side GPU offload. The template also clamps Vulkan max buffer,
allocation, and ggml suballocation sizes to 512 MiB by default so llama.cpp's
allocator plans match the current bridge-safe range instead of assuming a
larger native driver allocation. Raise `LLAMA_ARG_N_GPU_LAYERS` for deeper offload
while tuning the bridge; `2` currently reaches an Adreno pipeline compiler
failure in one 18 KiB ggml SPIR-V shader and is tracked as bridge work.
When the container-facing Skydnir Vulkan ICD is visible but does not yet
advertise `PDOCKER_VULKAN_ICD_READY=1`, the entrypoint adds llama.cpp's
standard `--no-kv-offload` option and keeps the KV cache on CPU. This avoids
the known scheduler abort where cache tensors are reserved in an unfinished
Vulkan buffer. Set `PDOCKER_VULKAN_ALLOW_KV_OFFLOAD=1` only when validating
the ICD allocation/chunking path itself.

The image build pins `LLAMA_CPP_REF` to `b9030` and records the resolved commit
inside `/opt/llama.cpp/.pdocker-llama-cpp-commit`. It defaults CMake to
`Release` and one build job. The single job is intentionally slow, but it keeps
the build inside the generic pdocker execution path without llama.cpp source
patches or shader-compiler wrappers. Raise `LLAMA_CPP_BUILD_JOBS` only when the
device has enough free memory and swap for shader or Vulkan backend
compilation. Skydnir may apply generic Android build-executor memory telemetry
and guardrails outside the Dockerfile, but it must not rewrite the build tools
by default.

The GPU profile action writes:

- `profiles/pdocker-gpu.env`, sourced by `start-llama-server.sh`
- `profiles/pdocker-gpu-diagnostics.json`, with the selected backend,
  recommendation reason, memory/thread/context choices, and CUDA/Vulkan signal
  booleans
- pdocker GPU bridge evidence when available. The profile script probes
  `pdocker-gpu-shim --queue-probe` and `--vector-add-fd`; a successful FD
  shared-buffer probe is recorded as bridge readiness, not as llama.cpp GPU
  acceleration until the llama backend is wired to the bridge.

All startup, GPU profile generation, download, status-page, and `llama-server`
output is written to stdout/stderr so `docker logs skydnir-llama-cpp` can show
it. The same stream is also copied to `/workspace/logs/llama-server.log`;
override `LLAMA_LOG_FILE` to change or disable that extra file. The entrypoint
also writes `/workspace/logs/llama-startup.json` with the profile refresh result,
resolved GPU/backend arguments, KV offload guard state, llama-server argv, and
`MemAvailable`/`SwapFree` startup memory evidence.

Qwen3 weights are available under the Apache 2.0 license. This template
downloads the model at runtime; it is not bundled into the APK.

The compose file requests Docker-compatible `gpus: all`. pdockerd maps that to
its Vulkan passthrough / CUDA-compatible negotiation state where available.
GPU acceleration is considered real only when the glibc llama.cpp process uses
a container-facing pdocker GPU shim. Android/Bionic GPU libraries and services
may sit behind that shim, but they must not own the LLM engine or replace the
container's llama-server process.
The same distinction applies to reporting: `/health` proves only that
`llama-server` is alive, GPU mode proves only that an accelerated backend was
requested, and `pdocker-llama-correctness` is required before treating a GPU run
as computation-correct.
The default llama-server port is `18081`, offset from common development ports
to reduce collisions with Android/Termux services.

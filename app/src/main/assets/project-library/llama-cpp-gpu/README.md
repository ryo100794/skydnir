# pdocker llama.cpp GPU workspace

This template builds a llama.cpp server workspace for pdocker.

It includes:

- `llama-server` built from `ggml-org/llama.cpp`.
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

Usage from pdocker:

1. Open the Library tab.
2. Install the `llama.cpp GPU workspace` template.
3. Run the GPU profile action.
4. Run compose up and let the default Qwen3 8B GGUF download complete.
5. Open the service on port `18081`.

The compose header comment `# pdocker.service-url: 18081=llama.cpp` labels the
local browser shortcut without changing standard Compose behavior.

By default, first compose up downloads an 8B-class Apache-2.0 model in GGUF
form:

`https://huggingface.co/Qwen/Qwen3-8B-GGUF/resolve/main/Qwen3-8B-Q4_K_M.gguf`

The file is about 5 GB and is stored as `models/model.gguf`. The download uses
`models/model.gguf.part` while in progress so it can resume after interruption.
Set `LLAMA_MODEL_URL` to another direct GGUF URL, or set it to an empty value
and place a GGUF manually. If no model is available after the download attempt,
the container still opens a small status page on port `18081` so the workspace
has a visible running state.

The entrypoint adds `--jinja` by default because the bundled Qwen3 GGUF uses a
chat template. Override `LLAMA_EXTRA_ARGS` if you need different llama-server
options. The template defaults `PDOCKER_GPU_MODE` to `vulkan-raw` and
`LLAMA_ARG_N_GPU_LAYERS` to `2` so the first normal Compose run exercises the
pdocker Vulkan ICD beyond output-layer-only offload. Raise
`LLAMA_ARG_N_GPU_LAYERS` for deeper offload while tuning the bridge.

The image build pins `LLAMA_CPP_REF` to `b9030` and records the resolved commit
inside `/opt/llama.cpp/.pdocker-llama-cpp-commit`. It defaults CMake to
`MinSizeRel` and one build job. This is slower than a desktop-style `Release`
build, but avoids Android LMK/OOM failures while the direct executor and Vulkan
bridge are still being tuned. Raise `LLAMA_CPP_BUILD_JOBS` or set
`LLAMA_CPP_BUILD_TYPE=Release` only when the device has enough free memory and
swap for shader or Vulkan backend compilation. pdocker may apply an Android
build-executor profile outside the Dockerfile to keep upstream Dockerfiles
unchanged while reducing peak memory pressure on device.

The GPU profile action writes:

- `profiles/pdocker-gpu.env`, sourced by `start-llama-server.sh`
- `profiles/pdocker-gpu-diagnostics.json`, with the selected backend,
  recommendation reason, memory/thread/context choices, and CUDA/Vulkan signal
  booleans
- pdocker GPU bridge evidence when available. The profile script probes
  `pdocker-gpu-shim --queue-probe` and `--vector-add-fd`; a successful FD
  shared-buffer probe is recorded as bridge readiness, not as llama.cpp GPU
  acceleration until the llama backend is wired to the bridge.

All startup, download, status-page, and `llama-server` output is written to
stdout/stderr so `docker logs pdocker-llama-cpp` can show it. The same stream is
also copied to `/workspace/logs/llama-server.log`; override `LLAMA_LOG_FILE` to
change or disable that extra file.

Qwen3 weights are available under the Apache 2.0 license. This template
downloads the model at runtime; it is not bundled into the APK.

The compose file requests Docker-compatible `gpus: all`. pdockerd maps that to
its Vulkan passthrough / CUDA-compatible negotiation state where available.
GPU acceleration is considered real only when the glibc llama.cpp process uses
a container-facing pdocker GPU shim. Android/Bionic GPU libraries and services
may sit behind that shim, but they must not own the LLM engine or replace the
container's llama-server process.
The default llama-server port is `18081`, offset from common development ports
to reduce collisions with Android/Termux services.

#!/usr/bin/env bash
set -euo pipefail

profile="${LLAMA_GPU_PROFILE:-/profiles/pdocker-gpu.env}"
diagnostics="${LLAMA_GPU_DIAGNOSTICS:-/profiles/pdocker-gpu-diagnostics.json}"
refresh="${LLAMA_GPU_PROFILE_REFRESH:-auto}"
if [[ "$refresh" = "always" || ! -f "$profile" || ! -f "$diagnostics" || ( "$refresh" = "auto" && "${PDOCKER_GPU_AUTO:-}" = "1" ) ]]; then
  LLAMA_GPU_DIAGNOSTICS="$diagnostics" pdocker-gpu-profile "$profile" >/dev/null || true
fi
if [[ -f "$profile" ]]; then
  # shellcheck disable=SC1090
  source "$profile"
fi
if [[ "${LLAMA_GPU_BACKEND:-cpu}" = "cpu" ]]; then
  export GGML_VK_VISIBLE_DEVICES=""
  unset VK_ICD_FILENAMES
  unset VK_DRIVER_FILES
  unset CUDA_VISIBLE_DEVICES
  unset CUDA_DEVICE_ORDER
  unset OCL_ICD_VENDORS
  unset PDOCKER_VULKAN_PASSTHROUGH
  unset PDOCKER_VULKAN_ICD
  unset PDOCKER_VULKAN_ICD_KIND
  unset PDOCKER_OPENCL_PASSTHROUGH
  unset PDOCKER_OPENCL_ICD
  unset PDOCKER_OPENCL_ICD_KIND
fi

log_file="${LLAMA_LOG_FILE:-/workspace/logs/llama-server.log}"
if [[ -n "$log_file" ]]; then
  mkdir -p "$(dirname "$log_file")" /var/log/pdocker
  touch "$log_file"
  ln -sf "$log_file" /var/log/pdocker/llama-server.log
  exec > >(tee -a "$log_file") 2>&1
fi

model="${LLAMA_ARG_MODEL:-/models/model.gguf}"
model_url="${LLAMA_MODEL_URL:-}"
port="${LLAMA_ARG_PORT:-18081}"
ctx="${LLAMA_ARG_CTX:-4096}"
threads="${LLAMA_ARG_THREADS:-$(nproc 2>/dev/null || echo 4)}"
ngl="${LLAMA_ARG_N_GPU_LAYERS:-0}"
extra_args="${LLAMA_EXTRA_ARGS:---jinja}"
server="/opt/llama.cpp/build/bin/llama-server"

if [[ ! -f "$model" && -n "$model_url" ]]; then
  echo "Downloading GGUF model from LLAMA_MODEL_URL to $model"
  mkdir -p "$(dirname "$model")"
  partial="${model}.part"
  if curl -fL --retry 3 --retry-delay 2 -C - -o "$partial" "$model_url"; then
    mv "$partial" "$model"
  else
    echo "Model download failed; partial download remains at $partial" >&2
  fi
fi

if [[ ! -f "$model" ]]; then
  status_dir="/tmp/pdocker-llama-status"
  mkdir -p "$status_dir"
  cat >&2 <<EOF
Missing model: $model
Place a GGUF model at /models/model.gguf or set LLAMA_ARG_MODEL.
Optionally set LLAMA_MODEL_URL to download a GGUF at startup.
Current GPU profile:
$(cat "$profile" 2>/dev/null || true)
Current GPU diagnostics:
$(cat "$diagnostics" 2>/dev/null || true)
EOF
  cat > "$status_dir/index.html" <<EOF
<!doctype html>
<html>
<head><meta charset="utf-8"><title>pdocker llama.cpp</title></head>
<body>
<h1>pdocker llama.cpp workspace</h1>
<p><strong>Status:</strong> waiting for a GGUF model.</p>
<p>Expected model path: <code>$model</code></p>
<p>Place a model at <code>models/model.gguf</code>, set <code>LLAMA_ARG_MODEL</code>, or set <code>LLAMA_MODEL_URL</code> and compose up again. The default template downloads <code>Qwen/Qwen3-8B-GGUF</code>.</p>
<pre>$(cat "$profile" 2>/dev/null || true)</pre>
<pre>$(cat "$diagnostics" 2>/dev/null || true)</pre>
</body>
</html>
EOF
  cat > "$status_dir/status.txt" <<EOF
pdocker llama.cpp workspace is running.
Missing model: $model
Port: $port
Profile:
$(cat "$profile" 2>/dev/null || true)
Diagnostics:
$(cat "$diagnostics" 2>/dev/null || true)
EOF
  echo "llama.cpp status page: http://0.0.0.0:$port"
  exec python3 -u -m http.server "$port" --bind 0.0.0.0 --directory "$status_dir"
fi

echo "llama.cpp backend=${LLAMA_GPU_BACKEND:-unknown} ngl=$ngl threads=$threads ctx=$ctx port=$port log=$log_file"
if [[ -f "$diagnostics" ]]; then
  echo "llama.cpp gpu diagnostics:"
  cat "$diagnostics"
fi
exec stdbuf -oL -eL "$server" \
  --host 0.0.0.0 \
  --port "$port" \
  --model "$model" \
  --ctx-size "$ctx" \
  --threads "$threads" \
  --n-gpu-layers "$ngl" \
  ${extra_args}

#!/usr/bin/env bash
set -euo pipefail

profile="${LLAMA_GPU_PROFILE:-/profiles/pdocker-gpu.env}"
diagnostics="${LLAMA_GPU_DIAGNOSTICS:-/profiles/pdocker-gpu-diagnostics.json}"
refresh="${LLAMA_GPU_PROFILE_REFRESH:-auto}"
log_file="${LLAMA_LOG_FILE:-/workspace/logs/llama-server.log}"
if [[ -n "$log_file" ]]; then
  mkdir -p "$(dirname "$log_file")" /var/log/pdocker
  touch "$log_file"
  ln -sf "$log_file" /var/log/pdocker/llama-server.log
  exec > >(tee -a "$log_file") 2>&1
fi

profile_refresh_rc=0
if [[ "$refresh" = "always" || ! -f "$profile" || ! -f "$diagnostics" || ( "$refresh" = "auto" && "${PDOCKER_GPU_AUTO:-}" = "1" ) ]]; then
  echo "pdocker llama startup: refreshing GPU profile path=$profile diagnostics=$diagnostics refresh=$refresh"
  LLAMA_GPU_DIAGNOSTICS="$diagnostics" pdocker-gpu-profile "$profile" || profile_refresh_rc=$?
  echo "pdocker llama startup: GPU profile refresh rc=$profile_refresh_rc path=$profile diagnostics=$diagnostics"
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

model="${LLAMA_ARG_MODEL:-/models/model.gguf}"
model_url="${LLAMA_MODEL_URL:-}"
port="${LLAMA_ARG_PORT:-18081}"
ctx="${LLAMA_ARG_CTX:-4096}"
threads="${LLAMA_ARG_THREADS:-$(nproc 2>/dev/null || echo 4)}"
ngl="${LLAMA_ARG_N_GPU_LAYERS:-0}"
extra_args="${LLAMA_EXTRA_ARGS:---jinja}"
server="/opt/llama.cpp/build/bin/llama-server"
startup_json="${LLAMA_STARTUP_JSON:-/workspace/logs/llama-startup.json}"

has_llama_arg() {
  local needle="$1"
  case " $extra_args " in
    *" $needle "*) return 0 ;;
    *) return 1 ;;
  esac
}

kv_offload_guard_active=0
kv_offload_guard_added_arg=0
kv_offload_guard_arg_present_before=0
if has_llama_arg "--no-kv-offload" || has_llama_arg "-nkvo" || has_llama_arg "--kv-offload"; then
  kv_offload_guard_arg_present_before=1
fi
if [[ "${LLAMA_GPU_BACKEND:-}" = "vulkan" \
      && "${PDOCKER_VULKAN_ICD_KIND:-}" = pdocker-* \
      && "${PDOCKER_VULKAN_ICD_READY:-0}" != "1" \
      && "${PDOCKER_VULKAN_ALLOW_KV_OFFLOAD:-0}" != "1" ]]; then
  kv_offload_guard_active=1
  export LLAMA_ARG_KV_OFFLOAD=0
  if ! has_llama_arg "--no-kv-offload" && ! has_llama_arg "-nkvo" && ! has_llama_arg "--kv-offload"; then
    extra_args="--no-kv-offload ${extra_args}"
    kv_offload_guard_added_arg=1
  fi
  echo "Skydnir: disabling llama.cpp KV cache offload for unfinished Skydnir Vulkan ICD; set PDOCKER_VULKAN_ALLOW_KV_OFFLOAD=1 to override"
fi

mkdir -p "$(dirname "$startup_json")"
python3 - "$startup_json" "$profile_refresh_rc" "$profile" "$diagnostics" "$refresh" "$server" "$model" "$port" "$ctx" "$threads" "$ngl" "$extra_args" "$kv_offload_guard_active" "$kv_offload_guard_added_arg" "$kv_offload_guard_arg_present_before" <<'PY' || true
import json
import os
import sys
import time

(
    out, profile_rc, profile, diagnostics, refresh, server, model, port, ctx,
    threads, ngl, extra_args, kv_guard_active, kv_guard_added_arg,
    kv_guard_arg_present_before,
) = sys.argv[1:16]
interesting = (
    "LLAMA_",
    "PDOCKER_GPU_",
    "PDOCKER_VULKAN_",
    "GGML_VK_",
    "VK_ICD_FILENAMES",
    "VK_DRIVER_FILES",
    "OCL_ICD_VENDORS",
)
env = {
    key: value
    for key, value in sorted(os.environ.items())
    if any(key == prefix or key.startswith(prefix) for prefix in interesting)
}
meminfo = {}
try:
    with open("/proc/meminfo", encoding="utf-8", errors="replace") as f:
        for line in f:
            name, _, rest = line.partition(":")
            if name in {"MemAvailable", "MemFree", "SwapFree", "SwapTotal"}:
                meminfo[name] = rest.strip()
except OSError:
    pass
argv = [
    server,
    "--host", "0.0.0.0",
    "--port", port,
    "--model", model,
    "--ctx-size", ctx,
    "--threads", threads,
    "--n-gpu-layers", ngl,
] + extra_args.split()
kv_offload_arg_present = any(arg in {"--no-kv-offload", "-nkvo"} for arg in argv)
kv_offload_env = os.environ.get("LLAMA_ARG_KV_OFFLOAD")
resolved = {
    "LLAMA_GPU_BACKEND": os.environ.get("LLAMA_GPU_BACKEND", ""),
    "LLAMA_ARG_N_GPU_LAYERS": ngl,
    "LLAMA_ARG_CTX": ctx,
    "LLAMA_ARG_THREADS": threads,
    "VK_ICD_FILENAMES": os.environ.get("VK_ICD_FILENAMES", ""),
    "PDOCKER_GPU_QUEUE_SOCKET": os.environ.get("PDOCKER_GPU_QUEUE_SOCKET", ""),
    "PDOCKER_VULKAN_ICD_KIND": os.environ.get("PDOCKER_VULKAN_ICD_KIND", ""),
    "PDOCKER_VULKAN_ICD_READY": os.environ.get("PDOCKER_VULKAN_ICD_READY", ""),
}
kv_guard_active_bool = kv_guard_active == "1"
kv_guard_added_arg_bool = kv_guard_added_arg == "1"
kv_guard_arg_present_before_bool = kv_guard_arg_present_before == "1"
kv_offload_disabled_effective = kv_offload_arg_present or kv_offload_env == "0"
report = {
    "schema": "pdocker.llama.startup.v1",
    "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "profile": profile,
    "profile_path": profile,
    "diagnostics": diagnostics,
    "diagnostics_path": diagnostics,
    "profile_refresh": refresh,
    "profile_refresh_rc": int(profile_rc),
    "resolved": resolved,
    "env": env,
    "meminfo": meminfo,
    "memory": {
        "MemAvailable": meminfo.get("MemAvailable", ""),
        "SwapFree": meminfo.get("SwapFree", ""),
    },
    "argv": argv,
    "llama_server_argv": argv,
    "kv_offload_env": kv_offload_env,
    "kv_offload_arg_present": kv_offload_arg_present,
    "kv_offload_disabled_effective": kv_offload_disabled_effective,
    "kv_offload_guarded": kv_guard_active_bool,
    "kv_offload_guard": {
        "active": kv_guard_active_bool,
        "added_arg": kv_guard_added_arg_bool,
        "arg_present_before": kv_guard_arg_present_before_bool,
        "disabled_effective": kv_offload_disabled_effective,
        "env": kv_offload_env,
        "backend": resolved["LLAMA_GPU_BACKEND"],
        "icd_kind": resolved["PDOCKER_VULKAN_ICD_KIND"],
        "icd_ready": resolved["PDOCKER_VULKAN_ICD_READY"],
        "allow_override": os.environ.get("PDOCKER_VULKAN_ALLOW_KV_OFFLOAD", ""),
    },
}
with open(out, "w", encoding="utf-8") as f:
    json.dump(report, f, indent=2, sort_keys=True)
    f.write("\n")
print("pdocker llama startup diagnostics: " + out)
PY

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

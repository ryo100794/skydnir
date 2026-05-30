#!/usr/bin/env bash
set -euo pipefail

out="${1:-/profiles/pdocker-gpu.env}"
diagnostics="${LLAMA_GPU_DIAGNOSTICS:-}"
if [[ -z "$diagnostics" ]]; then
  diagnostics="${out%.env}-diagnostics.json"
  if [[ "$diagnostics" = "$out" ]]; then
    diagnostics="${out}.diagnostics.json"
  fi
fi
mkdir -p "$(dirname "$out")"
mkdir -p "$(dirname "$diagnostics")"

json_escape() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  value="${value//$'\n'/\\n}"
  value="${value//$'\r'/\\r}"
  value="${value//$'\t'/\\t}"
  printf '%s' "$value"
}

json_int_or_string() {
  local value="$1"
  if [[ "$value" =~ ^[0-9]+$ ]]; then
    printf '%s' "$value"
  else
    printf '"%s"' "$(json_escape "$value")"
  fi
}

shell_quote() {
  printf '%q' "$1"
}

threads="${LLAMA_ARG_THREADS:-}"
if [[ -z "$threads" ]]; then
  threads="$(nproc 2>/dev/null || echo 4)"
fi

mem_kb="$(awk '/MemTotal/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)"
if [[ ! "$mem_kb" =~ ^[0-9]+$ ]]; then
  mem_kb="0"
fi
ctx="${LLAMA_ARG_CTX:-4096}"
if [[ "$mem_kb" -gt 0 && "$mem_kb" -lt 5000000 ]]; then
  ctx=2048
fi

backend="cpu"
ngl="0"
extra="${LLAMA_EXTRA_ARGS:-}"
reason="no validated glibc GPU bridge detected; using CPU fallback"
cuda_signal="false"
vulkan_env_signal="false"
vulkan_icd_signal="false"
vulkaninfo_signal="false"
pdocker_vulkan_icd_signal="false"
nvidia_device_signal="false"
opencl_signal="false"
pdocker_opencl_icd_signal="false"
pdocker_opencl_icd_ready_signal="false"
bridge_shim_signal="false"
bridge_queue_signal="false"
bridge_fd_signal="false"
bridge_probe_json=""
bridge_fd_probe_json=""
mode="${PDOCKER_GPU_MODE:-${PDOCKER_GPU:-auto}}"
mode="$(printf '%s' "$mode" | tr '[:upper:]' '[:lower:]')"

run_probe() {
  if command -v timeout >/dev/null 2>&1; then
    timeout 10 "$@" 2>/dev/null || true
  else
    "$@" 2>/dev/null || true
  fi
}

if [[ "${PDOCKER_CUDA_COMPAT:-}" = "1" ]]; then
  cuda_signal="true"
fi
if [[ "${PDOCKER_VULKAN_PASSTHROUGH:-}" = "1" ]]; then
  vulkan_env_signal="true"
fi
if [[ -n "${VK_ICD_FILENAMES:-}" || -e /etc/vulkan/icd.d/pdocker-android.json ]]; then
  vulkan_icd_signal="true"
fi
if [[ "${PDOCKER_VULKAN_ICD_KIND:-}" = pdocker-* || "${PDOCKER_VULKAN_ICD:-}" = *pdocker-vulkan-icd.so ]]; then
  pdocker_vulkan_icd_signal="true"
fi
if [[ -e /dev/nvidia0 ]]; then
  nvidia_device_signal="true"
fi
if [[ "${PDOCKER_OPENCL_PASSTHROUGH:-}" = "1" || -n "${OCL_ICD_VENDORS:-}" || -e /etc/OpenCL/vendors/pdocker-android.icd ]]; then
  opencl_signal="true"
fi
if [[ "${PDOCKER_OPENCL_ICD_KIND:-}" = pdocker-* || "${PDOCKER_OPENCL_ICD:-}" = *pdocker-opencl-icd.so || -e /usr/local/lib/pdocker-opencl-icd.so ]]; then
  pdocker_opencl_icd_signal="true"
fi
if [[ "$pdocker_opencl_icd_signal" = "true" && "${PDOCKER_OPENCL_ICD_READY:-0}" = "1" ]]; then
  pdocker_opencl_icd_ready_signal="true"
fi
if command -v pdocker-gpu-shim >/dev/null 2>&1; then
  bridge_shim_signal="true"
fi
if [[ -z "${PDOCKER_GPU_QUEUE_SOCKET:-}" && -S /run/pdocker-gpu/pdocker-gpu.sock ]]; then
  export PDOCKER_GPU_QUEUE_SOCKET=/run/pdocker-gpu/pdocker-gpu.sock
fi
if [[ -n "${PDOCKER_GPU_QUEUE_SOCKET:-}" ]]; then
  bridge_queue_signal="true"
fi
if [[ "$bridge_shim_signal" = "true" && "$bridge_queue_signal" = "true" ]]; then
  bridge_probe_json="$(run_probe pdocker-gpu-shim --queue-probe | tail -n 1)"
  bridge_fd_probe_json="$(run_probe pdocker-gpu-shim --vector-add-fd | tail -n 1)"
  if printf '%s' "$bridge_fd_probe_json" | grep -q '"valid":true'; then
    bridge_fd_signal="true"
  fi
fi

if [[ "$mode" = "cpu" || "$mode" = "off" || "$mode" = "none" || "${PDOCKER_GPU_AUTO:-}" = "0" ]]; then
  backend="cpu"
  ngl="0"
  reason="PDOCKER_GPU_MODE requests CPU-only execution"
elif [[ "$mode" = "vulkan-raw" || "$mode" = "android-vulkan-raw" ]]; then
  backend="vulkan"
  ngl="${LLAMA_ARG_N_GPU_LAYERS:-999}"
  reason="raw Android Vulkan library exposure was explicitly requested"
elif [[ "$pdocker_vulkan_icd_signal" = "true" && "${PDOCKER_VULKAN_ICD_READY:-0}" != "1" ]]; then
  backend="cpu"
  ngl="0"
  if [[ "$bridge_fd_signal" = "true" ]]; then
    reason="Skydnir Vulkan ICD is visible and the GPU bridge validates, but the Vulkan compute lowering is not complete yet; using CPU fallback"
  else
    reason="Skydnir Vulkan ICD is visible, but the GPU bridge is not validated yet; using CPU fallback"
  fi
elif [[ "$mode" = "cuda" || "$mode" = "cuda-compat" || "${PDOCKER_CUDA_COMPAT:-}" = "1" || -e /dev/nvidia0 ]]; then
  backend="cpu"
  ngl="0"
  if [[ "$bridge_fd_signal" = "true" ]]; then
    reason="CUDA-compatible mode was requested and the Skydnir GPU bridge validated, but llama.cpp bridge backend is not wired yet; using CPU fallback"
  else
    reason="CUDA-compatible mode was requested, but no validated glibc GPU bridge exists; using CPU fallback"
  fi
elif [[ "$pdocker_opencl_icd_ready_signal" = "true" ]]; then
  backend="cpu"
  ngl="0"
  if [[ "$bridge_fd_signal" = "true" ]]; then
    reason="Skydnir OpenCL ICD is ready and the shared-buffer GPU bridge validates, but llama.cpp OpenCL kernel lowering is not complete yet; using CPU fallback"
  else
    reason="Skydnir OpenCL ICD is ready, but the shared-buffer GPU bridge did not validate yet; using CPU fallback"
  fi
elif command -v vulkaninfo >/dev/null 2>&1 && vulkaninfo --summary >/dev/null 2>&1; then
  backend="vulkan"
  ngl="${LLAMA_ARG_N_GPU_LAYERS:-999}"
  reason="vulkaninfo --summary succeeded"
  vulkaninfo_signal="true"
elif [[ "$bridge_fd_signal" = "true" ]]; then
  backend="cpu"
  ngl="0"
  reason="Skydnir GPU bridge validated a shared-buffer command, but llama.cpp bridge backend is not wired yet; using CPU fallback"
fi

cat > "$out" <<EOF
LLAMA_GPU_BACKEND=$backend
LLAMA_ARG_THREADS=$threads
LLAMA_ARG_CTX=$ctx
LLAMA_ARG_N_GPU_LAYERS=$ngl
LLAMA_EXTRA_ARGS=$(shell_quote "$extra")
EOF

cat > "$diagnostics" <<EOF
{
  "backend": "$(json_escape "$backend")",
  "recommendation": "$(json_escape "$backend")",
  "reason": "$(json_escape "$reason")",
  "mode": "$(json_escape "$mode")",
  "threads": $(json_int_or_string "$threads"),
  "ctx": $(json_int_or_string "$ctx"),
  "n_gpu_layers": $(json_int_or_string "$ngl"),
  "mem_total_kb": $(json_int_or_string "$mem_kb"),
  "signals": {
    "pdocker_cuda_compat": $cuda_signal,
    "pdocker_vulkan_passthrough": $vulkan_env_signal,
    "vk_icd_filenames": "$(json_escape "${VK_ICD_FILENAMES:-}")",
    "pdocker_icd_file": $vulkan_icd_signal,
    "pdocker_vulkan_icd": $pdocker_vulkan_icd_signal,
    "pdocker_vulkan_icd_kind": "$(json_escape "${PDOCKER_VULKAN_ICD_KIND:-}")",
    "pdocker_vulkan_icd_ready": "$(json_escape "${PDOCKER_VULKAN_ICD_READY:-0}")",
    "vulkaninfo_summary": $vulkaninfo_signal,
    "nvidia_device": $nvidia_device_signal,
    "pdocker_opencl_passthrough": $opencl_signal,
    "pdocker_opencl_icd": $pdocker_opencl_icd_signal,
    "pdocker_opencl_icd_kind": "$(json_escape "${PDOCKER_OPENCL_ICD_KIND:-}")",
    "pdocker_opencl_icd_ready": "$(json_escape "${PDOCKER_OPENCL_ICD_READY:-0}")",
    "pdocker_opencl_api_version": "$(json_escape "${PDOCKER_OPENCL_API_VERSION:-}")",
    "pdocker_opencl_icd_path": "$(json_escape "${PDOCKER_OPENCL_ICD:-}")",
    "ocl_icd_vendors": "$(json_escape "${OCL_ICD_VENDORS:-}")",
    "pdocker_gpu_shim": $bridge_shim_signal,
    "pdocker_gpu_queue": $bridge_queue_signal,
    "pdocker_gpu_fd_shared_buffer": $bridge_fd_signal
  },
  "pdocker_gpu_bridge": {
    "api": "$(json_escape "${PDOCKER_GPU_COMMAND_API:-pdocker-gpu-command-v1}")",
    "queue_socket": "$(json_escape "${PDOCKER_GPU_QUEUE_SOCKET:-/run/pdocker-gpu/pdocker-gpu.sock}")",
    "shared_dir": "$(json_escape "${PDOCKER_GPU_SHARED_DIR:-/run/pdocker-gpu}")",
    "capability_probe": "$(json_escape "$bridge_probe_json")",
    "fd_vector_add_probe": "$(json_escape "$bridge_fd_probe_json")",
    "llama_backend_wired": false,
    "kv_offload_guard": "$(json_escape "Skydnir Vulkan ICD keeps llama.cpp KV cache on CPU until PDOCKER_VULKAN_ICD_READY=1 or PDOCKER_VULKAN_ALLOW_KV_OFFLOAD=1")"
  },
  "outputs": {
    "env": "$(json_escape "$out")",
    "diagnostics": "$(json_escape "$diagnostics")"
  }
}
EOF

cat "$out"
printf '\nGPU diagnostics: %s\n' "$diagnostics"

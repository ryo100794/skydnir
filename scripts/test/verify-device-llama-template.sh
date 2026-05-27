#!/usr/bin/env bash
set -euo pipefail

DEVICE="${1:-${ANDROID_SERIAL:-}}"
PKG="${SKYDNIR_PACKAGE:-${PDOCKER_PACKAGE:-io.github.ryo100794.pdocker.compat}}"
ADB=(adb)
if [[ -n "$DEVICE" ]]; then
  ADB+=( -s "$DEVICE" )
fi

PROJECT="/data/data/$PKG/files/pdocker/projects/llama-cpp-gpu"
DOCKERFILE="$PROJECT/Dockerfile"
COMPOSE="$PROJECT/compose.yaml"
START="$PROJECT/scripts/start-llama-server.sh"

"${ADB[@]}" shell run-as "$PKG" test -f "$DOCKERFILE"
"${ADB[@]}" shell run-as "$PKG" test -f "$COMPOSE"
"${ADB[@]}" shell run-as "$PKG" test -f "$START"

dockerfile_text="$("${ADB[@]}" shell run-as "$PKG" cat "$DOCKERFILE")"
compose_text="$("${ADB[@]}" shell run-as "$PKG" cat "$COMPOSE")"
start_text="$("${ADB[@]}" shell run-as "$PKG" cat "$START")"
combined_text="$dockerfile_text
$compose_text
$start_text"

if grep -F -q -- 'LLAMA_CPP_VULKAN_SHADER_PROFILE' <<<"$combined_text"; then
  echo "FAIL: installed llama-cpp-gpu project still contains stale pdocker shader wrapper tuning" >&2
  exit 1
fi
if grep -F -q -- 'pdocker-bridge-safe-glslc' <<<"$combined_text"; then
  echo "FAIL: installed llama-cpp-gpu project still contains stale pdocker shader wrapper tuning" >&2
  exit 1
fi

grep -F -q -- 'git checkout --detach FETCH_HEAD' <<<"$dockerfile_text"
grep -F -q -- 'LLAMA_CPP_REF=b9030' <<<"$dockerfile_text"
grep -F -q -- 'LLAMA_CPP_BUILD_JOBS:-1' <<<"$compose_text"
grep -F -q -- 'LLAMA_CPP_BUILD_TYPE:-Release' <<<"$compose_text"
grep -F -q -- 'LLAMA_ARG_N_GPU_LAYERS:-1' <<<"$compose_text"
grep -F -q -- 'ARG LLAMA_CPP_BUILD_TYPE=Release' <<<"$dockerfile_text"
grep -F -q -- 'PDOCKER_VULKAN_ALLOW_KV_OFFLOAD' <<<"$compose_text"
grep -F -q -- 'PDOCKER_VULKAN_ALLOW_KV_OFFLOAD' <<<"$start_text"
grep -F -q -- '--no-kv-offload' <<<"$start_text"
grep -F -q -- 'llama-startup.json' <<<"$start_text"
grep -F -q -- 'profile_path' <<<"$start_text"
grep -F -q -- 'llama_server_argv' <<<"$start_text"
grep -F -q -- 'kv_offload_guard' <<<"$start_text"

echo "ok: installed llama-cpp-gpu project matches current template migration"

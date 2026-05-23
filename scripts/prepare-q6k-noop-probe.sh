#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SPV="$ROOT/docs/test/spirv-q6k-native-adb45055/native-q6-source.spv"
OUT_DIR="$ROOT/docs/test/q6k-noop-probe-latest"
DEBUG_BYTES="${PDOCKER_Q6K_PROBE_DEBUG_BYTES:-65536}"
TARGET_ENV="${PDOCKER_Q6K_PROBE_TARGET_ENV:-vulkan1.2}"
PROBE_WRITES=0

usage() {
  cat <<'EOF'
usage: scripts/prepare-q6k-noop-probe.sh [--spv PATH] [--out-dir DIR] [--probe-writes]

Prepare a deterministic Q6K no-op SPIR-V probe bundle.  This does not rebuild
the llama image, does not modify llama.cpp/Dockerfile/model/prompt, and does not
dispatch anything.  It only creates validated artifacts and an env file for the
next device run.  --probe-writes keeps the same whole-module validation path but
adds executable Q6K debug SSBO stores for the manifest priority targets.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --spv)
      SPV="$2"
      shift 2
      ;;
    --out-dir)
      OUT_DIR="$2"
      shift 2
      ;;
    --probe-writes)
      PROBE_WRITES=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

mkdir -p "$OUT_DIR"

MANIFEST="$OUT_DIR/native-q6.probe.json"
if [[ "$PROBE_WRITES" == "1" ]]; then
  PROBE_KIND="write"
else
  PROBE_KIND="noop"
fi
NOOP_SPV="$OUT_DIR/native-q6.$PROBE_KIND.spv"
NOOP_MANIFEST="$OUT_DIR/native-q6.$PROBE_KIND.probe.json"
NOOP_ASM="$OUT_DIR/native-q6.$PROBE_KIND.spvasm"
INSTRUMENTATION_JSON="$OUT_DIR/native-q6.$PROBE_KIND.instrumentation.json"
VERIFY_SOURCE_JSON="$OUT_DIR/native-q6.probe.verify.json"
VERIFY_NOOP_JSON="$OUT_DIR/native-q6.noop.probe.verify.json"
ENV_FILE="$OUT_DIR/noop-probe.env"
SUMMARY_JSON="$OUT_DIR/summary.json"

python3 "$ROOT/scripts/analyze-spirv.py" "$SPV" \
  --probe-plan-out "$MANIFEST" \
  --probe-range 0:2 \
  --json-out "$OUT_DIR/native-q6.analysis.json" \
  --disassemble-dir "$OUT_DIR/disasm" >/dev/null

python3 "$ROOT/scripts/verify-spirv-probe-manifest.py" "$MANIFEST" \
  --json-out "$VERIFY_SOURCE_JSON" >/dev/null

INSTRUMENT_ARGS=(
  "$ROOT/scripts/instrument-spirv-noop-probe.py" "$SPV" "$NOOP_SPV"
  --manifest-in "$MANIFEST"
  --manifest-out "$NOOP_MANIFEST"
  --asm-out "$NOOP_ASM"
  --target-env "$TARGET_ENV"
)
if [[ "$PROBE_WRITES" == "1" ]]; then
  INSTRUMENT_ARGS+=(--probe-writes)
fi
python3 "${INSTRUMENT_ARGS[@]}" >"$INSTRUMENTATION_JSON"

python3 "$ROOT/scripts/verify-spirv-probe-manifest.py" "$NOOP_MANIFEST" \
  --json-out "$VERIFY_NOOP_JSON" >/dev/null

python3 - "$MANIFEST" "$NOOP_MANIFEST" "$INSTRUMENTATION_JSON" "$ENV_FILE" "$SUMMARY_JSON" "$NOOP_SPV" "$DEBUG_BYTES" <<'PY'
import json
import shlex
import sys
from pathlib import Path

manifest_path = Path(sys.argv[1])
noop_manifest_path = Path(sys.argv[2])
instrumentation_path = Path(sys.argv[3])
env_path = Path(sys.argv[4])
summary_path = Path(sys.argv[5])
noop_spv = Path(sys.argv[6])
debug_bytes = int(sys.argv[7])

manifest = json.loads(manifest_path.read_text())
noop_manifest = json.loads(noop_manifest_path.read_text())
instrumentation = json.loads(instrumentation_path.read_text())
debug = noop_manifest["debug_ssbo"]["descriptor"]
source_hash = manifest["basis"]["module_hash"]
effective_hash = instrumentation["instrumented_spirv_hash"]

env = {
    "PDOCKER_GPU_SPIRV_PROBE_MANIFEST": str(noop_manifest_path),
    "PDOCKER_GPU_SPIRV_PROBE_SHADER": str(noop_spv),
    "PDOCKER_GPU_SPIRV_PROBE_EXPECTED_HASH": source_hash,
    "PDOCKER_GPU_SPIRV_PROBE_EFFECTIVE_HASH": effective_hash,
    "PDOCKER_GPU_SPIRV_PROBE_DEBUG_BYTES": str(debug_bytes),
    "PDOCKER_GPU_SPIRV_PROBE_DEBUG_SET": str(debug["set"]),
    "PDOCKER_GPU_SPIRV_PROBE_DEBUG_BINDING": str(debug["binding"]),
    "PDOCKER_GPU_SPIRV_PROBE_TARGET_ONLY": "1",
}
env_path.write_text(
    "\n".join(f"export {key}={shlex.quote(value)}" for key, value in env.items()) + "\n"
)
summary = {
    "schema": f"pdocker.q6k.{instrumentation['instrumentation']['kind']}.bundle.v1",
    "valid": True,
    "source_manifest": str(manifest_path),
    "noop_manifest": str(noop_manifest_path),
    "noop_spirv": str(noop_spv),
    "env_file": str(env_path),
    "debug_bytes": debug_bytes,
    "source_spirv_hash": source_hash,
    "instrumented_spirv_hash": effective_hash,
    "debug_descriptor": {
        "set": debug["set"],
        "binding": debug["binding"],
        "descriptor_type": "storage_buffer",
    },
    "policy": {
        "llama_cpp_modified": False,
        "dockerfile_model_prompt_modified": False,
        "vulkan_dispatch_v4_abi_changed": False,
        "dispatch_performed": False,
        "executable_probe_writes": instrumentation["instrumentation"]["executable_probe_writes"],
        "target_only_nonmatching_shaders_passthrough": True,
    },
}
summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
print(json.dumps(summary, indent=2, sort_keys=True))
PY

echo
echo "Prepared Q6K no-op probe bundle:"
echo "  $SUMMARY_JSON"
echo "To use in the next device run, source:"
echo "  $ENV_FILE"

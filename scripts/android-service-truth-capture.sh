#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SMOKE="$ROOT/scripts/android-device-smoke.sh"
ADB_BIN="${ADB:-adb}"
TARGET="default-workspace"
NO_INSTALL=1
PRINT_PLAN=0
ARTIFACT_DIR="${SKYDNIR_SMOKE_ARTIFACT_DIR:-${PDOCKER_SMOKE_ARTIFACT_DIR:-$ROOT/tmp/device-smoke-artifacts/service-truth-$(date -u +%Y%m%dT%H%M%SZ)}}"

usage() {
  cat <<USAGE
Usage: $0 [--target <default-workspace|llama>] [--install] [--no-install] [--artifact-dir DIR] [--print-plan]

Concrete command path for collecting service-truth device evidence.  The script
is safe in hosts without adb when --print-plan is used; without --print-plan it
requires adb and a connected device and delegates to:

  SKYDNIR_SMOKE_ARTIFACT_DIR=<dir> bash scripts/android-device-smoke.sh --no-install --service-truth <target>

Collected on device under files/pdocker/diagnostics/service-truth/ and copied to
<dir> when adb/run-as is available:
  - UI card: ui-rendered-service-truth-latest.json
  - docker ps: engine-ps.out, engine-ps-running.out, engine-candidates.json
  - Engine API /containers/json?all=1: engine-containers-json.http
  - state.json: persisted-state-json.txt, state-id-comparison.json
  - process table: process-table.txt plus inspect-selected.http PID
  - listener owner: proc-net-tcp.txt, listener-probe.json, listener-owner-map.json/.tsv
  - logs: logs-selected.out and logs-<container-id>.out

The smoke runner may report device-pass only after all seven sources reduce to
the same exact 64-hex Engine container ID. This wrapper never manufactures or
edits files/pdocker/diagnostics/service-truth-latest.json and never promotes a
planned-gap artifact to a pass.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target)
      [[ $# -ge 2 ]] || { echo "--target requires a value" >&2; exit 2; }
      TARGET="$2"
      shift
      ;;
    --install) NO_INSTALL=0 ;;
    --no-install) NO_INSTALL=1 ;;
    --artifact-dir)
      [[ $# -ge 2 ]] || { echo "--artifact-dir requires a directory" >&2; exit 2; }
      ARTIFACT_DIR="$2"
      shift
      ;;
    --print-plan|--dry-run) PRINT_PLAN=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

case "$TARGET" in
  default-workspace|llama) ;;
  *) echo "--target must be default-workspace or llama (got '$TARGET')" >&2; exit 2 ;;
esac

install_arg="--no-install"
if [[ "$NO_INSTALL" -eq 0 ]]; then
  install_arg=""
fi

print_plan() {
  cat <<PLAN
Service truth device capture plan (no device pass is claimed here)

1. Connect one Android device with the debug app installed/runnable via run-as.
2. Run:
     export SKYDNIR_SMOKE_ARTIFACT_DIR=$(printf '%q' "$ARTIFACT_DIR")
     export ADB=$(printf '%q' "$ADB_BIN")
     bash scripts/android-device-smoke.sh ${install_arg:+$install_arg }--service-truth $(printf '%q' "$TARGET")
3. Inspect host artifacts copied from the device:
     $ARTIFACT_DIR/service-truth-latest.json
     $ARTIFACT_DIR/service-truth/ (if pulled by the smoke environment)
4. Required same Engine container ID evidence:
     UICard -> files/pdocker/diagnostics/service-truth/ui-rendered-service-truth-latest.json
     DockerPs -> files/pdocker/diagnostics/service-truth/engine-ps.out
     EngineApiContainersJson -> files/pdocker/diagnostics/service-truth/engine-containers-json.http (/containers/json?all=1)
     PersistedStateJson -> files/pdocker/diagnostics/service-truth/state-id-comparison.json and persisted-state-json.txt
     ProcessTable -> files/pdocker/diagnostics/service-truth/process-table.txt
     ListenerProbe -> files/pdocker/diagnostics/service-truth/listener-owner-map.json and proc-net-tcp.txt
     ContainerLogs -> files/pdocker/diagnostics/service-truth/logs-selected.out and logs-<container-id>.out
5. Acceptance remains false unless service-truth-latest.json proves the exact
   same 64-hex Proof.EngineContainerId in all seven sources. planned-gap,
   missing adb, missing UI card, prefix-only IDs, configured-port-only evidence,
   stale state.json, stale logs, or mismatched listener owner are not passes.
PLAN
}

if [[ "$PRINT_PLAN" -eq 1 ]]; then
  print_plan
  exit 0
fi

if ! command -v "$ADB_BIN" >/dev/null 2>&1; then
  echo "adb executable not found: $ADB_BIN" >&2
  echo "Use --print-plan to show the exact command path without executing adb." >&2
  exit 127
fi

if ! "$ADB_BIN" get-state >/dev/null 2>&1; then
  echo "no connected adb device is ready" >&2
  echo "Use --print-plan to show the exact command path without executing adb." >&2
  exit 3
fi

mkdir -p "$ARTIFACT_DIR"
export SKYDNIR_SMOKE_ARTIFACT_DIR="$ARTIFACT_DIR"
export PDOCKER_SMOKE_ARTIFACT_DIR="$ARTIFACT_DIR"
export ADB="$ADB_BIN"
cmd=(bash "$SMOKE")
if [[ "$NO_INSTALL" -eq 1 ]]; then
  cmd+=(--no-install)
fi
cmd+=(--service-truth "$TARGET")

printf 'Running service truth device capture: '
printf '%q ' "${cmd[@]}"
printf '\nArtifacts: %s\n' "$ARTIFACT_DIR"
exec "${cmd[@]}"

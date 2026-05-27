#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

DEFAULT_PACKAGE="io.github.ryo100794.pdocker.compat"
PYTHON="${PYTHON:-python3}"
OUT="${PDOCKER_STORAGE_METRICS_SEQUENCE_ARTIFACT:-$ROOT/docs/test/storage-metrics-sequence-latest.json}"
WORKDIR="${PDOCKER_STORAGE_METRICS_SEQUENCE_WORKDIR:-$ROOT/tmp/storage-metrics-sequence/$(date -u +%Y%m%dT%H%M%SZ)-$$}"
ADB_BIN="${ADB:-adb}"
ANDROID_SERIAL_ARG="${ANDROID_SERIAL:-${ADB_SERIAL:-}}"
PKG="${SKYDNIR_ANDROID_PACKAGE:-${SKYDNIR_PACKAGE:-${PDOCKER_ANDROID_PACKAGE:-${PDOCKER_PACKAGE:-$DEFAULT_PACKAGE}}}}"
VERIFY="${PDOCKER_STORAGE_METRICS_VERIFY:-$ROOT/scripts/verify-storage-metrics.py}"
SOCKET="${PDOCKER_STORAGE_METRICS_SOCKET:-pdockerd.sock}"
TIMEOUT="${PDOCKER_STORAGE_METRICS_TIMEOUT:-5}"

usage() {
  cat <<USAGE
Usage: $(basename "$0") [options]

Non-destructive Android storage-metrics sequence scaffold. It removes the
previous output artifact, attempts only a read-only baseline capture through
scripts/verify-storage-metrics.py --capture-device, records the planned
build/rebuild/edit/prune phases, and writes success=false until all real device
phase captures are present and freshly validated.

Options:
  --out PATH        sequence artifact path (default: docs/test/storage-metrics-sequence-latest.json)
  --workdir PATH    scratch directory for baseline/log evidence
  --adb PATH        adb executable (default: ADB env or adb)
  --serial SERIAL   adb serial passed through a temporary wrapper
  --package NAME    Android package (default: PDOCKER_ANDROID_PACKAGE or compat package)
  --verify PATH     verify-storage-metrics.py path
  --socket NAME     pdockerd socket relative to files/pdocker (default: pdockerd.sock)
  --timeout SEC     nc timeout forwarded to verify-storage-metrics.py (default: 5)
  -h, --help        show this help
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --out) OUT="${2:?--out requires a path}"; shift 2 ;;
    --workdir) WORKDIR="${2:?--workdir requires a path}"; shift 2 ;;
    --adb) [[ $# -ge 2 ]] || { echo "--adb requires a path" >&2; exit 2; }; ADB_BIN="$2"; shift 2 ;;
    --serial) ANDROID_SERIAL_ARG="${2:?--serial requires a value}"; shift 2 ;;
    --package) [[ $# -ge 2 ]] || { echo "--package requires a value" >&2; exit 2; }; PKG="$2"; shift 2 ;;
    --verify) VERIFY="${2:?--verify requires a path}"; shift 2 ;;
    --socket) SOCKET="${2:?--socket requires a value}"; shift 2 ;;
    --timeout) TIMEOUT="${2:?--timeout requires a value}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

mkdir -p "$(dirname "$OUT")" "$WORKDIR"
BASELINE_JSON="$WORKDIR/baseline-snapshot.json"
CAPTURE_LOG="$WORKDIR/baseline-capture.log"
CAPTURE_STATUS="$WORKDIR/baseline-capture.status"
SEQUENCE_VERIFY_LOG="$WORKDIR/sequence-verify.log"
SEQUENCE_VERIFY_STATUS="$WORKDIR/sequence-verify.status"
INPUT_FAILURES_FILE="$WORKDIR/input-failures.txt"
: >"$INPUT_FAILURES_FILE"
: >"$CAPTURE_LOG"
: >"$SEQUENCE_VERIFY_LOG"
printf 'not-run\n' >"$CAPTURE_STATUS"
printf 'not-run\n' >"$SEQUENCE_VERIFY_STATUS"

PREVIOUS_ARTIFACT_PRESENT=false
if [[ -e "$OUT" ]]; then
  PREVIOUS_ARTIFACT_PRESENT=true
fi
# Never allow a stale success artifact to satisfy the sequence gate.
rm -f "$OUT"
rm -f "$BASELINE_JSON"

record_failure() {
  printf '%s\n' "$1" >>"$INPUT_FAILURES_FILE"
}

if [[ -z "$ADB_BIN" ]]; then
  record_failure "adb executable is required for Android storage metrics sequence capture"
elif ! command -v -- "$ADB_BIN" >/dev/null 2>&1; then
  record_failure "adb executable was not found: $ADB_BIN"
fi

if [[ -z "$PKG" ]]; then
  record_failure "Android package is required for adb run-as capture"
fi

if [[ ! -f "$VERIFY" ]]; then
  record_failure "verify-storage-metrics.py is not available: $VERIFY"
fi

if ! [[ "$TIMEOUT" =~ ^[0-9]+$ ]] || [[ "$TIMEOUT" -le 0 ]]; then
  record_failure "timeout must be a positive integer"
fi

ADB_FOR_VERIFY="$ADB_BIN"
if [[ -n "$ANDROID_SERIAL_ARG" && -n "$ADB_BIN" ]]; then
  ADB_WRAPPER="$WORKDIR/adb-with-serial.sh"
  "$PYTHON" - "$ADB_WRAPPER" "$ADB_BIN" "$ANDROID_SERIAL_ARG" <<'PY'
from pathlib import Path
import shlex
import sys
wrapper, adb, serial = sys.argv[1:4]
Path(wrapper).write_text(
    "#!/usr/bin/env bash\n"
    "set -euo pipefail\n"
    f"exec {shlex.quote(adb)} -s {shlex.quote(serial)} \"$@\"\n",
    encoding="utf-8",
)
Path(wrapper).chmod(0o755)
PY
  ADB_FOR_VERIFY="$ADB_WRAPPER"
fi

BUILD_SHA="unknown-build"
if git rev-parse --short=12 HEAD >/dev/null 2>&1; then
  BUILD_SHA="$(git rev-parse --short=12 HEAD)"
fi

DEVICE_ID="${ANDROID_SERIAL_ARG:-unknown-device}"

if [[ -s "$INPUT_FAILURES_FILE" ]]; then
  printf 'baseline capture skipped because required inputs were missing\n' >>"$CAPTURE_LOG"
else
  set +e
  "$PYTHON" "$VERIFY" \
    --capture-device \
    --adb "$ADB_FOR_VERIFY" \
    --package "$PKG" \
    --socket "$SOCKET" \
    --timeout "$TIMEOUT" \
    --output "$BASELINE_JSON" \
    >"$CAPTURE_LOG" 2>&1
  CAPTURE_RC=$?
  set -e
  printf '%s\n' "$CAPTURE_RC" >"$CAPTURE_STATUS"
fi

write_artifact() {
  local write_stage="$1"
  "$PYTHON" - \
    "$OUT" "$BASELINE_JSON" "$CAPTURE_LOG" "$CAPTURE_STATUS" \
    "$SEQUENCE_VERIFY_LOG" "$SEQUENCE_VERIFY_STATUS" "$INPUT_FAILURES_FILE" \
    "$PKG" "$DEVICE_ID" "$BUILD_SHA" "$VERIFY" "$ADB_BIN" "$ADB_FOR_VERIFY" \
    "$SOCKET" "$TIMEOUT" "$PREVIOUS_ARTIFACT_PRESENT" "$WORKDIR" "$write_stage" <<'PY'
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

(
    out_raw,
    baseline_raw,
    capture_log_raw,
    capture_status_raw,
    sequence_log_raw,
    sequence_status_raw,
    input_failures_raw,
    package,
    device_id,
    build_sha,
    verify_raw,
    adb_raw,
    adb_for_verify_raw,
    socket,
    timeout,
    previous_present_raw,
    workdir_raw,
    write_stage,
) = sys.argv[1:19]

out = Path(out_raw)
baseline_path = Path(baseline_raw)
capture_log_path = Path(capture_log_raw)
capture_status_path = Path(capture_status_raw)
sequence_log_path = Path(sequence_log_raw)
sequence_status_path = Path(sequence_status_raw)
input_failures_path = Path(input_failures_raw)
verify_path = Path(verify_raw)
workdir = Path(workdir_raw)

def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def read_status(path: Path):
    value = read_text(path).strip()
    if not value or value == "not-run":
        return None
    try:
        return int(value)
    except ValueError:
        return value

input_failures = [line for line in read_text(input_failures_path).splitlines() if line.strip()]
capture_rc = read_status(capture_status_path)
sequence_rc = read_status(sequence_status_path)
capture_log = read_text(capture_log_path)
sequence_log = read_text(sequence_log_path)

baseline_snapshot = None
baseline_error = None
if baseline_path.exists():
    try:
        baseline_snapshot = json.loads(baseline_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - artifact should record parse blocker verbatim.
        baseline_error = f"{type(exc).__name__}: {exc}"
else:
    baseline_error = "baseline snapshot was not created"

baseline_complete = (
    capture_rc == 0
    and isinstance(baseline_snapshot, dict)
    and isinstance(baseline_snapshot.get("system_df"), dict)
    and isinstance(baseline_snapshot.get("images"), list)
    and isinstance(baseline_snapshot.get("containers"), list)
)

planned_phase_specs = [
    {
        "name": "after-build",
        "operation": "build a scenario-owned image that reuses an existing lower layer",
        "required_evidence": [
            "image build command/output",
            "fresh storage metrics snapshot after build",
            "proof the base/lower layer is shared rather than double-counted",
        ],
    },
    {
        "name": "after-rebuild",
        "operation": "rebuild the same Dockerfile without changes",
        "required_evidence": [
            "unchanged rebuild command/output",
            "fresh storage metrics snapshot after rebuild",
            "proof UniqueBytes and SharedLayerBytes did not grow from reused lower layers",
        ],
    },
    {
        "name": "after-edit",
        "operation": "edit or copy a file in a scenario-owned container to force copy-up",
        "required_evidence": [
            "container id and edit command/output",
            "fresh /containers/json?all=1&size=1 snapshot",
            "proof SizeRw and ContainerUpperBytes increased after copy-up",
        ],
    },
    {
        "name": "after-prune",
        "operation": "prune only scenario-owned unused images/containers after evidence is collected",
        "required_evidence": [
            "cleanup command/output scoped to scenario-owned objects",
            "fresh storage metrics snapshot after cleanup",
            "proof UniqueBytes did not increase after cleanup",
        ],
    },
]

baseline_phase = {
    "name": "baseline",
    "operation": "read-only capture through verify-storage-metrics.py --capture-device",
    "status": "captured" if baseline_complete else "planned-gap",
    "success": bool(baseline_complete),
    "destructive": False,
    "snapshot": baseline_snapshot if baseline_complete else {},
    "capture": {
        "attempted": not input_failures,
        "return_code": capture_rc,
        "output": str(baseline_path),
        "log": str(capture_log_path),
        "error": baseline_error if not baseline_complete else None,
    },
}

planned_phases = [
    {
        "name": spec["name"],
        "operation": spec["operation"],
        "status": "planned-gap",
        "success": False,
        "destructive": False,
        "snapshot": {},
        "required_evidence": spec["required_evidence"],
        "planned_only": True,
    }
    for spec in planned_phase_specs
]

artifact = {
    "schema": "pdocker.storage.metrics.sequence.v1",
    "status": "planned-gap",
    "success": False,
    "created_at_epoch": int(time.time()),
    "metadata": {
        "device": device_id or "unknown-device",
        "build_sha": build_sha or "unknown-build",
        "package": package or "missing-package",
        "runner": "scripts/android-storage-metrics-sequence.sh",
        "artifact": str(out),
        "workdir": str(workdir),
    },
    "runner": {
        "schema": "pdocker.android.storage-metrics.sequence-runner.v1",
        "write_stage": write_stage,
        "non_destructive": True,
        "removed_previous_artifact": previous_present_raw == "true",
        "stale_artifact_policy": "rm -f output artifact before any capture; never report success from a pre-existing file",
        "requires": {"adb": True, "package": True, "verify_storage_metrics_capture": True},
        "adb": adb_raw,
        "adb_for_verify": adb_for_verify_raw,
        "socket": socket,
        "timeout_seconds": int(timeout) if str(timeout).isdigit() else timeout,
        "verify_storage_metrics": str(verify_path),
    },
    "input_failures": input_failures,
    "capture": {
        "baseline": {
            "attempted": not input_failures,
            "via_existing_verify_capture": verify_path.exists(),
            "command": [
                "python3", str(verify_path), "--capture-device", "--adb", adb_for_verify_raw,
                "--package", package, "--socket", socket, "--timeout", str(timeout), "--output", str(baseline_path),
            ],
            "return_code": capture_rc,
            "log_path": str(capture_log_path),
            "log_excerpt": capture_log[-4000:],
            "complete": baseline_complete,
        },
        "real_capture_complete": False,
    },
    "planned_device_phases": planned_phase_specs,
    "phases": [baseline_phase, *planned_phases],
    "validation": {
        "attempted": sequence_rc is not None,
        "command": ["python3", str(verify_path), "--sequence", str(out)],
        "return_code": sequence_rc,
        "log_path": str(sequence_log_path),
        "log_excerpt": sequence_log[-4000:],
        "invoked_only_after_fresh_artifact_creation": sequence_rc is not None,
        "expected_to_fail_until_planned_phases_have_real_snapshots": True,
    },
    "remaining_gap": [
        "Implement device-side build capture for a scenario-owned image that reuses a lower layer.",
        "Implement unchanged rebuild capture and compare it against after-build.",
        "Implement container edit/copy-up capture with SizeRw and ContainerUpperBytes growth evidence.",
        "Implement scenario-owned cleanup capture and compare it against after-edit.",
        "Only promote success=true after every phase snapshot is fresh and verify-storage-metrics.py --sequence returns 0.",
    ],
}

out.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
}

write_artifact "pre-sequence-validation"

if [[ -f "$VERIFY" && -s "$OUT" ]]; then
  set +e
  "$PYTHON" "$VERIFY" --sequence "$OUT" >"$SEQUENCE_VERIFY_LOG" 2>&1
  SEQUENCE_RC=$?
  set -e
  printf '%s\n' "$SEQUENCE_RC" >"$SEQUENCE_VERIFY_STATUS"
else
  printf 'sequence validation skipped because verify script or fresh artifact is unavailable\n' >"$SEQUENCE_VERIFY_LOG"
fi

write_artifact "post-sequence-validation"

echo "$OUT"
exit 2

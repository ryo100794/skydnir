#!/system/bin/sh
# Device-side scaffold for COW/overlay kill-at-step evidence.
# It is intentionally fail-closed: without the future APK debug helper that
# acknowledges exact mutation checkpoints and returns exact daemon/helper pids,
# this script writes planned-gap evidence and exits non-zero.  It never kills by
# process name and never reports pass from an HTTP/CLI acknowledgement alone.
set -eu

OUT_DIR=/data/local/tmp/pdocker-cow-overlay-kill-at-step
PHASE=preflight
PACKAGE=io.github.ryo100794.pdocker.compat
TOKEN=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --phase) PHASE="$2"; shift 2 ;;
    --package) PACKAGE="$2"; shift 2 ;;
    --token) TOKEN="$2"; shift 2 ;;
    --out-dir) OUT_DIR="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 64 ;;
  esac
done

TOKEN="$(printf '%s' "${TOKEN:-manual-$$}" | sed 's/[^A-Za-z0-9_.-]/-/g' | cut -c1-64)"
[ -n "$TOKEN" ] || TOKEN="manual-$$"
mkdir -p "$OUT_DIR"

json_string() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

write_summary() {
  name="$1"
  status="$2"
  reason="$3"
  cat > "$OUT_DIR/$name" <<EOF
{
  "schema": "pdocker.cow-overlay-kill-at-step-device-side.v1",
  "scenario_id": "cow.overlay.external-daemon-helper-kill-at-step",
  "phase": "$(json_string "$PHASE")",
  "status": "$(json_string "$status")",
  "success": false,
  "token": "$(json_string "$TOKEN")",
  "package": "$(json_string "$PACKAGE")",
  "reason": "$(json_string "$reason")"
}
EOF
}

run_as_app() {
  run-as "$PACKAGE" sh -c "$1"
}

write_context() {
  sock_present=false
  if run_as_app 'test -S files/pdocker/pdockerd.sock' >/dev/null 2>&1; then
    sock_present=true
  fi
  cat > "$OUT_DIR/context.json" <<EOF
{
  "schema": "pdocker.cow-overlay-kill-at-step-context.v1",
  "scenario_id": "cow.overlay.external-daemon-helper-kill-at-step",
  "package": "$(json_string "$PACKAGE")",
  "token": "$(json_string "$TOKEN")",
  "socket_present": $sock_present,
  "required_operations": ["copy-up", "rename", "metadata", "whiteout", "hardlink-ring"],
  "required_process_targets": ["daemon", "helper"],
  "required_checkpoints": [
    "copyup.before_publish_rename",
    "rename.before_destination_publish",
    "metadata.before_chmod_or_sidecar_publish",
    "whiteout.before_marker_publish",
    "hardlink_ring.before_cache_publish",
    "hardlink_ring.helper_rebuild_before_publish"
  ]
}
EOF
}

kill_exact_checkpoint_pid() {
  # The future helper must write an exact numeric pid for the acknowledged
  # checkpoint.  Name-based process termination is forbidden because it can kill the
  # wrong process and cannot prove which operation was interrupted.
  pid="$1"
  case "$pid" in
    ''|*[!0-9]*) echo "refusing non-numeric checkpoint pid" >&2; return 64 ;;
  esac
  kill -TERM "$pid"
}

case "$PHASE" in
  preflight)
    write_context
    write_summary preflight-summary.json planned-gap "preflight recorded; exact checkpoint debug helper is not yet promoted"
    exit 42
    ;;
  execute)
    write_context
    if ! run_as_app 'test -x files/pdocker/tools/pdocker-cow-kill-at-step' >/dev/null 2>&1; then
      write_summary execute-summary.json planned-gap "missing files/pdocker/tools/pdocker-cow-kill-at-step; no daemon/helper kill attempted"
      exit 42
    fi
    write_summary execute-summary.json planned-gap "debug helper exists but host-side promotion parser is not enabled in this scaffold"
    exit 42
    ;;
  cleanup)
    # Token-scoped artifact directory cleanup only; app payload stores are never removed here.
    write_summary cleanup-summary.json planned-gap "cleanup is limited to the device evidence directory"
    exit 0
    ;;
  *)
    echo "unknown phase: $PHASE" >&2
    exit 64
    ;;
esac

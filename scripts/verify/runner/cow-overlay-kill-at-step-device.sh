#!/system/bin/sh
# Device-side scaffold for COW/overlay kill-at-step evidence.
# It is intentionally fail-closed: without the APK debug helper that
# acknowledges exact mutation checkpoints and returns exact daemon/helper pids,
# this script writes planned-gap evidence and exits non-zero.  It never kills by
# process name and never reports pass from an HTTP/CLI acknowledgement alone.
set -eu

OUT_DIR=/data/local/tmp/pdocker-cow-overlay-kill-at-step
PHASE=preflight
PACKAGE=io.github.ryo100794.pdocker.compat
TOKEN=""
HELPER=files/pdocker/tools/pdocker-cow-kill-at-step
REMOTE_CASE_DIR=""

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
REMOTE_CASE_DIR="$OUT_DIR/$TOKEN"
mkdir -p "$REMOTE_CASE_DIR"

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

write_case_contract_jsonl() {
  cat > "$OUT_DIR/cases.jsonl" <<'EOF'
{"Id":"copy_up.daemon_kill_before_publish","Operation":"copy-up","ProcessTarget":"daemon","Step":"copyup.before_publish_rename"}
{"Id":"rename.daemon_kill_before_destination_publish","Operation":"rename","ProcessTarget":"daemon","Step":"rename.before_destination_publish"}
{"Id":"metadata.daemon_kill_before_metadata_publish","Operation":"metadata","ProcessTarget":"daemon","Step":"metadata.before_chmod_or_sidecar_publish"}
{"Id":"whiteout.daemon_kill_before_marker_publish","Operation":"whiteout","ProcessTarget":"daemon","Step":"whiteout.before_marker_publish"}
{"Id":"hardlink_ring.daemon_kill_during_cache_publish","Operation":"hardlink-ring","ProcessTarget":"daemon","Step":"hardlink_ring.before_cache_publish"}
{"Id":"hardlink_ring.helper_kill_during_cache_rebuild","Operation":"hardlink-ring","ProcessTarget":"helper","Step":"hardlink_ring.helper_rebuild_before_publish"}
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
  "external_helper": "$(json_string "$HELPER")",
  "pass_forbids_name_based_kill": true,
  "pass_requires_exact_checkpoint_pid": true,
  "token_scoped_case_dir": "$(json_string "$REMOTE_CASE_DIR")",
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
  write_case_contract_jsonl
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

wait_for_checkpoint_ack() {
  ack="$1"
  deadline="$2"
  while [ "$deadline" -gt 0 ]; do
    [ -s "$ack" ] && return 0
    sleep 1
    deadline=$((deadline - 1))
  done
  echo "checkpoint acknowledgement not written: $ack" >&2
  return 70
}

read_checkpoint_pid() {
  ack="$1"
  # The helper contract is deliberately simple for shell portability: the first
  # line of the ack file must be the exact pid to kill.  Rich JSON may appear in
  # sibling evidence files, but this shell runner never derives a pid from a
  # process name or ps output.
  head -n 1 "$ack" | tr -d '\r\n '
}

run_one_case() {
  case_id="$1"
  operation="$2"
  target="$3"
  checkpoint="$4"
  case_dir="$REMOTE_CASE_DIR/$case_id"
  ack="$case_dir/checkpoint.pid"
  mkdir -p "$case_dir"
  rm -f "$ack"
  cat > "$case_dir/request.json" <<EOF
{
  "schema": "pdocker.cow-overlay-kill-at-step-request.v1",
  "scenario_id": "cow.overlay.external-daemon-helper-kill-at-step",
  "token": "$(json_string "$TOKEN")",
  "case_id": "$(json_string "$case_id")",
  "operation": "$(json_string "$operation")",
  "process_target": "$(json_string "$target")",
  "checkpoint": "$(json_string "$checkpoint")",
  "ack_file": "$(json_string "$ack")"
}
EOF
  # Contract for the app helper:
  #   prepare must start/reach the checkpoint and write checkpoint.pid with the
  #   exact daemon/helper pid. verify must perform restart/reconciliation and
  #   write post-restart evidence. Any non-zero status is non-promoting.
  if ! run_as_app "$HELPER prepare --token '$TOKEN' --case-id '$case_id' --operation '$operation' --target '$target' --checkpoint '$checkpoint' --out-dir '$case_dir'" > "$case_dir/prepare.stdout" 2> "$case_dir/prepare.stderr"; then
    write_summary "execute-summary.json" blocked-device "helper prepare failed before checkpoint for $case_id"
    return 72
  fi
  if ! wait_for_checkpoint_ack "$ack" 30; then
    write_summary "execute-summary.json" blocked-device "helper did not acknowledge checkpoint for $case_id"
    return 70
  fi
  pid="$(read_checkpoint_pid "$ack")"
  if ! kill_exact_checkpoint_pid "$pid"; then
    write_summary "execute-summary.json" blocked-device "refused or failed exact-pid kill for $case_id"
    return 71
  fi
  if ! run_as_app "$HELPER verify --token '$TOKEN' --case-id '$case_id' --operation '$operation' --target '$target' --checkpoint '$checkpoint' --out-dir '$case_dir'" > "$case_dir/verify.stdout" 2> "$case_dir/verify.stderr"; then
    write_summary "execute-summary.json" blocked-device "helper verify failed after exact-pid kill for $case_id"
    return 73
  fi
}

run_all_cases() {
  run_one_case "copy_up.daemon_kill_before_publish" "copy-up" "daemon" "copyup.before_publish_rename"
  run_one_case "rename.daemon_kill_before_destination_publish" "rename" "daemon" "rename.before_destination_publish"
  run_one_case "metadata.daemon_kill_before_metadata_publish" "metadata" "daemon" "metadata.before_chmod_or_sidecar_publish"
  run_one_case "whiteout.daemon_kill_before_marker_publish" "whiteout" "daemon" "whiteout.before_marker_publish"
  run_one_case "hardlink_ring.daemon_kill_during_cache_publish" "hardlink-ring" "daemon" "hardlink_ring.before_cache_publish"
  run_one_case "hardlink_ring.helper_kill_during_cache_rebuild" "hardlink-ring" "helper" "hardlink_ring.helper_rebuild_before_publish"
}

case "$PHASE" in
  preflight)
    write_context
    write_summary preflight-summary.json planned-gap "preflight recorded; exact checkpoint debug helper is not yet promoted"
    exit 42
    ;;
  execute)
    write_context
    if ! run_as_app "test -x $HELPER" >/dev/null 2>&1; then
      write_summary execute-summary.json planned-gap "missing files/pdocker/tools/pdocker-cow-kill-at-step; no daemon/helper kill attempted"
      exit 42
    fi
    run_all_cases
    write_summary execute-summary.json blocked-device "case evidence collected but host promotion still requires validated adb pull artifact; no device-side shell success is promoting"
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

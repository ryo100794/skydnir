#!/system/bin/sh
# Device-side evidence runner for image pull crash-safety recovery.
# Safe by construction: every mutated path carries the caller-provided token and
# cleanup removes only those scenario-owned paths.
set -eu

OUT_DIR=/data/local/tmp/pdocker-image-pull-crash-safety
PHASE=unset
IMAGE=busybox:latest
PACKAGE=io.github.ryo100794.pdocker.compat
TOKEN=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --phase) PHASE="$2"; shift 2 ;;
    --image) IMAGE="$2"; shift 2 ;;
    --package) PACKAGE="$2"; shift 2 ;;
    --token) TOKEN="$2"; shift 2 ;;
    --out-dir) OUT_DIR="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 64 ;;
  esac
done

TOKEN="$(printf '%s' "${TOKEN:-manual-$$}" | sed 's/[^A-Za-z0-9_.-]/-/g' | cut -c1-64)"
[ -n "$TOKEN" ] || TOKEN="manual-$$"
SCENARIO_TAG="pdocker-crash-safety-probe:$TOKEN"
NEVER_TAG="pdocker-crash-safety-never:$TOKEN"
IMG_BASE="docker.io_library_pdocker-crash-safety-probe_$TOKEN"
NEVER_BASE="docker.io_library_pdocker-crash-safety-never_$TOKEN"
TMP_LAYER="1111111111111111111111111111111111111111111111111111111111111111"
PARTIAL_LAYER="2222222222222222222222222222222222222222222222222222222222222222"
CLASS_PREFIX=io.github.ryo100794.pdocker
ACTION_PREFIX=io.github.ryo100794.pdocker
SOCKET=files/pdocker/pdockerd.sock

mkdir -p "$OUT_DIR"

json_bool() {
  if [ "$1" = "0" ]; then printf false; else printf true; fi
}

run_as_app() {
  run-as "$PACKAGE" sh -c "$1"
}

write_context() {
  cat > "$OUT_DIR/context.json" <<EOF
{
  "scenario_id": "image.pull.interrupted-kill-restart",
  "package": "$PACKAGE",
  "image": "$IMAGE",
  "token": "$TOKEN",
  "scenario_tag": "$SCENARIO_TAG",
  "never_tag": "$NEVER_TAG",
  "image_base": "$IMG_BASE",
  "never_base": "$NEVER_BASE",
  "tmp_layer": "$TMP_LAYER",
  "partial_layer": "$PARTIAL_LAYER"
}
EOF
}

store_listing() {
  local dest="$1"
  run_as_app "cd files && { echo '# images'; find pdocker/images -maxdepth 1 -type d 2>/dev/null | sort; echo '# layers'; find pdocker/layers -maxdepth 1 -type d 2>/dev/null | sort; }" > "$OUT_DIR/$dest" 2> "$OUT_DIR/$dest.err" || true
}

ps_capture() {
  local dest="$1"
  run_as_app "ps -A 2>/dev/null | grep -E 'pdockerd|pdocker|python' || true" > "$OUT_DIR/$dest" 2> "$OUT_DIR/$dest.err" || true
}

engine_get() {
  local path="$1"
  local dest="$2"
  run_as_app "cd files && { printf 'GET $path HTTP/1.1\r\nHost: pdocker\r\nConnection: close\r\n\r\n'; } | toybox nc -U -W 8 pdocker/pdockerd.sock" > "$OUT_DIR/$dest" 2> "$OUT_DIR/$dest.err" || true
}

http_status() {
  local file="$1"
  if [ ! -s "$OUT_DIR/$file" ]; then
    echo 000
    return
  fi
  head -n 1 "$OUT_DIR/$file" | tr -d '\r' | sed 's/^[^ ]* \([0-9][0-9][0-9]\).*/\1/'
}

wait_socket() {
  i=0
  while [ "$i" -lt 45 ]; do
    if run_as_app "test -S $SOCKET" >/dev/null 2>&1; then
      return 0
    fi
    i=$((i + 1))
    sleep 1
  done
  return 1
}

cleanup_paths() {
  run_as_app "cd files && rm -rf \
    pdocker/images/$IMG_BASE \
    pdocker/images/$IMG_BASE.pull-$TOKEN \
    pdocker/images/$IMG_BASE.old-$TOKEN \
    pdocker/images/$NEVER_BASE \
    pdocker/images/$NEVER_BASE.pull-$TOKEN \
    pdocker/images/$NEVER_BASE.old-$TOKEN \
    pdocker/layers/$TMP_LAYER.tmp-$TOKEN \
    pdocker/layers/$PARTIAL_LAYER" >/dev/null 2>&1 || return 1
  return 0
}

phase_prepare() {
  write_context
  cleanup_paths || true
  run_as_app "cd files && \
    mkdir -p pdocker/images pdocker/layers pdocker/diagnostics/image-pull-crash-safety && \
    mkdir -p pdocker/images/$IMG_BASE.old-$TOKEN/rootfs/bin && \
    printf '{\"architecture\":\"arm64\",\"os\":\"linux\",\"rootfs\":{\"type\":\"layers\",\"diff_ids\":[]},\"config\":{\"Cmd\":[\"/bin/sh\"]}}\n' > pdocker/images/$IMG_BASE.old-$TOKEN/config.json && \
    printf 'docker.io/library/pdocker-crash-safety-probe:$TOKEN' > pdocker/images/$IMG_BASE.old-$TOKEN/image_ref && \
    printf '2026-05-13T00:00:00Z' > pdocker/images/$IMG_BASE.old-$TOKEN/pulled_at && \
    printf '#!/bin/sh\nexit 0\n' > pdocker/images/$IMG_BASE.old-$TOKEN/rootfs/bin/true && chmod 755 pdocker/images/$IMG_BASE.old-$TOKEN/rootfs/bin/true && \
    mkdir -p pdocker/images/$IMG_BASE.pull-$TOKEN/rootfs/partial && printf 'partial replacement stage\n' > pdocker/images/$IMG_BASE.pull-$TOKEN/rootfs/partial/file && \
    mkdir -p pdocker/images/$NEVER_BASE.pull-$TOKEN/rootfs/partial && printf 'never published stage\n' > pdocker/images/$NEVER_BASE.pull-$TOKEN/rootfs/partial/file && \
    mkdir -p pdocker/layers/$TMP_LAYER.tmp-$TOKEN/tree && printf 'tmp layer residue\n' > pdocker/layers/$TMP_LAYER.tmp-$TOKEN/tree/file && \
    mkdir -p pdocker/layers/$PARTIAL_LAYER && printf '{\"diff_id\":\"sha256:$PARTIAL_LAYER\",\"size\":1}\n' > pdocker/layers/$PARTIAL_LAYER/meta.json"
  store_listing store-before-kill.txt
  cat > "$OUT_DIR/prepare-summary.json" <<EOF
{
  "phase": "prepare-residue",
  "success": true,
  "created_paths": [
    "files/pdocker/images/$IMG_BASE.old-$TOKEN",
    "files/pdocker/images/$IMG_BASE.pull-$TOKEN",
    "files/pdocker/images/$NEVER_BASE.pull-$TOKEN",
    "files/pdocker/layers/$TMP_LAYER.tmp-$TOKEN",
    "files/pdocker/layers/$PARTIAL_LAYER"
  ]
}
EOF
}

phase_kill() {
  ps_capture ps-before-kill.txt
  run_as_app "pkill -TERM -f pdockerd 2>/dev/null || true; sleep 1; pkill -KILL -f pdockerd 2>/dev/null || true" >/dev/null 2>&1 || true
  ps_capture ps-after-kill.txt
  cat > "$OUT_DIR/kill-summary.json" <<EOF
{
  "phase": "kill-daemon",
  "success": true,
  "kill_attempted": true,
  "signal_sequence": ["TERM", "KILL"]
}
EOF
}

phase_restart_probe() {
  am start -n "$PACKAGE/$CLASS_PREFIX.MainActivity" -a "$ACTION_PREFIX.action.SMOKE_START" > "$OUT_DIR/am-start.txt" 2> "$OUT_DIR/am-start.err" || true
  daemon_restarted=0
  if wait_socket; then daemon_restarted=1; fi
  # Give startup recovery one extra tick after the socket appears.
  sleep 2
  store_listing store-after-restart.txt
  ps_capture ps-after-restart.txt
  engine_get "/images/$SCENARIO_TAG/json" inspect-restored.raw
  engine_get "/images/$NEVER_TAG/json" inspect-never.raw

  run_as_app "test -d files/pdocker/images/$IMG_BASE" >/dev/null 2>&1; old_rc=$?
  run_as_app "test -d files/pdocker/images/$IMG_BASE.pull-$TOKEN || test -d files/pdocker/images/$NEVER_BASE.pull-$TOKEN" >/dev/null 2>&1; pull_rc=$?
  run_as_app "test -d files/pdocker/layers/$TMP_LAYER.tmp-$TOKEN" >/dev/null 2>&1; tmp_rc=$?
  run_as_app "test -e files/pdocker/layers/$PARTIAL_LAYER" >/dev/null 2>&1; partial_rc=$?
  run_as_app "test -e files/pdocker/images/$NEVER_BASE" >/dev/null 2>&1; never_rc=$?
  restored_status="$(http_status inspect-restored.raw)"
  never_status="$(http_status inspect-never.raw)"

  cat > "$OUT_DIR/restart-summary.json" <<EOF
{
  "phase": "restart-and-probe",
  "success": true,
  "daemon_restarted": $(json_bool "$daemon_restarted"),
  "old_tag_restored": $(json_bool "$old_rc"),
  "pull_stage_pruned": $(json_bool "$pull_rc"),
  "tmp_layer_pruned": $(json_bool "$tmp_rc"),
  "partial_layer_pruned": $(json_bool "$partial_rc"),
  "never_published_tag_rejected": $(json_bool "$never_rc"),
  "restored_tag_inspectable": $( [ "$restored_status" = "200" ] && echo true || echo false ),
  "never_image_inspect_status": "$never_status",
  "restored_image_inspect_status": "$restored_status",
  "live_interrupted_network_pull": false
}
EOF
}

phase_cleanup() {
  before="$(run_as_app "cd files && { find pdocker/images -maxdepth 1 -type d -name '*$TOKEN*'; find pdocker/layers -maxdepth 1 -type d -name '*$TOKEN*'; } 2>/dev/null | wc -l" | tr -d '\r ' || true)"
  cleanup_paths; cleanup_rc=$?
  after="$(run_as_app "cd files && { find pdocker/images -maxdepth 1 -type d -name '*$TOKEN*'; find pdocker/layers -maxdepth 1 -type d -name '*$TOKEN*'; } 2>/dev/null | wc -l" | tr -d '\r ' || true)"
  cat > "$OUT_DIR/cleanup-summary.json" <<EOF
{
  "phase": "cleanup",
  "success": $( [ "$cleanup_rc" = "0" ] && echo true || echo false ),
  "cleanup_removed_only_scenario_owned_paths": $( [ "$cleanup_rc" = "0" ] && echo true || echo false ),
  "scenario_owned_path_count_before": "${before:-unknown}",
  "scenario_owned_path_count_after": "${after:-unknown}"
}
EOF
  return "$cleanup_rc"
}

case "$PHASE" in
  prepare-residue|prepare) phase_prepare ;;
  kill-daemon|kill) phase_kill ;;
  restart-and-probe|restart) phase_restart_probe ;;
  cleanup) phase_cleanup ;;
  all)
    phase_prepare
    phase_kill
    phase_restart_probe
    phase_cleanup
    ;;
  *) echo "unknown phase: $PHASE" >&2; exit 64 ;;
esac

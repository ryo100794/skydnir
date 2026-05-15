#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FLAVOR="${PDOCKER_ANDROID_FLAVOR:-compat}"
case "$FLAVOR" in
  compat)
    DEFAULT_PKG="io.github.ryo100794.pdocker.compat"
    DEFAULT_APK="$ROOT/app/build/outputs/apk/compat/debug/app-compat-debug.apk"
    ;;
  modern)
    DEFAULT_PKG="io.github.ryo100794.pdocker"
    DEFAULT_APK="$ROOT/app/build/outputs/apk/modern/debug/app-modern-debug.apk"
    ;;
  *)
    echo "PDOCKER_ANDROID_FLAVOR must be 'compat' or 'modern' (got '$FLAVOR')" >&2
    exit 2
    ;;
esac
PKG="${PDOCKER_PACKAGE:-$DEFAULT_PKG}"
APK="${PDOCKER_APK:-$DEFAULT_APK}"
CLASS_PREFIX="io.github.ryo100794.pdocker"
ACTION_PREFIX="io.github.ryo100794.pdocker"
PROJECT="device-smoke"
MODE="full"
GPU_BENCH=0
SERVICE_TRUTH_TARGET=""
RUNTIME_TEARDOWN_TARGET=""
SMOKE_ARTIFACT_DIR_RESOLVED=""

usage() {
  cat <<EOF
Usage: $0 [--quick] [--gpu-bench] [--no-install]
       $0 --service-truth <default-workspace|llama>
       $0 --runtime-teardown <default-workspace|llama>

Runs a repeatable pdocker Android device smoke through adb + run-as.

Environment:
  ADB               adb executable (default: adb)
  PDOCKER_PACKAGE   Android package (default: $PKG)
  PDOCKER_APK       debug APK path (default: $APK)
  PDOCKER_STAGE_TEST_CLI
                    stage repository Docker CLI/Compose into app files for
                    compatibility tests (default: 1)
  PDOCKER_KEEP_TEST_CLI
                    keep staged test CLI/Compose after the smoke run
                    (default: 0)
  PDOCKER_SMOKE_FORCE_STOP
                    force-stop the app before the smoke run. This kills any
                    running pdocker containers, so it is opt-in (default: 0)
  PDOCKER_SMOKE_ARTIFACT_DIR
                    host directory for collected device diagnostics (default:
                    tmp/device-smoke-artifacts/<timestamp>)
  PDOCKER_UI_IT_SELFTEST_CONTAINER
                    optional existing container ID/name to drive through
                    ACTION_PREFIX.action.SMOKE_UI_IT_SELFTEST before the full
                    smoke project is created. When empty/no container is
                    available, a planned-skip artifact is written with
                    Success=false.
  PDOCKER_UI_IT_SELFTEST_REQUIRE_CONTAINER
                    when set to 1, an empty UI exec-it self-test container is
                    a hard-gate failure. Planned-skip artifacts remain useful
                    evidence but are never accepted as a passing required gate.

Modes:
  --quick       only install/start pdockerd and run docker version
  --gpu-bench   also run debug-only android-gpu-bench and verify artifacts
  --no-install  skip adb install; useful when the same debug APK is present
  --service-truth TARGET
                collect planned-gap listener/container-ID truth evidence and
                write files/pdocker/diagnostics/service-truth-latest.json.
  --runtime-teardown TARGET
                planned acceptance entrypoint for future stop/process-tree
                proof. Currently exits nonzero with a structured artifact.
EOF
}

ADB="${ADB:-adb}"
INSTALL=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --quick) MODE="quick" ;;
    --gpu-bench) GPU_BENCH=1 ;;
    --no-install) INSTALL=0 ;;
    --service-truth)
      [[ $# -ge 2 ]] || { echo "--service-truth requires a target" >&2; exit 2; }
      SERVICE_TRUTH_TARGET="$2"
      shift
      ;;
    --runtime-teardown)
      [[ $# -ge 2 ]] || { echo "--runtime-teardown requires a target" >&2; exit 2; }
      RUNTIME_TEARDOWN_TARGET="$2"
      shift
      ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

service_truth_acceptance_entrypoint() {
  local target="$1"
  local remote_script="/data/local/tmp/pdocker-service-truth-smoke.sh"
  local local_script
  local_script="$(mktemp)"
  cat > "$local_script" <<'REMOTE_SERVICE_TRUTH'
#!/system/bin/sh
set +e
TARGET="${1:-default-workspace}"
cd files || exit 1
mkdir -p pdocker/tmp
export TMPDIR="$PWD/pdocker/tmp"
export PATH="$PWD/pdocker-runtime/docker-bin:$PATH"
export DOCKER_CONFIG="$PWD/pdocker-runtime/docker-bin"
export DOCKER_HOST="unix://$PWD/pdocker/pdockerd.sock"
export DOCKER_BUILDKIT=0 COMPOSE_DOCKER_CLI_BUILD=0 BUILDKIT_PROGRESS=plain COMPOSE_PROGRESS=plain COMPOSE_MENU=false

DIAG="pdocker/diagnostics/service-truth"
LATEST="pdocker/diagnostics/service-truth-latest.json"
rm -rf "$DIAG"
mkdir -p "$DIAG"
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date)"
SOCKET="pdocker/pdockerd.sock"

json_string() {
  printf '%s' "$1" | awk 'BEGIN{printf "\""}{gsub(/\\/,"\\\\"); gsub(/\"/,"\\\""); gsub(/\t/,"\\t"); if (NR>1) printf "\\n"; printf "%s",$0}END{printf "\""}'
}

record_cmd() {
  label="$1"
  shift
  out="$DIAG/$label.out"
  err="$DIAG/$label.err"
  "$@" >"$out" 2>"$err"
  rc=$?
  printf '%s' "$rc" >"$DIAG/$label.rc"
  return 0
}

http_get() {
  label="$1"
  path="$2"
  { printf 'GET %s HTTP/1.1\r\nHost: docker\r\nConnection: close\r\n\r\n' "$path"; } \
    | nc -U -W 5 "$SOCKET" >"$DIAG/$label.http" 2>"$DIAG/$label.err"
  rc=$?
  printf '%s' "$rc" >"$DIAG/$label.rc"
  sed -n '1s/\r$//p' "$DIAG/$label.http" >"$DIAG/$label.status" 2>/dev/null
}

snapshot_ps() {
  label="$1"
  (ps -A -o PID,PPID,USER,NAME,ARGS 2>/dev/null || ps -A -ef 2>/dev/null || ps 2>/dev/null) >"$DIAG/$label.txt" 2>"$DIAG/$label.err"
}

snapshot_state_json() {
  label="$1"
  out="$DIAG/$label.txt"
  : >"$out"
  find pdocker -name state.json -type f 2>/dev/null | sort | while IFS= read -r f; do
    printf '\n--- %s ---\n' "$f" >>"$out"
    cat "$f" >>"$out" 2>/dev/null
    printf '\n' >>"$out"
  done
}

snapshot_ui_inputs() {
  cp pdocker/jobs.json "$DIAG/ui-jobs.json" 2>"$DIAG/ui-jobs.err" || : >"$DIAG/ui-jobs.missing"
  cp pdocker/diagnostics/ui-rendered-service-truth-latest.json "$DIAG/ui-rendered-service-truth-latest.json" 2>"$DIAG/ui-rendered-service-truth-latest.err" || : >"$DIAG/ui-rendered-service-truth-latest.missing"
  : >"$DIAG/ui-project-files.txt"
  find pdocker/projects -maxdepth 4 \( -name compose.yaml -o -name docker-compose.yml -o -name .smoke-cid -o -name 'pdocker-*.json' \) -type f 2>/dev/null | sort >"$DIAG/ui-project-files.txt"
  : >"$DIAG/ui-project-snippets.txt"
  while IFS= read -r f; do
    printf '\n--- %s ---\n' "$f" >>"$DIAG/ui-project-snippets.txt"
    sed -n '1,120p' "$f" >>"$DIAG/ui-project-snippets.txt" 2>/dev/null
  done <"$DIAG/ui-project-files.txt"
}

collect_project_ports() {
  : >"$DIAG/configured-ports.txt"
  target_lc=$(printf '%s' "$TARGET" | tr '[:upper:]' '[:lower:]')
  case "$target_lc" in
    default-workspace|workspace|vscode)
      printf '18080\n' >"$DIAG/configured-ports.txt"
      return
      ;;
    llama)
      printf '18081\n' >"$DIAG/configured-ports.txt"
      return
      ;;
  esac
  find pdocker/projects -maxdepth 4 \( -name compose.yaml -o -name docker-compose.yml \) -type f 2>/dev/null | sort | while IFS= read -r f; do
    sed -n 's/.*\([0-9][0-9][0-9][0-9][0-9]*\):[0-9][0-9]*/\1/p' "$f" 2>/dev/null
  done | tr -d '"' | sort -u >>"$DIAG/configured-ports.txt"
  printf '18080\n18081\n' >>"$DIAG/configured-ports.txt"
  sort -u "$DIAG/configured-ports.txt" -o "$DIAG/configured-ports.txt" 2>/dev/null || true
}

json_bool() {
  case "$1" in
    true|1|yes) printf 'true' ;;
    *) printf 'false' ;;
  esac
}

json_number_or_null() {
  case "$1" in
    ''|*[!0-9]*) printf 'null' ;;
    *) printf '%s' "$1" ;;
  esac
}

is_engine_container_id() {
  printf '%s' "$1" | grep -Eq '^[0-9a-fA-F]{64}$'
}

json_word_array() {
  first=1
  printf '['
  for item in $1; do
    [ -n "$item" ] || continue
    [ "$first" = 1 ] || printf ', '
    first=0
    json_string "$item"
  done
  printf ']'
}

snapshot_listener_probe() {
  : >"$DIAG/listener-probe.txt"
  : >"$DIAG/listener-probe.json"
  (cat /proc/net/tcp 2>/dev/null; cat /proc/net/tcp6 2>/dev/null) >"$DIAG/proc-net-tcp.txt" 2>"$DIAG/proc-net-tcp.err"
  (ss -ltnp 2>/dev/null || netstat -ltnp 2>/dev/null || true) >"$DIAG/listeners-tool.txt" 2>"$DIAG/listeners-tool.err"
  printf '{"ProcNetTcpArtifact":"files/%s/proc-net-tcp.txt","Ports":[\n' "$DIAG" >"$DIAG/listener-probe.json"
  first=1
  while IFS= read -r port; do
    [ -n "$port" ] || continue
    hex=$(printf '%04X' "$port" 2>/dev/null)
    proc_matches=$(grep -i ":$hex" "$DIAG/proc-net-tcp.txt" 2>/dev/null | wc -l | tr -d ' ')
    printf 'port=%s hex=%s proc_net_tcp_matches=%s\n' "$port" "$hex" "$proc_matches" >>"$DIAG/listener-probe.txt"
    (echo | nc -w 2 127.0.0.1 "$port") >"$DIAG/listener-$port.out" 2>"$DIAG/listener-$port.err"
    nc_rc="$?"
    printf '%s' "$nc_rc" >"$DIAG/listener-$port.rc"
    [ "$first" = 1 ] || printf ',\n' >>"$DIAG/listener-probe.json"
    first=0
    printf '  {"Port":%s,"Hex":%s,"ProcNetTcpMatches":%s,"TcpConnectExitCode":%s,"ProcNetTcpProven":%s,"Artifacts":[%s,%s,%s]}' \
      "$(json_number_or_null "$port")" "$(json_string "$hex")" "$(json_number_or_null "$proc_matches")" "$(json_number_or_null "$nc_rc")" \
      "$(json_bool "$( [ "${proc_matches:-0}" != 0 ] && echo true || echo false )")" \
      "$(json_string "files/$DIAG/proc-net-tcp.txt")" "$(json_string "files/$DIAG/listener-$port.out")" "$(json_string "files/$DIAG/listener-$port.err")" >>"$DIAG/listener-probe.json"
  done <"$DIAG/configured-ports.txt"
  printf '\n]}\n' >>"$DIAG/listener-probe.json"
}

collect_engine_candidates() {
  : >"$DIAG/engine-candidates.tsv"
  docker ps --no-trunc --format '{{.ID}}	{{.Names}}	{{.Labels}}	{{.Ports}}	{{.Status}}' >"$DIAG/engine-candidates.tsv" 2>"$DIAG/engine-candidates.err"
  printf '%s' "$?" >"$DIAG/engine-candidates.rc"
  : >"$DIAG/engine-candidate-selected.txt"
  target_lc=$(printf '%s' "$TARGET" | tr '[:upper:]' '[:lower:]')
  best_id= best_score=-1 best_reasons= best_names= best_labels= best_ports= best_status=
  while IFS='	' read -r cid names labels ports status; do
    [ -n "$cid" ] || continue
    hay=$(printf '%s %s' "$names" "$labels" | tr '[:upper:]' '[:lower:]')
    score=0
    reasons=
    case "$hay" in *pdocker*) score=$((score + 10)); reasons="${reasons}pdocker-label-or-name," ;; esac
    case "$hay" in *"$target_lc"*) score=$((score + 8)); reasons="${reasons}target-match," ;; esac
    case "$target_lc" in
      default-workspace|workspace|vscode)
        case "$names" in *pdocker-dev*) score=$((score + 20)); reasons="${reasons}pdocker-dev-name," ;; esac
        case "$hay" in *workspace*|*code-server*|*vscode*) score=$((score + 5)); reasons="${reasons}workspace-service-hint," ;; esac
        case "$ports" in *18080*) score=$((score + 6)); reasons="${reasons}vscode-port-18080," ;; esac
        ;;
      llama) case "$hay" in *llama*) score=$((score + 5)); reasons="${reasons}llama-service-hint," ;; esac ;;
    esac
    case "$labels" in *com.docker.compose.service*|*pdocker.service*|*pdocker.project*) score=$((score + 4)); reasons="${reasons}service-label," ;; esac
    case "$ports" in *18080*|*18081*) score=$((score + 3)); reasons="${reasons}known-service-port," ;; esac
    if [ "$score" -gt "$best_score" ]; then
      best_score="$score"; best_id="$cid"; best_reasons="$reasons"; best_names="$names"; best_labels="$labels"; best_ports="$ports"; best_status="$status"
    fi
  done <"$DIAG/engine-candidates.tsv"
  [ "${best_score:-0}" -gt 0 ] && printf '%s' "$best_id" >"$DIAG/engine-candidate-selected.txt"
  write_engine_candidates_json "$best_id" "$best_score" "$best_reasons" "$best_names" "$best_labels" "$best_ports" "$best_status"
}

write_engine_candidates_json() {
  selected_id="$1"; selected_score="$2"; selected_reasons="$3"; selected_names="$4"; selected_labels="$5"; selected_ports="$6"; selected_status="$7"
  printf '{\n  "SelectionRule": "Score running Engine containers by pdocker/project/service labels, names, target hints, and known listener ports; names alone are hints, not proof.",\n  "SelectedContainerId": %s,\n  "SelectedScore": %s,\n  "SelectedNames": %s,\n  "SelectedLabels": %s,\n  "SelectedPorts": %s,\n  "SelectedStatus": %s,\n  "SelectedReasons": %s,\n  "Candidates": [\n' \
    "$( [ -n "$selected_id" ] && json_string "$selected_id" || printf null )" \
    "$(json_number_or_null "$selected_score")" \
    "$(json_string "$selected_names")" "$(json_string "$selected_labels")" "$(json_string "$selected_ports")" "$(json_string "$selected_status")" "$(json_string "$selected_reasons")" >"$DIAG/engine-candidates.json"
  first=1
  target_lc=$(printf '%s' "$TARGET" | tr '[:upper:]' '[:lower:]')
  while IFS='	' read -r cid names labels ports status; do
    [ -n "$cid" ] || continue
    hay=$(printf '%s %s' "$names" "$labels" | tr '[:upper:]' '[:lower:]')
    score=0; reasons=
    case "$hay" in *pdocker*) score=$((score + 10)); reasons="${reasons}pdocker-label-or-name," ;; esac
    case "$hay" in *"$target_lc"*) score=$((score + 8)); reasons="${reasons}target-match," ;; esac
    case "$target_lc" in default-workspace|workspace|vscode) case "$names" in *pdocker-dev*) score=$((score + 20)); reasons="${reasons}pdocker-dev-name," ;; esac; case "$hay" in *workspace*|*code-server*|*vscode*) score=$((score + 5)); reasons="${reasons}workspace-service-hint," ;; esac; case "$ports" in *18080*) score=$((score + 6)); reasons="${reasons}vscode-port-18080," ;; esac ;; llama) case "$hay" in *llama*) score=$((score + 5)); reasons="${reasons}llama-service-hint," ;; esac ;; esac
    case "$labels" in *com.docker.compose.service*|*pdocker.service*|*pdocker.project*) score=$((score + 4)); reasons="${reasons}service-label," ;; esac
    case "$ports" in *18080*|*18081*) score=$((score + 3)); reasons="${reasons}known-service-port," ;; esac
    [ "$first" = 1 ] || printf ',\n' >>"$DIAG/engine-candidates.json"
    first=0
    printf '    {"Id":%s,"Names":%s,"Labels":%s,"Ports":%s,"Status":%s,"Score":%s,"Reasons":%s,"Selected":%s}' \
      "$(json_string "$cid")" "$(json_string "$names")" "$(json_string "$labels")" "$(json_string "$ports")" "$(json_string "$status")" \
      "$(json_number_or_null "$score")" "$(json_string "$reasons")" "$(json_bool "$( [ "$cid" = "$selected_id" ] && echo true || echo false )")" >>"$DIAG/engine-candidates.json"
  done <"$DIAG/engine-candidates.tsv"
  printf '\n  ]\n}\n' >>"$DIAG/engine-candidates.json"
}

extract_state_ids_and_compare() {
  selected_id="$1"
  : >"$DIAG/state-container-ids.tsv"
  find pdocker -name state.json -type f 2>/dev/null | sort | while IFS= read -r f; do
    sed -n 's/.*"id"[[:space:]]*:[[:space:]]*"\([0-9a-fA-F][0-9a-fA-F]*\)".*/\1/p; s/.*"containerId"[[:space:]]*:[[:space:]]*"\([0-9a-fA-F][0-9a-fA-F]*\)".*/\1/p; s/.*"container_id"[[:space:]]*:[[:space:]]*"\([0-9a-fA-F][0-9a-fA-F]*\)".*/\1/p' "$f" 2>/dev/null | while IFS= read -r sid; do
      [ -n "$sid" ] && printf '%s	%s\n' "$f" "$sid" >>"$DIAG/state-container-ids.tsv"
    done
  done
  match=false
  [ -n "$selected_id" ] && awk -F '\t' -v id="$selected_id" '$2 == id { found=1 } END{ exit found ? 0 : 1 }' "$DIAG/state-container-ids.tsv" 2>/dev/null && match=true
  printf '{\n  "SelectedEngineContainerId": %s,\n  "AnyStateIdMatchesSelected": %s,\n  "Matches": [\n' "$( [ -n "$selected_id" ] && json_string "$selected_id" || printf null )" "$(json_bool "$match")" >"$DIAG/state-id-comparison.json"
  first=1
  while IFS='	' read -r path sid; do
    [ -n "$sid" ] || continue
    row_match=false
    if [ -n "$selected_id" ] && [ "$sid" = "$selected_id" ]; then row_match=true; fi
    [ "$first" = 1 ] || printf ',\n' >>"$DIAG/state-id-comparison.json"
    first=0
    printf '    {"Path":%s,"StateContainerId":%s,"MatchesSelected":%s}' "$(json_string "$path")" "$(json_string "$sid")" "$(json_bool "$row_match")" >>"$DIAG/state-id-comparison.json"
  done <"$DIAG/state-container-ids.tsv"
  printf '\n  ]\n}\n' >>"$DIAG/state-id-comparison.json"
}

source_container_id_json() {
  cid="$1"
  [ -n "$cid" ] && json_string "$cid" || printf null
}

selected_in_docker_ps() {
  selected_id="$1"
  [ -n "$selected_id" ] || return 1
  awk -F '\t' -v id="$selected_id" '$1 == id { found=1 } END{ exit found ? 0 : 1 }' "$DIAG/engine-candidates.tsv" 2>/dev/null
}

selected_in_engine_api() {
  selected_id="$1"
  [ -n "$selected_id" ] || return 1
  grep -Fq "\"Id\":\"$selected_id\"" "$DIAG/engine-containers-json.http" 2>/dev/null
}

inspect_pid_from_file() {
  grep -ao '"Pid"[[:space:]]*:[[:space:]]*[0-9][0-9]*' "$1" 2>/dev/null \
    | head -n 1 \
    | sed 's/.*://; s/[^0-9]//g'
}

inspect_running_from_file() {
  grep -Eq '"Running"[[:space:]]*:[[:space:]]*true' "$1" 2>/dev/null
}

process_has_pid() {
  pid="$1"
  [ -n "$pid" ] && [ "$pid" != 0 ] || return 1
  grep -Eq "(^|[[:space:]])$pid([[:space:]]|$)" "$DIAG/process-table.txt" 2>/dev/null
}

collect_listener_owner_evidence() {
  selected_id="$1"
  selected_pid="$2"
  : >"$DIAG/listener-owner-map.tsv"
  : >"$DIAG/listener-owner-map.json"
  printf '{\n  "SelectedEngineContainerId": %s,\n  "SelectedInspectPid": %s,\n  "Ports": [\n' \
    "$(source_container_id_json "$selected_id")" "$(json_number_or_null "$selected_pid")" >"$DIAG/listener-owner-map.json"
  first=1
  while IFS= read -r port; do
    [ -n "$port" ] || continue
    hex=$(printf '%04X' "$port" 2>/dev/null)
    # /proc/net/tcp columns include local_address in column 2 and inode in column 10.
    inodes=$(awk -v h=":$hex" 'tolower($2) ~ tolower(h) { print $10 }' "$DIAG/proc-net-tcp.txt" 2>/dev/null | sort -u | tr '\n' ' ' | sed 's/[[:space:]]*$//')
    owners=""
    for inode in $inodes; do
      [ -n "$inode" ] || continue
      for fd in /proc/[0-9]*/fd/*; do
        link=$(readlink "$fd" 2>/dev/null) || continue
        [ "$link" = "socket:[$inode]" ] || continue
        pid=$(printf '%s' "$fd" | sed 's#^/proc/\([0-9][0-9]*\)/fd/.*#\1#')
        owners="$owners $pid"
        printf '%s\t%s\t%s\n' "$port" "$inode" "$pid" >>"$DIAG/listener-owner-map.tsv"
      done
    done
    owners=$(printf '%s' "$owners" | tr ' ' '\n' | sed '/^$/d' | sort -u | tr '\n' ' ' | sed 's/[[:space:]]*$//')
    selected_pid_owns=false
    for owner in $owners; do [ "$owner" = "$selected_pid" ] && selected_pid_owns=true; done
    [ "$first" = 1 ] || printf ',\n' >>"$DIAG/listener-owner-map.json"
    first=0
    printf '    {"Port":%s,"Hex":%s,"SocketInodes":%s,"OwnerPids":%s,"SelectedPidOwnsListener":%s,"Artifacts":[%s,%s,%s]}' \
      "$(json_number_or_null "$port")" "$(json_string "$hex")" "$(json_string "$inodes")" "$(json_string "$owners")" "$(json_bool "$selected_pid_owns")" \
      "$(json_string "files/$DIAG/proc-net-tcp.txt")" "$(json_string "files/$DIAG/listener-owner-map.tsv")" "$(json_string "files/$DIAG/process-table.txt")" >>"$DIAG/listener-owner-map.json"
  done <"$DIAG/configured-ports.txt"
  printf '\n  ]\n}\n' >>"$DIAG/listener-owner-map.json"
}

write_same_id_source_summary() {
  selected_id="$1"
  ui_cid="$2"
  ui_state="$3"
  state_match="$4"
  selected_pid="$5"
  docker_ps_present=false; selected_in_docker_ps "$selected_id" && docker_ps_present=true
  engine_api_present=false; selected_in_engine_api "$selected_id" && inspect_running_from_file "$DIAG/inspect-selected.http" && engine_api_present=true
  process_pid_present=false; process_has_pid "$selected_pid" && process_pid_present=true
  listener_pid_present=false; [ -n "$selected_pid" ] && awk -F '\t' -v pid="$selected_pid" '$3 == pid { found=1 } END{ exit found ? 0 : 1 }' "$DIAG/listener-owner-map.tsv" 2>/dev/null && listener_pid_present=true
  listener_exact_engine_id=false; [ "$listener_pid_present" = true ] && is_engine_container_id "$selected_id" && listener_exact_engine_id=true
  logs_present=false; [ -n "$selected_id" ] && [ -s "$DIAG/logs-selected.out" ] && logs_present=true
  logs_marker_present=false
  [ "$logs_present" = true ] && grep -F "pdocker-service-truth-marker " "$DIAG/logs-selected.out" 2>/dev/null | grep -F "$selected_id" >/dev/null 2>&1 && logs_marker_present=true
  same_id=false
  mismatched=""
  missing=""
  [ -n "$ui_cid" ] && [ "$ui_state" = current ] && [ "$UI_SOURCE_CURRENT" = true ] && [ -n "$UI_CARD_CURRENT_REASON" ] || missing="$missing UICard"
  [ -n "$selected_id" ] || missing="$missing DockerPs EngineApiContainersJson"
  [ "$docker_ps_present" = true ] || missing="$missing DockerPs"
  [ "$engine_api_present" = true ] || missing="$missing EngineApiContainersJson"
  [ "$state_match" = true ] || missing="$missing PersistedStateJson"
  [ "$process_pid_present" = true ] || missing="$missing ProcessTable"
  [ "$listener_exact_engine_id" = true ] || missing="$missing ListenerProbe"
  [ "$logs_marker_present" = true ] || missing="$missing ContainerLogs"
  if [ -n "$selected_id" ] && [ -n "$ui_cid" ] && [ "$ui_cid" != "$selected_id" ]; then mismatched="$mismatched UICard"; fi
  [ -n "$selected_id" ] && is_engine_container_id "$selected_id" && [ "$ui_cid" = "$selected_id" ] && [ "$ui_state" = current ] && [ "$UI_SOURCE_CURRENT" = true ] && [ -n "$UI_CARD_CURRENT_REASON" ] && [ "$docker_ps_present" = true ] && [ "$engine_api_present" = true ] && [ "$state_match" = true ] && [ "$process_pid_present" = true ] && [ "$listener_exact_engine_id" = true ] && [ "$logs_marker_present" = true ] && same_id=true
  cat >"$DIAG/same-id-source-summary.json" <<JSON
{
  "SelectedEngineContainerId": $(source_container_id_json "$selected_id"),
  "SameEngineContainerIdIfPromoted": $(json_bool "$same_id"),
  "Note": "Device-pass is allowed only when all seven sources are current/proven and name this exact 64-hex Engine container ID, including docker ps/API running-state, persisted state, selected process PID, listener owner PID, UI card, and current log marker; otherwise the top-level result remains planned-gap/Success false.",
  "SourceIds": {
    "UICard": $(source_container_id_json "$ui_cid"),
    "DockerPs": $( [ "$docker_ps_present" = true ] && source_container_id_json "$selected_id" || printf null ),
    "EngineApiContainersJson": $( [ "$engine_api_present" = true ] && source_container_id_json "$selected_id" || printf null ),
    "PersistedStateJson": $( [ "$state_match" = true ] && source_container_id_json "$selected_id" || printf null ),
    "ProcessTable": $( [ "$process_pid_present" = true ] && source_container_id_json "$selected_id" || printf null ),
    "ListenerProbe": $( [ "$listener_exact_engine_id" = true ] && source_container_id_json "$selected_id" || printf null ),
    "ContainerLogs": $( [ "$logs_marker_present" = true ] && source_container_id_json "$selected_id" || printf null )
  },
  "MissingSourcesText": $(json_string "$missing"),
  "MismatchedSourcesText": $(json_string "$mismatched"),
  "Artifacts": [
    "files/$DIAG/ui-rendered-service-truth-latest.json",
    "files/$DIAG/engine-candidates.json",
    "files/$DIAG/engine-ps.out",
    "files/$DIAG/engine-containers-json.http",
    "files/$DIAG/state-id-comparison.json",
    "files/$DIAG/process-table.txt",
    "files/$DIAG/listener-owner-map.json",
    "files/$DIAG/logs-selected.out"
  ]
}
JSON
}

http_get engine-containers-json '/containers/json?all=1'
record_cmd engine-ps docker ps -a --no-trunc
record_cmd engine-ps-running docker ps -q --no-trunc
snapshot_ui_inputs
collect_project_ports
snapshot_ps process-table
snapshot_state_json persisted-state-json
snapshot_listener_probe
collect_engine_candidates
SELECTED_ENGINE_CID="$(cat "$DIAG/engine-candidate-selected.txt" 2>/dev/null)"
SELECTED_ID_EXACT=false
is_engine_container_id "$SELECTED_ENGINE_CID" && SELECTED_ID_EXACT=true
extract_state_ids_and_compare "$SELECTED_ENGINE_CID"
SELECTED_SAFE="$(printf '%s' "$SELECTED_ENGINE_CID" | sed 's/[^0-9A-Za-z_.-]/_/g')"
if [ -n "$SELECTED_ENGINE_CID" ]; then
  http_get "inspect-selected" "/containers/$SELECTED_ENGINE_CID/json"
  record_cmd "docker-inspect-selected" docker inspect "$SELECTED_ENGINE_CID"
else
  : >"$DIAG/inspect-selected.http"; printf '1' >"$DIAG/inspect-selected.rc"
  : >"$DIAG/docker-inspect-selected.out"; printf '1' >"$DIAG/docker-inspect-selected.rc"
fi
SELECTED_INSPECT_PID="$(inspect_pid_from_file "$DIAG/inspect-selected.http")"
collect_listener_owner_evidence "$SELECTED_ENGINE_CID" "$SELECTED_INSPECT_PID"

: >"$DIAG/container-ids.txt"
cat "$DIAG/engine-ps-running.out" 2>/dev/null | while IFS= read -r cid; do
  [ -n "$cid" ] || continue
  printf '%s\n' "$cid" >>"$DIAG/container-ids.txt"
  safe=$(printf '%s' "$cid" | sed 's/[^0-9A-Za-z_.-]/_/g')
  http_get "inspect-$safe" "/containers/$cid/json"
  record_cmd "logs-$safe" docker logs --tail=200 "$cid"
done
if [ -n "$SELECTED_ENGINE_CID" ]; then
  record_cmd "logs-selected" docker logs --tail=200 "$SELECTED_ENGINE_CID"
else
  : >"$DIAG/logs-selected.out"; printf '1' >"$DIAG/logs-selected.rc"
fi

ENGINE_PS_RC="$(cat "$DIAG/engine-ps.rc" 2>/dev/null)"
ENGINE_HTTP_RC="$(cat "$DIAG/engine-containers-json.rc" 2>/dev/null)"
ENGINE_STATUS="$(cat "$DIAG/engine-containers-json.status" 2>/dev/null)"
CID_COUNT="$(wc -l <"$DIAG/container-ids.txt" 2>/dev/null | tr -d ' ')"
PORTS="$(tr '\n' ' ' <"$DIAG/configured-ports.txt" 2>/dev/null | sed 's/[[:space:]]*$//')"
STATE_MATCH="false"
grep -q '"AnyStateIdMatchesSelected": true' "$DIAG/state-id-comparison.json" 2>/dev/null && STATE_MATCH="true"
LISTENER_PROC_MATCH_PORTS="$(sed -n 's/^port=\([0-9][0-9]*\).*proc_net_tcp_matches=\([1-9][0-9]*\).*/\1/p' "$DIAG/listener-probe.txt" 2>/dev/null | tr '\n' ' ' | sed 's/[[:space:]]*$//')"
UI_RENDERED_EXPORT="files/$DIAG/ui-rendered-service-truth-latest.json"
UI_CARD_CID="$(sed -n 's/.*"EngineContainerId"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$DIAG/ui-rendered-service-truth-latest.json" 2>/dev/null | head -1)"
UI_CARD_SOURCE="$(sed -n 's/.*"ContainerIdSource"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$DIAG/ui-rendered-service-truth-latest.json" 2>/dev/null | head -1)"
UI_CARD_STATE="$(sed -n 's/.*"TruthState"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$DIAG/ui-rendered-service-truth-latest.json" 2>/dev/null | head -1)"
UI_CARD_CURRENT_REASON="$(sed -n 's/.*"CurrentReason"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$DIAG/ui-rendered-service-truth-latest.json" 2>/dev/null | head -1)"
UI_CARD_STALE_REASON="$(sed -n 's/.*"StaleReason"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$DIAG/ui-rendered-service-truth-latest.json" 2>/dev/null | head -1)"
UI_CARD_UNKNOWN_REASON="$(sed -n 's/.*"UnknownReason"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$DIAG/ui-rendered-service-truth-latest.json" 2>/dev/null | head -1)"
UI_CARD_ENGINE_SNAPSHOT_STATUS="$(sed -n 's/.*"EngineSnapshotStatus"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$DIAG/ui-rendered-service-truth-latest.json" 2>/dev/null | head -1)"
UI_CARD_ENGINE_SNAPSHOT_AGE_MS="$(sed -n 's/.*"EngineSnapshotAgeMs"[[:space:]]*:[[:space:]]*\([0-9][0-9]*\).*/\1/p' "$DIAG/ui-rendered-service-truth-latest.json" 2>/dev/null | head -1)"
UI_CARD_ENGINE_SNAPSHOT_ID_MISMATCH=false
grep -q '"EngineSnapshotIdMismatch"[[:space:]]*:[[:space:]]*true' "$DIAG/ui-rendered-service-truth-latest.json" 2>/dev/null && UI_CARD_ENGINE_SNAPSHOT_ID_MISMATCH=true
UI_CARD_PROVEN=false
[ "$UI_CARD_STATE" = current ] && [ -n "$UI_CARD_CID" ] && UI_CARD_PROVEN=true
DOCKER_PS_PROVEN=false; selected_in_docker_ps "$SELECTED_ENGINE_CID" && DOCKER_PS_PROVEN=true
ENGINE_API_PROVEN=false; selected_in_engine_api "$SELECTED_ENGINE_CID" && ENGINE_API_PROVEN=true
INSPECT_STATE_RUNNING=false; inspect_running_from_file "$DIAG/inspect-selected.http" && INSPECT_STATE_RUNNING=true
[ "$ENGINE_API_PROVEN" = true ] && [ "$INSPECT_STATE_RUNNING" = true ] && ENGINE_API_PROVEN=true || ENGINE_API_PROVEN=false
PROCESS_PROVEN=false; process_has_pid "$SELECTED_INSPECT_PID" && PROCESS_PROVEN=true
LISTENER_PID_OWNS_CONFIGURED_PORT=false; [ -n "$SELECTED_INSPECT_PID" ] && awk -F '\t' -v pid="$SELECTED_INSPECT_PID" '$3 == pid { found=1 } END{ exit found ? 0 : 1 }' "$DIAG/listener-owner-map.tsv" 2>/dev/null && LISTENER_PID_OWNS_CONFIGURED_PORT=true
LISTENER_PROVEN=false
[ "$SELECTED_ID_EXACT" = true ] && [ "$LISTENER_PID_OWNS_CONFIGURED_PORT" = true ] && LISTENER_PROVEN=true
LISTENER_OWNER_ENGINE_CID=""
[ "$LISTENER_PROVEN" = true ] && LISTENER_OWNER_ENGINE_CID="$SELECTED_ENGINE_CID"
LOGS_PROVEN=false; [ -n "$SELECTED_ENGINE_CID" ] && [ -s "$DIAG/logs-selected.out" ] && LOGS_PROVEN=true
LOGS_CURRENT_MARKER=false
[ "$LOGS_PROVEN" = true ] && grep -F "pdocker-service-truth-marker " "$DIAG/logs-selected.out" 2>/dev/null | grep -F "$SELECTED_ENGINE_CID" >/dev/null 2>&1 && LOGS_CURRENT_MARKER=true
LOGS_MARKER_ENGINE_CID=""
[ "$LOGS_CURRENT_MARKER" = true ] && LOGS_MARKER_ENGINE_CID="$SELECTED_ENGINE_CID"
LOGS_PROVEN=false
[ "$LOGS_CURRENT_MARKER" = true ] && LOGS_PROVEN=true

UI_SOURCE_CURRENT=false
case "$(printf '%s' "$UI_CARD_SOURCE" | tr '[:upper:]' '[:lower:]')" in
  ''|unknown|state.json|persistedstatejson|persisted_state_json|stale|ambiguous) UI_SOURCE_CURRENT=false ;;
  *) UI_SOURCE_CURRENT=true ;;
esac
[ "$SELECTED_ID_EXACT" = true ] && [ "$UI_CARD_CID" = "$SELECTED_ENGINE_CID" ] && [ "$UI_CARD_STATE" = current ] && [ "$UI_SOURCE_CURRENT" = true ] && [ -n "$UI_CARD_CURRENT_REASON" ] && UI_CARD_PROVEN=true || UI_CARD_PROVEN=false
write_same_id_source_summary "$SELECTED_ENGINE_CID" "$UI_CARD_CID" "$UI_CARD_STATE" "$STATE_MATCH" "$SELECTED_INSPECT_PID"

MISSING_SOURCES=""
MISMATCHED_SOURCES=""
[ "$SELECTED_ID_EXACT" = true ] || MISSING_SOURCES="$MISSING_SOURCES EngineContainerId"
[ "$UI_CARD_PROVEN" = true ] || MISSING_SOURCES="$MISSING_SOURCES UICard"
[ "$DOCKER_PS_PROVEN" = true ] || MISSING_SOURCES="$MISSING_SOURCES DockerPs"
[ "$ENGINE_API_PROVEN" = true ] || MISSING_SOURCES="$MISSING_SOURCES EngineApiContainersJson"
[ "$STATE_MATCH" = true ] || MISSING_SOURCES="$MISSING_SOURCES PersistedStateJson"
[ "$PROCESS_PROVEN" = true ] || MISSING_SOURCES="$MISSING_SOURCES ProcessTable"
[ "$LISTENER_PROVEN" = true ] || MISSING_SOURCES="$MISSING_SOURCES ListenerProbe"
[ "$LOGS_PROVEN" = true ] || MISSING_SOURCES="$MISSING_SOURCES ContainerLogs"
[ -n "$UI_CARD_CID" ] && [ -n "$SELECTED_ENGINE_CID" ] && [ "$UI_CARD_CID" != "$SELECTED_ENGINE_CID" ] && MISMATCHED_SOURCES="$MISMATCHED_SOURCES UICard"

SAME_ENGINE_CONTAINER_ID=false
SERVICE_TRUTH_STATUS="planned-gap"
SERVICE_TRUTH_SUCCESS=false
SERVICE_TRUTH_EXIT=2
if [ "$SELECTED_ID_EXACT" = true ]   && [ "$UI_CARD_PROVEN" = true ]   && [ "$DOCKER_PS_PROVEN" = true ]   && [ "$ENGINE_API_PROVEN" = true ]   && [ "$STATE_MATCH" = true ]   && [ "$PROCESS_PROVEN" = true ]   && [ "$LISTENER_PROVEN" = true ]   && [ "$LOGS_PROVEN" = true ]   && [ -z "$(printf '%s' "$MISMATCHED_SOURCES" | tr -d ' ')" ]; then
  SAME_ENGINE_CONTAINER_ID=true
  SERVICE_TRUTH_STATUS="device-pass"
  SERVICE_TRUTH_SUCCESS=true
  SERVICE_TRUTH_EXIT=0
fi

cat > "$LATEST" <<JSON
{
  "SchemaVersion": 1,
  "Kind": "service-truth",
  "Status": $(json_string "$SERVICE_TRUTH_STATUS"),
  "Success": $(json_bool "$SERVICE_TRUTH_SUCCESS"),
  "Target": $(json_string "$TARGET"),
  "StartedAt": $(json_string "$STARTED_AT"),
  "CompletedAt": $(json_string "$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date)"),
  "DeviceProofAttempted": true,
  "TruthContract": {
    "RequiredSameContainerId": ["UICard", "DockerPs", "EngineApiContainersJson", "PersistedStateJson", "ProcessTable", "ListenerProbe", "ContainerLogs"],
    "AcceptanceRule": "Success may become true only when every source names the same current Engine container ID for the service listener; configured ports, stale names, stale PIDs, previous logs, and background job success are insufficient."
  },
  "Proof": {
    "EngineContainerId": $( [ -n "$SELECTED_ENGINE_CID" ] && json_string "$SELECTED_ENGINE_CID" || printf null ),
    "SameEngineContainerId": $(json_bool "$SAME_ENGINE_CONTAINER_ID"),
    "AggregationArtifact": "files/$DIAG/same-id-source-summary.json",
    "MismatchedSources": $(json_word_array "$MISMATCHED_SOURCES"),
    "MissingSources": $(json_word_array "$MISSING_SOURCES")
  },
  "Observed": {
    "EngineCliExitCode": $(json_string "$ENGINE_PS_RC"),
    "EngineApiContainersStatus": $(json_string "$ENGINE_STATUS"),
    "EngineApiContainersRc": $(json_string "$ENGINE_HTTP_RC"),
    "RunningContainerIdCount": $(json_string "$CID_COUNT"),
    "ConfiguredPorts": $(json_string "$PORTS"),
    "SelectedEngineContainerId": $( [ -n "$SELECTED_ENGINE_CID" ] && json_string "$SELECTED_ENGINE_CID" || printf null ),
    "StateJsonMatchesSelectedEngineContainerId": $(json_bool "$STATE_MATCH"),
    "ListenerProcNetTcpMatchedPorts": $(json_string "$LISTENER_PROC_MATCH_PORTS")
  },
  "CandidateSelection": {
    "SelectedEngineContainerId": $( [ -n "$SELECTED_ENGINE_CID" ] && json_string "$SELECTED_ENGINE_CID" || printf null ),
    "Rule": "Select the best Engine container ID candidate from docker ps labels, names, target hints, and known listener ports; this is evidence, not acceptance, until UI/state/process/listener/log sources agree.",
    "Artifacts": ["files/$DIAG/engine-candidates.json", "files/$DIAG/engine-candidates.tsv", "files/$DIAG/engine-candidate-selected.txt"]
  },
  "StateIdComparison": {
    "SelectedEngineContainerId": $( [ -n "$SELECTED_ENGINE_CID" ] && json_string "$SELECTED_ENGINE_CID" || printf null ),
    "AnyStateIdMatchesSelected": $(json_bool "$STATE_MATCH"),
    "Artifacts": ["files/$DIAG/state-id-comparison.json", "files/$DIAG/state-container-ids.tsv", "files/$DIAG/persisted-state-json.txt"]
  },
  "ListenerProcNetTcpEvidence": {
    "MatchedPorts": $(json_string "$LISTENER_PROC_MATCH_PORTS"),
    "Artifacts": ["files/$DIAG/listener-probe.json", "files/$DIAG/listener-probe.txt", "files/$DIAG/proc-net-tcp.txt", "files/$DIAG/listeners-tool.txt"]
  },
  "Sources": {
    "UICard": {
      "ContainerId": $( [ -n "$UI_CARD_CID" ] && json_string "$UI_CARD_CID" || printf null ),
      "ContainerIdSource": $(json_string "${UI_CARD_SOURCE:-unknown}"),
      "TruthState": $(json_string "${UI_CARD_STATE:-unknown}"),
      "CurrentReason": $( [ -n "$UI_CARD_CURRENT_REASON" ] && json_string "$UI_CARD_CURRENT_REASON" || printf null ),
      "StaleReason": $( [ -n "$UI_CARD_STALE_REASON" ] && json_string "$UI_CARD_STALE_REASON" || printf null ),
      "UnknownReason": $( [ -n "$UI_CARD_UNKNOWN_REASON" ] && json_string "$UI_CARD_UNKNOWN_REASON" || printf null ),
      "EngineSnapshotStatus": $( [ -n "$UI_CARD_ENGINE_SNAPSHOT_STATUS" ] && json_string "$UI_CARD_ENGINE_SNAPSHOT_STATUS" || printf null ),
      "EngineSnapshotAgeMs": $(json_number_or_null "$UI_CARD_ENGINE_SNAPSHOT_AGE_MS"),
      "EngineSnapshotIdMismatch": $(json_bool "$UI_CARD_ENGINE_SNAPSHOT_ID_MISMATCH"),
      "ExactEngineContainerIdRequired": true,
      "Proven": $(json_bool "$UI_CARD_PROVEN"),
      "Artifacts": ["$UI_RENDERED_EXPORT", "files/$DIAG/ui-jobs.json", "files/$DIAG/ui-project-files.txt", "files/$DIAG/ui-project-snippets.txt"],
      "Gap": "Rendered UI card export is collected when the app has rendered it, but success remains false until UI/Engine/state/process/listener/log sources agree on the same current Engine container ID."
    },
    "DockerPs": {
      "ContainerId": $( [ "$DOCKER_PS_PROVEN" = true ] && [ -n "$SELECTED_ENGINE_CID" ] && json_string "$SELECTED_ENGINE_CID" || printf null ),
      "Running": $(json_bool "$DOCKER_PS_PROVEN"),
      "ExactEngineContainerIdRequired": true,
      "Proven": $(json_bool "$DOCKER_PS_PROVEN"),
      "Artifacts": ["files/$DIAG/engine-ps.out", "files/$DIAG/engine-ps-running.out", "files/$DIAG/engine-candidates.tsv", "files/$DIAG/engine-candidates.json"],
      "Gap": "docker ps evidence is reduced to the selected exact Engine container ID, but top-level success remains false until all sources agree and the gate is promoted."
    },
    "EngineApiContainersJson": {
      "ContainerId": $( [ "$ENGINE_API_PROVEN" = true ] && [ -n "$SELECTED_ENGINE_CID" ] && json_string "$SELECTED_ENGINE_CID" || printf null ),
      "CandidateSelected": $(json_bool "$( [ -n "$SELECTED_ENGINE_CID" ] && echo true || echo false )"),
      "CurrentContainerFound": $(json_bool "$ENGINE_API_PROVEN"),
      "InspectStateRunning": $(json_bool "$INSPECT_STATE_RUNNING"),
      "ExactEngineContainerIdRequired": true,
      "Proven": $(json_bool "$ENGINE_API_PROVEN"),
      "Artifacts": ["files/$DIAG/engine-containers-json.http", "files/$DIAG/inspect-selected.http", "files/$DIAG/docker-inspect-selected.out", "files/$DIAG/container-ids.txt", "files/$DIAG/engine-candidates.json"],
      "Gap": "Engine API evidence is reduced to the selected exact Engine container ID, but it is not acceptance until UI/state/process/listener/log sources agree."
    },
    "PersistedStateJson": {
      "ContainerId": $( [ "$STATE_MATCH" = true ] && [ -n "$SELECTED_ENGINE_CID" ] && json_string "$SELECTED_ENGINE_CID" || printf null ),
      "MatchesSelectedEngineContainerId": $(json_bool "$STATE_MATCH"),
      "ExactEngineContainerIdRequired": true,
      "Proven": $(json_bool "$STATE_MATCH"),
      "Artifacts": ["files/$DIAG/persisted-state-json.txt", "files/$DIAG/state-id-comparison.json", "files/$DIAG/state-container-ids.tsv"],
      "Gap": "state.json ID comparison is machine-readable, but still not a same-source acceptance proof without UI/process/listener/log agreement."
    },
    "ProcessTable": {
      "ContainerId": $( [ "$PROCESS_PROVEN" = true ] && [ -n "$SELECTED_ENGINE_CID" ] && json_string "$SELECTED_ENGINE_CID" || printf null ),
      "Pid": $(json_number_or_null "$SELECTED_INSPECT_PID"),
      "SelectedPidPresent": $(json_bool "$PROCESS_PROVEN"),
      "ExactEngineContainerIdRequired": true,
      "Proven": $(json_bool "$PROCESS_PROVEN"),
      "Artifacts": ["files/$DIAG/process-table.txt", "files/$DIAG/inspect-selected.http", "files/$DIAG/docker-inspect-selected.out"],
      "Gap": "Selected inspect PID is searched in the process table, but top-level success remains false until all seven sources agree."
    },
    "ListenerProbe": {
      "ContainerId": $( [ "$LISTENER_PROVEN" = true ] && [ -n "$SELECTED_ENGINE_CID" ] && json_string "$SELECTED_ENGINE_CID" || printf null ),
      "OwnerEngineContainerId": $( [ -n "$LISTENER_OWNER_ENGINE_CID" ] && json_string "$LISTENER_OWNER_ENGINE_CID" || printf null ),
      "SelectedEngineContainerId": $( [ -n "$SELECTED_ENGINE_CID" ] && json_string "$SELECTED_ENGINE_CID" || printf null ),
      "Pid": $(json_number_or_null "$SELECTED_INSPECT_PID"),
      "SelectedPidOwnsListener": $(json_bool "$LISTENER_PID_OWNS_CONFIGURED_PORT"),
      "ExactEngineContainerIdRequired": true,
      "Proven": $(json_bool "$LISTENER_PROVEN"),
      "Artifacts": ["files/$DIAG/configured-ports.txt", "files/$DIAG/listener-probe.json", "files/$DIAG/listener-owner-map.json", "files/$DIAG/listener-owner-map.tsv", "files/$DIAG/listener-probe.txt", "files/$DIAG/proc-net-tcp.txt", "files/$DIAG/listeners-tool.txt"],
      "ProcNetTcpMatchedPorts": $(json_string "$LISTENER_PROC_MATCH_PORTS"),
      "Gap": "Listener port declarations and /proc/net/tcp matches are not proof by themselves; the listener PID must map to the selected process and OwnerEngineContainerId must be the exact same 64-hex Engine container ID before top-level success can pass."
    },
    "ContainerLogs": {
      "ContainerId": $( [ "$LOGS_PROVEN" = true ] && [ -n "$SELECTED_ENGINE_CID" ] && json_string "$SELECTED_ENGINE_CID" || printf null ),
      "Proven": $(json_bool "$LOGS_PROVEN"),
      "CurrentServiceMarker": $(json_bool "$LOGS_CURRENT_MARKER"),
      "MarkerEngineContainerId": $( [ -n "$LOGS_MARKER_ENGINE_CID" ] && json_string "$LOGS_MARKER_ENGINE_CID" || printf null ),
      "ExactEngineContainerIdRequired": true,
      "Artifacts": ["files/$DIAG/logs-selected.out", "files/$DIAG/logs-<container-id>.out"],
      "Gap": "Logs are collected for the selected Engine container ID and checked for pdocker-service-truth-marker, but top-level success remains planned-gap until all seven sources are promoted together."
    }
  },
  "Evidence": {
    "UICard": ["$UI_RENDERED_EXPORT", "files/$DIAG/ui-jobs.json", "files/$DIAG/ui-project-snippets.txt"],
    "DockerPs": ["files/$DIAG/engine-ps.out", "files/$DIAG/engine-ps-running.out", "files/$DIAG/engine-candidates.tsv", "files/$DIAG/engine-candidates.json"],
    "EngineApiContainersJson": ["files/$DIAG/engine-containers-json.http", "files/$DIAG/inspect-selected.http", "files/$DIAG/docker-inspect-selected.out", "files/$DIAG/container-ids.txt", "files/$DIAG/engine-candidates.json"],
    "PersistedStateJson": ["files/$DIAG/persisted-state-json.txt", "files/$DIAG/state-id-comparison.json"],
    "ProcessTable": ["files/$DIAG/process-table.txt", "files/$DIAG/inspect-selected.http", "files/$DIAG/docker-inspect-selected.out"],
    "ListenerProbe": ["files/$DIAG/configured-ports.txt", "files/$DIAG/listener-probe.json", "files/$DIAG/listener-owner-map.json", "files/$DIAG/listener-owner-map.tsv", "files/$DIAG/listener-probe.txt", "files/$DIAG/proc-net-tcp.txt", "files/$DIAG/listeners-tool.txt"],
    "ContainerLogs": ["files/$DIAG/logs-selected.out", "files/$DIAG/logs-<container-id>.out"],
    "SameIdAggregation": ["files/$DIAG/same-id-source-summary.json"]
  },
  "Unresolved": [
    "Rendered UI card container ID is exported as unknown/stale/current when the app has rendered files/pdocker/diagnostics/ui-rendered-service-truth-latest.json; missing or stale UI export is not success.",
    "Engine candidate selection, state.json ID comparison, and listener /proc/net/tcp evidence are recorded, but process/listener ownership and UI card agreement are not yet reduced to one same-container-ID proof.",
    "Negative cases for configured-port-only, stale listener/PID, duplicate name, and previous-container logs remain unproven on device."
  ]
}
JSON
cat "$LATEST"
exit "$SERVICE_TRUTH_EXIT"
REMOTE_SERVICE_TRUTH
  run_adb push "$local_script" "$remote_script" >/dev/null
  rm -f "$local_script"
  run_adb shell chmod 755 "$remote_script" >/dev/null 2>&1 || true
  run_as "sh $remote_script $(remote_quote "$target")"
}
runtime_teardown_acceptance_entrypoint() {
  local target="$1"
  local remote_script="/data/local/tmp/pdocker-runtime-teardown-smoke.sh"
  local local_script
  local_script="$(mktemp)"
  cat > "$local_script" <<'REMOTE_RUNTIME_TEARDOWN'
#!/system/bin/sh
set +e
TARGET="${1:-default-workspace}"
cd files || exit 1
mkdir -p pdocker/tmp
export TMPDIR="$PWD/pdocker/tmp"
export PATH="$PWD/pdocker-runtime/docker-bin:$PATH"
export DOCKER_CONFIG="$PWD/pdocker-runtime/docker-bin"
export DOCKER_HOST="unix://$PWD/pdocker/pdockerd.sock"
export DOCKER_BUILDKIT=0 COMPOSE_DOCKER_CLI_BUILD=0 BUILDKIT_PROGRESS=plain COMPOSE_PROGRESS=plain COMPOSE_MENU=false

DIAG="pdocker/diagnostics/runtime-teardown"
LATEST="pdocker/diagnostics/runtime-teardown-latest.json"
rm -rf "$DIAG"
mkdir -p "$DIAG"
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date)"
RUN_TAG="rt-$(date +%s 2>/dev/null || echo now)-$$"
STOP_NAME="pdocker-runtime-teardown-stop-$RUN_TAG"
KILL_NAME="pdocker-runtime-teardown-kill-$RUN_TAG"
IMAGE="ubuntu:22.04"
SOCKET="pdocker/pdockerd.sock"

json_string() {
  printf '%s' "$1" | awk 'BEGIN{printf "\""}{gsub(/\\/,"\\\\"); gsub(/\"/,"\\\""); gsub(/\t/,"\\t"); if (NR>1) printf "\\n"; printf "%s",$0}END{printf "\""}'
}

record_cmd() {
  label="$1"
  shift
  out="$DIAG/$label.out"
  err="$DIAG/$label.err"
  "$@" >"$out" 2>"$err"
  rc=$?
  printf '%s' "$rc" >"$DIAG/$label.rc"
  return 0
}

http_get() {
  label="$1"
  path="$2"
  { printf 'GET %s HTTP/1.1\r\nHost: docker\r\nConnection: close\r\n\r\n' "$path"; } \
    | nc -U -W 5 "$SOCKET" >"$DIAG/$label.http" 2>"$DIAG/$label.err"
  rc=$?
  printf '%s' "$rc" >"$DIAG/$label.rc"
  sed -n '1s/\r$//p' "$DIAG/$label.http" >"$DIAG/$label.status" 2>/dev/null
}

snapshot_ps() {
  label="$1"
  (ps -A -o PID,PPID,USER,NAME,ARGS 2>/dev/null || ps -A -ef 2>/dev/null || ps 2>/dev/null) >"$DIAG/$label.txt" 2>"$DIAG/$label.err"
}

snapshot_listeners() {
  label="$1"
  {
    printf '%s\n' '--- /proc/net/tcp ---'
    cat /proc/net/tcp 2>/dev/null || true
    printf '%s\n' '--- /proc/net/tcp6 ---'
    cat /proc/net/tcp6 2>/dev/null || true
    printf '%s\n' '--- ss -ltnp ---'
    ss -ltnp 2>/dev/null || true
    printf '%s\n' '--- netstat -ltnp ---'
    netstat -ltnp 2>/dev/null || true
  } >"$DIAG/$label.txt" 2>"$DIAG/$label.err"
}

snapshot_executor_residue() {
  label="$1"
  snapshot_ps "$label-all-processes"
  {
    printf '%s\n' 'Residue search terms: pdocker gpu media camera audio vulkan executor'
    grep -Ei 'pdocker.*(gpu|media|camera|audio|vulkan|executor)|gpuexecutor|media.*executor|camera|audio|vulkan' \
      "$DIAG/$label-all-processes.txt" 2>/dev/null || true
  } >"$DIAG/$label.txt" 2>"$DIAG/$label.err"
}

snapshot_state_json() {
  label="$1"
  out="$DIAG/$label.txt"
  : >"$out"
  find pdocker -name state.json -type f 2>/dev/null | sort | while IFS= read -r f; do
    printf '\n--- %s ---\n' "$f" >>"$out"
    cat "$f" >>"$out" 2>/dev/null
    printf '\n' >>"$out"
  done
}

container_id_from_out() {
  tr -d '\r\n' < "$1" | sed 's/[^0-9a-f].*$//'
}

inspect_pid_from_http() {
  grep -ao '"Pid"[[:space:]]*:[[:space:]]*[0-9][0-9]*' "$1" 2>/dev/null \
    | head -n 1 \
    | sed 's/.*://; s/[^0-9]//g'
}

write_pid_evidence() {
  label="$1"
  cid="$2"
  inspect_http="$3"
  process_snapshot="$4"
  pid="$(inspect_pid_from_http "$inspect_http")"
  if [ -z "$pid" ] || [ "$pid" = "0" ]; then
    present="unknown"
  elif grep -Eq "(^|[[:space:]])$pid([[:space:]]|$)" "$process_snapshot" 2>/dev/null; then
    present="true"
  else
    present="false"
  fi
  cat >"$DIAG/$label.json" <<JSON
{
  "ContainerId": $(json_string "$cid"),
  "InspectHttp": $(json_string "files/$inspect_http"),
  "ProcessSnapshot": $(json_string "files/$process_snapshot"),
  "InspectPid": $(json_string "$pid"),
  "PidStillPresentInSnapshot": $(json_string "$present"),
  "Contract": "A stopped/killed/removed container is not proven torn down until its inspect PID/process tree and any stale PID references are absent for the same Engine container ID."
}
JSON
}

write_same_id_evidence() {
  label="$1"
  cid="$2"
  operation="$3"
  create_out="$4"
  inspect_before="$5"
  inspect_after="$6"
  inspect_after_rm="$7"
  process_before="$8"
  process_after_start="$9"
  process_after_operation="${10}"
  process_after_rm="${11}"
  listener_before="${12}"
  listener_after_start="${13}"
  listener_after_operation="${14}"
  listener_after_rm="${15}"
  residue_before="${16}"
  residue_after_start="${17}"
  residue_after_operation="${18}"
  residue_after_rm="${19}"
  state_before="${20}"
  state_after_start="${21}"
  state_after_operation="${22}"
  state_after_rm="${23}"
  stale_pid_after_operation="${24}"
  stale_pid_after_rm="${25}"
  logs_before="${26}"
  logs_after="${27}"
  case "$operation" in
    stop-rm) lifecycle_op="stop"; lifecycle_rm="rm-stopped" ;;
    kill-rm) lifecycle_op="kill"; lifecycle_rm="rm-killed" ;;
    *) lifecycle_op="$operation"; lifecycle_rm="rm" ;;
  esac
  cat >"$DIAG/$label.json" <<JSON
{
  "SchemaVersion": 2,
  "Kind": "same-container-id-teardown-proof",
  "ContainerId": $(json_string "$cid"),
  "Operation": $(json_string "$operation"),
  "Status": "planned-gap",
  "Success": false,
  "RequiredSameContainerId": [
    "EngineCreateOutput",
    "EngineInspectBefore",
    "EngineInspectAfterOperation",
    "EngineInspectAfterRemove",
    "ProcessTreeBeforeAfter",
    "ListenerAbsenceBeforeAfter",
    "StalePidAbsence",
    "GpuMediaExecutorResidueBeforeAfter",
    "PersistedStateJsonBeforeAfter",
    "LifecycleLogs",
    "ContainerLogs"
  ],
  "BeforeAfterEvidence": {
    "EngineInspect": {
      "Before": $(json_string "files/$inspect_before"),
      "AfterOperation": $(json_string "files/$inspect_after"),
      "AfterRemove": $(json_string "files/$inspect_after_rm")
    },
    "ProcessTree": {
      "Before": $(json_string "files/$process_before"),
      "AfterStart": $(json_string "files/$process_after_start"),
      "AfterOperation": $(json_string "files/$process_after_operation"),
      "AfterRemove": $(json_string "files/$process_after_rm")
    },
    "ListenerAbsence": {
      "Before": $(json_string "files/$listener_before"),
      "AfterStart": $(json_string "files/$listener_after_start"),
      "AfterOperation": $(json_string "files/$listener_after_operation"),
      "AfterRemove": $(json_string "files/$listener_after_rm")
    },
    "GpuMediaExecutorResidue": {
      "Before": $(json_string "files/$residue_before"),
      "AfterStart": $(json_string "files/$residue_after_start"),
      "AfterOperation": $(json_string "files/$residue_after_operation"),
      "AfterRemove": $(json_string "files/$residue_after_rm")
    },
    "PersistedStateJson": {
      "Before": $(json_string "files/$state_before"),
      "AfterStart": $(json_string "files/$state_after_start"),
      "AfterOperation": $(json_string "files/$state_after_operation"),
      "AfterRemove": $(json_string "files/$state_after_rm")
    },
    "StalePid": {
      "AfterOperation": $(json_string "files/$stale_pid_after_operation"),
      "AfterRemove": $(json_string "files/$stale_pid_after_rm")
    },
    "ContainerLogs": {
      "BeforeOperation": $(json_string "files/$logs_before"),
      "AfterOperation": $(json_string "files/$logs_after")
    },
    "LifecycleLogs": {
      "OperationOut": $(json_string "files/$DIAG/$lifecycle_op.out"),
      "OperationErr": $(json_string "files/$DIAG/$lifecycle_op.err"),
      "RemoveOut": $(json_string "files/$DIAG/$lifecycle_rm.out"),
      "RemoveErr": $(json_string "files/$DIAG/$lifecycle_rm.err")
    }
  },
  "Evidence": {
    "EngineCreateOutput": $(json_string "files/$create_out"),
    "EngineInspectBefore": $(json_string "files/$inspect_before"),
    "EngineInspectAfterOperation": $(json_string "files/$inspect_after"),
    "EngineInspectAfterRemove": $(json_string "files/$inspect_after_rm"),
    "ProcessTreeBeforeAfter": [$(json_string "files/$process_before"), $(json_string "files/$process_after_start"), $(json_string "files/$process_after_operation"), $(json_string "files/$process_after_rm")],
    "ListenerAbsenceBeforeAfter": [$(json_string "files/$listener_before"), $(json_string "files/$listener_after_start"), $(json_string "files/$listener_after_operation"), $(json_string "files/$listener_after_rm")],
    "GpuMediaExecutorResidueBeforeAfter": [$(json_string "files/$residue_before"), $(json_string "files/$residue_after_start"), $(json_string "files/$residue_after_operation"), $(json_string "files/$residue_after_rm")],
    "PersistedStateJsonBeforeAfter": [$(json_string "files/$state_before"), $(json_string "files/$state_after_start"), $(json_string "files/$state_after_operation"), $(json_string "files/$state_after_rm")],
    "StalePidAbsence": [$(json_string "files/$stale_pid_after_operation"), $(json_string "files/$stale_pid_after_rm")],
    "LifecycleLogs": [$(json_string "files/$DIAG/$lifecycle_op.out"), $(json_string "files/$DIAG/$lifecycle_op.err"), $(json_string "files/$DIAG/$lifecycle_rm.out"), $(json_string "files/$DIAG/$lifecycle_rm.err")],
    "ContainerLogs": [$(json_string "files/$logs_before"), $(json_string "files/$logs_after")]
  },
  "AcceptanceRule": "A device verifier may only pass this artifact when every before/after evidence source is tied to this exact Engine container ID and proves no surviving process tree, listener, stale PID, GPU/media executor residue, stale state reference, or misleading previous log remains after removal; persisted state after stop/kill must show PdockerTeardown.NoOrphanProcesses=true and empty PdockerTeardown.Survivors.",
  "Contract": "Same-container-ID teardown proof is collected, but this scaffold must remain non-passing until the device verifier proves process tree, listeners, stale PID references, GPU/media executor residue, persisted state, Engine inspect, and logs all agree for this exact container ID."
}
JSON
}

write_negative_case_evidence() {
  label="$1"
  rejected_signal="$2"
  reason="$3"
  cat >"$DIAG/$label.json" <<JSON
{
  "SchemaVersion": 1,
  "Kind": "runtime-teardown-negative-case",
  "Status": "planned-gap",
  "Success": false,
  "ExpectedAccepted": false,
  "RejectedSignal": $(json_string "$rejected_signal"),
  "Reason": $(json_string "$reason"),
  "RequiredProof": "same Engine container ID plus before/after process tree, listener absence, stale PID absence, GPU/media executor residue, persisted state, Engine inspect, lifecycle logs, and container logs"
}
JSON
}

http_get engine-containers-before '/containers/json?all=1'
snapshot_ps process-before
snapshot_listeners listeners-before
snapshot_executor_residue executor-residue-before
snapshot_state_json persisted-state-before

record_cmd create-stop docker create --name "$STOP_NAME" "$IMAGE" sh -lc 'trap "" TERM; while true; do sleep 30; done'
STOP_CID="$(container_id_from_out "$DIAG/create-stop.out")"
record_cmd start-stop docker start "$STOP_CID"
http_get stop-inspect-before "/containers/$STOP_CID/json"
record_cmd logs-stop-before docker logs "$STOP_CID"
snapshot_ps process-after-stop-start
snapshot_listeners listeners-after-stop-start
snapshot_executor_residue executor-residue-after-stop-start
snapshot_state_json persisted-state-after-stop-start
record_cmd stop docker stop -t 1 "$STOP_CID"
http_get stop-inspect-after "/containers/$STOP_CID/json"
record_cmd logs-stop-after docker logs "$STOP_CID"
snapshot_ps process-after-stop
snapshot_listeners listeners-after-stop
snapshot_executor_residue executor-residue-after-stop
write_pid_evidence stop-stale-pid-after-stop "$STOP_CID" "$DIAG/stop-inspect-after.http" "$DIAG/process-after-stop.txt"
snapshot_state_json persisted-state-after-stop
record_cmd rm-stopped docker rm "$STOP_CID"
http_get stop-inspect-after-rm "/containers/$STOP_CID/json"
snapshot_ps process-after-rm-stopped
snapshot_listeners listeners-after-rm-stopped
snapshot_executor_residue executor-residue-after-rm-stopped
write_pid_evidence stop-stale-pid-after-rm "$STOP_CID" "$DIAG/stop-inspect-after-rm.http" "$DIAG/process-after-rm-stopped.txt"
snapshot_state_json persisted-state-after-rm-stopped
write_same_id_evidence same-container-id-stop-rm "$STOP_CID" "stop-rm" "$DIAG/create-stop.out" "$DIAG/stop-inspect-before.http" "$DIAG/stop-inspect-after.http" "$DIAG/stop-inspect-after-rm.http" "$DIAG/process-before.txt" "$DIAG/process-after-stop-start.txt" "$DIAG/process-after-stop.txt" "$DIAG/process-after-rm-stopped.txt" "$DIAG/listeners-before.txt" "$DIAG/listeners-after-stop-start.txt" "$DIAG/listeners-after-stop.txt" "$DIAG/listeners-after-rm-stopped.txt" "$DIAG/executor-residue-before.txt" "$DIAG/executor-residue-after-stop-start.txt" "$DIAG/executor-residue-after-stop.txt" "$DIAG/executor-residue-after-rm-stopped.txt" "$DIAG/persisted-state-before.txt" "$DIAG/persisted-state-after-stop-start.txt" "$DIAG/persisted-state-after-stop.txt" "$DIAG/persisted-state-after-rm-stopped.txt" "$DIAG/stop-stale-pid-after-stop.json" "$DIAG/stop-stale-pid-after-rm.json" "$DIAG/logs-stop-before.out" "$DIAG/logs-stop-after.out"

record_cmd create-kill docker create --name "$KILL_NAME" "$IMAGE" sh -lc 'while true; do sleep 30; done'
KILL_CID="$(container_id_from_out "$DIAG/create-kill.out")"
record_cmd start-kill docker start "$KILL_CID"
http_get kill-inspect-before "/containers/$KILL_CID/json"
record_cmd logs-kill-before docker logs "$KILL_CID"
snapshot_ps process-after-kill-start
snapshot_listeners listeners-after-kill-start
snapshot_executor_residue executor-residue-after-kill-start
snapshot_state_json persisted-state-after-kill-start
record_cmd kill docker kill "$KILL_CID"
http_get kill-inspect-after "/containers/$KILL_CID/json"
record_cmd logs-kill-after docker logs "$KILL_CID"
snapshot_ps process-after-kill
snapshot_listeners listeners-after-kill
snapshot_executor_residue executor-residue-after-kill
write_pid_evidence kill-stale-pid-after-kill "$KILL_CID" "$DIAG/kill-inspect-after.http" "$DIAG/process-after-kill.txt"
snapshot_state_json persisted-state-after-kill
record_cmd rm-killed docker rm "$KILL_CID"
http_get kill-inspect-after-rm "/containers/$KILL_CID/json"
snapshot_ps process-after-rm-killed
snapshot_listeners listeners-after-rm-killed
snapshot_executor_residue executor-residue-after-rm-killed
write_pid_evidence kill-stale-pid-after-rm "$KILL_CID" "$DIAG/kill-inspect-after-rm.http" "$DIAG/process-after-rm-killed.txt"
snapshot_state_json persisted-state-after-rm-killed
write_same_id_evidence same-container-id-kill-rm "$KILL_CID" "kill-rm" "$DIAG/create-kill.out" "$DIAG/kill-inspect-before.http" "$DIAG/kill-inspect-after.http" "$DIAG/kill-inspect-after-rm.http" "$DIAG/process-before.txt" "$DIAG/process-after-kill-start.txt" "$DIAG/process-after-kill.txt" "$DIAG/process-after-rm-killed.txt" "$DIAG/listeners-before.txt" "$DIAG/listeners-after-kill-start.txt" "$DIAG/listeners-after-kill.txt" "$DIAG/listeners-after-rm-killed.txt" "$DIAG/executor-residue-before.txt" "$DIAG/executor-residue-after-kill-start.txt" "$DIAG/executor-residue-after-kill.txt" "$DIAG/executor-residue-after-rm-killed.txt" "$DIAG/persisted-state-before.txt" "$DIAG/persisted-state-after-kill-start.txt" "$DIAG/persisted-state-after-kill.txt" "$DIAG/persisted-state-after-rm-killed.txt" "$DIAG/kill-stale-pid-after-kill.json" "$DIAG/kill-stale-pid-after-rm.json" "$DIAG/logs-kill-before.out" "$DIAG/logs-kill-after.out"
http_get engine-containers-after '/containers/json?all=1'

write_negative_case_evidence negative-http-204-only "HTTP 204 or Engine API acknowledgement alone" "Reject API acknowledgement without same-container-ID before/after process tree, listener, stale PID, GPU executor, state, and log proof."
write_negative_case_evidence negative-cli-exit-zero-only "CLI exit 0 alone" "Reject successful docker stop/kill/rm command output without matching Engine inspect and absence evidence for the same container ID."
write_negative_case_evidence negative-name-only "matching container name without same Engine container ID" "Reject name or label matches that cannot be tied to the exact Engine container ID created by this teardown probe."
write_negative_case_evidence negative-stale-state-json "stale state.json still names a removed container" "Reject persisted state that still references the container after rm unless the verifier proves it is stale and absent from live runtime evidence."
write_negative_case_evidence negative-listener-only "listener absence without process-tree and stale-PID proof" "Reject clean /proc/net/tcp, ss, or netstat snapshots when process tree, stale PID, GPU executor residue, state, and logs are not proven for the same container ID."
write_negative_case_evidence negative-process-only "clean process table without Engine inspect and logs" "Reject process-table absence that is not tied to Engine inspect, lifecycle logs, container logs, listener absence, and persisted state for the same container ID."
write_negative_case_evidence negative-previous-container-logs "previous-container logs or reused names" "Reject logs from an earlier container, duplicate name, or reused project unless log evidence is bound to this exact Engine container ID."
write_negative_case_evidence negative-wrong-container-id "mixed evidence from a different container ID" "Reject any before/after proof set where Engine create, inspect, process, listener, stale PID, GPU executor, state, or logs name different container IDs."

# Keep the compatibility evidence above immutable, then clean up any test
# residue best-effort so this planned-gap probe does not poison later smokes.
: >"$DIAG/cleanup-leftovers.out"
: >"$DIAG/cleanup-leftovers.err"
CLEANUP_RC=0
for cid in "$STOP_CID" "$KILL_CID"; do
  if [ -n "$cid" ]; then
    docker rm -f "$cid" >>"$DIAG/cleanup-leftovers.out" 2>>"$DIAG/cleanup-leftovers.err" || CLEANUP_RC=$?
  fi
done
printf '%s' "$CLEANUP_RC" >"$DIAG/cleanup-leftovers.rc"

STOP_RC="$(cat "$DIAG/stop.rc" 2>/dev/null)"
KILL_RC="$(cat "$DIAG/kill.rc" 2>/dev/null)"
RM_STOP_RC="$(cat "$DIAG/rm-stopped.rc" 2>/dev/null)"
RM_KILL_RC="$(cat "$DIAG/rm-killed.rc" 2>/dev/null)"
CLEANUP_RC="$(cat "$DIAG/cleanup-leftovers.rc" 2>/dev/null)"
STOP_AFTER_STATUS="$(cat "$DIAG/stop-inspect-after.status" 2>/dev/null)"
KILL_AFTER_STATUS="$(cat "$DIAG/kill-inspect-after.status" 2>/dev/null)"
STOP_RM_STATUS="$(cat "$DIAG/stop-inspect-after-rm.status" 2>/dev/null)"
KILL_RM_STATUS="$(cat "$DIAG/kill-inspect-after-rm.status" 2>/dev/null)"

cat > "$LATEST" <<JSON
{
  "SchemaVersion": 1,
  "Kind": "runtime-teardown",
  "Status": "planned-gap",
  "Success": false,
  "Target": $(json_string "$TARGET"),
  "StartedAt": $(json_string "$STARTED_AT"),
  "CompletedAt": $(json_string "$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date)"),
  "ContainerIds": {
    "StopRm": $(json_string "$STOP_CID"),
    "KillRm": $(json_string "$KILL_CID")
  },
  "Operations": {
    "Stop": {"CliExitCode": $(json_string "$STOP_RC"), "InspectAfterStatus": $(json_string "$STOP_AFTER_STATUS")},
    "Kill": {"CliExitCode": $(json_string "$KILL_RC"), "InspectAfterStatus": $(json_string "$KILL_AFTER_STATUS")},
    "RmStopped": {"CliExitCode": $(json_string "$RM_STOP_RC"), "InspectAfterStatus": $(json_string "$STOP_RM_STATUS")},
    "RmKilled": {"CliExitCode": $(json_string "$RM_KILL_RC"), "InspectAfterStatus": $(json_string "$KILL_RM_STATUS")},
    "CleanupLeftovers": {"CliExitCode": $(json_string "$CLEANUP_RC")}
  },
  "Evidence": {
    "EngineApiContainersJson": ["files/$DIAG/engine-containers-before.http", "files/$DIAG/engine-containers-after.http"],
    "EngineApiInspect": ["files/$DIAG/stop-inspect-before.http", "files/$DIAG/stop-inspect-after.http", "files/$DIAG/stop-inspect-after-rm.http", "files/$DIAG/kill-inspect-before.http", "files/$DIAG/kill-inspect-after.http", "files/$DIAG/kill-inspect-after-rm.http"],
    "ProcessTable": ["files/$DIAG/process-before.txt", "files/$DIAG/process-after-stop-start.txt", "files/$DIAG/process-after-stop.txt", "files/$DIAG/process-after-rm-stopped.txt", "files/$DIAG/process-after-kill-start.txt", "files/$DIAG/process-after-kill.txt", "files/$DIAG/process-after-rm-killed.txt"],
    "ProcessTree": ["files/$DIAG/same-container-id-stop-rm.json", "files/$DIAG/same-container-id-kill-rm.json"],
    "ListenerAbsence": ["files/$DIAG/listeners-before.txt", "files/$DIAG/listeners-after-stop.txt", "files/$DIAG/listeners-after-rm-stopped.txt", "files/$DIAG/listeners-after-kill.txt", "files/$DIAG/listeners-after-rm-killed.txt"],
    "StalePid": ["files/$DIAG/stop-stale-pid-after-stop.json", "files/$DIAG/stop-stale-pid-after-rm.json", "files/$DIAG/kill-stale-pid-after-kill.json", "files/$DIAG/kill-stale-pid-after-rm.json"],
    "GpuMediaExecutorResidue": ["files/$DIAG/executor-residue-before.txt", "files/$DIAG/executor-residue-after-rm-stopped.txt", "files/$DIAG/executor-residue-after-rm-killed.txt"],
    "SameContainerId": ["files/$DIAG/same-container-id-stop-rm.json", "files/$DIAG/same-container-id-kill-rm.json"],
    "PersistedStateJson": ["files/$DIAG/persisted-state-before.txt", "files/$DIAG/persisted-state-after-stop.txt", "files/$DIAG/persisted-state-after-rm-stopped.txt", "files/$DIAG/persisted-state-after-kill.txt", "files/$DIAG/persisted-state-after-rm-killed.txt"],
    "LifecycleLogs": ["files/$DIAG/stop.out", "files/$DIAG/stop.err", "files/$DIAG/kill.out", "files/$DIAG/kill.err", "files/$DIAG/rm-stopped.out", "files/$DIAG/rm-stopped.err", "files/$DIAG/rm-killed.out", "files/$DIAG/rm-killed.err", "files/$DIAG/cleanup-leftovers.out", "files/$DIAG/cleanup-leftovers.err"],
    "ContainerLogs": ["files/$DIAG/logs-stop-before.out", "files/$DIAG/logs-stop-after.out", "files/$DIAG/logs-kill-before.out", "files/$DIAG/logs-kill-after.out"]
  },
  "TeardownProofSchema": {
    "Name": "same-container-id-teardown-artifact",
    "Version": 2,
    "SuccessInvariant": "Success remains false until a device verifier proves every before/after artifact for one exact Engine container ID.",
    "PerContainerArtifacts": ["files/$DIAG/same-container-id-stop-rm.json", "files/$DIAG/same-container-id-kill-rm.json"]
  },
  "SameContainerIdTeardownArtifacts": {
    "StopRm": {
      "ContainerId": $(json_string "$STOP_CID"),
      "Artifact": "files/$DIAG/same-container-id-stop-rm.json",
      "Operation": "stop-rm"
    },
    "KillRm": {
      "ContainerId": $(json_string "$KILL_CID"),
      "Artifact": "files/$DIAG/same-container-id-kill-rm.json",
      "Operation": "kill-rm"
    }
  },
  "BeforeAfterEvidenceRequired": [
    "Engine inspect before/after operation/after remove for the same Engine container ID",
    "Process tree before/start/after operation/after remove for the same Engine container ID",
    "Listener absence from /proc/net/tcp, /proc/net/tcp6, ss -ltnp, and netstat -ltnp before/after",
    "Stale PID absence from inspect State.Pid and post-operation process tables",
    "GPU/media executor residue before/start/after operation/after remove",
    "Persisted state.json before/start/after operation/after remove, including PdockerTeardown.NoOrphanProcesses=true and an empty PdockerTeardown.Survivors list after successful stop/kill",
    "Lifecycle logs and container logs bound to the same Engine container ID"
  ],
  "RuntimeTeardownStateContract": "After successful stop/kill, pdockerd must clear State.Pid, PidStartTime, PdockerKnownPids, PdockerLauncherPid, and PdockerLauncherPidStartTime; PdockerTeardown.NoOrphanProcesses must be true and PdockerTeardown.Survivors must be empty before remove is accepted.",
  "NegativeCases": {
    "Http204Only": "files/$DIAG/negative-http-204-only.json",
    "CliExitZeroOnly": "files/$DIAG/negative-cli-exit-zero-only.json",
    "NameOnly": "files/$DIAG/negative-name-only.json",
    "StaleStateJson": "files/$DIAG/negative-stale-state-json.json",
    "ListenerOnly": "files/$DIAG/negative-listener-only.json",
    "ProcessOnly": "files/$DIAG/negative-process-only.json",
    "PreviousContainerLogs": "files/$DIAG/negative-previous-container-logs.json",
    "WrongContainerId": "files/$DIAG/negative-wrong-container-id.json"
  },
  "RequiredSameContainerId": [
    "EngineCreateOutput",
    "EngineInspect",
    "ProcessTreeBeforeAfter",
    "ListenerAbsenceBeforeAfter",
    "StalePidAbsence",
    "GpuMediaExecutorResidueBeforeAfter",
    "PersistedStateJsonBeforeAfter",
    "LifecycleLogs",
    "ContainerLogs"
  ],
  "TruthContract": "stop/kill/rm remains non-passing with no fake success until every teardown evidence source proves absence for the same Engine container ID; HTTP 204, CLI exit 0, stale state.json, configured name, listener absence alone, clean process table alone, previous logs, duplicate names, or mixed container IDs are never sufficient.",
  "Unresolved": [
    "HTTP/CLI acknowledgement is recorded but not accepted as proof of teardown.",
    "The smoke does not yet map every observed process back to the Engine container ID/process tree.",
    "Listener absence, stale PID absence, and GPU/media executor residue are collected but not yet reduced by a device verifier into a passing proof.",
    "This planned-gap artifact is explicitly not fake success.",
    "Device verifier promotion still needs to reduce raw stop/kill/rm evidence into a passing proof before this can pass."
  ]
}
JSON
cat "$LATEST"
exit 2
REMOTE_RUNTIME_TEARDOWN
  run_adb push "$local_script" "$remote_script" >/dev/null
  rm -f "$local_script"
  run_adb shell chmod 755 "$remote_script" >/dev/null 2>&1 || true
  run_as "sh $remote_script $(remote_quote "$target")"
}

run_adb() {
  "$ADB" "$@"
}

remote_quote() {
  printf "'%s'" "$(printf "%s" "$1" | sed "s/'/'\\\\''/g")"
}

run_as() {
  run_adb shell "run-as $PKG sh -c $(remote_quote "$1")"
}

stage_test_cli() {
  [[ "${PDOCKER_STAGE_TEST_CLI:-1}" != "0" ]] || return 0
  local docker_bin="$ROOT/docker-proot-setup/docker-bin/docker"
  local compose_bin="$ROOT/vendor/lib/docker-compose"
  if [[ ! -x "$docker_bin" || ! -x "$compose_bin" ]]; then
    echo "test Docker CLI/Compose binaries missing; run backend fetch/build first" >&2
    return 1
  fi
  echo "[pdocker smoke] staging test-only Docker CLI/Compose outside APK"
  run_adb push "$docker_bin" /data/local/tmp/pdocker-test-docker >/dev/null
  run_adb push "$compose_bin" /data/local/tmp/pdocker-test-docker-compose >/dev/null
  run_as "mkdir -p files/pdocker-runtime/docker-bin/cli-plugins && cp /data/local/tmp/pdocker-test-docker files/pdocker-runtime/docker-bin/docker && cp /data/local/tmp/pdocker-test-docker-compose files/pdocker-runtime/docker-bin/cli-plugins/docker-compose && chmod 755 files/pdocker-runtime/docker-bin/docker files/pdocker-runtime/docker-bin/cli-plugins/docker-compose"
}

cleanup_test_cli() {
  [[ "${PDOCKER_STAGE_TEST_CLI:-1}" != "0" ]] || return 0
  [[ "${PDOCKER_KEEP_TEST_CLI:-0}" != "1" ]] || return 0
  run_as "rm -f files/pdocker-runtime/docker-bin/docker files/pdocker-runtime/docker-bin/cli-plugins/docker-compose" >/dev/null 2>&1 || true
}

wait_for_socket() {
  local i
  for i in $(seq 1 45); do
    if run_as 'test -S files/pdocker/pdockerd.sock' >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  echo "pdockerd socket did not appear" >&2
  return 1
}

docker_cmd() {
  local cmd="$1"
  run_as "cd files && export PATH=\"\$PWD/pdocker-runtime/docker-bin:\$PATH\" DOCKER_CONFIG=\"\$PWD/pdocker-runtime/docker-bin\" DOCKER_HOST=\"unix://\$PWD/pdocker/pdockerd.sock\" DOCKER_BUILDKIT=0 COMPOSE_DOCKER_CLI_BUILD=0 BUILDKIT_PROGRESS=plain COMPOSE_PROGRESS=plain COMPOSE_MENU=false PDOCKER_DIRECT_TRACE_MODE=seccomp && $cmd"
}

engine_exec_it_smoke() {
  local container_ref="$1"
  run_as "cd files/pdocker || exit 1
create_body='{\"AttachStdin\":true,\"AttachStdout\":true,\"AttachStderr\":true,\"Tty\":true,\"Env\":[\"ENV=\",\"BASH_ENV=\"],\"Cmd\":[\"/bin/sh\",\"-i\"]}'
create_len=\${#create_body}
create_resp=\$({ printf 'POST /containers/$container_ref/exec HTTP/1.1\r\nHost: docker\r\nContent-Type: application/json\r\nContent-Length: %s\r\nConnection: close\r\n\r\n' \"\$create_len\"; printf '%s' \"\$create_body\"; } | nc -U -W 5 pdockerd.sock | tr -d '\r')
exec_id=\$(printf '%s\n' \"\$create_resp\" | sed -n 's/.*\"Id\": \"\([0-9a-f]*\)\".*/\1/p')
test -n \"\$exec_id\"
start_body='{\"Detach\":false,\"Tty\":true}'
start_len=\${#start_body}
exec_out=\$({ printf 'POST /exec/%s/start HTTP/1.1\r\nHost: docker\r\nConnection: Upgrade\r\nUpgrade: tcp\r\nContent-Type: application/json\r\nContent-Length: %s\r\n\r\n' \"\$exec_id\" \"\$start_len\"; printf '%s' \"\$start_body\"; sleep 1; printf '[ \"x\" = \"x\" ]; /usr/bin/[ \"x\" = \"x\" ]; echo pdocker-it-bracket-ok; echo pdocker-it-ok; pwd; exit\r'; } | nc -U -W 10 pdockerd.sock | tr -d '\r')
printf '%s\n' \"\$exec_out\"
printf '%s\n' \"\$exec_out\" | grep -q 'pdocker-it-bracket-ok'
printf '%s\n' \"\$exec_out\" | grep -q 'pdocker-it-ok'
! printf '%s\n' \"\$exec_out\" | grep -Eq '(/usr/bin/)?\[: extra argument'
! printf '%s\n' \"\$exec_out\" | grep -q 'exit\\\\r'
"
}

smoke_artifact_dir() {
  if [[ -z "$SMOKE_ARTIFACT_DIR_RESOLVED" ]]; then
    if [[ -n "${PDOCKER_SMOKE_ARTIFACT_DIR:-}" ]]; then
      SMOKE_ARTIFACT_DIR_RESOLVED="$PDOCKER_SMOKE_ARTIFACT_DIR"
    else
      SMOKE_ARTIFACT_DIR_RESOLVED="$ROOT/tmp/device-smoke-artifacts/$(date -u +%Y%m%dT%H%M%SZ)"
    fi
  fi
  printf '%s' "$SMOKE_ARTIFACT_DIR_RESOLVED"
}

collect_device_file() {
  local device_path="$1"
  local host_name="$2"
  local dest_dir
  dest_dir="$(smoke_artifact_dir)"
  mkdir -p "$dest_dir"
  if run_adb exec-out run-as "$PKG" cat "$device_path" >"$dest_dir/$host_name" 2>"$dest_dir/$host_name.err"; then
    rm -f "$dest_dir/$host_name.err"
    echo "[pdocker smoke] collected $device_path -> $dest_dir/$host_name"
  else
    echo "[pdocker smoke] could not collect $device_path; see $dest_dir/$host_name.err" >&2
    return 1
  fi
}

collect_ui_it_selftest_artifacts() {
  collect_device_file "files/pdocker/diagnostics/ui-it-selftest-latest.json" "ui-it-selftest-latest.json" || true
  collect_device_file "files/pdocker/diagnostics/engine-exec-input-latest.jsonl" "engine-exec-input-latest.jsonl" || true
}

json_escape_host() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

write_ui_it_selftest_skip_artifact() {
  local reason="$1"
  local hard_gate_required="${2:-0}"
  local tmp now escaped_reason hard_gate_json
  tmp="$(mktemp)"
  now="$(date +%s000 2>/dev/null || printf '0')"
  escaped_reason="$(json_escape_host "$reason")"
  if [[ "$hard_gate_required" == "1" ]]; then
    hard_gate_json=true
  else
    hard_gate_json=false
  fi
  cat >"$tmp" <<JSON
{
  "Name": "ui-engine-exec-it",
  "Status": "planned-skip",
  "Success": false,
  "Reason": "$escaped_reason",
  "DeviceProofAttempted": false,
  "RequiredContainerId": true,
  "HardGateRequired": $hard_gate_json,
  "Evidence": {
    "Enter": false,
    "CtrlC": false,
    "ArrowHistory": false,
    "ImeEnterCtrlC": false,
    "Top": false,
    "TopRefresh": false,
    "TopRepaint": false,
    "TopQuit": false,
    "Resize": false
  },
  "RequiredEvidence": [
    "enter-single-submit",
    "ctrl-c-interrupts-without-literal-c",
    "arrow-up-reaches-readline-history",
    "ime-enter-ctrlc-regression-covered",
    "top-starts-on-tty",
    "top-refresh-observed-before-q",
    "top-repaint-remains-terminal-shaped",
    "q-quits-top",
    "resize-route-is-observable"
  ],
  "Contract": "ACTION_PREFIX.action.SMOKE_UI_IT_SELFTEST must only pass after a real requested/running Engine container is exercised; skip artifacts are non-success and must not be treated as fake success.",
  "StartedAtMs": $now,
  "CompletedAtMs": $now
}
JSON
  run_adb push "$tmp" /data/local/tmp/pdocker-ui-it-selftest-skip.json >/dev/null
  rm -f "$tmp"
  run_as "mkdir -p files/pdocker/diagnostics && cp /data/local/tmp/pdocker-ui-it-selftest-skip.json files/pdocker/diagnostics/ui-it-selftest-latest.json" >/dev/null
  collect_ui_it_selftest_artifacts
}

validate_ui_it_selftest_artifact() {
  local require_container="$1"
  local dest_dir
  dest_dir="$(smoke_artifact_dir)"
  python3 - "$dest_dir/ui-it-selftest-latest.json" "$dest_dir/engine-exec-input-latest.jsonl" "$require_container" <<'PY'
import json
import pathlib
import sys

artifact_path = pathlib.Path(sys.argv[1])
input_path = pathlib.Path(sys.argv[2])
require_container = sys.argv[3] == "1"

if not artifact_path.is_file():
    raise SystemExit(f"missing UI exec-it artifact: {artifact_path}")

artifact = json.loads(artifact_path.read_text())
status = artifact.get("Status", "")
success = artifact.get("Success") is True

if status == "planned-skip":
    if success:
        raise SystemExit("UI exec-it planned-skip must never report Success=true")
    if artifact.get("DeviceProofAttempted") is True:
        raise SystemExit("UI exec-it planned-skip must not claim device proof was attempted")
    if require_container:
        raise SystemExit("UI exec-it hard gate requires a real container; planned-skip is not a pass")
    raise SystemExit(0)

if success and status == "planned-skip":
    raise SystemExit("UI exec-it planned-skip must never be accepted as success")

if require_container and not artifact.get("Container"):
    raise SystemExit("UI exec-it hard gate artifact is missing Container")

if not success:
    raise SystemExit(f"UI exec-it self-test failed: {artifact.get('Error', artifact)}")

tail = artifact.get("OutputTail", "")
diagnostics = ""
if input_path.is_file():
    diagnostics = input_path.read_text(errors="replace")
diagnostics += "\n" + artifact.get("EngineExecDiagnostics", "")

top_refresh_markers = ("PID", "Tasks:", "Task:", "Mem:", "CPU:", "load average", "Load Avg")
required_names = [
    "enter-single-submit",
    "ctrl-c-interrupts-without-literal-c",
    "arrow-up-reaches-readline-history",
    "ime-enter-ctrlc-regression-covered",
    "top-starts-on-tty",
    "top-refresh-observed-before-q",
    "top-repaint-remains-terminal-shaped",
    "q-quits-top",
    "resize-route-is-observable",
]
artifact_required = set(artifact.get("RequiredEvidence") or [])
artifact_evidence = artifact.get("Evidence") or {}
checks = {
    "enter-single-submit": "pdocker-ui-it-ok" in tail,
    "ctrl-c-interrupts-without-literal-c": "pdocker-ui-it-ctrlc-ok" in tail and "sleep 15c" not in tail,
    "arrow-up-reaches-readline-history": tail.count("pdocker-ui-it-arrow-seed") >= 2 and "\\e[A" not in tail,
    "ime-enter-ctrlc-regression-covered": "pdocker-ui-it-ime-enter-ok" in tail and "pdocker-ui-it-ctrlc-ok" in tail and "sleep 15c" not in tail,
    "top-starts-on-tty": "pdocker-ui-it-top-ok" in tail,
    "top-refresh-observed-before-q": any(marker in tail for marker in top_refresh_markers),
    "top-repaint-remains-terminal-shaped": artifact_evidence.get("top-repaint-remains-terminal-shaped") is True,
    "q-quits-top": "pdocker-ui-it-topq-ok" in tail,
    # stream-started only proves the exec stream was opened; resize evidence must
    # show the Docker-compatible resize route or an explicit resize-failed event.
    "resize-route-is-observable": ("/resize?h=" in diagnostics) or ('"event":"resize-failed"' in diagnostics) or ('"event": "resize-failed"' in diagnostics),
}
missing_required = [name for name in required_names if name not in artifact_required]
if missing_required:
    raise SystemExit("UI exec-it artifact RequiredEvidence missing: " + ", ".join(missing_required))
missing_evidence_flags = [name for name in required_names if artifact_evidence.get(name) is not True]
if missing_evidence_flags:
    raise SystemExit("UI exec-it artifact Evidence flags not true: " + ", ".join(missing_evidence_flags))
missing = [name for name, ok in checks.items() if not ok]
if missing:
    raise SystemExit("UI exec-it evidence missing: " + ", ".join(missing))
PY
}

ui_engine_exec_it_selftest() {
  local container_ref="$1"
  local require_container="${2:-0}"
  if [[ -z "$container_ref" ]]; then
    echo "[pdocker smoke] ui self-test engine exec -it planned-skip: no container id"
    write_ui_it_selftest_skip_artifact "no container id was available for UI exec-it self-test" "$require_container"
    if ! validate_ui_it_selftest_artifact "$require_container"; then
      if [[ "$require_container" == "1" ]]; then
        echo "UI exec -it hard gate requires a real container; planned-skip is non-passing evidence" >&2
      fi
      return 1
    fi
    if [[ "$require_container" == "1" ]]; then
      echo "UI exec -it hard gate requires a real container; planned-skip is non-passing evidence" >&2
      return 1
    fi
    return 0
  fi
  echo "[pdocker smoke] ui self-test engine exec -it container=$container_ref"
  run_as "rm -f files/pdocker/diagnostics/ui-it-selftest-latest.json" >/dev/null 2>&1 || true
  run_adb shell am start \
    -n "$PKG/$CLASS_PREFIX.MainActivity" \
    -a "$ACTION_PREFIX.action.SMOKE_UI_IT_SELFTEST" \
    --es container "$container_ref" >/dev/null
  local i
  for i in $(seq 1 30); do
    if run_as "test -f files/pdocker/diagnostics/ui-it-selftest-latest.json"; then
      run_as "cat files/pdocker/diagnostics/ui-it-selftest-latest.json"
      collect_ui_it_selftest_artifacts
      run_as "grep -q '\"Success\": true' files/pdocker/diagnostics/ui-it-selftest-latest.json"
      validate_ui_it_selftest_artifact "$require_container"
      return 0
    fi
    sleep 1
  done
  echo "UI exec -it self-test did not produce diagnostics" >&2
  write_ui_it_selftest_skip_artifact "ACTION_SMOKE_UI_IT_SELFTEST did not produce diagnostics before timeout" "$require_container"
  return 1
}

run_gpu_bench() {
  local bench_dir="files/pdocker/bench"
  echo "[pdocker smoke] android-gpu-bench"
  run_as "rm -rf '$bench_dir' && mkdir -p '$bench_dir'" >/dev/null 2>&1 || true
  run_adb shell am broadcast \
    -n "$PKG/$CLASS_PREFIX.PdockerdDebugReceiver" \
    -a "$ACTION_PREFIX.action.SMOKE_GPU_BENCH" >/dev/null
  local i
  for i in $(seq 1 20); do
    if run_as "ls '$bench_dir'/android-gpu-bench-*.jsonl >/dev/null 2>&1 && ls '$bench_dir'/android-gpu-bench-*.csv >/dev/null 2>&1"; then
      run_as "tail -n 7 '$bench_dir'/android-gpu-bench-*.jsonl"
      return 0
    fi
    sleep 1
  done
  echo "android-gpu-bench artifacts did not appear in $bench_dir" >&2
  return 1
}

run_gpu_executor_bench() {
  echo "[pdocker smoke] gpu executor same-api probes"
  run_as 'files/pdocker-runtime/gpu/pdocker-gpu-executor --bench-vulkan-vector-add 1' | tee /tmp/pdocker-smoke-vulkan-executor.json
  grep -q '"backend_impl":"android_vulkan"' /tmp/pdocker-smoke-vulkan-executor.json
  grep -q '"valid":true' /tmp/pdocker-smoke-vulkan-executor.json
  run_as 'files/pdocker-runtime/gpu/pdocker-gpu-executor --bench-opencl-vector-add 1 || true' | tee /tmp/pdocker-smoke-opencl-executor.json
}

docker_run_rm_smoke() {
  echo "[pdocker smoke] docker run --rm ubuntu:22.04 echo hi"
  docker_cmd 'mkdir -p pdocker/diagnostics
run_log=pdocker/diagnostics/docker-run-rm-ubuntu-echo-hi.log
run_json=pdocker/diagnostics/docker-run-rm-ubuntu-echo-hi.json
rm -f "$run_log" "$run_json"
set +e
docker run --rm ubuntu:22.04 echo hi >"$run_log" 2>&1
status=$?
cat "$run_log"
if grep -qx hi "$run_log"; then saw_hi=true; else saw_hi=false; fi
printf "{\"Command\":\"docker run --rm ubuntu:22.04 echo hi\",\"ExitCode\":%s,\"SawHi\":%s,\"Log\":\"files/%s\"}\n" "$status" "$saw_hi" "$run_log" > "$run_json"
if [ "$status" -ne 0 ] || [ "$saw_hi" != true ]; then
  echo "docker run --rm ubuntu:22.04 echo hi failed; diagnostics: files/$run_log and files/$run_json" >&2
  exit 1
fi'
}

echo "[pdocker smoke] device: $(run_adb get-serialno)"

if [[ "$INSTALL" -eq 1 ]]; then
  if [[ ! -f "$APK" ]]; then
    echo "APK not found: $APK" >&2
    echo "Run ./gradlew :app:assembleDebug first." >&2
    exit 1
  fi
  echo "[pdocker smoke] installing $APK"
  run_adb install -r "$APK" >/dev/null
fi

trap 'cleanup_test_cli || true' EXIT

if [[ "${PDOCKER_SMOKE_FORCE_STOP:-0}" == "1" ]]; then
  echo "[pdocker smoke] force-stopping app; running containers will stop"
  run_adb shell am force-stop "$PKG" >/dev/null 2>&1 || true
  run_as 'rm -f files/pdocker/pdockerd.sock' >/dev/null 2>&1 || true
fi
run_adb shell pm grant "$PKG" android.permission.POST_NOTIFICATIONS >/dev/null 2>&1 || true
run_adb shell am start \
  -n "$PKG/$CLASS_PREFIX.MainActivity" \
  -a "$ACTION_PREFIX.action.SMOKE_START" >/dev/null

wait_for_socket
stage_test_cli

if [[ -n "$SERVICE_TRUTH_TARGET" ]]; then
  service_truth_acceptance_entrypoint "$SERVICE_TRUTH_TARGET"
fi
if [[ -n "$RUNTIME_TEARDOWN_TARGET" ]]; then
  runtime_teardown_acceptance_entrypoint "$RUNTIME_TEARDOWN_TARGET"
fi

echo "[pdocker smoke] docker version"
docker_cmd 'docker version'

echo "[pdocker smoke] direct executor probe"
run_as 'files/pdocker-runtime/docker-bin/pdocker-direct --pdocker-direct-probe | grep -q "pdocker-direct-executor:1"'
run_as 'files/pdocker-runtime/docker-bin/pdocker-direct --pdocker-direct-probe | grep -Eq "process-exec=(0|1)"'
if [[ "$FLAVOR" == "compat" ]]; then
  echo "[pdocker smoke] compat direct process probe"
  run_as 'PDOCKER_DIRECT_EXPERIMENTAL_PROCESS_EXEC=1 files/pdocker-runtime/docker-bin/pdocker-direct --pdocker-direct-probe | grep -q "process-exec=1"'
  echo "[pdocker smoke] compat memory pager syscall probe"
  run_as 'files/pdocker-runtime/docker-bin/pdocker-direct --pdocker-memory-pager-probe | grep -q "pager-probe:ptrace_path=ok"'
  echo "[pdocker smoke] compat memory pager poc"
  run_as 'files/pdocker-runtime/docker-bin/pdocker-direct --pdocker-memory-pager-poc | grep -q "pager-poc:result=ok"'
  echo "[pdocker smoke] compat managed anonymous pager poc"
  run_as 'APP_DATA=$(pwd); cd "$APP_DATA" && mkdir -p files/pdocker/tmp cache && TMPDIR="$APP_DATA/files/pdocker/tmp" files/pdocker-runtime/docker-bin/pdocker-direct --pdocker-memory-pager-managed-poc | grep -q "pager-managed-poc:result=ok"'
  echo "[pdocker smoke] compat transparent managed pager poc"
  run_as 'APP_DATA=$(pwd); cd "$APP_DATA" && mkdir -p files/pdocker/tmp cache && TMPDIR="$APP_DATA/files/pdocker/tmp" files/pdocker-runtime/docker-bin/pdocker-direct --pdocker-memory-pager-transparent-poc | grep -q "pager-transparent-poc:result=ok"'
  run_as '! test -e files/pdocker-runtime/docker-bin/proot'
else
  run_as 'files/pdocker-runtime/docker-bin/pdocker-direct --pdocker-direct-probe | grep -q "process-exec=0"'
fi

if [[ "$GPU_BENCH" -eq 1 ]]; then
  run_gpu_executor_bench
  run_gpu_bench
fi

if [[ -n "${PDOCKER_UI_IT_SELFTEST_CONTAINER:-}" ]]; then
  ui_engine_exec_it_selftest "$PDOCKER_UI_IT_SELFTEST_CONTAINER" "${PDOCKER_UI_IT_SELFTEST_REQUIRE_CONTAINER:-1}"
elif [[ "$MODE" == "quick" ]]; then
  ui_engine_exec_it_selftest "" "${PDOCKER_UI_IT_SELFTEST_REQUIRE_CONTAINER:-0}"
fi

if [[ "$MODE" == "quick" ]]; then
  echo "[pdocker smoke] quick mode passed"
  exit 0
fi

echo "[pdocker smoke] creating tiny project"
TMP_PROJECT="$(mktemp -d)"
trap 'rm -rf "$TMP_PROJECT"; cleanup_test_cli || true' EXIT
cat > "$TMP_PROJECT/Dockerfile" <<'EOF'
FROM ubuntu:22.04
RUN printf 'pdocker-smoke-build\n' > /pdocker-smoke.txt
CMD ["/bin/sh", "-lc", "cat /pdocker-smoke.txt && sleep 300"]
EOF
cat > "$TMP_PROJECT/compose.yaml" <<'EOF'
services:
  app:
    build: .
    command: ["/bin/sh", "-lc", "cat /pdocker-smoke.txt && sleep 300"]
EOF
REMOTE_PROJECT="/data/local/tmp/pdocker-$PROJECT"
run_adb shell "rm -rf '$REMOTE_PROJECT' && mkdir -p '$REMOTE_PROJECT'"
run_adb push "$TMP_PROJECT/." "$REMOTE_PROJECT/" >/dev/null
run_as "rm -rf files/pdocker/projects/$PROJECT && mkdir -p files/pdocker/projects/$PROJECT && cp -R $REMOTE_PROJECT/. files/pdocker/projects/$PROJECT/"

echo "[pdocker smoke] docker build"
docker_cmd "cd pdocker/projects/$PROJECT && docker build -t local/pdocker-device-smoke:latest ."
docker_run_rm_smoke

echo "[pdocker smoke] compose up/down"
docker_cmd "cd pdocker/projects/$PROJECT && docker compose down >/dev/null 2>&1 || true && docker rm -f device-smoke-app-1 >/dev/null 2>&1 || true && docker compose up --detach --build && CID=\$(docker compose ps -q app) && test -n \"\$CID\" && printf '%s' \"\$CID\" > .smoke-cid && for i in \$(seq 1 10); do docker compose logs --tail=80 | grep -q pdocker-smoke-build && break; sleep 1; done && docker compose logs --tail=80 | grep -q pdocker-smoke-build && EXEC_OUT=\$(docker exec \"\$CID\" sh -lc 'echo pdocker-exec-ok' 2>&1) && echo \"\$EXEC_OUT\" && echo \"\$EXEC_OUT\" | grep -q pdocker-exec-ok && ! echo \"\$EXEC_OUT\" | grep -q '/vendor/xbin' && BRACKET_OUT=\$(docker exec \"\$CID\" sh -lc '[ \"x\" = \"x\" ]; /usr/bin/[ \"x\" = \"x\" ]; echo pdocker-bracket-ok' 2>&1) && echo \"\$BRACKET_OUT\" && echo \"\$BRACKET_OUT\" | grep -q pdocker-bracket-ok && LONG_ARGV_OUT=\$(docker exec \"\$CID\" sh -lc 'long=\"\"; i=0; while [ \$i -lt 320 ]; do long=\"\$long flash_attn.comp.cpp.o\"; i=\$((i+1)); done; test \${#long} -gt 4096; /bin/sh -lc '\\''case \"\$1\" in *flash_attn.comp.cpp.o*flash_attn.comp.cpp.o*) echo pdocker-long-argv-ok ;; *) echo \"bad long argv: \$1\"; exit 7 ;; esac'\\'' sh \"\$long\"' 2>&1) && echo \"\$LONG_ARGV_OUT\" && echo \"\$LONG_ARGV_OUT\" | grep -q pdocker-long-argv-ok && docker compose ps -a"
CID="$(run_as "cat files/pdocker/projects/$PROJECT/.smoke-cid" | tr -d '\r')"
echo "[pdocker smoke] docker ps filters"
docker_cmd "FILTERED=\$(docker ps -a --filter name=device-smoke-app-1 -q) && test -n \"\$FILTERED\" && case \"$CID\" in \"\$FILTERED\"*) true ;; *) case \"\$FILTERED\" in \"$CID\"*) true ;; *) echo \"filter mismatch expected=$CID actual=\$FILTERED\"; false ;; esac ;; esac && test -z \"\$(docker ps -a --filter name=pdocker-smoke-filter-miss -q)\""
echo "[pdocker smoke] engine exec -it"
engine_exec_it_smoke "$CID"
ui_engine_exec_it_selftest "$CID" 1
docker_cmd "cd pdocker/projects/$PROJECT && docker compose down"

echo "[pdocker smoke] checking UI-visible job state path"
run_as 'ls -l files/pdocker/jobs.json >/dev/null 2>&1 || true; ls -ld files/pdocker/projects/device-smoke files/pdocker-runtime'

echo "[pdocker smoke] passed"

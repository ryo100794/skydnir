#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FLAVOR="${SKYDNIR_ANDROID_FLAVOR:-${PDOCKER_ANDROID_FLAVOR:-compat}}"
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
    echo "SKYDNIR_ANDROID_FLAVOR/PDOCKER_ANDROID_FLAVOR must be 'compat' or 'modern' (got '$FLAVOR')" >&2
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
DOCKER_CP_E2E_TARGET=""
SINGLE_CONTAINER_ECHO_HI=0
SMOKE_ARTIFACT_DIR_RESOLVED="${PDOCKER_SMOKE_ARTIFACT_DIR:-$ROOT/tmp/device-smoke-artifacts/$(date -u +%Y%m%dT%H%M%SZ)}"

usage() {
  cat <<EOF
Usage: $0 [--quick] [--gpu-bench] [--no-install]
       $0 --service-truth <default-workspace|llama>
       $0 --runtime-teardown <default-workspace|llama>
       $0 --docker-cp-e2e <default-workspace|llama>
       $0 --single-container-echo-hi

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
  --docker-cp-e2e TARGET
                planned device gate for Docker CLI `docker cp` end-to-end
                parity. Currently exits nonzero with a structured artifact.
  --single-container-echo-hi
                run only the focused `docker run --rm ubuntu:22.04 echo hi`
                gate after daemon startup and collect its diagnostic JSON.
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
    --docker-cp-e2e)
      [[ $# -ge 2 ]] || { echo "--docker-cp-e2e requires a target" >&2; exit 2; }
      DOCKER_CP_E2E_TARGET="$2"
      shift
      ;;
    --single-container-echo-hi)
      SINGLE_CONTAINER_ECHO_HI=1
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

UI_CARD_SAME_CONTAINER_ID=false
DOCKER_PS_SAME_CONTAINER_ID=false
ENGINE_API_CONTAINERS_JSON_SAME_CONTAINER_ID=false
PERSISTED_STATE_JSON_SAME_CONTAINER_ID=false
PROCESS_TABLE_SAME_CONTAINER_ID=false
LISTENER_OWNER_SAME_CONTAINER_ID=false
CONTAINER_LOGS_SAME_CONTAINER_ID=false
[ "$SELECTED_ID_EXACT" = true ] && [ "$UI_CARD_PROVEN" = true ] && [ "$UI_CARD_CID" = "$SELECTED_ENGINE_CID" ] && UI_CARD_SAME_CONTAINER_ID=true
[ "$SELECTED_ID_EXACT" = true ] && [ "$DOCKER_PS_PROVEN" = true ] && DOCKER_PS_SAME_CONTAINER_ID=true
[ "$SELECTED_ID_EXACT" = true ] && [ "$ENGINE_API_PROVEN" = true ] && ENGINE_API_CONTAINERS_JSON_SAME_CONTAINER_ID=true
[ "$SELECTED_ID_EXACT" = true ] && [ "$STATE_MATCH" = true ] && PERSISTED_STATE_JSON_SAME_CONTAINER_ID=true
[ "$SELECTED_ID_EXACT" = true ] && [ "$PROCESS_PROVEN" = true ] && PROCESS_TABLE_SAME_CONTAINER_ID=true
[ "$SELECTED_ID_EXACT" = true ] && [ "$LISTENER_PROVEN" = true ] && [ "$LISTENER_OWNER_ENGINE_CID" = "$SELECTED_ENGINE_CID" ] && LISTENER_OWNER_SAME_CONTAINER_ID=true
[ "$SELECTED_ID_EXACT" = true ] && [ "$LOGS_PROVEN" = true ] && [ "$LOGS_MARKER_ENGINE_CID" = "$SELECTED_ENGINE_CID" ] && CONTAINER_LOGS_SAME_CONTAINER_ID=true

REDUCTION_MISSING_SOURCES=""
REDUCTION_MISMATCHED_SOURCES="$MISMATCHED_SOURCES"
[ "$UI_CARD_SAME_CONTAINER_ID" = true ] || REDUCTION_MISSING_SOURCES="$REDUCTION_MISSING_SOURCES UICard"
[ "$DOCKER_PS_SAME_CONTAINER_ID" = true ] || REDUCTION_MISSING_SOURCES="$REDUCTION_MISSING_SOURCES DockerPs"
[ "$ENGINE_API_CONTAINERS_JSON_SAME_CONTAINER_ID" = true ] || REDUCTION_MISSING_SOURCES="$REDUCTION_MISSING_SOURCES EngineApiContainersJson"
[ "$PERSISTED_STATE_JSON_SAME_CONTAINER_ID" = true ] || REDUCTION_MISSING_SOURCES="$REDUCTION_MISSING_SOURCES PersistedStateJson"
[ "$PROCESS_TABLE_SAME_CONTAINER_ID" = true ] || REDUCTION_MISSING_SOURCES="$REDUCTION_MISSING_SOURCES ProcessTable"
[ "$LISTENER_OWNER_SAME_CONTAINER_ID" = true ] || REDUCTION_MISSING_SOURCES="$REDUCTION_MISSING_SOURCES ListenerProbe"
[ "$CONTAINER_LOGS_SAME_CONTAINER_ID" = true ] || REDUCTION_MISSING_SOURCES="$REDUCTION_MISSING_SOURCES ContainerLogs"

SAME_ENGINE_CONTAINER_ID=false
SERVICE_TRUTH_STATUS="planned-gap"
SERVICE_TRUTH_SUCCESS=false
SERVICE_TRUTH_EXIT=2
if [ "$SELECTED_ID_EXACT" = true ]   && [ "$UI_CARD_SAME_CONTAINER_ID" = true ]   && [ "$DOCKER_PS_SAME_CONTAINER_ID" = true ]   && [ "$ENGINE_API_CONTAINERS_JSON_SAME_CONTAINER_ID" = true ]   && [ "$PERSISTED_STATE_JSON_SAME_CONTAINER_ID" = true ]   && [ "$PROCESS_TABLE_SAME_CONTAINER_ID" = true ]   && [ "$LISTENER_OWNER_SAME_CONTAINER_ID" = true ]   && [ "$CONTAINER_LOGS_SAME_CONTAINER_ID" = true ]   && [ -z "$(printf '%s' "$REDUCTION_MISMATCHED_SOURCES" | tr -d ' ')" ]   && [ -z "$(printf '%s' "$REDUCTION_MISSING_SOURCES" | tr -d ' ')" ]; then
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
  "VerifierReduction": {
    "ReducedEngineContainerId": $( [ "$SELECTED_ID_EXACT" = true ] && json_string "$SELECTED_ENGINE_CID" || printf null ),
    "RequiredSources": ["UICard", "DockerPs", "EngineApiContainersJson", "PersistedStateJson", "ProcessTable", "ListenerProbe", "ContainerLogs"],
    "SourceContainerIds": {
      "UICard": $(is_engine_container_id "$UI_CARD_CID" && json_string "$UI_CARD_CID" || printf null),
      "DockerPs": $( [ "$DOCKER_PS_SAME_CONTAINER_ID" = true ] && json_string "$SELECTED_ENGINE_CID" || printf null ),
      "EngineApiContainersJson": $( [ "$ENGINE_API_CONTAINERS_JSON_SAME_CONTAINER_ID" = true ] && json_string "$SELECTED_ENGINE_CID" || printf null ),
      "PersistedStateJson": $( [ "$PERSISTED_STATE_JSON_SAME_CONTAINER_ID" = true ] && json_string "$SELECTED_ENGINE_CID" || printf null ),
      "ProcessTable": $( [ "$PROCESS_TABLE_SAME_CONTAINER_ID" = true ] && json_string "$SELECTED_ENGINE_CID" || printf null ),
      "ListenerProbe": $( [ "$LISTENER_OWNER_SAME_CONTAINER_ID" = true ] && json_string "$SELECTED_ENGINE_CID" || printf null ),
      "ContainerLogs": $( [ "$CONTAINER_LOGS_SAME_CONTAINER_ID" = true ] && json_string "$SELECTED_ENGINE_CID" || printf null )
    },
    "UICardSameContainerId": $(json_bool "$UI_CARD_SAME_CONTAINER_ID"),
    "DockerPsSameContainerId": $(json_bool "$DOCKER_PS_SAME_CONTAINER_ID"),
    "EngineApiContainersJsonSameContainerId": $(json_bool "$ENGINE_API_CONTAINERS_JSON_SAME_CONTAINER_ID"),
    "PersistedStateJsonSameContainerId": $(json_bool "$PERSISTED_STATE_JSON_SAME_CONTAINER_ID"),
    "ProcessTableSameContainerId": $(json_bool "$PROCESS_TABLE_SAME_CONTAINER_ID"),
    "ListenerOwnerSameContainerId": $(json_bool "$LISTENER_OWNER_SAME_CONTAINER_ID"),
    "ContainerLogsSameContainerId": $(json_bool "$CONTAINER_LOGS_SAME_CONTAINER_ID"),
    "MismatchedSources": $(json_word_array "$REDUCTION_MISMATCHED_SOURCES"),
    "MissingSources": $(json_word_array "$REDUCTION_MISSING_SOURCES")
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
  set +e
  run_as "sh $remote_script $(remote_quote "$target")"
  local rc=$?
  set -e
  collect_device_file "files/pdocker/diagnostics/service-truth-latest.json" "service-truth-latest.json" || true
  return "$rc"
}
docker_cp_e2e_acceptance_entrypoint() {
  local target="$1"
  local remote_script="/data/local/tmp/pdocker-docker-cp-e2e-smoke.sh"
  local local_script
  local_script="$(mktemp)"
  cat > "$local_script" <<'REMOTE_DOCKER_CP_E2E'
#!/system/bin/sh
set +e
TARGET="${1:-default-workspace}"
cd files || exit 1
DIAG="pdocker/diagnostics/docker-cp-e2e"
LATEST="pdocker/diagnostics/docker-cp-e2e-latest.json"
rm -rf "$DIAG"; mkdir -p "$DIAG"
write_negative_case() { cat >"$DIAG/$1.json" <<JSON
{"Kind":"docker-cp-e2e-negative-case","Status":"planned-gap","Success":false,"ExpectedAccepted":false,"RejectedSignal":"$2","Reason":"$3"}
JSON
}
write_negative_case negative-cli-exit-zero-only "CLI exit 0 alone" "same Engine container ID and full metadata proof required"
write_negative_case negative-container-name-only "container name only" "same Engine container ID required"
write_negative_case negative-bytes-only "payload bytes only" "metadata, links, and headers required"
write_negative_case negative-host-only "host-only archive helper pass" "adb run-as device evidence required"
write_negative_case negative-network-pull-required "network pull required" "local image/prestaged fixture required"
write_negative_case negative-terminal-required "terminal/TTY evidence" "non-interactive docker cp proof required"
cat > "$LATEST" <<JSON
{
  "Kind": "docker-cp-e2e",
  "Status": "planned-gap",
  "Success": false,
  "Target": "$TARGET",
  "DeviceGate": {"RequiresAdb": true, "CollectedViaAdbRunAs": true, "HostStaticVerifierCannotPromote": true, "DoNotClaimDevicePassWithoutAdb": true, "NoGpuRequired": true, "NoTerminalRequired": true, "NoNetworkRequired": true},
  "EvidencePlan": ["same Engine container ID", "docker cp host-to-container", "container-to-host", "HEAD /containers/{id}/archive", "GET /containers/{id}/archive", "PUT /containers/{id}/archive", "X-Docker-Container-Path-Stat", "byte and sha256 equality", "hardlink", "symlink no-follow policy", "mode, mtime, uid/gid policy", "user.* xattr", "reserved whiteout", "absolute symlink", "escaping hardlink"],
  "NegativeCases": ["negative-cli-exit-zero-only.json", "negative-container-name-only.json", "negative-bytes-only.json", "negative-host-only.json", "negative-network-pull-required.json", "negative-terminal-required.json"],
  "Unresolved": ["No device verifier has reduced docker cp CLI and archive HTTP evidence into a passing same-container-ID proof."]
}
JSON
cat "$LATEST"
exit 2
REMOTE_DOCKER_CP_E2E
  run_adb push "$local_script" "$remote_script" >/dev/null
  rm -f "$local_script"
  run_adb shell chmod 755 "$remote_script" >/dev/null 2>&1 || true
  set +e
  run_as "sh $remote_script $(remote_quote "$target")"
  local rc=$?
  set -e
  collect_device_file "files/pdocker/diagnostics/docker-cp-e2e-latest.json" "docker-cp-e2e-latest.json" || true
  return "$rc"
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

json_bool() {
  case "$1" in
    true) printf 'true' ;;
    *) printf 'false' ;;
  esac
}

is_exact_engine_container_id() {
  printf '%s' "$1" | grep -Eq '^[0-9a-f]{64}$'
}

artifact_contains() {
  needle="$1"
  artifact="$2"
  [ -n "$needle" ] && [ -f "$artifact" ] && grep -Fq "$needle" "$artifact" 2>/dev/null
}

json_id_field_equals() {
  cid="$1"
  artifact="$2"
  is_exact_engine_container_id "$cid" && [ -f "$artifact" ] \
    && grep -Eq '"Id"[[:space:]]*:[[:space:]]*"'$cid'"' "$artifact" 2>/dev/null
}

json_field_equals() {
  artifact="$1"
  field="$2"
  expected="$3"
  [ -f "$artifact" ] && grep -Eq '"'$field'"[[:space:]]*:[[:space:]]*"'$expected'"' "$artifact" 2>/dev/null
}

write_json_string_array_from_file() {
  artifact="$1"
  first=1
  printf '['
  if [ -f "$artifact" ]; then
    while IFS= read -r line; do
      [ -n "$line" ] || continue
      if [ "$first" = "1" ]; then
        first=0
      else
        printf ', '
      fi
      json_string "$line"
    done <"$artifact"
  fi
  printf ']'
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

inspect_running_from_http() {
  grep -Eq '"Running"[[:space:]]*:[[:space:]]*true' "$1" 2>/dev/null
}

process_snapshot_has_pid() {
  pid="$1"
  process_snapshot="$2"
  [ -n "$pid" ] && [ "$pid" != "0" ] && grep -Eq "(^|[[:space:]])$pid([[:space:]]|$)" "$process_snapshot" 2>/dev/null
}

json_bool_field_true() {
  artifact="$1"
  field="$2"
  [ -f "$artifact" ] && grep -Eq '"'$field'"[[:space:]]*:[[:space:]]*true' "$artifact" 2>/dev/null
}

listener_snapshot_mentions_pid() {
  pid="$1"
  artifact="$2"
  [ -n "$pid" ] && [ "$pid" != "0" ] && [ -f "$artifact" ] \
    && grep -Eq "(pid=|pid[[:space:]]+|[,/[:space:]])$pid([,)/[:space:]]|$)" "$artifact" 2>/dev/null
}

executor_residue_has_entries() {
  artifact="$1"
  [ -f "$artifact" ] && awk 'NR > 1 && NF { found=1 } END { exit found ? 0 : 1 }' "$artifact" 2>/dev/null
}

state_field_number_cleared() {
  artifact="$1"
  field="$2"
  [ -f "$artifact" ] && ! grep -Eq '"'$field'"[[:space:]]*:[[:space:]]*[1-9][0-9]*' "$artifact" 2>/dev/null
}

state_field_text_cleared() {
  artifact="$1"
  field="$2"
  [ -f "$artifact" ] && ! grep -Eq '"'$field'"[[:space:]]*:[[:space:]]*"[^"]+"' "$artifact" 2>/dev/null
}

state_field_array_cleared() {
  artifact="$1"
  field="$2"
  [ -f "$artifact" ] && ! grep -Eq '"'$field'"[[:space:]]*:[[:space:]]*\[[^]]*[0-9][^]]*\]' "$artifact" 2>/dev/null
}

state_teardown_no_orphans() {
  artifact="$1"
  [ -f "$artifact" ] && grep -Eq '"NoOrphanProcesses"[[:space:]]*:[[:space:]]*true' "$artifact" 2>/dev/null
}

state_teardown_survivors_empty() {
  artifact="$1"
  [ -f "$artifact" ] && grep -Eq '"Survivors"[[:space:]]*:[[:space:]]*\[[[:space:]]*\]' "$artifact" 2>/dev/null
}

write_live_identity_evidence() {
  label="$1"
  cid="$2"
  identity_inspect_http="$3"
  process_snapshot="$4"
  pid="$(inspect_pid_from_http "$identity_inspect_http")"
  running=false
  inspect_running_from_http "$identity_inspect_http" && running=true
  pid_present=false
  process_snapshot_has_pid "$pid" "$process_snapshot" && pid_present=true
  same_container=false
  is_exact_engine_container_id "$cid" && json_id_field_equals "$cid" "$identity_inspect_http" && same_container=true
  proven=false
  [ "$same_container" = true ] && [ "$running" = true ] && [ "$pid_present" = true ] && proven=true
  cat >"$DIAG/$label.json" <<JSON
{
  "SchemaVersion": 1,
  "Kind": "runtime-teardown-live-identity-proof",
  "ContainerId": $(json_string "$cid"),
  "IdentityInspectHttp": $(json_string "files/$identity_inspect_http"),
  "ProcessSnapshot": $(json_string "files/$process_snapshot"),
  "InspectBeforePid": $(json_string "$pid"),
  "LiveBeforeOperation": $(json_bool "$running"),
  "PidPresentBeforeOperation": $(json_bool "$pid_present"),
  "SameContainerId": $(json_bool "$same_container"),
  "Proven": $(json_bool "$proven"),
  "Contract": "Stale PID and direct-child absence must be anchored to the live pre-stop/pre-kill inspect identity for this exact Engine container ID; after-stop or after-rm State.Pid=0 is not enough."
}
JSON
}

write_pid_evidence() {
  label="$1"
  cid="$2"
  identity_inspect_http="$3"
  process_snapshot="$4"
  observed_inspect_http="${5:-$identity_inspect_http}"
  pid="$(inspect_pid_from_http "$identity_inspect_http")"
  identity_running=false
  inspect_running_from_http "$identity_inspect_http" && identity_running=true
  identity_same=false
  is_exact_engine_container_id "$cid" && json_id_field_equals "$cid" "$identity_inspect_http" && identity_same=true
  if [ -z "$pid" ] || [ "$pid" = "0" ]; then
    present="unknown"
  elif process_snapshot_has_pid "$pid" "$process_snapshot"; then
    present="true"
  else
    present="false"
  fi
  anchored=false
  [ "$identity_same" = true ] && [ "$identity_running" = true ] && [ -n "$pid" ] && [ "$pid" != "0" ] && anchored=true
  stale_absent=false
  [ "$anchored" = true ] && [ "$present" = false ] && stale_absent=true
  cat >"$DIAG/$label.json" <<JSON
{
  "SchemaVersion": 1,
  "Kind": "runtime-teardown-stale-pid-proof",
  "ContainerId": $(json_string "$cid"),
  "IdentityInspectHttp": $(json_string "files/$identity_inspect_http"),
  "ObservedInspectHttp": $(json_string "files/$observed_inspect_http"),
  "ProcessSnapshot": $(json_string "files/$process_snapshot"),
  "InspectPid": $(json_string "$pid"),
  "LivePreOperationPid": $(json_string "$pid"),
  "LivePreOperationIdentitySameContainerId": $(json_bool "$identity_same"),
  "LivePreOperationIdentityRunning": $(json_bool "$identity_running"),
  "AnchoredToLivePreOperationIdentity": $(json_bool "$anchored"),
  "PidStillPresentInSnapshot": $(json_string "$present"),
  "StalePidAbsence": $(json_bool "$stale_absent"),
  "Contract": "A stale PID proof must search the post-operation process table for the live pre-stop/pre-kill State.Pid of this exact Engine container ID; after-operation inspect PID clearing alone is not teardown proof."
}
JSON
}

write_process_tree_evidence() {
  label="$1"
  cid="$2"
  identity_inspect_http="$3"
  process_snapshot="$4"
  observed_inspect_http="${5:-$identity_inspect_http}"
  pid="$(inspect_pid_from_http "$identity_inspect_http")"
  identity_running=false
  inspect_running_from_http "$identity_inspect_http" && identity_running=true
  identity_same=false
  is_exact_engine_container_id "$cid" && json_id_field_equals "$cid" "$identity_inspect_http" && identity_same=true
  anchored=false
  [ "$identity_same" = true ] && [ "$identity_running" = true ] && [ -n "$pid" ] && [ "$pid" != "0" ] && anchored=true
  children="$DIAG/$label-direct-children.txt"
  : >"$children"
  if [ -n "$pid" ] && [ "$pid" != "0" ]; then
    awk -v p="$pid" 'NR > 1 && $2 == p {print}' "$process_snapshot" >"$children" 2>/dev/null || true
  fi
  if [ -s "$children" ]; then
    direct_children_present="true"
  elif [ "$anchored" = true ]; then
    direct_children_present="false"
  else
    direct_children_present="unknown"
  fi
  direct_child_absence=false
  [ "$anchored" = true ] && [ "$direct_children_present" = false ] && direct_child_absence=true
  cat >"$DIAG/$label.json" <<JSON
{
  "SchemaVersion": 1,
  "Kind": "runtime-teardown-process-tree-proof",
  "ContainerId": $(json_string "$cid"),
  "IdentityInspectHttp": $(json_string "files/$identity_inspect_http"),
  "ObservedInspectHttp": $(json_string "files/$observed_inspect_http"),
  "ProcessSnapshot": $(json_string "files/$process_snapshot"),
  "InspectPid": $(json_string "$pid"),
  "LivePreOperationPid": $(json_string "$pid"),
  "LivePreOperationIdentitySameContainerId": $(json_bool "$identity_same"),
  "LivePreOperationIdentityRunning": $(json_bool "$identity_running"),
  "AnchoredToLivePreOperationIdentity": $(json_bool "$anchored"),
  "DirectChildrenArtifact": $(json_string "files/$children"),
  "DirectChildrenPresent": $(json_string "$direct_children_present"),
  "DirectChildAbsence": $(json_bool "$direct_child_absence"),
  "RequiredForDevicePass": true,
  "Contract": "No device-pass may be claimed until the live pre-stop/pre-kill State.Pid for this exact Engine container ID has no direct children in the post-operation process table; HTTP 204, CLI exit 0, or after-operation State.Pid=0 is not direct-child proof."
}
JSON
}

write_name_residue_evidence() {
  label="$1"
  cid="$2"
  name="$3"
  containers_http="$4"
  matches="$DIAG/$label-name-matches.txt"
  grep -F "$name" "$containers_http" >"$matches" 2>/dev/null || true
  if [ -s "$matches" ]; then
    name_present="true"
  else
    name_present="false"
  fi
  cat >"$DIAG/$label.json" <<JSON
{
  "SchemaVersion": 1,
  "Kind": "runtime-teardown-stale-name-proof",
  "ContainerId": $(json_string "$cid"),
  "Name": $(json_string "$name"),
  "ContainersHttp": $(json_string "files/$containers_http"),
  "NameMatchesArtifact": $(json_string "files/$matches"),
  "NameStillPresentAfterRemove": $(json_string "$name_present"),
  "RequiredForDevicePass": true,
  "Contract": "A removed container name, duplicate name, or previous-container log may not be accepted unless the current Engine container ID is absent from /containers/json after rm and all evidence is bound to the same ID."
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
    stop-rm)
      lifecycle_op="stop"; lifecycle_rm="rm-stopped"
      live_identity="$DIAG/stop-live-identity-before-stop.json"
      direct_children_after_operation="$DIAG/stop-process-tree-after-stop.json"
      direct_children_after_rm="$DIAG/stop-process-tree-after-rm.json"
      stale_name_after_rm="$DIAG/stop-stale-name-after-rm.json"
      ;;
    kill-rm)
      lifecycle_op="kill"; lifecycle_rm="rm-killed"
      live_identity="$DIAG/kill-live-identity-before-kill.json"
      direct_children_after_operation="$DIAG/kill-process-tree-after-kill.json"
      direct_children_after_rm="$DIAG/kill-process-tree-after-rm.json"
      stale_name_after_rm="$DIAG/kill-stale-name-after-rm.json"
      ;;
    *)
      lifecycle_op="$operation"; lifecycle_rm="rm"
      live_identity=""
      direct_children_after_operation=""
      direct_children_after_rm=""
      stale_name_after_rm=""
      ;;
  esac
  containers_after="$DIAG/engine-containers-after.http"

  gap_reasons="$DIAG/$label-gap-reasons.txt"
  fail_reasons="$DIAG/$label-fail-reasons.txt"
  mismatches="$DIAG/$label-mismatched-container-ids.txt"
  survivors="$DIAG/$label-survivors.txt"
  : >"$gap_reasons"
  : >"$fail_reasons"
  : >"$mismatches"
  : >"$survivors"

  add_gap_reason() {
    printf '%s\n' "$1" >>"$gap_reasons"
  }
  add_fail_reason() {
    printf '%s\n' "$1" >>"$fail_reasons"
  }
  add_mismatch() {
    printf '%s\n' "$1" >>"$mismatches"
  }
  add_survivor() {
    printf '%s\n' "$1" >>"$survivors"
  }

  created_out_id="$(container_id_from_out "$create_out")"
  cid_exact=false
  create_output_same=false
  inspect_before_same=false
  inspect_after_same=false
  inspect_after_rm_gone=false
  engine_containers_after_absent=false
  direct_child_absence=false
  stale_pid_absence=false
  process_tree_clear=false
  live_identity_same_container_id=false
  stale_pid_anchored_to_live_identity=false
  direct_child_anchored_to_live_identity=false
  listener_absence=false
  listener_owner_same_container_id=false
  stale_name_absence=false
  residue_absence=false
  gpu_media_executor_residue_same_container_id=false
  persisted_state_cleared=false
  persisted_state_json_same_container_id=false
  state_pid_cleared=false
  pid_start_time_cleared=false
  pdocker_known_pids_cleared=false
  pdocker_launcher_pid_cleared=false
  pdocker_launcher_pid_start_time_cleared=false
  pdocker_launcher_pgid_cleared=false
  pdocker_process_group_id_cleared=false
  pdocker_teardown_no_orphan_processes=false
  pdocker_teardown_survivors_empty=false
  lifecycle_logs_bound=false
  lifecycle_logs_same_container_id=false
  container_logs_bound=false
  container_logs_same_container_id=false

  if is_exact_engine_container_id "$cid"; then
    cid_exact=true
  else
    add_gap_reason "container ID is not an exact 64-hex Engine ID; create/inspect reduction cannot promote"
  fi

  if [ "$cid_exact" = "true" ] && [ "$created_out_id" = "$cid" ]; then
    create_output_same=true
  else
    add_fail_reason "Engine create output is not the same exact container ID"
    add_mismatch "create_out=$(printf '%s' "$created_out_id") expected=$(printf '%s' "$cid")"
  fi

  if [ "$cid_exact" = "true" ] && json_id_field_equals "$cid" "$inspect_before"; then
    inspect_before_same=true
  else
    add_fail_reason "Engine inspect before operation is not bound to the same container ID"
    add_mismatch "inspect_before=$(printf '%s' "$inspect_before")"
  fi

  if [ "$cid_exact" = "true" ] && json_id_field_equals "$cid" "$inspect_after"; then
    inspect_after_same=true
  else
    add_fail_reason "Engine inspect after operation is not bound to the same container ID"
    add_mismatch "inspect_after_operation=$(printf '%s' "$inspect_after")"
  fi

  inspect_after_rm_status="${inspect_after_rm%.http}.status"
  if [ -f "$inspect_after_rm_status" ] && grep -Eq 'HTTP/[0-9.]+[[:space:]]+404' "$inspect_after_rm_status" 2>/dev/null; then
    inspect_after_rm_gone=true
  elif [ "$cid_exact" = "true" ] && artifact_contains "$cid" "$inspect_after_rm"; then
    add_fail_reason "Engine inspect after rm still returns the removed container ID"
    add_mismatch "inspect_after_rm=$(printf '%s' "$inspect_after_rm") still contains $(printf '%s' "$cid")"
  else
    add_gap_reason "Engine inspect after rm did not prove HTTP 404/non-existence for the same container ID"
  fi

  if [ "$cid_exact" = "true" ] && [ -f "$containers_after" ]; then
    if artifact_contains "$cid" "$containers_after"; then
      add_fail_reason "Engine /containers/json after rm still contains the removed container ID"
      add_mismatch "containers_after=$(printf '%s' "$containers_after") still contains $(printf '%s' "$cid")"
    else
      engine_containers_after_absent=true
    fi
  else
    engine_containers_after_absent=false
    add_gap_reason "Engine /containers/json after rm was missing or cannot be bound to an exact container ID"
  fi

  pre_operation_pid="$(inspect_pid_from_http "$inspect_before")"
  if json_bool_field_true "$live_identity" "Proven"; then
    live_identity_same_container_id=true
  else
    add_gap_reason "live pre-stop/pre-kill identity is missing or not bound to the same Engine container ID"
  fi

  if json_field_equals "$direct_children_after_operation" "DirectChildrenPresent" "true"; then
    add_fail_reason "direct children are still present after lifecycle operation"
    add_survivor "direct_children_after_operation=$(printf '%s' "$direct_children_after_operation")"
  elif ! json_field_equals "$direct_children_after_operation" "DirectChildrenPresent" "false"; then
    add_gap_reason "direct-child proof after lifecycle operation is unknown or missing"
  fi
  if json_field_equals "$direct_children_after_rm" "DirectChildrenPresent" "true"; then
    add_fail_reason "direct children are still present after rm"
    add_survivor "direct_children_after_rm=$(printf '%s' "$direct_children_after_rm")"
  elif ! json_field_equals "$direct_children_after_rm" "DirectChildrenPresent" "false"; then
    add_gap_reason "direct-child proof after rm is unknown or missing"
  fi
  if json_bool_field_true "$direct_children_after_operation" "AnchoredToLivePreOperationIdentity" \
    && json_bool_field_true "$direct_children_after_rm" "AnchoredToLivePreOperationIdentity"; then
    direct_child_anchored_to_live_identity=true
  else
    add_gap_reason "direct-child proof is not anchored to the live pre-stop/pre-kill identity"
  fi
  if json_bool_field_true "$direct_children_after_operation" "DirectChildAbsence" \
    && json_bool_field_true "$direct_children_after_rm" "DirectChildAbsence"; then
    direct_child_absence=true
    process_tree_clear=true
  fi

  if json_field_equals "$stale_pid_after_operation" "PidStillPresentInSnapshot" "true"; then
    add_fail_reason "stale live pre-operation PID is still present after lifecycle operation"
    add_survivor "stale_pid_after_operation=$(printf '%s' "$stale_pid_after_operation")"
  elif ! json_field_equals "$stale_pid_after_operation" "PidStillPresentInSnapshot" "false"; then
    add_gap_reason "stale PID proof after lifecycle operation is unknown or missing"
  fi
  if json_field_equals "$stale_pid_after_rm" "PidStillPresentInSnapshot" "true"; then
    add_fail_reason "stale live pre-operation PID is still present after rm"
    add_survivor "stale_pid_after_rm=$(printf '%s' "$stale_pid_after_rm")"
  elif ! json_field_equals "$stale_pid_after_rm" "PidStillPresentInSnapshot" "false"; then
    add_gap_reason "stale PID proof after rm is unknown or missing"
  fi
  if json_bool_field_true "$stale_pid_after_operation" "AnchoredToLivePreOperationIdentity" \
    && json_bool_field_true "$stale_pid_after_rm" "AnchoredToLivePreOperationIdentity"; then
    stale_pid_anchored_to_live_identity=true
  else
    add_gap_reason "stale PID proof is not anchored to the live pre-stop/pre-kill identity"
  fi
  if json_bool_field_true "$stale_pid_after_operation" "StalePidAbsence" \
    && json_bool_field_true "$stale_pid_after_rm" "StalePidAbsence"; then
    stale_pid_absence=true
  fi

  if [ "$live_identity_same_container_id" = true ] && [ -n "$pre_operation_pid" ] && [ "$pre_operation_pid" != "0" ] \
    && [ -f "$listener_after_operation" ] && [ -f "$listener_after_rm" ] \
    && ! listener_snapshot_mentions_pid "$pre_operation_pid" "$listener_after_operation" \
    && ! listener_snapshot_mentions_pid "$pre_operation_pid" "$listener_after_rm"; then
    listener_absence=true
    listener_owner_same_container_id=true
  else
    add_gap_reason "listener reducer did not prove absence for the live pre-operation PID and same Engine container ID"
  fi

  if json_field_equals "$stale_name_after_rm" "NameStillPresentAfterRemove" "true"; then
    add_fail_reason "container name is still present in /containers/json after rm"
    add_survivor "stale_name_after_rm=$(printf '%s' "$stale_name_after_rm")"
  elif json_field_equals "$stale_name_after_rm" "NameStillPresentAfterRemove" "false"; then
    stale_name_absence=true
  else
    add_gap_reason "stale-name proof after rm is unknown or missing"
  fi

  if [ -f "$residue_after_operation" ] && [ -f "$residue_after_rm" ] \
    && ! executor_residue_has_entries "$residue_after_operation" && ! executor_residue_has_entries "$residue_after_rm"; then
    residue_absence=true
    gpu_media_executor_residue_same_container_id=true
  else
    add_gap_reason "GPU/media executor residue reducer found entries or missing snapshots for the same Engine container ID"
    executor_residue_has_entries "$residue_after_operation" && add_survivor "gpu_media_executor_residue_after_operation=$(printf '%s' "$residue_after_operation")"
    executor_residue_has_entries "$residue_after_rm" && add_survivor "gpu_media_executor_residue_after_rm=$(printf '%s' "$residue_after_rm")"
  fi

  state_field_number_cleared "$state_after_operation" "Pid" && state_pid_cleared=true
  state_field_text_cleared "$state_after_operation" "PidStartTime" && pid_start_time_cleared=true
  state_field_array_cleared "$state_after_operation" "PdockerKnownPids" && pdocker_known_pids_cleared=true
  state_field_number_cleared "$state_after_operation" "PdockerLauncherPid" && pdocker_launcher_pid_cleared=true
  state_field_text_cleared "$state_after_operation" "PdockerLauncherPidStartTime" && pdocker_launcher_pid_start_time_cleared=true
  state_field_number_cleared "$state_after_operation" "PdockerLauncherPgid" && pdocker_launcher_pgid_cleared=true
  state_field_number_cleared "$state_after_operation" "PdockerProcessGroupId" && pdocker_process_group_id_cleared=true
  state_teardown_no_orphans "$state_after_operation" && pdocker_teardown_no_orphan_processes=true
  state_teardown_survivors_empty "$state_after_operation" && pdocker_teardown_survivors_empty=true
  if [ "$cid_exact" = true ] && artifact_contains "$cid" "$state_after_operation" \
    && [ "$state_pid_cleared" = true ] \
    && [ "$pid_start_time_cleared" = true ] \
    && [ "$pdocker_known_pids_cleared" = true ] \
    && [ "$pdocker_launcher_pid_cleared" = true ] \
    && [ "$pdocker_launcher_pid_start_time_cleared" = true ] \
    && [ "$pdocker_launcher_pgid_cleared" = true ] \
    && [ "$pdocker_process_group_id_cleared" = true ] \
    && [ "$pdocker_teardown_no_orphan_processes" = true ] \
    && [ "$pdocker_teardown_survivors_empty" = true ]; then
    persisted_state_cleared=true
    persisted_state_json_same_container_id=true
  else
    add_gap_reason "persisted state reducer did not prove cleared PID/launcher/process-group fields plus PdockerTeardown.NoOrphanProcesses=true and empty Survivors for the same Engine container ID"
  fi

  if [ -f "$DIAG/$lifecycle_op.rc" ] && [ -f "$DIAG/$lifecycle_rm.rc" ]; then
    lifecycle_logs_bound=true
  else
    add_gap_reason "lifecycle command stdout/stderr/rc artifacts are incomplete"
  fi
  if [ "$lifecycle_logs_bound" = true ] && [ "$create_output_same" = true ] && [ "$inspect_before_same" = true ]; then
    lifecycle_logs_same_container_id=true
  else
    add_gap_reason "lifecycle log binding is not reduced to the same Engine container ID"
  fi
  logs_before_rc="${logs_before%.out}.rc"
  logs_after_rc="${logs_after%.out}.rc"
  if [ -f "$logs_before" ] && [ -f "$logs_after" ] && [ -f "$logs_before_rc" ] && [ -f "$logs_after_rc" ]; then
    container_logs_bound=true
  else
    add_gap_reason "container log artifacts are incomplete"
  fi
  if [ "$container_logs_bound" = true ] && [ "$cid_exact" = true ] && [ "$inspect_before_same" = true ]; then
    container_logs_same_container_id=true
  else
    add_gap_reason "container log binding is not reduced to the same Engine container ID"
  fi

  engine_inspect_same_container_id=false
  if [ "$create_output_same" = "true" ] && [ "$inspect_before_same" = "true" ] \
    && [ "$inspect_after_same" = "true" ] && [ "$inspect_after_rm_gone" = "true" ]; then
    engine_inspect_same_container_id=true
  fi

  proof_status="planned-gap"
  proof_success=false
  if [ "$engine_inspect_same_container_id" = true ] \
    && [ "$engine_containers_after_absent" = true ] \
    && [ "$live_identity_same_container_id" = true ] \
    && [ "$process_tree_clear" = true ] \
    && [ "$direct_child_absence" = true ] \
    && [ "$direct_child_anchored_to_live_identity" = true ] \
    && [ "$listener_absence" = true ] \
    && [ "$listener_owner_same_container_id" = true ] \
    && [ "$stale_pid_absence" = true ] \
    && [ "$stale_pid_anchored_to_live_identity" = true ] \
    && [ "$stale_name_absence" = true ] \
    && [ "$residue_absence" = true ] \
    && [ "$gpu_media_executor_residue_same_container_id" = true ] \
    && [ "$persisted_state_cleared" = true ] \
    && [ "$persisted_state_json_same_container_id" = true ] \
    && [ "$lifecycle_logs_bound" = true ] \
    && [ "$lifecycle_logs_same_container_id" = true ] \
    && [ "$container_logs_bound" = true ] \
    && [ "$container_logs_same_container_id" = true ] \
    && [ ! -s "$gap_reasons" ] \
    && [ ! -s "$fail_reasons" ] \
    && [ ! -s "$mismatches" ] \
    && [ ! -s "$survivors" ]; then
    proof_status="device-pass"
    proof_success=true
  fi

  cat >"$DIAG/$label.json" <<JSON
{
  "SchemaVersion": 2,
  "Kind": "same-container-id-teardown-proof",
  "ContainerId": $(json_string "$cid"),
  "Operation": $(json_string "$operation"),
  "Status": $(json_string "$proof_status"),
  "Success": $(json_bool "$proof_success"),
  "VerifierReduction": {
    "ReducedEngineContainerId": $( [ "$cid_exact" = true ] && json_string "$cid" || printf null ),
    "SourceContainerIds": {
      "EngineApiContainersJson": $( [ "$engine_containers_after_absent" = true ] && json_string "$cid" || printf null ),
      "EngineApiInspect": $( [ "$engine_inspect_same_container_id" = true ] && json_string "$cid" || printf null ),
      "PersistedStateJson": $( [ "$persisted_state_json_same_container_id" = true ] && json_string "$cid" || printf null ),
      "ProcessTable": $( [ "$stale_pid_absence" = true ] && json_string "$cid" || printf null ),
      "ProcessTree": $( [ "$process_tree_clear" = true ] && json_string "$cid" || printf null ),
      "ListenerOwner": $( [ "$listener_owner_same_container_id" = true ] && json_string "$cid" || printf null ),
      "GpuMediaExecutorResidue": $( [ "$gpu_media_executor_residue_same_container_id" = true ] && json_string "$cid" || printf null ),
      "ContainerLogs": $( [ "$container_logs_same_container_id" = true ] && json_string "$cid" || printf null ),
      "LifecycleLogs": $( [ "$lifecycle_logs_same_container_id" = true ] && json_string "$cid" || printf null )
    },
    "EngineInspectSameContainerId": $(json_bool "$engine_inspect_same_container_id"),
    "EngineContainersAfterIdAbsent": $(json_bool "$engine_containers_after_absent"),
    "ProcessTreeClear": $(json_bool "$process_tree_clear"),
    "DirectChildAbsence": $(json_bool "$direct_child_absence"),
    "ListenerAbsence": $(json_bool "$listener_absence"),
    "StalePidAbsence": $(json_bool "$stale_pid_absence"),
    "StaleNameAbsence": $(json_bool "$stale_name_absence"),
    "GpuMediaExecutorResidueAbsence": $(json_bool "$residue_absence"),
    "PersistedStateCleared": $(json_bool "$persisted_state_cleared"),
    "LifecycleLogsBound": $(json_bool "$lifecycle_logs_bound"),
    "ContainerLogsBound": $(json_bool "$container_logs_bound"),
    "LivePreOperationIdentitySameContainerId": $(json_bool "$live_identity_same_container_id"),
    "StalePidAnchoredToLiveIdentity": $(json_bool "$stale_pid_anchored_to_live_identity"),
    "DirectChildProofAnchoredToLiveIdentity": $(json_bool "$direct_child_anchored_to_live_identity"),
    "ListenerOwnerSameContainerId": $(json_bool "$listener_owner_same_container_id"),
    "GpuMediaExecutorResidueSameContainerId": $(json_bool "$gpu_media_executor_residue_same_container_id"),
    "PersistedStateJsonSameContainerId": $(json_bool "$persisted_state_json_same_container_id"),
    "LifecycleLogsSameContainerId": $(json_bool "$lifecycle_logs_same_container_id"),
    "ContainerLogsSameContainerId": $(json_bool "$container_logs_same_container_id"),
    "LiveIdentity": {
      "ContainerId": $( [ "$live_identity_same_container_id" = true ] && json_string "$cid" || printf null ),
      "Artifact": $(json_string "files/$live_identity"),
      "InspectBefore": $(json_string "files/$inspect_before"),
      "InspectBeforePid": $(json_string "$pre_operation_pid"),
      "LiveBeforeOperation": $(json_bool "$(inspect_running_from_http "$inspect_before" && printf true || printf false)"),
      "PidPresentBeforeOperation": $(json_bool "$(process_snapshot_has_pid "$pre_operation_pid" "$process_after_start" && printf true || printf false)"),
      "StalePidArtifactsAnchored": $(json_bool "$stale_pid_anchored_to_live_identity"),
      "DirectChildArtifactsAnchored": $(json_bool "$direct_child_anchored_to_live_identity")
    },
    "ListenerReduction": {
      "ContainerId": $( [ "$listener_owner_same_container_id" = true ] && json_string "$cid" || printf null ),
      "ListenerOwnerSameContainerId": $(json_bool "$listener_owner_same_container_id"),
      "LivePreOperationPid": $(json_string "$pre_operation_pid"),
      "AfterOperationListenerForLivePidAbsent": $(json_bool "$([ -f "$listener_after_operation" ] && ! listener_snapshot_mentions_pid "$pre_operation_pid" "$listener_after_operation" && printf true || printf false)"),
      "AfterRemoveListenerForLivePidAbsent": $(json_bool "$([ -f "$listener_after_rm" ] && ! listener_snapshot_mentions_pid "$pre_operation_pid" "$listener_after_rm" && printf true || printf false)"),
      "Artifacts": [$(json_string "files/$listener_after_operation"), $(json_string "files/$listener_after_rm")]
    },
    "PersistedStateTeardownFields": {
      "ContainerId": $( [ "$persisted_state_json_same_container_id" = true ] && json_string "$cid" || printf null ),
      "StatePidCleared": $(json_bool "$state_pid_cleared"),
      "PidStartTimeCleared": $(json_bool "$pid_start_time_cleared"),
      "PdockerKnownPidsCleared": $(json_bool "$pdocker_known_pids_cleared"),
      "PdockerLauncherPidCleared": $(json_bool "$pdocker_launcher_pid_cleared"),
      "PdockerLauncherPidStartTimeCleared": $(json_bool "$pdocker_launcher_pid_start_time_cleared"),
      "PdockerLauncherPgidCleared": $(json_bool "$pdocker_launcher_pgid_cleared"),
      "PdockerProcessGroupIdCleared": $(json_bool "$pdocker_process_group_id_cleared"),
      "PdockerTeardownNoOrphanProcesses": $(json_bool "$pdocker_teardown_no_orphan_processes"),
      "PdockerTeardownSurvivorsEmpty": $(json_bool "$pdocker_teardown_survivors_empty"),
      "PdockerTeardownSurvivors": [],
      "Artifacts": [$(json_string "files/$state_after_operation"), $(json_string "files/$state_after_rm")]
    },
    "LogBinding": {
      "ContainerId": $( [ "$lifecycle_logs_same_container_id" = true ] && [ "$container_logs_same_container_id" = true ] && json_string "$cid" || printf null ),
      "LifecycleLogsSameContainerId": $(json_bool "$lifecycle_logs_same_container_id"),
      "ContainerLogsSameContainerId": $(json_bool "$container_logs_same_container_id"),
      "LifecycleCommandArtifactsComplete": $(json_bool "$lifecycle_logs_bound"),
      "ContainerLogArtifactsComplete": $(json_bool "$container_logs_bound"),
      "Artifacts": [$(json_string "files/$DIAG/$lifecycle_op.out"), $(json_string "files/$DIAG/$lifecycle_op.err"), $(json_string "files/$DIAG/$lifecycle_rm.out"), $(json_string "files/$DIAG/$lifecycle_rm.err"), $(json_string "files/$logs_before"), $(json_string "files/$logs_after")]
    },
    "MismatchedContainerIds": $(write_json_string_array_from_file "$mismatches"),
    "Survivors": $(write_json_string_array_from_file "$survivors")
  },
  "GapReasons": $(write_json_string_array_from_file "$gap_reasons"),
  "FailReasons": $(write_json_string_array_from_file "$fail_reasons"),
  "ReductionArtifacts": {
    "GapReasons": $(json_string "files/$gap_reasons"),
    "FailReasons": $(json_string "files/$fail_reasons"),
    "MismatchedContainerIds": $(json_string "files/$mismatches"),
    "Survivors": $(json_string "files/$survivors")
  },
  "RequiredSameContainerId": [
    "EngineCreateOutput",
    "EngineInspectBefore",
    "EngineInspectAfterOperation",
    "EngineInspectAfterRemove",
    "LivePreOperationIdentity",
    "ProcessTreeBeforeAfter",
    "DirectChildAbsence",
    "ListenerAbsenceBeforeAfter",
    "StalePidAbsence",
    "StaleNameAbsence",
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
    "LivePreOperationIdentity": {
      "Artifact": $(json_string "files/$live_identity"),
      "InspectBefore": $(json_string "files/$inspect_before"),
      "ProcessSnapshotBeforeOperation": $(json_string "files/$process_after_start")
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
    "StaleName": {
      "AfterRemove": $(json_string "files/$stale_name_after_rm")
    },
    "DirectChildAbsence": {
      "AfterOperation": $(json_string "files/$direct_children_after_operation"),
      "AfterRemove": $(json_string "files/$direct_children_after_rm")
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
    "LivePreOperationIdentity": $(json_string "files/$live_identity"),
    "ProcessTreeBeforeAfter": [$(json_string "files/$process_before"), $(json_string "files/$process_after_start"), $(json_string "files/$process_after_operation"), $(json_string "files/$process_after_rm")],
    "ListenerAbsenceBeforeAfter": [$(json_string "files/$listener_before"), $(json_string "files/$listener_after_start"), $(json_string "files/$listener_after_operation"), $(json_string "files/$listener_after_rm")],
    "GpuMediaExecutorResidueBeforeAfter": [$(json_string "files/$residue_before"), $(json_string "files/$residue_after_start"), $(json_string "files/$residue_after_operation"), $(json_string "files/$residue_after_rm")],
    "PersistedStateJsonBeforeAfter": [$(json_string "files/$state_before"), $(json_string "files/$state_after_start"), $(json_string "files/$state_after_operation"), $(json_string "files/$state_after_rm")],
    "StalePidAbsence": [$(json_string "files/$stale_pid_after_operation"), $(json_string "files/$stale_pid_after_rm")],
    "StaleNameAbsence": $(json_string "files/$stale_name_after_rm"),
    "DirectChildAbsence": [$(json_string "files/$direct_children_after_operation"), $(json_string "files/$direct_children_after_rm")],
    "LifecycleLogs": [$(json_string "files/$DIAG/$lifecycle_op.out"), $(json_string "files/$DIAG/$lifecycle_op.err"), $(json_string "files/$DIAG/$lifecycle_rm.out"), $(json_string "files/$DIAG/$lifecycle_rm.err")],
    "ContainerLogs": [$(json_string "files/$logs_before"), $(json_string "files/$logs_after")]
  },
  "AcceptanceRule": "A device verifier may only pass this artifact when every before/after evidence source is tied to this exact Engine container ID and proves no surviving process tree, no direct children of State.Pid, listener absence, stale PID absence, GPU/media executor residue absence, stale state/name absence, and no misleading previous log remains after removal; persisted state after stop/kill must show PdockerTeardown.NoOrphanProcesses=true and empty PdockerTeardown.Survivors.",
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
write_live_identity_evidence stop-live-identity-before-stop "$STOP_CID" "$DIAG/stop-inspect-before.http" "$DIAG/process-after-stop-start.txt"
record_cmd stop docker stop -t 1 "$STOP_CID"
http_get stop-inspect-after "/containers/$STOP_CID/json"
record_cmd logs-stop-after docker logs "$STOP_CID"
snapshot_ps process-after-stop
snapshot_listeners listeners-after-stop
snapshot_executor_residue executor-residue-after-stop
write_pid_evidence stop-stale-pid-after-stop "$STOP_CID" "$DIAG/stop-inspect-before.http" "$DIAG/process-after-stop.txt" "$DIAG/stop-inspect-after.http"
write_process_tree_evidence stop-process-tree-after-stop "$STOP_CID" "$DIAG/stop-inspect-before.http" "$DIAG/process-after-stop.txt" "$DIAG/stop-inspect-after.http"
snapshot_state_json persisted-state-after-stop
record_cmd rm-stopped docker rm "$STOP_CID"
http_get stop-inspect-after-rm "/containers/$STOP_CID/json"
snapshot_ps process-after-rm-stopped
snapshot_listeners listeners-after-rm-stopped
snapshot_executor_residue executor-residue-after-rm-stopped
write_pid_evidence stop-stale-pid-after-rm "$STOP_CID" "$DIAG/stop-inspect-before.http" "$DIAG/process-after-rm-stopped.txt" "$DIAG/stop-inspect-after-rm.http"
write_process_tree_evidence stop-process-tree-after-rm "$STOP_CID" "$DIAG/stop-inspect-before.http" "$DIAG/process-after-rm-stopped.txt" "$DIAG/stop-inspect-after-rm.http"
snapshot_state_json persisted-state-after-rm-stopped
record_cmd create-kill docker create --name "$KILL_NAME" "$IMAGE" sh -lc 'while true; do sleep 30; done'
KILL_CID="$(container_id_from_out "$DIAG/create-kill.out")"
record_cmd start-kill docker start "$KILL_CID"
http_get kill-inspect-before "/containers/$KILL_CID/json"
record_cmd logs-kill-before docker logs "$KILL_CID"
snapshot_ps process-after-kill-start
snapshot_listeners listeners-after-kill-start
snapshot_executor_residue executor-residue-after-kill-start
snapshot_state_json persisted-state-after-kill-start
write_live_identity_evidence kill-live-identity-before-kill "$KILL_CID" "$DIAG/kill-inspect-before.http" "$DIAG/process-after-kill-start.txt"
record_cmd kill docker kill "$KILL_CID"
http_get kill-inspect-after "/containers/$KILL_CID/json"
record_cmd logs-kill-after docker logs "$KILL_CID"
snapshot_ps process-after-kill
snapshot_listeners listeners-after-kill
snapshot_executor_residue executor-residue-after-kill
write_pid_evidence kill-stale-pid-after-kill "$KILL_CID" "$DIAG/kill-inspect-before.http" "$DIAG/process-after-kill.txt" "$DIAG/kill-inspect-after.http"
write_process_tree_evidence kill-process-tree-after-kill "$KILL_CID" "$DIAG/kill-inspect-before.http" "$DIAG/process-after-kill.txt" "$DIAG/kill-inspect-after.http"
snapshot_state_json persisted-state-after-kill
record_cmd rm-killed docker rm "$KILL_CID"
http_get kill-inspect-after-rm "/containers/$KILL_CID/json"
snapshot_ps process-after-rm-killed
snapshot_listeners listeners-after-rm-killed
snapshot_executor_residue executor-residue-after-rm-killed
write_pid_evidence kill-stale-pid-after-rm "$KILL_CID" "$DIAG/kill-inspect-before.http" "$DIAG/process-after-rm-killed.txt" "$DIAG/kill-inspect-after-rm.http"
write_process_tree_evidence kill-process-tree-after-rm "$KILL_CID" "$DIAG/kill-inspect-before.http" "$DIAG/process-after-rm-killed.txt" "$DIAG/kill-inspect-after-rm.http"
snapshot_state_json persisted-state-after-rm-killed
http_get engine-containers-after '/containers/json?all=1'
write_name_residue_evidence stop-stale-name-after-rm "$STOP_CID" "$STOP_NAME" "$DIAG/engine-containers-after.http"
write_name_residue_evidence kill-stale-name-after-rm "$KILL_CID" "$KILL_NAME" "$DIAG/engine-containers-after.http"

write_same_id_evidence same-container-id-stop-rm "$STOP_CID" "stop-rm" "$DIAG/create-stop.out" "$DIAG/stop-inspect-before.http" "$DIAG/stop-inspect-after.http" "$DIAG/stop-inspect-after-rm.http" "$DIAG/process-before.txt" "$DIAG/process-after-stop-start.txt" "$DIAG/process-after-stop.txt" "$DIAG/process-after-rm-stopped.txt" "$DIAG/listeners-before.txt" "$DIAG/listeners-after-stop-start.txt" "$DIAG/listeners-after-stop.txt" "$DIAG/listeners-after-rm-stopped.txt" "$DIAG/executor-residue-before.txt" "$DIAG/executor-residue-after-stop-start.txt" "$DIAG/executor-residue-after-stop.txt" "$DIAG/executor-residue-after-rm-stopped.txt" "$DIAG/persisted-state-before.txt" "$DIAG/persisted-state-after-stop-start.txt" "$DIAG/persisted-state-after-stop.txt" "$DIAG/persisted-state-after-rm-stopped.txt" "$DIAG/stop-stale-pid-after-stop.json" "$DIAG/stop-stale-pid-after-rm.json" "$DIAG/logs-stop-before.out" "$DIAG/logs-stop-after.out"

write_same_id_evidence same-container-id-kill-rm "$KILL_CID" "kill-rm" "$DIAG/create-kill.out" "$DIAG/kill-inspect-before.http" "$DIAG/kill-inspect-after.http" "$DIAG/kill-inspect-after-rm.http" "$DIAG/process-before.txt" "$DIAG/process-after-kill-start.txt" "$DIAG/process-after-kill.txt" "$DIAG/process-after-rm-killed.txt" "$DIAG/listeners-before.txt" "$DIAG/listeners-after-kill-start.txt" "$DIAG/listeners-after-kill.txt" "$DIAG/listeners-after-rm-killed.txt" "$DIAG/executor-residue-before.txt" "$DIAG/executor-residue-after-kill-start.txt" "$DIAG/executor-residue-after-kill.txt" "$DIAG/executor-residue-after-rm-killed.txt" "$DIAG/persisted-state-before.txt" "$DIAG/persisted-state-after-kill-start.txt" "$DIAG/persisted-state-after-kill.txt" "$DIAG/persisted-state-after-rm-killed.txt" "$DIAG/kill-stale-pid-after-kill.json" "$DIAG/kill-stale-pid-after-rm.json" "$DIAG/logs-kill-before.out" "$DIAG/logs-kill-after.out"

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

STOP_PROOF_PASS=false
KILL_PROOF_PASS=false
grep -Eq '"Status"[[:space:]]*:[[:space:]]*"device-pass"' "$DIAG/same-container-id-stop-rm.json" 2>/dev/null   && grep -Eq '"Success"[[:space:]]*:[[:space:]]*true' "$DIAG/same-container-id-stop-rm.json" 2>/dev/null   && STOP_PROOF_PASS=true
grep -Eq '"Status"[[:space:]]*:[[:space:]]*"device-pass"' "$DIAG/same-container-id-kill-rm.json" 2>/dev/null   && grep -Eq '"Success"[[:space:]]*:[[:space:]]*true' "$DIAG/same-container-id-kill-rm.json" 2>/dev/null   && KILL_PROOF_PASS=true
RUNTIME_TEARDOWN_STATUS="planned-gap"
RUNTIME_TEARDOWN_SUCCESS=false
RUNTIME_TEARDOWN_EXIT=2
if [ "$STOP_PROOF_PASS" = true ] && [ "$KILL_PROOF_PASS" = true ]; then
  RUNTIME_TEARDOWN_STATUS="device-pass"
  RUNTIME_TEARDOWN_SUCCESS=true
  RUNTIME_TEARDOWN_EXIT=0
fi

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
  "Status": $(json_string "$RUNTIME_TEARDOWN_STATUS"),
  "Success": $(json_bool "$RUNTIME_TEARDOWN_SUCCESS"),
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
    "ProcessTree": ["files/$DIAG/same-container-id-stop-rm.json", "files/$DIAG/same-container-id-kill-rm.json", "files/$DIAG/stop-process-tree-after-stop.json", "files/$DIAG/stop-process-tree-after-rm.json", "files/$DIAG/kill-process-tree-after-kill.json", "files/$DIAG/kill-process-tree-after-rm.json"],
    "LivePreOperationIdentity": ["files/$DIAG/stop-live-identity-before-stop.json", "files/$DIAG/kill-live-identity-before-kill.json"],
    "DirectChildAbsence": ["files/$DIAG/stop-process-tree-after-stop.json", "files/$DIAG/stop-process-tree-after-rm.json", "files/$DIAG/kill-process-tree-after-kill.json", "files/$DIAG/kill-process-tree-after-rm.json", "files/$DIAG/stop-process-tree-after-stop-direct-children.txt", "files/$DIAG/kill-process-tree-after-kill-direct-children.txt"],
    "ListenerAbsence": ["files/$DIAG/listeners-before.txt", "files/$DIAG/listeners-after-stop.txt", "files/$DIAG/listeners-after-rm-stopped.txt", "files/$DIAG/listeners-after-kill.txt", "files/$DIAG/listeners-after-rm-killed.txt"],
    "StalePid": ["files/$DIAG/stop-stale-pid-after-stop.json", "files/$DIAG/stop-stale-pid-after-rm.json", "files/$DIAG/kill-stale-pid-after-kill.json", "files/$DIAG/kill-stale-pid-after-rm.json"],
    "StaleName": ["files/$DIAG/stop-stale-name-after-rm.json", "files/$DIAG/kill-stale-name-after-rm.json", "files/$DIAG/stop-stale-name-after-rm-name-matches.txt", "files/$DIAG/kill-stale-name-after-rm-name-matches.txt"],
    "GpuMediaExecutorResidue": ["files/$DIAG/executor-residue-before.txt", "files/$DIAG/executor-residue-after-stop.txt", "files/$DIAG/executor-residue-after-rm-stopped.txt", "files/$DIAG/executor-residue-after-kill.txt", "files/$DIAG/executor-residue-after-rm-killed.txt"],
    "SameContainerId": ["files/$DIAG/same-container-id-stop-rm.json", "files/$DIAG/same-container-id-kill-rm.json"],
    "PersistedStateJson": ["files/$DIAG/persisted-state-before.txt", "files/$DIAG/persisted-state-after-stop.txt", "files/$DIAG/persisted-state-after-rm-stopped.txt", "files/$DIAG/persisted-state-after-kill.txt", "files/$DIAG/persisted-state-after-rm-killed.txt"],
    "LifecycleLogs": ["files/$DIAG/stop.out", "files/$DIAG/stop.err", "files/$DIAG/kill.out", "files/$DIAG/kill.err", "files/$DIAG/rm-stopped.out", "files/$DIAG/rm-stopped.err", "files/$DIAG/rm-killed.out", "files/$DIAG/rm-killed.err", "files/$DIAG/cleanup-leftovers.out", "files/$DIAG/cleanup-leftovers.err"],
    "ContainerLogs": ["files/$DIAG/logs-stop-before.out", "files/$DIAG/logs-stop-after.out", "files/$DIAG/logs-kill-before.out", "files/$DIAG/logs-kill-after.out"]
  },
  "DeviceGate": {
    "RequiresAdb": true,
    "CollectedViaAdbRunAs": true,
    "HostStaticVerifierCannotPromote": true,
    "DoNotClaimDevicePassWithoutAdb": true,
    "DevicePassStatusRequires": [
      "adb device serial and package run-as context",
      "same Engine container ID across create/inspect/stop-or-kill/rm/logs",
      "no live pre-stop/pre-kill State.Pid and no direct children after stop/kill/rm",
      "no pdocker-direct, service child, listener, GPU executor, media executor, camera/audio/Vulkan helper residue for that container",
      "no stale PID, stale state.json, stale name, duplicate-name, or previous-container-log ambiguity"
    ]
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
    "Stale PID absence anchored to the live pre-stop/pre-kill inspect State.Pid and post-operation process tables",
    "Direct child absence anchored to the live pre-stop/pre-kill inspect State.Pid in post-operation process tables",
    "GPU/media executor residue before/start/after operation/after remove",
    "Stale container-name and duplicate-name absence from /containers/json after rm",
    "Persisted state.json before/start/after operation/after remove, including PdockerTeardown.NoOrphanProcesses=true and an empty PdockerTeardown.Survivors list after successful stop/kill",
    "Lifecycle logs and container logs bound to the same Engine container ID"
  ],
  "RuntimeTeardownStateContract": "After successful stop/kill, pdockerd must clear State.Pid, PidStartTime, PdockerKnownPids, PdockerLauncherPid, PdockerLauncherPidStartTime, PdockerLauncherPgid, and PdockerProcessGroupId; PdockerTeardown.NoOrphanProcesses must be true and PdockerTeardown.Survivors must be empty before remove is accepted.",
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
    "DirectChildAbsence",
    "ListenerAbsenceBeforeAfter",
    "StalePidAbsence",
    "StaleNameAbsence",
    "GpuMediaExecutorResidueBeforeAfter",
    "PersistedStateJsonBeforeAfter",
    "LifecycleLogs",
    "ContainerLogs"
  ],
  "TruthContract": "stop/kill/rm remains non-passing with no fake success until every teardown evidence source proves absence for the same Engine container ID; HTTP 204, CLI exit 0, stale state.json, configured name, listener absence alone, clean process table alone, previous logs, duplicate names, or mixed container IDs are never sufficient.",
  "Unresolved": [
    "HTTP/CLI acknowledgement is recorded but not accepted as proof of teardown.",
    "The smoke fails closed unless stale PID and direct-child absence are anchored to the live pre-stop/pre-kill identity.",
    "Listener absence, persisted state, log binding, and GPU/media executor residue must reduce to the same Engine container ID before device-pass.",
    "This planned-gap artifact is explicitly not fake success.",
    "Device verifier promotion still needs to reduce raw stop/kill/rm evidence into a passing proof before this can pass."
  ]
}
JSON
cat "$LATEST"
exit "$RUNTIME_TEARDOWN_EXIT"
REMOTE_RUNTIME_TEARDOWN
  run_adb push "$local_script" "$remote_script" >/dev/null
  rm -f "$local_script"
  run_adb shell chmod 755 "$remote_script" >/dev/null 2>&1 || true
  set +e
  run_as "sh $remote_script $(remote_quote "$target")"
  local rc=$?
  set -e
  collect_device_dir "files/pdocker/diagnostics/runtime-teardown" "runtime-teardown" || true
  collect_device_file "files/pdocker/diagnostics/runtime-teardown-latest.json" "runtime-teardown-latest.json" || true
  return "$rc"
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

collect_device_dir() {
  local device_dir="$1"
  local host_name="$2"
  local dest_dir rel parent base tmp err
  dest_dir="$(smoke_artifact_dir)"
  rel="${device_dir#files/}"
  parent="${rel%/*}"
  base="${rel##*/}"
  tmp="$(mktemp)"
  err="$dest_dir/$host_name.err"
  mkdir -p "$dest_dir"
  rm -rf "$dest_dir/$host_name"
  if run_adb exec-out run-as "$PKG" sh -c "cd files/$parent && tar cf - $base" >"$tmp" 2>"$err" \
      && tar xf "$tmp" -C "$dest_dir" 2>>"$err"; then
    rm -f "$tmp" "$err"
    if [[ "$base" != "$host_name" && -d "$dest_dir/$base" ]]; then
      mv "$dest_dir/$base" "$dest_dir/$host_name"
    fi
    echo "[pdocker smoke] collected $device_dir -> $dest_dir/$host_name"
  else
    rm -f "$tmp"
    echo "[pdocker smoke] could not collect $device_dir; see $err" >&2
    return 1
  fi
}

collect_ui_it_selftest_artifacts() {
  collect_device_file "files/pdocker/diagnostics/ui-it-selftest-latest.json" "ui-it-selftest-latest.json" || true
  collect_device_file "files/pdocker/diagnostics/engine-exec-input-latest.jsonl" "engine-exec-input-latest.jsonl" || true
}

clear_ui_it_selftest_artifacts() {
  local dest_dir
  dest_dir="$(smoke_artifact_dir)"
  mkdir -p "$dest_dir"
  rm -f "$dest_dir/ui-it-selftest-latest.json" \
    "$dest_dir/engine-exec-input-latest.jsonl" \
    "$dest_dir/ui-it-selftest-latest.json.err" \
    "$dest_dir/engine-exec-input-latest.jsonl.err"
  run_as "rm -f files/pdocker/diagnostics/ui-it-selftest-latest.json files/pdocker/diagnostics/engine-exec-input-latest.jsonl" >/dev/null 2>&1 || true
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
    "enter-single-submit": false,
    "enter-no-duplicate-submit": false,
    "ctrl-c-interrupts-without-literal-c": false,
    "jp-en-ctrl-c-isolated-etx": false,
    "arrow-up-reaches-readline-history": false,
    "arrow-up-no-escape-text": false,
    "ime-enter-ctrlc-regression-covered": false,
    "top-starts-on-tty": false,
    "top-refresh-observed-before-q": false,
    "top-repaint-remains-terminal-shaped": false,
    "q-quits-top": false,
    "top-q-shell-recovery": false,
    "resize-route-is-observable": false,
    "selection-keyboard-suppression": false
  },
  "RequiredEvidence": [
    "enter-single-submit",
    "enter-no-duplicate-submit",
    "ctrl-c-interrupts-without-literal-c",
    "jp-en-ctrl-c-isolated-etx",
    "arrow-up-reaches-readline-history",
    "arrow-up-no-escape-text",
    "ime-enter-ctrlc-regression-covered",
    "top-starts-on-tty",
    "top-refresh-observed-before-q",
    "top-repaint-remains-terminal-shaped",
    "q-quits-top",
    "top-q-shell-recovery",
    "resize-route-is-observable",
    "selection-keyboard-suppression"
  ],
  "Contract": "ACTION_PREFIX.action.SMOKE_UI_IT_SELFTEST must only pass after a real requested/running Engine container is exercised; skip artifacts are non-success and must not be treated as fake success.",
  "StartedAtMs": $now,
  "CompletedAtMs": $now
}
JSON
  clear_ui_it_selftest_artifacts
  run_adb push "$tmp" /data/local/tmp/pdocker-ui-it-selftest-skip.json >/dev/null
  rm -f "$tmp"
  run_as "mkdir -p files/pdocker/diagnostics && cp /data/local/tmp/pdocker-ui-it-selftest-skip.json files/pdocker/diagnostics/ui-it-selftest-latest.json" >/dev/null
  collect_ui_it_selftest_artifacts
}

validate_ui_it_selftest_artifact() {
  local require_container="$1"
  local dest_dir
  local require_flag=()
  dest_dir="$(smoke_artifact_dir)"
  if [[ "$require_container" == "1" ]]; then
    require_flag=(--require-container)
  fi
  python3 "$ROOT/scripts/verify-terminal-exec-it-artifact.py" \
    "$dest_dir/ui-it-selftest-latest.json" \
    "$dest_dir/engine-exec-input-latest.jsonl" \
    "${require_flag[@]}"
  python3 - "$dest_dir/ui-it-selftest-latest.json" "$dest_dir/engine-exec-input-latest.jsonl" <<'PY'
import json
import sys
from pathlib import Path

artifact_path = Path(sys.argv[1])
jsonl_path = Path(sys.argv[2])

def fail(message: str) -> None:
    raise SystemExit(message)

artifact = json.loads(artifact_path.read_text())
if artifact.get("Status") == "planned-skip":
    fail("UI exec-it planned-skip is non-passing evidence; a real container is required")

tail = str(artifact.get("OutputTail", ""))
if tail.count("pdocker-ui-it-ok") != 1:
    fail("UI exec-it Enter evidence must show exactly one pdocker-ui-it-ok marker")
if "sleep 15c" in tail:
    fail("UI exec-it Ctrl-C evidence contains literal c appended to sleep")
if "\x1b[A" in tail or "^[[A" in tail:
    fail("UI exec-it arrow evidence leaked raw ArrowUp escape text")
if "pdocker-ui-it-topq-ok" not in tail:
    fail("UI exec-it top/q evidence missing shell recovery marker")
if not artifact.get("Evidence", {}).get("selection-keyboard-suppression"):
    fail("UI exec-it artifact missing selection keyboard suppression evidence")

events = []
for line in jsonl_path.read_text(errors="replace").splitlines():
    if line.strip():
        events.append(json.loads(line))
inputs = [event for event in events if event.get("event") == "input"]
hex_tokens = [
    [token.lower() for token in str(event.get("hex", "")).split()]
    for event in inputs
]
texts = [str(event.get("text", "")) for event in inputs]

ime_indexes = [index for index, text in enumerate(texts) if "ime-enter-ok" in text]
if not ime_indexes:
    fail("UI exec-it IME Enter command input is missing")
ime_index = ime_indexes[0]
if ime_index + 1 >= len(hex_tokens) or hex_tokens[ime_index + 1] != ["0d"]:
    fail("UI exec-it IME Enter must be proven by exactly one Enter byte after the command")
if ime_index + 2 < len(hex_tokens) and hex_tokens[ime_index + 2] == ["0d"]:
    fail("UI exec-it IME Enter evidence shows a double Enter")

sleep_indexes = [index for index, text in enumerate(texts) if "sleep 15" in text]
if not sleep_indexes:
    fail("UI exec-it Ctrl-C sleep command input is missing")
sleep_index = sleep_indexes[0]
ctrl_indexes = [
    index for index in range(sleep_index + 1, len(hex_tokens))
    if "03" in hex_tokens[index]
]
if not ctrl_indexes:
    fail("UI exec-it Ctrl-C evidence is missing ETX after sleep")
ctrl_index = ctrl_indexes[0]
if hex_tokens[ctrl_index] != ["03"]:
    fail("UI exec-it Ctrl-C must be an isolated ETX byte with no injected literal c")
recovery_indexes = [
    index for index in range(ctrl_index + 1, len(texts))
    if "ctrlc-ok" in texts[index]
]
if not recovery_indexes:
    fail("UI exec-it Ctrl-C recovery command input is missing")
for index in range(sleep_index, recovery_indexes[0] + 1):
    if "sleep 15c" in texts[index]:
        fail("UI exec-it Ctrl-C evidence contains literal c appended to sleep")
    if hex_tokens[index] == ["63"] or ("03" in hex_tokens[index] and "63" in hex_tokens[index]):
        fail("UI exec-it Ctrl-C evidence contains an injected literal c around ETX")

arrow_indexes = [
    index for index, tokens in enumerate(hex_tokens)
    if tokens == ["1b", "5b", "41", "0d"]
]
if not arrow_indexes:
    fail("UI exec-it ArrowUp+Enter byte evidence is missing")
top_indexes = [index for index, text in enumerate(texts) if text == "top\r"]
q_indexes = [index for index, tokens in enumerate(hex_tokens) if tokens == ["71"]]
topq_indexes = [index for index, text in enumerate(texts) if "topq-ok" in text]
if not top_indexes or not q_indexes or not topq_indexes or not (top_indexes[0] < q_indexes[0] < topq_indexes[0]):
    fail("UI exec-it top q shell recovery must be ordered as top, q, recovery command")
PY
}

ui_engine_exec_it_selftest() {
  local container_ref="$1"
  local require_container="${2:-0}"
  if [[ -z "$container_ref" ]]; then
    echo "[pdocker smoke] ui self-test engine exec -it planned-skip: no container id"
    write_ui_it_selftest_skip_artifact "no container id was available for UI exec-it self-test" "$require_container"
    validate_ui_it_selftest_artifact "$require_container" || true
    echo "UI exec -it gate requires a real container; planned-skip is non-passing evidence" >&2
    return 1
  fi
  echo "[pdocker smoke] ui self-test engine exec -it container=$container_ref"
  clear_ui_it_selftest_artifacts
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
run_stdout=pdocker/diagnostics/docker-run-rm-ubuntu-echo-hi.stdout
run_stderr=pdocker/diagnostics/docker-run-rm-ubuntu-echo-hi.stderr
run_log=pdocker/diagnostics/docker-run-rm-ubuntu-echo-hi.log
run_json=pdocker/diagnostics/docker-run-rm-ubuntu-echo-hi.json
run_cid=pdocker/diagnostics/docker-run-rm-ubuntu-echo-hi.cid
rm -f "$run_stdout" "$run_stderr" "$run_log" "$run_json" "$run_cid"
set +e
docker run --cidfile "$run_cid" --rm ubuntu:22.04 echo hi >"$run_stdout" 2>"$run_stderr"
status=$?
cat "$run_stdout" "$run_stderr" >"$run_log"
cat "$run_log"
if grep -qx hi "$run_stdout" && [ "$(wc -l <"$run_stdout" | tr -d " ")" = "1" ]; then stdout_exact_hi=true; else stdout_exact_hi=false; fi
if [ ! -s "$run_stderr" ]; then stderr_empty=true; else stderr_empty=false; fi
cid="$(cat "$run_cid" 2>/dev/null | tr -d "\r\n")"
if printf "%s" "$cid" | grep -Eq "^[0-9a-f]{64}$"; then cid_ok=true; else cid_ok=false; fi
if [ "$status" -eq 0 ] && [ "$stdout_exact_hi" = true ] && [ "$stderr_empty" = true ] && [ "$cid_ok" = true ]; then
  success=true
  status_text=pass
else
  success=false
  status_text=failed
fi
cat > "$run_json" <<JSON
{
  "schema": "pdocker.android.runtime-single-container-echo-hi.v1",
  "status": "$status_text",
  "success": $success,
  "command": "docker run --rm ubuntu:22.04 echo hi",
  "effective_command": "docker run --cidfile <diagnostic-cidfile> --rm ubuntu:22.04 echo hi",
  "exit_code": $status,
  "stdout_exact": "hi",
  "stdout_exact_match": $stdout_exact_hi,
  "stderr_empty": $stderr_empty,
  "container_id": "$cid",
  "container_id_source": "docker --cidfile",
  "host_shell_fallback": $([ "$cid_ok" = true ] && echo false || echo true),
  "evidence": {
    "stdout": "files/$run_stdout",
    "stderr": "files/$run_stderr",
    "combined_log": "files/$run_log",
    "cidfile": "files/$run_cid"
  }
}
JSON
if [ "$success" != true ]; then
  echo "docker run --rm ubuntu:22.04 echo hi failed; diagnostics: files/$run_log and files/$run_json" >&2
  exit 1
fi'
}

single_container_echo_hi_entrypoint() {
  rm -f "$(smoke_artifact_dir)/runtime-single-container-echo-hi-latest.json"
  docker_run_rm_smoke
  collect_device_file \
    "files/pdocker/diagnostics/docker-run-rm-ubuntu-echo-hi.json" \
    "runtime-single-container-echo-hi-latest.json"
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
if [[ -n "$DOCKER_CP_E2E_TARGET" ]]; then
  docker_cp_e2e_acceptance_entrypoint "$DOCKER_CP_E2E_TARGET"
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
fi

if [[ "$SINGLE_CONTAINER_ECHO_HI" -eq 1 ]]; then
  single_container_echo_hi_entrypoint
  exit 0
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

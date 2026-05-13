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
  find pdocker/projects -maxdepth 4 \( -name compose.yaml -o -name docker-compose.yml \) -type f 2>/dev/null | sort | while IFS= read -r f; do
    sed -n 's/.*\([0-9][0-9][0-9][0-9][0-9]*\):[0-9][0-9]*/\1/p' "$f" 2>/dev/null
  done | sort -u >>"$DIAG/configured-ports.txt"
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
      default-workspace|workspace) case "$hay" in *workspace*|*code-server*|*vscode*) score=$((score + 5)); reasons="${reasons}workspace-service-hint," ;; esac ;;
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
    case "$target_lc" in default-workspace|workspace) case "$hay" in *workspace*|*code-server*|*vscode*) score=$((score + 5)); reasons="${reasons}workspace-service-hint," ;; esac ;; llama) case "$hay" in *llama*) score=$((score + 5)); reasons="${reasons}llama-service-hint," ;; esac ;; esac
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
  [ -n "$selected_id" ] && awk -F '\t' -v id="$selected_id" 'index(id,$2)==1 || index($2,id)==1 { found=1 } END{ exit found ? 0 : 1 }' "$DIAG/state-container-ids.tsv" 2>/dev/null && match=true
  printf '{\n  "SelectedEngineContainerId": %s,\n  "AnyStateIdMatchesSelected": %s,\n  "Matches": [\n' "$( [ -n "$selected_id" ] && json_string "$selected_id" || printf null )" "$(json_bool "$match")" >"$DIAG/state-id-comparison.json"
  first=1
  while IFS='	' read -r path sid; do
    [ -n "$sid" ] || continue
    row_match=false
    if [ -n "$selected_id" ]; then case "$selected_id" in "$sid"*) row_match=true ;; esac; case "$sid" in "$selected_id"*) row_match=true ;; esac; fi
    [ "$first" = 1 ] || printf ',\n' >>"$DIAG/state-id-comparison.json"
    first=0
    printf '    {"Path":%s,"StateContainerId":%s,"MatchesSelected":%s}' "$(json_string "$path")" "$(json_string "$sid")" "$(json_bool "$row_match")" >>"$DIAG/state-id-comparison.json"
  done <"$DIAG/state-container-ids.tsv"
  printf '\n  ]\n}\n' >>"$DIAG/state-id-comparison.json"
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
extract_state_ids_and_compare "$SELECTED_ENGINE_CID"

: >"$DIAG/container-ids.txt"
cat "$DIAG/engine-ps-running.out" 2>/dev/null | while IFS= read -r cid; do
  [ -n "$cid" ] || continue
  printf '%s\n' "$cid" >>"$DIAG/container-ids.txt"
  safe=$(printf '%s' "$cid" | sed 's/[^0-9A-Za-z_.-]/_/g')
  http_get "inspect-$safe" "/containers/$cid/json"
  record_cmd "logs-$safe" docker logs --tail=200 "$cid"
done

ENGINE_PS_RC="$(cat "$DIAG/engine-ps.rc" 2>/dev/null)"
ENGINE_HTTP_RC="$(cat "$DIAG/engine-containers-json.rc" 2>/dev/null)"
ENGINE_STATUS="$(cat "$DIAG/engine-containers-json.status" 2>/dev/null)"
CID_COUNT="$(wc -l <"$DIAG/container-ids.txt" 2>/dev/null | tr -d ' ')"
PORTS="$(tr '\n' ' ' <"$DIAG/configured-ports.txt" 2>/dev/null | sed 's/[[:space:]]*$//')"
STATE_MATCH="false"
grep -q '"AnyStateIdMatchesSelected": true' "$DIAG/state-id-comparison.json" 2>/dev/null && STATE_MATCH="true"
LISTENER_PROC_MATCH_PORTS="$(sed -n 's/^port=\([0-9][0-9]*\).*proc_net_tcp_matches=\([1-9][0-9]*\).*/\1/p' "$DIAG/listener-probe.txt" 2>/dev/null | tr '\n' ' ' | sed 's/[[:space:]]*$//')"

cat > "$LATEST" <<JSON
{
  "SchemaVersion": 1,
  "Kind": "service-truth",
  "Status": "planned-gap",
  "Success": false,
  "Target": $(json_string "$TARGET"),
  "StartedAt": $(json_string "$STARTED_AT"),
  "CompletedAt": $(json_string "$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date)"),
  "DeviceProofAttempted": true,
  "TruthContract": {
    "RequiredSameContainerId": ["UICard", "EngineApiContainersJson", "PersistedStateJson", "ProcessTable", "ListenerProbe", "ContainerLogs"],
    "AcceptanceRule": "Success may become true only when every source names the same current Engine container ID for the service listener; configured ports, stale names, stale PIDs, previous logs, and background job success are insufficient."
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
      "ContainerId": null,
      "Proven": false,
      "Artifacts": ["files/$DIAG/ui-jobs.json", "files/$DIAG/ui-project-files.txt", "files/$DIAG/ui-project-snippets.txt"],
      "Gap": "The shell smoke can collect UI input files but cannot yet export the rendered UI card service-health container ID."
    },
    "EngineApiContainersJson": {
      "ContainerId": $( [ -n "$SELECTED_ENGINE_CID" ] && json_string "$SELECTED_ENGINE_CID" || printf null ),
      "CandidateSelected": $(json_bool "$( [ -n "$SELECTED_ENGINE_CID" ] && echo true || echo false )"),
      "Proven": false,
      "Artifacts": ["files/$DIAG/engine-containers-json.http", "files/$DIAG/engine-ps.out", "files/$DIAG/container-ids.txt", "files/$DIAG/engine-candidates.json"],
      "Gap": "Engine candidate selection from labels/names is machine-readable, but it is not acceptance until bound to the UI card."
    },
    "PersistedStateJson": {
      "ContainerId": $( [ "$STATE_MATCH" = true ] && [ -n "$SELECTED_ENGINE_CID" ] && json_string "$SELECTED_ENGINE_CID" || printf null ),
      "MatchesSelectedEngineContainerId": $(json_bool "$STATE_MATCH"),
      "Proven": false,
      "Artifacts": ["files/$DIAG/persisted-state-json.txt", "files/$DIAG/state-id-comparison.json", "files/$DIAG/state-container-ids.tsv"],
      "Gap": "state.json ID comparison is machine-readable, but still not a same-source acceptance proof without UI/process/listener/log agreement."
    },
    "ProcessTable": {
      "ContainerId": null,
      "Pid": null,
      "Proven": false,
      "Artifacts": ["files/$DIAG/process-table.txt"],
      "Gap": "Process rows are captured, but no pid/container ownership map is proven for the selected Engine container ID."
    },
    "ListenerProbe": {
      "ContainerId": null,
      "Pid": null,
      "Proven": false,
      "Artifacts": ["files/$DIAG/configured-ports.txt", "files/$DIAG/listener-probe.json", "files/$DIAG/listener-probe.txt", "files/$DIAG/proc-net-tcp.txt", "files/$DIAG/listeners-tool.txt"],
      "ProcNetTcpMatchedPorts": $(json_string "$LISTENER_PROC_MATCH_PORTS"),
      "Gap": "Listener port and /proc/net/tcp evidence is machine-readable, but listener socket ownership is not yet mapped to the selected container process tree."
    },
    "ContainerLogs": {
      "ContainerId": null,
      "Proven": false,
      "Artifacts": ["files/$DIAG/logs-<container-id>.out"],
      "Gap": "Logs are collected per running Engine container ID, but no current service log marker has been selected and bound to the UI card/listener."
    }
  },
  "Evidence": {
    "UICard": ["files/$DIAG/ui-jobs.json", "files/$DIAG/ui-project-snippets.txt"],
    "EngineApiContainersJson": ["files/$DIAG/engine-containers-json.http", "files/$DIAG/engine-ps.out", "files/$DIAG/container-ids.txt", "files/$DIAG/engine-candidates.json"],
    "PersistedStateJson": ["files/$DIAG/persisted-state-json.txt", "files/$DIAG/state-id-comparison.json"],
    "ProcessTable": ["files/$DIAG/process-table.txt"],
    "ListenerProbe": ["files/$DIAG/configured-ports.txt", "files/$DIAG/listener-probe.json", "files/$DIAG/listener-probe.txt", "files/$DIAG/proc-net-tcp.txt", "files/$DIAG/listeners-tool.txt"],
    "ContainerLogs": ["files/$DIAG/logs-<container-id>.out"]
  },
  "Unresolved": [
    "Rendered UI card container ID is not exported by this smoke entrypoint.",
    "Engine candidate selection, state.json ID comparison, and listener /proc/net/tcp evidence are recorded, but process/listener ownership and UI card agreement are not yet reduced to one same-container-ID proof.",
    "Negative cases for configured-port-only, stale listener/PID, duplicate name, and previous-container logs remain unproven on device."
  ]
}
JSON
cat "$LATEST"
exit 2
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

http_get engine-containers-before '/containers/json?all=1'
snapshot_ps process-before
snapshot_state_json persisted-state-before

record_cmd create-stop docker create --name "$STOP_NAME" "$IMAGE" sh -lc 'trap "" TERM; while true; do sleep 30; done'
STOP_CID="$(container_id_from_out "$DIAG/create-stop.out")"
record_cmd start-stop docker start "$STOP_CID"
http_get stop-inspect-before "/containers/$STOP_CID/json"
record_cmd logs-stop-before docker logs "$STOP_CID"
snapshot_ps process-after-stop-start
snapshot_state_json persisted-state-after-stop-start
record_cmd stop docker stop -t 1 "$STOP_CID"
http_get stop-inspect-after "/containers/$STOP_CID/json"
record_cmd logs-stop-after docker logs "$STOP_CID"
snapshot_ps process-after-stop
snapshot_state_json persisted-state-after-stop
record_cmd rm-stopped docker rm "$STOP_CID"
http_get stop-inspect-after-rm "/containers/$STOP_CID/json"
snapshot_ps process-after-rm-stopped
snapshot_state_json persisted-state-after-rm-stopped

record_cmd create-kill docker create --name "$KILL_NAME" "$IMAGE" sh -lc 'while true; do sleep 30; done'
KILL_CID="$(container_id_from_out "$DIAG/create-kill.out")"
record_cmd start-kill docker start "$KILL_CID"
http_get kill-inspect-before "/containers/$KILL_CID/json"
record_cmd logs-kill-before docker logs "$KILL_CID"
snapshot_ps process-after-kill-start
snapshot_state_json persisted-state-after-kill-start
record_cmd kill docker kill "$KILL_CID"
http_get kill-inspect-after "/containers/$KILL_CID/json"
record_cmd logs-kill-after docker logs "$KILL_CID"
snapshot_ps process-after-kill
snapshot_state_json persisted-state-after-kill
record_cmd rm-killed docker rm "$KILL_CID"
http_get kill-inspect-after-rm "/containers/$KILL_CID/json"
snapshot_ps process-after-rm-killed
snapshot_state_json persisted-state-after-rm-killed
http_get engine-containers-after '/containers/json?all=1'

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
    "PersistedStateJson": ["files/$DIAG/persisted-state-before.txt", "files/$DIAG/persisted-state-after-stop.txt", "files/$DIAG/persisted-state-after-rm-stopped.txt", "files/$DIAG/persisted-state-after-kill.txt", "files/$DIAG/persisted-state-after-rm-killed.txt"],
    "LifecycleLogs": ["files/$DIAG/stop.out", "files/$DIAG/stop.err", "files/$DIAG/kill.out", "files/$DIAG/kill.err", "files/$DIAG/rm-stopped.out", "files/$DIAG/rm-stopped.err", "files/$DIAG/rm-killed.out", "files/$DIAG/rm-killed.err", "files/$DIAG/cleanup-leftovers.out", "files/$DIAG/cleanup-leftovers.err"],
    "ContainerLogs": ["files/$DIAG/logs-stop-before.out", "files/$DIAG/logs-stop-after.out", "files/$DIAG/logs-kill-before.out", "files/$DIAG/logs-kill-after.out"]
  },
  "Unresolved": [
    "HTTP/CLI acknowledgement is recorded but not accepted as proof of teardown.",
    "The smoke does not yet map every observed process back to the Engine container ID/process tree.",
    "pdockerd implementation still needs strict stop/kill/rm verification before this can pass."
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

ui_engine_exec_it_selftest() {
  local container_ref="$1"
  echo "[pdocker smoke] ui self-test engine exec -it"
  run_as "rm -f files/pdocker/diagnostics/ui-it-selftest-latest.json" >/dev/null 2>&1 || true
  run_adb shell am start \
    -n "$PKG/$CLASS_PREFIX.MainActivity" \
    -a "$ACTION_PREFIX.action.SMOKE_UI_IT_SELFTEST" \
    --es container "$container_ref" >/dev/null
  local i
  for i in $(seq 1 30); do
    if run_as "test -f files/pdocker/diagnostics/ui-it-selftest-latest.json"; then
      run_as "cat files/pdocker/diagnostics/ui-it-selftest-latest.json"
      run_as "grep -q '\"Success\": true' files/pdocker/diagnostics/ui-it-selftest-latest.json"
      return 0
    fi
    sleep 1
  done
  echo "UI exec -it self-test did not produce diagnostics" >&2
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
  run_as 'files/pdocker-runtime/docker-bin/pdocker-direct --pdocker-memory-pager-managed-poc | grep -q "pager-managed-poc:result=ok"'
  echo "[pdocker smoke] compat transparent managed pager poc"
  run_as 'files/pdocker-runtime/docker-bin/pdocker-direct --pdocker-memory-pager-transparent-poc | grep -q "pager-transparent-poc:result=ok"'
  run_as '! test -e files/pdocker-runtime/docker-bin/proot'
else
  run_as 'files/pdocker-runtime/docker-bin/pdocker-direct --pdocker-direct-probe | grep -q "process-exec=0"'
fi

if [[ "$GPU_BENCH" -eq 1 ]]; then
  run_gpu_executor_bench
  run_gpu_bench
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
ui_engine_exec_it_selftest "$CID"
docker_cmd "cd pdocker/projects/$PROJECT && docker compose down"

echo "[pdocker smoke] checking UI-visible job state path"
run_as 'ls -l files/pdocker/jobs.json >/dev/null 2>&1 || true; ls -ld files/pdocker/projects/device-smoke files/pdocker-runtime'

echo "[pdocker smoke] passed"

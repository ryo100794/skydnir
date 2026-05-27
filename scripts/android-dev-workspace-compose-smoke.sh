#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

FLAVOR="${SKYDNIR_ANDROID_FLAVOR:-${PDOCKER_ANDROID_FLAVOR:-compat}}"
case "$FLAVOR" in
  compat) PKG="${PDOCKER_PACKAGE:-io.github.ryo100794.pdocker.compat}" ;;
  modern) PKG="${PDOCKER_PACKAGE:-io.github.ryo100794.pdocker}" ;;
  *) echo "SKYDNIR_ANDROID_FLAVOR/PDOCKER_ANDROID_FLAVOR must be compat or modern" >&2; exit 2 ;;
esac

ADB="${ADB:-adb}"
CLASS_PREFIX="io.github.ryo100794.pdocker"
ACTION_PREFIX="io.github.ryo100794.pdocker"
PROJECT="${PDOCKER_DEV_WORKSPACE_PROJECT:-default}"
SERVICE="${PDOCKER_DEV_WORKSPACE_SERVICE:-dev}"
CONTAINER="${PDOCKER_DEV_WORKSPACE_CONTAINER:-pdocker-dev}"
PORT="${PDOCKER_DEV_WORKSPACE_PORT:-18080}"
TIMEOUT_SECONDS="${PDOCKER_DEV_WORKSPACE_TIMEOUT_SECONDS:-900}"
OUT="${PDOCKER_DEV_WORKSPACE_SMOKE_ARTIFACT:-$ROOT/docs/test/dev-workspace-compose-latest.json}"
WORKDIR="${PDOCKER_DEV_WORKSPACE_SMOKE_WORKDIR:-$ROOT/tmp/dev-workspace-compose-smoke/$(date -u +%Y%m%dT%H%M%SZ)}"
EVIDENCE_DIR="$WORKDIR/evidence"
REQUIRED_EXTENSIONS="${PDOCKER_DEV_WORKSPACE_REQUIRED_EXTENSIONS:-Continue.continue OpenAI.chatgpt Anthropic.claude-code}"

mkdir -p "$EVIDENCE_DIR" "$(dirname "$OUT")"

remote_quote() {
  printf "'%s'" "$(printf "%s" "$1" | sed "s/'/'\\\\''/g")"
}

run_adb() {
  "$ADB" "$@"
}

run_as() {
  run_adb shell "run-as $PKG sh -c $(remote_quote "$1")"
}

http_body() {
  python3 -c 'import sys
data = sys.stdin.buffer.read()
split = data.find(b"\r\n\r\n")
if split >= 0:
    sys.stdout.buffer.write(data[split + 4:])
else:
    split = data.find(b"\n\n")
    sys.stdout.buffer.write(data[split + 2:] if split >= 0 else data)'
}

decode_engine_logs() {
  python3 -c 'import sys
data = sys.stdin.buffer.read()
split = data.find(b"\r\n\r\n")
if split >= 0:
    data = data[split + 4:]
out = bytearray()
idx = 0
while idx + 8 <= len(data):
    size = int.from_bytes(data[idx + 4:idx + 8], "big")
    idx += 8
    if size <= 0 or idx + size > len(data):
        idx -= 8
        break
    out.extend(data[idx:idx + size])
    idx += size
if idx < len(data):
    out.extend(data[idx:])
sys.stdout.buffer.write(out)'
}

engine_request() {
  local method="$1"
  local path="$2"
  local body="${3-}"
  local len
  if [[ "$#" -ge 3 ]]; then
    len="$(printf "%s" "$body" | wc -c | tr -d ' ')"
    run_as "cd files && { printf '%s %s HTTP/1.1\r\nHost: pdocker\r\nContent-Type: application/json\r\nContent-Length: %s\r\nConnection: close\r\n\r\n' $(remote_quote "$method") $(remote_quote "$path") $(remote_quote "$len"); printf %s $(remote_quote "$body"); } | toybox nc -U -W 60 pdocker/pdockerd.sock"
  else
    run_as "cd files && { printf '%s %s HTTP/1.1\r\nHost: pdocker\r\nConnection: close\r\n\r\n' $(remote_quote "$method") $(remote_quote "$path"); } | toybox nc -U -W 60 pdocker/pdockerd.sock"
  fi
}

engine_body() {
  engine_request "$@" | http_body
}

json_get_dev_workspace_cid() {
  python3 - "$CONTAINER" <<'PY'
import json
import sys

name = sys.argv[1]
try:
    data = json.load(sys.stdin)
except Exception:
    raise SystemExit(1)
for item in data:
    names = [n.lstrip("/") for n in item.get("Names") or []]
    cid = item.get("Id") or ""
    state = (item.get("State") or "").lower()
    status = (item.get("Status") or "").lower()
    if name in names or item.get("Names") == [name] or item.get("Name") == name:
        if state == "running" or status.startswith("up"):
            print(cid)
            raise SystemExit(0)
        raise SystemExit(2)
raise SystemExit(1)
PY
}

wait_for_socket() {
  run_adb shell am start \
    -n "$PKG/$CLASS_PREFIX.MainActivity" \
    -a "$ACTION_PREFIX.action.SMOKE_START" >/dev/null
  local i
  for i in $(seq 1 45); do
    if run_as 'test -S files/pdocker/pdockerd.sock' >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

trigger_compose_up() {
  run_as "rm -f files/pdocker/diagnostics/ui-rendered-service-truth-latest.json" >/dev/null 2>&1 || true
  run_adb shell am start \
    -n "$PKG/$CLASS_PREFIX.MainActivity" \
    -a "$ACTION_PREFIX.action.SMOKE_COMPOSE_UP" \
    --es project "$PROJECT" >/dev/null
}

wait_for_running_container() {
  local deadline now rc cid
  deadline=$(( $(date +%s) + TIMEOUT_SECONDS ))
  while true; do
    engine_body GET "/containers/json?all=1" >"$EVIDENCE_DIR/containers-json-latest.json" 2>"$EVIDENCE_DIR/containers-json-latest.err" || true
    set +e
    cid="$(json_get_dev_workspace_cid <"$EVIDENCE_DIR/containers-json-latest.json" 2>/dev/null)"
    rc=$?
    set -e
    if [[ "$rc" -eq 0 && -n "$cid" ]]; then
      printf '%s' "$cid" >"$EVIDENCE_DIR/running-container-id.txt"
      printf '%s\n' "$cid"
      return 0
    fi
    now="$(date +%s)"
    if [[ "$now" -ge "$deadline" ]]; then
      return 1
    fi
    sleep 3
  done
}

engine_exec_capture() {
  local cid="$1"
  local label="$2"
  local shell_cmd="$3"
  local create_body create_resp exec_id start_body
  create_body="$(python3 - "$shell_cmd" <<'PY'
import json
import sys
print(json.dumps({
    "AttachStdout": True,
    "AttachStderr": True,
    "Tty": False,
    "Cmd": ["/bin/sh", "-lc", sys.argv[1]],
}, separators=(",", ":")))
PY
)"
  create_resp="$(engine_body POST "/containers/$cid/exec" "$create_body")"
  printf '%s\n' "$create_resp" >"$EVIDENCE_DIR/$label-create.json"
  exec_id="$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("Id",""))' <"$EVIDENCE_DIR/$label-create.json")"
  if [[ -z "$exec_id" ]]; then
    return 1
  fi
  start_body='{"Detach":false,"Tty":false}'
  engine_request POST "/exec/$exec_id/start" "$start_body" | decode_engine_logs >"$EVIDENCE_DIR/$label.out" 2>"$EVIDENCE_DIR/$label.err" || true
  engine_body GET "/exec/$exec_id/json" >"$EVIDENCE_DIR/$label-inspect.json" 2>"$EVIDENCE_DIR/$label-inspect.err" || true
}

collect_remote_evidence() {
  local cid="$1"
  local remote_diag="files/pdocker/diagnostics/dev-workspace-compose-smoke"
  run_as "rm -rf $remote_diag && mkdir -p $remote_diag/job-logs
cp files/pdocker/diagnostics/ui-rendered-service-truth-latest.json $remote_diag/ui-rendered-service-truth-latest.json 2>$remote_diag/ui-rendered-service-truth-latest.err || : >$remote_diag/ui-rendered-service-truth-latest.missing
cp files/pdocker/logs/jobs/*.log $remote_diag/job-logs/ 2>$remote_diag/job-logs.err || true
cat /proc/net/tcp > $remote_diag/proc-net-tcp.txt 2>$remote_diag/proc-net-tcp.err || true
(ps -A -o PID,PPID,USER,NAME,ARGS 2>/dev/null || ps -A -ef 2>/dev/null || ps 2>/dev/null) > $remote_diag/process-table.txt 2>$remote_diag/process-table.err || true
{ printf 'GET / HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n'; } | toybox nc -w 5 127.0.0.1 $PORT > $remote_diag/listener-http.raw 2>$remote_diag/listener-http.err
printf '%s' \$? > $remote_diag/listener-http.rc" >/dev/null 2>&1 || true
  engine_body GET "/containers/$cid/json" >"$EVIDENCE_DIR/container-inspect.json" 2>"$EVIDENCE_DIR/container-inspect.err" || true
  engine_request GET "/containers/$cid/logs?stdout=1&stderr=1&tail=200" | decode_engine_logs >"$EVIDENCE_DIR/container-logs.txt" 2>"$EVIDENCE_DIR/container-logs.err" || true
  run_adb exec-out run-as "$PKG" sh -c "cd files && tar -cf - pdocker/diagnostics/dev-workspace-compose-smoke 2>/dev/null" >"$WORKDIR/device-dev-workspace-evidence.tar" 2>"$WORKDIR/device-dev-workspace-evidence.tar.err" || true
  tar -xf "$WORKDIR/device-dev-workspace-evidence.tar" -C "$EVIDENCE_DIR" >/dev/null 2>&1 || true
}

write_artifact() {
  local flow_rc="$1"
  local failures_file="$EVIDENCE_DIR/failures.txt"
  printf '%s\n' "${FAILURES[@]:-}" >"$failures_file"
  DEV_WORKSPACE_ROOT="$ROOT" \
  DEV_WORKSPACE_OUT="$OUT" \
  DEV_WORKSPACE_EVIDENCE_DIR="$EVIDENCE_DIR" \
  DEV_WORKSPACE_PROJECT="$PROJECT" \
  DEV_WORKSPACE_SERVICE="$SERVICE" \
  DEV_WORKSPACE_CONTAINER="$CONTAINER" \
  DEV_WORKSPACE_PORT="$PORT" \
  DEV_WORKSPACE_REQUIRED_EXTENSIONS="$REQUIRED_EXTENSIONS" \
  DEV_WORKSPACE_FLOW_RC="$flow_rc" \
  python3 <<'PY'
import json
import os
from pathlib import Path

evidence = Path(os.environ["DEV_WORKSPACE_EVIDENCE_DIR"])
out = Path(os.environ["DEV_WORKSPACE_OUT"])
container = os.environ["DEV_WORKSPACE_CONTAINER"]
port = int(os.environ["DEV_WORKSPACE_PORT"])
required_extensions = os.environ["DEV_WORKSPACE_REQUIRED_EXTENSIONS"].split()

def read_text(name):
    path = evidence / name
    if not path.is_file():
        return ""
    return path.read_text(errors="replace")

def read_json(name, default):
    text = read_text(name)
    if not text.strip():
        return default
    try:
        return json.loads(text)
    except Exception:
        return default

failures = [line for line in read_text("failures.txt").splitlines() if line.strip()]
containers = read_json("containers-json-latest.json", [])
running = None
for item in containers if isinstance(containers, list) else []:
    names = [n.lstrip("/") for n in item.get("Names") or []]
    if container in names:
        running = item
        break
running_id = (running or {}).get("Id") or read_text("running-container-id.txt").strip()
running_state = ((running or {}).get("State") or "").lower()
running_status = ((running or {}).get("Status") or "").lower()
running_ok = bool(running_id) and (running_state == "running" or running_status.startswith("up"))

container_inspect = read_json("container-inspect.json", {})
inspect_id = container_inspect.get("Id") if isinstance(container_inspect, dict) else ""
inspect_name = (container_inspect.get("Name") or "").lstrip("/") if isinstance(container_inspect, dict) else ""
inspect_state = container_inspect.get("State") if isinstance(container_inspect, dict) else {}
inspect_running = bool(inspect_state.get("Running")) if isinstance(inspect_state, dict) else False
engine_state_current = bool(
    running_id
    and inspect_id
    and (running_id == inspect_id or running_id.startswith(inspect_id) or inspect_id.startswith(running_id))
    and inspect_name == container
    and inspect_running
    and running_ok
)

extension_output = read_text("extensions.out")
extensions_present = {
    ext: any(line.strip().lower() == ext.lower() for line in extension_output.splitlines())
    for ext in required_extensions
}
extensions_configured = bool(required_extensions)
extension_inspect = read_json("extensions-inspect.json", {})
extension_exit = extension_inspect.get("ExitCode")
extensions_ok = (not extensions_configured) or (all(extensions_present.values()) and extension_exit == 0)

listener_rc_text = read_text("pdocker/diagnostics/dev-workspace-compose-smoke/listener-http.rc").strip()
listener_raw = read_text("pdocker/diagnostics/dev-workspace-compose-smoke/listener-http.raw")
listener_status_line = next((line.strip() for line in listener_raw.splitlines() if line.upper().startswith("HTTP/")), "")
try:
    listener_http_status_code = int(listener_status_line.split()[1])
except Exception:
    listener_http_status_code = None
listener_ok = listener_rc_text == "0" and bool(listener_raw.strip())
code_server_http_ok = listener_ok and listener_http_status_code is not None and 200 <= listener_http_status_code <= 399

ui_truth = read_json("pdocker/diagnostics/dev-workspace-compose-smoke/ui-rendered-service-truth-latest.json", {})
rendered_cards = ui_truth.get("RenderedCards") if isinstance(ui_truth, dict) else []
target_cards = []
if isinstance(rendered_cards, list):
    for card in rendered_cards:
        if not isinstance(card, dict):
            continue
        if (
            card.get("ContainerName") == container
            or card.get("ServiceName") == os.environ["DEV_WORKSPACE_SERVICE"]
            or (running_id and str(card.get("EngineContainerId") or "").startswith(running_id[:12]))
            or (running_id and str(card.get("EngineContainerId") or "") == running_id)
        ):
            target_cards.append(card)
stale_or_unknown_cards = [
    card for card in target_cards
    if card.get("TruthState") in {"unknown", "stale", "ambiguous"}
]
ui_current_match = any(
    card.get("TruthState") == "current"
    and str(card.get("EngineContainerId") or "")
    and (running_id.startswith(str(card.get("EngineContainerId"))) or str(card.get("EngineContainerId")).startswith(running_id[:12]))
    for card in target_cards
)
ui_truth_ok = bool(ui_current_match) and not stale_or_unknown_cards

job_text = "\n".join(
    path.read_text(errors="replace")
    for path in sorted((evidence / "pdocker/diagnostics/dev-workspace-compose-smoke/job-logs").glob("*.log"))
)
build_started = f"Service {os.environ['DEV_WORKSPACE_SERVICE']} Building" in job_text
build_completed = (
    "Successfully tagged pdocker/dev-workspace:latest" in job_text
    or "Using image cache for pdocker/dev-workspace:latest" in job_text
)
container_create_seen = f"Container {container} Creating" in job_text
container_start_seen = f"Container {container} Starting" in job_text
container_started_seen = f"Container {container} Started" in job_text
service_url_seen = f"Service URL VS Code http://127.0.0.1:{port}/" in job_text or f"Service URL VS Code http://127.0.0.1:{port}" in job_text
build_failed = any(term in job_text.lower() for term in ["error: build failed", "build failed", "runtime blocked"])
build_run_ok = build_started and build_completed and container_create_seen and container_start_seen and container_started_seen and running_ok and not build_failed

checks = {
    "build_run": {
        "ok": build_run_ok,
        "build_started_observed": build_started,
        "build_completed_observed": build_completed,
        "container_create_observed": container_create_seen,
        "container_start_observed": container_start_seen,
        "container_started_observed": container_started_seen,
        "service_url_observed": service_url_seen,
        "build_failed_marker_observed": build_failed,
        "running_container_id": running_id or None,
        "container_state": running_state or None,
        "container_status": running_status or None,
    },
    "engine_state": {
        "ok": engine_state_current,
        "container": container,
        "running_container_id": running_id or None,
        "inspect_id": inspect_id or None,
        "inspect_name": inspect_name or None,
        "inspect_running": inspect_running,
        "containers_json_state": running_state or None,
        "containers_json_status": running_status or None,
    },
    "listener": {
        "ok": listener_ok,
        "port": port,
        "http_probe_exit_code": listener_rc_text or None,
        "http_status_line": listener_status_line or None,
        "http_status_code": listener_http_status_code,
        "response_preview": listener_raw[:200],
    },
    "code_server_http": {
        "ok": code_server_http_ok,
        "url": f"http://127.0.0.1:{port}/",
        "expected_status": "HTTP 2xx/3xx from code-server",
        "http_status_line": listener_status_line or None,
        "http_status_code": listener_http_status_code,
    },
    "extensions": {
        "ok": extensions_ok,
        "configured": extensions_configured,
        "required": required_extensions,
        "present": extensions_present,
        "exec_exit_code": extension_exit,
        "output_preview": extension_output[:1000],
    },
    "ui_truth": {
        "ok": ui_truth_ok,
        "target_card_count": len(target_cards),
        "current_match": bool(ui_current_match),
        "stale_or_unknown_target_card_count": len(stale_or_unknown_cards),
        "target_cards": target_cards,
        "summary": ui_truth.get("Summary") if isinstance(ui_truth, dict) else None,
    },
}

all_checks_ok = all(item["ok"] for item in checks.values())
success = all_checks_ok and not failures and int(os.environ["DEV_WORKSPACE_FLOW_RC"]) == 0
artifact = {
    "schema": "pdocker.android.dev-workspace.compose-smoke.v1",
    "success": success,
    "status": "pass" if success else "fail",
    "project": os.environ["DEV_WORKSPACE_PROJECT"],
    "service": os.environ["DEV_WORKSPACE_SERVICE"],
    "container": container,
    "port": port,
    "required_extensions": required_extensions,
    "flow_exit_code": int(os.environ["DEV_WORKSPACE_FLOW_RC"]),
    "failures": failures,
    "checks": checks,
    "evidence": {
        "host_evidence_dir": str(evidence),
        "engine_containers_json": str(evidence / "containers-json-latest.json"),
        "engine_container_inspect": str(evidence / "container-inspect.json"),
        "engine_container_logs": str(evidence / "container-logs.txt"),
        "extension_exec_output": str(evidence / "extensions.out"),
        "extension_exec_inspect": str(evidence / "extensions-inspect.json"),
        "ui_rendered_service_truth": str(evidence / "pdocker/diagnostics/dev-workspace-compose-smoke/ui-rendered-service-truth-latest.json"),
        "listener_http": str(evidence / "pdocker/diagnostics/dev-workspace-compose-smoke/listener-http.raw"),
        "proc_net_tcp": str(evidence / "pdocker/diagnostics/dev-workspace-compose-smoke/proc-net-tcp.txt"),
        "process_table": str(evidence / "pdocker/diagnostics/dev-workspace-compose-smoke/process-table.txt"),
        "job_logs_dir": str(evidence / "pdocker/diagnostics/dev-workspace-compose-smoke/job-logs"),
    },
    "acceptance_contract": {
        "no_fake_success": "The script exits zero only when compose/build/run, current Engine state for pdocker-dev, port 18080 listener, code-server HTTP reachability, configured required extensions, and UI service-truth all pass.",
        "extensions_if_configured": "Required extension evidence is enforced when PDOCKER_DEV_WORKSPACE_REQUIRED_EXTENSIONS is non-empty; the default requires Continue.continue, OpenAI.chatgpt, and Anthropic.claude-code.",
        "ui_truth": "TruthState stale/unknown/ambiguous is never accepted as success; a current UI card must match the running Engine container ID.",
    },
}
out.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
print(out)
raise SystemExit(0 if success else 1)
PY
}

FAILURES=()
flow_rc=0
cid=""

if ! wait_for_socket; then
  FAILURES+=("pdockerd socket did not appear")
  flow_rc=1
else
  if ! trigger_compose_up; then
    FAILURES+=("failed to trigger ACTION_SMOKE_COMPOSE_UP for project $PROJECT")
    flow_rc=1
  else
    if ! cid="$(wait_for_running_container)"; then
      FAILURES+=("compose up did not produce a running $CONTAINER container before timeout")
      flow_rc=1
    else
      collect_remote_evidence "$cid"
      engine_exec_capture "$cid" "extensions" "set -e; command -v code-server; code-server --list-extensions; node --version; npm --version" || {
        FAILURES+=("failed to collect code-server extension evidence through Engine exec")
        flow_rc=1
      }
      collect_remote_evidence "$cid"
    fi
  fi
fi

write_artifact "$flow_rc"

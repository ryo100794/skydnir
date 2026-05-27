#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ADB="${ADB:-adb}"
PKG="${SKYDNIR_ANDROID_PACKAGE:-${SKYDNIR_PACKAGE:-${PDOCKER_ANDROID_PACKAGE:-io.github.ryo100794.pdocker.compat}}}"
CLASS_PREFIX="io.github.ryo100794.pdocker"
ACTION_PREFIX="io.github.ryo100794.pdocker"
PROJECT="${PDOCKER_TEST_SUITE_PROJECT:-pdocker/projects/pdocker-test-suite}"
PROJECT_NAME="${PDOCKER_TEST_SUITE_PROJECT_NAME:-${PROJECT##*/}}"
CONTAINER="${PDOCKER_TEST_SUITE_CONTAINER:-pdocker-test-suite}"
IMAGE="${PDOCKER_TEST_SUITE_IMAGE:-pdocker/test-suite:latest}"
SCENARIO="${PDOCKER_TEST_SUITE_SCENARIO:-all}"
STAGE_TEMPLATE="${PDOCKER_TEST_SUITE_STAGE_TEMPLATE:-1}"
REFRESH_TEMPLATE="${PDOCKER_TEST_SUITE_REFRESH_TEMPLATE:-1}"
TEMPLATE_ROOT="$ROOT/app/src/main/assets/project-library/pdocker-test-suite"
STAGE_TAR="/tmp/pdocker-test-suite-template.tar"

remote_quote() {
  printf "'%s'" "$(printf "%s" "$1" | sed "s/'/'\\\\''/g")"
}

run_as() {
  "$ADB" shell "run-as $PKG sh -c $(remote_quote "$1")"
}

start_daemon_for_test() {
  "$ADB" shell am broadcast \
    -n "$PKG/$CLASS_PREFIX.PdockerdDebugReceiver" \
    -a "$ACTION_PREFIX.action.SMOKE_START" >/dev/null 2>&1 || true
}

wait_for_engine() {
  start_daemon_for_test
  for i in $(seq 1 45); do
    if run_as 'cd files && test -S pdocker/pdockerd.sock && { printf "GET /_ping HTTP/1.1\r\nHost: pdocker\r\nConnection: close\r\n\r\n"; } | toybox nc -U pdocker/pdockerd.sock | grep -q OK' >/dev/null 2>&1; then
      return 0
    fi
    if [ $((i % 5)) -eq 0 ]; then
      start_daemon_for_test
    fi
    sleep 1
  done
  echo "pdockerd socket did not appear" >&2
  return 1
}

urlencode() {
  python3 - "$1" <<'PY'
import sys
import urllib.parse
print(urllib.parse.quote(sys.argv[1], safe=""))
PY
}

http_body() {
  python3 -c 'import sys
data = sys.stdin.buffer.read()
split = data.find(b"\r\n\r\n")
if split < 0:
    split = data.find(b"\n\n")
    offset = 2
else:
    offset = 4
body = data[split + offset:] if split >= 0 else data
sys.stdout.buffer.write(body)'
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
  method="$1"
  path="$2"
  body="${3-}"
  if [ "$#" -ge 3 ]; then
    len="$(printf "%s" "$body" | wc -c | tr -d ' ')"
    run_as "cd files && { printf '%s %s HTTP/1.1\r\nHost: pdocker\r\nContent-Type: application/json\r\nContent-Length: %s\r\nConnection: close\r\n\r\n' $(remote_quote "$method") $(remote_quote "$path") $(remote_quote "$len"); printf %s $(remote_quote "$body"); } | toybox nc -U -W 30 pdocker/pdockerd.sock"
  else
    run_as "cd files && { printf '%s %s HTTP/1.1\r\nHost: pdocker\r\nConnection: close\r\n\r\n' $(remote_quote "$method") $(remote_quote "$path"); } | toybox nc -U -W 30 pdocker/pdockerd.sock"
  fi
}

engine_body() {
  engine_request "$@" | http_body
}

parse_engine_id() {
  python3 -c 'import json,sys
body = sys.stdin.read()
try:
    data = json.loads(body)
except Exception as exc:
    print(f"Engine response was not JSON: {exc}: {body[:500]}", file=sys.stderr)
    raise SystemExit(1)
ident = data.get("Id")
if not ident:
    print(f"Engine response did not include Id: {json.dumps(data, ensure_ascii=False)[:500]}", file=sys.stderr)
    raise SystemExit(1)
print(ident)'
}

exec_payload() {
  python3 - "$SCENARIO" <<'PY'
import json
import sys
scenario = sys.argv[1]
print(json.dumps({
    "Cmd": ["run-pdocker-test-suite", "--scenario", scenario],
    "AttachStdout": True,
    "AttachStderr": True,
    "Tty": False,
}, separators=(",", ":")))
PY
}

exec_start_payload() {
  printf '{"Detach":false,"Tty":false}'
}

compose_up_project() {
  echo "[pdocker test suite] compose up/build project $PROJECT_NAME through app route"
  engine_request DELETE "/containers/$(urlencode "$CONTAINER")?force=true" >/dev/null || true
  "$ADB" shell am start \
    -n "$PKG/$CLASS_PREFIX.MainActivity" \
    -a "$ACTION_PREFIX.action.SMOKE_COMPOSE_UP" \
    --es project "$PROJECT_NAME" >/dev/null
}

wait_for_compose_container() {
  python_check='import json,sys
name=sys.argv[1]
body=sys.stdin.read()
try:
    data=json.loads(body or "[]")
except Exception:
    raise SystemExit(1)
for item in data:
    names=item.get("Names") or []
    if ("/"+name) in names or name in [n.lstrip("/") for n in names]:
        state=(item.get("State") or "").lower()
        status=(item.get("Status") or "").lower()
        if state == "running" or status.startswith("up"):
            print(item.get("Id") or "")
            raise SystemExit(0)
        raise SystemExit(2)
raise SystemExit(1)'
  i=0
  while [ "$i" -lt 180 ]; do
    set +e
    body="$(engine_body GET "/containers/json?all=1")"
    cid="$(printf "%s" "$body" | python3 -c "$python_check" "$CONTAINER" 2>/dev/null)"
    rc=$?
    set -e
    if [ "$rc" -eq 0 ] && [ -n "$cid" ]; then
      printf '%s\n' "$cid"
      return 0
    fi
    if [ "$rc" -eq 2 ]; then
      echo "[pdocker test suite] container exists but is not running yet" >&2
    fi
    sleep 2
    i=$((i + 1))
  done
  echo "compose up did not produce a running $CONTAINER container" >&2
  run_as "tail -120 files/pdocker/logs/jobs/*.log 2>/dev/null || true" >&2 || true
  return 1
}

if [[ "$STAGE_TEMPLATE" != "0" ]]; then
  if [[ "$REFRESH_TEMPLATE" == "1" ]] || ! run_as "test -f $(printf "%q" "files/$PROJECT/compose.yaml")"; then
    echo "[pdocker test suite] stage bundled template to files/$PROJECT"
    tar -C "$TEMPLATE_ROOT/.." -cf "$STAGE_TAR" pdocker-test-suite
    "$ADB" push "$STAGE_TAR" /data/local/tmp/pdocker-test-suite-template.tar >/dev/null
    "$ADB" shell "chmod 644 /data/local/tmp/pdocker-test-suite-template.tar" >/dev/null || true
    run_as "rm -rf files/$(printf "%q" "$PROJECT") && mkdir -p files/pdocker/projects && tar -xf /data/local/tmp/pdocker-test-suite-template.tar -C files/pdocker/projects"
  fi
fi

wait_for_engine

compose_up_project
cid="$(wait_for_compose_container)"

echo "[pdocker test suite] exec run-pdocker-test-suite --scenario $SCENARIO"
exec_body="$(engine_body POST "/containers/$cid/exec" "$(exec_payload)")"
exec_id="$(printf "%s" "$exec_body" | parse_engine_id)"
engine_request POST "/exec/$exec_id/start" "$(exec_start_payload)" | decode_engine_logs
exec_json="$(engine_body GET "/exec/$exec_id/json")"
python3 - "$exec_json" <<'PY'
import json
import sys
data = json.loads(sys.argv[1])
code = int(data.get("ExitCode") or 0)
if code:
    print(f"pdocker test suite exec failed with exit code {code}", file=sys.stderr)
raise SystemExit(code)
PY

echo "[pdocker test suite] latest in-container summary"
run_as "cat files/$(printf "%q" "$PROJECT")/reports/latest.json"

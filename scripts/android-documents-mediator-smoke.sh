#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

FLAVOR="${PDOCKER_ANDROID_FLAVOR:-compat}"
case "$FLAVOR" in
  compat) PKG="${PDOCKER_PACKAGE:-io.github.ryo100794.pdocker.compat}" ;;
  modern) PKG="${PDOCKER_PACKAGE:-io.github.ryo100794.pdocker}" ;;
  *) echo "PDOCKER_ANDROID_FLAVOR must be compat or modern" >&2; exit 2 ;;
esac

ADB="${ADB:-adb}"
ADB_SERIAL="${ANDROID_SERIAL:-${ADB_SERIAL:-}}"
CLASS_PREFIX="io.github.ryo100794.pdocker"
ACTION_PREFIX="io.github.ryo100794.pdocker"
OUT="${PDOCKER_SAF_DIRECT_OUTPUT_ARTIFACT:-$ROOT/docs/test/saf-direct-output-latest.json}"
WORKDIR="${PDOCKER_SAF_DIRECT_OUTPUT_WORKDIR:-$ROOT/tmp/saf-direct-output/$(date -u +%Y%m%dT%H%M%SZ)}"
EVIDENCE_DIR="$WORKDIR/evidence"
CONTAINER="${PDOCKER_SAF_DIRECT_OUTPUT_CONTAINER:-}"
REQUIRE_CONTAINER="${PDOCKER_SAF_DIRECT_OUTPUT_REQUIRE_CONTAINER:-1}"
DOCUMENTS_MOUNT="${PDOCKER_DOCUMENTS_MOUNT:-/documents}"
TIMEOUT_SECONDS="${PDOCKER_SAF_DIRECT_OUTPUT_TIMEOUT_SECONDS:-60}"
CASE_NAME="${PDOCKER_SAF_DIRECT_OUTPUT_CASE:-saf-direct-$(date -u +%Y%m%dT%H%M%SZ)}"
PAYLOAD="saf-direct-payload-$CASE_NAME"

WRITE_RELATIVE="pdocker-exports/$CASE_NAME/nested/latest.log"
RENAME_SOURCE_RELATIVE="pdocker-exports/$CASE_NAME/nested/rename-source.log"
RENAME_TARGET_RELATIVE="pdocker-exports/$CASE_NAME/nested/renamed.log"
UNLINK_RELATIVE="pdocker-exports/$CASE_NAME/nested/unlink-target.log"
INVALID_DIRECT_WRITE_TARGET="../escape-phase2.txt"

mkdir -p "$EVIDENCE_DIR" "$(dirname "$OUT")"

run_adb() {
  if [[ -n "$ADB_SERIAL" ]]; then
    "$ADB" -s "$ADB_SERIAL" "$@"
  else
    "$ADB" "$@"
  fi
}

remote_quote() {
  printf "'%s'" "$(printf "%s" "$1" | sed "s/'/'\\\\''/g")"
}

run_as() {
  run_adb shell "run-as $PKG sh -c $(remote_quote "$1")"
}

http_body() {
  python3 -c 'import sys
data = sys.stdin.buffer.read()
for sep in (b"\r\n\r\n", b"\n\n"):
    idx = data.find(sep)
    if idx >= 0:
        sys.stdout.buffer.write(data[idx + len(sep):])
        break
else:
    sys.stdout.buffer.write(data)'
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

validate_relative_path() {
  local path="$1"
  case "$path" in
    ""|/*|*'\'*|../*|*/../*|*/..|.|..|*//*) return 1 ;;
    *) return 0 ;;
  esac
}

sidecar_name_for() {
  printf '%s.json' "$(printf '%s' "$1" | tr '/' '_')"
}

write_text_file() {
  local path="$1"
  local text="$2"
  printf '%s\n' "$text" >"$path"
}

collect_settings() {
  run_as "cat shared_prefs/pdocker-settings.xml 2>/dev/null || true" >"$EVIDENCE_DIR/pdocker-settings.xml" 2>"$EVIDENCE_DIR/pdocker-settings.err" || true
  python3 - "$EVIDENCE_DIR/pdocker-settings.xml" >"$EVIDENCE_DIR/selected-host-path.txt" <<'PY'
import re
import sys
text = open(sys.argv[1], errors="replace").read()
m = re.search(r'name="documents\.hostPath">([^<]+)<', text)
print(m.group(1) if m else "")
PY
}

collect_engine_documents_status() {
  if run_as 'test -S files/pdocker/pdockerd.sock' >/dev/null 2>&1; then
    engine_body GET "/system/documents/status" >"$EVIDENCE_DIR/engine-documents-status.json" 2>"$EVIDENCE_DIR/engine-documents-status.err" || true
  else
    write_text_file "$EVIDENCE_DIR/engine-documents-status.json" '{}'
    write_text_file "$EVIDENCE_DIR/engine-documents-status.err" 'pdockerd socket was not available'
  fi
}

start_app_and_daemon() {
  run_adb shell am start \
    -n "$PKG/$CLASS_PREFIX.MainActivity" \
    -a "$ACTION_PREFIX.action.SMOKE_START" >/dev/null || true
  local i
  for i in $(seq 1 45); do
    if run_as 'test -S files/pdocker/pdockerd.sock' >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

engine_exec_capture() {
  local cid="$1"
  local label="$2"
  local shell_cmd="$3"
  local create_body create_resp exec_id
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
  exec_id="$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("Id",""))' <"$EVIDENCE_DIR/$label-create.json" 2>/dev/null || true)"
  if [[ -z "$exec_id" ]]; then
    write_text_file "$EVIDENCE_DIR/$label.err" "failed to create exec"
    return 1
  fi
  engine_request POST "/exec/$exec_id/start" '{"Detach":false,"Tty":false}' | decode_engine_logs >"$EVIDENCE_DIR/$label.out" 2>"$EVIDENCE_DIR/$label.err" || true
  engine_body GET "/exec/$exec_id/json" >"$EVIDENCE_DIR/$label-inspect.json" 2>"$EVIDENCE_DIR/$label-inspect.err" || true
}

run_container_documents_cases() {
  local cid="$1"
  local cmd
  cmd="$(python3 - "$DOCUMENTS_MOUNT" "$PAYLOAD" "$WRITE_RELATIVE" "$RENAME_SOURCE_RELATIVE" "$RENAME_TARGET_RELATIVE" "$UNLINK_RELATIVE" <<'PY'
import shlex
import sys
from pathlib import PurePosixPath
mount, payload, write_rel, rename_src, rename_dst, unlink_rel = sys.argv[1:]
def q(v): return shlex.quote(v)
def full(rel): return mount.rstrip("/") + "/" + rel
write_path = full(write_rel)
rename_src_path = full(rename_src)
rename_dst_path = full(rename_dst)
unlink_path = full(unlink_rel)
lines = [
    "set -eu",
    f"test -d {q(mount)}",
    f"mkdir -p {q(str(PurePosixPath(write_path).parent))}",
    f"printf '%s\\n' {q(payload)} > {q(write_path)}",
    f"printf '%s\\n' {q(payload)} > {q(rename_src_path)}",
    f"mv {q(rename_src_path)} {q(rename_dst_path)}",
    f"printf '%s\\n' {q(payload)} > {q(unlink_path)}",
    f"rm {q(unlink_path)}",
    f"test ! -e {q(unlink_path)}",
    f"stat {q(write_path)} {q(rename_dst_path)} 2>/dev/null || ls -l {q(write_path)} {q(rename_dst_path)}",
    "printf 'container-documents-cases-ok\\n'",
]
print("\n".join(lines))
PY
)"
  engine_exec_capture "$cid" "container-documents-cases" "$cmd"
}

collect_device_documents_evidence() {
  local selected_host="$1"
  local remote_diag="files/pdocker/diagnostics/saf-direct-output"
  local sidecar_write sidecar_rename sidecar_unlink
  sidecar_write="$(sidecar_name_for "$WRITE_RELATIVE")"
  sidecar_rename="$(sidecar_name_for "$RENAME_TARGET_RELATIVE")"
  sidecar_unlink="$(sidecar_name_for "$UNLINK_RELATIVE")"
  run_as "rm -rf $remote_diag && mkdir -p $remote_diag
cat $(remote_quote "$selected_host/$WRITE_RELATIVE") > $remote_diag/write.payload 2>$remote_diag/write.err || true
cat $(remote_quote "$selected_host/$RENAME_TARGET_RELATIVE") > $remote_diag/rename.payload 2>$remote_diag/rename.err || true
test ! -e $(remote_quote "$selected_host/$UNLINK_RELATIVE"); printf '%s' \$? > $remote_diag/unlink-absent.rc
test -e files/pdocker/documents-saf-mediated/mirror/$(remote_quote "$WRITE_RELATIVE"); printf '%s' \$? > $remote_diag/write-mirror-exists.rc
cat files/pdocker/documents-saf-mediated/sidecar/$(remote_quote "$sidecar_write") > $remote_diag/write.sidecar.json 2>$remote_diag/write.sidecar.err || true
cat files/pdocker/documents-saf-mediated/sidecar/$(remote_quote "$sidecar_rename") > $remote_diag/rename.sidecar.json 2>$remote_diag/rename.sidecar.err || true
cat files/pdocker/documents-saf-mediated/sidecar/$(remote_quote "$sidecar_unlink") > $remote_diag/unlink.sidecar.json 2>$remote_diag/unlink.sidecar.err || true
cat /proc/net/unix > $remote_diag/proc-net-unix.txt 2>$remote_diag/proc-net-unix.err || true" >/dev/null 2>&1 || true
  run_adb exec-out run-as "$PKG" sh -c "cd files && tar -cf - pdocker/diagnostics/saf-direct-output 2>/dev/null" >"$WORKDIR/device-saf-direct-output.tar" 2>"$WORKDIR/device-saf-direct-output.tar.err" || true
  tar -xf "$WORKDIR/device-saf-direct-output.tar" -C "$EVIDENCE_DIR" >/dev/null 2>&1 || true
}

run_invalid_direct_write_case() {
  local src="/data/data/$PKG/files/pdocker/diagnostics/saf-direct-output/invalid-direct-write-source.txt"
  run_as "mkdir -p files/pdocker/diagnostics/saf-direct-output
printf '%s\n' 'invalid-direct-write-source' > files/pdocker/diagnostics/saf-direct-output/invalid-direct-write-source.txt
rm -f files/pdocker/diagnostics/saf-write-latest.json" >/dev/null 2>&1 || true
  run_adb shell am start \
    -n "$PKG/$CLASS_PREFIX.MainActivity" \
    -a "$ACTION_PREFIX.action.SMOKE_DOCUMENTS_WRITE_FILE" \
    --es source "$src" \
    --es target "$INVALID_DIRECT_WRITE_TARGET" \
    --es mimeType "text/plain" >/dev/null || true
  local i
  for i in $(seq 1 30); do
    run_as "cat files/pdocker/diagnostics/saf-write-latest.json 2>/dev/null || true" >"$EVIDENCE_DIR/saf-write-invalid-target.json" 2>"$EVIDENCE_DIR/saf-write-invalid-target.err" || true
    if grep -q '"PathValidationPolicy"' "$EVIDENCE_DIR/saf-write-invalid-target.json" 2>/dev/null; then
      return 0
    fi
    sleep 1
  done
  return 1
}

write_artifact() {
  local selected_host="$1"
  local flow_rc="$2"
  SAF_DIRECT_ROOT="$ROOT" \
  SAF_DIRECT_OUT="$OUT" \
  SAF_DIRECT_EVIDENCE="$EVIDENCE_DIR" \
  SAF_DIRECT_SELECTED_HOST="$selected_host" \
  SAF_DIRECT_CONTAINER="$CONTAINER" \
  SAF_DIRECT_REQUIRE_CONTAINER="$REQUIRE_CONTAINER" \
  SAF_DIRECT_DOCUMENTS_MOUNT="$DOCUMENTS_MOUNT" \
  SAF_DIRECT_CASE="$CASE_NAME" \
  SAF_DIRECT_PAYLOAD="$PAYLOAD" \
  SAF_DIRECT_FLOW_RC="$flow_rc" \
  SAF_DIRECT_VALIDATION_RC="${3:-0}" \
  SAF_DIRECT_WRITE_RELATIVE="$WRITE_RELATIVE" \
  SAF_DIRECT_RENAME_TARGET_RELATIVE="$RENAME_TARGET_RELATIVE" \
  SAF_DIRECT_UNLINK_RELATIVE="$UNLINK_RELATIVE" \
  SAF_DIRECT_INVALID_TARGET="$INVALID_DIRECT_WRITE_TARGET" \
  python3 <<'PY'
import json
import os
from pathlib import Path

evidence = Path(os.environ["SAF_DIRECT_EVIDENCE"])
out = Path(os.environ["SAF_DIRECT_OUT"])
payload = os.environ["SAF_DIRECT_PAYLOAD"]
selected_host = os.environ["SAF_DIRECT_SELECTED_HOST"]
container = os.environ["SAF_DIRECT_CONTAINER"]
require_container = os.environ["SAF_DIRECT_REQUIRE_CONTAINER"] == "1"
flow_rc = int(os.environ["SAF_DIRECT_FLOW_RC"])
validation_rc = int(os.environ["SAF_DIRECT_VALIDATION_RC"])

def text(name):
    p = evidence / name
    return p.read_text(errors="replace").strip() if p.is_file() else ""

def j(name):
    try:
        return json.loads(text(name) or "{}")
    except Exception:
        return {}

def sidecar_payload_state(name):
    return (j(name).get("payloadState") or "").strip()

engine_status = j("engine-documents-status.json")
exec_inspect = j("container-documents-cases-inspect.json")
exec_rc = exec_inspect.get("ExitCode")
write_payload = text("pdocker/diagnostics/saf-direct-output/write.payload")
rename_payload = text("pdocker/diagnostics/saf-direct-output/rename.payload")
unlink_absent_rc = text("pdocker/diagnostics/saf-direct-output/unlink-absent.rc")
write_sidecar = j("pdocker/diagnostics/saf-direct-output/write.sidecar.json")
rename_sidecar = j("pdocker/diagnostics/saf-direct-output/rename.sidecar.json")
unlink_sidecar = j("pdocker/diagnostics/saf-direct-output/unlink.sidecar.json")
mirror_exists_rc = text("pdocker/diagnostics/saf-direct-output/write-mirror-exists.rc")
invalid_direct_write = j("saf-write-invalid-target.json")

container_attempted = bool(container)
container_ok = container_attempted and exec_rc == 0 and "container-documents-cases-ok" in text("container-documents-cases.out")
direct_write_ok = write_payload == payload
direct_rename_ok = rename_payload == payload
unlink_ok = unlink_absent_rc == "0"
write_state = sidecar_payload_state("pdocker/diagnostics/saf-direct-output/write.sidecar.json")
rename_state = sidecar_payload_state("pdocker/diagnostics/saf-direct-output/rename.sidecar.json")
fallback_states = {"mirror-fallback-after-saf-error"}
fallback_recorded = write_state in fallback_states or rename_state in fallback_states
mirror_present = mirror_exists_rc == "0"
mirror_evicted = mirror_exists_rc == "1"
mirror_only_not_direct = mirror_present and not direct_write_ok
invalid_direct_write_rejected = (
    invalid_direct_write.get("Success") is False
    and invalid_direct_write.get("Fallback") is False
    and invalid_direct_write.get("PathValidationPolicy") == "fail-closed"
    and "invalid target path" in (invalid_direct_write.get("Error") or "")
)

path_traversal = {
    "Name": "path-traversal-validation",
    "Attempted": False,
    "Success": True,
    "Policy": "The gate validates relative paths before issuing container/app writes.",
    "RejectedExamples": ["../escape", "/absolute", "nested/../../escape", "nested\\\\escape"],
}
read_only = {
    "Name": "read-only-grant-fallback",
    "Attempted": False,
    "Success": True,
    "Policy": "A read-only or missing write grant may use app-private fallback only when payloadState records mirror-fallback-after-saf-error with a reason.",
    "ObservedPersistedWriteGrant": engine_status.get("PersistedWriteGrant"),
}

cases = {
    "container_documents_write": {
        "Attempted": container_attempted,
        "Success": container_ok,
        "Container": container,
        "DocumentsMount": os.environ["SAF_DIRECT_DOCUMENTS_MOUNT"],
        "ExitCode": exec_rc,
    },
    "direct_saf_payload": {
        "Attempted": bool(selected_host),
        "Success": direct_write_ok and not fallback_recorded,
        "RelativePath": os.environ["SAF_DIRECT_WRITE_RELATIVE"],
        "SelectedHostPath": selected_host,
        "PayloadState": write_state,
        "DirectPayloadObserved": direct_write_ok,
        "MirrorPayloadPresent": mirror_present,
        "MirrorPayloadEvicted": mirror_evicted,
    },
    "mirror_not_accepted_as_direct": {
        "Attempted": bool(selected_host),
        "Success": not mirror_only_not_direct,
        "MirrorOnlyRejected": mirror_only_not_direct,
        "Policy": "An app-private mirror file is fallback/cache evidence only; direct-output success requires matching payload under the selected SAF/Documents host path.",
    },
    "sidecar_metadata": {
        "Attempted": bool(write_sidecar or rename_sidecar),
        "Success": bool(write_sidecar.get("unixMetadata") == "sidecar" and write_sidecar.get("relativePath") == os.environ["SAF_DIRECT_WRITE_RELATIVE"]),
        "WriteSidecar": write_sidecar,
        "RenameSidecar": rename_sidecar,
    },
    "rename_stat": {
        "Attempted": container_attempted,
        "Success": direct_rename_ok and bool(rename_sidecar),
        "RelativePath": os.environ["SAF_DIRECT_RENAME_TARGET_RELATIVE"],
        "PayloadState": rename_state,
    },
    "unlink": {
        "Attempted": container_attempted,
        "Success": unlink_ok,
        "RelativePath": os.environ["SAF_DIRECT_UNLINK_RELATIVE"],
        "UnlinkSidecar": unlink_sidecar,
    },
    "direct_write_path_validation": {
        "Attempted": validation_rc == 0 or bool(invalid_direct_write),
        "Success": invalid_direct_write_rejected,
        "RejectedTarget": os.environ["SAF_DIRECT_INVALID_TARGET"],
        "PathValidationPolicy": "fail-closed",
        "Result": invalid_direct_write,
    },
    "path_traversal": path_traversal,
    "read_only_grant": read_only,
}

failures = []
if require_container and not container_attempted:
    failures.append("real container is required; set PDOCKER_SAF_DIRECT_OUTPUT_CONTAINER to a running container id/name")
if container_attempted and not container_ok:
    failures.append("container /documents write/rename/unlink command failed")
if not selected_host:
    failures.append("documents.hostPath is not configured")
if selected_host and not (selected_host.startswith("/storage/") or selected_host.startswith("/sdcard/")):
    failures.append(f"unsupported documents.hostPath: {selected_host}")
if not direct_write_ok:
    failures.append("payload was not observed directly under selected SAF/Documents host path")
if mirror_only_not_direct:
    failures.append("app-private mirror exists but selected SAF/Documents host payload is missing; mirror is not direct-output evidence")
if not direct_rename_ok:
    failures.append("renamed payload was not observed directly under selected SAF/Documents host path")
if not unlink_ok:
    failures.append("unlink target remained visible in selected SAF/Documents host path")
if fallback_recorded:
    failures.append("app-private fallback was used; this is recorded evidence but not a direct-output pass")
if write_state and write_state not in {"saf-synced-mirror-evicted", "mirror-present"}:
    failures.append(f"unexpected write payloadState: {write_state}")
if not cases["sidecar_metadata"]["Success"]:
    failures.append("sidecar metadata for write payload is missing or malformed")
if not invalid_direct_write_rejected:
    failures.append("unsafe direct-write target was not rejected fail-closed")

success = flow_rc == 0 and validation_rc == 0 and not failures
artifact = {
    "SchemaVersion": 1,
    "Kind": "saf-direct-output-gate",
    "Success": success,
    "Status": "pass" if success else "fail",
    "NoFakeSuccess": True,
    "GeneratedAtUnixMs": int(__import__("time").time() * 1000),
    "Case": os.environ["SAF_DIRECT_CASE"],
    "Package": os.environ.get("PKG", ""),
    "SelectedHostPath": selected_host,
    "DocumentsMount": os.environ["SAF_DIRECT_DOCUMENTS_MOUNT"],
    "Container": container,
    "RequireContainer": require_container,
    "EngineDocumentsStatus": engine_status,
    "LayerBoundary": {
        "ContainerPath": "/documents",
        "FilesystemBackend": "SAF mediator or direct Documents host path",
        "UnixMetadata": "app-private sidecar",
        "OverlayCowAwareness": "none; upper layers must use the FilesystemBackend contract and must not reach around it",
    },
    "Cases": cases,
    "FallbackPolicy": {
        "AllowedOnlyWhenExplicitlyRecorded": True,
        "AcceptedPayloadStateForFallback": "mirror-fallback-after-saf-error",
        "FallbackRecorded": fallback_recorded,
        "MirrorOnlyRejected": mirror_only_not_direct,
    },
    "Failures": failures,
    "EvidenceDirectory": str(evidence),
}
out.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
print(json.dumps({"Success": success, "Failures": failures}, indent=2))
raise SystemExit(0 if success else 1)
PY
}

main() {
  local selected_host flow_rc=0 validation_rc=0
  for rel in "$WRITE_RELATIVE" "$RENAME_SOURCE_RELATIVE" "$RENAME_TARGET_RELATIVE" "$UNLINK_RELATIVE"; do
    if ! validate_relative_path "$rel"; then
      echo "invalid relative test path: $rel" >&2
      exit 2
    fi
  done

  set +e
  start_app_and_daemon
  flow_rc=$?
  set -e

  collect_settings
  collect_engine_documents_status
  selected_host="$(cat "$EVIDENCE_DIR/selected-host-path.txt" 2>/dev/null | tr -d '\r' || true)"
  run_invalid_direct_write_case || validation_rc=1

  if [[ "$flow_rc" -eq 0 && -n "$CONTAINER" ]]; then
    run_container_documents_cases "$CONTAINER" || flow_rc=1
  elif [[ "$REQUIRE_CONTAINER" == "1" ]]; then
    flow_rc=1
  fi

  if [[ -n "$selected_host" ]]; then
    local deadline=$((SECONDS + TIMEOUT_SECONDS))
    while [[ "$SECONDS" -lt "$deadline" ]]; do
      collect_device_documents_evidence "$selected_host"
      if [[ "$(cat "$EVIDENCE_DIR/pdocker/diagnostics/saf-direct-output/write.payload" 2>/dev/null | tr -d '\r' || true)" == "$PAYLOAD" ]]; then
        break
      fi
      sleep 2
    done
  fi

  write_artifact "$selected_host" "$flow_rc" "$validation_rc"
}

main "$@"

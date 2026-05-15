#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PKG="${PDOCKER_ANDROID_PACKAGE:-io.github.ryo100794.pdocker.compat}"
SERIAL="${ANDROID_SERIAL:-}"
OUT="${OUT:-$ROOT/docs/test/apk-memory-pager-transparent-latest.json}"
APK="${APK:-$ROOT/app/build/outputs/apk/compat/debug/app-compat-debug.apk}"
INSTALL_APK="${INSTALL_APK:-0}"
POC_PAGES="${PDOCKER_MEMORY_PAGER_POC_PAGES:-32}"
POC_RESIDENT_PAGES="${PDOCKER_MEMORY_PAGER_POC_RESIDENT_PAGES:-4}"
REMOTE_PREAMBLE="APP_DATA=\$(pwd); case \"\$APP_DATA\" in /data/*) ;; *) for d in /data/user/0/$PKG /data/data/$PKG; do if [ -d \"\$d/files\" ]; then APP_DATA=\"\$d\"; break; fi; done ;; esac; cd \"\$APP_DATA\" || exit 70; mkdir -p files/pdocker/tmp files/pdocker/diagnostics/memory-pager-transparent cache || exit 71; export TMPDIR=\"\$APP_DATA/files/pdocker/tmp\"; export PDOCKER_MEMORY_TELEMETRY_PATH=\"\$APP_DATA/files/pdocker/diagnostics/memory-pager-transparent/memory-ring.jsonl\"; export PDOCKER_MEMORY_RING_PATH=\"\$PDOCKER_MEMORY_TELEMETRY_PATH\"; export PDOCKER_MEMORY_SUMMARY_PATH=\"\$APP_DATA/files/pdocker/diagnostics/memory-pager-transparent/memory-summary.json\"; export PDOCKER_MEMORY_TELEMETRY_OPERATION_ID=apk-memory-pager-transparent; export PDOCKER_MEMORY_TELEMETRY_CONTAINER_ID=apk-memory-pager-transparent"
DIRECT_CMD="$REMOTE_PREAMBLE; PDOCKER_MEMORY_PAGER_POC_PAGES=$POC_PAGES PDOCKER_MEMORY_PAGER_POC_RESIDENT_PAGES=$POC_RESIDENT_PAGES files/pdocker-runtime/docker-bin/pdocker-direct --pdocker-memory-pager-transparent-poc; POC_RC=\$?; echo __PDOCKER_MEMORY_RING_BEGIN__; cat \"\$PDOCKER_MEMORY_RING_PATH\" 2>/dev/null; echo __PDOCKER_MEMORY_RING_END__; echo __PDOCKER_MEMORY_SUMMARY_BEGIN__; cat \"\$PDOCKER_MEMORY_SUMMARY_PATH\" 2>/dev/null; echo __PDOCKER_MEMORY_SUMMARY_END__; echo exact_rc=\$POC_RC; exit \$POC_RC"

ADB=(adb)
if [[ -n "$SERIAL" ]]; then
  ADB+=( -s "$SERIAL" )
fi

if [[ "${1:-}" == "--dry-run" ]]; then
  mkdir -p "$(dirname "$OUT")"
  python3 - "$OUT" "$DIRECT_CMD" <<'PY'
import json, sys
from pathlib import Path
out = Path(sys.argv[1])
command = sys.argv[2]
out.write_text(json.dumps({
  "schema": "pdocker.apk-memory-pager-transparent.v1",
  "status": "dry-run",
  "command": command,
  "force_stops_app": False,
}, indent=2) + "\n")
PY
  echo "$OUT"
  exit 0
fi

mkdir -p "$(dirname "$OUT")"
TMP_OUTPUT="$(mktemp)"
TMP_MEM="$(mktemp)"
TMP_DEVICES="$(mktemp)"
trap 'rm -f "$TMP_OUTPUT" "$TMP_MEM" "$TMP_DEVICES"' EXIT

set +e
adb devices -l >"$TMP_DEVICES" 2>&1
if [[ -n "$SERIAL" ]]; then
  adb connect "$SERIAL" >>"$TMP_DEVICES" 2>&1
fi
"${ADB[@]}" shell 'cat /proc/meminfo | egrep "MemAvailable|SwapFree|SwapTotal"' >"$TMP_MEM" 2>&1
MEM_RC=$?
if [[ "$INSTALL_APK" == "1" ]]; then
  "${ADB[@]}" install -r "$APK" >>"$TMP_OUTPUT" 2>&1
  INSTALL_RC=$?
else
  INSTALL_RC=0
fi
ACTIVITY=$("${ADB[@]}" shell cmd package resolve-activity --brief "$PKG" 2>/dev/null | tail -1 | tr -d '\r')
if [[ -n "$ACTIVITY" && "$ACTIVITY" != "No activity found" ]]; then
  "${ADB[@]}" shell am start -n "$ACTIVITY" >/dev/null 2>>"$TMP_OUTPUT"
fi
for _ in $(seq 1 10); do
  "${ADB[@]}" shell "run-as $PKG sh -lc 'test -x files/pdocker-runtime/docker-bin/pdocker-direct'" >/dev/null 2>&1 && break
  sleep 0.5
done
"${ADB[@]}" shell "run-as $PKG sh -lc '$DIRECT_CMD'" >>"$TMP_OUTPUT" 2>&1
RUN_RC=$?
set -e

python3 - "$OUT" "$TMP_OUTPUT" "$TMP_MEM" "$TMP_DEVICES" "$RUN_RC" "$MEM_RC" "$INSTALL_RC" "$PKG" "$SERIAL" "$INSTALL_APK" "$DIRECT_CMD" <<'PY'
import json, re, sys, time
from pathlib import Path
out, output_file, mem_file, devices_file = map(Path, sys.argv[1:5])
run_rc, mem_rc, install_rc = map(int, sys.argv[5:8])
pkg, serial, install_apk, command = sys.argv[8:12]
text = output_file.read_text(errors="replace")
metrics = {}
for line in text.splitlines():
    m = re.match(r"pager-transparent-poc:([a-z_]+)=([0-9]+|ok|fail|yes|no)", line.strip())
    if m:
        v = m.group(2)
        metrics[m.group(1)] = int(v) if v.isdigit() else v
exact = re.search(r"exact_rc=([0-9]+)", text)
required = ["registered", "max_resident_pages", "page_ins", "page_outs", "dirty_page_outs", "bytes_in", "bytes_out", "elapsed_ns"]
missing = [name for name in required if name not in metrics]
def between(begin, end):
    if begin not in text or end not in text:
        return ""
    return text.split(begin, 1)[1].split(end, 1)[0].strip()
ring_text = between("__PDOCKER_MEMORY_RING_BEGIN__", "__PDOCKER_MEMORY_RING_END__")
summary_text = between("__PDOCKER_MEMORY_SUMMARY_BEGIN__", "__PDOCKER_MEMORY_SUMMARY_END__")
ring_rows = []
summary = None
artifact_errors = []
try:
    ring_rows = [json.loads(line) for line in ring_text.splitlines() if line.strip()]
    if not ring_rows or ring_rows[-1].get("ring_schema") != "pdocker.memory-telemetry-ring.v1":
        artifact_errors.append("missing memory ring schema")
except Exception as exc:
    artifact_errors.append(f"memory ring parse failed: {exc}")
try:
    summary = json.loads(summary_text) if summary_text else None
    if not summary or summary.get("summary_schema") != "pdocker.memory-telemetry-summary.v1":
        artifact_errors.append("missing memory summary schema")
except Exception as exc:
    artifact_errors.append(f"memory summary parse failed: {exc}")
status = "pass" if run_rc == 0 and metrics.get("result") == "ok" and exact and exact.group(1) == "0" and not missing and not artifact_errors else "fail"
if "device" in text.lower() and "not found" in text.lower() or "Connection refused" in devices_file.read_text(errors="replace"):
    status = "blocked-device"
record = {
    "schema": "pdocker.apk-memory-pager-transparent.v1",
    "created_at_epoch": int(time.time()),
    "status": status,
    "package": pkg,
    "android_serial": serial or None,
    "command": command,
    "force_stops_app": False,
    "install_apk_requested": install_apk == "1",
    "return_codes": {"run": run_rc, "meminfo": mem_rc, "install": install_rc},
    "metrics": metrics,
    "missing_metrics": missing,
    "memory_artifacts": {
        "ring_path": "files/pdocker/diagnostics/memory-pager-transparent/memory-ring.jsonl",
        "summary_path": "files/pdocker/diagnostics/memory-pager-transparent/memory-summary.json",
        "ring_samples": len(ring_rows),
        "summary": summary,
        "errors": artifact_errors,
    },
    "meminfo": mem_file.read_text(errors="replace"),
    "devices": devices_file.read_text(errors="replace"),
    "stdout": text,
}
out.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
print(out)
raise SystemExit(0 if status == "pass" else 2)
PY

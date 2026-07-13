#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PKG="${SKYDNIR_ANDROID_PACKAGE:-${SKYDNIR_PACKAGE:-${PDOCKER_ANDROID_PACKAGE:-io.github.ryo100794.pdocker.compat}}}"
SERIAL="${ANDROID_SERIAL:-}"
OUT="${OUT:-$ROOT/docs/test/apk-memory-pager-managed-latest.json}"
APK="${APK:-$ROOT/app/build/outputs/apk/compat/debug/app-compat-debug.apk}"
INSTALL_APK="${INSTALL_APK:-0}"
POC_PAGES="${PDOCKER_MEMORY_PAGER_POC_PAGES:-32}"
POC_RESIDENT_PAGES="${PDOCKER_MEMORY_PAGER_POC_RESIDENT_PAGES:-4}"
ADB_CONNECT="${PDOCKER_ADB_CONNECT:-0}"
DRY_RUN=0
REMOTE_PREAMBLE="APP_DATA=\$(pwd); case \"\$APP_DATA\" in /data/*) ;; *) for d in /data/user/0/$PKG /data/data/$PKG; do if [ -d \"\$d/files\" ]; then APP_DATA=\"\$d\"; break; fi; done ;; esac; cd \"\$APP_DATA\" || exit 70; mkdir -p files/pdocker/tmp cache || exit 71; export TMPDIR=\"\$APP_DATA/files/pdocker/tmp\""
DIRECT_CMD="$REMOTE_PREAMBLE; PDOCKER_MEMORY_PAGER_POC_PAGES=$POC_PAGES PDOCKER_MEMORY_PAGER_POC_RESIDENT_PAGES=$POC_RESIDENT_PAGES files/pdocker-runtime/docker-bin/pdocker-direct --pdocker-memory-pager-managed-poc"

usage() {
  cat <<EOF
Usage: $0 [--dry-run] [--adb-connect]

Runs the APK managed memory-pager proof of concept against an already connected
Android device.  ANDROID_SERIAL is passed to adb with -s; even when it is a
host:port wireless-debugging serial, this script does not run adb connect unless
PDOCKER_ADB_CONNECT=1 or --adb-connect is provided.

Environment:
  ANDROID_SERIAL        optional adb serial for adb -s
  PDOCKER_ADB_CONNECT   set to 1 to explicitly run adb connect ANDROID_SERIAL
  OUT                   JSON artifact path (default: $OUT)
  INSTALL_APK           set to 1 to install APK before the probe
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --adb-connect) ADB_CONNECT=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

ADB=(adb)
if [[ -n "$SERIAL" ]]; then
  ADB+=( -s "$SERIAL" )
fi

if [[ "$DRY_RUN" == "1" ]]; then
  mkdir -p "$(dirname "$OUT")"
python3 - "$OUT" "$DIRECT_CMD" <<'PY'
import json, sys
from pathlib import Path
out = Path(sys.argv[1])
command = sys.argv[2]
out.write_text(json.dumps({
  "schema": "pdocker.apk-memory-pager-managed.v1",
  "status": "dry-run",
  "command": command,
  "installs_apk_by_default": False,
  "force_stops_app": False,
  "adb_connects_by_default": False,
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
if [[ "$ADB_CONNECT" == "1" && -n "$SERIAL" ]]; then
  adb connect "$SERIAL" >>"$TMP_DEVICES" 2>&1
elif [[ -n "$SERIAL" ]]; then
  {
    echo "using preconnected adb serial via adb -s: $SERIAL"
    echo "not running adb connect; set PDOCKER_ADB_CONNECT=1 or pass --adb-connect to opt in"
  } >>"$TMP_DEVICES"
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
"${ADB[@]}" shell "run-as $PKG sh -lc '$DIRECT_CMD; rc=\$?; echo exact_rc=\$rc'" >>"$TMP_OUTPUT" 2>&1
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
    m = re.match(r"pager-managed-poc:([a-z_]+)=([0-9]+|ok|fail)", line.strip())
    if m:
        v = m.group(2)
        metrics[m.group(1)] = int(v) if v.isdigit() else v
exact = re.search(r"exact_rc=([0-9]+)", text)
required_metrics = [
    "resident_pages",
    "max_resident_pages",
    "page_ins",
    "page_outs",
    "dirty_page_outs",
    "bytes_in",
    "bytes_out",
    "elapsed_ns",
]
missing_metrics = [name for name in required_metrics if name not in metrics]
status = (
    "pass"
    if run_rc == 0 and metrics.get("result") == "ok" and exact and exact.group(1) == "0" and not missing_metrics
    else "fail"
)
if "device" in text.lower() and "not found" in text.lower() or "Connection refused" in devices_file.read_text(errors="replace"):
    status = "blocked-device"
record = {
    "schema": "pdocker.apk-memory-pager-managed.v1",
    "created_at_epoch": int(time.time()),
    "status": status,
    "package": pkg,
    "android_serial": serial or None,
    "command": command,
    "force_stops_app": False,
    "install_apk_requested": install_apk == "1",
    "return_codes": {"run": run_rc, "meminfo": mem_rc, "install": install_rc},
    "metrics": metrics,
    "missing_metrics": missing_metrics,
    "meminfo": mem_file.read_text(errors="replace"),
    "devices": devices_file.read_text(errors="replace"),
    "stdout": text,
}
out.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
print(out)
raise SystemExit(0 if status == "pass" else 2)
PY

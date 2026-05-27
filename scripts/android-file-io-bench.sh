#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ADB="${ADB:-adb}"
PKG="${SKYDNIR_ANDROID_PACKAGE:-${SKYDNIR_PACKAGE:-${PDOCKER_ANDROID_PACKAGE:-io.github.ryo100794.pdocker.compat}}}"
SIZE_MB="${PDOCKER_FILE_IO_SIZE_MB:-16}"
SMALL_FILES="${PDOCKER_FILE_IO_SMALL_FILES:-256}"
TRACE_MODE="${PDOCKER_DIRECT_TRACE_MODE:-seccomp}"
PERSISTENT_RUNS="${PDOCKER_FILE_IO_PERSISTENT_RUNS:-100}"
OUT="${PDOCKER_FILE_IO_BENCH_OUT:-$ROOT/docs/test/file-io-bench-latest.json}"
MD_OUT="${PDOCKER_FILE_IO_BENCH_MD:-$ROOT/docs/test/file-io-bench-latest.md}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REMOTE_BENCH_DIR="files/pdocker/bench"
REMOTE_JSON="file-io-bench-$STAMP.json"
RAW="$(mktemp "${TMPDIR:-/tmp}/pdocker-file-io.XXXXXX.log")"
trap 'rm -f "$RAW"' EXIT

usage() {
  cat <<EOF
Usage: scripts/android-file-io-bench.sh [--size-mb N] [--small-files N] [--trace-mode MODE] [--persistent-runs N] [--out PATH]

Compares APK-native run-as file I/O with the same workload executed through
pdocker-direct inside an existing container/image rootfs. The benchmark writes
machine-readable JSON to docs/test and mirrors it into files/pdocker/bench on
the Android device.

Environment:
  ADB                         adb executable (default: adb)
  SKYDNIR_ANDROID_PACKAGE    package name (SKYDNIR_PACKAGE/PDOCKER_ANDROID_PACKAGE are still accepted; default: $PKG)
  PDOCKER_FILE_IO_SIZE_MB     sequential file size in MiB (default: $SIZE_MB)
  PDOCKER_FILE_IO_SMALL_FILES small-file count (default: $SMALL_FILES)
  PDOCKER_FILE_IO_PERSISTENT_RUNS steady-state noop samples (default: $PERSISTENT_RUNS)
  PDOCKER_DIRECT_TRACE_MODE   direct backend trace mode (default: $TRACE_MODE)
EOF
}

while (($#)); do
  case "$1" in
    --size-mb)
      shift
      SIZE_MB="${1:?--size-mb requires a value}"
      ;;
    --small-files)
      shift
      SMALL_FILES="${1:?--small-files requires a value}"
      ;;
    --trace-mode)
      shift
      TRACE_MODE="${1:?--trace-mode requires a value}"
      ;;
    --persistent-runs)
      shift
      PERSISTENT_RUNS="${1:?--persistent-runs requires a value}"
      ;;
    --out)
      shift
      OUT="${1:?--out requires a value}"
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

remote_quote() {
  printf "'%s'" "$(printf "%s" "$1" | sed "s/'/'\\\\''/g")"
}

adb_run_as() {
  "$ADB" shell "run-as $PKG sh -c $(remote_quote "$1")"
}

append_raw() {
  tee -a "$RAW"
}

native_script() {
  cat <<EOF
set +e
ROOT="files/pdocker/bench/file-io-native-$STAMP"
rm -rf "\$ROOT"
mkdir -p "\$ROOT/small"
cd "\$ROOT" || exit 1
echo "__PDIO_CONTEXT__:native:path=\$ROOT"
fail=0
for spec in \\
  "noop::true" \\
  "seq_write::dd if=/dev/zero of=seq.bin bs=1048576 count=$SIZE_MB >/dev/null 2>&1" \\
  "seq_read::dd if=seq.bin of=/dev/null bs=1048576 >/dev/null 2>&1" \\
  "small_create::i=0; while [ \\\$i -lt $SMALL_FILES ]; do printf '%08d\\\\n' \\\$i > small/f\\\$i || exit 1; i=\\\$((i+1)); done" \\
  "small_stat::i=0; while [ \\\$i -lt $SMALL_FILES ]; do test -s small/f\\\$i || exit 1; i=\\\$((i+1)); done" \\
  "small_read::cat small/f* >/dev/null" \\
  "compile_prepare::rm -rf src build libmock.a; mkdir -p src build; i=0; while [ \\\$i -lt $SMALL_FILES ]; do d=src/d\\\$((i%16)); mkdir -p \\\$d; printf 'int pdocker_compile_unit_%s(void){return %s;}\\\\n' \\\$i \\\$i > \\\$d/unit_\\\$i.c || exit 1; i=\\\$((i+1)); done" \\
  "compile_scan::find src -type f -name '*.c' | while read f; do grep -q pdocker_compile_unit \\\$f || exit 1; done" \\
  "compile_objects::find src -type f -name '*.c' | while read f; do b=\\\$(basename \\\$f .c); cat \\\$f \\\$f > build/\\\$b.o || exit 1; printf 'build/%s.o: %s\\\\n' \\\$b \\\$f > build/\\\$b.d || exit 1; done" \\
  "compile_archive::cat build/*.o > libmock.a; test -s libmock.a" \\
  "overlay_prepare::rm -rf lower upper; mkdir -p lower upper; i=0; while [ \\\$i -lt $SMALL_FILES ]; do printf 'lower-layer-%08d\\\\n' \\\$i > lower/f\\\$i || exit 1; cp lower/f\\\$i upper/f\\\$i || exit 1; i=\\\$((i+1)); done" \\
  "overlay_copyup_write::i=0; while [ \\\$i -lt $SMALL_FILES ]; do printf 'upper-change-%08d\\\\n' \\\$i >> upper/f\\\$i || exit 1; i=\\\$((i+1)); done" \\
  "overlay_truncate::i=0; while [ \\\$i -lt $SMALL_FILES ]; do : > upper/f\\\$i || exit 1; i=\\\$((i+1)); done" \\
  "overlay_unlink::i=0; while [ \\\$i -lt $SMALL_FILES ]; do rm -f upper/f\\\$i || exit 1; i=\\\$((i+1)); done"
do
  label="\${spec%%::*}"
  body="\${spec#*::}"
  echo "__PDIO_BEGIN__:native:\$label"
  /system/bin/time -p sh -c "\$body" 2>&1
  rc=\$?
  if [ "\$rc" -ne 0 ]; then fail=1; fi
  echo "__PDIO_END__:native:\$label:rc=\$rc"
done
rm -rf "\$ROOT"
exit "\$fail"
EOF
}

container_script() {
  cat <<EOF
set +e
cd files || exit 1
R=\$(find pdocker/containers -mindepth 2 -maxdepth 2 -type d -name rootfs 2>/dev/null | head -1)
if test -z "\$R"; then
  R=\$(find pdocker/images -mindepth 2 -maxdepth 3 -type d -name rootfs 2>/dev/null | head -1)
fi
if test -z "\$R"; then
  echo "__PDIO_ERROR__:container:no-rootfs"
  exit 2
fi
export PDOCKER_DIRECT_EXPERIMENTAL_PROCESS_EXEC=1
export PDOCKER_DIRECT_TRACE_MODE="$TRACE_MODE"
export PDOCKER_DIRECT_TRACE_SYSCALLS=0
export PDOCKER_DIRECT_TRACE_VERBOSE=0
export PDOCKER_DIRECT_TRACE_PATHS=0
export PDOCKER_DIRECT_STATS=1
echo "__PDIO_CONTEXT__:container:rootfs=\$R"
fail=0
run_one() {
  label="\$1"
  body="\$2"
  echo "__PDIO_BEGIN__:container:\$label"
  /system/bin/time -p pdocker-runtime/docker-bin/pdocker-direct run --mode bench --rootfs "\$R" --workdir / --env HOME=/root --env PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin -- /bin/sh -lc "\$body" 2>&1
  rc=\$?
  if [ "\$rc" -ne 0 ]; then fail=1; fi
  echo "__PDIO_END__:container:\$label:rc=\$rc"
}
run_one setup "rm -rf /tmp/pdocker-file-io-bench; mkdir -p /tmp/pdocker-file-io-bench/small"
run_one noop "true"
run_one seq_write "cd /tmp/pdocker-file-io-bench && dd if=/dev/zero of=seq.bin bs=1048576 count=$SIZE_MB >/dev/null 2>&1"
run_one seq_read "cd /tmp/pdocker-file-io-bench && dd if=seq.bin of=/dev/null bs=1048576 >/dev/null 2>&1"
run_one small_create "cd /tmp/pdocker-file-io-bench && i=0; while [ \\\$i -lt $SMALL_FILES ]; do printf '%08d\\\\n' \\\$i > small/f\\\$i || exit 1; i=\\\$((i+1)); done"
run_one small_stat "cd /tmp/pdocker-file-io-bench && i=0; while [ \\\$i -lt $SMALL_FILES ]; do test -s small/f\\\$i || exit 1; i=\\\$((i+1)); done"
run_one small_read "cd /tmp/pdocker-file-io-bench && cat small/f* >/dev/null"
run_one compile_prepare "cd /tmp/pdocker-file-io-bench && rm -rf src build libmock.a; mkdir -p src build; i=0; while [ \\\$i -lt $SMALL_FILES ]; do d=src/d\\\$((i%16)); mkdir -p \\\$d; printf 'int pdocker_compile_unit_%s(void){return %s;}\\\\n' \\\$i \\\$i > \\\$d/unit_\\\$i.c || exit 1; i=\\\$((i+1)); done"
run_one compile_scan "cd /tmp/pdocker-file-io-bench && find src -type f -name '*.c' | while read f; do grep -q pdocker_compile_unit \\\$f || exit 1; done"
run_one compile_objects "cd /tmp/pdocker-file-io-bench && find src -type f -name '*.c' | while read f; do b=\\\$(basename \\\$f .c); cat \\\$f \\\$f > build/\\\$b.o || exit 1; printf 'build/%s.o: %s\\\\n' \\\$b \\\$f > build/\\\$b.d || exit 1; done"
run_one compile_archive "cd /tmp/pdocker-file-io-bench && cat build/*.o > libmock.a; test -s libmock.a"
run_one overlay_prepare "cd /tmp/pdocker-file-io-bench && rm -rf lower upper; mkdir -p lower upper; i=0; while [ \\\$i -lt $SMALL_FILES ]; do printf 'lower-layer-%08d\\\\n' \\\$i > lower/f\\\$i || exit 1; ln lower/f\\\$i upper/f\\\$i || exit 1; i=\\\$((i+1)); done"
run_one overlay_copyup_write "cd /tmp/pdocker-file-io-bench && if test -e /.libcow.so; then export LD_PRELOAD=/.libcow.so; fi; i=0; while [ \\\$i -lt $SMALL_FILES ]; do printf 'upper-change-%08d\\\\n' \\\$i >> upper/f\\\$i || exit 1; i=\\\$((i+1)); done"
run_one overlay_truncate "cd /tmp/pdocker-file-io-bench && if test -e /.libcow.so; then export LD_PRELOAD=/.libcow.so; fi; i=0; while [ \\\$i -lt $SMALL_FILES ]; do : > upper/f\\\$i || exit 1; i=\\\$((i+1)); done"
run_one overlay_unlink "cd /tmp/pdocker-file-io-bench && i=0; while [ \\\$i -lt $SMALL_FILES ]; do rm -f upper/f\\\$i || exit 1; i=\\\$((i+1)); done"
run_one cleanup "rm -rf /tmp/pdocker-file-io-bench"
exit "\$fail"
EOF
}

persistent_payload() {
  cat <<'EOF'
set +e
ROOT="${PDOCKER_FILE_IO_ROOT:-/tmp/pdocker-file-io-bench-persistent}"
SCOPE="${PDOCKER_FILE_IO_SCOPE:-container_persistent}"
OVERLAY_LINK_MODE="${PDOCKER_FILE_IO_OVERLAY_LINK_MODE:-hardlink}"
export ROOT OVERLAY_LINK_MODE
rm -rf "$ROOT"
mkdir -p "$ROOT/small"
fail=0
now_ns() { date +%s%N; }
run_one() {
  label="$1"
  body="$2"
  echo "__PDIO_BEGIN__:$SCOPE:$label"
  start=$(now_ns)
  sh -c "$body"
  rc=$?
  end=$(now_ns)
  elapsed_ms=$(awk "BEGIN { printf \"%.6f\", ($end - $start) / 1000000.0 }")
  echo "elapsed_ms $elapsed_ms"
  if [ "$rc" -ne 0 ]; then fail=1; fi
  echo "__PDIO_END__:$SCOPE:$label:rc=$rc"
}
run_one persistent_noop 'i=0; while [ $i -lt "$PDOCKER_FILE_IO_PERSISTENT_RUNS" ]; do :; i=$((i+1)); done'
run_one seq_write 'cd "$ROOT" && dd if=/dev/zero of=seq.bin bs=1048576 count="$PDOCKER_FILE_IO_SIZE_MB" >/dev/null 2>&1'
run_one seq_read 'cd "$ROOT" && dd if=seq.bin of=/dev/null bs=1048576 >/dev/null 2>&1'
run_one small_create 'cd "$ROOT" && i=0; while [ $i -lt "$PDOCKER_FILE_IO_SMALL_FILES" ]; do printf "%08d\n" $i > small/f$i || exit 1; i=$((i+1)); done'
run_one small_stat 'cd "$ROOT" && i=0; while [ $i -lt "$PDOCKER_FILE_IO_SMALL_FILES" ]; do test -s small/f$i || exit 1; i=$((i+1)); done'
run_one small_read 'cd "$ROOT" && cat small/f* >/dev/null'
run_one compile_prepare 'cd "$ROOT" && rm -rf src build libmock.a; mkdir -p src build; i=0; while [ $i -lt "$PDOCKER_FILE_IO_SMALL_FILES" ]; do d=src/d$((i%16)); mkdir -p $d; printf "int pdocker_compile_unit_%s(void){return %s;}\n" $i $i > $d/unit_$i.c || exit 1; i=$((i+1)); done'
run_one compile_scan 'cd "$ROOT" && find src -type f -name "*.c" | while read f; do grep -q pdocker_compile_unit $f || exit 1; done'
run_one compile_objects 'cd "$ROOT" && find src -type f -name "*.c" | while read f; do b=$(basename $f .c); cat $f $f > build/$b.o || exit 1; printf "build/%s.o: %s\n" $b $f > build/$b.d || exit 1; done'
run_one compile_archive 'cd "$ROOT" && cat build/*.o > libmock.a; test -s libmock.a'
run_one overlay_prepare 'cd "$ROOT" && rm -rf lower upper; mkdir -p lower upper; i=0; while [ $i -lt "$PDOCKER_FILE_IO_SMALL_FILES" ]; do printf "lower-layer-%08d\n" $i > lower/f$i || exit 1; if [ "$OVERLAY_LINK_MODE" = copy ]; then cp lower/f$i upper/f$i || exit 1; else ln lower/f$i upper/f$i || exit 1; fi; i=$((i+1)); done'
run_one overlay_copyup_write 'cd "$ROOT" && if test -e /.libcow.so; then export LD_PRELOAD=/.libcow.so; fi; i=0; while [ $i -lt "$PDOCKER_FILE_IO_SMALL_FILES" ]; do printf "upper-change-%08d\n" $i >> upper/f$i || exit 1; i=$((i+1)); done'
run_one overlay_truncate 'cd "$ROOT" && if test -e /.libcow.so; then export LD_PRELOAD=/.libcow.so; fi; i=0; while [ $i -lt "$PDOCKER_FILE_IO_SMALL_FILES" ]; do : > upper/f$i || exit 1; i=$((i+1)); done'
run_one overlay_unlink 'cd "$ROOT" && i=0; while [ $i -lt "$PDOCKER_FILE_IO_SMALL_FILES" ]; do rm -f upper/f$i || exit 1; i=$((i+1)); done'
rm -rf "$ROOT"
exit "$fail"
EOF
}

native_persistent_script() {
  local payload_b64
  payload_b64="$(persistent_payload | base64 -w0)"
  cat <<EOF
set +e
ROOT="files/pdocker/bench/file-io-native-persistent-$STAMP"
SCRIPT="files/pdocker/bench/file-io-native-persistent-$STAMP.sh"
mkdir -p files/pdocker/bench || exit 1
printf '%s' '$payload_b64' | base64 -d > "\$SCRIPT"
chmod 755 "\$SCRIPT"
echo "__PDIO_CONTEXT__:native_persistent:path=\$ROOT;overlay_link_mode=copy"
PDOCKER_FILE_IO_ROOT="\$ROOT" PDOCKER_FILE_IO_SCOPE=native_persistent PDOCKER_FILE_IO_OVERLAY_LINK_MODE=copy PDOCKER_FILE_IO_SIZE_MB=$SIZE_MB PDOCKER_FILE_IO_SMALL_FILES=$SMALL_FILES PDOCKER_FILE_IO_PERSISTENT_RUNS=$PERSISTENT_RUNS sh "\$SCRIPT"
rc=\$?
rm -rf "\$ROOT" "\$SCRIPT"
exit "\$rc"
EOF
}

native_rootfs_persistent_script() {
  local payload_b64
  payload_b64="$(persistent_payload | base64 -w0)"
  cat <<EOF
set +e
cd files || exit 1
R=\$(find pdocker/containers -mindepth 2 -maxdepth 2 -type d -name rootfs 2>/dev/null | head -1)
if test -z "\$R"; then
  R=\$(find pdocker/images -mindepth 2 -maxdepth 3 -type d -name rootfs 2>/dev/null | head -1)
fi
if test -z "\$R"; then
  echo "__PDIO_ERROR__:native_rootfs_persistent:no-rootfs"
  exit 2
fi
mkdir -p pdocker/bench || exit 1
ROOT="\$R/tmp/pdocker-file-io-native-rootfs-persistent-$STAMP"
SCRIPT="pdocker/bench/file-io-native-rootfs-persistent-$STAMP.sh"
printf '%s' '$payload_b64' | base64 -d > "\$SCRIPT"
chmod 755 "\$SCRIPT"
echo "__PDIO_CONTEXT__:native_rootfs_persistent:rootfs=\$R;path=\$ROOT;overlay_link_mode=copy"
PDOCKER_FILE_IO_ROOT="\$ROOT" PDOCKER_FILE_IO_SCOPE=native_rootfs_persistent PDOCKER_FILE_IO_OVERLAY_LINK_MODE=copy PDOCKER_FILE_IO_SIZE_MB=$SIZE_MB PDOCKER_FILE_IO_SMALL_FILES=$SMALL_FILES PDOCKER_FILE_IO_PERSISTENT_RUNS=$PERSISTENT_RUNS sh "\$SCRIPT"
rc=\$?
rm -rf "\$ROOT" "\$SCRIPT"
exit "\$rc"
EOF
}

container_persistent_script() {
  local payload_b64
  payload_b64="$(persistent_payload | base64 -w0)"
  cat <<EOF
set +e
cd files || exit 1
R=\$(find pdocker/containers -mindepth 2 -maxdepth 2 -type d -name rootfs 2>/dev/null | head -1)
if test -z "\$R"; then
  R=\$(find pdocker/images -mindepth 2 -maxdepth 3 -type d -name rootfs 2>/dev/null | head -1)
fi
if test -z "\$R"; then
  echo "__PDIO_ERROR__:container_persistent:no-rootfs"
  exit 2
fi
export PDOCKER_DIRECT_EXPERIMENTAL_PROCESS_EXEC=1
export PDOCKER_DIRECT_TRACE_MODE="$TRACE_MODE"
export PDOCKER_DIRECT_TRACE_SYSCALLS=0
export PDOCKER_DIRECT_TRACE_VERBOSE=0
export PDOCKER_DIRECT_TRACE_PATHS=0
export PDOCKER_DIRECT_STATS=1
echo "__PDIO_CONTEXT__:container_persistent:rootfs=\$R"
SCRIPT="\$R/tmp/pdocker-file-io-persistent.sh"
printf '%s' '$payload_b64' | base64 -d > "\$SCRIPT"
chmod 755 "\$SCRIPT"
pdocker-runtime/docker-bin/pdocker-direct run --mode bench --rootfs "\$R" --workdir / --env HOME=/root --env PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin --env PDOCKER_FILE_IO_SIZE_MB=$SIZE_MB --env PDOCKER_FILE_IO_SMALL_FILES=$SMALL_FILES --env PDOCKER_FILE_IO_PERSISTENT_RUNS=$PERSISTENT_RUNS -- /bin/sh /tmp/pdocker-file-io-persistent.sh
rc=\$?
rm -f "\$SCRIPT"
exit "\$rc"
EOF
}

device_serial="$("${ADB}" get-serialno 2>&1 || true)"
echo "[pdocker file-io bench] device: $device_serial"
echo "[pdocker file-io bench] size=${SIZE_MB}MiB small_files=${SMALL_FILES} trace-mode=$TRACE_MODE"
echo "[pdocker file-io bench] native run-as"
set +e
adb_run_as "$(native_script)" 2>&1 | append_raw
native_rc=${PIPESTATUS[0]}
set -e
if ((native_rc != 0)); then
  echo "__PDIO_HOST_ERROR__:native:adb-run-as-rc=$native_rc" | append_raw
fi
echo
echo "[pdocker file-io bench] native run-as persistent"
set +e
adb_run_as "$(native_persistent_script)" 2>&1 | append_raw
native_persistent_rc=${PIPESTATUS[0]}
set -e
if ((native_persistent_rc != 0)); then
  echo "__PDIO_HOST_ERROR__:native_persistent:adb-run-as-rc=$native_persistent_rc" | append_raw
fi
echo
echo "[pdocker file-io bench] native run-as rootfs persistent"
set +e
adb_run_as "$(native_rootfs_persistent_script)" 2>&1 | append_raw
native_rootfs_persistent_rc=${PIPESTATUS[0]}
set -e
if ((native_rootfs_persistent_rc != 0)); then
  echo "__PDIO_HOST_ERROR__:native_rootfs_persistent:adb-run-as-rc=$native_rootfs_persistent_rc" | append_raw
fi
echo
echo "[pdocker file-io bench] container direct"
set +e
adb_run_as "$(container_script)" 2>&1 | append_raw
container_rc=${PIPESTATUS[0]}
set -e
if ((container_rc != 0)); then
  echo "__PDIO_HOST_ERROR__:container:adb-run-as-rc=$container_rc" | append_raw
fi
echo
echo "[pdocker file-io bench] container direct persistent"
set +e
adb_run_as "$(container_persistent_script)" 2>&1 | append_raw
container_persistent_rc=${PIPESTATUS[0]}
set -e
if ((container_persistent_rc != 0)); then
  echo "__PDIO_HOST_ERROR__:container_persistent:adb-run-as-rc=$container_persistent_rc" | append_raw
fi

mkdir -p "$(dirname "$OUT")" "$(dirname "$MD_OUT")"
python3 - "$RAW" "$OUT" "$MD_OUT" "$SIZE_MB" "$SMALL_FILES" "$TRACE_MODE" "$PERSISTENT_RUNS" "$device_serial" "$native_rc" "$native_persistent_rc" "$native_rootfs_persistent_rc" "$container_rc" "$container_persistent_rc" <<'PY'
import json
import math
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

raw_path, out_path, md_path, size_mb, small_files, trace_mode, persistent_runs, device_serial, native_rc, native_persistent_rc, native_rootfs_persistent_rc, container_rc, container_persistent_rc = sys.argv[1:14]
raw = Path(raw_path).read_text(errors="replace").splitlines()
events = []
current = None
contexts = {}
pending_stats = []
for line in raw:
    if line.startswith("__PDIO_CONTEXT__:"):
        _, scope, payload = line.split(":", 2)
        contexts[scope] = payload
        continue
    if line.startswith("__PDIO_ERROR__:"):
        events.append({"scope": line.split(":", 2)[1], "label": "error", "rc": 2, "raw": [line]})
        continue
    if line.startswith("__PDIO_HOST_ERROR__:"):
        _, scope, payload = line.split(":", 2)
        events.append({"scope": scope, "label": "host_error", "rc": 1, "raw": [line], "message": payload})
        continue
    m = re.match(r"__PDIO_BEGIN__:(native|native_persistent|native_rootfs_persistent|container|container_persistent):([^:]+)$", line)
    if m:
        current = {"scope": m.group(1), "label": m.group(2), "raw": [], "stats": []}
        continue
    m = re.match(r"__PDIO_END__:(native|native_persistent|native_rootfs_persistent|container|container_persistent):([^:]+):rc=([0-9]+)$", line)
    if m and current:
        current["rc"] = int(m.group(3))
        current["stats"] = pending_stats
        pending_stats = []
        events.append(current)
        current = None
        continue
    m = re.match(r"(real|user|sys) ([0-9.]+)$", line)
    if m and current:
        current[m.group(1) + "_s"] = float(m.group(2))
        current["raw"].append(line)
        continue
    m = re.match(r"elapsed_ms ([0-9.]+)$", line)
    if m and current:
        current["real_s"] = float(m.group(1)) / 1000.0
        current["raw"].append(line)
        continue
    if line.startswith("pdocker-direct-stats:"):
        if current is not None:
            current["raw"].append(line)
            current["stats"].append(line)
        else:
            pending_stats.append(line)
        continue
    if current is not None:
        current["raw"].append(line)

for event in events:
    stats = "\n".join(event.get("stats") or event.get("raw", []))
    m = re.search(r"stops=([0-9]+)", stats)
    if m:
        event["direct_stops"] = int(m.group(1))
    top = []
    for rank, nr, name, count in re.findall(r"#([0-9]+) nr=([0-9]+)\(([^)]+)\) count=([0-9]+)", stats):
        top.append({"rank": int(rank), "nr": int(nr), "name": name, "count": int(count)})
    if top:
        event["top_syscalls"] = top
    event.pop("stats", None)
    event["raw_tail"] = event.pop("raw", [])[-12:]

by_key = {(e.get("scope"), e.get("label")): e for e in events}
comparisons = []
labels = [
    "noop",
    "seq_write",
    "seq_read",
    "small_create",
    "small_stat",
    "small_read",
    "compile_prepare",
    "compile_scan",
    "compile_objects",
    "compile_archive",
    "overlay_prepare",
    "overlay_copyup_write",
    "overlay_truncate",
    "overlay_unlink",
]
native_noop = by_key.get(("native", "noop"), {}).get("real_s", 0.0)
native_persistent_noop = by_key.get(("native_persistent", "persistent_noop"), {}).get("real_s", 0.0)
native_persistent_noop_per_run = (
    native_persistent_noop / float(persistent_runs)
    if native_persistent_noop and float(persistent_runs) > 0
    else 0.0
)
native_rootfs_persistent_noop = by_key.get(("native_rootfs_persistent", "persistent_noop"), {}).get("real_s", 0.0)
native_rootfs_persistent_noop_per_run = (
    native_rootfs_persistent_noop / float(persistent_runs)
    if native_rootfs_persistent_noop and float(persistent_runs) > 0
    else 0.0
)
container_noop = by_key.get(("container", "noop"), {}).get("real_s", 0.0)
container_persistent_noop = by_key.get(("container_persistent", "persistent_noop"), {}).get("real_s", 0.0)
container_persistent_noop_per_run = (
    container_persistent_noop / float(persistent_runs)
    if container_persistent_noop and float(persistent_runs) > 0
    else 0.0
)
for label in labels:
    n = by_key.get(("native", label), {})
    c = by_key.get(("container", label), {})
    row = {"label": label}
    if "real_s" in n:
        row["native_real_s"] = n["real_s"]
        row["native_adjusted_s"] = max(0.0, n["real_s"] - native_noop)
    np = by_key.get(("native_persistent", label), {})
    if "real_s" in np:
        row["native_persistent_real_s"] = np["real_s"]
        row["native_persistent_adjusted_s"] = max(0.0, np["real_s"] - native_persistent_noop)
    nrp = by_key.get(("native_rootfs_persistent", label), {})
    if "real_s" in nrp:
        row["native_rootfs_persistent_real_s"] = nrp["real_s"]
        row["native_rootfs_persistent_adjusted_s"] = max(0.0, nrp["real_s"] - native_rootfs_persistent_noop)
    if "real_s" in c:
        row["container_real_s"] = c["real_s"]
        row["container_adjusted_s"] = max(0.0, c["real_s"] - container_noop)
        row["container_stops"] = c.get("direct_stops")
    cp = by_key.get(("container_persistent", label), {})
    if "real_s" in cp:
        row["container_persistent_real_s"] = cp["real_s"]
        row["container_persistent_adjusted_s"] = max(0.0, cp["real_s"] - container_persistent_noop)
    if "real_s" in n and "real_s" in c and n["real_s"] > 0:
        row["container_vs_native"] = c["real_s"] / n["real_s"]
    if "real_s" in np and "real_s" in cp and np["real_s"] > 0:
        row["container_persistent_vs_native_persistent"] = cp["real_s"] / np["real_s"]
    if "real_s" in nrp and "real_s" in cp and nrp["real_s"] > 0:
        row["container_persistent_vs_native_rootfs_persistent"] = cp["real_s"] / nrp["real_s"]
    if row.keys() - {"label"}:
        comparisons.append(row)

def mbps(seconds):
    if not seconds or seconds <= 0:
        return None
    return float(size_mb) / seconds

for row in comparisons:
    if row["label"] in {"seq_write", "seq_read"}:
        row["native_mib_s"] = mbps(row.get("native_adjusted_s"))
        row["native_persistent_mib_s"] = mbps(row.get("native_persistent_adjusted_s"))
        row["native_rootfs_persistent_mib_s"] = mbps(row.get("native_rootfs_persistent_adjusted_s"))
        row["container_mib_s"] = mbps(row.get("container_adjusted_s"))
        row["container_persistent_mib_s"] = mbps(row.get("container_persistent_adjusted_s"))

persistent_delta_s = container_persistent_noop_per_run - native_persistent_noop_per_run
persistent_abs_delta_s = abs(persistent_delta_s)
rootfs_persistent_delta_s = container_persistent_noop_per_run - native_rootfs_persistent_noop_per_run
rootfs_persistent_abs_delta_s = abs(rootfs_persistent_delta_s)

summary = {
    "max_container_vs_native": max((r.get("container_vs_native", 0.0) for r in comparisons), default=0.0),
    "container_noop_s": container_noop,
    "native_persistent_noop_s": native_persistent_noop,
    "native_persistent_noop_per_run_s": native_persistent_noop_per_run,
    "native_rootfs_persistent_noop_s": native_rootfs_persistent_noop,
    "native_rootfs_persistent_noop_per_run_s": native_rootfs_persistent_noop_per_run,
    "container_persistent_noop_s": container_persistent_noop,
    "container_persistent_noop_per_run_s": container_persistent_noop_per_run,
    "persistent_noop_delta_s": persistent_delta_s,
    "persistent_noop_abs_delta_s": persistent_abs_delta_s,
    "rootfs_persistent_noop_delta_s": rootfs_persistent_delta_s,
    "rootfs_persistent_noop_abs_delta_s": rootfs_persistent_abs_delta_s,
    "persistent_noop_delta_target_s": 0.010,
    "persistent_noop_delta_target_met": rootfs_persistent_abs_delta_s <= 0.010 if container_persistent_noop_per_run else False,
    "native_noop_s": native_noop,
}
artifact = {
    "schema": 1,
    "kind": "pdocker.file-io-bench",
    "git_commit": subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip(),
    "build_flavor": "compat",
    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    "command": "bash scripts/android-file-io-bench.sh",
    "parameters": {
        "size_mb": int(size_mb),
        "small_files": int(small_files),
        "trace_mode": trace_mode,
        "persistent_runs": int(persistent_runs),
    },
    "device_serial": device_serial,
    "host_rc": {
        "native": int(native_rc),
        "native_persistent": int(native_persistent_rc),
        "native_rootfs_persistent": int(native_rootfs_persistent_rc),
        "container": int(container_rc),
        "container_persistent": int(container_persistent_rc),
    },
    "contexts": contexts,
    "events": events,
    "comparisons": comparisons,
    "summary": summary,
}
Path(out_path).write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")

lines = [
    "# pdocker File I/O Benchmark",
    "",
    f"- Commit: `{artifact['git_commit']}`",
    f"- Timestamp: `{artifact['timestamp_utc']}`",
    f"- Size: {size_mb} MiB sequential, {small_files} small files",
    f"- Trace mode: `{trace_mode}`",
    f"- Persistent noop: native app {native_persistent_noop_per_run * 1000.0:.3f} ms/run, native rootfs {native_rootfs_persistent_noop_per_run * 1000.0:.3f} ms/run, container rootfs {container_persistent_noop_per_run * 1000.0:.3f} ms/run",
    f"- Rootfs persistent noop delta: signed {rootfs_persistent_delta_s * 1000.0:.3f} ms/run, absolute {rootfs_persistent_abs_delta_s * 1000.0:.3f} ms/run over {persistent_runs} runs (target absolute delta <= 10 ms)",
    f"- Device: `{device_serial}`",
    f"- Host rc: native `{native_rc}`, native persistent `{native_persistent_rc}`, native rootfs persistent `{native_rootfs_persistent_rc}`, container `{container_rc}`, container persistent `{container_persistent_rc}`",
    "",
    "| operation | native launch s | native app persistent s | native rootfs persistent s | container launch s | container rootfs persistent s | launch ratio | rootfs persistent ratio | native rootfs MiB/s | container rootfs MiB/s | stops |",
    "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
]
for row in comparisons:
    def fmt(value, digits=3):
        return "" if value is None else f"{value:.{digits}f}"
    lines.append(
        "| {label} | {native} | {native_persistent} | {native_rootfs_persistent} | {container} | {persistent} | {ratio} | {persistent_ratio} | {nrpmb} | {pmb} | {stops} |".format(
            label=row["label"],
            native=fmt(row.get("native_real_s")),
            native_persistent=fmt(row.get("native_persistent_real_s")),
            native_rootfs_persistent=fmt(row.get("native_rootfs_persistent_real_s")),
            container=fmt(row.get("container_real_s")),
            persistent=fmt(row.get("container_persistent_real_s")),
            ratio=fmt(row.get("container_vs_native"), 2),
            persistent_ratio=fmt(row.get("container_persistent_vs_native_rootfs_persistent"), 2),
            nrpmb=fmt(row.get("native_rootfs_persistent_mib_s"), 1),
            pmb=fmt(row.get("container_persistent_mib_s"), 1),
            stops="" if row.get("container_stops") is None else row["container_stops"],
        )
    )
lines.extend([
    "",
    "## Interpretation",
    "",
    "- `noop` is the process/direct-executor startup floor; adjusted launch MiB/s subtracts that floor.",
    "- `native app persistent`, `native rootfs persistent`, and `container rootfs persistent` run the same payload inside one long-lived shell, separating steady-state mediation cost from launch cost at the same measurement granularity.",
    "- `native rootfs persistent` writes into the same rootfs backing tree used by the container path, but still uses Android `/system/bin/sh` and Android userland tools.",
    "- Small-file rows emphasize path mediation, metadata syscalls, and shell loop overhead.",
    "- Compile rows emulate build-system traffic without requiring a compiler: source-tree fanout, dependency scanning, object/dep file writes, and archive concatenation.",
    "- Overlay rows target the pdocker layer/COW shape: hardlink-shared lower/upper files, first-write copy-up via `/.libcow.so` when present, truncate, and unlink-style cleanup.",
    "- Sequential rows emphasize bulk read/write throughput through the mediated rootfs.",
])
Path(md_path).write_text("\n".join(lines) + "\n")
print(f"[pdocker file-io bench] wrote {out_path}")
print(f"[pdocker file-io bench] wrote {md_path}")
PY

set +e
"$ADB" push "$OUT" "/data/local/tmp/$REMOTE_JSON" >/dev/null
push_rc=$?
if ((push_rc == 0)); then
  adb_run_as "mkdir -p '$REMOTE_BENCH_DIR' && cp '/data/local/tmp/$REMOTE_JSON' '$REMOTE_BENCH_DIR/$REMOTE_JSON' && cp '$REMOTE_BENCH_DIR/$REMOTE_JSON' '$REMOTE_BENCH_DIR/file-io-bench-latest.json'" >/dev/null
  mirror_rc=$?
else
  mirror_rc=1
fi
set -e
if ((push_rc == 0 && mirror_rc == 0)); then
  echo "[pdocker file-io bench] device artifact: $REMOTE_BENCH_DIR/file-io-bench-latest.json"
else
  echo "[pdocker file-io bench] device artifact mirror failed: push_rc=$push_rc mirror_rc=$mirror_rc" >&2
fi
if ((native_rc != 0 || native_persistent_rc != 0 || native_rootfs_persistent_rc != 0 || container_rc != 0 || container_persistent_rc != 0)); then
  exit 1
fi

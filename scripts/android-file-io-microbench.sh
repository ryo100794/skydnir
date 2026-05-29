#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ADB="${ADB:-adb}"
PKG="${SKYDNIR_ANDROID_PACKAGE:-${SKYDNIR_PACKAGE:-${PDOCKER_ANDROID_PACKAGE:-io.github.ryo100794.pdocker.compat}}}"
FILES="${SKYDNIR_FILE_IO_MICRO_FILES:-${PDOCKER_FILE_IO_MICRO_FILES:-128}}"
BLOCKS="${SKYDNIR_FILE_IO_MICRO_BLOCKS:-${PDOCKER_FILE_IO_MICRO_BLOCKS:-64}}"
BLOCK_SIZE="${SKYDNIR_FILE_IO_MICRO_BLOCK_SIZE:-${PDOCKER_FILE_IO_MICRO_BLOCK_SIZE:-4096}}"
FSYNC="${SKYDNIR_FILE_IO_MICRO_FSYNC:-${PDOCKER_FILE_IO_MICRO_FSYNC:-0}}"
OPEN_CLOSE="${SKYDNIR_FILE_IO_MICRO_OPEN_CLOSE:-${PDOCKER_FILE_IO_MICRO_OPEN_CLOSE:-1000}}"
TRACE_MODE="${PDOCKER_DIRECT_TRACE_MODE:-seccomp}"
DOCUMENTS_HOST="${SKYDNIR_FILE_IO_MICRO_DOCUMENTS_HOST:-${PDOCKER_FILE_IO_MICRO_DOCUMENTS_HOST:-}}"
EXPORT_DOCUMENTS="${SKYDNIR_FILE_IO_MICRO_EXPORT_DOCUMENTS:-${PDOCKER_FILE_IO_MICRO_EXPORT_DOCUMENTS:-1}}"
DOCUMENTS_EXPORT_SUBDIR="${SKYDNIR_FILE_IO_MICRO_DOCUMENTS_SUBDIR:-${PDOCKER_FILE_IO_MICRO_DOCUMENTS_SUBDIR:-skydnir}}"
OUT="${SKYDNIR_FILE_IO_MICRO_OUT:-${PDOCKER_FILE_IO_MICRO_OUT:-$ROOT/docs/test/file-io-microbench-latest.json}}"
MD_OUT="${SKYDNIR_FILE_IO_MICRO_MD:-${PDOCKER_FILE_IO_MICRO_MD:-$ROOT/docs/test/file-io-microbench-latest.md}}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REMOTE_BENCH_DIR="files/pdocker/bench"
RAW="$(mktemp "${TMPDIR:-/tmp}/pdocker-file-io-micro.XXXXXX.log")"
BIN="/tmp/pdocker-fileio-microbench-$STAMP"
DIRECT="/tmp/pdocker-direct-microbench-$STAMP"
trap 'rm -f "$RAW" "$BIN" "$DIRECT"' EXIT

usage() {
  cat <<EOF
Usage: scripts/android-file-io-microbench.sh [--files N] [--blocks N] [--block-size N] [--fsync 0|1] [--open-close N] [--documents-host PATH] [--out PATH]

Runs the same static AArch64 file-I/O microbenchmark binary directly in the
APK domain and through pdocker-direct against the same rootfs backing tree.
This is the performance benchmark for direct-executor file syscall overhead;
the shell-based file-io bench is diagnostic only.

The default syscall-granularity probe opens and closes the same file 1000
times, then repeats the same workload across app-private, rootfs, container,
and configured Documents/SD-backed paths when those paths are available.
EOF
}

while (($#)); do
  case "$1" in
    --files) shift; FILES="${1:?--files requires a value}" ;;
    --blocks) shift; BLOCKS="${1:?--blocks requires a value}" ;;
    --block-size) shift; BLOCK_SIZE="${1:?--block-size requires a value}" ;;
    --fsync) shift; FSYNC="${1:?--fsync requires a value}" ;;
    --open-close) shift; OPEN_CLOSE="${1:?--open-close requires a value}" ;;
    --documents-host) shift; DOCUMENTS_HOST="${1:?--documents-host requires a value}" ;;
    --out) shift; OUT="${1:?--out requires a value}" ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

case "$DOCUMENTS_EXPORT_SUBDIR" in
  /*|*..*)
    echo "unsafe Documents export subdir: $DOCUMENTS_EXPORT_SUBDIR" >&2
    exit 2
    ;;
esac

remote_quote() {
  printf "'%s'" "$(printf "%s" "$1" | sed "s/'/'\\\\''/g")"
}

adb_run_as() {
  "$ADB" shell "run-as $PKG sh -c $(remote_quote "$1")"
}

export_documents_artifacts() {
  [[ "$EXPORT_DOCUMENTS" != "0" ]] || return 0
  local remote_json="/data/local/tmp/file-io-microbench-$STAMP.json"
  local remote_md="/data/local/tmp/file-io-microbench-$STAMP.md"
  "$ADB" push "$OUT" "$remote_json" >/dev/null || true
  "$ADB" push "$MD_OUT" "$remote_md" >/dev/null || true
  "$ADB" shell "chmod 644 $(remote_quote "$remote_json") $(remote_quote "$remote_md")" >/dev/null 2>&1 || true
  local export_info
  export_info="$(adb_run_as "
set -eu
DOC_HOST=$(remote_quote "$DOCUMENTS_HOST")
if test -z \"\$DOC_HOST\"; then
  ENV_FILE='files/pdocker/projects/.skydnir-common.env'
  if ! test -f \"\$ENV_FILE\"; then
    ENV_FILE='files/pdocker/projects/.pdocker-common.env'
  fi
  if test -f \"\$ENV_FILE\"; then
    DOC_HOST=\$(sed -n 's/^SKYDNIR_DOCUMENTS_HOST=//p; s/^PDOCKER_DOCUMENTS_HOST=//p' \"\$ENV_FILE\" | tail -1)
    DOC_HOST=\$(printf '%s' \"\$DOC_HOST\" | sed 's/^\"//; s/\"\$//; s/^'\''//; s/'\''\$//')
  fi
fi
ENV_FILE='files/pdocker/projects/.skydnir-common.env'
if ! test -f \"\$ENV_FILE\"; then
  ENV_FILE='files/pdocker/projects/.pdocker-common.env'
fi
SELECTED_HOST=\$(sed -n 's/^SKYDNIR_DOCUMENTS_SELECTED_HOST=//p; s/^PDOCKER_DOCUMENTS_SELECTED_HOST=//p' \"\$ENV_FILE\" 2>/dev/null | tail -1)
SELECTED_HOST=\$(printf '%s' \"\$SELECTED_HOST\" | sed 's/^\"//; s/\"\$//; s/^'\''//; s/'\''\$//')
MEDIATOR=\$(sed -n 's/^SKYDNIR_DOCUMENTS_MEDIATOR=//p; s/^PDOCKER_DOCUMENTS_MEDIATOR=//p' \"\$ENV_FILE\" 2>/dev/null | tail -1)
MEDIATOR=\$(printf '%s' \"\$MEDIATOR\" | sed 's/^\"//; s/\"\$//; s/^'\''//; s/'\''\$//')
if test -n \"\$DOC_HOST\"; then
  printf '__SKYDNIR_FILE_IO_MICRO_DOCUMENTS_EXPORT__:host=%s;selected=%s;mediator=%s;latest=%s\n' \"\$DOC_HOST\" \"\$SELECTED_HOST\" \"\$MEDIATOR\" \"\$DOC_HOST/$DOCUMENTS_EXPORT_SUBDIR/test-runs/latest-benchmark\"
else
  echo '__SKYDNIR_FILE_IO_MICRO_DOCUMENTS_EXPORT__:skipped=no-documents-host'
fi
")"
  printf '%s\n' "$export_info"
  local mediator selected latest_rel
  mediator="$(printf '%s\n' "$export_info" | sed -n 's/.*mediator=\([^;]*\).*/\1/p' | tail -1)"
  selected="$(printf '%s\n' "$export_info" | sed -n 's/.*selected=\([^;]*\).*/\1/p' | tail -1)"
  if [[ -n "$selected" ]]; then
    echo "[Skydnir file-io microbench] writing benchmark artifacts directly through Documents provider"
    local write_rc=0
    write_documents_file "$remote_json" "$DOCUMENTS_EXPORT_SUBDIR/test-runs/file-io-microbench-$STAMP/file-io-microbench.json" "application/json" || write_rc=1
    write_documents_file "$remote_md" "$DOCUMENTS_EXPORT_SUBDIR/test-runs/file-io-microbench-$STAMP/file-io-microbench.md" "text/markdown" || write_rc=1
    write_documents_file "$remote_json" "$DOCUMENTS_EXPORT_SUBDIR/test-runs/latest-benchmark/file-io-microbench.json" "application/json" || write_rc=1
    write_documents_file "$remote_md" "$DOCUMENTS_EXPORT_SUBDIR/test-runs/latest-benchmark/file-io-microbench.md" "text/markdown" || write_rc=1
    latest_rel="$DOCUMENTS_EXPORT_SUBDIR/test-runs/latest-benchmark/file-io-microbench.json"
    local deadline=$((SECONDS + 60))
    while (( SECONDS < deadline )); do
      if "$ADB" shell "test -s $(remote_quote "$selected/$latest_rel")" >/dev/null 2>&1; then
        echo "[Skydnir file-io microbench] Documents export: $selected/$latest_rel"
        return 0
      fi
      sleep 2
    done
    if (( write_rc == 0 )); then
      echo "[Skydnir file-io microbench] Documents export completed through mediator; selected path is not directly visible over adb"
      adb_run_as "cat files/pdocker/diagnostics/saf-write-latest.json 2>/dev/null || true" || true
      return 0
    fi
    echo "[Skydnir file-io microbench] Documents direct export pending or unavailable: $selected/$latest_rel mediator=$mediator" >&2
    return 1
  fi
}

write_documents_file() {
  local source="$1"
  local target="$2"
  local mime="$3"
  "$ADB" shell am start \
      -n "$PKG/io.github.ryo100794.pdocker.MainActivity" \
      -a io.github.ryo100794.pdocker.action.SMOKE_DOCUMENTS_WRITE_FILE \
      --es source "$source" \
      --es target "$target" \
      --es mimeType "$mime" >/dev/null || true
  local deadline=$((SECONDS + 30))
  while (( SECONDS < deadline )); do
    local diag
    diag="$(adb_run_as "cat files/pdocker/diagnostics/saf-write-latest.json 2>/dev/null || true" || true)"
    if printf '%s\n' "$diag" | grep -F "\"Target\": \"$target\"" >/dev/null &&
       printf '%s\n' "$diag" | grep -F '"Success": true' >/dev/null; then
      return 0
    fi
    if printf '%s\n' "$diag" | grep -F "\"Target\": \"$target\"" >/dev/null &&
       printf '%s\n' "$diag" | grep -F '"Success": false' >/dev/null; then
      printf '%s\n' "$diag" >&2
      return 1
    fi
    sleep 1
  done
  echo "[Skydnir file-io microbench] Documents write did not report completion: $target" >&2
  return 1
}

echo "[Skydnir file-io microbench] building static workload"
aarch64-linux-gnu-gcc -O2 -Wall -Wextra -static \
  -o "$BIN" tools/pdocker_fileio_microbench.c

echo "[Skydnir file-io microbench] building direct executor"
bash scripts/build-native-android-ndk.sh >/dev/null
cp app/src/main/jniLibs/arm64-v8a/libpdockerdirect.so "$DIRECT"

"$ADB" push "$BIN" "/data/local/tmp/$(basename "$BIN")" >/dev/null
"$ADB" push "$DIRECT" "/data/local/tmp/$(basename "$DIRECT")" >/dev/null

device_serial="$("$ADB" get-serialno 2>&1 || true)"
cat >"$RAW" <<EOF
__PDIO_MICRO_CONTEXT__:device=$device_serial;files=$FILES;blocks=$BLOCKS;block_size=$BLOCK_SIZE;fsync=$FSYNC;open_close=$OPEN_CLOSE;trace_mode=$TRACE_MODE
EOF

set +e
adb_run_as "
set +e
cd files || exit 1
R=\$(find pdocker/containers -mindepth 2 -maxdepth 2 -type d -name rootfs 2>/dev/null | head -1)
if test -z \"\$R\"; then
  R=\$(find pdocker/images -mindepth 2 -maxdepth 3 -type d -name rootfs 2>/dev/null | head -1)
fi
if test -z \"\$R\"; then
  echo '__PDIO_MICRO_ERROR__:no-rootfs'
  exit 2
fi
mkdir -p pdocker/bench \"\$R/tmp\" || exit 1
WORKLOAD=\"\$R/tmp/pdocker_fileio_microbench\"
APP_WORKLOAD=\"pdocker/bench/pdocker_fileio_microbench\"
DIRECT=\"pdocker/bench/pdocker-direct-microbench\"
cp '/data/local/tmp/$(basename "$BIN")' \"\$WORKLOAD\" && chmod 755 \"\$WORKLOAD\"
cp '/data/local/tmp/$(basename "$BIN")' \"\$APP_WORKLOAD\" && chmod 755 \"\$APP_WORKLOAD\"
cp '/data/local/tmp/$(basename "$DIRECT")' \"\$DIRECT\" && chmod 755 \"\$DIRECT\"
DOC_HOST=$(remote_quote "$DOCUMENTS_HOST")
if test -z \"\$DOC_HOST\"; then
  ENV_FILE=\$(find pdocker/projects -maxdepth 3 -name .skydnir-common.env 2>/dev/null | head -1)
  if test -z \"\$ENV_FILE\"; then
    ENV_FILE=\$(find pdocker/projects -maxdepth 3 -name .pdocker-common.env 2>/dev/null | head -1)
  fi
  if test -n \"\$ENV_FILE\"; then
    DOC_HOST=\$(sed -n 's/^SKYDNIR_DOCUMENTS_HOST=//p; s/^PDOCKER_DOCUMENTS_HOST=//p' \"\$ENV_FILE\" | tail -1)
    DOC_HOST=\$(printf '%s' \"\$DOC_HOST\" | sed 's/^\"//; s/\"\$//; s/^'\''//; s/'\''\$//')
  fi
fi
echo \"__PDIO_MICRO_CONTEXT__:rootfs=\$R;workload=\$WORKLOAD;app_workload=\$APP_WORKLOAD;direct=\$DIRECT;documents_host=\$DOC_HOST\"
run_native_scope() {
  scope=\"\$1\"
  bench_root=\"\$2\"
  echo \"__PDIO_MICRO_BEGIN__:\$scope\"
  \"\$APP_WORKLOAD\" \"\$bench_root\" '$FILES' '$BLOCKS' '$BLOCK_SIZE' '$FSYNC' '$OPEN_CLOSE'
  rc=\$?
  echo \"__PDIO_MICRO_END__:\$scope:rc=\$rc\"
  return \$rc
}
run_container_scope() {
  scope=\"\$1\"
  workdir=\"\$2\"
  bench_root=\"\$3\"
  shift 3
  echo \"__PDIO_MICRO_BEGIN__:\$scope\"
  \"\$DIRECT\" run --mode bench --rootfs \"\$R\" --workdir \"\$workdir\" \"\$@\" -- /tmp/pdocker_fileio_microbench \"\$bench_root\" '$FILES' '$BLOCKS' '$BLOCK_SIZE' '$FSYNC' '$OPEN_CLOSE'
  rc=\$?
  echo \"__PDIO_MICRO_END__:\$scope:rc=\$rc\"
  return \$rc
}
native_app_rc=0
native_rootfs_rc=0
container_rootfs_rc=0
container_app_rc=0
native_documents_rc=0
container_documents_rc=0

APP_ROOT=\"pdocker/bench/pdocker-fileio-micro-native-app-$STAMP\"
mkdir -p \"\$APP_ROOT\" || exit 1
run_native_scope native_app \"\$APP_ROOT\"
native_app_rc=\$?
echo '__PDIO_MICRO_BEGIN__:native_rootfs'
(cd \"\$R/tmp\" && ./pdocker_fileio_microbench \"pdocker-fileio-micro-native-rootfs-$STAMP\" '$FILES' '$BLOCKS' '$BLOCK_SIZE' '$FSYNC' '$OPEN_CLOSE')
native_rc=\$?
echo \"__PDIO_MICRO_END__:native_rootfs:rc=\$native_rc\"
native_rootfs_rc=\$native_rc
echo '__PDIO_MICRO_BEGIN__:container_rootfs'
export PDOCKER_DIRECT_EXPERIMENTAL_PROCESS_EXEC=1
export PDOCKER_DIRECT_TRACE_MODE='$TRACE_MODE'
export PDOCKER_DIRECT_TRACE_SYSCALLS=0
export PDOCKER_DIRECT_TRACE_VERBOSE=0
export PDOCKER_DIRECT_TRACE_PATHS=0
export PDOCKER_DIRECT_STATS=1
\"\$DIRECT\" run --mode bench --rootfs \"\$R\" --workdir /tmp -- /tmp/pdocker_fileio_microbench \"pdocker-fileio-micro-container-rootfs-$STAMP\" '$FILES' '$BLOCKS' '$BLOCK_SIZE' '$FSYNC' '$OPEN_CLOSE'
container_rc=\$?
echo \"__PDIO_MICRO_END__:container_rootfs:rc=\$container_rc\"
container_rootfs_rc=\$container_rc

APP_BIND_ROOT=\"pdocker/bench/pdocker-fileio-micro-container-app-bind-$STAMP\"
mkdir -p \"\$APP_BIND_ROOT\"
run_container_scope container_app_bind /bench/app /bench/app/pdocker-fileio-micro-container-app-bind-$STAMP --bind \"\$APP_BIND_ROOT:/bench/app\"
container_app_rc=\$?

if test -n \"\$DOC_HOST\" && test -d \"\$DOC_HOST\" && test -w \"\$DOC_HOST\"; then
  DOC_ROOT=\"\$DOC_HOST/pdocker-fileio-micro-$STAMP\"
  mkdir -p \"\$DOC_ROOT\"
  run_native_scope native_documents \"\$DOC_ROOT/native\"
  native_documents_rc=\$?
  run_container_scope container_documents_bind /documents /documents/pdocker-fileio-micro-$STAMP/container --bind \"\$DOC_HOST:/documents\"
  container_documents_rc=\$?
  rm -rf \"\$DOC_ROOT\"
else
  echo \"__PDIO_MICRO_SKIP__:documents:host=\$DOC_HOST\"
fi

rm -rf \"\$APP_ROOT\" \"\$APP_BIND_ROOT\" \"\$R/tmp/pdocker-fileio-micro-native-rootfs-$STAMP\" \"\$R/tmp/pdocker-fileio-micro-container-rootfs-$STAMP\"
exit \$((native_app_rc != 0 || native_rootfs_rc != 0 || container_rootfs_rc != 0 || container_app_rc != 0 || native_documents_rc != 0 || container_documents_rc != 0))
" 2>&1 | tee -a "$RAW"
run_rc=${PIPESTATUS[0]}
set -e

mkdir -p "$(dirname "$OUT")" "$(dirname "$MD_OUT")"
python3 - "$RAW" "$OUT" "$MD_OUT" "$FILES" "$BLOCKS" "$BLOCK_SIZE" "$FSYNC" "$OPEN_CLOSE" "$TRACE_MODE" "$device_serial" "$run_rc" <<'PY'
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

raw_path, out_path, md_path, files, blocks, block_size, fsync, open_close, trace_mode, device_serial, run_rc = sys.argv[1:12]
lines = Path(raw_path).read_text(errors="replace").splitlines()
contexts = []
events = []
current = None
for line in lines:
    if line.startswith("__PDIO_MICRO_CONTEXT__:"):
        contexts.append(line.split(":", 1)[1])
        continue
    if line.startswith("__PDIO_MICRO_SKIP__:"):
        events.append({"scope": line.split(":", 2)[1], "skipped": True, "raw_tail": [line]})
        continue
    m = re.match(r"__PDIO_MICRO_BEGIN__:([A-Za-z0-9_]+)$", line)
    if m:
        current = {"scope": m.group(1), "results": [], "raw_tail": []}
        continue
    m = re.match(r"__PDIO_MICRO_END__:([A-Za-z0-9_]+):rc=([0-9]+)$", line)
    if m and current:
        current["rc"] = int(m.group(2))
        events.append(current)
        current = None
        continue
    if current is not None:
        if line.startswith("{"):
            try:
                current["results"].append(json.loads(line))
            except json.JSONDecodeError:
                current["raw_tail"].append(line)
        else:
            current["raw_tail"].append(line)
git_commit = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
git_status = subprocess.check_output(["git", "status", "--short"], text=True).splitlines()

for event in events:
    stats = "\n".join(event["raw_tail"])
    m = re.search(r"stops=([0-9]+)", stats)
    if m:
        event["direct_stops"] = int(m.group(1))
    top = []
    for rank, nr, name, count in re.findall(r"#([0-9]+) nr=([0-9]+)\(([^)]+)\) count=([0-9]+)", stats):
        top.append({"rank": int(rank), "nr": int(nr), "name": name, "count": int(count)})
    if top:
        event["top_syscalls"] = top
    event["raw_tail"] = event["raw_tail"][-20:]

by_scope = {e["scope"]: {r["label"]: r for r in e.get("results", [])} for e in events}
comparisons = []
scopes = [
    "native_app",
    "native_rootfs",
    "container_rootfs",
    "container_app_bind",
    "native_documents",
    "container_documents_bind",
]
labels = []
for event in events:
    for result in event.get("results", []):
        label = result.get("label")
        if label and label not in labels:
            labels.append(label)
for label in labels:
    row = {"label": label}
    for scope in scopes:
        result = by_scope.get(scope, {}).get(label)
        if result:
            row[f"{scope}_ms"] = result["elapsed_ms"]
            row[f"{scope}_ops"] = result["ops"]
            row[f"{scope}_bytes"] = result["bytes"]
    def compare(container_scope, native_scope, key):
        c = by_scope.get(container_scope, {}).get(label)
        n = by_scope.get(native_scope, {}).get(label)
        if c and n and n["elapsed_ms"] > 0:
            overhead = c["elapsed_ms"] - n["elapsed_ms"]
            ops = max(1, int(c.get("ops") or n.get("ops") or 1))
            row[f"{key}_overhead_ms"] = overhead
            row[f"{key}_ratio"] = c["elapsed_ms"] / n["elapsed_ms"]
            row[f"{key}_overhead_ms_per_op"] = overhead / ops
            row[f"{key}_target_met"] = (overhead / ops) <= 1.0
    compare("container_rootfs", "native_rootfs", "rootfs")
    compare("container_app_bind", "native_app", "app_bind")
    compare("container_documents_bind", "native_documents", "documents_bind")
    comparisons.append(row)

overhead_keys = [
    "rootfs_overhead_ms_per_op",
    "app_bind_overhead_ms_per_op",
    "documents_bind_overhead_ms_per_op",
]
artifact = {
    "schema": 1,
    "kind": "pdocker.file-io-microbench",
    "git_commit": git_commit,
    "git_dirty": bool(git_status),
    "git_status": git_status,
    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    "device_serial": device_serial,
    "parameters": {
        "files": int(files),
        "blocks": int(blocks),
        "block_size": int(block_size),
        "fsync": int(fsync),
        "open_close": int(open_close),
        "trace_mode": trace_mode,
    },
    "run_rc": int(run_rc),
    "contexts": contexts,
    "events": events,
    "comparisons": comparisons,
    "summary": {
        "all_rc_zero": int(run_rc) == 0 and all(e.get("rc") == 0 for e in events),
        "max_overhead_ms_per_op": max((r.get(k, 0.0) for r in comparisons for k in overhead_keys), default=0.0),
        "target_overhead_ms_per_op": 1.0,
        "target_met": all(
            r.get(k, True)
            for r in comparisons
            for k in ("rootfs_target_met", "app_bind_target_met", "documents_bind_target_met")
        ),
    },
}
Path(out_path).write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")

md = [
    "# Skydnir File I/O Microbenchmark",
    "",
    f"- Commit: `{artifact['git_commit']}`",
    f"- Dirty tree: `{artifact['git_dirty']}`",
    f"- Timestamp: `{artifact['timestamp_utc']}`",
    f"- Device: `{device_serial}`",
    f"- Workload: files={files}, blocks={blocks}, block_size={block_size}, fsync={fsync}",
    f"- Target: direct executor overhead <= 1 ms per file operation",
    f"- Result: {'PASS' if artifact['summary']['target_met'] else 'FAIL'}; max per-op overhead {artifact['summary']['max_overhead_ms_per_op']:.3f} ms",
    "",
    "| operation | ops | native app ms | native rootfs ms | container rootfs ms | rootfs overhead ms/op | container app bind ms | app bind overhead ms/op | native docs ms | container docs bind ms | docs overhead ms/op |",
    "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
]
for row in comparisons:
    md.append(
        "| {label} | {ops} | {native_app:.3f} | {native_rootfs:.3f} | {container_rootfs:.3f} | {rootfs_overhead:.3f} | {container_app:.3f} | {app_overhead:.3f} | {native_docs:.3f} | {container_docs:.3f} | {docs_overhead:.3f} |".format(
            label=row["label"],
            ops=int(row.get("container_rootfs_ops") or row.get("native_rootfs_ops") or row.get("native_app_ops") or 0),
            native_app=row.get("native_app_ms", 0.0),
            native_rootfs=row.get("native_rootfs_ms", 0.0),
            container_rootfs=row.get("container_rootfs_ms", 0.0),
            rootfs_overhead=row.get("rootfs_overhead_ms_per_op", 0.0),
            container_app=row.get("container_app_bind_ms", 0.0),
            app_overhead=row.get("app_bind_overhead_ms_per_op", 0.0),
            native_docs=row.get("native_documents_ms", 0.0),
            container_docs=row.get("container_documents_bind_ms", 0.0),
            docs_overhead=row.get("documents_bind_overhead_ms_per_op", 0.0),
        )
    )
md.extend([
    "",
    "## Method",
    "",
    "- All rows run the same static AArch64 benchmark binary.",
    "- `open_close` opens and closes the same existing file 1000 times by default.",
    "- `*_only` rows time a hot loop around the named syscall with setup and cleanup outside the measured region where practical.",
    "- `*_pair` rows report the combined cost of the named syscall pair because the filesystem object must be returned to its starting state each iteration.",
    "- `native app` uses the app-private pdocker bench folder without the container executor.",
    "- `native rootfs` executes directly in the APK domain against the same rootfs backing path used by the container.",
    "- `container rootfs` executes through `pdocker-direct`; the same executor behavior is used as normal container execution.",
    "- `container app bind` binds an app-private folder into the container and repeats the same workload.",
    "- `native/container documents` run only when a direct writable Documents/SD path is configured or passed with `--documents-host`.",
    "- Timing is measured inside the benchmark process around direct file syscall loops, not around shell command startup.",
])
Path(md_path).write_text("\n".join(md) + "\n")
print(f"[Skydnir file-io microbench] wrote {out_path}")
print(f"[Skydnir file-io microbench] wrote {md_path}")
PY

"$ADB" push "$OUT" "/data/local/tmp/file-io-microbench-$STAMP.json" >/dev/null || true
adb_run_as "mkdir -p '$REMOTE_BENCH_DIR' && cp '/data/local/tmp/file-io-microbench-$STAMP.json' '$REMOTE_BENCH_DIR/file-io-microbench-$STAMP.json' && cp '$REMOTE_BENCH_DIR/file-io-microbench-$STAMP.json' '$REMOTE_BENCH_DIR/file-io-microbench-latest.json'" >/dev/null || true
export_documents_artifacts || run_rc=1

exit "$run_rc"

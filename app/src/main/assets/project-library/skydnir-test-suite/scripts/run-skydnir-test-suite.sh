#!/bin/sh
set -eu

report_dir="${SKYDNIR_TEST_SUITE_REPORT_DIR:-${PDOCKER_TEST_SUITE_REPORT_DIR:-/reports}}"
documents_mount="${SKYDNIR_DOCUMENTS_MOUNT:-${PDOCKER_DOCUMENTS_MOUNT:-/documents}}"
shared_mount="${SKYDNIR_SHARED_DOCUMENTS_MOUNT:-${PDOCKER_SHARED_DOCUMENTS_MOUNT:-/shared}}"
export_dir="${SKYDNIR_EXPORT_DIR:-${PDOCKER_EXPORT_DIR:-${documents_mount}/skydnir-exports}}/skydnir-test-suite"
fast_workdir="${SKYDNIR_FAST_WORKDIR:-${PDOCKER_FAST_WORKDIR:-/workspace}}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ 2>/dev/null || date +%s)"
scenario="all"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --scenario)
      shift
      scenario="${1:-}"
      ;;
    --scenario=*)
      scenario="${1#--scenario=}"
      ;;
    --help|-h)
      cat <<'EOF'
Usage: run-skydnir-test-suite [--scenario all|smoke|direct|io|archive|documents]

Runs Skydnir's reusable in-container test scenarios and writes JSON/log
artifacts to /reports and /documents/skydnir-exports/skydnir-test-suite.
EOF
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 2
      ;;
  esac
  shift
done

case "$scenario" in
  all|smoke|direct|io|archive|documents) ;;
  *)
    echo "unknown scenario: $scenario" >&2
    exit 2
    ;;
esac

mkdir -p "$report_dir" "$export_dir" "$fast_workdir" "$shared_mount"

log="$report_dir/skydnir-test-suite-$timestamp.log"
json="$report_dir/skydnir-test-suite-$timestamp.json"
latest_log="$report_dir/latest.log"
latest_json="$report_dir/latest.json"
export_log="$export_dir/skydnir-test-suite-$timestamp.log"
export_json="$export_dir/skydnir-test-suite-$timestamp.json"
export_latest_log="$export_dir/latest.log"
export_latest_json="$export_dir/latest.json"

passes=0
failures=0
results_file="$report_dir/.skydnir-test-suite-results-$timestamp"
: > "$results_file"

log_line() {
  printf '%s\n' "$*" | tee -a "$log"
}

record_case() {
  name="$1"
  group="$2"
  status="$3"
  detail="$4"
  if [ "$status" = "pass" ]; then
    passes=$((passes + 1))
  else
    failures=$((failures + 1))
  fi
  printf '%s\t%s\t%s\t%s\n' "$name" "$group" "$status" "$detail" >> "$results_file"
  log_line "[$status] $group/$name - $detail"
}

run_case() {
  name="$1"
  group="$2"
  shift
  shift
  tmp="$report_dir/.case-$name-$timestamp.log"
  set +e
  "$@" > "$tmp" 2>&1
  rc=$?
  set -e
  detail="$(tr '\n' ' ' < "$tmp" | sed 's/[[:space:]][[:space:]]*/ /g; s/^ //; s/ $//')"
  if [ -z "$detail" ]; then
    detail="rc=$rc"
  fi
  if [ "$rc" -eq 0 ]; then
    record_case "$name" "$group" "pass" "$detail"
  else
    record_case "$name" "$group" "fail" "rc=$rc $detail"
  fi
  rm -f "$tmp"
}

run_selected_case() {
  case_name="$1"
  group="$2"
  shift
  shift
  case "$scenario:$group" in
    all:*|smoke:smoke|direct:direct|io:io|archive:archive|documents:documents)
      run_case "$case_name" "$group" "$@"
      ;;
  esac
}

json_escape() {
  sed 's/\\/\\\\/g; s/"/\\"/g; s/	/\\t/g' | awk '
    BEGIN { printf "\"" }
    NR > 1 { printf "\\n" }
    { printf "%s", $0 }
    END { print "\"" }
  '
}

case_documents_writable() {
  mkdir -p "$export_dir"
  probe="$export_dir/write-probe-$timestamp.txt"
  printf 'documents-write-ok\n' > "$probe"
  grep -q documents-write-ok "$probe"
  printf '%s' "$probe"
}

case_workspace_writable() {
  probe="$fast_workdir/write-probe-$timestamp.txt"
  printf 'workspace-write-ok\n' > "$probe"
  grep -q workspace-write-ok "$probe"
  printf '%s' "$probe"
}

case_argv_preservation() {
  out="$report_dir/.argv-preservation-$timestamp.out"
  long_obj='ggml/src/ggml-vulkan/CMakeFiles/ggml-vulkan.dir/flash_attn_split_k_reduce.comp.cpp.o'
  /bin/sh -c '
i=0
for arg in "$@"; do
  i=$((i + 1))
  printf 'arg%03d=%s\n' "$i" "$arg"
done
  ' sh "flash_attn_mask_opt.comp.cpp.o" "$long_obj" "bracket-[b]-argument" > "$out" || return 1
  grep -F "arg001=flash_attn_mask_opt.comp.cpp.o" "$out" >/dev/null
  grep -F "arg002=$long_obj" "$out" >/dev/null
  grep -F "arg003=bracket-[b]-argument" "$out" >/dev/null
  rm -f "$out"
  printf 'argv preserved'
}

case_linker_argv_preservation() {
  out="$report_dir/.linker-argv-preservation-$timestamp.out"
  probe="$report_dir/.pdocker-linker-argv-dump-$timestamp"
  cat > "$probe" <<'SH'
#!/bin/sh
i=0
for arg in "$@"; do
  i=$((i + 1))
  printf 'arg%03d=%s\n' "$i" "$arg"
done
printf 'argc=%s\n' "$i"
SH
  chmod 755 "$probe"
  set -- cmake -B build -G Ninja -DGGML_BLAS=ON -DGGML_BLAS_VENDOR=OpenBLAS
  for obj in \
    flash_attn.comp.cpp.o \
    flash_attn_cm1.comp.cpp.o \
    flash_attn_cm2.comp.cpp.o \
    flash_attn_mask_opt.comp.cpp.o \
    flash_attn_split_k_reduce.comp.cpp.o
  do
    set -- "$@" "ggml/src/ggml-vulkan/CMakeFiles/ggml-vulkan.dir/$obj"
  done
  i=0
  while [ "$i" -lt 96 ]; do
    set -- "$@" "ggml/src/ggml-vulkan/CMakeFiles/ggml-vulkan.dir/generated_flash_attn_$i.comp.cpp.o"
    i=$((i + 1))
  done
  "$probe" "$@" > "$out"
  grep -F "arg001=cmake" "$out" >/dev/null
  grep -F "arg006=-DGGML_BLAS=ON" "$out" >/dev/null
  grep -F "arg008=ggml/src/ggml-vulkan/CMakeFiles/ggml-vulkan.dir/flash_attn.comp.cpp.o" "$out" >/dev/null
  grep -F "flash_attn_mask_opt.comp.cpp.o" "$out" >/dev/null
  grep -F "flash_attn_split_k_reduce.comp.cpp.o" "$out" >/dev/null
  grep -F "generated_flash_attn_95.comp.cpp.o" "$out" >/dev/null
  ! grep -Eq '^arg[0-9]+=flash$' "$out"
  grep -F "argc=108" "$out" >/dev/null
  rm -f "$out" "$probe"
  printf 'linker argv preserved count=108'
}

case_shell_bracket() {
  /usr/bin/[ -x /bin/sh ]
  printf 'bracket executable ok'
}

case_proc_exe() {
  target="$(readlink /proc/self/exe)"
  test -n "$target"
  printf '%s' "$target"
}

case_file_io_smoke() {
  root="$fast_workdir/file-io-smoke-$timestamp"
  path="$root/probe.txt"
  mkdir -p "$root"
  i=0
  while [ "$i" -lt 100 ]; do
    printf x >> "$path"
    i=$((i + 1))
  done
  bytes="$(wc -c < "$path" | tr -d ' ')"
  [ "$bytes" = "100" ]
  printf 'open_write_close_100_bytes=%s' "$bytes"
}

case_archive_roundtrip() {
  src="$fast_workdir/archive-src-$timestamp"
  dst="$fast_workdir/archive-dst-$timestamp"
  mkdir -p "$src/dir" "$dst"
  printf 'archive-ok\n' > "$src/dir/file.txt"
  (cd "$src" && tar -cf "$report_dir/archive-$timestamp.tar" .)
  (cd "$dst" && tar -xf "$report_dir/archive-$timestamp.tar")
  grep -q archive-ok "$dst/dir/file.txt"
  printf 'tar roundtrip ok'
}

case_shared_mount_visible() {
  if [ ! -d "$shared_mount" ]; then
    echo "$shared_mount missing"
    return 1
  fi
  printf 'shared mount visible: %s' "$shared_mount"
}

case_direct_runtime_probe() {
  probe="pdocker-container-probe"
  if ! command -v "$probe" >/dev/null 2>&1; then
    script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
    probe="$script_dir/pdocker-container-probe.sh"
  fi
  PDOCKER_CONTAINER_PROBE_LARGE_MB="${PDOCKER_CONTAINER_PROBE_LARGE_MB:-64}" \
    "$probe"
}

case_path_semantics() {
  root="$fast_workdir/path-semantics-$timestamp"
  sub="$root/sub"
  mkdir -p "$sub"
  printf 'target\n' > "$sub/target.txt"
  test -s "$sub/target.txt"
  rm -f "$sub/link"
  ln -s target.txt "$sub/link"
  grep -q target "$sub/link"
  rm -f "$sub/target.txt"
  printf 'replacement\n' > "$sub/target.txt"
  grep -q replacement "$sub/link"
  printf 'path semantics ok'
}

case_invalid_inputs() {
  set +e
  "$0" --scenario does-not-exist >/dev/null 2>&1
  rc=$?
  set -e
  [ "$rc" -eq 2 ]
  printf 'invalid input rejection ok'
}

: > "$log"
log_line "Skydnir test suite start: $timestamp"
log_line "scenario: $scenario"
log_line "reports: $report_dir"
log_line "documents export: $export_dir"
log_line "workspace: $fast_workdir"

run_selected_case documents_writable documents case_documents_writable
run_selected_case shared_mount_visible documents case_shared_mount_visible
run_selected_case workspace_writable smoke case_workspace_writable
run_selected_case argv_preservation direct case_argv_preservation
run_selected_case linker_argv_preservation direct case_linker_argv_preservation
run_selected_case shell_bracket direct case_shell_bracket
run_selected_case proc_exe direct case_proc_exe
run_selected_case direct_runtime_probe direct case_direct_runtime_probe
run_selected_case path_semantics direct case_path_semantics
run_selected_case invalid_inputs smoke case_invalid_inputs
run_selected_case file_io_smoke io case_file_io_smoke
run_selected_case archive_roundtrip archive case_archive_roundtrip

if [ "$failures" -eq 0 ]; then
  status=pass
else
  status=fail
fi

{
  printf '{\n'
  printf '  "schema": "pdocker.test-suite.v1",\n'
  printf '  "status": "%s",\n' "$status"
  printf '  "timestamp": "%s",\n' "$timestamp"
  printf '  "scenario": %s,\n' "$(printf '%s' "$scenario" | json_escape)"
  printf '  "passes": %s,\n' "$passes"
  printf '  "failures": %s,\n' "$failures"
  printf '  "report_dir": %s,\n' "$(printf '%s' "$report_dir" | json_escape)"
  printf '  "documents_dir": %s,\n' "$(printf '%s' "$export_dir" | json_escape)"
  printf '  "cases": [\n'
  first=1
  while IFS="$(printf '\t')" read -r name group case_status detail; do
    if [ "$first" -eq 0 ]; then
      printf ',\n'
    fi
    first=0
    printf '    {"name": %s, "group": %s, "status": %s, "detail": %s}' \
      "$(printf '%s' "$name" | json_escape)" \
      "$(printf '%s' "$group" | json_escape)" \
      "$(printf '%s' "$case_status" | json_escape)" \
      "$(printf '%s' "$detail" | json_escape)"
  done < "$results_file"
  printf '\n  ]\n'
  printf '}\n'
} > "$json"

cp "$log" "$latest_log"
cp "$json" "$latest_json"
cp "$log" "$export_log" 2>/dev/null || true
cp "$json" "$export_json" 2>/dev/null || true
cp "$log" "$export_latest_log" 2>/dev/null || true
cp "$json" "$export_latest_json" 2>/dev/null || true

rm -f "$results_file"
log_line "Skydnir test suite $status passes=$passes failures=$failures"
log_line "json: $json"
log_line "documents json: $export_latest_json"

if [ "$failures" -ne 0 ]; then
  exit 1
fi

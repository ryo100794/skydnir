#!/bin/sh
set -eu

say() {
  printf 'pdocker-container-probe: %s\n' "$*"
}

fail() {
  printf 'pdocker-container-probe: FAIL: %s\n' "$*" >&2
  exit 1
}

need_file() {
  [ -e "$1" ] || fail "missing $1"
}

test_shell_basics() {
  say "shell-basics start"
  [ "x" = "x" ]
  if [ -x /usr/bin/[ ]; then
    /usr/bin/[ "x" = "x" ]
  fi
  need_file /bin/sh
  need_file /etc/passwd
  pwd | grep '^/' >/dev/null || fail "pwd is not guest absolute"
  say "shell-basics ok"
}

test_argv_preservation() {
  say "argv-preservation start"
  probe=/tmp/pdocker-argv-dump
  out=/tmp/pdocker-argv-dump.out
  cat > "$probe" <<'SH'
#!/bin/sh
i=0
for arg in "$@"; do
  i=$((i + 1))
  printf 'arg%03d=%s\n' "$i" "$arg"
done
SH
  chmod 755 "$probe"
  long_obj='ggml/src/ggml-vulkan/CMakeFiles/ggml-vulkan.dir/flash_attn_mask_opt.comp.cpp.o'
  "$probe" alpha "$long_obj" 'bracket-[b]-argument' 'space separated value' > "$out"
  grep -F "arg001=alpha" "$out" >/dev/null || fail "argv arg001 was not preserved"
  grep -F "arg002=$long_obj" "$out" >/dev/null || fail "long object argv was truncated"
  grep -F "arg003=bracket-[b]-argument" "$out" >/dev/null || fail "bracket argv was rewritten"
  grep -F "arg004=space separated value" "$out" >/dev/null || fail "space argv was split"
  say "argv-preservation ok"
}

test_linker_argv_preservation() {
  say "linker-argv-preservation start"
  probe=/tmp/pdocker-linker-argv-dump
  out=/tmp/pdocker-linker-argv-dump.out
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
  grep -F "arg001=cmake" "$out" >/dev/null || fail "cmake argv was not preserved"
  grep -F "arg006=-DGGML_BLAS=ON" "$out" >/dev/null || fail "standard BLAS option was not preserved"
  grep -F "arg008=ggml/src/ggml-vulkan/CMakeFiles/ggml-vulkan.dir/flash_attn.comp.cpp.o" "$out" >/dev/null || fail "first flash_attn object was not preserved"
  grep -F "flash_attn_mask_opt.comp.cpp.o" "$out" >/dev/null || fail "flash_attn_mask_opt object was lost"
  grep -F "flash_attn_split_k_reduce.comp.cpp.o" "$out" >/dev/null || fail "flash_attn_split_k_reduce object was lost"
  grep -F "generated_flash_attn_95.comp.cpp.o" "$out" >/dev/null || fail "long linker argv tail was lost"
  if grep -Eq '^arg[0-9]+=flash$' "$out"; then
    fail "linker argv was truncated to flash"
  fi
  grep -F "argc=108" "$out" >/dev/null || fail "linker argv count changed"
  say "linker-argv-preservation ok"
}

test_large_allocation_guard() {
  say "large-allocation start"
  if ! command -v python3 >/dev/null 2>&1; then
    say "large-allocation skip: python3 missing"
    return 0
  fi
  mb="${PDOCKER_CONTAINER_PROBE_LARGE_MB:-128}"
  expect_guard="${PDOCKER_CONTAINER_PROBE_EXPECT_GUARD:-0}"
  python3 - "$mb" "$expect_guard" <<'PY'
import errno
import mmap
import sys

mb = int(sys.argv[1])
expect_guard = sys.argv[2] == "1"
size = mb * 1024 * 1024
try:
    mapping = mmap.mmap(-1, size)
except OSError as exc:
    print(f"large_allocation_errno={exc.errno} size={size}")
    if expect_guard and exc.errno == errno.ENOMEM:
        print("large_allocation_guard_ok")
        raise SystemExit(0)
    raise
else:
    mapping[0:1] = b"x"
    mapping.close()
    print(f"large_allocation_ok size={size}")
    if expect_guard:
        raise SystemExit("expected ENOMEM from memory guard")
PY
  say "large-allocation ok"
}

test_proc_view() {
  say "proc-view start"
  exe="$(readlink /proc/self/exe 2>/dev/null || true)"
  [ -n "$exe" ] || fail "/proc/self/exe was empty"
  case "$exe" in
    /bin/*|/usr/bin/*) ;;
    *) fail "/proc/self/exe leaked non-guest path: $exe" ;;
  esac
  say "proc-view ok exe=$exe"
}

test_shell_basics
test_argv_preservation
test_linker_argv_preservation
test_proc_view
test_large_allocation_guard
say "all ok"

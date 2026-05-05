# APK Memory Pager Probe

Snapshot date: 2026-05-05.

## Purpose

This file records the APK-scoped syscall probe for the planned pdocker memory
pager. The probe must run from the SDK28 compat APK process, not from root and
not from an unrelated `/data/local/tmp` binary.

## Probe Command

```sh
adb shell am start -n io.github.ryo100794.pdocker.compat/.MainActivity
adb shell 'run-as io.github.ryo100794.pdocker.compat sh -lc \
  "files/pdocker-runtime/docker-bin/pdocker-direct --pdocker-memory-pager-probe; rc=\$?; echo exact_rc=\$rc"'
```

## Latest Result

Device: SOG15, Android SDK 36, compat APK target SDK 28.

```text
pager-probe:mmap_prot_none=ok
pager-probe:mprotect_rw=ok
pager-probe:write_after_mprotect=ok
pager-probe:madvise_dontneed=ok
pager-probe:userfaultfd_syscall=fail errno=1
pager-probe:open_dev_userfaultfd=fail errno=13
pager-probe:ptrace_traceme_stop=ok
pager-probe:process_vm_writev_child=ok
pager-probe:ptrace_sigsegv_stop=ok
pager-probe:ptrace_getsiginfo=ok
pager-probe:userfaultfd=blocked
pager-probe:ptrace_path=ok
pager-probe:result=ok
exact_rc=0
```

## PoC Command

```sh
adb shell am start -n io.github.ryo100794.pdocker.compat/.MainActivity
adb shell 'run-as io.github.ryo100794.pdocker.compat sh -lc \
  "files/pdocker-runtime/docker-bin/pdocker-direct --pdocker-memory-pager-poc; rc=\$?; echo exact_rc=\$rc"'
```

## Latest PoC Result

```text
pager-poc:child_fault_page_reported=ok
pager-poc:initial_ptrace_stop=ok
pager-poc:fault_sigsegv_stop=ok
pager-poc:fault_siginfo=ok
pager-poc:get_fault_regs=ok
pager-poc:inject_mprotect_syscall=ok
pager-poc:write_backed_page=ok
pager-poc:restore_fault_regs=ok
pager-poc:resumed_fault_instruction=ok
pager-poc:result=ok
exact_rc=0
```

## Interpretation

The required syscall surface for the ptrace SIGSEGV pager prototype is present
inside the compat APK process. The next implementation step can use a reserved
`PROT_NONE` virtual range and ptrace signal stops as the primary missing-page
event path.

`userfaultfd` is not available on this device. That path must stay optional and
must not be required for the first managed pager implementation.

The PoC confirms more than signal visibility: the tracer can recover a
deliberate fault by making the same tracee page writable, writing data into it,
restoring the saved fault registers, and resuming the original instruction.

The latest PoC uses generic aarch64 syscall injection: temporarily patch the
faulting tracee instruction stream with `svc; brk`, run `mprotect` in the
tracee, restore the original instructions and registers, then resume the
faulting instruction. That removes the earlier cooperative-trampoline
limitation.

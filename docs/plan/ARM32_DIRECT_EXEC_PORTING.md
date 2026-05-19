# ARM32 Direct Executor Porting Plan

Snapshot date: 2026-05-19.

## Purpose

`armeabi-v7a` Android/Bionic helper binaries are now packaged, but
`pdocker-direct` for 32-bit ARM is intentionally an unsupported-ABI executable.
This document records what must change before that stub can be replaced by a
real process-exec backend.

## Current Status

- `arm64-v8a` uses the current AArch64 direct executor.
- `armeabi-v7a` packages `libpdockerdirect.so`, but the binary exits with a
  clear capability error.
- The unsupported status is deliberate. The current executor is built around
  AArch64 ptrace register layout, syscall numbers, loader paths, and pointer
  width assumptions.

## AArch64-Specific Areas

| Area | Current AArch64 assumption | ARM32 work required |
|---|---|---|
| Register ABI | `struct user_pt_regs`, args in `regs[0..5]`, syscall number in `regs[8]`, return in `regs[0]`, `sp`, `pc` | Add a register abstraction for ARM EABI: syscall number in `r7`, args in `r0-r6`, return in `r0`, `sp`, `pc`, CPSR/T-bit |
| Syscall table | AArch64/generic syscall numbers such as `openat=56`, `execve=221`, `mmap=222` | Add an ARM EABI syscall table and rewrite matrix, including `mmap2` semantics |
| Seccomp arch | `AUDIT_ARCH_AARCH64` | Use `AUDIT_ARCH_ARM` and ARM syscall numbers |
| Injected syscall | A64 `svc #0` / `brk #0`, 4-byte instruction advance | Implement ARM/Thumb-aware SVC/BKPT injection and PC advance |
| Pointer layout | `unsigned long long` argv/envp pointers, 16-byte scratch alignment | Use pointer-size-aware argv/envp rewriting and EABI stack alignment |
| ELF parser | `Elf64_Ehdr`, `Elf64_Phdr`, `ELFCLASS64` | Add dual `Elf32` / `Elf64` interpreter detection |
| Loader paths | `ld-linux-aarch64.so.1`, `lib/aarch64-linux-gnu` | Add `ld-linux-armhf.so.3` / `ld-linux.so.3`, `lib/arm-linux-gnueabihf`, and matching helper names |
| Memory pager | AArch64 `mmap`, `mprotect`, `munmap`, `brk` numbers and 64-bit address space | Handle `mmap2`, 32-bit address limits, 32-bit fault addresses, and tighter managed-pager limits |
| Container platform | backend defaults favor `linux/arm64` / `aarch64` | Add validated `linux/arm/v7` selection and rootfs bootstrap paths |

## Porting Task List

1. Add a `pdocker_trace_arch` abstraction for syscall number, args, return
   value, stack pointer, program counter, and trap advance.
2. Split syscall numbers and names into AArch64 and ARM EABI tables.
3. Make seccomp filter generation architecture-specific.
4. Implement ARM/Thumb syscall injection and trap detection.
5. Make exec argument rewriting pointer-size-aware.
6. Add `Elf32` interpreter parsing.
7. Add ARM32 glibc loader and library-path candidates.
8. Add backend platform selection and manifest handling for `linux/arm/v7`.
9. Replace the `armeabi-v7a` unsupported stub only after device evidence proves
   all required ptrace/syscall paths.
10. Update tests that currently require the unsupported status.

## Minimum Promotion Tests

Before promoting ARM32 process execution:

- Build/package both `arm64-v8a` and `armeabi-v7a` helpers.
- Run an ARM32 direct probe on-device.
- Execute `/bin/true` and `/bin/sh -c 'echo ok'` in an ARM32 rootfs.
- Cover path syscalls: `openat`, stat/newfstatat equivalent, `readlinkat`,
  `faccessat`, `mkdirat`, `renameat`, and `unlinkat`.
- Cover `execve` for dynamic ELF, static ELF, shebang scripts, and long argv.
- Cover uid/gid emulation.
- Cover `mmap2`, `mprotect`, `munmap`, `brk`, memory guard, and managed-pager
  PoCs.
- Cover fork/clone/exec lifecycle.
- Run a backend scenario equivalent to:

```sh
docker run --platform linux/arm/v7 busybox echo ok
```

Until these pass, `armeabi-v7a` packaging is ABI coverage evidence only, not a
claim that Docker-compatible process execution works on 32-bit ARM.


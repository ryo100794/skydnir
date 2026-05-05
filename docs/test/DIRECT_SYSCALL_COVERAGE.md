# Direct Runtime Syscall Coverage

This record inventories the syscall mediation surface in
`app/src/main/cpp/pdocker_direct_exec.c` and the related PTY/libcow paths. The
machine-readable source of truth is `tests/direct_syscall_coverage.json`; the
fast gate is `python3 scripts/verify_direct_syscall_contracts.py`.
`python3 scripts/run_direct_syscall_scenarios.py` turns the same manifest into a
repeatable execution plan.

## Fast Static Gate

The verifier mines the direct executor for:

- selective seccomp trace and errno hooks;
- path rewrite switch entries;
- userland emulation hooks for getcwd, access, link/symlink, rootfs-fd close,
  io_uring fallback, credential identity, and NUMA fallback;
- PTY exec/wait markers in `app/src/main/cpp/pty.c`;
- pid identity/reconcile tests in `docker-proot-setup/scripts/verify_runtime_contract.py`;
- libcow copy-up path operation hooks and the local `test_cow.sh` contract.

It fails when an active named syscall hook is missing a coverage entry, when a
heavy case reference is stale, or when one of the required coverage areas drops
out of the manifest.

The fast gate also lists runnable local scenarios:

```bash
python3 scripts/run_direct_syscall_scenarios.py --tier fast-local --list
```

For an isolated syscall-coverage lane that does not require ADB, an installed
APK, or a local native rebuild, run:

```bash
python3 scripts/run_direct_syscall_scenarios.py --lane local
```

That lane checks the static direct-executor hook inventory, runs the focused
`tests/direct_syscall/` manifest-contract tests, lists fast-local scenarios,
and dry-runs the Android-heavy plan. It is the default acceptance command for
coverage-only changes because device-only scenarios remain explicit without
being launched.

Filter by execution status when you want only concrete commands or only
remaining plans:

```bash
python3 scripts/run_direct_syscall_scenarios.py --status runnable --list --verbose
python3 scripts/run_direct_syscall_scenarios.py --status planned --json
```

Preview the commands that would run without launching local build steps or adb:

```bash
python3 scripts/run_direct_syscall_scenarios.py --status runnable --execute --dry-run
```

Run the currently executable local scenarios with:

```bash
python3 scripts/run_direct_syscall_scenarios.py --tier fast-local --execute
```

## Active Hook Inventory

Current active syscall groups:

- File/path and metadata: `openat`, `openat2`, `newfstatat`, `statx`,
  `statfs`, `faccessat`, `faccessat2`, `getcwd`, `chdir`, `readlinkat`,
  `mknodat`, `mkdirat`, `unlinkat`, `renameat`, `renameat2`, `utimensat`,
  `fchmodat`, `fchownat`, `fchown`, `name_to_handle_at`, xattr calls, and
  protected `close(rootfs_fd)`.
- Exec: `execve` and `execveat`, including loader argv rebuild, shebang
  handling, rootfs/PATH resolution, and guest `/proc/self/exe` reporting.
- Sockets: `bind(AF_UNIX)` and `connect(AF_UNIX)` path rewrite for guest
  absolute socket paths.
- Compatibility fallbacks: `set_robust_list`, `rseq`, `clone3`,
  `close_range`, `io_uring_*`, NUMA policy calls, and container-root credential
  identity calls.
- Process tracking: fork/vfork/clone tracee state inheritance, wait/exit/signal
  raw status handling, and pid reuse reconciliation in the daemon tests.
- CoW path operations: libcow open/openat/creat/truncate/fopen, metadata,
  xattr, fd lifecycle, and local hardlink copy-up tests.

## Heavy Cases

The manifest defines Android/device cases without running them in the fast
gate. They cover direct rootfs open/stat/access/getcwd, cwd ERANGE, exec argv
and rootfs resolution, AF_UNIX bind/connect rewrite, NUMA ENOSYS,
credential/pid state, PTY exec/wait status, and copy-on-write path operations.

Do not run these from `scripts/verify-fast.sh`; they require an Android device,
an installed compat/direct runtime, or a locally built libcow.
Runnable Android-heavy entries use `PDOCKER_PACKAGE` and `ROOTFS` so the same
manifest command can target the installed package and app-visible rootfs:

```bash
PDOCKER_PACKAGE=io.github.ryo100794.pdocker.compat \
ROOTFS=files/pdocker/containers/<id>/rootfs \
python3 scripts/run_direct_syscall_scenarios.py --tier heavy-android --status runnable --execute --dry-run
```

Android-heavy entries stay marked as planned until the manifest has a concrete
`adb shell run-as ...` invocation and explicit `"runnable": true`. The AF_UNIX
socket case still needs host-side socket setup to exercise both active
bind/connect rewrite hooks, and the PTY case still needs the APK/JNI terminal
path, so they remain plans rather than self-contained device commands. Use
`--list --tier heavy-android --verbose` or `--tier heavy-android --json` to
inspect the current Android execution plan.

## Acceptance

Coverage changes are accepted when the local lane passes:

```bash
python3 scripts/run_direct_syscall_scenarios.py --lane local
```

Before promoting a device scenario from planned to runnable, make sure its
manifest entry has a concrete `adb shell run-as ...` command, names `ROOTFS`,
sets the expected direct-runtime trace flag where useful, and documents the
observable checks. Do not re-add AF_UNIX bind rewrite to `known_gaps`; the
direct executor has active `bind` and `connect` rewrite hooks. The remaining
socket scenario gap is executable device coverage for a host-side socket setup,
not hook availability.

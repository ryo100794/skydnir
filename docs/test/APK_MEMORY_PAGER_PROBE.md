# APK Memory Pager Probe

Snapshot date: 2026-05-05.

## Purpose

This file records the APK-scoped syscall probe for the planned pdocker memory
pager. The probe must run from the SDK28 compat APK process, not from root and
not from an unrelated `/data/local/tmp` binary.

## Probe Command

```sh
ACTIVITY="$(adb shell cmd package resolve-activity --brief io.github.ryo100794.pdocker.compat | tail -1)"
adb shell am start -n "$ACTIVITY"
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
ACTIVITY="$(adb shell cmd package resolve-activity --brief io.github.ryo100794.pdocker.compat | tail -1)"
adb shell am start -n "$ACTIVITY"
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

## Managed Anonymous Pager PoC Command

This is the first cooperative paging slice. It does not rely on Android kernel
swap. It reserves an APK-owned `PROT_NONE` range, backs pages from a sparse
file, keeps only a small resident window, evicts dirty pages with `pwrite`, and
restores them with `pread` plus `mprotect`.

```sh
ACTIVITY="$(adb shell cmd package resolve-activity --brief io.github.ryo100794.pdocker.compat | tail -1)"
adb shell am start -n "$ACTIVITY"
adb shell 'run-as io.github.ryo100794.pdocker.compat sh -lc \
  "files/pdocker-runtime/docker-bin/pdocker-direct --pdocker-memory-pager-managed-poc; rc=\$?; echo exact_rc=\$rc"'
```

## Latest Managed Anonymous Pager Result

Pending on the next connected device after installing an APK built from the
commit that contains `--pdocker-memory-pager-managed-poc`.

The result must include replayable counters so later low-memory tuning can
separate correctness from overhead:

- `resident_pages`
- `max_resident_pages`
- `page_ins`
- `page_outs`
- `dirty_page_outs`
- `bytes_in`
- `bytes_out`
- `elapsed_ns`

## Transparent Managed Pager PoC Command

This command validates the production-style ptrace path: a child process makes
a large anonymous private `mmap`, the direct executor rewrites it to
`PROT_NONE`, then SIGSEGV-driven page-in/page-out preserves data while the child
continues without application changes.

```sh
ACTIVITY="$(adb shell cmd package resolve-activity --brief io.github.ryo100794.pdocker.compat | tail -1)"
adb shell am start -n "$ACTIVITY"
adb shell 'run-as io.github.ryo100794.pdocker.compat sh -lc \
  "files/pdocker-runtime/docker-bin/pdocker-direct --pdocker-memory-pager-transparent-poc; rc=\$?; echo exact_rc=\$rc"'
```

## Latest Transparent Managed Pager Result

Pending on the next connected device after installing an APK built from the
commit that contains `--pdocker-memory-pager-transparent-poc`.

## Planned OOM/LMK Diagnostics Artifact Probe

Planned gap: no current device probe writes the complete runtime
OOM/LMK-diagnostics artifact yet.  The executable/static verifier must keep this
gap visible until a connected-device test can run a large-allocation scenario,
kill/restart reconciliation, and artifact-retention check from inside the APK.

The future probe should run without ADB root, without an external debugger, and
without force-stopping the app during the sample window.  It should create a
bounded artifact under app-private operation/container state with this minimum
shape:

- `schema`: `pdocker.memory-oom-lmk-diagnostics.v1`
- `artifact_created_at_epoch`, `artifact_retention_policy`, and total retained
  artifact bytes
- current and last-known `/proc/meminfo` fields: `MemAvailable`, `SwapFree`,
  `SwapTotal`, and zram fields when visible
- per-process `rss_bytes` and `pss_bytes`, or `pss_unavailable` with errno when
  `smaps_rollup` cannot be read
- `last_large_allocation` with syscall, requested bytes, result/errno,
  threshold, MemAvailable, SwapFree, and managed-region id when applicable
- `last_known_progress` with phase, sequence number, step label, and completed
  bytes/items when available
- pager counters: reserved bytes, resident bytes, backing bytes, page-ins,
  page-outs, dirty page-outs, and page-in latency summary
- `lmk_suspected_classifier` with one of `lmk_suspected`,
  `not_lmk_suspected`, or `unknown`, plus explicit evidence/reason fields
- stale-running guard evidence: current engine snapshot status, pid-liveness
  result, container metadata state, and whether UI is allowed to show a live running state

Acceptance criteria for the future probe:

1. A synthetic large allocation records requested bytes and the
   MemAvailable/SwapFree values used for the decision.
2. A process sample records RSS and either PSS or `pss_unavailable`.
3. A forced stale-active replay cannot make UI truth report `running` unless a
   fresh engine snapshot and live pid agree.
4. A suspected background daemon/process loss records last known progress and a
   classifier reason rather than leaving only a stale `Up`/running state.
5. Artifact retention keeps the latest summary after pager backing-file cleanup
   and enforces a bounded byte cap.

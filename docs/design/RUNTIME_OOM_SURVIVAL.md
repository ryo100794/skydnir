# Runtime OOM Survival Strategy

Snapshot date: 2026-05-05.

## Purpose

This document defines how pdocker should behave when a running build,
container, or llama/GPU workload approaches Android low-memory conditions.
The goal is not to hide every out-of-memory condition. The goal is to turn a
hard app/process death into one of three controlled outcomes:

- the allocation is rejected early with `ENOMEM` and structured diagnostics;
- the workload is kept alive through an explicit managed-memory feature;
- the process is killed by Android, but pdocker can classify and explain it
  from persisted evidence after restart.

## Android Reality

Android LMK and process OOM kills provide no reliable, app-visible
"about to die" callback. Once the system selects a process to kill, there may be
no useful grace window. Therefore pdocker cannot depend on last-second cleanup.

The durable strategy must be proactive:

- sample memory while the workload is still healthy;
- intercept large allocation requests before they cross the danger zone;
- persist operation state outside the foreground UI;
- reconcile process/container state after restart.

This still leaves the important product requirement: pdocker should not only
fail safely. It also needs a mode that can run workloads larger than comfortable
RAM. That mode is intentionally separate from the default guard path.

## Existing Building Blocks

The direct executor already has the first enforcement layer:

- `PDOCKER_DIRECT_TRACE_MEMORY=1` records `mmap`, `mremap`, `brk`, `munmap`,
  `mprotect`, and `madvise` summaries.
- `PDOCKER_DIRECT_MEMORY_GUARD=1` can deny large `mmap`, `mremap`, or `brk`
  growth before the kernel accepts the request.
- The build path sets Android low-memory defaults through pdockerd without
  requiring Dockerfile edits.
- The SDK28 compat APK has proven the opt-in memory-pager primitives:
  `mmap(PROT_NONE)`, `mprotect`, `madvise`, `process_vm_writev`, ptrace
  `SIGSEGV` stops, `PTRACE_GETSIGINFO`, and generic aarch64 syscall injection.

## Runtime Policy

### 1. Memory Telemetry Ring

For every daemon operation and running container, pdockerd should keep a small
append-only ring file under `files/pdocker/operations/<operation-id>/` or the
container state directory. Each sample should include:

- timestamp and monotonic elapsed time;
- `/proc/meminfo` `MemAvailable`, `MemFree`, `SwapFree`, and zram hints when
  visible;
- tracee pid, process group, and direct-executor pid;
- per-process RSS from `/proc/<pid>/status` or `statm`;
- last large allocation request and largest successful allocation;
- current operation phase such as pull, build `RUN`, snapshot, compose start,
  llama model load, or GPU dispatch.

The UI can render the newest sample, but the file is owned by pdockerd so an app
activity restart does not erase the evidence.

### 2. Early Allocation Denial

The memory guard remains the default survival mechanism for unmodified Linux
programs. For a large request, pdocker should deny before Android LMK has to
kill the app process:

- `mmap` and `mremap` return `-ENOMEM`;
- `brk` returns the previous break;
- stderr receives a `pdocker-direct-memory: deny ...` line;
- pdockerd records a structured event with requested bytes, available bytes,
  swap-free bytes, thresholds, pid, command, image, container ID, and phase.

This preserves normal program semantics better than killing the whole app.
Programs that can recover from `ENOMEM` may continue; programs that cannot will
exit in a diagnosable way.

### 3. Managed Pager For Opt-In Large Buffers

`PDOCKER_MEMORY_PAGER=managed` and `io.pdocker.memory-pager=managed` are the
explicit opt-ins for pdocker-owned anonymous regions. Standard Docker/Compose
memory keys such as `mem_limit`, `memswap_limit`, and
`deploy.resources.limits.memory` must remain metadata/budget inputs; they do
not silently enable the pdocker-specific pager.

The pager is appropriate for selected large anonymous buffers. It is not a
general allocator replacement. It must not page executable text, stacks, libc
internal mappings, GPU command rings, or mappings that pdocker did not reserve.

### 3b. Large Workload Mode

Large Workload Mode is the "make it run even when it is too big" path. It is
not the same as the default memory guard.

Enablement should be explicit:

- Compose label: `io.pdocker.large-workload=enabled`.
- Optional env: `PDOCKER_LARGE_WORKLOAD=1`.
- Optional policy: `PDOCKER_LARGE_WORKLOAD_POLICY=prefer-file-backed`,
  `managed-anonymous`, or `gpu-bridge-guarded`.

The mode combines three strategies:

1. File-backed first: keep models, datasets, build artifacts, and caches as
   files or sparse files and use mmap/streaming instead of eager copies.
2. Managed anonymous fallback: for pdocker-owned large anonymous buffers,
   reserve virtual address ranges and back pages with app-private sparse files.
3. Bridge-aware GPU memory: for Vulkan/OpenCL staging and model offload, use
   chunked registered buffers plus dirty-span synchronization rather than
   copying a whole model or whole tensor buffer on each command.

The user-facing tradeoff is honest: this can be slower than fitting in RAM, but
it should avoid the catastrophic failure mode where Android kills the app or the
whole daemon disappears.

Large Workload Mode must expose:

- current resident bytes;
- backing-file bytes;
- page-in/page-out counts;
- guard-denial counts;
- storage remaining;
- whether the current process is using file-backed, managed-anonymous, or GPU
  bridge guarded memory.

### 4. File-Backed And Streaming First

If data is naturally a file, use a file:

- GGUF model weights should stay file-backed and mmap-friendly.
- Large artifacts should be streamed or chunked instead of copied whole.
- GPU bridge uploads should move toward registered buffers and dirty-span
  updates rather than whole-buffer duplication.

This avoids doubling memory for model load and makes the kernel page cache do
the work it is already good at.

### 5. Kill Classification After Restart

When Android still kills a process, pdocker should classify rather than guess:

- if the tracer sees `SIGKILL`, record exit code `137` plus the last memory
  ring sample;
- if the app/daemon restarts and finds an active operation without a live pid,
  mark it `interrupted-or-lmk-suspected` and attach the last sample;
- if logcat or tombstone access is available, attach a bounded excerpt;
- never leave a UI card saying `Up` when Engine state, pid liveness, and
  container metadata disagree.

The key is to keep enough pre-kill samples that a post-kill diagnosis is still
useful.

## Scope By Workload

| Workload | Default Action | Future Action |
| --- | --- | --- |
| Dockerfile `RUN` build tools | Memory guard, low parallelism defaults, structured ENOMEM event. | Optional per-step memory profile and adaptive parallelism. |
| `docker run` / Compose services | Memory guard plus operation ring. | Per-container managed pager opt-in. |
| llama.cpp CPU | Keep GGUF mmap-backed, avoid `--no-mmap` by default, record model-load phase. | Optional managed pager for non-GGUF anonymous buffers only. |
| llama.cpp Vulkan/OpenCL bridge | Avoid whole model copies; use bridge-safe chunks and dirty spans. | GPU bridge virtual memory contract independent from the general pager. |
| UI / pdockerd | Keep heavy work off the UI thread and persist operations in pdockerd. | Separate service/process survival and restart reconciliation. |

## Implementation Plan

1. Add an operation memory ring writer in pdockerd and direct executor.
2. Emit structured abnormal events for memory-guard denial and suspected LMK.
3. Surface the latest memory sample in the operation card and debug pane.
4. Add a synthetic container test that forces a guarded allocation and verifies
   `ENOMEM`, event shape, and UI/state reconciliation.
5. Add a controlled LMK-suspected replay test that starts an operation, removes
   the pid, restarts the app/daemon, and verifies classification.
6. Add Large Workload Mode metadata and UI surfaces without changing ordinary
   Docker/Compose semantics.
7. Prototype `libpdocker-mempager.so` only after the above diagnostics are
   stable.
8. Connect the GPU bridge guarded-memory path to llama.cpp Vulkan/OpenCL
   workloads without modifying llama.cpp itself.

## Non-Goals

- No attempt to change system zram, swappiness, or global swap from the APK.
- No attempt to catch arbitrary kernel page faults outside pdocker-managed
  regions.
- No silent opt-in based solely on Docker-compatible memory keys.
- No fake success when Android kills the process.

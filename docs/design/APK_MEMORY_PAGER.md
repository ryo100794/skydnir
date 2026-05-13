# APK-Scoped Memory Pager

Snapshot date: 2026-05-05.

## Purpose

This document evaluates a pdocker extension that behaves like swap inside the
APK boundary. It is not Docker compatibility. It is an Android survival layer
for memory-heavy containers such as llama.cpp when the device does not allow
ADB/root swap tuning.

Operational low-memory policy, Android LMK/down classification, and the
user-facing Large Workload Mode are tracked in
[`RUNTIME_OOM_SURVIVAL.md`](RUNTIME_OOM_SURVIVAL.md). This page stays focused on
the pager mechanism itself.

## Device Constraints Observed

On the SOG15 test device:

- `adb root` is not available on the production build.
- `swapon` exists, but a shell-created swapfile fails with `Operation not
  permitted`.
- `/proc/sys/vm/swappiness`, `/proc/sys/vm/page-cluster`, and `/proc/swaps`
  are not readable/writable from the shell user.
- zram is already present and heavily used.
- Kernel config exposes `CONFIG_USERFAULTFD=y`, but `/dev/userfaultfd` is
  `0600 root:root`, so it cannot be assumed usable by the APK.

Therefore the product cannot rely on adding system swap, changing zram size, or
changing VM policy. Any swap-like behavior must be scoped to processes launched
and mediated by pdocker.

The SDK28 compat APK now carries repeatable native checks:
`pdocker-direct --pdocker-memory-pager-probe` and
`pdocker-direct --pdocker-memory-pager-poc`. On 2026-05-05 they confirmed that
the ptrace fallback primitives are visible from the APK process:
`mmap(PROT_NONE)`, `mprotect`, `madvise`, child ptrace stop,
`process_vm_writev`, intentional `SIGSEGV` stop, and `PTRACE_GETSIGINFO`.
`userfaultfd` remains blocked (`EPERM` from the syscall and `EACCES` for
`/dev/userfaultfd`), so it is a future optional path rather than the default.
The PoC also proves the core recovery loop: fault a reserved `PROT_NONE` page,
stop on `SIGSEGV`, make that same tracee page writable, write page bytes, restore
the fault registers, and resume the original instruction successfully.

## Important Boundary

Normal Linux page faults are handled by the kernel and are not delivered to user
space. An APK cannot observe every ordinary major/minor page fault from another
process.

pdocker can only catch page-fault-like events that it deliberately creates:

- `userfaultfd` faults on registered ranges, if a future device permits it.
- `SIGSEGV` faults on ranges pdocker marks `PROT_NONE` or write-protected.
- ptrace stops caused by those `SIGSEGV` deliveries before the signal reaches
  the tracee.

This means the memory pager must manage explicit regions. It is not a global
replacement for kernel swap.

## Source of the SIGSEGV Pager Idea

The SIGSEGV path is not a new kernel bypass and is not copied from an external
component. It combines three established operating-system techniques:

- Guard pages: runtimes mark memory inaccessible with `mprotect(PROT_NONE)` so
  a later access produces a deterministic fault.
- User-space paging: a cooperating pager owns selected virtual ranges and fills
  pages on demand.
- Debugger signal interception: a ptrace tracer sees a signal-delivery stop
  before the tracee receives `SIGSEGV`, can inspect `siginfo.si_addr`, and can
  resume the tracee with or without delivering the signal.

The pdocker-specific reason this became plausible is that pdocker-direct
already controls traced container processes for syscall mediation. If a fault
address belongs to a pdocker-managed range, the tracer can treat that stop as a
pager event. If it does not, the original `SIGSEGV` must be delivered normally.

## Candidate Designs

### A. File-Backed Memory First

For data that is naturally file-backed, prefer real files and `mmap`:

- GGUF model weights should remain file-backed and mmap-friendly.
- Build caches, layer indexes, and large immutable artifacts should be kept on
  disk and mapped on demand.
- Use application-level chunking and streaming before inventing fault handling.

This is the safest path. It lets the kernel reclaim clean pages without a
pdocker-specific pager.

### B. Managed Anonymous Memory Pager

For large anonymous buffers that currently pressure RAM, add an opt-in
pdocker-managed pager.

Container opt-in:

- Compose label or env: `PDOCKER_MEMORY_PAGER=managed`.
- Optional limit: `PDOCKER_MEMORY_PAGER_MAX_BYTES`.
- Docker-compatible Compose memory keys such as `mem_limit`,
  `memswap_limit`, and `deploy.resources.limits.memory` define the requested
  container budget in Engine metadata. They do not enable the pdocker pager by
  themselves; the pager remains an explicit pdocker opt-in so ordinary Compose
  files keep Docker-compatible meaning.
- Optional backing directory under app-private storage:
  `files/pdocker/memory/<container-id>/`.

Container injection:

- Add `libpdocker-mempager.so` through the same direct-loader preload mechanism
  already used for rootfs shims.
- The shim wraps large `mmap(MAP_ANONYMOUS)` allocations first.
- Later, selected allocator entry points may be wrapped, but stack, executable
  mappings, JIT mappings, GPU shared buffers, and small allocations stay out of
  scope.

Managed region lifecycle:

1. Reserve a virtual range with `mmap(PROT_NONE)`.
2. Create a sparse backing file per region or per container.
3. Register region metadata in a shared table visible to the direct executor.
4. On first access, fault the page intentionally.
5. Load the page from backing storage, make it accessible, and resume.
6. On memory pressure or aging, write dirty pages to backing storage and return
   them to `PROT_NONE`.

Implementation slice:

- `pdocker-direct --pdocker-memory-pager-managed-poc` now contains the first
  APK-owned managed anonymous pager loop.  It reserves a multi-page
  `PROT_NONE` virtual region, keeps only a fixed number of pages resident,
  writes dirty pages to a sparse backing file on eviction, restores pages on
  later access, and verifies that data survives multiple eviction rounds.
- The PoC emits replayable counters (`page_ins`, `page_outs`,
  `dirty_page_outs`, `bytes_in`, `bytes_out`, `max_resident_pages`, and
  `elapsed_ns`).  `scripts/android-memory-pager-managed-poc.sh` records those
  counters as `docs/test/apk-memory-pager-managed-latest.json` without
  force-stopping the app, so low-memory experiments can be compared across
  devices and builds.
- This is a cooperative pager API, not yet a transparent allocator shim.  It
  proves the core backing/eviction/reload mechanics without depending on
  `userfaultfd`, external code, or a global SIGSEGV handler.
- The direct executor now has the first opt-in transparent path for large
  anonymous mappings.  When `PDOCKER_MEMORY_PAGER=managed` or
  `PDOCKER_DIRECT_MEMORY_PAGER=managed` is set, suitable private anonymous
  `mmap` calls are mapped as `PROT_NONE`, registered in a per-tracee managed
  region table, and paged in by the ptrace SIGSEGV path.  Resident pages are
  bounded by `PDOCKER_DIRECT_MEMORY_PAGER_RESIDENT_PAGES`; evicted pages are
  copied to the backing file and protected back to `PROT_NONE`.
- This first transparent path intentionally covers large anonymous `mmap`
  regions only.  It does not rewrite `brk`, thread stacks, `MAP_SHARED`,
  device shared buffers, or file-backed model mappings.  That keeps the feature safe
  enough for opt-in large-workload experiments without changing normal Docker
  semantics by default.

### C. Preferred Fault Catch: userfaultfd

If a device allows unprivileged userfaultfd from the APK process:

1. Register managed ranges with `UFFDIO_REGISTER`.
2. Run a pager thread in the same process or a pdocker helper process.
3. Resolve missing pages with `UFFDIO_COPY`.
4. Track dirty pages with write-protect mode where available.

This is the cleanest architecture, but it is not the current default because
the observed production device exposes `/dev/userfaultfd` as root-only.

### D. Fallback Fault Catch: ptrace SIGSEGV Pager

pdocker-direct already owns the tracee lifecycle, so it can catch intentional
faults without kernel privileges:

1. The shim reserves managed pages as `PROT_NONE`.
2. The tracee touches a managed page and receives `SIGSEGV`.
3. Because pdocker-direct is tracing the process, the tracer sees a signal stop
   before delivery.
4. The tracer reads `siginfo.si_addr` with `PTRACE_GETSIGINFO`.
5. If the address belongs to a registered managed region:
   - suppress delivery of `SIGSEGV`;
   - inject an `mprotect(page, page_size, wanted_prot)` syscall into the tracee;
   - load page bytes from the backing file;
   - write them into the tracee with `process_vm_writev` or ptrace data writes;
   - resume the original instruction.
6. If the address is not managed, deliver the original `SIGSEGV`.

The key detail is that the virtual address must already belong to a reserved
managed VMA. The pager does not discover an arbitrary fault and map unrelated
memory into it. The shim first creates the whole managed window with
`mmap(PROT_NONE)`, records its start/end, and hands normal pointers from that
window to the program. When a fault arrives, the tracer page-aligns
`siginfo.si_addr`, validates that page against the managed table, changes that
same page to accessible permissions inside the tracee, copies the saved page
bytes into that same virtual address, and resumes. For eviction, the dirty page
is written back, discarded with `madvise(MADV_DONTNEED)` where available, and
returned to `PROT_NONE` so a later access faults again.

If `userfaultfd` is available, the same virtual-address ownership rule applies:
the managed range is registered first and `UFFDIO_COPY` fills the exact faulting
page. It is cleaner than ptrace because the kernel provides the missing-page
event and page fill API directly.

Dirty tracking can use a second intentional fault:

- Pages are restored read-only after load.
- The first write faults.
- The tracer marks the page dirty, upgrades it to writable, and resumes.

Eviction can be conservative:

- Only evict pages from managed regions.
- Never evict a page while a GPU/shared-memory command owns it.
- Use an approximate clock/LRU list maintained by the shim and tracer.
- Write dirty pages to backing storage before setting `PROT_NONE`.

This is slower than kernel swap. Its value is avoiding OOM for selected large
buffers, not improving normal performance.

## Safety Rules

- Do not page executable text, loader state, thread stacks, signal stacks, or
  libc internal mappings.
- Do not page GPU shared buffers, Vulkan/OpenCL mapped memory, or command ring
  memory unless a dedicated GPU memory contract exists.
- Do not page `MAP_SHARED` mappings by default.
- Keep the feature opt-in until correctness and performance are measured.
- Store backing files in app-private storage and delete them on container
  removal.
- Treat storage exhaustion as a hard failure with clear UI diagnostics.

## OOM/LMK Diagnostics Contract

The pager must also make low-memory failures diagnosable without ADB, root, or
an attached debugger.  The runtime OOM policy document owns the daemon-wide
survival strategy; this pager contract defines the memory-pager-specific
evidence that must be persisted before a process disappears.

Planned gap: the transparent pager PoC currently proves fault handling and
replayable counters, but it does not yet write the full daemon/container memory
diagnostic artifact described here.  Until that artifact exists, static checks
must keep this section and the probe runbook in sync so UI and tests do not
pretend that post-LMK diagnosis is already complete.

For every pager-enabled operation or Large Workload Mode run, pdockerd/direct
executor should retain a bounded JSONL ring plus a final summary under
app-private operation or container state.  Each sample should include:

- monotonic timestamp, wall-clock timestamp, operation id, container id, image,
  command, tracee pid/process group, direct-executor pid, and whether the app is
  foreground, background service, or recovered after restart;
- `/proc/meminfo` `MemAvailable`, `MemFree`, `SwapFree`, `SwapTotal`, zram
  fields when visible, and storage-free bytes for the pager backing directory;
- per-process RSS and, when `/proc/<pid>/smaps_rollup` is readable, PSS,
  Private_Dirty, Shared_Clean, and SwapPss; if PSS is blocked, record
  `pss_unavailable` with errno rather than dropping the sample;
- the last large allocation request (`mmap`, `mremap`, or `brk`) with requested
  bytes, result, errno, guard threshold, MemAvailable/SwapFree at decision time,
  region id, and whether it was denied, file-backed, or pager-managed;
- pager counters: reserved bytes, resident bytes, backing bytes, page-ins,
  page-outs, dirty page-outs, average/max page-in latency, and storage
  exhaustion state;
- last known progress: phase name, phase sequence number, human-readable step
  such as model load/accelerator dispatch/build RUN, last successful progress marker,
  and bytes/items completed when the workload exposes them.

The post-restart classifier should emit `lmk_suspected=true` only when current
engine truth and pid liveness are missing for a previously active operation and
the persisted evidence is consistent with low memory.  Strong signals include a
recent `SIGKILL`/exit 137 observed by the tracer, abrupt loss of the tracee or
daemon while `MemAvailable` or `SwapFree` was below the configured danger
threshold, or a stale active operation recovered after app/daemon restart with
no live pid.  Weak signals such as user stop, normal nonzero exit, explicit
guard `ENOMEM`, storage exhaustion, or an unrelated crash must classify as
`not_lmk_suspected` or `unknown` with the reason recorded.

Artifact retention must be bounded and user-safe:

- keep the latest summary for each active/recent operation and at least the last
  N ring samples needed to explain the final minute before failure;
- redact environment values and arguments known to carry secrets before writing
  artifacts;
- cap total memory-diagnostic artifact bytes per container/app and delete
  expired artifacts during normal container cleanup;
- keep pager backing files separate from diagnostic artifacts so cleanup can
  remove backing storage without erasing the failure explanation.

UI contract: a memory-diagnostic artifact is past evidence, not live `/proc`
state.  A UI card must not show `running`, `Up`, or an active spinner solely
because a persisted operation or pager artifact says it was running earlier.
It may show `interrupted-or-lmk-suspected` only after reconciling current engine
snapshot, pid liveness, and container metadata; this engine snapshot must be
fresh runtime truth, not only persisted container JSON.  If those disagree, the UI must
prefer stale-safe wording and attach the last known progress, MemAvailable,
SwapFree, RSS/PSS, and classifier reason.

## Interaction With llama.cpp

The current llama GPU experiments show that the bridge path can produce
diagnostics, but correctness and performance are not release-ready.  The memory
pager is not the first performance fix for that path.  The immediate GPU work
remains correctness-gated executor-marker evidence, persistent registered buffers,
and command-ring transport.  This pager is not expected to make token generation
faster by itself.

The memory pager is useful for:

- preventing OOM during large model/container workloads;
- experimenting with larger context or batch sizes;
- keeping CPU fallback alive when zram is saturated.

It is not expected to make token generation faster by itself.

Dockerfile build pressure is handled separately. General build tools such as
`cc1plus` allocate ordinary process heap and anonymous mappings that pdocker
does not own ahead of time, so the managed pager cannot safely reclaim those
regions transparently. Build-time memory control must stay outside the
managed-region pager contract unless a process explicitly opts in.

## GPU Bridge Virtual Memory Contract

llama.cpp reads GGUF model files with mmap by default. The pdocker llama
template does not pass `--no-mmap`, and the recorded `llama-bench` artifacts show
`use_mmap: true`. That model-file mapping should stay owned by llama.cpp and the
kernel. pdocker must not copy or page the whole 5 GB model just to make the GPU
bridge work.

The GPU-specific virtual memory work is a separate contract inside
`pdocker-vulkan-icd.so` and the APK-side GPU executor:

1. Keep model files file-backed and mmap-friendly.
2. Treat Vulkan `VkDeviceMemory` allocations in the ICD as managed bridge
   memory, not as anonymous container heap.
3. For large host-visible bridge allocations, reserve the memfd mapping as a
   guarded virtual range and install an in-process `SIGSEGV` handler owned by the
   ICD. This is cheaper and safer than ptracing every bridge memory fault because
   the ICD is already loaded in the faulting process.
4. On the first page access, `mprotect` only that page or span and mark it
   resident. On first write, mark the page dirty.
5. Extend the dispatch protocol after `VULKAN_DISPATCH_V2` with dirty-span
   metadata so the executor can update cached Vulkan buffers from only the pages
   that changed.
6. Pin all pages referenced by an in-flight GPU command until the executor
   returns. Eviction is allowed only after the command fence is complete.

This contract is not a general swap feature. It is a bridge transport
optimization and OOM guard for large Vulkan/OpenCL staging buffers. It should be
enabled independently from `PDOCKER_MEMORY_PAGER=managed`, for example with
`PDOCKER_GPU_VIRTUAL_MEMORY=guarded`, so ordinary container memory and GPU
transport memory can be tested and disabled separately.

The current llama GPU evidence makes this the next useful slice:

- The 8B GGUF is mmap-backed on the CPU side.
- The offloaded Vulkan model buffer is about 486.87 MiB.
- `--n-gpu-layers 3` serves with repeating transformer layers through the
  pdocker Vulkan ICD and APK-side Android Vulkan executor.
- Current best measured short run is 0.1436 tokens/s, 2.56x the CPU baseline,
  with llama.cpp unmodified.
- Trace evidence now shows `vkCmdCopyBuffer` replay is almost entirely
  alias-only: 565 of 566 copy submits avoided host-side `memmove`, leaving one
  16 KiB real copy in the captured run.
- The remaining bottleneck is generic dispatch transport: repeated
  upload/download of mutable activation buffers and per-dispatch
  container/APK synchronization.

Current implementation status:

- `pdocker-vulkan-icd.so` can reserve large bridge allocations as
  `memfd`-backed `mmap(PROT_NONE)` regions when
  `PDOCKER_GPU_VIRTUAL_MEMORY=guarded` is set.
- The ICD-owned `SIGSEGV` handler materializes faulted pages with `mprotect` and
  records resident/dirty page bitmaps for those guarded bridge allocations.
- Trace mode records guarded binding resident/dirty byte summaries into the
  llama GPU comparison artifact.
- Dirty tracking is currently conservative first-touch tracking. Once a page is
  made read/write, later writes to the same page do not generate another fault.
  This is enough to prove sparse residency and avoid eager physical memory
  commitment, but it is not yet sufficient for precise dirty-span upload.
- `VULKAN_DISPATCH_V2` still passes whole binding ranges. The planned V3 step is
  to pass page-span metadata so the executor can update cached Android Vulkan
  buffers from only changed guarded pages.

## Implementation Plan

The SDK28 compat probe gate has recorded the basic syscall availability needed
for the experimental pager PoC. The product may keep file-backed memory and
streaming improvements unconditionally, but the SIGSEGV/userfault-style pager
must remain opt-in until managed-region isolation, latency benchmarks, and
production guardrails are complete.

1. Add a probe command that records userfaultfd availability, zram/swap
   visibility, `swapon` permission, and SDK28 compat syscall behavior into a
   device artifact.
2. Add an APK-owned managed pager prototype with a synthetic managed region,
   sparse backing file, bounded resident set, eviction counter, and reload
   verification.  Current command:
   `pdocker-direct --pdocker-memory-pager-managed-poc`.
3. Add an Android direct-executor experiment for ptrace `SIGSEGV` interception:
   reserve one page as `PROT_NONE`, fault it, suppress the signal, map/write the
   page, and resume.
4. Move the experiment behind `PDOCKER_MEMORY_PAGER=managed`.
5. Add a synthetic memory-pressure benchmark:
   - working set size;
   - backed bytes;
   - page faults served;
   - evictions;
   - average fault latency;
   - OOM/LMK result.
6. Only after the synthetic benchmark is reliable, allow selected container
   templates to opt in.

## SDK28 Compat Probe Gate

The first Android implementation slice must prove these behaviors inside the
compat APK process, not from a root shell:

| Gate | Required result | If blocked |
|---|---|---|
| `mmap(PROT_NONE)` reserve | Creates a page-aligned managed VMA. | No SIGSEGV pager; file-backed mmap only. |
| `mprotect(PROT_READ|PROT_WRITE)` | Makes the exact faulting page accessible. | No SIGSEGV pager. |
| `madvise(MADV_DONTNEED)` | Releases resident backing for an evicted page, or fails with a recorded errno. | Keep pages accessible after writeback; treat as memory-saving partial failure. |
| child trace attach | Parent/tracer can trace a child launched by pdocker-direct. | No ptrace pager; userfaultfd-only future path. |
| `PTRACE_GETSIGINFO` on intentional `SIGSEGV` | Returns `si_addr` for the managed page before delivery. | No ptrace pager. |
| signal suppression and resume | The tracee resumes the original instruction after the tracer handles the page. | No ptrace pager. |
| tracee syscall injection or equivalent | The tracer can change permissions for the tracee page. | Fall back to a cooperative in-process shim handler only. |
| `process_vm_writev` or ptrace data writes | Page bytes can be copied into the tracee at the same virtual address. | Use slower ptrace word writes if allowed; otherwise no ptrace pager. |
| storage backing file | Sparse backing file works under app-private storage. | Limit to in-memory accounting/profiling only. |
| latency budget | Synthetic page-in latency is recorded before any llama/container opt-in. | Keep feature experimental and disabled by default. |

The probe artifact should record target SDK flavor, device SDK, SELinux mode,
errno values, and whether the process ran under `run-as` or normal APK launch.
The compat flavor currently uses target SDK 28, but the device may still run a
new Android release, so both values must be captured.

The first PoC now uses generic aarch64 syscall injection instead of a
cooperative trampoline: it temporarily patches the stopped tracee with
`svc; brk`, runs `mprotect` in the tracee, restores the original instructions
and fault registers, and resumes the faulting instruction. This removes the
main tracee-control feasibility wall; the remaining work is turning the PoC
into a managed-region implementation.

## Remaining Production Risks

The PoC removes the biggest feasibility doubt, but these issues can still block
or narrow the feature:

- General tracee control: generic aarch64 syscall injection works in the PoC.
  Production code still needs guardrails around instruction patching, BTI/PAC
  compatibility, signal races, and fallback behavior when the stopped PC is not
  safe to patch.
- Threads: another thread may touch or mutate the same managed page while the
  tracer is resolving a fault. The first production mode should be single-thread
  or stop-the-process-group until locking is designed.
- Signal semantics: real programs may install SIGSEGV handlers. pdocker must
  only suppress faults for registered managed ranges and must deliver all other
  SIGSEGV events unchanged.
- Allocator coverage: wrapping only large anonymous `mmap` calls will miss
  malloc arenas and custom allocators. Broader hooks increase risk and should be
  measured before enabling them.
- Page size and tagged pointers: devices may use 4 KiB or 16 KiB pages, and
  arm64 tagged pointers/MTE can affect address comparison. All fault addresses
  must be untagged and page-aligned before metadata lookup.
- Performance: each missing page currently costs ptrace stops, permission
  changes, backing-file I/O, and process memory writes. This is an OOM survival
  path first, not a speed path.
- Storage pressure: backing files live in app-private storage. The pager must
  expose used bytes, fail cleanly when storage is low, and delete backing files
  when containers are removed.
- Android lifecycle: the APK may be killed or restarted while backing files and
  tracees exist. Recovery needs a manifest, cleanup, and clear UI diagnostics.
- GPU/shared memory: Vulkan/OpenCL buffers and command rings must stay outside
  the pager until they have a dedicated ownership contract.
- Device policy variation: SOG15 SDK36 target-SDK28 compat works for the ptrace
  path, but other Android builds may differ. The probe must remain a startup
  gate before enabling the feature.

## Open Questions

- Whether ptrace syscall injection for `mprotect` is fast enough under real
  workloads.
- Whether `process_vm_writev` can write a newly mprotected tracee page reliably
  across Android SELinux policy on all target devices.
- Whether write-protect dirty tracking causes too many stops for llama.cpp
  compute buffers.
- Whether the pager should be per-process or per-container when a container has
  multiple processes.

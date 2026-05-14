#!/usr/bin/env python3
"""Static contract checks for the APK-scoped memory pager design."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs/design/APK_MEMORY_PAGER.md"
OOM_DOC = ROOT / "docs/design/RUNTIME_OOM_SURVIVAL.md"
PROBE_DOC = ROOT / "docs/test/APK_MEMORY_PAGER_PROBE.md"
DIRECT_EXEC = ROOT / "app/src/main/cpp/pdocker_direct_exec.c"
ANDROID_SMOKE = ROOT / "scripts/android-device-smoke.sh"


def fail(message: str) -> None:
    raise SystemExit(f"FAIL: {message}")


def require(name: str, condition: bool) -> None:
    if not condition:
        fail(name)
    print(f"ok: {name}")


def main() -> int:
    text = DOC.read_text()
    oom = OOM_DOC.read_text()
    probe = PROBE_DOC.read_text()
    direct = DIRECT_EXEC.read_text()
    smoke = ANDROID_SMOKE.read_text()
    flat = " ".join(text.split())
    oom_flat = " ".join(oom.split())
    probe_flat = " ".join(probe.split())
    require("records that system swap is unavailable to non-root apk", "swapon" in text and "Operation not permitted" in flat and "adb root" in text)
    require("states normal page faults are not globally catchable", "Normal Linux page faults" in flat and "not delivered to user space" in flat)
    require("documents sigsegv pager origin without external code", "Source of the SIGSEGV Pager Idea" in text and "not copied from an external component" in flat and "Guard pages" in text)
    require("defines userfaultfd path but not as default", "userfaultfd" in text and "root-only" in text and "not the current default" in text)
    require("defines ptrace sigsegv fallback", "ptrace SIGSEGV Pager" in text and "PTRACE_GETSIGINFO" in text and "suppress delivery of `SIGSEGV`" in text)
    require("explains how fault address becomes backed", "virtual address must already belong to a reserved managed VMA" in flat and "mmap(PROT_NONE)" in text and "same virtual address" in text)
    require("keeps managed pager opt-in", "PDOCKER_MEMORY_PAGER=managed" in text and "opt-in" in text)
    require("requires sdk28 compat probe gate before runtime feature", "SDK28 Compat Probe Gate" in text and "probe gate has recorded" in flat and "must remain opt-in" in flat and "target SDK 28" in text)
    require("probe gate covers android-blockable syscalls", "PTRACE_GETSIGINFO" in text and "process_vm_writev" in text and "mprotect(PROT_READ|PROT_WRITE)" in text)
    require("direct executor exposes apk memory pager probe", "--pdocker-memory-pager-probe" in direct and "pager-probe:ptrace_path" in direct)
    require("direct executor exposes apk memory pager poc", "--pdocker-memory-pager-poc" in direct and "resumed_fault_instruction" in direct)
    require("direct executor exposes managed anonymous pager poc",
            "--pdocker-memory-pager-managed-poc" in direct and
            "ManagedPagerPoc" in direct and
            "pager-managed-poc:result=%s" in direct and
            "--pdocker-memory-pager-transparent-poc" in direct and
            "pager-transparent-poc:result=%s" in direct and
            "resident_limit_pages" in direct and
            "pager-managed-poc:page_ins=%llu" in direct and
            "pager-managed-poc:page_outs=%llu" in direct and
            "pager-managed-poc:elapsed_ns=%llu" in direct)
    require("direct executor has opt-in transparent managed mmap path",
            "PDOCKER_DIRECT_MEMORY_PAGER" in direct and
            "managed_trace_is_candidate_mmap" in direct and
            "maybe_prepare_managed_mmap" in direct and
            "maybe_finish_managed_mmap" in direct and
            "handle_managed_memory_fault" in direct and
            "sig == SIGSEGV && g_managed_memory_pager" in direct)
    require("device smoke checks compat memory pager probe", "--pdocker-memory-pager-probe" in smoke and "pager-probe:ptrace_path=ok" in smoke)
    require("device smoke checks compat memory pager poc", "--pdocker-memory-pager-poc" in smoke and "pager-poc:result=ok" in smoke)
    require("device smoke checks managed anonymous pager poc",
            "--pdocker-memory-pager-managed-poc" in smoke and "pager-managed-poc:result=ok" in smoke)
    require("device smoke checks transparent managed pager poc",
            "--pdocker-memory-pager-transparent-poc" in smoke and "pager-transparent-poc:result=ok" in smoke)
    require("latest apk memory pager probe result is recorded", "pager-probe:ptrace_path=ok" in probe and "pager-probe:userfaultfd=blocked" in probe and "exact_rc=0" in probe)
    require("latest apk memory pager poc result is recorded", "pager-poc:resumed_fault_instruction=ok" in probe and "pager-poc:inject_mprotect_syscall=ok" in probe and "pager-poc:result=ok" in probe)
    require("poc records generic syscall injection", "generic aarch64 syscall injection" in text and "svc; brk" in text and "earlier cooperative-trampoline limitation" in probe_flat)
    require("production risk register is explicit", "Remaining Production Risks" in text and "Threads" in text and "Signal semantics" in text and "Device policy variation" in text)
    require("excludes unsafe mappings", "thread stacks" in text and "GPU shared buffers" in text and "MAP_SHARED" in text)
    require("keeps llama gpu performance priority separate", "persistent registered buffers" in text and "not expected to make token generation faster" in text)
    require("defines gpu bridge virtual memory as a separate contract", "GPU Bridge Virtual Memory Contract" in text and "PDOCKER_GPU_VIRTUAL_MEMORY=guarded" in text and "VULKAN_DISPATCH_V2" in text)
    require("gpu bridge contract keeps gguf mmap file-backed", "llama.cpp reads GGUF model files with mmap by default" in text and "must not copy or page the whole 5 GB model" in text)
    require("gpu bridge contract tracks dirty spans before v3 dispatch", "dirty-span metadata" in flat and "Pin all pages referenced by an in-flight GPU command" in text)
    require("runtime oom survival records android no-prekill reality", "Android LMK" in oom and "no reliable" in oom_flat and "last-second cleanup" in oom)
    require("runtime oom survival defines large workload mode", "Large Workload Mode" in oom and "io.pdocker.large-workload=enabled" in oom and "managed-anonymous" in oom)
    require("runtime oom survival keeps fail-safe and run-large paths separate", "default memory guard" in oom_flat and "not the same" in oom_flat and "make it run even when it is too big" in oom_flat)
    require("runtime oom survival requires persisted telemetry evidence", "Memory Telemetry Ring" in oom and "last large allocation request" in oom and "owned by pdockerd" in oom)
    require("pager diagnostics planned gap is explicit",
            "OOM/LMK Diagnostics Contract" in text and
            "Planned gap" in text and
            "pdocker.memory-oom-lmk-diagnostics.v1" in probe)
    require("pager diagnostics record allocation and system pressure",
            "last large allocation request" in text and
            "MemAvailable" in text and
            "SwapFree" in text and
            "last_large_allocation" in probe and
            "requested bytes" in probe)
    require("pager admission telemetry is implemented and documented",
            "pdocker.memory-pager.admission.v1" in text and
            "pdocker.memory-pager.admission.v1" in direct and
            "record_managed_pager_admission" in direct and
            "print_managed_pager_admission_stats" in direct and
            "rejected_below_threshold" in direct and
            "rejected_file_backed" in direct and
            "unsupported-protection" in direct and
            "backing_errno" in direct and
            "classification=allocation_denied_enomem" in direct and
            "__NR_munmap" in direct and
            "(unsigned long long)-ENOMEM" in direct)
    require("pager diagnostics record rss and pss",
            "per-process RSS" in text and
            "PSS" in text and
            "pss_unavailable" in text and
            "rss_bytes" in probe and
            "pss_bytes" in probe)
    require("pager diagnostics preserve progress and classify lmk",
            "last known progress" in text and
            "lmk_suspected=true" in text and
            "interrupted-or-lmk-suspected" in text and
            "lmk_suspected_classifier" in probe and
            "not_lmk_suspected" in probe)
    require("pager diagnostics define retention and stale-running ui guard",
            "Artifact retention must be bounded" in text and
            "must not show `running`, `Up`, or an active spinner solely" in flat and
            "engine snapshot" in text and
            "pid liveness" in text and
            "artifact_retention_policy" in probe and
            "UI is allowed to show a live running state" in probe)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

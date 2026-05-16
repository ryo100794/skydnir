import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAGER_DOC = ROOT / "docs" / "design" / "APK_MEMORY_PAGER.md"
OOM_DOC = ROOT / "docs" / "design" / "RUNTIME_OOM_SURVIVAL.md"
PROBE_DOC = ROOT / "docs" / "test" / "APK_MEMORY_PAGER_PROBE.md"
ABNORMAL_CASES = ROOT / "tests" / "abnormal_event_cases.json"
FEASIBILITY_SCRIPT = ROOT / "scripts" / "verify-memory-pager-contract.py"
TODO = ROOT / "docs" / "plan" / "TODO.md"


class ApkMemoryPagerImplementationContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pager_doc = PAGER_DOC.read_text()
        cls.oom_doc = OOM_DOC.read_text()
        cls.probe_doc = PROBE_DOC.read_text()
        cls.abnormal = json.loads(ABNORMAL_CASES.read_text())
        cls.abnormal_blob = json.dumps(cls.abnormal, sort_keys=True)

    def assertDocHasAll(self, text, tokens):
        missing = [token for token in tokens if token not in text]
        self.assertFalse(missing, "missing contract tokens: " + ", ".join(missing))

    def test_managed_region_table_contract_is_specific(self):
        self.assertDocHasAll(self.pager_doc, [
            "Managed region table",
            "region_id",
            "tracee_pid",
            "base",
            "length",
            "end",
            "page_size",
            "page_count",
            "backing_fd",
            "backing_path",
            "resident_limit_pages",
            "state",
            "dirty",
            "write_observed",
            "backing_valid",
            "Registered ranges must not overlap",
            "untagged",
            "No table match means the original signal is delivered unchanged",
            "munmap",
        ])

    def test_page_state_and_transition_contract_is_explicit(self):
        self.assertDocHasAll(self.pager_doc, [
            "Page state model",
            "clean",
            "dirty",
            "evicted",
            "resident",
            "resident_clean",
            "resident_dirty",
            "evicted -> resident_clean",
            "resident_clean -> resident_dirty",
            "resident_dirty -> evicted",
            "resident_clean -> evicted",
            "must not silently expose stale bytes",
        ])

    def test_fault_handling_path_preserves_signal_semantics(self):
        self.assertDocHasAll(self.pager_doc, [
            "Fault handling path",
            "ptrace SIGSEGV",
            "PTRACE_GETSIGINFO",
            "siginfo.si_addr",
            "Untag and page-align",
            "deliver the original `SIGSEGV` unchanged",
            "serialize the tracee threads",
            "mprotect the exact faulting page to read-only",
            "zero-fill",
            "mark dirty and upgrade the exact page to writable",
            "userfaultfd",
        ])
        self.assertDocHasAll(self.probe_doc, [
            "fault_handling_path",
            "ptrace_sigsegv",
            "handled and delivered counts",
            "A non-managed `SIGSEGV` is delivered",
        ])

    def test_syscall_constraints_and_large_allocation_opt_in_are_defined(self):
        tokens = [
            "mmap, mprotect, sigaction, and munmap constraints",
            "MAP_PRIVATE | MAP_ANONYMOUS",
            "PDOCKER_MEMORY_PAGER=managed",
            "PDOCKER_DIRECT_MEMORY_PAGER=managed",
            "Docker/Compose memory limit keys are budgets only",
            "PDOCKER_DIRECT_MEMORY_PAGER_MIN_REGION_BYTES",
            "PDOCKER_DIRECT_MEMORY_PAGER_MAX_REGION",
            "PROT_EXEC",
            "Application `SIGSEGV` handlers are allowed",
            "Partial unmap either splits the region",
            "Large allocation opt-in",
            "io.pdocker.large-workload=enabled",
            "Exclusions always win over opt-in",
        ]
        self.assertDocHasAll(self.pager_doc, tokens)
        self.assertDocHasAll(self.oom_doc, [
            "Implementation-ready contract summary",
            "mmap` management is limited to explicit opt-in large",
            "sigaction` handlers must keep normal semantics",
            "munmap` must flush/split/remove region metadata",
        ])
        self.assertDocHasAll(self.probe_doc, [
            "syscall_constraints",
            "large_allocation_opt_in",
            "mmap",
            "mprotect",
            "sigaction",
            "munmap",
        ])

    def test_dirty_precision_contract_prevents_false_precision_claims(self):
        self.assertDocHasAll(self.pager_doc, [
            "Dirty precision",
            "dirty_precision=write_fault_precise",
            "dirty_precision=conservative_page",
            "dirty_precision=region_conservative",
            "must never claim write-fault precision",
            "dirty_pages_observed",
            "dirty_pages_written",
        ])
        self.assertDocHasAll(self.probe_doc, [
            "dirty_precision",
            "write_fault_precise",
            "conservative_page",
            "region_conservative",
            "dirty_pages_observed",
            "dirty_pages_written",
        ])

    def test_lmk_enomem_classification_and_ui_telemetry_fields_are_stable(self):
        classification_tokens = [
            "allocation_denied_enomem",
            "pager_storage_exhausted",
            "pager_fault_unhandled",
            "lmk_suspected",
            "not_lmk_suspected",
            "unknown",
        ]
        telemetry_tokens = [
            "UI telemetry fields",
            "pdocker.memory-pager.telemetry.v1",
            "managed_region_count",
            "reserved_bytes",
            "resident_bytes",
            "backing_bytes",
            "storage_free_bytes",
            "page_ins",
            "page_outs",
            "dirty_page_outs",
            "fault_latency_avg_us",
            "fault_latency_max_us",
            "last_large_allocation",
            "memory_pressure",
            "classifier_reason",
            "ui_live_state_allowed=false",
        ]
        self.assertDocHasAll(self.pager_doc, ["LMK/ENOMEM classification"] + classification_tokens + telemetry_tokens)
        self.assertDocHasAll(self.probe_doc, ["classification", "ui_telemetry"] + classification_tokens + telemetry_tokens[1:-1] + ["ui_live_state_allowed"])

    def test_fail_closed_behavior_is_testable_and_tied_to_abnormal_events(self):
        self.assertDocHasAll(self.pager_doc, [
            "Fail-closed behavior",
            "Fail closed means correctness and diagnosability beat progress",
            "backing allocation",
            "page-in",
            "page-out",
            "permission change",
            "thread serialization",
            "Unsupported mapping shapes must pass through unmanaged or return `ENOMEM`",
            "must emit a structured abnormal event",
        ])
        self.assertDocHasAll(self.probe_doc, [
            "fail_closed",
            "Low storage or backing I/O failure classifies as `pager_storage_exhausted`",
            "Partial `munmap` either splits table rows or fails closed",
        ])
        for token in [
            "abnormal.runtime.memory-pager-storage-exhausted",
            "abnormal.runtime.memory-pager-unhandled-fault",
            "abnormal.runtime.memory-pager-allocation-denied-enomem",
            "pager_storage_exhausted",
            "pager_fault_unhandled",
            "allocation_denied_enomem",
        ]:
            self.assertIn(token, self.abnormal_blob)

    def test_virtual_memory_feasibility_gate_runs_as_non_promoting_static_check(self):
        result = subprocess.run(
            [sys.executable, str(FEASIBILITY_SCRIPT)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("non-promoting planned-gap feasibility gate", result.stdout)
        self.assertIn("mmap_fixed_mapping", result.stdout)
        self.assertIn("file_backed_spill", result.stdout)
        self.assertIn("unsupported_kernel_fallback", result.stdout)

    def test_virtual_memory_feasibility_gate_rejects_incomplete_promotion(self):
        artifact = {
            "schema": "pdocker.memory-pager.feasibility-gate.v1",
            "status": "pass",
            "success": True,
            "stable_checkpoint_eligible": True,
            "promotes_app_virtual_memory": True,
            "syscall_capability_evidence": {
                "mprotect": {"result": "ok"},
                "sigsegv_handler": {"supported": True},
            },
            "fallback": {"unsupported_android_kernel": {"safe": True}},
        }
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "incomplete-feasibility.json"
            path.write_text(json.dumps(artifact), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(FEASIBILITY_SCRIPT), "--validate-artifact", str(path)],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=20,
            )
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("mmap_fixed_mapping", result.stdout + result.stderr)
        self.assertIn("file_backed_spill", result.stdout + result.stderr)

    def test_virtual_memory_feasibility_gate_accepts_only_complete_promotion_fixture(self):
        artifact = {
            "schema": "pdocker.memory-pager.feasibility-gate.v1",
            "status": "pass",
            "success": True,
            "stable_checkpoint_eligible": True,
            "promotes_app_virtual_memory": True,
            "syscall_capability_evidence": {
                "mmap_fixed_mapping": {"result": "ok", "method": "MAP_FIXED_NOREPLACE"},
                "mprotect": {"result": "ok"},
                "sigsegv_handler": {"supported": True, "source": "ptrace SIGSEGV stop"},
                "file_backed_spill": {"result": "ok", "storage": "app-private"},
                "unsupported_kernel_fallback": {"result": "ok", "mode": "disabled-or-ENOMEM"},
            },
            "fallback": {"unsupported_android_kernel": {"safe": True}},
        }
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "complete-feasibility.json"
            path.write_text(json.dumps(artifact), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(FEASIBILITY_SCRIPT), "--validate-artifact", str(path)],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=20,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_todo_keeps_task_h_as_planned_gap_no_native_code(self):
        todo = TODO.read_text()
        self.assertDocHasAll(todo, [
            "Task H virtual memory feasibility gate",
            "planned-gap/non-promoting",
            "mmap fixed mapping",
            "mprotect on exact managed pages",
            "SIGSEGV handler or userfaultfd",
            "file-backed spill",
            "safe fallback on unsupported Android kernels",
            "No native pager code is promoted by this gate",
        ])


if __name__ == "__main__":
    unittest.main()

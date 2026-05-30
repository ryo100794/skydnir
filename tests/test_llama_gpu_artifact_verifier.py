import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERIFIER = ROOT / "scripts" / "verify-llama-gpu-artifact.py"


def runtime_marker():
    return {
        "summary": "pass",
        "expected_executor_marker": "gpu-executor-llama-q4k-callsite-20260520",
        "observed_executor_markers": ["gpu-executor-llama-q4k-callsite-20260520"],
        "expected_icd_marker": "vulkan-icd-feature-chain-marker-20260518",
        "observed_icd_markers": ["vulkan-icd-feature-chain-marker-20260518"],
        "executor_event_count": 1,
    }


def load_verifier_module():
    spec = importlib.util.spec_from_file_location("llama_gpu_artifact_verifier", VERIFIER)
    verifier = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(verifier)
    return verifier


def passing_config_propagation():
    verifier = load_verifier_module()
    return {
        "summary": "pass",
        "checks": [
            {
                "env": env_name,
                "executor_field": field_name,
                "expected": None,
                "observed_values": [],
                "status": "not-requested",
            }
            for env_name, field_name in verifier.LLAMA_GPU_CONFIG_PROPAGATION_ENV_FIELDS
        ],
    }


def gpu_correctness_report(correctness="pass", required_failures=0, passed=True, content="5"):
    return {
        "schema": "pdocker.llama.correctness.v1.compare",
        "endpoint": "http://127.0.0.1:28081",
        "mode": "vulkan-forced-ngl-1",
        "gpu_layers": 1,
        "model_path": "/models/model.gguf",
        "probes": [
            {
                "name": "addition",
                "prompt": "2+3=",
                "expected": ["5"],
                "required": True,
                "passed": passed,
                "content": content,
                "error": None,
                "duration_ms": 12.0,
                "status_code": 200,
            }
        ],
        "summary": {
            "correctness": correctness,
            "required_failures": required_failures,
            "optional_failures": 0,
            "benchmark_claim_allowed": required_failures == 0,
        },
    }


def speedup_sections(speedup=2.0, target_met=True, cpu_tps=0.1, gpu_tps=0.2):
    return {
        "comparison": {
            "speedup": speedup,
            "target_tokens_per_second": cpu_tps * 10.0,
            "target_met": target_met,
        },
        "bridge_overhead_phase": {
            "cpu_tokens_per_second": cpu_tps,
            "gpu_tokens_per_second": gpu_tps,
            "speedup": speedup,
            "target_speedup": 10.0,
            "target_tokens_per_second": cpu_tps * 10.0,
            "target_met": target_met,
        },
    }


def q6_verified_writeback(hash_value="0x1111111111111111"):
    return {
        "local_size_resolved": [32, 1, 1],
        "q6_writeback_verified_all": True,
        "q6_row_indexed_sample_indices": [257],
        "q6_row_indexed_writeback_verified": True,
        "q6_row_indexed_writeback_evidence": [
            {
                "index": 2,
                "binding": 2,
                "alias_rep": 2,
                "offset": 0,
                "size": 607744,
                "q6_row_indexed": True,
                "q6_sample_indices": [257],
                "f32_after_dispatch": [{"index": 257, "value": 1.25}],
                "f32_after_writeback": [{"index": 257, "value": 1.25}],
                "row_indexed_samples_match_oracle": True,
            }
        ],
        "q6_writable_bindings": [
            {
                "index": 2,
                "binding": 2,
                "alias_rep": 2,
                "offset": 0,
                "size": 607744,
                "readable": True,
                "writable": True,
                "gpu_after_dispatch_hash": hash_value,
                "fd_after_hash": hash_value,
                "writeback_verified": True,
                "writeback_mismatch": False,
                "offset_equals_memory_plus_api_offset": True,
                "gpu_offset_equals_memory_plus_api_offset": True,
                "descriptor_offset_equals_api_offset": True,
                "descriptor_range_matches_api_range": True,
            }
        ],
        "q6_writable_writeback_mismatches": [],
        "q6_writable_writeback_unknown": [],
    }


def q6_store_index_model_reflection():
    return {
        "q6_dispatch_groups": [1187, 1, 64],
        "q6_block_size": 32,
        "q6_num_rows": 2,
        "q6_num_cols": 1,
        "q6_store_index_model_valid": True,
        "q6_store_index_sampled_nonzero_j": True,
        "q6_store_index_sampled_nonzero_y": True,
        "q6_store_index_full_coverage": True,
        "q6_store_window_begin": 0,
        "q6_store_window_end": 151936,
    }


def q6_final_store_boundary(summary, final_value=0.5, expected=1.25, fd_after=0.5):
    return {
        "schema": "pdocker.q6k.final-store-boundary.v1",
        "summary": summary,
        "joined_sample_count": 1,
        "store_index_model_valid": True,
        "samples": [
            {
                "probe": 4,
                "candidate_id": 64,
                "output_index": 257,
                "expected_store_index": 257,
                "dst_index": 257,
                "final_store_value_f32": final_value,
                "expected": expected,
                "fd_after_writeback": fd_after,
                "final_store_matches_expected": abs(final_value - expected) < 1.0e-3,
                "writeback_matches_final_store": abs(fd_after - final_value) < 1.0e-3,
                "writeback_matches_expected": abs(fd_after - expected) < 1.0e-3,
                "trace_writeback_verified": True,
                "trace_writeback_mismatch": False,
                "trace_writeback_mismatch_fields": [],
                "sample_class": summary,
            }
        ],
    }


def q6_layout_sample_with_store_model(dst_index=0, expected=7.5, gpu_at_dst=3.2, **extra):
    sample = {
        "dst_index": dst_index,
        "expected_store_index": dst_index,
        "store_formula_valid": True,
        "store_j": 0,
        "store_workgroup": [dst_index // 2, 0, 0],
        "store_row_in_group": dst_index % 2,
        "store_row": dst_index,
        "expected": expected,
        "gpu_at_dst": gpu_at_dst,
    }
    sample.update(extra)
    return sample


def api_executor_reconciliation(
    summary="pass",
    api_hash="0xaaaaaaaaaaaaaaaa",
    executor_hash="0xaaaaaaaaaaaaaaaa",
    match_status="match",
    **extra,
):
    result = {
        "summary": summary,
        "proof_strength": "full",
        "hash_algorithm": "sha256",
        "dispatches": [
            {
                "api_canonical_hash": api_hash,
                "executor_canonical_hash": executor_hash,
                "match_status": match_status,
            }
        ],
    }
    result.update(extra)
    return result


def generic_spirv_cpu_oracle_event(status="match", spirv_hash="0xac41e8033a67af4a"):
    return {
        "kernel": "generic_spirv",
        "pipeline_key": {"spirv_hash": spirv_hash},
        "cpu_oracle": {
            "requested": True,
            "candidate": True,
            "executed": True,
            "status": status,
            "kernel_hint": "rope-yarn",
            "scope": "debug-only-spv-hash-gated",
        },
    }


def wrong_completion_payload(reconciliation=None):
    diagnostics = {
        "runtime_freshness": runtime_marker()
    }
    if reconciliation is not None:
        diagnostics["api_executor_reconciliation"] = reconciliation
    return {
        "schema": "pdocker.llama.gpu.compare.v1",
        "gpu": {
            "served": True,
            "service_readiness": {
                "schema": "pdocker.llama.service-readiness.v1",
                "summary": {"health": "pass", "models": "pass", "completion": "pass"},
                "health": {"ok": True, "status": "pass"},
                "models": {"ok": True, "status": "pass"},
                "completion": {
                    "ok": True,
                    "status": "pass",
                    "status_code": 200,
                    "duration_ms": 47771.432,
                    "prompt": "2+3=",
                    "expected": ["5"],
                    "content": " Marvel",
                    "content_excerpt": " Marvel",
                    "passed": False,
                },
            },
            "diagnostics": diagnostics,
        },
    }


class LlamaGpuArtifactVerifierTest(unittest.TestCase):
    def run_verifier(self, payload, *args):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "artifact.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            return subprocess.run(
                [str(VERIFIER), str(path), *args],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

    def assert_claims_blocked(self, report):
        self.assertFalse(report["correctness_claim_allowed"])
        self.assertFalse(report["benchmark_claim_allowed"])

    def test_memory_blocker_is_structured_and_optionally_allowed(self):
        payload = {
            "error": "insufficient_memory",
            "memory": {"mem_available_mb": 1036, "swap_free_mb": 5},
            "required": {"mem_free_mb": 512, "swap_free_mb": 1024},
            "device_actions": ["wait"],
        }
        blocked = self.run_verifier(payload)
        self.assertEqual(blocked.returncode, 20, blocked.stdout)
        report = json.loads(blocked.stdout)
        self.assertEqual(report["classification"], "insufficient_memory")
        self.assertFalse(report["benchmark_claim_allowed"])
        self.assertTrue(report["device_memory_blocked"])
        self.assertIn("wait", report["device_actions"])
        self.assertTrue(
            any("Skydnir-owned" in action and "stale llama" in action for action in report["device_actions"]),
            report["device_actions"],
        )
        self.assertTrue(
            any("run-as io.github.ryo100794.pdocker.compat" in command for command in report["diagnostic_commands"]),
            report["diagnostic_commands"],
        )
        self.assertEqual(report["memory_thresholds"]["swap_free_mb"]["state"], "below-threshold")
        self.assertEqual(report["memory_thresholds"]["swap_free_mb"]["observed_mb"], 5)
        self.assertFalse(report["memory_thresholds"]["swap_free_mb"]["ok"])
        self.assertTrue(
            any("/containers/skydnir-llama-cpp/stop" in command for command in report["cleanup_commands"]),
            report["cleanup_commands"],
        )
        self.assertFalse(
            any("am force-stop" in action for action in report["device_actions"]),
            report["device_actions"],
        )
        self.assertFalse(
            any("am force-stop" in command for command in report["cleanup_commands"]),
            report["cleanup_commands"],
        )
        allowed = self.run_verifier(payload, "--allow-memory-blocker")
        self.assertEqual(allowed.returncode, 0, allowed.stdout)

    def test_completion_disconnect_after_liveness_precedes_missing_executor_marker(self):
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "served": True,
                "service_readiness": {
                    "schema": "pdocker.llama.service-readiness.v1",
                    "summary": {"health": "pass", "models": "pass", "completion": "fail"},
                    "health": {"ok": True, "status": "pass"},
                    "models": {"ok": True, "status": "pass"},
                    "completion": {
                        "ok": False,
                        "status": "fail",
                        "error": "RemoteDisconnected: Remote end closed connection without response",
                        "timeout_sec": 180,
                    },
                    "post_completion_health": {
                        "ok": False,
                        "status": "fail",
                        "error": "ConnectionRefusedError: refused",
                    },
                },
            },
            "runtime_freshness": {
                "summary": "fail",
                "expected_executor_marker": "gpu-executor-llama-q4k-callsite-20260520",
                "observed_executor_markers": [],
                "expected_icd_marker": "vulkan-icd-feature-chain-marker-20260518",
                "observed_icd_markers": [],
            },
        }
        result = self.run_verifier(payload)
        self.assertEqual(result.returncode, 22, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "llama-completion-disconnected")
        self.assertEqual(report["responsibility_boundary"], "service-readiness")
        self.assertFalse(report["service_readiness"]["post_completion_health_ok"])

    def test_completion_wrong_output_requires_api_executor_reconciliation(self):
        payload = wrong_completion_payload()
        result = self.run_verifier(payload)
        self.assertEqual(result.returncode, 44, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "api-executor-reconciliation-missing")
        self.assertEqual(report["responsibility_boundary"], "api-executor-reconciliation")
        self.assertIn(
            "gpu.diagnostics.api_executor_reconciliation",
            report["api_executor_reconciliation"]["missing"],
        )
        self.assert_claims_blocked(report)

    def test_completion_wrong_output_with_passed_reconciliation_and_fresh_markers(self):
        payload = wrong_completion_payload(api_executor_reconciliation())
        result = self.run_verifier(payload)
        self.assertEqual(result.returncode, 22, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "llama-completion-wrong-output")
        self.assertEqual(report["responsibility_boundary"], "reconciled-gpu-correctness")
        self.assertEqual(report["service_readiness"]["completion_content_excerpt"], " Marvel")
        self.assertEqual(report["api_executor_reconciliation"]["summary"], "pass")
        self.assert_claims_blocked(report)

    def test_completion_wrong_output_with_q6_dispatch_but_missing_oracle_blocks_at_q6_evidence(self):
        payload = wrong_completion_payload(api_executor_reconciliation())
        payload["gpu"]["diagnostics"]["q6_workgroup_diagnostics"] = {
            "event_count": 0,
            "q6_dispatch_seen": True,
            "q6_dispatch_event_count": 1,
            "q6_dispatch_latest": {
                "spirv_hash": "0x1bf751845c5dce75",
                "dispatch": [1187, 1, 64],
                "source": "dispatch-lifecycle",
                "has_cpu_oracle": False,
            },
            "q6_oracle_capture_missing": True,
            "blocker_class": "q6-oracle-capture-missing",
            "diagnostic_interpretation": "q6-dispatch-seen-without-oracle-response",
        }
        result = self.run_verifier(payload)
        self.assertEqual(result.returncode, 48, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "q6-oracle-capture-missing")
        self.assertEqual(report["responsibility_boundary"], "q6-diagnostic-evidence")
        self.assertEqual(report["observed_service_failure"], "llama-completion-wrong-output")
        self.assertEqual(
            report["q6_workgroup_diagnostics"]["q6_dispatch_latest"]["spirv_hash"],
            "0x1bf751845c5dce75",
        )
        self.assert_claims_blocked(report)

    def test_q6_probe_writeback_cleared_oracle_missing_is_retained_by_verifier(self):
        payload = wrong_completion_payload(api_executor_reconciliation())
        payload["gpu"]["diagnostics"]["q6_workgroup_diagnostics"] = {
            "event_count": 0,
            "q6_probe_event_count": 1,
            "q6_dispatch_seen": True,
            "q6_dispatch_event_count": 1,
            "q6_oracle_capture_missing": True,
            "blocker_class": "q6-probe-writeback-cleared-oracle-missing",
            "diagnostic_interpretation": (
                "q6-probe-writeback-cleared-but-source-oracle-not-available-for-instrumented-module"
            ),
            **q6_verified_writeback(),
        }
        result = self.run_verifier(payload)
        self.assertEqual(result.returncode, 48, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "q6-probe-writeback-cleared-oracle-missing")
        self.assertEqual(report["responsibility_boundary"], "q6-diagnostic-evidence")
        self.assertEqual(
            report["q6_effective_blocker_class"],
            "q6-probe-writeback-cleared-oracle-missing",
        )
        self.assertEqual(report["q6_writeback_evidence"]["summary"], "pass")
        self.assertEqual(report["observed_service_failure"], "llama-completion-wrong-output")
        self.assert_claims_blocked(report)

    def test_completion_wrong_output_rejects_empty_reconciliation_pass(self):
        payload = wrong_completion_payload({"summary": "pass"})
        result = self.run_verifier(payload)
        self.assertEqual(result.returncode, 45, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "api-executor-reconciliation-ambiguous")
        self.assertIn("lacks substantive", json.dumps(report["api_executor_reconciliation"]["ambiguous"]))
        self.assert_claims_blocked(report)

    def test_completion_wrong_output_rejects_fnv_only_reconciliation_pass(self):
        payload = wrong_completion_payload(
            {
                "summary": "pass",
                "hash_algorithm": "fnv1a64",
                "dispatches": [
                    {
                        "api_canonical_hash": "0xaaaaaaaaaaaaaaaa",
                        "executor_canonical_hash": "0xaaaaaaaaaaaaaaaa",
                        "match_status": "match",
                    }
                ],
            }
        )
        result = self.run_verifier(payload)
        self.assertEqual(result.returncode, 45, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "api-executor-reconciliation-ambiguous")
        self.assertIn("diagnostic-only", json.dumps(report["api_executor_reconciliation"]["ambiguous"]))
        self.assert_claims_blocked(report)

    def test_completion_wrong_output_treats_producer_diagnostic_reconciliation_as_ambiguous(self):
        payload = wrong_completion_payload(
            {
                "schema": "pdocker.llama.api-executor-reconciliation.v1",
                "summary": "diagnostic",
                "proof_strength": "diagnostic",
                "hash_algorithm": "fnv1a64",
                "canonical_raw_fields_present": False,
                "dispatches": [
                    {
                        "dispatch_id": "1",
                        "match_status": "diagnostic-match",
                        "sender": {"spirv_hash": "0xaaaaaaaaaaaaaaaa"},
                        "received": {"spirv_hash": "0xaaaaaaaaaaaaaaaa"},
                    }
                ],
            }
        )
        result = self.run_verifier(payload)
        self.assertEqual(result.returncode, 45, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "api-executor-reconciliation-ambiguous")
        self.assertIn("unrecognized summary", json.dumps(report["api_executor_reconciliation"]["ambiguous"]))
        self.assert_claims_blocked(report)

    def test_completion_wrong_output_accepts_strict_transport_match_reconciliation(self):
        payload = wrong_completion_payload(
            {
                "schema": "pdocker.llama.api-executor-reconciliation.v1",
                "summary": "diagnostic",
                "proof_strength": "diagnostic",
                "hash_algorithm": "fnv1a64",
                "canonical_raw_fields_present": False,
                "duplicate_dispatch_ids": [],
                "dispatches": [
                    {
                        "dispatch_id": "1",
                        "match_status": "diagnostic-match",
                        "matches": {
                            "core_command_hash_comparable": True,
                            "core_command_hash": True,
                            "spirv_hash": True,
                            "descriptor_hash": True,
                            "push_hash": True,
                            "spec_hash": True,
                            "dispatch_hash": True,
                        },
                        "transport": {
                            "msg_trunc": False,
                            "msg_ctrunc": False,
                        },
                    }
                ],
            }
        )
        result = self.run_verifier(payload)
        self.assertNotEqual(result.returncode, 45, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["api_executor_reconciliation"]["summary"], "pass")
        self.assertNotEqual(report["classification"], "api-executor-reconciliation-ambiguous")
        self.assert_claims_blocked(report)

    def test_completion_wrong_output_with_reconciliation_still_requires_fresh_executor_marker(self):
        payload = wrong_completion_payload(api_executor_reconciliation())
        payload["gpu"]["diagnostics"]["runtime_freshness"] = {
            "summary": "fail",
            "expected_executor_marker": "gpu-executor-llama-q4k-callsite-20260520",
            "observed_executor_markers": [],
            "expected_icd_marker": "vulkan-icd-feature-chain-marker-20260518",
            "observed_icd_markers": ["vulkan-icd-feature-chain-marker-20260518"],
        }
        result = self.run_verifier(payload)
        self.assertEqual(result.returncode, 34, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "executor-marker-not-observed")
        self.assertEqual(report["observed_service_failure"], "llama-completion-wrong-output")
        self.assertEqual(report["api_executor_reconciliation"]["summary"], "pass")
        self.assert_claims_blocked(report)

    def test_completion_wrong_output_mismatch_reconciliation_hash_blocks_classification(self):
        payload = wrong_completion_payload(
            api_executor_reconciliation(executor_hash="0xbbbbbbbbbbbbbbbb")
        )
        result = self.run_verifier(payload)
        self.assertEqual(result.returncode, 46, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "api-executor-reconciliation-mismatch")
        self.assertEqual(report["api_executor_reconciliation"]["summary"], "mismatch")
        self.assertIn("canonical hash mismatch", json.dumps(report["api_executor_reconciliation"]["mismatches"]))
        self.assertFalse(report["benchmark_claim_allowed"])

    def test_completion_wrong_output_ambiguous_duplicate_or_unmatched_reconciliation(self):
        duplicate_payload = wrong_completion_payload(
            api_executor_reconciliation(duplicate_dispatches=[{"dispatch_id": "q6-0"}])
        )
        duplicate = self.run_verifier(duplicate_payload)
        self.assertEqual(duplicate.returncode, 45, duplicate.stdout)
        duplicate_report = json.loads(duplicate.stdout)
        self.assertEqual(duplicate_report["classification"], "api-executor-reconciliation-ambiguous")
        self.assertIn("duplicate", json.dumps(duplicate_report["api_executor_reconciliation"]["ambiguous"]))
        self.assertFalse(duplicate_report["benchmark_claim_allowed"])

        unmatched_payload = wrong_completion_payload(
            api_executor_reconciliation(unmatched_api_outputs=[{"prompt": "2+3="}])
        )
        unmatched = self.run_verifier(unmatched_payload)
        self.assertEqual(unmatched.returncode, 45, unmatched.stdout)
        unmatched_report = json.loads(unmatched.stdout)
        self.assertEqual(unmatched_report["classification"], "api-executor-reconciliation-ambiguous")
        self.assertIn("unmatched", json.dumps(unmatched_report["api_executor_reconciliation"]["ambiguous"]))
        self.assertFalse(unmatched_report["benchmark_claim_allowed"])

    def test_memory_blocker_preserves_artifact_diagnostics(self):
        payload = {
            "error": "runtime_memory_pressure",
            "memory": {"mem_available_mb": 600, "swap_free_mb": 4},
            "diagnostic_commands": ["adb shell cat /proc/meminfo"],
            "pdocker_memory_diagnostics": {
                "process_count": 1,
                "stale_llama_process_hint": True,
                "top_rss_processes": [{"raw": "u0_a1 42 1 123456 llama-server", "rss_mb": 120.6}],
                "process_sample": [{"raw": "u0_a1 42 1 123456 llama-server", "rss_mb": 120.6}],
                "diagnostic_commands": ["adb shell run-as pkg ps"],
                "cleanup_commands": ["adb shell run-as pkg stop-target-container"],
            },
            "memory_thresholds": {
                "summary": "fail",
                "mem_preflight_free_mb": {"observed_mb": 600, "required_min_mb": 384, "ok": True},
                "swap_free_mb": {
                    "observed_mb": 4,
                    "required_min_mb": 512,
                    "ok": False,
                    "state": "below-threshold",
                },
            },
        }
        result = self.run_verifier(payload)
        self.assertEqual(result.returncode, 20, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "runtime_memory_pressure")
        self.assertEqual(report["pdocker_memory_diagnostics"]["process_count"], 1)
        self.assertEqual(report["pdocker_memory_diagnostics"]["top_rss_processes"][0]["rss_mb"], 120.6)
        self.assertEqual(report["swap_free_threshold_state"], "below-threshold")
        self.assertIn("adb shell cat /proc/meminfo", report["diagnostic_commands"])
        self.assertIn("adb shell run-as pkg ps", report["diagnostic_commands"])
        self.assertIn("adb shell run-as pkg stop-target-container", report["cleanup_commands"])

    def test_readiness_false_blocks_gpu_run_claims(self):
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "readiness": {
                "ready": False,
                "memory": {"mem_available_mb": 128, "swap_free_mb": 64},
                "device_actions": ["wait for reclaim"],
            },
            "gpu": {"correctness": {"summary": {"correctness": "pass"}}},
            "comparison": {"speedup": 3.0},
        }
        result = self.run_verifier(payload)
        self.assertEqual(result.returncode, 21, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "readiness-blocked")
        self.assertFalse(report["correctness_claim_allowed"])
        self.assertFalse(report["benchmark_claim_allowed"])

    def test_env_mismatch_takes_precedence_over_q6_oracle_writeback_and_local_size(self):
        config = passing_config_propagation()
        config["summary"] = "fail"
        config["checks"][0]["expected"] = True
        config["checks"][0]["observed_values"] = [False]
        config["checks"][0]["status"] = "mismatch"
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "diagnostics": {
                    "runtime_freshness": runtime_marker(),
                    "config_propagation": config,
                    "q6_workgroup_diagnostics": {
                        "workgroup_shape_blocker": True,
                        "latest_status": "mismatch",
                        "blocker_class": "vulkan-device-execution-or-writeback",
                        **q6_verified_writeback(),
                    },
                },
                "correctness": gpu_correctness_report("fail", required_failures=1, passed=False, content="4"),
            },
            "cpu": {"tokens_per_second": 0.1},
            **speedup_sections(speedup=0.5, target_met=False, cpu_tps=0.1, gpu_tps=0.05),
        }
        result = self.run_verifier(payload, "--require-q6-workgroup-clear")
        self.assertEqual(result.returncode, 35, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "config-propagation-mismatch")
        self.assertEqual(report["responsibility_boundary"], "env-propagation")
        self.assertNotIn("q6_writeback_evidence", report)
        self.assertFalse(report["correctness_claim_allowed"])
        self.assertFalse(report["benchmark_claim_allowed"])

    def test_q6_local_size_takes_precedence_over_writeback_and_oracle_boundaries(self):
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "diagnostics": {
                    "runtime_freshness": runtime_marker(),
                    "config_propagation": passing_config_propagation(),
                    "q6_workgroup_diagnostics": {
                        "workgroup_shape_blocker": True,
                        "latest_status": "match",
                        "q6_writable_writeback_mismatches": [
                            {
                                "index": 2,
                                "binding": 2,
                                "gpu_after_dispatch_hash": "0x1111111111111111",
                                "fd_after_hash": "0x2222222222222222",
                                "writeback_mismatch": True,
                            }
                        ],
                    },
                },
                "correctness": gpu_correctness_report(),
            },
            "cpu": {"tokens_per_second": 0.1},
            **speedup_sections(speedup=2.0, target_met=True),
        }
        result = self.run_verifier(payload)
        self.assertEqual(result.returncode, 32, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "q6-workgroup-shape-blocker")
        self.assertEqual(report["responsibility_boundary"], "q6-local-size")
        self.assertEqual(report["q6_writeback_evidence"]["summary"], "mismatch")
        self.assertFalse(report["correctness_claim_allowed"])
        self.assertFalse(report["benchmark_claim_allowed"])

    def test_missing_executor_marker_blocks_compare_and_benchmark_claims(self):
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "diagnostics": {
                    "runtime_freshness": {
                        "summary": "fail",
                        "expected_executor_marker": "gpu-executor-llama-q4k-callsite-20260520",
                        "observed_executor_markers": [],
                        "observed_icd_markers": ["vulkan-icd-feature-chain-marker-20260518"],
                    },
                    "q6_workgroup_diagnostics": {
                        "workgroup_shape_blocker": False,
                        "latest_status": "match",
                        "q6_writeback_verified_all": True,
                    },
                },
                "correctness": {"summary": {"correctness": "pass"}},
            },
            "cpu": {"tokens_per_second": 0.1},
            "comparison": {"speedup": 2.0, "target_met": True},
        }
        result = self.run_verifier(payload)
        self.assertEqual(result.returncode, 34, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "executor-marker-not-observed")
        self.assertFalse(report["correctness_claim_allowed"])
        self.assertFalse(report["benchmark_claim_allowed"])

    def test_cpu_comparison_required_for_benchmark_claim(self):
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "diagnostics": {
                    "runtime_freshness": runtime_marker(),
                    "config_propagation": passing_config_propagation(),
                    "q6_workgroup_diagnostics": {
                        "workgroup_shape_blocker": False,
                        "latest_status": "match",
                        **q6_verified_writeback(),
                    },
                },
                "correctness": gpu_correctness_report(),
            },
            **speedup_sections(),
        }
        result = self.run_verifier(payload)
        self.assertEqual(result.returncode, 0, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "q6-workgroup-cleared-and-oracle-match")
        self.assertTrue(report["correctness_claim_allowed"])
        self.assertFalse(report["cpu_comparison_available"])
        self.assertFalse(report["benchmark_claim_allowed"])

        payload["cpu"] = {"tokens_per_second": 0.1}
        with_cpu = self.run_verifier(payload)
        self.assertEqual(with_cpu.returncode, 0, with_cpu.stdout)
        self.assertTrue(json.loads(with_cpu.stdout)["benchmark_claim_allowed"])

    def test_generic_spirv_rope_yarn_cpu_oracle_mismatch_blocks_claims(self):
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "diagnostics": {
                    "runtime_freshness": runtime_marker(),
                    "config_propagation": passing_config_propagation(),
                    "generic_spirv_dispatch": {
                        "valid_android_vulkan_events": [
                            generic_spirv_cpu_oracle_event(status="mismatch")
                        ],
                    },
                    "q6_workgroup_diagnostics": {
                        "workgroup_shape_blocker": False,
                        "latest_status": "match",
                        **q6_verified_writeback(),
                    },
                },
                "correctness": gpu_correctness_report(),
            },
            "cpu": {"tokens_per_second": 0.1},
            **speedup_sections(),
        }
        result = self.run_verifier(payload)
        self.assertEqual(result.returncode, 47, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "generic-spirv-cpu-oracle-mismatch")
        self.assertEqual(report["responsibility_boundary"], "generic-spirv-cpu-oracle")
        self.assert_claims_blocked(report)
        self.assertIn(
            "0xac41e8033a67af4a",
            json.dumps(report["generic_spirv_cpu_oracle_mismatches"]),
        )

    def test_generic_spirv_cpu_oracle_match_does_not_trip_mismatch_gate(self):
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "diagnostics": {
                    "runtime_freshness": runtime_marker(),
                    "config_propagation": passing_config_propagation(),
                    "generic_spirv_dispatch": {
                        "valid_android_vulkan_events": [
                            generic_spirv_cpu_oracle_event(status="match")
                        ],
                    },
                    "q6_workgroup_diagnostics": {
                        "workgroup_shape_blocker": False,
                        "latest_status": "match",
                        **q6_verified_writeback(),
                    },
                },
                "correctness": gpu_correctness_report(),
            },
            "cpu": {"tokens_per_second": 0.1},
            **speedup_sections(),
        }
        result = self.run_verifier(payload)
        self.assertEqual(result.returncode, 0, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "q6-workgroup-cleared-and-oracle-match")
        self.assertTrue(report["correctness_claim_allowed"])
        self.assertTrue(report["benchmark_claim_allowed"])

    def test_q6_workgroup_clear_can_pass_even_when_numeric_oracle_still_mismatches(self):
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "diagnostics": {
                    "runtime_freshness": runtime_marker(),
                    "config_propagation": passing_config_propagation(),
                    "q6_workgroup_diagnostics": {
                        "workgroup_shape_blocker": False,
                        "latest_status": "mismatch",
                        "q6_shader_like_64_abs_delta": 3.25,
                        **q6_verified_writeback(),
                    },
                },
                "correctness": gpu_correctness_report("fail", required_failures=1, passed=False, content="4"),
            },
            **speedup_sections(speedup=0.5, target_met=False, cpu_tps=0.1, gpu_tps=0.05),
        }
        clear = self.run_verifier(payload, "--require-q6-workgroup-clear")
        self.assertEqual(clear.returncode, 0, clear.stdout)
        report = json.loads(clear.stdout)
        self.assertEqual(report["classification"], "q6-workgroup-cleared-but-oracle-mismatch")
        exact = self.run_verifier(payload, "--require-q6-match")
        self.assertEqual(exact.returncode, 30, exact.stdout)

    def test_q6_32x1x1_num_rows_treats_64_lane_delta_as_diagnostic_only(self):
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "served": False,
                "diagnostics": {
                    "blocker_class": "vulkan_device_discovery",
                    "runtime_freshness": runtime_marker(),
                    "config_propagation": passing_config_propagation(),
                    "q6_workgroup_diagnostics": {
                        "event_count": 2,
                        "workgroup_shape_blocker": False,
                        "latest_status": "mismatch",
                        "local_size_resolved": [32, 1, 1],
                        "blocker_class": "q6-arithmetic-reduction-or-output-layout",
                        "q6_shader_like_abs_delta": 1.0e-7,
                        "q6_shader_like_64_abs_delta": 6.2,
                        **q6_verified_writeback(),
                    },
                },
                "correctness": gpu_correctness_report("fail", required_failures=1, passed=False, content="4"),
            },
            "cpu": {"tokens_per_second": 0.1},
            **speedup_sections(speedup=0.5, target_met=False, cpu_tps=0.1, gpu_tps=0.05),
        }
        result = self.run_verifier(payload, "--require-q6-workgroup-clear")
        self.assertEqual(result.returncode, 0, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "q6-workgroup-cleared-but-oracle-mismatch")
        self.assertEqual(report["q6_effective_blocker_class"], "vulkan-device-execution")
        self.assertIn("vulkan-device-execution", report["next_action"])
        self.assertEqual(report["responsibility_boundary"], "q6-oracle")
        self.assertFalse(report["correctness_claim_allowed"])
        self.assertFalse(report["benchmark_claim_allowed"])
        interpretation = report["q6_shader_like_interpretation"]
        self.assertTrue(interpretation["q6_shader_like_oracle_cleared"])
        self.assertFalse(interpretation["q6_shader_like_64_required"])
        self.assertIn(
            "q6_shader_like_64_abs_delta=diagnostic-only",
            interpretation["q6_shader_like_clear_basis"],
        )

    def test_q6_safe_kernel_match_is_diagnostic_only_not_correctness_claim(self):
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "diagnostics": {
                    "runtime_freshness": runtime_marker(),
                    "config_propagation": passing_config_propagation(),
                    "q6_workgroup_diagnostics": {
                        **q6_verified_writeback(),
                        "event_count": 1,
                        "workgroup_shape_blocker": False,
                        "latest_status": "match",
                        "local_size_resolved": [1, 1, 1],
                        "q6k_safe_kernel": True,
                    },
                },
                "correctness": gpu_correctness_report("pass"),
            },
            "cpu": {"tokens_per_second": 0.1},
            **speedup_sections(),
        }
        result = self.run_verifier(payload)
        self.assertEqual(result.returncode, 0, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "q6-safe-kernel-diagnostic-only")
        self.assertEqual(report["responsibility_boundary"], "q6-diagnostic-evidence")
        self.assertFalse(report["terminal"])
        self.assertFalse(report["correctness_claim_allowed"])
        self.assertFalse(report["benchmark_claim_allowed"])
        self.assertEqual(report["q6_effective_blocker_class"], "q6-safe-kernel-diagnostic-only")

    def test_q6_safe_kernel_uses_single_invocation_local_size_and_bypasses_api_gate(self):
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "served": False,
                "diagnostics": {
                    "blocker_class": "vulkan_device_discovery",
                    "runtime_freshness": runtime_marker(),
                    "config_propagation": passing_config_propagation(),
                    "q6_workgroup_diagnostics": {
                        "event_count": 2,
                        "workgroup_shape_blocker": False,
                        "latest_status": "match",
                        "q6k_safe_kernel": True,
                        "local_size_resolved": [1, 1, 1],
                        "q6_shader_like_abs_delta": 1.0e-7,
                        "q6_shader_like_64_abs_delta": 7.8,
                        **q6_verified_writeback(),
                    },
                },
            },
        }
        payload["gpu"]["diagnostics"]["q6_workgroup_diagnostics"]["local_size_resolved"] = [1, 1, 1]
        result = self.run_verifier(payload, "--require-q6-match")
        self.assertEqual(result.returncode, 30, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "q6-safe-kernel-diagnostic-only")
        self.assertEqual(report["responsibility_boundary"], "q6-diagnostic-evidence")
        self.assertFalse(report["correctness_claim_allowed"])
        self.assertFalse(report["benchmark_claim_allowed"])
        interpretation = report["q6_shader_like_interpretation"]
        self.assertFalse(interpretation["q6_shader_like_64_required"])
        self.assertIn("q6k_safe_kernel=true", interpretation["q6_shader_like_clear_basis"])

    def test_q6_native_output_layout_probe_gets_specific_classification(self):
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "served": False,
                "diagnostics": {
                    "runtime_freshness": runtime_marker(),
                    "config_propagation": passing_config_propagation(),
                    "q6_workgroup_diagnostics": {
                        "event_count": 1,
                        "workgroup_shape_blocker": False,
                        "latest_status": "mismatch",
                        "local_size_resolved": [32, 1, 1],
                        "q6_shader_like_abs_delta": 1.0e-7,
                        "q6_output_layout_probe": {
                            "summary": "canonical-mismatch-found-elsewhere",
                            "mismatch_count": 2,
                            "found_elsewhere_count": 2,
                            "consistent_relative_offset": True,
                            "relative_offset": 2,
                            "samples": [
                                q6_layout_sample_with_store_model(
                                    0,
                                    expected=7.5,
                                    gpu_at_dst=3.2,
                                    abs_error_at_dst=4.3,
                                    best_index=2,
                                    best_value=7.5,
                                    best_abs_error=0.0,
                                    best_relative_offset=2,
                                    found_elsewhere=True,
                                ),
                                q6_layout_sample_with_store_model(
                                    1,
                                    expected=8.5,
                                    gpu_at_dst=4.2,
                                    abs_error_at_dst=4.3,
                                    best_index=3,
                                    best_value=8.5,
                                    best_abs_error=0.0,
                                    best_relative_offset=2,
                                    found_elsewhere=True,
                                )
                            ],
                        },
                        **q6_store_index_model_reflection(),
                        **q6_verified_writeback(),
                    },
                },
            },
        }
        result = self.run_verifier(payload, "--require-q6-workgroup-clear")
        self.assertEqual(result.returncode, 0, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "q6-native-output-layout")
        self.assertEqual(report["responsibility_boundary"], "q6-output-layout")
        self.assertEqual(report["q6_effective_blocker_class"], "native-q6-output-layout")
        self.assertFalse(report["correctness_claim_allowed"])

    def test_q6_output_index_probe_structured_evidence_is_preserved(self):
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "served": False,
                "diagnostics": {
                    "runtime_freshness": runtime_marker(),
                    "config_propagation": passing_config_propagation(),
                    "q6_workgroup_diagnostics": {
                        "event_count": 1,
                        "workgroup_shape_blocker": False,
                        "latest_status": "mismatch",
                        "local_size_resolved": [32, 1, 1],
                        "q6_shader_like_abs_delta": 0.0,
                        "q6_output_layout_probe": {
                            "summary": "canonical-mismatch-inconclusive",
                            "samples": [
                                q6_layout_sample_with_store_model(257, expected=1.25, gpu_at_dst=0.5)
                            ],
                        },
                        "q6_output_index_probe": {
                            "summary": "fixed-offset",
                            "sample_count": 1,
                            "fixed_offset_count": 1,
                            "samples": [
                                {
                                    "output_index": 257,
                                    "expected_store_index": 257,
                                    "observed_index": 259,
                                    "relative_offset": 2,
                                    "sample_class": "fixed-offset",
                                }
                            ],
                        },
                        **q6_store_index_model_reflection(),
                        **q6_verified_writeback(),
                    },
                },
            },
        }
        result = self.run_verifier(payload, "--require-q6-workgroup-clear")
        self.assertEqual(result.returncode, 0, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["q6_output_index_probe_summary"], "fixed-offset")
        self.assertEqual(report["q6_output_index_probe"]["summary"], "fixed-offset")
        self.assertEqual(report["q6_output_index_probe"]["samples"][0]["output_index"], 257)
        self.assertEqual(report["classification"], "q6-native-output-layout")

    def test_q6_output_index_probe_summary_classifies_scatter_layout(self):
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "served": False,
                "diagnostics": {
                    "runtime_freshness": runtime_marker(),
                    "config_propagation": passing_config_propagation(),
                    "q6_workgroup_diagnostics": {
                        "event_count": 1,
                        "workgroup_shape_blocker": False,
                        "latest_status": "mismatch",
                        "local_size_resolved": [32, 1, 1],
                        "q6_shader_like_abs_delta": 1.0e-7,
                        "q6_output_index_probe_summary": "scatter",
                        "q6_output_layout_probe": {
                            "summary": "canonical-mismatch-inconclusive",
                            "samples": [
                                q6_layout_sample_with_store_model(257, expected=1.25, gpu_at_dst=0.5)
                            ],
                        },
                        **q6_store_index_model_reflection(),
                        **q6_verified_writeback(),
                    },
                },
            },
        }
        result = self.run_verifier(payload, "--require-q6-workgroup-clear")
        self.assertEqual(result.returncode, 0, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "q6-native-output-layout")
        self.assertEqual(report["q6_output_index_probe_summary"], "scatter")
        self.assertEqual(report["q6_effective_blocker_class"], "native-q6-output-layout")

    def test_q6_output_index_probe_summary_classifies_final_store_value(self):
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "served": False,
                "diagnostics": {
                    "runtime_freshness": runtime_marker(),
                    "config_propagation": passing_config_propagation(),
                    "q6_workgroup_diagnostics": {
                        "event_count": 1,
                        "workgroup_shape_blocker": False,
                        "latest_status": "mismatch",
                        "local_size_resolved": [32, 1, 1],
                        "q6_shader_like_abs_delta": 1.0e-7,
                        "q6_output_index_probe_summary": "final-store-value",
                        "q6_output_layout_probe": {
                            "summary": "canonical-mismatch-not-found",
                            "samples": [
                                q6_layout_sample_with_store_model(257, expected=1.25, gpu_at_dst=0.5)
                            ],
                        },
                        **q6_store_index_model_reflection(),
                        **q6_verified_writeback(),
                    },
                },
            },
        }
        result = self.run_verifier(payload, "--require-q6-workgroup-clear")
        self.assertEqual(result.returncode, 0, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "q6-native-final-store")
        self.assertEqual(report["q6_output_index_probe_summary"], "final-store-value")
        self.assertEqual(report["q6_effective_blocker_class"], "native-q6-final-store")

    def test_q6_single_elsewhere_probe_is_inconclusive_not_output_layout(self):
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "served": False,
                "diagnostics": {
                    "runtime_freshness": runtime_marker(),
                    "config_propagation": passing_config_propagation(),
                    "q6_workgroup_diagnostics": {
                        "event_count": 1,
                        "workgroup_shape_blocker": False,
                        "latest_status": "mismatch",
                        "local_size_resolved": [32, 1, 1],
                        "q6_shader_like_abs_delta": 1.0e-7,
                        "q6_output_layout_probe": {
                            "summary": "canonical-mismatch-inconclusive",
                            "mismatch_count": 2,
                            "found_elsewhere_count": 1,
                            "consistent_relative_offset": False,
                            "samples": [
                                q6_layout_sample_with_store_model(
                                    0,
                                    expected=7.5,
                                    gpu_at_dst=3.2,
                                    best_index=2,
                                    best_value=7.5,
                                    best_abs_error=0.0,
                                    best_relative_offset=2,
                                    found_elsewhere=True,
                                ),
                                q6_layout_sample_with_store_model(
                                    1,
                                    expected=8.5,
                                    gpu_at_dst=4.2,
                                    best_index=1,
                                    best_value=4.2,
                                    best_abs_error=4.3,
                                    best_relative_offset=0,
                                    found_elsewhere=False,
                                ),
                            ],
                        },
                        **q6_store_index_model_reflection(),
                        **q6_verified_writeback(),
                    },
                },
            },
        }
        result = self.run_verifier(payload, "--require-q6-workgroup-clear")
        self.assertEqual(result.returncode, 0, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "q6-native-output-layout-inconclusive")
        self.assertEqual(report["q6_effective_blocker_class"], "native-q6-output-layout-inconclusive")

    def test_q6_store_index_model_requires_full_reflection(self):
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "diagnostics": {
                    "runtime_freshness": runtime_marker(),
                    "config_propagation": passing_config_propagation(),
                    "q6_workgroup_diagnostics": {
                        "event_count": 1,
                        "workgroup_shape_blocker": False,
                        "latest_status": "mismatch",
                        "local_size_resolved": [32, 1, 1],
                        "q6_shader_like_abs_delta": 1.0e-7,
                        "q6_store_index_model_valid": True,
                        "q6_output_layout_probe": {
                            "summary": "canonical-mismatch-found-elsewhere",
                            "mismatch_count": 1,
                            "found_elsewhere_count": 1,
                            "samples": [
                                {
                                    "dst_index": 0,
                                    "expected_store_index": 0,
                                    "store_formula_valid": True,
                                    "expected": 1.0,
                                    "gpu_at_dst": 0.0,
                                    "found_elsewhere": True,
                                }
                            ],
                        },
                        **q6_verified_writeback(),
                    },
                },
            },
        }
        result = self.run_verifier(payload, "--require-q6-workgroup-clear")
        self.assertEqual(result.returncode, 31, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "q6-store-index-model-incomplete")
        self.assertEqual(report["responsibility_boundary"], "q6-oracle")

    def test_q6_broad_inconsistent_elsewhere_probe_rejects_fixed_output_layout(self):
        samples = []
        for index in range(16):
            samples.append(
                q6_layout_sample_with_store_model(
                    index,
                    expected=float(index + 1),
                    gpu_at_dst=0.0,
                    best_index=index + 100 + index,
                    best_value=float(index + 1),
                    best_abs_error=0.0,
                    best_relative_offset=100 + index,
                    found_elsewhere=index < 4,
                )
            )
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "served": False,
                "diagnostics": {
                    "runtime_freshness": runtime_marker(),
                    "config_propagation": passing_config_propagation(),
                    "q6_workgroup_diagnostics": {
                        "event_count": 1,
                        "workgroup_shape_blocker": False,
                        "latest_status": "mismatch",
                        "local_size_resolved": [32, 1, 1],
                        "q6_shader_like_abs_delta": 1.0e-7,
                        "q6_output_layout_probe": {
                            "summary": "canonical-mismatch-inconclusive",
                            "mismatch_count": 16,
                            "found_elsewhere_count": 4,
                            "consistent_relative_offset": False,
                            "samples": samples,
                        },
                        **q6_store_index_model_reflection(),
                        **q6_verified_writeback(),
                    },
                },
            },
        }
        result = self.run_verifier(payload, "--require-q6-workgroup-clear")
        self.assertEqual(result.returncode, 0, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "q6-native-device-execution-or-final-store")
        self.assertEqual(report["responsibility_boundary"], "q6-native-device-execution")
        self.assertEqual(report["q6_effective_blocker_class"], "native-q6-device-execution-or-final-store")

    def test_q6_row_provenance_probe_classifies_other_row_layout(self):
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "served": False,
                "diagnostics": {
                    "runtime_freshness": runtime_marker(),
                    "config_propagation": passing_config_propagation(),
                    "q6_workgroup_diagnostics": {
                        "event_count": 1,
                        "workgroup_shape_blocker": False,
                        "latest_status": "mismatch",
                        "local_size_resolved": [32, 1, 1],
                        "q6_shader_like_abs_delta": 1.0e-7,
                        "q6_output_layout_probe": {
                            "summary": "canonical-mismatch-inconclusive",
                            "mismatch_count": 16,
                            "found_elsewhere_count": 4,
                            "consistent_relative_offset": False,
                        },
                        "q6_row_provenance_probe": {
                            "summary": "other-row-match",
                            "mismatch_count": 4,
                            "other_row_match_count": 4,
                            "consistent_row_delta": True,
                            "row_delta": 1,
                            "samples": [
                                {
                                    "dst_index": 0,
                                    "gpu_at_dst": 2.0,
                                    "canonical_expected": 1.0,
                                    "best_expected_dst_index": 1,
                                    "best_expected_value": 2.0,
                                    "best_expected_abs_error": 0.0,
                                    "row_delta": 1,
                                    "class": "other-row",
                                }
                            ],
                        },
                        **q6_verified_writeback(),
                    },
                },
            },
        }
        result = self.run_verifier(payload, "--require-q6-workgroup-clear")
        self.assertEqual(result.returncode, 31, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "q6-store-index-model-incomplete")
        self.assertEqual(report["responsibility_boundary"], "q6-oracle")

    def test_q6_partial_signature_probe_classifies_local_y_partial_store(self):
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "served": False,
                "diagnostics": {
                    "runtime_freshness": runtime_marker(),
                    "config_propagation": passing_config_propagation(),
                    "q6_workgroup_diagnostics": {
                        "event_count": 1,
                        "workgroup_shape_blocker": False,
                        "latest_status": "mismatch",
                        "local_size_resolved": [32, 1, 1],
                        "q6_shader_like_abs_delta": 1.0e-7,
                        "q6_output_layout_probe": {
                            "summary": "canonical-mismatch-inconclusive",
                            "mismatch_count": 16,
                            "found_elsewhere_count": 0,
                            "consistent_relative_offset": False,
                            "samples": [
                                q6_layout_sample_with_store_model(
                                    0,
                                    expected=7.5,
                                    gpu_at_dst=3.75,
                                )
                            ],
                        },
                        "q6_partial_signature_probe": {
                            "summary": "local-y-partial",
                            "mismatch_count": 16,
                            "local_y_partial_match_count": 16,
                            "lane_partial_match_count": 0,
                            "samples": [
                                {
                                    "dst_index": 0,
                                    "store_formula_valid": True,
                                    "expected": 7.5,
                                    "gpu_at_dst": 3.75,
                                    "local_y0_sum": 3.75,
                                    "local_y1_sum": 3.75,
                                    "local_y_best": 0,
                                    "local_y_best_abs_error": 0.0,
                                    "native_reduction_tree_available": True,
                                    "native_reduction_tree_sum": 7.5,
                                    "native_reduction_tree_with_accumulator": 7.5,
                                    "native_reduction_tree_gpu_abs_error": 3.75,
                                    "expected_gpu_abs_error": 3.75,
                                    "class": "local-y-partial",
                                }
                            ],
                        },
                        **q6_store_index_model_reflection(),
                        **q6_verified_writeback(),
                    },
                },
            },
        }
        result = self.run_verifier(payload, "--require-q6-workgroup-clear")
        self.assertEqual(result.returncode, 0, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "q6-native-local-y-partial-store")
        self.assertEqual(report["responsibility_boundary"], "q6-native-partial-store")
        self.assertEqual(report["q6_effective_blocker_class"], "native-q6-local-y-partial-store")
        self.assertEqual(report["q6_partial_signature_probe"]["summary"], "local-y-partial")
        sample = report["q6_partial_signature_probe"]["samples"][0]
        self.assertTrue(sample["native_reduction_tree_available"])
        self.assertEqual(sample["native_reduction_tree_with_accumulator"], 7.5)
        self.assertEqual(sample["native_reduction_tree_gpu_abs_error"], 3.75)
        self.assertEqual(sample["expected_gpu_abs_error"], 3.75)

    def test_q6_partial_signature_probe_classifies_lane_partial_store(self):
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "served": False,
                "diagnostics": {
                    "runtime_freshness": runtime_marker(),
                    "config_propagation": passing_config_propagation(),
                    "q6_workgroup_diagnostics": {
                        "event_count": 1,
                        "workgroup_shape_blocker": False,
                        "latest_status": "mismatch",
                        "local_size_resolved": [32, 1, 1],
                        "q6_shader_like_abs_delta": 1.0e-7,
                        "q6_output_layout_probe": {
                            "summary": "canonical-mismatch-inconclusive",
                            "mismatch_count": 16,
                            "found_elsewhere_count": 0,
                            "consistent_relative_offset": False,
                            "samples": [
                                q6_layout_sample_with_store_model(
                                    0,
                                    expected=7.5,
                                    gpu_at_dst=1.0,
                                )
                            ],
                        },
                        "q6_partial_signature_probe": {
                            "summary": "lane-partial",
                            "mismatch_count": 16,
                            "local_y_partial_match_count": 0,
                            "lane_partial_match_count": 16,
                            "samples": [
                                {
                                    "dst_index": 0,
                                    "store_formula_valid": True,
                                    "expected": 7.5,
                                    "gpu_at_dst": 1.0,
                                    "best_lane": 3,
                                    "best_lane_value": 1.0,
                                    "best_lane_abs_error": 0.0,
                                    "class": "lane-partial",
                                }
                            ],
                        },
                        **q6_store_index_model_reflection(),
                        **q6_verified_writeback(),
                    },
                },
            },
        }
        result = self.run_verifier(payload, "--require-q6-workgroup-clear")
        self.assertEqual(result.returncode, 0, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "q6-native-lane-partial-store")
        self.assertEqual(report["responsibility_boundary"], "q6-native-partial-store")
        self.assertEqual(report["q6_effective_blocker_class"], "native-q6-lane-partial-store")
        self.assertEqual(report["q6_partial_signature_probe"]["summary"], "lane-partial")

    def test_q6_native_reduction_probe_gets_specific_classification(self):
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "served": False,
                "diagnostics": {
                    "runtime_freshness": runtime_marker(),
                    "config_propagation": passing_config_propagation(),
                    "q6_workgroup_diagnostics": {
                        "event_count": 1,
                        "workgroup_shape_blocker": False,
                        "latest_status": "mismatch",
                        "local_size_resolved": [32, 1, 1],
                        "q6_shader_like_abs_delta": 1.0e-7,
                        "q6_output_layout_probe": {
                            "summary": "canonical-mismatch-not-found",
                            "samples": [
                                q6_layout_sample_with_store_model(
                                    0,
                                    expected=7.5,
                                    gpu_at_dst=3.2,
                                    abs_error_at_dst=4.3,
                                    best_index=5,
                                    best_value=3.1,
                                    best_abs_error=4.4,
                                    best_relative_offset=5,
                                    found_elsewhere=False,
                                )
                            ],
                        },
                        **q6_store_index_model_reflection(),
                        **q6_verified_writeback(),
                    },
                },
            },
        }
        result = self.run_verifier(payload, "--require-q6-workgroup-clear")
        self.assertEqual(result.returncode, 0, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "q6-native-reduction-or-device-execution")
        self.assertEqual(report["responsibility_boundary"], "q6-native-reduction")
        self.assertEqual(report["q6_effective_blocker_class"], "native-q6-reduction-or-device-execution")
        self.assertFalse(report["benchmark_claim_allowed"])

    def test_q6_native_reduction_probe_without_store_index_evidence_fails_closed(self):
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "served": False,
                "diagnostics": {
                    "runtime_freshness": runtime_marker(),
                    "config_propagation": passing_config_propagation(),
                    "q6_workgroup_diagnostics": {
                        "event_count": 1,
                        "workgroup_shape_blocker": False,
                        "latest_status": "mismatch",
                        "local_size_resolved": [32, 1, 1],
                        "q6_shader_like_abs_delta": 1.0e-7,
                        "q6_output_layout_probe": {
                            "summary": "canonical-mismatch-not-found",
                            "samples": [],
                        },
                        **q6_verified_writeback(),
                    },
                },
            },
        }
        result = self.run_verifier(payload, "--require-q6-workgroup-clear")
        self.assertEqual(result.returncode, 31, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "q6-store-index-model-incomplete")
        self.assertEqual(report["responsibility_boundary"], "q6-oracle")

    def test_q6_writeback_mismatch_precedes_native_output_layout_probe(self):
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "diagnostics": {
                    "runtime_freshness": runtime_marker(),
                    "config_propagation": passing_config_propagation(),
                    "q6_workgroup_diagnostics": {
                        "event_count": 1,
                        "workgroup_shape_blocker": False,
                        "latest_status": "mismatch",
                        "local_size_resolved": [32, 1, 1],
                        "q6_output_layout_probe": {"summary": "canonical-mismatch-found-elsewhere"},
                        **q6_verified_writeback(),
                    },
                },
            },
        }
        q6 = payload["gpu"]["diagnostics"]["q6_workgroup_diagnostics"]
        q6["q6_row_indexed_writeback_evidence"][0]["f32_after_writeback"] = [
            {"index": 257, "value": 9.99}
        ]
        result = self.run_verifier(payload, "--require-q6-workgroup-clear")
        self.assertEqual(result.returncode, 40, result.stdout)
        self.assertEqual(json.loads(result.stdout)["classification"], "q6-writeback-mismatch")

    def test_q6_final_store_boundary_classifies_native_final_store(self):
        q6 = {
            "event_count": 1,
            "workgroup_shape_blocker": False,
            "latest_status": "mismatch",
            "local_size_resolved": [32, 1, 1],
            "q6_output_layout_probe": {
                "summary": "canonical-mismatch-inconclusive",
                "samples": [
                    q6_layout_sample_with_store_model(257, expected=1.25, gpu_at_dst=0.5)
                ],
            },
            "q6_final_store_boundary": q6_final_store_boundary(
                "native-final-store-mismatch",
                final_value=0.5,
                expected=1.25,
                fd_after=0.5,
            ),
            **q6_store_index_model_reflection(),
            **q6_verified_writeback(),
        }
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "diagnostics": {
                    "runtime_freshness": runtime_marker(),
                    "config_propagation": passing_config_propagation(),
                    "q6_workgroup_diagnostics": q6,
                },
            },
        }
        result = self.run_verifier(payload, "--require-q6-workgroup-clear")
        self.assertEqual(result.returncode, 0, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "q6-native-final-store")
        self.assertEqual(report["responsibility_boundary"], "q6-native-final-store")
        self.assertEqual(report["q6_effective_blocker_class"], "native-q6-final-store")
        self.assertEqual(report["q6_final_store_boundary"]["summary"], "native-final-store-mismatch")

    def test_q6_final_store_boundary_classifies_executor_writeback(self):
        q6 = {
            "event_count": 1,
            "workgroup_shape_blocker": False,
            "latest_status": "mismatch",
            "local_size_resolved": [32, 1, 1],
            "q6_output_layout_probe": {
                "summary": "canonical-mismatch-inconclusive",
                "samples": [
                    q6_layout_sample_with_store_model(257, expected=1.25, gpu_at_dst=1.25)
                ],
            },
            "q6_final_store_boundary": q6_final_store_boundary(
                "executor-writeback-mismatch",
                final_value=1.25,
                expected=1.25,
                fd_after=0.5,
            ),
            **q6_store_index_model_reflection(),
            **q6_verified_writeback(),
        }
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "diagnostics": {
                    "runtime_freshness": runtime_marker(),
                    "config_propagation": passing_config_propagation(),
                    "q6_workgroup_diagnostics": q6,
                },
            },
        }
        result = self.run_verifier(payload, "--require-q6-workgroup-clear")
        self.assertEqual(result.returncode, 40, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "q6-writeback-mismatch")
        self.assertEqual(report["responsibility_boundary"], "q6-writeback")
        self.assertEqual(report["q6_effective_blocker_class"], "executor-final-writeback")
        self.assertEqual(report["q6_final_store_boundary"]["summary"], "executor-writeback-mismatch")

    def test_q6_debug_u32_blocker_precedes_final_store_boundary(self):
        q6 = {
            "event_count": 1,
            "workgroup_shape_blocker": False,
            "latest_status": "mismatch",
            "local_size_resolved": [32, 1, 1],
            "q6_debug_u32_probe_blocker": "q6-debug-u32-final-store-trace-missing",
            "q6_final_store_boundary": q6_final_store_boundary("native-final-store-mismatch"),
            "q6_output_layout_probe": {
                "summary": "canonical-mismatch-inconclusive",
                "samples": [
                    q6_layout_sample_with_store_model(257, expected=1.25, gpu_at_dst=0.5)
                ],
            },
            **q6_store_index_model_reflection(),
            **q6_verified_writeback(),
        }
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "diagnostics": {
                    "runtime_freshness": runtime_marker(),
                    "config_propagation": passing_config_propagation(),
                    "q6_workgroup_diagnostics": q6,
                },
            },
        }
        result = self.run_verifier(payload, "--require-q6-workgroup-clear")
        self.assertEqual(result.returncode, 49, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "q6-debug-u32-final-store-trace-missing")
        self.assertEqual(report["responsibility_boundary"], "q6-debug-u32-probe")

    def test_q6_final_store_boundary_summary_without_samples_is_inconclusive(self):
        q6 = {
            "event_count": 1,
            "workgroup_shape_blocker": False,
            "latest_status": "mismatch",
            "local_size_resolved": [32, 1, 1],
            "q6_shader_like_abs_delta": 0.0,
            "q6_output_layout_probe": {
                "summary": "canonical-mismatch-inconclusive",
                "samples": [
                    q6_layout_sample_with_store_model(257, expected=1.25, gpu_at_dst=0.5)
                ],
            },
            "q6_final_store_boundary": {
                "schema": "pdocker.q6k.final-store-boundary.v1",
                "summary": "native-final-store-mismatch",
                "joined_sample_count": 0,
                "samples": [],
            },
            **q6_store_index_model_reflection(),
            **q6_verified_writeback(),
        }
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "diagnostics": {
                    "runtime_freshness": runtime_marker(),
                    "config_propagation": passing_config_propagation(),
                    "q6_workgroup_diagnostics": q6,
                },
            },
        }
        result = self.run_verifier(payload, "--require-q6-workgroup-clear")
        self.assertEqual(result.returncode, 0, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["q6_final_store_boundary"]["summary"], "inconclusive")
        self.assertNotEqual(report["classification"], "q6-native-final-store")
        self.assertFalse(report["correctness_claim_allowed"])
        self.assertFalse(report["benchmark_claim_allowed"])

    def test_q6_native_vs_writeback_split_classifies_native_final_store(self):
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "diagnostics": {
                    "runtime_freshness": runtime_marker(),
                    "config_propagation": passing_config_propagation(),
                    "q6_workgroup_diagnostics": {
                        "event_count": 1,
                        "workgroup_shape_blocker": False,
                        "latest_status": "mismatch",
                        "local_size_resolved": [32, 1, 1],
                        "q6_shader_like_abs_delta": 0.0,
                        "q6_shader_like_oracle_cleared": True,
                        "q6_output_layout_probe": {
                            "summary": "canonical-mismatch-inconclusive",
                            "mismatch_count": 16,
                            "found_elsewhere_count": 4,
                            "consistent_relative_offset": False,
                            "samples": [
                                q6_layout_sample_with_store_model(
                                    257,
                                    expected=1.25,
                                    gpu_at_dst=0.5,
                                )
                            ],
                        },
                        "q6_native_vs_writeback_split": {
                            "summary": "native-final-store-or-readback",
                            "oracle_writeback": False,
                            "joined_sample_count": 1,
                            "samples": [
                                {
                                    "dst_index": 257,
                                    "expected": 1.25,
                                    "native_gpu_at_dst": 0.5,
                                    "fd_after_writeback": 0.5,
                                    "native_matches_expected": False,
                                    "writeback_matches_native": True,
                                    "writeback_matches_expected": False,
                                }
                            ],
                        },
                        **q6_store_index_model_reflection(),
                        **q6_verified_writeback(),
                    },
                },
            },
        }
        result = self.run_verifier(payload, "--require-q6-workgroup-clear")
        self.assertEqual(result.returncode, 0, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "q6-native-final-store-or-readback")
        self.assertEqual(report["responsibility_boundary"], "q6-native-final-store-readback")
        self.assertEqual(report["q6_effective_blocker_class"], "native-q6-final-store-or-readback")

    def test_q6_debug_u32_final_store_trace_missing_is_surfaced(self):
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "diagnostics": {
                    "runtime_freshness": runtime_marker(),
                    "config_propagation": passing_config_propagation(),
                    "q6_workgroup_diagnostics": {
                        "event_count": 1,
                        "workgroup_shape_blocker": False,
                        "latest_status": "mismatch",
                        "local_size_resolved": [32, 1, 1],
                        "q6_debug_u32_probe": {
                            "summary": "fail",
                            "debug_binding_count": 1,
                            "executed_final_trace_v2_count": 0,
                            "failures": ["no executed Q6 final-store trace-v2 record was found"],
                        },
                        "q6_native_vs_writeback_split": {
                            "summary": "native-final-store-or-readback",
                            "joined_sample_count": 1,
                        },
                        **q6_verified_writeback(),
                    },
                },
            },
        }
        result = self.run_verifier(payload, "--require-q6-workgroup-clear")
        self.assertEqual(result.returncode, 49, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "q6-debug-u32-final-store-trace-missing")
        self.assertEqual(report["responsibility_boundary"], "q6-debug-u32-probe")
        self.assertEqual(report["q6_effective_blocker_class"], "q6-debug-u32-final-store-trace-missing")

    def test_q6_debug_u32_blocker_precedes_native_output_layout(self):
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "diagnostics": {
                    "runtime_freshness": runtime_marker(),
                    "config_propagation": passing_config_propagation(),
                    "q6_workgroup_diagnostics": {
                        "event_count": 1,
                        "workgroup_shape_blocker": False,
                        "latest_status": "mismatch",
                        "local_size_resolved": [32, 1, 1],
                        "q6_debug_u32_probe_blocker": "q6-debug-u32-final-store-trace-missing",
                        "q6_debug_u32_probe": {
                            "summary": "fail",
                            "debug_binding_count": 1,
                            "executed_final_trace_v2_count": 0,
                        },
                        "q6_output_layout_probe": {
                            "summary": "canonical-mismatch-found-elsewhere",
                            "mismatch_count": 1,
                        },
                        **q6_verified_writeback(),
                    },
                },
            },
        }
        result = self.run_verifier(payload, "--require-q6-workgroup-clear")
        self.assertEqual(result.returncode, 49, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "q6-debug-u32-final-store-trace-missing")

    def test_q6_writeback_mismatch_precedes_debug_u32_blocker(self):
        q6 = q6_verified_writeback()
        q6["q6_writable_writeback_mismatches"] = [{"binding": 2}]
        q6["q6_writable_bindings"][0]["fd_after_hash"] = "0x2222222222222222"
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "diagnostics": {
                    "runtime_freshness": runtime_marker(),
                    "config_propagation": passing_config_propagation(),
                    "q6_workgroup_diagnostics": {
                        "event_count": 1,
                        "workgroup_shape_blocker": False,
                        "latest_status": "mismatch",
                        "local_size_resolved": [32, 1, 1],
                        "q6_debug_u32_probe_blocker": "q6-debug-u32-final-store-trace-missing",
                        **q6,
                    },
                },
            },
        }
        result = self.run_verifier(payload, "--require-q6-workgroup-clear")
        self.assertEqual(result.returncode, 40, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "q6-writeback-mismatch")

    def test_q6_native_vs_writeback_split_classifies_executor_writeback(self):
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "diagnostics": {
                    "runtime_freshness": runtime_marker(),
                    "config_propagation": passing_config_propagation(),
                    "q6_workgroup_diagnostics": {
                        "event_count": 1,
                        "workgroup_shape_blocker": False,
                        "latest_status": "mismatch",
                        "local_size_resolved": [32, 1, 1],
                        "q6_output_layout_probe": {
                            "summary": "canonical-mismatch-inconclusive",
                            "samples": [
                                q6_layout_sample_with_store_model(
                                    257,
                                    expected=1.25,
                                    gpu_at_dst=1.25,
                                )
                            ],
                        },
                        "q6_native_vs_writeback_split": {
                            "summary": "executor-final-writeback",
                            "oracle_writeback": False,
                            "joined_sample_count": 1,
                            "samples": [
                                {
                                    "dst_index": 257,
                                    "expected": 1.25,
                                    "native_gpu_at_dst": 1.25,
                                    "fd_after_writeback": 0.5,
                                    "native_matches_expected": True,
                                    "writeback_matches_native": False,
                                    "writeback_matches_expected": False,
                                }
                            ],
                        },
                        **q6_store_index_model_reflection(),
                        **q6_verified_writeback(),
                    },
                },
            },
        }
        result = self.run_verifier(payload, "--require-q6-workgroup-clear")
        self.assertEqual(result.returncode, 40, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "q6-writeback-mismatch")
        self.assertEqual(report["responsibility_boundary"], "q6-writeback")
        self.assertEqual(report["q6_effective_blocker_class"], "executor-final-writeback")

    def test_q6_non_expected_local_size_fails_closed_as_shape_blocker(self):
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "diagnostics": {
                    "runtime_freshness": runtime_marker(),
                    "config_propagation": passing_config_propagation(),
                    "q6_workgroup_diagnostics": {
                        "event_count": 1,
                        "workgroup_shape_blocker": False,
                        "latest_status": "mismatch",
                        "local_size_resolved": [64, 1, 1],
                        "q6_shader_like_abs_delta": 0.0,
                        "q6_shader_like_64_abs_delta": 0.0,
                        "q6_shader_like_oracle_cleared": True,
                        **q6_verified_writeback(),
                    },
                },
                "correctness": gpu_correctness_report("fail", required_failures=1, passed=False, content="4"),
            },
            "cpu": {"tokens_per_second": 0.1},
            **speedup_sections(speedup=0.5, target_met=False, cpu_tps=0.1, gpu_tps=0.05),
        }
        payload["gpu"]["diagnostics"]["q6_workgroup_diagnostics"]["local_size_resolved"] = [64, 1, 1]
        result = self.run_verifier(payload, "--require-q6-workgroup-clear")
        self.assertEqual(result.returncode, 31, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "q6-workgroup-shape-blocker")
        self.assertEqual(report["responsibility_boundary"], "q6-local-size")
        self.assertFalse(report["correctness_claim_allowed"])
        self.assertFalse(report["benchmark_claim_allowed"])

    def test_q6_missing_local_size_fails_closed_as_shape_blocker(self):
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "diagnostics": {
                    "runtime_freshness": runtime_marker(),
                    "config_propagation": passing_config_propagation(),
                    "q6_workgroup_diagnostics": {
                        "event_count": 1,
                        "workgroup_shape_blocker": False,
                        "latest_status": "mismatch",
                        "q6_shader_like_abs_delta": 0.0,
                        "q6_shader_like_64_abs_delta": 0.0,
                        "q6_shader_like_oracle_cleared": True,
                        **q6_verified_writeback(),
                    },
                },
                "correctness": gpu_correctness_report("fail", required_failures=1, passed=False, content="4"),
            },
            "cpu": {"tokens_per_second": 0.1},
            **speedup_sections(speedup=0.5, target_met=False, cpu_tps=0.1, gpu_tps=0.05),
        }
        payload["gpu"]["diagnostics"]["q6_workgroup_diagnostics"].pop("local_size_resolved")
        result = self.run_verifier(payload)
        self.assertEqual(result.returncode, 32, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "q6-workgroup-shape-blocker")

    def test_q6_32x1x1_num_rows_nonzero_shader_like_delta_remains_arithmetic_boundary(self):
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "diagnostics": {
                    "runtime_freshness": runtime_marker(),
                    "config_propagation": passing_config_propagation(),
                    "q6_workgroup_diagnostics": {
                        "event_count": 1,
                        "workgroup_shape_blocker": False,
                        "latest_status": "mismatch",
                        "local_size_resolved": [32, 1, 1],
                        "blocker_class": "q6-arithmetic-reduction-or-output-layout",
                        "q6_shader_like_abs_delta": 0.5,
                        "q6_shader_like_64_abs_delta": 0.0,
                        **q6_verified_writeback(),
                    },
                },
                "correctness": gpu_correctness_report("fail", required_failures=1, passed=False, content="4"),
            },
            "cpu": {"tokens_per_second": 0.1},
            **speedup_sections(speedup=0.5, target_met=False, cpu_tps=0.1, gpu_tps=0.05),
        }
        result = self.run_verifier(payload, "--require-q6-workgroup-clear")
        self.assertEqual(result.returncode, 0, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["q6_effective_blocker_class"], "q6-arithmetic-reduction-or-output-layout")
        self.assertFalse(report["q6_shader_like_interpretation"]["q6_shader_like_oracle_cleared"])

    def test_q6_descriptor_invariant_mismatch_blocks_native_classification(self):
        q6 = {
            "event_count": 1,
            "workgroup_shape_blocker": False,
            "latest_status": "mismatch",
            "local_size_resolved": [32, 1, 1],
            "q6_shader_like_abs_delta": 0.0,
            "q6_descriptor_invariant_mismatches": [
                {
                    "index": 2,
                    "binding": 2,
                    "failed_invariant": "descriptor_offset_equals_api_offset",
                    "offset_equals_memory_plus_api_offset": True,
                    "gpu_offset_equals_memory_plus_api_offset": True,
                    "descriptor_offset_equals_api_offset": False,
                    "descriptor_range_matches_api_range": True,
                }
            ],
            **q6_store_index_model_reflection(),
            **q6_verified_writeback(),
        }
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "diagnostics": {
                    "runtime_freshness": runtime_marker(),
                    "config_propagation": passing_config_propagation(),
                    "q6_workgroup_diagnostics": q6,
                },
            },
        }
        result = self.run_verifier(payload, "--require-q6-workgroup-clear")
        self.assertEqual(result.returncode, 50, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "q6-descriptor-invariant-mismatch")
        self.assertEqual(report["responsibility_boundary"], "q6-descriptor-object-graph")
        self.assertEqual(report["q6_effective_blocker_class"], "descriptor-invariant-mismatch")
        self.assertEqual(
            report["q6_descriptor_invariant_mismatches"][0]["failed_invariant"],
            "descriptor_offset_equals_api_offset",
        )
        self.assertFalse(report["correctness_claim_allowed"])
        self.assertFalse(report["benchmark_claim_allowed"])

    def test_q6_descriptor_invariant_false_in_writable_binding_blocks_native_classification(self):
        q6 = {
            "event_count": 1,
            "workgroup_shape_blocker": False,
            "latest_status": "mismatch",
            "local_size_resolved": [32, 1, 1],
            "q6_shader_like_abs_delta": 0.0,
            **q6_store_index_model_reflection(),
            **q6_verified_writeback(),
        }
        q6["q6_writable_bindings"][0]["gpu_offset_equals_memory_plus_api_offset"] = False
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "diagnostics": {
                    "runtime_freshness": runtime_marker(),
                    "config_propagation": passing_config_propagation(),
                    "q6_workgroup_diagnostics": q6,
                },
            },
        }
        result = self.run_verifier(payload, "--require-q6-workgroup-clear")
        self.assertEqual(result.returncode, 50, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "q6-descriptor-invariant-mismatch")
        self.assertEqual(
            report["q6_descriptor_invariant_mismatches"][0]["failed_invariant"],
            "gpu_offset_equals_memory_plus_api_offset",
        )

    def test_q6_missing_descriptor_invariant_fields_blocks_native_classification(self):
        q6 = {
            "event_count": 1,
            "workgroup_shape_blocker": False,
            "latest_status": "mismatch",
            "local_size_resolved": [32, 1, 1],
            "q6_shader_like_abs_delta": 0.0,
            **q6_store_index_model_reflection(),
            **q6_verified_writeback(),
        }
        verifier_module = load_verifier_module()
        for field in verifier_module.Q6_DESCRIPTOR_INVARIANT_FIELDS:
            q6["q6_writable_bindings"][0].pop(field, None)
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "diagnostics": {
                    "runtime_freshness": runtime_marker(),
                    "config_propagation": passing_config_propagation(),
                    "q6_workgroup_diagnostics": q6,
                },
            },
        }
        result = self.run_verifier(payload, "--require-q6-workgroup-clear")
        self.assertEqual(result.returncode, 50, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "q6-descriptor-invariant-mismatch")
        self.assertEqual(report["responsibility_boundary"], "q6-descriptor-object-graph")
        self.assertEqual(report["q6_effective_blocker_class"], "descriptor-invariant-mismatch")
        self.assertEqual(
            report["q6_descriptor_invariant_mismatches"][0]["failed_invariant"],
            "offset_equals_memory_plus_api_offset",
        )
        self.assertEqual(
            report["q6_descriptor_invariant_mismatches"][0]["reason"],
            "missing-or-not-true",
        )
        self.assertIsNone(report["q6_descriptor_invariant_mismatches"][0]["value"])
        self.assertFalse(report["correctness_claim_allowed"])
        self.assertFalse(report["benchmark_claim_allowed"])

    def test_q6_oracle_mismatch_blocks_correctness_and_benchmark_claims(self):
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "diagnostics": {
                    "runtime_freshness": runtime_marker(),
                    "config_propagation": passing_config_propagation(),
                    "q6_workgroup_diagnostics": {
                        "workgroup_shape_blocker": False,
                        "latest_status": "mismatch",
                        "blocker_class": "vulkan-device-execution-or-writeback",
                        "q6_shader_like_oracle_cleared": True,
                        **q6_verified_writeback(),
                    },
                },
                "correctness": gpu_correctness_report("pass", required_failures=0, passed=True, content="5"),
            },
            "cpu": {"tokens_per_second": 0.1},
            **speedup_sections(speedup=2.0, target_met=True),
        }
        result = self.run_verifier(payload)
        self.assertEqual(result.returncode, 0, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "q6-workgroup-cleared-but-oracle-mismatch")
        self.assertFalse(report["correctness_claim_allowed"])
        self.assertFalse(report["benchmark_claim_allowed"])
        self.assertIn("vulkan-device-execution-or-writeback", report["next_action"])

    def test_q6_oracle_mismatch_requires_verified_writable_writeback(self):
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "diagnostics": {
                    "runtime_freshness": runtime_marker(),
                    "config_propagation": passing_config_propagation(),
                    "q6_workgroup_diagnostics": {
                        "workgroup_shape_blocker": False,
                        "latest_status": "mismatch",
                        "local_size_resolved": [32, 1, 1],
                        "q6_shader_like_oracle_cleared": True,
                    },
                },
                "correctness": gpu_correctness_report("fail", required_failures=1, passed=False, content="4"),
            },
            "cpu": {"tokens_per_second": 0.1},
            **speedup_sections(speedup=0.5, target_met=False, cpu_tps=0.1, gpu_tps=0.05),
        }
        result = self.run_verifier(payload, "--require-q6-workgroup-clear")
        self.assertEqual(result.returncode, 41, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "q6-writeback-unverified")
        self.assertIn("q6_row_indexed_writeback_evidence", json.dumps(report["q6_writeback_evidence"]["missing"]))
        self.assertFalse(report["correctness_claim_allowed"])
        self.assertFalse(report["benchmark_claim_allowed"])

    def test_q6_oracle_mismatch_fails_closed_when_row_indexed_writeback_differs(self):
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "diagnostics": {
                    "runtime_freshness": runtime_marker(),
                    "config_propagation": passing_config_propagation(),
                    "q6_workgroup_diagnostics": {
                        "workgroup_shape_blocker": False,
                        "latest_status": "mismatch",
                        "q6_shader_like_oracle_cleared": True,
                        **q6_verified_writeback(),
                    },
                },
                "correctness": gpu_correctness_report("fail", required_failures=1, passed=False, content="4"),
            },
            "cpu": {"tokens_per_second": 0.1},
            **speedup_sections(speedup=0.5, target_met=False, cpu_tps=0.1, gpu_tps=0.05),
        }
        q6 = payload["gpu"]["diagnostics"]["q6_workgroup_diagnostics"]
        q6["q6_row_indexed_writeback_evidence"][0]["f32_after_writeback"] = [
            {"index": 257, "value": 9.5}
        ]
        result = self.run_verifier(payload, "--require-q6-workgroup-clear")
        self.assertEqual(result.returncode, 40, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "q6-writeback-mismatch")
        self.assertIn("f32_after_dispatch/f32_after_writeback", json.dumps(report["q6_writeback_evidence"]["mismatches"]))
        self.assertFalse(report["correctness_claim_allowed"])
        self.assertFalse(report["benchmark_claim_allowed"])

    def test_q6_oracle_match_requires_verified_writable_writeback(self):
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "diagnostics": {
                    "runtime_freshness": runtime_marker(),
                    "config_propagation": passing_config_propagation(),
                    "q6_workgroup_diagnostics": {
                        "workgroup_shape_blocker": False,
                        "latest_status": "match",
                        "local_size_resolved": [32, 1, 1],
                        "q6_writeback_verified_all": False,
                        "q6_writable_writeback_unknown": [
                            {"index": 2, "binding": 2, "writeback_verified": None}
                        ],
                    },
                },
                "correctness": gpu_correctness_report(),
            },
            "cpu": {"tokens_per_second": 0.1},
            **speedup_sections(speedup=2.0, target_met=True),
        }
        result = self.run_verifier(payload)
        self.assertEqual(result.returncode, 41, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "q6-writeback-unverified")
        self.assertFalse(report["correctness_claim_allowed"])
        self.assertFalse(report["benchmark_claim_allowed"])
        self.assertIn("writeback", report["next_action"])

    def test_q6_writeback_mismatch_fails_closed_even_if_oracle_matched(self):
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "diagnostics": {
                    "runtime_freshness": runtime_marker(),
                    "config_propagation": passing_config_propagation(),
                    "q6_workgroup_diagnostics": {
                        "workgroup_shape_blocker": False,
                        "latest_status": "match",
                        "local_size_resolved": [32, 1, 1],
                        "q6_writeback_verified_all": False,
                        "q6_writable_writeback_mismatches": [
                            {
                                "index": 2,
                                "binding": 2,
                                "gpu_after_dispatch_hash": "0x1111111111111111",
                                "fd_after_hash": "0x2222222222222222",
                                "writeback_mismatch": True,
                            }
                        ],
                    },
                },
                "correctness": gpu_correctness_report(),
            },
            "cpu": {"tokens_per_second": 0.1},
            **speedup_sections(speedup=2.0, target_met=True),
        }
        result = self.run_verifier(payload)
        self.assertEqual(result.returncode, 40, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "q6-writeback-mismatch")
        self.assertFalse(report["correctness_claim_allowed"])
        self.assertFalse(report["benchmark_claim_allowed"])
        self.assertIn("writeback", report["next_action"])

    def test_q6_match_fails_closed_when_compact_writable_binding_hashes_are_absent(self):
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "diagnostics": {
                    "runtime_freshness": runtime_marker(),
                    "config_propagation": passing_config_propagation(),
                    "q6_workgroup_diagnostics": {
                        "workgroup_shape_blocker": False,
                        "latest_status": "match",
                        "local_size_resolved": [32, 1, 1],
                        "q6_writeback_verified_all": True,
                    },
                },
                "correctness": gpu_correctness_report(),
            },
            "cpu": {"tokens_per_second": 0.1},
            **speedup_sections(speedup=2.0, target_met=True),
        }
        result = self.run_verifier(payload)
        self.assertEqual(result.returncode, 41, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "q6-writeback-unverified")
        self.assertIn("q6_writable_bindings", json.dumps(report["q6_writeback_evidence"]["missing"]))
        self.assertFalse(report["correctness_claim_allowed"])
        self.assertFalse(report["benchmark_claim_allowed"])

    def test_q6_match_fails_closed_when_only_exact_index_f32_samples_lack_row_index_evidence(self):
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "diagnostics": {
                    "runtime_freshness": runtime_marker(),
                    "config_propagation": passing_config_propagation(),
                    "q6_workgroup_diagnostics": {
                        "workgroup_shape_blocker": False,
                        "latest_status": "match",
                        "local_size_resolved": [32, 1, 1],
                        "q6_writeback_verified_all": True,
                        "q6_row_indexed_sample_indices": [257],
                        "q6_writable_bindings": [
                            {
                                "index": 2,
                                "binding": 2,
                                "alias_rep": 2,
                                "offset": 0,
                                "size": 607744,
                                "writable": True,
                                "gpu_after_dispatch_hash": "0x1111111111111111",
                                "fd_after_hash": "0x1111111111111111",
                                "writeback_verified": True,
                                "writeback_mismatch": False,
                                "offset_equals_memory_plus_api_offset": True,
                                "gpu_offset_equals_memory_plus_api_offset": True,
                                "descriptor_offset_equals_api_offset": True,
                                "descriptor_range_matches_api_range": True,
                                "f32_after_dispatch": [{"index": 257, "value": 1.25}],
                                "f32_after_writeback": [{"index": 257, "value": 1.25}],
                            }
                        ],
                    },
                },
                "correctness": gpu_correctness_report(),
            },
            "cpu": {"tokens_per_second": 0.1},
            **speedup_sections(speedup=2.0, target_met=True),
        }
        result = self.run_verifier(payload)
        self.assertEqual(result.returncode, 41, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "q6-writeback-unverified")
        missing = json.dumps(report["q6_writeback_evidence"]["missing"])
        self.assertIn("q6_row_indexed_writeback_evidence", missing)
        self.assertIn("q6_row_indexed_writeback_verified", missing)
        self.assertFalse(report["correctness_claim_allowed"])
        self.assertFalse(report["benchmark_claim_allowed"])

    def test_q6_match_fails_closed_when_compact_before_after_hashes_mismatch(self):
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "diagnostics": {
                    "runtime_freshness": runtime_marker(),
                    "config_propagation": passing_config_propagation(),
                    "q6_workgroup_diagnostics": {
                        "workgroup_shape_blocker": False,
                        "latest_status": "match",
                        **q6_verified_writeback(),
                    },
                },
                "correctness": gpu_correctness_report(),
            },
            "cpu": {"tokens_per_second": 0.1},
            **speedup_sections(speedup=2.0, target_met=True),
        }
        writable = payload["gpu"]["diagnostics"]["q6_workgroup_diagnostics"]["q6_writable_bindings"][0]
        writable["fd_after_hash"] = "0x2222222222222222"
        result = self.run_verifier(payload)
        self.assertEqual(result.returncode, 40, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "q6-writeback-mismatch")
        self.assertIn("0x2222222222222222", json.dumps(report["q6_writeback_evidence"]["mismatches"]))
        self.assertFalse(report["correctness_claim_allowed"])
        self.assertFalse(report["benchmark_claim_allowed"])

    def test_q6_match_fails_closed_when_compact_writeback_hash_is_invalid(self):
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "diagnostics": {
                    "runtime_freshness": runtime_marker(),
                    "config_propagation": passing_config_propagation(),
                    "q6_workgroup_diagnostics": {
                        "workgroup_shape_blocker": False,
                        "latest_status": "match",
                        **q6_verified_writeback(),
                    },
                },
                "correctness": gpu_correctness_report(),
            },
            "cpu": {"tokens_per_second": 0.1},
            **speedup_sections(speedup=2.0, target_met=True),
        }
        writable = payload["gpu"]["diagnostics"]["q6_workgroup_diagnostics"]["q6_writable_bindings"][0]
        writable["gpu_after_dispatch_hash"] = "0x0000000000000000"
        result = self.run_verifier(payload)
        self.assertEqual(result.returncode, 41, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "q6-writeback-unverified")
        self.assertIn("gpu_after_dispatch_hash", json.dumps(report["q6_writeback_evidence"]["missing"]))
        self.assertFalse(report["correctness_claim_allowed"])
        self.assertFalse(report["benchmark_claim_allowed"])


    def test_compare_artifact_without_config_propagation_fails_closed(self):
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "diagnostics": {
                    "runtime_freshness": runtime_marker(),
                    "q6_workgroup_diagnostics": {
                        "workgroup_shape_blocker": False,
                        "latest_status": "match",
                        "q6_writeback_verified_all": True,
                    },
                },
                "correctness": {"summary": {"correctness": "pass"}},
            },
            "cpu": {"tokens_per_second": 0.1},
            "comparison": {"speedup": 2.0, "target_met": True},
        }
        result = self.run_verifier(payload)
        self.assertEqual(result.returncode, 35, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "config-propagation-mismatch")
        self.assertTrue(report["config_propagation_missing"])
        self.assertFalse(report["correctness_claim_allowed"])
        self.assertFalse(report["benchmark_claim_allowed"])


    def test_config_propagation_pass_must_cover_verifier_manifest(self):
        config_propagation = passing_config_propagation()
        omitted = config_propagation["checks"].pop()["env"]
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "diagnostics": {
                    "runtime_freshness": runtime_marker(),
                    "config_propagation": config_propagation,
                    "q6_workgroup_diagnostics": {
                        "workgroup_shape_blocker": False,
                        "latest_status": "match",
                        "q6_writeback_verified_all": True,
                    },
                },
                "correctness": {"summary": {"correctness": "pass"}},
            },
            "cpu": {"tokens_per_second": 0.1},
            "comparison": {"speedup": 2.0, "target_met": True},
        }
        result = self.run_verifier(payload)
        self.assertEqual(result.returncode, 35, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "config-propagation-mismatch")
        self.assertIn(omitted, report["config_propagation_manifest_misses"])
        self.assertFalse(report["correctness_claim_allowed"])
        self.assertFalse(report["benchmark_claim_allowed"])

    def test_requested_env_without_reflection_evidence_fails_closed(self):
        config_propagation = passing_config_propagation()
        config_propagation["summary"] = "fail"
        config_propagation["checks"][0].update(
            {
                "expected": True,
                "observed_values": [],
                "status": "missing-evidence",
            }
        )
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "diagnostics": {
                    "runtime_freshness": runtime_marker(),
                    "config_propagation": config_propagation,
                    "q6_workgroup_diagnostics": {
                        "workgroup_shape_blocker": False,
                        "latest_status": "match",
                        "q6_writeback_verified_all": True,
                    },
                },
                "correctness": {"summary": {"correctness": "pass"}},
            },
            "cpu": {"tokens_per_second": 0.1},
            "comparison": {"speedup": 2.0, "target_met": True},
        }
        result = self.run_verifier(payload)
        self.assertEqual(result.returncode, 35, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "config-propagation-mismatch")
        self.assertFalse(report["correctness_claim_allowed"])
        self.assertFalse(report["benchmark_claim_allowed"])

    def test_requested_env_pass_without_observed_values_fails_closed(self):
        config_propagation = passing_config_propagation()
        config_propagation["checks"][0].update(
            {
                "expected": True,
                "observed_values": [],
                "status": "pass",
            }
        )
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "diagnostics": {
                    "runtime_freshness": runtime_marker(),
                    "config_propagation": config_propagation,
                    "q6_workgroup_diagnostics": {
                        "workgroup_shape_blocker": False,
                        "latest_status": "match",
                        "q6_writeback_verified_all": True,
                    },
                },
                "correctness": {"summary": {"correctness": "pass"}},
            },
            "cpu": {"tokens_per_second": 0.1},
            "comparison": {"speedup": 2.0, "target_met": True},
        }
        result = self.run_verifier(payload)
        self.assertEqual(result.returncode, 35, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "config-propagation-mismatch")
        self.assertFalse(report["correctness_claim_allowed"])
        self.assertFalse(report["benchmark_claim_allowed"])

    def test_q6_workgroup_shape_blocker_fails_hard(self):
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "diagnostics": {
                    "runtime_freshness": runtime_marker(),
                    "config_propagation": passing_config_propagation(),
                    "q6_workgroup_diagnostics": {
                        "workgroup_shape_blocker": True,
                        "latest_status": "mismatch",
                    },
                },
                "correctness": gpu_correctness_report("fail", required_failures=1, passed=False, content="4"),
            },
            **speedup_sections(speedup=0.0, target_met=False, cpu_tps=0.1, gpu_tps=0.0),
        }
        result = self.run_verifier(payload)
        self.assertEqual(result.returncode, 32, result.stdout)
        self.assertIn("q6-workgroup-shape-blocker", result.stdout)

    def test_pre_http_vulkan_pipeline_feature_keeps_first_failure_evidence(self):
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "next_action": "map failed SPIR-V capabilities to Android Vulkan feature bits",
            "gpu": {
                "served": False,
                "diagnostics": {
                    "blocker_class": "vulkan_pipeline_feature",
                    "blocker_detail": "Android Vulkan rejected a ggml generic SPIR-V compute pipeline with VK_ERROR_FEATURE_NOT_PRESENT",
                    "runtime_freshness": runtime_marker(),
                    "config_propagation": passing_config_propagation(),
                    "generic_spirv_dispatch": {
                        "attempted": True,
                        "failed_events": [
                            {
                                "valid": False,
                                "kernel": "generic_spirv",
                                "stage": "vulkan-dispatch",
                                "error": "create-generic-compute-pipeline",
                                "vk_result": -13,
                                "spirv_hash": "0xee4e8d4acf23ec08",
                                "shader_bytes": 18844,
                                "entry": "main",
                                "bindings": 5,
                                "dispatch": [6144, 1, 1],
                                "push_bytes": 128,
                                "requested_feature_mask": "0x0000000000000038",
                                "requested_feature_mask_present": True,
                                "strict_passthrough": True,
                                "spirv_required_feature_mask": "0x0000000000000448",
                                "spirv_requested_feature_missing_mask": "0x0000000000000440",
                                "spirv_requested_feature_mismatches": [
                                    "storageBuffer8BitAccess",
                                    "shaderInt8",
                                ],
                                "pipeline_key": {
                                    "spirv_hash": "0xee4e8d4acf23ec08",
                                    "spec_hash": "0x4256e6bd7dad2e74",
                                    "layout_bindings": 5,
                                    "descriptor_sets": 1,
                                    "push_bytes": 128,
                                },
                                "spirv_feature_requirements": {
                                    "int8": True,
                                    "storage16": True,
                                    "storage8": True,
                                },
                                "spirv_feature_mismatch": False,
                                "spirv_feature_mismatches": [],
                                "android_vulkan_features": {
                                    "shaderInt8": 1,
                                    "storageBuffer16BitAccess": 1,
                                    "storageBuffer8BitAccess": 1,
                                },
                                "android_vulkan_enabled_features": {
                                    "shaderInt8": 1,
                                    "storageBuffer16BitAccess": 1,
                                    "storageBuffer8BitAccess": 1,
                                    "extension_count": 4,
                                    "chain_compat_feature_structs": 1,
                                },
                                "spirv_capabilities": [1, 39, 4433, 4448],
                            }
                        ],
                        "llama_throw": "vk::Queue::submit: ErrorFeatureNotPresent",
                    },
                    "q6_workgroup_diagnostics": {
                        "event_count": 0,
                        "blocker_class": "not-reached",
                        "diagnostic_interpretation": "no-q6-oracle-event",
                    },
                },
                "correctness": gpu_correctness_report("fail", required_failures=1, passed=False, content="4"),
            },
            **speedup_sections(speedup=0.0, target_met=False, cpu_tps=0.1, gpu_tps=0.0),
        }
        result = self.run_verifier(payload)
        self.assertEqual(result.returncode, 0, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "vulkan-pipeline-feature")
        evidence = report["pre_http_failure_evidence"]
        self.assertTrue(evidence["generic_spirv_attempted"])
        self.assertEqual(evidence["failed_event_count"], 1)
        self.assertEqual(evidence["failure_event"]["error"], "create-generic-compute-pipeline")
        self.assertEqual(evidence["failure_event"]["vk_result"], -13)
        self.assertEqual(evidence["failure_event"]["spirv_hash"], "0xee4e8d4acf23ec08")
        self.assertEqual(evidence["failure_event"]["spirv_required_feature_mask"], "0x0000000000000448")
        self.assertEqual(evidence["failure_event"]["spirv_requested_feature_missing_mask"], "0x0000000000000440")
        self.assertEqual(evidence["failure_event"]["spirv_requested_feature_mismatches"], [
            "storageBuffer8BitAccess",
            "shaderInt8",
        ])
        self.assertEqual(evidence["failure_event"]["android_vulkan_enabled_features"]["shaderInt8"], 1)
        self.assertEqual(evidence["pipeline_key"]["spec_hash"], "0x4256e6bd7dad2e74")
        self.assertEqual(evidence["q6_reachability"]["blocker_class"], "not-reached")
        self.assertEqual(evidence["q6_reachability"]["event_count"], 0)

    def test_legacy_pre_http_pipeline_feature_artifacts_do_not_require_enabled_features(self):
        legacy_runtime = runtime_marker()
        legacy_runtime["expected_icd_marker"] = "vulkan-icd-runtime-marker-20260510"
        legacy_runtime["observed_icd_markers"] = ["vulkan-icd-runtime-marker-20260510"]
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "served": False,
                "diagnostics": {
                    "blocker_class": "vulkan_pipeline_feature",
                    "blocker_detail": "Android Vulkan rejected a ggml generic SPIR-V compute pipeline with VK_ERROR_FEATURE_NOT_PRESENT",
                    "runtime_freshness": legacy_runtime,
                    "config_propagation": passing_config_propagation(),
                    "generic_spirv_dispatch": {
                        "attempted": True,
                        "failed_events": [
                            {
                                "error": "create-generic-compute-pipeline",
                                "vk_result": -13,
                                "spirv_hash": "0xlegacy",
                                "android_vulkan_features": {"shaderInt8": 1},
                            }
                        ],
                    },
                    "q6_workgroup_diagnostics": {"event_count": 0, "blocker_class": "not-reached"},
                },
            },
        }
        result = self.run_verifier(payload)
        self.assertEqual(result.returncode, 0, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "vulkan-pipeline-feature")
        failure_event = report["pre_http_failure_evidence"]["failure_event"]
        self.assertEqual(failure_event["android_vulkan_features"]["shaderInt8"], 1)
        self.assertNotIn("android_vulkan_enabled_features", failure_event)

    def test_fresh_pre_http_pipeline_feature_requires_feature_evidence(self):
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "served": False,
                "diagnostics": {
                    "blocker_class": "vulkan_pipeline_feature",
                    "blocker_detail": "Android Vulkan rejected a ggml generic SPIR-V compute pipeline with VK_ERROR_FEATURE_NOT_PRESENT",
                    "runtime_freshness": runtime_marker(),
                    "config_propagation": passing_config_propagation(),
                    "generic_spirv_dispatch": {
                        "attempted": True,
                        "failed_events": [
                            {
                                "error": "create-generic-compute-pipeline",
                                "vk_result": -13,
                                "spirv_hash": "0xfresh-missing",
                                "android_vulkan_features": {"shaderInt8": 1},
                            }
                        ],
                    },
                    "q6_workgroup_diagnostics": {"event_count": 0, "blocker_class": "not-reached"},
                },
            },
        }
        result = self.run_verifier(payload)
        self.assertEqual(result.returncode, 43, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "vulkan-pipeline-feature-evidence-missing")
        self.assertIn("android_vulkan_enabled_features", report["missing_pre_http_feature_evidence"])
        self.assertIn("spirv_requested_feature_missing_mask", report["missing_pre_http_feature_evidence"])

    def test_pre_http_failure_evidence_uses_first_failed_event(self):
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "served": False,
                "diagnostics": {
                    "blocker_class": "vulkan_pipeline_feature",
                    "blocker_detail": "Android Vulkan rejected a ggml generic SPIR-V compute pipeline with VK_ERROR_FEATURE_NOT_PRESENT",
                    "runtime_freshness": runtime_marker(),
                    "config_propagation": passing_config_propagation(),
                    "generic_spirv_dispatch": {
                        "attempted": True,
                        "failed_events": [
                            {
                                "error": "create-generic-compute-pipeline",
                                "vk_result": -13,
                                "spirv_hash": "0xfirst",
                                "spirv_required_feature_mask": "0x0000000000000448",
                                "spirv_requested_feature_missing_mask": "0x0000000000000440",
                                "spirv_requested_feature_mismatches": ["storageBuffer8BitAccess"],
                                "android_vulkan_features": {"shaderInt8": 1},
                                "android_vulkan_enabled_features": {"shaderInt8": 1},
                            },
                            {
                                "error": "secondary-cleanup-failure",
                                "vk_result": -1,
                                "spirv_hash": "0xsecond",
                                "spirv_required_feature_mask": "0x0000000000000000",
                                "spirv_requested_feature_missing_mask": "0x0000000000000000",
                                "spirv_requested_feature_mismatches": [],
                                "android_vulkan_features": {},
                                "android_vulkan_enabled_features": {},
                            },
                        ],
                    },
                    "q6_workgroup_diagnostics": {"event_count": 0, "blocker_class": "not-reached"},
                },
            },
        }
        result = self.run_verifier(payload)
        self.assertEqual(result.returncode, 0, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(
            report["pre_http_failure_evidence"]["failure_event"]["spirv_hash"],
            "0xfirst",
        )
        self.assertEqual(report["pre_http_failure_evidence"]["failed_event_count"], 2)

    def test_pre_http_pipeline_feature_requires_fresh_icd_marker(self):
        stale_runtime = runtime_marker()
        stale_runtime["observed_icd_markers"] = ["vulkan-icd-runtime-marker-20260510"]
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "served": False,
                "diagnostics": {
                    "blocker_class": "vulkan_pipeline_feature",
                    "blocker_detail": "Android Vulkan rejected a ggml generic SPIR-V compute pipeline with VK_ERROR_FEATURE_NOT_PRESENT",
                    "runtime_freshness": stale_runtime,
                    "config_propagation": passing_config_propagation(),
                    "generic_spirv_dispatch": {
                        "attempted": True,
                        "failed_events": [
                            {
                                "error": "create-generic-compute-pipeline",
                                "vk_result": -13,
                                "spirv_hash": "0xee4e8d4acf23ec08",
                            }
                        ],
                    },
                    "q6_workgroup_diagnostics": {"event_count": 0, "blocker_class": "not-reached"},
                },
            },
        }
        result = self.run_verifier(payload)
        self.assertEqual(result.returncode, 42, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "icd-marker-not-observed")
        self.assertEqual(report["responsibility_boundary"], "runtime-freshness")
        self.assertNotIn("pre_http_failure_evidence", report)

    def test_structured_unsupported_executor_oracle_evidence_fails_closed(self):
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "diagnostics": {
                    "runtime_freshness": runtime_marker(),
                    "config_propagation": passing_config_propagation(),
                    "generic_spirv_dispatch": {
                        "valid_android_vulkan_events": [
                            {
                                "valid": True,
                                "kernel": "generic_spirv",
                                "cpu_oracle": {
                                    "requested": True,
                                    "status": "unsupported-q6k-layout",
                                    "kernel_hint": "mul-mat-vec-q6-k-large",
                                },
                            }
                        ],
                    },
                    "q6_workgroup_diagnostics": {
                        "workgroup_shape_blocker": False,
                        "latest_status": "match",
                        "q6_writeback_verified_all": True,
                    },
                },
                "correctness": {"summary": {"correctness": "pass"}},
            },
            "cpu": {"tokens_per_second": 0.1},
            "comparison": {"speedup": 2.0, "target_met": True},
        }
        result = self.run_verifier(payload)
        self.assertEqual(result.returncode, 36, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "unsupported-gpu-work-accepted")
        self.assertFalse(report["correctness_claim_allowed"])
        self.assertFalse(report["benchmark_claim_allowed"])
        self.assertIn("unsupported-q6k-layout", json.dumps(report["unsupported_gpu_work_evidence"]))

    def test_structured_not_implemented_executor_evidence_fails_closed(self):
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "diagnostics": {
                    "runtime_freshness": runtime_marker(),
                    "config_propagation": passing_config_propagation(),
                    "generic_spirv_dispatch": {
                        "valid_android_vulkan_events": [
                            {
                                "valid": True,
                                "latest_status": "kernel-not-implemented-yet",
                                "kernel": "generic_spirv",
                            }
                        ],
                    },
                    "q6_workgroup_diagnostics": {
                        "workgroup_shape_blocker": False,
                        "latest_status": "match",
                        "q6_writeback_verified_all": True,
                    },
                },
                "correctness": {"summary": {"correctness": "pass"}},
            },
            "cpu": {"tokens_per_second": 0.1},
            "comparison": {"speedup": 2.0, "target_met": True},
        }
        result = self.run_verifier(payload)
        self.assertEqual(result.returncode, 36, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "unsupported-gpu-work-accepted")
        self.assertIn("kernel-not-implemented-yet", json.dumps(report["unsupported_gpu_work_evidence"]))


    def test_oracle_fail_closed_evidence_fails_closed(self):
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "diagnostics": {
                    "runtime_freshness": runtime_marker(),
                    "config_propagation": passing_config_propagation(),
                    "generic_spirv_dispatch": {
                        "failed_events": [
                            {
                                "valid": False,
                                "stage": "cpu-oracle-required",
                                "oracle_fail_closed": True,
                                "cpu_oracle": {"status": "fused-rms-rope-oracle-pending"},
                            }
                        ]
                    },
                    "q6_workgroup_diagnostics": {
                        "workgroup_shape_blocker": False,
                        "latest_status": "match",
                        "q6_writeback_verified_all": True,
                    },
                },
                "correctness": gpu_correctness_report(),
            },
            "cpu": {"tokens_per_second": 0.1},
            **speedup_sections(),
        }
        result = self.run_verifier(payload)
        self.assertEqual(result.returncode, 37, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "oracle-fail-closed")
        self.assertFalse(report["correctness_claim_allowed"])
        self.assertFalse(report["benchmark_claim_allowed"])
        self.assertIn("oracle_fail_closed", json.dumps(report["oracle_fail_closed_evidence"]))

    def test_compare_artifact_without_api_prompt_sanity_fails_closed(self):
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "diagnostics": {
                    "runtime_freshness": runtime_marker(),
                    "config_propagation": passing_config_propagation(),
                    "q6_workgroup_diagnostics": {
                        "workgroup_shape_blocker": False,
                        "latest_status": "match",
                        "q6_writeback_verified_all": True,
                    },
                },
                "correctness": {"summary": {"correctness": "pass"}},
            },
            "cpu": {"tokens_per_second": 0.1},
            **speedup_sections(),
        }
        result = self.run_verifier(payload)
        self.assertEqual(result.returncode, 38, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "api-prompt-sanity-missing")
        self.assertIn("gpu.correctness.schema", report["api_prompt_sanity"]["missing"])
        self.assertFalse(report["correctness_claim_allowed"])
        self.assertFalse(report["benchmark_claim_allowed"])

    def test_compare_artifact_without_speedup_fields_fails_closed(self):
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "diagnostics": {
                    "runtime_freshness": runtime_marker(),
                    "config_propagation": passing_config_propagation(),
                    "q6_workgroup_diagnostics": {
                        "workgroup_shape_blocker": False,
                        "latest_status": "match",
                        "q6_writeback_verified_all": True,
                    },
                },
                "correctness": gpu_correctness_report(),
            },
            "cpu": {"tokens_per_second": 0.1},
            "comparison": {"speedup": 2.0},
        }
        result = self.run_verifier(payload)
        self.assertEqual(result.returncode, 39, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["classification"], "speedup-fields-missing")
        self.assertIn("comparison.target_tokens_per_second", report["speedup_fields"]["missing"])
        self.assertIn("bridge_overhead_phase", report["speedup_fields"]["missing"])
        self.assertFalse(report["correctness_claim_allowed"])
        self.assertFalse(report["benchmark_claim_allowed"])


if __name__ == "__main__":
    unittest.main()

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
        "expected_executor_marker": "gpu-executor-enabled-features-20260518",
        "observed_executor_markers": ["gpu-executor-enabled-features-20260518"],
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
            }
        ],
        "q6_writable_writeback_mismatches": [],
        "q6_writable_writeback_unknown": [],
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
            any("pdocker-owned" in action and "stale llama" in action for action in report["device_actions"]),
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
            any("/containers/pdocker-llama-cpp/stop" in command for command in report["cleanup_commands"]),
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
                        "expected_executor_marker": "gpu-executor-enabled-features-20260518",
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

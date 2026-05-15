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
        "expected_executor_marker": "gpu-executor-workgroup3d-20260513",
        "observed_executor_markers": ["gpu-executor-workgroup3d-20260513"],
        "observed_icd_markers": ["vulkan-icd-runtime-marker-20260510"],
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
            "memory": {"mem_available_mb": 100, "swap_free_mb": 1},
            "device_actions": ["wait"],
        }
        blocked = self.run_verifier(payload)
        self.assertEqual(blocked.returncode, 20, blocked.stdout)
        report = json.loads(blocked.stdout)
        self.assertEqual(report["classification"], "insufficient_memory")
        self.assertFalse(report["benchmark_claim_allowed"])
        allowed = self.run_verifier(payload, "--allow-memory-blocker")
        self.assertEqual(allowed.returncode, 0, allowed.stdout)

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

    def test_missing_executor_marker_blocks_compare_and_benchmark_claims(self):
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "diagnostics": {
                    "runtime_freshness": {
                        "summary": "fail",
                        "expected_executor_marker": "gpu-executor-workgroup3d-20260513",
                        "observed_executor_markers": [],
                        "observed_icd_markers": ["vulkan-icd-runtime-marker-20260510"],
                    },
                    "q6_workgroup_diagnostics": {
                        "workgroup_shape_blocker": False,
                        "latest_status": "match",
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
                    },
                },
                "correctness": {"summary": {"correctness": "pass"}},
            },
            "comparison": {"speedup": 2.0, "target_met": True},
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
                    },
                },
                "correctness": {"summary": {"correctness": "fail"}},
            },
            "comparison": {"speedup": 0.5, "target_met": False},
        }
        clear = self.run_verifier(payload, "--require-q6-workgroup-clear")
        self.assertEqual(clear.returncode, 0, clear.stdout)
        report = json.loads(clear.stdout)
        self.assertEqual(report["classification"], "q6-workgroup-cleared-but-oracle-mismatch")
        exact = self.run_verifier(payload, "--require-q6-match")
        self.assertEqual(exact.returncode, 30, exact.stdout)


    def test_compare_artifact_without_config_propagation_fails_closed(self):
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "diagnostics": {
                    "runtime_freshness": runtime_marker(),
                    "q6_workgroup_diagnostics": {
                        "workgroup_shape_blocker": False,
                        "latest_status": "match",
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
                "correctness": {"summary": {"correctness": "fail"}},
            },
            "comparison": {},
        }
        result = self.run_verifier(payload)
        self.assertEqual(result.returncode, 32, result.stdout)
        self.assertIn("q6-workgroup-shape-blocker", result.stdout)

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


if __name__ == "__main__":
    unittest.main()

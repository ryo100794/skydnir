import json
import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SWEEP = ROOT / "scripts" / "maintenance" / "summarize-llama-gpu-artifacts.py"
LATEST = ROOT / "docs" / "test" / "llama-gpu-artifact-sweep-latest.json"
VERIFIER = ROOT / "scripts" / "verify-llama-gpu-artifact.py"


def passing_config_propagation():
    spec = importlib.util.spec_from_file_location("llama_gpu_artifact_verifier", VERIFIER)
    verifier = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(verifier)
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


def q6_verified_writeback(hash_value="0x1111111111111111"):
    return {
        "local_size_resolved": [32, 2, 1],
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


class LlamaGpuArtifactSweepTest(unittest.TestCase):
    def test_sweep_handles_memory_blocker_and_non_object_artifact(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            memory = root / "memory.json"
            memory.write_text(
                json.dumps(
                    {
                        "error": "insufficient_memory",
                        "next_blocker": "recover Android memory and rerun unchanged",
                        "memory": {"mem_available_mb": 64, "swap_free_mb": 128},
                        "required": {"mem_preflight_free_mb": 4096},
                    }
                ),
                encoding="utf-8",
            )
            non_object = root / "executor-events.json"
            non_object.write_text(json.dumps([{"executor": "pdocker-gpu-executor"}]), encoding="utf-8")

            result = subprocess.run(
                [
                    str(SWEEP),
                    "--snapshot-date",
                    "2026-05-17",
                    str(memory),
                    str(non_object),
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["schema"], "pdocker.llama.gpu.artifact-sweep.v1")
            self.assertEqual(report["artifact_count"], 2)
            self.assertEqual(report["classification_counts"]["insufficient_memory"], 1)
            self.assertEqual(report["classification_counts"]["invalid-root"], 1)
        self.assertIn("row-indexed Q6_K", "\n".join(report["next_device_run_checklist"]))

    def test_sweep_uses_verifier_effective_q6_blocker_over_raw_artifact_hint(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            artifact = root / "q6-match.json"
            artifact.write_text(
                json.dumps(
                    {
                        "schema": "pdocker.llama.gpu.compare.v1",
                        "gpu": {
                            "diagnostics": {
                                "runtime_freshness": {
                                    "summary": "pass",
                                    "expected_executor_marker": "gpu-executor-float16-cap-diagnostic-20260520",
                                    "observed_executor_markers": ["gpu-executor-float16-cap-diagnostic-20260520"],
                                    "expected_icd_marker": "vulkan-icd-feature-chain-marker-20260518",
                                    "observed_icd_markers": ["vulkan-icd-feature-chain-marker-20260518"],
                                    "executor_event_count": 1,
                                },
                                "config_propagation": passing_config_propagation(),
                                "q6_workgroup_diagnostics": {
                                    "event_count": 1,
                                    "latest_status": "match",
                                    "blocker_class": "stale-workgroup-shape",
                                    "q6k_safe_kernel": True,
                                    "local_size_resolved": [1, 1, 1],
                                    "q6_writeback_verified_all": True,
                                    "q6_row_indexed_sample_indices": [0],
                                    "q6_row_indexed_writeback_verified": True,
                                    "q6_row_indexed_writeback_evidence": [
                                        {
                                            "index": 2,
                                            "binding": 2,
                                            "writable": True,
                                            "q6_row_indexed": True,
                                            "q6_sample_indices": [0],
                                            "f32_after_dispatch": [{"index": 0, "value": 1.0}],
                                            "f32_after_writeback": [{"index": 0, "value": 1.0}],
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
                                            "gpu_after_dispatch_hash": "0x1111111111111111",
                                            "fd_after_hash": "0x1111111111111111",
                                            "writeback_verified": True,
                                            "writeback_mismatch": False,
                                        }
                                    ],
                                    "q6_row_indexed_sample_indices": [257],
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
                                    "q6_writable_writeback_mismatches": [],
                                    "q6_writable_writeback_unknown": [],
                                },
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            result = subprocess.run(
                [str(SWEEP), str(artifact)],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            entry = json.loads(result.stdout)["artifacts"][0]
            self.assertEqual(entry["classification"], "q6-workgroup-cleared-and-oracle-match")
            self.assertEqual(entry["q6_blocker_class"], "cleared")
            self.assertEqual(entry["q6_raw_blocker_class"], "stale-workgroup-shape")

    def test_sweep_surfaces_q6_probe_details(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            artifact = root / "q6-final-store.json"
            artifact.write_text(
                json.dumps(
                    {
                        "schema": "pdocker.llama.gpu.compare.v1",
                        "gpu": {
                            "served": False,
                            "diagnostics": {
                                "runtime_freshness": {
                                    "summary": "pass",
                                    "expected_executor_marker": "gpu-executor-float16-cap-diagnostic-20260520",
                                    "observed_executor_markers": ["gpu-executor-float16-cap-diagnostic-20260520"],
                                    "expected_icd_marker": "vulkan-icd-feature-chain-marker-20260518",
                                    "observed_icd_markers": ["vulkan-icd-feature-chain-marker-20260518"],
                                    "executor_event_count": 1,
                                },
                                "config_propagation": passing_config_propagation(),
                                "q6_workgroup_diagnostics": {
                                    "event_count": 1,
                                    "workgroup_shape_blocker": False,
                                    "latest_status": "mismatch",
                                    **q6_verified_writeback(),
                                    "q6_shader_like_abs_delta": 1.0e-7,
                                    "q6_native_reduction_tree_abs_delta": 7.0e-7,
                                    "q6_native_reduction_tree_gpu_abs_error": 7.04,
                                    "q6_native_reduction_tree_sum": 13.87,
                                    "q6_output_layout_fixed_offset_rejected": True,
                                    "q6_output_layout_probe": {
                                        "summary": "canonical-mismatch-inconclusive",
                                        "mismatch_count": 32,
                                        "found_elsewhere_count": 3,
                                        "consistent_relative_offset": False,
                                        "samples": [
                                            {
                                                "dst_index": 0,
                                                "expected": 13.87,
                                                "gpu_at_dst": 6.83,
                                                "canonical_match": False,
                                                "found_elsewhere": False,
                                            }
                                        ],
                                    },
                                    "q6_row_provenance_probe": {
                                        "summary": "not-row-provenance",
                                        "same_row_match_count": 0,
                                        "other_row_match_count": 0,
                                        "mismatch_count": 32,
                                    },
                                    "q6_partial_signature_probe": {
                                        "summary": "not-partial",
                                        "mismatch_count": 32,
                                    },
                                },
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            result = subprocess.run(
                [str(SWEEP), str(artifact)],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            entry = json.loads(result.stdout)["artifacts"][0]
            self.assertEqual(entry["classification"], "q6-native-device-execution-or-final-store")
            self.assertEqual(entry["q6_output_layout_probe_summary"], "canonical-mismatch-inconclusive")
            self.assertTrue(entry["q6_output_layout_fixed_offset_rejected"])
            self.assertEqual(entry["q6_row_provenance_probe_summary"], "not-row-provenance")
            self.assertEqual(entry["q6_partial_signature_probe_summary"], "not-partial")
            self.assertEqual(entry["q6_native_reduction_tree_gpu_abs_error"], 7.04)

    def test_committed_sweep_records_current_blocker_inventory(self):
        report = json.loads(LATEST.read_text(encoding="utf-8"))
        self.assertEqual(report["schema"], "pdocker.llama.gpu.artifact-sweep.v1")
        self.assertGreater(report["artifact_count"], 0)
        self.assertIn("classification_counts", report)
        self.assertIn("q6_classification_counts", report)
        self.assertIn("next_device_run_checklist", report)
        paths = {entry["path"] for entry in report["artifacts"]}
        self.assertNotIn("docs/test/llama-gpu-artifact-sweep-latest.json", paths)
        checklist = "\n".join(report["next_device_run_checklist"])
        self.assertIn("config_propagation.summary == pass", checklist)
        self.assertIn("q6_writeback_verified_all", checklist)
        q6_entries = [entry for entry in report["artifacts"] if entry.get("q6_blocker_class")]
        self.assertTrue(
            any(entry.get("q6_output_layout_probe_summary") for entry in q6_entries),
            "committed sweep should expose Q6 output-layout probe summaries",
        )


if __name__ == "__main__":
    unittest.main()

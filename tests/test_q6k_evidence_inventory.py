import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "maintenance" / "summarize-q6k-evidence.py"


def load_module():
    spec = importlib.util.spec_from_file_location("q6k_evidence_inventory", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class Q6kEvidenceInventoryTest(unittest.TestCase):
    def test_inventory_extracts_q6_event_and_next_tasks(self):
        module = load_module()
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "diagnostics": {
                    "q6_workgroup_diagnostics": {
                        "latest_status": "mismatch",
                        "blocker_class": "native-q6-device-execution-or-final-store",
                        "q6_dispatch_seen": True,
                        "q6_writeback_verified_all": True,
                    }
                },
                "events": [
                    {
                        "kernel": "generic_spirv",
                        "source_spirv_hash": module.Q6_NATIVE_HASH,
                        "effective_spirv_hash": module.Q6_NATIVE_HASH,
                        "dispatch": [1, 1, 1],
                        "push_u32": list(range(32)),
                        "specialization_entries": [
                            {"constant_id": 0, "value_u64": 32},
                            {"constant_id": 1, "value_u64": 2},
                        ],
                        "binding_details": [
                            {
                                "index": 2,
                                "binding": 2,
                                "offset": 16384,
                                "size": 607744,
                                "readable": True,
                                "writable": True,
                                "writeback_verified": True,
                            }
                        ],
                        "cpu_oracle": {
                            "status": "mismatch",
                            "kernel_hint": "mul-mat-vec-q6-k-large",
                            "mismatch_count": 32,
                            "first_mismatch": {"dst_index": 0, "expected": 13.0, "gpu": 6.0},
                        },
                    }
                ],
            },
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "artifact.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            summary = module.summarize([path])

        self.assertEqual(summary["q6_artifact_count"], 1)
        self.assertEqual(summary["artifacts"][0]["q6_event_count"], 1)
        event = summary["artifacts"][0]["q6_events"][0]
        self.assertEqual(event["source_spirv_hash"], module.Q6_NATIVE_HASH)
        self.assertEqual(event["cpu_oracle"]["status"], "mismatch")
        self.assertEqual(event["push_u32_prefix"][-1]["truncated_count"], 16)
        task_ids = {task["id"] for task in summary["next_task_queue"]}
        self.assertIn("q6k-native-mismatch-classify", task_ids)

    def test_safe_only_inventory_blocks_native_compare(self):
        module = load_module()
        payload = {
            "gpu": {
                "events": [
                    {
                        "source_spirv_hash": module.Q6_NATIVE_HASH,
                        "effective_spirv_hash": module.Q6_SAFE_HASH,
                        "q6k_safe_kernel": True,
                        "cpu_oracle": {"status": "match", "kernel_hint": "mul-mat-vec-q6-k-large"},
                    }
                ]
            }
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "safe.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            artifact_summary = module.artifact_summary(path, {module.Q6_NATIVE_HASH, module.Q6_SAFE_HASH})
            tasks = module.derive_next_tasks([artifact_summary], [])

        task_by_id = {task["id"]: task for task in tasks}
        self.assertEqual(task_by_id["q6k-safe-vs-native-static-compare-blocked"]["status"], "blocked")
        self.assertEqual(
            task_by_id["q6k-safe-vs-native-static-compare-blocked"]["blocked_by"],
            "q6k-native-spv-dump",
        )


if __name__ == "__main__":
    unittest.main()

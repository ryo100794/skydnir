import json
import os
import platform
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIRECT = ROOT / "app" / "src" / "main" / "jniLibs" / "arm64-v8a" / "libpdockerdirect.so"


class MemoryTelemetryRuntimeTest(unittest.TestCase):
    def setUp(self):
        if platform.machine() not in {"aarch64", "arm64"}:
            self.skipTest("pdocker-direct runtime smoke requires an arm64 host")
        if not DIRECT.exists() or not os.access(DIRECT, os.X_OK):
            self.skipTest("pdocker-direct binary is not built")

    def test_bounded_ring_drops_oldest_complete_and_partial_records(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ring = root / "memory-ring.jsonl"
            summary = root / "memory-summary.json"
            ring.write_text(
                "".join(
                    json.dumps({
                        "ring_schema": "pdocker.memory-telemetry-ring.v1",
                        "sample_seq": i,
                        "payload": "x" * 64,
                    }) + "\n"
                    for i in range(6)
                )
                + '{"partial": true',
                encoding="utf-8",
            )
            env = os.environ.copy()
            env.update({
                "PDOCKER_MEMORY_TELEMETRY_PATH": str(ring),
                "PDOCKER_MEMORY_SUMMARY_PATH": str(summary),
                "PDOCKER_MEMORY_TELEMETRY_OPERATION_ID": "runtime-ring-test",
                "PDOCKER_MEMORY_TELEMETRY_CONTAINER_ID": "runtime-ring-container",
                "PDOCKER_MEMORY_TELEMETRY_MAX_LINES": "3",
                "PDOCKER_MEMORY_TELEMETRY_MAX_BYTES": "2048",
                "PDOCKER_MEMORY_TELEMETRY_MAX_LINE_BYTES": "1024",
                "PDOCKER_MEMORY_PAGER_POC_PAGES": "4",
                "PDOCKER_MEMORY_PAGER_POC_RESIDENT_PAGES": "2",
            })
            run = subprocess.run(
                [str(DIRECT), "--pdocker-memory-pager-managed-poc"],
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
            )
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            rows = [json.loads(line) for line in ring.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertLessEqual(len(rows), 3)
            self.assertEqual(rows[-1]["ring_schema"], "pdocker.memory-telemetry-ring.v1")
            self.assertEqual(rows[-1]["operation_id"], "runtime-ring-test")
            self.assertEqual(rows[-1]["container_id"], "runtime-ring-container")
            self.assertNotIn("partial", json.dumps(rows, sort_keys=True))
            doc = json.loads(summary.read_text(encoding="utf-8"))
            self.assertEqual(doc["summary_schema"], "pdocker.memory-telemetry-summary.v1")
            self.assertTrue(doc["ring_truncated"])
            self.assertFalse(doc["telemetry_persistence_failed"])


if __name__ == "__main__":
    unittest.main()

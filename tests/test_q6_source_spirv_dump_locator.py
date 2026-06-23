import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCATOR = ROOT / "scripts" / "locate-q6-source-spirv-dump.py"


def load_locator():
    spec = importlib.util.spec_from_file_location("q6_source_spirv_dump_locator", LOCATOR)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class Q6SourceSpirvDumpLocatorTest(unittest.TestCase):
    def setUp(self):
        self.locator = load_locator()

    def write_dump(self, dump_dir: Path, phase="original", dispatch=7, data=None, meta_hash=None):
        if data is None:
            data = self.locator.SPIRV_MAGIC_LE + b"\x01\x00\x00\x00" * 7
        hash_value = self.locator.hex64(data)
        spv = dump_dir / f"pdocker-spirv-{phase}-{dispatch}-0x{hash_value[2:]}.spv"
        spv.write_bytes(data)
        meta = spv.with_suffix(".json")
        meta.write_text(
            json.dumps(
                {
                    "schema": "pdocker.spirv.dump.v1",
                    "phase": phase,
                    "dispatch_id": dispatch,
                    "spirv_hash": meta_hash or hash_value,
                    "shader_bytes": len(data),
                    "valid": True,
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return spv, hash_value

    def run_locator(self, *args):
        return subprocess.run(
            ["python3", str(LOCATOR), *map(str, args)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def test_selects_original_dump_from_artifact_q6_native_split(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            dump_dir = tmp_path / "dumps"
            dump_dir.mkdir()
            spv, hash_value = self.write_dump(dump_dir, dispatch=12)
            artifact = tmp_path / "artifact.json"
            artifact.write_text(
                json.dumps(
                    {
                        "gpu": {
                            "diagnostics": {
                                "q6_workgroup_diagnostics": {
                                    "q6_native_vs_writeback_split": {
                                        "samples": [{"source_spirv_hash": hash_value}]
                                    }
                                }
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            result = self.run_locator(
                "--dump-dir",
                dump_dir,
                "--artifact",
                artifact,
                "--prepare-out-dir",
                tmp_path / "probe",
                "--probe-writes",
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
        self.assertTrue(payload["valid"])
        self.assertEqual(hash_value, payload["target_hash"])
        self.assertEqual(str(spv), payload["selected"]["spv"])
        self.assertEqual("original", payload["selected"]["phase"])
        self.assertIn("--expected-hash", payload["prepare_args"])
        self.assertIn(hash_value, payload["prepare_args"])
        self.assertEqual(["--probe-source-spv", str(spv), "--probe-source-hash", hash_value], payload["runner_args"])

    def test_rejects_effective_only_without_explicit_allow(self):
        with tempfile.TemporaryDirectory() as tmp:
            dump_dir = Path(tmp) / "dumps"
            dump_dir.mkdir()
            _spv, hash_value = self.write_dump(dump_dir, phase="effective")
            result = self.run_locator("--dump-dir", dump_dir, "--hash", hash_value)
            self.assertEqual(1, result.returncode, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
        self.assertFalse(payload["valid"])
        self.assertEqual("no-matching-original-dump", payload["reason"])
        self.assertEqual(0, payload["candidate_count"])

    def test_rejects_meta_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            dump_dir = Path(tmp) / "dumps"
            dump_dir.mkdir()
            _spv, hash_value = self.write_dump(dump_dir, meta_hash="0x0000000000000001")
            result = self.run_locator("--dump-dir", dump_dir, "--hash", hash_value)
            self.assertEqual(1, result.returncode, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
        self.assertFalse(payload["valid"])
        self.assertIn("meta-hash-mismatch", json.dumps(payload["rejected_sample"]))

    def test_explicit_hash_overrides_ambiguous_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            dump_dir = tmp_path / "dumps"
            dump_dir.mkdir()
            _spv, hash_value = self.write_dump(dump_dir)
            artifact = tmp_path / "artifact.json"
            artifact.write_text(
                json.dumps(
                    {
                        "gpu": {
                            "diagnostics": {
                                "q6_workgroup_diagnostics": {
                                    "q6_native_vs_writeback_split": {
                                        "samples": [
                                            {"source_spirv_hash": "0x0000000000000001"},
                                            {"source_spirv_hash": "0x0000000000000002"},
                                        ]
                                    }
                                }
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            result = self.run_locator("--dump-dir", dump_dir, "--artifact", artifact, "--hash", hash_value)
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
        self.assertEqual("--hash", payload["target_hash_source"])
        self.assertEqual(hash_value, payload["target_hash"])
        self.assertTrue(payload["valid"])


if __name__ == "__main__":
    unittest.main()

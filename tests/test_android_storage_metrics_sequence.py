import json
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "android-storage-metrics-sequence.sh"
DOC = ROOT / "docs" / "test" / "STORAGE_METRICS_SEQUENCE_RUNNER.md"


class AndroidStorageMetricsSequenceRunnerTest(unittest.TestCase):
    def test_runner_is_executable_non_destructive_and_documents_planned_phases(self):
        mode = SCRIPT.stat().st_mode
        self.assertTrue(mode & stat.S_IXUSR, "storage metrics sequence runner must be executable")
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("rm -f \"$OUT\"", source)
        self.assertIn("verify-storage-metrics.py", source)
        self.assertIn("--capture-device", source)
        self.assertIn("--sequence", source)
        self.assertIn("adb executable is required", source)
        self.assertIn("Android package is required", source)
        self.assertIn('"success": False', source)
        for phase in ["after-build", "after-rebuild", "after-edit", "after-prune"]:
            self.assertIn(phase, source)
        for forbidden in ["docker build", "docker prune", "containers/prune", "images/prune"]:
            self.assertNotIn(forbidden, source)

    def test_no_stale_file_can_pass_when_capture_fails(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            out = tmp / "storage-sequence.json"
            workdir = tmp / "work"
            fake_adb = self._write_fake_adb(tmp)
            log = tmp / "verify.log"
            fake_verify = self._write_fake_verify(tmp, log, capture_success=False, sequence_rc=5)
            out.write_text(
                json.dumps(
                    {
                        "schema": "pdocker.storage.metrics.sequence.v1",
                        "success": True,
                        "stale": True,
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            result = self._run_runner(out, workdir, fake_adb, fake_verify)

            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            artifact = json.loads(out.read_text(encoding="utf-8"))
            self.assertFalse(artifact["success"])
            self.assertEqual(artifact["status"], "planned-gap")
            self.assertNotIn("stale", artifact)
            self.assertTrue(artifact["runner"]["removed_previous_artifact"])
            self.assertEqual(artifact["capture"]["baseline"]["return_code"], 23)
            self.assertEqual(artifact["validation"]["return_code"], 5)
            self.assertIn("rm -f output artifact", artifact["runner"]["stale_artifact_policy"])
            self.assertIn("sequence_saw_fresh_artifact", log.read_text(encoding="utf-8"))

    def test_sequence_validator_runs_only_after_fresh_artifact_creation(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            out = tmp / "storage-sequence.json"
            workdir = tmp / "work"
            fake_adb = self._write_fake_adb(tmp)
            log = tmp / "verify.log"
            fake_verify = self._write_fake_verify(tmp, log, capture_success=True, sequence_rc=17)
            out.write_text('{"success": true, "stale": true}\n', encoding="utf-8")

            result = self._run_runner(out, workdir, fake_adb, fake_verify)

            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            events = log.read_text(encoding="utf-8").splitlines()
            self.assertGreaterEqual(len(events), 2)
            self.assertIn("capture_created_baseline", events[0])
            self.assertIn("sequence_saw_fresh_artifact", "\n".join(events[1:]))
            artifact = json.loads(out.read_text(encoding="utf-8"))
            phases = {phase["name"]: phase for phase in artifact["phases"]}
            self.assertEqual(phases["baseline"]["status"], "captured")
            self.assertIn("system_df", phases["baseline"]["snapshot"])
            for phase in ["after-build", "after-rebuild", "after-edit", "after-prune"]:
                self.assertEqual(phases[phase]["status"], "planned-gap")
                self.assertFalse(phases[phase]["success"])
                self.assertEqual(phases[phase]["snapshot"], {})
            self.assertEqual(artifact["validation"]["return_code"], 17)
            self.assertTrue(artifact["validation"]["invoked_only_after_fresh_artifact_creation"])
            self.assertFalse(artifact["capture"]["real_capture_complete"])

    def test_runbook_describes_no_stale_success_and_planned_gap(self):
        doc = DOC.read_text(encoding="utf-8")
        self.assertIn("scripts/android-storage-metrics-sequence.sh", doc)
        self.assertIn("success=false", doc)
        self.assertIn("rm -f", doc)
        self.assertIn("verify-storage-metrics.py --capture-device", doc)
        self.assertIn("verify-storage-metrics.py --sequence", doc)
        for phase in ["build", "rebuild", "edit", "prune"]:
            self.assertIn(phase, doc.lower())

    def _run_runner(self, out: Path, workdir: Path, fake_adb: Path, fake_verify: Path) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env.pop("ANDROID_SERIAL", None)
        env.pop("ADB_SERIAL", None)
        env["PYTHON"] = sys.executable
        return subprocess.run(
            [
                str(SCRIPT),
                "--out",
                str(out),
                "--workdir",
                str(workdir),
                "--adb",
                str(fake_adb),
                "--package",
                "io.example.pdocker",
                "--verify",
                str(fake_verify),
            ],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def _write_fake_adb(self, tmp: Path) -> Path:
        fake_adb = tmp / "adb"
        fake_adb.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        fake_adb.chmod(0o755)
        return fake_adb

    def _write_fake_verify(self, tmp: Path, log: Path, *, capture_success: bool, sequence_rc: int) -> Path:
        fake_verify = tmp / "verify-storage-metrics.py"
        capture_rc = 0 if capture_success else 23
        script = f"""
            #!/usr/bin/env python3
            import json
            import sys
            from pathlib import Path

            log = Path({str(log)!r})

            def append(message):
                with log.open("a", encoding="utf-8") as fh:
                    fh.write(message + "\\n")

            def value_after(flag):
                return sys.argv[sys.argv.index(flag) + 1]

            if "--capture-device" in sys.argv:
                if {capture_success!r}:
                    Path(value_after("--output")).write_text(json.dumps({{
                        "system_df": {{
                            "SharedLayerBytes": 1,
                            "ContainerUpperBytes": 0,
                            "UniqueBytes": 1,
                            "TotalBytes": 10,
                            "FreeBytes": 9,
                            "PdockerStorage": {{
                                "SharedLayerPool": "shared layer pool",
                                "Overlap": "image views must not be added",
                                "ContainerUpper": "private upper storage",
                            }},
                        }},
                        "images": [{{"VirtualSize": 1, "SharedSize": 0, "UniqueSize": 1}}],
                        "containers": [],
                    }}) + "\\n", encoding="utf-8")
                    append("capture_created_baseline " + " ".join(sys.argv[1:]))
                    raise SystemExit(0)
                append("capture_failed " + " ".join(sys.argv[1:]))
                raise SystemExit({capture_rc})

            if "--sequence" in sys.argv:
                artifact_path = Path(value_after("--sequence"))
                if not artifact_path.exists():
                    append("sequence_missing_artifact")
                    raise SystemExit(91)
                data = json.loads(artifact_path.read_text(encoding="utf-8"))
                if data.get("stale") or data.get("success") is True:
                    append("sequence_saw_stale_or_passing_artifact")
                    raise SystemExit(92)
                phase_names = [phase.get("name") for phase in data.get("phases", [])]
                required = ["baseline", "after-build", "after-rebuild", "after-edit", "after-prune"]
                if phase_names != required:
                    append("sequence_missing_required_phases " + repr(phase_names))
                    raise SystemExit(93)
                if data.get("runner", {{}}).get("write_stage") != "pre-sequence-validation":
                    append("sequence_not_called_after_initial_fresh_write")
                    raise SystemExit(94)
                append("sequence_saw_fresh_artifact " + str(artifact_path))
                raise SystemExit({sequence_rc})

            append("unexpected_args " + " ".join(sys.argv[1:]))
            raise SystemExit(99)
        """
        fake_verify.write_text(textwrap.dedent(script).lstrip(), encoding="utf-8")
        fake_verify.chmod(0o755)
        return fake_verify


if __name__ == "__main__":
    unittest.main()

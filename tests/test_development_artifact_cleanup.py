import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "maintenance" / "clean-development-artifacts.py"
SPEC = importlib.util.spec_from_file_location("clean_development_artifacts", SCRIPT)
cleanup = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = cleanup
assert SPEC.loader is not None
SPEC.loader.exec_module(cleanup)


class DevelopmentArtifactCleanupTest(unittest.TestCase):
    def test_include_evidence_never_selects_directory_with_tracked_descendant(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            tracked_log = root / "docs" / "test" / "device-logs" / "run.log"
            tracked_log.parent.mkdir(parents=True)
            tracked_log.write_text("tracked evidence\n", encoding="utf-8")
            tracked_wait = root / "docs" / "test" / "llama-gpu-compare-latest-artifacts" / "wait-server.jsonl"
            tracked_wait.parent.mkdir(parents=True)
            tracked_wait.write_text("{}\n", encoding="utf-8")

            original = cleanup.tracked_paths
            cleanup.tracked_paths = lambda _: {
                "docs/test/device-logs/run.log",
                "docs/test/llama-gpu-compare-latest-artifacts/wait-server.jsonl",
            }
            try:
                candidates = cleanup.collect_candidates(root, include_evidence=True)
            finally:
                cleanup.tracked_paths = original

        paths = {candidate.path for candidate in candidates}
        self.assertNotIn("docs/test/device-logs", paths)
        self.assertNotIn("docs/test/llama-gpu-compare-latest-artifacts", paths)
        self.assertEqual(set(), paths)

    def test_include_evidence_still_selects_untracked_ignored_evidence_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            evidence = root / "docs" / "test" / "llama-gpu-probe-latest.json"
            evidence.parent.mkdir(parents=True)
            evidence.write_text("{}\n", encoding="utf-8")

            original = cleanup.tracked_paths
            cleanup.tracked_paths = lambda _: set()
            try:
                candidates = cleanup.collect_candidates(root, include_evidence=True)
            finally:
                cleanup.tracked_paths = original

        self.assertEqual(["docs/test/llama-gpu-probe-latest.json"], [candidate.path for candidate in candidates])

    def test_default_cleanup_ignores_generated_evidence_without_explicit_opt_in(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            evidence = root / "docs" / "test" / "llama-gpu-probe-latest.json"
            evidence.parent.mkdir(parents=True)
            evidence.write_text("{}\n", encoding="utf-8")

            original = cleanup.tracked_paths
            cleanup.tracked_paths = lambda _: set()
            try:
                candidates = cleanup.collect_candidates(root, include_evidence=False)
            finally:
                cleanup.tracked_paths = original

        self.assertEqual([], candidates)


if __name__ == "__main__":
    unittest.main()

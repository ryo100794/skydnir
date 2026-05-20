import importlib.util
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERIFY_INVENTORY_PATH = ROOT / "scripts" / "verify-script-inventory.py"
VERIFY_INVENTORY_SPEC = importlib.util.spec_from_file_location(
    "verify_script_inventory",
    VERIFY_INVENTORY_PATH,
)
verify_script_inventory = importlib.util.module_from_spec(VERIFY_INVENTORY_SPEC)
assert VERIFY_INVENTORY_SPEC.loader is not None
VERIFY_INVENTORY_SPEC.loader.exec_module(verify_script_inventory)


class ScriptInventoryAuditTest(unittest.TestCase):
    def setUp(self):
        self.inventory = json.loads((ROOT / "scripts" / "script-inventory.json").read_text(encoding="utf-8"))
        self.readme = (ROOT / "scripts" / "README.md").read_text(encoding="utf-8")
        self.entries = {entry["path"]: entry for entry in self.inventory["entries"]}
        self.subtree_entries = {
            entry["path"]: entry
            for entry in self.inventory.get("subtree_entries", [])
        }

    def assertIsExecutable(self, path):
        self.assertTrue(
            path.stat().st_mode & 0o111,
            f"{path.relative_to(ROOT)} must keep an executable bit",
        )

    def assertShellWrapper(self, path, candidate_path, wrapper):
        self.assertIn("#!/usr/bin/env bash", wrapper)
        self.assertIn("set -euo pipefail", wrapper)
        self.assertIn('ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"', wrapper)
        self.assertIn(f'exec "$ROOT/{candidate_path}" "$@"', wrapper)

    def assertPythonWrapper(self, path, candidate_path, wrapper):
        self.assertTrue(wrapper.startswith("#!/usr/bin/env python3\n"))
        for snippet in (
            "import runpy",
            "import sys",
            f'TARGET_REL = "{candidate_path}"',
            "sys.argv[0] = str(TARGET)",
            'runpy.run_path(str(TARGET), run_name="__main__")',
        ):
            self.assertIn(snippet, wrapper)

    def test_every_entry_has_move_candidate_and_wrapper_policy(self):
        expected_targets = {
            "runtime-package-needed": "scripts/runtime",
            "build-developer": "scripts/build",
            "test-verification": "scripts/test",
            "generated-maintenance": "scripts/maintenance",
            "obsolete-suspect": "scripts/obsolete-candidates",
        }
        self.assertEqual(
            expected_targets,
            {
                category: value["target_dir"]
                for category, value in self.inventory["category_targets"].items()
            },
        )

        for entry in self.inventory["entries"]:
            with self.subTest(path=entry["path"]):
                migration = entry.get("migration")
                self.assertIsInstance(migration, dict)
                target_dir = expected_targets[entry["category"]]
                self.assertEqual(migration["target_dir"], target_dir)
                self.assertEqual(
                    migration["candidate_path"],
                    f"{target_dir}/{Path(entry['path']).name}",
                )
                self.assertIn("wrapper", migration["compat_wrapper"].lower())
                if entry["category"] == "obsolete-suspect":
                    self.assertEqual(migration["action"], "audit-delete-or-archive")
                else:
                    self.assertIn(
                        migration["action"],
                        {"candidate-move-behind-wrapper", "migrated-behind-wrapper"},
                    )
                if migration["action"] == "migrated-behind-wrapper":
                    candidate_path = migration["candidate_path"]
                    self.assertTrue((ROOT / candidate_path).is_file())
                    wrapper_path = ROOT / entry["path"]
                    self.assertIsExecutable(wrapper_path)
                    wrapper = wrapper_path.read_text(encoding="utf-8")
                    self.assertIn(candidate_path, wrapper)
                    if entry["path"].endswith(".sh"):
                        self.assertShellWrapper(entry["path"], candidate_path, wrapper)
                    elif entry["path"].endswith(".py"):
                        self.assertPythonWrapper(entry["path"], candidate_path, wrapper)
                    else:
                        self.fail(f"unsupported wrapper type: {entry['path']}")

    def test_verify_runner_files_are_classified_as_subtree_entries(self):
        expected_runner_paths = {
            "scripts/verify/runner/cow-overlay-kill-at-step-device.sh": "device-side-runner",
            "scripts/verify/runner/cow_overlay_kill_at_step_device.py": "device-runner",
            "scripts/verify/runner/image-pull-crash-safety-device.sh": "device-side-runner",
            "scripts/verify/runner/image_pull_crash_safety_device.py": "device-runner",
        }

        self.assertEqual(set(expected_runner_paths), set(self.subtree_entries))
        for path, stability in expected_runner_paths.items():
            with self.subTest(path=path):
                entry = self.subtree_entries[path]
                self.assertTrue((ROOT / path).is_file())
                self.assertEqual(entry["category"], "test-verification")
                self.assertEqual(entry["stability"], stability)
                migration = entry["migration"]
                self.assertEqual(migration["target_dir"], "scripts/test/verify/runner")
                self.assertEqual(
                    migration["candidate_path"],
                    f"scripts/test/verify/runner/{Path(path).name}",
                )
                self.assertEqual(migration["action"], "candidate-move-behind-wrapper")
                self.assertIn("scripts/verify/runner", migration["compat_wrapper"])
                self.assertIn(path, self.readme)

    def test_script_surface_budget_is_intentional(self):
        categories = Counter(entry["category"] for entry in self.inventory["entries"])
        self.assertEqual(
            verify_script_inventory.EXPECTED_TOP_LEVEL_SCRIPT_COUNT,
            len(self.inventory["entries"]),
        )
        self.assertEqual(
            verify_script_inventory.EXPECTED_SUBTREE_ENTRY_COUNT,
            len(self.inventory.get("subtree_entries", [])),
        )
        self.assertEqual(
            verify_script_inventory.EXPECTED_CATEGORY_COUNTS,
            dict(categories),
        )
        verify_script_inventory.validate_script_surface_budget(
            self.inventory["entries"],
            self.inventory.get("subtree_entries", []),
            categories,
        )

    def test_script_surface_budget_rejects_extra_top_level_entry(self):
        entries = list(self.inventory["entries"]) + [
            {
                "path": "scripts/new-helper.sh",
                "category": "test-verification",
            }
        ]
        categories = Counter(entry["category"] for entry in entries)
        with self.assertRaises(SystemExit):
            verify_script_inventory.validate_script_surface_budget(
                entries,
                self.inventory.get("subtree_entries", []),
                categories,
            )

    def test_script_surface_budget_rejects_category_drift(self):
        categories = Counter(
            {
                "runtime-package-needed": 1,
                "build-developer": 10,
                "test-verification": 75,
                "generated-maintenance": 3,
                "obsolete-suspect": 3,
            }
        )
        with self.assertRaises(SystemExit):
            verify_script_inventory.validate_script_surface_budget(
                self.inventory["entries"],
                self.inventory.get("subtree_entries", []),
                categories,
            )

    def test_script_doc_inventory_matches_counts_and_obsolete_suspects(self):
        categories = Counter(entry["category"] for entry in self.inventory["entries"])

        verify_script_inventory.validate_maintenance_doc_sync(
            self.inventory["entries"],
            categories,
        )

    def test_script_doc_inventory_rejects_stale_category_count(self):
        categories = Counter(entry["category"] for entry in self.inventory["entries"])
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            doc = tmp_root / "docs" / "maintenance" / "SCRIPT_DOC_INVENTORY.md"
            doc.parent.mkdir(parents=True)
            doc.write_text(
                "Runtime packaging | 1 top-level script\n"
                "Build | 9 top-level scripts\n"
                "Test | 75 top-level scripts\n"
                "Generated maintenance | 3 entries\n"
                "Unused or legacy candidates | 1 tracked candidate\n"
                "android-terminal-it-repro.sh\n",
                encoding="utf-8",
            )
            previous_doc = verify_script_inventory.SCRIPT_DOC_INVENTORY
            try:
                verify_script_inventory.SCRIPT_DOC_INVENTORY = doc
                with self.assertRaises(SystemExit):
                    verify_script_inventory.validate_maintenance_doc_sync(
                        self.inventory["entries"],
                        categories,
                    )
            finally:
                verify_script_inventory.SCRIPT_DOC_INVENTORY = previous_doc

    def test_script_doc_inventory_rejects_stale_obsolete_candidate_names(self):
        categories = Counter(entry["category"] for entry in self.inventory["entries"])
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            doc = tmp_root / "docs" / "maintenance" / "SCRIPT_DOC_INVENTORY.md"
            doc.parent.mkdir(parents=True)
            doc.write_text(
                "Runtime packaging | 1 top-level script\n"
                "Build | 9 top-level scripts\n"
                "Test | 76 top-level scripts\n"
                "Generated maintenance | 3 entries\n"
                "Unused or legacy candidates | 1 tracked candidate\n"
                "stale-terminal-repro.sh\n",
                encoding="utf-8",
            )
            previous_doc = verify_script_inventory.SCRIPT_DOC_INVENTORY
            try:
                verify_script_inventory.SCRIPT_DOC_INVENTORY = doc
                with self.assertRaises(SystemExit):
                    verify_script_inventory.validate_maintenance_doc_sync(
                        self.inventory["entries"],
                        categories,
                    )
            finally:
                verify_script_inventory.SCRIPT_DOC_INVENTORY = previous_doc

    def test_migrated_wrappers_have_no_retirement_blocking_references(self):
        migrated_paths = {
            entry["path"]
            for entry in self.inventory["entries"]
            if entry["migration"]["action"] == "migrated-behind-wrapper"
        }
        references = verify_script_inventory.find_script_references(
            migrated_paths,
            verify_script_inventory.tracked_reference_scan_files(),
        )
        self.assertEqual({}, references)

    def test_candidate_duplicate_guard_accepts_current_inventory(self):
        verify_script_inventory.validate_candidate_duplicate_consistency(
            self.inventory["entries"]
        )

    def test_candidate_duplicate_guard_rejects_unmigrated_duplicate(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            (tmp_root / "scripts" / "test").mkdir(parents=True)
            (tmp_root / "scripts" / "foo.sh").write_text(
                "#!/usr/bin/env bash\n", encoding="utf-8"
            )
            (tmp_root / "scripts" / "test" / "foo.sh").write_text(
                "#!/usr/bin/env bash\n", encoding="utf-8"
            )
            entry = {
                "path": "scripts/foo.sh",
                "migration": {
                    "candidate_path": "scripts/test/foo.sh",
                    "action": "candidate-move-behind-wrapper",
                },
            }
            previous_root = verify_script_inventory.ROOT
            try:
                verify_script_inventory.ROOT = tmp_root
                with self.assertRaises(SystemExit):
                    verify_script_inventory.validate_candidate_duplicate_consistency(
                        [entry]
                    )
            finally:
                verify_script_inventory.ROOT = previous_root

    def test_candidate_duplicate_guard_allows_migrated_wrapper_duplicate(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            (tmp_root / "scripts" / "test").mkdir(parents=True)
            (tmp_root / "scripts" / "foo.sh").write_text(
                "#!/usr/bin/env bash\n", encoding="utf-8"
            )
            (tmp_root / "scripts" / "test" / "foo.sh").write_text(
                "#!/usr/bin/env bash\n", encoding="utf-8"
            )
            entry = {
                "path": "scripts/foo.sh",
                "migration": {
                    "candidate_path": "scripts/test/foo.sh",
                    "action": "migrated-behind-wrapper",
                },
            }
            previous_root = verify_script_inventory.ROOT
            try:
                verify_script_inventory.ROOT = tmp_root
                verify_script_inventory.validate_candidate_duplicate_consistency([entry])
            finally:
                verify_script_inventory.ROOT = previous_root

    def test_reference_scan_scope_and_allowlist(self):
        self.assertTrue(
            verify_script_inventory.is_reference_scan_path("docs/manual/README.md")
        )
        self.assertTrue(
            verify_script_inventory.is_reference_scan_path(
                ".github/workflows/showcase.yml"
            )
        )
        self.assertTrue(
            verify_script_inventory.is_reference_scan_path(
                "tests/test_driver_manifest.json"
            )
        )
        self.assertTrue(
            verify_script_inventory.is_reference_scan_path(
                "tests/test_scenario_manifest.py"
            )
        )
        self.assertFalse(
            verify_script_inventory.is_reference_scan_path("scripts/README.md")
        )
        self.assertFalse(
            verify_script_inventory.is_reference_scan_path(
                "scripts/script-inventory.json"
            )
        )
        self.assertFalse(
            verify_script_inventory.is_reference_scan_path(
                "scripts/verify-script-inventory.py"
            )
        )
        self.assertFalse(
            verify_script_inventory.is_reference_scan_path(
                "tests/test_script_inventory_audit.py"
            )
        )

    def test_reference_scan_finds_top_level_wrapper_mentions(self):
        script_path = "scripts/smoke-opencl-bridge.sh"
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            manifest = tmp_root / "manifest.json"
            manifest.write_text(
                '{"cmd": "bash scripts/smoke-opencl-bridge.sh"}\n',
                encoding="utf-8",
            )
            previous_root = verify_script_inventory.ROOT
            try:
                verify_script_inventory.ROOT = tmp_root
                references = verify_script_inventory.find_script_references(
                    [script_path],
                    [manifest],
                )
            finally:
                verify_script_inventory.ROOT = previous_root
        self.assertEqual(
            {
                script_path: [
                    'manifest.json:1: {"cmd": "bash scripts/smoke-opencl-bridge.sh"}'
                ]
            },
            references,
        )

    def test_obsolete_suspects_have_audit_decisions_and_replacements(self):
        expected_replacements = {
            "scripts/android-terminal-it-repro.sh": "python3 scripts/pdocker-test-driver.py --lane android-terminal-exec-it",
        }

        obsolete = {
            entry["path"]
            for entry in self.inventory["entries"]
            if entry["category"] == "obsolete-suspect"
        }
        self.assertEqual(set(expected_replacements), obsolete)

        for path, replacement in expected_replacements.items():
            with self.subTest(path=path):
                entry = self.entries[path]
                audit = entry.get("audit")
                self.assertIsInstance(audit, dict)
                self.assertEqual(audit["date"], "2026-05-18")
                self.assertIn("no", audit["reference_scan"].lower())
                self.assertEqual(audit["replacement_command"], replacement)
                self.assertIn("keep", audit["decision"].lower())
                self.assertRegex(audit["decision"].lower(), r"delet(e|ion)")
                self.assertIn(path, self.readme)
                self.assertIn(replacement, self.readme)


if __name__ == "__main__":
    unittest.main()

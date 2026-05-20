import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify-docs-maintenance.py"

spec = importlib.util.spec_from_file_location("verify_docs_maintenance", SCRIPT)
assert spec and spec.loader
verifier = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = verifier
spec.loader.exec_module(verifier)


BACKLOG = """# Documentation Deduplication Backlog

## Duplicate and scatter hotspots

### 1. Release facts

Canonical owners:

- Release posture: [`../release/RELEASE_READINESS.md`](../release/RELEASE_READINESS.md)

Backlog:

- Link duplicates.

### 2. Llama material

Canonical owners:

- GPU design: [`../design/GPU_COMPAT.md`](../design/GPU_COMPAT.md)

Backlog:

- Add index.

### 3. Memory material

Canonical owners:

- Pager design: [`../design/APK_MEMORY_PAGER.md`](../design/APK_MEMORY_PAGER.md)

Backlog:

- Link design.

### 4. Storage material

Canonical owners:

- Storage architecture: [`../design/STORAGE_LAYER_ARCHITECTURE.md`](../design/STORAGE_LAYER_ARCHITECTURE.md)

Backlog:

- Add index.

### 5. Runtime material

Canonical owners:

- Runtime direction: [`../design/RUNTIME_STRATEGY.md`](../design/RUNTIME_STRATEGY.md)

Backlog:

- Link commands.

### 6. Terminal material

Canonical owners:

- Terminal architecture: [`../design/TERMINAL_STREAM_ARCHITECTURE.md`](../design/TERMINAL_STREAM_ARCHITECTURE.md)

Backlog:

- Link gates.

### 7. Test evidence

Canonical owners:

- Test category rules: [`../test/README.md`](../test/README.md)

Backlog:

- Keep latest pointers.

### 8. Planning material

Canonical owners:

- Current TODOs: [`../plan/TODO.md`](../plan/TODO.md)

Backlog:

- Keep timelines immutable.

## Open backlog count

There are 8 active deduplication backlog groups in this inventory.
"""


class DocsMaintenanceVerifierTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pdocker-docs-maint-test-"))
        (self.tmp / "docs" / "manual").mkdir(parents=True)
        (self.tmp / "docs" / "maintenance").mkdir(parents=True)
        (self.tmp / "docs" / "release").mkdir(parents=True)
        (self.tmp / "docs" / "design").mkdir(parents=True)
        (self.tmp / "docs" / "test").mkdir(parents=True)
        (self.tmp / "docs" / "plan").mkdir(parents=True)
        for path in [
            "docs/manual/README.md",
            "docs/README.md",
            "LICENSE",
            "docs/design/README.md",
            "docs/release/RELEASE_READINESS.md",
            "docs/design/GPU_COMPAT.md",
            "docs/design/APK_MEMORY_PAGER.md",
            "docs/design/STORAGE_LAYER_ARCHITECTURE.md",
            "docs/design/RUNTIME_STRATEGY.md",
            "docs/design/TERMINAL_STREAM_ARCHITECTURE.md",
            "docs/test/README.md",
        ]:
            (self.tmp / path).write_text("# fixture\n", encoding="utf-8")
        (self.tmp / "docs" / "plan" / "TODO.md").write_text(
            "# TODO\n\n"
            "- [doing] [#1](https://example.invalid/issues/1) issue-linked item\n"
            "- [next] Artifact-backed item writes docs/test/example-latest.json\n",
            encoding="utf-8",
        )
        (self.tmp / "docs" / "maintenance" / "DOCUMENTATION_DEDUP_BACKLOG.md").write_text(
            BACKLOG,
            encoding="utf-8",
        )
        (self.tmp / "docs" / "maintenance" / "README.md").write_text(
            "[`DOCUMENTATION_DEDUP_BACKLOG.md`](DOCUMENTATION_DEDUP_BACKLOG.md)\n",
            encoding="utf-8",
        )
        (self.tmp / "docs" / "README.md").write_text(
            "# Docs\n\n"
            "## Contents\n\n"
            "| Category | Purpose | Index |\n"
            "|---|---|---|\n"
            "| Manual | User docs | [`manual/README.md`](manual/README.md) |\n"
            "| Design | Architecture | [`design/README.md`](design/README.md) |\n"
            "| Test | Test docs | [`test/README.md`](test/README.md) |\n"
            "| License/compliance | Root policy | [`../LICENSE`](../LICENSE) |\n",
            encoding="utf-8",
        )
        (self.tmp / "README.md").write_text(
            "# Root\n\n"
            "## Documentation map\n\n"
            "- [`docs/README.md`](docs/README.md): documentation index\n"
            "- [`docs/manual/`](docs/manual/): user docs\n"
            "- [`docs/design/README.md`](docs/design/README.md): architecture\n"
            "- [`docs/test/README.md`](docs/test/README.md): tests\n",
            encoding="utf-8",
        )

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_backlog_fixture_passes(self):
        verifier.check_backlog(self.tmp)

    def test_backlog_requires_eight_groups(self):
        backlog = self.tmp / "docs" / "maintenance" / "DOCUMENTATION_DEDUP_BACKLOG.md"
        backlog.write_text(BACKLOG.replace("### 8. Planning material", "## Planning material"), encoding="utf-8")

        with self.assertRaises(verifier.CheckFailure):
            verifier.check_backlog(self.tmp)

    def test_backlog_requires_canonical_owner_section(self):
        backlog = self.tmp / "docs" / "maintenance" / "DOCUMENTATION_DEDUP_BACKLOG.md"
        backlog.write_text(BACKLOG.replace("Canonical owners:", "Canonical owner:", 1), encoding="utf-8")

        with self.assertRaises(verifier.CheckFailure):
            verifier.check_backlog(self.tmp)

    def test_local_markdown_link_checker_ignores_external_urls(self):
        doc = self.tmp / "docs" / "maintenance" / "README.md"
        doc.write_text(
            "[ok](DOCUMENTATION_DEDUP_BACKLOG.md) [external](https://example.invalid/missing)",
            encoding="utf-8",
        )

        self.assertEqual([], verifier.check_local_markdown_links(self.tmp))

    def test_local_markdown_link_checker_reports_missing_relative_target(self):
        doc = self.tmp / "docs" / "maintenance" / "README.md"
        doc.write_text("[missing](NOPE.md)", encoding="utf-8")

        issues = verifier.check_local_markdown_links(self.tmp)
        self.assertEqual(1, len(issues))
        self.assertEqual("NOPE.md", issues[0].target)

    def test_doc_discoverability_accepts_category_readme_link(self):
        doc = self.tmp / "docs" / "test" / "DEVICE_GATE.md"
        doc.write_text("# Device gate\n", encoding="utf-8")
        readme = self.tmp / "docs" / "test" / "README.md"
        readme.write_text(
            "| Document | Scope |\n"
            "|---|---|\n"
            "| [`DEVICE_GATE.md`](DEVICE_GATE.md) | Device gate |\n",
            encoding="utf-8",
        )

        verifier.check_doc_discoverability(self.tmp)

    def test_doc_discoverability_rejects_unindexed_durable_doc(self):
        doc = self.tmp / "docs" / "test" / "UNLISTED.md"
        doc.write_text("# Unlisted\n", encoding="utf-8")

        with self.assertRaises(verifier.CheckFailure):
            verifier.check_doc_discoverability(self.tmp)

    def test_doc_discoverability_accepts_explicit_owner_pattern(self):
        run_dir = (
            self.tmp
            / "docs"
            / "test"
            / "runs"
            / "20260518T000000Z-host-smoke"
        )
        run_dir.mkdir(parents=True)
        (run_dir / "summary.md").write_text("# Run summary\n", encoding="utf-8")

        verifier.check_doc_discoverability(self.tmp)

    def test_latest_evidence_file_accepts_index_owner(self):
        artifact = self.tmp / "docs" / "test" / "example-latest.json"
        artifact.write_text("{}", encoding="utf-8")
        evidence_index = self.tmp / "docs" / "test" / "EVIDENCE_INDEX.md"
        evidence_index.write_text(
            "| Family | Representative latest files | Canonical owner |\n"
            "|---|---|---|\n"
            "| Fixture | `example-latest.json` | [`README.md`](README.md) |\n",
            encoding="utf-8",
        )

        verifier.check_latest_evidence_files_have_owner(self.tmp)

    def test_latest_evidence_file_rejects_accidental_suffix_substring_owner(self):
        artifact = self.tmp / "docs" / "test" / "unowned-latest.json"
        artifact.write_text("{}", encoding="utf-8")
        evidence_index = self.tmp / "docs" / "test" / "EVIDENCE_INDEX.md"
        evidence_index.write_text("`some-unowned-latest.json`\n", encoding="utf-8")

        with self.assertRaises(verifier.CheckFailure):
            verifier.check_latest_evidence_files_have_owner(self.tmp)

    def test_latest_evidence_nested_file_accepts_latest_artifact_directory_owner(self):
        artifact_dir = self.tmp / "docs" / "test" / "example-latest-artifacts"
        artifact_dir.mkdir()
        (artifact_dir / "wait-server.jsonl").write_text("{}", encoding="utf-8")
        evidence_index = self.tmp / "docs" / "test" / "EVIDENCE_INDEX.md"
        evidence_index.write_text(
            "| Family | Representative latest files | Canonical owner |\n"
            "|---|---|---|\n"
            "| Fixture | `example-latest-artifacts` | [`README.md`](README.md) |\n",
            encoding="utf-8",
        )

        verifier.check_latest_evidence_files_have_owner(self.tmp)

    def test_latest_evidence_nested_file_accepts_exact_full_path_owner(self):
        artifact_dir = self.tmp / "docs" / "test" / "example-latest-artifacts"
        artifact_dir.mkdir()
        (artifact_dir / "wait-server.jsonl").write_text("{}", encoding="utf-8")
        evidence_index = self.tmp / "docs" / "test" / "EVIDENCE_INDEX.md"
        evidence_index.write_text(
            "`docs/test/example-latest-artifacts/wait-server.jsonl`\n",
            encoding="utf-8",
        )

        verifier.check_latest_evidence_files_have_owner(self.tmp)

    def test_latest_evidence_nested_file_rejects_accidental_directory_substring_owner(self):
        artifact_dir = self.tmp / "docs" / "test" / "example-latest-artifacts"
        artifact_dir.mkdir()
        (artifact_dir / "wait-server.jsonl").write_text("{}", encoding="utf-8")
        evidence_index = self.tmp / "docs" / "test" / "EVIDENCE_INDEX.md"
        evidence_index.write_text("`example-latest-artifacts-old`\n", encoding="utf-8")

        with self.assertRaises(verifier.CheckFailure):
            verifier.check_latest_evidence_files_have_owner(self.tmp)

    def test_latest_evidence_nested_file_rejects_generic_child_basename_owner(self):
        artifact_dir = self.tmp / "docs" / "test" / "unowned-latest-artifacts"
        artifact_dir.mkdir()
        (artifact_dir / "wait-server.jsonl").write_text("{}", encoding="utf-8")
        evidence_index = self.tmp / "docs" / "test" / "EVIDENCE_INDEX.md"
        evidence_index.write_text("`wait-server.jsonl`\n", encoding="utf-8")

        with self.assertRaises(verifier.CheckFailure):
            verifier.check_latest_evidence_files_have_owner(self.tmp)

    def test_latest_evidence_nested_file_rejects_unowned_latest_path(self):
        artifact_dir = self.tmp / "docs" / "test" / "unowned-latest-artifacts"
        artifact_dir.mkdir()
        (artifact_dir / "wait-server.jsonl").write_text("{}", encoding="utf-8")

        with self.assertRaises(verifier.CheckFailure):
            verifier.check_latest_evidence_files_have_owner(self.tmp)

    def test_latest_evidence_file_rejects_unowned_pointer(self):
        artifact = self.tmp / "docs" / "test" / "unowned-latest.json"
        artifact.write_text("{}", encoding="utf-8")

        with self.assertRaises(verifier.CheckFailure):
            verifier.check_latest_evidence_files_have_owner(self.tmp)

    def test_latest_evidence_file_rejects_manifest_only_owner(self):
        artifact = self.tmp / "docs" / "test" / "manifest-only-latest.log"
        artifact.write_text("log\n", encoding="utf-8")
        manifest = self.tmp / "tests" / "test_driver_manifest.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            '{"outputs": ["docs/test/manifest-only-latest.log"]}\n',
            encoding="utf-8",
        )

        with self.assertRaises(verifier.CheckFailure):
            verifier.check_latest_evidence_files_have_owner(self.tmp)

    def test_root_documentation_map_matches_docs_categories(self):
        verifier.check_root_documentation_map_matches_docs_categories(self.tmp)

    def test_root_documentation_map_rejects_missing_docs_category(self):
        (self.tmp / "README.md").write_text(
            "# Root\n\n"
            "## Documentation map\n\n"
            "- [`docs/README.md`](docs/README.md): documentation index\n"
            "- [`docs/manual/`](docs/manual/): user docs\n",
            encoding="utf-8",
        )

        with self.assertRaises(verifier.CheckFailure):
            verifier.check_root_documentation_map_matches_docs_categories(self.tmp)

    def test_root_documentation_map_rejects_extra_docs_category(self):
        (self.tmp / "docs" / "stale").mkdir()
        (self.tmp / "docs" / "stale" / "README.md").write_text(
            "# Stale\n", encoding="utf-8"
        )
        readme = self.tmp / "README.md"
        readme.write_text(
            readme.read_text(encoding="utf-8")
            + "- [`docs/stale/`](docs/stale/): stale category\n",
            encoding="utf-8",
        )

        with self.assertRaises(verifier.CheckFailure):
            verifier.check_root_documentation_map_matches_docs_categories(self.tmp)

    def write_adboff_queue(self, highest: int, prose_highest: int | None = None):
        prose_highest = highest if prose_highest is None else prose_highest
        queue = self.tmp / "docs" / "plan" / "ADB_OFF_TASK_QUEUE_20260520.md"
        rows = [
            "| ID | Priority | Task | Host-only acceptance | Status |\n",
            "|---|---:|---|---|---|\n",
        ]
        for index in range(1, highest + 1):
            status = "Done."
            if index == 6:
                status = (
                    "Ongoing maintenance; current queue items ADBOFF-001 through "
                    f"ADBOFF-{prose_highest:03d} have landed."
                )
            rows.append(f"| ADBOFF-{index:03d} | P1 | Task | Acceptance | {status} |\n")
        queue.write_text(
            "# ADB-Off Task Queue\n\n"
            f"ADBOFF-001 through ADBOFF-{prose_highest:03d} have landed.\n\n"
            + "".join(rows),
            encoding="utf-8",
        )

    def test_adboff_queue_completion_ledger_accepts_matching_range(self):
        self.write_adboff_queue(6)

        verifier.check_adboff_queue_completion_ledger(self.tmp)

    def test_adboff_queue_completion_ledger_rejects_stale_range(self):
        self.write_adboff_queue(7, prose_highest=6)

        with self.assertRaises(verifier.CheckFailure):
            verifier.check_adboff_queue_completion_ledger(self.tmp)

    def write_script_inventory(self, obsolete_count=1):
        scripts = self.tmp / "scripts"
        scripts.mkdir(exist_ok=True)
        entries = [
            {"path": f"scripts/obsolete-{index}.sh", "category": "obsolete-suspect"}
            for index in range(obsolete_count)
        ]
        (scripts / "script-inventory.json").write_text(
            json.dumps({"entries": entries}),
            encoding="utf-8",
        )

    def test_agent_obsolete_suspect_count_language_accepts_current_count(self):
        self.write_script_inventory(obsolete_count=1)
        coordination = self.tmp / "docs" / "plan" / "AGENT_COORDINATION.md"
        coordination.write_text("Only one obsolete suspect remains.\n", encoding="utf-8")

        verifier.check_agent_obsolete_suspect_count_language(self.tmp)

    def test_agent_obsolete_suspect_count_language_rejects_stale_count(self):
        self.write_script_inventory(obsolete_count=1)
        coordination = self.tmp / "docs" / "plan" / "AGENT_COORDINATION.md"
        coordination.write_text(
            "The old transition updated counts from three to two obsolete suspects.\n",
            encoding="utf-8",
        )

        with self.assertRaises(verifier.CheckFailure):
            verifier.check_agent_obsolete_suspect_count_language(self.tmp)

    def test_historical_plan_rejects_live_running_assignment_section(self):
        timeline = self.tmp / "docs" / "plan" / "EXECUTION_TIMELINE_20260513.md"
        timeline.write_text(
            "The service was observed running in historical prose.\n\n"
            "## Current Agent Assignments\n\n"
            "| Agent | Lane | Write ownership | Manager status |\n"
            "| --- | --- | --- | --- |\n"
            "| Locke | T0-B | docs only | running |\n",
            encoding="utf-8",
        )

        with self.assertRaises(verifier.CheckFailure):
            verifier.check_historical_agent_assignments(self.tmp)

    def test_operational_ledger_allows_running_status(self):
        ledger = self.tmp / "docs" / "plan" / "AGENT_COORDINATION.md"
        ledger.write_text(
            "## Current Agent Assignments\n\n"
            "| Agent | Lane | Write ownership | Manager status |\n"
            "| --- | --- | --- | --- |\n"
            "| Main | GPU | runtime | running |\n",
            encoding="utf-8",
        )

        verifier.check_historical_agent_assignments(self.tmp)

    def test_historical_plan_accepts_non_running_status(self):
        timeline = self.tmp / "docs" / "plan" / "EXECUTION_TIMELINE_20260513.md"
        timeline.write_text(
            "## Current Agent Assignments\n\n"
            "| Agent | Lane | Write ownership | Manager status |\n"
            "| --- | --- | --- | --- |\n"
            "| Locke | T0-B | docs only | historical |\n",
            encoding="utf-8",
        )

        verifier.check_historical_agent_assignments(self.tmp)

    def test_todo_source_quality_rejects_vague_active_item(self):
        todo = self.tmp / "docs" / "plan" / "TODO.md"
        todo.write_text("- [doing] Vague public roadmap item without proof cue\n", encoding="utf-8")

        with self.assertRaises(verifier.CheckFailure):
            verifier.check_todo_roadmap_source_quality(self.tmp)

    def test_todo_source_quality_scans_beyond_showcase_horizon(self):
        todo = self.tmp / "docs" / "plan" / "TODO.md"
        todo.write_text(
            "".join(
                f"- [next] Acceptance: backed item {index}\n"
                for index in range(20)
            )
            + "- [next] Late vague item without durable cue\n",
            encoding="utf-8",
        )

        with self.assertRaises(verifier.CheckFailure):
            verifier.check_todo_roadmap_source_quality(self.tmp)

    def test_todo_source_quality_rejects_generic_modal_words(self):
        todo = self.tmp / "docs" / "plan" / "TODO.md"
        todo.write_text(
            "- [next] We must improve the UI before launch and prove it later\n",
            encoding="utf-8",
        )

        with self.assertRaises(verifier.CheckFailure):
            verifier.check_todo_roadmap_source_quality(self.tmp)

    def test_todo_source_quality_accepts_issue_artifact_and_acceptance_cues(self):
        todo = self.tmp / "docs" / "plan" / "TODO.md"
        todo.write_text(
            "- [doing] [#4](https://example.invalid/issues/4) issue cue\n"
            "- [next] Artifact cue records docs/test/example-latest.json\n"
            "- [blocked] Acceptance: explicit gate must prove device evidence\n",
            encoding="utf-8",
        )

        verifier.check_todo_roadmap_source_quality(self.tmp)

    def test_historical_evidence_language_rejects_good_row_with_open_gap(self):
        compat = self.tmp / "docs" / "test" / "COMPATIBILITY.md"
        compat.write_text(
            "| Area | Current status | Notes |\n"
            "|---|---:|---|\n"
            "| Container lifecycle | Good | Basic APIs exist, but teardown remains open. |\n",
            encoding="utf-8",
        )

        with self.assertRaises(verifier.CheckFailure):
            verifier.check_historical_evidence_language(self.tmp)

    def test_historical_evidence_language_accepts_partial_row_with_open_gap(self):
        compat = self.tmp / "docs" / "test" / "COMPATIBILITY.md"
        compat.write_text(
            "| Area | Current status | Notes |\n"
            "|---|---:|---|\n"
            "| Container lifecycle | Partial / teardown-gated | Basic APIs exist, but teardown remains open. |\n",
            encoding="utf-8",
        )

        verifier.check_historical_evidence_language(self.tmp)

    def test_historical_evidence_language_rejects_current_release_blocking_phrase(self):
        release = self.tmp / "docs" / "release" / "BUILD.md"
        release.write_text(
            "Keep this as the current release-blocking device smoke evidence.\n",
            encoding="utf-8",
        )

        with self.assertRaises(verifier.CheckFailure):
            verifier.check_historical_evidence_language(self.tmp)

    def write_extension_surface_fixture(self, compatibility_extra=""):
        pdockerd = self.tmp / "docker-proot-setup" / "bin" / "pdockerd"
        pdockerd.parent.mkdir(parents=True)
        pdockerd.write_text(
            'if path == "/system/df" and method == "GET": pass\n'
            'if path == "/system/prune" and method == "POST": pass\n'
            'if path == "/system/host" and method == "GET": pass\n'
            '"PdockerGpu": {}, "PdockerWarning": "host-network"\n',
            encoding="utf-8",
        )
        compatibility = self.tmp / "docs" / "test" / "COMPATIBILITY.md"
        compatibility.write_text(
            "Standard Docker system endpoints: `GET /system/df`, `POST /system/prune`.\n"
            "pdocker extensions: `GET /system/host`.\n"
            "Fields: `PdockerGpu`, `PdockerWarning`.\n"
            + compatibility_extra,
            encoding="utf-8",
        )
        scope = self.tmp / "docs" / "design" / "DOCKER_COMPAT_SCOPE.md"
        scope.write_text(
            "pdocker-only extension endpoints use selected `/system/*` paths, "
            "excluding Docker-standard `GET /system/df` and `POST /system/prune`.\n",
            encoding="utf-8",
        )

    def test_extension_surface_accepts_documented_routes_and_fields(self):
        self.write_extension_surface_fixture()

        verifier.check_pdocker_extension_surface(self.tmp)

    def test_extension_surface_rejects_undocumented_pdocker_route(self):
        self.write_extension_surface_fixture()
        pdockerd = self.tmp / "docker-proot-setup" / "bin" / "pdockerd"
        pdockerd.write_text(
            pdockerd.read_text(encoding="utf-8")
            + 'if path == "/system/media" and method == "GET": pass\n',
            encoding="utf-8",
        )

        with self.assertRaises(verifier.CheckFailure):
            verifier.check_pdocker_extension_surface(self.tmp)

    def test_extension_surface_rejects_undocumented_pdocker_field(self):
        self.write_extension_surface_fixture()
        pdockerd = self.tmp / "docker-proot-setup" / "bin" / "pdockerd"
        pdockerd.write_text(
            pdockerd.read_text(encoding="utf-8") + '"PdockerHidden": true\n',
            encoding="utf-8",
        )

        with self.assertRaises(verifier.CheckFailure):
            verifier.check_pdocker_extension_surface(self.tmp)

    def test_extension_surface_rejects_scope_without_standard_system_exception(self):
        self.write_extension_surface_fixture()
        scope = self.tmp / "docs" / "design" / "DOCKER_COMPAT_SCOPE.md"
        scope.write_text("pdocker extensions live under `/system/*`.\n", encoding="utf-8")

        with self.assertRaises(verifier.CheckFailure):
            verifier.check_pdocker_extension_surface(self.tmp)


if __name__ == "__main__":
    unittest.main()

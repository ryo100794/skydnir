import json
import stat
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "android-documents-mediator-smoke.sh"
MANIFEST = ROOT / "tests" / "test_driver_manifest.json"
DOC = ROOT / "docs" / "test" / "SAF_DIRECT_OUTPUT_GATE.md"


class SafDirectOutputContractTest(unittest.TestCase):
    def setUp(self):
        self.script = SCRIPT.read_text()
        self.manifest = json.loads(MANIFEST.read_text())
        self.doc = DOC.read_text()

    def test_script_is_executable_and_targets_container_documents_mount(self):
        self.assertTrue(SCRIPT.stat().st_mode & stat.S_IXUSR)
        for token in [
            "PDOCKER_SAF_DIRECT_OUTPUT_CONTAINER",
            "PDOCKER_SAF_DIRECT_OUTPUT_REQUIRE_CONTAINER",
            'DOCUMENTS_MOUNT="${PDOCKER_DOCUMENTS_MOUNT:-/documents}"',
            "run_container_documents_cases",
            'Cmd": ["/bin/sh", "-lc", sys.argv[1]]',
            "container-documents-cases-ok",
            "real container is required",
        ]:
            self.assertIn(token, self.script)
        self.assertNotIn("docker compose up", self.script)
        self.assertNotIn("docker exec", self.script)

    def test_direct_saf_backend_and_fallback_are_explicitly_distinguished(self):
        for token in [
            "direct_saf_payload",
            "sidecar_metadata",
            "FallbackPolicy",
            "AllowedOnlyWhenExplicitlyRecorded",
            "mirror-fallback-after-saf-error",
            "saf-synced-mirror-evicted",
            "app-private fallback was used; this is recorded evidence but not a direct-output pass",
            "payload was not observed directly under selected SAF/Documents host path",
        ]:
            self.assertIn(token, self.script)

    def test_gate_covers_sidecar_rename_stat_unlink_and_validation_cases(self):
        for token in [
            "WRITE_RELATIVE",
            "RENAME_SOURCE_RELATIVE",
            "RENAME_TARGET_RELATIVE",
            "UNLINK_RELATIVE",
            "write.sidecar.json",
            "rename.sidecar.json",
            "unlink-absent.rc",
            "validate_relative_path",
            "path_traversal",
            "read_only_grant",
            "RejectedExamples",
            "ObservedPersistedWriteGrant",
        ]:
            self.assertIn(token, self.script)

    def test_layer_boundary_is_documented_without_touching_cow_internals(self):
        for token in [
            "LayerBoundary",
            "FilesystemBackend",
            "UnixMetadata",
            "OverlayCowAwareness",
            "must not reach around it",
        ]:
            self.assertIn(token, self.script)
        for token in [
            "SAF direct-output gate",
            "Layer boundary",
            "COW/overlay",
            "must not bypass",
            "path traversal",
            "read-only grant",
        ]:
            self.assertIn(token, self.doc)

    def test_manifest_has_android_documents_lane(self):
        lane = self.manifest["lanes"].get("android-documents")
        self.assertIsInstance(lane, dict)
        self.assertIn("adb", lane.get("requires") or [])
        self.assertIn("installed-debug-apk", lane.get("requires") or [])
        commands = lane.get("commands") or []
        self.assertEqual(len(commands), 1)
        command = commands[0]
        self.assertEqual(command.get("id"), "android-documents-saf-direct-output")
        self.assertEqual(command.get("argv"), ["bash", "scripts/android-documents-mediator-smoke.sh"])
        self.assertIn("docs/test/saf-direct-output-latest.json", command.get("artifacts") or [])


if __name__ == "__main__":
    unittest.main()

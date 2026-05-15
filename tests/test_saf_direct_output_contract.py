import json
import stat
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "android-documents-mediator-smoke.sh"
MANIFEST = ROOT / "tests" / "test_driver_manifest.json"
DOC = ROOT / "docs" / "test" / "SAF_DIRECT_OUTPUT_GATE.md"
MEDIATOR = ROOT / "app" / "src" / "main" / "kotlin" / "io" / "github" / "ryo100794" / "pdocker" / "SafDocumentsMediator.kt"
MAIN = ROOT / "app" / "src" / "main" / "kotlin" / "io" / "github" / "ryo100794" / "pdocker" / "MainActivity.kt"


class SafDirectOutputContractTest(unittest.TestCase):
    def setUp(self):
        self.script = SCRIPT.read_text()
        self.manifest = json.loads(MANIFEST.read_text())
        self.doc = DOC.read_text()
        self.mediator = MEDIATOR.read_text()
        self.main = MAIN.read_text()

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

    def test_app_private_mirror_is_never_counted_as_direct_saf_success(self):
        for token in [
            "mirror_not_accepted_as_direct",
            "DirectPayloadObserved",
            "MirrorPayloadPresent",
            "MirrorOnlyRejected",
            "mirror_only_not_direct = mirror_present and not direct_write_ok",
            "app-private mirror exists but selected SAF/Documents host payload is missing; mirror is not direct-output evidence",
            "direct-output success requires matching payload under the selected SAF/Documents host path",
        ]:
            self.assertIn(token, self.script)
        self.assertIn('direct_write_ok = write_payload == payload', self.script)
        self.assertNotIn('direct_write_ok = mirror_exists', self.script)
        self.assertNotIn('Success": mirror_present', self.script)

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
            "ACTION_PREFIX.action.SMOKE_DOCUMENTS_WRITE_FILE",
            "saf-write-invalid-target.json",
            "direct_write_path_validation",
            "PathValidationPolicy",
            "../escape-phase2.txt",
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
            "fail-closed",
            "conflict evidence",
        ]:
            self.assertIn(token, self.doc)

    def test_mediator_rejects_unsafe_paths_and_records_conflicts(self):
        for token in [
            "normalizeRelativePathOrThrow",
            "Invalid SAF/Documents relative path",
            "unsafeRelativePathExamples",
            "PathValidationPolicy",
            "fail-closed",
            "checkNoProviderConflict",
            "recordConflictSidecar",
            "conflictState",
            "external-provider-change",
            "provider-payload-hash-changed",
            "providerEvidence",
            "sha256",
            "fallbackRecorded",
            "mirror-fallback-after-saf-error",
        ]:
            self.assertIn(token, self.mediator)
        for bad in ['path.replace', 'filter { it.isNotBlank() && it != "." && it != ".." }']:
            self.assertNotIn(bad, self.mediator)

    def test_direct_write_automation_fails_closed_before_fallback(self):
        for token in [
            "SafDocumentsMediator.normalizeRelativePathOrThrow(targetPath)",
            'put("PathValidationPolicy", "fail-closed")',
            'put("Fallback", false)',
            "invalid target path",
            "canonicalTarget.path.startsWith(root.path + File.separator)",
        ]:
            self.assertIn(token, self.main)

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

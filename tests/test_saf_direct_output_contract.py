import json
import stat
import unittest
from pathlib import Path

import importlib.util
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "android-documents-mediator-smoke.sh"
MANIFEST = ROOT / "tests" / "test_driver_manifest.json"
DOC = ROOT / "docs" / "test" / "SAF_DIRECT_OUTPUT_GATE.md"
MEDIATOR = ROOT / "app" / "src" / "main" / "kotlin" / "io" / "github" / "ryo100794" / "pdocker" / "SafDocumentsMediator.kt"
MAIN = ROOT / "app" / "src" / "main" / "kotlin" / "io" / "github" / "ryo100794" / "pdocker" / "MainActivity.kt"
VERIFIER = ROOT / "scripts" / "verify-saf-direct-output-artifact.py"
STORAGE_DOC = ROOT / "docs" / "design" / "STORAGE_LAYER_ARCHITECTURE.md"


class SafDirectOutputContractTest(unittest.TestCase):
    def setUp(self):
        self.script = SCRIPT.read_text()
        self.manifest = json.loads(MANIFEST.read_text())
        self.doc = DOC.read_text()
        self.mediator = MEDIATOR.read_text()
        self.main = MAIN.read_text()
        self.verifier = VERIFIER.read_text()

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

    def test_standalone_artifact_verifier_is_documented(self):
        self.assertTrue(VERIFIER.stat().st_mode & stat.S_IXUSR)
        for token in [
            "verify-saf-direct-output-artifact.py",
            "mirror-only",
            "recorded fallback",
            "planned-skip/planned-gap",
            "sidecar/provider/conflict evidence",
        ]:
            self.assertIn(token, self.doc)
        for token in [
            "FALLBACK_PAYLOAD_STATES",
            "NON_PROMOTING_STATUSES",
            "direct_saf_payload",
            "mirror_not_accepted_as_direct",
            "sidecar_metadata",
            "rename_stat",
            "unlink",
            "direct_write_path_validation",
            "DirectWriteEvidence",
            "LayerBoundary",
            "FallbackReason",
            "UnixMetadataBackend",
            "FilesystemBackend",
            "mirror-only payload evidence is not direct SAF output",
            "fallback was recorded; fallback evidence is non-promoting",
        ]:
            self.assertIn(token, self.verifier)

    def test_manifest_has_android_documents_lane(self):
        lane = self.manifest["lanes"].get("android-documents")
        self.assertIsInstance(lane, dict)
        self.assertIn("adb", lane.get("requires") or [])
        self.assertIn("installed-debug-apk", lane.get("requires") or [])
        commands = lane.get("commands") or []
        self.assertEqual(len(commands), 1)
        command = commands[0]
        self.assertEqual(command.get("id"), "android-documents-saf-direct-output")
        self.assertNotIn("argv", command)
        shell = command.get("shell", "")
        self.assertIn("scripts/android-documents-mediator-smoke.sh", shell)
        self.assertIn("PDOCKER_SAF_DIRECT_OUTPUT_CONTAINER", shell)
        self.assertIn("dev-workspace-compose-latest.json", shell)
        self.assertIn("running_container_id", shell)
        self.assertIn("docs/test/saf-direct-output-latest.json", command.get("artifacts") or [])

verifier_spec = importlib.util.spec_from_file_location("saf_direct_output_verifier", VERIFIER)
verifier = importlib.util.module_from_spec(verifier_spec)
assert verifier_spec.loader is not None
verifier_spec.loader.exec_module(verifier)


def synthetic_saf_artifact():
    write_rel = "pdocker-exports/case/nested/latest.log"
    rename_rel = "pdocker-exports/case/nested/renamed.log"
    unlink_rel = "pdocker-exports/case/nested/unlink-target.log"
    def sidecar(rel):
        return {
            "relativePath": rel,
            "unixMetadata": "sidecar",
            "UnixMetadata": {
                "source": "sidecar",
                "emulates": "unixfs",
                "fileType": "regular",
                "mode": 0o100644,
                "uid": 1000,
                "gid": 1000,
                "mtime": "2026-05-18T00:00:00Z",
            },
            "CapabilityReport": {
                "emulated_unix_metadata": True,
                "native_unix_metadata": False,
                "external_mutation_possible": True,
            },
            "conflictState": "clean",
            "providerEvidence": {
                "relativePath": rel,
                "documentId": "primary:Documents/" + rel,
                "size": 19,
                "sha256": "0" * 64,
            },
        }
    return {
        "SchemaVersion": 1,
        "Kind": "saf-direct-output-gate",
        "Success": True,
        "Status": "pass",
        "NoFakeSuccess": True,
        "SelectedHostPath": "/storage/emulated/0/Documents",
        "DocumentsMount": "/documents",
        "Container": "abc123",
        "RequireContainer": True,
        "Cases": {
            "container_documents_write": {
                "Attempted": True,
                "Success": True,
                "Container": "abc123",
                "DocumentsMount": "/documents",
                "ExitCode": 0,
            },
            "direct_saf_payload": {
                "Attempted": True,
                "Success": True,
                "RelativePath": write_rel,
                "SelectedHostPath": "/storage/emulated/0/Documents",
                "PayloadState": "saf-synced-mirror-evicted",
                "DirectPayloadObserved": True,
                "MirrorPayloadPresent": False,
                "MirrorPayloadEvicted": True,
                "DirectWriteEvidence": {
                    "Backend": "saf-unixfs",
                    "WritePath": "selected-saf-documents",
                    "SelectedHostPath": "/storage/emulated/0/Documents",
                    "RelativePath": write_rel,
                    "BytesWritten": 19,
                    "AppPrivateMirrorPromotes": False,
                },
            },
            "mirror_not_accepted_as_direct": {
                "Attempted": True,
                "Success": True,
                "MirrorOnlyRejected": False,
            },
            "sidecar_metadata": {
                "Attempted": True,
                "Success": True,
                "WriteSidecar": sidecar(write_rel),
                "RenameSidecar": sidecar(rename_rel),
            },
            "rename_stat": {
                "Attempted": True,
                "Success": True,
                "RelativePath": rename_rel,
                "PayloadState": "saf-synced-mirror-evicted",
            },
            "unlink": {
                "Attempted": True,
                "Success": True,
                "RelativePath": unlink_rel,
                "UnlinkSidecar": {"relativePath": unlink_rel, "operation": "unlink"},
            },
            "direct_write_path_validation": {
                "Attempted": True,
                "Success": True,
                "RejectedTarget": "../escape-phase2.txt",
                "PathValidationPolicy": "fail-closed",
                "Result": {
                    "Success": False,
                    "Fallback": False,
                    "PathValidationPolicy": "fail-closed",
                    "Error": "invalid target path: ../escape-phase2.txt",
                },
            },
        },
        "FallbackPolicy": {
            "AllowedOnlyWhenExplicitlyRecorded": True,
            "AcceptedPayloadStateForFallback": "mirror-fallback-after-saf-error",
            "FallbackRecorded": False,
            "MirrorOnlyRejected": False,
        },
        "LayerBoundary": {
            "FilesystemBackend": "saf-unixfs",
            "UnixMetadataBackend": "sidecar",
            "UpperLayersSeeSaf": False,
            "AbstractConsumers": ["overlay", "archive", "runtime", "ui"],
            "ForbiddenUpperLayerTerms": ["DocumentProvider", "treeUri", "FAT32", "exFAT", "SD-card"],
        },
        "Failures": [],
    }


class SafDirectOutputArtifactVerifierTest(unittest.TestCase):
    def write_artifact(self, artifact):
        tmp = tempfile.TemporaryDirectory()
        path = Path(tmp.name) / "saf-direct-output.json"
        path.write_text(json.dumps(artifact))
        return tmp, path

    def test_verifier_accepts_synthetic_direct_output_pass(self):
        tmp, path = self.write_artifact(synthetic_saf_artifact())
        with tmp:
            verifier.verify(path, require_container=True)

    def test_verifier_rejects_mirror_only_fake_success(self):
        artifact = synthetic_saf_artifact()
        artifact["Cases"]["direct_saf_payload"]["Success"] = True
        artifact["Cases"]["direct_saf_payload"]["DirectPayloadObserved"] = False
        artifact["Cases"]["direct_saf_payload"]["MirrorPayloadPresent"] = True
        artifact["Cases"]["mirror_not_accepted_as_direct"]["MirrorOnlyRejected"] = True
        tmp, path = self.write_artifact(artifact)
        with tmp:
            with self.assertRaisesRegex(verifier.VerificationError, "payload was not observed|mirror-only"):
                verifier.verify(path, require_container=True)

    def test_verifier_rejects_fallback_as_non_promoting(self):
        artifact = synthetic_saf_artifact()
        artifact["Cases"]["direct_saf_payload"]["PayloadState"] = "mirror-fallback-after-saf-error"
        artifact["FallbackPolicy"]["FallbackRecorded"] = True
        tmp, path = self.write_artifact(artifact)
        with tmp:
            with self.assertRaisesRegex(verifier.VerificationError, "non-promoting|fallback"):
                verifier.verify(path, require_container=True)

    def test_verifier_rejects_planned_skip_as_non_promoting(self):
        artifact = synthetic_saf_artifact()
        artifact["Success"] = False
        artifact["Status"] = "planned-skip"
        tmp, path = self.write_artifact(artifact)
        with tmp:
            with self.assertRaisesRegex(verifier.VerificationError, "non-promoting"):
                verifier.verify(path, require_container=True)

    def test_verifier_rejects_missing_path_validation(self):
        artifact = synthetic_saf_artifact()
        artifact["Cases"]["direct_write_path_validation"]["Success"] = False
        artifact["Cases"]["direct_write_path_validation"]["Result"]["Fallback"] = True
        tmp, path = self.write_artifact(artifact)
        with tmp:
            with self.assertRaisesRegex(verifier.VerificationError, "path.*fail-closed|fallback"):
                verifier.verify(path, require_container=True)

    def test_verifier_rejects_sidecar_without_provider_conflict_evidence(self):
        artifact = synthetic_saf_artifact()
        artifact["Cases"]["sidecar_metadata"]["WriteSidecar"].pop("providerEvidence")
        tmp, path = self.write_artifact(artifact)
        with tmp:
            with self.assertRaisesRegex(verifier.VerificationError, "write sidecar"):
                verifier.verify(path, require_container=True)

    def test_verifier_rejects_missing_direct_write_evidence(self):
        artifact = synthetic_saf_artifact()
        artifact["Cases"]["direct_saf_payload"].pop("DirectWriteEvidence")
        tmp, path = self.write_artifact(artifact)
        with tmp:
            with self.assertRaisesRegex(verifier.VerificationError, "DirectWriteEvidence"):
                verifier.verify(path, require_container=True)

    def test_verifier_rejects_fallback_without_explicit_reason(self):
        artifact = synthetic_saf_artifact()
        artifact["Cases"]["direct_saf_payload"]["PayloadState"] = "mirror-fallback-after-saf-error"
        artifact["FallbackPolicy"]["FallbackRecorded"] = True
        tmp, path = self.write_artifact(artifact)
        with tmp:
            with self.assertRaisesRegex(verifier.VerificationError, "FallbackReason"):
                verifier.verify(path, require_container=True)

    def test_verifier_rejects_sidecar_without_unix_metadata_contract(self):
        artifact = synthetic_saf_artifact()
        artifact["Cases"]["sidecar_metadata"]["WriteSidecar"].pop("UnixMetadata")
        tmp, path = self.write_artifact(artifact)
        with tmp:
            with self.assertRaisesRegex(verifier.VerificationError, "write sidecar"):
                verifier.verify(path, require_container=True)

    def test_verifier_rejects_missing_layer_boundary(self):
        artifact = synthetic_saf_artifact()
        artifact.pop("LayerBoundary")
        tmp, path = self.write_artifact(artifact)
        with tmp:
            with self.assertRaisesRegex(verifier.VerificationError, "LayerBoundary"):
                verifier.verify(path, require_container=True)

    def test_storage_layer_architecture_documents_unixfs_mediator_gate(self):
        for token in [
            "SAF-backed UnixFS exchange layer",
            "direct SAF write first",
            "fallbackRecorded",
            "fallbackReason",
            "FilesystemBackend",
            "UnixMetadataBackend",
            "sidecar metadata",
            "FAT32/exFAT/SD-card",
            "upper layers must not branch on SAF",
            "host contract",
        ]:
            self.assertIn(token, STORAGE_DOC.read_text())


if __name__ == "__main__":
    unittest.main()

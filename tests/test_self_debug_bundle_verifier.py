import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERIFIER_PATH = ROOT / "scripts" / "verify-self-debug-bundle.py"
DOC = ROOT / "docs/test/ANDROID_SELFDEBUG.md"

spec = importlib.util.spec_from_file_location("self_debug_bundle_verifier", VERIFIER_PATH)
verifier = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(verifier)


def successful_export(target="pdocker/diagnostics/self-debug-bundle-latest.json"):
    return {
        "Source": "/data/user/0/io.github.ryo100794.pdocker/files/pdocker/diagnostics/self-debug-bundle-latest.json",
        "Target": target,
        "MimeType": "application/json",
        "Access": "direct-path-writable",
        "PersistedWriteGrant": True,
        "SelectedHostPath": "/storage/emulated/0/Documents",
        "ActiveHostPath": "/storage/emulated/0/Documents",
        "PathValidationPolicy": "fail-closed",
        "Success": True,
        "Bytes": 8192,
        "Mode": "direct-path",
        "Attempts": [
            {
                "Success": True,
                "Mode": "direct-path",
                "RelativePath": target,
                "Bytes": 8192,
                "PathValidationPolicy": "fail-closed",
            }
        ],
    }


def failed_export(target="pdocker/diagnostics/self-debug-bundle-latest.json"):
    export = successful_export(target)
    export.update({"Success": False, "Bytes": 0, "Mode": "saf", "Error": "Documents grant is not writable"})
    export["Attempts"] = [{"Success": False, "Mode": "saf", "Error": "Documents grant is not writable"}]
    return export


def good_bundle():
    return {
        "schema": "pdocker.self-debug.bundle.v1",
        "created_at_epoch_ms": 1770000000000,
        "adb_independent": True,
        "requires_adb": False,
        "debug_policy": {
            "NoUsbNoWifiFallback": "Use this UI-exported bundle plus Documents artifacts when Android exposes no ADB transport.",
            "AdbHelper": "scripts/android-selfdebug.sh remains only a convenience wrapper for Wireless debugging.",
        },
        "app": {
            "Package": "io.github.ryo100794.pdocker",
            "Uid": 10123,
            "Version": "0.1.0 (42) 2026-05-18 abcdef0",
            "BuildGitCommit": "abcdef0",
            "BuildTimeUtc": "2026-05-18T00:00:00Z",
            "SdkInt": 35,
            "Device": "Google Pixel",
            "Abi": "arm64-v8a,armeabi-v7a",
        },
        "engine": {
            "Ping": {"Status": 200, "Text": "OK"},
            "Version": {"_HttpStatus": 200, "Version": "0.1"},
            "Info": {"_HttpStatus": 200, "ID": "pdocker"},
            "ContainersAll": {"Status": 200, "Items": []},
        },
        "documents": {
            "Metadata": {"DisplayName": "Documents", "ActiveHostPath": "/storage/emulated/0/Documents"},
            "PersistedGrant": {"Read": True, "Write": True},
        },
        "debug_roots": [
            {
                "Label": "pdocker home",
                "Path": "/data/user/0/io.github.ryo100794.pdocker/files/pdocker",
                "Writable": True,
                "Exists": True,
                "Summary": "directory, 5 entries",
            }
        ],
        "artifacts": {"ServiceTruth": {"Path": "/x", "Exists": False, "Bytes": 0, "ModifiedEpochMs": 0}},
        "memory_layers": {
            "OsMemTotal": 4096,
            "OsMemAvailable": 2048,
            "OsSwapTotal": 0,
            "OsSwapFree": 0,
            "PdockerProcessCount": 2,
            "PdockerRss": 123456,
            "PdockerSwap": 0,
            "ManagedReserveBytes": 0,
            "ManagedResidentBytes": 0,
            "TransparentRegistered": False,
            "Source": "/proc/meminfo + /proc",
        },
        "memory_snapshot_text": "MemTotal: 4096 kB\n",
        "process_snapshot_text": "PID     PPID    STATE  RSS\n123 1 S 12\n",
        "handle_snapshot_text": "fd snapshot\n123/fd/0 -> /dev/null\n",
        "active_operations": {
            "Source": "engine:/system/operations",
            "Count": 1,
            "Items": [
                {
                    "Id": "op-1",
                    "Kind": "pull",
                    "Status": "Running",
                    "StartedAt": 1770000000000,
                    "UpdatedAt": 1770000001000,
                }
            ],
        },
        "jobs": {
            "Source": "files/pdocker/jobs.json + files/pdocker/logs/jobs/*.log",
            "JobLogPathPolicy": "app-owned",
            "Count": 1,
            "Items": [
                {
                    "id": "job-1",
                    "title": "Docker pull",
                    "command": "engine pull alpine:latest",
                    "status": "Running",
                    "LogPath": "/data/user/0/io.github.ryo100794.pdocker/files/pdocker/logs/jobs/job-1.log",
                    "output": ["pulling alpine", "extracting layer"],
                    "LogExcerptBytes": 31,
                }
            ],
        },
        "LocalEvidenceFiles": {
            "Latest": "/data/user/0/io.github.ryo100794.pdocker/files/pdocker/diagnostics/self-debug-bundle-latest.json",
            "Timestamped": "/data/user/0/io.github.ryo100794.pdocker/files/pdocker/diagnostics/self-debug-bundle-1770000000000.json",
        },
        "DocumentsExport": successful_export(),
        "DocumentsEvidenceExport": successful_export("pdocker/diagnostics/self-debug-bundle-1770000000000.json"),
    }


class SelfDebugBundleVerifierTest(unittest.TestCase):
    def write_case(self, bundle):
        tmp = tempfile.TemporaryDirectory()
        path = Path(tmp.name) / "self-debug-bundle-latest.json"
        path.write_text(json.dumps(bundle), encoding="utf-8")
        return tmp, path

    def assert_rejected(self, bundle, pattern):
        tmp, path = self.write_case(bundle)
        with tmp:
            with self.assertRaisesRegex(verifier.VerificationError, pattern):
                verifier.verify(path)

    def test_accepts_complete_synthetic_adb_free_bundle(self):
        tmp, path = self.write_case(good_bundle())
        with tmp:
            self.assertEqual(verifier.verify(path)["schema"], "pdocker.self-debug.bundle.v1")

    def test_rejects_wrong_schema_or_adb_required_flags(self):
        bundle = good_bundle()
        bundle["schema"] = "other"
        self.assert_rejected(bundle, "schema must be")

        bundle = good_bundle()
        bundle["adb_independent"] = False
        self.assert_rejected(bundle, "adb_independent must be true")

        bundle = good_bundle()
        bundle["requires_adb"] = True
        self.assert_rejected(bundle, "requires_adb must be false")

    def test_rejects_missing_engine_probes(self):
        for probe in ("Ping", "Version", "Info", "ContainersAll"):
            with self.subTest(probe=probe):
                bundle = good_bundle()
                del bundle["engine"][probe]
                self.assert_rejected(bundle, f"engine.{probe} is required")

    def test_accepts_engine_probe_explicit_errors(self):
        bundle = good_bundle()
        bundle["engine"]["Ping"] = {"Error": "socket missing", "Type": "java.io.IOException"}
        bundle["engine"]["Version"] = {"Error": "socket missing", "Type": "java.io.IOException"}
        tmp, path = self.write_case(bundle)
        with tmp:
            verifier.verify(path)

    def test_rejects_missing_documents_metadata_and_persisted_grant(self):
        bundle = good_bundle()
        bundle["documents"]["Metadata"] = []
        self.assert_rejected(bundle, "documents.Metadata must be an object")

        bundle = good_bundle()
        bundle["documents"]["PersistedGrant"]["Write"] = "yes"
        self.assert_rejected(bundle, "documents.PersistedGrant.Write must be a boolean")

    def test_rejects_missing_debug_roots_memory_layers_and_snapshots(self):
        bundle = good_bundle()
        bundle["debug_roots"] = []
        self.assert_rejected(bundle, "debug_roots must not be empty")

        bundle = good_bundle()
        del bundle["memory_layers"]["PdockerRss"]
        self.assert_rejected(bundle, "memory_layers.PdockerRss must be numeric")

        bundle = good_bundle()
        bundle["handle_snapshot_text"] = ""
        self.assert_rejected(bundle, "handle_snapshot_text must be a non-empty string")

    def test_rejects_missing_local_evidence_files(self):
        bundle = good_bundle()
        bundle["LocalEvidenceFiles"]["Latest"] = ""
        self.assert_rejected(bundle, "LocalEvidenceFiles.Latest must be a non-empty string")

    def test_requires_active_operations_and_jobs_objects(self):
        bundle = good_bundle()
        del bundle["active_operations"]
        self.assert_rejected(bundle, "active_operations must be an object")

        bundle = good_bundle()
        del bundle["jobs"]
        self.assert_rejected(bundle, "jobs must be an object")

    def test_accepts_explicit_active_operations_and_jobs_collection_errors(self):
        bundle = good_bundle()
        bundle["active_operations"] = {"Error": "daemon socket unavailable"}
        bundle["jobs"] = {"Error": "jobs.json unavailable"}
        tmp, path = self.write_case(bundle)
        with tmp:
            verifier.verify(path)

    def test_rejects_job_count_and_log_excerpt_caps(self):
        bundle = good_bundle()
        base_job = bundle["jobs"]["Items"][0]
        bundle["jobs"]["Items"] = [dict(base_job, id=f"job-{i}", LogPath=f"/data/user/0/io.github.ryo100794.pdocker/files/pdocker/logs/jobs/job-{i}.log") for i in range(11)]
        self.assert_rejected(bundle, "at most 10 jobs")

        bundle = good_bundle()
        bundle["jobs"]["Items"][0]["output"] = [f"line {i}" for i in range(21)]
        self.assert_rejected(bundle, "at most 20 lines")

        bundle = good_bundle()
        bundle["jobs"]["Items"][0].pop("output")
        bundle["jobs"]["Items"][0]["LogExcerpt"] = "x" * 32769
        self.assert_rejected(bundle, "at most 32768 bytes")

        bundle = good_bundle()
        bundle["jobs"]["Items"][0].pop("output")
        bundle["jobs"]["Items"][0]["LogExcerpt"] = {
            "Path": "/data/user/0/io.github.ryo100794.pdocker/files/pdocker/logs/jobs/job-1.log",
            "Exists": True,
            "Bytes": 65536,
            "ExcerptBytes": 32768,
            "Truncated": True,
            "Text": "tail\n",
        }
        tmp, path = self.write_case(bundle)
        with tmp:
            verifier.verify(path)

        bundle = good_bundle()
        bundle["jobs"]["Items"][0].pop("output")
        bundle["jobs"]["Items"][0]["LogExcerpt"] = {
            "Path": "/data/user/0/io.github.ryo100794.pdocker/files/pdocker/logs/jobs/job-1.log",
            "Exists": True,
            "Bytes": 65536,
            "ExcerptBytes": 32769,
            "Truncated": True,
            "Text": "tail\n",
        }
        self.assert_rejected(bundle, "ExcerptBytes must be at most 32768")

    def test_rejects_unsafe_or_non_app_owned_job_log_paths(self):
        bundle = good_bundle()
        bundle["jobs"]["JobLogPathPolicy"] = "external"
        self.assert_rejected(bundle, "JobLogPathPolicy must be app-owned")

        bundle = good_bundle()
        bundle["jobs"]["Items"][0]["LogPath"] = "/sdcard/Download/job-1.log"
        self.assert_rejected(bundle, "app-owned job log path")

        bundle = good_bundle()
        bundle["jobs"]["Items"][0]["LogPath"] = "/data/user/0/io.github.ryo100794.pdocker/files/pdocker/logs/jobs/../secret.log"
        self.assert_rejected(bundle, "app-owned job log path")

    def test_accepts_failed_documents_export_with_explicit_error(self):
        bundle = good_bundle()
        bundle["DocumentsExport"] = failed_export()
        bundle["DocumentsEvidenceExport"] = failed_export("pdocker/diagnostics/self-debug-bundle-1770000000000.json")
        tmp, path = self.write_case(bundle)
        with tmp:
            verifier.verify(path)

    def test_rejects_failed_documents_export_without_explicit_error(self):
        bundle = good_bundle()
        export = failed_export()
        export.pop("Error")
        export["Attempts"] = [{"Success": False, "Mode": "saf"}]
        bundle["DocumentsExport"] = export
        self.assert_rejected(bundle, "DocumentsExport failed/planned export must include an explicit")

    def test_rejects_wrong_documents_paths_and_mime_type(self):
        bundle = good_bundle()
        bundle["DocumentsExport"] = successful_export("pdocker/diagnostics/other.json")
        self.assert_rejected(bundle, "DocumentsExport.Target must be")

        bundle = good_bundle()
        bundle["DocumentsEvidenceExport"] = successful_export("pdocker/diagnostics/self-debug-bundle-latest.json")
        self.assert_rejected(bundle, "DocumentsEvidenceExport.Target must be timestamped")

        bundle = good_bundle()
        bundle["DocumentsExport"]["MimeType"] = "text/plain"
        self.assert_rejected(bundle, "MimeType must be application/json")

    def test_cli_reports_ok(self):
        tmp, path = self.write_case(good_bundle())
        with tmp:
            proc = subprocess.run([sys.executable, str(VERIFIER_PATH), str(path)], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("OK:", proc.stdout)

    def test_runbook_documents_verifier_command(self):
        doc = DOC.read_text(encoding="utf-8")
        self.assertIn("scripts/verify-self-debug-bundle.py", doc)
        self.assertIn("pdocker.self-debug.bundle.v1", doc)
        self.assertIn("DocumentsExport", doc)
        self.assertIn("DocumentsEvidenceExport", doc)
        self.assertIn("active_operations", doc)
        self.assertIn("jobs", doc)
        self.assertIn("32768 bytes", doc)


if __name__ == "__main__":
    unittest.main()

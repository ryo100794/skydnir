import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "verify-storage-metrics.py"

spec = importlib.util.spec_from_file_location("verify_storage_metrics", SCRIPT)
verify_storage_metrics = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = verify_storage_metrics
spec.loader.exec_module(verify_storage_metrics)


def http_json(payload):
    body = json.dumps(payload)
    return (
        "HTTP/1.1 200 OK\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        "\r\n"
        f"{body}"
    )


class Completed:
    def __init__(self, stdout, returncode=0, stderr=""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


class StorageMetricsCaptureTest(unittest.TestCase):
    def test_dry_run_lists_required_device_endpoints_without_adb(self):
        stdout = io.StringIO()

        with mock.patch.object(
            verify_storage_metrics.subprocess,
            "run",
            side_effect=AssertionError("adb should not run during dry-run"),
        ):
            with contextlib.redirect_stdout(stdout):
                rc = verify_storage_metrics.main(["--capture-device", "--dry-run", "--adb", "adb-test"])

        self.assertEqual(rc, 0)
        output = stdout.getvalue()
        self.assertIn("/system/df", output)
        self.assertIn("/images/json", output)
        self.assertIn("/containers/json?all=1&size=1", output)
        self.assertIn("adb-test shell", output)

    def test_capture_device_combines_endpoint_json_and_writes_fixture(self):
        responses = [
            Completed(http_json(verify_storage_metrics.FIXTURE["system_df"])),
            Completed(http_json(verify_storage_metrics.FIXTURE["images"])),
            Completed(http_json(verify_storage_metrics.FIXTURE["containers"])),
        ]

        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / "storage-snapshot.json"
            stdout = io.StringIO()
            with mock.patch.object(
                verify_storage_metrics.subprocess,
                "run",
                side_effect=responses,
            ) as run:
                with contextlib.redirect_stdout(stdout):
                    rc = verify_storage_metrics.main(
                        [
                            "--capture-device",
                            "--adb",
                            "adb-test",
                            "--package",
                            "io.example.pdocker",
                            "--output",
                            str(output),
                        ]
                    )

            self.assertEqual(rc, 0)
            self.assertEqual(run.call_count, 3)
            captured = json.loads(output.read_text())

        self.assertEqual(captured["system_df"], verify_storage_metrics.FIXTURE["system_df"])
        self.assertEqual(captured["images"], verify_storage_metrics.FIXTURE["images"])
        self.assertEqual(captured["containers"], verify_storage_metrics.FIXTURE["containers"])
        self.assertIn("verify-storage-metrics: PASS", stdout.getvalue())

    def test_capture_device_reports_non_json_endpoint_response(self):
        with mock.patch.object(
            verify_storage_metrics.subprocess,
            "run",
            return_value=Completed("HTTP/1.1 500 FAIL\r\n\r\nnot-json"),
        ):
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                rc = verify_storage_metrics.main(["--capture-device"])

        self.assertEqual(rc, 1)
        self.assertIn("did not return JSON", stdout.getvalue())

    def test_fixture_validation_catches_additive_image_view_total(self):
        snapshot = json.loads(json.dumps(verify_storage_metrics.FIXTURE))
        snapshot["system_df"]["TotalBytes"] = (
            snapshot["system_df"]["UniqueBytes"] + snapshot["system_df"]["ImageViewBytes"]
        )

        errors = verify_storage_metrics.validate(snapshot)

        self.assertIn("FAIL: system_df.TotalBytes appears to double count ImageViewBytes", errors)

    def test_fixture_validation_requires_container_upper_semantics_note(self):
        snapshot = json.loads(json.dumps(verify_storage_metrics.FIXTURE))
        snapshot["system_df"]["PdockerStorage"].pop("ContainerUpper")

        errors = verify_storage_metrics.validate(snapshot)

        self.assertIn(
            "FAIL: PdockerStorage notes must describe container upper/private storage",
            errors,
        )

    def test_fixture_validation_catches_rootfs_view_smaller_than_upperdir(self):
        snapshot = json.loads(json.dumps(verify_storage_metrics.FIXTURE))
        snapshot["system_df"]["RootfsViewBytes"] = snapshot["system_df"]["ContainerUpperBytes"] - 1

        errors = verify_storage_metrics.validate(snapshot)

        self.assertIn(
            "FAIL: system_df.RootfsViewBytes must be at least ContainerUpperBytes",
            errors,
        )


if __name__ == "__main__":
    unittest.main()

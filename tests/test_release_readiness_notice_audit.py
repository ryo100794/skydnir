import importlib.util
import tempfile
import unittest
import zipfile
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify-release-readiness.py"
NOTICE_ENTRY = "assets/oss-licenses/THIRD_PARTY_NOTICES.md"
RELEASE_READINESS_WORKFLOW = ROOT / ".github" / "workflows" / "release-readiness.yml"

spec = importlib.util.spec_from_file_location("verify_release_readiness", SCRIPT)
assert spec and spec.loader
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


FULL_NOTICE = """
go-containerregistry
xterm.js
xterm-addon-fit
Chaquopy
CPython
Python 3.11
OpenSSL
SQLite
certificate
AndroidX
Material Components
Kotlin
"""

FULL_INVENTORY = """
APK:lib/arm64-v8a/libchaquopy_java.so Chaquopy
APK:lib/arm64-v8a/libpython3.11.so CPython Python 3.11
APK:lib/arm64-v8a/libcrypto_chaquopy.so OpenSSL
APK:lib/arm64-v8a/libssl_chaquopy.so OpenSSL
APK:lib/arm64-v8a/libsqlite3_chaquopy.so SQLite
APK:assets/chaquopy/cacert.pem certificate
APK:assets/chaquopy/*.imy Chaquopy CPython
APK:assets/chaquopy/bootstrap-native/arm64-v8a/*.so Chaquopy CPython
"""


class ReleaseReadinessPayloadInventoryTest(unittest.TestCase):
    INVENTORY_PREFIX = """# Generated and Prebuilt Payload Inventory\n\nThis is a release-readiness inventory, not F-Droid submission metadata.\n\n| Path | Source or regeneration path | Current release status |\n| --- | --- | --- |\n"""

    def make_root(self, inventory_rows: str) -> Path:
        root = Path(tempfile.mkdtemp(prefix="pdocker-inventory-test-"))
        inventory = root / "metadata" / "fdroid" / "generated-binary-inventory.md"
        inventory.parent.mkdir(parents=True)
        inventory.write_text(self.INVENTORY_PREFIX + inventory_rows, encoding="utf-8")
        return root

    def run_inventory_check(self, root: Path, ignored_paths: set[str] | None = None) -> None:
        payload_dirs = (
            root / "app" / "src" / "main" / "assets" / "pdockerd",
            root / "app" / "src" / "main" / "jniLibs",
            root / "docker-proot-setup" / "docker-bin",
        )
        ignored_paths = ignored_paths or set()
        with mock.patch.object(verifier, "ROOT", root), \
             mock.patch.object(verifier, "INVENTORY", root / "metadata" / "fdroid" / "generated-binary-inventory.md"), \
             mock.patch.object(verifier, "PAYLOAD_DIRS", payload_dirs), \
             mock.patch.object(verifier, "is_gitignored_inventory_path", side_effect=ignored_paths.__contains__):
            verifier.check_payload_inventory()

    def test_allows_missing_generated_staged_app_inventory_rows_on_clean_checkout(self):
        root = self.make_root(
            "| `docker-proot-setup/docker-bin/docker` | External Docker CLI payload. | Prebuilt external binary; blocker. |\n"
            "| `app/src/main/assets/pdockerd/pdockerd` | Copied from `docker-proot-setup/bin/pdockerd` by the Gradle `syncPdockerdAsset` task. | Generated asset; verify by comparing source and staged asset. |\n"
            "| `app/src/main/jniLibs/arm64-v8a/libcow.so` | Built from `docker-proot-setup/src/overlay/libcow.c` by local native build tooling. | Generated binary; must be rebuilt and compared before release. |\n"
        )
        source_payload = root / "docker-proot-setup" / "docker-bin" / "docker"
        source_payload.parent.mkdir(parents=True)
        source_payload.write_bytes(b"\x7fELFfixture")

        self.run_inventory_check(root, {
            "app/src/main/assets/pdockerd/pdockerd",
            "app/src/main/jniLibs/arm64-v8a/libcow.so",
        })

    def test_rejects_missing_non_generated_source_tree_inventory_rows(self):
        root = self.make_root(
            "| `docker-proot-setup/docker-bin/docker` | External Docker CLI payload. | Prebuilt external binary; blocker. |\n"
        )

        with self.assertRaisesRegex(verifier.CheckFailure, "missing source-tree files"):
            self.run_inventory_check(root)


class ReleaseReadinessNoticeAuditTest(unittest.TestCase):
    def make_apk(self, entries: dict[str, str | bytes]) -> Path:
        tmpdir = Path(tempfile.mkdtemp(prefix="pdocker-apk-notice-test-"))
        apk = tmpdir / "fixture.apk"
        with zipfile.ZipFile(apk, "w") as archive:
            for name, value in entries.items():
                data = value.encode("utf-8") if isinstance(value, str) else value
                archive.writestr(name, data)
        return apk

    def audit(self, apk: Path, notice: str = FULL_NOTICE, inventory: str = FULL_INVENTORY) -> None:
        verifier.check_apk_notice_audit(
            required=True,
            apk_paths=[apk],
            notice_source_text=notice,
            top_notice_text=notice,
            inventory_text=inventory,
        )

    def test_passes_with_resolved_runtime_payload_notice_coverage(self):
        apk = self.make_apk(
            {
                NOTICE_ENTRY: FULL_NOTICE,
                "lib/arm64-v8a/libpython3.11.so": b"",
                "lib/arm64-v8a/libssl_chaquopy.so": b"",
                "assets/chaquopy/bootstrap-native/arm64-v8a/mmap.so": b"",
                "assets/chaquopy/stdlib-common.imy": b"",
                "assets/chaquopy/cacert.pem": b"",
            }
        )

        self.audit(apk)

    def test_fails_when_notice_asset_is_missing(self):
        apk = self.make_apk({"lib/arm64-v8a/libssl_chaquopy.so": b""})

        with self.assertRaises(verifier.CheckFailure):
            self.audit(apk)

    def test_fails_when_resolved_payload_lacks_notice_token(self):
        notice_without_openssl = FULL_NOTICE.replace("OpenSSL\n", "")
        inventory_without_openssl = FULL_INVENTORY.replace("OpenSSL", "")
        apk = self.make_apk(
            {
                NOTICE_ENTRY: notice_without_openssl,
                "lib/arm64-v8a/libssl_chaquopy.so": b"",
            }
        )

        with self.assertRaises(verifier.CheckFailure):
            self.audit(apk, notice=notice_without_openssl, inventory=inventory_without_openssl)

    def test_rejects_forbidden_legacy_payloads(self):
        apk = self.make_apk(
            {
                NOTICE_ENTRY: FULL_NOTICE,
                "lib/arm64-v8a/libproot.so": b"",
            }
        )

        with self.assertRaises(verifier.CheckFailure):
            self.audit(apk)


class ReleaseReadinessWorkflowTriggerTest(unittest.TestCase):
    def test_workflow_path_filter_covers_release_readiness_inputs(self):
        workflow = RELEASE_READINESS_WORKFLOW.read_text(encoding="utf-8")
        required_paths = {
            "docs/build/**",
            "docs/release/**",
            "metadata/fdroid/**",
            "THIRD_PARTY_NOTICES.md",
            "app/src/main/assets/oss-licenses/THIRD_PARTY_NOTICES.md",
            "scripts/verify-release-readiness.py",
        }

        for path in required_paths:
            with self.subTest(path=path):
                self.assertGreaterEqual(
                    workflow.count(f"- {path}"),
                    2,
                    f"{path} must trigger release-readiness on pull_request and push",
                )


if __name__ == "__main__":
    unittest.main()

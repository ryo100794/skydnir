import importlib.util
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify-release-readiness.py"
NOTICE_ENTRY = "assets/oss-licenses/THIRD_PARTY_NOTICES.md"

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


if __name__ == "__main__":
    unittest.main()

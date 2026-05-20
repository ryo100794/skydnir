import importlib.util
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify-native-payloads.py"

spec = importlib.util.spec_from_file_location("verify_native_payloads", SCRIPT)
assert spec and spec.loader
verifier = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = verifier
spec.loader.exec_module(verifier)


class NativePayloadVerifierSyntheticApkTest(unittest.TestCase):
    """Small APK fixtures for native payload APK policy checks.

    The full native payload verifier intentionally inspects checked-in ELF
    binaries and, when present, the built compat APK.  These synthetic fixtures
    pin the APK-entry/source-mirror logic without depending on a large Gradle
    output artifact, so regression gates can fail quickly when packaging policy
    changes.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pdocker-native-payload-synth-"))
        self.original_root = verifier.ROOT
        self.original_specs = verifier.SPECS
        self.original_assets = verifier.APK_ASSETS
        self.original_source_by_entry = verifier.SOURCE_MIRROR_BY_APK_ENTRY
        self.original_source_by_payload = verifier.SOURCE_MIRROR_BY_PAYLOAD
        verifier.ROOT = self.tmp
        verifier.SPECS = ()
        verifier.APK_ASSETS = (
            verifier.ApkAssetSpec(
                "assets/pdockerd/pdockerd",
                "docker-proot-setup/bin/pdockerd",
                "pdockerd-daemon",
            ),
        )
        verifier.SOURCE_MIRROR_BY_APK_ENTRY = {}
        verifier.SOURCE_MIRROR_BY_PAYLOAD = {}
        self.source = self.tmp / "docker-proot-setup" / "bin" / "pdockerd"
        self.source.parent.mkdir(parents=True)
        self.source.write_bytes(b"pdockerd synthetic source\n")

    def tearDown(self):
        verifier.ROOT = self.original_root
        verifier.SPECS = self.original_specs
        verifier.APK_ASSETS = self.original_assets
        verifier.SOURCE_MIRROR_BY_APK_ENTRY = self.original_source_by_entry
        verifier.SOURCE_MIRROR_BY_PAYLOAD = self.original_source_by_payload
        shutil.rmtree(self.tmp)

    def write_apk(self, name: str, entries: dict[str, bytes]) -> Path:
        apk = self.tmp / name
        with zipfile.ZipFile(apk, "w") as zf:
            for entry, content in entries.items():
                zf.writestr(entry, content)
        return apk

    def test_synthetic_apk_asset_matches_source(self):
        apk = self.write_apk(
            "ok.apk",
            {"assets/pdockerd/pdockerd": self.source.read_bytes()},
        )

        result = verifier.verify_payloads(apk=apk)

        self.assertTrue(result["success"], result["errors"])
        self.assertEqual([], result["apk"]["missing_entries"])
        self.assertEqual([], result["apk"]["forbidden_prefix_entries"])
        detail = result["apk"]["payload_details"][0]
        self.assertEqual("pdockerd-daemon", detail["role"])
        self.assertTrue(detail["same_bytes_as_source"])

    def test_synthetic_apk_rejects_missing_required_asset(self):
        apk = self.write_apk("missing.apk", {})

        result = verifier.verify_payloads(apk=apk)

        self.assertFalse(result["success"])
        self.assertEqual(["assets/pdockerd/pdockerd"], result["apk"]["missing_entries"])
        self.assertIn(
            "APK missing native payload entries: assets/pdockerd/pdockerd",
            result["errors"],
        )

    def test_synthetic_apk_rejects_stale_asset_bytes(self):
        apk = self.write_apk(
            "stale.apk",
            {"assets/pdockerd/pdockerd": b"old daemon bytes\n"},
        )

        result = verifier.verify_payloads(apk=apk)

        self.assertFalse(result["success"])
        detail = result["apk"]["payload_details"][0]
        self.assertFalse(detail["same_bytes_as_source"])
        self.assertIn(
            "APK entry assets/pdockerd/pdockerd differs from source "
            "docker-proot-setup/bin/pdockerd",
            result["errors"],
        )

    def test_synthetic_apk_rejects_pyc_cache_entries(self):
        apk = self.write_apk(
            "cache.apk",
            {
                "assets/pdockerd/pdockerd": self.source.read_bytes(),
                "assets/pdockerd/__pycache__/pdockerd.cpython-313.pyc": b"cache",
            },
        )

        result = verifier.verify_payloads(apk=apk)

        self.assertFalse(result["success"])
        self.assertEqual(
            ["assets/pdockerd/__pycache__/pdockerd.cpython-313.pyc"],
            result["apk"]["forbidden_prefix_entries"],
        )
        self.assertIn(
            "APK includes forbidden generated cache entries: "
            "assets/pdockerd/__pycache__/pdockerd.cpython-313.pyc",
            result["errors"],
        )


if __name__ == "__main__":
    unittest.main()

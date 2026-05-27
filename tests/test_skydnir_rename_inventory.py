import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INVENTORY_SCRIPT = ROOT / "scripts" / "maintenance" / "generate-skydnir-rename-inventory.py"
PLAN = ROOT / "docs" / "plan" / "SKYDNIR_RENAME_PLAN.md"
LATEST_JSON = ROOT / "docs" / "maintenance" / "skydnir-rename-inventory-latest.json"
LATEST_MD = ROOT / "docs" / "maintenance" / "skydnir-rename-inventory-latest.md"


class SkydnirRenameInventoryTest(unittest.TestCase):
    def test_inventory_script_classifies_required_name_surfaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            json_out = Path(tmp) / "inventory.json"
            md_out = Path(tmp) / "inventory.md"
            subprocess.run(
                [
                    "python3",
                    str(INVENTORY_SCRIPT),
                    "--snapshot-date",
                    "2026-05-27",
                    "--json-out",
                    str(json_out),
                    "--md-out",
                    str(md_out),
                ],
                cwd=ROOT,
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            )
            data = json.loads(json_out.read_text(encoding="utf-8"))
            md = md_out.read_text(encoding="utf-8")

        self.assertEqual("skydnir.rename.inventory.v1", data["schema"])
        self.assertEqual("2026-05-27", data["snapshot_date"])
        self.assertGreater(data["entry_count"], 1000)
        for token in ["pdocker", "pdockerd", "PDOCKER", "pdocker-android"]:
            self.assertIn(token, data["counts"]["by_token"])
        for category in [
            "cli_command",
            "daemon_binary_or_service",
            "environment_variable",
            "artifact_schema",
            "socket_or_storage_path",
            "historical_evidence",
        ]:
            self.assertIn(category, data["counts"]["by_category"])
        self.assertEqual(0, data["counts"]["by_category"].get("public_branding", 0))
        self.assertIn("Do not rename `environment_variable`", md)

    def test_committed_inventory_is_current_phase_zero_evidence(self):
        data = json.loads(LATEST_JSON.read_text(encoding="utf-8"))
        md = LATEST_MD.read_text(encoding="utf-8")

        self.assertEqual("skydnir.rename.inventory.v1", data["schema"])
        self.assertEqual("2026-05-27", data["snapshot_date"])
        self.assertEqual(data["entry_count"], sum(data["counts"]["by_token"].values()))
        self.assertEqual(0, data["counts"]["by_phase"].get("phase-1-public-branding", 0))
        self.assertIn("phase-5-dual-read-required", data["counts"]["by_phase"])
        self.assertIn("Skydnir Rename Inventory", md)

    def test_rename_plan_forbids_unsafe_broad_replacement(self):
        text = PLAN.read_text(encoding="utf-8")

        self.assertIn("must not be implemented as a broad one-shot text replacement", text)
        self.assertIn("Each usage must be classified before it is changed", text)
        self.assertIn("Keep old artifact schemas readable", text)
        self.assertIn("Do not rename `PDOCKER_*` environment variables", text)
        self.assertIn("Phase 0: Inventory and Guard Rails", text)


if __name__ == "__main__":
    unittest.main()

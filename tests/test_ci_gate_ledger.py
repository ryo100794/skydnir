import json
import re
import shlex
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "docs" / "test" / "CI_GATE_LEDGER.md"
MANIFEST = ROOT / "tests" / "test_driver_manifest.json"
VERIFY_FAST = ROOT / "scripts" / "verify-fast.sh"

FAST_STATIC_VERIFY_FAST_EXEMPTIONS: dict[str, str] = {}


class CiGateLedgerTest(unittest.TestCase):
    def setUp(self):
        self.ledger = LEDGER.read_text(encoding="utf-8")
        self.manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.verify_fast = VERIFY_FAST.read_text(encoding="utf-8")

    def _gate_table_rows(self):
        in_table = False
        for line in self.ledger.splitlines():
            if line.strip() == "## Gate table":
                in_table = True
                continue
            if not in_table:
                continue
            if line.startswith("## "):
                break
            if not line.startswith("|"):
                continue
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if len(cells) != 7 or cells[0] in {"Gate", "---"}:
                continue
            yield cells

    @staticmethod
    def _backticked_commands(markdown_cell: str) -> list[str]:
        return re.findall(r"`([^`]+)`", markdown_cell)

    @staticmethod
    def _ledger_command_tokens(command: str) -> list[str]:
        cleaned = command.replace("\\|", "|")
        return shlex.split(cleaned)

    @classmethod
    def _ledger_command_units(cls, command: str) -> list[str]:
        tokens = cls._ledger_command_tokens(command)
        if not tokens:
            return []
        if tokens[0] == "python3" and len(tokens) >= 3 and tokens[1:3] == ["-m", "unittest"]:
            return [
                token
                for token in tokens[3:]
                if token.startswith("tests.") and "<" not in token and ">" not in token
            ]
        script_units: list[str] = []
        for token in tokens:
            if "<" in token or ">" in token:
                continue
            if token.startswith("scripts/") or token.startswith("docker-proot-setup/scripts/"):
                script_units.append(token)
        return script_units

    def test_fast_static_ledger_commands_are_represented_in_verify_fast(self):
        missing: list[str] = []
        for row in self._gate_table_rows():
            fast_static_cell = row[3]
            for command in self._backticked_commands(fast_static_cell):
                if command in FAST_STATIC_VERIFY_FAST_EXEMPTIONS:
                    self.assertTrue(FAST_STATIC_VERIFY_FAST_EXEMPTIONS[command].strip(), command)
                    continue
                if not command.startswith(("python3 ", "bash ", "scripts/")):
                    continue
                units = self._ledger_command_units(command)
                if not units:
                    continue
                for unit in units:
                    if unit not in self.verify_fast:
                        missing.append(f"{command!r} requires {unit!r}")

        self.assertEqual(
            [],
            missing,
            "CI fast/static gate commands must be represented in scripts/verify-fast.sh "
            "or added to FAST_STATIC_VERIFY_FAST_EXEMPTIONS with a reason",
        )

    def test_p0_p1_gate_table_names_all_current_focus_areas(self):
        for term in [
            "Service truth",
            "Runtime teardown",
            "Image pull crash safety",
            "OOM/LMK",
            "Single-container execution",
            "Modern/no-PRoot runtime truth",
            "Direct `linkat` hardlink semantics",
            "Docker CLI `docker cp` end-to-end",
            "Storage graph/layer maintenance UI",
            "Media bridge capture/playback",
            "Terminal `-it`",
            "llama GPU correctness",
            "status=planned-gap",
            "success=false",
        ]:
            self.assertIn(term, self.ledger)

    def test_host_smoke_keeps_planned_gap_contracts_visible(self):
        host_ids = {command["id"] for command in self.manifest["lanes"]["host-smoke"]["commands"]}
        for command_id in [
            "verify-service-truth-plan",
            "verify-image-pull-crash-safety",
            "unittest-all",
        ]:
            self.assertIn(command_id, host_ids)
        for ledger_only in [
            "verify-memory-pager-design",
            "verify-llama-gpu-artifact.py",
        ]:
            self.assertIn(ledger_only, self.ledger)

    def test_device_gates_require_non_passing_artifacts_until_proven(self):
        for artifact in [
            "docs/test/service-truth-latest.json",
            "docs/test/runtime-teardown-latest.json",
            "docs/test/image-pull-crash-safety-latest.json",
            "docs/test/test-run-latest.json",
            "docs/test/no-proot-runtime-truth-latest.json",
            "docs/test/linkat-hardlink-semantics-latest.json",
            "files/pdocker/diagnostics/docker-cp-e2e-latest.json",
            "docs/test/storage-layer-maintenance-ui-latest.json",
            "docs/test/media-bridge-capture-playback-latest.json",
            "docs/test/dev-workspace-compose-latest.json",
            "docs/test/saf-direct-output-latest.json",
            "docs/test/storage-metrics-sequence-latest.json",
            "docs/test/llama-gpu-q6k-workflow-latest.json",
        ]:
            self.assertIn(artifact, self.ledger)
        self.assertIn("must not silently pass", self.ledger)

    def test_planned_gap_lanes_are_non_promoting_for_stable_checkpoint(self):
        policy = self.manifest["policy"]
        self.assertIn("stable-checkpoint eligible only", policy["stable_checkpoint_rule"])
        self.assertIn("planned-gap", policy["non_promoting_statuses"])
        self.assertIn("success=false", self.ledger)
        self.assertIn("stable checkpoint", self.ledger)

        for lane_name in [
            "host-smoke",
            "cow-overlay-bench-recovery",
            "android-test-suite",
            "android-documents",
            "android-dev-workspace",
            "android-memory-pager",
            "governance",
            "release-honesty",
        ]:
            self.assertFalse(
                self.manifest["lanes"][lane_name]["stable_checkpoint_eligible"],
                lane_name,
            )

        release_lane = self.manifest["lanes"]["release-honesty"]
        self.assertEqual(release_lane["checkpoint_class"], "release-blocker-ledger-only")
        self.assertEqual(release_lane["commands"][0]["id"], "verify-release-readiness-honesty")


if __name__ == "__main__":
    unittest.main()

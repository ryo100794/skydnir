import json
import stat
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "android-dev-workspace-compose-smoke.sh"
MANIFEST = ROOT / "tests" / "test_driver_manifest.json"


class DevWorkspaceSmokeContractTest(unittest.TestCase):
    def setUp(self):
        self.script = SCRIPT.read_text()
        self.manifest = json.loads(MANIFEST.read_text())

    def test_script_is_executable_and_uses_app_compose_route(self):
        mode = SCRIPT.stat().st_mode
        self.assertTrue(mode & stat.S_IXUSR, "dev workspace smoke script must be executable")
        self.assertIn("ACTION_PREFIX.action.SMOKE_COMPOSE_UP", self.script)
        self.assertIn('PROJECT="${PDOCKER_DEV_WORKSPACE_PROJECT:-default}"', self.script)
        self.assertIn('SERVICE="${PDOCKER_DEV_WORKSPACE_SERVICE:-dev}"', self.script)
        self.assertIn('CONTAINER="${PDOCKER_DEV_WORKSPACE_CONTAINER:-pdocker-dev}"', self.script)
        self.assertIn('PORT="${PDOCKER_DEV_WORKSPACE_PORT:-18080}"', self.script)
        self.assertIn("--es project \"$PROJECT\"", self.script)
        self.assertNotIn("docker compose up", self.script)
        self.assertNotIn("docker build", self.script)

    def test_script_collects_required_evidence_without_fake_success(self):
        for token in [
            "containers-json-latest.json",
            "container-inspect.json",
            "container-logs.txt",
            "extensions.out",
            "extensions-inspect.json",
            "ui-rendered-service-truth-latest.json",
            "listener-http.raw",
            "proc-net-tcp.txt",
            "process-table.txt",
            "job-logs",
            "code-server --list-extensions",
            "Continue.continue OpenAI.chatgpt Anthropic.claude-code",
            "Successfully tagged pdocker/dev-workspace:latest",
            "Using image cache for pdocker/dev-workspace:latest",
        ]:
            self.assertIn(token, self.script)

        for check in ["build_run", "engine_state", "listener", "code_server_http", "extensions", "ui_truth"]:
            self.assertIn(f'"{check}"', self.script)
        self.assertIn("engine_state_current", self.script)
        self.assertIn("code_server_http_ok", self.script)
        self.assertIn("extensions_configured", self.script)
        self.assertIn("all_checks_ok = all(item[\"ok\"] for item in checks.values())", self.script)
        self.assertIn("success = all_checks_ok and not failures", self.script)
        self.assertIn("no_fake_success", self.script)
        self.assertIn("extensions_if_configured", self.script)

    def test_ui_truth_must_be_current_and_matching_not_stale_or_unknown(self):
        for token in [
            '"unknown", "stale", "ambiguous"',
            'card.get("TruthState") == "current"',
            "EngineContainerId",
            "running_id",
            "ui_truth_ok = bool(ui_current_match) and not stale_or_unknown_cards",
            "TruthState stale/unknown/ambiguous is never accepted as success",
        ]:
            self.assertIn(token, self.script)

    def test_manifest_has_device_lane_for_dev_workspace_gate(self):
        lane = self.manifest["lanes"].get("android-dev-workspace")
        self.assertIsInstance(lane, dict)
        commands = lane.get("commands") or []
        self.assertEqual(len(commands), 1)
        command = commands[0]
        self.assertEqual(command.get("id"), "android-dev-workspace-compose-smoke")
        self.assertIn("bash scripts/android-dev-workspace-compose-smoke.sh", command.get("shell") or "")
        self.assertIn("verify-dev-workspace-compose-artifact.py", command.get("shell") or "")
        self.assertIn("rm -f docs/test/dev-workspace-compose-latest.json", command.get("shell") or "")
        self.assertIn("docs/test/dev-workspace-compose-latest.json", command.get("artifacts") or [])
        self.assertIn("installed-debug-apk", lane.get("requires") or [])


if __name__ == "__main__":
    unittest.main()

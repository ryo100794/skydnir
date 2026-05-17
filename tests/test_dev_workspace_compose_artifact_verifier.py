import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERIFIER_PATH = ROOT / "scripts" / "verify-dev-workspace-compose-artifact.py"

spec = importlib.util.spec_from_file_location("dev_workspace_compose_verifier", VERIFIER_PATH)
verifier = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(verifier)

CID = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
REQUIRED_EXTENSIONS = ["Continue.continue", "OpenAI.chatgpt", "Anthropic.claude-code"]


def good_artifact():
    return {
        "schema": "pdocker.android.dev-workspace.compose-smoke.v1",
        "success": True,
        "status": "pass",
        "project": "default",
        "service": "dev",
        "container": "pdocker-dev",
        "port": 18080,
        "required_extensions": list(REQUIRED_EXTENSIONS),
        "flow_exit_code": 0,
        "failures": [],
        "checks": {
            "build_run": {
                "ok": True,
                "build_started_observed": True,
                "build_completed_observed": True,
                "container_create_observed": True,
                "container_start_observed": True,
                "container_started_observed": True,
                "service_url_observed": True,
                "build_failed_marker_observed": False,
                "running_container_id": CID,
                "container_state": "running",
                "container_status": "Up 3 seconds",
            },
            "engine_state": {
                "ok": True,
                "container": "pdocker-dev",
                "running_container_id": CID,
                "inspect_id": CID,
                "inspect_name": "pdocker-dev",
                "inspect_running": True,
                "containers_json_state": "running",
                "containers_json_status": "Up 3 seconds",
            },
            "listener": {
                "ok": True,
                "port": 18080,
                "http_probe_exit_code": "0",
                "http_status_line": "HTTP/1.1 302 Found",
                "http_status_code": 302,
            },
            "code_server_http": {
                "ok": True,
                "url": "http://127.0.0.1:18080/",
                "expected_status": "HTTP 2xx/3xx from code-server",
                "http_status_line": "HTTP/1.1 302 Found",
                "http_status_code": 302,
            },
            "extensions": {
                "ok": True,
                "configured": True,
                "required": list(REQUIRED_EXTENSIONS),
                "present": {ext: True for ext in REQUIRED_EXTENSIONS},
                "exec_exit_code": 0,
            },
            "ui_truth": {
                "ok": True,
                "target_card_count": 1,
                "current_match": True,
                "stale_or_unknown_target_card_count": 0,
                "target_cards": [{"TruthState": "current", "EngineContainerId": CID[:12]}],
            },
        },
    }


class DevWorkspaceComposeArtifactVerifierTest(unittest.TestCase):
    def write_case(self, artifact):
        tmp = tempfile.TemporaryDirectory()
        path = Path(tmp.name) / "dev-workspace-compose-latest.json"
        path.write_text(json.dumps(artifact), encoding="utf-8")
        return tmp, path

    def test_accepts_synthetic_pass_artifact(self):
        tmp, path = self.write_case(good_artifact())
        with tmp:
            verifier.verify(path)

    def test_rejects_fake_success_when_required_check_is_false(self):
        artifact = good_artifact()
        artifact["checks"]["code_server_http"]["ok"] = False
        tmp, path = self.write_case(artifact)
        with tmp:
            with self.assertRaisesRegex(verifier.VerificationError, "check code_server_http must be ok=true"):
                verifier.verify(path)

    def test_rejects_top_level_success_without_ui_current_match(self):
        artifact = good_artifact()
        artifact["checks"]["ui_truth"]["current_match"] = False
        artifact["checks"]["ui_truth"]["stale_or_unknown_target_card_count"] = 1
        tmp, path = self.write_case(artifact)
        with tmp:
            with self.assertRaisesRegex(verifier.VerificationError, "ui_truth must have current_match=true"):
                verifier.verify(path)

    def test_rejects_configured_extensions_missing_from_artifact(self):
        artifact = good_artifact()
        artifact["checks"]["extensions"]["present"]["OpenAI.chatgpt"] = False
        tmp, path = self.write_case(artifact)
        with tmp:
            with self.assertRaisesRegex(verifier.VerificationError, "required extensions missing: OpenAI.chatgpt"):
                verifier.verify(path)

    def test_accepts_unconfigured_extensions_only_when_required_list_is_empty(self):
        artifact = good_artifact()
        artifact["required_extensions"] = []
        artifact["checks"]["extensions"] = {
            "ok": True,
            "configured": False,
            "required": [],
            "present": {},
            "exec_exit_code": None,
        }
        tmp, path = self.write_case(artifact)
        with tmp:
            verifier.verify(path)

    def test_rejects_planned_or_failed_artifacts_as_non_promoting(self):
        for status in ["planned-gap", "fail"]:
            artifact = good_artifact()
            artifact["status"] = status
            artifact["success"] = False
            tmp, path = self.write_case(artifact)
            with tmp:
                with self.assertRaisesRegex(verifier.VerificationError, "not promotion eligible"):
                    verifier.verify(path)

    def test_rejects_planned_fake_success(self):
        artifact = good_artifact()
        artifact["status"] = "planned-gap"
        artifact["success"] = True
        tmp, path = self.write_case(artifact)
        with tmp:
            with self.assertRaisesRegex(verifier.VerificationError, "non-promoting status planned-gap must set success=false"):
                verifier.verify(path)


if __name__ == "__main__":
    unittest.main()

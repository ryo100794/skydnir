import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / "scripts" / "android-device-smoke.sh"
DOC = ROOT / "docs" / "test" / "DOCKER_CP_E2E_DEVICE_GATE.md"

class DockerCpDeviceGateTest(unittest.TestCase):
    def setUp(self):
        self.smoke = SMOKE.read_text()
        self.doc = DOC.read_text()

    def test_scaffold_is_non_promoting_and_device_gated(self):
        for required in [
            "--docker-cp-e2e", "docker_cp_e2e_acceptance_entrypoint",
            '"Kind": "docker-cp-e2e"', '"Status": "planned-gap"',
            '"Success": false', '"RequiresAdb": true',
            '"HostStaticVerifierCannotPromote": true', '"NoGpuRequired": true',
            '"NoTerminalRequired": true', '"NoNetworkRequired": true', "exit 2",
        ]:
            self.assertIn(required, self.smoke)
        self.assertNotIn('"Success": true', self.smoke)

    def test_evidence_and_negative_cases_are_explicit(self):
        for required in [
            "same Engine container ID", "docker cp host-to-container",
            "container-to-host", "HEAD /containers/{id}/archive",
            "GET /containers/{id}/archive", "PUT /containers/{id}/archive",
            "X-Docker-Container-Path-Stat", "byte and sha256 equality",
            "hardlink", "symlink no-follow policy", "mode, mtime, uid/gid policy",
            "user.* xattr", "reserved whiteout", "escaping hardlink",
            "absolute symlink", "negative-cli-exit-zero-only.json",
            "negative-host-only.json", "negative-network-pull-required.json",
            "negative-terminal-required.json",
        ]:
            self.assertIn(required, self.smoke)

    def test_doc_matches_contract(self):
        for required in [
            "Docker CP End-to-End Device Gate", "Status: planned-gap",
            "Success: false", "scripts/android-device-smoke.sh --docker-cp-e2e",
            "files/pdocker/diagnostics/docker-cp-e2e-latest.json",
            "same Engine container ID", "Byte and `sha256` equality",
            "Hardlink preservation", "symlink no-follow behavior", "uid/gid policy",
            "user.*` xattr", "X-Docker-Container-Path-Stat",
            "HostStaticVerifierCannotPromote", "NoGpuRequired",
            "NoTerminalRequired", "NoNetworkRequired", "not\nsufficient",
        ]:
            self.assertIn(required, self.doc)

if __name__ == "__main__":
    unittest.main()

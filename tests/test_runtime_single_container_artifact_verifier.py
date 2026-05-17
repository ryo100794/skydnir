import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERIFIER_PATH = ROOT / "scripts" / "verify-runtime-single-container-artifact.py"

spec = importlib.util.spec_from_file_location("runtime_single_container_verifier", VERIFIER_PATH)
verifier = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(verifier)

CID = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"


def good_artifact():
    return {
        "schema": "pdocker.android.runtime-single-container-echo-hi.v1",
        "status": "pass",
        "success": True,
        "command": "docker run --rm ubuntu:22.04 echo hi",
        "effective_command": "docker run --cidfile <diagnostic-cidfile> --rm ubuntu:22.04 echo hi",
        "exit_code": 0,
        "stdout_exact": "hi",
        "stdout_exact_match": True,
        "stderr_empty": True,
        "container_id": CID,
        "container_id_source": "docker --cidfile",
        "host_shell_fallback": False,
        "evidence": {
            "stdout": "files/pdocker/diagnostics/docker-run-rm-ubuntu-echo-hi.stdout",
            "stderr": "files/pdocker/diagnostics/docker-run-rm-ubuntu-echo-hi.stderr",
            "combined_log": "files/pdocker/diagnostics/docker-run-rm-ubuntu-echo-hi.log",
            "cidfile": "files/pdocker/diagnostics/docker-run-rm-ubuntu-echo-hi.cid",
        },
    }


class RuntimeSingleContainerArtifactVerifierTest(unittest.TestCase):
    def write_case(self, artifact):
        tmp = tempfile.TemporaryDirectory()
        path = Path(tmp.name) / "runtime-single-container-echo-hi-latest.json"
        path.write_text(json.dumps(artifact), encoding="utf-8")
        return tmp, path

    def assert_rejected(self, artifact, pattern):
        tmp, path = self.write_case(artifact)
        with tmp:
            with self.assertRaisesRegex(verifier.VerificationError, pattern):
                verifier.verify(path)

    def test_accepts_synthetic_pass_artifact(self):
        tmp, path = self.write_case(good_artifact())
        with tmp:
            self.assertEqual(verifier.verify(path)["container_id"], CID)

    def test_rejects_missing_exact_stdout_hi(self):
        artifact = good_artifact()
        artifact["stdout_exact"] = "hi\n"
        self.assert_rejected(artifact, "stdout_exact must be exactly")

    def test_rejects_stdout_match_flag_false_even_with_success_true(self):
        artifact = good_artifact()
        artifact["stdout_exact_match"] = False
        self.assert_rejected(artifact, "stdout_exact_match must be true")

    def test_rejects_missing_exit_code_zero(self):
        artifact = good_artifact()
        artifact["exit_code"] = 1
        self.assert_rejected(artifact, "exit_code must be numeric 0")

    def test_rejects_string_exit_code_zero(self):
        artifact = good_artifact()
        artifact["exit_code"] = "0"
        self.assert_rejected(artifact, "exit_code must be numeric 0")

    def test_rejects_missing_real_64_hex_container_id(self):
        for bad_id in ["", CID[:12], "g" * 64, "0" * 64, CID.upper()]:
            with self.subTest(bad_id=bad_id):
                artifact = good_artifact()
                artifact["container_id"] = bad_id
                self.assert_rejected(artifact, "container_id must be a real lowercase 64-hex")

    def test_rejects_host_shell_fallback(self):
        artifact = good_artifact()
        artifact["host_shell_fallback"] = True
        self.assert_rejected(artifact, "host_shell_fallback must be false")

    def test_rejects_planned_gap_and_blocked_statuses(self):
        for status in ["planned-gap", "blocked"]:
            with self.subTest(status=status):
                artifact = good_artifact()
                artifact["status"] = status
                artifact["success"] = False
                self.assert_rejected(artifact, f"non-promoting status {status} is not promotion eligible")

    def test_rejects_non_promoting_fake_success(self):
        artifact = good_artifact()
        artifact["status"] = "planned-gap"
        artifact["success"] = True
        self.assert_rejected(artifact, "non-promoting status planned-gap must set success=false")

    def test_rejects_stale_or_missing_command(self):
        stale = good_artifact()
        stale["command"] = "docker run ubuntu:22.04 echo hi"
        self.assert_rejected(stale, "stale/missing command")

        missing = good_artifact()
        del missing["command"]
        self.assert_rejected(missing, "stale/missing command")

    def test_rejects_stale_effective_command_without_cidfile(self):
        artifact = good_artifact()
        artifact["effective_command"] = "docker run --rm ubuntu:22.04 echo hi"
        self.assert_rejected(artifact, "effective_command must be exactly")

    def test_rejects_no_evidence_links(self):
        for evidence in [None, {}, {"stdout": ""}]:
            with self.subTest(evidence=evidence):
                artifact = good_artifact()
                artifact["evidence"] = evidence
                self.assert_rejected(artifact, "evidence must be an object|missing device evidence links")

    def test_rejects_evidence_link_not_from_expected_diagnostics(self):
        artifact = good_artifact()
        artifact["evidence"] = copy.deepcopy(artifact["evidence"])
        artifact["evidence"]["stdout"] = "files/pdocker/diagnostics/other.stdout"
        self.assert_rejected(artifact, "evidence link stdout")

    def test_rejects_non_cidfile_container_id_source(self):
        artifact = good_artifact()
        artifact["container_id_source"] = "synthetic"
        self.assert_rejected(artifact, "container_id_source must be docker --cidfile")


if __name__ == "__main__":
    unittest.main()

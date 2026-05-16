import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERIFIER_PATH = ROOT / "scripts" / "verify-terminal-exec-it-artifact.py"
SMOKE_SCRIPT_PATH = ROOT / "scripts" / "android-device-smoke.sh"

spec = importlib.util.spec_from_file_location("terminal_exec_it_verifier", VERIFIER_PATH)
verifier = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(verifier)


REQUIRED = list(verifier.REQUIRED_EVIDENCE)
CID = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
EXEC_ID = "abcdef123456"


def good_artifact():
    tail = "\n".join(
        [
            "pdocker-ui-it-ok",
            "pdocker-ui-it-bracket-ok",
            "pdocker-ui-it-tty-ok",
            "pdocker-ui-it-term-ok",
            "pdocker-ui-it-bash-ok",
            "pdocker-ui-it-top-ok",
            "pdocker-ui-it-arrow-seed",
            "pdocker-ui-it-arrow-seed",
            "PID USER PR NI VIRT RES SHR S %CPU %MEM TIME+ COMMAND",
            "pdocker-ui-it-ime-enter-ok",
            "pdocker-ui-it-topq-ok",
            "pdocker-ui-it-ctrlc-ok",
        ]
    ) + "\n"
    return {
        "Name": "ui-engine-exec-it",
        "Success": True,
        "Container": CID,
        "RequestedContainer": CID[:12],
        "RequiredEvidence": REQUIRED,
        "Evidence": {name: True for name in REQUIRED},
        "OutputTail": tail,
        "EngineExecDiagnostics": "this embedded field is not enough by itself",
    }


def good_events():
    return [
        {"event": "start", "container": CID, "timestampMs": 1},
        {"event": "create-response", "timestampMs": 2, "status": 201, "body": json.dumps({"Id": EXEC_ID}), "execId": ""},
        {"event": "created", "timestampMs": 3, "execId": EXEC_ID},
        {"event": "resize", "timestampMs": 4, "execId": EXEC_ID, "status": 201, "body": f"/exec/{EXEC_ID}/resize?h=24&w=80"},
        {"event": "start-response", "timestampMs": 5, "execId": EXEC_ID, "body": "HTTP/1.1 101 UPGRADED\r\n\r\n"},
        {"event": "stream-started", "timestampMs": 6, "execId": EXEC_ID},
        {"event": "input", "timestampMs": 7, "bytes": 220, "hex": "70 3d 70 64 6f", "text": "p=pdocker-ui-it\necho ${p}-ok\n/usr/bin/[ \"x\" = \"x\" ] && echo ${p}-bracket-ok\npwd\n[ -t 0 ] && echo ${p}-tty-ok\n[ \"$TERM\" = \"xterm-256color\" ] && echo ${p}-term-ok\n[ -n \"$BASH_VERSION\" ] && echo ${p}-bash-ok\ntop -b -n 1 >/dev/null && echo ${p}-top-ok\necho ${p}-arrow-seed\n"},
        {"event": "input", "timestampMs": 8, "bytes": 33, "hex": "70 72 69 6e 74 66 0d", "text": "printf '%s\\n' \"$p-ime-enter-ok\""},
        {"event": "input", "timestampMs": 9, "bytes": 1, "hex": "0d", "text": "\r"},
        {"event": "input", "timestampMs": 10, "bytes": 4, "hex": "1b 5b 41 0d", "text": "\\e[A\r"},
        {"event": "input", "timestampMs": 11, "bytes": 4, "hex": "74 6f 70 0d", "text": "top\r"},
        {"event": "input", "timestampMs": 12, "bytes": 1, "hex": "71", "text": "q"},
        {"event": "input", "timestampMs": 13, "bytes": 9, "hex": "73 6c 65 65 70 20 31 35 0d", "text": "sleep 15\r"},
        {"event": "input", "timestampMs": 14, "bytes": 1, "hex": "03", "text": "\u0003"},
        {"event": "input", "timestampMs": 15, "bytes": 30, "hex": "65 63 68 6f 20 63 74 72 6c 63 2d 6f 6b 0d 65 78 69 74 0d", "text": "echo ${p}-ctrlc-ok\nexit\n"},
    ]


class TerminalExecItArtifactVerifierTest(unittest.TestCase):
    def write_case(self, artifact=None, events=None):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        artifact_path = root / "ui-it-selftest-latest.json"
        input_path = root / "engine-exec-input-latest.jsonl"
        artifact_path.write_text(json.dumps(good_artifact() if artifact is None else artifact))
        if events is not None:
            input_path.write_text("".join(json.dumps(event) + "\n" for event in events))
        return tmp, artifact_path, input_path

    def test_accepts_real_device_artifact_with_jsonl_proof(self):
        tmp, artifact_path, input_path = self.write_case(events=good_events())
        with tmp:
            verifier.verify(artifact_path, input_path, require_container=True)

    def test_rejects_planned_skip_fake_success_even_when_optional(self):
        artifact = {
            "Name": "ui-engine-exec-it",
            "Status": "planned-skip",
            "Success": True,
            "DeviceProofAttempted": False,
        }
        tmp, artifact_path, input_path = self.write_case(artifact=artifact, events=None)
        with tmp:
            with self.assertRaisesRegex(verifier.VerificationError, "planned-skip must never report Success=true"):
                verifier.verify(artifact_path, input_path, require_container=False)

    def test_rejects_success_json_without_raw_engine_exec_jsonl(self):
        artifact = good_artifact()
        artifact["EngineExecDiagnostics"] = '{"event":"stream-started"}\n{"event":"resize","body":"/resize?h=24&w=80"}'
        tmp, artifact_path, input_path = self.write_case(artifact=artifact, events=None)
        with tmp:
            with self.assertRaisesRegex(verifier.VerificationError, "missing Engine exec input diagnostics"):
                verifier.verify(artifact_path, input_path, require_container=True)

    def test_rejects_stream_started_without_resize_route(self):
        events = [event for event in good_events() if event["event"] != "resize"]
        tmp, artifact_path, input_path = self.write_case(events=events)
        with tmp:
            with self.assertRaisesRegex(verifier.VerificationError, "missing resize route event"):
                verifier.verify(artifact_path, input_path, require_container=True)

    def test_rejects_missing_ctrl_c_byte_even_with_success_markers(self):
        events = [dict(event) for event in good_events()]
        for event in events:
            if event.get("hex") == "03":
                event["hex"] = "63"
                event["text"] = "c"
        tmp, artifact_path, input_path = self.write_case(events=events)
        with tmp:
            with self.assertRaisesRegex(verifier.VerificationError, "missing Ctrl-C byte"):
                verifier.verify(artifact_path, input_path, require_container=True)

    def test_rejects_literal_c_appended_to_sleep_even_with_etx(self):
        events = [dict(event) for event in good_events()]
        for event in events:
            if "sleep 15" in event.get("text", ""):
                event["text"] = "sleep 15c\r"
                event["hex"] = "73 6c 65 65 70 20 31 35 63 0d"
        artifact = good_artifact()
        artifact["OutputTail"] = artifact["OutputTail"].replace(
            "pdocker-ui-it-ctrlc-ok",
            "sleep 15c\npdocker-ui-it-ctrlc-ok",
        )
        tmp, artifact_path, input_path = self.write_case(artifact=artifact, events=events)
        with tmp:
            with self.assertRaisesRegex(verifier.VerificationError, "ctrl-c-interrupts|ime-enter"):
                verifier.verify(artifact_path, input_path, require_container=True)

    def test_rejects_raw_arrow_escape_in_output_tail(self):
        artifact = good_artifact()
        artifact["OutputTail"] = artifact["OutputTail"].replace(
            "pdocker-ui-it-arrow-seed\npdocker-ui-it-arrow-seed",
            "pdocker-ui-it-arrow-seed\n\u001b[A",
        )
        tmp, artifact_path, input_path = self.write_case(artifact=artifact, events=good_events())
        with tmp:
            with self.assertRaisesRegex(verifier.VerificationError, "arrow-up-reaches-readline-history"):
                verifier.verify(artifact_path, input_path, require_container=True)

    def test_rejects_top_without_refresh_marker_before_q_recovery(self):
        artifact = good_artifact()
        artifact["OutputTail"] = artifact["OutputTail"].replace(
            "PID USER PR NI VIRT RES SHR S %CPU %MEM TIME+ COMMAND\n",
            "",
        )
        tmp, artifact_path, input_path = self.write_case(artifact=artifact, events=good_events())
        with tmp:
            with self.assertRaisesRegex(verifier.VerificationError, "top-refresh-observed-before-q"):
                verifier.verify(artifact_path, input_path, require_container=True)

    def test_rejects_missing_q_byte_even_when_top_recovery_marker_exists(self):
        events = [event for event in good_events() if event.get("hex") != "71"]
        tmp, artifact_path, input_path = self.write_case(events=events)
        with tmp:
            with self.assertRaisesRegex(verifier.VerificationError, "missing q byte"):
                verifier.verify(artifact_path, input_path, require_container=True)

    def test_rejects_missing_first_class_evidence_flag(self):
        artifact = good_artifact()
        artifact["Evidence"]["ime-enter-ctrlc-regression-covered"] = False
        tmp, artifact_path, input_path = self.write_case(artifact=artifact, events=good_events())
        with tmp:
            with self.assertRaisesRegex(verifier.VerificationError, "Evidence flags not true"):
                verifier.verify(artifact_path, input_path, require_container=True)

    def test_smoke_skip_artifact_uses_same_required_evidence_names_and_scrubs_stale_jsonl(self):
        script = SMOKE_SCRIPT_PATH.read_text()
        skip_function = script[script.index("write_ui_it_selftest_skip_artifact()") :]
        skip_function = skip_function[: skip_function.index("validate_ui_it_selftest_artifact()")]
        evidence_block = re.search(r'"Evidence": \{(?P<body>.*?)\n  \},', skip_function, re.S)
        self.assertIsNotNone(evidence_block)
        for name in REQUIRED:
            self.assertIn(f'"{name}": false', evidence_block.group("body"))
        self.assertIn("clear_ui_it_selftest_artifacts", skip_function)
        self.assertIn("engine-exec-input-latest.jsonl", script)
        self.assertIn("Ctrl-C must be an isolated ETX byte", script)
        self.assertIn("IME Enter must be proven by exactly one Enter byte", script)


if __name__ == "__main__":
    unittest.main()

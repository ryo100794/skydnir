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
STARTED_AT_MS = 1_000_000


def input_event(timestamp, text, raw_bytes=None):
    data = raw_bytes if raw_bytes is not None else text.encode("utf-8")
    return {
        "event": "input",
        "timestampMs": timestamp,
        "bytes": len(data),
        "hex": " ".join(f"{byte:02x}" for byte in data),
        "text": text.replace("\x1b", "\\e"),
    }


def smoke_script_function(script, name):
    match = re.search(rf"^{re.escape(name)}\(\) \{{\n(?P<body>.*?)(?=^[A-Za-z0-9_]+\(\) \{{|\Z)", script, re.M | re.S)
    if match is None:
        raise AssertionError(f"missing bash function {name}")
    return match.group("body")


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
            "Tasks: 2 total, 1 running, 1 sleeping",
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
        "StartedAtMs": STARTED_AT_MS,
        "DurationMs": 2_000,
        "RequiredEvidence": REQUIRED,
        "Evidence": {name: True for name in REQUIRED},
        "OutputTail": tail,
        "EngineExecDiagnostics": "this embedded field is not enough by itself",
    }


def good_events():
    initial_script = """p=pdocker-ui-it
echo ${p}-ok
/usr/bin/[ "x" = "x" ] && echo ${p}-bracket-ok
pwd
[ -t 0 ] && echo ${p}-tty-ok
[ "$TERM" = "xterm-256color" ] && echo ${p}-term-ok
[ -n "$BASH_VERSION" ] && echo ${p}-bash-ok
top -b -n 1 >/dev/null && echo ${p}-top-ok
echo ${p}-arrow-seed
"""
    top_recovery = """echo ${p}-top-ok
echo ${p}-topq-ok
"""
    ctrlc_recovery = """echo ${p}-ctrlc-ok
exit
"""
    return [
        {"event": "start", "container": CID, "timestampMs": STARTED_AT_MS + 1},
        {"event": "create-response", "timestampMs": STARTED_AT_MS + 2, "status": 201, "body": json.dumps({"Id": EXEC_ID}), "execId": ""},
        {"event": "created", "timestampMs": STARTED_AT_MS + 3, "execId": EXEC_ID},
        {"event": "resize", "timestampMs": STARTED_AT_MS + 4, "execId": EXEC_ID, "status": 201, "body": f"/exec/{EXEC_ID}/resize?h=24&w=80"},
        {"event": "start-response", "timestampMs": STARTED_AT_MS + 5, "execId": EXEC_ID, "body": "HTTP/1.1 101 UPGRADED\r\n\r\n"},
        {"event": "stream-started", "timestampMs": STARTED_AT_MS + 6, "execId": EXEC_ID},
        input_event(STARTED_AT_MS + 7, initial_script),
        input_event(STARTED_AT_MS + 8, "printf '%s\\n' \"$p-ime-enter-ok\""),
        input_event(STARTED_AT_MS + 9, "\r"),
        input_event(STARTED_AT_MS + 10, "\x1b[A\r", raw_bytes=b"\x1b[A\r"),
        input_event(STARTED_AT_MS + 11, "top\r"),
        input_event(STARTED_AT_MS + 12, "q"),
        input_event(STARTED_AT_MS + 13, top_recovery),
        input_event(STARTED_AT_MS + 14, "sleep 15\r"),
        input_event(STARTED_AT_MS + 15, "\u0003", raw_bytes=b"\x03"),
        input_event(STARTED_AT_MS + 16, ctrlc_recovery),
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
            "HardGateRequired": False,
        }
        tmp, artifact_path, input_path = self.write_case(artifact=artifact, events=None)
        with tmp:
            with self.assertRaisesRegex(verifier.VerificationError, "planned-skip must never report Success=true"):
                verifier.verify(artifact_path, input_path, require_container=False)

    def test_rejects_planned_skip_even_when_optional_non_promoting_skip_evidence(self):
        artifact = {
            "Name": "ui-engine-exec-it",
            "Status": "planned-skip",
            "Success": False,
            "DeviceProofAttempted": False,
            "HardGateRequired": False,
        }
        tmp, artifact_path, input_path = self.write_case(artifact=artifact, events=None)
        with tmp:
            with self.assertRaisesRegex(verifier.VerificationError, "requires a real container; planned-skip is not a pass"):
                verifier.verify(artifact_path, input_path, require_container=False)

    def test_rejects_planned_skip_when_artifact_marks_hard_gate_even_without_cli_flag(self):
        artifact = {
            "Name": "ui-engine-exec-it",
            "Status": "planned-skip",
            "Success": False,
            "DeviceProofAttempted": False,
            "HardGateRequired": True,
        }
        tmp, artifact_path, input_path = self.write_case(artifact=artifact, events=None)
        with tmp:
            with self.assertRaisesRegex(verifier.VerificationError, "requires a real container"):
                verifier.verify(artifact_path, input_path, require_container=False)

    def test_rejects_success_artifact_without_container_even_when_optional(self):
        artifact = good_artifact()
        artifact["Container"] = ""
        tmp, artifact_path, input_path = self.write_case(artifact=artifact, events=good_events())
        with tmp:
            with self.assertRaisesRegex(verifier.VerificationError, "success artifact is missing Container"):
                verifier.verify(artifact_path, input_path, require_container=False)


    def test_rejects_success_artifact_without_device_timing_window(self):
        artifact = good_artifact()
        del artifact["StartedAtMs"]
        tmp, artifact_path, input_path = self.write_case(artifact=artifact, events=good_events())
        with tmp:
            with self.assertRaisesRegex(verifier.VerificationError, "missing StartedAtMs"):
                verifier.verify(artifact_path, input_path, require_container=True)

    def test_rejects_success_artifact_claiming_no_device_proof_attempt(self):
        artifact = good_artifact()
        artifact["DeviceProofAttempted"] = False
        tmp, artifact_path, input_path = self.write_case(artifact=artifact, events=good_events())
        with tmp:
            with self.assertRaisesRegex(verifier.VerificationError, "DeviceProofAttempted=false"):
                verifier.verify(artifact_path, input_path, require_container=True)

    def test_rejects_stale_jsonl_outside_artifact_time_window(self):
        events = [dict(event) for event in good_events()]
        for event in events:
            event["timestampMs"] = STARTED_AT_MS - 120_000 + (event["timestampMs"] - STARTED_AT_MS)
        tmp, artifact_path, input_path = self.write_case(events=events)
        with tmp:
            with self.assertRaisesRegex(verifier.VerificationError, "do not overlap the UI device artifact window"):
                verifier.verify(artifact_path, input_path, require_container=True)

    def test_rejects_input_event_with_incomplete_hex_byte_count(self):
        events = [dict(event) for event in good_events()]
        first_input = next(event for event in events if event.get("event") == "input")
        first_input["hex"] = "70 3d 70"
        tmp, artifact_path, input_path = self.write_case(events=events)
        with tmp:
            with self.assertRaisesRegex(verifier.VerificationError, "bytes count must match hex byte count"):
                verifier.verify(artifact_path, input_path, require_container=True)

    def test_rejects_container_mismatch_between_artifact_and_engine_exec_jsonl(self):
        events = [dict(event) for event in good_events()]
        events[0]["container"] = "fedcba9876543210"
        tmp, artifact_path, input_path = self.write_case(events=events)
        with tmp:
            with self.assertRaisesRegex(verifier.VerificationError, "start container mismatch"):
                verifier.verify(artifact_path, input_path, require_container=True)

    def test_rejects_resize_route_for_different_exec_id(self):
        events = [dict(event) for event in good_events()]
        for event in events:
            if event.get("event") == "resize":
                event["execId"] = "different-exec-id"
        tmp, artifact_path, input_path = self.write_case(events=events)
        with tmp:
            with self.assertRaisesRegex(verifier.VerificationError, "resize route is not observable for the created execId"):
                verifier.verify(artifact_path, input_path, require_container=True)

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

    def test_rejects_duplicate_ime_enter_byte(self):
        events = [dict(event) for event in good_events()]
        for index, event in enumerate(events):
            if event.get("text") == "\r" and index > 0 and "ime-enter-ok" in events[index - 1].get("text", ""):
                events.insert(index + 1, {"event": "input", "timestampMs": STARTED_AT_MS + 9.5, "bytes": 1, "hex": "0d", "text": "\r"})
                break
        tmp, artifact_path, input_path = self.write_case(events=events)
        with tmp:
            with self.assertRaisesRegex(verifier.VerificationError, "duplicate Enter byte"):
                verifier.verify(artifact_path, input_path, require_container=True)

    def test_rejects_ctrl_c_followed_by_literal_c_before_recovery(self):
        events = [dict(event) for event in good_events()]
        for index, event in enumerate(events):
            if event.get("hex") == "03":
                events.insert(index + 1, {"event": "input", "timestampMs": STARTED_AT_MS + 15.5, "bytes": 1, "hex": "63", "text": "c"})
                break
        tmp, artifact_path, input_path = self.write_case(events=events)
        with tmp:
            with self.assertRaisesRegex(verifier.VerificationError, "literal c byte"):
                verifier.verify(artifact_path, input_path, require_container=True)

    def test_rejects_arrow_history_probe_before_seed_script(self):
        events = [dict(event) for event in good_events()]
        arrow = next(event for event in events if event.get("hex") == "1b 5b 41 0d")
        events.remove(arrow)
        arrow["timestampMs"] = STARTED_AT_MS + 6.5
        events.insert(6, arrow)
        tmp, artifact_path, input_path = self.write_case(events=events)
        with tmp:
            with self.assertRaisesRegex(verifier.VerificationError, "after the seed shell script"):
                verifier.verify(artifact_path, input_path, require_container=True)

    def test_rejects_q_before_foreground_top(self):
        events = [dict(event) for event in good_events()]
        q = next(event for event in events if event.get("hex") == "71")
        events.remove(q)
        q["timestampMs"] = STARTED_AT_MS + 10.5
        events.insert(10, q)
        tmp, artifact_path, input_path = self.write_case(events=events)
        with tmp:
            with self.assertRaisesRegex(verifier.VerificationError, "after foreground top"):
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

    def test_rejects_single_top_refresh_marker_as_non_periodic(self):
        artifact = good_artifact()
        artifact["OutputTail"] = artifact["OutputTail"].replace(
            "Tasks: 2 total, 1 running, 1 sleeping\n",
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
        self.assertIn('SMOKE_ARTIFACT_DIR_RESOLVED="${PDOCKER_SMOKE_ARTIFACT_DIR:-', script)
        self.assertNotIn('SMOKE_ARTIFACT_DIR_RESOLVED="$ROOT/tmp/device-smoke-artifacts/$(date', skip_function)
        self.assertIn("Ctrl-C must be an isolated ETX byte", script)
        self.assertIn("IME Enter must be proven by exactly one Enter byte", script)

    def test_smoke_artifact_dir_is_resolved_once_and_reused_by_collect_and_validate(self):
        script = SMOKE_SCRIPT_PATH.read_text()
        timestamp_expr = "$(date -u +%Y%m%dT%H%M%SZ)"
        self.assertEqual(1, script.count(timestamp_expr))
        self.assertIn(
            'SMOKE_ARTIFACT_DIR_RESOLVED="${PDOCKER_SMOKE_ARTIFACT_DIR:-$ROOT/tmp/device-smoke-artifacts/$(date -u +%Y%m%dT%H%M%SZ)}"\n',
            script,
        )

        smoke_artifact_dir = smoke_script_function(script, "smoke_artifact_dir")
        self.assertIn('printf \'%s\' "$SMOKE_ARTIFACT_DIR_RESOLVED"', smoke_artifact_dir)
        self.assertNotIn("date -u", smoke_artifact_dir)

        for name in ("collect_device_file", "clear_ui_it_selftest_artifacts", "validate_ui_it_selftest_artifact"):
            with self.subTest(function=name):
                body = smoke_script_function(script, name)
                self.assertIn('dest_dir="$(smoke_artifact_dir)"', body)
                self.assertNotIn(timestamp_expr, body)
                self.assertNotIn("PDOCKER_SMOKE_ARTIFACT_DIR:-", body)


if __name__ == "__main__":
    unittest.main()

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "app" / "src" / "main" / "kotlin" / "io" / "github" / "ryo100794" / "pdocker" / "MainActivity.kt"
BRIDGE = ROOT / "app" / "src" / "main" / "kotlin" / "io" / "github" / "ryo100794" / "pdocker" / "Bridge.kt"
XTERM = ROOT / "app" / "src" / "main" / "assets" / "xterm" / "index.html"
ANDROID_SMOKE = ROOT / "scripts" / "android-device-smoke.sh"
DEVICE_GATE_DOC = ROOT / "docs" / "test" / "TERMINAL_EXEC_IT_DEVICE_GATE.md"


def _shell_function_body(source: str, name: str) -> str:
    start = source.index(f"{name}() {{")
    end = source.index("\n}\n", start) + 3
    return source[start:end]

def _method_body(source: str, signature: str) -> str:
    start = source.index(signature)
    brace = source.index("{", start)
    depth = 0
    for idx in range(brace, len(source)):
        ch = source[idx]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return source[start : idx + 1]
    raise AssertionError(f"method not closed: {signature}")


class TerminalExecItContractTest(unittest.TestCase):
    def setUp(self):
        self.main = MAIN.read_text()
        self.bridge = BRIDGE.read_text()
        self.xterm = XTERM.read_text()
        self.android_smoke = ANDROID_SMOKE.read_text()

    def test_container_terminal_uses_engine_exec_api_not_local_shell_or_logs(self):
        body = _method_body(self.main, "private fun openDockerInteractiveTerminal")
        self.assertIn("startDaemon()", body)
        self.assertIn("engineExecTerminalCommand(containerId)", body)
        self.assertIn("contextualize = false", body)
        self.assertIn('keyOverride = "engine-exec:', body)
        for forbidden in ["openDockerTerminal", "terminalSessionCommand", "docker exec", "attach", "logs("]:
            self.assertNotIn(forbidden, body)

        command_match = re.search(r"private fun engineExecTerminalCommand[\s\S]*?containerId\.trim\(\)\}\"", self.main)
        self.assertIsNotNone(command_match)
        command_body = command_match.group(0)
        self.assertIn("Bridge.ENGINE_EXEC_PREFIX", command_body)
        self.assertNotIn("docker exec", command_body)

    def test_engine_exec_create_and_start_are_tty_stdin_raw_stream_contract(self):
        create_body = _method_body(self.bridge, "private fun createEngineExec")
        for required in [
            '.put("AttachStdin", true)',
            '.put("AttachStdout", true)',
            '.put("AttachStderr", true)',
            '.put("Tty", true)',
            '"TERM=xterm-256color"',
            '"COLORTERM=truecolor"',
            '"/bin/sh", "-lc"',
            "exec /bin/bash -i",
            "exec /bin/sh -i",
        ]:
            self.assertIn(required, create_body)
        self.assertIn("/containers/${DockerEngineClient.encodePath(containerId)}/exec", create_body)
        self.assertNotIn("docker exec", create_body)

        start_body = _method_body(self.bridge, "private fun startEngineExecStream")
        self.assertIn('.put("Tty", true)', start_body)
        self.assertIn('append("Connection: Upgrade\\r\\n")', start_body)
        self.assertIn('append("Upgrade: tcp\\r\\n")', start_body)
        self.assertIn('append("POST /exec/$execId/start HTTP/1.1\\r\\n")', start_body)
        self.assertIn('head.startsWith("HTTP/1.1 101")', start_body)

        input_body = _method_body(self.bridge, "fun input")
        self.assertRegex(input_body, r"socket\.outputStream\.write\(bytes\)")
        self.assertRegex(input_body, r"socket\.outputStream\.flush\(\)")
        self.assertNotIn("readLine", input_body)
        self.assertNotIn("+ \"\\n\"", input_body)
        self.assertNotIn("+ \"\\r\"", input_body)

    def test_terminal_keyboard_sends_control_bytes_and_escape_sequences_not_text(self):
        for button in [
            'data-key="\\u001b[D">←',
            'data-key="\\u001b[B">↓',
            'data-key="\\u001b[A">↑',
            'data-key="\\u001b[C">→',
        ]:
            self.assertIn(button, self.xterm)
        self.assertIn('data-key="\\r">Enter', self.xterm)
        self.assertIn('data-key="\\u0003" data-raw="1">Ctrl-C', self.xterm)
        self.assertIn('data-key="\\u0004" data-raw="1">Ctrl-D', self.xterm)
        self.assertIn('data-key="\\u001a" data-raw="1">Ctrl-Z', self.xterm)
        self.assertIn("const ctrlMap = (ch) =>", self.xterm)
        self.assertIn("return String.fromCharCode(code - 0x60)", self.xterm)
        self.assertIn("const payload = raw ? normalized : applyModifiers(normalized);", self.xterm)
        self.assertIn("PdockerBridge.input(toB64(enc.encode(payload)))", self.xterm)

    def test_ime_fallback_claims_modifier_input_to_prevent_literal_c_and_double_enter(self):
        self.assertIn("const normalizeTerminalInput = (data) =>", self.xterm)
        self.assertIn("return data.replace(/\\r\\n/g, '\\r').replace(/\\n/g, '\\r');", self.xterm)
        self.assertIn("if (event.inputType === 'insertLineBreak')", self.xterm)
        self.assertIn("claimImeEvent(event);", self.xterm)
        self.assertIn("suppressTerminalDataOnce('\\r');", self.xterm)
        self.assertIn("sendInput('\\r', true);", self.xterm)
        self.assertIn("if ((mods.ctrl || mods.alt || mods.esc) && event.data)", self.xterm)
        self.assertIn("suppressTerminalDataOnce(event.data);", self.xterm)
        self.assertIn("sendInput(event.data, false);", self.xterm)
        self.assertIn("if ((mods.ctrl || mods.alt || mods.esc) && event.key && event.key.length === 1)", self.xterm)
        self.assertIn("suppressTerminalDataOnce(event.key);", self.xterm)
        self.assertIn("sendInput(event.key, false);", self.xterm)
        self.assertIn("ta.addEventListener('compositionstart'", self.xterm)
        self.assertIn("ta.addEventListener('compositionend'", self.xterm)
        self.assertIn("lastCompositionTerminalData", self.xterm)
        self.assertIn("if (event.inputType === 'insertCompositionText') return;", self.xterm)
        self.assertIn("sendInput(data, true);", self.xterm)

    def test_ui_it_selftest_keeps_regression_symptoms_observable(self):
        self.assertIn('.put("Name", "ui-engine-exec-it")', self.main)
        self.assertIn("window.pdockerTestSendInput", self.main)
        self.assertIn("window.pdockerTestCtrlInput('c')", self.main)
        for marker in [
            "pdocker-ui-it-bracket-ok",
            "pdocker-ui-it-tty-ok",
            "pdocker-ui-it-term-ok",
            "pdocker-ui-it-bash-ok",
            "pdocker-ui-it-top-ok",
            "pdocker-ui-it-arrow-seed",
            "pdocker-ui-it-topq-ok",
            "pdocker-ui-it-ctrlc-ok",
        ]:
            self.assertIn(marker, self.main)
        self.assertIn("window.pdockerTestSendInput('\\\\u001b[A\\\\r', true)", self.main)
        self.assertIn('Regex("pdocker-ui-it-arrow-seed").findAll', self.main)
        self.assertIn('UI exec -it printed arrow escape bytes', self.main)
        self.assertIn("window.pdockerTestSendInput('top\\\\n', false)", self.main)
        self.assertIn("window.pdockerTestSendInput('q', true)", self.main)
        self.assertIn(r"window.pdockerTestSendInput('echo \${p}-topq-ok\\n', false)", self.main)
        self.assertIn('"UI exec -it fullscreen top did not accept q', self.main)
        self.assertIn('Regex("(/usr/bin/)?\\\\[: extra argument")', self.main)
        self.assertIn('"UI exec -it produced bracket argv noise"', self.main)
        self.assertIn('"UI exec -it did not preserve terminal CRLF line control"', self.main)
        self.assertIn('"UI exec -it is not attached to a controlling tty"', self.main)
        self.assertIn('"EngineExecDiagnostics"', self.main)


    def test_device_smoke_runs_ui_it_selftest_only_with_real_container_and_collects_artifacts(self):
        self.assertIn("ACTION_PREFIX.action.SMOKE_UI_IT_SELFTEST", self.android_smoke)
        self.assertIn("PDOCKER_UI_IT_SELFTEST_CONTAINER", self.android_smoke)
        self.assertIn("PDOCKER_UI_IT_SELFTEST_REQUIRE_CONTAINER", self.android_smoke)
        self.assertIn("PDOCKER_SMOKE_ARTIFACT_DIR", self.android_smoke)
        self.assertIn("collect_ui_it_selftest_artifacts", self.android_smoke)
        self.assertIn("validate_ui_it_selftest_artifact", self.android_smoke)
        self.assertIn("files/pdocker/diagnostics/ui-it-selftest-latest.json", self.android_smoke)
        self.assertIn("engine-exec-input-latest.jsonl", self.android_smoke)
        self.assertIn('run_adb exec-out run-as "$PKG" cat "$device_path"', self.android_smoke)

        body = _shell_function_body(self.android_smoke, "ui_engine_exec_it_selftest")
        self.assertIn('if [[ -z "$container_ref" ]]', body)
        self.assertIn('write_ui_it_selftest_skip_artifact "no container id was available', body)
        no_container_block = body[body.index('if [[ -z "$container_ref" ]]') : body.index('echo "[pdocker smoke] ui self-test engine exec -it container=$container_ref"')]
        self.assertIn('validate_ui_it_selftest_artifact "$require_container"', no_container_block)
        self.assertLess(no_container_block.index('validate_ui_it_selftest_artifact "$require_container"'), no_container_block.index('return 0'))
        self.assertIn('if [[ "$require_container" == "1" ]]', body)
        self.assertIn('planned-skip is non-passing evidence', body)
        self.assertIn('return 1', body)
        self.assertIn('return 0', body)
        self.assertIn('--es container "$container_ref"', body)
        self.assertIn('collect_ui_it_selftest_artifacts', body)
        self.assertIn('grep -q \'\\"Success\\": true\'', body)
        self.assertIn('validate_ui_it_selftest_artifact "$require_container"', body)

        skip_body = _shell_function_body(self.android_smoke, "write_ui_it_selftest_skip_artifact")
        self.assertIn('"Status": "planned-skip"', skip_body)
        self.assertIn('"Success": false', skip_body)
        self.assertIn('"DeviceProofAttempted": false', skip_body)
        self.assertIn('"HardGateRequired": $hard_gate_json', skip_body)
        self.assertIn('"RequiredEvidence"', skip_body)
        self.assertIn('"Enter": false', skip_body)
        self.assertIn('"CtrlC": false', skip_body)
        self.assertIn('"ArrowHistory": false', skip_body)
        self.assertIn('"Top": false', skip_body)
        self.assertIn('"TopQuit": false', skip_body)
        self.assertIn('"Resize": false', skip_body)
        self.assertIn('fake success', skip_body)

        validate_body = _shell_function_body(self.android_smoke, "validate_ui_it_selftest_artifact")
        self.assertIn('status == "planned-skip"', validate_body)
        self.assertIn('if success:', validate_body)
        self.assertIn("planned-skip must never report Success=true", validate_body)
        self.assertIn('artifact.get("DeviceProofAttempted") is True', validate_body)
        self.assertIn("hard gate requires a real container; planned-skip is not a pass", validate_body)
        self.assertIn('"enter-single-submit"', validate_body)
        self.assertIn('"ctrl-c-interrupts-without-literal-c"', validate_body)
        self.assertIn('"arrow-up-reaches-readline-history"', validate_body)
        self.assertIn('"top-starts-on-tty"', validate_body)
        self.assertIn('"q-quits-top"', validate_body)
        self.assertIn('"resize-route-is-observable"', validate_body)
        self.assertIn('"/resize?h=" in diagnostics', validate_body)
        self.assertIn('"event":"resize-failed"', validate_body)
        self.assertNotRegex(
            validate_body,
            r'"resize-route-is-observable"[^\n]*stream-started',
            'stream-started only proves exec stream startup and must not satisfy resize evidence',
        )

        self.assertIn('ui_engine_exec_it_selftest "$PDOCKER_UI_IT_SELFTEST_CONTAINER" "${PDOCKER_UI_IT_SELFTEST_REQUIRE_CONTAINER:-1}"', self.android_smoke)
        self.assertIn('ui_engine_exec_it_selftest "$CID" 1', self.android_smoke)
        self.assertIn('ui_engine_exec_it_selftest "" "${PDOCKER_UI_IT_SELFTEST_REQUIRE_CONTAINER:-0}"', self.android_smoke)
        self.assertNotIn('ui_engine_exec_it_selftest "$CID"\n', self.android_smoke)

    def test_ui_it_validator_rejects_fake_success_planned_skip_and_stream_started_resize(self):
        validate_body = _shell_function_body(self.android_smoke, "validate_ui_it_selftest_artifact")

        # A planned-skip artifact is useful diagnostic evidence, but even an
        # accidental/fabricated Success=true must be rejected before optional
        # non-required skips are accepted.
        planned_skip_block = validate_body[
            validate_body.index('if status == "planned-skip":') : validate_body.index('if require_container and not artifact.get("Container")')
        ]
        self.assertIn('if success:', planned_skip_block)
        self.assertIn('planned-skip must never report Success=true', planned_skip_block)
        self.assertIn('DeviceProofAttempted', planned_skip_block)
        self.assertLess(planned_skip_block.index('if success:'), planned_skip_block.index('raise SystemExit(0)'))

        resize_line = next(
            line for line in validate_body.splitlines() if '"resize-route-is-observable"' in line
        )
        self.assertIn('"/resize?h=" in diagnostics', resize_line)
        self.assertIn('resize-failed', resize_line)
        self.assertNotIn('stream-started', resize_line)
        self.assertIn('stream-started only proves the exec stream was opened', validate_body)


    def test_terminal_surface_stays_session_neutral_static_boundary(self):
        # xterm/index.html is the generic terminal surface. It may talk to the
        # bridge using byte-oriented UI verbs, but Docker/Engine/PTY semantics
        # must stay in Kotlin session/API code so UI fixes cannot bypass exec -it.
        forbidden_patterns = [
            r"Bridge\.ENGINE_EXEC_PREFIX",
            r"ENGINE_EXEC_PREFIX",
            r"/containers/[^\n\r]*exec",
            r"/exec/[^\n\r]*start",
            r"/exec/[^\n\r]*resize",
            r"docker\s+exec",
            r"PtyNative",
            r"createEngineExec",
            r"startEngineExecStream",
            r"ui-it-selftest-latest\.json",
            r"engine-exec-input-latest\.jsonl",
            r"PDOCKER_UI_IT_SELFTEST",
        ]
        for pattern in forbidden_patterns:
            self.assertIsNone(re.search(pattern, self.xterm, flags=re.IGNORECASE), pattern)

        bridge_calls = set(re.findall(r"PdockerBridge\.([A-Za-z_][A-Za-z0-9_]*)\(", self.xterm))
        self.assertLessEqual(
            bridge_calls,
            {"readOnly", "copyToClipboard", "input", "resize", "startInitial"},
        )
        for generic_hook in [
            "window.pdockerTestSendInput",
            "window.pdockerTestCtrlInput",
            "PdockerBridge.input(toB64(enc.encode(payload)))",
            "PdockerBridge.resize(term.rows, term.cols)",
        ]:
            self.assertIn(generic_hook, self.xterm)

    def test_design_doc_records_terminal_surface_and_session_api_boundary(self):
        design = (ROOT / "docs" / "design" / "TERMINAL_STREAM_ARCHITECTURE.md").read_text()
        for required in [
            "The terminal UI is a generic byte terminal",
            "The API/session layer owns session semantics",
            "The surface must never branch on Docker container IDs",
            "Docker-specific commands, container IDs, and Engine API endpoints must not",
            "Docker exec/PTY semantics belong to the session/API layer",
            "static host test",
            "stream-started event",
            "not resize evidence",
            "must not count stream-started alone",
        ]:
            self.assertIn(required, design)

    def test_terminal_exec_it_device_gate_doc_records_artifact_contract(self):
        doc = DEVICE_GATE_DOC.read_text()
        for required in [
            "planned-skip is evidence, not success",
            "HardGateRequired",
            "enter-single-submit",
            "ctrl-c-interrupts-without-literal-c",
            "arrow-up-reaches-readline-history",
            "top-starts-on-tty",
            "q-quits-top",
            "resize-route-is-observable",
            "top-repaint-remains-terminal-shaped",
            "UI-driven reproduction route",
            "Japanese IME",
            "Layer separation contract",
            "static host test",
            "ui-it-selftest-latest.json",
            "engine-exec-input-latest.jsonl",
        ]:
            self.assertIn(required, doc)

    def test_resize_contract_supports_full_screen_programs(self):
        self.assertIn("private val lastTerminalSize", self.bridge)
        resize_body = _method_body(self.bridge, "fun resize")
        self.assertIn("lastTerminalSize.set(rows to cols)", resize_body)
        self.assertIn("resizeEngineExecAsync(execId, rows, cols)", resize_body)
        self.assertIn("PtyNative.resize(fd, rows, cols)", resize_body)
        resize_sync = _method_body(self.bridge, "private fun resizeEngineExecSync")
        self.assertIn('"/exec/${DockerEngineClient.encodePath(execId)}/resize?h=$rows&w=$cols"', resize_sync)
        self.assertIn("pushResize", self.xterm)
        self.assertIn("PdockerBridge.resize(term.rows, term.cols)", self.xterm)


if __name__ == "__main__":
    unittest.main()

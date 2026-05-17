import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "app" / "src" / "main" / "kotlin" / "io" / "github" / "ryo100794" / "pdocker" / "MainActivity.kt"
BRIDGE = ROOT / "app" / "src" / "main" / "kotlin" / "io" / "github" / "ryo100794" / "pdocker" / "Bridge.kt"
ENGINE_EXEC_SESSION = ROOT / "app" / "src" / "main" / "kotlin" / "io" / "github" / "ryo100794" / "pdocker" / "EngineExecSession.kt"
XTERM = ROOT / "app" / "src" / "main" / "assets" / "xterm" / "index.html"
ANDROID_SMOKE = ROOT / "scripts" / "android-device-smoke.sh"
TERMINAL_EXEC_IT_VERIFIER = ROOT / "scripts" / "verify-terminal-exec-it-artifact.py"
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
        self.engine_exec_session = ENGINE_EXEC_SESSION.read_text()
        self.xterm = XTERM.read_text()
        self.android_smoke = ANDROID_SMOKE.read_text()
        self.terminal_exec_it_verifier = TERMINAL_EXEC_IT_VERIFIER.read_text()

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
        create_body = _method_body(self.engine_exec_session, "private fun createEngineExec")
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

        start_body = _method_body(self.engine_exec_session, "private fun startEngineExecStream")
        self.assertIn('.put("Tty", true)', start_body)
        self.assertIn('append("Connection: Upgrade\\r\\n")', start_body)
        self.assertIn('append("Upgrade: tcp\\r\\n")', start_body)
        self.assertIn('append("POST /exec/$execId/start HTTP/1.1\\r\\n")', start_body)
        self.assertIn('head.startsWith("HTTP/1.1 101")', start_body)

        write_body = _method_body(self.engine_exec_session, "fun write")
        self.assertRegex(write_body, r"socket\.outputStream\.write\(bytes\)")
        self.assertRegex(write_body, r"socket\.outputStream\.flush\(\)")
        self.assertNotIn("readLine", write_body)
        self.assertNotIn("+ \"\\n\"", write_body)
        self.assertNotIn("+ \"\\r\"", write_body)

        bridge_input = _method_body(self.bridge, "fun input")
        self.assertIn("engineExecSession.get()?.let", bridge_input)
        self.assertIn("session.write(bytes)", bridge_input)
        self.assertNotIn("socket.outputStream", bridge_input)

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

    def test_terminal_modifier_policy_is_generic_one_shot_ctrl_then_alt_prefix(self):
        for mapping in [
            "case ' ': return '\\x00';",
            "case '[': return '\\x1b';",
            "case '\\\\': return '\\x1c';",
            "case ']': return '\\x1d';",
            "case '^': return '\\x1e';",
            "case '_': return '\\x1f';",
            "case '?': return '\\x7f';",
        ]:
            self.assertIn(mapping, self.xterm)

        apply_modifiers = _method_body(self.xterm, "const applyModifiers = (data) =>")
        self.assertLess(
            apply_modifiers.index("if (mods.ctrl && data.length === 1) out = ctrlMap(data);"),
            apply_modifiers.index("if (mods.alt || mods.esc) out = '\\x1b' + out;"),
        )

        send_input = _method_body(self.xterm, "const sendInput = (data, raw = false) =>")
        self.assertIn("const payload = raw ? normalized : applyModifiers(normalized);", send_input)
        self.assertIn("finally", send_input)
        self.assertIn("clearTransientModifiers();", send_input)

        schedule_clear = _method_body(self.xterm, "const scheduleModifierClear = () =>")
        self.assertIn("setTimeout", schedule_clear)
        self.assertIn("clearTransientModifiers();", schedule_clear)
        self.assertIn("}, 4000);", schedule_clear)
        self.assertIn("sendInput(decodeKey(btn.dataset.key), btn.dataset.raw === '1');", self.xterm)

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
        self.assertIn("window.pdockerTestImeFallbackInput", self.xterm)
        self.assertIn("selectionSuppressesIme()", self.xterm)
        self.assertIn("suppressImeForSelection()", self.xterm)
        self.assertIn("ta.setAttribute('inputmode', 'none')", self.xterm)
        self.assertIn("enter-beforeinput", self.xterm)
        self.assertIn("ctrl-beforeinput", self.xterm)
        self.assertIn("dispatchSyntheticTerminalEvent", self.xterm)

    def test_ui_it_selftest_keeps_regression_symptoms_observable(self):
        self.assertIn('.put("Name", "ui-engine-exec-it")', self.main)
        self.assertIn("window.pdockerTestSendInput", self.main)
        self.assertIn("window.pdockerTestImeFallbackInput('enter-beforeinput')", self.main)
        self.assertIn("window.pdockerTestImeFallbackInput('ctrl-beforeinput', 'c')", self.main)
        for marker in [
            "pdocker-ui-it-bracket-ok",
            "pdocker-ui-it-tty-ok",
            "pdocker-ui-it-term-ok",
            "pdocker-ui-it-bash-ok",
            "pdocker-ui-it-top-ok",
            "pdocker-ui-it-arrow-seed",
            "pdocker-ui-it-ime-enter-ok",
            "pdocker-ui-it-topq-ok",
            "pdocker-ui-it-ctrlc-ok",
        ]:
            self.assertIn(marker, self.main)
        self.assertIn("window.pdockerTestSendInput('\\\\u001b[A\\\\r', true)", self.main)
        self.assertIn('Regex("pdocker-ui-it-arrow-seed").findAll', self.main)
        self.assertIn('UI exec -it printed arrow escape bytes', self.main)
        self.assertIn("window.pdockerTestSendInput('top\\\\n', false)", self.main)
        self.assertIn("window.pdockerTestSendInput('q', true)", self.main)
        self.assertIn(r"window.pdockerTestSendInput('echo \${p}-top-ok\\necho \${p}-topq-ok\\n', false)", self.main)
        self.assertIn('"UI exec -it fullscreen top did not render a refresh before q"', self.main)
        self.assertIn('"UI exec -it fullscreen top did not accept q', self.main)
        self.assertIn('"ime-enter-ctrlc-regression-covered"', self.main)
        self.assertIn('"top-refresh-observed-before-q"', self.main)
        self.assertIn('"top-repaint-remains-terminal-shaped"', self.main)
        self.assertIn("pdocker-ui-it-top-batch-ok", self.main)
        self.assertIn('evidence.put("top-starts-on-tty", true)', self.main)
        self.assertIn('evidence.put("top-refresh-observed-before-q", true)', self.main)
        self.assertIn('evidence.put("ime-enter-ctrlc-regression-covered", true)', self.main)
        self.assertIn('evidence.put("top-repaint-remains-terminal-shaped", true)', self.main)
        self.assertIn('val finalImeEnterCount = Regex("pdocker-ui-it-ime-enter-ok").findAll(text).count()', self.main)
        self.assertIn('check(finalImeEnterCount == 1)', self.main)
        initial_script = self.main[self.main.index('val script = "p=pdocker-ui-it') : self.main.index('ui.post {', self.main.index('val script = "p=pdocker-ui-it'))]
        self.assertNotIn('echo \\${p}-top-ok', initial_script)
        self.assertIn('evidence.put("resize-route-is-observable", true)', self.main)
        self.assertIn('"UI exec -it did not observe Engine exec resize route in diagnostics"', self.main)
        self.assertIn('Regex("(/usr/bin/)?\\\\[: extra argument")', self.main)
        self.assertIn('"UI exec -it produced bracket argv noise"', self.main)
        self.assertIn('"UI exec -it did not cover IME Enter/Ctrl-C fallback', self.main)
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
        for evidence_name in [
            "enter-single-submit",
            "enter-no-duplicate-submit",
            "ctrl-c-interrupts-without-literal-c",
            "jp-en-ctrl-c-isolated-etx",
            "arrow-up-reaches-readline-history",
            "arrow-up-no-escape-text",
            "ime-enter-ctrlc-regression-covered",
            "top-starts-on-tty",
            "top-refresh-observed-before-q",
            "top-repaint-remains-terminal-shaped",
            "q-quits-top",
            "top-q-shell-recovery",
            "resize-route-is-observable",
            "selection-keyboard-suppression",
        ]:
            self.assertIn(f'"{evidence_name}": false', skip_body)
        self.assertIn('fake success', skip_body)

        validate_body = _shell_function_body(self.android_smoke, "validate_ui_it_selftest_artifact")
        self.assertIn('python3 "$ROOT/scripts/verify-terminal-exec-it-artifact.py"', validate_body)
        self.assertIn('"$dest_dir/ui-it-selftest-latest.json"', validate_body)
        self.assertIn('"$dest_dir/engine-exec-input-latest.jsonl"', validate_body)
        self.assertIn('require_flag=(--require-container)', validate_body)

        verifier = self.terminal_exec_it_verifier
        self.assertIn('status == "planned-skip"', verifier)
        self.assertIn('artifact.get("Success") is not True', verifier)
        self.assertIn("planned-skip must never report Success=true", verifier)
        self.assertIn('artifact.get("DeviceProofAttempted") is not True', verifier)
        self.assertIn("hard gate requires a real container; planned-skip is not a pass", verifier)
        self.assertIn('REQUIRED_EVIDENCE = [', verifier)
        self.assertIn('"enter-single-submit"', verifier)
        self.assertIn('"enter-no-duplicate-submit"', verifier)
        self.assertIn('"ctrl-c-interrupts-without-literal-c"', verifier)
        self.assertIn('"jp-en-ctrl-c-isolated-etx"', verifier)
        self.assertIn('"arrow-up-reaches-readline-history"', verifier)
        self.assertIn('"arrow-up-no-escape-text"', verifier)
        self.assertIn('"ime-enter-ctrlc-regression-covered"', verifier)
        self.assertIn('"top-starts-on-tty"', verifier)
        self.assertIn('"top-refresh-observed-before-q"', verifier)
        self.assertIn('"top-repaint-remains-terminal-shaped"', verifier)
        self.assertIn('"q-quits-top"', verifier)
        self.assertIn('"top-q-shell-recovery"', verifier)
        self.assertIn('"resize-route-is-observable"', verifier)
        self.assertIn('"selection-keyboard-suppression"', verifier)
        self.assertIn('missing_flags', verifier)
        self.assertIn('"pdocker-ui-it-ime-enter-ok" in tail', verifier)
        self.assertIn('any(marker in tail for marker in TOP_REFRESH_MARKERS)', verifier)
        self.assertIn('"/resize?h=" in _event_body(event)', verifier)
        self.assertIn('"resize-failed"', verifier)
        self.assertIn('_read_jsonl(input_jsonl_path)', verifier)
        self.assertIn('Engine exec input diagnostics missing Ctrl-C byte', verifier)
        self.assertNotRegex(
            verifier,
            r'"resize-route-is-observable"[^\n]*stream-started',
            'stream-started only proves exec stream startup and must not satisfy resize evidence',
        )

        self.assertIn('ui_engine_exec_it_selftest "$PDOCKER_UI_IT_SELFTEST_CONTAINER" "${PDOCKER_UI_IT_SELFTEST_REQUIRE_CONTAINER:-1}"', self.android_smoke)
        self.assertIn('ui_engine_exec_it_selftest "$CID" 1', self.android_smoke)
        self.assertIn('ui_engine_exec_it_selftest "" "${PDOCKER_UI_IT_SELFTEST_REQUIRE_CONTAINER:-0}"', self.android_smoke)
        self.assertNotIn('ui_engine_exec_it_selftest "$CID"\n', self.android_smoke)

    def test_ui_it_validator_rejects_fake_success_planned_skip_and_stream_started_resize(self):
        verifier = self.terminal_exec_it_verifier

        # A planned-skip artifact is useful diagnostic evidence, but even an
        # accidental/fabricated Success=true must be rejected before optional
        # non-required skips are accepted.
        planned_skip_block = verifier[
            verifier.index('def _verify_planned_skip') : verifier.index('def _verify_success_json')
        ]
        self.assertIn('artifact.get("Success") is not True', planned_skip_block)
        self.assertIn('planned-skip must never report Success=true', planned_skip_block)
        self.assertIn('DeviceProofAttempted', planned_skip_block)
        self.assertLess(planned_skip_block.index('Success'), planned_skip_block.index('DeviceProofAttempted'))

        resize_line = next(
            line for line in verifier.splitlines() if 'resize proof must be a resize route' in line
        )
        self.assertIn('not stream-started alone', resize_line)
        self.assertIn('stream-started alone is not accepted', verifier)
        self.assertIn('Engine exec diagnostics missing resize route event', verifier)
        self.assertIn('Engine exec input diagnostics missing ArrowUp+Enter bytes', verifier)
        self.assertIn('Engine exec input diagnostics missing Ctrl-C byte', verifier)
        self.assertIn('Engine exec input diagnostics missing q byte', verifier)


    def test_engine_exec_path_is_centralized_in_named_session(self):
        self.assertIn("class EngineExecSession", self.engine_exec_session)
        bridge_start = _method_body(self.bridge, "private fun startEngineExec")
        self.assertIn("EngineExecSession(", bridge_start)
        self.assertIn("session.start(containerId)", bridge_start)
        self.assertIn("engineExecSession", self.bridge)
        for forbidden in [
            "/containers/${DockerEngineClient.encodePath(containerId)}/exec",
            'append("POST /exec/$execId/start HTTP/1.1\\r\\n")',
            "LocalSocket",
            "recordEngineExecInput",
            "engineExecInputDiagnosticsFile",
        ]:
            self.assertNotIn(forbidden, self.bridge)
            self.assertIn(forbidden, self.engine_exec_session)

        for required in [
            "fun start(containerId: String): Boolean",
            "fun write(bytes: ByteArray)",
            "fun resize(rows: Int, cols: Int)",
            "fun close()",
            "private fun createEngineExec",
            "private fun startEngineExecStream",
            "private fun resizeEngineExecSync",
            "private fun recordEngineExecEvent",
        ]:
            self.assertIn(required, self.engine_exec_session)

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
            "window.pdockerTestImeFallbackInput",
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
            "Current Self-Test Evidence",
            "EngineExecSession transport",
            "Ctrl/Alt modifier policy",
            "Ctrl applies the conventional terminal control mapping",
            "Top/fullscreen behavior",
            "Raw soft-key buttons bypass the modifier",
        ]:
            self.assertIn(required, design)

    def test_terminal_exec_it_device_gate_doc_records_artifact_contract(self):
        doc = DEVICE_GATE_DOC.read_text()
        for required in [
            "planned-skip is evidence, not success",
            "HardGateRequired",
            "enter-single-submit",
            "enter-no-duplicate-submit",
            "ctrl-c-interrupts-without-literal-c",
            "jp-en-ctrl-c-isolated-etx",
            "arrow-up-reaches-readline-history",
            "arrow-up-no-escape-text",
            "top-starts-on-tty",
            "top-refresh-observed-before-q",
            "q-quits-top",
            "top-q-shell-recovery",
            "resize-route-is-observable",
            "selection-keyboard-suppression",
            "top-repaint-remains-terminal-shaped",
            "ime-enter-ctrlc-regression-covered",
            "UI-driven reproduction route",
            "Japanese IME",
            "Layer separation contract",
            "static host test",
            "ui-it-selftest-latest.json",
            "engine-exec-input-latest.jsonl",
            "Current proof matrix",
            "EngineExecSession transport",
            "generic Ctrl/Alt modifier policy",
            "foreground/full-screen `top`",
            "batch `top -b -n 1` probe is only a",
        ]:
            self.assertIn(required, doc)

    def test_resize_contract_supports_full_screen_programs(self):
        self.assertIn("private val lastTerminalSize", self.bridge)
        resize_body = _method_body(self.bridge, "fun resize")
        self.assertIn("lastTerminalSize.set(rows to cols)", resize_body)
        self.assertIn("engineExecSession.get()?.let", resize_body)
        self.assertIn("PtyNative.resize(fd, rows, cols)", resize_body)
        bridge_resize = _method_body(self.bridge, "fun resize")
        self.assertIn("session.resize(rows, cols)", bridge_resize)
        self.assertNotIn("/resize?h=", bridge_resize)
        resize_sync = _method_body(self.engine_exec_session, "private fun resizeEngineExecSync")
        self.assertIn('val path = "/exec/${DockerEngineClient.encodePath(execId)}/resize?h=$rows&w=$cols"', resize_sync)
        self.assertIn('recordEngineExecEvent("resize", execId = execId, status = response.status, body = path)', resize_sync)
        self.assertIn('recordEngineExecEvent("resize-failed", execId = execId, body = path, error = it.message.orEmpty())', resize_sync)
        self.assertIn("pushResize", self.xterm)
        self.assertIn("PdockerBridge.resize(term.rows, term.cols)", self.xterm)


if __name__ == "__main__":
    unittest.main()

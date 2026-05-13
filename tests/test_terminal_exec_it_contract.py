import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "app" / "src" / "main" / "kotlin" / "io" / "github" / "ryo100794" / "pdocker" / "MainActivity.kt"
BRIDGE = ROOT / "app" / "src" / "main" / "kotlin" / "io" / "github" / "ryo100794" / "pdocker" / "Bridge.kt"
XTERM = ROOT / "app" / "src" / "main" / "assets" / "xterm" / "index.html"


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

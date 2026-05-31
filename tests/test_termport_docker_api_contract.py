import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "app" / "src" / "main" / "python" / "pdockerd_bridge.py"
PDOCKERD = ROOT / "app" / "src" / "main" / "assets" / "pdockerd" / "pdockerd"
ENGINE_EXEC_SESSION = (
    ROOT
    / "app"
    / "src"
    / "main"
    / "kotlin"
    / "io"
    / "github"
    / "ryo100794"
    / "pdocker"
    / "EngineExecSession.kt"
)


class TermPortDockerApiContractTest(unittest.TestCase):
    def test_android_daemon_exposes_loopback_docker_tcp_endpoint_by_default(self):
        bridge = BRIDGE.read_text(encoding="utf-8")
        self.assertIn("def _engine_tcp_host()", bridge)
        self.assertIn('or "127.0.0.1:2375"', bridge)
        self.assertIn("SKYDNIR_ENGINE_TCP_HOST", bridge)
        self.assertIn("PDOCKER_ENGINE_TCP_HOST", bridge)
        self.assertIn('"--host", engine_tcp_host', bridge)
        self.assertIn("SKYDNIR_ENGINE_TCP_HOST_EFFECTIVE", bridge)

    def test_pdockerd_has_termport_required_docker_engine_routes(self):
        source = PDOCKERD.read_text(encoding="utf-8")
        for marker in [
            'path == "/_ping"',
            'path == "/version"',
            'path == "/containers/json"',
            r"^/containers/(.+?)/exec$",
            r"^/exec/(.+?)/start$",
            r"^/exec/(.+?)/resize$",
            "HTTP/1.1 101 UPGRADED",
            "Upgrade: tcp",
            "AttachStdin",
            "AttachStdout",
            "AttachStderr",
            "Tty",
            "_resize_pty_fd",
        ]:
            self.assertIn(marker, source)

    def test_exec_start_tty_path_is_raw_stream_not_multiplexed(self):
        source = PDOCKERD.read_text(encoding="utf-8")
        self.assertIn("if tty:", source)
        self.assertIn("self._send_hijacked()", source)
        self.assertIn("tty_output(rest)", source)
        self.assertIn("bytes([st, 0, 0, 0]) + len(rest).to_bytes(4, \"big\")", source)


    def test_pdockerd_tcp_listener_accepts_host_argument_without_requiring_unix_socket(self):
        source = PDOCKERD.read_text(encoding="utf-8")
        for marker in [
            'parser.add_argument("--host"',
            'parser.add_argument("--no-socket"',
            'ThreadingTCPHTTPServer((host, int(port)), DockerAPIHandler)',
            'servers.append((f"tcp://{host}:{port}", srv))',
            'if not args.no_socket:',
        ]:
            self.assertIn(marker, source)

    def test_exec_start_tty_hijack_uses_raw_tty_stream_contract(self):
        source = PDOCKERD.read_text(encoding="utf-8")
        for marker in [
            'm = re.match(r"^/exec/(.+?)/start$", path)',
            'HTTP/1.1 101 UPGRADED',
            'Connection: Upgrade',
            'Upgrade: tcp',
            'if tty:',
            'tty_output(rest)',
            'bytes([st, 0, 0, 0]) + len(rest).to_bytes(4, "big")',
        ]:
            self.assertIn(marker, source)

    def test_exec_resize_validates_and_applies_rows_cols_contract(self):
        source = PDOCKERD.read_text(encoding="utf-8")
        for marker in [
            'm = re.match(r"^/exec/(.+?)/resize$", path)',
            'rows = int(query.get("h", ["0"])[0])',
            'cols = int(query.get("w", ["0"])[0])',
            'if rows <= 0 or cols <= 0:',
            'self._send_json(400, {"message": "invalid terminal size"})',
            'ses["rows"] = rows',
            'ses["cols"] = cols',
            'proc.resize(rows, cols)',
            'self.send_response(201)',
        ]:
            self.assertIn(marker, source)

    def test_existing_android_terminal_client_uses_same_docker_exec_contract(self):
        source = ENGINE_EXEC_SESSION.read_text(encoding="utf-8")
        for marker in [
            '"/containers/${DockerEngineClient.encodePath(containerId)}/exec"',
            'append("POST /exec/$execId/start HTTP/1.1\\r\\n")',
            'append("Connection: Upgrade\\r\\n")',
            'append("Upgrade: tcp\\r\\n")',
            '"/exec/${DockerEngineClient.encodePath(execId)}/resize?h=$rows&w=$cols"',
            '"TERM=xterm-256color"',
            '"COLORTERM=truecolor"',
        ]:
            self.assertIn(marker, source)


if __name__ == "__main__":
    unittest.main()

import json
import os
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROBE = (
    ROOT
    / "app"
    / "src"
    / "main"
    / "assets"
    / "project-library"
    / "llama-cpp-gpu"
    / "scripts"
    / "pdocker-llama-correctness.sh"
)


class _LlamaProbeHandler(BaseHTTPRequestHandler):
    responses = {}

    def log_message(self, _format, *_args):
        return

    def do_GET(self):
        if self.path != "/health":
            self.send_error(404)
            return
        self._json({"status": "ok"})

    def do_POST(self):
        if self.path != "/completion":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        content = self.responses.get(payload.get("prompt"), "")
        self._json({"content": content})

    def _json(self, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class LlamaCorrectnessProbeTest(unittest.TestCase):
    def _run_probe(self, responses):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            server = HTTPServer(("127.0.0.1", 0), _LlamaProbeHandler)
            _LlamaProbeHandler.responses = responses
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                env = os.environ.copy()
                env.update(
                    {
                        "LLAMA_ARG_PORT": str(server.server_port),
                        "LLAMA_CORRECTNESS_FILE": str(tmp / "correctness.json"),
                        "LLAMA_CORRECTNESS_PROFILE_COPY": str(tmp / "profile.json"),
                        "LLAMA_CORRECTNESS_TIMEOUT": "5",
                        "PDOCKER_GPU_MODE": "vulkan-raw",
                        "LLAMA_ARG_N_GPU_LAYERS": "1",
                    }
                )
                result = subprocess.run(
                    [str(PROBE)],
                    cwd=ROOT,
                    env=env,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )
                report = json.loads((tmp / "correctness.json").read_text())
                profile_copy = json.loads((tmp / "profile.json").read_text())
                return result, report, profile_copy
            finally:
                server.shutdown()
                server.server_close()

    def test_probe_passes_when_required_prompt_matches(self):
        result, report, profile_copy = self._run_probe(
            {"2+3=": "5", "12*7=": "84"}
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(report["summary"]["correctness"], "pass")
        self.assertEqual(report["summary"]["gpu_correctness"], "pass")
        self.assertTrue(report["summary"]["benchmark_claim_allowed"])
        self.assertEqual(profile_copy["schema"], "pdocker.llama.correctness.v1")

    def test_probe_fails_when_gpu_logits_are_wrong(self):
        result, report, _profile_copy = self._run_probe(
            {"2+3=": "!", "12*7=": "!"}
        )
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(report["summary"]["correctness"], "fail")
        self.assertEqual(report["summary"]["gpu_correctness"], "fail")
        self.assertFalse(report["summary"]["benchmark_claim_allowed"])
        self.assertEqual(report["probes"][0]["content"], "!")


if __name__ == "__main__":
    unittest.main()

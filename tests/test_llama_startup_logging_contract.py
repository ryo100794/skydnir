import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
START = ROOT / "app/src/main/assets/project-library/llama-cpp-gpu/scripts/start-llama-server.sh"


class LlamaStartupLoggingContractTest(unittest.TestCase):
    """Host-only contract for llama-cpp-gpu startup diagnostics.

    This is the maintained replacement for the old ad-hoc
    `scripts/verify-llama-startup-logging.py` helper.  It stubs
    `pdocker-gpu-profile`, keeps the model missing so the entrypoint reaches
    its status-page path, and verifies that early tee logging plus
    `llama-startup.json` preserve the GPU profile and KV-offload guard state.
    """

    def test_missing_model_startup_writes_early_log_and_json(self):
        with tempfile.TemporaryDirectory(prefix="pdocker-llama-startup-") as td:
            tmp = Path(td)
            fakebin = tmp / "bin"
            profiles = tmp / "profiles"
            logs = tmp / "logs"
            fakebin.mkdir()
            profiles.mkdir()
            logs.mkdir()

            profiler = fakebin / "pdocker-gpu-profile"
            profiler.write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                "out=\"$1\"\n"
                "diag=\"${LLAMA_GPU_DIAGNOSTICS:?}\"\n"
                "mkdir -p \"$(dirname \"$out\")\" \"$(dirname \"$diag\")\"\n"
                "echo PROFILE_STDOUT_SENTINEL\n"
                "echo PROFILE_STDERR_SENTINEL >&2\n"
                "cat >\"$out\" <<'ENV'\n"
                "export LLAMA_GPU_BACKEND=vulkan\n"
                "export LLAMA_ARG_N_GPU_LAYERS=3\n"
                "export LLAMA_ARG_CTX=2048\n"
                "export LLAMA_ARG_THREADS=7\n"
                "export VK_ICD_FILENAMES=/tmp/fake-vulkan.json\n"
                "export PDOCKER_GPU_QUEUE_SOCKET=/tmp/pdocker-gpu.sock\n"
                "export PDOCKER_VULKAN_ICD_KIND=pdocker-adreno\n"
                "export PDOCKER_VULKAN_ICD_READY=0\n"
                "ENV\n"
                "printf '{\"backend\":\"vulkan\"}\\n' >\"$diag\"\n",
                encoding="utf-8",
            )
            profiler.chmod(0o755)

            log_file = logs / "llama-server.log"
            startup_json = logs / "llama-startup.json"
            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fakebin}:{env.get('PATH', '')}",
                    "LLAMA_LOG_FILE": str(log_file),
                    "LLAMA_STARTUP_JSON": str(startup_json),
                    "LLAMA_GPU_PROFILE": str(profiles / "pdocker-gpu.env"),
                    "LLAMA_GPU_DIAGNOSTICS": str(profiles / "pdocker-gpu-diagnostics.json"),
                    "LLAMA_GPU_PROFILE_REFRESH": "always",
                    "LLAMA_ARG_MODEL": str(tmp / "missing.gguf"),
                    "LLAMA_MODEL_URL": "",
                    "LLAMA_ARG_PORT": "0",
                    "LLAMA_EXTRA_ARGS": "--jinja",
                }
            )

            proc = subprocess.run(
                ["timeout", "3", "bash", str(START)],
                cwd=tmp,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertIn(proc.returncode, {0, 124}, proc.stdout)
            self.assertTrue(log_file.exists(), "LLAMA_LOG_FILE was not created")
            log_text = log_file.read_text(encoding="utf-8", errors="replace")
            combined_log = proc.stdout + "\n" + log_text
            self.assertIn("PROFILE_STDOUT_SENTINEL", combined_log)
            self.assertIn("PROFILE_STDERR_SENTINEL", combined_log)
            self.assertTrue(startup_json.exists(), "llama-startup.json was not written")

            report = json.loads(startup_json.read_text(encoding="utf-8"))
            self.assertEqual(env["LLAMA_GPU_PROFILE"], report["profile_path"])
            self.assertEqual(0, report["profile_refresh_rc"])
            resolved = report.get("resolved", {})
            expected_resolved = {
                "LLAMA_GPU_BACKEND": "vulkan",
                "LLAMA_ARG_N_GPU_LAYERS": "3",
                "LLAMA_ARG_CTX": "2048",
                "LLAMA_ARG_THREADS": "7",
                "VK_ICD_FILENAMES": "/tmp/fake-vulkan.json",
                "PDOCKER_GPU_QUEUE_SOCKET": "/tmp/pdocker-gpu.sock",
                "PDOCKER_VULKAN_ICD_KIND": "pdocker-adreno",
                "PDOCKER_VULKAN_ICD_READY": "0",
            }
            for key, expected in expected_resolved.items():
                self.assertEqual(expected, resolved.get(key), key)
            self.assertTrue(report.get("memory", {}).get("MemAvailable"))
            self.assertIn("SwapFree", report.get("memory", {}))

            argv = report.get("llama_server_argv", [])
            self.assertIn("--no-kv-offload", argv)
            guard = report.get("kv_offload_guard", {})
            self.assertIs(True, guard.get("active"))
            self.assertIs(True, guard.get("added_arg"))
            self.assertIs(True, guard.get("disabled_effective"))


if __name__ == "__main__":
    unittest.main()

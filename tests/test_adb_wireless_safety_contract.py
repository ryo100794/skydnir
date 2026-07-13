import json
import os
import re
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIRS = (
    ROOT / "scripts",
    ROOT / "tests",
)


class AdbWirelessSafetyContractTest(unittest.TestCase):

    def _run_script_with_adb_stub(self, script_name, *args, env_overrides=None):
        serial = "127.0.0.1:37777"
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            log_path = td_path / "adb.jsonl"
            adb_path = td_path / "adb"
            adb_path.write_text(textwrap.dedent(r'''
                #!/usr/bin/env python3
                import json
                import os
                import sys

                original_args = sys.argv[1:]
                with open(os.environ["ADB_LOG"], "a", encoding="utf-8") as log:
                    log.write(json.dumps(original_args) + "\n")

                args = list(original_args)
                if len(args) >= 2 and args[0] == "-s":
                    args = args[2:]
                cmd = args[0] if args else ""
                serial = os.environ.get("ANDROID_SERIAL") or os.environ.get("PDOCKER_ADB_SERIAL") or "127.0.0.1:37777"

                def shell_command():
                    return " ".join(args[1:]) if len(args) > 1 else ""

                if cmd == "devices":
                    print("List of devices attached")
                    print(f"{serial}\tdevice product:stub")
                    sys.exit(0)
                if cmd == "connect":
                    print(f"connected to {args[1] if len(args) > 1 else serial}")
                    sys.exit(0)
                if cmd == "get-serialno":
                    print(serial)
                    sys.exit(0)
                if cmd in {"install", "push"}:
                    sys.exit(0)
                if cmd == "logcat":
                    if "-d" in args:
                        print("I pdocker: stub logcat")
                    sys.exit(0)
                if cmd == "shell":
                    sh = shell_command()
                    if "/proc/meminfo" in sh:
                        print("MemAvailable: 123456 kB")
                        print("SwapTotal: 0 kB")
                        print("SwapFree: 0 kB")
                        sys.exit(0)
                    if "getprop ro.build.version.sdk" in sh:
                        print("29")
                        sys.exit(0)
                    if "getprop ro.build.version.release" in sh:
                        print("10")
                        sys.exit(0)
                    if "getprop ro.product.model" in sh:
                        print("stub-model")
                        sys.exit(0)
                    if "cmd package resolve-activity" in sh:
                        package = sh.split()[-1].strip("'\"")
                        print(f"{package}/.MainActivity")
                        sys.exit(0)
                    if "dumpsys package" in sh:
                        print("targetSdk=29")
                        print("seInfo=default")
                        print("codePath=/data/app/stub")
                        sys.exit(0)
                    if "ps -AZ" in sh:
                        print("u:r:untrusted_app:s0 u0_a123 123 1 io.github.ryo100794.pdocker")
                        sys.exit(0)
                    if "pdocker-memory-pager-managed-poc" in sh:
                        print("pager-managed-poc:result=ok")
                        print("pager-managed-poc:resident_pages=4")
                        print("pager-managed-poc:max_resident_pages=4")
                        print("pager-managed-poc:page_ins=1")
                        print("pager-managed-poc:page_outs=1")
                        print("pager-managed-poc:dirty_page_outs=1")
                        print("pager-managed-poc:bytes_in=4096")
                        print("pager-managed-poc:bytes_out=4096")
                        print("pager-managed-poc:elapsed_ns=1")
                        print("exact_rc=0")
                        sys.exit(0)
                    if "pdocker-memory-pager-transparent-poc" in sh:
                        print("pager-transparent-poc:result=ok")
                        print("pager-transparent-poc:registered=yes")
                        print("pager-transparent-poc:max_resident_pages=4")
                        print("pager-transparent-poc:page_ins=1")
                        print("pager-transparent-poc:page_outs=1")
                        print("pager-transparent-poc:dirty_page_outs=1")
                        print("pager-transparent-poc:bytes_in=4096")
                        print("pager-transparent-poc:bytes_out=4096")
                        print("pager-transparent-poc:elapsed_ns=1")
                        print("__PDOCKER_MEMORY_RING_BEGIN__")
                        print('{"ring_schema":"pdocker.memory-telemetry-ring.v1"}')
                        print("__PDOCKER_MEMORY_RING_END__")
                        print("__PDOCKER_MEMORY_SUMMARY_BEGIN__")
                        print('{"summary_schema":"pdocker.memory-telemetry-summary.v1"}')
                        print("__PDOCKER_MEMORY_SUMMARY_END__")
                        print("exact_rc=0")
                        sys.exit(0)
                    sys.exit(0)
                sys.exit(0)
            ''').lstrip(), encoding="utf-8")
            adb_path.chmod(0o755)

            env = os.environ.copy()
            env.update({
                "PATH": f"{td}{os.pathsep}{env.get('PATH', '')}",
                "ADB": "adb",
                "ADB_LOG": str(log_path),
                "ANDROID_SERIAL": serial,
                "PDOCKER_ADB_SERIAL": serial,
                "OUT": str(td_path / f"{script_name}.json"),
                "INSTALL_APK": "0",
                "SKYDNIR_ANDROID_PACKAGE": "io.github.ryo100794.pdocker.compat",
                "SKYDNIR_PACKAGE": "io.github.ryo100794.pdocker",
                "PDOCKER_PACKAGE": "io.github.ryo100794.pdocker",
            })
            env.pop("PDOCKER_ADB_CONNECT", None)
            env.pop("PDOCKER_API29_FORCE_STOP_ALLOWLIST", None)
            if env_overrides:
                env.update(env_overrides)

            result = subprocess.run(
                [str(ROOT / "scripts" / script_name), *args],
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=20,
            )
            commands = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
            return result, commands

    def _adb_payload(self, command):
        if len(command) >= 2 and command[0] == "-s":
            return command[2:]
        return command

    def _command_contains(self, commands, needle):
        return any(needle in " ".join(self._adb_payload(command)) for command in commands)

    def test_project_automation_does_not_toggle_wireless_debugging(self):
        """Device-side ADB/Wi-Fi debug switches are user-owned state.

        Test and benchmark automation may connect to an already-advertised
        endpoint, install APKs, forward ports, and run app-owned commands.  It
        must not disable wireless debugging, restart adbd, or mutate tcp-port
        properties because that invalidates long device runs and looks exactly
        like an external flaky disconnect.
        """

        forbidden = {
            "settings put global adb_wifi_enabled": "must not toggle Android wireless debugging",
            "settings put global adb_enabled": "must not toggle Android debugging",
            "svc wifi": "must not toggle Wi-Fi as a side effect of tests",
            "cmd wifi": "must not toggle Wi-Fi as a side effect of tests",
            "setprop service.adb.tcp.port": "must not rewrite adbd tcp port",
            "setprop persist.adb.tcp.port": "must not rewrite persistent adbd tcp port",
            "stop adbd": "must not restart the device adbd daemon",
            "start adbd": "must not restart the device adbd daemon",
            "adb tcpip": "must not switch device adbd transport mode",
            "adb usb": "must not switch device adbd transport mode",
        }

        offenders = []
        for root in SCRIPT_DIRS:
            for path in root.rglob("*"):
                if path.is_dir() or path.suffix in {".pyc", ".json"}:
                    continue
                if path == Path(__file__).resolve():
                    continue
                try:
                    text = path.read_text(errors="ignore")
                except OSError:
                    continue
                for needle, reason in forbidden.items():
                    if re.search(re.escape(needle), text, re.IGNORECASE):
                        offenders.append(f"{path.relative_to(ROOT)}: {needle} ({reason})")

        self.assertEqual([], offenders)


    def test_memory_pager_scripts_use_preconnected_serial_without_auto_connect(self):
        serial = "127.0.0.1:37777"
        for script in (
            "android-memory-pager-managed-poc.sh",
            "android-memory-pager-transparent-poc.sh",
        ):
            with self.subTest(script=script):
                result, commands = self._run_script_with_adb_stub(script)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertFalse(
                    any(self._adb_payload(command)[:1] == ["connect"] for command in commands),
                    commands,
                )
                self.assertTrue(
                    any(command[:3] == ["-s", serial, "shell"] for command in commands),
                    commands,
                )

    def test_memory_pager_adb_connect_is_explicit_opt_in(self):
        serial = "127.0.0.1:37777"
        result, commands = self._run_script_with_adb_stub(
            "android-memory-pager-managed-poc.sh",
            "--adb-connect",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(["connect", serial], commands)

        result, commands = self._run_script_with_adb_stub(
            "android-memory-pager-transparent-poc.sh",
            env_overrides={"PDOCKER_ADB_CONNECT": "1"},
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(["connect", serial], commands)

    def test_api29_feasibility_skips_force_stop_by_default(self):
        result, commands = self._run_script_with_adb_stub(
            "android-api29-direct-feasibility.sh",
            "--no-install",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self._command_contains(commands, "am force-stop"), commands)
        self.assertIn("skipping app force-stop", result.stdout)

    def test_api29_force_stop_requires_flag_and_allowlist(self):
        result, commands = self._run_script_with_adb_stub(
            "android-api29-direct-feasibility.sh",
            "--no-install",
            "--force-stop-app",
        )
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertFalse(self._command_contains(commands, "am force-stop"), commands)
        self.assertIn("requires the package", result.stderr)

        result, commands = self._run_script_with_adb_stub(
            "android-api29-direct-feasibility.sh",
            "--no-install",
            "--force-stop-app",
            "--force-stop-allowlist",
            "com.example,io.github.ryo100794.pdocker",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(
            self._command_contains(commands, "am force-stop io.github.ryo100794.pdocker"),
            commands,
        )

    def test_llama_gpu_compare_never_uses_client_side_adb_disconnect(self):
        compare = ROOT / "scripts" / "android-llama-gpu-compare.sh"
        self.assertNotIn("adb disconnect", compare.read_text(errors="ignore"))

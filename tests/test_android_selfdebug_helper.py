import os
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "android-selfdebug.sh"
DOC = ROOT / "docs" / "test" / "ANDROID_SELFDEBUG.md"


class AndroidSelfDebugHelperTest(unittest.TestCase):
    def test_helper_is_executable_localhost_only_and_documents_core_actions(self):
        mode = SCRIPT.stat().st_mode
        self.assertTrue(mode & stat.S_IXUSR, "android-selfdebug helper must be executable")
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("127.0.0.1", source)
        self.assertIn("active Wi-Fi association", source)
        self.assertIn("cannot bypass that OS prerequisite", source)
        self.assertIn("explain_wireless_debugging_prerequisite", source)
        self.assertIn("adb_output_failed", source)
        self.assertIn("failed to connect", source)
        self.assertIn("adb_plain pair", source)
        self.assertIn("adb_plain connect", source)
        self.assertIn("install -r", source)
        self.assertIn("cmd package resolve-activity --brief", source)
        self.assertIn("am start -n", source)
        self.assertIn("logcat -d", source)
        self.assertIn("run-as \"$PKG\"", source)
        self.assertIn("--unix-socket files/pdocker/pdockerd.sock", source)
        self.assertIn("http://d/_ping", source)
        self.assertIn("SKYDNIR_ANDROID_FLAVOR", source)
        self.assertIn("PDOCKER_ANDROID_FLAVOR", source)
        self.assertIn("SKYDNIR_PACKAGE", source)
        self.assertIn("SKYDNIR_APK", source)
        self.assertIn("io.github.ryo100794.pdocker.compat", source)
        self.assertIn("app-compat-debug.apk", source)
        self.assertIn("app-modern-debug.apk", source)
        self.assertNotIn("start-foreground-service", source)
        self.assertNotIn(" tcpip ", source)

    def test_helper_rejects_non_localhost_pair_and_connect_targets(self):
        for command in ("pair", "connect"):
            args = [str(SCRIPT), command, "192.168.1.20:37000"]
            if command == "pair":
                args.append("123456")
            result = subprocess.run(
                args,
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn("must be localhost", result.stderr)

    def test_helper_invokes_expected_adb_commands_with_fake_adb(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            log = tmp / "adb.log"
            apk = tmp / "app-compat-debug.apk"
            apk.write_text("fake apk\n", encoding="utf-8")
            fake_adb = tmp / "adb"
            fake_adb.write_text(
                textwrap.dedent(
                    f"""
                    #!/usr/bin/env bash
                    printf '%s\\n' "$*" >> {str(log)!r}
                    if [[ "$*" == *"cmd package resolve-activity --brief"* ]]; then
                      echo "io.github.ryo100794.pdocker.compat/io.github.ryo100794.pdocker.MainActivity"
                    elif [[ "$*" == *"pidof io.github.ryo100794.pdocker.compat"* ]]; then
                      echo "1234"
                    elif [[ "$*" == *"logcat -d"* ]]; then
                      echo "05-17 python.stderr: ready"
                    elif [[ "$1" == "pair" ]]; then
                      echo "Successfully paired"
                    elif [[ "$1" == "connect" ]]; then
                      echo "connected to $2"
                    else
                      echo "ok"
                    fi
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            fake_adb.chmod(0o755)
            env = os.environ.copy()
            env["ADB"] = str(fake_adb)
            env["ANDROID_SERIAL"] = "127.0.0.1:37777"
            env["SKYDNIR_APK"] = str(apk)

            commands = [
                [str(SCRIPT), "pair", "127.0.0.1:37111", "123456"],
                [str(SCRIPT), "connect", "127.0.0.1:37777"],
                [str(SCRIPT), "install-debug"],
                [str(SCRIPT), "start"],
                [str(SCRIPT), "ping-daemon"],
                [str(SCRIPT), "socket-get", "/version"],
                [str(SCRIPT), "run-as", "ls", "-la", "files"],
            ]
            for command in commands:
                result = subprocess.run(
                    command,
                    cwd=ROOT,
                    env=env,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            calls = log.read_text(encoding="utf-8")
            self.assertIn("pair 127.0.0.1:37111 123456", calls)
            self.assertIn("connect 127.0.0.1:37777", calls)
            self.assertIn("-s 127.0.0.1:37777 install -r", calls)
            self.assertIn("cmd package resolve-activity --brief io.github.ryo100794.pdocker.compat", calls)
            self.assertIn("shell am start -n io.github.ryo100794.pdocker.compat/io.github.ryo100794.pdocker.MainActivity", calls)
            self.assertIn("shell run-as io.github.ryo100794.pdocker.compat curl -fsS --unix-socket files/pdocker/pdockerd.sock http://d/_ping", calls)
            self.assertIn("shell run-as io.github.ryo100794.pdocker.compat curl -fsS --unix-socket files/pdocker/pdockerd.sock http://d/version", calls)
            self.assertIn("shell run-as io.github.ryo100794.pdocker.compat ls -la files", calls)

    def test_helper_treats_adb_connect_refused_output_as_failure_even_with_zero_rc(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            fake_adb = tmp / "adb"
            fake_adb.write_text(
                textwrap.dedent(
                    """
                    #!/usr/bin/env bash
                    if [[ "$1" == "connect" ]]; then
                      echo "failed to connect to '$2': Connection refused"
                      exit 0
                    fi
                    exit 0
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            fake_adb.chmod(0o755)
            env = os.environ.copy()
            env["ADB"] = str(fake_adb)

            result = subprocess.run(
                [str(SCRIPT), "connect", "127.0.0.1:5555"],
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("Connection refused", result.stderr)
            self.assertIn("Wireless debugging must already be enabled", result.stderr)
            self.assertIn("no root/userdebug privileges", result.stderr)


    def _main_activity_source(self):
        return (ROOT / "app/src/main/kotlin/io/github/ryo100794/pdocker/MainActivity.kt").read_text(encoding="utf-8")

    def _kotlin_function_body(self, source: str, name: str) -> str:
        marker = f"private fun {name}("
        start = source.index(marker)
        brace = source.index("{", start)
        depth = 0
        for idx in range(brace, len(source)):
            ch = source[idx]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return source[brace + 1:idx]
        self.fail(f"could not find body for {name}")

    def test_in_app_self_debug_bundle_is_adb_free_and_documents_exported(self):
        source = self._main_activity_source()
        render_body = self._kotlin_function_body(source, "renderDebugResources")
        export_body = self._kotlin_function_body(source, "exportSelfDebugBundle")
        bundle_body = self._kotlin_function_body(source, "selfDebugBundleJson")

        self.assertIn("action_export_self_debug_bundle", render_body)
        self.assertIn("openTextToolAsync", render_body)
        self.assertIn("exportSelfDebugBundle()", render_body)
        self.assertIn('File(diagnosticsDir, "self-debug-bundle-latest.json")', export_body)
        self.assertIn("selfDebugBundleJson()", export_body)
        self.assertIn("writeDocumentsFileForAutomation", export_body)
        self.assertIn('targetPath = "pdocker/diagnostics/self-debug-bundle-latest.json"', export_body)
        self.assertIn('mimeType = "application/json"', export_body)
        self.assertIn('bundle.put("DocumentsExport", latestExport)', export_body)
        self.assertIn('bundle.put("DocumentsEvidenceExport", evidenceExport)', export_body)
        self.assertIn('bundle.put("DocumentsExportRetry", retry)', export_body)
        self.assertIn("documents.success=", export_body)
        self.assertIn("documents.mode=", export_body)
        self.assertIn("documents.target=", export_body)
        self.assertIn("documents.evidence.target=", export_body)
        self.assertIn("documents.activeHostPath=", export_body)
        self.assertIn("It does not require USB, Wi-Fi ADB, run-as, or shell access", export_body)
        self.assertIn('.put("adb_independent", true)', bundle_body)
        self.assertIn('.put("requires_adb", false)', bundle_body)
        self.assertIn("NoUsbNoWifiFallback", bundle_body)
        self.assertIn("Use this UI-exported bundle plus Documents artifacts", bundle_body)
        self.assertIn("scripts/android-selfdebug.sh remains only a convenience wrapper", bundle_body)
        forbidden_runtime_dependencies = ("adb pair", "adb connect", "logcat -d", "run-as ", "am start")
        for forbidden in forbidden_runtime_dependencies:
            self.assertNotIn(forbidden, export_body + bundle_body)

    def test_self_debug_bundle_contains_engine_probe_and_resource_snapshots(self):
        source = self._main_activity_source()
        bundle_body = self._kotlin_function_body(source, "selfDebugBundleJson")
        memory_body = self._kotlin_function_body(source, "debugMemorySnapshot")
        process_body = self._kotlin_function_body(source, "debugProcessSnapshot")
        handle_body = self._kotlin_function_body(source, "debugHandleSnapshot")
        debug_processes_body = self._kotlin_function_body(source, "debugProcesses")
        engine_text_body = self._kotlin_function_body(source, "engineTextOrError")
        engine_json_body = self._kotlin_function_body(source, "engineJsonOrError")
        engine_array_body = self._kotlin_function_body(source, "engineArrayOrError")

        for key, probe in {
            '"Ping"': 'engineTextOrError("/_ping")',
            '"Version"': 'engineJsonOrError("/version")',
            '"Info"': 'engineJsonOrError("/info")',
            '"ContainersAll"': 'engineArrayOrError("/containers/json?all=1")',
        }.items():
            self.assertIn(key, bundle_body)
            self.assertIn(probe, bundle_body)
        self.assertIn("timeoutMs = 5_000", engine_text_body)
        self.assertIn("timeoutMs = 5_000", engine_json_body)
        self.assertIn("timeoutMs = 5_000", engine_array_body)
        self.assertIn('put("Status", resp.status)', engine_text_body + engine_json_body + engine_array_body)
        self.assertIn('put("Items", JSONArray', engine_array_body)
        self.assertGreaterEqual(source.count(".getOrElse { throwableJson(it) }"), 3)

        for key, producer in {
            '"memory_layers"': "memoryLayerSnapshot()",
            '"memory_snapshot_text"': "boundedDebugText(debugMemorySnapshot())",
            '"process_snapshot_text"': "boundedDebugText(debugProcessSnapshot())",
            '"handle_snapshot_text"': "boundedDebugText(debugHandleSnapshot(), maxChars = 96 * 1024)",
        }.items():
            self.assertIn(key, bundle_body)
            self.assertIn(producer, bundle_body)
        for field in ("OsMemTotal", "OsMemAvailable", "OsSwapTotal", "OsSwapFree", "PdockerProcessCount", "PdockerRss", "PdockerSwap"):
            self.assertIn(field, bundle_body)
        self.assertIn("/system/memory-pressure", memory_body)
        self.assertIn('File("/proc/meminfo")', memory_body)
        self.assertIn("debugProcesses().forEach", memory_body)
        self.assertIn("PID     PPID    STATE", process_body)
        self.assertIn("fdCount", process_body)
        self.assertIn('File("/proc").listFiles()', debug_processes_body)
        self.assertIn('File(dir, "fd").list()?.size', debug_processes_body)
        self.assertIn('File("/proc/${proc.pid}/fd")', handle_body)
        self.assertIn("Os.readlink", handle_body)
        self.assertIn("fds.take(256)", handle_body)

    def test_runbook_points_to_helper_without_replacing_manual_workflow(self):
        doc = DOC.read_text(encoding="utf-8")
        self.assertIn("scripts/android-selfdebug.sh", doc)
        self.assertIn("adb pair 127.0.0.1", doc)
        self.assertIn("adb connect 127.0.0.1", doc)
        self.assertIn("Pair device with pairing code", doc)
        self.assertIn("disabled until the phone is associated with a", doc)
        self.assertIn("no USB and no Wi-Fi association", doc)
        self.assertIn("in-app diagnostics", doc)
        self.assertIn("ADB-Free Self-Debug Bundle", doc)
        self.assertIn("pdocker.self-debug.bundle.v1", doc)
        self.assertIn("self-debug-bundle-latest.json", doc)


if __name__ == "__main__":
    unittest.main()

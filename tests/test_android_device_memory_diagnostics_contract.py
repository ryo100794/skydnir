import json
import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "android-device-memory-diagnostics.sh"


class AndroidDeviceMemoryDiagnosticsContractTest(unittest.TestCase):
    def setUp(self):
        self.source = SCRIPT.read_text(encoding="utf-8")

    def test_script_collects_standalone_memory_process_and_swap_diagnostics(self):
        self.assertIn("pdocker.android.device-memory-diagnostics.v1", self.source)
        self.assertIn("MemAvailable", self.source)
        self.assertIn("SwapFree", self.source)
        self.assertIn("/proc/pressure/memory", self.source)
        self.assertIn("/proc/vmstat", self.source)
        self.assertIn("/proc/swaps", self.source)
        self.assertIn("/sys/block/zram", self.source)
        self.assertIn("ps -A -o PID,PPID,RSS,VSZ,NAME,ARGS", self.source)
        self.assertIn('"pdocker_process_rss_mb_total"', self.source)
        self.assertIn('"stale_llama_process_hint"', self.source)
        self.assertIn('"top_memory_process_sample"', self.source)

    def test_script_policy_is_read_only_and_does_not_kill_or_start_work(self):
        self.assertIn('"read_only": True', self.source)
        self.assertIn('"no_llama_compare_started": True', self.source)
        self.assertIn('"no_pdockerd_start": True', self.source)
        self.assertIn('"no_container_start": True', self.source)
        self.assertIn('"no_force_stop_user_apps": True', self.source)
        self.assertIn("Low SwapFree on Android zram is advisory by default", self.source)
        forbidden = [
            "am force-stop",
            "SMOKE_START",
            "containers/create",
            "containers/start",
            "/containers/",
            "pkill",
            "killall",
            " toybox nc ",
        ]
        for needle in forbidden:
            self.assertNotIn(needle, self.source)

    def test_fake_adb_run_writes_expected_json_without_destructive_commands(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            log = tmp / "adb.log"
            out = tmp / "diagnostics.json"
            fake_adb = tmp / "adb"
            fake_adb.write_text(
                textwrap.dedent(
                    f"""
                    #!/usr/bin/env bash
                    echo "$*" >> {str(log)!r}
                    if [[ "$1" != "shell" ]]; then exit 0; fi
                    cmd="$2"
                    case "$cmd" in
                      *'/proc/meminfo'*)
                        cat <<'EOF'
                    MemTotal:        4096000 kB
                    MemFree:          512000 kB
                    MemAvailable:    1024000 kB
                    SwapCached:        65536 kB
                    SwapTotal:       2097152 kB
                    SwapFree:           5120 kB
                    Cached:           256000 kB
                    EOF
                        ;;
                      *'free -m'*)
                        cat <<'EOF'
                                  total        used        free      shared  buff/cache   available
                    Mem:           4000        2500         500          10        1000        1000
                    Swap:          2048        2043           5
                    EOF
                        ;;
                      *'/proc/vmstat'*)
                        echo 'pswpin 12'
                        echo 'pswpout 34'
                        echo 'pgmajfault 56'
                        ;;
                      *'/proc/pressure/memory'*)
                        echo 'some avg10=0.50 avg60=0.10 avg300=0.03 total=1234'
                        echo 'full avg10=0.01 avg60=0.00 avg300=0.00 total=42'
                        ;;
                      *'/proc/swaps'*)
                        cat <<'EOF'
                    Filename                                Type            Size            Used            Priority
                    /dev/block/zram0                        partition       2097148         2092028         32767
                    --- /sys/block/zram0/mm_stat
                    1 2 3 4 5 6 7 8
                    EOF
                        ;;
                      *'run-as'*'pdockerd.sock'*)
                        echo 'present'
                        ;;
                      *'run-as'*'ps -A'*)
                        cat <<'EOF'
                    PID PPID RSS VSZ NAME ARGS
                    222 1 65536 300000 pdockerd /data/user/0/io.github.ryo100794.pdocker.compat/files/pdocker/pdockerd
                    EOF
                        ;;
                      *'/proc/[0-9]'*)
                        echo '333 VmRSS: 40960 kB /data/local/tmp/pdocker-llama-cpp llama-server'
                        ;;
                      *'ps -A'*)
                        cat <<'EOF'
                    PID PPID RSS VSZ NAME ARGS
                    111 1 131072 400000 chrome com.android.chrome
                    222 1 65536 300000 pdockerd /data/user/0/io.github.ryo100794.pdocker.compat/files/pdocker/pdockerd
                    333 222 40960 200000 llama-server /usr/local/bin/llama-server
                    EOF
                        ;;
                    esac
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            fake_adb.chmod(0o755)

            env = os.environ.copy()
            env["ADB"] = str(fake_adb)
            subprocess.run(
                [str(SCRIPT), "--out", str(out), "--process-limit", "8"],
                cwd=ROOT,
                env=env,
                check=True,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            report = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(report["schema"], "pdocker.android.device-memory-diagnostics.v1")
            self.assertEqual(report["memory"]["mem_available_mb"], 1000)
            self.assertEqual(report["memory"]["swap_free_mb"], 5)
            self.assertEqual(report["vmstat"]["pswpout"], 34)
            self.assertEqual(report["pressure"]["memory"]["some"]["total"], 1234)
            self.assertEqual(report["pdockerd_socket"], "present")
            self.assertGreaterEqual(report["pdocker_process_count"], 2)
            self.assertTrue(report["stale_llama_process_hint"])
            self.assertTrue(report["collection_policy"]["no_force_stop_user_apps"])
            self.assertTrue(report["collection_policy"]["no_llama_compare_started"])

            commands = log.read_text(encoding="utf-8")
            self.assertIn("/proc/meminfo", commands)
            self.assertIn("/proc/pressure/memory", commands)
            self.assertIn("ps -A -o PID,PPID,RSS,VSZ,NAME,ARGS", commands)
            self.assertNotIn("am force-stop", commands)
            self.assertNotIn("/containers/", commands)


if __name__ == "__main__":
    unittest.main()

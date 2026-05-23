import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIRS = (
    ROOT / "scripts",
    ROOT / "tests",
)


class AdbWirelessSafetyContractTest(unittest.TestCase):
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

    def test_llama_gpu_compare_never_uses_client_side_adb_disconnect(self):
        compare = ROOT / "scripts" / "android-llama-gpu-compare.sh"
        self.assertNotIn("adb disconnect", compare.read_text(errors="ignore"))

import importlib.machinery
import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
PDOCKERD = ROOT / "docker-proot-setup" / "bin" / "pdockerd"
ASSET_PDOCKERD = ROOT / "app" / "src" / "main" / "assets" / "pdockerd" / "pdockerd"


def load_pdockerd(home):
    module_name = f"pdockerd_state_io_{uuid.uuid4().hex}"
    loader = importlib.machinery.SourceFileLoader(module_name, str(PDOCKERD))
    spec = importlib.util.spec_from_loader(module_name, loader)
    module = importlib.util.module_from_spec(spec)
    env = {
        "PDOCKER_HOME": str(home),
        "PDOCKER_TMP_DIR": str(home / "tmp"),
        "PDOCKER_RUNTIME_BACKEND": "direct",
        "PDOCKER_DIRECT_EXECUTOR": "",
    }
    with mock.patch.dict(os.environ, env, clear=False):
        loader.exec_module(module)
    return module


class ContainerStateIoTest(unittest.TestCase):
    def test_container_state_save_uses_unique_atomic_temp_and_fsync(self):
        source = PDOCKERD.read_text()
        self.assertIn('tmp = f"{path}.tmp.{os.getpid()}.{threading.get_ident()}"', source)
        self.assertIn("os.fsync(f.fileno())", source)
        self.assertIn("os.replace(tmp, path)", source)
        self.assertEqual(source, ASSET_PDOCKERD.read_text())

    def test_new_engine_container_ids_are_full_64_hex(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            mod = load_pdockerd(root / "pdocker")
            ids = {mod._new_container_id() for _ in range(16)}
            self.assertEqual(len(ids), 16)
            for cid in ids:
                self.assertRegex(cid, r"^[0-9a-f]{64}$")

        source = PDOCKERD.read_text()
        self.assertIn("def _new_container_id", source)
        self.assertIn("hashlib.sha256(seed).hexdigest()", source)
        self.assertIn("cid = _new_container_id()", source)
        self.assertNotIn("cid = uuid.uuid4().hex\n    image = config.get", source)

    def test_service_truth_log_marker_contract_is_stable(self):
        source = PDOCKERD.read_text()
        for token in [
            "def _append_container_log_marker",
            "pdocker.service-truth-log-marker.v1",
            "pdocker-service-truth-marker ",
            '"container_id"',
            '"project"',
            '"service"',
            '"pid"',
            '_append_container_log_marker(state, "container-start", proc.pid)',
            '_append_container_log_marker(state, "container-live-reconciled", live_pid)',
        ]:
            self.assertIn(token, source)
        self.assertEqual(source, ASSET_PDOCKERD.read_text())

    def test_container_memory_exit_classification_uses_summary_then_sigkill(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            mod = load_pdockerd(root / "pdocker")
            cid = "memexit"
            cdir = Path(mod.CONTAINERS_DIR) / cid
            cdir.mkdir(parents=True)
            state = {
                "Id": cid,
                "Name": "/memexit",
                "State": {
                    "Running": False,
                    "Status": "exited",
                    "ExitCode": 12,
                    "PdockerRawReturnCode": 12,
                    "PdockerSignal": 0,
                },
                "Config": {"Env": []},
                "NetworkSettings": {"Ports": {}},
            }
            (cdir / "memory-summary.json").write_text(json.dumps({
                "summary_schema": "pdocker.memory-telemetry-summary.v1",
                "classification": "allocation_denied_enomem",
                "ring_path": str(cdir / "memory-ring.jsonl"),
            }))

            evidence = mod._classify_container_memory_exit(state)

            self.assertEqual(evidence["ExitClassification"], "allocation_denied_enomem")
            self.assertFalse(evidence["LmkSuspected"])
            self.assertFalse(state["State"]["OOMKilled"])
            self.assertIn("memory-summary.json", evidence["Artifacts"]["SummaryPath"])

            state["State"].update({
                "ExitCode": 137,
                "PdockerRawReturnCode": -9,
                "PdockerSignal": 9,
            })
            (cdir / "memory-summary.json").unlink()

            evidence = mod._classify_container_memory_exit(state)

            self.assertEqual(evidence["ExitClassification"], "sigkill-or-lmk-suspected")
            self.assertTrue(evidence["LmkSuspected"])
            self.assertTrue(state["State"]["OOMKilled"])

    def test_container_state_loader_repairs_trailing_stale_json(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            mod = load_pdockerd(root / "pdocker")
            cid = "stateio"
            cdir = Path(mod.CONTAINERS_DIR) / cid
            cdir.mkdir(parents=True)
            state = {"Id": cid, "State": {"Running": True}, "Config": {"Env": []}}
            path = cdir / "state.json"
            path.write_text(json.dumps(state, indent=2) + "       " + '"stale": true}\n')

            loaded = mod.load_container_state(cid)

            self.assertEqual(loaded, state)
            self.assertEqual(json.loads(path.read_text()), state)

    def test_reconcile_clears_stale_healthy_status_for_exited_container(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            mod = load_pdockerd(root / "pdocker")
            cid = "stalehealthy"
            state = {
                "Id": cid,
                "Name": "/stalehealthy",
                "Config": {"Env": [], "Healthcheck": {"Test": ["CMD", "true"]}},
                "State": {
                    "Running": False,
                    "Status": "exited",
                    "ExitCode": 255,
                    "Health": {
                        "Status": "healthy",
                        "FailingStreak": 0,
                        "Log": [],
                    },
                },
                "NetworkSettings": {"Ports": {}},
            }
            mod.save_container_state(cid, state)

            reconciled = mod.reconcile_container_state(state)

            health = reconciled["State"]["Health"]
            self.assertFalse(reconciled["State"]["Running"])
            self.assertEqual(health["Status"], "unhealthy")
            self.assertTrue(health["PdockerStopped"])
            self.assertIn("container is not running", health["Log"][-1]["Output"])

    def test_health_monitor_cannot_mark_stopped_container_healthy(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            mod = load_pdockerd(root / "pdocker")
            cid = "stoppedhealth"
            state = {
                "Id": cid,
                "Name": "/stoppedhealth",
                "Config": {"Env": [], "Healthcheck": {"Test": ["CMD", "true"]}},
                "State": {
                    "Running": False,
                    "Status": "exited",
                    "ExitCode": 0,
                    "Health": {
                        "Status": "starting",
                        "FailingStreak": 0,
                        "Log": [],
                    },
                },
                "NetworkSettings": {"Ports": {}},
            }
            mod.save_container_state(cid, state)

            mod._set_health(cid, "healthy", 0, "")
            loaded = mod.load_container_state(cid)

            health = loaded["State"]["Health"]
            self.assertEqual(health["Status"], "unhealthy")
            self.assertGreaterEqual(health["FailingStreak"], 1)
            self.assertTrue(health["PdockerStopped"])

    def test_stop_container_kills_orphan_runtime_process_by_container_path(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            mod = load_pdockerd(root / "pdocker")
            cid = "orphanruntime"
            cdir = Path(mod.CONTAINERS_DIR) / cid
            rootfs = cdir / "rootfs"
            rootfs.mkdir(parents=True)
            state = {
                "Id": cid,
                "Name": "/orphanruntime",
                "Config": {"Env": []},
                "State": {
                    "Running": True,
                    "Status": "running",
                    "Pid": 0,
                    "PdockerKnownPids": [],
                    "ExitCode": 0,
                },
                "NetworkSettings": {"Ports": {}},
            }
            mod.save_container_state(cid, state)
            proc = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                cwd=rootfs,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                preexec_fn=os.setsid,
            )
            try:
                self.assertTrue(mod.stop_container(cid, timeout=0.2))
                proc.wait(timeout=5)
                loaded = mod.load_container_state(cid)
                st = loaded["State"]
                self.assertFalse(st["Running"])
                self.assertEqual(st["Pid"], 0)
                self.assertEqual(st["PdockerKnownPids"], [])
                self.assertTrue(st["PdockerTeardown"]["NoOrphanProcesses"])
                self.assertEqual(st["PdockerTeardown"]["Survivors"], [])
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=5)

    def test_stop_container_kills_late_direct_child_in_launcher_process_group(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            mod = load_pdockerd(root / "pdocker")
            cid = "latedirectchild"
            cdir = Path(mod.CONTAINERS_DIR) / cid
            rootfs = cdir / "rootfs"
            rootfs.mkdir(parents=True)
            marker = root / "late-child.pid"
            parent_marker = root / "late-parent.pid"
            parent_script = root / "spawn_late_child.py"
            parent_script.write_text(
                """
import os
import signal
import subprocess
import sys
import time

marker = sys.argv[1]
parent_marker = sys.argv[2]

def on_term(signum, frame):
    subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import os, sys, time; open(sys.argv[1], 'w').write(str(os.getpid())); time.sleep(60)",
            marker,
        ],
        cwd="/",
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(0.2)
    raise SystemExit(0)

signal.signal(signal.SIGTERM, on_term)
open(parent_marker, "w").write(str(os.getpid()))
while True:
    time.sleep(1)
""".strip()
            )
            proc = subprocess.Popen(
                [sys.executable, str(parent_script), str(marker), str(parent_marker)],
                cwd=rootfs,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                preexec_fn=os.setsid,
            )
            try:
                for _ in range(50):
                    if parent_marker.exists():
                        break
                    time.sleep(0.05)
                self.assertTrue(parent_marker.exists())
                launcher_start = mod._pid_start_time(proc.pid)
                launcher_pgid = os.getpgid(proc.pid)
                state = {
                    "Id": cid,
                    "Name": "/latedirectchild",
                    "Config": {"Env": []},
                    "State": {
                        "Running": True,
                        "Status": "running",
                        "Pid": proc.pid,
                        "PidStartTime": launcher_start,
                        "PdockerLauncherPid": proc.pid,
                        "PdockerLauncherPidStartTime": launcher_start,
                        "PdockerLauncherPgid": launcher_pgid,
                        "PdockerProcessGroupId": launcher_pgid,
                        "PdockerKnownPids": [{"Pid": proc.pid, "StartTime": launcher_start}],
                        "ExitCode": 0,
                    },
                    "NetworkSettings": {"Ports": {}},
                }
                mod.save_container_state(cid, state)
                with mod.running_lock:
                    mod.running_containers[cid] = proc

                self.assertTrue(mod.stop_container(cid, timeout=0.2))
                proc.wait(timeout=5)

                for _ in range(50):
                    child_pid = int(marker.read_text()) if marker.exists() else 0
                    if child_pid and not mod._pid_alive(child_pid):
                        break
                    time.sleep(0.05)
                self.assertTrue(marker.exists(), "late direct child marker was not written")
                child_pid = int(marker.read_text())
                self.assertFalse(mod._pid_alive(child_pid), f"late direct child {child_pid} survived")
                loaded = mod.load_container_state(cid)
                st = loaded["State"]
                self.assertFalse(st["Running"])
                self.assertEqual(st["Pid"], 0)
                self.assertEqual(st["PdockerKnownPids"], [])
                self.assertNotIn("PdockerProcessGroupId", st)
                self.assertEqual(st["PdockerLastProcessGroupId"], launcher_pgid)
                self.assertTrue(st["PdockerTeardown"]["NoOrphanProcesses"])
                self.assertEqual(st["PdockerTeardown"]["Survivors"], [])
            finally:
                if proc.poll() is None:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    proc.wait(timeout=5)

    def test_stopped_container_state_drops_stale_launcher_pid_references(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            mod = load_pdockerd(root / "pdocker")
            cid = "stalelauncher"
            state = {
                "Id": cid,
                "Name": "/stalelauncher",
                "Config": {"Env": []},
                "State": {
                    "Running": False,
                    "Status": "exited",
                    "Pid": 12345,
                    "PidStartTime": "1",
                    "PdockerLauncherPid": 12345,
                    "PdockerLauncherPidStartTime": "1",
                    "PdockerLauncherPgid": 12345,
                    "PdockerProcessGroupId": 12345,
                    "PdockerKnownPids": [{"Pid": 12345, "StartTime": "1"}],
                    "ExitCode": 0,
                },
                "NetworkSettings": {"Ports": {}},
            }
            mod.save_container_state(cid, state)

            reconciled = mod.reconcile_container_state(state)
            st = reconciled["State"]

            self.assertEqual(st["Pid"], 0)
            self.assertNotIn("PidStartTime", st)
            self.assertNotIn("PdockerLauncherPid", st)
            self.assertNotIn("PdockerLauncherPidStartTime", st)
            self.assertNotIn("PdockerLauncherPgid", st)
            self.assertNotIn("PdockerProcessGroupId", st)
            self.assertEqual(st["PdockerKnownPids"], [])
            self.assertEqual(st["PdockerLastLauncherPid"], 12345)
            self.assertEqual(st["PdockerLastProcessGroupId"], 12345)


if __name__ == "__main__":
    unittest.main()

import importlib.machinery
import importlib.util
import os
import socket
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
PDOCKERD = ROOT / "docker-proot-setup" / "bin" / "pdockerd"


def load_pdockerd(home):
    module_name = f"pdockerd_network_status_{uuid.uuid4().hex}"
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


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def state_for(mod, cid, host_port, *, running=True, known_pids=None, rewrite_extra=None):
    rewrite = {
        "ContainerPort": 80,
        "Protocol": "tcp",
        "HostIp": "127.0.0.1",
        "HostPort": host_port,
        "Hook": "bind",
        "Status": "planned",
    }
    rewrite.update(rewrite_extra or {})
    return {
        "Id": cid,
        "Name": f"/{cid}",
        "State": {
            "Running": running,
            "Status": "running" if running else "created",
            "PdockerKnownPids": known_pids or [],
        },
        "PdockerNetwork": {
            "Ports": {"80/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(host_port)}]},
            "PortRewrite": [rewrite],
        },
        "NetworkSettings": {
            "Ports": {"80/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(host_port)}]},
        },
    }


class NetworkPortMappingStatusTest(unittest.TestCase):
    def test_running_mapping_stays_inactive_from_metadata_only(self):
        with tempfile.TemporaryDirectory() as td:
            mod = load_pdockerd(Path(td) / "pdocker")
            state = state_for(mod, "metaonly", free_port(), running=True)

            mod._refresh_port_mapping_status(state, peer_states=[])

            status = state["PdockerNetwork"]["PortMappingStatus"][0]
            self.assertEqual(status["State"], "inactive")
            self.assertFalse(status["Active"])
            self.assertEqual(status["Evidence"], [])
            self.assertEqual(state["PdockerNetwork"]["PortMappingSummary"]["Inactive"], 1)

    def test_stopped_mapping_is_planned_not_active(self):
        with tempfile.TemporaryDirectory() as td:
            mod = load_pdockerd(Path(td) / "pdocker")
            state = state_for(mod, "stopped", free_port(), running=False)

            mod._refresh_port_mapping_status(state, peer_states=[])

            status = state["PdockerNetwork"]["PortMappingStatus"][0]
            self.assertEqual(status["State"], "planned")
            self.assertFalse(status["Active"])
            self.assertEqual(state["PdockerNetwork"]["PortMappingSummary"]["Planned"], 1)

    def test_container_owned_listener_is_active_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            mod = load_pdockerd(Path(td) / "pdocker")
            port = free_port()
            row = {
                "Protocol": "tcp",
                "Family": "4",
                "LocalAddress": "127.0.0.1",
                "LocalPort": port,
                "State": "LISTEN",
                "Inode": "4242",
            }
            state = state_for(
                mod,
                "listener",
                port,
                running=True,
                known_pids=[mod._process_identity(os.getpid())],
            )

            with mock.patch.object(mod, "_proc_net_socket_rows", return_value=[row]), \
                    mock.patch.object(mod, "_socket_inodes_for_pids", return_value={"4242": {os.getpid()}}), \
                    mock.patch.object(mod, "_pids_for_socket_inodes", return_value={"4242": {os.getpid()}}):
                mod._refresh_port_mapping_status(state, peer_states=[])

            status = state["PdockerNetwork"]["PortMappingStatus"][0]
            self.assertEqual(status["State"], "active")
            self.assertTrue(status["Active"])
            self.assertEqual(status["Evidence"][0]["Kind"], "listener")
            self.assertEqual(status["Evidence"][0]["Source"], "proc-net")
            self.assertEqual(status["Evidence"][0]["HostPort"], port)
            self.assertEqual(state["PdockerNetwork"]["PortMappingSummary"]["Active"], 1)

    def test_foreign_listener_is_conflict_not_active(self):
        with tempfile.TemporaryDirectory() as td:
            mod = load_pdockerd(Path(td) / "pdocker")
            port = free_port()
            row = {
                "Protocol": "tcp",
                "Family": "4",
                "LocalAddress": "127.0.0.1",
                "LocalPort": port,
                "State": "LISTEN",
                "Inode": "5252",
            }
            state = state_for(mod, "foreign", port, running=True, known_pids=[])

            with mock.patch.object(mod, "_proc_net_socket_rows", return_value=[row]), \
                    mock.patch.object(mod, "_socket_inodes_for_pids", return_value={}), \
                    mock.patch.object(mod, "_pids_for_socket_inodes", return_value={"5252": {os.getpid()}}):
                mod._refresh_port_mapping_status(state, peer_states=[])

            status = state["PdockerNetwork"]["PortMappingStatus"][0]
            self.assertEqual(status["State"], "conflict")
            self.assertFalse(status["Active"])
            self.assertEqual(status["Conflict"][0]["Type"], "host-listener")
            self.assertEqual(state["PdockerNetwork"]["PortMappingSummary"]["Conflict"], 1)

    def test_foreign_listener_conflict_wins_over_declared_runtime_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            mod = load_pdockerd(Path(td) / "pdocker")
            port = free_port()
            row = {
                "Protocol": "tcp",
                "Family": "4",
                "LocalAddress": "127.0.0.1",
                "LocalPort": port,
                "State": "LISTEN",
                "Inode": "6262",
            }
            ident = mod._process_identity(os.getpid())
            state = state_for(
                mod,
                "foreign-active-claim",
                port,
                running=True,
                known_pids=[],
                rewrite_extra={
                    "RuntimeEvidence": [{
                        "Kind": "proxy",
                        "Status": "active",
                        "Pid": ident["Pid"],
                        "StartTime": ident["StartTime"],
                        "HostIp": "127.0.0.1",
                        "HostPort": port,
                        "Protocol": "tcp",
                    }],
                },
            )

            with mock.patch.object(mod, "_proc_net_socket_rows", return_value=[row]), \
                    mock.patch.object(mod, "_socket_inodes_for_pids", return_value={}), \
                    mock.patch.object(mod, "_pids_for_socket_inodes", return_value={"6262": {os.getpid()}}):
                mod._refresh_port_mapping_status(state, peer_states=[])

            status = state["PdockerNetwork"]["PortMappingStatus"][0]
            self.assertEqual(status["State"], "conflict")
            self.assertFalse(status["Active"])
            self.assertEqual(status["Conflict"][0]["Type"], "host-listener")

    def test_peer_claim_conflict_wins_over_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            mod = load_pdockerd(Path(td) / "pdocker")
            port = free_port()
            state = state_for(mod, "primary", port, running=True)
            peer = state_for(mod, "peer", port, running=True)

            mod._refresh_port_mapping_status(state, peer_states=[peer])

            status = state["PdockerNetwork"]["PortMappingStatus"][0]
            self.assertEqual(status["State"], "conflict")
            self.assertFalse(status["Active"])
            self.assertEqual(status["Conflict"][0]["ContainerId"], "peer")

    def test_host_port_conflict_corpus_covers_wildcards_and_protocol(self):
        with tempfile.TemporaryDirectory() as td:
            mod = load_pdockerd(Path(td) / "pdocker")
            base = {
                "Protocol": "tcp",
                "HostIp": "127.0.0.1",
                "HostPort": 18080,
            }
            cases = [
                ({"Protocol": "tcp", "HostIp": "127.0.0.1", "HostPort": 18080}, True),
                ({"Protocol": "TCP", "HostIp": "127.0.0.1", "HostPort": 18080}, True),
                ({"Protocol": "tcp", "HostIp": "0.0.0.0", "HostPort": 18080}, True),
                ({"Protocol": "tcp", "HostIp": "::", "HostPort": 18080}, True),
                ({"Protocol": "udp", "HostIp": "127.0.0.1", "HostPort": 18080}, False),
                ({"Protocol": "tcp", "HostIp": "127.0.0.1", "HostPort": 18081}, False),
                ({"Protocol": "tcp", "HostIp": "127.0.0.2", "HostPort": 18080}, False),
            ]

            for other, expected in cases:
                self.assertEqual(mod._port_mapping_conflict(base, other), expected, other)

    def test_verified_runtime_rewrite_evidence_can_mark_active(self):
        with tempfile.TemporaryDirectory() as td:
            mod = load_pdockerd(Path(td) / "pdocker")
            port = free_port()
            ident = mod._process_identity(os.getpid())
            state = state_for(
                mod,
                "rewrite",
                port,
                running=True,
                rewrite_extra={
                    "RuntimeEvidence": [{
                        "Kind": "syscall-rewrite",
                        "Status": "active",
                        "Verified": True,
                        "Pid": ident["Pid"],
                        "StartTime": ident["StartTime"],
                        "HostIp": "127.0.0.1",
                        "HostPort": port,
                        "Protocol": "tcp",
                    }],
                },
            )

            mod._refresh_port_mapping_status(state, peer_states=[])

            status = state["PdockerNetwork"]["PortMappingStatus"][0]
            self.assertEqual(status["State"], "active")
            self.assertEqual(status["Evidence"][0]["Kind"], "syscall-rewrite")

    def test_live_proxy_evidence_can_mark_active(self):
        with tempfile.TemporaryDirectory() as td:
            mod = load_pdockerd(Path(td) / "pdocker")
            port = free_port()
            ident = mod._process_identity(os.getpid())
            state = state_for(
                mod,
                "proxy",
                port,
                running=True,
                rewrite_extra={
                    "Proxy": {
                        "Status": "active",
                        "Pid": ident["Pid"],
                        "StartTime": ident["StartTime"],
                        "HostIp": "127.0.0.1",
                        "HostPort": port,
                        "Protocol": "tcp",
                        "Target": "127.0.0.1:8080",
                    },
                },
            )

            mod._refresh_port_mapping_status(state, peer_states=[])

            status = state["PdockerNetwork"]["PortMappingStatus"][0]
            self.assertEqual(status["State"], "active")
            self.assertEqual(status["Evidence"][0]["Kind"], "proxy")
            self.assertEqual(status["Evidence"][0]["Target"], "127.0.0.1:8080")

    def test_legacy_ports_without_rewrite_still_get_truth_status(self):
        with tempfile.TemporaryDirectory() as td:
            mod = load_pdockerd(Path(td) / "pdocker")
            port = free_port()
            state = state_for(mod, "legacy", port, running=True)
            del state["PdockerNetwork"]["PortRewrite"]

            mod._refresh_port_mapping_status(state, peer_states=[])

            status = state["PdockerNetwork"]["PortMappingStatus"][0]
            self.assertEqual(status["HostPort"], port)
            self.assertEqual(status["ContainerPort"], 80)
            self.assertEqual(status["State"], "inactive")

    def test_bare_active_flags_do_not_count_as_proof(self):
        with tempfile.TemporaryDirectory() as td:
            mod = load_pdockerd(Path(td) / "pdocker")
            state = state_for(
                mod,
                "bareflag",
                free_port(),
                running=True,
                rewrite_extra={"RuntimeStatus": "active", "Active": True},
            )

            mod._refresh_port_mapping_status(state, peer_states=[])

            status = state["PdockerNetwork"]["PortMappingStatus"][0]
            self.assertEqual(status["State"], "inactive")
            self.assertFalse(status["Active"])
            self.assertEqual(status["Evidence"], [])


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Detailed regression tests for the pdockerd backend selection contract.

These checks run quickly and focus on the behavior that is most likely to
regress while switching runtime backends (notably the no-PRoot/direct path).
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import http.client
import json
import os
import shutil
import tempfile
import sys
import threading
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
PDOCKERD = ROOT / "bin" / "pdockerd"
ANDROID_MANIFEST = REPO_ROOT / "app" / "src" / "main" / "AndroidManifest.xml"
ANDROID_BRIDGE = REPO_ROOT / "app" / "src" / "main" / "python" / "pdockerd_bridge.py"


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    raise SystemExit(1)


def ok(msg: str) -> None:
    print(f"ok: {msg}")


def load_pdockerd_with_env(
    name: str,
    runtime_backend: str,
    home: Path,
    extra_env: dict[str, str] | None = None,
):
    env = os.environ.copy()
    os.environ["PDOCKER_RUNTIME_BACKEND"] = runtime_backend
    os.environ["PDOCKER_HOME"] = str(home)
    if extra_env:
        os.environ.update(extra_env)
    else:
        os.environ.pop("PDOCKER_DIRECT_EXECUTOR", None)
    if "PDOCKER_RUNTIME_PREFLIGHT" not in env:
        os.environ["PDOCKER_RUNTIME_PREFLIGHT"] = "0"
    # Force a deterministic no-proxy setup path in test env.
    os.environ.setdefault("LD_LIBRARY_PATH", str((ROOT / "lib").resolve()))

    # Ensure the imported module can be reloaded fresh for each backend mode.
    module_name = f"pdockerd_test_{name}"
    for key in [k for k in sys.modules if k.startswith("pdockerd_test_")]:
        if k != "importlib.machinery" and k != "importlib.util":
            del sys.modules[k]

    loader = importlib.machinery.SourceFileLoader(module_name, str(PDOCKERD))
    spec = importlib.util.spec_from_loader(module_name, loader)
    if spec is None or spec.loader is None:
        fail("failed to create import spec for pdockerd")
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_direct_backend_contract() -> None:
    with tempfile.TemporaryDirectory(prefix="pdocker-test-") as home:
        home_path = Path(home)
        mod = load_pdockerd_with_env("direct", "no-proot", home_path)

        if mod.runtime_backend_kind() != "direct":
            fail(f"backend kind must be direct, got {mod.runtime_backend_kind()}")
        ok("runtime backend kind selects direct")

        if mod.runtime_driver_name() != "pdocker-direct":
            fail(f"driver name mismatch: {mod.runtime_driver_name()}")
        ok("runtime driver name is pdocker-direct")

        if mod.runtime_info_id() != "PDOCKER:DIRECT":
            fail(f"info id mismatch: {mod.runtime_info_id()}")
        ok("runtime info id is PDOCKER:DIRECT")

        msg = mod.runtime_backend_unavailable_message()
        if msg:
            fail(f"direct backend should be available, got: {msg}")
        ok("runtime unavailable message is clear")

        process_msg = mod.runtime_process_unavailable_message()
        if "cannot execute container processes yet" not in process_msg:
            fail(f"direct process unavailable message mismatch: {process_msg!r}")
        ok("direct backend reports process execution gap")

        rootfs = home_path / "rootfs"
        (rootfs / "bin").mkdir(parents=True)
        (rootfs / "bin" / "sh").write_text("#!/bin/sh\n")
        pre = mod.runtime_preflight(str(rootfs), env={}, workdir="/", binds=None, cow_bind=None)
        if pre:
            fail(f"runtime_preflight should pass for direct backend: {pre!r}")
        ok("runtime_preflight accepts direct backend")

        try:
            mod.build_run_argv(str(rootfs), ["/bin/sh", "-c", "echo hi"], {}, "/", None, None)
        except RuntimeError as exc:
            if "cannot execute container processes yet" not in str(exc):
                fail(f"direct build_run_argv raised wrong error: {exc}")
        else:
            fail("direct build_run_argv should require a probed executor")
        ok("build_run_argv rejects direct execution without helper")


def test_direct_executor_probe_contract() -> None:
    with tempfile.TemporaryDirectory(prefix="pdocker-test-") as home:
        home_path = Path(home)
        helper = home_path / "pdocker-direct-helper"
        helper.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = \"--pdocker-direct-probe\" ]; then\n"
            "  echo pdocker-direct-executor:1\n"
            "  echo process-exec=0\n"
            "  exit 0\n"
            "fi\n"
            "exit 99\n"
        )
        helper.chmod(0o755)
        mod = load_pdockerd_with_env(
            "direct_helper",
            "no-proot",
            home_path,
            {"PDOCKER_DIRECT_EXECUTOR": str(helper)},
        )
        if not mod.direct_executor_available():
            fail("valid direct helper probe should be detected")
        process_msg = mod.runtime_process_unavailable_message()
        if "does not advertise process-exec=1" not in process_msg:
            fail(f"helper without process-exec=1 should remain blocked: {process_msg!r}")
        try:
            mod.build_run_argv(str(home_path / "rootfs"), ["/bin/echo"], {}, "/", None, None)
        except RuntimeError as exc:
            if "does not advertise process-exec=1" not in str(exc):
                fail(f"direct helper capability rejection mismatch: {exc}")
        else:
            fail("direct helper without process-exec=1 should not build argv")
        ok("direct executor probe alone does not enable process execution")


def test_direct_executor_process_capability_contract() -> None:
    with tempfile.TemporaryDirectory(prefix="pdocker-test-") as home:
        home_path = Path(home)
        helper = home_path / "pdocker-direct-helper"
        helper.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = \"--pdocker-direct-probe\" ]; then\n"
            "  echo pdocker-direct-executor:1\n"
            "  echo process-exec=1\n"
            "  exit 0\n"
            "fi\n"
            "exit 99\n"
        )
        helper.chmod(0o755)
        mod = load_pdockerd_with_env(
            "direct_helper_exec",
            "no-proot",
            home_path,
            {"PDOCKER_DIRECT_EXECUTOR": str(helper)},
        )
        if mod.runtime_process_unavailable_message():
            fail("direct helper with process-exec=1 should make process execution available")
        rootfs = home_path / "rootfs"
        rootfs.mkdir()
        argv = mod.build_run_argv(
            str(rootfs),
            ["/bin/echo", "hi"],
            {"A": "B"},
            "/work",
            binds=["/host:/guest:ro"],
            cow_bind={"upper": "/upper", "lower": "/lower", "guest_path": "/"},
            mode="exec",
        )
        joined = " ".join(argv)
        for token in (str(helper), "--rootfs", str(rootfs), "--workdir", "/work", "--env", "A=B", "--bind", "/host:/guest:ro", "--cow-upper", "/upper", "--", "/bin/echo"):
            if token not in joined:
                fail(f"direct helper argv missing {token!r}: {argv!r}")
        ok("direct executor process capability enables structured helper argv")


def test_direct_backend_rejects_fake_container_start() -> None:
    with tempfile.TemporaryDirectory(prefix="pdocker-test-") as home:
        home_path = Path(home)
        mod = load_pdockerd_with_env("direct_start", "no-proot", home_path)
        image = "ubuntu:22.04"
        img_dir = Path(mod.image_dir(mod.normalize_image(image)))
        rootfs = img_dir / "rootfs"
        (rootfs / "bin").mkdir(parents=True)
        (rootfs / "usr/local/bin").mkdir(parents=True)
        (rootfs / "workspace").mkdir(parents=True)
        (rootfs / "bin" / "sh").write_text("#!/bin/sh\n")
        (rootfs / "usr/local/bin" / "start-code-server").write_text("#!/bin/sh\n")
        (img_dir / "config.json").write_text(
            '{"config":{"Cmd":["/usr/local/bin/start-code-server"],"Env":[]}}'
        )
        state = mod.create_container(
            {
                "Image": image,
                "Cmd": ["/usr/local/bin/start-code-server"],
                "WorkingDir": "/workspace",
                "Env": ["CODE_SERVER_PORT=18080"],
            },
            name="direct-dev",
        )
        try:
            mod.start_container(state["Id"])
        except RuntimeError as exc:
            message = str(exc)
        else:
            fail("direct backend started a fake container instead of rejecting execution")
        if "cannot execute container processes yet" not in message:
            fail(f"direct start rejection did not explain executor gap: {message!r}")
        saved = mod.load_container_state(state["Id"])
        if saved["State"]["Running"]:
            fail("direct backend marked rejected container as running")
        if saved["State"]["ExitCode"] != 126:
            fail(f"direct backend exit code mismatch: {saved['State']['ExitCode']!r}")
        log_path = Path(mod.LOGS_DIR) / f"{state['Id']}.log"
        text = log_path.read_text(errors="replace") if log_path.exists() else ""
        if "fake listener" not in text:
            fail("direct backend log did not state that no fake listener was started")
        ok("direct backend rejects fake service start honestly")


def test_start_container_reconciles_live_pid() -> None:
    with tempfile.TemporaryDirectory(prefix="pdocker-test-") as home:
        home_path = Path(home)
        mod = load_pdockerd_with_env("start_live_pid", "no-proot", home_path)
        image = "ubuntu:22.04"
        img_dir = Path(mod.image_dir(mod.normalize_image(image)))
        rootfs = img_dir / "rootfs"
        (rootfs / "bin").mkdir(parents=True)
        (rootfs / "bin" / "sh").write_text("#!/bin/sh\n")
        (img_dir / "config.json").write_text('{"config":{"Cmd":["/bin/sh"],"Env":[]}}')
        state = mod.create_container({"Image": image, "Cmd": ["/bin/sh"]}, name="live-pid")
        state["State"]["Running"] = False
        state["State"]["Status"] = "exited"
        state["State"]["Pid"] = os.getpid()
        state["State"]["PidStartTime"] = mod._pid_start_time(os.getpid())
        state["State"]["ExitCode"] = 1
        mod.save_container_state(state["Id"], state)

        returned = mod.start_container(state["Id"])
        saved = mod.load_container_state(state["Id"])
        if returned["State"]["Pid"] != os.getpid() or saved["State"]["Pid"] != os.getpid():
            fail("start_container did not preserve the live runtime pid")
        if not saved["State"]["Running"] or saved["State"]["Status"] != "running":
            fail(f"start_container did not reconcile live pid to running: {saved['State']!r}")
        ok("container start reconciles verified live pid without double-starting")


def test_start_container_rejects_reused_pid() -> None:
    with tempfile.TemporaryDirectory(prefix="pdocker-test-") as home:
        home_path = Path(home)
        mod = load_pdockerd_with_env("start_reused_pid", "no-proot", home_path)
        image = "ubuntu:22.04"
        img_dir = Path(mod.image_dir(mod.normalize_image(image)))
        rootfs = img_dir / "rootfs"
        (rootfs / "bin").mkdir(parents=True)
        (rootfs / "bin" / "sh").write_text("#!/bin/sh\n")
        (img_dir / "config.json").write_text('{"config":{"Cmd":["/bin/sh"],"Env":[]}}')
        state = mod.create_container({"Image": image, "Cmd": ["/bin/sh"]}, name="reused-pid")
        state["State"]["Running"] = True
        state["State"]["Status"] = "running"
        state["State"]["Pid"] = os.getpid()
        state["State"]["PidStartTime"] = "definitely-not-this-process"
        mod.save_container_state(state["Id"], state)

        try:
            mod.start_container(state["Id"])
        except RuntimeError:
            pass
        else:
            fail("start_container treated a reused pid as the live container process")
        saved = mod.load_container_state(state["Id"])
        if saved["State"]["Running"]:
            fail(f"reused pid remained running: {saved['State']!r}")
        if saved["State"].get("Pid") != 0:
            fail(f"reused pid was not cleared: {saved['State']!r}")
        ok("container start rejects reused pid identity")


def test_default_no_proot_runtime_path() -> None:
    with tempfile.TemporaryDirectory(prefix="pdocker-test-") as home:
        home_path = Path(home)
        mod = load_pdockerd_with_env("default", "no-proot", home_path)

        if not mod.RUNTIME_BACKEND or mod.RUNTIME_BACKEND.kind != "direct":
            fail("requested no-proot backend was not selected")
        ok("no-proot request creates direct backend instance")


def test_duplicate_name_resolution_contract() -> None:
    with tempfile.TemporaryDirectory(prefix="pdocker-test-") as home:
        home_path = Path(home)
        mod = load_pdockerd_with_env("duplicate_names", "no-proot", home_path)
        old_id = "1" * 64
        new_id = "2" * 64
        name = "stale-name"
        for cid, created, running, status, log_text in (
            (old_id, "2026-01-01T00:00:00.000000000Z", False, "exited", "old duplicate log\n"),
            (new_id, "2026-01-02T00:00:00.000000000Z", True, "running", "new duplicate log\n"),
        ):
            (Path(mod.CONTAINERS_DIR) / cid).mkdir(parents=True)
            mod.save_container_state(
                cid,
                {
                    "Id": cid,
                    "Name": f"/{name}",
                    "Created": created,
                    "State": {"Running": running, "Status": status, "Pid": 0},
                },
            )
            (Path(mod.LOGS_DIR) / f"{cid}.log").write_text(log_text)

        matches = mod.find_containers(name)
        if [state["Id"] for state in matches] != [new_id, old_id]:
            fail(f"duplicate name lookup did not return newest-first matches: {matches!r}")

        srv = mod.ThreadingTCPHTTPServer(("127.0.0.1", 0), mod.DockerAPIHandler)
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()

        def request(method: str, target: str) -> tuple[int, bytes]:
            conn = http.client.HTTPConnection(srv.server_address[0], srv.server_address[1], timeout=5)
            try:
                conn.request(method, target)
                resp = conn.getresponse()
                return resp.status, resp.read()
            finally:
                conn.close()

        def raw_stream_payload(body: bytes) -> bytes:
            if not body:
                return b""
            if len(body) < 8:
                fail(f"raw log stream too short: {body!r}")
            size = int.from_bytes(body[4:8], "big")
            return body[8:8 + size]

        try:
            status, body = request("GET", f"/containers/{name}/logs")
            if status != 200:
                fail(f"name logs request failed with HTTP {status}: {body!r}")
            if raw_stream_payload(body) != b"new duplicate log\n":
                fail(f"name logs did not resolve newest duplicate: {body!r}")

            status, body = request("GET", f"/containers/{old_id}/logs")
            if status != 200:
                fail(f"ID logs request failed with HTTP {status}: {body!r}")
            if raw_stream_payload(body) != b"old duplicate log\n":
                fail(f"ID logs did not resolve the exact container object: {body!r}")

            try:
                mod.create_container({"Image": "unused:latest"}, name=name)
            except ValueError as exc:
                if new_id not in str(exc):
                    fail(f"duplicate create conflict did not report newest match: {exc}")
            else:
                fail("duplicate container name should reject create_container")

            status, body = request("DELETE", f"/containers/{name}?force=1")
            if status != 204:
                fail(f"name delete did not clean duplicate containers, HTTP {status}: {body!r}")
        finally:
            srv.shutdown()
            srv.server_close()
            thread.join(timeout=5)

        if mod.find_containers(name):
            fail("name delete left stale duplicate matches behind")
        seed_legacy_image(mod, "unused:latest")
        try:
            mod.create_container({"Image": "unused:latest"}, name=name)
        except ValueError as exc:
            fail(f"container name was still blocked after name delete cleaned duplicates: {exc}")
        else:
            ok("duplicate container names resolve newest-first, keep log truth, and clean by name")


def seed_legacy_image(mod, image: str) -> None:
    img_dir = Path(mod.image_dir(mod.normalize_image(image)))
    rootfs = img_dir / "rootfs"
    (rootfs / "bin").mkdir(parents=True)
    (rootfs / "bin" / "sh").write_text("#!/bin/sh\n")
    (img_dir / "config.json").write_text('{"config":{"Env":[]}}')


def test_network_metadata_contract() -> None:
    with tempfile.TemporaryDirectory(prefix="pdocker-test-") as home:
        home_path = Path(home)
        mod = load_pdockerd_with_env("network_metadata", "no-proot", home_path)
        seed_legacy_image(mod, "ubuntu:22.04")
        net_name = "demo_default"
        net_dir = Path(mod.NETWORKS_DIR) / net_name
        net_dir.mkdir(parents=True)
        (net_dir / "members.json").write_text("[]")
        stable_net_id = mod._stable_network_id(net_name)
        (net_dir / "meta.json").write_text(json.dumps({
            "Id": stable_net_id,
            "Created": "2026-05-04T00:00:00Z",
            "Driver": "bridge",
            "Labels": {"com.docker.compose.project": "demo"},
            "Options": {},
            "IPAM": {"Driver": "default", "Config": []},
            "Warning": mod.HOST_NETWORK_LIMITATION_WARNING,
        }))

        def make_service(name: str, aliases: list[str], host_port: str):
            return mod.create_container(
                {
                    "Image": "ubuntu:22.04",
                    "Cmd": ["/bin/sh"],
                    "ExposedPorts": {"8080/tcp": {}},
                    "Labels": {
                        "com.docker.compose.project": "demo",
                        "com.docker.compose.service": name,
                        "io.github.ryo100794.pdocker.compose-service": name,
                    },
                    "HostConfig": {
                        "NetworkMode": net_name,
                        "PortBindings": {
                            "8080/tcp": [{"HostIp": "0.0.0.0", "HostPort": host_port}],
                        },
                    },
                    "NetworkingConfig": {
                        "EndpointsConfig": {
                            net_name: {"Aliases": aliases},
                        },
                    },
                },
                name=f"demo-{name}-1",
            )

        web = make_service("web", ["web", "demo-web"], "18080")
        db = make_service("db", ["db", "demo-db"], "15432")
        web_net = web["NetworkSettings"]["Networks"][net_name]
        if web_net.get("NetworkID") != stable_net_id or len(web_net.get("EndpointID", "")) != 64:
            fail(f"stable network/endpoint IDs missing: {web_net!r}")
        aliases = web_net.get("Aliases") or []
        for alias in ("demo-web-1", "web", "demo-web"):
            if alias not in aliases:
                fail(f"compose/service alias {alias!r} missing: {aliases!r}")
        if web["NetworkSettings"]["Ports"]["8080/tcp"][0]["HostPort"] != "18080":
            fail(f"Docker port binding did not persist: {web['NetworkSettings']['Ports']!r}")
        if web["PdockerNetwork"]["IPAddress"] != web["NetworkSettings"]["IPAddress"]:
            fail(f"PdockerNetwork and NetworkSettings IPs diverged: {web!r}")
        warnings = web.get("Warnings") or []
        if not any("host-network-only" in item and "no TUN" in item for item in warnings):
            fail(f"host-network limitation warning missing: {warnings!r}")

        containers = mod._network_containers(net_name)
        web_member = containers.get(web["Id"]) or {}
        if web_member.get("EndpointID") != web_net.get("EndpointID"):
            fail(f"network inspect endpoint mismatch: {containers!r}")
        if web_member.get("IPv4Address") != f"{web['NetworkSettings']['IPAddress']}/32":
            fail(f"network inspect synthetic IP mismatch: {web_member!r}")
        if "web" not in (web_member.get("Aliases") or []):
            fail(f"network inspect aliases missing: {web_member!r}")

        hosts_root = home_path / "hosts-root"
        mod._inject_network_hosts(str(hosts_root), net_name, web["Id"])
        hosts = (hosts_root / "etc" / "hosts").read_text()
        if "db" not in hosts or "demo-db" not in hosts:
            fail(f"/etc/hosts alias injection omitted peer service aliases: {hosts!r}")

        loaded_web = mod.load_container_state(web["Id"])
        mod._disconnect_network_metadata(loaded_web, net_name)
        disconnected = mod.load_container_state(web["Id"])
        if net_name in disconnected.get("NetworkSettings", {}).get("Networks", {}):
            fail(f"disconnect left endpoint in container NetworkSettings: {disconnected!r}")
        members = mod._network_members(net_name)
        if any(member.get("id") == web["Id"] for member in members):
            fail(f"disconnect left network membership behind: {members!r}")
        if not any(member.get("id") == db["Id"] for member in members):
            fail(f"disconnect removed the wrong network member: {members!r}")
        ok("network metadata records stable IDs, aliases, ports, IPs, and host-only warnings")


def test_port_mapping_status_contract() -> None:
    with tempfile.TemporaryDirectory(prefix="pdocker-test-") as home:
        home_path = Path(home)
        mod = load_pdockerd_with_env("port_mapping_status", "no-proot", home_path)
        seed_legacy_image(mod, "ubuntu:22.04")

        def create_with_port(name: str, host_port: str):
            return mod.create_container(
                {
                    "Image": "ubuntu:22.04",
                    "Cmd": ["/bin/sh"],
                    "ExposedPorts": {"8080/tcp": {}},
                    "HostConfig": {
                        "PortBindings": {
                            "8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": host_port}],
                        },
                    },
                },
                name=name,
            )

        planned = create_with_port("planned-port", "18080")
        planned_status = planned["PdockerNetwork"]["PortMappingStatus"][0]
        if planned_status.get("State") != "planned" or planned_status.get("Active"):
            fail(f"created container port mapping should be planned: {planned_status!r}")
        if planned["PdockerNetwork"]["PortMappingSummary"].get("Planned") != 1:
            fail(f"planned summary mismatch: {planned['PdockerNetwork']!r}")

        inactive = create_with_port("inactive-port", "18081")
        inactive["State"]["Running"] = True
        inactive["State"]["Status"] = "running"
        mod._refresh_port_mapping_status(inactive)
        inactive_status = inactive["PdockerNetwork"]["PortMappingStatus"][0]
        if inactive_status.get("State") != "inactive" or inactive_status.get("Active"):
            fail(f"running container without runtime listener should be inactive: {inactive_status!r}")
        if "no pdocker-owned listener" not in inactive_status.get("Message", ""):
            fail(f"inactive mapping must explain missing active listener: {inactive_status!r}")

        active = create_with_port("active-port", "18082")
        active["State"]["Running"] = True
        active["State"]["Status"] = "running"
        active["PdockerNetwork"]["PortRewrite"][0]["RuntimeStatus"] = "active"
        active["PdockerNetwork"]["PortRewrite"][0]["ProxyTarget"] = "127.0.0.1:8080"
        mod._refresh_port_mapping_status(active)
        active_status = active["PdockerNetwork"]["PortMappingStatus"][0]
        if active_status.get("State") != "active" or not active_status.get("Active"):
            fail(f"runtime-marked mapping should be active: {active_status!r}")
        if active_status.get("ProxyTarget") != "127.0.0.1:8080":
            fail(f"active mapping should expose proxy target: {active_status!r}")

        first = create_with_port("conflict-one", "18083")
        second = create_with_port("conflict-two", "18083")
        mod._refresh_port_mapping_status(first)
        mod._refresh_port_mapping_status(second)
        first_status = first["PdockerNetwork"]["PortMappingStatus"][0]
        second_status = second["PdockerNetwork"]["PortMappingStatus"][0]
        if first_status.get("State") != "conflict" or second_status.get("State") != "conflict":
            fail(f"duplicate host ports should be conflict: {first_status!r}, {second_status!r}")
        if second_status.get("Runtime") != "host-network-only":
            fail(f"port status must keep host-network-only limitation visible: {second_status!r}")
        ok("port mapping status distinguishes planned, inactive, active, and conflict")


def seed_layered_image(mod, image: str, diff_ids: list[str], config: dict | None = None) -> None:
    img_dir = Path(mod.image_dir(mod.normalize_image(image)))
    rootfs = img_dir / "rootfs"
    rootfs.mkdir(parents=True, exist_ok=True)
    for did in diff_ids:
        tree = Path(mod.LAYERS_DIR) / did / "tree"
        tree.mkdir(parents=True, exist_ok=True)
    mod._save_image_manifest(str(img_dir), diff_ids, config or {"config": {"Env": []}})
    (img_dir / "config.json").write_text(json.dumps(config or {"config": {"Env": []}}))
    (img_dir / "image_ref").write_text(mod.normalize_image(image))


def test_storage_summary_distinguishes_layer_and_upper_bytes() -> None:
    with tempfile.TemporaryDirectory(prefix="pdocker-test-") as home:
        home_path = Path(home)
        mod = load_pdockerd_with_env("storage_summary", "no-proot", home_path)
        layers = {
            "aaa": 10,
            "bbb": 20,
            "ccc": 30,
        }
        for did, size in layers.items():
            layer_dir = Path(mod.LAYERS_DIR) / did
            tree = layer_dir / "tree"
            tree.mkdir(parents=True, exist_ok=True)
            (tree / f"{did}.txt").write_bytes(b"x" * size)
            (layer_dir / "meta.json").write_text(json.dumps({"size": size}))
        seed_layered_image(mod, "local/one:latest", ["aaa", "bbb"])
        seed_layered_image(mod, "local/two:latest", ["aaa", "ccc"])

        cid = "c" * 64
        cdir = Path(mod.CONTAINERS_DIR) / cid
        upper = cdir / "upper"
        upper.mkdir(parents=True)
        (upper / "changed.txt").write_bytes(b"1234567")
        state = {
            "Id": cid,
            "Name": "/storage-test",
            "Image": mod.normalize_image("local/one:latest"),
            "ImageId": mod.image_id(mod.normalize_image("local/one:latest")),
            "Cmd": ["/bin/sh"],
            "Created": "2026-05-04T00:00:00Z",
            "Labels": {},
            "Storage": {
                "Mode": "cow_bind",
                "LowerDir": str(Path(mod.image_dir(mod.normalize_image("local/one:latest"))) / "rootfs"),
                "UpperDir": str(upper),
            },
            "State": {"Running": False, "Status": "created", "ExitCode": 0, "Pid": 0},
            "NetworkSettings": {},
        }
        mod.save_container_state(cid, state)

        summary = mod.collect_storage_summary()
        if summary.get("SharedLayerBytes") != 60:
            fail(f"layer pool bytes should be counted once: {summary!r}")
        if summary.get("ContainerUpperBytes") != 7:
            fail(f"container upper bytes mismatch: {summary!r}")
        if summary.get("UniqueBytes") != 67:
            fail(f"unique total must be layer pool plus upper bytes: {summary!r}")
        if "must not be added" not in summary.get("PdockerStorage", {}).get("Overlap", ""):
            fail(f"storage summary must explain overlapping views: {summary!r}")

        images = {img["RepoTags"][0]: img for img in mod.list_images()}
        one = images.get("local/one:latest")
        if not one or one.get("VirtualSize") != 30 or one.get("SharedSize") != 10 or one.get("UniqueSize") != 20:
            fail(f"image storage sizes should split virtual/shared/unique bytes: {images!r}")
        containers = mod.list_containers(all_=True, size=True)
        if len(containers) != 1 or containers[0].get("SizeRw") != 7:
            fail(f"container summary must expose upper/private bytes: {containers!r}")
        fast_containers = mod.list_containers(all_=True)
        if len(fast_containers) != 1 or "SizeRw" in fast_containers[0]:
            fail(f"container list must skip expensive storage sizes by default: {fast_containers!r}")

        srv = mod.ThreadingTCPHTTPServer(("127.0.0.1", 0), mod.DockerAPIHandler)
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        try:
            conn = http.client.HTTPConnection(srv.server_address[0], srv.server_address[1], timeout=5)
            try:
                conn.request("GET", "/system/df")
                resp = conn.getresponse()
                body = resp.read()
            finally:
                conn.close()
        finally:
            srv.shutdown()
            srv.server_close()
        if resp.status != 200:
            fail(f"/system/df status mismatch: {resp.status}, body={body!r}")
        endpoint = json.loads(body.decode("utf-8"))
        if endpoint.get("UniqueBytes") != 67 or endpoint.get("ContainerUpperBytes") != 7:
            fail(f"/system/df storage truth mismatch: {endpoint!r}")
        ok("storage summary separates shared layers, image views, and container upper bytes")


def test_dockerfile_unknown_instruction_rejected() -> None:
    with tempfile.TemporaryDirectory(prefix="pdocker-test-") as home:
        home_path = Path(home)
        mod = load_pdockerd_with_env("dockerfile_unknown", "no-proot", home_path)
        seed_legacy_image(mod, "ubuntu:22.04")
        ctx = home_path / "ctx"
        ctx.mkdir()
        dockerfile = ctx / "Dockerfile"
        dockerfile.write_text("FROM ubuntu:22.04\nPDOCKER_MAGIC true\n")
        output: list[str] = []
        result = mod.execute_dockerfile_build(
            str(dockerfile), str(ctx), "local/unknown:latest", {}, output.append
        )
        if result is not None:
            fail("unknown Dockerfile instruction should fail the build")
        if not any("unknown Dockerfile instruction" in line for line in output):
            fail(f"unknown instruction diagnostic missing: {output!r}")
        ok("Dockerfile parser rejects non-standard instructions")


def test_direct_run_requires_real_executor() -> None:
    with tempfile.TemporaryDirectory(prefix="pdocker-test-") as home:
        home_path = Path(home)
        mod = load_pdockerd_with_env("dockerfile_run", "no-proot", home_path)
        seed_legacy_image(mod, "ubuntu:22.04")
        ctx = home_path / "ctx"
        ctx.mkdir()
        dockerfile = ctx / "Dockerfile"
        dockerfile.write_text("FROM ubuntu:22.04\nRUN echo should-not-be-faked\n")
        output: list[str] = []
        result = mod.execute_dockerfile_build(
            str(dockerfile), str(ctx), "local/run:latest", {}, output.append
        )
        if result is not None:
            fail("direct backend should not build fake RUN layers")
        joined = "\n".join(output)
        if "RUN requires a real container process executor" not in joined:
            fail(f"direct RUN diagnostic missing: {joined!r}")
        ok("direct Dockerfile RUN fails instead of recording fake layers")


def test_existing_tag_inline_run_cache() -> None:
    with tempfile.TemporaryDirectory(prefix="pdocker-test-") as home:
        home_path = Path(home)
        mod = load_pdockerd_with_env("inline_cache", "no-proot", home_path)
        base = "a" * 64
        run = "b" * 64
        base_tree = Path(mod.LAYERS_DIR) / base / "tree"
        (base_tree / "bin").mkdir(parents=True)
        (base_tree / "bin" / "sh").write_text("#!/bin/sh\n")
        run_tree = Path(mod.LAYERS_DIR) / run / "tree"
        (run_tree / "cached").mkdir(parents=True)
        (run_tree / "cached" / "marker").write_text("reused\n")
        seed_layered_image(mod, "ubuntu:22.04", [base])
        cfg = {
            "config": {"Env": []},
            "rootfs": {"type": "layers", "diff_ids": [f"sha256:{base}", f"sha256:{run}"]},
            "history": [{"created_by": "RUN echo cached"}],
        }
        seed_layered_image(mod, "local/cached:latest", [base, run], cfg)
        ctx = home_path / "ctx"
        ctx.mkdir()
        dockerfile = ctx / "Dockerfile"
        dockerfile.write_text("FROM ubuntu:22.04\nRUN echo cached\n")
        output: list[str] = []
        result = mod.execute_dockerfile_build(
            str(dockerfile), str(ctx), "local/cached:latest", {}, output.append
        )
        if result != mod.normalize_image("local/cached:latest"):
            fail(f"inline cache rebuild failed: {result!r}, output={output!r}")
        joined = "\n".join(output)
        if "Using inline cache" not in joined:
            fail(f"existing tag inline cache was not used: {joined!r}")
        final_diff_ids = mod._load_image_manifest(Path(mod.image_dir(result)))
        if final_diff_ids != [base, run]:
            fail(f"inline cache rebuilt unexpected layer stack: {final_diff_ids!r}")
        ok("existing tagged image can seed Dockerfile RUN cache")


def test_existing_tag_full_image_cache() -> None:
    with tempfile.TemporaryDirectory(prefix="pdocker-test-") as home:
        home_path = Path(home)
        mod = load_pdockerd_with_env("image_cache", "no-proot", home_path)
        base = "a" * 64
        run = "b" * 64
        copy = "c" * 64
        seed_layered_image(mod, "ubuntu:22.04", [base])
        dest = Path(mod.image_dir(mod.normalize_image("local/full-cache:latest")))
        rootfs = dest / "rootfs"
        (rootfs / "bin").mkdir(parents=True)
        (rootfs / "bin" / "sh").write_text("#!/bin/sh\n")
        (rootfs / "app").mkdir(parents=True)
        (rootfs / "app" / "hello.txt").write_text("hello\n")
        for did in (run, copy):
            (Path(mod.LAYERS_DIR) / did / "tree").mkdir(parents=True, exist_ok=True)
        cfg = {
            "config": {"Env": []},
            "rootfs": {"type": "layers", "diff_ids": [f"sha256:{base}", f"sha256:{run}", f"sha256:{copy}"]},
            "history": [
                {"created_by": "FROM ubuntu:22.04", "empty_layer": True},
                {"created_by": "RUN echo prepared"},
                {"created_by": "COPY hello.txt /app/hello.txt"},
            ],
        }
        mod._save_image_manifest(str(dest), [base, run, copy], cfg)
        (dest / "config.json").write_text(json.dumps(cfg))
        ctx = home_path / "ctx"
        ctx.mkdir()
        (ctx / "hello.txt").write_text("hello\n")
        dockerfile = ctx / "Dockerfile"
        dockerfile.write_text("FROM ubuntu:22.04\nRUN echo prepared\nCOPY hello.txt /app/hello.txt\n")
        output: list[str] = []
        result = mod.execute_dockerfile_build(
            str(dockerfile), str(ctx), "local/full-cache:latest", {}, output.append
        )
        if result != mod.normalize_image("local/full-cache:latest"):
            fail(f"full image cache rebuild failed: {result!r}, output={output!r}")
        if not any("Using image cache" in line for line in output):
            fail(f"full image cache was not used: {output!r}")
        ok("unchanged existing image skips full Dockerfile rebuild")


def test_build_cache_contract() -> None:
    with tempfile.TemporaryDirectory(prefix="pdocker-test-") as home:
        home_path = Path(home)
        mod = load_pdockerd_with_env("build_cache", "no-proot", home_path)
        parent = ["a" * 64]
        state = {
            "env": {"A": "B"},
            "workdir": "/work",
            "shell": ["/bin/sh", "-c"],
            "user": "",
            "platform": "linux/arm64",
        }
        key1 = mod.build_cache_key("RUN", "echo hi", parent, state)
        key2 = mod.build_cache_key("RUN", "echo hi", parent, dict(reversed(state.items())))
        if key1 != key2:
            fail("build cache key should be stable for equivalent state")
        key3 = mod.build_cache_key("RUN", "echo hi", ["b" * 64], state)
        if key1 == key3:
            fail("build cache key must include parent layer stack")
        did = "c" * 64
        layer_tree = Path(mod.LAYERS_DIR) / did / "tree"
        layer_tree.mkdir(parents=True)
        (layer_tree / "marker").write_text("cached\n")
        mod.store_build_cache_entry(key1, {"diff_id": did, "size": 7})
        entry = mod.load_build_cache_entry(key1)
        if not entry or entry.get("diff_id") != did:
            fail("stored build cache entry was not reusable")
        shutil.rmtree(layer_tree.parent)
        if mod.load_build_cache_entry(key1) is not None:
            fail("build cache must ignore entries whose layer was pruned")
        ok("Dockerfile RUN cache keys and layer validation work")


def test_active_operations_contract() -> None:
    with tempfile.TemporaryDirectory(prefix="pdocker-test-") as home:
        mod = load_pdockerd_with_env("active_ops", "no-proot", Path(home))
        op_id = mod.start_operation("build", "build local/test:latest", "receiving context")
        mod.update_operation(op_id, "Step: FROM ubuntu:22.04")
        ops = mod.list_active_operations()
        if len(ops) != 1 or ops[0].get("Detail") != "Step: FROM ubuntu:22.04":
            fail(f"active operation not visible: {ops!r}")
        mod.finish_operation(op_id, "done", "Successfully tagged local/test:latest")
        if mod.list_active_operations():
            fail("finished operation should not remain active")
        ok("daemon active operations are listed independently of UI jobs")


def test_exclusive_build_operations_contract() -> None:
    with tempfile.TemporaryDirectory(prefix="pdocker-test-") as home:
        mod = load_pdockerd_with_env("exclusive_build_ops", "no-proot", Path(home))
        op_id, existing = mod.start_exclusive_operation(
            "build",
            "build local/test:latest",
            "receiving context",
            exclusive_key="build:local/test:latest",
        )
        if not op_id or existing:
            fail("first exclusive build operation should start")
        duplicate_id, duplicate = mod.start_exclusive_operation(
            "build",
            "build local/test:latest",
            "receiving context",
            exclusive_key="build:local/test:latest",
        )
        if duplicate_id or not duplicate or duplicate.get("Id") != op_id:
            fail(f"duplicate exclusive build should report existing operation: {duplicate_id!r} {duplicate!r}")
        listed = mod.list_active_operations()
        if listed and "_ExclusiveKey" in listed[0]:
            fail("operation internals must not leak into the Engine/UI operation list")
        mod.finish_operation(op_id, "failed", "cancelled")
        next_id, next_existing = mod.start_exclusive_operation(
            "build",
            "build local/test:latest",
            "receiving context",
            exclusive_key="build:local/test:latest",
        )
        if not next_id or next_existing:
            fail("exclusive build key should be reusable after the previous operation finishes")
        ok("daemon rejects duplicate running builds for the same tagged image")


def test_active_operations_prune_stale_idle_entries() -> None:
    with tempfile.TemporaryDirectory(prefix="pdocker-test-") as home:
        mod = load_pdockerd_with_env("active_ops_stale", "no-proot", Path(home))
        mod.OPERATION_IDLE_STALE_SECONDS = 10
        op_id = mod.start_operation("diagnostic", "llama.cpp GPU compare", "forced Vulkan")
        with mod.active_operations_lock:
            mod.active_operations[op_id]["UpdatedAt"] = time.time() - 11
        if mod.list_active_operations():
            fail("stale daemon operation should be pruned before UI listing")
        ok("stale daemon operations are pruned from UI-visible state")


def test_host_environment_contract() -> None:
    with tempfile.TemporaryDirectory(prefix="pdocker-test-") as home:
        home_path = Path(home)
        direct = home_path / "pdocker-direct"
        direct.write_text("#!/bin/sh\nexit 0\n")
        direct.chmod(0o755)
        gpu = home_path / "pdocker-gpu-executor"
        gpu.write_text("#!/bin/sh\nexit 0\n")
        gpu.chmod(0o755)
        mod = load_pdockerd_with_env(
            "host_environment",
            "no-proot",
            home_path,
            {
                "PDOCKER_DIRECT_EXECUTOR": str(direct),
                "PDOCKER_GPU_EXECUTOR": str(gpu),
                "PDOCKER_GPU_EXECUTOR_AVAILABLE": "1",
                "PDOCKER_GPU_COMMAND_API": "pdocker-gpu-command-v1",
                "PDOCKER_VULKAN_ICD_KIND": "pdocker-bridge-minimal",
                "PDOCKER_VULKAN_ICD_READY": "0",
                "PDOCKER_MEDIA_CONTAINER_DIR": "/run/pdocker-media",
                "PDOCKER_MEDIA_QUEUE_SOCKET": "/run/pdocker-media/pdocker-media.sock",
                "PDOCKER_MEDIA_SHARED_DIR": "/run/pdocker-media",
                "PDOCKER_MEDIA_COMMAND_API": "pdocker-media-command-v1",
                "PDOCKER_MEDIA_ABI_VERSION": "0.1",
                "PDOCKER_MEDIA_STATUS": "scaffold-disabled",
                "PDOCKER_MEDIA_EXECUTOR_AVAILABLE": "0",
                "PDOCKER_MEDIA_CAPTURE_READY": "0",
                "PDOCKER_MEDIA_CAMERA_READY": "0",
                "PDOCKER_MEDIA_AUDIO_READY": "0",
            },
        )
        env = mod.collect_host_environment("1.43")
        if env.get("Runtime", {}).get("DockerApiVersion") != "1.43":
            fail(f"host environment API version missing: {env!r}")
        if env.get("Gpu", {}).get("CommandApi") != "pdocker-gpu-command-v1":
            fail(f"host environment GPU command api missing: {env!r}")
        if env.get("Frameworks", {}).get("Vulkan", {}).get("ApiVersion") != "1.2.0":
            fail(f"host environment Vulkan API version missing: {env!r}")
        if "OpenCL" not in env.get("Frameworks", {}):
            fail(f"host environment OpenCL diagnostic missing: {env!r}")
        if "NnApi" not in env.get("Frameworks", {}):
            fail(f"host environment NNAPI diagnostic missing: {env!r}")
        if "OpenCVPython" in env.get("Frameworks", {}):
            fail(f"host environment should stay focused on GPU/NPU, not OpenCV: {env!r}")
        media = env.get("Media", {})
        if media.get("QueueSocket") != "/run/pdocker-media/pdocker-media.sock":
            fail(f"host environment media socket contract missing: {env!r}")
        if media.get("Status") != "scaffold-disabled" or media.get("CaptureReady"):
            fail(f"host environment must report media scaffold disabled: {env!r}")
        if not env.get("Paths", {}).get("DirectExecutor", {}).get("Exists"):
            fail(f"host environment direct executor path missing: {env!r}")
        if "PATH" in env.get("Environment", {}):
            fail(f"host environment must not dump broad process environment: {env!r}")
        ok("host environment contract exposes bounded runtime diagnostics")


def test_media_bridge_scaffold_contract() -> None:
    with tempfile.TemporaryDirectory(prefix="pdocker-test-") as home:
        home_path = Path(home)
        executor = home_path / "pdocker-media-executor"
        executor.write_text("#!/bin/sh\nexit 0\n")
        executor.chmod(0o755)
        (home_path / "pdocker-media.sock").touch()
        mod = load_pdockerd_with_env(
            "media_scaffold",
            "no-proot",
            home_path,
            {
                "PDOCKER_MEDIA_HOST_DIR": str(home_path),
                "PDOCKER_MEDIA_CONTAINER_DIR": "/run/pdocker-media",
                "PDOCKER_MEDIA_QUEUE_SOCKET": "/run/pdocker-media/pdocker-media.sock",
                "PDOCKER_MEDIA_SHARED_DIR": "/run/pdocker-media",
                "PDOCKER_MEDIA_COMMAND_API": "pdocker-media-command-v1",
                "PDOCKER_MEDIA_ABI_VERSION": "0.1",
                "PDOCKER_MEDIA_STATUS": "executor-present-capture-disabled",
                "PDOCKER_MEDIA_EXECUTOR": str(executor),
                "PDOCKER_MEDIA_EXECUTOR_AVAILABLE": "1",
                "PDOCKER_MEDIA_CAPTURE_READY": "0",
                "PDOCKER_MEDIA_CAMERA_READY": "0",
                "PDOCKER_MEDIA_AUDIO_READY": "0",
                "PDOCKER_MEDIA_DEVICE_PASSTHROUGH": "0",
            },
        )
        state = {
            "HostConfig": {
                "DeviceRequests": [
                    {
                        "Driver": "pdocker-media",
                        "Count": -1,
                        "Capabilities": [["camera", "audio"]],
                    }
                ]
            },
            "Labels": {"io.pdocker.media": "camera"},
        }
        env = mod._media_env(state)
        binds = mod._media_binds(state)
        media = mod.collect_media_environment()
        expected_modes = [
            "audio.capture",
            "audio.playback",
            "audio.usb.multichannel",
            "camera.front",
            "camera.rear",
            "video.camera2",
        ]
        if sorted(mod._media_request_modes(state)) != expected_modes:
            fail(f"media request modes mismatch: {env!r}")
        if env.get("PDOCKER_MEDIA_QUEUE_SOCKET") != "/run/pdocker-media/pdocker-media.sock":
            fail(f"media queue socket env missing: {env!r}")
        if env.get("PDOCKER_MEDIA_STATUS") != "executor-present-capture-disabled":
            fail(f"media status must allow executor control plane without capture: {env!r}")
        if env.get("PDOCKER_MEDIA_CAPTURE_READY") != "0" or env.get("PDOCKER_MEDIA_ENABLED") != "0":
            fail(f"media capture readiness must be false: {env!r}")
        if env.get("PDOCKER_MEDIA_DEVICE_PASSTHROUGH") != "0":
            fail(f"media must not expose raw Android device passthrough: {env!r}")
        if env.get("PDOCKER_MEDIA_EXECUTOR_AVAILABLE") != "1":
            fail(f"media executor control plane should be visible separately from readiness: {env!r}")
        if "/dev/video" in "".join(binds) or "/dev/snd" in "".join(binds):
            fail(f"media binds must not pass raw host media devices: {binds!r}")
        if media.get("CaptureReady") or media.get("CameraReady") or media.get("AudioReady"):
            fail(f"media diagnostics must not claim capture readiness: {media!r}")
        if not media.get("ExecutorAvailable") or media.get("RawDevicePassthrough"):
            fail(f"media diagnostics must separate executor control from raw passthrough: {media!r}")
        expected_bind = f"{home_path}:/run/pdocker-media"
        if expected_bind not in binds:
            fail(f"media runtime dir bind missing {expected_bind!r}: {binds!r}")
        srv = mod.ThreadingTCPHTTPServer(("127.0.0.1", 0), mod.DockerAPIHandler)
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        try:
            conn = http.client.HTTPConnection(srv.server_address[0], srv.server_address[1], timeout=5)
            try:
                conn.request("GET", "/system/media")
                resp = conn.getresponse()
                body = resp.read()
            finally:
                conn.close()
        finally:
            srv.shutdown()
            srv.server_close()
        if resp.status != 200:
            fail(f"/system/media status mismatch: {resp.status}, body={body!r}")
        endpoint = json.loads(body.decode("utf-8"))
        if endpoint.get("QueueSocket") != "/run/pdocker-media/pdocker-media.sock":
            fail(f"/system/media socket contract mismatch: {endpoint!r}")
        if endpoint.get("CaptureReady"):
            fail(f"/system/media must report disabled capture: {endpoint!r}")
        if not endpoint.get("ExecutorAvailable") or endpoint.get("RawDevicePassthrough"):
            fail(f"/system/media must expose executor control without raw passthrough: {endpoint!r}")
        ok("media bridge scaffold exposes socket contract without capture")


def test_android_media_static_contract() -> None:
    manifest = ANDROID_MANIFEST.read_text()
    bridge = ANDROID_BRIDGE.read_text()
    for permission in (
        "android.permission.CAMERA",
        "android.permission.RECORD_AUDIO",
        "android.permission.FOREGROUND_SERVICE_CAMERA",
        "android.permission.FOREGROUND_SERVICE_MICROPHONE",
    ):
        if permission not in manifest:
            fail(f"Android manifest missing media permission {permission}")
    if 'android:foregroundServiceType="dataSync"' not in manifest:
        fail("pdockerd service must stay dataSync until capture is implemented")
    for feature in (
        'android:name="android.hardware.camera"',
        'android:name="android.hardware.camera.any"',
        'android:name="android.hardware.microphone"',
    ):
        if feature not in manifest:
            fail(f"Android manifest must mark media hardware optional: {feature}")
    if "/run/pdocker-media/pdocker-media.sock" not in bridge:
        fail("Chaquopy bridge missing media queue socket contract")
    if 'PDOCKER_MEDIA_CAPTURE_READY"] = "0"' not in bridge:
        fail("Chaquopy bridge must keep media capture disabled in Phase-0")
    if 'PDOCKER_MEDIA_DEVICE_PASSTHROUGH"] = "0"' not in bridge:
        fail("Chaquopy bridge must keep raw media device passthrough disabled")
    if "/dev/video" in bridge or "/dev/snd" in bridge:
        fail("Chaquopy bridge must not expose raw Linux media device paths")
    ok("Android media manifest and bridge scaffold are statically guarded")


def test_gpu_shim_contract() -> None:
    with tempfile.TemporaryDirectory(prefix="pdocker-test-") as home:
        home_path = Path(home)
        shim = home_path / "pdocker-gpu-shim"
        shim.write_text("#!/bin/sh\nexit 0\n")
        shim.chmod(0o755)
        vulkan_icd = home_path / "pdocker-vulkan-icd.so"
        vulkan_icd.write_text("icd")
        vulkan_icd.chmod(0o755)
        opencl_icd = home_path / "pdocker-opencl-icd.so"
        opencl_icd.write_text("opencl")
        opencl_icd.chmod(0o755)
        mod = load_pdockerd_with_env(
            "gpu_shim",
            "no-proot",
            home_path,
            {
                "PDOCKER_GPU_SHIM_HOST_PATH": str(shim),
                "PDOCKER_GPU_SHIM_CONTAINER_PATH": "/usr/local/bin/pdocker-gpu-shim",
                "PDOCKER_VULKAN_ICD_HOST_PATH": str(vulkan_icd),
                "PDOCKER_VULKAN_ICD_CONTAINER_PATH": "/usr/local/lib/pdocker-vulkan-icd.so",
                "PDOCKER_OPENCL_ICD_HOST_PATH": str(opencl_icd),
                "PDOCKER_OPENCL_ICD_CONTAINER_PATH": "/usr/local/lib/pdocker-opencl-icd.so",
                "PDOCKER_GPU_EXECUTOR": str(home_path / "pdocker-gpu-executor"),
                "PDOCKER_GPU_HOST_DIR": str(home_path),
                "PDOCKER_GPU_CONTAINER_DIR": "/run/pdocker-gpu",
                "PDOCKER_GPU_QUEUE_SOCKET": "/run/pdocker-gpu/pdocker-gpu.sock",
                "PDOCKER_GPU_SHARED_DIR": "/run/pdocker-gpu",
                "PDOCKER_GPU_COMMAND_API": "pdocker-gpu-command-v1",
                "PDOCKER_GPU_ABI_VERSION": "0.1",
            },
        )
        state = {
            "HostConfig": {
                "DeviceRequests": [
                    {
                        "Driver": "pdocker-gpu",
                        "Count": -1,
                        "Capabilities": [["gpu"]],
                        "Options": {"pdocker.opencl": "opencl"},
                    }
                ]
            }
        }
        env = mod._gpu_env(state)
        binds = mod._gpu_binds(state)
        if env.get("PDOCKER_GPU_SHIM") != "/usr/local/bin/pdocker-gpu-shim":
            fail(f"gpu shim env missing: {env!r}")
        if env.get("PDOCKER_GPU_COMMAND_API") != "pdocker-gpu-command-v1":
            fail(f"gpu command api missing: {env!r}")
        if env.get("PDOCKER_GPU_LLM_ENGINE_LOCATION") != "container":
            fail(f"gpu engine location must stay container: {env!r}")
        if env.get("PDOCKER_GPU_QUEUE_SOCKET") != "/run/pdocker-gpu/pdocker-gpu.sock":
            fail(f"gpu queue socket env missing: {env!r}")
        if env.get("PDOCKER_GPU_SHARED_DIR") != "/run/pdocker-gpu":
            fail(f"gpu shared dir env missing: {env!r}")
        if env.get("PDOCKER_VULKAN_ICD") != "/usr/local/lib/pdocker-vulkan-icd.so":
            fail(f"pdocker Vulkan ICD env missing: {env!r}")
        if env.get("PDOCKER_VULKAN_ICD_KIND") != "pdocker-bridge-minimal":
            fail(f"pdocker Vulkan ICD kind missing: {env!r}")
        if env.get("PDOCKER_VULKAN_ICD_READY") != "0":
            fail(f"pdocker Vulkan ICD must not claim compute readiness yet: {env!r}")
        if env.get("PDOCKER_OPENCL_ICD") != "/usr/local/lib/pdocker-opencl-icd.so":
            fail(f"pdocker OpenCL ICD env missing: {env!r}")
        if env.get("PDOCKER_OPENCL_ICD_KIND") != "pdocker-bridge-minimal":
            fail(f"pdocker OpenCL ICD kind missing: {env!r}")
        if env.get("PDOCKER_OPENCL_API_VERSION") != "1.2":
            fail(f"pdocker OpenCL API version missing: {env!r}")
        expected_bind = f"{shim}:/usr/local/bin/pdocker-gpu-shim:ro"
        if expected_bind not in binds:
            fail(f"gpu shim bind missing {expected_bind!r}: {binds!r}")
        expected_icd_bind = f"{vulkan_icd}:/usr/local/lib/pdocker-vulkan-icd.so:ro"
        if expected_icd_bind not in binds:
            fail(f"gpu Vulkan ICD bind missing {expected_icd_bind!r}: {binds!r}")
        expected_opencl_bind = f"{opencl_icd}:/usr/local/lib/pdocker-opencl-icd.so:ro"
        if expected_opencl_bind not in binds:
            fail(f"gpu OpenCL ICD bind missing {expected_opencl_bind!r}: {binds!r}")
        expected_opencl_lib_bind = f"{opencl_icd}:/usr/local/lib/libOpenCL.so.1:ro"
        if expected_opencl_lib_bind not in binds:
            fail(f"gpu OpenCL lib bind missing {expected_opencl_lib_bind!r}: {binds!r}")
        expected_gpu_dir_bind = f"{home_path}:/run/pdocker-gpu"
        if expected_gpu_dir_bind not in binds:
            fail(f"gpu runtime dir bind missing {expected_gpu_dir_bind!r}: {binds!r}")
        ok("GPU shim contract injects device-independent container ABI")


def test_dockerfile_run_process_group_isolation() -> None:
    # Dockerfile RUN process-group isolation: long native builds may spawn
    # tracers, compilers, linkers, and shell wrappers. Keep that process tree
    # in its own session so child-side signals cannot terminate pdockerd.
    text = PDOCKERD.read_text(errors="replace")
    marker = (
        "proc = subprocess.Popen(\n"
        "                build_run_argv(rootfs, build_run_command(args),"
    )
    at = text.find(marker)
    if at < 0:
        fail("Dockerfile RUN subprocess launch not found")
    snippet = text[at : at + 700]
    if "preexec_fn=build_child_preexec" not in snippet:
        fail("Dockerfile RUN subprocess must use build_child_preexec")
    if "def build_child_preexec()" not in text or "oom_score_adj" not in text or "os.setsid()" not in text:
        fail("build_child_preexec must isolate sessions and bias LMK away from pdockerd")
    ok("Dockerfile RUN process-group isolation keeps build children away from pdockerd")


def test_android_build_profile_injected_outside_dockerfile() -> None:
    text = PDOCKERD.read_text(errors="replace")
    required = [
        "PDOCKER_BUILD_PROFILE",
        "CMAKE_BUILD_PARALLEL_LEVEL",
        "MAKEFLAGS",
        "NINJA_STATUS",
        'os.environ.get("PDOCKER_BUILD_TOOLS", "0") == "1"',
    ]
    missing = [needle for needle in required if needle not in text]
    if missing:
        fail(f"Android build profile missing markers: {missing}")
    if 'os.environ.get("PDOCKER_BUILD_TOOLS", "1")' in text:
        fail("Android build tool wrappers must be opt-in, not default")
    dockerfile = (REPO_ROOT / "app/src/main/assets/project-library/llama-cpp-gpu/Dockerfile").read_text(errors="replace")
    if "pdocker-bridge-safe-glslc" in dockerfile or "LLAMA_CPP_VULKAN_SHADER_PROFILE" in dockerfile:
        fail("llama Dockerfile must not carry pdocker-specific shader wrapper tuning")
    ok("Android build profile is injected without default build-tool rewriting")


def test_vulkan_icd_memory_advertising_is_not_fixed_8gib() -> None:
    text = (REPO_ROOT / "docker-proot-setup/src/gpu/pdocker_vulkan_icd.c").read_text(errors="replace")
    required = [
        'fopen("/proc/meminfo", "r")',
        "MemAvailable:",
        "max_heap = (VkDeviceSize)(2ull * 1024ull * 1024ull * 1024ull)",
        "min_heap = (VkDeviceSize)(512ull * 1024ull * 1024ull)",
        "PDOCKER_VULKAN_HEAP_BYTES",
    ]
    missing = [needle for needle in required if needle not in text]
    if missing:
        fail(f"Vulkan ICD memory advertising guard missing markers: {missing}")
    if "return (VkDeviceSize)(8ull * 1024ull * 1024ull * 1024ull)" in text:
        fail("Vulkan ICD must not default to a fixed 8GiB advertised heap")
    ok("Vulkan ICD defaults to device-memory-aware advertised limits")


def main() -> int:
    test_direct_backend_contract()
    test_direct_executor_probe_contract()
    test_direct_executor_process_capability_contract()
    test_direct_backend_rejects_fake_container_start()
    test_start_container_reconciles_live_pid()
    test_start_container_rejects_reused_pid()
    test_default_no_proot_runtime_path()
    test_duplicate_name_resolution_contract()
    test_network_metadata_contract()
    test_port_mapping_status_contract()
    test_dockerfile_unknown_instruction_rejected()
    test_direct_run_requires_real_executor()
    test_existing_tag_inline_run_cache()
    test_existing_tag_full_image_cache()
    test_build_cache_contract()
    test_storage_summary_distinguishes_layer_and_upper_bytes()
    test_active_operations_contract()
    test_exclusive_build_operations_contract()
    test_active_operations_prune_stale_idle_entries()
    test_host_environment_contract()
    test_media_bridge_scaffold_contract()
    test_android_media_static_contract()
    test_gpu_shim_contract()
    test_dockerfile_run_process_group_isolation()
    test_android_build_profile_injected_outside_dockerfile()
    test_vulkan_icd_memory_advertising_is_not_fixed_8gib()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

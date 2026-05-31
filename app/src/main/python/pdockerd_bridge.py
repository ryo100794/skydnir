"""Kotlin -> pdockerd adapter run inside Chaquopy.

Kotlin stages the expected pdockerd project layout at `runtime_dir`
(bin/pdockerd, docker-bin/crane, docker-bin/proot, lib/libcow.so), so
pdockerd's `_PROJECT_DIR = dirname(dirname(__file__))` resolves to that
directory and the daemon finds all native deps via the same relative
paths it uses on a Linux host. We just exec the script via runpy.
"""
import os
import runpy
import socket
import socketserver
import sys
import threading


class _ConnectProxyHandler(socketserver.BaseRequestHandler):
    """Minimal HTTP CONNECT proxy.

    Why: crane (pure-Go, CGO disabled) resolves DNS by reading
    /etc/resolv.conf directly — Android app sandboxes don't ship one,
    so Go falls back to [::1]:53 and everything ENOENTs. proot can't
    step in because its ptrace-based execve intercept on Android 15
    returns ENOENT for child execve. By setting HTTPS_PROXY/HTTP_PROXY
    to this proxy, crane sends `CONNECT docker.io:443 HTTP/1.1` and we
    resolve docker.io via Python's socket.create_connection which goes
    through bionic's getaddrinfo and honours Android's per-network DNS.
    """
    timeout = 30

    def handle(self) -> None:
        client = self.request
        try:
            header = b""
            while b"\r\n\r\n" not in header:
                chunk = client.recv(4096)
                if not chunk:
                    return
                header += chunk
                if len(header) > 16384:
                    return
            line = header.split(b"\r\n", 1)[0].decode("latin-1", "replace")
            parts = line.split()
            if len(parts) < 2 or parts[0].upper() != "CONNECT":
                client.sendall(b"HTTP/1.1 405 Method Not Allowed\r\n\r\n")
                return
            host, _, port = parts[1].rpartition(":")
            if not host:
                client.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                return
            try:
                upstream = socket.create_connection((host, int(port)), timeout=30)
            except (socket.gaierror, OSError) as e:
                msg = f"HTTP/1.1 502 Bad Gateway\r\n\r\n{e}".encode()
                client.sendall(msg)
                return
            client.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
            _pipe(client, upstream)
        except Exception:
            pass
        finally:
            try: client.close()
            except Exception: pass


def _pipe(a: socket.socket, b: socket.socket) -> None:
    def forward(src: socket.socket, dst: socket.socket) -> None:
        try:
            while True:
                data = src.recv(16384)
                if not data:
                    break
                dst.sendall(data)
        except OSError:
            pass
        finally:
            for s in (src, dst):
                try: s.shutdown(socket.SHUT_RDWR)
                except OSError: pass
    t1 = threading.Thread(target=forward, args=(a, b), daemon=True)
    t2 = threading.Thread(target=forward, args=(b, a), daemon=True)
    t1.start(); t2.start()
    t1.join(); t2.join()
    for s in (a, b):
        try: s.close()
        except OSError: pass


class _ReusingThreadingServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def _engine_tcp_host() -> str | None:
    """Return the local Docker-compatible TCP endpoint for external clients.

    TermPort and other Android apps must see Skydnir as a Docker Engine API
    endpoint, not as an Android-private service.  Keep the default loopback-only
    and Docker-shaped (`127.0.0.1:2375`).  Users/tests can override it with
    SKYDNIR_ENGINE_TCP_HOST or PDOCKER_ENGINE_TCP_HOST; set it to empty/none/off
    to disable the TCP listener while keeping the Unix socket path.
    """
    value = (
        os.environ.get("SKYDNIR_ENGINE_TCP_HOST")
        or os.environ.get("PDOCKER_ENGINE_TCP_HOST")
        or "127.0.0.1:2375"
    ).strip()
    if not value or value.lower() in {"0", "false", "no", "none", "off", "disabled"}:
        return None
    return value


def _start_connect_proxy() -> int:
    server = _ReusingThreadingServer(("127.0.0.1", 0), _ConnectProxyHandler)
    threading.Thread(
        target=server.serve_forever, name="pdockerd-proxy", daemon=True
    ).start()
    return server.server_address[1]


def run_daemon(
    sock_path: str,
    home: str,
    runtime_dir: str,
    runtime_backend: str = "",
    direct_experimental_process_exec: bool = False,
) -> None:
    os.environ["PDOCKER_HOME"] = home
    # pdockerd stages blob downloads, layer tars and image save/load tarballs
    # under /tmp by default. Android app sandboxes can't write there, so
    # point pdockerd at runtime/tmp (already created for proot). Also set
    # TMPDIR so proot's f2fs probe + path canonicalization don't fall back
    # to its baked-in Termux default (/data/data/com.termux/files/usr/tmp,
    # which doesn't exist outside Termux and floods stderr with two
    # warnings on every container start).
    tmp_dir = os.path.join(runtime_dir, "tmp")
    os.environ["PDOCKER_TMP_DIR"] = tmp_dir
    os.environ["TMPDIR"] = tmp_dir
    os.environ["PROOT_TMP_DIR"] = tmp_dir
    os.environ["PDOCKER_RUNTIME_PREFLIGHT"] = "1"
    # Phones have tight app-data budgets. Keep successful rebuilds from
    # accumulating old, unreferenced filesystem layers when a tag is replaced.
    os.environ.setdefault("PDOCKER_AUTO_PRUNE_UNREFERENCED_LAYERS", "1")
    # Interrupted Dockerfile builds leave internal build_* rootfs directories.
    # pdockerd skips roots that are still referenced by a running build process.
    os.environ.setdefault("PDOCKER_AUTO_PRUNE_BUILD_ARTIFACTS", "1")

    if runtime_backend:
        os.environ["PDOCKER_RUNTIME_BACKEND"] = runtime_backend
    effective_process_exec = bool(direct_experimental_process_exec)
    # For direct backend runs, process-exec must be explicitly available so
    # container RUN/docker run/compose services can be scheduled.
    if runtime_backend == "direct":
        effective_process_exec = True
    if effective_process_exec:
        os.environ["PDOCKER_DIRECT_EXPERIMENTAL_PROCESS_EXEC"] = "1"
        os.environ.setdefault("PDOCKER_DIRECT_TRACE_SYSCALLS", "0")
        os.environ.setdefault("PDOCKER_DIRECT_TRACE_MODE", "seccomp")
        # Direct execution supports pdockerd's lower/upper cow_bind contract.
        # Prefer that over materializing a full container rootfs for every
        # create; large development images otherwise spend tens of seconds in
        # rootfs preparation before the process can even start.
        os.environ.setdefault("PDOCKER_USE_COW_BIND", "1")
    else:
        os.environ.pop("PDOCKER_DIRECT_EXPERIMENTAL_PROCESS_EXEC", None)
        os.environ.pop("PDOCKER_DIRECT_TRACE_SYSCALLS", None)
        os.environ.pop("PDOCKER_DIRECT_TRACE_MODE", None)
        os.environ.pop("PDOCKER_USE_COW_BIND", None)

    os.environ.setdefault("PDOCKER_RUNTIME_BACKEND", "no-proot")
    direct_executor = os.path.join(runtime_dir, "docker-bin", "pdocker-direct")
    if os.path.exists(direct_executor):
        os.environ["PDOCKER_DIRECT_EXECUTOR"] = direct_executor
    gpu_executor = os.path.join(runtime_dir, "gpu", "pdocker-gpu-executor")
    if os.path.exists(gpu_executor):
        os.environ["PDOCKER_GPU_EXECUTOR"] = gpu_executor
        os.environ["PDOCKER_GPU_EXECUTOR_AVAILABLE"] = "1"
        os.environ["PDOCKER_GPU_HOST_DIR"] = os.path.join(runtime_dir, "gpu")
        os.environ["PDOCKER_GPU_CONTAINER_DIR"] = "/run/pdocker-gpu"
        os.environ["PDOCKER_GPU_QUEUE_SOCKET"] = "/run/pdocker-gpu/pdocker-gpu.sock"
        os.environ["PDOCKER_GPU_SHARED_DIR"] = "/run/pdocker-gpu"
        os.environ["PDOCKER_GPU_COMMAND_API"] = "pdocker-gpu-command-v1"
        os.environ["PDOCKER_GPU_ABI_VERSION"] = "0.1"
        os.environ["PDOCKER_GPU_EXECUTOR_ROLE"] = "gpu-command-executor"
        os.environ["PDOCKER_GPU_LLM_ENGINE_LOCATION"] = "container"
    gpu_shim = os.path.join(runtime_dir, "lib", "pdocker-gpu-shim")
    if os.path.exists(gpu_shim):
        os.environ["PDOCKER_GPU_SHIM_HOST_PATH"] = gpu_shim
        os.environ["PDOCKER_GPU_SHIM_CONTAINER_PATH"] = "/usr/local/bin/pdocker-gpu-shim"
    vulkan_icd = os.path.join(runtime_dir, "lib", "pdocker-vulkan-icd.so")
    if os.path.exists(vulkan_icd):
        os.environ["PDOCKER_VULKAN_ICD_HOST_PATH"] = vulkan_icd
        os.environ["PDOCKER_VULKAN_ICD_CONTAINER_PATH"] = "/usr/local/lib/pdocker-vulkan-icd.so"
        os.environ["PDOCKER_VULKAN_ICD_KIND"] = "pdocker-bridge-minimal"
        os.environ["PDOCKER_VULKAN_ICD_READY"] = "0"
        os.environ["PDOCKER_VULKAN_API_VERSION"] = "1.2.0"
    opencl_icd = os.path.join(runtime_dir, "lib", "pdocker-opencl-icd.so")
    if os.path.exists(opencl_icd):
        os.environ["PDOCKER_OPENCL_ICD_HOST_PATH"] = opencl_icd
        os.environ["PDOCKER_OPENCL_ICD_CONTAINER_PATH"] = "/usr/local/lib/pdocker-opencl-icd.so"
        os.environ["PDOCKER_OPENCL_LIBRARY_CONTAINER_PATH"] = "/usr/local/lib/libOpenCL.so"
        os.environ["PDOCKER_OPENCL_ICD_KIND"] = "pdocker-bridge-minimal"
        os.environ["PDOCKER_OPENCL_ICD_READY"] = "1"
        os.environ["PDOCKER_OPENCL_API_VERSION"] = "1.2"
    else:
        os.environ.setdefault("PDOCKER_OPENCL_ICD_KIND", "android-opencl-loader-probe")
        os.environ.setdefault("PDOCKER_OPENCL_ICD_READY", "0")

    media_host_dir = os.path.join(runtime_dir, "media")
    os.makedirs(media_host_dir, exist_ok=True)
    media_executor = os.path.join(media_host_dir, "pdocker-media-executor")
    media_descriptor = os.path.join(media_host_dir, "pdocker-media-capabilities.json")
    os.environ["PDOCKER_MEDIA_HOST_DIR"] = media_host_dir
    os.environ["PDOCKER_MEDIA_CONTAINER_DIR"] = "/run/pdocker-media"
    os.environ["PDOCKER_MEDIA_QUEUE_SOCKET"] = "/run/pdocker-media/pdocker-media.sock"
    os.environ["PDOCKER_MEDIA_SHARED_DIR"] = "/run/pdocker-media"
    os.environ["PDOCKER_MEDIA_DESCRIPTOR_HOST_PATH"] = media_descriptor
    os.environ["PDOCKER_MEDIA_DESCRIPTOR_PATH"] = "/run/pdocker-media/pdocker-media-capabilities.json"
    os.environ["PDOCKER_MEDIA_COMMAND_API"] = "pdocker-media-command-v1"
    os.environ["PDOCKER_MEDIA_ABI_VERSION"] = "0.1"
    os.environ["PDOCKER_MEDIA_EXECUTOR_ROLE"] = "android-media-command-executor"
    os.environ["PDOCKER_MEDIA_CONTRACT"] = "linux-like-socket-env-v1"
    os.environ["PDOCKER_MEDIA_DEVICE_PASSTHROUGH"] = "0"
    os.environ["PDOCKER_MEDIA_VIDEO_API"] = "android-camera2"
    os.environ["PDOCKER_MEDIA_AUDIO_API"] = "android-audiorecord-audiotrack"
    os.environ["PDOCKER_MEDIA_AUDIO_DEVICE_API"] = "android-audiomanager"
    os.environ["PDOCKER_MEDIA_CAPABILITIES"] = ",".join([
        "video.camera2",
        "camera.front",
        "camera.rear",
        "camera.external",
        "audio.capture",
        "audio.playback",
        "audio.usb.multichannel",
    ])
    os.environ["PDOCKER_MEDIA_CAPTURE_READY"] = "0"
    os.environ["PDOCKER_MEDIA_CAMERA_READY"] = "0"
    os.environ["PDOCKER_MEDIA_AUDIO_READY"] = "0"
    os.environ["PDOCKER_MEDIA_STATUS"] = "phase1-control-plane-only"
    os.environ["PDOCKER_MEDIA_EXECUTOR_AVAILABLE"] = "0"
    if os.path.exists(media_executor):
        os.environ["PDOCKER_MEDIA_EXECUTOR"] = media_executor
        os.environ["PDOCKER_MEDIA_EXECUTOR_AVAILABLE"] = "1"
        os.environ["PDOCKER_MEDIA_STATUS"] = "executor-control-plane-ready-capture-disabled"

    bin_dir = os.path.join(runtime_dir, "docker-bin")
    os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")

    # Keep runtime/lib visible for libcow and future direct-runtime shims.
    lib_dir = os.path.join(runtime_dir, "lib")
    existing = os.environ.get("LD_LIBRARY_PATH", "")
    os.environ["LD_LIBRARY_PATH"] = lib_dir + (os.pathsep + existing if existing else "")

    # DNS fix for crane: start an in-process CONNECT proxy on 127.0.0.1
    # and advertise it via HTTP(S)_PROXY. Python's socket.gethostbyname
    # uses bionic's Android-aware resolver, so DNS "just works".
    port = _start_connect_proxy()
    proxy_url = f"http://127.0.0.1:{port}"
    os.environ["HTTP_PROXY"]  = proxy_url
    os.environ["HTTPS_PROXY"] = proxy_url
    os.environ["NO_PROXY"]    = "localhost,127.0.0.1,::1"

    # TLS roots: Go's default certFiles/certDirectories list doesn't cover
    # Android. SSL_CERT_DIR points crane at the system CA store
    # (/system/etc/security/cacerts, c_rehash-hashed filenames, readable
    # by untrusted_app_29). Without this crane reports
    # "x509: certificate signed by unknown authority".
    os.environ.setdefault("SSL_CERT_DIR", "/system/etc/security/cacerts")

    # Probe hardlink capability on the app's own data dir, then pick a
    # layer-extraction link policy. Android app sandboxes return EACCES
    # on link() for app_data_file via SELinux, so use symlink as the
    # nearest-lossless substitute — same "two names, one content" feel,
    # zero extra disk.
    link_ok = _probe_hardlink(home)
    os.environ["PDOCKER_LINK_MODE"] = "hard" if link_ok else "symlink"

    pdockerd_path = os.path.join(runtime_dir, "bin", "pdockerd")
    os.environ.setdefault("SKYDNIR_DAEMON_NAME", "skydnird")
    os.environ.setdefault("PDOCKER_SUPPRESS_DEPRECATION_WARNING", "1")
    sys.argv = ["skydnird", "--socket", sock_path]
    engine_tcp_host = _engine_tcp_host()
    if engine_tcp_host:
        os.environ["SKYDNIR_ENGINE_TCP_HOST_EFFECTIVE"] = engine_tcp_host
        sys.argv.extend(["--host", engine_tcp_host])
    runpy.run_path(pdockerd_path, run_name="__main__")


def _probe_hardlink(home: str) -> bool:
    """Probe whether os.link() works under the app's data dir. Android
    SELinux (untrusted_app_29) denies link() on app_data_file with EACCES
    — we don't find that out from the manifest, only by trying."""
    import errno
    probe_dir = os.path.join(home, ".probe")
    os.makedirs(probe_dir, exist_ok=True)
    src = os.path.join(probe_dir, "src")
    dst = os.path.join(probe_dir, "dst")
    symdst = os.path.join(probe_dir, "symdst")
    for p in (src, dst, symdst):
        try: os.remove(p)
        except OSError: pass
    with open(src, "w") as f:
        f.write("probe")
    link_ok = False
    link_err = sym_ok = sym_err = None
    try:
        os.link(src, dst); link_ok = True
    except OSError as e:
        link_err = f"{errno.errorcode.get(e.errno, e.errno)}: {e.strerror}"
    try:
        os.symlink(src, symdst); sym_ok = True
    except OSError as e:
        sym_err = f"{errno.errorcode.get(e.errno, e.errno)}: {e.strerror}"
    sys.stderr.write(
        f"[pdockerd] hardlink_probe: link_ok={link_ok} ({link_err}), "
        f"symlink_ok={sym_ok} ({sym_err}) at {probe_dir}\n"
    )
    sys.stderr.flush()
    for p in (src, dst, symdst):
        try: os.remove(p)
        except OSError: pass
    return link_ok

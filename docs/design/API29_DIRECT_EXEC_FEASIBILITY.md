# API 29+ direct execution feasibility

Snapshot date: 2026-05-02.

This note records the technical building blocks for running Docker/OCI rootfs
processes from an Android app that targets API 29 or later. It is intentionally
separate from the product roadmap: the goal here is to show what is already
proved, what is plausible, and what is still unproved.

## Hard constraint

Android 10/API 29 adds a W^X restriction for untrusted apps: app-home files are
writable data, so they cannot be invoked directly with `execve()`. A Docker
image unpacked under:

```text
/data/data/<package>/files/pdocker/images/.../rootfs
```

must therefore not be launched as:

```text
execve("/data/data/<package>/files/.../rootfs/bin/sh", ...)
```

The first executable must be code that Android considers app-shipped code, such
as an APK native library extracted into `nativeLibraryDir`.

## Compatibility invariant

The Docker/OCI image rootfs remains standard. This plan must not introduce a
Skydnir-specific base image, Dockerfile syntax, or rootfs layout.

The Android-specific work is only the execution layer:

```text
OCI image/layers/rootfs unchanged
  -> APK-shipped native executor
      -> APK-shipped or rootfs dynamic loader
          -> Docker rootfs program and libraries
              -> path/syscall mediation
```

## Building blocks

| Building block | Current evidence | Status |
|---|---|---|
| APK-shipped executor | `libpdockerdirect.so` is staged through `jniLibs` and linked as `docker-bin/pdocker-direct`. | Proved for helper startup/probe. |
| Avoid direct rootfs `execve` | `pdocker-direct` calls an executable loader first, then passes the rootfs program as loader input. | Partially proved. It avoids the direct app-data program `execve` path. |
| Rootfs loader/library path | `pdocker-direct` builds `--library-path` from rootfs lib dirs and routes child `execve()` calls back through the rootfs dynamic loader. | Proved for SDK28 compat smoke on SOG15. |
| Rootfs path mediation | `pdocker-direct` rewrites selected syscall path arguments (`openat`, `newfstatat`, `statx`, `execve`, etc.) to the materialized rootfs. | Proved for the tiny build/compose smoke; incomplete for full Docker semantics. |
| Docker layer/rootfs compatibility | Existing image materialization keeps Docker/OCI rootfs content and layer semantics in `pdockerd`. | Proved for pull/materialize/metadata; not dependent on custom base images. |
| Process capability gate | `pdockerd` probes `pdocker-direct` and only routes RUN/run/exec when the helper advertises `process-exec=1`. | Proved as a safety mechanism. The helper must not advertise success until app-domain process execution is stable. |
| Compose/Engine API integration | Compose can build, create, start, log, and remove a tiny service through `pdockerd` on SDK28 compat. | Proved for the smoke scenario; larger services still need coverage. |

## Current proof points

The useful proof is not "Termux can do it". The useful proof points are:

1. Android will start an APK-shipped native helper from `nativeLibraryDir`.
2. The helper can parse a Docker-like execution request: rootfs, workdir, env,
   bind metadata, and argv.
3. The helper can avoid calling `execve()` on the rootfs program directly.
4. A glibc loader path can be made explicit instead of relying on the kernel's
   ELF interpreter lookup for the rootfs program.
5. A preload shim can virtualize rootfs paths without changing the image.

The strongest local experiment so far is that the exact helper/rootfs path can
launch a rootfs command under `run-as`. The same path from the app/pdockerd
execution route still fails with SIGSYS (`exit code -31`). That means the
remaining blocker is not "the rootfs is not an ELF" or "the rootfs cannot be
read"; it is an app-domain syscall/seccomp/runtime-init issue.

## Latest device test

Command:

```sh
bash scripts/android-api29-direct-feasibility.sh --no-install
```

Initial result on 2026-05-02:

```text
Device: SOG15
Android: 16 / SDK 36
Package targetSdk: 34
SELinux domain: u:r:untrusted_app:s0:...
API29_DIRECT_EXEC_FEASIBILITY=FAIL
```

Observed details:

- Engine metadata path works: `docker version` reaches bundled `pdockerd`.
- `run-as` controls are intentionally not treated as product proof:
  - app-data copy of `/system/bin/sh` executes with rc 0;
  - `pdocker-direct -> bundled loader -> rootfs /bin/sh` executes with rc 0;
  - rootfs path shim blocks host `/data` lookup (`ls /data` rc 2) while leaving
    `/proc` visible.
- Real app-domain Dockerfile execution fails:

```text
Step: RUN printf 'api29-direct-feasibility\n' > /pdocker-api29.txt
pdocker-direct-executor: mode=build rootfs=/data/data/.../rootfs workdir=/ env=3 bind=0 argv0=/bin/sh

RUN failed with exit code -31
ERROR: build failed
```

Interpretation:

- `run-as` is a misleading control path for this question. It can execute cases
  that the real app process cannot.
- The app-domain path is the only product feasibility signal here, and it still
  fails.
- A helper must not advertise `process-exec=1` in a release/default build until
  this app-domain test passes. Experimental builds may enable it only to keep
  probing the SIGSYS boundary.

## SDK28 compat direct test

Command:

```sh
PDOCKER_ANDROID_FLAVOR=compat bash scripts/build-apk.sh
PDOCKER_ANDROID_FLAVOR=compat bash scripts/android-device-smoke.sh --no-install
```

Initial result on 2026-05-02:

```text
Device: SOG15
Android: 16 / SDK 36
Package targetSdk: 28
SELinux domain: u:r:untrusted_app_27:s0:...
External PRoot payloads: absent
Dockerfile RUN: failed with exit code -31
```

Additional Java-side control:

```text
PdockerdDebugReceiver -> Java ProcessBuilder -> pdocker-direct -> loader -> rootfs /bin/sh
rc=159
```

Interpretation:

- Lowering targetSdk to 28 is not sufficient on this Android 16 device for the
  current direct-loader approach.
- The failure is not specific to Chaquopy/Python `subprocess`; Java
  `ProcessBuilder` from the app domain also dies with SIGSYS.
- PRoot is not included in the compat APK. The compat flavor remains useful as
  a build/runtime switch point, but it is not yet a working execution backend.

Follow-up syscall-fetch result:

```text
pdocker-direct-trace: enter nr=221(execve)
pdocker-direct-trace: SIGSYS nr=99(set_robust_list)
pdocker-direct-trace: suppress SIGSYS after emulated nr=99(set_robust_list)
shell_builtin_ok
pdocker-direct-trace: child exited rc=0
```

This proves that the app-domain trace/broker foundation is viable for at least
the initial glibc loader and shell path. The blocker is no longer "the app
domain cannot fetch syscalls"; `pdocker-direct` can fetch syscall numbers and
registers, identify the blocked `set_robust_list`, emulate success, suppress the
corresponding SIGSYS, and run `/bin/sh -c "echo shell_builtin_ok"` to rc 0.

Follow-up SDK28 compat result on 2026-05-02:

```text
PDOCKER_ANDROID_FLAVOR=compat bash scripts/android-device-smoke.sh --no-install
...
Step: RUN printf 'pdocker-smoke-build\n' > /pdocker-smoke.txt
Successfully built docker.io/local/pdocker-device-smoke:latest
...
Container device-smoke-app-1  Started
compose container state: exited 0
[pdocker smoke] passed
```

This proves the scratch broker can now:

- trace fork/vfork/clone children with per-tracee state;
- route child `execve("/bin/ls")`-style calls through the rootfs dynamic loader;
- rewrite common absolute path syscalls into the materialized rootfs;
- emulate or suppress currently blocked startup syscalls (`set_robust_list`,
  `rseq`, and a permissive `faccessat2` compatibility response);
- run a standard `ubuntu:22.04` Dockerfile `RUN`;
- run a tiny `docker compose up --build -d` service and capture its logs.

Important correction: the first passing smoke briefly used an incorrect
`pdocker-direct` fallback that redirected missing `/bin/*` paths to `/usr/bin/*`
after `pdockerd` had flattened merged-usr symlinks into real directories. That
is not Docker-compatible behavior. The correct implementation is to preserve
the image rootfs exactly enough that `/bin -> usr/bin` remains a symlink, and
to let the rootfs loader/path mediation resolve the standard path. The fallback
was removed; the materializer must keep merged-usr symlinks intact.

The compatibility proof is currently for the SDK28 compat flavor on SOG15
(Android 16 / SDK 36). API29+ targetSdk direct execution remains unproved.
The broker still needs exact errno semantics for path probes, TTY/attach,
signals, bind mounts, port mediation, and broader syscall coverage.

## Unproved blocker

API 29+ full direct execution is not yet proved. The blocking item is:

```text
APK helper -> loader -> glibc rootfs program
```

inside the real app process domain without SIGSYS.

Before `process-exec=1` is enabled by default, the project needs a repeatable
test that identifies the killed syscall and proves one of these fixes:

- avoid the syscall with loader/glibc tunables or a smaller trampoline;
- emulate or translate it in a ptrace/syscall broker;
- replace the affected startup path with a bionic-native trampoline;
- intentionally choose an SDK 28 compatibility flavor for the PRoot-like path.

If none of those can pass on stock Android app domains, then API 29+ direct
Docker-image execution is not product-feasible as a normal unprivileged APK, and
the project should keep API 29+ for metadata/UI/image management while using a
separate compatibility flavor or privileged helper for process execution.

## Acceptance test for feasibility

The minimum proof must run on a target API 29+ APK build:

```sh
docker build -t pdocker-smoke - <<'EOF'
FROM ubuntu:22.04
RUN printf 'ok\n' > /pdocker-smoke.txt
CMD ["/bin/sh", "-c", "cat /pdocker-smoke.txt && sleep 2"]
EOF

docker run --name pdocker-smoke-run pdocker-smoke
docker logs pdocker-smoke-run
docker inspect pdocker-smoke-run
```

Pass criteria:

- Dockerfile syntax is standard.
- Base image is standard `ubuntu:22.04`.
- `/bin/sh` and shared libraries come from the image rootfs.
- no rootfs ELF is directly invoked with `execve()`;
- logs are produced by the real container process, not a fake listener;
- `docker inspect` reports the correct image/container metadata.

Until this passes in the app domain, API 29+ direct execution remains a
prototype, not a proven runtime.

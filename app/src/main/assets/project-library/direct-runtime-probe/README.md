# Skydnir Direct Runtime Probe

This template is a small, reusable test container for pdocker direct-runtime
debugging. It is intentionally separate from llama.cpp, code-server, Blender,
or ROS so runtime regressions can be reproduced without rebuilding large
application images.

It includes:

- A long-lived container named `pdocker-direct-runtime-probe`.
- A default startup probe on `compose up`.
- `python3` for controlled large-allocation tests.
- `/usr/local/bin/pdocker-container-probe`, the same probe payload used by
  the repository test runner.
- Standard Compose and Dockerfile inputs only; no pdocker-specific Dockerfile
  syntax.
- The selected Android Documents folder mounted at `/documents` for exporting
  test reports when needed.
- A cross-project shared bind mount at `/shared`. Override
  `PDOCKER_DOCUMENTS_HOST`, `PDOCKER_DOCUMENTS_MOUNT`,
  `PDOCKER_SHARED_DOCUMENTS_HOST`, or `PDOCKER_SHARED_DOCUMENTS_MOUNT` when
  projects intentionally share the same folder or mount path.

Use from the repository test route:

```sh
ROOTFS=/path/to/container/rootfs scripts/verify-heavy.sh --container-probe
```

Use from pdocker:

1. Install `Skydnir Direct Runtime Probe` from the Library tab.
2. Run compose up.
3. Open `/documents/pdocker-exports/direct-runtime-probe/latest.log` from the
   selected Android Documents folder, or inspect `/reports/latest.log` inside
   the container.

Use from inside the running container:

```sh
pdocker-container-probe
```

The probe checks argv preservation, `/usr/bin/[` shell bracket execution,
guest `/proc/self/exe` behavior, and large allocation handling. The default
compose-up run performs the safe in-container allocation path. The generic
repository runner can additionally force the direct-executor memory guard path
without consuming real device memory.

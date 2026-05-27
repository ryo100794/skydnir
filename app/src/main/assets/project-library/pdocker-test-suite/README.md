# Skydnir Test Suite

This template is the reusable in-container test harness for pdocker. It is
separate from application templates such as VS Code Server, llama.cpp, ROS, or
Blender so test runs can be repeated without rebuilding those larger images.

The container is intentionally idle after `compose up`. Run tests through
Docker exec:

```sh
docker exec pdocker-test-suite run-pdocker-test-suite --scenario all
```

The test command writes reports to `/reports` and mirrors them to the selected
Android Documents folder:

```text
/documents/pdocker-exports/pdocker-test-suite/latest.json
/documents/pdocker-exports/pdocker-test-suite/latest.log
```

The compose template mounts the selected Android Documents folder at
`/documents` by default. Use it only for explicit test reports, import/export,
or data exchange. Override `PDOCKER_DOCUMENTS_HOST` or
`PDOCKER_DOCUMENTS_MOUNT` to share a folder or move the mount path, or use
`PDOCKER_SHARED_DOCUMENTS_HOST` for the cross-project `/shared` mount.

The default suite covers a small but repeatable smoke set:

- Documents report write path.
- Fast workspace write path.
- argv preservation for long object-file-like arguments.
- linker-style argv preservation for many separate `flash_attn*.o` object
  arguments, matching the llama.cpp Vulkan link failure class.
- `/usr/bin/[` execution.
- `/proc/self/exe` visibility.
- small file-I/O open/close loop.
- tar archive round-trip.
- `/shared` mount visibility.

Scenario selectors:

- `all`: run every bundled scenario.
- `smoke`: fast workspace and input-validation checks.
- `direct`: direct-runtime observable behavior, argv, proc, path, and payload
  probe checks.
- `io`: file-I/O smoke checks.
- `archive`: tar/archive round-trip checks.
- `documents`: Documents and shared-mount checks.

Do not put hot build caches, layer stores, model files, or database journals in
Documents by default. Keep those in the app-private workspace and write only
summaries or requested artifacts to `/documents`.

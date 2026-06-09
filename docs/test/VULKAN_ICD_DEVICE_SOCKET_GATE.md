# Vulkan ICD Device Socket Gate

Snapshot date: 2026-06-09.

## Scope

This gate defines the evidence needed to promote a glibc-facing Vulkan ICD
smoke from host-only coverage to real Android-device coverage. The specific
path under test is:

```text
glibc Vulkan loader in a guest/container
  -> /etc/vulkan/icd.d/pdocker-android.json
  -> /usr/local/lib/pdocker-vulkan-icd.so
  -> PDOCKER_GPU_QUEUE_SOCKET=/run/pdocker-gpu/pdocker-gpu.sock
  -> app-owned files/pdocker-runtime/gpu/pdocker-gpu-executor
  -> Android Vulkan driver
```

The existing `scripts/test/smoke-vulkan-icd-storage-image.sh` is useful as a
host-side ICD/object-transport regression smoke, but it compiles with host
`gcc`, links host `-lvulkan`, starts a repo-local executor path, and sets a
host `VK_ICD_FILENAMES`. It must not be treated as real-device evidence.

## Clean device route

Use the existing Android smoke harness primitives instead of local host Vulkan:

1. Install/start the APK and wait for the app runtime to prepare the sidecars.
   `scripts/android-device-smoke.sh` already has reusable `run_as`,
   `wait_for_socket`, `stage_test_cli`, and `docker_cmd` helpers. The gate must
   wait for both:
   - `files/pdocker-runtime/gpu/pdocker-gpu.sock`
   - `files/pdocker-runtime/lib/pdocker-vulkan-icd.so`
2. Run inside a real glibc guest/container that has a Vulkan loader
   (`libvulkan.so.1`). Do not use the host loader. If no such container is
   available, emit a non-promoting planned-skip artifact.
3. Stage only a tiny smoke client into that container. Preferred order:
   - an already-built aarch64 glibc smoke binary, or
   - source compiled inside the guest only when `cc`, Vulkan headers, and the
     guest loader are already present.
4. Execute the smoke through the container/guest runtime with:

   ```sh
   VK_ICD_FILENAMES=/etc/vulkan/icd.d/pdocker-android.json \
   PDOCKER_GPU_QUEUE_SOCKET=/run/pdocker-gpu/pdocker-gpu.sock \
   PDOCKER_VULKAN_ICD_TRACE_ALLOC=1 \
   PDOCKER_GPU_DISPATCH_PROFILE_LOG=1 \
   <smoke-client>
   ```

   The storage-image smoke should reuse the same workload shape as
   `scripts/test/smoke-vulkan-icd-storage-image.sh`: storage image descriptor,
   `VK_FORMAT_R8G8B8A8_UNORM`, `vkCmdDispatch`, `vkCmdCopyImageToBuffer`, and a
   `storageImageMaxErr` validation line.
5. Collect stdout/stderr, executor logs, and a JSON summary under
   `docs/test/vulkan-icd-device-socket-latest.json` or an immutable
   `docs/test/runs/<run-id>/...` directory.

## Runner

Use `scripts/test/android-vulkan-icd-device-socket-smoke.sh` to generate the
latest artifact. The runner stages the smoke client and ICD into an existing
running container and records a non-promoting `success:false` artifact when a
real guest Vulkan loader, app socket, or running container is unavailable.

Validate generated artifacts with:

```sh
python3 scripts/test/verify-vulkan-icd-device-socket-artifact.py docs/test/vulkan-icd-device-socket-latest.json
```

Use `--allow-planned-skip` only for disconnected or missing-prerequisite runs;
that mode validates artifact shape but never promotes Vulkan passthrough.

## Promotion requirements

A passing artifact must prove all of these facts from the same device run:

- `adb` serial and package `run-as` context are recorded.
- App runtime socket exists at `files/pdocker-runtime/gpu/pdocker-gpu.sock`.
- Container environment used `/etc/vulkan/icd.d/pdocker-android.json`; the host
  `VK_ICD_FILENAMES`/host Vulkan loader was not used.
- The guest ICD logged a bridge response from `pdocker-vulkan-icd`.
- The executor response contains `"executor":"pdocker-gpu-executor"`,
  `"backend_impl":"android_vulkan"`, and `"valid":true`.
- The smoke output contains `storageImageMaxErr` within tolerance.
- No `"backend_affinity":"fallback"` event is accepted as pass evidence.

Native executor self-bench commands such as
`pdocker-gpu-executor --bench-vulkan-storage-image-roundtrip` are useful
preflight, but they are not sufficient: they bypass the glibc Vulkan loader and
ICD.

## Fail-closed cases

Write a non-promoting artifact with `success:false` when any required piece is
missing:

- no connected `adb` device or no `run-as` access;
- app GPU executor socket missing;
- no guest/container with `libvulkan.so.1`;
- smoke compiled or linked against host `-lvulkan`;
- ICD JSON points anywhere other than `/etc/vulkan/icd.d/pdocker-android.json`;
- executor reports fallback, timeout, invalid output, or no Android Vulkan
  event.

This gate is intentionally separate from local host smoke scripts so missing
real-device evidence cannot be promoted by static or host-only tests.

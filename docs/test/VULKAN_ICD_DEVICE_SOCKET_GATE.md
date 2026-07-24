# Vulkan ICD Device Socket Gate

Snapshot date: 2026-07-24.

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

The gate runs two independent guest clients from one device/runtime
generation. The storage-image client proves object/payload transport and
writeback. `tests/device/skydnir-vulkan-p0-smoke.c` proves the remaining generic
P0 control and lifetime paths: executor-backed query pools, synchronization2
submission, queue/device idle, and headless WSI acquire/present/destruction.

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
   PDOCKER_VULKAN_ADVERTISEMENT_SOURCE=executor \
   PDOCKER_VULKAN_ICD_DEBUG=1 \
   PDOCKER_VULKAN_ICD_TRACE_ALLOC=1 \
   PDOCKER_GPU_DISPATCH_PROFILE_LOG=1 \
   <smoke-client>
   ```

   The storage-image smoke should reuse the same workload shape as
   `scripts/test/smoke-vulkan-icd-storage-image.sh`: storage image descriptor,
   `VK_FORMAT_R8G8B8A8_UNORM`, `vkCmdDispatch`, `vkCmdCopyImageToBuffer`, and a
   `storageImageMaxErr` validation line. The generic P0 client must emit exactly
   one `skydnir.vulkan.p0.device.v1` JSON object and must report `advertised`,
   `executed`, and `passed` for query, synchronization2, idle, and WSI. A path
   advertised by the guest ICD but not executed is a failure, not a skip.
5. Collect stdout/stderr, executor logs, and a JSON summary under
   `docs/test/vulkan-icd-device-socket-latest.json` or an immutable
   `docs/test/runs/<run-id>/...` directory.

## Runner

Use `scripts/test/android-vulkan-icd-device-socket-smoke.sh` to generate the
latest artifact. The runner invalidates any prior latest success before device
work, writes every state through a same-directory atomic replacement, and only
promotes a private candidate after the shared verifier accepts it. It stages the
smoke client and current tracked ICD into an existing running container, but
captures and preserves the product-generated ICD manifest and its `api_version`
instead of synthesizing a test manifest. A missing manifest, real guest Vulkan
loader, app socket, or running container produces a non-promoting
`success:false` artifact.

A process-wide single-writer lock, independent of output-path aliases, prevents
concurrent runs from publishing out of order. An exit trap converts every
unexpected command failure into a terminal
`success:false` artifact instead of leaving `in_progress` behind.

The runner snapshots the exact runner, P0 source, generated storage source, and
ICD bytes before staging. It records a UUID run ID, current Git commit, SHA-256
hashes for those snapshots and their guest copies, the product manifest and
its post-copy guest readback, the installed APK and GPU executor hashes, and the
inspected container image identity. Promotion rejects source-to-guest,
manifest-readback, inspect/image-identity, or current-checkout mismatches.

Every ADB control call has a finite host watchdog. The three long guest
operations also run below an in-container hard timeout in a dedicated `setsid`
process group carrying a per-run marker. Exit cleanup identifies marked groups
through `/proc` and terminates the entire group; the next locked run reaps stale
groups before testing. A cleanup failure prevents successful promotion, so a
lost ADB client cannot silently overlap an old compile or workload with a new
gate. The workload watchdog defaults to 180 seconds with
a 10-second host kill-after interval; the
control timeout defaults to 30 seconds. They can be configured with
`SKYDNIR_VULKAN_ICD_TIMEOUT_SECONDS`,
`SKYDNIR_VULKAN_ICD_TIMEOUT_KILL_AFTER_SECONDS`, and
`SKYDNIR_VULKAN_ICD_CONTROL_TIMEOUT_SECONDS`.

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
- Container environment used `/etc/vulkan/icd.d/pdocker-android.json` and
  `PDOCKER_VULKAN_ADVERTISEMENT_SOURCE=executor`; the host
  `VK_ICD_FILENAMES`/host Vulkan loader was not used.
- The guest ICD logged a bridge response from `pdocker-vulkan-icd`.
- The direct preflight has exactly one strict JSON result with the production
  executor/API/ABI, `"backend_impl":"android_vulkan"` with same-API affinity,
  storage-image transport and kernel, `valid:true`, and bounded numerical
  error. Substring matches do not
  count.
- The storage-image smoke output contains `storageImageMaxErr` within tolerance.
- The generic P0 report uses schema `skydnir.vulkan.p0.device.v1`, has
  `success:true`, and records query, synchronization2, idle, and WSI as all
  advertised, executed, and passed.
- The query path creates separate executor-backed pools for host reset and
  command-buffer reset. It writes independent timestamps to both pools and
  retrieves independent positive-availability results, so a working command
  reset cannot mask a no-op host reset; an HTTP/health response or host-only mock
  is not substitute evidence.
- The WSI path creates a headless surface and swapchain, uses a finite acquire
  timeout, records the standard `UNDEFINED -> PRESENT_SRC_KHR` image transition,
  presents, waits idle, and destroys the ownership graph in the same run.
- P0 stderr must contain strictly parsed executor response objects for both
  query-pool creates, host reset, both result reads, queue/device idle, and at
  least four completed V5 frames covering query submit and WSI synchronization.
  Required responses must be `valid:true` with integer `VK_SUCCESS`; query
  pool IDs must preserve create/reset/read identity. Every V5 terminal must say
  `execution_implemented:true`, carry a unique positive submit ID, and be
  enclosed by its correlated begin and successful end lifecycle events. A
  client-side `success:true` self-report or a stage-name substring is not
  executor evidence.
- Every captured executor response is structurally valid and has a successful
  integer `VkResult`; an unrelated failing cleanup/control response also blocks
  promotion.
- Each timeout field is exactly one canonical non-timeout record matching the
  configured values; appended or contradictory records are rejected.
- All successful lane records are contradiction-free: `unsupported:false`,
  `skipped:false`, integer `VkResult` values, empty error step, and zero
  process exit status.
- Product/guest manifest bytes, run state, watchdog outcomes, Git commit, and
  current input hashes must pass provenance validation.
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
  event;
- the P0 client reports a missing advertised path, skips an advertised operation,
  emits malformed/multiple/contradictory JSON objects, lacks required executor
  response stages, times out, or fails query/sync2/idle/WSI;
- the product manifest is absent or altered beyond deterministic serialization,
  the staged inputs do not match their hashes/current checkout, or a stale
  `success:true` artifact survives a new run.

This gate is intentionally separate from local host smoke scripts so missing
real-device evidence cannot be promoted by static or host-only tests.

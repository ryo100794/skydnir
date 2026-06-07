# llama.cpp GPU Bridge Next Steps

Snapshot date: 2026-06-03.

This document is the handoff plan for continuing the llama.cpp GPU bridge work
with a smaller or faster coding model.  It assumes the repository is on or
after commit `14b14fc` (`Add SPIR-V dataflow comparison tool`) and that
llama.cpp itself remains unmodified.

## Current Ground Truth

The current implementation is a Skydnir-owned glibc Vulkan ICD bridge plus an
APK-owned Android Vulkan executor.  The container still owns llama.cpp model
loading, graph construction, sampling, and HTTP serving.  The bridge only
lowers selected Vulkan buffer/descriptor/dispatch work to Android Vulkan.

Confirmed facts:

| Area | Current result | Evidence |
|---|---|---|
| `ngl=0` default route | Required correctness passes | `docs/test/llama-gpu-default-oracle-match-ngl0-20260509.json` |
| unsafe SPIR-V materialization | Disabled by default | commit `02619fd` |
| zero-layer small multiply shader | CPU oracle matches default non-materialized hash | `0x11d5243c43b23a7b`, `mismatch_count=0` |
| `ngl=1` small add shader | CPU oracle matches | `0x11c0523df6c795b8`, `mismatch_count=0` |
| `ngl=1` RoPE/Yarn shader | CPU oracle executes and matches | `0xac41e8033a67af4a`, `docs/test/llama-gpu-ngl1-rope-yarn-oracle-20260509.json` |
| `ngl=1` RMSNorm shader | CPU oracle executes and matches | `0xf2f988b94bd3e0dc`, `docs/test/llama-gpu-ngl1-rms-norm-oracle-20260509.json` |
| `ngl=1` Q6_K/final-projection shader | Row-indexed writeback verified; workgroup shape and native reduction sum clear; final output still mismatches | `docs/test/llama-gpu-ngl1-q6-row-provenance-20260519.json`, `blocker_class=native-q6-device-execution-or-final-store` |
| current device readiness | Heavy compare is memory-gated | readiness requires sufficient `MemAvailable`; low Android zram `SwapFree` is advisory unless a strict swap gate is explicitly configured |
| 2026-05-20 Q6_K workflow | Device workflow reaches the known Q6_K blocker again; create-timeout race is no longer the blocker | `docs/test/llama-gpu-q6k-adb41503-20260520T110352Z.json` (ignored runtime evidence), workflow `classification=q6-native-device-execution-or-final-store` |
| 2026-05-23 Q6 WorkgroupSize lane | Device is reachable and Q6 dispatch evidence is present, but the effective Q6 WorkgroupSize evidence is still not visible in the oracle record | ADB `192.168.179.26:34761`; `docs/test/llama-gpu-readiness-adb34761-latest.json`; `docs/test/llama-gpu-ngl1-q6-workgroup-legalized-adb34761-20260523T084956Z.json`; `docs/test/llama-gpu-ngl1-q6-workgroup-composite-adb34761-20260523T091428Z.json` |
| commit `ac40e49` safe-kernel lane | `ngl=1` prompt/Q6 oracle/writeback correctness clears only under bridge-owned Q6 safe-kernel substitution | `docs/test/llama-gpu-ngl1-q6-safe-kernel-adb44443-20260523T112715Z.json`; classification `q6-workgroup-cleared-and-oracle-match`; safe-kernel hash `0x7ec0292e948c9b41` for source hash `0x1bf751845c5dce75` |
| 2026-05-23 SPIR-V structural lane | Safe Q6 module is now analyzed by static dataflow/origin tooling; native Q6 comparison is blocked until a real native `.spv` dump is collected from device | commits `59b0a4e`, `ab3b24b`, `e42ce9e`, `14b14fc`; `docs/test/spirv-q6k-safe-current/q6k-safe.analysis.json`; `scripts/analyze-spirv.py`; `scripts/compare-spirv-dataflow.py`; `scripts/verify-spirv-probe-manifest.py` |
| 2026-05-23 valid-module probe lane | Native Q6 no-op replay reaches the known wrong-output blocker without changing llama.cpp/model/prompt, and executable Q6 debug-SSBO write probes are generated/validated locally for the next device run | commits `139fa83`, `5956a41`, `8515829`; `docs/test/llama-gpu-ngl1-q6-noop-probe-strictid-adb39419-20260523T230924Z.json`; `scripts/prepare-q6k-noop-probe.sh --probe-writes`; effective probe hash `0xfd2949c11ffa33e9` |
| 2026-05-24 Q6 write-probe lane | Native Q6 valid-module replay now emits a 10-record debug SSBO split across tail/full partial, reduction, post-reduction, and final stores.  Device evidence shows the full branch executes partial/reduction/final records and writeback matches dispatch samples; post-reduction candidate stores are not dynamically executed for this prompt.  Compare now maps the instrumented probe hash back to the original Q6 source hash through the probe manifest env, so the diagnostics classify this as `q6-probe-writeback-cleared-oracle-missing` instead of silently losing the Q6 event.  Prompt sanity still fails (`" Marvel"` for `2+3=`), so Q6 writeback is no longer the first suspected boundary for this run. | local artifacts `docs/test/llama-gpu-ngl1-q6-write10-probe-adb42493-20260524T005341Z.json`, `docs/test/llama-gpu-ngl1-q6-write10-classified2-adb40309-20260524T021223Z.json` (ignored runtime evidence); parsed summary `pass`; effective probe hash `0x3f14f34b0679040e`; original/source hash `0x1bf751845c5dce75` |
| 2026-05-24 strict passthrough/object-graph lane | Strict passthrough now preserves descriptor/push/specialization bytes by default and no longer hard-stops on local-size disagreement.  Android Vulkan object handles still cannot be copied across the glibc/Bionic process boundary: the executor reconstructs an equivalent Android `VkDeviceMemory`/`VkBuffer`/descriptor object graph from IDs, offsets, ranges, and shared backing fds.  Q6 WorkgroupSize literal lowering now clears the local-size blocker on device, but prompt sanity still fails.  Static inspection shows the native Q6 module also uses a specialized `BuiltIn WorkgroupSize` value in reduction control flow; the next compatibility lane explicitly materializes specialization constants after the LocalSize lowering so Android drivers cannot execute code derived from stale default `gl_WorkGroupSize`. | host tests `tests/test_gpu_abi_contract.py tests/test_llama_gpu_env_parity.py`; artifacts `docs/test/llama-gpu-ngl1-q6-workgroup-legalized-adb34929-20260524T045343Z.json`, `docs/test/llama-gpu-ngl1-q6-workgroup-native-legalized-adb34929-20260524T050109Z.json`; source hash `0x1bf751845c5dce75`, effective localized hash `0xe38f6a6a906d765c` |
| 2026-05-25 static-proof lane | New runtime collection is not the default next step.  Q6 store-index diagnostics now fail closed unless dispatch dimensions, specialization constants, store-window bounds, and every layout sample's decoded store coordinates are present.  Missing or column-0-only store-index evidence is classified as oracle/model-incomplete, not as a Vulkan passthrough/native shader failure. | `app/src/main/cpp/pdocker_gpu_executor.c`; `scripts/android-llama-gpu-compare.sh`; `scripts/verify-llama-gpu-artifact.py`; host tests `tests.test_gpu_abi_contract tests.test_llama_gpu_artifact_verifier` |
| 2026-05-26 final-store boundary desk lane | Host/static P0 desk checks can now distinguish Q6 final-store value failure from executor writeback failure before a fresh device run.  The compare artifact records `q6_final_store_boundary` by joining final-store trace records, output-layout samples, and row-indexed writeback samples; the verifier classifies `q6-native-final-store` or `q6-writeback-mismatch` fail-closed without changing llama.cpp, Dockerfiles, models, or prompts. | `scripts/android-llama-gpu-compare.sh`; `scripts/verify-llama-gpu-artifact.py`; `tests.test_llama_gpu_artifact_verifier`; host gate `tests.test_gpu_abi_contract tests.test_llama_gpu_artifact_verifier tests.test_llama_gpu_q6k_workflow tests.test_llama_gpu_env_parity` |
| 2026-05-31 final-store sample lane | Executor-side Q6 binding-2 sampling now appends final-store `output_index` values extracted from the debug probe SSBO before emitting f32 dispatch/writeback evidence.  The latest device run joined a final-store sample and classified the remaining failure as `native-final-store-mismatch`: `final_store_value_f32 == fd_after_writeback == 3.22796106`, expected `6.38452625`, with alias/writeback cleared.  This narrows the next target to native Q6 SPIR-V execution/final-store semantics, not executor writeback. | `app/src/main/cpp/pdocker_gpu_executor.c`; `scripts/android-llama-gpu-compare.sh`; `docs/test/llama-gpu-ngl1-q6-final-store-samples-adb46015-20260531T051758Z.json` (local evidence); host gate `tests.test_gpu_abi_contract tests.test_llama_gpu_artifact_verifier`; APK run on `192.168.179.21:46015` |
| 2026-05-31 final-store provenance lane | Fresh run with the installed APK on `192.168.0.212:32925` preserved final-store layout provenance and split the failure to `native-final-store-mismatch`: the debug SSBO final-store value matches post-writeback, while both differ from the CPU oracle.  The verifier now accepts latest-event identity by dispatch id or by matching source/effective SPIR-V compact hashes because executor compare events can omit `dispatch_id`.  The offline effective-SPIR-V reconstructor now mirrors the executor's storage16-to-storage8 lowering and reproduces the observed effective hash `0x72f4a362b00221fd` from the instrumented Q6 source hash `0xd2d7fbedceb5a8a6`. | `scripts/reconstruct-q6-effective-spirv.py`; `scripts/verify-llama-gpu-artifact.py`; `tests.test_gpu_abi_contract`; `tests.test_llama_gpu_artifact_verifier`; local evidence `docs/test/llama-gpu-ngl1-q6-final-store-provenance-192_168_0_212_32925-20260531T093549Z.json` |
| 2026-05-31 Q6 final-store barrier lane | Static analysis of the effective Q6 module shows the final store reads Workgroup `%143` at lane0 immediately after the reduction loop.  A hash-gated compatibility lowering now inserts one additional Workgroup-memory `OpControlBarrier` after the reduction loop convergence and before the lane0 final-store branch.  This keeps descriptor, buffer, push, specialization, dispatch, model, prompt, and llama.cpp bytes unchanged; it only tightens shader-side workgroup-memory visibility before final-store. | `app/src/main/cpp/pdocker_gpu_executor.c`; `scripts/reconstruct-q6-effective-spirv.py`; packaged `libpdockergpuexecutor.so`; host gate `tests.test_gpu_abi_contract tests.test_llama_gpu_artifact_verifier tests.test_termport_docker_api_contract`; APK build `:app:assembleCompatDebug` |
| 2026-06-03 Vulkan graphics V6.1 P0-P6 preflight lane | Producer commit `9d6e724` has completed V6.1 serialization through the attachment table and command table.  The executor now validates/describes V6.1 frames, runs an explicit `vulkan-graphics-v6-replay-preflight`, and accepts only validated no-op frames as implemented.  Non-empty graphics replay now advances through queue submit/fence wait and fails closed at attachment writeback until Android Vulkan readback is implemented. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract` |
| 2026-06-03 Vulkan graphics pipeline-state fail-closed lane | The container ICD refuses to promote graphics pipelines that depend on static state not serialized into the current replay ABI: blend/logic-op/blend constants/non-RGBA write masks, and non-dynamic viewport/scissor.  The earlier depth/stencil static-state gap is superseded by the V6.3 depth/stencil state lane.  This prevents executor P6 from reconstructing guessed defaults when real Android Vulkan replay is added. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host test `tests.test_gpu_abi_contract` |
| 2026-06-03 Vulkan graphics pipeline-materialization lane | Executor P6 now advances past generic command-recording refusal by materializing the serialized graphics pipeline object graph first: shader fd hash revalidation, entry-name copy, push-constant layout reconstruction, vertex input state, dynamic-rendering color formats, viewport/scissor dynamic state, and Android `vkCreateGraphicsPipelines`.  Attachment image materialization and command-buffer replay still fail closed after pipeline materialization; no success or benchmark claim is promoted from this partial P6 step. | `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract` |
| 2026-06-03 Vulkan graphics attachment-materialization lane | Executor P6 now materializes the serialized attachment image graph before command-buffer replay: it reuses the V5 image/image-view/sampler materializer with the V6.1 resource tables, checks color-attachment role, image-view mapping, `VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT`, attachment format parity, supported layouts, and fails closed for staged `LOAD` attachments until upload/replay/writeback is implemented.  Queue submission/writeback remains non-promoting. | `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract` |
| 2026-06-03 Vulkan graphics command-recording lane | Executor P6 now allocates a host graphics command buffer and records the supported V6.1 subset: dynamic rendering begin/end, graphics pipeline bind, viewport/scissor dynamic state, and push constants.  It still fails closed before queue submit/writeback and rejects vertex/index/descriptor/barrier/draw paths that are not yet materialized into host objects. | `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract` |
| 2026-06-03 Vulkan graphics vertex-buffer replay lane | Executor P6 now materializes read-only vertex buffers from V6.1 resource metadata before command recording.  It reads only the serialized vertex binding ranges from host-fd-backed memory resources, creates compact host-visible `VK_BUFFER_USAGE_VERTEX_BUFFER_BIT` buffers, records `vkCmdBindVertexBuffers`, and records unindexed `vkCmdDraw`.  Descriptors, explicit barriers, depth/stencil, and MSAA/resolve remain fail-closed. | `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract` |
| 2026-06-04 Vulkan graphics queue-submit lane | Executor P6 now preserves a recorded graphics command buffer, submits it on the Android graphics queue, and waits on a fence with `PDOCKER_GPU_GRAPHICS_SUBMIT_TIMEOUT_MS`.  This proves the bridge can reach real host Vulkan execution without promoting full correctness until readback evidence exists. | `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract` |
| 2026-06-04 Vulkan graphics attachment-writeback lane | Executor P6 now marks stored color attachments for writeback, transitions rendered images for host readback, copies optimal-tiled attachments through staging with `vkCmdCopyImageToBuffer`, waits for queue completion, and writes attachment memory back to the shared backing fd.  This closes the previous attachment-writeback gate for the currently supported unindexed draw subset; image descriptors, write descriptors, explicit barriers, depth/stencil, MSAA/resolve, and broader synchronization remain fail-closed. | `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract`; native build `scripts/build-native-android-ndk.sh`; APK gate `:app:assembleCompatDebug` |
| 2026-06-04 Vulkan graphics read-only descriptor lane | Executor P6 now replays read-only buffer descriptors plus non-staged sampled-image/sampler descriptors for the supported graphics subset.  It reconstructs descriptor set layouts from serialized binding metadata, allocates/updates Android descriptor sets, binds them during command replay, validates sampled-image layouts, and transitions read-only image descriptors to shader-read layouts before draw.  Input attachments, write descriptors outside storage buffers/images, staged/optimal texture upload, explicit barriers, depth/stencil, MSAA/resolve, and broader synchronization remain fail-closed. | `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract` |
| 2026-06-04 Vulkan graphics staged sampled-image upload lane | Executor P6 now accepts optimal-tiled read-only sampled-image/combined-image-sampler descriptors when their backing memory is fd-backed and their image-view range is a bounded color mip/layer range.  It keeps the existing image materializer staging buffer, records host-to-transfer and image transfer-dst barriers, copies staged fd-backed image bytes into the Android image with `vkCmdCopyBufferToImage`, clears `upload_pending`, then transitions the descriptor image to the serialized shader-read layout before draw.  Input attachments, write descriptors outside storage buffers/images, copy+draw mixed submit semantics, explicit user barriers, depth/stencil, MSAA/resolve, and broader synchronization remain fail-closed. | `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract` |
| 2026-06-04 Vulkan graphics descriptor preflight reachability lane | Executor P6 no longer rejects every non-empty descriptor table before command-specific validation.  Descriptor replay now reaches the per-command checks and materialization path added by the buffer/image descriptor lanes; only unsupported descriptor classes such as input attachments and unsupported write descriptor classes remain fail-closed. | `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract` |
| 2026-06-04 Vulkan graphics explicit barrier lane | V6.1 now carries explicit image, global memory, and buffer barriers as separate extension tables.  The ICD serializes `vkCmdPipelineBarrier` / `vkCmdPipelineBarrier2` memory/buffer/image barriers into ordered graphics commands, and the executor validates buffer/subresource ranges, rejects queue-family ownership transfers, materializes buffer barrier ranges, and records matching Android `vkCmdPipelineBarrier` calls in command order.  Input attachments, unsupported write descriptor classes, depth/stencil, MSAA/resolve, sync2-only 64-bit masks, and broader synchronization remain fail-closed. | `app/src/main/cpp/pdocker_gpu_abi.h`; `docker-proot-setup/src/gpu/pdocker_gpu_abi.h`; `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `app/src/main/cpp/pdocker_gpu_executor.c`; host tests `tests.test_gpu_abi_contract tests.test_llama_gpu_env_parity`; native builds `scripts/build-native-android-ndk.sh`, `scripts/build-gpu-shim.sh`; APK gate `:app:assembleCompatDebug` |
| 2026-06-04 Vulkan graphics instanced-draw lane | Executor P6 now allows serialized `vkCmdDraw` and `vkCmdDrawIndexed` instance counts and first-instance values to pass through to Android Vulkan instead of rejecting all instanced draws in preflight.  The ABI already carried these fields and replay already passed them to Vulkan; this step removes the stale fail-closed gate while keeping buffer range validation on the serialized vertex/index resources.  Input attachments, unsupported write descriptor classes, depth/stencil, MSAA/resolve, sync2-only 64-bit masks, and broader synchronization remain fail-closed. | `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract` |
| 2026-06-04 Vulkan graphics non-depth dynamic-state lane | ICD and executor now normalize graphics dynamic-state masks instead of shifting raw `VkDynamicState` enum values, so extended dynamic states with large enum values are preserved.  The supported replay subset initially included viewport, scissor, line width, cull mode, front face, and primitive topology.  The executor validates each dynamic-state payload shape, enables `VK_EXT_extended_dynamic_state` when the Android device exposes it, loads the matching `vkCmdSet*` entry points, and records the supported commands into the Android graphics command buffer.  Later lanes extend this same contract to depth/stencil state, depth bounds, blend constants, and stencil masks/references. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `app/src/main/cpp/pdocker_gpu_executor.c`; host tests `tests.test_gpu_abi_contract tests.test_llama_gpu_env_parity`; native builds `scripts/build-native-android-ndk.sh`, `scripts/build-gpu-shim.sh` |
| 2026-06-04 Vulkan graphics writable storage-buffer descriptor lane | Executor P6 now accepts writable `VK_DESCRIPTOR_TYPE_STORAGE_BUFFER` graphics descriptors for the supported subset.  It materializes the referenced fd-backed buffer ranges as host-visible Android Vulkan storage buffers, records a shader-write to host-read barrier before queue completion, writes the changed descriptor ranges back to the shared backing fd after submit, and emits `vulkan-graphics-v6-storage-buffer-writeback` evidence.  Input attachments, depth/stencil, MSAA/resolve, copy+draw mixed submit semantics, and broader synchronization remain fail-closed. | `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract` |
| 2026-06-04 Vulkan graphics storage-image descriptor lane | Executor P6 now accepts `VK_DESCRIPTOR_TYPE_STORAGE_IMAGE` descriptors for the supported graphics subset when they use `VK_IMAGE_LAYOUT_GENERAL`, a bounded color image-view range, and an fd-backed image resource.  Storage images are conservatively treated as potentially writable, transitioned with shader read/write access, copied back through the existing image writeback path after queue completion, and written to the shared backing fd.  Input attachments, depth/stencil, MSAA/resolve, copy+draw mixed submit semantics, and broader synchronization remain fail-closed. | `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract` |
| 2026-06-05 Vulkan graphics copy-range refactor lane | Executor P6 now centralizes bounded color image-view range validation and copy/writeback range merging through `vulkan_graphics_merge_attachment_copy_range`.  This removes duplicate attachment-vs-descriptor range checks and makes writable storage-image descriptors use the same bounded writeback range contract as stored color attachments.  This cleanup slice originally did not widen the supported descriptor, depth/stencil, MSAA/resolve, or mixed-submit contract; the later depth/stencil writeback lane reuses the generalized helper. | `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract`; native build `scripts/build-native-android-ndk.sh` |
| 2026-06-05 Vulkan graphics V6.2 specialization metadata lane | Graphics shader specialization is no longer a generic fail-closed gap.  V6.2 adds an append-only specialization map-entry table keyed by serialized shader-stage index.  The ICD captures `VkSpecializationInfo`, appends per-stage specialization data into the existing shader-stage payload bytes, emits V6.2 only when specialization exists, and the executor validates/reconstructs `VkSpecializationInfo` before `vkCreateGraphicsPipelines`.  This preserves specialization bytes and metadata without changing V6.0/V6.1 layouts.  Broader graphics gaps such as depth/stencil, MSAA/resolve, mixed submit semantics, and unsupported descriptor classes remain fail-closed. | `app/src/main/cpp/pdocker_gpu_abi.h`; `docker-proot-setup/src/gpu/pdocker_gpu_abi.h`; `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract`; native builds `scripts/build-native-android-ndk.sh`, `scripts/build-gpu-shim.sh` |
| 2026-06-05 Vulkan graphics input-attachment descriptor lane | Input attachments are no longer an unsupported descriptor class for the supported graphics replay subset.  The executor now treats `VK_DESCRIPTOR_TYPE_INPUT_ATTACHMENT` as an image-view-only descriptor, validates read-only/general input layouts, includes input attachments in descriptor-set layouts and pools, skips unnecessary buffer materialization, and updates Android descriptor sets with the serialized image view and layout.  This only widens descriptor replay; depth/stencil input attachments, MSAA/resolve, mixed submit semantics, and broader synchronization still remain fail-closed. | `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract`; native build `scripts/build-native-android-ndk.sh` |
| 2026-06-05 Vulkan graphics V6.3 depth/stencil state lane | Static graphics depth/stencil pipeline state is now serialized as append-only V6.3 metadata instead of being collapsed into an unsupported flag.  The ICD captures depth-test/write/bounds/stencil enables, compare ops, front/back stencil ops/masks/references, and depth bounds; the executor validates the V6.3 table and reconstructs `VkPipelineDepthStencilStateCreateInfo` before Android graphics pipeline creation.  MSAA/resolve, mixed submit semantics, and broader synchronization remain fail-closed. | `app/src/main/cpp/pdocker_gpu_abi.h`; `docker-proot-setup/src/gpu/pdocker_gpu_abi.h`; `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract`; native builds `scripts/build-native-android-ndk.sh`, `scripts/build-gpu-shim.sh` |
| 2026-06-05 Vulkan graphics depth/stencil disabled-state attachment lane | The executor now replays depth/stencil attachments for the conservative subset.  It validates depth/stencil attachment layouts and image usage, materializes the image view, records depth/stencil dynamic-rendering attachment pointers, and creates a matching Android graphics pipeline with depth/stencil formats.  Static depth/stencil pipeline state is supplied by V6.3 when enabled; MSAA/resolve, mixed submit semantics, and broader synchronization remain fail-closed. | `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract`; native build `scripts/build-native-android-ndk.sh` |
| 2026-06-05 Vulkan graphics depth/stencil attachment writeback lane | Executor P6 now accepts `VK_ATTACHMENT_STORE_OP_STORE` for depth and stencil attachments in the supported graphics subset.  Attachment copy-range validation is shared across color/depth/stencil roles, writeback-needed state is role-independent, and post-draw barriers now use aspect-specific access/stage masks before copying depth/stencil aspects through the existing staging/fd writeback path.  MSAA/resolve, mixed submit semantics, unsupported write descriptor classes, cross-family ownership transfer, and broader synchronization remain fail-closed. | `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract`; native build `scripts/build-native-android-ndk.sh` |
| 2026-06-05 Vulkan graphics depth/stencil writeback hardening lane | Depth/stencil attachment writeback now uses aspect-aware conservative bytes-per-pixel and staging offset/size helpers for color, depth, and stencil aspects.  The executor rejects unsupported depth/stencil formats or copy regions whose `bufferOffset + copySize` would exceed staging memory, keeping combined depth+stencil dual-aspect writeback fail-closed until it has an explicit layout contract. | `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract`; native build `scripts/build-native-android-ndk.sh` |
| 2026-06-05 Vulkan graphics same-family queue-barrier lane | Graphics buffer/image barriers no longer reject every serialized queue-family index.  The executor accepts `IGNORED/IGNORED` and same-family `src == dst` barriers, then normalizes replayed Android Vulkan barriers to `VK_QUEUE_FAMILY_IGNORED` because container queue-family indices are not assumed to match Android queue-family indices.  True cross-family ownership transfer remains fail-closed. | `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract`; native build `scripts/build-native-android-ndk.sh` |
| 2026-06-05 Vulkan graphics depth/stencil dynamic-state lane | ICD/executor dynamic-state bit mappings now include depth-bias values, blend constants, depth bounds, stencil compare/write/reference masks, depth test/write/compare state, stencil test enable, and stencil op.  The executor validates each serialized payload shape, replays core `vkCmdSetDepthBias`, `vkCmdSetBlendConstants`, `vkCmdSetDepthBounds`, `vkCmdSetStencil*Mask`, and `vkCmdSetStencilReference`, and loads EXT/core aliases for `vkCmdSetDepthTestEnable`, `vkCmdSetDepthWriteEnable`, `vkCmdSetDepthCompareOp`, `vkCmdSetStencilTestEnable`, and `vkCmdSetStencilOp`.  `depthBiasEnable` itself is supplied by the V6.5 static pipeline-state lane. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract`; native builds `scripts/build-native-android-ndk.sh`, `scripts/build-gpu-shim.sh` |
| 2026-06-05 Vulkan graphics V6.4 resolve-attachment lane | Dynamic-rendering resolve attachments are now represented by append-only V6.4 metadata instead of being guessed from the base attachment table.  The ICD captures resolve image view, resolve mode, and resolve layout; the executor validates the V6.4 table, reconstructs `VkRenderingAttachmentInfo.resolveMode/resolveImageView/resolveImageLayout`, permits Vulkan multisample image creation, and routes stored resolved output writeback through the single-sample resolve target.  Unresolved MSAA store/readback, true cross-family ownership transfer, mixed submit semantics, and broader synchronization remain fail-closed. | `app/src/main/cpp/pdocker_gpu_abi.h`; `docker-proot-setup/src/gpu/pdocker_gpu_abi.h`; `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract`; native build `scripts/build-native-android-ndk.sh` |
| 2026-06-05 Vulkan graphics V6.5 static pipeline-state lane | Static input-assembly/rasterization state is now serialized as append-only V6.5 metadata instead of being rejected or defaulted.  The ICD captures primitive restart, depth clamp, rasterizer discard, depth-bias enable/factors, and static line width; the executor validates the V6.5 table and reconstructs `VkPipelineInputAssemblyStateCreateInfo` and `VkPipelineRasterizationStateCreateInfo` before Android graphics pipeline creation.  Non-dynamic viewport/scissor, unresolved MSAA store/readback, mixed submit semantics, and broader synchronization remain fail-closed. | `app/src/main/cpp/pdocker_gpu_abi.h`; `docker-proot-setup/src/gpu/pdocker_gpu_abi.h`; `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract` |
| 2026-06-05 Vulkan graphics V6.6 color-blend state lane | Static color-blend pipeline state is now serialized as append-only V6.6 metadata instead of being rejected or defaulted.  The ICD captures logic-op enable/op, static blend constants, per-attachment blend enable/factors/ops, and color write masks; the executor validates the V6.6 tables and reconstructs `VkPipelineColorBlendStateCreateInfo` before Android graphics pipeline creation.  Static viewport/scissor is handled by the later V6.7 lane; unresolved MSAA store/readback, mixed submit semantics, and broader synchronization remain fail-closed. | `app/src/main/cpp/pdocker_gpu_abi.h`; `docker-proot-setup/src/gpu/pdocker_gpu_abi.h`; `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract` |
| 2026-06-05 Vulkan graphics V6.7 static viewport/scissor lane | Non-dynamic viewport/scissor pipeline state is now serialized as append-only V6.7 metadata instead of being rejected or defaulted.  The ICD captures per-pipeline viewport/scissor counts plus static `VkViewport` and `VkRect2D` arrays when the states are not dynamic; the executor validates the V6.7 tables, preserves dynamic viewport/scissor behavior, and reconstructs `VkPipelineViewportStateCreateInfo` with static arrays before Android graphics pipeline creation.  Unresolved MSAA store/readback, mixed submit semantics, render-pass compatibility, indirect draw, and broader synchronization remain fail-closed. | `app/src/main/cpp/pdocker_gpu_abi.h`; `docker-proot-setup/src/gpu/pdocker_gpu_abi.h`; `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `app/src/main/cpp/pdocker_gpu_executor.c`; host tests `tests.test_gpu_abi_contract tests.test_llama_gpu_env_parity`; native builds `scripts/build-native-android-ndk.sh`, `scripts/build-gpu-shim.sh` |

| 2026-06-06 Vulkan secondary command-buffer concat lane | The ICD no longer rejects every `vkCmdExecuteCommands` call after superficial validation.  It tracks command-buffer level at allocation time and safely appends secondary command-buffer records into the primary command stream when the secondary is complete, not already marked unsupported, and all recorded table counts fit.  Copy/dispatch/graphics command indices are rebased, graphics metadata indices are rebased, and `vkCmdUpdateBuffer` payload ownership is duplicated to avoid double-free.  Secondary inheritance rendering state, indirect draw ABI transport, unresolved MSAA store/readback, mixed submit semantics, and broader synchronization remain fail-closed. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host tests `tests.test_gpu_abi_contract tests.test_llama_gpu_env_parity`; native build `scripts/build-gpu-shim.sh`; APK gate `:app:assembleCompatDebug`; device install/monkey smoke |
| 2026-06-06 Vulkan graphics V6.8 indirect-draw lane | Indirect graphics draw metadata is now serialized as append-only V6.8 metadata instead of being rejected as an unsupported draw path.  The ICD records `vkCmdDrawIndirect`, `vkCmdDrawIndexedIndirect`, and count-buffer variants as command-indexed metadata, validates indirect/count buffer ranges, and includes the referenced indirect/count buffers in the fd-backed resource table.  The executor validates the V6.8 table, materializes indirect buffers with `VK_BUFFER_USAGE_INDIRECT_BUFFER_BIT`, binds indexed-draw index buffers conservatively, and replays the matching Android `vkCmdDraw*Indirect*` command when the required entry point is available.  Secondary inheritance rendering state, unresolved MSAA store/readback, mixed submit semantics, unsupported indirect-count entry points, and broader synchronization remain fail-closed. | `app/src/main/cpp/pdocker_gpu_abi.h`; `docker-proot-setup/src/gpu/pdocker_gpu_abi.h`; `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `app/src/main/cpp/pdocker_gpu_executor.c`; host tests `tests.test_gpu_abi_contract tests.test_llama_gpu_env_parity`; native builds `scripts/build-native-android-ndk.sh`, `scripts/build-gpu-shim.sh`; APK gates `:app:verifyPackagedPayloadFresh :app:assembleCompatDebug`; payload check `scripts/verify-native-payloads.py` |
| 2026-06-06 Vulkan secondary command-buffer inheritance lane | The ICD no longer drops `VkCommandBufferBeginInfo::pInheritanceInfo` for secondary command buffers.  It records a conservative inherited rendering context for single-subpass render-pass inheritance or dynamic-rendering inheritance and keeps occlusion queries, query flags, pipeline statistics, multiview, rendering flags, and unknown inheritance pNext structs fail-closed.  This lets secondary command buffers whose draws rely on the parent rendering scope survive producer-side validation before `vkCmdExecuteCommands` concatenates them into the primary stream.  Executor-side dynamic rendering flags/multiview, unresolved MSAA store/readback, mixed submit semantics, and broader synchronization remain fail-closed. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host tests `tests.test_gpu_abi_contract tests.test_llama_gpu_env_parity`; native build `scripts/build-gpu-shim.sh` |
| 2026-06-06 Vulkan graphics optimal-image upload lane | Executor P6 now routes optimal-tiled attachment `LOAD` and read-only sampled/combined/input image descriptors through the same fd-backed staging upload path instead of accepting only storage-image upload ranges.  Attachment `LOAD` registers the attachment view range before command recording, uploads pending staged bytes before `vkCmdBeginRendering`, and then lets the normal attachment layout barrier move the image into the serialized attachment layout.  Descriptor uploads still remain bounded to a single color-aspect mip/layer range; depth/stencil sampled descriptors, ambiguous multi-range image views, copy+draw mixed submit semantics, unresolved MSAA store/readback, and broader synchronization remain fail-closed. | `app/src/main/cpp/pdocker_gpu_executor.c`; host tests `tests.test_gpu_abi_contract tests.test_llama_gpu_env_parity`; native build `scripts/build-native-android-ndk.sh` |
| 2026-06-06 Vulkan graphics uint8-index lane | `VK_EXT_index_type_uint8` is now a strict advertised-capability contract instead of an unchecked replay assumption.  The Android executor queries and enables `VkPhysicalDeviceIndexTypeUint8FeaturesEXT` only when the host driver exposes `VK_EXT_index_type_uint8`; the producer ICD advertises/fills/validates the extension and feature only from executor advertisement caps.  Graphics indexed replay now accepts `VK_INDEX_TYPE_UINT8_EXT` with one-byte stride and fails closed if a serialized uint8-index draw reaches an executor without the feature enabled.  This widens generic Vulkan graphics pass-through without changing llama.cpp, Dockerfiles, prompts, or model bytes. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `app/src/main/cpp/pdocker_gpu_executor.c`; host tests `tests.test_gpu_abi_contract tests.test_llama_gpu_env_parity` |
| 2026-06-06 Vulkan graphics bounded mixed-submit lane | The producer ICD no longer rejects every submit that mixes transfer/layout commands with graphics replay.  It now statically plans the command-op order, executes host-side transfer/layout operations before the first graphics draw and after the last graphics draw, then submits the serialized graphics frame in between.  Transfers interleaved between graphics draws, dispatch+graphics mixing, and broader synchronization remain fail-closed so command ordering is not guessed. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host test `tests.test_gpu_abi_contract` |
| 2026-06-06 Vulkan graphics V6.9 interleaved buffer-copy lane | Graphics frames now have an append-only V6.9 metadata lane for command-ordered `vkCmdCopyBuffer` operations interleaved between draws.  The producer records each graphics command's original command-op sequence, serializes safe interleaved buffer-copy commands into the graphics command stream, and keeps other interleaved transfer/image/dispatch cases fail-closed.  The Android executor validates one command-indexed buffer-copy metadata entry per copy command, materializes source/destination buffer ranges with transfer usage, records `vkCmdCopyBuffer` in order, and writes back the destination range.  Broader mixed submit semantics, image copies, dispatch+graphics mixing, and full Vulkan synchronization remain fail-closed. | `app/src/main/cpp/pdocker_gpu_abi.h`; `docker-proot-setup/src/gpu/pdocker_gpu_abi.h`; `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `app/src/main/cpp/pdocker_gpu_executor.c`; host tests `tests.test_gpu_abi_contract tests.test_llama_gpu_env_parity`; native builds `scripts/build-native-android-ndk.sh`, `scripts/build-gpu-shim.sh`; APK gates `:app:verifyPackagedPayloadFresh :app:assembleCompatDebug`; payload check `scripts/verify-native-payloads.py` |
| 2026-06-06 Vulkan graphics V6.10 interleaved image-copy lane | Graphics frames now have an append-only V6.10 metadata lane for command-ordered `vkCmdCopyBufferToImage`, `vkCmdCopyImageToBuffer`, and `vkCmdCopyImage` operations interleaved between draws.  The producer serializes core `VkBufferImageCopy`/`VkImageCopy` fields with explicit ABI direction constants, keeps unsupported aspects and copy2 pNext semantics fail-closed, and the Android executor validates one command-indexed metadata entry per copy command, materializes buffer/image transfer ranges, replays the matching Vulkan copy call in order, and writes back transfer-written destinations.  First-lane coverage is intentionally bounded to color-aspect fd-backed images; depth/stencil, multiplanar/compressed images, broader image-layout synchronization, dispatch+graphics mixing, and full Vulkan synchronization remain fail-closed. | `app/src/main/cpp/pdocker_gpu_abi.h`; `docker-proot-setup/src/gpu/pdocker_gpu_abi.h`; `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `app/src/main/cpp/pdocker_gpu_executor.c`; host tests `tests.test_gpu_abi_contract tests.test_llama_gpu_env_parity`; native builds `scripts/build-native-android-ndk.sh`, `scripts/build-gpu-shim.sh`; pending APK gates `:app:verifyPackagedPayloadFresh :app:assembleCompatDebug`; pending payload check `scripts/verify-native-payloads.py` |
| 2026-06-06 Vulkan graphics V6.11 interleaved fill/update-buffer lane | Graphics frames now have an append-only V6.11 metadata lane for command-ordered `vkCmdFillBuffer` and `vkCmdUpdateBuffer` operations interleaved between draws.  The producer serializes command-indexed fill/update metadata instead of treating draw-between fill/update as host-only fail-closed: fill entries carry destination buffer resource, offset, size, and 32-bit pattern; update entries carry destination buffer resource, offset, size, payload range, and payload hash.  The Android executor validates one metadata entry per command, materializes destination buffers with `VK_BUFFER_USAGE_TRANSFER_DST_BIT`, records `vkCmdFillBuffer`/`vkCmdUpdateBuffer` in command order, includes transfer-write source stage/access in writeback barriers, and marks destination ranges for writeback.  The first lane remains bounded to fd-backed buffers, 4-byte-aligned offsets/sizes, `vkCmdUpdateBuffer` payloads no larger than 65536 bytes, no active rendering scope, no dispatch+graphics mixing, and no guessed synchronization beyond serialized barriers plus transfer-write writeback. | `app/src/main/cpp/pdocker_gpu_abi.h`; `docker-proot-setup/src/gpu/pdocker_gpu_abi.h`; `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `app/src/main/cpp/pdocker_gpu_executor.c`; host tests `tests.test_gpu_abi_contract tests.test_llama_gpu_env_parity`; native builds `scripts/build-native-android-ndk.sh`, `scripts/build-gpu-shim.sh`; pending APK gates `:app:verifyPackagedPayloadFresh :app:assembleCompatDebug`; pending payload check `scripts/verify-native-payloads.py`; no llama.cpp changes |
| 2026-06-06 Vulkan conservative image format/property lane | The ICD now advertises a conservative nonzero Vulkan format/image capability subset instead of reporting zero format features and rejecting every vkGetPhysicalDeviceImageFormatProperties query.  The advertised surface is intentionally bounded to single-sample optimal-tiling images, supported color/depth/stencil formats, transfer/sample/color/depth-storage use where implemented, and nonzero image/framebuffer limits.  It still refuses linear tiling, sparse images, texel buffers, blit/filter/compressed/SRGB/YCbCr promises, and unsupported create flags so graphics replay cannot depend on guessed capabilities. | docker-proot-setup/src/gpu/pdocker_vulkan_icd.c; host tests tests.test_gpu_abi_contract tests.test_llama_gpu_env_parity; native build scripts/build-gpu-shim.sh; APK gates :app:verifyPackagedPayloadFresh :app:assembleCompatDebug; payload check scripts/verify-native-payloads.py; no llama.cpp changes |
| 2026-06-06 Vulkan graphics V6.12 interleaved clear-color image lane | Graphics frames now have an append-only V6.12 metadata lane for command-ordered `vkCmdClearColorImage` operations interleaved between draws.  The producer serializes one command-indexed clear-color image metadata entry per clear command, including target image index, layout, bounded color subresource range, and raw four-lane clear color bits.  The Android executor validates the table/hash contract, materializes fd-backed transfer-destination images, records `vkCmdClearColorImage` in command order, includes transfer-write source stage/access in image writeback barriers, and marks the touched image ranges for writeback.  This first lane is intentionally bounded to color-aspect images, concrete mip/layer ranges, no active rendering scope, no dispatch+graphics mixing, and no guessed synchronization outside serialized barriers plus transfer-write writeback. | `app/src/main/cpp/pdocker_gpu_abi.h`; `docker-proot-setup/src/gpu/pdocker_gpu_abi.h`; `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `app/src/main/cpp/pdocker_gpu_executor.c`; host tests `tests.test_gpu_abi_contract`; native builds `scripts/build-native-android-ndk.sh`, `scripts/build-gpu-shim.sh`; pending APK gates `:app:verifyPackagedPayloadFresh :app:assembleCompatDebug`; pending payload check `scripts/verify-native-payloads.py`; no llama.cpp changes |
| 2026-06-07 Vulkan graphics V6.13 interleaved clear-depth-stencil image lane | Graphics frames now have an append-only V6.13 metadata lane for command-ordered `vkCmdClearDepthStencilImage` operations interleaved between draws.  The producer serializes one command-indexed clear-depth/stencil metadata entry per clear command, including target image index, layout, bounded mip/layer range, aspect mask, raw depth bits, and stencil value.  The Android executor validates the V6.13 table/hash contract, rejects missing metadata fail-closed, materializes fd-backed transfer-destination depth/stencil images, records `vkCmdClearDepthStencilImage` in command order, includes transfer-write source stage/access in image writeback barriers, and marks the touched image ranges for writeback.  The producer now normalizes `VK_REMAINING_MIP_LEVELS` and `VK_REMAINING_ARRAY_LAYERS` at command-record time for V6.12/V6.13 clear-image lanes, so the ABI continues carrying concrete bounded ranges.  The current lane remains intentionally bounded to single depth or single stencil aspect, no active rendering scope, no dispatch+graphics mixing, and no guessed synchronization outside serialized barriers plus transfer-write writeback; combined depth+stencil aspect remains a follow-up gap rather than guessed behavior. | `app/src/main/cpp/pdocker_gpu_abi.h`; `docker-proot-setup/src/gpu/pdocker_gpu_abi.h`; `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `app/src/main/cpp/pdocker_gpu_executor.c`; host tests `tests.test_gpu_abi_contract tests.test_llama_gpu_env_parity`; native builds `scripts/build-native-android-ndk.sh`, `scripts/build-gpu-shim.sh`; pending APK gates `:app:verifyPackagedPayloadFresh :app:assembleCompatDebug`; pending payload check `scripts/verify-native-payloads.py`; no llama.cpp changes |

Do not claim GPU inference correctness or performance for `ngl>=1` from served
HTTP alone.  The latest promoted correctness evidence is the commit `ac40e49`
safe-kernel artifact, not native llama.cpp Q6 SPIR-V correctness.  The
safe-kernel is a Skydnir bridge compatibility substitution selected under
`PDOCKER_GPU_Q6K_SAFE_KERNEL=1`; it is not a llama.cpp change, not a model
change, and not proof that the original native Q6 shader/driver path is fixed.
The memory readiness gate is still required before heavy compare or benchmark
evidence can promote anything.

Device execution is not a substitute for static proof.  Before any new ADB or
runtime collection, the repository must contain a static hypothesis, a dry-run
plan, the exact evidence fields expected from that run, and explicit branch
decisions for every plausible outcome.  If a static-only review is requested,
do not answer it by collecting fresh artifacts.

### Passthrough boundary terminology

In this bridge, "strict passthrough" means preserving the application-visible
Vulkan semantics, not copying opaque handle values.  SPIR-V bytes, push
constant bytes, specialization data bytes, and buffer payload bytes are the
byte-preservation boundary.  `VkBuffer`, `VkDeviceMemory`, descriptor set, and
pipeline handles are process-local driver objects; the container-side ICD
therefore sends object IDs, descriptor offsets/ranges, memory offsets/sizes,
and shared backing fds, and the Android executor reconstructs an equivalent
object graph with real Android Vulkan handles.

This is different from upstream Docker on Linux.  Docker usually exposes the
host device nodes, driver libraries, ICD files, and permissions into the
container, so the container process calls the real host driver directly.  It
does not translate `VkBuffer` handles.  Skydnir cannot rely on that path on
Android because the product boundary is glibc-container code to APK-owned
Bionic/vendor Vulkan code.

The explicit Q6 WorkgroupSize compatibility lowering is allowed only as a
narrow driver-compatibility lane: a valid module with exactly one literal
`OpExecutionMode LocalSize 1,1,1`, no `LocalSizeId`, a specialized
`BuiltIn WorkgroupSize.x`, and a runtime specialization resolving to
`[32,1,1]`.  It may change only the three literal `LocalSize` operands and
must not rewrite descriptors, push constants, specialization data, bindings, or
buffer contents.

Use three separate lanes when discussing Vulkan work:

1. **Raw vendor passthrough** is not the product path on Android.  It means a
   process calls the vendor driver directly with native process-local handles.
2. **Native strict object-graph passthrough** is the Skydnir product target:
   preserve app-visible Vulkan bytes and semantics while reconstructing Android
   handles from recorded object IDs, offsets, ranges, and shared backing fds.
3. **Diagnostic or compatibility transformations** are explicit, labeled
   deviations such as scoped LocalSize legalization, specialization
   materialization, probe insertion, or Q4/Q6 safe-kernel substitution.  A pass
   in this lane can split causes, but it is not native Q6 passthrough proof.

When this lowering is enabled, `PDOCKER_GPU_MATERIALIZE_SPIRV_SPECIALIZATION_CONSTANTS=1`
is the next allowed compatibility step.  It is still API-equivalent Vulkan
specialization lowering, not a llama.cpp/kernel substitution: descriptor bytes,
push constants, specialization input bytes, and buffer contents remain
unchanged.  The materializer must preserve the `BuiltIn WorkgroupSize` subtree
only while literal `LocalSize` and specialization-resolved WorkgroupSize are
inconsistent; after LocalSize is legalized, that subtree must materialize too
or the driver may keep using the stale default `gl_WorkGroupSize` value.

2026-05-31 update: final-store sampling cleared executor writeback for the
joined Q6 sample, leaving native Q6 SPIR-V execution/final-store semantics as
the active boundary.  The next compatibility pass is a scoped
storage16-to-storage8 lowering for the Q6_K duplicate binding-0 views.  It
rewrites only exact `OpAccessChain` + `OpLoad %ushort` patterns from the
storage16 alias into two byte loads from the byte-identical storage8 alias and
reconstructs the same little-endian `ushort` in SPIR-V.  It does not change
descriptors, buffers, offsets, ranges, push constants, specialization values,
dispatch dimensions, llama.cpp, Dockerfiles, prompts, or model bytes.  Runtime
evidence must report `q6_storage16_loads_lowered` and
`q6_storage16_loads_lowered_count` before any device result from this lane can
be promoted.

2026-05-31 device result: the scoped storage16-to-storage8 lowering is now
active for the instrumented Q6 probe path (`q6_storage16_loads_lowered=true`,
`q6_storage16_loads_lowered_count=24`, effective hash
`0x72f4a362b00221fd`).  It did not fix the prompt result: `/completion`
still returns `" Marvel"`, Q6 writeback remains verified, and the current
Q6 diagnostic boundary remains native Q6 final-store/output-layout semantics
rather than executor writeback.  The next target is not another storage16
view rewrite; inspect the output-index/layout path and final-store value
selection using the lowered effective module as the new baseline.

### Vulkan graphics V6.1 P0-P6 handoff

The current graphics lane is now a supported-subset replay contract, not a
no-op-only diagnostic path.  Producer commit `9d6e724` remains the baseline
container ICD milestone for V6.1 frame construction: resource, descriptor,
image, image-view, sampler, shader-stage, pipeline, vertex, dynamic-state,
command, dynamic-offset, push-metadata, and attachment table serialization.
Since that baseline, Android executor replay has advanced beyond preflight:
validated subset frames can materialize Android Vulkan objects, record command
buffers, submit them, and write back supported attachment and storage-buffer
results.  The producer and executor still fail closed when a frame requires
state outside the serialized ABI or supported replay subset, including blend,
depth/stencil, static viewport/scissor, primitive restart, unsupported
rasterization features, unsupported image descriptor classes, or mixed submit
semantics.  Treat the describe event as schema evidence only; replay success
requires the later materialize/record/submit/writeback evidence stages.

P0-P6 test/design scope:

- **P0 ABI/header gate:** graphics frames must use the `PDGPUG6` magic, ABI
  major 6, V6 or V6.1 minor, fixed header sizes, zero flags/reserved fields,
  bounded frame/fd counts, and matching table schema hashes.
- **P1 object graph gate:** resources, descriptors, images, image views, and
  samplers must reference valid table entries and fd-backed memory ranges;
  invalid indexes, unsupported descriptor object combinations, and overflows
  fail closed before diagnostics are promoted.
- **P2 shader/pipeline gate:** shader fd hashes, entry-name/specialization
  payload ranges, pipeline stage ranges, vertex binding/attribute links, and
  dynamic rendering metadata must be self-consistent before any future replay.
- **P3 command-stream gate:** bind-pipeline, bind-descriptor, push-constant,
  dynamic-state, vertex/index-buffer, draw, indexed-draw, barrier, and V6.1
  dynamic-offset references are checked as table ranges, not guessed from host
  handles.
- **P4 attachment gate:** begin-rendering/render-pass snapshots must serialize
  the attachment table, including image-view/resolve-view refs and clear-value
  payload ranges.  Missing or out-of-range attachment evidence is a preflight
  failure, not a partial graphics pass.
- **P5 diagnostic/preflight executor gate:** the executor must emit the
  nonterminal `vulkan-graphics-v6-describe` JSON with
  `execution_implemented=false` and table counts, then run
  `vulkan-graphics-v6-replay-preflight`.  A describe event alone is never
  graphics success.  Supported replay must continue through the explicit
  pipeline materialize, attachment/buffer/descriptor materialize, command
  record, queue submit, and writeback evidence stages; unsupported command or
  resource subsets must fail closed with a specific reason before result
  promotion.
- **P6 command replay gate:** after P0-P5 pass, Android Vulkan replay must
  reconstruct an Android object graph from serialized IDs/ranges/fds and replay
  commands in order; it must not copy process-local container `Vk*` handles or
  weaken any V6.1 validation to make a frame run.

P6 command-buffer execution, color-attachment writeback, read-only
vertex/index buffers, read-only buffer descriptors, and read-only sampled
image/sampler descriptor replay now exist for the currently supported subset.
Optimal-tiled sampled textures are uploaded through the executor-owned staging
buffer when the serialized image view gives a bounded color mip/layer range.
Graphics evidence can validate producer/executor ABI understanding, pipeline
materialization, attachment image materialization, descriptor set layout/update,
staged sampled-image upload, vertex/index draw recording, queue submit/fence
wait, and stored color-attachment writeback.  This is still not full Vulkan
pass-through: writable storage-buffer descriptors are supported for the current
subset, including writable storage-buffer and storage-image descriptors;
input-attachment descriptors are also replayed when they are image-view-only and
use a validated read-only/general input layout.  Copy+draw mixed submit
semantics, true cross-family ownership transfer, unresolved MSAA store/readback, and broader synchronization remain fail-closed.  It must not be
mixed with llama
Q6 correctness claims, served-HTTP readiness, or benchmark claims until a
dedicated correctness artifact exercises the graphics writeback path.

ABI maintenance rule: `app/src/main/cpp/pdocker_gpu_abi.h` and
`docker-proot-setup/src/gpu/pdocker_gpu_abi.h` are byte-for-byte synchronized
contract headers.  Do not hand-edit one side only.  Any ABI change must update
both headers in the same commit and must pass
`test_container_and_apk_gpu_abi_headers_stay_in_sync` plus the schema hash
contract tests before it is promoted.  V6.11 must be append-only on top of
V6.10: add a `PdockerGpuVulkanGraphicsV611FrameHeader` that embeds the prior
header/extensions, add separate header-extension/table schema hashes for
buffer-fill and buffer-update metadata, and allocate new command IDs after
`PDOCKER_GPU_GRAPHICS_V6_COMMAND_COPY_IMAGE`.  Do not reinterpret any V6.0-V6.10
fields.  The fill/update lane should keep spec-sensitive validation explicit:
4-byte alignment and size multiples for both commands, `vkCmdUpdateBuffer`
payload size no larger than 65536 bytes, bounded payload range/hash checks, and
transfer-write writeback barriers before host fd writeback.  V6.2 is now the
append-only graphics shader specialization metadata extension: the producer may
emit V6.2 only when a graphics stage carries `VkSpecializationInfo`, and the
executor may accept it only when the specialization map-entry table, table hash,
per-stage payload hashes, and stage-index/range checks all validate.  V6.0/V6.1
structs and schema hashes remain frozen.

## Non-Negotiable Rules

- Do not modify llama.cpp.
- Do not modify prompts, Dockerfiles, model files, or diagnostic gates to make
  a run pass.
- Do not rebuild the llama image unless the user explicitly allows it.
- Do not add external libraries or copied upstream code without explicit user
  approval.
- Do not run trial-and-error device jobs or collect new runtime data while a
  static-proof task is active.  A runtime run requires a preflight plan,
  expected evidence schema, pass/fail branches, and explicit user/device
  authorization.
- Keep Android vendor GPU libraries behind the APK/executor boundary.  Do not
  bind Bionic vendor libraries directly into the glibc image as a product path.
- Benchmark claims require a passing correctness report.  Speed without
  correctness is diagnostic only.
- `served=true`, `/health`, or `/v1/models` alone is never success.
- Do not weaken artifact verifier, prompt sanity, runtime freshness,
  config-propagation, Q6 oracle, or writeback gates.
- Commit only focused changes and their directly relevant evidence artifacts.

## Canonical Commands

Use the connected device serial from the user when it changes.  ADB is not a
persistent assumption: if the user says ADB is off, continue host-only checks
and wait for a fresh endpoint before running device readiness or compare jobs.
The latest observed device endpoints are historical evidence only.

Fast local checks:

```bash
cd /root/tl/pdocker-android
python3 -m unittest tests.test_gpu_abi_contract tests.test_llama_gpu_artifact_verifier
python3 -m unittest tests.test_llama_gpu_q6k_workflow
python3 scripts/maintenance/summarize-llama-gpu-artifacts.py \
  --snapshot-date 2026-05-19 \
  --out docs/test/llama-gpu-artifact-sweep-latest.json
bash scripts/build-native-android-ndk.sh
./gradlew :app:assembleCompatDebug
```

The artifact sweep is a local inventory step.  It applies the current
`scripts/verify-llama-gpu-artifact.py` classifier to every
`docs/test/llama-gpu-*.json` file and records the latest blocker distribution,
including row-indexed Q6_K writeback readiness, without touching llama.cpp,
Dockerfiles, models, prompts, or the device.

Install the compat APK:

```bash
ANDROID_SERIAL=192.168.179.26:45443 \
adb install -r app/build/outputs/apk/compat/debug/app-compat-debug.apk

ANDROID_SERIAL=192.168.179.26:45443 \
adb shell am start \
  -n io.github.ryo100794.pdocker.compat/io.github.ryo100794.pdocker.MainActivity
```

Run the tight llama GPU compare loop:

```bash
ANDROID_SERIAL=192.168.179.26:45443 \
bash scripts/android-llama-gpu-readiness.sh \
  --out docs/test/llama-gpu-device-readiness-latest.json

ANDROID_SERIAL=192.168.179.26:45443 \
PDOCKER_GPU_CPU_ORACLE=1 \
PDOCKER_GPU_DISPATCH_PROFILE_LOG=1 \
PDOCKER_GPU_DISPATCH_PROFILE_RESPONSE=1 \
bash scripts/android-llama-gpu-compare.sh \
  --gpu-only \
  --cpu-tps 0.04702448956650603 \
  --cpu-ctx 512 \
  --gpu-ctx 512 \
  --gpu-layers 1 \
  --predict 4 \
  --repeat 1 \
  --out docs/test/llama-gpu-ngl1-<short-name>-20260509.json
```

Do not start the `ngl=1` Q6_K evidence run unless the readiness artifact has
`ready=true` and `preconditions.q6_ngl1_evidence_collection_allowed=true`.
The compare script also writes `gpu.runtime_env_manifest` into the artifact and
echoes manifest-selected runtime environment variables before collection; keep
that record with the Q6_K evidence so env propagation can be audited without
changing llama.cpp, the image, models, or prompts.
For shader-structure triage, `PDOCKER_GPU_SPIRV_DUMP_DIR` may be set to a
workspace/log directory.  The Android executor then records both the original
container-provided SPIR-V module and the effective executor module, plus compact
JSON metadata with word count, instruction count, opcode class counts, local
size evidence, and the FNV hash.  Analyze those dumps with
`scripts/analyze-spirv.py`; this is a structural SPIR-V observation path, not a
hash-targeted correctness bypass.

Static SPIR-V dataflow comparison now has a canonical host-only loop.  Use it
before any device-side patch when the question is "did the bridge understand
the shader's ABI/dataflow?" rather than "did the Android GPU compute the right
numbers?":

```bash
python3 scripts/analyze-spirv.py <native-q6.spv> \
  --json-out <native-q6.analysis.json> \
  --probe-plan-out <native-q6.probe.json> \
  --probe-range 0:2 \
  --disassemble-dir <spvasm-dir>

python3 scripts/verify-spirv-probe-manifest.py <native-q6.probe.json>

python3 scripts/compare-spirv-dataflow.py \
  docs/test/spirv-q6k-safe-current/q6k-safe.analysis.json \
  <native-q6.analysis.json> \
  --json-out <safe-vs-native-q6.dataflow.json>
```

Use the `validation_gates.target_env` emitted by the analyzer.  SPIR-V 1.5
native Q6 modules require `vulkan1.2` validation; treating them as
`vulkan1.1` artifacts is a false blocker and must not be used to reject a
valid module.

Before the next Q6 WorkgroupSize device run, also run the narrow lowering
preflight against the exact native Q6 SPIR-V sample that will be replayed:

```bash
python3 scripts/maintenance/verify-q6-workgroup-lowering-preflight.py \
  /tmp/q6write10-bundle/native-q6.write.spv \
  --expect-spec-id 0 \
  --expect-value 32 \
  --json-out docs/test/q6-workgroup-lowering-preflight-latest.json
```

The preflight must report `ok:true`.  It proves only that this specific module
is structurally eligible for the explicit compatibility lowering; it is not a
correctness claim.  The following run must set
`PDOCKER_GPU_LEGALIZE_WORKGROUP_SIZE_FROM_SPEC=1` explicitly, and the artifact
must later show `local_size_patched:true`, `spirv_local_size_resolved:[32,1,1]`,
Q6 writeback verified, and prompt sanity passing before any benchmark claim is
allowed.

To avoid env-propagation mistakes, prefer the fixed runner instead of typing
the compare command by hand:

```bash
ANDROID_SERIAL=<host:port> \
scripts/android-llama-gpu-q6-workgroup-run.sh \
  --out docs/test/llama-gpu-ngl1-q6-workgroup-legalized-<serial>-<timestamp>.json
```

The runner performs the SPIR-V preflight, records readiness, sets
`PDOCKER_GPU_STRICT_PASSTHROUGH=1`,
`PDOCKER_GPU_STRICT_RECONCILIATION=1`,
`PDOCKER_GPU_STRICT_DUPLICATE_DESCRIPTOR_NORMALIZATION=1`, and
`PDOCKER_GPU_LEGALIZE_WORKGROUP_SIZE_FROM_SPEC=1`, reuses the CPU baseline, and
runs the artifact verifier with `--require-q6-workgroup-clear`.

For host-only review, add `--dry-run` to the runner.  Dry-run mode writes only
the pre-flight plan (using `adb-not-used` when no serial is supplied) and exits
before SPIR-V/probe-env checks, readiness, ADB, or compare steps can touch a
device.  The plan also carries a machine-readable `runner_step_contract` and
`q6_required_env_overlay`, so review and tests can validate the intended
preflight, readiness, compare, artifact-verifier, and plan-verdict sequence
without relying on shell string drift.  Use this first when ADB is unavailable
or before sharing a planned Q6 run for review.

Latest 2026-05-25 Q6 workgroup run:

- Strict API-to-executor reconciliation and strict duplicate descriptor
  normalization now propagate to the executor.  The stale-ICD false negative
  was removed by rebuilding the packaged Vulkan ICD payloads.
- Device run reached `/health`, `/v1/models`, and deterministic completion,
  but prompt sanity still returned `" Marvel"` and did not pass.
- Q6 writeback evidence is no longer the first suspect:
  `q6_writeback_verified_all=true`, row-indexed writeback evidence is present,
  and fd-after-writeback matches the native GPU output samples.  The remaining
  blocker is the native Q6 final-store/output-index path.
- `q6_readonly_dispatch_mutations` is retained as a raw/all observation.  It
  may include legal alias side-effects when llama.cpp binds the same storage
  window through writable and read-only descriptor views.  Blocker selection
  must use `q6_unexpected_readonly_dispatch_mutations` only.  Expected alias
  visibility is reported separately in
  `q6_readonly_dispatch_alias_side_effects`.
- Local artifacts:
  `docs/test/llama-gpu-ngl1-q6-strict-normalized-adb34413b-20260525T135302Z.json`
  and its plan verdict are runtime evidence for this boundary.  They still do
  not allow correctness or benchmark claims.
- Next implementation target: add final-store/output-index diagnostics around
  binding 2.  The executor records `q6_stride_d`, `q6_batch_stride_d`,
  `q6_store_window_begin`, `q6_store_window_end`, per-sample
  `expected_store_index`, `best_index_in_store_window`, `best_store_row`, and
  `best_store_row_delta`.  The compare artifact summarizes these as
  `q6_output_index_probe_summary` with `fixed-offset`, `scatter`,
  `final-store-value`, or `inconclusive`.  Do not modify llama.cpp,
  Dockerfile, model, or prompt.

The tracked safe baseline currently has source hash `0x7ec0292e948c9b41`,
entry point `main`, local size `[1,1,1]`, descriptors set 0 bindings
`0`/`1` read-only and `2` writable, and 13 push-constant uints
(`ncols`, strides, batch strides, fusion flags, base workgroup, and broadcast
fields).  It also records pointer-origin evidence such as
`push[0:ncols@0]` loads and `descriptor[0,2]` stores.  This baseline is useful
for detecting ABI/dataflow drift; it is not proof that the original native
llama.cpp Q6 module is correct.

The repository tracks native Q6 JSON evidence for source hash
`0x1bf751845c5dce75`, but not the raw `.spv`/`.spvasm` binaries.  Those binary
SPIR-V files are ignored local inputs; do not synthesize or fake them in a
clean checkout.  The tracked `.probe.json` artifacts must verify with the
current `scripts/verify-spirv-probe-manifest.py` schema before accepting static
Q6 conclusions.  If a fresh runtime dump is needed, the next ADB run should set
`PDOCKER_GPU_SPIRV_DUMP_DIR`, locate the dumped module matching
`q6_workgroup_diagnostics.latest_spirv_hash` or source hash
`0x1bf751845c5dce75`, and then run the analyze/verify/compare loop above.

The optional probe replay path is fail-closed and uses the existing
`VULKAN_DISPATCH_V4` command, not a new GPU ABI.  A replay run must provide all
of the following and must leave llama.cpp, Dockerfile, model, and prompt
unchanged:

```bash
PDOCKER_GPU_SPIRV_PROBE_MANIFEST=<probe.json>
PDOCKER_GPU_SPIRV_PROBE_SHADER=<instrumented.spv>
PDOCKER_GPU_SPIRV_PROBE_EXPECTED_HASH=<original-source-fnv>
PDOCKER_GPU_SPIRV_PROBE_EFFECTIVE_HASH=<instrumented-fnv>
PDOCKER_GPU_SPIRV_PROBE_DEBUG_BYTES=<bounded-byte-count>
PDOCKER_GPU_SPIRV_PROBE_DEBUG_SET=<unused-set>
PDOCKER_GPU_SPIRV_PROBE_DEBUG_BINDING=<unused-binding>
```

The ICD verifies the manifest, opens and hashes the effective probe shader, and
adds the debug buffer as an ordinary storage-buffer binding.  If any manifest,
hash, size, or binding guard fails, the probe must not dispatch.  This keeps
the narrowing work auditable and prevents "works because diagnostics changed
the workload" regressions.

For Q6_K executable probe writes, `scripts/prepare-q6k-noop-probe.sh
--probe-writes` produces a module with debug-SSBO records and leaves the
V4 schema unchanged.  The executor now emits `debug_probe_binding`,
`u32_after_dispatch`, and `u32_after_writeback` samples for the configured
debug binding.  The next device-side evidence run should inspect those u32
records with `scripts/parse-q6k-probe-u32.py` before adding more shader
substitutions.
`scripts/analyze-spirv.py` also emits a control-flow graph with function,
basic-block, successor, store-site, and probe-candidate inventories.  Do not
try to submit arbitrary SPIR-V fragments to Vulkan: the valid-module boundary
must be preserved.  The intended narrowing method is block-boundary/store-site
instrumentation inside a still-valid module, then CPU-oracle comparison of the
probe output.  Static block order is not the same as dynamic execution order,
so treat the generated split plan as candidate-range bisection, not proof of a
first executed divergent block until dynamic probe records confirm it.  That
lets us bisect shader evidence ranges without replacing
llama.cpp, changing prompts, or depending on one hard-coded hash.
The replay path should not introduce a new GPU command ABI at first:
instrumented modules should be passed through the existing `VULKAN_DISPATCH_V4`
path as a replacement shader fd plus one extra ordinary storage-buffer binding
for debug output.  The debug binding must use a statically unused descriptor
set/binding pair and, until set-aware executor reflection is broader, a globally
unused binding number.  The V4 schema, required command tokens, model, prompt,
Dockerfile, and llama.cpp source remain unchanged; original/effective/probe
hashes are correlated through the probe manifest and artifact logs.

V5 framed transport ABI proposal: keep `VULKAN_DISPATCH_V4` as the default and
wire-compatible fallback.  A V5 frame may be used only when the ICD and executor
negotiate the same explicit capability/version bit; any missing or mismatched
capability falls back to V4 behavior.  The frame should carry a compact header
plus a resource table for object IDs, fds, memory offsets, sizes, and lifetime
tokens, and a descriptor table for set/binding/type/resource-index/offset/range
entries.  SPIR-V, push constants, specialization data, and buffer bytes remain
byte-preserved; the first goal is unambiguous transport growth, not a semantics
or performance claim.

2026-05-24 Q6 write10 probe integration status:

- Evidence artifact:
  `docs/test/llama-gpu-ngl1-q6-write10-classified2-adb40309-20260524T021223Z.json`
  (ignored runtime evidence).  This run used the 10-point executable probe
  from commit `1368734`; commit `81e57c1` preserves the probe hash as the Q6
  diagnostic identity instead of dropping the event after shader substitution.
- Cleared for this run: Q6/probe reachability is visible
  (`q6_probe_event_count=3`), all writable/probe writebacks sampled by the
  diagnostic path verify (`q6_writeback_verified_all=true`), and the bounded
  probe parser summary is `pass`.
- Not cleared: prompt sanity still fails (`2+3=` does not produce the expected
  answer), the native source-oracle path is still not attached for the original
  Q6 source hash `0x1bf751845c5dce75`, and `served=true` is only liveness -- it
  is not a correctness or benchmark success signal.
- Current blocker wording: `q6-probe-writeback-cleared-oracle-missing`.  This
  means Q6 writeback is no longer the first suspect for this artifact, but it
  also does **not** prove native Q6 shader arithmetic or model correctness; the
  source oracle must be connected before the blocker can move to arithmetic,
  synchronization, or final output semantics.
- Implemented after this artifact: the ICD now carries
  `sender_source_spirv_hash` and `sender_effective_spirv_hash` on probe replay,
  and the executor resolves CPU-oracle identity through a fail-closed
  source/effective relation before it may classify the original Q6 source
  shader.  This fixes the structural misunderstanding where the executor only
  saw the instrumented/effective probe module hash and therefore could not
  safely attach the source Q6 oracle.
- Next task: keep llama.cpp, Dockerfile, model, and prompt unchanged; rerun the
  same bounded probe only after installing the APK containing that source/effective
  identity transport, then decide whether the next concrete blocker is native Q6
  arithmetic, dynamic shader execution, synchronization, or final output
  semantics.  Do not add more device tries before the static SPIR-V control-flow
  and descriptor/push-constant interpretation above has been reviewed.

Static misunderstanding fixed in the follow-up commits:

- "Passthrough" did not mean the bridge had no opportunity to change the
  shader.  The executor could still apply diagnostic transformations after
  receiving the container SPIR-V.  In strict passthrough mode, descriptor
  duplicate rewriting was already disabled, and WorkgroupSize legalization is
  now disabled as well (`legalize_workgroup_size_from_spec_source` reports
  `strict-passthrough`).  This makes the strict lane a real ABI-preservation
  lane instead of a partially transformed compatibility lane.
- Q6 binding 0 is intentionally duplicated in the SPIR-V as two typed views of
  the same descriptor: an 8-bit byte view (`%346`) and a 16-bit ushort view
  (`%371`).  Rewriting either view to another binding changes the shader ABI
  and is not valid evidence for native llama.cpp passthrough correctness.
- Source, effective, and oracle shader identities are separate.  Probe replay
  passes an instrumented/effective module, but the CPU oracle must classify the
  original Q6 source module.  The ICD now carries
  `sender_source_spirv_hash`/`sender_effective_spirv_hash`; the executor trusts
  the source oracle only when that source/effective relation is verified and a
  probe debug binding is present.
- The apparent post-reduction probe miss was not a writeback failure.  Those
  candidate blocks are optional fused-add branches controlled by push constant
  member 7 (`&1` for binding 3 and `&2` for binding 4).  With push[7] equal to
  zero, skipping those blocks is expected and the final-output store can still
  execute.
- The next static split point is upstream of the verified final/reduction
  writeback path: cand83/cand93 stage Q6 `scales[]` into Workgroup `%332`, and
  cand98 accumulates input-vector dot products into Function `%656`.  If the
  next strict probe still produces wrong prompt output after source-oracle
  attachment, inspect this dequant/FMA accumulator boundary before changing
  Dockerfiles, prompts, or llama.cpp.

If the run stops before Q6_K, the artifact verifier now preserves bounded
`pre_http_failure_evidence` for the first failed generic SPIR-V event
(`fail_stage`/`error`, `vk_result`, SPIR-V hash, pipeline key, feature
requirements, Android feature bits, and `q6_reachability`). Treat that as a
pre-Q6 setup blocker, not as a Q6 correctness result.

2026-05-18 update: the ICD/runtime freshness marker for this lane is now
`vulkan-icd-feature-chain-marker-20260518`.  Re-run device artifacts after
installing an APK with that marker before accepting any new pre-Q6 conclusion.
The ICD now keeps the requested-feature mask tied to the full Vulkan
`VkDeviceCreateInfo`/`VkPhysicalDeviceFeatures2` pNext chain and advertises the
8-bit storage, shader-float16-int8, and storage-buffer-storage-class extension
surface consistently with the feature bits it exposes.  If a pre-Q6
`VK_ERROR_FEATURE_NOT_PRESENT` remains, compare `spirv_required_feature_mask`,
`spirv_requested_feature_missing_mask`, `android_vulkan_features`, and
`android_vulkan_enabled_features` first; do not jump to Q6_K oracle work until
those fields prove the bridge setup is coherent.

2026-05-18 follow-up: commit `5e5f0c7` hardens the ICD pNext traversal used by
that feature-chain path.  The previous generic `VkBaseInStructure` view can
miss nested feature structs under optimized C builds, so the ICD now copies the
header fields before dispatching to concrete Vulkan structs.  Keep
`tests.test_vulkan_icd_feature_chain` in the fast gate; it compiles a tiny
`-O2` C harness and catches regressions where `VkPhysicalDeviceFeatures2 ->
VkPhysicalDeviceVulkan11Features -> VkPhysicalDeviceVulkan12Features` collapses
back to the base feature mask only.

2026-05-18 verifier gate: commit `cdd5f3f` also prevents a stale ICD artifact
from being promoted into a new pre-Q6 conclusion.  When the compare artifact
declares an `expected_icd_marker`, `scripts/verify-llama-gpu-artifact.py`
requires that marker in `observed_icd_markers` before classifying generic
SPIR-V pipeline failures.  If this trips, reinstall the freshly built compat
APK and rerun the same compare; do not infer feature-chain or Q6_K state from
the stale artifact.

2026-05-18 compare hardening: the compare artifact now marks runtime freshness
as `pass` only when both requested runtime markers are observed, and pre-Q6
generic SPIR-V evidence is anchored to the first failed event rather than a
later cleanup or follow-on failure.  Fresh feature-chain ICD artifacts also
fail closed as `vulkan-pipeline-feature-evidence-missing` if a
`VK_ERROR_FEATURE_NOT_PRESENT` blocker lacks required/requested feature masks
or Android enabled-feature evidence.  This keeps the next device run from
turning incomplete setup evidence into a false Q6_K conclusion.

2026-05-19 workflow hardening: `scripts/android-llama-gpu-q6k-run.py` now
persists the verifier stdout next to the workflow manifest as
`*.verifier.stdout` and extracts JSON classification from the full output, not
from the 8 KiB `stdout_tail`.  This prevents long verifier diagnostics from
silently dropping `classification`/`next_action` in
`docs/test/llama-gpu-q6k-workflow-latest.json`.

2026-05-20 device-run hardening: the compare script now treats
`POST /containers/create` as a heavier Engine operation than start/inspect.  A
host-side create timeout no longer immediately becomes a false GPU failure:
the script polls the named container until a delayed create becomes inspectable,
waits for stale targets to disappear before recreating them, and retries
late-created target cleanup on failure.  The first retest on
`192.168.179.26:41503` created and started `3d02cf0782c5`
(`/pdocker-llama-cpp`) and the verifier returned the previous real blocker,
`q6-native-device-execution-or-final-store`; the HTTP server became healthy
after the compare wait window, but a `2+3=` completion probe still timed out.
Treat this as runtime/startup latency plus the existing Q6_K correctness
blocker, not as proof of correct or fast GPU inference.

2026-05-20 llama call-site correlation: the current pre-Q6 pipeline failure
`0xf3cd7d18f0276b42` was matched against upstream llama.cpp sources without
changing llama.cpp.  It is `ggml-vulkan.cpp` creating
`mul_mat_vec_q4_k_f32_f32` from `vulkan-shaders/mul_mat_vec_q4_k.comp` with
`vk_mat_vec_push_constants`, five descriptor buffers
`A/B/D/Fuse0/Fuse1`, and specialization constants
`{ BLOCK_SIZE=32, NUM_ROWS=2, NUM_COLS=1/2 }`.  The shader deliberately
declares three typed views of binding 0 for the same Q4_K block
(`block_q4_K`, `block_q4_K_packed16`, `block_q4_K_packed32`); this is the
llama.cpp Q4_K ABI, not a Q5/Q6 dispatch mix-up.  The Skydnir-side
diagnostic classifier now recognizes the original hash, the Float16-capability
insertion hash `0x853c49b4900eed3c`, and the duplicate-descriptor-materialized
hash `0x22ab0152b230e983` as Q4_K matvec variants.  `PDOCKER_GPU_Q4K_SAFE_KERNEL`
remains an explicit diagnostic override and is available under strict
passthrough for isolating driver compilation rejection from descriptor/call-site
ABI correctness; it is not a benchmarkable product optimization.
Fresh APK/device evidence for this lane must show executor marker
`gpu-executor-q6-readonly-snapshot-20260531`.

2026-05-21 Q6 evidence-retention gate: a fresh `ngl=1` compare on
`192.168.179.26:37303` served `/health`, `/v1/models`, and `/completion`, but
the deterministic prompt returned the wrong text (`2+3=` produced `Marvel`).
The compact artifact also showed an important evidence gap: the dispatch
lifecycle reached the known Q6_K/final-projection hash `0x1bf751845c5dce75`,
while `q6_workgroup_diagnostics` still reported `event_count=0` and
`not-reached`.  The compare summarizer now keeps known Q6_K/final-projection
dispatches ahead of bounded tail sampling, records lifecycle Q6 dispatches as
`q6_dispatch_seen`, and fails closed as `q6-oracle-capture-missing` when a Q6
dispatch is observed without CPU-oracle/local-size/binding/writeback evidence.
The verifier also treats that as a diagnostic-evidence blocker before any
served HTTP wrong-output claim can be promoted.  Next device run should use the
same Dockerfile, model, prompt, and image and verify that the new artifact
classifies the run as either a concrete Q6_K oracle result or
`q6-oracle-capture-missing`; it must no longer look like Q6 was simply not
reached.

2026-05-23 Q6 WorkgroupSize validation lane: the fresh device endpoint was
`192.168.179.26:34761`.  Readiness reported `ready=true` with
`memory.mem_available_mb=2656`, but Android zram was under pressure
(`memory.swap_free_mb=156`; advisory threshold `1024`; hard swap gate disabled).
That makes the run acceptable for diagnostic evidence, but not for performance
claims or long benchmark interpretation.

Relevant artifacts:

- `docs/test/llama-gpu-readiness-adb34761-latest.json`
- `docs/test/llama-gpu-ngl1-q6-workgroup-legalized-adb34761-20260523T084956Z.json`
- `docs/test/llama-gpu-ngl1-q6-workgroup-composite-adb34761-20260523T091428Z.json`

The `q6-workgroup-legalized` artifact reached generic SPIR-V dispatch and kept
Q6 lifecycle evidence, but still did not surface a Q6 oracle response:
`q6_dispatch_seen=true`, `q6_dispatch_event_count=4`,
`q6_workgroup_diagnostics.event_count=0`, and
`diagnostic_interpretation=q6-dispatch-seen-without-oracle-response`.  Treat
that as an evidence-capture blocker, not as a Q6 mathematical result.

The `q6-workgroup-composite` artifact did not provide fresh executor evidence
for the Q6 oracle path and the verifier classified it under runtime freshness
(`executor-marker-not-observed`).  Its run-level blocker was a wait-server
memory-pressure stop, not a completed GPU correctness result.

Current blocker name for this lane:

- `spirv-local-size-inconsistent` / Q6 `BuiltIn WorkgroupSize` evidence not yet
  visible in the compact Q6 oracle record.

Next validation criteria:

- The run must observe the expected fresh executor marker before interpreting
  Q6 correctness.
- The Q6 event for source hash `0x1bf751845c5dce75` must include a valid JSON
  oracle response, not only lifecycle dispatch evidence.
- The Q6 record must expose the effective specialization-backed workgroup tuple
  as `[32,1,1]` through `spirv_local_size_resolved` or the equivalent folded
  summary field.
- If legalization is active, the event must explicitly show that the source
  shader hash remains `0x1bf751845c5dce75` while the effective execution module
  was legalized from the `BuiltIn WorkgroupSize` specialization composite.
- Only after those fields are visible may the next blocker move to Q6 writeback,
  synchronization, output layout, or arithmetic/reduction.  Do not promote
  prompt output, throughput, or benchmark evidence while Q6 WorkgroupSize
  evidence is missing.

Milestone compare with CPU baseline should be run only after a correctness
blocker changes, not after every small diagnostic edit.

## Stage Plan And Acceptance Criteria

### Stage 1: Keep the known-good `ngl=0` boundary green

Purpose: make sure the bridge did not regress while working on `ngl=1`.

Procedure:

1. Run the tight compare with `--gpu-layers 0`.
2. Inspect `gpu.correctness.summary`.
3. Inspect the first `small-f32-indexing` oracle events.

Pass criteria:

- `gpu.correctness.summary.correctness == "pass"`.
- `gpu.correctness.summary.required_failures == 0`.
- `benchmark_claim_allowed == true`.
- For `0x11d5243c43b23a7b`, `cpu_oracle.status == "match"`.
- For the matching oracle events, `mismatch_count == 0`.
- The event reports `materialize_specialization == false`.

Fail criteria:

- Required correctness fails.
- `PDOCKER_GPU_MATERIALIZE_SPIRV_SPECIALIZATION_CONSTANTS` is accidentally
  defaulting back to true.
- A known small shader hash becomes unsupported or mismatching.

If this fails, stop `ngl=1` work and fix the regression first.

### Stage 2: Classify each `ngl=1` front-blocker shader

Purpose: determine which shader first explains the wrong first token.

Current `ngl=1` front-blocker candidates:

| Hash | Current classification | Current status |
|---|---|---|
| `0xac41e8033a67af4a` | RoPE/Yarn | completed; oracle matches in `docs/test/llama-gpu-ngl1-rope-yarn-oracle-20260509.json` |
| `0xf2f988b94bd3e0dc` | RMSNorm with optional multiply | oracle matches in `docs/test/llama-gpu-ngl1-rms-norm-oracle-20260509.json` |
| `0x274f68a67dfef210` | `mul_mat_vec_q6_k`-like large quantized matvec / final projection | row-indexed writeback verified; current blocker `native-q6-device-execution-or-final-store` |

Procedure:

1. For each candidate, inspect SPIR-V assembly dumped under the llama workspace
   logs, or pull the `.spv` file from the device and run `spirv-dis`.
2. Identify:
   - descriptor binding read/write roles,
   - push constant indices used,
   - specialization constants used,
   - arithmetic operation family,
   - dispatch geometry and local size,
   - output binding index.
3. Add only a hash-gated debug oracle when the operation is small enough to
   emulate safely inside `pdocker_gpu_executor.c`.
4. Record `cpu_oracle.status`, `compared_floats`, `mismatch_count`,
   first mismatch, and sample values.

Pass criteria for a shader:

- The shader has a stable classification in
  `docs/test/LLAMA_GPU_CORRECTNESS_20260507.md`.
- The oracle either:
  - executes and reports `status == "match"` with `mismatch_count == 0`, or
  - executes and reports a precise mismatch with first-mismatch samples, or
  - is explicitly marked too large/unsafe with a documented reason.
- Unsupported hashes are not silently ignored if they are present in the latest
  `ngl=1` correctness-failing run.

Fail criteria:

- A hash is called "fixed" without oracle evidence or a correctness run.
- The oracle reads or writes large buffers without a cap.
- The oracle mutates container buffers; oracle code must remain diagnostic-only.

### Stage 3: RoPE/Yarn oracle for `0xac41e8033a67af4a` (completed)

Purpose: clear the small, deterministic RoPE/Yarn transform before attacking
large final-projection/matmul-like work.

Completed procedure:

1. Use the existing dumped SPIR-V assembly for the hash.
2. Implement a hash-gated CPU oracle only for the exact observed descriptor and
   push layout.
3. Keep memory caps small; this shader's captured binding footprint is under
   about 400 KiB in the zero-layer control.
4. Compare after Vulkan fence and before writeback, same as existing CPU
   oracles.

Evidence-backed pass criteria:

- `cpu_oracle.kernel_hint == "rope-yarn"`.
- `executed == true`.
- `compared_floats > 0`.
- `mismatch_count == 0`.
- `docs/test/llama-gpu-ngl1-rope-yarn-oracle-20260509.json` records
  `compared_floats=4096` and `status=match`.
- If this ever regresses, the first mismatch must include source sample,
  expected value, GPU value, and absolute error.

Regression fail criteria:

- The oracle assumes a different binding order than `spirv_binding_reflection`
  reports.
- The oracle's push constant interpretation is not checked against SPIR-V
  access.
- The run omits `PDOCKER_GPU_CPU_ORACLE=1` but is used as oracle evidence.
- The hash disappears from `cpu_oracle_known_llama_hash()` or no longer maps to
  `kernel_hint == "rope-yarn"`.

### Stage 4: Large candidate split for `0x274f68a67dfef210`

Purpose: decide whether the remaining correctness failure is final-projection,
quantized matmul, descriptor aliasing, or writeback/residency.

Current entry condition: Stage 3 is complete for the observed `ngl=1` run.
Both `0xac41e8033a67af4a` (`rope-yarn`) and `0xf2f988b94bd3e0dc`
(`rms-norm`) execute bounded CPU oracles and report `mismatch_count == 0` in
`docs/test/llama-gpu-ngl1-rms-norm-oracle-20260509.json`.  The model-level
correctness probe still fails, so `0x274f68a67dfef210` is now the next primary
blocker.

Current blocker statement: keep Q6_K strict passthrough as the fidelity
baseline.  The next fix must explain the
`native-q6-device-execution-or-final-store` blocker for
`0x274f68a67dfef210` without changing llama.cpp, the Dockerfile, the model, or
the prompts.  Workgroup shape and row-indexed writeback are currently clear;
focus on executor/Vulkan device execution, also recorded as
`Vulkan device-execution`, versus final output store before any
performance claim.

Procedure:

1. Do not start with a full CPU oracle for the 510 MiB input range.
2. First add metadata classification:
   - descriptor sizes,
   - descriptor aliases,
   - storage format clues from SPIR-V,
   - output binding sample hash before/after,
   - whether output and read-only bindings overlap.
   The current shader dump matches llama.cpp's `mul_mat_vec_q6_k` family:
   it declares multiple binding-0 views for the same quantized weight buffer,
   uses storage8/storage16/int8 features, and specializes
   `BLOCK_SIZE=32`, `NUM_ROWS=2`, `NUM_COLS=1`.
   The compact executor event must also include bounded `push_u32` values so a
   sampled oracle can reproduce row/stride coordinates without copying the
   large weight buffer.
3. Add a sample-window oracle only if a bounded subset can be proven correct.
   This is now implemented for the observed Q6_K layout: it reads only eight
   output rows, `8 * 16 * 210` weight bytes, and the 16 KiB vector input.
4. Compare the sampled output values with CPU/no-offload logits if available.

Pass criteria:

- A clear blocker class is recorded:
  - descriptor alias/rewrite bug,
  - quantized storage decode mismatch,
  - push/specialization interpretation mismatch,
  - copy/upload/writeback/residency bug,
  - or Android Vulkan execution mismatch.
- Any oracle for this hash is bounded by memory and time caps.
- The output includes enough sample coordinates to reproduce the mismatch.
- Current evidence `llama-gpu-ngl1-q6k-sample-oracle-20260509.json` reports a
  bounded oracle mismatch for all eight sampled rows. This shifts the next
  split from "unknown large shader" to "Q6_K decode/math vs descriptor-view
  semantics/local-size execution".
- The no-duplicate-rewrite rerun changes the rewritten shader hash from
  `0x274f68a67dfef210` to `0x1bf751845c5dce75`, but the sampled Q6_K oracle
  still mismatches the same first row. Do not spend the next iteration only on
  duplicate descriptor rewrite; split local-size/specialization execution,
  Q6_K decode layout, and descriptor-view semantics instead.
- The literal-local-size patch changes the active hash to
  `0x09c4622d92c6acb9` and records `spirv_local_size=[32,1,1]`, but the sampled
  oracle still mismatches. Treat local-size patching as a necessary compatibility
  hardening step, not as the current root cause. The next most valuable split is
  a dequant-only check for the same Q6_K blocks before reduction.
- The first decode-variant check rules out the obvious high-bit, signed-scale,
  and zero-point mistakes: none produces the GPU's row-0 value. Continue with a
  descriptor-view/reduction split: verify the byte view and packed16 view
  produce identical per-lane inputs, then inspect whether the shared-memory
  reduction writes the same full sum that the sampled oracle computes.
- The byte-view vs packed16-view Q6_K split has now been executed in
  `llama-gpu-ngl1-q6k-packed16-view-20260509.json`. The packed16-view oracle
  gives the same row-0 sum as the canonical byte view (`abs_delta=0`), while the
  GPU output remains `6.83085108`. This means the Vulkan bridge should not add a
  data-structure conversion for Q6_K blocks. The next split should stay at the
  API/dispatch boundary: descriptor effective range/offset, buffer aliasing,
  specialization-local-size execution, and shared-memory reduction.
- The first 32-lane reduction split is recorded in
  `llama-gpu-ngl1-q6k-partial-lanes-fixed-20260509.json`. Row 0's half-full
  value (`6.93901168`) is close to but not equal to the GPU value
  (`6.83085108`), and the sampled rows do not follow a stable half-reduction
  pattern. Continue by expanding the oracle from sparse sampled rows to a small
  contiguous row window, then compare GPU output indices against expected row
  sums and half/subgroup sums to detect output-layout or workgroup-row mapping
  mistakes.
- The contiguous window is now recorded in
  `llama-gpu-ngl1-q6k-row-window-20260509.json`. All 32 rows still mismatch.
  Some GPU values are close to half sums from nearby rows, but no stable mapping
  emerges. Next, inspect the Q6_K SPIR-V index arithmetic directly: derive the
  exact output index expression from `GlobalInvocationId`, specialization
  constants, and push constants, then update the oracle to follow that mapping
  instead of assuming `dst[row]`.
- The shader-like oracle in
  `llama-gpu-ngl1-q6k-shader-like-oracle-20260509.json` follows the source
  shader's packed 32-bit loads and scale-cache accumulation and still matches
  the canonical oracle within `4.16e-7`. Do not add a data conversion layer.
- The duplicate Binding 0 materialization probe in
  `llama-gpu-ngl1-q6k-materialized-alias-icd-20260509.json` confirms the option
  is propagated through the container ICD and executor, but output is unchanged.
  Same-buffer aliasing is therefore not the sole failure. Next probes should
  reduce the shader execution model itself: specialize/materialize constants
  more completely, then force/disable shared-memory reduction variants or
  emulate the Q6_K shader as a bridge-owned kernel for this hash.
- If a new artifact reports `config_propagation.summary == "fail"`, stop Q6_K
  diagnosis and fix environment propagation first.  A missing diagnostic knob
  can invalidate every Q6_K split, including safe-kernel, strict-passthrough,
  specialization, descriptor-transfer, and subgroup experiments.
- The next Q6_K action after environment propagation is trusted is to preserve
  strict passthrough and collect a workgroup-cleared artifact that names one
  precise blocker class: descriptor effective range/offset, memory
  residency/staging/writeback, synchronization/device-execution, or Q6_K
  arithmetic/reduction.  Do not treat another sampled mismatch as progress
  unless it narrows one of those classes.
- As of 2026-05-15, the compare summarizer records that narrowed class in
  `gpu.diagnostics.q6_workgroup_diagnostics.blocker_class`, plus bounded Q6_K
  evidence (`q6_first_mismatch`, writable output binding hashes, read-only
  upload/dispatch hash mismatches, and whether the shader-like 32/64-lane CPU
  oracle matched the canonical sum).  The artifact verifier now blocks
  correctness and benchmark claims unless Q6_K workgroup shape is clear *and*
  the Q6_K oracle reports `latest_status == "match"`.
- The Q6_K oracle also now decodes the observed push layout for accumulator
  mask (`push_u32[7]`), base workgroup/batch offset (`push_u32[8]`), derived
  output base, derived weight-row block base, and optional accumulator bindings
  3/4.  A nonzero accumulator mask with missing/unreadable accumulator inputs is
  a fail-closed oracle blocker, not a generic arithmetic mismatch.
- The next host-side diagnostic split now records writable-binding writeback
  hash evidence.  Executor binding details include `writeback_verified` and
  `writeback_mismatch`; the compare summary includes
  `q6_writable_writeback_mismatches`, `q6_writable_writeback_unknown`, and
  `q6_writeback_verified_all`.  A strict-passthrough artifact can now narrow the
  previous `vulkan-device-execution-or-writeback` class to `writeback` when the
  fd hash disagrees with the post-dispatch GPU/staging hash, or to
  `vulkan-device-execution` when shader-like Q6 arithmetic is cleared and all
  writable writebacks are hash-verified.
- The verifier now treats a Q6_K oracle match as insufficient unless writable
  output writeback is hash-verified.  `latest_status == "match"` with
  `q6_writable_writeback_mismatches` fails closed as `q6-writeback-mismatch`;
  missing/unknown writable writeback evidence fails closed as
  `q6-writeback-unverified`.  This prevents a pre-writeback oracle match from
  being promoted into a correctness claim when the container-visible fd boundary
  has not been proven.
- The bounded native Q6_K reduction/output-layout probes have now run through
  `docs/test/llama-gpu-ngl1-q6-row-provenance-20260519.json`. Row-indexed
  writeback is verified, workgroup shape is clear, and the native reduction /
  shader-like sum clears, but final output still mismatches. The artifact
  rejects a stable fixed output-layout offset and row-provenance explanation.
  Current blocker: `native-q6-device-execution-or-final-store`; next work should
  narrow executor/Vulkan device execution versus final output store, not
  recollect a generic row-indexed artifact.

#### Row-indexed Q6_K device-run decision tree

For strict `ngl=1` device artifacts with row-indexed Q6_K writeback evidence,
decide the C-side blocker in this order. The latest row-provenance artifact has
already landed past the generic row-indexed gate; use this tree for regressions
or reruns, not as a request to collect another generic row-indexed artifact.

1. **If memory-blocked**: if the artifact reports `insufficient_memory`,
   `runtime_memory_pressure`, `device_memory_blocked:true`, or a runtime abort
   before the Q6_K dispatch, stop Q6 diagnosis.  This is not Q6 evidence and it
   does not justify a C-side Q6 change.  Free Android memory without killing the
   user's browser/VS Code session, keep the same APK/image/prompts, and rerun
   the same compare command.
2. **If row-indexed writeback is absent or differs**: if
   `q6_row_indexed_writeback_evidence` is empty, `q6_row_indexed_writeback_verified`
   is not true, `q6_writeback_verified_all` is not true, or any
   `f32_after_dispatch` / `f32_after_writeback` value differs at the
   `q6_row_indexed_sample_indices`, classify the next blocker as `writeback`.
   Fix only writable-output staging/cache/download/fd propagation before
   revisiting shader math.
3. **If writeback is verified + the Q6 oracle still mismatches**: require
   `q6_writeback_verified_all == true`,
   `q6_row_indexed_writeback_verified == true`, non-empty
   `q6_row_indexed_writeback_evidence`, and `latest_status == "mismatch"`.
   Then use the existing sub-classifier instead of treating "another mismatch"
   as progress:
   - If `workgroup_shape_blocker == true`, `spirv_local_size_consistent` is not
     true, or `spirv_local_size_resolved` is not `[32,1,1]` for the Q6_K event,
     the next C-side blocker is **workgroup-shape**: fix local-size
     propagation/materialization and strict refusal semantics.
   - If workgroup shape is clear, read-only upload/dispatch hashes are clean,
     and `q6_shader_like_64_abs_delta` / shader-like diagnostics clear the
     CPU-side Q6 arithmetic, the next C-side blocker is **Vulkan
     device-execution**: inspect barriers, queue submission, device-local
     staging, and host/device visibility, not the Q6 decode.
   - If workgroup shape and writeback are clear but the shader-like oracle does
     not clear the math, the next C-side blocker is
     **Q6 arithmetic/reduction/output-layout**: inspect the native Q6 SPIR-V
     reduction, lane mapping, accumulator mask/base-workgroup handling, and
     output index expression.  Do not add a Q6 block data conversion layer or
     rebuild llama.cpp unless a bounded artifact proves that exact need.
4. **If writeback is verified + the Q6 oracle matches**: only then may the run
   advance out of this blocker, and only if the normal prompt correctness,
   runtime freshness, config propagation, and speedup fields also pass.

Fail criteria:

- Eagerly reading hundreds of MiB into a diagnostic oracle.
- Treating speed as useful while the required correctness probe fails.
- Hiding a mismatch by lowering `n_predict`, changing prompt probes, or
  rebuilding llama.cpp.

## UI/compose runtime defaults and compare-only diagnostics

Environment propagation has caused repeated false trails, so the current rule
is explicit rather than implicit:

- UI/compose runtime defaults in `docker-proot-setup/bin/pdockerd` must carry
  production-safe Vulkan limits and Q6_K toggles that containers need at normal
  startup, including `PDOCKER_VULKAN_DISABLE_8BIT_STORAGE`,
  `PDOCKER_GPU_REWRITE_DUPLICATE_DESCRIPTOR_BINDINGS`,
  `PDOCKER_GPU_RESIDENT_CACHE`, `PDOCKER_GPU_RESIDENT_CACHE_MIN_BYTES`,
  `PDOCKER_GPU_STRICT_GRAPH_CACHE`,
  `PDOCKER_GPU_Q6K_ORACLE_WRITEBACK`, `PDOCKER_GPU_Q6K_SAFE_KERNEL`,
  `PDOCKER_GPU_Q6K_COMPAT_REWRITES`, `PDOCKER_GPU_Q6K_READONLY_OVERLAP_SNAPSHOT`,
  `PDOCKER_GPU_DISABLE_PIPELINE_OPTIMIZATION`,
  `PDOCKER_VULKAN_HEAP_BYTES`, `PDOCKER_VULKAN_MAX_BUFFER_BYTES`,
  `GGML_VK_FORCE_MAX_BUFFER_SIZE`, `GGML_VK_FORCE_MAX_ALLOCATION_SIZE`, and
  `GGML_VK_SUBALLOCATION_BLOCK_SIZE`.
- The compare driver must additionally forward diagnostic knobs that are too
  experimental or noisy to force into all UI/compose launches:
  `PDOCKER_GPU_CPU_ORACLE`, `PDOCKER_GPU_STRICT_PASSTHROUGH`,
  `PDOCKER_GPU_STRICT_RECONCILIATION`,
  `PDOCKER_GPU_STRICT_DEVICE_LOCAL_STAGING`,
  `PDOCKER_GPU_STRICT_DUPLICATE_DESCRIPTOR_NORMALIZATION`,
  `PDOCKER_GPU_LEGALIZE_WORKGROUP_SIZE_FROM_SPEC`,
  `PDOCKER_GPU_RETRY_MATERIALIZE_SPECIALIZATION`,
  `PDOCKER_GPU_SKIP_UNUSED_DESCRIPTOR_TRANSFERS`,
  `PDOCKER_GPU_USE_SPIRV_DESCRIPTOR_ACCESS`,
  `PDOCKER_VULKAN_DISABLE_16BIT_STORAGE`, and
  `PDOCKER_VULKAN_SUBGROUP_SIZE`.
- Promotion rule: once a diagnostic knob becomes required for ordinary
  correctness, promote it into `_gpu_env(state)` and keep the compare driver
  forwarding it.  Do not leave correctness-critical behavior only in the
  ad-hoc compare script.

- Strict object-graph cache contract: `PDOCKER_GPU_STRICT_GRAPH_CACHE` controls
  reuse of executor-owned strict Vulkan memory/buffer object graphs.  It is a
  production-safe performance knob only after correctness is unchanged: cache
  hits must preserve descriptor bytes, API memory/buffer IDs, offsets, ranges,
  and writeback ownership.  Artifacts must expose
  `strict_object_graph.cache_enabled`, `cache_hit`, `cache_adopted`,
  `cache_key`, `cache_bytes`, and `cache_disabled_reason` so stale or partial
  environment propagation cannot be mistaken for a real cache result.
- Regression guard: `scripts/llama-gpu-env-manifest.json` is the single
  manifest for UI/compose runtime defaults, pdockerd runtime defaults,
  compare-only diagnostic forwarding, full compare env forwarding, and executor
  reflection fields.  Since `d5ce2e8`, pdockerd loads the packaged manifest at
  startup (falling back to the old literals only when the manifest is absent),
  and the Android asset/copy path packages the same manifest beside the daemon.
  The compare driver and artifact verifier both load this file;
  `tests.test_gpu_abi_contract` checks the verifier constants derived from it,
  so future edits cannot silently drop one side of the bridge.


### Env bridge contract inventory (2026-05-23)

Adding a name to `scripts/llama-gpu-env-manifest.json` only proves that the
compare/pdockerd container payload can carry the variable.  It does **not** prove
that the persistent Android executor process can observe it.  Current manifest
env keys are classified as follows:

| Class | Env keys | Contract |
|---|---|---|
| `container_env_only` | `PDOCKER_VULKAN_HEAP_BYTES`, `PDOCKER_VULKAN_MAX_BUFFER_BYTES`, `GGML_VK_FORCE_MAX_BUFFER_SIZE`, `GGML_VK_FORCE_MAX_ALLOCATION_SIZE`, `GGML_VK_SUBALLOCATION_BLOCK_SIZE`, `PDOCKER_VULKAN_ICD_DEBUG`, `PDOCKER_VULKAN_ICD_TRACE_ALLOC`, `PDOCKER_VULKAN_ALIAS_COPIES`, `PDOCKER_VULKAN_DUMP_SPIRV_DIR`, `PDOCKER_VULKAN_ENABLE_8BIT_STORAGE`, `PDOCKER_VULKAN_ENABLE_16BIT_STORAGE`, `PDOCKER_VULKAN_ENABLE_INT64`, `PDOCKER_VULKAN_ENABLE_SUBGROUP_ARITHMETIC`, `PDOCKER_VULKAN_SUBGROUP_SIZE`, `PDOCKER_VULKAN_ADVERTISEMENT_SOURCE`, `PDOCKER_GPU_VIRTUAL_MEMORY`, `PDOCKER_GPU_VIRTUAL_MEMORY_MIN_BYTES`, `LLAMA_ARG_N_GPU_LAYERS` | Consumed by llama.cpp/container scripts or the glibc ICD before command emission; no executor reflection is expected. |
| `icd_to_executor_bool_option` | `PDOCKER_VULKAN_DISABLE_8BIT_STORAGE`, `PDOCKER_VULKAN_DISABLE_16BIT_STORAGE`, `PDOCKER_VULKAN_DISABLE_SUBGROUP_ARITHMETIC`, `PDOCKER_GPU_REWRITE_DUPLICATE_DESCRIPTOR_BINDINGS`, `PDOCKER_GPU_STRICT_DUPLICATE_DESCRIPTOR_NORMALIZATION`, `PDOCKER_GPU_MATERIALIZE_DESCRIPTOR_ALIASES`, `PDOCKER_GPU_MATERIALIZE_SPIRV_SPECIALIZATION_CONSTANTS`, `PDOCKER_GPU_DISABLE_PIPELINE_OPTIMIZATION`, `PDOCKER_GPU_STRICT_PASSTHROUGH`, `PDOCKER_GPU_STRICT_RECONCILIATION`, `PDOCKER_GPU_STRICT_DEVICE_LOCAL_STAGING`, `PDOCKER_GPU_SKIP_UNUSED_DESCRIPTOR_TRANSFERS`, `PDOCKER_GPU_USE_SPIRV_DESCRIPTOR_ACCESS`, `PDOCKER_GPU_DISABLE_OVERLAP_ALIASING`, `PDOCKER_GPU_CPU_ORACLE`, `PDOCKER_GPU_Q6K_ORACLE_WRITEBACK`, `PDOCKER_GPU_Q6K_SAFE_KERNEL`, `PDOCKER_GPU_Q6K_COMPAT_REWRITES`, `PDOCKER_GPU_Q6K_READONLY_OVERLAP_SNAPSHOT`, `PDOCKER_GPU_Q4K_SAFE_KERNEL`, `PDOCKER_GPU_Q4K_TARGETED_SPECIALIZATION`, `PDOCKER_GPU_Q4K_PIPELINE_RETRY_LADDER`, `PDOCKER_GPU_RESIDENT_CACHE`, `PDOCKER_GPU_MUTABLE_BUFFER_CACHE`, `PDOCKER_GPU_WRITEONLY_BUFFER_CACHE`, `PDOCKER_GPU_WRITEONLY_DIRTY_PROBE`, `PDOCKER_GPU_WRITEONLY_DIRTY_WRITEBACK`, `PDOCKER_GPU_STRICT_GRAPH_CACHE`, `PDOCKER_GPU_ADD_FLOAT16_CAPABILITY_FOR_STORAGE16`, `PDOCKER_GPU_DISPATCH_PROFILE_RESPONSE` | ICD appends a command-token boolean (or the existing `profile=1` token) and executor JSON must expose the effective value. |
| `icd_to_executor_size_option` | `PDOCKER_GPU_RESIDENT_CACHE_MIN_BYTES`, `PDOCKER_GPU_MUTABLE_BUFFER_CACHE_MAX_BYTES`, `PDOCKER_GPU_WRITEONLY_DIRTY_PROBE_MIN_BYTES` | ICD appends a parsed unsigned-size command token; malformed values are ignored rather than guessed. |
| `icd_to_executor_string_option` | _none today_ | Future string/path options need a bounded, escaped command field plus executor reflection; container env alone is not enough. |
| `app_process_only` | `PDOCKER_GPU_DISABLE_ANDROID_VULKAN`, `PDOCKER_GPU_DISABLE_ANDROID_OPENCL`, `PDOCKER_ANDROID_OPENCL_LIBRARY` | Read by the APK/executor process before or outside per-dispatch Vulkan command emission. Forwarding these only into the container is not a reliable override. |
| `deprecated_or_invalid` | _none in the manifest env set_ | Keep unsupported work tokens out of env classification. |
| `needs_bridge` | `PDOCKER_GPU_LEGALIZE_WORKGROUP_SIZE_FROM_SPEC`, `PDOCKER_GPU_RETRY_MATERIALIZE_SPECIALIZATION`, `PDOCKER_GPU_DISPATCH_PROFILE_LOG`, `PDOCKER_GPU_FAILED_SPIRV_DIR`, `PDOCKER_GPU_CHAIN_COMPAT_FEATURE_STRUCTS`, `PDOCKER_GPU_UNSAFE_DIRTY_WRITEBACK_CACHE`, `PDOCKER_GPU_WRITEBACK_FULL_HASH_MAX_BYTES` | Manifest forwarding can make these look requested, but the current executor-side behavior still depends on APK-process `getenv()` or an unreflected default. Do not interpret a run as having honored these until a dispatch option and JSON reflection exist. |

`needs_bridge` priority, highest risk first:

1. `PDOCKER_GPU_LEGALIZE_WORKGROUP_SIZE_FROM_SPEC` - directly controls the
   active Q6 WorkgroupSize lane.  Until Aquinas lands a reflected bool option,
   a container-only setting must not be used as proof that executor legalization
   changed.
2. `PDOCKER_GPU_RETRY_MATERIALIZE_SPECIALIZATION` and
   `PDOCKER_GPU_CHAIN_COMPAT_FEATURE_STRUCTS` - can change shader/module
   creation paths and feature-chain interpretation, so stale executor defaults
   can invalidate SPIR-V blocker conclusions.
3. `PDOCKER_GPU_DISPATCH_PROFILE_LOG`, `PDOCKER_GPU_FAILED_SPIRV_DIR`, and
   `PDOCKER_GPU_WRITEBACK_FULL_HASH_MAX_BYTES` - affect evidence capture.  A
   missing bridge may look like missing Q6 evidence rather than a failed knob.
4. `PDOCKER_GPU_UNSAFE_DIRTY_WRITEBACK_CACHE` - safety gate for dirty-writeback
   caching; keep it fail-closed until it has an explicit reflected option.

String option design: add an ICD-to-executor string option only for bounded
ASCII/UTF-8 payloads, escape separators or length-prefix the value, cap it well
below `PDOCKER_GPU_MAX_COMMAND_BYTES`, and echo the accepted value in compact
executor JSON.  For path-like diagnostics such as `PDOCKER_GPU_FAILED_SPIRV_DIR`,
prefer a host/container path chosen by compare, bridged as `failed_spirv_dir=...`,
and rejected if empty, absolute/relative policy is violated, or it would
truncate the command.

- Lightweight env parity guard: `tests.test_llama_gpu_env_parity` checks that
  the manifest's pdockerd runtime env list, UI-compose runtime env list,
  compare diagnostic/forward env lists, and verifier constants stay in sync
  without running a device.  Compare-only Q6_K diagnostic knobs must remain out
  of the UI compose template until explicitly promoted to ordinary runtime
  behavior.
- Artifact guard: `scripts/verify-llama-gpu-artifact.py` treats failed
  `gpu.diagnostics.config_propagation` evidence as
  `config-propagation-mismatch` and blocks correctness/benchmark claims.  This
  catches cases where a compare command requested a diagnostic environment
  variable but executor dispatch evidence did not reflect it.
- Artifact verifier manifest guard: when compare emits config propagation
  checks, the verifier requires those checks to cover every env/field pair in
  `LLAMA_GPU_CONFIG_PROPAGATION_ENV_FIELDS`.  A stale compare script that omits
  a diagnostic env from the artifact is classified as
  `config-propagation-mismatch` even if the remaining checks say `pass`.
- Artifact verifier strictness update: compare artifacts now fail closed if
  `gpu.diagnostics.config_propagation.checks` is missing entirely.  This closes
  the stale-artifact hole where a run with no env reflection evidence could
  still inherit a later Q6_K/pass classification.
- Artifact responsibility-boundary guard: `config-propagation-mismatch` is
  classified before Q6_K local-size, writeback, or oracle evidence and reports
  `responsibility_boundary="env-propagation"`.  Once env propagation is trusted,
  Q6_K classifications keep separate `q6-local-size`, `q6-writeback`, and
  `q6-oracle` boundaries so an env mismatch cannot be mixed with
  oracle/writeback/local-size root-cause work.
- Unsupported GPU work gate: structured executor/oracle fields such as
  `status`, `latest_status`, `error`, `blocker_class`, or `classification`
  containing `unsupported`/`kernel-not-implemented-yet` are classified as
  `unsupported-gpu-work-accepted` and block correctness and benchmark claims.
  This keeps unsupported kernels/layouts from being hidden by served HTTP,
  speedup, or unrelated Q6_K summary fields.
- Executor-side fail-closed oracle gate: when `PDOCKER_GPU_CPU_ORACLE=1` is
  requested for a known llama shader candidate, pending or unsupported oracle
  statuses now stop the generic Vulkan dispatch with
  `stage=cpu-oracle-required`, `oracle_fail_closed=true`, `valid=false`, and
  an attached `cpu_oracle` report.  This specifically prevents the known
  fused RMS/RoPE pending path (`fused-rms-rope-oracle-pending`) and unsupported
  Q4/Q6 layouts from being recorded as `valid=true` bridge work.
- Artifact verifier fail-closed oracle gate: any structured artifact evidence
  containing `oracle_fail_closed: true`, `cpu-oracle-required`, or an
  `*-oracle-pending` status is classified as `oracle-fail-closed` and blocks
  correctness and benchmark claims.  A later HTTP response, Q6 summary, or
  speedup cannot override this.
- Artifact verifier web/API gate: compare artifacts must include the unchanged
  required `/completion` prompt sanity probe (`addition`, `2+3=`, expected
  prefix `5`) with HTTP status and content evidence.  Missing or mutated prompt
  evidence is classified as `api-prompt-sanity-missing`; a wrong answer can
  remain diagnostic but cannot be hidden by performance fields.
- Completion-readiness gate: `/v1/models` liveness is not enough.  The compare
  driver now records `gpu.service_readiness` with `/health`, `/v1/models`, and
  an unchanged one-token `/completion` probe before benchmarking.  If liveness
  passes but completion times out, the artifact is classified as
  `llama-completion-timeout`; it is evidence for ICD/executor dispatch
  boundary investigation, not a correctness or speed claim.
- Runtime-startup evidence gate: the llama entrypoint writes
  `/workspace/logs/llama-startup.json`, and compare artifacts embed it as
  `gpu.startup_diagnostics` while merging its post-profile environment into
  `gpu.runtime_env`.  Use this to detect stale profile/env propagation before
  changing Dockerfile, model, prompt, or llama.cpp.
- Dispatch lifecycle gate: when `PDOCKER_GPU_DISPATCH_PROFILE_LOG=1`, both the
  glibc ICD and Android executor emit compact `generic dispatch lifecycle`
  begin/stage/end records.  Compare artifacts summarize them under
  `gpu.diagnostics.dispatch_lifecycle`, including unmatched begin/end IDs.  If
  `/completion` stalls, inspect this boundary first to decide whether the wait
  is in ICD socket response, executor submit, fence wait, or writeback.
- Artifact verifier speedup-field gate: compare artifacts must carry
  `comparison.speedup`, `comparison.target_tokens_per_second`,
  `comparison.target_met`, plus the matching `bridge_overhead_phase` CPU/GPU
  tokens-per-second and speedup fields.  The CPU run itself may be skipped or
  reused during tuning, but without CPU baseline evidence the verifier keeps
  `benchmark_claim_allowed=false`.

### 2026-05-21 Q6_K evidence-capture and WorkgroupSize update

Latest device evidence before this patch:

- `docs/test/llama-gpu-ngl1-q6-valid-json-adb33619-20260521T220914Z.json`
- `/health`, `/v1/models`, and `/completion` were reachable, but the required
  deterministic prompt returned `Marvel` for `2+3=` instead of `5`.
- Runtime freshness passed and executor markers were fresh.
- Q6_K/final projection hash `0x1bf751845c5dce75` was reached with
  `q6_dispatch_seen=true`.
- The old log merger duplicated durable engine/workspace log records, causing
  API/executor reconciliation to report `ambiguous` even when the duplicated
  records were byte-identical.
- The executor previously emitted non-finite diagnostic doubles as JSON
  `inf`, which caused the compare driver to drop the Q6 oracle response.
- After fixing JSON emission, the artifact exposed a real Q6 blocker:
  `blocker_class=workgroup-shape`,
  `spirv_local_size=[1,1,1]`,
  `spirv_local_size_resolved=[1,1,1]`, while the Q6 specialization entries
  carried the effective workgroup tuple `[32,1,1]`.

Structural fixes now in the bridge:

- The compare driver deduplicates identical executor JSON events after merging
  multiple durable log sources.  This keeps crash-safe log collection without
  weakening the verifier's ambiguity checks for genuinely different duplicate
  dispatches.
- The executor emits valid JSON for non-finite Q6 diagnostic doubles by writing
  `null` instead of `inf`/`NaN`.
- SPIR-V summary now resolves `BuiltIn WorkgroupSize` specialization-constant
  composites, not only `OpExecutionModeId LocalSizeId`.  This is required for
  shaders that declare literal `LocalSize 1,1,1` but use Vulkan specialization
  constants to carry the actual workgroup shape.
- `PDOCKER_GPU_LEGALIZE_WORKGROUP_SIZE_FROM_SPEC=1` can now patch that literal
  local size from the `BuiltIn WorkgroupSize` specialization tuple.  This is a
  bridge-side Vulkan compatibility legalization; it does not change llama.cpp,
  Dockerfiles, model files, prompts, descriptor bytes, or tensor data.
- `source_spirv_hash` remains the original container-provided shader hash even
  when the bridge applies the compatibility legalization, so known-hash Q6/Q4
  diagnostics are not lost after an effective shader hash changes.

Next device run once ADB is available:

```bash
ANDROID_SERIAL=<device> \
PDOCKER_GPU_CPU_ORACLE=1 \
PDOCKER_GPU_DISPATCH_PROFILE_LOG=1 \
PDOCKER_GPU_DISPATCH_PROFILE_RESPONSE=1 \
bash scripts/android-llama-gpu-compare.sh \
  --gpu-only \
  --cpu-tps 0.04702448956650603 \
  --cpu-ctx 512 \
  --gpu-ctx 512 \
  --gpu-layers 1 \
  --predict 4 \
  --repeat 1 \
  --out docs/test/llama-gpu-ngl1-q6-workgroup-legalized-<device>-$(date -u +%Y%m%dT%H%M%SZ).json
```

Expected acceptance for the next run:

- `gpu.diagnostics.q6_workgroup_diagnostics.local_size_resolved == [32,1,1]`.
- `local_size_patched == true` appears in Q6 executor evidence when the source
  module uses the `BuiltIn WorkgroupSize` specialization path.
- The verifier should no longer classify the run as
  `q6-oracle-capture-missing` or reconciliation-ambiguous solely due to
  duplicate merged log lines.
- If the prompt still fails, the next blocker must be a concrete Q6 oracle,
  writeback, synchronization, or output-layout class with valid JSON evidence.

### Stage 5: Correctness gate for `ngl=1`

Purpose: make one real offloaded layer safe before increasing GPU layer count.

Procedure:

1. Run `--gpu-layers 1 --predict 4 --repeat 1`.
2. Keep `PDOCKER_GPU_CPU_ORACLE=1` and profile response enabled.
3. Check deterministic `/completion` probes.
4. Check all known shader oracles.

Pass criteria:

- `gpu.correctness.summary.correctness == "pass"`.
- `required_failures == 0`.
- `benchmark_claim_allowed == true`.
- No known oracle candidate reports `status == "mismatch"`.
- `next_blocker` no longer says correctness probes do not match.

Fail criteria:

- Required `2+3=` probe fails.
- Any known oracle reports mismatch.
- The run is served but reports only performance without correctness.

### Stage 6: Performance work after correctness

Purpose: move from "correct but slow" to useful speedup.

Procedure:

1. Only start after `ngl=1` correctness passes.
2. Measure profile fields:
   - upload/copy/writeback counts and bytes,
   - dispatch count,
   - resident/mutable buffer cache hits,
   - guarded/resident page stats,
   - wall time per prompt.
3. Prefer reducing bridge crossings and copies before adding more kernels.
4. Re-run correctness after each optimization.

Pass criteria:

- Correctness still passes.
- Speedup improves against the same CPU baseline.
- Artifacts record `target_met`, speedup, GPU layers, blocker, and profile
  summary.

Target gates:

| Gate | Required |
|---|---:|
| Early correctness gate | `ngl=1` pass |
| Useful first speed gate | `>= 3x` with correctness pass |
| Project target | `>= 10x` with correctness pass |

## Handoff Notes For GPT-5.3 Codex Spark

Spark should operate as a focused executor, not as a broad replanner.  Use this
loop:

1. Read this file, then read only the latest tail of
   `docs/test/LLAMA_GPU_CORRECTNESS_20260507.md`.
2. Work on exactly one shader hash or one acceptance criterion per turn.
3. Make the smallest code change needed.
4. Run the fast local checks.
5. Install APK and run one device compare.
6. Summarize:
   - commit hash,
   - artifact path,
   - speedup,
   - correctness summary,
   - oracle status per relevant hash,
   - next blocker.

Spark should not:

- edit broad docs unrelated to llama GPU,
- change llama.cpp, Dockerfile, model, or prompt probes to make a test pass,
- add unbounded CPU oracles,
- commit unrelated untracked old evidence files,
- claim success from `served == true` alone.

Suggested first Spark task:

```text
Continue the Q6_K strict-passthrough blocker for 0x274f68a67dfef210.  Do not
modify llama.cpp, Dockerfiles, the model, or prompt probes.  Acceptance:
preserve the row-indexed writeback/workgroup-shape evidence from
docs/test/llama-gpu-ngl1-q6-row-provenance-20260519.json, then narrow
native-q6-device-execution-or-final-store to either executor/Vulkan device
execution or final output store. A rerun that loses row-indexed writeback
verification or workgroup-shape clarity is a setup/regression artifact, not
progress.
```

If Spark gets lost, it should run:

```bash
git log --oneline -5
git status --short
python3 -m unittest tests.test_gpu_abi_contract
```

Then resume from the newest committed artifact listed in this document.

## When Spark Should Escalate To GPT-5.5

Spark may continue while the work is a bounded implementation or evidence
collection loop.  It should explicitly recommend switching to GPT-5.5 when the
task stops being a narrow patch and becomes ambiguous architecture, algorithm
design, or cross-system debugging.

### Stay On Spark

Continue with GPT-5.3 Codex Spark when all of these are true:

- The target is one known file or a small, declared file set.
- The target shader hash and acceptance condition are already named.
- The change is a hash-gated oracle, JSON/report field, docs update, or small
  regression test.
- The next command is obvious from this document.
- Failure is local and reproducible with one compare artifact.

Examples:

- Add a bounded oracle for one known SPIR-V hash.
- Add a JSON field to `cpu_oracle`.
- Update `LLAMA_GPU_CORRECTNESS_20260507.md` with a new artifact.
- Run the next `ngl=1` compare and summarize the blocker.

### Switch To GPT-5.5

Recommend switching to GPT-5.5 before continuing if any of these are true:

- Two consecutive compare artifacts contradict the expected blocker class.
- A fix would require changing the bridge architecture, descriptor ownership
  model, persistent buffer protocol, or command queue design.
- The next step needs a new SPIR-V interpreter subset instead of a single
  hash-gated oracle.
- The suspected bug crosses three or more layers, for example ICD descriptor
  rewrite + executor aliasing + Android Vulkan memory visibility.
- The issue involves large buffers where memory safety, OOM behavior, or
  virtual-memory techniques must be reasoned about.
- The work might relax a correctness gate, alter benchmark prompts, rebuild
  llama.cpp, or change user-visible product semantics.
- Spark cannot explain why a change should fix the observed artifact before
  making the change.
- Spark is about to make broad speculative edits, especially in both
  `docker-proot-setup/src/gpu/` and `app/src/main/cpp/`.

Escalation message template:

```text
Switch to GPT-5.5 recommended.

Reason:
- <specific trigger from the list above>

Current evidence:
- latest artifact: <path>
- correctness: <pass/fail>
- speedup: <value>
- relevant hashes: <hash list>
- suspected layer: <ICD/executor/Vulkan memory/model/prompt/etc.>

Safe resume point:
- last commit: <git hash>
- next decision needed: <precise design question>
```

### Automatic Stop Rule

Spark must stop and ask for a GPT-5.5 handoff if it is considering a change
that could make a failing test pass by weakening the test instead of fixing the
bridge.  Examples include changing prompts, disabling correctness probes,
lowering required checks, hiding a shader hash from diagnostics, or treating
`served=true` as success.

## 2026-05-23 Update: Q6 safe-kernel path clears correctness

Latest validated artifact:

- `docs/test/llama-gpu-ngl1-q6-safe-kernel-adb44443-20260523T112715Z.json`

Outcome:

- `q6-workgroup-cleared-and-oracle-match`.
- API prompt sanity passed: deterministic `2+3=` returned `5`.
- Q6 source hash `0x1bf751845c5dce75` was replaced by bridge-owned safe kernel
  hash `0x7ec0292e948c9b41` under `PDOCKER_GPU_Q6K_SAFE_KERNEL=1`.
- Q6 oracle matched with `mismatch_count=0` and row-indexed writeback evidence
  passed.
- Speedup was `1.1976089878024805x` versus the CPU baseline, below the current
  10x target.

Planning implications:

1. For llama.cpp b9030 Q6_K, expected local size is `[32,1,1]`; specialization
   constant `1` is `NUM_ROWS`, not `WorkGroupSizeY`.  Treat older `[32,2,1]`
   requirements as stale diagnostic assumptions.
2. Commit `ac40e49` and the safe-kernel artifact establish that the bridge can
   carry descriptor data, execute a bridge-owned compatibility kernel, write
   back the sampled Q6 outputs, and satisfy the unchanged prompt gate for
   `ngl=1`.  They do **not** establish native llama.cpp Q6 shader correctness,
   a product performance win, or permission to tune by trial and error.
3. The safe-kernel path must remain labelled as a bridge-owned compatibility
   substitution: the original llama.cpp shader source, Dockerfile, model,
   prompt, and tensor bytes are unchanged, while the Skydnir bridge substitutes
   the driver-facing compute kernel for a known Q6 dispatch shape.
4. The next phase is a static-invariant implementation phase, not
   "run variants until one passes".  Before code changes, derive and document
   the expected data flow from:
   - llama.cpp Vulkan dispatch metadata: source hash, descriptor set/binding
     roles, descriptor offsets/ranges, push constants, specialization constants,
     and output indices;
   - the ICD command ABI: how container Vulkan object identity, memory/buffer
     offsets, descriptor updates, specialization data, and safe-kernel selection
     are serialized to the executor;
   - the executor object graph: Android `VkDeviceMemory`/`VkBuffer` identity,
     upload ranges, descriptor set layout, dispatch module choice, barriers,
     staging/download, and fd writeback.
5. Only after those static invariants are written down and matched against the
   `ac40e49` artifact may implementation proceed.  The implementation target is
   to preserve the proven bridge data-flow contract while making the
   compatibility substitution explicit and auditable, not to mutate prompts,
   Dockerfiles, llama.cpp, or verifier gates.

Acceptance criteria before `ngl=2` or performance tuning:

- A static invariant note identifies every Q6 input/output buffer boundary from
  llama.cpp dispatch through ICD command tokens to executor objects and
  writeback, including which fields prove source hash `0x1bf751845c5dce75` and
  safe-kernel hash `0x7ec0292e948c9b41` are intentionally related.
- The safe-kernel decision is reflected in executor JSON as a compatibility
  substitution with original source hash retained; artifacts must not look like
  llama.cpp emitted a different shader.
- Prompt sanity remains unchanged (`2+3=` expected prefix `5`), runtime
  freshness/config propagation pass, Q6 oracle status is `match`, row-indexed
  writeback is verified, and `benchmark_claim_allowed` is true for `ngl=1`.
- Native Q6 SPIR-V mismatch remains separately visible as a compatibility
  blocker; it must not be hidden behind the safe-kernel success.
- No acceptance path depends on `served=true`, `/health`, `/v1/models`, speedup,
  missing diagnostics, or weakened verifier classification.

### 2026-05-23 Update: Q6 safe-kernel transfer pruning

The first performance change after `ac40e49` is now constrained to the proven
Q6 safe-kernel lane.  In strict passthrough mode the executor still preserves
the application descriptor object graph: descriptor sets, `VkBuffer` identity,
offsets, ranges, and descriptor writes are not removed or rewritten for native
llama.cpp shaders.  When `PDOCKER_GPU_Q6K_SAFE_KERNEL=1` selects the bridge-owned
safe kernel, the executor may use SPIR-V reflection only to prune byte
transfers:

- undeclared safe-kernel bindings stay bound for ABI fidelity but do not upload
  or write back bytes;
- read-only safe-kernel bindings remain uploaded but skip writeback;
- the output binding must remain writable, and input bindings must remain
  readable, otherwise dispatch fails closed.

Executor JSON now exposes
`safe_kernel_reflection_transfer_pruning`,
`effective_skip_unused_descriptor_transfers`, and
`effective_spirv_descriptor_access` so runtime artifacts can prove that the
optimization came from the audited safe-kernel contract, not from a broad
native-shader heuristic.

Next device validation, once ADB is available, must compare the new artifact
against `docs/test/llama-gpu-ngl1-q6-safe-kernel-adb44443-20260523T112715Z.json`
and check these fields before interpreting throughput:

- `safe_kernel_reflection_transfer_pruning == true`;
- `effective_skip_unused_descriptor_transfers == true`;
- `effective_spirv_descriptor_access == true`;
- binding 0/1 remain readable and skip writeback;
- undeclared safe-kernel bindings remain present in descriptor evidence but
  have zero transfer intent;
- prompt sanity remains `2+3=` -> `5` and Q6 oracle remains `match`.

If these hold, the next static performance target is output-range narrowing for
binding 2, followed by resident/read-only buffer caching.  Do not increase
`ngl` or change the model/prompt/Dockerfile until this transfer-pruning evidence
is recorded.

### 2026-05-23 Update: SPIR-V dataflow/origin tooling

Latest implementation commits:

- `59b0a4e` - probe replay guard hardening.
- `ab3b24b` - entry point, push constant, and descriptor dataflow exposure in
  `scripts/analyze-spirv.py`.
- `e42ce9e` - pointer-origin tracking for loads, stores, and access chains.
- `14b14fc` - `scripts/compare-spirv-dataflow.py`.

Purpose:

- Replace trial-and-error shader debugging with a static ABI/dataflow
  comparison loop.
- Keep native Q6 SPIR-V, safe-kernel SPIR-V, and any instrumented probe module
  explicitly related by hashes, manifests, and structural analysis.
- Prevent "update漏れ / reflection漏れ / env反映漏れ" style regressions by
  making the expected dataflow visible before device execution is interpreted.

Current safe baseline:

- `docs/test/spirv-q6k-safe-current/q6k-safe.analysis.json`
- `docs/test/spirv-q6k-safe-current/q6k-safe.probe.json`

Known limitation:

- This is structural analysis, not a full SPIR-V decompiler or GLSL source
  reconstruction.
- Native Q6 comparison is not complete until the device run produces a real
  `.spv` dump for the original llama.cpp Q6 source module.  Do not infer native
  Q6 correctness from the safe baseline.

Next concrete action when ADB is available:

1. Run a diagnostic compare with `PDOCKER_GPU_SPIRV_DUMP_DIR` set.
2. Identify the native Q6 dump for source hash `0x1bf751845c5dce75`.
3. Run `scripts/analyze-spirv.py` on that native dump.
4. Run `scripts/compare-spirv-dataflow.py` between the safe baseline and the
   native analysis.
5. If entry/descriptors/push constants/output stores diverge, fix the bridge's
   ABI understanding before executing more GPU trials.
6. If static dataflow matches, the next blocker is dynamic: Android Vulkan
   execution, synchronization, memory visibility, writeback, or a valid-module
   instrumentation probe.

### 2026-05-24 Update: Q6 strict-passthrough scoping and reflection transfer intent

Latest device artifacts on `192.168.179.21:46565`:

- `docs/test/llama-gpu-ngl1-q6-specialized-adb46565-20260524T153925Z.json`
- `docs/test/llama-gpu-ngl1-q6-scoped-specialization-adb46565-20260524T155335Z.json`
- `docs/test/llama-gpu-ngl1-q6-legalize-before-materialize-adb46565-20260524T160750Z.json`
- `docs/test/llama-gpu-ngl1-q6-reflection-access-adb46565-20260524T162113Z.json`

Findings:

1. Global `PDOCKER_GPU_MATERIALIZE_SPIRV_SPECIALIZATION_CONSTANTS=1` is too
   broad.  It reached a non-Q6 shader (`0x7bf05c459ac87f2b`) and produced a
   `VK_ERROR_DEVICE_LOST` submit failure before Q6 evidence.  The executor now
   scopes specialization materialization to known Q6 hashes or an instrumented
   probe whose `source_spirv_hash` maps back to Q6.
2. Q6 `LocalSize` legalization remains cleared: the Q6 probe reports
   `local_size == local_size_resolved == [32, 1, 1]`, and the workgroup-shape
   blocker remains false.
3. Specialization materialization is requested for Q6 but currently does not
   rewrite the module (`specialization_materialized == false` on the Q6 probe),
   so the Vulkan specialization payload is still passed to the driver.  Do not
   treat materialization as a completed correctness fix until the materializer
   exposes a changed effective hash or an explicit skip reason.
4. Native strict passthrough now uses SPIR-V access qualifiers for transfer
   intent while preserving all descriptor bindings.  This corrects the evidence
   model for Q6: binding 2 is write-only, bindings 3/4 are read-only, and the
   executor no longer reports all native bindings as read-write solely because
   their backing ranges alias.
5. Correctness is still not achieved.  `/health` and `/v1/models` pass, but the
   deterministic prompt probe (`2+3=`) still returns an incorrect token
   (`"Marvel"`/similar), and Q6 remains classified at the native final-store /
   device-execution boundary.

Current blocker:

- Q6 shader-like CPU oracle and native reduction-tree oracle are internally
  consistent, but Android Vulkan execution writes different values to the output
  range.  Writeback from GPU memory to the container is verified, so the next
  investigation must focus on descriptor/object-graph semantics, feature-chain
  enablement, memory visibility/barrier scope, or a driver-facing SPIR-V
  semantic mismatch.  Do not change llama.cpp, the Dockerfile, model, or prompt.

Next concrete actions:

1. Re-run Q6 with the new executor-side
   `specialization_materialize_report` evidence.  This report records the
   materializer's exact decision path (`failure_reason`, folded spec constants,
   folded composites, folded spec ops, first unsupported opcode/spec-op, output
   word count, and whether the WorkgroupSize spec subtree was preserved).  Use
   it to decide whether Q6 is still passing live specialization data to the
   Android driver because of an unsupported SPIR-V expression, a guarded
   WorkgroupSize subtree, or a no-op rewrite.
   The skip guard is now intentionally conditional: WorkgroupSize composite
   operands are skipped only while the pre-materialized module still has an
   inconsistent literal/specialized workgroup shape.  After LocalSize
   legalization makes the literal shape match the requested specialization, the
   WorkgroupSize subtree is allowed to fold with the rest of the Q6 module.
   The run must follow the pre-flight matrix in
   [`../design/VULKAN_BRIDGE_PROBE_MATRIX.md`](../design/VULKAN_BRIDGE_PROBE_MATRIX.md):
   the expected artifact path, required evidence fields, pass branch, and fail
   branches must be named before ADB is requested.
   Use `scripts/plan-llama-gpu-q6-run.py --out docs/test/llama-gpu-q6-preflight-plan-latest.json`
   to generate that run plan without touching ADB.
2. Compare Q6 descriptor/access evidence before and after reflection transfer
   intent to ensure no application-visible descriptor write was removed.
3. Run one targeted device-local staging diagnostic only after static evidence
   is recorded; its purpose is to split memory visibility/coherency from shader
   arithmetic, not to tune performance.
4. If staging does not change Q6 output, continue with static SPIR-V dataflow
   around the two final `OpStore` paths into binding 2 and the relevant push
   constants (`push[7]` fuse flags, output base, row/column strides).

### 2026-05-30 Update: effective Q6 SPIR-V lineage is now statically reproducible

Latest promoted device artifact:

- `docs/test/llama-gpu-ngl1-q6-workgroup-adb46015-20260530T232458Z.json`

The stale-executor marker issue was closed for this lane in the promoted
artifact with marker `gpu-executor-q6-descriptor-invariants-20260530`.
Descriptor/readback invariants are present and true.  Q6 is still not correct:
`/completion` returns `" Marvel"` for the deterministic sanity prompt, so no
performance claim is allowed.  The next executor build marker is
`gpu-executor-q6-readonly-snapshot-20260531`; a fresh device artifact must
show that marker before interpreting the new overlap-snapshot evidence.

The effective native Q6 probe module is now reproducible offline with
`scripts/reconstruct-q6-effective-spirv.py`:

```bash
scripts/reconstruct-q6-effective-spirv.py \
  /tmp/q6write10-bundle/native-q6.write.spv \
  --artifact docs/test/llama-gpu-ngl1-q6-workgroup-adb46015-20260530T232458Z.json \
  --out-spv /tmp/q6-effective-0x2abe8e79566aa67a.spv \
  --out-json docs/test/llama-gpu-q6-effective-lineage-adb46015-20260530.json
```

Static lineage:

1. source/instrumented Q6 module: `0xd2d7fbedceb5a8a6`, `7797` words.
2. literal `LocalSize 1,1,1` legalized from WorkgroupSize SpecId evidence to
   `LocalSize 32,1,1`: `0x4c00be09530ea2db`.
3. specialization constants materialized with `{0:32, 1:2, 2:1}`:
   `0xab97bf7e13302b50`, `7773` words; folded 4 spec constants, 1 spec
   composite, and 4 supported spec ops.
4. strict duplicate descriptor normalization rewrites target id `371` from
   binding `0` to first free binding `6`, producing the runtime effective hash
   `0x2abe8e79566aa67a`.

This means the next blocker is not "unknown effective bytes."  It is the
native Q6 final value path in the effective module.  Current evidence says:

- row-indexed output writeback is verified,
- descriptor offset/range invariants are verified,
- native-vs-writeback samples preserve the native GPU value,
- output-layout remapping has no stable alternate mapping,
- shader-like and native reduction-tree CPU oracles are internally consistent,
- but the actual Android Vulkan final output value differs from the oracle.

Next static target before another ADB run:

1. Use the reconstructed effective module to trace the value feeding the final
   binding-2 `OpStore`:
   `%1873 -> %1874 -> %1875 -> OpStore`, including the Workgroup `%143`
   reduction and optional fuse-add paths gated by `push[7]`.
2. Verify that debug/probe stores to binding 5 are post-store observation only
   and do not feed back into `%143`, `%1874`, or binding 2.
3. If the static value path is coherent, the next implementation must be a
   generic driver-compatibility lowering for the proven semantic boundary
   (workgroup-memory/barrier/final-store behavior), not a hash-only safe kernel
   and not a llama.cpp change.

### 2026-05-31 Update: strict read-only overlap snapshot is implemented

Static review of the promoted Q6 artifact found that binding 2 is the writable
final output while bindings 3 and 4 are read-only optional fuse inputs over the
same `api_memory_id`, `api_buffer_id`, offset, and range.  The shader gates the
optional fuse reads with `push[7] == 0`, but the Android driver still sees a
single dispatch with read/write storage-buffer aliasing over the same window.

The executor now implements a narrowly gated compatibility lowering under the
existing `PDOCKER_GPU_DISABLE_OVERLAP_ALIASING` switch:

- only strict passthrough dispatches are eligible;
- only active read-only bindings that overlap an active writable binding with
  the same API memory and buffer identity are snapshotted;
- the writable binding stays on the strict object graph and remains the only
  writeback source;
- the read-only snapshot preserves descriptor offset and range by allocating a
  temporary Vulkan buffer large enough for `api_offset + api_range`;
- llama.cpp, Dockerfile, model, prompt, SPIR-V source bytes, and tensor bytes
  are not changed.

Fresh artifacts must expose:

- `executor_build_marker == gpu-executor-q6-readonly-snapshot-20260531`;
- `strict_object_graph.readonly_overlap_snapshots`;
- `strict_object_graph.readonly_overlap_snapshot_bytes`;
- per-binding `readonly_overlap_snapshot`,
  `readonly_overlap_source_index`, and `readonly_overlap_snapshot_bytes`.

Interpretation rules:

1. If Q6 prompt correctness passes with snapshots enabled, the previous native
   Q6 failure depends on read/write descriptor aliasing in the Android Vulkan
   execution path.  Then measure the snapshot overhead and decide whether a
   better alias-preserving lowering is needed.
2. If Q6 still fails, the alias hypothesis is rejected for this lane and the
   next target remains the workgroup-memory/barrier/final-store path in the
   effective Q6 module.
3. This is not a benchmark success condition by itself.  Prompt correctness and
   verifier gates still decide whether any speed result is reportable.

Fresh device result:
`docs/test/llama-gpu-ngl1-q6-readonly-snapshot-192_168_43_47_34827-20260531T145546Z.json`
observed the required executor marker
`gpu-executor-q6-readonly-snapshot-20260531` and did materialize two
read-only overlap snapshots:

- `readonly_overlap_snapshot_policy.effective == true`;
- `strict_object_graph.readonly_overlap_snapshots == 2`;
- `strict_object_graph.readonly_overlap_snapshot_bytes == 1248256`;
- bindings 3 and 4 were snapshotted from the writable binding-2 storage
  window;
- `q6_readonly_dispatch_alias_side_effects == []`;
- `q6_unexpected_readonly_dispatch_mutations == []`.

The deterministic prompt still returned `" Marvel"` and the joined final-store
sample remained `native-final-store-mismatch`:

- `final_store_value_f32 == fd_after_writeback == 3.2279610633850098`;
- expected oracle value was `6.38452625`;
- executor writeback still matches the native GPU final store.

Therefore read-only descriptor overlap is rejected as the sufficient root cause
for this Q6 lane.  The next target remains the native Q6 value path before the
binding-2 final store.  Static SPIR-V review also shows that `SpecId 1` is the
Q6 row-count dimension (`2`) and `SpecId 2` is the outer count dimension (`1`),
not `WorkgroupSize.y/z`; the final lane-0 store loops over both Q6 row slots.
Do not patch `SpecId 1` into LocalSize.y.

The compare parser now treats the existing Q6 debug binding as a staged trace,
not only as a final-store trace.  The known instrumented slots are:

- candidate 39 / role 1: tail pre-reduction store;
- candidate 49 / role 2: tail reduction store;
- candidate 61 / role 3: tail accumulator-A add store;
- candidate 63 / role 3: tail accumulator-B add store;
- candidate 64 / role 4: tail final store;
- candidate 105 / role 1: full pre-reduction store;
- candidate 115 / role 2: full reduction store;
- candidate 127 / role 3: full accumulator-A add store;
- candidate 129 / role 3: full accumulator-B add store;
- candidate 130 / role 4: full final store.

The next fresh run should use this staged trace to decide whether the first
device divergence is present before reduction, during reduction, or only at the
final lane-0 store.  This is still evidence collection; it is not a safe-kernel
replacement and it does not modify llama.cpp, Dockerfile, model, prompt, or
tensor bytes.

Offline guard:
`scripts/maintenance/analyze-q6-stage-trace-spvasm.py` statically checks the
instrumented SPIR-V disassembly for these debug binding-5 stage slots.  The
latest offline result
`docs/test/q6-stage-trace-static-analysis-latest.json` passes for
`/tmp/q6-effective-barrier.spvasm`: all ten expected stage records are present.
Non-final stage records carry candidate/role/value fields only; final-store
records additionally carry output index, workgroup/local invocation metadata,
and schema version 2.  The compare parser therefore must not reject non-final
stage records for lacking final-store metadata.
The same offline report now records the SSA producer for each traced value.  In
the current module the reduction and accumulator stage values are `OpFAdd`
results, while the pre-reduction and final-store values are loaded values that
are bitcast into the debug SSBO.  The next device run should compare these
stage values in order; the first divergent stage is the native Q6 value-path
boundary to inspect next.
For each traced value the report also includes a small `value_flow_context`
window from the SSA origin to the debug write.  This keeps the next analysis
anchored to SPIR-V data flow rather than hash-specific assumptions or
trial-and-error reruns.
For final-store records it also records the output-index SSA flow.  The current
tail/full final output indices originate from `OpIAdd` chains and are emitted as
`output_index_source_id` / `output_index_origin_*` in the offline report.  The
next device artifact must use only role-4 records for final-store boundary
joins; non-final records are stage evidence, not output-index evidence.

### 2026-06-01 Update: stale same-device HTTP evidence is rejected

An ADB run on `10.75.202.179:35875` confirmed that Q6 probe environment
propagation works when using `scripts/android-llama-gpu-q6-workgroup-run.sh`:
all `PDOCKER_GPU_SPIRV_PROBE_*` keys reached the runtime.  The run did not
produce valid GPU executor evidence because the newly created target container
stopped before readiness while a same-device HTTP request still reached an older
llama server on the same port.  That artifact was correctly non-terminal, but
the wait loop had accepted the stale HTTP response as readiness.

The compare script now fails closed in same-device HTTP mode: an HTTP 2xx
readiness response is accepted only while the selected target container is still
running.  If the target is not running, the wait event records
`stale-same-device-http-target-not-running` and refuses to use stale server
output as Q6 evidence.  The next device run must therefore either keep the
target container running or fail before prompt/evidence collection.

- 2026-06-03: Graphics V6.1 ABI now carries explicit dynamic-rendering replay data needed to avoid heuristic reconstruction: render area, layer count, view mask, pipeline dynamic-rendering attachment formats, descriptor `firstSet`, and command pipeline-layout id.  Producer fills these fields; executor validation distinguishes static pipeline vertex input bindings from actual bound vertex buffers.

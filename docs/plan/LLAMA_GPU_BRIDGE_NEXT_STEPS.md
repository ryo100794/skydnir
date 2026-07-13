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
| 2026-07-11 strict transport identity evidence gate | Strict pass-through evidence is now separated from diagnostic/compatibility Vulkan execution. The executor emits `strict_transport_identity_eligible` plus a reason and requires source/effective/received SPIR-V identity, unchanged shader bytes through pipeline creation, and no shader/pipeline reconstruction knobs before a run can be promoted as no-reconstruction pass-through evidence. The artifact verifier now treats an ineligible strict-transport record as diagnostic-only. | `app/src/main/cpp/pdocker_gpu_executor.c`; `scripts/verify-llama-gpu-artifact.py`; host test `tests.test_gpu_abi_contract`; native build `scripts/build-native-android-ndk.sh`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-11 V5 compute native-plan gate | The Android executor builds a `VulkanDispatchV5NativePlan` from V5/V5.1/V5.2 resource, descriptor, image, view, sampler, and V5.2 layout-range tables before execution-table materialization. This gate validates table shape and fail-closes frames that exceed the current V5 replay limits instead of silently narrowing them to legacy 16-slot assumptions. | `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract`; native build `scripts/build-native-android-ndk.sh`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-12 V5 compute native-plan materializer lane | The Android executor V5 handler no longer reparses the frame through a V4-named converter. `VulkanDispatchV5NativePlan` now owns both legacy V5 descriptors and V5.1/V5.2 object tables, and `materialize_vulkan_dispatch_v5_native_plan_bindings` consumes that plan as the single source of truth for buffer/image run bindings and object-table handoff to `run_vulkan_dispatch_fd`. This is a structural step toward table-native replay; the remaining execution core still uses the generic compute runner after plan materialization. | `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract`; native build `scripts/build-native-android-ndk.sh`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-11 compute descriptor-write heap lane | The legacy compute runner no longer uses fixed `VkWriteDescriptorSet[16]`, `VkDescriptorBufferInfo[16]`, or `VkDescriptorImageInfo[16]` arrays for descriptor updates. Descriptor-write staging is allocated from `binding_count + image_descriptor_count + binding_alias_count` and capped by the V5 descriptor-table maximum, removing the separate total-write cap that could reject valid mixed V5.1 buffer plus image/sampler frames even when each legacy bucket was within 16. | `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract`; native build `scripts/build-native-android-ndk.sh`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-11 ICD V5.1 frame heap tables | The container-side Vulkan ICD V5.1 frame builder no longer allocates resource, descriptor, image, image-view, sampler, or specialization tables as maximum-sized stack arrays. It allocates exactly the validated frame-table counts and cleans them through one cleanup path, and the local V5.1 send guard no longer narrows buffer/image descriptor counts to `PDOCKER_VK_MAX_STORAGE_BUFFERS`. This reduces stack pressure and removes one producer-side V5 table narrowing point; descriptor capture and layout storage are still separately capped by current ICD descriptor-state tables. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host test `tests.test_gpu_abi_contract`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-11 executor V5 handler heap tables | The Android executor V5 handler allocates its run-binding, image-descriptor, and fd tables from the already validated `VulkanDispatchV5NativePlan` counts instead of fixed `PDOCKER_GPU_MAX_VULKAN_BINDINGS` stack arrays. This removes another executor-side stack/narrowing point before the generic compute runner. Full table-native replay still requires replacing or further widening the remaining `run_vulkan_dispatch_fd` execution core. | `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract`; native build `scripts/build-native-android-ndk.sh`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-11 compute command-barrier heap lane | The Android executor legacy compute runner now heap-allocates its command-recording barrier tables from the active buffer/image descriptor counts instead of stack arrays fixed to `PDOCKER_GPU_MAX_VULKAN_BINDINGS`. This removes silent loop truncation around pre/post buffer barriers and image staging barriers and keeps error exits cleanup-safe. Descriptor layout/index caps and V5 table-native replay remain pending. | `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract`; native build `scripts/build-native-android-ndk.sh`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-11 sparse compute descriptor binding lane | The Android executor legacy compute runner now keeps descriptor layout metadata in heap-backed flattened tables sized by `descriptor_set_count * layout_count` and hashes them with an explicit layout stride. This removes the legacy 16-binding-number cap for frames that still have 16 or fewer buffer/image descriptors and descriptor array elements within the current legacy bucket. Descriptor count, array-element, object-table, duplicate-rewrite, and graphics V6 caps remain separate pending lanes. | `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract`; native build `scripts/build-native-android-ndk.sh`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-12 compute shader reflection heap lane | The Android executor legacy compute runner now allocates SPIR-V descriptor reflection tables (`shader_used_bindings` and `shader_binding_access`) from final `layout_count` instead of the legacy 16 binding slots. Sparse high binding numbers no longer look undeclared or lose NonReadable/NonWritable access metadata when descriptor count is still within the current legacy execution bucket. Descriptor count, array-element, object-table, duplicate-rewrite, and graphics V6 caps remain separate pending lanes. | `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract`; native build `scripts/build-native-android-ndk.sh`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-12 compute dispatch producer table lane | The container-side Vulkan ICD generic compute dispatch collector now stores its transient descriptor, image, image-view, sampler, and fd tables in one cleanup-owned heap record sized by the V5 transport table limits instead of stack arrays capped by `PDOCKER_VK_MAX_STORAGE_BUFFERS`. Buffer descriptors still fail closed at the real SCM_RIGHTS transport boundary (`PDOCKER_GPU_TRANSPORT_MAX_PASSED_FDS - 1` payload fds), but sparse/high API binding numbers and image-object metadata no longer hit an additional producer-side 16-slot temporary-table cap before V5/V5.1 frame selection. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host test `tests.test_gpu_abi_contract`; glibc payload build `scripts/build-gpu-shim.sh`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-12 executor compute work-table heap lane | The Android executor compute runner now allocates its per-dispatch binding, alias, strict-object, image-object, telemetry, dirty-probe, and coordinate tables from validated V5 counts instead of fixed 16-slot stack arrays. `materialize_vulkan_dispatch_images`, `create_strict_vulkan_object_graph`, and duplicate-descriptor rewrite now take explicit capacities, and the V5 native-plan gate accepts V5 table limits instead of rejecting anything above the legacy 16 slots. The strict-graph cache is widened by the follow-up heap-backed cache lane below. | `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract`; native build `scripts/build-native-android-ndk.sh`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-12 executor strict-graph-cache heap lane | The Android compute runner strict object graph cache no longer stores cached memory/buffer objects or key scratch memory in fixed `PDOCKER_GPU_MAX_VULKAN_BINDINGS` arrays. Cache entries now own heap-backed strict memory and buffer tables, cache-key scratch memory is sized by the validated binding table, cache adoption is centralized in one helper, and the key hashes the binding-table index so >64 active bindings do not rely on the diagnostic 64-bit active mask for identity. | `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract`; native build `scripts/build-native-android-ndk.sh`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-12 ICD descriptor-state heap lane | The container-side Vulkan ICD descriptor layout and descriptor-set state no longer stores binding slots in fixed 16-entry arrays. Layout metadata, immutable-sampler tables, and descriptor-set binding tables are allocated from the validated binding count, layout-backed slot counts no longer clamp to `PDOCKER_VK_MAX_STORAGE_BUFFERS`, descriptor updates stage through deep-cloned heap slots, and command-record descriptor snapshots are deep-cloned so dynamic offsets do not mutate live descriptor sets. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host test `tests.test_gpu_abi_contract`; glibc payload build `scripts/build-gpu-shim.sh`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-12 ICD secondary descriptor-snapshot lane | Secondary command-buffer append now carries heap-backed descriptor snapshots forward instead of fail-closing when a secondary command buffer contains compute dispatches, graphics draws, or descriptor-bind records that captured descriptor sets. The append path deep-clones each descriptor snapshot table, rebases existing command indices as before, and rolls back appended records plus duplicated update payloads on allocation failure. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host test `tests.test_gpu_abi_contract`; glibc payload build `scripts/build-gpu-shim.sh`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-12 legacy text dispatch cap cleanup | Legacy `VULKAN_DISPATCH_V1..V4` command construction now uses heap-backed text and parser binding storage, with the text fallback capped only by the real `SCM_RIGHTS` one-shader-fd-plus-binding-fds transport shape. Wide generic compute still promotes to V5 framed transport. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract`; native/glibc payload builds; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-12 graphics descriptor-write heap lane | Android graphics V6 descriptor replay now heap-allocates descriptor buffer/image info and write tables from the command descriptor count instead of `PDOCKER_GPU_MAX_VULKAN_BINDINGS`. This removes a descriptor-update staging ceiling for otherwise layout-valid graphics descriptor binds. | `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract`; native payload build; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-12 graphics descriptor-layout heap lane | Android graphics V6 replay no longer stores descriptor-set layout bindings, pipeline binding metadata, or V6.25 exact-coverage tracking in fixed `PDOCKER_GPU_MAX_VULKAN_BINDINGS` arrays. Descriptor bindings are tracked as heap-backed sparse binding records, V6.25 coverage uses expected-slot counting plus duplicate triple detection, and descriptor pool/layout creation consumes those records directly. | `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract`; native payload build; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-12 graphics attachment arena heap lane | Android graphics V6 attachment image/materialization state no longer stores image memory objects, images, image views, samplers, or the MSAA allow mask in fixed `PDOCKER_GPU_MAX_VULKAN_BINDINGS` arrays. Attachment replay now allocates those object arenas from the validated V6/V5 frame counts and removes the Android-side 16-image materialization ceiling while keeping validation bounded by the transport table limits. | `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract`; native payload build; APK and fast regression; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-12 graphics writeback/upload barrier heap lane | Android graphics V6 staged-image upload and attachment writeback no longer allocate command-recording image/buffer barrier tables as fixed 16-entry stack arrays. Both lanes allocate cleanup-owned barrier arenas from the materialized image count, keep the Vulkan command-buffer record order unchanged, and fail closed on allocation/capacity errors instead of narrowing at `PDOCKER_GPU_MAX_VULKAN_BINDINGS`. | `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract`; native payload build; APK and fast regression; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-12 graphics V6.24 descriptor validation cap cleanup | V6.24 descriptor-set layout metadata validation no longer rejects sparse API binding numbers or descriptor-array counts at the legacy 16-slot executor constant. Binding numbers are consumed by the heap-backed sparse descriptor binding records, and descriptor counts are bounded by the V5 descriptor-table maximum instead of `PDOCKER_GPU_MAX_VULKAN_BINDINGS`. | `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract`; native payload build; APK and fast regression; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-12 graphics V6.1 dependency-barrier heap lane | Android graphics V6.1 explicit dependency barrier replay no longer counts per-command memory/buffer/image barriers against `PDOCKER_GPU_MAX_VULKAN_BINDINGS` or records them through fixed stack arrays. The recorder allocates synchronization2 and legacy barrier tables from the validated V6.1 metadata counts, keeps cross-queue-family transfers fail-closed, and frees the arenas through the command-buffer cleanup path. | `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract`; native payload build; APK and fast regression; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-12 graphics descriptor-bind image-barrier heap lane | Android graphics descriptor binding no longer records image layout transition barriers through a fixed `PDOCKER_GPU_MAX_VULKAN_BINDINGS` stack array. The replay path allocates a descriptor-image barrier arena from the validated graphics descriptor table count, uses that capacity for per-command descriptor image barriers, and frees it through command-buffer cleanup. | `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract`; native payload build; APK and fast regression; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-12 producer V6.24 descriptor cap cleanup | The container-side Vulkan ICD no longer applies the stale `PDOCKER_GPU_MAX_VULKAN_BINDINGS` descriptor-count check while collecting graphics V6.24 descriptor-set layout metadata. The remaining bound is the descriptor-array storage limit (`PDOCKER_VK_MAX_DESCRIPTOR_ARRAY_ELEMENTS`), so this removes the wrong legacy constant without claiming full descriptor-array heap support. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host test `tests.test_gpu_abi_contract`; glibc payload build `scripts/build-gpu-shim.sh`; APK and fast regression; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-12 producer descriptor-array heap storage lane | The container-side Vulkan ICD now stores descriptor-set rows and immutable-sampler rows as per-binding heap allocations instead of fixed `[PDOCKER_VK_MAX_DESCRIPTOR_ARRAY_ELEMENTS]` rows. Descriptor snapshots, update shadows, generic dispatch fallback, descriptor writes/copies, dynamic-offset binding, graphics descriptor collection, and legacy vector-add fallback now use descriptor slot accessors, and the advertised descriptor-array bound is raised to the V5 descriptor-table transport limit. This removes the producer-side 16-element descriptor-array storage ceiling while keeping V5 transport-table overflow fail-closed. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host test `tests.test_gpu_abi_contract`; glibc payload build `scripts/build-gpu-shim.sh`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-12 executor descriptor-array alias rewrite lane | The Android compute executor no longer fail-closes duplicate descriptor alias normalization solely because the source binding is a descriptor array. Alias layout sizing now scans every active source array element, allocates the rewritten binding with the same array span, mirrors alias descriptor writes with the original `api_array_element`, and frees materialized alias buffers by actual write count. This lane handled descriptor arrays; descriptor-set generalization is tracked in the following row. | `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract`; native payload build; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-12 executor multi-set descriptor alias rewrite lane | Duplicate descriptor Binding normalization is no longer restricted to descriptor set 0. The SPIR-V rewrite now tracks used and first-seen bindings by `(descriptor_set, binding)`, allocates spare bindings within the same descriptor set, records the alias set in `VulkanBindingAlias`, and lets the existing layout/write paths mirror rewritten descriptors into the matching Android descriptor set. | `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract`; native payload build; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-12 compute V5.3 texel-buffer descriptor lane | Compute dispatch V5 now has an append-only V5.3 buffer-view metadata extension. The container ICD transports texel-buffer descriptor identity, `VkFormat`, buffer-view offset/range, and generation alongside the underlying buffer resource; the Android executor validates the V5.3 table, materializes native `VkBufferView` objects, writes `pTexelBufferView` descriptors, and destroys those views after submit completion. This removes the former `texel-buffer descriptor ABI pending` fail-closed path for generic compute. Texel buffer `bufferFeatures` are advertised only when the executor reports matching V5.3 support; graphics V6 buffer-view transport remains a separate follow-up lane. | `app/src/main/cpp/pdocker_gpu_abi.h`; `docker-proot-setup/src/gpu/pdocker_gpu_abi.h`; `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract`; native builds `scripts/build-gpu-shim.sh`, `scripts/build-native-android-ndk.sh`; `verify-fast`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-13 graphics V6.28 declared push-constant range lane | Graphics replay no longer reconstructs `VkPipelineLayout` push-constant ranges from observed `vkCmdPushConstants` calls. The container ICD captures declared `VkPushConstantRange` arrays from `vkCreatePipelineLayout`, snapshots them with push commands, transports them in an append-only V6.28 table, and the Android executor validates hash/schema/range coverage before recreating pipeline layouts from the declared ranges. Executor diagnostics now expose the V6.28 `push_constant_ranges` table so missing layout declarations are visible before device reruns. | `app/src/main/cpp/pdocker_gpu_abi.h`; `docker-proot-setup/src/gpu/pdocker_gpu_abi.h`; `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract`; native builds `scripts/build-gpu-shim.sh`, `scripts/build-native-android-ndk.sh`; `verify-fast`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-13 graphics dependency DEVICE_GROUP transport lane | Legacy and sync2 graphics barrier/event transport now preserves `VK_DEPENDENCY_DEVICE_GROUP_BIT` together with `VK_DEPENDENCY_BY_REGION_BIT`.  The producer uses one allow-list for legacy barriers, sync2 barriers, render-pass dependencies, and event payloads, and the Android executor validates/replays the same two flags in native dependency structs.  This intentionally treats device-group as a single-device pass-through/no-op lane; sync2 pNext payloads, view-local semantics, feedback-loop dependency flags, and true multi-device/device-group behavior remain fail-closed. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-13 barrier external acquire-unmodified pNext lane | Buffer and image barriers now accept `VkExternalMemoryAcquireUnmodifiedEXT` as a dropped no-op metadata pNext when the existing queue-family ownership checks keep the barrier inside the bridge-supported same/ignored queue-family boundary. Legacy `vkCmdPipelineBarrier` and sync2 `VkDependencyInfo` both preserve the actual buffer/image barrier payload while continuing to reject unknown pNext chains and cross-queue-family ownership transfers. This widens generic Vulkan pNext pass-through without adding ABI fields or changing llama.cpp, Dockerfiles, models, or prompts. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host tests `tests.test_gpu_abi_contract tests.test_vulkan_icd_sync_harness`; glibc payload build `scripts/build-gpu-shim.sh`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-13 graphics descriptor-indexing per-flag gate | Android graphics replay now validates descriptor binding flags against their matching descriptor-indexing feature instead of requiring `descriptorBindingPartiallyBound` for every nonzero flag.  `VK_DESCRIPTOR_BINDING_UPDATE_UNUSED_WHILE_PENDING_BIT`, partially-bound, and variable descriptor-count bindings require `descriptorBindingUpdateUnusedWhilePending`, `descriptorBindingPartiallyBound`, and `descriptorBindingVariableDescriptorCount` respectively.  The update-unused lane does not relax descriptor coverage; only partially-bound may omit unwritten descriptors.  Update-after-bind remains unsupported because the bridge snapshots descriptor state at command recording/bind boundaries and must not claim mutable-after-bind semantics until submit-time descriptor revalidation exists. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract`; native builds; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-13 dependency queue-family all-stages no-op lane | The ICD now separates dependency flags into transported flags and accepted no-op flags. `VK_DEPENDENCY_QUEUE_FAMILY_OWNERSHIP_TRANSFER_USE_ALL_STAGES_BIT_KHR`, when present in the local Vulkan headers, is accepted only when every serialized buffer/image barrier has no queue-family ownership transfer (`IGNORED/IGNORED` or the same advertised family). The bit is deliberately dropped from the transported graphics V6 command flags so the executor ABI is unchanged, while actual cross-family transfers still fail closed. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host tests `tests.test_gpu_abi_contract tests.test_vulkan_icd_sync_harness`; glibc payload build `scripts/build-gpu-shim.sh`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-13 device queue shape fail-closed lane | `vkGetDeviceQueue2` now requires `VkDeviceQueueInfo2` to carry the correct `sType`, a null `pNext`, and the same advertised queue family/index/flags accepted by queue creation before it returns the bridge queue. `vkCreateDevice` queue-create validation now also rejects wrong `VkDeviceQueueCreateInfo::sType`, keeping queue acquisition and queue creation aligned on struct-shape fail-closed behavior. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host tests `tests.test_gpu_abi_contract tests.test_vulkan_icd_feature_chain`; glibc payload build `scripts/build-gpu-shim.sh`; `verify-fast`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-13 submit2 shape and handle fail-closed lane | `vkQueueSubmit2` now prevalidates `VkSubmitInfo2`, wait/signal `VkSemaphoreSubmitInfo`, and `VkCommandBufferSubmitInfo` structure shapes, pNext/flag/device-index fields, null semaphore handles, and null command buffers before any user fence is unsignaled. Submit2 command-buffer handle arrays are also allocated and populated before fence mutation so allocation failure cannot turn a signaled fence into a stale unsignaled failure. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host tests `tests.test_gpu_abi_contract tests.test_vulkan_icd_sync_harness`; glibc payload build `scripts/build-gpu-shim.sh`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-13 descriptor update template lane | `vkCreateDescriptorUpdateTemplate`, `vkDestroyDescriptorUpdateTemplate`, and `vkUpdateDescriptorSetWithTemplate` now support ordinary `VK_DESCRIPTOR_UPDATE_TEMPLATE_TYPE_DESCRIPTOR_SET` templates for the descriptor classes already supported by the bridge. Template entries are copied into a typed handle, shape/range/layout compatibility is validated up front, and template updates expand to `VkWriteDescriptorSet` records that call the existing staged `vkUpdateDescriptorSets` path, so descriptor shadowing, immutable samplers, snapshots, and fail-closed commit behavior stay centralized. Push descriptors, unsupported pNext/flags, and unsupported descriptor classes still fail closed. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host tests `tests.test_gpu_abi_contract tests.test_vulkan_icd_sync_harness`; glibc payload build `scripts/build-gpu-shim.sh`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-13 Vulkan legacy render-pass multiview lane | Legacy vkCreateRenderPass now accepts VkRenderPassMultiviewCreateInfo as generic Vulkan render-pass state instead of only accepting all-zero no-op metadata. pViewMasks are copied into the existing PdockerVkSubpassState::view_mask path shared with RenderPass2 and dynamic rendering, nonzero view-offset dependencies are normalized to the existing conservative all-view subpass barrier model, correlation masks remain optimization metadata, and malformed count/pointer/duplicate/unknown pNext shapes still fail closed. This keeps the bridge aligned with Vulkan multiview semantics without llama.cpp-specific shader or model logic. | docker-proot-setup/src/gpu/pdocker_vulkan_icd.c; host test tests.test_gpu_abi_contract; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-13 Vulkan maintenance5 query-surface lane | The ICD now implements query-only vkGetImageSubresourceLayout2, vkGetImageSubresourceLayout2KHR/EXT, vkGetDeviceImageSubresourceLayout, vkGetDeviceImageSubresourceLayoutKHR, and vkGetRenderingAreaGranularity/KHR without advertising broader maintenance5 semantics. The layout2 entry points reuse the existing tight fd-backed color subresource layout calculation, preserve output pNext chains, reject input pNext/shape drift by returning zero layout, and remain hidden when the bridge advertises only Vulkan 1.2 or does not advertise the matching extension alias. This is generic Vulkan API-surface pass-through hardening, not a llama.cpp workaround. | docker-proot-setup/src/gpu/pdocker_vulkan_icd.c; host test tests.test_gpu_abi_contract; glibc payload build scripts/build-gpu-shim.sh; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-13 Vulkan descriptor update-after-bind lane | The bridge now carries `VK_DESCRIPTOR_BINDING_UPDATE_AFTER_BIND_BIT` as generic Vulkan descriptor-indexing state instead of llama-specific logic. The ICD advertises the six type-specific update-after-bind features only when the Android executor reports them, validates the required descriptor-set-layout and descriptor-pool flags, rejects UAB layout allocation from ordinary pools, and serializes UAB bindings from live descriptor-set state at submit time while non-UAB bindings remain bind-time snapshots. Bound descriptor-set handles are retained so free/reset before submit fails closed rather than dereferencing stale pointers. The Android executor enables the type-specific features, replays UAB layout/pool flags, and keeps descriptor writes unchanged. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `app/src/main/cpp/pdocker_gpu_executor.c`; host tests `tests.test_gpu_abi_contract tests.test_vulkan_icd_sync_harness`; glibc/native payload build `scripts/build-gpu-shim.sh`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-13 Vulkan sampler reduction min/max lane | Sampler reduction mode is now carried as generic Vulkan sampler state instead of being collapsed into the former reserved sampler ABI field. The V5 sampler table stores `reduction_mode`, weighted-average remains the default, and MIN/MAX sampler reduction is accepted only when the Android executor advertises and enables `samplerFilterMinmax`. The ICD fail-closes malformed or duplicate `VkSamplerReductionModeCreateInfo` pNext chains; the Android executor validates the same feature gate and replays `VkSamplerReductionModeCreateInfo` into `vkCreateSampler`. | `app/src/main/cpp/pdocker_gpu_abi.h`; `docker-proot-setup/src/gpu/pdocker_gpu_abi.h`; `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-13 Vulkan host query reset feature lane | `hostQueryReset` is now advertised and accepted as a generic Vulkan feature because the ICD already implements host-side `vkResetQueryPool` over Skydnir-owned query-pool state. `VkPhysicalDeviceHostQueryResetFeatures` and Vulkan 1.2 feature queries report the feature, create-device feature validation accepts it, and requested/advertised feature masks include a dedicated `PDOCKER_VK_FEATURE_HOST_QUERY_RESET` bit. Query reset range validation remains centralized in `reset_query_range`/`query_range_valid`, so invalid ranges do not widen execution semantics. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host test `tests.test_gpu_abi_contract`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-13 Vulkan pipeline create no-op flags lane | Compute and graphics pipeline creation now accepts execution-neutral pipeline create hints (`VK_PIPELINE_CREATE_DISABLE_OPTIMIZATION_BIT`, `VK_PIPELINE_CREATE_ALLOW_DERIVATIVES_BIT`, and shape-validated `VK_PIPELINE_CREATE_DERIVATIVE_BIT`) instead of rejecting every nonzero `Vk*PipelineCreateInfo::flags` value. Unknown or semantic flags still fail closed; derivative base references are validated for handle/index shape and then dropped because the bridge transports captured shader/pipeline state rather than host driver compilation-cache topology. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host test `tests.test_gpu_abi_contract`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-13 Vulkan attachment load/store NONE lane | Graphics dynamic-rendering replay now accepts `VK_ATTACHMENT_LOAD_OP_NONE` and `VK_ATTACHMENT_STORE_OP_NONE` family enum values as generic Vulkan attachment state. The executor centralizes attachment-op validation, normalizes NONE to `DONT_CARE` for Android replay compatibility, and treats STORE NONE as non-writing so it does not request Skydnir image writeback. Unknown attachment ops still fail closed. | `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract`; native payload build `scripts/build-native-android-ndk.sh`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-13 Vulkan load-store-op-none extension lane | The ICD now advertises and accepts `VK_KHR_load_store_op_none` and `VK_EXT_load_store_op_none` only when dynamic rendering is advertised. This pairs the previously implemented attachment `LOAD_OP_NONE`/`STORE_OP_NONE` transport with the public Vulkan extension names clients use for feature discovery, while keeping unsupported devices hidden by the existing executor-capability gate. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host test `tests.test_gpu_abi_contract`; glibc payload build `scripts/build-gpu-shim.sh`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-13 Vulkan null/no-op attachment lane | Graphics dynamic-rendering replay now accepts null/no-op attachments as generic Vulkan attachment state. Color no-op attachments preserve their slot with a null `VkRenderingAttachmentInfo::imageView`; depth and stencil no-op attachments leave the corresponding rendering pointer absent. The executor only accepts this when the attachment is execution-neutral: no view, no resolve view, undefined format/layout, one sample, no clear payload, no resource id, no flags, and DONT_CARE/NONE load-store ops. Non-neutral null attachments still fail closed. | `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract`; native payload build `scripts/build-native-android-ndk.sh`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-13 Vulkan query validation lane | The ICD now fail-closes query-pool and command mismatches before recording commands: occlusion begin/end require occlusion pools, timestamp writes require timestamp pools, reset remains limited to owned occlusion/timestamp pools, pipeline-statistics query pools are rejected, and precise occlusion query flags are only allowed when the advertised `occlusionQueryPrecise` feature is true. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host test `tests.test_vulkan_icd_sync_harness`; glibc payload build `scripts/build-gpu-shim.sh`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-13 Vulkan image-format external no-op pNext lane | `vkGetPhysicalDeviceImageFormatProperties2` now accepts `VkPhysicalDeviceExternalImageFormatInfo` in the input pNext chain when `handleType == 0`, treating it as a standard no-op query modifier. Non-zero external memory handle requests still return `VK_ERROR_FORMAT_NOT_SUPPORTED`, and output `VkExternalImageFormatProperties` remains conservatively zero-filled. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host tests `tests.test_vulkan_icd_feature_chain tests.test_gpu_abi_contract`; glibc payload build `scripts/build-gpu-shim.sh`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-13 Vulkan dynamic-rendering secondary contents flag lane | The ICD now accepts `VK_RENDERING_CONTENTS_SECONDARY_COMMAND_BUFFERS_BIT` on `vkCmdBeginRendering` and transports it through the existing graphics V6 begin-rendering flags field. The Android executor already validates that this is the only nonzero dynamic-rendering flag and drops it for flattened primary replay; suspending/resuming and unknown rendering flags remain fail-closed. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host test `tests.test_gpu_abi_contract`; glibc payload build `scripts/build-gpu-shim.sh`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-13 graphics descriptor-bind live-handle groundwork | Graphics descriptor-bind command snapshots now retain the live descriptor-set handle table beside the existing immutable bind-time descriptor snapshot. Current replay still serializes from the snapshot and `VK_DESCRIPTOR_BINDING_UPDATE_AFTER_BIND_BIT` remains unadvertised/unsupported, but the command record now has the structural data needed for a future submit-time per-binding merge where update-after-bind bindings can be resolved from live sets while non-update-after-bind bindings and dynamic offsets remain bind-time state. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host test `tests.test_gpu_abi_contract`; glibc payload build `scripts/build-gpu-shim.sh`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-13 graphics V6.29 variable descriptor-count lane | Variable descriptor-count descriptor indexing now has an append-only graphics V6.29 metadata table. The container ICD accepts `VK_DESCRIPTOR_BINDING_VARIABLE_DESCRIPTOR_COUNT_BIT` only when the application requested the advertised descriptor-indexing feature, preserves allocation-time actual descriptor counts per set/binding, validates descriptor writes/copies/binds against the actual count rather than the layout maximum, and transports the actual/layout count pair with V6.25 bind and V6.24 layout identity. The Android executor advertises/enables the variable descriptor-count feature, validates the V6.29 table against command/layout metadata, recreates descriptor sets with `VkDescriptorSetVariableDescriptorCountAllocateInfo`, sizes descriptor pools from actual counts, and exposes `variable_descriptor_counts` in frame diagnostics. This remains generic Vulkan pass-through plumbing and does not modify llama.cpp, Dockerfiles, models, or prompts. | `app/src/main/cpp/pdocker_gpu_abi.h`; `docker-proot-setup/src/gpu/pdocker_gpu_abi.h`; `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract`; native builds `scripts/build-gpu-shim.sh`, `scripts/build-native-android-ndk.sh`; `verify-fast`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-13 graphics V6.24 partially-bound descriptor lane | Descriptor-set layout binding flags now flow through the generic Vulkan bridge instead of being rejected or dropped. The ICD accepts `VkDescriptorSetLayoutBindingFlagsCreateInfo` only for `VK_DESCRIPTOR_BINDING_PARTIALLY_BOUND_BIT` when the application requested the advertised descriptor-indexing feature, stores binding flags in descriptor layout state, and transports them in V6.24 layout metadata. The executor advertises/enables descriptor-indexing support from Android Vulkan, recreates descriptor-set layouts with `VkDescriptorSetLayoutBindingFlagsCreateInfo`, and keeps exact descriptor coverage fail-closed for normal bindings while allowing missing array elements only on partially-bound bindings. This is a generic Vulkan pass-through lane, not a llama.cpp-specific shader or model workaround. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract`; native builds `scripts/build-gpu-shim.sh`, `scripts/build-native-android-ndk.sh`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-13 Vulkan maintenance1 3D image 2D-array view lane | `VK_IMAGE_CREATE_2D_ARRAY_COMPATIBLE_BIT` is now accepted for valid 3D images and allows 2D/2D-array views over bounded depth slices on both the ICD and Android executor validation paths. The implementation keeps the Vulkan `levelCount == 1` rule for 3D-to-2D/2D-array views, treats `baseArrayLayer/layerCount` as mip-local depth slices only for 2D/2D-array views, preserves ordinary 3D view array-layer normalization, and fail-closes 2D images with the flag, 3D images without the flag, out-of-range depth slices, multi-mip 3D slice views, unknown flags, and unsupported pNext semantics. This is generic Vulkan maintenance1 compatibility and does not change llama.cpp, Dockerfiles, models, prompts, SPIR-V bytes, or descriptor data. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `app/src/main/cpp/pdocker_gpu_executor.c`; host tests `tests.test_gpu_abi_contract tests.test_vulkan_icd_feature_chain`; builds `scripts/build-gpu-shim.sh`, `scripts/build-native-android-ndk.sh`, `:app:assembleCompatDebug`; `verify-fast`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-13 Vulkan image-view minLod no-op pNext lane | `vkCreateImageView` now accepts `VkImageViewMinLodCreateInfoEXT` only when `minLod == 0.0f`, treating the default value as execution-neutral metadata. Nonzero min-lod clamps still fail closed because the current image-view transport has no per-view LOD field and must not silently widen application-visible sampling state. This widens generic image-view pNext compatibility without changing executor ABI, replay data, llama.cpp, Dockerfiles, models, prompts, or shader bytes. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host tests `tests.test_gpu_abi_contract tests.test_vulkan_icd_feature_chain`; glibc payload build `scripts/build-gpu-shim.sh`; APK build `:app:assembleCompatDebug`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-13 Vulkan image-format2 filter-cubic view-query pNext lane | `vkGetPhysicalDeviceImageFormatProperties2` now accepts `VkPhysicalDeviceImageViewImageFormatInfoEXT` as query metadata when `imageViewType` is one of the known Vulkan image-view types. The output-side `VkFilterCubicImageViewImageFormatPropertiesEXT` remains zero-filled, so filter-cubic support is still not advertised or implied. Unknown view-type values and unknown input pNext structs continue to return `VK_ERROR_FORMAT_NOT_SUPPORTED`. This is query-only generic Vulkan API-surface widening and does not touch executor ABI, replay, llama.cpp, Dockerfiles, models, prompts, or shader bytes. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host tests `tests.test_gpu_abi_contract tests.test_vulkan_icd_feature_chain`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-13 Vulkan memory allocation capture-address no-op pNext lane | `vkAllocateMemory` now accepts `VkMemoryOpaqueCaptureAddressAllocateInfo` only when `opaqueCaptureAddress == 0`, treating it as execution-neutral capture/replay metadata. Nonzero capture addresses remain fail-closed because the bridge does not advertise buffer-device-address capture/replay and must not silently reinterpret application-provided device addresses. This widens generic Vulkan allocation pNext compatibility without changing executor ABI, replay data, llama.cpp, Dockerfiles, models, prompts, or shader bytes. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host tests `tests.test_gpu_abi_contract tests.test_vulkan_icd_feature_chain`; glibc payload build `scripts/build-gpu-shim.sh`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-13 Vulkan memory allocation export-handle no-op pNext lane | `vkAllocateMemory` now accepts `VkExportMemoryAllocateInfo` only when `handleTypes == 0`, treating the default external-memory export request as execution-neutral metadata. Nonzero external handle requests remain fail-closed because the bridge does not advertise or transport Vulkan external memory handles. This is generic allocation pNext compatibility and does not change executor ABI, replay data, llama.cpp, Dockerfiles, models, prompts, or shader bytes. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host tests `tests.test_gpu_abi_contract tests.test_vulkan_icd_feature_chain`; glibc payload build `scripts/build-gpu-shim.sh`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-13 Vulkan private-data no-op feature/create-info lane | `vkGetPhysicalDeviceFeatures2` now initializes `VkPhysicalDevicePrivateDataFeatures` as `privateData = VK_FALSE`, and `vkCreateDevice` accepts that feature struct only when false. `VkDevicePrivateDataCreateInfo` is accepted only when `privateDataSlotRequestCount == 0`. Nonzero private-data feature or slot requests remain fail-closed because the bridge does not advertise private-data slot APIs or carry per-object private-data state. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host tests `tests.test_gpu_abi_contract tests.test_vulkan_icd_feature_chain`; no executor ABI, llama.cpp, Dockerfile, model, prompt, or shader changes |
| 2026-07-13 Vulkan memory-priority no-op feature/allocation lane | `vkGetPhysicalDeviceFeatures2` now initializes `VkPhysicalDeviceMemoryPriorityFeaturesEXT` as `memoryPriority = VK_FALSE`, and `vkCreateDevice` accepts that feature struct only when false. `vkAllocateMemory` accepts `VkMemoryPriorityAllocateInfoEXT` only when `priority == 0.5f`, the Vulkan default priority. Non-default priority values remain fail-closed because the bridge does not advertise `VK_EXT_memory_priority`, replay allocation priorities, or expose `vkSetDeviceMemoryPriorityEXT`. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host tests `tests.test_gpu_abi_contract tests.test_vulkan_icd_feature_chain`; no executor ABI, llama.cpp, Dockerfile, model, prompt, or shader changes |
| 2026-07-13 Vulkan shader-demote no-op feature lane | `vkGetPhysicalDeviceFeatures2` now initializes `VkPhysicalDeviceShaderDemoteToHelperInvocationFeatures` as `shaderDemoteToHelperInvocation = VK_FALSE`, and `vkCreateDevice` accepts that feature struct only when false. True requests remain fail-closed because the bridge does not advertise `VK_EXT_shader_demote_to_helper_invocation` and must not silently accept shader semantics it does not validate through Android replay. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host tests `tests.test_gpu_abi_contract tests.test_vulkan_icd_feature_chain`; no executor ABI, llama.cpp, Dockerfile, model, prompt, or shader changes |
| 2026-07-13 Vulkan standalone core feature pNext lane | Standalone core feature structs for multiview, variable pointers, protected memory, shader draw parameters, shader int64 atomics, and imageless framebuffer now participate in feature queries and create-device validation instead of falling into unknown-pNext failure. Multiview mirrors the existing Vulkan 1.1 aggregate advertisement; the remaining feature structs are queryable as false-only and true requests fail closed. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host tests `tests.test_gpu_abi_contract tests.test_vulkan_icd_feature_chain`; no executor ABI, llama.cpp, Dockerfile, model, prompt, or shader changes |
| 2026-07-13 Vulkan robustness false-only/query-property lane | Robustness-related feature structs now participate in generic feature query and create-device validation without promoting unsupported semantics. `VkPhysicalDeviceRobustness2FeaturesEXT`, `VkPhysicalDeviceImageRobustnessFeatures`, and `VkPhysicalDevicePipelineRobustnessFeatures` report false-only and reject true requests. Robustness2 and pipeline robustness property structs are initialized with conservative query values so pNext property chains do not fail merely due to struct form. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host tests `tests.test_gpu_abi_contract tests.test_vulkan_icd_feature_chain`; no executor ABI, llama.cpp, Dockerfile, model, prompt, or shader changes |
| 2026-07-13 Vulkan pipeline robustness create-info lane | `vkCreateComputePipelines` and `vkCreateGraphicsPipelines` now accept `VkPipelineRobustnessCreateInfo` only when every behavior field is `DEVICE_DEFAULT`, treating it as execution-neutral metadata. Any non-default storage/uniform/vertex/image robustness behavior fails closed with `VK_ERROR_FEATURE_NOT_PRESENT` because the bridge still does not advertise or replay pipeline robustness semantics. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host tests `tests.test_gpu_abi_contract tests.test_vulkan_icd_feature_chain`; no executor ABI, llama.cpp, Dockerfile, model, prompt, or shader changes |
| 2026-07-13 Vulkan dedicated allocation bind enforcement lane | `VkMemoryDedicatedAllocateInfo` now records the requested buffer or image target on the memory object, and `vkBindBufferMemory`/`vkBindImageMemory` enforce the dedicated-allocation contract: only the recorded resource may be bound and only at offset zero. Mismatched resource type, different target, and nonzero offsets fail closed before the bridge exposes KHR dedicated allocation publicly. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host tests `tests.test_gpu_abi_contract tests.test_vulkan_icd_feature_chain`; no executor ABI, llama.cpp, Dockerfile, model, prompt, or shader changes |
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
| 2026-07-05 Q6 structural final-store barrier lane | The final-store pre-barrier pass is no longer tied to one source hash or fixed SSA IDs.  It now fails closed on SPIR-V structure: descriptor set 0 binding 2 final stores, lane-0 input compare, staged Function/Workgroup load source, Q6-like input binding presence, and subgroup-reduction presence.  The offline effective-SPIR-V reconstructor mirrors the same structural pass, so instrumented/probe modules whose hashes and ids differ from the original native module remain analyzable. | `app/src/main/cpp/pdocker_gpu_executor.c`; `scripts/reconstruct-q6-effective-spirv.py`; host gate `tests.test_gpu_abi_contract`; pending APK/device rerun on the next stable ADB connection to confirm `q6_final_store_pre_barrier_inserted=true` in runtime evidence; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-05 Q6 structural storage/bitcast/final-store lane | The Q6 storage16-to-storage8 and uint-to-u8vec4 bitcast compatibility passes are no longer tied to the original source hash or fixed SSA IDs.  They now fail closed on duplicate set-0/binding-0 storage-buffer topology, exact Q6 counts (`24` storage16 loads and `16` bitcasts), unsigned byte type availability, immediate AccessChain+Load shape, member-index constant values instead of signed/unsigned constant IDs, and Q6 decode consumer/source structure for the bitcast lane.  The final-store pre-barrier no longer has the residual fixed label/compare/local-invocation SSA-ID branch; it uses the structural lane-zero input compare and staged binding-2 store topology only.  The offline reconstructor mirrors the same structural rules against the saved native Q6 fixture. | `app/src/main/cpp/pdocker_gpu_executor.c`; `scripts/reconstruct-q6-effective-spirv.py`; `tests/test_gpu_abi_contract.py`; native build `scripts/build-native-android-ndk.sh`; host gates `tests.test_gpu_abi_contract tests.test_llama_gpu_artifact_verifier`, APK gate `:app:assembleCompatDebug`; pending device rerun because ADB `192.168.179.21:43119` refused connection |
| 2026-07-06 Vulkan env/ABI propagation contract guard | Host-only static coverage now ties the Vulkan dispatch-option ABI macro, env manifest classifications, Q6 runner overlay, ICD token emission, executor token parsing, and invalid-option fail-closed messages together.  This prevents runner/ICD/executor key drift or a missing env bridge from being mistaken for a shader/device blocker at runtime. | `tests/test_gpu_abi_contract.py`; `docs/plan/LLAMA_GPU_BRIDGE_NEXT_STEPS.md`; host gate `python3 -m unittest tests.test_gpu_abi_contract.GpuAbiContractTest.test_vulkan_dispatch_option_env_contract_is_single_source_and_fail_closed`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-06 Vulkan packed depth/stencil dual-aspect descriptor lane | The executor now accepts dual-aspect packed `D24S8`/`D32S8` image views for read-only sampled/combined/input-attachment descriptor replay by splitting the copy planning into depth and stencil plane ranges. Storage-image descriptors and buffer-image raw packed dual-aspect copies remain fail-closed until they have an explicit scratch/repack ABI lane. | `app/src/main/cpp/pdocker_gpu_executor.c`; host gate `tests.test_gpu_abi_contract`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-06 Q6 stage-divergence preflight evidence lane | The Q6 run plan now names `q6_stage_divergence`, manifest-backed probe expectations, debug-u32 probe output, and SPIR-V probe env audit as required evidence. The plan verifier selects a dedicated stage-divergence evidence branch before native-final-store arithmetic work, so the next device run should not end in another ambiguous information-missing state. | `scripts/plan-llama-gpu-q6-run.py`; `scripts/verify-llama-gpu-q6-run-against-plan.py`; host gate `tests.test_gpu_abi_contract`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-06 Q6 probe-source freshness and raw SPIR-V evidence lane | The Q6 workgroup runner now refuses to touch ADB unless the probe bundle is tied to the actual Q6 source SPIR-V via `--probe-locator` or `--probe-source-spv` plus `--probe-source-hash`; archived fixtures require an explicit `PDOCKER_Q6K_ALLOW_ARCHIVED_PROBE_SOURCE=1`.  The run plan also requires raw original/effective SPIR-V dump evidence (`PDOCKER_GPU_SPIRV_DUMP_DIR`) so the next failure can be analyzed statically instead of by rerunning blindly. | `scripts/android-llama-gpu-q6-workgroup-run.sh`; `scripts/plan-llama-gpu-q6-run.py`; `scripts/android-llama-gpu-compare.sh`; host gate `tests.test_gpu_abi_contract`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-06 MSAA source STORE fail-closed lane | Dynamic-rendering MSAA replay now rejects a multisample source attachment with `STORE_OP_STORE` even when a V6.4 resolve target exists.  The supported path remains source `DONT_CARE` plus single-sample resolve-target writeback; unresolved source-store readback is held for a future explicit MSAA content/readback ABI instead of silently treating resolve output as full source preservation. | `app/src/main/cpp/pdocker_gpu_executor.c`; host gate `tests.test_gpu_abi_contract`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-06 Vulkan graphics immutable-sampler desugaring lane | V6.24 descriptor-set layout metadata now accepts immutable sampler shape metadata instead of rejecting it at producer or executor preflight.  The sampler object itself is not added as a new ABI payload: the ICD applies immutable samplers into descriptor-set slots and serializes them through the existing V6.25 descriptor bind path, while the executor validates count/type consistency and reconstructs a normal Android descriptor-set layout plus explicit descriptor writes. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `app/src/main/cpp/pdocker_gpu_executor.c`; host gate `tests.test_gpu_abi_contract`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-06 Vulkan graphics geometry-shader stage lane | Graphics passthrough now accepts `VK_SHADER_STAGE_GEOMETRY_BIT` through the existing V6 shader-stage table without changing the graphics ABI.  The ICD advertises `geometryShader` only when executor-derived Android Vulkan caps and geometry limits are present, includes the feature in create-device masks, and marks geometry-stage pipelines unsupported unless the application enabled the feature.  The executor widens its internal graphics replay stage scratch limit to five stages and performs runtime fail-closed validation after Android Vulkan initialization. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `app/src/main/cpp/pdocker_gpu_executor.c`; host gate `tests.test_gpu_abi_contract`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-07 Vulkan graphics base-feature advertisement lane | Added executor-derived advertisement and create-device request masks for `sampleRateShading`, `alphaToOne`, `logicOp`, `depthBiasClamp`, and `depthBounds`, plus ICD pipeline/command-buffer fail-closed checks before forwarding captured graphics state. `wideLines` remains deliberately deferred until line-width range/granularity caps are shadowed truthfully. This is generic Vulkan passthrough plumbing; llama.cpp, Dockerfile, model, and prompt remain unchanged. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `app/src/main/cpp/pdocker_gpu_executor.c`; host gate `tests.test_gpu_abi_contract`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-07 Vulkan graphics wide-lines limit shadow lane | `wideLines` is now advertised only when the Android executor reports the base feature plus truthful `lineWidthRange` and `lineWidthGranularity` limits. The glibc ICD shadows those limits into `VkPhysicalDeviceProperties`, includes `wideLines` in create-device masks, and fails closed for static or dynamic non-1.0 line width when the feature/range is unavailable. This remains generic Vulkan passthrough plumbing with no llama.cpp, Dockerfile, model, or prompt changes. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `app/src/main/cpp/pdocker_gpu_executor.c`; host gate `tests.test_gpu_abi_contract`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-07 Vulkan graphics rasterization base-feature lane | `depthClamp` and `fillModeNonSolid` are now advertised from executor-derived Android caps, included in create-device request masks, and guarded by ICD/executor fail-closed checks for depth-clamp and non-solid polygon rasterization state. This is generic Vulkan passthrough plumbing; llama.cpp, Dockerfile, model, and prompt remain unchanged. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `app/src/main/cpp/pdocker_gpu_executor.c`; host gate `tests.test_gpu_abi_contract`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-07 Vulkan indirect-draw base-feature lane | `multiDrawIndirect` and `drawIndirectFirstInstance` are now advertised from executor-derived Android caps, included in create-device request masks, and guarded in ICD/executor replay for V6.8 indirect draw metadata. Multi-draw indirect is rejected when drawCount > 1 without the feature; indirect firstInstance is fail-closed unless the application enabled the feature because the indirect buffer owns that member. This is generic Vulkan passthrough plumbing; llama.cpp, Dockerfile, model, and prompt remain unchanged. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `app/src/main/cpp/pdocker_gpu_executor.c`; host gate `tests.test_gpu_abi_contract`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-07 Vulkan descriptor image/sampler limits lane | Executor advertisement caps now include sampler, sampled-image, storage-image, input-attachment, per-stage-resource, and descriptor-set image/sampler limits. The glibc ICD parses those caps and shadows them into `VkPhysicalDeviceProperties` with bridge-side caps instead of leaving image/sampler descriptor classes implicit while replay supports them. Per-stage descriptor classes stay capped to `PDOCKER_VK_MAX_STORAGE_BUFFERS`; per-set image/sampler classes use the existing descriptor-array capacity helper; storage-buffer set limits remain conservative. This is generic Vulkan passthrough plumbing; llama.cpp, Dockerfile, model, and prompt remain unchanged. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `app/src/main/cpp/pdocker_gpu_executor.c`; `scripts/verify-ui-actions.py`; host gate `tests.test_gpu_abi_contract`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-07 Vulkan immutable-sampler support-query alignment lane | `vkGetDescriptorSetLayoutSupport` and `vkCreateDescriptorSetLayout` now share the same immutable-sampler validation: sampler and combined-image-sampler bindings may reference existing bridge sampler handles, while immutable samplers on non-sampler descriptor types and invalid sampler handles fail closed. This makes the existing immutable-sampler desugaring path reachable without adding a new ABI payload; descriptors still flow through existing sampler table plus V6.25 descriptor writes. This is generic Vulkan passthrough plumbing; llama.cpp, Dockerfile, model, and prompt remain unchanged. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `tests/test_gpu_abi_contract.py`; host gate `tests.test_gpu_abi_contract`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-07 Vulkan sampler anisotropy feature/limit gate lane | Executor caps now report `samplerAnisotropy` and `maxSamplerAnisotropy`; the ICD shadows the feature/limit only when Android caps are valid, includes the feature in create-device masks, and rejects anisotropic samplers when the guest did not enable the feature or exceeds the advertised limit. This is generic Vulkan passthrough plumbing; llama.cpp, Dockerfile, model, and prompt remain unchanged. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `app/src/main/cpp/pdocker_gpu_executor.c`; `scripts/verify-ui-actions.py`; host gate `tests.test_gpu_abi_contract`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-08 Vulkan independent-blend feature gate lane | `independentBlend` is now shadowed from executor-derived Android Vulkan caps, exposed through `VkPhysicalDeviceFeatures`, included in create-device request masks, and enforced for non-identical multi-attachment color-blend state. The ICD marks unsupported pipelines fail-closed, and the executor preflight/materialization rejects replay when Android did not enable `independentBlend`. This is generic Vulkan passthrough plumbing; llama.cpp, Dockerfile, model, and prompt remain unchanged. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `app/src/main/cpp/pdocker_gpu_executor.c`; host gate `tests.test_gpu_abi_contract`; native gates `scripts/build-gpu-shim.sh`, `scripts/build-native-android-ndk.sh`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-08 Vulkan local-read/sampler replay hardening lane | `VK_KHR_dynamic_rendering_local_read` is now an explicit non-advertised/fail-closed surface: feature queries return `dynamicRenderingLocalRead=false`, create-device rejects `true`, and `VkRenderingInfo` local-read pNext structs are treated as no-op only when their semantic counts are zero. Executor sampler replay now validates anisotropy feature and `maxSamplerAnisotropy` before `vkCreateSampler`, so cap drift fails under Skydnir control instead of leaking to native Vulkan as a generic create failure. This is generic Vulkan passthrough correctness work; llama.cpp, Dockerfile, model, and prompt remain unchanged. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `app/src/main/cpp/pdocker_gpu_executor.c`; host gates `tests.test_gpu_abi_contract`, `tests.test_vulkan_icd_feature_chain`; no llama.cpp/Dockerfile/model/prompt changes |
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
| 2026-06-20 Vulkan sync2 barrier NONE-stage lane | The ICD/executor split legacy and synchronization2 barrier semantics.  Legacy `vkCmdPipelineBarrier` now fails closed for zero stage masks, unsupported barrier `pNext`, or missing barrier arrays, while `vkCmdPipelineBarrier2` preserves valid `VK_PIPELINE_STAGE_2_NONE` barriers through the V6.1 graphics barrier tables.  Sync2 barriers with NONE-stage plus nonzero access masks remain fail-closed before submit.  This removes a self-imposed rejection from an advertised `VK_KHR_synchronization2` path without widening queue-family ownership transfer or broader synchronization. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `app/src/main/cpp/pdocker_gpu_executor.c`; host tests `tests.test_gpu_abi_contract tests.test_llama_gpu_env_parity`; native builds `scripts/build-native-android-ndk.sh`, `scripts/build-gpu-shim.sh` |
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
| 2026-06-06 Vulkan graphics optimal-image upload lane | Executor P6 now routes optimal-tiled attachment `LOAD` and read-only sampled/combined/input image descriptors through the same fd-backed staging upload path instead of accepting only storage-image upload ranges.  Attachment `LOAD` registers the attachment view range before command recording, uploads pending staged bytes before `vkCmdBeginRendering`, and then lets the normal attachment layout barrier move the image into the serialized attachment layout.  Descriptor uploads are now aspect-aware for bounded single-aspect color/depth/stencil descriptors, including packed `D24S8`/`D32S8` depth-only or stencil-only views for sampled/combined/input descriptors.  Storage images remain color-only, while dual-aspect depth/stencil, ambiguous multi-range image views, copy+draw mixed submit semantics, unresolved MSAA store/readback, and broader synchronization remain fail-closed. | `app/src/main/cpp/pdocker_gpu_executor.c`; host tests `tests.test_gpu_abi_contract tests.test_llama_gpu_env_parity`; native build `scripts/build-native-android-ndk.sh` |
| 2026-06-06 Vulkan graphics uint8-index lane | `VK_EXT_index_type_uint8` is now a strict advertised-capability contract instead of an unchecked replay assumption.  The Android executor queries and enables `VkPhysicalDeviceIndexTypeUint8FeaturesEXT` only when the host driver exposes `VK_EXT_index_type_uint8`; the producer ICD advertises/fills/validates the extension and feature only from executor advertisement caps.  Graphics indexed replay now accepts `VK_INDEX_TYPE_UINT8_EXT` with one-byte stride and fails closed if a serialized uint8-index draw reaches an executor without the feature enabled.  This widens generic Vulkan graphics pass-through without changing llama.cpp, Dockerfiles, prompts, or model bytes. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `app/src/main/cpp/pdocker_gpu_executor.c`; host tests `tests.test_gpu_abi_contract tests.test_llama_gpu_env_parity` |
| 2026-06-06 Vulkan graphics bounded mixed-submit lane | The producer ICD no longer rejects every submit that mixes transfer/layout commands with graphics replay.  It now statically plans the command-op order, executes host-side transfer/layout operations before the first graphics draw and after the last graphics draw, then submits the serialized graphics frame in between.  Transfers interleaved between graphics draws, dispatch+graphics mixing, and broader synchronization remain fail-closed so command ordering is not guessed. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host test `tests.test_gpu_abi_contract` |
| 2026-06-06 Vulkan graphics V6.9 interleaved buffer-copy lane | Graphics frames now have an append-only V6.9 metadata lane for command-ordered `vkCmdCopyBuffer` operations interleaved between draws.  The producer records each graphics command's original command-op sequence, serializes safe interleaved buffer-copy commands into the graphics command stream, and keeps other interleaved transfer/image/dispatch cases fail-closed.  The Android executor validates one command-indexed buffer-copy metadata entry per copy command, materializes source/destination buffer ranges with transfer usage, records `vkCmdCopyBuffer` in order, and writes back the destination range.  Broader mixed submit semantics, image copies, dispatch+graphics mixing, and full Vulkan synchronization remain fail-closed. | `app/src/main/cpp/pdocker_gpu_abi.h`; `docker-proot-setup/src/gpu/pdocker_gpu_abi.h`; `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `app/src/main/cpp/pdocker_gpu_executor.c`; host tests `tests.test_gpu_abi_contract tests.test_llama_gpu_env_parity`; native builds `scripts/build-native-android-ndk.sh`, `scripts/build-gpu-shim.sh`; APK gates `:app:verifyPackagedPayloadFresh :app:assembleCompatDebug`; payload check `scripts/verify-native-payloads.py` |
| 2026-06-06 Vulkan graphics V6.10 interleaved image-copy lane | Graphics frames now have an append-only V6.10 metadata lane for command-ordered `vkCmdCopyBufferToImage`, `vkCmdCopyImageToBuffer`, and `vkCmdCopyImage` operations interleaved between draws.  The producer serializes core `VkBufferImageCopy`/`VkImageCopy` fields with explicit ABI direction constants, keeps unsupported copy2 pNext semantics fail-closed, and the Android executor validates one command-indexed metadata entry per copy command, materializes buffer/image transfer ranges, replays the matching Vulkan copy call in order, and writes back transfer-written destinations.  Current image-aspect coverage is no longer color-only: fd-backed single-aspect color/depth/stencil copy regions are accepted when the aspect is valid for the image format, including packed depth/stencil formats when exactly one aspect is selected. Image-to-image copies require matching source/destination aspects.  Dual-aspect packed depth/stencil ranges, multiplanar/compressed images, broader image-layout synchronization, dispatch+graphics mixing, and full Vulkan synchronization remain fail-closed. | `app/src/main/cpp/pdocker_gpu_abi.h`; `docker-proot-setup/src/gpu/pdocker_gpu_abi.h`; `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `app/src/main/cpp/pdocker_gpu_executor.c`; host tests `tests.test_gpu_abi_contract tests.test_llama_gpu_env_parity`; native builds `scripts/build-native-android-ndk.sh`, `scripts/build-gpu-shim.sh`; pending APK gates `:app:verifyPackagedPayloadFresh :app:assembleCompatDebug`; pending payload check `scripts/verify-native-payloads.py` |
| 2026-06-06 Vulkan graphics V6.11 interleaved fill/update-buffer lane | Graphics frames now have an append-only V6.11 metadata lane for command-ordered `vkCmdFillBuffer` and `vkCmdUpdateBuffer` operations interleaved between draws.  The producer serializes command-indexed fill/update metadata instead of treating draw-between fill/update as host-only fail-closed: fill entries carry destination buffer resource, offset, size, and 32-bit pattern; update entries carry destination buffer resource, offset, size, payload range, and payload hash.  The Android executor validates one metadata entry per command, materializes destination buffers with `VK_BUFFER_USAGE_TRANSFER_DST_BIT`, records `vkCmdFillBuffer`/`vkCmdUpdateBuffer` in command order, includes transfer-write source stage/access in writeback barriers, and marks destination ranges for writeback.  The first lane remains bounded to fd-backed buffers, 4-byte-aligned offsets/sizes, `vkCmdUpdateBuffer` payloads no larger than 65536 bytes, no active rendering scope, no dispatch+graphics mixing, and no guessed synchronization beyond serialized barriers plus transfer-write writeback. | `app/src/main/cpp/pdocker_gpu_abi.h`; `docker-proot-setup/src/gpu/pdocker_gpu_abi.h`; `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `app/src/main/cpp/pdocker_gpu_executor.c`; host tests `tests.test_gpu_abi_contract tests.test_llama_gpu_env_parity`; native builds `scripts/build-native-android-ndk.sh`, `scripts/build-gpu-shim.sh`; pending APK gates `:app:verifyPackagedPayloadFresh :app:assembleCompatDebug`; pending payload check `scripts/verify-native-payloads.py`; no llama.cpp changes |
| 2026-06-06 Vulkan conservative image format/property lane | The ICD now advertises a conservative nonzero Vulkan format/image capability subset instead of reporting zero format features and rejecting every vkGetPhysicalDeviceImageFormatProperties query.  The advertised surface is intentionally bounded to single-sample optimal-tiling images, supported color/depth/stencil formats, transfer/sample/color/depth-storage use where implemented, and nonzero image/framebuffer limits.  It still refuses linear tiling, sparse images, texel buffers, blit/filter/compressed/SRGB/YCbCr promises, and unsupported create flags so graphics replay cannot depend on guessed capabilities. | docker-proot-setup/src/gpu/pdocker_vulkan_icd.c; host tests tests.test_gpu_abi_contract tests.test_llama_gpu_env_parity; native build scripts/build-gpu-shim.sh; APK gates :app:verifyPackagedPayloadFresh :app:assembleCompatDebug; payload check scripts/verify-native-payloads.py; no llama.cpp changes |
| 2026-06-15 Vulkan graphics V6.14/V6.15 advertised-cap gate lane | V6.14 resolve and V6.15 blit metadata remain append-only, but replay is now gated by the same capabilities the ICD advertises. Resolve requires advertised multisample-image support, source MSAA, destination single-sample, matching color format, transfer usage, and color-attachment-capable format features. The conservative fallback exposes only `VK_SAMPLE_COUNT_1_BIT`, so resolve still fails closed unless a later MSAA lane widens sample support. Blit requires single-sample color images plus advertised `BLIT_SRC`/`BLIT_DST` format features, and `VK_FILTER_LINEAR` additionally requires `SAMPLED_IMAGE_FILTER_LINEAR`; eligible command-ordered blits are routed through executor replay even without draw commands. The ICD can now consume executor-derived per-format image features, but missing/malformed executor caps fail closed. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract`; native builds `scripts/build-gpu-shim.sh`, `scripts/build-native-android-ndk.sh`; APK gate `:app:assembleDebug`; no llama.cpp changes |
| 2026-06-15 Vulkan executor-derived image-format caps lane | The executor now emits a per-format optimal-image feature table in `VULKAN_ADVERTISEMENT_CAPS` using `format_caps_schema`, `format_caps_count`, `fmt%dOptimalFeatures`, and `fmt%dSampleCounts`. The ICD parser requires the schema and complete bridge-format coverage, masks advertised features through the transport-supported set, rejects truncated caps JSON, and returns zero image features when executor caps are unavailable or malformed under `PDOCKER_VULKAN_ADVERTISEMENT_SOURCE=executor`. `vkGetPhysicalDeviceFormatProperties` and `vkGetPhysicalDeviceImageFormatProperties` now share those advertised bits, preventing image creation/probing from succeeding on capabilities the executor did not report. Sample counts are usage-scoped by later MSAA lanes rather than being a general per-format promise. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `app/src/main/cpp/pdocker_gpu_executor.c`; host tests `tests.test_gpu_abi_contract tests.test_llama_gpu_env_parity`; native builds `scripts/build-gpu-shim.sh`, `scripts/build-native-android-ndk.sh`; APK gate `:app:assembleDebug`; no llama.cpp changes |
| 2026-06-15 Vulkan MSAA image-allocation safety prerequisite | The ICD now accounts for sample count in image memory requirements before any MSAA advertisement is widened. `estimate_image_requirement_size()` maps `VkSampleCountFlagBits` to an exact sample count, rejects invalid/multiple-bit sample-count values, and multiplies the byte requirement by that count. This closes a future under-allocation hazard for multisample image transport without enabling MSAA advertisement or resolve replay yet. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host test `tests.test_gpu_abi_contract`; no llama.cpp changes |
| 2026-06-06 Vulkan graphics V6.12 interleaved clear-color image lane | Graphics frames now have an append-only V6.12 metadata lane for command-ordered `vkCmdClearColorImage` operations interleaved between draws.  The producer serializes one command-indexed clear-color image metadata entry per clear command, including target image index, layout, bounded color subresource range, and raw four-lane clear color bits.  The Android executor validates the table/hash contract, materializes fd-backed transfer-destination images, records `vkCmdClearColorImage` in command order, includes transfer-write source stage/access in image writeback barriers, and marks the touched image ranges for writeback.  This first lane is intentionally bounded to color-aspect images, concrete mip/layer ranges, no active rendering scope, no dispatch+graphics mixing, and no guessed synchronization outside serialized barriers plus transfer-write writeback. | `app/src/main/cpp/pdocker_gpu_abi.h`; `docker-proot-setup/src/gpu/pdocker_gpu_abi.h`; `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `app/src/main/cpp/pdocker_gpu_executor.c`; host tests `tests.test_gpu_abi_contract`; native builds `scripts/build-native-android-ndk.sh`, `scripts/build-gpu-shim.sh`; pending APK gates `:app:verifyPackagedPayloadFresh :app:assembleCompatDebug`; pending payload check `scripts/verify-native-payloads.py`; no llama.cpp changes |
| 2026-06-07 Vulkan graphics V6.13 interleaved clear-depth-stencil image lane | Graphics frames now have an append-only V6.13 metadata lane for command-ordered `vkCmdClearDepthStencilImage` operations interleaved between draws.  The producer serializes one command-indexed clear-depth/stencil metadata entry per clear command, including target image index, layout, bounded mip/layer range, aspect mask, raw depth bits, and stencil value.  The Android executor validates the V6.13 table/hash contract, rejects missing metadata fail-closed, materializes fd-backed transfer-destination depth/stencil images, records `vkCmdClearDepthStencilImage` in command order, includes transfer-write source stage/access in image writeback barriers, and marks the touched image ranges for writeback.  The producer now normalizes `VK_REMAINING_MIP_LEVELS` and `VK_REMAINING_ARRAY_LAYERS` at command-record time for V6.12/V6.13 clear-image lanes, so the ABI continues carrying concrete bounded ranges.  Combined depth+stencil clears are represented by splitting the producer-side operation into command-ordered single-aspect depth and stencil clear commands, preserving the executor's single-aspect writeback contract without guessing a packed depth/stencil layout.  The current lane remains intentionally bounded to no active rendering scope, no dispatch+graphics mixing, and no guessed synchronization outside serialized barriers plus transfer-write writeback. | `app/src/main/cpp/pdocker_gpu_abi.h`; `docker-proot-setup/src/gpu/pdocker_gpu_abi.h`; `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `app/src/main/cpp/pdocker_gpu_executor.c`; host tests `tests.test_gpu_abi_contract tests.test_llama_gpu_env_parity`; native builds `scripts/build-native-android-ndk.sh`, `scripts/build-gpu-shim.sh`; pending APK gates `:app:verifyPackagedPayloadFresh :app:assembleCompatDebug`; pending payload check `scripts/verify-native-payloads.py`; no llama.cpp changes |
| 2026-06-07 Vulkan dynamic whole-size descriptor lane | Dynamic buffer descriptors whose original range is `VK_WHOLE_SIZE` no longer fail closed when a nonzero dynamic offset is supplied.  The ICD now preserves the original whole-size range, applies the dynamic offset to the effective VkBuffer coordinate, and lets the existing VkBuffer-scoped descriptor validation derive the remaining buffer tail after the dynamic offset.  This removes a generic Vulkan descriptor compatibility gap without widening descriptor ranges to the backing allocation tail.  Overflow, missing dynamic offsets, empty effective ranges, non-fd-backed memory, and out-of-buffer/out-of-memory ranges still fail closed. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host tests `tests.test_gpu_abi_contract tests.test_llama_gpu_env_parity`; native build `scripts/build-gpu-shim.sh`; pending APK gates `:app:verifyPackagedPayloadFresh :app:assembleCompatDebug`; pending payload check `scripts/verify-native-payloads.py`; no llama.cpp changes |
| 2026-06-15 color-attachment-scoped MSAA advertisement lane | Executor-derived `fmt%dSampleCounts` is now scoped to color-attachment usage only. The ICD advertises wider counts only for bounded 2D non-depth/stencil color attachment image-format queries and keeps sampled/storage/transfer-only/depth/stencil/combined usages single-sample. Explicit `vkCmdResolveImage` is opened only for the same bounded color MSAA resolve subset and is routed through executor replay; host fallback remains fail-closed. In-render color resolve remains behind replay validation. Executor preflight rejects unresolved MSAA, MSAA `LOAD`, and pipeline/attachment sample-count mismatches, and resolve writeback targets the single-sample resolve image. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `app/src/main/cpp/pdocker_gpu_executor.c`; host tests `tests.test_gpu_abi_contract tests.test_llama_gpu_env_parity`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-06-15 MSAA materialization gate lane | Executor materialization now enforces the same MSAA scope it advertises. Generic V5 image dispatch rejects every non-single-sample image before Vulkan runtime setup. Graphics V6 allows multisample images only when command-ordered dynamic rendering metadata proves a V6.4 color attachment with a single-sample resolve target, matching image/view formats, color aspects, and bounded attachment ranges; all other MSAA table entries fail closed. The materializer also separates optimal single-sample staging from linear direct host upload so allowed MSAA render targets stay off the HOST_VISIBLE fd-upload path. | `app/src/main/cpp/pdocker_gpu_executor.c`; host tests `tests.test_gpu_abi_contract tests.test_llama_gpu_env_parity`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-06-16 unresolved MSAA writeback guard lane | Materialized executor image objects now preserve sample count through replay. Attachment writeback command recording and fd writeback both reject `writeback_needed` images whose sample count is not `VK_SAMPLE_COUNT_1_BIT`, so unresolved MSAA store/readback cannot accidentally flow through the single-sample copy path. The supported in-render MSAA path still writes back the single-sample V6.4 resolve target. | `app/src/main/cpp/pdocker_gpu_executor.c`; host tests `tests.test_gpu_abi_contract tests.test_llama_gpu_env_parity`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-06-16 single-virtual-queue ownership gate lane | The advertised Vulkan device still exposes a single graphics/compute/transfer queue family. Producer-side `vkCmdPipelineBarrier`/`vkCmdPipelineBarrier2` recording now rejects buffer and image barriers whose queue-family indices request a true cross-family ownership transfer, before those barriers are serialized into the graphics frame. Same-family and `IGNORED/IGNORED` barriers remain accepted and executor replay still normalizes them to `VK_QUEUE_FAMILY_IGNORED`. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host tests `tests.test_gpu_abi_contract tests.test_llama_gpu_env_parity`; native build `scripts/build-gpu-shim.sh`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-01 Vulkan explicit resolve MSAA source reachability lane | The ICD now treats `VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT` as the bounded MSAA color source usage for explicit `vkCmdResolveImage`, instead of advertising multisample counts only for exact color-attachment queries. The executor's advertisement table intersects native color-attachment sample counts with native explicit-resolve-source sample counts, and replay requires the MSAA source to remain a color attachment plus transfer source. Sampled/storage/depth/stencil/cube/3D and transfer-only MSAA usages remain fail-closed. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract`; native builds `scripts/build-gpu-shim.sh`, `scripts/build-native-android-ndk.sh`; APK gate `:app:assembleCompatDebug`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-01 Vulkan format properties2 query lane | The ICD now exposes `vkGetPhysicalDeviceFormatProperties2` / `vkGetPhysicalDeviceImageFormatProperties2` plus their KHR aliases, reusing the legacy format/image-format capability helpers instead of adding a second hard-coded capability path. This makes executor-derived format features and the bounded MSAA explicit-resolve source sample counts visible through the Vulkan 1.1+ query APIs used by modern clients. Unknown input `pNext` chains for image-format queries fail closed with `VK_ERROR_FORMAT_NOT_SUPPORTED`; known output chains are preserved and filled conservatively. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host test `tests.test_gpu_abi_contract`; native build `scripts/build-gpu-shim.sh`; APK gate `:app:assembleCompatDebug`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-03 Vulkan create-device feature gate lane | Device creation now validates requested base and pNext feature structs against the exact conservative feature set advertised by the ICD. Unsupported base features such as anisotropy, geometry, sample-rate shading, alpha-to-one, and depth-bounds now fail with `VK_ERROR_FEATURE_NOT_PRESENT` instead of being silently ignored. Core 1.1/1.2 and common extension feature structs are fully zero-initialized on query and checked field-by-field on create-device; unknown device pNext structs fail closed. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host test `tests.test_gpu_abi_contract`; native build `scripts/build-gpu-shim.sh`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-03 Vulkan single-device group create lane | Because the ICD advertises a single Vulkan 1.1 physical-device group, `vkCreateDevice` now accepts a standard `VkDeviceGroupDeviceCreateInfo` pNext only when it selects zero devices or exactly the advertised bridge physical device. Multi-device or foreign-device requests fail closed, while loader-injected `VK_STRUCTURE_TYPE_LOADER_DEVICE_CREATE_INFO` remains ignored as ICD metadata. The llama-init smoke now exercises this pNext shape. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `scripts/test/smoke-vulkan-llama-init.sh`; host test `tests.test_gpu_abi_contract`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-03 Vulkan properties pNext initialization lane | `vkGetPhysicalDeviceProperties2` now conservatively initializes more standard Vulkan 1.1/1.2 output pNext structs instead of leaving caller memory unchanged: ID, point clipping, multiview, protected memory, descriptor indexing, depth/stencil resolve, sampler minmax, float controls, and timeline semaphore properties. Unsupported capability values remain zero/false while stable bridge IDs and known scalar limits are filled. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host test `tests.test_gpu_abi_contract`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-04 Vulkan image-view usage pNext lane | Image-view creation no longer rejects the standard `VkImageViewUsageCreateInfo` pNext unconditionally. The ICD accepts it only when the requested view usage is non-zero and a subset of the backing image usage, which is safe for the current V5 image-view transport because executor-side Android image views can use the already-created image's full usage. Unknown image-view pNext structs and usage widening still fail closed. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host test `tests.test_gpu_abi_contract`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-04 Vulkan sampler reduction pNext lane | Initial sampler creation support accepted `VkSamplerReductionModeCreateInfo` only for the default `VK_SAMPLER_REDUCTION_MODE_WEIGHTED_AVERAGE` mode, which is equivalent to the base sampler behavior already transported to Android Vulkan. This row is superseded by the 2026-07-13 sampler reduction min/max lane, where the V5 sampler table carries `reduction_mode` and MIN/MAX is feature-gated by `samplerFilterMinmax`. Unknown sampler pNext structs still fail closed. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host test `tests.test_gpu_abi_contract`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-04 Vulkan buffer no-op pNext lane | Buffer creation now accepts selected create-info pNext structs only when they are semantically no-op for the current transport: zero external-memory handle types, zero opaque/device capture addresses, matching 32-bit `VkBufferUsageFlags2CreateInfo` usage, and false NV dedicated-allocation requests. Non-zero external handles, capture addresses, usage widening, dedicated allocation, and unknown buffer pNext structs still fail closed. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host test `tests.test_gpu_abi_contract`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-04 Vulkan image no-op pNext lane | Image creation now accepts selected pNext structs only when they are no-op for the current V5 image transport: zero external-memory handle types, stencil usage exactly matching the base image usage, and image format lists that are empty or contain only the base image format. External handles, divergent stencil usage, mutable alternate view formats, and unknown image pNext structs still fail closed. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host test `tests.test_gpu_abi_contract`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-04 Vulkan bind-memory device-group no-op lane | `vkBindBufferMemory2` and `vkBindImageMemory2` now accept standard device-group bind pNext structs only when they select the single local device or leave the selection empty. Image split-instance bind regions, foreign device indices, multi-device selections, and unknown bind pNext structs still fail closed, matching the bridge's single-advertised-device model. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host test `tests.test_gpu_abi_contract`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-04 Vulkan command-buffer begin device-group lane | `vkBeginCommandBuffer` now validates begin-info pNext instead of silently ignoring it. A standard `VkDeviceGroupCommandBufferBeginInfo` is accepted only for the default/single local device mask, while multi-device masks and unknown begin pNext structs mark the command buffer failed so `vkEndCommandBuffer`/submit fail closed. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host test `tests.test_gpu_abi_contract`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-04 Vulkan sync export no-op pNext lane | Fence and semaphore creation now accept standard export pNext structs only when `handleTypes == 0`, which is a no-op for the current bridge. Non-zero external fence/semaphore handle requests still fail closed because the bridge does not advertise or transport Vulkan external sync handles. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host test `tests.test_gpu_abi_contract`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-04 Vulkan render-pass legacy no-op pNext lane | Legacy `vkCreateRenderPass` no longer treats every pNext as unsupported. It accepts no-op `VkRenderPassMultiviewCreateInfo` chains only when all view masks, view offsets, and correlation masks are zero and accepts input-attachment-aspect pNext only with zero aspect references. Non-zero multiview/aspect behavior and unknown render-pass pNext structs still mark the render pass unsupported for dynamic-rendering normalization. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host test `tests.test_gpu_abi_contract`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-04 Vulkan framebuffer create validation lane | `vkCreateFramebuffer` now validates flags, pNext, and attachment arrays before creating a bridge framebuffer. Standard imageless-framebuffer attachment metadata is accepted only as an empty no-op pNext; real imageless framebuffer use remains fail-closed until `VkRenderPassAttachmentBeginInfo` replay is implemented. Non-zero flags and missing non-imageless attachment arrays now fail early instead of being silently accepted. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host test `tests.test_gpu_abi_contract`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-04 Vulkan render-pass begin no-op pNext lane | `vkCmdBeginRenderPass` now distinguishes no-op begin-time pNext metadata from unsupported render-pass semantics. Single-device `VkDeviceGroupRenderPassBeginInfo`, empty `VkRenderPassAttachmentBeginInfo`, and empty sample-location begin metadata no longer force the command into unsupported graphics fallback, while real imageless framebuffer attachment replacement, multi-device render areas, and sample-location payloads remain fail-closed until they have explicit replay support. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host test `tests.test_gpu_abi_contract`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-04 Vulkan dynamic-rendering device-group no-op lane | `vkCmdBeginRendering` now shares the single-device render-area pNext validator used by legacy render-pass begin. Empty or render-area-identical `VkDeviceGroupRenderPassBeginInfo` no longer marks dynamic rendering unsupported, while multi-device masks or divergent per-device render areas remain fail-closed. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host test `tests.test_gpu_abi_contract`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-04 headless WSI device-group no-op lane | `vkAcquireNextImage2KHR` accepts only its built-in single-device `deviceMask` field and keeps acquire pNext chains fail-closed; `vkQueuePresentKHR` accepts single-device `VkDeviceGroupPresentInfoKHR` pNext metadata as no-op for the headless swapchain path. Multi-device masks, non-local present modes, mismatched swapchain counts, and unknown WSI pNext chains remain fail-closed. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host test `tests.test_gpu_abi_contract`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-04 headless WSI swapchain create device-group lane | `vkCreateSwapchainKHR` now accepts `VkDeviceGroupSwapchainCreateInfoKHR` only when it requests the single-device local present mode already supported by the headless swapchain path. Other device-group modes and unknown swapchain-create pNext chains remain fail-closed. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host test `tests.test_gpu_abi_contract`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-04 Vulkan render-pass2 feedback no-op lane | `vkCreateRenderPass2` now accepts standard subpass-merge feedback pNext metadata without changing rendering semantics. `VkRenderPassCreationControlEXT` is tracked only to report disallowed merging, top-level creation feedback records the existing subpass count as no-merge behavior, and per-subpass feedback reports conservative not-merged statuses. Unknown render-pass2/subpass pNext payloads and real semantic extensions remain fail-closed. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host test `tests.test_gpu_abi_contract`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-04 Vulkan subpass-merge feedback feature-query lane | `VkPhysicalDeviceSubpassMergeFeedbackFeaturesEXT` is now initialized as a conservative false feature in feature queries, and `vkCreateDevice` accepts the feature pNext only when `subpassMergeFeedback == VK_FALSE`. This lets modern clients attach the standard feature-query struct without leaving memory undefined while still refusing to advertise or enable the extension capability. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host tests `tests.test_gpu_abi_contract tests.test_vulkan_icd_feature_chain`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-04 Vulkan pNext guard hygiene lane | ICD pNext handling no longer uses enum constants such as `VK_STRUCTURE_TYPE_*` as preprocessor guards. All conditional Vulkan struct handling now keys off real `VK_VERSION_*` or extension guard macros, and the ABI contract rejects future `#ifdef/#ifndef VK_STRUCTURE_TYPE_*` regressions. This closes a class of silent no-op implementation loss where compiled code skipped valid pNext handlers even though the headers defined the enum values. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host test `tests.test_gpu_abi_contract`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-04 Vulkan pipeline feedback and compute fail-closed lane | `vkCreateComputePipelines` no longer silently accepts unsupported pipeline create flags, derivative pipeline inheritance, non-compute shader stages, shader-stage flags, or shader-stage pNext chains. Base-pipeline fields are accepted as ignored metadata when no derivative flag is present, matching ordinary zero-initialized Vulkan create-info behavior and preventing false `VK_ERROR_FEATURE_NOT_PRESENT` rejects for compute clients such as ggml-vulkan. Top-level pipeline pNext handling now accepts `VkPipelineCreationFeedbackCreateInfo` as output-only metadata for both compute and graphics pipelines, fills valid zero-duration feedback, validates stage-feedback count, and continues to allow graphics `VkPipelineRenderingCreateInfo` semantics already captured by the dynamic-rendering path. Unsupported pipeline pNext structs still fail closed rather than being ignored. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host test `tests.test_gpu_abi_contract`; native build `scripts/build-gpu-shim.sh`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-04 Q6 runtime evidence routing lane | Focused device run `docs/test/llama-gpu-ngl1-q6-workgroup-legalized-192_168_179_21_43119-20260704T174315Z.json` reached the llama HTTP service with runtime binary freshness passing and no compute-pipeline feature rejection. The remaining blocker is not transport/writeback/probe arming: Q6 final-store evidence shows `native-final-store-mismatch` while executor writeback matches native GPU memory. Plan routing now prioritizes that native final-store evidence over stale probe-audit noise. Current measured speedup is 1.43x CPU, below the 10x target, and correctness remains failed because deterministic completion returned `Marvel`. | `scripts/android-llama-gpu-compare.sh`; `scripts/verify-llama-gpu-artifact.py`; `scripts/verify-llama-gpu-q6-run-against-plan.py`; host tests `tests.test_gpu_abi_contract tests.test_llama_gpu_artifact_verifier`; device summary `docs/test/q6-native-final-store-evidence-20260704T174315Z.json`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-06 Vulkan graphics standalone layout materializer lane | Graphics replay now materializes descriptor-set layouts and pipeline layouts once from serialized V6.24/V6.25 layout metadata before graphics pipeline creation, descriptor allocation, push constants, or descriptor binding. `vkCreateGraphicsPipelines`, `vkCmdBindDescriptorSets`, and `vkCmdPushConstants` now use the same Android `VkPipelineLayout` handle instead of relying on separately reconstructed but only theoretically compatible layouts. V6.25 descriptor binds fail closed when a layout-declared descriptor slot is missing or duplicated. This advances generic Vulkan graphics pass-through structure; it does not claim llama GPU correctness or performance. | `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract`; native build `scripts/build-native-android-ndk.sh`; fast gate `scripts/verify-fast.sh`; commit `7203514c`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-06 Vulkan descriptor/layout early-reject lane | The container ICD aligns descriptor/pipeline layout creation with the conservative advertised feature contract before objects are created. Descriptor set layouts reject duplicate bindings, zero descriptor counts, unsupported binding flags, oversized arrays, and immutable samplers on non-sampler descriptor types through the same support/create path. Pipeline layouts fail early for oversized set-layout counts, invalid set-layout handles, missing push-constant ranges, and unsupported push-constant range counts instead of creating handles that fail only at bind/submit time. The earlier broad immutable-sampler rejection is superseded by the 2026-07-06 desugaring lane and the 2026-07-07 support-query alignment lane. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host tests `tests.test_gpu_abi_contract tests.test_llama_gpu_env_parity tests.test_llama_gpu_readiness_contract`; native build `scripts/build-gpu-shim.sh`; fast gate `scripts/verify-fast.sh`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-06 Vulkan graphics image-layout range split lane | The Android Vulkan executor now mirrors the ICD-side image layout range split/merge model when replaying graphics image barriers. `vulkan_replay_image_set_layout_for_range()` no longer requires exact overlap with an existing range: it seeds whole-image state when needed, preserves unaffected aspect/mip/layer spans, appends the replacement range, merges adjacent compatible ranges, and still fails closed on invalid ranges or range-table overflow. This advances generic graphics pass-through image/barrier replay and is not llama-specific. | `app/src/main/cpp/pdocker_gpu_executor.c`; host tests `tests.test_gpu_abi_contract tests.test_llama_gpu_env_parity`; native build `scripts/build-native-android-ndk.sh`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-06 Vulkan graphics ICD layout commit lane | Successful graphics V6 segment replay now commits the same image-barrier layout transitions back into the container ICD layout cache, using the recorded graphics barrier/set-event/wait-event metadata after the executor has returned success. Failed graphics replay and validation-producer paths do not mutate ICD layout state. This prevents later V6.20 initial-layout metadata, present checks, and follow-up command buffers from seeing stale `PdockerVkImage` layout/range state after Android Vulkan replay. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host tests `tests.test_gpu_abi_contract tests.test_llama_gpu_env_parity`; native build `scripts/build-gpu-shim.sh`; fast gate `scripts/verify-fast.sh`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-06 Vulkan single-queue sharing lane | The bridge now treats `VK_SHARING_MODE_CONCURRENT` over the single advertised guest queue family as a no-op sharing alias instead of rejecting it. ICD image and buffer creation validate that all supplied guest queue-family indices are advertised, normalize transported images to `EXCLUSIVE`, and keep true cross-family ownership transfers fail-closed. The Android executor also guards malformed/future concurrent image metadata by synthesizing native compute/graphics queue-family lists only when its native queues differ, otherwise replaying images as exclusive. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `app/src/main/cpp/pdocker_gpu_executor.c`; host tests `tests.test_gpu_abi_contract tests.test_llama_gpu_env_parity`; native builds `scripts/build-native-android-ndk.sh`, `scripts/build-gpu-shim.sh`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-04 Vulkan descriptor update pNext fail-closed lane | `vkUpdateDescriptorSets` now validates `VkWriteDescriptorSet::pNext` and `VkCopyDescriptorSet::pNext` before staging any descriptor shadow updates. Because the current descriptor transport has no inline-uniform, acceleration-structure, or future descriptor-extension table, non-null write/copy pNext chains fail closed before commit instead of being silently ignored. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host test `tests.test_gpu_abi_contract`; native build `scripts/build-gpu-shim.sh`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-04 Vulkan device-queue pNext fail-closed lane | `vkCreateDevice` now validates every `VkDeviceQueueCreateInfo::pNext` chain. The bridge accepts `VkDeviceQueueGlobalPriorityCreateInfo` only when it requests the default medium priority, which is a no-op for the single advertised queue, and rejects high/realtime/unknown queue pNext semantics instead of silently ignoring scheduling changes. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host test `tests.test_gpu_abi_contract`; native build `scripts/build-gpu-shim.sh`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-04 Vulkan command/event create-info fail-closed lane | Command-pool creation, command-buffer allocation, command-pool reset/trim, and event creation now validate `sType`, `pNext`, handles, and flags instead of silently ignoring unsupported control bits. The bridge accepts only transient/resettable command-pool flags, validates the advertised single queue family, rejects command-buffer allocation pNext and invalid levels, and rejects event pNext/flags before creating executor-backed event objects. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host test `tests.test_gpu_abi_contract`; native build `scripts/build-gpu-shim.sh`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-04 Vulkan WSI surface-capabilities output pNext lane | `vkGetPhysicalDeviceSurfaceCapabilities2KHR` now rejects only input `VkPhysicalDeviceSurfaceInfo2KHR::pNext` while walking output `VkSurfaceCapabilities2KHR::pNext` safely. The bridge zero-fills `VkSurfaceProtectedCapabilitiesKHR` with `supportsProtected = VK_FALSE` and `VkSharedPresentSurfaceCapabilitiesKHR` with no shared-present usages, preserving pNext chains and rejecting unknown output structs. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host test `tests.test_gpu_abi_contract`; native build `scripts/build-gpu-shim.sh`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-04 Vulkan legacy submit pNext no-op lane | `vkQueueSubmit` now validates legacy submit shape before mutating fence state, rejects missing wait/signal/command pointer arrays, and classifies standard submit pNext structs explicitly. Timeline submit metadata remains supported, single-device `VkDeviceGroupSubmitInfo` and `VkProtectedSubmitInfo{protectedSubmit = VK_FALSE}` are accepted as no-op metadata, while multi-device, protected, duplicate timeline, and unknown submit pNext semantics fail closed. `vkQueueSubmit2` command-buffer device masks now accept the single-device `1` mask and continue to reject multi-device masks. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host test `tests.test_gpu_abi_contract`; native build `scripts/build-gpu-shim.sh`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-04 Vulkan descriptor allocation pNext no-op lane | Descriptor pool creation and descriptor set allocation now classify standard descriptor pNext structs explicitly. `VkDescriptorPoolInlineUniformBlockCreateInfo` is accepted only when `maxInlineUniformBlockBindings == 0`, and `VkDescriptorSetVariableDescriptorCountAllocateInfo` is accepted only when all variable counts are zero and the count shape matches the allocation. Real inline-uniform and variable-descriptor semantics, malformed counts, and unknown descriptor pNext structs fail closed instead of being silently ignored. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host test `tests.test_gpu_abi_contract`; native build `scripts/build-gpu-shim.sh`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-04 Q6 SPIR-V dataflow path-diff lane | `scripts/compare-spirv-dataflow.py` now reports exact `diff_paths`, `first_mismatch_path`, and structured path diffs for list/map comparisons and adds load/store event path comparisons. This closes a static diagnostic blind spot where origin counts could match while dynamic index expressions or stored-value producer paths differed. Synthetic tests now prove `load_origins`/`store_origins` can remain matched while `load_paths`/`store_paths` fail with the offending expression or descriptor binding visible. | `scripts/compare-spirv-dataflow.py`; host test `tests.test_gpu_abi_contract`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-04 Q6 final-store flow analysis lane | `scripts/analyze-spirv.py` now embeds structural `q6_probe_targets` in normal analysis bundles, not only in probe manifests, so `scripts/compare-spirv-dataflow.py` can compare Q6 final-store value-flow directly. The dataflow report now has a `q6_final_store_value_flow` comparison that names differences in availability, final-store count, phase shape, output binding, Workgroup-load reachability, op histograms, and debug/probe exclusion. A regenerated safe-vs-native Q6 comparison now reports the boundary at `q6_final_store_value_flow.available` / `final_store_count` before runtime collection. | `scripts/analyze-spirv.py`; `scripts/compare-spirv-dataflow.py`; host test `tests.test_gpu_abi_contract`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-04 Q6 Function-accumulator dataflow lane | Static Q6 final-store analysis now follows `OpLoad` values from Function-storage accumulators back through preceding Function `OpStore` producers and parses `OpExtInst` FMA operands. This closes the false `final_store_value_flow.available=false` result for the current `0x6ec5d7a41443f157` runtime module, where final binding-2 stores load from Function variables before writing output. The probe manifest verifier also accepts any collision-free debug descriptor chosen by the analyzer instead of hardcoding binding 5, while still requiring debug/probe exclusion from stored values and output indices. | `scripts/analyze-spirv.py`; `scripts/verify-spirv-probe-manifest.py`; fixture `docs/test/spirv-q6k-function-accumulator/native-q6-function-accumulator.spv`; host tests `tests.test_gpu_abi_contract tests.test_llama_gpu_artifact_verifier`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-04 Q6 specialization dependency evidence lane | Q6 final-store analysis now records bounded dependency facts for spec constants, ordinary constants, built-ins, push constants, and descriptors. The preserved source/effective Function-accumulator fixtures prove the exact runtime specialization materialization boundary: source `0x6ec5d7a41443f157` carries output-index dependency `SpecId=1 default=1`, while effective `0x00c3414e5d50b925` carries `id=15 value=2` with `LocalSize=[64,1,1]`. This makes specialization/workgroup-size mismatches visible in `q6_final_store_value_flow` comparison instead of hiding them behind large load/store path diffs. | `scripts/analyze-spirv.py`; `scripts/compare-spirv-dataflow.py`; fixtures `docs/test/spirv-q6k-function-accumulator/*.spv`; host test `tests.test_gpu_abi_contract`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-04 Q6 barrier window evidence lane | Static SPIR-V analysis now decodes `OpControlBarrier` and `OpMemoryBarrier` scope/semantics and attaches Q6 phase windows that map Workgroup stores to the following barriers before final output stores. The Function-accumulator fixture records four `OpControlBarrier` events with Workgroup/Workgroup `AcquireRelease|WorkgroupMemory` semantics and phase windows `tail=[2145,2845]`, `full=[5260,6163]`. `compare-spirv-dataflow.py` now reports barrier-window differences independently from final-store value-flow differences. | `scripts/analyze-spirv.py`; `scripts/compare-spirv-dataflow.py`; fixture `docs/test/spirv-q6k-function-accumulator/native-q6-function-accumulator.spv`; host test `tests.test_gpu_abi_contract`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-04 Q6 descriptor access leaf evidence lane | Q6 final-store dependency summaries now include descriptor-load leaves with load word/result/pointer ids, descriptor set/binding/variable ids, member path, symbolic byte-offset terms, array stride, and terminal type. The Function-accumulator fixture proves both final stores read binding 3 and 4 f32 runtime-array leaves with static byte offset 0 plus dynamic index scale 4, while the slice remains explicitly marked incomplete when recursion/depth limits leave unresolved ids. `compare-spirv-dataflow.py` now carries these descriptor leaves in the Q6 dependency signature. | `scripts/analyze-spirv.py`; `scripts/compare-spirv-dataflow.py`; fixture `docs/test/spirv-q6k-function-accumulator/native-q6-function-accumulator.spv`; host test `tests.test_gpu_abi_contract`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-04 Q6 named arithmetic evidence lane | Static SPIR-V analysis now parses `OpExtInstImport`, names `GLSL.std.450.Fma`, records top-level and dependency-slice FMA histograms, and parses `OpGroupNonUniformFAdd` with subgroup/reduce metadata. The Function-accumulator fixture records `GLSL.std.450.Fma=80` and `OpGroupNonUniformFAdd=2` for the whole module, plus per-Q6-window histograms of `Fma=40` and `OpGroupNonUniformFAdd=1` for both tail and full phases. Bounded final-store slices also carry named arithmetic histograms, while static windows provide the complete phase-level evidence. | `scripts/analyze-spirv.py`; `scripts/compare-spirv-dataflow.py`; fixture `docs/test/spirv-q6k-function-accumulator/native-q6-function-accumulator.spv`; host test `tests.test_gpu_abi_contract`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-04 Q6 stage-divergence fail-closed lane | Device run `docs/test/llama-gpu-ngl1-q6-stage-trace-192_168_179_21_43119-20260704T203858Z.json` reached the forced Vulkan HTTP service and measured `1.95x` CPU speedup, but deterministic completion still returned `" Marvel"`. The run did not request the SPIR-V probe manifest (`spirv_probe_env_audit.summary=not-requested`), so `q6_debug_u32_probe.summary=not-run` and the artifact cannot classify the first bad stage. The verifier now requires explicit `q6_stage_divergence` evidence before promoting `native-final-store-mismatch` to a final-lane-0 conclusion; otherwise it fails closed as `q6-stage-divergence-evidence-missing`. | `scripts/verify-llama-gpu-artifact.py`; `tests.test_llama_gpu_artifact_verifier`; device artifact `docs/test/llama-gpu-ngl1-q6-stage-trace-192_168_179_21_43119-20260704T203858Z.json`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-04 Q6 probe manifest freshness lane | The latest manifest-backed device artifact showed a reduction-lane `candidate-id/local-x` mismatch because the default `/tmp/q6write10-bundle` probe bundle still advertised the old 32-lane trace layout (`reduction slot_base=400`) while current Q6 tracing requires 64 lanes (`reduction slot_base=656`). This is now classified as a stale probe-bundle problem, not a Vulkan native arithmetic result. The Q6 workgroup runner refuses stale default bundles before ADB unless an actual source SPIR-V/hash is supplied, the probe manifest verifier rejects stale lane layouts, and the artifact verifier reports `q6-debug-u32-probe-layout-stale` instead of promoting metadata mismatch or final-store conclusions. | `scripts/android-llama-gpu-q6-workgroup-run.sh`; `scripts/verify-spirv-probe-manifest.py`; `scripts/android-llama-gpu-compare.sh`; `scripts/verify-llama-gpu-artifact.py`; host tests `tests.test_gpu_abi_contract tests.test_llama_gpu_artifact_verifier`; stale manifest proof `/tmp/q6write10-bundle/native-q6.write.probe.json` rejected locally; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-05 Q6 current-hash stage trace lane | Manifest-backed run `docs/test/llama-gpu-ngl1-q6-stage-trace-currenthash-192_168_179_21_43119-20260705T0020Z.json` used the refreshed 64-lane probe layout and `q6_debug_u32_probe.summary=pass`. The final-store trace proves executor writeback preserved the Android Vulkan value (`final_store_value_f32 == fd_after_writeback`) while the value still differs from the CPU oracle, so the active boundary remains native Q6 final-store/readback. The same run did not execute the current reduction-lane trace, so `q6_stage_divergence` is now emitted as explicit `missing-evidence` instead of silently promoting a final-lane-0 conclusion. The probe audit also distinguishes executor-visible debug probe evidence from a missing ICD arm log so probe-log noise does not override the native boundary. | `scripts/android-llama-gpu-compare.sh`; `scripts/verify-llama-gpu-artifact.py`; host tests `tests.test_gpu_abi_contract tests.test_llama_gpu_artifact_verifier`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-05 Q6 effective-SPIR-V reconstruction parity lane | Offline Q6 reconstruction now matches the runtime executor for the current hash pair: source `0x9f25cd017b2fbaf2` reconstructs to effective `0x282077d0a3b74fb4`. The bug was in the evidence tool, not llama.cpp: its LocalSize legalizer was hard-coded to `[32,1,1]`, so it preserved the WorkgroupSize specialization subtree for the current `[64,1,1]` run and then failed/mis-modeled specialization materialization. The tool now mirrors the C executor bounds (`1 < invocation_count <= 1024`), folds the WorkgroupSize composite after LocalSize legalization, and applies only the transformations that the runtime artifact says actually ran, so disabled Q6 lowerings are not invented offline. | `scripts/reconstruct-q6-effective-spirv.py`; host tests `tests.test_gpu_abi_contract tests.test_llama_gpu_artifact_verifier`; local proof `/tmp/q6-probe-reconstruct.json`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-05 Q6 probe-write manifest cross-check lane | The Q6 probe instrumenter/verifier now fails closed when instrumentation probe writes drift away from the structural manifest. Unknown probe roles no longer degrade to `role_code=0`; every emitted `probe_writes[*]` role code, phase code, `(pointer_id, object_id, candidate_id, role, phase)` tuple, and lane-trace role/phase is checked against `q6_probe_targets.priority_targets`. This prevents a stale or hand-edited probe manifest from reporting stage evidence for the wrong store site. | `scripts/instrument-spirv-noop-probe.py`; `scripts/verify-spirv-probe-manifest.py`; host test `tests.test_gpu_abi_contract`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-05 Q6 reduction static-support lane | Q6 probe manifests now carry structural proof for `reduction_candidate` stores instead of trusting positional labels alone. Native reduction-style stores must backward-slice to a same-base Workgroup `OpLoad`; function-accumulator/descriptor-backed stores must slice to concrete descriptor load leaves. In both paths the slice must not depend on the debug probe descriptor, and the manifest verifier fails closed if `role_static_support`/`stored_value` evidence is missing, false, or stale. The preserved safe/native/effective Q6 probe manifests were regenerated under this schema. | `scripts/analyze-spirv.py`; `scripts/verify-spirv-probe-manifest.py`; fixtures `docs/test/spirv-q6k-*/*.probe.json`; host test `tests.test_gpu_abi_contract`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-05 Q6 stage-target dataflow compare lane | Static dataflow comparison now includes `q6_stage_targets`, `q6_final_store_execution_shape`, and a derived `q6_static_boundary` summary, so native/effective comparisons expose pre-final Workgroup-store roles, reduction support kind, actual Workgroup base ids, debug-probe independence, Workgroup-load reachability, descriptor-load leaves, named arithmetic histograms, and whether final-store flow is tied to a locally consistent `LocalSize`/`BuiltIn WorkgroupSize` shape. This closes a diagnostic gap where final-store value flow could match while the reduction/probe target path or execution shape changed before the final binding-2 store. | `scripts/compare-spirv-dataflow.py`; host test `tests.test_gpu_abi_contract`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-04 Q6 workgroup execution-shape lane | Static analysis now records `workgroup_execution_shape` so native/effective SPIR-V comparisons can distinguish preserved Q6 final-store dataflow from a remaining LocalSize/BuiltIn WorkgroupSize execution-shape mismatch. The preserved native/effective Q6 artifacts now show `q6_final_store_value_flow`, load paths, and store paths matching, while the old local-size-only effective artifact is flagged as `statically_consistent=false` because `LocalSize=[32,1,1]` still has a `BuiltIn WorkgroupSize` default of `[1,1,1]`. The executor now fail-closes if a LocalSize legalization produces an effective module that is still inconsistent or outside the Vulkan local invocation bound. | `scripts/analyze-spirv.py`; `scripts/compare-spirv-dataflow.py`; `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-03 Vulkan P1 core fail-closed surface lane | The ICD now exposes additional generic core Vulkan entry points needed by non-llama Vulkan 1.0/1.1/1.2 clients while keeping unsupported capabilities conservative: legacy sparse image requirements report zero, sparse queue binds reject non-empty work, buffer views are validated, sampler YCbCr conversions fail closed with null handles, buffer-device-address queries return zero, and command-pool trim is a no-op. Descriptor update templates were promoted later by the 2026-07-13 descriptor update template lane. KHR/EXT aliases remain hidden when their extensions are not advertised. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host test `tests.test_gpu_abi_contract`; native build `scripts/build-gpu-shim.sh`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-02 Vulkan single-device group logical API lane | After exposing a single physical-device group, the ICD now also exposes `vkGetDeviceGroupPeerMemoryFeatures` and `vkCmdSetDeviceMask` as core Vulkan device-group logical APIs. Peer memory features are reported as zero, command-buffer device masks are accepted only for the single local device bit, and KHR aliases remain hidden because `VK_KHR_device_group` is not advertised. This prevents generic Vulkan 1.1 clients from failing on missing symbols while avoiding false multi-device support. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host test `tests.test_gpu_abi_contract`; native build `scripts/build-gpu-shim.sh`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-02 Vulkan sparse-image requirements2 fail-closed lane | The ICD now exposes the core `vkGetImageSparseMemoryRequirements2` query plus its KHR alias in the dispatch table. Because the bridge does not advertise sparse resources or `VK_KHR_get_memory_requirements2`, the query returns zero sparse requirements and the KHR alias remains hidden by advertisement gating. This closes another generic Vulkan 1.1 query surface without claiming sparse memory support. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host test `tests.test_gpu_abi_contract`; native build `scripts/build-gpu-shim.sh`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-02 Vulkan descriptor-support and external-handle fail-closed lane | The ICD now exposes `vkGetDescriptorSetLayoutSupport` as a core Vulkan 1.1 descriptor layout query and keeps the KHR alias hidden because `VK_KHR_maintenance3` is not advertised. The same lane hardens `vkAllocateMemory`, `vkCreateFence`, `vkCreateSemaphore`, device memory-requirements queries, and buffer binding so unsupported external-handle, device-mask, timeline, and unknown pNext/flag paths fail closed instead of being silently ignored. This remains generic Vulkan pass-through hardening and does not modify llama.cpp, Dockerfiles, models, prompts, or SPIR-V payload bytes. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host test `tests.test_gpu_abi_contract`; native build `scripts/build-gpu-shim.sh`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-01 Vulkan 1.1 conservative capability-query surface lane | The ICD now exposes the core Vulkan 1.1 physical-device group, sparse-image-format2, and external buffer/semaphore/fence property query APIs. These APIs return a single-device group or zero unsupported capabilities instead of leaving modern clients with missing entry points. KHR aliases remain present in the dispatch table but hidden because the matching KHR instance extensions are not advertised; internal bridge FDs are not exposed as Vulkan external memory or sync handles. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host test `tests.test_gpu_abi_contract`; native build `scripts/build-gpu-shim.sh`; APK gate `:app:assembleCompatDebug`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-06-16 bounded dispatch+graphics mixed-submit lane | Generic compute dispatch commands are now accepted as side work before or after a graphics replay frame in the same submitted command buffer. The submit path reuses the generic SPIR-V dispatch transport for those side dispatches, splits wait sync before pre-graphics side work, defers completion sync until post-graphics side work finishes, and still rejects dispatch commands inside the graphics-frame interval without reordering them. Dispatch rejection now distinguishes active rendering (`graphics-mixed-dispatch-inside-rendering-unimplemented`) from compute recorded between render scopes (`graphics-mixed-dispatch-between-render-scopes-unimplemented`), which defines the next append-only ABI boundary for true in-frame compute replay. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host tests `tests.test_gpu_abi_contract tests.test_llama_gpu_env_parity`; native build `scripts/build-gpu-shim.sh`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-06-17 graphics range-split mixed-submit lane | The ICD now keeps the existing V6.x graphics ABI and splits a command buffer that interleaves compute dispatches between dynamic-rendering scopes into ordered execution segments: graphics range, generic dispatch, next graphics range. A range-capable graphics frame sender serializes only graphics records in `[sequence_begin, sequence_end)`, includes same-sequence graphics records such as `EndRendering` before the dispatch boundary, and injects prior graphics state records as a preamble for later segments so pipeline/descriptor/vertex/index/dynamic/push state is not lost. Dispatch inside an active rendering scope remains fail-closed; between-scope dispatch can advance without rewriting llama.cpp, Dockerfiles, models, prompts, SPIR-V bytes, descriptor bytes, or buffer contents. Transfer-only operations outside the draw-bounded graphics interval no longer extend the GPU frame and remain routed through ordered host-side transfer handling, preventing `Draw -> Dispatch -> CopyBuffer` style drops. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host tests `tests.test_gpu_abi_contract tests.test_llama_gpu_env_parity`; native build `scripts/build-gpu-shim.sh`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-06-16 packed single-aspect depth/stencil lane | The ICD and executor now treat packed `D24S8`/`D32S8` formats as transportable when the Vulkan operation selects exactly one depth or stencil aspect. Producer-side image-copy validation and executor-side attachment/descriptor/copy-range validation share the same single-aspect rule; dual-aspect ranges and attempts to merge depth and stencil copy ranges for one image still fail closed instead of guessing a packed memory layout. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `app/src/main/cpp/pdocker_gpu_executor.c`; host tests `tests.test_gpu_abi_contract tests.test_llama_gpu_env_parity`; native builds `scripts/build-gpu-shim.sh`, `scripts/build-native-android-ndk.sh`; APK gate `:app:assembleDebug`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-06-18 headless WSI sampled-usage lane | Headless swapchain images can now request `VK_IMAGE_USAGE_SAMPLED_BIT` in addition to color-attachment and transfer usage. This does not add Android native-window WSI or storage/input/depth swapchain usage; it only aligns the headless offscreen swapchain with the already implemented image-view/sampler descriptor transport when the application transitions the image into a shader-readable layout. `PRESENT_SRC_KHR` still remains a container-side WSI layout and is normalized before Android executor replay. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host tests `tests.test_gpu_abi_contract tests.test_llama_gpu_env_parity`; native build `scripts/build-gpu-shim.sh`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-06-20 headless WSI old-swapchain recreation lane | `vkCreateSwapchainKHR` now accepts a valid `oldSwapchain` for the headless WSI path instead of rejecting every swapchain recreation.  The old swapchain is not destroyed implicitly; it is only validated as a Skydnir-owned headless swapchain on the same surface, with invalid or cross-surface handles fail-closed.  Native-window WSI, non-headless surfaces, swapchain flags, and unsupported pNext remain fail-closed. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host tests `tests.test_gpu_abi_contract tests.test_llama_gpu_env_parity`; native build `scripts/build-gpu-shim.sh`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-06-20 Vulkan submit2 validation-order lane | `vkQueueSubmit2` now prevalidates all submit2 records, pNext/device-index constraints, sync metadata collection, and command-buffer arrays before clearing the submit fence. This prevents failed submit2 calls from mutating fence state before the bridge knows the whole batch is admissible. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host tests `tests.test_gpu_abi_contract tests.test_llama_gpu_env_parity`; native build `scripts/build-gpu-shim.sh`; APK gate `:app:verifyPackagedPayloadFresh :app:assembleDebug`; device install/launch on `192.168.179.21:35875`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-06-20 Vulkan submit validation-order lane | `vkQueueSubmit` now rejects `submitCount > 0 && pSubmits == NULL` before changing the submit fence state. This closes a small validation-order hole where a failed submit could still clear a signaled fence. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host tests `tests.test_gpu_abi_contract tests.test_llama_gpu_env_parity`; native build `scripts/build-gpu-shim.sh`; APK gate `:app:verifyPackagedPayloadFresh :app:assembleDebug`; device install/launch on `192.168.179.21:35875`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-06-20 headless WSI duplicate-present fail-closed lane | `vkQueuePresentKHR` now rejects duplicate `(swapchain, imageIndex)` targets within the same `VkPresentInfoKHR` before consuming wait semaphores or releasing acquired swapchain images. This keeps the headless WSI state machine two-phase: all present targets must validate first, then and only then are sync and acquired-state mutations applied. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host tests `tests.test_gpu_abi_contract tests.test_llama_gpu_env_parity`; native build `scripts/build-gpu-shim.sh`; APK gate `:app:verifyPackagedPayloadFresh :app:assembleDebug`; device install/launch on `192.168.179.21:35875`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-06-20 Vulkan graphics dynamic vertex-stride lane | `VK_DYNAMIC_STATE_VERTEX_INPUT_BINDING_STRIDE` is now carried as a normal dynamic graphics state instead of failing as an unknown pipeline state. `vkCmdBindVertexBuffers2{,EXT}` records explicit pStrides through an ABI command flag and the existing vertex-binding stride field; Android replay uses `vkCmdBindVertexBuffers2` only for stride-present commands and keeps legacy `vkCmdBindVertexBuffers` for normal binds. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `app/src/main/cpp/pdocker_gpu_executor.c`; `app/src/main/cpp/pdocker_gpu_abi.h`; `docker-proot-setup/src/gpu/pdocker_gpu_abi.h`; host test `tests.test_gpu_abi_contract`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-06-20 headless WSI state-validation lane | Headless swapchain queries, acquire, and present now validate the runtime swapchain/image/memory relationship before mutating state. Acquire rejects invalid, timeline, or already-signaled sync objects before marking an image acquired. Present validates `VkPresentInfoKHR`, wait semaphores, swapchain image ownership, acquired state, and `PRESENT_SRC_KHR` layout in a two-phase path so failed presents do not consume semaphores or release acquired images. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host tests `tests.test_gpu_abi_contract`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-06-20 headless WSI surface-capabilities2 lane | The headless WSI surface now advertises `VK_KHR_get_surface_capabilities2` and exposes `vkGetPhysicalDeviceSurfaceCapabilities2KHR` / `vkGetPhysicalDeviceSurfaceFormats2KHR` as strict wrappers over the already bounded headless surface queries.  Unknown `pNext` chains remain fail-closed so platform-native WSI and extended surface metadata are not implied. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host tests `tests.test_gpu_abi_contract tests.test_llama_gpu_env_parity`; native build `scripts/build-gpu-shim.sh`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-06-19 packed depth/stencil image-copy split lane | V6.10 image-to-image copies can now carry packed `D24S8`/`D32S8` dual-aspect work as two explicit command-ordered single-aspect copies, one depth and one stencil, instead of rejecting the whole image-copy operation. The executor allocates packed raw plus per-aspect staging planes, unpacks fd-backed raw image bytes before replay, records Android `vkCmdCopyImage` / readback work with one aspect bit per copy, then repacks the raw fd bytes before writeback. Buffer-to-image and image-to-buffer dual-aspect packed depth/stencil copies remain fail-closed in V6.10 because Vulkan buffer-image copy valid usage is single-aspect; any future raw packed user-buffer transport needs a separate explicit ABI/capability lane, not an implicit V6.10 meaning change. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `app/src/main/cpp/pdocker_gpu_executor.c`; host tests `tests.test_gpu_abi_contract tests.test_llama_gpu_env_parity`; native builds `scripts/build-gpu-shim.sh`, `scripts/build-native-android-ndk.sh`; payload gate `scripts/verify-native-payloads.py`; APK gate `:app:verifyPackagedPayloadFresh :app:assembleDebug`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-06-20 Vulkan graphics extended-dynamic-state2 base lane | The ICD/executor now advertise and replay the valid base `VK_EXT_extended_dynamic_state2` feature as one coherent unit: `vkCmdSetRasterizerDiscardEnable{,EXT}`, `vkCmdSetDepthBiasEnable{,EXT}`, and `vkCmdSetPrimitiveRestartEnable{,EXT}` are serialized through the existing dynamic-state ABI and replayed only when Android Vulkan reports both the feature and extension. The lane adds a static depth-bias-factor presence flag so pipelines with dynamic depth-bias enable but static bias factors do not lose those factors during Android pipeline reconstruction. `extendedDynamicState2LogicOp` and `extendedDynamicState2PatchControlPoints` remain deliberately unadvertised until their separate state/validation lanes are implemented. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `app/src/main/cpp/pdocker_gpu_executor.c`; `app/src/main/cpp/pdocker_gpu_abi.h`; `docker-proot-setup/src/gpu/pdocker_gpu_abi.h`; host test `tests.test_gpu_abi_contract`; native build `scripts/build-gpu-shim.sh`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-06-20 Vulkan graphics extended-dynamic-state2 logic-op lane | The ICD/executor now advertise and replay `extendedDynamicState2LogicOp` as a separate generic `VK_EXT_extended_dynamic_state2` subfeature. `vkCmdSetLogicOpEXT` is exposed only when executor-derived Android caps report the extension, base extended-dynamic-state2, and the logic-op subfeature; the dynamic-state ABI carries the raw `VkLogicOp` value without translation and executor replay fails closed if the Android PFN or enabled subfeature is absent. `extendedDynamicState2PatchControlPoints` remains deliberately unadvertised until the tessellation patch-state lane is implemented. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `app/src/main/cpp/pdocker_gpu_executor.c`; host tests `tests.test_gpu_abi_contract tests.test_llama_gpu_env_parity`; native builds `scripts/build-gpu-shim.sh`, `scripts/build-native-android-ndk.sh`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-06-20 Vulkan explicit blit executor-routing lane | The ICD now routes eligible command-ordered `vkCmdBlitImage` operations into the native executor frame even when the command buffer has no draw commands. The routing uses the same advertised-capability gate as the V6.15 producer metadata: single-sample color images, transfer usage, advertised `BLIT_SRC`/`BLIT_DST`, and linear-filter support only when advertised. This removes an unreachable pure-blit path where executor-capable blits could otherwise fall into the non-authoritative host fallback instead of Android Vulkan replay. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host tests `tests.test_gpu_abi_contract tests.test_llama_gpu_env_parity`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-06-20 Vulkan explicit resolve executor-routing lane | The ICD now routes eligible command-ordered `vkCmdResolveImage` operations into the native executor frame even when the command buffer has no draw commands. The eligibility gate mirrors the executor's conservative resolve contract: same non-depth/stencil color format, source MSAA, destination single-sample, transfer usage, transfer/general layouts, advertised color-attachment plus transfer features, and bounded mip/layer/offset/extent ranges. Host fallback remains deliberately closed because Vulkan resolve is sample reduction, not a byte copy. Ineligible resolve operations therefore stay outside this lane rather than being silently reinterpreted on the CPU. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host tests `tests.test_gpu_abi_contract tests.test_llama_gpu_env_parity`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-06-20 Vulkan graphics extended-dynamic-state2 patch-control lane | The ICD/executor now advertise and replay `extendedDynamicState2PatchControlPoints` as the tessellation subfeature of `VK_EXT_extended_dynamic_state2`. `vkCmdSetPatchControlPointsEXT` is exposed only when executor-derived Android caps report the extension, base extended-dynamic-state2, and the patch-control subfeature; the existing dynamic-state ABI carries the raw `uint32_t` patch count, and executor replay validates it against Android `maxTessellationPatchSize` before issuing the native command. Tessellated draws with dynamic patch-control points now fail closed unless a valid dynamic value has been recorded. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `app/src/main/cpp/pdocker_gpu_executor.c`; host tests `tests.test_gpu_abi_contract tests.test_llama_gpu_env_parity`; native builds `scripts/build-gpu-shim.sh`, `scripts/build-native-android-ndk.sh`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-06-20 Vulkan strict sampled depth/stencil descriptor lane | Generic V5/strict dispatch image materialization now accepts read-only `VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE` and `VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER` descriptors over single-aspect depth or stencil views.  The executor no longer treats sampled depth/stencil image usage as unsupported, reuses the existing packed-depth/stencil plane unpacker for optimal-tiled fd-backed staging, copies upload/download subresources one aspect at a time, and keeps storage-image descriptors color-only.  Dual-aspect depth/stencil descriptor views and non-standard raw packed buffer-image transport remain fail-closed. | `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-06-15 image format/view fail-closed lane | The ICD and executor now share a conservative image transport boundary: unknown formats are no longer assigned a guessed 16-byte pixel size, image materialization rejects unsupported format/usage combinations, image views must match the source image format, plane aspects and mutable-format views are unsupported, and mip/layer ranges must be concrete and in bounds. The ICD normalizes `VK_REMAINING_MIP_LEVELS`/`VK_REMAINING_ARRAY_LAYERS` for image views before storing them; the executor rejects any remaining sentinel in transported tables. Compressed, multiplanar, YCbCr, and mutable-format image support remains fail-closed instead of being silently byte-linearized. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `app/src/main/cpp/pdocker_gpu_executor.c`; host tests `tests.test_gpu_abi_contract tests.test_llama_gpu_env_parity`; native builds `scripts/build-gpu-shim.sh`, `scripts/build-native-android-ndk.sh`; no llama.cpp/Dockerfile/model/prompt changes |

| 2026-06-20 Vulkan event wait barrier fail-closed lane | `vkCmdWaitEvents`, `vkCmdSetEvent2`, and `vkCmdWaitEvents2` now fail closed when event-scoped dependency/barrier payloads cannot be preserved in the current graphics/event ABI instead of replaying them as standalone barriers and dropping event scope. Stage-only event set/wait remains supported. | `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; host tests `tests.test_gpu_abi_contract tests.test_llama_gpu_env_parity`; native build `scripts/build-gpu-shim.sh`; payload gate `scripts/verify-native-payloads.py`; APK gate `:app:verifyPackagedPayloadFresh :app:assembleDebug`; device install/launch on `192.168.179.21:35875`; no llama.cpp/Dockerfile/model/prompt changes |
| 2026-07-11 Vulkan V6.26 multi-event wait barrier lane | Legacy `vkCmdWaitEvents` with multiple events and one barrier payload is now preserved through an append-only V6.26 event-wait reference table.  The producer records one wait command plus ordered event ids, host fallback checks all events, and Android replay resolves all event handles while collecting the barrier payload once before issuing native `vkCmdWaitEvents` with `wait_event_count`.  This closes the former multi-event fail-closed lane without changing llama.cpp, Dockerfile, model, or prompt inputs.  Remaining synchronization gaps are now narrower: sync2 pNext payloads and dependency flags beyond the BY_REGION/DEVICE_GROUP allow-list are still explicit fail-closed cases. | `app/src/main/cpp/pdocker_gpu_abi.h`; `docker-proot-setup/src/gpu/pdocker_gpu_abi.h`; `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `app/src/main/cpp/pdocker_gpu_executor.c`; tests `tests.test_gpu_abi_contract` and `tests.test_vulkan_icd_sync_harness`; native builds `scripts/build-gpu-shim.sh`, `scripts/build-native-android-ndk.sh`; APK `:app:assembleDebug`; `verify-fast`; device install/launch on `192.168.179.21:38537`; commit `405aa4fc` |
| 2026-07-11 Vulkan FD transport cap alignment | The V5/V6 frame ABI now distinguishes the protocol fd index range (`PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_FDS`) from the Android Unix-domain socket SCM_RIGHTS transport cap (`PDOCKER_GPU_TRANSPORT_MAX_PASSED_FDS`).  The producer-side ICD rejects oversized fd-bearing frames before `sendmsg`, the executor receive arrays and V5/V6 validators use the same shared transport cap, and executor capabilities advertise `transport_max_passed_fds` so the real runtime boundary is visible. | `app/src/main/cpp/pdocker_gpu_abi.h`; `docker-proot-setup/src/gpu/pdocker_gpu_abi.h`; `docker-proot-setup/src/gpu/pdocker_vulkan_icd.c`; `app/src/main/cpp/pdocker_gpu_executor.c`; host test `tests.test_gpu_abi_contract`; no llama.cpp/Dockerfile/model/prompt changes |


### Vulkan graphics image-aspect/V6.14-V6.23 status (2026-06-14 docs audit)

The current image-aspect slice changes the V6.10 ground truth: interleaved
image-copy replay is no longer color-only.  V6.10 now covers fd-backed
single-aspect color/depth/stencil subresources for `vkCmdCopyBufferToImage`,
`vkCmdCopyImageToBuffer`, and `vkCmdCopyImage`, including packed `D24S8` and
`D32S8` images when exactly one depth or stencil aspect is selected. Matching
source/destination aspects remain required for image-to-image copies.

The append-only graphics ABI/replay chain after V6.13 is also part of the
current handoff:

- **V6.14** serializes command-ordered `vkCmdResolveImage`; eligible
  color MSAA resolves are routed through executor replay when advertised caps
  include multisample resolve support. Host fallback remains closed.
- **V6.15** serializes command-ordered `vkCmdBlitImage`; replay is currently
  fail-closed unless advertised format features include blit/filter support.
- **V6.16** serializes and replays `vkCmdClearAttachments` metadata scoped to
  the active dynamic-rendering state.
- **V6.17/V6.18** serialize query reset/write-timestamp/begin/end plus
  copy-query-results metadata and executor-side query result writeback.
- **V6.19/V6.21** carry submit synchronization and submit2 metadata, including
  binary/timeline semaphore, fence, and device-index fields.
- **V6.20** carries image-layout range metadata so replay can materialize
  per-subresource initial layouts instead of guessing a single image layout.
- **V6.22/V6.23** carry multisample and tessellation pipeline state metadata.

Residual graphics gaps remain explicit: descriptor dual-aspect depth/stencil
views, any non-standard raw packed depth/stencil buffer-image transport beyond
Vulkan's single-aspect valid usage, explicit compressed/multiplanar image
support beyond the current fail-closed gate, unresolved MSAA store/readback,
true cross-family ownership transfer, dispatch inside an active rendering
scope, and broader synchronization are still fail-closed until they have their
own ABI/evidence lanes.  The V6.1
image-barrier range/aspect and copy2 pNext gaps have been audited closed.

Next active implementation slices should reduce the remaining residuals without
weakening the fail-closed boundary. Acceptance checks for any image/barrier
slice:

- Producer serialization normalizes `VK_REMAINING_MIP_LEVELS` and
  `VK_REMAINING_ARRAY_LAYERS` to concrete mip/layer counts before emitting V6.1
  image-barrier metadata; the executor rejects any unnormalized sentinel value
  that reaches replay.
- Barrier1 and barrier2 fixtures cover fd-backed color, pure-depth, and
  pure-stencil images with aspect masks that are valid for each serialized
  format, and reject empty, out-of-bounds, or overflowed subresource ranges.
- Negative fixtures keep metadata/plane aspects, compressed/multiplanar images,
  invalid color-vs-depth/stencil masks, and ambiguous dual-aspect packed depth/stencil
  ranges fail-closed. Plane/compressed/multiplanar support requires a future
  explicit ABI lane; the current bridge must not reinterpret them as
  byte-linear color images.
- Same-family/ignored queue-family behavior remains limited to the existing
  normalization rules; true cross-family ownership transfer and broader
  synchronization remain explicit non-goals for this slice.
- Host acceptance after the future code change is
  `python3 -m unittest tests.test_gpu_abi_contract tests.test_llama_gpu_env_parity`,
  plus synchronized ABI headers/native payload freshness checks whenever the
  implementation touches the graphics ABI or executor payloads.


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

P6 command-buffer execution, color/depth/stencil attachment writeback,
read-only vertex/index buffers, read-only buffer descriptors, and read-only
sampled image/sampler descriptor replay now exist for the currently supported
subset.  V6.10 image-copy replay is aspect-aware for fd-backed single-aspect
color/depth/stencil subresources, including packed single-aspect depth/stencil; V6.14-V6.23 add resolve,
blit, clear-attachments, query, submit-sync/submit2, image-layout-range,
multisample, and tessellation metadata.  Optimal-tiled sampled textures are
uploaded through the executor-owned staging buffer when the serialized image
view gives a bounded single-aspect color/depth/stencil, including packed
`D24S8`/`D32S8` depth or stencil mip/layer range; storage-image descriptors remain color-only,
and dual-aspect depth/stencil descriptors remain fail-closed.
Graphics evidence can validate producer/executor ABI understanding, pipeline
materialization, attachment image materialization, descriptor set layout/update,
staged sampled-image upload, vertex/index draw recording, queue submit/fence
wait, and stored attachment/image-copy writeback.  This is still not full Vulkan
pass-through: writable storage-buffer descriptors are supported for the current
subset, including writable storage-buffer and storage-image descriptors;
input-attachment descriptors are also replayed when they are image-view-only and
use a validated read-only/general input layout.  Copy+draw mixed submit
semantics outside the serialized V6.9-V6.16 lanes, true cross-family ownership
transfer, dispatch inside the graphics-frame interval, unresolved MSAA
store/readback, dual-aspect depth/stencil copies, and broader
synchronization remain fail-closed.  It must not be
mixed with llama
Q6 correctness claims, served-HTTP readiness, or benchmark claims until a
dedicated correctness artifact exercises the graphics writeback path.

ABI maintenance rule: `app/src/main/cpp/pdocker_gpu_abi.h` and
`docker-proot-setup/src/gpu/pdocker_gpu_abi.h` are byte-for-byte synchronized
contract headers.  Do not hand-edit one side only.  Any ABI change must update
both headers in the same commit and must pass
`test_container_and_apk_gpu_abi_headers_stay_in_sync` plus the schema hash
contract tests before it is promoted.  Graphics V6.x minor headers are an
append-only chain through V6.23: each newer `PdockerGpuVulkanGraphicsV6xxFrameHeader`
embeds the prior header/extensions and adds only new table ranges, schema
hashes, and command IDs.  Do not reinterpret any V6.0-V6.23 fields or
retroactively change older schema hashes.  Lane-specific validation must stay
spec-sensitive: V6.10 image copies validate image aspects and copy spans, V6.11
fill/update validates 4-byte alignment and payload bounds, V6.14/V6.15 validate
resolve/blit aspects and extents, V6.16 validates active-rendering attachment
scope, V6.20 validates image-layout ranges, and V6.22/V6.23 validate Android
runtime feature/limit support before replay.

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
debug binding.  Fresh device-side evidence must be interpreted by the
manifest-driven parser embedded in `scripts/android-llama-gpu-compare.sh`;
`scripts/parse-q6k-probe-u32.py` is retained only for archived fixed-layout
fixtures.
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
| `icd_to_executor_size_option` | `PDOCKER_GPU_RESIDENT_CACHE_MIN_BYTES`, `PDOCKER_GPU_MUTABLE_BUFFER_CACHE_MAX_BYTES`, `PDOCKER_GPU_STRICT_GRAPH_CACHE_MAX_BYTES`, `PDOCKER_GPU_STRICT_DEVICE_LOCAL_STAGING_MAX_TRANSFER_BYTES`, `PDOCKER_GPU_SPIRV_PROBE_DEBUG_BINDING`, `PDOCKER_GPU_WRITEONLY_DIRTY_PROBE_MIN_BYTES` | ICD appends a parsed unsigned-size command token; malformed values are ignored rather than guessed. |
| `icd_to_executor_string_option` | `PDOCKER_GPU_FAILED_SPIRV_DIR`, `PDOCKER_GPU_SPIRV_DUMP_DIR` | ICD appends bounded hex-encoded string tokens and the executor consumes those tokens before falling back to APK-process env; malformed or oversized values fail closed before dispatch. |
| `app_process_only` | `PDOCKER_GPU_DISABLE_ANDROID_VULKAN`, `PDOCKER_GPU_DISABLE_ANDROID_OPENCL`, `PDOCKER_ANDROID_OPENCL_LIBRARY` | Read by the APK/executor process before or outside per-dispatch Vulkan command emission. Forwarding these only into the container is not a reliable override. |
| `deprecated_or_invalid` | _none in the manifest env set_ | Keep unsupported work tokens out of env classification. |
| `needs_bridge` | `PDOCKER_GPU_CHAIN_COMPAT_FEATURE_STRUCTS`, `PDOCKER_GPU_DISPATCH_PROFILE_LOG`, `PDOCKER_GPU_RETRY_MATERIALIZE_SPECIALIZATION`, `PDOCKER_GPU_UNSAFE_DIRTY_WRITEBACK_CACHE`, `PDOCKER_GPU_WRITEBACK_FULL_HASH_MAX_BYTES` | Manifest forwarding can make these look requested, but the current executor-side behavior still depends on APK-process `getenv()` or an unreflected default. Do not interpret a run as having honored these until a dispatch option and, for correctness-affecting booleans, JSON reflection exist. |

`needs_bridge` priority, highest risk first:

1. `PDOCKER_GPU_RETRY_MATERIALIZE_SPECIALIZATION` and
   `PDOCKER_GPU_CHAIN_COMPAT_FEATURE_STRUCTS` - can change shader/module
   creation paths and feature-chain interpretation, so stale executor defaults
   can invalidate SPIR-V blocker conclusions.
2. `PDOCKER_GPU_DISPATCH_PROFILE_LOG` and
   `PDOCKER_GPU_WRITEBACK_FULL_HASH_MAX_BYTES` - affect evidence capture.  A
   missing bridge may look like missing Q6 evidence rather than a failed knob.
3. `PDOCKER_GPU_UNSAFE_DIRTY_WRITEBACK_CACHE` - safety gate for dirty-writeback
   caching; keep it fail-closed until it has an explicit reflected option.

`PDOCKER_GPU_LEGALIZE_WORKGROUP_SIZE_FROM_SPEC` is no longer in
`needs_bridge`: it is a reflected bool dispatch option and remains part of the
Q6 required overlay.  `PDOCKER_GPU_FAILED_SPIRV_DIR` and
`PDOCKER_GPU_SPIRV_DUMP_DIR` are bounded string dispatch options; they are
diagnostic path controls, not correctness propagation booleans.

String option design: add an ICD-to-executor string option only for bounded
ASCII/UTF-8 payloads, hex-escape separators, cap it below
`PDOCKER_GPU_VULKAN_STRING_DISPATCH_OPTION_MAX_BYTES`, and keep malformed,
newline-containing, or oversized values fail-closed before dispatch.  For
path-like diagnostics such as `PDOCKER_GPU_FAILED_SPIRV_DIR`, prefer a
host/container path chosen by compare, bridged as `failed_spirv_dir_hex=...`,
and rejected if it would truncate the command.  Add compact executor JSON
reflection only when a string option becomes correctness-critical.

- Static dispatch-option route guard: `tests.test_gpu_abi_contract` compares
  the ABI dispatch-option macros, manifest `abi_dispatch_option_env_fields`,
  bridge classifications, compare forwarding, Q6 runner `q6_required_env_overlay`,
  ICD macro-driven token emitters, executor macro-driven token parsers, and
  invalid-option fail-closed messages.  This catches runner/ICD/executor key drift
  before runtime collection can misclassify an env bridge miss as a Vulkan shader
  or device failure.

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
not only as a final-store trace.  Active runs must take candidate, role, slot,
and lane-trace layout from `instrumentation.probe_writes` in the effective
probe manifest.  The older fixed-slot candidate list is historical evidence
only and must not be used as the authoritative decoder for new runs.

The next fresh run should use this staged trace to decide whether the first
device divergence is present before reduction, during reduction, or only at the
final lane-0 store.  This is still evidence collection; it is not a safe-kernel
replacement and it does not modify llama.cpp, Dockerfile, model, prompt, or
tensor bytes.

Offline guard:
`scripts/maintenance/analyze-q6-stage-trace-spvasm.py` is a legacy fixed-layout
static analyzer for archived disassemblies.  Live Q6 probe validation should
use the effective probe manifest and compare artifact instead.  The latest
offline historical result
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

### 2026-06-28 V5/V5.1 compute frame integrity lane

V5/V5.1 compute dispatch frames now fail closed at the executor boundary for
truncated ancillary-data receives, corrupt frame/table/payload ranges,
overlapping payload regions, resource/descriptor/push/option/object/frame hash
mismatches, shader FD hash mismatches, and V5 `dispatch_hash` mismatches.  The
V5 handler also preserves parsed `base_group_x/y/z` when calling the native
Vulkan dispatch path instead of collapsing `vkCmdDispatchBase*` to zero.

`specialization_hash` is now a hard V5 frame gate after normalizing the
canonical specialization semantic hash to fixed-width `uint64_t` entry-count
and entry-size fields on both the container ICD and Android executor sides.
This avoids false mismatches across 64-bit ICD and 32-bit executor builds while
still hashing the Vulkan-visible `(constantID, size, referenced bytes)`
semantics rather than raw struct padding or unused specialization data bytes.

### 2026-06-28 Runtime bridge binary identity gate

The llama GPU compare artifact now records a `bridge_binary_identity` object
inside `gpu.diagnostics.runtime_freshness`.  The compare driver hashes the
checked-out JNI `libpdockergpuexecutor.so` and `libpdockervulkanicd.so`, then
collects the installed app runtime payload hashes from
`files/pdocker-runtime/gpu/pdocker-gpu-executor` and
`files/pdocker-runtime/lib/pdocker-vulkan-icd.so`.  If active executor or ICD
process mappings are visible, their hashes are recorded as additional runtime
evidence.

The verifier recomputes the expected checkout hashes and fails closed with
`gpu-bridge-binary-freshness-mismatch` when checked-out, installed, or visible
runtime bridge binaries disagree.  This prevents stale APK/native payloads from
being promoted as llama GPU correctness or performance evidence even when older
marker strings are present in logs.
### 2026-07-12 Producer ICD sparse descriptor binding lane

The container-facing Vulkan ICD now separates Vulkan API binding numbers from
its fixed internal descriptor slots for compute/graphics descriptor metadata.
`PdockerVkDescriptorSetLayout` records `storage_binding_numbers[]`,
`descriptor_linear_slot()` resolves API `dstBinding/srcBinding` into compact
slots for storage, and compute V4/V5/V5.1 plus graphics V6 metadata serialize
the original API binding number back to the executor.

This is intentionally a compact-slot change, not a full heap descriptor-set
rewrite: command-buffer snapshots still copy descriptor sets by value, so
turning descriptor storage into pointers would require deep-copy/free snapshot
helpers first.  The remaining hard cap is descriptor entry count, not sparse
API binding number.





### 2026-07-13 Vulkan dedicated allocation bind enforcement lane

Dedicated allocation state is now preserved from allocation through bind.
`VkMemoryDedicatedAllocateInfo` records the target buffer or image on the
Skydnir memory object after pNext validation.  `vkBindBufferMemory` and
`vkBindImageMemory` then enforce the Vulkan dedicated-allocation contract: the
recorded resource type must match, the exact recorded resource must be used, and
the bind offset must be zero.  Mismatches fail closed before public
`VK_KHR_dedicated_allocation` extension advertising is widened.

### 2026-07-13 Vulkan pipeline robustness create-info lane

Pipeline robustness create-info is now accepted only as default metadata.
`vkCreateComputePipelines` and `vkCreateGraphicsPipelines` accept
`VkPipelineRobustnessCreateInfo` when all storage-buffer, uniform-buffer,
vertex-input, and image behavior fields are `DEVICE_DEFAULT`.  Any non-default
behavior fails closed with `VK_ERROR_FEATURE_NOT_PRESENT`, because the bridge
still does not advertise or replay `VK_EXT_pipeline_robustness` semantics.
Graphics pipeline object capture also treats the already-validated default-only
struct as no-op metadata instead of marking the pipeline unsupported.

### 2026-07-13 Vulkan robustness false-only/query-property lane

Robustness pNext structs are now handled as generic Vulkan API-surface
compatibility without silently enabling semantics the bridge does not replay.
`VkPhysicalDeviceRobustness2FeaturesEXT`,
`VkPhysicalDeviceImageRobustnessFeatures`, and
`VkPhysicalDevicePipelineRobustnessFeatures` are queryable as false-only, and
any true request fails closed during `vkCreateDevice`.  The corresponding
Robustness2 and pipeline robustness property structs are also initialized in
`vkGetPhysicalDeviceProperties2`, using conservative alignment/default behavior
values.  The ICD still does not advertise the robustness extensions or accept
non-default pipeline robustness semantics.

### 2026-07-13 Vulkan standalone core feature pNext lane

Standalone core feature structs now use the same query/validation path as their
aggregate Vulkan 1.1/1.2 counterparts instead of being rejected only because
their struct form was different.  `VkPhysicalDeviceMultiviewFeatures` mirrors
the existing Vulkan 1.1 `multiview` advertisement and still keeps geometry and
tessellation multiview false.  `VkPhysicalDeviceVariablePointersFeatures`,
`VkPhysicalDeviceProtectedMemoryFeatures`,
`VkPhysicalDeviceShaderDrawParametersFeatures`,
`VkPhysicalDeviceShaderAtomicInt64Features`, and
`VkPhysicalDeviceImagelessFramebufferFeatures` are queryable as false-only; any
true request remains fail-closed until the bridge has real transport/replay
semantics for that feature.

### 2026-07-13 Vulkan shader-demote no-op feature lane

`vkGetPhysicalDeviceFeatures2` now handles
`VkPhysicalDeviceShaderDemoteToHelperInvocationFeatures` explicitly and reports
`shaderDemoteToHelperInvocation = VK_FALSE`.  `vkCreateDevice` accepts the
feature struct only when false.  A true request remains fail-closed because
Skydnir does not advertise `VK_EXT_shader_demote_to_helper_invocation` and the
bridge does not validate or replay shader-demote helper-invocation semantics as
part of its generic Vulkan transport.

### 2026-07-13 Vulkan memory-priority no-op feature/allocation lane

`vkGetPhysicalDeviceFeatures2` now handles
`VkPhysicalDeviceMemoryPriorityFeaturesEXT` explicitly and reports
`memoryPriority = VK_FALSE`.  `vkCreateDevice` accepts the feature struct only
when false.  `vkAllocateMemory` also classifies
`VkMemoryPriorityAllocateInfoEXT`, accepting only `priority == 0.5f`, the
Vulkan default priority value, as no-op metadata.  Any non-default priority
request remains fail-closed because Skydnir does not advertise
`VK_EXT_memory_priority`, does not replay allocation priority hints on Android,
and does not expose `vkSetDeviceMemoryPriorityEXT`.

### 2026-07-13 Vulkan private-data no-op feature/create-info lane

`vkGetPhysicalDeviceFeatures2` now handles
`VkPhysicalDevicePrivateDataFeatures` explicitly and reports
`privateData = VK_FALSE`.  `vkCreateDevice` accepts the same feature struct only
when false, and accepts `VkDevicePrivateDataCreateInfo` only when
`privateDataSlotRequestCount == 0`.  Any request to enable private data or
reserve private-data slots still fails with `VK_ERROR_FEATURE_NOT_PRESENT`
because Skydnir does not advertise `VK_EXT_private_data`, does not expose the
private-data slot commands, and does not serialize per-object private-data
state through the bridge.  This is a no-op metadata compatibility lane, not a
private-data implementation.

### 2026-07-13 Vulkan memory allocation export-handle no-op pNext lane

`vkAllocateMemory` now classifies `VkExportMemoryAllocateInfo` explicitly.
A zero `handleTypes` value is accepted as a no-op external-memory export
metadata struct, matching the bridge policy used for buffer/image external
create-info pNext chains.  Any nonzero external-memory handle request still
returns `VK_ERROR_FEATURE_NOT_PRESENT` because Skydnir does not advertise,
serialize, or replay Vulkan external memory handles.  This closes another
allocation-chain compatibility gap without adding executor ABI fields or
pretending external-handle interop exists.

### 2026-07-13 Vulkan memory allocation capture-address no-op pNext lane

`vkAllocateMemory` now classifies `VkMemoryOpaqueCaptureAddressAllocateInfo`
explicitly.  A zero `opaqueCaptureAddress` is accepted as no-op
capture/replay metadata, which keeps modern Vulkan allocation chains from
failing when they attach the standard struct with default values.  A nonzero
address still returns `VK_ERROR_FEATURE_NOT_PRESENT` because Skydnir does not
advertise buffer-device-address capture/replay and the executor ABI has no
portable way to preserve caller-selected device addresses.  Unknown memory
allocation pNext structs and real multi-device allocation masks remain
fail-closed.

### 2026-07-13 Vulkan buffer memory-requirements pNext lane

The container-facing ICD now keeps `vkGetDeviceBufferMemoryRequirements`
consistent with `vkCreateBuffer` for buffer-create pNext chains.  No-op buffer
pNext structs accepted by `vkCreateBuffer` (`VkExternalMemoryBufferCreateInfo`
with no external handle type and matching `VkBufferUsageFlags2CreateInfo`) also
produce normal maintenance4 memory requirements.  Unsupported handle types or
usage mismatches remain fail-closed by returning zero requirements from the
query path.  This is a generic Vulkan pass-through compatibility fix, not a
llama.cpp- or model-specific workaround.

### 2026-07-13 Vulkan buffer-view usage2 pNext lane

`vkCreateBufferView` now accepts the valid maintenance5
`VkBufferUsageFlags2CreateInfo` pNext only when it is execution-neutral for the
current bridge ABI: the 64-bit usage must be nonzero, texel-buffer-only, and
match the texel usage already present on the backing buffer.  Narrowed view
usage, non-texel usage bits, duplicates, and unknown buffer-view pNext structs
remain fail-closed because the buffer-view transport does not yet carry a
separate per-view usage field.  This is a generic Vulkan API-surface widening,
not a llama.cpp- or model-specific path.

### 2026-07-13 Vulkan buffer usage2 effective-usage lane

`VkBufferUsageFlags2CreateInfo` on `VkBufferCreateInfo` is now treated as the
buffer's effective usage when it is valid and fits the bridge's current 32-bit
usage storage.  This allows maintenance5-style buffer creation and
`vkGetDeviceBufferMemoryRequirements` queries where the legacy
`VkBufferCreateInfo::usage` field is zero and the 64-bit pNext struct supplies
the real usage.  Zero, duplicate, or out-of-range usage2 payloads remain
fail-closed, and buffer-view usage2 still accepts only the no-op texel-usage
case until the transport carries a distinct per-view usage field.

### 2026-07-13 Vulkan image/view shape compatibility lane

Image creation and image-view replay now fail-close on generic Vulkan shape
compatibility before transport/native replay.  Cube-compatible images must be
2D, square, one-depth-layer images with at least six array layers.  Image-view
types are validated against source image type, cube compatibility, layer count,
and cube-array layer alignment on both the container ICD and Android executor
side.  This prevents invalid 2D/3D/cube view reinterpretation from reaching
Android `vkCreateImageView` and keeps the lane generic; llama.cpp, Dockerfiles,
models, prompts, and shader bytes are unchanged.

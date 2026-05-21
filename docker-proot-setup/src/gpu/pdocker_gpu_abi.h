#ifndef PDOCKER_GPU_ABI_H
#define PDOCKER_GPU_ABI_H

/*
 * Backend-neutral GPU command ABI labels.
 *
 * Containers should target this pdocker contract through a glibc shim. Android
 * APIs such as GLES, Vulkan, OpenCL, or NNAPI are executor implementations
 * behind the ABI and must stay hidden from container application engines.
 */
#define PDOCKER_GPU_COMMAND_API "pdocker-gpu-command-v1"
#define PDOCKER_GPU_ABI_VERSION "0.1"
#define PDOCKER_GPU_EXECUTOR_ROLE "gpu-command-executor"
#define PDOCKER_GPU_LLM_ENGINE_LOCATION "container"
#define PDOCKER_GPU_CONTAINER_CONTRACT "glibc-shim-command-queue"
#define PDOCKER_GPU_VECTOR_ADD_DEFAULT_N 262144u
#define PDOCKER_GPU_VECTOR_ADD_MAX_N 4194304u

/*
 * Single source of truth for Vulkan dispatch options that cross the
 * container ICD -> APK executor boundary.  Keep this list backend-neutral:
 * env_name is the container-visible knob, option_name is the command-token
 * field, and the executor fields are the VulkanDispatchOptions members that
 * receive the parsed value.
 */
#define PDOCKER_GPU_VULKAN_BOOL_DISPATCH_OPTIONS(X) \
    X(PDOCKER_GPU_WRITEONLY_DIRTY_PROBE, dirty_probe, has_dirty_probe, dirty_probe, 0) \
    X(PDOCKER_GPU_WRITEONLY_DIRTY_WRITEBACK, dirty_writeback, has_dirty_writeback, dirty_writeback, 0) \
    X(PDOCKER_GPU_WRITEONLY_BUFFER_CACHE, writeonly_cache, has_writeonly_buffer_cache, writeonly_buffer_cache, 0) \
    X(PDOCKER_GPU_MUTABLE_BUFFER_CACHE, mutable_cache, has_mutable_buffer_cache, mutable_buffer_cache, 1) \
    X(PDOCKER_GPU_RESIDENT_CACHE, resident_cache, has_resident_cache, resident_cache, 1) \
    X(PDOCKER_GPU_STRICT_PASSTHROUGH, strict_passthrough, has_strict_passthrough, strict_passthrough, 0) \
    X(PDOCKER_GPU_STRICT_RECONCILIATION, strict_reconciliation, has_strict_reconciliation, strict_reconciliation, 0) \
    X(PDOCKER_GPU_STRICT_DEVICE_LOCAL_STAGING, strict_device_local_staging, has_strict_device_local_staging, strict_device_local_staging, 0) \
    X(PDOCKER_GPU_REWRITE_DUPLICATE_DESCRIPTOR_BINDINGS, rewrite_duplicate_descriptors, has_rewrite_duplicate_descriptors, rewrite_duplicate_descriptors, 1) \
    X(PDOCKER_GPU_MATERIALIZE_DESCRIPTOR_ALIASES, materialize_descriptor_aliases, has_materialize_descriptor_aliases, materialize_descriptor_aliases, 0) \
    X(PDOCKER_GPU_MATERIALIZE_SPIRV_SPECIALIZATION_CONSTANTS, materialize_specialization, has_materialize_specialization_constants, materialize_specialization_constants, 1) \
    X(PDOCKER_GPU_DISABLE_PIPELINE_OPTIMIZATION, disable_pipeline_optimization, has_disable_pipeline_optimization, disable_pipeline_optimization, 1) \
    X(PDOCKER_GPU_SKIP_UNUSED_DESCRIPTOR_TRANSFERS, skip_unused_descriptor_transfers, has_skip_unused_descriptor_transfers, skip_unused_descriptor_transfers, 1) \
    X(PDOCKER_GPU_USE_SPIRV_DESCRIPTOR_ACCESS, use_spirv_descriptor_access, has_use_spirv_descriptor_access, use_spirv_descriptor_access, 1) \
    X(PDOCKER_GPU_DISABLE_OVERLAP_ALIASING, disable_overlap_aliasing, has_disable_overlap_aliasing, disable_overlap_aliasing, 0) \
    X(PDOCKER_GPU_CPU_ORACLE, cpu_oracle, has_cpu_oracle, cpu_oracle, 0) \
    X(PDOCKER_GPU_Q6K_ORACLE_WRITEBACK, q6k_oracle_writeback, has_q6k_oracle_writeback, q6k_oracle_writeback, 0) \
    X(PDOCKER_GPU_Q6K_SAFE_KERNEL, q6k_safe_kernel, has_q6k_safe_kernel, q6k_safe_kernel, 0) \
    X(PDOCKER_GPU_Q4K_SAFE_KERNEL, q4k_safe_kernel, has_q4k_safe_kernel, q4k_safe_kernel, 0) \
    X(PDOCKER_GPU_Q4K_TARGETED_SPECIALIZATION, q4k_targeted_specialization, has_q4k_targeted_specialization, q4k_targeted_specialization, 0) \
    X(PDOCKER_GPU_Q4K_PIPELINE_RETRY_LADDER, q4k_pipeline_retry_ladder, has_q4k_pipeline_retry_ladder, q4k_pipeline_retry_ladder, 1) \
    X(PDOCKER_GPU_ADD_FLOAT16_CAPABILITY_FOR_STORAGE16, add_float16_capability_for_storage16, has_add_float16_capability_for_storage16, add_float16_capability_for_storage16, 0)

#define PDOCKER_GPU_VULKAN_BOOL_DISPATCH_OPTIONS_NO_HAS(X) \
    X(PDOCKER_VULKAN_DISABLE_8BIT_STORAGE, disable_storage8, disable_storage8, 0) \
    X(PDOCKER_VULKAN_DISABLE_16BIT_STORAGE, disable_storage16, disable_storage16, 0) \
    X(PDOCKER_VULKAN_DISABLE_SUBGROUP_ARITHMETIC, disable_subgroup_arithmetic, disable_subgroup_arithmetic, 0)

#define PDOCKER_GPU_VULKAN_SIZE_DISPATCH_OPTIONS(X) \
    X(PDOCKER_GPU_MUTABLE_BUFFER_CACHE_MAX_BYTES, mutable_cache_max, has_mutable_buffer_cache_max_bytes, mutable_buffer_cache_max_bytes) \
    X(PDOCKER_GPU_RESIDENT_CACHE_MIN_BYTES, resident_cache_min, has_resident_cache_min_bytes, resident_cache_min_bytes) \
    X(PDOCKER_GPU_WRITEONLY_DIRTY_PROBE_MIN_BYTES, dirty_probe_min, has_dirty_probe_min_bytes, dirty_probe_min_bytes)

#endif

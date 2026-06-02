#ifndef PDOCKER_GPU_ABI_H
#define PDOCKER_GPU_ABI_H

#include <stdint.h>

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
    X(PDOCKER_GPU_STRICT_GRAPH_CACHE, strict_graph_cache, has_strict_graph_cache, strict_graph_cache, 1) \
    X(PDOCKER_GPU_STRICT_PASSTHROUGH, strict_passthrough, has_strict_passthrough, strict_passthrough, 0) \
    X(PDOCKER_GPU_STRICT_RECONCILIATION, strict_reconciliation, has_strict_reconciliation, strict_reconciliation, 0) \
    X(PDOCKER_GPU_STRICT_DEVICE_LOCAL_STAGING, strict_device_local_staging, has_strict_device_local_staging, strict_device_local_staging, 0) \
    X(PDOCKER_GPU_STRICT_DUPLICATE_DESCRIPTOR_NORMALIZATION, strict_duplicate_descriptor_normalization, has_strict_duplicate_descriptor_normalization, strict_duplicate_descriptor_normalization, 0) \
    X(PDOCKER_GPU_REWRITE_DUPLICATE_DESCRIPTOR_BINDINGS, rewrite_duplicate_descriptors, has_rewrite_duplicate_descriptors, rewrite_duplicate_descriptors, 1) \
    X(PDOCKER_GPU_MATERIALIZE_DESCRIPTOR_ALIASES, materialize_descriptor_aliases, has_materialize_descriptor_aliases, materialize_descriptor_aliases, 0) \
    X(PDOCKER_GPU_MATERIALIZE_SPIRV_SPECIALIZATION_CONSTANTS, materialize_specialization, has_materialize_specialization_constants, materialize_specialization_constants, 1) \
    X(PDOCKER_GPU_LEGALIZE_WORKGROUP_SIZE_FROM_SPEC, legalize_workgroup_size_from_spec, has_legalize_workgroup_size_from_spec, legalize_workgroup_size_from_spec, 1) \
    X(PDOCKER_GPU_DISABLE_PIPELINE_OPTIMIZATION, disable_pipeline_optimization, has_disable_pipeline_optimization, disable_pipeline_optimization, 1) \
    X(PDOCKER_GPU_SKIP_UNUSED_DESCRIPTOR_TRANSFERS, skip_unused_descriptor_transfers, has_skip_unused_descriptor_transfers, skip_unused_descriptor_transfers, 1) \
    X(PDOCKER_GPU_USE_SPIRV_DESCRIPTOR_ACCESS, use_spirv_descriptor_access, has_use_spirv_descriptor_access, use_spirv_descriptor_access, 1) \
    X(PDOCKER_GPU_DISABLE_OVERLAP_ALIASING, disable_overlap_aliasing, has_disable_overlap_aliasing, disable_overlap_aliasing, 0) \
    X(PDOCKER_GPU_CPU_ORACLE, cpu_oracle, has_cpu_oracle, cpu_oracle, 0) \
    X(PDOCKER_GPU_Q6K_ORACLE_WRITEBACK, q6k_oracle_writeback, has_q6k_oracle_writeback, q6k_oracle_writeback, 0) \
    X(PDOCKER_GPU_Q6K_SAFE_KERNEL, q6k_safe_kernel, has_q6k_safe_kernel, q6k_safe_kernel, 0) \
    X(PDOCKER_GPU_Q6K_COMPAT_REWRITES, q6k_compat_rewrites, has_q6k_compat_rewrites, q6k_compat_rewrites, 0) \
    X(PDOCKER_GPU_Q6K_READONLY_OVERLAP_SNAPSHOT, q6k_readonly_overlap_snapshot, has_q6k_readonly_overlap_snapshot, q6k_readonly_overlap_snapshot, 0) \
    X(PDOCKER_GPU_Q4K_SAFE_KERNEL, q4k_safe_kernel, has_q4k_safe_kernel, q4k_safe_kernel, 0) \
    X(PDOCKER_GPU_Q4K_TARGETED_SPECIALIZATION, q4k_targeted_specialization, has_q4k_targeted_specialization, q4k_targeted_specialization, 0) \
    X(PDOCKER_GPU_Q4K_PIPELINE_RETRY_LADDER, q4k_pipeline_retry_ladder, has_q4k_pipeline_retry_ladder, q4k_pipeline_retry_ladder, 0) \
    X(PDOCKER_GPU_ADD_FLOAT16_CAPABILITY_FOR_STORAGE16, add_float16_capability_for_storage16, has_add_float16_capability_for_storage16, add_float16_capability_for_storage16, 0)

#define PDOCKER_GPU_VULKAN_BOOL_DISPATCH_OPTIONS_NO_HAS(X) \
    X(PDOCKER_VULKAN_DISABLE_8BIT_STORAGE, disable_storage8, disable_storage8, 0) \
    X(PDOCKER_VULKAN_DISABLE_16BIT_STORAGE, disable_storage16, disable_storage16, 0) \
    X(PDOCKER_VULKAN_DISABLE_SUBGROUP_ARITHMETIC, disable_subgroup_arithmetic, disable_subgroup_arithmetic, 0)

#define PDOCKER_GPU_VULKAN_SIZE_DISPATCH_OPTIONS(X) \
    X(PDOCKER_GPU_MUTABLE_BUFFER_CACHE_MAX_BYTES, mutable_cache_max, has_mutable_buffer_cache_max_bytes, mutable_buffer_cache_max_bytes) \
    X(PDOCKER_GPU_RESIDENT_CACHE_MIN_BYTES, resident_cache_min, has_resident_cache_min_bytes, resident_cache_min_bytes) \
    X(PDOCKER_GPU_WRITEONLY_DIRTY_PROBE_MIN_BYTES, dirty_probe_min, has_dirty_probe_min_bytes, dirty_probe_min_bytes) \
    X(PDOCKER_GPU_SPIRV_PROBE_DEBUG_BINDING, spirv_probe_debug_binding, has_spirv_probe_debug_binding, spirv_probe_debug_binding)

/*
 * Positional VULKAN_DISPATCH_V4 binding payload schema.
 *
 * The wire format intentionally stays compact and positional for compatibility
 * with existing commands; producers append v4_binding_schema/v4_binding_fields
 * options so receivers can reject accidental schema drift instead of silently
 * reinterpreting later fields.
 *
 * schema_hash is FNV-1a64 over each "field:type\0" entry below in order.
 */
#define PDOCKER_GPU_VULKAN_DISPATCH_V4_BINDING_FIELDS(X) \
    X(descriptor_set, u32) \
    X(binding, u32) \
    X(offset, u64) \
    X(size, size) \
    X(api_offset, u64) \
    X(api_range, size) \
    X(api_buffer_size, size) \
    X(api_descriptor_type, u32) \
    X(api_dynamic, u32) \
    X(api_memory_offset, u64) \
    X(api_memory_size, size) \
    X(api_memory_id, u64) \
    X(api_buffer_id, u64)

#define PDOCKER_GPU_VULKAN_DISPATCH_V4_BINDING_FIELD_COUNT 13u
#define PDOCKER_GPU_VULKAN_DISPATCH_V4_BINDING_SCHEMA_HASH 0x4a322a1f9f143a20ull


/*
 * Framed VULKAN_DISPATCH_V5 schema foundation.
 *
 * V5 is a new command family and must not reinterpret or extend the positional
 * V4 binding list.  The executor advertises these schema hashes through
 * CAPABILITIES; ICDs may use V5 only when every advertised hash matches.
 */
#define PDOCKER_GPU_VULKAN_DISPATCH_V5_MAGIC "PDGPUV5"
#define PDOCKER_GPU_VULKAN_DISPATCH_V5_ABI_MAJOR 5u
#define PDOCKER_GPU_VULKAN_DISPATCH_V5_ABI_MINOR 0u
#define PDOCKER_GPU_VULKAN_DISPATCH_V5_COMMAND_DISPATCH 1u
#define PDOCKER_GPU_VULKAN_DISPATCH_V5_FRAME_HEADER_SCHEMA_HASH 0x3de711f5a527e2f8ull
#define PDOCKER_GPU_VULKAN_DISPATCH_V5_RESOURCE_SCHEMA_HASH 0x5fd531f2d77e9ad1ull
#define PDOCKER_GPU_VULKAN_DISPATCH_V5_DESCRIPTOR_SCHEMA_HASH 0xb262ddf93c2ca096ull
#define PDOCKER_GPU_VULKAN_DISPATCH_V5_SPECIALIZATION_SCHEMA_HASH 0xae7e0f61a22df66eull
#define PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_FRAME_BYTES (4u * 1024u * 1024u)
#define PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_FDS 253u
#define PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_RESOURCES 1024u
#define PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_DESCRIPTORS 2048u

#define PDOCKER_GPU_VULKAN_DISPATCH_V5_FRAME_HEADER_FIELDS(X) \
    X(magic, bytes8) \
    X(header_size, u16) \
    X(abi_major, u16) \
    X(abi_minor, u16) \
    X(command, u16) \
    X(flags, u32) \
    X(reserved0, u32) \
    X(frame_size, u64) \
    X(dispatch_id, u64) \
    X(fd_count, u32) \
    X(shader_fd_index, u32) \
    X(shader_size, u64) \
    X(shader_hash, u64) \
    X(gx, u32) \
    X(gy, u32) \
    X(gz, u32) \
    X(reserved1, u32) \
    X(resource_count, u32) \
    X(resource_entry_size, u32) \
    X(resource_table_offset, u64) \
    X(resource_table_size, u64) \
    X(resource_schema_hash, u64) \
    X(descriptor_count, u32) \
    X(descriptor_entry_size, u32) \
    X(descriptor_table_offset, u64) \
    X(descriptor_table_size, u64) \
    X(descriptor_schema_hash, u64) \
    X(specialization_count, u32) \
    X(specialization_entry_size, u32) \
    X(specialization_table_offset, u64) \
    X(specialization_table_size, u64) \
    X(specialization_data_offset, u64) \
    X(specialization_data_size, u64) \
    X(specialization_hash, u64) \
    X(push_offset, u64) \
    X(push_size, u64) \
    X(push_hash, u64) \
    X(entry_name_offset, u64) \
    X(entry_name_size, u64) \
    X(option_text_offset, u64) \
    X(option_text_size, u64) \
    X(option_hash, u64) \
    X(resource_hash, u64) \
    X(descriptor_hash, u64) \
    X(dispatch_hash, u64) \
    X(frame_hash, u64)
#define PDOCKER_GPU_VULKAN_DISPATCH_V5_FRAME_HEADER_FIELD_COUNT 46u

#define PDOCKER_GPU_V5_RESOURCE_TYPE_MEMORY 1u
#define PDOCKER_GPU_V5_RESOURCE_TYPE_BUFFER 2u
#define PDOCKER_GPU_V5_RESOURCE_PARENT_NONE 0xffffffffu
#define PDOCKER_GPU_V5_RESOURCE_FD_NONE 0xffffffffu
#define PDOCKER_GPU_V5_RESOURCE_FLAG_HOST_FD_BACKED (1u << 0)
#define PDOCKER_GPU_V5_RESOURCE_FLAG_DEVICE_LOCAL_PREFERRED (1u << 1)
#define PDOCKER_GPU_V5_RESOURCE_FLAG_READONLY_SNAPSHOT (1u << 2)
#define PDOCKER_GPU_V5_RESOURCE_FLAG_MUTABLE (1u << 3)
#define PDOCKER_GPU_VULKAN_DISPATCH_V5_RESOURCE_FIELDS(X) \
    X(resource_type, u32) \
    X(resource_flags, u32) \
    X(resource_id, u64) \
    X(parent_resource_index, u32) \
    X(fd_index, u32) \
    X(memory_offset, u64) \
    X(size, u64) \
    X(usage, u64) \
    X(memory_property_flags, u64) \
    X(external_offset, u64) \
    X(generation, u64)
#define PDOCKER_GPU_VULKAN_DISPATCH_V5_RESOURCE_FIELD_COUNT 11u

#define PDOCKER_GPU_V5_DESCRIPTOR_FLAG_DYNAMIC (1u << 0)
#define PDOCKER_GPU_V5_DESCRIPTOR_FLAG_WHOLE_SIZE (1u << 1)
#define PDOCKER_GPU_V5_DESCRIPTOR_FLAG_ARRAY_ENTRY (1u << 2)
#define PDOCKER_GPU_V5_ACCESS_READ (1u << 0)
#define PDOCKER_GPU_V5_ACCESS_WRITE (1u << 1)
#define PDOCKER_GPU_VULKAN_DISPATCH_V5_DESCRIPTOR_FIELDS(X) \
    X(descriptor_set, u32) \
    X(binding, u32) \
    X(array_element, u32) \
    X(descriptor_type, u32) \
    X(descriptor_flags, u32) \
    X(access_flags, u32) \
    X(resource_index, u32) \
    X(reserved0, u32) \
    X(resource_id, u64) \
    X(buffer_offset, u64) \
    X(range, u64) \
    X(transfer_offset, u64) \
    X(transfer_size, u64) \
    X(dynamic_offset, u64)
#define PDOCKER_GPU_VULKAN_DISPATCH_V5_DESCRIPTOR_FIELD_COUNT 14u

#define PDOCKER_GPU_VULKAN_DISPATCH_V5_SPECIALIZATION_FIELDS(X) \
    X(constant_id, u32) \
    X(offset, u32) \
    X(size, u64)
#define PDOCKER_GPU_VULKAN_DISPATCH_V5_SPECIALIZATION_FIELD_COUNT 3u


typedef struct PdockerGpuVulkanDispatchV5FrameHeader {
    char magic[8];
    uint16_t header_size;
    uint16_t abi_major;
    uint16_t abi_minor;
    uint16_t command;
    uint32_t flags;
    uint32_t reserved0;
    uint64_t frame_size;
    uint64_t dispatch_id;
    uint32_t fd_count;
    uint32_t shader_fd_index;
    uint64_t shader_size;
    uint64_t shader_hash;
    uint32_t gx;
    uint32_t gy;
    uint32_t gz;
    uint32_t reserved1;
    uint32_t resource_count;
    uint32_t resource_entry_size;
    uint64_t resource_table_offset;
    uint64_t resource_table_size;
    uint64_t resource_schema_hash;
    uint32_t descriptor_count;
    uint32_t descriptor_entry_size;
    uint64_t descriptor_table_offset;
    uint64_t descriptor_table_size;
    uint64_t descriptor_schema_hash;
    uint32_t specialization_count;
    uint32_t specialization_entry_size;
    uint64_t specialization_table_offset;
    uint64_t specialization_table_size;
    uint64_t specialization_data_offset;
    uint64_t specialization_data_size;
    uint64_t specialization_hash;
    uint64_t push_offset;
    uint64_t push_size;
    uint64_t push_hash;
    uint64_t entry_name_offset;
    uint64_t entry_name_size;
    uint64_t option_text_offset;
    uint64_t option_text_size;
    uint64_t option_hash;
    uint64_t resource_hash;
    uint64_t descriptor_hash;
    uint64_t dispatch_hash;
    uint64_t frame_hash;
} PdockerGpuVulkanDispatchV5FrameHeader;

typedef struct PdockerGpuVulkanDispatchV5ResourceEntry {
    uint32_t resource_type;
    uint32_t resource_flags;
    uint64_t resource_id;
    uint32_t parent_resource_index;
    uint32_t fd_index;
    uint64_t memory_offset;
    uint64_t size;
    uint64_t usage;
    uint64_t memory_property_flags;
    uint64_t external_offset;
    uint64_t generation;
} PdockerGpuVulkanDispatchV5ResourceEntry;

typedef struct PdockerGpuVulkanDispatchV5DescriptorEntry {
    uint32_t descriptor_set;
    uint32_t binding;
    uint32_t array_element;
    uint32_t descriptor_type;
    uint32_t descriptor_flags;
    uint32_t access_flags;
    uint32_t resource_index;
    uint32_t reserved0;
    uint64_t resource_id;
    uint64_t buffer_offset;
    uint64_t range;
    uint64_t transfer_offset;
    uint64_t transfer_size;
    uint64_t dynamic_offset;
} PdockerGpuVulkanDispatchV5DescriptorEntry;

typedef struct PdockerGpuVulkanDispatchV5SpecializationEntry {
    uint32_t constant_id;
    uint32_t offset;
    uint64_t size;
} PdockerGpuVulkanDispatchV5SpecializationEntry;

#endif

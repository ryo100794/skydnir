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
#define PDOCKER_GPU_VULKAN_DISPATCH_V5_ABI_MINOR_OBJECTS 1u
#define PDOCKER_GPU_VULKAN_DISPATCH_V5_COMMAND_DISPATCH 1u
#define PDOCKER_GPU_VULKAN_DISPATCH_V5_FRAME_HEADER_SCHEMA_HASH 0x3de711f5a527e2f8ull
#define PDOCKER_GPU_VULKAN_DISPATCH_V5_RESOURCE_SCHEMA_HASH 0x5fd531f2d77e9ad1ull
#define PDOCKER_GPU_VULKAN_DISPATCH_V5_DESCRIPTOR_SCHEMA_HASH 0xb262ddf93c2ca096ull
#define PDOCKER_GPU_VULKAN_DISPATCH_V5_SPECIALIZATION_SCHEMA_HASH 0xae7e0f61a22df66eull
#define PDOCKER_GPU_VULKAN_DISPATCH_V5_IMAGE_SCHEMA_HASH 0x41f750ec2c5cfc82ull
#define PDOCKER_GPU_VULKAN_DISPATCH_V5_IMAGE_VIEW_SCHEMA_HASH 0xfef2d65210c4a660ull
#define PDOCKER_GPU_VULKAN_DISPATCH_V5_SAMPLER_SCHEMA_HASH 0xc4c997c91fb85ab5ull
#define PDOCKER_GPU_VULKAN_DISPATCH_V5_DESCRIPTOR_OBJECT_SCHEMA_HASH 0xc7d2cec7923555f7ull
#define PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_FRAME_BYTES (4u * 1024u * 1024u)
#define PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_FDS 253u
#define PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_RESOURCES 1024u
#define PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_DESCRIPTORS 2048u
#define PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_IMAGES 256u
#define PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_IMAGE_VIEWS 512u
#define PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_SAMPLERS 512u

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
#define PDOCKER_GPU_V5_RESOURCE_TYPE_IMAGE 3u
#define PDOCKER_GPU_V5_RESOURCE_TYPE_IMAGE_VIEW 4u
#define PDOCKER_GPU_V5_RESOURCE_TYPE_SAMPLER 5u
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

#define PDOCKER_GPU_VULKAN_DISPATCH_V5_FRAME_HEADER_OBJECT_FIELDS(X) \
    X(image_count, u32) \
    X(image_entry_size, u32) \
    X(image_table_offset, u64) \
    X(image_table_size, u64) \
    X(image_schema_hash, u64) \
    X(image_view_count, u32) \
    X(image_view_entry_size, u32) \
    X(image_view_table_offset, u64) \
    X(image_view_table_size, u64) \
    X(image_view_schema_hash, u64) \
    X(sampler_count, u32) \
    X(sampler_entry_size, u32) \
    X(sampler_table_offset, u64) \
    X(sampler_table_size, u64) \
    X(sampler_schema_hash, u64) \
    X(object_hash, u64)
#define PDOCKER_GPU_VULKAN_DISPATCH_V5_FRAME_HEADER_OBJECT_FIELD_COUNT 16u

#define PDOCKER_GPU_V5_IMAGE_FLAG_MUTABLE_FORMAT (1u << 0)
#define PDOCKER_GPU_V5_IMAGE_FLAG_CUBE_COMPATIBLE (1u << 1)
#define PDOCKER_GPU_V5_IMAGE_FLAG_ALIAS (1u << 2)
#define PDOCKER_GPU_V5_IMAGE_FLAG_HOST_CONTENT_VALID (1u << 3)
#define PDOCKER_GPU_V5_IMAGE_FLAG_NEEDS_WRITEBACK (1u << 4)
#define PDOCKER_GPU_VULKAN_DISPATCH_V5_IMAGE_FIELDS(X) \
    X(flags, u32) \
    X(image_type, u32) \
    X(image_id, u64) \
    X(memory_resource_index, u32) \
    X(reserved0, u32) \
    X(memory_offset, u64) \
    X(memory_size, u64) \
    X(format, u32) \
    X(extent_width, u32) \
    X(extent_height, u32) \
    X(extent_depth, u32) \
    X(mip_levels, u32) \
    X(array_layers, u32) \
    X(samples, u32) \
    X(tiling, u32) \
    X(usage, u64) \
    X(create_flags, u64) \
    X(sharing_mode, u32) \
    X(initial_layout, u32) \
    X(generation, u64)
#define PDOCKER_GPU_VULKAN_DISPATCH_V5_IMAGE_FIELD_COUNT 20u

#define PDOCKER_GPU_VULKAN_DISPATCH_V5_IMAGE_VIEW_FIELDS(X) \
    X(flags, u32) \
    X(view_type, u32) \
    X(view_id, u64) \
    X(image_index, u32) \
    X(format, u32) \
    X(component_r, u32) \
    X(component_g, u32) \
    X(component_b, u32) \
    X(component_a, u32) \
    X(aspect_mask, u32) \
    X(base_mip_level, u32) \
    X(level_count, u32) \
    X(base_array_layer, u32) \
    X(layer_count, u32) \
    X(generation, u64)
#define PDOCKER_GPU_VULKAN_DISPATCH_V5_IMAGE_VIEW_FIELD_COUNT 15u

#define PDOCKER_GPU_VULKAN_DISPATCH_V5_SAMPLER_FIELDS(X) \
    X(flags, u32) \
    X(reserved0, u32) \
    X(sampler_id, u64) \
    X(mag_filter, u32) \
    X(min_filter, u32) \
    X(mipmap_mode, u32) \
    X(address_mode_u, u32) \
    X(address_mode_v, u32) \
    X(address_mode_w, u32) \
    X(mip_lod_bias_bits, u32) \
    X(anisotropy_enable, u32) \
    X(max_anisotropy_bits, u32) \
    X(compare_enable, u32) \
    X(compare_op, u32) \
    X(min_lod_bits, u32) \
    X(max_lod_bits, u32) \
    X(border_color, u32) \
    X(unnormalized_coordinates, u32) \
    X(generation, u64)
#define PDOCKER_GPU_VULKAN_DISPATCH_V5_SAMPLER_FIELD_COUNT 19u

#define PDOCKER_GPU_V5_DESCRIPTOR_OBJECT_NONE 0xffffffffu
#define PDOCKER_GPU_VULKAN_DISPATCH_V5_DESCRIPTOR_OBJECT_FIELDS(X) \
    X(descriptor_set, u32) \
    X(binding, u32) \
    X(array_element, u32) \
    X(descriptor_type, u32) \
    X(descriptor_flags, u32) \
    X(access_flags, u32) \
    X(resource_index, u32) \
    X(image_view_index, u32) \
    X(sampler_index, u32) \
    X(image_layout, u32) \
    X(resource_id, u64) \
    X(buffer_offset, u64) \
    X(range, u64) \
    X(transfer_offset, u64) \
    X(transfer_size, u64) \
    X(dynamic_offset, u64)
#define PDOCKER_GPU_VULKAN_DISPATCH_V5_DESCRIPTOR_OBJECT_FIELD_COUNT 16u


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

typedef struct PdockerGpuVulkanDispatchV5ObjectHeaderExtension {
    uint32_t image_count;
    uint32_t image_entry_size;
    uint64_t image_table_offset;
    uint64_t image_table_size;
    uint64_t image_schema_hash;
    uint32_t image_view_count;
    uint32_t image_view_entry_size;
    uint64_t image_view_table_offset;
    uint64_t image_view_table_size;
    uint64_t image_view_schema_hash;
    uint32_t sampler_count;
    uint32_t sampler_entry_size;
    uint64_t sampler_table_offset;
    uint64_t sampler_table_size;
    uint64_t sampler_schema_hash;
    uint64_t object_hash;
} PdockerGpuVulkanDispatchV5ObjectHeaderExtension;

typedef struct PdockerGpuVulkanDispatchV5ObjectFrameHeader {
    PdockerGpuVulkanDispatchV5FrameHeader base;
    PdockerGpuVulkanDispatchV5ObjectHeaderExtension objects;
} PdockerGpuVulkanDispatchV5ObjectFrameHeader;

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

typedef struct PdockerGpuVulkanDispatchV5ImageEntry {
    uint32_t flags;
    uint32_t image_type;
    uint64_t image_id;
    uint32_t memory_resource_index;
    uint32_t reserved0;
    uint64_t memory_offset;
    uint64_t memory_size;
    uint32_t format;
    uint32_t extent_width;
    uint32_t extent_height;
    uint32_t extent_depth;
    uint32_t mip_levels;
    uint32_t array_layers;
    uint32_t samples;
    uint32_t tiling;
    uint64_t usage;
    uint64_t create_flags;
    uint32_t sharing_mode;
    uint32_t initial_layout;
    uint64_t generation;
} PdockerGpuVulkanDispatchV5ImageEntry;

typedef struct PdockerGpuVulkanDispatchV5ImageViewEntry {
    uint32_t flags;
    uint32_t view_type;
    uint64_t view_id;
    uint32_t image_index;
    uint32_t format;
    uint32_t component_r;
    uint32_t component_g;
    uint32_t component_b;
    uint32_t component_a;
    uint32_t aspect_mask;
    uint32_t base_mip_level;
    uint32_t level_count;
    uint32_t base_array_layer;
    uint32_t layer_count;
    uint64_t generation;
} PdockerGpuVulkanDispatchV5ImageViewEntry;

typedef struct PdockerGpuVulkanDispatchV5SamplerEntry {
    uint32_t flags;
    uint32_t reserved0;
    uint64_t sampler_id;
    uint32_t mag_filter;
    uint32_t min_filter;
    uint32_t mipmap_mode;
    uint32_t address_mode_u;
    uint32_t address_mode_v;
    uint32_t address_mode_w;
    uint32_t mip_lod_bias_bits;
    uint32_t anisotropy_enable;
    uint32_t max_anisotropy_bits;
    uint32_t compare_enable;
    uint32_t compare_op;
    uint32_t min_lod_bits;
    uint32_t max_lod_bits;
    uint32_t border_color;
    uint32_t unnormalized_coordinates;
    uint64_t generation;
} PdockerGpuVulkanDispatchV5SamplerEntry;

typedef struct PdockerGpuVulkanDispatchV5DescriptorObjectEntry {
    uint32_t descriptor_set;
    uint32_t binding;
    uint32_t array_element;
    uint32_t descriptor_type;
    uint32_t descriptor_flags;
    uint32_t access_flags;
    uint32_t resource_index;
    uint32_t image_view_index;
    uint32_t sampler_index;
    uint32_t image_layout;
    uint64_t resource_id;
    uint64_t buffer_offset;
    uint64_t range;
    uint64_t transfer_offset;
    uint64_t transfer_size;
    uint64_t dynamic_offset;
} PdockerGpuVulkanDispatchV5DescriptorObjectEntry;

typedef struct PdockerGpuVulkanDispatchV5SpecializationEntry {
    uint32_t constant_id;
    uint32_t offset;
    uint64_t size;
} PdockerGpuVulkanDispatchV5SpecializationEntry;

/*
 * V6 graphics submit ABI foundation.
 *
 * V5/V5.1 remains the compute-dispatch ABI.  V6.0 is the base ordered
 * graphics command stream.  V6.1 is an append-only header extension for
 * dynamic offsets, push metadata, and explicit barrier tables.  V6.2 is an
 * append-only header extension for graphics shader specialization map-entry
 * metadata.  V6.3 is an append-only header extension for serialized static
 * graphics depth/stencil pipeline state.  V6.4 is an append-only header
 * extension for dynamic-rendering resolve attachment metadata.  V6.5 through
 * V6.17 continue this append-only rule for static pipeline state, blend,
 * viewport/scissor, indirect draw, copy/fill/update, clear-color,
 * clear-depth-stencil, image resolve/blit, and attachment clear metadata.
 * Existing structs and schema hashes must not be changed retroactively.
 */
#define PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAGIC "PDGPUG6"
#define PDOCKER_GPU_VULKAN_GRAPHICS_V6_ABI_MAJOR 6u
#define PDOCKER_GPU_VULKAN_GRAPHICS_V6_ABI_MINOR 0u
#define PDOCKER_GPU_VULKAN_GRAPHICS_V61_ABI_MINOR 1u
#define PDOCKER_GPU_VULKAN_GRAPHICS_V62_ABI_MINOR 2u
#define PDOCKER_GPU_VULKAN_GRAPHICS_V63_ABI_MINOR 3u
#define PDOCKER_GPU_VULKAN_GRAPHICS_V64_ABI_MINOR 4u
#define PDOCKER_GPU_VULKAN_GRAPHICS_V65_ABI_MINOR 5u
#define PDOCKER_GPU_VULKAN_GRAPHICS_V66_ABI_MINOR 6u
#define PDOCKER_GPU_VULKAN_GRAPHICS_V67_ABI_MINOR 7u
#define PDOCKER_GPU_VULKAN_GRAPHICS_V68_ABI_MINOR 8u
#define PDOCKER_GPU_VULKAN_GRAPHICS_V69_ABI_MINOR 9u
#define PDOCKER_GPU_VULKAN_GRAPHICS_V610_ABI_MINOR 10u
#define PDOCKER_GPU_VULKAN_GRAPHICS_V611_ABI_MINOR 11u
#define PDOCKER_GPU_VULKAN_GRAPHICS_V612_ABI_MINOR 12u
#define PDOCKER_GPU_VULKAN_GRAPHICS_V613_ABI_MINOR 13u
#define PDOCKER_GPU_VULKAN_GRAPHICS_V614_ABI_MINOR 14u
#define PDOCKER_GPU_VULKAN_GRAPHICS_V615_ABI_MINOR 15u
#define PDOCKER_GPU_VULKAN_GRAPHICS_V616_ABI_MINOR 16u
#define PDOCKER_GPU_VULKAN_GRAPHICS_V617_ABI_MINOR 17u
#define PDOCKER_GPU_VULKAN_GRAPHICS_V6_COMMAND_SUBMIT 1u
#define PDOCKER_GPU_VULKAN_GRAPHICS_V6_FRAME_HEADER_SCHEMA_HASH 0x8787f343f2f4f255ull
#define PDOCKER_GPU_VULKAN_GRAPHICS_V6_SHADER_STAGE_SCHEMA_HASH 0xc9b21285e5a281b8ull
#define PDOCKER_GPU_VULKAN_GRAPHICS_V6_PIPELINE_SCHEMA_HASH 0x37218816fe25c7ddull
#define PDOCKER_GPU_VULKAN_GRAPHICS_V6_VERTEX_BINDING_SCHEMA_HASH 0x7a735aaa6fdc4e5aull
#define PDOCKER_GPU_VULKAN_GRAPHICS_V6_VERTEX_ATTRIBUTE_SCHEMA_HASH 0x0a82d873c2a230c5ull
#define PDOCKER_GPU_VULKAN_GRAPHICS_V6_ATTACHMENT_SCHEMA_HASH 0x29ca5fee670cb0e0ull
#define PDOCKER_GPU_VULKAN_GRAPHICS_V6_DYNAMIC_STATE_SCHEMA_HASH 0x0305d9e579f44e90ull
#define PDOCKER_GPU_VULKAN_GRAPHICS_V6_COMMAND_SCHEMA_HASH 0x3e932210bbed0c3cull
#define PDOCKER_GPU_VULKAN_GRAPHICS_V61_HEADER_EXTENSION_SCHEMA_HASH 0xe8ec901a6f1d6a79ull
#define PDOCKER_GPU_VULKAN_GRAPHICS_V61_DYNAMIC_OFFSET_SCHEMA_HASH 0x4fed60f52743cc94ull
#define PDOCKER_GPU_VULKAN_GRAPHICS_V61_PUSH_CONSTANT_METADATA_SCHEMA_HASH 0xfec2e2aff5874940ull
#define PDOCKER_GPU_VULKAN_GRAPHICS_V61_IMAGE_BARRIER_SCHEMA_HASH 0xfab42820638bfb19ull
#define PDOCKER_GPU_VULKAN_GRAPHICS_V61_MEMORY_BARRIER_SCHEMA_HASH 0x80fba87057d8753dull
#define PDOCKER_GPU_VULKAN_GRAPHICS_V61_BUFFER_BARRIER_SCHEMA_HASH 0xec42d150dc692354ull
#define PDOCKER_GPU_VULKAN_GRAPHICS_V62_HEADER_EXTENSION_SCHEMA_HASH 0xe10f3b27ce857893ull
#define PDOCKER_GPU_VULKAN_GRAPHICS_V62_SPECIALIZATION_ENTRY_SCHEMA_HASH 0x0eaa6b0ee1be40c1ull
#define PDOCKER_GPU_VULKAN_GRAPHICS_V63_HEADER_EXTENSION_SCHEMA_HASH 0x5c9c629728c861f5ull
#define PDOCKER_GPU_VULKAN_GRAPHICS_V63_DEPTH_STENCIL_STATE_SCHEMA_HASH 0x4da182d1f0ea5a83ull
#define PDOCKER_GPU_VULKAN_GRAPHICS_V64_HEADER_EXTENSION_SCHEMA_HASH 0xb8bc09c35442b3a8ull
#define PDOCKER_GPU_VULKAN_GRAPHICS_V64_RESOLVE_ATTACHMENT_SCHEMA_HASH 0xf601060db3fe6d70ull
#define PDOCKER_GPU_VULKAN_GRAPHICS_V65_HEADER_EXTENSION_SCHEMA_HASH 0x6ab3135cb8051e8eull
#define PDOCKER_GPU_VULKAN_GRAPHICS_V65_STATIC_PIPELINE_STATE_SCHEMA_HASH 0xf2d422fe89c57221ull
#define PDOCKER_GPU_VULKAN_GRAPHICS_V66_HEADER_EXTENSION_SCHEMA_HASH 0x5765106119509108ull
#define PDOCKER_GPU_VULKAN_GRAPHICS_V66_COLOR_BLEND_STATE_SCHEMA_HASH 0xa2b61fe8cccf0ea6ull
#define PDOCKER_GPU_VULKAN_GRAPHICS_V66_COLOR_BLEND_ATTACHMENT_SCHEMA_HASH 0x763f1cd2b92a7710ull
#define PDOCKER_GPU_VULKAN_GRAPHICS_V67_HEADER_EXTENSION_SCHEMA_HASH 0xf4fc5c01f74f87f0ull
#define PDOCKER_GPU_VULKAN_GRAPHICS_V67_VIEWPORT_SCISSOR_STATE_SCHEMA_HASH 0xa76b96bdcf2c00eaull
#define PDOCKER_GPU_VULKAN_GRAPHICS_V67_VIEWPORT_SCHEMA_HASH 0x1b32b902609358a7ull
#define PDOCKER_GPU_VULKAN_GRAPHICS_V67_SCISSOR_SCHEMA_HASH 0x57b0da55f6a9871aull
#define PDOCKER_GPU_VULKAN_GRAPHICS_V68_HEADER_EXTENSION_SCHEMA_HASH 0x7d182f8a521ce006ull
#define PDOCKER_GPU_VULKAN_GRAPHICS_V68_INDIRECT_DRAW_SCHEMA_HASH 0x0c0f27c9d746a371ull
#define PDOCKER_GPU_VULKAN_GRAPHICS_V69_HEADER_EXTENSION_SCHEMA_HASH 0x94fe0eb9c0ee7c3bull
#define PDOCKER_GPU_VULKAN_GRAPHICS_V69_BUFFER_COPY_SCHEMA_HASH 0x7fb0d4996f0e6919ull
#define PDOCKER_GPU_VULKAN_GRAPHICS_V610_HEADER_EXTENSION_SCHEMA_HASH 0x5b35f76f76f18bf9ull
#define PDOCKER_GPU_VULKAN_GRAPHICS_V610_BUFFER_IMAGE_COPY_SCHEMA_HASH 0x2d3b53d05c5cb70dull
#define PDOCKER_GPU_VULKAN_GRAPHICS_V610_IMAGE_COPY_SCHEMA_HASH 0x3a3de925dfb62446ull
#define PDOCKER_GPU_VULKAN_GRAPHICS_V611_HEADER_EXTENSION_SCHEMA_HASH 0x93cb79f20953cc17ull
#define PDOCKER_GPU_VULKAN_GRAPHICS_V611_FILL_BUFFER_SCHEMA_HASH 0xc1f23a447ec9ea61ull
#define PDOCKER_GPU_VULKAN_GRAPHICS_V611_UPDATE_BUFFER_SCHEMA_HASH 0x53e9386aec8278afull
#define PDOCKER_GPU_VULKAN_GRAPHICS_V612_HEADER_EXTENSION_SCHEMA_HASH 0x482efa60b13164b1ull
#define PDOCKER_GPU_VULKAN_GRAPHICS_V612_CLEAR_COLOR_IMAGE_SCHEMA_HASH 0x4264b293962f7a49ull
#define PDOCKER_GPU_VULKAN_GRAPHICS_V613_HEADER_EXTENSION_SCHEMA_HASH 0x2c4d99be0f6a13d7ull
#define PDOCKER_GPU_VULKAN_GRAPHICS_V613_CLEAR_DEPTH_STENCIL_IMAGE_SCHEMA_HASH 0x849d3b264f940cb5ull
#define PDOCKER_GPU_VULKAN_GRAPHICS_V614_HEADER_EXTENSION_SCHEMA_HASH 0x4e8f7c1d2b9a6035ull
#define PDOCKER_GPU_VULKAN_GRAPHICS_V614_RESOLVE_IMAGE_SCHEMA_HASH 0x934b7c2de01fa856ull
#define PDOCKER_GPU_VULKAN_GRAPHICS_V615_HEADER_EXTENSION_SCHEMA_HASH 0x68b3d4a9c271e50full
#define PDOCKER_GPU_VULKAN_GRAPHICS_V615_BLIT_IMAGE_SCHEMA_HASH 0xb5f09d34a6e7182cull
#define PDOCKER_GPU_VULKAN_GRAPHICS_V616_HEADER_EXTENSION_SCHEMA_HASH 0xb77e3182d2adf401ull
#define PDOCKER_GPU_VULKAN_GRAPHICS_V616_CLEAR_ATTACHMENTS_COMMAND_SCHEMA_HASH 0x34f0a54df6dfbb3bull
#define PDOCKER_GPU_VULKAN_GRAPHICS_V616_CLEAR_ATTACHMENT_SCHEMA_HASH 0x476ab6f70c8d8c29ull
#define PDOCKER_GPU_VULKAN_GRAPHICS_V616_CLEAR_RECT_SCHEMA_HASH 0x4dc34ebad138ca87ull
#define PDOCKER_GPU_VULKAN_GRAPHICS_V617_HEADER_EXTENSION_SCHEMA_HASH 0x8d3f70ec4b7a9c21ull
#define PDOCKER_GPU_VULKAN_GRAPHICS_V617_QUERY_COMMAND_SCHEMA_HASH 0xe3769b15473cbe2dull
#define PDOCKER_GPU_VULKAN_GRAPHICS_V617_QUERY_RESULT_SCHEMA_HASH 0x58e90c4d9a3f672bull
#define PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_FRAME_BYTES (8u * 1024u * 1024u)
#define PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_SHADER_STAGES 16u
#define PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_PIPELINES 64u
#define PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_VERTEX_BINDINGS 64u
#define PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_VERTEX_ATTRIBUTES 128u
#define PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_ATTACHMENTS 64u
#define PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_DYNAMIC_STATES 256u
#define PDOCKER_GPU_VULKAN_GRAPHICS_V61_MAX_DYNAMIC_OFFSETS 4096u
#define PDOCKER_GPU_VULKAN_GRAPHICS_V61_MAX_PUSH_CONSTANT_METADATA PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_COMMANDS
#define PDOCKER_GPU_VULKAN_GRAPHICS_V61_MAX_IMAGE_BARRIERS 4096u
#define PDOCKER_GPU_VULKAN_GRAPHICS_V61_MAX_MEMORY_BARRIERS 4096u
#define PDOCKER_GPU_VULKAN_GRAPHICS_V61_MAX_BUFFER_BARRIERS 4096u
#define PDOCKER_GPU_VULKAN_GRAPHICS_V62_MAX_SPECIALIZATION_ENTRIES 1024u
#define PDOCKER_GPU_VULKAN_GRAPHICS_V63_MAX_DEPTH_STENCIL_STATES PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_PIPELINES
#define PDOCKER_GPU_VULKAN_GRAPHICS_V64_MAX_RESOLVE_ATTACHMENTS PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_ATTACHMENTS
#define PDOCKER_GPU_VULKAN_GRAPHICS_V65_MAX_STATIC_PIPELINE_STATES PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_PIPELINES
#define PDOCKER_GPU_VULKAN_GRAPHICS_V66_MAX_COLOR_BLEND_STATES PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_PIPELINES
#define PDOCKER_GPU_VULKAN_GRAPHICS_V66_MAX_COLOR_BLEND_ATTACHMENTS PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_ATTACHMENTS
#define PDOCKER_GPU_VULKAN_GRAPHICS_V67_MAX_VIEWPORT_SCISSOR_STATES PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_PIPELINES
#define PDOCKER_GPU_VULKAN_GRAPHICS_V67_MAX_VIEWPORTS 1024u
#define PDOCKER_GPU_VULKAN_GRAPHICS_V67_MAX_SCISSORS 1024u
#define PDOCKER_GPU_VULKAN_GRAPHICS_V67_MAX_VIEWPORTS_PER_PIPELINE 16u
#define PDOCKER_GPU_VULKAN_GRAPHICS_V67_MAX_SCISSORS_PER_PIPELINE 16u
#define PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_COMMANDS 4096u
#define PDOCKER_GPU_VULKAN_GRAPHICS_V68_MAX_INDIRECT_DRAWS PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_COMMANDS
#define PDOCKER_GPU_VULKAN_GRAPHICS_V69_MAX_BUFFER_COPIES PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_COMMANDS
#define PDOCKER_GPU_VULKAN_GRAPHICS_V610_MAX_BUFFER_IMAGE_COPIES PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_COMMANDS
#define PDOCKER_GPU_VULKAN_GRAPHICS_V610_MAX_IMAGE_COPIES PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_COMMANDS
#define PDOCKER_GPU_VULKAN_GRAPHICS_V611_MAX_FILL_BUFFERS PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_COMMANDS
#define PDOCKER_GPU_VULKAN_GRAPHICS_V611_MAX_UPDATE_BUFFERS PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_COMMANDS
#define PDOCKER_GPU_VULKAN_GRAPHICS_V611_MAX_UPDATE_BUFFER_BYTES 65536u
#define PDOCKER_GPU_VULKAN_GRAPHICS_V612_MAX_CLEAR_COLOR_IMAGES PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_COMMANDS
#define PDOCKER_GPU_VULKAN_GRAPHICS_V613_MAX_CLEAR_DEPTH_STENCIL_IMAGES PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_COMMANDS
#define PDOCKER_GPU_VULKAN_GRAPHICS_V614_MAX_RESOLVE_IMAGES PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_COMMANDS
#define PDOCKER_GPU_VULKAN_GRAPHICS_V615_MAX_BLIT_IMAGES PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_COMMANDS
#define PDOCKER_GPU_VULKAN_GRAPHICS_V616_MAX_CLEAR_ATTACHMENTS_COMMANDS PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_COMMANDS
#define PDOCKER_GPU_VULKAN_GRAPHICS_V616_MAX_CLEAR_ATTACHMENTS (PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_COMMANDS * 4u)
#define PDOCKER_GPU_VULKAN_GRAPHICS_V616_MAX_CLEAR_RECTS (PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_COMMANDS * 4u)
#define PDOCKER_GPU_VULKAN_GRAPHICS_V617_MAX_QUERY_COMMANDS PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_COMMANDS


#define PDOCKER_GPU_GRAPHICS_V63_DEPTH_STENCIL_DEPTH_TEST_ENABLE 0x00000001u
#define PDOCKER_GPU_GRAPHICS_V63_DEPTH_STENCIL_DEPTH_WRITE_ENABLE 0x00000002u
#define PDOCKER_GPU_GRAPHICS_V63_DEPTH_STENCIL_DEPTH_BOUNDS_TEST_ENABLE 0x00000004u
#define PDOCKER_GPU_GRAPHICS_V63_DEPTH_STENCIL_STENCIL_TEST_ENABLE 0x00000008u

#define PDOCKER_GPU_VULKAN_GRAPHICS_V63_DEPTH_STENCIL_STATE_FIELDS(X) \
    X(pipeline_index, u32) \
    X(flags, u32) \
    X(depth_compare_op, u32) \
    X(front_fail_op, u32) \
    X(front_pass_op, u32) \
    X(front_depth_fail_op, u32) \
    X(front_compare_op, u32) \
    X(front_compare_mask, u32) \
    X(front_write_mask, u32) \
    X(front_reference, u32) \
    X(back_fail_op, u32) \
    X(back_pass_op, u32) \
    X(back_depth_fail_op, u32) \
    X(back_compare_op, u32) \
    X(back_compare_mask, u32) \
    X(back_write_mask, u32) \
    X(back_reference, u32) \
    X(min_depth_bounds_bits, u32) \
    X(max_depth_bounds_bits, u32) \
    X(reserved0, u32)
#define PDOCKER_GPU_VULKAN_GRAPHICS_V63_DEPTH_STENCIL_STATE_FIELD_COUNT 20u

#define PDOCKER_GPU_VULKAN_GRAPHICS_V64_RESOLVE_ATTACHMENT_FIELDS(X) \
    X(attachment_index, u32) \
    X(resolve_mode, u32) \
    X(resolve_layout, u32) \
    X(reserved0, u32)
#define PDOCKER_GPU_VULKAN_GRAPHICS_V64_RESOLVE_ATTACHMENT_FIELD_COUNT 4u

#define PDOCKER_GPU_GRAPHICS_V65_STATIC_PRIMITIVE_RESTART_ENABLE 0x00000001u
#define PDOCKER_GPU_GRAPHICS_V65_STATIC_DEPTH_CLAMP_ENABLE 0x00000002u
#define PDOCKER_GPU_GRAPHICS_V65_STATIC_RASTERIZER_DISCARD_ENABLE 0x00000004u
#define PDOCKER_GPU_GRAPHICS_V65_STATIC_DEPTH_BIAS_ENABLE 0x00000008u
#define PDOCKER_GPU_GRAPHICS_V65_STATIC_LINE_WIDTH_PRESENT 0x00000010u

#define PDOCKER_GPU_VULKAN_GRAPHICS_V65_STATIC_PIPELINE_STATE_FIELDS(X) \
    X(pipeline_index, u32) \
    X(flags, u32) \
    X(depth_bias_constant_factor_bits, u32) \
    X(depth_bias_clamp_bits, u32) \
    X(depth_bias_slope_factor_bits, u32) \
    X(line_width_bits, u32) \
    X(reserved0, u32) \
    X(reserved1, u32)
#define PDOCKER_GPU_VULKAN_GRAPHICS_V65_STATIC_PIPELINE_STATE_FIELD_COUNT 8u

#define PDOCKER_GPU_GRAPHICS_V66_COLOR_BLEND_LOGIC_OP_ENABLE 0x00000001u
#define PDOCKER_GPU_GRAPHICS_V66_COLOR_BLEND_CONSTANTS_PRESENT 0x00000002u
#define PDOCKER_GPU_GRAPHICS_V66_COLOR_BLEND_ATTACHMENT_BLEND_ENABLE 0x00000001u

#define PDOCKER_GPU_GRAPHICS_V67_VIEWPORT_STATIC_PRESENT 0x00000001u
#define PDOCKER_GPU_GRAPHICS_V67_SCISSOR_STATIC_PRESENT 0x00000002u
#define PDOCKER_GPU_GRAPHICS_V67_INDEX_NONE 0xffffffffu
#define PDOCKER_GPU_GRAPHICS_V68_INDEX_NONE 0xffffffffu
#define PDOCKER_GPU_GRAPHICS_V68_INDIRECT_DRAW_COUNT_BUFFER_PRESENT 0x00000001u

#define PDOCKER_GPU_VULKAN_GRAPHICS_V66_COLOR_BLEND_STATE_FIELDS(X) \
    X(pipeline_index, u32) \
    X(flags, u32) \
    X(logic_op, u32) \
    X(attachment_first, u32) \
    X(attachment_count, u32) \
    X(blend_constant0_bits, u32) \
    X(blend_constant1_bits, u32) \
    X(blend_constant2_bits, u32) \
    X(blend_constant3_bits, u32) \
    X(reserved0, u32)
#define PDOCKER_GPU_VULKAN_GRAPHICS_V66_COLOR_BLEND_STATE_FIELD_COUNT 10u

#define PDOCKER_GPU_VULKAN_GRAPHICS_V66_COLOR_BLEND_ATTACHMENT_FIELDS(X) \
    X(pipeline_index, u32) \
    X(attachment_index, u32) \
    X(flags, u32) \
    X(src_color_blend_factor, u32) \
    X(dst_color_blend_factor, u32) \
    X(color_blend_op, u32) \
    X(src_alpha_blend_factor, u32) \
    X(dst_alpha_blend_factor, u32) \
    X(alpha_blend_op, u32) \
    X(color_write_mask, u32)
#define PDOCKER_GPU_VULKAN_GRAPHICS_V66_COLOR_BLEND_ATTACHMENT_FIELD_COUNT 10u

#define PDOCKER_GPU_VULKAN_GRAPHICS_V67_VIEWPORT_SCISSOR_STATE_FIELDS(X) \
    X(pipeline_index, u32) \
    X(flags, u32) \
    X(viewport_static_first, u32) \
    X(viewport_count, u32) \
    X(scissor_static_first, u32) \
    X(scissor_count, u32) \
    X(reserved0, u32) \
    X(reserved1, u32)
#define PDOCKER_GPU_VULKAN_GRAPHICS_V67_VIEWPORT_SCISSOR_STATE_FIELD_COUNT 8u

#define PDOCKER_GPU_VULKAN_GRAPHICS_V67_VIEWPORT_FIELDS(X) \
    X(pipeline_index, u32) \
    X(viewport_index, u32) \
    X(x_bits, u32) \
    X(y_bits, u32) \
    X(width_bits, u32) \
    X(height_bits, u32) \
    X(min_depth_bits, u32) \
    X(max_depth_bits, u32)
#define PDOCKER_GPU_VULKAN_GRAPHICS_V67_VIEWPORT_FIELD_COUNT 8u

#define PDOCKER_GPU_VULKAN_GRAPHICS_V67_SCISSOR_FIELDS(X) \
    X(pipeline_index, u32) \
    X(scissor_index, u32) \
    X(offset_x, i32) \
    X(offset_y, i32) \
    X(extent_width, u32) \
    X(extent_height, u32) \
    X(reserved0, u32) \
    X(reserved1, u32)
#define PDOCKER_GPU_VULKAN_GRAPHICS_V67_SCISSOR_FIELD_COUNT 8u

#define PDOCKER_GPU_VULKAN_GRAPHICS_V68_HEADER_EXTENSION_FIELDS(X) \
    X(indirect_draw_count, u32) \
    X(indirect_draw_entry_size, u32) \
    X(indirect_draw_table_offset, u64) \
    X(indirect_draw_table_size, u64) \
    X(indirect_draw_schema_hash, u64) \
    X(indirect_draw_table_hash, u64) \
    X(extension_hash, u64)
#define PDOCKER_GPU_VULKAN_GRAPHICS_V68_HEADER_EXTENSION_FIELD_COUNT 7u

#define PDOCKER_GPU_VULKAN_GRAPHICS_V68_INDIRECT_DRAW_FIELDS(X) \
    X(command_index, u32) \
    X(flags, u32) \
    X(indirect_resource_index, u32) \
    X(count_resource_index, u32) \
    X(indirect_offset, u64) \
    X(count_offset, u64) \
    X(draw_count, u32) \
    X(stride, u32)
#define PDOCKER_GPU_VULKAN_GRAPHICS_V68_INDIRECT_DRAW_FIELD_COUNT 8u

#define PDOCKER_GPU_VULKAN_GRAPHICS_V69_HEADER_EXTENSION_FIELDS(X) \
    X(buffer_copy_count, u32) \
    X(buffer_copy_entry_size, u32) \
    X(buffer_copy_table_offset, u64) \
    X(buffer_copy_table_size, u64) \
    X(buffer_copy_schema_hash, u64) \
    X(buffer_copy_table_hash, u64) \
    X(extension_hash, u64)
#define PDOCKER_GPU_VULKAN_GRAPHICS_V69_HEADER_EXTENSION_FIELD_COUNT 7u

#define PDOCKER_GPU_VULKAN_GRAPHICS_V69_BUFFER_COPY_FIELDS(X) \
    X(command_index, u32) \
    X(src_resource_index, u32) \
    X(dst_resource_index, u32) \
    X(reserved0, u32) \
    X(src_offset, u64) \
    X(dst_offset, u64) \
    X(size, u64)
#define PDOCKER_GPU_VULKAN_GRAPHICS_V69_BUFFER_COPY_FIELD_COUNT 7u

#define PDOCKER_GPU_VULKAN_GRAPHICS_V610_HEADER_EXTENSION_FIELDS(X) \
    X(buffer_image_copy_count, u32) \
    X(buffer_image_copy_entry_size, u32) \
    X(buffer_image_copy_table_offset, u64) \
    X(buffer_image_copy_table_size, u64) \
    X(buffer_image_copy_schema_hash, u64) \
    X(buffer_image_copy_table_hash, u64) \
    X(image_copy_count, u32) \
    X(image_copy_entry_size, u32) \
    X(image_copy_table_offset, u64) \
    X(image_copy_table_size, u64) \
    X(image_copy_schema_hash, u64) \
    X(image_copy_table_hash, u64) \
    X(extension_hash, u64)
#define PDOCKER_GPU_VULKAN_GRAPHICS_V610_HEADER_EXTENSION_FIELD_COUNT 13u

#define PDOCKER_GPU_VULKAN_GRAPHICS_V610_BUFFER_IMAGE_COPY_FIELDS(X) \
    X(command_index, u32) \
    X(direction, u32) \
    X(buffer_resource_index, u32) \
    X(image_index, u32) \
    X(image_layout, u32) \
    X(aspect_mask, u32) \
    X(mip_level, u32) \
    X(base_array_layer, u32) \
    X(layer_count, u32) \
    X(buffer_row_length, u32) \
    X(buffer_image_height, u32) \
    X(image_offset_x, i32) \
    X(image_offset_y, i32) \
    X(image_offset_z, i32) \
    X(image_extent_width, u32) \
    X(image_extent_height, u32) \
    X(image_extent_depth, u32) \
    X(reserved0, u32) \
    X(buffer_offset, u64)
#define PDOCKER_GPU_VULKAN_GRAPHICS_V610_BUFFER_IMAGE_COPY_FIELD_COUNT 19u

#define PDOCKER_GPU_VULKAN_GRAPHICS_V610_IMAGE_COPY_FIELDS(X) \
    X(command_index, u32) \
    X(src_image_index, u32) \
    X(dst_image_index, u32) \
    X(src_layout, u32) \
    X(dst_layout, u32) \
    X(src_aspect_mask, u32) \
    X(src_mip_level, u32) \
    X(src_base_array_layer, u32) \
    X(dst_aspect_mask, u32) \
    X(dst_mip_level, u32) \
    X(dst_base_array_layer, u32) \
    X(layer_count, u32) \
    X(src_offset_x, i32) \
    X(src_offset_y, i32) \
    X(src_offset_z, i32) \
    X(dst_offset_x, i32) \
    X(dst_offset_y, i32) \
    X(dst_offset_z, i32) \
    X(extent_width, u32) \
    X(extent_height, u32) \
    X(extent_depth, u32) \
    X(reserved0, u32)
#define PDOCKER_GPU_VULKAN_GRAPHICS_V610_IMAGE_COPY_FIELD_COUNT 22u

#define PDOCKER_GPU_VULKAN_GRAPHICS_V611_HEADER_EXTENSION_FIELDS(X) \
    X(fill_buffer_count, u32) \
    X(fill_buffer_entry_size, u32) \
    X(fill_buffer_table_offset, u64) \
    X(fill_buffer_table_size, u64) \
    X(fill_buffer_schema_hash, u64) \
    X(fill_buffer_table_hash, u64) \
    X(update_buffer_count, u32) \
    X(update_buffer_entry_size, u32) \
    X(update_buffer_table_offset, u64) \
    X(update_buffer_table_size, u64) \
    X(update_buffer_schema_hash, u64) \
    X(update_buffer_table_hash, u64) \
    X(update_payload_data_offset, u64) \
    X(update_payload_data_size, u64) \
    X(update_payload_data_hash, u64) \
    X(extension_hash, u64)
#define PDOCKER_GPU_VULKAN_GRAPHICS_V611_HEADER_EXTENSION_FIELD_COUNT 16u

#define PDOCKER_GPU_VULKAN_GRAPHICS_V611_FILL_BUFFER_FIELDS(X) \
    X(command_index, u32) \
    X(dst_resource_index, u32) \
    X(reserved0, u32) \
    X(reserved1, u32) \
    X(dst_offset, u64) \
    X(size, u64) \
    X(data, u32) \
    X(reserved2, u32)
#define PDOCKER_GPU_VULKAN_GRAPHICS_V611_FILL_BUFFER_FIELD_COUNT 8u

#define PDOCKER_GPU_VULKAN_GRAPHICS_V611_UPDATE_BUFFER_FIELDS(X) \
    X(command_index, u32) \
    X(dst_resource_index, u32) \
    X(reserved0, u32) \
    X(reserved1, u32) \
    X(dst_offset, u64) \
    X(data_size, u64) \
    X(payload_offset, u64) \
    X(payload_hash, u64)
#define PDOCKER_GPU_VULKAN_GRAPHICS_V611_UPDATE_BUFFER_FIELD_COUNT 8u

#define PDOCKER_GPU_VULKAN_GRAPHICS_V612_HEADER_EXTENSION_FIELDS(X) \
    X(clear_color_image_count, u32) \
    X(clear_color_image_entry_size, u32) \
    X(clear_color_image_table_offset, u64) \
    X(clear_color_image_table_size, u64) \
    X(clear_color_image_schema_hash, u64) \
    X(clear_color_image_table_hash, u64) \
    X(extension_hash, u64)
#define PDOCKER_GPU_VULKAN_GRAPHICS_V612_HEADER_EXTENSION_FIELD_COUNT 7u

#define PDOCKER_GPU_VULKAN_GRAPHICS_V612_CLEAR_COLOR_IMAGE_FIELDS(X) \
    X(command_index, u32) \
    X(image_index, u32) \
    X(image_layout, u32) \
    X(aspect_mask, u32) \
    X(base_mip_level, u32) \
    X(level_count, u32) \
    X(base_array_layer, u32) \
    X(layer_count, u32) \
    X(color_uint32_0, u32) \
    X(color_uint32_1, u32) \
    X(color_uint32_2, u32) \
    X(color_uint32_3, u32) \
    X(reserved0, u32) \
    X(reserved1, u32) \
    X(reserved2, u32) \
    X(reserved3, u32)
#define PDOCKER_GPU_VULKAN_GRAPHICS_V612_CLEAR_COLOR_IMAGE_FIELD_COUNT 16u

#define PDOCKER_GPU_VULKAN_GRAPHICS_V613_HEADER_EXTENSION_FIELDS(X) \
    X(clear_depth_stencil_image_count, u32) \
    X(clear_depth_stencil_image_entry_size, u32) \
    X(clear_depth_stencil_image_table_offset, u64) \
    X(clear_depth_stencil_image_table_size, u64) \
    X(clear_depth_stencil_image_schema_hash, u64) \
    X(clear_depth_stencil_image_table_hash, u64) \
    X(extension_hash, u64)
#define PDOCKER_GPU_VULKAN_GRAPHICS_V613_HEADER_EXTENSION_FIELD_COUNT 7u

#define PDOCKER_GPU_VULKAN_GRAPHICS_V613_CLEAR_DEPTH_STENCIL_IMAGE_FIELDS(X) \
    X(command_index, u32) \
    X(image_index, u32) \
    X(image_layout, u32) \
    X(aspect_mask, u32) \
    X(base_mip_level, u32) \
    X(level_count, u32) \
    X(base_array_layer, u32) \
    X(layer_count, u32) \
    X(depth_bits, u32) \
    X(stencil, u32) \
    X(reserved0, u32) \
    X(reserved1, u32) \
    X(reserved2, u32) \
    X(reserved3, u32) \
    X(reserved4, u32) \
    X(reserved5, u32)
#define PDOCKER_GPU_VULKAN_GRAPHICS_V613_CLEAR_DEPTH_STENCIL_IMAGE_FIELD_COUNT 16u

#define PDOCKER_GPU_VULKAN_GRAPHICS_V614_HEADER_EXTENSION_FIELDS(X) \
    X(resolve_image_count, u32) \
    X(resolve_image_entry_size, u32) \
    X(resolve_image_table_offset, u64) \
    X(resolve_image_table_size, u64) \
    X(resolve_image_schema_hash, u64) \
    X(resolve_image_table_hash, u64) \
    X(extension_hash, u64)
#define PDOCKER_GPU_VULKAN_GRAPHICS_V614_HEADER_EXTENSION_FIELD_COUNT 7u

#define PDOCKER_GPU_VULKAN_GRAPHICS_V614_RESOLVE_IMAGE_FIELDS(X) \
    X(command_index, u32) \
    X(src_image_index, u32) \
    X(dst_image_index, u32) \
    X(src_layout, u32) \
    X(dst_layout, u32) \
    X(src_aspect_mask, u32) \
    X(src_mip_level, u32) \
    X(src_base_array_layer, u32) \
    X(dst_aspect_mask, u32) \
    X(dst_mip_level, u32) \
    X(dst_base_array_layer, u32) \
    X(layer_count, u32) \
    X(src_offset_x, i32) \
    X(src_offset_y, i32) \
    X(src_offset_z, i32) \
    X(dst_offset_x, i32) \
    X(dst_offset_y, i32) \
    X(dst_offset_z, i32) \
    X(extent_width, u32) \
    X(extent_height, u32) \
    X(extent_depth, u32) \
    X(reserved0, u32)
#define PDOCKER_GPU_VULKAN_GRAPHICS_V614_RESOLVE_IMAGE_FIELD_COUNT 22u

#define PDOCKER_GPU_VULKAN_GRAPHICS_V615_HEADER_EXTENSION_FIELDS(X) \
    X(blit_image_count, u32) \
    X(blit_image_entry_size, u32) \
    X(blit_image_table_offset, u64) \
    X(blit_image_table_size, u64) \
    X(blit_image_schema_hash, u64) \
    X(blit_image_table_hash, u64) \
    X(extension_hash, u64)
#define PDOCKER_GPU_VULKAN_GRAPHICS_V615_HEADER_EXTENSION_FIELD_COUNT 7u

#define PDOCKER_GPU_VULKAN_GRAPHICS_V615_BLIT_IMAGE_FIELDS(X) \
    X(command_index, u32) \
    X(src_image_index, u32) \
    X(dst_image_index, u32) \
    X(src_layout, u32) \
    X(dst_layout, u32) \
    X(src_aspect_mask, u32) \
    X(src_mip_level, u32) \
    X(src_base_array_layer, u32) \
    X(dst_aspect_mask, u32) \
    X(dst_mip_level, u32) \
    X(dst_base_array_layer, u32) \
    X(layer_count, u32) \
    X(src_offset0_x, i32) \
    X(src_offset0_y, i32) \
    X(src_offset0_z, i32) \
    X(src_offset1_x, i32) \
    X(src_offset1_y, i32) \
    X(src_offset1_z, i32) \
    X(dst_offset0_x, i32) \
    X(dst_offset0_y, i32) \
    X(dst_offset0_z, i32) \
    X(dst_offset1_x, i32) \
    X(dst_offset1_y, i32) \
    X(dst_offset1_z, i32) \
    X(filter, u32) \
    X(reserved0, u32) \
    X(reserved1, u32) \
    X(reserved2, u32)
#define PDOCKER_GPU_VULKAN_GRAPHICS_V615_BLIT_IMAGE_FIELD_COUNT 28u

#define PDOCKER_GPU_VULKAN_GRAPHICS_V616_HEADER_EXTENSION_FIELDS(X) \
    X(clear_attachments_command_count, u32) \
    X(clear_attachments_command_entry_size, u32) \
    X(clear_attachments_command_table_offset, u64) \
    X(clear_attachments_command_table_size, u64) \
    X(clear_attachments_command_schema_hash, u64) \
    X(clear_attachments_command_table_hash, u64) \
    X(clear_attachment_count, u32) \
    X(clear_attachment_entry_size, u32) \
    X(clear_attachment_table_offset, u64) \
    X(clear_attachment_table_size, u64) \
    X(clear_attachment_schema_hash, u64) \
    X(clear_attachment_table_hash, u64) \
    X(clear_rect_count, u32) \
    X(clear_rect_entry_size, u32) \
    X(clear_rect_table_offset, u64) \
    X(clear_rect_table_size, u64) \
    X(clear_rect_schema_hash, u64) \
    X(clear_rect_table_hash, u64) \
    X(extension_hash, u64)
#define PDOCKER_GPU_VULKAN_GRAPHICS_V616_HEADER_EXTENSION_FIELD_COUNT 19u

#define PDOCKER_GPU_VULKAN_GRAPHICS_V616_CLEAR_ATTACHMENTS_COMMAND_FIELDS(X) \
    X(command_index, u32) \
    X(clear_attachment_first, u32) \
    X(clear_attachment_count, u32) \
    X(clear_rect_first, u32) \
    X(clear_rect_count, u32) \
    X(reserved0, u32) \
    X(reserved1, u32) \
    X(reserved2, u32)
#define PDOCKER_GPU_VULKAN_GRAPHICS_V616_CLEAR_ATTACHMENTS_COMMAND_FIELD_COUNT 8u

#define PDOCKER_GPU_VULKAN_GRAPHICS_V616_CLEAR_ATTACHMENT_FIELDS(X) \
    X(aspect_mask, u32) \
    X(color_attachment, u32) \
    X(color_uint32_0, u32) \
    X(color_uint32_1, u32) \
    X(color_uint32_2, u32) \
    X(color_uint32_3, u32) \
    X(depth_bits, u32) \
    X(stencil, u32) \
    X(reserved0, u32) \
    X(reserved1, u32) \
    X(reserved2, u32) \
    X(reserved3, u32)
#define PDOCKER_GPU_VULKAN_GRAPHICS_V616_CLEAR_ATTACHMENT_FIELD_COUNT 12u

#define PDOCKER_GPU_VULKAN_GRAPHICS_V616_CLEAR_RECT_FIELDS(X) \
    X(rect_offset_x, i32) \
    X(rect_offset_y, i32) \
    X(rect_extent_width, u32) \
    X(rect_extent_height, u32) \
    X(base_array_layer, u32) \
    X(layer_count, u32) \
    X(reserved0, u32) \
    X(reserved1, u32)
#define PDOCKER_GPU_VULKAN_GRAPHICS_V616_CLEAR_RECT_FIELD_COUNT 8u

#define PDOCKER_GPU_VULKAN_GRAPHICS_V617_HEADER_EXTENSION_FIELDS(X) \
    X(query_command_count, u32) \
    X(query_command_entry_size, u32) \
    X(query_command_table_offset, u64) \
    X(query_command_table_size, u64) \
    X(query_command_schema_hash, u64) \
    X(query_command_table_hash, u64) \
    X(query_result_entry_size, u32) \
    X(query_result_schema_hash, u64) \
    X(extension_hash, u64)
#define PDOCKER_GPU_VULKAN_GRAPHICS_V617_HEADER_EXTENSION_FIELD_COUNT 9u

#define PDOCKER_GPU_VULKAN_GRAPHICS_V617_QUERY_COMMAND_FIELDS(X) \
    X(command_index, u32) \
    X(op, u32) \
    X(query_pool_id, u64) \
    X(first_query, u32) \
    X(query_count, u32) \
    X(stage_mask, u64) \
    X(result_fd_index, u32) \
    X(result_stride, u32) \
    X(result_offset, u64) \
    X(reserved0, u64)
#define PDOCKER_GPU_VULKAN_GRAPHICS_V617_QUERY_COMMAND_FIELD_COUNT 10u

#define PDOCKER_GPU_VULKAN_GRAPHICS_V617_QUERY_RESULT_FIELDS(X) \
    X(value, u64) \
    X(available, u32) \
    X(status, u32)
#define PDOCKER_GPU_VULKAN_GRAPHICS_V617_QUERY_RESULT_FIELD_COUNT 3u

#define PDOCKER_GPU_GRAPHICS_V6_ATTACHMENT_COLOR 1u
#define PDOCKER_GPU_GRAPHICS_V6_ATTACHMENT_DEPTH 2u
#define PDOCKER_GPU_GRAPHICS_V6_ATTACHMENT_STENCIL 3u
#define PDOCKER_GPU_GRAPHICS_V6_ATTACHMENT_DEPTH_STENCIL 4u
#define PDOCKER_GPU_GRAPHICS_V6_ATTACHMENT_UNUSED_SLOT 0x00000001u

#define PDOCKER_GPU_GRAPHICS_V6_COMMAND_BEGIN_RENDERING 1u
#define PDOCKER_GPU_GRAPHICS_V6_COMMAND_END_RENDERING 2u
#define PDOCKER_GPU_GRAPHICS_V6_COMMAND_BIND_PIPELINE 3u
#define PDOCKER_GPU_GRAPHICS_V6_COMMAND_BIND_DESCRIPTOR_SETS 4u
#define PDOCKER_GPU_GRAPHICS_V6_COMMAND_PUSH_CONSTANTS 5u
#define PDOCKER_GPU_GRAPHICS_V6_COMMAND_BIND_VERTEX_BUFFERS 6u
#define PDOCKER_GPU_GRAPHICS_V6_COMMAND_BIND_INDEX_BUFFER 7u
#define PDOCKER_GPU_GRAPHICS_V6_COMMAND_SET_DYNAMIC_STATE 8u
#define PDOCKER_GPU_GRAPHICS_V6_COMMAND_DRAW 9u
#define PDOCKER_GPU_GRAPHICS_V6_COMMAND_DRAW_INDEXED 10u
#define PDOCKER_GPU_GRAPHICS_V6_COMMAND_BARRIER 11u
#define PDOCKER_GPU_GRAPHICS_V6_COMMAND_COPY_BUFFER 12u
#define PDOCKER_GPU_GRAPHICS_V6_COMMAND_COPY_BUFFER_TO_IMAGE 13u
#define PDOCKER_GPU_GRAPHICS_V6_COMMAND_COPY_IMAGE_TO_BUFFER 14u
#define PDOCKER_GPU_GRAPHICS_V6_COMMAND_COPY_IMAGE 15u
#define PDOCKER_GPU_GRAPHICS_V6_COMMAND_FILL_BUFFER 16u
#define PDOCKER_GPU_GRAPHICS_V6_COMMAND_UPDATE_BUFFER 17u
#define PDOCKER_GPU_GRAPHICS_V6_COMMAND_CLEAR_COLOR_IMAGE 18u
#define PDOCKER_GPU_GRAPHICS_V6_COMMAND_CLEAR_DEPTH_STENCIL_IMAGE 19u
#define PDOCKER_GPU_GRAPHICS_V6_COMMAND_RESOLVE_IMAGE 20u
#define PDOCKER_GPU_GRAPHICS_V6_COMMAND_BLIT_IMAGE 21u
#define PDOCKER_GPU_GRAPHICS_V6_COMMAND_CLEAR_ATTACHMENTS 22u
#define PDOCKER_GPU_GRAPHICS_V6_COMMAND_RESET_QUERY_POOL 23u
#define PDOCKER_GPU_GRAPHICS_V6_COMMAND_WRITE_TIMESTAMP 24u
#define PDOCKER_GPU_GRAPHICS_V617_QUERY_OP_RESET 1u
#define PDOCKER_GPU_GRAPHICS_V617_QUERY_OP_WRITE_TIMESTAMP 2u
#define PDOCKER_GPU_GRAPHICS_V610_BUFFER_IMAGE_COPY_DIRECTION_BUFFER_TO_IMAGE 1u
#define PDOCKER_GPU_GRAPHICS_V610_BUFFER_IMAGE_COPY_DIRECTION_IMAGE_TO_BUFFER 2u

#define PDOCKER_GPU_VULKAN_GRAPHICS_V6_FRAME_HEADER_FIELDS(X) \
    X(magic, bytes8) \
    X(header_size, u16) \
    X(abi_major, u16) \
    X(abi_minor, u16) \
    X(command, u16) \
    X(flags, u32) \
    X(reserved0, u32) \
    X(frame_size, u64) \
    X(submit_id, u64) \
    X(fd_count, u32) \
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
    X(image_count, u32) \
    X(image_entry_size, u32) \
    X(image_table_offset, u64) \
    X(image_table_size, u64) \
    X(image_schema_hash, u64) \
    X(image_view_count, u32) \
    X(image_view_entry_size, u32) \
    X(image_view_table_offset, u64) \
    X(image_view_table_size, u64) \
    X(image_view_schema_hash, u64) \
    X(sampler_count, u32) \
    X(sampler_entry_size, u32) \
    X(sampler_table_offset, u64) \
    X(sampler_table_size, u64) \
    X(sampler_schema_hash, u64) \
    X(shader_stage_count, u32) \
    X(shader_stage_entry_size, u32) \
    X(shader_stage_table_offset, u64) \
    X(shader_stage_table_size, u64) \
    X(shader_stage_schema_hash, u64) \
    X(pipeline_count, u32) \
    X(pipeline_entry_size, u32) \
    X(pipeline_table_offset, u64) \
    X(pipeline_table_size, u64) \
    X(pipeline_schema_hash, u64) \
    X(vertex_binding_count, u32) \
    X(vertex_binding_entry_size, u32) \
    X(vertex_binding_table_offset, u64) \
    X(vertex_binding_table_size, u64) \
    X(vertex_binding_schema_hash, u64) \
    X(vertex_attribute_count, u32) \
    X(vertex_attribute_entry_size, u32) \
    X(vertex_attribute_table_offset, u64) \
    X(vertex_attribute_table_size, u64) \
    X(vertex_attribute_schema_hash, u64) \
    X(attachment_count, u32) \
    X(attachment_entry_size, u32) \
    X(attachment_table_offset, u64) \
    X(attachment_table_size, u64) \
    X(attachment_schema_hash, u64) \
    X(dynamic_state_count, u32) \
    X(dynamic_state_entry_size, u32) \
    X(dynamic_state_table_offset, u64) \
    X(dynamic_state_table_size, u64) \
    X(dynamic_state_schema_hash, u64) \
    X(command_count, u32) \
    X(command_entry_size, u32) \
    X(command_table_offset, u64) \
    X(command_table_size, u64) \
    X(command_schema_hash, u64) \
    X(payload_hash, u64) \
    X(frame_hash, u64)
#define PDOCKER_GPU_VULKAN_GRAPHICS_V6_FRAME_HEADER_FIELD_COUNT 72u

#define PDOCKER_GPU_VULKAN_GRAPHICS_V6_SHADER_STAGE_FIELDS(X) \
    X(stage_flags, u32) \
    X(shader_fd_index, u32) \
    X(shader_size, u64) \
    X(shader_hash, u64) \
    X(entry_name_offset, u64) \
    X(entry_name_size, u64) \
    X(specialization_offset, u64) \
    X(specialization_size, u64) \
    X(specialization_hash, u64)
#define PDOCKER_GPU_VULKAN_GRAPHICS_V6_SHADER_STAGE_FIELD_COUNT 9u

#define PDOCKER_GPU_VULKAN_GRAPHICS_V6_PIPELINE_FIELDS(X) \
    X(pipeline_id, u64) \
    X(layout_id, u64) \
    X(render_pass_id, u64) \
    X(shader_stage_first, u32) \
    X(shader_stage_count, u32) \
    X(vertex_binding_first, u32) \
    X(vertex_binding_count, u32) \
    X(vertex_attribute_first, u32) \
    X(vertex_attribute_count, u32) \
    X(topology, u32) \
    X(polygon_mode, u32) \
    X(cull_mode, u32) \
    X(front_face, u32) \
    X(rasterization_samples, u32) \
    X(color_attachment_count, u32) \
    X(subpass, u32) \
    X(depth_stencil_flags, u32) \
    X(dynamic_rendering_view_mask, u32) \
    X(dynamic_rendering_depth_format, u32) \
    X(dynamic_rendering_stencil_format, u32) \
    X(color_attachment_format0, u32) \
    X(color_attachment_format1, u32) \
    X(color_attachment_format2, u32) \
    X(color_attachment_format3, u32) \
    X(color_attachment_format4, u32) \
    X(color_attachment_format5, u32) \
    X(color_attachment_format6, u32) \
    X(color_attachment_format7, u32) \
    X(color_attachment_format8, u32) \
    X(color_attachment_format9, u32) \
    X(color_attachment_format10, u32) \
    X(color_attachment_format11, u32) \
    X(color_attachment_format12, u32) \
    X(color_attachment_format13, u32) \
    X(color_attachment_format14, u32) \
    X(color_attachment_format15, u32) \
    X(dynamic_state_mask, u64) \
    X(pipeline_hash, u64)
#define PDOCKER_GPU_VULKAN_GRAPHICS_V6_PIPELINE_FIELD_COUNT 38u

#define PDOCKER_GPU_VULKAN_GRAPHICS_V6_VERTEX_BINDING_FIELDS(X) \
    X(binding, u32) \
    X(stride, u32) \
    X(input_rate, u32) \
    X(buffer_resource_index, u32) \
    X(offset, u64) \
    X(size, u64)
#define PDOCKER_GPU_VULKAN_GRAPHICS_V6_VERTEX_BINDING_FIELD_COUNT 6u

#define PDOCKER_GPU_VULKAN_GRAPHICS_V6_VERTEX_ATTRIBUTE_FIELDS(X) \
    X(location, u32) \
    X(binding, u32) \
    X(format, u32) \
    X(offset, u32)
#define PDOCKER_GPU_VULKAN_GRAPHICS_V6_VERTEX_ATTRIBUTE_FIELD_COUNT 4u

#define PDOCKER_GPU_VULKAN_GRAPHICS_V6_ATTACHMENT_FIELDS(X) \
    X(attachment_role, u32) \
    X(flags, u32) \
    X(image_view_index, u32) \
    X(resolve_image_view_index, u32) \
    X(format, u32) \
    X(samples, u32) \
    X(layout, u32) \
    X(load_op, u32) \
    X(store_op, u32) \
    X(stencil_load_op, u32) \
    X(stencil_store_op, u32) \
    X(clear_value_offset, u64) \
    X(clear_value_size, u64) \
    X(resource_id, u64)
#define PDOCKER_GPU_VULKAN_GRAPHICS_V6_ATTACHMENT_FIELD_COUNT 14u

#define PDOCKER_GPU_VULKAN_GRAPHICS_V6_DYNAMIC_STATE_FIELDS(X) \
    X(state_type, u32) \
    X(flags, u32) \
    X(first_index, u32) \
    X(count, u32) \
    X(data_offset, u64) \
    X(data_size, u64) \
    X(data_hash, u64)
#define PDOCKER_GPU_VULKAN_GRAPHICS_V6_DYNAMIC_STATE_FIELD_COUNT 7u

#define PDOCKER_GPU_VULKAN_GRAPHICS_V61_HEADER_EXTENSION_FIELDS(X) \
    X(dynamic_offset_count, u32) \
    X(dynamic_offset_entry_size, u32) \
    X(dynamic_offset_table_offset, u64) \
    X(dynamic_offset_table_size, u64) \
    X(dynamic_offset_schema_hash, u64) \
    X(push_constant_metadata_count, u32) \
    X(push_constant_metadata_entry_size, u32) \
    X(push_constant_metadata_table_offset, u64) \
    X(push_constant_metadata_table_size, u64) \
    X(push_constant_metadata_schema_hash, u64) \
    X(image_barrier_count, u32) \
    X(image_barrier_entry_size, u32) \
    X(image_barrier_table_offset, u64) \
    X(image_barrier_table_size, u64) \
    X(image_barrier_schema_hash, u64) \
    X(memory_barrier_count, u32) \
    X(memory_barrier_entry_size, u32) \
    X(memory_barrier_table_offset, u64) \
    X(memory_barrier_table_size, u64) \
    X(memory_barrier_schema_hash, u64) \
    X(buffer_barrier_count, u32) \
    X(buffer_barrier_entry_size, u32) \
    X(buffer_barrier_table_offset, u64) \
    X(buffer_barrier_table_size, u64) \
    X(buffer_barrier_schema_hash, u64) \
    X(extension_hash, u64)
#define PDOCKER_GPU_VULKAN_GRAPHICS_V61_HEADER_EXTENSION_FIELD_COUNT 26u

#define PDOCKER_GPU_VULKAN_GRAPHICS_V61_DYNAMIC_OFFSET_FIELDS(X) \
    X(offset, u32) \
    X(reserved0, u32)
#define PDOCKER_GPU_VULKAN_GRAPHICS_V61_DYNAMIC_OFFSET_FIELD_COUNT 2u

#define PDOCKER_GPU_VULKAN_GRAPHICS_V61_PUSH_CONSTANT_METADATA_FIELDS(X) \
    X(command_index, u32) \
    X(stage_flags, u32) \
    X(layout_id, u64) \
    X(range_offset, u32) \
    X(range_size, u32)
#define PDOCKER_GPU_VULKAN_GRAPHICS_V61_PUSH_CONSTANT_METADATA_FIELD_COUNT 5u

#define PDOCKER_GPU_VULKAN_GRAPHICS_V61_IMAGE_BARRIER_FIELDS(X) \
    X(command_index, u32) \
    X(image_index, u32) \
    X(old_layout, u32) \
    X(new_layout, u32) \
    X(aspect_mask, u32) \
    X(base_mip_level, u32) \
    X(level_count, u32) \
    X(base_array_layer, u32) \
    X(layer_count, u32) \
    X(src_access_mask, u64) \
    X(dst_access_mask, u64) \
    X(src_stage_mask, u64) \
    X(dst_stage_mask, u64) \
    X(src_queue_family_index, u32) \
    X(dst_queue_family_index, u32) \
    X(reserved0, u32)
#define PDOCKER_GPU_VULKAN_GRAPHICS_V61_IMAGE_BARRIER_FIELD_COUNT 16u

#define PDOCKER_GPU_VULKAN_GRAPHICS_V61_MEMORY_BARRIER_FIELDS(X) \
    X(command_index, u32) \
    X(reserved0, u32) \
    X(src_access_mask, u64) \
    X(dst_access_mask, u64) \
    X(src_stage_mask, u64) \
    X(dst_stage_mask, u64)
#define PDOCKER_GPU_VULKAN_GRAPHICS_V61_MEMORY_BARRIER_FIELD_COUNT 6u

#define PDOCKER_GPU_VULKAN_GRAPHICS_V61_BUFFER_BARRIER_FIELDS(X) \
    X(command_index, u32) \
    X(resource_index, u32) \
    X(offset, u64) \
    X(size, u64) \
    X(src_access_mask, u64) \
    X(dst_access_mask, u64) \
    X(src_stage_mask, u64) \
    X(dst_stage_mask, u64) \
    X(src_queue_family_index, u32) \
    X(dst_queue_family_index, u32) \
    X(reserved0, u32) \
    X(reserved1, u32)
#define PDOCKER_GPU_VULKAN_GRAPHICS_V61_BUFFER_BARRIER_FIELD_COUNT 12u

#define PDOCKER_GPU_VULKAN_GRAPHICS_V62_HEADER_EXTENSION_FIELDS(X) \
    X(specialization_entry_count, u32) \
    X(specialization_entry_size, u32) \
    X(specialization_entry_table_offset, u64) \
    X(specialization_entry_table_size, u64) \
    X(specialization_entry_schema_hash, u64) \
    X(specialization_entry_table_hash, u64) \
    X(extension_hash, u64)
#define PDOCKER_GPU_VULKAN_GRAPHICS_V62_HEADER_EXTENSION_FIELD_COUNT 7u

#define PDOCKER_GPU_VULKAN_GRAPHICS_V62_SPECIALIZATION_ENTRY_FIELDS(X) \
    X(shader_stage_index, u32) \
    X(constant_id, u32) \
    X(offset, u32) \
    X(reserved0, u32) \
    X(size, u64)
#define PDOCKER_GPU_VULKAN_GRAPHICS_V62_SPECIALIZATION_ENTRY_FIELD_COUNT 5u

#define PDOCKER_GPU_VULKAN_GRAPHICS_V64_HEADER_EXTENSION_FIELDS(X) \
    X(resolve_attachment_count, u32) \
    X(resolve_attachment_entry_size, u32) \
    X(resolve_attachment_table_offset, u64) \
    X(resolve_attachment_table_size, u64) \
    X(resolve_attachment_schema_hash, u64) \
    X(resolve_attachment_table_hash, u64) \
    X(extension_hash, u64)
#define PDOCKER_GPU_VULKAN_GRAPHICS_V64_HEADER_EXTENSION_FIELD_COUNT 7u

#define PDOCKER_GPU_VULKAN_GRAPHICS_V65_HEADER_EXTENSION_FIELDS(X) \
    X(static_pipeline_state_count, u32) \
    X(static_pipeline_state_entry_size, u32) \
    X(static_pipeline_state_table_offset, u64) \
    X(static_pipeline_state_table_size, u64) \
    X(static_pipeline_state_schema_hash, u64) \
    X(static_pipeline_state_table_hash, u64) \
    X(extension_hash, u64)
#define PDOCKER_GPU_VULKAN_GRAPHICS_V65_HEADER_EXTENSION_FIELD_COUNT 7u

#define PDOCKER_GPU_VULKAN_GRAPHICS_V66_HEADER_EXTENSION_FIELDS(X) \
    X(color_blend_state_count, u32) \
    X(color_blend_state_entry_size, u32) \
    X(color_blend_state_table_offset, u64) \
    X(color_blend_state_table_size, u64) \
    X(color_blend_state_schema_hash, u64) \
    X(color_blend_state_table_hash, u64) \
    X(color_blend_attachment_count, u32) \
    X(color_blend_attachment_entry_size, u32) \
    X(color_blend_attachment_table_offset, u64) \
    X(color_blend_attachment_table_size, u64) \
    X(color_blend_attachment_schema_hash, u64) \
    X(color_blend_attachment_table_hash, u64) \
    X(extension_hash, u64)
#define PDOCKER_GPU_VULKAN_GRAPHICS_V66_HEADER_EXTENSION_FIELD_COUNT 13u

#define PDOCKER_GPU_VULKAN_GRAPHICS_V67_HEADER_EXTENSION_FIELDS(X) \
    X(viewport_scissor_state_count, u32) \
    X(viewport_scissor_state_entry_size, u32) \
    X(viewport_scissor_state_table_offset, u64) \
    X(viewport_scissor_state_table_size, u64) \
    X(viewport_scissor_state_schema_hash, u64) \
    X(viewport_scissor_state_table_hash, u64) \
    X(viewport_count, u32) \
    X(viewport_entry_size, u32) \
    X(viewport_table_offset, u64) \
    X(viewport_table_size, u64) \
    X(viewport_schema_hash, u64) \
    X(viewport_table_hash, u64) \
    X(scissor_count, u32) \
    X(scissor_entry_size, u32) \
    X(scissor_table_offset, u64) \
    X(scissor_table_size, u64) \
    X(scissor_schema_hash, u64) \
    X(scissor_table_hash, u64) \
    X(extension_hash, u64)
#define PDOCKER_GPU_VULKAN_GRAPHICS_V67_HEADER_EXTENSION_FIELD_COUNT 19u

#define PDOCKER_GPU_VULKAN_GRAPHICS_V6_COMMAND_FIELDS(X) \
    X(command_type, u32) \
    X(flags, u32) \
    X(pipeline_index, u32) \
    X(first_descriptor, u32) \
    X(descriptor_count, u32) \
    X(descriptor_first_set, u32) \
    X(first_dynamic_offset, u32) \
    X(dynamic_offset_count, u32) \
    X(vertex_binding_first, u32) \
    X(vertex_binding_count, u32) \
    X(index_buffer_resource_index, u32) \
    X(index_type, u32) \
    X(first_vertex, u32) \
    X(vertex_count, u32) \
    X(first_index, u32) \
    X(index_count, u32) \
    X(vertex_offset, i32) \
    X(first_instance, u32) \
    X(instance_count, u32) \
    X(attachment_first, u32) \
    X(attachment_count, u32) \
    X(render_area_offset_x, i32) \
    X(render_area_offset_y, i32) \
    X(render_area_extent_width, u32) \
    X(render_area_extent_height, u32) \
    X(rendering_layer_count, u32) \
    X(rendering_view_mask, u32) \
    X(dynamic_state_first, u32) \
    X(dynamic_state_count, u32) \
    X(index_offset, u64) \
    X(push_offset, u64) \
    X(push_size, u64) \
    X(push_hash, u64) \
    X(pipeline_layout_id, u64)
#define PDOCKER_GPU_VULKAN_GRAPHICS_V6_COMMAND_FIELD_COUNT 34u

typedef struct PdockerGpuVulkanGraphicsV6FrameHeader {
    char magic[8];
    uint16_t header_size;
    uint16_t abi_major;
    uint16_t abi_minor;
    uint16_t command;
    uint32_t flags;
    uint32_t reserved0;
    uint64_t frame_size;
    uint64_t submit_id;
    uint32_t fd_count;
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
    uint32_t image_count;
    uint32_t image_entry_size;
    uint64_t image_table_offset;
    uint64_t image_table_size;
    uint64_t image_schema_hash;
    uint32_t image_view_count;
    uint32_t image_view_entry_size;
    uint64_t image_view_table_offset;
    uint64_t image_view_table_size;
    uint64_t image_view_schema_hash;
    uint32_t sampler_count;
    uint32_t sampler_entry_size;
    uint64_t sampler_table_offset;
    uint64_t sampler_table_size;
    uint64_t sampler_schema_hash;
    uint32_t shader_stage_count;
    uint32_t shader_stage_entry_size;
    uint64_t shader_stage_table_offset;
    uint64_t shader_stage_table_size;
    uint64_t shader_stage_schema_hash;
    uint32_t pipeline_count;
    uint32_t pipeline_entry_size;
    uint64_t pipeline_table_offset;
    uint64_t pipeline_table_size;
    uint64_t pipeline_schema_hash;
    uint32_t vertex_binding_count;
    uint32_t vertex_binding_entry_size;
    uint64_t vertex_binding_table_offset;
    uint64_t vertex_binding_table_size;
    uint64_t vertex_binding_schema_hash;
    uint32_t vertex_attribute_count;
    uint32_t vertex_attribute_entry_size;
    uint64_t vertex_attribute_table_offset;
    uint64_t vertex_attribute_table_size;
    uint64_t vertex_attribute_schema_hash;
    uint32_t attachment_count;
    uint32_t attachment_entry_size;
    uint64_t attachment_table_offset;
    uint64_t attachment_table_size;
    uint64_t attachment_schema_hash;
    uint32_t dynamic_state_count;
    uint32_t dynamic_state_entry_size;
    uint64_t dynamic_state_table_offset;
    uint64_t dynamic_state_table_size;
    uint64_t dynamic_state_schema_hash;
    uint32_t command_count;
    uint32_t command_entry_size;
    uint64_t command_table_offset;
    uint64_t command_table_size;
    uint64_t command_schema_hash;
    uint64_t payload_hash;
    uint64_t frame_hash;
} PdockerGpuVulkanGraphicsV6FrameHeader;

typedef struct PdockerGpuVulkanGraphicsV6ShaderStageEntry {
    uint32_t stage_flags;
    uint32_t shader_fd_index;
    uint64_t shader_size;
    uint64_t shader_hash;
    uint64_t entry_name_offset;
    uint64_t entry_name_size;
    uint64_t specialization_offset;
    uint64_t specialization_size;
    uint64_t specialization_hash;
} PdockerGpuVulkanGraphicsV6ShaderStageEntry;

typedef struct PdockerGpuVulkanGraphicsV6PipelineEntry {
    uint64_t pipeline_id;
    uint64_t layout_id;
    uint64_t render_pass_id;
    uint32_t shader_stage_first;
    uint32_t shader_stage_count;
    uint32_t vertex_binding_first;
    uint32_t vertex_binding_count;
    uint32_t vertex_attribute_first;
    uint32_t vertex_attribute_count;
    uint32_t topology;
    uint32_t polygon_mode;
    uint32_t cull_mode;
    uint32_t front_face;
    uint32_t rasterization_samples;
    uint32_t color_attachment_count;
    uint32_t subpass;
    uint32_t depth_stencil_flags;
    uint32_t dynamic_rendering_view_mask;
    uint32_t dynamic_rendering_depth_format;
    uint32_t dynamic_rendering_stencil_format;
    uint32_t color_attachment_format0;
    uint32_t color_attachment_format1;
    uint32_t color_attachment_format2;
    uint32_t color_attachment_format3;
    uint32_t color_attachment_format4;
    uint32_t color_attachment_format5;
    uint32_t color_attachment_format6;
    uint32_t color_attachment_format7;
    uint32_t color_attachment_format8;
    uint32_t color_attachment_format9;
    uint32_t color_attachment_format10;
    uint32_t color_attachment_format11;
    uint32_t color_attachment_format12;
    uint32_t color_attachment_format13;
    uint32_t color_attachment_format14;
    uint32_t color_attachment_format15;
    uint64_t dynamic_state_mask;
    uint64_t pipeline_hash;
} PdockerGpuVulkanGraphicsV6PipelineEntry;

typedef struct PdockerGpuVulkanGraphicsV6VertexBindingEntry {
    uint32_t binding;
    uint32_t stride;
    uint32_t input_rate;
    uint32_t buffer_resource_index;
    uint64_t offset;
    uint64_t size;
} PdockerGpuVulkanGraphicsV6VertexBindingEntry;

typedef struct PdockerGpuVulkanGraphicsV6VertexAttributeEntry {
    uint32_t location;
    uint32_t binding;
    uint32_t format;
    uint32_t offset;
} PdockerGpuVulkanGraphicsV6VertexAttributeEntry;

typedef struct PdockerGpuVulkanGraphicsV6AttachmentEntry {
    uint32_t attachment_role;
    uint32_t flags;
    uint32_t image_view_index;
    uint32_t resolve_image_view_index;
    uint32_t format;
    uint32_t samples;
    uint32_t layout;
    uint32_t load_op;
    uint32_t store_op;
    uint32_t stencil_load_op;
    uint32_t stencil_store_op;
    uint64_t clear_value_offset;
    uint64_t clear_value_size;
    uint64_t resource_id;
} PdockerGpuVulkanGraphicsV6AttachmentEntry;

typedef struct PdockerGpuVulkanGraphicsV6DynamicStateEntry {
    uint32_t state_type;
    uint32_t flags;
    uint32_t first_index;
    uint32_t count;
    uint64_t data_offset;
    uint64_t data_size;
    uint64_t data_hash;
} PdockerGpuVulkanGraphicsV6DynamicStateEntry;

typedef struct PdockerGpuVulkanGraphicsV61HeaderExtension {
    uint32_t dynamic_offset_count;
    uint32_t dynamic_offset_entry_size;
    uint64_t dynamic_offset_table_offset;
    uint64_t dynamic_offset_table_size;
    uint64_t dynamic_offset_schema_hash;
    uint32_t push_constant_metadata_count;
    uint32_t push_constant_metadata_entry_size;
    uint64_t push_constant_metadata_table_offset;
    uint64_t push_constant_metadata_table_size;
    uint64_t push_constant_metadata_schema_hash;
    uint32_t image_barrier_count;
    uint32_t image_barrier_entry_size;
    uint64_t image_barrier_table_offset;
    uint64_t image_barrier_table_size;
    uint64_t image_barrier_schema_hash;
    uint32_t memory_barrier_count;
    uint32_t memory_barrier_entry_size;
    uint64_t memory_barrier_table_offset;
    uint64_t memory_barrier_table_size;
    uint64_t memory_barrier_schema_hash;
    uint32_t buffer_barrier_count;
    uint32_t buffer_barrier_entry_size;
    uint64_t buffer_barrier_table_offset;
    uint64_t buffer_barrier_table_size;
    uint64_t buffer_barrier_schema_hash;
    uint64_t extension_hash;
} PdockerGpuVulkanGraphicsV61HeaderExtension;

typedef struct PdockerGpuVulkanGraphicsV61FrameHeader {
    PdockerGpuVulkanGraphicsV6FrameHeader base;
    PdockerGpuVulkanGraphicsV61HeaderExtension v61;
} PdockerGpuVulkanGraphicsV61FrameHeader;

typedef struct PdockerGpuVulkanGraphicsV62HeaderExtension {
    uint32_t specialization_entry_count;
    uint32_t specialization_entry_size;
    uint64_t specialization_entry_table_offset;
    uint64_t specialization_entry_table_size;
    uint64_t specialization_entry_schema_hash;
    uint64_t specialization_entry_table_hash;
    uint64_t extension_hash;
} PdockerGpuVulkanGraphicsV62HeaderExtension;

typedef struct PdockerGpuVulkanGraphicsV62FrameHeader {
    PdockerGpuVulkanGraphicsV6FrameHeader base;
    PdockerGpuVulkanGraphicsV61HeaderExtension v61;
    PdockerGpuVulkanGraphicsV62HeaderExtension v62;
} PdockerGpuVulkanGraphicsV62FrameHeader;

typedef struct PdockerGpuVulkanGraphicsV63HeaderExtension {
    uint32_t depth_stencil_state_count;
    uint32_t depth_stencil_state_entry_size;
    uint64_t depth_stencil_state_table_offset;
    uint64_t depth_stencil_state_table_size;
    uint64_t depth_stencil_state_schema_hash;
    uint64_t depth_stencil_state_table_hash;
    uint64_t extension_hash;
} PdockerGpuVulkanGraphicsV63HeaderExtension;

typedef struct PdockerGpuVulkanGraphicsV63FrameHeader {
    PdockerGpuVulkanGraphicsV6FrameHeader base;
    PdockerGpuVulkanGraphicsV61HeaderExtension v61;
    PdockerGpuVulkanGraphicsV62HeaderExtension v62;
    PdockerGpuVulkanGraphicsV63HeaderExtension v63;
} PdockerGpuVulkanGraphicsV63FrameHeader;

typedef struct PdockerGpuVulkanGraphicsV64HeaderExtension {
    uint32_t resolve_attachment_count;
    uint32_t resolve_attachment_entry_size;
    uint64_t resolve_attachment_table_offset;
    uint64_t resolve_attachment_table_size;
    uint64_t resolve_attachment_schema_hash;
    uint64_t resolve_attachment_table_hash;
    uint64_t extension_hash;
} PdockerGpuVulkanGraphicsV64HeaderExtension;

typedef struct PdockerGpuVulkanGraphicsV64FrameHeader {
    PdockerGpuVulkanGraphicsV6FrameHeader base;
    PdockerGpuVulkanGraphicsV61HeaderExtension v61;
    PdockerGpuVulkanGraphicsV62HeaderExtension v62;
    PdockerGpuVulkanGraphicsV63HeaderExtension v63;
    PdockerGpuVulkanGraphicsV64HeaderExtension v64;
} PdockerGpuVulkanGraphicsV64FrameHeader;

typedef struct PdockerGpuVulkanGraphicsV65HeaderExtension {
    uint32_t static_pipeline_state_count;
    uint32_t static_pipeline_state_entry_size;
    uint64_t static_pipeline_state_table_offset;
    uint64_t static_pipeline_state_table_size;
    uint64_t static_pipeline_state_schema_hash;
    uint64_t static_pipeline_state_table_hash;
    uint64_t extension_hash;
} PdockerGpuVulkanGraphicsV65HeaderExtension;

typedef struct PdockerGpuVulkanGraphicsV65FrameHeader {
    PdockerGpuVulkanGraphicsV6FrameHeader base;
    PdockerGpuVulkanGraphicsV61HeaderExtension v61;
    PdockerGpuVulkanGraphicsV62HeaderExtension v62;
    PdockerGpuVulkanGraphicsV63HeaderExtension v63;
    PdockerGpuVulkanGraphicsV64HeaderExtension v64;
    PdockerGpuVulkanGraphicsV65HeaderExtension v65;
} PdockerGpuVulkanGraphicsV65FrameHeader;

typedef struct PdockerGpuVulkanGraphicsV66HeaderExtension {
    uint32_t color_blend_state_count;
    uint32_t color_blend_state_entry_size;
    uint64_t color_blend_state_table_offset;
    uint64_t color_blend_state_table_size;
    uint64_t color_blend_state_schema_hash;
    uint64_t color_blend_state_table_hash;
    uint32_t color_blend_attachment_count;
    uint32_t color_blend_attachment_entry_size;
    uint64_t color_blend_attachment_table_offset;
    uint64_t color_blend_attachment_table_size;
    uint64_t color_blend_attachment_schema_hash;
    uint64_t color_blend_attachment_table_hash;
    uint64_t extension_hash;
} PdockerGpuVulkanGraphicsV66HeaderExtension;

typedef struct PdockerGpuVulkanGraphicsV66FrameHeader {
    PdockerGpuVulkanGraphicsV6FrameHeader base;
    PdockerGpuVulkanGraphicsV61HeaderExtension v61;
    PdockerGpuVulkanGraphicsV62HeaderExtension v62;
    PdockerGpuVulkanGraphicsV63HeaderExtension v63;
    PdockerGpuVulkanGraphicsV64HeaderExtension v64;
    PdockerGpuVulkanGraphicsV65HeaderExtension v65;
    PdockerGpuVulkanGraphicsV66HeaderExtension v66;
} PdockerGpuVulkanGraphicsV66FrameHeader;

typedef struct PdockerGpuVulkanGraphicsV67HeaderExtension {
    uint32_t viewport_scissor_state_count;
    uint32_t viewport_scissor_state_entry_size;
    uint64_t viewport_scissor_state_table_offset;
    uint64_t viewport_scissor_state_table_size;
    uint64_t viewport_scissor_state_schema_hash;
    uint64_t viewport_scissor_state_table_hash;
    uint32_t viewport_count;
    uint32_t viewport_entry_size;
    uint64_t viewport_table_offset;
    uint64_t viewport_table_size;
    uint64_t viewport_schema_hash;
    uint64_t viewport_table_hash;
    uint32_t scissor_count;
    uint32_t scissor_entry_size;
    uint64_t scissor_table_offset;
    uint64_t scissor_table_size;
    uint64_t scissor_schema_hash;
    uint64_t scissor_table_hash;
    uint64_t extension_hash;
} PdockerGpuVulkanGraphicsV67HeaderExtension;

typedef struct PdockerGpuVulkanGraphicsV67FrameHeader {
    PdockerGpuVulkanGraphicsV6FrameHeader base;
    PdockerGpuVulkanGraphicsV61HeaderExtension v61;
    PdockerGpuVulkanGraphicsV62HeaderExtension v62;
    PdockerGpuVulkanGraphicsV63HeaderExtension v63;
    PdockerGpuVulkanGraphicsV64HeaderExtension v64;
    PdockerGpuVulkanGraphicsV65HeaderExtension v65;
    PdockerGpuVulkanGraphicsV66HeaderExtension v66;
    PdockerGpuVulkanGraphicsV67HeaderExtension v67;
} PdockerGpuVulkanGraphicsV67FrameHeader;

typedef struct PdockerGpuVulkanGraphicsV68HeaderExtension {
    uint32_t indirect_draw_count;
    uint32_t indirect_draw_entry_size;
    uint64_t indirect_draw_table_offset;
    uint64_t indirect_draw_table_size;
    uint64_t indirect_draw_schema_hash;
    uint64_t indirect_draw_table_hash;
    uint64_t extension_hash;
} PdockerGpuVulkanGraphicsV68HeaderExtension;

typedef struct PdockerGpuVulkanGraphicsV68FrameHeader {
    PdockerGpuVulkanGraphicsV6FrameHeader base;
    PdockerGpuVulkanGraphicsV61HeaderExtension v61;
    PdockerGpuVulkanGraphicsV62HeaderExtension v62;
    PdockerGpuVulkanGraphicsV63HeaderExtension v63;
    PdockerGpuVulkanGraphicsV64HeaderExtension v64;
    PdockerGpuVulkanGraphicsV65HeaderExtension v65;
    PdockerGpuVulkanGraphicsV66HeaderExtension v66;
    PdockerGpuVulkanGraphicsV67HeaderExtension v67;
    PdockerGpuVulkanGraphicsV68HeaderExtension v68;
} PdockerGpuVulkanGraphicsV68FrameHeader;

typedef struct PdockerGpuVulkanGraphicsV69HeaderExtension {
    uint32_t buffer_copy_count;
    uint32_t buffer_copy_entry_size;
    uint64_t buffer_copy_table_offset;
    uint64_t buffer_copy_table_size;
    uint64_t buffer_copy_schema_hash;
    uint64_t buffer_copy_table_hash;
    uint64_t extension_hash;
} PdockerGpuVulkanGraphicsV69HeaderExtension;

typedef struct PdockerGpuVulkanGraphicsV69FrameHeader {
    PdockerGpuVulkanGraphicsV6FrameHeader base;
    PdockerGpuVulkanGraphicsV61HeaderExtension v61;
    PdockerGpuVulkanGraphicsV62HeaderExtension v62;
    PdockerGpuVulkanGraphicsV63HeaderExtension v63;
    PdockerGpuVulkanGraphicsV64HeaderExtension v64;
    PdockerGpuVulkanGraphicsV65HeaderExtension v65;
    PdockerGpuVulkanGraphicsV66HeaderExtension v66;
    PdockerGpuVulkanGraphicsV67HeaderExtension v67;
    PdockerGpuVulkanGraphicsV68HeaderExtension v68;
    PdockerGpuVulkanGraphicsV69HeaderExtension v69;
} PdockerGpuVulkanGraphicsV69FrameHeader;

typedef struct PdockerGpuVulkanGraphicsV610HeaderExtension {
    uint32_t buffer_image_copy_count;
    uint32_t buffer_image_copy_entry_size;
    uint64_t buffer_image_copy_table_offset;
    uint64_t buffer_image_copy_table_size;
    uint64_t buffer_image_copy_schema_hash;
    uint64_t buffer_image_copy_table_hash;
    uint32_t image_copy_count;
    uint32_t image_copy_entry_size;
    uint64_t image_copy_table_offset;
    uint64_t image_copy_table_size;
    uint64_t image_copy_schema_hash;
    uint64_t image_copy_table_hash;
    uint64_t extension_hash;
} PdockerGpuVulkanGraphicsV610HeaderExtension;

typedef struct PdockerGpuVulkanGraphicsV610FrameHeader {
    PdockerGpuVulkanGraphicsV6FrameHeader base;
    PdockerGpuVulkanGraphicsV61HeaderExtension v61;
    PdockerGpuVulkanGraphicsV62HeaderExtension v62;
    PdockerGpuVulkanGraphicsV63HeaderExtension v63;
    PdockerGpuVulkanGraphicsV64HeaderExtension v64;
    PdockerGpuVulkanGraphicsV65HeaderExtension v65;
    PdockerGpuVulkanGraphicsV66HeaderExtension v66;
    PdockerGpuVulkanGraphicsV67HeaderExtension v67;
    PdockerGpuVulkanGraphicsV68HeaderExtension v68;
    PdockerGpuVulkanGraphicsV69HeaderExtension v69;
    PdockerGpuVulkanGraphicsV610HeaderExtension v610;
} PdockerGpuVulkanGraphicsV610FrameHeader;

typedef struct PdockerGpuVulkanGraphicsV611HeaderExtension {
    uint32_t fill_buffer_count;
    uint32_t fill_buffer_entry_size;
    uint64_t fill_buffer_table_offset;
    uint64_t fill_buffer_table_size;
    uint64_t fill_buffer_schema_hash;
    uint64_t fill_buffer_table_hash;
    uint32_t update_buffer_count;
    uint32_t update_buffer_entry_size;
    uint64_t update_buffer_table_offset;
    uint64_t update_buffer_table_size;
    uint64_t update_buffer_schema_hash;
    uint64_t update_buffer_table_hash;
    uint64_t update_payload_data_offset;
    uint64_t update_payload_data_size;
    uint64_t update_payload_data_hash;
    uint64_t extension_hash;
} PdockerGpuVulkanGraphicsV611HeaderExtension;

typedef struct PdockerGpuVulkanGraphicsV611FrameHeader {
    PdockerGpuVulkanGraphicsV6FrameHeader base;
    PdockerGpuVulkanGraphicsV61HeaderExtension v61;
    PdockerGpuVulkanGraphicsV62HeaderExtension v62;
    PdockerGpuVulkanGraphicsV63HeaderExtension v63;
    PdockerGpuVulkanGraphicsV64HeaderExtension v64;
    PdockerGpuVulkanGraphicsV65HeaderExtension v65;
    PdockerGpuVulkanGraphicsV66HeaderExtension v66;
    PdockerGpuVulkanGraphicsV67HeaderExtension v67;
    PdockerGpuVulkanGraphicsV68HeaderExtension v68;
    PdockerGpuVulkanGraphicsV69HeaderExtension v69;
    PdockerGpuVulkanGraphicsV610HeaderExtension v610;
    PdockerGpuVulkanGraphicsV611HeaderExtension v611;
} PdockerGpuVulkanGraphicsV611FrameHeader;

typedef struct PdockerGpuVulkanGraphicsV612HeaderExtension {
    uint32_t clear_color_image_count;
    uint32_t clear_color_image_entry_size;
    uint64_t clear_color_image_table_offset;
    uint64_t clear_color_image_table_size;
    uint64_t clear_color_image_schema_hash;
    uint64_t clear_color_image_table_hash;
    uint64_t extension_hash;
} PdockerGpuVulkanGraphicsV612HeaderExtension;

typedef struct PdockerGpuVulkanGraphicsV612FrameHeader {
    PdockerGpuVulkanGraphicsV6FrameHeader base;
    PdockerGpuVulkanGraphicsV61HeaderExtension v61;
    PdockerGpuVulkanGraphicsV62HeaderExtension v62;
    PdockerGpuVulkanGraphicsV63HeaderExtension v63;
    PdockerGpuVulkanGraphicsV64HeaderExtension v64;
    PdockerGpuVulkanGraphicsV65HeaderExtension v65;
    PdockerGpuVulkanGraphicsV66HeaderExtension v66;
    PdockerGpuVulkanGraphicsV67HeaderExtension v67;
    PdockerGpuVulkanGraphicsV68HeaderExtension v68;
    PdockerGpuVulkanGraphicsV69HeaderExtension v69;
    PdockerGpuVulkanGraphicsV610HeaderExtension v610;
    PdockerGpuVulkanGraphicsV611HeaderExtension v611;
    PdockerGpuVulkanGraphicsV612HeaderExtension v612;
} PdockerGpuVulkanGraphicsV612FrameHeader;

typedef struct PdockerGpuVulkanGraphicsV613HeaderExtension {
    uint32_t clear_depth_stencil_image_count;
    uint32_t clear_depth_stencil_image_entry_size;
    uint64_t clear_depth_stencil_image_table_offset;
    uint64_t clear_depth_stencil_image_table_size;
    uint64_t clear_depth_stencil_image_schema_hash;
    uint64_t clear_depth_stencil_image_table_hash;
    uint64_t extension_hash;
} PdockerGpuVulkanGraphicsV613HeaderExtension;

typedef struct PdockerGpuVulkanGraphicsV613FrameHeader {
    PdockerGpuVulkanGraphicsV6FrameHeader base;
    PdockerGpuVulkanGraphicsV61HeaderExtension v61;
    PdockerGpuVulkanGraphicsV62HeaderExtension v62;
    PdockerGpuVulkanGraphicsV63HeaderExtension v63;
    PdockerGpuVulkanGraphicsV64HeaderExtension v64;
    PdockerGpuVulkanGraphicsV65HeaderExtension v65;
    PdockerGpuVulkanGraphicsV66HeaderExtension v66;
    PdockerGpuVulkanGraphicsV67HeaderExtension v67;
    PdockerGpuVulkanGraphicsV68HeaderExtension v68;
    PdockerGpuVulkanGraphicsV69HeaderExtension v69;
    PdockerGpuVulkanGraphicsV610HeaderExtension v610;
    PdockerGpuVulkanGraphicsV611HeaderExtension v611;
    PdockerGpuVulkanGraphicsV612HeaderExtension v612;
    PdockerGpuVulkanGraphicsV613HeaderExtension v613;
} PdockerGpuVulkanGraphicsV613FrameHeader;

typedef struct PdockerGpuVulkanGraphicsV614HeaderExtension {
    uint32_t resolve_image_count;
    uint32_t resolve_image_entry_size;
    uint64_t resolve_image_table_offset;
    uint64_t resolve_image_table_size;
    uint64_t resolve_image_schema_hash;
    uint64_t resolve_image_table_hash;
    uint64_t extension_hash;
} PdockerGpuVulkanGraphicsV614HeaderExtension;

typedef struct PdockerGpuVulkanGraphicsV614FrameHeader {
    PdockerGpuVulkanGraphicsV6FrameHeader base;
    PdockerGpuVulkanGraphicsV61HeaderExtension v61;
    PdockerGpuVulkanGraphicsV62HeaderExtension v62;
    PdockerGpuVulkanGraphicsV63HeaderExtension v63;
    PdockerGpuVulkanGraphicsV64HeaderExtension v64;
    PdockerGpuVulkanGraphicsV65HeaderExtension v65;
    PdockerGpuVulkanGraphicsV66HeaderExtension v66;
    PdockerGpuVulkanGraphicsV67HeaderExtension v67;
    PdockerGpuVulkanGraphicsV68HeaderExtension v68;
    PdockerGpuVulkanGraphicsV69HeaderExtension v69;
    PdockerGpuVulkanGraphicsV610HeaderExtension v610;
    PdockerGpuVulkanGraphicsV611HeaderExtension v611;
    PdockerGpuVulkanGraphicsV612HeaderExtension v612;
    PdockerGpuVulkanGraphicsV613HeaderExtension v613;
    PdockerGpuVulkanGraphicsV614HeaderExtension v614;
} PdockerGpuVulkanGraphicsV614FrameHeader;

typedef struct PdockerGpuVulkanGraphicsV615HeaderExtension {
    uint32_t blit_image_count;
    uint32_t blit_image_entry_size;
    uint64_t blit_image_table_offset;
    uint64_t blit_image_table_size;
    uint64_t blit_image_schema_hash;
    uint64_t blit_image_table_hash;
    uint64_t extension_hash;
} PdockerGpuVulkanGraphicsV615HeaderExtension;

typedef struct PdockerGpuVulkanGraphicsV615FrameHeader {
    PdockerGpuVulkanGraphicsV6FrameHeader base;
    PdockerGpuVulkanGraphicsV61HeaderExtension v61;
    PdockerGpuVulkanGraphicsV62HeaderExtension v62;
    PdockerGpuVulkanGraphicsV63HeaderExtension v63;
    PdockerGpuVulkanGraphicsV64HeaderExtension v64;
    PdockerGpuVulkanGraphicsV65HeaderExtension v65;
    PdockerGpuVulkanGraphicsV66HeaderExtension v66;
    PdockerGpuVulkanGraphicsV67HeaderExtension v67;
    PdockerGpuVulkanGraphicsV68HeaderExtension v68;
    PdockerGpuVulkanGraphicsV69HeaderExtension v69;
    PdockerGpuVulkanGraphicsV610HeaderExtension v610;
    PdockerGpuVulkanGraphicsV611HeaderExtension v611;
    PdockerGpuVulkanGraphicsV612HeaderExtension v612;
    PdockerGpuVulkanGraphicsV613HeaderExtension v613;
    PdockerGpuVulkanGraphicsV614HeaderExtension v614;
    PdockerGpuVulkanGraphicsV615HeaderExtension v615;
} PdockerGpuVulkanGraphicsV615FrameHeader;

typedef struct PdockerGpuVulkanGraphicsV616HeaderExtension {
    uint32_t clear_attachments_command_count;
    uint32_t clear_attachments_command_entry_size;
    uint64_t clear_attachments_command_table_offset;
    uint64_t clear_attachments_command_table_size;
    uint64_t clear_attachments_command_schema_hash;
    uint64_t clear_attachments_command_table_hash;
    uint32_t clear_attachment_count;
    uint32_t clear_attachment_entry_size;
    uint64_t clear_attachment_table_offset;
    uint64_t clear_attachment_table_size;
    uint64_t clear_attachment_schema_hash;
    uint64_t clear_attachment_table_hash;
    uint32_t clear_rect_count;
    uint32_t clear_rect_entry_size;
    uint64_t clear_rect_table_offset;
    uint64_t clear_rect_table_size;
    uint64_t clear_rect_schema_hash;
    uint64_t clear_rect_table_hash;
    uint64_t extension_hash;
} PdockerGpuVulkanGraphicsV616HeaderExtension;

typedef struct PdockerGpuVulkanGraphicsV616FrameHeader {
    PdockerGpuVulkanGraphicsV6FrameHeader base;
    PdockerGpuVulkanGraphicsV61HeaderExtension v61;
    PdockerGpuVulkanGraphicsV62HeaderExtension v62;
    PdockerGpuVulkanGraphicsV63HeaderExtension v63;
    PdockerGpuVulkanGraphicsV64HeaderExtension v64;
    PdockerGpuVulkanGraphicsV65HeaderExtension v65;
    PdockerGpuVulkanGraphicsV66HeaderExtension v66;
    PdockerGpuVulkanGraphicsV67HeaderExtension v67;
    PdockerGpuVulkanGraphicsV68HeaderExtension v68;
    PdockerGpuVulkanGraphicsV69HeaderExtension v69;
    PdockerGpuVulkanGraphicsV610HeaderExtension v610;
    PdockerGpuVulkanGraphicsV611HeaderExtension v611;
    PdockerGpuVulkanGraphicsV612HeaderExtension v612;
    PdockerGpuVulkanGraphicsV613HeaderExtension v613;
    PdockerGpuVulkanGraphicsV614HeaderExtension v614;
    PdockerGpuVulkanGraphicsV615HeaderExtension v615;
    PdockerGpuVulkanGraphicsV616HeaderExtension v616;
} PdockerGpuVulkanGraphicsV616FrameHeader;

typedef struct PdockerGpuVulkanGraphicsV617HeaderExtension {
    uint32_t query_command_count;
    uint32_t query_command_entry_size;
    uint64_t query_command_table_offset;
    uint64_t query_command_table_size;
    uint64_t query_command_schema_hash;
    uint64_t query_command_table_hash;
    uint32_t query_result_entry_size;
    uint64_t query_result_schema_hash;
    uint64_t extension_hash;
} PdockerGpuVulkanGraphicsV617HeaderExtension;

typedef struct PdockerGpuVulkanGraphicsV617FrameHeader {
    PdockerGpuVulkanGraphicsV6FrameHeader base;
    PdockerGpuVulkanGraphicsV61HeaderExtension v61;
    PdockerGpuVulkanGraphicsV62HeaderExtension v62;
    PdockerGpuVulkanGraphicsV63HeaderExtension v63;
    PdockerGpuVulkanGraphicsV64HeaderExtension v64;
    PdockerGpuVulkanGraphicsV65HeaderExtension v65;
    PdockerGpuVulkanGraphicsV66HeaderExtension v66;
    PdockerGpuVulkanGraphicsV67HeaderExtension v67;
    PdockerGpuVulkanGraphicsV68HeaderExtension v68;
    PdockerGpuVulkanGraphicsV69HeaderExtension v69;
    PdockerGpuVulkanGraphicsV610HeaderExtension v610;
    PdockerGpuVulkanGraphicsV611HeaderExtension v611;
    PdockerGpuVulkanGraphicsV612HeaderExtension v612;
    PdockerGpuVulkanGraphicsV613HeaderExtension v613;
    PdockerGpuVulkanGraphicsV614HeaderExtension v614;
    PdockerGpuVulkanGraphicsV615HeaderExtension v615;
    PdockerGpuVulkanGraphicsV616HeaderExtension v616;
    PdockerGpuVulkanGraphicsV617HeaderExtension v617;
} PdockerGpuVulkanGraphicsV617FrameHeader;

typedef struct PdockerGpuVulkanGraphicsV62SpecializationEntry {
    uint32_t shader_stage_index;
    uint32_t constant_id;
    uint32_t offset;
    uint32_t reserved0;
    uint64_t size;
} PdockerGpuVulkanGraphicsV62SpecializationEntry;

typedef struct PdockerGpuVulkanGraphicsV63DepthStencilStateEntry {
    uint32_t pipeline_index;
    uint32_t flags;
    uint32_t depth_compare_op;
    uint32_t front_fail_op;
    uint32_t front_pass_op;
    uint32_t front_depth_fail_op;
    uint32_t front_compare_op;
    uint32_t front_compare_mask;
    uint32_t front_write_mask;
    uint32_t front_reference;
    uint32_t back_fail_op;
    uint32_t back_pass_op;
    uint32_t back_depth_fail_op;
    uint32_t back_compare_op;
    uint32_t back_compare_mask;
    uint32_t back_write_mask;
    uint32_t back_reference;
    uint32_t min_depth_bounds_bits;
    uint32_t max_depth_bounds_bits;
    uint32_t reserved0;
} PdockerGpuVulkanGraphicsV63DepthStencilStateEntry;

typedef struct PdockerGpuVulkanGraphicsV64ResolveAttachmentEntry {
    uint32_t attachment_index;
    uint32_t resolve_mode;
    uint32_t resolve_layout;
    uint32_t reserved0;
} PdockerGpuVulkanGraphicsV64ResolveAttachmentEntry;

typedef struct PdockerGpuVulkanGraphicsV65StaticPipelineStateEntry {
    uint32_t pipeline_index;
    uint32_t flags;
    uint32_t depth_bias_constant_factor_bits;
    uint32_t depth_bias_clamp_bits;
    uint32_t depth_bias_slope_factor_bits;
    uint32_t line_width_bits;
    uint32_t reserved0;
    uint32_t reserved1;
} PdockerGpuVulkanGraphicsV65StaticPipelineStateEntry;

typedef struct PdockerGpuVulkanGraphicsV66ColorBlendStateEntry {
    uint32_t pipeline_index;
    uint32_t flags;
    uint32_t logic_op;
    uint32_t attachment_first;
    uint32_t attachment_count;
    uint32_t blend_constant0_bits;
    uint32_t blend_constant1_bits;
    uint32_t blend_constant2_bits;
    uint32_t blend_constant3_bits;
    uint32_t reserved0;
} PdockerGpuVulkanGraphicsV66ColorBlendStateEntry;

typedef struct PdockerGpuVulkanGraphicsV66ColorBlendAttachmentEntry {
    uint32_t pipeline_index;
    uint32_t attachment_index;
    uint32_t flags;
    uint32_t src_color_blend_factor;
    uint32_t dst_color_blend_factor;
    uint32_t color_blend_op;
    uint32_t src_alpha_blend_factor;
    uint32_t dst_alpha_blend_factor;
    uint32_t alpha_blend_op;
    uint32_t color_write_mask;
} PdockerGpuVulkanGraphicsV66ColorBlendAttachmentEntry;

typedef struct PdockerGpuVulkanGraphicsV67ViewportScissorStateEntry {
    uint32_t pipeline_index;
    uint32_t flags;
    uint32_t viewport_static_first;
    uint32_t viewport_count;
    uint32_t scissor_static_first;
    uint32_t scissor_count;
    uint32_t reserved0;
    uint32_t reserved1;
} PdockerGpuVulkanGraphicsV67ViewportScissorStateEntry;

typedef struct PdockerGpuVulkanGraphicsV67ViewportEntry {
    uint32_t pipeline_index;
    uint32_t viewport_index;
    uint32_t x_bits;
    uint32_t y_bits;
    uint32_t width_bits;
    uint32_t height_bits;
    uint32_t min_depth_bits;
    uint32_t max_depth_bits;
} PdockerGpuVulkanGraphicsV67ViewportEntry;

typedef struct PdockerGpuVulkanGraphicsV67ScissorEntry {
    uint32_t pipeline_index;
    uint32_t scissor_index;
    int32_t offset_x;
    int32_t offset_y;
    uint32_t extent_width;
    uint32_t extent_height;
    uint32_t reserved0;
    uint32_t reserved1;
} PdockerGpuVulkanGraphicsV67ScissorEntry;

typedef struct PdockerGpuVulkanGraphicsV68IndirectDrawEntry {
    uint32_t command_index;
    uint32_t flags;
    uint32_t indirect_resource_index;
    uint32_t count_resource_index;
    uint64_t indirect_offset;
    uint64_t count_offset;
    uint32_t draw_count;
    uint32_t stride;
} PdockerGpuVulkanGraphicsV68IndirectDrawEntry;

typedef struct PdockerGpuVulkanGraphicsV69BufferCopyEntry {
    uint32_t command_index;
    uint32_t src_resource_index;
    uint32_t dst_resource_index;
    uint32_t reserved0;
    uint64_t src_offset;
    uint64_t dst_offset;
    uint64_t size;
} PdockerGpuVulkanGraphicsV69BufferCopyEntry;

typedef struct PdockerGpuVulkanGraphicsV610BufferImageCopyEntry {
    uint32_t command_index;
    uint32_t direction;
    uint32_t buffer_resource_index;
    uint32_t image_index;
    uint32_t image_layout;
    uint32_t aspect_mask;
    uint32_t mip_level;
    uint32_t base_array_layer;
    uint32_t layer_count;
    uint32_t buffer_row_length;
    uint32_t buffer_image_height;
    int32_t image_offset_x;
    int32_t image_offset_y;
    int32_t image_offset_z;
    uint32_t image_extent_width;
    uint32_t image_extent_height;
    uint32_t image_extent_depth;
    uint32_t reserved0;
    uint64_t buffer_offset;
} PdockerGpuVulkanGraphicsV610BufferImageCopyEntry;

typedef struct PdockerGpuVulkanGraphicsV610ImageCopyEntry {
    uint32_t command_index;
    uint32_t src_image_index;
    uint32_t dst_image_index;
    uint32_t src_layout;
    uint32_t dst_layout;
    uint32_t src_aspect_mask;
    uint32_t src_mip_level;
    uint32_t src_base_array_layer;
    uint32_t dst_aspect_mask;
    uint32_t dst_mip_level;
    uint32_t dst_base_array_layer;
    uint32_t layer_count;
    int32_t src_offset_x;
    int32_t src_offset_y;
    int32_t src_offset_z;
    int32_t dst_offset_x;
    int32_t dst_offset_y;
    int32_t dst_offset_z;
    uint32_t extent_width;
    uint32_t extent_height;
    uint32_t extent_depth;
    uint32_t reserved0;
} PdockerGpuVulkanGraphicsV610ImageCopyEntry;

typedef struct PdockerGpuVulkanGraphicsV611FillBufferEntry {
    uint32_t command_index;
    uint32_t dst_resource_index;
    uint32_t reserved0;
    uint32_t reserved1;
    uint64_t dst_offset;
    uint64_t size;
    uint32_t data;
    uint32_t reserved2;
} PdockerGpuVulkanGraphicsV611FillBufferEntry;

typedef struct PdockerGpuVulkanGraphicsV611UpdateBufferEntry {
    uint32_t command_index;
    uint32_t dst_resource_index;
    uint32_t reserved0;
    uint32_t reserved1;
    uint64_t dst_offset;
    uint64_t data_size;
    uint64_t payload_offset;
    uint64_t payload_hash;
} PdockerGpuVulkanGraphicsV611UpdateBufferEntry;

typedef struct PdockerGpuVulkanGraphicsV612ClearColorImageEntry {
    uint32_t command_index;
    uint32_t image_index;
    uint32_t image_layout;
    uint32_t aspect_mask;
    uint32_t base_mip_level;
    uint32_t level_count;
    uint32_t base_array_layer;
    uint32_t layer_count;
    uint32_t color_uint32_0;
    uint32_t color_uint32_1;
    uint32_t color_uint32_2;
    uint32_t color_uint32_3;
    uint32_t reserved0;
    uint32_t reserved1;
    uint32_t reserved2;
    uint32_t reserved3;
} PdockerGpuVulkanGraphicsV612ClearColorImageEntry;

typedef struct PdockerGpuVulkanGraphicsV613ClearDepthStencilImageEntry {
    uint32_t command_index;
    uint32_t image_index;
    uint32_t image_layout;
    uint32_t aspect_mask;
    uint32_t base_mip_level;
    uint32_t level_count;
    uint32_t base_array_layer;
    uint32_t layer_count;
    uint32_t depth_bits;
    uint32_t stencil;
    uint32_t reserved0;
    uint32_t reserved1;
    uint32_t reserved2;
    uint32_t reserved3;
    uint32_t reserved4;
    uint32_t reserved5;
} PdockerGpuVulkanGraphicsV613ClearDepthStencilImageEntry;

typedef struct PdockerGpuVulkanGraphicsV614ResolveImageEntry {
    uint32_t command_index;
    uint32_t src_image_index;
    uint32_t dst_image_index;
    uint32_t src_layout;
    uint32_t dst_layout;
    uint32_t src_aspect_mask;
    uint32_t src_mip_level;
    uint32_t src_base_array_layer;
    uint32_t dst_aspect_mask;
    uint32_t dst_mip_level;
    uint32_t dst_base_array_layer;
    uint32_t layer_count;
    int32_t src_offset_x;
    int32_t src_offset_y;
    int32_t src_offset_z;
    int32_t dst_offset_x;
    int32_t dst_offset_y;
    int32_t dst_offset_z;
    uint32_t extent_width;
    uint32_t extent_height;
    uint32_t extent_depth;
    uint32_t reserved0;
} PdockerGpuVulkanGraphicsV614ResolveImageEntry;

typedef struct PdockerGpuVulkanGraphicsV615BlitImageEntry {
    uint32_t command_index;
    uint32_t src_image_index;
    uint32_t dst_image_index;
    uint32_t src_layout;
    uint32_t dst_layout;
    uint32_t src_aspect_mask;
    uint32_t src_mip_level;
    uint32_t src_base_array_layer;
    uint32_t dst_aspect_mask;
    uint32_t dst_mip_level;
    uint32_t dst_base_array_layer;
    uint32_t layer_count;
    int32_t src_offset0_x;
    int32_t src_offset0_y;
    int32_t src_offset0_z;
    int32_t src_offset1_x;
    int32_t src_offset1_y;
    int32_t src_offset1_z;
    int32_t dst_offset0_x;
    int32_t dst_offset0_y;
    int32_t dst_offset0_z;
    int32_t dst_offset1_x;
    int32_t dst_offset1_y;
    int32_t dst_offset1_z;
    uint32_t filter;
    uint32_t reserved0;
    uint32_t reserved1;
    uint32_t reserved2;
} PdockerGpuVulkanGraphicsV615BlitImageEntry;

typedef struct PdockerGpuVulkanGraphicsV616ClearAttachmentsCommandEntry {
    uint32_t command_index;
    uint32_t clear_attachment_first;
    uint32_t clear_attachment_count;
    uint32_t clear_rect_first;
    uint32_t clear_rect_count;
    uint32_t reserved0;
    uint32_t reserved1;
    uint32_t reserved2;
} PdockerGpuVulkanGraphicsV616ClearAttachmentsCommandEntry;

typedef struct PdockerGpuVulkanGraphicsV616ClearAttachmentEntry {
    uint32_t aspect_mask;
    uint32_t color_attachment;
    uint32_t color_uint32_0;
    uint32_t color_uint32_1;
    uint32_t color_uint32_2;
    uint32_t color_uint32_3;
    uint32_t depth_bits;
    uint32_t stencil;
    uint32_t reserved0;
    uint32_t reserved1;
    uint32_t reserved2;
    uint32_t reserved3;
} PdockerGpuVulkanGraphicsV616ClearAttachmentEntry;

typedef struct PdockerGpuVulkanGraphicsV616ClearRectEntry {
    int32_t rect_offset_x;
    int32_t rect_offset_y;
    uint32_t rect_extent_width;
    uint32_t rect_extent_height;
    uint32_t base_array_layer;
    uint32_t layer_count;
    uint32_t reserved0;
    uint32_t reserved1;
} PdockerGpuVulkanGraphicsV616ClearRectEntry;

typedef struct PdockerGpuVulkanGraphicsV617QueryCommandEntry {
    uint32_t command_index;
    uint32_t op;
    uint64_t query_pool_id;
    uint32_t first_query;
    uint32_t query_count;
    uint64_t stage_mask;
    uint32_t result_fd_index;
    uint32_t result_stride;
    uint64_t result_offset;
    uint64_t reserved0;
} PdockerGpuVulkanGraphicsV617QueryCommandEntry;

typedef struct PdockerGpuVulkanGraphicsV617QueryResultEntry {
    uint64_t value;
    uint32_t available;
    uint32_t status;
} PdockerGpuVulkanGraphicsV617QueryResultEntry;

typedef struct PdockerGpuVulkanGraphicsV61DynamicOffsetEntry {
    uint32_t offset;
    uint32_t reserved0;
} PdockerGpuVulkanGraphicsV61DynamicOffsetEntry;

typedef struct PdockerGpuVulkanGraphicsV61PushConstantMetadataEntry {
    uint32_t command_index;
    uint32_t stage_flags;
    uint64_t layout_id;
    uint32_t range_offset;
    uint32_t range_size;
} PdockerGpuVulkanGraphicsV61PushConstantMetadataEntry;

typedef struct PdockerGpuVulkanGraphicsV61ImageBarrierEntry {
    uint32_t command_index;
    uint32_t image_index;
    uint32_t old_layout;
    uint32_t new_layout;
    uint32_t aspect_mask;
    uint32_t base_mip_level;
    uint32_t level_count;
    uint32_t base_array_layer;
    uint32_t layer_count;
    uint64_t src_access_mask;
    uint64_t dst_access_mask;
    uint64_t src_stage_mask;
    uint64_t dst_stage_mask;
    uint32_t src_queue_family_index;
    uint32_t dst_queue_family_index;
    uint32_t reserved0;
} PdockerGpuVulkanGraphicsV61ImageBarrierEntry;

typedef struct PdockerGpuVulkanGraphicsV61MemoryBarrierEntry {
    uint32_t command_index;
    uint32_t reserved0;
    uint64_t src_access_mask;
    uint64_t dst_access_mask;
    uint64_t src_stage_mask;
    uint64_t dst_stage_mask;
} PdockerGpuVulkanGraphicsV61MemoryBarrierEntry;

typedef struct PdockerGpuVulkanGraphicsV61BufferBarrierEntry {
    uint32_t command_index;
    uint32_t resource_index;
    uint64_t offset;
    uint64_t size;
    uint64_t src_access_mask;
    uint64_t dst_access_mask;
    uint64_t src_stage_mask;
    uint64_t dst_stage_mask;
    uint32_t src_queue_family_index;
    uint32_t dst_queue_family_index;
    uint32_t reserved0;
    uint32_t reserved1;
} PdockerGpuVulkanGraphicsV61BufferBarrierEntry;

typedef struct PdockerGpuVulkanGraphicsV6CommandEntry {
    uint32_t command_type;
    uint32_t flags;
    uint32_t pipeline_index;
    uint32_t first_descriptor;
    uint32_t descriptor_count;
    uint32_t descriptor_first_set;
    uint32_t first_dynamic_offset;
    uint32_t dynamic_offset_count;
    uint32_t vertex_binding_first;
    uint32_t vertex_binding_count;
    uint32_t index_buffer_resource_index;
    uint32_t index_type;
    uint32_t first_vertex;
    uint32_t vertex_count;
    uint32_t first_index;
    uint32_t index_count;
    int32_t vertex_offset;
    uint32_t first_instance;
    uint32_t instance_count;
    uint32_t attachment_first;
    uint32_t attachment_count;
    int32_t render_area_offset_x;
    int32_t render_area_offset_y;
    uint32_t render_area_extent_width;
    uint32_t render_area_extent_height;
    uint32_t rendering_layer_count;
    uint32_t rendering_view_mask;
    uint32_t dynamic_state_first;
    uint32_t dynamic_state_count;
    uint64_t index_offset;
    uint64_t push_offset;
    uint64_t push_size;
    uint64_t push_hash;
    uint64_t pipeline_layout_id;
} PdockerGpuVulkanGraphicsV6CommandEntry;

#endif

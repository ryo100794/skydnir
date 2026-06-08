/*
 * Minimal glibc-facing pdocker Vulkan ICD.
 *
 * This is the standard Vulkan-loader entry point that containers should see.
 * It deliberately does not dlopen Android/Bionic vendor Vulkan libraries from
 * glibc. Real execution is added below this ICD by lowering Vulkan calls into
 * the pdocker GPU command bridge.
 */
#include "pdocker_gpu_abi.h"

#include <stdbool.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/syscall.h>
#include <sys/un.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
#include <vulkan/vulkan.h>
#include <vulkan/vk_icd.h>

#ifndef MFD_CLOEXEC
#define MFD_CLOEXEC 0x0001U
#endif

#ifndef VK_KHR_8BIT_STORAGE_EXTENSION_NAME
#define VK_KHR_8BIT_STORAGE_EXTENSION_NAME "VK_KHR_8bit_storage"
#define VK_KHR_8BIT_STORAGE_SPEC_VERSION 1
#endif

#ifndef VK_KHR_SHADER_FLOAT16_INT8_EXTENSION_NAME
#define VK_KHR_SHADER_FLOAT16_INT8_EXTENSION_NAME "VK_KHR_shader_float16_int8"
#define VK_KHR_SHADER_FLOAT16_INT8_SPEC_VERSION 1
#endif

#ifndef VK_KHR_STORAGE_BUFFER_STORAGE_CLASS_EXTENSION_NAME
#define VK_KHR_STORAGE_BUFFER_STORAGE_CLASS_EXTENSION_NAME "VK_KHR_storage_buffer_storage_class"
#define VK_KHR_STORAGE_BUFFER_STORAGE_CLASS_SPEC_VERSION 1
#endif

#ifndef VK_KHR_COPY_COMMANDS_2_EXTENSION_NAME
#define VK_KHR_COPY_COMMANDS_2_EXTENSION_NAME "VK_KHR_copy_commands2"
#define VK_KHR_COPY_COMMANDS_2_SPEC_VERSION 1
#endif

#ifndef VK_KHR_SYNCHRONIZATION_2_EXTENSION_NAME
#define VK_KHR_SYNCHRONIZATION_2_EXTENSION_NAME "VK_KHR_synchronization2"
#define VK_KHR_SYNCHRONIZATION_2_SPEC_VERSION 1
#endif

#ifndef VK_KHR_DYNAMIC_RENDERING_EXTENSION_NAME
#define VK_KHR_DYNAMIC_RENDERING_EXTENSION_NAME "VK_KHR_dynamic_rendering"
#define VK_KHR_DYNAMIC_RENDERING_SPEC_VERSION 1
#endif

typedef struct {
    VK_LOADER_DATA loader;
} PdockerVkInstance;

typedef struct {
    VK_LOADER_DATA loader;
} PdockerVkPhysicalDevice;

typedef struct {
    VK_LOADER_DATA loader;
    uint64_t requested_feature_mask;
} PdockerVkDevice;

typedef struct {
    VK_LOADER_DATA loader;
} PdockerVkQueue;

typedef struct PdockerVkMemory PdockerVkMemory;
typedef struct PdockerVkBuffer PdockerVkBuffer;
typedef struct PdockerVkDescriptorBinding PdockerVkDescriptorBinding;
typedef struct PdockerVkDescriptorSetLayout PdockerVkDescriptorSetLayout;
typedef struct PdockerVkDescriptorSet PdockerVkDescriptorSet;
typedef struct PdockerVkShaderModule PdockerVkShaderModule;
typedef struct PdockerVkPipelineLayout PdockerVkPipelineLayout;
typedef struct PdockerVkPipeline PdockerVkPipeline;
typedef struct PdockerVkFence PdockerVkFence;
typedef struct PdockerVkImage PdockerVkImage;
typedef struct PdockerVkImageView PdockerVkImageView;
typedef struct PdockerVkSampler PdockerVkSampler;
typedef struct PdockerVkEvent PdockerVkEvent;
typedef struct PdockerVkQueryPool PdockerVkQueryPool;
typedef struct PdockerVkRenderPass PdockerVkRenderPass;
typedef struct PdockerVkFramebuffer PdockerVkFramebuffer;

#define PDOCKER_VK_MAX_STORAGE_BUFFERS 16
#define PDOCKER_VK_MAX_DESCRIPTOR_ARRAY_ELEMENTS PDOCKER_VK_MAX_STORAGE_BUFFERS
#define PDOCKER_VK_MAX_DESCRIPTOR_SETS 8
#define PDOCKER_VK_MAX_PUSH_BYTES 256
#define PDOCKER_VK_MAX_PUSH_CONSTANT_RANGES 8
#define PDOCKER_VK_MAX_PUSH_CONSTANT_OPS 64
#define PDOCKER_VK_MAX_ENTRY_NAME 128
#define PDOCKER_VK_MAX_SPECIALIZATION_ENTRIES 16
#define PDOCKER_VK_MAX_SPECIALIZATION_BYTES 256
#define PDOCKER_VK_REQUIREMENT_ALIGNMENT 16ull
#define PDOCKER_VK_MAX_COPY_OPS 64
#define PDOCKER_VK_MAX_DISPATCH_OPS 128
#define PDOCKER_VK_MAX_COMMAND_OPS 256
#define PDOCKER_VK_MAX_GRAPHICS_VERTEX_BINDINGS 16
#define PDOCKER_VK_MAX_GRAPHICS_VERTEX_ATTRIBUTES 32
#define PDOCKER_VK_MAX_GRAPHICS_DYNAMIC_STATES 64

static uint32_t pdocker_vk_graphics_dynamic_state_bit_index(VkDynamicState state) {
    switch (state) {
        case VK_DYNAMIC_STATE_VIEWPORT: return 0u;
        case VK_DYNAMIC_STATE_SCISSOR: return 1u;
        case VK_DYNAMIC_STATE_LINE_WIDTH: return 2u;
        case VK_DYNAMIC_STATE_CULL_MODE: return 3u;
        case VK_DYNAMIC_STATE_FRONT_FACE: return 4u;
        case VK_DYNAMIC_STATE_PRIMITIVE_TOPOLOGY: return 5u;
        case VK_DYNAMIC_STATE_DEPTH_BIAS: return 6u;
        case VK_DYNAMIC_STATE_BLEND_CONSTANTS: return 7u;
        case VK_DYNAMIC_STATE_DEPTH_BOUNDS: return 8u;
        case VK_DYNAMIC_STATE_STENCIL_COMPARE_MASK: return 9u;
        case VK_DYNAMIC_STATE_STENCIL_WRITE_MASK: return 10u;
        case VK_DYNAMIC_STATE_STENCIL_REFERENCE: return 11u;
        case VK_DYNAMIC_STATE_DEPTH_TEST_ENABLE: return 12u;
        case VK_DYNAMIC_STATE_DEPTH_WRITE_ENABLE: return 13u;
        case VK_DYNAMIC_STATE_DEPTH_COMPARE_OP: return 14u;
        case VK_DYNAMIC_STATE_STENCIL_TEST_ENABLE: return 15u;
        case VK_DYNAMIC_STATE_STENCIL_OP: return 16u;
        default: return UINT32_MAX;
    }
}

static uint64_t pdocker_vk_graphics_dynamic_state_bit(VkDynamicState state) {
    uint32_t bit = pdocker_vk_graphics_dynamic_state_bit_index(state);
    return bit < 64u ? (1ull << bit) : 0ull;
}
#define PDOCKER_VK_MAX_GRAPHICS_DRAW_OPS 128
#define PDOCKER_VK_MAX_GRAPHICS_DESCRIPTOR_BIND_OPS 128
#define PDOCKER_VK_MAX_GRAPHICS_RENDERING_OPS 128
#define PDOCKER_VK_MAX_GRAPHICS_COMMAND_OPS 512
#define PDOCKER_VK_MAX_GRAPHICS_DYNAMIC_OFFSETS 4096
#define PDOCKER_VK_MAX_CLEAR_ATTACHMENTS_COMMANDS PDOCKER_GPU_VULKAN_GRAPHICS_V616_MAX_CLEAR_ATTACHMENTS_COMMANDS
#define PDOCKER_VK_MAX_CLEAR_ATTACHMENTS PDOCKER_GPU_VULKAN_GRAPHICS_V616_MAX_CLEAR_ATTACHMENTS
#define PDOCKER_VK_MAX_CLEAR_RECTS PDOCKER_GPU_VULKAN_GRAPHICS_V616_MAX_CLEAR_RECTS
#define PDOCKER_VK_MAX_QUERY_COUNT 4096
#define PDOCKER_VK_MAX_COPY_ALIASES 128
#define PDOCKER_VK_ALIAS_MIN_SOURCE_BYTES (64ull * 1024ull * 1024ull)
#define PDOCKER_VK_MAX_GUARDED_MEMORIES 256
#define PDOCKER_VK_MAX_PROBE_SHADER_BYTES (8ull * 1024ull * 1024ull)
#define PDOCKER_VK_MAX_PROBE_MANIFEST_BYTES (1024ull * 1024ull)
#define PDOCKER_VULKAN_ICD_BUILD_MARKER "vulkan-icd-feature-chain-marker-20260518"
#define PDOCKER_VK_GUARDED_DEFAULT_MIN_BYTES (64ull * 1024ull * 1024ull)

static uint64_t g_generic_dispatch_sequence = 0;

#define PDOCKER_VK_FEATURE_SHADER_INT64                 (1ull << 0)
#define PDOCKER_VK_FEATURE_SHADER_INT16                 (1ull << 1)
#define PDOCKER_VK_FEATURE_SHADER_FLOAT64               (1ull << 2)
#define PDOCKER_VK_FEATURE_STORAGE_BUFFER_16            (1ull << 3)
#define PDOCKER_VK_FEATURE_UNIFORM_STORAGE_BUFFER_16    (1ull << 4)
#define PDOCKER_VK_FEATURE_STORAGE_PUSH_CONSTANT_16     (1ull << 5)
#define PDOCKER_VK_FEATURE_STORAGE_BUFFER_8             (1ull << 6)
#define PDOCKER_VK_FEATURE_UNIFORM_STORAGE_BUFFER_8     (1ull << 7)
#define PDOCKER_VK_FEATURE_STORAGE_PUSH_CONSTANT_8      (1ull << 8)
#define PDOCKER_VK_FEATURE_SHADER_FLOAT16               (1ull << 9)
#define PDOCKER_VK_FEATURE_SHADER_INT8                  (1ull << 10)
#define PDOCKER_VK_FEATURE_BUFFER_DEVICE_ADDRESS        (1ull << 11)
#define PDOCKER_VK_FEATURE_VULKAN_MEMORY_MODEL          (1ull << 12)
#define PDOCKER_VK_FEATURE_MAINTENANCE_4                (1ull << 13)
#define PDOCKER_VK_FEATURE_INDEX_TYPE_UINT8             (1ull << 14)
#define PDOCKER_VK_FEATURE_TIMELINE_SEMAPHORE           (1ull << 15)

struct PdockerVkMemory {
    size_t size;
    uint32_t memory_type_index;
    VkMemoryPropertyFlags property_flags;
    int fd;
    void *map;
    bool guarded;
    size_t page_size;
    size_t page_count;
    unsigned char *resident_pages;
    unsigned char *dirty_pages;
};

typedef struct {
    bool valid;
    PdockerVkMemory *src_memory;
    VkDeviceSize src_offset;
    VkDeviceSize dst_offset;
    VkDeviceSize size;
} PdockerVkCopyAlias;

struct PdockerVkBuffer {
    size_t size;
    VkDeviceSize requirements_size;
    VkDeviceSize requirements_alignment;
    PdockerVkMemory *memory;
    VkDeviceSize memory_offset;
    PdockerVkCopyAlias aliases[PDOCKER_VK_MAX_COPY_ALIASES];
    uint32_t alias_count;
};

struct PdockerVkImage {
    VkImageCreateFlags flags;
    VkImageType image_type;
    VkFormat format;
    VkExtent3D extent;
    uint32_t mip_levels;
    uint32_t array_layers;
    VkSampleCountFlagBits samples;
    VkImageTiling tiling;
    VkImageUsageFlags usage;
    VkSharingMode sharing_mode;
    VkImageLayout initial_layout;
    VkImageLayout current_layout;
    uint64_t layout_generation;
    bool layout_mixed;
    VkDeviceSize requirements_size;
    VkDeviceSize requirements_alignment;
    uint32_t memory_type_bits;
    PdockerVkMemory *memory;
    VkDeviceSize memory_offset;
    uint64_t generation;
};

struct PdockerVkImageView {
    PdockerVkImage *image;
    VkImageViewType view_type;
    VkFormat format;
    VkComponentMapping components;
    VkImageSubresourceRange subresource_range;
    uint64_t generation;
};

struct PdockerVkSampler {
    VkFilter mag_filter;
    VkFilter min_filter;
    VkSamplerMipmapMode mipmap_mode;
    VkSamplerAddressMode address_mode_u;
    VkSamplerAddressMode address_mode_v;
    VkSamplerAddressMode address_mode_w;
    float mip_lod_bias;
    VkBool32 anisotropy_enable;
    float max_anisotropy;
    VkBool32 compare_enable;
    VkCompareOp compare_op;
    float min_lod;
    float max_lod;
    VkBorderColor border_color;
    VkBool32 unnormalized_coordinates;
    uint64_t generation;
};

struct PdockerVkDescriptorBinding {
    PdockerVkBuffer *buffer;
    PdockerVkImageView *image_view;
    PdockerVkSampler *sampler;
    VkDeviceSize offset;
    VkDeviceSize base_offset;
    VkDeviceSize dynamic_offset;
    VkDeviceSize range;
    VkImageLayout image_layout;
    VkDescriptorType descriptor_type;
    bool dynamic;
};

struct PdockerVkDescriptorSetLayout {
    uint32_t storage_binding_count;
    VkDescriptorType storage_binding_types[PDOCKER_VK_MAX_STORAGE_BUFFERS];
    uint32_t storage_binding_counts[PDOCKER_VK_MAX_STORAGE_BUFFERS];
    bool unsupported_descriptor_array;
    bool unsupported_descriptor_type;
};

struct PdockerVkDescriptorSet {
    PdockerVkDescriptorSetLayout *layout;
    PdockerVkDescriptorBinding storage_buffers
        [PDOCKER_VK_MAX_STORAGE_BUFFERS][PDOCKER_VK_MAX_DESCRIPTOR_ARRAY_ELEMENTS];
    bool unsupported_descriptor_array;
    bool unsupported_descriptor_type;
    bool has_image_descriptor;
};

struct PdockerVkShaderModule {
    size_t code_size;
    uint32_t first_word;
    int code_fd;
    void *code_map;
};

typedef struct {
    VkShaderStageFlags stage_flags;
    uint32_t offset;
    uint32_t size;
} PdockerVkPushConstantRangeSnapshot;

typedef struct {
    VkShaderStageFlags stage_flags;
    uint32_t offset;
    uint32_t size;
    uint64_t layout_id;
    uint64_t value_hash;
} PdockerVkPushConstantOpSnapshot;

struct PdockerVkPipelineLayout {
    uint64_t layout_id;
    uint32_t push_constant_size;
    PdockerVkPushConstantRangeSnapshot push_constant_ranges[PDOCKER_VK_MAX_PUSH_CONSTANT_RANGES];
    uint32_t push_constant_range_count;
    uint32_t set_layout_count;
    PdockerVkDescriptorSetLayout *set_layouts[PDOCKER_VK_MAX_DESCRIPTOR_SETS];
    bool unsupported_set_layout_count;
    bool unsupported_push_constant_ranges;
};

struct PdockerVkPipeline {
    PdockerVkShaderModule *shader;
    PdockerVkPipelineLayout *layout;
    uint64_t requested_feature_mask;
    uint32_t local_size_x;
    char entry_name[PDOCKER_VK_MAX_ENTRY_NAME];
    VkSpecializationMapEntry specialization_entries[PDOCKER_VK_MAX_SPECIALIZATION_ENTRIES];
    uint32_t specialization_entry_count;
    uint8_t specialization_data[PDOCKER_VK_MAX_SPECIALIZATION_BYTES];
    size_t specialization_data_size;
    bool specialization_too_large;
    bool graphics;
    bool graphics_unsupported;
    PdockerVkRenderPass *render_pass;
    uint32_t shader_stage_count;
    VkShaderStageFlags shader_stage_flags;
    PdockerVkShaderModule *graphics_stage_modules[PDOCKER_VK_MAX_GRAPHICS_VERTEX_BINDINGS];
    VkShaderStageFlagBits graphics_stage_flags[PDOCKER_VK_MAX_GRAPHICS_VERTEX_BINDINGS];
    char graphics_stage_entry_names[PDOCKER_VK_MAX_GRAPHICS_VERTEX_BINDINGS][PDOCKER_VK_MAX_ENTRY_NAME];
    VkSpecializationMapEntry graphics_stage_specialization_entries
        [PDOCKER_VK_MAX_GRAPHICS_VERTEX_BINDINGS][PDOCKER_VK_MAX_SPECIALIZATION_ENTRIES];
    uint32_t graphics_stage_specialization_entry_counts[PDOCKER_VK_MAX_GRAPHICS_VERTEX_BINDINGS];
    uint8_t graphics_stage_specialization_data
        [PDOCKER_VK_MAX_GRAPHICS_VERTEX_BINDINGS][PDOCKER_VK_MAX_SPECIALIZATION_BYTES];
    size_t graphics_stage_specialization_data_sizes[PDOCKER_VK_MAX_GRAPHICS_VERTEX_BINDINGS];
    bool graphics_stage_specialization_too_large[PDOCKER_VK_MAX_GRAPHICS_VERTEX_BINDINGS];
    VkPrimitiveTopology topology;
    VkPolygonMode polygon_mode;
    VkCullModeFlags cull_mode;
    VkFrontFace front_face;
    bool primitive_restart_enable;
    bool depth_clamp_enable;
    bool rasterizer_discard_enable;
    bool depth_bias_enable;
    float depth_bias_constant_factor;
    float depth_bias_clamp;
    float depth_bias_slope_factor;
    float line_width;
    VkSampleCountFlagBits rasterization_samples;
    uint32_t subpass;
    uint32_t color_attachment_count;
    bool color_blend_logic_op_enable;
    VkLogicOp color_blend_logic_op;
    float color_blend_constants[4];
    VkPipelineColorBlendAttachmentState color_blend_attachments[PDOCKER_VK_MAX_STORAGE_BUFFERS];
    bool color_blend_attachment_overflow;
    uint32_t viewport_count;
    uint32_t scissor_count;
    VkViewport static_viewports[PDOCKER_GPU_VULKAN_GRAPHICS_V67_MAX_VIEWPORTS_PER_PIPELINE];
    VkRect2D static_scissors[PDOCKER_GPU_VULKAN_GRAPHICS_V67_MAX_SCISSORS_PER_PIPELINE];
    bool viewport_state_overflow;
    bool scissor_state_overflow;
    bool dynamic_rendering_pipeline;
    bool dynamic_rendering_format_overflow;
    uint32_t dynamic_rendering_view_mask;
    uint32_t dynamic_rendering_color_attachment_count;
    VkFormat dynamic_rendering_color_formats[PDOCKER_VK_MAX_STORAGE_BUFFERS];
    VkFormat dynamic_rendering_depth_format;
    VkFormat dynamic_rendering_stencil_format;
    uint32_t depth_stencil_flags;
    VkCompareOp depth_compare_op;
    VkStencilOpState front_stencil_state;
    VkStencilOpState back_stencil_state;
    float min_depth_bounds;
    float max_depth_bounds;
    uint32_t vertex_binding_count;
    VkVertexInputBindingDescription vertex_bindings[PDOCKER_VK_MAX_GRAPHICS_VERTEX_BINDINGS];
    uint32_t vertex_attribute_count;
    VkVertexInputAttributeDescription vertex_attributes[PDOCKER_VK_MAX_GRAPHICS_VERTEX_ATTRIBUTES];
    uint64_t dynamic_state_mask;
};

struct PdockerVkFence {
    bool signaled;
};

typedef struct PdockerVkSemaphore {
    bool signaled;
    bool timeline;
    uint64_t value;
} PdockerVkSemaphore;

struct PdockerVkEvent {
    bool signaled;
};

struct PdockerVkQueryPool {
    VkQueryType type;
    uint32_t query_count;
    uint64_t pool_id;
    int result_fd;
    size_t result_size;
    uint64_t *values;
    uint8_t *available;
    uint8_t *active;
    PdockerGpuVulkanGraphicsV617QueryResultEntry *result_entries;
};

typedef struct {
    VkFormat format;
    VkSampleCountFlagBits samples;
    VkAttachmentLoadOp load_op;
    VkAttachmentStoreOp store_op;
    VkAttachmentLoadOp stencil_load_op;
    VkAttachmentStoreOp stencil_store_op;
    VkImageLayout initial_layout;
    VkImageLayout final_layout;
} PdockerVkRenderPassAttachmentState;

typedef struct {
    uint32_t color_attachment_count;
    uint32_t color_attachments[PDOCKER_VK_MAX_STORAGE_BUFFERS];
    VkImageLayout color_layouts[PDOCKER_VK_MAX_STORAGE_BUFFERS];
    uint32_t resolve_attachments[PDOCKER_VK_MAX_STORAGE_BUFFERS];
    VkImageLayout resolve_layouts[PDOCKER_VK_MAX_STORAGE_BUFFERS];
    bool has_depth_stencil_attachment;
    uint32_t depth_stencil_attachment;
    VkImageLayout depth_stencil_layout;
    bool has_depth_stencil_resolve_attachment;
    uint32_t depth_stencil_resolve_attachment;
    VkImageLayout depth_stencil_resolve_layout;
    VkResolveModeFlagBits depth_resolve_mode;
    VkResolveModeFlagBits stencil_resolve_mode;
    bool unsupported;
} PdockerVkSubpassState;

typedef struct {
    bool seen;
    VkPipelineStageFlags2 src_stage_mask;
    VkAccessFlags2 src_access_mask;
    VkPipelineStageFlags2 dst_stage_mask;
    VkAccessFlags2 dst_access_mask;
} PdockerVkSubpassDependencyState;

typedef struct {
    PdockerVkImageView *image_view;
    VkImageLayout image_layout;
    PdockerVkImageView *resolve_image_view;
    VkImageLayout resolve_image_layout;
    VkResolveModeFlagBits resolve_mode;
    VkAttachmentLoadOp load_op;
    VkAttachmentStoreOp store_op;
    VkClearValue clear_value;
    bool valid;
} PdockerVkRenderingAttachmentState;

struct PdockerVkRenderPass {
    uint32_t attachment_count;
    uint32_t subpass_count;
    PdockerVkRenderPassAttachmentState attachments[PDOCKER_VK_MAX_STORAGE_BUFFERS];
    PdockerVkSubpassState subpasses[PDOCKER_VK_MAX_STORAGE_BUFFERS];
    PdockerVkSubpassDependencyState begin_dependency;
    PdockerVkSubpassDependencyState end_dependency;
    bool attachment_overflow;
    bool subpass_overflow;
    uint64_t generation;
};

struct PdockerVkFramebuffer {
    PdockerVkRenderPass *render_pass;
    uint32_t attachment_count;
    PdockerVkImageView *attachments[PDOCKER_VK_MAX_STORAGE_BUFFERS];
    uint32_t width;
    uint32_t height;
    uint32_t layers;
    uint64_t generation;
};

typedef struct {
    PdockerVkBuffer *src;
    PdockerVkBuffer *dst;
    VkBufferCopy region;
} PdockerVkCopyOp;

typedef enum {
    PDOCKER_VK_IMAGE_COPY_BUFFER_TO_IMAGE = 1,
    PDOCKER_VK_IMAGE_COPY_IMAGE_TO_BUFFER = 2,
} PdockerVkImageCopyDirection;

typedef struct {
    PdockerVkImageCopyDirection direction;
    PdockerVkBuffer *buffer;
    PdockerVkImage *image;
    VkImageLayout image_layout;
    VkBufferImageCopy region;
} PdockerVkImageCopyOp;

typedef struct {
    PdockerVkImage *src;
    PdockerVkImage *dst;
    VkImageLayout src_layout;
    VkImageLayout dst_layout;
    VkImageCopy region;
} PdockerVkImageToImageCopyOp;

typedef struct {
    PdockerVkImage *image;
    VkImageLayout image_layout;
    VkClearColorValue color;
    VkImageSubresourceRange range;
} PdockerVkImageClearOp;

typedef struct {
    PdockerVkImage *src;
    PdockerVkImage *dst;
    VkImageLayout src_layout;
    VkImageLayout dst_layout;
    VkImageResolve region;
} PdockerVkImageResolveOp;

typedef struct {
    PdockerVkImage *src;
    PdockerVkImage *dst;
    VkImageLayout src_layout;
    VkImageLayout dst_layout;
    VkImageBlit region;
    VkFilter filter;
} PdockerVkImageBlitOp;

typedef struct {
    PdockerVkImage *image;
    VkImageLayout image_layout;
    VkClearDepthStencilValue value;
    VkImageSubresourceRange range;
} PdockerVkDepthStencilClearOp;

typedef struct {
    VkAccessFlags2 src_access_mask;
    VkAccessFlags2 dst_access_mask;
    VkPipelineStageFlags2 src_stage_mask;
    VkPipelineStageFlags2 dst_stage_mask;
} PdockerVkMemoryBarrierOp;

typedef struct {
    PdockerVkBuffer *buffer;
    VkDeviceSize offset;
    VkDeviceSize size;
    VkAccessFlags2 src_access_mask;
    VkAccessFlags2 dst_access_mask;
    VkPipelineStageFlags2 src_stage_mask;
    VkPipelineStageFlags2 dst_stage_mask;
    uint32_t src_queue_family_index;
    uint32_t dst_queue_family_index;
} PdockerVkBufferBarrierOp;

typedef struct {
    PdockerVkImage *image;
    VkImageLayout old_layout;
    VkImageLayout new_layout;
    VkImageSubresourceRange range;
    VkAccessFlags2 src_access_mask;
    VkAccessFlags2 dst_access_mask;
    VkPipelineStageFlags2 src_stage_mask;
    VkPipelineStageFlags2 dst_stage_mask;
    uint32_t src_queue_family_index;
    uint32_t dst_queue_family_index;
} PdockerVkImageBarrierOp;

typedef struct {
    PdockerVkPipeline *pipeline;
    PdockerVkDescriptorSet *set_handles[PDOCKER_VK_MAX_DESCRIPTOR_SETS];
    PdockerVkDescriptorSet set_snapshots[PDOCKER_VK_MAX_DESCRIPTOR_SETS];
    bool set_snapshot_used[PDOCKER_VK_MAX_DESCRIPTOR_SETS];
    uint32_t dispatch_x;
    uint32_t dispatch_y;
    uint32_t dispatch_z;
    uint32_t base_group_x;
    uint32_t base_group_y;
    uint32_t base_group_z;
    bool dispatch_indirect;
    PdockerVkBuffer *dispatch_indirect_buffer;
    VkDeviceSize dispatch_indirect_offset;
    uint8_t push_constants[PDOCKER_VK_MAX_PUSH_BYTES];
    uint32_t push_constant_size;
    PdockerVkPushConstantOpSnapshot push_constant_ops[PDOCKER_VK_MAX_PUSH_CONSTANT_OPS];
    uint32_t push_constant_op_count;
} PdockerVkDispatchOp;

typedef enum {
    PDOCKER_VK_COMMAND_COPY = 1,
    PDOCKER_VK_COMMAND_FILL = 2,
    PDOCKER_VK_COMMAND_UPDATE = 3,
    PDOCKER_VK_COMMAND_DISPATCH = 4,
    PDOCKER_VK_COMMAND_BARRIER = 5,
    PDOCKER_VK_COMMAND_IMAGE_COPY = 6,
    PDOCKER_VK_COMMAND_IMAGE_TO_IMAGE_COPY = 7,
    PDOCKER_VK_COMMAND_CLEAR_COLOR_IMAGE = 8,
    PDOCKER_VK_COMMAND_RESOLVE_IMAGE = 9,
    PDOCKER_VK_COMMAND_BLIT_IMAGE = 10,
    PDOCKER_VK_COMMAND_CLEAR_DEPTH_STENCIL_IMAGE = 11,
    PDOCKER_VK_COMMAND_EVENT = 12,
    PDOCKER_VK_COMMAND_QUERY_BEGIN = 13,
    PDOCKER_VK_COMMAND_QUERY_END = 14,
    PDOCKER_VK_COMMAND_QUERY_RESET = 15,
    PDOCKER_VK_COMMAND_QUERY_TIMESTAMP = 16,
    PDOCKER_VK_COMMAND_COPY_QUERY_RESULTS = 17,
    PDOCKER_VK_COMMAND_IMAGE_BARRIER = 18,
    PDOCKER_VK_COMMAND_EVENT_WAIT = 19,
    PDOCKER_VK_COMMAND_GRAPHICS_DRAW = 20,
} PdockerVkCommandOpType;

typedef struct {
    PdockerVkCommandOpType type;
    uint32_t index;
    PdockerVkBuffer *buffer;
    VkDeviceSize offset;
    VkDeviceSize size;
    uint32_t data;
    void *payload;
    PdockerVkEvent *event;
    bool event_signaled;
    PdockerVkQueryPool *query_pool;
    PdockerVkBuffer *query_dst_buffer;
    uint32_t query_index;
    uint32_t query_count;
    VkPipelineStageFlags2 query_stage_mask;
    VkDeviceSize query_dst_offset;
    VkDeviceSize query_stride;
    VkQueryResultFlags query_result_flags;
    uint32_t draw_vertex_count;
    uint32_t draw_instance_count;
    uint32_t draw_first_vertex;
    uint32_t draw_first_instance;
    uint32_t draw_first_index;
    uint32_t draw_index_count;
    int32_t draw_vertex_offset;
    PdockerVkBuffer *draw_indirect_buffer;
    VkDeviceSize draw_indirect_offset;
    uint32_t draw_indirect_stride;
    PdockerVkBuffer *draw_count_buffer;
    VkDeviceSize draw_count_offset;
    bool draw_indexed;
    bool draw_indirect;
} PdockerVkCommandOp;

typedef struct {
    PdockerVkBuffer *buffer;
    VkDeviceSize offset;
    VkDeviceSize size;
    VkDeviceSize stride;
    bool bound;
} PdockerVkVertexBindingState;

typedef struct {
    uint32_t state_type;
    uint32_t first_index;
    uint32_t count;
    uint8_t data[128];
    uint32_t data_size;
} PdockerVkDynamicStateSnapshot;

typedef struct {
    PdockerVkPipeline *pipeline;
    PdockerVkDescriptorSet set_snapshots[PDOCKER_VK_MAX_DESCRIPTOR_SETS];
    bool set_snapshot_used[PDOCKER_VK_MAX_DESCRIPTOR_SETS];
    uint8_t push_constants[PDOCKER_VK_MAX_PUSH_BYTES];
    uint32_t push_constant_size;
    PdockerVkPushConstantOpSnapshot push_constant_ops[PDOCKER_VK_MAX_PUSH_CONSTANT_OPS];
    uint32_t push_constant_op_count;
    PdockerVkVertexBindingState vertex_bindings[PDOCKER_VK_MAX_GRAPHICS_VERTEX_BINDINGS];
    uint32_t vertex_binding_count;
    bool vertex_buffer_bound;
    PdockerVkBuffer *index_buffer;
    VkDeviceSize index_offset;
    VkIndexType index_type;
    bool index_buffer_bound;
    uint32_t vertex_count;
    uint32_t instance_count;
    uint32_t first_vertex;
    uint32_t first_instance;
    uint32_t index_count;
    uint32_t first_index;
    int32_t vertex_offset;
    bool indexed;
    bool indirect;
    PdockerVkBuffer *indirect_buffer;
    VkDeviceSize indirect_offset;
    PdockerVkBuffer *count_buffer;
    VkDeviceSize count_offset;
    uint32_t indirect_stride;
    PdockerVkDynamicStateSnapshot dynamic_states[PDOCKER_VK_MAX_GRAPHICS_DYNAMIC_STATES];
    uint32_t dynamic_state_count;
    bool dynamic_rendering_active;
    bool render_pass_active;
    PdockerVkRenderPass *active_render_pass;
    PdockerVkFramebuffer *active_framebuffer;
    VkRect2D active_render_area;
    VkRenderingFlags active_rendering_flags;
    uint32_t active_rendering_layer_count;
    uint32_t active_rendering_view_mask;
    VkSubpassContents active_subpass_contents;
    uint32_t active_subpass;
    uint32_t active_color_attachment_count;
    PdockerVkRenderingAttachmentState active_color_attachments[PDOCKER_VK_MAX_STORAGE_BUFFERS];
    PdockerVkRenderingAttachmentState active_depth_attachment;
    PdockerVkRenderingAttachmentState active_stencil_attachment;
    VkClearValue active_clear_values[PDOCKER_VK_MAX_STORAGE_BUFFERS];
    uint32_t active_clear_value_count;
} PdockerVkGraphicsDrawSnapshot;

typedef struct {
    uint32_t first_set;
    uint32_t descriptor_set_count;
    uint32_t first_dynamic_offset;
    uint32_t dynamic_offset_count;
    PdockerVkDescriptorSet set_snapshots[PDOCKER_VK_MAX_DESCRIPTOR_SETS];
    bool set_snapshot_used[PDOCKER_VK_MAX_DESCRIPTOR_SETS];
} PdockerVkGraphicsDescriptorBindSnapshot;

typedef struct {
    VkRenderingFlags flags;
    VkRect2D render_area;
    uint32_t layer_count;
    uint32_t view_mask;
    uint32_t color_attachment_count;
    PdockerVkRenderingAttachmentState color_attachments[PDOCKER_VK_MAX_STORAGE_BUFFERS];
    PdockerVkRenderingAttachmentState depth_attachment;
    PdockerVkRenderingAttachmentState stencil_attachment;
} PdockerVkGraphicsRenderingSnapshot;

typedef struct {
    VkImageAspectFlags aspect_mask;
    uint32_t color_attachment;
    VkClearValue clear_value;
} PdockerVkClearAttachmentSnapshot;

typedef struct {
    VkRect2D rect;
    uint32_t base_array_layer;
    uint32_t layer_count;
} PdockerVkClearRectSnapshot;

typedef struct {
    uint32_t clear_attachment_first;
    uint32_t clear_attachment_count;
    uint32_t clear_rect_first;
    uint32_t clear_rect_count;
} PdockerVkClearAttachmentsCommandSnapshot;

typedef struct {
    uint32_t command_type;
    uint32_t flags;
    PdockerVkPipeline *pipeline;
    uint64_t layout_id;
    uint32_t first_set;
    uint32_t descriptor_set_count;
    uint32_t first_dynamic_offset;
    uint32_t dynamic_offset_count;
    uint32_t descriptor_bind_snapshot_index;
    uint32_t rendering_snapshot_index;
    uint32_t dynamic_state_index;
    uint32_t draw_snapshot_index;
    uint32_t push_op_index;
    uint32_t memory_barrier_op_first;
    uint32_t memory_barrier_op_count;
    uint32_t buffer_barrier_op_first;
    uint32_t buffer_barrier_op_count;
    uint32_t image_barrier_op_first;
    uint32_t image_barrier_op_count;
    uint32_t vertex_binding_first;
    uint32_t vertex_binding_count;
    uint64_t index_offset;
    uint32_t index_type;
    uint32_t command_op_sequence;
} PdockerVkGraphicsCommandRecord;

typedef struct {
    VK_LOADER_DATA loader;
    PdockerVkPipeline *pipeline;
    PdockerVkPipeline *compute_pipeline;
    PdockerVkPipeline *graphics_pipeline;
    PdockerVkDescriptorSet *bound_set_handles[PDOCKER_VK_MAX_DESCRIPTOR_SETS];
    PdockerVkDescriptorSet bound_set_snapshots[PDOCKER_VK_MAX_DESCRIPTOR_SETS];
    bool bound_set_used[PDOCKER_VK_MAX_DESCRIPTOR_SETS];
    PdockerVkDescriptorSet *graphics_bound_set_handles[PDOCKER_VK_MAX_DESCRIPTOR_SETS];
    PdockerVkDescriptorSet graphics_bound_set_snapshots[PDOCKER_VK_MAX_DESCRIPTOR_SETS];
    bool graphics_bound_set_used[PDOCKER_VK_MAX_DESCRIPTOR_SETS];
    PdockerVkCopyOp copy_ops[PDOCKER_VK_MAX_COPY_OPS];
    uint32_t copy_op_count;
    PdockerVkImageCopyOp image_copy_ops[PDOCKER_VK_MAX_COPY_OPS];
    uint32_t image_copy_op_count;
    PdockerVkImageToImageCopyOp image_to_image_copy_ops[PDOCKER_VK_MAX_COPY_OPS];
    uint32_t image_to_image_copy_op_count;
    PdockerVkImageClearOp image_clear_ops[PDOCKER_VK_MAX_COPY_OPS];
    uint32_t image_clear_op_count;
    PdockerVkImageResolveOp image_resolve_ops[PDOCKER_VK_MAX_COPY_OPS];
    uint32_t image_resolve_op_count;
    PdockerVkImageBlitOp image_blit_ops[PDOCKER_VK_MAX_COPY_OPS];
    uint32_t image_blit_op_count;
    PdockerVkDepthStencilClearOp depth_stencil_clear_ops[PDOCKER_VK_MAX_COPY_OPS];
    uint32_t depth_stencil_clear_op_count;
    PdockerVkMemoryBarrierOp memory_barrier_ops[PDOCKER_VK_MAX_COPY_OPS];
    uint32_t memory_barrier_op_count;
    PdockerVkBufferBarrierOp buffer_barrier_ops[PDOCKER_VK_MAX_COPY_OPS];
    uint32_t buffer_barrier_op_count;
    PdockerVkImageBarrierOp image_barrier_ops[PDOCKER_VK_MAX_COPY_OPS];
    uint32_t image_barrier_op_count;
    PdockerVkDispatchOp dispatch_ops[PDOCKER_VK_MAX_DISPATCH_OPS];
    uint32_t dispatch_op_count;
    PdockerVkCommandOp command_ops[PDOCKER_VK_MAX_COMMAND_OPS];
    uint32_t command_op_count;
    PdockerVkGraphicsDrawSnapshot graphics_draw_ops[PDOCKER_VK_MAX_GRAPHICS_DRAW_OPS];
    uint32_t graphics_draw_op_count;
    PdockerVkGraphicsDescriptorBindSnapshot
        graphics_descriptor_bind_ops[PDOCKER_VK_MAX_GRAPHICS_DESCRIPTOR_BIND_OPS];
    uint32_t graphics_descriptor_bind_op_count;
    PdockerVkGraphicsRenderingSnapshot
        graphics_rendering_ops[PDOCKER_VK_MAX_GRAPHICS_RENDERING_OPS];
    uint32_t graphics_rendering_op_count;
    PdockerVkClearAttachmentsCommandSnapshot
        clear_attachments_command_ops[PDOCKER_VK_MAX_CLEAR_ATTACHMENTS_COMMANDS];
    uint32_t clear_attachments_command_op_count;
    PdockerVkClearAttachmentSnapshot clear_attachment_ops[PDOCKER_VK_MAX_CLEAR_ATTACHMENTS];
    uint32_t clear_attachment_op_count;
    PdockerVkClearRectSnapshot clear_rect_ops[PDOCKER_VK_MAX_CLEAR_RECTS];
    uint32_t clear_rect_op_count;
    PdockerVkGraphicsCommandRecord graphics_command_ops[PDOCKER_VK_MAX_GRAPHICS_COMMAND_OPS];
    uint32_t graphics_command_op_count;
    uint32_t graphics_dynamic_offsets[PDOCKER_VK_MAX_GRAPHICS_DYNAMIC_OFFSETS];
    uint32_t graphics_dynamic_offset_count;
    uint32_t dispatch_x;
    uint32_t dispatch_y;
    uint32_t dispatch_z;
    uint8_t push_constants[PDOCKER_VK_MAX_PUSH_BYTES];
    uint32_t push_constant_size;
    PdockerVkPushConstantOpSnapshot push_constant_ops[PDOCKER_VK_MAX_PUSH_CONSTANT_OPS];
    uint32_t push_constant_op_count;
    bool has_dispatch;
    bool unsupported_descriptor_set_layout;
    bool dynamic_rendering_active;
    bool inherited_rendering_active;
    bool render_pass_active;
    PdockerVkRenderPass *active_render_pass;
    PdockerVkFramebuffer *active_framebuffer;
    VkRect2D active_render_area;
    VkRenderingFlags active_rendering_flags;
    uint32_t active_rendering_layer_count;
    uint32_t active_rendering_view_mask;
    VkSubpassContents active_subpass_contents;
    uint32_t active_subpass;
    uint32_t active_color_attachment_count;
    PdockerVkRenderingAttachmentState active_color_attachments[PDOCKER_VK_MAX_STORAGE_BUFFERS];
    PdockerVkRenderingAttachmentState active_depth_attachment;
    PdockerVkRenderingAttachmentState active_stencil_attachment;
    VkClearValue active_clear_values[PDOCKER_VK_MAX_STORAGE_BUFFERS];
    uint32_t active_clear_value_count;
    bool graphics_unsupported;
    VkCommandBufferLevel level;
    PdockerVkVertexBindingState vertex_bindings[PDOCKER_VK_MAX_GRAPHICS_VERTEX_BINDINGS];
    uint32_t vertex_binding_count;
    PdockerVkBuffer *index_buffer;
    VkDeviceSize index_offset;
    VkIndexType index_type;
    bool index_buffer_bound;
    PdockerVkDynamicStateSnapshot dynamic_states[PDOCKER_VK_MAX_GRAPHICS_DYNAMIC_STATES];
    uint32_t dynamic_state_count;
    bool vertex_buffer_bound;
} PdockerVkCommandBuffer;

typedef struct {
    int unused;
} PdockerHandle;

static PdockerVkPhysicalDevice g_device;
static PdockerVkQueue g_queue;
static unsigned g_shader_dump_counter;
static PdockerVkMemory *g_guarded_memories[PDOCKER_VK_MAX_GUARDED_MEMORIES];
static struct sigaction g_previous_sigsegv;
static bool g_guarded_sigsegv_installed;
static uint64_t g_vulkan_object_generation;
static uint64_t g_vulkan_query_pool_generation;

static bool trace_allocations(void);
static bool query_range_valid(
        const PdockerVkQueryPool *pool,
        uint32_t firstQuery,
        uint32_t queryCount);
static void execute_recorded_query_op(PdockerVkCommandOp *op);
static void trace_image_layout_mismatch(
        const char *stage,
        const PdockerVkImage *image,
        VkImageLayout requested_layout);

static bool env_enabled(const char *name) {
    const char *value = getenv(name);
    return value && value[0] && strcmp(value, "0") != 0 &&
           strcasecmp(value, "false") != 0 && strcasecmp(value, "no") != 0;
}

static bool env_disabled(const char *name) {
    return env_enabled(name);
}

static bool env_truthy_default(const char *name, bool default_value) {
    const char *value = getenv(name);
    if (!value || !value[0]) return default_value;
    if (strcmp(value, "0") == 0 || strcasecmp(value, "false") == 0 ||
        strcasecmp(value, "no") == 0) {
        return false;
    }
    return true;
}

static bool vulkan_v5_object_transport_enabled(void) {
    return env_truthy_default("PDOCKER_VULKAN_ENABLE_V5_OBJECT_TRANSPORT", false);
}

static uint64_t next_vulkan_object_generation(void) {
    return __sync_add_and_fetch(&g_vulkan_object_generation, 1);
}

static uint64_t next_vulkan_query_pool_id(void) {
    return __sync_add_and_fetch(&g_vulkan_query_pool_generation, 1);
}

static uint32_t clamp_u32(uint32_t value, uint32_t limit) {
    return value > limit ? limit : value;
}

static void safe_copy_cstr(char *dst, size_t dst_size, const char *src) {
    if (!dst || dst_size == 0) return;
    if (!src) {
        dst[0] = '\0';
        return;
    }
    snprintf(dst, dst_size, "%s", src);
}

static bool append_graphics_command_record(
        PdockerVkCommandBuffer *cmd,
        const PdockerVkGraphicsCommandRecord *record) {
    if (!cmd || !record) return false;
    if (cmd->graphics_command_op_count >= PDOCKER_VK_MAX_GRAPHICS_COMMAND_OPS) {
        cmd->graphics_unsupported = true;
        return false;
    }
    PdockerVkGraphicsCommandRecord stored = *record;
    stored.command_op_sequence = cmd->command_op_count;
    cmd->graphics_command_ops[cmd->graphics_command_op_count++] = stored;
    return true;
}

static void record_graphics_dynamic_state_bytes(
        PdockerVkCommandBuffer *cmd,
        VkDynamicState state_type,
        uint32_t first_index,
        uint32_t count,
        const void *data,
        size_t data_size) {
    if (!cmd) return;
    if (cmd->dynamic_state_count >= PDOCKER_VK_MAX_GRAPHICS_DYNAMIC_STATES ||
        data_size > sizeof(cmd->dynamic_states[0].data)) {
        cmd->graphics_unsupported = true;
        return;
    }
    PdockerVkDynamicStateSnapshot *state = &cmd->dynamic_states[cmd->dynamic_state_count++];
    memset(state, 0, sizeof(*state));
    state->state_type = (uint32_t)state_type;
    state->first_index = first_index;
    state->count = count;
    state->data_size = (uint32_t)data_size;
    if (data && data_size) memcpy(state->data, data, data_size);
    PdockerVkGraphicsCommandRecord record;
    memset(&record, 0, sizeof(record));
    record.command_type = PDOCKER_GPU_GRAPHICS_V6_COMMAND_SET_DYNAMIC_STATE;
    record.dynamic_state_index = cmd->dynamic_state_count - 1u;
    (void)append_graphics_command_record(cmd, &record);
}

static bool copy_rendering_attachment_state(
        PdockerVkRenderingAttachmentState *dst,
        const VkRenderingAttachmentInfo *src) {
    if (!dst) return false;
    memset(dst, 0, sizeof(*dst));
    if (!src) return true;
    if (src->pNext) return false;
    dst->image_view = (PdockerVkImageView *)src->imageView;
    dst->image_layout = src->imageLayout;
    dst->resolve_image_view = (PdockerVkImageView *)src->resolveImageView;
    dst->resolve_image_layout = src->resolveImageLayout;
    dst->resolve_mode = src->resolveMode;
    dst->load_op = src->loadOp;
    dst->store_op = src->storeOp;
    dst->clear_value = src->clearValue;
    dst->valid = true;
    return true;
}

static bool append_graphics_rendering_snapshot(PdockerVkCommandBuffer *cmd,
                                               uint32_t *snapshot_index_out) {
    if (snapshot_index_out) *snapshot_index_out = UINT32_MAX;
    if (!cmd || !snapshot_index_out) return false;
    if (cmd->graphics_rendering_op_count >= PDOCKER_VK_MAX_GRAPHICS_RENDERING_OPS) {
        cmd->graphics_unsupported = true;
        return false;
    }
    uint32_t index = cmd->graphics_rendering_op_count++;
    PdockerVkGraphicsRenderingSnapshot *snapshot = &cmd->graphics_rendering_ops[index];
    memset(snapshot, 0, sizeof(*snapshot));
    snapshot->flags = cmd->active_rendering_flags;
    snapshot->render_area = cmd->active_render_area;
    snapshot->layer_count = cmd->active_rendering_layer_count;
    snapshot->view_mask = cmd->active_rendering_view_mask;
    snapshot->color_attachment_count = cmd->active_color_attachment_count;
    memcpy(snapshot->color_attachments, cmd->active_color_attachments,
           sizeof(snapshot->color_attachments));
    snapshot->depth_attachment = cmd->active_depth_attachment;
    snapshot->stencil_attachment = cmd->active_stencil_attachment;
    *snapshot_index_out = index;
    return true;
}

static void clear_recorded_command_ops(PdockerVkCommandBuffer *cmd) {
    if (!cmd) return;
    for (uint32_t i = 0; i < cmd->command_op_count; ++i) {
        if (cmd->command_ops[i].type == PDOCKER_VK_COMMAND_UPDATE) {
            free(cmd->command_ops[i].payload);
            cmd->command_ops[i].payload = NULL;
        }
    }
    cmd->command_op_count = 0;
    cmd->copy_op_count = 0;
    cmd->image_copy_op_count = 0;
    cmd->image_to_image_copy_op_count = 0;
    cmd->image_clear_op_count = 0;
    cmd->image_resolve_op_count = 0;
    cmd->image_blit_op_count = 0;
    cmd->depth_stencil_clear_op_count = 0;
    cmd->memory_barrier_op_count = 0;
    cmd->buffer_barrier_op_count = 0;
    cmd->image_barrier_op_count = 0;
    cmd->dispatch_op_count = 0;
    cmd->graphics_draw_op_count = 0;
    cmd->graphics_descriptor_bind_op_count = 0;
    cmd->graphics_rendering_op_count = 0;
    cmd->clear_attachments_command_op_count = 0;
    cmd->clear_attachment_op_count = 0;
    cmd->clear_rect_op_count = 0;
    cmd->graphics_command_op_count = 0;
    cmd->graphics_dynamic_offset_count = 0;
    cmd->push_constant_op_count = 0;
}

static bool append_command_op(PdockerVkCommandBuffer *cmd, const PdockerVkCommandOp *op) {
    if (!cmd || !op) return false;
    if (cmd->command_op_count >= PDOCKER_VK_MAX_COMMAND_OPS) {
        if (trace_allocations() || getenv("PDOCKER_VULKAN_ICD_DEBUG")) {
            fprintf(stderr,
                    "pdocker-vulkan-icd: command buffer op list full max=%u type=%u\n",
                    PDOCKER_VK_MAX_COMMAND_OPS,
                    (unsigned)op->type);
        }
        return false;
    }
    cmd->command_ops[cmd->command_op_count++] = *op;
    return true;
}

static bool command_buffer_has_room_for_secondary(
        const PdockerVkCommandBuffer *dst,
        const PdockerVkCommandBuffer *src) {
    return dst && src &&
        src->copy_op_count <= PDOCKER_VK_MAX_COPY_OPS - dst->copy_op_count &&
        src->image_copy_op_count <= PDOCKER_VK_MAX_COPY_OPS - dst->image_copy_op_count &&
        src->image_to_image_copy_op_count <= PDOCKER_VK_MAX_COPY_OPS - dst->image_to_image_copy_op_count &&
        src->image_clear_op_count <= PDOCKER_VK_MAX_COPY_OPS - dst->image_clear_op_count &&
        src->image_resolve_op_count <= PDOCKER_VK_MAX_COPY_OPS - dst->image_resolve_op_count &&
        src->image_blit_op_count <= PDOCKER_VK_MAX_COPY_OPS - dst->image_blit_op_count &&
        src->depth_stencil_clear_op_count <= PDOCKER_VK_MAX_COPY_OPS - dst->depth_stencil_clear_op_count &&
        src->memory_barrier_op_count <= PDOCKER_VK_MAX_COPY_OPS - dst->memory_barrier_op_count &&
        src->buffer_barrier_op_count <= PDOCKER_VK_MAX_COPY_OPS - dst->buffer_barrier_op_count &&
        src->image_barrier_op_count <= PDOCKER_VK_MAX_COPY_OPS - dst->image_barrier_op_count &&
        src->dispatch_op_count <= PDOCKER_VK_MAX_DISPATCH_OPS - dst->dispatch_op_count &&
        src->command_op_count <= PDOCKER_VK_MAX_COMMAND_OPS - dst->command_op_count &&
        src->graphics_draw_op_count <= PDOCKER_VK_MAX_GRAPHICS_DRAW_OPS - dst->graphics_draw_op_count &&
        src->graphics_descriptor_bind_op_count <= PDOCKER_VK_MAX_GRAPHICS_DESCRIPTOR_BIND_OPS - dst->graphics_descriptor_bind_op_count &&
        src->graphics_rendering_op_count <= PDOCKER_VK_MAX_GRAPHICS_RENDERING_OPS - dst->graphics_rendering_op_count &&
        src->clear_attachments_command_op_count <= PDOCKER_VK_MAX_CLEAR_ATTACHMENTS_COMMANDS - dst->clear_attachments_command_op_count &&
        src->clear_attachment_op_count <= PDOCKER_VK_MAX_CLEAR_ATTACHMENTS - dst->clear_attachment_op_count &&
        src->clear_rect_op_count <= PDOCKER_VK_MAX_CLEAR_RECTS - dst->clear_rect_op_count &&
        src->graphics_command_op_count <= PDOCKER_VK_MAX_GRAPHICS_COMMAND_OPS - dst->graphics_command_op_count &&
        src->graphics_dynamic_offset_count <= PDOCKER_VK_MAX_GRAPHICS_DYNAMIC_OFFSETS - dst->graphics_dynamic_offset_count &&
        src->dynamic_state_count <= PDOCKER_VK_MAX_GRAPHICS_DYNAMIC_STATES - dst->dynamic_state_count &&
        src->push_constant_op_count <= PDOCKER_VK_MAX_PUSH_CONSTANT_OPS - dst->push_constant_op_count;
}

static bool append_secondary_command_buffer(
        PdockerVkCommandBuffer *dst,
        const PdockerVkCommandBuffer *src) {
    if (!command_buffer_has_room_for_secondary(dst, src)) return false;
    if (src->graphics_unsupported || src->unsupported_descriptor_set_layout ||
        src->dynamic_rendering_active || src->render_pass_active) {
        return false;
    }

    uint32_t command_op_base = dst->command_op_count;
    uint32_t copy_base = dst->copy_op_count;
    uint32_t image_copy_base = dst->image_copy_op_count;
    uint32_t image_to_image_copy_base = dst->image_to_image_copy_op_count;
    uint32_t image_clear_base = dst->image_clear_op_count;
    uint32_t image_resolve_base = dst->image_resolve_op_count;
    uint32_t image_blit_base = dst->image_blit_op_count;
    uint32_t depth_stencil_clear_base = dst->depth_stencil_clear_op_count;
    uint32_t memory_barrier_base = dst->memory_barrier_op_count;
    uint32_t buffer_barrier_base = dst->buffer_barrier_op_count;
    uint32_t image_barrier_base = dst->image_barrier_op_count;
    uint32_t dispatch_base = dst->dispatch_op_count;
    uint32_t graphics_draw_base = dst->graphics_draw_op_count;
    uint32_t descriptor_bind_base = dst->graphics_descriptor_bind_op_count;
    uint32_t rendering_base = dst->graphics_rendering_op_count;
    uint32_t clear_attachments_command_base = dst->clear_attachments_command_op_count;
    uint32_t clear_attachment_base = dst->clear_attachment_op_count;
    uint32_t clear_rect_base = dst->clear_rect_op_count;
    uint32_t dynamic_state_base = dst->dynamic_state_count;
    uint32_t dynamic_offset_base = dst->graphics_dynamic_offset_count;
    uint32_t push_op_base = dst->push_constant_op_count;

    void *update_payloads[PDOCKER_VK_MAX_COMMAND_OPS];
    memset(update_payloads, 0, sizeof(update_payloads));
    for (uint32_t i = 0; i < src->command_op_count; ++i) {
        const PdockerVkCommandOp *op = &src->command_ops[i];
        if (op->type != PDOCKER_VK_COMMAND_UPDATE || op->size == 0 || !op->payload) continue;
        if (op->size > (VkDeviceSize)SIZE_MAX) {
            for (uint32_t j = 0; j < i; ++j) free(update_payloads[j]);
            return false;
        }
        update_payloads[i] = malloc((size_t)op->size);
        if (!update_payloads[i]) {
            for (uint32_t j = 0; j < i; ++j) free(update_payloads[j]);
            return false;
        }
        memcpy(update_payloads[i], op->payload, (size_t)op->size);
    }

    memcpy(dst->copy_ops + dst->copy_op_count, src->copy_ops,
           sizeof(src->copy_ops[0]) * src->copy_op_count);
    dst->copy_op_count += src->copy_op_count;
    memcpy(dst->image_copy_ops + dst->image_copy_op_count, src->image_copy_ops,
           sizeof(src->image_copy_ops[0]) * src->image_copy_op_count);
    dst->image_copy_op_count += src->image_copy_op_count;
    memcpy(dst->image_to_image_copy_ops + dst->image_to_image_copy_op_count, src->image_to_image_copy_ops,
           sizeof(src->image_to_image_copy_ops[0]) * src->image_to_image_copy_op_count);
    dst->image_to_image_copy_op_count += src->image_to_image_copy_op_count;
    memcpy(dst->image_clear_ops + dst->image_clear_op_count, src->image_clear_ops,
           sizeof(src->image_clear_ops[0]) * src->image_clear_op_count);
    dst->image_clear_op_count += src->image_clear_op_count;
    memcpy(dst->image_resolve_ops + dst->image_resolve_op_count, src->image_resolve_ops,
           sizeof(src->image_resolve_ops[0]) * src->image_resolve_op_count);
    dst->image_resolve_op_count += src->image_resolve_op_count;
    memcpy(dst->image_blit_ops + dst->image_blit_op_count, src->image_blit_ops,
           sizeof(src->image_blit_ops[0]) * src->image_blit_op_count);
    dst->image_blit_op_count += src->image_blit_op_count;
    memcpy(dst->depth_stencil_clear_ops + dst->depth_stencil_clear_op_count, src->depth_stencil_clear_ops,
           sizeof(src->depth_stencil_clear_ops[0]) * src->depth_stencil_clear_op_count);
    dst->depth_stencil_clear_op_count += src->depth_stencil_clear_op_count;
    memcpy(dst->memory_barrier_ops + dst->memory_barrier_op_count, src->memory_barrier_ops,
           sizeof(src->memory_barrier_ops[0]) * src->memory_barrier_op_count);
    dst->memory_barrier_op_count += src->memory_barrier_op_count;
    memcpy(dst->buffer_barrier_ops + dst->buffer_barrier_op_count, src->buffer_barrier_ops,
           sizeof(src->buffer_barrier_ops[0]) * src->buffer_barrier_op_count);
    dst->buffer_barrier_op_count += src->buffer_barrier_op_count;
    memcpy(dst->image_barrier_ops + dst->image_barrier_op_count, src->image_barrier_ops,
           sizeof(src->image_barrier_ops[0]) * src->image_barrier_op_count);
    dst->image_barrier_op_count += src->image_barrier_op_count;
    memcpy(dst->dispatch_ops + dst->dispatch_op_count, src->dispatch_ops,
           sizeof(src->dispatch_ops[0]) * src->dispatch_op_count);
    dst->dispatch_op_count += src->dispatch_op_count;
    memcpy(dst->graphics_draw_ops + dst->graphics_draw_op_count, src->graphics_draw_ops,
           sizeof(src->graphics_draw_ops[0]) * src->graphics_draw_op_count);
    dst->graphics_draw_op_count += src->graphics_draw_op_count;
    memcpy(dst->graphics_descriptor_bind_ops + dst->graphics_descriptor_bind_op_count,
           src->graphics_descriptor_bind_ops,
           sizeof(src->graphics_descriptor_bind_ops[0]) * src->graphics_descriptor_bind_op_count);
    dst->graphics_descriptor_bind_op_count += src->graphics_descriptor_bind_op_count;
    memcpy(dst->graphics_rendering_ops + dst->graphics_rendering_op_count, src->graphics_rendering_ops,
           sizeof(src->graphics_rendering_ops[0]) * src->graphics_rendering_op_count);
    dst->graphics_rendering_op_count += src->graphics_rendering_op_count;
    memcpy(dst->clear_attachments_command_ops + dst->clear_attachments_command_op_count,
           src->clear_attachments_command_ops,
           sizeof(src->clear_attachments_command_ops[0]) * src->clear_attachments_command_op_count);
    for (uint32_t i = 0; i < src->clear_attachments_command_op_count; ++i) {
        PdockerVkClearAttachmentsCommandSnapshot *snapshot =
            &dst->clear_attachments_command_ops[dst->clear_attachments_command_op_count + i];
        snapshot->clear_attachment_first += clear_attachment_base;
        snapshot->clear_rect_first += clear_rect_base;
    }
    dst->clear_attachments_command_op_count += src->clear_attachments_command_op_count;
    memcpy(dst->clear_attachment_ops + dst->clear_attachment_op_count, src->clear_attachment_ops,
           sizeof(src->clear_attachment_ops[0]) * src->clear_attachment_op_count);
    dst->clear_attachment_op_count += src->clear_attachment_op_count;
    memcpy(dst->clear_rect_ops + dst->clear_rect_op_count, src->clear_rect_ops,
           sizeof(src->clear_rect_ops[0]) * src->clear_rect_op_count);
    dst->clear_rect_op_count += src->clear_rect_op_count;
    memcpy(dst->dynamic_states + dst->dynamic_state_count, src->dynamic_states,
           sizeof(src->dynamic_states[0]) * src->dynamic_state_count);
    dst->dynamic_state_count += src->dynamic_state_count;
    memcpy(dst->graphics_dynamic_offsets + dst->graphics_dynamic_offset_count, src->graphics_dynamic_offsets,
           sizeof(src->graphics_dynamic_offsets[0]) * src->graphics_dynamic_offset_count);
    dst->graphics_dynamic_offset_count += src->graphics_dynamic_offset_count;
    memcpy(dst->push_constant_ops + dst->push_constant_op_count, src->push_constant_ops,
           sizeof(src->push_constant_ops[0]) * src->push_constant_op_count);
    dst->push_constant_op_count += src->push_constant_op_count;

    for (uint32_t i = 0; i < src->graphics_command_op_count; ++i) {
        PdockerVkGraphicsCommandRecord record = src->graphics_command_ops[i];
        record.command_op_sequence += command_op_base;
        switch (record.command_type) {
            case PDOCKER_GPU_GRAPHICS_V6_COMMAND_BEGIN_RENDERING:
                record.rendering_snapshot_index += rendering_base;
                break;
            case PDOCKER_GPU_GRAPHICS_V6_COMMAND_CLEAR_ATTACHMENTS:
                record.descriptor_bind_snapshot_index += clear_attachments_command_base;
                record.first_set += clear_attachment_base;
                record.first_dynamic_offset += clear_rect_base;
                break;
            case PDOCKER_GPU_GRAPHICS_V6_COMMAND_BIND_DESCRIPTOR_SETS:
                record.descriptor_bind_snapshot_index += descriptor_bind_base;
                record.first_dynamic_offset += dynamic_offset_base;
                break;
            case PDOCKER_GPU_GRAPHICS_V6_COMMAND_SET_DYNAMIC_STATE:
                record.dynamic_state_index += dynamic_state_base;
                break;
            case PDOCKER_GPU_GRAPHICS_V6_COMMAND_PUSH_CONSTANTS:
                record.push_op_index += push_op_base;
                break;
            case PDOCKER_GPU_GRAPHICS_V6_COMMAND_BARRIER:
                record.memory_barrier_op_first += memory_barrier_base;
                record.buffer_barrier_op_first += buffer_barrier_base;
                record.image_barrier_op_first += image_barrier_base;
                break;
            case PDOCKER_GPU_GRAPHICS_V6_COMMAND_DRAW:
            case PDOCKER_GPU_GRAPHICS_V6_COMMAND_DRAW_INDEXED:
                record.draw_snapshot_index += graphics_draw_base;
                break;
            default:
                break;
        }
        dst->graphics_command_ops[dst->graphics_command_op_count++] = record;
    }

    for (uint32_t i = 0; i < src->command_op_count; ++i) {
        PdockerVkCommandOp op = src->command_ops[i];
        switch (op.type) {
            case PDOCKER_VK_COMMAND_COPY:
                op.index += copy_base;
                break;
            case PDOCKER_VK_COMMAND_DISPATCH:
                op.index += dispatch_base;
                break;
            case PDOCKER_VK_COMMAND_IMAGE_COPY:
                op.index += image_copy_base;
                break;
            case PDOCKER_VK_COMMAND_IMAGE_TO_IMAGE_COPY:
                op.index += image_to_image_copy_base;
                break;
            case PDOCKER_VK_COMMAND_CLEAR_COLOR_IMAGE:
                op.index += image_clear_base;
                break;
            case PDOCKER_VK_COMMAND_RESOLVE_IMAGE:
                op.index += image_resolve_base;
                break;
            case PDOCKER_VK_COMMAND_BLIT_IMAGE:
                op.index += image_blit_base;
                break;
            case PDOCKER_VK_COMMAND_CLEAR_DEPTH_STENCIL_IMAGE:
                op.index += depth_stencil_clear_base;
                break;
            case PDOCKER_VK_COMMAND_IMAGE_BARRIER:
                op.index += image_barrier_base;
                break;
            case PDOCKER_VK_COMMAND_GRAPHICS_DRAW:
                op.index += graphics_draw_base;
                break;
            case PDOCKER_VK_COMMAND_UPDATE:
                op.payload = update_payloads[i];
                update_payloads[i] = NULL;
                break;
            default:
                break;
        }
        dst->command_ops[dst->command_op_count++] = op;
    }

    memcpy(dst->vertex_bindings, src->vertex_bindings, sizeof(dst->vertex_bindings));
    dst->vertex_binding_count = src->vertex_binding_count;
    dst->vertex_buffer_bound = src->vertex_buffer_bound;
    if (src->index_buffer_bound) {
        dst->index_buffer = src->index_buffer;
        dst->index_offset = src->index_offset;
        dst->index_type = src->index_type;
        dst->index_buffer_bound = true;
    }
    if (src->push_constant_size > 0) {
        memcpy(dst->push_constants, src->push_constants, sizeof(dst->push_constants));
        dst->push_constant_size = src->push_constant_size;
    }
    if (src->has_dispatch) {
        dst->dispatch_x = src->dispatch_x;
        dst->dispatch_y = src->dispatch_y;
        dst->dispatch_z = src->dispatch_z;
        dst->has_dispatch = true;
    }
    if (src->pipeline) dst->pipeline = src->pipeline;
    if (src->compute_pipeline) dst->compute_pipeline = src->compute_pipeline;
    if (src->graphics_pipeline) dst->graphics_pipeline = src->graphics_pipeline;
    return true;
}

static void maybe_dump_spirv(const VkShaderModuleCreateInfo *info) {
    const char *dir = getenv("PDOCKER_VULKAN_DUMP_SPIRV_DIR");
    if (!dir || !dir[0] || !info || !info->pCode || info->codeSize == 0) return;
    char path[512];
    uint32_t first = info->codeSize >= sizeof(uint32_t) ? info->pCode[0] : 0;
    int n = snprintf(path, sizeof(path), "%s/pdocker-spirv-%06u-%zu-%08x.spv",
                     dir, ++g_shader_dump_counter, info->codeSize, first);
    if (n < 0 || (size_t)n >= sizeof(path)) return;
    FILE *f = fopen(path, "wb");
    if (!f) return;
    fwrite(info->pCode, 1, info->codeSize, f);
    fclose(f);
    if (getenv("PDOCKER_VULKAN_ICD_TRACE_ALLOC")) {
        fprintf(stderr, "pdocker-vulkan-icd: dumped SPIR-V %s\n", path);
    }
}

static VkBool32 executor_advertised_shader_int64_or(VkBool32 legacy);
static VkBool32 executor_advertised_storage16_or(VkBool32 legacy);
static VkBool32 executor_advertised_storage8_or(VkBool32 legacy);

static VkBool32 advertised_shader_int64(void) {
    /*
     * llama.cpp/ggml can select different shader variants from advertised
     * features. Until the executor proves the Android device supports the same
     * SPIR-V path, keep expensive/fragile features opt-in instead of optimistic.
     */
    VkBool32 legacy = env_truthy_default("PDOCKER_VULKAN_ENABLE_INT64", false) ? VK_TRUE : VK_FALSE;
    return executor_advertised_shader_int64_or(legacy);
}

static VkBool32 advertised_storage16(void) {
    /*
     * llama.cpp currently expects 16-bit storage to load useful Vulkan paths on
     * this device. Keep it enabled by default, but allow tuning runs to clamp it
     * off when investigating Android executor capability mismatches.
     */
    if (env_disabled("PDOCKER_VULKAN_DISABLE_16BIT_STORAGE")) return VK_FALSE;
    VkBool32 legacy = env_truthy_default("PDOCKER_VULKAN_ENABLE_16BIT_STORAGE", true) ? VK_TRUE : VK_FALSE;
    return executor_advertised_storage16_or(legacy);
}

static VkBool32 advertised_storage8(void) {
    /*
     * ggml Vulkan can emit int8/8-bit-storage kernels for quantized weights.
     * Android devices that report both storageBuffer8BitAccess and shaderInt8
     * need the container-visible ICD to advertise both bits together; otherwise
     * llama.cpp can still hand the bridge an Int8/Storage8 SPIR-V module while
     * the executor device was created without those requested features.
     */
    if (env_disabled("PDOCKER_VULKAN_DISABLE_8BIT_STORAGE")) return VK_FALSE;
    VkBool32 legacy = env_truthy_default("PDOCKER_VULKAN_ENABLE_8BIT_STORAGE", true) ? VK_TRUE : VK_FALSE;
    return executor_advertised_storage8_or(legacy);
}

static VkDeviceSize pdocker_vulkan_heap_size(void) {
    const char *env = getenv("PDOCKER_VULKAN_HEAP_BYTES");
    if (env && env[0]) {
        char *end = NULL;
        unsigned long long value = strtoull(env, &end, 10);
        if (end && *end == '\0' && value >= 256ull * 1024ull * 1024ull) {
            return (VkDeviceSize)value;
        }
    }
    unsigned long long mem_available = 0;
    FILE *f = fopen("/proc/meminfo", "r");
    if (f) {
        char line[160];
        while (fgets(line, sizeof(line), f)) {
            unsigned long long kb = 0;
            if (sscanf(line, "MemAvailable: %llu kB", &kb) == 1) {
                mem_available = kb * 1024ull;
                break;
            }
        }
        fclose(f);
    }
    const VkDeviceSize min_heap = (VkDeviceSize)(512ull * 1024ull * 1024ull);
    const VkDeviceSize max_heap = (VkDeviceSize)(2ull * 1024ull * 1024ull * 1024ull);
    if (mem_available >= 1024ull * 1024ull * 1024ull) {
        VkDeviceSize dynamic_heap = (VkDeviceSize)(mem_available / 4ull);
        if (dynamic_heap < min_heap) dynamic_heap = min_heap;
        if (dynamic_heap > max_heap) dynamic_heap = max_heap;
        return dynamic_heap;
    }
    return min_heap;
}

static VkDeviceSize align_device_size(VkDeviceSize value, VkDeviceSize alignment) {
    if (alignment == 0) return value;
    VkDeviceSize rem = value % alignment;
    if (rem == 0) return value;
    return value + alignment - rem;
}

static bool checked_mul_u64(uint64_t a, uint64_t b, uint64_t *out) {
    if (!out) return false;
    if (a != 0 && b > UINT64_MAX / a) return false;
    *out = a * b;
    return true;
}

static uint32_t conservative_format_bytes_per_pixel(VkFormat format) {
    switch (format) {
        case VK_FORMAT_R8_UNORM:
        case VK_FORMAT_R8_SNORM:
        case VK_FORMAT_R8_UINT:
        case VK_FORMAT_R8_SINT:
        case VK_FORMAT_S8_UINT:
            return 1;
        case VK_FORMAT_R8G8_UNORM:
        case VK_FORMAT_R8G8_SNORM:
        case VK_FORMAT_R8G8_UINT:
        case VK_FORMAT_R8G8_SINT:
        case VK_FORMAT_R16_SFLOAT:
        case VK_FORMAT_R16_UINT:
        case VK_FORMAT_R16_SINT:
        case VK_FORMAT_D16_UNORM:
            return 2;
        case VK_FORMAT_R8G8B8A8_UNORM:
        case VK_FORMAT_R8G8B8A8_SNORM:
        case VK_FORMAT_R8G8B8A8_UINT:
        case VK_FORMAT_R8G8B8A8_SINT:
        case VK_FORMAT_B8G8R8A8_UNORM:
        case VK_FORMAT_R16G16_SFLOAT:
        case VK_FORMAT_R32_SFLOAT:
        case VK_FORMAT_R32_UINT:
        case VK_FORMAT_R32_SINT:
        case VK_FORMAT_D32_SFLOAT:
        case VK_FORMAT_D24_UNORM_S8_UINT:
            return 4;
        case VK_FORMAT_R16G16B16A16_SFLOAT:
        case VK_FORMAT_R32G32_SFLOAT:
        case VK_FORMAT_R32G32_UINT:
        case VK_FORMAT_R32G32_SINT:
        case VK_FORMAT_D32_SFLOAT_S8_UINT:
            return 8;
        case VK_FORMAT_R32G32B32A32_SFLOAT:
        case VK_FORMAT_R32G32B32A32_UINT:
        case VK_FORMAT_R32G32B32A32_SINT:
            return 16;
        default:
            /*
             * Fail safe for block-compressed/depth/vendor formats: reserve a
             * conservative RGBA32F-sized image footprint rather than returning
             * a too-small requirement that would corrupt later bind validation.
             */
            return 16;
    }
}

static bool pdocker_vk_format_has_depth(VkFormat format) {
    switch (format) {
        case VK_FORMAT_D16_UNORM:
        case VK_FORMAT_D16_UNORM_S8_UINT:
        case VK_FORMAT_D24_UNORM_S8_UINT:
        case VK_FORMAT_D32_SFLOAT:
        case VK_FORMAT_D32_SFLOAT_S8_UINT:
            return true;
        default:
            return false;
    }
}

static bool pdocker_vk_format_has_stencil(VkFormat format) {
    switch (format) {
        case VK_FORMAT_S8_UINT:
        case VK_FORMAT_D16_UNORM_S8_UINT:
        case VK_FORMAT_D24_UNORM_S8_UINT:
        case VK_FORMAT_D32_SFLOAT_S8_UINT:
            return true;
        default:
            return false;
    }
}

static bool pdocker_vk_format_is_depth_stencil(VkFormat format) {
    return pdocker_vk_format_has_depth(format) ||
           pdocker_vk_format_has_stencil(format);
}

static bool pdocker_vk_format_bridge_supported(VkFormat format) {
    switch (format) {
        case VK_FORMAT_R8_UNORM:
        case VK_FORMAT_R8_SNORM:
        case VK_FORMAT_R8_UINT:
        case VK_FORMAT_R8_SINT:
        case VK_FORMAT_R8G8_UNORM:
        case VK_FORMAT_R8G8_SNORM:
        case VK_FORMAT_R8G8_UINT:
        case VK_FORMAT_R8G8_SINT:
        case VK_FORMAT_R16_SFLOAT:
        case VK_FORMAT_R16_UINT:
        case VK_FORMAT_R16_SINT:
        case VK_FORMAT_R8G8B8A8_UNORM:
        case VK_FORMAT_R8G8B8A8_SNORM:
        case VK_FORMAT_R8G8B8A8_UINT:
        case VK_FORMAT_R8G8B8A8_SINT:
        case VK_FORMAT_B8G8R8A8_UNORM:
        case VK_FORMAT_R16G16_SFLOAT:
        case VK_FORMAT_R32_SFLOAT:
        case VK_FORMAT_R32_UINT:
        case VK_FORMAT_R32_SINT:
        case VK_FORMAT_R16G16B16A16_SFLOAT:
        case VK_FORMAT_R32G32_SFLOAT:
        case VK_FORMAT_R32G32_UINT:
        case VK_FORMAT_R32G32_SINT:
        case VK_FORMAT_R32G32B32A32_SFLOAT:
        case VK_FORMAT_R32G32B32A32_UINT:
        case VK_FORMAT_R32G32B32A32_SINT:
        case VK_FORMAT_S8_UINT:
        case VK_FORMAT_D16_UNORM:
        case VK_FORMAT_D24_UNORM_S8_UINT:
        case VK_FORMAT_D32_SFLOAT:
        case VK_FORMAT_D32_SFLOAT_S8_UINT:
            return true;
        default:
            return false;
    }
}

static VkFormatFeatureFlags pdocker_vk_format_buffer_features(VkFormat format) {
    if (!pdocker_vk_format_bridge_supported(format) ||
        pdocker_vk_format_is_depth_stencil(format)) {
        return 0;
    }
    return VK_FORMAT_FEATURE_VERTEX_BUFFER_BIT;
}

static VkFormatFeatureFlags pdocker_vk_format_image_features(VkFormat format) {
    if (!pdocker_vk_format_bridge_supported(format)) return 0;
    VkFormatFeatureFlags features = VK_FORMAT_FEATURE_TRANSFER_SRC_BIT |
                                    VK_FORMAT_FEATURE_TRANSFER_DST_BIT |
                                    VK_FORMAT_FEATURE_SAMPLED_IMAGE_BIT;
    if (pdocker_vk_format_is_depth_stencil(format)) {
        features |= VK_FORMAT_FEATURE_DEPTH_STENCIL_ATTACHMENT_BIT;
    } else {
        features |= VK_FORMAT_FEATURE_STORAGE_IMAGE_BIT |
                    VK_FORMAT_FEATURE_COLOR_ATTACHMENT_BIT;
    }
    return features;
}

static bool pdocker_vk_image_usage_supported_by_format(
        VkFormat format,
        VkImageUsageFlags usage) {
    if (!pdocker_vk_format_bridge_supported(format)) return false;
    const bool depth_stencil = pdocker_vk_format_is_depth_stencil(format);
    const VkImageUsageFlags common = VK_IMAGE_USAGE_TRANSFER_SRC_BIT |
                                     VK_IMAGE_USAGE_TRANSFER_DST_BIT |
                                     VK_IMAGE_USAGE_SAMPLED_BIT |
                                     VK_IMAGE_USAGE_INPUT_ATTACHMENT_BIT;
    const VkImageUsageFlags color = common |
                                    VK_IMAGE_USAGE_STORAGE_BIT |
                                    VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT;
    const VkImageUsageFlags ds = common |
                                 VK_IMAGE_USAGE_DEPTH_STENCIL_ATTACHMENT_BIT;
    const VkImageUsageFlags supported = depth_stencil ? ds : color;
    return usage != 0 && (usage & ~supported) == 0;
}

static uint32_t pdocker_vk_image_max_mip_levels(VkExtent3D extent) {
    uint32_t max_dim = extent.width;
    if (extent.height > max_dim) max_dim = extent.height;
    if (extent.depth > max_dim) max_dim = extent.depth;
    uint32_t levels = 1;
    while (max_dim > 1) {
        max_dim >>= 1;
        ++levels;
    }
    return levels;
}

static uint8_t clear_unorm8(float v) {
    if (v <= 0.0f) return 0;
    if (v >= 1.0f) return 255;
    return (uint8_t)(v * 255.0f + 0.5f);
}

static int8_t clear_snorm8(float v) {
    if (v <= -1.0f) return -127;
    if (v >= 1.0f) return 127;
    return (int8_t)(v * 127.0f + (v >= 0.0f ? 0.5f : -0.5f));
}

static uint16_t clear_float32_to_float16_bits(float value) {
    union {
        float f;
        uint32_t u;
    } in;
    in.f = value;
    uint32_t sign = (in.u >> 16) & 0x8000u;
    int32_t exponent = (int32_t)((in.u >> 23) & 0xffu) - 127 + 15;
    uint32_t mantissa = in.u & 0x7fffffu;
    if (exponent <= 0) {
        if (exponent < -10) return (uint16_t)sign;
        mantissa |= 0x800000u;
        uint32_t shifted = mantissa >> (uint32_t)(1 - exponent + 13);
        return (uint16_t)(sign | shifted);
    }
    if (exponent >= 31) {
        return (uint16_t)(sign | 0x7c00u | (mantissa ? 0x0200u : 0u));
    }
    return (uint16_t)(sign | ((uint32_t)exponent << 10) | (mantissa >> 13));
}

static void encode_clear_color_pixel(
        VkFormat format,
        const VkClearColorValue *color,
        uint8_t *pixel,
        size_t pixel_size) {
    if (!color || !pixel || pixel_size == 0) return;
    memset(pixel, 0, pixel_size);
#define COPY_BYTES(ptr_, size_) do { \
        size_t n_ = (size_) < pixel_size ? (size_) : pixel_size; \
        memcpy(pixel, (ptr_), n_); \
    } while (0)
    switch (format) {
        case VK_FORMAT_R8_UNORM:
            pixel[0] = clear_unorm8(color->float32[0]);
            break;
        case VK_FORMAT_R8_SNORM:
            pixel[0] = (uint8_t)clear_snorm8(color->float32[0]);
            break;
        case VK_FORMAT_R8_UINT:
            pixel[0] = (uint8_t)color->uint32[0];
            break;
        case VK_FORMAT_R8_SINT:
            pixel[0] = (uint8_t)color->int32[0];
            break;
        case VK_FORMAT_R8G8_UNORM:
            pixel[0] = clear_unorm8(color->float32[0]);
            pixel[1] = clear_unorm8(color->float32[1]);
            break;
        case VK_FORMAT_R8G8_SNORM:
            pixel[0] = (uint8_t)clear_snorm8(color->float32[0]);
            pixel[1] = (uint8_t)clear_snorm8(color->float32[1]);
            break;
        case VK_FORMAT_R8G8_UINT:
            pixel[0] = (uint8_t)color->uint32[0];
            pixel[1] = (uint8_t)color->uint32[1];
            break;
        case VK_FORMAT_R8G8_SINT:
            pixel[0] = (uint8_t)color->int32[0];
            pixel[1] = (uint8_t)color->int32[1];
            break;
        case VK_FORMAT_R8G8B8A8_UNORM:
            pixel[0] = clear_unorm8(color->float32[0]);
            pixel[1] = clear_unorm8(color->float32[1]);
            pixel[2] = clear_unorm8(color->float32[2]);
            pixel[3] = clear_unorm8(color->float32[3]);
            break;
        case VK_FORMAT_R8G8B8A8_SNORM:
            pixel[0] = (uint8_t)clear_snorm8(color->float32[0]);
            pixel[1] = (uint8_t)clear_snorm8(color->float32[1]);
            pixel[2] = (uint8_t)clear_snorm8(color->float32[2]);
            pixel[3] = (uint8_t)clear_snorm8(color->float32[3]);
            break;
        case VK_FORMAT_R8G8B8A8_UINT:
            pixel[0] = (uint8_t)color->uint32[0];
            pixel[1] = (uint8_t)color->uint32[1];
            pixel[2] = (uint8_t)color->uint32[2];
            pixel[3] = (uint8_t)color->uint32[3];
            break;
        case VK_FORMAT_R8G8B8A8_SINT:
            pixel[0] = (uint8_t)color->int32[0];
            pixel[1] = (uint8_t)color->int32[1];
            pixel[2] = (uint8_t)color->int32[2];
            pixel[3] = (uint8_t)color->int32[3];
            break;
        case VK_FORMAT_B8G8R8A8_UNORM:
            pixel[0] = clear_unorm8(color->float32[2]);
            pixel[1] = clear_unorm8(color->float32[1]);
            pixel[2] = clear_unorm8(color->float32[0]);
            pixel[3] = clear_unorm8(color->float32[3]);
            break;
        case VK_FORMAT_R16_SFLOAT: {
            uint16_t v = clear_float32_to_float16_bits(color->float32[0]);
            COPY_BYTES(&v, sizeof(v));
            break;
        }
        case VK_FORMAT_R16_UINT: {
            uint16_t v = (uint16_t)color->uint32[0];
            COPY_BYTES(&v, sizeof(v));
            break;
        }
        case VK_FORMAT_R16_SINT: {
            int16_t v = (int16_t)color->int32[0];
            COPY_BYTES(&v, sizeof(v));
            break;
        }
        case VK_FORMAT_R16G16_SFLOAT: {
            uint16_t v[2] = {
                clear_float32_to_float16_bits(color->float32[0]),
                clear_float32_to_float16_bits(color->float32[1]),
            };
            COPY_BYTES(&v, sizeof(v));
            break;
        }
        case VK_FORMAT_R16G16B16A16_SFLOAT: {
            uint16_t v[4] = {
                clear_float32_to_float16_bits(color->float32[0]),
                clear_float32_to_float16_bits(color->float32[1]),
                clear_float32_to_float16_bits(color->float32[2]),
                clear_float32_to_float16_bits(color->float32[3]),
            };
            COPY_BYTES(&v, sizeof(v));
            break;
        }
        case VK_FORMAT_R32_SFLOAT:
            COPY_BYTES(&color->float32[0], sizeof(color->float32[0]));
            break;
        case VK_FORMAT_R32_UINT:
            COPY_BYTES(&color->uint32[0], sizeof(color->uint32[0]));
            break;
        case VK_FORMAT_R32_SINT:
            COPY_BYTES(&color->int32[0], sizeof(color->int32[0]));
            break;
        case VK_FORMAT_R32G32_SFLOAT:
            COPY_BYTES(color->float32, sizeof(color->float32));
            break;
        case VK_FORMAT_R32G32_UINT:
            COPY_BYTES(color->uint32, sizeof(color->uint32));
            break;
        case VK_FORMAT_R32G32_SINT:
            COPY_BYTES(color->int32, sizeof(color->int32));
            break;
        case VK_FORMAT_R32G32B32A32_SFLOAT:
            COPY_BYTES(color->float32, sizeof(color->float32));
            break;
        case VK_FORMAT_R32G32B32A32_UINT:
            COPY_BYTES(color->uint32, sizeof(color->uint32));
            break;
        case VK_FORMAT_R32G32B32A32_SINT:
            COPY_BYTES(color->int32, sizeof(color->int32));
            break;
        default:
            COPY_BYTES(color->uint32, sizeof(color->uint32));
            break;
    }
#undef COPY_BYTES
}

static VkDeviceSize estimate_image_requirement_size(const VkImageCreateInfo *info) {
    if (!info) return 0;
    uint64_t pixels = 0;
    if (!checked_mul_u64(info->extent.width ? info->extent.width : 1, info->extent.height ? info->extent.height : 1, &pixels)) {
        return 0;
    }
    if (!checked_mul_u64(pixels, info->extent.depth ? info->extent.depth : 1, &pixels)) return 0;
    if (!checked_mul_u64(pixels, info->arrayLayers ? info->arrayLayers : 1, &pixels)) return 0;
    if (!checked_mul_u64(pixels, info->mipLevels ? info->mipLevels : 1, &pixels)) return 0;
    if (!checked_mul_u64(pixels, conservative_format_bytes_per_pixel(info->format), &pixels)) return 0;
    return align_device_size((VkDeviceSize)pixels, PDOCKER_VK_REQUIREMENT_ALIGNMENT);
}

static VkDeviceSize pdocker_vulkan_max_buffer_size(void) {
    const char *env = getenv("PDOCKER_VULKAN_MAX_BUFFER_BYTES");
    if (env && env[0]) {
        char *end = NULL;
        unsigned long long value = strtoull(env, &end, 10);
        if (end && *end == '\0' && value >= 16ull * 1024ull * 1024ull) {
            return (VkDeviceSize)value;
        }
    }
    const VkDeviceSize heap = pdocker_vulkan_heap_size();
    const VkDeviceSize bridge_default = (VkDeviceSize)(2ull * 1024ull * 1024ull * 1024ull);
    return heap < bridge_default ? heap : bridge_default;
}

static VkDeviceSize pdocker_vulkan_host_heap_size(void) {
    VkDeviceSize heap = pdocker_vulkan_heap_size();
    VkDeviceSize host_heap = heap / 2;
    const VkDeviceSize min_heap = (VkDeviceSize)(256ull * 1024ull * 1024ull);
    if (host_heap < min_heap) host_heap = min_heap;
    if (host_heap > heap) host_heap = heap;
    return host_heap;
}

static bool trace_allocations(void) {
    return getenv("PDOCKER_VULKAN_ICD_TRACE_ALLOC") != NULL;
}

static void trace_icd_runtime_failure(const char *stage, int rc) {
    fprintf(stderr,
            "pdocker-vulkan-icd: runtime_marker=%s stage=%s rc=%d\n",
            PDOCKER_VULKAN_ICD_BUILD_MARKER,
            stage ? stage : "unknown",
            rc);
}

static void trace_icd_runtime_marker_once(const char *stage) {
    static int emitted = 0;
    if (__sync_lock_test_and_set(&emitted, 1)) return;
    fprintf(stderr,
            "pdocker-vulkan-icd: runtime_marker=%s stage=%s rc=0\n",
            PDOCKER_VULKAN_ICD_BUILD_MARKER,
            stage ? stage : "loaded");
}

static bool copy_alias_enabled(void) {
    return env_truthy_default("PDOCKER_VULKAN_ALIAS_COPIES", false);
}

static size_t guarded_page_size(void) {
    long page = sysconf(_SC_PAGESIZE);
    return page > 0 ? (size_t)page : 4096u;
}

static size_t guarded_memory_threshold(void) {
    const char *env = getenv("PDOCKER_GPU_VIRTUAL_MEMORY_MIN_BYTES");
    if (env && env[0]) {
        char *end = NULL;
        unsigned long long value = strtoull(env, &end, 10);
        if (end && *end == '\0' && value > 0) return (size_t)value;
    }
    return PDOCKER_VK_GUARDED_DEFAULT_MIN_BYTES;
}

static bool guarded_memory_enabled(size_t size, VkMemoryPropertyFlags flags) {
    (void)flags;
    const char *mode = getenv("PDOCKER_GPU_VIRTUAL_MEMORY");
    if (!mode || strcmp(mode, "guarded") != 0) return false;
    return size >= guarded_memory_threshold();
}

static void chain_previous_sigsegv(int sig, siginfo_t *info, void *context) {
    if (g_previous_sigsegv.sa_flags & SA_SIGINFO) {
        if (g_previous_sigsegv.sa_sigaction) {
            g_previous_sigsegv.sa_sigaction(sig, info, context);
            return;
        }
    } else if (g_previous_sigsegv.sa_handler == SIG_IGN) {
        return;
    } else if (g_previous_sigsegv.sa_handler &&
               g_previous_sigsegv.sa_handler != SIG_DFL) {
        g_previous_sigsegv.sa_handler(sig);
        return;
    }
    signal(sig, SIG_DFL);
    raise(sig);
}

static void guarded_sigsegv_handler(int sig, siginfo_t *info, void *context) {
    uintptr_t fault = (uintptr_t)(info ? info->si_addr : NULL);
    for (size_t i = 0; i < PDOCKER_VK_MAX_GUARDED_MEMORIES; ++i) {
        PdockerVkMemory *memory = g_guarded_memories[i];
        if (!memory || !memory->guarded || !memory->map || !memory->page_size) continue;
        uintptr_t start = (uintptr_t)memory->map;
        uintptr_t end = start + memory->size;
        if (fault < start || fault >= end) continue;
        size_t page = (fault - start) / memory->page_size;
        uintptr_t page_addr = start + page * memory->page_size;
        if (mprotect((void *)page_addr, memory->page_size, PROT_READ | PROT_WRITE) == 0) {
            if (page < memory->page_count) {
                memory->resident_pages[page] = 1;
                memory->dirty_pages[page] = 1;
            }
            return;
        }
        break;
    }
    chain_previous_sigsegv(sig, info, context);
}

static bool install_guarded_sigsegv_handler(void) {
    if (g_guarded_sigsegv_installed) return true;
    struct sigaction action;
    memset(&action, 0, sizeof(action));
    action.sa_sigaction = guarded_sigsegv_handler;
    action.sa_flags = SA_SIGINFO | SA_NODEFER;
    sigemptyset(&action.sa_mask);
    if (sigaction(SIGSEGV, &action, &g_previous_sigsegv) != 0) return false;
    g_guarded_sigsegv_installed = true;
    return true;
}

static bool register_guarded_memory(PdockerVkMemory *memory) {
    if (!memory) return false;
    if (!install_guarded_sigsegv_handler()) return false;
    for (size_t i = 0; i < PDOCKER_VK_MAX_GUARDED_MEMORIES; ++i) {
        if (!g_guarded_memories[i]) {
            g_guarded_memories[i] = memory;
            return true;
        }
    }
    return false;
}

static size_t guarded_page_count(const unsigned char *pages, size_t count) {
    if (!pages) return 0;
    size_t out = 0;
    for (size_t i = 0; i < count; ++i) {
        if (pages[i]) out++;
    }
    return out;
}

static void trace_guarded_binding(uint32_t binding,
                                  const PdockerVkMemory *memory,
                                  VkDeviceSize offset,
                                  size_t size) {
    if (!trace_allocations() || !memory || !memory->guarded || !memory->page_size) return;
    size_t resident_pages = guarded_page_count(memory->resident_pages, memory->page_count);
    size_t dirty_pages = guarded_page_count(memory->dirty_pages, memory->page_count);
    fprintf(stderr,
            "pdocker-vulkan-icd: guarded-binding binding=%u offset=%llu range=%zu allocation=%zu page_size=%zu resident_pages=%zu dirty_pages=%zu resident_bytes=%llu dirty_bytes=%llu\n",
            binding,
            (unsigned long long)offset,
            size,
            memory->size,
            memory->page_size,
            resident_pages,
            dirty_pages,
            (unsigned long long)(resident_pages * memory->page_size),
            (unsigned long long)(dirty_pages * memory->page_size));
}

static void unregister_guarded_memory(PdockerVkMemory *memory) {
    if (!memory) return;
    for (size_t i = 0; i < PDOCKER_VK_MAX_GUARDED_MEMORIES; ++i) {
        if (g_guarded_memories[i] == memory) g_guarded_memories[i] = NULL;
    }
}

typedef struct {
    VkStructureType sType;
    const void *pNext;
} PdockerVkStructHeader;

static PdockerVkStructHeader read_vk_struct_header(const void *node) {
    PdockerVkStructHeader header;
    memset(&header, 0, sizeof(header));
    if (node) memcpy(&header, node, sizeof(header));
    return header;
}

static void trace_pnext_chain(const char *prefix, const void *pNext) {
    if (!trace_allocations()) return;
    const void *node = pNext;
    while (node) {
        PdockerVkStructHeader header = read_vk_struct_header(node);
        fprintf(stderr,
                "pdocker-vulkan-icd: %s pnext sType=%d\n",
                prefix,
                (int)header.sType);
        node = header.pNext;
    }
}

static void *pdocker_alloc_handle(size_t size) {
    return calloc(1, size ? size : sizeof(PdockerHandle));
}

static int create_shared_fd(size_t bytes) {
#ifdef __NR_memfd_create
    int memfd = (int)syscall(__NR_memfd_create, "pdocker-vulkan-memory", MFD_CLOEXEC);
    if (memfd >= 0) {
        if (ftruncate(memfd, (off_t)bytes) == 0) return memfd;
        int err = errno;
        close(memfd);
        errno = err;
        return -1;
    }
#endif
    const char *dir = getenv("PDOCKER_GPU_SHARED_DIR");
    if (!dir || !dir[0]) dir = "/tmp";
    char path[512];
    snprintf(path, sizeof(path), "%s/pdocker-vulkan-memory-XXXXXX", dir);
    int fd = mkstemp(path);
    if (fd < 0) return -1;
    unlink(path);
    if (ftruncate(fd, (off_t)bytes) != 0) {
        int err = errno;
        close(fd);
        errno = err;
        return -1;
    }
    return fd;
}

static bool bridge_available(void) {
    const char *socket_path = getenv("PDOCKER_GPU_QUEUE_SOCKET");
    if (socket_path && socket_path[0]) return true;
    return access("/run/pdocker-gpu/pdocker-gpu.sock", F_OK) == 0;
}

static int connect_queue(void) {
    const char *path = getenv("PDOCKER_GPU_QUEUE_SOCKET");
    if (!path || !path[0]) path = "/run/pdocker-gpu/pdocker-gpu.sock";
    if (strlen(path) >= sizeof(((struct sockaddr_un *)0)->sun_path)) return -ENAMETOOLONG;
    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) return -errno;
    struct sockaddr_un addr;
    memset(&addr, 0, sizeof(addr));
    addr.sun_family = AF_UNIX;
    snprintf(addr.sun_path, sizeof(addr.sun_path), "%s", path);
    if (connect(fd, (struct sockaddr *)&addr, sizeof(addr)) != 0) {
        int err = errno;
        close(fd);
        return -err;
    }
    return fd;
}

static int send_vector_add_3fd(size_t n, int fd_a, int fd_b, int fd_out) {
    int socket_fd = connect_queue();
    if (socket_fd < 0) return socket_fd;
    char cmd[64];
    snprintf(cmd, sizeof(cmd), "VULKAN_VECTOR_ADD_3FD %zu\n", n);
    int fds[3] = { fd_a, fd_b, fd_out };
    char control[CMSG_SPACE(sizeof(fds))];
    struct iovec iov;
    struct msghdr msg;
    memset(control, 0, sizeof(control));
    memset(&iov, 0, sizeof(iov));
    memset(&msg, 0, sizeof(msg));
    iov.iov_base = cmd;
    iov.iov_len = strlen(cmd);
    msg.msg_iov = &iov;
    msg.msg_iovlen = 1;
    msg.msg_control = control;
    msg.msg_controllen = sizeof(control);
    struct cmsghdr *cmsg = CMSG_FIRSTHDR(&msg);
    cmsg->cmsg_level = SOL_SOCKET;
    cmsg->cmsg_type = SCM_RIGHTS;
    cmsg->cmsg_len = CMSG_LEN(sizeof(fds));
    memcpy(CMSG_DATA(cmsg), fds, sizeof(fds));
    msg.msg_controllen = sizeof(control);
    int rc = 0;
    if (sendmsg(socket_fd, &msg, 0) < 0) {
        rc = -errno;
    } else {
        char line[4096];
        size_t off = 0;
        while (off + 1 < sizeof(line)) {
            char ch;
            ssize_t r = read(socket_fd, &ch, 1);
            if (r <= 0) break;
            line[off++] = ch;
            if (ch == '\n') break;
        }
        line[off] = '\0';
        if (getenv("PDOCKER_VULKAN_ICD_DEBUG")) {
            fprintf(stderr, "pdocker-vulkan-icd: bridge response: %s", line);
            if (off == 0 || line[off - 1] != '\n') fprintf(stderr, "\n");
        }
        if (strstr(line, "\"valid\":true") == NULL) rc = -EIO;
    }
    close(socket_fd);
    return rc;
}

static void hex_encode(const uint8_t *src, size_t src_size, char *dst, size_t dst_size) {
    static const char hex[] = "0123456789abcdef";
    if (!dst || dst_size == 0) return;
    size_t max_bytes = (dst_size - 1) / 2;
    if (src_size > max_bytes) src_size = max_bytes;
    for (size_t i = 0; i < src_size; ++i) {
        dst[i * 2] = hex[(src[i] >> 4) & 0xf];
        dst[i * 2 + 1] = hex[src[i] & 0xf];
    }
    dst[src_size * 2] = '\0';
}

static double monotonic_ms(void) {
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0) return 0.0;
    return (double)ts.tv_sec * 1000.0 + (double)ts.tv_nsec / 1000000.0;
}

static uint64_t monotonic_ns(void) {
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0) return 0;
    return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
}

static uint64_t fnv1a64_bytes(const void *data, size_t size) {
    const unsigned char *bytes = (const unsigned char *)data;
    uint64_t hash = 1469598103934665603ull;
    if (!bytes) return 0;
    for (size_t i = 0; i < size; ++i) {
        hash ^= (uint64_t)bytes[i];
        hash *= 1099511628211ull;
    }
    return hash;
}

static uint64_t fnv1a64_update_bytes(uint64_t hash, const void *data, size_t size) {
    const unsigned char *bytes = (const unsigned char *)data;
    if (!bytes) return hash;
    for (size_t i = 0; i < size; ++i) {
        hash ^= (uint64_t)bytes[i];
        hash *= 1099511628211ull;
    }
    return hash;
}

static uint64_t fnv1a64_update_u32(uint64_t hash, uint32_t value) {
    return fnv1a64_update_bytes(hash, &value, sizeof(value));
}

static uint64_t fnv1a64_update_u64(uint64_t hash, uint64_t value) {
    return fnv1a64_update_bytes(hash, &value, sizeof(value));
}

static bool vulkan_v5_frame_enabled(void) {
    return env_truthy_default("PDOCKER_VULKAN_USE_V5_FRAME", false);
}

static size_t align_size_8(size_t value) {
    return (value + 7u) & ~(size_t)7u;
}

static int write_exact_fd(int fd, const void *data, size_t size) {
    const unsigned char *p = (const unsigned char *)data;
    size_t off = 0;
    while (off < size) {
        ssize_t n = write(fd, p + off, size - off);
        if (n < 0) {
            if (errno == EINTR) continue;
            return -errno;
        }
        if (n == 0) return -EPIPE;
        off += (size_t)n;
    }
    return 0;
}

static bool dispatch_response_is_graphics_transport(const char *transport_name) {
    return transport_name && strstr(transport_name, "VULKAN_GRAPHICS_V6") != NULL;
}

static bool dispatch_response_is_graphics_terminal_success(const char *line) {
    return line &&
           strstr(line, "\"stage\":\"vulkan-graphics-v6-replay\"") != NULL &&
           strstr(line, "\"valid\":true") != NULL &&
           strstr(line, "\"execution_implemented\":true") != NULL;
}

static int read_dispatch_response_status(int socket_fd, const char *transport_name) {
    const size_t max_response = 1024 * 1024;
    char stack_line[16384];
    char *heap_line = NULL;
    char *line = stack_line;
    size_t line_cap = sizeof(stack_line);
    size_t total_read = 0;
    bool saw_nonterminal = false;
    bool graphics_transport = dispatch_response_is_graphics_transport(transport_name);
    int rc = -EIO;

    for (unsigned line_index = 0; line_index < 16 && total_read < max_response; ++line_index) {
        size_t line_off = 0;
        int read_rc = 0;
        while (line_off + 1 < max_response - total_read) {
            if (line_off + 1 >= line_cap) {
                size_t next_cap = line_cap * 2;
                if (next_cap < line_cap) {
                    read_rc = -EOVERFLOW;
                    break;
                }
                if (next_cap > max_response) next_cap = max_response;
                char *next = (char *)malloc(next_cap);
                if (!next) {
                    read_rc = -ENOMEM;
                    break;
                }
                memcpy(next, line, line_off);
                free(heap_line);
                heap_line = next;
                line = heap_line;
                line_cap = next_cap;
            }
            char ch;
            ssize_t r = read(socket_fd, &ch, 1);
            if (r < 0) {
                if (errno == EINTR) continue;
                read_rc = -errno;
                break;
            }
            if (r == 0) break;
            line[line_off++] = ch;
            total_read++;
            if (ch == '\n') break;
        }
        line[line_off] = '\0';
        if (read_rc != 0) {
            rc = read_rc;
            break;
        }
        if (line_off == 0) {
            rc = saw_nonterminal ? -EPROTO : -EIO;
            break;
        }
        if (trace_allocations() || getenv("PDOCKER_VULKAN_ICD_DEBUG") ||
            env_truthy_default("PDOCKER_GPU_DISPATCH_PROFILE_LOG", false)) {
            fprintf(stderr,
                    "pdocker-vulkan-icd: %s dispatch response: %s",
                    transport_name ? transport_name : "generic",
                    line);
            if (line[line_off - 1] != '\n') fprintf(stderr, "\n");
        }
        if (graphics_transport) {
            if (strstr(line, "\"valid\":false") != NULL) {
                rc = -EIO;
                break;
            }
            if (dispatch_response_is_graphics_terminal_success(line)) {
                rc = 0;
                break;
            }
            saw_nonterminal = true;
            continue;
        }
        if (strstr(line, "\"stage\":\"vulkan-graphics-v6-describe\"") != NULL) {
            saw_nonterminal = true;
            continue;
        }
        if (strstr(line, "\"valid\":true") != NULL) {
            rc = 0;
            break;
        }
        rc = -EIO;
        break;
    }
    if (total_read >= max_response) rc = -EMSGSIZE;
    free(heap_line);
    return rc;
}

typedef struct {
    bool enabled;
    int shader_fd;
    int debug_fd;
    size_t shader_size;
    size_t debug_bytes;
    uint32_t debug_set;
    uint32_t debug_binding;
    uint64_t expected_source_hash;
    uint64_t effective_shader_hash;
    const char *manifest_path;
    const char *shader_path;
} PdockerVkSpirvProbeReplay;

static bool parse_u64_env_base0(const char *name, uint64_t *out) {
    const char *value = getenv(name);
    if (!value || !value[0] || !out) return false;
    char *end = NULL;
    errno = 0;
    unsigned long long parsed = strtoull(value, &end, 0);
    if (errno != 0 || !end || *end != '\0') return false;
    *out = (uint64_t)parsed;
    return true;
}

static bool parse_size_env_base0(const char *name, size_t *out) {
    uint64_t value = 0;
    if (!parse_u64_env_base0(name, &value) || value > (uint64_t)SIZE_MAX) return false;
    *out = (size_t)value;
    return true;
}

static bool parse_u32_env_base0(const char *name, uint32_t *out) {
    uint64_t value = 0;
    if (!parse_u64_env_base0(name, &value) || value > UINT32_MAX) return false;
    *out = (uint32_t)value;
    return true;
}

static void close_spirv_probe_replay(PdockerVkSpirvProbeReplay *probe) {
    if (!probe) return;
    if (probe->shader_fd >= 0) {
        close(probe->shader_fd);
        probe->shader_fd = -1;
    }
    if (probe->debug_fd >= 0) {
        close(probe->debug_fd);
        probe->debug_fd = -1;
    }
}

static bool text_contains_u32_json_field(const char *text, const char *field, uint32_t value) {
    if (!text || !field) return false;
    char compact[64];
    char spaced[64];
    snprintf(compact, sizeof(compact), "\"%s\":%u", field, value);
    snprintf(spaced, sizeof(spaced), "\"%s\": %u", field, value);
    return strstr(text, compact) || strstr(text, spaced);
}

static bool text_contains_size_json_field(const char *text, const char *field, size_t value) {
    if (!text || !field) return false;
    char compact[80];
    char spaced[80];
    snprintf(compact, sizeof(compact), "\"%s\":%zu", field, value);
    snprintf(spaced, sizeof(spaced), "\"%s\": %zu", field, value);
    return strstr(text, compact) || strstr(text, spaced);
}

static bool text_contains_json_false_field(const char *text, const char *field) {
    if (!text || !field) return false;
    char compact[80];
    char spaced[80];
    snprintf(compact, sizeof(compact), "\"%s\":false", field);
    snprintf(spaced, sizeof(spaced), "\"%s\": false", field);
    return strstr(text, compact) || strstr(text, spaced);
}

static int read_probe_manifest_limited(const char *path, char **out_text, size_t *out_size) {
    if (!path || !path[0] || !out_text || !out_size) return -EINVAL;
    *out_text = NULL;
    *out_size = 0;
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) return -errno;
    struct stat st;
    if (fstat(fd, &st) != 0) {
        int rc = -errno;
        close(fd);
        return rc;
    }
    if (st.st_size <= 0 || (uint64_t)st.st_size > PDOCKER_VK_MAX_PROBE_MANIFEST_BYTES) {
        close(fd);
        return -EFBIG;
    }
    size_t size = (size_t)st.st_size;
    char *text = (char *)malloc(size + 1);
    if (!text) {
        close(fd);
        return -ENOMEM;
    }
    size_t off = 0;
    while (off < size) {
        ssize_t r = read(fd, text + off, size - off);
        if (r < 0) {
            int rc = -errno;
            free(text);
            close(fd);
            return rc;
        }
        if (r == 0) break;
        off += (size_t)r;
    }
    close(fd);
    if (off != size) {
        free(text);
        return -EIO;
    }
    text[size] = '\0';
    *out_text = text;
    *out_size = size;
    return 0;
}

static int verify_spirv_probe_manifest_runtime_guard(
        const PdockerVkSpirvProbeReplay *probe,
        uint64_t source_shader_hash) {
    if (!probe || !probe->manifest_path || !probe->manifest_path[0]) {
        fprintf(stderr,
                "pdocker-vulkan-icd: SPIR-V probe replay rejected: missing PDOCKER_GPU_SPIRV_PROBE_MANIFEST\n");
        return -EINVAL;
    }
    char *manifest = NULL;
    size_t manifest_size = 0;
    int rc = read_probe_manifest_limited(probe->manifest_path, &manifest, &manifest_size);
    if (rc < 0) {
        fprintf(stderr,
                "pdocker-vulkan-icd: SPIR-V probe replay rejected: cannot read manifest %s rc=%d\n",
                probe->manifest_path,
                rc);
        return rc;
    }
    char source_hash[32];
    char effective_hash[32];
    snprintf(source_hash, sizeof(source_hash), "0x%016llx",
             (unsigned long long)source_shader_hash);
    snprintf(effective_hash, sizeof(effective_hash), "0x%016llx",
             (unsigned long long)probe->effective_shader_hash);
    bool ok =
        strstr(manifest, "\"schema\"") &&
        strstr(manifest, "pdocker.spirv.probe-manifest.v1") &&
        strstr(manifest, "\"submission_model\"") &&
        strstr(manifest, "valid-module-instrumentation") &&
        text_contains_json_false_field(manifest, "fragment_submission_allowed") &&
        strstr(manifest, "\"dispatch_transport\"") &&
        strstr(manifest, "append-as-normal-vulkan-dispatch-v4-binding") &&
        text_contains_json_false_field(manifest, "dispatch_allowed") &&
        strstr(manifest, source_hash) &&
        text_contains_u32_json_field(manifest, "set", probe->debug_set) &&
        text_contains_u32_json_field(manifest, "binding", probe->debug_binding);
    /*
     * The pre-instrumentation manifest may not know the final instrumented
     * shader hash yet.  When the instrumenter records it, require the manifest
     * and runtime env to agree; otherwise the env hash still guards the fd.
     */
    if (strstr(manifest, "instrumented_spirv_hash") ||
        strstr(manifest, "effective_probe_shader_hash")) {
        ok = ok && strstr(manifest, effective_hash);
    }
    if (strstr(manifest, "\"debug_bytes\"") ||
        strstr(manifest, "\"min_bytes\"")) {
        ok = ok && (text_contains_size_json_field(manifest, "debug_bytes", probe->debug_bytes) ||
                    text_contains_size_json_field(manifest, "min_bytes", probe->debug_bytes));
    }
    free(manifest);
    if (!ok) {
        fprintf(stderr,
                "pdocker-vulkan-icd: SPIR-V probe replay rejected: manifest guard mismatch manifest=%s source_hash=%s effective_hash=%s debug_set=%u debug_binding=%u debug_bytes=%zu\n",
                probe->manifest_path,
                source_hash,
                effective_hash,
                probe->debug_set,
                probe->debug_binding,
                probe->debug_bytes);
        return -EPERM;
    }
    (void)manifest_size;
    return 0;
}

static int prepare_spirv_probe_replay(PdockerVkSpirvProbeReplay *probe,
                                      uint64_t source_shader_hash,
                                      size_t binding_count,
                                      const uint32_t *descriptor_sets,
                                      const uint32_t *bindings) {
    if (!probe) return -EINVAL;
    memset(probe, 0, sizeof(*probe));
    probe->shader_fd = -1;
    probe->debug_fd = -1;
    probe->manifest_path = getenv("PDOCKER_GPU_SPIRV_PROBE_MANIFEST");
    probe->shader_path = getenv("PDOCKER_GPU_SPIRV_PROBE_SHADER");
    if (!probe->shader_path || !probe->shader_path[0]) return 0;

    if (!parse_u64_env_base0("PDOCKER_GPU_SPIRV_PROBE_EXPECTED_HASH",
                             &probe->expected_source_hash)) {
        fprintf(stderr,
                "pdocker-vulkan-icd: SPIR-V probe replay rejected: missing PDOCKER_GPU_SPIRV_PROBE_EXPECTED_HASH\n");
        return -EINVAL;
    }
    if (probe->expected_source_hash != source_shader_hash) {
        if (env_truthy_default("PDOCKER_GPU_SPIRV_PROBE_TARGET_ONLY", false)) {
            if (trace_allocations() || env_truthy_default("PDOCKER_GPU_DISPATCH_PROFILE_LOG", false)) {
                fprintf(stderr,
                        "pdocker-vulkan-icd: SPIR-V probe replay skipped non-target shader expected=0x%016llx actual=0x%016llx manifest=%s\n",
                        (unsigned long long)probe->expected_source_hash,
                        (unsigned long long)source_shader_hash,
                        (probe->manifest_path && probe->manifest_path[0]) ? probe->manifest_path : "-");
            }
            return 0;
        }
        fprintf(stderr,
                "pdocker-vulkan-icd: SPIR-V probe replay rejected: source hash mismatch expected=0x%016llx actual=0x%016llx manifest=%s\n",
                (unsigned long long)probe->expected_source_hash,
                (unsigned long long)source_shader_hash,
                (probe->manifest_path && probe->manifest_path[0]) ? probe->manifest_path : "-");
        return -ENOEXEC;
    }
    uint64_t expected_effective_hash = 0;
    if (!parse_u64_env_base0("PDOCKER_GPU_SPIRV_PROBE_EFFECTIVE_HASH",
                             &expected_effective_hash)) {
        fprintf(stderr,
                "pdocker-vulkan-icd: SPIR-V probe replay rejected: missing PDOCKER_GPU_SPIRV_PROBE_EFFECTIVE_HASH\n");
        return -EINVAL;
    }
    if (!parse_size_env_base0("PDOCKER_GPU_SPIRV_PROBE_DEBUG_BYTES",
                              &probe->debug_bytes) ||
        probe->debug_bytes == 0 ||
        probe->debug_bytes > 16ull * 1024ull * 1024ull) {
        fprintf(stderr,
                "pdocker-vulkan-icd: SPIR-V probe replay rejected: invalid PDOCKER_GPU_SPIRV_PROBE_DEBUG_BYTES\n");
        return -EINVAL;
    }
    if (!parse_u32_env_base0("PDOCKER_GPU_SPIRV_PROBE_DEBUG_SET",
                             &probe->debug_set) ||
        !parse_u32_env_base0("PDOCKER_GPU_SPIRV_PROBE_DEBUG_BINDING",
                             &probe->debug_binding) ||
        probe->debug_set >= PDOCKER_VK_MAX_DESCRIPTOR_SETS ||
        probe->debug_binding >= PDOCKER_VK_MAX_STORAGE_BUFFERS) {
        fprintf(stderr,
                "pdocker-vulkan-icd: SPIR-V probe replay rejected: invalid debug descriptor set/binding\n");
        return -EINVAL;
    }
    if (binding_count >= PDOCKER_VK_MAX_STORAGE_BUFFERS) {
        fprintf(stderr,
                "pdocker-vulkan-icd: SPIR-V probe replay rejected: no V4 binding slot for debug SSBO\n");
        return -E2BIG;
    }
    for (size_t i = 0; i < binding_count; ++i) {
        if (descriptor_sets && bindings &&
            descriptor_sets[i] == probe->debug_set &&
            bindings[i] == probe->debug_binding) {
            fprintf(stderr,
                    "pdocker-vulkan-icd: SPIR-V probe replay rejected: debug descriptor collides set=%u binding=%u\n",
                    probe->debug_set,
                    probe->debug_binding);
            return -EEXIST;
        }
        if (bindings && bindings[i] == probe->debug_binding) {
            fprintf(stderr,
                    "pdocker-vulkan-icd: SPIR-V probe replay rejected: debug binding number collides binding=%u\n",
                    probe->debug_binding);
            return -EEXIST;
        }
    }

    probe->shader_fd = open(probe->shader_path, O_RDONLY | O_CLOEXEC);
    if (probe->shader_fd < 0) {
        int rc = -errno;
        fprintf(stderr,
                "pdocker-vulkan-icd: SPIR-V probe replay rejected: cannot open probe shader %s\n",
                probe->shader_path);
        return rc;
    }
    struct stat st;
    if (fstat(probe->shader_fd, &st) != 0) {
        int rc = -errno;
        fprintf(stderr,
                "pdocker-vulkan-icd: SPIR-V probe replay rejected: cannot fstat probe shader %s\n",
                probe->shader_path);
        close_spirv_probe_replay(probe);
        return rc;
    }
    if (st.st_size <= 0 || (uint64_t)st.st_size > PDOCKER_VK_MAX_PROBE_SHADER_BYTES) {
        fprintf(stderr,
                "pdocker-vulkan-icd: SPIR-V probe replay rejected: invalid probe shader size=%lld max=%llu\n",
                (long long)st.st_size,
                (unsigned long long)PDOCKER_VK_MAX_PROBE_SHADER_BYTES);
        close_spirv_probe_replay(probe);
        return -EFBIG;
    }
    probe->shader_size = (size_t)st.st_size;
    void *shader_map = mmap(NULL, probe->shader_size, PROT_READ, MAP_PRIVATE, probe->shader_fd, 0);
    if (shader_map == MAP_FAILED) {
        int rc = -errno;
        close_spirv_probe_replay(probe);
        return rc;
    }
    probe->effective_shader_hash = fnv1a64_bytes(shader_map, probe->shader_size);
    munmap(shader_map, probe->shader_size);
    if (probe->effective_shader_hash != expected_effective_hash) {
        fprintf(stderr,
                "pdocker-vulkan-icd: SPIR-V probe replay rejected: effective hash mismatch expected=0x%016llx actual=0x%016llx\n",
                (unsigned long long)expected_effective_hash,
                (unsigned long long)probe->effective_shader_hash);
        close_spirv_probe_replay(probe);
        return -ENOEXEC;
    }

    probe->debug_fd = create_shared_fd(probe->debug_bytes);
    if (probe->debug_fd < 0) {
        int rc = -errno;
        close_spirv_probe_replay(probe);
        return rc ? rc : -ENOMEM;
    }
    int manifest_rc = verify_spirv_probe_manifest_runtime_guard(probe, source_shader_hash);
    if (manifest_rc < 0) {
        close_spirv_probe_replay(probe);
        return manifest_rc;
    }
    probe->enabled = true;
    fprintf(stderr,
            "pdocker-vulkan-icd: SPIR-V probe replay armed: manifest=%s source_hash=0x%016llx effective_hash=0x%016llx debug_set=%u debug_binding=%u debug_bytes=%zu transport=VULKAN_DISPATCH_V4\n",
            (probe->manifest_path && probe->manifest_path[0]) ? probe->manifest_path : "-",
            (unsigned long long)source_shader_hash,
            (unsigned long long)probe->effective_shader_hash,
            probe->debug_set,
            probe->debug_binding,
            probe->debug_bytes);
    return 0;
}

static uint64_t fnv1a64_specialization_hash(
        const VkSpecializationMapEntry *entries,
        size_t entry_count,
        const void *data,
        size_t data_size) {
    uint64_t hash = 1469598103934665603ull;
    hash = fnv1a64_update_bytes(hash, &entry_count, sizeof(entry_count));
    for (size_t i = 0; i < entry_count; ++i) {
        const uint32_t constant_id = entries[i].constantID;
        const size_t size = entries[i].size;
        hash = fnv1a64_update_bytes(hash, &constant_id, sizeof(constant_id));
        hash = fnv1a64_update_bytes(hash, &size, sizeof(size));
        if (data &&
            entries[i].offset <= data_size &&
            size <= data_size - entries[i].offset) {
            hash = fnv1a64_update_bytes(
                hash,
                (const unsigned char *)data + entries[i].offset,
                size);
        } else {
            const uint32_t invalid_offset = entries[i].offset;
            hash = fnv1a64_update_bytes(hash, &invalid_offset, sizeof(invalid_offset));
        }
    }
    return hash;
}

static bool dispatch_lifecycle_log_enabled(void) {
    return trace_allocations() ||
           getenv("PDOCKER_VULKAN_ICD_DEBUG") ||
           env_truthy_default("PDOCKER_GPU_DISPATCH_PROFILE_LOG", false);
}

static bool reconcile_api_trace_requested(void) {
    return env_enabled("PDOCKER_VULKAN_RECONCILE_API_TRACE");
}

static bool reconcile_api_evidence_log_enabled(void) {
    return reconcile_api_trace_requested() ||
           env_truthy_default("PDOCKER_GPU_DISPATCH_PROFILE_LOG", false) ||
           env_truthy_default("PDOCKER_GPU_DISPATCH_PROFILE_RESPONSE", false);
}

static void trace_vulkan_command_length_warning(uint64_t dispatch_id,
                                                size_t command_len,
                                                size_t command_max,
                                                const char *stage,
                                                bool fail_closed) {
    if (command_max == 0) return;
    const bool near_max = command_len >= (command_max * 9) / 10;
    if (!fail_closed && !near_max) return;
    fprintf(stderr,
            "pdocker-vulkan-icd: reconcile api trace: "
            "{\"component\":\"icd\",\"event\":\"command_length_warning\","
            "\"dispatch_id\":%llu,\"stage\":\"%s\",\"command_len\":%zu,"
            "\"command_max\":%zu,\"near_max\":%s,\"fail_closed\":%s}\n",
            (unsigned long long)dispatch_id,
            stage ? stage : "unknown",
            command_len,
            command_max,
            near_max ? "true" : "false",
            fail_closed ? "true" : "false");
    fflush(stderr);
}

static void trace_vulkan_reconcile_evidence(
        uint64_t dispatch_id,
        size_t raw_command_len,
        uint64_t raw_command_hash,
        size_t core_command_len,
        uint64_t core_command_hash,
        size_t shader_size,
        uint64_t shader_hash,
        uint32_t push_size,
        uint64_t push_hash,
        uint32_t specialization_count,
        size_t specialization_data_size,
        uint64_t specialization_data_hash,
        uint64_t specialization_hash,
        uint32_t dispatch_x,
        uint32_t dispatch_y,
        uint32_t dispatch_z,
        size_t binding_count,
        uint64_t descriptor_hash,
        uint64_t dispatch_hash,
        const uint32_t *api_descriptor_sets,
        const uint32_t *api_descriptor_array_elements,
        const uint32_t *bindings,
        const VkDeviceSize *offsets,
        const size_t *sizes,
        const VkDeviceSize *api_offsets,
        const VkDeviceSize *api_ranges,
        const size_t *api_buffer_sizes,
        const uint32_t *api_descriptor_types,
        const uint32_t *api_dynamic_flags,
        const VkDeviceSize *api_dynamic_offsets,
        const VkDeviceSize *api_memory_offsets,
        const size_t *api_memory_sizes,
        const uintptr_t *api_memory_ids,
        const uintptr_t *api_buffer_ids) {
    if (!reconcile_api_evidence_log_enabled()) return;
    fprintf(stderr,
            "pdocker-vulkan-icd: reconcile api trace: "
            "{\"component\":\"icd\",\"event\":\"send_evidence\","
            "\"dispatch_id\":%llu,"
            "\"raw_command_len\":%zu,\"raw_command_hash\":\"0x%016llx\","
            "\"core_command_len\":%zu,\"core_command_hash\":\"0x%016llx\","
            "\"shader_size\":%zu,\"shader_hash\":\"0x%016llx\","
            "\"push_size\":%u,\"push_hash\":\"0x%016llx\","
            "\"specialization_count\":%u,\"specialization_data_size\":%zu,"
            "\"specialization_data_hash\":\"0x%016llx\","
            "\"specialization_hash\":\"0x%016llx\","
            "\"dispatch_x\":%u,\"dispatch_y\":%u,\"dispatch_z\":%u,"
            "\"dispatch_hash\":\"0x%016llx\","
            "\"binding_count\":%zu,\"descriptor_hash\":\"0x%016llx\","
            "\"bindings\":[",
            (unsigned long long)dispatch_id,
            raw_command_len,
            (unsigned long long)raw_command_hash,
            core_command_len,
            (unsigned long long)core_command_hash,
            shader_size,
            (unsigned long long)shader_hash,
            push_size,
            (unsigned long long)push_hash,
            specialization_count,
            specialization_data_size,
            (unsigned long long)specialization_data_hash,
            (unsigned long long)specialization_hash,
            dispatch_x,
            dispatch_y,
            dispatch_z,
            (unsigned long long)dispatch_hash,
            binding_count,
            (unsigned long long)descriptor_hash);
    for (size_t i = 0; i < binding_count; ++i) {
        fprintf(stderr,
                "%s{\"set\":%u,\"binding\":%u,\"array\":%u,\"offset\":%llu,\"size\":%zu,"
                "\"api_offset\":%llu,\"api_range\":%llu,"
                "\"api_buffer_size\":%zu,\"api_descriptor_type\":%u,"
                "\"api_dynamic\":%u,\"api_dynamic_offset\":%llu,\"api_memory_offset\":%llu,"
                "\"api_memory_size\":%zu,\"api_memory_id\":%llu,"
                "\"api_buffer_id\":%llu}",
                i ? "," : "",
                api_descriptor_sets[i],
                bindings[i],
                api_descriptor_array_elements[i],
                (unsigned long long)offsets[i],
                sizes[i],
                (unsigned long long)api_offsets[i],
                (unsigned long long)api_ranges[i],
                api_buffer_sizes[i],
                api_descriptor_types[i],
                api_dynamic_flags[i],
                (unsigned long long)api_dynamic_offsets[i],
                (unsigned long long)api_memory_offsets[i],
                api_memory_sizes[i],
                (unsigned long long)api_memory_ids[i],
                (unsigned long long)api_buffer_ids[i]);
    }
    fprintf(stderr, "]}\n");
    fflush(stderr);
}

static int frame_append_bytes(unsigned char *frame,
                              size_t frame_capacity,
                              size_t *cursor,
                              const void *data,
                              size_t size,
                              uint64_t *offset_out) {
    if (!frame || !cursor || !offset_out) return -EINVAL;
    size_t aligned = align_size_8(*cursor);
    if (aligned > frame_capacity || size > frame_capacity - aligned) return -EMSGSIZE;
    *offset_out = (uint64_t)aligned;
    if (size > 0 && data) memcpy(frame + aligned, data, size);
    *cursor = aligned + size;
    return 0;
}

static int send_vulkan_dispatch_v5_frame_with_fds(
        int socket_fd,
        const unsigned char *frame,
        size_t frame_size,
        const int *fds,
        size_t fd_count) {
    if (socket_fd < 0 || !frame || frame_size < sizeof(PdockerGpuVulkanDispatchV5FrameHeader) ||
        !fds || fd_count == 0 || fd_count > PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_FDS) {
        return -EINVAL;
    }
    char control[CMSG_SPACE(sizeof(int) * PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_FDS)];
    struct iovec iov;
    struct msghdr msg;
    memset(control, 0, sizeof(control));
    memset(&iov, 0, sizeof(iov));
    memset(&msg, 0, sizeof(msg));
    iov.iov_base = (void *)frame;
    iov.iov_len = sizeof(PdockerGpuVulkanDispatchV5FrameHeader);
    msg.msg_iov = &iov;
    msg.msg_iovlen = 1;
    msg.msg_control = control;
    msg.msg_controllen = sizeof(control);
    struct cmsghdr *cmsg = CMSG_FIRSTHDR(&msg);
    cmsg->cmsg_level = SOL_SOCKET;
    cmsg->cmsg_type = SCM_RIGHTS;
    cmsg->cmsg_len = CMSG_LEN(sizeof(int) * fd_count);
    memcpy(CMSG_DATA(cmsg), fds, sizeof(int) * fd_count);
    msg.msg_controllen = CMSG_SPACE(sizeof(int) * fd_count);
    if (sendmsg(socket_fd, &msg, 0) < 0) return -errno;
    return write_exact_fd(socket_fd,
                          frame + sizeof(PdockerGpuVulkanDispatchV5FrameHeader),
                          frame_size - sizeof(PdockerGpuVulkanDispatchV5FrameHeader));
}


static int send_vulkan_graphics_v6_frame_with_fds(
        int socket_fd,
        const unsigned char *frame,
        size_t frame_size,
        const int *fds,
        size_t fd_count) {
    if (socket_fd < 0 || !frame || frame_size < sizeof(PdockerGpuVulkanGraphicsV6FrameHeader) ||
        fd_count > PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_FDS || (fd_count > 0 && !fds)) {
        return -EINVAL;
    }
    char control[CMSG_SPACE(sizeof(int) * PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_FDS)];
    struct iovec iov;
    struct msghdr msg;
    memset(control, 0, sizeof(control));
    memset(&iov, 0, sizeof(iov));
    memset(&msg, 0, sizeof(msg));
    iov.iov_base = (void *)frame;
    iov.iov_len = sizeof(PdockerGpuVulkanGraphicsV6FrameHeader);
    msg.msg_iov = &iov;
    msg.msg_iovlen = 1;
    if (fd_count > 0) {
        msg.msg_control = control;
        msg.msg_controllen = sizeof(control);
        struct cmsghdr *cmsg = CMSG_FIRSTHDR(&msg);
        cmsg->cmsg_level = SOL_SOCKET;
        cmsg->cmsg_type = SCM_RIGHTS;
        cmsg->cmsg_len = CMSG_LEN(sizeof(int) * fd_count);
        memcpy(CMSG_DATA(cmsg), fds, sizeof(int) * fd_count);
        msg.msg_controllen = CMSG_SPACE(sizeof(int) * fd_count);
    }
    if (sendmsg(socket_fd, &msg, 0) < 0) return -errno;
    return write_exact_fd(socket_fd,
                          frame + sizeof(PdockerGpuVulkanGraphicsV6FrameHeader),
                          frame_size - sizeof(PdockerGpuVulkanGraphicsV6FrameHeader));
}

static uint32_t float_bits_u32(float value) {
    uint32_t bits = 0;
    memcpy(&bits, &value, sizeof(bits));
    return bits;
}


static int send_empty_vulkan_graphics_v6_1_validation_frame(void) {
    int socket_fd = connect_queue();
    if (socket_fd < 0) return socket_fd;
    PdockerGpuVulkanGraphicsV61FrameHeader frame;
    memset(&frame, 0, sizeof(frame));
    PdockerGpuVulkanGraphicsV6FrameHeader *header = &frame.base;
    memcpy(header->magic, PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAGIC, 8);
    header->header_size = sizeof(frame);
    header->abi_major = PDOCKER_GPU_VULKAN_GRAPHICS_V6_ABI_MAJOR;
    header->abi_minor = PDOCKER_GPU_VULKAN_GRAPHICS_V61_ABI_MINOR;
    header->command = PDOCKER_GPU_VULKAN_GRAPHICS_V6_COMMAND_SUBMIT;
    header->frame_size = sizeof(frame);
    header->submit_id = __sync_add_and_fetch(&g_generic_dispatch_sequence, 1);
    header->resource_entry_size = sizeof(PdockerGpuVulkanDispatchV5ResourceEntry);
    header->resource_schema_hash = PDOCKER_GPU_VULKAN_DISPATCH_V5_RESOURCE_SCHEMA_HASH;
    header->descriptor_entry_size = sizeof(PdockerGpuVulkanDispatchV5DescriptorObjectEntry);
    header->descriptor_schema_hash = PDOCKER_GPU_VULKAN_DISPATCH_V5_DESCRIPTOR_OBJECT_SCHEMA_HASH;
    header->image_entry_size = sizeof(PdockerGpuVulkanDispatchV5ImageEntry);
    header->image_schema_hash = PDOCKER_GPU_VULKAN_DISPATCH_V5_IMAGE_SCHEMA_HASH;
    header->image_view_entry_size = sizeof(PdockerGpuVulkanDispatchV5ImageViewEntry);
    header->image_view_schema_hash = PDOCKER_GPU_VULKAN_DISPATCH_V5_IMAGE_VIEW_SCHEMA_HASH;
    header->sampler_entry_size = sizeof(PdockerGpuVulkanDispatchV5SamplerEntry);
    header->sampler_schema_hash = PDOCKER_GPU_VULKAN_DISPATCH_V5_SAMPLER_SCHEMA_HASH;
    header->shader_stage_entry_size = sizeof(PdockerGpuVulkanGraphicsV6ShaderStageEntry);
    header->shader_stage_schema_hash = PDOCKER_GPU_VULKAN_GRAPHICS_V6_SHADER_STAGE_SCHEMA_HASH;
    header->pipeline_entry_size = sizeof(PdockerGpuVulkanGraphicsV6PipelineEntry);
    header->pipeline_schema_hash = PDOCKER_GPU_VULKAN_GRAPHICS_V6_PIPELINE_SCHEMA_HASH;
    header->vertex_binding_entry_size = sizeof(PdockerGpuVulkanGraphicsV6VertexBindingEntry);
    header->vertex_binding_schema_hash = PDOCKER_GPU_VULKAN_GRAPHICS_V6_VERTEX_BINDING_SCHEMA_HASH;
    header->vertex_attribute_entry_size = sizeof(PdockerGpuVulkanGraphicsV6VertexAttributeEntry);
    header->vertex_attribute_schema_hash = PDOCKER_GPU_VULKAN_GRAPHICS_V6_VERTEX_ATTRIBUTE_SCHEMA_HASH;
    header->attachment_entry_size = sizeof(PdockerGpuVulkanGraphicsV6AttachmentEntry);
    header->attachment_schema_hash = PDOCKER_GPU_VULKAN_GRAPHICS_V6_ATTACHMENT_SCHEMA_HASH;
    header->dynamic_state_entry_size = sizeof(PdockerGpuVulkanGraphicsV6DynamicStateEntry);
    header->dynamic_state_schema_hash = PDOCKER_GPU_VULKAN_GRAPHICS_V6_DYNAMIC_STATE_SCHEMA_HASH;
    header->command_entry_size = sizeof(PdockerGpuVulkanGraphicsV6CommandEntry);
    header->command_schema_hash = PDOCKER_GPU_VULKAN_GRAPHICS_V6_COMMAND_SCHEMA_HASH;
    frame.v61.dynamic_offset_entry_size = sizeof(PdockerGpuVulkanGraphicsV61DynamicOffsetEntry);
    frame.v61.dynamic_offset_schema_hash = PDOCKER_GPU_VULKAN_GRAPHICS_V61_DYNAMIC_OFFSET_SCHEMA_HASH;
    frame.v61.push_constant_metadata_entry_size = sizeof(PdockerGpuVulkanGraphicsV61PushConstantMetadataEntry);
    frame.v61.push_constant_metadata_schema_hash = PDOCKER_GPU_VULKAN_GRAPHICS_V61_PUSH_CONSTANT_METADATA_SCHEMA_HASH;
    frame.v61.image_barrier_entry_size = sizeof(PdockerGpuVulkanGraphicsV61ImageBarrierEntry);
    frame.v61.image_barrier_schema_hash = PDOCKER_GPU_VULKAN_GRAPHICS_V61_IMAGE_BARRIER_SCHEMA_HASH;
    frame.v61.memory_barrier_entry_size = sizeof(PdockerGpuVulkanGraphicsV61MemoryBarrierEntry);
    frame.v61.memory_barrier_schema_hash = PDOCKER_GPU_VULKAN_GRAPHICS_V61_MEMORY_BARRIER_SCHEMA_HASH;
    frame.v61.buffer_barrier_entry_size = sizeof(PdockerGpuVulkanGraphicsV61BufferBarrierEntry);
    frame.v61.buffer_barrier_schema_hash = PDOCKER_GPU_VULKAN_GRAPHICS_V61_BUFFER_BARRIER_SCHEMA_HASH;
    frame.v61.extension_hash = 1469598103934665603ull;
    header->payload_hash = 1469598103934665603ull;
    header->frame_hash = fnv1a64_bytes(&frame, sizeof(frame));
    int rc = send_vulkan_graphics_v6_frame_with_fds(
        socket_fd, (const unsigned char *)&frame, sizeof(frame), NULL, 0);
    if (rc == 0) rc = read_dispatch_response_status(socket_fd, "VULKAN_GRAPHICS_V6.1");
    close(socket_fd);
    return rc;
}


static int find_graphics_pipeline_index(
        PdockerVkPipeline *const *pipelines,
        size_t count,
        const PdockerVkPipeline *pipeline) {
    for (size_t i = 0; i < count; ++i) {
        if (pipelines[i] == pipeline) return (int)i;
    }
    return -1;
}

static int find_graphics_memory_resource_index(
        PdockerVkMemory *const *memories,
        const uint32_t *resource_indices,
        size_t count,
        const PdockerVkMemory *memory) {
    for (size_t i = 0; i < count; ++i) {
        if (memories[i] == memory) return (int)resource_indices[i];
    }
    return -1;
}

static int find_graphics_buffer_resource_index(
        PdockerVkBuffer *const *buffers,
        const uint32_t *resource_indices,
        size_t count,
        const PdockerVkBuffer *buffer) {
    for (size_t i = 0; i < count; ++i) {
        if (buffers[i] == buffer) return (int)resource_indices[i];
    }
    return -1;
}

static int collect_graphics_memory_resource(
        PdockerGpuVulkanDispatchV5ResourceEntry *resources,
        size_t *resource_count,
        PdockerVkMemory **memory_objects,
        uint32_t *memory_resource_indices,
        size_t *memory_count,
        int *fds,
        size_t *fd_count,
        PdockerVkMemory *memory,
        uint64_t generation) {
    if (!resources || !resource_count || !memory_objects || !memory_resource_indices ||
        !memory_count || !fds || !fd_count || !memory) {
        return -EINVAL;
    }
    int existing = find_graphics_memory_resource_index(
        memory_objects, memory_resource_indices, *memory_count, memory);
    if (existing >= 0) return existing;
    if (*resource_count >= PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_RESOURCES ||
        *memory_count >= PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_RESOURCES ||
        *fd_count >= PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_FDS ||
        memory->fd < 0 || memory->size == 0) {
        return -E2BIG;
    }
    uint32_t index = (uint32_t)(*resource_count)++;
    PdockerGpuVulkanDispatchV5ResourceEntry *entry = &resources[index];
    memset(entry, 0, sizeof(*entry));
    entry->resource_type = PDOCKER_GPU_V5_RESOURCE_TYPE_MEMORY;
    entry->resource_flags = PDOCKER_GPU_V5_RESOURCE_FLAG_HOST_FD_BACKED |
                            PDOCKER_GPU_V5_RESOURCE_FLAG_MUTABLE;
    entry->resource_id = (uint64_t)(uintptr_t)memory;
    entry->parent_resource_index = PDOCKER_GPU_V5_RESOURCE_PARENT_NONE;
    entry->fd_index = (uint32_t)(*fd_count);
    entry->size = (uint64_t)memory->size;
    entry->memory_property_flags = memory->property_flags;
    entry->generation = generation;
    fds[(*fd_count)++] = memory->fd;
    memory_objects[*memory_count] = memory;
    memory_resource_indices[*memory_count] = index;
    (*memory_count)++;
    return (int)index;
}

static int collect_graphics_buffer_resource(
        PdockerGpuVulkanDispatchV5ResourceEntry *resources,
        size_t *resource_count,
        PdockerVkMemory **memory_objects,
        uint32_t *memory_resource_indices,
        size_t *memory_count,
        PdockerVkBuffer **buffer_objects,
        uint32_t *buffer_resource_indices,
        size_t *buffer_count,
        int *fds,
        size_t *fd_count,
        PdockerVkBuffer *buffer,
        uint64_t generation) {
    if (!resources || !resource_count || !buffer_objects || !buffer_resource_indices ||
        !buffer_count || !buffer || !buffer->memory || buffer->size == 0) {
        return -EINVAL;
    }
    int existing = find_graphics_buffer_resource_index(
        buffer_objects, buffer_resource_indices, *buffer_count, buffer);
    if (existing >= 0) return existing;
    int memory_index = collect_graphics_memory_resource(
        resources, resource_count, memory_objects, memory_resource_indices, memory_count,
        fds, fd_count, buffer->memory, generation);
    if (memory_index < 0) return memory_index;
    if (*resource_count >= PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_RESOURCES ||
        *buffer_count >= PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_RESOURCES) {
        return -E2BIG;
    }
    uint32_t index = (uint32_t)(*resource_count)++;
    PdockerGpuVulkanDispatchV5ResourceEntry *entry = &resources[index];
    memset(entry, 0, sizeof(*entry));
    entry->resource_type = PDOCKER_GPU_V5_RESOURCE_TYPE_BUFFER;
    entry->resource_flags = PDOCKER_GPU_V5_RESOURCE_FLAG_MUTABLE;
    entry->resource_id = (uint64_t)(uintptr_t)buffer;
    entry->parent_resource_index = (uint32_t)memory_index;
    entry->fd_index = PDOCKER_GPU_V5_RESOURCE_FD_NONE;
    entry->memory_offset = (uint64_t)buffer->memory_offset;
    entry->size = (uint64_t)buffer->size;
    entry->usage = 0;
    entry->generation = generation;
    buffer_objects[*buffer_count] = buffer;
    buffer_resource_indices[*buffer_count] = index;
    (*buffer_count)++;
    return (int)index;
}

static int find_image_table_index(PdockerVkImage *const *images,
                                  size_t count,
                                  const PdockerVkImage *image);
static int find_image_view_table_index(PdockerVkImageView *const *views,
                                       size_t count,
                                       const PdockerVkImageView *view);
static int find_sampler_table_index(PdockerVkSampler *const *samplers,
                                    size_t count,
                                    const PdockerVkSampler *sampler);
static bool descriptor_type_requires_image_view(VkDescriptorType type);
static bool descriptor_type_requires_sampler(VkDescriptorType type);
static bool descriptor_type_supported_by_v4_transport(VkDescriptorType type);
static bool descriptor_type_supported_by_v5_object_transport(VkDescriptorType type);
static int validate_descriptor_transport_shape(
        const PdockerVkDescriptorBinding *binding,
        uint32_t set_index,
        uint32_t binding_index,
        size_t *effective_size);
static bool image_mip_extent(const PdockerVkImage *image,
                             uint32_t mip_level,
                             VkExtent3D *out);

static int collect_graphics_image_entry(
        PdockerGpuVulkanDispatchV5ImageEntry *image_entries,
        PdockerVkImage **image_objects,
        size_t *image_count,
        PdockerGpuVulkanDispatchV5ResourceEntry *resources,
        size_t *resource_count,
        PdockerVkMemory **memory_objects,
        uint32_t *memory_resource_indices,
        size_t *memory_count,
        int *fds,
        size_t *fd_count,
        PdockerVkImage *image,
        uint64_t generation) {
    if (!image_entries || !image_objects || !image_count || !resources || !resource_count ||
        !memory_objects || !memory_resource_indices || !memory_count || !fds || !fd_count ||
        !image || !image->memory) {
        return -EINVAL;
    }
    int existing = find_image_table_index(image_objects, *image_count, image);
    if (existing >= 0) return existing;
    if (*image_count >= PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_IMAGES) return -E2BIG;
    if (image->memory->fd < 0) return -EINVAL;
    if (image->memory_offset > (VkDeviceSize)image->memory->size ||
        image->requirements_size > (VkDeviceSize)image->memory->size - image->memory_offset) {
        return -ERANGE;
    }
    int memory_index = collect_graphics_memory_resource(
        resources, resource_count, memory_objects, memory_resource_indices, memory_count,
        fds, fd_count, image->memory, generation);
    if (memory_index < 0) return memory_index;
    uint32_t index = (uint32_t)(*image_count)++;
    PdockerGpuVulkanDispatchV5ImageEntry *entry = &image_entries[index];
    memset(entry, 0, sizeof(*entry));
    entry->flags =
        ((image->flags & VK_IMAGE_CREATE_MUTABLE_FORMAT_BIT)
            ? PDOCKER_GPU_V5_IMAGE_FLAG_MUTABLE_FORMAT : 0u) |
        ((image->flags & VK_IMAGE_CREATE_CUBE_COMPATIBLE_BIT)
            ? PDOCKER_GPU_V5_IMAGE_FLAG_CUBE_COMPATIBLE : 0u) |
        ((image->flags & VK_IMAGE_CREATE_ALIAS_BIT)
            ? PDOCKER_GPU_V5_IMAGE_FLAG_ALIAS : 0u);
    entry->image_type = image->image_type;
    entry->image_id = (uint64_t)(uintptr_t)image;
    entry->memory_resource_index = (uint32_t)memory_index;
    entry->memory_offset = (uint64_t)image->memory_offset;
    entry->memory_size = (uint64_t)image->requirements_size;
    entry->format = image->format;
    entry->extent_width = image->extent.width;
    entry->extent_height = image->extent.height;
    entry->extent_depth = image->extent.depth;
    entry->mip_levels = image->mip_levels;
    entry->array_layers = image->array_layers;
    entry->samples = image->samples;
    entry->tiling = image->tiling;
    entry->usage = image->usage;
    entry->create_flags = image->flags;
    entry->sharing_mode = image->sharing_mode;
    entry->initial_layout = image->initial_layout;
    entry->generation = image->generation ? image->generation : generation;
    image_objects[index] = image;
    return (int)index;
}

static int collect_graphics_image_view_entry(
        PdockerGpuVulkanDispatchV5ImageViewEntry *image_view_entries,
        PdockerVkImageView **image_view_objects,
        size_t *image_view_count,
        PdockerGpuVulkanDispatchV5ImageEntry *image_entries,
        PdockerVkImage **image_objects,
        size_t *image_count,
        PdockerGpuVulkanDispatchV5ResourceEntry *resources,
        size_t *resource_count,
        PdockerVkMemory **memory_objects,
        uint32_t *memory_resource_indices,
        size_t *memory_count,
        int *fds,
        size_t *fd_count,
        PdockerVkImageView *view,
        uint64_t generation) {
    if (!image_view_entries || !image_view_objects || !image_view_count || !view || !view->image) {
        return -EINVAL;
    }
    int existing = find_image_view_table_index(image_view_objects, *image_view_count, view);
    if (existing >= 0) return existing;
    if (*image_view_count >= PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_IMAGE_VIEWS) return -E2BIG;
    int image_index = collect_graphics_image_entry(
        image_entries, image_objects, image_count,
        resources, resource_count, memory_objects, memory_resource_indices, memory_count,
        fds, fd_count, view->image, generation);
    if (image_index < 0) return image_index;
    uint32_t index = (uint32_t)(*image_view_count)++;
    PdockerGpuVulkanDispatchV5ImageViewEntry *entry = &image_view_entries[index];
    memset(entry, 0, sizeof(*entry));
    entry->view_type = view->view_type;
    entry->view_id = (uint64_t)(uintptr_t)view;
    entry->image_index = (uint32_t)image_index;
    entry->format = view->format;
    entry->component_r = view->components.r;
    entry->component_g = view->components.g;
    entry->component_b = view->components.b;
    entry->component_a = view->components.a;
    entry->aspect_mask = view->subresource_range.aspectMask;
    entry->base_mip_level = view->subresource_range.baseMipLevel;
    entry->level_count = view->subresource_range.levelCount;
    entry->base_array_layer = view->subresource_range.baseArrayLayer;
    entry->layer_count = view->subresource_range.layerCount;
    entry->generation = view->generation ? view->generation : generation;
    image_view_objects[index] = view;
    return (int)index;
}

static int collect_graphics_sampler_entry(
        PdockerGpuVulkanDispatchV5SamplerEntry *sampler_entries,
        PdockerVkSampler **sampler_objects,
        size_t *sampler_count,
        PdockerVkSampler *sampler,
        uint64_t generation) {
    if (!sampler_entries || !sampler_objects || !sampler_count || !sampler) return -EINVAL;
    int existing = find_sampler_table_index(sampler_objects, *sampler_count, sampler);
    if (existing >= 0) return existing;
    if (*sampler_count >= PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_SAMPLERS) return -E2BIG;
    uint32_t index = (uint32_t)(*sampler_count)++;
    PdockerGpuVulkanDispatchV5SamplerEntry *entry = &sampler_entries[index];
    memset(entry, 0, sizeof(*entry));
    entry->sampler_id = (uint64_t)(uintptr_t)sampler;
    entry->mag_filter = sampler->mag_filter;
    entry->min_filter = sampler->min_filter;
    entry->mipmap_mode = sampler->mipmap_mode;
    entry->address_mode_u = sampler->address_mode_u;
    entry->address_mode_v = sampler->address_mode_v;
    entry->address_mode_w = sampler->address_mode_w;
    entry->mip_lod_bias_bits = float_bits_u32(sampler->mip_lod_bias);
    entry->anisotropy_enable = sampler->anisotropy_enable;
    entry->max_anisotropy_bits = float_bits_u32(sampler->max_anisotropy);
    entry->compare_enable = sampler->compare_enable;
    entry->compare_op = sampler->compare_op;
    entry->min_lod_bits = float_bits_u32(sampler->min_lod);
    entry->max_lod_bits = float_bits_u32(sampler->max_lod);
    entry->border_color = sampler->border_color;
    entry->unnormalized_coordinates = sampler->unnormalized_coordinates;
    entry->generation = sampler->generation ? sampler->generation : generation;
    sampler_objects[index] = sampler;
    return (int)index;
}

static int append_graphics_attachment_entry(
        PdockerGpuVulkanGraphicsV6AttachmentEntry *attachments,
        size_t *attachment_count,
        PdockerGpuVulkanGraphicsV64ResolveAttachmentEntry *resolve_attachments,
        size_t *resolve_attachment_count,
        bool *need_v64_resolve_attachment,
        unsigned char *frame,
        size_t frame_capacity,
        size_t *cursor,
        uint32_t role,
        const PdockerVkRenderingAttachmentState *src,
        PdockerGpuVulkanDispatchV5ImageEntry *image_entries,
        PdockerVkImage **image_objects,
        size_t *image_count,
        PdockerGpuVulkanDispatchV5ImageViewEntry *image_view_entries,
        PdockerVkImageView **image_view_objects,
        size_t *image_view_count,
        PdockerGpuVulkanDispatchV5ResourceEntry *resources,
        size_t *resource_count,
        PdockerVkMemory **memory_objects,
        uint32_t *memory_resource_indices,
        size_t *memory_count,
        int *fds,
        size_t *fd_count,
        uint64_t generation) {
    if (!attachments || !attachment_count || !frame || !cursor || !src) return -EINVAL;
    if (*attachment_count >= PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_ATTACHMENTS) return -E2BIG;
    if (!src->valid) {
        if (role != PDOCKER_GPU_GRAPHICS_V6_ATTACHMENT_COLOR) return 0;
        PdockerGpuVulkanGraphicsV6AttachmentEntry *entry = &attachments[(*attachment_count)++];
        memset(entry, 0, sizeof(*entry));
        entry->attachment_role = role;
        entry->flags = PDOCKER_GPU_GRAPHICS_V6_ATTACHMENT_UNUSED_SLOT;
        entry->image_view_index = PDOCKER_GPU_V5_DESCRIPTOR_OBJECT_NONE;
        entry->resolve_image_view_index = PDOCKER_GPU_V5_DESCRIPTOR_OBJECT_NONE;
        entry->format = VK_FORMAT_UNDEFINED;
        entry->samples = VK_SAMPLE_COUNT_1_BIT;
        entry->layout = VK_IMAGE_LAYOUT_UNDEFINED;
        entry->load_op = VK_ATTACHMENT_LOAD_OP_DONT_CARE;
        entry->store_op = VK_ATTACHMENT_STORE_OP_DONT_CARE;
        entry->stencil_load_op = VK_ATTACHMENT_LOAD_OP_DONT_CARE;
        entry->stencil_store_op = VK_ATTACHMENT_STORE_OP_DONT_CARE;
        return 0;
    }
    if ((src->resolve_mode == VK_RESOLVE_MODE_NONE) != (src->resolve_image_view == NULL)) {
        return -EOPNOTSUPP;
    }
    if (src->resolve_image_view &&
        (!resolve_attachments || !resolve_attachment_count || !need_v64_resolve_attachment)) {
        return -EINVAL;
    }
    uint32_t view_index = PDOCKER_GPU_V5_DESCRIPTOR_OBJECT_NONE;
    uint32_t resolve_view_index = PDOCKER_GPU_V5_DESCRIPTOR_OBJECT_NONE;
    uint64_t resource_id = 0;
    VkFormat format = VK_FORMAT_UNDEFINED;
    VkSampleCountFlagBits samples = VK_SAMPLE_COUNT_1_BIT;
    if (src->image_view) {
        int collected_view = collect_graphics_image_view_entry(
            image_view_entries, image_view_objects, image_view_count,
            image_entries, image_objects, image_count,
            resources, resource_count, memory_objects, memory_resource_indices,
            memory_count, fds, fd_count, src->image_view, generation);
        if (collected_view < 0) return collected_view;
        view_index = (uint32_t)collected_view;
        resource_id = (uint64_t)(uintptr_t)src->image_view;
        format = src->image_view->format;
        samples = src->image_view->image ? src->image_view->image->samples : VK_SAMPLE_COUNT_1_BIT;
    }
    if (src->resolve_image_view) {
        if (!src->image_view || src->resolve_image_view->format != format) return -EOPNOTSUPP;
        if (src->image_view->image && src->image_view->image->samples == VK_SAMPLE_COUNT_1_BIT) {
            return -EOPNOTSUPP;
        }
        if (src->resolve_image_view->image &&
            src->resolve_image_view->image->samples != VK_SAMPLE_COUNT_1_BIT) {
            return -EOPNOTSUPP;
        }
        if (src->resolve_image_view->subresource_range.aspectMask !=
            src->image_view->subresource_range.aspectMask) {
            return -EOPNOTSUPP;
        }
        if (*resolve_attachment_count >= PDOCKER_GPU_VULKAN_GRAPHICS_V64_MAX_RESOLVE_ATTACHMENTS) {
            return -E2BIG;
        }
        int collected_resolve_view = collect_graphics_image_view_entry(
            image_view_entries, image_view_objects, image_view_count,
            image_entries, image_objects, image_count,
            resources, resource_count, memory_objects, memory_resource_indices,
            memory_count, fds, fd_count, src->resolve_image_view, generation);
        if (collected_resolve_view < 0) return collected_resolve_view;
        resolve_view_index = (uint32_t)collected_resolve_view;
    }
    const uint32_t attachment_index = (uint32_t)*attachment_count;
    PdockerGpuVulkanGraphicsV6AttachmentEntry *entry = &attachments[(*attachment_count)++];
    memset(entry, 0, sizeof(*entry));
    entry->attachment_role = role;
    entry->image_view_index = view_index;
    entry->resolve_image_view_index = resolve_view_index;
    entry->format = format;
    entry->samples = samples;
    entry->layout = src->image_layout;
    entry->load_op = VK_ATTACHMENT_LOAD_OP_DONT_CARE;
    entry->store_op = VK_ATTACHMENT_STORE_OP_DONT_CARE;
    entry->stencil_load_op = VK_ATTACHMENT_LOAD_OP_DONT_CARE;
    entry->stencil_store_op = VK_ATTACHMENT_STORE_OP_DONT_CARE;
    if (role == PDOCKER_GPU_GRAPHICS_V6_ATTACHMENT_STENCIL) {
        entry->stencil_load_op = src->load_op;
        entry->stencil_store_op = src->store_op;
    } else {
        entry->load_op = src->load_op;
        entry->store_op = src->store_op;
    }
    entry->resource_id = resource_id;
    if (src->resolve_image_view) {
        PdockerGpuVulkanGraphicsV64ResolveAttachmentEntry *resolve_entry =
            &resolve_attachments[(*resolve_attachment_count)++];
        memset(resolve_entry, 0, sizeof(*resolve_entry));
        resolve_entry->attachment_index = attachment_index;
        resolve_entry->resolve_mode = src->resolve_mode;
        resolve_entry->resolve_layout = src->resolve_image_layout;
        *need_v64_resolve_attachment = true;
    }
    if (src->load_op == VK_ATTACHMENT_LOAD_OP_CLEAR) {
        int rc = frame_append_bytes(frame, frame_capacity, cursor,
                                    &src->clear_value, sizeof(src->clear_value),
                                    &entry->clear_value_offset);
        if (rc != 0) return rc;
        entry->clear_value_size = sizeof(src->clear_value);
    }
    return 0;
}

static int collect_graphics_attachment_entries(
        PdockerGpuVulkanGraphicsV6AttachmentEntry *attachments,
        size_t *attachment_count,
        PdockerGpuVulkanGraphicsV64ResolveAttachmentEntry *resolve_attachments,
        size_t *resolve_attachment_count,
        bool *need_v64_resolve_attachment,
        unsigned char *frame,
        size_t frame_capacity,
        size_t *cursor,
        const PdockerVkGraphicsRenderingSnapshot *snapshot,
        PdockerGpuVulkanDispatchV5ImageEntry *image_entries,
        PdockerVkImage **image_objects,
        size_t *image_count,
        PdockerGpuVulkanDispatchV5ImageViewEntry *image_view_entries,
        PdockerVkImageView **image_view_objects,
        size_t *image_view_count,
        PdockerGpuVulkanDispatchV5ResourceEntry *resources,
        size_t *resource_count,
        PdockerVkMemory **memory_objects,
        uint32_t *memory_resource_indices,
        size_t *memory_count,
        int *fds,
        size_t *fd_count,
        uint64_t generation) {
    if (!snapshot) return -EINVAL;
    if (snapshot->color_attachment_count > PDOCKER_VK_MAX_STORAGE_BUFFERS) return -E2BIG;
    for (uint32_t i = 0; i < snapshot->color_attachment_count; ++i) {
        int rc = append_graphics_attachment_entry(
            attachments, attachment_count, resolve_attachments, resolve_attachment_count,
            need_v64_resolve_attachment, frame, frame_capacity, cursor,
            PDOCKER_GPU_GRAPHICS_V6_ATTACHMENT_COLOR, &snapshot->color_attachments[i],
            image_entries, image_objects, image_count,
            image_view_entries, image_view_objects, image_view_count,
            resources, resource_count, memory_objects, memory_resource_indices, memory_count,
            fds, fd_count, generation);
        if (rc != 0) return rc;
    }
    int rc = append_graphics_attachment_entry(
        attachments, attachment_count, resolve_attachments, resolve_attachment_count,
        need_v64_resolve_attachment, frame, frame_capacity, cursor,
        PDOCKER_GPU_GRAPHICS_V6_ATTACHMENT_DEPTH, &snapshot->depth_attachment,
        image_entries, image_objects, image_count,
        image_view_entries, image_view_objects, image_view_count,
        resources, resource_count, memory_objects, memory_resource_indices, memory_count,
        fds, fd_count, generation);
    if (rc != 0) return rc;
    return append_graphics_attachment_entry(
        attachments, attachment_count, resolve_attachments, resolve_attachment_count,
        need_v64_resolve_attachment, frame, frame_capacity, cursor,
        PDOCKER_GPU_GRAPHICS_V6_ATTACHMENT_STENCIL, &snapshot->stencil_attachment,
        image_entries, image_objects, image_count,
        image_view_entries, image_view_objects, image_view_count,
        resources, resource_count, memory_objects, memory_resource_indices, memory_count,
        fds, fd_count, generation);
}

static int collect_graphics_descriptor_entries(
        PdockerGpuVulkanDispatchV5DescriptorObjectEntry *descriptors,
        size_t *descriptor_count,
        PdockerGpuVulkanDispatchV5ResourceEntry *resources,
        size_t *resource_count,
        PdockerVkMemory **memory_objects,
        uint32_t *memory_resource_indices,
        size_t *memory_count,
        PdockerVkBuffer **buffer_objects,
        uint32_t *buffer_resource_indices,
        size_t *buffer_count,
        PdockerGpuVulkanDispatchV5ImageEntry *image_entries,
        PdockerVkImage **image_objects,
        size_t *image_count,
        PdockerGpuVulkanDispatchV5ImageViewEntry *image_view_entries,
        PdockerVkImageView **image_view_objects,
        size_t *image_view_count,
        PdockerGpuVulkanDispatchV5SamplerEntry *sampler_entries,
        PdockerVkSampler **sampler_objects,
        size_t *sampler_count,
        int *fds,
        size_t *fd_count,
        const PdockerVkGraphicsDescriptorBindSnapshot *snapshot,
        uint64_t generation,
        uint32_t *dynamic_descriptor_count_out) {
    if (dynamic_descriptor_count_out) *dynamic_descriptor_count_out = 0;
    if (!descriptors || !descriptor_count || !resources || !resource_count ||
        !snapshot || !dynamic_descriptor_count_out) {
        return -EINVAL;
    }
    if (snapshot->descriptor_set_count > PDOCKER_VK_MAX_DESCRIPTOR_SETS ||
        snapshot->first_set > PDOCKER_VK_MAX_DESCRIPTOR_SETS ||
        snapshot->descriptor_set_count > PDOCKER_VK_MAX_DESCRIPTOR_SETS - snapshot->first_set) {
        return -ERANGE;
    }
    uint32_t dynamic_descriptor_count = 0;
    for (uint32_t set_i = 0; set_i < snapshot->descriptor_set_count; ++set_i) {
        uint32_t set_index = snapshot->first_set + set_i;
        if (!snapshot->set_snapshot_used[set_index]) return -EPROTO;
        const PdockerVkDescriptorSet *set = &snapshot->set_snapshots[set_index];
        const PdockerVkDescriptorSetLayout *layout = set->layout;
        if (set->unsupported_descriptor_array || set->unsupported_descriptor_type ||
            (layout && (layout->unsupported_descriptor_array || layout->unsupported_descriptor_type))) {
            return -EOPNOTSUPP;
        }
        uint32_t binding_limit = layout ? layout->storage_binding_count : PDOCKER_VK_MAX_STORAGE_BUFFERS;
        if (binding_limit > PDOCKER_VK_MAX_STORAGE_BUFFERS) return -E2BIG;
        for (uint32_t binding_index = 0; binding_index < binding_limit; ++binding_index) {
            uint32_t array_limit = layout
                ? layout->storage_binding_counts[binding_index]
                : PDOCKER_VK_MAX_DESCRIPTOR_ARRAY_ELEMENTS;
            if (array_limit == 0 && !layout) array_limit = PDOCKER_VK_MAX_DESCRIPTOR_ARRAY_ELEMENTS;
            if (array_limit > PDOCKER_VK_MAX_DESCRIPTOR_ARRAY_ELEMENTS) return -E2BIG;
            for (uint32_t array_element = 0; array_element < array_limit; ++array_element) {
                const PdockerVkDescriptorBinding *binding =
                    &set->storage_buffers[binding_index][array_element];
                if (!binding->buffer && !binding->image_view && !binding->sampler) continue;
                if (descriptor_type_supported_by_v5_object_transport(binding->descriptor_type) ||
                    binding->image_view || binding->sampler) {
                    VkDescriptorType descriptor_type = binding->descriptor_type;
                    if (!descriptor_type_supported_by_v5_object_transport(descriptor_type)) {
                        return -EOPNOTSUPP;
                    }
                    const bool requires_view = descriptor_type_requires_image_view(descriptor_type);
                    const bool requires_sampler = descriptor_type_requires_sampler(descriptor_type);
                    if ((requires_view && !binding->image_view) ||
                        (requires_sampler && !binding->sampler)) {
                        return -EINVAL;
                    }
                    uint32_t view_index = PDOCKER_GPU_V5_DESCRIPTOR_OBJECT_NONE;
                    uint32_t sampler_index = PDOCKER_GPU_V5_DESCRIPTOR_OBJECT_NONE;
                    if (requires_view) {
                        int collected_view = collect_graphics_image_view_entry(
                            image_view_entries, image_view_objects, image_view_count,
                            image_entries, image_objects, image_count,
                            resources, resource_count, memory_objects, memory_resource_indices,
                            memory_count, fds, fd_count, binding->image_view, generation);
                        if (collected_view < 0) return collected_view;
                        view_index = (uint32_t)collected_view;
                    }
                    if (requires_sampler) {
                        int collected_sampler = collect_graphics_sampler_entry(
                            sampler_entries, sampler_objects, sampler_count, binding->sampler, generation);
                        if (collected_sampler < 0) return collected_sampler;
                        sampler_index = (uint32_t)collected_sampler;
                    }
                    if (*descriptor_count >= PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_DESCRIPTORS) {
                        return -E2BIG;
                    }
                    PdockerGpuVulkanDispatchV5DescriptorObjectEntry *descriptor =
                        &descriptors[(*descriptor_count)++];
                    memset(descriptor, 0, sizeof(*descriptor));
                    descriptor->descriptor_set = set_index;
                    descriptor->binding = binding_index;
                    descriptor->array_element = array_element;
                    descriptor->descriptor_type = descriptor_type;
                    descriptor->descriptor_flags = array_element ? PDOCKER_GPU_V5_DESCRIPTOR_FLAG_ARRAY_ENTRY : 0u;
                    descriptor->access_flags = descriptor_type == VK_DESCRIPTOR_TYPE_STORAGE_IMAGE
                        ? (PDOCKER_GPU_V5_ACCESS_READ | PDOCKER_GPU_V5_ACCESS_WRITE)
                        : PDOCKER_GPU_V5_ACCESS_READ;
                    descriptor->resource_index = PDOCKER_GPU_V5_DESCRIPTOR_OBJECT_NONE;
                    descriptor->image_view_index = view_index;
                    descriptor->sampler_index = sampler_index;
                    descriptor->image_layout = binding->image_layout;
                    descriptor->resource_id =
                        view_index != PDOCKER_GPU_V5_DESCRIPTOR_OBJECT_NONE
                            ? (uint64_t)(uintptr_t)image_view_objects[view_index]
                            : (uint64_t)(uintptr_t)sampler_objects[sampler_index];
                    continue;
                }
                if (!descriptor_type_supported_by_v4_transport(binding->descriptor_type)) {
                    return -EOPNOTSUPP;
                }
                size_t bytes = 0;
                int rc = validate_descriptor_transport_shape(
                    binding, set_index, binding_index, &bytes);
                if (rc < 0) return rc;
                int buffer_index = collect_graphics_buffer_resource(
                    resources, resource_count,
                    memory_objects, memory_resource_indices, memory_count,
                    buffer_objects, buffer_resource_indices, buffer_count,
                    fds, fd_count, binding->buffer, generation);
                if (buffer_index < 0) return buffer_index;
                if (*descriptor_count >= PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_DESCRIPTORS) {
                    return -E2BIG;
                }
                PdockerGpuVulkanDispatchV5DescriptorObjectEntry *descriptor =
                    &descriptors[(*descriptor_count)++];
                memset(descriptor, 0, sizeof(*descriptor));
                descriptor->descriptor_set = set_index;
                descriptor->binding = binding_index;
                descriptor->array_element = array_element;
                descriptor->descriptor_type = binding->descriptor_type;
                descriptor->descriptor_flags =
                    (binding->dynamic ? PDOCKER_GPU_V5_DESCRIPTOR_FLAG_DYNAMIC : 0u) |
                    (binding->range == VK_WHOLE_SIZE ? PDOCKER_GPU_V5_DESCRIPTOR_FLAG_WHOLE_SIZE : 0u) |
                    (array_element ? PDOCKER_GPU_V5_DESCRIPTOR_FLAG_ARRAY_ENTRY : 0u);
                descriptor->access_flags = PDOCKER_GPU_V5_ACCESS_READ | PDOCKER_GPU_V5_ACCESS_WRITE;
                descriptor->resource_index = (uint32_t)buffer_index;
                descriptor->image_view_index = PDOCKER_GPU_V5_DESCRIPTOR_OBJECT_NONE;
                descriptor->sampler_index = PDOCKER_GPU_V5_DESCRIPTOR_OBJECT_NONE;
                descriptor->image_layout = 0;
                descriptor->resource_id = (uint64_t)(uintptr_t)binding->buffer;
                descriptor->buffer_offset = (uint64_t)binding->base_offset;
                descriptor->range = (uint64_t)binding->range;
                descriptor->transfer_offset = (uint64_t)binding->offset;
                descriptor->transfer_size = (uint64_t)bytes;
                descriptor->dynamic_offset = (uint64_t)binding->dynamic_offset;
                if (binding->dynamic) dynamic_descriptor_count++;
            }
        }
    }
    *dynamic_descriptor_count_out = dynamic_descriptor_count;
    return 0;
}

static int send_recorded_vulkan_graphics_v6_1_frame(const PdockerVkCommandBuffer *cmd) {
    if (!cmd || cmd->graphics_command_op_count == 0) {
        return send_empty_vulkan_graphics_v6_1_validation_frame();
    }
    int socket_fd = connect_queue();
    if (socket_fd < 0) return socket_fd;

    PdockerVkPipeline *pipeline_objects[PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_PIPELINES];
    PdockerVkMemory *memory_objects[PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_RESOURCES];
    uint32_t memory_resource_indices[PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_RESOURCES];
    PdockerVkBuffer *buffer_objects[PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_RESOURCES];
    uint32_t buffer_resource_indices[PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_RESOURCES];
    PdockerVkImage *image_objects[PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_IMAGES];
    PdockerVkImageView *image_view_objects[PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_IMAGE_VIEWS];
    PdockerVkSampler *sampler_objects[PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_SAMPLERS];
    PdockerGpuVulkanDispatchV5ResourceEntry resources[PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_RESOURCES];
    PdockerGpuVulkanDispatchV5DescriptorObjectEntry descriptors[PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_DESCRIPTORS];
    PdockerGpuVulkanDispatchV5ImageEntry image_entries[PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_IMAGES];
    PdockerGpuVulkanDispatchV5ImageViewEntry image_view_entries[PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_IMAGE_VIEWS];
    PdockerGpuVulkanDispatchV5SamplerEntry sampler_entries[PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_SAMPLERS];
    PdockerGpuVulkanGraphicsV6ShaderStageEntry shader_stages[PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_SHADER_STAGES];
    PdockerGpuVulkanGraphicsV6PipelineEntry pipelines[PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_PIPELINES];
    PdockerGpuVulkanGraphicsV6VertexBindingEntry vertex_bindings[PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_VERTEX_BINDINGS];
    PdockerGpuVulkanGraphicsV6VertexAttributeEntry vertex_attributes[PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_VERTEX_ATTRIBUTES];
    PdockerGpuVulkanGraphicsV6AttachmentEntry attachments[PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_ATTACHMENTS];
    PdockerGpuVulkanGraphicsV6DynamicStateEntry dynamic_states[PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_DYNAMIC_STATES];
    PdockerGpuVulkanGraphicsV6CommandEntry commands[PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_COMMANDS];
    PdockerGpuVulkanGraphicsV61DynamicOffsetEntry dynamic_offsets[PDOCKER_GPU_VULKAN_GRAPHICS_V61_MAX_DYNAMIC_OFFSETS];
    PdockerGpuVulkanGraphicsV61PushConstantMetadataEntry push_metadata[PDOCKER_GPU_VULKAN_GRAPHICS_V61_MAX_PUSH_CONSTANT_METADATA];
    PdockerGpuVulkanGraphicsV61ImageBarrierEntry image_barriers[PDOCKER_GPU_VULKAN_GRAPHICS_V61_MAX_IMAGE_BARRIERS];
    PdockerGpuVulkanGraphicsV61MemoryBarrierEntry memory_barriers[PDOCKER_GPU_VULKAN_GRAPHICS_V61_MAX_MEMORY_BARRIERS];
    PdockerGpuVulkanGraphicsV61BufferBarrierEntry buffer_barriers[PDOCKER_GPU_VULKAN_GRAPHICS_V61_MAX_BUFFER_BARRIERS];
    PdockerGpuVulkanGraphicsV62SpecializationEntry specialization_entries[PDOCKER_GPU_VULKAN_GRAPHICS_V62_MAX_SPECIALIZATION_ENTRIES];
    PdockerGpuVulkanGraphicsV63DepthStencilStateEntry depth_stencil_states[PDOCKER_GPU_VULKAN_GRAPHICS_V63_MAX_DEPTH_STENCIL_STATES];
    PdockerGpuVulkanGraphicsV64ResolveAttachmentEntry resolve_attachments[PDOCKER_GPU_VULKAN_GRAPHICS_V64_MAX_RESOLVE_ATTACHMENTS];
    PdockerGpuVulkanGraphicsV65StaticPipelineStateEntry static_pipeline_states[PDOCKER_GPU_VULKAN_GRAPHICS_V65_MAX_STATIC_PIPELINE_STATES];
    PdockerGpuVulkanGraphicsV66ColorBlendStateEntry color_blend_states[PDOCKER_GPU_VULKAN_GRAPHICS_V66_MAX_COLOR_BLEND_STATES];
    PdockerGpuVulkanGraphicsV66ColorBlendAttachmentEntry color_blend_attachments[PDOCKER_GPU_VULKAN_GRAPHICS_V66_MAX_COLOR_BLEND_ATTACHMENTS];
    PdockerGpuVulkanGraphicsV67ViewportScissorStateEntry viewport_scissor_states[PDOCKER_GPU_VULKAN_GRAPHICS_V67_MAX_VIEWPORT_SCISSOR_STATES];
    PdockerGpuVulkanGraphicsV67ViewportEntry viewport_entries[PDOCKER_GPU_VULKAN_GRAPHICS_V67_MAX_VIEWPORTS];
    PdockerGpuVulkanGraphicsV67ScissorEntry scissor_entries[PDOCKER_GPU_VULKAN_GRAPHICS_V67_MAX_SCISSORS];
    PdockerGpuVulkanGraphicsV68IndirectDrawEntry indirect_draws[PDOCKER_GPU_VULKAN_GRAPHICS_V68_MAX_INDIRECT_DRAWS];
    PdockerGpuVulkanGraphicsV69BufferCopyEntry buffer_copies[PDOCKER_GPU_VULKAN_GRAPHICS_V69_MAX_BUFFER_COPIES];
    PdockerGpuVulkanGraphicsV610BufferImageCopyEntry buffer_image_copies[PDOCKER_GPU_VULKAN_GRAPHICS_V610_MAX_BUFFER_IMAGE_COPIES];
    PdockerGpuVulkanGraphicsV610ImageCopyEntry image_copies[PDOCKER_GPU_VULKAN_GRAPHICS_V610_MAX_IMAGE_COPIES];
    PdockerGpuVulkanGraphicsV611FillBufferEntry fill_buffers[PDOCKER_GPU_VULKAN_GRAPHICS_V611_MAX_FILL_BUFFERS];
    PdockerGpuVulkanGraphicsV611UpdateBufferEntry update_buffers[PDOCKER_GPU_VULKAN_GRAPHICS_V611_MAX_UPDATE_BUFFERS];
    PdockerGpuVulkanGraphicsV612ClearColorImageEntry clear_color_images[PDOCKER_GPU_VULKAN_GRAPHICS_V612_MAX_CLEAR_COLOR_IMAGES];
    PdockerGpuVulkanGraphicsV613ClearDepthStencilImageEntry clear_depth_stencil_images[PDOCKER_GPU_VULKAN_GRAPHICS_V613_MAX_CLEAR_DEPTH_STENCIL_IMAGES];
    PdockerGpuVulkanGraphicsV614ResolveImageEntry resolve_images[PDOCKER_GPU_VULKAN_GRAPHICS_V614_MAX_RESOLVE_IMAGES];
    PdockerGpuVulkanGraphicsV615BlitImageEntry blit_images[PDOCKER_GPU_VULKAN_GRAPHICS_V615_MAX_BLIT_IMAGES];
    PdockerGpuVulkanGraphicsV616ClearAttachmentsCommandEntry clear_attachments_commands[PDOCKER_GPU_VULKAN_GRAPHICS_V616_MAX_CLEAR_ATTACHMENTS_COMMANDS];
    PdockerGpuVulkanGraphicsV616ClearAttachmentEntry clear_attachments[PDOCKER_GPU_VULKAN_GRAPHICS_V616_MAX_CLEAR_ATTACHMENTS];
    PdockerGpuVulkanGraphicsV616ClearRectEntry clear_rects[PDOCKER_GPU_VULKAN_GRAPHICS_V616_MAX_CLEAR_RECTS];
    PdockerGpuVulkanGraphicsV617QueryCommandEntry query_commands[PDOCKER_GPU_VULKAN_GRAPHICS_V617_MAX_QUERY_COMMANDS];
    PdockerGpuVulkanGraphicsV618CopyQueryResultEntry copy_query_results[PDOCKER_GPU_VULKAN_GRAPHICS_V618_MAX_COPY_QUERY_RESULTS];
    int fds[PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_FDS];
    memset(pipeline_objects, 0, sizeof(pipeline_objects));
    memset(memory_objects, 0, sizeof(memory_objects));
    memset(memory_resource_indices, 0, sizeof(memory_resource_indices));
    memset(buffer_objects, 0, sizeof(buffer_objects));
    memset(buffer_resource_indices, 0, sizeof(buffer_resource_indices));
    memset(image_objects, 0, sizeof(image_objects));
    memset(image_view_objects, 0, sizeof(image_view_objects));
    memset(sampler_objects, 0, sizeof(sampler_objects));
    memset(resources, 0, sizeof(resources));
    memset(descriptors, 0, sizeof(descriptors));
    memset(image_entries, 0, sizeof(image_entries));
    memset(image_view_entries, 0, sizeof(image_view_entries));
    memset(sampler_entries, 0, sizeof(sampler_entries));
    memset(shader_stages, 0, sizeof(shader_stages));
    memset(pipelines, 0, sizeof(pipelines));
    memset(vertex_bindings, 0, sizeof(vertex_bindings));
    memset(vertex_attributes, 0, sizeof(vertex_attributes));
    memset(attachments, 0, sizeof(attachments));
    memset(dynamic_states, 0, sizeof(dynamic_states));
    memset(commands, 0, sizeof(commands));
    memset(dynamic_offsets, 0, sizeof(dynamic_offsets));
    memset(push_metadata, 0, sizeof(push_metadata));
    memset(image_barriers, 0, sizeof(image_barriers));
    memset(memory_barriers, 0, sizeof(memory_barriers));
    memset(buffer_barriers, 0, sizeof(buffer_barriers));
    memset(specialization_entries, 0, sizeof(specialization_entries));
    memset(depth_stencil_states, 0, sizeof(depth_stencil_states));
    memset(resolve_attachments, 0, sizeof(resolve_attachments));
    memset(static_pipeline_states, 0, sizeof(static_pipeline_states));
    memset(color_blend_states, 0, sizeof(color_blend_states));
    memset(color_blend_attachments, 0, sizeof(color_blend_attachments));
    memset(viewport_scissor_states, 0, sizeof(viewport_scissor_states));
    memset(viewport_entries, 0, sizeof(viewport_entries));
    memset(scissor_entries, 0, sizeof(scissor_entries));
    memset(indirect_draws, 0, sizeof(indirect_draws));
    memset(buffer_copies, 0, sizeof(buffer_copies));
    memset(buffer_image_copies, 0, sizeof(buffer_image_copies));
    memset(image_copies, 0, sizeof(image_copies));
    memset(fill_buffers, 0, sizeof(fill_buffers));
    memset(update_buffers, 0, sizeof(update_buffers));
    memset(clear_color_images, 0, sizeof(clear_color_images));
    memset(clear_depth_stencil_images, 0, sizeof(clear_depth_stencil_images));
    memset(resolve_images, 0, sizeof(resolve_images));
    memset(blit_images, 0, sizeof(blit_images));
    memset(clear_attachments_commands, 0, sizeof(clear_attachments_commands));
    memset(clear_attachments, 0, sizeof(clear_attachments));
    memset(clear_rects, 0, sizeof(clear_rects));
    memset(query_commands, 0, sizeof(query_commands));
    memset(copy_query_results, 0, sizeof(copy_query_results));
    memset(fds, -1, sizeof(fds));

    bool pre_need_v611_buffer_write = false;
    bool pre_need_v612_clear_color = false;
    bool pre_need_v613_clear_depth_stencil = false;
    bool pre_need_v614_resolve_image = false;
    bool pre_need_v615_blit_image = false;
    bool pre_need_v616_clear_attachments = false;
    bool pre_need_v617_query = false;
    bool pre_need_v618_copy_query = false;
    {
        for (uint32_t gi = 0; gi < cmd->graphics_command_op_count; ++gi) {
            if (cmd->graphics_command_ops[gi].command_type == PDOCKER_GPU_GRAPHICS_V6_COMMAND_CLEAR_ATTACHMENTS) {
                pre_need_v616_clear_attachments = true;
            }
            if (cmd->graphics_command_ops[gi].command_type == PDOCKER_GPU_GRAPHICS_V6_COMMAND_RESET_QUERY_POOL ||
                cmd->graphics_command_ops[gi].command_type == PDOCKER_GPU_GRAPHICS_V6_COMMAND_WRITE_TIMESTAMP ||
                cmd->graphics_command_ops[gi].command_type == PDOCKER_GPU_GRAPHICS_V6_COMMAND_BEGIN_QUERY ||
                cmd->graphics_command_ops[gi].command_type == PDOCKER_GPU_GRAPHICS_V6_COMMAND_END_QUERY) {
                pre_need_v617_query = true;
            }
            if (cmd->graphics_command_ops[gi].command_type == PDOCKER_GPU_GRAPHICS_V6_COMMAND_COPY_QUERY_POOL_RESULTS) {
                pre_need_v617_query = true;
                pre_need_v618_copy_query = true;
            }
            if (pre_need_v616_clear_attachments && pre_need_v617_query && pre_need_v618_copy_query) {
                break;
            }
        }
        for (uint32_t op_index = 0; op_index < cmd->command_op_count; ++op_index) {
            PdockerVkCommandOpType type = cmd->command_ops[op_index].type;
            if (type == PDOCKER_VK_COMMAND_FILL || type == PDOCKER_VK_COMMAND_UPDATE) {
                pre_need_v611_buffer_write = true;
            }
            if (type == PDOCKER_VK_COMMAND_CLEAR_COLOR_IMAGE) {
                pre_need_v612_clear_color = true;
            }
            if (type == PDOCKER_VK_COMMAND_CLEAR_DEPTH_STENCIL_IMAGE) {
                pre_need_v613_clear_depth_stencil = true;
            }
            if (type == PDOCKER_VK_COMMAND_RESOLVE_IMAGE) {
                pre_need_v614_resolve_image = true;
            }
            if (type == PDOCKER_VK_COMMAND_BLIT_IMAGE) {
                pre_need_v615_blit_image = true;
            }
            if (type == PDOCKER_VK_COMMAND_QUERY_BEGIN || type == PDOCKER_VK_COMMAND_QUERY_END ||
                type == PDOCKER_VK_COMMAND_QUERY_RESET || type == PDOCKER_VK_COMMAND_QUERY_TIMESTAMP) {
                pre_need_v617_query = true;
            }
            if (type == PDOCKER_VK_COMMAND_COPY_QUERY_RESULTS) {
                pre_need_v617_query = true;
                pre_need_v618_copy_query = true;
            }
            if ((pre_need_v611_buffer_write && pre_need_v612_clear_color &&
                 pre_need_v613_clear_depth_stencil && pre_need_v614_resolve_image &&
                 pre_need_v615_blit_image) || (pre_need_v616_clear_attachments && pre_need_v617_query)) {
                break;
            }
        }
    }

    unsigned char *frame = (unsigned char *)calloc(1, PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_FRAME_BYTES);
    if (!frame) {
        close(socket_fd);
        return -ENOMEM;
    }
    PdockerGpuVulkanGraphicsV618FrameHeader *frame_header_v618 =
        (PdockerGpuVulkanGraphicsV618FrameHeader *)frame;
    PdockerGpuVulkanGraphicsV617FrameHeader *frame_header_v617 =
        (PdockerGpuVulkanGraphicsV617FrameHeader *)frame;
    PdockerGpuVulkanGraphicsV616FrameHeader *frame_header_v616 =
        (PdockerGpuVulkanGraphicsV616FrameHeader *)frame;
    PdockerGpuVulkanGraphicsV615FrameHeader *frame_header_v615 =
        (PdockerGpuVulkanGraphicsV615FrameHeader *)frame;
    PdockerGpuVulkanGraphicsV614FrameHeader *frame_header_v614 =
        (PdockerGpuVulkanGraphicsV614FrameHeader *)frame;
    PdockerGpuVulkanGraphicsV613FrameHeader *frame_header_v613 =
        (PdockerGpuVulkanGraphicsV613FrameHeader *)frame;
    PdockerGpuVulkanGraphicsV612FrameHeader *frame_header_v612 =
        (PdockerGpuVulkanGraphicsV612FrameHeader *)frame;
    PdockerGpuVulkanGraphicsV611FrameHeader *frame_header_v611 =
        (PdockerGpuVulkanGraphicsV611FrameHeader *)frame;
    PdockerGpuVulkanGraphicsV610FrameHeader *frame_header_v610 =
        (PdockerGpuVulkanGraphicsV610FrameHeader *)frame;
    PdockerGpuVulkanGraphicsV69FrameHeader *frame_header_v69 =
        (PdockerGpuVulkanGraphicsV69FrameHeader *)frame;
    PdockerGpuVulkanGraphicsV68FrameHeader *frame_header_v68 =
        (PdockerGpuVulkanGraphicsV68FrameHeader *)frame;
    PdockerGpuVulkanGraphicsV67FrameHeader *frame_header_v67 =
        (PdockerGpuVulkanGraphicsV67FrameHeader *)frame;
    PdockerGpuVulkanGraphicsV66FrameHeader *frame_header_v66 =
        (PdockerGpuVulkanGraphicsV66FrameHeader *)frame;
    PdockerGpuVulkanGraphicsV65FrameHeader *frame_header_v65 =
        (PdockerGpuVulkanGraphicsV65FrameHeader *)frame;
    PdockerGpuVulkanGraphicsV64FrameHeader *frame_header_v64 =
        (PdockerGpuVulkanGraphicsV64FrameHeader *)frame;
    PdockerGpuVulkanGraphicsV63FrameHeader *frame_header_v63 =
        (PdockerGpuVulkanGraphicsV63FrameHeader *)frame;
    PdockerGpuVulkanGraphicsV62FrameHeader *frame_header_v62 =
        (PdockerGpuVulkanGraphicsV62FrameHeader *)frame;
    PdockerGpuVulkanGraphicsV61FrameHeader *frame_header =
        (PdockerGpuVulkanGraphicsV61FrameHeader *)frame;
    PdockerGpuVulkanGraphicsV6FrameHeader *header = &frame_header->base;
    size_t cursor = pre_need_v618_copy_query ? sizeof(PdockerGpuVulkanGraphicsV618FrameHeader) :
        (pre_need_v617_query ? sizeof(PdockerGpuVulkanGraphicsV617FrameHeader) :
        (pre_need_v616_clear_attachments ? sizeof(*frame_header_v616) :
        (pre_need_v615_blit_image ? sizeof(*frame_header_v615) :
        (pre_need_v614_resolve_image ? sizeof(*frame_header_v614) :
        (pre_need_v613_clear_depth_stencil ? sizeof(*frame_header_v613) :
        (pre_need_v612_clear_color ? sizeof(*frame_header_v612) :
        (pre_need_v611_buffer_write ? sizeof(*frame_header_v611) : sizeof(*frame_header_v610))))))));
    size_t fd_count = 0;
    size_t resource_count = 0;
    size_t descriptor_count = 0;
    size_t memory_count = 0;
    size_t buffer_count = 0;
    size_t image_count = 0;
    size_t image_view_count = 0;
    size_t sampler_count = 0;
    size_t pipeline_count = 0;
    size_t shader_stage_count = 0;
    size_t vertex_binding_count = 0;
    size_t vertex_attribute_count = 0;
    size_t attachment_count = 0;
    size_t dynamic_state_count = 0;
    size_t command_count = 0;
    size_t dynamic_offset_count = 0;
    size_t push_metadata_count = 0;
    size_t image_barrier_count = 0;
    size_t memory_barrier_count = 0;
    size_t buffer_barrier_count = 0;
    size_t specialization_entry_count = 0;
    size_t depth_stencil_state_count = 0;
    size_t resolve_attachment_count = 0;
    size_t static_pipeline_state_count = 0;
    size_t color_blend_state_count = 0;
    size_t color_blend_attachment_count = 0;
    size_t viewport_scissor_state_count = 0;
    size_t viewport_entry_count = 0;
    size_t scissor_entry_count = 0;
    size_t indirect_draw_count = 0;
    size_t buffer_copy_count = 0;
    size_t buffer_image_copy_count = 0;
    size_t image_copy_count = 0;
    size_t fill_buffer_count = 0;
    size_t update_buffer_count = 0;
    size_t clear_color_image_count = 0;
    size_t clear_depth_stencil_image_count = 0;
    size_t resolve_image_count = 0;
    size_t blit_image_count = 0;
    size_t clear_attachments_command_count = 0;
    size_t clear_attachment_count = 0;
    size_t clear_rect_count = 0;
    size_t query_command_count = 0;
    size_t copy_query_result_count = 0;
    uint64_t update_payload_data_offset = 0;
    uint64_t update_payload_data_size = 0;
    bool need_v62_specialization = false;
    bool need_v63_depth_stencil = false;
    bool need_v64_resolve_attachment = false;
    bool need_v65_static_pipeline_state = false;
    bool need_v66_color_blend_state = false;
    bool need_v67_viewport_scissor_state = false;
    bool need_v68_indirect_draw = false;
    bool need_v69_buffer_copy = false;
    bool need_v610_image_copy = false;
    bool need_v611_buffer_write = pre_need_v611_buffer_write;
    bool need_v612_clear_color = pre_need_v612_clear_color;
    bool need_v613_clear_depth_stencil = pre_need_v613_clear_depth_stencil;
    bool need_v614_resolve_image = pre_need_v614_resolve_image;
    bool need_v615_blit_image = pre_need_v615_blit_image;
    bool need_v616_clear_attachments = pre_need_v616_clear_attachments;
    bool need_v617_query = pre_need_v617_query;
    bool need_v618_copy_query = pre_need_v618_copy_query;
    uint64_t submit_id = __sync_add_and_fetch(&g_generic_dispatch_sequence, 1);
    int rc = 0;

    for (uint32_t i = 0; i < cmd->graphics_command_op_count; ++i) {
        const PdockerVkGraphicsCommandRecord *record = &cmd->graphics_command_ops[i];
        if (!record->pipeline) continue;
        if (find_graphics_pipeline_index(pipeline_objects, pipeline_count, record->pipeline) >= 0) {
            continue;
        }
        if (pipeline_count >= PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_PIPELINES) {
            rc = -E2BIG;
            goto cleanup;
        }
        PdockerVkPipeline *pipeline = record->pipeline;
        if (pipeline->graphics_unsupported ||
            pipeline->dynamic_rendering_format_overflow ||
            pipeline->shader_stage_count > PDOCKER_VK_MAX_GRAPHICS_VERTEX_BINDINGS ||
            shader_stage_count + pipeline->shader_stage_count > PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_SHADER_STAGES) {
            rc = -EOPNOTSUPP;
            goto cleanup;
        }
        PdockerGpuVulkanGraphicsV6PipelineEntry *pipeline_entry = &pipelines[pipeline_count];
        pipeline_entry->pipeline_id = (uint64_t)(uintptr_t)pipeline;
        pipeline_entry->layout_id = pipeline->layout ? pipeline->layout->layout_id : 0;
        pipeline_entry->render_pass_id = (uint64_t)(uintptr_t)pipeline->render_pass;
        pipeline_entry->shader_stage_first = (uint32_t)shader_stage_count;
        pipeline_entry->shader_stage_count = pipeline->shader_stage_count;
        pipeline_entry->topology = pipeline->topology;
        pipeline_entry->polygon_mode = pipeline->polygon_mode;
        pipeline_entry->cull_mode = pipeline->cull_mode;
        pipeline_entry->front_face = pipeline->front_face;
        pipeline_entry->rasterization_samples = pipeline->rasterization_samples;
        pipeline_entry->color_attachment_count = pipeline->dynamic_rendering_pipeline
            ? pipeline->dynamic_rendering_color_attachment_count
            : pipeline->color_attachment_count;
        pipeline_entry->subpass = pipeline->subpass;
        pipeline_entry->dynamic_rendering_view_mask = pipeline->dynamic_rendering_view_mask;
        pipeline_entry->dynamic_rendering_depth_format = pipeline->dynamic_rendering_depth_format;
        pipeline_entry->dynamic_rendering_stencil_format = pipeline->dynamic_rendering_stencil_format;
        pipeline_entry->depth_stencil_flags = pipeline->depth_stencil_flags;
        if (pipeline->depth_stencil_flags != 0) {
            if (depth_stencil_state_count >= PDOCKER_GPU_VULKAN_GRAPHICS_V63_MAX_DEPTH_STENCIL_STATES) {
                rc = -E2BIG;
                goto cleanup;
            }
            PdockerGpuVulkanGraphicsV63DepthStencilStateEntry *ds =
                &depth_stencil_states[depth_stencil_state_count++];
            ds->pipeline_index = (uint32_t)pipeline_count;
            ds->flags = pipeline->depth_stencil_flags;
            ds->depth_compare_op = pipeline->depth_compare_op;
            ds->front_fail_op = pipeline->front_stencil_state.failOp;
            ds->front_pass_op = pipeline->front_stencil_state.passOp;
            ds->front_depth_fail_op = pipeline->front_stencil_state.depthFailOp;
            ds->front_compare_op = pipeline->front_stencil_state.compareOp;
            ds->front_compare_mask = pipeline->front_stencil_state.compareMask;
            ds->front_write_mask = pipeline->front_stencil_state.writeMask;
            ds->front_reference = pipeline->front_stencil_state.reference;
            ds->back_fail_op = pipeline->back_stencil_state.failOp;
            ds->back_pass_op = pipeline->back_stencil_state.passOp;
            ds->back_depth_fail_op = pipeline->back_stencil_state.depthFailOp;
            ds->back_compare_op = pipeline->back_stencil_state.compareOp;
            ds->back_compare_mask = pipeline->back_stencil_state.compareMask;
            ds->back_write_mask = pipeline->back_stencil_state.writeMask;
            ds->back_reference = pipeline->back_stencil_state.reference;
            ds->min_depth_bounds_bits = float_bits_u32(pipeline->min_depth_bounds);
            ds->max_depth_bounds_bits = float_bits_u32(pipeline->max_depth_bounds);
            need_v63_depth_stencil = true;
        }
        uint32_t static_pipeline_flags =
            (pipeline->primitive_restart_enable ? PDOCKER_GPU_GRAPHICS_V65_STATIC_PRIMITIVE_RESTART_ENABLE : 0u) |
            (pipeline->depth_clamp_enable ? PDOCKER_GPU_GRAPHICS_V65_STATIC_DEPTH_CLAMP_ENABLE : 0u) |
            (pipeline->rasterizer_discard_enable ? PDOCKER_GPU_GRAPHICS_V65_STATIC_RASTERIZER_DISCARD_ENABLE : 0u) |
            (pipeline->depth_bias_enable ? PDOCKER_GPU_GRAPHICS_V65_STATIC_DEPTH_BIAS_ENABLE : 0u);
        if ((pipeline->dynamic_state_mask & pdocker_vk_graphics_dynamic_state_bit(VK_DYNAMIC_STATE_LINE_WIDTH)) == 0 &&
            pipeline->line_width != 1.0f) {
            static_pipeline_flags |= PDOCKER_GPU_GRAPHICS_V65_STATIC_LINE_WIDTH_PRESENT;
        }
        if (static_pipeline_flags != 0) {
            if (static_pipeline_state_count >= PDOCKER_GPU_VULKAN_GRAPHICS_V65_MAX_STATIC_PIPELINE_STATES) {
                rc = -E2BIG;
                goto cleanup;
            }
            PdockerGpuVulkanGraphicsV65StaticPipelineStateEntry *sp =
                &static_pipeline_states[static_pipeline_state_count++];
            sp->pipeline_index = (uint32_t)pipeline_count;
            sp->flags = static_pipeline_flags;
            sp->depth_bias_constant_factor_bits = pipeline->depth_bias_enable
                ? float_bits_u32(pipeline->depth_bias_constant_factor)
                : 0u;
            sp->depth_bias_clamp_bits = pipeline->depth_bias_enable
                ? float_bits_u32(pipeline->depth_bias_clamp)
                : 0u;
            sp->depth_bias_slope_factor_bits = pipeline->depth_bias_enable
                ? float_bits_u32(pipeline->depth_bias_slope_factor)
                : 0u;
            sp->line_width_bits = (static_pipeline_flags & PDOCKER_GPU_GRAPHICS_V65_STATIC_LINE_WIDTH_PRESENT)
                ? float_bits_u32(pipeline->line_width)
                : float_bits_u32(1.0f);
            need_v65_static_pipeline_state = true;
        }
        uint32_t color_blend_flags =
            (pipeline->color_blend_logic_op_enable ? PDOCKER_GPU_GRAPHICS_V66_COLOR_BLEND_LOGIC_OP_ENABLE : 0u);
        if ((pipeline->dynamic_state_mask & pdocker_vk_graphics_dynamic_state_bit(VK_DYNAMIC_STATE_BLEND_CONSTANTS)) == 0 &&
            (pipeline->color_blend_constants[0] != 0.0f ||
             pipeline->color_blend_constants[1] != 0.0f ||
             pipeline->color_blend_constants[2] != 0.0f ||
             pipeline->color_blend_constants[3] != 0.0f)) {
            color_blend_flags |= PDOCKER_GPU_GRAPHICS_V66_COLOR_BLEND_CONSTANTS_PRESENT;
        }
        bool color_blend_attachment_nondefault = false;
        for (uint32_t a = 0; a < pipeline_entry->color_attachment_count; ++a) {
            const VkPipelineColorBlendAttachmentState *ba = &pipeline->color_blend_attachments[a];
            if (ba->blendEnable ||
                ba->colorWriteMask != (VK_COLOR_COMPONENT_R_BIT | VK_COLOR_COMPONENT_G_BIT |
                                       VK_COLOR_COMPONENT_B_BIT | VK_COLOR_COMPONENT_A_BIT)) {
                color_blend_attachment_nondefault = true;
                break;
            }
        }
        if (color_blend_flags != 0 || color_blend_attachment_nondefault) {
            if (color_blend_state_count >= PDOCKER_GPU_VULKAN_GRAPHICS_V66_MAX_COLOR_BLEND_STATES ||
                color_blend_attachment_count + pipeline_entry->color_attachment_count >
                    PDOCKER_GPU_VULKAN_GRAPHICS_V66_MAX_COLOR_BLEND_ATTACHMENTS) {
                rc = -E2BIG;
                goto cleanup;
            }
            PdockerGpuVulkanGraphicsV66ColorBlendStateEntry *cb =
                &color_blend_states[color_blend_state_count++];
            cb->pipeline_index = (uint32_t)pipeline_count;
            cb->flags = color_blend_flags;
            cb->logic_op = pipeline->color_blend_logic_op_enable ? pipeline->color_blend_logic_op : 0u;
            cb->attachment_first = (uint32_t)color_blend_attachment_count;
            cb->attachment_count = pipeline_entry->color_attachment_count;
            cb->blend_constant0_bits = (color_blend_flags & PDOCKER_GPU_GRAPHICS_V66_COLOR_BLEND_CONSTANTS_PRESENT)
                ? float_bits_u32(pipeline->color_blend_constants[0]) : 0u;
            cb->blend_constant1_bits = (color_blend_flags & PDOCKER_GPU_GRAPHICS_V66_COLOR_BLEND_CONSTANTS_PRESENT)
                ? float_bits_u32(pipeline->color_blend_constants[1]) : 0u;
            cb->blend_constant2_bits = (color_blend_flags & PDOCKER_GPU_GRAPHICS_V66_COLOR_BLEND_CONSTANTS_PRESENT)
                ? float_bits_u32(pipeline->color_blend_constants[2]) : 0u;
            cb->blend_constant3_bits = (color_blend_flags & PDOCKER_GPU_GRAPHICS_V66_COLOR_BLEND_CONSTANTS_PRESENT)
                ? float_bits_u32(pipeline->color_blend_constants[3]) : 0u;
            for (uint32_t a = 0; a < pipeline_entry->color_attachment_count; ++a) {
                const VkPipelineColorBlendAttachmentState *src = &pipeline->color_blend_attachments[a];
                PdockerGpuVulkanGraphicsV66ColorBlendAttachmentEntry *dst =
                    &color_blend_attachments[color_blend_attachment_count++];
                dst->pipeline_index = (uint32_t)pipeline_count;
                dst->attachment_index = a;
                dst->flags = src->blendEnable ? PDOCKER_GPU_GRAPHICS_V66_COLOR_BLEND_ATTACHMENT_BLEND_ENABLE : 0u;
                dst->src_color_blend_factor = src->blendEnable ? src->srcColorBlendFactor : VK_BLEND_FACTOR_ONE;
                dst->dst_color_blend_factor = src->blendEnable ? src->dstColorBlendFactor : VK_BLEND_FACTOR_ZERO;
                dst->color_blend_op = src->blendEnable ? src->colorBlendOp : VK_BLEND_OP_ADD;
                dst->src_alpha_blend_factor = src->blendEnable ? src->srcAlphaBlendFactor : VK_BLEND_FACTOR_ONE;
                dst->dst_alpha_blend_factor = src->blendEnable ? src->dstAlphaBlendFactor : VK_BLEND_FACTOR_ZERO;
                dst->alpha_blend_op = src->blendEnable ? src->alphaBlendOp : VK_BLEND_OP_ADD;
                dst->color_write_mask = src->colorWriteMask;
            }
            need_v66_color_blend_state = true;
        }
        const bool viewport_dynamic =
            (pipeline->dynamic_state_mask & pdocker_vk_graphics_dynamic_state_bit(VK_DYNAMIC_STATE_VIEWPORT)) != 0;
        const bool scissor_dynamic =
            (pipeline->dynamic_state_mask & pdocker_vk_graphics_dynamic_state_bit(VK_DYNAMIC_STATE_SCISSOR)) != 0;
        const bool need_viewport_scissor_state =
            (pipeline->viewport_count > 0 && !viewport_dynamic) ||
            (pipeline->scissor_count > 0 && !scissor_dynamic) ||
            (viewport_dynamic && pipeline->viewport_count != 1u) ||
            (scissor_dynamic && pipeline->scissor_count != 1u);
        if (need_viewport_scissor_state) {
            if (viewport_scissor_state_count >= PDOCKER_GPU_VULKAN_GRAPHICS_V67_MAX_VIEWPORT_SCISSOR_STATES) {
                rc = -E2BIG;
                goto cleanup;
            }
            uint32_t viewport_first = PDOCKER_GPU_GRAPHICS_V67_INDEX_NONE;
            uint32_t scissor_first = PDOCKER_GPU_GRAPHICS_V67_INDEX_NONE;
            uint32_t viewport_flags = 0;
            if (pipeline->viewport_count > 0 && !viewport_dynamic) {
                if (viewport_entry_count + pipeline->viewport_count > PDOCKER_GPU_VULKAN_GRAPHICS_V67_MAX_VIEWPORTS) {
                    rc = -E2BIG;
                    goto cleanup;
                }
                viewport_first = (uint32_t)viewport_entry_count;
                viewport_flags |= PDOCKER_GPU_GRAPHICS_V67_VIEWPORT_STATIC_PRESENT;
                for (uint32_t v = 0; v < pipeline->viewport_count; ++v) {
                    const VkViewport *src = &pipeline->static_viewports[v];
                    PdockerGpuVulkanGraphicsV67ViewportEntry *dst = &viewport_entries[viewport_entry_count++];
                    dst->pipeline_index = (uint32_t)pipeline_count;
                    dst->viewport_index = v;
                    dst->x_bits = float_bits_u32(src->x);
                    dst->y_bits = float_bits_u32(src->y);
                    dst->width_bits = float_bits_u32(src->width);
                    dst->height_bits = float_bits_u32(src->height);
                    dst->min_depth_bits = float_bits_u32(src->minDepth);
                    dst->max_depth_bits = float_bits_u32(src->maxDepth);
                }
            }
            if (pipeline->scissor_count > 0 && !scissor_dynamic) {
                if (scissor_entry_count + pipeline->scissor_count > PDOCKER_GPU_VULKAN_GRAPHICS_V67_MAX_SCISSORS) {
                    rc = -E2BIG;
                    goto cleanup;
                }
                scissor_first = (uint32_t)scissor_entry_count;
                viewport_flags |= PDOCKER_GPU_GRAPHICS_V67_SCISSOR_STATIC_PRESENT;
                for (uint32_t v = 0; v < pipeline->scissor_count; ++v) {
                    const VkRect2D *src = &pipeline->static_scissors[v];
                    PdockerGpuVulkanGraphicsV67ScissorEntry *dst = &scissor_entries[scissor_entry_count++];
                    dst->pipeline_index = (uint32_t)pipeline_count;
                    dst->scissor_index = v;
                    dst->offset_x = src->offset.x;
                    dst->offset_y = src->offset.y;
                    dst->extent_width = src->extent.width;
                    dst->extent_height = src->extent.height;
                }
            }
            PdockerGpuVulkanGraphicsV67ViewportScissorStateEntry *vs =
                &viewport_scissor_states[viewport_scissor_state_count++];
            vs->pipeline_index = (uint32_t)pipeline_count;
            vs->flags = viewport_flags;
            vs->viewport_static_first = viewport_first;
            vs->viewport_count = pipeline->viewport_count;
            vs->scissor_static_first = scissor_first;
            vs->scissor_count = pipeline->scissor_count;
            need_v67_viewport_scissor_state = true;
        }
        uint32_t *color_formats = &pipeline_entry->color_attachment_format0;
        for (uint32_t c = 0; c < PDOCKER_VK_MAX_STORAGE_BUFFERS; ++c) {
            color_formats[c] = c < pipeline->dynamic_rendering_color_attachment_count
                ? pipeline->dynamic_rendering_color_formats[c]
                : VK_FORMAT_UNDEFINED;
        }
        pipeline_entry->dynamic_state_mask = pipeline->dynamic_state_mask;
        pipeline_entry->vertex_binding_first = (uint32_t)vertex_binding_count;
        pipeline_entry->vertex_binding_count = pipeline->vertex_binding_count;
        if (vertex_binding_count + pipeline->vertex_binding_count >
                PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_VERTEX_BINDINGS ||
            vertex_attribute_count + pipeline->vertex_attribute_count >
                PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_VERTEX_ATTRIBUTES) {
            rc = -E2BIG;
            goto cleanup;
        }
        for (uint32_t b = 0; b < pipeline->vertex_binding_count; ++b) {
            const VkVertexInputBindingDescription *src = &pipeline->vertex_bindings[b];
            PdockerGpuVulkanGraphicsV6VertexBindingEntry *entry =
                &vertex_bindings[vertex_binding_count++];
            entry->binding = src->binding;
            entry->stride = src->stride;
            entry->input_rate = src->inputRate;
            entry->buffer_resource_index = UINT32_MAX;
            entry->offset = 0;
            entry->size = 0;
        }
        pipeline_entry->vertex_attribute_first = (uint32_t)vertex_attribute_count;
        pipeline_entry->vertex_attribute_count = pipeline->vertex_attribute_count;
        for (uint32_t a = 0; a < pipeline->vertex_attribute_count; ++a) {
            PdockerGpuVulkanGraphicsV6VertexAttributeEntry *attr =
                &vertex_attributes[vertex_attribute_count++];
            attr->location = pipeline->vertex_attributes[a].location;
            attr->binding = pipeline->vertex_attributes[a].binding;
            attr->format = pipeline->vertex_attributes[a].format;
            attr->offset = pipeline->vertex_attributes[a].offset;
        }
        for (uint32_t stage_i = 0; stage_i < pipeline->shader_stage_count; ++stage_i) {
            if (pipeline->graphics_stage_specialization_too_large[stage_i]) {
                rc = -E2BIG;
                goto cleanup;
            }
            PdockerVkShaderModule *shader = pipeline->graphics_stage_modules[stage_i];
            if (!shader || shader->code_fd < 0 || shader->code_size == 0 ||
                fd_count >= PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_FDS) {
                rc = -EINVAL;
                goto cleanup;
            }
            const char *entry_name = pipeline->graphics_stage_entry_names[stage_i][0]
                ? pipeline->graphics_stage_entry_names[stage_i]
                : "main";
            PdockerGpuVulkanGraphicsV6ShaderStageEntry *stage =
                &shader_stages[shader_stage_count++];
            stage->stage_flags = pipeline->graphics_stage_flags[stage_i];
            stage->shader_fd_index = (uint32_t)fd_count;
            stage->shader_size = shader->code_size;
            stage->shader_hash =
                (shader->code_map && shader->code_map != MAP_FAILED)
                    ? fnv1a64_bytes(shader->code_map, shader->code_size)
                    : 0;
            stage->entry_name_size = strlen(entry_name);
            rc = frame_append_bytes(frame, PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_FRAME_BYTES,
                                    &cursor, entry_name, (size_t)stage->entry_name_size,
                                    &stage->entry_name_offset);
            if (rc != 0) goto cleanup;
            if (pipeline->graphics_stage_specialization_data_sizes[stage_i] > 0) {
                const size_t spec_data_size = pipeline->graphics_stage_specialization_data_sizes[stage_i];
                stage->specialization_size = spec_data_size;
                stage->specialization_hash = fnv1a64_bytes(
                    pipeline->graphics_stage_specialization_data[stage_i], spec_data_size);
                rc = frame_append_bytes(frame, PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_FRAME_BYTES,
                                        &cursor, pipeline->graphics_stage_specialization_data[stage_i],
                                        spec_data_size, &stage->specialization_offset);
                if (rc != 0) goto cleanup;
                need_v62_specialization = true;
            }
            for (uint32_t spec_i = 0;
                 spec_i < pipeline->graphics_stage_specialization_entry_counts[stage_i];
                 ++spec_i) {
                if (specialization_entry_count >= PDOCKER_GPU_VULKAN_GRAPHICS_V62_MAX_SPECIALIZATION_ENTRIES) {
                    rc = -E2BIG;
                    goto cleanup;
                }
                const VkSpecializationMapEntry *src_spec =
                    &pipeline->graphics_stage_specialization_entries[stage_i][spec_i];
                if ((uint64_t)src_spec->offset + (uint64_t)src_spec->size > stage->specialization_size) {
                    rc = -ERANGE;
                    goto cleanup;
                }
                PdockerGpuVulkanGraphicsV62SpecializationEntry *dst_spec =
                    &specialization_entries[specialization_entry_count++];
                dst_spec->shader_stage_index = (uint32_t)(shader_stage_count - 1u);
                dst_spec->constant_id = src_spec->constantID;
                dst_spec->offset = src_spec->offset;
                dst_spec->size = (uint64_t)src_spec->size;
                need_v62_specialization = true;
            }
            fds[fd_count++] = shader->code_fd;
        }
        pipeline_entry->pipeline_hash =
            fnv1a64_bytes(pipeline_entry, sizeof(*pipeline_entry));
        pipeline_objects[pipeline_count++] = pipeline;
    }

    for (uint32_t i = 0; i < cmd->dynamic_state_count; ++i) {
        if (dynamic_state_count >= PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_DYNAMIC_STATES) {
            rc = -E2BIG;
            goto cleanup;
        }
        const PdockerVkDynamicStateSnapshot *src = &cmd->dynamic_states[i];
        PdockerGpuVulkanGraphicsV6DynamicStateEntry *dst =
            &dynamic_states[dynamic_state_count++];
        dst->state_type = src->state_type;
        dst->first_index = src->first_index;
        dst->count = src->count;
        dst->data_size = src->data_size;
        dst->data_hash = fnv1a64_bytes(src->data, src->data_size);
        rc = frame_append_bytes(frame, PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_FRAME_BYTES,
                                &cursor, src->data, src->data_size, &dst->data_offset);
        if (rc != 0) goto cleanup;
    }

    for (uint32_t i = 0; i < cmd->graphics_dynamic_offset_count; ++i) {
        if (dynamic_offset_count >= PDOCKER_GPU_VULKAN_GRAPHICS_V61_MAX_DYNAMIC_OFFSETS) {
            rc = -E2BIG;
            goto cleanup;
        }
        dynamic_offsets[dynamic_offset_count++].offset = cmd->graphics_dynamic_offsets[i];
    }

    uint32_t first_graphics_draw_sequence = UINT32_MAX;
    uint32_t last_graphics_draw_sequence = 0;
    for (uint32_t op_index = 0; op_index < cmd->command_op_count; ++op_index) {
        if (cmd->command_ops[op_index].type == PDOCKER_VK_COMMAND_GRAPHICS_DRAW) {
            if (first_graphics_draw_sequence == UINT32_MAX) first_graphics_draw_sequence = op_index;
            last_graphics_draw_sequence = op_index;
        }
    }
    uint32_t next_command_op_for_graphics = 0;
#define APPEND_INTERLEAVED_GRAPHICS_BUFFER_COPIES(until_sequence_) \
    do { \
        uint32_t until__ = (until_sequence_); \
        while (next_command_op_for_graphics < cmd->command_op_count && \
               next_command_op_for_graphics < until__) { \
            uint32_t op_sequence__ = next_command_op_for_graphics++; \
            if (op_sequence__ <= first_graphics_draw_sequence || \
                op_sequence__ >= last_graphics_draw_sequence) { \
                continue; \
            } \
            const PdockerVkCommandOp *op__ = &cmd->command_ops[op_sequence__]; \
            if (op__->type == PDOCKER_VK_COMMAND_COPY) { \
                if (op__->index >= cmd->copy_op_count || \
                    command_count >= PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_COMMANDS || \
                    buffer_copy_count >= PDOCKER_GPU_VULKAN_GRAPHICS_V69_MAX_BUFFER_COPIES) { \
                    rc = -E2BIG; \
                    goto cleanup; \
                } \
                const PdockerVkCopyOp *copy__ = &cmd->copy_ops[op__->index]; \
                if (!copy__->src || !copy__->dst || !copy__->src->memory || !copy__->dst->memory || \
                    copy__->region.size == 0 || \
                    copy__->region.srcOffset > copy__->src->size || \
                    copy__->region.size > copy__->src->size - copy__->region.srcOffset || \
                    copy__->region.dstOffset > copy__->dst->size || \
                    copy__->region.size > copy__->dst->size - copy__->region.dstOffset) { \
                    rc = -ERANGE; \
                    goto cleanup; \
                } \
                int src_index__ = collect_graphics_buffer_resource( \
                    resources, &resource_count, memory_objects, memory_resource_indices, &memory_count, \
                    buffer_objects, buffer_resource_indices, &buffer_count, fds, &fd_count, \
                    copy__->src, submit_id); \
                if (src_index__ < 0) { rc = src_index__; goto cleanup; } \
                int dst_index__ = collect_graphics_buffer_resource( \
                    resources, &resource_count, memory_objects, memory_resource_indices, &memory_count, \
                    buffer_objects, buffer_resource_indices, &buffer_count, fds, &fd_count, \
                    copy__->dst, submit_id); \
                if (dst_index__ < 0) { rc = dst_index__; goto cleanup; } \
                PdockerGpuVulkanGraphicsV6CommandEntry *copy_command__ = &commands[command_count]; \
                copy_command__->command_type = PDOCKER_GPU_GRAPHICS_V6_COMMAND_COPY_BUFFER; \
                copy_command__->pipeline_index = UINT32_MAX; \
                copy_command__->descriptor_first_set = UINT32_MAX; \
                copy_command__->index_buffer_resource_index = UINT32_MAX; \
                PdockerGpuVulkanGraphicsV69BufferCopyEntry *copy_entry__ = &buffer_copies[buffer_copy_count++]; \
                copy_entry__->command_index = (uint32_t)command_count; \
                copy_entry__->src_resource_index = (uint32_t)src_index__; \
                copy_entry__->dst_resource_index = (uint32_t)dst_index__; \
                copy_entry__->src_offset = (uint64_t)copy__->region.srcOffset; \
                copy_entry__->dst_offset = (uint64_t)copy__->region.dstOffset; \
                copy_entry__->size = (uint64_t)copy__->region.size; \
                need_v69_buffer_copy = true; \
                command_count++; \
                continue; \
            } \
            if (op__->type == PDOCKER_VK_COMMAND_FILL) { \
                if (command_count >= PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_COMMANDS || \
                    fill_buffer_count >= PDOCKER_GPU_VULKAN_GRAPHICS_V611_MAX_FILL_BUFFERS || \
                    !op__->buffer || !op__->buffer->memory || op__->size == 0 || \
                    (op__->offset & 3u) != 0 || (op__->size & 3u) != 0 || \
                    op__->offset > op__->buffer->size || \
                    op__->size > op__->buffer->size - op__->offset) { \
                    rc = -ERANGE; \
                    goto cleanup; \
                } \
                int dst_index__ = collect_graphics_buffer_resource( \
                    resources, &resource_count, memory_objects, memory_resource_indices, &memory_count, \
                    buffer_objects, buffer_resource_indices, &buffer_count, fds, &fd_count, \
                    op__->buffer, submit_id); \
                if (dst_index__ < 0) { rc = dst_index__; goto cleanup; } \
                PdockerGpuVulkanGraphicsV6CommandEntry *fill_command__ = &commands[command_count]; \
                fill_command__->command_type = PDOCKER_GPU_GRAPHICS_V6_COMMAND_FILL_BUFFER; \
                fill_command__->pipeline_index = UINT32_MAX; \
                fill_command__->descriptor_first_set = UINT32_MAX; \
                fill_command__->index_buffer_resource_index = UINT32_MAX; \
                PdockerGpuVulkanGraphicsV611FillBufferEntry *fill_entry__ = &fill_buffers[fill_buffer_count++]; \
                fill_entry__->command_index = (uint32_t)command_count; \
                fill_entry__->dst_resource_index = (uint32_t)dst_index__; \
                fill_entry__->dst_offset = (uint64_t)op__->offset; \
                fill_entry__->size = (uint64_t)op__->size; \
                fill_entry__->data = op__->data; \
                need_v611_buffer_write = true; \
                command_count++; \
                continue; \
            } \
            if (op__->type == PDOCKER_VK_COMMAND_UPDATE) { \
                if (command_count >= PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_COMMANDS || \
                    update_buffer_count >= PDOCKER_GPU_VULKAN_GRAPHICS_V611_MAX_UPDATE_BUFFERS || \
                    !op__->buffer || !op__->buffer->memory || !op__->payload || op__->size == 0 || \
                    op__->size > PDOCKER_GPU_VULKAN_GRAPHICS_V611_MAX_UPDATE_BUFFER_BYTES || \
                    (op__->offset & 3u) != 0 || (op__->size & 3u) != 0 || \
                    op__->offset > op__->buffer->size || \
                    op__->size > op__->buffer->size - op__->offset) { \
                    rc = -ERANGE; \
                    goto cleanup; \
                } \
                int dst_index__ = collect_graphics_buffer_resource( \
                    resources, &resource_count, memory_objects, memory_resource_indices, &memory_count, \
                    buffer_objects, buffer_resource_indices, &buffer_count, fds, &fd_count, \
                    op__->buffer, submit_id); \
                if (dst_index__ < 0) { rc = dst_index__; goto cleanup; } \
                if (update_payload_data_size == 0) update_payload_data_offset = (uint64_t)cursor; \
                PdockerGpuVulkanGraphicsV611UpdateBufferEntry *update_entry__ = &update_buffers[update_buffer_count++]; \
                uint64_t payload_offset__ = 0; \
                rc = frame_append_bytes(frame, PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_FRAME_BYTES, \
                                        &cursor, op__->payload, (size_t)op__->size, &payload_offset__); \
                if (rc != 0) goto cleanup; \
                update_payload_data_size = ((uint64_t)cursor) - update_payload_data_offset; \
                PdockerGpuVulkanGraphicsV6CommandEntry *update_command__ = &commands[command_count]; \
                update_command__->command_type = PDOCKER_GPU_GRAPHICS_V6_COMMAND_UPDATE_BUFFER; \
                update_command__->pipeline_index = UINT32_MAX; \
                update_command__->descriptor_first_set = UINT32_MAX; \
                update_command__->index_buffer_resource_index = UINT32_MAX; \
                update_entry__->command_index = (uint32_t)command_count; \
                update_entry__->dst_resource_index = (uint32_t)dst_index__; \
                update_entry__->dst_offset = (uint64_t)op__->offset; \
                update_entry__->data_size = (uint64_t)op__->size; \
                update_entry__->payload_offset = payload_offset__; \
                update_entry__->payload_hash = fnv1a64_bytes(op__->payload, (size_t)op__->size); \
                need_v611_buffer_write = true; \
                command_count++; \
                continue; \
            } \
            if (op__->type == PDOCKER_VK_COMMAND_CLEAR_COLOR_IMAGE) { \
                if (op__->index >= cmd->image_clear_op_count || \
                    command_count >= PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_COMMANDS || \
                    clear_color_image_count >= PDOCKER_GPU_VULKAN_GRAPHICS_V612_MAX_CLEAR_COLOR_IMAGES) { \
                    rc = -E2BIG; \
                    goto cleanup; \
                } \
                const PdockerVkImageClearOp *clear__ = &cmd->image_clear_ops[op__->index]; \
                if (!clear__->image || !clear__->image->memory || \
                    clear__->range.aspectMask != VK_IMAGE_ASPECT_COLOR_BIT || \
                    clear__->range.levelCount == 0 || clear__->range.layerCount == 0 || \
                    clear__->range.baseMipLevel >= clear__->image->mip_levels || \
                    clear__->range.levelCount > clear__->image->mip_levels - clear__->range.baseMipLevel || \
                    clear__->range.baseArrayLayer >= clear__->image->array_layers || \
                    clear__->range.layerCount > clear__->image->array_layers - clear__->range.baseArrayLayer || \
                    !(clear__->image->usage & VK_IMAGE_USAGE_TRANSFER_DST_BIT)) { \
                    rc = -EOPNOTSUPP; \
                    goto cleanup; \
                } \
                int image_index__ = collect_graphics_image_entry( \
                    image_entries, image_objects, &image_count, resources, &resource_count, \
                    memory_objects, memory_resource_indices, &memory_count, fds, &fd_count, \
                    clear__->image, submit_id); \
                if (image_index__ < 0) { rc = image_index__; goto cleanup; } \
                PdockerGpuVulkanGraphicsV6CommandEntry *clear_command__ = &commands[command_count]; \
                clear_command__->command_type = PDOCKER_GPU_GRAPHICS_V6_COMMAND_CLEAR_COLOR_IMAGE; \
                clear_command__->pipeline_index = UINT32_MAX; \
                clear_command__->descriptor_first_set = UINT32_MAX; \
                clear_command__->index_buffer_resource_index = UINT32_MAX; \
                PdockerGpuVulkanGraphicsV612ClearColorImageEntry *clear_entry__ = &clear_color_images[clear_color_image_count++]; \
                clear_entry__->command_index = (uint32_t)command_count; \
                clear_entry__->image_index = (uint32_t)image_index__; \
                clear_entry__->image_layout = (uint32_t)clear__->image_layout; \
                clear_entry__->aspect_mask = (uint32_t)clear__->range.aspectMask; \
                clear_entry__->base_mip_level = clear__->range.baseMipLevel; \
                clear_entry__->level_count = clear__->range.levelCount; \
                clear_entry__->base_array_layer = clear__->range.baseArrayLayer; \
                clear_entry__->layer_count = clear__->range.layerCount; \
                clear_entry__->color_uint32_0 = clear__->color.uint32[0]; \
                clear_entry__->color_uint32_1 = clear__->color.uint32[1]; \
                clear_entry__->color_uint32_2 = clear__->color.uint32[2]; \
                clear_entry__->color_uint32_3 = clear__->color.uint32[3]; \
                need_v612_clear_color = true; \
                command_count++; \
                continue; \
            } \
            if (op__->type == PDOCKER_VK_COMMAND_CLEAR_DEPTH_STENCIL_IMAGE) { \
                if (op__->index >= cmd->depth_stencil_clear_op_count) { \
                    rc = -E2BIG; \
                    goto cleanup; \
                } \
                const PdockerVkDepthStencilClearOp *clear__ = &cmd->depth_stencil_clear_ops[op__->index]; \
                const VkImageAspectFlags ds_aspects__ = VK_IMAGE_ASPECT_DEPTH_BIT | VK_IMAGE_ASPECT_STENCIL_BIT; \
                VkImageAspectFlags split_aspects__[2]; \
                uint32_t clear_aspect_count__ = 0; \
                if (clear__->range.aspectMask & VK_IMAGE_ASPECT_DEPTH_BIT) { \
                    split_aspects__[clear_aspect_count__++] = VK_IMAGE_ASPECT_DEPTH_BIT; \
                } \
                if (clear__->range.aspectMask & VK_IMAGE_ASPECT_STENCIL_BIT) { \
                    split_aspects__[clear_aspect_count__++] = VK_IMAGE_ASPECT_STENCIL_BIT; \
                } \
                if (!clear__->image || !clear__->image->memory || \
                    clear_aspect_count__ == 0 || \
                    (clear__->range.aspectMask & ~ds_aspects__) != 0 || \
                    ((clear__->range.aspectMask & VK_IMAGE_ASPECT_DEPTH_BIT) && \
                     !pdocker_vk_format_has_depth(clear__->image->format)) || \
                    ((clear__->range.aspectMask & VK_IMAGE_ASPECT_STENCIL_BIT) && \
                     !pdocker_vk_format_has_stencil(clear__->image->format)) || \
                    clear__->range.levelCount == 0 || clear__->range.layerCount == 0 || \
                    clear__->range.baseMipLevel >= clear__->image->mip_levels || \
                    clear__->range.levelCount > clear__->image->mip_levels - clear__->range.baseMipLevel || \
                    clear__->range.baseArrayLayer >= clear__->image->array_layers || \
                    clear__->range.layerCount > clear__->image->array_layers - clear__->range.baseArrayLayer || \
                    !(clear__->image->usage & VK_IMAGE_USAGE_TRANSFER_DST_BIT)) { \
                    rc = -EOPNOTSUPP; \
                    goto cleanup; \
                } \
                if (command_count > PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_COMMANDS - clear_aspect_count__ || \
                    clear_depth_stencil_image_count > PDOCKER_GPU_VULKAN_GRAPHICS_V613_MAX_CLEAR_DEPTH_STENCIL_IMAGES - clear_aspect_count__) { \
                    rc = -E2BIG; \
                    goto cleanup; \
                } \
                int image_index__ = collect_graphics_image_entry( \
                    image_entries, image_objects, &image_count, resources, &resource_count, \
                    memory_objects, memory_resource_indices, &memory_count, fds, &fd_count, \
                    clear__->image, submit_id); \
                if (image_index__ < 0) { rc = image_index__; goto cleanup; } \
                uint32_t depth_bits__ = 0; \
                memcpy(&depth_bits__, &clear__->value.depth, sizeof(depth_bits__)); \
                for (uint32_t clear_aspect_i__ = 0; clear_aspect_i__ < clear_aspect_count__; ++clear_aspect_i__) { \
                    PdockerGpuVulkanGraphicsV6CommandEntry *clear_command__ = &commands[command_count]; \
                    clear_command__->command_type = PDOCKER_GPU_GRAPHICS_V6_COMMAND_CLEAR_DEPTH_STENCIL_IMAGE; \
                    clear_command__->pipeline_index = UINT32_MAX; \
                    clear_command__->descriptor_first_set = UINT32_MAX; \
                    clear_command__->index_buffer_resource_index = UINT32_MAX; \
                    PdockerGpuVulkanGraphicsV613ClearDepthStencilImageEntry *clear_entry__ = &clear_depth_stencil_images[clear_depth_stencil_image_count++]; \
                    clear_entry__->command_index = (uint32_t)command_count; \
                    clear_entry__->image_index = (uint32_t)image_index__; \
                    clear_entry__->image_layout = (uint32_t)clear__->image_layout; \
                    clear_entry__->aspect_mask = (uint32_t)split_aspects__[clear_aspect_i__]; \
                    clear_entry__->base_mip_level = clear__->range.baseMipLevel; \
                    clear_entry__->level_count = clear__->range.levelCount; \
                    clear_entry__->base_array_layer = clear__->range.baseArrayLayer; \
                    clear_entry__->layer_count = clear__->range.layerCount; \
                    clear_entry__->depth_bits = depth_bits__; \
                    clear_entry__->stencil = clear__->value.stencil; \
                    command_count++; \
                } \
                need_v613_clear_depth_stencil = true; \
                continue; \
            } \
            if (op__->type == PDOCKER_VK_COMMAND_RESOLVE_IMAGE) { \
                if (op__->index >= cmd->image_resolve_op_count || \
                    command_count >= PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_COMMANDS || \
                    resolve_image_count >= PDOCKER_GPU_VULKAN_GRAPHICS_V614_MAX_RESOLVE_IMAGES) { \
                    rc = -E2BIG; \
                    goto cleanup; \
                } \
                const PdockerVkImageResolveOp *resolve__ = &cmd->image_resolve_ops[op__->index]; \
                VkExtent3D src_extent__; \
                VkExtent3D dst_extent__; \
                if (!resolve__->src || !resolve__->dst || !resolve__->src->memory || !resolve__->dst->memory || \
                    resolve__->src->format != resolve__->dst->format || \
                    resolve__->src->samples == VK_SAMPLE_COUNT_1_BIT || \
                    resolve__->dst->samples != VK_SAMPLE_COUNT_1_BIT || \
                    resolve__->region.srcSubresource.aspectMask != VK_IMAGE_ASPECT_COLOR_BIT || \
                    resolve__->region.dstSubresource.aspectMask != VK_IMAGE_ASPECT_COLOR_BIT || \
                    resolve__->region.srcSubresource.layerCount == 0 || \
                    resolve__->region.srcSubresource.layerCount != resolve__->region.dstSubresource.layerCount || \
                    resolve__->region.extent.width == 0 || resolve__->region.extent.height == 0 || resolve__->region.extent.depth == 0 || \
                    !(resolve__->src->usage & VK_IMAGE_USAGE_TRANSFER_SRC_BIT) || \
                    !(resolve__->dst->usage & VK_IMAGE_USAGE_TRANSFER_DST_BIT) || \
                    !image_mip_extent(resolve__->src, resolve__->region.srcSubresource.mipLevel, &src_extent__) || \
                    !image_mip_extent(resolve__->dst, resolve__->region.dstSubresource.mipLevel, &dst_extent__) || \
                    resolve__->region.srcOffset.x < 0 || resolve__->region.srcOffset.y < 0 || resolve__->region.srcOffset.z < 0 || \
                    resolve__->region.dstOffset.x < 0 || resolve__->region.dstOffset.y < 0 || resolve__->region.dstOffset.z < 0 || \
                    (uint32_t)resolve__->region.srcOffset.x > src_extent__.width || \
                    resolve__->region.extent.width > src_extent__.width - (uint32_t)resolve__->region.srcOffset.x || \
                    (uint32_t)resolve__->region.srcOffset.y > src_extent__.height || \
                    resolve__->region.extent.height > src_extent__.height - (uint32_t)resolve__->region.srcOffset.y || \
                    (uint32_t)resolve__->region.srcOffset.z > src_extent__.depth || \
                    resolve__->region.extent.depth > src_extent__.depth - (uint32_t)resolve__->region.srcOffset.z || \
                    (uint32_t)resolve__->region.dstOffset.x > dst_extent__.width || \
                    resolve__->region.extent.width > dst_extent__.width - (uint32_t)resolve__->region.dstOffset.x || \
                    (uint32_t)resolve__->region.dstOffset.y > dst_extent__.height || \
                    resolve__->region.extent.height > dst_extent__.height - (uint32_t)resolve__->region.dstOffset.y || \
                    (uint32_t)resolve__->region.dstOffset.z > dst_extent__.depth || \
                    resolve__->region.extent.depth > dst_extent__.depth - (uint32_t)resolve__->region.dstOffset.z) { \
                    rc = -EOPNOTSUPP; \
                    goto cleanup; \
                } \
                int src_index__ = collect_graphics_image_entry( \
                    image_entries, image_objects, &image_count, resources, &resource_count, \
                    memory_objects, memory_resource_indices, &memory_count, fds, &fd_count, \
                    resolve__->src, submit_id); \
                if (src_index__ < 0) { rc = src_index__; goto cleanup; } \
                int dst_index__ = collect_graphics_image_entry( \
                    image_entries, image_objects, &image_count, resources, &resource_count, \
                    memory_objects, memory_resource_indices, &memory_count, fds, &fd_count, \
                    resolve__->dst, submit_id); \
                if (dst_index__ < 0) { rc = dst_index__; goto cleanup; } \
                PdockerGpuVulkanGraphicsV6CommandEntry *resolve_command__ = &commands[command_count]; \
                resolve_command__->command_type = PDOCKER_GPU_GRAPHICS_V6_COMMAND_RESOLVE_IMAGE; \
                resolve_command__->pipeline_index = UINT32_MAX; \
                resolve_command__->descriptor_first_set = UINT32_MAX; \
                resolve_command__->index_buffer_resource_index = UINT32_MAX; \
                PdockerGpuVulkanGraphicsV614ResolveImageEntry *resolve_entry__ = &resolve_images[resolve_image_count++]; \
                resolve_entry__->command_index = (uint32_t)command_count; \
                resolve_entry__->src_image_index = (uint32_t)src_index__; \
                resolve_entry__->dst_image_index = (uint32_t)dst_index__; \
                resolve_entry__->src_layout = (uint32_t)resolve__->src_layout; \
                resolve_entry__->dst_layout = (uint32_t)resolve__->dst_layout; \
                resolve_entry__->src_aspect_mask = (uint32_t)resolve__->region.srcSubresource.aspectMask; \
                resolve_entry__->src_mip_level = resolve__->region.srcSubresource.mipLevel; \
                resolve_entry__->src_base_array_layer = resolve__->region.srcSubresource.baseArrayLayer; \
                resolve_entry__->dst_aspect_mask = (uint32_t)resolve__->region.dstSubresource.aspectMask; \
                resolve_entry__->dst_mip_level = resolve__->region.dstSubresource.mipLevel; \
                resolve_entry__->dst_base_array_layer = resolve__->region.dstSubresource.baseArrayLayer; \
                resolve_entry__->layer_count = resolve__->region.srcSubresource.layerCount; \
                resolve_entry__->src_offset_x = resolve__->region.srcOffset.x; \
                resolve_entry__->src_offset_y = resolve__->region.srcOffset.y; \
                resolve_entry__->src_offset_z = resolve__->region.srcOffset.z; \
                resolve_entry__->dst_offset_x = resolve__->region.dstOffset.x; \
                resolve_entry__->dst_offset_y = resolve__->region.dstOffset.y; \
                resolve_entry__->dst_offset_z = resolve__->region.dstOffset.z; \
                resolve_entry__->extent_width = resolve__->region.extent.width; \
                resolve_entry__->extent_height = resolve__->region.extent.height; \
                resolve_entry__->extent_depth = resolve__->region.extent.depth; \
                need_v614_resolve_image = true; \
                command_count++; \
                continue; \
            } \
            if (op__->type == PDOCKER_VK_COMMAND_BLIT_IMAGE) { \
                if (op__->index >= cmd->image_blit_op_count || \
                    command_count >= PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_COMMANDS || \
                    blit_image_count >= PDOCKER_GPU_VULKAN_GRAPHICS_V615_MAX_BLIT_IMAGES) { \
                    rc = -E2BIG; \
                    goto cleanup; \
                } \
                const PdockerVkImageBlitOp *blit__ = &cmd->image_blit_ops[op__->index]; \
                VkExtent3D src_extent__; \
                VkExtent3D dst_extent__; \
                if (!blit__->src || !blit__->dst || !blit__->src->memory || !blit__->dst->memory || \
                    blit__->src->samples != VK_SAMPLE_COUNT_1_BIT || \
                    blit__->dst->samples != VK_SAMPLE_COUNT_1_BIT || \
                    blit__->region.srcSubresource.aspectMask != VK_IMAGE_ASPECT_COLOR_BIT || \
                    blit__->region.dstSubresource.aspectMask != VK_IMAGE_ASPECT_COLOR_BIT || \
                    blit__->region.srcSubresource.layerCount == 0 || \
                    blit__->region.srcSubresource.layerCount != blit__->region.dstSubresource.layerCount || \
                    !(blit__->src->usage & VK_IMAGE_USAGE_TRANSFER_SRC_BIT) || \
                    !(blit__->dst->usage & VK_IMAGE_USAGE_TRANSFER_DST_BIT) || \
                    (blit__->filter != VK_FILTER_NEAREST && blit__->filter != VK_FILTER_LINEAR) || \
                    !image_mip_extent(blit__->src, blit__->region.srcSubresource.mipLevel, &src_extent__) || \
                    !image_mip_extent(blit__->dst, blit__->region.dstSubresource.mipLevel, &dst_extent__) || \
                    blit__->region.srcSubresource.baseArrayLayer >= blit__->src->array_layers || \
                    blit__->region.srcSubresource.layerCount > blit__->src->array_layers - blit__->region.srcSubresource.baseArrayLayer || \
                    blit__->region.dstSubresource.baseArrayLayer >= blit__->dst->array_layers || \
                    blit__->region.dstSubresource.layerCount > blit__->dst->array_layers - blit__->region.dstSubresource.baseArrayLayer || \
                    blit__->region.srcOffsets[0].x < 0 || blit__->region.srcOffsets[0].y < 0 || blit__->region.srcOffsets[0].z < 0 || \
                    blit__->region.srcOffsets[1].x < 0 || blit__->region.srcOffsets[1].y < 0 || blit__->region.srcOffsets[1].z < 0 || \
                    blit__->region.dstOffsets[0].x < 0 || blit__->region.dstOffsets[0].y < 0 || blit__->region.dstOffsets[0].z < 0 || \
                    blit__->region.dstOffsets[1].x < 0 || blit__->region.dstOffsets[1].y < 0 || blit__->region.dstOffsets[1].z < 0 || \
                    (uint32_t)blit__->region.srcOffsets[0].x > src_extent__.width || \
                    (uint32_t)blit__->region.srcOffsets[1].x > src_extent__.width || \
                    (uint32_t)blit__->region.srcOffsets[0].y > src_extent__.height || \
                    (uint32_t)blit__->region.srcOffsets[1].y > src_extent__.height || \
                    (uint32_t)blit__->region.srcOffsets[0].z > src_extent__.depth || \
                    (uint32_t)blit__->region.srcOffsets[1].z > src_extent__.depth || \
                    (uint32_t)blit__->region.dstOffsets[0].x > dst_extent__.width || \
                    (uint32_t)blit__->region.dstOffsets[1].x > dst_extent__.width || \
                    (uint32_t)blit__->region.dstOffsets[0].y > dst_extent__.height || \
                    (uint32_t)blit__->region.dstOffsets[1].y > dst_extent__.height || \
                    (uint32_t)blit__->region.dstOffsets[0].z > dst_extent__.depth || \
                    (uint32_t)blit__->region.dstOffsets[1].z > dst_extent__.depth || \
                    blit__->region.srcOffsets[0].x == blit__->region.srcOffsets[1].x || \
                    blit__->region.srcOffsets[0].y == blit__->region.srcOffsets[1].y || \
                    blit__->region.srcOffsets[0].z == blit__->region.srcOffsets[1].z || \
                    blit__->region.dstOffsets[0].x == blit__->region.dstOffsets[1].x || \
                    blit__->region.dstOffsets[0].y == blit__->region.dstOffsets[1].y || \
                    blit__->region.dstOffsets[0].z == blit__->region.dstOffsets[1].z) { \
                    rc = -EOPNOTSUPP; \
                    goto cleanup; \
                } \
                int src_index__ = collect_graphics_image_entry( \
                    image_entries, image_objects, &image_count, resources, &resource_count, \
                    memory_objects, memory_resource_indices, &memory_count, fds, &fd_count, \
                    blit__->src, submit_id); \
                if (src_index__ < 0) { rc = src_index__; goto cleanup; } \
                int dst_index__ = collect_graphics_image_entry( \
                    image_entries, image_objects, &image_count, resources, &resource_count, \
                    memory_objects, memory_resource_indices, &memory_count, fds, &fd_count, \
                    blit__->dst, submit_id); \
                if (dst_index__ < 0) { rc = dst_index__; goto cleanup; } \
                PdockerGpuVulkanGraphicsV6CommandEntry *blit_command__ = &commands[command_count]; \
                blit_command__->command_type = PDOCKER_GPU_GRAPHICS_V6_COMMAND_BLIT_IMAGE; \
                blit_command__->pipeline_index = UINT32_MAX; \
                blit_command__->descriptor_first_set = UINT32_MAX; \
                blit_command__->index_buffer_resource_index = UINT32_MAX; \
                PdockerGpuVulkanGraphicsV615BlitImageEntry *blit_entry__ = &blit_images[blit_image_count++]; \
                blit_entry__->command_index = (uint32_t)command_count; \
                blit_entry__->src_image_index = (uint32_t)src_index__; \
                blit_entry__->dst_image_index = (uint32_t)dst_index__; \
                blit_entry__->src_layout = (uint32_t)blit__->src_layout; \
                blit_entry__->dst_layout = (uint32_t)blit__->dst_layout; \
                blit_entry__->src_aspect_mask = (uint32_t)blit__->region.srcSubresource.aspectMask; \
                blit_entry__->src_mip_level = blit__->region.srcSubresource.mipLevel; \
                blit_entry__->src_base_array_layer = blit__->region.srcSubresource.baseArrayLayer; \
                blit_entry__->dst_aspect_mask = (uint32_t)blit__->region.dstSubresource.aspectMask; \
                blit_entry__->dst_mip_level = blit__->region.dstSubresource.mipLevel; \
                blit_entry__->dst_base_array_layer = blit__->region.dstSubresource.baseArrayLayer; \
                blit_entry__->layer_count = blit__->region.srcSubresource.layerCount; \
                blit_entry__->src_offset0_x = blit__->region.srcOffsets[0].x; \
                blit_entry__->src_offset0_y = blit__->region.srcOffsets[0].y; \
                blit_entry__->src_offset0_z = blit__->region.srcOffsets[0].z; \
                blit_entry__->src_offset1_x = blit__->region.srcOffsets[1].x; \
                blit_entry__->src_offset1_y = blit__->region.srcOffsets[1].y; \
                blit_entry__->src_offset1_z = blit__->region.srcOffsets[1].z; \
                blit_entry__->dst_offset0_x = blit__->region.dstOffsets[0].x; \
                blit_entry__->dst_offset0_y = blit__->region.dstOffsets[0].y; \
                blit_entry__->dst_offset0_z = blit__->region.dstOffsets[0].z; \
                blit_entry__->dst_offset1_x = blit__->region.dstOffsets[1].x; \
                blit_entry__->dst_offset1_y = blit__->region.dstOffsets[1].y; \
                blit_entry__->dst_offset1_z = blit__->region.dstOffsets[1].z; \
                blit_entry__->filter = (uint32_t)blit__->filter; \
                need_v615_blit_image = true; \
                command_count++; \
                continue; \
            } \
            if (op__->type == PDOCKER_VK_COMMAND_IMAGE_COPY) { \
                if (op__->index >= cmd->image_copy_op_count || \
                    command_count >= PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_COMMANDS || \
                    buffer_image_copy_count >= PDOCKER_GPU_VULKAN_GRAPHICS_V610_MAX_BUFFER_IMAGE_COPIES) { \
                    rc = -E2BIG; \
                    goto cleanup; \
                } \
                const PdockerVkImageCopyOp *copy__ = &cmd->image_copy_ops[op__->index]; \
                if (!copy__->buffer || !copy__->image || !copy__->buffer->memory || !copy__->image->memory || \
                    copy__->region.imageSubresource.aspectMask != VK_IMAGE_ASPECT_COLOR_BIT || \
                    copy__->region.imageSubresource.layerCount == 0 || \
                    copy__->region.imageExtent.width == 0 || copy__->region.imageExtent.height == 0 || \
                    copy__->region.imageExtent.depth == 0 || copy__->region.imageOffset.x < 0 || \
                    copy__->region.imageOffset.y < 0 || copy__->region.imageOffset.z < 0 || \
                    copy__->region.imageSubresource.baseArrayLayer >= copy__->image->array_layers || \
                    copy__->region.imageSubresource.layerCount > copy__->image->array_layers - copy__->region.imageSubresource.baseArrayLayer) { \
                    rc = -EOPNOTSUPP; \
                    goto cleanup; \
                } \
                VkExtent3D extent__; \
                if (!image_mip_extent(copy__->image, copy__->region.imageSubresource.mipLevel, &extent__) || \
                    (uint32_t)copy__->region.imageOffset.x > extent__.width || \
                    copy__->region.imageExtent.width > extent__.width - (uint32_t)copy__->region.imageOffset.x || \
                    (uint32_t)copy__->region.imageOffset.y > extent__.height || \
                    copy__->region.imageExtent.height > extent__.height - (uint32_t)copy__->region.imageOffset.y || \
                    (uint32_t)copy__->region.imageOffset.z > extent__.depth || \
                    copy__->region.imageExtent.depth > extent__.depth - (uint32_t)copy__->region.imageOffset.z) { \
                    rc = -ERANGE; \
                    goto cleanup; \
                } \
                int buffer_index__ = collect_graphics_buffer_resource( \
                    resources, &resource_count, memory_objects, memory_resource_indices, &memory_count, \
                    buffer_objects, buffer_resource_indices, &buffer_count, fds, &fd_count, \
                    copy__->buffer, submit_id); \
                if (buffer_index__ < 0) { rc = buffer_index__; goto cleanup; } \
                int image_index__ = collect_graphics_image_entry( \
                    image_entries, image_objects, &image_count, resources, &resource_count, \
                    memory_objects, memory_resource_indices, &memory_count, fds, &fd_count, \
                    copy__->image, submit_id); \
                if (image_index__ < 0) { rc = image_index__; goto cleanup; } \
                PdockerGpuVulkanGraphicsV6CommandEntry *copy_command__ = &commands[command_count]; \
                copy_command__->command_type = copy__->direction == PDOCKER_VK_IMAGE_COPY_BUFFER_TO_IMAGE \
                    ? PDOCKER_GPU_GRAPHICS_V6_COMMAND_COPY_BUFFER_TO_IMAGE \
                    : PDOCKER_GPU_GRAPHICS_V6_COMMAND_COPY_IMAGE_TO_BUFFER; \
                copy_command__->pipeline_index = UINT32_MAX; \
                copy_command__->descriptor_first_set = UINT32_MAX; \
                copy_command__->index_buffer_resource_index = UINT32_MAX; \
                PdockerGpuVulkanGraphicsV610BufferImageCopyEntry *copy_entry__ = \
                    &buffer_image_copies[buffer_image_copy_count++]; \
                copy_entry__->command_index = (uint32_t)command_count; \
                copy_entry__->direction = (uint32_t)copy__->direction; \
                copy_entry__->buffer_resource_index = (uint32_t)buffer_index__; \
                copy_entry__->image_index = (uint32_t)image_index__; \
                copy_entry__->image_layout = (uint32_t)copy__->image_layout; \
                copy_entry__->aspect_mask = (uint32_t)copy__->region.imageSubresource.aspectMask; \
                copy_entry__->mip_level = copy__->region.imageSubresource.mipLevel; \
                copy_entry__->base_array_layer = copy__->region.imageSubresource.baseArrayLayer; \
                copy_entry__->layer_count = copy__->region.imageSubresource.layerCount; \
                copy_entry__->buffer_row_length = copy__->region.bufferRowLength; \
                copy_entry__->buffer_image_height = copy__->region.bufferImageHeight; \
                copy_entry__->image_offset_x = copy__->region.imageOffset.x; \
                copy_entry__->image_offset_y = copy__->region.imageOffset.y; \
                copy_entry__->image_offset_z = copy__->region.imageOffset.z; \
                copy_entry__->image_extent_width = copy__->region.imageExtent.width; \
                copy_entry__->image_extent_height = copy__->region.imageExtent.height; \
                copy_entry__->image_extent_depth = copy__->region.imageExtent.depth; \
                copy_entry__->buffer_offset = (uint64_t)copy__->region.bufferOffset; \
                need_v610_image_copy = true; \
                command_count++; \
                continue; \
            } \
            if (op__->type == PDOCKER_VK_COMMAND_IMAGE_TO_IMAGE_COPY) { \
                if (op__->index >= cmd->image_to_image_copy_op_count || \
                    command_count >= PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_COMMANDS || \
                    image_copy_count >= PDOCKER_GPU_VULKAN_GRAPHICS_V610_MAX_IMAGE_COPIES) { \
                    rc = -E2BIG; \
                    goto cleanup; \
                } \
                const PdockerVkImageToImageCopyOp *copy__ = &cmd->image_to_image_copy_ops[op__->index]; \
                if (!copy__->src || !copy__->dst || !copy__->src->memory || !copy__->dst->memory || \
                    copy__->region.srcSubresource.aspectMask != VK_IMAGE_ASPECT_COLOR_BIT || \
                    copy__->region.dstSubresource.aspectMask != VK_IMAGE_ASPECT_COLOR_BIT || \
                    copy__->region.srcSubresource.layerCount == 0 || \
                    copy__->region.srcSubresource.layerCount != copy__->region.dstSubresource.layerCount || \
                    copy__->region.extent.width == 0 || copy__->region.extent.height == 0 || \
                    copy__->region.extent.depth == 0 || copy__->region.srcOffset.x < 0 || \
                    copy__->region.srcOffset.y < 0 || copy__->region.srcOffset.z < 0 || \
                    copy__->region.dstOffset.x < 0 || copy__->region.dstOffset.y < 0 || \
                    copy__->region.dstOffset.z < 0 || \
                    copy__->region.srcSubresource.baseArrayLayer >= copy__->src->array_layers || \
                    copy__->region.srcSubresource.layerCount > copy__->src->array_layers - copy__->region.srcSubresource.baseArrayLayer || \
                    copy__->region.dstSubresource.baseArrayLayer >= copy__->dst->array_layers || \
                    copy__->region.dstSubresource.layerCount > copy__->dst->array_layers - copy__->region.dstSubresource.baseArrayLayer) { \
                    rc = -EOPNOTSUPP; \
                    goto cleanup; \
                } \
                VkExtent3D src_extent__; \
                VkExtent3D dst_extent__; \
                if (!image_mip_extent(copy__->src, copy__->region.srcSubresource.mipLevel, &src_extent__) || \
                    !image_mip_extent(copy__->dst, copy__->region.dstSubresource.mipLevel, &dst_extent__) || \
                    (uint32_t)copy__->region.srcOffset.x > src_extent__.width || \
                    copy__->region.extent.width > src_extent__.width - (uint32_t)copy__->region.srcOffset.x || \
                    (uint32_t)copy__->region.srcOffset.y > src_extent__.height || \
                    copy__->region.extent.height > src_extent__.height - (uint32_t)copy__->region.srcOffset.y || \
                    (uint32_t)copy__->region.srcOffset.z > src_extent__.depth || \
                    copy__->region.extent.depth > src_extent__.depth - (uint32_t)copy__->region.srcOffset.z || \
                    (uint32_t)copy__->region.dstOffset.x > dst_extent__.width || \
                    copy__->region.extent.width > dst_extent__.width - (uint32_t)copy__->region.dstOffset.x || \
                    (uint32_t)copy__->region.dstOffset.y > dst_extent__.height || \
                    copy__->region.extent.height > dst_extent__.height - (uint32_t)copy__->region.dstOffset.y || \
                    (uint32_t)copy__->region.dstOffset.z > dst_extent__.depth || \
                    copy__->region.extent.depth > dst_extent__.depth - (uint32_t)copy__->region.dstOffset.z) { \
                    rc = -ERANGE; \
                    goto cleanup; \
                } \
                int src_index__ = collect_graphics_image_entry( \
                    image_entries, image_objects, &image_count, resources, &resource_count, \
                    memory_objects, memory_resource_indices, &memory_count, fds, &fd_count, \
                    copy__->src, submit_id); \
                if (src_index__ < 0) { rc = src_index__; goto cleanup; } \
                int dst_index__ = collect_graphics_image_entry( \
                    image_entries, image_objects, &image_count, resources, &resource_count, \
                    memory_objects, memory_resource_indices, &memory_count, fds, &fd_count, \
                    copy__->dst, submit_id); \
                if (dst_index__ < 0) { rc = dst_index__; goto cleanup; } \
                PdockerGpuVulkanGraphicsV6CommandEntry *copy_command__ = &commands[command_count]; \
                copy_command__->command_type = PDOCKER_GPU_GRAPHICS_V6_COMMAND_COPY_IMAGE; \
                copy_command__->pipeline_index = UINT32_MAX; \
                copy_command__->descriptor_first_set = UINT32_MAX; \
                copy_command__->index_buffer_resource_index = UINT32_MAX; \
                PdockerGpuVulkanGraphicsV610ImageCopyEntry *copy_entry__ = &image_copies[image_copy_count++]; \
                copy_entry__->command_index = (uint32_t)command_count; \
                copy_entry__->src_image_index = (uint32_t)src_index__; \
                copy_entry__->dst_image_index = (uint32_t)dst_index__; \
                copy_entry__->src_layout = (uint32_t)copy__->src_layout; \
                copy_entry__->dst_layout = (uint32_t)copy__->dst_layout; \
                copy_entry__->src_aspect_mask = (uint32_t)copy__->region.srcSubresource.aspectMask; \
                copy_entry__->src_mip_level = copy__->region.srcSubresource.mipLevel; \
                copy_entry__->src_base_array_layer = copy__->region.srcSubresource.baseArrayLayer; \
                copy_entry__->dst_aspect_mask = (uint32_t)copy__->region.dstSubresource.aspectMask; \
                copy_entry__->dst_mip_level = copy__->region.dstSubresource.mipLevel; \
                copy_entry__->dst_base_array_layer = copy__->region.dstSubresource.baseArrayLayer; \
                copy_entry__->layer_count = copy__->region.srcSubresource.layerCount; \
                copy_entry__->src_offset_x = copy__->region.srcOffset.x; \
                copy_entry__->src_offset_y = copy__->region.srcOffset.y; \
                copy_entry__->src_offset_z = copy__->region.srcOffset.z; \
                copy_entry__->dst_offset_x = copy__->region.dstOffset.x; \
                copy_entry__->dst_offset_y = copy__->region.dstOffset.y; \
                copy_entry__->dst_offset_z = copy__->region.dstOffset.z; \
                copy_entry__->extent_width = copy__->region.extent.width; \
                copy_entry__->extent_height = copy__->region.extent.height; \
                copy_entry__->extent_depth = copy__->region.extent.depth; \
                need_v610_image_copy = true; \
                command_count++; \
                continue; \
            } \
        } \
    } while (0)

    for (uint32_t i = 0; i < cmd->graphics_command_op_count; ++i) {
        const PdockerVkGraphicsCommandRecord *record = &cmd->graphics_command_ops[i];
        APPEND_INTERLEAVED_GRAPHICS_BUFFER_COPIES(record->command_op_sequence);
        if (command_count >= PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_COMMANDS) {
            rc = -E2BIG;
            goto cleanup;
        }
        PdockerGpuVulkanGraphicsV6CommandEntry *command = &commands[command_count];
        command->command_type = record->command_type;
        command->flags = record->flags;
        command->pipeline_index = UINT32_MAX;
        command->descriptor_first_set = UINT32_MAX;
        command->index_buffer_resource_index = UINT32_MAX;
        command->pipeline_layout_id = record->layout_id;
        if (record->pipeline) {
            int pipeline_index = find_graphics_pipeline_index(
                pipeline_objects, pipeline_count, record->pipeline);
            if (pipeline_index < 0) {
                rc = -EPROTO;
                goto cleanup;
            }
            command->pipeline_index = (uint32_t)pipeline_index;
        }
        if (record->command_type == PDOCKER_GPU_GRAPHICS_V6_COMMAND_BEGIN_RENDERING) {
            if (record->rendering_snapshot_index >= cmd->graphics_rendering_op_count) {
                rc = -EOPNOTSUPP;
                goto cleanup;
            }
            const PdockerVkGraphicsRenderingSnapshot *snapshot =
                &cmd->graphics_rendering_ops[record->rendering_snapshot_index];
            command->attachment_first = (uint32_t)attachment_count;
            rc = collect_graphics_attachment_entries(
                attachments, &attachment_count, resolve_attachments,
                &resolve_attachment_count, &need_v64_resolve_attachment, frame,
                PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_FRAME_BYTES, &cursor, snapshot,
                image_entries, image_objects, &image_count,
                image_view_entries, image_view_objects, &image_view_count,
                resources, &resource_count, memory_objects, memory_resource_indices, &memory_count,
                fds, &fd_count, submit_id);
            if (rc != 0) goto cleanup;
            command->attachment_count = (uint32_t)(attachment_count - command->attachment_first);
            command->render_area_offset_x = snapshot->render_area.offset.x;
            command->render_area_offset_y = snapshot->render_area.offset.y;
            command->render_area_extent_width = snapshot->render_area.extent.width;
            command->render_area_extent_height = snapshot->render_area.extent.height;
            command->rendering_layer_count = snapshot->layer_count;
            command->rendering_view_mask = snapshot->view_mask;
        } else if (record->command_type == PDOCKER_GPU_GRAPHICS_V6_COMMAND_BIND_DESCRIPTOR_SETS) {
            if (record->descriptor_bind_snapshot_index >= cmd->graphics_descriptor_bind_op_count) {
                rc = -EPROTO;
                goto cleanup;
            }
            if (record->first_dynamic_offset > dynamic_offset_count ||
                record->dynamic_offset_count > dynamic_offset_count - record->first_dynamic_offset) {
                rc = -ERANGE;
                goto cleanup;
            }
            const PdockerVkGraphicsDescriptorBindSnapshot *snapshot =
                &cmd->graphics_descriptor_bind_ops[record->descriptor_bind_snapshot_index];
            uint32_t dynamic_descriptor_count = 0;
            command->first_descriptor = (uint32_t)descriptor_count;
            command->descriptor_first_set = snapshot->first_set;
            command->first_dynamic_offset = record->first_dynamic_offset;
            command->dynamic_offset_count = record->dynamic_offset_count;
            rc = collect_graphics_descriptor_entries(
                descriptors, &descriptor_count, resources, &resource_count,
                memory_objects, memory_resource_indices, &memory_count,
                buffer_objects, buffer_resource_indices, &buffer_count,
                image_entries, image_objects, &image_count,
                image_view_entries, image_view_objects, &image_view_count,
                sampler_entries, sampler_objects, &sampler_count,
                fds, &fd_count, snapshot, submit_id, &dynamic_descriptor_count);
            if (rc != 0) goto cleanup;
            if (dynamic_descriptor_count != record->dynamic_offset_count) {
                rc = -EPROTO;
                goto cleanup;
            }
            command->descriptor_count = (uint32_t)(descriptor_count - command->first_descriptor);
        } else if (record->command_type == PDOCKER_GPU_GRAPHICS_V6_COMMAND_SET_DYNAMIC_STATE) {
            if (record->dynamic_state_index >= dynamic_state_count) {
                rc = -EPROTO;
                goto cleanup;
            }
            command->dynamic_state_first = record->dynamic_state_index;
            command->dynamic_state_count = 1;
        } else if (record->command_type == PDOCKER_GPU_GRAPHICS_V6_COMMAND_BIND_VERTEX_BUFFERS) {
            if (vertex_binding_count + record->vertex_binding_count >
                PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_VERTEX_BINDINGS) {
                rc = -E2BIG;
                goto cleanup;
            }
            command->vertex_binding_first = (uint32_t)vertex_binding_count;
            command->vertex_binding_count = record->vertex_binding_count;
            for (uint32_t b = 0; b < record->vertex_binding_count; ++b) {
                uint32_t slot = record->vertex_binding_first + b;
                if (slot >= PDOCKER_VK_MAX_GRAPHICS_VERTEX_BINDINGS ||
                    !cmd->vertex_bindings[slot].bound || !cmd->vertex_bindings[slot].buffer) {
                    rc = -EPROTO;
                    goto cleanup;
                }
                const PdockerVkVertexBindingState *binding = &cmd->vertex_bindings[slot];
                int buffer_index = collect_graphics_buffer_resource(
                    resources, &resource_count, memory_objects, memory_resource_indices, &memory_count,
                    buffer_objects, buffer_resource_indices, &buffer_count, fds, &fd_count,
                    binding->buffer, submit_id);
                if (buffer_index < 0) { rc = buffer_index; goto cleanup; }
                uint64_t binding_size = (uint64_t)binding->size;
                if (binding->size == VK_WHOLE_SIZE) {
                    if (binding->offset > binding->buffer->size) { rc = -ERANGE; goto cleanup; }
                    binding_size = (uint64_t)(binding->buffer->size - binding->offset);
                }
                PdockerGpuVulkanGraphicsV6VertexBindingEntry *entry =
                    &vertex_bindings[vertex_binding_count++];
                entry->binding = slot;
                entry->stride = (uint32_t)binding->stride;
                entry->input_rate = 0;
                entry->buffer_resource_index = (uint32_t)buffer_index;
                entry->offset = (uint64_t)binding->offset;
                entry->size = binding_size;
            }
        } else if (record->command_type == PDOCKER_GPU_GRAPHICS_V6_COMMAND_BIND_INDEX_BUFFER) {
            PdockerVkBuffer *index_buffer = cmd->index_buffer;
            if (!index_buffer) { rc = -EPROTO; goto cleanup; }
            int buffer_index = collect_graphics_buffer_resource(
                resources, &resource_count, memory_objects, memory_resource_indices, &memory_count,
                buffer_objects, buffer_resource_indices, &buffer_count, fds, &fd_count,
                index_buffer, submit_id);
            if (buffer_index < 0) { rc = buffer_index; goto cleanup; }
            command->index_buffer_resource_index = (uint32_t)buffer_index;
            command->index_offset = record->index_offset;
            command->index_type = record->index_type;
        } else if (record->command_type == PDOCKER_GPU_GRAPHICS_V6_COMMAND_PUSH_CONSTANTS) {
            if (record->push_op_index >= cmd->push_constant_op_count ||
                push_metadata_count >= PDOCKER_GPU_VULKAN_GRAPHICS_V61_MAX_PUSH_CONSTANT_METADATA) {
                rc = -EPROTO;
                goto cleanup;
            }
            const PdockerVkPushConstantOpSnapshot *push = &cmd->push_constant_ops[record->push_op_index];
            if ((uint64_t)push->offset + (uint64_t)push->size > cmd->push_constant_size ||
                push->size > PDOCKER_VK_MAX_PUSH_BYTES) {
                rc = -ERANGE;
                goto cleanup;
            }
            const uint8_t *push_data = cmd->push_constants + push->offset;
            command->push_size = push->size;
            command->push_hash = fnv1a64_bytes(push_data, push->size);
            rc = frame_append_bytes(frame, PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_FRAME_BYTES,
                                    &cursor, push_data, push->size, &command->push_offset);
            if (rc != 0) goto cleanup;
            PdockerGpuVulkanGraphicsV61PushConstantMetadataEntry *meta =
                &push_metadata[push_metadata_count++];
            meta->command_index = (uint32_t)command_count;
            meta->stage_flags = push->stage_flags;
            meta->layout_id = push->layout_id;
            meta->range_offset = push->offset;
            meta->range_size = push->size;
        } else if (record->command_type == PDOCKER_GPU_GRAPHICS_V6_COMMAND_BARRIER) {
            for (uint32_t mi = 0; mi < record->memory_barrier_op_count; ++mi) {
                uint32_t op_index = record->memory_barrier_op_first + mi;
                if (op_index >= cmd->memory_barrier_op_count ||
                    memory_barrier_count >= PDOCKER_GPU_VULKAN_GRAPHICS_V61_MAX_MEMORY_BARRIERS) {
                    rc = -EPROTO;
                    goto cleanup;
                }
                const PdockerVkMemoryBarrierOp *src = &cmd->memory_barrier_ops[op_index];
                PdockerGpuVulkanGraphicsV61MemoryBarrierEntry *dst = &memory_barriers[memory_barrier_count++];
                dst->command_index = (uint32_t)command_count;
                dst->src_access_mask = (uint64_t)src->src_access_mask;
                dst->dst_access_mask = (uint64_t)src->dst_access_mask;
                dst->src_stage_mask = (uint64_t)src->src_stage_mask;
                dst->dst_stage_mask = (uint64_t)src->dst_stage_mask;
            }
            for (uint32_t bi = 0; bi < record->buffer_barrier_op_count; ++bi) {
                uint32_t op_index = record->buffer_barrier_op_first + bi;
                if (op_index >= cmd->buffer_barrier_op_count ||
                    buffer_barrier_count >= PDOCKER_GPU_VULKAN_GRAPHICS_V61_MAX_BUFFER_BARRIERS) {
                    rc = -EPROTO;
                    goto cleanup;
                }
                const PdockerVkBufferBarrierOp *src = &cmd->buffer_barrier_ops[op_index];
                if (!src->buffer) { rc = -EPROTO; goto cleanup; }
                int buffer_index = collect_graphics_buffer_resource(
                    resources, &resource_count, memory_objects, memory_resource_indices, &memory_count,
                    buffer_objects, buffer_resource_indices, &buffer_count, fds, &fd_count,
                    src->buffer, submit_id);
                if (buffer_index < 0) { rc = buffer_index; goto cleanup; }
                PdockerGpuVulkanGraphicsV61BufferBarrierEntry *dst = &buffer_barriers[buffer_barrier_count++];
                dst->command_index = (uint32_t)command_count;
                dst->resource_index = (uint32_t)buffer_index;
                dst->offset = (uint64_t)src->offset;
                dst->size = (uint64_t)src->size;
                dst->src_access_mask = (uint64_t)src->src_access_mask;
                dst->dst_access_mask = (uint64_t)src->dst_access_mask;
                dst->src_stage_mask = (uint64_t)src->src_stage_mask;
                dst->dst_stage_mask = (uint64_t)src->dst_stage_mask;
                dst->src_queue_family_index = src->src_queue_family_index;
                dst->dst_queue_family_index = src->dst_queue_family_index;
            }
            for (uint32_t bi = 0; bi < record->image_barrier_op_count; ++bi) {
                uint32_t op_index = record->image_barrier_op_first + bi;
                if (op_index >= cmd->image_barrier_op_count ||
                    image_barrier_count >= PDOCKER_GPU_VULKAN_GRAPHICS_V61_MAX_IMAGE_BARRIERS) {
                    rc = -EPROTO;
                    goto cleanup;
                }
                const PdockerVkImageBarrierOp *src = &cmd->image_barrier_ops[op_index];
                if (!src->image) {
                    rc = -EPROTO;
                    goto cleanup;
                }
                int image_index = collect_graphics_image_entry(
                    image_entries, image_objects, &image_count,
                    resources, &resource_count, memory_objects, memory_resource_indices, &memory_count,
                    fds, &fd_count, src->image, submit_id);
                if (image_index < 0) { rc = image_index; goto cleanup; }
                PdockerGpuVulkanGraphicsV61ImageBarrierEntry *dst = &image_barriers[image_barrier_count++];
                dst->command_index = (uint32_t)command_count;
                dst->image_index = (uint32_t)image_index;
                dst->old_layout = (uint32_t)src->old_layout;
                dst->new_layout = (uint32_t)src->new_layout;
                dst->aspect_mask = (uint32_t)src->range.aspectMask;
                dst->base_mip_level = src->range.baseMipLevel;
                dst->level_count = src->range.levelCount;
                dst->base_array_layer = src->range.baseArrayLayer;
                dst->layer_count = src->range.layerCount;
                dst->src_access_mask = (uint64_t)src->src_access_mask;
                dst->dst_access_mask = (uint64_t)src->dst_access_mask;
                dst->src_stage_mask = (uint64_t)src->src_stage_mask;
                dst->dst_stage_mask = (uint64_t)src->dst_stage_mask;
                dst->src_queue_family_index = src->src_queue_family_index;
                dst->dst_queue_family_index = src->dst_queue_family_index;
            }
        } else if (record->command_type == PDOCKER_GPU_GRAPHICS_V6_COMMAND_CLEAR_ATTACHMENTS) {
            if (record->descriptor_bind_snapshot_index >= cmd->clear_attachments_command_op_count) {
                rc = -EPROTO;
                goto cleanup;
            }
            const PdockerVkClearAttachmentsCommandSnapshot *snapshot =
                &cmd->clear_attachments_command_ops[record->descriptor_bind_snapshot_index];
            if (snapshot->clear_attachment_first != record->first_set ||
                snapshot->clear_attachment_count != record->descriptor_set_count ||
                snapshot->clear_rect_first != record->first_dynamic_offset ||
                snapshot->clear_rect_count != record->dynamic_offset_count ||
                snapshot->clear_attachment_first > cmd->clear_attachment_op_count ||
                snapshot->clear_attachment_count > cmd->clear_attachment_op_count - snapshot->clear_attachment_first ||
                snapshot->clear_rect_first > cmd->clear_rect_op_count ||
                snapshot->clear_rect_count > cmd->clear_rect_op_count - snapshot->clear_rect_first ||
                clear_attachments_command_count >= PDOCKER_GPU_VULKAN_GRAPHICS_V616_MAX_CLEAR_ATTACHMENTS_COMMANDS ||
                snapshot->clear_attachment_count > PDOCKER_GPU_VULKAN_GRAPHICS_V616_MAX_CLEAR_ATTACHMENTS - clear_attachment_count ||
                snapshot->clear_rect_count > PDOCKER_GPU_VULKAN_GRAPHICS_V616_MAX_CLEAR_RECTS - clear_rect_count) {
                rc = -EPROTO;
                goto cleanup;
            }
            PdockerGpuVulkanGraphicsV616ClearAttachmentsCommandEntry *clear_command =
                &clear_attachments_commands[clear_attachments_command_count++];
            clear_command->command_index = (uint32_t)command_count;
            clear_command->clear_attachment_first = (uint32_t)clear_attachment_count;
            clear_command->clear_attachment_count = snapshot->clear_attachment_count;
            clear_command->clear_rect_first = (uint32_t)clear_rect_count;
            clear_command->clear_rect_count = snapshot->clear_rect_count;
            for (uint32_t ai = 0; ai < snapshot->clear_attachment_count; ++ai) {
                const PdockerVkClearAttachmentSnapshot *src =
                    &cmd->clear_attachment_ops[snapshot->clear_attachment_first + ai];
                PdockerGpuVulkanGraphicsV616ClearAttachmentEntry *dst =
                    &clear_attachments[clear_attachment_count++];
                dst->aspect_mask = (uint32_t)src->aspect_mask;
                dst->color_attachment = src->color_attachment;
                dst->color_uint32_0 = src->clear_value.color.uint32[0];
                dst->color_uint32_1 = src->clear_value.color.uint32[1];
                dst->color_uint32_2 = src->clear_value.color.uint32[2];
                dst->color_uint32_3 = src->clear_value.color.uint32[3];
                dst->depth_bits = float_bits_u32(src->clear_value.depthStencil.depth);
                dst->stencil = src->clear_value.depthStencil.stencil;
            }
            for (uint32_t ri = 0; ri < snapshot->clear_rect_count; ++ri) {
                const PdockerVkClearRectSnapshot *src =
                    &cmd->clear_rect_ops[snapshot->clear_rect_first + ri];
                PdockerGpuVulkanGraphicsV616ClearRectEntry *dst = &clear_rects[clear_rect_count++];
                dst->rect_offset_x = src->rect.offset.x;
                dst->rect_offset_y = src->rect.offset.y;
                dst->rect_extent_width = src->rect.extent.width;
                dst->rect_extent_height = src->rect.extent.height;
                dst->base_array_layer = src->base_array_layer;
                dst->layer_count = src->layer_count;
            }
            need_v616_clear_attachments = true;
        } else if (record->command_type == PDOCKER_GPU_GRAPHICS_V6_COMMAND_BEGIN_QUERY ||
                   record->command_type == PDOCKER_GPU_GRAPHICS_V6_COMMAND_END_QUERY ||
                   record->command_type == PDOCKER_GPU_GRAPHICS_V6_COMMAND_RESET_QUERY_POOL ||
                   record->command_type == PDOCKER_GPU_GRAPHICS_V6_COMMAND_WRITE_TIMESTAMP) {
            if (record->command_op_sequence >= cmd->command_op_count ||
                query_command_count >= PDOCKER_GPU_VULKAN_GRAPHICS_V617_MAX_QUERY_COMMANDS) {
                rc = -EPROTO;
                goto cleanup;
            }
            const PdockerVkCommandOp *op = &cmd->command_ops[record->command_op_sequence];
            const bool is_begin = record->command_type == PDOCKER_GPU_GRAPHICS_V6_COMMAND_BEGIN_QUERY;
            const bool is_end = record->command_type == PDOCKER_GPU_GRAPHICS_V6_COMMAND_END_QUERY;
            const bool is_reset = record->command_type == PDOCKER_GPU_GRAPHICS_V6_COMMAND_RESET_QUERY_POOL;
            const bool is_timestamp = record->command_type == PDOCKER_GPU_GRAPHICS_V6_COMMAND_WRITE_TIMESTAMP;
            if ((is_begin && op->type != PDOCKER_VK_COMMAND_QUERY_BEGIN) ||
                (is_end && op->type != PDOCKER_VK_COMMAND_QUERY_END) ||
                (is_reset && op->type != PDOCKER_VK_COMMAND_QUERY_RESET) ||
                (is_timestamp && op->type != PDOCKER_VK_COMMAND_QUERY_TIMESTAMP) ||
                !op->query_pool || op->query_pool->result_fd < 0 ||
                !op->query_pool->result_entries ||
                !query_range_valid(op->query_pool, op->query_index, op->query_count)) {
                rc = -EPROTO;
                goto cleanup;
            }
            if ((is_begin || is_end || is_timestamp) && op->query_count != 1) {
                rc = -EPROTO;
                goto cleanup;
            }
            int result_fd_index = -1;
            for (size_t fi = 0; fi < fd_count; ++fi) {
                if (fds[fi] == op->query_pool->result_fd) {
                    result_fd_index = (int)fi;
                    break;
                }
            }
            if (result_fd_index < 0) {
                if (fd_count >= PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_FDS) {
                    rc = -E2BIG;
                    goto cleanup;
                }
                result_fd_index = (int)fd_count;
                fds[fd_count++] = op->query_pool->result_fd;
            }
            PdockerGpuVulkanGraphicsV617QueryCommandEntry *query =
                &query_commands[query_command_count++];
            query->command_index = (uint32_t)command_count;
            if (is_begin) {
                query->op = PDOCKER_GPU_GRAPHICS_V617_QUERY_OP_BEGIN;
            } else if (is_end) {
                query->op = PDOCKER_GPU_GRAPHICS_V617_QUERY_OP_END;
            } else if (is_reset) {
                query->op = PDOCKER_GPU_GRAPHICS_V617_QUERY_OP_RESET;
            } else {
                query->op = PDOCKER_GPU_GRAPHICS_V617_QUERY_OP_WRITE_TIMESTAMP;
            }
            query->query_pool_id = op->query_pool->pool_id;
            query->first_query = op->query_index;
            query->query_count = op->query_count;
            query->stage_mask = (uint64_t)op->query_stage_mask;
            query->result_fd_index = (uint32_t)result_fd_index;
            query->result_stride = sizeof(PdockerGpuVulkanGraphicsV617QueryResultEntry);
            query->result_offset = (uint64_t)op->query_index * sizeof(PdockerGpuVulkanGraphicsV617QueryResultEntry);
            need_v617_query = true;
        } else if (record->command_type == PDOCKER_GPU_GRAPHICS_V6_COMMAND_COPY_QUERY_POOL_RESULTS) {
            if (record->command_op_sequence >= cmd->command_op_count ||
                copy_query_result_count >= PDOCKER_GPU_VULKAN_GRAPHICS_V618_MAX_COPY_QUERY_RESULTS) {
                rc = -EPROTO;
                goto cleanup;
            }
            const PdockerVkCommandOp *op = &cmd->command_ops[record->command_op_sequence];
            if (op->type != PDOCKER_VK_COMMAND_COPY_QUERY_RESULTS ||
                !op->query_pool || op->query_pool->result_fd < 0 ||
                !op->query_dst_buffer || !query_range_valid(op->query_pool, op->query_index, op->query_count)) {
                rc = -EPROTO;
                goto cleanup;
            }
            int dst_resource_index = collect_graphics_buffer_resource(
                resources, &resource_count, memory_objects, memory_resource_indices, &memory_count,
                buffer_objects, buffer_resource_indices, &buffer_count, fds, &fd_count,
                op->query_dst_buffer, submit_id);
            if (dst_resource_index < 0) {
                rc = dst_resource_index;
                goto cleanup;
            }
            int result_fd_index = -1;
            for (size_t fi = 0; fi < fd_count; ++fi) {
                if (fds[fi] == op->query_pool->result_fd) {
                    result_fd_index = (int)fi;
                    break;
                }
            }
            if (result_fd_index < 0) {
                if (fd_count >= PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_FDS) {
                    rc = -E2BIG;
                    goto cleanup;
                }
                result_fd_index = (int)fd_count;
                fds[fd_count++] = op->query_pool->result_fd;
            }
            PdockerGpuVulkanGraphicsV618CopyQueryResultEntry *copy =
                &copy_query_results[copy_query_result_count++];
            copy->command_index = (uint32_t)command_count;
            copy->dst_resource_index = (uint32_t)dst_resource_index;
            copy->query_pool_id = op->query_pool->pool_id;
            copy->first_query = op->query_index;
            copy->query_count = op->query_count;
            copy->query_type = (uint32_t)op->query_pool->type;
            copy->flags = (uint32_t)op->query_result_flags;
            copy->result_fd_index = (uint32_t)result_fd_index;
            copy->dst_offset = (uint64_t)op->query_dst_offset;
            copy->stride = (uint64_t)op->query_stride;
            need_v617_query = true;
            need_v618_copy_query = true;
        } else if (record->command_type == PDOCKER_GPU_GRAPHICS_V6_COMMAND_DRAW ||
                   record->command_type == PDOCKER_GPU_GRAPHICS_V6_COMMAND_DRAW_INDEXED) {
            if (record->draw_snapshot_index >= cmd->graphics_draw_op_count) {
                rc = -EPROTO;
                goto cleanup;
            }
            const PdockerVkGraphicsDrawSnapshot *draw =
                &cmd->graphics_draw_ops[record->draw_snapshot_index];
            if (draw->indirect) {
                if (!draw->indirect_buffer || !draw->indirect_buffer->memory ||
                    indirect_draw_count >= PDOCKER_GPU_VULKAN_GRAPHICS_V68_MAX_INDIRECT_DRAWS) {
                    rc = -EPROTO;
                    goto cleanup;
                }
                const uint64_t command_size = record->command_type == PDOCKER_GPU_GRAPHICS_V6_COMMAND_DRAW_INDEXED
                    ? (uint64_t)sizeof(VkDrawIndexedIndirectCommand)
                    : (uint64_t)sizeof(VkDrawIndirectCommand);
                const uint32_t draw_count = draw->vertex_count;
                if (draw_count == 0 || (draw->indirect_stride % 4u) != 0 ||
                    draw->indirect_stride < command_size) {
                    rc = -EINVAL;
                    goto cleanup;
                }
                uint64_t last_offset = 0;
                uint64_t indirect_bytes = 0;
                if (!checked_mul_u64((uint64_t)(draw_count - 1u), (uint64_t)draw->indirect_stride, &last_offset) ||
                    last_offset > UINT64_MAX - command_size) {
                    rc = -EOVERFLOW;
                    goto cleanup;
                }
                indirect_bytes = last_offset + command_size;
                if (draw->indirect_offset > draw->indirect_buffer->size ||
                    indirect_bytes > (uint64_t)draw->indirect_buffer->size - draw->indirect_offset) {
                    rc = -ERANGE;
                    goto cleanup;
                }
                int indirect_buffer_index = collect_graphics_buffer_resource(
                    resources, &resource_count, memory_objects, memory_resource_indices, &memory_count,
                    buffer_objects, buffer_resource_indices, &buffer_count, fds, &fd_count,
                    draw->indirect_buffer, submit_id);
                if (indirect_buffer_index < 0) { rc = indirect_buffer_index; goto cleanup; }
                uint32_t count_buffer_index = PDOCKER_GPU_GRAPHICS_V68_INDEX_NONE;
                uint32_t indirect_flags = 0;
                if (draw->count_buffer) {
                    if (!draw->count_buffer->memory || draw->count_offset > draw->count_buffer->size ||
                        (uint64_t)sizeof(uint32_t) > (uint64_t)draw->count_buffer->size - draw->count_offset) {
                        rc = -ERANGE;
                        goto cleanup;
                    }
                    int count_index = collect_graphics_buffer_resource(
                        resources, &resource_count, memory_objects, memory_resource_indices, &memory_count,
                        buffer_objects, buffer_resource_indices, &buffer_count, fds, &fd_count,
                        draw->count_buffer, submit_id);
                    if (count_index < 0) { rc = count_index; goto cleanup; }
                    count_buffer_index = (uint32_t)count_index;
                    indirect_flags |= PDOCKER_GPU_GRAPHICS_V68_INDIRECT_DRAW_COUNT_BUFFER_PRESENT;
                }
                PdockerGpuVulkanGraphicsV68IndirectDrawEntry *indirect = &indirect_draws[indirect_draw_count++];
                indirect->command_index = (uint32_t)command_count;
                indirect->flags = indirect_flags;
                indirect->indirect_resource_index = (uint32_t)indirect_buffer_index;
                indirect->count_resource_index = count_buffer_index;
                indirect->indirect_offset = (uint64_t)draw->indirect_offset;
                indirect->count_offset = (uint64_t)draw->count_offset;
                indirect->draw_count = draw_count;
                indirect->stride = draw->indirect_stride;
                need_v68_indirect_draw = true;
            }
            if (draw->index_buffer_bound) {
                if (!draw->index_buffer) { rc = -EPROTO; goto cleanup; }
                int buffer_index = collect_graphics_buffer_resource(
                    resources, &resource_count, memory_objects, memory_resource_indices, &memory_count,
                    buffer_objects, buffer_resource_indices, &buffer_count, fds, &fd_count,
                    draw->index_buffer, submit_id);
                if (buffer_index < 0) { rc = buffer_index; goto cleanup; }
                command->index_buffer_resource_index = (uint32_t)buffer_index;
                command->index_offset = draw->index_offset;
                command->index_type = draw->index_type;
            }
            command->first_vertex = draw->first_vertex;
            command->vertex_count = draw->vertex_count;
            command->first_index = draw->first_index;
            command->index_count = draw->index_count;
            command->vertex_offset = draw->vertex_offset;
            command->first_instance = draw->first_instance;
            command->instance_count = draw->instance_count;
        }
        command_count++;
    }
    APPEND_INTERLEAVED_GRAPHICS_BUFFER_COPIES(UINT32_MAX);
#undef APPEND_INTERLEAVED_GRAPHICS_BUFFER_COPIES

    memcpy(header->magic, PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAGIC, 8);
    if (need_v618_copy_query) {
        header->header_size = sizeof(*frame_header_v618);
        header->abi_minor = PDOCKER_GPU_VULKAN_GRAPHICS_V618_ABI_MINOR;
    } else if (need_v617_query) {
        header->header_size = sizeof(*frame_header_v617);
        header->abi_minor = PDOCKER_GPU_VULKAN_GRAPHICS_V617_ABI_MINOR;
    } else if (need_v616_clear_attachments) {
        header->header_size = sizeof(*frame_header_v616);
        header->abi_minor = PDOCKER_GPU_VULKAN_GRAPHICS_V616_ABI_MINOR;
    } else if (need_v615_blit_image) {
        header->header_size = sizeof(*frame_header_v615);
        header->abi_minor = PDOCKER_GPU_VULKAN_GRAPHICS_V615_ABI_MINOR;
    } else if (need_v614_resolve_image) {
        header->header_size = sizeof(*frame_header_v614);
        header->abi_minor = PDOCKER_GPU_VULKAN_GRAPHICS_V614_ABI_MINOR;
    } else if (need_v613_clear_depth_stencil) {
        header->header_size = sizeof(*frame_header_v613);
        header->abi_minor = PDOCKER_GPU_VULKAN_GRAPHICS_V613_ABI_MINOR;
    } else if (need_v612_clear_color) {
        header->header_size = sizeof(*frame_header_v612);
        header->abi_minor = PDOCKER_GPU_VULKAN_GRAPHICS_V612_ABI_MINOR;
    } else if (need_v611_buffer_write) {
        header->header_size = sizeof(*frame_header_v611);
        header->abi_minor = PDOCKER_GPU_VULKAN_GRAPHICS_V611_ABI_MINOR;
    } else if (need_v610_image_copy) {
        header->header_size = sizeof(*frame_header_v610);
        header->abi_minor = PDOCKER_GPU_VULKAN_GRAPHICS_V610_ABI_MINOR;
    } else if (need_v69_buffer_copy) {
        header->header_size = sizeof(*frame_header_v69);
        header->abi_minor = PDOCKER_GPU_VULKAN_GRAPHICS_V69_ABI_MINOR;
    } else if (need_v68_indirect_draw) {
        header->header_size = sizeof(*frame_header_v68);
        header->abi_minor = PDOCKER_GPU_VULKAN_GRAPHICS_V68_ABI_MINOR;
    } else if (need_v67_viewport_scissor_state) {
        header->header_size = sizeof(*frame_header_v67);
        header->abi_minor = PDOCKER_GPU_VULKAN_GRAPHICS_V67_ABI_MINOR;
    } else if (need_v66_color_blend_state) {
        header->header_size = sizeof(*frame_header_v66);
        header->abi_minor = PDOCKER_GPU_VULKAN_GRAPHICS_V66_ABI_MINOR;
    } else if (need_v65_static_pipeline_state) {
        header->header_size = sizeof(*frame_header_v65);
        header->abi_minor = PDOCKER_GPU_VULKAN_GRAPHICS_V65_ABI_MINOR;
    } else if (need_v64_resolve_attachment) {
        header->header_size = sizeof(*frame_header_v64);
        header->abi_minor = PDOCKER_GPU_VULKAN_GRAPHICS_V64_ABI_MINOR;
    } else if (need_v63_depth_stencil) {
        header->header_size = sizeof(*frame_header_v63);
        header->abi_minor = PDOCKER_GPU_VULKAN_GRAPHICS_V63_ABI_MINOR;
    } else if (need_v62_specialization) {
        header->header_size = sizeof(*frame_header_v62);
        header->abi_minor = PDOCKER_GPU_VULKAN_GRAPHICS_V62_ABI_MINOR;
    } else {
        header->header_size = sizeof(*frame_header);
        header->abi_minor = PDOCKER_GPU_VULKAN_GRAPHICS_V61_ABI_MINOR;
    }
    header->abi_major = PDOCKER_GPU_VULKAN_GRAPHICS_V6_ABI_MAJOR;
    if (!need_v611_buffer_write && !need_v612_clear_color &&
        !need_v613_clear_depth_stencil && !need_v614_resolve_image &&
        !need_v615_blit_image && !need_v616_clear_attachments &&
        !need_v617_query && !need_v618_copy_query) {
        cursor = header->header_size;
    }
    header->command = PDOCKER_GPU_VULKAN_GRAPHICS_V6_COMMAND_SUBMIT;
    header->submit_id = submit_id;
    header->fd_count = (uint32_t)fd_count;
    header->resource_count = (uint32_t)resource_count;
    header->resource_entry_size = sizeof(PdockerGpuVulkanDispatchV5ResourceEntry);
    header->resource_schema_hash = PDOCKER_GPU_VULKAN_DISPATCH_V5_RESOURCE_SCHEMA_HASH;
    header->descriptor_count = (uint32_t)descriptor_count;
    header->descriptor_entry_size = sizeof(PdockerGpuVulkanDispatchV5DescriptorObjectEntry);
    header->descriptor_schema_hash = PDOCKER_GPU_VULKAN_DISPATCH_V5_DESCRIPTOR_OBJECT_SCHEMA_HASH;
    header->image_count = (uint32_t)image_count;
    header->image_entry_size = sizeof(PdockerGpuVulkanDispatchV5ImageEntry);
    header->image_schema_hash = PDOCKER_GPU_VULKAN_DISPATCH_V5_IMAGE_SCHEMA_HASH;
    header->image_view_count = (uint32_t)image_view_count;
    header->image_view_entry_size = sizeof(PdockerGpuVulkanDispatchV5ImageViewEntry);
    header->image_view_schema_hash = PDOCKER_GPU_VULKAN_DISPATCH_V5_IMAGE_VIEW_SCHEMA_HASH;
    header->sampler_count = (uint32_t)sampler_count;
    header->sampler_entry_size = sizeof(PdockerGpuVulkanDispatchV5SamplerEntry);
    header->sampler_schema_hash = PDOCKER_GPU_VULKAN_DISPATCH_V5_SAMPLER_SCHEMA_HASH;
    header->shader_stage_count = (uint32_t)shader_stage_count;
    header->shader_stage_entry_size = sizeof(PdockerGpuVulkanGraphicsV6ShaderStageEntry);
    header->shader_stage_schema_hash = PDOCKER_GPU_VULKAN_GRAPHICS_V6_SHADER_STAGE_SCHEMA_HASH;
    header->pipeline_count = (uint32_t)pipeline_count;
    header->pipeline_entry_size = sizeof(PdockerGpuVulkanGraphicsV6PipelineEntry);
    header->pipeline_schema_hash = PDOCKER_GPU_VULKAN_GRAPHICS_V6_PIPELINE_SCHEMA_HASH;
    header->vertex_binding_count = (uint32_t)vertex_binding_count;
    header->vertex_binding_entry_size = sizeof(PdockerGpuVulkanGraphicsV6VertexBindingEntry);
    header->vertex_binding_schema_hash = PDOCKER_GPU_VULKAN_GRAPHICS_V6_VERTEX_BINDING_SCHEMA_HASH;
    header->vertex_attribute_count = (uint32_t)vertex_attribute_count;
    header->vertex_attribute_entry_size = sizeof(PdockerGpuVulkanGraphicsV6VertexAttributeEntry);
    header->vertex_attribute_schema_hash = PDOCKER_GPU_VULKAN_GRAPHICS_V6_VERTEX_ATTRIBUTE_SCHEMA_HASH;
    header->attachment_count = (uint32_t)attachment_count;
    header->attachment_entry_size = sizeof(PdockerGpuVulkanGraphicsV6AttachmentEntry);
    header->attachment_schema_hash = PDOCKER_GPU_VULKAN_GRAPHICS_V6_ATTACHMENT_SCHEMA_HASH;
    header->dynamic_state_count = (uint32_t)dynamic_state_count;
    header->dynamic_state_entry_size = sizeof(PdockerGpuVulkanGraphicsV6DynamicStateEntry);
    header->dynamic_state_schema_hash = PDOCKER_GPU_VULKAN_GRAPHICS_V6_DYNAMIC_STATE_SCHEMA_HASH;
    header->command_count = (uint32_t)command_count;
    header->command_entry_size = sizeof(PdockerGpuVulkanGraphicsV6CommandEntry);
    header->command_schema_hash = PDOCKER_GPU_VULKAN_GRAPHICS_V6_COMMAND_SCHEMA_HASH;
    frame_header->v61.dynamic_offset_count = (uint32_t)dynamic_offset_count;
    frame_header->v61.dynamic_offset_entry_size = sizeof(PdockerGpuVulkanGraphicsV61DynamicOffsetEntry);
    frame_header->v61.dynamic_offset_schema_hash = PDOCKER_GPU_VULKAN_GRAPHICS_V61_DYNAMIC_OFFSET_SCHEMA_HASH;
    frame_header->v61.push_constant_metadata_count = (uint32_t)push_metadata_count;
    frame_header->v61.push_constant_metadata_entry_size = sizeof(PdockerGpuVulkanGraphicsV61PushConstantMetadataEntry);
    frame_header->v61.push_constant_metadata_schema_hash = PDOCKER_GPU_VULKAN_GRAPHICS_V61_PUSH_CONSTANT_METADATA_SCHEMA_HASH;
    frame_header->v61.image_barrier_count = (uint32_t)image_barrier_count;
    frame_header->v61.image_barrier_entry_size = sizeof(PdockerGpuVulkanGraphicsV61ImageBarrierEntry);
    frame_header->v61.image_barrier_schema_hash = PDOCKER_GPU_VULKAN_GRAPHICS_V61_IMAGE_BARRIER_SCHEMA_HASH;
    frame_header->v61.memory_barrier_count = (uint32_t)memory_barrier_count;
    frame_header->v61.memory_barrier_entry_size = sizeof(PdockerGpuVulkanGraphicsV61MemoryBarrierEntry);
    frame_header->v61.memory_barrier_schema_hash = PDOCKER_GPU_VULKAN_GRAPHICS_V61_MEMORY_BARRIER_SCHEMA_HASH;
    frame_header->v61.buffer_barrier_count = (uint32_t)buffer_barrier_count;
    frame_header->v61.buffer_barrier_entry_size = sizeof(PdockerGpuVulkanGraphicsV61BufferBarrierEntry);
    frame_header->v61.buffer_barrier_schema_hash = PDOCKER_GPU_VULKAN_GRAPHICS_V61_BUFFER_BARRIER_SCHEMA_HASH;
    if (need_v62_specialization || need_v63_depth_stencil || need_v64_resolve_attachment || need_v65_static_pipeline_state || need_v66_color_blend_state || need_v67_viewport_scissor_state || need_v68_indirect_draw || need_v69_buffer_copy || need_v610_image_copy || need_v611_buffer_write || need_v612_clear_color || need_v613_clear_depth_stencil || need_v614_resolve_image || need_v615_blit_image || need_v616_clear_attachments || need_v617_query || need_v618_copy_query) {
        frame_header_v62->v62.specialization_entry_count = (uint32_t)specialization_entry_count;
        frame_header_v62->v62.specialization_entry_size = sizeof(PdockerGpuVulkanGraphicsV62SpecializationEntry);
        frame_header_v62->v62.specialization_entry_schema_hash = PDOCKER_GPU_VULKAN_GRAPHICS_V62_SPECIALIZATION_ENTRY_SCHEMA_HASH;
    }
    if (need_v63_depth_stencil || need_v64_resolve_attachment || need_v65_static_pipeline_state || need_v66_color_blend_state || need_v67_viewport_scissor_state || need_v68_indirect_draw || need_v69_buffer_copy || need_v610_image_copy || need_v611_buffer_write || need_v612_clear_color || need_v613_clear_depth_stencil || need_v614_resolve_image || need_v615_blit_image || need_v616_clear_attachments || need_v617_query || need_v618_copy_query) {
        frame_header_v63->v63.depth_stencil_state_count = (uint32_t)depth_stencil_state_count;
        frame_header_v63->v63.depth_stencil_state_entry_size = sizeof(PdockerGpuVulkanGraphicsV63DepthStencilStateEntry);
        frame_header_v63->v63.depth_stencil_state_schema_hash = PDOCKER_GPU_VULKAN_GRAPHICS_V63_DEPTH_STENCIL_STATE_SCHEMA_HASH;
    }
    if (need_v64_resolve_attachment || need_v65_static_pipeline_state || need_v66_color_blend_state || need_v67_viewport_scissor_state || need_v68_indirect_draw || need_v69_buffer_copy || need_v610_image_copy || need_v611_buffer_write || need_v612_clear_color || need_v613_clear_depth_stencil || need_v614_resolve_image || need_v615_blit_image || need_v616_clear_attachments || need_v617_query || need_v618_copy_query) {
        frame_header_v64->v64.resolve_attachment_count = (uint32_t)resolve_attachment_count;
        frame_header_v64->v64.resolve_attachment_entry_size = sizeof(PdockerGpuVulkanGraphicsV64ResolveAttachmentEntry);
        frame_header_v64->v64.resolve_attachment_schema_hash = PDOCKER_GPU_VULKAN_GRAPHICS_V64_RESOLVE_ATTACHMENT_SCHEMA_HASH;
    }
    if (need_v65_static_pipeline_state || need_v66_color_blend_state || need_v67_viewport_scissor_state || need_v68_indirect_draw || need_v69_buffer_copy || need_v610_image_copy || need_v611_buffer_write || need_v612_clear_color || need_v613_clear_depth_stencil || need_v614_resolve_image || need_v615_blit_image || need_v616_clear_attachments || need_v617_query || need_v618_copy_query) {
        frame_header_v65->v65.static_pipeline_state_count = (uint32_t)static_pipeline_state_count;
        frame_header_v65->v65.static_pipeline_state_entry_size = sizeof(PdockerGpuVulkanGraphicsV65StaticPipelineStateEntry);
        frame_header_v65->v65.static_pipeline_state_schema_hash = PDOCKER_GPU_VULKAN_GRAPHICS_V65_STATIC_PIPELINE_STATE_SCHEMA_HASH;
    }
    if (need_v66_color_blend_state || need_v67_viewport_scissor_state || need_v68_indirect_draw || need_v69_buffer_copy || need_v610_image_copy || need_v611_buffer_write || need_v612_clear_color || need_v613_clear_depth_stencil || need_v614_resolve_image || need_v615_blit_image || need_v616_clear_attachments || need_v617_query || need_v618_copy_query) {
        frame_header_v66->v66.color_blend_state_count = (uint32_t)color_blend_state_count;
        frame_header_v66->v66.color_blend_state_entry_size = sizeof(PdockerGpuVulkanGraphicsV66ColorBlendStateEntry);
        frame_header_v66->v66.color_blend_state_schema_hash = PDOCKER_GPU_VULKAN_GRAPHICS_V66_COLOR_BLEND_STATE_SCHEMA_HASH;
        frame_header_v66->v66.color_blend_attachment_count = (uint32_t)color_blend_attachment_count;
        frame_header_v66->v66.color_blend_attachment_entry_size = sizeof(PdockerGpuVulkanGraphicsV66ColorBlendAttachmentEntry);
        frame_header_v66->v66.color_blend_attachment_schema_hash = PDOCKER_GPU_VULKAN_GRAPHICS_V66_COLOR_BLEND_ATTACHMENT_SCHEMA_HASH;
    }
    if (need_v67_viewport_scissor_state || need_v68_indirect_draw || need_v69_buffer_copy || need_v610_image_copy || need_v611_buffer_write || need_v612_clear_color || need_v613_clear_depth_stencil || need_v614_resolve_image || need_v615_blit_image || need_v616_clear_attachments || need_v617_query || need_v618_copy_query) {
        frame_header_v67->v67.viewport_scissor_state_count = (uint32_t)viewport_scissor_state_count;
        frame_header_v67->v67.viewport_scissor_state_entry_size = sizeof(PdockerGpuVulkanGraphicsV67ViewportScissorStateEntry);
        frame_header_v67->v67.viewport_scissor_state_schema_hash = PDOCKER_GPU_VULKAN_GRAPHICS_V67_VIEWPORT_SCISSOR_STATE_SCHEMA_HASH;
        frame_header_v67->v67.viewport_count = (uint32_t)viewport_entry_count;
        frame_header_v67->v67.viewport_entry_size = sizeof(PdockerGpuVulkanGraphicsV67ViewportEntry);
        frame_header_v67->v67.viewport_schema_hash = PDOCKER_GPU_VULKAN_GRAPHICS_V67_VIEWPORT_SCHEMA_HASH;
        frame_header_v67->v67.scissor_count = (uint32_t)scissor_entry_count;
        frame_header_v67->v67.scissor_entry_size = sizeof(PdockerGpuVulkanGraphicsV67ScissorEntry);
        frame_header_v67->v67.scissor_schema_hash = PDOCKER_GPU_VULKAN_GRAPHICS_V67_SCISSOR_SCHEMA_HASH;
    }
    if (need_v68_indirect_draw || need_v69_buffer_copy || need_v610_image_copy || need_v611_buffer_write || need_v612_clear_color || need_v613_clear_depth_stencil || need_v614_resolve_image || need_v615_blit_image || need_v616_clear_attachments || need_v617_query || need_v618_copy_query) {
        frame_header_v68->v68.indirect_draw_count = (uint32_t)indirect_draw_count;
        frame_header_v68->v68.indirect_draw_entry_size = sizeof(PdockerGpuVulkanGraphicsV68IndirectDrawEntry);
        frame_header_v68->v68.indirect_draw_schema_hash = PDOCKER_GPU_VULKAN_GRAPHICS_V68_INDIRECT_DRAW_SCHEMA_HASH;
    }
    if (need_v69_buffer_copy || need_v610_image_copy || need_v611_buffer_write || need_v612_clear_color || need_v613_clear_depth_stencil || need_v614_resolve_image || need_v615_blit_image || need_v616_clear_attachments || need_v617_query || need_v618_copy_query) {
        frame_header_v69->v69.buffer_copy_count = (uint32_t)buffer_copy_count;
        frame_header_v69->v69.buffer_copy_entry_size = sizeof(PdockerGpuVulkanGraphicsV69BufferCopyEntry);
        frame_header_v69->v69.buffer_copy_schema_hash = PDOCKER_GPU_VULKAN_GRAPHICS_V69_BUFFER_COPY_SCHEMA_HASH;
    }
    if (need_v610_image_copy || need_v611_buffer_write || need_v612_clear_color || need_v613_clear_depth_stencil || need_v614_resolve_image || need_v615_blit_image || need_v616_clear_attachments || need_v617_query || need_v618_copy_query) {
        frame_header_v610->v610.buffer_image_copy_count = (uint32_t)buffer_image_copy_count;
        frame_header_v610->v610.buffer_image_copy_entry_size = sizeof(PdockerGpuVulkanGraphicsV610BufferImageCopyEntry);
        frame_header_v610->v610.buffer_image_copy_schema_hash = PDOCKER_GPU_VULKAN_GRAPHICS_V610_BUFFER_IMAGE_COPY_SCHEMA_HASH;
        frame_header_v610->v610.image_copy_count = (uint32_t)image_copy_count;
        frame_header_v610->v610.image_copy_entry_size = sizeof(PdockerGpuVulkanGraphicsV610ImageCopyEntry);
        frame_header_v610->v610.image_copy_schema_hash = PDOCKER_GPU_VULKAN_GRAPHICS_V610_IMAGE_COPY_SCHEMA_HASH;
    }
    if (need_v611_buffer_write || need_v612_clear_color || need_v613_clear_depth_stencil || need_v614_resolve_image || need_v615_blit_image || need_v616_clear_attachments || need_v617_query || need_v618_copy_query) {
        frame_header_v611->v611.fill_buffer_count = (uint32_t)fill_buffer_count;
        frame_header_v611->v611.fill_buffer_entry_size = sizeof(PdockerGpuVulkanGraphicsV611FillBufferEntry);
        frame_header_v611->v611.fill_buffer_schema_hash = PDOCKER_GPU_VULKAN_GRAPHICS_V611_FILL_BUFFER_SCHEMA_HASH;
        frame_header_v611->v611.update_buffer_count = (uint32_t)update_buffer_count;
        frame_header_v611->v611.update_buffer_entry_size = sizeof(PdockerGpuVulkanGraphicsV611UpdateBufferEntry);
        frame_header_v611->v611.update_buffer_schema_hash = PDOCKER_GPU_VULKAN_GRAPHICS_V611_UPDATE_BUFFER_SCHEMA_HASH;
        frame_header_v611->v611.update_payload_data_offset = update_payload_data_offset;
        frame_header_v611->v611.update_payload_data_size = update_payload_data_size;
        frame_header_v611->v611.update_payload_data_hash = update_payload_data_size
            ? fnv1a64_bytes(frame + update_payload_data_offset, (size_t)update_payload_data_size)
            : 1469598103934665603ull;
    }
    if (need_v612_clear_color || need_v613_clear_depth_stencil || need_v614_resolve_image || need_v615_blit_image || need_v616_clear_attachments || need_v617_query || need_v618_copy_query) {
        frame_header_v612->v612.clear_color_image_count = (uint32_t)clear_color_image_count;
        frame_header_v612->v612.clear_color_image_entry_size = sizeof(PdockerGpuVulkanGraphicsV612ClearColorImageEntry);
        frame_header_v612->v612.clear_color_image_schema_hash = PDOCKER_GPU_VULKAN_GRAPHICS_V612_CLEAR_COLOR_IMAGE_SCHEMA_HASH;
    }
    if (need_v613_clear_depth_stencil || need_v614_resolve_image || need_v615_blit_image || need_v616_clear_attachments || need_v617_query || need_v618_copy_query) {
        frame_header_v613->v613.clear_depth_stencil_image_count = (uint32_t)clear_depth_stencil_image_count;
        frame_header_v613->v613.clear_depth_stencil_image_entry_size = sizeof(PdockerGpuVulkanGraphicsV613ClearDepthStencilImageEntry);
        frame_header_v613->v613.clear_depth_stencil_image_schema_hash = PDOCKER_GPU_VULKAN_GRAPHICS_V613_CLEAR_DEPTH_STENCIL_IMAGE_SCHEMA_HASH;
    }
    if (need_v614_resolve_image || need_v615_blit_image || need_v616_clear_attachments || need_v617_query || need_v618_copy_query) {
        frame_header_v614->v614.resolve_image_count = (uint32_t)resolve_image_count;
        frame_header_v614->v614.resolve_image_entry_size = sizeof(PdockerGpuVulkanGraphicsV614ResolveImageEntry);
        frame_header_v614->v614.resolve_image_schema_hash = PDOCKER_GPU_VULKAN_GRAPHICS_V614_RESOLVE_IMAGE_SCHEMA_HASH;
    }
    if (need_v615_blit_image || need_v616_clear_attachments || need_v617_query || need_v618_copy_query) {
        frame_header_v615->v615.blit_image_count = (uint32_t)blit_image_count;
        frame_header_v615->v615.blit_image_entry_size = sizeof(PdockerGpuVulkanGraphicsV615BlitImageEntry);
        frame_header_v615->v615.blit_image_schema_hash = PDOCKER_GPU_VULKAN_GRAPHICS_V615_BLIT_IMAGE_SCHEMA_HASH;
    }
    if (need_v616_clear_attachments || need_v617_query || need_v618_copy_query) {
        frame_header_v616->v616.clear_attachments_command_count = (uint32_t)clear_attachments_command_count;
        frame_header_v616->v616.clear_attachments_command_entry_size = sizeof(PdockerGpuVulkanGraphicsV616ClearAttachmentsCommandEntry);
        frame_header_v616->v616.clear_attachments_command_schema_hash = PDOCKER_GPU_VULKAN_GRAPHICS_V616_CLEAR_ATTACHMENTS_COMMAND_SCHEMA_HASH;
        frame_header_v616->v616.clear_attachment_count = (uint32_t)clear_attachment_count;
        frame_header_v616->v616.clear_attachment_entry_size = sizeof(PdockerGpuVulkanGraphicsV616ClearAttachmentEntry);
        frame_header_v616->v616.clear_attachment_schema_hash = PDOCKER_GPU_VULKAN_GRAPHICS_V616_CLEAR_ATTACHMENT_SCHEMA_HASH;
        frame_header_v616->v616.clear_rect_count = (uint32_t)clear_rect_count;
        frame_header_v616->v616.clear_rect_entry_size = sizeof(PdockerGpuVulkanGraphicsV616ClearRectEntry);
        frame_header_v616->v616.clear_rect_schema_hash = PDOCKER_GPU_VULKAN_GRAPHICS_V616_CLEAR_RECT_SCHEMA_HASH;
    }
    if (need_v617_query) {
        frame_header_v617->v617.query_command_count = (uint32_t)query_command_count;
        frame_header_v617->v617.query_command_entry_size = sizeof(PdockerGpuVulkanGraphicsV617QueryCommandEntry);
        frame_header_v617->v617.query_command_schema_hash = PDOCKER_GPU_VULKAN_GRAPHICS_V617_QUERY_COMMAND_SCHEMA_HASH;
        frame_header_v617->v617.query_result_entry_size = sizeof(PdockerGpuVulkanGraphicsV617QueryResultEntry);
        frame_header_v617->v617.query_result_schema_hash = PDOCKER_GPU_VULKAN_GRAPHICS_V617_QUERY_RESULT_SCHEMA_HASH;
    }
    if (need_v618_copy_query) {
        frame_header_v618->v618.copy_query_result_count = (uint32_t)copy_query_result_count;
        frame_header_v618->v618.copy_query_result_entry_size = sizeof(PdockerGpuVulkanGraphicsV618CopyQueryResultEntry);
        frame_header_v618->v618.copy_query_result_schema_hash = PDOCKER_GPU_VULKAN_GRAPHICS_V618_COPY_QUERY_RESULT_SCHEMA_HASH;
    }

#define APPEND_GRAPHICS_TABLE(data_, count_, entry_size_, offset_field_, size_field_) \
    do { \
        if ((count_) > 0) { \
            rc = frame_append_bytes(frame, PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_FRAME_BYTES, \
                                    &cursor, (data_), (entry_size_) * (count_), &(offset_field_)); \
            if (rc != 0) goto cleanup; \
            (size_field_) = (uint64_t)((entry_size_) * (count_)); \
        } \
    } while (0)
    APPEND_GRAPHICS_TABLE(resources, resource_count, sizeof(resources[0]),
                          header->resource_table_offset, header->resource_table_size);
    APPEND_GRAPHICS_TABLE(descriptors, descriptor_count, sizeof(descriptors[0]),
                          header->descriptor_table_offset, header->descriptor_table_size);
    APPEND_GRAPHICS_TABLE(image_entries, image_count, sizeof(image_entries[0]),
                          header->image_table_offset, header->image_table_size);
    APPEND_GRAPHICS_TABLE(image_view_entries, image_view_count, sizeof(image_view_entries[0]),
                          header->image_view_table_offset, header->image_view_table_size);
    APPEND_GRAPHICS_TABLE(sampler_entries, sampler_count, sizeof(sampler_entries[0]),
                          header->sampler_table_offset, header->sampler_table_size);
    APPEND_GRAPHICS_TABLE(shader_stages, shader_stage_count, sizeof(shader_stages[0]),
                          header->shader_stage_table_offset, header->shader_stage_table_size);
    APPEND_GRAPHICS_TABLE(pipelines, pipeline_count, sizeof(pipelines[0]),
                          header->pipeline_table_offset, header->pipeline_table_size);
    APPEND_GRAPHICS_TABLE(vertex_bindings, vertex_binding_count, sizeof(vertex_bindings[0]),
                          header->vertex_binding_table_offset, header->vertex_binding_table_size);
    APPEND_GRAPHICS_TABLE(vertex_attributes, vertex_attribute_count, sizeof(vertex_attributes[0]),
                          header->vertex_attribute_table_offset, header->vertex_attribute_table_size);
    APPEND_GRAPHICS_TABLE(attachments, attachment_count, sizeof(attachments[0]),
                          header->attachment_table_offset, header->attachment_table_size);
    APPEND_GRAPHICS_TABLE(dynamic_states, dynamic_state_count, sizeof(dynamic_states[0]),
                          header->dynamic_state_table_offset, header->dynamic_state_table_size);
    APPEND_GRAPHICS_TABLE(commands, command_count, sizeof(commands[0]),
                          header->command_table_offset, header->command_table_size);
    APPEND_GRAPHICS_TABLE(dynamic_offsets, dynamic_offset_count, sizeof(dynamic_offsets[0]),
                          frame_header->v61.dynamic_offset_table_offset,
                          frame_header->v61.dynamic_offset_table_size);
    APPEND_GRAPHICS_TABLE(push_metadata, push_metadata_count, sizeof(push_metadata[0]),
                          frame_header->v61.push_constant_metadata_table_offset,
                          frame_header->v61.push_constant_metadata_table_size);
    APPEND_GRAPHICS_TABLE(image_barriers, image_barrier_count, sizeof(image_barriers[0]),
                          frame_header->v61.image_barrier_table_offset,
                          frame_header->v61.image_barrier_table_size);
    APPEND_GRAPHICS_TABLE(memory_barriers, memory_barrier_count, sizeof(memory_barriers[0]),
                          frame_header->v61.memory_barrier_table_offset,
                          frame_header->v61.memory_barrier_table_size);
    APPEND_GRAPHICS_TABLE(buffer_barriers, buffer_barrier_count, sizeof(buffer_barriers[0]),
                          frame_header->v61.buffer_barrier_table_offset,
                          frame_header->v61.buffer_barrier_table_size);
    if (need_v62_specialization || need_v63_depth_stencil || need_v64_resolve_attachment || need_v65_static_pipeline_state || need_v66_color_blend_state || need_v67_viewport_scissor_state || need_v68_indirect_draw || need_v69_buffer_copy || need_v610_image_copy || need_v611_buffer_write || need_v612_clear_color || need_v613_clear_depth_stencil || need_v614_resolve_image || need_v615_blit_image || need_v616_clear_attachments || need_v617_query || need_v618_copy_query) {
        APPEND_GRAPHICS_TABLE(specialization_entries, specialization_entry_count,
                              sizeof(specialization_entries[0]),
                              frame_header_v62->v62.specialization_entry_table_offset,
                              frame_header_v62->v62.specialization_entry_table_size);
    }
    if (need_v63_depth_stencil || need_v64_resolve_attachment || need_v65_static_pipeline_state || need_v66_color_blend_state || need_v67_viewport_scissor_state || need_v68_indirect_draw || need_v69_buffer_copy || need_v610_image_copy || need_v611_buffer_write || need_v612_clear_color || need_v613_clear_depth_stencil || need_v614_resolve_image || need_v615_blit_image || need_v616_clear_attachments || need_v617_query || need_v618_copy_query) {
        APPEND_GRAPHICS_TABLE(depth_stencil_states, depth_stencil_state_count,
                              sizeof(depth_stencil_states[0]),
                              frame_header_v63->v63.depth_stencil_state_table_offset,
                              frame_header_v63->v63.depth_stencil_state_table_size);
    }
    if (need_v64_resolve_attachment || need_v65_static_pipeline_state || need_v66_color_blend_state || need_v67_viewport_scissor_state || need_v68_indirect_draw || need_v69_buffer_copy || need_v610_image_copy || need_v611_buffer_write || need_v612_clear_color || need_v613_clear_depth_stencil || need_v614_resolve_image || need_v615_blit_image || need_v616_clear_attachments || need_v617_query || need_v618_copy_query) {
        APPEND_GRAPHICS_TABLE(resolve_attachments, resolve_attachment_count,
                              sizeof(resolve_attachments[0]),
                              frame_header_v64->v64.resolve_attachment_table_offset,
                              frame_header_v64->v64.resolve_attachment_table_size);
    }
    if (need_v65_static_pipeline_state || need_v66_color_blend_state || need_v67_viewport_scissor_state || need_v68_indirect_draw || need_v69_buffer_copy || need_v610_image_copy || need_v611_buffer_write || need_v612_clear_color || need_v613_clear_depth_stencil || need_v614_resolve_image || need_v615_blit_image || need_v616_clear_attachments || need_v617_query || need_v618_copy_query) {
        APPEND_GRAPHICS_TABLE(static_pipeline_states, static_pipeline_state_count,
                              sizeof(static_pipeline_states[0]),
                              frame_header_v65->v65.static_pipeline_state_table_offset,
                              frame_header_v65->v65.static_pipeline_state_table_size);
    }
    if (need_v66_color_blend_state || need_v67_viewport_scissor_state || need_v68_indirect_draw || need_v69_buffer_copy || need_v610_image_copy || need_v611_buffer_write || need_v612_clear_color || need_v613_clear_depth_stencil || need_v614_resolve_image || need_v615_blit_image || need_v616_clear_attachments || need_v617_query || need_v618_copy_query) {
        APPEND_GRAPHICS_TABLE(color_blend_states, color_blend_state_count,
                              sizeof(color_blend_states[0]),
                              frame_header_v66->v66.color_blend_state_table_offset,
                              frame_header_v66->v66.color_blend_state_table_size);
        APPEND_GRAPHICS_TABLE(color_blend_attachments, color_blend_attachment_count,
                              sizeof(color_blend_attachments[0]),
                              frame_header_v66->v66.color_blend_attachment_table_offset,
                              frame_header_v66->v66.color_blend_attachment_table_size);
    }
    if (need_v67_viewport_scissor_state || need_v68_indirect_draw || need_v69_buffer_copy || need_v610_image_copy || need_v611_buffer_write || need_v612_clear_color || need_v613_clear_depth_stencil || need_v614_resolve_image || need_v615_blit_image || need_v616_clear_attachments || need_v617_query || need_v618_copy_query) {
        APPEND_GRAPHICS_TABLE(viewport_scissor_states, viewport_scissor_state_count,
                              sizeof(viewport_scissor_states[0]),
                              frame_header_v67->v67.viewport_scissor_state_table_offset,
                              frame_header_v67->v67.viewport_scissor_state_table_size);
        APPEND_GRAPHICS_TABLE(viewport_entries, viewport_entry_count,
                              sizeof(viewport_entries[0]),
                              frame_header_v67->v67.viewport_table_offset,
                              frame_header_v67->v67.viewport_table_size);
        APPEND_GRAPHICS_TABLE(scissor_entries, scissor_entry_count,
                              sizeof(scissor_entries[0]),
                              frame_header_v67->v67.scissor_table_offset,
                              frame_header_v67->v67.scissor_table_size);
    }
    if (need_v68_indirect_draw || need_v69_buffer_copy || need_v610_image_copy || need_v611_buffer_write || need_v612_clear_color || need_v613_clear_depth_stencil || need_v614_resolve_image || need_v615_blit_image || need_v616_clear_attachments || need_v617_query || need_v618_copy_query) {
        APPEND_GRAPHICS_TABLE(indirect_draws, indirect_draw_count,
                              sizeof(indirect_draws[0]),
                              frame_header_v68->v68.indirect_draw_table_offset,
                              frame_header_v68->v68.indirect_draw_table_size);
    }
    if (need_v69_buffer_copy || need_v610_image_copy || need_v611_buffer_write || need_v612_clear_color || need_v613_clear_depth_stencil || need_v614_resolve_image || need_v615_blit_image || need_v616_clear_attachments || need_v617_query || need_v618_copy_query) {
        APPEND_GRAPHICS_TABLE(buffer_copies, buffer_copy_count,
                              sizeof(buffer_copies[0]),
                              frame_header_v69->v69.buffer_copy_table_offset,
                              frame_header_v69->v69.buffer_copy_table_size);
    }
    if (need_v610_image_copy || need_v611_buffer_write || need_v612_clear_color || need_v613_clear_depth_stencil || need_v614_resolve_image || need_v615_blit_image || need_v616_clear_attachments || need_v617_query || need_v618_copy_query) {
        APPEND_GRAPHICS_TABLE(buffer_image_copies, buffer_image_copy_count,
                              sizeof(buffer_image_copies[0]),
                              frame_header_v610->v610.buffer_image_copy_table_offset,
                              frame_header_v610->v610.buffer_image_copy_table_size);
        APPEND_GRAPHICS_TABLE(image_copies, image_copy_count,
                              sizeof(image_copies[0]),
                              frame_header_v610->v610.image_copy_table_offset,
                              frame_header_v610->v610.image_copy_table_size);
    }
    if (need_v611_buffer_write || need_v612_clear_color || need_v613_clear_depth_stencil || need_v614_resolve_image || need_v615_blit_image || need_v616_clear_attachments || need_v617_query || need_v618_copy_query) {
        APPEND_GRAPHICS_TABLE(fill_buffers, fill_buffer_count,
                              sizeof(fill_buffers[0]),
                              frame_header_v611->v611.fill_buffer_table_offset,
                              frame_header_v611->v611.fill_buffer_table_size);
        APPEND_GRAPHICS_TABLE(update_buffers, update_buffer_count,
                              sizeof(update_buffers[0]),
                              frame_header_v611->v611.update_buffer_table_offset,
                              frame_header_v611->v611.update_buffer_table_size);
    }
    if (need_v612_clear_color || need_v613_clear_depth_stencil || need_v614_resolve_image || need_v615_blit_image || need_v616_clear_attachments || need_v617_query || need_v618_copy_query) {
        APPEND_GRAPHICS_TABLE(clear_color_images, clear_color_image_count,
                              sizeof(clear_color_images[0]),
                              frame_header_v612->v612.clear_color_image_table_offset,
                              frame_header_v612->v612.clear_color_image_table_size);
    }
    if (need_v613_clear_depth_stencil || need_v614_resolve_image || need_v615_blit_image || need_v616_clear_attachments || need_v617_query || need_v618_copy_query) {
        APPEND_GRAPHICS_TABLE(clear_depth_stencil_images, clear_depth_stencil_image_count,
                              sizeof(clear_depth_stencil_images[0]),
                              frame_header_v613->v613.clear_depth_stencil_image_table_offset,
                              frame_header_v613->v613.clear_depth_stencil_image_table_size);
    }
    if (need_v614_resolve_image || need_v615_blit_image || need_v616_clear_attachments || need_v617_query || need_v618_copy_query) {
        APPEND_GRAPHICS_TABLE(resolve_images, resolve_image_count,
                              sizeof(resolve_images[0]),
                              frame_header_v614->v614.resolve_image_table_offset,
                              frame_header_v614->v614.resolve_image_table_size);
    }
    if (need_v615_blit_image || need_v616_clear_attachments || need_v617_query || need_v618_copy_query) {
        APPEND_GRAPHICS_TABLE(blit_images, blit_image_count,
                              sizeof(blit_images[0]),
                              frame_header_v615->v615.blit_image_table_offset,
                              frame_header_v615->v615.blit_image_table_size);
    }
    if (need_v616_clear_attachments || need_v617_query || need_v618_copy_query) {
        APPEND_GRAPHICS_TABLE(clear_attachments_commands, clear_attachments_command_count,
                              sizeof(clear_attachments_commands[0]),
                              frame_header_v616->v616.clear_attachments_command_table_offset,
                              frame_header_v616->v616.clear_attachments_command_table_size);
        APPEND_GRAPHICS_TABLE(clear_attachments, clear_attachment_count,
                              sizeof(clear_attachments[0]),
                              frame_header_v616->v616.clear_attachment_table_offset,
                              frame_header_v616->v616.clear_attachment_table_size);
        APPEND_GRAPHICS_TABLE(clear_rects, clear_rect_count,
                              sizeof(clear_rects[0]),
                              frame_header_v616->v616.clear_rect_table_offset,
                              frame_header_v616->v616.clear_rect_table_size);
    }
    if (need_v617_query) {
        APPEND_GRAPHICS_TABLE(query_commands, query_command_count,
                              sizeof(query_commands[0]),
                              frame_header_v617->v617.query_command_table_offset,
                              frame_header_v617->v617.query_command_table_size);
    }
    if (need_v618_copy_query) {
        APPEND_GRAPHICS_TABLE(copy_query_results, copy_query_result_count,
                              sizeof(copy_query_results[0]),
                              frame_header_v618->v618.copy_query_result_table_offset,
                              frame_header_v618->v618.copy_query_result_table_size);
    }
#undef APPEND_GRAPHICS_TABLE
    frame_header->v61.extension_hash = 1469598103934665603ull;
    frame_header->v61.extension_hash = fnv1a64_update_bytes(
        frame_header->v61.extension_hash, dynamic_offsets,
        sizeof(dynamic_offsets[0]) * dynamic_offset_count);
    frame_header->v61.extension_hash = fnv1a64_update_bytes(
        frame_header->v61.extension_hash, push_metadata,
        sizeof(push_metadata[0]) * push_metadata_count);
    frame_header->v61.extension_hash = fnv1a64_update_bytes(
        frame_header->v61.extension_hash, image_barriers,
        sizeof(image_barriers[0]) * image_barrier_count);
    frame_header->v61.extension_hash = fnv1a64_update_bytes(
        frame_header->v61.extension_hash, memory_barriers,
        sizeof(memory_barriers[0]) * memory_barrier_count);
    frame_header->v61.extension_hash = fnv1a64_update_bytes(
        frame_header->v61.extension_hash, buffer_barriers,
        sizeof(buffer_barriers[0]) * buffer_barrier_count);
    if (need_v62_specialization || need_v63_depth_stencil || need_v64_resolve_attachment || need_v65_static_pipeline_state || need_v66_color_blend_state || need_v67_viewport_scissor_state || need_v68_indirect_draw || need_v69_buffer_copy || need_v610_image_copy || need_v611_buffer_write || need_v612_clear_color || need_v613_clear_depth_stencil || need_v614_resolve_image || need_v615_blit_image || need_v616_clear_attachments || need_v617_query || need_v618_copy_query) {
        frame_header_v62->v62.specialization_entry_table_hash = fnv1a64_bytes(
            specialization_entries, sizeof(specialization_entries[0]) * specialization_entry_count);
        frame_header_v62->v62.extension_hash = frame_header_v62->v62.specialization_entry_table_hash;
    }
    if (need_v63_depth_stencil || need_v64_resolve_attachment || need_v65_static_pipeline_state || need_v66_color_blend_state || need_v67_viewport_scissor_state || need_v68_indirect_draw || need_v69_buffer_copy || need_v610_image_copy || need_v611_buffer_write || need_v612_clear_color || need_v613_clear_depth_stencil || need_v614_resolve_image || need_v615_blit_image || need_v616_clear_attachments || need_v617_query || need_v618_copy_query) {
        frame_header_v63->v63.depth_stencil_state_table_hash = fnv1a64_bytes(
            depth_stencil_states, sizeof(depth_stencil_states[0]) * depth_stencil_state_count);
        frame_header_v63->v63.extension_hash = frame_header_v63->v63.depth_stencil_state_table_hash;
    }
    if (need_v64_resolve_attachment || need_v65_static_pipeline_state || need_v66_color_blend_state || need_v67_viewport_scissor_state || need_v68_indirect_draw || need_v69_buffer_copy || need_v610_image_copy || need_v611_buffer_write || need_v612_clear_color || need_v613_clear_depth_stencil || need_v614_resolve_image || need_v615_blit_image || need_v616_clear_attachments || need_v617_query || need_v618_copy_query) {
        frame_header_v64->v64.resolve_attachment_table_hash = fnv1a64_bytes(
            resolve_attachments, sizeof(resolve_attachments[0]) * resolve_attachment_count);
        frame_header_v64->v64.extension_hash = frame_header_v64->v64.resolve_attachment_table_hash;
    }
    if (need_v65_static_pipeline_state || need_v66_color_blend_state || need_v67_viewport_scissor_state || need_v68_indirect_draw || need_v69_buffer_copy || need_v610_image_copy || need_v611_buffer_write || need_v612_clear_color || need_v613_clear_depth_stencil || need_v614_resolve_image || need_v615_blit_image || need_v616_clear_attachments || need_v617_query || need_v618_copy_query) {
        frame_header_v65->v65.static_pipeline_state_table_hash = fnv1a64_bytes(
            static_pipeline_states, sizeof(static_pipeline_states[0]) * static_pipeline_state_count);
        frame_header_v65->v65.extension_hash = frame_header_v65->v65.static_pipeline_state_table_hash;
    }
    if (need_v66_color_blend_state || need_v67_viewport_scissor_state || need_v68_indirect_draw || need_v69_buffer_copy || need_v610_image_copy || need_v611_buffer_write || need_v612_clear_color || need_v613_clear_depth_stencil || need_v614_resolve_image || need_v615_blit_image || need_v616_clear_attachments || need_v617_query || need_v618_copy_query) {
        frame_header_v66->v66.color_blend_state_table_hash = fnv1a64_bytes(
            color_blend_states, sizeof(color_blend_states[0]) * color_blend_state_count);
        frame_header_v66->v66.color_blend_attachment_table_hash = fnv1a64_bytes(
            color_blend_attachments, sizeof(color_blend_attachments[0]) * color_blend_attachment_count);
        frame_header_v66->v66.extension_hash = 1469598103934665603ull;
        frame_header_v66->v66.extension_hash = fnv1a64_update_bytes(
            frame_header_v66->v66.extension_hash, color_blend_states,
            sizeof(color_blend_states[0]) * color_blend_state_count);
        frame_header_v66->v66.extension_hash = fnv1a64_update_bytes(
            frame_header_v66->v66.extension_hash, color_blend_attachments,
            sizeof(color_blend_attachments[0]) * color_blend_attachment_count);
    }
    if (need_v67_viewport_scissor_state || need_v68_indirect_draw || need_v69_buffer_copy || need_v610_image_copy || need_v611_buffer_write || need_v612_clear_color || need_v613_clear_depth_stencil || need_v614_resolve_image || need_v615_blit_image || need_v616_clear_attachments || need_v617_query || need_v618_copy_query) {
        frame_header_v67->v67.viewport_scissor_state_table_hash = fnv1a64_bytes(
            viewport_scissor_states, sizeof(viewport_scissor_states[0]) * viewport_scissor_state_count);
        frame_header_v67->v67.viewport_table_hash = fnv1a64_bytes(
            viewport_entries, sizeof(viewport_entries[0]) * viewport_entry_count);
        frame_header_v67->v67.scissor_table_hash = fnv1a64_bytes(
            scissor_entries, sizeof(scissor_entries[0]) * scissor_entry_count);
        frame_header_v67->v67.extension_hash = 1469598103934665603ull;
        frame_header_v67->v67.extension_hash = fnv1a64_update_bytes(
            frame_header_v67->v67.extension_hash, viewport_scissor_states,
            sizeof(viewport_scissor_states[0]) * viewport_scissor_state_count);
        frame_header_v67->v67.extension_hash = fnv1a64_update_bytes(
            frame_header_v67->v67.extension_hash, viewport_entries,
            sizeof(viewport_entries[0]) * viewport_entry_count);
        frame_header_v67->v67.extension_hash = fnv1a64_update_bytes(
            frame_header_v67->v67.extension_hash, scissor_entries,
            sizeof(scissor_entries[0]) * scissor_entry_count);
    }
    if (need_v68_indirect_draw || need_v69_buffer_copy || need_v610_image_copy || need_v611_buffer_write || need_v612_clear_color || need_v613_clear_depth_stencil || need_v614_resolve_image || need_v615_blit_image || need_v616_clear_attachments || need_v617_query || need_v618_copy_query) {
        frame_header_v68->v68.indirect_draw_table_hash = fnv1a64_bytes(
            indirect_draws, sizeof(indirect_draws[0]) * indirect_draw_count);
        frame_header_v68->v68.extension_hash = frame_header_v68->v68.indirect_draw_table_hash;
    }
    if (need_v69_buffer_copy || need_v610_image_copy || need_v611_buffer_write || need_v612_clear_color || need_v613_clear_depth_stencil || need_v614_resolve_image || need_v615_blit_image || need_v616_clear_attachments || need_v617_query || need_v618_copy_query) {
        frame_header_v69->v69.buffer_copy_table_hash = fnv1a64_bytes(
            buffer_copies, sizeof(buffer_copies[0]) * buffer_copy_count);
        frame_header_v69->v69.extension_hash = frame_header_v69->v69.buffer_copy_table_hash;
    }
    if (need_v610_image_copy || need_v611_buffer_write || need_v612_clear_color || need_v613_clear_depth_stencil || need_v614_resolve_image || need_v615_blit_image || need_v616_clear_attachments || need_v617_query || need_v618_copy_query) {
        frame_header_v610->v610.buffer_image_copy_table_hash = fnv1a64_bytes(
            buffer_image_copies, sizeof(buffer_image_copies[0]) * buffer_image_copy_count);
        frame_header_v610->v610.image_copy_table_hash = fnv1a64_bytes(
            image_copies, sizeof(image_copies[0]) * image_copy_count);
        frame_header_v610->v610.extension_hash = 1469598103934665603ull;
        frame_header_v610->v610.extension_hash = fnv1a64_update_bytes(
            frame_header_v610->v610.extension_hash, buffer_image_copies,
            sizeof(buffer_image_copies[0]) * buffer_image_copy_count);
        frame_header_v610->v610.extension_hash = fnv1a64_update_bytes(
            frame_header_v610->v610.extension_hash, image_copies,
            sizeof(image_copies[0]) * image_copy_count);
    }
    if (need_v611_buffer_write || need_v612_clear_color || need_v613_clear_depth_stencil || need_v614_resolve_image || need_v615_blit_image || need_v616_clear_attachments || need_v617_query || need_v618_copy_query) {
        frame_header_v611->v611.fill_buffer_table_hash = fnv1a64_bytes(
            fill_buffers, sizeof(fill_buffers[0]) * fill_buffer_count);
        frame_header_v611->v611.update_buffer_table_hash = fnv1a64_bytes(
            update_buffers, sizeof(update_buffers[0]) * update_buffer_count);
        frame_header_v611->v611.update_payload_data_hash = update_payload_data_size
            ? fnv1a64_bytes(frame + update_payload_data_offset, (size_t)update_payload_data_size)
            : 1469598103934665603ull;
        frame_header_v611->v611.extension_hash = 1469598103934665603ull;
        frame_header_v611->v611.extension_hash = fnv1a64_update_bytes(
            frame_header_v611->v611.extension_hash, fill_buffers,
            sizeof(fill_buffers[0]) * fill_buffer_count);
        frame_header_v611->v611.extension_hash = fnv1a64_update_bytes(
            frame_header_v611->v611.extension_hash, update_buffers,
            sizeof(update_buffers[0]) * update_buffer_count);
        if (update_payload_data_size) {
            frame_header_v611->v611.extension_hash = fnv1a64_update_bytes(
                frame_header_v611->v611.extension_hash, frame + update_payload_data_offset,
                (size_t)update_payload_data_size);
        }
    }
    if (need_v612_clear_color || need_v613_clear_depth_stencil || need_v614_resolve_image || need_v615_blit_image || need_v616_clear_attachments || need_v617_query || need_v618_copy_query) {
        frame_header_v612->v612.clear_color_image_table_hash = fnv1a64_bytes(
            clear_color_images, sizeof(clear_color_images[0]) * clear_color_image_count);
        frame_header_v612->v612.extension_hash = frame_header_v612->v612.clear_color_image_table_hash;
    }
    if (need_v613_clear_depth_stencil || need_v614_resolve_image || need_v615_blit_image || need_v616_clear_attachments || need_v617_query || need_v618_copy_query) {
        frame_header_v613->v613.clear_depth_stencil_image_table_hash = fnv1a64_bytes(
            clear_depth_stencil_images, sizeof(clear_depth_stencil_images[0]) * clear_depth_stencil_image_count);
        frame_header_v613->v613.extension_hash = frame_header_v613->v613.clear_depth_stencil_image_table_hash;
    }
    if (need_v614_resolve_image || need_v615_blit_image || need_v616_clear_attachments || need_v617_query || need_v618_copy_query) {
        frame_header_v614->v614.resolve_image_table_hash = fnv1a64_bytes(
            resolve_images, sizeof(resolve_images[0]) * resolve_image_count);
        frame_header_v614->v614.extension_hash = frame_header_v614->v614.resolve_image_table_hash;
    }
    if (need_v615_blit_image || need_v616_clear_attachments || need_v617_query || need_v618_copy_query) {
        frame_header_v615->v615.blit_image_table_hash = fnv1a64_bytes(
            blit_images, sizeof(blit_images[0]) * blit_image_count);
        frame_header_v615->v615.extension_hash = frame_header_v615->v615.blit_image_table_hash;
    }
    if (need_v616_clear_attachments || need_v617_query || need_v618_copy_query) {
        frame_header_v616->v616.clear_attachments_command_table_hash = fnv1a64_bytes(
            clear_attachments_commands, sizeof(clear_attachments_commands[0]) * clear_attachments_command_count);
        frame_header_v616->v616.clear_attachment_table_hash = fnv1a64_bytes(
            clear_attachments, sizeof(clear_attachments[0]) * clear_attachment_count);
        frame_header_v616->v616.clear_rect_table_hash = fnv1a64_bytes(
            clear_rects, sizeof(clear_rects[0]) * clear_rect_count);
        frame_header_v616->v616.extension_hash = 1469598103934665603ull;
        frame_header_v616->v616.extension_hash = fnv1a64_update_bytes(
            frame_header_v616->v616.extension_hash, clear_attachments_commands,
            sizeof(clear_attachments_commands[0]) * clear_attachments_command_count);
        frame_header_v616->v616.extension_hash = fnv1a64_update_bytes(
            frame_header_v616->v616.extension_hash, clear_attachments,
            sizeof(clear_attachments[0]) * clear_attachment_count);
        frame_header_v616->v616.extension_hash = fnv1a64_update_bytes(
            frame_header_v616->v616.extension_hash, clear_rects,
            sizeof(clear_rects[0]) * clear_rect_count);
    }
    if (need_v617_query) {
        frame_header_v617->v617.query_command_table_hash = fnv1a64_bytes(
            query_commands, sizeof(query_commands[0]) * query_command_count);
        frame_header_v617->v617.extension_hash = frame_header_v617->v617.query_command_table_hash;
    }
    if (need_v618_copy_query) {
        frame_header_v618->v618.copy_query_result_table_hash = fnv1a64_bytes(
            copy_query_results, sizeof(copy_query_results[0]) * copy_query_result_count);
        frame_header_v618->v618.extension_hash = frame_header_v618->v618.copy_query_result_table_hash;
    }
    header->frame_size = cursor;
    header->payload_hash = fnv1a64_bytes(frame + header->header_size,
                                         cursor - header->header_size);
    header->frame_hash = fnv1a64_bytes(frame, cursor);
    rc = send_vulkan_graphics_v6_frame_with_fds(socket_fd, frame, cursor, fds, fd_count);
    const char *graphics_label =
        need_v618_copy_query ? "VULKAN_GRAPHICS_V6.18" :
        need_v617_query ? "VULKAN_GRAPHICS_V6.17" :
        need_v616_clear_attachments ? "VULKAN_GRAPHICS_V6.16" :
        need_v615_blit_image ? "VULKAN_GRAPHICS_V6.15" :
        need_v614_resolve_image ? "VULKAN_GRAPHICS_V6.14" :
        need_v613_clear_depth_stencil ? "VULKAN_GRAPHICS_V6.13" :
        need_v612_clear_color ? "VULKAN_GRAPHICS_V6.12" :
        need_v611_buffer_write ? "VULKAN_GRAPHICS_V6.11" :
        need_v610_image_copy ? "VULKAN_GRAPHICS_V6.10" :
        need_v69_buffer_copy ? "VULKAN_GRAPHICS_V6.9" :
        need_v68_indirect_draw ? "VULKAN_GRAPHICS_V6.8" :
        need_v67_viewport_scissor_state ? "VULKAN_GRAPHICS_V6.7" :
        need_v66_color_blend_state ? "VULKAN_GRAPHICS_V6.6" :
        need_v65_static_pipeline_state ? "VULKAN_GRAPHICS_V6.5" :
        need_v64_resolve_attachment ? "VULKAN_GRAPHICS_V6.4" :
        need_v63_depth_stencil ? "VULKAN_GRAPHICS_V6.3" :
        need_v62_specialization ? "VULKAN_GRAPHICS_V6.2" : "VULKAN_GRAPHICS_V6.1";
    if (rc == 0) rc = read_dispatch_response_status(socket_fd, graphics_label);

cleanup:
    free(frame);
    close(socket_fd);
    return rc;
}

static int find_image_table_index(PdockerVkImage *const *images,
                                  size_t count,
                                  const PdockerVkImage *image) {
    for (size_t i = 0; i < count; ++i) {
        if (images[i] == image) return (int)i;
    }
    return -1;
}

static int find_image_view_table_index(PdockerVkImageView *const *views,
                                       size_t count,
                                       const PdockerVkImageView *view) {
    for (size_t i = 0; i < count; ++i) {
        if (views[i] == view) return (int)i;
    }
    return -1;
}

static int find_sampler_table_index(PdockerVkSampler *const *samplers,
                                    size_t count,
                                    const PdockerVkSampler *sampler) {
    for (size_t i = 0; i < count; ++i) {
        if (samplers[i] == sampler) return (int)i;
    }
    return -1;
}

static bool descriptor_type_requires_image_view(VkDescriptorType type) {
    return type == VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER ||
           type == VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE ||
           type == VK_DESCRIPTOR_TYPE_STORAGE_IMAGE ||
           type == VK_DESCRIPTOR_TYPE_INPUT_ATTACHMENT;
}

static bool descriptor_type_requires_sampler(VkDescriptorType type) {
    return type == VK_DESCRIPTOR_TYPE_SAMPLER ||
           type == VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER;
}

static bool descriptor_set_has_image_descriptor(const PdockerVkDescriptorSet *set) {
    if (!set) return false;
    for (uint32_t i = 0; i < PDOCKER_VK_MAX_STORAGE_BUFFERS; ++i) {
        for (uint32_t j = 0; j < PDOCKER_VK_MAX_DESCRIPTOR_ARRAY_ELEMENTS; ++j) {
            const PdockerVkDescriptorBinding *binding = &set->storage_buffers[i][j];
            if (binding->image_view || binding->sampler) return true;
            if (!binding->buffer && !binding->image_view && !binding->sampler) continue;
            VkDescriptorType descriptor_type = binding->descriptor_type;
            if (descriptor_type == VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER ||
                descriptor_type == VK_DESCRIPTOR_TYPE_SAMPLER ||
                descriptor_type == VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE ||
                descriptor_type == VK_DESCRIPTOR_TYPE_STORAGE_IMAGE ||
                descriptor_type == VK_DESCRIPTOR_TYPE_INPUT_ATTACHMENT) {
                return true;
            }
        }
    }
    return false;
}

static bool descriptor_advance_to_valid_slot(
        const PdockerVkDescriptorSetLayout *layout,
        uint32_t *binding,
        uint32_t *array_element) {
    if (!binding || !array_element) return false;
    while (*binding < PDOCKER_VK_MAX_STORAGE_BUFFERS) {
        uint32_t count = layout
            ? layout->storage_binding_counts[*binding]
            : PDOCKER_VK_MAX_DESCRIPTOR_ARRAY_ELEMENTS;
        if (count > PDOCKER_VK_MAX_DESCRIPTOR_ARRAY_ELEMENTS) return false;
        if (count == 0) {
            (*binding)++;
            *array_element = 0;
            continue;
        }
        if (*array_element < count) return true;
        (*binding)++;
        *array_element = 0;
    }
    return false;
}

static bool descriptor_linear_slot(
        const PdockerVkDescriptorSetLayout *layout,
        uint32_t start_binding,
        uint32_t start_array_element,
        uint32_t linear_index,
        uint32_t *binding_out,
        uint32_t *array_element_out) {
    uint32_t binding = start_binding;
    uint32_t array_element = start_array_element;
    if (!descriptor_advance_to_valid_slot(layout, &binding, &array_element)) return false;
    for (uint32_t i = 0; i < linear_index; ++i) {
        array_element++;
        if (!descriptor_advance_to_valid_slot(layout, &binding, &array_element)) return false;
    }
    if (binding_out) *binding_out = binding;
    if (array_element_out) *array_element_out = array_element;
    return true;
}

static int send_generic_vulkan_dispatch_v5_1_op(
        int socket_fd,
        uint64_t dispatch_id,
        int *fds,
        size_t binding_count,
        size_t image_descriptor_count,
        size_t shader_size,
        uint64_t shader_hash,
        uint32_t gx,
        uint32_t gy,
        uint32_t gz,
        const uint8_t *push,
        size_t push_size,
        uint64_t push_hash,
        const char *entry_name,
        const VkSpecializationMapEntry *specialization_entries,
        uint32_t specialization_entry_count,
        const uint8_t *specialization_data,
        size_t specialization_data_size,
        uint64_t specialization_hash,
        const char *option_text,
        size_t option_text_size,
        const uint32_t *api_descriptor_sets,
        const uint32_t *api_descriptor_array_elements,
        const uint32_t *bindings,
        const VkDeviceSize *offsets,
        const size_t *sizes,
        const VkDeviceSize *api_offsets,
        const VkDeviceSize *api_ranges,
        const size_t *api_buffer_sizes,
        const uint32_t *api_descriptor_types,
        const uint32_t *api_dynamic_flags,
        const VkDeviceSize *api_dynamic_offsets,
        const VkDeviceSize *api_memory_offsets,
        const size_t *api_memory_sizes,
        const uintptr_t *api_memory_ids,
        const uintptr_t *api_buffer_ids,
        const uint32_t *image_descriptor_sets,
        const uint32_t *image_descriptor_bindings,
        const uint32_t *image_descriptor_array_elements,
        const uint32_t *image_descriptor_types,
        const uint32_t *image_descriptor_view_indices,
        const uint32_t *image_descriptor_sampler_indices,
        const VkImageLayout *image_descriptor_layouts,
        PdockerVkImage *const *image_objects,
        size_t image_count,
        PdockerVkImageView *const *image_view_objects,
        size_t image_view_count,
        PdockerVkSampler *const *sampler_objects,
        size_t sampler_count,
        uint64_t descriptor_hash,
        uint64_t dispatch_hash) {
    (void)descriptor_hash;
    if (socket_fd < 0 || !fds || !entry_name ||
        (binding_count > 0 &&
         (!api_descriptor_sets || !api_descriptor_array_elements || !bindings || !offsets || !sizes || !api_offsets ||
          !api_ranges || !api_buffer_sizes || !api_descriptor_types ||
          !api_dynamic_flags || !api_dynamic_offsets || !api_memory_offsets || !api_memory_sizes ||
          !api_memory_ids || !api_buffer_ids)) ||
        (image_descriptor_count > 0 &&
         (!image_descriptor_sets || !image_descriptor_bindings ||
          !image_descriptor_array_elements || !image_descriptor_types || !image_descriptor_view_indices ||
          !image_descriptor_sampler_indices || !image_descriptor_layouts))) {
        fprintf(stderr,
                "pdocker-vulkan-icd: V5.1 frame rejected: invalid arguments dispatch_id=%llu socket_fd=%d bindings=%zu image_descriptors=%zu entry=%p\n",
                (unsigned long long)dispatch_id,
                socket_fd,
                binding_count,
                image_descriptor_count,
                (const void *)entry_name);
        return -EINVAL;
    }
    if (binding_count == 0 && image_descriptor_count == 0) {
        fprintf(stderr,
                "pdocker-vulkan-icd: V5.1 frame rejected: empty descriptor table dispatch_id=%llu\n",
                (unsigned long long)dispatch_id);
        return -EINVAL;
    }
    if (binding_count > PDOCKER_VK_MAX_STORAGE_BUFFERS ||
        image_descriptor_count > PDOCKER_VK_MAX_STORAGE_BUFFERS ||
        image_count > PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_IMAGES ||
        image_view_count > PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_IMAGE_VIEWS ||
        sampler_count > PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_SAMPLERS) {
        return -E2BIG;
    }
    const size_t resource_count = binding_count * 2u + image_count;
    const size_t descriptor_count = binding_count + image_descriptor_count;
    const size_t fd_count = 1u + binding_count + image_count;
    if (resource_count > PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_RESOURCES ||
        descriptor_count > PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_DESCRIPTORS ||
        fd_count > PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_FDS) {
        return -E2BIG;
    }
    if (specialization_entry_count > PDOCKER_VK_MAX_SPECIALIZATION_ENTRIES ||
        specialization_data_size > PDOCKER_VK_MAX_SPECIALIZATION_BYTES ||
        push_size > PDOCKER_VK_MAX_PUSH_BYTES) {
        fprintf(stderr,
                "pdocker-vulkan-icd: V5.1 frame rejected: metadata too large dispatch_id=%llu spec_entries=%u spec_bytes=%zu push_bytes=%zu\n",
                (unsigned long long)dispatch_id,
                specialization_entry_count,
                specialization_data_size,
                push_size);
        return -E2BIG;
    }
    size_t entry_name_size = strlen(entry_name);
    if (entry_name_size == 0 || entry_name_size >= PDOCKER_VK_MAX_ENTRY_NAME) {
        fprintf(stderr,
                "pdocker-vulkan-icd: V5.1 frame rejected: invalid entry name dispatch_id=%llu entry_name_size=%zu\n",
                (unsigned long long)dispatch_id,
                entry_name_size);
        return -EINVAL;
    }

    PdockerGpuVulkanDispatchV5ResourceEntry resources[PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_RESOURCES];
    PdockerGpuVulkanDispatchV5DescriptorObjectEntry descriptors[PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_DESCRIPTORS];
    PdockerGpuVulkanDispatchV5ImageEntry image_entries[PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_IMAGES];
    PdockerGpuVulkanDispatchV5ImageViewEntry image_view_entries[PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_IMAGE_VIEWS];
    PdockerGpuVulkanDispatchV5SamplerEntry sampler_entries[PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_SAMPLERS];
    PdockerGpuVulkanDispatchV5SpecializationEntry specs[PDOCKER_VK_MAX_SPECIALIZATION_ENTRIES];
    memset(resources, 0, sizeof(resources));
    memset(descriptors, 0, sizeof(descriptors));
    memset(image_entries, 0, sizeof(image_entries));
    memset(image_view_entries, 0, sizeof(image_view_entries));
    memset(sampler_entries, 0, sizeof(sampler_entries));
    memset(specs, 0, sizeof(specs));
    size_t resource_index = 0;
    size_t fd_index = 1;
    for (size_t i = 0; i < binding_count; ++i) {
        if (api_memory_offsets[i] > offsets[i]) {
            fprintf(stderr,
                    "pdocker-vulkan-icd: V5.1 frame rejected: memory offset beyond transport offset dispatch_id=%llu descriptor=%zu memory_offset=%llu offset=%llu\n",
                    (unsigned long long)dispatch_id,
                    i,
                    (unsigned long long)api_memory_offsets[i],
                    (unsigned long long)offsets[i]);
            return -ERANGE;
        }
        uint64_t transfer_offset = (uint64_t)(offsets[i] - api_memory_offsets[i]);
        if (transfer_offset > api_buffer_sizes[i] ||
            sizes[i] > api_buffer_sizes[i] - transfer_offset) {
            fprintf(stderr,
                    "pdocker-vulkan-icd: V5.1 frame rejected: transfer outside buffer dispatch_id=%llu descriptor=%zu transfer_offset=%llu transfer_size=%zu buffer_size=%zu\n",
                    (unsigned long long)dispatch_id,
                    i,
                    (unsigned long long)transfer_offset,
                    sizes[i],
                    api_buffer_sizes[i]);
            return -ERANGE;
        }
        uint32_t memory_index = (uint32_t)resource_index++;
        uint32_t buffer_index = memory_index + 1u;
        resource_index++;
        resources[memory_index].resource_type = PDOCKER_GPU_V5_RESOURCE_TYPE_MEMORY;
        resources[memory_index].resource_flags =
            PDOCKER_GPU_V5_RESOURCE_FLAG_HOST_FD_BACKED |
            PDOCKER_GPU_V5_RESOURCE_FLAG_MUTABLE;
        resources[memory_index].resource_id = (uint64_t)api_memory_ids[i];
        resources[memory_index].parent_resource_index = PDOCKER_GPU_V5_RESOURCE_PARENT_NONE;
        resources[memory_index].fd_index = (uint32_t)fd_index;
        resources[memory_index].memory_offset = 0;
        resources[memory_index].size = (uint64_t)api_memory_sizes[i];
        resources[memory_index].memory_property_flags =
            VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT;
        resources[memory_index].external_offset = 0;
        resources[memory_index].generation = dispatch_id;

        resources[buffer_index].resource_type = PDOCKER_GPU_V5_RESOURCE_TYPE_BUFFER;
        resources[buffer_index].resource_flags = PDOCKER_GPU_V5_RESOURCE_FLAG_MUTABLE;
        resources[buffer_index].resource_id = (uint64_t)api_buffer_ids[i];
        resources[buffer_index].parent_resource_index = memory_index;
        resources[buffer_index].fd_index = PDOCKER_GPU_V5_RESOURCE_FD_NONE;
        resources[buffer_index].memory_offset = (uint64_t)api_memory_offsets[i];
        resources[buffer_index].size = (uint64_t)api_buffer_sizes[i];
        resources[buffer_index].generation = dispatch_id;
        fds[fd_index++] = fds[1u + i];

        descriptors[i].descriptor_set = api_descriptor_sets[i];
        descriptors[i].binding = bindings[i];
        descriptors[i].array_element = api_descriptor_array_elements[i];
        descriptors[i].descriptor_type = api_descriptor_types[i];
        descriptors[i].descriptor_flags =
            (api_dynamic_flags[i] ? PDOCKER_GPU_V5_DESCRIPTOR_FLAG_DYNAMIC : 0u) |
            (api_ranges[i] == VK_WHOLE_SIZE ? PDOCKER_GPU_V5_DESCRIPTOR_FLAG_WHOLE_SIZE : 0u) |
            (api_descriptor_array_elements[i] ? PDOCKER_GPU_V5_DESCRIPTOR_FLAG_ARRAY_ENTRY : 0u);
        descriptors[i].access_flags = PDOCKER_GPU_V5_ACCESS_READ | PDOCKER_GPU_V5_ACCESS_WRITE;
        descriptors[i].resource_index = buffer_index;
        descriptors[i].image_view_index = PDOCKER_GPU_V5_DESCRIPTOR_OBJECT_NONE;
        descriptors[i].sampler_index = PDOCKER_GPU_V5_DESCRIPTOR_OBJECT_NONE;
        descriptors[i].image_layout = 0;
        descriptors[i].resource_id = (uint64_t)api_buffer_ids[i];
        descriptors[i].buffer_offset = (uint64_t)api_offsets[i];
        descriptors[i].range = (uint64_t)api_ranges[i];
        descriptors[i].transfer_offset = transfer_offset;
        descriptors[i].transfer_size = (uint64_t)sizes[i];
        descriptors[i].dynamic_offset = (uint64_t)api_dynamic_offsets[i];
    }
    for (size_t i = 0; i < image_count; ++i) {
        PdockerVkImage *image = image_objects ? image_objects[i] : NULL;
        if (!image || !image->memory || image->memory->fd < 0) {
            fprintf(stderr,
                    "pdocker-vulkan-icd: V5.1 frame rejected: invalid image memory dispatch_id=%llu image=%zu image_ptr=%p memory_ptr=%p fd=%d\n",
                    (unsigned long long)dispatch_id,
                    i,
                    (void *)image,
                    image ? (void *)image->memory : NULL,
                    image && image->memory ? image->memory->fd : -1);
            return -EINVAL;
        }
        if (image->memory_offset > (VkDeviceSize)image->memory->size ||
            image->requirements_size > (VkDeviceSize)image->memory->size - image->memory_offset) {
            fprintf(stderr,
                    "pdocker-vulkan-icd: V5.1 frame rejected: image outside memory dispatch_id=%llu image=%zu memory_offset=%llu requirements=%llu memory_size=%zu\n",
                    (unsigned long long)dispatch_id,
                    i,
                    (unsigned long long)image->memory_offset,
                    (unsigned long long)image->requirements_size,
                    image->memory->size);
            return -ERANGE;
        }
        const uint32_t memory_index = (uint32_t)resource_index++;
        resources[memory_index].resource_type = PDOCKER_GPU_V5_RESOURCE_TYPE_MEMORY;
        resources[memory_index].resource_flags =
            PDOCKER_GPU_V5_RESOURCE_FLAG_HOST_FD_BACKED |
            PDOCKER_GPU_V5_RESOURCE_FLAG_MUTABLE;
        resources[memory_index].resource_id = (uint64_t)(uintptr_t)image->memory;
        resources[memory_index].parent_resource_index = PDOCKER_GPU_V5_RESOURCE_PARENT_NONE;
        resources[memory_index].fd_index = (uint32_t)fd_index;
        resources[memory_index].size = (uint64_t)image->memory->size;
        resources[memory_index].memory_property_flags =
            VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT;
        resources[memory_index].generation = dispatch_id;
        fds[fd_index++] = image->memory->fd;

        image_entries[i].flags =
            ((image->flags & VK_IMAGE_CREATE_MUTABLE_FORMAT_BIT)
                ? PDOCKER_GPU_V5_IMAGE_FLAG_MUTABLE_FORMAT : 0u) |
            ((image->flags & VK_IMAGE_CREATE_CUBE_COMPATIBLE_BIT)
                ? PDOCKER_GPU_V5_IMAGE_FLAG_CUBE_COMPATIBLE : 0u) |
            ((image->flags & VK_IMAGE_CREATE_ALIAS_BIT)
                ? PDOCKER_GPU_V5_IMAGE_FLAG_ALIAS : 0u);
        image_entries[i].image_type = image->image_type;
        image_entries[i].image_id = (uint64_t)(uintptr_t)image;
        image_entries[i].memory_resource_index = memory_index;
        image_entries[i].memory_offset = (uint64_t)image->memory_offset;
        image_entries[i].memory_size = (uint64_t)image->requirements_size;
        image_entries[i].format = image->format;
        image_entries[i].extent_width = image->extent.width;
        image_entries[i].extent_height = image->extent.height;
        image_entries[i].extent_depth = image->extent.depth;
        image_entries[i].mip_levels = image->mip_levels;
        image_entries[i].array_layers = image->array_layers;
        image_entries[i].samples = image->samples;
        image_entries[i].tiling = image->tiling;
        image_entries[i].usage = image->usage;
        image_entries[i].create_flags = image->flags;
        image_entries[i].sharing_mode = image->sharing_mode;
        image_entries[i].initial_layout = image->initial_layout;
        image_entries[i].generation = image->generation;
    }
    for (size_t i = 0; i < image_view_count; ++i) {
        PdockerVkImageView *view = image_view_objects ? image_view_objects[i] : NULL;
        if (!view || !view->image) {
            fprintf(stderr,
                    "pdocker-vulkan-icd: V5.1 frame rejected: invalid image view dispatch_id=%llu view=%zu view_ptr=%p image_ptr=%p\n",
                    (unsigned long long)dispatch_id,
                    i,
                    (void *)view,
                    view ? (void *)view->image : NULL);
            return -EINVAL;
        }
        int image_index = find_image_table_index(image_objects, image_count, view->image);
        if (image_index < 0) {
            fprintf(stderr,
                    "pdocker-vulkan-icd: V5.1 frame rejected: image view has no image table entry dispatch_id=%llu view=%zu image_ptr=%p image_count=%zu\n",
                    (unsigned long long)dispatch_id,
                    i,
                    (void *)view->image,
                    image_count);
            return -EINVAL;
        }
        image_view_entries[i].view_type = view->view_type;
        image_view_entries[i].view_id = (uint64_t)(uintptr_t)view;
        image_view_entries[i].image_index = (uint32_t)image_index;
        image_view_entries[i].format = view->format;
        image_view_entries[i].component_r = view->components.r;
        image_view_entries[i].component_g = view->components.g;
        image_view_entries[i].component_b = view->components.b;
        image_view_entries[i].component_a = view->components.a;
        image_view_entries[i].aspect_mask = view->subresource_range.aspectMask;
        image_view_entries[i].base_mip_level = view->subresource_range.baseMipLevel;
        image_view_entries[i].level_count = view->subresource_range.levelCount;
        image_view_entries[i].base_array_layer = view->subresource_range.baseArrayLayer;
        image_view_entries[i].layer_count = view->subresource_range.layerCount;
        image_view_entries[i].generation = view->generation;
    }
    for (size_t i = 0; i < sampler_count; ++i) {
        PdockerVkSampler *sampler = sampler_objects ? sampler_objects[i] : NULL;
        if (!sampler) {
            fprintf(stderr,
                    "pdocker-vulkan-icd: V5.1 frame rejected: invalid sampler dispatch_id=%llu sampler=%zu\n",
                    (unsigned long long)dispatch_id,
                    i);
            return -EINVAL;
        }
        sampler_entries[i].sampler_id = (uint64_t)(uintptr_t)sampler;
        sampler_entries[i].mag_filter = sampler->mag_filter;
        sampler_entries[i].min_filter = sampler->min_filter;
        sampler_entries[i].mipmap_mode = sampler->mipmap_mode;
        sampler_entries[i].address_mode_u = sampler->address_mode_u;
        sampler_entries[i].address_mode_v = sampler->address_mode_v;
        sampler_entries[i].address_mode_w = sampler->address_mode_w;
        sampler_entries[i].mip_lod_bias_bits = float_bits_u32(sampler->mip_lod_bias);
        sampler_entries[i].anisotropy_enable = sampler->anisotropy_enable;
        sampler_entries[i].max_anisotropy_bits = float_bits_u32(sampler->max_anisotropy);
        sampler_entries[i].compare_enable = sampler->compare_enable;
        sampler_entries[i].compare_op = sampler->compare_op;
        sampler_entries[i].min_lod_bits = float_bits_u32(sampler->min_lod);
        sampler_entries[i].max_lod_bits = float_bits_u32(sampler->max_lod);
        sampler_entries[i].border_color = sampler->border_color;
        sampler_entries[i].unnormalized_coordinates = sampler->unnormalized_coordinates;
        sampler_entries[i].generation = sampler->generation;
    }
    for (size_t i = 0; i < image_descriptor_count; ++i) {
        const size_t descriptor_index = binding_count + i;
        VkDescriptorType type = (VkDescriptorType)image_descriptor_types[i];
        if (descriptor_type_requires_image_view(type) &&
            image_descriptor_view_indices[i] == PDOCKER_GPU_V5_DESCRIPTOR_OBJECT_NONE) {
            fprintf(stderr,
                    "pdocker-vulkan-icd: V5.1 frame rejected: image descriptor missing view dispatch_id=%llu descriptor=%zu type=%u set=%u binding=%u array=%u\n",
                    (unsigned long long)dispatch_id,
                    i,
                    image_descriptor_types[i],
                    image_descriptor_sets[i],
                    image_descriptor_bindings[i],
                    image_descriptor_array_elements[i]);
            return -EINVAL;
        }
        if (descriptor_type_requires_sampler(type) &&
            image_descriptor_sampler_indices[i] == PDOCKER_GPU_V5_DESCRIPTOR_OBJECT_NONE) {
            fprintf(stderr,
                    "pdocker-vulkan-icd: V5.1 frame rejected: image descriptor missing sampler dispatch_id=%llu descriptor=%zu type=%u set=%u binding=%u array=%u\n",
                    (unsigned long long)dispatch_id,
                    i,
                    image_descriptor_types[i],
                    image_descriptor_sets[i],
                    image_descriptor_bindings[i],
                    image_descriptor_array_elements[i]);
            return -EINVAL;
        }
        descriptors[descriptor_index].descriptor_set = image_descriptor_sets[i];
        descriptors[descriptor_index].binding = image_descriptor_bindings[i];
        descriptors[descriptor_index].array_element = image_descriptor_array_elements[i];
        descriptors[descriptor_index].descriptor_type = image_descriptor_types[i];
        descriptors[descriptor_index].access_flags =
            type == VK_DESCRIPTOR_TYPE_STORAGE_IMAGE
                ? (PDOCKER_GPU_V5_ACCESS_READ | PDOCKER_GPU_V5_ACCESS_WRITE)
                : PDOCKER_GPU_V5_ACCESS_READ;
        descriptors[descriptor_index].resource_index = PDOCKER_GPU_V5_DESCRIPTOR_OBJECT_NONE;
        descriptors[descriptor_index].image_view_index = image_descriptor_view_indices[i];
        descriptors[descriptor_index].sampler_index = image_descriptor_sampler_indices[i];
        descriptors[descriptor_index].image_layout = image_descriptor_layouts[i];
        descriptors[descriptor_index].resource_id =
            image_descriptor_view_indices[i] != PDOCKER_GPU_V5_DESCRIPTOR_OBJECT_NONE
                ? (uint64_t)(uintptr_t)image_view_objects[image_descriptor_view_indices[i]]
                : (uint64_t)(uintptr_t)sampler_objects[image_descriptor_sampler_indices[i]];
    }
    for (uint32_t i = 0; i < specialization_entry_count; ++i) {
        specs[i].constant_id = specialization_entries[i].constantID;
        specs[i].offset = specialization_entries[i].offset;
        specs[i].size = specialization_entries[i].size;
    }

    size_t frame_capacity =
        sizeof(PdockerGpuVulkanDispatchV5ObjectFrameHeader) +
        align_size_8(sizeof(PdockerGpuVulkanDispatchV5ResourceEntry) * resource_count) +
        align_size_8(sizeof(PdockerGpuVulkanDispatchV5DescriptorObjectEntry) * descriptor_count) +
        align_size_8(sizeof(PdockerGpuVulkanDispatchV5ImageEntry) * image_count) +
        align_size_8(sizeof(PdockerGpuVulkanDispatchV5ImageViewEntry) * image_view_count) +
        align_size_8(sizeof(PdockerGpuVulkanDispatchV5SamplerEntry) * sampler_count) +
        align_size_8(sizeof(PdockerGpuVulkanDispatchV5SpecializationEntry) * specialization_entry_count) +
        align_size_8(specialization_data_size) +
        align_size_8(push_size) +
        align_size_8(entry_name_size) +
        align_size_8(option_text_size) +
        64u;
    if (frame_capacity > PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_FRAME_BYTES) return -EMSGSIZE;
    unsigned char *frame = (unsigned char *)calloc(1, frame_capacity);
    if (!frame) return -ENOMEM;
    PdockerGpuVulkanDispatchV5ObjectFrameHeader *object_header =
        (PdockerGpuVulkanDispatchV5ObjectFrameHeader *)frame;
    PdockerGpuVulkanDispatchV5FrameHeader *header = &object_header->base;
    memcpy(header->magic, PDOCKER_GPU_VULKAN_DISPATCH_V5_MAGIC, 8);
    header->header_size = sizeof(PdockerGpuVulkanDispatchV5ObjectFrameHeader);
    header->abi_major = PDOCKER_GPU_VULKAN_DISPATCH_V5_ABI_MAJOR;
    header->abi_minor = PDOCKER_GPU_VULKAN_DISPATCH_V5_ABI_MINOR_OBJECTS;
    header->command = PDOCKER_GPU_VULKAN_DISPATCH_V5_COMMAND_DISPATCH;
    header->dispatch_id = dispatch_id;
    header->fd_count = (uint32_t)fd_count;
    header->shader_fd_index = 0;
    header->shader_size = shader_size;
    header->shader_hash = shader_hash;
    header->gx = gx;
    header->gy = gy ? gy : 1;
    header->gz = gz ? gz : 1;
    header->resource_count = (uint32_t)resource_count;
    header->resource_entry_size = sizeof(PdockerGpuVulkanDispatchV5ResourceEntry);
    header->resource_schema_hash = PDOCKER_GPU_VULKAN_DISPATCH_V5_RESOURCE_SCHEMA_HASH;
    header->descriptor_count = (uint32_t)descriptor_count;
    header->descriptor_entry_size = sizeof(PdockerGpuVulkanDispatchV5DescriptorObjectEntry);
    header->descriptor_schema_hash = PDOCKER_GPU_VULKAN_DISPATCH_V5_DESCRIPTOR_OBJECT_SCHEMA_HASH;
    header->specialization_count = specialization_entry_count;
    header->specialization_entry_size = sizeof(PdockerGpuVulkanDispatchV5SpecializationEntry);
    header->specialization_hash = specialization_hash;
    header->push_size = push_size;
    header->push_hash = push_hash;
    header->entry_name_size = entry_name_size;
    header->option_text_size = option_text_size;
    header->option_hash = fnv1a64_bytes(option_text, option_text_size);
    header->resource_hash = fnv1a64_bytes(resources, sizeof(resources[0]) * resource_count);
    header->descriptor_hash = fnv1a64_bytes(descriptors, sizeof(descriptors[0]) * descriptor_count);
    header->dispatch_hash = dispatch_hash;

    object_header->objects.image_count = (uint32_t)image_count;
    object_header->objects.image_entry_size = sizeof(PdockerGpuVulkanDispatchV5ImageEntry);
    object_header->objects.image_schema_hash = PDOCKER_GPU_VULKAN_DISPATCH_V5_IMAGE_SCHEMA_HASH;
    object_header->objects.image_view_count = (uint32_t)image_view_count;
    object_header->objects.image_view_entry_size = sizeof(PdockerGpuVulkanDispatchV5ImageViewEntry);
    object_header->objects.image_view_schema_hash = PDOCKER_GPU_VULKAN_DISPATCH_V5_IMAGE_VIEW_SCHEMA_HASH;
    object_header->objects.sampler_count = (uint32_t)sampler_count;
    object_header->objects.sampler_entry_size = sizeof(PdockerGpuVulkanDispatchV5SamplerEntry);
    object_header->objects.sampler_schema_hash = PDOCKER_GPU_VULKAN_DISPATCH_V5_SAMPLER_SCHEMA_HASH;
    uint64_t object_hash = 1469598103934665603ull;
    object_hash = fnv1a64_update_u64(object_hash, image_count);
    object_hash = fnv1a64_update_bytes(object_hash, image_entries, sizeof(image_entries[0]) * image_count);
    object_hash = fnv1a64_update_u64(object_hash, image_view_count);
    object_hash = fnv1a64_update_bytes(object_hash, image_view_entries, sizeof(image_view_entries[0]) * image_view_count);
    object_hash = fnv1a64_update_u64(object_hash, sampler_count);
    object_hash = fnv1a64_update_bytes(object_hash, sampler_entries, sizeof(sampler_entries[0]) * sampler_count);
    object_header->objects.object_hash = object_hash;

    size_t cursor = sizeof(PdockerGpuVulkanDispatchV5ObjectFrameHeader);
    int rc = frame_append_bytes(frame, frame_capacity, &cursor,
                                resources, sizeof(resources[0]) * resource_count,
                                &header->resource_table_offset);
    if (rc != 0) goto cleanup;
    header->resource_table_size = sizeof(resources[0]) * resource_count;
    rc = frame_append_bytes(frame, frame_capacity, &cursor,
                            descriptors, sizeof(descriptors[0]) * descriptor_count,
                            &header->descriptor_table_offset);
    if (rc != 0) goto cleanup;
    header->descriptor_table_size = sizeof(descriptors[0]) * descriptor_count;
    rc = frame_append_bytes(frame, frame_capacity, &cursor,
                            image_entries, sizeof(image_entries[0]) * image_count,
                            &object_header->objects.image_table_offset);
    if (rc != 0) goto cleanup;
    object_header->objects.image_table_size = sizeof(image_entries[0]) * image_count;
    rc = frame_append_bytes(frame, frame_capacity, &cursor,
                            image_view_entries, sizeof(image_view_entries[0]) * image_view_count,
                            &object_header->objects.image_view_table_offset);
    if (rc != 0) goto cleanup;
    object_header->objects.image_view_table_size = sizeof(image_view_entries[0]) * image_view_count;
    rc = frame_append_bytes(frame, frame_capacity, &cursor,
                            sampler_entries, sizeof(sampler_entries[0]) * sampler_count,
                            &object_header->objects.sampler_table_offset);
    if (rc != 0) goto cleanup;
    object_header->objects.sampler_table_size = sizeof(sampler_entries[0]) * sampler_count;
    rc = frame_append_bytes(frame, frame_capacity, &cursor,
                            specs, sizeof(specs[0]) * specialization_entry_count,
                            &header->specialization_table_offset);
    if (rc != 0) goto cleanup;
    header->specialization_table_size = sizeof(specs[0]) * specialization_entry_count;
    rc = frame_append_bytes(frame, frame_capacity, &cursor,
                            specialization_data, specialization_data_size,
                            &header->specialization_data_offset);
    if (rc != 0) goto cleanup;
    header->specialization_data_size = specialization_data_size;
    rc = frame_append_bytes(frame, frame_capacity, &cursor, push, push_size, &header->push_offset);
    if (rc != 0) goto cleanup;
    rc = frame_append_bytes(frame, frame_capacity, &cursor, entry_name, entry_name_size, &header->entry_name_offset);
    if (rc != 0) goto cleanup;
    rc = frame_append_bytes(frame, frame_capacity, &cursor, option_text, option_text_size, &header->option_text_offset);
    if (rc != 0) goto cleanup;
    header->frame_size = cursor;
    header->frame_hash = fnv1a64_bytes(frame, cursor);

    rc = send_vulkan_dispatch_v5_frame_with_fds(socket_fd, frame, cursor, fds, fd_count);
cleanup:
    free(frame);
    return rc;
}

static size_t descriptor_binding_size(const PdockerVkDescriptorBinding *binding);
static int validate_descriptor_transport_shape(
        const PdockerVkDescriptorBinding *binding,
        uint32_t set_index,
        uint32_t binding_index,
        size_t *effective_size);
static bool image_mip_extent(const PdockerVkImage *image,
                             uint32_t mip_level,
                             VkExtent3D *out);
static bool descriptor_type_supported_by_v5_object_transport(VkDescriptorType type);
static VkSubgroupFeatureFlags advertised_subgroup_operations(void);
static uint32_t advertised_subgroup_size(void);
static bool resolve_copy_alias(PdockerVkBuffer *buffer,
                               VkDeviceSize offset,
                               VkDeviceSize size,
                               PdockerVkMemory **src_memory,
                               VkDeviceSize *src_offset);

static int resolve_vulkan_dispatch_group_counts(
        const PdockerVkDispatchOp *op,
        uint32_t *group_count_x,
        uint32_t *group_count_y,
        uint32_t *group_count_z) {
    if (!op || !group_count_x || !group_count_y || !group_count_z) return -EINVAL;
    if (!op->dispatch_indirect) {
        *group_count_x = op->dispatch_x;
        *group_count_y = op->dispatch_y;
        *group_count_z = op->dispatch_z;
        return 0;
    }
    const PdockerVkBuffer *buffer = op->dispatch_indirect_buffer;
    if (!buffer || !buffer->memory || !buffer->memory->map ||
        buffer->memory->map == MAP_FAILED || op->dispatch_indirect_offset % 4u != 0) {
        return -EINVAL;
    }
    if (op->dispatch_indirect_offset > buffer->size ||
        (VkDeviceSize)sizeof(uint32_t) * 3u > buffer->size - op->dispatch_indirect_offset) {
        return -ERANGE;
    }
    VkDeviceSize absolute = buffer->memory_offset + op->dispatch_indirect_offset;
    if (absolute < buffer->memory_offset || absolute > (VkDeviceSize)buffer->memory->size ||
        (VkDeviceSize)sizeof(uint32_t) * 3u > (VkDeviceSize)buffer->memory->size - absolute) {
        return -ERANGE;
    }
    uint32_t counts[3] = {0, 0, 0};
    memcpy(counts, (const char *)buffer->memory->map + absolute, sizeof(counts));
    *group_count_x = counts[0];
    *group_count_y = counts[1];
    *group_count_z = counts[2];
    return 0;
}

static int send_generic_vulkan_dispatch_op(const PdockerVkDispatchOp *op) {
    if (!op || !op->pipeline || !op->pipeline->shader) {
        fprintf(stderr,
                "pdocker-vulkan-icd: generic dispatch rejected: invalid op op=%p pipeline=%p shader=%p\n",
                (const void *)op,
                op ? (const void *)op->pipeline : NULL,
                op && op->pipeline ? (const void *)op->pipeline->shader : NULL);
        return -EINVAL;
    }
    PdockerVkShaderModule *shader = op->pipeline->shader;
    if (shader->code_fd < 0 || shader->code_size == 0) {
        fprintf(stderr,
                "pdocker-vulkan-icd: generic dispatch rejected: invalid shader fd=%d size=%zu shader=%p\n",
                shader->code_fd,
                shader->code_size,
                (void *)shader);
        return -EINVAL;
    }
    const bool strict_passthrough =
        env_truthy_default("PDOCKER_GPU_STRICT_PASSTHROUGH", false);
    if (strict_passthrough && copy_alias_enabled()) {
        fprintf(stderr,
                "pdocker-vulkan-icd: rejecting PDOCKER_VULKAN_ALIAS_COPIES under strict passthrough\n");
        return -EINVAL;
    }
    const uint64_t dispatch_id = __sync_add_and_fetch(&g_generic_dispatch_sequence, 1);

    int fds[PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_FDS];
    uint32_t bindings[PDOCKER_VK_MAX_STORAGE_BUFFERS];
    VkDeviceSize offsets[PDOCKER_VK_MAX_STORAGE_BUFFERS];
    size_t sizes[PDOCKER_VK_MAX_STORAGE_BUFFERS];
    VkDeviceSize api_offsets[PDOCKER_VK_MAX_STORAGE_BUFFERS];
    VkDeviceSize api_ranges[PDOCKER_VK_MAX_STORAGE_BUFFERS];
    size_t api_buffer_sizes[PDOCKER_VK_MAX_STORAGE_BUFFERS];
    uint32_t api_descriptor_types[PDOCKER_VK_MAX_STORAGE_BUFFERS];
    uint32_t api_dynamic_flags[PDOCKER_VK_MAX_STORAGE_BUFFERS];
    VkDeviceSize api_dynamic_offsets[PDOCKER_VK_MAX_STORAGE_BUFFERS];
    VkDeviceSize api_memory_offsets[PDOCKER_VK_MAX_STORAGE_BUFFERS];
    size_t api_memory_sizes[PDOCKER_VK_MAX_STORAGE_BUFFERS];
    uintptr_t api_memory_ids[PDOCKER_VK_MAX_STORAGE_BUFFERS];
    uintptr_t api_buffer_ids[PDOCKER_VK_MAX_STORAGE_BUFFERS];
    uint32_t api_descriptor_sets[PDOCKER_VK_MAX_STORAGE_BUFFERS];
    uint32_t api_descriptor_array_elements[PDOCKER_VK_MAX_STORAGE_BUFFERS];
    uint32_t image_descriptor_sets[PDOCKER_VK_MAX_STORAGE_BUFFERS];
    uint32_t image_descriptor_array_elements[PDOCKER_VK_MAX_STORAGE_BUFFERS];
    uint32_t image_descriptor_bindings[PDOCKER_VK_MAX_STORAGE_BUFFERS];
    uint32_t image_descriptor_types[PDOCKER_VK_MAX_STORAGE_BUFFERS];
    uint32_t image_descriptor_view_indices[PDOCKER_VK_MAX_STORAGE_BUFFERS];
    uint32_t image_descriptor_sampler_indices[PDOCKER_VK_MAX_STORAGE_BUFFERS];
    VkImageLayout image_descriptor_layouts[PDOCKER_VK_MAX_STORAGE_BUFFERS];
    PdockerVkImage *image_objects[PDOCKER_VK_MAX_STORAGE_BUFFERS];
    PdockerVkImageView *image_view_objects[PDOCKER_VK_MAX_STORAGE_BUFFERS];
    PdockerVkSampler *sampler_objects[PDOCKER_VK_MAX_STORAGE_BUFFERS];
    size_t binding_count = 0;
    size_t image_descriptor_count = 0;
    size_t image_count = 0;
    size_t image_view_count = 0;
    size_t sampler_count = 0;
    memset(fds, -1, sizeof(fds));
    memset(api_descriptor_array_elements, 0, sizeof(api_descriptor_array_elements));
    memset(image_descriptor_sets, 0, sizeof(image_descriptor_sets));
    memset(image_descriptor_array_elements, 0, sizeof(image_descriptor_array_elements));
    memset(image_descriptor_bindings, 0, sizeof(image_descriptor_bindings));
    memset(image_descriptor_types, 0, sizeof(image_descriptor_types));
    for (size_t i = 0; i < PDOCKER_VK_MAX_STORAGE_BUFFERS; ++i) {
        image_descriptor_view_indices[i] = PDOCKER_GPU_V5_DESCRIPTOR_OBJECT_NONE;
        image_descriptor_sampler_indices[i] = PDOCKER_GPU_V5_DESCRIPTOR_OBJECT_NONE;
    }
    memset(image_descriptor_layouts, 0, sizeof(image_descriptor_layouts));
    memset(image_objects, 0, sizeof(image_objects));
    memset(image_view_objects, 0, sizeof(image_view_objects));
    memset(sampler_objects, 0, sizeof(sampler_objects));
    fds[0] = shader->code_fd;
    bool descriptor_array_transport_required = false;
    for (uint32_t set_index = 0; set_index < PDOCKER_VK_MAX_DESCRIPTOR_SETS; ++set_index) {
        if (!op->set_snapshot_used[set_index]) continue;
        const PdockerVkDescriptorSet *snapshot_set = &op->set_snapshots[set_index];
        const PdockerVkDescriptorSet *live_set = op->set_handles[set_index];
        const PdockerVkDescriptorSet *set = live_set ? live_set : snapshot_set;
        const PdockerVkDescriptorSetLayout *layout = set->layout ? set->layout : snapshot_set->layout;
        for (uint32_t i = 0; i < PDOCKER_VK_MAX_STORAGE_BUFFERS; ++i) {
            uint32_t array_limit = layout ? layout->storage_binding_counts[i] : PDOCKER_VK_MAX_DESCRIPTOR_ARRAY_ELEMENTS;
            if (array_limit == 0 && !layout) array_limit = PDOCKER_VK_MAX_DESCRIPTOR_ARRAY_ELEMENTS;
            if (array_limit > PDOCKER_VK_MAX_DESCRIPTOR_ARRAY_ELEMENTS) return -E2BIG;
            for (uint32_t array_element = 0; array_element < array_limit; ++array_element) {
                PdockerVkDescriptorBinding *binding =
                    (PdockerVkDescriptorBinding *)&set->storage_buffers[i][array_element];
                if (descriptor_type_supported_by_v5_object_transport(binding->descriptor_type)) {
                    VkDescriptorType descriptor_type = binding->descriptor_type;
                    const bool requires_view = descriptor_type_requires_image_view(descriptor_type);
                    const bool requires_sampler = descriptor_type_requires_sampler(descriptor_type);
                    if ((requires_view && !binding->image_view) ||
                        (requires_sampler && !binding->sampler)) {
                        continue;
                    }
                    descriptor_array_transport_required = descriptor_array_transport_required || array_element != 0;
                    if (image_descriptor_count >= PDOCKER_VK_MAX_STORAGE_BUFFERS) return -E2BIG;
                    uint32_t view_index = PDOCKER_GPU_V5_DESCRIPTOR_OBJECT_NONE;
                    uint32_t sampler_index = PDOCKER_GPU_V5_DESCRIPTOR_OBJECT_NONE;
                    if (requires_view) {
                        if (!binding->image_view->image ||
                            !binding->image_view->image->memory ||
                            binding->image_view->image->memory->fd < 0) {
                            fprintf(stderr,
                                    "pdocker-vulkan-icd: generic dispatch rejected: invalid image descriptor dispatch_id=%llu set=%u binding=%u array=%u type=%u image_view=%p image=%p memory=%p fd=%d\n",
                                    (unsigned long long)dispatch_id,
                                    set_index,
                                    i,
                                    array_element,
                                    descriptor_type,
                                    (void *)binding->image_view,
                                    binding->image_view ? (void *)binding->image_view->image : NULL,
                                    binding->image_view && binding->image_view->image ? (void *)binding->image_view->image->memory : NULL,
                                    binding->image_view && binding->image_view->image && binding->image_view->image->memory ? binding->image_view->image->memory->fd : -1);
                            return -EINVAL;
                        }
                        int existing_view =
                            find_image_view_table_index(image_view_objects,
                                                        image_view_count,
                                                        binding->image_view);
                        if (existing_view < 0) {
                            if (image_view_count >= PDOCKER_VK_MAX_STORAGE_BUFFERS) return -E2BIG;
                            existing_view = (int)image_view_count;
                            image_view_objects[image_view_count++] = binding->image_view;
                        }
                        view_index = (uint32_t)existing_view;
                        int existing_image =
                            find_image_table_index(image_objects,
                                                   image_count,
                                                   binding->image_view->image);
                        if (existing_image < 0) {
                            if (image_count >= PDOCKER_VK_MAX_STORAGE_BUFFERS) return -E2BIG;
                            image_objects[image_count++] = binding->image_view->image;
                        }
                    }
                    if (requires_sampler) {
                        int existing_sampler =
                            find_sampler_table_index(sampler_objects,
                                                     sampler_count,
                                                     binding->sampler);
                        if (existing_sampler < 0) {
                            if (sampler_count >= PDOCKER_VK_MAX_STORAGE_BUFFERS) return -E2BIG;
                            existing_sampler = (int)sampler_count;
                            sampler_objects[sampler_count++] = binding->sampler;
                        }
                        sampler_index = (uint32_t)existing_sampler;
                    }
                    image_descriptor_sets[image_descriptor_count] = set_index;
                    image_descriptor_bindings[image_descriptor_count] = i;
                    image_descriptor_array_elements[image_descriptor_count] = array_element;
                    image_descriptor_types[image_descriptor_count] = descriptor_type;
                    image_descriptor_view_indices[image_descriptor_count] = view_index;
                    image_descriptor_sampler_indices[image_descriptor_count] = sampler_index;
                    image_descriptor_layouts[image_descriptor_count] = binding->image_layout;
                    image_descriptor_count++;
                    continue;
                }
                if (!binding->buffer || !binding->buffer->memory) continue;
                descriptor_array_transport_required = descriptor_array_transport_required || array_element != 0;
                size_t bytes = 0;
                int shape_rc = validate_descriptor_transport_shape(binding, set_index, i, &bytes);
                if (shape_rc < 0) {
                    fprintf(stderr,
                            "pdocker-vulkan-icd: generic dispatch rejected: descriptor shape dispatch_id=%llu rc=%d set=%u binding=%u array=%u descriptor_type=%u buffer=%p memory=%p offset=%llu range=%llu\n",
                            (unsigned long long)dispatch_id,
                            shape_rc,
                            set_index,
                            i,
                            array_element,
                            binding->descriptor_type,
                            (void *)binding->buffer,
                            binding->buffer ? (void *)binding->buffer->memory : NULL,
                            (unsigned long long)binding->offset,
                            (unsigned long long)binding->range);
                    return shape_rc;
                }
                if (binding_count >= PDOCKER_VK_MAX_STORAGE_BUFFERS) return -E2BIG;
                api_descriptor_sets[binding_count] = set_index;
                api_descriptor_array_elements[binding_count] = array_element;
                bindings[binding_count] = i;
                PdockerVkMemory *dispatch_memory = binding->buffer->memory;
                VkDeviceSize dispatch_offset = binding->buffer->memory_offset + binding->offset;
                bool alias_hit = false;
                if (copy_alias_enabled()) {
                    alias_hit = resolve_copy_alias(binding->buffer, binding->offset, bytes,
                                                   &dispatch_memory, &dispatch_offset);
                }
                if (!dispatch_memory || dispatch_memory->fd < 0) {
                    fprintf(stderr,
                            "pdocker-vulkan-icd: generic dispatch rejected: invalid dispatch memory dispatch_id=%llu set=%u binding=%u array=%u memory=%p fd=%d alias=%u\n",
                            (unsigned long long)dispatch_id,
                            set_index,
                            i,
                            array_element,
                            (void *)dispatch_memory,
                            dispatch_memory ? dispatch_memory->fd : -1,
                            alias_hit ? 1u : 0u);
                    return -EINVAL;
                }
                if (dispatch_offset > (VkDeviceSize)dispatch_memory->size ||
                    (VkDeviceSize)bytes > (VkDeviceSize)dispatch_memory->size - dispatch_offset) {
                    fprintf(stderr,
                            "pdocker-vulkan-icd: rejecting descriptor outside transport memory"
                            " set=%u binding=%u array=%u dispatch_offset=%llu size=%zu memory_size=%zu alias=%u\n",
                            set_index,
                            i,
                            array_element,
                            (unsigned long long)dispatch_offset,
                            bytes,
                            dispatch_memory ? dispatch_memory->size : 0,
                            alias_hit ? 1u : 0u);
                    return -ERANGE;
                }
                offsets[binding_count] = dispatch_offset;
                sizes[binding_count] = bytes;
                api_offsets[binding_count] = binding->base_offset;
                api_ranges[binding_count] = binding->range;
                api_buffer_sizes[binding_count] = binding->buffer ? binding->buffer->size : 0;
                api_descriptor_types[binding_count] = (uint32_t)binding->descriptor_type;
                api_dynamic_flags[binding_count] = binding->dynamic ? 1u : 0u;
                api_dynamic_offsets[binding_count] = binding->dynamic_offset;
                api_memory_offsets[binding_count] = binding->buffer ? binding->buffer->memory_offset : 0;
                api_memory_sizes[binding_count] = dispatch_memory ? dispatch_memory->size : 0;
                api_memory_ids[binding_count] = (uintptr_t)dispatch_memory;
                api_buffer_ids[binding_count] = (uintptr_t)binding->buffer;
                fds[1 + binding_count] = dispatch_memory->fd;
                trace_guarded_binding(i, dispatch_memory, dispatch_offset, bytes);
                if (alias_hit && trace_allocations()) {
                    fprintf(stderr,
                            "pdocker-vulkan-icd: descriptor alias set=%u binding=%u array=%u offset=%llu range=%zu source_mem=%zu source_off=%llu\n",
                            set_index,
                            i,
                            array_element,
                            (unsigned long long)binding->offset,
                            bytes,
                            dispatch_memory ? dispatch_memory->size : 0,
                            (unsigned long long)dispatch_offset);
                }
                binding_count++;
            }
        }
    }
    if (binding_count == 0 && image_descriptor_count == 0) {
        fprintf(stderr,
                "pdocker-vulkan-icd: generic dispatch rejected: no descriptors found dispatch_id=%llu sets_used_mask=0x%02x live_mask=0x%02x\n",
                (unsigned long long)dispatch_id,
                (op->set_snapshot_used[0] ? 1u : 0u) |
                    (op->set_snapshot_used[1] ? 2u : 0u) |
                    (op->set_snapshot_used[2] ? 4u : 0u) |
                    (op->set_snapshot_used[3] ? 8u : 0u),
                (op->set_handles[0] ? 1u : 0u) |
                    (op->set_handles[1] ? 2u : 0u) |
                    (op->set_handles[2] ? 4u : 0u) |
                    (op->set_handles[3] ? 8u : 0u));
        return -EINVAL;
    }

    const uint64_t source_shader_hash =
        (shader->code_map && shader->code_map != MAP_FAILED)
            ? fnv1a64_bytes(shader->code_map, shader->code_size)
            : 0;
    uint32_t push_size = op->push_constant_size;
    if (op->pipeline && op->pipeline->layout &&
        op->pipeline->layout->push_constant_size > push_size) {
        push_size = op->pipeline->layout->push_constant_size;
    }
    if (push_size > PDOCKER_VK_MAX_PUSH_BYTES) {
        fprintf(stderr,
                "pdocker-vulkan-icd: generic dispatch rejected: push constants too large dispatch_id=%llu push_size=%u max=%u\n",
                (unsigned long long)dispatch_id,
                push_size,
                PDOCKER_VK_MAX_PUSH_BYTES);
        return -E2BIG;
    }
    uint32_t dispatch_x = 0;
    uint32_t dispatch_y = 0;
    uint32_t dispatch_z = 0;
    int dispatch_count_rc = resolve_vulkan_dispatch_group_counts(
        op, &dispatch_x, &dispatch_y, &dispatch_z);
    if (dispatch_count_rc != 0) {
        fprintf(stderr,
                "pdocker-vulkan-icd: generic dispatch rejected: indirect group counts dispatch_id=%llu rc=%d indirect=%u buffer=%p offset=%llu\n",
                (unsigned long long)dispatch_id,
                dispatch_count_rc,
                op->dispatch_indirect ? 1u : 0u,
                (void *)op->dispatch_indirect_buffer,
                (unsigned long long)op->dispatch_indirect_offset);
        return dispatch_count_rc;
    }
    char push_hex[PDOCKER_VK_MAX_PUSH_BYTES * 2 + 1];
    hex_encode(op->push_constants, push_size, push_hex, sizeof(push_hex));
    const char *push_token = push_size ? push_hex : "-";
    char entry_hex[PDOCKER_VK_MAX_ENTRY_NAME * 2 + 1];
    const char *entry_name = op->pipeline->entry_name[0] ? op->pipeline->entry_name : "main";
    hex_encode((const uint8_t *)entry_name, strlen(entry_name), entry_hex, sizeof(entry_hex));
    char spec_hex[PDOCKER_VK_MAX_SPECIALIZATION_BYTES * 2 + 1];
    hex_encode(op->pipeline->specialization_data,
               op->pipeline->specialization_data_size,
               spec_hex,
               sizeof(spec_hex));
    const char *spec_token = op->pipeline->specialization_data_size ? spec_hex : "-";
    if (op->pipeline->specialization_too_large) {
        fprintf(stderr,
                "pdocker-vulkan-icd: generic dispatch rejected: specialization too large dispatch_id=%llu entries=%u data_size=%zu\n",
                (unsigned long long)dispatch_id,
                op->pipeline->specialization_entry_count,
                op->pipeline->specialization_data_size);
        return -E2BIG;
    }

    PdockerVkSpirvProbeReplay probe;
    int probe_rc = prepare_spirv_probe_replay(&probe,
                                              source_shader_hash,
                                              binding_count,
                                              api_descriptor_sets,
                                              bindings);
    if (probe_rc < 0) {
        fprintf(stderr,
                "pdocker-vulkan-icd: generic dispatch rejected: probe replay setup dispatch_id=%llu rc=%d source_hash=0x%016llx binding_count=%zu\n",
                (unsigned long long)dispatch_id,
                probe_rc,
                (unsigned long long)source_shader_hash,
                binding_count);
        return probe_rc;
    }
    size_t shader_size_to_send = shader->code_size;
    uint64_t shader_hash_to_send = source_shader_hash;
    if (probe.enabled) {
        fds[0] = probe.shader_fd;
        shader_size_to_send = probe.shader_size;
        shader_hash_to_send = probe.effective_shader_hash;
        uintptr_t probe_memory_id =
            (uintptr_t)0x5044513600000000ull ^ (uintptr_t)dispatch_id;
        uintptr_t probe_buffer_id =
            (uintptr_t)0x5044513600000001ull ^ ((uintptr_t)dispatch_id << 1);
        if (probe_memory_id == 0) probe_memory_id = (uintptr_t)0x50445136u;
        if (probe_buffer_id == 0) probe_buffer_id = (uintptr_t)0x50445137u;
        api_descriptor_sets[binding_count] = probe.debug_set;
        api_descriptor_array_elements[binding_count] = 0;
        bindings[binding_count] = probe.debug_binding;
        offsets[binding_count] = 0;
        sizes[binding_count] = probe.debug_bytes;
        api_offsets[binding_count] = 0;
        api_ranges[binding_count] = probe.debug_bytes;
        api_buffer_sizes[binding_count] = probe.debug_bytes;
        api_descriptor_types[binding_count] = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
        api_dynamic_flags[binding_count] = 0;
        api_dynamic_offsets[binding_count] = 0;
        api_memory_offsets[binding_count] = 0;
        api_memory_sizes[binding_count] = probe.debug_bytes;
        /*
         * The debug SSBO is synthetic, but in strict Vulkan passthrough mode
         * the executor intentionally validates the same object-graph contract
         * for every binding: memory id, buffer id, object sizes, descriptor
         * offsets, and absolute transport offset must all be coherent.  Use
         * deterministic non-zero pseudo object ids for the probe binding
         * instead of weakening the contract or adding a probe-only bypass.
         */
        api_memory_ids[binding_count] = probe_memory_id;
        api_buffer_ids[binding_count] = probe_buffer_id;
        fds[1 + binding_count] = probe.debug_fd;
        binding_count++;
    }

    char command[4096];
#define PDOCKER_VK_COMMAND_TOO_LONG(stage_, used_) \
    do { \
        trace_vulkan_command_length_warning(dispatch_id, \
                                            (used_), \
                                            sizeof(command), \
                                            (stage_), \
                                            true); \
        close_spirv_probe_replay(&probe); \
        return -ENAMETOOLONG; \
    } while (0)
#define PDOCKER_VK_APPEND_TOO_LONG(stage_) \
    PDOCKER_VK_COMMAND_TOO_LONG((stage_), off + ((n > 0) ? (size_t)n : 0))
    int n = snprintf(command, sizeof(command),
                     "VULKAN_DISPATCH_V4 %zu %zu %u %u %u %u %s %s %u %zu %s",
                     shader_size_to_send,
                     binding_count,
                     push_size,
                     dispatch_x,
                     dispatch_y ? dispatch_y : 1,
                     dispatch_z ? dispatch_z : 1,
                     push_token,
                     entry_hex[0] ? entry_hex : "-",
                     op->pipeline->specialization_entry_count,
                     op->pipeline->specialization_data_size,
                     spec_token);
    if (n < 0 || (size_t)n >= sizeof(command)) {
        PDOCKER_VK_COMMAND_TOO_LONG("core-header", (n > 0) ? (size_t)n : 0);
    }
    size_t off = (size_t)n;
    for (uint32_t i = 0; i < op->pipeline->specialization_entry_count; ++i) {
        const VkSpecializationMapEntry *entry = &op->pipeline->specialization_entries[i];
        n = snprintf(command + off, sizeof(command) - off,
                     " %u %u %zu",
                     entry->constantID,
                     entry->offset,
                     entry->size);
        if (n < 0 || (size_t)n >= sizeof(command) - off) PDOCKER_VK_APPEND_TOO_LONG("append-option");
        off += (size_t)n;
    }
    for (size_t i = 0; i < binding_count; ++i) {
        n = snprintf(command + off, sizeof(command) - off,
                     " %u %u %llu %zu %llu %llu %zu %u %u %llu %zu %llu %llu",
                     api_descriptor_sets[i],
                     bindings[i],
                     (unsigned long long)offsets[i],
                     sizes[i],
                     (unsigned long long)api_offsets[i],
                     (unsigned long long)api_ranges[i],
                     api_buffer_sizes[i],
                     api_descriptor_types[i],
                     api_dynamic_flags[i],
                     (unsigned long long)api_memory_offsets[i],
                     api_memory_sizes[i],
                     (unsigned long long)api_memory_ids[i],
                     (unsigned long long)api_buffer_ids[i]);
        if (n < 0 || (size_t)n >= sizeof(command) - off) PDOCKER_VK_APPEND_TOO_LONG("append-option");
        off += (size_t)n;
    }
    if (op->base_group_x || op->base_group_y || op->base_group_z) {
        n = snprintf(command + off, sizeof(command) - off,
                     " base_group_x=%u base_group_y=%u base_group_z=%u",
                     op->base_group_x,
                     op->base_group_y,
                     op->base_group_z);
        if (n < 0 || (size_t)n >= sizeof(command) - off) PDOCKER_VK_APPEND_TOO_LONG("append-base-group");
        off += (size_t)n;
    }
    const size_t core_command_len = off;
    const uint64_t core_command_hash = fnv1a64_bytes(command, core_command_len);
    const uint64_t shader_hash = shader_hash_to_send;
    const uint64_t push_hash = fnv1a64_bytes(op->push_constants, push_size);
    const uint64_t specialization_data_hash =
        fnv1a64_bytes(op->pipeline->specialization_data,
                      op->pipeline->specialization_data_size);
    const uint64_t specialization_hash =
        fnv1a64_specialization_hash(op->pipeline->specialization_entries,
                                    op->pipeline->specialization_entry_count,
                                    op->pipeline->specialization_data,
                                    op->pipeline->specialization_data_size);
    dispatch_y = dispatch_y ? dispatch_y : 1;
    dispatch_z = dispatch_z ? dispatch_z : 1;
    uint64_t dispatch_hash = 1469598103934665603ull;
    dispatch_hash = fnv1a64_update_u32(dispatch_hash, dispatch_x);
    dispatch_hash = fnv1a64_update_u32(dispatch_hash, dispatch_y);
    dispatch_hash = fnv1a64_update_u32(dispatch_hash, dispatch_z);
    dispatch_hash = fnv1a64_update_u32(dispatch_hash, op->base_group_x);
    dispatch_hash = fnv1a64_update_u32(dispatch_hash, op->base_group_y);
    dispatch_hash = fnv1a64_update_u32(dispatch_hash, op->base_group_z);
    uint64_t descriptor_hash = 1469598103934665603ull;
    descriptor_hash = fnv1a64_update_u64(descriptor_hash, (uint64_t)binding_count);
    for (size_t i = 0; i < binding_count; ++i) {
        descriptor_hash = fnv1a64_update_u32(descriptor_hash, api_descriptor_sets[i]);
        descriptor_hash = fnv1a64_update_u32(descriptor_hash, bindings[i]);
        descriptor_hash = fnv1a64_update_u32(descriptor_hash, api_descriptor_array_elements[i]);
        descriptor_hash = fnv1a64_update_u64(descriptor_hash, (uint64_t)offsets[i]);
        descriptor_hash = fnv1a64_update_u64(descriptor_hash, (uint64_t)sizes[i]);
        descriptor_hash = fnv1a64_update_u64(descriptor_hash, (uint64_t)api_offsets[i]);
        descriptor_hash = fnv1a64_update_u64(descriptor_hash, (uint64_t)api_ranges[i]);
        descriptor_hash = fnv1a64_update_u64(descriptor_hash, (uint64_t)api_buffer_sizes[i]);
        descriptor_hash = fnv1a64_update_u32(descriptor_hash, api_descriptor_types[i]);
        descriptor_hash = fnv1a64_update_u32(descriptor_hash, api_dynamic_flags[i]);
        descriptor_hash = fnv1a64_update_u64(descriptor_hash, (uint64_t)api_memory_offsets[i]);
        descriptor_hash = fnv1a64_update_u64(descriptor_hash, (uint64_t)api_memory_sizes[i]);
        descriptor_hash = fnv1a64_update_u64(descriptor_hash, (uint64_t)api_memory_ids[i]);
        descriptor_hash = fnv1a64_update_u64(descriptor_hash, (uint64_t)api_buffer_ids[i]);
    }
    if (reconcile_api_evidence_log_enabled()) {
        n = snprintf(command + off, sizeof(command) - off,
                     " dispatch_id=%llu sender_core_command_hash=0x%016llx"
                     " sender_spirv_hash=0x%016llx sender_push_hash=0x%016llx"
                     " sender_specialization_hash=0x%016llx sender_descriptor_hash=0x%016llx"
                     " sender_dispatch_hash=0x%016llx",
                     (unsigned long long)dispatch_id,
                     (unsigned long long)core_command_hash,
                     (unsigned long long)shader_hash,
                     (unsigned long long)push_hash,
                     (unsigned long long)specialization_hash,
                     (unsigned long long)descriptor_hash,
                     (unsigned long long)dispatch_hash);
        if (n < 0 || (size_t)n >= sizeof(command) - off) PDOCKER_VK_APPEND_TOO_LONG("append-option");
        off += (size_t)n;
    }
    if (probe.enabled) {
        /*
         * Probe replay sends an instrumented/effective SPIR-V module through
         * fd[0], so the executor cannot recover the original llama.cpp source
         * shader identity by hashing the received bytes.  Carry the
         * source/effective relation explicitly and fail closed on the executor
         * side if it does not match the received module hash or if the debug
         * binding option is absent.  Pipeline cache/reconciliation still use
         * sender_spirv_hash above, which is the actual transmitted shader.
         */
        n = snprintf(command + off, sizeof(command) - off,
                     " sender_source_spirv_hash=0x%016llx"
                     " sender_effective_spirv_hash=0x%016llx",
                     (unsigned long long)source_shader_hash,
                     (unsigned long long)shader_hash_to_send);
        if (n < 0 || (size_t)n >= sizeof(command) - off) PDOCKER_VK_APPEND_TOO_LONG("append-option");
        off += (size_t)n;
    }
    typedef struct {
        const char *env;
        const char *option;
        bool default_value;
    } PdockerVkBoolBridgeOption;
    static const PdockerVkBoolBridgeOption bool_bridge_options[] = {
#define PDOCKER_VK_BOOL_BRIDGE_OPTION(env_name, option_name, has_field, value_field, default_value) \
        {#env_name, #option_name, (default_value) != 0},
        PDOCKER_GPU_VULKAN_BOOL_DISPATCH_OPTIONS(PDOCKER_VK_BOOL_BRIDGE_OPTION)
#undef PDOCKER_VK_BOOL_BRIDGE_OPTION
#define PDOCKER_VK_BOOL_BRIDGE_OPTION_NO_HAS(env_name, option_name, value_field, default_value) \
        {#env_name, #option_name, (default_value) != 0},
        PDOCKER_GPU_VULKAN_BOOL_DISPATCH_OPTIONS_NO_HAS(PDOCKER_VK_BOOL_BRIDGE_OPTION_NO_HAS)
#undef PDOCKER_VK_BOOL_BRIDGE_OPTION_NO_HAS
    };
    for (size_t i = 0; i < sizeof(bool_bridge_options) / sizeof(bool_bridge_options[0]); ++i) {
        const PdockerVkBoolBridgeOption *option = &bool_bridge_options[i];
        if (!getenv(option->env)) continue;
        n = snprintf(command + off, sizeof(command) - off,
                     " %s=%u",
                     option->option,
                     env_truthy_default(option->env, option->default_value) ? 1u : 0u);
        if (n < 0 || (size_t)n >= sizeof(command) - off) PDOCKER_VK_APPEND_TOO_LONG("append-option");
        off += (size_t)n;
    }

    typedef struct {
        const char *env;
        const char *option;
    } PdockerVkU64BridgeOption;
    static const PdockerVkU64BridgeOption u64_bridge_options[] = {
#define PDOCKER_VK_U64_BRIDGE_OPTION(env_name, option_name, has_field, value_field) \
        {#env_name, #option_name},
        PDOCKER_GPU_VULKAN_SIZE_DISPATCH_OPTIONS(PDOCKER_VK_U64_BRIDGE_OPTION)
#undef PDOCKER_VK_U64_BRIDGE_OPTION
    };
    for (size_t i = 0; i < sizeof(u64_bridge_options) / sizeof(u64_bridge_options[0]); ++i) {
        const PdockerVkU64BridgeOption *option = &u64_bridge_options[i];
        const char *value = getenv(option->env);
        if (!value || !value[0]) continue;
        char *end = NULL;
        unsigned long long parsed = strtoull(value, &end, 10);
        if (!end || *end != '\0') continue;
        n = snprintf(command + off, sizeof(command) - off,
                     " %s=%llu",
                     option->option,
                     parsed);
        if (n < 0 || (size_t)n >= sizeof(command) - off) PDOCKER_VK_APPEND_TOO_LONG("append-option");
        off += (size_t)n;
    }
    if (trace_allocations() || env_truthy_default("PDOCKER_GPU_DISPATCH_PROFILE_RESPONSE", false)) {
        n = snprintf(command + off, sizeof(command) - off, " profile=1");
        if (n < 0 || (size_t)n >= sizeof(command) - off) PDOCKER_VK_APPEND_TOO_LONG("append-option");
        off += (size_t)n;
    }
    n = snprintf(command + off, sizeof(command) - off,
                 " requested_feature_mask=%llu",
                 (unsigned long long)op->pipeline->requested_feature_mask);
    if (n < 0 || (size_t)n >= sizeof(command) - off) PDOCKER_VK_APPEND_TOO_LONG("append-option");
    off += (size_t)n;
    n = snprintf(command + off, sizeof(command) - off,
                 " v4_binding_schema=0x%016llx v4_binding_fields=%u",
                 (unsigned long long)PDOCKER_GPU_VULKAN_DISPATCH_V4_BINDING_SCHEMA_HASH,
                 PDOCKER_GPU_VULKAN_DISPATCH_V4_BINDING_FIELD_COUNT);
    if (n < 0 || (size_t)n >= sizeof(command) - off) PDOCKER_VK_APPEND_TOO_LONG("append-option");
    off += (size_t)n;
    if (off + 2 >= sizeof(command)) PDOCKER_VK_COMMAND_TOO_LONG("newline", off + 2);
    command[off++] = '\n';
    command[off] = '\0';

    const size_t raw_command_len = off;
    const uint64_t raw_command_hash = fnv1a64_bytes(command, raw_command_len);
    trace_vulkan_command_length_warning(dispatch_id,
                                        raw_command_len,
                                        sizeof(command),
                                        "pre-send",
                                        false);
    trace_vulkan_reconcile_evidence(dispatch_id,
                                    raw_command_len,
                                    raw_command_hash,
                                    core_command_len,
                                    core_command_hash,
                                    shader_size_to_send,
                                    shader_hash,
                                    push_size,
                                    push_hash,
                                    op->pipeline->specialization_entry_count,
                                    op->pipeline->specialization_data_size,
                                    specialization_data_hash,
                                    specialization_hash,
                                    op->dispatch_x,
                                    dispatch_y,
                                    dispatch_z,
                                    binding_count,
                                    descriptor_hash,
                                    dispatch_hash,
                                    api_descriptor_sets,
                                    api_descriptor_array_elements,
                                    bindings,
                                    offsets,
                                    sizes,
                                    api_offsets,
                                    api_ranges,
                                    api_buffer_sizes,
                                    api_descriptor_types,
                                    api_dynamic_flags,
                                    api_dynamic_offsets,
                                    api_memory_offsets,
                                    api_memory_sizes,
                                    api_memory_ids,
                                    api_buffer_ids);
#undef PDOCKER_VK_APPEND_TOO_LONG
#undef PDOCKER_VK_COMMAND_TOO_LONG
    const bool lifecycle_log = dispatch_lifecycle_log_enabled();
    const double lifecycle_start_ms = monotonic_ms();
    if (lifecycle_log) {
        fprintf(stderr,
                "pdocker-vulkan-icd: generic dispatch lifecycle: "
                "{\"component\":\"icd\",\"event\":\"begin\",\"dispatch_id\":%llu,"
                "\"spirv_hash\":\"0x%016llx\",\"shader_bytes\":%zu,"
                "\"bindings\":%zu,\"dispatch\":[%u,%u,%u]}\n",
                (unsigned long long)dispatch_id,
                (unsigned long long)shader_hash,
                shader_size_to_send,
                binding_count,
                op->dispatch_x,
                op->dispatch_y ? op->dispatch_y : 1,
                op->dispatch_z ? op->dispatch_z : 1);
        fflush(stderr);
    }

    int socket_fd = connect_queue();
    if (socket_fd < 0) {
        if (lifecycle_log) {
            fprintf(stderr,
                    "pdocker-vulkan-icd: generic dispatch lifecycle: "
                    "{\"component\":\"icd\",\"event\":\"end\",\"dispatch_id\":%llu,"
                    "\"rc\":%d,\"elapsed_ms\":%.3f,\"stage\":\"connect\"}\n",
                    (unsigned long long)dispatch_id,
                    socket_fd,
                    monotonic_ms() - lifecycle_start_ms);
            fflush(stderr);
        }
        close_spirv_probe_replay(&probe);
        return socket_fd;
    }
    const bool requires_v5_frame = descriptor_array_transport_required || image_descriptor_count > 0;
    if ((vulkan_v5_frame_enabled() || requires_v5_frame) && !copy_alias_enabled()) {
        const char *option_text = "";
        size_t option_text_size = 0;
        if (raw_command_len > core_command_len + 1u &&
            command[core_command_len] == ' ') {
            option_text = command + core_command_len + 1u;
            option_text_size = raw_command_len - core_command_len - 1u;
            if (option_text_size > 0 && option_text[option_text_size - 1u] == '\n') {
                option_text_size--;
            }
        }
        int rc = send_generic_vulkan_dispatch_v5_1_op(
            socket_fd,
            dispatch_id,
            fds,
            binding_count,
            image_descriptor_count,
            shader_size_to_send,
            shader_hash,
            op->dispatch_x,
            dispatch_y,
            dispatch_z,
            op->push_constants,
            push_size,
            push_hash,
            entry_name,
            op->pipeline->specialization_entries,
            op->pipeline->specialization_entry_count,
            op->pipeline->specialization_data,
            op->pipeline->specialization_data_size,
            specialization_hash,
            option_text,
            option_text_size,
            api_descriptor_sets,
            api_descriptor_array_elements,
            bindings,
            offsets,
            sizes,
            api_offsets,
            api_ranges,
            api_buffer_sizes,
            api_descriptor_types,
            api_dynamic_flags,
            api_dynamic_offsets,
            api_memory_offsets,
            api_memory_sizes,
            api_memory_ids,
            api_buffer_ids,
            image_descriptor_sets,
            image_descriptor_bindings,
            image_descriptor_array_elements,
            image_descriptor_types,
            image_descriptor_view_indices,
            image_descriptor_sampler_indices,
            image_descriptor_layouts,
            image_objects,
            image_count,
            image_view_objects,
            image_view_count,
            sampler_objects,
            sampler_count,
            descriptor_hash,
            dispatch_hash);
        if (rc == 0) rc = read_dispatch_response_status(socket_fd, "VULKAN_DISPATCH_V5.1");
        if (lifecycle_log) {
            fprintf(stderr,
                    "pdocker-vulkan-icd: generic dispatch lifecycle: "
                    "{\"component\":\"icd\",\"event\":\"end\",\"dispatch_id\":%llu,"
                    "\"rc\":%d,\"elapsed_ms\":%.3f,\"stage\":\"v5.1-response\"}\n",
                    (unsigned long long)dispatch_id,
                    rc,
                    monotonic_ms() - lifecycle_start_ms);
            fflush(stderr);
        }
        close(socket_fd);
        close_spirv_probe_replay(&probe);
        return rc;
    }
    if ((vulkan_v5_frame_enabled() || requires_v5_frame) && copy_alias_enabled()) {
        fprintf(stderr,
                "pdocker-vulkan-icd: V5.1 frame required but disabled for this dispatch "
                "because PDOCKER_VULKAN_ALIAS_COPIES is active\n");
        close(socket_fd);
        close_spirv_probe_replay(&probe);
        return -EOPNOTSUPP;
    }
    char control[CMSG_SPACE(sizeof(fds))];
    struct iovec iov;
    struct msghdr msg;
    memset(control, 0, sizeof(control));
    memset(&iov, 0, sizeof(iov));
    memset(&msg, 0, sizeof(msg));
    iov.iov_base = command;
    iov.iov_len = strlen(command);
    msg.msg_iov = &iov;
    msg.msg_iovlen = 1;
    msg.msg_control = control;
    msg.msg_controllen = sizeof(control);
    struct cmsghdr *cmsg = CMSG_FIRSTHDR(&msg);
    cmsg->cmsg_level = SOL_SOCKET;
    cmsg->cmsg_type = SCM_RIGHTS;
    cmsg->cmsg_len = CMSG_LEN(sizeof(int) * (1 + binding_count));
    memcpy(CMSG_DATA(cmsg), fds, sizeof(int) * (1 + binding_count));
    msg.msg_controllen = CMSG_SPACE(sizeof(int) * (1 + binding_count));

    int rc = 0;
    if (sendmsg(socket_fd, &msg, 0) < 0) {
        rc = -errno;
    } else {
        /*
         * Executor responses are normally tiny.  Keep the hot path allocation
         * free, but allow large diagnostic JSON events without truncating the
         * evidence we need for llama GPU bisection.
         *
         * Ownership policy:
         * - stack_line is used for the common case.
         * - heap_line is per-call/per-thread state, never shared globally.
         * - growth is geometric and capped, so a malformed executor cannot
         *   force unbounded allocation or an alloc/free storm.
         * - the old heap block is freed only after the replacement is ready,
         *   leaving line valid on ENOMEM.
         */
        const size_t max_response = 1024 * 1024;
        char stack_line[16384];
        size_t line_cap = sizeof(stack_line);
        size_t line_off = 0;
        char *heap_line = NULL;
        char *line = stack_line;
        while (line_off + 1 < max_response) {
            if (line_off + 1 >= line_cap) {
                size_t next_cap = line_cap * 2;
                if (next_cap < line_cap) {
                    rc = -EOVERFLOW;
                    break;
                }
                if (next_cap > max_response) next_cap = max_response;
                char *next = (char *)malloc(next_cap);
                if (!next) {
                    rc = -ENOMEM;
                    break;
                }
                memcpy(next, line, line_off);
                free(heap_line);
                heap_line = next;
                line = heap_line;
                line_cap = next_cap;
            }
            char ch;
            ssize_t r = read(socket_fd, &ch, 1);
            if (r <= 0) break;
            line[line_off++] = ch;
            if (ch == '\n') break;
        }
        line[line_off] = '\0';
        if (rc == 0 && line_off + 1 >= max_response) rc = -EMSGSIZE;
        if (trace_allocations() || getenv("PDOCKER_VULKAN_ICD_DEBUG") ||
            env_truthy_default("PDOCKER_GPU_DISPATCH_PROFILE_LOG", false)) {
            fprintf(stderr, "pdocker-vulkan-icd: generic dispatch response: %s", line);
            if (line_off == 0 || line[line_off - 1] != '\n') fprintf(stderr, "\n");
        }
        if (rc == 0 && strstr(line, "\"valid\":true") == NULL) rc = -EIO;
        free(heap_line);
    }
    if (lifecycle_log) {
        fprintf(stderr,
                "pdocker-vulkan-icd: generic dispatch lifecycle: "
                "{\"component\":\"icd\",\"event\":\"end\",\"dispatch_id\":%llu,"
                "\"rc\":%d,\"elapsed_ms\":%.3f,\"stage\":\"response\"}\n",
                (unsigned long long)dispatch_id,
                rc,
                monotonic_ms() - lifecycle_start_ms);
        fflush(stderr);
    }
    close(socket_fd);
    close_spirv_probe_replay(&probe);
    return rc;
}

static int send_generic_vulkan_dispatch(PdockerVkCommandBuffer *cmd) {
    if (!cmd) return -EINVAL;
    PdockerVkDispatchOp op;
    memset(&op, 0, sizeof(op));
    op.pipeline = cmd->compute_pipeline;
    memcpy(op.set_handles, cmd->bound_set_handles, sizeof(op.set_handles));
    memcpy(op.set_snapshots, cmd->bound_set_snapshots, sizeof(op.set_snapshots));
    memcpy(op.set_snapshot_used, cmd->bound_set_used, sizeof(op.set_snapshot_used));
    op.dispatch_x = cmd->dispatch_x;
    op.dispatch_y = cmd->dispatch_y;
    op.dispatch_z = cmd->dispatch_z;
    op.base_group_x = 0;
    op.base_group_y = 0;
    op.base_group_z = 0;
    op.dispatch_indirect = false;
    op.dispatch_indirect_buffer = NULL;
    op.dispatch_indirect_offset = 0;
    op.push_constant_size = cmd->push_constant_size;
    memcpy(op.push_constants, cmd->push_constants, sizeof(op.push_constants));
    memcpy(op.push_constant_ops, cmd->push_constant_ops, sizeof(op.push_constant_ops));
    op.push_constant_op_count = cmd->push_constant_op_count;
    return send_generic_vulkan_dispatch_op(&op);
}

static size_t buffer_available(const PdockerVkBuffer *buffer, VkDeviceSize offset) {
    if (!buffer || !buffer->memory) return 0;
    if (offset > buffer->size) return 0;
    VkDeviceSize absolute = buffer->memory_offset + offset;
    if (absolute > buffer->memory->size) return 0;
    size_t allocation_available = buffer->memory->size - (size_t)absolute;
    size_t buffer_available_bytes = buffer->size - (size_t)offset;
    return allocation_available < buffer_available_bytes
        ? allocation_available
        : buffer_available_bytes;
}

static void *buffer_ptr(PdockerVkBuffer *buffer, VkDeviceSize offset, VkDeviceSize bytes) {
    if (!buffer || !buffer->memory) return NULL;
    size_t available = buffer_available(buffer, offset);
    if ((size_t)bytes > available) return NULL;
    return (char *)buffer->memory->map + buffer->memory_offset + offset;
}

static bool checked_add_u64(uint64_t a, uint64_t b, uint64_t *out) {
    if (!out || a > UINT64_MAX - b) return false;
    *out = a + b;
    return true;
}

static bool image_mip_extent(const PdockerVkImage *image,
                             uint32_t mip_level,
                             VkExtent3D *out) {
    if (!image || !out || mip_level >= image->mip_levels) return false;
    out->width = image->extent.width >> mip_level;
    out->height = image->extent.height >> mip_level;
    out->depth = image->extent.depth >> mip_level;
    if (out->width == 0) out->width = 1;
    if (out->height == 0) out->height = 1;
    if (out->depth == 0) out->depth = 1;
    return true;
}

static bool image_tight_mip_size(const PdockerVkImage *image,
                                 uint32_t mip_level,
                                 uint64_t *out_size) {
    VkExtent3D extent;
    if (!image_mip_extent(image, mip_level, &extent) || !out_size) return false;
    uint64_t pixels = 0;
    if (!checked_mul_u64(extent.width, extent.height, &pixels)) return false;
    if (!checked_mul_u64(pixels, extent.depth, &pixels)) return false;
    if (!checked_mul_u64(pixels, conservative_format_bytes_per_pixel(image->format), out_size)) return false;
    return true;
}

static bool image_tight_layer_stride(const PdockerVkImage *image,
                                     uint64_t *out_stride) {
    if (!image || !out_stride) return false;
    uint64_t stride = 0;
    for (uint32_t mip = 0; mip < image->mip_levels; ++mip) {
        uint64_t mip_size = 0;
        if (!image_tight_mip_size(image, mip, &mip_size)) return false;
        if (!checked_add_u64(stride, mip_size, &stride)) return false;
    }
    *out_stride = stride;
    return true;
}

static bool image_tight_subresource_offset(const PdockerVkImage *image,
                                           uint32_t mip_level,
                                           uint32_t array_layer,
                                           VkOffset3D image_offset,
                                           uint64_t *out_offset) {
    if (!image || !out_offset || mip_level >= image->mip_levels ||
        array_layer >= image->array_layers ||
        image_offset.x < 0 || image_offset.y < 0 || image_offset.z < 0) {
        return false;
    }
    VkExtent3D extent;
    if (!image_mip_extent(image, mip_level, &extent)) return false;
    if ((uint32_t)image_offset.x > extent.width ||
        (uint32_t)image_offset.y > extent.height ||
        (uint32_t)image_offset.z > extent.depth) {
        return false;
    }
    uint64_t layer_stride = 0;
    if (!image_tight_layer_stride(image, &layer_stride)) return false;
    uint64_t offset = 0;
    if (!checked_mul_u64(layer_stride, array_layer, &offset)) return false;
    for (uint32_t mip = 0; mip < mip_level; ++mip) {
        uint64_t mip_size = 0;
        if (!image_tight_mip_size(image, mip, &mip_size)) return false;
        if (!checked_add_u64(offset, mip_size, &offset)) return false;
    }
    uint64_t bpp = conservative_format_bytes_per_pixel(image->format);
    uint64_t row_bytes = 0;
    uint64_t slice_bytes = 0;
    if (!checked_mul_u64(extent.width, bpp, &row_bytes)) return false;
    if (!checked_mul_u64(row_bytes, extent.height, &slice_bytes)) return false;
    uint64_t z_bytes = 0;
    uint64_t y_bytes = 0;
    uint64_t x_bytes = 0;
    if (!checked_mul_u64((uint64_t)image_offset.z, slice_bytes, &z_bytes) ||
        !checked_mul_u64((uint64_t)image_offset.y, row_bytes, &y_bytes) ||
        !checked_mul_u64((uint64_t)image_offset.x, bpp, &x_bytes) ||
        !checked_add_u64(offset, z_bytes, &offset) ||
        !checked_add_u64(offset, y_bytes, &offset) ||
        !checked_add_u64(offset, x_bytes, &offset)) {
        return false;
    }
    *out_offset = offset;
    return true;
}

static void *image_ptr(PdockerVkImage *image,
                       uint32_t mip_level,
                       uint32_t array_layer,
                       VkOffset3D image_offset,
                       VkDeviceSize bytes) {
    if (!image || !image->memory) return NULL;
    uint64_t relative = 0;
    if (!image_tight_subresource_offset(image, mip_level, array_layer, image_offset, &relative)) {
        return NULL;
    }
    uint64_t absolute = 0;
    if (!checked_add_u64((uint64_t)image->memory_offset, relative, &absolute) ||
        absolute > (uint64_t)image->memory->size ||
        (uint64_t)bytes > (uint64_t)image->memory->size - absolute) {
        return NULL;
    }
    return (char *)image->memory->map + absolute;
}

static bool ranges_overlap(VkDeviceSize a_offset, VkDeviceSize a_size,
                           VkDeviceSize b_offset, VkDeviceSize b_size) {
    if (a_size == 0 || b_size == 0) return false;
    return a_offset < b_offset + b_size && b_offset < a_offset + a_size;
}

static bool resolve_copy_alias(PdockerVkBuffer *buffer,
                               VkDeviceSize offset,
                               VkDeviceSize size,
                               PdockerVkMemory **src_memory,
                               VkDeviceSize *src_offset) {
    if (!buffer || !src_memory || !src_offset) return false;
    for (uint32_t i = 0; i < buffer->alias_count; ++i) {
        PdockerVkCopyAlias *alias = &buffer->aliases[i];
        if (!alias->valid || !alias->src_memory) continue;
        if (offset < alias->dst_offset) continue;
        VkDeviceSize delta = offset - alias->dst_offset;
        if (delta > alias->size || size > alias->size - delta) continue;
        *src_memory = alias->src_memory;
        *src_offset = alias->src_offset + delta;
        return true;
    }
    return false;
}

static void invalidate_copy_aliases(PdockerVkBuffer *buffer,
                                    VkDeviceSize offset,
                                    VkDeviceSize size) {
    if (!buffer) return;
    uint32_t out = 0;
    for (uint32_t i = 0; i < buffer->alias_count; ++i) {
        PdockerVkCopyAlias alias = buffer->aliases[i];
        if (!alias.valid || ranges_overlap(offset, size, alias.dst_offset, alias.size)) {
            continue;
        }
        buffer->aliases[out++] = alias;
    }
    buffer->alias_count = out;
}

static void add_copy_alias(PdockerVkBuffer *dst,
                           VkDeviceSize dst_offset,
                           VkDeviceSize size,
                           PdockerVkMemory *src_memory,
                           VkDeviceSize src_offset) {
    if (!dst || !src_memory || size == 0) return;
    invalidate_copy_aliases(dst, dst_offset, size);
    if (dst->alias_count >= PDOCKER_VK_MAX_COPY_ALIASES) {
        memmove(&dst->aliases[0], &dst->aliases[1],
                sizeof(dst->aliases[0]) * (PDOCKER_VK_MAX_COPY_ALIASES - 1));
        dst->alias_count = PDOCKER_VK_MAX_COPY_ALIASES - 1;
    }
    PdockerVkCopyAlias *alias = &dst->aliases[dst->alias_count++];
    alias->valid = true;
    alias->src_memory = src_memory;
    alias->src_offset = src_offset;
    alias->dst_offset = dst_offset;
    alias->size = size;
}

static bool copy_alias_candidate(PdockerVkMemory *src_memory) {
    return copy_alias_enabled() && src_memory &&
           src_memory->size >= PDOCKER_VK_ALIAS_MIN_SOURCE_BYTES;
}

typedef struct {
    size_t op_count;
    size_t alias_ops;
    size_t memmove_ops;
    size_t skipped_ops;
    VkDeviceSize alias_bytes;
    VkDeviceSize memmove_bytes;
    VkDeviceSize skipped_bytes;
} PdockerVkCopyStats;

static void execute_recorded_copy_op(PdockerVkCopyOp *op, PdockerVkCopyStats *stats) {
    if (!op) return;
    if (stats) stats->op_count++;
    void *dst_ptr = buffer_ptr(op->dst, op->region.dstOffset, op->region.size);
    void *src_ptr = buffer_ptr(op->src, op->region.srcOffset, op->region.size);
    if (!src_ptr || !dst_ptr) {
        if (stats) {
            stats->skipped_ops++;
            stats->skipped_bytes += op->region.size;
        }
        return;
    }
    PdockerVkMemory *alias_memory = op->src->memory;
    VkDeviceSize alias_offset = op->src->memory_offset + op->region.srcOffset;
    (void)resolve_copy_alias(op->src, op->region.srcOffset, op->region.size,
                             &alias_memory, &alias_offset);
    if (copy_alias_candidate(alias_memory)) {
        add_copy_alias(op->dst, op->region.dstOffset, op->region.size,
                       alias_memory, alias_offset);
        if (stats) {
            stats->alias_ops++;
            stats->alias_bytes += op->region.size;
        }
        if (trace_allocations()) {
            fprintf(stderr,
                    "pdocker-vulkan-icd: copy-alias dst_size=%zu dst_off=%llu src_mem=%zu src_off=%llu bytes=%llu\n",
                    op->dst->size,
                    (unsigned long long)op->region.dstOffset,
                    alias_memory ? alias_memory->size : 0,
                    (unsigned long long)alias_offset,
                    (unsigned long long)op->region.size);
        }
        return;
    }
    invalidate_copy_aliases(op->dst, op->region.dstOffset, op->region.size);
    memmove(dst_ptr, src_ptr, (size_t)op->region.size);
    if (stats) {
        stats->memmove_ops++;
        stats->memmove_bytes += op->region.size;
    }
}

static void execute_recorded_image_copy_op(PdockerVkImageCopyOp *op, PdockerVkCopyStats *stats) {
    if (!op || !op->buffer || !op->image || !op->buffer->memory ||
        !op->image->memory ||
        op->region.imageSubresource.aspectMask != VK_IMAGE_ASPECT_COLOR_BIT ||
        op->region.imageSubresource.layerCount == 0 ||
        op->region.imageExtent.width == 0 ||
        op->region.imageExtent.height == 0 ||
        op->region.imageExtent.depth == 0) {
        if (stats) stats->skipped_ops++;
        return;
    }
    trace_image_layout_mismatch(op->direction == PDOCKER_VK_IMAGE_COPY_BUFFER_TO_IMAGE
                                    ? "copy-buffer-to-image"
                                    : "copy-image-to-buffer",
                                op->image,
                                op->image_layout);
    const uint64_t bpp = conservative_format_bytes_per_pixel(op->image->format);
    VkExtent3D mip_extent;
    if (!image_mip_extent(op->image, op->region.imageSubresource.mipLevel, &mip_extent)) {
        if (stats) stats->skipped_ops++;
        return;
    }
    const uint64_t buffer_row_texels =
        op->region.bufferRowLength ? op->region.bufferRowLength : op->region.imageExtent.width;
    const uint64_t buffer_image_rows =
        op->region.bufferImageHeight ? op->region.bufferImageHeight : op->region.imageExtent.height;
    uint64_t buffer_row_bytes = 0;
    uint64_t buffer_slice_bytes = 0;
    if (!checked_mul_u64(buffer_row_texels, bpp, &buffer_row_bytes) ||
        !checked_mul_u64(buffer_row_bytes, buffer_image_rows, &buffer_slice_bytes)) {
        if (stats) stats->skipped_ops++;
        return;
    }
    for (uint32_t layer = 0; layer < op->region.imageSubresource.layerCount; ++layer) {
        uint32_t array_layer = op->region.imageSubresource.baseArrayLayer + layer;
        for (uint32_t z = 0; z < op->region.imageExtent.depth; ++z) {
            for (uint32_t y = 0; y < op->region.imageExtent.height; ++y) {
                VkOffset3D image_offset = {
                    .x = op->region.imageOffset.x,
                    .y = op->region.imageOffset.y + (int32_t)y,
                    .z = op->region.imageOffset.z + (int32_t)z,
                };
                if ((uint32_t)image_offset.x + op->region.imageExtent.width > mip_extent.width ||
                    (uint32_t)image_offset.y > mip_extent.height ||
                    (uint32_t)image_offset.z > mip_extent.depth) {
                    if (stats) stats->skipped_ops++;
                    return;
                }
                uint64_t buffer_offset = op->region.bufferOffset;
                uint64_t layer_bytes = 0;
                uint64_t z_bytes = 0;
                uint64_t y_bytes = 0;
                uint64_t row_copy_bytes = 0;
                if (!checked_mul_u64((uint64_t)layer, buffer_slice_bytes, &layer_bytes) ||
                    !checked_mul_u64((uint64_t)z, buffer_slice_bytes, &z_bytes) ||
                    !checked_mul_u64((uint64_t)y, buffer_row_bytes, &y_bytes) ||
                    !checked_mul_u64(op->region.imageExtent.width, bpp, &row_copy_bytes) ||
                    !checked_add_u64(buffer_offset, layer_bytes, &buffer_offset) ||
                    !checked_add_u64(buffer_offset, z_bytes, &buffer_offset) ||
                    !checked_add_u64(buffer_offset, y_bytes, &buffer_offset)) {
                    if (stats) stats->skipped_ops++;
                    return;
                }
                void *buffer_row = buffer_ptr(op->buffer,
                                              (VkDeviceSize)buffer_offset,
                                              (VkDeviceSize)row_copy_bytes);
                void *image_row = image_ptr(op->image,
                                            op->region.imageSubresource.mipLevel,
                                            array_layer,
                                            image_offset,
                                            (VkDeviceSize)row_copy_bytes);
                if (!buffer_row || !image_row) {
                    if (stats) stats->skipped_ops++;
                    return;
                }
                if (op->direction == PDOCKER_VK_IMAGE_COPY_BUFFER_TO_IMAGE) {
                    memmove(image_row, buffer_row, (size_t)row_copy_bytes);
                } else {
                    memmove(buffer_row, image_row, (size_t)row_copy_bytes);
                }
                if (stats) {
                    stats->op_count++;
                    stats->memmove_ops++;
                    stats->memmove_bytes += (VkDeviceSize)row_copy_bytes;
                }
            }
        }
    }
}

static void execute_recorded_image_to_image_copy_op(
        PdockerVkImageToImageCopyOp *op,
        PdockerVkCopyStats *stats) {
    if (!op || !op->src || !op->dst || !op->src->memory || !op->dst->memory ||
        op->region.srcSubresource.aspectMask != VK_IMAGE_ASPECT_COLOR_BIT ||
        op->region.dstSubresource.aspectMask != VK_IMAGE_ASPECT_COLOR_BIT ||
        op->region.srcSubresource.layerCount == 0 ||
        op->region.srcSubresource.layerCount != op->region.dstSubresource.layerCount ||
        op->region.extent.width == 0 ||
        op->region.extent.height == 0 ||
        op->region.extent.depth == 0) {
        if (stats) stats->skipped_ops++;
        return;
    }
    trace_image_layout_mismatch("copy-image-src", op->src, op->src_layout);
    trace_image_layout_mismatch("copy-image-dst", op->dst, op->dst_layout);
    const uint64_t src_bpp = conservative_format_bytes_per_pixel(op->src->format);
    const uint64_t dst_bpp = conservative_format_bytes_per_pixel(op->dst->format);
    if (src_bpp != dst_bpp) {
        if (stats) stats->skipped_ops++;
        return;
    }
    VkExtent3D src_extent;
    VkExtent3D dst_extent;
    if (!image_mip_extent(op->src, op->region.srcSubresource.mipLevel, &src_extent) ||
        !image_mip_extent(op->dst, op->region.dstSubresource.mipLevel, &dst_extent)) {
        if (stats) stats->skipped_ops++;
        return;
    }
    for (uint32_t layer = 0; layer < op->region.srcSubresource.layerCount; ++layer) {
        uint32_t src_layer = op->region.srcSubresource.baseArrayLayer + layer;
        uint32_t dst_layer = op->region.dstSubresource.baseArrayLayer + layer;
        for (uint32_t z = 0; z < op->region.extent.depth; ++z) {
            for (uint32_t y = 0; y < op->region.extent.height; ++y) {
                VkOffset3D src_offset = {
                    .x = op->region.srcOffset.x,
                    .y = op->region.srcOffset.y + (int32_t)y,
                    .z = op->region.srcOffset.z + (int32_t)z,
                };
                VkOffset3D dst_offset = {
                    .x = op->region.dstOffset.x,
                    .y = op->region.dstOffset.y + (int32_t)y,
                    .z = op->region.dstOffset.z + (int32_t)z,
                };
                if (src_offset.x < 0 || dst_offset.x < 0 ||
                    src_offset.y < 0 || src_offset.z < 0 ||
                    dst_offset.y < 0 || dst_offset.z < 0 ||
                    (uint32_t)src_offset.x + op->region.extent.width > src_extent.width ||
                    (uint32_t)src_offset.y >= src_extent.height ||
                    (uint32_t)src_offset.z >= src_extent.depth ||
                    (uint32_t)dst_offset.x + op->region.extent.width > dst_extent.width ||
                    (uint32_t)dst_offset.y >= dst_extent.height ||
                    (uint32_t)dst_offset.z >= dst_extent.depth) {
                    if (stats) stats->skipped_ops++;
                    return;
                }
                uint64_t row_copy_bytes = 0;
                if (!checked_mul_u64(op->region.extent.width, src_bpp, &row_copy_bytes)) {
                    if (stats) stats->skipped_ops++;
                    return;
                }
                void *src_row = image_ptr(op->src,
                                          op->region.srcSubresource.mipLevel,
                                          src_layer,
                                          src_offset,
                                          (VkDeviceSize)row_copy_bytes);
                void *dst_row = image_ptr(op->dst,
                                          op->region.dstSubresource.mipLevel,
                                          dst_layer,
                                          dst_offset,
                                          (VkDeviceSize)row_copy_bytes);
                if (!src_row || !dst_row) {
                    if (stats) stats->skipped_ops++;
                    return;
                }
                memmove(dst_row, src_row, (size_t)row_copy_bytes);
                if (stats) {
                    stats->op_count++;
                    stats->memmove_ops++;
                    stats->memmove_bytes += (VkDeviceSize)row_copy_bytes;
                }
            }
        }
    }
}

static bool resolve_image_subresource_range(
        const PdockerVkImage *image,
        const VkImageSubresourceRange *range,
        uint32_t *first_mip,
        uint32_t *mip_count,
        uint32_t *first_layer,
        uint32_t *layer_count) {
    if (!image || !range || !first_mip || !mip_count || !first_layer || !layer_count ||
        range->aspectMask != VK_IMAGE_ASPECT_COLOR_BIT ||
        range->baseMipLevel >= image->mip_levels ||
        range->baseArrayLayer >= image->array_layers) {
        return false;
    }
    uint32_t resolved_mips = range->levelCount == VK_REMAINING_MIP_LEVELS
        ? image->mip_levels - range->baseMipLevel
        : range->levelCount;
    uint32_t resolved_layers = range->layerCount == VK_REMAINING_ARRAY_LAYERS
        ? image->array_layers - range->baseArrayLayer
        : range->layerCount;
    if (resolved_mips == 0 || resolved_layers == 0 ||
        resolved_mips > image->mip_levels - range->baseMipLevel ||
        resolved_layers > image->array_layers - range->baseArrayLayer) {
        return false;
    }
    *first_mip = range->baseMipLevel;
    *mip_count = resolved_mips;
    *first_layer = range->baseArrayLayer;
    *layer_count = resolved_layers;
    return true;
}

static void execute_recorded_clear_color_image_op(
        PdockerVkImageClearOp *op,
        PdockerVkCopyStats *stats) {
    if (!op || !op->image || !op->image->memory) {
        if (stats) stats->skipped_ops++;
        return;
    }
    trace_image_layout_mismatch("clear-color-image", op->image, op->image_layout);
    uint32_t first_mip = 0;
    uint32_t mip_count = 0;
    uint32_t first_layer = 0;
    uint32_t layer_count = 0;
    if (!resolve_image_subresource_range(op->image,
                                         &op->range,
                                         &first_mip,
                                         &mip_count,
                                         &first_layer,
                                         &layer_count)) {
        if (stats) stats->skipped_ops++;
        return;
    }
    const uint64_t bpp = conservative_format_bytes_per_pixel(op->image->format);
    if (bpp == 0 || bpp > 16) {
        if (stats) stats->skipped_ops++;
        return;
    }
    uint8_t pixel[16];
    encode_clear_color_pixel(op->image->format, &op->color, pixel, (size_t)bpp);
    for (uint32_t mip_i = 0; mip_i < mip_count; ++mip_i) {
        const uint32_t mip = first_mip + mip_i;
        VkExtent3D extent;
        if (!image_mip_extent(op->image, mip, &extent)) {
            if (stats) stats->skipped_ops++;
            return;
        }
        uint64_t row_bytes = 0;
        if (!checked_mul_u64(extent.width, bpp, &row_bytes)) {
            if (stats) stats->skipped_ops++;
            return;
        }
        for (uint32_t layer_i = 0; layer_i < layer_count; ++layer_i) {
            const uint32_t layer = first_layer + layer_i;
            for (uint32_t z = 0; z < extent.depth; ++z) {
                for (uint32_t y = 0; y < extent.height; ++y) {
                    void *row = image_ptr(op->image,
                                          mip,
                                          layer,
                                          (VkOffset3D){0, (int32_t)y, (int32_t)z},
                                          (VkDeviceSize)row_bytes);
                    if (!row) {
                        if (stats) stats->skipped_ops++;
                        return;
                    }
                    uint8_t *dst = (uint8_t *)row;
                    for (uint32_t x = 0; x < extent.width; ++x) {
                        memcpy(dst + (uint64_t)x * bpp, pixel, (size_t)bpp);
                    }
                    if (stats) {
                        stats->op_count++;
                        stats->memmove_ops++;
                        stats->memmove_bytes += (VkDeviceSize)row_bytes;
                    }
                }
            }
        }
    }
}

static void execute_recorded_resolve_image_op(
        PdockerVkImageResolveOp *op,
        PdockerVkCopyStats *stats) {
    if (!op || !op->src || !op->dst || !op->src->memory || !op->dst->memory ||
        op->region.srcSubresource.aspectMask != VK_IMAGE_ASPECT_COLOR_BIT ||
        op->region.dstSubresource.aspectMask != VK_IMAGE_ASPECT_COLOR_BIT ||
        op->region.srcSubresource.layerCount == 0 ||
        op->region.srcSubresource.layerCount != op->region.dstSubresource.layerCount ||
        op->region.extent.width == 0 ||
        op->region.extent.height == 0 ||
        op->region.extent.depth == 0) {
        if (stats) stats->skipped_ops++;
        return;
    }
    trace_image_layout_mismatch("resolve-image-src", op->src, op->src_layout);
    trace_image_layout_mismatch("resolve-image-dst", op->dst, op->dst_layout);
    const uint64_t src_bpp = conservative_format_bytes_per_pixel(op->src->format);
    const uint64_t dst_bpp = conservative_format_bytes_per_pixel(op->dst->format);
    if (src_bpp != dst_bpp) {
        if (stats) stats->skipped_ops++;
        return;
    }
    VkExtent3D src_extent;
    VkExtent3D dst_extent;
    if (!image_mip_extent(op->src, op->region.srcSubresource.mipLevel, &src_extent) ||
        !image_mip_extent(op->dst, op->region.dstSubresource.mipLevel, &dst_extent)) {
        if (stats) stats->skipped_ops++;
        return;
    }
    uint64_t row_copy_bytes = 0;
    if (!checked_mul_u64(op->region.extent.width, src_bpp, &row_copy_bytes)) {
        if (stats) stats->skipped_ops++;
        return;
    }
    for (uint32_t layer = 0; layer < op->region.srcSubresource.layerCount; ++layer) {
        uint32_t src_layer = op->region.srcSubresource.baseArrayLayer + layer;
        uint32_t dst_layer = op->region.dstSubresource.baseArrayLayer + layer;
        for (uint32_t z = 0; z < op->region.extent.depth; ++z) {
            for (uint32_t y = 0; y < op->region.extent.height; ++y) {
                VkOffset3D src_offset = {
                    .x = op->region.srcOffset.x,
                    .y = op->region.srcOffset.y + (int32_t)y,
                    .z = op->region.srcOffset.z + (int32_t)z,
                };
                VkOffset3D dst_offset = {
                    .x = op->region.dstOffset.x,
                    .y = op->region.dstOffset.y + (int32_t)y,
                    .z = op->region.dstOffset.z + (int32_t)z,
                };
                if (src_offset.x < 0 || src_offset.y < 0 || src_offset.z < 0 ||
                    dst_offset.x < 0 || dst_offset.y < 0 || dst_offset.z < 0 ||
                    (uint32_t)src_offset.x + op->region.extent.width > src_extent.width ||
                    (uint32_t)src_offset.y >= src_extent.height ||
                    (uint32_t)src_offset.z >= src_extent.depth ||
                    (uint32_t)dst_offset.x + op->region.extent.width > dst_extent.width ||
                    (uint32_t)dst_offset.y >= dst_extent.height ||
                    (uint32_t)dst_offset.z >= dst_extent.depth) {
                    if (stats) stats->skipped_ops++;
                    return;
                }
                void *src_row = image_ptr(op->src,
                                          op->region.srcSubresource.mipLevel,
                                          src_layer,
                                          src_offset,
                                          (VkDeviceSize)row_copy_bytes);
                void *dst_row = image_ptr(op->dst,
                                          op->region.dstSubresource.mipLevel,
                                          dst_layer,
                                          dst_offset,
                                          (VkDeviceSize)row_copy_bytes);
                if (!src_row || !dst_row) {
                    if (stats) stats->skipped_ops++;
                    return;
                }
                memmove(dst_row, src_row, (size_t)row_copy_bytes);
                if (stats) {
                    stats->op_count++;
                    stats->memmove_ops++;
                    stats->memmove_bytes += (VkDeviceSize)row_copy_bytes;
                }
            }
        }
    }
}

static uint32_t blit_axis_extent(int32_t a, int32_t b) {
    int64_t delta = (int64_t)b - (int64_t)a;
    return (uint32_t)(delta < 0 ? -delta : delta);
}

static int32_t blit_axis_sample(int32_t src0,
                                int32_t src1,
                                uint32_t dst_index,
                                uint32_t dst_extent) {
    if (dst_extent == 0) return src0;
    int64_t span = (int64_t)src1 - (int64_t)src0;
    int64_t sampled = span >= 0
        ? (int64_t)src0 + ((int64_t)dst_index * span) / (int64_t)dst_extent
        : (int64_t)src0 - 1 - ((int64_t)dst_index * -span) / (int64_t)dst_extent;
    return (int32_t)sampled;
}

static void execute_recorded_blit_image_op(
        PdockerVkImageBlitOp *op,
        PdockerVkCopyStats *stats) {
    if (!op || !op->src || !op->dst || !op->src->memory || !op->dst->memory ||
        op->region.srcSubresource.aspectMask != VK_IMAGE_ASPECT_COLOR_BIT ||
        op->region.dstSubresource.aspectMask != VK_IMAGE_ASPECT_COLOR_BIT ||
        op->region.srcSubresource.layerCount == 0 ||
        op->region.srcSubresource.layerCount != op->region.dstSubresource.layerCount) {
        if (stats) stats->skipped_ops++;
        return;
    }
    trace_image_layout_mismatch("blit-image-src", op->src, op->src_layout);
    trace_image_layout_mismatch("blit-image-dst", op->dst, op->dst_layout);
    const uint64_t src_bpp = conservative_format_bytes_per_pixel(op->src->format);
    const uint64_t dst_bpp = conservative_format_bytes_per_pixel(op->dst->format);
    if (src_bpp == 0 || src_bpp != dst_bpp || src_bpp > 16) {
        if (stats) stats->skipped_ops++;
        return;
    }
    VkExtent3D src_extent;
    VkExtent3D dst_extent;
    if (!image_mip_extent(op->src, op->region.srcSubresource.mipLevel, &src_extent) ||
        !image_mip_extent(op->dst, op->region.dstSubresource.mipLevel, &dst_extent)) {
        if (stats) stats->skipped_ops++;
        return;
    }
    const uint32_t out_w = blit_axis_extent(op->region.dstOffsets[0].x, op->region.dstOffsets[1].x);
    const uint32_t out_h = blit_axis_extent(op->region.dstOffsets[0].y, op->region.dstOffsets[1].y);
    const uint32_t out_d = blit_axis_extent(op->region.dstOffsets[0].z, op->region.dstOffsets[1].z);
    const uint32_t in_w = blit_axis_extent(op->region.srcOffsets[0].x, op->region.srcOffsets[1].x);
    const uint32_t in_h = blit_axis_extent(op->region.srcOffsets[0].y, op->region.srcOffsets[1].y);
    const uint32_t in_d = blit_axis_extent(op->region.srcOffsets[0].z, op->region.srcOffsets[1].z);
    if (out_w == 0 || out_h == 0 || out_d == 0 || in_w == 0 || in_h == 0 || in_d == 0) {
        if (stats) stats->skipped_ops++;
        return;
    }
    uint8_t pixel[16];
    for (uint32_t layer = 0; layer < op->region.srcSubresource.layerCount; ++layer) {
        uint32_t src_layer = op->region.srcSubresource.baseArrayLayer + layer;
        uint32_t dst_layer = op->region.dstSubresource.baseArrayLayer + layer;
        for (uint32_t z = 0; z < out_d; ++z) {
            for (uint32_t y = 0; y < out_h; ++y) {
                for (uint32_t x = 0; x < out_w; ++x) {
                    VkOffset3D src_offset = {
                        .x = blit_axis_sample(op->region.srcOffsets[0].x, op->region.srcOffsets[1].x, x, out_w),
                        .y = blit_axis_sample(op->region.srcOffsets[0].y, op->region.srcOffsets[1].y, y, out_h),
                        .z = blit_axis_sample(op->region.srcOffsets[0].z, op->region.srcOffsets[1].z, z, out_d),
                    };
                    VkOffset3D dst_offset = {
                        .x = blit_axis_sample(op->region.dstOffsets[0].x, op->region.dstOffsets[1].x, x, out_w),
                        .y = blit_axis_sample(op->region.dstOffsets[0].y, op->region.dstOffsets[1].y, y, out_h),
                        .z = blit_axis_sample(op->region.dstOffsets[0].z, op->region.dstOffsets[1].z, z, out_d),
                    };
                    if (src_offset.x < 0 || src_offset.y < 0 || src_offset.z < 0 ||
                        dst_offset.x < 0 || dst_offset.y < 0 || dst_offset.z < 0 ||
                        (uint32_t)src_offset.x >= src_extent.width ||
                        (uint32_t)src_offset.y >= src_extent.height ||
                        (uint32_t)src_offset.z >= src_extent.depth ||
                        (uint32_t)dst_offset.x >= dst_extent.width ||
                        (uint32_t)dst_offset.y >= dst_extent.height ||
                        (uint32_t)dst_offset.z >= dst_extent.depth) {
                        if (stats) stats->skipped_ops++;
                        return;
                    }
                    void *src_pixel = image_ptr(op->src,
                                                op->region.srcSubresource.mipLevel,
                                                src_layer,
                                                src_offset,
                                                (VkDeviceSize)src_bpp);
                    void *dst_pixel = image_ptr(op->dst,
                                                op->region.dstSubresource.mipLevel,
                                                dst_layer,
                                                dst_offset,
                                                (VkDeviceSize)dst_bpp);
                    if (!src_pixel || !dst_pixel) {
                        if (stats) stats->skipped_ops++;
                        return;
                    }
                    memcpy(pixel, src_pixel, (size_t)src_bpp);
                    memcpy(dst_pixel, pixel, (size_t)dst_bpp);
                    if (stats) {
                        stats->op_count++;
                        stats->memmove_ops++;
                        stats->memmove_bytes += (VkDeviceSize)dst_bpp;
                    }
                }
            }
        }
    }
}

static uint16_t clear_depth_unorm16(float v) {
    if (v <= 0.0f) return 0;
    if (v >= 1.0f) return 65535u;
    return (uint16_t)(v * 65535.0f + 0.5f);
}

static uint32_t clear_depth_unorm24(float v) {
    if (v <= 0.0f) return 0;
    if (v >= 1.0f) return 0x00ffffffu;
    return (uint32_t)(v * 16777215.0f + 0.5f) & 0x00ffffffu;
}

static bool encode_clear_depth_stencil_pixel(
        VkFormat format,
        VkImageAspectFlags aspect_mask,
        const VkClearDepthStencilValue *value,
        uint8_t *pixel,
        size_t pixel_size) {
    if (!value || !pixel) return false;
    switch (format) {
        case VK_FORMAT_D16_UNORM:
            if (!(aspect_mask & VK_IMAGE_ASPECT_DEPTH_BIT) || pixel_size < 2) return false;
            {
                uint16_t depth = clear_depth_unorm16(value->depth);
                memcpy(pixel, &depth, sizeof(depth));
            }
            return true;
        case VK_FORMAT_D32_SFLOAT:
            if (!(aspect_mask & VK_IMAGE_ASPECT_DEPTH_BIT) || pixel_size < 4) return false;
            memcpy(pixel, &value->depth, sizeof(value->depth));
            return true;
        case VK_FORMAT_S8_UINT:
            if (!(aspect_mask & VK_IMAGE_ASPECT_STENCIL_BIT) || pixel_size < 1) return false;
            pixel[0] = (uint8_t)value->stencil;
            return true;
        case VK_FORMAT_D24_UNORM_S8_UINT:
            if (pixel_size < 4) return false;
            {
                uint32_t packed = 0;
                memcpy(&packed, pixel, sizeof(packed));
                if (aspect_mask & VK_IMAGE_ASPECT_DEPTH_BIT) {
                    packed = (packed & 0xff000000u) | clear_depth_unorm24(value->depth);
                }
                if (aspect_mask & VK_IMAGE_ASPECT_STENCIL_BIT) {
                    packed = (packed & 0x00ffffffu) | ((uint32_t)(uint8_t)value->stencil << 24);
                }
                memcpy(pixel, &packed, sizeof(packed));
            }
            return true;
        case VK_FORMAT_D32_SFLOAT_S8_UINT:
            if (pixel_size < 8) return false;
            if (aspect_mask & VK_IMAGE_ASPECT_DEPTH_BIT) {
                memcpy(pixel, &value->depth, sizeof(value->depth));
            }
            if (aspect_mask & VK_IMAGE_ASPECT_STENCIL_BIT) {
                pixel[4] = (uint8_t)value->stencil;
            }
            return true;
        default:
            return false;
    }
}

static void execute_recorded_clear_depth_stencil_image_op(
        PdockerVkDepthStencilClearOp *op,
        PdockerVkCopyStats *stats) {
    if (!op || !op->image || !op->image->memory ||
        !(op->range.aspectMask & (VK_IMAGE_ASPECT_DEPTH_BIT | VK_IMAGE_ASPECT_STENCIL_BIT)) ||
        op->range.baseMipLevel >= op->image->mip_levels ||
        op->range.baseArrayLayer >= op->image->array_layers) {
        if (stats) stats->skipped_ops++;
        return;
    }
    trace_image_layout_mismatch("clear-depth-stencil-image", op->image, op->image_layout);
    uint32_t mip_count = op->range.levelCount == VK_REMAINING_MIP_LEVELS
        ? op->image->mip_levels - op->range.baseMipLevel
        : op->range.levelCount;
    uint32_t layer_count = op->range.layerCount == VK_REMAINING_ARRAY_LAYERS
        ? op->image->array_layers - op->range.baseArrayLayer
        : op->range.layerCount;
    if (mip_count == 0 || layer_count == 0 ||
        mip_count > op->image->mip_levels - op->range.baseMipLevel ||
        layer_count > op->image->array_layers - op->range.baseArrayLayer) {
        if (stats) stats->skipped_ops++;
        return;
    }
    const uint64_t bpp = conservative_format_bytes_per_pixel(op->image->format);
    if (bpp == 0 || bpp > 16) {
        if (stats) stats->skipped_ops++;
        return;
    }
    uint8_t pixel[16];
    for (uint32_t mip_i = 0; mip_i < mip_count; ++mip_i) {
        const uint32_t mip = op->range.baseMipLevel + mip_i;
        VkExtent3D extent;
        if (!image_mip_extent(op->image, mip, &extent)) {
            if (stats) stats->skipped_ops++;
            return;
        }
        for (uint32_t layer_i = 0; layer_i < layer_count; ++layer_i) {
            const uint32_t layer = op->range.baseArrayLayer + layer_i;
            for (uint32_t z = 0; z < extent.depth; ++z) {
                for (uint32_t y = 0; y < extent.height; ++y) {
                    for (uint32_t x = 0; x < extent.width; ++x) {
                        void *dst_pixel = image_ptr(op->image,
                                                    mip,
                                                    layer,
                                                    (VkOffset3D){(int32_t)x, (int32_t)y, (int32_t)z},
                                                    (VkDeviceSize)bpp);
                        if (!dst_pixel) {
                            if (stats) stats->skipped_ops++;
                            return;
                        }
                        memcpy(pixel, dst_pixel, (size_t)bpp);
                        if (!encode_clear_depth_stencil_pixel(op->image->format,
                                                              op->range.aspectMask,
                                                              &op->value,
                                                              pixel,
                                                              (size_t)bpp)) {
                            if (stats) stats->skipped_ops++;
                            return;
                        }
                        memcpy(dst_pixel, pixel, (size_t)bpp);
                        if (stats) {
                            stats->op_count++;
                            stats->memmove_ops++;
                            stats->memmove_bytes += (VkDeviceSize)bpp;
                        }
                    }
                }
            }
        }
    }
}

static void execute_recorded_fill_op(const PdockerVkCommandOp *op) {
    if (!op || !op->buffer || !op->buffer->memory || op->size == 0) return;
    uint32_t *p = (uint32_t *)buffer_ptr(op->buffer, op->offset, op->size);
    if (!p) return;
    invalidate_copy_aliases(op->buffer, op->offset, op->size);
    for (size_t i = 0; i < op->size / sizeof(uint32_t); ++i) p[i] = op->data;
}

static void execute_recorded_update_op(const PdockerVkCommandOp *op) {
    if (!op || !op->buffer || !op->buffer->memory || !op->payload || op->size == 0) return;
    void *dst_ptr = buffer_ptr(op->buffer, op->offset, op->size);
    if (!dst_ptr) return;
    invalidate_copy_aliases(op->buffer, op->offset, op->size);
    memcpy(dst_ptr, op->payload, (size_t)op->size);
}

static void execute_recorded_copy_ops(PdockerVkCommandBuffer *cmd) {
    if (!cmd) return;
    PdockerVkCopyStats stats;
    memset(&stats, 0, sizeof(stats));
    for (uint32_t i = 0; i < cmd->copy_op_count; ++i) {
        execute_recorded_copy_op(&cmd->copy_ops[i], &stats);
    }
    if (trace_allocations() && stats.op_count > 0) {
        fprintf(stderr,
                "pdocker-vulkan-icd: copy-submit summary ops=%zu alias_ops=%zu memmove_ops=%zu skipped_ops=%zu alias_bytes=%llu memmove_bytes=%llu skipped_bytes=%llu\n",
                stats.op_count,
                stats.alias_ops,
                stats.memmove_ops,
                stats.skipped_ops,
                (unsigned long long)stats.alias_bytes,
                (unsigned long long)stats.memmove_bytes,
                (unsigned long long)stats.skipped_bytes);
    }
}

static size_t descriptor_binding_size(const PdockerVkDescriptorBinding *binding) {
    if (!binding || !binding->buffer) return 0;
    /*
     * Vulkan descriptor ranges are scoped to the VkBuffer, not to the backing
     * VkDeviceMemory allocation.  llama.cpp suballocates several VkBuffers
     * from one large allocation; using buffer_available() here would expose the
     * allocation tail for VK_WHOLE_SIZE and can hand adjacent suballocations to
     * the Android executor.  That is silent data corruption, not just wasted IO.
     */
    if (binding->offset > binding->buffer->size) return 0;
    size_t available_in_buffer = binding->buffer->size - (size_t)binding->offset;
    if (binding->range == VK_WHOLE_SIZE) return available_in_buffer;
    return (size_t)binding->range < available_in_buffer
        ? (size_t)binding->range
        : available_in_buffer;
}

static int validate_descriptor_transport_shape(
        const PdockerVkDescriptorBinding *binding,
        uint32_t set_index,
        uint32_t binding_index,
        size_t *effective_size) {
    if (effective_size) *effective_size = 0;
    if (!binding || !binding->buffer || !binding->buffer->memory) return -EINVAL;

    const PdockerVkBuffer *buffer = binding->buffer;
    const PdockerVkMemory *memory = buffer->memory;
    if (memory->fd < 0) return -EINVAL;

    if (binding->offset > (VkDeviceSize)buffer->size) {
        fprintf(stderr,
                "pdocker-vulkan-icd: rejecting descriptor past buffer"
                " set=%u binding=%u offset=%llu buffer_size=%zu\n",
                set_index,
                binding_index,
                (unsigned long long)binding->offset,
                buffer->size);
        return -ERANGE;
    }

    const size_t available_in_buffer = buffer->size - (size_t)binding->offset;
    size_t bytes = available_in_buffer;
    if (binding->range != VK_WHOLE_SIZE) {
        if (binding->range > (VkDeviceSize)available_in_buffer) {
            fprintf(stderr,
                    "pdocker-vulkan-icd: rejecting descriptor range outside buffer"
                    " set=%u binding=%u offset=%llu range=%llu buffer_size=%zu\n",
                    set_index,
                    binding_index,
                    (unsigned long long)binding->offset,
                    (unsigned long long)binding->range,
                    buffer->size);
            return -ERANGE;
        }
        bytes = (size_t)binding->range;
    }
    if (bytes == 0) {
        fprintf(stderr,
                "pdocker-vulkan-icd: rejecting empty descriptor"
                " set=%u binding=%u offset=%llu range=%llu buffer_size=%zu\n",
                set_index,
                binding_index,
                (unsigned long long)binding->offset,
                (unsigned long long)binding->range,
                buffer->size);
        return -EINVAL;
    }

    if (buffer->memory_offset > (VkDeviceSize)memory->size ||
        binding->offset > (VkDeviceSize)memory->size - buffer->memory_offset ||
        (VkDeviceSize)bytes > (VkDeviceSize)memory->size - buffer->memory_offset - binding->offset) {
        fprintf(stderr,
                "pdocker-vulkan-icd: rejecting descriptor outside backing memory"
                " set=%u binding=%u memory_offset=%llu offset=%llu size=%zu memory_size=%zu\n",
                set_index,
                binding_index,
                (unsigned long long)buffer->memory_offset,
                (unsigned long long)binding->offset,
                bytes,
                memory->size);
        return -ERANGE;
    }

    if (effective_size) *effective_size = bytes;
    return 0;
}

static uint32_t pdocker_api_version(void) {
    return VK_API_VERSION_1_2;
}

static void copy_extension_properties(
        const VkExtensionProperties *available,
        uint32_t available_count,
        uint32_t *pPropertyCount,
        VkExtensionProperties *pProperties) {
    if (!pPropertyCount) return;
    if (!pProperties) {
        *pPropertyCount = available_count;
        return;
    }
    uint32_t count = *pPropertyCount < available_count ? *pPropertyCount : available_count;
    for (uint32_t i = 0; i < count; ++i) pProperties[i] = available[i];
    *pPropertyCount = count;
}

typedef struct {
    bool loaded;
    bool executor_valid;
    uint32_t api_version;
    uint32_t vendor_id;
    uint32_t device_id;
    VkPhysicalDeviceType device_type;
    char device_name[VK_MAX_PHYSICAL_DEVICE_NAME_SIZE];
    VkPhysicalDeviceLimits limits;
    VkPhysicalDeviceFeatures features;
    VkPhysicalDevice16BitStorageFeatures storage16;
    VkPhysicalDevice8BitStorageFeatures storage8;
    VkPhysicalDeviceShaderFloat16Int8Features float16_int8;
    VkPhysicalDeviceIndexTypeUint8FeaturesEXT index_type_uint8;
    VkPhysicalDeviceSubgroupProperties subgroup;
    bool ext_16bit_storage;
    bool ext_8bit_storage;
    bool ext_shader_float16_int8;
    bool ext_storage_buffer_storage_class;
    bool ext_extended_dynamic_state;
    bool ext_index_type_uint8;
} PdockerVkAdvertisedCaps;

static const char *json_find_value(const char *json, const char *key) {
    if (!json || !key || !key[0]) return NULL;
    char pattern[128];
    int n = snprintf(pattern, sizeof(pattern), "\"%s\":", key);
    if (n <= 0 || (size_t)n >= sizeof(pattern)) return NULL;
    const char *p = strstr(json, pattern);
    return p ? p + n : NULL;
}

static bool json_read_u32(const char *json, const char *key, uint32_t *out) {
    const char *p = json_find_value(json, key);
    if (!p || !out) return false;
    char *end = NULL;
    unsigned long value = strtoul(p, &end, 10);
    if (end == p || value > UINT32_MAX) return false;
    *out = (uint32_t)value;
    return true;
}

static bool json_read_u32_array3(const char *json, const char *key, uint32_t out[3]) {
    const char *p = json_find_value(json, key);
    if (!p || !out || *p != '[') return false;
    ++p;
    for (size_t i = 0; i < 3; ++i) {
        char *end = NULL;
        unsigned long value = strtoul(p, &end, 10);
        if (end == p || value > UINT32_MAX) return false;
        out[i] = (uint32_t)value;
        p = end;
        if (i < 2) {
            if (*p != ',') return false;
            ++p;
        }
    }
    return *p == ']';
}

static bool json_read_string(const char *json, const char *key, char *out, size_t out_cap) {
    const char *p = json_find_value(json, key);
    if (!p || !out || out_cap == 0 || *p != '"') return false;
    ++p;
    size_t off = 0;
    while (*p && *p != '"') {
        unsigned char ch = (unsigned char)*p++;
        if (ch == '\\' && *p) {
            ch = (unsigned char)*p++;
        }
        if (off + 1 < out_cap && ch >= 0x20 && ch < 0x7f) {
            out[off++] = (char)ch;
        }
    }
    if (*p != '"') return false;
    out[off] = '\0';
    return true;
}

static bool parse_executor_advertisement_caps_json(
        const char *json,
        PdockerVkAdvertisedCaps *caps) {
    if (!json || !caps ||
        strstr(json, "\"schema\":\"skydnir-vulkan-advertisement-caps-v1\"") == NULL) {
        return false;
    }
    memset(caps, 0, sizeof(*caps));
    caps->loaded = true;
    caps->executor_valid = true;
    uint32_t value = 0;
    if (json_read_u32(json, "apiVersion", &value)) caps->api_version = value;
    if (json_read_u32(json, "vendorID", &value)) caps->vendor_id = value;
    if (json_read_u32(json, "deviceID", &value)) caps->device_id = value;
    if (json_read_u32(json, "deviceType", &value)) caps->device_type = (VkPhysicalDeviceType)value;
    if (!json_read_string(json, "deviceName", caps->device_name, sizeof(caps->device_name))) {
        snprintf(caps->device_name, sizeof(caps->device_name), "executor Vulkan device");
    }

    json_read_u32(json, "maxPushConstantsSize", &caps->limits.maxPushConstantsSize);
    json_read_u32(json, "maxComputeSharedMemorySize", &caps->limits.maxComputeSharedMemorySize);
    json_read_u32(json, "maxPerStageDescriptorStorageBuffers", &caps->limits.maxPerStageDescriptorStorageBuffers);
    json_read_u32(json, "maxDescriptorSetStorageBuffers", &caps->limits.maxDescriptorSetStorageBuffers);
    json_read_u32(json, "maxBoundDescriptorSets", &caps->limits.maxBoundDescriptorSets);
    json_read_u32(json, "maxComputeWorkGroupInvocations", &caps->limits.maxComputeWorkGroupInvocations);
    json_read_u32(json, "maxStorageBufferRange", &caps->limits.maxStorageBufferRange);
    json_read_u32_array3(json, "maxComputeWorkGroupSize", caps->limits.maxComputeWorkGroupSize);
    json_read_u32_array3(json, "maxComputeWorkGroupCount", caps->limits.maxComputeWorkGroupCount);

    json_read_u32(json, "shaderInt64", &caps->features.shaderInt64);
    json_read_u32(json, "storageBuffer16BitAccess", &caps->storage16.storageBuffer16BitAccess);
    json_read_u32(json, "uniformAndStorageBuffer16BitAccess", &caps->storage16.uniformAndStorageBuffer16BitAccess);
    json_read_u32(json, "storagePushConstant16", &caps->storage16.storagePushConstant16);
    json_read_u32(json, "storageInputOutput16", &caps->storage16.storageInputOutput16);
    json_read_u32(json, "storageBuffer8BitAccess", &caps->storage8.storageBuffer8BitAccess);
    json_read_u32(json, "uniformAndStorageBuffer8BitAccess", &caps->storage8.uniformAndStorageBuffer8BitAccess);
    json_read_u32(json, "storagePushConstant8", &caps->storage8.storagePushConstant8);
    json_read_u32(json, "shaderFloat16", &caps->float16_int8.shaderFloat16);
    json_read_u32(json, "shaderInt8", &caps->float16_int8.shaderInt8);
    json_read_u32(json, "indexTypeUint8", &caps->index_type_uint8.indexTypeUint8);
    json_read_u32(json, "subgroupSize", &caps->subgroup.subgroupSize);
    json_read_u32(json, "supportedStages", &caps->subgroup.supportedStages);
    json_read_u32(json, "supportedOperations", &caps->subgroup.supportedOperations);

    if (json_read_u32(json, "VK_KHR_16bit_storage", &value)) caps->ext_16bit_storage = value != 0;
    if (json_read_u32(json, "VK_KHR_8bit_storage", &value)) caps->ext_8bit_storage = value != 0;
    if (json_read_u32(json, "VK_KHR_shader_float16_int8", &value)) caps->ext_shader_float16_int8 = value != 0;
    if (json_read_u32(json, "VK_KHR_storage_buffer_storage_class", &value)) caps->ext_storage_buffer_storage_class = value != 0;
    if (json_read_u32(json, "VK_EXT_extended_dynamic_state", &value)) caps->ext_extended_dynamic_state = value != 0;
    if (json_read_u32(json, "VK_EXT_index_type_uint8", &value)) caps->ext_index_type_uint8 = value != 0;
    return caps->api_version != 0;
}

static int query_executor_advertisement_caps_line(char *line, size_t line_cap) {
    if (!line || line_cap == 0) return -EINVAL;
    line[0] = '\0';
    int socket_fd = connect_queue();
    if (socket_fd < 0) return socket_fd;
    const char command[] = "VULKAN_ADVERTISEMENT_CAPS\n";
    ssize_t sent = write(socket_fd, command, sizeof(command) - 1);
    int rc = 0;
    if (sent < 0 || (size_t)sent != sizeof(command) - 1) {
        rc = sent < 0 ? -errno : -EIO;
    } else {
        size_t off = 0;
        while (off + 1 < line_cap) {
            char ch;
            ssize_t r = read(socket_fd, &ch, 1);
            if (r <= 0) break;
            line[off++] = ch;
            if (ch == '\n') break;
        }
        line[off] = '\0';
        if (off == 0) rc = -EIO;
        if (rc == 0 && strstr(line, "\"schema\":\"skydnir-vulkan-advertisement-caps-v1\"") == NULL) {
            rc = -EPROTO;
        }
    }
    close(socket_fd);
    return rc;
}

static const PdockerVkAdvertisedCaps *pdocker_vk_advertised_caps(void) {
    static PdockerVkAdvertisedCaps caps;
    static bool queried = false;
    if (queried) return &caps;
    queried = true;
    char line[8192];
    int rc = query_executor_advertisement_caps_line(line, sizeof(line));
    if (rc == 0 && parse_executor_advertisement_caps_json(line, &caps)) {
        caps.loaded = true;
        caps.executor_valid = true;
    } else {
        memset(&caps, 0, sizeof(caps));
        caps.loaded = true;
        caps.executor_valid = false;
    }
    return &caps;
}

static bool executor_advertisement_source_enabled(void) {
    const char *source = getenv("PDOCKER_VULKAN_ADVERTISEMENT_SOURCE");
    return source && strcmp(source, "executor") == 0;
}

static const PdockerVkAdvertisedCaps *executor_advertisement_caps_if_enabled(void) {
    if (!executor_advertisement_source_enabled()) return NULL;
    const PdockerVkAdvertisedCaps *caps = pdocker_vk_advertised_caps();
    return caps && caps->executor_valid ? caps : NULL;
}

static VkBool32 executor_advertised_shader_int64_or(VkBool32 legacy) {
    const PdockerVkAdvertisedCaps *caps = executor_advertisement_caps_if_enabled();
    if (!caps) return legacy;
    return caps->features.shaderInt64 ? VK_TRUE : VK_FALSE;
}

static VkBool32 executor_advertised_storage16_or(VkBool32 legacy) {
    const PdockerVkAdvertisedCaps *caps = executor_advertisement_caps_if_enabled();
    if (!caps) return legacy;
    if (env_disabled("PDOCKER_VULKAN_DISABLE_16BIT_STORAGE")) return VK_FALSE;
    return caps->storage16.storageBuffer16BitAccess ? VK_TRUE : VK_FALSE;
}

static VkBool32 executor_advertised_storage8_or(VkBool32 legacy) {
    const PdockerVkAdvertisedCaps *caps = executor_advertisement_caps_if_enabled();
    if (!caps) return legacy;
    if (env_disabled("PDOCKER_VULKAN_DISABLE_8BIT_STORAGE")) return VK_FALSE;
    return (caps->storage8.storageBuffer8BitAccess && caps->float16_int8.shaderInt8)
        ? VK_TRUE
        : VK_FALSE;
}

static void trace_executor_advertisement_caps_once(void) {
    static int traced = 0;
    if (traced || !getenv("PDOCKER_VULKAN_ICD_DEBUG")) return;
    traced = 1;
    const PdockerVkAdvertisedCaps *caps = pdocker_vk_advertised_caps();
    if (caps && caps->executor_valid) {
        fprintf(stderr,
                "pdocker-vulkan-icd: executor advertisement caps shadow: "
                "api=0x%08x device=\"%s\" vendor=0x%04x device_id=0x%04x "
                "type=%u storage16=%u storage8=%u int8=%u indexTypeUint8=%u subgroup={size:%u,ops:0x%x}\n",
                caps->api_version,
                caps->device_name,
                caps->vendor_id,
                caps->device_id,
                caps->device_type,
                caps->storage16.storageBuffer16BitAccess,
                caps->storage8.storageBuffer8BitAccess,
                caps->float16_int8.shaderInt8,
                caps->index_type_uint8.indexTypeUint8,
                caps->subgroup.subgroupSize,
                caps->subgroup.supportedOperations);
    } else {
        fprintf(stderr,
                "pdocker-vulkan-icd: executor advertisement caps shadow unavailable\n");
    }
}

static void fill_physical_device_properties(VkPhysicalDeviceProperties *pProperties) {
    if (!pProperties) return;
    trace_executor_advertisement_caps_once();
    const PdockerVkAdvertisedCaps *caps = executor_advertisement_caps_if_enabled();
    memset(pProperties, 0, sizeof(*pProperties));
    pProperties->apiVersion = caps && caps->api_version ? caps->api_version : pdocker_api_version();
    if (pProperties->apiVersion > VK_API_VERSION_1_2) {
        pProperties->apiVersion = VK_API_VERSION_1_2;
    }
    pProperties->driverVersion = VK_MAKE_API_VERSION(0, 0, 1, 0);
    pProperties->vendorID = caps && caps->vendor_id ? caps->vendor_id : 0x5044; /* PD */
    pProperties->deviceID = caps && caps->device_id ? caps->device_id : 0x0001;
    /*
     * The Android GPU is usually physically integrated, but this glibc-facing
     * ICD does not expose true UMA pointers into vendor memory. Work is
     * lowered through the APK-owned executor, so advertise a discrete-like
     * device to keep ggml/llama.cpp out of UMA host-pointer fast paths.
     */
    pProperties->deviceType = caps
        ? caps->device_type
        : (bridge_available() ? VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU
                              : VK_PHYSICAL_DEVICE_TYPE_CPU);
    if (caps) {
        snprintf(pProperties->deviceName, sizeof(pProperties->deviceName),
                 "skydnir Vulkan bridge (%.200s)", caps->device_name);
    } else {
        snprintf(pProperties->deviceName, sizeof(pProperties->deviceName),
                 "pdocker Vulkan bridge (%s)", bridge_available() ? "queue" : "offline");
    }
    pProperties->limits.maxComputeSharedMemorySize =
        caps && caps->limits.maxComputeSharedMemorySize
            ? caps->limits.maxComputeSharedMemorySize
            : 32768;
    pProperties->limits.maxComputeWorkGroupCount[0] =
        caps && caps->limits.maxComputeWorkGroupCount[0]
            ? caps->limits.maxComputeWorkGroupCount[0]
            : 65535;
    pProperties->limits.maxComputeWorkGroupCount[1] =
        caps && caps->limits.maxComputeWorkGroupCount[1]
            ? caps->limits.maxComputeWorkGroupCount[1]
            : 65535;
    pProperties->limits.maxComputeWorkGroupCount[2] =
        caps && caps->limits.maxComputeWorkGroupCount[2]
            ? caps->limits.maxComputeWorkGroupCount[2]
            : 65535;
    pProperties->limits.maxComputeWorkGroupInvocations =
        caps && caps->limits.maxComputeWorkGroupInvocations
            ? caps->limits.maxComputeWorkGroupInvocations
            : 256;
    pProperties->limits.maxComputeWorkGroupSize[0] =
        caps && caps->limits.maxComputeWorkGroupSize[0]
            ? caps->limits.maxComputeWorkGroupSize[0]
            : 256;
    pProperties->limits.maxComputeWorkGroupSize[1] =
        caps && caps->limits.maxComputeWorkGroupSize[1]
            ? caps->limits.maxComputeWorkGroupSize[1]
            : 256;
    pProperties->limits.maxComputeWorkGroupSize[2] =
        caps && caps->limits.maxComputeWorkGroupSize[2]
            ? caps->limits.maxComputeWorkGroupSize[2]
            : 64;
    pProperties->limits.maxPushConstantsSize =
        caps && caps->limits.maxPushConstantsSize
            ? caps->limits.maxPushConstantsSize
            : 256;
    VkDeviceSize max_buffer = pdocker_vulkan_max_buffer_size();
    uint32_t transport_max_storage_range =
        max_buffer > UINT32_MAX ? UINT32_MAX : (uint32_t)max_buffer;
    pProperties->limits.maxStorageBufferRange =
        caps && caps->limits.maxStorageBufferRange
            ? (caps->limits.maxStorageBufferRange < transport_max_storage_range
                ? caps->limits.maxStorageBufferRange
                : transport_max_storage_range)
            : transport_max_storage_range;
    pProperties->limits.maxMemoryAllocationCount = 4096;
    pProperties->limits.maxImageDimension1D = 4096;
    pProperties->limits.maxImageDimension2D = 4096;
    pProperties->limits.maxImageDimension3D = 256;
    pProperties->limits.maxImageDimensionCube = 4096;
    pProperties->limits.maxImageArrayLayers = 256;
    pProperties->limits.maxFramebufferWidth = 4096;
    pProperties->limits.maxFramebufferHeight = 4096;
    pProperties->limits.maxFramebufferLayers = 256;
    pProperties->limits.framebufferColorSampleCounts = VK_SAMPLE_COUNT_1_BIT;
    pProperties->limits.framebufferDepthSampleCounts = VK_SAMPLE_COUNT_1_BIT;
    pProperties->limits.framebufferStencilSampleCounts = VK_SAMPLE_COUNT_1_BIT;
    pProperties->limits.framebufferNoAttachmentsSampleCounts = VK_SAMPLE_COUNT_1_BIT;
    pProperties->limits.sampledImageColorSampleCounts = VK_SAMPLE_COUNT_1_BIT;
    pProperties->limits.sampledImageIntegerSampleCounts = VK_SAMPLE_COUNT_1_BIT;
    pProperties->limits.sampledImageDepthSampleCounts = VK_SAMPLE_COUNT_1_BIT;
    pProperties->limits.sampledImageStencilSampleCounts = VK_SAMPLE_COUNT_1_BIT;
    pProperties->limits.storageImageSampleCounts = VK_SAMPLE_COUNT_1_BIT;
    pProperties->limits.maxSampleMaskWords = 1;
    pProperties->limits.maxColorAttachments = 8;
    pProperties->limits.maxBoundDescriptorSets =
        caps && caps->limits.maxBoundDescriptorSets &&
        caps->limits.maxBoundDescriptorSets < PDOCKER_VK_MAX_DESCRIPTOR_SETS
            ? caps->limits.maxBoundDescriptorSets
            : PDOCKER_VK_MAX_DESCRIPTOR_SETS;
    pProperties->limits.maxPerStageDescriptorStorageBuffers =
        caps && caps->limits.maxPerStageDescriptorStorageBuffers &&
        caps->limits.maxPerStageDescriptorStorageBuffers < PDOCKER_VK_MAX_STORAGE_BUFFERS
            ? caps->limits.maxPerStageDescriptorStorageBuffers
            : PDOCKER_VK_MAX_STORAGE_BUFFERS;
    pProperties->limits.maxDescriptorSetStorageBuffers =
        caps && caps->limits.maxDescriptorSetStorageBuffers &&
        caps->limits.maxDescriptorSetStorageBuffers < PDOCKER_VK_MAX_STORAGE_BUFFERS
            ? caps->limits.maxDescriptorSetStorageBuffers
            : PDOCKER_VK_MAX_STORAGE_BUFFERS;
    pProperties->limits.minStorageBufferOffsetAlignment = 16;
    pProperties->limits.minUniformBufferOffsetAlignment = 16;
    pProperties->limits.minMemoryMapAlignment = 64;
    pProperties->limits.nonCoherentAtomSize = 64;
    pProperties->limits.timestampComputeAndGraphics = VK_TRUE;
    pProperties->limits.timestampPeriod = 1.0f;
    if (trace_allocations()) {
        fprintf(stderr,
                "pdocker-vulkan-icd: advertised limits maxBuffer=%llu maxStorageRange=%u subgroupSize=%u subgroupOps=0x%x shaderInt64=%u storage16=%u storage8=%u\n",
                (unsigned long long)max_buffer,
                (unsigned)pProperties->limits.maxStorageBufferRange,
                (unsigned)advertised_subgroup_size(),
                (unsigned)advertised_subgroup_operations(),
                (unsigned)advertised_shader_int64(),
                (unsigned)advertised_storage16(),
                (unsigned)advertised_storage8());
    }
}

static VkSubgroupFeatureFlags advertised_subgroup_operations(void) {
    const PdockerVkAdvertisedCaps *caps = executor_advertisement_caps_if_enabled();
    if (caps) {
        VkSubgroupFeatureFlags ops = caps->subgroup.supportedOperations
            ? caps->subgroup.supportedOperations
            : VK_SUBGROUP_FEATURE_BASIC_BIT;
        if (env_disabled("PDOCKER_VULKAN_DISABLE_SUBGROUP_ARITHMETIC")) {
            ops &= ~VK_SUBGROUP_FEATURE_ARITHMETIC_BIT;
            if (!ops) ops = VK_SUBGROUP_FEATURE_BASIC_BIT;
        }
        return ops;
    }
    if (env_disabled("PDOCKER_VULKAN_DISABLE_SUBGROUP_ARITHMETIC")) {
        return VK_SUBGROUP_FEATURE_BASIC_BIT;
    }
    if (!env_truthy_default("PDOCKER_VULKAN_ENABLE_SUBGROUP_ARITHMETIC", false)) {
        return VK_SUBGROUP_FEATURE_BASIC_BIT;
    }
    return VK_SUBGROUP_FEATURE_BASIC_BIT |
           VK_SUBGROUP_FEATURE_ARITHMETIC_BIT |
           VK_SUBGROUP_FEATURE_BALLOT_BIT |
           VK_SUBGROUP_FEATURE_SHUFFLE_BIT |
           VK_SUBGROUP_FEATURE_VOTE_BIT;
}

static uint32_t advertised_subgroup_size(void) {
    const PdockerVkAdvertisedCaps *caps = executor_advertisement_caps_if_enabled();
    if (caps && caps->subgroup.subgroupSize) {
        return caps->subgroup.subgroupSize;
    }
    const char *value = getenv("PDOCKER_VULKAN_SUBGROUP_SIZE");
    if (value && value[0]) {
        char *end = NULL;
        unsigned long parsed = strtoul(value, &end, 10);
        if (end != value && parsed >= 1 && parsed <= 128) {
            return (uint32_t)parsed;
        }
    }
    return 32;
}

static void fill_pnext_properties(void *pNext) {
    for (void *node = pNext; node;) {
        PdockerVkStructHeader header = read_vk_struct_header(node);
        switch (header.sType) {
            case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MAINTENANCE_3_PROPERTIES: {
                VkPhysicalDeviceMaintenance3Properties *p = (VkPhysicalDeviceMaintenance3Properties *)node;
                p->maxPerSetDescriptors = 1024;
                p->maxMemoryAllocationSize = pdocker_vulkan_max_buffer_size();
                break;
            }
#ifdef VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MAINTENANCE_4_PROPERTIES
            case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MAINTENANCE_4_PROPERTIES: {
                VkPhysicalDeviceMaintenance4Properties *p = (VkPhysicalDeviceMaintenance4Properties *)node;
                p->maxBufferSize = pdocker_vulkan_max_buffer_size();
                break;
            }
#endif
            case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SUBGROUP_PROPERTIES: {
                VkPhysicalDeviceSubgroupProperties *p = (VkPhysicalDeviceSubgroupProperties *)node;
                p->subgroupSize = advertised_subgroup_size();
                p->supportedStages = VK_SHADER_STAGE_COMPUTE_BIT;
                p->supportedOperations = advertised_subgroup_operations();
                p->quadOperationsInAllStages = VK_FALSE;
                break;
            }
            case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DRIVER_PROPERTIES: {
                VkPhysicalDeviceDriverProperties *p = (VkPhysicalDeviceDriverProperties *)node;
                p->driverID = VK_DRIVER_ID_MESA_LLVMPIPE;
                snprintf(p->driverName, sizeof(p->driverName), "pdocker-vulkan-bridge");
                snprintf(p->driverInfo, sizeof(p->driverInfo), "pdocker neutral Vulkan bridge");
                p->conformanceVersion.major = 1;
                p->conformanceVersion.minor = 2;
                p->conformanceVersion.subminor = 0;
                p->conformanceVersion.patch = 0;
                break;
            }
            case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_1_PROPERTIES: {
                VkPhysicalDeviceVulkan11Properties *p = (VkPhysicalDeviceVulkan11Properties *)node;
                p->subgroupSize = advertised_subgroup_size();
                p->subgroupSupportedStages = VK_SHADER_STAGE_COMPUTE_BIT;
                p->subgroupSupportedOperations = advertised_subgroup_operations();
                p->subgroupQuadOperationsInAllStages = VK_FALSE;
                p->maxMultiviewViewCount = 1;
                p->maxMultiviewInstanceIndex = 1;
                p->maxPerSetDescriptors = 1024;
                p->maxMemoryAllocationSize = pdocker_vulkan_max_buffer_size();
                break;
            }
            case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_PROPERTIES: {
                VkPhysicalDeviceVulkan12Properties *p = (VkPhysicalDeviceVulkan12Properties *)node;
                p->driverID = VK_DRIVER_ID_MESA_LLVMPIPE;
                snprintf(p->driverName, sizeof(p->driverName), "pdocker-vulkan-bridge");
                snprintf(p->driverInfo, sizeof(p->driverInfo), "pdocker neutral Vulkan bridge");
                p->conformanceVersion.major = 1;
                p->conformanceVersion.minor = 2;
                p->shaderRoundingModeRTEFloat16 = VK_FALSE;
                p->shaderRoundingModeRTZFloat16 = VK_FALSE;
                break;
            }
            default:
                break;
        }
        node = (void *)header.pNext;
    }
}

static void fill_physical_device_features(VkPhysicalDeviceFeatures *pFeatures) {
    if (!pFeatures) return;
    memset(pFeatures, 0, sizeof(*pFeatures));
    pFeatures->shaderInt64 = advertised_shader_int64();
}

static void fill_pnext_features(void *pNext) {
    const PdockerVkAdvertisedCaps *caps = executor_advertisement_caps_if_enabled();
    for (void *node = pNext; node;) {
        PdockerVkStructHeader header = read_vk_struct_header(node);
        switch (header.sType) {
            case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_1_FEATURES: {
                VkPhysicalDeviceVulkan11Features *p = (VkPhysicalDeviceVulkan11Features *)node;
                if (caps) {
                    bool disabled = env_disabled("PDOCKER_VULKAN_DISABLE_16BIT_STORAGE");
                    p->storageBuffer16BitAccess = disabled ? VK_FALSE : caps->storage16.storageBuffer16BitAccess;
                    p->uniformAndStorageBuffer16BitAccess = disabled ? VK_FALSE : caps->storage16.uniformAndStorageBuffer16BitAccess;
                    p->storagePushConstant16 = disabled ? VK_FALSE : caps->storage16.storagePushConstant16;
                    p->storageInputOutput16 = disabled ? VK_FALSE : caps->storage16.storageInputOutput16;
                } else {
                    VkBool32 storage16 = advertised_storage16();
                    p->storageBuffer16BitAccess = storage16;
                    p->uniformAndStorageBuffer16BitAccess = VK_FALSE;
                    p->storagePushConstant16 = VK_FALSE;
                    p->storageInputOutput16 = VK_FALSE;
                }
                break;
            }
            case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_16BIT_STORAGE_FEATURES: {
                VkPhysicalDevice16BitStorageFeatures *p = (VkPhysicalDevice16BitStorageFeatures *)node;
                if (caps) {
                    bool disabled = env_disabled("PDOCKER_VULKAN_DISABLE_16BIT_STORAGE");
                    p->storageBuffer16BitAccess = disabled ? VK_FALSE : caps->storage16.storageBuffer16BitAccess;
                    p->uniformAndStorageBuffer16BitAccess = disabled ? VK_FALSE : caps->storage16.uniformAndStorageBuffer16BitAccess;
                    p->storagePushConstant16 = disabled ? VK_FALSE : caps->storage16.storagePushConstant16;
                    p->storageInputOutput16 = disabled ? VK_FALSE : caps->storage16.storageInputOutput16;
                } else {
                    VkBool32 storage16 = advertised_storage16();
                    p->storageBuffer16BitAccess = storage16;
                    p->uniformAndStorageBuffer16BitAccess = VK_FALSE;
                    p->storagePushConstant16 = VK_FALSE;
                    p->storageInputOutput16 = VK_FALSE;
                }
                break;
            }
            case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES: {
                VkPhysicalDeviceVulkan12Features *p = (VkPhysicalDeviceVulkan12Features *)node;
                if (caps) {
                    bool disabled = env_disabled("PDOCKER_VULKAN_DISABLE_8BIT_STORAGE");
                    p->storageBuffer8BitAccess = disabled ? VK_FALSE : caps->storage8.storageBuffer8BitAccess;
                    p->uniformAndStorageBuffer8BitAccess = disabled ? VK_FALSE : caps->storage8.uniformAndStorageBuffer8BitAccess;
                    p->storagePushConstant8 = disabled ? VK_FALSE : caps->storage8.storagePushConstant8;
                    p->shaderFloat16 = caps->float16_int8.shaderFloat16;
                    p->shaderInt8 = disabled ? VK_FALSE : caps->float16_int8.shaderInt8;
                } else {
                    VkBool32 storage8 = advertised_storage8();
                    p->storageBuffer8BitAccess = storage8;
                    p->uniformAndStorageBuffer8BitAccess = VK_FALSE;
                    p->storagePushConstant8 = VK_FALSE;
                    p->shaderFloat16 = VK_FALSE;
                    p->shaderInt8 = storage8;
                }
                p->bufferDeviceAddress = VK_FALSE;
                p->vulkanMemoryModel = VK_FALSE;
                p->timelineSemaphore = VK_TRUE;
                break;
            }
            case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_8BIT_STORAGE_FEATURES: {
                VkPhysicalDevice8BitStorageFeatures *p = (VkPhysicalDevice8BitStorageFeatures *)node;
                if (caps) {
                    bool disabled = env_disabled("PDOCKER_VULKAN_DISABLE_8BIT_STORAGE");
                    p->storageBuffer8BitAccess = disabled ? VK_FALSE : caps->storage8.storageBuffer8BitAccess;
                    p->uniformAndStorageBuffer8BitAccess = disabled ? VK_FALSE : caps->storage8.uniformAndStorageBuffer8BitAccess;
                    p->storagePushConstant8 = disabled ? VK_FALSE : caps->storage8.storagePushConstant8;
                } else {
                    p->storageBuffer8BitAccess = advertised_storage8();
                    p->uniformAndStorageBuffer8BitAccess = VK_FALSE;
                    p->storagePushConstant8 = VK_FALSE;
                }
                break;
            }
            case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SHADER_FLOAT16_INT8_FEATURES: {
                VkPhysicalDeviceShaderFloat16Int8Features *p = (VkPhysicalDeviceShaderFloat16Int8Features *)node;
                if (caps) {
                    bool storage8_disabled = env_disabled("PDOCKER_VULKAN_DISABLE_8BIT_STORAGE");
                    p->shaderFloat16 = caps->float16_int8.shaderFloat16;
                    p->shaderInt8 = storage8_disabled ? VK_FALSE : caps->float16_int8.shaderInt8;
                } else {
                    p->shaderFloat16 = VK_FALSE;
                    p->shaderInt8 = advertised_storage8();
                }
                break;
            }
#ifdef VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SYNCHRONIZATION_2_FEATURES
            case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SYNCHRONIZATION_2_FEATURES: {
                VkPhysicalDeviceSynchronization2Features *p = (VkPhysicalDeviceSynchronization2Features *)node;
                p->synchronization2 = VK_TRUE;
                break;
            }
#endif
#ifdef VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_TIMELINE_SEMAPHORE_FEATURES
            case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_TIMELINE_SEMAPHORE_FEATURES: {
                VkPhysicalDeviceTimelineSemaphoreFeatures *p = (VkPhysicalDeviceTimelineSemaphoreFeatures *)node;
                p->timelineSemaphore = VK_TRUE;
                break;
            }
#endif
#ifdef VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DYNAMIC_RENDERING_FEATURES
            case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DYNAMIC_RENDERING_FEATURES: {
                VkPhysicalDeviceDynamicRenderingFeatures *p = (VkPhysicalDeviceDynamicRenderingFeatures *)node;
                p->dynamicRendering = VK_TRUE;
                break;
            }
#endif
#ifdef VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_EXTENDED_DYNAMIC_STATE_FEATURES_EXT
            case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_EXTENDED_DYNAMIC_STATE_FEATURES_EXT: {
                VkPhysicalDeviceExtendedDynamicStateFeaturesEXT *p = (VkPhysicalDeviceExtendedDynamicStateFeaturesEXT *)node;
                p->extendedDynamicState = caps ? (caps->ext_extended_dynamic_state ? VK_TRUE : VK_FALSE) : VK_TRUE;
                break;
            }
#endif
#ifdef VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_INDEX_TYPE_UINT8_FEATURES_EXT
            case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_INDEX_TYPE_UINT8_FEATURES_EXT: {
                VkPhysicalDeviceIndexTypeUint8FeaturesEXT *p = (VkPhysicalDeviceIndexTypeUint8FeaturesEXT *)node;
                p->indexTypeUint8 = (caps && caps->ext_index_type_uint8 && caps->index_type_uint8.indexTypeUint8)
                    ? VK_TRUE
                    : VK_FALSE;
                break;
            }
#endif
#ifdef VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MAINTENANCE_4_FEATURES
            case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MAINTENANCE_4_FEATURES: {
                VkPhysicalDeviceMaintenance4Features *p = (VkPhysicalDeviceMaintenance4Features *)node;
                p->maintenance4 = VK_TRUE;
                break;
            }
#endif
            default:
                break;
        }
        node = (void *)header.pNext;
    }
}

static uint64_t feature_mask_from_base_features(const VkPhysicalDeviceFeatures *features) {
    uint64_t mask = 0;
    if (!features) return mask;
    if (features->shaderInt64) mask |= PDOCKER_VK_FEATURE_SHADER_INT64;
    if (features->shaderInt16) mask |= PDOCKER_VK_FEATURE_SHADER_INT16;
    if (features->shaderFloat64) mask |= PDOCKER_VK_FEATURE_SHADER_FLOAT64;
    return mask;
}

static uint64_t feature_mask_from_pnext_chain(const void *pNext) {
    uint64_t mask = 0;
    /*
     * Vulkan applications commonly pass VkPhysicalDeviceFeatures2 in
     * VkDeviceCreateInfo::pNext and hang the actual 1.1/1.2/extension feature
     * structs from Features2::pNext.  Treat the pNext list as one continuous
     * header-compatible chain so requested_feature_mask mirrors what the app
     * asked Vulkan to enable.  The header is copied out before dispatching to
     * concrete struct types; this avoids relying on compiler strict-aliasing
     * behavior for the generic VkBaseInStructure view.  This mask is forwarded
     * unchanged to the Android executor for strict passthrough validation.
     */
    for (const void *node = pNext; node;) {
        PdockerVkStructHeader header = read_vk_struct_header(node);
        switch (header.sType) {
            case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2: {
                const VkPhysicalDeviceFeatures2 *p = (const VkPhysicalDeviceFeatures2 *)node;
                mask |= feature_mask_from_base_features(&p->features);
                break;
            }
            case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_1_FEATURES: {
                const VkPhysicalDeviceVulkan11Features *p = (const VkPhysicalDeviceVulkan11Features *)node;
                if (p->storageBuffer16BitAccess) mask |= PDOCKER_VK_FEATURE_STORAGE_BUFFER_16;
                if (p->uniformAndStorageBuffer16BitAccess) mask |= PDOCKER_VK_FEATURE_UNIFORM_STORAGE_BUFFER_16;
                if (p->storagePushConstant16) mask |= PDOCKER_VK_FEATURE_STORAGE_PUSH_CONSTANT_16;
                break;
            }
            case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_16BIT_STORAGE_FEATURES: {
                const VkPhysicalDevice16BitStorageFeatures *p = (const VkPhysicalDevice16BitStorageFeatures *)node;
                if (p->storageBuffer16BitAccess) mask |= PDOCKER_VK_FEATURE_STORAGE_BUFFER_16;
                if (p->uniformAndStorageBuffer16BitAccess) mask |= PDOCKER_VK_FEATURE_UNIFORM_STORAGE_BUFFER_16;
                if (p->storagePushConstant16) mask |= PDOCKER_VK_FEATURE_STORAGE_PUSH_CONSTANT_16;
                break;
            }
            case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES: {
                const VkPhysicalDeviceVulkan12Features *p = (const VkPhysicalDeviceVulkan12Features *)node;
                if (p->storageBuffer8BitAccess) mask |= PDOCKER_VK_FEATURE_STORAGE_BUFFER_8;
                if (p->uniformAndStorageBuffer8BitAccess) mask |= PDOCKER_VK_FEATURE_UNIFORM_STORAGE_BUFFER_8;
                if (p->storagePushConstant8) mask |= PDOCKER_VK_FEATURE_STORAGE_PUSH_CONSTANT_8;
                if (p->shaderFloat16) mask |= PDOCKER_VK_FEATURE_SHADER_FLOAT16;
                if (p->shaderInt8) mask |= PDOCKER_VK_FEATURE_SHADER_INT8;
                if (p->bufferDeviceAddress) mask |= PDOCKER_VK_FEATURE_BUFFER_DEVICE_ADDRESS;
                if (p->vulkanMemoryModel) mask |= PDOCKER_VK_FEATURE_VULKAN_MEMORY_MODEL;
                if (p->timelineSemaphore) mask |= PDOCKER_VK_FEATURE_TIMELINE_SEMAPHORE;
                break;
            }
            case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_8BIT_STORAGE_FEATURES: {
                const VkPhysicalDevice8BitStorageFeatures *p = (const VkPhysicalDevice8BitStorageFeatures *)node;
                if (p->storageBuffer8BitAccess) mask |= PDOCKER_VK_FEATURE_STORAGE_BUFFER_8;
                if (p->uniformAndStorageBuffer8BitAccess) mask |= PDOCKER_VK_FEATURE_UNIFORM_STORAGE_BUFFER_8;
                if (p->storagePushConstant8) mask |= PDOCKER_VK_FEATURE_STORAGE_PUSH_CONSTANT_8;
                break;
            }
            case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SHADER_FLOAT16_INT8_FEATURES: {
                const VkPhysicalDeviceShaderFloat16Int8Features *p = (const VkPhysicalDeviceShaderFloat16Int8Features *)node;
                if (p->shaderFloat16) mask |= PDOCKER_VK_FEATURE_SHADER_FLOAT16;
                if (p->shaderInt8) mask |= PDOCKER_VK_FEATURE_SHADER_INT8;
                break;
            }
#ifdef VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_INDEX_TYPE_UINT8_FEATURES_EXT
            case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_INDEX_TYPE_UINT8_FEATURES_EXT: {
                const VkPhysicalDeviceIndexTypeUint8FeaturesEXT *p = (const VkPhysicalDeviceIndexTypeUint8FeaturesEXT *)node;
                if (p->indexTypeUint8) mask |= PDOCKER_VK_FEATURE_INDEX_TYPE_UINT8;
                break;
            }
#endif
#ifdef VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_TIMELINE_SEMAPHORE_FEATURES
            case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_TIMELINE_SEMAPHORE_FEATURES: {
                const VkPhysicalDeviceTimelineSemaphoreFeatures *p = (const VkPhysicalDeviceTimelineSemaphoreFeatures *)node;
                if (p->timelineSemaphore) mask |= PDOCKER_VK_FEATURE_TIMELINE_SEMAPHORE;
                break;
            }
#endif
#ifdef VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MAINTENANCE_4_FEATURES
            case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MAINTENANCE_4_FEATURES: {
                const VkPhysicalDeviceMaintenance4Features *p = (const VkPhysicalDeviceMaintenance4Features *)node;
                if (p->maintenance4) mask |= PDOCKER_VK_FEATURE_MAINTENANCE_4;
                break;
            }
#endif
            default:
                break;
        }
        node = header.pNext;
    }
    return mask;
}

static uint64_t requested_feature_mask_from_device_create_info(
        const VkDeviceCreateInfo *pCreateInfo) {
    if (!pCreateInfo) return 0;
    uint64_t mask = feature_mask_from_base_features(pCreateInfo->pEnabledFeatures);
    mask |= feature_mask_from_pnext_chain(pCreateInfo->pNext);
    return mask;
}

static uint64_t advertised_feature_mask(void) {
    uint64_t mask = 0;
    const PdockerVkAdvertisedCaps *caps = executor_advertisement_caps_if_enabled();
    if (advertised_shader_int64()) mask |= PDOCKER_VK_FEATURE_SHADER_INT64;
    if (caps) {
        bool storage16_disabled = env_disabled("PDOCKER_VULKAN_DISABLE_16BIT_STORAGE");
        bool storage8_disabled = env_disabled("PDOCKER_VULKAN_DISABLE_8BIT_STORAGE");
        if (!storage16_disabled && caps->storage16.storageBuffer16BitAccess) {
            mask |= PDOCKER_VK_FEATURE_STORAGE_BUFFER_16;
        }
        if (!storage16_disabled && caps->storage16.uniformAndStorageBuffer16BitAccess) {
            mask |= PDOCKER_VK_FEATURE_UNIFORM_STORAGE_BUFFER_16;
        }
        if (!storage16_disabled && caps->storage16.storagePushConstant16) {
            mask |= PDOCKER_VK_FEATURE_STORAGE_PUSH_CONSTANT_16;
        }
        if (!storage8_disabled && caps->storage8.storageBuffer8BitAccess) {
            mask |= PDOCKER_VK_FEATURE_STORAGE_BUFFER_8;
        }
        if (!storage8_disabled && caps->storage8.uniformAndStorageBuffer8BitAccess) {
            mask |= PDOCKER_VK_FEATURE_UNIFORM_STORAGE_BUFFER_8;
        }
        if (!storage8_disabled && caps->storage8.storagePushConstant8) {
            mask |= PDOCKER_VK_FEATURE_STORAGE_PUSH_CONSTANT_8;
        }
        if (caps->float16_int8.shaderFloat16) {
            mask |= PDOCKER_VK_FEATURE_SHADER_FLOAT16;
        }
        if (!storage8_disabled && caps->float16_int8.shaderInt8) {
            mask |= PDOCKER_VK_FEATURE_SHADER_INT8;
        }
        if (caps->ext_index_type_uint8 && caps->index_type_uint8.indexTypeUint8) {
            mask |= PDOCKER_VK_FEATURE_INDEX_TYPE_UINT8;
        }
    } else {
        if (advertised_storage16()) mask |= PDOCKER_VK_FEATURE_STORAGE_BUFFER_16;
        if (advertised_storage8()) {
            mask |= PDOCKER_VK_FEATURE_STORAGE_BUFFER_8;
            mask |= PDOCKER_VK_FEATURE_SHADER_INT8;
        }
    }
#ifdef VK_KHR_MAINTENANCE_4_EXTENSION_NAME
    mask |= PDOCKER_VK_FEATURE_MAINTENANCE_4;
#endif
    mask |= PDOCKER_VK_FEATURE_TIMELINE_SEMAPHORE;
    return mask;
}

static bool requested_features_supported(uint64_t requested, uint64_t supported, uint64_t *unsupported) {
    uint64_t missing = requested & ~supported;
    if (unsupported) *unsupported = missing;
    return missing == 0;
}

static void trace_device_create_features(const VkDeviceCreateInfo *pCreateInfo) {
    if (!pCreateInfo || !(trace_allocations() || getenv("PDOCKER_VULKAN_ICD_DEBUG"))) return;
    const VkPhysicalDeviceFeatures *features = pCreateInfo->pEnabledFeatures;
    fprintf(stderr,
            "pdocker-vulkan-icd: create-device extensions=%u base_features={shaderInt64:%u,shaderInt16:%u,shaderFloat64:%u}\n",
            pCreateInfo->enabledExtensionCount,
            features ? features->shaderInt64 : 0,
            features ? features->shaderInt16 : 0,
            features ? features->shaderFloat64 : 0);
    for (const void *node = pCreateInfo->pNext; node;) {
        PdockerVkStructHeader header = read_vk_struct_header(node);
        switch (header.sType) {
            case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2: {
                const VkPhysicalDeviceFeatures2 *p = (const VkPhysicalDeviceFeatures2 *)node;
                fprintf(stderr,
                        "pdocker-vulkan-icd: create-device features2={shaderInt64:%u,shaderInt16:%u,shaderFloat64:%u}\n",
                        p->features.shaderInt64,
                        p->features.shaderInt16,
                        p->features.shaderFloat64);
                break;
            }
            case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_1_FEATURES: {
                const VkPhysicalDeviceVulkan11Features *p = (const VkPhysicalDeviceVulkan11Features *)node;
                fprintf(stderr,
                        "pdocker-vulkan-icd: create-device vk11_features={storage16:%u,ubo_ssbo16:%u,push16:%u,io16:%u}\n",
                        p->storageBuffer16BitAccess,
                        p->uniformAndStorageBuffer16BitAccess,
                        p->storagePushConstant16,
                        p->storageInputOutput16);
                break;
            }
            case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_16BIT_STORAGE_FEATURES: {
                const VkPhysicalDevice16BitStorageFeatures *p = (const VkPhysicalDevice16BitStorageFeatures *)node;
                fprintf(stderr,
                        "pdocker-vulkan-icd: create-device storage16_features={storage16:%u,ubo_ssbo16:%u,push16:%u,io16:%u}\n",
                        p->storageBuffer16BitAccess,
                        p->uniformAndStorageBuffer16BitAccess,
                        p->storagePushConstant16,
                        p->storageInputOutput16);
                break;
            }
            case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES: {
                const VkPhysicalDeviceVulkan12Features *p = (const VkPhysicalDeviceVulkan12Features *)node;
                fprintf(stderr,
                        "pdocker-vulkan-icd: create-device vk12_features={storage8:%u,ubo_ssbo8:%u,push8:%u,float16:%u,int8:%u,bufferDeviceAddress:%u,vulkanMemoryModel:%u}\n",
                        p->storageBuffer8BitAccess,
                        p->uniformAndStorageBuffer8BitAccess,
                        p->storagePushConstant8,
                        p->shaderFloat16,
                        p->shaderInt8,
                        p->bufferDeviceAddress,
                        p->vulkanMemoryModel);
                break;
            }
            case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_8BIT_STORAGE_FEATURES: {
                const VkPhysicalDevice8BitStorageFeatures *p = (const VkPhysicalDevice8BitStorageFeatures *)node;
                fprintf(stderr,
                        "pdocker-vulkan-icd: create-device storage8_features={storage8:%u,ubo_ssbo8:%u,push8:%u}\n",
                        p->storageBuffer8BitAccess,
                        p->uniformAndStorageBuffer8BitAccess,
                        p->storagePushConstant8);
                break;
            }
            case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SHADER_FLOAT16_INT8_FEATURES: {
                const VkPhysicalDeviceShaderFloat16Int8Features *p = (const VkPhysicalDeviceShaderFloat16Int8Features *)node;
                fprintf(stderr,
                        "pdocker-vulkan-icd: create-device float16_int8_features={float16:%u,int8:%u}\n",
                        p->shaderFloat16,
                        p->shaderInt8);
                break;
            }
#ifdef VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_INDEX_TYPE_UINT8_FEATURES_EXT
            case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_INDEX_TYPE_UINT8_FEATURES_EXT: {
                const VkPhysicalDeviceIndexTypeUint8FeaturesEXT *p = (const VkPhysicalDeviceIndexTypeUint8FeaturesEXT *)node;
                fprintf(stderr,
                        "pdocker-vulkan-icd: create-device index_type_uint8_features={indexTypeUint8:%u}\n",
                        p->indexTypeUint8);
                break;
            }
#endif
            default:
                fprintf(stderr, "pdocker-vulkan-icd: create-device pnext sType=%d\n", (int)header.sType);
                break;
        }
        node = header.pNext;
    }
}

VKAPI_ATTR VkResult VKAPI_CALL vk_icdNegotiateLoaderICDInterfaceVersion(uint32_t *pVersion) {
    if (!pVersion) return VK_ERROR_INITIALIZATION_FAILED;
    if (*pVersion > 5) *pVersion = 5;
    return VK_SUCCESS;
}

VKAPI_ATTR VkResult VKAPI_CALL vkEnumerateInstanceVersion(uint32_t *pApiVersion) {
    if (!pApiVersion) return VK_ERROR_INITIALIZATION_FAILED;
    *pApiVersion = pdocker_api_version();
    return VK_SUCCESS;
}

VKAPI_ATTR VkResult VKAPI_CALL vkEnumerateInstanceExtensionProperties(
        const char *pLayerName,
        uint32_t *pPropertyCount,
        VkExtensionProperties *pProperties) {
    (void)pLayerName;
    if (!pPropertyCount) return VK_ERROR_INITIALIZATION_FAILED;
    if (!pProperties) {
        *pPropertyCount = 0;
        return VK_SUCCESS;
    }
    *pPropertyCount = 0;
    return VK_SUCCESS;
}

VKAPI_ATTR VkResult VKAPI_CALL vkEnumerateInstanceLayerProperties(
        uint32_t *pPropertyCount,
        VkLayerProperties *pProperties) {
    if (!pPropertyCount) return VK_ERROR_INITIALIZATION_FAILED;
    if (!pProperties) {
        *pPropertyCount = 0;
        return VK_SUCCESS;
    }
    *pPropertyCount = 0;
    return VK_SUCCESS;
}

VKAPI_ATTR VkResult VKAPI_CALL vkCreateInstance(
        const VkInstanceCreateInfo *pCreateInfo,
        const VkAllocationCallbacks *pAllocator,
        VkInstance *pInstance) {
    (void)pAllocator;
    if (!pInstance) return VK_ERROR_INITIALIZATION_FAILED;
    if (pCreateInfo && pCreateInfo->enabledExtensionCount > 0) {
        for (uint32_t i = 0; i < pCreateInfo->enabledExtensionCount; ++i) {
            const char *name = pCreateInfo->ppEnabledExtensionNames
                ? pCreateInfo->ppEnabledExtensionNames[i]
                : NULL;
            if (name && name[0]) {
                trace_icd_runtime_failure("instance-extension-not-present",
                                          VK_ERROR_EXTENSION_NOT_PRESENT);
                if (trace_allocations() || getenv("PDOCKER_VULKAN_ICD_DEBUG")) {
                    fprintf(stderr,
                            "pdocker-vulkan-icd: instance extension unsupported: %s\n",
                            name);
                }
                return VK_ERROR_EXTENSION_NOT_PRESENT;
            }
        }
    }
    PdockerVkInstance *instance = calloc(1, sizeof(*instance));
    if (!instance) return VK_ERROR_OUT_OF_HOST_MEMORY;
    set_loader_magic_value(instance);
    *pInstance = (VkInstance)instance;
    set_loader_magic_value(&g_device);
    set_loader_magic_value(&g_queue);
    trace_icd_runtime_marker_once("create-instance");
    return VK_SUCCESS;
}

VKAPI_ATTR void VKAPI_CALL vkDestroyInstance(
        VkInstance instance,
        const VkAllocationCallbacks *pAllocator) {
    (void)pAllocator;
    free((void *)instance);
}

VKAPI_ATTR VkResult VKAPI_CALL vkEnumeratePhysicalDevices(
        VkInstance instance,
        uint32_t *pPhysicalDeviceCount,
        VkPhysicalDevice *pPhysicalDevices) {
    (void)instance;
    if (!pPhysicalDeviceCount) return VK_ERROR_INITIALIZATION_FAILED;
    if (!pPhysicalDevices) {
        *pPhysicalDeviceCount = 1;
        return VK_SUCCESS;
    }
    if (*pPhysicalDeviceCount < 1) return VK_INCOMPLETE;
    pPhysicalDevices[0] = (VkPhysicalDevice)&g_device;
    *pPhysicalDeviceCount = 1;
    return VK_SUCCESS;
}

VKAPI_ATTR void VKAPI_CALL vkGetPhysicalDeviceProperties(
        VkPhysicalDevice physicalDevice,
        VkPhysicalDeviceProperties *pProperties) {
    (void)physicalDevice;
    fill_physical_device_properties(pProperties);
}

VKAPI_ATTR void VKAPI_CALL vkGetPhysicalDeviceProperties2(
        VkPhysicalDevice physicalDevice,
        VkPhysicalDeviceProperties2 *pProperties) {
    if (!pProperties) return;
    vkGetPhysicalDeviceProperties(physicalDevice, &pProperties->properties);
    fill_pnext_properties(pProperties->pNext);
}

VKAPI_ATTR void VKAPI_CALL vkGetPhysicalDeviceFeatures(
        VkPhysicalDevice physicalDevice,
        VkPhysicalDeviceFeatures *pFeatures) {
    (void)physicalDevice;
    fill_physical_device_features(pFeatures);
}

VKAPI_ATTR void VKAPI_CALL vkGetPhysicalDeviceFeatures2(
        VkPhysicalDevice physicalDevice,
        VkPhysicalDeviceFeatures2 *pFeatures) {
    if (!pFeatures) return;
    (void)physicalDevice;
    fill_physical_device_features(&pFeatures->features);
    fill_pnext_features(pFeatures->pNext);
}

VKAPI_ATTR void VKAPI_CALL vkGetPhysicalDeviceFormatProperties(
        VkPhysicalDevice physicalDevice,
        VkFormat format,
        VkFormatProperties *pFormatProperties) {
    (void)physicalDevice;
    if (!pFormatProperties) return;
    memset(pFormatProperties, 0, sizeof(*pFormatProperties));
    if (!pdocker_vk_format_bridge_supported(format)) return;
    pFormatProperties->bufferFeatures = pdocker_vk_format_buffer_features(format);
    pFormatProperties->linearTilingFeatures = 0;
    pFormatProperties->optimalTilingFeatures = pdocker_vk_format_image_features(format);
}

VKAPI_ATTR VkResult VKAPI_CALL vkGetPhysicalDeviceImageFormatProperties(
        VkPhysicalDevice physicalDevice,
        VkFormat format,
        VkImageType type,
        VkImageTiling tiling,
        VkImageUsageFlags usage,
        VkImageCreateFlags flags,
        VkImageFormatProperties *pImageFormatProperties) {
    (void)physicalDevice;
    if (!pImageFormatProperties) return VK_ERROR_FORMAT_NOT_SUPPORTED;
    memset(pImageFormatProperties, 0, sizeof(*pImageFormatProperties));
    if (!pdocker_vk_format_bridge_supported(format) ||
        !pdocker_vk_image_usage_supported_by_format(format, usage)) {
        return VK_ERROR_FORMAT_NOT_SUPPORTED;
    }
    if (tiling != VK_IMAGE_TILING_OPTIMAL) return VK_ERROR_FORMAT_NOT_SUPPORTED;
    if (type != VK_IMAGE_TYPE_1D && type != VK_IMAGE_TYPE_2D && type != VK_IMAGE_TYPE_3D) {
        return VK_ERROR_FORMAT_NOT_SUPPORTED;
    }
    const VkImageCreateFlags unsupported_sparse_flags = VK_IMAGE_CREATE_SPARSE_BINDING_BIT |
                                                          VK_IMAGE_CREATE_SPARSE_RESIDENCY_BIT |
                                                          VK_IMAGE_CREATE_SPARSE_ALIASED_BIT;
    if (flags & unsupported_sparse_flags) return VK_ERROR_FORMAT_NOT_SUPPORTED;
    const VkImageCreateFlags supported_flags = VK_IMAGE_CREATE_MUTABLE_FORMAT_BIT |
                                               VK_IMAGE_CREATE_ALIAS_BIT |
                                               VK_IMAGE_CREATE_CUBE_COMPATIBLE_BIT;
    if (flags & ~supported_flags) return VK_ERROR_FORMAT_NOT_SUPPORTED;
    if ((flags & VK_IMAGE_CREATE_CUBE_COMPATIBLE_BIT) && type != VK_IMAGE_TYPE_2D) {
        return VK_ERROR_FORMAT_NOT_SUPPORTED;
    }
    VkExtent3D max_extent = {4096u, 4096u, 1u};
    uint32_t max_layers = 256u;
    if (type == VK_IMAGE_TYPE_1D) {
        max_extent = (VkExtent3D){4096u, 1u, 1u};
    } else if (type == VK_IMAGE_TYPE_3D) {
        max_extent = (VkExtent3D){256u, 256u, 256u};
        max_layers = 1u;
    }
    pImageFormatProperties->maxExtent = max_extent;
    pImageFormatProperties->maxMipLevels = pdocker_vk_image_max_mip_levels(max_extent);
    pImageFormatProperties->maxArrayLayers = max_layers;
    pImageFormatProperties->sampleCounts = VK_SAMPLE_COUNT_1_BIT;
    VkDeviceSize max_buffer = pdocker_vulkan_max_buffer_size();
    VkDeviceSize heap = pdocker_vulkan_heap_size();
    VkDeviceSize max_resource = max_buffer < heap ? max_buffer : heap;
    const VkDeviceSize vulkan_min_resource_size = (VkDeviceSize)1u << 31;
    if (max_resource < vulkan_min_resource_size) {
        return VK_ERROR_FORMAT_NOT_SUPPORTED;
    }
    pImageFormatProperties->maxResourceSize = max_resource;
    return VK_SUCCESS;
}

VKAPI_ATTR void VKAPI_CALL vkGetPhysicalDeviceSparseImageFormatProperties(
        VkPhysicalDevice physicalDevice,
        VkFormat format,
        VkImageType type,
        VkSampleCountFlagBits samples,
        VkImageUsageFlags usage,
        VkImageTiling tiling,
        uint32_t *pPropertyCount,
        VkSparseImageFormatProperties *pProperties) {
    (void)physicalDevice;
    (void)format;
    (void)type;
    (void)samples;
    (void)usage;
    (void)tiling;
    if (!pPropertyCount) return;
    if (!pProperties) {
        *pPropertyCount = 0;
        return;
    }
    *pPropertyCount = 0;
}

VKAPI_ATTR void VKAPI_CALL vkGetPhysicalDeviceQueueFamilyProperties(
        VkPhysicalDevice physicalDevice,
        uint32_t *pQueueFamilyPropertyCount,
        VkQueueFamilyProperties *pQueueFamilyProperties) {
    (void)physicalDevice;
    if (!pQueueFamilyPropertyCount) return;
    if (!pQueueFamilyProperties) {
        *pQueueFamilyPropertyCount = 1;
        return;
    }
    if (*pQueueFamilyPropertyCount >= 1) {
        memset(&pQueueFamilyProperties[0], 0, sizeof(pQueueFamilyProperties[0]));
        pQueueFamilyProperties[0].queueFlags = VK_QUEUE_COMPUTE_BIT | VK_QUEUE_TRANSFER_BIT;
        pQueueFamilyProperties[0].queueCount = 2;
        pQueueFamilyProperties[0].timestampValidBits = 64;
        pQueueFamilyProperties[0].minImageTransferGranularity.width = 1;
        pQueueFamilyProperties[0].minImageTransferGranularity.height = 1;
        pQueueFamilyProperties[0].minImageTransferGranularity.depth = 1;
        *pQueueFamilyPropertyCount = 1;
    }
}

VKAPI_ATTR void VKAPI_CALL vkGetPhysicalDeviceQueueFamilyProperties2(
        VkPhysicalDevice physicalDevice,
        uint32_t *pQueueFamilyPropertyCount,
        VkQueueFamilyProperties2 *pQueueFamilyProperties) {
    (void)physicalDevice;
    if (!pQueueFamilyPropertyCount) return;
    if (!pQueueFamilyProperties) {
        *pQueueFamilyPropertyCount = 1;
        return;
    }
    if (*pQueueFamilyPropertyCount >= 1) {
        memset(&pQueueFamilyProperties[0].queueFamilyProperties, 0, sizeof(pQueueFamilyProperties[0].queueFamilyProperties));
        pQueueFamilyProperties[0].queueFamilyProperties.queueFlags = VK_QUEUE_COMPUTE_BIT | VK_QUEUE_TRANSFER_BIT;
        pQueueFamilyProperties[0].queueFamilyProperties.queueCount = 2;
        pQueueFamilyProperties[0].queueFamilyProperties.timestampValidBits = 64;
        pQueueFamilyProperties[0].queueFamilyProperties.minImageTransferGranularity.width = 1;
        pQueueFamilyProperties[0].queueFamilyProperties.minImageTransferGranularity.height = 1;
        pQueueFamilyProperties[0].queueFamilyProperties.minImageTransferGranularity.depth = 1;
        *pQueueFamilyPropertyCount = 1;
    }
}

VKAPI_ATTR void VKAPI_CALL vkGetPhysicalDeviceMemoryProperties(
        VkPhysicalDevice physicalDevice,
        VkPhysicalDeviceMemoryProperties *pMemoryProperties) {
    (void)physicalDevice;
    if (!pMemoryProperties) return;
    memset(pMemoryProperties, 0, sizeof(*pMemoryProperties));
    pMemoryProperties->memoryTypeCount = 2;
    pMemoryProperties->memoryTypes[0].propertyFlags =
        VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT;
    pMemoryProperties->memoryTypes[0].heapIndex = 0;
    pMemoryProperties->memoryTypes[1].propertyFlags =
        VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT |
        VK_MEMORY_PROPERTY_HOST_COHERENT_BIT;
    pMemoryProperties->memoryTypes[1].heapIndex = 1;
    pMemoryProperties->memoryHeapCount = 2;
    pMemoryProperties->memoryHeaps[0].size = pdocker_vulkan_heap_size();
    pMemoryProperties->memoryHeaps[0].flags = VK_MEMORY_HEAP_DEVICE_LOCAL_BIT;
    pMemoryProperties->memoryHeaps[1].size = pdocker_vulkan_host_heap_size();
    pMemoryProperties->memoryHeaps[1].flags = 0;
}

VKAPI_ATTR void VKAPI_CALL vkGetPhysicalDeviceMemoryProperties2(
        VkPhysicalDevice physicalDevice,
        VkPhysicalDeviceMemoryProperties2 *pMemoryProperties) {
    if (!pMemoryProperties) return;
    vkGetPhysicalDeviceMemoryProperties(physicalDevice, &pMemoryProperties->memoryProperties);
}

VKAPI_ATTR VkResult VKAPI_CALL vkCreateBuffer(
        VkDevice device,
        const VkBufferCreateInfo *pCreateInfo,
        const VkAllocationCallbacks *pAllocator,
        VkBuffer *pBuffer) {
    (void)device;
    (void)pAllocator;
    if (!pCreateInfo || !pBuffer) return VK_ERROR_INITIALIZATION_FAILED;
    if (pCreateInfo->size == 0 || pCreateInfo->size > pdocker_vulkan_max_buffer_size()) {
        if (trace_allocations()) {
            fprintf(stderr,
                    "pdocker-vulkan-icd: create-buffer rejected size=%llu max=%llu\n",
                    (unsigned long long)pCreateInfo->size,
                    (unsigned long long)pdocker_vulkan_max_buffer_size());
        }
        return VK_ERROR_OUT_OF_DEVICE_MEMORY;
    }
    PdockerVkBuffer *buffer = pdocker_alloc_handle(sizeof(*buffer));
    if (!buffer) return VK_ERROR_OUT_OF_HOST_MEMORY;
    buffer->size = (size_t)pCreateInfo->size;
    buffer->requirements_alignment = PDOCKER_VK_REQUIREMENT_ALIGNMENT;
    buffer->requirements_size = align_device_size(pCreateInfo->size, buffer->requirements_alignment);
    if (trace_allocations()) {
        fprintf(stderr,
                "pdocker-vulkan-icd: create-buffer size=%zu usage=0x%x sharing=%u\n",
                buffer->size,
                (unsigned)pCreateInfo->usage,
                (unsigned)pCreateInfo->sharingMode);
    }
    *pBuffer = (VkBuffer)buffer;
    return VK_SUCCESS;
}

VKAPI_ATTR void VKAPI_CALL vkDestroyBuffer(
        VkDevice device,
        VkBuffer buffer,
        const VkAllocationCallbacks *pAllocator) {
    (void)device;
    (void)pAllocator;
    free((void *)buffer);
}

static VkResult unsupported_image_transport_result(const char *api_name) {
    if (trace_allocations() || getenv("PDOCKER_VULKAN_ICD_DEBUG")) {
        fprintf(stderr,
                "pdocker-vulkan-icd: %s is unsupported until V5 image/sampler object transport is implemented; rejecting instead of fabricating image handles\n",
                api_name ? api_name : "image-api");
    }
    return VK_ERROR_FEATURE_NOT_PRESENT;
}

VKAPI_ATTR VkResult VKAPI_CALL vkCreateImage(
        VkDevice device,
        const VkImageCreateInfo *pCreateInfo,
        const VkAllocationCallbacks *pAllocator,
        VkImage *pImage) {
    (void)device;
    (void)pAllocator;
    if (!pImage || !pCreateInfo) return VK_ERROR_INITIALIZATION_FAILED;
    *pImage = VK_NULL_HANDLE;
    if (!vulkan_v5_object_transport_enabled()) {
        return unsupported_image_transport_result("vkCreateImage");
    }
    VkDeviceSize requirements_size = estimate_image_requirement_size(pCreateInfo);
    if (requirements_size == 0 || requirements_size > pdocker_vulkan_max_buffer_size()) {
        return VK_ERROR_OUT_OF_DEVICE_MEMORY;
    }
    PdockerVkImage *image = pdocker_alloc_handle(sizeof(*image));
    if (!image) return VK_ERROR_OUT_OF_HOST_MEMORY;
    image->flags = pCreateInfo->flags;
    image->image_type = pCreateInfo->imageType;
    image->format = pCreateInfo->format;
    image->extent = pCreateInfo->extent;
    image->mip_levels = pCreateInfo->mipLevels;
    image->array_layers = pCreateInfo->arrayLayers;
    image->samples = pCreateInfo->samples;
    image->tiling = pCreateInfo->tiling;
    image->usage = pCreateInfo->usage;
    image->sharing_mode = pCreateInfo->sharingMode;
    image->initial_layout = pCreateInfo->initialLayout;
    image->current_layout = pCreateInfo->initialLayout;
    image->layout_generation = next_vulkan_object_generation();
    image->layout_mixed = false;
    image->requirements_alignment = PDOCKER_VK_REQUIREMENT_ALIGNMENT;
    image->requirements_size = requirements_size;
    image->memory_type_bits = 0x3;
    image->generation = next_vulkan_object_generation();
    if (trace_allocations()) {
        fprintf(stderr,
                "pdocker-vulkan-icd: create-image type=%u format=%u extent=%ux%ux%u mips=%u layers=%u usage=0x%x req=%llu generation=%llu\n",
                (unsigned)image->image_type,
                (unsigned)image->format,
                image->extent.width,
                image->extent.height,
                image->extent.depth,
                image->mip_levels,
                image->array_layers,
                (unsigned)image->usage,
                (unsigned long long)image->requirements_size,
                (unsigned long long)image->generation);
    }
    *pImage = (VkImage)image;
    return VK_SUCCESS;
}

VKAPI_ATTR void VKAPI_CALL vkDestroyImage(
        VkDevice device,
        VkImage image,
        const VkAllocationCallbacks *pAllocator) {
    (void)device;
    (void)pAllocator;
    free((void *)image);
}

VKAPI_ATTR void VKAPI_CALL vkGetImageMemoryRequirements(
        VkDevice device,
        VkImage image,
        VkMemoryRequirements *pMemoryRequirements) {
    (void)device;
    if (!pMemoryRequirements) return;
    PdockerVkImage *img = (PdockerVkImage *)image;
    memset(pMemoryRequirements, 0, sizeof(*pMemoryRequirements));
    pMemoryRequirements->size = img ? img->requirements_size : 0;
    pMemoryRequirements->alignment =
        img && img->requirements_alignment ? img->requirements_alignment : PDOCKER_VK_REQUIREMENT_ALIGNMENT;
    pMemoryRequirements->memoryTypeBits = img ? img->memory_type_bits : 0;
}

VKAPI_ATTR void VKAPI_CALL vkGetImageMemoryRequirements2(
        VkDevice device,
        const VkImageMemoryRequirementsInfo2 *pInfo,
        VkMemoryRequirements2 *pMemoryRequirements) {
    if (!pMemoryRequirements) return;
    vkGetImageMemoryRequirements(device, pInfo ? pInfo->image : VK_NULL_HANDLE,
                                 &pMemoryRequirements->memoryRequirements);
}

VKAPI_ATTR void VKAPI_CALL vkGetImageSubresourceLayout(
        VkDevice device,
        VkImage image,
        const VkImageSubresource *pSubresource,
        VkSubresourceLayout *pLayout) {
    (void)device;
    if (!pLayout) return;
    memset(pLayout, 0, sizeof(*pLayout));
    PdockerVkImage *img = (PdockerVkImage *)image;
    if (!img || !pSubresource ||
        pSubresource->aspectMask != VK_IMAGE_ASPECT_COLOR_BIT ||
        pSubresource->mipLevel >= img->mip_levels ||
        pSubresource->arrayLayer >= img->array_layers) {
        return;
    }
    VkExtent3D extent;
    uint64_t subresource_offset = 0;
    uint64_t mip_size = 0;
    uint64_t layer_stride = 0;
    const uint64_t bpp = conservative_format_bytes_per_pixel(img->format);
    if (!image_mip_extent(img, pSubresource->mipLevel, &extent) ||
        !image_tight_subresource_offset(img,
                                        pSubresource->mipLevel,
                                        pSubresource->arrayLayer,
                                        (VkOffset3D){0, 0, 0},
                                        &subresource_offset) ||
        !image_tight_mip_size(img, pSubresource->mipLevel, &mip_size) ||
        !image_tight_layer_stride(img, &layer_stride)) {
        return;
    }
    pLayout->offset = (VkDeviceSize)subresource_offset;
    pLayout->size = (VkDeviceSize)mip_size;
    pLayout->rowPitch = (VkDeviceSize)((uint64_t)extent.width * bpp);
    pLayout->depthPitch = (VkDeviceSize)((uint64_t)extent.width * (uint64_t)extent.height * bpp);
    pLayout->arrayPitch = (VkDeviceSize)layer_stride;
}

VKAPI_ATTR VkResult VKAPI_CALL vkBindImageMemory(
        VkDevice device,
        VkImage image,
        VkDeviceMemory memory,
        VkDeviceSize memoryOffset) {
    (void)device;
    PdockerVkImage *img = (PdockerVkImage *)image;
    PdockerVkMemory *mem = (PdockerVkMemory *)memory;
    if (!img || !mem) return VK_ERROR_INITIALIZATION_FAILED;
    VkDeviceSize alignment = img->requirements_alignment ? img->requirements_alignment : PDOCKER_VK_REQUIREMENT_ALIGNMENT;
    VkDeviceSize needed = img->requirements_size;
    if ((memoryOffset % alignment) != 0 ||
        memoryOffset > (VkDeviceSize)mem->size ||
        needed > (VkDeviceSize)mem->size - memoryOffset) {
        return VK_ERROR_OUT_OF_DEVICE_MEMORY;
    }
    img->memory = mem;
    img->memory_offset = memoryOffset;
    if (trace_allocations()) {
        fprintf(stderr,
                "pdocker-vulkan-icd: bind-image req=%llu memory_size=%zu offset=%llu generation=%llu\n",
                (unsigned long long)needed,
                mem->size,
                (unsigned long long)memoryOffset,
                (unsigned long long)img->generation);
    }
    return VK_SUCCESS;
}

VKAPI_ATTR VkResult VKAPI_CALL vkBindImageMemory2(
        VkDevice device,
        uint32_t bindInfoCount,
        const VkBindImageMemoryInfo *pBindInfos) {
    if (bindInfoCount > 0 && !pBindInfos) return VK_ERROR_INITIALIZATION_FAILED;
    for (uint32_t i = 0; i < bindInfoCount; ++i) {
        VkResult rc = vkBindImageMemory(device,
                                        pBindInfos[i].image,
                                        pBindInfos[i].memory,
                                        pBindInfos[i].memoryOffset);
        if (rc != VK_SUCCESS) return rc;
    }
    return VK_SUCCESS;
}

VKAPI_ATTR VkResult VKAPI_CALL vkCreateImageView(
        VkDevice device,
        const VkImageViewCreateInfo *pCreateInfo,
        const VkAllocationCallbacks *pAllocator,
        VkImageView *pView) {
    (void)device;
    (void)pAllocator;
    if (!pCreateInfo || !pView) return VK_ERROR_INITIALIZATION_FAILED;
    *pView = VK_NULL_HANDLE;
    if (!vulkan_v5_object_transport_enabled()) {
        return unsupported_image_transport_result("vkCreateImageView");
    }
    PdockerVkImage *image = (PdockerVkImage *)pCreateInfo->image;
    if (!image) return VK_ERROR_INITIALIZATION_FAILED;
    PdockerVkImageView *view = pdocker_alloc_handle(sizeof(*view));
    if (!view) return VK_ERROR_OUT_OF_HOST_MEMORY;
    view->image = image;
    view->view_type = pCreateInfo->viewType;
    view->format = pCreateInfo->format;
    view->components = pCreateInfo->components;
    view->subresource_range = pCreateInfo->subresourceRange;
    view->generation = next_vulkan_object_generation();
    *pView = (VkImageView)view;
    return VK_SUCCESS;
}

VKAPI_ATTR void VKAPI_CALL vkDestroyImageView(
        VkDevice device,
        VkImageView imageView,
        const VkAllocationCallbacks *pAllocator) {
    (void)device;
    (void)pAllocator;
    free((void *)imageView);
}

VKAPI_ATTR VkResult VKAPI_CALL vkCreateSampler(
        VkDevice device,
        const VkSamplerCreateInfo *pCreateInfo,
        const VkAllocationCallbacks *pAllocator,
        VkSampler *pSampler) {
    (void)device;
    (void)pAllocator;
    if (!pCreateInfo || !pSampler) return VK_ERROR_INITIALIZATION_FAILED;
    *pSampler = VK_NULL_HANDLE;
    if (!vulkan_v5_object_transport_enabled()) {
        return unsupported_image_transport_result("vkCreateSampler");
    }
    PdockerVkSampler *sampler = pdocker_alloc_handle(sizeof(*sampler));
    if (!sampler) return VK_ERROR_OUT_OF_HOST_MEMORY;
    sampler->mag_filter = pCreateInfo->magFilter;
    sampler->min_filter = pCreateInfo->minFilter;
    sampler->mipmap_mode = pCreateInfo->mipmapMode;
    sampler->address_mode_u = pCreateInfo->addressModeU;
    sampler->address_mode_v = pCreateInfo->addressModeV;
    sampler->address_mode_w = pCreateInfo->addressModeW;
    sampler->mip_lod_bias = pCreateInfo->mipLodBias;
    sampler->anisotropy_enable = pCreateInfo->anisotropyEnable;
    sampler->max_anisotropy = pCreateInfo->maxAnisotropy;
    sampler->compare_enable = pCreateInfo->compareEnable;
    sampler->compare_op = pCreateInfo->compareOp;
    sampler->min_lod = pCreateInfo->minLod;
    sampler->max_lod = pCreateInfo->maxLod;
    sampler->border_color = pCreateInfo->borderColor;
    sampler->unnormalized_coordinates = pCreateInfo->unnormalizedCoordinates;
    sampler->generation = next_vulkan_object_generation();
    *pSampler = (VkSampler)sampler;
    return VK_SUCCESS;
}

VKAPI_ATTR void VKAPI_CALL vkDestroySampler(
        VkDevice device,
        VkSampler sampler,
        const VkAllocationCallbacks *pAllocator) {
    (void)device;
    (void)pAllocator;
    free((void *)sampler);
}

VKAPI_ATTR void VKAPI_CALL vkGetBufferMemoryRequirements(
        VkDevice device,
        VkBuffer buffer,
        VkMemoryRequirements *pMemoryRequirements) {
    (void)device;
    if (!pMemoryRequirements) return;
    PdockerVkBuffer *b = (PdockerVkBuffer *)buffer;
    memset(pMemoryRequirements, 0, sizeof(*pMemoryRequirements));
    pMemoryRequirements->size = b ? b->requirements_size : 0;
    pMemoryRequirements->alignment = b && b->requirements_alignment ? b->requirements_alignment : PDOCKER_VK_REQUIREMENT_ALIGNMENT;
    pMemoryRequirements->memoryTypeBits = 0x3;
    if (trace_allocations()) {
        fprintf(stderr,
                "pdocker-vulkan-icd: buffer-requirements size=%llu alignment=%llu typeBits=0x%x\n",
                (unsigned long long)pMemoryRequirements->size,
                (unsigned long long)pMemoryRequirements->alignment,
                (unsigned)pMemoryRequirements->memoryTypeBits);
    }
}

VKAPI_ATTR void VKAPI_CALL vkGetBufferMemoryRequirements2(
        VkDevice device,
        const VkBufferMemoryRequirementsInfo2 *pInfo,
        VkMemoryRequirements2 *pMemoryRequirements) {
    if (!pInfo || !pMemoryRequirements) return;
    vkGetBufferMemoryRequirements(device, pInfo->buffer, &pMemoryRequirements->memoryRequirements);
    for (void *node = pMemoryRequirements->pNext; node;) {
        PdockerVkStructHeader header = read_vk_struct_header(node);
        if (header.sType == VK_STRUCTURE_TYPE_MEMORY_DEDICATED_REQUIREMENTS) {
            VkMemoryDedicatedRequirements *dedicated = (VkMemoryDedicatedRequirements *)node;
            dedicated->prefersDedicatedAllocation = VK_FALSE;
            dedicated->requiresDedicatedAllocation = VK_FALSE;
            if (trace_allocations()) {
                fprintf(stderr,
                        "pdocker-vulkan-icd: memory-requirements2 dedicated prefers=0 requires=0\n");
            }
        } else if (trace_allocations()) {
            fprintf(stderr,
                    "pdocker-vulkan-icd: memory-requirements2 ignored pnext sType=%d\n",
                    (int)header.sType);
        }
        node = (void *)header.pNext;
    }
}

VKAPI_ATTR VkResult VKAPI_CALL vkAllocateMemory(
        VkDevice device,
        const VkMemoryAllocateInfo *pAllocateInfo,
        const VkAllocationCallbacks *pAllocator,
        VkDeviceMemory *pMemory) {
    (void)device;
    (void)pAllocator;
    if (!pAllocateInfo || !pMemory) return VK_ERROR_INITIALIZATION_FAILED;
    if (pAllocateInfo->allocationSize == 0 ||
        pAllocateInfo->allocationSize > pdocker_vulkan_max_buffer_size()) {
        if (trace_allocations()) {
            fprintf(stderr,
                    "pdocker-vulkan-icd: allocate rejected size=%llu max=%llu type=%u\n",
                    (unsigned long long)pAllocateInfo->allocationSize,
                    (unsigned long long)pdocker_vulkan_max_buffer_size(),
                    pAllocateInfo->memoryTypeIndex);
        }
        return VK_ERROR_OUT_OF_DEVICE_MEMORY;
    }
    PdockerVkMemory *memory = pdocker_alloc_handle(sizeof(*memory));
    if (!memory) return VK_ERROR_OUT_OF_HOST_MEMORY;
    memory->size = (size_t)pAllocateInfo->allocationSize;
    memory->memory_type_index = pAllocateInfo->memoryTypeIndex;
    memory->property_flags = memory->memory_type_index == 0
        ? VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT
        : (VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT);
    trace_pnext_chain("allocate", pAllocateInfo->pNext);
    if (trace_allocations()) {
        fprintf(stderr,
                "pdocker-vulkan-icd: allocate %zu bytes type=%u flags=0x%x\n",
                memory->size,
                memory->memory_type_index,
                (unsigned)memory->property_flags);
    }
    const bool guarded = guarded_memory_enabled(memory->size, memory->property_flags);
    if (guarded) {
        memory->page_size = guarded_page_size();
        memory->page_count = (memory->size + memory->page_size - 1) / memory->page_size;
        memory->resident_pages = calloc(memory->page_count, 1);
        memory->dirty_pages = calloc(memory->page_count, 1);
        if (!memory->resident_pages || !memory->dirty_pages) {
            free(memory->resident_pages);
            free(memory->dirty_pages);
            free(memory);
            return VK_ERROR_OUT_OF_HOST_MEMORY;
        }
    }
    memory->fd = create_shared_fd(memory->size);
    if (memory->fd < 0) {
        free(memory->resident_pages);
        free(memory->dirty_pages);
        free(memory);
        return VK_ERROR_OUT_OF_HOST_MEMORY;
    }
    memory->map = mmap(
        NULL,
        memory->size,
        guarded ? PROT_NONE : (PROT_READ | PROT_WRITE),
        MAP_SHARED,
        memory->fd,
        0);
    if (memory->map == MAP_FAILED) {
        close(memory->fd);
        free(memory->resident_pages);
        free(memory->dirty_pages);
        free(memory);
        return VK_ERROR_MEMORY_MAP_FAILED;
    }
    if (guarded) {
        if (!register_guarded_memory(memory)) {
            munmap(memory->map, memory->size);
            close(memory->fd);
            free(memory->resident_pages);
            free(memory->dirty_pages);
            free(memory);
            return VK_ERROR_OUT_OF_HOST_MEMORY;
        }
        memory->guarded = true;
        if (trace_allocations()) {
            fprintf(stderr,
                    "pdocker-vulkan-icd: guarded-memory allocation=%zu page_size=%zu pages=%zu\n",
                    memory->size,
                    memory->page_size,
                    memory->page_count);
        }
    }
    *pMemory = (VkDeviceMemory)memory;
    return VK_SUCCESS;
}

VKAPI_ATTR void VKAPI_CALL vkFreeMemory(
        VkDevice device,
        VkDeviceMemory memory,
        const VkAllocationCallbacks *pAllocator) {
    (void)device;
    (void)pAllocator;
    PdockerVkMemory *m = (PdockerVkMemory *)memory;
    if (!m) return;
    unregister_guarded_memory(m);
    if (m->map && m->map != MAP_FAILED) munmap(m->map, m->size);
    if (m->fd >= 0) close(m->fd);
    free(m->resident_pages);
    free(m->dirty_pages);
    free(m);
}

VKAPI_ATTR VkResult VKAPI_CALL vkMapMemory(
        VkDevice device,
        VkDeviceMemory memory,
        VkDeviceSize offset,
        VkDeviceSize size,
        VkMemoryMapFlags flags,
        void **ppData) {
    (void)device;
    (void)size;
    (void)flags;
    if (!memory || !ppData) return VK_ERROR_MEMORY_MAP_FAILED;
    PdockerVkMemory *m = (PdockerVkMemory *)memory;
    if ((size_t)offset > m->size) return VK_ERROR_MEMORY_MAP_FAILED;
    if ((m->property_flags & VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT) == 0) {
        if (trace_allocations()) {
            fprintf(stderr,
                    "pdocker-vulkan-icd: map rejected non-host-visible type=%u allocation=%zu\n",
                    m->memory_type_index,
                    m->size);
        }
        return VK_ERROR_MEMORY_MAP_FAILED;
    }
    if (trace_allocations()) {
        fprintf(stderr,
                "pdocker-vulkan-icd: map offset=%llu size=%llu allocation=%zu\n",
                (unsigned long long)offset,
                (unsigned long long)size,
                m->size);
    }
    *ppData = (char *)m->map + offset;
    return VK_SUCCESS;
}

VKAPI_ATTR void VKAPI_CALL vkUnmapMemory(VkDevice device, VkDeviceMemory memory) {
    (void)device;
    (void)memory;
}

VKAPI_ATTR void VKAPI_CALL vkGetDeviceMemoryCommitment(
        VkDevice device,
        VkDeviceMemory memory,
        VkDeviceSize *pCommittedMemoryInBytes) {
    (void)device;
    PdockerVkMemory *m = (PdockerVkMemory *)memory;
    if (pCommittedMemoryInBytes) *pCommittedMemoryInBytes = m ? (VkDeviceSize)m->size : 0;
}

VKAPI_ATTR VkResult VKAPI_CALL vkFlushMappedMemoryRanges(
        VkDevice device,
        uint32_t memoryRangeCount,
        const VkMappedMemoryRange *pMemoryRanges) {
    (void)device;
    (void)memoryRangeCount;
    (void)pMemoryRanges;
    return VK_SUCCESS;
}

VKAPI_ATTR VkResult VKAPI_CALL vkInvalidateMappedMemoryRanges(
        VkDevice device,
        uint32_t memoryRangeCount,
        const VkMappedMemoryRange *pMemoryRanges) {
    (void)device;
    (void)memoryRangeCount;
    (void)pMemoryRanges;
    return VK_SUCCESS;
}

VKAPI_ATTR VkResult VKAPI_CALL vkBindBufferMemory(
        VkDevice device,
        VkBuffer buffer,
        VkDeviceMemory memory,
        VkDeviceSize memoryOffset) {
    (void)device;
    PdockerVkBuffer *b = (PdockerVkBuffer *)buffer;
    if (!b || !memory) return VK_ERROR_INITIALIZATION_FAILED;
    PdockerVkMemory *m = (PdockerVkMemory *)memory;
    VkDeviceSize alignment = b->requirements_alignment ? b->requirements_alignment : PDOCKER_VK_REQUIREMENT_ALIGNMENT;
    VkDeviceSize needed = b->requirements_size ? b->requirements_size : align_device_size((VkDeviceSize)b->size, alignment);
    if ((memoryOffset % alignment) != 0 ||
        memoryOffset > (VkDeviceSize)m->size ||
        needed > (VkDeviceSize)m->size - memoryOffset) {
        if (trace_allocations()) {
            fprintf(stderr,
                    "pdocker-vulkan-icd: bind-buffer rejected buffer_size=%zu req_size=%llu memory_size=%zu offset=%llu alignment=%llu\n",
                    b->size,
                    (unsigned long long)needed,
                    m->size,
                    (unsigned long long)memoryOffset,
                    (unsigned long long)alignment);
        }
        return VK_ERROR_OUT_OF_DEVICE_MEMORY;
    }
    b->memory = m;
    b->memory_offset = memoryOffset;
    if (trace_allocations()) {
        fprintf(stderr,
                "pdocker-vulkan-icd: bind-buffer buffer_size=%zu memory_size=%zu offset=%llu\n",
                b->size,
                b->memory->size,
                (unsigned long long)memoryOffset);
    }
    return VK_SUCCESS;
}

VKAPI_ATTR VkResult VKAPI_CALL vkBindBufferMemory2(
        VkDevice device,
        uint32_t bindInfoCount,
        const VkBindBufferMemoryInfo *pBindInfos) {
    for (uint32_t i = 0; i < bindInfoCount; ++i) {
        VkResult rc = vkBindBufferMemory(
            device,
            pBindInfos[i].buffer,
            pBindInfos[i].memory,
            pBindInfos[i].memoryOffset);
        if (rc != VK_SUCCESS) return rc;
    }
    return VK_SUCCESS;
}

VKAPI_ATTR VkResult VKAPI_CALL vkEnumerateDeviceExtensionProperties(
        VkPhysicalDevice physicalDevice,
        const char *pLayerName,
        uint32_t *pPropertyCount,
        VkExtensionProperties *pProperties) {
    (void)physicalDevice;
    (void)pLayerName;
    VkExtensionProperties available[13];
    uint32_t available_count = 0;
#define ADD_DEVICE_EXTENSION(name, version) do { \
        if (available_count < (uint32_t)(sizeof(available) / sizeof(available[0]))) { \
            memset(&available[available_count], 0, sizeof(available[available_count])); \
            snprintf(available[available_count].extensionName, \
                     sizeof(available[available_count].extensionName), "%s", (name)); \
            available[available_count].specVersion = (version); \
            available_count++; \
        } \
    } while (0)
    const PdockerVkAdvertisedCaps *caps = executor_advertisement_caps_if_enabled();
    if (caps ? caps->ext_16bit_storage : advertised_storage16()) {
        ADD_DEVICE_EXTENSION(VK_KHR_16BIT_STORAGE_EXTENSION_NAME, VK_KHR_16BIT_STORAGE_SPEC_VERSION);
    }
    if (caps ? caps->ext_8bit_storage : advertised_storage8()) {
        ADD_DEVICE_EXTENSION(VK_KHR_8BIT_STORAGE_EXTENSION_NAME, VK_KHR_8BIT_STORAGE_SPEC_VERSION);
    }
    if (caps ? caps->ext_shader_float16_int8 : advertised_storage8()) {
        ADD_DEVICE_EXTENSION(VK_KHR_SHADER_FLOAT16_INT8_EXTENSION_NAME, VK_KHR_SHADER_FLOAT16_INT8_SPEC_VERSION);
    }
    if (!caps || caps->ext_storage_buffer_storage_class) {
        ADD_DEVICE_EXTENSION(VK_KHR_STORAGE_BUFFER_STORAGE_CLASS_EXTENSION_NAME,
                             VK_KHR_STORAGE_BUFFER_STORAGE_CLASS_SPEC_VERSION);
    }
#ifdef VK_KHR_MAINTENANCE_4_EXTENSION_NAME
    ADD_DEVICE_EXTENSION(VK_KHR_MAINTENANCE_4_EXTENSION_NAME, VK_KHR_MAINTENANCE_4_SPEC_VERSION);
#endif
    ADD_DEVICE_EXTENSION(VK_KHR_COPY_COMMANDS_2_EXTENSION_NAME, VK_KHR_COPY_COMMANDS_2_SPEC_VERSION);
    ADD_DEVICE_EXTENSION(VK_KHR_SYNCHRONIZATION_2_EXTENSION_NAME, VK_KHR_SYNCHRONIZATION_2_SPEC_VERSION);
#ifdef VK_KHR_TIMELINE_SEMAPHORE_EXTENSION_NAME
    ADD_DEVICE_EXTENSION(VK_KHR_TIMELINE_SEMAPHORE_EXTENSION_NAME, VK_KHR_TIMELINE_SEMAPHORE_SPEC_VERSION);
#endif
    ADD_DEVICE_EXTENSION(VK_KHR_DYNAMIC_RENDERING_EXTENSION_NAME, VK_KHR_DYNAMIC_RENDERING_SPEC_VERSION);
#ifdef VK_EXT_EXTENDED_DYNAMIC_STATE_EXTENSION_NAME
    if (!caps || caps->ext_extended_dynamic_state) {
        ADD_DEVICE_EXTENSION(VK_EXT_EXTENDED_DYNAMIC_STATE_EXTENSION_NAME,
                             VK_EXT_EXTENDED_DYNAMIC_STATE_SPEC_VERSION);
    }
#endif
#ifdef VK_EXT_INDEX_TYPE_UINT8_EXTENSION_NAME
    if (caps && caps->ext_index_type_uint8 && caps->index_type_uint8.indexTypeUint8) {
        ADD_DEVICE_EXTENSION(VK_EXT_INDEX_TYPE_UINT8_EXTENSION_NAME,
                             VK_EXT_INDEX_TYPE_UINT8_SPEC_VERSION);
    }
#endif
#undef ADD_DEVICE_EXTENSION
    copy_extension_properties(available, available_count, pPropertyCount, pProperties);
    return VK_SUCCESS;
}

static bool device_extension_advertised_name(const char *name) {
    if (!name) return false;
    const PdockerVkAdvertisedCaps *caps = executor_advertisement_caps_if_enabled();
    if (strcmp(name, VK_KHR_16BIT_STORAGE_EXTENSION_NAME) == 0) {
        return caps ? caps->ext_16bit_storage : advertised_storage16();
    }
    if (strcmp(name, VK_KHR_8BIT_STORAGE_EXTENSION_NAME) == 0) {
        return caps ? caps->ext_8bit_storage : advertised_storage8();
    }
    if (strcmp(name, VK_KHR_SHADER_FLOAT16_INT8_EXTENSION_NAME) == 0) {
        return caps ? caps->ext_shader_float16_int8 : advertised_storage8();
    }
    if (strcmp(name, VK_KHR_STORAGE_BUFFER_STORAGE_CLASS_EXTENSION_NAME) == 0) {
        return !caps || caps->ext_storage_buffer_storage_class;
    }
#ifdef VK_KHR_MAINTENANCE_4_EXTENSION_NAME
    if (strcmp(name, VK_KHR_MAINTENANCE_4_EXTENSION_NAME) == 0) return true;
#endif
    if (strcmp(name, VK_KHR_COPY_COMMANDS_2_EXTENSION_NAME) == 0) return true;
    if (strcmp(name, VK_KHR_SYNCHRONIZATION_2_EXTENSION_NAME) == 0) return true;
#ifdef VK_KHR_TIMELINE_SEMAPHORE_EXTENSION_NAME
    if (strcmp(name, VK_KHR_TIMELINE_SEMAPHORE_EXTENSION_NAME) == 0) return true;
#endif
    if (strcmp(name, VK_KHR_DYNAMIC_RENDERING_EXTENSION_NAME) == 0) return true;
#ifdef VK_EXT_EXTENDED_DYNAMIC_STATE_EXTENSION_NAME
    if (strcmp(name, VK_EXT_EXTENDED_DYNAMIC_STATE_EXTENSION_NAME) == 0) {
        return !caps || caps->ext_extended_dynamic_state;
    }
#endif
#ifdef VK_EXT_INDEX_TYPE_UINT8_EXTENSION_NAME
    if (strcmp(name, VK_EXT_INDEX_TYPE_UINT8_EXTENSION_NAME) == 0) {
        return caps && caps->ext_index_type_uint8 && caps->index_type_uint8.indexTypeUint8;
    }
#endif
    return false;
}

static VkResult validate_device_extensions(const VkDeviceCreateInfo *pCreateInfo) {
    if (!pCreateInfo) return VK_SUCCESS;
    for (uint32_t i = 0; i < pCreateInfo->enabledExtensionCount; ++i) {
        const char *name = pCreateInfo->ppEnabledExtensionNames
            ? pCreateInfo->ppEnabledExtensionNames[i]
            : NULL;
        if (!device_extension_advertised_name(name)) {
            fprintf(stderr,
                    "pdocker-vulkan-icd: create-device rejected unadvertised extension %s\n",
                    name ? name : "<null>");
            return VK_ERROR_EXTENSION_NOT_PRESENT;
        }
    }
    return VK_SUCCESS;
}

VKAPI_ATTR VkResult VKAPI_CALL vkEnumerateDeviceLayerProperties(
        VkPhysicalDevice physicalDevice,
        uint32_t *pPropertyCount,
        VkLayerProperties *pProperties) {
    (void)physicalDevice;
    if (!pPropertyCount) return VK_ERROR_INITIALIZATION_FAILED;
    if (!pProperties) {
        *pPropertyCount = 0;
        return VK_SUCCESS;
    }
    *pPropertyCount = 0;
    return VK_SUCCESS;
}

VKAPI_ATTR VkResult VKAPI_CALL vkCreateDevice(
        VkPhysicalDevice physicalDevice,
        const VkDeviceCreateInfo *pCreateInfo,
        const VkAllocationCallbacks *pAllocator,
        VkDevice *pDevice) {
    (void)physicalDevice;
    (void)pAllocator;
    if (!pDevice) return VK_ERROR_INITIALIZATION_FAILED;
    *pDevice = VK_NULL_HANDLE;
    trace_device_create_features(pCreateInfo);
    VkResult extension_rc = validate_device_extensions(pCreateInfo);
    if (extension_rc != VK_SUCCESS) return extension_rc;
    uint64_t requested_feature_mask = requested_feature_mask_from_device_create_info(pCreateInfo);
    uint64_t supported_feature_mask = advertised_feature_mask();
    uint64_t unsupported_feature_mask = 0;
    if (!requested_features_supported(requested_feature_mask, supported_feature_mask,
                                      &unsupported_feature_mask)) {
        fprintf(stderr,
                "pdocker-vulkan-icd: create-device rejected unsupported feature_mask=0x%016llx supported=0x%016llx requested=0x%016llx\n",
                (unsigned long long)unsupported_feature_mask,
                (unsigned long long)supported_feature_mask,
                (unsigned long long)requested_feature_mask);
        return VK_ERROR_FEATURE_NOT_PRESENT;
    }
    PdockerVkDevice *device = calloc(1, sizeof(*device));
    if (!device) return VK_ERROR_OUT_OF_HOST_MEMORY;
    device->requested_feature_mask = requested_feature_mask;
    if (trace_allocations() || getenv("PDOCKER_VULKAN_ICD_DEBUG")) {
        fprintf(stderr,
                "pdocker-vulkan-icd: create-device requested_feature_mask=0x%016llx supported_feature_mask=0x%016llx\n",
                (unsigned long long)device->requested_feature_mask,
                (unsigned long long)supported_feature_mask);
    }
    set_loader_magic_value(device);
    *pDevice = (VkDevice)device;
    trace_icd_runtime_marker_once("create-device");
    return VK_SUCCESS;
}

VKAPI_ATTR void VKAPI_CALL vkDestroyDevice(
        VkDevice device,
        const VkAllocationCallbacks *pAllocator) {
    (void)pAllocator;
    free((void *)device);
}

VKAPI_ATTR void VKAPI_CALL vkGetDeviceQueue(
        VkDevice device,
        uint32_t queueFamilyIndex,
        uint32_t queueIndex,
        VkQueue *pQueue) {
    (void)device;
    (void)queueFamilyIndex;
    (void)queueIndex;
    if (pQueue) *pQueue = (VkQueue)&g_queue;
}

VKAPI_ATTR void VKAPI_CALL vkGetDeviceQueue2(
        VkDevice device,
        const VkDeviceQueueInfo2 *pQueueInfo,
        VkQueue *pQueue) {
    (void)pQueueInfo;
    vkGetDeviceQueue(device, 0, 0, pQueue);
}

static bool descriptor_type_supported_by_v4_transport(VkDescriptorType type) {
    return type == VK_DESCRIPTOR_TYPE_STORAGE_BUFFER ||
           type == VK_DESCRIPTOR_TYPE_STORAGE_BUFFER_DYNAMIC ||
           type == VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER ||
           type == VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER_DYNAMIC;
}

static bool descriptor_type_supported_by_v5_object_transport(VkDescriptorType type) {
    return type == VK_DESCRIPTOR_TYPE_SAMPLER ||
           type == VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER ||
           type == VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE ||
           type == VK_DESCRIPTOR_TYPE_STORAGE_IMAGE ||
           type == VK_DESCRIPTOR_TYPE_INPUT_ATTACHMENT;
}

static bool descriptor_type_is_dynamic(VkDescriptorType type) {
    return type == VK_DESCRIPTOR_TYPE_STORAGE_BUFFER_DYNAMIC ||
           type == VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER_DYNAMIC;
}

static bool descriptor_set_layout_compatible(
        const PdockerVkDescriptorSetLayout *expected,
        const PdockerVkDescriptorSetLayout *actual) {
    if (!expected || !actual) return false;
    if (expected == actual) return true;
    if (expected->storage_binding_count != actual->storage_binding_count ||
        expected->unsupported_descriptor_array != actual->unsupported_descriptor_array ||
        expected->unsupported_descriptor_type != actual->unsupported_descriptor_type) {
        return false;
    }
    for (uint32_t i = 0; i < expected->storage_binding_count; ++i) {
        if (expected->storage_binding_types[i] != actual->storage_binding_types[i] ||
            expected->storage_binding_counts[i] != actual->storage_binding_counts[i]) {
            return false;
        }
    }
    return true;
}

VKAPI_ATTR VkResult VKAPI_CALL vkCreateDescriptorSetLayout(
        VkDevice device,
        const VkDescriptorSetLayoutCreateInfo *pCreateInfo,
        const VkAllocationCallbacks *pAllocator,
        VkDescriptorSetLayout *pSetLayout) {
    (void)device;
    (void)pCreateInfo;
    (void)pAllocator;
    if (!pSetLayout) return VK_ERROR_INITIALIZATION_FAILED;
    PdockerVkDescriptorSetLayout *layout = pdocker_alloc_handle(sizeof(*layout));
    if (!layout) return VK_ERROR_OUT_OF_HOST_MEMORY;
    for (uint32_t i = 0; pCreateInfo && i < pCreateInfo->bindingCount; ++i) {
        const VkDescriptorSetLayoutBinding *binding = &pCreateInfo->pBindings[i];
        bool v4_descriptor = descriptor_type_supported_by_v4_transport(binding->descriptorType);
        bool v5_object_descriptor =
            vulkan_v5_object_transport_enabled() &&
            descriptor_type_supported_by_v5_object_transport(binding->descriptorType);
        if (!v4_descriptor && !v5_object_descriptor) {
            layout->unsupported_descriptor_type = true;
            fprintf(stderr,
                    "pdocker-vulkan-icd: descriptor type binding=%u type=%u is unsupported by current transport; rejecting instead of ignoring\n",
                    binding->binding,
                    binding->descriptorType);
            continue;
        }
        if (binding->binding >= PDOCKER_VK_MAX_STORAGE_BUFFERS) {
            layout->unsupported_descriptor_type = true;
            fprintf(stderr,
                    "pdocker-vulkan-icd: descriptor binding=%u exceeds V4 transport limit=%u; rejecting instead of truncating\n",
                    binding->binding,
                    PDOCKER_VK_MAX_STORAGE_BUFFERS);
            continue;
        }
        if ((v4_descriptor || v5_object_descriptor) && binding->binding < PDOCKER_VK_MAX_STORAGE_BUFFERS &&
            binding->binding + 1 > layout->storage_binding_count) {
            layout->storage_binding_count = binding->binding + 1;
        }
        if ((v4_descriptor || v5_object_descriptor) && binding->binding < PDOCKER_VK_MAX_STORAGE_BUFFERS) {
            layout->storage_binding_types[binding->binding] = binding->descriptorType;
            layout->storage_binding_counts[binding->binding] = binding->descriptorCount;
        }
    }
    *pSetLayout = (VkDescriptorSetLayout)layout;
    return VK_SUCCESS;
}

VKAPI_ATTR void VKAPI_CALL vkDestroyDescriptorSetLayout(
        VkDevice device,
        VkDescriptorSetLayout descriptorSetLayout,
        const VkAllocationCallbacks *pAllocator) {
    (void)device;
    (void)pAllocator;
    free((void *)descriptorSetLayout);
}

VKAPI_ATTR VkResult VKAPI_CALL vkCreatePipelineLayout(
        VkDevice device,
        const VkPipelineLayoutCreateInfo *pCreateInfo,
        const VkAllocationCallbacks *pAllocator,
        VkPipelineLayout *pPipelineLayout) {
    (void)device;
    (void)pAllocator;
    if (!pPipelineLayout) return VK_ERROR_INITIALIZATION_FAILED;
    PdockerVkPipelineLayout *layout = pdocker_alloc_handle(sizeof(*layout));
    if (!layout) return VK_ERROR_OUT_OF_HOST_MEMORY;
    layout->layout_id = next_vulkan_object_generation();
    if (pCreateInfo) {
        layout->set_layout_count = pCreateInfo->setLayoutCount;
        if (layout->set_layout_count > PDOCKER_VK_MAX_DESCRIPTOR_SETS) {
            layout->unsupported_set_layout_count = true;
            layout->set_layout_count = PDOCKER_VK_MAX_DESCRIPTOR_SETS;
        }
        for (uint32_t i = 0; i < layout->set_layout_count; ++i) {
            layout->set_layouts[i] =
                pCreateInfo->pSetLayouts
                    ? (PdockerVkDescriptorSetLayout *)pCreateInfo->pSetLayouts[i]
                    : NULL;
        }
    }
    for (uint32_t i = 0; pCreateInfo && i < pCreateInfo->pushConstantRangeCount; ++i) {
        const VkPushConstantRange *range = &pCreateInfo->pPushConstantRanges[i];
        uint64_t end64 = (uint64_t)range->offset + (uint64_t)range->size;
        if (end64 > UINT32_MAX) {
            free(layout);
            return VK_ERROR_OUT_OF_HOST_MEMORY;
        }
        uint32_t end = (uint32_t)end64;
        if (end > layout->push_constant_size) layout->push_constant_size = end;
        if (layout->push_constant_range_count < PDOCKER_VK_MAX_PUSH_CONSTANT_RANGES) {
            PdockerVkPushConstantRangeSnapshot *snapshot =
                &layout->push_constant_ranges[layout->push_constant_range_count++];
            snapshot->stage_flags = range->stageFlags;
            snapshot->offset = range->offset;
            snapshot->size = range->size;
        } else {
            layout->unsupported_push_constant_ranges = true;
        }
    }
    if (layout->push_constant_size > PDOCKER_VK_MAX_PUSH_BYTES) {
        free(layout);
        return VK_ERROR_OUT_OF_HOST_MEMORY;
    }
    *pPipelineLayout = (VkPipelineLayout)layout;
    return VK_SUCCESS;
}

VKAPI_ATTR void VKAPI_CALL vkDestroyPipelineLayout(
        VkDevice device,
        VkPipelineLayout pipelineLayout,
        const VkAllocationCallbacks *pAllocator) {
    (void)device;
    (void)pAllocator;
    free((void *)pipelineLayout);
}

VKAPI_ATTR VkResult VKAPI_CALL vkCreateDescriptorPool(
        VkDevice device,
        const VkDescriptorPoolCreateInfo *pCreateInfo,
        const VkAllocationCallbacks *pAllocator,
        VkDescriptorPool *pDescriptorPool) {
    (void)device;
    (void)pCreateInfo;
    (void)pAllocator;
    if (!pDescriptorPool) return VK_ERROR_INITIALIZATION_FAILED;
    *pDescriptorPool = (VkDescriptorPool)pdocker_alloc_handle(sizeof(PdockerHandle));
    return *pDescriptorPool ? VK_SUCCESS : VK_ERROR_OUT_OF_HOST_MEMORY;
}

VKAPI_ATTR void VKAPI_CALL vkDestroyDescriptorPool(
        VkDevice device,
        VkDescriptorPool descriptorPool,
        const VkAllocationCallbacks *pAllocator) {
    (void)device;
    (void)pAllocator;
    free((void *)descriptorPool);
}

VKAPI_ATTR VkResult VKAPI_CALL vkResetDescriptorPool(
        VkDevice device,
        VkDescriptorPool descriptorPool,
        VkDescriptorPoolResetFlags flags) {
    (void)device;
    (void)descriptorPool;
    (void)flags;
    return VK_SUCCESS;
}

VKAPI_ATTR VkResult VKAPI_CALL vkAllocateDescriptorSets(
        VkDevice device,
        const VkDescriptorSetAllocateInfo *pAllocateInfo,
        VkDescriptorSet *pDescriptorSets) {
    (void)device;
    if (!pAllocateInfo || !pDescriptorSets) return VK_ERROR_INITIALIZATION_FAILED;
    for (uint32_t i = 0; i < pAllocateInfo->descriptorSetCount; ++i) {
        PdockerVkDescriptorSet *set = pdocker_alloc_handle(sizeof(*set));
        if (!set) return VK_ERROR_OUT_OF_HOST_MEMORY;
        if (pAllocateInfo->pSetLayouts) {
            set->layout = (PdockerVkDescriptorSetLayout *)pAllocateInfo->pSetLayouts[i];
        }
        pDescriptorSets[i] = (VkDescriptorSet)set;
    }
    return VK_SUCCESS;
}

VKAPI_ATTR VkResult VKAPI_CALL vkFreeDescriptorSets(
        VkDevice device,
        VkDescriptorPool descriptorPool,
        uint32_t descriptorSetCount,
        const VkDescriptorSet *pDescriptorSets) {
    (void)device;
    (void)descriptorPool;
    for (uint32_t i = 0; i < descriptorSetCount; ++i) {
        free((void *)pDescriptorSets[i]);
    }
    return VK_SUCCESS;
}

VKAPI_ATTR void VKAPI_CALL vkUpdateDescriptorSets(
        VkDevice device,
        uint32_t descriptorWriteCount,
        const VkWriteDescriptorSet *pDescriptorWrites,
        uint32_t descriptorCopyCount,
        const VkCopyDescriptorSet *pDescriptorCopies) {
    (void)device;
    for (uint32_t i = 0; i < descriptorWriteCount; ++i) {
        const VkWriteDescriptorSet *w = &pDescriptorWrites[i];
        PdockerVkDescriptorSet *set = (PdockerVkDescriptorSet *)w->dstSet;
        if (!set) continue;
        bool v4_descriptor = descriptor_type_supported_by_v4_transport(w->descriptorType);
        bool v5_object_descriptor =
            vulkan_v5_object_transport_enabled() &&
            descriptor_type_supported_by_v5_object_transport(w->descriptorType);
        if (!v4_descriptor && !v5_object_descriptor) {
            set->unsupported_descriptor_type = true;
            fprintf(stderr,
                    "pdocker-vulkan-icd: descriptor write binding=%u type=%u is unsupported by current transport; rejecting instead of ignoring\n",
                    w->dstBinding,
                    w->descriptorType);
            continue;
        }
        bool descriptor_write_valid = true;
        for (uint32_t j = 0; j < w->descriptorCount; ++j) {
            uint32_t resolved_binding = 0;
            uint32_t resolved_array = 0;
            if (!descriptor_linear_slot(set->layout, w->dstBinding, w->dstArrayElement,
                                        j, &resolved_binding, &resolved_array)) {
                descriptor_write_valid = false;
                break;
            }
            if (set->layout &&
                set->layout->storage_binding_types[resolved_binding] != w->descriptorType) {
                descriptor_write_valid = false;
                break;
            }
        }
        if (!descriptor_write_valid) {
            set->unsupported_descriptor_array = true;
            fprintf(stderr,
                    "pdocker-vulkan-icd: descriptor linear write binding=%u array=%u count=%u exceeds transport/layout shape\n",
                    w->dstBinding,
                    w->dstArrayElement,
                    w->descriptorCount);
            continue;
        }
        if (v5_object_descriptor) {
            if (!w->pImageInfo) continue;
            for (uint32_t j = 0; j < w->descriptorCount; ++j) {
                uint32_t binding = 0;
                uint32_t array_element = 0;
                if (!descriptor_linear_slot(set->layout, w->dstBinding, w->dstArrayElement,
                                            j, &binding, &array_element)) {
                    set->unsupported_descriptor_array = true;
                    break;
                }
                PdockerVkDescriptorBinding *slot =
                    &set->storage_buffers[binding][array_element];
                const VkDescriptorImageInfo *info = &w->pImageInfo[j];
                slot->buffer = NULL;
                slot->image_view = (PdockerVkImageView *)info->imageView;
                slot->sampler = (PdockerVkSampler *)info->sampler;
                slot->image_layout = info->imageLayout;
                slot->base_offset = 0;
                slot->dynamic_offset = 0;
                slot->offset = 0;
                slot->range = 0;
                slot->descriptor_type = w->descriptorType;
                slot->dynamic = false;
                set->has_image_descriptor = descriptor_set_has_image_descriptor(set);
                if (slot->image_view && slot->image_view->image) {
                    trace_image_layout_mismatch(
                        "descriptor-update",
                        slot->image_view->image,
                        info->imageLayout);
                }
                if (trace_allocations()) {
                    fprintf(stderr,
                            "pdocker-vulkan-icd: descriptor image binding=%u array=%u type=%u view=%p sampler=%p layout=%u\n",
                            binding,
                            array_element,
                            w->descriptorType,
                            (void *)slot->image_view,
                            (void *)slot->sampler,
                            (unsigned)slot->image_layout);
                }
            }
            continue;
        }
        if (!w->pBufferInfo) continue;
        for (uint32_t j = 0; j < w->descriptorCount; ++j) {
            uint32_t binding = 0;
            uint32_t array_element = 0;
            if (!descriptor_linear_slot(set->layout, w->dstBinding, w->dstArrayElement,
                                        j, &binding, &array_element)) {
                set->unsupported_descriptor_array = true;
                break;
            }
            PdockerVkDescriptorBinding *slot =
                &set->storage_buffers[binding][array_element];
            slot->buffer = (PdockerVkBuffer *)w->pBufferInfo[j].buffer;
            slot->image_view = NULL;
            slot->sampler = NULL;
            slot->image_layout = VK_IMAGE_LAYOUT_UNDEFINED;
            slot->base_offset = w->pBufferInfo[j].offset;
            slot->dynamic_offset = 0;
            slot->offset = w->pBufferInfo[j].offset;
            slot->range = w->pBufferInfo[j].range;
            slot->descriptor_type = w->descriptorType;
            slot->dynamic = descriptor_type_is_dynamic(w->descriptorType);
            set->has_image_descriptor = descriptor_set_has_image_descriptor(set);
            if (trace_allocations()) {
                PdockerVkBuffer *buffer = slot->buffer;
                fprintf(stderr,
                        "pdocker-vulkan-icd: descriptor storage binding=%u array=%u type=%u buffer_size=%zu offset=%llu range=%llu effective=%zu\n",
                        binding,
                        array_element,
                        w->descriptorType,
                        buffer ? buffer->size : 0,
                        (unsigned long long)slot->offset,
                        (unsigned long long)slot->range,
                        descriptor_binding_size(slot));
            }
        }
    }
    for (uint32_t i = 0; i < descriptorCopyCount; ++i) {
        const VkCopyDescriptorSet *c = &pDescriptorCopies[i];
        PdockerVkDescriptorSet *src = c ? (PdockerVkDescriptorSet *)c->srcSet : NULL;
        PdockerVkDescriptorSet *dst = c ? (PdockerVkDescriptorSet *)c->dstSet : NULL;
        if (!src || !dst) continue;
        if (src->unsupported_descriptor_array || dst->unsupported_descriptor_array ||
            src->unsupported_descriptor_type || dst->unsupported_descriptor_type ||
            (src->layout && src->layout->unsupported_descriptor_type) ||
            (dst->layout && dst->layout->unsupported_descriptor_type)) {
            dst->unsupported_descriptor_array = true;
            fprintf(stderr,
                    "pdocker-vulkan-icd: descriptor copy rejected because source or destination set is already unsupported src_binding=%u dst_binding=%u count=%u\n",
                    c->srcBinding,
                    c->dstBinding,
                    c->descriptorCount);
            continue;
        }
        bool descriptor_copy_valid = true;
        for (uint32_t j = 0; j < c->descriptorCount; ++j) {
            uint32_t src_binding = 0;
            uint32_t src_array = 0;
            uint32_t dst_binding = 0;
            uint32_t dst_array = 0;
            if (!descriptor_linear_slot(src->layout, c->srcBinding, c->srcArrayElement,
                                        j, &src_binding, &src_array) ||
                !descriptor_linear_slot(dst->layout, c->dstBinding, c->dstArrayElement,
                                        j, &dst_binding, &dst_array)) {
                descriptor_copy_valid = false;
                break;
            }
        }
        if (!descriptor_copy_valid) {
            dst->unsupported_descriptor_array = true;
            fprintf(stderr,
                    "pdocker-vulkan-icd: descriptor linear copy src_binding=%u src_array=%u dst_binding=%u dst_array=%u count=%u exceeds transport/layout shape\n",
                    c->srcBinding,
                    c->srcArrayElement,
                    c->dstBinding,
                    c->dstArrayElement,
                    c->descriptorCount);
            continue;
        }
        for (uint32_t j = 0; j < c->descriptorCount; ++j) {
            uint32_t src_binding = 0;
            uint32_t src_array = 0;
            uint32_t dst_binding = 0;
            uint32_t dst_array = 0;
            if (!descriptor_linear_slot(src->layout, c->srcBinding, c->srcArrayElement,
                                        j, &src_binding, &src_array) ||
                !descriptor_linear_slot(dst->layout, c->dstBinding, c->dstArrayElement,
                                        j, &dst_binding, &dst_array)) {
                dst->unsupported_descriptor_array = true;
                break;
            }
            dst->storage_buffers[dst_binding][dst_array] =
                src->storage_buffers[src_binding][src_array];
            dst->storage_buffers[dst_binding][dst_array].dynamic_offset = 0;
            dst->storage_buffers[dst_binding][dst_array].offset =
                dst->storage_buffers[dst_binding][dst_array].base_offset;
            dst->has_image_descriptor = descriptor_set_has_image_descriptor(dst);
            if (trace_allocations()) {
                fprintf(stderr,
                        "pdocker-vulkan-icd: descriptor copy src=%u[%u] dst=%u[%u] count=%u\n",
                        src_binding,
                        src_array,
                        dst_binding,
                        dst_array,
                        c->descriptorCount);
            }
        }
    }
}

VKAPI_ATTR VkResult VKAPI_CALL vkCreateShaderModule(
        VkDevice device,
        const VkShaderModuleCreateInfo *pCreateInfo,
        const VkAllocationCallbacks *pAllocator,
        VkShaderModule *pShaderModule) {
    (void)device;
    (void)pAllocator;
    if (!pCreateInfo || !pShaderModule) return VK_ERROR_INITIALIZATION_FAILED;
    PdockerVkShaderModule *shader = pdocker_alloc_handle(sizeof(*shader));
    if (!shader) return VK_ERROR_OUT_OF_HOST_MEMORY;
    shader->code_size = pCreateInfo->codeSize;
    shader->first_word = (pCreateInfo->pCode && pCreateInfo->codeSize >= sizeof(uint32_t))
        ? pCreateInfo->pCode[0]
        : 0;
    maybe_dump_spirv(pCreateInfo);
    shader->code_fd = create_shared_fd(shader->code_size);
    if (shader->code_fd < 0) {
        free(shader);
        return VK_ERROR_OUT_OF_HOST_MEMORY;
    }
    shader->code_map = mmap(NULL, shader->code_size, PROT_READ | PROT_WRITE, MAP_SHARED, shader->code_fd, 0);
    if (shader->code_map == MAP_FAILED) {
        close(shader->code_fd);
        free(shader);
        return VK_ERROR_MEMORY_MAP_FAILED;
    }
    memcpy(shader->code_map, pCreateInfo->pCode, shader->code_size);
    *pShaderModule = (VkShaderModule)shader;
    return *pShaderModule ? VK_SUCCESS : VK_ERROR_OUT_OF_HOST_MEMORY;
}

VKAPI_ATTR void VKAPI_CALL vkDestroyShaderModule(
        VkDevice device,
        VkShaderModule shaderModule,
        const VkAllocationCallbacks *pAllocator) {
    (void)device;
    (void)pAllocator;
    PdockerVkShaderModule *shader = (PdockerVkShaderModule *)shaderModule;
    if (!shader) return;
    if (shader->code_map && shader->code_map != MAP_FAILED) munmap(shader->code_map, shader->code_size);
    if (shader->code_fd >= 0) close(shader->code_fd);
    free(shader);
}

VKAPI_ATTR VkResult VKAPI_CALL vkCreateComputePipelines(
        VkDevice device,
        VkPipelineCache pipelineCache,
        uint32_t createInfoCount,
        const VkComputePipelineCreateInfo *pCreateInfos,
        const VkAllocationCallbacks *pAllocator,
        VkPipeline *pPipelines) {
    (void)device;
    (void)pipelineCache;
    (void)pCreateInfos;
    (void)pAllocator;
    if (!pPipelines) return VK_ERROR_INITIALIZATION_FAILED;
    for (uint32_t i = 0; i < createInfoCount; ++i) {
        PdockerVkPipeline *pipeline = pdocker_alloc_handle(sizeof(*pipeline));
        if (!pipeline) return VK_ERROR_OUT_OF_HOST_MEMORY;
        pipeline->shader = (PdockerVkShaderModule *)pCreateInfos[i].stage.module;
        pipeline->layout = (PdockerVkPipelineLayout *)pCreateInfos[i].layout;
        pipeline->requested_feature_mask =
            device ? ((PdockerVkDevice *)device)->requested_feature_mask : 0;
        if (env_truthy_default("PDOCKER_GPU_ADD_FLOAT16_CAPABILITY_FOR_STORAGE16", false)) {
            /*
             * Keep strict executor validation aligned with the explicit
             * Android-driver compatibility lowering.  The lowering only adds
             * OpCapability Float16 for shaders that already contain 16-bit
             * float storage types; if requested here, the forwarded feature
             * contract must include shaderFloat16 as well.
             */
            pipeline->requested_feature_mask |= PDOCKER_VK_FEATURE_SHADER_FLOAT16;
        }
        pipeline->local_size_x = 128;
        const char *entry_name = pCreateInfos[i].stage.pName ? pCreateInfos[i].stage.pName : "main";
        snprintf(pipeline->entry_name, sizeof(pipeline->entry_name), "%s", entry_name);
        const VkSpecializationInfo *spec = pCreateInfos[i].stage.pSpecializationInfo;
        if (spec) {
            if (spec->mapEntryCount > PDOCKER_VK_MAX_SPECIALIZATION_ENTRIES ||
                spec->dataSize > PDOCKER_VK_MAX_SPECIALIZATION_BYTES) {
                pipeline->specialization_too_large = true;
            } else {
                pipeline->specialization_entry_count = spec->mapEntryCount;
                for (uint32_t j = 0; j < spec->mapEntryCount; ++j) {
                    pipeline->specialization_entries[j] = spec->pMapEntries[j];
                }
                pipeline->specialization_data_size = spec->dataSize;
                if (spec->dataSize && spec->pData) {
                    memcpy(pipeline->specialization_data, spec->pData, spec->dataSize);
                }
            }
        }
        pPipelines[i] = (VkPipeline)pipeline;
    }
    return VK_SUCCESS;
}


static void record_memory_barrier_op(
        VkCommandBuffer commandBuffer,
        VkAccessFlags2 srcAccessMask,
        VkAccessFlags2 dstAccessMask,
        VkPipelineStageFlags2 srcStageMask,
        VkPipelineStageFlags2 dstStageMask);

static void merge_render_pass_dependency_state(
        PdockerVkSubpassDependencyState *dst,
        VkPipelineStageFlags2 src_stage_mask,
        VkAccessFlags2 src_access_mask,
        VkPipelineStageFlags2 dst_stage_mask,
        VkAccessFlags2 dst_access_mask) {
    if (!dst) return;
    dst->seen = true;
    dst->src_stage_mask |= src_stage_mask;
    dst->src_access_mask |= src_access_mask;
    dst->dst_stage_mask |= dst_stage_mask;
    dst->dst_access_mask |= dst_access_mask;
}

static bool capture_single_subpass_dependency(
        PdockerVkRenderPass *rp,
        uint32_t src_subpass,
        uint32_t dst_subpass,
        VkPipelineStageFlags2 src_stage_mask,
        VkAccessFlags2 src_access_mask,
        VkPipelineStageFlags2 dst_stage_mask,
        VkAccessFlags2 dst_access_mask,
        VkDependencyFlags dependency_flags) {
    if (!rp) return false;
    const VkDependencyFlags supported_flags = VK_DEPENDENCY_BY_REGION_BIT;
    if ((dependency_flags & ~supported_flags) != 0) return false;
    if (src_subpass == VK_SUBPASS_EXTERNAL && dst_subpass == 0) {
        merge_render_pass_dependency_state(&rp->begin_dependency,
                                           src_stage_mask, src_access_mask,
                                           dst_stage_mask, dst_access_mask);
        return true;
    }
    if (src_subpass == 0 && dst_subpass == VK_SUBPASS_EXTERNAL) {
        merge_render_pass_dependency_state(&rp->end_dependency,
                                           src_stage_mask, src_access_mask,
                                           dst_stage_mask, dst_access_mask);
        return true;
    }
    return false;
}

static void capture_render_pass_dependencies(
        PdockerVkRenderPass *rp,
        uint32_t dependency_count,
        const VkSubpassDependency *dependencies) {
    for (uint32_t i = 0; rp && dependencies && i < dependency_count; ++i) {
        const VkSubpassDependency *dep = &dependencies[i];
        if (!capture_single_subpass_dependency(
                rp, dep->srcSubpass, dep->dstSubpass,
                (VkPipelineStageFlags2)dep->srcStageMask,
                (VkAccessFlags2)dep->srcAccessMask,
                (VkPipelineStageFlags2)dep->dstStageMask,
                (VkAccessFlags2)dep->dstAccessMask,
                dep->dependencyFlags)) {
            rp->subpass_overflow = true;
        }
    }
    if (rp && dependency_count > 0 && !dependencies) {
        rp->subpass_overflow = true;
    }
}

static void capture_render_pass_dependencies2(
        PdockerVkRenderPass *rp,
        uint32_t dependency_count,
        const VkSubpassDependency2 *dependencies) {
    for (uint32_t i = 0; rp && dependencies && i < dependency_count; ++i) {
        const VkSubpassDependency2 *dep = &dependencies[i];
        if (dep->pNext || dep->viewOffset != 0 ||
            !capture_single_subpass_dependency(
                rp, dep->srcSubpass, dep->dstSubpass,
                (VkPipelineStageFlags2)dep->srcStageMask,
                (VkAccessFlags2)dep->srcAccessMask,
                (VkPipelineStageFlags2)dep->dstStageMask,
                (VkAccessFlags2)dep->dstAccessMask,
                dep->dependencyFlags)) {
            rp->subpass_overflow = true;
        }
    }
    if (rp && dependency_count > 0 && !dependencies) {
        rp->subpass_overflow = true;
    }
}

static bool render_pass_subpass_can_normalize_to_dynamic_rendering(
        const PdockerVkRenderPass *rp,
        uint32_t subpass_index);

static void record_image_barrier_op(
        VkCommandBuffer commandBuffer,
        PdockerVkImage *image,
        VkImageLayout oldLayout,
        VkImageLayout newLayout,
        VkImageSubresourceRange range,
        VkAccessFlags2 srcAccessMask,
        VkAccessFlags2 dstAccessMask,
        VkPipelineStageFlags2 srcStageMask,
        VkPipelineStageFlags2 dstStageMask,
        uint32_t srcQueueFamilyIndex,
        uint32_t dstQueueFamilyIndex);

VKAPI_ATTR VkResult VKAPI_CALL vkCreateGraphicsPipelines(
        VkDevice device,
        VkPipelineCache pipelineCache,
        uint32_t createInfoCount,
        const VkGraphicsPipelineCreateInfo *pCreateInfos,
        const VkAllocationCallbacks *pAllocator,
        VkPipeline *pPipelines) {
    (void)device;
    (void)pipelineCache;
    (void)pAllocator;
    if (!pPipelines || (createInfoCount > 0 && !pCreateInfos)) {
        return VK_ERROR_INITIALIZATION_FAILED;
    }
    for (uint32_t i = 0; i < createInfoCount; ++i) {
        PdockerVkPipeline *pipeline = pdocker_alloc_handle(sizeof(*pipeline));
        if (!pipeline) return VK_ERROR_OUT_OF_HOST_MEMORY;
        pipeline->graphics = true;
        pipeline->graphics_unsupported = false;
        pipeline->line_width = 1.0f;
        const VkGraphicsPipelineCreateInfo *ci = &pCreateInfos[i];
        uint64_t captured_dynamic_state_mask = 0;
        if (ci->pDynamicState) {
            for (uint32_t d = 0; d < ci->pDynamicState->dynamicStateCount; ++d) {
                VkDynamicState state = ci->pDynamicState->pDynamicStates[d];
                captured_dynamic_state_mask |= pdocker_vk_graphics_dynamic_state_bit(state);
            }
        }
        pipeline->dynamic_state_mask = captured_dynamic_state_mask;
        pipeline->layout = (PdockerVkPipelineLayout *)ci->layout;
        pipeline->render_pass = (PdockerVkRenderPass *)ci->renderPass;
        pipeline->shader_stage_count = ci->stageCount;
        uint32_t captured_stages = clamp_u32(ci->stageCount, PDOCKER_VK_MAX_GRAPHICS_VERTEX_BINDINGS);
        if (ci->stageCount > PDOCKER_VK_MAX_GRAPHICS_VERTEX_BINDINGS) {
            pipeline->graphics_unsupported = true;
        }
        for (uint32_t stage_i = 0; stage_i < captured_stages; ++stage_i) {
            const VkPipelineShaderStageCreateInfo *stage = ci->pStages ? &ci->pStages[stage_i] : NULL;
            pipeline->shader_stage_flags |= stage ? stage->stage : 0;
            pipeline->graphics_stage_flags[stage_i] = stage ? stage->stage : 0;
            pipeline->graphics_stage_modules[stage_i] = stage ? (PdockerVkShaderModule *)stage->module : NULL;
            safe_copy_cstr(pipeline->graphics_stage_entry_names[stage_i],
                           sizeof(pipeline->graphics_stage_entry_names[stage_i]),
                           stage ? stage->pName : NULL);
            const VkSpecializationInfo *spec = stage ? stage->pSpecializationInfo : NULL;
            if (spec) {
                if (spec->mapEntryCount > PDOCKER_VK_MAX_SPECIALIZATION_ENTRIES ||
                    spec->dataSize > PDOCKER_VK_MAX_SPECIALIZATION_BYTES) {
                    pipeline->graphics_stage_specialization_too_large[stage_i] = true;
                    pipeline->graphics_unsupported = true;
                } else {
                    pipeline->graphics_stage_specialization_entry_counts[stage_i] = spec->mapEntryCount;
                    for (uint32_t spec_i = 0; spec_i < spec->mapEntryCount; ++spec_i) {
                        pipeline->graphics_stage_specialization_entries[stage_i][spec_i] =
                            spec->pMapEntries[spec_i];
                    }
                    pipeline->graphics_stage_specialization_data_sizes[stage_i] = spec->dataSize;
                    if (spec->dataSize && spec->pData) {
                        memcpy(pipeline->graphics_stage_specialization_data[stage_i],
                               spec->pData, spec->dataSize);
                    }
                }
            }
        }
        if (ci->pInputAssemblyState) {
            pipeline->topology = ci->pInputAssemblyState->topology;
            pipeline->primitive_restart_enable = ci->pInputAssemblyState->primitiveRestartEnable;
        }
        if (ci->pRasterizationState) {
            pipeline->polygon_mode = ci->pRasterizationState->polygonMode;
            pipeline->cull_mode = ci->pRasterizationState->cullMode;
            pipeline->front_face = ci->pRasterizationState->frontFace;
            pipeline->depth_clamp_enable = ci->pRasterizationState->depthClampEnable;
            pipeline->rasterizer_discard_enable = ci->pRasterizationState->rasterizerDiscardEnable;
            pipeline->depth_bias_enable = ci->pRasterizationState->depthBiasEnable;
            pipeline->depth_bias_constant_factor = ci->pRasterizationState->depthBiasConstantFactor;
            pipeline->depth_bias_clamp = ci->pRasterizationState->depthBiasClamp;
            pipeline->depth_bias_slope_factor = ci->pRasterizationState->depthBiasSlopeFactor;
            pipeline->line_width = ci->pRasterizationState->lineWidth;
        }
        if (ci->pMultisampleState) {
            pipeline->rasterization_samples = ci->pMultisampleState->rasterizationSamples;
        }
        pipeline->subpass = ci->subpass;
        pipeline->color_attachment_count = ci->pColorBlendState
            ? ci->pColorBlendState->attachmentCount
            : 0;
        if (ci->pColorBlendState) {
            const VkPipelineColorBlendStateCreateInfo *cb = ci->pColorBlendState;
            pipeline->color_blend_logic_op_enable = cb->logicOpEnable;
            pipeline->color_blend_logic_op = cb->logicOp;
            memcpy(pipeline->color_blend_constants, cb->blendConstants, sizeof(pipeline->color_blend_constants));
            if (cb->attachmentCount > PDOCKER_VK_MAX_STORAGE_BUFFERS) {
                pipeline->color_blend_attachment_overflow = true;
                pipeline->graphics_unsupported = true;
            }
            uint32_t captured_attachment_count = clamp_u32(cb->attachmentCount, PDOCKER_VK_MAX_STORAGE_BUFFERS);
            for (uint32_t a = 0; a < captured_attachment_count; ++a) {
                if (!cb->pAttachments) {
                    pipeline->graphics_unsupported = true;
                    break;
                }
                pipeline->color_blend_attachments[a] = cb->pAttachments[a];
            }
        }
        if (ci->pDepthStencilState) {
            const VkPipelineDepthStencilStateCreateInfo *ds = ci->pDepthStencilState;
            pipeline->depth_stencil_flags =
                (ds->depthTestEnable ? PDOCKER_GPU_GRAPHICS_V63_DEPTH_STENCIL_DEPTH_TEST_ENABLE : 0u) |
                (ds->depthWriteEnable ? PDOCKER_GPU_GRAPHICS_V63_DEPTH_STENCIL_DEPTH_WRITE_ENABLE : 0u) |
                (ds->depthBoundsTestEnable ? PDOCKER_GPU_GRAPHICS_V63_DEPTH_STENCIL_DEPTH_BOUNDS_TEST_ENABLE : 0u) |
                (ds->stencilTestEnable ? PDOCKER_GPU_GRAPHICS_V63_DEPTH_STENCIL_STENCIL_TEST_ENABLE : 0u);
            pipeline->depth_compare_op = ds->depthCompareOp;
            pipeline->front_stencil_state = ds->front;
            pipeline->back_stencil_state = ds->back;
            pipeline->min_depth_bounds = ds->minDepthBounds;
            pipeline->max_depth_bounds = ds->maxDepthBounds;
        }
        if (ci->pViewportState) {
            const VkPipelineViewportStateCreateInfo *vs = ci->pViewportState;
            const bool viewport_dynamic =
                (captured_dynamic_state_mask & pdocker_vk_graphics_dynamic_state_bit(VK_DYNAMIC_STATE_VIEWPORT)) != 0;
            const bool scissor_dynamic =
                (captured_dynamic_state_mask & pdocker_vk_graphics_dynamic_state_bit(VK_DYNAMIC_STATE_SCISSOR)) != 0;
            pipeline->viewport_count = vs->viewportCount;
            pipeline->scissor_count = vs->scissorCount;
            if (vs->viewportCount > PDOCKER_GPU_VULKAN_GRAPHICS_V67_MAX_VIEWPORTS_PER_PIPELINE) {
                pipeline->viewport_state_overflow = true;
                pipeline->graphics_unsupported = true;
            }
            if (vs->scissorCount > PDOCKER_GPU_VULKAN_GRAPHICS_V67_MAX_SCISSORS_PER_PIPELINE) {
                pipeline->scissor_state_overflow = true;
                pipeline->graphics_unsupported = true;
            }
            uint32_t viewport_capture_count = clamp_u32(vs->viewportCount,
                PDOCKER_GPU_VULKAN_GRAPHICS_V67_MAX_VIEWPORTS_PER_PIPELINE);
            uint32_t scissor_capture_count = clamp_u32(vs->scissorCount,
                PDOCKER_GPU_VULKAN_GRAPHICS_V67_MAX_SCISSORS_PER_PIPELINE);
            if (!viewport_dynamic && viewport_capture_count > 0) {
                if (!vs->pViewports) {
                    pipeline->graphics_unsupported = true;
                } else {
                    for (uint32_t v = 0; v < viewport_capture_count; ++v) {
                        pipeline->static_viewports[v] = vs->pViewports[v];
                    }
                }
            }
            if (!scissor_dynamic && scissor_capture_count > 0) {
                if (!vs->pScissors) {
                    pipeline->graphics_unsupported = true;
                } else {
                    for (uint32_t v = 0; v < scissor_capture_count; ++v) {
                        pipeline->static_scissors[v] = vs->pScissors[v];
                    }
                }
            }
        }
        for (const VkBaseInStructure *chain = (const VkBaseInStructure *)ci->pNext;
             chain;
             chain = (const VkBaseInStructure *)chain->pNext) {
            if (chain->sType == VK_STRUCTURE_TYPE_PIPELINE_RENDERING_CREATE_INFO) {
                const VkPipelineRenderingCreateInfo *rendering =
                    (const VkPipelineRenderingCreateInfo *)chain;
                pipeline->dynamic_rendering_pipeline = true;
                pipeline->dynamic_rendering_view_mask = rendering->viewMask;
                if (rendering->viewMask != 0) {
                    pipeline->graphics_unsupported = true;
                }
                pipeline->dynamic_rendering_color_attachment_count =
                    clamp_u32(rendering->colorAttachmentCount, PDOCKER_VK_MAX_STORAGE_BUFFERS);
                if (rendering->colorAttachmentCount > PDOCKER_VK_MAX_STORAGE_BUFFERS) {
                    pipeline->dynamic_rendering_format_overflow = true;
                    pipeline->graphics_unsupported = true;
                }
                for (uint32_t c = 0; c < pipeline->dynamic_rendering_color_attachment_count; ++c) {
                    pipeline->dynamic_rendering_color_formats[c] =
                        rendering->pColorAttachmentFormats
                            ? rendering->pColorAttachmentFormats[c]
                            : VK_FORMAT_UNDEFINED;
                }
                pipeline->dynamic_rendering_depth_format = rendering->depthAttachmentFormat;
                pipeline->dynamic_rendering_stencil_format = rendering->stencilAttachmentFormat;
            }
        }
        if (!pipeline->dynamic_rendering_pipeline && pipeline->render_pass) {
            PdockerVkRenderPass *rp = pipeline->render_pass;
            if (!render_pass_subpass_can_normalize_to_dynamic_rendering(rp, ci->subpass)) {
                pipeline->graphics_unsupported = true;
            } else {
                const PdockerVkSubpassState *subpass = &rp->subpasses[ci->subpass];
                pipeline->dynamic_rendering_pipeline = true;
                pipeline->dynamic_rendering_view_mask = 0;
                pipeline->dynamic_rendering_color_attachment_count = subpass->color_attachment_count;
                for (uint32_t c = 0; c < subpass->color_attachment_count; ++c) {
                    uint32_t attachment = subpass->color_attachments[c];
                    pipeline->dynamic_rendering_color_formats[c] =
                        attachment < rp->attachment_count ? rp->attachments[attachment].format : VK_FORMAT_UNDEFINED;
                }
                if (subpass->has_depth_stencil_attachment &&
                    subpass->depth_stencil_attachment < rp->attachment_count) {
                    VkFormat ds_format = rp->attachments[subpass->depth_stencil_attachment].format;
                    pipeline->dynamic_rendering_depth_format =
                        pdocker_vk_format_has_depth(ds_format) ? ds_format : VK_FORMAT_UNDEFINED;
                    pipeline->dynamic_rendering_stencil_format =
                        pdocker_vk_format_has_stencil(ds_format) ? ds_format : VK_FORMAT_UNDEFINED;
                } else {
                    pipeline->dynamic_rendering_depth_format = VK_FORMAT_UNDEFINED;
                    pipeline->dynamic_rendering_stencil_format = VK_FORMAT_UNDEFINED;
                }
            }
        }
        if (ci->pVertexInputState) {
            pipeline->vertex_binding_count = clamp_u32(
                ci->pVertexInputState->vertexBindingDescriptionCount,
                PDOCKER_VK_MAX_GRAPHICS_VERTEX_BINDINGS);
            if (ci->pVertexInputState->vertexBindingDescriptionCount > PDOCKER_VK_MAX_GRAPHICS_VERTEX_BINDINGS) {
                pipeline->graphics_unsupported = true;
            }
            for (uint32_t b = 0; b < pipeline->vertex_binding_count; ++b) {
                pipeline->vertex_bindings[b] = ci->pVertexInputState->pVertexBindingDescriptions[b];
            }
            pipeline->vertex_attribute_count = clamp_u32(
                ci->pVertexInputState->vertexAttributeDescriptionCount,
                PDOCKER_VK_MAX_GRAPHICS_VERTEX_ATTRIBUTES);
            if (ci->pVertexInputState->vertexAttributeDescriptionCount > PDOCKER_VK_MAX_GRAPHICS_VERTEX_ATTRIBUTES) {
                pipeline->graphics_unsupported = true;
            }
            for (uint32_t a = 0; a < pipeline->vertex_attribute_count; ++a) {
                pipeline->vertex_attributes[a] = ci->pVertexInputState->pVertexAttributeDescriptions[a];
            }
        }
        pPipelines[i] = (VkPipeline)pipeline;
    }
    return VK_SUCCESS;
}

VKAPI_ATTR void VKAPI_CALL vkDestroyPipeline(
        VkDevice device,
        VkPipeline pipeline,
        const VkAllocationCallbacks *pAllocator) {
    (void)device;
    (void)pAllocator;
    free((void *)pipeline);
}

static void capture_render_pass_subpass_state(
        PdockerVkRenderPass *rp,
        uint32_t subpass_index,
        uint32_t color_attachment_count,
        const VkAttachmentReference *color_attachments,
        const VkAttachmentReference *resolve_attachments,
        const VkAttachmentReference *depth_stencil_attachment,
        uint32_t input_attachment_count,
        uint32_t preserve_attachment_count) {
    if (!rp || subpass_index >= PDOCKER_VK_MAX_STORAGE_BUFFERS) return;
    PdockerVkSubpassState *dst = &rp->subpasses[subpass_index];
    memset(dst, 0, sizeof(*dst));
    if (input_attachment_count != 0 || preserve_attachment_count != 0) {
        dst->unsupported = true;
    }
    if (color_attachment_count > PDOCKER_VK_MAX_STORAGE_BUFFERS) {
        dst->unsupported = true;
        color_attachment_count = PDOCKER_VK_MAX_STORAGE_BUFFERS;
    }
    if (color_attachment_count > 0 && !color_attachments) {
        dst->unsupported = true;
        color_attachment_count = 0;
    }
    dst->color_attachment_count = color_attachment_count;
    for (uint32_t i = 0; i < color_attachment_count; ++i) {
        const VkAttachmentReference *color = &color_attachments[i];
        dst->color_attachments[i] = color->attachment;
        dst->color_layouts[i] = color->layout;
        dst->resolve_attachments[i] = VK_ATTACHMENT_UNUSED;
        dst->resolve_layouts[i] = VK_IMAGE_LAYOUT_UNDEFINED;
        if (color->attachment != VK_ATTACHMENT_UNUSED &&
            (color->attachment >= rp->attachment_count ||
             pdocker_vk_format_is_depth_stencil(rp->attachments[color->attachment].format))) {
            dst->unsupported = true;
        }
        if (resolve_attachments && resolve_attachments[i].attachment != VK_ATTACHMENT_UNUSED) {
            dst->resolve_attachments[i] = resolve_attachments[i].attachment;
            dst->resolve_layouts[i] = resolve_attachments[i].layout;
            if (color->attachment == VK_ATTACHMENT_UNUSED ||
                resolve_attachments[i].attachment >= rp->attachment_count ||
                pdocker_vk_format_is_depth_stencil(rp->attachments[resolve_attachments[i].attachment].format)) {
                dst->unsupported = true;
            }
        }
    }
    if (depth_stencil_attachment &&
        depth_stencil_attachment->attachment != VK_ATTACHMENT_UNUSED) {
        dst->has_depth_stencil_attachment = true;
        dst->depth_stencil_attachment = depth_stencil_attachment->attachment;
        dst->depth_stencil_layout = depth_stencil_attachment->layout;
        if (depth_stencil_attachment->attachment >= rp->attachment_count ||
            !pdocker_vk_format_is_depth_stencil(
                rp->attachments[depth_stencil_attachment->attachment].format)) {
            dst->unsupported = true;
        }
    } else {
        dst->depth_stencil_attachment = VK_ATTACHMENT_UNUSED;
        dst->depth_stencil_layout = VK_IMAGE_LAYOUT_UNDEFINED;
    }
    dst->depth_stencil_resolve_attachment = VK_ATTACHMENT_UNUSED;
    dst->depth_stencil_resolve_layout = VK_IMAGE_LAYOUT_UNDEFINED;
    dst->depth_resolve_mode = VK_RESOLVE_MODE_NONE;
    dst->stencil_resolve_mode = VK_RESOLVE_MODE_NONE;
}

static void capture_render_pass_subpass_state2(
        PdockerVkRenderPass *rp,
        uint32_t subpass_index,
        const VkSubpassDescription2 *subpass) {
    VkAttachmentReference color_refs[PDOCKER_VK_MAX_STORAGE_BUFFERS];
    VkAttachmentReference resolve_refs[PDOCKER_VK_MAX_STORAGE_BUFFERS];
    VkAttachmentReference depth_stencil_ref;
    memset(color_refs, 0, sizeof(color_refs));
    memset(resolve_refs, 0, sizeof(resolve_refs));
    memset(&depth_stencil_ref, 0, sizeof(depth_stencil_ref));
    depth_stencil_ref.attachment = VK_ATTACHMENT_UNUSED;
    if (!subpass) {
        if (rp && subpass_index < PDOCKER_VK_MAX_STORAGE_BUFFERS) {
            rp->subpasses[subpass_index].unsupported = true;
        }
        return;
    }
    bool unsupported = subpass->flags != 0 || subpass->viewMask != 0;
    const VkSubpassDescriptionDepthStencilResolve *depth_stencil_resolve = NULL;
    for (const VkBaseInStructure *chain = (const VkBaseInStructure *)subpass->pNext;
         chain;
         chain = (const VkBaseInStructure *)chain->pNext) {
        if (chain->sType == VK_STRUCTURE_TYPE_SUBPASS_DESCRIPTION_DEPTH_STENCIL_RESOLVE) {
            if (depth_stencil_resolve) {
                unsupported = true;
            }
            depth_stencil_resolve = (const VkSubpassDescriptionDepthStencilResolve *)chain;
            if (depth_stencil_resolve->pNext) {
                unsupported = true;
            }
        } else {
            unsupported = true;
        }
    }
    uint32_t color_count = subpass->colorAttachmentCount;
    uint32_t copy_count = clamp_u32(color_count, PDOCKER_VK_MAX_STORAGE_BUFFERS);
    for (uint32_t i = 0; i < copy_count; ++i) {
        if (subpass->pColorAttachments) {
            if (subpass->pColorAttachments[i].pNext ||
                subpass->pColorAttachments[i].aspectMask != 0) {
                unsupported = true;
            }
            color_refs[i].attachment = subpass->pColorAttachments[i].attachment;
            color_refs[i].layout = subpass->pColorAttachments[i].layout;
        } else {
            color_refs[i].attachment = VK_ATTACHMENT_UNUSED;
            color_refs[i].layout = VK_IMAGE_LAYOUT_UNDEFINED;
        }
        if (subpass->pResolveAttachments) {
            if (subpass->pResolveAttachments[i].pNext ||
                subpass->pResolveAttachments[i].aspectMask != 0) {
                unsupported = true;
            }
            resolve_refs[i].attachment = subpass->pResolveAttachments[i].attachment;
            resolve_refs[i].layout = subpass->pResolveAttachments[i].layout;
        } else {
            resolve_refs[i].attachment = VK_ATTACHMENT_UNUSED;
            resolve_refs[i].layout = VK_IMAGE_LAYOUT_UNDEFINED;
        }
    }
    if (subpass->pDepthStencilAttachment) {
        if (subpass->pDepthStencilAttachment->pNext ||
            subpass->pDepthStencilAttachment->aspectMask != 0) {
            unsupported = true;
        }
        depth_stencil_ref.attachment = subpass->pDepthStencilAttachment->attachment;
        depth_stencil_ref.layout = subpass->pDepthStencilAttachment->layout;
    }
    capture_render_pass_subpass_state(
        rp, subpass_index, color_count,
        subpass->pColorAttachments ? color_refs : NULL,
        subpass->pResolveAttachments ? resolve_refs : NULL,
        subpass->pDepthStencilAttachment ? &depth_stencil_ref : NULL,
        subpass->inputAttachmentCount,
        subpass->preserveAttachmentCount);
    if (depth_stencil_resolve && rp && subpass_index < PDOCKER_VK_MAX_STORAGE_BUFFERS) {
        PdockerVkSubpassState *dst = &rp->subpasses[subpass_index];
        const VkAttachmentReference2 *resolve_ref =
            depth_stencil_resolve->pDepthStencilResolveAttachment;
        dst->depth_resolve_mode = depth_stencil_resolve->depthResolveMode;
        dst->stencil_resolve_mode = depth_stencil_resolve->stencilResolveMode;
        if (resolve_ref && resolve_ref->attachment != VK_ATTACHMENT_UNUSED) {
            if (resolve_ref->pNext || resolve_ref->aspectMask != 0 ||
                resolve_ref->attachment >= rp->attachment_count ||
                !pdocker_vk_format_is_depth_stencil(
                    rp->attachments[resolve_ref->attachment].format)) {
                unsupported = true;
            }
            dst->has_depth_stencil_resolve_attachment = true;
            dst->depth_stencil_resolve_attachment = resolve_ref->attachment;
            dst->depth_stencil_resolve_layout = resolve_ref->layout;
        }
    }
    if (unsupported && rp && subpass_index < PDOCKER_VK_MAX_STORAGE_BUFFERS) {
        rp->subpasses[subpass_index].unsupported = true;
    }
}

static bool render_pass_subpass_can_normalize_to_dynamic_rendering(
        const PdockerVkRenderPass *rp,
        uint32_t subpass_index) {
    if (!rp || rp->attachment_overflow || rp->subpass_overflow ||
        rp->subpass_count != 1 || subpass_index != 0 ||
        subpass_index >= PDOCKER_VK_MAX_STORAGE_BUFFERS) {
        return false;
    }
    const PdockerVkSubpassState *subpass = &rp->subpasses[subpass_index];
    return !subpass->unsupported;
}

static bool command_buffer_begin_inheritance_supported(
        PdockerVkCommandBuffer *cmd,
        const VkCommandBufferBeginInfo *begin) {
    if (!cmd) return false;
    cmd->inherited_rendering_active = false;
    if (cmd->level != VK_COMMAND_BUFFER_LEVEL_SECONDARY) return true;
    if (!begin || !begin->pInheritanceInfo) return true;
    const VkCommandBufferInheritanceInfo *inherit = begin->pInheritanceInfo;
    if (inherit->occlusionQueryEnable || inherit->queryFlags != 0 ||
        inherit->pipelineStatistics != 0) {
        return false;
    }
    if (inherit->renderPass) {
        PdockerVkRenderPass *rp = (PdockerVkRenderPass *)inherit->renderPass;
        if (!render_pass_subpass_can_normalize_to_dynamic_rendering(rp, inherit->subpass)) {
            return false;
        }
        cmd->inherited_rendering_active = true;
    }
    for (const VkBaseInStructure *chain = (const VkBaseInStructure *)inherit->pNext;
         chain;
         chain = (const VkBaseInStructure *)chain->pNext) {
        switch (chain->sType) {
            case VK_STRUCTURE_TYPE_COMMAND_BUFFER_INHERITANCE_RENDERING_INFO: {
                const VkCommandBufferInheritanceRenderingInfo *rendering =
                    (const VkCommandBufferInheritanceRenderingInfo *)chain;
                if (rendering->flags != 0 || rendering->viewMask != 0 ||
                    rendering->colorAttachmentCount > PDOCKER_VK_MAX_STORAGE_BUFFERS) {
                    return false;
                }
                if (rendering->colorAttachmentCount > 0 ||
                    rendering->depthAttachmentFormat != VK_FORMAT_UNDEFINED ||
                    rendering->stencilAttachmentFormat != VK_FORMAT_UNDEFINED) {
                    cmd->inherited_rendering_active = true;
                }
                break;
            }
            default:
                return false;
        }
    }
    return true;
}

VKAPI_ATTR VkResult VKAPI_CALL vkCreateRenderPass(
        VkDevice device,
        const VkRenderPassCreateInfo *pCreateInfo,
        const VkAllocationCallbacks *pAllocator,
        VkRenderPass *pRenderPass) {
    (void)device;
    (void)pAllocator;
    if (!pRenderPass) return VK_ERROR_INITIALIZATION_FAILED;
    PdockerVkRenderPass *rp = pdocker_alloc_handle(sizeof(*rp));
    if (!rp) return VK_ERROR_OUT_OF_HOST_MEMORY;
    rp->attachment_count = pCreateInfo ? pCreateInfo->attachmentCount : 0;
    rp->subpass_count = pCreateInfo ? pCreateInfo->subpassCount : 0;
    if (pCreateInfo && (pCreateInfo->pNext || pCreateInfo->flags != 0)) {
        rp->subpass_overflow = true;
    }
    if (rp->attachment_count > PDOCKER_VK_MAX_STORAGE_BUFFERS) {
        rp->attachment_overflow = true;
        rp->attachment_count = PDOCKER_VK_MAX_STORAGE_BUFFERS;
    }
    if (rp->attachment_count > 0 && (!pCreateInfo || !pCreateInfo->pAttachments)) {
        rp->attachment_overflow = true;
        rp->attachment_count = 0;
    }
    for (uint32_t a = 0; pCreateInfo && a < rp->attachment_count; ++a) {
        const VkAttachmentDescription *src = &pCreateInfo->pAttachments[a];
        rp->attachments[a].format = src->format;
        rp->attachments[a].samples = src->samples;
        rp->attachments[a].load_op = src->loadOp;
        rp->attachments[a].store_op = src->storeOp;
        rp->attachments[a].stencil_load_op = src->stencilLoadOp;
        rp->attachments[a].stencil_store_op = src->stencilStoreOp;
        rp->attachments[a].initial_layout = src->initialLayout;
        rp->attachments[a].final_layout = src->finalLayout;
        if (src->flags != 0) {
            rp->subpass_overflow = true;
        }
    }
    if (pCreateInfo && pCreateInfo->subpassCount > PDOCKER_VK_MAX_STORAGE_BUFFERS) {
        rp->subpass_overflow = true;
    }
    uint32_t captured_subpasses = pCreateInfo
        ? clamp_u32(pCreateInfo->subpassCount, PDOCKER_VK_MAX_STORAGE_BUFFERS)
        : 0;
    for (uint32_t sp = 0; pCreateInfo && sp < captured_subpasses; ++sp) {
        const VkSubpassDescription *src = pCreateInfo->pSubpasses
            ? &pCreateInfo->pSubpasses[sp] : NULL;
        capture_render_pass_subpass_state(
            rp, sp, src ? src->colorAttachmentCount : 0,
            src ? src->pColorAttachments : NULL,
            src ? src->pResolveAttachments : NULL,
            src ? src->pDepthStencilAttachment : NULL,
            src ? src->inputAttachmentCount : 0,
            src ? src->preserveAttachmentCount : 0);
        if (!src || src->flags != 0) rp->subpasses[sp].unsupported = true;
    }
    if (pCreateInfo) {
        capture_render_pass_dependencies(
            rp, pCreateInfo->dependencyCount, pCreateInfo->pDependencies);
    }
    rp->generation = next_vulkan_object_generation();
    *pRenderPass = (VkRenderPass)rp;
    return VK_SUCCESS;
}

VKAPI_ATTR VkResult VKAPI_CALL vkCreateRenderPass2(
        VkDevice device,
        const VkRenderPassCreateInfo2 *pCreateInfo,
        const VkAllocationCallbacks *pAllocator,
        VkRenderPass *pRenderPass) {
    (void)device;
    (void)pAllocator;
    if (!pRenderPass) return VK_ERROR_INITIALIZATION_FAILED;
    PdockerVkRenderPass *rp = pdocker_alloc_handle(sizeof(*rp));
    if (!rp) return VK_ERROR_OUT_OF_HOST_MEMORY;
    rp->attachment_count = pCreateInfo ? pCreateInfo->attachmentCount : 0;
    rp->subpass_count = pCreateInfo ? pCreateInfo->subpassCount : 0;
    if (pCreateInfo && (pCreateInfo->pNext || pCreateInfo->flags != 0 ||
                        pCreateInfo->correlatedViewMaskCount != 0)) {
        rp->subpass_overflow = true;
    }
    if (rp->attachment_count > PDOCKER_VK_MAX_STORAGE_BUFFERS) {
        rp->attachment_overflow = true;
        rp->attachment_count = PDOCKER_VK_MAX_STORAGE_BUFFERS;
    }
    if (rp->attachment_count > 0 && (!pCreateInfo || !pCreateInfo->pAttachments)) {
        rp->attachment_overflow = true;
        rp->attachment_count = 0;
    }
    for (uint32_t a = 0; pCreateInfo && a < rp->attachment_count; ++a) {
        const VkAttachmentDescription2 *src = &pCreateInfo->pAttachments[a];
        rp->attachments[a].format = src->format;
        rp->attachments[a].samples = src->samples;
        rp->attachments[a].load_op = src->loadOp;
        rp->attachments[a].store_op = src->storeOp;
        rp->attachments[a].stencil_load_op = src->stencilLoadOp;
        rp->attachments[a].stencil_store_op = src->stencilStoreOp;
        rp->attachments[a].initial_layout = src->initialLayout;
        rp->attachments[a].final_layout = src->finalLayout;
        if (src->pNext || src->flags != 0) {
            rp->subpass_overflow = true;
        }
    }
    if (pCreateInfo && pCreateInfo->subpassCount > PDOCKER_VK_MAX_STORAGE_BUFFERS) {
        rp->subpass_overflow = true;
    }
    uint32_t captured_subpasses = pCreateInfo
        ? clamp_u32(pCreateInfo->subpassCount, PDOCKER_VK_MAX_STORAGE_BUFFERS)
        : 0;
    for (uint32_t sp = 0; pCreateInfo && sp < captured_subpasses; ++sp) {
        capture_render_pass_subpass_state2(
            rp, sp, pCreateInfo->pSubpasses ? &pCreateInfo->pSubpasses[sp] : NULL);
    }
    if (pCreateInfo) {
        capture_render_pass_dependencies2(
            rp, pCreateInfo->dependencyCount, pCreateInfo->pDependencies);
    }
    rp->generation = next_vulkan_object_generation();
    *pRenderPass = (VkRenderPass)rp;
    return VK_SUCCESS;
}

VKAPI_ATTR void VKAPI_CALL vkDestroyRenderPass(
        VkDevice device,
        VkRenderPass renderPass,
        const VkAllocationCallbacks *pAllocator) {
    (void)device;
    (void)pAllocator;
    free((void *)renderPass);
}

VKAPI_ATTR VkResult VKAPI_CALL vkCreateFramebuffer(
        VkDevice device,
        const VkFramebufferCreateInfo *pCreateInfo,
        const VkAllocationCallbacks *pAllocator,
        VkFramebuffer *pFramebuffer) {
    (void)device;
    (void)pAllocator;
    if (!pCreateInfo || !pFramebuffer) return VK_ERROR_INITIALIZATION_FAILED;
    PdockerVkFramebuffer *fb = pdocker_alloc_handle(sizeof(*fb));
    if (!fb) return VK_ERROR_OUT_OF_HOST_MEMORY;
    fb->render_pass = (PdockerVkRenderPass *)pCreateInfo->renderPass;
    fb->attachment_count = pCreateInfo->attachmentCount;
    if (fb->attachment_count > PDOCKER_VK_MAX_STORAGE_BUFFERS) {
        fb->attachment_count = PDOCKER_VK_MAX_STORAGE_BUFFERS;
    }
    for (uint32_t i = 0; i < fb->attachment_count; ++i) {
        fb->attachments[i] = pCreateInfo->pAttachments
            ? (PdockerVkImageView *)pCreateInfo->pAttachments[i]
            : NULL;
    }
    fb->width = pCreateInfo->width;
    fb->height = pCreateInfo->height;
    fb->layers = pCreateInfo->layers;
    fb->generation = next_vulkan_object_generation();
    *pFramebuffer = (VkFramebuffer)fb;
    return VK_SUCCESS;
}

VKAPI_ATTR void VKAPI_CALL vkDestroyFramebuffer(
        VkDevice device,
        VkFramebuffer framebuffer,
        const VkAllocationCallbacks *pAllocator) {
    (void)device;
    (void)pAllocator;
    free((void *)framebuffer);
}

VKAPI_ATTR void VKAPI_CALL vkGetRenderAreaGranularity(
        VkDevice device,
        VkRenderPass renderPass,
        VkExtent2D *pGranularity) {
    (void)device;
    (void)renderPass;
    if (pGranularity) {
        pGranularity->width = 1;
        pGranularity->height = 1;
    }
}

VKAPI_ATTR void VKAPI_CALL vkDestroySurfaceKHR(
        VkInstance instance,
        VkSurfaceKHR surface,
        const VkAllocationCallbacks *pAllocator) {
    (void)instance;
    (void)surface;
    (void)pAllocator;
}

VKAPI_ATTR VkResult VKAPI_CALL vkGetPhysicalDeviceSurfaceSupportKHR(
        VkPhysicalDevice physicalDevice,
        uint32_t queueFamilyIndex,
        VkSurfaceKHR surface,
        VkBool32 *pSupported) {
    (void)physicalDevice;
    (void)queueFamilyIndex;
    (void)surface;
    if (!pSupported) return VK_ERROR_INITIALIZATION_FAILED;
    *pSupported = VK_FALSE;
    return VK_SUCCESS;
}

VKAPI_ATTR VkResult VKAPI_CALL vkGetPhysicalDeviceSurfaceCapabilitiesKHR(
        VkPhysicalDevice physicalDevice,
        VkSurfaceKHR surface,
        VkSurfaceCapabilitiesKHR *pSurfaceCapabilities) {
    (void)physicalDevice;
    (void)surface;
    (void)pSurfaceCapabilities;
    trace_icd_runtime_failure("surface-unimplemented", VK_ERROR_FEATURE_NOT_PRESENT);
    return VK_ERROR_FEATURE_NOT_PRESENT;
}

VKAPI_ATTR VkResult VKAPI_CALL vkGetPhysicalDeviceSurfaceFormatsKHR(
        VkPhysicalDevice physicalDevice,
        VkSurfaceKHR surface,
        uint32_t *pSurfaceFormatCount,
        VkSurfaceFormatKHR *pSurfaceFormats) {
    (void)physicalDevice;
    (void)surface;
    (void)pSurfaceFormats;
    if (!pSurfaceFormatCount) return VK_ERROR_INITIALIZATION_FAILED;
    *pSurfaceFormatCount = 0;
    trace_icd_runtime_failure("surface-unimplemented", VK_ERROR_FEATURE_NOT_PRESENT);
    return VK_ERROR_FEATURE_NOT_PRESENT;
}

VKAPI_ATTR VkResult VKAPI_CALL vkGetPhysicalDeviceSurfacePresentModesKHR(
        VkPhysicalDevice physicalDevice,
        VkSurfaceKHR surface,
        uint32_t *pPresentModeCount,
        VkPresentModeKHR *pPresentModes) {
    (void)physicalDevice;
    (void)surface;
    (void)pPresentModes;
    if (!pPresentModeCount) return VK_ERROR_INITIALIZATION_FAILED;
    *pPresentModeCount = 0;
    trace_icd_runtime_failure("surface-unimplemented", VK_ERROR_FEATURE_NOT_PRESENT);
    return VK_ERROR_FEATURE_NOT_PRESENT;
}

VKAPI_ATTR VkResult VKAPI_CALL vkCreateSwapchainKHR(
        VkDevice device,
        const VkSwapchainCreateInfoKHR *pCreateInfo,
        const VkAllocationCallbacks *pAllocator,
        VkSwapchainKHR *pSwapchain) {
    (void)device;
    (void)pCreateInfo;
    (void)pAllocator;
    if (pSwapchain) *pSwapchain = VK_NULL_HANDLE;
    trace_icd_runtime_failure("swapchain-unimplemented", VK_ERROR_FEATURE_NOT_PRESENT);
    return VK_ERROR_FEATURE_NOT_PRESENT;
}

VKAPI_ATTR void VKAPI_CALL vkDestroySwapchainKHR(
        VkDevice device,
        VkSwapchainKHR swapchain,
        const VkAllocationCallbacks *pAllocator) {
    (void)device;
    (void)swapchain;
    (void)pAllocator;
}

VKAPI_ATTR VkResult VKAPI_CALL vkGetSwapchainImagesKHR(
        VkDevice device,
        VkSwapchainKHR swapchain,
        uint32_t *pSwapchainImageCount,
        VkImage *pSwapchainImages) {
    (void)device;
    (void)swapchain;
    (void)pSwapchainImages;
    if (!pSwapchainImageCount) return VK_ERROR_INITIALIZATION_FAILED;
    *pSwapchainImageCount = 0;
    trace_icd_runtime_failure("swapchain-unimplemented", VK_ERROR_FEATURE_NOT_PRESENT);
    return VK_ERROR_FEATURE_NOT_PRESENT;
}

VKAPI_ATTR VkResult VKAPI_CALL vkAcquireNextImageKHR(
        VkDevice device,
        VkSwapchainKHR swapchain,
        uint64_t timeout,
        VkSemaphore semaphore,
        VkFence fence,
        uint32_t *pImageIndex) {
    (void)device;
    (void)swapchain;
    (void)timeout;
    (void)semaphore;
    (void)fence;
    if (pImageIndex) *pImageIndex = 0;
    trace_icd_runtime_failure("swapchain-unimplemented", VK_ERROR_FEATURE_NOT_PRESENT);
    return VK_ERROR_FEATURE_NOT_PRESENT;
}

VKAPI_ATTR VkResult VKAPI_CALL vkAcquireNextImage2KHR(
        VkDevice device,
        const VkAcquireNextImageInfoKHR *pAcquireInfo,
        uint32_t *pImageIndex) {
    (void)device;
    (void)pAcquireInfo;
    if (pImageIndex) *pImageIndex = 0;
    trace_icd_runtime_failure("swapchain-unimplemented", VK_ERROR_FEATURE_NOT_PRESENT);
    return VK_ERROR_FEATURE_NOT_PRESENT;
}

VKAPI_ATTR VkResult VKAPI_CALL vkQueuePresentKHR(
        VkQueue queue,
        const VkPresentInfoKHR *pPresentInfo) {
    (void)queue;
    (void)pPresentInfo;
    trace_icd_runtime_failure("swapchain-unimplemented", VK_ERROR_FEATURE_NOT_PRESENT);
    return VK_ERROR_FEATURE_NOT_PRESENT;
}

VKAPI_ATTR VkResult VKAPI_CALL vkCreateCommandPool(
        VkDevice device,
        const VkCommandPoolCreateInfo *pCreateInfo,
        const VkAllocationCallbacks *pAllocator,
        VkCommandPool *pCommandPool) {
    (void)device;
    (void)pCreateInfo;
    (void)pAllocator;
    if (!pCommandPool) return VK_ERROR_INITIALIZATION_FAILED;
    *pCommandPool = (VkCommandPool)pdocker_alloc_handle(sizeof(PdockerHandle));
    return *pCommandPool ? VK_SUCCESS : VK_ERROR_OUT_OF_HOST_MEMORY;
}

VKAPI_ATTR void VKAPI_CALL vkDestroyCommandPool(
        VkDevice device,
        VkCommandPool commandPool,
        const VkAllocationCallbacks *pAllocator) {
    (void)device;
    (void)pAllocator;
    free((void *)commandPool);
}

VKAPI_ATTR VkResult VKAPI_CALL vkResetCommandPool(
        VkDevice device,
        VkCommandPool commandPool,
        VkCommandPoolResetFlags flags) {
    (void)device;
    (void)commandPool;
    (void)flags;
    return VK_SUCCESS;
}

VKAPI_ATTR VkResult VKAPI_CALL vkAllocateCommandBuffers(
        VkDevice device,
        const VkCommandBufferAllocateInfo *pAllocateInfo,
        VkCommandBuffer *pCommandBuffers) {
    (void)device;
    if (!pAllocateInfo || !pCommandBuffers) return VK_ERROR_INITIALIZATION_FAILED;
    for (uint32_t i = 0; i < pAllocateInfo->commandBufferCount; ++i) {
        PdockerVkCommandBuffer *cmd = pdocker_alloc_handle(sizeof(*cmd));
        if (!cmd) return VK_ERROR_OUT_OF_HOST_MEMORY;
        set_loader_magic_value(cmd);
        cmd->level = pAllocateInfo->level;
        pCommandBuffers[i] = (VkCommandBuffer)cmd;
    }
    return VK_SUCCESS;
}

VKAPI_ATTR void VKAPI_CALL vkFreeCommandBuffers(
        VkDevice device,
        VkCommandPool commandPool,
        uint32_t commandBufferCount,
        const VkCommandBuffer *pCommandBuffers) {
    (void)device;
    (void)commandPool;
    for (uint32_t i = 0; i < commandBufferCount; ++i) {
        PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)pCommandBuffers[i];
        clear_recorded_command_ops(cmd);
        free((void *)cmd);
    }
}

VKAPI_ATTR VkResult VKAPI_CALL vkBeginCommandBuffer(
        VkCommandBuffer commandBuffer,
        const VkCommandBufferBeginInfo *pBeginInfo) {
    PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)commandBuffer;
    if (!cmd) return VK_ERROR_INITIALIZATION_FAILED;
    clear_recorded_command_ops(cmd);
    cmd->pipeline = NULL;
    cmd->compute_pipeline = NULL;
    cmd->graphics_pipeline = NULL;
    memset(cmd->bound_set_handles, 0, sizeof(cmd->bound_set_handles));
    memset(cmd->bound_set_snapshots, 0, sizeof(cmd->bound_set_snapshots));
    memset(cmd->bound_set_used, 0, sizeof(cmd->bound_set_used));
    memset(cmd->graphics_bound_set_handles, 0, sizeof(cmd->graphics_bound_set_handles));
    memset(cmd->graphics_bound_set_snapshots, 0, sizeof(cmd->graphics_bound_set_snapshots));
    memset(cmd->graphics_bound_set_used, 0, sizeof(cmd->graphics_bound_set_used));
    cmd->dispatch_x = 0;
    cmd->dispatch_y = 0;
    cmd->dispatch_z = 0;
    memset(cmd->push_constants, 0, sizeof(cmd->push_constants));
    cmd->push_constant_size = 0;
    cmd->has_dispatch = false;
    cmd->unsupported_descriptor_set_layout = false;
    cmd->dynamic_rendering_active = false;
    cmd->inherited_rendering_active = false;
    cmd->render_pass_active = false;
    cmd->active_render_pass = NULL;
    cmd->active_framebuffer = NULL;
    memset(&cmd->active_render_area, 0, sizeof(cmd->active_render_area));
    cmd->active_rendering_flags = 0;
    cmd->active_rendering_layer_count = 0;
    cmd->active_rendering_view_mask = 0;
    cmd->active_subpass_contents = VK_SUBPASS_CONTENTS_INLINE;
    cmd->active_subpass = 0;
    cmd->active_color_attachment_count = 0;
    memset(cmd->active_color_attachments, 0, sizeof(cmd->active_color_attachments));
    memset(&cmd->active_depth_attachment, 0, sizeof(cmd->active_depth_attachment));
    memset(&cmd->active_stencil_attachment, 0, sizeof(cmd->active_stencil_attachment));
    memset(cmd->active_clear_values, 0, sizeof(cmd->active_clear_values));
    cmd->active_clear_value_count = 0;
    cmd->graphics_unsupported = false;
    memset(cmd->vertex_bindings, 0, sizeof(cmd->vertex_bindings));
    cmd->vertex_binding_count = 0;
    cmd->index_buffer = NULL;
    cmd->index_offset = 0;
    cmd->index_type = VK_INDEX_TYPE_UINT16;
    cmd->index_buffer_bound = false;
    memset(cmd->dynamic_states, 0, sizeof(cmd->dynamic_states));
    cmd->dynamic_state_count = 0;
    cmd->vertex_buffer_bound = false;
    if (!command_buffer_begin_inheritance_supported(cmd, pBeginInfo)) {
        cmd->graphics_unsupported = true;
    }
    return VK_SUCCESS;
}

VKAPI_ATTR VkResult VKAPI_CALL vkEndCommandBuffer(VkCommandBuffer commandBuffer) {
    return commandBuffer ? VK_SUCCESS : VK_ERROR_INITIALIZATION_FAILED;
}

VKAPI_ATTR VkResult VKAPI_CALL vkResetCommandBuffer(
        VkCommandBuffer commandBuffer,
        VkCommandBufferResetFlags flags) {
    (void)flags;
    return vkBeginCommandBuffer(commandBuffer, NULL);
}

VKAPI_ATTR void VKAPI_CALL vkCmdBindPipeline(
        VkCommandBuffer commandBuffer,
        VkPipelineBindPoint pipelineBindPoint,
        VkPipeline pipeline) {
    PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)commandBuffer;
    if (!cmd) return;
    if (pipelineBindPoint == VK_PIPELINE_BIND_POINT_COMPUTE) {
        cmd->compute_pipeline = (PdockerVkPipeline *)pipeline;
        cmd->pipeline = cmd->compute_pipeline;
    } else if (pipelineBindPoint == VK_PIPELINE_BIND_POINT_GRAPHICS) {
        cmd->graphics_pipeline = (PdockerVkPipeline *)pipeline;
        PdockerVkGraphicsCommandRecord record;
        memset(&record, 0, sizeof(record));
        record.command_type = PDOCKER_GPU_GRAPHICS_V6_COMMAND_BIND_PIPELINE;
        record.pipeline = cmd->graphics_pipeline;
        record.layout_id = cmd->graphics_pipeline && cmd->graphics_pipeline->layout
            ? cmd->graphics_pipeline->layout->layout_id
            : 0;
        (void)append_graphics_command_record(cmd, &record);
    } else {
        cmd->graphics_unsupported = true;
    }
}

VKAPI_ATTR void VKAPI_CALL vkCmdBeginRendering(
        VkCommandBuffer commandBuffer,
        const VkRenderingInfo *pRenderingInfo) {
    PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)commandBuffer;
    if (!cmd) return;
    cmd->dynamic_rendering_active = true;
    cmd->render_pass_active = false;
    cmd->active_render_pass = NULL;
    cmd->active_framebuffer = NULL;
    memset(cmd->active_color_attachments, 0, sizeof(cmd->active_color_attachments));
    memset(&cmd->active_depth_attachment, 0, sizeof(cmd->active_depth_attachment));
    memset(&cmd->active_stencil_attachment, 0, sizeof(cmd->active_stencil_attachment));
    cmd->active_color_attachment_count = pRenderingInfo
        ? pRenderingInfo->colorAttachmentCount
        : 0;
    if (pRenderingInfo) {
        if (pRenderingInfo->pNext) {
            cmd->graphics_unsupported = true;
        }
        if (pRenderingInfo->flags != 0 || pRenderingInfo->viewMask != 0) {
            cmd->graphics_unsupported = true;
        }
        cmd->active_render_area = pRenderingInfo->renderArea;
        cmd->active_rendering_flags = pRenderingInfo->flags;
        cmd->active_rendering_layer_count = pRenderingInfo->layerCount;
        cmd->active_rendering_view_mask = pRenderingInfo->viewMask;
        if (cmd->active_color_attachment_count > PDOCKER_VK_MAX_STORAGE_BUFFERS) {
            cmd->graphics_unsupported = true;
            cmd->active_color_attachment_count = PDOCKER_VK_MAX_STORAGE_BUFFERS;
        }
        for (uint32_t i = 0; i < cmd->active_color_attachment_count; ++i) {
            if (!copy_rendering_attachment_state(&cmd->active_color_attachments[i],
                                                 pRenderingInfo->pColorAttachments
                                                     ? &pRenderingInfo->pColorAttachments[i]
                                                     : NULL)) {
                cmd->graphics_unsupported = true;
            }
        }
        if (!copy_rendering_attachment_state(&cmd->active_depth_attachment,
                                             pRenderingInfo->pDepthAttachment)) {
            cmd->graphics_unsupported = true;
        }
        if (!copy_rendering_attachment_state(&cmd->active_stencil_attachment,
                                             pRenderingInfo->pStencilAttachment)) {
            cmd->graphics_unsupported = true;
        }
    } else {
        memset(&cmd->active_render_area, 0, sizeof(cmd->active_render_area));
        cmd->active_rendering_flags = 0;
        cmd->active_rendering_layer_count = 0;
        cmd->active_rendering_view_mask = 0;
    }
    uint32_t rendering_snapshot_index = UINT32_MAX;
    if (!append_graphics_rendering_snapshot(cmd, &rendering_snapshot_index)) return;
    PdockerVkGraphicsCommandRecord record;
    memset(&record, 0, sizeof(record));
    record.command_type = PDOCKER_GPU_GRAPHICS_V6_COMMAND_BEGIN_RENDERING;
    record.flags = (uint32_t)cmd->active_rendering_flags;
    record.descriptor_set_count = cmd->active_rendering_layer_count;
    record.dynamic_offset_count = cmd->active_rendering_view_mask;
    record.rendering_snapshot_index = rendering_snapshot_index;
    (void)append_graphics_command_record(cmd, &record);
}

VKAPI_ATTR void VKAPI_CALL vkCmdEndRendering(VkCommandBuffer commandBuffer) {
    PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)commandBuffer;
    if (!cmd) return;
    PdockerVkGraphicsCommandRecord record;
    memset(&record, 0, sizeof(record));
    record.command_type = PDOCKER_GPU_GRAPHICS_V6_COMMAND_END_RENDERING;
    (void)append_graphics_command_record(cmd, &record);
    cmd->dynamic_rendering_active = false;
    cmd->active_rendering_flags = 0;
    cmd->active_rendering_layer_count = 0;
    cmd->active_rendering_view_mask = 0;
    cmd->active_color_attachment_count = 0;
    memset(cmd->active_color_attachments, 0, sizeof(cmd->active_color_attachments));
    memset(&cmd->active_depth_attachment, 0, sizeof(cmd->active_depth_attachment));
    memset(&cmd->active_stencil_attachment, 0, sizeof(cmd->active_stencil_attachment));
}

static bool populate_render_pass_attachment_for_rendering(
        PdockerVkRenderingAttachmentState *dst,
        PdockerVkRenderPass *rp,
        PdockerVkFramebuffer *fb,
        uint32_t attachment_index,
        VkImageLayout layout,
        const VkClearValue *clear_values,
        uint32_t clear_value_count,
        bool stencil_role) {
    if (!dst || !rp || !fb || attachment_index == VK_ATTACHMENT_UNUSED ||
        attachment_index >= rp->attachment_count || attachment_index >= fb->attachment_count) {
        return false;
    }
    PdockerVkImageView *view = fb->attachments[attachment_index];
    if (!view) return false;
    const PdockerVkRenderPassAttachmentState *attachment = &rp->attachments[attachment_index];
    memset(dst, 0, sizeof(*dst));
    dst->image_view = view;
    dst->image_layout = layout;
    dst->resolve_image_view = NULL;
    dst->resolve_image_layout = VK_IMAGE_LAYOUT_UNDEFINED;
    dst->resolve_mode = VK_RESOLVE_MODE_NONE;
    dst->load_op = stencil_role ? attachment->stencil_load_op : attachment->load_op;
    dst->store_op = stencil_role ? attachment->stencil_store_op : attachment->store_op;
    if (attachment_index < clear_value_count && clear_values) {
        dst->clear_value = clear_values[attachment_index];
    }
    dst->valid = true;
    return true;
}

static VkPipelineStageFlags2 render_pass_attachment_stage_mask(bool depth_stencil) {
    return depth_stencil
        ? (VK_PIPELINE_STAGE_2_EARLY_FRAGMENT_TESTS_BIT | VK_PIPELINE_STAGE_2_LATE_FRAGMENT_TESTS_BIT)
        : VK_PIPELINE_STAGE_2_COLOR_ATTACHMENT_OUTPUT_BIT;
}

static bool render_pass_layout_is_read_only(VkImageLayout layout) {
    switch (layout) {
        case VK_IMAGE_LAYOUT_DEPTH_STENCIL_READ_ONLY_OPTIMAL:
        case VK_IMAGE_LAYOUT_DEPTH_READ_ONLY_OPTIMAL:
        case VK_IMAGE_LAYOUT_STENCIL_READ_ONLY_OPTIMAL:
        case VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL:
            return true;
        default:
            return false;
    }
}

static VkAccessFlags2 render_pass_attachment_access_mask(bool depth_stencil, bool read_only) {
    if (depth_stencil) {
        VkAccessFlags2 access = VK_ACCESS_2_DEPTH_STENCIL_ATTACHMENT_READ_BIT;
        if (!read_only) access |= VK_ACCESS_2_DEPTH_STENCIL_ATTACHMENT_WRITE_BIT;
        return access;
    }
    (void)read_only;
    return VK_ACCESS_2_COLOR_ATTACHMENT_READ_BIT | VK_ACCESS_2_COLOR_ATTACHMENT_WRITE_BIT;
}

static VkAccessFlags2 render_pass_resolve_attachment_access_mask(void) {
    return VK_ACCESS_2_COLOR_ATTACHMENT_WRITE_BIT;
}

static VkPipelineStageFlags2 render_pass_layout_stage_mask(VkImageLayout layout) {
    switch (layout) {
        case VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL:
            return VK_PIPELINE_STAGE_2_COLOR_ATTACHMENT_OUTPUT_BIT;
        case VK_IMAGE_LAYOUT_DEPTH_STENCIL_ATTACHMENT_OPTIMAL:
        case VK_IMAGE_LAYOUT_DEPTH_ATTACHMENT_OPTIMAL:
        case VK_IMAGE_LAYOUT_STENCIL_ATTACHMENT_OPTIMAL:
        case VK_IMAGE_LAYOUT_DEPTH_STENCIL_READ_ONLY_OPTIMAL:
        case VK_IMAGE_LAYOUT_DEPTH_READ_ONLY_OPTIMAL:
        case VK_IMAGE_LAYOUT_STENCIL_READ_ONLY_OPTIMAL:
            return VK_PIPELINE_STAGE_2_EARLY_FRAGMENT_TESTS_BIT |
                   VK_PIPELINE_STAGE_2_LATE_FRAGMENT_TESTS_BIT;
        case VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL:
        case VK_IMAGE_LAYOUT_GENERAL:
            return VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT;
        case VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL:
        case VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL:
            return VK_PIPELINE_STAGE_2_TRANSFER_BIT;
        default:
            return VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT;
    }
}

static VkPipelineStageFlags2 render_pass_begin_src_stage_mask(VkImageLayout old_layout) {
    return old_layout == VK_IMAGE_LAYOUT_UNDEFINED
        ? VK_PIPELINE_STAGE_2_TOP_OF_PIPE_BIT
        : VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT;
}

static VkAccessFlags2 render_pass_begin_src_access_mask(VkImageLayout old_layout) {
    return old_layout == VK_IMAGE_LAYOUT_UNDEFINED
        ? 0
        : VK_ACCESS_2_MEMORY_WRITE_BIT;
}

static VkPipelineStageFlags2 render_pass_nonzero_stage_mask(VkPipelineStageFlags2 mask) {
    return mask ? mask : VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT;
}

static VkAccessFlags2 render_pass_nonzero_access_mask(VkAccessFlags2 mask) {
    return mask ? mask : (VK_ACCESS_2_MEMORY_READ_BIT | VK_ACCESS_2_MEMORY_WRITE_BIT);
}

static VkAccessFlags2 render_pass_layout_access_mask(VkImageLayout layout) {
    switch (layout) {
        case VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL:
            return VK_ACCESS_2_COLOR_ATTACHMENT_READ_BIT |
                   VK_ACCESS_2_COLOR_ATTACHMENT_WRITE_BIT;
        case VK_IMAGE_LAYOUT_DEPTH_STENCIL_ATTACHMENT_OPTIMAL:
        case VK_IMAGE_LAYOUT_DEPTH_ATTACHMENT_OPTIMAL:
        case VK_IMAGE_LAYOUT_STENCIL_ATTACHMENT_OPTIMAL:
            return VK_ACCESS_2_DEPTH_STENCIL_ATTACHMENT_READ_BIT |
                   VK_ACCESS_2_DEPTH_STENCIL_ATTACHMENT_WRITE_BIT;
        case VK_IMAGE_LAYOUT_DEPTH_STENCIL_READ_ONLY_OPTIMAL:
        case VK_IMAGE_LAYOUT_DEPTH_READ_ONLY_OPTIMAL:
        case VK_IMAGE_LAYOUT_STENCIL_READ_ONLY_OPTIMAL:
            return VK_ACCESS_2_DEPTH_STENCIL_ATTACHMENT_READ_BIT;
        case VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL:
            return VK_ACCESS_2_SHADER_READ_BIT;
        case VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL:
            return VK_ACCESS_2_TRANSFER_READ_BIT;
        case VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL:
            return VK_ACCESS_2_TRANSFER_WRITE_BIT;
        case VK_IMAGE_LAYOUT_GENERAL:
            return VK_ACCESS_2_MEMORY_READ_BIT | VK_ACCESS_2_MEMORY_WRITE_BIT;
        default:
            return VK_ACCESS_2_MEMORY_READ_BIT | VK_ACCESS_2_MEMORY_WRITE_BIT;
    }
}

static bool append_graphics_barrier_record_for_ranges(
        PdockerVkCommandBuffer *cmd,
        uint32_t memory_barrier_first,
        uint32_t memory_barrier_count,
        uint32_t image_barrier_first,
        uint32_t image_barrier_count) {
    if (!cmd || (memory_barrier_count == 0 && image_barrier_count == 0)) return true;
    PdockerVkGraphicsCommandRecord record;
    memset(&record, 0, sizeof(record));
    record.command_type = PDOCKER_GPU_GRAPHICS_V6_COMMAND_BARRIER;
    record.memory_barrier_op_first = memory_barrier_first;
    record.memory_barrier_op_count = memory_barrier_count;
    record.image_barrier_op_first = image_barrier_first;
    record.image_barrier_op_count = image_barrier_count;
    return append_graphics_command_record(cmd, &record);
}

static bool record_render_pass_attachment_transition(
        PdockerVkCommandBuffer *cmd,
        PdockerVkImageView *view,
        VkImageLayout old_layout,
        VkImageLayout new_layout,
        VkAccessFlags2 src_access,
        VkAccessFlags2 dst_access,
        VkPipelineStageFlags2 src_stage,
        VkPipelineStageFlags2 dst_stage) {
    if (!cmd || !view || !view->image || view->image->layout_mixed) return false;
    if (old_layout == new_layout) return true;
    if (cmd->image_barrier_op_count >= PDOCKER_VK_MAX_COPY_OPS) return false;
    uint32_t before = cmd->image_barrier_op_count;
    record_image_barrier_op((VkCommandBuffer)cmd,
                            view->image,
                            old_layout,
                            new_layout,
                            view->subresource_range,
                            src_access,
                            dst_access,
                            src_stage,
                            dst_stage,
                            VK_QUEUE_FAMILY_IGNORED,
                            VK_QUEUE_FAMILY_IGNORED);
    return cmd->image_barrier_op_count == before + 1u;
}

static bool append_render_pass_begin_layout_transitions(PdockerVkCommandBuffer *cmd) {
    if (!cmd || !cmd->active_render_pass) return false;
    PdockerVkRenderPass *rp = cmd->active_render_pass;
    const PdockerVkSubpassState *subpass = &rp->subpasses[0];
    uint32_t memory_first = cmd->memory_barrier_op_count;
    if (rp->begin_dependency.seen) {
        record_memory_barrier_op((VkCommandBuffer)cmd,
                                 render_pass_nonzero_access_mask(rp->begin_dependency.src_access_mask),
                                 render_pass_nonzero_access_mask(rp->begin_dependency.dst_access_mask),
                                 render_pass_nonzero_stage_mask(rp->begin_dependency.src_stage_mask),
                                 render_pass_nonzero_stage_mask(rp->begin_dependency.dst_stage_mask));
    }
    uint32_t first = cmd->image_barrier_op_count;
    for (uint32_t c = 0; c < cmd->active_color_attachment_count; ++c) {
        PdockerVkRenderingAttachmentState *attachment = &cmd->active_color_attachments[c];
        uint32_t color_index = subpass->color_attachments[c];
        if (attachment->image_view && color_index < rp->attachment_count) {
            VkImageLayout initial_layout = rp->attachments[color_index].initial_layout;
            if (!record_render_pass_attachment_transition(
                    cmd, attachment->image_view,
                    initial_layout,
                    attachment->image_layout,
                    rp->begin_dependency.seen
                        ? render_pass_nonzero_access_mask(rp->begin_dependency.src_access_mask)
                        : render_pass_begin_src_access_mask(initial_layout),
                    render_pass_attachment_access_mask(false, false) | rp->begin_dependency.dst_access_mask,
                    rp->begin_dependency.seen
                        ? render_pass_nonzero_stage_mask(rp->begin_dependency.src_stage_mask)
                        : render_pass_begin_src_stage_mask(initial_layout),
                    render_pass_attachment_stage_mask(false) | rp->begin_dependency.dst_stage_mask)) {
                return false;
            }
        }
        uint32_t resolve_index = subpass->resolve_attachments[c];
        if (attachment->resolve_image_view && resolve_index < rp->attachment_count) {
            VkImageLayout initial_layout = rp->attachments[resolve_index].initial_layout;
            if (!record_render_pass_attachment_transition(
                    cmd, attachment->resolve_image_view,
                    initial_layout,
                    attachment->resolve_image_layout,
                    rp->begin_dependency.seen
                        ? render_pass_nonzero_access_mask(rp->begin_dependency.src_access_mask)
                        : render_pass_begin_src_access_mask(initial_layout),
                    render_pass_resolve_attachment_access_mask() | rp->begin_dependency.dst_access_mask,
                    rp->begin_dependency.seen
                        ? render_pass_nonzero_stage_mask(rp->begin_dependency.src_stage_mask)
                        : render_pass_begin_src_stage_mask(initial_layout),
                    render_pass_attachment_stage_mask(false) | rp->begin_dependency.dst_stage_mask)) {
                return false;
            }
        }
    }
    if (subpass->has_depth_stencil_attachment &&
        subpass->depth_stencil_attachment < rp->attachment_count) {
        VkImageLayout initial_layout = rp->attachments[subpass->depth_stencil_attachment].initial_layout;
        if (cmd->active_depth_attachment.image_view &&
            !record_render_pass_attachment_transition(
                cmd, cmd->active_depth_attachment.image_view,
                initial_layout,
                cmd->active_depth_attachment.image_layout,
                rp->begin_dependency.seen
                    ? render_pass_nonzero_access_mask(rp->begin_dependency.src_access_mask)
                    : render_pass_begin_src_access_mask(initial_layout),
                render_pass_attachment_access_mask(
                    true, render_pass_layout_is_read_only(cmd->active_depth_attachment.image_layout)) |
                    rp->begin_dependency.dst_access_mask,
                rp->begin_dependency.seen
                    ? render_pass_nonzero_stage_mask(rp->begin_dependency.src_stage_mask)
                    : render_pass_begin_src_stage_mask(initial_layout),
                render_pass_attachment_stage_mask(true) | rp->begin_dependency.dst_stage_mask)) {
            return false;
        }
        if (cmd->active_stencil_attachment.image_view &&
            cmd->active_stencil_attachment.image_view != cmd->active_depth_attachment.image_view &&
            !record_render_pass_attachment_transition(
                cmd, cmd->active_stencil_attachment.image_view,
                initial_layout,
                cmd->active_stencil_attachment.image_layout,
                rp->begin_dependency.seen
                    ? render_pass_nonzero_access_mask(rp->begin_dependency.src_access_mask)
                    : render_pass_begin_src_access_mask(initial_layout),
                render_pass_attachment_access_mask(
                    true, render_pass_layout_is_read_only(cmd->active_stencil_attachment.image_layout)) |
                    rp->begin_dependency.dst_access_mask,
                rp->begin_dependency.seen
                    ? render_pass_nonzero_stage_mask(rp->begin_dependency.src_stage_mask)
                    : render_pass_begin_src_stage_mask(initial_layout),
                render_pass_attachment_stage_mask(true) | rp->begin_dependency.dst_stage_mask)) {
            return false;
        }
        if (subpass->has_depth_stencil_resolve_attachment &&
            subpass->depth_stencil_resolve_attachment < rp->attachment_count) {
            VkImageLayout resolve_initial_layout =
                rp->attachments[subpass->depth_stencil_resolve_attachment].initial_layout;
            PdockerVkImageView *resolve_view = cmd->active_depth_attachment.resolve_image_view
                ? cmd->active_depth_attachment.resolve_image_view
                : cmd->active_stencil_attachment.resolve_image_view;
            if (resolve_view && !record_render_pass_attachment_transition(
                    cmd, resolve_view,
                    resolve_initial_layout,
                    subpass->depth_stencil_resolve_layout,
                    rp->begin_dependency.seen
                        ? render_pass_nonzero_access_mask(rp->begin_dependency.src_access_mask)
                        : render_pass_begin_src_access_mask(resolve_initial_layout),
                    VK_ACCESS_2_DEPTH_STENCIL_ATTACHMENT_WRITE_BIT |
                        rp->begin_dependency.dst_access_mask,
                    rp->begin_dependency.seen
                        ? render_pass_nonzero_stage_mask(rp->begin_dependency.src_stage_mask)
                        : render_pass_begin_src_stage_mask(resolve_initial_layout),
                    render_pass_attachment_stage_mask(true) | rp->begin_dependency.dst_stage_mask)) {
                return false;
            }
        }
    }
    return append_graphics_barrier_record_for_ranges(
        cmd, memory_first, cmd->memory_barrier_op_count - memory_first,
        first, cmd->image_barrier_op_count - first);
}

static bool append_render_pass_end_layout_transitions(PdockerVkCommandBuffer *cmd) {
    if (!cmd || !cmd->active_render_pass) return false;
    PdockerVkRenderPass *rp = cmd->active_render_pass;
    uint32_t memory_first = cmd->memory_barrier_op_count;
    if (rp->end_dependency.seen) {
        record_memory_barrier_op((VkCommandBuffer)cmd,
                                 render_pass_nonzero_access_mask(rp->end_dependency.src_access_mask),
                                 render_pass_nonzero_access_mask(rp->end_dependency.dst_access_mask),
                                 render_pass_nonzero_stage_mask(rp->end_dependency.src_stage_mask),
                                 render_pass_nonzero_stage_mask(rp->end_dependency.dst_stage_mask));
    }
    uint32_t first = cmd->image_barrier_op_count;
    for (uint32_t c = 0; c < cmd->active_color_attachment_count; ++c) {
        PdockerVkRenderingAttachmentState *attachment = &cmd->active_color_attachments[c];
        const PdockerVkSubpassState *subpass = &rp->subpasses[0];
        uint32_t color_index = subpass->color_attachments[c];
        if (attachment->image_view && color_index < rp->attachment_count &&
            !record_render_pass_attachment_transition(
                cmd, attachment->image_view,
                attachment->image_layout,
                rp->attachments[color_index].final_layout,
                render_pass_attachment_access_mask(false, false),
                render_pass_layout_access_mask(rp->attachments[color_index].final_layout),
                render_pass_attachment_stage_mask(false),
                render_pass_layout_stage_mask(rp->attachments[color_index].final_layout))) {
            return false;
        }
        uint32_t resolve_index = subpass->resolve_attachments[c];
        if (attachment->resolve_image_view && resolve_index < rp->attachment_count &&
            !record_render_pass_attachment_transition(
                cmd, attachment->resolve_image_view,
                attachment->resolve_image_layout,
                rp->attachments[resolve_index].final_layout,
                render_pass_resolve_attachment_access_mask(),
                render_pass_layout_access_mask(rp->attachments[resolve_index].final_layout),
                render_pass_attachment_stage_mask(false),
                render_pass_layout_stage_mask(rp->attachments[resolve_index].final_layout))) {
            return false;
        }
    }
    const PdockerVkSubpassState *subpass = &rp->subpasses[0];
    if (subpass->has_depth_stencil_attachment &&
        subpass->depth_stencil_attachment < rp->attachment_count) {
        VkImageLayout final_layout = rp->attachments[subpass->depth_stencil_attachment].final_layout;
        if (cmd->active_depth_attachment.image_view &&
            !record_render_pass_attachment_transition(
                cmd, cmd->active_depth_attachment.image_view,
                cmd->active_depth_attachment.image_layout,
                final_layout,
                render_pass_attachment_access_mask(
                    true, render_pass_layout_is_read_only(cmd->active_depth_attachment.image_layout)),
                render_pass_layout_access_mask(final_layout),
                render_pass_attachment_stage_mask(true),
                render_pass_layout_stage_mask(final_layout))) {
            return false;
        }
        if (cmd->active_stencil_attachment.image_view &&
            cmd->active_stencil_attachment.image_view != cmd->active_depth_attachment.image_view &&
            !record_render_pass_attachment_transition(
                cmd, cmd->active_stencil_attachment.image_view,
                cmd->active_stencil_attachment.image_layout,
                final_layout,
                render_pass_attachment_access_mask(
                    true, render_pass_layout_is_read_only(cmd->active_stencil_attachment.image_layout)),
                render_pass_layout_access_mask(final_layout),
                render_pass_attachment_stage_mask(true),
                render_pass_layout_stage_mask(final_layout))) {
            return false;
        }
        if (subpass->has_depth_stencil_resolve_attachment &&
            subpass->depth_stencil_resolve_attachment < rp->attachment_count) {
            VkImageLayout resolve_final_layout =
                rp->attachments[subpass->depth_stencil_resolve_attachment].final_layout;
            PdockerVkImageView *resolve_view = cmd->active_depth_attachment.resolve_image_view
                ? cmd->active_depth_attachment.resolve_image_view
                : cmd->active_stencil_attachment.resolve_image_view;
            if (resolve_view && !record_render_pass_attachment_transition(
                    cmd, resolve_view,
                    subpass->depth_stencil_resolve_layout,
                    resolve_final_layout,
                    VK_ACCESS_2_DEPTH_STENCIL_ATTACHMENT_WRITE_BIT,
                    render_pass_layout_access_mask(resolve_final_layout),
                    render_pass_attachment_stage_mask(true),
                    render_pass_layout_stage_mask(resolve_final_layout))) {
                return false;
            }
        }
    }
    return append_graphics_barrier_record_for_ranges(
        cmd, memory_first, cmd->memory_barrier_op_count - memory_first,
        first, cmd->image_barrier_op_count - first);
}

static bool populate_single_subpass_render_pass_rendering_state(
        PdockerVkCommandBuffer *cmd,
        const VkRenderPassBeginInfo *begin,
        VkSubpassContents contents) {
    if (!cmd || !begin || contents != VK_SUBPASS_CONTENTS_INLINE) return false;
    PdockerVkRenderPass *rp = (PdockerVkRenderPass *)begin->renderPass;
    PdockerVkFramebuffer *fb = (PdockerVkFramebuffer *)begin->framebuffer;
    if (!render_pass_subpass_can_normalize_to_dynamic_rendering(rp, 0) || !fb ||
        fb->render_pass != rp || fb->attachment_count < rp->attachment_count) {
        return false;
    }
    const PdockerVkSubpassState *subpass = &rp->subpasses[0];
    memset(cmd->active_color_attachments, 0, sizeof(cmd->active_color_attachments));
    memset(&cmd->active_depth_attachment, 0, sizeof(cmd->active_depth_attachment));
    memset(&cmd->active_stencil_attachment, 0, sizeof(cmd->active_stencil_attachment));
    cmd->active_color_attachment_count = subpass->color_attachment_count;
    for (uint32_t c = 0; c < subpass->color_attachment_count; ++c) {
        if (subpass->color_attachments[c] != VK_ATTACHMENT_UNUSED) {
            if (!populate_render_pass_attachment_for_rendering(
                    &cmd->active_color_attachments[c], rp, fb,
                    subpass->color_attachments[c], subpass->color_layouts[c],
                    begin->pClearValues, begin->clearValueCount, false)) {
                return false;
            }
        }
        if (subpass->resolve_attachments[c] != VK_ATTACHMENT_UNUSED) {
            uint32_t resolve_index = subpass->resolve_attachments[c];
            if (resolve_index >= fb->attachment_count || !fb->attachments[resolve_index]) return false;
            cmd->active_color_attachments[c].resolve_image_view = fb->attachments[resolve_index];
            cmd->active_color_attachments[c].resolve_image_layout = subpass->resolve_layouts[c];
            cmd->active_color_attachments[c].resolve_mode = VK_RESOLVE_MODE_AVERAGE_BIT;
        }
    }
    if (subpass->has_depth_stencil_attachment) {
        uint32_t ds_index = subpass->depth_stencil_attachment;
        if (ds_index >= rp->attachment_count) return false;
        VkFormat ds_format = rp->attachments[ds_index].format;
        if (pdocker_vk_format_has_depth(ds_format) &&
            !populate_render_pass_attachment_for_rendering(
                &cmd->active_depth_attachment, rp, fb, ds_index,
                subpass->depth_stencil_layout, begin->pClearValues, begin->clearValueCount, false)) {
            return false;
        }
        if (pdocker_vk_format_has_stencil(ds_format) &&
            !populate_render_pass_attachment_for_rendering(
                &cmd->active_stencil_attachment, rp, fb, ds_index,
                subpass->depth_stencil_layout, begin->pClearValues, begin->clearValueCount, true)) {
            return false;
        }
        if (subpass->has_depth_stencil_resolve_attachment) {
            uint32_t resolve_index = subpass->depth_stencil_resolve_attachment;
            if (resolve_index >= fb->attachment_count || !fb->attachments[resolve_index]) return false;
            VkFormat resolve_format = rp->attachments[resolve_index].format;
            if (!pdocker_vk_format_is_depth_stencil(resolve_format)) return false;
            if (cmd->active_depth_attachment.valid &&
                subpass->depth_resolve_mode != VK_RESOLVE_MODE_NONE) {
                cmd->active_depth_attachment.resolve_image_view = fb->attachments[resolve_index];
                cmd->active_depth_attachment.resolve_image_layout =
                    subpass->depth_stencil_resolve_layout;
                cmd->active_depth_attachment.resolve_mode = subpass->depth_resolve_mode;
            }
            if (cmd->active_stencil_attachment.valid &&
                subpass->stencil_resolve_mode != VK_RESOLVE_MODE_NONE) {
                cmd->active_stencil_attachment.resolve_image_view = fb->attachments[resolve_index];
                cmd->active_stencil_attachment.resolve_image_layout =
                    subpass->depth_stencil_resolve_layout;
                cmd->active_stencil_attachment.resolve_mode = subpass->stencil_resolve_mode;
            }
        }
    }
    cmd->active_render_area = begin->renderArea;
    cmd->active_rendering_flags = 0;
    cmd->active_rendering_layer_count = fb->layers ? fb->layers : 1;
    cmd->active_rendering_view_mask = 0;
    return true;
}

static bool append_normalized_render_pass_begin(
        PdockerVkCommandBuffer *cmd,
        const VkRenderPassBeginInfo *begin,
        VkSubpassContents contents) {
    if (!populate_single_subpass_render_pass_rendering_state(cmd, begin, contents)) {
        if (cmd) cmd->graphics_unsupported = true;
        return false;
    }
    cmd->active_render_pass = begin ? (PdockerVkRenderPass *)begin->renderPass : NULL;
    cmd->active_framebuffer = begin ? (PdockerVkFramebuffer *)begin->framebuffer : NULL;
    if (!append_render_pass_begin_layout_transitions(cmd)) return false;
    uint32_t rendering_snapshot_index = UINT32_MAX;
    if (!append_graphics_rendering_snapshot(cmd, &rendering_snapshot_index)) return false;
    PdockerVkGraphicsCommandRecord record;
    memset(&record, 0, sizeof(record));
    record.command_type = PDOCKER_GPU_GRAPHICS_V6_COMMAND_BEGIN_RENDERING;
    record.flags = 0;
    record.descriptor_set_count = cmd->active_rendering_layer_count;
    record.dynamic_offset_count = cmd->active_rendering_view_mask;
    record.rendering_snapshot_index = rendering_snapshot_index;
    if (!append_graphics_command_record(cmd, &record)) return false;
    cmd->dynamic_rendering_active = true;
    cmd->render_pass_active = false;
    cmd->active_subpass = 0;
    cmd->active_subpass_contents = contents;
    return true;
}

VKAPI_ATTR void VKAPI_CALL vkCmdBeginRenderPass(
        VkCommandBuffer commandBuffer,
        const VkRenderPassBeginInfo *pRenderPassBegin,
        VkSubpassContents contents) {
    PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)commandBuffer;
    if (!cmd) return;
    cmd->active_clear_value_count = pRenderPassBegin ? pRenderPassBegin->clearValueCount : 0;
    if (cmd->active_clear_value_count > PDOCKER_VK_MAX_STORAGE_BUFFERS) {
        cmd->graphics_unsupported = true;
        cmd->active_clear_value_count = PDOCKER_VK_MAX_STORAGE_BUFFERS;
    }
    if (cmd->active_clear_value_count > 0 && (!pRenderPassBegin || !pRenderPassBegin->pClearValues)) {
        cmd->graphics_unsupported = true;
        cmd->active_clear_value_count = 0;
    }
    for (uint32_t i = 0; pRenderPassBegin && i < cmd->active_clear_value_count; ++i) {
        cmd->active_clear_values[i] = pRenderPassBegin->pClearValues[i];
    }
    if (pRenderPassBegin && pRenderPassBegin->pNext) {
        cmd->graphics_unsupported = true;
    }
    if (!append_normalized_render_pass_begin(cmd, pRenderPassBegin, contents)) {
        cmd->render_pass_active = true;
        cmd->dynamic_rendering_active = false;
        cmd->active_render_pass = pRenderPassBegin
            ? (PdockerVkRenderPass *)pRenderPassBegin->renderPass
            : NULL;
        cmd->active_framebuffer = pRenderPassBegin
            ? (PdockerVkFramebuffer *)pRenderPassBegin->framebuffer
            : NULL;
        cmd->active_subpass = 0;
        cmd->active_subpass_contents = contents;
        cmd->active_render_area = pRenderPassBegin ? pRenderPassBegin->renderArea : (VkRect2D){{0, 0}, {0, 0}};
    }
}

VKAPI_ATTR void VKAPI_CALL vkCmdNextSubpass(
        VkCommandBuffer commandBuffer,
        VkSubpassContents contents) {
    PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)commandBuffer;
    if (!cmd) return;
    cmd->active_subpass += 1;
    cmd->active_subpass_contents = contents;
    cmd->graphics_unsupported = true;
}

VKAPI_ATTR void VKAPI_CALL vkCmdEndRenderPass(VkCommandBuffer commandBuffer) {
    PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)commandBuffer;
    if (!cmd) return;
    PdockerVkGraphicsCommandRecord record;
    memset(&record, 0, sizeof(record));
    record.command_type = PDOCKER_GPU_GRAPHICS_V6_COMMAND_END_RENDERING;
    (void)append_graphics_command_record(cmd, &record);
    if (cmd->dynamic_rendering_active && cmd->active_render_pass &&
        !append_render_pass_end_layout_transitions(cmd)) {
        cmd->graphics_unsupported = true;
    }
    cmd->dynamic_rendering_active = false;
    cmd->render_pass_active = false;
    cmd->active_render_pass = NULL;
    cmd->active_framebuffer = NULL;
    cmd->active_subpass = 0;
    cmd->active_clear_value_count = 0;
    memset(cmd->active_clear_values, 0, sizeof(cmd->active_clear_values));
}

VKAPI_ATTR void VKAPI_CALL vkCmdBeginRenderPass2(
        VkCommandBuffer commandBuffer,
        const VkRenderPassBeginInfo *pRenderPassBegin,
        const VkSubpassBeginInfo *pSubpassBeginInfo) {
    PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)commandBuffer;
    if (cmd && pSubpassBeginInfo && pSubpassBeginInfo->pNext) {
        cmd->graphics_unsupported = true;
    }
    vkCmdBeginRenderPass(commandBuffer,
                         pRenderPassBegin,
                         pSubpassBeginInfo ? pSubpassBeginInfo->contents
                                           : VK_SUBPASS_CONTENTS_INLINE);
}

VKAPI_ATTR void VKAPI_CALL vkCmdNextSubpass2(
        VkCommandBuffer commandBuffer,
        const VkSubpassBeginInfo *pSubpassBeginInfo,
        const VkSubpassEndInfo *pSubpassEndInfo) {
    PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)commandBuffer;
    if (cmd && ((pSubpassBeginInfo && pSubpassBeginInfo->pNext) ||
                (pSubpassEndInfo && pSubpassEndInfo->pNext))) {
        cmd->graphics_unsupported = true;
    }
    vkCmdNextSubpass(commandBuffer, pSubpassBeginInfo
                                      ? pSubpassBeginInfo->contents
                                      : VK_SUBPASS_CONTENTS_INLINE);
}

VKAPI_ATTR void VKAPI_CALL vkCmdEndRenderPass2(
        VkCommandBuffer commandBuffer,
        const VkSubpassEndInfo *pSubpassEndInfo) {
    PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)commandBuffer;
    if (cmd && pSubpassEndInfo && pSubpassEndInfo->pNext) {
        cmd->graphics_unsupported = true;
    }
    vkCmdEndRenderPass(commandBuffer);
}

static void record_vertex_buffer_bindings(
        VkCommandBuffer commandBuffer,
        uint32_t firstBinding,
        uint32_t bindingCount,
        const VkBuffer *pBuffers,
        const VkDeviceSize *pOffsets,
        const VkDeviceSize *pSizes,
        const VkDeviceSize *pStrides) {
    PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)commandBuffer;
    if (!cmd) return;
    if (bindingCount > 0 && (!pBuffers || !pOffsets)) {
        cmd->graphics_unsupported = true;
        return;
    }
    if (firstBinding > PDOCKER_VK_MAX_GRAPHICS_VERTEX_BINDINGS ||
        bindingCount > PDOCKER_VK_MAX_GRAPHICS_VERTEX_BINDINGS - firstBinding) {
        cmd->graphics_unsupported = true;
        return;
    }
    for (uint32_t i = 0; i < bindingCount; ++i) {
        uint32_t slot = firstBinding + i;
        PdockerVkVertexBindingState *binding = &cmd->vertex_bindings[slot];
        binding->buffer = (PdockerVkBuffer *)pBuffers[i];
        binding->offset = pOffsets[i];
        binding->size = pSizes ? pSizes[i] : VK_WHOLE_SIZE;
        binding->stride = pStrides ? pStrides[i] : 0;
        binding->bound = true;
        if (slot + 1 > cmd->vertex_binding_count) cmd->vertex_binding_count = slot + 1;
    }
    cmd->vertex_buffer_bound = true;
    PdockerVkGraphicsCommandRecord record;
    memset(&record, 0, sizeof(record));
    record.command_type = PDOCKER_GPU_GRAPHICS_V6_COMMAND_BIND_VERTEX_BUFFERS;
    record.vertex_binding_first = firstBinding;
    record.vertex_binding_count = bindingCount;
    (void)append_graphics_command_record(cmd, &record);
}

VKAPI_ATTR void VKAPI_CALL vkCmdBindVertexBuffers(
        VkCommandBuffer commandBuffer,
        uint32_t firstBinding,
        uint32_t bindingCount,
        const VkBuffer *pBuffers,
        const VkDeviceSize *pOffsets) {
    record_vertex_buffer_bindings(commandBuffer, firstBinding, bindingCount, pBuffers, pOffsets, NULL, NULL);
}

VKAPI_ATTR void VKAPI_CALL vkCmdBindVertexBuffers2(
        VkCommandBuffer commandBuffer,
        uint32_t firstBinding,
        uint32_t bindingCount,
        const VkBuffer *pBuffers,
        const VkDeviceSize *pOffsets,
        const VkDeviceSize *pSizes,
        const VkDeviceSize *pStrides) {
    record_vertex_buffer_bindings(commandBuffer, firstBinding, bindingCount, pBuffers, pOffsets, pSizes, pStrides);
}

VKAPI_ATTR void VKAPI_CALL vkCmdBindIndexBuffer(
        VkCommandBuffer commandBuffer,
        VkBuffer buffer,
        VkDeviceSize offset,
        VkIndexType indexType) {
    PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)commandBuffer;
    if (!cmd) return;
    cmd->index_buffer = (PdockerVkBuffer *)buffer;
    cmd->index_offset = offset;
    cmd->index_type = indexType;
    cmd->index_buffer_bound = true;
    PdockerVkGraphicsCommandRecord record;
    memset(&record, 0, sizeof(record));
    record.command_type = PDOCKER_GPU_GRAPHICS_V6_COMMAND_BIND_INDEX_BUFFER;
    record.index_offset = offset;
    record.index_type = indexType;
    (void)append_graphics_command_record(cmd, &record);
}

static void record_graphics_draw_command(
        VkCommandBuffer commandBuffer,
        uint32_t vertexCount,
        uint32_t instanceCount,
        uint32_t firstVertex,
        uint32_t firstInstance,
        uint32_t indexCount,
        uint32_t firstIndex,
        int32_t vertexOffset,
        bool indexed,
        bool indirect,
        VkBuffer indirectBuffer,
        VkDeviceSize indirectOffset,
        VkBuffer countBuffer,
        VkDeviceSize countOffset,
        uint32_t stride) {
    PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)commandBuffer;
    if (!cmd) return;
    const bool graphics_rendering_context_active =
        cmd->dynamic_rendering_active || cmd->inherited_rendering_active;
    if (!cmd->graphics_pipeline || !graphics_rendering_context_active ||
        cmd->render_pass_active || (indexed && !cmd->index_buffer_bound)) {
        cmd->graphics_unsupported = true;
    }
    if (cmd->graphics_draw_op_count >= PDOCKER_VK_MAX_GRAPHICS_DRAW_OPS) {
        cmd->graphics_unsupported = true;
        return;
    }
    uint32_t snapshot_index = cmd->graphics_draw_op_count++;
    PdockerVkGraphicsDrawSnapshot *snapshot = &cmd->graphics_draw_ops[snapshot_index];
    memset(snapshot, 0, sizeof(*snapshot));
    snapshot->pipeline = cmd->graphics_pipeline;
    memcpy(snapshot->set_snapshots, cmd->graphics_bound_set_snapshots, sizeof(snapshot->set_snapshots));
    memcpy(snapshot->set_snapshot_used, cmd->graphics_bound_set_used, sizeof(snapshot->set_snapshot_used));
    memcpy(snapshot->push_constants, cmd->push_constants, sizeof(snapshot->push_constants));
    snapshot->push_constant_size = cmd->push_constant_size;
    memcpy(snapshot->push_constant_ops, cmd->push_constant_ops, sizeof(snapshot->push_constant_ops));
    snapshot->push_constant_op_count = cmd->push_constant_op_count;
    memcpy(snapshot->vertex_bindings, cmd->vertex_bindings, sizeof(snapshot->vertex_bindings));
    snapshot->vertex_binding_count = cmd->vertex_binding_count;
    snapshot->vertex_buffer_bound = cmd->vertex_buffer_bound;
    snapshot->index_buffer = cmd->index_buffer;
    snapshot->index_offset = cmd->index_offset;
    snapshot->index_type = cmd->index_type;
    snapshot->index_buffer_bound = cmd->index_buffer_bound;
    memcpy(snapshot->dynamic_states, cmd->dynamic_states, sizeof(snapshot->dynamic_states));
    snapshot->dynamic_state_count = cmd->dynamic_state_count;
    snapshot->dynamic_rendering_active = cmd->dynamic_rendering_active;
    snapshot->render_pass_active = cmd->render_pass_active;
    snapshot->active_render_pass = cmd->active_render_pass;
    snapshot->active_framebuffer = cmd->active_framebuffer;
    snapshot->active_render_area = cmd->active_render_area;
    snapshot->active_rendering_flags = cmd->active_rendering_flags;
    snapshot->active_rendering_layer_count = cmd->active_rendering_layer_count;
    snapshot->active_rendering_view_mask = cmd->active_rendering_view_mask;
    snapshot->active_subpass_contents = cmd->active_subpass_contents;
    snapshot->active_subpass = cmd->active_subpass;
    snapshot->active_color_attachment_count = cmd->active_color_attachment_count;
    memcpy(snapshot->active_color_attachments, cmd->active_color_attachments,
           sizeof(snapshot->active_color_attachments));
    snapshot->active_depth_attachment = cmd->active_depth_attachment;
    snapshot->active_stencil_attachment = cmd->active_stencil_attachment;
    memcpy(snapshot->active_clear_values, cmd->active_clear_values,
           sizeof(snapshot->active_clear_values));
    snapshot->active_clear_value_count = cmd->active_clear_value_count;
    if (snapshot->pipeline && snapshot->pipeline->layout &&
        snapshot->pipeline->layout->push_constant_size > snapshot->push_constant_size) {
        snapshot->push_constant_size = snapshot->pipeline->layout->push_constant_size;
    }
    snapshot->vertex_count = vertexCount;
    snapshot->instance_count = instanceCount;
    snapshot->first_vertex = firstVertex;
    snapshot->first_instance = firstInstance;
    snapshot->index_count = indexCount;
    snapshot->first_index = firstIndex;
    snapshot->vertex_offset = vertexOffset;
    snapshot->indexed = indexed;
    snapshot->indirect = indirect;
    snapshot->indirect_buffer = (PdockerVkBuffer *)indirectBuffer;
    snapshot->indirect_offset = indirectOffset;
    snapshot->count_buffer = (PdockerVkBuffer *)countBuffer;
    snapshot->count_offset = countOffset;
    snapshot->indirect_stride = stride;
    PdockerVkGraphicsCommandRecord graphics_record;
    memset(&graphics_record, 0, sizeof(graphics_record));
    graphics_record.command_type = indexed
        ? PDOCKER_GPU_GRAPHICS_V6_COMMAND_DRAW_INDEXED
        : PDOCKER_GPU_GRAPHICS_V6_COMMAND_DRAW;
    graphics_record.pipeline = snapshot->pipeline;
    graphics_record.layout_id = snapshot->pipeline && snapshot->pipeline->layout
        ? snapshot->pipeline->layout->layout_id
        : 0;
    graphics_record.draw_snapshot_index = snapshot_index;
    graphics_record.vertex_binding_first = 0;
    graphics_record.vertex_binding_count = snapshot->vertex_binding_count;
    graphics_record.index_offset = snapshot->index_offset;
    graphics_record.index_type = snapshot->index_type;
    (void)append_graphics_command_record(cmd, &graphics_record);
    PdockerVkCommandOp op;
    memset(&op, 0, sizeof(op));
    op.type = PDOCKER_VK_COMMAND_GRAPHICS_DRAW;
    op.index = snapshot_index;
    op.draw_vertex_count = vertexCount;
    op.draw_instance_count = instanceCount;
    op.draw_first_vertex = firstVertex;
    op.draw_first_instance = firstInstance;
    op.draw_index_count = indexCount;
    op.draw_first_index = firstIndex;
    op.draw_vertex_offset = vertexOffset;
    op.draw_indexed = indexed;
    op.draw_indirect = indirect;
    op.draw_indirect_buffer = (PdockerVkBuffer *)indirectBuffer;
    op.draw_indirect_offset = indirectOffset;
    op.draw_count_buffer = (PdockerVkBuffer *)countBuffer;
    op.draw_count_offset = countOffset;
    op.draw_indirect_stride = stride;
    (void)append_command_op(cmd, &op);
}

VKAPI_ATTR void VKAPI_CALL vkCmdDraw(
        VkCommandBuffer commandBuffer,
        uint32_t vertexCount,
        uint32_t instanceCount,
        uint32_t firstVertex,
        uint32_t firstInstance) {
    record_graphics_draw_command(commandBuffer, vertexCount, instanceCount, firstVertex, firstInstance, 0, 0, 0, false, false, VK_NULL_HANDLE, 0, VK_NULL_HANDLE, 0, 0);
}

VKAPI_ATTR void VKAPI_CALL vkCmdDrawIndexed(
        VkCommandBuffer commandBuffer,
        uint32_t indexCount,
        uint32_t instanceCount,
        uint32_t firstIndex,
        int32_t vertexOffset,
        uint32_t firstInstance) {
    record_graphics_draw_command(commandBuffer, 0, instanceCount, 0, firstInstance, indexCount, firstIndex, vertexOffset, true, false, VK_NULL_HANDLE, 0, VK_NULL_HANDLE, 0, 0);
}

VKAPI_ATTR void VKAPI_CALL vkCmdDrawIndirect(
        VkCommandBuffer commandBuffer,
        VkBuffer buffer,
        VkDeviceSize offset,
        uint32_t drawCount,
        uint32_t stride) {
    record_graphics_draw_command(commandBuffer, drawCount, 1, 0, 0, 0, 0, 0, false, true, buffer, offset, VK_NULL_HANDLE, 0, stride);
}

VKAPI_ATTR void VKAPI_CALL vkCmdDrawIndexedIndirect(
        VkCommandBuffer commandBuffer,
        VkBuffer buffer,
        VkDeviceSize offset,
        uint32_t drawCount,
        uint32_t stride) {
    record_graphics_draw_command(commandBuffer, drawCount, 1, 0, 0, 0, 0, 0, true, true, buffer, offset, VK_NULL_HANDLE, 0, stride);
}

VKAPI_ATTR void VKAPI_CALL vkCmdDrawIndirectCount(
        VkCommandBuffer commandBuffer,
        VkBuffer buffer,
        VkDeviceSize offset,
        VkBuffer countBuffer,
        VkDeviceSize countBufferOffset,
        uint32_t maxDrawCount,
        uint32_t stride) {
    record_graphics_draw_command(commandBuffer, maxDrawCount, 1, 0, 0, 0, 0, 0, false, true, buffer, offset, countBuffer, countBufferOffset, stride);
}

VKAPI_ATTR void VKAPI_CALL vkCmdDrawIndexedIndirectCount(
        VkCommandBuffer commandBuffer,
        VkBuffer buffer,
        VkDeviceSize offset,
        VkBuffer countBuffer,
        VkDeviceSize countBufferOffset,
        uint32_t maxDrawCount,
        uint32_t stride) {
    record_graphics_draw_command(commandBuffer, maxDrawCount, 1, 0, 0, 0, 0, 0, true, true, buffer, offset, countBuffer, countBufferOffset, stride);
}

VKAPI_ATTR void VKAPI_CALL vkCmdSetViewport(
        VkCommandBuffer commandBuffer,
        uint32_t firstViewport,
        uint32_t viewportCount,
        const VkViewport *pViewports) {
    record_graphics_dynamic_state_bytes((PdockerVkCommandBuffer *)commandBuffer,
                                        VK_DYNAMIC_STATE_VIEWPORT,
                                        firstViewport,
                                        viewportCount,
                                        pViewports,
                                        pViewports ? (size_t)viewportCount * sizeof(VkViewport) : 0);
}

VKAPI_ATTR void VKAPI_CALL vkCmdSetScissor(
        VkCommandBuffer commandBuffer,
        uint32_t firstScissor,
        uint32_t scissorCount,
        const VkRect2D *pScissors) {
    record_graphics_dynamic_state_bytes((PdockerVkCommandBuffer *)commandBuffer,
                                        VK_DYNAMIC_STATE_SCISSOR,
                                        firstScissor,
                                        scissorCount,
                                        pScissors,
                                        pScissors ? (size_t)scissorCount * sizeof(VkRect2D) : 0);
}

VKAPI_ATTR void VKAPI_CALL vkCmdSetLineWidth(
        VkCommandBuffer commandBuffer,
        float lineWidth) {
    record_graphics_dynamic_state_bytes((PdockerVkCommandBuffer *)commandBuffer,
                                        VK_DYNAMIC_STATE_LINE_WIDTH,
                                        0, 1, &lineWidth, sizeof(lineWidth));
}

VKAPI_ATTR void VKAPI_CALL vkCmdSetDepthBias(
        VkCommandBuffer commandBuffer,
        float depthBiasConstantFactor,
        float depthBiasClamp,
        float depthBiasSlopeFactor) {
    float values[3] = {depthBiasConstantFactor, depthBiasClamp, depthBiasSlopeFactor};
    record_graphics_dynamic_state_bytes((PdockerVkCommandBuffer *)commandBuffer,
                                        VK_DYNAMIC_STATE_DEPTH_BIAS,
                                        0, 3, values, sizeof(values));
}

VKAPI_ATTR void VKAPI_CALL vkCmdSetBlendConstants(
        VkCommandBuffer commandBuffer,
        const float blendConstants[4]) {
    record_graphics_dynamic_state_bytes((PdockerVkCommandBuffer *)commandBuffer,
                                        VK_DYNAMIC_STATE_BLEND_CONSTANTS,
                                        0, 4, blendConstants, blendConstants ? sizeof(float) * 4u : 0);
}

VKAPI_ATTR void VKAPI_CALL vkCmdSetDepthBounds(
        VkCommandBuffer commandBuffer,
        float minDepthBounds,
        float maxDepthBounds) {
    float values[2] = {minDepthBounds, maxDepthBounds};
    record_graphics_dynamic_state_bytes((PdockerVkCommandBuffer *)commandBuffer,
                                        VK_DYNAMIC_STATE_DEPTH_BOUNDS,
                                        0, 2, values, sizeof(values));
}

VKAPI_ATTR void VKAPI_CALL vkCmdSetStencilCompareMask(
        VkCommandBuffer commandBuffer,
        VkStencilFaceFlags faceMask,
        uint32_t compareMask) {
    uint32_t values[2] = {(uint32_t)faceMask, compareMask};
    record_graphics_dynamic_state_bytes((PdockerVkCommandBuffer *)commandBuffer,
                                        VK_DYNAMIC_STATE_STENCIL_COMPARE_MASK,
                                        0, 2, values, sizeof(values));
}

VKAPI_ATTR void VKAPI_CALL vkCmdSetStencilWriteMask(
        VkCommandBuffer commandBuffer,
        VkStencilFaceFlags faceMask,
        uint32_t writeMask) {
    uint32_t values[2] = {(uint32_t)faceMask, writeMask};
    record_graphics_dynamic_state_bytes((PdockerVkCommandBuffer *)commandBuffer,
                                        VK_DYNAMIC_STATE_STENCIL_WRITE_MASK,
                                        0, 2, values, sizeof(values));
}

VKAPI_ATTR void VKAPI_CALL vkCmdSetStencilReference(
        VkCommandBuffer commandBuffer,
        VkStencilFaceFlags faceMask,
        uint32_t reference) {
    uint32_t values[2] = {(uint32_t)faceMask, reference};
    record_graphics_dynamic_state_bytes((PdockerVkCommandBuffer *)commandBuffer,
                                        VK_DYNAMIC_STATE_STENCIL_REFERENCE,
                                        0, 2, values, sizeof(values));
}

VKAPI_ATTR void VKAPI_CALL vkCmdSetViewportWithCount(
        VkCommandBuffer commandBuffer,
        uint32_t viewportCount,
        const VkViewport *pViewports) {
    vkCmdSetViewport(commandBuffer, 0, viewportCount, pViewports);
}

VKAPI_ATTR void VKAPI_CALL vkCmdSetScissorWithCount(
        VkCommandBuffer commandBuffer,
        uint32_t scissorCount,
        const VkRect2D *pScissors) {
    vkCmdSetScissor(commandBuffer, 0, scissorCount, pScissors);
}

VKAPI_ATTR void VKAPI_CALL vkCmdSetCullMode(
        VkCommandBuffer commandBuffer,
        VkCullModeFlags cullMode) {
    record_graphics_dynamic_state_bytes((PdockerVkCommandBuffer *)commandBuffer,
                                        VK_DYNAMIC_STATE_CULL_MODE,
                                        0, 1, &cullMode, sizeof(cullMode));
}

VKAPI_ATTR void VKAPI_CALL vkCmdSetFrontFace(
        VkCommandBuffer commandBuffer,
        VkFrontFace frontFace) {
    record_graphics_dynamic_state_bytes((PdockerVkCommandBuffer *)commandBuffer,
                                        VK_DYNAMIC_STATE_FRONT_FACE,
                                        0, 1, &frontFace, sizeof(frontFace));
}

VKAPI_ATTR void VKAPI_CALL vkCmdSetPrimitiveTopology(
        VkCommandBuffer commandBuffer,
        VkPrimitiveTopology primitiveTopology) {
    record_graphics_dynamic_state_bytes((PdockerVkCommandBuffer *)commandBuffer,
                                        VK_DYNAMIC_STATE_PRIMITIVE_TOPOLOGY,
                                        0, 1, &primitiveTopology, sizeof(primitiveTopology));
}

VKAPI_ATTR void VKAPI_CALL vkCmdSetDepthTestEnable(
        VkCommandBuffer commandBuffer,
        VkBool32 depthTestEnable) {
    record_graphics_dynamic_state_bytes((PdockerVkCommandBuffer *)commandBuffer,
                                        VK_DYNAMIC_STATE_DEPTH_TEST_ENABLE,
                                        0, 1, &depthTestEnable, sizeof(depthTestEnable));
}

VKAPI_ATTR void VKAPI_CALL vkCmdSetDepthWriteEnable(
        VkCommandBuffer commandBuffer,
        VkBool32 depthWriteEnable) {
    record_graphics_dynamic_state_bytes((PdockerVkCommandBuffer *)commandBuffer,
                                        VK_DYNAMIC_STATE_DEPTH_WRITE_ENABLE,
                                        0, 1, &depthWriteEnable, sizeof(depthWriteEnable));
}

VKAPI_ATTR void VKAPI_CALL vkCmdSetDepthCompareOp(
        VkCommandBuffer commandBuffer,
        VkCompareOp depthCompareOp) {
    record_graphics_dynamic_state_bytes((PdockerVkCommandBuffer *)commandBuffer,
                                        VK_DYNAMIC_STATE_DEPTH_COMPARE_OP,
                                        0, 1, &depthCompareOp, sizeof(depthCompareOp));
}

VKAPI_ATTR void VKAPI_CALL vkCmdSetStencilTestEnable(
        VkCommandBuffer commandBuffer,
        VkBool32 stencilTestEnable) {
    record_graphics_dynamic_state_bytes((PdockerVkCommandBuffer *)commandBuffer,
                                        VK_DYNAMIC_STATE_STENCIL_TEST_ENABLE,
                                        0, 1, &stencilTestEnable, sizeof(stencilTestEnable));
}

VKAPI_ATTR void VKAPI_CALL vkCmdSetStencilOp(
        VkCommandBuffer commandBuffer,
        VkStencilFaceFlags faceMask,
        VkStencilOp failOp,
        VkStencilOp passOp,
        VkStencilOp depthFailOp,
        VkCompareOp compareOp) {
    uint32_t values[5] = {(uint32_t)faceMask, (uint32_t)failOp, (uint32_t)passOp, (uint32_t)depthFailOp, (uint32_t)compareOp};
    record_graphics_dynamic_state_bytes((PdockerVkCommandBuffer *)commandBuffer,
                                        VK_DYNAMIC_STATE_STENCIL_OP,
                                        0, 5, values, sizeof(values));
}

static bool pdocker_vk_rect_inside_render_area(const VkRect2D *rect, const VkRect2D *area) {
    if (!rect || !area || rect->extent.width == 0 || rect->extent.height == 0) return false;
    int64_t rx0 = rect->offset.x;
    int64_t ry0 = rect->offset.y;
    int64_t ax0 = area->offset.x;
    int64_t ay0 = area->offset.y;
    int64_t rx1 = rx0 + (int64_t)rect->extent.width;
    int64_t ry1 = ry0 + (int64_t)rect->extent.height;
    int64_t ax1 = ax0 + (int64_t)area->extent.width;
    int64_t ay1 = ay0 + (int64_t)area->extent.height;
    return rx0 >= ax0 && ry0 >= ay0 && rx1 <= ax1 && ry1 <= ay1;
}

VKAPI_ATTR void VKAPI_CALL vkCmdClearAttachments(
        VkCommandBuffer commandBuffer,
        uint32_t attachmentCount,
        const VkClearAttachment *pAttachments,
        uint32_t rectCount,
        const VkClearRect *pRects) {
    PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)commandBuffer;
    if (!cmd) return;
    if (!cmd->dynamic_rendering_active || cmd->inherited_rendering_active ||
        attachmentCount == 0 || rectCount == 0 || !pAttachments || !pRects ||
        attachmentCount > PDOCKER_VK_MAX_CLEAR_ATTACHMENTS - cmd->clear_attachment_op_count ||
        rectCount > PDOCKER_VK_MAX_CLEAR_RECTS - cmd->clear_rect_op_count ||
        cmd->clear_attachments_command_op_count >= PDOCKER_VK_MAX_CLEAR_ATTACHMENTS_COMMANDS ||
        cmd->graphics_command_op_count >= PDOCKER_VK_MAX_GRAPHICS_COMMAND_OPS) {
        cmd->graphics_unsupported = true;
        return;
    }

    for (uint32_t i = 0; i < attachmentCount; ++i) {
        VkImageAspectFlags aspect = pAttachments[i].aspectMask;
        const VkImageAspectFlags supported =
            VK_IMAGE_ASPECT_COLOR_BIT | VK_IMAGE_ASPECT_DEPTH_BIT | VK_IMAGE_ASPECT_STENCIL_BIT;
        bool has_color = (aspect & VK_IMAGE_ASPECT_COLOR_BIT) != 0;
        bool has_depth = (aspect & VK_IMAGE_ASPECT_DEPTH_BIT) != 0;
        bool has_stencil = (aspect & VK_IMAGE_ASPECT_STENCIL_BIT) != 0;
        if (aspect == 0 || (aspect & ~supported) != 0 ||
            (has_color && (has_depth || has_stencil))) {
            cmd->graphics_unsupported = true;
            return;
        }
        if (has_color) {
            uint32_t color = pAttachments[i].colorAttachment;
            if (color >= cmd->active_color_attachment_count ||
                !cmd->active_color_attachments[color].valid ||
                !cmd->active_color_attachments[color].image_view) {
                cmd->graphics_unsupported = true;
                return;
            }
        } else {
            if (has_depth && (!cmd->active_depth_attachment.valid ||
                              !cmd->active_depth_attachment.image_view)) {
                cmd->graphics_unsupported = true;
                return;
            }
            if (has_stencil && (!cmd->active_stencil_attachment.valid ||
                                !cmd->active_stencil_attachment.image_view)) {
                cmd->graphics_unsupported = true;
                return;
            }
        }
    }
    for (uint32_t r = 0; r < rectCount; ++r) {
        if (pRects[r].layerCount == 0 ||
            !pdocker_vk_rect_inside_render_area(&pRects[r].rect, &cmd->active_render_area) ||
            pRects[r].baseArrayLayer > cmd->active_rendering_layer_count ||
            pRects[r].layerCount > cmd->active_rendering_layer_count - pRects[r].baseArrayLayer) {
            cmd->graphics_unsupported = true;
            return;
        }
    }

    uint32_t attachment_first = cmd->clear_attachment_op_count;
    uint32_t rect_first = cmd->clear_rect_op_count;
    for (uint32_t i = 0; i < attachmentCount; ++i) {
        PdockerVkClearAttachmentSnapshot *dst = &cmd->clear_attachment_ops[cmd->clear_attachment_op_count++];
        memset(dst, 0, sizeof(*dst));
        dst->aspect_mask = pAttachments[i].aspectMask;
        dst->color_attachment = (pAttachments[i].aspectMask & VK_IMAGE_ASPECT_COLOR_BIT)
            ? pAttachments[i].colorAttachment
            : UINT32_MAX;
        dst->clear_value = pAttachments[i].clearValue;
    }
    for (uint32_t r = 0; r < rectCount; ++r) {
        PdockerVkClearRectSnapshot *dst = &cmd->clear_rect_ops[cmd->clear_rect_op_count++];
        memset(dst, 0, sizeof(*dst));
        dst->rect = pRects[r].rect;
        dst->base_array_layer = pRects[r].baseArrayLayer;
        dst->layer_count = pRects[r].layerCount;
    }
    uint32_t command_snapshot_index = cmd->clear_attachments_command_op_count++;
    PdockerVkClearAttachmentsCommandSnapshot *snapshot =
        &cmd->clear_attachments_command_ops[command_snapshot_index];
    snapshot->clear_attachment_first = attachment_first;
    snapshot->clear_attachment_count = attachmentCount;
    snapshot->clear_rect_first = rect_first;
    snapshot->clear_rect_count = rectCount;

    PdockerVkGraphicsCommandRecord record;
    memset(&record, 0, sizeof(record));
    record.command_type = PDOCKER_GPU_GRAPHICS_V6_COMMAND_CLEAR_ATTACHMENTS;
    record.descriptor_bind_snapshot_index = command_snapshot_index;
    record.first_set = attachment_first;
    record.descriptor_set_count = attachmentCount;
    record.first_dynamic_offset = rect_first;
    record.dynamic_offset_count = rectCount;
    (void)append_graphics_command_record(cmd, &record);
}

VKAPI_ATTR void VKAPI_CALL vkCmdExecuteCommands(
        VkCommandBuffer commandBuffer,
        uint32_t commandBufferCount,
        const VkCommandBuffer *pCommandBuffers) {
    PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)commandBuffer;
    if (!cmd || commandBufferCount == 0) return;
    if (cmd->level != VK_COMMAND_BUFFER_LEVEL_PRIMARY || !pCommandBuffers) {
        cmd->graphics_unsupported = true;
        return;
    }
    for (uint32_t i = 0; i < commandBufferCount; ++i) {
        PdockerVkCommandBuffer *secondary = (PdockerVkCommandBuffer *)pCommandBuffers[i];
        if (!secondary || secondary->level != VK_COMMAND_BUFFER_LEVEL_SECONDARY ||
            !append_secondary_command_buffer(cmd, secondary)) {
            cmd->graphics_unsupported = true;
            return;
        }
    }
}

VKAPI_ATTR void VKAPI_CALL vkCmdBindDescriptorSets(
        VkCommandBuffer commandBuffer,
        VkPipelineBindPoint pipelineBindPoint,
        VkPipelineLayout layout,
        uint32_t firstSet,
        uint32_t descriptorSetCount,
        const VkDescriptorSet *pDescriptorSets,
        uint32_t dynamicOffsetCount,
        const uint32_t *pDynamicOffsets) {
    PdockerVkPipelineLayout *pipeline_layout = (PdockerVkPipelineLayout *)layout;
    PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)commandBuffer;
    if (cmd && descriptorSetCount > 0 && pDescriptorSets) {
        PdockerVkDescriptorSet **target_set_handles = NULL;
        PdockerVkDescriptorSet *target_set_snapshots = NULL;
        bool *target_set_used = NULL;
        if (pipelineBindPoint == VK_PIPELINE_BIND_POINT_GRAPHICS) {
            target_set_handles = cmd->graphics_bound_set_handles;
            target_set_snapshots = cmd->graphics_bound_set_snapshots;
            target_set_used = cmd->graphics_bound_set_used;
        } else if (pipelineBindPoint == VK_PIPELINE_BIND_POINT_COMPUTE) {
            target_set_handles = cmd->bound_set_handles;
            target_set_snapshots = cmd->bound_set_snapshots;
            target_set_used = cmd->bound_set_used;
        } else {
            cmd->unsupported_descriptor_set_layout = true;
            return;
        }
        uint32_t graphics_first_dynamic_offset = cmd->graphics_dynamic_offset_count;
        if (pipelineBindPoint == VK_PIPELINE_BIND_POINT_GRAPHICS && dynamicOffsetCount > 0) {
            if (!pDynamicOffsets ||
                dynamicOffsetCount > PDOCKER_VK_MAX_GRAPHICS_DYNAMIC_OFFSETS - cmd->graphics_dynamic_offset_count) {
                cmd->unsupported_descriptor_set_layout = true;
            } else {
                memcpy(&cmd->graphics_dynamic_offsets[cmd->graphics_dynamic_offset_count],
                       pDynamicOffsets,
                       sizeof(cmd->graphics_dynamic_offsets[0]) * dynamicOffsetCount);
                cmd->graphics_dynamic_offset_count += dynamicOffsetCount;
            }
        }
        if (pipeline_layout && pipeline_layout->unsupported_set_layout_count) {
            cmd->unsupported_descriptor_set_layout = true;
            if (trace_allocations() || getenv("PDOCKER_VULKAN_ICD_DEBUG")) {
                fprintf(stderr,
                        "pdocker-vulkan-icd: pipeline layout set count exceeds passthrough limit=%u\n",
                        PDOCKER_VK_MAX_DESCRIPTOR_SETS);
            }
        }
        if (firstSet + descriptorSetCount > PDOCKER_VK_MAX_DESCRIPTOR_SETS) {
            cmd->unsupported_descriptor_set_layout = true;
        }
        if (firstSet + descriptorSetCount > PDOCKER_VK_MAX_DESCRIPTOR_SETS &&
            (trace_allocations() || getenv("PDOCKER_VULKAN_ICD_DEBUG"))) {
            fprintf(stderr,
                    "pdocker-vulkan-icd: descriptor sets firstSet=%u count=%u exceed passthrough limit=%u; rejecting instead of flattening\n",
                    firstSet,
                    descriptorSetCount,
                    PDOCKER_VK_MAX_DESCRIPTOR_SETS);
        }
        uint32_t dynamic_index = 0;
        for (uint32_t set_i = 0; set_i < descriptorSetCount; ++set_i) {
            if (firstSet + set_i >= PDOCKER_VK_MAX_DESCRIPTOR_SETS) break;
            PdockerVkDescriptorSet *set = (PdockerVkDescriptorSet *)pDescriptorSets[set_i];
            if (!set) continue;
            uint32_t target_set = firstSet + set_i;
            if (pipeline_layout &&
                target_set < pipeline_layout->set_layout_count &&
                !descriptor_set_layout_compatible(pipeline_layout->set_layouts[target_set],
                                                  set->layout)) {
                cmd->unsupported_descriptor_set_layout = true;
                if (trace_allocations() || getenv("PDOCKER_VULKAN_ICD_DEBUG")) {
                    fprintf(stderr,
                            "pdocker-vulkan-icd: descriptor set layout mismatch set=%u expected=%p actual=%p\n",
                            target_set,
                            (void *)pipeline_layout->set_layouts[target_set],
                            (void *)set->layout);
                }
            }
            if (pipeline_layout && target_set >= pipeline_layout->set_layout_count) {
                cmd->unsupported_descriptor_set_layout = true;
                if (trace_allocations() || getenv("PDOCKER_VULKAN_ICD_DEBUG")) {
                    fprintf(stderr,
                            "pdocker-vulkan-icd: descriptor set=%u has no pipeline layout slot count=%u\n",
                            target_set,
                            pipeline_layout->set_layout_count);
                }
            }
            if (set->unsupported_descriptor_array || set->unsupported_descriptor_type ||
                (set->layout && set->layout->unsupported_descriptor_type)) {
                cmd->unsupported_descriptor_set_layout = true;
            }
            target_set_handles[target_set] = set;
            target_set_snapshots[target_set] = *set;
            target_set_used[target_set] = true;
            PdockerVkDescriptorSetLayout *expected_layout =
                pipeline_layout && target_set < pipeline_layout->set_layout_count
                    ? pipeline_layout->set_layouts[target_set]
                    : set->layout;
            uint32_t binding_limit = expected_layout
                ? expected_layout->storage_binding_count
                : PDOCKER_VK_MAX_STORAGE_BUFFERS;
            if (binding_limit > PDOCKER_VK_MAX_STORAGE_BUFFERS) {
                binding_limit = PDOCKER_VK_MAX_STORAGE_BUFFERS;
            }
            for (uint32_t binding = 0; binding < binding_limit; ++binding) {
                uint32_t array_limit = expected_layout
                    ? expected_layout->storage_binding_counts[binding]
                    : PDOCKER_VK_MAX_DESCRIPTOR_ARRAY_ELEMENTS;
                if (array_limit == 0 && !expected_layout) {
                    array_limit = PDOCKER_VK_MAX_DESCRIPTOR_ARRAY_ELEMENTS;
                }
                if (array_limit > PDOCKER_VK_MAX_DESCRIPTOR_ARRAY_ELEMENTS) {
                    cmd->unsupported_descriptor_set_layout = true;
                    array_limit = PDOCKER_VK_MAX_DESCRIPTOR_ARRAY_ELEMENTS;
                }
                bool expects_dynamic = expected_layout
                    ? descriptor_type_is_dynamic(expected_layout->storage_binding_types[binding])
                    : false;
                for (uint32_t array_element = 0; array_element < array_limit; ++array_element) {
                    PdockerVkDescriptorBinding *slot =
                        &target_set_snapshots[target_set].storage_buffers[binding][array_element];
                    bool slot_expects_dynamic = expected_layout ? expects_dynamic : slot->dynamic;
                    if (!slot_expects_dynamic) continue;
                    if (!slot->dynamic) {
                        cmd->unsupported_descriptor_set_layout = true;
                        if (trace_allocations() || getenv("PDOCKER_VULKAN_ICD_DEBUG")) {
                            fprintf(stderr,
                                    "pdocker-vulkan-icd: layout expects dynamic descriptor set=%u binding=%u array=%u but descriptor write is not dynamic\n",
                                    target_set,
                                    binding,
                                    array_element);
                        }
                    }
                    if (dynamic_index < dynamicOffsetCount && pDynamicOffsets) {
                        if ((VkDeviceSize)pDynamicOffsets[dynamic_index] >
                            (VkDeviceSize)(UINT64_MAX - slot->base_offset)) {
                            cmd->unsupported_descriptor_set_layout = true;
                            if (trace_allocations() || getenv("PDOCKER_VULKAN_ICD_DEBUG")) {
                                fprintf(stderr,
                                        "pdocker-vulkan-icd: dynamic descriptor offset overflow set=%u binding=%u array=%u base=%llu add=%u\n",
                                        target_set,
                                        binding,
                                        array_element,
                                        (unsigned long long)slot->base_offset,
                                        pDynamicOffsets[dynamic_index]);
                            }
                        } else {
                            /*
                             * VK_WHOLE_SIZE is evaluated after applying the
                             * dynamic offset.  Keep slot->range unchanged so
                             * descriptor_binding_size() and
                             * validate_descriptor_transport_shape() derive the
                             * effective range from the remaining VkBuffer
                             * coordinate space instead of from the backing
                             * allocation tail.
                             */
                            slot->dynamic_offset = pDynamicOffsets[dynamic_index];
                            slot->offset = slot->base_offset + slot->dynamic_offset;
                        }
                        if (trace_allocations()) {
                            fprintf(stderr,
                                    "pdocker-vulkan-icd: dynamic descriptor set=%u binding=%u array=%u dyn_index=%u add=%u effective_offset=%llu\n",
                                    target_set,
                                    binding,
                                    array_element,
                                    dynamic_index,
                                    pDynamicOffsets[dynamic_index],
                                    (unsigned long long)slot->offset);
                        }
                    } else {
                        cmd->unsupported_descriptor_set_layout = true;
                        if (trace_allocations() || getenv("PDOCKER_VULKAN_ICD_DEBUG")) {
                            fprintf(stderr,
                                    "pdocker-vulkan-icd: missing dynamic offset set=%u binding=%u array=%u dyn_index=%u count=%u\n",
                                    target_set,
                                    binding,
                                    array_element,
                                    dynamic_index,
                                    dynamicOffsetCount);
                        }
                    }
                    dynamic_index++;
                }
            }
        }
        if (dynamic_index != dynamicOffsetCount) {
            cmd->unsupported_descriptor_set_layout = true;
            if (trace_allocations() || getenv("PDOCKER_VULKAN_ICD_DEBUG")) {
                fprintf(stderr,
                        "pdocker-vulkan-icd: extra dynamic offsets expected=%u supplied=%u\n",
                        dynamic_index,
                        dynamicOffsetCount);
            }
        }
        if (pipelineBindPoint == VK_PIPELINE_BIND_POINT_GRAPHICS) {
            if (cmd->graphics_descriptor_bind_op_count >= PDOCKER_VK_MAX_GRAPHICS_DESCRIPTOR_BIND_OPS) {
                cmd->unsupported_descriptor_set_layout = true;
                return;
            }
            uint32_t bind_snapshot_index = cmd->graphics_descriptor_bind_op_count++;
            PdockerVkGraphicsDescriptorBindSnapshot *bind_snapshot =
                &cmd->graphics_descriptor_bind_ops[bind_snapshot_index];
            memset(bind_snapshot, 0, sizeof(*bind_snapshot));
            bind_snapshot->first_set = firstSet;
            bind_snapshot->descriptor_set_count = descriptorSetCount;
            bind_snapshot->first_dynamic_offset = graphics_first_dynamic_offset;
            bind_snapshot->dynamic_offset_count = dynamicOffsetCount;
            memcpy(bind_snapshot->set_snapshots, cmd->graphics_bound_set_snapshots,
                   sizeof(bind_snapshot->set_snapshots));
            memcpy(bind_snapshot->set_snapshot_used, cmd->graphics_bound_set_used,
                   sizeof(bind_snapshot->set_snapshot_used));

            PdockerVkGraphicsCommandRecord record;
            memset(&record, 0, sizeof(record));
            record.command_type = PDOCKER_GPU_GRAPHICS_V6_COMMAND_BIND_DESCRIPTOR_SETS;
            record.layout_id = pipeline_layout ? pipeline_layout->layout_id : 0;
            record.first_set = firstSet;
            record.descriptor_set_count = descriptorSetCount;
            record.first_dynamic_offset = graphics_first_dynamic_offset;
            record.dynamic_offset_count = dynamicOffsetCount;
            record.descriptor_bind_snapshot_index = bind_snapshot_index;
            (void)append_graphics_command_record(cmd, &record);
        }
    }
}

static void validate_bound_descriptor_layouts_before_dispatch(PdockerVkCommandBuffer *cmd) {
    if (!cmd || !cmd->compute_pipeline || !cmd->compute_pipeline->layout) return;
    PdockerVkPipelineLayout *layout = cmd->compute_pipeline->layout;
    if (layout->unsupported_set_layout_count) {
        cmd->unsupported_descriptor_set_layout = true;
        return;
    }
    for (uint32_t set_i = 0; set_i < PDOCKER_VK_MAX_DESCRIPTOR_SETS; ++set_i) {
        if (!cmd->bound_set_used[set_i]) continue;
        if (set_i >= layout->set_layout_count ||
            !descriptor_set_layout_compatible(layout->set_layouts[set_i],
                                              cmd->bound_set_snapshots[set_i].layout)) {
            cmd->unsupported_descriptor_set_layout = true;
            if (trace_allocations() || getenv("PDOCKER_VULKAN_ICD_DEBUG")) {
                fprintf(stderr,
                        "pdocker-vulkan-icd: dispatch descriptor layout mismatch set=%u layout_count=%u expected=%p actual=%p\n",
                        set_i,
                        layout->set_layout_count,
                        set_i < layout->set_layout_count ? (void *)layout->set_layouts[set_i] : NULL,
                        (void *)cmd->bound_set_snapshots[set_i].layout);
            }
        }
    }
}

VKAPI_ATTR void VKAPI_CALL vkCmdDispatch(
        VkCommandBuffer commandBuffer,
        uint32_t groupCountX,
        uint32_t groupCountY,
        uint32_t groupCountZ) {
    (void)groupCountY;
    (void)groupCountZ;
    PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)commandBuffer;
    if (cmd) {
        cmd->dispatch_x = groupCountX;
        cmd->dispatch_y = groupCountY;
        cmd->dispatch_z = groupCountZ;
        cmd->has_dispatch = true;
        validate_bound_descriptor_layouts_before_dispatch(cmd);
        if (cmd->dispatch_op_count < PDOCKER_VK_MAX_DISPATCH_OPS) {
            uint32_t op_index = cmd->dispatch_op_count++;
            PdockerVkDispatchOp *op = &cmd->dispatch_ops[op_index];
            op->pipeline = cmd->compute_pipeline;
            memcpy(op->set_handles, cmd->bound_set_handles, sizeof(op->set_handles));
            memcpy(op->set_snapshots, cmd->bound_set_snapshots, sizeof(op->set_snapshots));
            memcpy(op->set_snapshot_used, cmd->bound_set_used, sizeof(op->set_snapshot_used));
            op->dispatch_x = groupCountX;
            op->dispatch_y = groupCountY;
            op->dispatch_z = groupCountZ;
            op->base_group_x = 0;
            op->base_group_y = 0;
            op->base_group_z = 0;
            op->dispatch_indirect = false;
            op->dispatch_indirect_buffer = NULL;
            op->dispatch_indirect_offset = 0;
            op->push_constant_size = cmd->push_constant_size;
            if (op->pipeline && op->pipeline->layout &&
                op->pipeline->layout->push_constant_size > op->push_constant_size) {
                op->push_constant_size = op->pipeline->layout->push_constant_size;
            }
            memcpy(op->push_constants, cmd->push_constants, sizeof(op->push_constants));
            memcpy(op->push_constant_ops, cmd->push_constant_ops, sizeof(op->push_constant_ops));
            op->push_constant_op_count = cmd->push_constant_op_count;
            PdockerVkCommandOp command_op;
            memset(&command_op, 0, sizeof(command_op));
            command_op.type = PDOCKER_VK_COMMAND_DISPATCH;
            command_op.index = op_index;
            (void)append_command_op(cmd, &command_op);
        } else if (trace_allocations() || getenv("PDOCKER_VULKAN_ICD_DEBUG")) {
            fprintf(stderr,
                    "pdocker-vulkan-icd: dispatch command buffer full max=%u\n",
                    PDOCKER_VK_MAX_DISPATCH_OPS);
        }
    }
}

VKAPI_ATTR void VKAPI_CALL vkCmdDispatchBase(
        VkCommandBuffer commandBuffer,
        uint32_t baseGroupX,
        uint32_t baseGroupY,
        uint32_t baseGroupZ,
        uint32_t groupCountX,
        uint32_t groupCountY,
        uint32_t groupCountZ) {
    PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)commandBuffer;
    if (cmd) {
        cmd->dispatch_x = groupCountX;
        cmd->dispatch_y = groupCountY;
        cmd->dispatch_z = groupCountZ;
        cmd->has_dispatch = true;
        validate_bound_descriptor_layouts_before_dispatch(cmd);
        if (cmd->dispatch_op_count < PDOCKER_VK_MAX_DISPATCH_OPS) {
            uint32_t op_index = cmd->dispatch_op_count++;
            PdockerVkDispatchOp *op = &cmd->dispatch_ops[op_index];
            memset(op, 0, sizeof(*op));
            op->pipeline = cmd->compute_pipeline;
            memcpy(op->set_handles, cmd->bound_set_handles, sizeof(op->set_handles));
            memcpy(op->set_snapshots, cmd->bound_set_snapshots, sizeof(op->set_snapshots));
            memcpy(op->set_snapshot_used, cmd->bound_set_used, sizeof(op->set_snapshot_used));
            op->dispatch_x = groupCountX;
            op->dispatch_y = groupCountY;
            op->dispatch_z = groupCountZ;
            op->base_group_x = baseGroupX;
            op->base_group_y = baseGroupY;
            op->base_group_z = baseGroupZ;
            op->dispatch_indirect = false;
            op->dispatch_indirect_buffer = NULL;
            op->dispatch_indirect_offset = 0;
            op->push_constant_size = cmd->push_constant_size;
            if (op->pipeline && op->pipeline->layout &&
                op->pipeline->layout->push_constant_size > op->push_constant_size) {
                op->push_constant_size = op->pipeline->layout->push_constant_size;
            }
            memcpy(op->push_constants, cmd->push_constants, sizeof(op->push_constants));
            memcpy(op->push_constant_ops, cmd->push_constant_ops, sizeof(op->push_constant_ops));
            op->push_constant_op_count = cmd->push_constant_op_count;
            PdockerVkCommandOp command_op;
            memset(&command_op, 0, sizeof(command_op));
            command_op.type = PDOCKER_VK_COMMAND_DISPATCH;
            command_op.index = op_index;
            (void)append_command_op(cmd, &command_op);
        } else if (trace_allocations() || getenv("PDOCKER_VULKAN_ICD_DEBUG")) {
            fprintf(stderr,
                    "pdocker-vulkan-icd: dispatch-base command buffer full max=%u\n",
                    PDOCKER_VK_MAX_DISPATCH_OPS);
        }
    }
}

VKAPI_ATTR void VKAPI_CALL vkCmdDispatchBaseKHR(
        VkCommandBuffer commandBuffer,
        uint32_t baseGroupX,
        uint32_t baseGroupY,
        uint32_t baseGroupZ,
        uint32_t groupCountX,
        uint32_t groupCountY,
        uint32_t groupCountZ) {
    vkCmdDispatchBase(commandBuffer,
                      baseGroupX, baseGroupY, baseGroupZ,
                      groupCountX, groupCountY, groupCountZ);
}

VKAPI_ATTR void VKAPI_CALL vkCmdDispatchIndirect(
        VkCommandBuffer commandBuffer,
        VkBuffer buffer,
        VkDeviceSize offset) {
    PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)commandBuffer;
    PdockerVkBuffer *indirect_buffer = (PdockerVkBuffer *)buffer;
    if (cmd) {
        cmd->dispatch_x = 0;
        cmd->dispatch_y = 0;
        cmd->dispatch_z = 0;
        cmd->has_dispatch = true;
        validate_bound_descriptor_layouts_before_dispatch(cmd);
        if (cmd->dispatch_op_count < PDOCKER_VK_MAX_DISPATCH_OPS) {
            uint32_t op_index = cmd->dispatch_op_count++;
            PdockerVkDispatchOp *op = &cmd->dispatch_ops[op_index];
            memset(op, 0, sizeof(*op));
            op->pipeline = cmd->compute_pipeline;
            memcpy(op->set_handles, cmd->bound_set_handles, sizeof(op->set_handles));
            memcpy(op->set_snapshots, cmd->bound_set_snapshots, sizeof(op->set_snapshots));
            memcpy(op->set_snapshot_used, cmd->bound_set_used, sizeof(op->set_snapshot_used));
            op->base_group_x = 0;
            op->base_group_y = 0;
            op->base_group_z = 0;
            op->dispatch_indirect = true;
            op->dispatch_indirect_buffer = indirect_buffer;
            op->dispatch_indirect_offset = offset;
            op->push_constant_size = cmd->push_constant_size;
            if (op->pipeline && op->pipeline->layout &&
                op->pipeline->layout->push_constant_size > op->push_constant_size) {
                op->push_constant_size = op->pipeline->layout->push_constant_size;
            }
            memcpy(op->push_constants, cmd->push_constants, sizeof(op->push_constants));
            memcpy(op->push_constant_ops, cmd->push_constant_ops, sizeof(op->push_constant_ops));
            op->push_constant_op_count = cmd->push_constant_op_count;
            PdockerVkCommandOp command_op;
            memset(&command_op, 0, sizeof(command_op));
            command_op.type = PDOCKER_VK_COMMAND_DISPATCH;
            command_op.index = op_index;
            (void)append_command_op(cmd, &command_op);
        } else if (trace_allocations() || getenv("PDOCKER_VULKAN_ICD_DEBUG")) {
            fprintf(stderr,
                    "pdocker-vulkan-icd: indirect dispatch command buffer full max=%u\n",
                    PDOCKER_VK_MAX_DISPATCH_OPS);
        }
    }
}

VKAPI_ATTR void VKAPI_CALL vkCmdPushConstants(
        VkCommandBuffer commandBuffer,
        VkPipelineLayout layout,
        VkShaderStageFlags stageFlags,
        uint32_t offset,
        uint32_t size,
        const void *pValues) {
    (void)layout;
    PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)commandBuffer;
    if (!cmd || !pValues || offset >= PDOCKER_VK_MAX_PUSH_BYTES) return;
    uint64_t end64 = (uint64_t)offset + (uint64_t)size;
    if (end64 > PDOCKER_VK_MAX_PUSH_BYTES) size = PDOCKER_VK_MAX_PUSH_BYTES - offset;
    memcpy(cmd->push_constants + offset, pValues, size);
    if (offset + size > cmd->push_constant_size) cmd->push_constant_size = offset + size;
    if (cmd->push_constant_op_count < PDOCKER_VK_MAX_PUSH_CONSTANT_OPS) {
        PdockerVkPushConstantOpSnapshot *op =
            &cmd->push_constant_ops[cmd->push_constant_op_count++];
        op->stage_flags = stageFlags;
        op->offset = offset;
        op->size = size;
        PdockerVkPipelineLayout *captured_layout = (PdockerVkPipelineLayout *)layout;
        op->layout_id = captured_layout ? captured_layout->layout_id : 0;
        op->value_hash = fnv1a64_bytes(pValues, size);
        PdockerVkGraphicsCommandRecord record;
        memset(&record, 0, sizeof(record));
        record.command_type = PDOCKER_GPU_GRAPHICS_V6_COMMAND_PUSH_CONSTANTS;
        record.layout_id = op->layout_id;
        record.flags = (uint32_t)stageFlags;
        record.push_op_index = cmd->push_constant_op_count - 1u;
        (void)append_graphics_command_record(cmd, &record);
    } else {
        cmd->graphics_unsupported = true;
    }
}

static bool image_subresource_range_is_whole_image(
        const PdockerVkImage *image,
        const VkImageSubresourceRange *range) {
    if (!image || !range) return false;
    uint32_t levels = range->levelCount == VK_REMAINING_MIP_LEVELS
        ? image->mip_levels - range->baseMipLevel
        : range->levelCount;
    uint32_t layers = range->layerCount == VK_REMAINING_ARRAY_LAYERS
        ? image->array_layers - range->baseArrayLayer
        : range->layerCount;
    return range->baseMipLevel == 0 &&
           range->baseArrayLayer == 0 &&
           levels == image->mip_levels &&
           layers == image->array_layers;
}

static bool normalize_image_subresource_range(
        const PdockerVkImage *image,
        const VkImageSubresourceRange *range,
        VkImageSubresourceRange *out) {
    if (!image || !range || !out) return false;
    *out = *range;
    if (out->baseMipLevel >= image->mip_levels ||
        out->baseArrayLayer >= image->array_layers) {
        return false;
    }
    if (out->levelCount == VK_REMAINING_MIP_LEVELS) {
        out->levelCount = image->mip_levels - out->baseMipLevel;
    }
    if (out->layerCount == VK_REMAINING_ARRAY_LAYERS) {
        out->layerCount = image->array_layers - out->baseArrayLayer;
    }
    if (out->levelCount == 0 ||
        out->levelCount > image->mip_levels - out->baseMipLevel ||
        out->layerCount == 0 ||
        out->layerCount > image->array_layers - out->baseArrayLayer) {
        return false;
    }
    return true;
}

static void trace_image_layout_mismatch(
        const char *stage,
        const PdockerVkImage *image,
        VkImageLayout requested_layout) {
    if (!stage || !image || image->layout_mixed ||
        requested_layout == VK_IMAGE_LAYOUT_UNDEFINED ||
        requested_layout == image->current_layout) {
        return;
    }
    if (trace_allocations() || getenv("PDOCKER_VULKAN_ICD_DEBUG")) {
        fprintf(stderr,
                "pdocker-vulkan-icd: image layout mismatch stage=%s requested=%u current=%u initial=%u generation=%llu\n",
                stage,
                (unsigned)requested_layout,
                (unsigned)image->current_layout,
                (unsigned)image->initial_layout,
                (unsigned long long)image->layout_generation);
    }
}

static void execute_recorded_image_barrier_op(PdockerVkImageBarrierOp *op) {
    if (!op || !op->image) return;
    if (op->old_layout != VK_IMAGE_LAYOUT_UNDEFINED &&
        op->image->current_layout != op->old_layout &&
        !op->image->layout_mixed &&
        (trace_allocations() || getenv("PDOCKER_VULKAN_ICD_DEBUG"))) {
        fprintf(stderr,
                "pdocker-vulkan-icd: image barrier old-layout mismatch old=%u current=%u new=%u generation=%llu\n",
                (unsigned)op->old_layout,
                (unsigned)op->image->current_layout,
                (unsigned)op->new_layout,
                (unsigned long long)op->image->layout_generation);
    }
    if (op->src_queue_family_index != VK_QUEUE_FAMILY_IGNORED ||
        op->dst_queue_family_index != VK_QUEUE_FAMILY_IGNORED) {
        if (trace_allocations() || getenv("PDOCKER_VULKAN_ICD_DEBUG")) {
            fprintf(stderr,
                    "pdocker-vulkan-icd: image barrier queue-family ownership transfer is traced only src=%u dst=%u\n",
                    op->src_queue_family_index,
                    op->dst_queue_family_index);
        }
    }
    if (!image_subresource_range_is_whole_image(op->image, &op->range)) {
        op->image->layout_mixed = true;
    } else {
        op->image->current_layout = op->new_layout;
        op->image->layout_mixed = false;
    }
    op->image->layout_generation = next_vulkan_object_generation();
    (void)op->src_access_mask;
    (void)op->dst_access_mask;
    (void)op->src_stage_mask;
    (void)op->dst_stage_mask;
}

static void record_image_barrier_op(
        VkCommandBuffer commandBuffer,
        PdockerVkImage *image,
        VkImageLayout oldLayout,
        VkImageLayout newLayout,
        VkImageSubresourceRange range,
        VkAccessFlags2 srcAccessMask,
        VkAccessFlags2 dstAccessMask,
        VkPipelineStageFlags2 srcStageMask,
        VkPipelineStageFlags2 dstStageMask,
        uint32_t srcQueueFamilyIndex,
        uint32_t dstQueueFamilyIndex) {
    PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)commandBuffer;
    if (!cmd || !image) return;
    if (cmd->image_barrier_op_count >= PDOCKER_VK_MAX_COPY_OPS) {
        if (trace_allocations() || getenv("PDOCKER_VULKAN_ICD_DEBUG")) {
            fprintf(stderr,
                    "pdocker-vulkan-icd: image-barrier command buffer full max=%u\n",
                    PDOCKER_VK_MAX_COPY_OPS);
        }
        return;
    }
    uint32_t op_index = cmd->image_barrier_op_count++;
    PdockerVkImageBarrierOp *op = &cmd->image_barrier_ops[op_index];
    memset(op, 0, sizeof(*op));
    op->image = image;
    op->old_layout = oldLayout;
    op->new_layout = newLayout;
    op->range = range;
    op->src_access_mask = srcAccessMask;
    op->dst_access_mask = dstAccessMask;
    op->src_stage_mask = srcStageMask;
    op->dst_stage_mask = dstStageMask;
    op->src_queue_family_index = srcQueueFamilyIndex;
    op->dst_queue_family_index = dstQueueFamilyIndex;
    PdockerVkCommandOp command_op;
    memset(&command_op, 0, sizeof(command_op));
    command_op.type = PDOCKER_VK_COMMAND_IMAGE_BARRIER;
    command_op.index = op_index;
    (void)append_command_op(cmd, &command_op);
}

static void record_memory_barrier_op(
        VkCommandBuffer commandBuffer,
        VkAccessFlags2 srcAccessMask,
        VkAccessFlags2 dstAccessMask,
        VkPipelineStageFlags2 srcStageMask,
        VkPipelineStageFlags2 dstStageMask) {
    PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)commandBuffer;
    if (!cmd) return;
    if (cmd->memory_barrier_op_count >= PDOCKER_VK_MAX_COPY_OPS) {
        cmd->graphics_unsupported = true;
        return;
    }
    PdockerVkMemoryBarrierOp *op = &cmd->memory_barrier_ops[cmd->memory_barrier_op_count++];
    memset(op, 0, sizeof(*op));
    op->src_access_mask = srcAccessMask;
    op->dst_access_mask = dstAccessMask;
    op->src_stage_mask = srcStageMask;
    op->dst_stage_mask = dstStageMask;
}

static void record_buffer_barrier_op(
        VkCommandBuffer commandBuffer,
        PdockerVkBuffer *buffer,
        VkDeviceSize offset,
        VkDeviceSize size,
        VkAccessFlags2 srcAccessMask,
        VkAccessFlags2 dstAccessMask,
        VkPipelineStageFlags2 srcStageMask,
        VkPipelineStageFlags2 dstStageMask,
        uint32_t srcQueueFamilyIndex,
        uint32_t dstQueueFamilyIndex) {
    PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)commandBuffer;
    if (!cmd || !buffer) return;
    if (cmd->buffer_barrier_op_count >= PDOCKER_VK_MAX_COPY_OPS) {
        cmd->graphics_unsupported = true;
        return;
    }
    if (size == VK_WHOLE_SIZE) {
        if (offset > buffer->size) {
            cmd->graphics_unsupported = true;
            return;
        }
        size = buffer->size - offset;
    }
    if (offset > buffer->size || size > buffer->size - offset) {
        cmd->graphics_unsupported = true;
        return;
    }
    PdockerVkBufferBarrierOp *op = &cmd->buffer_barrier_ops[cmd->buffer_barrier_op_count++];
    memset(op, 0, sizeof(*op));
    op->buffer = buffer;
    op->offset = offset;
    op->size = size;
    op->src_access_mask = srcAccessMask;
    op->dst_access_mask = dstAccessMask;
    op->src_stage_mask = srcStageMask;
    op->dst_stage_mask = dstStageMask;
    op->src_queue_family_index = srcQueueFamilyIndex;
    op->dst_queue_family_index = dstQueueFamilyIndex;
}

VKAPI_ATTR void VKAPI_CALL vkCmdPipelineBarrier(
        VkCommandBuffer commandBuffer,
        VkPipelineStageFlags srcStageMask,
        VkPipelineStageFlags dstStageMask,
        VkDependencyFlags dependencyFlags,
        uint32_t memoryBarrierCount,
        const VkMemoryBarrier *pMemoryBarriers,
        uint32_t bufferMemoryBarrierCount,
        const VkBufferMemoryBarrier *pBufferMemoryBarriers,
        uint32_t imageMemoryBarrierCount,
        const VkImageMemoryBarrier *pImageMemoryBarriers) {
    PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)commandBuffer;
    if (cmd) {
        if ((dependencyFlags & ~VK_DEPENDENCY_BY_REGION_BIT) != 0) {
            cmd->graphics_unsupported = true;
        }
        uint32_t memory_barrier_first = cmd->memory_barrier_op_count;
        uint32_t buffer_barrier_first = cmd->buffer_barrier_op_count;
        uint32_t image_barrier_first = cmd->image_barrier_op_count;
        for (uint32_t i = 0; pMemoryBarriers && i < memoryBarrierCount; ++i) {
            const VkMemoryBarrier *b = &pMemoryBarriers[i];
            record_memory_barrier_op(commandBuffer,
                                     (VkAccessFlags2)b->srcAccessMask,
                                     (VkAccessFlags2)b->dstAccessMask,
                                     (VkPipelineStageFlags2)srcStageMask,
                                     (VkPipelineStageFlags2)dstStageMask);
        }
        for (uint32_t i = 0; pBufferMemoryBarriers && i < bufferMemoryBarrierCount; ++i) {
            const VkBufferMemoryBarrier *b = &pBufferMemoryBarriers[i];
            record_buffer_barrier_op(commandBuffer,
                                     (PdockerVkBuffer *)b->buffer,
                                     b->offset,
                                     b->size,
                                     (VkAccessFlags2)b->srcAccessMask,
                                     (VkAccessFlags2)b->dstAccessMask,
                                     (VkPipelineStageFlags2)srcStageMask,
                                     (VkPipelineStageFlags2)dstStageMask,
                                     b->srcQueueFamilyIndex,
                                     b->dstQueueFamilyIndex);
        }
        for (uint32_t i = 0; pImageMemoryBarriers && i < imageMemoryBarrierCount; ++i) {
            const VkImageMemoryBarrier *b = &pImageMemoryBarriers[i];
            record_image_barrier_op(commandBuffer,
                                    (PdockerVkImage *)b->image,
                                    b->oldLayout,
                                    b->newLayout,
                                    b->subresourceRange,
                                    (VkAccessFlags2)b->srcAccessMask,
                                    (VkAccessFlags2)b->dstAccessMask,
                                    (VkPipelineStageFlags2)srcStageMask,
                                    (VkPipelineStageFlags2)dstStageMask,
                                    b->srcQueueFamilyIndex,
                                    b->dstQueueFamilyIndex);
        }
        uint32_t memory_barrier_count = cmd->memory_barrier_op_count - memory_barrier_first;
        uint32_t buffer_barrier_count = cmd->buffer_barrier_op_count - buffer_barrier_first;
        uint32_t image_barrier_count = cmd->image_barrier_op_count - image_barrier_first;
        if (memory_barrier_count || buffer_barrier_count || image_barrier_count) {
            PdockerVkGraphicsCommandRecord record;
            memset(&record, 0, sizeof(record));
            record.command_type = PDOCKER_GPU_GRAPHICS_V6_COMMAND_BARRIER;
            record.flags = dependencyFlags & VK_DEPENDENCY_BY_REGION_BIT;
            record.memory_barrier_op_first = memory_barrier_first;
            record.memory_barrier_op_count = memory_barrier_count;
            record.buffer_barrier_op_first = buffer_barrier_first;
            record.buffer_barrier_op_count = buffer_barrier_count;
            record.image_barrier_op_first = image_barrier_first;
            record.image_barrier_op_count = image_barrier_count;
            (void)append_graphics_command_record(cmd, &record);
        }
        PdockerVkCommandOp op;
        memset(&op, 0, sizeof(op));
        op.type = PDOCKER_VK_COMMAND_BARRIER;
        (void)append_command_op(cmd, &op);
    }
}

VKAPI_ATTR void VKAPI_CALL vkCmdCopyBuffer(
        VkCommandBuffer commandBuffer,
        VkBuffer srcBuffer,
        VkBuffer dstBuffer,
        uint32_t regionCount,
        const VkBufferCopy *pRegions) {
    PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)commandBuffer;
    PdockerVkBuffer *src = (PdockerVkBuffer *)srcBuffer;
    PdockerVkBuffer *dst = (PdockerVkBuffer *)dstBuffer;
    if (!cmd || !src || !dst || !src->memory || !dst->memory || !pRegions) return;
    for (uint32_t i = 0; i < regionCount; ++i) {
        const VkBufferCopy *r = &pRegions[i];
        void *dst_ptr = buffer_ptr(dst, r->dstOffset, r->size);
        void *src_ptr = buffer_ptr(src, r->srcOffset, r->size);
        bool appended = false;
        if (cmd->copy_op_count < PDOCKER_VK_MAX_COPY_OPS) {
            uint32_t op_index = cmd->copy_op_count++;
            PdockerVkCopyOp *op = &cmd->copy_ops[op_index];
            op->src = src;
            op->dst = dst;
            op->region = *r;
            PdockerVkCommandOp command_op;
            memset(&command_op, 0, sizeof(command_op));
            command_op.type = PDOCKER_VK_COMMAND_COPY;
            command_op.index = op_index;
            appended = append_command_op(cmd, &command_op);
        }
        if (trace_allocations()) {
            fprintf(stderr,
                    "pdocker-vulkan-icd: copy-buffer src_size=%zu src_mem=%zu src_off=%llu dst_size=%zu dst_mem=%zu dst_off=%llu bytes=%llu ok=%u\n",
                    src->size,
                    src->memory->size,
                    (unsigned long long)r->srcOffset,
                    dst->size,
                    dst->memory->size,
                    (unsigned long long)r->dstOffset,
                    (unsigned long long)r->size,
                    (src_ptr && dst_ptr && appended) ? 1u : 0u);
        }
        if (!appended && trace_allocations()) {
            fprintf(stderr,
                    "pdocker-vulkan-icd: copy-buffer command buffer full max=%u\n",
                    PDOCKER_VK_MAX_COPY_OPS);
        }
    }
}

static void record_image_copy_op(PdockerVkCommandBuffer *cmd,
                                 PdockerVkImageCopyDirection direction,
                                 PdockerVkBuffer *buffer,
                                 PdockerVkImage *image,
                                 VkImageLayout image_layout,
                                 const VkBufferImageCopy *region) {
    if (!cmd || !buffer || !image || !region) return;
    if (cmd->image_copy_op_count >= PDOCKER_VK_MAX_COPY_OPS) {
        if (trace_allocations() || getenv("PDOCKER_VULKAN_ICD_DEBUG")) {
            fprintf(stderr,
                    "pdocker-vulkan-icd: image-copy command buffer full max=%u\n",
                    PDOCKER_VK_MAX_COPY_OPS);
        }
        return;
    }
    uint32_t op_index = cmd->image_copy_op_count++;
    PdockerVkImageCopyOp *op = &cmd->image_copy_ops[op_index];
    memset(op, 0, sizeof(*op));
    op->direction = direction;
    op->buffer = buffer;
    op->image = image;
    op->image_layout = image_layout;
    op->region = *region;
    PdockerVkCommandOp command_op;
    memset(&command_op, 0, sizeof(command_op));
    command_op.type = PDOCKER_VK_COMMAND_IMAGE_COPY;
    command_op.index = op_index;
    (void)append_command_op(cmd, &command_op);
    if (trace_allocations()) {
        fprintf(stderr,
                "pdocker-vulkan-icd: record-image-copy dir=%u buffer_size=%zu image_req=%llu mip=%u layer=%u layers=%u extent=%ux%ux%u buffer_offset=%llu\n",
                (unsigned)direction,
                buffer->size,
                (unsigned long long)image->requirements_size,
                region->imageSubresource.mipLevel,
                region->imageSubresource.baseArrayLayer,
                region->imageSubresource.layerCount,
                region->imageExtent.width,
                region->imageExtent.height,
                region->imageExtent.depth,
                (unsigned long long)region->bufferOffset);
    }
}

VKAPI_ATTR void VKAPI_CALL vkCmdCopyBufferToImage(
        VkCommandBuffer commandBuffer,
        VkBuffer srcBuffer,
        VkImage dstImage,
        VkImageLayout dstImageLayout,
        uint32_t regionCount,
        const VkBufferImageCopy *pRegions) {
    PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)commandBuffer;
    PdockerVkBuffer *src = (PdockerVkBuffer *)srcBuffer;
    PdockerVkImage *dst = (PdockerVkImage *)dstImage;
    if (!cmd || !src || !dst || !src->memory || !dst->memory || !pRegions) return;
    for (uint32_t i = 0; i < regionCount; ++i) {
        record_image_copy_op(cmd,
                             PDOCKER_VK_IMAGE_COPY_BUFFER_TO_IMAGE,
                             src,
                             dst,
                             dstImageLayout,
                             &pRegions[i]);
    }
}

VKAPI_ATTR void VKAPI_CALL vkCmdCopyImageToBuffer(
        VkCommandBuffer commandBuffer,
        VkImage srcImage,
        VkImageLayout srcImageLayout,
        VkBuffer dstBuffer,
        uint32_t regionCount,
        const VkBufferImageCopy *pRegions) {
    PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)commandBuffer;
    PdockerVkImage *src = (PdockerVkImage *)srcImage;
    PdockerVkBuffer *dst = (PdockerVkBuffer *)dstBuffer;
    if (!cmd || !src || !dst || !src->memory || !dst->memory || !pRegions) return;
    for (uint32_t i = 0; i < regionCount; ++i) {
        record_image_copy_op(cmd,
                             PDOCKER_VK_IMAGE_COPY_IMAGE_TO_BUFFER,
                             dst,
                             src,
                             srcImageLayout,
                             &pRegions[i]);
    }
}

static void record_image_to_image_copy_op(PdockerVkCommandBuffer *cmd,
                                          PdockerVkImage *src,
                                          PdockerVkImage *dst,
                                          VkImageLayout src_layout,
                                          VkImageLayout dst_layout,
                                          const VkImageCopy *region) {
    if (!cmd || !src || !dst || !region) return;
    if (cmd->image_to_image_copy_op_count >= PDOCKER_VK_MAX_COPY_OPS) {
        if (trace_allocations() || getenv("PDOCKER_VULKAN_ICD_DEBUG")) {
            fprintf(stderr,
                    "pdocker-vulkan-icd: image-to-image copy command buffer full max=%u\n",
                    PDOCKER_VK_MAX_COPY_OPS);
        }
        return;
    }
    uint32_t op_index = cmd->image_to_image_copy_op_count++;
    PdockerVkImageToImageCopyOp *op = &cmd->image_to_image_copy_ops[op_index];
    memset(op, 0, sizeof(*op));
    op->src = src;
    op->dst = dst;
    op->src_layout = src_layout;
    op->dst_layout = dst_layout;
    op->region = *region;
    PdockerVkCommandOp command_op;
    memset(&command_op, 0, sizeof(command_op));
    command_op.type = PDOCKER_VK_COMMAND_IMAGE_TO_IMAGE_COPY;
    command_op.index = op_index;
    (void)append_command_op(cmd, &command_op);
    if (trace_allocations()) {
        fprintf(stderr,
                "pdocker-vulkan-icd: record-image-to-image-copy src_req=%llu dst_req=%llu src_mip=%u dst_mip=%u src_layer=%u dst_layer=%u layers=%u extent=%ux%ux%u\n",
                (unsigned long long)src->requirements_size,
                (unsigned long long)dst->requirements_size,
                region->srcSubresource.mipLevel,
                region->dstSubresource.mipLevel,
                region->srcSubresource.baseArrayLayer,
                region->dstSubresource.baseArrayLayer,
                region->srcSubresource.layerCount,
                region->extent.width,
                region->extent.height,
                region->extent.depth);
    }
}

VKAPI_ATTR void VKAPI_CALL vkCmdCopyImage(
        VkCommandBuffer commandBuffer,
        VkImage srcImage,
        VkImageLayout srcImageLayout,
        VkImage dstImage,
        VkImageLayout dstImageLayout,
        uint32_t regionCount,
        const VkImageCopy *pRegions) {
    PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)commandBuffer;
    PdockerVkImage *src = (PdockerVkImage *)srcImage;
    PdockerVkImage *dst = (PdockerVkImage *)dstImage;
    if (!cmd || !src || !dst || !src->memory || !dst->memory || !pRegions) return;
    for (uint32_t i = 0; i < regionCount; ++i) {
        record_image_to_image_copy_op(cmd, src, dst, srcImageLayout, dstImageLayout, &pRegions[i]);
    }
}

static void record_clear_color_image_op(PdockerVkCommandBuffer *cmd,
                                        PdockerVkImage *image,
                                        VkImageLayout image_layout,
                                        const VkClearColorValue *color,
                                        const VkImageSubresourceRange *range) {
    if (!cmd || !image || !color || !range) return;
    if (cmd->image_clear_op_count >= PDOCKER_VK_MAX_COPY_OPS) {
        if (trace_allocations() || getenv("PDOCKER_VULKAN_ICD_DEBUG")) {
            fprintf(stderr,
                    "pdocker-vulkan-icd: clear-color-image command buffer full max=%u\n",
                    PDOCKER_VK_MAX_COPY_OPS);
        }
        return;
    }
    uint32_t op_index = cmd->image_clear_op_count++;
    PdockerVkImageClearOp *op = &cmd->image_clear_ops[op_index];
    memset(op, 0, sizeof(*op));
    op->image = image;
    op->image_layout = image_layout;
    op->color = *color;
    VkImageSubresourceRange normalized_range;
    op->range = normalize_image_subresource_range(image, range, &normalized_range)
        ? normalized_range
        : *range;
    PdockerVkCommandOp command_op;
    memset(&command_op, 0, sizeof(command_op));
    command_op.type = PDOCKER_VK_COMMAND_CLEAR_COLOR_IMAGE;
    command_op.index = op_index;
    (void)append_command_op(cmd, &command_op);
    if (trace_allocations()) {
        fprintf(stderr,
                "pdocker-vulkan-icd: record-clear-color-image image_req=%llu base_mip=%u levels=%u base_layer=%u layers=%u color_u32=%08x,%08x,%08x,%08x\n",
                (unsigned long long)image->requirements_size,
                op->range.baseMipLevel,
                op->range.levelCount,
                op->range.baseArrayLayer,
                op->range.layerCount,
                color->uint32[0],
                color->uint32[1],
                color->uint32[2],
                color->uint32[3]);
    }
}

VKAPI_ATTR void VKAPI_CALL vkCmdClearColorImage(
        VkCommandBuffer commandBuffer,
        VkImage image,
        VkImageLayout imageLayout,
        const VkClearColorValue *pColor,
        uint32_t rangeCount,
        const VkImageSubresourceRange *pRanges) {
    PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)commandBuffer;
    PdockerVkImage *img = (PdockerVkImage *)image;
    if (!cmd || !img || !img->memory || !pColor || !pRanges) return;
    for (uint32_t i = 0; i < rangeCount; ++i) {
        record_clear_color_image_op(cmd, img, imageLayout, pColor, &pRanges[i]);
    }
}

static void record_resolve_image_op(PdockerVkCommandBuffer *cmd,
                                    PdockerVkImage *src,
                                    PdockerVkImage *dst,
                                    VkImageLayout src_layout,
                                    VkImageLayout dst_layout,
                                    const VkImageResolve *region) {
    if (!cmd || !src || !dst || !region) return;
    if (cmd->image_resolve_op_count >= PDOCKER_VK_MAX_COPY_OPS) {
        if (trace_allocations() || getenv("PDOCKER_VULKAN_ICD_DEBUG")) {
            fprintf(stderr,
                    "pdocker-vulkan-icd: resolve-image command buffer full max=%u\n",
                    PDOCKER_VK_MAX_COPY_OPS);
        }
        return;
    }
    uint32_t op_index = cmd->image_resolve_op_count++;
    PdockerVkImageResolveOp *op = &cmd->image_resolve_ops[op_index];
    memset(op, 0, sizeof(*op));
    op->src = src;
    op->dst = dst;
    op->src_layout = src_layout;
    op->dst_layout = dst_layout;
    op->region = *region;
    PdockerVkCommandOp command_op;
    memset(&command_op, 0, sizeof(command_op));
    command_op.type = PDOCKER_VK_COMMAND_RESOLVE_IMAGE;
    command_op.index = op_index;
    (void)append_command_op(cmd, &command_op);
    if (trace_allocations()) {
        fprintf(stderr,
                "pdocker-vulkan-icd: record-resolve-image src_req=%llu dst_req=%llu src_mip=%u dst_mip=%u layers=%u extent=%ux%ux%u\n",
                (unsigned long long)src->requirements_size,
                (unsigned long long)dst->requirements_size,
                region->srcSubresource.mipLevel,
                region->dstSubresource.mipLevel,
                region->srcSubresource.layerCount,
                region->extent.width,
                region->extent.height,
                region->extent.depth);
    }
}

VKAPI_ATTR void VKAPI_CALL vkCmdResolveImage(
        VkCommandBuffer commandBuffer,
        VkImage srcImage,
        VkImageLayout srcImageLayout,
        VkImage dstImage,
        VkImageLayout dstImageLayout,
        uint32_t regionCount,
        const VkImageResolve *pRegions) {
    PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)commandBuffer;
    PdockerVkImage *src = (PdockerVkImage *)srcImage;
    PdockerVkImage *dst = (PdockerVkImage *)dstImage;
    if (!cmd || !src || !dst || !src->memory || !dst->memory || !pRegions) return;
    for (uint32_t i = 0; i < regionCount; ++i) {
        record_resolve_image_op(cmd, src, dst, srcImageLayout, dstImageLayout, &pRegions[i]);
    }
}

static void record_blit_image_op(PdockerVkCommandBuffer *cmd,
                                 PdockerVkImage *src,
                                 PdockerVkImage *dst,
                                 VkImageLayout src_layout,
                                 VkImageLayout dst_layout,
                                 const VkImageBlit *region,
                                 VkFilter filter) {
    if (!cmd || !src || !dst || !region) return;
    if (cmd->image_blit_op_count >= PDOCKER_VK_MAX_COPY_OPS) {
        if (trace_allocations() || getenv("PDOCKER_VULKAN_ICD_DEBUG")) {
            fprintf(stderr,
                    "pdocker-vulkan-icd: blit-image command buffer full max=%u\n",
                    PDOCKER_VK_MAX_COPY_OPS);
        }
        return;
    }
    uint32_t op_index = cmd->image_blit_op_count++;
    PdockerVkImageBlitOp *op = &cmd->image_blit_ops[op_index];
    memset(op, 0, sizeof(*op));
    op->src = src;
    op->dst = dst;
    op->src_layout = src_layout;
    op->dst_layout = dst_layout;
    op->region = *region;
    op->filter = filter;
    PdockerVkCommandOp command_op;
    memset(&command_op, 0, sizeof(command_op));
    command_op.type = PDOCKER_VK_COMMAND_BLIT_IMAGE;
    command_op.index = op_index;
    (void)append_command_op(cmd, &command_op);
    if (trace_allocations()) {
        fprintf(stderr,
                "pdocker-vulkan-icd: record-blit-image src_req=%llu dst_req=%llu src_mip=%u dst_mip=%u layers=%u src=(%d,%d,%d)->(%d,%d,%d) dst=(%d,%d,%d)->(%d,%d,%d) filter=%u\n",
                (unsigned long long)src->requirements_size,
                (unsigned long long)dst->requirements_size,
                region->srcSubresource.mipLevel,
                region->dstSubresource.mipLevel,
                region->srcSubresource.layerCount,
                region->srcOffsets[0].x,
                region->srcOffsets[0].y,
                region->srcOffsets[0].z,
                region->srcOffsets[1].x,
                region->srcOffsets[1].y,
                region->srcOffsets[1].z,
                region->dstOffsets[0].x,
                region->dstOffsets[0].y,
                region->dstOffsets[0].z,
                region->dstOffsets[1].x,
                region->dstOffsets[1].y,
                region->dstOffsets[1].z,
                (unsigned)filter);
    }
}

VKAPI_ATTR void VKAPI_CALL vkCmdBlitImage(
        VkCommandBuffer commandBuffer,
        VkImage srcImage,
        VkImageLayout srcImageLayout,
        VkImage dstImage,
        VkImageLayout dstImageLayout,
        uint32_t regionCount,
        const VkImageBlit *pRegions,
        VkFilter filter) {
    PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)commandBuffer;
    PdockerVkImage *src = (PdockerVkImage *)srcImage;
    PdockerVkImage *dst = (PdockerVkImage *)dstImage;
    if (!cmd || !src || !dst || !src->memory || !dst->memory || !pRegions) return;
    for (uint32_t i = 0; i < regionCount; ++i) {
        record_blit_image_op(cmd, src, dst, srcImageLayout, dstImageLayout, &pRegions[i], filter);
    }
}

static void record_clear_depth_stencil_image_op(PdockerVkCommandBuffer *cmd,
                                                PdockerVkImage *image,
                                                VkImageLayout image_layout,
                                                const VkClearDepthStencilValue *value,
                                                const VkImageSubresourceRange *range) {
    if (!cmd || !image || !value || !range) return;
    if (cmd->depth_stencil_clear_op_count >= PDOCKER_VK_MAX_COPY_OPS) {
        if (trace_allocations() || getenv("PDOCKER_VULKAN_ICD_DEBUG")) {
            fprintf(stderr,
                    "pdocker-vulkan-icd: clear-depth-stencil-image command buffer full max=%u\n",
                    PDOCKER_VK_MAX_COPY_OPS);
        }
        return;
    }
    uint32_t op_index = cmd->depth_stencil_clear_op_count++;
    PdockerVkDepthStencilClearOp *op = &cmd->depth_stencil_clear_ops[op_index];
    memset(op, 0, sizeof(*op));
    op->image = image;
    op->image_layout = image_layout;
    op->value = *value;
    VkImageSubresourceRange normalized_range;
    op->range = normalize_image_subresource_range(image, range, &normalized_range)
        ? normalized_range
        : *range;
    PdockerVkCommandOp command_op;
    memset(&command_op, 0, sizeof(command_op));
    command_op.type = PDOCKER_VK_COMMAND_CLEAR_DEPTH_STENCIL_IMAGE;
    command_op.index = op_index;
    (void)append_command_op(cmd, &command_op);
    if (trace_allocations()) {
        fprintf(stderr,
                "pdocker-vulkan-icd: record-clear-depth-stencil-image image_req=%llu base_mip=%u levels=%u base_layer=%u layers=%u aspect=0x%x depth=%f stencil=%u\n",
                (unsigned long long)image->requirements_size,
                op->range.baseMipLevel,
                op->range.levelCount,
                op->range.baseArrayLayer,
                op->range.layerCount,
                (unsigned)op->range.aspectMask,
                value->depth,
                value->stencil);
    }
}

VKAPI_ATTR void VKAPI_CALL vkCmdClearDepthStencilImage(
        VkCommandBuffer commandBuffer,
        VkImage image,
        VkImageLayout imageLayout,
        const VkClearDepthStencilValue *pDepthStencil,
        uint32_t rangeCount,
        const VkImageSubresourceRange *pRanges) {
    PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)commandBuffer;
    PdockerVkImage *img = (PdockerVkImage *)image;
    if (!cmd || !img || !img->memory || !pDepthStencil || !pRanges) return;
    for (uint32_t i = 0; i < rangeCount; ++i) {
        record_clear_depth_stencil_image_op(cmd, img, imageLayout, pDepthStencil, &pRanges[i]);
    }
}

VKAPI_ATTR void VKAPI_CALL vkCmdCopyBuffer2(
        VkCommandBuffer commandBuffer,
        const VkCopyBufferInfo2 *pCopyBufferInfo) {
    if (!pCopyBufferInfo || !pCopyBufferInfo->pRegions) return;
    PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)commandBuffer;
    PdockerVkBuffer *src = (PdockerVkBuffer *)pCopyBufferInfo->srcBuffer;
    PdockerVkBuffer *dst = (PdockerVkBuffer *)pCopyBufferInfo->dstBuffer;
    if (!cmd || !src || !dst || !src->memory || !dst->memory) return;
    for (uint32_t i = 0; i < pCopyBufferInfo->regionCount; ++i) {
        const VkBufferCopy2 *r2 = &pCopyBufferInfo->pRegions[i];
        VkBufferCopy r = {
            .srcOffset = r2->srcOffset,
            .dstOffset = r2->dstOffset,
            .size = r2->size,
        };
        vkCmdCopyBuffer(commandBuffer,
                        pCopyBufferInfo->srcBuffer,
                        pCopyBufferInfo->dstBuffer,
                        1,
                        &r);
    }
}

VKAPI_ATTR void VKAPI_CALL vkCmdCopyImage2(
        VkCommandBuffer commandBuffer,
        const VkCopyImageInfo2 *pCopyImageInfo) {
    if (!pCopyImageInfo || !pCopyImageInfo->pRegions) return;
    for (uint32_t i = 0; i < pCopyImageInfo->regionCount; ++i) {
        const VkImageCopy2 *r2 = &pCopyImageInfo->pRegions[i];
        VkImageCopy r = {
            .srcSubresource = r2->srcSubresource,
            .srcOffset = r2->srcOffset,
            .dstSubresource = r2->dstSubresource,
            .dstOffset = r2->dstOffset,
            .extent = r2->extent,
        };
        vkCmdCopyImage(commandBuffer,
                       pCopyImageInfo->srcImage,
                       pCopyImageInfo->srcImageLayout,
                       pCopyImageInfo->dstImage,
                       pCopyImageInfo->dstImageLayout,
                       1,
                       &r);
    }
}

VKAPI_ATTR void VKAPI_CALL vkCmdCopyBufferToImage2(
        VkCommandBuffer commandBuffer,
        const VkCopyBufferToImageInfo2 *pCopyBufferToImageInfo) {
    if (!pCopyBufferToImageInfo || !pCopyBufferToImageInfo->pRegions) return;
    for (uint32_t i = 0; i < pCopyBufferToImageInfo->regionCount; ++i) {
        const VkBufferImageCopy2 *r2 = &pCopyBufferToImageInfo->pRegions[i];
        VkBufferImageCopy r = {
            .bufferOffset = r2->bufferOffset,
            .bufferRowLength = r2->bufferRowLength,
            .bufferImageHeight = r2->bufferImageHeight,
            .imageSubresource = r2->imageSubresource,
            .imageOffset = r2->imageOffset,
            .imageExtent = r2->imageExtent,
        };
        vkCmdCopyBufferToImage(commandBuffer,
                               pCopyBufferToImageInfo->srcBuffer,
                               pCopyBufferToImageInfo->dstImage,
                               pCopyBufferToImageInfo->dstImageLayout,
                               1,
                               &r);
    }
}

VKAPI_ATTR void VKAPI_CALL vkCmdCopyImageToBuffer2(
        VkCommandBuffer commandBuffer,
        const VkCopyImageToBufferInfo2 *pCopyImageToBufferInfo) {
    if (!pCopyImageToBufferInfo || !pCopyImageToBufferInfo->pRegions) return;
    for (uint32_t i = 0; i < pCopyImageToBufferInfo->regionCount; ++i) {
        const VkBufferImageCopy2 *r2 = &pCopyImageToBufferInfo->pRegions[i];
        VkBufferImageCopy r = {
            .bufferOffset = r2->bufferOffset,
            .bufferRowLength = r2->bufferRowLength,
            .bufferImageHeight = r2->bufferImageHeight,
            .imageSubresource = r2->imageSubresource,
            .imageOffset = r2->imageOffset,
            .imageExtent = r2->imageExtent,
        };
        vkCmdCopyImageToBuffer(commandBuffer,
                               pCopyImageToBufferInfo->srcImage,
                               pCopyImageToBufferInfo->srcImageLayout,
                               pCopyImageToBufferInfo->dstBuffer,
                               1,
                               &r);
    }
}

VKAPI_ATTR void VKAPI_CALL vkCmdBlitImage2(
        VkCommandBuffer commandBuffer,
        const VkBlitImageInfo2 *pBlitImageInfo) {
    if (!pBlitImageInfo || !pBlitImageInfo->pRegions) return;
    for (uint32_t i = 0; i < pBlitImageInfo->regionCount; ++i) {
        const VkImageBlit2 *r2 = &pBlitImageInfo->pRegions[i];
        VkImageBlit r = {
            .srcSubresource = r2->srcSubresource,
            .srcOffsets = { r2->srcOffsets[0], r2->srcOffsets[1] },
            .dstSubresource = r2->dstSubresource,
            .dstOffsets = { r2->dstOffsets[0], r2->dstOffsets[1] },
        };
        vkCmdBlitImage(commandBuffer,
                       pBlitImageInfo->srcImage,
                       pBlitImageInfo->srcImageLayout,
                       pBlitImageInfo->dstImage,
                       pBlitImageInfo->dstImageLayout,
                       1,
                       &r,
                       pBlitImageInfo->filter);
    }
}

VKAPI_ATTR void VKAPI_CALL vkCmdResolveImage2(
        VkCommandBuffer commandBuffer,
        const VkResolveImageInfo2 *pResolveImageInfo) {
    if (!pResolveImageInfo || !pResolveImageInfo->pRegions) return;
    for (uint32_t i = 0; i < pResolveImageInfo->regionCount; ++i) {
        const VkImageResolve2 *r2 = &pResolveImageInfo->pRegions[i];
        VkImageResolve r = {
            .srcSubresource = r2->srcSubresource,
            .srcOffset = r2->srcOffset,
            .dstSubresource = r2->dstSubresource,
            .dstOffset = r2->dstOffset,
            .extent = r2->extent,
        };
        vkCmdResolveImage(commandBuffer,
                          pResolveImageInfo->srcImage,
                          pResolveImageInfo->srcImageLayout,
                          pResolveImageInfo->dstImage,
                          pResolveImageInfo->dstImageLayout,
                          1,
                          &r);
    }
}

VKAPI_ATTR void VKAPI_CALL vkCmdFillBuffer(
        VkCommandBuffer commandBuffer,
        VkBuffer dstBuffer,
        VkDeviceSize dstOffset,
        VkDeviceSize size,
        uint32_t data) {
    PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)commandBuffer;
    PdockerVkBuffer *dst = (PdockerVkBuffer *)dstBuffer;
    if (!dst || !dst->memory) return;
    size_t available = buffer_available(dst, dstOffset);
    size_t bytes = size == VK_WHOLE_SIZE ? available : (size_t)size;
    if (bytes > available) bytes = available;
    if (trace_allocations()) {
        fprintf(stderr,
                "pdocker-vulkan-icd: fill-buffer dst_size=%zu dst_mem=%zu off=%llu bytes=%zu available=%zu\n",
                dst->size,
                dst->memory->size,
                (unsigned long long)dstOffset,
                bytes,
                available);
    }
    if (!cmd || bytes == 0) return;
    PdockerVkCommandOp op;
    memset(&op, 0, sizeof(op));
    op.type = PDOCKER_VK_COMMAND_FILL;
    op.buffer = dst;
    op.offset = dstOffset;
    op.size = bytes;
    op.data = data;
    (void)append_command_op(cmd, &op);
}

VKAPI_ATTR void VKAPI_CALL vkCmdUpdateBuffer(
        VkCommandBuffer commandBuffer,
        VkBuffer dstBuffer,
        VkDeviceSize dstOffset,
        VkDeviceSize dataSize,
        const void *pData) {
    PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)commandBuffer;
    PdockerVkBuffer *dst = (PdockerVkBuffer *)dstBuffer;
    if (!dst || !dst->memory || !pData) return;
    size_t available = buffer_available(dst, dstOffset);
    size_t bytes = (size_t)dataSize < available ? (size_t)dataSize : available;
    void *dst_ptr = buffer_ptr(dst, dstOffset, bytes);
    if (trace_allocations()) {
        fprintf(stderr,
                "pdocker-vulkan-icd: update-buffer dst_size=%zu dst_mem=%zu off=%llu bytes=%llu ok=%u\n",
                dst->size,
                dst->memory->size,
                (unsigned long long)dstOffset,
                (unsigned long long)bytes,
                dst_ptr ? 1u : 0u);
    }
    if (!cmd || !dst_ptr || bytes == 0) return;
    void *payload = malloc(bytes);
    if (!payload) return;
    memcpy(payload, pData, bytes);
    PdockerVkCommandOp op;
    memset(&op, 0, sizeof(op));
    op.type = PDOCKER_VK_COMMAND_UPDATE;
    op.buffer = dst;
    op.offset = dstOffset;
    op.size = bytes;
    op.payload = payload;
    if (!append_command_op(cmd, &op)) free(payload);
}

static bool semaphore_wait_satisfied(const PdockerVkSemaphore *sem, uint64_t value) {
    if (!sem) return false;
    if (sem->timeline) return sem->value >= value;
    (void)value;
    return sem->signaled;
}

static void semaphore_complete_wait(PdockerVkSemaphore *sem) {
    if (!sem || sem->timeline) return;
    sem->signaled = false;
}

static void semaphore_complete_signal(PdockerVkSemaphore *sem, uint64_t value) {
    if (!sem) return;
    if (sem->timeline) {
        if (sem->value < value) sem->value = value;
        sem->signaled = true;
        return;
    }
    (void)value;
    sem->signaled = true;
}

static VkResult validate_submit_wait_semaphores(const VkSubmitInfo *submit) {
    if (!submit) return VK_ERROR_INITIALIZATION_FAILED;
    for (uint32_t i = 0; i < submit->waitSemaphoreCount; ++i) {
        PdockerVkSemaphore *sem = submit->pWaitSemaphores
            ? (PdockerVkSemaphore *)submit->pWaitSemaphores[i]
            : NULL;
        uint64_t required_value = sem && sem->timeline ? 0 : 0;
        if (!semaphore_wait_satisfied(sem, required_value)) {
            trace_icd_runtime_failure("semaphore-wait-unsignaled",
                                      VK_ERROR_FEATURE_NOT_PRESENT);
            return VK_ERROR_FEATURE_NOT_PRESENT;
        }
    }
    return VK_SUCCESS;
}

static void complete_submit_semaphores(const VkSubmitInfo *submit) {
    if (!submit) return;
    for (uint32_t i = 0; i < submit->waitSemaphoreCount; ++i) {
        PdockerVkSemaphore *sem = submit->pWaitSemaphores
            ? (PdockerVkSemaphore *)submit->pWaitSemaphores[i]
            : NULL;
        semaphore_complete_wait(sem);
    }
    for (uint32_t i = 0; i < submit->signalSemaphoreCount; ++i) {
        PdockerVkSemaphore *sem = submit->pSignalSemaphores
            ? (PdockerVkSemaphore *)submit->pSignalSemaphores[i]
            : NULL;
        semaphore_complete_signal(sem, sem && sem->timeline ? sem->value + 1 : 0);
    }
}

static VkResult validate_submit2_wait_semaphores(const VkSubmitInfo2 *submit) {
    if (!submit) return VK_ERROR_INITIALIZATION_FAILED;
    for (uint32_t i = 0; i < submit->waitSemaphoreInfoCount; ++i) {
        const VkSemaphoreSubmitInfo *info = submit->pWaitSemaphoreInfos ? &submit->pWaitSemaphoreInfos[i] : NULL;
        PdockerVkSemaphore *sem = info ? (PdockerVkSemaphore *)info->semaphore : NULL;
        uint64_t required_value = sem && sem->timeline ? info->value : 0;
        if (!semaphore_wait_satisfied(sem, required_value)) {
            trace_icd_runtime_failure("semaphore2-wait-unsignaled",
                                      VK_ERROR_FEATURE_NOT_PRESENT);
            return VK_ERROR_FEATURE_NOT_PRESENT;
        }
    }
    return VK_SUCCESS;
}

static void complete_submit2_semaphores(const VkSubmitInfo2 *submit) {
    if (!submit) return;
    for (uint32_t i = 0; i < submit->waitSemaphoreInfoCount; ++i) {
        const VkSemaphoreSubmitInfo *info = submit->pWaitSemaphoreInfos ? &submit->pWaitSemaphoreInfos[i] : NULL;
        PdockerVkSemaphore *sem = info ? (PdockerVkSemaphore *)info->semaphore : NULL;
        semaphore_complete_wait(sem);
    }
    for (uint32_t i = 0; i < submit->signalSemaphoreInfoCount; ++i) {
        const VkSemaphoreSubmitInfo *info = submit->pSignalSemaphoreInfos ? &submit->pSignalSemaphoreInfos[i] : NULL;
        PdockerVkSemaphore *sem = info ? (PdockerVkSemaphore *)info->semaphore : NULL;
        semaphore_complete_signal(sem, sem && sem->timeline ? info->value : 0);
    }
}

static bool command_op_is_graphics_frame_op(PdockerVkCommandOpType type) {
    return type == PDOCKER_VK_COMMAND_GRAPHICS_DRAW ||
           type == PDOCKER_VK_COMMAND_IMAGE_BARRIER ||
           type == PDOCKER_VK_COMMAND_BARRIER ||
           type == PDOCKER_VK_COMMAND_QUERY_BEGIN ||
           type == PDOCKER_VK_COMMAND_QUERY_END ||
           type == PDOCKER_VK_COMMAND_QUERY_RESET ||
           type == PDOCKER_VK_COMMAND_QUERY_TIMESTAMP ||
           type == PDOCKER_VK_COMMAND_COPY_QUERY_RESULTS;
}

static bool command_op_is_host_transfer_or_layout_op(PdockerVkCommandOpType type) {
    switch (type) {
        case PDOCKER_VK_COMMAND_COPY:
        case PDOCKER_VK_COMMAND_IMAGE_COPY:
        case PDOCKER_VK_COMMAND_IMAGE_TO_IMAGE_COPY:
        case PDOCKER_VK_COMMAND_CLEAR_COLOR_IMAGE:
        case PDOCKER_VK_COMMAND_RESOLVE_IMAGE:
        case PDOCKER_VK_COMMAND_BLIT_IMAGE:
        case PDOCKER_VK_COMMAND_CLEAR_DEPTH_STENCIL_IMAGE:
        case PDOCKER_VK_COMMAND_EVENT:
        case PDOCKER_VK_COMMAND_EVENT_WAIT:
        case PDOCKER_VK_COMMAND_QUERY_BEGIN:
        case PDOCKER_VK_COMMAND_QUERY_END:
        case PDOCKER_VK_COMMAND_QUERY_RESET:
        case PDOCKER_VK_COMMAND_QUERY_TIMESTAMP:
        case PDOCKER_VK_COMMAND_COPY_QUERY_RESULTS:
        case PDOCKER_VK_COMMAND_IMAGE_BARRIER:
        case PDOCKER_VK_COMMAND_BARRIER:
        case PDOCKER_VK_COMMAND_FILL:
        case PDOCKER_VK_COMMAND_UPDATE:
            return true;
        default:
            return false;
    }
}

static VkResult execute_recorded_event_wait_op(const PdockerVkCommandOp *op) {
    if (!op || !op->event) {
        trace_icd_runtime_failure("event-wait-invalid", VK_ERROR_INITIALIZATION_FAILED);
        return VK_ERROR_INITIALIZATION_FAILED;
    }
    if (!op->event->signaled) {
        trace_icd_runtime_failure("event-wait-unsignaled", VK_ERROR_FEATURE_NOT_PRESENT);
        return VK_ERROR_FEATURE_NOT_PRESENT;
    }
    return VK_SUCCESS;
}

static VkResult execute_recorded_host_transfer_or_layout_op(
        PdockerVkCommandBuffer *cmd,
        PdockerVkCommandOp *op,
        PdockerVkCopyStats *stats) {
    if (!cmd || !op) return VK_SUCCESS;
    switch (op->type) {
        case PDOCKER_VK_COMMAND_COPY:
            if (op->index < cmd->copy_op_count) execute_recorded_copy_op(&cmd->copy_ops[op->index], stats);
            break;
        case PDOCKER_VK_COMMAND_IMAGE_COPY:
            if (op->index < cmd->image_copy_op_count) execute_recorded_image_copy_op(&cmd->image_copy_ops[op->index], stats);
            break;
        case PDOCKER_VK_COMMAND_IMAGE_TO_IMAGE_COPY:
            if (op->index < cmd->image_to_image_copy_op_count) execute_recorded_image_to_image_copy_op(&cmd->image_to_image_copy_ops[op->index], stats);
            break;
        case PDOCKER_VK_COMMAND_CLEAR_COLOR_IMAGE:
            if (op->index < cmd->image_clear_op_count) execute_recorded_clear_color_image_op(&cmd->image_clear_ops[op->index], stats);
            break;
        case PDOCKER_VK_COMMAND_RESOLVE_IMAGE:
            if (op->index < cmd->image_resolve_op_count) execute_recorded_resolve_image_op(&cmd->image_resolve_ops[op->index], stats);
            break;
        case PDOCKER_VK_COMMAND_BLIT_IMAGE:
            if (op->index < cmd->image_blit_op_count) execute_recorded_blit_image_op(&cmd->image_blit_ops[op->index], stats);
            break;
        case PDOCKER_VK_COMMAND_CLEAR_DEPTH_STENCIL_IMAGE:
            if (op->index < cmd->depth_stencil_clear_op_count) execute_recorded_clear_depth_stencil_image_op(&cmd->depth_stencil_clear_ops[op->index], stats);
            break;
        case PDOCKER_VK_COMMAND_EVENT:
            if (op->event) op->event->signaled = op->event_signaled;
            break;
        case PDOCKER_VK_COMMAND_EVENT_WAIT: {
            VkResult wait_rc = execute_recorded_event_wait_op(op);
            if (wait_rc != VK_SUCCESS) return wait_rc;
            break;
        }
        case PDOCKER_VK_COMMAND_QUERY_BEGIN:
        case PDOCKER_VK_COMMAND_QUERY_END:
        case PDOCKER_VK_COMMAND_QUERY_RESET:
        case PDOCKER_VK_COMMAND_QUERY_TIMESTAMP:
            execute_recorded_query_op(op);
            break;
        case PDOCKER_VK_COMMAND_COPY_QUERY_RESULTS:
            break;
        case PDOCKER_VK_COMMAND_IMAGE_BARRIER:
            if (op->index < cmd->image_barrier_op_count) execute_recorded_image_barrier_op(&cmd->image_barrier_ops[op->index]);
            break;
        case PDOCKER_VK_COMMAND_BARRIER:
            break;
        case PDOCKER_VK_COMMAND_FILL:
            execute_recorded_fill_op(op);
            break;
        case PDOCKER_VK_COMMAND_UPDATE:
            execute_recorded_update_op(op);
            break;
        default:
            break;
    }
    return VK_SUCCESS;
}

static bool command_op_is_graphics_interleavable_transfer_op(PdockerVkCommandOpType type) {
    switch (type) {
        case PDOCKER_VK_COMMAND_COPY:
        case PDOCKER_VK_COMMAND_IMAGE_COPY:
        case PDOCKER_VK_COMMAND_IMAGE_TO_IMAGE_COPY:
        case PDOCKER_VK_COMMAND_CLEAR_COLOR_IMAGE:
        case PDOCKER_VK_COMMAND_RESOLVE_IMAGE:
        case PDOCKER_VK_COMMAND_BLIT_IMAGE:
        case PDOCKER_VK_COMMAND_CLEAR_DEPTH_STENCIL_IMAGE:
        case PDOCKER_VK_COMMAND_FILL:
        case PDOCKER_VK_COMMAND_UPDATE:
            return true;
        default:
            return false;
    }
}

static bool graphics_mixed_submit_plan(
        const PdockerVkCommandBuffer *cmd,
        uint32_t *first_gpu_op_out,
        uint32_t *last_gpu_op_out,
        const char **reason_out) {
    if (!cmd) return false;
    uint32_t first_draw = UINT32_MAX;
    uint32_t last_draw = 0;
    for (uint32_t op_index = 0; op_index < cmd->command_op_count; ++op_index) {
        if (cmd->command_ops[op_index].type == PDOCKER_VK_COMMAND_GRAPHICS_DRAW) {
            if (first_draw == UINT32_MAX) first_draw = op_index;
            last_draw = op_index;
        }
    }
    uint32_t first_gpu_op = UINT32_MAX;
    uint32_t last_gpu_op = 0;
    for (uint32_t record_index = 0; record_index < cmd->graphics_command_op_count; ++record_index) {
        uint32_t sequence = cmd->graphics_command_ops[record_index].command_op_sequence;
        if (sequence < first_gpu_op) first_gpu_op = sequence;
        if (sequence > last_gpu_op) last_gpu_op = sequence;
    }
    for (uint32_t op_index = 0; op_index < cmd->command_op_count; ++op_index) {
        PdockerVkCommandOpType type = cmd->command_ops[op_index].type;
        if (command_op_is_graphics_frame_op(type)) continue;
        if (!command_op_is_host_transfer_or_layout_op(type)) {
            if (reason_out) *reason_out = "graphics-mixed-submit-unimplemented";
            return false;
        }
        bool inside_gpu_frame = first_gpu_op != UINT32_MAX &&
                                op_index >= first_gpu_op && op_index <= last_gpu_op;
        bool interleaved_between_draws = first_draw != UINT32_MAX &&
                                         op_index > first_draw && op_index < last_draw &&
                                         command_op_is_graphics_interleavable_transfer_op(type);
        if (inside_gpu_frame && !interleaved_between_draws) {
            if (reason_out) {
                *reason_out = (first_draw != UINT32_MAX && op_index > first_draw && op_index < last_draw)
                    ? "graphics-mixed-transfer-between-draws-unimplemented"
                    : "graphics-mixed-host-op-inside-gpu-frame-unimplemented";
            }
            return false;
        }
    }
    if (first_gpu_op_out) *first_gpu_op_out = first_gpu_op;
    if (last_gpu_op_out) *last_gpu_op_out = last_gpu_op;
    return true;
}

static VkResult execute_graphics_mixed_host_side_ops(
        PdockerVkCommandBuffer *cmd,
        uint32_t first_draw,
        uint32_t last_draw,
        bool before_graphics,
        PdockerVkCopyStats *stats) {
    if (!cmd) return VK_SUCCESS;
    for (uint32_t op_index = 0; op_index < cmd->command_op_count; ++op_index) {
        PdockerVkCommandOp *op = &cmd->command_ops[op_index];
        if (!command_op_is_host_transfer_or_layout_op(op->type)) continue;
        bool run = false;
        if (first_draw == UINT32_MAX) {
            run = before_graphics;
        } else if (before_graphics) {
            run = op_index < first_draw;
        } else {
            run = op_index > last_draw;
        }
        if (run) {
            VkResult rc = execute_recorded_host_transfer_or_layout_op(cmd, op, stats);
            if (rc != VK_SUCCESS) return rc;
        }
    }
    return VK_SUCCESS;
}

VKAPI_ATTR VkResult VKAPI_CALL vkQueueSubmit(
        VkQueue queue,
        uint32_t submitCount,
        const VkSubmitInfo *pSubmits,
        VkFence fence) {
    (void)queue;
    PdockerVkFence *submit_fence = (PdockerVkFence *)fence;
    if (submit_fence) submit_fence->signaled = false;
    for (uint32_t i = 0; i < submitCount; ++i) {
        if (!pSubmits) return VK_ERROR_INITIALIZATION_FAILED;
        VkResult semaphore_rc = validate_submit_wait_semaphores(&pSubmits[i]);
        if (semaphore_rc != VK_SUCCESS) return semaphore_rc;
        for (uint32_t j = 0; j < pSubmits[i].commandBufferCount; ++j) {
            PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)pSubmits[i].pCommandBuffers[j];
            if (!cmd) return VK_ERROR_INITIALIZATION_FAILED;
            if (cmd->unsupported_descriptor_set_layout) {
                trace_icd_runtime_failure("descriptor-set-index-out-of-range",
                                          VK_ERROR_FEATURE_NOT_PRESENT);
                return VK_ERROR_FEATURE_NOT_PRESENT;
            }
            if (cmd->graphics_unsupported) {
                if (env_truthy_default("PDOCKER_VULKAN_GRAPHICS_V6_VALIDATE_PRODUCER", false)) {
                    int graphics_rc = send_recorded_vulkan_graphics_v6_1_frame(cmd);
                    if (trace_allocations() || getenv("PDOCKER_VULKAN_ICD_DEBUG")) {
                        fprintf(stderr,
                                "pdocker-vulkan-icd: graphics V6.1 validation producer rc=%d\n",
                                graphics_rc);
                    }
                }
                trace_icd_runtime_failure("graphics-command-unimplemented",
                                          VK_ERROR_FEATURE_NOT_PRESENT);
                return VK_ERROR_FEATURE_NOT_PRESENT;
            }
            if (cmd->graphics_command_op_count > 0) {
                uint32_t first_graphics_gpu_op = UINT32_MAX;
                uint32_t last_graphics_gpu_op = 0;
                const char *mixed_submit_reason = NULL;
                if (!graphics_mixed_submit_plan(cmd, &first_graphics_gpu_op,
                                                &last_graphics_gpu_op,
                                                &mixed_submit_reason)) {
                    trace_icd_runtime_failure(mixed_submit_reason ? mixed_submit_reason :
                                              "graphics-mixed-submit-unimplemented",
                                              VK_ERROR_FEATURE_NOT_PRESENT);
                    return VK_ERROR_FEATURE_NOT_PRESENT;
                }
                PdockerVkCopyStats mixed_stats;
                memset(&mixed_stats, 0, sizeof(mixed_stats));
                VkResult mixed_host_rc = execute_graphics_mixed_host_side_ops(
                    cmd, first_graphics_gpu_op, last_graphics_gpu_op, true, &mixed_stats);
                if (mixed_host_rc != VK_SUCCESS) return mixed_host_rc;
                int graphics_rc = send_recorded_vulkan_graphics_v6_1_frame(cmd);
                if (trace_allocations() || getenv("PDOCKER_VULKAN_ICD_DEBUG")) {
                    fprintf(stderr,
                            "pdocker-vulkan-icd: graphics V6.1 mixed submit rc=%d prepost_ops=%zu bytes=%llu\n",
                            graphics_rc,
                            mixed_stats.op_count,
                            (unsigned long long)mixed_stats.memmove_bytes);
                }
                if (graphics_rc != 0) {
                    trace_icd_runtime_failure("graphics-v6-submit-failed",
                                              VK_ERROR_FEATURE_NOT_PRESENT);
                    return VK_ERROR_FEATURE_NOT_PRESENT;
                }
                mixed_host_rc = execute_graphics_mixed_host_side_ops(
                    cmd, first_graphics_gpu_op, last_graphics_gpu_op, false, &mixed_stats);
                if (mixed_host_rc != VK_SUCCESS) return mixed_host_rc;
                continue;
            }
            if (cmd->command_op_count > 0) {
                PdockerVkCopyStats stats;
                memset(&stats, 0, sizeof(stats));
                uint32_t dispatches = 0;
                for (uint32_t op_index = 0; op_index < cmd->command_op_count; ++op_index) {
                    PdockerVkCommandOp *op = &cmd->command_ops[op_index];
                    switch (op->type) {
                        case PDOCKER_VK_COMMAND_COPY:
                            if (op->index < cmd->copy_op_count) {
                                execute_recorded_copy_op(&cmd->copy_ops[op->index], &stats);
                            }
                            break;
                        case PDOCKER_VK_COMMAND_IMAGE_COPY:
                            if (op->index < cmd->image_copy_op_count) {
                                execute_recorded_image_copy_op(
                                    &cmd->image_copy_ops[op->index], &stats);
                            }
                            break;
                        case PDOCKER_VK_COMMAND_IMAGE_TO_IMAGE_COPY:
                            if (op->index < cmd->image_to_image_copy_op_count) {
                                execute_recorded_image_to_image_copy_op(
                                    &cmd->image_to_image_copy_ops[op->index], &stats);
                            }
                            break;
                        case PDOCKER_VK_COMMAND_CLEAR_COLOR_IMAGE:
                            if (op->index < cmd->image_clear_op_count) {
                                execute_recorded_clear_color_image_op(
                                    &cmd->image_clear_ops[op->index], &stats);
                            }
                            break;
                        case PDOCKER_VK_COMMAND_RESOLVE_IMAGE:
                            if (op->index < cmd->image_resolve_op_count) {
                                execute_recorded_resolve_image_op(
                                    &cmd->image_resolve_ops[op->index], &stats);
                            }
                            break;
                        case PDOCKER_VK_COMMAND_BLIT_IMAGE:
                            if (op->index < cmd->image_blit_op_count) {
                                execute_recorded_blit_image_op(
                                    &cmd->image_blit_ops[op->index], &stats);
                            }
                            break;
                        case PDOCKER_VK_COMMAND_CLEAR_DEPTH_STENCIL_IMAGE:
                            if (op->index < cmd->depth_stencil_clear_op_count) {
                                execute_recorded_clear_depth_stencil_image_op(
                                    &cmd->depth_stencil_clear_ops[op->index], &stats);
                            }
                            break;
                        case PDOCKER_VK_COMMAND_EVENT:
                            if (op->event) op->event->signaled = op->event_signaled;
                            break;
                        case PDOCKER_VK_COMMAND_EVENT_WAIT: {
                            VkResult wait_rc = execute_recorded_event_wait_op(op);
                            if (wait_rc != VK_SUCCESS) return wait_rc;
                            break;
                        }
                        case PDOCKER_VK_COMMAND_IMAGE_BARRIER:
                            if (op->index < cmd->image_barrier_op_count) {
                                execute_recorded_image_barrier_op(
                                    &cmd->image_barrier_ops[op->index]);
                            }
                            break;
                        case PDOCKER_VK_COMMAND_QUERY_BEGIN:
                        case PDOCKER_VK_COMMAND_QUERY_END:
                        case PDOCKER_VK_COMMAND_QUERY_RESET:
                        case PDOCKER_VK_COMMAND_QUERY_TIMESTAMP:
                            execute_recorded_query_op(op);
                            break;
                        case PDOCKER_VK_COMMAND_COPY_QUERY_RESULTS:
                            break;
                        case PDOCKER_VK_COMMAND_GRAPHICS_DRAW:
                            trace_icd_runtime_failure("graphics-draw-unimplemented",
                                                      VK_ERROR_FEATURE_NOT_PRESENT);
                            if (trace_allocations() || getenv("PDOCKER_VULKAN_ICD_DEBUG")) {
                                fprintf(stderr,
                                        "pdocker-vulkan-icd: graphics draw unsupported indexed=%u indirect=%u vertices=%u instances=%u rendering=%u renderpass=%u vertex_bound=%u index_bound=%u\n",
                                        op->draw_indexed ? 1u : 0u,
                                        op->draw_indirect ? 1u : 0u,
                                        op->draw_vertex_count,
                                        op->draw_instance_count,
                                        cmd->dynamic_rendering_active ? 1u : 0u,
                                        cmd->render_pass_active ? 1u : 0u,
                                        cmd->vertex_buffer_bound ? 1u : 0u,
                                        cmd->index_buffer_bound ? 1u : 0u);
                            }
                            return VK_ERROR_FEATURE_NOT_PRESENT;
                        case PDOCKER_VK_COMMAND_FILL:
                            execute_recorded_fill_op(op);
                            break;
                        case PDOCKER_VK_COMMAND_UPDATE:
                            execute_recorded_update_op(op);
                            break;
                        case PDOCKER_VK_COMMAND_DISPATCH:
                            if (op->index < cmd->dispatch_op_count) {
                                PdockerVkDispatchOp *dispatch = &cmd->dispatch_ops[op->index];
                                if (!dispatch->pipeline || !dispatch->pipeline->shader ||
                                    dispatch->pipeline->shader->code_size <= sizeof(uint32_t)) {
                                    trace_icd_runtime_failure("dispatch-missing-shader", VK_ERROR_FEATURE_NOT_PRESENT);
                                    return VK_ERROR_FEATURE_NOT_PRESENT;
                                }
                                int generic_rc = send_generic_vulkan_dispatch_op(dispatch);
                                if (generic_rc != 0) {
                                    trace_icd_runtime_failure("generic-dispatch-op", generic_rc);
                                    if (trace_allocations() || getenv("PDOCKER_VULKAN_ICD_DEBUG")) {
                                        fprintf(stderr,
                                                "pdocker-vulkan-icd: generic SPIR-V dispatch failed rc=%d op=%u/%u code_size=%zu first_word=0x%08x dispatch=%u,%u,%u push=%u\n",
                                                generic_rc,
                                                op->index + 1,
                                                cmd->dispatch_op_count,
                                                dispatch->pipeline->shader->code_size,
                                                dispatch->pipeline->shader->first_word,
                                                dispatch->dispatch_x,
                                                dispatch->dispatch_y,
                                                dispatch->dispatch_z,
                                                dispatch->push_constant_size);
                                    }
                                    return VK_ERROR_FEATURE_NOT_PRESENT;
                                }
                                dispatches++;
                            }
                            break;
                        case PDOCKER_VK_COMMAND_BARRIER:
                            break;
                    }
                }
                if (trace_allocations()) {
                    if (stats.op_count > 0) {
                        fprintf(stderr,
                                "pdocker-vulkan-icd: copy-submit summary ops=%zu alias_ops=%zu memmove_ops=%zu skipped_ops=%zu alias_bytes=%llu memmove_bytes=%llu skipped_bytes=%llu\n",
                                stats.op_count,
                                stats.alias_ops,
                                stats.memmove_ops,
                                stats.skipped_ops,
                                (unsigned long long)stats.alias_bytes,
                                (unsigned long long)stats.memmove_bytes,
                                (unsigned long long)stats.skipped_bytes);
                    }
                    fprintf(stderr,
                            "pdocker-vulkan-icd: queue-submit replayed ordered ops=%u dispatches=%u\n",
                            cmd->command_op_count,
                            dispatches);
                }
                continue;
            }
            execute_recorded_copy_ops(cmd);
            if (!cmd->has_dispatch) {
                if (trace_allocations()) {
                    fprintf(stderr, "pdocker-vulkan-icd: queue-submit transfer-only command buffer\n");
                }
                continue;
            }
            if (cmd->dispatch_op_count > 0) {
                bool all_generic = true;
                for (uint32_t op_index = 0; op_index < cmd->dispatch_op_count; ++op_index) {
                    PdockerVkDispatchOp *op = &cmd->dispatch_ops[op_index];
                    if (!op->pipeline || !op->pipeline->shader ||
                        op->pipeline->shader->code_size <= sizeof(uint32_t)) {
                        all_generic = false;
                        break;
                    }
                }
                if (all_generic) {
                    for (uint32_t op_index = 0; op_index < cmd->dispatch_op_count; ++op_index) {
                        PdockerVkDispatchOp *op = &cmd->dispatch_ops[op_index];
                        int generic_rc = send_generic_vulkan_dispatch_op(op);
                        if (generic_rc != 0) {
                            trace_icd_runtime_failure("generic-dispatch-list", generic_rc);
                            if (trace_allocations() || getenv("PDOCKER_VULKAN_ICD_DEBUG")) {
                                fprintf(stderr,
                                        "pdocker-vulkan-icd: generic SPIR-V dispatch failed rc=%d op=%u/%u code_size=%zu first_word=0x%08x dispatch=%u,%u,%u push=%u\n",
                                        generic_rc,
                                        op_index + 1,
                                        cmd->dispatch_op_count,
                                        op->pipeline->shader->code_size,
                                        op->pipeline->shader->first_word,
                                        op->dispatch_x,
                                        op->dispatch_y,
                                        op->dispatch_z,
                                        op->push_constant_size);
                            }
                            return VK_ERROR_FEATURE_NOT_PRESENT;
                        }
                    }
                    if ((trace_allocations() || getenv("PDOCKER_VULKAN_ICD_DEBUG")) &&
                        cmd->dispatch_op_count > 1) {
                        fprintf(stderr,
                                "pdocker-vulkan-icd: queue-submit replayed dispatch ops=%u\n",
                                cmd->dispatch_op_count);
                    }
                    continue;
                }
            }
            if (cmd->compute_pipeline && cmd->compute_pipeline->shader && cmd->compute_pipeline->shader->code_size > sizeof(uint32_t)) {
                int generic_rc = send_generic_vulkan_dispatch(cmd);
                if (generic_rc == 0) continue;
                trace_icd_runtime_failure("generic-dispatch-single", generic_rc);
                if (trace_allocations() || getenv("PDOCKER_VULKAN_ICD_DEBUG")) {
                    fprintf(stderr,
                            "pdocker-vulkan-icd: generic SPIR-V dispatch failed rc=%d code_size=%zu first_word=0x%08x dispatch=%u,%u,%u push=%u\n",
                            generic_rc,
                            cmd->compute_pipeline->shader->code_size,
                            cmd->compute_pipeline->shader->first_word,
                            cmd->dispatch_x,
                            cmd->dispatch_y,
                            cmd->dispatch_z,
                            cmd->push_constant_size);
                }
                return VK_ERROR_FEATURE_NOT_PRESENT;
            }
            PdockerVkDescriptorSet *legacy_set = cmd->bound_set_used[0]
                ? &cmd->bound_set_snapshots[0]
                : NULL;
            if (!cmd || !legacy_set || !legacy_set->storage_buffers[0][0].buffer ||
                !legacy_set->storage_buffers[1][0].buffer || !legacy_set->storage_buffers[2][0].buffer) {
                if (trace_allocations() || getenv("PDOCKER_VULKAN_ICD_DEBUG")) {
                    fprintf(stderr,
                            "pdocker-vulkan-icd: vector-add dispatch missing storage buffers set=%p\n",
                            (void *)legacy_set);
                }
                return VK_ERROR_FEATURE_NOT_PRESENT;
            }
            PdockerVkBuffer *a = legacy_set->storage_buffers[0][0].buffer;
            PdockerVkBuffer *b = legacy_set->storage_buffers[1][0].buffer;
            PdockerVkBuffer *out = legacy_set->storage_buffers[2][0].buffer;
            if (!a->memory || !b->memory || !out->memory) return VK_ERROR_MEMORY_MAP_FAILED;
            size_t n = descriptor_binding_size(&legacy_set->storage_buffers[0][0]) / sizeof(float);
            size_t b_n = descriptor_binding_size(&legacy_set->storage_buffers[1][0]) / sizeof(float);
            size_t out_n = descriptor_binding_size(&legacy_set->storage_buffers[2][0]) / sizeof(float);
            if (b_n < n) n = b_n;
            if (out_n < n) n = out_n;
            if (cmd->dispatch_x && cmd->compute_pipeline && cmd->compute_pipeline->local_size_x) {
                size_t dispatched = (size_t)cmd->dispatch_x * cmd->compute_pipeline->local_size_x;
                if (dispatched < n) n = dispatched;
            }
            int rc = send_vector_add_3fd(n, a->memory->fd, b->memory->fd, out->memory->fd);
            if (rc != 0) return VK_ERROR_DEVICE_LOST;
        }
        complete_submit_semaphores(&pSubmits[i]);
    }
    if (submit_fence) submit_fence->signaled = true;
    return VK_SUCCESS;
}

static void free_submit_info_arrays(VkSubmitInfo *submits, uint32_t submitCount) {
    if (!submits) return;
    for (uint32_t i = 0; i < submitCount; ++i) {
        free((void *)submits[i].pWaitSemaphores);
        free((void *)submits[i].pWaitDstStageMask);
        free((void *)submits[i].pCommandBuffers);
        free((void *)submits[i].pSignalSemaphores);
    }
    free(submits);
}

VKAPI_ATTR VkResult VKAPI_CALL vkQueueSubmit2(
        VkQueue queue,
        uint32_t submitCount,
        const VkSubmitInfo2 *pSubmits,
        VkFence fence) {
    if (submitCount == 0) return vkQueueSubmit(queue, 0, NULL, fence);
    if (!pSubmits) return VK_ERROR_INITIALIZATION_FAILED;
    VkSubmitInfo *legacy_submits = calloc(submitCount, sizeof(*legacy_submits));
    if (!legacy_submits) return VK_ERROR_OUT_OF_HOST_MEMORY;
    VkResult rc = VK_SUCCESS;
    for (uint32_t i = 0; i < submitCount; ++i) {
        const VkSubmitInfo2 *src = &pSubmits[i];
        VkSubmitInfo *dst = &legacy_submits[i];
        dst->sType = VK_STRUCTURE_TYPE_SUBMIT_INFO;
        dst->commandBufferCount = src->commandBufferInfoCount;
        if (src->waitSemaphoreInfoCount > 0 && !src->pWaitSemaphoreInfos) {
            rc = VK_ERROR_INITIALIZATION_FAILED;
            break;
        }
        rc = validate_submit2_wait_semaphores(src);
        if (rc != VK_SUCCESS) break;
        if (src->commandBufferInfoCount > 0 && !src->pCommandBufferInfos) {
            rc = VK_ERROR_INITIALIZATION_FAILED;
            break;
        }
        if (src->signalSemaphoreInfoCount > 0 && !src->pSignalSemaphoreInfos) {
            rc = VK_ERROR_INITIALIZATION_FAILED;
            break;
        }
        if (src->commandBufferInfoCount > 0) {
            VkCommandBuffer *cmds = calloc(src->commandBufferInfoCount, sizeof(*cmds));
            if (!cmds) {
                rc = VK_ERROR_OUT_OF_HOST_MEMORY;
                break;
            }
            for (uint32_t j = 0; j < src->commandBufferInfoCount; ++j) {
                cmds[j] = src->pCommandBufferInfos[j].commandBuffer;
            }
            dst->pCommandBuffers = cmds;
        }
    }
    if (rc == VK_SUCCESS) {
        rc = vkQueueSubmit(queue, submitCount, legacy_submits, fence);
        if (rc == VK_SUCCESS) {
            for (uint32_t i = 0; i < submitCount; ++i) {
                complete_submit2_semaphores(&pSubmits[i]);
            }
        }
    }
    free_submit_info_arrays(legacy_submits, submitCount);
    return rc;
}

VKAPI_ATTR VkResult VKAPI_CALL vkQueueWaitIdle(VkQueue queue) {
    (void)queue;
    return VK_SUCCESS;
}

VKAPI_ATTR VkResult VKAPI_CALL vkDeviceWaitIdle(VkDevice device) {
    (void)device;
    return VK_SUCCESS;
}

VKAPI_ATTR VkResult VKAPI_CALL vkCreateEvent(
        VkDevice device,
        const VkEventCreateInfo *pCreateInfo,
        const VkAllocationCallbacks *pAllocator,
        VkEvent *pEvent) {
    (void)device;
    (void)pCreateInfo;
    (void)pAllocator;
    if (!pEvent) return VK_ERROR_INITIALIZATION_FAILED;
    PdockerVkEvent *event = pdocker_alloc_handle(sizeof(*event));
    if (!event) return VK_ERROR_OUT_OF_HOST_MEMORY;
    event->signaled = false;
    *pEvent = (VkEvent)event;
    return VK_SUCCESS;
}

VKAPI_ATTR void VKAPI_CALL vkDestroyEvent(
        VkDevice device,
        VkEvent event,
        const VkAllocationCallbacks *pAllocator) {
    (void)device;
    (void)pAllocator;
    free((void *)event);
}

VKAPI_ATTR VkResult VKAPI_CALL vkGetEventStatus(
        VkDevice device,
        VkEvent event) {
    (void)device;
    PdockerVkEvent *e = (PdockerVkEvent *)event;
    if (!e) return VK_EVENT_RESET;
    return e->signaled ? VK_EVENT_SET : VK_EVENT_RESET;
}

VKAPI_ATTR VkResult VKAPI_CALL vkSetEvent(
        VkDevice device,
        VkEvent event) {
    (void)device;
    PdockerVkEvent *e = (PdockerVkEvent *)event;
    if (!e) return VK_ERROR_INITIALIZATION_FAILED;
    e->signaled = true;
    return VK_SUCCESS;
}

VKAPI_ATTR VkResult VKAPI_CALL vkResetEvent(
        VkDevice device,
        VkEvent event) {
    (void)device;
    PdockerVkEvent *e = (PdockerVkEvent *)event;
    if (!e) return VK_ERROR_INITIALIZATION_FAILED;
    e->signaled = false;
    return VK_SUCCESS;
}

static void record_event_command(VkCommandBuffer commandBuffer,
                                 VkEvent event,
                                 bool signaled) {
    PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)commandBuffer;
    PdockerVkEvent *e = (PdockerVkEvent *)event;
    if (!cmd || !e) return;
    PdockerVkCommandOp op;
    memset(&op, 0, sizeof(op));
    op.type = PDOCKER_VK_COMMAND_EVENT;
    op.event = e;
    op.event_signaled = signaled;
    (void)append_command_op(cmd, &op);
}

static void record_event_wait_command(VkCommandBuffer commandBuffer, VkEvent event) {
    PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)commandBuffer;
    PdockerVkEvent *e = (PdockerVkEvent *)event;
    if (!cmd || !e) {
        if (cmd) cmd->graphics_unsupported = true;
        return;
    }
    PdockerVkCommandOp op;
    memset(&op, 0, sizeof(op));
    op.type = PDOCKER_VK_COMMAND_EVENT_WAIT;
    op.event = e;
    (void)append_command_op(cmd, &op);
}

VKAPI_ATTR void VKAPI_CALL vkCmdSetEvent(
        VkCommandBuffer commandBuffer,
        VkEvent event,
        VkPipelineStageFlags stageMask) {
    (void)stageMask;
    record_event_command(commandBuffer, event, true);
}

VKAPI_ATTR void VKAPI_CALL vkCmdResetEvent(
        VkCommandBuffer commandBuffer,
        VkEvent event,
        VkPipelineStageFlags stageMask) {
    (void)stageMask;
    record_event_command(commandBuffer, event, false);
}

VKAPI_ATTR void VKAPI_CALL vkCmdWaitEvents(
        VkCommandBuffer commandBuffer,
        uint32_t eventCount,
        const VkEvent *pEvents,
        VkPipelineStageFlags srcStageMask,
        VkPipelineStageFlags dstStageMask,
        uint32_t memoryBarrierCount,
        const VkMemoryBarrier *pMemoryBarriers,
        uint32_t bufferMemoryBarrierCount,
        const VkBufferMemoryBarrier *pBufferMemoryBarriers,
        uint32_t imageMemoryBarrierCount,
        const VkImageMemoryBarrier *pImageMemoryBarriers) {
    PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)commandBuffer;
    if (eventCount > 0 && !pEvents) {
        if (cmd) cmd->graphics_unsupported = true;
        return;
    }
    for (uint32_t i = 0; i < eventCount; ++i) {
        if (!pEvents[i]) {
            if (cmd) cmd->graphics_unsupported = true;
            return;
        }
        record_event_wait_command(commandBuffer, pEvents[i]);
    }
    vkCmdPipelineBarrier(commandBuffer,
                         srcStageMask,
                         dstStageMask,
                         0,
                         memoryBarrierCount,
                         pMemoryBarriers,
                         bufferMemoryBarrierCount,
                         pBufferMemoryBarriers,
                         imageMemoryBarrierCount,
                         pImageMemoryBarriers);
}

static bool dependency_info_has_unsupported_pnext(const VkDependencyInfo *info) {
    if (!info) return false;
    if (info->pNext) return true;
    if (info->memoryBarrierCount && !info->pMemoryBarriers) return true;
    if (info->pMemoryBarriers) {
        for (uint32_t i = 0; i < info->memoryBarrierCount; ++i) {
            if (info->pMemoryBarriers[i].pNext) return true;
        }
    }
    if (info->bufferMemoryBarrierCount && !info->pBufferMemoryBarriers) return true;
    if (info->pBufferMemoryBarriers) {
        for (uint32_t i = 0; i < info->bufferMemoryBarrierCount; ++i) {
            if (info->pBufferMemoryBarriers[i].pNext) return true;
        }
    }
    if (info->imageMemoryBarrierCount && !info->pImageMemoryBarriers) return true;
    if (info->pImageMemoryBarriers) {
        for (uint32_t i = 0; i < info->imageMemoryBarrierCount; ++i) {
            if (info->pImageMemoryBarriers[i].pNext) return true;
        }
    }
    return false;
}

VKAPI_ATTR void VKAPI_CALL vkCmdPipelineBarrier2(
        VkCommandBuffer commandBuffer,
        const VkDependencyInfo *pDependencyInfo) {
    PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)commandBuffer;
    if (!cmd) return;
    if (dependency_info_has_unsupported_pnext(pDependencyInfo)) {
        cmd->graphics_unsupported = true;
        return;
    }
    VkDependencyFlags dependency_flags = pDependencyInfo ? pDependencyInfo->dependencyFlags : 0;
    if ((dependency_flags & ~VK_DEPENDENCY_BY_REGION_BIT) != 0) {
        cmd->graphics_unsupported = true;
    }
    uint32_t memory_barrier_first = cmd->memory_barrier_op_count;
    uint32_t buffer_barrier_first = cmd->buffer_barrier_op_count;
    uint32_t image_barrier_first = cmd->image_barrier_op_count;
    if (pDependencyInfo && pDependencyInfo->pMemoryBarriers) {
        for (uint32_t i = 0; i < pDependencyInfo->memoryBarrierCount; ++i) {
            const VkMemoryBarrier2 *b = &pDependencyInfo->pMemoryBarriers[i];
            record_memory_barrier_op(commandBuffer,
                                     b->srcAccessMask,
                                     b->dstAccessMask,
                                     b->srcStageMask,
                                     b->dstStageMask);
        }
    }
    if (pDependencyInfo && pDependencyInfo->pBufferMemoryBarriers) {
        for (uint32_t i = 0; i < pDependencyInfo->bufferMemoryBarrierCount; ++i) {
            const VkBufferMemoryBarrier2 *b = &pDependencyInfo->pBufferMemoryBarriers[i];
            record_buffer_barrier_op(commandBuffer,
                                     (PdockerVkBuffer *)b->buffer,
                                     b->offset,
                                     b->size,
                                     b->srcAccessMask,
                                     b->dstAccessMask,
                                     b->srcStageMask,
                                     b->dstStageMask,
                                     b->srcQueueFamilyIndex,
                                     b->dstQueueFamilyIndex);
        }
    }
    if (pDependencyInfo && pDependencyInfo->pImageMemoryBarriers) {
        for (uint32_t i = 0; i < pDependencyInfo->imageMemoryBarrierCount; ++i) {
            const VkImageMemoryBarrier2 *b = &pDependencyInfo->pImageMemoryBarriers[i];
            record_image_barrier_op(commandBuffer,
                                    (PdockerVkImage *)b->image,
                                    b->oldLayout,
                                    b->newLayout,
                                    b->subresourceRange,
                                    b->srcAccessMask,
                                    b->dstAccessMask,
                                    b->srcStageMask,
                                    b->dstStageMask,
                                    b->srcQueueFamilyIndex,
                                    b->dstQueueFamilyIndex);
        }
    }
    uint32_t memory_barrier_count = cmd->memory_barrier_op_count - memory_barrier_first;
    uint32_t buffer_barrier_count = cmd->buffer_barrier_op_count - buffer_barrier_first;
    uint32_t image_barrier_count = cmd->image_barrier_op_count - image_barrier_first;
    if (memory_barrier_count || buffer_barrier_count || image_barrier_count) {
        PdockerVkGraphicsCommandRecord record;
        memset(&record, 0, sizeof(record));
        record.command_type = PDOCKER_GPU_GRAPHICS_V6_COMMAND_BARRIER;
        record.flags = dependency_flags & VK_DEPENDENCY_BY_REGION_BIT;
        record.memory_barrier_op_first = memory_barrier_first;
        record.memory_barrier_op_count = memory_barrier_count;
        record.buffer_barrier_op_first = buffer_barrier_first;
        record.buffer_barrier_op_count = buffer_barrier_count;
        record.image_barrier_op_first = image_barrier_first;
        record.image_barrier_op_count = image_barrier_count;
        (void)append_graphics_command_record(cmd, &record);
    }
    PdockerVkCommandOp op;
    memset(&op, 0, sizeof(op));
    op.type = PDOCKER_VK_COMMAND_BARRIER;
    (void)append_command_op(cmd, &op);
}

static bool dependency_info_has_supported_barrier_payload(const VkDependencyInfo *info) {
    return info && (info->dependencyFlags != 0 ||
                    info->memoryBarrierCount != 0 ||
                    info->bufferMemoryBarrierCount != 0 ||
                    info->imageMemoryBarrierCount != 0);
}

VKAPI_ATTR void VKAPI_CALL vkCmdSetEvent2(
        VkCommandBuffer commandBuffer,
        VkEvent event,
        const VkDependencyInfo *pDependencyInfo) {
    PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)commandBuffer;
    if (dependency_info_has_unsupported_pnext(pDependencyInfo)) {
        if (cmd) cmd->graphics_unsupported = true;
        return;
    }
    if (dependency_info_has_supported_barrier_payload(pDependencyInfo)) {
        vkCmdPipelineBarrier2(commandBuffer, pDependencyInfo);
    }
    record_event_command(commandBuffer, event, true);
}

VKAPI_ATTR void VKAPI_CALL vkCmdResetEvent2(
        VkCommandBuffer commandBuffer,
        VkEvent event,
        VkPipelineStageFlags2 stageMask) {
    (void)stageMask;
    record_event_command(commandBuffer, event, false);
}

VKAPI_ATTR void VKAPI_CALL vkCmdWaitEvents2(
        VkCommandBuffer commandBuffer,
        uint32_t eventCount,
        const VkEvent *pEvents,
        const VkDependencyInfo *pDependencyInfos) {
    PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)commandBuffer;
    if (eventCount > 0 && (!pEvents || !pDependencyInfos)) {
        if (cmd) cmd->graphics_unsupported = true;
        return;
    }
    for (uint32_t i = 0; i < eventCount; ++i) {
        if (!pEvents[i]) {
            if (cmd) cmd->graphics_unsupported = true;
            return;
        }
        record_event_wait_command(commandBuffer, pEvents[i]);
        vkCmdPipelineBarrier2(commandBuffer, &pDependencyInfos[i]);
    }
}

static bool query_range_valid(
        const PdockerVkQueryPool *pool,
        uint32_t firstQuery,
        uint32_t queryCount) {
    return pool &&
           firstQuery <= pool->query_count &&
           queryCount <= pool->query_count - firstQuery;
}

static void reset_query_range(
        PdockerVkQueryPool *pool,
        uint32_t firstQuery,
        uint32_t queryCount) {
    if (!query_range_valid(pool, firstQuery, queryCount)) return;
    for (uint32_t i = 0; i < queryCount; ++i) {
        uint32_t q = firstQuery + i;
        pool->values[q] = 0;
        pool->available[q] = 0;
        pool->active[q] = 0;
        if (pool->result_entries) {
            pool->result_entries[q].value = 0;
            pool->result_entries[q].available = 0;
            pool->result_entries[q].status = 0;
        }
    }
}

static void execute_recorded_query_op(PdockerVkCommandOp *op) {
    if (!op || !op->query_pool ||
        !query_range_valid(op->query_pool, op->query_index, op->query_count)) {
        return;
    }
    PdockerVkQueryPool *pool = op->query_pool;
    switch (op->type) {
        case PDOCKER_VK_COMMAND_QUERY_BEGIN:
            pool->active[op->query_index] = 1;
            pool->available[op->query_index] = 0;
            break;
        case PDOCKER_VK_COMMAND_QUERY_END:
            pool->active[op->query_index] = 0;
            pool->values[op->query_index] = monotonic_ns();
            pool->available[op->query_index] = 1;
            if (pool->result_entries) {
                pool->result_entries[op->query_index].value = pool->values[op->query_index];
                pool->result_entries[op->query_index].available = 1;
                pool->result_entries[op->query_index].status = VK_SUCCESS;
            }
            break;
        case PDOCKER_VK_COMMAND_QUERY_RESET:
            reset_query_range(pool, op->query_index, op->query_count);
            break;
        case PDOCKER_VK_COMMAND_QUERY_TIMESTAMP:
            (void)op->query_stage_mask;
            pool->values[op->query_index] = monotonic_ns();
            pool->available[op->query_index] = 1;
            pool->active[op->query_index] = 0;
            if (pool->result_entries) {
                pool->result_entries[op->query_index].value = pool->values[op->query_index];
                pool->result_entries[op->query_index].available = 1;
                pool->result_entries[op->query_index].status = VK_SUCCESS;
            }
            break;
        default:
            break;
    }
}

static void record_query_command(
        VkCommandBuffer commandBuffer,
        PdockerVkCommandOpType type,
        VkQueryPool queryPool,
        uint32_t firstQuery,
        uint32_t queryCount,
        VkPipelineStageFlags2 stageMask) {
    PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)commandBuffer;
    PdockerVkQueryPool *pool = (PdockerVkQueryPool *)queryPool;
    if (!cmd || !query_range_valid(pool, firstQuery, queryCount)) return;
    PdockerVkCommandOp op;
    memset(&op, 0, sizeof(op));
    op.type = type;
    op.query_pool = pool;
    op.query_index = firstQuery;
    op.query_count = queryCount;
    op.query_stage_mask = stageMask;
    if (type == PDOCKER_VK_COMMAND_QUERY_BEGIN || type == PDOCKER_VK_COMMAND_QUERY_END ||
        type == PDOCKER_VK_COMMAND_QUERY_RESET || type == PDOCKER_VK_COMMAND_QUERY_TIMESTAMP) {
        PdockerVkGraphicsCommandRecord record;
        memset(&record, 0, sizeof(record));
        switch (type) {
            case PDOCKER_VK_COMMAND_QUERY_BEGIN:
                record.command_type = PDOCKER_GPU_GRAPHICS_V6_COMMAND_BEGIN_QUERY;
                break;
            case PDOCKER_VK_COMMAND_QUERY_END:
                record.command_type = PDOCKER_GPU_GRAPHICS_V6_COMMAND_END_QUERY;
                break;
            case PDOCKER_VK_COMMAND_QUERY_RESET:
                record.command_type = PDOCKER_GPU_GRAPHICS_V6_COMMAND_RESET_QUERY_POOL;
                break;
            case PDOCKER_VK_COMMAND_QUERY_TIMESTAMP:
                record.command_type = PDOCKER_GPU_GRAPHICS_V6_COMMAND_WRITE_TIMESTAMP;
                break;
            default:
                return;
        }
        if (!append_graphics_command_record(cmd, &record)) return;
    }
    (void)append_command_op(cmd, &op);
}

static void record_copy_query_results_command(
        VkCommandBuffer commandBuffer,
        VkQueryPool queryPool,
        uint32_t firstQuery,
        uint32_t queryCount,
        VkBuffer dstBuffer,
        VkDeviceSize dstOffset,
        VkDeviceSize stride,
        VkQueryResultFlags flags) {
    PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)commandBuffer;
    PdockerVkQueryPool *pool = (PdockerVkQueryPool *)queryPool;
    PdockerVkBuffer *dst = (PdockerVkBuffer *)dstBuffer;
    if (!cmd || !dst || !query_range_valid(pool, firstQuery, queryCount) || queryCount == 0 || stride == 0) return;
    PdockerVkCommandOp op;
    memset(&op, 0, sizeof(op));
    op.type = PDOCKER_VK_COMMAND_COPY_QUERY_RESULTS;
    op.query_pool = pool;
    op.query_dst_buffer = dst;
    op.query_index = firstQuery;
    op.query_count = queryCount;
    op.query_dst_offset = dstOffset;
    op.query_stride = stride;
    op.query_result_flags = flags;
    PdockerVkGraphicsCommandRecord record;
    memset(&record, 0, sizeof(record));
    record.command_type = PDOCKER_GPU_GRAPHICS_V6_COMMAND_COPY_QUERY_POOL_RESULTS;
    if (!append_graphics_command_record(cmd, &record)) return;
    (void)append_command_op(cmd, &op);
}

VKAPI_ATTR VkResult VKAPI_CALL vkCreateQueryPool(
        VkDevice device,
        const VkQueryPoolCreateInfo *pCreateInfo,
        const VkAllocationCallbacks *pAllocator,
        VkQueryPool *pQueryPool) {
    (void)device;
    (void)pAllocator;
    if (!pCreateInfo || !pQueryPool || pCreateInfo->queryCount == 0 ||
        pCreateInfo->queryCount > PDOCKER_VK_MAX_QUERY_COUNT) {
        return VK_ERROR_INITIALIZATION_FAILED;
    }
    if (pCreateInfo->queryType != VK_QUERY_TYPE_TIMESTAMP &&
        pCreateInfo->queryType != VK_QUERY_TYPE_OCCLUSION) {
        trace_icd_runtime_failure("query-type-unsupported",
                                  VK_ERROR_FEATURE_NOT_PRESENT);
        return VK_ERROR_FEATURE_NOT_PRESENT;
    }
    PdockerVkQueryPool *pool = pdocker_alloc_handle(sizeof(*pool));
    if (!pool) return VK_ERROR_OUT_OF_HOST_MEMORY;
    pool->type = pCreateInfo->queryType;
    pool->query_count = pCreateInfo->queryCount;
    pool->pool_id = next_vulkan_query_pool_id();
    pool->result_fd = -1;
    pool->result_size = (size_t)pool->query_count * sizeof(PdockerGpuVulkanGraphicsV617QueryResultEntry);
    pool->values = calloc(pool->query_count, sizeof(pool->values[0]));
    pool->available = calloc(pool->query_count, sizeof(pool->available[0]));
    pool->active = calloc(pool->query_count, sizeof(pool->active[0]));
    if (pool->result_size && pool->result_size / sizeof(PdockerGpuVulkanGraphicsV617QueryResultEntry) == pool->query_count) {
        pool->result_fd = create_shared_fd(pool->result_size);
        if (pool->result_fd >= 0) {
            void *mapped = mmap(NULL, pool->result_size, PROT_READ | PROT_WRITE, MAP_SHARED, pool->result_fd, 0);
            if (mapped != MAP_FAILED) {
                pool->result_entries = (PdockerGpuVulkanGraphicsV617QueryResultEntry *)mapped;
                memset(pool->result_entries, 0, pool->result_size);
            } else {
                close(pool->result_fd);
                pool->result_fd = -1;
            }
        }
    }
    if (!pool->values || !pool->available || !pool->active || !pool->result_entries) {
        free(pool->values);
        free(pool->available);
        free(pool->active);
        if (pool->result_entries) munmap(pool->result_entries, pool->result_size);
        if (pool->result_fd >= 0) close(pool->result_fd);
        free(pool);
        return VK_ERROR_OUT_OF_HOST_MEMORY;
    }
    *pQueryPool = (VkQueryPool)pool;
    return VK_SUCCESS;
}

VKAPI_ATTR void VKAPI_CALL vkDestroyQueryPool(
        VkDevice device,
        VkQueryPool queryPool,
        const VkAllocationCallbacks *pAllocator) {
    (void)device;
    (void)pAllocator;
    PdockerVkQueryPool *pool = (PdockerVkQueryPool *)queryPool;
    if (!pool) return;
    free(pool->values);
    free(pool->available);
    free(pool->active);
    if (pool->result_entries) munmap(pool->result_entries, pool->result_size);
    if (pool->result_fd >= 0) close(pool->result_fd);
    free(pool);
}

VKAPI_ATTR void VKAPI_CALL vkCmdBeginQuery(
        VkCommandBuffer commandBuffer,
        VkQueryPool queryPool,
        uint32_t query,
        VkQueryControlFlags flags) {
    record_query_command(commandBuffer,
                         PDOCKER_VK_COMMAND_QUERY_BEGIN,
                         queryPool,
                         query,
                         1,
                         (VkPipelineStageFlags2)flags);
}

VKAPI_ATTR void VKAPI_CALL vkCmdEndQuery(
        VkCommandBuffer commandBuffer,
        VkQueryPool queryPool,
        uint32_t query) {
    record_query_command(commandBuffer,
                         PDOCKER_VK_COMMAND_QUERY_END,
                         queryPool,
                         query,
                         1,
                         0);
}

VKAPI_ATTR void VKAPI_CALL vkCmdResetQueryPool(
        VkCommandBuffer commandBuffer,
        VkQueryPool queryPool,
        uint32_t firstQuery,
        uint32_t queryCount) {
    record_query_command(commandBuffer,
                         PDOCKER_VK_COMMAND_QUERY_RESET,
                         queryPool,
                         firstQuery,
                         queryCount,
                         0);
}

VKAPI_ATTR void VKAPI_CALL vkResetQueryPool(
        VkDevice device,
        VkQueryPool queryPool,
        uint32_t firstQuery,
        uint32_t queryCount) {
    (void)device;
    reset_query_range((PdockerVkQueryPool *)queryPool, firstQuery, queryCount);
}

VKAPI_ATTR void VKAPI_CALL vkCmdWriteTimestamp(
        VkCommandBuffer commandBuffer,
        VkPipelineStageFlagBits pipelineStage,
        VkQueryPool queryPool,
        uint32_t query) {
    record_query_command(commandBuffer,
                         PDOCKER_VK_COMMAND_QUERY_TIMESTAMP,
                         queryPool,
                         query,
                         1,
                         (VkPipelineStageFlags2)pipelineStage);
}

VKAPI_ATTR void VKAPI_CALL vkCmdWriteTimestamp2(
        VkCommandBuffer commandBuffer,
        VkPipelineStageFlags2 stage,
        VkQueryPool queryPool,
        uint32_t query) {
    record_query_command(commandBuffer,
                         PDOCKER_VK_COMMAND_QUERY_TIMESTAMP,
                         queryPool,
                         query,
                         1,
                         stage);
}

VKAPI_ATTR void VKAPI_CALL vkCmdCopyQueryPoolResults(
        VkCommandBuffer commandBuffer,
        VkQueryPool queryPool,
        uint32_t firstQuery,
        uint32_t queryCount,
        VkBuffer dstBuffer,
        VkDeviceSize dstOffset,
        VkDeviceSize stride,
        VkQueryResultFlags flags) {
    record_copy_query_results_command(commandBuffer, queryPool, firstQuery, queryCount,
                                      dstBuffer, dstOffset, stride, flags);
}

static void write_query_result_scalar(uint8_t *dst, bool result64, uint64_t value) {
    if (result64) {
        uint64_t v = value;
        memcpy(dst, &v, sizeof(v));
    } else {
        uint32_t v = value > UINT32_MAX ? UINT32_MAX : (uint32_t)value;
        memcpy(dst, &v, sizeof(v));
    }
}

VKAPI_ATTR VkResult VKAPI_CALL vkGetQueryPoolResults(
        VkDevice device,
        VkQueryPool queryPool,
        uint32_t firstQuery,
        uint32_t queryCount,
        size_t dataSize,
        void *pData,
        VkDeviceSize stride,
        VkQueryResultFlags flags) {
    (void)device;
    PdockerVkQueryPool *pool = (PdockerVkQueryPool *)queryPool;
    if (!pData || !query_range_valid(pool, firstQuery, queryCount)) {
        return VK_ERROR_INITIALIZATION_FAILED;
    }
    bool result64 = (flags & VK_QUERY_RESULT_64_BIT) != 0;
    bool with_availability = (flags & VK_QUERY_RESULT_WITH_AVAILABILITY_BIT) != 0;
    bool wait = (flags & VK_QUERY_RESULT_WAIT_BIT) != 0;
    bool partial = (flags & VK_QUERY_RESULT_PARTIAL_BIT) != 0;
    size_t scalar_size = result64 ? sizeof(uint64_t) : sizeof(uint32_t);
    size_t item_size = scalar_size + (with_availability ? scalar_size : 0);
    if (queryCount > 1 && stride < item_size) return VK_INCOMPLETE;
    uint8_t *bytes = (uint8_t *)pData;
    VkResult rc = VK_SUCCESS;
    for (uint32_t i = 0; i < queryCount; ++i) {
        size_t offset = (size_t)i * (size_t)stride;
        if ((VkDeviceSize)offset != (VkDeviceSize)i * stride ||
            offset > dataSize || item_size > dataSize - offset) {
            return VK_INCOMPLETE;
        }
        uint32_t q = firstQuery + i;
        if (pool->result_entries && pool->result_entries[q].available) {
            pool->values[q] = pool->result_entries[q].value;
            pool->available[q] = 1;
            pool->active[q] = 0;
        }
        if (!pool->available[q] && wait) {
            pool->values[q] = monotonic_ns();
            pool->available[q] = 1;
            pool->active[q] = 0;
            if (pool->result_entries) {
                pool->result_entries[q].value = pool->values[q];
                pool->result_entries[q].available = 1;
                pool->result_entries[q].status = VK_SUCCESS;
            }
        }
        if (!pool->available[q] && !partial) {
            rc = VK_NOT_READY;
            continue;
        }
        write_query_result_scalar(bytes + offset, result64, pool->values[q]);
        if (with_availability) {
            write_query_result_scalar(bytes + offset + scalar_size,
                                      result64,
                                      pool->available[q] ? 1 : 0);
        }
    }
    return rc;
}

VKAPI_ATTR VkResult VKAPI_CALL vkCreateFence(
        VkDevice device,
        const VkFenceCreateInfo *pCreateInfo,
        const VkAllocationCallbacks *pAllocator,
        VkFence *pFence) {
    (void)device;
    (void)pAllocator;
    if (!pFence) return VK_ERROR_INITIALIZATION_FAILED;
    PdockerVkFence *fence = pdocker_alloc_handle(sizeof(*fence));
    if (!fence) return VK_ERROR_OUT_OF_HOST_MEMORY;
    fence->signaled = pCreateInfo && (pCreateInfo->flags & VK_FENCE_CREATE_SIGNALED_BIT);
    *pFence = (VkFence)fence;
    return VK_SUCCESS;
}

VKAPI_ATTR void VKAPI_CALL vkDestroyFence(
        VkDevice device,
        VkFence fence,
        const VkAllocationCallbacks *pAllocator) {
    (void)device;
    (void)pAllocator;
    free((void *)fence);
}

VKAPI_ATTR VkResult VKAPI_CALL vkResetFences(
        VkDevice device,
        uint32_t fenceCount,
        const VkFence *pFences) {
    (void)device;
    for (uint32_t i = 0; i < fenceCount; ++i) {
        PdockerVkFence *fence = (PdockerVkFence *)pFences[i];
        if (fence) fence->signaled = false;
    }
    return VK_SUCCESS;
}

VKAPI_ATTR VkResult VKAPI_CALL vkGetFenceStatus(VkDevice device, VkFence fence) {
    (void)device;
    PdockerVkFence *f = (PdockerVkFence *)fence;
    return (!f || f->signaled) ? VK_SUCCESS : VK_NOT_READY;
}

VKAPI_ATTR VkResult VKAPI_CALL vkWaitForFences(
        VkDevice device,
        uint32_t fenceCount,
        const VkFence *pFences,
        VkBool32 waitAll,
        uint64_t timeout) {
    (void)device;
    (void)timeout;
    bool any = false;
    for (uint32_t i = 0; i < fenceCount; ++i) {
        PdockerVkFence *fence = (PdockerVkFence *)pFences[i];
        bool signaled = !fence || fence->signaled;
        any = any || signaled;
        if (waitAll && !signaled) return VK_NOT_READY;
    }
    return (!waitAll && fenceCount > 0 && !any) ? VK_NOT_READY : VK_SUCCESS;
}

static bool semaphore_create_info_parse_pnext(const void *pNext, bool *timeline, uint64_t *initial_value) {
    if (timeline) *timeline = false;
    if (initial_value) *initial_value = 0;
    for (const void *node = pNext; node;) {
        PdockerVkStructHeader header = read_vk_struct_header(node);
        switch (header.sType) {
            case VK_STRUCTURE_TYPE_SEMAPHORE_TYPE_CREATE_INFO: {
                const VkSemaphoreTypeCreateInfo *info = (const VkSemaphoreTypeCreateInfo *)node;
                if (info->semaphoreType == VK_SEMAPHORE_TYPE_TIMELINE) {
                    if (timeline) *timeline = true;
                    if (initial_value) *initial_value = info->initialValue;
                } else if (info->semaphoreType != VK_SEMAPHORE_TYPE_BINARY) {
                    return false;
                }
                break;
            }
            default:
                return false;
        }
        node = header.pNext;
    }
    return true;
}

VKAPI_ATTR VkResult VKAPI_CALL vkCreateSemaphore(
        VkDevice device,
        const VkSemaphoreCreateInfo *pCreateInfo,
        const VkAllocationCallbacks *pAllocator,
        VkSemaphore *pSemaphore) {
    (void)device;
    (void)pAllocator;
    if (!pSemaphore) return VK_ERROR_INITIALIZATION_FAILED;
    bool timeline = false;
    uint64_t initial_value = 0;
    if (pCreateInfo && !semaphore_create_info_parse_pnext(pCreateInfo->pNext, &timeline, &initial_value)) {
        trace_icd_runtime_failure("semaphore-pnext-unsupported",
                                  VK_ERROR_FEATURE_NOT_PRESENT);
        return VK_ERROR_FEATURE_NOT_PRESENT;
    }
    PdockerVkSemaphore *sem = pdocker_alloc_handle(sizeof(*sem));
    if (!sem) return VK_ERROR_OUT_OF_HOST_MEMORY;
    sem->timeline = timeline;
    sem->value = initial_value;
    sem->signaled = timeline ? (initial_value > 0) : false;
    *pSemaphore = (VkSemaphore)sem;
    return VK_SUCCESS;
}

VKAPI_ATTR void VKAPI_CALL vkDestroySemaphore(
        VkDevice device,
        VkSemaphore semaphore,
        const VkAllocationCallbacks *pAllocator) {
    (void)device;
    (void)pAllocator;
    free((void *)semaphore);
}
VKAPI_ATTR VkResult VKAPI_CALL vkGetSemaphoreCounterValue(
        VkDevice device,
        VkSemaphore semaphore,
        uint64_t *pValue) {
    (void)device;
    PdockerVkSemaphore *sem = (PdockerVkSemaphore *)semaphore;
    if (!sem || !pValue || !sem->timeline) return VK_ERROR_FEATURE_NOT_PRESENT;
    *pValue = sem->value;
    return VK_SUCCESS;
}

VKAPI_ATTR VkResult VKAPI_CALL vkWaitSemaphores(
        VkDevice device,
        const VkSemaphoreWaitInfo *pWaitInfo,
        uint64_t timeout) {
    (void)device;
    if (!pWaitInfo) return VK_ERROR_INITIALIZATION_FAILED;
    bool wait_any = (pWaitInfo->flags & VK_SEMAPHORE_WAIT_ANY_BIT) != 0;
    bool any = false;
    for (uint32_t i = 0; i < pWaitInfo->semaphoreCount; ++i) {
        PdockerVkSemaphore *sem = pWaitInfo->pSemaphores
            ? (PdockerVkSemaphore *)pWaitInfo->pSemaphores[i]
            : NULL;
        uint64_t value = pWaitInfo->pValues ? pWaitInfo->pValues[i] : 0;
        bool ready = sem && sem->timeline && sem->value >= value;
        any = any || ready;
        if (!wait_any && !ready) return timeout == 0 ? VK_TIMEOUT : VK_NOT_READY;
    }
    return (wait_any && pWaitInfo->semaphoreCount > 0 && !any)
        ? (timeout == 0 ? VK_TIMEOUT : VK_NOT_READY)
        : VK_SUCCESS;
}

VKAPI_ATTR VkResult VKAPI_CALL vkSignalSemaphore(
        VkDevice device,
        const VkSemaphoreSignalInfo *pSignalInfo) {
    (void)device;
    if (!pSignalInfo) return VK_ERROR_INITIALIZATION_FAILED;
    PdockerVkSemaphore *sem = (PdockerVkSemaphore *)pSignalInfo->semaphore;
    if (!sem || !sem->timeline) return VK_ERROR_FEATURE_NOT_PRESENT;
    semaphore_complete_signal(sem, pSignalInfo->value);
    return VK_SUCCESS;
}


VKAPI_ATTR VkResult VKAPI_CALL vkCreatePipelineCache(
        VkDevice device,
        const VkPipelineCacheCreateInfo *pCreateInfo,
        const VkAllocationCallbacks *pAllocator,
        VkPipelineCache *pPipelineCache) {
    (void)device;
    (void)pCreateInfo;
    (void)pAllocator;
    if (!pPipelineCache) return VK_ERROR_INITIALIZATION_FAILED;
    *pPipelineCache = (VkPipelineCache)pdocker_alloc_handle(sizeof(PdockerHandle));
    return *pPipelineCache ? VK_SUCCESS : VK_ERROR_OUT_OF_HOST_MEMORY;
}

VKAPI_ATTR void VKAPI_CALL vkDestroyPipelineCache(
        VkDevice device,
        VkPipelineCache pipelineCache,
        const VkAllocationCallbacks *pAllocator) {
    (void)device;
    (void)pAllocator;
    free((void *)pipelineCache);
}

VKAPI_ATTR VkResult VKAPI_CALL vkGetPipelineCacheData(
        VkDevice device,
        VkPipelineCache pipelineCache,
        size_t *pDataSize,
        void *pData) {
    (void)device;
    (void)pipelineCache;
    if (!pDataSize) return VK_ERROR_INITIALIZATION_FAILED;
    if (!pData) {
        *pDataSize = 0;
        return VK_SUCCESS;
    }
    if (*pDataSize > 0) *pDataSize = 0;
    return VK_SUCCESS;
}

VKAPI_ATTR VkResult VKAPI_CALL vkMergePipelineCaches(
        VkDevice device,
        VkPipelineCache dstCache,
        uint32_t srcCacheCount,
        const VkPipelineCache *pSrcCaches) {
    (void)device;
    (void)dstCache;
    (void)srcCacheCount;
    (void)pSrcCaches;
    return VK_SUCCESS;
}

VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL vkGetDeviceProcAddr(VkDevice device, const char *pName);
VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL vkGetInstanceProcAddr(VkInstance instance, const char *pName);

static PFN_vkVoidFunction proc_address(const char *pName) {
    if (!pName) return NULL;
#define MAP_PROC(name) if (strcmp(pName, #name) == 0) return (PFN_vkVoidFunction)name
#define MAP_ALIAS(alias, name) if (strcmp(pName, (alias)) == 0) return (PFN_vkVoidFunction)name
    MAP_PROC(vkGetInstanceProcAddr);
    MAP_PROC(vkGetDeviceProcAddr);
    MAP_PROC(vkEnumerateInstanceVersion);
    MAP_PROC(vkEnumerateInstanceExtensionProperties);
    MAP_PROC(vkEnumerateInstanceLayerProperties);
    MAP_PROC(vkCreateInstance);
    MAP_PROC(vkDestroyInstance);
    MAP_PROC(vkEnumeratePhysicalDevices);
    MAP_PROC(vkGetPhysicalDeviceProperties);
    MAP_PROC(vkGetPhysicalDeviceProperties2);
    MAP_ALIAS("vkGetPhysicalDeviceProperties2KHR", vkGetPhysicalDeviceProperties2);
    MAP_PROC(vkGetPhysicalDeviceFeatures);
    MAP_PROC(vkGetPhysicalDeviceFeatures2);
    MAP_ALIAS("vkGetPhysicalDeviceFeatures2KHR", vkGetPhysicalDeviceFeatures2);
    MAP_PROC(vkGetPhysicalDeviceFormatProperties);
    MAP_PROC(vkGetPhysicalDeviceImageFormatProperties);
    MAP_PROC(vkGetPhysicalDeviceSparseImageFormatProperties);
    MAP_PROC(vkGetPhysicalDeviceQueueFamilyProperties);
    MAP_PROC(vkGetPhysicalDeviceQueueFamilyProperties2);
    MAP_ALIAS("vkGetPhysicalDeviceQueueFamilyProperties2KHR", vkGetPhysicalDeviceQueueFamilyProperties2);
    MAP_PROC(vkGetPhysicalDeviceMemoryProperties);
    MAP_PROC(vkGetPhysicalDeviceMemoryProperties2);
    MAP_ALIAS("vkGetPhysicalDeviceMemoryProperties2KHR", vkGetPhysicalDeviceMemoryProperties2);
    MAP_PROC(vkEnumerateDeviceExtensionProperties);
    MAP_PROC(vkEnumerateDeviceLayerProperties);
    MAP_PROC(vkCreateDevice);
    MAP_PROC(vkDestroyDevice);
    MAP_PROC(vkGetDeviceQueue);
    MAP_PROC(vkGetDeviceQueue2);
    MAP_PROC(vkCreateBuffer);
    MAP_PROC(vkDestroyBuffer);
    MAP_PROC(vkCreateImage);
    MAP_PROC(vkDestroyImage);
    MAP_PROC(vkCreateImageView);
    MAP_PROC(vkDestroyImageView);
    MAP_PROC(vkCreateSampler);
    MAP_PROC(vkDestroySampler);
    MAP_PROC(vkGetBufferMemoryRequirements);
    MAP_PROC(vkGetBufferMemoryRequirements2);
    MAP_ALIAS("vkGetBufferMemoryRequirements2KHR", vkGetBufferMemoryRequirements2);
    MAP_PROC(vkGetImageMemoryRequirements);
    MAP_PROC(vkGetImageMemoryRequirements2);
    MAP_ALIAS("vkGetImageMemoryRequirements2KHR", vkGetImageMemoryRequirements2);
    MAP_PROC(vkGetImageSubresourceLayout);
    MAP_PROC(vkAllocateMemory);
    MAP_PROC(vkFreeMemory);
    MAP_PROC(vkMapMemory);
    MAP_PROC(vkUnmapMemory);
    MAP_PROC(vkGetDeviceMemoryCommitment);
    MAP_PROC(vkFlushMappedMemoryRanges);
    MAP_PROC(vkInvalidateMappedMemoryRanges);
    MAP_PROC(vkBindBufferMemory);
    MAP_PROC(vkBindBufferMemory2);
    MAP_ALIAS("vkBindBufferMemory2KHR", vkBindBufferMemory2);
    MAP_PROC(vkBindImageMemory);
    MAP_PROC(vkBindImageMemory2);
    MAP_ALIAS("vkBindImageMemory2KHR", vkBindImageMemory2);
    MAP_PROC(vkCreateDescriptorSetLayout);
    MAP_PROC(vkDestroyDescriptorSetLayout);
    MAP_PROC(vkCreatePipelineLayout);
    MAP_PROC(vkDestroyPipelineLayout);
    MAP_PROC(vkCreateDescriptorPool);
    MAP_PROC(vkDestroyDescriptorPool);
    MAP_PROC(vkResetDescriptorPool);
    MAP_PROC(vkAllocateDescriptorSets);
    MAP_PROC(vkFreeDescriptorSets);
    MAP_PROC(vkUpdateDescriptorSets);
    MAP_PROC(vkCreateShaderModule);
    MAP_PROC(vkDestroyShaderModule);
    MAP_PROC(vkCreatePipelineCache);
    MAP_PROC(vkDestroyPipelineCache);
    MAP_PROC(vkGetPipelineCacheData);
    MAP_PROC(vkMergePipelineCaches);
    MAP_PROC(vkCreateComputePipelines);
    MAP_PROC(vkCreateGraphicsPipelines);
    MAP_PROC(vkDestroyPipeline);
    MAP_PROC(vkCreateRenderPass);
    MAP_PROC(vkCreateRenderPass2);
    MAP_ALIAS("vkCreateRenderPass2KHR", vkCreateRenderPass2);
    MAP_PROC(vkDestroyRenderPass);
    MAP_PROC(vkCreateFramebuffer);
    MAP_PROC(vkDestroyFramebuffer);
    MAP_PROC(vkGetRenderAreaGranularity);
    MAP_PROC(vkDestroySurfaceKHR);
    MAP_PROC(vkGetPhysicalDeviceSurfaceSupportKHR);
    MAP_PROC(vkGetPhysicalDeviceSurfaceCapabilitiesKHR);
    MAP_PROC(vkGetPhysicalDeviceSurfaceFormatsKHR);
    MAP_PROC(vkGetPhysicalDeviceSurfacePresentModesKHR);
    MAP_PROC(vkCreateSwapchainKHR);
    MAP_PROC(vkDestroySwapchainKHR);
    MAP_PROC(vkGetSwapchainImagesKHR);
    MAP_PROC(vkAcquireNextImageKHR);
    MAP_PROC(vkAcquireNextImage2KHR);
    MAP_PROC(vkQueuePresentKHR);
    MAP_PROC(vkCreateCommandPool);
    MAP_PROC(vkDestroyCommandPool);
    MAP_PROC(vkResetCommandPool);
    MAP_PROC(vkAllocateCommandBuffers);
    MAP_PROC(vkFreeCommandBuffers);
    MAP_PROC(vkBeginCommandBuffer);
    MAP_PROC(vkEndCommandBuffer);
    MAP_PROC(vkResetCommandBuffer);
    MAP_PROC(vkCmdBindPipeline);
    MAP_PROC(vkCmdBeginRendering);
    MAP_ALIAS("vkCmdBeginRenderingKHR", vkCmdBeginRendering);
    MAP_PROC(vkCmdEndRendering);
    MAP_ALIAS("vkCmdEndRenderingKHR", vkCmdEndRendering);
    MAP_PROC(vkCmdBeginRenderPass);
    MAP_PROC(vkCmdNextSubpass);
    MAP_PROC(vkCmdEndRenderPass);
    MAP_PROC(vkCmdBeginRenderPass2);
    MAP_ALIAS("vkCmdBeginRenderPass2KHR", vkCmdBeginRenderPass2);
    MAP_PROC(vkCmdNextSubpass2);
    MAP_ALIAS("vkCmdNextSubpass2KHR", vkCmdNextSubpass2);
    MAP_PROC(vkCmdEndRenderPass2);
    MAP_ALIAS("vkCmdEndRenderPass2KHR", vkCmdEndRenderPass2);
    MAP_PROC(vkCmdBindVertexBuffers);
    MAP_PROC(vkCmdBindVertexBuffers2);
    MAP_ALIAS("vkCmdBindVertexBuffers2EXT", vkCmdBindVertexBuffers2);
    MAP_PROC(vkCmdBindIndexBuffer);
    MAP_PROC(vkCmdDraw);
    MAP_PROC(vkCmdDrawIndexed);
    MAP_PROC(vkCmdDrawIndirect);
    MAP_PROC(vkCmdDrawIndexedIndirect);
    MAP_PROC(vkCmdDrawIndirectCount);
    MAP_ALIAS("vkCmdDrawIndirectCountKHR", vkCmdDrawIndirectCount);
    MAP_ALIAS("vkCmdDrawIndirectCountAMD", vkCmdDrawIndirectCount);
    MAP_PROC(vkCmdDrawIndexedIndirectCount);
    MAP_ALIAS("vkCmdDrawIndexedIndirectCountKHR", vkCmdDrawIndexedIndirectCount);
    MAP_ALIAS("vkCmdDrawIndexedIndirectCountAMD", vkCmdDrawIndexedIndirectCount);
    MAP_PROC(vkCmdSetViewport);
    MAP_PROC(vkCmdSetScissor);
    MAP_PROC(vkCmdSetLineWidth);
    MAP_PROC(vkCmdSetDepthBias);
    MAP_PROC(vkCmdSetBlendConstants);
    MAP_PROC(vkCmdSetDepthBounds);
    MAP_PROC(vkCmdSetStencilCompareMask);
    MAP_PROC(vkCmdSetStencilWriteMask);
    MAP_PROC(vkCmdSetStencilReference);
    MAP_PROC(vkCmdSetViewportWithCount);
    MAP_ALIAS("vkCmdSetViewportWithCountEXT", vkCmdSetViewportWithCount);
    MAP_PROC(vkCmdSetScissorWithCount);
    MAP_ALIAS("vkCmdSetScissorWithCountEXT", vkCmdSetScissorWithCount);
    MAP_PROC(vkCmdSetCullMode);
    MAP_ALIAS("vkCmdSetCullModeEXT", vkCmdSetCullMode);
    MAP_PROC(vkCmdSetFrontFace);
    MAP_ALIAS("vkCmdSetFrontFaceEXT", vkCmdSetFrontFace);
    MAP_PROC(vkCmdSetPrimitiveTopology);
    MAP_ALIAS("vkCmdSetPrimitiveTopologyEXT", vkCmdSetPrimitiveTopology);
    MAP_PROC(vkCmdSetDepthTestEnable);
    MAP_ALIAS("vkCmdSetDepthTestEnableEXT", vkCmdSetDepthTestEnable);
    MAP_PROC(vkCmdSetDepthWriteEnable);
    MAP_ALIAS("vkCmdSetDepthWriteEnableEXT", vkCmdSetDepthWriteEnable);
    MAP_PROC(vkCmdSetDepthCompareOp);
    MAP_ALIAS("vkCmdSetDepthCompareOpEXT", vkCmdSetDepthCompareOp);
    MAP_PROC(vkCmdSetStencilTestEnable);
    MAP_ALIAS("vkCmdSetStencilTestEnableEXT", vkCmdSetStencilTestEnable);
    MAP_PROC(vkCmdSetStencilOp);
    MAP_ALIAS("vkCmdSetStencilOpEXT", vkCmdSetStencilOp);
    MAP_PROC(vkCmdClearAttachments);
    MAP_PROC(vkCmdExecuteCommands);
    MAP_PROC(vkCmdBindDescriptorSets);
    MAP_PROC(vkCmdPushConstants);
    MAP_PROC(vkCmdPipelineBarrier);
    MAP_PROC(vkCmdPipelineBarrier2);
    MAP_ALIAS("vkCmdPipelineBarrier2KHR", vkCmdPipelineBarrier2);
    MAP_PROC(vkCmdCopyBuffer);
    MAP_PROC(vkCmdCopyBuffer2);
    MAP_ALIAS("vkCmdCopyBuffer2KHR", vkCmdCopyBuffer2);
    MAP_PROC(vkCmdCopyBufferToImage);
    MAP_PROC(vkCmdCopyBufferToImage2);
    MAP_ALIAS("vkCmdCopyBufferToImage2KHR", vkCmdCopyBufferToImage2);
    MAP_PROC(vkCmdCopyImageToBuffer);
    MAP_PROC(vkCmdCopyImageToBuffer2);
    MAP_ALIAS("vkCmdCopyImageToBuffer2KHR", vkCmdCopyImageToBuffer2);
    MAP_PROC(vkCmdCopyImage);
    MAP_PROC(vkCmdCopyImage2);
    MAP_ALIAS("vkCmdCopyImage2KHR", vkCmdCopyImage2);
    MAP_PROC(vkCmdClearColorImage);
    MAP_PROC(vkCmdResolveImage);
    MAP_PROC(vkCmdResolveImage2);
    MAP_ALIAS("vkCmdResolveImage2KHR", vkCmdResolveImage2);
    MAP_PROC(vkCmdBlitImage);
    MAP_PROC(vkCmdBlitImage2);
    MAP_ALIAS("vkCmdBlitImage2KHR", vkCmdBlitImage2);
    MAP_PROC(vkCmdClearDepthStencilImage);
    MAP_PROC(vkCmdFillBuffer);
    MAP_PROC(vkCmdUpdateBuffer);
    MAP_PROC(vkCmdDispatch);
    MAP_PROC(vkCmdDispatchBase);
    MAP_ALIAS("vkCmdDispatchBaseKHR", vkCmdDispatchBaseKHR);
    MAP_PROC(vkCmdDispatchIndirect);
    MAP_PROC(vkQueueSubmit);
    MAP_PROC(vkQueueSubmit2);
    MAP_ALIAS("vkQueueSubmit2KHR", vkQueueSubmit2);
    MAP_PROC(vkQueueWaitIdle);
    MAP_PROC(vkDeviceWaitIdle);
    MAP_PROC(vkCreateEvent);
    MAP_PROC(vkDestroyEvent);
    MAP_PROC(vkGetEventStatus);
    MAP_PROC(vkSetEvent);
    MAP_PROC(vkResetEvent);
    MAP_PROC(vkCmdSetEvent);
    MAP_PROC(vkCmdResetEvent);
    MAP_PROC(vkCmdWaitEvents);
    MAP_PROC(vkCmdSetEvent2);
    MAP_ALIAS("vkCmdSetEvent2KHR", vkCmdSetEvent2);
    MAP_PROC(vkCmdResetEvent2);
    MAP_ALIAS("vkCmdResetEvent2KHR", vkCmdResetEvent2);
    MAP_PROC(vkCmdWaitEvents2);
    MAP_ALIAS("vkCmdWaitEvents2KHR", vkCmdWaitEvents2);
    MAP_PROC(vkCreateQueryPool);
    MAP_PROC(vkDestroyQueryPool);
    MAP_PROC(vkCmdBeginQuery);
    MAP_PROC(vkCmdEndQuery);
    MAP_PROC(vkCmdResetQueryPool);
    MAP_PROC(vkCmdCopyQueryPoolResults);
    MAP_PROC(vkResetQueryPool);
    MAP_PROC(vkGetQueryPoolResults);
    MAP_PROC(vkCmdWriteTimestamp);
    MAP_PROC(vkCmdWriteTimestamp2);
    MAP_ALIAS("vkCmdWriteTimestamp2KHR", vkCmdWriteTimestamp2);
    MAP_PROC(vkCreateFence);
    MAP_PROC(vkDestroyFence);
    MAP_PROC(vkResetFences);
    MAP_PROC(vkGetFenceStatus);
    MAP_PROC(vkWaitForFences);
    MAP_PROC(vkCreateSemaphore);
    MAP_PROC(vkDestroySemaphore);
    MAP_PROC(vkGetSemaphoreCounterValue);
    MAP_ALIAS("vkGetSemaphoreCounterValueKHR", vkGetSemaphoreCounterValue);
    MAP_PROC(vkWaitSemaphores);
    MAP_ALIAS("vkWaitSemaphoresKHR", vkWaitSemaphores);
    MAP_PROC(vkSignalSemaphore);
    MAP_ALIAS("vkSignalSemaphoreKHR", vkSignalSemaphore);
    MAP_PROC(vk_icdNegotiateLoaderICDInterfaceVersion);
#undef MAP_ALIAS
#undef MAP_PROC
    return NULL;
}

VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL vkGetInstanceProcAddr(VkInstance instance, const char *pName) {
    (void)instance;
    return proc_address(pName);
}

VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL vkGetDeviceProcAddr(VkDevice device, const char *pName) {
    (void)device;
    return proc_address(pName);
}

VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL vk_icdGetInstanceProcAddr(VkInstance instance, const char *pName) {
    return vkGetInstanceProcAddr(instance, pName);
}

VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL vk_icdGetPhysicalDeviceProcAddr(VkInstance instance, const char *pName) {
    (void)instance;
    return proc_address(pName);
}

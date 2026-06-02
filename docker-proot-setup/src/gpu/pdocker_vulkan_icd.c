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

#define PDOCKER_VK_MAX_STORAGE_BUFFERS 16
#define PDOCKER_VK_MAX_DESCRIPTOR_SETS 8
#define PDOCKER_VK_MAX_PUSH_BYTES 256
#define PDOCKER_VK_MAX_ENTRY_NAME 128
#define PDOCKER_VK_MAX_SPECIALIZATION_ENTRIES 16
#define PDOCKER_VK_MAX_SPECIALIZATION_BYTES 256
#define PDOCKER_VK_REQUIREMENT_ALIGNMENT 16ull
#define PDOCKER_VK_MAX_COPY_OPS 64
#define PDOCKER_VK_MAX_DISPATCH_OPS 128
#define PDOCKER_VK_MAX_COMMAND_OPS 256
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
    PdockerVkDescriptorBinding storage_buffers[PDOCKER_VK_MAX_STORAGE_BUFFERS];
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

struct PdockerVkPipelineLayout {
    uint32_t push_constant_size;
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
};

struct PdockerVkFence {
    bool signaled;
};

typedef struct PdockerVkSemaphore {
    bool signaled;
} PdockerVkSemaphore;

struct PdockerVkEvent {
    bool signaled;
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
    VkBufferImageCopy region;
} PdockerVkImageCopyOp;

typedef struct {
    PdockerVkImage *src;
    PdockerVkImage *dst;
    VkImageCopy region;
} PdockerVkImageToImageCopyOp;

typedef struct {
    PdockerVkImage *image;
    VkClearColorValue color;
    VkImageSubresourceRange range;
} PdockerVkImageClearOp;

typedef struct {
    PdockerVkImage *src;
    PdockerVkImage *dst;
    VkImageResolve region;
} PdockerVkImageResolveOp;

typedef struct {
    PdockerVkImage *src;
    PdockerVkImage *dst;
    VkImageBlit region;
    VkFilter filter;
} PdockerVkImageBlitOp;

typedef struct {
    PdockerVkImage *image;
    VkClearDepthStencilValue value;
    VkImageSubresourceRange range;
} PdockerVkDepthStencilClearOp;

typedef struct {
    PdockerVkPipeline *pipeline;
    PdockerVkDescriptorSet set_snapshots[PDOCKER_VK_MAX_DESCRIPTOR_SETS];
    bool set_snapshot_used[PDOCKER_VK_MAX_DESCRIPTOR_SETS];
    uint32_t dispatch_x;
    uint32_t dispatch_y;
    uint32_t dispatch_z;
    uint8_t push_constants[PDOCKER_VK_MAX_PUSH_BYTES];
    uint32_t push_constant_size;
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
} PdockerVkCommandOp;

typedef struct {
    VK_LOADER_DATA loader;
    PdockerVkPipeline *pipeline;
    PdockerVkDescriptorSet bound_set_snapshots[PDOCKER_VK_MAX_DESCRIPTOR_SETS];
    bool bound_set_used[PDOCKER_VK_MAX_DESCRIPTOR_SETS];
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
    PdockerVkDispatchOp dispatch_ops[PDOCKER_VK_MAX_DISPATCH_OPS];
    uint32_t dispatch_op_count;
    PdockerVkCommandOp command_ops[PDOCKER_VK_MAX_COMMAND_OPS];
    uint32_t command_op_count;
    uint32_t dispatch_x;
    uint32_t dispatch_y;
    uint32_t dispatch_z;
    uint8_t push_constants[PDOCKER_VK_MAX_PUSH_BYTES];
    uint32_t push_constant_size;
    bool has_dispatch;
    bool unsupported_descriptor_set_layout;
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

static bool trace_allocations(void);

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
    cmd->dispatch_op_count = 0;
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

static int read_dispatch_response_status(int socket_fd, const char *transport_name) {
    const size_t max_response = 1024 * 1024;
    char stack_line[16384];
    size_t line_cap = sizeof(stack_line);
    size_t line_off = 0;
    char *heap_line = NULL;
    char *line = stack_line;
    int rc = 0;
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
        if (r < 0) {
            if (errno == EINTR) continue;
            rc = -errno;
            break;
        }
        if (r == 0) break;
        line[line_off++] = ch;
        if (ch == '\n') break;
    }
    line[line_off] = '\0';
    if (rc == 0 && line_off + 1 >= max_response) rc = -EMSGSIZE;
    if (trace_allocations() || getenv("PDOCKER_VULKAN_ICD_DEBUG") ||
        env_truthy_default("PDOCKER_GPU_DISPATCH_PROFILE_LOG", false)) {
        fprintf(stderr,
                "pdocker-vulkan-icd: %s dispatch response: %s",
                transport_name ? transport_name : "generic",
                line);
        if (line_off == 0 || line[line_off - 1] != '\n') fprintf(stderr, "\n");
    }
    if (rc == 0 && strstr(line, "\"valid\":true") == NULL) rc = -EIO;
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
        const uint32_t *bindings,
        const VkDeviceSize *offsets,
        const size_t *sizes,
        const VkDeviceSize *api_offsets,
        const VkDeviceSize *api_ranges,
        const size_t *api_buffer_sizes,
        const uint32_t *api_descriptor_types,
        const uint32_t *api_dynamic_flags,
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
                "%s{\"set\":%u,\"binding\":%u,\"offset\":%llu,\"size\":%zu,"
                "\"api_offset\":%llu,\"api_range\":%llu,"
                "\"api_buffer_size\":%zu,\"api_descriptor_type\":%u,"
                "\"api_dynamic\":%u,\"api_memory_offset\":%llu,"
                "\"api_memory_size\":%zu,\"api_memory_id\":%llu,"
                "\"api_buffer_id\":%llu}",
                i ? "," : "",
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

static uint32_t float_bits_u32(float value) {
    uint32_t bits = 0;
    memcpy(&bits, &value, sizeof(bits));
    return bits;
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
        const uint32_t *bindings,
        const VkDeviceSize *offsets,
        const size_t *sizes,
        const VkDeviceSize *api_offsets,
        const VkDeviceSize *api_ranges,
        const size_t *api_buffer_sizes,
        const uint32_t *api_descriptor_types,
        const uint32_t *api_dynamic_flags,
        const VkDeviceSize *api_memory_offsets,
        const size_t *api_memory_sizes,
        const uintptr_t *api_memory_ids,
        const uintptr_t *api_buffer_ids,
        const uint32_t *image_descriptor_sets,
        const uint32_t *image_descriptor_bindings,
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
         (!api_descriptor_sets || !bindings || !offsets || !sizes || !api_offsets ||
          !api_ranges || !api_buffer_sizes || !api_descriptor_types ||
          !api_dynamic_flags || !api_memory_offsets || !api_memory_sizes ||
          !api_memory_ids || !api_buffer_ids)) ||
        (image_descriptor_count > 0 &&
         (!image_descriptor_sets || !image_descriptor_bindings ||
          !image_descriptor_types || !image_descriptor_view_indices ||
          !image_descriptor_sampler_indices || !image_descriptor_layouts))) {
        return -EINVAL;
    }
    if (binding_count == 0 && image_descriptor_count == 0) return -EINVAL;
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
        return -E2BIG;
    }
    size_t entry_name_size = strlen(entry_name);
    if (entry_name_size == 0 || entry_name_size >= PDOCKER_VK_MAX_ENTRY_NAME) return -EINVAL;

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
        if (api_memory_offsets[i] > offsets[i]) return -ERANGE;
        uint64_t transfer_offset = (uint64_t)(offsets[i] - api_memory_offsets[i]);
        if (transfer_offset > api_buffer_sizes[i] ||
            sizes[i] > api_buffer_sizes[i] - transfer_offset) {
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
        descriptors[i].array_element = 0;
        descriptors[i].descriptor_type = api_descriptor_types[i];
        descriptors[i].descriptor_flags =
            (api_dynamic_flags[i] ? PDOCKER_GPU_V5_DESCRIPTOR_FLAG_DYNAMIC : 0u) |
            (api_ranges[i] == VK_WHOLE_SIZE ? PDOCKER_GPU_V5_DESCRIPTOR_FLAG_WHOLE_SIZE : 0u);
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
        descriptors[i].dynamic_offset = 0;
    }
    for (size_t i = 0; i < image_count; ++i) {
        PdockerVkImage *image = image_objects ? image_objects[i] : NULL;
        if (!image || !image->memory || image->memory->fd < 0) return -EINVAL;
        if (image->memory_offset > (VkDeviceSize)image->memory->size ||
            image->requirements_size > (VkDeviceSize)image->memory->size - image->memory_offset) {
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
        if (!view || !view->image) return -EINVAL;
        int image_index = find_image_table_index(image_objects, image_count, view->image);
        if (image_index < 0) return -EINVAL;
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
        if (!sampler) return -EINVAL;
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
            return -EINVAL;
        }
        if (descriptor_type_requires_sampler(type) &&
            image_descriptor_sampler_indices[i] == PDOCKER_GPU_V5_DESCRIPTOR_OBJECT_NONE) {
            return -EINVAL;
        }
        descriptors[descriptor_index].descriptor_set = image_descriptor_sets[i];
        descriptors[descriptor_index].binding = image_descriptor_bindings[i];
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
static bool descriptor_type_supported_by_v5_object_transport(VkDescriptorType type);
static VkSubgroupFeatureFlags advertised_subgroup_operations(void);
static uint32_t advertised_subgroup_size(void);
static bool resolve_copy_alias(PdockerVkBuffer *buffer,
                               VkDeviceSize offset,
                               VkDeviceSize size,
                               PdockerVkMemory **src_memory,
                               VkDeviceSize *src_offset);

static int send_generic_vulkan_dispatch_op(const PdockerVkDispatchOp *op) {
    if (!op || !op->pipeline || !op->pipeline->shader) return -EINVAL;
    PdockerVkShaderModule *shader = op->pipeline->shader;
    if (shader->code_fd < 0 || shader->code_size == 0) return -EINVAL;
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
    VkDeviceSize api_memory_offsets[PDOCKER_VK_MAX_STORAGE_BUFFERS];
    size_t api_memory_sizes[PDOCKER_VK_MAX_STORAGE_BUFFERS];
    uintptr_t api_memory_ids[PDOCKER_VK_MAX_STORAGE_BUFFERS];
    uintptr_t api_buffer_ids[PDOCKER_VK_MAX_STORAGE_BUFFERS];
    uint32_t api_descriptor_sets[PDOCKER_VK_MAX_STORAGE_BUFFERS];
    uint32_t image_descriptor_sets[PDOCKER_VK_MAX_STORAGE_BUFFERS];
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
    memset(image_descriptor_sets, 0, sizeof(image_descriptor_sets));
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
    for (uint32_t set_index = 0; set_index < PDOCKER_VK_MAX_DESCRIPTOR_SETS; ++set_index) {
        if (!op->set_snapshot_used[set_index]) continue;
        const PdockerVkDescriptorSet *set = &op->set_snapshots[set_index];
        for (uint32_t i = 0; i < PDOCKER_VK_MAX_STORAGE_BUFFERS; ++i) {
            PdockerVkDescriptorBinding *binding = (PdockerVkDescriptorBinding *)&set->storage_buffers[i];
            if (descriptor_type_supported_by_v5_object_transport(binding->descriptor_type)) {
                if (!vulkan_v5_frame_enabled()) return -EOPNOTSUPP;
                if (image_descriptor_count >= PDOCKER_VK_MAX_STORAGE_BUFFERS) return -E2BIG;
                VkDescriptorType descriptor_type = binding->descriptor_type;
                uint32_t view_index = PDOCKER_GPU_V5_DESCRIPTOR_OBJECT_NONE;
                uint32_t sampler_index = PDOCKER_GPU_V5_DESCRIPTOR_OBJECT_NONE;
                if (descriptor_type_requires_image_view(descriptor_type)) {
                    if (!binding->image_view || !binding->image_view->image ||
                        !binding->image_view->image->memory ||
                        binding->image_view->image->memory->fd < 0) {
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
                if (descriptor_type_requires_sampler(descriptor_type)) {
                    if (!binding->sampler) return -EINVAL;
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
                image_descriptor_types[image_descriptor_count] = descriptor_type;
                image_descriptor_view_indices[image_descriptor_count] = view_index;
                image_descriptor_sampler_indices[image_descriptor_count] = sampler_index;
                image_descriptor_layouts[image_descriptor_count] = binding->image_layout;
                image_descriptor_count++;
                continue;
            }
            if (!binding->buffer || !binding->buffer->memory) continue;
            size_t bytes = 0;
            int shape_rc = validate_descriptor_transport_shape(binding, set_index, i, &bytes);
            if (shape_rc < 0) return shape_rc;
            if (binding_count >= PDOCKER_VK_MAX_STORAGE_BUFFERS) return -E2BIG;
            api_descriptor_sets[binding_count] = set_index;
            bindings[binding_count] = i;
            PdockerVkMemory *dispatch_memory = binding->buffer->memory;
            VkDeviceSize dispatch_offset = binding->buffer->memory_offset + binding->offset;
            bool alias_hit = false;
            if (copy_alias_enabled()) {
                alias_hit = resolve_copy_alias(binding->buffer, binding->offset, bytes,
                                               &dispatch_memory, &dispatch_offset);
            }
            if (!dispatch_memory || dispatch_memory->fd < 0) return -EINVAL;
            if (dispatch_offset > (VkDeviceSize)dispatch_memory->size ||
                (VkDeviceSize)bytes > (VkDeviceSize)dispatch_memory->size - dispatch_offset) {
                fprintf(stderr,
                        "pdocker-vulkan-icd: rejecting descriptor outside transport memory"
                        " set=%u binding=%u dispatch_offset=%llu size=%zu memory_size=%zu alias=%u\n",
                        set_index,
                        i,
                        (unsigned long long)dispatch_offset,
                        bytes,
                        dispatch_memory ? dispatch_memory->size : 0,
                        alias_hit ? 1u : 0u);
                return -ERANGE;
            }
            offsets[binding_count] = dispatch_offset;
            sizes[binding_count] = bytes;
            api_offsets[binding_count] = binding->offset;
            api_ranges[binding_count] = binding->range;
            api_buffer_sizes[binding_count] = binding->buffer ? binding->buffer->size : 0;
            api_descriptor_types[binding_count] = (uint32_t)binding->descriptor_type;
            api_dynamic_flags[binding_count] = binding->dynamic ? 1u : 0u;
            api_memory_offsets[binding_count] = binding->buffer ? binding->buffer->memory_offset : 0;
            api_memory_sizes[binding_count] = dispatch_memory ? dispatch_memory->size : 0;
            api_memory_ids[binding_count] = (uintptr_t)dispatch_memory;
            api_buffer_ids[binding_count] = (uintptr_t)binding->buffer;
            fds[1 + binding_count] = dispatch_memory->fd;
            trace_guarded_binding(i, dispatch_memory, dispatch_offset, bytes);
            if (alias_hit && trace_allocations()) {
                fprintf(stderr,
                        "pdocker-vulkan-icd: descriptor alias set=%u binding=%u offset=%llu range=%zu source_mem=%zu source_off=%llu\n",
                        set_index,
                        i,
                        (unsigned long long)binding->offset,
                        bytes,
                        dispatch_memory ? dispatch_memory->size : 0,
                        (unsigned long long)dispatch_offset);
            }
            binding_count++;
        }
    }
    if (binding_count == 0 && image_descriptor_count == 0) return -EINVAL;

    const uint64_t source_shader_hash =
        (shader->code_map && shader->code_map != MAP_FAILED)
            ? fnv1a64_bytes(shader->code_map, shader->code_size)
            : 0;
    uint32_t push_size = op->push_constant_size;
    if (op->pipeline && op->pipeline->layout &&
        op->pipeline->layout->push_constant_size > push_size) {
        push_size = op->pipeline->layout->push_constant_size;
    }
    if (push_size > PDOCKER_VK_MAX_PUSH_BYTES) return -E2BIG;
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
    if (op->pipeline->specialization_too_large) return -E2BIG;

    PdockerVkSpirvProbeReplay probe;
    int probe_rc = prepare_spirv_probe_replay(&probe,
                                              source_shader_hash,
                                              binding_count,
                                              api_descriptor_sets,
                                              bindings);
    if (probe_rc < 0) return probe_rc;
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
        bindings[binding_count] = probe.debug_binding;
        offsets[binding_count] = 0;
        sizes[binding_count] = probe.debug_bytes;
        api_offsets[binding_count] = 0;
        api_ranges[binding_count] = probe.debug_bytes;
        api_buffer_sizes[binding_count] = probe.debug_bytes;
        api_descriptor_types[binding_count] = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
        api_dynamic_flags[binding_count] = 0;
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
                     op->dispatch_x,
                     op->dispatch_y ? op->dispatch_y : 1,
                     op->dispatch_z ? op->dispatch_z : 1,
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
    const uint32_t dispatch_y = op->dispatch_y ? op->dispatch_y : 1;
    const uint32_t dispatch_z = op->dispatch_z ? op->dispatch_z : 1;
    uint64_t dispatch_hash = 1469598103934665603ull;
    dispatch_hash = fnv1a64_update_u32(dispatch_hash, op->dispatch_x);
    dispatch_hash = fnv1a64_update_u32(dispatch_hash, dispatch_y);
    dispatch_hash = fnv1a64_update_u32(dispatch_hash, dispatch_z);
    uint64_t descriptor_hash = 1469598103934665603ull;
    descriptor_hash = fnv1a64_update_u64(descriptor_hash, (uint64_t)binding_count);
    for (size_t i = 0; i < binding_count; ++i) {
        descriptor_hash = fnv1a64_update_u32(descriptor_hash, api_descriptor_sets[i]);
        descriptor_hash = fnv1a64_update_u32(descriptor_hash, bindings[i]);
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
                                    bindings,
                                    offsets,
                                    sizes,
                                    api_offsets,
                                    api_ranges,
                                    api_buffer_sizes,
                                    api_descriptor_types,
                                    api_dynamic_flags,
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
    if (vulkan_v5_frame_enabled() && !copy_alias_enabled()) {
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
            bindings,
            offsets,
            sizes,
            api_offsets,
            api_ranges,
            api_buffer_sizes,
            api_descriptor_types,
            api_dynamic_flags,
            api_memory_offsets,
            api_memory_sizes,
            api_memory_ids,
            api_buffer_ids,
            image_descriptor_sets,
            image_descriptor_bindings,
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
    if (vulkan_v5_frame_enabled() && copy_alias_enabled() &&
        (trace_allocations() || getenv("PDOCKER_VULKAN_ICD_DEBUG"))) {
        fprintf(stderr,
                "pdocker-vulkan-icd: V5.1 frame disabled for this dispatch because PDOCKER_VULKAN_ALIAS_COPIES is active; using V4 text transport\n");
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
    op.pipeline = cmd->pipeline;
    memcpy(op.set_snapshots, cmd->bound_set_snapshots, sizeof(op.set_snapshots));
    memcpy(op.set_snapshot_used, cmd->bound_set_used, sizeof(op.set_snapshot_used));
    op.dispatch_x = cmd->dispatch_x;
    op.dispatch_y = cmd->dispatch_y;
    op.dispatch_z = cmd->dispatch_z;
    op.push_constant_size = cmd->push_constant_size;
    memcpy(op.push_constants, cmd->push_constants, sizeof(op.push_constants));
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
    VkPhysicalDeviceSubgroupProperties subgroup;
    bool ext_16bit_storage;
    bool ext_8bit_storage;
    bool ext_shader_float16_int8;
    bool ext_storage_buffer_storage_class;
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
    json_read_u32(json, "subgroupSize", &caps->subgroup.subgroupSize);
    json_read_u32(json, "supportedStages", &caps->subgroup.supportedStages);
    json_read_u32(json, "supportedOperations", &caps->subgroup.supportedOperations);

    if (json_read_u32(json, "VK_KHR_16bit_storage", &value)) caps->ext_16bit_storage = value != 0;
    if (json_read_u32(json, "VK_KHR_8bit_storage", &value)) caps->ext_8bit_storage = value != 0;
    if (json_read_u32(json, "VK_KHR_shader_float16_int8", &value)) caps->ext_shader_float16_int8 = value != 0;
    if (json_read_u32(json, "VK_KHR_storage_buffer_storage_class", &value)) caps->ext_storage_buffer_storage_class = value != 0;
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
                "type=%u storage16=%u storage8=%u int8=%u subgroup={size:%u,ops:0x%x}\n",
                caps->api_version,
                caps->device_name,
                caps->vendor_id,
                caps->device_id,
                caps->device_type,
                caps->storage16.storageBuffer16BitAccess,
                caps->storage8.storageBuffer8BitAccess,
                caps->float16_int8.shaderInt8,
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
                 "skydnir Vulkan bridge (%s)", caps->device_name);
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
    pProperties->limits.timestampComputeAndGraphics = VK_FALSE;
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
    (void)pCreateInfo;
    (void)pAllocator;
    if (!pInstance) return VK_ERROR_INITIALIZATION_FAILED;
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
    (void)format;
    if (!pFormatProperties) return;
    memset(pFormatProperties, 0, sizeof(*pFormatProperties));
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
    (void)format;
    (void)type;
    (void)tiling;
    (void)usage;
    (void)flags;
    if (!pImageFormatProperties) return VK_ERROR_FORMAT_NOT_SUPPORTED;
    memset(pImageFormatProperties, 0, sizeof(*pImageFormatProperties));
    return VK_ERROR_FORMAT_NOT_SUPPORTED;
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
        pQueueFamilyProperties[0].timestampValidBits = 0;
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
    VkExtensionProperties available[9];
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
            if (binding->descriptorCount > 1) {
                layout->unsupported_descriptor_array = true;
                fprintf(stderr,
                        "pdocker-vulkan-icd: descriptor array layout binding=%u count=%u type=%u is unsupported by V4 transport; rejecting instead of flattening\n",
                        binding->binding,
                        binding->descriptorCount,
                        binding->descriptorType);
            }
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
    for (uint32_t i = 0; pCreateInfo && i < pCreateInfo->pushConstantRangeCount; ++i) {
        const VkPushConstantRange *range = &pCreateInfo->pPushConstantRanges[i];
        uint32_t end = range->offset + range->size;
        if (end > layout->push_constant_size) layout->push_constant_size = end;
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
        if (w->descriptorCount > 1 || w->dstArrayElement != 0 ||
            (set->layout && set->layout->unsupported_descriptor_array)) {
            set->unsupported_descriptor_array = true;
            fprintf(stderr,
                    "pdocker-vulkan-icd: descriptor array write binding=%u array=%u count=%u is unsupported by V4 transport; rejecting instead of flattening\n",
                    w->dstBinding,
                    w->dstArrayElement,
                    w->descriptorCount);
            continue;
        }
        if (v5_object_descriptor) {
            if (!w->pImageInfo) continue;
            uint32_t binding = w->dstBinding;
            if (binding < PDOCKER_VK_MAX_STORAGE_BUFFERS) {
                const VkDescriptorImageInfo *info = &w->pImageInfo[0];
                set->storage_buffers[binding].buffer = NULL;
                set->storage_buffers[binding].image_view = (PdockerVkImageView *)info->imageView;
                set->storage_buffers[binding].sampler = (PdockerVkSampler *)info->sampler;
                set->storage_buffers[binding].image_layout = info->imageLayout;
                set->storage_buffers[binding].descriptor_type = w->descriptorType;
                set->storage_buffers[binding].dynamic = false;
                set->has_image_descriptor = true;
                if (trace_allocations()) {
                    fprintf(stderr,
                            "pdocker-vulkan-icd: descriptor image binding=%u type=%u view=%p sampler=%p layout=%u\n",
                            binding,
                            w->descriptorType,
                            (void *)set->storage_buffers[binding].image_view,
                            (void *)set->storage_buffers[binding].sampler,
                            (unsigned)set->storage_buffers[binding].image_layout);
                }
            }
            continue;
        }
        if (!w->pBufferInfo) continue;
        for (uint32_t j = 0; j < w->descriptorCount; ++j) {
            uint32_t binding = w->dstBinding;
            if (binding < PDOCKER_VK_MAX_STORAGE_BUFFERS) {
                set->storage_buffers[binding].buffer = (PdockerVkBuffer *)w->pBufferInfo[j].buffer;
                set->storage_buffers[binding].image_view = NULL;
                set->storage_buffers[binding].sampler = NULL;
                set->storage_buffers[binding].image_layout = VK_IMAGE_LAYOUT_UNDEFINED;
                set->storage_buffers[binding].offset = w->pBufferInfo[j].offset;
                set->storage_buffers[binding].range = w->pBufferInfo[j].range;
                set->storage_buffers[binding].descriptor_type = w->descriptorType;
                set->storage_buffers[binding].dynamic =
                    w->descriptorType == VK_DESCRIPTOR_TYPE_STORAGE_BUFFER_DYNAMIC;
                if (trace_allocations()) {
                    PdockerVkBuffer *buffer = set->storage_buffers[binding].buffer;
                    fprintf(stderr,
                            "pdocker-vulkan-icd: descriptor storage binding=%u base_binding=%u array=%u type=%u buffer_size=%zu offset=%llu range=%llu effective=%zu\n",
                            binding,
                            w->dstBinding,
                            w->dstArrayElement + j,
                            w->descriptorType,
                            buffer ? buffer->size : 0,
                            (unsigned long long)set->storage_buffers[binding].offset,
                            (unsigned long long)set->storage_buffers[binding].range,
                            descriptor_binding_size(&set->storage_buffers[binding]));
                }
            }
        }
    }
    for (uint32_t i = 0; i < descriptorCopyCount; ++i) {
        const VkCopyDescriptorSet *c = &pDescriptorCopies[i];
        PdockerVkDescriptorSet *src = c ? (PdockerVkDescriptorSet *)c->srcSet : NULL;
        PdockerVkDescriptorSet *dst = c ? (PdockerVkDescriptorSet *)c->dstSet : NULL;
        if (!src || !dst) continue;
        if (c->descriptorCount > 1 || c->srcArrayElement != 0 || c->dstArrayElement != 0 ||
            src->unsupported_descriptor_array || dst->unsupported_descriptor_array ||
            src->unsupported_descriptor_type || dst->unsupported_descriptor_type ||
            (src->layout && src->layout->unsupported_descriptor_array) ||
            (dst->layout && dst->layout->unsupported_descriptor_array) ||
            (src->layout && src->layout->unsupported_descriptor_type) ||
            (dst->layout && dst->layout->unsupported_descriptor_type)) {
            dst->unsupported_descriptor_array = true;
            fprintf(stderr,
                    "pdocker-vulkan-icd: descriptor array copy src_binding=%u src_array=%u dst_binding=%u dst_array=%u count=%u is unsupported by V4 transport; rejecting instead of flattening\n",
                    c->srcBinding,
                    c->srcArrayElement,
                    c->dstBinding,
                    c->dstArrayElement,
                    c->descriptorCount);
            continue;
        }
        for (uint32_t j = 0; j < c->descriptorCount; ++j) {
            uint32_t src_binding = c->srcBinding;
            uint32_t dst_binding = c->dstBinding;
            if (src_binding >= PDOCKER_VK_MAX_STORAGE_BUFFERS ||
                dst_binding >= PDOCKER_VK_MAX_STORAGE_BUFFERS) {
                continue;
            }
            dst->storage_buffers[dst_binding] = src->storage_buffers[src_binding];
            if (src->storage_buffers[src_binding].image_view ||
                src->storage_buffers[src_binding].sampler) {
                dst->has_image_descriptor = true;
            }
            if (trace_allocations()) {
                fprintf(stderr,
                        "pdocker-vulkan-icd: descriptor copy src=%u dst=%u count=%u\n",
                        src_binding,
                        dst_binding,
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

VKAPI_ATTR void VKAPI_CALL vkDestroyPipeline(
        VkDevice device,
        VkPipeline pipeline,
        const VkAllocationCallbacks *pAllocator) {
    (void)device;
    (void)pAllocator;
    free((void *)pipeline);
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
    (void)pBeginInfo;
    PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)commandBuffer;
    if (!cmd) return VK_ERROR_INITIALIZATION_FAILED;
    clear_recorded_command_ops(cmd);
    cmd->pipeline = NULL;
    memset(cmd->bound_set_snapshots, 0, sizeof(cmd->bound_set_snapshots));
    memset(cmd->bound_set_used, 0, sizeof(cmd->bound_set_used));
    cmd->dispatch_x = 0;
    cmd->dispatch_y = 0;
    cmd->dispatch_z = 0;
    memset(cmd->push_constants, 0, sizeof(cmd->push_constants));
    cmd->push_constant_size = 0;
    cmd->has_dispatch = false;
    cmd->unsupported_descriptor_set_layout = false;
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
    (void)pipelineBindPoint;
    PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)commandBuffer;
    if (cmd) cmd->pipeline = (PdockerVkPipeline *)pipeline;
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
    (void)pipelineBindPoint;
    (void)layout;
    PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)commandBuffer;
    if (cmd && descriptorSetCount > 0 && pDescriptorSets) {
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
            if (set->unsupported_descriptor_array || set->unsupported_descriptor_type ||
                (set->layout &&
                 (set->layout->unsupported_descriptor_array ||
                  set->layout->unsupported_descriptor_type))) {
                cmd->unsupported_descriptor_set_layout = true;
            }
            if (set->has_image_descriptor && !vulkan_v5_frame_enabled()) {
                cmd->unsupported_descriptor_set_layout = true;
                if (trace_allocations() || getenv("PDOCKER_VULKAN_ICD_DEBUG")) {
                    fprintf(stderr,
                            "pdocker-vulkan-icd: image descriptor set=%u requires V5.1 frame emission; rejecting submit because PDOCKER_VULKAN_USE_V5_FRAME is disabled\n",
                            target_set);
                }
            }
            cmd->bound_set_snapshots[target_set] = *set;
            cmd->bound_set_used[target_set] = true;
            for (uint32_t binding = 0; binding < PDOCKER_VK_MAX_STORAGE_BUFFERS; ++binding) {
                PdockerVkDescriptorBinding *slot =
                    &cmd->bound_set_snapshots[target_set].storage_buffers[binding];
                if (!slot->dynamic) continue;
                if (dynamic_index < dynamicOffsetCount && pDynamicOffsets) {
                    slot->offset += pDynamicOffsets[dynamic_index];
                    if (trace_allocations()) {
                        fprintf(stderr,
                                "pdocker-vulkan-icd: dynamic descriptor set=%u binding=%u dyn_index=%u add=%u effective_offset=%llu\n",
                                target_set,
                                binding,
                                dynamic_index,
                                pDynamicOffsets[dynamic_index],
                                (unsigned long long)slot->offset);
                    }
                } else if (trace_allocations() || getenv("PDOCKER_VULKAN_ICD_DEBUG")) {
                    fprintf(stderr,
                            "pdocker-vulkan-icd: missing dynamic offset set=%u binding=%u dyn_index=%u count=%u\n",
                            target_set,
                            binding,
                            dynamic_index,
                            dynamicOffsetCount);
                }
                dynamic_index++;
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
        if (cmd->dispatch_op_count < PDOCKER_VK_MAX_DISPATCH_OPS) {
            uint32_t op_index = cmd->dispatch_op_count++;
            PdockerVkDispatchOp *op = &cmd->dispatch_ops[op_index];
            op->pipeline = cmd->pipeline;
            memcpy(op->set_snapshots, cmd->bound_set_snapshots, sizeof(op->set_snapshots));
            memcpy(op->set_snapshot_used, cmd->bound_set_used, sizeof(op->set_snapshot_used));
            op->dispatch_x = groupCountX;
            op->dispatch_y = groupCountY;
            op->dispatch_z = groupCountZ;
            op->push_constant_size = cmd->push_constant_size;
            if (op->pipeline && op->pipeline->layout &&
                op->pipeline->layout->push_constant_size > op->push_constant_size) {
                op->push_constant_size = op->pipeline->layout->push_constant_size;
            }
            memcpy(op->push_constants, cmd->push_constants, sizeof(op->push_constants));
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

VKAPI_ATTR void VKAPI_CALL vkCmdPushConstants(
        VkCommandBuffer commandBuffer,
        VkPipelineLayout layout,
        VkShaderStageFlags stageFlags,
        uint32_t offset,
        uint32_t size,
        const void *pValues) {
    (void)layout;
    (void)stageFlags;
    PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)commandBuffer;
    if (!cmd || !pValues || offset >= PDOCKER_VK_MAX_PUSH_BYTES) return;
    if (offset + size > PDOCKER_VK_MAX_PUSH_BYTES) size = PDOCKER_VK_MAX_PUSH_BYTES - offset;
    memcpy(cmd->push_constants + offset, pValues, size);
    if (offset + size > cmd->push_constant_size) cmd->push_constant_size = offset + size;
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
    (void)srcStageMask;
    (void)dstStageMask;
    (void)dependencyFlags;
    (void)memoryBarrierCount;
    (void)pMemoryBarriers;
    (void)bufferMemoryBarrierCount;
    (void)pBufferMemoryBarriers;
    (void)imageMemoryBarrierCount;
    (void)pImageMemoryBarriers;
    PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)commandBuffer;
    if (cmd) {
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
    (void)dstImageLayout;
    PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)commandBuffer;
    PdockerVkBuffer *src = (PdockerVkBuffer *)srcBuffer;
    PdockerVkImage *dst = (PdockerVkImage *)dstImage;
    if (!cmd || !src || !dst || !src->memory || !dst->memory || !pRegions) return;
    for (uint32_t i = 0; i < regionCount; ++i) {
        record_image_copy_op(cmd,
                             PDOCKER_VK_IMAGE_COPY_BUFFER_TO_IMAGE,
                             src,
                             dst,
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
    (void)srcImageLayout;
    PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)commandBuffer;
    PdockerVkImage *src = (PdockerVkImage *)srcImage;
    PdockerVkBuffer *dst = (PdockerVkBuffer *)dstBuffer;
    if (!cmd || !src || !dst || !src->memory || !dst->memory || !pRegions) return;
    for (uint32_t i = 0; i < regionCount; ++i) {
        record_image_copy_op(cmd,
                             PDOCKER_VK_IMAGE_COPY_IMAGE_TO_BUFFER,
                             dst,
                             src,
                             &pRegions[i]);
    }
}

static void record_image_to_image_copy_op(PdockerVkCommandBuffer *cmd,
                                          PdockerVkImage *src,
                                          PdockerVkImage *dst,
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
    (void)srcImageLayout;
    (void)dstImageLayout;
    PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)commandBuffer;
    PdockerVkImage *src = (PdockerVkImage *)srcImage;
    PdockerVkImage *dst = (PdockerVkImage *)dstImage;
    if (!cmd || !src || !dst || !src->memory || !dst->memory || !pRegions) return;
    for (uint32_t i = 0; i < regionCount; ++i) {
        record_image_to_image_copy_op(cmd, src, dst, &pRegions[i]);
    }
}

static void record_clear_color_image_op(PdockerVkCommandBuffer *cmd,
                                        PdockerVkImage *image,
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
    op->color = *color;
    op->range = *range;
    PdockerVkCommandOp command_op;
    memset(&command_op, 0, sizeof(command_op));
    command_op.type = PDOCKER_VK_COMMAND_CLEAR_COLOR_IMAGE;
    command_op.index = op_index;
    (void)append_command_op(cmd, &command_op);
    if (trace_allocations()) {
        fprintf(stderr,
                "pdocker-vulkan-icd: record-clear-color-image image_req=%llu base_mip=%u levels=%u base_layer=%u layers=%u color_u32=%08x,%08x,%08x,%08x\n",
                (unsigned long long)image->requirements_size,
                range->baseMipLevel,
                range->levelCount,
                range->baseArrayLayer,
                range->layerCount,
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
    (void)imageLayout;
    PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)commandBuffer;
    PdockerVkImage *img = (PdockerVkImage *)image;
    if (!cmd || !img || !img->memory || !pColor || !pRanges) return;
    for (uint32_t i = 0; i < rangeCount; ++i) {
        record_clear_color_image_op(cmd, img, pColor, &pRanges[i]);
    }
}

static void record_resolve_image_op(PdockerVkCommandBuffer *cmd,
                                    PdockerVkImage *src,
                                    PdockerVkImage *dst,
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
    (void)srcImageLayout;
    (void)dstImageLayout;
    PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)commandBuffer;
    PdockerVkImage *src = (PdockerVkImage *)srcImage;
    PdockerVkImage *dst = (PdockerVkImage *)dstImage;
    if (!cmd || !src || !dst || !src->memory || !dst->memory || !pRegions) return;
    for (uint32_t i = 0; i < regionCount; ++i) {
        record_resolve_image_op(cmd, src, dst, &pRegions[i]);
    }
}

static void record_blit_image_op(PdockerVkCommandBuffer *cmd,
                                 PdockerVkImage *src,
                                 PdockerVkImage *dst,
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
    (void)srcImageLayout;
    (void)dstImageLayout;
    PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)commandBuffer;
    PdockerVkImage *src = (PdockerVkImage *)srcImage;
    PdockerVkImage *dst = (PdockerVkImage *)dstImage;
    if (!cmd || !src || !dst || !src->memory || !dst->memory || !pRegions) return;
    for (uint32_t i = 0; i < regionCount; ++i) {
        record_blit_image_op(cmd, src, dst, &pRegions[i], filter);
    }
}

static void record_clear_depth_stencil_image_op(PdockerVkCommandBuffer *cmd,
                                                PdockerVkImage *image,
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
    op->value = *value;
    op->range = *range;
    PdockerVkCommandOp command_op;
    memset(&command_op, 0, sizeof(command_op));
    command_op.type = PDOCKER_VK_COMMAND_CLEAR_DEPTH_STENCIL_IMAGE;
    command_op.index = op_index;
    (void)append_command_op(cmd, &command_op);
    if (trace_allocations()) {
        fprintf(stderr,
                "pdocker-vulkan-icd: record-clear-depth-stencil-image image_req=%llu base_mip=%u levels=%u base_layer=%u layers=%u aspect=0x%x depth=%f stencil=%u\n",
                (unsigned long long)image->requirements_size,
                range->baseMipLevel,
                range->levelCount,
                range->baseArrayLayer,
                range->layerCount,
                (unsigned)range->aspectMask,
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
    (void)imageLayout;
    PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)commandBuffer;
    PdockerVkImage *img = (PdockerVkImage *)image;
    if (!cmd || !img || !img->memory || !pDepthStencil || !pRanges) return;
    for (uint32_t i = 0; i < rangeCount; ++i) {
        record_clear_depth_stencil_image_op(cmd, img, pDepthStencil, &pRanges[i]);
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

static VkResult validate_submit_wait_semaphores(const VkSubmitInfo *submit) {
    if (!submit) return VK_ERROR_INITIALIZATION_FAILED;
    for (uint32_t i = 0; i < submit->waitSemaphoreCount; ++i) {
        PdockerVkSemaphore *sem = submit->pWaitSemaphores
            ? (PdockerVkSemaphore *)submit->pWaitSemaphores[i]
            : NULL;
        if (!sem || !sem->signaled) {
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
        if (sem) sem->signaled = false;
    }
    for (uint32_t i = 0; i < submit->signalSemaphoreCount; ++i) {
        PdockerVkSemaphore *sem = submit->pSignalSemaphores
            ? (PdockerVkSemaphore *)submit->pSignalSemaphores[i]
            : NULL;
        if (sem) sem->signaled = true;
    }
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
            if (cmd->pipeline && cmd->pipeline->shader && cmd->pipeline->shader->code_size > sizeof(uint32_t)) {
                int generic_rc = send_generic_vulkan_dispatch(cmd);
                if (generic_rc == 0) continue;
                trace_icd_runtime_failure("generic-dispatch-single", generic_rc);
                if (trace_allocations() || getenv("PDOCKER_VULKAN_ICD_DEBUG")) {
                    fprintf(stderr,
                            "pdocker-vulkan-icd: generic SPIR-V dispatch failed rc=%d code_size=%zu first_word=0x%08x dispatch=%u,%u,%u push=%u\n",
                            generic_rc,
                            cmd->pipeline->shader->code_size,
                            cmd->pipeline->shader->first_word,
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
            if (!cmd || !legacy_set || !legacy_set->storage_buffers[0].buffer ||
                !legacy_set->storage_buffers[1].buffer || !legacy_set->storage_buffers[2].buffer) {
                if (trace_allocations() || getenv("PDOCKER_VULKAN_ICD_DEBUG")) {
                    fprintf(stderr,
                            "pdocker-vulkan-icd: vector-add dispatch missing storage buffers set=%p\n",
                            (void *)legacy_set);
                }
                return VK_ERROR_FEATURE_NOT_PRESENT;
            }
            PdockerVkBuffer *a = legacy_set->storage_buffers[0].buffer;
            PdockerVkBuffer *b = legacy_set->storage_buffers[1].buffer;
            PdockerVkBuffer *out = legacy_set->storage_buffers[2].buffer;
            if (!a->memory || !b->memory || !out->memory) return VK_ERROR_MEMORY_MAP_FAILED;
            size_t n = descriptor_binding_size(&legacy_set->storage_buffers[0]) / sizeof(float);
            size_t b_n = descriptor_binding_size(&legacy_set->storage_buffers[1]) / sizeof(float);
            size_t out_n = descriptor_binding_size(&legacy_set->storage_buffers[2]) / sizeof(float);
            if (b_n < n) n = b_n;
            if (out_n < n) n = out_n;
            if (cmd->dispatch_x && cmd->pipeline && cmd->pipeline->local_size_x) {
                size_t dispatched = (size_t)cmd->dispatch_x * cmd->pipeline->local_size_x;
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
    (void)eventCount;
    (void)pEvents;
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

VKAPI_ATTR VkResult VKAPI_CALL vkCreateSemaphore(
        VkDevice device,
        const VkSemaphoreCreateInfo *pCreateInfo,
        const VkAllocationCallbacks *pAllocator,
        VkSemaphore *pSemaphore) {
    (void)device;
    (void)pAllocator;
    if (!pSemaphore) return VK_ERROR_INITIALIZATION_FAILED;
    PdockerVkSemaphore *sem = pdocker_alloc_handle(sizeof(*sem));
    if (!sem) return VK_ERROR_OUT_OF_HOST_MEMORY;
    sem->signaled = false;
    if (pCreateInfo && pCreateInfo->pNext) {
        trace_icd_runtime_failure("semaphore-pnext-unsupported",
                                  VK_ERROR_FEATURE_NOT_PRESENT);
        free(sem);
        return VK_ERROR_FEATURE_NOT_PRESENT;
    }
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
    MAP_PROC(vkDestroyPipeline);
    MAP_PROC(vkCreateCommandPool);
    MAP_PROC(vkDestroyCommandPool);
    MAP_PROC(vkResetCommandPool);
    MAP_PROC(vkAllocateCommandBuffers);
    MAP_PROC(vkFreeCommandBuffers);
    MAP_PROC(vkBeginCommandBuffer);
    MAP_PROC(vkEndCommandBuffer);
    MAP_PROC(vkResetCommandBuffer);
    MAP_PROC(vkCmdBindPipeline);
    MAP_PROC(vkCmdBindDescriptorSets);
    MAP_PROC(vkCmdPushConstants);
    MAP_PROC(vkCmdPipelineBarrier);
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
    MAP_PROC(vkQueueSubmit);
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
    MAP_PROC(vkCreateFence);
    MAP_PROC(vkDestroyFence);
    MAP_PROC(vkResetFences);
    MAP_PROC(vkGetFenceStatus);
    MAP_PROC(vkWaitForFences);
    MAP_PROC(vkCreateSemaphore);
    MAP_PROC(vkDestroySemaphore);
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

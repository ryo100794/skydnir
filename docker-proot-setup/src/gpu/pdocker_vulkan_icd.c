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
#define PDOCKER_VULKAN_ICD_BUILD_MARKER "vulkan-icd-runtime-marker-20260510"
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

struct PdockerVkDescriptorBinding {
    PdockerVkBuffer *buffer;
    VkDeviceSize offset;
    VkDeviceSize range;
    VkDescriptorType descriptor_type;
    bool dynamic;
};

struct PdockerVkDescriptorSetLayout {
    uint32_t storage_binding_count;
    VkDescriptorType storage_binding_types[PDOCKER_VK_MAX_STORAGE_BUFFERS];
    uint32_t storage_binding_counts[PDOCKER_VK_MAX_STORAGE_BUFFERS];
};

struct PdockerVkDescriptorSet {
    PdockerVkDescriptorSetLayout *layout;
    PdockerVkDescriptorBinding storage_buffers[PDOCKER_VK_MAX_STORAGE_BUFFERS];
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

typedef struct {
    PdockerVkBuffer *src;
    PdockerVkBuffer *dst;
    VkBufferCopy region;
} PdockerVkCopyOp;

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
} PdockerVkCommandOpType;

typedef struct {
    PdockerVkCommandOpType type;
    uint32_t index;
    PdockerVkBuffer *buffer;
    VkDeviceSize offset;
    VkDeviceSize size;
    uint32_t data;
    void *payload;
} PdockerVkCommandOp;

typedef struct {
    VK_LOADER_DATA loader;
    PdockerVkPipeline *pipeline;
    PdockerVkDescriptorSet bound_set_snapshots[PDOCKER_VK_MAX_DESCRIPTOR_SETS];
    bool bound_set_used[PDOCKER_VK_MAX_DESCRIPTOR_SETS];
    PdockerVkCopyOp copy_ops[PDOCKER_VK_MAX_COPY_OPS];
    uint32_t copy_op_count;
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

static VkBool32 advertised_shader_int64(void) {
    /*
     * llama.cpp/ggml can select different shader variants from advertised
     * features. Until the executor proves the Android device supports the same
     * SPIR-V path, keep expensive/fragile features opt-in instead of optimistic.
     */
    return env_truthy_default("PDOCKER_VULKAN_ENABLE_INT64", false) ? VK_TRUE : VK_FALSE;
}

static VkBool32 advertised_storage16(void) {
    /*
     * llama.cpp currently expects 16-bit storage to load useful Vulkan paths on
     * this device. Keep it enabled by default, but allow tuning runs to clamp it
     * off when investigating Android executor capability mismatches.
     */
    if (env_disabled("PDOCKER_VULKAN_DISABLE_16BIT_STORAGE")) return VK_FALSE;
    return env_truthy_default("PDOCKER_VULKAN_ENABLE_16BIT_STORAGE", true) ? VK_TRUE : VK_FALSE;
}

static VkBool32 advertised_storage8(void) {
    /*
     * ggml Vulkan can emit int8/8-bit-storage kernels for quantized weights.
     * The APK-side Android executor currently rejects at least one real
     * llama.cpp 8-bit SPIR-V pipeline on this device, so keep the glibc-facing
     * bridge conservative by default. Advanced tuning runs can opt back in.
     */
    if (env_disabled("PDOCKER_VULKAN_DISABLE_8BIT_STORAGE")) return VK_FALSE;
    return env_truthy_default("PDOCKER_VULKAN_ENABLE_8BIT_STORAGE", false) ? VK_TRUE : VK_FALSE;
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

static void trace_pnext_chain(const char *prefix, const void *pNext) {
    if (!trace_allocations()) return;
    const VkBaseInStructure *base = (const VkBaseInStructure *)pNext;
    while (base) {
        fprintf(stderr,
                "pdocker-vulkan-icd: %s pnext sType=%d\n",
                prefix,
                (int)base->sType);
        base = base->pNext;
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

static bool dispatch_lifecycle_log_enabled(void) {
    return trace_allocations() ||
           getenv("PDOCKER_VULKAN_ICD_DEBUG") ||
           env_truthy_default("PDOCKER_GPU_DISPATCH_PROFILE_LOG", false);
}

static size_t descriptor_binding_size(const PdockerVkDescriptorBinding *binding);
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

    int fds[1 + PDOCKER_VK_MAX_STORAGE_BUFFERS];
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
    size_t binding_count = 0;
    fds[0] = shader->code_fd;
    for (uint32_t set_index = 0; set_index < PDOCKER_VK_MAX_DESCRIPTOR_SETS; ++set_index) {
        if (!op->set_snapshot_used[set_index]) continue;
        const PdockerVkDescriptorSet *set = &op->set_snapshots[set_index];
        for (uint32_t i = 0; i < PDOCKER_VK_MAX_STORAGE_BUFFERS; ++i) {
            PdockerVkDescriptorBinding *binding = (PdockerVkDescriptorBinding *)&set->storage_buffers[i];
            if (!binding->buffer || !binding->buffer->memory) continue;
            size_t bytes = descriptor_binding_size(binding);
            if (bytes == 0) continue;
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
    if (binding_count == 0) return -EINVAL;

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

    char command[4096];
    int n = snprintf(command, sizeof(command),
                     "VULKAN_DISPATCH_V4 %zu %zu %u %u %u %u %s %s %u %zu %s",
                     shader->code_size,
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
    if (n < 0 || (size_t)n >= sizeof(command)) return -ENAMETOOLONG;
    size_t off = (size_t)n;
    for (uint32_t i = 0; i < op->pipeline->specialization_entry_count; ++i) {
        const VkSpecializationMapEntry *entry = &op->pipeline->specialization_entries[i];
        n = snprintf(command + off, sizeof(command) - off,
                     " %u %u %zu",
                     entry->constantID,
                     entry->offset,
                     entry->size);
        if (n < 0 || (size_t)n >= sizeof(command) - off) return -ENAMETOOLONG;
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
        if (n < 0 || (size_t)n >= sizeof(command) - off) return -ENAMETOOLONG;
        off += (size_t)n;
    }
    if (getenv("PDOCKER_GPU_WRITEONLY_DIRTY_PROBE")) {
        n = snprintf(command + off, sizeof(command) - off,
                     " dirty_probe=%u",
                     env_truthy_default("PDOCKER_GPU_WRITEONLY_DIRTY_PROBE", false) ? 1u : 0u);
        if (n < 0 || (size_t)n >= sizeof(command) - off) return -ENAMETOOLONG;
        off += (size_t)n;
    }
    if (getenv("PDOCKER_GPU_WRITEONLY_DIRTY_WRITEBACK")) {
        n = snprintf(command + off, sizeof(command) - off,
                     " dirty_writeback=%u",
                     env_truthy_default("PDOCKER_GPU_WRITEONLY_DIRTY_WRITEBACK", false) ? 1u : 0u);
        if (n < 0 || (size_t)n >= sizeof(command) - off) return -ENAMETOOLONG;
        off += (size_t)n;
    }
    if (getenv("PDOCKER_GPU_WRITEONLY_BUFFER_CACHE")) {
        n = snprintf(command + off, sizeof(command) - off,
                     " writeonly_cache=%u",
                     env_truthy_default("PDOCKER_GPU_WRITEONLY_BUFFER_CACHE", false) ? 1u : 0u);
        if (n < 0 || (size_t)n >= sizeof(command) - off) return -ENAMETOOLONG;
        off += (size_t)n;
    }
    if (getenv("PDOCKER_GPU_MUTABLE_BUFFER_CACHE")) {
        n = snprintf(command + off, sizeof(command) - off,
                     " mutable_cache=%u",
                     env_truthy_default("PDOCKER_GPU_MUTABLE_BUFFER_CACHE", true) ? 1u : 0u);
        if (n < 0 || (size_t)n >= sizeof(command) - off) return -ENAMETOOLONG;
        off += (size_t)n;
    }
    const char *mutable_cache_max = getenv("PDOCKER_GPU_MUTABLE_BUFFER_CACHE_MAX_BYTES");
    if (mutable_cache_max && mutable_cache_max[0]) {
        char *end = NULL;
        unsigned long long parsed = strtoull(mutable_cache_max, &end, 10);
        if (end && *end == '\0') {
            n = snprintf(command + off, sizeof(command) - off,
                         " mutable_cache_max=%llu",
                         parsed);
            if (n < 0 || (size_t)n >= sizeof(command) - off) return -ENAMETOOLONG;
            off += (size_t)n;
        }
    }
    if (getenv("PDOCKER_GPU_RESIDENT_CACHE")) {
        n = snprintf(command + off, sizeof(command) - off,
                     " resident_cache=%u",
                     env_truthy_default("PDOCKER_GPU_RESIDENT_CACHE", true) ? 1u : 0u);
        if (n < 0 || (size_t)n >= sizeof(command) - off) return -ENAMETOOLONG;
        off += (size_t)n;
    }
    const char *resident_cache_min = getenv("PDOCKER_GPU_RESIDENT_CACHE_MIN_BYTES");
    if (resident_cache_min && resident_cache_min[0]) {
        char *end = NULL;
        unsigned long long parsed = strtoull(resident_cache_min, &end, 10);
        if (end && *end == '\0') {
            n = snprintf(command + off, sizeof(command) - off,
                         " resident_cache_min=%llu",
                         parsed);
            if (n < 0 || (size_t)n >= sizeof(command) - off) return -ENAMETOOLONG;
            off += (size_t)n;
        }
    }
    const char *dirty_probe_min = getenv("PDOCKER_GPU_WRITEONLY_DIRTY_PROBE_MIN_BYTES");
    if (dirty_probe_min && dirty_probe_min[0]) {
        char *end = NULL;
        unsigned long long parsed = strtoull(dirty_probe_min, &end, 10);
        if (end && *end == '\0') {
            n = snprintf(command + off, sizeof(command) - off,
                         " dirty_probe_min=%llu",
                         parsed);
            if (n < 0 || (size_t)n >= sizeof(command) - off) return -ENAMETOOLONG;
            off += (size_t)n;
        }
    }
    if (trace_allocations() || env_truthy_default("PDOCKER_GPU_DISPATCH_PROFILE_RESPONSE", false)) {
        n = snprintf(command + off, sizeof(command) - off, " profile=1");
        if (n < 0 || (size_t)n >= sizeof(command) - off) return -ENAMETOOLONG;
        off += (size_t)n;
    }
    if (getenv("PDOCKER_GPU_STRICT_PASSTHROUGH")) {
        n = snprintf(command + off, sizeof(command) - off,
                     " strict_passthrough=%u",
                     env_truthy_default("PDOCKER_GPU_STRICT_PASSTHROUGH", false) ? 1u : 0u);
        if (n < 0 || (size_t)n >= sizeof(command) - off) return -ENAMETOOLONG;
        off += (size_t)n;
    }
    if (getenv("PDOCKER_GPU_STRICT_DEVICE_LOCAL_STAGING")) {
        n = snprintf(command + off, sizeof(command) - off,
                     " strict_device_local_staging=%u",
                     env_truthy_default("PDOCKER_GPU_STRICT_DEVICE_LOCAL_STAGING", false) ? 1u : 0u);
        if (n < 0 || (size_t)n >= sizeof(command) - off) return -ENAMETOOLONG;
        off += (size_t)n;
    }
    if (getenv("PDOCKER_GPU_REWRITE_DUPLICATE_DESCRIPTOR_BINDINGS")) {
        n = snprintf(command + off, sizeof(command) - off,
                     " rewrite_duplicate_descriptors=%u",
                     env_truthy_default("PDOCKER_GPU_REWRITE_DUPLICATE_DESCRIPTOR_BINDINGS", true) ? 1u : 0u);
        if (n < 0 || (size_t)n >= sizeof(command) - off) return -ENAMETOOLONG;
        off += (size_t)n;
    }
    if (getenv("PDOCKER_GPU_MATERIALIZE_DESCRIPTOR_ALIASES")) {
        n = snprintf(command + off, sizeof(command) - off,
                     " materialize_descriptor_aliases=%u",
                     env_truthy_default("PDOCKER_GPU_MATERIALIZE_DESCRIPTOR_ALIASES", false) ? 1u : 0u);
        if (n < 0 || (size_t)n >= sizeof(command) - off) return -ENAMETOOLONG;
        off += (size_t)n;
    }
    if (getenv("PDOCKER_GPU_MATERIALIZE_SPIRV_SPECIALIZATION_CONSTANTS")) {
        n = snprintf(command + off, sizeof(command) - off,
                     " materialize_specialization=%u",
                     env_truthy_default("PDOCKER_GPU_MATERIALIZE_SPIRV_SPECIALIZATION_CONSTANTS", true) ? 1u : 0u);
        if (n < 0 || (size_t)n >= sizeof(command) - off) return -ENAMETOOLONG;
        off += (size_t)n;
    }
    if (getenv("PDOCKER_GPU_DISABLE_PIPELINE_OPTIMIZATION")) {
        n = snprintf(command + off, sizeof(command) - off,
                     " disable_pipeline_optimization=%u",
                     env_truthy_default("PDOCKER_GPU_DISABLE_PIPELINE_OPTIMIZATION", true) ? 1u : 0u);
        if (n < 0 || (size_t)n >= sizeof(command) - off) return -ENAMETOOLONG;
        off += (size_t)n;
    }
    if (getenv("PDOCKER_GPU_SKIP_UNUSED_DESCRIPTOR_TRANSFERS")) {
        n = snprintf(command + off, sizeof(command) - off,
                     " skip_unused_descriptor_transfers=%u",
                     env_truthy_default("PDOCKER_GPU_SKIP_UNUSED_DESCRIPTOR_TRANSFERS", true) ? 1u : 0u);
        if (n < 0 || (size_t)n >= sizeof(command) - off) return -ENAMETOOLONG;
        off += (size_t)n;
    }
    if (getenv("PDOCKER_GPU_USE_SPIRV_DESCRIPTOR_ACCESS")) {
        n = snprintf(command + off, sizeof(command) - off,
                     " use_spirv_descriptor_access=%u",
                     env_truthy_default("PDOCKER_GPU_USE_SPIRV_DESCRIPTOR_ACCESS", true) ? 1u : 0u);
        if (n < 0 || (size_t)n >= sizeof(command) - off) return -ENAMETOOLONG;
        off += (size_t)n;
    }
    if (getenv("PDOCKER_GPU_DISABLE_OVERLAP_ALIASING")) {
        n = snprintf(command + off, sizeof(command) - off,
                     " disable_overlap_aliasing=%u",
                     env_truthy_default("PDOCKER_GPU_DISABLE_OVERLAP_ALIASING", false) ? 1u : 0u);
        if (n < 0 || (size_t)n >= sizeof(command) - off) return -ENAMETOOLONG;
        off += (size_t)n;
    }
    if (getenv("PDOCKER_GPU_CPU_ORACLE")) {
        n = snprintf(command + off, sizeof(command) - off,
                     " cpu_oracle=%u",
                     env_truthy_default("PDOCKER_GPU_CPU_ORACLE", false) ? 1u : 0u);
        if (n < 0 || (size_t)n >= sizeof(command) - off) return -ENAMETOOLONG;
        off += (size_t)n;
    }
    if (getenv("PDOCKER_GPU_Q6K_ORACLE_WRITEBACK")) {
        n = snprintf(command + off, sizeof(command) - off,
                     " q6k_oracle_writeback=%u",
                     env_truthy_default("PDOCKER_GPU_Q6K_ORACLE_WRITEBACK", false) ? 1u : 0u);
        if (n < 0 || (size_t)n >= sizeof(command) - off) return -ENAMETOOLONG;
        off += (size_t)n;
    }
    if (getenv("PDOCKER_GPU_Q6K_SAFE_KERNEL")) {
        n = snprintf(command + off, sizeof(command) - off,
                     " q6k_safe_kernel=%u",
                     env_truthy_default("PDOCKER_GPU_Q6K_SAFE_KERNEL", false) ? 1u : 0u);
        if (n < 0 || (size_t)n >= sizeof(command) - off) return -ENAMETOOLONG;
        off += (size_t)n;
    }
    if (getenv("PDOCKER_GPU_Q4K_SAFE_KERNEL")) {
        n = snprintf(command + off, sizeof(command) - off,
                     " q4k_safe_kernel=%u",
                     env_truthy_default("PDOCKER_GPU_Q4K_SAFE_KERNEL", false) ? 1u : 0u);
        if (n < 0 || (size_t)n >= sizeof(command) - off) return -ENAMETOOLONG;
        off += (size_t)n;
    }
    if (getenv("PDOCKER_GPU_Q4K_TARGETED_SPECIALIZATION")) {
        n = snprintf(command + off, sizeof(command) - off,
                     " q4k_targeted_specialization=%u",
                     env_truthy_default("PDOCKER_GPU_Q4K_TARGETED_SPECIALIZATION", false) ? 1u : 0u);
        if (n < 0 || (size_t)n >= sizeof(command) - off) return -ENAMETOOLONG;
        off += (size_t)n;
    }
    if (getenv("PDOCKER_VULKAN_DISABLE_8BIT_STORAGE")) {
        n = snprintf(command + off, sizeof(command) - off,
                     " disable_storage8=%u",
                     env_truthy_default("PDOCKER_VULKAN_DISABLE_8BIT_STORAGE", false) ? 1u : 0u);
        if (n < 0 || (size_t)n >= sizeof(command) - off) return -ENAMETOOLONG;
        off += (size_t)n;
    }
    if (getenv("PDOCKER_VULKAN_DISABLE_16BIT_STORAGE")) {
        n = snprintf(command + off, sizeof(command) - off,
                     " disable_storage16=%u",
                     env_truthy_default("PDOCKER_VULKAN_DISABLE_16BIT_STORAGE", false) ? 1u : 0u);
        if (n < 0 || (size_t)n >= sizeof(command) - off) return -ENAMETOOLONG;
        off += (size_t)n;
    }
    if (getenv("PDOCKER_VULKAN_DISABLE_SUBGROUP_ARITHMETIC")) {
        n = snprintf(command + off, sizeof(command) - off,
                     " disable_subgroup_arithmetic=%u",
                     env_truthy_default("PDOCKER_VULKAN_DISABLE_SUBGROUP_ARITHMETIC", false) ? 1u : 0u);
        if (n < 0 || (size_t)n >= sizeof(command) - off) return -ENAMETOOLONG;
        off += (size_t)n;
    }
    n = snprintf(command + off, sizeof(command) - off,
                 " requested_feature_mask=%llu",
                 (unsigned long long)op->pipeline->requested_feature_mask);
    if (n < 0 || (size_t)n >= sizeof(command) - off) return -ENAMETOOLONG;
    off += (size_t)n;
    if (off + 2 >= sizeof(command)) return -ENAMETOOLONG;
    command[off++] = '\n';
    command[off] = '\0';

    const uint64_t dispatch_id = __sync_add_and_fetch(&g_generic_dispatch_sequence, 1);
    const uint64_t shader_hash = fnv1a64_bytes(shader->code_map, shader->code_size);
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
                shader->code_size,
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
        return socket_fd;
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

static void fill_physical_device_properties(VkPhysicalDeviceProperties *pProperties) {
    if (!pProperties) return;
    memset(pProperties, 0, sizeof(*pProperties));
    pProperties->apiVersion = pdocker_api_version();
    pProperties->driverVersion = VK_MAKE_API_VERSION(0, 0, 1, 0);
    pProperties->vendorID = 0x5044; /* PD */
    pProperties->deviceID = 0x0001;
    /*
     * The Android GPU is usually physically integrated, but this glibc-facing
     * ICD does not expose true UMA pointers into vendor memory. Work is
     * lowered through the APK-owned executor, so advertise a discrete-like
     * device to keep ggml/llama.cpp out of UMA host-pointer fast paths.
     */
    pProperties->deviceType = bridge_available() ? VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU
                                                 : VK_PHYSICAL_DEVICE_TYPE_CPU;
    snprintf(pProperties->deviceName, sizeof(pProperties->deviceName),
             "pdocker Vulkan bridge (%s)", bridge_available() ? "queue" : "offline");
    pProperties->limits.maxComputeSharedMemorySize = 32768;
    pProperties->limits.maxComputeWorkGroupCount[0] = 65535;
    pProperties->limits.maxComputeWorkGroupCount[1] = 65535;
    pProperties->limits.maxComputeWorkGroupCount[2] = 65535;
    pProperties->limits.maxComputeWorkGroupInvocations = 256;
    pProperties->limits.maxComputeWorkGroupSize[0] = 256;
    pProperties->limits.maxComputeWorkGroupSize[1] = 256;
    pProperties->limits.maxComputeWorkGroupSize[2] = 64;
    pProperties->limits.maxPushConstantsSize = 256;
    VkDeviceSize max_buffer = pdocker_vulkan_max_buffer_size();
    pProperties->limits.maxStorageBufferRange = max_buffer > UINT32_MAX ? UINT32_MAX : (uint32_t)max_buffer;
    pProperties->limits.maxMemoryAllocationCount = 4096;
    pProperties->limits.maxBoundDescriptorSets = 8;
    pProperties->limits.maxPerStageDescriptorStorageBuffers = PDOCKER_VK_MAX_STORAGE_BUFFERS;
    pProperties->limits.maxDescriptorSetStorageBuffers = PDOCKER_VK_MAX_STORAGE_BUFFERS;
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
    for (VkBaseOutStructure *cur = (VkBaseOutStructure *)pNext; cur; cur = cur->pNext) {
        switch (cur->sType) {
            case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MAINTENANCE_3_PROPERTIES: {
                VkPhysicalDeviceMaintenance3Properties *p = (VkPhysicalDeviceMaintenance3Properties *)cur;
                p->maxPerSetDescriptors = 1024;
                p->maxMemoryAllocationSize = pdocker_vulkan_max_buffer_size();
                break;
            }
#ifdef VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MAINTENANCE_4_PROPERTIES
            case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MAINTENANCE_4_PROPERTIES: {
                VkPhysicalDeviceMaintenance4Properties *p = (VkPhysicalDeviceMaintenance4Properties *)cur;
                p->maxBufferSize = pdocker_vulkan_max_buffer_size();
                break;
            }
#endif
            case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SUBGROUP_PROPERTIES: {
                VkPhysicalDeviceSubgroupProperties *p = (VkPhysicalDeviceSubgroupProperties *)cur;
                p->subgroupSize = advertised_subgroup_size();
                p->supportedStages = VK_SHADER_STAGE_COMPUTE_BIT;
                p->supportedOperations = advertised_subgroup_operations();
                p->quadOperationsInAllStages = VK_FALSE;
                break;
            }
            case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DRIVER_PROPERTIES: {
                VkPhysicalDeviceDriverProperties *p = (VkPhysicalDeviceDriverProperties *)cur;
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
                VkPhysicalDeviceVulkan11Properties *p = (VkPhysicalDeviceVulkan11Properties *)cur;
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
                VkPhysicalDeviceVulkan12Properties *p = (VkPhysicalDeviceVulkan12Properties *)cur;
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
    }
}

static void fill_physical_device_features(VkPhysicalDeviceFeatures *pFeatures) {
    if (!pFeatures) return;
    memset(pFeatures, 0, sizeof(*pFeatures));
    pFeatures->shaderInt64 = advertised_shader_int64();
}

static void fill_pnext_features(void *pNext) {
    for (VkBaseOutStructure *cur = (VkBaseOutStructure *)pNext; cur; cur = cur->pNext) {
        switch (cur->sType) {
            case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_1_FEATURES: {
                VkPhysicalDeviceVulkan11Features *p = (VkPhysicalDeviceVulkan11Features *)cur;
                VkBool32 storage16 = advertised_storage16();
                p->storageBuffer16BitAccess = storage16;
                p->uniformAndStorageBuffer16BitAccess = storage16;
                p->storagePushConstant16 = storage16;
                p->storageInputOutput16 = VK_FALSE;
                break;
            }
            case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_16BIT_STORAGE_FEATURES: {
                VkPhysicalDevice16BitStorageFeatures *p = (VkPhysicalDevice16BitStorageFeatures *)cur;
                VkBool32 storage16 = advertised_storage16();
                p->storageBuffer16BitAccess = storage16;
                p->uniformAndStorageBuffer16BitAccess = storage16;
                p->storagePushConstant16 = storage16;
                p->storageInputOutput16 = VK_FALSE;
                break;
            }
            case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES: {
                VkPhysicalDeviceVulkan12Features *p = (VkPhysicalDeviceVulkan12Features *)cur;
                VkBool32 storage8 = advertised_storage8();
                p->storageBuffer8BitAccess = storage8;
                p->uniformAndStorageBuffer8BitAccess = VK_FALSE;
                p->storagePushConstant8 = VK_FALSE;
                p->shaderFloat16 = VK_FALSE;
                p->shaderInt8 = storage8;
                p->bufferDeviceAddress = VK_FALSE;
                p->vulkanMemoryModel = VK_FALSE;
                break;
            }
            case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_8BIT_STORAGE_FEATURES: {
                VkPhysicalDevice8BitStorageFeatures *p = (VkPhysicalDevice8BitStorageFeatures *)cur;
                p->storageBuffer8BitAccess = advertised_storage8();
                p->uniformAndStorageBuffer8BitAccess = VK_FALSE;
                p->storagePushConstant8 = VK_FALSE;
                break;
            }
            case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SHADER_FLOAT16_INT8_FEATURES: {
                VkPhysicalDeviceShaderFloat16Int8Features *p = (VkPhysicalDeviceShaderFloat16Int8Features *)cur;
                p->shaderFloat16 = VK_FALSE;
                p->shaderInt8 = advertised_storage8();
                break;
            }
#ifdef VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MAINTENANCE_4_FEATURES
            case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MAINTENANCE_4_FEATURES: {
                VkPhysicalDeviceMaintenance4Features *p = (VkPhysicalDeviceMaintenance4Features *)cur;
                p->maintenance4 = VK_TRUE;
                break;
            }
#endif
            default:
                break;
        }
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

static uint64_t requested_feature_mask_from_device_create_info(
        const VkDeviceCreateInfo *pCreateInfo) {
    if (!pCreateInfo) return 0;
    uint64_t mask = feature_mask_from_base_features(pCreateInfo->pEnabledFeatures);
    for (const VkBaseInStructure *cur = (const VkBaseInStructure *)pCreateInfo->pNext;
         cur;
         cur = cur->pNext) {
        switch (cur->sType) {
            case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2: {
                const VkPhysicalDeviceFeatures2 *p = (const VkPhysicalDeviceFeatures2 *)cur;
                mask |= feature_mask_from_base_features(&p->features);
                break;
            }
            case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_1_FEATURES: {
                const VkPhysicalDeviceVulkan11Features *p = (const VkPhysicalDeviceVulkan11Features *)cur;
                if (p->storageBuffer16BitAccess) mask |= PDOCKER_VK_FEATURE_STORAGE_BUFFER_16;
                if (p->uniformAndStorageBuffer16BitAccess) mask |= PDOCKER_VK_FEATURE_UNIFORM_STORAGE_BUFFER_16;
                if (p->storagePushConstant16) mask |= PDOCKER_VK_FEATURE_STORAGE_PUSH_CONSTANT_16;
                break;
            }
            case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_16BIT_STORAGE_FEATURES: {
                const VkPhysicalDevice16BitStorageFeatures *p = (const VkPhysicalDevice16BitStorageFeatures *)cur;
                if (p->storageBuffer16BitAccess) mask |= PDOCKER_VK_FEATURE_STORAGE_BUFFER_16;
                if (p->uniformAndStorageBuffer16BitAccess) mask |= PDOCKER_VK_FEATURE_UNIFORM_STORAGE_BUFFER_16;
                if (p->storagePushConstant16) mask |= PDOCKER_VK_FEATURE_STORAGE_PUSH_CONSTANT_16;
                break;
            }
            case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES: {
                const VkPhysicalDeviceVulkan12Features *p = (const VkPhysicalDeviceVulkan12Features *)cur;
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
                const VkPhysicalDevice8BitStorageFeatures *p = (const VkPhysicalDevice8BitStorageFeatures *)cur;
                if (p->storageBuffer8BitAccess) mask |= PDOCKER_VK_FEATURE_STORAGE_BUFFER_8;
                if (p->uniformAndStorageBuffer8BitAccess) mask |= PDOCKER_VK_FEATURE_UNIFORM_STORAGE_BUFFER_8;
                if (p->storagePushConstant8) mask |= PDOCKER_VK_FEATURE_STORAGE_PUSH_CONSTANT_8;
                break;
            }
            case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SHADER_FLOAT16_INT8_FEATURES: {
                const VkPhysicalDeviceShaderFloat16Int8Features *p = (const VkPhysicalDeviceShaderFloat16Int8Features *)cur;
                if (p->shaderFloat16) mask |= PDOCKER_VK_FEATURE_SHADER_FLOAT16;
                if (p->shaderInt8) mask |= PDOCKER_VK_FEATURE_SHADER_INT8;
                break;
            }
#ifdef VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MAINTENANCE_4_FEATURES
            case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MAINTENANCE_4_FEATURES: {
                const VkPhysicalDeviceMaintenance4Features *p = (const VkPhysicalDeviceMaintenance4Features *)cur;
                if (p->maintenance4) mask |= PDOCKER_VK_FEATURE_MAINTENANCE_4;
                break;
            }
#endif
            default:
                break;
        }
    }
    return mask;
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
    for (const VkBaseInStructure *cur = (const VkBaseInStructure *)pCreateInfo->pNext;
         cur;
         cur = cur->pNext) {
        switch (cur->sType) {
            case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2: {
                const VkPhysicalDeviceFeatures2 *p = (const VkPhysicalDeviceFeatures2 *)cur;
                fprintf(stderr,
                        "pdocker-vulkan-icd: create-device features2={shaderInt64:%u,shaderInt16:%u,shaderFloat64:%u}\n",
                        p->features.shaderInt64,
                        p->features.shaderInt16,
                        p->features.shaderFloat64);
                break;
            }
            case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_1_FEATURES: {
                const VkPhysicalDeviceVulkan11Features *p = (const VkPhysicalDeviceVulkan11Features *)cur;
                fprintf(stderr,
                        "pdocker-vulkan-icd: create-device vk11_features={storage16:%u,ubo_ssbo16:%u,push16:%u,io16:%u}\n",
                        p->storageBuffer16BitAccess,
                        p->uniformAndStorageBuffer16BitAccess,
                        p->storagePushConstant16,
                        p->storageInputOutput16);
                break;
            }
            case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_16BIT_STORAGE_FEATURES: {
                const VkPhysicalDevice16BitStorageFeatures *p = (const VkPhysicalDevice16BitStorageFeatures *)cur;
                fprintf(stderr,
                        "pdocker-vulkan-icd: create-device storage16_features={storage16:%u,ubo_ssbo16:%u,push16:%u,io16:%u}\n",
                        p->storageBuffer16BitAccess,
                        p->uniformAndStorageBuffer16BitAccess,
                        p->storagePushConstant16,
                        p->storageInputOutput16);
                break;
            }
            case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES: {
                const VkPhysicalDeviceVulkan12Features *p = (const VkPhysicalDeviceVulkan12Features *)cur;
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
                const VkPhysicalDevice8BitStorageFeatures *p = (const VkPhysicalDevice8BitStorageFeatures *)cur;
                fprintf(stderr,
                        "pdocker-vulkan-icd: create-device storage8_features={storage8:%u,ubo_ssbo8:%u,push8:%u}\n",
                        p->storageBuffer8BitAccess,
                        p->uniformAndStorageBuffer8BitAccess,
                        p->storagePushConstant8);
                break;
            }
            case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SHADER_FLOAT16_INT8_FEATURES: {
                const VkPhysicalDeviceShaderFloat16Int8Features *p = (const VkPhysicalDeviceShaderFloat16Int8Features *)cur;
                fprintf(stderr,
                        "pdocker-vulkan-icd: create-device float16_int8_features={float16:%u,int8:%u}\n",
                        p->shaderFloat16,
                        p->shaderInt8);
                break;
            }
            default:
                fprintf(stderr, "pdocker-vulkan-icd: create-device pnext sType=%d\n", (int)cur->sType);
                break;
        }
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
    for (VkBaseOutStructure *base = (VkBaseOutStructure *)pMemoryRequirements->pNext;
         base;
         base = base->pNext) {
        if (base->sType == VK_STRUCTURE_TYPE_MEMORY_DEDICATED_REQUIREMENTS) {
            VkMemoryDedicatedRequirements *dedicated = (VkMemoryDedicatedRequirements *)base;
            dedicated->prefersDedicatedAllocation = VK_FALSE;
            dedicated->requiresDedicatedAllocation = VK_FALSE;
            if (trace_allocations()) {
                fprintf(stderr,
                        "pdocker-vulkan-icd: memory-requirements2 dedicated prefers=0 requires=0\n");
            }
        } else if (trace_allocations()) {
            fprintf(stderr,
                    "pdocker-vulkan-icd: memory-requirements2 ignored pnext sType=%d\n",
                    (int)base->sType);
        }
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
    const VkExtensionProperties available[] = {
        { VK_KHR_16BIT_STORAGE_EXTENSION_NAME, VK_KHR_16BIT_STORAGE_SPEC_VERSION },
#ifdef VK_KHR_MAINTENANCE_4_EXTENSION_NAME
        { VK_KHR_MAINTENANCE_4_EXTENSION_NAME, VK_KHR_MAINTENANCE_4_SPEC_VERSION },
#endif
    };
    copy_extension_properties(available, (uint32_t)(sizeof(available) / sizeof(available[0])), pPropertyCount, pProperties);
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
    trace_device_create_features(pCreateInfo);
    PdockerVkDevice *device = calloc(1, sizeof(*device));
    if (!device) return VK_ERROR_OUT_OF_HOST_MEMORY;
    device->requested_feature_mask = requested_feature_mask_from_device_create_info(pCreateInfo);
    if (trace_allocations() || getenv("PDOCKER_VULKAN_ICD_DEBUG")) {
        fprintf(stderr,
                "pdocker-vulkan-icd: create-device requested_feature_mask=0x%016llx\n",
                (unsigned long long)device->requested_feature_mask);
    }
    set_loader_magic_value(device);
    *pDevice = (VkDevice)device;
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
        if ((binding->descriptorType == VK_DESCRIPTOR_TYPE_STORAGE_BUFFER ||
             binding->descriptorType == VK_DESCRIPTOR_TYPE_STORAGE_BUFFER_DYNAMIC) &&
            binding->binding < PDOCKER_VK_MAX_STORAGE_BUFFERS &&
            binding->binding + 1 > layout->storage_binding_count) {
            layout->storage_binding_count = binding->binding + 1;
        }
        if ((binding->descriptorType == VK_DESCRIPTOR_TYPE_STORAGE_BUFFER ||
             binding->descriptorType == VK_DESCRIPTOR_TYPE_STORAGE_BUFFER_DYNAMIC) &&
            binding->binding < PDOCKER_VK_MAX_STORAGE_BUFFERS) {
            layout->storage_binding_types[binding->binding] = binding->descriptorType;
            layout->storage_binding_counts[binding->binding] = binding->descriptorCount;
            if ((trace_allocations() ||
                 env_truthy_default("PDOCKER_GPU_DISPATCH_PROFILE_LOG", false)) &&
                binding->descriptorCount > 1) {
                fprintf(stderr,
                        "pdocker-vulkan-icd: descriptor array layout binding=%u count=%u type=%u flattened_capacity=%u\n",
                        binding->binding,
                        binding->descriptorCount,
                        binding->descriptorType,
                        PDOCKER_VK_MAX_STORAGE_BUFFERS);
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
        if (!set ||
            (w->descriptorType != VK_DESCRIPTOR_TYPE_STORAGE_BUFFER &&
             w->descriptorType != VK_DESCRIPTOR_TYPE_STORAGE_BUFFER_DYNAMIC) ||
            !w->pBufferInfo) continue;
        for (uint32_t j = 0; j < w->descriptorCount; ++j) {
            uint32_t binding = w->dstBinding + w->dstArrayElement + j;
            if (binding < PDOCKER_VK_MAX_STORAGE_BUFFERS) {
                set->storage_buffers[binding].buffer = (PdockerVkBuffer *)w->pBufferInfo[j].buffer;
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
        for (uint32_t j = 0; j < c->descriptorCount; ++j) {
            uint32_t src_binding = c->srcBinding + c->srcArrayElement + j;
            uint32_t dst_binding = c->dstBinding + c->dstArrayElement + j;
            if (src_binding >= PDOCKER_VK_MAX_STORAGE_BUFFERS ||
                dst_binding >= PDOCKER_VK_MAX_STORAGE_BUFFERS) {
                continue;
            }
            dst->storage_buffers[dst_binding] = src->storage_buffers[src_binding];
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

VKAPI_ATTR VkResult VKAPI_CALL vkQueueSubmit(
        VkQueue queue,
        uint32_t submitCount,
        const VkSubmitInfo *pSubmits,
        VkFence fence) {
    (void)queue;
    (void)fence;
    for (uint32_t i = 0; i < submitCount; ++i) {
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
    }
    PdockerVkFence *f = (PdockerVkFence *)fence;
    if (f) f->signaled = true;
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
    (void)pCreateInfo;
    (void)pAllocator;
    if (!pSemaphore) return VK_ERROR_INITIALIZATION_FAILED;
    *pSemaphore = (VkSemaphore)pdocker_alloc_handle(sizeof(PdockerHandle));
    return *pSemaphore ? VK_SUCCESS : VK_ERROR_OUT_OF_HOST_MEMORY;
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
    MAP_PROC(vkGetPhysicalDeviceFeatures);
    MAP_PROC(vkGetPhysicalDeviceFeatures2);
    MAP_PROC(vkGetPhysicalDeviceFormatProperties);
    MAP_PROC(vkGetPhysicalDeviceImageFormatProperties);
    MAP_PROC(vkGetPhysicalDeviceSparseImageFormatProperties);
    MAP_PROC(vkGetPhysicalDeviceQueueFamilyProperties);
    MAP_PROC(vkGetPhysicalDeviceQueueFamilyProperties2);
    MAP_PROC(vkGetPhysicalDeviceMemoryProperties);
    MAP_PROC(vkGetPhysicalDeviceMemoryProperties2);
    MAP_PROC(vkEnumerateDeviceExtensionProperties);
    MAP_PROC(vkEnumerateDeviceLayerProperties);
    MAP_PROC(vkCreateDevice);
    MAP_PROC(vkDestroyDevice);
    MAP_PROC(vkGetDeviceQueue);
    MAP_PROC(vkGetDeviceQueue2);
    MAP_PROC(vkCreateBuffer);
    MAP_PROC(vkDestroyBuffer);
    MAP_PROC(vkGetBufferMemoryRequirements);
    MAP_PROC(vkGetBufferMemoryRequirements2);
    MAP_PROC(vkAllocateMemory);
    MAP_PROC(vkFreeMemory);
    MAP_PROC(vkMapMemory);
    MAP_PROC(vkUnmapMemory);
    MAP_PROC(vkGetDeviceMemoryCommitment);
    MAP_PROC(vkFlushMappedMemoryRanges);
    MAP_PROC(vkInvalidateMappedMemoryRanges);
    MAP_PROC(vkBindBufferMemory);
    MAP_PROC(vkBindBufferMemory2);
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
    MAP_PROC(vkCmdFillBuffer);
    MAP_PROC(vkCmdUpdateBuffer);
    MAP_PROC(vkCmdDispatch);
    MAP_PROC(vkQueueSubmit);
    MAP_PROC(vkQueueWaitIdle);
    MAP_PROC(vkDeviceWaitIdle);
    MAP_PROC(vkCreateFence);
    MAP_PROC(vkDestroyFence);
    MAP_PROC(vkResetFences);
    MAP_PROC(vkGetFenceStatus);
    MAP_PROC(vkWaitForFences);
    MAP_PROC(vkCreateSemaphore);
    MAP_PROC(vkDestroySemaphore);
    MAP_PROC(vk_icdNegotiateLoaderICDInterfaceVersion);
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

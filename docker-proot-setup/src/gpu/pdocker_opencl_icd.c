/*
 * Minimal glibc-facing pdocker OpenCL shim.
 *
 * This intentionally does not load Android/Bionic libOpenCL from a glibc
 * container. It exposes enough OpenCL 1.2-style surface to prove the standard
 * app entry point can lower compute work into the pdocker GPU command queue.
 */
#include "pdocker_gpu_abi.h"

#include <errno.h>
#include <fcntl.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/syscall.h>
#include <sys/un.h>
#include <unistd.h>

#ifndef MFD_CLOEXEC
#define MFD_CLOEXEC 0x0001U
#endif

typedef int32_t cl_int;
typedef uint32_t cl_uint;
typedef uint64_t cl_ulong;
typedef cl_ulong cl_bitfield;
typedef cl_bitfield cl_device_type;
typedef cl_bitfield cl_mem_flags;
typedef cl_uint cl_bool;
typedef intptr_t cl_context_properties;
typedef intptr_t cl_queue_properties;

typedef struct PdockerClPlatform *cl_platform_id;
typedef struct PdockerClDevice *cl_device_id;
typedef struct PdockerClContext *cl_context;
typedef struct PdockerClQueue *cl_command_queue;
typedef struct PdockerClMem *cl_mem;
typedef struct PdockerClProgram *cl_program;
typedef struct PdockerClKernel *cl_kernel;
typedef struct PdockerClEvent *cl_event;

#define CL_SUCCESS 0
#define CL_DEVICE_NOT_FOUND -1
#define CL_OUT_OF_HOST_MEMORY -6
#define CL_INVALID_VALUE -30
#define CL_INVALID_DEVICE -33
#define CL_INVALID_CONTEXT -34
#define CL_INVALID_MEM_OBJECT -38
#define CL_INVALID_KERNEL -48
#define CL_INVALID_ARG_INDEX -49
#define CL_INVALID_OPERATION -59

#define CL_TRUE 1

#define CL_DEVICE_TYPE_DEFAULT (1u << 0)
#define CL_DEVICE_TYPE_GPU (1u << 2)
#define CL_DEVICE_TYPE_ALL 0xFFFFFFFFu

#define CL_MEM_READ_WRITE (1u << 0)
#define CL_MEM_COPY_HOST_PTR (1u << 5)

#define CL_PLATFORM_PROFILE 0x0900
#define CL_PLATFORM_VERSION 0x0901
#define CL_PLATFORM_NAME 0x0902
#define CL_PLATFORM_VENDOR 0x0903
#define CL_PLATFORM_EXTENSIONS 0x0904

#define CL_DEVICE_TYPE 0x1000
#define CL_DEVICE_VENDOR_ID 0x1001
#define CL_DEVICE_MAX_COMPUTE_UNITS 0x1002
#define CL_DEVICE_MAX_WORK_ITEM_DIMENSIONS 0x1003
#define CL_DEVICE_MAX_WORK_GROUP_SIZE 0x1004
#define CL_DEVICE_MAX_WORK_ITEM_SIZES 0x1005
#define CL_DEVICE_GLOBAL_MEM_SIZE 0x101F
#define CL_DEVICE_LOCAL_MEM_SIZE 0x1023
#define CL_DEVICE_NAME 0x102B
#define CL_DEVICE_VENDOR 0x102C
#define CL_DRIVER_VERSION 0x102D
#define CL_DEVICE_PROFILE 0x102E
#define CL_DEVICE_VERSION 0x102F
#define CL_DEVICE_EXTENSIONS 0x1030
#define CL_DEVICE_OPENCL_C_VERSION 0x103D

#define CL_CONTEXT_REFERENCE_COUNT 0x1080
#define CL_CONTEXT_NUM_DEVICES 0x1083
#define CL_CONTEXT_DEVICES 0x1081

#define CL_QUEUE_CONTEXT 0x1090
#define CL_QUEUE_DEVICE 0x1091
#define CL_QUEUE_REFERENCE_COUNT 0x1092

#define CL_MEM_SIZE 0x1102
#define CL_MEM_HOST_PTR 0x1103

#define CL_PROGRAM_REFERENCE_COUNT 0x1160
#define CL_PROGRAM_CONTEXT 0x1161
#define CL_PROGRAM_NUM_DEVICES 0x1162
#define CL_PROGRAM_DEVICES 0x1163
#define CL_PROGRAM_SOURCE 0x1164

#define CL_KERNEL_FUNCTION_NAME 0x1190
#define CL_KERNEL_NUM_ARGS 0x1191
#define CL_KERNEL_REFERENCE_COUNT 0x1192
#define CL_KERNEL_CONTEXT 0x1193
#define CL_KERNEL_PROGRAM 0x1194
#define CL_KERNEL_WORK_GROUP_SIZE 0x11B0
#define CL_KERNEL_COMPILE_WORK_GROUP_SIZE 0x11B1

#define CL_PROGRAM_BUILD_LOG 0x1183

struct PdockerClPlatform {
    const char *name;
};

struct PdockerClDevice {
    const char *name;
};

struct PdockerClContext {
    cl_device_id device;
};

struct PdockerClQueue {
    cl_context context;
    cl_device_id device;
};

struct PdockerClMem {
    size_t size;
    int fd;
    void *map;
};

struct PdockerClProgram {
    cl_context context;
    char *source;
};

struct PdockerClKernel {
    char *name;
    cl_program program;
    cl_mem mem_args[16];
    unsigned char scalar_args[16][32];
    size_t arg_sizes[16];
};

struct PdockerClEvent {
    int complete;
};

static struct PdockerClPlatform g_platform = { "pdocker OpenCL bridge" };
static struct PdockerClDevice g_device = { "pdocker GPU bridge (OpenCL)" };

static void set_error(cl_int *errcode_ret, cl_int value) {
    if (errcode_ret) *errcode_ret = value;
}

static cl_int copy_info(const void *src, size_t src_size, size_t dst_size, void *dst, size_t *size_ret) {
    if (size_ret) *size_ret = src_size;
    if (!dst) return CL_SUCCESS;
    if (dst_size < src_size) return CL_INVALID_VALUE;
    memcpy(dst, src, src_size);
    return CL_SUCCESS;
}

static cl_int copy_string_info(const char *value, size_t dst_size, void *dst, size_t *size_ret) {
    return copy_info(value, strlen(value) + 1, dst_size, dst, size_ret);
}

static bool buffer_range_valid(cl_mem buffer, size_t offset, size_t cb) {
    return buffer && offset <= buffer->size && cb <= buffer->size - offset;
}

static int create_shared_fd(size_t bytes) {
#ifdef __NR_memfd_create
    int memfd = (int)syscall(__NR_memfd_create, "pdocker-opencl-buffer", MFD_CLOEXEC);
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
    snprintf(path, sizeof(path), "%s/pdocker-opencl-buffer-XXXXXX", dir);
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

static int connect_queue(void) {
    const char *path = getenv("PDOCKER_GPU_QUEUE_SOCKET");
    if (!path || !path[0]) return -ENOENT;
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
    char cmd[80];
    snprintf(cmd, sizeof(cmd), "OPENCL_VECTOR_ADD_3FD %zu\n", n);
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
        if (getenv("PDOCKER_OPENCL_ICD_DEBUG")) {
            fprintf(stderr, "pdocker-opencl-icd: bridge response: %s", line);
            if (off == 0 || line[off - 1] != '\n') fprintf(stderr, "\n");
        }
        if (strstr(line, "\"valid\":true") == NULL) rc = -EIO;
    }
    close(socket_fd);
    return rc;
}

static void trace_unsupported_kernel(const cl_kernel kernel) {
    const char *path = getenv("PDOCKER_OPENCL_ICD_TRACE");
    if (!path || !path[0] || !kernel) return;
    FILE *f = fopen(path, "a");
    if (!f) return;
    fprintf(f, "unsupported kernel name=%s\n", kernel->name ? kernel->name : "");
    if (kernel->program && kernel->program->source) {
        fprintf(f, "source-begin\n%s\nsource-end\n", kernel->program->source);
    }
    fclose(f);
}

static bool source_looks_like_vector_add(const char *source) {
    if (!source) return false;
    return strstr(source, "+ b[") != NULL ||
           strstr(source, "+b[") != NULL ||
           strstr(source, "a[i] + b[i]") != NULL ||
           strstr(source, "a[i]+b[i]") != NULL;
}

static bool kernel_is_supported_vector_add(const cl_kernel kernel) {
    if (!kernel || !kernel->program) return false;
    if (!kernel->mem_args[0] || !kernel->mem_args[1] || !kernel->mem_args[2]) return false;
    if (kernel->name && strstr(kernel->name, "add") != NULL) return source_looks_like_vector_add(kernel->program->source);
    return source_looks_like_vector_add(kernel->program->source);
}

static bool env_is(const char *name, const char *value) {
    const char *v = getenv(name);
    return v && strcmp(v, value) == 0;
}

static size_t env_size_or(const char *name, size_t fallback) {
    const char *v = getenv(name);
    if (!v || !v[0]) return fallback;
    char *end = NULL;
    unsigned long long parsed = strtoull(v, &end, 10);
    if (!end || *end || parsed == 0) return fallback;
    return (size_t)parsed;
}

static bool cpu_governor_enabled(size_t n) {
    if (env_is("PDOCKER_GPU_GOVERNOR", "force-gpu") ||
        env_is("PDOCKER_GPU_GOVERNOR", "gpu") ||
        env_is("PDOCKER_GPU_GOVERNOR", "bridge")) {
        return false;
    }
    if (env_is("PDOCKER_GPU_GOVERNOR", "force-cpu") ||
        env_is("PDOCKER_GPU_GOVERNOR", "cpu") ||
        env_is("PDOCKER_GPU_GOVERNOR", "cpu-emulated")) {
        return true;
    }
    size_t max_n = env_size_or(
        "PDOCKER_GPU_CPU_FALLBACK_MAX_VECTOR_ADD_N",
        PDOCKER_GPU_VECTOR_ADD_DEFAULT_N);
    return n <= max_n;
}

static cl_int run_vector_add_cpu_emulated(cl_mem a, cl_mem b, cl_mem out, size_t n) {
    if (!a || !b || !out || !a->map || !b->map || !out->map) return CL_INVALID_OPERATION;
    const float *av = (const float *)a->map;
    const float *bv = (const float *)b->map;
    float *ov = (float *)out->map;
    for (size_t i = 0; i < n; ++i) ov[i] = av[i] + bv[i];
    if (getenv("PDOCKER_OPENCL_ICD_DEBUG")) {
        fprintf(stderr,
                "pdocker-opencl-icd: vector_add CPU-emulated n=%zu reason=governor\n",
                n);
    }
    return CL_SUCCESS;
}

cl_int clGetPlatformIDs(cl_uint num_entries, cl_platform_id *platforms, cl_uint *num_platforms) {
    if (num_platforms) *num_platforms = 1;
    if (platforms && num_entries > 0) platforms[0] = &g_platform;
    return CL_SUCCESS;
}

cl_int clIcdGetPlatformIDsKHR(cl_uint num_entries, cl_platform_id *platforms, cl_uint *num_platforms) {
    return clGetPlatformIDs(num_entries, platforms, num_platforms);
}

cl_int clGetPlatformInfo(cl_platform_id platform, cl_uint param_name, size_t param_value_size, void *param_value, size_t *param_value_size_ret) {
    if (platform != &g_platform) return CL_INVALID_VALUE;
    switch (param_name) {
        case CL_PLATFORM_PROFILE: return copy_string_info("FULL_PROFILE", param_value_size, param_value, param_value_size_ret);
        case CL_PLATFORM_VERSION: return copy_string_info("OpenCL 1.2 pdocker", param_value_size, param_value, param_value_size_ret);
        case CL_PLATFORM_NAME: return copy_string_info(g_platform.name, param_value_size, param_value, param_value_size_ret);
        case CL_PLATFORM_VENDOR: return copy_string_info("pdocker", param_value_size, param_value, param_value_size_ret);
        case CL_PLATFORM_EXTENSIONS: return copy_string_info("cl_khr_icd", param_value_size, param_value, param_value_size_ret);
        default: return CL_INVALID_VALUE;
    }
}

cl_int clGetDeviceIDs(cl_platform_id platform, cl_device_type device_type, cl_uint num_entries, cl_device_id *devices, cl_uint *num_devices) {
    if (platform != &g_platform) return CL_INVALID_VALUE;
    if (!(device_type & (CL_DEVICE_TYPE_GPU | CL_DEVICE_TYPE_DEFAULT | CL_DEVICE_TYPE_ALL))) return CL_DEVICE_NOT_FOUND;
    if (num_devices) *num_devices = 1;
    if (devices && num_entries > 0) devices[0] = &g_device;
    return CL_SUCCESS;
}

cl_int clGetDeviceInfo(cl_device_id device, cl_uint param_name, size_t param_value_size, void *param_value, size_t *param_value_size_ret) {
    if (device != &g_device) return CL_INVALID_DEVICE;
    cl_device_type type = CL_DEVICE_TYPE_GPU;
    cl_uint u32 = 1;
    cl_ulong u64 = 256ull * 1024ull * 1024ull;
    size_t sizes[3] = { 128, 1, 1 };
    size_t wg = 128;
    switch (param_name) {
        case CL_DEVICE_TYPE: return copy_info(&type, sizeof(type), param_value_size, param_value, param_value_size_ret);
        case CL_DEVICE_VENDOR_ID: u32 = 0x5044; return copy_info(&u32, sizeof(u32), param_value_size, param_value, param_value_size_ret);
        case CL_DEVICE_MAX_COMPUTE_UNITS: u32 = 1; return copy_info(&u32, sizeof(u32), param_value_size, param_value, param_value_size_ret);
        case CL_DEVICE_MAX_WORK_ITEM_DIMENSIONS: u32 = 3; return copy_info(&u32, sizeof(u32), param_value_size, param_value, param_value_size_ret);
        case CL_DEVICE_MAX_WORK_GROUP_SIZE: return copy_info(&wg, sizeof(wg), param_value_size, param_value, param_value_size_ret);
        case CL_DEVICE_MAX_WORK_ITEM_SIZES: return copy_info(sizes, sizeof(sizes), param_value_size, param_value, param_value_size_ret);
        case CL_DEVICE_GLOBAL_MEM_SIZE: return copy_info(&u64, sizeof(u64), param_value_size, param_value, param_value_size_ret);
        case CL_DEVICE_LOCAL_MEM_SIZE: u64 = 32768; return copy_info(&u64, sizeof(u64), param_value_size, param_value, param_value_size_ret);
        case CL_DEVICE_NAME: return copy_string_info(g_device.name, param_value_size, param_value, param_value_size_ret);
        case CL_DEVICE_VENDOR: return copy_string_info("pdocker", param_value_size, param_value, param_value_size_ret);
        case CL_DRIVER_VERSION: return copy_string_info("0.1", param_value_size, param_value, param_value_size_ret);
        case CL_DEVICE_PROFILE: return copy_string_info("FULL_PROFILE", param_value_size, param_value, param_value_size_ret);
        case CL_DEVICE_VERSION: return copy_string_info("OpenCL 1.2 pdocker", param_value_size, param_value, param_value_size_ret);
        case CL_DEVICE_OPENCL_C_VERSION: return copy_string_info("OpenCL C 1.2", param_value_size, param_value, param_value_size_ret);
        case CL_DEVICE_EXTENSIONS: return copy_string_info("", param_value_size, param_value, param_value_size_ret);
        default: return CL_INVALID_VALUE;
    }
}

cl_context clCreateContext(const cl_context_properties *properties, cl_uint num_devices, const cl_device_id *devices, void (*pfn_notify)(const char *, const void *, size_t, void *), void *user_data, cl_int *errcode_ret) {
    (void)properties;
    (void)pfn_notify;
    (void)user_data;
    if (num_devices == 0 || !devices || devices[0] != &g_device) {
        set_error(errcode_ret, CL_INVALID_DEVICE);
        return NULL;
    }
    cl_context ctx = calloc(1, sizeof(*ctx));
    if (!ctx) {
        set_error(errcode_ret, CL_OUT_OF_HOST_MEMORY);
        return NULL;
    }
    ctx->device = devices[0];
    set_error(errcode_ret, CL_SUCCESS);
    return ctx;
}

cl_int clReleaseContext(cl_context context) {
    free(context);
    return CL_SUCCESS;
}

cl_int clRetainContext(cl_context context) {
    return context ? CL_SUCCESS : CL_INVALID_CONTEXT;
}

cl_int clGetContextInfo(cl_context context, cl_uint param_name, size_t param_value_size, void *param_value, size_t *param_value_size_ret) {
    if (!context) return CL_INVALID_CONTEXT;
    cl_uint u32 = 1;
    switch (param_name) {
        case CL_CONTEXT_REFERENCE_COUNT: return copy_info(&u32, sizeof(u32), param_value_size, param_value, param_value_size_ret);
        case CL_CONTEXT_NUM_DEVICES: return copy_info(&u32, sizeof(u32), param_value_size, param_value, param_value_size_ret);
        case CL_CONTEXT_DEVICES: return copy_info(&context->device, sizeof(context->device), param_value_size, param_value, param_value_size_ret);
        default: return CL_INVALID_VALUE;
    }
}

cl_command_queue clCreateCommandQueue(cl_context context, cl_device_id device, cl_bitfield properties, cl_int *errcode_ret) {
    (void)properties;
    if (!context || device != &g_device) {
        set_error(errcode_ret, CL_INVALID_VALUE);
        return NULL;
    }
    cl_command_queue q = calloc(1, sizeof(*q));
    if (!q) {
        set_error(errcode_ret, CL_OUT_OF_HOST_MEMORY);
        return NULL;
    }
    q->context = context;
    q->device = device;
    set_error(errcode_ret, CL_SUCCESS);
    return q;
}

cl_command_queue clCreateCommandQueueWithProperties(cl_context context, cl_device_id device, const cl_queue_properties *properties, cl_int *errcode_ret) {
    (void)properties;
    return clCreateCommandQueue(context, device, 0, errcode_ret);
}

cl_int clReleaseCommandQueue(cl_command_queue command_queue) {
    free(command_queue);
    return CL_SUCCESS;
}

cl_int clRetainCommandQueue(cl_command_queue command_queue) {
    return command_queue ? CL_SUCCESS : CL_INVALID_VALUE;
}

cl_int clGetCommandQueueInfo(cl_command_queue command_queue, cl_uint param_name, size_t param_value_size, void *param_value, size_t *param_value_size_ret) {
    if (!command_queue) return CL_INVALID_VALUE;
    cl_uint u32 = 1;
    switch (param_name) {
        case CL_QUEUE_CONTEXT: return copy_info(&command_queue->context, sizeof(command_queue->context), param_value_size, param_value, param_value_size_ret);
        case CL_QUEUE_DEVICE: return copy_info(&command_queue->device, sizeof(command_queue->device), param_value_size, param_value, param_value_size_ret);
        case CL_QUEUE_REFERENCE_COUNT: return copy_info(&u32, sizeof(u32), param_value_size, param_value, param_value_size_ret);
        default: return CL_INVALID_VALUE;
    }
}

cl_mem clCreateBuffer(cl_context context, cl_mem_flags flags, size_t size, void *host_ptr, cl_int *errcode_ret) {
    (void)context;
    if (size == 0) {
        set_error(errcode_ret, CL_INVALID_VALUE);
        return NULL;
    }
    cl_mem mem = calloc(1, sizeof(*mem));
    if (!mem) {
        set_error(errcode_ret, CL_OUT_OF_HOST_MEMORY);
        return NULL;
    }
    mem->size = size;
    mem->fd = create_shared_fd(size);
    if (mem->fd < 0) {
        free(mem);
        set_error(errcode_ret, CL_OUT_OF_HOST_MEMORY);
        return NULL;
    }
    mem->map = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_SHARED, mem->fd, 0);
    if (mem->map == MAP_FAILED) {
        close(mem->fd);
        free(mem);
        set_error(errcode_ret, CL_OUT_OF_HOST_MEMORY);
        return NULL;
    }
    if ((flags & CL_MEM_COPY_HOST_PTR) && host_ptr) memcpy(mem->map, host_ptr, size);
    set_error(errcode_ret, CL_SUCCESS);
    return mem;
}

cl_int clReleaseMemObject(cl_mem memobj) {
    if (!memobj) return CL_INVALID_MEM_OBJECT;
    if (memobj->map && memobj->map != MAP_FAILED) munmap(memobj->map, memobj->size);
    if (memobj->fd >= 0) close(memobj->fd);
    free(memobj);
    return CL_SUCCESS;
}

cl_int clRetainMemObject(cl_mem memobj) {
    return memobj ? CL_SUCCESS : CL_INVALID_MEM_OBJECT;
}

cl_int clGetMemObjectInfo(cl_mem memobj, cl_uint param_name, size_t param_value_size, void *param_value, size_t *param_value_size_ret) {
    if (!memobj) return CL_INVALID_MEM_OBJECT;
    switch (param_name) {
        case CL_MEM_SIZE: return copy_info(&memobj->size, sizeof(memobj->size), param_value_size, param_value, param_value_size_ret);
        case CL_MEM_HOST_PTR: return copy_info(&memobj->map, sizeof(memobj->map), param_value_size, param_value, param_value_size_ret);
        default: return CL_INVALID_VALUE;
    }
}

cl_int clEnqueueWriteBuffer(cl_command_queue command_queue, cl_mem buffer, cl_bool blocking_write, size_t offset, size_t cb, const void *ptr, cl_uint num_events_in_wait_list, const cl_event *event_wait_list, cl_event *event) {
    (void)command_queue;
    (void)blocking_write;
    (void)num_events_in_wait_list;
    (void)event_wait_list;
    if (event) *event = NULL;
    if (!buffer_range_valid(buffer, offset, cb) || !ptr) return CL_INVALID_VALUE;
    memcpy((char *)buffer->map + offset, ptr, cb);
    return CL_SUCCESS;
}

cl_int clEnqueueReadBuffer(cl_command_queue command_queue, cl_mem buffer, cl_bool blocking_read, size_t offset, size_t cb, void *ptr, cl_uint num_events_in_wait_list, const cl_event *event_wait_list, cl_event *event) {
    (void)command_queue;
    (void)blocking_read;
    (void)num_events_in_wait_list;
    (void)event_wait_list;
    if (event) *event = NULL;
    if (!buffer_range_valid(buffer, offset, cb) || !ptr) return CL_INVALID_VALUE;
    memcpy(ptr, (char *)buffer->map + offset, cb);
    return CL_SUCCESS;
}

cl_int clEnqueueCopyBuffer(cl_command_queue command_queue, cl_mem src_buffer, cl_mem dst_buffer, size_t src_offset, size_t dst_offset, size_t cb, cl_uint num_events_in_wait_list, const cl_event *event_wait_list, cl_event *event) {
    (void)command_queue;
    (void)num_events_in_wait_list;
    (void)event_wait_list;
    if (event) *event = NULL;
    if (!buffer_range_valid(src_buffer, src_offset, cb) ||
        !buffer_range_valid(dst_buffer, dst_offset, cb)) return CL_INVALID_VALUE;
    memmove((char *)dst_buffer->map + dst_offset, (const char *)src_buffer->map + src_offset, cb);
    return CL_SUCCESS;
}

cl_int clEnqueueFillBuffer(cl_command_queue command_queue, cl_mem buffer, const void *pattern, size_t pattern_size, size_t offset, size_t cb, cl_uint num_events_in_wait_list, const cl_event *event_wait_list, cl_event *event) {
    (void)command_queue;
    (void)num_events_in_wait_list;
    (void)event_wait_list;
    if (event) *event = NULL;
    if (!buffer_range_valid(buffer, offset, cb) || !pattern || pattern_size == 0) return CL_INVALID_VALUE;
    unsigned char *dst = (unsigned char *)buffer->map + offset;
    for (size_t i = 0; i < cb; ++i) dst[i] = ((const unsigned char *)pattern)[i % pattern_size];
    return CL_SUCCESS;
}

void *clEnqueueMapBuffer(cl_command_queue command_queue, cl_mem buffer, cl_bool blocking_map, cl_bitfield map_flags, size_t offset, size_t cb, cl_uint num_events_in_wait_list, const cl_event *event_wait_list, cl_event *event, cl_int *errcode_ret) {
    (void)command_queue;
    (void)blocking_map;
    (void)map_flags;
    (void)num_events_in_wait_list;
    (void)event_wait_list;
    if (event) *event = NULL;
    if (!buffer_range_valid(buffer, offset, cb)) {
        set_error(errcode_ret, CL_INVALID_VALUE);
        return NULL;
    }
    set_error(errcode_ret, CL_SUCCESS);
    return (char *)buffer->map + offset;
}

cl_int clEnqueueUnmapMemObject(cl_command_queue command_queue, cl_mem memobj, void *mapped_ptr, cl_uint num_events_in_wait_list, const cl_event *event_wait_list, cl_event *event) {
    (void)command_queue;
    (void)mapped_ptr;
    (void)num_events_in_wait_list;
    (void)event_wait_list;
    if (event) *event = NULL;
    return memobj ? CL_SUCCESS : CL_INVALID_MEM_OBJECT;
}

cl_program clCreateProgramWithSource(cl_context context, cl_uint count, const char **strings, const size_t *lengths, cl_int *errcode_ret) {
    (void)context;
    if (count == 0 || !strings) {
        set_error(errcode_ret, CL_INVALID_VALUE);
        return NULL;
    }
    size_t total = 0;
    for (cl_uint i = 0; i < count; ++i) total += lengths && lengths[i] ? lengths[i] : strlen(strings[i]);
    cl_program program = calloc(1, sizeof(*program));
    if (!program) {
        set_error(errcode_ret, CL_OUT_OF_HOST_MEMORY);
        return NULL;
    }
    program->context = context;
    program->source = calloc(1, total + 1);
    if (!program->source) {
        free(program);
        set_error(errcode_ret, CL_OUT_OF_HOST_MEMORY);
        return NULL;
    }
    size_t off = 0;
    for (cl_uint i = 0; i < count; ++i) {
        size_t len = lengths && lengths[i] ? lengths[i] : strlen(strings[i]);
        memcpy(program->source + off, strings[i], len);
        off += len;
    }
    set_error(errcode_ret, CL_SUCCESS);
    return program;
}

cl_int clBuildProgram(cl_program program, cl_uint num_devices, const cl_device_id *device_list, const char *options, void (*pfn_notify)(cl_program, void *), void *user_data) {
    (void)num_devices;
    (void)device_list;
    (void)options;
    if (!program) return CL_INVALID_VALUE;
    if (pfn_notify) pfn_notify(program, user_data);
    return CL_SUCCESS;
}

cl_int clGetProgramBuildInfo(cl_program program, cl_device_id device, cl_uint param_name, size_t param_value_size, void *param_value, size_t *param_value_size_ret) {
    (void)program;
    (void)device;
    if (param_name == CL_PROGRAM_BUILD_LOG) return copy_string_info("", param_value_size, param_value, param_value_size_ret);
    return CL_INVALID_VALUE;
}

cl_int clReleaseProgram(cl_program program) {
    if (!program) return CL_INVALID_VALUE;
    free(program->source);
    free(program);
    return CL_SUCCESS;
}

cl_int clRetainProgram(cl_program program) {
    return program ? CL_SUCCESS : CL_INVALID_VALUE;
}

cl_int clGetProgramInfo(cl_program program, cl_uint param_name, size_t param_value_size, void *param_value, size_t *param_value_size_ret) {
    if (!program) return CL_INVALID_VALUE;
    cl_uint u32 = 1;
    switch (param_name) {
        case CL_PROGRAM_REFERENCE_COUNT: return copy_info(&u32, sizeof(u32), param_value_size, param_value, param_value_size_ret);
        case CL_PROGRAM_CONTEXT: return copy_info(&program->context, sizeof(program->context), param_value_size, param_value, param_value_size_ret);
        case CL_PROGRAM_NUM_DEVICES: return copy_info(&u32, sizeof(u32), param_value_size, param_value, param_value_size_ret);
        case CL_PROGRAM_DEVICES: {
            cl_device_id device = &g_device;
            return copy_info(&device, sizeof(device), param_value_size, param_value, param_value_size_ret);
        }
        case CL_PROGRAM_SOURCE: return copy_string_info(program->source ? program->source : "", param_value_size, param_value, param_value_size_ret);
        default: return CL_INVALID_VALUE;
    }
}

cl_kernel clCreateKernel(cl_program program, const char *kernel_name, cl_int *errcode_ret) {
    (void)program;
    if (!kernel_name) {
        set_error(errcode_ret, CL_INVALID_VALUE);
        return NULL;
    }
    cl_kernel kernel = calloc(1, sizeof(*kernel));
    if (!kernel) {
        set_error(errcode_ret, CL_OUT_OF_HOST_MEMORY);
        return NULL;
    }
    kernel->name = strdup(kernel_name);
    kernel->program = program;
    set_error(errcode_ret, CL_SUCCESS);
    return kernel;
}

cl_int clReleaseKernel(cl_kernel kernel) {
    if (!kernel) return CL_INVALID_KERNEL;
    free(kernel->name);
    free(kernel);
    return CL_SUCCESS;
}

cl_int clRetainKernel(cl_kernel kernel) {
    return kernel ? CL_SUCCESS : CL_INVALID_KERNEL;
}

cl_int clGetKernelInfo(cl_kernel kernel, cl_uint param_name, size_t param_value_size, void *param_value, size_t *param_value_size_ret) {
    if (!kernel) return CL_INVALID_KERNEL;
    cl_uint u32 = 1;
    switch (param_name) {
        case CL_KERNEL_FUNCTION_NAME: return copy_string_info(kernel->name ? kernel->name : "", param_value_size, param_value, param_value_size_ret);
        case CL_KERNEL_NUM_ARGS: u32 = 16; return copy_info(&u32, sizeof(u32), param_value_size, param_value, param_value_size_ret);
        case CL_KERNEL_REFERENCE_COUNT: return copy_info(&u32, sizeof(u32), param_value_size, param_value, param_value_size_ret);
        case CL_KERNEL_CONTEXT: return copy_info(&kernel->program->context, sizeof(kernel->program->context), param_value_size, param_value, param_value_size_ret);
        case CL_KERNEL_PROGRAM: return copy_info(&kernel->program, sizeof(kernel->program), param_value_size, param_value, param_value_size_ret);
        default: return CL_INVALID_VALUE;
    }
}

cl_int clGetKernelWorkGroupInfo(cl_kernel kernel, cl_device_id device, cl_uint param_name, size_t param_value_size, void *param_value, size_t *param_value_size_ret) {
    if (!kernel) return CL_INVALID_KERNEL;
    if (device && device != &g_device) return CL_INVALID_DEVICE;
    size_t wg = 128;
    size_t compile_wg[3] = { 0, 0, 0 };
    switch (param_name) {
        case CL_KERNEL_WORK_GROUP_SIZE: return copy_info(&wg, sizeof(wg), param_value_size, param_value, param_value_size_ret);
        case CL_KERNEL_COMPILE_WORK_GROUP_SIZE: return copy_info(compile_wg, sizeof(compile_wg), param_value_size, param_value, param_value_size_ret);
        default: return CL_INVALID_VALUE;
    }
}

cl_int clSetKernelArg(cl_kernel kernel, cl_uint arg_index, size_t arg_size, const void *arg_value) {
    if (!kernel) return CL_INVALID_KERNEL;
    if (arg_index >= 16) return CL_INVALID_ARG_INDEX;
    if (arg_size == sizeof(cl_mem) && arg_value) {
        kernel->mem_args[arg_index] = *(const cl_mem *)arg_value;
    } else if (arg_value && arg_size <= sizeof(kernel->scalar_args[arg_index])) {
        memcpy(kernel->scalar_args[arg_index], arg_value, arg_size);
        kernel->arg_sizes[arg_index] = arg_size;
    }
    return CL_SUCCESS;
}

cl_int clEnqueueNDRangeKernel(cl_command_queue command_queue, cl_kernel kernel, cl_uint work_dim, const size_t *global_work_offset, const size_t *global_work_size, const size_t *local_work_size, cl_uint num_events_in_wait_list, const cl_event *event_wait_list, cl_event *event) {
    (void)command_queue;
    (void)work_dim;
    (void)global_work_offset;
    (void)local_work_size;
    (void)num_events_in_wait_list;
    (void)event_wait_list;
    if (event) *event = NULL;
    if (!kernel) return CL_INVALID_KERNEL;
    if (!kernel_is_supported_vector_add(kernel)) {
        trace_unsupported_kernel(kernel);
        return CL_INVALID_OPERATION;
    }
    cl_mem a = kernel->mem_args[0];
    cl_mem b = kernel->mem_args[1];
    cl_mem out = kernel->mem_args[2];
    if (!a || !b || !out) return CL_INVALID_OPERATION;
    size_t n = a->size / sizeof(float);
    if (b->size / sizeof(float) < n) n = b->size / sizeof(float);
    if (out->size / sizeof(float) < n) n = out->size / sizeof(float);
    if (global_work_size && global_work_size[0] < n) n = global_work_size[0];
    if (cpu_governor_enabled(n)) {
        return run_vector_add_cpu_emulated(a, b, out, n);
    }
    int rc = send_vector_add_3fd(n, a->fd, b->fd, out->fd);
    return rc == 0 ? CL_SUCCESS : CL_INVALID_OPERATION;
}

cl_int clFlush(cl_command_queue command_queue) {
    (void)command_queue;
    return CL_SUCCESS;
}

cl_int clFinish(cl_command_queue command_queue) {
    (void)command_queue;
    return CL_SUCCESS;
}

cl_int clWaitForEvents(cl_uint num_events, const cl_event *event_list) {
    (void)num_events;
    (void)event_list;
    return CL_SUCCESS;
}

cl_int clReleaseEvent(cl_event event) {
    free(event);
    return CL_SUCCESS;
}

void *clGetExtensionFunctionAddress(const char *func_name) {
    if (!func_name) return NULL;
    if (strcmp(func_name, "clIcdGetPlatformIDsKHR") == 0) return (void *)clIcdGetPlatformIDsKHR;
    return NULL;
}

void *clGetExtensionFunctionAddressForPlatform(cl_platform_id platform, const char *func_name) {
    (void)platform;
    return clGetExtensionFunctionAddress(func_name);
}

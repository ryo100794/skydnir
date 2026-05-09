/*
 * pdocker_gpu_executor.c
 *
 * APK-owned Android/Bionic GPU command executor probe.
 *
 * This process is intentionally not an LLM engine. Container processes keep
 * model loading, tokenization, graph ownership, sampling, and HTTP serving.
 * The executor validates the Android-side GPU command boundary that later
 * backs a glibc-facing pdocker shim/command queue.
 */
#include <EGL/egl.h>
#include <GLES3/gl31.h>
#include <vulkan/vulkan.h>
#include "pdocker_gpu_abi.h"
#include <dlfcn.h>
#include <errno.h>
#include <math.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <fcntl.h>
#include <time.h>
#include <unistd.h>

#ifndef VK_KHR_8BIT_STORAGE_EXTENSION_NAME
#define VK_KHR_8BIT_STORAGE_EXTENSION_NAME "VK_KHR_8bit_storage"
#endif
#ifndef VK_KHR_16BIT_STORAGE_EXTENSION_NAME
#define VK_KHR_16BIT_STORAGE_EXTENSION_NAME "VK_KHR_16bit_storage"
#endif
#ifndef VK_KHR_SHADER_FLOAT16_INT8_EXTENSION_NAME
#define VK_KHR_SHADER_FLOAT16_INT8_EXTENSION_NAME "VK_KHR_shader_float16_int8"
#endif
#ifndef VK_KHR_STORAGE_BUFFER_STORAGE_CLASS_EXTENSION_NAME
#define VK_KHR_STORAGE_BUFFER_STORAGE_CLASS_EXTENSION_NAME "VK_KHR_storage_buffer_storage_class"
#endif

#define PDOCKER_GPU_MAX_PASSED_FDS 24
#define PDOCKER_GPU_MAX_COMMAND_BYTES 4096
#define PDOCKER_GPU_MAX_VULKAN_BINDINGS 16
#define PDOCKER_GPU_MAX_PUSH_BYTES 256
#define PDOCKER_GPU_MAX_VULKAN_ENTRY_NAME 128
#define PDOCKER_GPU_MAX_VULKAN_SPECIALIZATION_ENTRIES 16
#define PDOCKER_GPU_MAX_VULKAN_SPECIALIZATION_BYTES 256
#define PDOCKER_GPU_RESIDENT_CACHE_SLOTS 8
#define PDOCKER_GPU_MUTABLE_BUFFER_CACHE_SLOTS 32
#define PDOCKER_GPU_PIPELINE_CACHE_SLOTS 32
#define PDOCKER_GPU_DIRTY_MASK_CACHE_SLOTS 64
#define PDOCKER_GPU_RESIDENT_CACHE_DEFAULT_THRESHOLD (64u * 1024u * 1024u)
#define PDOCKER_GPU_MUTABLE_BUFFER_CACHE_DEFAULT_MAX_BYTES (32u * 1024u * 1024u)
#define PDOCKER_GPU_WRITEONLY_DIRTY_PROBE_DEFAULT_MIN_BYTES (16u * 1024u * 1024u)
#define PDOCKER_GPU_WRITEONLY_DIRTY_PROBE_SENTINEL 0xA5u

#ifndef GL_COMPUTE_SHADER
#define GL_COMPUTE_SHADER 0x91B9
#endif
#ifndef EGL_OPENGL_ES3_BIT_KHR
#define EGL_OPENGL_ES3_BIT_KHR 0x00000040
#endif

typedef int32_t ocl_int;
typedef uint32_t ocl_uint;
typedef uint64_t ocl_ulong;
typedef uintptr_t ocl_bitfield;
typedef ocl_bitfield ocl_device_type;
typedef ocl_bitfield ocl_mem_flags;
typedef ocl_uint ocl_bool;
typedef intptr_t ocl_context_properties;
typedef intptr_t ocl_queue_properties;

typedef struct _cl_platform_id *ocl_platform_id;
typedef struct _cl_device_id *ocl_device_id;
typedef struct _cl_context *ocl_context;
typedef struct _cl_command_queue *ocl_command_queue;
typedef struct _cl_mem *ocl_mem;
typedef struct _cl_program *ocl_program;
typedef struct _cl_kernel *ocl_kernel;
typedef struct _cl_event *ocl_event;

#define OCL_SUCCESS 0
#define OCL_TRUE 1
#define OCL_DEVICE_TYPE_GPU (1u << 2)
#define OCL_DEVICE_TYPE_DEFAULT (1u << 0)
#define OCL_MEM_READ_WRITE (1u << 0)
#define OCL_MEM_COPY_HOST_PTR (1u << 5)

typedef struct {
    uint64_t dst_index;
    float expected;
    float gpu;
    float src0;
    float src1;
    double abs_error;
} CpuOracleSample;

typedef struct {
    void *lib;
    ocl_platform_id platform;
    ocl_device_id device;
    ocl_context context;
    ocl_command_queue queue;
    ocl_int (*clGetPlatformIDs)(ocl_uint, ocl_platform_id *, ocl_uint *);
    ocl_int (*clGetDeviceIDs)(ocl_platform_id, ocl_device_type, ocl_uint, ocl_device_id *, ocl_uint *);
    ocl_context (*clCreateContext)(const ocl_context_properties *, ocl_uint, const ocl_device_id *, void (*)(const char *, const void *, size_t, void *), void *, ocl_int *);
    ocl_command_queue (*clCreateCommandQueue)(ocl_context, ocl_device_id, ocl_bitfield, ocl_int *);
    ocl_command_queue (*clCreateCommandQueueWithProperties)(ocl_context, ocl_device_id, const ocl_queue_properties *, ocl_int *);
    ocl_mem (*clCreateBuffer)(ocl_context, ocl_mem_flags, size_t, void *, ocl_int *);
    ocl_int (*clReleaseMemObject)(ocl_mem);
    ocl_program (*clCreateProgramWithSource)(ocl_context, ocl_uint, const char **, const size_t *, ocl_int *);
    ocl_int (*clBuildProgram)(ocl_program, ocl_uint, const ocl_device_id *, const char *, void (*)(ocl_program, void *), void *);
    ocl_int (*clGetProgramBuildInfo)(ocl_program, ocl_device_id, ocl_uint, size_t, void *, size_t *);
    ocl_int (*clReleaseProgram)(ocl_program);
    ocl_kernel (*clCreateKernel)(ocl_program, const char *, ocl_int *);
    ocl_int (*clReleaseKernel)(ocl_kernel);
    ocl_int (*clSetKernelArg)(ocl_kernel, ocl_uint, size_t, const void *);
    ocl_int (*clEnqueueWriteBuffer)(ocl_command_queue, ocl_mem, ocl_bool, size_t, size_t, const void *, ocl_uint, const ocl_event *, ocl_event *);
    ocl_int (*clEnqueueReadBuffer)(ocl_command_queue, ocl_mem, ocl_bool, size_t, size_t, void *, ocl_uint, const ocl_event *, ocl_event *);
    ocl_int (*clEnqueueNDRangeKernel)(ocl_command_queue, ocl_kernel, ocl_uint, const size_t *, const size_t *, const size_t *, ocl_uint, const ocl_event *, ocl_event *);
    ocl_int (*clFinish)(ocl_command_queue);
    ocl_int (*clReleaseCommandQueue)(ocl_command_queue);
    ocl_int (*clReleaseContext)(ocl_context);
} OpenClBackend;

typedef enum {
    GPU_API_AUTO = 0,
    GPU_API_VULKAN = 1,
    GPU_API_OPENCL = 2,
} GpuApiAffinity;

static const uint32_t kVectorAddSpv[] = {
    0x07230203, 0x00010000, 0x0008000b, 0x0000002c, 0x00000000, 0x00020011, 0x00000001, 0x0006000b,
    0x00000001, 0x4c534c47, 0x6474732e, 0x3035342e, 0x00000000, 0x0003000e, 0x00000000, 0x00000001,
    0x0006000f, 0x00000005, 0x00000004, 0x6e69616d, 0x00000000, 0x0000000b, 0x00060010, 0x00000004,
    0x00000011, 0x00000080, 0x00000001, 0x00000001, 0x00030003, 0x00000002, 0x000001c2, 0x00040005,
    0x00000004, 0x6e69616d, 0x00000000, 0x00030005, 0x00000008, 0x00000069, 0x00080005, 0x0000000b,
    0x475f6c67, 0x61626f6c, 0x766e496c, 0x7461636f, 0x496e6f69, 0x00000044, 0x00030005, 0x00000012,
    0x0000004f, 0x00040006, 0x00000012, 0x00000000, 0x0000006f, 0x00030005, 0x00000014, 0x00000000,
    0x00030005, 0x00000019, 0x00000041, 0x00040006, 0x00000019, 0x00000000, 0x00000061, 0x00030005,
    0x0000001b, 0x00000000, 0x00030005, 0x00000021, 0x00000042, 0x00040006, 0x00000021, 0x00000000,
    0x00000062, 0x00030005, 0x00000023, 0x00000000, 0x00040047, 0x0000000b, 0x0000000b, 0x0000001c,
    0x00040047, 0x00000011, 0x00000006, 0x00000004, 0x00030047, 0x00000012, 0x00000003, 0x00040048,
    0x00000012, 0x00000000, 0x00000019, 0x00050048, 0x00000012, 0x00000000, 0x00000023, 0x00000000,
    0x00030047, 0x00000014, 0x00000019, 0x00040047, 0x00000014, 0x00000021, 0x00000002, 0x00040047,
    0x00000014, 0x00000022, 0x00000000, 0x00040047, 0x00000018, 0x00000006, 0x00000004, 0x00030047,
    0x00000019, 0x00000003, 0x00040048, 0x00000019, 0x00000000, 0x00000018, 0x00050048, 0x00000019,
    0x00000000, 0x00000023, 0x00000000, 0x00030047, 0x0000001b, 0x00000018, 0x00040047, 0x0000001b,
    0x00000021, 0x00000000, 0x00040047, 0x0000001b, 0x00000022, 0x00000000, 0x00040047, 0x00000020,
    0x00000006, 0x00000004, 0x00030047, 0x00000021, 0x00000003, 0x00040048, 0x00000021, 0x00000000,
    0x00000018, 0x00050048, 0x00000021, 0x00000000, 0x00000023, 0x00000000, 0x00030047, 0x00000023,
    0x00000018, 0x00040047, 0x00000023, 0x00000021, 0x00000001, 0x00040047, 0x00000023, 0x00000022,
    0x00000000, 0x00040047, 0x0000002b, 0x0000000b, 0x00000019, 0x00020013, 0x00000002, 0x00030021,
    0x00000003, 0x00000002, 0x00040015, 0x00000006, 0x00000020, 0x00000000, 0x00040020, 0x00000007,
    0x00000007, 0x00000006, 0x00040017, 0x00000009, 0x00000006, 0x00000003, 0x00040020, 0x0000000a,
    0x00000001, 0x00000009, 0x0004003b, 0x0000000a, 0x0000000b, 0x00000001, 0x0004002b, 0x00000006,
    0x0000000c, 0x00000000, 0x00040020, 0x0000000d, 0x00000001, 0x00000006, 0x00030016, 0x00000010,
    0x00000020, 0x0003001d, 0x00000011, 0x00000010, 0x0003001e, 0x00000012, 0x00000011, 0x00040020,
    0x00000013, 0x00000002, 0x00000012, 0x0004003b, 0x00000013, 0x00000014, 0x00000002, 0x00040015,
    0x00000015, 0x00000020, 0x00000001, 0x0004002b, 0x00000015, 0x00000016, 0x00000000, 0x0003001d,
    0x00000018, 0x00000010, 0x0003001e, 0x00000019, 0x00000018, 0x00040020, 0x0000001a, 0x00000002,
    0x00000019, 0x0004003b, 0x0000001a, 0x0000001b, 0x00000002, 0x00040020, 0x0000001d, 0x00000002,
    0x00000010, 0x0003001d, 0x00000020, 0x00000010, 0x0003001e, 0x00000021, 0x00000020, 0x00040020,
    0x00000022, 0x00000002, 0x00000021, 0x0004003b, 0x00000022, 0x00000023, 0x00000002, 0x0004002b,
    0x00000006, 0x00000029, 0x00000080, 0x0004002b, 0x00000006, 0x0000002a, 0x00000001, 0x0006002c,
    0x00000009, 0x0000002b, 0x00000029, 0x0000002a, 0x0000002a, 0x00050036, 0x00000002, 0x00000004,
    0x00000000, 0x00000003, 0x000200f8, 0x00000005, 0x0004003b, 0x00000007, 0x00000008, 0x00000007,
    0x00050041, 0x0000000d, 0x0000000e, 0x0000000b, 0x0000000c, 0x0004003d, 0x00000006, 0x0000000f,
    0x0000000e, 0x0003003e, 0x00000008, 0x0000000f, 0x0004003d, 0x00000006, 0x00000017, 0x00000008,
    0x0004003d, 0x00000006, 0x0000001c, 0x00000008, 0x00060041, 0x0000001d, 0x0000001e, 0x0000001b,
    0x00000016, 0x0000001c, 0x0004003d, 0x00000010, 0x0000001f, 0x0000001e, 0x0004003d, 0x00000006,
    0x00000024, 0x00000008, 0x00060041, 0x0000001d, 0x00000025, 0x00000023, 0x00000016, 0x00000024,
    0x0004003d, 0x00000010, 0x00000026, 0x00000025, 0x00050081, 0x00000010, 0x00000027, 0x0000001f,
    0x00000026, 0x00060041, 0x0000001d, 0x00000028, 0x00000014, 0x00000016, 0x00000017, 0x0003003e,
    0x00000028, 0x00000027, 0x000100fd, 0x00010038,
};

static const uint32_t kMatmul256Spv[] = {
    0x07230203, 0x00010000, 0x0008000b, 0x00000053, 0x00000000, 0x00020011, 0x00000001, 0x0006000b,
    0x00000001, 0x4c534c47, 0x6474732e, 0x3035342e, 0x00000000, 0x0003000e, 0x00000000, 0x00000001,
    0x0006000f, 0x00000005, 0x00000004, 0x6e69616d, 0x00000000, 0x0000000b, 0x00060010, 0x00000004,
    0x00000011, 0x00000008, 0x00000008, 0x00000001, 0x00030003, 0x00000002, 0x000001c2, 0x00040005,
    0x00000004, 0x6e69616d, 0x00000000, 0x00030005, 0x00000008, 0x006c6f63, 0x00080005, 0x0000000b,
    0x475f6c67, 0x61626f6c, 0x766e496c, 0x7461636f, 0x496e6f69, 0x00000044, 0x00030005, 0x00000010,
    0x00776f72, 0x00030005, 0x00000020, 0x006d7573, 0x00030005, 0x00000022, 0x0000006b, 0x00030005,
    0x0000002b, 0x00000041, 0x00040006, 0x0000002b, 0x00000000, 0x00000061, 0x00030005, 0x0000002d,
    0x00000000, 0x00030005, 0x00000038, 0x00000042, 0x00040006, 0x00000038, 0x00000000, 0x00000062,
    0x00030005, 0x0000003a, 0x00000000, 0x00030005, 0x00000048, 0x0000004f, 0x00040006, 0x00000048,
    0x00000000, 0x0000006f, 0x00030005, 0x0000004a, 0x00000000, 0x00040047, 0x0000000b, 0x0000000b,
    0x0000001c, 0x00040047, 0x0000002a, 0x00000006, 0x00000004, 0x00030047, 0x0000002b, 0x00000003,
    0x00040048, 0x0000002b, 0x00000000, 0x00000018, 0x00050048, 0x0000002b, 0x00000000, 0x00000023,
    0x00000000, 0x00030047, 0x0000002d, 0x00000018, 0x00040047, 0x0000002d, 0x00000021, 0x00000000,
    0x00040047, 0x0000002d, 0x00000022, 0x00000000, 0x00040047, 0x00000037, 0x00000006, 0x00000004,
    0x00030047, 0x00000038, 0x00000003, 0x00040048, 0x00000038, 0x00000000, 0x00000018, 0x00050048,
    0x00000038, 0x00000000, 0x00000023, 0x00000000, 0x00030047, 0x0000003a, 0x00000018, 0x00040047,
    0x0000003a, 0x00000021, 0x00000001, 0x00040047, 0x0000003a, 0x00000022, 0x00000000, 0x00040047,
    0x00000047, 0x00000006, 0x00000004, 0x00030047, 0x00000048, 0x00000003, 0x00040048, 0x00000048,
    0x00000000, 0x00000019, 0x00050048, 0x00000048, 0x00000000, 0x00000023, 0x00000000, 0x00030047,
    0x0000004a, 0x00000019, 0x00040047, 0x0000004a, 0x00000021, 0x00000002, 0x00040047, 0x0000004a,
    0x00000022, 0x00000000, 0x00040047, 0x00000052, 0x0000000b, 0x00000019, 0x00020013, 0x00000002,
    0x00030021, 0x00000003, 0x00000002, 0x00040015, 0x00000006, 0x00000020, 0x00000000, 0x00040020,
    0x00000007, 0x00000007, 0x00000006, 0x00040017, 0x00000009, 0x00000006, 0x00000003, 0x00040020,
    0x0000000a, 0x00000001, 0x00000009, 0x0004003b, 0x0000000a, 0x0000000b, 0x00000001, 0x0004002b,
    0x00000006, 0x0000000c, 0x00000000, 0x00040020, 0x0000000d, 0x00000001, 0x00000006, 0x0004002b,
    0x00000006, 0x00000011, 0x00000001, 0x0004002b, 0x00000006, 0x00000015, 0x00000100, 0x00020014,
    0x00000016, 0x00030016, 0x0000001e, 0x00000020, 0x00040020, 0x0000001f, 0x00000007, 0x0000001e,
    0x0004002b, 0x0000001e, 0x00000021, 0x00000000, 0x0003001d, 0x0000002a, 0x0000001e, 0x0003001e,
    0x0000002b, 0x0000002a, 0x00040020, 0x0000002c, 0x00000002, 0x0000002b, 0x0004003b, 0x0000002c,
    0x0000002d, 0x00000002, 0x00040015, 0x0000002e, 0x00000020, 0x00000001, 0x0004002b, 0x0000002e,
    0x0000002f, 0x00000000, 0x00040020, 0x00000034, 0x00000002, 0x0000001e, 0x0003001d, 0x00000037,
    0x0000001e, 0x0003001e, 0x00000038, 0x00000037, 0x00040020, 0x00000039, 0x00000002, 0x00000038,
    0x0004003b, 0x00000039, 0x0000003a, 0x00000002, 0x0004002b, 0x0000002e, 0x00000045, 0x00000001,
    0x0003001d, 0x00000047, 0x0000001e, 0x0003001e, 0x00000048, 0x00000047, 0x00040020, 0x00000049,
    0x00000002, 0x00000048, 0x0004003b, 0x00000049, 0x0000004a, 0x00000002, 0x0004002b, 0x00000006,
    0x00000051, 0x00000008, 0x0006002c, 0x00000009, 0x00000052, 0x00000051, 0x00000051, 0x00000011,
    0x00050036, 0x00000002, 0x00000004, 0x00000000, 0x00000003, 0x000200f8, 0x00000005, 0x0004003b,
    0x00000007, 0x00000008, 0x00000007, 0x0004003b, 0x00000007, 0x00000010, 0x00000007, 0x0004003b,
    0x0000001f, 0x00000020, 0x00000007, 0x0004003b, 0x00000007, 0x00000022, 0x00000007, 0x00050041,
    0x0000000d, 0x0000000e, 0x0000000b, 0x0000000c, 0x0004003d, 0x00000006, 0x0000000f, 0x0000000e,
    0x0003003e, 0x00000008, 0x0000000f, 0x00050041, 0x0000000d, 0x00000012, 0x0000000b, 0x00000011,
    0x0004003d, 0x00000006, 0x00000013, 0x00000012, 0x0003003e, 0x00000010, 0x00000013, 0x0004003d,
    0x00000006, 0x00000014, 0x00000010, 0x000500ae, 0x00000016, 0x00000017, 0x00000014, 0x00000015,
    0x0004003d, 0x00000006, 0x00000018, 0x00000008, 0x000500ae, 0x00000016, 0x00000019, 0x00000018,
    0x00000015, 0x000500a6, 0x00000016, 0x0000001a, 0x00000017, 0x00000019, 0x000300f7, 0x0000001c,
    0x00000000, 0x000400fa, 0x0000001a, 0x0000001b, 0x0000001c, 0x000200f8, 0x0000001b, 0x000100fd,
    0x000200f8, 0x0000001c, 0x0003003e, 0x00000020, 0x00000021, 0x0003003e, 0x00000022, 0x0000000c,
    0x000200f9, 0x00000023, 0x000200f8, 0x00000023, 0x000400f6, 0x00000025, 0x00000026, 0x00000000,
    0x000200f9, 0x00000027, 0x000200f8, 0x00000027, 0x0004003d, 0x00000006, 0x00000028, 0x00000022,
    0x000500b0, 0x00000016, 0x00000029, 0x00000028, 0x00000015, 0x000400fa, 0x00000029, 0x00000024,
    0x00000025, 0x000200f8, 0x00000024, 0x0004003d, 0x00000006, 0x00000030, 0x00000010, 0x00050084,
    0x00000006, 0x00000031, 0x00000030, 0x00000015, 0x0004003d, 0x00000006, 0x00000032, 0x00000022,
    0x00050080, 0x00000006, 0x00000033, 0x00000031, 0x00000032, 0x00060041, 0x00000034, 0x00000035,
    0x0000002d, 0x0000002f, 0x00000033, 0x0004003d, 0x0000001e, 0x00000036, 0x00000035, 0x0004003d,
    0x00000006, 0x0000003b, 0x00000022, 0x00050084, 0x00000006, 0x0000003c, 0x0000003b, 0x00000015,
    0x0004003d, 0x00000006, 0x0000003d, 0x00000008, 0x00050080, 0x00000006, 0x0000003e, 0x0000003c,
    0x0000003d, 0x00060041, 0x00000034, 0x0000003f, 0x0000003a, 0x0000002f, 0x0000003e, 0x0004003d,
    0x0000001e, 0x00000040, 0x0000003f, 0x00050085, 0x0000001e, 0x00000041, 0x00000036, 0x00000040,
    0x0004003d, 0x0000001e, 0x00000042, 0x00000020, 0x00050081, 0x0000001e, 0x00000043, 0x00000042,
    0x00000041, 0x0003003e, 0x00000020, 0x00000043, 0x000200f9, 0x00000026, 0x000200f8, 0x00000026,
    0x0004003d, 0x00000006, 0x00000044, 0x00000022, 0x00050080, 0x00000006, 0x00000046, 0x00000044,
    0x00000045, 0x0003003e, 0x00000022, 0x00000046, 0x000200f9, 0x00000023, 0x000200f8, 0x00000025,
    0x0004003d, 0x00000006, 0x0000004b, 0x00000010, 0x00050084, 0x00000006, 0x0000004c, 0x0000004b,
    0x00000015, 0x0004003d, 0x00000006, 0x0000004d, 0x00000008, 0x00050080, 0x00000006, 0x0000004e,
    0x0000004c, 0x0000004d, 0x0004003d, 0x0000001e, 0x0000004f, 0x00000020, 0x00060041, 0x00000034,
    0x00000050, 0x0000004a, 0x0000002f, 0x0000004e, 0x0003003e, 0x00000050, 0x0000004f, 0x000100fd,
    0x00010038,
};

static FILE *g_json_out = NULL;

static FILE *json_out(void) {
    return g_json_out ? g_json_out : stdout;
}

static double now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec * 1000.0 + (double)ts.tv_nsec / 1000000.0;
}

static void json_fail(const char *stage, const char *message) {
    fprintf(json_out(),
            "{\"executor\":\"pdocker-gpu-executor\",\"api\":\"%s\",\"abi_version\":\"%s\","
            "\"role\":\"%s\",\"llm_engine\":\"%s\",\"device_independent\":true,"
            "\"backend_impl\":\"gles31_compute\","
            "\"backend_affinity\":\"fallback\","
            "\"valid\":false,\"stage\":\"%s\",\"error\":\"%s\"}\n",
            PDOCKER_GPU_COMMAND_API, PDOCKER_GPU_ABI_VERSION,
            PDOCKER_GPU_EXECUTOR_ROLE, PDOCKER_GPU_LLM_ENGINE_LOCATION,
            stage, message ? message : "unknown");
    fflush(json_out());
}

static void fill_inputs(float *a, float *b, size_t n) {
    for (size_t i = 0; i < n; ++i) {
        a[i] = (float)i * 0.25f;
        b[i] = 1.0f - (float)i * 0.125f;
    }
}

static GLuint compile_shader(const char *src) {
    GLuint shader = glCreateShader(GL_COMPUTE_SHADER);
    glShaderSource(shader, 1, &src, NULL);
    glCompileShader(shader);
    GLint ok = GL_FALSE;
    glGetShaderiv(shader, GL_COMPILE_STATUS, &ok);
    if (!ok) {
        char log[512];
        GLsizei len = 0;
        glGetShaderInfoLog(shader, (GLsizei)sizeof(log), &len, log);
        fprintf(stderr, "shader compile failed: %.*s\n", (int)len, log);
        glDeleteShader(shader);
        return 0;
    }
    return shader;
}

static GLuint link_program(GLuint shader) {
    GLuint program = glCreateProgram();
    glAttachShader(program, shader);
    glLinkProgram(program);
    GLint ok = GL_FALSE;
    glGetProgramiv(program, GL_LINK_STATUS, &ok);
    if (!ok) {
        char log[512];
        GLsizei len = 0;
        glGetProgramInfoLog(program, (GLsizei)sizeof(log), &len, log);
        fprintf(stderr, "program link failed: %.*s\n", (int)len, log);
        glDeleteProgram(program);
        return 0;
    }
    return program;
}

static GLuint make_ssbo(GLuint binding, const void *data, size_t bytes, GLenum usage) {
    GLuint id = 0;
    glGenBuffers(1, &id);
    glBindBuffer(GL_SHADER_STORAGE_BUFFER, id);
    glBufferData(GL_SHADER_STORAGE_BUFFER, (GLsizeiptr)bytes, data, usage);
    glBindBufferBase(GL_SHADER_STORAGE_BUFFER, binding, id);
    return id;
}

static void *load_symbol(void *lib, const char *name) {
    void *sym = dlsym(lib, name);
    if (!sym) fprintf(stderr, "pdocker-gpu-executor: OpenCL symbol missing: %s\n", name);
    return sym;
}

static int load_opencl_backend(OpenClBackend *cl) {
    memset(cl, 0, sizeof(*cl));
    const char *last_error = "not attempted";
    const char *env_path = getenv("PDOCKER_ANDROID_OPENCL_LIBRARY");
    const char *paths[] = {
        env_path && env_path[0] ? env_path : NULL,
        "libOpenCL.so",
        "/vendor/lib64/libOpenCL.so",
        "/system/vendor/lib64/libOpenCL.so",
        "/system/lib64/libOpenCL.so",
        "/vendor/lib64/egl/libOpenCL.so",
        NULL,
    };
    for (size_t i = 0; i < sizeof(paths) / sizeof(paths[0]); ++i) {
        if (!paths[i] || !paths[i][0]) continue;
        cl->lib = dlopen(paths[i], RTLD_NOW | RTLD_LOCAL);
        if (cl->lib) break;
        last_error = dlerror();
    }
    if (!cl->lib) {
        fprintf(stderr, "pdocker-gpu-executor: Android OpenCL dlopen failed: %s\n", last_error ? last_error : "unknown");
        return -1;
    }

#define LOAD_OCL(name) do { \
        cl->name = (void *)load_symbol(cl->lib, #name); \
        if (!cl->name) return -2; \
    } while (0)
    LOAD_OCL(clGetPlatformIDs);
    LOAD_OCL(clGetDeviceIDs);
    LOAD_OCL(clCreateContext);
    cl->clCreateCommandQueueWithProperties = (void *)dlsym(cl->lib, "clCreateCommandQueueWithProperties");
    LOAD_OCL(clCreateCommandQueue);
    LOAD_OCL(clCreateBuffer);
    LOAD_OCL(clReleaseMemObject);
    LOAD_OCL(clCreateProgramWithSource);
    LOAD_OCL(clBuildProgram);
    cl->clGetProgramBuildInfo = (void *)dlsym(cl->lib, "clGetProgramBuildInfo");
    LOAD_OCL(clReleaseProgram);
    LOAD_OCL(clCreateKernel);
    LOAD_OCL(clReleaseKernel);
    LOAD_OCL(clSetKernelArg);
    LOAD_OCL(clEnqueueWriteBuffer);
    LOAD_OCL(clEnqueueReadBuffer);
    LOAD_OCL(clEnqueueNDRangeKernel);
    LOAD_OCL(clFinish);
    LOAD_OCL(clReleaseCommandQueue);
    LOAD_OCL(clReleaseContext);
#undef LOAD_OCL

    ocl_int err = OCL_SUCCESS;
    if (cl->clGetPlatformIDs(1, &cl->platform, NULL) != OCL_SUCCESS || !cl->platform) return -3;
    if (cl->clGetDeviceIDs(cl->platform, OCL_DEVICE_TYPE_GPU, 1, &cl->device, NULL) != OCL_SUCCESS || !cl->device) {
        if (cl->clGetDeviceIDs(cl->platform, OCL_DEVICE_TYPE_DEFAULT, 1, &cl->device, NULL) != OCL_SUCCESS || !cl->device) return -4;
    }
    cl->context = cl->clCreateContext(NULL, 1, &cl->device, NULL, NULL, &err);
    if (err != OCL_SUCCESS || !cl->context) return -5;
    if (cl->clCreateCommandQueueWithProperties) {
        cl->queue = cl->clCreateCommandQueueWithProperties(cl->context, cl->device, NULL, &err);
    } else {
        cl->queue = cl->clCreateCommandQueue(cl->context, cl->device, 0, &err);
    }
    if (err != OCL_SUCCESS || !cl->queue) return -6;
    return 0;
}

static void close_opencl_backend(OpenClBackend *cl) {
    if (!cl) return;
    if (cl->queue && cl->clReleaseCommandQueue) cl->clReleaseCommandQueue(cl->queue);
    if (cl->context && cl->clReleaseContext) cl->clReleaseContext(cl->context);
    if (cl->lib) dlclose(cl->lib);
    memset(cl, 0, sizeof(*cl));
}

static uint32_t find_vulkan_memory_type(VkPhysicalDevice physical_device, uint32_t type_bits, VkMemoryPropertyFlags flags) {
    VkPhysicalDeviceMemoryProperties props;
    vkGetPhysicalDeviceMemoryProperties(physical_device, &props);
    for (uint32_t i = 0; i < props.memoryTypeCount; ++i) {
        if ((type_bits & (1u << i)) && (props.memoryTypes[i].propertyFlags & flags) == flags) return i;
    }
    return UINT32_MAX;
}

static int vulkan_device_extension_supported(
        VkPhysicalDevice physical_device,
        const char *name) {
    if (!name || !name[0]) return 0;
    uint32_t count = 0;
    if (vkEnumerateDeviceExtensionProperties(physical_device, NULL, &count, NULL) != VK_SUCCESS ||
        count == 0) {
        return 0;
    }
    VkExtensionProperties *props =
        (VkExtensionProperties *)calloc(count, sizeof(VkExtensionProperties));
    if (!props) return 0;
    VkResult rc = vkEnumerateDeviceExtensionProperties(
        physical_device, NULL, &count, props);
    int found = 0;
    if (rc == VK_SUCCESS) {
        for (uint32_t i = 0; i < count; ++i) {
            if (strcmp(props[i].extensionName, name) == 0) {
                found = 1;
                break;
            }
        }
    }
    free(props);
    return found;
}

static void append_vulkan_device_extension(
        VkPhysicalDevice physical_device,
        const char **extensions,
        uint32_t *count,
        uint32_t capacity,
        const char *name) {
    if (!extensions || !count || *count >= capacity || !name || !name[0]) return;
    for (uint32_t i = 0; i < *count; ++i) {
        if (strcmp(extensions[i], name) == 0) return;
    }
    if (!vulkan_device_extension_supported(physical_device, name)) return;
    extensions[(*count)++] = name;
}

typedef struct {
    VkBuffer buffer;
    VkDeviceMemory memory;
    void *map;
    size_t size;
} VulkanVectorBuffer;

typedef struct {
    uint32_t binding;
    off_t offset;
    size_t size;
    off_t api_offset;
    size_t api_range;
    size_t api_buffer_size;
    uint32_t api_descriptor_type;
    int api_dynamic;
    off_t api_memory_offset;
} VulkanDispatchBinding;

typedef struct {
    int has_dirty_probe;
    int dirty_probe;
    int has_dirty_probe_min_bytes;
    size_t dirty_probe_min_bytes;
    int has_dirty_writeback;
    int dirty_writeback;
    int has_writeonly_buffer_cache;
    int writeonly_buffer_cache;
    int has_mutable_buffer_cache_max_bytes;
    size_t mutable_buffer_cache_max_bytes;
    int has_profile_response;
    int profile_response;
    int has_rewrite_duplicate_descriptors;
    int rewrite_duplicate_descriptors;
    int has_materialize_descriptor_aliases;
    int materialize_descriptor_aliases;
    int has_materialize_specialization_constants;
    int materialize_specialization_constants;
    int has_disable_pipeline_optimization;
    int disable_pipeline_optimization;
    int has_skip_unused_descriptor_transfers;
    int skip_unused_descriptor_transfers;
    int has_use_spirv_descriptor_access;
    int use_spirv_descriptor_access;
    int has_disable_overlap_aliasing;
    int disable_overlap_aliasing;
    int has_cpu_oracle;
    int cpu_oracle;
    int disable_storage8;
    int disable_storage16;
    int disable_subgroup_arithmetic;
} VulkanDispatchOptions;

typedef struct {
    uint32_t constant_id;
    uint32_t offset;
    size_t size;
} VulkanDispatchSpecialization;

static uint64_t specialization_value_u64(
        const uint8_t *specialization_data,
        size_t specialization_data_size,
        const VulkanDispatchSpecialization *specialization);

static int specialization_value_for_id(
        const VulkanDispatchSpecialization *specializations,
        size_t specialization_count,
        const uint8_t *specialization_data,
        size_t specialization_data_size,
        uint32_t constant_id,
        uint64_t *out_value);

typedef struct {
    uint32_t target_id;
    uint32_t original_binding;
    uint32_t rewritten_binding;
} VulkanBindingAlias;

typedef struct {
    uint8_t used;
    uint8_t readable;
    uint8_t writable;
} SpirvDescriptorAccess;

typedef struct {
    int valid;
    uint64_t value;
} SpirvScalarConstant;

typedef struct {
    int valid;
    uint32_t count;
    uint64_t values[4];
} SpirvCompositeConstant;

typedef struct {
    int ready;
    VkInstance instance;
    VkPhysicalDevice physical_device;
    VkDevice device;
    VkQueue queue;
    uint32_t queue_family;
    VkShaderModule shader;
    VkShaderModule matmul_shader;
    VkDescriptorSetLayout set_layout;
    VkPipelineLayout pipeline_layout;
    VkPipeline pipeline;
    VkPipeline matmul_pipeline;
    VkCommandPool command_pool;
    uint32_t api_version;
    VkPhysicalDeviceProperties physical_properties;
    VkPhysicalDeviceFeatures physical_features;
    VkPhysicalDevice16BitStorageFeatures physical_storage16;
    VkPhysicalDevice8BitStorageFeatures physical_storage8;
    VkPhysicalDeviceShaderFloat16Int8Features physical_float16_int8;
    VkPhysicalDeviceSubgroupProperties subgroup_properties;
    double init_ms;
} VulkanRuntime;

static VulkanRuntime g_vulkan_runtime;

static uint32_t choose_vulkan_instance_api_version(void) {
    uint32_t supported = VK_API_VERSION_1_0;
    PFN_vkEnumerateInstanceVersion enumerate_instance_version =
        (PFN_vkEnumerateInstanceVersion)vkGetInstanceProcAddr(NULL, "vkEnumerateInstanceVersion");
    if (enumerate_instance_version) {
        VkResult rc = enumerate_instance_version(&supported);
        if (rc != VK_SUCCESS || supported < VK_API_VERSION_1_0) {
            supported = VK_API_VERSION_1_0;
        }
    }
    if (supported >= VK_API_VERSION_1_2) return VK_API_VERSION_1_2;
    return supported;
}

typedef struct {
    int valid;
    dev_t dev;
    ino_t ino;
    off_t offset;
    size_t size;
    uint32_t binding;
    unsigned long hits;
    VulkanVectorBuffer buffer;
} VulkanResidentCacheEntry;

static VulkanResidentCacheEntry g_vulkan_resident_cache[PDOCKER_GPU_RESIDENT_CACHE_SLOTS];

typedef struct {
    int valid;
    int scratch;
    dev_t dev;
    ino_t ino;
    off_t offset;
    size_t size;
    uint32_t binding;
    unsigned long hits;
    VulkanVectorBuffer buffer;
} VulkanMutableBufferCacheEntry;

static VulkanMutableBufferCacheEntry g_vulkan_mutable_buffer_cache[PDOCKER_GPU_MUTABLE_BUFFER_CACHE_SLOTS];

typedef struct {
    int valid;
    uint64_t shader_hash;
    uint64_t spec_hash;
    uint32_t binding;
    size_t size;
    size_t page_size;
    size_t page_count;
    size_t dirty_page_count;
    size_t dirty_bytes;
    unsigned long hits;
    unsigned char *dirty_pages;
} VulkanDirtyMaskCacheEntry;

static VulkanDirtyMaskCacheEntry g_vulkan_dirty_mask_cache[PDOCKER_GPU_DIRTY_MASK_CACHE_SLOTS];

typedef struct {
    int valid;
    uint64_t shader_hash;
    uint64_t spec_hash;
    uint64_t policy_hash;
    size_t shader_size;
    size_t specialization_data_size;
    size_t specialization_count;
    uint32_t layout_count;
    uint32_t push_size;
    char entry_name[PDOCKER_GPU_MAX_VULKAN_ENTRY_NAME];
    unsigned long hits;
    VkDescriptorSetLayout set_layout;
    VkPipelineLayout pipeline_layout;
    VkShaderModule shader;
    VkPipeline pipeline;
} VulkanPipelineCacheEntry;

static VulkanPipelineCacheEntry g_vulkan_pipeline_cache[PDOCKER_GPU_PIPELINE_CACHE_SLOTS];

typedef struct {
    uint32_t magic;
    uint32_t version;
    uint32_t bound;
    uint32_t local_size[3];
    uint32_t local_size_id[3];
    uint32_t capability_count;
    uint32_t capabilities[24];
    int requires_float16;
    int requires_int16;
    int requires_int8;
    int requires_int64;
    int requires_storage16;
    int requires_storage8;
    int requires_subgroup_arithmetic;
    uint64_t hash;
    int valid;
    int truncated;
} SpirvTraceSummary;

static const char *spirv_capability_name(uint32_t capability) {
    switch (capability) {
        case 1: return "Shader";
        case 9: return "Float16";
        case 10: return "Float64";
        case 11: return "Int64";
        case 12: return "Int64Atomics";
        case 22: return "Int16";
        case 39: return "Int8";
        case 61: return "GroupNonUniform";
        case 62: return "GroupNonUniformVote";
        case 63: return "GroupNonUniformArithmetic";
        case 64: return "GroupNonUniformBallot";
        case 65: return "GroupNonUniformShuffle";
        case 66: return "GroupNonUniformShuffleRelative";
        case 67: return "GroupNonUniformQuad";
        case 68: return "GroupNonUniformClustered";
        case 4433: return "StorageBuffer16BitAccess";
        case 4434: return "UniformAndStorageBuffer16BitAccess";
        case 4435: return "StoragePushConstant16";
        case 4448: return "StorageBuffer8BitAccess";
        case 4449: return "UniformAndStorageBuffer8BitAccess";
        case 4450: return "StoragePushConstant8";
        default: return "cap";
    }
}

static SpirvTraceSummary summarize_spirv(const uint32_t *code, size_t bytes) {
    SpirvTraceSummary s;
    memset(&s, 0, sizeof(s));
    s.local_size[0] = s.local_size[1] = s.local_size[2] = 0;
    s.local_size_id[0] = s.local_size_id[1] = s.local_size_id[2] = 0;
    s.hash = 1469598103934665603ull;
    if (!code) return s;
    const uint8_t *raw = (const uint8_t *)code;
    for (size_t i = 0; i < bytes; ++i) {
        s.hash ^= raw[i];
        s.hash *= 1099511628211ull;
    }
    if (bytes < 20 || (bytes % sizeof(uint32_t)) != 0) return s;
    const size_t words = bytes / sizeof(uint32_t);
    s.magic = code[0];
    s.version = code[1];
    s.bound = code[3];
    if (s.magic != 0x07230203u) return s;
    s.valid = 1;
    for (size_t i = 5; i < words;) {
        uint32_t inst = code[i];
        uint16_t word_count = (uint16_t)(inst >> 16);
        uint16_t op = (uint16_t)(inst & 0xffffu);
        if (word_count == 0 || i + word_count > words) {
            s.truncated = 1;
            break;
        }
        if (op == 17 && word_count >= 2) {
            uint32_t cap = code[i + 1];
            int seen = 0;
            for (uint32_t c = 0; c < s.capability_count; ++c) {
                if (s.capabilities[c] == cap) {
                    seen = 1;
                    break;
                }
            }
            if (!seen && s.capability_count < (uint32_t)(sizeof(s.capabilities) / sizeof(s.capabilities[0]))) {
                s.capabilities[s.capability_count++] = cap;
            }
            if (cap == 9) s.requires_float16 = 1;
            else if (cap == 11 || cap == 12) s.requires_int64 = 1;
            else if (cap == 22) s.requires_int16 = 1;
            else if (cap == 39) s.requires_int8 = 1;
            else if (cap == 4433 || cap == 4434 || cap == 4435) s.requires_storage16 = 1;
            else if (cap == 4448 || cap == 4449 || cap == 4450) s.requires_storage8 = 1;
            else if (cap == 63) s.requires_subgroup_arithmetic = 1;
        } else if (op == 16 && word_count >= 6 && code[i + 2] == 17) {
            s.local_size[0] = code[i + 3];
            s.local_size[1] = code[i + 4];
            s.local_size[2] = code[i + 5];
        } else if (op == 331 && word_count >= 6 && code[i + 2] == 38) {
            s.local_size_id[0] = code[i + 3];
            s.local_size_id[1] = code[i + 4];
            s.local_size_id[2] = code[i + 5];
        }
        i += word_count;
    }
    return s;
}

static void log_vulkan_feature_trace(const VulkanRuntime *rt) {
    if (!rt) return;
    fprintf(stderr,
            "pdocker-gpu-executor: Android Vulkan features api=%u.%u device=\"%s\" vendor=0x%04x device=0x%04x "
            "shaderInt64=%u "
            "storage16={ssbo:%u,ubo_ssbo:%u,push:%u,io:%u} "
            "storage8={ssbo:%u,ubo_ssbo:%u,push:%u} "
            "float16=%u int8=%u subgroup={size:%u,stages:0x%x,ops:0x%x} "
            "limits={push:%u,shared:%u,per_stage_storage:%u,set_storage:%u,max_bound_sets:%u,workgroup_invocations:%u}\n",
            VK_API_VERSION_MAJOR(rt->api_version),
            VK_API_VERSION_MINOR(rt->api_version),
            rt->physical_properties.deviceName,
            rt->physical_properties.vendorID,
            rt->physical_properties.deviceID,
            rt->physical_features.shaderInt64,
            rt->physical_storage16.storageBuffer16BitAccess,
            rt->physical_storage16.uniformAndStorageBuffer16BitAccess,
            rt->physical_storage16.storagePushConstant16,
            rt->physical_storage16.storageInputOutput16,
            rt->physical_storage8.storageBuffer8BitAccess,
            rt->physical_storage8.uniformAndStorageBuffer8BitAccess,
            rt->physical_storage8.storagePushConstant8,
            rt->physical_float16_int8.shaderFloat16,
            rt->physical_float16_int8.shaderInt8,
            rt->subgroup_properties.subgroupSize,
            rt->subgroup_properties.supportedStages,
            rt->subgroup_properties.supportedOperations,
            rt->physical_properties.limits.maxPushConstantsSize,
            rt->physical_properties.limits.maxComputeSharedMemorySize,
            rt->physical_properties.limits.maxPerStageDescriptorStorageBuffers,
            rt->physical_properties.limits.maxDescriptorSetStorageBuffers,
            rt->physical_properties.limits.maxBoundDescriptorSets,
            rt->physical_properties.limits.maxComputeWorkGroupInvocations);
}

static void log_vulkan_enabled_feature_trace(
        const VkPhysicalDeviceFeatures *features,
        const VkPhysicalDevice16BitStorageFeatures *storage16,
        const VkPhysicalDevice8BitStorageFeatures *storage8,
        const VkPhysicalDeviceShaderFloat16Int8Features *float16_int8) {
    fprintf(stderr,
            "pdocker-gpu-executor: Android Vulkan enabled features shaderInt64=%u "
            "storage16={ssbo:%u,ubo_ssbo:%u,push:%u,io:%u} "
            "storage8={ssbo:%u,ubo_ssbo:%u,push:%u} "
            "float16=%u int8=%u\n",
            features ? features->shaderInt64 : 0,
            storage16 ? storage16->storageBuffer16BitAccess : 0,
            storage16 ? storage16->uniformAndStorageBuffer16BitAccess : 0,
            storage16 ? storage16->storagePushConstant16 : 0,
            storage16 ? storage16->storageInputOutput16 : 0,
            storage8 ? storage8->storageBuffer8BitAccess : 0,
            storage8 ? storage8->uniformAndStorageBuffer8BitAccess : 0,
            storage8 ? storage8->storagePushConstant8 : 0,
            float16_int8 ? float16_int8->shaderFloat16 : 0,
            float16_int8 ? float16_int8->shaderInt8 : 0);
}

static void log_spirv_trace(
        const SpirvTraceSummary *summary,
        const VulkanDispatchBinding *bindings,
        size_t binding_count,
        size_t push_size,
        uint32_t gx,
        uint32_t gy,
        uint32_t gz) {
    if (!summary) return;
    fprintf(stderr,
            "pdocker-gpu-executor: SPIR-V trace valid=%u truncated=%u hash=0x%016llx "
            "magic=0x%08x version=0x%08x bound=%u local_size=%u,%u,%u local_size_id=%u,%u,%u "
            "dispatch=%u,%u,%u push=%zu bindings=%zu caps=",
            summary->valid,
            summary->truncated,
            (unsigned long long)summary->hash,
            summary->magic,
            summary->version,
            summary->bound,
            summary->local_size[0],
            summary->local_size[1],
            summary->local_size[2],
            summary->local_size_id[0],
            summary->local_size_id[1],
            summary->local_size_id[2],
            gx,
            gy,
            gz,
            push_size,
            binding_count);
    for (uint32_t i = 0; i < summary->capability_count; ++i) {
        fprintf(stderr, "%s%s(%u)", i ? "," : "", spirv_capability_name(summary->capabilities[i]), summary->capabilities[i]);
    }
    if (summary->capability_count == 0) fprintf(stderr, "none");
    fprintf(stderr, "\n");
    for (size_t i = 0; i < binding_count; ++i) {
        fprintf(stderr,
                "pdocker-gpu-executor: SPIR-V binding[%zu] binding=%u fd_offset=%lld bytes=%zu\n",
                i,
                bindings[i].binding,
                (long long)bindings[i].offset,
                bindings[i].size);
    }
}

static int spirv_feature_missing(const SpirvTraceSummary *summary, const VulkanRuntime *rt) {
    if (!summary || !rt) return 0;
    if (summary->requires_float16 && !rt->physical_float16_int8.shaderFloat16) return 1;
    if (summary->requires_int64 && !rt->physical_features.shaderInt64) return 1;
    if (summary->requires_storage16 && !rt->physical_storage16.storageBuffer16BitAccess) return 1;
    if (summary->requires_storage8 && !rt->physical_storage8.storageBuffer8BitAccess) return 1;
    if (summary->requires_int8 && !rt->physical_float16_int8.shaderInt8) return 1;
    if (summary->requires_subgroup_arithmetic &&
        (rt->subgroup_properties.supportedOperations & VK_SUBGROUP_FEATURE_ARITHMETIC_BIT) == 0) {
        return 1;
    }
    /*
     * Vulkan has no standalone shaderInt16 feature in this bridge today. The
     * llama path we care about uses 16-bit storage, so record Int16 capability
     * as advisory instead of making every Int16 declaration a hard mismatch.
     */
    return 0;
}

static void write_spirv_feature_report(FILE *out, const SpirvTraceSummary *summary, const VulkanRuntime *rt) {
    if (!out || !summary) return;
    const int missing_float16 = summary->requires_float16 && (!rt || !rt->physical_float16_int8.shaderFloat16);
    const int missing_int64 = summary->requires_int64 && (!rt || !rt->physical_features.shaderInt64);
    const int missing_storage16 = summary->requires_storage16 && (!rt || !rt->physical_storage16.storageBuffer16BitAccess);
    const int missing_storage8 = summary->requires_storage8 && (!rt || !rt->physical_storage8.storageBuffer8BitAccess);
    const int missing_int8 = summary->requires_int8 && (!rt || !rt->physical_float16_int8.shaderInt8);
    const int missing_subgroup_arithmetic = summary->requires_subgroup_arithmetic &&
        (!rt || (rt->subgroup_properties.supportedOperations & VK_SUBGROUP_FEATURE_ARITHMETIC_BIT) == 0);
    fprintf(out,
            "\"spirv_feature_requirements\":{"
            "\"float16\":%s,\"int16\":%s,\"int8\":%s,\"int64\":%s,"
            "\"storage16\":%s,\"storage8\":%s,\"subgroup_arithmetic\":%s},"
            "\"spirv_feature_mismatch\":%s,"
            "\"spirv_feature_mismatches\":[",
            summary->requires_float16 ? "true" : "false",
            summary->requires_int16 ? "true" : "false",
            summary->requires_int8 ? "true" : "false",
            summary->requires_int64 ? "true" : "false",
            summary->requires_storage16 ? "true" : "false",
            summary->requires_storage8 ? "true" : "false",
            summary->requires_subgroup_arithmetic ? "true" : "false",
            spirv_feature_missing(summary, rt) ? "true" : "false");
    int first = 1;
#define WRITE_MISMATCH(name, cond) do { \
        if (cond) { \
            fprintf(out, "%s\"%s\"", first ? "" : ",", name); \
            first = 0; \
        } \
    } while (0)
    WRITE_MISMATCH("float16", missing_float16);
    WRITE_MISMATCH("int64", missing_int64);
    WRITE_MISMATCH("storage16", missing_storage16);
    WRITE_MISMATCH("storage8", missing_storage8);
    WRITE_MISMATCH("int8", missing_int8);
    WRITE_MISMATCH("subgroup_arithmetic", missing_subgroup_arithmetic);
#undef WRITE_MISMATCH
    fprintf(out, "]");
}

static void write_vulkan_specialization_report(
        FILE *out,
        const VulkanDispatchSpecialization *specializations,
        size_t specialization_count,
        const uint8_t *specialization_data,
        size_t specialization_data_size) {
    fprintf(out, "\"specialization_entries\":[");
    for (size_t i = 0; i < specialization_count; ++i) {
        uint64_t value = specialization_value_u64(
            specialization_data,
            specialization_data_size,
            &specializations[i]);
        fprintf(out,
                "%s{\"constant_id\":%u,\"offset\":%u,\"size\":%zu,\"value_u64\":%llu}",
                i ? "," : "",
                specializations[i].constant_id,
                specializations[i].offset,
                specializations[i].size,
                (unsigned long long)value);
    }
    fprintf(out, "]");
}

static void write_spirv_execution_report(
        FILE *out,
        const SpirvTraceSummary *summary,
        const VulkanDispatchSpecialization *specializations,
        size_t specialization_count,
        const uint8_t *specialization_data,
        size_t specialization_data_size,
        const uint8_t *push,
        size_t push_size) {
    if (!out || !summary) return;
    fprintf(out,
            "\"push_bytes\":%zu,"
            "\"push_u32\":[",
            push_size);
    size_t push_u32_count = push && push_size >= sizeof(uint32_t)
        ? push_size / sizeof(uint32_t)
        : 0;
    if (push_u32_count > 32) push_u32_count = 32;
    for (size_t i = 0; i < push_u32_count; ++i) {
        uint32_t value = 0;
        memcpy(&value, push + i * sizeof(uint32_t), sizeof(value));
        fprintf(out, "%s%u", i ? "," : "", value);
    }
    fprintf(out,
            "],"
            "\"spirv_hash\":\"0x%016llx\","
            "\"spirv_valid\":%s,\"spirv_truncated\":%u,"
            "\"spirv_local_size\":[%u,%u,%u],"
            "\"spirv_local_size_id\":[%u,%u,%u],"
            "\"spirv_local_size_resolved\":[",
            (unsigned long long)summary->hash,
            summary->valid ? "true" : "false",
            summary->truncated,
            summary->local_size[0],
            summary->local_size[1],
            summary->local_size[2],
            summary->local_size_id[0],
            summary->local_size_id[1],
            summary->local_size_id[2]);
    for (uint32_t i = 0; i < 3; ++i) {
        uint64_t value = summary->local_size[i];
        if (summary->local_size_id[i]) {
            uint64_t spec_value = 0;
            if (specialization_value_for_id(specializations,
                                            specialization_count,
                                            specialization_data,
                                            specialization_data_size,
                                            summary->local_size_id[i],
                                            &spec_value)) {
                value = spec_value;
            }
        }
        fprintf(out, "%s%llu", i ? "," : "", (unsigned long long)value);
    }
    fprintf(out, "],");
    write_vulkan_specialization_report(out,
                                       specializations,
                                       specialization_count,
                                       specialization_data,
                                       specialization_data_size);
}

static void write_vulkan_limits_report(FILE *out, const VulkanRuntime *rt) {
    if (!out || !rt) return;
    const VkPhysicalDeviceLimits *limits = &rt->physical_properties.limits;
    fprintf(out,
            "\"android_vulkan_device\":{"
            "\"api\":\"%u.%u\",\"deviceName\":\"",
            VK_API_VERSION_MAJOR(rt->api_version),
            VK_API_VERSION_MINOR(rt->api_version));
    const char *name = rt->physical_properties.deviceName;
    for (size_t i = 0; name && name[i]; ++i) {
        unsigned char ch = (unsigned char)name[i];
        if (ch == '"' || ch == '\\') fputc('\\', out);
        if (ch >= 0x20 && ch < 0x7f) fputc((int)ch, out);
    }
    fprintf(out,
            "\",\"vendorID\":%u,\"deviceID\":%u,"
            "\"limits\":{"
            "\"maxPushConstantsSize\":%u,"
            "\"maxComputeSharedMemorySize\":%u,"
            "\"maxPerStageDescriptorStorageBuffers\":%u,"
            "\"maxDescriptorSetStorageBuffers\":%u,"
            "\"maxBoundDescriptorSets\":%u,"
            "\"maxComputeWorkGroupInvocations\":%u,"
            "\"maxComputeWorkGroupSize\":[%u,%u,%u],"
            "\"maxComputeWorkGroupCount\":[%u,%u,%u]}}",
            rt->physical_properties.vendorID,
            rt->physical_properties.deviceID,
            limits->maxPushConstantsSize,
            limits->maxComputeSharedMemorySize,
            limits->maxPerStageDescriptorStorageBuffers,
            limits->maxDescriptorSetStorageBuffers,
            limits->maxBoundDescriptorSets,
            limits->maxComputeWorkGroupInvocations,
            limits->maxComputeWorkGroupSize[0],
            limits->maxComputeWorkGroupSize[1],
            limits->maxComputeWorkGroupSize[2],
            limits->maxComputeWorkGroupCount[0],
            limits->maxComputeWorkGroupCount[1],
            limits->maxComputeWorkGroupCount[2]);
}

static int create_vulkan_vector_buffer(VkPhysicalDevice physical_device, VkDevice device, size_t bytes, const void *initial, VulkanVectorBuffer *out) {
    memset(out, 0, sizeof(*out));
    VkBufferCreateInfo bci = {
        .sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO,
        .size = (VkDeviceSize)bytes,
        .usage = VK_BUFFER_USAGE_STORAGE_BUFFER_BIT,
        .sharingMode = VK_SHARING_MODE_EXCLUSIVE,
    };
    VkResult rc = vkCreateBuffer(device, &bci, NULL, &out->buffer);
    if (rc != VK_SUCCESS) return -10;
    VkMemoryRequirements req;
    vkGetBufferMemoryRequirements(device, out->buffer, &req);
    uint32_t memory_type = find_vulkan_memory_type(
        physical_device,
        req.memoryTypeBits,
        VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT);
    if (memory_type == UINT32_MAX) return -11;
    VkMemoryAllocateInfo mai = {
        .sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
        .allocationSize = req.size,
        .memoryTypeIndex = memory_type,
    };
    rc = vkAllocateMemory(device, &mai, NULL, &out->memory);
    if (rc != VK_SUCCESS) return -12;
    rc = vkBindBufferMemory(device, out->buffer, out->memory, 0);
    if (rc != VK_SUCCESS) return -13;
    rc = vkMapMemory(device, out->memory, 0, (VkDeviceSize)bytes, 0, &out->map);
    if (rc != VK_SUCCESS || !out->map) return -14;
    out->size = bytes;
    if (initial) memcpy(out->map, initial, bytes);
    return 0;
}

static void destroy_vulkan_vector_buffer(VkDevice device, VulkanVectorBuffer *buf) {
    if (!buf) return;
    if (buf->map) vkUnmapMemory(device, buf->memory);
    if (buf->buffer) vkDestroyBuffer(device, buf->buffer, NULL);
    if (buf->memory) vkFreeMemory(device, buf->memory, NULL);
    memset(buf, 0, sizeof(*buf));
}

static int read_fd_exact(int fd, void *buf, size_t size, off_t offset);

static int env_truthy(const char *name, int default_value) {
    const char *value = getenv(name);
    if (!value || !value[0]) return default_value;
    if (strcmp(value, "0") == 0 || strcasecmp(value, "false") == 0 ||
        strcasecmp(value, "no") == 0 || strcasecmp(value, "off") == 0) {
        return 0;
    }
    return 1;
}

static void apply_vulkan_feature_policy(VulkanRuntime *rt) {
    if (!rt) return;
    if (env_truthy("PDOCKER_VULKAN_DISABLE_8BIT_STORAGE", 0)) {
        rt->physical_storage8.storageBuffer8BitAccess = VK_FALSE;
        rt->physical_storage8.uniformAndStorageBuffer8BitAccess = VK_FALSE;
        rt->physical_storage8.storagePushConstant8 = VK_FALSE;
        rt->physical_float16_int8.shaderInt8 = VK_FALSE;
    }
    if (env_truthy("PDOCKER_VULKAN_DISABLE_16BIT_STORAGE", 0)) {
        rt->physical_storage16.storageBuffer16BitAccess = VK_FALSE;
        rt->physical_storage16.uniformAndStorageBuffer16BitAccess = VK_FALSE;
        rt->physical_storage16.storagePushConstant16 = VK_FALSE;
        rt->physical_storage16.storageInputOutput16 = VK_FALSE;
    }
    if (env_truthy("PDOCKER_VULKAN_DISABLE_SUBGROUP_ARITHMETIC", 0)) {
        rt->subgroup_properties.supportedOperations &= ~VK_SUBGROUP_FEATURE_ARITHMETIC_BIT;
    }
}

static VulkanRuntime effective_vulkan_runtime_for_dispatch(
        const VulkanRuntime *rt,
        const VulkanDispatchOptions *options) {
    VulkanRuntime effective;
    memset(&effective, 0, sizeof(effective));
    if (rt) effective = *rt;
    if (options && options->disable_storage8) {
        effective.physical_storage8.storageBuffer8BitAccess = VK_FALSE;
        effective.physical_storage8.uniformAndStorageBuffer8BitAccess = VK_FALSE;
        effective.physical_storage8.storagePushConstant8 = VK_FALSE;
        effective.physical_float16_int8.shaderInt8 = VK_FALSE;
    }
    if (options && options->disable_storage16) {
        effective.physical_storage16.storageBuffer16BitAccess = VK_FALSE;
        effective.physical_storage16.uniformAndStorageBuffer16BitAccess = VK_FALSE;
        effective.physical_storage16.storagePushConstant16 = VK_FALSE;
        effective.physical_storage16.storageInputOutput16 = VK_FALSE;
    }
    if (options && options->disable_subgroup_arithmetic) {
        effective.subgroup_properties.supportedOperations &= ~VK_SUBGROUP_FEATURE_ARITHMETIC_BIT;
    }
    return effective;
}

static size_t resident_cache_threshold(void) {
    const char *value = getenv("PDOCKER_GPU_RESIDENT_CACHE_MIN_BYTES");
    if (value && value[0]) {
        char *end = NULL;
        unsigned long long parsed = strtoull(value, &end, 10);
        if (end && *end == '\0' && parsed > 0) return (size_t)parsed;
    }
    return PDOCKER_GPU_RESIDENT_CACHE_DEFAULT_THRESHOLD;
}

static int resident_cache_key(int fd, dev_t *dev, ino_t *ino) {
    struct stat st;
    if (fstat(fd, &st) != 0) return -errno;
    *dev = st.st_dev;
    *ino = st.st_ino;
    return 0;
}

static int resident_cache_candidate(uint32_t binding, size_t size) {
    if (!env_truthy("PDOCKER_GPU_RESIDENT_CACHE", 1)) return 0;
    /*
     * Keep the first large storage binding resident by default. In llama.cpp's
     * Vulkan graphs this is the read-mostly model/weight side of the dispatch;
     * small activation/output buffers continue through the fully coherent
     * upload/download path for correctness.
     */
    if (binding != 0) return 0;
    return size >= resident_cache_threshold();
}

static size_t mutable_buffer_cache_max_bytes(void) {
    const char *value = getenv("PDOCKER_GPU_MUTABLE_BUFFER_CACHE_MAX_BYTES");
    if (value && value[0]) {
        char *end = NULL;
        unsigned long long parsed = strtoull(value, &end, 10);
        if (end && *end == '\0') return (size_t)parsed;
    }
    return PDOCKER_GPU_MUTABLE_BUFFER_CACHE_DEFAULT_MAX_BYTES;
}

static int mutable_buffer_cache_candidate_with_max(
        uint32_t binding,
        size_t size,
        size_t max_bytes) {
    (void)binding;
    if (!env_truthy("PDOCKER_GPU_MUTABLE_BUFFER_CACHE", 1)) return 0;
    return max_bytes > 0 && size > 0 && size <= max_bytes;
}

static int writeonly_buffer_cache_enabled(void) {
    return env_truthy("PDOCKER_GPU_WRITEONLY_BUFFER_CACHE", 0);
}

static int writeonly_dirty_probe_enabled(void) {
    return env_truthy("PDOCKER_GPU_WRITEONLY_DIRTY_PROBE", 0);
}

static int writeonly_dirty_writeback_enabled(void) {
    return env_truthy("PDOCKER_GPU_WRITEONLY_DIRTY_WRITEBACK", 0);
}

static size_t writeonly_dirty_probe_min_bytes(void) {
    const char *value = getenv("PDOCKER_GPU_WRITEONLY_DIRTY_PROBE_MIN_BYTES");
    if (value && value[0]) {
        char *end = NULL;
        unsigned long long parsed = strtoull(value, &end, 10);
        if (end && *end == '\0') return (size_t)parsed;
    }
    return PDOCKER_GPU_WRITEONLY_DIRTY_PROBE_DEFAULT_MIN_BYTES;
}

static size_t dirty_probe_page_size(void) {
    long page_size = sysconf(_SC_PAGESIZE);
    if (page_size <= 0) return 4096;
    return (size_t)page_size;
}

static int dirty_probe_page_changed(
        const unsigned char *page,
        size_t bytes,
        unsigned char sentinel) {
    for (size_t i = 0; i < bytes; ++i) {
        if (page[i] != sentinel) return 1;
    }
    return 0;
}

static size_t count_dirty_probe_pages(
        const void *map,
        size_t size,
        size_t page_size,
        unsigned char sentinel,
        unsigned char *dirty_pages,
        size_t dirty_page_capacity,
        size_t *dirty_bytes) {
    const unsigned char *bytes = (const unsigned char *)map;
    size_t pages = 0;
    size_t changed_bytes = 0;
    if (!bytes || page_size == 0) {
        if (dirty_bytes) *dirty_bytes = 0;
        return 0;
    }
    for (size_t offset = 0; offset < size; offset += page_size) {
        size_t span = page_size;
        if (span > size - offset) span = size - offset;
        if (dirty_probe_page_changed(bytes + offset, span, sentinel)) {
            size_t page_index = offset / page_size;
            if (dirty_pages && page_index < dirty_page_capacity) dirty_pages[page_index] = 1;
            pages++;
            changed_bytes += span;
        }
    }
    if (dirty_bytes) *dirty_bytes = changed_bytes;
    return pages;
}

static int parse_vulkan_dispatch_option(VulkanDispatchOptions *options, const char *token) {
    if (!options || !token || !token[0]) return -1;
    if (strncmp(token, "dirty_probe=", 12) == 0) {
        const char *value = token + 12;
        if (strcmp(value, "1") == 0 || strcasecmp(value, "true") == 0 ||
            strcasecmp(value, "yes") == 0 || strcasecmp(value, "on") == 0) {
            options->has_dirty_probe = 1;
            options->dirty_probe = 1;
            return 0;
        }
        if (strcmp(value, "0") == 0 || strcasecmp(value, "false") == 0 ||
            strcasecmp(value, "no") == 0 || strcasecmp(value, "off") == 0) {
            options->has_dirty_probe = 1;
            options->dirty_probe = 0;
            return 0;
        }
        return -1;
    }
    if (strncmp(token, "dirty_writeback=", 16) == 0) {
        const char *value = token + 16;
        if (strcmp(value, "1") == 0 || strcasecmp(value, "true") == 0 ||
            strcasecmp(value, "yes") == 0 || strcasecmp(value, "on") == 0) {
            options->has_dirty_writeback = 1;
            options->dirty_writeback = 1;
            return 0;
        }
        if (strcmp(value, "0") == 0 || strcasecmp(value, "false") == 0 ||
            strcasecmp(value, "no") == 0 || strcasecmp(value, "off") == 0) {
            options->has_dirty_writeback = 1;
            options->dirty_writeback = 0;
            return 0;
        }
        return -1;
    }
    if (strncmp(token, "dirty_probe_min=", 16) == 0) {
        const char *value = token + 16;
        char *end = NULL;
        unsigned long long parsed = strtoull(value, &end, 10);
        if (!end || *end != '\0') return -1;
        options->has_dirty_probe_min_bytes = 1;
        options->dirty_probe_min_bytes = (size_t)parsed;
        return 0;
    }
    if (strncmp(token, "writeonly_cache=", 16) == 0) {
        const char *value = token + 16;
        if (strcmp(value, "1") == 0 || strcasecmp(value, "true") == 0 ||
            strcasecmp(value, "yes") == 0 || strcasecmp(value, "on") == 0) {
            options->has_writeonly_buffer_cache = 1;
            options->writeonly_buffer_cache = 1;
            return 0;
        }
        if (strcmp(value, "0") == 0 || strcasecmp(value, "false") == 0 ||
            strcasecmp(value, "no") == 0 || strcasecmp(value, "off") == 0) {
            options->has_writeonly_buffer_cache = 1;
            options->writeonly_buffer_cache = 0;
            return 0;
        }
        return -1;
    }
    if (strncmp(token, "mutable_cache_max=", 18) == 0) {
        const char *value = token + 18;
        char *end = NULL;
        unsigned long long parsed = strtoull(value, &end, 10);
        if (!end || *end != '\0') return -1;
        options->has_mutable_buffer_cache_max_bytes = 1;
        options->mutable_buffer_cache_max_bytes = (size_t)parsed;
        return 0;
    }
    if (strncmp(token, "profile=", 8) == 0) {
        const char *value = token + 8;
        if (strcmp(value, "1") == 0 || strcasecmp(value, "true") == 0 ||
            strcasecmp(value, "yes") == 0 || strcasecmp(value, "on") == 0) {
            options->has_profile_response = 1;
            options->profile_response = 1;
            return 0;
        }
        if (strcmp(value, "0") == 0 || strcasecmp(value, "false") == 0 ||
            strcasecmp(value, "no") == 0 || strcasecmp(value, "off") == 0) {
            options->has_profile_response = 1;
            options->profile_response = 0;
            return 0;
        }
        return -1;
    }
    if (strncmp(token, "rewrite_duplicate_descriptors=", 30) == 0) {
        const char *value = token + 30;
        if (strcmp(value, "1") == 0 || strcasecmp(value, "true") == 0 ||
            strcasecmp(value, "yes") == 0 || strcasecmp(value, "on") == 0) {
            options->has_rewrite_duplicate_descriptors = 1;
            options->rewrite_duplicate_descriptors = 1;
            return 0;
        }
        if (strcmp(value, "0") == 0 || strcasecmp(value, "false") == 0 ||
            strcasecmp(value, "no") == 0 || strcasecmp(value, "off") == 0) {
            options->has_rewrite_duplicate_descriptors = 1;
            options->rewrite_duplicate_descriptors = 0;
            return 0;
        }
        return -1;
    }
    if (strncmp(token, "materialize_descriptor_aliases=", 31) == 0) {
        const char *value = token + 31;
        if (strcmp(value, "1") == 0 || strcasecmp(value, "true") == 0 ||
            strcasecmp(value, "yes") == 0 || strcasecmp(value, "on") == 0) {
            options->has_materialize_descriptor_aliases = 1;
            options->materialize_descriptor_aliases = 1;
            return 0;
        }
        if (strcmp(value, "0") == 0 || strcasecmp(value, "false") == 0 ||
            strcasecmp(value, "no") == 0 || strcasecmp(value, "off") == 0) {
            options->has_materialize_descriptor_aliases = 1;
            options->materialize_descriptor_aliases = 0;
            return 0;
        }
        return -1;
    }
    if (strncmp(token, "materialize_specialization=", 27) == 0) {
        const char *value = token + 27;
        if (strcmp(value, "1") == 0 || strcasecmp(value, "true") == 0 ||
            strcasecmp(value, "yes") == 0 || strcasecmp(value, "on") == 0) {
            options->has_materialize_specialization_constants = 1;
            options->materialize_specialization_constants = 1;
            return 0;
        }
        if (strcmp(value, "0") == 0 || strcasecmp(value, "false") == 0 ||
            strcasecmp(value, "no") == 0 || strcasecmp(value, "off") == 0) {
            options->has_materialize_specialization_constants = 1;
            options->materialize_specialization_constants = 0;
            return 0;
        }
        return -1;
    }
    if (strncmp(token, "disable_pipeline_optimization=", 30) == 0) {
        const char *value = token + 30;
        if (strcmp(value, "1") == 0 || strcasecmp(value, "true") == 0 ||
            strcasecmp(value, "yes") == 0 || strcasecmp(value, "on") == 0) {
            options->has_disable_pipeline_optimization = 1;
            options->disable_pipeline_optimization = 1;
            return 0;
        }
        if (strcmp(value, "0") == 0 || strcasecmp(value, "false") == 0 ||
            strcasecmp(value, "no") == 0 || strcasecmp(value, "off") == 0) {
            options->has_disable_pipeline_optimization = 1;
            options->disable_pipeline_optimization = 0;
            return 0;
        }
        return -1;
    }
    if (strncmp(token, "skip_unused_descriptor_transfers=", 33) == 0) {
        const char *value = token + 33;
        if (strcmp(value, "1") == 0 || strcasecmp(value, "true") == 0 ||
            strcasecmp(value, "yes") == 0 || strcasecmp(value, "on") == 0) {
            options->has_skip_unused_descriptor_transfers = 1;
            options->skip_unused_descriptor_transfers = 1;
            return 0;
        }
        if (strcmp(value, "0") == 0 || strcasecmp(value, "false") == 0 ||
            strcasecmp(value, "no") == 0 || strcasecmp(value, "off") == 0) {
            options->has_skip_unused_descriptor_transfers = 1;
            options->skip_unused_descriptor_transfers = 0;
            return 0;
        }
        return -1;
    }
    if (strncmp(token, "use_spirv_descriptor_access=", 28) == 0) {
        const char *value = token + 28;
        if (strcmp(value, "1") == 0 || strcasecmp(value, "true") == 0 ||
            strcasecmp(value, "yes") == 0 || strcasecmp(value, "on") == 0) {
            options->has_use_spirv_descriptor_access = 1;
            options->use_spirv_descriptor_access = 1;
            return 0;
        }
        if (strcmp(value, "0") == 0 || strcasecmp(value, "false") == 0 ||
            strcasecmp(value, "no") == 0 || strcasecmp(value, "off") == 0) {
            options->has_use_spirv_descriptor_access = 1;
            options->use_spirv_descriptor_access = 0;
            return 0;
        }
        return -1;
    }
    if (strncmp(token, "disable_overlap_aliasing=", 25) == 0) {
        const char *value = token + 25;
        if (strcmp(value, "1") == 0 || strcasecmp(value, "true") == 0 ||
            strcasecmp(value, "yes") == 0 || strcasecmp(value, "on") == 0) {
            options->has_disable_overlap_aliasing = 1;
            options->disable_overlap_aliasing = 1;
            return 0;
        }
        if (strcmp(value, "0") == 0 || strcasecmp(value, "false") == 0 ||
            strcasecmp(value, "no") == 0 || strcasecmp(value, "off") == 0) {
            options->has_disable_overlap_aliasing = 1;
            options->disable_overlap_aliasing = 0;
            return 0;
        }
        return -1;
    }
    if (strncmp(token, "cpu_oracle=", 11) == 0) {
        const char *value = token + 11;
        if (strcmp(value, "1") == 0 || strcasecmp(value, "true") == 0 ||
            strcasecmp(value, "yes") == 0 || strcasecmp(value, "on") == 0) {
            options->has_cpu_oracle = 1;
            options->cpu_oracle = 1;
            return 0;
        }
        if (strcmp(value, "0") == 0 || strcasecmp(value, "false") == 0 ||
            strcasecmp(value, "no") == 0 || strcasecmp(value, "off") == 0) {
            options->has_cpu_oracle = 1;
            options->cpu_oracle = 0;
            return 0;
        }
        return -1;
    }
    if (strncmp(token, "disable_storage8=", 17) == 0) {
        const char *value = token + 17;
        options->disable_storage8 =
            (strcmp(value, "1") == 0 || strcasecmp(value, "true") == 0 ||
             strcasecmp(value, "yes") == 0 || strcasecmp(value, "on") == 0);
        return 0;
    }
    if (strncmp(token, "disable_storage16=", 18) == 0) {
        const char *value = token + 18;
        options->disable_storage16 =
            (strcmp(value, "1") == 0 || strcasecmp(value, "true") == 0 ||
             strcasecmp(value, "yes") == 0 || strcasecmp(value, "on") == 0);
        return 0;
    }
    if (strncmp(token, "disable_subgroup_arithmetic=", 28) == 0) {
        const char *value = token + 28;
        options->disable_subgroup_arithmetic =
            (strcmp(value, "1") == 0 || strcasecmp(value, "true") == 0 ||
             strcasecmp(value, "yes") == 0 || strcasecmp(value, "on") == 0);
        return 0;
    }
    return -1;
}

static VulkanResidentCacheEntry *find_resident_cache_entry(
        dev_t dev, ino_t ino, off_t offset, size_t size, uint32_t binding) {
    for (size_t i = 0; i < PDOCKER_GPU_RESIDENT_CACHE_SLOTS; ++i) {
        VulkanResidentCacheEntry *entry = &g_vulkan_resident_cache[i];
        if (entry->valid && entry->dev == dev && entry->ino == ino &&
            entry->offset == offset && entry->size == size &&
            entry->binding == binding) {
            return entry;
        }
    }
    return NULL;
}

static VulkanResidentCacheEntry *select_resident_cache_slot(VkDevice device) {
    size_t victim = 0;
    for (size_t i = 0; i < PDOCKER_GPU_RESIDENT_CACHE_SLOTS; ++i) {
        if (!g_vulkan_resident_cache[i].valid) return &g_vulkan_resident_cache[i];
        if (g_vulkan_resident_cache[i].hits < g_vulkan_resident_cache[victim].hits) victim = i;
    }
    destroy_vulkan_vector_buffer(device, &g_vulkan_resident_cache[victim].buffer);
    memset(&g_vulkan_resident_cache[victim], 0, sizeof(g_vulkan_resident_cache[victim]));
    return &g_vulkan_resident_cache[victim];
}

static VulkanMutableBufferCacheEntry *find_mutable_buffer_cache_entry(
        dev_t dev, ino_t ino, off_t offset, size_t size, uint32_t binding) {
    for (size_t i = 0; i < PDOCKER_GPU_MUTABLE_BUFFER_CACHE_SLOTS; ++i) {
        VulkanMutableBufferCacheEntry *entry = &g_vulkan_mutable_buffer_cache[i];
        if (entry->valid && !entry->scratch &&
            entry->dev == dev && entry->ino == ino &&
            entry->offset == offset && entry->size == size &&
            entry->binding == binding) {
            return entry;
        }
    }
    return NULL;
}

static VulkanMutableBufferCacheEntry *find_writeonly_scratch_cache_entry(
        size_t size, uint32_t binding) {
    for (size_t i = 0; i < PDOCKER_GPU_MUTABLE_BUFFER_CACHE_SLOTS; ++i) {
        VulkanMutableBufferCacheEntry *entry = &g_vulkan_mutable_buffer_cache[i];
        if (entry->valid && entry->scratch &&
            entry->size == size && entry->binding == binding) {
            return entry;
        }
    }
    return NULL;
}

static VulkanMutableBufferCacheEntry *select_mutable_buffer_cache_slot(VkDevice device) {
    size_t victim = 0;
    for (size_t i = 0; i < PDOCKER_GPU_MUTABLE_BUFFER_CACHE_SLOTS; ++i) {
        if (!g_vulkan_mutable_buffer_cache[i].valid) return &g_vulkan_mutable_buffer_cache[i];
        if (g_vulkan_mutable_buffer_cache[i].hits < g_vulkan_mutable_buffer_cache[victim].hits) victim = i;
    }
    destroy_vulkan_vector_buffer(device, &g_vulkan_mutable_buffer_cache[victim].buffer);
    memset(&g_vulkan_mutable_buffer_cache[victim], 0, sizeof(g_vulkan_mutable_buffer_cache[victim]));
    return &g_vulkan_mutable_buffer_cache[victim];
}

static uint64_t fnv1a64_update(uint64_t hash, const void *data, size_t size) {
    const uint8_t *p = (const uint8_t *)data;
    for (size_t i = 0; i < size; ++i) {
        hash ^= (uint64_t)p[i];
        hash *= 1099511628211ull;
    }
    return hash;
}

static uint64_t pipeline_specialization_hash(
        const VulkanDispatchSpecialization *specializations,
        size_t specialization_count,
        const uint8_t *specialization_data,
        size_t specialization_data_size) {
    uint64_t hash = 1469598103934665603ull;
    hash = fnv1a64_update(hash, &specialization_count, sizeof(specialization_count));
    for (size_t i = 0; i < specialization_count; ++i) {
        hash = fnv1a64_update(hash, &specializations[i], sizeof(specializations[i]));
    }
    hash = fnv1a64_update(hash, &specialization_data_size, sizeof(specialization_data_size));
    if (specialization_data && specialization_data_size) {
        hash = fnv1a64_update(hash, specialization_data, specialization_data_size);
    }
    return hash;
}

static VulkanDirtyMaskCacheEntry *find_dirty_mask_cache_entry(
        uint64_t shader_hash,
        uint64_t spec_hash,
        uint32_t binding,
        size_t size,
        size_t page_size,
        size_t page_count) {
    for (size_t i = 0; i < PDOCKER_GPU_DIRTY_MASK_CACHE_SLOTS; ++i) {
        VulkanDirtyMaskCacheEntry *entry = &g_vulkan_dirty_mask_cache[i];
        if (entry->valid &&
            entry->shader_hash == shader_hash &&
            entry->spec_hash == spec_hash &&
            entry->binding == binding &&
            entry->size == size &&
            entry->page_size == page_size &&
            entry->page_count == page_count) {
            entry->hits++;
            return entry;
        }
    }
    return NULL;
}

static VulkanDirtyMaskCacheEntry *select_dirty_mask_cache_slot(void) {
    size_t victim = 0;
    for (size_t i = 0; i < PDOCKER_GPU_DIRTY_MASK_CACHE_SLOTS; ++i) {
        if (!g_vulkan_dirty_mask_cache[i].valid) return &g_vulkan_dirty_mask_cache[i];
        if (g_vulkan_dirty_mask_cache[i].hits < g_vulkan_dirty_mask_cache[victim].hits) victim = i;
    }
    free(g_vulkan_dirty_mask_cache[victim].dirty_pages);
    memset(&g_vulkan_dirty_mask_cache[victim], 0, sizeof(g_vulkan_dirty_mask_cache[victim]));
    return &g_vulkan_dirty_mask_cache[victim];
}

static void update_dirty_mask_cache(
        uint64_t shader_hash,
        uint64_t spec_hash,
        uint32_t binding,
        size_t size,
        size_t page_size,
        const unsigned char *dirty_pages,
        size_t page_count,
        size_t dirty_page_count,
        size_t dirty_bytes) {
    if (!dirty_pages || page_count == 0 || dirty_page_count == 0) return;
    VulkanDirtyMaskCacheEntry *entry = find_dirty_mask_cache_entry(
        shader_hash, spec_hash, binding, size, page_size, page_count);
    if (!entry) {
        entry = select_dirty_mask_cache_slot();
        unsigned char *copy = (unsigned char *)calloc(page_count, 1);
        if (!copy) return;
        entry->dirty_pages = copy;
    } else if (entry->page_count != page_count) {
        unsigned char *copy = (unsigned char *)calloc(page_count, 1);
        if (!copy) return;
        free(entry->dirty_pages);
        entry->dirty_pages = copy;
    }
    memset(entry->dirty_pages, 0, page_count);
    memcpy(entry->dirty_pages, dirty_pages, page_count);
    entry->valid = 1;
    entry->shader_hash = shader_hash;
    entry->spec_hash = spec_hash;
    entry->binding = binding;
    entry->size = size;
    entry->page_size = page_size;
    entry->page_count = page_count;
    entry->dirty_page_count = dirty_page_count;
    entry->dirty_bytes = dirty_bytes;
    entry->hits++;
}

static uint64_t specialization_value_u64(
        const uint8_t *specialization_data,
        size_t specialization_data_size,
        const VulkanDispatchSpecialization *specialization) {
    if (!specialization || !specialization_data) return 0;
    if (specialization->offset >= specialization_data_size) return 0;
    if (specialization->size == 0 ||
        specialization->offset + specialization->size > specialization_data_size) {
        return 0;
    }
    uint64_t value = 0;
    size_t copy = specialization->size;
    if (copy > sizeof(value)) copy = sizeof(value);
    memcpy(&value, specialization_data + specialization->offset, copy);
    return value;
}

static int specialization_value_for_id(
        const VulkanDispatchSpecialization *specializations,
        size_t specialization_count,
        const uint8_t *specialization_data,
        size_t specialization_data_size,
        uint32_t constant_id,
        uint64_t *out_value) {
    if (!out_value) return 0;
    for (size_t i = 0; i < specialization_count; ++i) {
        if (specializations[i].constant_id != constant_id) continue;
        *out_value = specialization_value_u64(
            specialization_data,
            specialization_data_size,
            &specializations[i]);
        return 1;
    }
    return 0;
}

static int materialize_spirv_specialization_constants(
        uint32_t *code,
        size_t *bytes,
        const VulkanDispatchSpecialization *specializations,
        size_t specialization_count,
        const uint8_t *specialization_data,
        size_t specialization_data_size) {
    if (!code || !bytes || *bytes < 20 || (*bytes % sizeof(uint32_t)) != 0 ||
        code[0] != 0x07230203u || !specializations || specialization_count == 0) {
        return 0;
    }
    const size_t words = *bytes / sizeof(uint32_t);
    const uint32_t bound = code[3];
    if (bound == 0 || bound > 65536) return 0;
    uint32_t *spec_ids = (uint32_t *)calloc(bound, sizeof(uint32_t));
    uint8_t *has_spec_id = (uint8_t *)calloc(bound, sizeof(uint8_t));
    uint8_t *skip_spec_materialization = (uint8_t *)calloc(bound, sizeof(uint8_t));
    uint8_t *workgroup_size_id = (uint8_t *)calloc(bound, sizeof(uint8_t));
    SpirvScalarConstant *scalars = (SpirvScalarConstant *)calloc(bound, sizeof(SpirvScalarConstant));
    SpirvCompositeConstant *composites = (SpirvCompositeConstant *)calloc(bound, sizeof(SpirvCompositeConstant));
    uint32_t *out = (uint32_t *)malloc(*bytes);
    if (!spec_ids || !has_spec_id || !skip_spec_materialization ||
        !workgroup_size_id || !scalars || !composites || !out) {
        free(spec_ids);
        free(has_spec_id);
        free(skip_spec_materialization);
        free(workgroup_size_id);
        free(scalars);
        free(composites);
        free(out);
        return 0;
    }

    for (size_t i = 5; i < words;) {
        uint32_t inst = code[i];
        uint16_t word_count = (uint16_t)(inst >> 16);
        uint16_t op = (uint16_t)(inst & 0xffffu);
        if (word_count == 0 || i + word_count > words) break;
        if (op == 71 && word_count >= 4 && code[i + 1] < bound) {
            if (code[i + 2] == 1) {
                has_spec_id[code[i + 1]] = 1;
                spec_ids[code[i + 1]] = code[i + 3];
            } else if (code[i + 2] == 11 && code[i + 3] == 25) {
                /*
                 * Some ggml Vulkan shaders carry a BuiltIn WorkgroupSize
                 * SpecConstantComposite even when OpExecutionMode uses a
                 * literal LocalSize.  If we materialize the shared SpecId into
                 * that composite while leaving LocalSize unchanged, the shader
                 * observes a gl_WorkGroupSize that does not match the actual
                 * number of local invocations.  Keep that specialization
                 * subtree at its SPIR-V default unless LocalSizeId support is
                 * explicitly implemented.
                 */
                workgroup_size_id[code[i + 1]] = 1;
                skip_spec_materialization[code[i + 1]] = 1;
            }
        }
        i += word_count;
    }
    for (size_t i = 5; i < words;) {
        uint32_t inst = code[i];
        uint16_t word_count = (uint16_t)(inst >> 16);
        uint16_t op = (uint16_t)(inst & 0xffffu);
        if (word_count == 0 || i + word_count > words) break;
        if (op == 51 && word_count >= 3 && code[i + 2] < bound &&
            workgroup_size_id[code[i + 2]]) {
            for (uint16_t j = 3; j < word_count; ++j) {
                if (code[i + j] < bound) {
                    skip_spec_materialization[code[i + j]] = 1;
                }
            }
        }
        i += word_count;
    }

    memcpy(out, code, 5 * sizeof(uint32_t));
    size_t out_words = 5;
    int changed = 0;
    int unsupported = 0;
    for (size_t i = 5; i < words;) {
        uint32_t inst = code[i];
        uint16_t word_count = (uint16_t)(inst >> 16);
        uint16_t op = (uint16_t)(inst & 0xffffu);
        if (word_count == 0 || i + word_count > words) {
            unsupported = 1;
            break;
        }

        if (op == 71 && word_count >= 4 && code[i + 2] == 1 &&
            code[i + 1] < bound && !skip_spec_materialization[code[i + 1]]) {
            changed = 1;
            i += word_count;
            continue;
        }

        if (op == 43 && word_count >= 4 && code[i + 2] < bound) {
            scalars[code[i + 2]].valid = 1;
            scalars[code[i + 2]].value = code[i + 3];
        } else if (op == 44 && word_count >= 3 && code[i + 2] < bound) {
            SpirvCompositeConstant *cc = &composites[code[i + 2]];
            cc->valid = 1;
            cc->count = 0;
            for (uint16_t j = 3; j < word_count && cc->count < 4; ++j) {
                uint32_t id = code[i + j];
                if (id >= bound || !scalars[id].valid) {
                    cc->valid = 0;
                    break;
                }
                cc->values[cc->count++] = scalars[id].value;
            }
        } else if (op == 50 && word_count >= 4 && code[i + 2] < bound &&
                   !skip_spec_materialization[code[i + 2]]) {
            uint64_t value = code[i + 3];
            if (has_spec_id[code[i + 2]]) {
                (void)specialization_value_for_id(
                    specializations, specialization_count,
                    specialization_data, specialization_data_size,
                    spec_ids[code[i + 2]], &value);
            }
            uint32_t rewritten[4] = {
                (4u << 16) | 43u,
                code[i + 1],
                code[i + 2],
                (uint32_t)value,
            };
            memcpy(out + out_words, rewritten, sizeof(rewritten));
            scalars[code[i + 2]].valid = 1;
            scalars[code[i + 2]].value = (uint32_t)value;
            out_words += 4;
            changed = 1;
            i += word_count;
            continue;
        } else if (op == 51 && word_count >= 3 && code[i + 2] < bound &&
                   !skip_spec_materialization[code[i + 2]]) {
            out[out_words++] = ((uint32_t)word_count << 16) | 44u;
            for (uint16_t j = 1; j < word_count; ++j) out[out_words++] = code[i + j];
            SpirvCompositeConstant *cc = &composites[code[i + 2]];
            cc->valid = 1;
            cc->count = 0;
            for (uint16_t j = 3; j < word_count && cc->count < 4; ++j) {
                uint32_t id = code[i + j];
                if (id >= bound || skip_spec_materialization[id] || !scalars[id].valid) {
                    cc->valid = 0;
                    break;
                }
                cc->values[cc->count++] = scalars[id].value;
            }
            changed = 1;
            i += word_count;
            continue;
        } else if (op == 52 && word_count >= 5 && code[i + 2] < bound) {
            int uses_skipped_spec = skip_spec_materialization[code[i + 2]];
            for (uint16_t j = 4; j < word_count; ++j) {
                if (code[i + j] < bound && skip_spec_materialization[code[i + j]]) {
                    uses_skipped_spec = 1;
                    break;
                }
            }
            if (uses_skipped_spec) {
                memcpy(out + out_words, code + i, word_count * sizeof(uint32_t));
                out_words += word_count;
                i += word_count;
                continue;
            }
            uint32_t spec_op = code[i + 3];
            uint64_t value = 0;
            int folded = 0;
            if (spec_op == 134 && word_count == 6) {
                uint32_t a = code[i + 4];
                uint32_t b = code[i + 5];
                if (a < bound && b < bound && scalars[a].valid &&
                    scalars[b].valid && scalars[b].value != 0) {
                    value = scalars[a].value / scalars[b].value;
                    folded = 1;
                }
            } else if (spec_op == 81 && word_count == 6) {
                uint32_t composite = code[i + 4];
                uint32_t index = code[i + 5];
                if (composite < bound && composites[composite].valid &&
                    index < composites[composite].count) {
                    value = composites[composite].values[index];
                    folded = 1;
                }
            }
            if (!folded) {
                unsupported = 1;
                break;
            }
            uint32_t rewritten[4] = {
                (4u << 16) | 43u,
                code[i + 1],
                code[i + 2],
                (uint32_t)value,
            };
            memcpy(out + out_words, rewritten, sizeof(rewritten));
            scalars[code[i + 2]].valid = 1;
            scalars[code[i + 2]].value = (uint32_t)value;
            out_words += 4;
            changed = 1;
            i += word_count;
            continue;
        }

        memcpy(out + out_words, code + i, word_count * sizeof(uint32_t));
        out_words += word_count;
        i += word_count;
    }

    if (changed && !unsupported && out_words <= words) {
        memcpy(code, out, out_words * sizeof(uint32_t));
        *bytes = out_words * sizeof(uint32_t);
    } else {
        changed = 0;
    }
    free(spec_ids);
    free(has_spec_id);
    free(skip_spec_materialization);
    free(workgroup_size_id);
    free(scalars);
    free(composites);
    free(out);
    return changed;
}

static int patch_spirv_literal_local_size_from_spec(
        uint32_t *code,
        size_t bytes,
        const VulkanDispatchSpecialization *specializations,
        size_t specialization_count,
        const uint8_t *specialization_data,
        size_t specialization_data_size) {
    if (!code || bytes < 20 || (bytes % sizeof(uint32_t)) != 0 ||
        code[0] != 0x07230203u || !specializations || specialization_count == 0) {
        return 0;
    }
    const size_t words = bytes / sizeof(uint32_t);
    int has_workgroup_size_builtin = 0;
    for (size_t i = 5; i < words;) {
        uint32_t inst = code[i];
        uint16_t word_count = (uint16_t)(inst >> 16);
        uint16_t op = (uint16_t)(inst & 0xffffu);
        if (word_count == 0 || i + word_count > words) return 0;
        if (op == 71 && word_count >= 4 && code[i + 2] == 11 && code[i + 3] == 25) {
            has_workgroup_size_builtin = 1;
            break;
        }
        i += word_count;
    }
    if (!has_workgroup_size_builtin) return 0;
    uint64_t local_x = 0;
    if (!specialization_value_for_id(specializations,
                                     specialization_count,
                                     specialization_data,
                                     specialization_data_size,
                                     0,
                                     &local_x) ||
        local_x <= 1 || local_x > 1024) {
        return 0;
    }
    for (size_t i = 5; i < words;) {
        uint32_t inst = code[i];
        uint16_t word_count = (uint16_t)(inst >> 16);
        uint16_t op = (uint16_t)(inst & 0xffffu);
        if (word_count == 0 || i + word_count > words) return 0;
        if (op == 16 && word_count >= 6 && code[i + 2] == 17 &&
            code[i + 3] == 1 && code[i + 4] == 1 && code[i + 5] == 1) {
            code[i + 3] = (uint32_t)local_x;
            code[i + 4] = 1;
            code[i + 5] = 1;
            return 1;
        }
        i += word_count;
    }
    return 0;
}

static int rewrite_duplicate_descriptor_bindings(
        uint32_t *code,
        size_t bytes,
        const VulkanDispatchBinding *bindings,
        size_t binding_count,
        VulkanBindingAlias *aliases,
        size_t *alias_count,
        uint32_t *max_binding) {
    if (!code || !aliases || !alias_count || !max_binding || bytes < 20 ||
        (bytes % sizeof(uint32_t)) != 0 || code[0] != 0x07230203u) {
        return 0;
    }
    const size_t words = bytes / sizeof(uint32_t);
    const uint32_t bound = code[3];
    uint8_t used[PDOCKER_GPU_MAX_VULKAN_BINDINGS];
    uint8_t first_seen[PDOCKER_GPU_MAX_VULKAN_BINDINGS];
    uint32_t *descriptor_sets = NULL;
    int ret = -1;
    memset(used, 0, sizeof(used));
    memset(first_seen, 0, sizeof(first_seen));
    descriptor_sets = (uint32_t *)calloc(bound ? bound : 1, sizeof(uint32_t));
    if (!descriptor_sets) return -1;
    for (size_t i = 0; i < binding_count; ++i) {
        if (!bindings) break;
        if (bindings[i].binding >= PDOCKER_GPU_MAX_VULKAN_BINDINGS) goto cleanup;
        used[bindings[i].binding] = 1;
    }

    for (size_t i = 5; i < words;) {
        uint32_t inst = code[i];
        uint16_t word_count = (uint16_t)(inst >> 16);
        uint16_t op = (uint16_t)(inst & 0xffffu);
        if (word_count == 0 || i + word_count > words) break;
        if (op == 71 && word_count >= 4 && code[i + 1] < bound && code[i + 2] == 34) {
            descriptor_sets[code[i + 1]] = code[i + 3];
        }
        i += word_count;
    }

    for (size_t i = 5; i < words;) {
        uint32_t inst = code[i];
        uint16_t word_count = (uint16_t)(inst >> 16);
        uint16_t op = (uint16_t)(inst & 0xffffu);
        if (word_count == 0 || i + word_count > words) break;
        if (op == 71 && word_count >= 4 && code[i + 1] < bound && code[i + 2] == 33) {
            if (descriptor_sets[code[i + 1]] != 0) {
                i += word_count;
                continue;
            }
            uint32_t binding = code[i + 3];
            if (binding >= PDOCKER_GPU_MAX_VULKAN_BINDINGS) goto cleanup;
            used[binding] = 1;
        }
        i += word_count;
    }

    size_t alias_used = 0;
    for (size_t i = 5; i < words;) {
        uint32_t inst = code[i];
        uint16_t word_count = (uint16_t)(inst >> 16);
        uint16_t op = (uint16_t)(inst & 0xffffu);
        if (word_count == 0 || i + word_count > words) break;
        if (op == 71 && word_count >= 4 && code[i + 1] < bound && code[i + 2] == 33) {
            if (descriptor_sets[code[i + 1]] != 0) {
                i += word_count;
                continue;
            }
            uint32_t binding = code[i + 3];
            if (binding >= PDOCKER_GPU_MAX_VULKAN_BINDINGS) goto cleanup;
            if (!first_seen[binding]) {
                first_seen[binding] = 1;
            } else {
                uint32_t alias_binding = UINT32_MAX;
                for (uint32_t candidate = 0; candidate < PDOCKER_GPU_MAX_VULKAN_BINDINGS; ++candidate) {
                    if (!used[candidate]) {
                        alias_binding = candidate;
                        break;
                    }
                }
                if (alias_used >= PDOCKER_GPU_MAX_VULKAN_BINDINGS ||
                    alias_binding == UINT32_MAX) {
                    goto cleanup;
                }
                used[alias_binding] = 1;
                aliases[alias_used].target_id = code[i + 1];
                aliases[alias_used].original_binding = binding;
                aliases[alias_used].rewritten_binding = alias_binding;
                code[i + 3] = alias_binding;
                if (alias_binding > *max_binding) *max_binding = alias_binding;
                ++alias_used;
            }
        }
        i += word_count;
    }
    *alias_count = alias_used;
    ret = 0;
cleanup:
    free(descriptor_sets);
    return ret;
}

static int collect_spirv_descriptor_bindings(
        const uint32_t *code,
        size_t bytes,
        uint8_t *used,
        size_t used_count) {
    if (!used || used_count == 0) return 0;
    memset(used, 0, used_count);
    if (!code || bytes < 20 || (bytes % sizeof(uint32_t)) != 0 ||
        code[0] != 0x07230203u) {
        return 0;
    }
    const size_t words = bytes / sizeof(uint32_t);
    for (size_t i = 5; i < words;) {
        uint32_t inst = code[i];
        uint16_t word_count = (uint16_t)(inst >> 16);
        uint16_t op = (uint16_t)(inst & 0xffffu);
        if (word_count == 0 || i + word_count > words) break;
        if (op == 71 && word_count >= 4 && code[i + 2] == 33) {
            uint32_t binding = code[i + 3];
            if (binding < used_count) used[binding] = 1;
        }
        i += word_count;
    }
    return 0;
}

static int collect_spirv_descriptor_accesses(
        const uint32_t *code,
        size_t bytes,
        SpirvDescriptorAccess *accesses,
        size_t access_count) {
    if (!accesses || access_count == 0) return 0;
    memset(accesses, 0, access_count * sizeof(accesses[0]));
    if (!code || bytes < 20 || (bytes % sizeof(uint32_t)) != 0 ||
        code[0] != 0x07230203u) {
        return 0;
    }
    const size_t words = bytes / sizeof(uint32_t);
    const uint32_t bound = code[3];
    if (bound == 0 || bound > 65536) return 0;
    int32_t *binding_by_id = (int32_t *)malloc(bound * sizeof(binding_by_id[0]));
    int32_t *pointer_target_by_id = (int32_t *)malloc(bound * sizeof(pointer_target_by_id[0]));
    int32_t *variable_type_by_id = (int32_t *)malloc(bound * sizeof(variable_type_by_id[0]));
    uint8_t *non_readable = (uint8_t *)calloc(bound, sizeof(non_readable[0]));
    uint8_t *non_writable = (uint8_t *)calloc(bound, sizeof(non_writable[0]));
    uint8_t *type_non_readable = (uint8_t *)calloc(bound, sizeof(type_non_readable[0]));
    uint8_t *type_non_writable = (uint8_t *)calloc(bound, sizeof(type_non_writable[0]));
    if (!binding_by_id || !pointer_target_by_id || !variable_type_by_id ||
        !non_readable || !non_writable || !type_non_readable || !type_non_writable) {
        free(binding_by_id);
        free(pointer_target_by_id);
        free(variable_type_by_id);
        free(non_readable);
        free(non_writable);
        free(type_non_readable);
        free(type_non_writable);
        return 0;
    }
    for (uint32_t i = 0; i < bound; ++i) {
        binding_by_id[i] = -1;
        pointer_target_by_id[i] = -1;
        variable_type_by_id[i] = -1;
    }
    for (size_t i = 5; i < words;) {
        uint32_t inst = code[i];
        uint16_t word_count = (uint16_t)(inst >> 16);
        uint16_t op = (uint16_t)(inst & 0xffffu);
        if (word_count == 0 || i + word_count > words) break;
        if (op == 71 && word_count >= 3) {
            uint32_t target = code[i + 1];
            uint32_t decoration = code[i + 2];
            if (target < bound) {
                if (decoration == 33 && word_count >= 4) {
                    binding_by_id[target] = (int32_t)code[i + 3];
                } else if (decoration == 24) {
                    non_writable[target] = 1;
                } else if (decoration == 25) {
                    non_readable[target] = 1;
                }
            }
        } else if (op == 72 && word_count >= 4) {
            uint32_t target = code[i + 1];
            uint32_t decoration = code[i + 3];
            if (target < bound) {
                if (decoration == 24) {
                    type_non_writable[target] = 1;
                } else if (decoration == 25) {
                    type_non_readable[target] = 1;
                }
            }
        } else if (op == 32 && word_count >= 4) {
            uint32_t result_id = code[i + 1];
            uint32_t pointee_type = code[i + 3];
            if (result_id < bound && pointee_type < bound) {
                pointer_target_by_id[result_id] = (int32_t)pointee_type;
            }
        } else if (op == 59 && word_count >= 4) {
            uint32_t result_type = code[i + 1];
            uint32_t result_id = code[i + 2];
            if (result_id < bound && result_type < bound) {
                variable_type_by_id[result_id] = (int32_t)result_type;
            }
        }
        i += word_count;
    }
    for (uint32_t id = 0; id < bound; ++id) {
        int32_t binding = binding_by_id[id];
        if (binding < 0 || (size_t)binding >= access_count) continue;
        int is_non_readable = non_readable[id];
        int is_non_writable = non_writable[id];
        int32_t variable_type = variable_type_by_id[id];
        if (variable_type >= 0 && (uint32_t)variable_type < bound) {
            int32_t pointee_type = pointer_target_by_id[variable_type];
            if (pointee_type >= 0 && (uint32_t)pointee_type < bound) {
                is_non_readable |= type_non_readable[pointee_type];
                is_non_writable |= type_non_writable[pointee_type];
            }
        }
        SpirvDescriptorAccess *access = &accesses[binding];
        access->used = 1;
        if (!is_non_readable) access->readable = 1;
        if (!is_non_writable) access->writable = 1;
    }
    free(binding_by_id);
    free(pointer_target_by_id);
    free(variable_type_by_id);
    free(non_readable);
    free(non_writable);
    free(type_non_readable);
    free(type_non_writable);
    return 0;
}

static int binding_index_for_number(const VulkanDispatchBinding *bindings,
                                    size_t binding_count,
                                    uint32_t binding) {
    for (size_t i = 0; i < binding_count; ++i) {
        if (bindings[i].binding == binding) return (int)i;
    }
    return -1;
}

static void destroy_pipeline_cache_entry(VkDevice device, VulkanPipelineCacheEntry *entry) {
    if (!entry || !entry->valid) return;
    if (entry->pipeline) vkDestroyPipeline(device, entry->pipeline, NULL);
    if (entry->shader) vkDestroyShaderModule(device, entry->shader, NULL);
    if (entry->pipeline_layout) vkDestroyPipelineLayout(device, entry->pipeline_layout, NULL);
    if (entry->set_layout) vkDestroyDescriptorSetLayout(device, entry->set_layout, NULL);
    memset(entry, 0, sizeof(*entry));
}

static VulkanPipelineCacheEntry *find_pipeline_cache_entry(
        uint64_t shader_hash,
        uint64_t spec_hash,
        uint64_t policy_hash,
        size_t shader_size,
        size_t specialization_data_size,
        size_t specialization_count,
        uint32_t layout_count,
        uint32_t push_size,
        const char *entry_name) {
    for (size_t i = 0; i < PDOCKER_GPU_PIPELINE_CACHE_SLOTS; ++i) {
        VulkanPipelineCacheEntry *entry = &g_vulkan_pipeline_cache[i];
        if (entry->valid &&
            entry->shader_hash == shader_hash &&
            entry->spec_hash == spec_hash &&
            entry->policy_hash == policy_hash &&
            entry->shader_size == shader_size &&
            entry->specialization_data_size == specialization_data_size &&
            entry->specialization_count == specialization_count &&
            entry->layout_count == layout_count &&
            entry->push_size == push_size &&
            strncmp(entry->entry_name, entry_name, sizeof(entry->entry_name)) == 0) {
            return entry;
        }
    }
    return NULL;
}

static uint64_t vulkan_pipeline_policy_hash(
        int rewrite_duplicate_descriptors,
        int materialize_specialization_constants,
        int specialization_materialized,
        int disable_pipeline_optimization,
        int skip_unused_descriptor_transfers,
        int use_spirv_descriptor_access,
        int disable_overlap_aliasing) {
    /*
     * Keep diagnostic/bridge policy in the pipeline cache key.  Some flags
     * already alter shader bytes or specialization state, but including the
     * explicit policy bits prevents one bisection run from silently reusing a
     * pipeline created under a different executor policy.
     *
     * This stays allocation-free and local to the dispatch hot path.
     */
    uint64_t hash = 1469598103934665603ull;
    const unsigned char bits[] = {
        (unsigned char)(rewrite_duplicate_descriptors ? 1 : 0),
        (unsigned char)(materialize_specialization_constants ? 1 : 0),
        (unsigned char)(specialization_materialized ? 1 : 0),
        (unsigned char)(disable_pipeline_optimization ? 1 : 0),
        (unsigned char)(skip_unused_descriptor_transfers ? 1 : 0),
        (unsigned char)(use_spirv_descriptor_access ? 1 : 0),
        (unsigned char)(disable_overlap_aliasing ? 1 : 0),
    };
    return fnv1a64_update(hash, bits, sizeof(bits));
}

static VulkanPipelineCacheEntry *select_pipeline_cache_slot(VkDevice device) {
    size_t victim = 0;
    for (size_t i = 0; i < PDOCKER_GPU_PIPELINE_CACHE_SLOTS; ++i) {
        if (!g_vulkan_pipeline_cache[i].valid) return &g_vulkan_pipeline_cache[i];
        if (g_vulkan_pipeline_cache[i].hits < g_vulkan_pipeline_cache[victim].hits) victim = i;
    }
    destroy_pipeline_cache_entry(device, &g_vulkan_pipeline_cache[victim]);
    return &g_vulkan_pipeline_cache[victim];
}

static uint64_t sample_memory_hash(const void *data, size_t size) {
    if (!data || size == 0) return 0;
    const unsigned char *bytes = (const unsigned char *)data;
    uint64_t hash = 1469598103934665603ull;
    size_t sample = size < 64 ? size : 64;
    hash = fnv1a64_update(hash, bytes, sample);
    if (size > 128) {
        size_t middle = (size / 2) - (((size / 2) < 32) ? 0 : 32);
        if (middle + sample > size) middle = size - sample;
        hash = fnv1a64_update(hash, bytes + middle, sample);
    }
    if (size > 64) {
        hash = fnv1a64_update(hash, bytes + size - sample, sample);
    }
    return hash;
}

static uint64_t sample_fd_hash(int fd, off_t offset, size_t size) {
    if (fd < 0 || size == 0) return 0;
    unsigned char buf[64];
    uint64_t hash = 1469598103934665603ull;
    size_t sample = size < sizeof(buf) ? size : sizeof(buf);
    ssize_t r = pread(fd, buf, sample, offset);
    if (r <= 0) return 0;
    hash = fnv1a64_update(hash, buf, (size_t)r);
    if (size > 128) {
        off_t middle = offset + (off_t)(size / 2) - (off_t)(((size / 2) < 32) ? 0 : 32);
        if (middle < offset) middle = offset;
        if ((uint64_t)(middle - offset) + sample > size) {
            middle = offset + (off_t)(size - sample);
        }
        r = pread(fd, buf, sample, middle);
        if (r > 0) hash = fnv1a64_update(hash, buf, (size_t)r);
    }
    if (size > 64) {
        r = pread(fd, buf, sample, offset + (off_t)(size - sample));
        if (r > 0) hash = fnv1a64_update(hash, buf, (size_t)r);
    }
    return hash;
}

static float sample_f32_at(const void *data, size_t size, size_t index) {
    float value = 0.0f;
    size_t offset = index * sizeof(float);
    if (!data || offset + sizeof(value) > size) return 0.0f;
    memcpy(&value, (const unsigned char *)data + offset, sizeof(value));
    return value;
}

static void write_f32_sample_array(FILE *out, const void *data, size_t size) {
    fprintf(out, "[");
    const size_t count = size / sizeof(float);
    size_t positions[8];
    size_t position_count = 0;
    if (count > 0) {
        positions[position_count++] = 0;
        if (count > 1) positions[position_count++] = 1;
        if (count > 2) positions[position_count++] = 2;
        if (count > 3) positions[position_count++] = 3;
        if (count > 8) {
            size_t mid = count / 2;
            positions[position_count++] = mid > 1 ? mid - 1 : mid;
            positions[position_count++] = mid;
            positions[position_count++] = count - 2;
            positions[position_count++] = count - 1;
        }
    }
    for (size_t i = 0; i < position_count; ++i) {
        float value = sample_f32_at(data, size, positions[i]);
        fprintf(out,
                "%s{\"index\":%zu,\"value\":",
                i ? "," : "",
                positions[i]);
        if (isfinite(value)) {
            fprintf(out, "%.9g", (double)value);
        } else {
            fprintf(out, "null");
        }
        fprintf(out, "}");
    }
    fprintf(out, "]");
}

static void write_vulkan_binding_report(
        FILE *out,
        const VulkanDispatchBinding *bindings,
        size_t binding_count,
        const uint8_t *active,
        const uint8_t *readable,
        const uint8_t *writable,
        const int *cache_hits,
        const int *cache_resident,
        const int *mutable_cache_hits,
        const int *mutable_cache_reused,
        const double *upload_ms,
        const double *download_ms,
        const size_t *dirty_probe_pages,
        const size_t *dirty_probe_bytes,
        const double *dirty_probe_ms,
        const int *dirty_writeback_cached,
        const size_t *dirty_writeback_bytes,
        const uint64_t *fd_before_hash,
        const uint64_t *gpu_after_upload_hash,
        const uint64_t *gpu_after_dispatch_hash,
        const uint64_t *fd_after_hash,
        const size_t *alias_rep) {
    fprintf(out, "\"binding_details\":[");
    for (size_t i = 0; i < binding_count; ++i) {
        fprintf(out,
                "%s{\"index\":%zu,\"binding\":%u,\"offset\":%lld,"
                "\"size\":%zu,"
                "\"api_offset\":%lld,\"api_range\":%zu,"
                "\"api_buffer_size\":%zu,\"api_descriptor_type\":%u,"
                "\"api_dynamic\":%s,\"api_memory_offset\":%lld,"
                "\"alias_rep\":%zu,\"active\":%s,\"readable\":%s,\"writable\":%s,"
                "\"resident\":%s,\"cache_hit\":%s,"
                "\"mutable_reused\":%s,\"mutable_cache_hit\":%s,"
                "\"upload_ms\":%.4f,\"download_ms\":%.4f,"
                "\"dirty_probe_pages\":%zu,\"dirty_probe_bytes\":%zu,"
                "\"dirty_probe_ms\":%.4f,\"dirty_writeback_cached\":%s,"
                "\"dirty_writeback_bytes\":%zu,"
                "\"fd_before_hash\":\"0x%016llx\","
                "\"gpu_after_upload_hash\":\"0x%016llx\","
                "\"gpu_after_dispatch_hash\":\"0x%016llx\","
                "\"fd_after_hash\":\"0x%016llx\"}",
                i ? "," : "",
                i,
                bindings[i].binding,
                (long long)bindings[i].offset,
                bindings[i].size,
                (long long)bindings[i].api_offset,
                bindings[i].api_range,
                bindings[i].api_buffer_size,
                bindings[i].api_descriptor_type,
                bindings[i].api_dynamic ? "true" : "false",
                (long long)bindings[i].api_memory_offset,
                alias_rep ? alias_rep[i] : i,
                active && active[i] ? "true" : "false",
                readable && readable[i] ? "true" : "false",
                writable && writable[i] ? "true" : "false",
                cache_resident && cache_resident[i] ? "true" : "false",
                cache_hits && cache_hits[i] ? "true" : "false",
                mutable_cache_reused && mutable_cache_reused[i] ? "true" : "false",
                mutable_cache_hits && mutable_cache_hits[i] ? "true" : "false",
                upload_ms ? upload_ms[i] : 0.0,
                download_ms ? download_ms[i] : 0.0,
                dirty_probe_pages ? dirty_probe_pages[i] : (size_t)0,
                dirty_probe_bytes ? dirty_probe_bytes[i] : (size_t)0,
                dirty_probe_ms ? dirty_probe_ms[i] : 0.0,
                dirty_writeback_cached && dirty_writeback_cached[i] ? "true" : "false",
                dirty_writeback_bytes ? dirty_writeback_bytes[i] : (size_t)0,
                (unsigned long long)(fd_before_hash ? fd_before_hash[i] : 0),
                (unsigned long long)(gpu_after_upload_hash ? gpu_after_upload_hash[i] : 0),
                (unsigned long long)(gpu_after_dispatch_hash ? gpu_after_dispatch_hash[i] : 0),
                (unsigned long long)(fd_after_hash ? fd_after_hash[i] : 0));
    }
    fprintf(out, "]");
}

static void write_vulkan_descriptor_write_report(
        FILE *out,
        const uint32_t *dst_bindings,
        const size_t *source_indices,
        const uint32_t *source_bindings,
        const size_t *alias_reps,
        const VkDeviceSize *offsets,
        const VkDeviceSize *ranges,
        const uint8_t *alias_writes,
        size_t write_count) {
    fprintf(out, "\"descriptor_writes\":[");
    for (size_t i = 0; i < write_count; ++i) {
        fprintf(out,
                "%s{\"index\":%zu,\"dst_binding\":%u,"
                "\"source_index\":%zu,\"source_binding\":%u,"
                "\"alias_rep\":%zu,\"offset\":%llu,\"range\":%llu,"
                "\"alias_write\":%s}",
                i ? "," : "",
                i,
                dst_bindings ? dst_bindings[i] : 0,
                source_indices ? source_indices[i] : (size_t)0,
                source_bindings ? source_bindings[i] : 0,
                alias_reps ? alias_reps[i] : (source_indices ? source_indices[i] : (size_t)0),
                (unsigned long long)(offsets ? offsets[i] : 0),
                (unsigned long long)(ranges ? ranges[i] : 0),
                alias_writes && alias_writes[i] ? "true" : "false");
    }
    fprintf(out, "]");
}

static void write_vulkan_descriptor_alias_report(
        FILE *out,
        const VulkanBindingAlias *aliases,
        size_t alias_count) {
    fprintf(out, "\"descriptor_alias_map\":[");
    for (size_t i = 0; i < alias_count; ++i) {
        fprintf(out,
                "%s{\"index\":%zu,\"target_id\":%u,"
                "\"original_binding\":%u,\"rewritten_binding\":%u}",
                i ? "," : "",
                i,
                aliases ? aliases[i].target_id : 0,
                aliases ? aliases[i].original_binding : 0,
                aliases ? aliases[i].rewritten_binding : 0);
    }
    fprintf(out, "]");
}

static int ranges_overlap_off_size(
        off_t a_offset,
        size_t a_size,
        off_t b_offset,
        size_t b_size,
        off_t *overlap_offset,
        size_t *overlap_size) {
    if (a_size == 0 || b_size == 0) return 0;
    if (a_offset < 0 || b_offset < 0) return 0;
    uint64_t a_start = (uint64_t)a_offset;
    uint64_t b_start = (uint64_t)b_offset;
    uint64_t a_end = a_start + (uint64_t)a_size;
    uint64_t b_end = b_start + (uint64_t)b_size;
    if (a_end < a_start || b_end < b_start) return 0;
    if (a_end <= b_start || b_end <= a_start) return 0;
    uint64_t start = a_start > b_start ? a_start : b_start;
    uint64_t end = a_end < b_end ? a_end : b_end;
    if (end <= start) return 0;
    if (overlap_offset) *overlap_offset = (off_t)start;
    if (overlap_size) *overlap_size = (size_t)(end - start);
    return 1;
}

static void write_vulkan_rw_alias_hazard_report(
        FILE *out,
        const VulkanDispatchBinding *bindings,
        size_t binding_count,
        const uint8_t *active,
        const uint8_t *readable,
        const uint8_t *writable,
        const size_t *alias_rep) {
    size_t count = 0;
    for (size_t w = 0; w < binding_count; ++w) {
        if ((active && !active[w]) || !writable || !writable[w]) continue;
        size_t w_rep = alias_rep ? alias_rep[w] : w;
        for (size_t r = 0; r < binding_count; ++r) {
            if (r == w) continue;
            if ((active && !active[r]) || !readable || !readable[r]) continue;
            size_t r_rep = alias_rep ? alias_rep[r] : r;
            if (w_rep != r_rep) continue;
            if (ranges_overlap_off_size(bindings[w].offset, bindings[w].size,
                                        bindings[r].offset, bindings[r].size,
                                        NULL, NULL)) {
                count++;
            }
        }
    }
    fprintf(out, "\"rw_alias_hazards\":{\"count\":%zu,\"items\":[", count);
    int first = 1;
    for (size_t w = 0; w < binding_count; ++w) {
        if ((active && !active[w]) || !writable || !writable[w]) continue;
        size_t w_rep = alias_rep ? alias_rep[w] : w;
        for (size_t r = 0; r < binding_count; ++r) {
            if (r == w) continue;
            if ((active && !active[r]) || !readable || !readable[r]) continue;
            size_t r_rep = alias_rep ? alias_rep[r] : r;
            if (w_rep != r_rep) continue;
            off_t overlap_offset = 0;
            size_t overlap_size = 0;
            if (!ranges_overlap_off_size(bindings[w].offset, bindings[w].size,
                                         bindings[r].offset, bindings[r].size,
                                         &overlap_offset, &overlap_size)) {
                continue;
            }
            fprintf(out,
                    "%s{\"write_index\":%zu,\"write_binding\":%u,"
                    "\"read_index\":%zu,\"read_binding\":%u,"
                    "\"alias_rep\":%zu,\"write_offset\":%lld,"
                    "\"write_size\":%zu,\"read_offset\":%lld,"
                    "\"read_size\":%zu,\"overlap_offset\":%lld,"
                    "\"overlap_size\":%zu}",
                    first ? "" : ",",
                    w,
                    bindings[w].binding,
                    r,
                    bindings[r].binding,
                    w_rep,
                    (long long)bindings[w].offset,
                    bindings[w].size,
                    (long long)bindings[r].offset,
                    bindings[r].size,
                    (long long)overlap_offset,
                    overlap_size);
            first = 0;
        }
    }
    fprintf(out, "]}");
}

static void write_spirv_binding_reflection_report(
        FILE *out,
        const uint8_t *shader_used_bindings,
        const SpirvDescriptorAccess *shader_binding_access,
        const VulkanDispatchBinding *bindings,
        size_t binding_count) {
    fprintf(out, "\"spirv_binding_reflection\":[");
    int first = 1;
    for (uint32_t binding = 0; binding < PDOCKER_GPU_MAX_VULKAN_BINDINGS; ++binding) {
        int declared = shader_used_bindings && shader_used_bindings[binding];
        int readable = shader_binding_access && shader_binding_access[binding].readable;
        int writable = shader_binding_access && shader_binding_access[binding].writable;
        int api_bound = 0;
        size_t api_index = 0;
        size_t api_size = 0;
        off_t api_offset = 0;
        for (size_t i = 0; i < binding_count; ++i) {
            if (bindings[i].binding == binding) {
                api_bound = 1;
                api_index = i;
                api_size = bindings[i].size;
                api_offset = bindings[i].offset;
                break;
            }
        }
        if (!declared && !api_bound) continue;
        fprintf(out,
                "%s{\"binding\":%u,\"shader_declared\":%s,"
                "\"shader_readable\":%s,\"shader_writable\":%s,"
                "\"api_bound\":%s,\"api_index\":%zu,"
                "\"effective_offset\":%lld,\"effective_size\":%zu}",
                first ? "" : ",",
                binding,
                declared ? "true" : "false",
                readable ? "true" : "false",
                writable ? "true" : "false",
                api_bound ? "true" : "false",
                api_index,
                (long long)api_offset,
                api_size);
        first = 0;
    }
    fprintf(out, "]");
}

static int cpu_oracle_known_llama_hash(uint64_t spirv_hash) {
    return spirv_hash == 0x7bf05c459ac87f2bull ||
           spirv_hash == 0x11d5243c43b23a7bull ||
           spirv_hash == 0x11c0523df6c795b8ull ||
           spirv_hash == 0xac41e8033a67af4aull ||
           spirv_hash == 0xf2f988b94bd3e0dcull ||
           spirv_hash == 0x274f68a67dfef210ull ||
           spirv_hash == 0x1bf751845c5dce75ull ||
           spirv_hash == 0x09c4622d92c6acb9ull;
}

static const char *cpu_oracle_kernel_hint(uint64_t spirv_hash) {
    if (spirv_hash == 0x7bf05c459ac87f2bull ||
        spirv_hash == 0x11d5243c43b23a7bull ||
        spirv_hash == 0x11c0523df6c795b8ull) {
        return "small-f32-indexing";
    }
    if (spirv_hash == 0xac41e8033a67af4aull) {
        return "rope-yarn";
    }
    if (spirv_hash == 0xf2f988b94bd3e0dcull) {
        return "rms-norm";
    }
    if (spirv_hash == 0x274f68a67dfef210ull ||
        spirv_hash == 0x1bf751845c5dce75ull ||
        spirv_hash == 0x09c4622d92c6acb9ull) {
        return "mul-mat-vec-q6-k-large";
    }
    return "unknown";
}

typedef struct {
    int requested;
    int candidate;
    int executed;
    int skipped;
    char status[96];
    char kernel_hint[64];
    size_t compared_floats;
    size_t mismatch_count;
    size_t mismatch_iter0;
    size_t mismatch_iter1;
    size_t compared_iter0;
    size_t compared_iter1;
    size_t zero_gpu_mismatch_count;
    double max_abs_error;
    double max_rel_error;
    uint64_t expected_hash;
    uint64_t gpu_hash;
    int input_output_overlap;
    int has_partial_diagnostic;
    int partial_best_lane;
    double partial_best_value;
    double partial_best_abs_error;
    double partial_first16_sum;
    double partial_second16_sum;
    double q6_no_high_bits_sum;
    double q6_unsigned_scales_sum;
    double q6_no_center_sum;
    double q6_packed16_view_sum;
    double q6_packed16_view_abs_delta;
    double q6_shader_like_sum;
    double q6_shader_like_abs_delta;
    double partial_lanes[32];
    size_t partial_lane_count;
    CpuOracleSample row_window[32];
    size_t row_window_count;
    int has_first_mismatch;
    CpuOracleSample first_mismatch;
    CpuOracleSample samples[8];
    size_t sample_count;
} CpuOracleReport;

static uint32_t load_le_u32(const uint8_t *bytes, size_t size, size_t index) {
    size_t offset = index * sizeof(uint32_t);
    if (!bytes || offset + sizeof(uint32_t) > size) return 0;
    uint32_t value = 0;
    memcpy(&value, bytes + offset, sizeof(value));
    return value;
}

static float load_f32_at_index(const uint8_t *bytes, size_t size, size_t index, int *ok) {
    size_t offset = index * sizeof(float);
    float value = 0.0f;
    if (!bytes || offset + sizeof(float) > size) {
        if (ok) *ok = 0;
        return value;
    }
    memcpy(&value, bytes + offset, sizeof(value));
    if (ok) *ok = 1;
    return value;
}

static int32_t load_i32_at_index(const uint8_t *bytes, size_t size, size_t index, int *ok) {
    size_t offset = index * sizeof(int32_t);
    int32_t value = 0;
    if (!bytes || offset + sizeof(int32_t) > size) {
        if (ok) *ok = 0;
        return value;
    }
    memcpy(&value, bytes + offset, sizeof(value));
    if (ok) *ok = 1;
    return value;
}

static float u32_to_f32(uint32_t bits) {
    float value = 0.0f;
    memcpy(&value, &bits, sizeof(value));
    return value;
}

static float f16_to_f32(uint16_t h) {
    uint32_t sign = (uint32_t)(h & 0x8000u) << 16;
    uint32_t exp = (h >> 10) & 0x1fu;
    uint32_t mant = h & 0x03ffu;
    uint32_t bits = 0;
    if (exp == 0) {
        if (mant == 0) {
            bits = sign;
        } else {
            exp = 1;
            while ((mant & 0x0400u) == 0) {
                mant <<= 1;
                exp--;
            }
            mant &= 0x03ffu;
            bits = sign | ((exp + 127u - 15u) << 23) | (mant << 13);
        }
    } else if (exp == 0x1fu) {
        bits = sign | 0x7f800000u | (mant << 13);
    } else {
        bits = sign | ((exp + 127u - 15u) << 23) | (mant << 13);
    }
    return u32_to_f32(bits);
}

static void store_hash_f32(uint64_t *hash, float value) {
    if (!hash) return;
    *hash = fnv1a64_update(*hash, &value, sizeof(value));
}

static int is_power_of_two_u32(uint32_t value) {
    return value != 0 && (value & (value - 1u)) == 0;
}

static uint32_t broadcast_index_u32(uint32_t index, uint32_t dim) {
    if (dim == 0) return 0;
    if (is_power_of_two_u32(dim)) return index & (dim - 1u);
    return index % dim;
}

static void init_cpu_oracle_report(CpuOracleReport *report, int requested, uint64_t spirv_hash) {
    if (!report) return;
    memset(report, 0, sizeof(*report));
    report->requested = requested ? 1 : 0;
    report->candidate = cpu_oracle_known_llama_hash(spirv_hash) ? 1 : 0;
    snprintf(report->kernel_hint, sizeof(report->kernel_hint), "%s",
             cpu_oracle_kernel_hint(spirv_hash));
    snprintf(report->status, sizeof(report->status), "%s",
             requested
                ? (report->candidate ? "scaffold-ready-needs-kernel-implementation"
                                     : "unsupported-shader-hash")
                : "not-requested");
}

static void write_cpu_oracle_report(
        FILE *out,
        const CpuOracleReport *report) {
    CpuOracleReport empty;
    if (!report) {
        init_cpu_oracle_report(&empty, 0, 0);
        report = &empty;
    }
    fprintf(out,
            "\"cpu_oracle\":{\"requested\":%s,"
            "\"candidate\":%s,\"executed\":%s,\"skipped\":%s,"
            "\"status\":\"%s\",\"kernel_hint\":\"%s\","
            "\"compared_floats\":%zu,\"mismatch_count\":%zu,"
            "\"compared_iter0\":%zu,\"compared_iter1\":%zu,"
            "\"mismatch_iter0\":%zu,\"mismatch_iter1\":%zu,"
            "\"zero_gpu_mismatch_count\":%zu,"
            "\"max_abs_error\":%.9g,\"max_rel_error\":%.9g,"
            "\"expected_hash\":\"0x%016llx\",\"gpu_hash\":\"0x%016llx\","
            "\"input_output_overlap\":%s,"
            "\"scope\":\"debug-only-spv-hash-gated\"",
            report->requested ? "true" : "false",
            report->candidate ? "true" : "false",
            report->executed ? "true" : "false",
            report->skipped ? "true" : "false",
            report->status,
            report->kernel_hint,
            report->compared_floats,
            report->mismatch_count,
            report->compared_iter0,
            report->compared_iter1,
            report->mismatch_iter0,
            report->mismatch_iter1,
            report->zero_gpu_mismatch_count,
            report->max_abs_error,
            report->max_rel_error,
            (unsigned long long)report->expected_hash,
            (unsigned long long)report->gpu_hash,
            report->input_output_overlap ? "true" : "false");
    if (report->has_partial_diagnostic) {
        fprintf(out,
                ",\"partial_diagnostic\":{\"best_lane\":%d,"
                "\"best_value\":%.9g,\"best_abs_error\":%.9g,"
                "\"first16_sum\":%.9g,\"second16_sum\":%.9g,"
                "\"q6_no_high_bits_sum\":%.9g,"
                "\"q6_unsigned_scales_sum\":%.9g,"
                "\"q6_no_center_sum\":%.9g,"
                "\"q6_packed16_view_sum\":%.9g,"
                "\"q6_packed16_view_abs_delta\":%.9g,"
                "\"q6_shader_like_sum\":%.9g,"
                "\"q6_shader_like_abs_delta\":%.9g",
                report->partial_best_lane,
                report->partial_best_value,
                report->partial_best_abs_error,
                report->partial_first16_sum,
                report->partial_second16_sum,
                report->q6_no_high_bits_sum,
                report->q6_unsigned_scales_sum,
                report->q6_no_center_sum,
                report->q6_packed16_view_sum,
                report->q6_packed16_view_abs_delta,
                report->q6_shader_like_sum,
                report->q6_shader_like_abs_delta);
        if (report->partial_lane_count > 0) {
            fprintf(out, ",\"partial_lanes\":[");
            for (size_t i = 0; i < report->partial_lane_count; ++i) {
                if (i) fprintf(out, ",");
                fprintf(out, "%.9g", report->partial_lanes[i]);
            }
            fprintf(out, "]");
        }
        fprintf(out, "}");
    }
    if (report->has_first_mismatch) {
        fprintf(out,
                ",\"first_mismatch\":{\"dst_index\":%llu,"
                "\"expected\":%.9g,\"gpu\":%.9g,"
                "\"src0\":%.9g,\"src1\":%.9g,"
                "\"abs_error\":%.9g}",
                (unsigned long long)report->first_mismatch.dst_index,
                report->first_mismatch.expected,
                report->first_mismatch.gpu,
                report->first_mismatch.src0,
                report->first_mismatch.src1,
                report->first_mismatch.abs_error);
    }
    if (report->row_window_count > 0) {
        fprintf(out, ",\"row_window\":[");
        for (size_t i = 0; i < report->row_window_count; ++i) {
            const CpuOracleSample *sample = &report->row_window[i];
            fprintf(out,
                    "%s{\"dst_index\":%llu,\"expected\":%.9g,"
                    "\"half_expected\":%.9g,\"gpu\":%.9g,"
                    "\"abs_error\":%.9g}",
                    i ? "," : "",
                    (unsigned long long)sample->dst_index,
                    sample->expected,
                    sample->src0,
                    sample->gpu,
                    sample->abs_error);
        }
        fprintf(out, "]");
    }
    fprintf(out, ",\"samples\":[");
    for (size_t i = 0; i < report->sample_count; ++i) {
        fprintf(out,
                "%s{\"dst_index\":%llu,\"expected\":%.9g,"
                "\"gpu\":%.9g,\"src0\":%.9g,\"src1\":%.9g,"
                "\"abs_error\":%.9g}",
                i ? "," : "",
                (unsigned long long)report->samples[i].dst_index,
                report->samples[i].expected,
                report->samples[i].gpu,
                report->samples[i].src0,
                report->samples[i].src1,
                report->samples[i].abs_error);
    }
    fprintf(out, "]}");
}


static float rope_yarn_ramp(float low, float high, uint32_t i0) {
    float y = (float)(i0 / 2u) - low;
    float span = high - low;
    if (span < 1.0e-3f) span = 1.0e-3f;
    float t = y / span;
    if (t < 0.0f) t = 0.0f;
    if (t > 1.0f) t = 1.0f;
    return 1.0f - t;
}

static void run_cpu_oracle_rope_yarn(
        CpuOracleReport *report,
        const int *buffer_fds,
        const VulkanDispatchBinding *bindings,
        size_t binding_count,
        VulkanVectorBuffer * const *vk_buffers,
        const size_t *binding_gpu_offset,
        const size_t *binding_alias_rep,
        const uint8_t *active,
        const uint8_t *writable,
        const uint8_t *push,
        size_t push_size,
        uint32_t gx,
        uint32_t gy,
        uint32_t gz) {
    int idx0 = binding_index_for_number(bindings, binding_count, 0);
    int idx1 = binding_index_for_number(bindings, binding_count, 1);
    int idx2 = binding_index_for_number(bindings, binding_count, 2);
    int idx3 = binding_index_for_number(bindings, binding_count, 3);
    int idx4 = binding_index_for_number(bindings, binding_count, 4);
    if (!report || !report->requested) return;
    init_cpu_oracle_report(report, report->requested, 0xac41e8033a67af4aull);
    if (idx0 < 0 || idx1 < 0 || idx2 < 0 || idx3 < 0 || idx4 < 0 ||
        !buffer_fds || !vk_buffers ||
        !vk_buffers[idx0] || !vk_buffers[idx0]->map ||
        !vk_buffers[idx1] || !vk_buffers[idx1]->map ||
        !vk_buffers[idx2] || !vk_buffers[idx2]->map ||
        !vk_buffers[idx3] || !vk_buffers[idx3]->map ||
        !vk_buffers[idx4] || !vk_buffers[idx4]->map || !push || push_size < 27 * sizeof(uint32_t)) {
        report->skipped = 1;
        snprintf(report->status, sizeof(report->status), "%s", "missing-oracle-inputs");
        return;
    }
    const off_t src0_start = bindings[idx0].offset;
    const off_t src0_end = bindings[idx0].offset + (off_t)bindings[idx0].size;
    const off_t src1_start = bindings[idx1].offset;
    const off_t src1_end = bindings[idx1].offset + (off_t)bindings[idx1].size;
    const off_t src2_start = bindings[idx2].offset;
    const off_t src2_end = bindings[idx2].offset + (off_t)bindings[idx2].size;
    const off_t dst_start = bindings[idx3].offset;
    const off_t dst_end = bindings[idx3].offset + (off_t)bindings[idx3].size;
    report->input_output_overlap =
        ((binding_alias_rep && binding_alias_rep[idx0] == binding_alias_rep[idx3]) ||
         buffer_fds[idx0] == buffer_fds[idx3]) &&
        src0_start < dst_end && dst_start < src0_end;
    report->input_output_overlap = report->input_output_overlap ||
        (((binding_alias_rep && binding_alias_rep[idx1] == binding_alias_rep[idx3]) ||
          buffer_fds[idx1] == buffer_fds[idx3]) &&
         src1_start < dst_end && dst_start < src1_end);
    report->input_output_overlap = report->input_output_overlap ||
        (((binding_alias_rep && binding_alias_rep[idx2] == binding_alias_rep[idx3]) ||
          buffer_fds[idx2] == buffer_fds[idx3]) &&
         src2_start < dst_end && dst_start < src2_end);
    if (active && (!active[idx0] || !active[idx1] || !active[idx2] ||
                  !active[idx3] || !active[idx4])) {
        report->skipped = 1;
        snprintf(report->status, sizeof(report->status), "%s", "inactive-oracle-binding");
        return;
    }
    if (writable && !writable[idx3]) {
        report->skipped = 1;
        snprintf(report->status, sizeof(report->status), "%s", "output-binding-not-writable");
        return;
    }
    const size_t oracle_cap = 8u * 1024u * 1024u;
    if (bindings[idx0].size > oracle_cap || bindings[idx1].size > oracle_cap ||
        bindings[idx2].size > oracle_cap || bindings[idx3].size > oracle_cap ||
        bindings[idx4].size > oracle_cap) {
        report->skipped = 1;
        snprintf(report->status, sizeof(report->status), "%s", "binding-too-large-for-oracle");
        return;
    }
    uint8_t *x = (uint8_t *)malloc(bindings[idx0].size);
    uint8_t *y = (uint8_t *)malloc(bindings[idx1].size);
    uint8_t *z = (uint8_t *)malloc(bindings[idx2].size);
    uint8_t *i_buf = (uint8_t *)malloc(bindings[idx4].size);
    uint8_t *dst = (uint8_t *)malloc(bindings[idx3].size);
    if (!x || !y || !z || !i_buf || !dst) {
        free(x);
        free(y);
        free(z);
        free(i_buf);
        free(dst);
        report->skipped = 1;
        snprintf(report->status, sizeof(report->status), "%s", "oracle-oom");
        return;
    }
    if (read_fd_exact(buffer_fds[idx0], x, bindings[idx0].size, bindings[idx0].offset) != 0 ||
        read_fd_exact(buffer_fds[idx1], y, bindings[idx1].size, bindings[idx1].offset) != 0 ||
        read_fd_exact(buffer_fds[idx2], z, bindings[idx2].size, bindings[idx2].offset) != 0 ||
        read_fd_exact(buffer_fds[idx4], i_buf, bindings[idx4].size, bindings[idx4].offset) != 0) {
        free(x); free(y); free(z); free(i_buf); free(dst);
        report->skipped = 1;
        snprintf(report->status, sizeof(report->status), "%s", "oracle-read-failed");
        return;
    }
    uint32_t pc[27];
    for (size_t i = 0; i < 27; ++i) pc[i] = load_le_u32(push, push_size, i);
    const uint32_t n_rows = pc[1];
    const uint32_t n_dims = pc[2];
    const float freq_scale = u32_to_f32(pc[3]);
    const float ext_factor = u32_to_f32(pc[5]);
    const float attn_factor = u32_to_f32(pc[6]);
    const float corr_low = u32_to_f32(pc[7]);
    const float corr_high = u32_to_f32(pc[8]);
    const float theta_scale = u32_to_f32(pc[9]);
    const uint32_t has_ff = pc[10];
    const uint32_t is_back = pc[16];
    const uint32_t set_rows_stride = pc[17];
    const uint32_t ne00 = pc[18];
    const uint32_t ne01 = pc[19];
    const uint32_t ne02 = pc[20];
    const uint32_t nb01 = pc[21];
    const uint32_t nb02 = pc[22];
    const uint32_t nb03 = pc[23];
    const uint32_t nb11 = pc[24];
    const uint32_t nb12 = pc[25];
    const uint32_t nb13 = pc[26];
    if (n_rows == 0 || n_dims == 0 || ne00 == 0 || ne01 == 0 || ne02 == 0) {
        free(x); free(y); free(z); free(i_buf); free(dst);
        report->skipped = 1;
        snprintf(report->status, sizeof(report->status), "%s", "invalid-push-shape");
        return;
    }
    const uint64_t row_plane = (uint64_t)ne01 * (uint64_t)ne02;
    if (row_plane == 0 || row_plane > UINT32_MAX || (n_dims & 1u) != 0) {
        free(x); free(y); free(z); free(i_buf); free(dst);
        report->skipped = 1;
        snprintf(report->status, sizeof(report->status), "%s", "unsupported-push-shape");
        return;
    }
    const uint64_t max_dispatch_invocations =
        (uint64_t)(gx ? gx : 1u) * (uint64_t)(gy ? gy : 1u) *
        (uint64_t)(gz ? gz : 1u) * 256ull;
    const uint64_t max_float_ops = max_dispatch_invocations * 2ull;
    if (max_float_ops > 4ull * 1024ull * 1024ull) {
        free(x); free(y); free(z); free(i_buf); free(dst);
        report->skipped = 1;
        snprintf(report->status, sizeof(report->status), "%s", "dispatch-too-large-for-oracle");
        return;
    }
    memcpy(dst, (const uint8_t *)vk_buffers[idx3]->map + binding_gpu_offset[idx3], bindings[idx3].size);
    report->expected_hash = 1469598103934665603ull;
    report->gpu_hash = 1469598103934665603ull;
    const uint64_t half_n_dims = (uint64_t)n_dims >> 1;
    const uint32_t dispatch_x = gx ? gx : 1u;
    const uint32_t dispatch_y = gy ? gy : 1u;
    const uint32_t dispatch_z = gz ? gz : 1u;
    for (uint32_t zc = 0; zc < dispatch_z; ++zc) {
        for (uint32_t wy = 0; wy < dispatch_y; ++wy) {
            for (uint32_t ly = 0; ly < 256u; ++ly) {
                const uint64_t i0 = 2ull * ((uint64_t)wy * 256ull + (uint64_t)ly);
                if (i0 >= ne00) continue;
                const uint64_t i0_pair = i0 >> 1;
                for (uint32_t wx = 0; wx < dispatch_x; ++wx) {
                    const uint64_t row = (uint64_t)wx + (uint64_t)zc * 32768ull;
                    if (row >= n_rows) continue;
                    const uint64_t i3 = row / row_plane;
                    const uint64_t row_rem = row - i3 * row_plane;
                    const uint64_t i2 = row_rem / ne01;
                    const uint64_t i1 = row_rem - i2 * ne01;
                    uint64_t idst = i0_pair + i1 * nb11 + i2 * nb12 + i3 * nb13;
                    const uint64_t ix = i0_pair + i1 * nb01 + i2 * nb02 + i3 * nb03;
                    if (set_rows_stride != 0) {
                        int row_stride_ok = 0;
                        int32_t row_stride_value = load_i32_at_index(i_buf, bindings[idx4].size,
                                                                     (size_t)(i2 * 2ull),
                                                                     &row_stride_ok);
                        if (!row_stride_ok) {
                            free(x); free(y); free(z); free(i_buf); free(dst);
                            report->skipped = 1;
                            snprintf(report->status, sizeof(report->status), "%s", "oracle-index-out-of-range");
                            return;
                        }
                        idst = i1 * nb11 + i0_pair;
                        idst += (uint64_t)(uint32_t)row_stride_value * (uint64_t)set_rows_stride;
                    }
                    if (i0 >= n_dims) {
                        const uint64_t dst0 = idst + i0_pair;
                        const uint64_t dst1 = idst + i0_pair + 1ull;
                        const uint64_t src0_idx = ix + i0_pair;
                        const uint64_t src1_idx = ix + i0_pair + 1ull;
                        if (dst0 >= (size_t)(bindings[idx3].size / sizeof(float)) ||
                            dst1 >= (size_t)(bindings[idx3].size / sizeof(float)) ||
                            src0_idx >= (size_t)(bindings[idx0].size / sizeof(float)) ||
                            src1_idx >= (size_t)(bindings[idx0].size / sizeof(float))) {
                            free(x); free(y); free(z); free(i_buf); free(dst);
                            report->skipped = 1;
                            snprintf(report->status, sizeof(report->status), "%s", "oracle-index-out-of-range");
                            return;
                        }
                        float expected0 = load_f32_at_index(x, bindings[idx0].size, (size_t)src0_idx, NULL);
                        float expected1 = load_f32_at_index(x, bindings[idx0].size, (size_t)src1_idx, NULL);
                        float gpu0 = load_f32_at_index(dst, bindings[idx3].size, (size_t)dst0, NULL);
                        float gpu1 = load_f32_at_index(dst, bindings[idx3].size, (size_t)dst1, NULL);
                        double abs_error0 = fabs((double)expected0 - (double)gpu0);
                        double abs_error1 = fabs((double)expected1 - (double)gpu1);
                        double rel_error0 = fabs((double)expected0) > 1e-30 ? abs_error0 / fabs((double)expected0) : abs_error0;
                        double rel_error1 = fabs((double)expected1) > 1e-30 ? abs_error1 / fabs((double)expected1) : abs_error1;
                        store_hash_f32(&report->expected_hash, expected0);
                        store_hash_f32(&report->expected_hash, expected1);
                        store_hash_f32(&report->gpu_hash, gpu0);
                        store_hash_f32(&report->gpu_hash, gpu1);
                        if (abs_error0 > report->max_abs_error) report->max_abs_error = abs_error0;
                        if (abs_error1 > report->max_abs_error) report->max_abs_error = abs_error1;
                        if (rel_error0 > report->max_rel_error) report->max_rel_error = rel_error0;
                        if (rel_error1 > report->max_rel_error) report->max_rel_error = rel_error1;
                        if (abs_error0 > 1.0e-5 || abs_error1 > 1.0e-5) {
                            if (report->sample_count < sizeof(report->samples) / sizeof(report->samples[0])) {
                                CpuOracleSample *sample0 = &report->samples[report->sample_count++];
                                sample0->dst_index = dst0;
                                sample0->expected = expected0;
                                sample0->gpu = gpu0;
                                sample0->src0 = expected0;
                                sample0->src1 = expected1;
                                sample0->abs_error = abs_error0;
                            }
                        }
                        if (!report->has_first_mismatch && abs_error0 > 1e-5) {
                            report->has_first_mismatch = 1;
                            report->first_mismatch.dst_index = dst0;
                            report->first_mismatch.expected = expected0;
                            report->first_mismatch.gpu = gpu0;
                            report->first_mismatch.src0 = expected0;
                            report->first_mismatch.src1 = expected1;
                            report->first_mismatch.abs_error = abs_error0;
                        }
                        report->compared_floats += 2;
                        report->compared_iter0 += 1;
                        if (abs_error0 > 1e-5) {
                            report->mismatch_count += 1;
                            report->mismatch_iter0 += 1;
                            if (gpu0 == 0.0f && expected0 != 0.0f) report->zero_gpu_mismatch_count += 1;
                        }
                        if (abs_error1 > 1e-5) {
                            report->mismatch_count += 1;
                            report->mismatch_iter1 += 1;
                            if (gpu1 == 0.0f && expected1 != 0.0f) report->zero_gpu_mismatch_count += 1;
                        }
                        continue;
                    }
                    if (i0_pair >= half_n_dims) {
                        free(x); free(y); free(z); free(i_buf); free(dst);
                        report->skipped = 1;
                        snprintf(report->status, sizeof(report->status), "%s", "invalid-push-shape");
                        return;
                    }
                    size_t y_index = (size_t)i2;
                    if (y_index >= bindings[idx1].size / sizeof(int32_t)) {
                        free(x); free(y); free(z); free(i_buf); free(dst);
                        report->skipped = 1;
                        snprintf(report->status, sizeof(report->status), "%s", "oracle-index-out-of-range");
                        return;
                    }
                    int y_ok = 0;
                    int32_t y_raw = load_i32_at_index(y, bindings[idx1].size, y_index, &y_ok);
                    if (!y_ok) {
                        free(x); free(y); free(z); free(i_buf); free(dst);
                        report->skipped = 1;
                        snprintf(report->status, sizeof(report->status), "%s", "oracle-index-out-of-range");
                        return;
                    }
                    float theta_base = (float)y_raw * powf(theta_scale, (float)i0_pair);
                    float freq_factor = 1.0f;
                    if (has_ff != 0) {
                        if (i0_pair >= (uint64_t)bindings[idx2].size / sizeof(float)) {
                            free(x); free(y); free(z); free(i_buf); free(dst);
                            report->skipped = 1;
                            snprintf(report->status, sizeof(report->status), "%s", "oracle-index-out-of-range");
                            return;
                        }
                        freq_factor = load_f32_at_index(z, bindings[idx2].size, (size_t)i0_pair, NULL);
                    }
                    if (freq_factor == 0.0f) {
                        free(x); free(y); free(z); free(i_buf); free(dst);
                        report->skipped = 1;
                        snprintf(report->status, sizeof(report->status), "%s", "invalid-push-shape");
                        return;
                    }
                    const float theta_extrap = theta_base / freq_factor;
                    float theta = freq_scale * theta_extrap;
                    float mscale = attn_factor;
                    if (fabsf(ext_factor) > 0.0f) {
                        const float ramp = rope_yarn_ramp(corr_low, corr_high, (uint32_t)i0);
                        const float ramp_mix = ext_factor * ramp;
                        theta = theta * (1.0f - ramp_mix) + theta_extrap * ramp_mix;
                        if (freq_scale > 0.0f) {
                            mscale *= 1.0f + 0.1f * logf(1.0f / freq_scale);
                        }
                    }
                    if (is_back) theta = -theta;
                    float cos_theta = cosf(theta) * mscale;
                    float sin_theta = sinf(theta) * mscale;
                    const uint64_t src0_idx = ix;
                    const uint64_t src1_idx = ix + half_n_dims;
                    if (src0_idx >= (size_t)(bindings[idx0].size / sizeof(float)) ||
                        src1_idx >= (size_t)(bindings[idx0].size / sizeof(float))) {
                        free(x); free(y); free(z); free(i_buf); free(dst);
                        report->skipped = 1;
                        snprintf(report->status, sizeof(report->status), "%s", "oracle-index-out-of-range");
                        return;
                    }
                    float x0 = load_f32_at_index(x, bindings[idx0].size, src0_idx, NULL);
                    float x1 = load_f32_at_index(x, bindings[idx0].size, src1_idx, NULL);
                    uint64_t dst0 = idst;
                    uint64_t dst1 = idst + half_n_dims;
                    if (dst0 >= (size_t)(bindings[idx3].size / sizeof(float)) ||
                        dst1 >= (size_t)(bindings[idx3].size / sizeof(float))) {
                        free(x); free(y); free(z); free(i_buf); free(dst);
                        report->skipped = 1;
                        snprintf(report->status, sizeof(report->status), "%s", "oracle-index-out-of-range");
                        return;
                    }
                    float expected0 = x0 * cos_theta - x1 * sin_theta;
                    float expected1 = x0 * sin_theta + x1 * cos_theta;
                    float gpu0 = load_f32_at_index(dst, bindings[idx3].size, dst0, NULL);
                    float gpu1 = load_f32_at_index(dst, bindings[idx3].size, dst1, NULL);
                    store_hash_f32(&report->expected_hash, expected0);
                    store_hash_f32(&report->expected_hash, expected1);
                    store_hash_f32(&report->gpu_hash, gpu0);
                    store_hash_f32(&report->gpu_hash, gpu1);
                    double abs_error0 = fabs((double)expected0 - (double)gpu0);
                    double abs_error1 = fabs((double)expected1 - (double)gpu1);
                    double rel_error0 = fabs((double)expected0) > 1e-30 ? abs_error0 / fabs((double)expected0) : abs_error0;
                    double rel_error1 = fabs((double)expected1) > 1e-30 ? abs_error1 / fabs((double)expected1) : abs_error1;
                    if (report->sample_count < sizeof(report->samples) / sizeof(report->samples[0])) {
                        CpuOracleSample *sample0 = &report->samples[report->sample_count++];
                        sample0->dst_index = dst0;
                        sample0->expected = expected0;
                        sample0->gpu = gpu0;
                        sample0->src0 = x0;
                        sample0->src1 = x1;
                        sample0->abs_error = abs_error0;
                    }
                    if (!report->has_first_mismatch && abs_error0 > 1e-5) {
                        report->has_first_mismatch = 1;
                        report->first_mismatch.dst_index = dst0;
                        report->first_mismatch.expected = expected0;
                        report->first_mismatch.gpu = gpu0;
                        report->first_mismatch.src0 = x0;
                        report->first_mismatch.src1 = x1;
                        report->first_mismatch.abs_error = abs_error0;
                    }
                    report->compared_floats += 2;
                    report->compared_iter0 += 1;
                    if (abs_error0 > 1e-5 && rel_error0 > 1e-4) {
                        report->mismatch_count++;
                        report->mismatch_iter0++;
                        if (gpu0 == 0.0f && expected0 != 0.0f) report->zero_gpu_mismatch_count++;
                    }
                    if (abs_error1 > 1e-5 && rel_error1 > 1e-4) {
                        if (report->sample_count < sizeof(report->samples) / sizeof(report->samples[0])) {
                            CpuOracleSample *sample1 = &report->samples[report->sample_count++];
                            sample1->dst_index = dst1;
                            sample1->expected = expected1;
                            sample1->gpu = gpu1;
                            sample1->src0 = x0;
                            sample1->src1 = x1;
                            sample1->abs_error = abs_error1;
                        }
                        if (!report->has_first_mismatch && abs_error1 > 1e-5) {
                            report->has_first_mismatch = 1;
                            report->first_mismatch.dst_index = dst1;
                            report->first_mismatch.expected = expected1;
                            report->first_mismatch.gpu = gpu1;
                            report->first_mismatch.src0 = x0;
                            report->first_mismatch.src1 = x1;
                            report->first_mismatch.abs_error = abs_error1;
                        }
                        report->mismatch_count++;
                        report->mismatch_iter1++;
                        if (gpu1 == 0.0f && expected1 != 0.0f) report->zero_gpu_mismatch_count++;
                        if (rel_error1 > report->max_rel_error) report->max_rel_error = rel_error1;
                    }
                    if (abs_error0 > report->max_abs_error) report->max_abs_error = abs_error0;
                    if (abs_error1 > report->max_abs_error) report->max_abs_error = abs_error1;
                    if (rel_error0 > report->max_rel_error) report->max_rel_error = rel_error0;
                }
            }
        }
    }
    report->executed = 1;
    report->skipped = 0;
    snprintf(report->status, sizeof(report->status), "%s", report->mismatch_count ? "mismatch" : "match");
    free(x); free(y); free(z); free(i_buf); free(dst);
}

static void run_cpu_oracle_rms_norm(
        CpuOracleReport *report,
        const int *buffer_fds,
        const VulkanDispatchBinding *bindings,
        size_t binding_count,
        VulkanVectorBuffer * const *vk_buffers,
        const size_t *binding_gpu_offset,
        const size_t *binding_alias_rep,
        const uint8_t *active,
        const uint8_t *writable,
        const VulkanDispatchSpecialization *specializations,
        size_t specialization_count,
        const uint8_t *specialization_data,
        size_t specialization_data_size,
        const uint8_t *push,
        size_t push_size,
        uint32_t gx,
        uint32_t gy,
        uint32_t gz) {
    int idx0 = binding_index_for_number(bindings, binding_count, 0);
    int idx1 = binding_index_for_number(bindings, binding_count, 1);
    int idx2 = binding_index_for_number(bindings, binding_count, 2);
    if (!report || !report->requested) return;
    init_cpu_oracle_report(report, report->requested, 0xf2f988b94bd3e0dcull);
    if (idx0 < 0 || idx1 < 0 || idx2 < 0 || !buffer_fds || !vk_buffers ||
        !vk_buffers[idx2] || !vk_buffers[idx2]->map || !push ||
        push_size < 29 * sizeof(uint32_t)) {
        report->skipped = 1;
        snprintf(report->status, sizeof(report->status), "%s", "missing-oracle-inputs");
        return;
    }
    const off_t src0_start = bindings[idx0].offset;
    const off_t src0_end = bindings[idx0].offset + (off_t)bindings[idx0].size;
    const off_t dst_start = bindings[idx2].offset;
    const off_t dst_end = bindings[idx2].offset + (off_t)bindings[idx2].size;
    report->input_output_overlap =
        (((binding_alias_rep && binding_alias_rep[idx0] == binding_alias_rep[idx2]) ||
          buffer_fds[idx0] == buffer_fds[idx2]) &&
         src0_start < dst_end && dst_start < src0_end);
    if (active && (!active[idx0] || !active[idx1] || !active[idx2])) {
        report->skipped = 1;
        snprintf(report->status, sizeof(report->status), "%s", "inactive-oracle-binding");
        return;
    }
    if (writable && !writable[idx2]) {
        report->skipped = 1;
        snprintf(report->status, sizeof(report->status), "%s", "output-binding-not-writable");
        return;
    }
    const size_t oracle_cap = 8u * 1024u * 1024u;
    if (bindings[idx0].size > oracle_cap || bindings[idx1].size > oracle_cap ||
        bindings[idx2].size > oracle_cap) {
        report->skipped = 1;
        snprintf(report->status, sizeof(report->status), "%s", "binding-too-large-for-oracle");
        return;
    }
    uint8_t *src0 = (uint8_t *)malloc(bindings[idx0].size);
    uint8_t *src1 = (uint8_t *)malloc(bindings[idx1].size);
    uint8_t *dst = (uint8_t *)malloc(bindings[idx2].size);
    if (!src0 || !src1 || !dst) {
        free(src0);
        free(src1);
        free(dst);
        report->skipped = 1;
        snprintf(report->status, sizeof(report->status), "%s", "oracle-oom");
        return;
    }
    if (read_fd_exact(buffer_fds[idx0], src0, bindings[idx0].size, bindings[idx0].offset) != 0 ||
        read_fd_exact(buffer_fds[idx1], src1, bindings[idx1].size, bindings[idx1].offset) != 0) {
        free(src0);
        free(src1);
        free(dst);
        report->skipped = 1;
        snprintf(report->status, sizeof(report->status), "%s", "oracle-read-failed");
        return;
    }
    memcpy(dst, (const uint8_t *)vk_buffers[idx2]->map + binding_gpu_offset[idx2], bindings[idx2].size);
    uint32_t pc[29];
    for (size_t i = 0; i < 29; ++i) pc[i] = load_le_u32(push, push_size, i);
    const uint32_t ncols = pc[1];
    const uint32_t ne10 = pc[9];
    const uint32_t ne11 = pc[10];
    const uint32_t ne12 = pc[11];
    const uint32_t ne13 = pc[12];
    const uint32_t nb11 = pc[14];
    const uint32_t nb12 = pc[15];
    const uint32_t nb13 = pc[16];
    const uint32_t nb01 = pc[6];
    const uint32_t nb02 = pc[7];
    const uint32_t nb03 = pc[8];
    const uint32_t misalign_offsets = pc[25];
    const uint32_t aoffset = misalign_offsets >> 16u;
    const uint32_t boffset = (misalign_offsets >> 8u) & 255u;
    const uint32_t doffset = misalign_offsets & 255u;
    const float eps = u32_to_f32(pc[26]);
    uint64_t spec_value = 0;
    const int norepeat =
        specialization_value_for_id(specializations, specialization_count,
                                    specialization_data, specialization_data_size,
                                    0, &spec_value) && spec_value != 0;
    spec_value = 0;
    const int do_multiply =
        specialization_value_for_id(specializations, specialization_count,
                                    specialization_data, specialization_data_size,
                                    1, &spec_value) && spec_value != 0;
    if (ncols == 0 || gx == 0 || gy == 0 || gz == 0) {
        free(src0);
        free(src1);
        free(dst);
        report->skipped = 1;
        snprintf(report->status, sizeof(report->status), "%s", "invalid-push-shape");
        return;
    }
    const uint64_t total_rows = (uint64_t)gx * (uint64_t)gy * (uint64_t)gz;
    if (total_rows * (uint64_t)ncols > 4ull * 1024ull * 1024ull) {
        free(src0);
        free(src1);
        free(dst);
        report->skipped = 1;
        snprintf(report->status, sizeof(report->status), "%s", "dispatch-too-large-for-oracle");
        return;
    }
    report->expected_hash = 1469598103934665603ull;
    report->gpu_hash = 1469598103934665603ull;
    for (uint32_t samp = 0; samp < gz; ++samp) {
        for (uint32_t channel = 0; channel < gy; ++channel) {
            for (uint32_t row = 0; row < gx; ++row) {
                const uint64_t a_base = (uint64_t)samp * nb03 +
                                        (uint64_t)channel * nb02 +
                                        (uint64_t)row * nb01 + aoffset;
                const uint64_t d_base = (((uint64_t)samp * gy + channel) * gx + row) *
                                        (uint64_t)ncols + doffset;
                uint64_t b_base = boffset;
                if (norepeat) {
                    b_base += (uint64_t)samp * nb13 +
                              (uint64_t)channel * nb12 +
                              (uint64_t)row * nb11;
                } else {
                    b_base += (uint64_t)broadcast_index_u32(samp, ne13) * nb13 +
                              (uint64_t)broadcast_index_u32(channel, ne12) * nb12 +
                              (uint64_t)broadcast_index_u32(row, ne11) * nb11;
                }
                double sum_sq = 0.0;
                for (uint32_t col = 0; col < ncols; ++col) {
                    int ok = 0;
                    float value = load_f32_at_index(src0, bindings[idx0].size,
                                                    (size_t)(a_base + col), &ok);
                    if (!ok) {
                        free(src0);
                        free(src1);
                        free(dst);
                        report->skipped = 1;
                        snprintf(report->status, sizeof(report->status), "%s", "oracle-index-out-of-range");
                        return;
                    }
                    sum_sq += (double)value * (double)value;
                }
                const float scale = 1.0f / sqrtf((float)(sum_sq / (double)ncols) + eps);
                for (uint32_t col = 0; col < ncols; ++col) {
                    const uint64_t src0_idx = a_base + col;
                    const uint64_t dst_idx = d_base + col;
                    uint64_t src1_idx = b_base + col;
                    if (do_multiply && ncols > ne10) {
                        src1_idx = b_base + broadcast_index_u32(col, ne10);
                    }
                    int ok0 = 0, ok1 = 1, ok2 = 0;
                    float a = load_f32_at_index(src0, bindings[idx0].size, (size_t)src0_idx, &ok0);
                    float b = 1.0f;
                    if (do_multiply) {
                        b = load_f32_at_index(src1, bindings[idx1].size, (size_t)src1_idx, &ok1);
                    }
                    float gpu = load_f32_at_index(dst, bindings[idx2].size, (size_t)dst_idx, &ok2);
                    if (!ok0 || !ok1 || !ok2) {
                        free(src0);
                        free(src1);
                        free(dst);
                        report->skipped = 1;
                        snprintf(report->status, sizeof(report->status), "%s", "oracle-index-out-of-range");
                        return;
                    }
                    float expected = scale * a * b;
                    store_hash_f32(&report->expected_hash, expected);
                    store_hash_f32(&report->gpu_hash, gpu);
                    double abs_error = fabs((double)expected - (double)gpu);
                    double denom = fabs((double)expected);
                    double rel_error = denom > 1e-30 ? abs_error / denom : abs_error;
                    if (abs_error > report->max_abs_error) report->max_abs_error = abs_error;
                    if (rel_error > report->max_rel_error) report->max_rel_error = rel_error;
                    if (report->sample_count < sizeof(report->samples) / sizeof(report->samples[0])) {
                        CpuOracleSample *sample = &report->samples[report->sample_count++];
                        sample->dst_index = dst_idx;
                        sample->expected = expected;
                        sample->gpu = gpu;
                        sample->src0 = a;
                        sample->src1 = b;
                        sample->abs_error = abs_error;
                    }
                    if (abs_error > 1e-5 && rel_error > 1e-4) {
                        if (!report->has_first_mismatch) {
                            report->has_first_mismatch = 1;
                            report->first_mismatch.dst_index = dst_idx;
                            report->first_mismatch.expected = expected;
                            report->first_mismatch.gpu = gpu;
                            report->first_mismatch.src0 = a;
                            report->first_mismatch.src1 = b;
                            report->first_mismatch.abs_error = abs_error;
                        }
                        report->mismatch_count++;
                        if (gpu == 0.0f && expected != 0.0f) {
                            report->zero_gpu_mismatch_count++;
                        }
                    }
                    report->compared_floats++;
                    report->compared_iter0++;
                }
            }
        }
    }
    report->executed = 1;
    report->skipped = 0;
    snprintf(report->status, sizeof(report->status), "%s",
             report->mismatch_count ? "mismatch" : "match");
    free(src0);
    free(src1);
    free(dst);
}

static int q6k_read_block(const int fd, off_t block_offset, uint8_t block[210]) {
    size_t done = 0;
    while (done < 210) {
        ssize_t n = pread(fd, block + done, 210 - done, block_offset + (off_t)done);
        if (n < 0) {
            if (errno == EINTR) continue;
            return 0;
        }
        if (n == 0) return 0;
        done += (size_t)n;
    }
    return 1;
}

static float q6k_value_at(const uint8_t block[210], uint32_t k) {
    const uint32_t pos = k & 127u;
    const uint32_t half = k >> 7;
    const uint32_t ql_index = half * 64u + (pos & 63u);
    const uint32_t qh_index = 128u + half * 32u + (pos & 31u);
    const uint32_t scale_index = 192u + (k >> 4);
    const uint8_t ql_byte = block[ql_index];
    const uint8_t qh_byte = block[qh_index];
    const uint32_t low4 = (pos < 64u) ? (ql_byte & 0x0fu) : ((ql_byte >> 4) & 0x0fu);
    const uint32_t high2 = (qh_byte >> (2u * ((pos >> 5) & 3u))) & 0x03u;
    const int32_t q = (int32_t)(low4 | (high2 << 4)) - 32;
    const int32_t scale = (int8_t)block[scale_index];
    uint16_t d_bits = 0;
    memcpy(&d_bits, block + 208, sizeof(d_bits));
    return f16_to_f32(d_bits) * (float)scale * (float)q;
}

static float q6k_value_variant_at(const uint8_t block[210], uint32_t k, int variant) {
    const uint32_t pos = k & 127u;
    const uint32_t half = k >> 7;
    const uint32_t ql_index = half * 64u + (pos & 63u);
    const uint32_t qh_index = 128u + half * 32u + (pos & 31u);
    const uint32_t scale_index = 192u + (k >> 4);
    const uint8_t ql_byte = block[ql_index];
    const uint8_t qh_byte = block[qh_index];
    const uint32_t low4 = (pos < 64u) ? (ql_byte & 0x0fu) : ((ql_byte >> 4) & 0x0fu);
    uint32_t high2 = (qh_byte >> (2u * ((pos >> 5) & 3u))) & 0x03u;
    if (variant == 1) high2 = 0;
    int32_t q = (int32_t)(low4 | (high2 << 4));
    if (variant != 3) q -= 32;
    const int32_t scale = variant == 2 ? (int32_t)block[scale_index] : (int32_t)(int8_t)block[scale_index];
    uint16_t d_bits = 0;
    memcpy(&d_bits, block + 208, sizeof(d_bits));
    return f16_to_f32(d_bits) * (float)scale * (float)q;
}

static uint16_t load_le_u16_unaligned(const uint8_t *bytes) {
    uint16_t value = 0;
    memcpy(&value, bytes, sizeof(value));
    return value;
}

static uint32_t load_le_u32_unaligned(const uint8_t *bytes) {
    uint32_t value = 0;
    memcpy(&value, bytes, sizeof(value));
    return value;
}

static int32_t unpack8_u32_lane(uint32_t value, uint32_t lane) {
    return (int32_t)((value >> ((lane & 3u) * 8u)) & 0xffu);
}

static float q6k_value_packed16_view_at(const uint8_t block[210], uint32_t idx) {
    const uint32_t b = (idx & 0x40u) >> 6;
    const uint32_t qhshift = (idx & 0x60u) >> 4;
    const uint32_t is = (idx & 0xF0u) >> 4;
    const uint32_t ql_word = ((idx & 0x80u) >> 2) + ((idx & 0x3Eu) >> 1);
    const uint32_t qh_word = 128u / 2u + ((idx & 0x80u) >> 3) + ((idx & 0x1Eu) >> 1);
    uint32_t ql = load_le_u16_unaligned(block + ql_word * 2u);
    ql = (ql >> (b * 4u)) & 0x0F0Fu;
    uint32_t qh = load_le_u16_unaligned(block + qh_word * 2u);
    qh = ((qh >> qhshift) & 0x0303u) << 4;
    const uint32_t byte_pair = ql | qh;
    const int32_t q = (int32_t)((byte_pair >> ((idx & 1u) * 8u)) & 0xffu) - 32;
    const int32_t scale = (int8_t)block[192u + is];
    uint16_t d_bits = 0;
    memcpy(&d_bits, block + 208, sizeof(d_bits));
    return f16_to_f32(d_bits) * (float)scale * (float)q;
}

static int q6k_accumulate_tid_partial_shader_like(
        const int weight_fd,
        const int vector_fd,
        const VulkanDispatchBinding *weight_binding,
        const VulkanDispatchBinding *vector_binding,
        uint32_t row,
        uint32_t tid,
        uint32_t ncols,
        uint32_t batch_stride_b,
        uint32_t base_work_group_y,
        double *out_sum) {
    if (!weight_binding || !vector_binding || !out_sum || ncols == 0 || (ncols % 256u) != 0) {
        return 0;
    }
    const uint32_t num_blocks_per_row = ncols / 256u;
    const uint32_t it_size = 32u / 16u;
    const uint32_t itid = tid % 16u;
    const uint32_t ix = tid / 16u;
    const uint32_t v_im = itid / 8u;
    const uint32_t v_in = itid - 8u * v_im;
    const uint32_t l0 = 4u * v_in;
    const uint32_t is = v_in / 4u;
    const uint32_t ql_offset = 64u * v_im + l0;
    const uint32_t qh_offset = 32u * v_im + l0;
    const uint32_t s_offset = 8u * v_im + is;
    const uint32_t y_offset = 128u * v_im + l0;
    float temp = 0.0f;
    for (uint32_t i = ix; i < num_blocks_per_row; i += it_size) {
        uint8_t block[210];
        off_t off = weight_binding->offset +
                    (off_t)(((uint64_t)row * num_blocks_per_row + i) * 210ull);
        if (!q6k_read_block(weight_fd, off, block)) return 0;
        const uint32_t ql0_u32 = load_le_u32_unaligned(block + ql_offset);
        const uint32_t ql32_u32 = load_le_u32_unaligned(block + ql_offset + 32u);
        const uint32_t ql0_u32_lo4 = ql0_u32 & 0x0F0F0F0Fu;
        const uint32_t ql0_u32_hi4 = (ql0_u32 >> 4) & 0x0F0F0F0Fu;
        const uint32_t ql32_u32_lo4 = ql32_u32 & 0x0F0F0F0Fu;
        const uint32_t ql32_u32_hi4 = (ql32_u32 >> 4) & 0x0F0F0F0Fu;
        const uint32_t qh_u32 = load_le_u32_unaligned(block + 128u + qh_offset);
        const uint32_t qh0_u32 = (qh_u32 & 0x03030303u) << 4;
        const uint32_t qh2_u32 = (qh_u32 & 0x0C0C0C0Cu) << 2;
        const uint32_t qh4_u32 = (qh_u32 & 0x30303030u);
        const uint32_t qh6_u32 = (qh_u32 & 0xC0C0C0C0u) >> 2;
        const uint32_t q_u32[4] = {
            ql0_u32_lo4 | qh0_u32,
            ql32_u32_lo4 | qh2_u32,
            ql0_u32_hi4 | qh4_u32,
            ql32_u32_hi4 | qh6_u32,
        };
        const int32_t scales[4] = {
            (int8_t)block[192u + s_offset],
            (int8_t)block[192u + s_offset + 2u],
            (int8_t)block[192u + s_offset + 4u],
            (int8_t)block[192u + s_offset + 6u],
        };
        uint16_t d_bits = 0;
        memcpy(&d_bits, block + 208, sizeof(d_bits));
        const float d = f16_to_f32(d_bits);
        float sum[4] = {0.0f, 0.0f, 0.0f, 0.0f};
        for (uint32_t l = 0; l < 4u; ++l) {
            const uint32_t b_indices[4] = {
                i * 256u + y_offset + l,
                i * 256u + y_offset + 32u + l,
                i * 256u + y_offset + 64u + l,
                i * 256u + y_offset + 96u + l,
            };
            for (uint32_t q = 0; q < 4u; ++q) {
                float b = 0.0f;
                const off_t b_off = vector_binding->offset +
                                    (off_t)((uint64_t)base_work_group_y * batch_stride_b * sizeof(float)) +
                                    (off_t)((uint64_t)b_indices[q] * sizeof(float));
                if (pread(vector_fd, &b, sizeof(b), b_off) != (ssize_t)sizeof(b)) return 0;
                sum[q] = fmaf(b, (float)(unpack8_u32_lane(q_u32[q], l) - 32), sum[q]);
            }
        }
        const float scaled =
            fmaf(sum[0], (float)scales[0],
                 fmaf(sum[1], (float)scales[1],
                      fmaf(sum[2], (float)scales[2],
                           sum[3] * (float)scales[3])));
        temp = fmaf(scaled, d, temp);
    }
    *out_sum = (double)temp;
    return 1;
}

static int q6k_accumulate_tid_partial(
        const int weight_fd,
        const int vector_fd,
        const VulkanDispatchBinding *weight_binding,
        const VulkanDispatchBinding *vector_binding,
        uint32_t row,
        uint32_t tid,
        uint32_t ncols,
        uint32_t batch_stride_b,
        uint32_t base_work_group_y,
        double *out_sum) {
    if (!weight_binding || !vector_binding || !out_sum || ncols == 0 || (ncols % 256u) != 0) {
        return 0;
    }
    const uint32_t num_blocks_per_row = ncols / 256u;
    const uint32_t itid = tid % 16u;
    const uint32_t ix = tid / 16u;
    const uint32_t v_im = itid / 8u;
    const uint32_t v_in = itid - 8u * v_im;
    const uint32_t l0 = 4u * v_in;
    const uint32_t y_offset = 128u * v_im + l0;
    double sum = 0.0;
    for (uint32_t block_index = ix; block_index < num_blocks_per_row; block_index += 2u) {
        uint8_t block[210];
        off_t off = weight_binding->offset +
                    (off_t)(((uint64_t)row * num_blocks_per_row + block_index) * 210ull);
        if (!q6k_read_block(weight_fd, off, block)) return 0;
        for (uint32_t l = 0; l < 4u; ++l) {
            const uint32_t elems[4] = {
                y_offset + l,
                y_offset + 32u + l,
                y_offset + 64u + l,
                y_offset + 96u + l,
            };
            for (uint32_t q = 0; q < 4u; ++q) {
                float b = 0.0f;
                const off_t b_off = vector_binding->offset +
                                    (off_t)((uint64_t)base_work_group_y * batch_stride_b * sizeof(float)) +
                                    (off_t)((uint64_t)(block_index * 256u + elems[q]) * sizeof(float));
                if (pread(vector_fd, &b, sizeof(b), b_off) != (ssize_t)sizeof(b)) return 0;
                sum += (double)q6k_value_at(block, elems[q]) * (double)b;
            }
        }
    }
    *out_sum = sum;
    return 1;
}

static void run_cpu_oracle_q6k_matvec_sample(
        CpuOracleReport *report,
        uint64_t spirv_hash,
        const int *buffer_fds,
        const VulkanDispatchBinding *bindings,
        size_t binding_count,
        VulkanVectorBuffer * const *vk_buffers,
        const size_t *binding_gpu_offset,
        const uint8_t *active,
        const uint8_t *writable,
        const uint8_t *push,
        size_t push_size) {
    if (!report || !report->requested) return;
    init_cpu_oracle_report(report, report->requested, spirv_hash);
    int idx0 = binding_index_for_number(bindings, binding_count, 0);
    int idx1 = binding_index_for_number(bindings, binding_count, 1);
    int idx2 = binding_index_for_number(bindings, binding_count, 2);
    if (idx0 < 0 || idx1 < 0 || idx2 < 0 || !buffer_fds || buffer_fds[idx0] < 0 ||
        buffer_fds[idx1] < 0 || !vk_buffers || !vk_buffers[idx2] || !vk_buffers[idx2]->map ||
        !active || !active[idx0] || !active[idx1] || !active[idx2] ||
        !writable || !writable[idx2] || !push || push_size < 8 * sizeof(uint32_t)) {
        report->skipped = 1;
        snprintf(report->status, sizeof(report->status), "%s", "missing-oracle-inputs");
        return;
    }
    const uint32_t ncols = load_le_u32(push, push_size, 0);
    const uint32_t stride_d = load_le_u32(push, push_size, 3);
    const uint32_t batch_stride_b = load_le_u32(push, push_size, 5);
    const uint32_t base_work_group_y = load_le_u32(push, push_size, 7);
    if (ncols == 0 || ncols > 16384 || (ncols % 256u) != 0 ||
        bindings[idx1].size < (size_t)ncols * sizeof(float) ||
        bindings[idx2].size < sizeof(float)) {
        report->skipped = 1;
        snprintf(report->status, sizeof(report->status), "%s", "unsupported-q6k-layout");
        return;
    }
    const uint32_t num_blocks_per_row = ncols / 256u;
    const size_t block_bytes = 210;
    uint32_t sample_rows[32];
    for (uint32_t i = 0; i < 32u; ++i) sample_rows[i] = i;
    report->expected_hash = 1469598103934665603ull;
    report->gpu_hash = 1469598103934665603ull;
    for (size_t si = 0; si < sizeof(sample_rows) / sizeof(sample_rows[0]); ++si) {
        const uint32_t row = sample_rows[si];
        if (row >= stride_d || ((size_t)row + 1u) * sizeof(float) > bindings[idx2].size) continue;
        double sum = 0.0;
        double variant_no_high = 0.0;
        double variant_unsigned_scales = 0.0;
        double variant_no_center = 0.0;
        double variant_packed16_view = 0.0;
        double partials[32];
        double shader_like_partials[32];
        memset(partials, 0, sizeof(partials));
        memset(shader_like_partials, 0, sizeof(shader_like_partials));
        int have_partials = 1;
        int have_shader_like_partials = 1;
        for (uint32_t block_index = 0; block_index < num_blocks_per_row; ++block_index) {
            uint8_t block[210];
            off_t off = bindings[idx0].offset +
                        (off_t)(((uint64_t)row * num_blocks_per_row + block_index) * block_bytes);
            if (!q6k_read_block(buffer_fds[idx0], off, block)) {
                report->skipped = 1;
                snprintf(report->status, sizeof(report->status), "%s", "oracle-weight-read-failed");
                return;
            }
            for (uint32_t e = 0; e < 256u; ++e) {
                float b = 0.0f;
                const off_t b_off = bindings[idx1].offset +
                                    (off_t)((uint64_t)base_work_group_y * batch_stride_b * sizeof(float)) +
                                    (off_t)((uint64_t)(block_index * 256u + e) * sizeof(float));
                if (pread(buffer_fds[idx1], &b, sizeof(b), b_off) != (ssize_t)sizeof(b)) {
                    report->skipped = 1;
                    snprintf(report->status, sizeof(report->status), "%s", "oracle-vector-read-failed");
                    return;
                }
                const double bv = (double)b;
                sum += (double)q6k_value_at(block, e) * bv;
                if (si == 0) {
                    variant_no_high += (double)q6k_value_variant_at(block, e, 1) * bv;
                    variant_unsigned_scales += (double)q6k_value_variant_at(block, e, 2) * bv;
                    variant_no_center += (double)q6k_value_variant_at(block, e, 3) * bv;
                    variant_packed16_view += (double)q6k_value_packed16_view_at(block, e) * bv;
                }
            }
        }
        for (uint32_t tid = 0; tid < 32u; ++tid) {
            if (!q6k_accumulate_tid_partial(buffer_fds[idx0],
                                           buffer_fds[idx1],
                                           &bindings[idx0],
                                           &bindings[idx1],
                                           row,
                                           tid,
                                           ncols,
                                           batch_stride_b,
                                           base_work_group_y,
                                           &partials[tid])) {
                have_partials = 0;
                break;
            }
        }
        for (uint32_t tid = 0; tid < 32u; ++tid) {
            if (!q6k_accumulate_tid_partial_shader_like(buffer_fds[idx0],
                                                       buffer_fds[idx1],
                                                       &bindings[idx0],
                                                       &bindings[idx1],
                                                       row,
                                                       tid,
                                                       ncols,
                                                       batch_stride_b,
                                                       base_work_group_y,
                                                       &shader_like_partials[tid])) {
                have_shader_like_partials = 0;
                break;
            }
        }
        int ok = 0;
        float gpu = load_f32_at_index((const uint8_t *)vk_buffers[idx2]->map + binding_gpu_offset[idx2],
                                      bindings[idx2].size, row, &ok);
        if (!ok) {
            report->skipped = 1;
            snprintf(report->status, sizeof(report->status), "%s", "oracle-output-read-failed");
            return;
        }
        float expected = (float)sum;
        store_hash_f32(&report->expected_hash, expected);
        store_hash_f32(&report->gpu_hash, gpu);
        double abs_error = fabs((double)expected - (double)gpu);
        double denom = fabs((double)expected);
        double rel_error = denom > 1e-30 ? abs_error / denom : abs_error;
        if (abs_error > report->max_abs_error) report->max_abs_error = abs_error;
        if (rel_error > report->max_rel_error) report->max_rel_error = rel_error;
        if (si == 0 && have_partials) {
            report->has_partial_diagnostic = 1;
            report->partial_best_lane = 0;
            report->partial_best_value = partials[0];
            report->partial_best_abs_error = fabs(partials[0] - (double)gpu);
            report->partial_first16_sum = 0.0;
            report->partial_second16_sum = 0.0;
            report->q6_no_high_bits_sum = variant_no_high;
            report->q6_unsigned_scales_sum = variant_unsigned_scales;
            report->q6_no_center_sum = variant_no_center;
            report->q6_packed16_view_sum = variant_packed16_view;
            report->q6_packed16_view_abs_delta = fabs(variant_packed16_view - sum);
            report->q6_shader_like_sum = 0.0;
            if (have_shader_like_partials) {
                for (uint32_t tid = 0; tid < 32u; ++tid) {
                    report->q6_shader_like_sum += shader_like_partials[tid];
                }
                report->q6_shader_like_abs_delta = fabs(report->q6_shader_like_sum - sum);
            }
            report->partial_lane_count = 32;
            for (uint32_t tid = 0; tid < 32u; ++tid) {
                report->partial_lanes[tid] = partials[tid];
                const double per_tid_error = fabs(partials[tid] - (double)gpu);
                if (per_tid_error < report->partial_best_abs_error) {
                    report->partial_best_abs_error = per_tid_error;
                    report->partial_best_lane = (int)tid;
                    report->partial_best_value = partials[tid];
                }
                if (tid < 16u) {
                    report->partial_first16_sum += partials[tid];
                } else {
                    report->partial_second16_sum += partials[tid];
                }
            }
        }
        if (report->sample_count < sizeof(report->samples) / sizeof(report->samples[0])) {
            CpuOracleSample *sample = &report->samples[report->sample_count++];
            sample->dst_index = row;
            sample->expected = expected;
            sample->gpu = gpu;
            sample->src0 = (float)row;
            sample->src1 = (float)ncols;
            sample->abs_error = abs_error;
        }
        if (abs_error > 1e-3 && rel_error > 1e-4) {
            if (!report->has_first_mismatch) {
                report->has_first_mismatch = 1;
                report->first_mismatch.dst_index = row;
                report->first_mismatch.expected = expected;
                report->first_mismatch.gpu = gpu;
                report->first_mismatch.src0 = (float)row;
                report->first_mismatch.src1 = (float)ncols;
                report->first_mismatch.abs_error = abs_error;
            }
            report->mismatch_count++;
            if (gpu == 0.0f && expected != 0.0f) report->zero_gpu_mismatch_count++;
        }
        report->compared_floats++;
        report->compared_iter0++;
        if (report->row_window_count < sizeof(report->row_window) / sizeof(report->row_window[0])) {
            CpuOracleSample *window = &report->row_window[report->row_window_count++];
            window->dst_index = row;
            window->expected = expected;
            window->gpu = gpu;
            window->src0 = expected * 0.5f;
            window->src1 = (float)ncols;
            window->abs_error = abs_error;
        }
    }
    report->executed = 1;
    report->skipped = 0;
    snprintf(report->status, sizeof(report->status), "%s",
             report->mismatch_count ? "mismatch" : "match");
}

static void run_cpu_oracle_small_f32_indexing(
        CpuOracleReport *report,
        uint64_t spirv_hash,
        const int *buffer_fds,
        const VulkanDispatchBinding *bindings,
        size_t binding_count,
        VulkanVectorBuffer * const *vk_buffers,
        const size_t *binding_gpu_offset,
        const size_t *binding_alias_rep,
        const uint8_t *active,
        const uint8_t *writable,
        const uint8_t *push,
        size_t push_size,
        uint32_t gx,
        uint32_t gy,
        uint32_t gz) {
    if (!report || !report->requested) return;
    init_cpu_oracle_report(report, report->requested, spirv_hash);
    if (spirv_hash != 0x7bf05c459ac87f2bull &&
        spirv_hash != 0x11d5243c43b23a7bull &&
        spirv_hash != 0x11c0523df6c795b8ull) {
        report->skipped = 1;
        snprintf(report->status, sizeof(report->status), "%s",
                 report->candidate ? "kernel-not-implemented-yet" : "unsupported-shader-hash");
        return;
    }
    const int use_direct_rhs_indexing =
        spirv_hash == 0x11c0523df6c795b8ull;
    const int use_add_op =
        spirv_hash == 0x11c0523df6c795b8ull;
    int idx0 = binding_index_for_number(bindings, binding_count, 0);
    int idx1 = binding_index_for_number(bindings, binding_count, 1);
    int idx2 = binding_index_for_number(bindings, binding_count, 2);
    if (idx0 < 0 || idx1 < 0 || idx2 < 0 || !buffer_fds || !vk_buffers ||
        !vk_buffers[idx2] || !vk_buffers[idx2]->map || !push || push_size < 26 * sizeof(uint32_t)) {
        report->skipped = 1;
        snprintf(report->status, sizeof(report->status), "%s", "missing-oracle-inputs");
        return;
    }
    const off_t src0_start = bindings[idx0].offset;
    const off_t src0_end = bindings[idx0].offset + (off_t)bindings[idx0].size;
    const off_t dst_start = bindings[idx2].offset;
    const off_t dst_end = bindings[idx2].offset + (off_t)bindings[idx2].size;
    report->input_output_overlap =
        ((binding_alias_rep && binding_alias_rep[idx0] == binding_alias_rep[idx2]) ||
         buffer_fds[idx0] == buffer_fds[idx2]) &&
        src0_start < dst_end && dst_start < src0_end;
    if (active && (!active[idx0] || !active[idx1] || !active[idx2])) {
        report->skipped = 1;
        snprintf(report->status, sizeof(report->status), "%s", "inactive-oracle-binding");
        return;
    }
    if (writable && !writable[idx2]) {
        report->skipped = 1;
        snprintf(report->status, sizeof(report->status), "%s", "output-binding-not-writable");
        return;
    }
    const size_t oracle_cap = 8u * 1024u * 1024u;
    if (bindings[idx0].size > oracle_cap || bindings[idx1].size > oracle_cap ||
        bindings[idx2].size > oracle_cap) {
        report->skipped = 1;
        snprintf(report->status, sizeof(report->status), "%s", "binding-too-large-for-oracle");
        return;
    }
    uint8_t *src0 = (uint8_t *)malloc(bindings[idx0].size);
    uint8_t *src1 = (uint8_t *)malloc(bindings[idx1].size);
    if (!src0 || !src1) {
        free(src0);
        free(src1);
        report->skipped = 1;
        snprintf(report->status, sizeof(report->status), "%s", "oracle-oom");
        return;
    }
    if (read_fd_exact(buffer_fds[idx0], src0, bindings[idx0].size, bindings[idx0].offset) != 0 ||
        read_fd_exact(buffer_fds[idx1], src1, bindings[idx1].size, bindings[idx1].offset) != 0) {
        free(src0);
        free(src1);
        report->skipped = 1;
        snprintf(report->status, sizeof(report->status), "%s", "oracle-read-failed");
        return;
    }
    uint32_t pc[26];
    for (size_t i = 0; i < 26; ++i) pc[i] = load_le_u32(push, push_size, i);
    const uint32_t total = pc[0];
    const uint32_t n0 = pc[1];
    const uint32_t n1 = pc[2];
    const uint32_t n2 = pc[3];
    if (total == 0 || n0 == 0 || n1 == 0 || n2 == 0) {
        free(src0);
        free(src1);
        report->skipped = 1;
        snprintf(report->status, sizeof(report->status), "%s", "invalid-push-shape");
        return;
    }
    const uint64_t n10 = (uint64_t)n1 * (uint64_t)n0;
    const uint64_t prod = (uint64_t)n2 * n10;
    if (prod == 0 || prod > UINT32_MAX) {
        free(src0);
        free(src1);
        report->skipped = 1;
        snprintf(report->status, sizeof(report->status), "%s", "unsupported-push-shape");
        return;
    }
    report->expected_hash = 1469598103934665603ull;
    report->gpu_hash = 1469598103934665603ull;
    const uint64_t max_invocations = (uint64_t)(gx ? gx : 1u) * 256ull *
                                     (uint64_t)(gy ? gy : 1u) *
                                     (uint64_t)(gz ? gz : 1u);
    const uint64_t max_iterations = max_invocations * 2ull;
    if (max_iterations > 4ull * 1024ull * 1024ull) {
        free(src0);
        free(src1);
        report->skipped = 1;
        snprintf(report->status, sizeof(report->status), "%s", "dispatch-too-large-for-oracle");
        return;
    }
    const uint8_t *gpu_dst_base = (const uint8_t *)vk_buffers[idx2]->map + binding_gpu_offset[idx2];
    for (uint32_t z = 0; z < (gz ? gz : 1u); ++z) {
        for (uint32_t y = 0; y < (gy ? gy : 1u); ++y) {
            for (uint32_t wx = 0; wx < (gx ? gx : 1u); ++wx) {
                for (uint32_t lx = 0; lx < 256u; ++lx) {
                    uint64_t base_id = ((uint64_t)z * 262144ull) +
                                       ((uint64_t)y * 512ull) +
                                       ((uint64_t)wx * 256ull) + lx;
                    for (uint32_t iter = 0; iter < 2u; ++iter) {
                        uint64_t id64 = base_id + (uint64_t)iter * 256ull;
                        if (id64 >= total) continue;
                        uint32_t id = (uint32_t)id64;
                        uint32_t i3 = id < prod ? 0u : (uint32_t)(id / prod);
                        uint32_t rem0 = id - (uint32_t)((uint64_t)i3 * prod);
                        uint32_t i2 = rem0 < n10 ? 0u : (uint32_t)(rem0 / n10);
                        uint32_t rem1 = rem0 - (uint32_t)((uint64_t)i2 * n10);
                        uint32_t i1 = rem1 / n0;
                        uint32_t i0 = rem1 - i1 * n0;
                        uint64_t dst_idx = (uint64_t)(pc[25] & 255u) +
                                           (uint64_t)i3 * pc[24] +
                                           (uint64_t)i2 * pc[23] +
                                           (uint64_t)i1 * pc[22] +
                                           (uint64_t)i0 * pc[21];
                        uint64_t src0_idx = (uint64_t)(pc[25] >> 16u) +
                                            (uint64_t)i3 * pc[8] +
                                            (uint64_t)i2 * pc[7] +
                                            (uint64_t)i1 * pc[6] +
                                            (uint64_t)i0 * pc[5];
                        uint64_t src1_idx = (uint64_t)((pc[25] >> 8u) & 255u);
                        if (use_direct_rhs_indexing) {
                            src1_idx += (uint64_t)i3 * pc[16] +
                                        (uint64_t)i2 * pc[15] +
                                        (uint64_t)i1 * pc[14] +
                                        (uint64_t)i0 * pc[13];
                        } else {
                            uint32_t b3 = broadcast_index_u32(i3, pc[12]);
                            uint32_t b2 = broadcast_index_u32(i2, pc[11]);
                            uint32_t b1 = broadcast_index_u32(i1, pc[10]);
                            uint32_t b0 = broadcast_index_u32(i0, pc[9]);
                            src1_idx += (uint64_t)b3 * pc[16] +
                                        (uint64_t)b2 * pc[15] +
                                        (uint64_t)b1 * pc[14] +
                                        (uint64_t)b0 * pc[13];
                        }
                        int ok0 = 0, ok1 = 0, ok2 = 0;
                        float a = load_f32_at_index(src0, bindings[idx0].size, (size_t)src0_idx, &ok0);
                        float b = load_f32_at_index(src1, bindings[idx1].size, (size_t)src1_idx, &ok1);
                        float gpu = load_f32_at_index(gpu_dst_base, bindings[idx2].size, (size_t)dst_idx, &ok2);
                        if (!ok0 || !ok1 || !ok2) {
                            report->skipped = 1;
                            snprintf(report->status, sizeof(report->status), "%s", "oracle-index-out-of-range");
                            free(src0);
                            free(src1);
                            return;
                        }
                        float expected = use_add_op ? (a + b) : (a * b);
                        store_hash_f32(&report->expected_hash, expected);
                        store_hash_f32(&report->gpu_hash, gpu);
                        double abs_error = fabs((double)expected - (double)gpu);
                        if (report->sample_count < sizeof(report->samples) / sizeof(report->samples[0])) {
                            CpuOracleSample *sample = &report->samples[report->sample_count++];
                            sample->dst_index = dst_idx;
                            sample->expected = expected;
                            sample->gpu = gpu;
                            sample->src0 = a;
                            sample->src1 = b;
                            sample->abs_error = abs_error;
                        }
                        double denom = fabs((double)expected);
                        double rel_error = denom > 1e-30 ? abs_error / denom : abs_error;
                        if (abs_error > report->max_abs_error) report->max_abs_error = abs_error;
                        if (rel_error > report->max_rel_error) report->max_rel_error = rel_error;
                        if (iter == 0) {
                            report->compared_iter0++;
                        } else {
                            report->compared_iter1++;
                        }
                        if (abs_error > 1e-5 && rel_error > 1e-4) {
                            if (!report->has_first_mismatch) {
                                report->has_first_mismatch = 1;
                                report->first_mismatch.dst_index = dst_idx;
                                report->first_mismatch.expected = expected;
                                report->first_mismatch.gpu = gpu;
                                report->first_mismatch.src0 = a;
                                report->first_mismatch.src1 = b;
                                report->first_mismatch.abs_error = abs_error;
                            }
                            if (iter == 0) {
                                report->mismatch_iter0++;
                            } else {
                                report->mismatch_iter1++;
                            }
                            if (gpu == 0.0f && expected != 0.0f) {
                                report->zero_gpu_mismatch_count++;
                            }
                            report->mismatch_count++;
                        }
                        report->compared_floats++;
                    }
                }
            }
        }
    }
    free(src0);
    free(src1);
    report->executed = 1;
    report->skipped = 0;
    snprintf(report->status, sizeof(report->status), "%s",
             report->mismatch_count ? "mismatch" : "match");
}

static void write_vulkan_binding_compact_report(
        FILE *out,
        const VulkanDispatchBinding *bindings,
        size_t binding_count,
        VulkanVectorBuffer * const *vk_buffers,
        const size_t *binding_gpu_offset,
        const uint8_t *active,
        const uint8_t *readable,
        const uint8_t *writable,
        const int *cache_resident,
        const int *cache_hits,
        const uint64_t *fd_before_hash,
        const uint64_t *gpu_after_upload_hash,
        const uint64_t *gpu_after_dispatch_hash,
        const uint64_t *fd_after_hash,
        const size_t *alias_rep) {
    fprintf(out, "\"binding_details\":[");
    for (size_t i = 0; i < binding_count; ++i) {
        fprintf(out,
                "%s{\"index\":%zu,\"binding\":%u,\"offset\":%lld,"
                "\"size\":%zu,"
                "\"api_offset\":%lld,\"api_range\":%zu,"
                "\"api_buffer_size\":%zu,\"api_descriptor_type\":%u,"
                "\"api_dynamic\":%s,\"api_memory_offset\":%lld,"
                "\"alias_rep\":%zu,\"active\":%s,"
                "\"readable\":%s,\"writable\":%s,\"resident\":%s,"
                "\"cache_hit\":%s,\"fd_before_hash\":\"0x%016llx\","
                "\"gpu_after_upload_hash\":\"0x%016llx\","
                "\"gpu_after_dispatch_hash\":\"0x%016llx\","
                "\"fd_after_hash\":\"0x%016llx\"",
                i ? "," : "",
                i,
                bindings[i].binding,
                (long long)bindings[i].offset,
                bindings[i].size,
                (long long)bindings[i].api_offset,
                bindings[i].api_range,
                bindings[i].api_buffer_size,
                bindings[i].api_descriptor_type,
                bindings[i].api_dynamic ? "true" : "false",
                (long long)bindings[i].api_memory_offset,
                alias_rep ? alias_rep[i] : i,
                active && active[i] ? "true" : "false",
                readable && readable[i] ? "true" : "false",
                writable && writable[i] ? "true" : "false",
                cache_resident && cache_resident[i] ? "true" : "false",
                cache_hits && cache_hits[i] ? "true" : "false",
                (unsigned long long)(fd_before_hash ? fd_before_hash[i] : 0),
                (unsigned long long)(gpu_after_upload_hash ? gpu_after_upload_hash[i] : 0),
                (unsigned long long)(gpu_after_dispatch_hash ? gpu_after_dispatch_hash[i] : 0),
                (unsigned long long)(fd_after_hash ? fd_after_hash[i] : 0));
        if (active && active[i] && writable && writable[i] &&
            vk_buffers && vk_buffers[i] && vk_buffers[i]->map &&
            binding_gpu_offset && binding_gpu_offset[i] < vk_buffers[i]->size) {
            size_t local_size = vk_buffers[i]->size - binding_gpu_offset[i];
            if (local_size > bindings[i].size) local_size = bindings[i].size;
            fprintf(out, ",\"f32_after_dispatch\":");
            write_f32_sample_array(
                out,
                (const unsigned char *)vk_buffers[i]->map + binding_gpu_offset[i],
                local_size);
        }
        fprintf(out, "}");
    }
    fprintf(out, "]");
}


static VulkanVectorBuffer *acquire_dispatch_buffer(
        VkPhysicalDevice physical_device,
        VkDevice device,
        int fd,
        const VulkanDispatchBinding *binding,
        VulkanVectorBuffer *temporary,
        int initialize_from_fd,
        int *cache_hit,
        int *cache_resident,
        int *mutable_cache_hit,
        int *mutable_cache_reused,
        int writeonly_scratch_enabled,
        size_t mutable_cache_max_bytes) {
    *cache_hit = 0;
    *cache_resident = 0;
    *mutable_cache_hit = 0;
    *mutable_cache_reused = 0;
    dev_t dev = 0;
    ino_t ino = 0;
    const int have_key = resident_cache_key(fd, &dev, &ino) == 0;
    if (!initialize_from_fd) {
        if (writeonly_scratch_enabled &&
            mutable_buffer_cache_candidate_with_max(
                binding->binding,
                binding->size,
                mutable_cache_max_bytes)) {
            VulkanMutableBufferCacheEntry *entry = find_writeonly_scratch_cache_entry(
                binding->size, binding->binding);
            if (entry) {
                entry->hits++;
                *mutable_cache_hit = 1;
                *mutable_cache_reused = 1;
                return &entry->buffer;
            }
            entry = select_mutable_buffer_cache_slot(device);
            if (create_vulkan_vector_buffer(physical_device, device, binding->size, NULL, &entry->buffer) == 0) {
                entry->valid = 1;
                entry->scratch = 1;
                entry->dev = dev;
                entry->ino = ino;
                entry->offset = 0;
                entry->size = binding->size;
                entry->binding = binding->binding;
                entry->hits = 1;
                *mutable_cache_reused = 1;
                return &entry->buffer;
            }
            destroy_vulkan_vector_buffer(device, &entry->buffer);
            memset(entry, 0, sizeof(*entry));
        }
        if (create_vulkan_vector_buffer(physical_device, device, binding->size, NULL, temporary) != 0) {
            return NULL;
        }
        return temporary;
    }
    if (resident_cache_candidate(binding->binding, binding->size)) {
        if (have_key) {
            VulkanResidentCacheEntry *entry = find_resident_cache_entry(
                dev, ino, binding->offset, binding->size, binding->binding);
            if (entry) {
                entry->hits++;
                *cache_hit = 1;
                *cache_resident = 1;
                return &entry->buffer;
            }
            entry = select_resident_cache_slot(device);
            if (create_vulkan_vector_buffer(physical_device, device, binding->size, NULL, &entry->buffer) == 0 &&
                read_fd_exact(fd, entry->buffer.map, binding->size, binding->offset) == 0) {
                entry->valid = 1;
                entry->dev = dev;
                entry->ino = ino;
                entry->offset = binding->offset;
                entry->size = binding->size;
                entry->binding = binding->binding;
                entry->hits = 1;
                *cache_resident = 1;
                return &entry->buffer;
            }
            destroy_vulkan_vector_buffer(device, &entry->buffer);
            memset(entry, 0, sizeof(*entry));
        }
    }
    if (have_key &&
        mutable_buffer_cache_candidate_with_max(
            binding->binding,
            binding->size,
            mutable_cache_max_bytes)) {
        VulkanMutableBufferCacheEntry *entry = find_mutable_buffer_cache_entry(
            dev, ino, binding->offset, binding->size, binding->binding);
        if (entry) {
            if (read_fd_exact(fd, entry->buffer.map, binding->size, binding->offset) == 0) {
                entry->hits++;
                *mutable_cache_hit = 1;
                *mutable_cache_reused = 1;
                return &entry->buffer;
            }
            destroy_vulkan_vector_buffer(device, &entry->buffer);
            memset(entry, 0, sizeof(*entry));
        }
        entry = select_mutable_buffer_cache_slot(device);
        if (create_vulkan_vector_buffer(physical_device, device, binding->size, NULL, &entry->buffer) == 0 &&
            read_fd_exact(fd, entry->buffer.map, binding->size, binding->offset) == 0) {
            entry->valid = 1;
            entry->dev = dev;
            entry->ino = ino;
            entry->offset = binding->offset;
            entry->size = binding->size;
            entry->binding = binding->binding;
            entry->hits = 1;
            *mutable_cache_reused = 1;
            return &entry->buffer;
        }
        destroy_vulkan_vector_buffer(device, &entry->buffer);
        memset(entry, 0, sizeof(*entry));
    }
    if (create_vulkan_vector_buffer(physical_device, device, binding->size, NULL, temporary) != 0) return NULL;
    if (read_fd_exact(fd, temporary->map, binding->size, binding->offset) != 0) {
        destroy_vulkan_vector_buffer(device, temporary);
        return NULL;
    }
    return temporary;
}

static int init_vulkan_runtime(VulkanRuntime *rt) {
    if (rt->ready) return 0;
    double start = now_ms();
    const char *stage = "start";
    VkResult rc = VK_SUCCESS;
    const uint32_t instance_api_version = choose_vulkan_instance_api_version();
    VkApplicationInfo app = {
        .sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
        .pApplicationName = "pdocker-gpu-executor",
        .apiVersion = instance_api_version,
    };
    VkInstanceCreateInfo ici = {
        .sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
        .pApplicationInfo = &app,
    };
    stage = "create-instance";
    rc = vkCreateInstance(&ici, NULL, &rt->instance);
    if (rc != VK_SUCCESS) goto fail;
    uint32_t physical_count = 0;
    stage = "enumerate-physical-count";
    rc = vkEnumeratePhysicalDevices(rt->instance, &physical_count, NULL);
    if (rc != VK_SUCCESS || physical_count == 0) goto fail;
    VkPhysicalDevice physical_devices[8];
    if (physical_count > 8) physical_count = 8;
    stage = "enumerate-physical";
    rc = vkEnumeratePhysicalDevices(rt->instance, &physical_count, physical_devices);
    if (rc != VK_SUCCESS || physical_count == 0) goto fail;
    rt->physical_device = physical_devices[0];
    memset(&rt->physical_properties, 0, sizeof(rt->physical_properties));
    vkGetPhysicalDeviceProperties(rt->physical_device, &rt->physical_properties);
    rt->api_version = rt->physical_properties.apiVersion;
    memset(&rt->physical_features, 0, sizeof(rt->physical_features));
    vkGetPhysicalDeviceFeatures(rt->physical_device, &rt->physical_features);
    memset(&rt->physical_storage16, 0, sizeof(rt->physical_storage16));
    memset(&rt->physical_storage8, 0, sizeof(rt->physical_storage8));
    memset(&rt->physical_float16_int8, 0, sizeof(rt->physical_float16_int8));
    memset(&rt->subgroup_properties, 0, sizeof(rt->subgroup_properties));
    rt->physical_storage16.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_16BIT_STORAGE_FEATURES;
    rt->physical_storage8.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_8BIT_STORAGE_FEATURES;
    rt->physical_float16_int8.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SHADER_FLOAT16_INT8_FEATURES;
    rt->subgroup_properties.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SUBGROUP_PROPERTIES;
    PFN_vkGetPhysicalDeviceFeatures2 get_features2 =
        (PFN_vkGetPhysicalDeviceFeatures2)vkGetInstanceProcAddr(rt->instance, "vkGetPhysicalDeviceFeatures2");
    if (!get_features2) {
        get_features2 = (PFN_vkGetPhysicalDeviceFeatures2)vkGetInstanceProcAddr(rt->instance, "vkGetPhysicalDeviceFeatures2KHR");
    }
    if (get_features2) {
        VkPhysicalDeviceFeatures2 features2;
        memset(&features2, 0, sizeof(features2));
        features2.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2;
        features2.pNext = &rt->physical_storage16;
        rt->physical_storage16.pNext = &rt->physical_storage8;
        rt->physical_storage8.pNext = &rt->physical_float16_int8;
        get_features2(rt->physical_device, &features2);
        rt->physical_features = features2.features;
        rt->physical_storage16.pNext = NULL;
        rt->physical_storage8.pNext = NULL;
        rt->physical_float16_int8.pNext = NULL;
    }
    PFN_vkGetPhysicalDeviceProperties2 get_properties2 =
        (PFN_vkGetPhysicalDeviceProperties2)vkGetInstanceProcAddr(rt->instance, "vkGetPhysicalDeviceProperties2");
    if (!get_properties2) {
        get_properties2 = (PFN_vkGetPhysicalDeviceProperties2)vkGetInstanceProcAddr(rt->instance, "vkGetPhysicalDeviceProperties2KHR");
    }
    if (get_properties2) {
        VkPhysicalDeviceProperties2 properties2;
        memset(&properties2, 0, sizeof(properties2));
        properties2.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2;
        properties2.pNext = &rt->subgroup_properties;
        get_properties2(rt->physical_device, &properties2);
    }
    uint32_t family_count = 0;
    vkGetPhysicalDeviceQueueFamilyProperties(rt->physical_device, &family_count, NULL);
    if (family_count == 0) { stage = "queue-family-count"; goto fail; }
    VkQueueFamilyProperties families[16];
    if (family_count > 16) family_count = 16;
    vkGetPhysicalDeviceQueueFamilyProperties(rt->physical_device, &family_count, families);
    rt->queue_family = UINT32_MAX;
    for (uint32_t i = 0; i < family_count; ++i) {
        if (families[i].queueFlags & VK_QUEUE_COMPUTE_BIT) {
            rt->queue_family = i;
            break;
        }
    }
    if (rt->queue_family == UINT32_MAX) { stage = "queue-family-compute"; goto fail; }
    float priority = 1.0f;
    VkDeviceQueueCreateInfo qci = {
        .sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,
        .queueFamilyIndex = rt->queue_family,
        .queueCount = 1,
        .pQueuePriorities = &priority,
    };
    VkPhysicalDeviceFeatures enabled_features = rt->physical_features;
    VkPhysicalDevice16BitStorageFeatures enabled_storage16;
    VkPhysicalDevice8BitStorageFeatures enabled_storage8;
    VkPhysicalDeviceShaderFloat16Int8Features enabled_float16_int8;
    memset(&enabled_storage16, 0, sizeof(enabled_storage16));
    memset(&enabled_storage8, 0, sizeof(enabled_storage8));
    memset(&enabled_float16_int8, 0, sizeof(enabled_float16_int8));
    enabled_storage16.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_16BIT_STORAGE_FEATURES;
    enabled_storage16.storageBuffer16BitAccess = rt->physical_storage16.storageBuffer16BitAccess;
    enabled_storage16.uniformAndStorageBuffer16BitAccess = rt->physical_storage16.uniformAndStorageBuffer16BitAccess;
    enabled_storage16.storagePushConstant16 = rt->physical_storage16.storagePushConstant16;
    enabled_storage16.storageInputOutput16 = rt->physical_storage16.storageInputOutput16;
    enabled_storage8.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_8BIT_STORAGE_FEATURES;
    enabled_storage8.storageBuffer8BitAccess = rt->physical_storage8.storageBuffer8BitAccess;
    enabled_storage8.uniformAndStorageBuffer8BitAccess = rt->physical_storage8.uniformAndStorageBuffer8BitAccess;
    enabled_storage8.storagePushConstant8 = rt->physical_storage8.storagePushConstant8;
    enabled_float16_int8.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SHADER_FLOAT16_INT8_FEATURES;
    enabled_float16_int8.shaderFloat16 = rt->physical_float16_int8.shaderFloat16;
    enabled_float16_int8.shaderInt8 = rt->physical_float16_int8.shaderInt8;
    void *device_features_pnext = NULL;
    /*
     * Use the narrowly scoped feature structs even on Vulkan 1.2 devices.
     * Some Android drivers report the promoted VkPhysicalDeviceVulkan12Features
     * values but still reject int8/storage8 SPIR-V unless the original feature
     * structs are present in the device-create pNext chain.
     */
    if (rt->api_version >= VK_API_VERSION_1_1 &&
        (enabled_float16_int8.shaderFloat16 || enabled_float16_int8.shaderInt8)) {
        enabled_float16_int8.pNext = device_features_pnext;
        device_features_pnext = &enabled_float16_int8;
    }
    if (rt->api_version >= VK_API_VERSION_1_1 &&
        (enabled_storage8.storageBuffer8BitAccess ||
         enabled_storage8.uniformAndStorageBuffer8BitAccess ||
         enabled_storage8.storagePushConstant8)) {
        enabled_storage8.pNext = device_features_pnext;
        device_features_pnext = &enabled_storage8;
    }
    if (rt->api_version >= VK_API_VERSION_1_1 &&
        (enabled_storage16.storageBuffer16BitAccess ||
         enabled_storage16.uniformAndStorageBuffer16BitAccess ||
         enabled_storage16.storagePushConstant16 ||
         enabled_storage16.storageInputOutput16)) {
        enabled_storage16.pNext = device_features_pnext;
        device_features_pnext = &enabled_storage16;
    }
    const char *enabled_extensions[8];
    uint32_t enabled_extension_count = 0;
    memset(enabled_extensions, 0, sizeof(enabled_extensions));
    if (enabled_storage16.storageBuffer16BitAccess ||
        enabled_storage16.uniformAndStorageBuffer16BitAccess ||
        enabled_storage16.storagePushConstant16 ||
        enabled_storage16.storageInputOutput16) {
        append_vulkan_device_extension(rt->physical_device,
                                       enabled_extensions,
                                       &enabled_extension_count,
                                       (uint32_t)(sizeof(enabled_extensions) / sizeof(enabled_extensions[0])),
                                       VK_KHR_16BIT_STORAGE_EXTENSION_NAME);
    }
    if (enabled_storage8.storageBuffer8BitAccess ||
        enabled_storage8.uniformAndStorageBuffer8BitAccess ||
        enabled_storage8.storagePushConstant8) {
        append_vulkan_device_extension(rt->physical_device,
                                       enabled_extensions,
                                       &enabled_extension_count,
                                       (uint32_t)(sizeof(enabled_extensions) / sizeof(enabled_extensions[0])),
                                       VK_KHR_8BIT_STORAGE_EXTENSION_NAME);
    }
    if (enabled_float16_int8.shaderFloat16 || enabled_float16_int8.shaderInt8) {
        append_vulkan_device_extension(rt->physical_device,
                                       enabled_extensions,
                                       &enabled_extension_count,
                                       (uint32_t)(sizeof(enabled_extensions) / sizeof(enabled_extensions[0])),
                                       VK_KHR_SHADER_FLOAT16_INT8_EXTENSION_NAME);
    }
    append_vulkan_device_extension(rt->physical_device,
                                   enabled_extensions,
                                   &enabled_extension_count,
                                   (uint32_t)(sizeof(enabled_extensions) / sizeof(enabled_extensions[0])),
                                   VK_KHR_STORAGE_BUFFER_STORAGE_CLASS_EXTENSION_NAME);
    VkDeviceCreateInfo dci = {
        .sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO,
        .pNext = device_features_pnext,
        .queueCreateInfoCount = 1,
        .pQueueCreateInfos = &qci,
        .pEnabledFeatures = &enabled_features,
        .enabledExtensionCount = enabled_extension_count,
        .ppEnabledExtensionNames = enabled_extension_count ? enabled_extensions : NULL,
    };
    stage = "create-device";
    log_vulkan_enabled_feature_trace(&enabled_features, &enabled_storage16, &enabled_storage8, &enabled_float16_int8);
    rc = vkCreateDevice(rt->physical_device, &dci, NULL, &rt->device);
    if (rc != VK_SUCCESS) goto fail;
    vkGetDeviceQueue(rt->device, rt->queue_family, 0, &rt->queue);
    VkShaderModuleCreateInfo smci = {
        .sType = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO,
        .codeSize = sizeof(kVectorAddSpv),
        .pCode = kVectorAddSpv,
    };
    stage = "create-shader-module";
    rc = vkCreateShaderModule(rt->device, &smci, NULL, &rt->shader);
    if (rc != VK_SUCCESS) goto fail;
    VkDescriptorSetLayoutBinding bindings[3];
    memset(bindings, 0, sizeof(bindings));
    for (uint32_t i = 0; i < 3; ++i) {
        bindings[i].binding = i;
        bindings[i].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
        bindings[i].descriptorCount = 1;
        bindings[i].stageFlags = VK_SHADER_STAGE_COMPUTE_BIT;
    }
    VkDescriptorSetLayoutCreateInfo dslci = {
        .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO,
        .bindingCount = 3,
        .pBindings = bindings,
    };
    stage = "create-descriptor-set-layout";
    rc = vkCreateDescriptorSetLayout(rt->device, &dslci, NULL, &rt->set_layout);
    if (rc != VK_SUCCESS) goto fail;
    VkPipelineLayoutCreateInfo plci = {
        .sType = VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO,
        .setLayoutCount = 1,
        .pSetLayouts = &rt->set_layout,
    };
    stage = "create-pipeline-layout";
    rc = vkCreatePipelineLayout(rt->device, &plci, NULL, &rt->pipeline_layout);
    if (rc != VK_SUCCESS) goto fail;
    VkComputePipelineCreateInfo cpci = {
        .sType = VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO,
        .stage = {
            .sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO,
            .stage = VK_SHADER_STAGE_COMPUTE_BIT,
            .module = rt->shader,
            .pName = "main",
        },
        .layout = rt->pipeline_layout,
    };
    stage = "create-compute-pipeline";
    rc = vkCreateComputePipelines(rt->device, VK_NULL_HANDLE, 1, &cpci, NULL, &rt->pipeline);
    if (rc != VK_SUCCESS) goto fail;
    VkShaderModuleCreateInfo mmsmci = {
        .sType = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO,
        .codeSize = sizeof(kMatmul256Spv),
        .pCode = kMatmul256Spv,
    };
    stage = "create-matmul-shader-module";
    rc = vkCreateShaderModule(rt->device, &mmsmci, NULL, &rt->matmul_shader);
    if (rc != VK_SUCCESS) goto fail;
    VkComputePipelineCreateInfo mmcpci = cpci;
    mmcpci.stage.module = rt->matmul_shader;
    stage = "create-matmul-compute-pipeline";
    rc = vkCreateComputePipelines(rt->device, VK_NULL_HANDLE, 1, &mmcpci, NULL, &rt->matmul_pipeline);
    if (rc != VK_SUCCESS) goto fail;
    VkCommandPoolCreateInfo cpoci = {
        .sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO,
        .queueFamilyIndex = rt->queue_family,
    };
    stage = "create-command-pool";
    rc = vkCreateCommandPool(rt->device, &cpoci, NULL, &rt->command_pool);
    if (rc != VK_SUCCESS) goto fail;
    rt->init_ms = now_ms() - start;
    rt->ready = 1;
    return 0;
fail:
    fprintf(stderr, "pdocker-gpu-executor: Vulkan runtime init failed stage=%s rc=%d\n", stage, rc);
    if (rt->command_pool) vkDestroyCommandPool(rt->device, rt->command_pool, NULL);
    if (rt->matmul_pipeline) vkDestroyPipeline(rt->device, rt->matmul_pipeline, NULL);
    if (rt->pipeline) vkDestroyPipeline(rt->device, rt->pipeline, NULL);
    if (rt->pipeline_layout) vkDestroyPipelineLayout(rt->device, rt->pipeline_layout, NULL);
    if (rt->set_layout) vkDestroyDescriptorSetLayout(rt->device, rt->set_layout, NULL);
    if (rt->matmul_shader) vkDestroyShaderModule(rt->device, rt->matmul_shader, NULL);
    if (rt->shader) vkDestroyShaderModule(rt->device, rt->shader, NULL);
    if (rt->device) vkDestroyDevice(rt->device, NULL);
    if (rt->instance) vkDestroyInstance(rt->instance, NULL);
    memset(rt, 0, sizeof(*rt));
    return -1;
}

static int run_vector_add_arrays_vulkan(const float *a, const float *b, float *out, size_t n, const char *transport) {
    const int was_ready = g_vulkan_runtime.ready;
    if (init_vulkan_runtime(&g_vulkan_runtime) != 0) return -21;
    VulkanRuntime *rt = &g_vulkan_runtime;
    const size_t bytes = n * sizeof(float);
    const double init_ms = was_ready ? 0.0 : rt->init_ms;
    const double compile_ms = 0.0;
    const char *fail_stage = "start";
    VkResult fail_result = VK_SUCCESS;
    VulkanVectorBuffer buffers[3];
    VkDescriptorPool descriptor_pool = VK_NULL_HANDLE;
    VkCommandBuffer command_buffer = VK_NULL_HANDLE;
    VkFence fence = VK_NULL_HANDLE;
    memset(buffers, 0, sizeof(buffers));

    double upload_start = now_ms();
    fail_stage = "create-buffer-a";
    if (create_vulkan_vector_buffer(rt->physical_device, rt->device, bytes, a, &buffers[0]) != 0) goto fail;
    fail_stage = "create-buffer-b";
    if (create_vulkan_vector_buffer(rt->physical_device, rt->device, bytes, b, &buffers[1]) != 0) goto fail;
    fail_stage = "create-buffer-out";
    if (create_vulkan_vector_buffer(rt->physical_device, rt->device, bytes, NULL, &buffers[2]) != 0) goto fail;
    double upload_ms = now_ms() - upload_start;

    VkDescriptorPoolSize pool_size = {
        .type = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,
        .descriptorCount = 3,
    };
    VkDescriptorPoolCreateInfo dpci = {
        .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO,
        .maxSets = 1,
        .poolSizeCount = 1,
        .pPoolSizes = &pool_size,
    };
    fail_stage = "create-descriptor-pool";
    VkResult rc = vkCreateDescriptorPool(rt->device, &dpci, NULL, &descriptor_pool);
    if (rc != VK_SUCCESS) { fail_result = rc; goto fail; }
    VkDescriptorSet descriptor_set = VK_NULL_HANDLE;
    VkDescriptorSetAllocateInfo dsai = {
        .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO,
        .descriptorPool = descriptor_pool,
        .descriptorSetCount = 1,
        .pSetLayouts = &rt->set_layout,
    };
    fail_stage = "allocate-descriptor-set";
    rc = vkAllocateDescriptorSets(rt->device, &dsai, &descriptor_set);
    if (rc != VK_SUCCESS) { fail_result = rc; goto fail; }
    VkDescriptorBufferInfo infos[3];
    VkWriteDescriptorSet writes[3];
    memset(writes, 0, sizeof(writes));
    for (uint32_t i = 0; i < 3; ++i) {
        infos[i].buffer = buffers[i].buffer;
        infos[i].offset = 0;
        infos[i].range = (VkDeviceSize)bytes;
        writes[i].sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;
        writes[i].dstSet = descriptor_set;
        writes[i].dstBinding = i;
        writes[i].descriptorCount = 1;
        writes[i].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
        writes[i].pBufferInfo = &infos[i];
    }
    vkUpdateDescriptorSets(rt->device, 3, writes, 0, NULL);

    VkCommandBufferAllocateInfo cbai = {
        .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,
        .commandPool = rt->command_pool,
        .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY,
        .commandBufferCount = 1,
    };
    fail_stage = "allocate-command-buffer";
    rc = vkAllocateCommandBuffers(rt->device, &cbai, &command_buffer);
    if (rc != VK_SUCCESS) { fail_result = rc; goto fail; }

    double dispatch_start = now_ms();
    VkCommandBufferBeginInfo cbi = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
    fail_stage = "begin-command-buffer";
    rc = vkBeginCommandBuffer(command_buffer, &cbi);
    if (rc != VK_SUCCESS) { fail_result = rc; goto fail; }
    vkCmdBindPipeline(command_buffer, VK_PIPELINE_BIND_POINT_COMPUTE, rt->pipeline);
    vkCmdBindDescriptorSets(command_buffer, VK_PIPELINE_BIND_POINT_COMPUTE, rt->pipeline_layout, 0, 1, &descriptor_set, 0, NULL);
    vkCmdDispatch(command_buffer, (uint32_t)((n + 127) / 128), 1, 1);
    fail_stage = "end-command-buffer";
    rc = vkEndCommandBuffer(command_buffer);
    if (rc != VK_SUCCESS) { fail_result = rc; goto fail; }
    VkFenceCreateInfo fci = {.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
    fail_stage = "create-fence";
    rc = vkCreateFence(rt->device, &fci, NULL, &fence);
    if (rc != VK_SUCCESS) { fail_result = rc; goto fail; }
    VkSubmitInfo submit = {
        .sType = VK_STRUCTURE_TYPE_SUBMIT_INFO,
        .commandBufferCount = 1,
        .pCommandBuffers = &command_buffer,
    };
    fail_stage = "queue-submit";
    rc = vkQueueSubmit(rt->queue, 1, &submit, fence);
    if (rc != VK_SUCCESS) { fail_result = rc; goto fail; }
    fail_stage = "wait-fence";
    rc = vkWaitForFences(rt->device, 1, &fence, VK_TRUE, UINT64_MAX);
    if (rc != VK_SUCCESS) { fail_result = rc; goto fail; }
    double dispatch_ms = now_ms() - dispatch_start;

    double download_start = now_ms();
    memcpy(out, buffers[2].map, bytes);
    double download_ms = now_ms() - download_start;

    double max_err = 0.0;
    for (size_t i = 0; i < n; ++i) {
        double e = fabs((double)out[i] - (double)(a[i] + b[i]));
        if (e > max_err) max_err = e;
    }
    const int valid = max_err <= 0.0001;
    fprintf(json_out(),
            "{\"executor\":\"pdocker-gpu-executor\",\"api\":\"%s\",\"abi_version\":\"%s\","
            "\"role\":\"%s\",\"llm_engine\":\"%s\",\"device_independent\":true,"
            "\"backend_impl\":\"android_vulkan\",\"backend_affinity\":\"same-api\","
            "\"backend_cached\":%s,\"transport\":\"%s\","
            "\"kernel\":\"vector_add\",\"problem_size\":\"n=%zu\","
            "\"init_ms\":%.4f,\"compile_ms\":%.4f,\"upload_ms\":%.4f,"
            "\"dispatch_ms\":%.4f,\"download_ms\":%.4f,\"total_ms\":%.4f,"
            "\"max_abs_error\":%.8f,\"valid\":%s}\n",
            PDOCKER_GPU_COMMAND_API, PDOCKER_GPU_ABI_VERSION,
            PDOCKER_GPU_EXECUTOR_ROLE, PDOCKER_GPU_LLM_ENGINE_LOCATION,
            was_ready ? "true" : "false",
            transport ? transport : "vulkan-local-process-buffer",
            n, init_ms, compile_ms, upload_ms, dispatch_ms, download_ms,
            init_ms + compile_ms + upload_ms + dispatch_ms + download_ms, max_err,
            valid ? "true" : "false");
    fflush(json_out());

    if (fence) vkDestroyFence(rt->device, fence, NULL);
    if (command_buffer) vkFreeCommandBuffers(rt->device, rt->command_pool, 1, &command_buffer);
    if (descriptor_pool) vkDestroyDescriptorPool(rt->device, descriptor_pool, NULL);
    destroy_vulkan_vector_buffer(rt->device, &buffers[0]);
    destroy_vulkan_vector_buffer(rt->device, &buffers[1]);
    destroy_vulkan_vector_buffer(rt->device, &buffers[2]);
    return valid ? 0 : 6;

fail:
    fprintf(stderr, "pdocker-gpu-executor: Vulkan vector_add cached failed stage=%s rc=%d\n", fail_stage, fail_result);
    if (fence) vkDestroyFence(rt->device, fence, NULL);
    if (command_buffer) vkFreeCommandBuffers(rt->device, rt->command_pool, 1, &command_buffer);
    if (descriptor_pool) vkDestroyDescriptorPool(rt->device, descriptor_pool, NULL);
    destroy_vulkan_vector_buffer(rt->device, &buffers[0]);
    destroy_vulkan_vector_buffer(rt->device, &buffers[1]);
    destroy_vulkan_vector_buffer(rt->device, &buffers[2]);
    return -21;
}

static int run_vector_add_arrays_opencl(const float *a, const float *b, float *out, size_t n, const char *transport) {
    OpenClBackend cl;
    double init_start = now_ms();
    int load_rc = load_opencl_backend(&cl);
    if (load_rc != 0) return load_rc;
    double init_ms = now_ms() - init_start;

    const size_t bytes = n * sizeof(float);
    ocl_int err = OCL_SUCCESS;
    ocl_mem buf_a = NULL;
    ocl_mem buf_b = NULL;
    ocl_mem buf_o = NULL;
    ocl_program program = NULL;
    ocl_kernel kernel = NULL;
    double upload_start = now_ms();
    buf_a = cl.clCreateBuffer(cl.context, OCL_MEM_READ_WRITE | OCL_MEM_COPY_HOST_PTR, bytes, (void *)a, &err);
    if (err != OCL_SUCCESS || !buf_a) goto fail;
    buf_b = cl.clCreateBuffer(cl.context, OCL_MEM_READ_WRITE | OCL_MEM_COPY_HOST_PTR, bytes, (void *)b, &err);
    if (err != OCL_SUCCESS || !buf_b) goto fail;
    buf_o = cl.clCreateBuffer(cl.context, OCL_MEM_READ_WRITE, bytes, NULL, &err);
    if (err != OCL_SUCCESS || !buf_o) goto fail;
    double upload_ms = now_ms() - upload_start;

    double compile_start = now_ms();
    const char *src =
        "__kernel void pdocker_vector_add(__global const float *a, __global const float *b, __global float *out, const uint n) {\n"
        "  size_t i = get_global_id(0);\n"
        "  if (i < n) out[i] = a[i] + b[i];\n"
        "}\n";
    program = cl.clCreateProgramWithSource(cl.context, 1, &src, NULL, &err);
    if (err != OCL_SUCCESS || !program) goto fail;
    err = cl.clBuildProgram(program, 1, &cl.device, "", NULL, NULL);
    if (err != OCL_SUCCESS) {
        if (cl.clGetProgramBuildInfo) {
            char log[1024];
            size_t log_size = 0;
            if (cl.clGetProgramBuildInfo(program, cl.device, 0x1183, sizeof(log), log, &log_size) == OCL_SUCCESS) {
                fprintf(stderr, "pdocker-gpu-executor: OpenCL build failed: %.*s\n", (int)(log_size < sizeof(log) ? log_size : sizeof(log)), log);
            }
        }
        goto fail_program;
    }
    kernel = cl.clCreateKernel(program, "pdocker_vector_add", &err);
    if (err != OCL_SUCCESS || !kernel) goto fail_program;
    double compile_ms = now_ms() - compile_start;

    double dispatch_start = now_ms();
    ocl_uint count = (ocl_uint)n;
    cl.clSetKernelArg(kernel, 0, sizeof(buf_a), &buf_a);
    cl.clSetKernelArg(kernel, 1, sizeof(buf_b), &buf_b);
    cl.clSetKernelArg(kernel, 2, sizeof(buf_o), &buf_o);
    cl.clSetKernelArg(kernel, 3, sizeof(count), &count);
    size_t global = n;
    err = cl.clEnqueueNDRangeKernel(cl.queue, kernel, 1, NULL, &global, NULL, 0, NULL, NULL);
    if (err != OCL_SUCCESS) goto fail_kernel;
    cl.clFinish(cl.queue);
    double dispatch_ms = now_ms() - dispatch_start;

    double download_start = now_ms();
    err = cl.clEnqueueReadBuffer(cl.queue, buf_o, OCL_TRUE, 0, bytes, out, 0, NULL, NULL);
    if (err != OCL_SUCCESS) goto fail_kernel;
    cl.clFinish(cl.queue);
    double download_ms = now_ms() - download_start;

    double max_err = 0.0;
    for (size_t i = 0; i < n; ++i) {
        double e = fabs((double)out[i] - (double)(a[i] + b[i]));
        if (e > max_err) max_err = e;
    }
    const int valid = max_err <= 0.0001;
    fprintf(json_out(),
            "{\"executor\":\"pdocker-gpu-executor\",\"api\":\"%s\",\"abi_version\":\"%s\","
            "\"role\":\"%s\",\"llm_engine\":\"%s\",\"device_independent\":true,"
            "\"backend_impl\":\"android_opencl\",\"backend_affinity\":\"same-api\",\"transport\":\"%s\","
            "\"kernel\":\"vector_add\",\"problem_size\":\"n=%zu\","
            "\"init_ms\":%.4f,\"compile_ms\":%.4f,\"upload_ms\":%.4f,"
            "\"dispatch_ms\":%.4f,\"download_ms\":%.4f,\"total_ms\":%.4f,"
            "\"max_abs_error\":%.8f,\"valid\":%s}\n",
            PDOCKER_GPU_COMMAND_API, PDOCKER_GPU_ABI_VERSION,
            PDOCKER_GPU_EXECUTOR_ROLE, PDOCKER_GPU_LLM_ENGINE_LOCATION,
            transport ? transport : "opencl-local-process-buffer",
            n, init_ms, compile_ms, upload_ms, dispatch_ms, download_ms,
            init_ms + compile_ms + upload_ms + dispatch_ms + download_ms, max_err,
            valid ? "true" : "false");
    fflush(json_out());
    cl.clReleaseKernel(kernel);
    cl.clReleaseProgram(program);
    cl.clReleaseMemObject(buf_a);
    cl.clReleaseMemObject(buf_b);
    cl.clReleaseMemObject(buf_o);
    close_opencl_backend(&cl);
    return valid ? 0 : 6;

fail_kernel:
    if (kernel) cl.clReleaseKernel(kernel);
fail_program:
    if (program) cl.clReleaseProgram(program);
fail:
    if (buf_a) cl.clReleaseMemObject(buf_a);
    if (buf_b) cl.clReleaseMemObject(buf_b);
    if (buf_o) cl.clReleaseMemObject(buf_o);
    close_opencl_backend(&cl);
    return -7;
}

typedef struct {
    EGLDisplay display;
    EGLSurface surface;
    EGLContext context;
} GpuContext;

static int init_gpu_context(GpuContext *ctx) {
    memset(ctx, 0, sizeof(*ctx));
    ctx->display = eglGetDisplay(EGL_DEFAULT_DISPLAY);
    if (ctx->display == EGL_NO_DISPLAY) {
        json_fail("egl", "eglGetDisplay failed");
        return 10;
    }
    EGLint major = 0, minor = 0;
    if (!eglInitialize(ctx->display, &major, &minor)) {
        json_fail("egl", "eglInitialize failed");
        return 11;
    }
    const EGLint attrs[] = {
        EGL_RENDERABLE_TYPE, EGL_OPENGL_ES3_BIT_KHR,
        EGL_SURFACE_TYPE, EGL_PBUFFER_BIT,
        EGL_RED_SIZE, 8,
        EGL_GREEN_SIZE, 8,
        EGL_BLUE_SIZE, 8,
        EGL_NONE,
    };
    EGLConfig config;
    EGLint count = 0;
    if (!eglChooseConfig(ctx->display, attrs, &config, 1, &count) || count <= 0) {
        eglTerminate(ctx->display);
        json_fail("egl", "OpenGL ES 3 config unavailable");
        return 12;
    }
    const EGLint ctx_attrs[] = { EGL_CONTEXT_CLIENT_VERSION, 3, EGL_NONE };
    ctx->context = eglCreateContext(ctx->display, config, EGL_NO_CONTEXT, ctx_attrs);
    if (ctx->context == EGL_NO_CONTEXT) {
        eglTerminate(ctx->display);
        json_fail("egl", "eglCreateContext failed");
        return 13;
    }
    const EGLint surf_attrs[] = { EGL_WIDTH, 1, EGL_HEIGHT, 1, EGL_NONE };
    ctx->surface = eglCreatePbufferSurface(ctx->display, config, surf_attrs);
    if (ctx->surface == EGL_NO_SURFACE) {
        eglDestroyContext(ctx->display, ctx->context);
        eglTerminate(ctx->display);
        json_fail("egl", "eglCreatePbufferSurface failed");
        return 14;
    }
    if (!eglMakeCurrent(ctx->display, ctx->surface, ctx->surface, ctx->context)) {
        eglDestroySurface(ctx->display, ctx->surface);
        eglDestroyContext(ctx->display, ctx->context);
        eglTerminate(ctx->display);
        json_fail("egl", "eglMakeCurrent failed");
        return 15;
    }
    return 0;
}

static void destroy_gpu_context(GpuContext *ctx) {
    if (!ctx || !ctx->display) return;
    eglMakeCurrent(ctx->display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
    if (ctx->surface) eglDestroySurface(ctx->display, ctx->surface);
    if (ctx->context) eglDestroyContext(ctx->display, ctx->context);
    eglTerminate(ctx->display);
    memset(ctx, 0, sizeof(*ctx));
}

static int run_vector_add_arrays(const float *a, const float *b, float *out, size_t n, const char *transport) {
    const size_t bytes = n * sizeof(float);

    double compile_start = now_ms();
    const char *src =
        "#version 310 es\n"
        "layout(local_size_x = 128) in;\n"
        "layout(std430, binding = 0) readonly buffer A { float a[]; };\n"
        "layout(std430, binding = 1) readonly buffer B { float b[]; };\n"
        "layout(std430, binding = 2) writeonly buffer O { float o[]; };\n"
        "uniform uint u_count;\n"
        "void main() {\n"
        "  uint i = gl_GlobalInvocationID.x;\n"
        "  if (i < u_count) o[i] = a[i] + b[i];\n"
        "}\n";
    GLuint shader = compile_shader(src);
    if (!shader) {
        json_fail("compile", "compute shader compile failed");
        return 3;
    }
    GLuint program = link_program(shader);
    glDeleteShader(shader);
    if (!program) {
        json_fail("link", "compute program link failed");
        return 4;
    }
    double compile_ms = now_ms() - compile_start;

    double upload_start = now_ms();
    GLuint buf_a = make_ssbo(0, a, bytes, GL_STATIC_DRAW);
    GLuint buf_b = make_ssbo(1, b, bytes, GL_STATIC_DRAW);
    GLuint buf_o = make_ssbo(2, NULL, bytes, GL_DYNAMIC_READ);
    glFinish();
    double upload_ms = now_ms() - upload_start;

    double dispatch_start = now_ms();
    glUseProgram(program);
    GLint loc = glGetUniformLocation(program, "u_count");
    glUniform1ui(loc, (GLuint)n);
    glDispatchCompute((GLuint)((n + 127) / 128), 1, 1);
    glMemoryBarrier(GL_SHADER_STORAGE_BARRIER_BIT | GL_BUFFER_UPDATE_BARRIER_BIT);
    glFinish();
    double dispatch_ms = now_ms() - dispatch_start;

    double download_start = now_ms();
    glBindBuffer(GL_SHADER_STORAGE_BUFFER, buf_o);
    void *mapped = glMapBufferRange(GL_SHADER_STORAGE_BUFFER, 0, (GLsizeiptr)bytes, GL_MAP_READ_BIT);
    if (!mapped) {
        glDeleteBuffers(1, &buf_a);
        glDeleteBuffers(1, &buf_b);
        glDeleteBuffers(1, &buf_o);
        glDeleteProgram(program);
        json_fail("download", "glMapBufferRange failed");
        return 5;
    }
    memcpy(out, mapped, bytes);
    glUnmapBuffer(GL_SHADER_STORAGE_BUFFER);
    double download_ms = now_ms() - download_start;

    double max_err = 0.0;
    for (size_t i = 0; i < n; ++i) {
        double err = fabs((double)out[i] - (double)(a[i] + b[i]));
        if (err > max_err) max_err = err;
    }

    glDeleteBuffers(1, &buf_a);
    glDeleteBuffers(1, &buf_b);
    glDeleteBuffers(1, &buf_o);
    glDeleteProgram(program);

    const int valid = max_err <= 0.0001;
    fprintf(json_out(),
            "{\"executor\":\"pdocker-gpu-executor\",\"api\":\"%s\",\"abi_version\":\"%s\","
            "\"role\":\"%s\",\"llm_engine\":\"%s\",\"device_independent\":true,"
            "\"backend_impl\":\"gles31_compute\",\"backend_affinity\":\"fallback\",\"transport\":\"%s\","
            "\"kernel\":\"vector_add\",\"problem_size\":\"n=%zu\","
            "\"compile_ms\":%.4f,\"upload_ms\":%.4f,\"dispatch_ms\":%.4f,"
            "\"download_ms\":%.4f,\"total_ms\":%.4f,\"max_abs_error\":%.8f,"
            "\"valid\":%s}\n",
            PDOCKER_GPU_COMMAND_API, PDOCKER_GPU_ABI_VERSION,
            PDOCKER_GPU_EXECUTOR_ROLE, PDOCKER_GPU_LLM_ENGINE_LOCATION,
            transport ? transport : "local-process-buffer",
            n, compile_ms, upload_ms, dispatch_ms, download_ms,
            compile_ms + upload_ms + dispatch_ms + download_ms, max_err,
            valid ? "true" : "false");
    fflush(json_out());
    return valid ? 0 : 6;
}

static int run_vector_add_arrays_best(const float *a, const float *b, float *out, size_t n, const char *transport, GpuApiAffinity affinity) {
    if (affinity == GPU_API_VULKAN && strcmp(getenv("PDOCKER_GPU_DISABLE_ANDROID_VULKAN") ? getenv("PDOCKER_GPU_DISABLE_ANDROID_VULKAN") : "0", "1") != 0) {
        int rc = run_vector_add_arrays_vulkan(a, b, out, n, transport ? transport : "vulkan-command-queue");
        if (rc == 0) return 0;
        fprintf(stderr, "pdocker-gpu-executor: Android Vulkan vector_add unavailable rc=%d; falling back to GLES compute (cross-api fallback)\n", rc);
        return run_vector_add_arrays(a, b, out, n, transport ? transport : "vulkan-to-gles31-fallback");
    }
    if ((affinity == GPU_API_OPENCL || affinity == GPU_API_AUTO) &&
        strcmp(getenv("PDOCKER_GPU_DISABLE_ANDROID_OPENCL") ? getenv("PDOCKER_GPU_DISABLE_ANDROID_OPENCL") : "0", "1") != 0) {
        int rc = run_vector_add_arrays_opencl(a, b, out, n, transport ? transport : "opencl-command-queue");
        if (rc == 0) return 0;
        fprintf(stderr, "pdocker-gpu-executor: Android OpenCL vector_add unavailable rc=%d; falling back to GLES compute (cross-api fallback)\n", rc);
    }
    return run_vector_add_arrays(a, b, out, n, transport ? transport : "gles31-fallback");
}

static int run_vector_add(void) {
    const size_t n = PDOCKER_GPU_VECTOR_ADD_DEFAULT_N;
    const size_t bytes = n * sizeof(float);
    float *a = (float *)malloc(bytes);
    float *b = (float *)malloc(bytes);
    float *out = (float *)calloc(n, sizeof(float));
    if (!a || !b || !out) {
        free(a);
        free(b);
        free(out);
        json_fail("alloc", "host allocation failed");
        return 2;
    }
    fill_inputs(a, b, n);
    int rc = run_vector_add_arrays_best(a, b, out, n, "local-process-buffer", GPU_API_AUTO);
    free(a);
    free(b);
    free(out);
    return rc;
}

static int run_vector_add_fd(int fd, size_t n, GpuApiAffinity affinity) {
    if (fd < 0) {
        json_fail("fd", "missing shared buffer fd");
        return 64;
    }
    if (n == 0 || n > PDOCKER_GPU_VECTOR_ADD_MAX_N) {
        json_fail("fd", "invalid vector size");
        return 64;
    }
    const size_t bytes = n * sizeof(float);
    const size_t total = bytes * 3;
    void *map = mmap(NULL, total, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (map == MAP_FAILED) {
        json_fail("mmap", strerror(errno));
        close(fd);
        return 70;
    }
    float *a = (float *)map;
    float *b = a + n;
    float *out = b + n;
    int rc = run_vector_add_arrays_best(a, b, out, n, "unix-socket-scm-rights-shared-buffer", affinity);
    munmap(map, total);
    close(fd);
    return rc;
}

static int run_vector_add_3fd(int fd_a, int fd_b, int fd_out, size_t n, GpuApiAffinity affinity) {
    if (fd_a < 0 || fd_b < 0 || fd_out < 0) {
        if (fd_a >= 0) close(fd_a);
        if (fd_b >= 0) close(fd_b);
        if (fd_out >= 0) close(fd_out);
        json_fail("fd", "missing vector buffer fd");
        return 64;
    }
    if (n == 0 || n > PDOCKER_GPU_VECTOR_ADD_MAX_N) {
        close(fd_a);
        close(fd_b);
        close(fd_out);
        json_fail("fd", "invalid vector size");
        return 64;
    }
    const size_t bytes = n * sizeof(float);
    void *map_a = mmap(NULL, bytes, PROT_READ | PROT_WRITE, MAP_SHARED, fd_a, 0);
    void *map_b = mmap(NULL, bytes, PROT_READ | PROT_WRITE, MAP_SHARED, fd_b, 0);
    void *map_out = mmap(NULL, bytes, PROT_READ | PROT_WRITE, MAP_SHARED, fd_out, 0);
    close(fd_a);
    close(fd_b);
    close(fd_out);
    if (map_a == MAP_FAILED || map_b == MAP_FAILED || map_out == MAP_FAILED) {
        if (map_a != MAP_FAILED) munmap(map_a, bytes);
        if (map_b != MAP_FAILED) munmap(map_b, bytes);
        if (map_out != MAP_FAILED) munmap(map_out, bytes);
        json_fail("mmap", strerror(errno));
        return 70;
    }
    int rc = run_vector_add_arrays_best((const float *)map_a, (const float *)map_b, (float *)map_out, n,
                                        affinity == GPU_API_VULKAN ? "vulkan-icd-scm-rights-3buffer" : "opencl-icd-scm-rights-3buffer",
                                        affinity);
    munmap(map_a, bytes);
    munmap(map_b, bytes);
    munmap(map_out, bytes);
    return rc;
}

static int hex_decode(const char *hex, uint8_t *out, size_t out_size) {
    if (!hex || !out) return -1;
    size_t len = strlen(hex);
    if ((len % 2) != 0 || len / 2 > out_size) return -1;
    for (size_t i = 0; i < len / 2; ++i) {
        char hi = hex[i * 2];
        char lo = hex[i * 2 + 1];
        int hv = (hi >= '0' && hi <= '9') ? hi - '0' : (hi >= 'a' && hi <= 'f') ? hi - 'a' + 10 : (hi >= 'A' && hi <= 'F') ? hi - 'A' + 10 : -1;
        int lv = (lo >= '0' && lo <= '9') ? lo - '0' : (lo >= 'a' && lo <= 'f') ? lo - 'a' + 10 : (lo >= 'A' && lo <= 'F') ? lo - 'A' + 10 : -1;
        if (hv < 0 || lv < 0) return -1;
        out[i] = (uint8_t)((hv << 4) | lv);
    }
    return (int)(len / 2);
}

static int read_fd_exact(int fd, void *buf, size_t size, off_t offset) {
    uint8_t *p = (uint8_t *)buf;
    size_t done = 0;
    while (done < size) {
        ssize_t n = pread(fd, p + done, size - done, offset + (off_t)done);
        if (n < 0) return -errno;
        if (n == 0) return -EIO;
        done += (size_t)n;
    }
    return 0;
}

static int write_fd_exact(int fd, const void *buf, size_t size, off_t offset) {
    const uint8_t *p = (const uint8_t *)buf;
    size_t done = 0;
    while (done < size) {
        ssize_t n = pwrite(fd, p + done, size - done, offset + (off_t)done);
        if (n < 0) return -errno;
        if (n == 0) return -EIO;
        done += (size_t)n;
    }
    return 0;
}

static int write_dirty_pages_exact(
        int fd,
        const void *buf,
        size_t size,
        off_t offset,
        size_t page_size,
        const unsigned char *dirty_pages,
        size_t page_count,
        size_t *written_bytes) {
    const uint8_t *bytes = (const uint8_t *)buf;
    size_t written = 0;
    if (written_bytes) *written_bytes = 0;
    if (!bytes || !dirty_pages || page_size == 0) return -EINVAL;
    for (size_t page = 0; page < page_count;) {
        if (!dirty_pages[page]) {
            page++;
            continue;
        }
        size_t start_page = page;
        while (page < page_count && dirty_pages[page]) page++;
        size_t start = start_page * page_size;
        size_t end = page * page_size;
        if (start >= size) break;
        if (end > size) end = size;
        int rc = write_fd_exact(fd, bytes + start, end - start, offset + (off_t)start);
        if (rc != 0) return rc;
        written += end - start;
    }
    if (written_bytes) *written_bytes = written;
    return 0;
}

static int run_vulkan_dispatch_fd(
        int shader_fd,
        const int *buffer_fds,
        const VulkanDispatchBinding *bindings,
        size_t binding_count,
        size_t shader_size,
        const char *entry_name,
        const VulkanDispatchSpecialization *specializations,
        size_t specialization_count,
        const uint8_t *specialization_data,
        size_t specialization_data_size,
        const VulkanDispatchOptions *options,
        const uint8_t *push,
        size_t push_size,
        uint32_t gx,
        uint32_t gy,
        uint32_t gz) {
    if (shader_fd < 0 || !buffer_fds || !bindings || binding_count == 0 ||
        binding_count > PDOCKER_GPU_MAX_VULKAN_BINDINGS || shader_size == 0 ||
        shader_size > 8 * 1024 * 1024 || push_size > PDOCKER_GPU_MAX_PUSH_BYTES ||
        specialization_count > PDOCKER_GPU_MAX_VULKAN_SPECIALIZATION_ENTRIES ||
        specialization_data_size > PDOCKER_GPU_MAX_VULKAN_SPECIALIZATION_BYTES) {
        json_fail("vulkan-dispatch", "invalid dispatch metadata");
        return 64;
    }
    if (!entry_name || !entry_name[0]) entry_name = "main";
    const int was_ready = g_vulkan_runtime.ready;
    if (init_vulkan_runtime(&g_vulkan_runtime) != 0) return -21;
    VulkanRuntime *rt = &g_vulkan_runtime;
    const char *fail_stage = "start";
    VkResult rc = VK_SUCCESS;
    int ret = -21;
    int io_rc = 0;
    int fail_binding = -1;
    uint32_t max_binding = 0;
    for (size_t i = 0; i < binding_count; ++i) {
        if (bindings[i].binding > max_binding) max_binding = bindings[i].binding;
        if (bindings[i].size == 0 || bindings[i].size > 512 * 1024 * 1024) {
            json_fail("vulkan-dispatch", "invalid binding size");
            return 64;
        }
    }
    uint32_t layout_count = max_binding + 1;
    if (layout_count > PDOCKER_GPU_MAX_VULKAN_BINDINGS) {
        json_fail("vulkan-dispatch", "too many descriptor bindings");
        return 64;
    }

    uint32_t *shader_code = (uint32_t *)malloc(shader_size);
    VulkanVectorBuffer temp_buffers[PDOCKER_GPU_MAX_VULKAN_BINDINGS];
    VulkanVectorBuffer alias_temp_buffers[PDOCKER_GPU_MAX_VULKAN_BINDINGS];
    VulkanVectorBuffer *vk_buffers[PDOCKER_GPU_MAX_VULKAN_BINDINGS];
    int cache_hits[PDOCKER_GPU_MAX_VULKAN_BINDINGS];
    int cache_resident[PDOCKER_GPU_MAX_VULKAN_BINDINGS];
    int mutable_cache_hits[PDOCKER_GPU_MAX_VULKAN_BINDINGS];
    int mutable_cache_reused[PDOCKER_GPU_MAX_VULKAN_BINDINGS];
    double binding_upload_ms[PDOCKER_GPU_MAX_VULKAN_BINDINGS];
    double binding_download_ms[PDOCKER_GPU_MAX_VULKAN_BINDINGS];
    double binding_dirty_probe_ms[PDOCKER_GPU_MAX_VULKAN_BINDINGS];
    uint64_t binding_fd_before_hash[PDOCKER_GPU_MAX_VULKAN_BINDINGS];
    uint64_t binding_gpu_after_upload_hash[PDOCKER_GPU_MAX_VULKAN_BINDINGS];
    uint64_t binding_gpu_after_dispatch_hash[PDOCKER_GPU_MAX_VULKAN_BINDINGS];
    uint64_t binding_fd_after_hash[PDOCKER_GPU_MAX_VULKAN_BINDINGS];
    size_t binding_dirty_probe_pages[PDOCKER_GPU_MAX_VULKAN_BINDINGS];
    size_t binding_dirty_probe_bytes[PDOCKER_GPU_MAX_VULKAN_BINDINGS];
    int binding_dirty_writeback_cached[PDOCKER_GPU_MAX_VULKAN_BINDINGS];
    size_t binding_dirty_writeback_bytes[PDOCKER_GPU_MAX_VULKAN_BINDINGS];
    unsigned char *binding_dirty_probe_masks[PDOCKER_GPU_MAX_VULKAN_BINDINGS];
    VkDescriptorSetLayout set_layout = VK_NULL_HANDLE;
    VkPipelineLayout pipeline_layout = VK_NULL_HANDLE;
    VkShaderModule shader = VK_NULL_HANDLE;
    VkPipeline pipeline = VK_NULL_HANDLE;
    VulkanPipelineCacheEntry *pipeline_cache_entry = NULL;
    int pipeline_cache_hit = 0;
    VkDescriptorPool descriptor_pool = VK_NULL_HANDLE;
    VkCommandBuffer command_buffer = VK_NULL_HANDLE;
    VkFence fence = VK_NULL_HANDLE;
    VkSpecializationMapEntry vk_spec_entries[PDOCKER_GPU_MAX_VULKAN_SPECIALIZATION_ENTRIES];
    VkSpecializationInfo vk_spec_info;
    const VkSpecializationInfo *vk_spec_ptr = NULL;
    SpirvTraceSummary spirv_summary;
    VulkanBindingAlias binding_aliases[PDOCKER_GPU_MAX_VULKAN_BINDINGS];
    size_t binding_alias_count = 0;
    int have_spirv_summary = 0;
    int specialization_materialized = 0;
    int local_size_patched = 0;
    const int skip_unused_descriptor_transfers =
        options && options->has_skip_unused_descriptor_transfers
            ? options->skip_unused_descriptor_transfers
            : env_truthy("PDOCKER_GPU_SKIP_UNUSED_DESCRIPTOR_TRANSFERS", 1);
    const int use_spirv_descriptor_access =
        options && options->has_use_spirv_descriptor_access
            ? options->use_spirv_descriptor_access
            : env_truthy("PDOCKER_GPU_USE_SPIRV_DESCRIPTOR_ACCESS", 1);
    const int disable_overlap_aliasing =
        options && options->has_disable_overlap_aliasing
            ? options->disable_overlap_aliasing
            : env_truthy("PDOCKER_GPU_DISABLE_OVERLAP_ALIASING", 0);
    const int cpu_oracle_requested =
        options && options->has_cpu_oracle
            ? options->cpu_oracle
            : env_truthy("PDOCKER_GPU_CPU_ORACLE", 0);
    CpuOracleReport cpu_oracle_report;
    init_cpu_oracle_report(&cpu_oracle_report, cpu_oracle_requested, 0);
    const int dirty_probe_enabled = options && options->has_dirty_probe
        ? options->dirty_probe
        : writeonly_dirty_probe_enabled();
    const int dirty_writeback_enabled = options && options->has_dirty_writeback
        ? options->dirty_writeback
        : writeonly_dirty_writeback_enabled();
    const size_t dirty_probe_min_bytes = options && options->has_dirty_probe_min_bytes
        ? options->dirty_probe_min_bytes
        : writeonly_dirty_probe_min_bytes();
    const size_t dirty_probe_pagesize = dirty_probe_page_size();
    const int writeonly_scratch_enabled = options && options->has_writeonly_buffer_cache
        ? options->writeonly_buffer_cache
        : writeonly_buffer_cache_enabled();
    const size_t mutable_cache_max_bytes = options && options->has_mutable_buffer_cache_max_bytes
        ? options->mutable_buffer_cache_max_bytes
        : mutable_buffer_cache_max_bytes();
    const int profile_response = options && options->has_profile_response
        ? options->profile_response
        : env_truthy("PDOCKER_GPU_DISPATCH_PROFILE_RESPONSE", 0);
    const int materialize_descriptor_aliases =
        options && options->has_materialize_descriptor_aliases
            ? options->materialize_descriptor_aliases
            : env_truthy("PDOCKER_GPU_MATERIALIZE_DESCRIPTOR_ALIASES", 0);
    uint8_t shader_used_bindings[PDOCKER_GPU_MAX_VULKAN_BINDINGS];
    SpirvDescriptorAccess shader_binding_access[PDOCKER_GPU_MAX_VULKAN_BINDINGS];
    uint8_t active_bindings[PDOCKER_GPU_MAX_VULKAN_BINDINGS];
    uint8_t binding_read_needed[PDOCKER_GPU_MAX_VULKAN_BINDINGS];
    uint8_t binding_write_needed[PDOCKER_GPU_MAX_VULKAN_BINDINGS];
    uint8_t binding_group_read_needed[PDOCKER_GPU_MAX_VULKAN_BINDINGS];
    size_t binding_alias_rep[PDOCKER_GPU_MAX_VULKAN_BINDINGS];
    off_t binding_group_base[PDOCKER_GPU_MAX_VULKAN_BINDINGS];
    off_t binding_group_end[PDOCKER_GPU_MAX_VULKAN_BINDINGS];
    size_t binding_gpu_offset[PDOCKER_GPU_MAX_VULKAN_BINDINGS];
    uint8_t binding_group_span_seen[PDOCKER_GPU_MAX_VULKAN_BINDINGS];
    dev_t binding_fd_dev[PDOCKER_GPU_MAX_VULKAN_BINDINGS];
    ino_t binding_fd_ino[PDOCKER_GPU_MAX_VULKAN_BINDINGS];
    memset(temp_buffers, 0, sizeof(temp_buffers));
    memset(alias_temp_buffers, 0, sizeof(alias_temp_buffers));
    memset(vk_buffers, 0, sizeof(vk_buffers));
    memset(cache_hits, 0, sizeof(cache_hits));
    memset(cache_resident, 0, sizeof(cache_resident));
    memset(mutable_cache_hits, 0, sizeof(mutable_cache_hits));
    memset(mutable_cache_reused, 0, sizeof(mutable_cache_reused));
    memset(binding_upload_ms, 0, sizeof(binding_upload_ms));
    memset(binding_download_ms, 0, sizeof(binding_download_ms));
    memset(binding_dirty_probe_ms, 0, sizeof(binding_dirty_probe_ms));
    memset(binding_fd_before_hash, 0, sizeof(binding_fd_before_hash));
    memset(binding_gpu_after_upload_hash, 0, sizeof(binding_gpu_after_upload_hash));
    memset(binding_gpu_after_dispatch_hash, 0, sizeof(binding_gpu_after_dispatch_hash));
    memset(binding_fd_after_hash, 0, sizeof(binding_fd_after_hash));
    memset(binding_dirty_probe_pages, 0, sizeof(binding_dirty_probe_pages));
    memset(binding_dirty_probe_bytes, 0, sizeof(binding_dirty_probe_bytes));
    memset(binding_dirty_writeback_cached, 0, sizeof(binding_dirty_writeback_cached));
    memset(binding_dirty_writeback_bytes, 0, sizeof(binding_dirty_writeback_bytes));
    memset(binding_dirty_probe_masks, 0, sizeof(binding_dirty_probe_masks));
    memset(vk_spec_entries, 0, sizeof(vk_spec_entries));
    memset(&vk_spec_info, 0, sizeof(vk_spec_info));
    memset(&spirv_summary, 0, sizeof(spirv_summary));
    memset(binding_aliases, 0, sizeof(binding_aliases));
    memset(shader_used_bindings, 0, sizeof(shader_used_bindings));
    memset(shader_binding_access, 0, sizeof(shader_binding_access));
    memset(active_bindings, 0, sizeof(active_bindings));
    memset(binding_read_needed, 0, sizeof(binding_read_needed));
    memset(binding_write_needed, 0, sizeof(binding_write_needed));
    memset(binding_group_read_needed, 0, sizeof(binding_group_read_needed));
    memset(binding_alias_rep, 0, sizeof(binding_alias_rep));
    memset(binding_group_base, 0, sizeof(binding_group_base));
    memset(binding_group_end, 0, sizeof(binding_group_end));
    memset(binding_gpu_offset, 0, sizeof(binding_gpu_offset));
    memset(binding_group_span_seen, 0, sizeof(binding_group_span_seen));
    memset(binding_fd_dev, 0, sizeof(binding_fd_dev));
    memset(binding_fd_ino, 0, sizeof(binding_fd_ino));
    if (!shader_code) return -21;
    if (read_fd_exact(shader_fd, shader_code, shader_size, 0) != 0) goto cleanup;
    const int materialize_specialization_constants =
        options && options->has_materialize_specialization_constants
            ? options->materialize_specialization_constants
            : env_truthy("PDOCKER_GPU_MATERIALIZE_SPIRV_SPECIALIZATION_CONSTANTS", 0);
    const int disable_pipeline_optimization =
        options && options->has_disable_pipeline_optimization
            ? options->disable_pipeline_optimization
            : env_truthy("PDOCKER_GPU_DISABLE_PIPELINE_OPTIMIZATION", 1);
    if (materialize_specialization_constants) {
        specialization_materialized = materialize_spirv_specialization_constants(
            shader_code,
            &shader_size,
            specializations,
            specialization_count,
            specialization_data,
            specialization_data_size);
    }
    local_size_patched = patch_spirv_literal_local_size_from_spec(
        shader_code,
        shader_size,
        specializations,
        specialization_count,
        specialization_data,
        specialization_data_size);
    const int rewrite_duplicate_descriptors =
        options && options->has_rewrite_duplicate_descriptors
            ? options->rewrite_duplicate_descriptors
            : env_truthy("PDOCKER_GPU_REWRITE_DUPLICATE_DESCRIPTOR_BINDINGS", 1);
    if (rewrite_duplicate_descriptors) {
        if (rewrite_duplicate_descriptor_bindings(
                shader_code,
                shader_size,
                bindings,
                binding_count,
                binding_aliases,
                &binding_alias_count,
                &max_binding) != 0) {
            json_fail("vulkan-dispatch", "too many rewritten descriptor aliases");
            ret = 64;
            goto cleanup;
        }
        layout_count = max_binding + 1;
        if (layout_count > PDOCKER_GPU_MAX_VULKAN_BINDINGS) {
            json_fail("vulkan-dispatch", "too many descriptor aliases");
            ret = 64;
            goto cleanup;
        }
    }
    const uint64_t pipeline_policy_hash = vulkan_pipeline_policy_hash(
        rewrite_duplicate_descriptors,
        materialize_specialization_constants,
        specialization_materialized,
        disable_pipeline_optimization,
        skip_unused_descriptor_transfers,
        use_spirv_descriptor_access,
        disable_overlap_aliasing);
    spirv_summary = summarize_spirv(shader_code, shader_size);
    have_spirv_summary = 1;
    if (skip_unused_descriptor_transfers) {
        collect_spirv_descriptor_bindings(
            shader_code,
            shader_size,
            shader_used_bindings,
            sizeof(shader_used_bindings));
        if (use_spirv_descriptor_access) {
            collect_spirv_descriptor_accesses(
                shader_code,
                shader_size,
                shader_binding_access,
                sizeof(shader_binding_access) / sizeof(shader_binding_access[0]));
        }
        for (size_t i = 0; i < binding_alias_count; ++i) {
            if (binding_aliases[i].rewritten_binding < sizeof(shader_used_bindings) &&
                shader_used_bindings[binding_aliases[i].rewritten_binding] &&
                binding_aliases[i].original_binding < sizeof(shader_used_bindings)) {
                shader_used_bindings[binding_aliases[i].original_binding] = 1;
            }
            if (use_spirv_descriptor_access &&
                binding_aliases[i].rewritten_binding <
                    sizeof(shader_binding_access) / sizeof(shader_binding_access[0]) &&
                binding_aliases[i].original_binding <
                    sizeof(shader_binding_access) / sizeof(shader_binding_access[0])) {
                SpirvDescriptorAccess *original =
                    &shader_binding_access[binding_aliases[i].original_binding];
                const SpirvDescriptorAccess *rewritten =
                    &shader_binding_access[binding_aliases[i].rewritten_binding];
                original->used |= rewritten->used;
                original->readable |= rewritten->readable;
                original->writable |= rewritten->writable;
            }
        }
    }
    size_t active_binding_count = 0;
    size_t read_binding_count = 0;
    size_t write_binding_count = 0;
    size_t skipped_binding_count = 0;
    size_t skipped_binding_bytes = 0;
    size_t skipped_upload_bytes = 0;
    size_t skipped_download_bytes = 0;
    for (size_t i = 0; i < binding_count; ++i) {
        if (!skip_unused_descriptor_transfers ||
            (bindings[i].binding < sizeof(shader_used_bindings) &&
             shader_used_bindings[bindings[i].binding])) {
            active_bindings[i] = 1;
            active_binding_count++;
            if (!skip_unused_descriptor_transfers || !use_spirv_descriptor_access ||
                bindings[i].binding >= sizeof(shader_binding_access) / sizeof(shader_binding_access[0])) {
                binding_read_needed[i] = 1;
                binding_write_needed[i] = 1;
            } else {
                const SpirvDescriptorAccess *access = &shader_binding_access[bindings[i].binding];
                binding_read_needed[i] = access->readable;
                binding_write_needed[i] = access->writable;
            }
            if (binding_read_needed[i]) {
                read_binding_count++;
            } else {
                skipped_upload_bytes += bindings[i].size;
            }
            if (binding_write_needed[i]) {
                write_binding_count++;
            } else {
                skipped_download_bytes += bindings[i].size;
            }
        } else {
            skipped_binding_count++;
            skipped_binding_bytes += bindings[i].size;
        }
    }
    if (active_binding_count == 0) {
        json_fail("vulkan-dispatch", "shader uses no passed storage bindings");
        ret = 64;
        goto cleanup;
    }
    for (size_t i = 0; i < binding_count; ++i) {
        binding_alias_rep[i] = i;
        binding_group_read_needed[i] = binding_read_needed[i];
        binding_group_base[i] = bindings[i].offset;
        binding_group_end[i] = bindings[i].offset + (off_t)bindings[i].size;
        if (!active_bindings[i]) continue;
        struct stat st;
        if (fstat(buffer_fds[i], &st) == 0) {
            binding_fd_dev[i] = st.st_dev;
            binding_fd_ino[i] = st.st_ino;
        }
    }
    if (!disable_overlap_aliasing) {
        for (size_t i = 0; i < binding_count; ++i) {
            if (!active_bindings[i] || !binding_fd_ino[i]) continue;
            for (size_t j = 0; j < i; ++j) {
                if (!active_bindings[j] || !binding_fd_ino[j]) continue;
                const off_t i0 = bindings[i].offset;
                const off_t i1 = bindings[i].offset + (off_t)bindings[i].size;
                const off_t j0 = bindings[j].offset;
                const off_t j1 = bindings[j].offset + (off_t)bindings[j].size;
                if (binding_fd_dev[i] == binding_fd_dev[j] &&
                    binding_fd_ino[i] == binding_fd_ino[j] &&
                    i0 < j1 && j0 < i1) {
                    size_t old_rep = binding_alias_rep[i];
                    size_t new_rep = binding_alias_rep[j] < old_rep
                        ? binding_alias_rep[j]
                        : old_rep;
                    for (size_t k = 0; k < binding_count; ++k) {
                        if (binding_alias_rep[k] == old_rep ||
                            binding_alias_rep[k] == binding_alias_rep[j]) {
                            binding_alias_rep[k] = new_rep;
                        }
                    }
                    break;
                }
            }
        }
    }
    for (size_t i = 0; i < binding_count; ++i) {
        if (!active_bindings[i]) continue;
        size_t rep = binding_alias_rep[i];
        if (rep >= binding_count) continue;
        const off_t start = bindings[i].offset;
        const off_t end = bindings[i].offset + (off_t)bindings[i].size;
        if (!binding_group_span_seen[rep] || start < binding_group_base[rep]) {
            binding_group_base[rep] = start;
        }
        if (!binding_group_span_seen[rep] || end > binding_group_end[rep]) {
            binding_group_end[rep] = end;
        }
        binding_group_span_seen[rep] = 1;
    }
    for (size_t i = 0; i < binding_count; ++i) {
        if (!active_bindings[i]) continue;
        size_t rep = binding_alias_rep[i];
        if (rep < binding_count && binding_read_needed[i]) {
            binding_group_read_needed[rep] = 1;
        }
        if (rep < binding_count && bindings[i].offset >= binding_group_base[rep]) {
            binding_gpu_offset[i] = (size_t)(bindings[i].offset - binding_group_base[rep]);
        }
    }
    const uint64_t spec_hash = pipeline_specialization_hash(
        specializations,
        specialization_count,
        specialization_data,
        specialization_data_size);

    double upload_start = now_ms();
    for (size_t i = 0; i < binding_count; ++i) {
        if (!active_bindings[i]) continue;
        fail_binding = (int)i;
        fail_stage = "create-dispatch-buffer";
        double binding_start = now_ms();
        if (profile_response) {
            binding_fd_before_hash[i] = sample_fd_hash(
                buffer_fds[i], bindings[i].offset, bindings[i].size);
        }
        if (binding_alias_rep[i] != i && binding_alias_rep[i] < i &&
            vk_buffers[binding_alias_rep[i]]) {
            vk_buffers[i] = vk_buffers[binding_alias_rep[i]];
            cache_hits[i] = cache_hits[binding_alias_rep[i]];
            cache_resident[i] = cache_resident[binding_alias_rep[i]];
            mutable_cache_hits[i] = mutable_cache_hits[binding_alias_rep[i]];
            mutable_cache_reused[i] = mutable_cache_reused[binding_alias_rep[i]];
            binding_upload_ms[i] = now_ms() - binding_start;
            if (profile_response && vk_buffers[i]->map) {
                binding_gpu_after_upload_hash[i] =
                    sample_memory_hash((const unsigned char *)vk_buffers[i]->map + binding_gpu_offset[i],
                                       bindings[i].size);
            }
            continue;
        }
        VulkanDispatchBinding group_binding = bindings[i];
        if (binding_group_end[i] > binding_group_base[i]) {
            group_binding.offset = binding_group_base[i];
            group_binding.size = (size_t)(binding_group_end[i] - binding_group_base[i]);
        }
        vk_buffers[i] = acquire_dispatch_buffer(
            rt->physical_device,
            rt->device,
            buffer_fds[i],
            &group_binding,
            &temp_buffers[i],
            binding_group_read_needed[i],
            &cache_hits[i],
            &cache_resident[i],
            &mutable_cache_hits[i],
            &mutable_cache_reused[i],
            writeonly_scratch_enabled,
            mutable_cache_max_bytes);
        binding_upload_ms[i] = now_ms() - binding_start;
        if (!vk_buffers[i]) goto cleanup;
        if (profile_response && vk_buffers[i]->map) {
            binding_gpu_after_upload_hash[i] =
                sample_memory_hash((const unsigned char *)vk_buffers[i]->map + binding_gpu_offset[i],
                                   bindings[i].size);
        }
        if ((dirty_probe_enabled || dirty_writeback_enabled) &&
            binding_write_needed[i] &&
            !binding_read_needed[i] &&
            !cache_resident[i] &&
            bindings[i].size >= dirty_probe_min_bytes &&
            vk_buffers[i]->map) {
            size_t page_count = (bindings[i].size + dirty_probe_pagesize - 1) / dirty_probe_pagesize;
            if (dirty_writeback_enabled &&
                find_dirty_mask_cache_entry(
                    spirv_summary.hash,
                    spec_hash,
                    bindings[i].binding,
                    bindings[i].size,
                    dirty_probe_pagesize,
                    page_count)) {
                binding_dirty_writeback_cached[i] = 1;
                continue;
            }
            double probe_start = now_ms();
            memset(vk_buffers[i]->map,
                   PDOCKER_GPU_WRITEONLY_DIRTY_PROBE_SENTINEL,
                   bindings[i].size);
            binding_dirty_probe_ms[i] += now_ms() - probe_start;
        }
    }
    fail_binding = -1;
    double upload_ms = now_ms() - upload_start;

    if (specialization_count > 0) {
        for (size_t i = 0; i < specialization_count; ++i) {
            if (specializations[i].offset + specializations[i].size > specialization_data_size) {
                json_fail("vulkan-dispatch", "invalid specialization range");
                ret = 64;
                goto cleanup;
            }
            vk_spec_entries[i].constantID = specializations[i].constant_id;
            vk_spec_entries[i].offset = specializations[i].offset;
            vk_spec_entries[i].size = specializations[i].size;
        }
        vk_spec_info.mapEntryCount = (uint32_t)specialization_count;
        vk_spec_info.pMapEntries = vk_spec_entries;
        vk_spec_info.dataSize = specialization_data_size;
        vk_spec_info.pData = specialization_data;
        vk_spec_ptr = specialization_materialized ? NULL : &vk_spec_info;
    }
    pipeline_cache_entry = find_pipeline_cache_entry(
        spirv_summary.hash,
        spec_hash,
        pipeline_policy_hash,
        shader_size,
        specialization_data_size,
        specialization_count,
        layout_count,
        (uint32_t)push_size,
        entry_name);
    if (pipeline_cache_entry) {
        pipeline_cache_hit = 1;
        pipeline_cache_entry->hits++;
        set_layout = pipeline_cache_entry->set_layout;
        pipeline_layout = pipeline_cache_entry->pipeline_layout;
        shader = pipeline_cache_entry->shader;
        pipeline = pipeline_cache_entry->pipeline;
    } else {
        VkDescriptorSetLayoutBinding layout_bindings[PDOCKER_GPU_MAX_VULKAN_BINDINGS];
        memset(layout_bindings, 0, sizeof(layout_bindings));
        for (uint32_t i = 0; i < layout_count; ++i) {
            layout_bindings[i].binding = i;
            layout_bindings[i].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
            layout_bindings[i].descriptorCount = 1;
            layout_bindings[i].stageFlags = VK_SHADER_STAGE_COMPUTE_BIT;
        }
        VkDescriptorSetLayoutCreateInfo dslci = {
            .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO,
            .bindingCount = layout_count,
            .pBindings = layout_bindings,
        };
        fail_stage = "create-generic-descriptor-set-layout";
        rc = vkCreateDescriptorSetLayout(rt->device, &dslci, NULL, &set_layout);
        if (rc != VK_SUCCESS) goto cleanup;
        VkPushConstantRange push_range = {
            .stageFlags = VK_SHADER_STAGE_COMPUTE_BIT,
            .offset = 0,
            .size = (uint32_t)push_size,
        };
        VkPipelineLayoutCreateInfo plci = {
            .sType = VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO,
            .setLayoutCount = 1,
            .pSetLayouts = &set_layout,
            .pushConstantRangeCount = push_size ? 1u : 0u,
            .pPushConstantRanges = push_size ? &push_range : NULL,
        };
        fail_stage = "create-generic-pipeline-layout";
        rc = vkCreatePipelineLayout(rt->device, &plci, NULL, &pipeline_layout);
        if (rc != VK_SUCCESS) goto cleanup;
        VkShaderModuleCreateInfo smci = {
            .sType = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO,
            .codeSize = shader_size,
            .pCode = shader_code,
        };
        fail_stage = "create-generic-shader-module";
        rc = vkCreateShaderModule(rt->device, &smci, NULL, &shader);
        if (rc != VK_SUCCESS) goto cleanup;
        VkComputePipelineCreateInfo cpci = {
            .sType = VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO,
            .flags = disable_pipeline_optimization ? VK_PIPELINE_CREATE_DISABLE_OPTIMIZATION_BIT : 0,
            .stage = {
                .sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO,
                .stage = VK_SHADER_STAGE_COMPUTE_BIT,
                .module = shader,
                .pName = entry_name,
                .pSpecializationInfo = vk_spec_ptr,
            },
            .layout = pipeline_layout,
        };
        fail_stage = "create-generic-compute-pipeline";
        rc = vkCreateComputePipelines(rt->device, VK_NULL_HANDLE, 1, &cpci, NULL, &pipeline);
        if (rc != VK_SUCCESS) goto cleanup;
        pipeline_cache_entry = select_pipeline_cache_slot(rt->device);
        pipeline_cache_entry->valid = 1;
        pipeline_cache_entry->shader_hash = spirv_summary.hash;
        pipeline_cache_entry->spec_hash = spec_hash;
        pipeline_cache_entry->policy_hash = pipeline_policy_hash;
        pipeline_cache_entry->shader_size = shader_size;
        pipeline_cache_entry->specialization_data_size = specialization_data_size;
        pipeline_cache_entry->specialization_count = specialization_count;
        pipeline_cache_entry->layout_count = layout_count;
        pipeline_cache_entry->push_size = (uint32_t)push_size;
        snprintf(pipeline_cache_entry->entry_name, sizeof(pipeline_cache_entry->entry_name), "%s", entry_name);
        pipeline_cache_entry->hits = 1;
        pipeline_cache_entry->set_layout = set_layout;
        pipeline_cache_entry->pipeline_layout = pipeline_layout;
        pipeline_cache_entry->shader = shader;
        pipeline_cache_entry->pipeline = pipeline;
    }
    VkDescriptorPoolSize pool_size = {.type = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, .descriptorCount = layout_count};
    VkDescriptorPoolCreateInfo dpci = {.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO, .maxSets = 1, .poolSizeCount = 1, .pPoolSizes = &pool_size};
    fail_stage = "create-generic-descriptor-pool";
    rc = vkCreateDescriptorPool(rt->device, &dpci, NULL, &descriptor_pool);
    if (rc != VK_SUCCESS) goto cleanup;
    VkDescriptorSet descriptor_set = VK_NULL_HANDLE;
    VkDescriptorSetAllocateInfo dsai = {.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO, .descriptorPool = descriptor_pool, .descriptorSetCount = 1, .pSetLayouts = &set_layout};
    fail_stage = "allocate-generic-descriptor-set";
    rc = vkAllocateDescriptorSets(rt->device, &dsai, &descriptor_set);
    if (rc != VK_SUCCESS) goto cleanup;
    VkDescriptorBufferInfo infos[PDOCKER_GPU_MAX_VULKAN_BINDINGS];
    VkWriteDescriptorSet writes[PDOCKER_GPU_MAX_VULKAN_BINDINGS];
    uint32_t descriptor_write_dst_bindings[PDOCKER_GPU_MAX_VULKAN_BINDINGS];
    uint32_t descriptor_write_source_bindings[PDOCKER_GPU_MAX_VULKAN_BINDINGS];
    size_t descriptor_write_source_indices[PDOCKER_GPU_MAX_VULKAN_BINDINGS];
    size_t descriptor_write_alias_reps[PDOCKER_GPU_MAX_VULKAN_BINDINGS];
    VkDeviceSize descriptor_write_offsets[PDOCKER_GPU_MAX_VULKAN_BINDINGS];
    VkDeviceSize descriptor_write_ranges[PDOCKER_GPU_MAX_VULKAN_BINDINGS];
    uint8_t descriptor_write_alias_flags[PDOCKER_GPU_MAX_VULKAN_BINDINGS];
    memset(writes, 0, sizeof(writes));
    memset(descriptor_write_dst_bindings, 0, sizeof(descriptor_write_dst_bindings));
    memset(descriptor_write_source_bindings, 0, sizeof(descriptor_write_source_bindings));
    memset(descriptor_write_source_indices, 0, sizeof(descriptor_write_source_indices));
    memset(descriptor_write_alias_reps, 0, sizeof(descriptor_write_alias_reps));
    memset(descriptor_write_offsets, 0, sizeof(descriptor_write_offsets));
    memset(descriptor_write_ranges, 0, sizeof(descriptor_write_ranges));
    memset(descriptor_write_alias_flags, 0, sizeof(descriptor_write_alias_flags));
    size_t write_count = 0;
    for (size_t i = 0; i < binding_count; ++i) {
        if (!active_bindings[i]) continue;
        infos[write_count].buffer = vk_buffers[i]->buffer;
        infos[write_count].offset = (VkDeviceSize)binding_gpu_offset[i];
        infos[write_count].range = (VkDeviceSize)bindings[i].size;
        writes[write_count].sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;
        writes[write_count].dstSet = descriptor_set;
        writes[write_count].dstBinding = bindings[i].binding;
        writes[write_count].descriptorCount = 1;
        writes[write_count].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
        writes[write_count].pBufferInfo = &infos[write_count];
        descriptor_write_dst_bindings[write_count] = bindings[i].binding;
        descriptor_write_source_indices[write_count] = i;
        descriptor_write_source_bindings[write_count] = bindings[i].binding;
        descriptor_write_alias_reps[write_count] = binding_alias_rep[i];
        descriptor_write_offsets[write_count] = infos[write_count].offset;
        descriptor_write_ranges[write_count] = infos[write_count].range;
        descriptor_write_alias_flags[write_count] = 0;
        ++write_count;
    }
    for (size_t i = 0; i < binding_alias_count; ++i) {
        if (binding_index_for_number(
                bindings,
                binding_count,
                binding_aliases[i].rewritten_binding) >= 0) {
            continue;
        }
        if (write_count >= PDOCKER_GPU_MAX_VULKAN_BINDINGS) {
            json_fail("vulkan-dispatch", "too many descriptor writes");
            ret = 64;
            goto cleanup;
        }
        int original_index = binding_index_for_number(
            bindings,
            binding_count,
            binding_aliases[i].original_binding);
        if (original_index < 0) {
            json_fail("vulkan-dispatch", "descriptor alias source missing");
            ret = 64;
            goto cleanup;
        }
        if (materialize_descriptor_aliases &&
            bindings[original_index].size > 0 &&
            vk_buffers[original_index] &&
            vk_buffers[original_index]->map &&
            binding_gpu_offset[original_index] + bindings[original_index].size <=
                vk_buffers[original_index]->size &&
            create_vulkan_vector_buffer(
                rt->physical_device,
                rt->device,
                bindings[original_index].size,
                (const unsigned char *)vk_buffers[original_index]->map + binding_gpu_offset[original_index],
                &alias_temp_buffers[i]) == 0) {
            infos[write_count].buffer = alias_temp_buffers[i].buffer;
            infos[write_count].offset = 0;
        } else {
            infos[write_count].buffer = vk_buffers[original_index]->buffer;
            infos[write_count].offset = (VkDeviceSize)binding_gpu_offset[original_index];
        }
        infos[write_count].range = (VkDeviceSize)bindings[original_index].size;
        writes[write_count].sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;
        writes[write_count].dstSet = descriptor_set;
        writes[write_count].dstBinding = binding_aliases[i].rewritten_binding;
        writes[write_count].descriptorCount = 1;
        writes[write_count].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
        writes[write_count].pBufferInfo = &infos[write_count];
        descriptor_write_dst_bindings[write_count] = binding_aliases[i].rewritten_binding;
        descriptor_write_source_indices[write_count] = (size_t)original_index;
        descriptor_write_source_bindings[write_count] = bindings[original_index].binding;
        descriptor_write_alias_reps[write_count] = binding_alias_rep[original_index];
        descriptor_write_offsets[write_count] = infos[write_count].offset;
        descriptor_write_ranges[write_count] = infos[write_count].range;
        descriptor_write_alias_flags[write_count] = 1;
        ++write_count;
    }
    vkUpdateDescriptorSets(rt->device, (uint32_t)write_count, writes, 0, NULL);
    VkCommandBufferAllocateInfo cbai = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO, .commandPool = rt->command_pool, .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY, .commandBufferCount = 1};
    fail_stage = "allocate-generic-command-buffer";
    rc = vkAllocateCommandBuffers(rt->device, &cbai, &command_buffer);
    if (rc != VK_SUCCESS) goto cleanup;
    double dispatch_start = now_ms();
    VkCommandBufferBeginInfo cbi = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
    rc = vkBeginCommandBuffer(command_buffer, &cbi);
    if (rc != VK_SUCCESS) { fail_stage = "begin-generic-command-buffer"; goto cleanup; }
    vkCmdBindPipeline(command_buffer, VK_PIPELINE_BIND_POINT_COMPUTE, pipeline);
    vkCmdBindDescriptorSets(command_buffer, VK_PIPELINE_BIND_POINT_COMPUTE, pipeline_layout, 0, 1, &descriptor_set, 0, NULL);
    VkBufferMemoryBarrier pre_barriers[PDOCKER_GPU_MAX_VULKAN_BINDINGS];
    uint32_t pre_barrier_count = 0;
    for (size_t i = 0; i < binding_count && pre_barrier_count < PDOCKER_GPU_MAX_VULKAN_BINDINGS; ++i) {
        if (!active_bindings[i] || !vk_buffers[i] || !vk_buffers[i]->buffer) continue;
        pre_barriers[pre_barrier_count++] = (VkBufferMemoryBarrier){
            .sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER,
            .srcAccessMask = VK_ACCESS_HOST_WRITE_BIT,
            .dstAccessMask = VK_ACCESS_SHADER_READ_BIT | VK_ACCESS_SHADER_WRITE_BIT,
            .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .buffer = vk_buffers[i]->buffer,
            .offset = (VkDeviceSize)binding_gpu_offset[i],
            .size = (VkDeviceSize)bindings[i].size,
        };
    }
    if (pre_barrier_count) {
        vkCmdPipelineBarrier(command_buffer,
                             VK_PIPELINE_STAGE_HOST_BIT,
                             VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
                             0,
                             0, NULL,
                             pre_barrier_count, pre_barriers,
                             0, NULL);
    }
    if (push_size) vkCmdPushConstants(command_buffer, pipeline_layout, VK_SHADER_STAGE_COMPUTE_BIT, 0, (uint32_t)push_size, push);
    vkCmdDispatch(command_buffer, gx ? gx : 1, gy ? gy : 1, gz ? gz : 1);
    VkBufferMemoryBarrier post_barriers[PDOCKER_GPU_MAX_VULKAN_BINDINGS];
    uint32_t post_barrier_count = 0;
    for (size_t i = 0; i < binding_count && post_barrier_count < PDOCKER_GPU_MAX_VULKAN_BINDINGS; ++i) {
        if (!active_bindings[i] || !binding_write_needed[i] || !vk_buffers[i] || !vk_buffers[i]->buffer) continue;
        post_barriers[post_barrier_count++] = (VkBufferMemoryBarrier){
            .sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER,
            .srcAccessMask = VK_ACCESS_SHADER_WRITE_BIT,
            .dstAccessMask = VK_ACCESS_HOST_READ_BIT | VK_ACCESS_HOST_WRITE_BIT,
            .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .buffer = vk_buffers[i]->buffer,
            .offset = (VkDeviceSize)binding_gpu_offset[i],
            .size = (VkDeviceSize)bindings[i].size,
        };
    }
    if (post_barrier_count) {
        vkCmdPipelineBarrier(command_buffer,
                             VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
                             VK_PIPELINE_STAGE_HOST_BIT,
                             0,
                             0, NULL,
                             post_barrier_count, post_barriers,
                             0, NULL);
    }
    rc = vkEndCommandBuffer(command_buffer);
    if (rc != VK_SUCCESS) { fail_stage = "end-generic-command-buffer"; goto cleanup; }
    VkFenceCreateInfo fci = {.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
    rc = vkCreateFence(rt->device, &fci, NULL, &fence);
    if (rc != VK_SUCCESS) { fail_stage = "create-generic-fence"; goto cleanup; }
    VkSubmitInfo submit = {.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO, .commandBufferCount = 1, .pCommandBuffers = &command_buffer};
    fail_stage = "submit-generic-dispatch";
    rc = vkQueueSubmit(rt->queue, 1, &submit, fence);
    if (rc != VK_SUCCESS) goto cleanup;
    fail_stage = "wait-generic-fence";
    rc = vkWaitForFences(rt->device, 1, &fence, VK_TRUE, UINT64_MAX);
    if (rc != VK_SUCCESS) goto cleanup;
    double dispatch_ms = now_ms() - dispatch_start;
    if (profile_response) {
        for (size_t i = 0; i < binding_count; ++i) {
            if (!active_bindings[i] || !vk_buffers[i] || !vk_buffers[i]->map) continue;
            binding_gpu_after_dispatch_hash[i] =
                sample_memory_hash((const unsigned char *)vk_buffers[i]->map + binding_gpu_offset[i],
                                   bindings[i].size);
        }
    }
    if (spirv_summary.hash == 0xac41e8033a67af4aull) {
        run_cpu_oracle_rope_yarn(&cpu_oracle_report,
                                 buffer_fds,
                                 bindings,
                                 binding_count,
                                 vk_buffers,
                                 binding_gpu_offset,
                                 binding_alias_rep,
                                 active_bindings,
                                 binding_write_needed,
                                 push,
                                 push_size,
                                 gx,
                                 gy,
                                 gz);
    } else if (spirv_summary.hash == 0xf2f988b94bd3e0dcull) {
        run_cpu_oracle_rms_norm(&cpu_oracle_report,
                                buffer_fds,
                                bindings,
                                binding_count,
                                vk_buffers,
                                binding_gpu_offset,
                                binding_alias_rep,
                                active_bindings,
                                binding_write_needed,
                                specializations,
                                specialization_count,
                                specialization_data,
                                specialization_data_size,
                                push,
                                push_size,
                                gx,
                                gy,
                                gz);
    } else if (spirv_summary.hash == 0x274f68a67dfef210ull ||
               spirv_summary.hash == 0x1bf751845c5dce75ull ||
               spirv_summary.hash == 0x09c4622d92c6acb9ull) {
        run_cpu_oracle_q6k_matvec_sample(&cpu_oracle_report,
                                         spirv_summary.hash,
                                         buffer_fds,
                                         bindings,
                                         binding_count,
                                         vk_buffers,
                                         binding_gpu_offset,
                                         active_bindings,
                                         binding_write_needed,
                                         push,
                                         push_size);
    } else {
        run_cpu_oracle_small_f32_indexing(&cpu_oracle_report,
                                          spirv_summary.hash,
                                          buffer_fds,
                                          bindings,
                                          binding_count,
                                          vk_buffers,
                                          binding_gpu_offset,
                                          binding_alias_rep,
                                          active_bindings,
                                          binding_write_needed,
                                          push,
                                          push_size,
                                          gx,
                                          gy,
                                          gz);
    }
    double download_start = now_ms();
    for (size_t i = 0; i < binding_count; ++i) {
        if (!active_bindings[i]) continue;
        if (!binding_write_needed[i]) continue;
        if (cache_resident[i]) continue;
        fail_stage = "download-dispatch-buffer";
        fail_binding = (int)i;
        double binding_start = now_ms();
        const int dirty_candidate =
            binding_gpu_offset[i] == 0 &&
            binding_alias_rep[i] == i &&
            !binding_read_needed[i] &&
            bindings[i].size >= dirty_probe_min_bytes &&
            vk_buffers[i]->map;
        if (dirty_writeback_enabled && dirty_candidate) {
            size_t page_count = (bindings[i].size + dirty_probe_pagesize - 1) / dirty_probe_pagesize;
            VulkanDirtyMaskCacheEntry *cached = find_dirty_mask_cache_entry(
                spirv_summary.hash,
                spec_hash,
                bindings[i].binding,
                bindings[i].size,
                dirty_probe_pagesize,
                page_count);
            if (cached && cached->dirty_pages) {
                binding_dirty_writeback_cached[i] = 1;
                binding_dirty_probe_pages[i] = cached->dirty_page_count;
                binding_dirty_probe_bytes[i] = cached->dirty_bytes;
                io_rc = write_dirty_pages_exact(
                    buffer_fds[i],
                    vk_buffers[i]->map,
                    bindings[i].size,
                    bindings[i].offset,
                    dirty_probe_pagesize,
                    cached->dirty_pages,
                    cached->page_count,
                    &binding_dirty_writeback_bytes[i]);
                binding_download_ms[i] = now_ms() - binding_start;
                if (io_rc != 0) goto cleanup;
                continue;
            }
        }
        if ((dirty_probe_enabled || dirty_writeback_enabled) && dirty_candidate) {
            const size_t page_count = (bindings[i].size + dirty_probe_pagesize - 1) / dirty_probe_pagesize;
            unsigned char *dirty_pages = NULL;
            if (dirty_writeback_enabled) {
                dirty_pages = (unsigned char *)calloc(page_count, 1);
            }
            double probe_start = now_ms();
            binding_dirty_probe_pages[i] = count_dirty_probe_pages(
                vk_buffers[i]->map,
                bindings[i].size,
                dirty_probe_pagesize,
                PDOCKER_GPU_WRITEONLY_DIRTY_PROBE_SENTINEL,
                dirty_pages,
                page_count,
                &binding_dirty_probe_bytes[i]);
            binding_dirty_probe_ms[i] += now_ms() - probe_start;
            binding_dirty_probe_masks[i] = dirty_pages;
            if (dirty_writeback_enabled &&
                dirty_pages &&
                binding_dirty_probe_pages[i] > 0) {
                update_dirty_mask_cache(
                    spirv_summary.hash,
                    spec_hash,
                    bindings[i].binding,
                    bindings[i].size,
                    dirty_probe_pagesize,
                    dirty_pages,
                    page_count,
                    binding_dirty_probe_pages[i],
                    binding_dirty_probe_bytes[i]);
                io_rc = write_dirty_pages_exact(
                    buffer_fds[i],
                    vk_buffers[i]->map,
                    bindings[i].size,
                    bindings[i].offset,
                    dirty_probe_pagesize,
                    dirty_pages,
                    page_count,
                    &binding_dirty_writeback_bytes[i]);
                binding_download_ms[i] = now_ms() - binding_start;
                if (io_rc != 0) goto cleanup;
                continue;
            }
        }
        io_rc = write_fd_exact(buffer_fds[i],
                               (const unsigned char *)vk_buffers[i]->map + binding_gpu_offset[i],
                               bindings[i].size,
                               bindings[i].offset);
        binding_dirty_writeback_bytes[i] = bindings[i].size;
        binding_download_ms[i] = now_ms() - binding_start;
        if (io_rc != 0) goto cleanup;
    }
    if (profile_response) {
        for (size_t i = 0; i < binding_count; ++i) {
            if (!active_bindings[i]) continue;
            binding_fd_after_hash[i] = sample_fd_hash(
                buffer_fds[i], bindings[i].offset, bindings[i].size);
        }
    }
    fail_binding = -1;
    double download_ms = now_ms() - download_start;
    size_t resident_count = 0;
    size_t hit_count = 0;
    size_t resident_bytes = 0;
    size_t mutable_count = 0;
    size_t mutable_hit_count = 0;
    size_t mutable_bytes = 0;
    for (size_t i = 0; i < binding_count; ++i) {
        if (!active_bindings[i]) continue;
        if (cache_resident[i]) {
            resident_count++;
            resident_bytes += bindings[i].size;
        }
        if (cache_hits[i]) hit_count++;
        if (mutable_cache_reused[i]) {
            mutable_count++;
            mutable_bytes += bindings[i].size;
        }
        if (mutable_cache_hits[i]) mutable_hit_count++;
    }
    VulkanRuntime effective_rt = effective_vulkan_runtime_for_dispatch(rt, options);
    if (profile_response) {
        fprintf(json_out(),
                "{\"executor\":\"pdocker-gpu-executor\",\"api\":\"%s\",\"abi_version\":\"%s\","
                "\"backend_impl\":\"android_vulkan\",\"kernel\":\"generic_spirv\","
                "\"compact_summary\":true,"
                "\"shader_bytes\":%zu,\"entry\":\"%s\",\"specializations\":%zu,"
                "\"bindings\":%zu,\"dispatch\":[%u,%u,%u],"
                "\"skip_unused_descriptor_transfers\":%s,"
                "\"spirv_descriptor_access\":%s,"
                "\"disable_overlap_aliasing\":%s,"
                "\"cpu_oracle_requested\":%s,"
                "\"descriptor_aliases\":%zu,\"duplicate_descriptor_rewrite\":%s,"
                "\"materialize_descriptor_aliases\":%s,"
                "\"materialize_specialization\":%s,"
                "\"disable_pipeline_optimization\":%s,"
                "\"specialization_materialized\":%s,"
                "\"local_size_patched\":%s,"
                "\"pipeline_policy_hash\":\"0x%016llx\","
                "\"resident_bytes\":%zu,\"mutable_bytes\":%zu,"
                "\"pre_barriers\":%u,\"post_barriers\":%u,"
                "\"valid\":true,",
                PDOCKER_GPU_COMMAND_API, PDOCKER_GPU_ABI_VERSION,
                shader_size, entry_name, specialization_count, binding_count, gx, gy, gz,
                skip_unused_descriptor_transfers ? "true" : "false",
                use_spirv_descriptor_access ? "true" : "false",
                disable_overlap_aliasing ? "true" : "false",
                cpu_oracle_requested ? "true" : "false",
                binding_alias_count,
                rewrite_duplicate_descriptors ? "true" : "false",
                materialize_descriptor_aliases ? "true" : "false",
                materialize_specialization_constants ? "true" : "false",
                disable_pipeline_optimization ? "true" : "false",
                specialization_materialized ? "true" : "false",
                local_size_patched ? "true" : "false",
                (unsigned long long)pipeline_policy_hash,
                resident_bytes,
                mutable_bytes,
                pre_barrier_count,
                post_barrier_count);
        write_vulkan_binding_compact_report(json_out(), bindings, binding_count,
                                            vk_buffers,
                                            binding_gpu_offset,
                                            active_bindings,
                                            binding_read_needed, binding_write_needed,
                                            cache_resident, cache_hits,
                                            binding_fd_before_hash,
                                            binding_gpu_after_upload_hash,
                                            binding_gpu_after_dispatch_hash,
                                            binding_fd_after_hash,
                                            binding_alias_rep);
        fprintf(json_out(), ",");
        write_spirv_feature_report(json_out(), &spirv_summary, &effective_rt);
        fprintf(json_out(), ",");
        write_spirv_execution_report(json_out(),
                                     &spirv_summary,
                                     specializations,
                                     specialization_count,
                                     specialization_data,
                                     specialization_data_size,
                                     push,
                                     push_size);
        fprintf(json_out(), ",");
        write_vulkan_descriptor_write_report(json_out(),
                                             descriptor_write_dst_bindings,
                                             descriptor_write_source_indices,
                                             descriptor_write_source_bindings,
                                             descriptor_write_alias_reps,
                                             descriptor_write_offsets,
                                             descriptor_write_ranges,
                                             descriptor_write_alias_flags,
                                             write_count);
        fprintf(json_out(), ",");
        write_vulkan_descriptor_alias_report(json_out(),
                                             binding_aliases,
                                             binding_alias_count);
        fprintf(json_out(), ",");
        write_vulkan_rw_alias_hazard_report(json_out(), bindings, binding_count,
                                            active_bindings,
                                            binding_read_needed,
                                            binding_write_needed,
                                            binding_alias_rep);
        fprintf(json_out(), ",");
        write_spirv_binding_reflection_report(json_out(),
                                              shader_used_bindings,
                                              shader_binding_access,
                                              bindings,
                                              binding_count);
        fprintf(json_out(), ",");
        write_cpu_oracle_report(json_out(), &cpu_oracle_report);
        fprintf(json_out(), "}\n");
    }
    fprintf(json_out(),
            "{\"executor\":\"pdocker-gpu-executor\",\"api\":\"%s\",\"abi_version\":\"%s\","
            "\"backend_impl\":\"android_vulkan\",\"kernel\":\"generic_spirv\","
            "\"shader_bytes\":%zu,\"entry\":\"%s\",\"specializations\":%zu,"
            "\"bindings\":%zu,\"dispatch\":[%u,%u,%u],"
            "\"backend_cached\":%s,\"pipeline_cache\":{\"hit\":%s,\"entries\":%u},"
            "\"skip_unused_descriptor_transfers\":%s,"
            "\"spirv_descriptor_access\":%s,"
            "\"disable_overlap_aliasing\":%s,"
            "\"cpu_oracle_requested\":%s,"
            "\"descriptor_aliases\":%zu,\"duplicate_descriptor_rewrite\":%s,"
            "\"materialize_descriptor_aliases\":%s,"
            "\"materialize_specialization\":%s,"
            "\"disable_pipeline_optimization\":%s,"
            "\"specialization_materialized\":%s,"
            "\"local_size_patched\":%s,"
            "\"pipeline_policy_hash\":\"0x%016llx\","
            "\"profile_response\":%s,"
            "\"pre_barriers\":%u,\"post_barriers\":%u,"
            "\"upload_ms\":%.4f,\"dispatch_ms\":%.4f,\"download_ms\":%.4f,"
            "\"resident_cache\":{\"enabled\":%s,\"resident_bindings\":%zu,"
            "\"hits\":%zu,\"bytes\":%zu},"
            "\"mutable_buffer_cache\":{\"enabled\":%s,\"entries\":%u,"
            "\"reused_bindings\":%zu,\"hits\":%zu,\"bytes\":%zu},"
            "\"descriptor_usage\":{\"active_bindings\":%zu,"
            "\"read_bindings\":%zu,\"write_bindings\":%zu,"
            "\"skipped_bindings\":%zu,\"skipped_bytes\":%zu,"
            "\"skipped_upload_bytes\":%zu,\"skipped_download_bytes\":%zu},"
            "\"valid\":true",
            PDOCKER_GPU_COMMAND_API, PDOCKER_GPU_ABI_VERSION,
            shader_size, entry_name, specialization_count, binding_count, gx, gy, gz,
            was_ready ? "true" : "false",
            pipeline_cache_hit ? "true" : "false",
            PDOCKER_GPU_PIPELINE_CACHE_SLOTS,
            skip_unused_descriptor_transfers ? "true" : "false",
            use_spirv_descriptor_access ? "true" : "false",
            disable_overlap_aliasing ? "true" : "false",
            cpu_oracle_requested ? "true" : "false",
            binding_alias_count,
            rewrite_duplicate_descriptors ? "true" : "false",
            materialize_descriptor_aliases ? "true" : "false",
            materialize_specialization_constants ? "true" : "false",
            disable_pipeline_optimization ? "true" : "false",
            specialization_materialized ? "true" : "false",
            local_size_patched ? "true" : "false",
            (unsigned long long)pipeline_policy_hash,
            profile_response ? "true" : "false",
            pre_barrier_count,
            post_barrier_count,
            upload_ms, dispatch_ms, download_ms,
            env_truthy("PDOCKER_GPU_RESIDENT_CACHE", 1) ? "true" : "false",
            resident_count, hit_count, resident_bytes,
            env_truthy("PDOCKER_GPU_MUTABLE_BUFFER_CACHE", 1) ? "true" : "false",
            PDOCKER_GPU_MUTABLE_BUFFER_CACHE_SLOTS,
            mutable_count, mutable_hit_count, mutable_bytes,
            active_binding_count, read_binding_count, write_binding_count,
            skipped_binding_count, skipped_binding_bytes,
            skipped_upload_bytes, skipped_download_bytes);
    if (profile_response) {
        fprintf(json_out(), ",");
        write_spirv_feature_report(json_out(), &spirv_summary, &effective_rt);
        fprintf(json_out(), ",");
        write_spirv_execution_report(json_out(),
                                     &spirv_summary,
                                     specializations,
                                     specialization_count,
                                     specialization_data,
                                     specialization_data_size,
                                     push,
                                     push_size);
        fprintf(json_out(), ",");
        write_vulkan_binding_report(json_out(), bindings, binding_count,
                                    active_bindings,
                                    binding_read_needed, binding_write_needed,
                                    cache_hits, cache_resident,
                                    mutable_cache_hits, mutable_cache_reused,
                                    binding_upload_ms, binding_download_ms,
                                    binding_dirty_probe_pages,
                                    binding_dirty_probe_bytes,
                                    binding_dirty_probe_ms,
                                    binding_dirty_writeback_cached,
                                    binding_dirty_writeback_bytes,
                                    binding_fd_before_hash,
                                    binding_gpu_after_upload_hash,
                                    binding_gpu_after_dispatch_hash,
                                    binding_fd_after_hash,
                                    binding_alias_rep);
        fprintf(json_out(), ",");
        write_vulkan_descriptor_write_report(json_out(),
                                             descriptor_write_dst_bindings,
                                             descriptor_write_source_indices,
                                             descriptor_write_source_bindings,
                                             descriptor_write_alias_reps,
                                             descriptor_write_offsets,
                                             descriptor_write_ranges,
                                             descriptor_write_alias_flags,
                                             write_count);
        fprintf(json_out(), ",");
        write_vulkan_descriptor_alias_report(json_out(),
                                             binding_aliases,
                                             binding_alias_count);
        fprintf(json_out(), ",");
        write_vulkan_rw_alias_hazard_report(json_out(), bindings, binding_count,
                                            active_bindings,
                                            binding_read_needed,
                                            binding_write_needed,
                                            binding_alias_rep);
        fprintf(json_out(), ",");
        write_spirv_binding_reflection_report(json_out(),
                                              shader_used_bindings,
                                              shader_binding_access,
                                              bindings,
                                              binding_count);
        fprintf(json_out(), ",");
        write_cpu_oracle_report(json_out(), &cpu_oracle_report);
    }
    fprintf(json_out(), "}\n");
    fflush(json_out());
    ret = 0;

cleanup:
    if (ret != 0) {
        fprintf(stderr, "pdocker-gpu-executor: generic Vulkan dispatch failed stage=%s rc=%d\n", fail_stage, rc);
        log_vulkan_feature_trace(rt);
        if (have_spirv_summary) {
            log_spirv_trace(&spirv_summary, bindings, binding_count, push_size, gx, gy, gz);
        }
        uint64_t resolved_spec0 = 0;
        uint64_t resolved_spec1 = 0;
        uint64_t resolved_spec2 = 0;
        (void)specialization_value_for_id(specializations, specialization_count,
                                          specialization_data, specialization_data_size,
                                          0, &resolved_spec0);
        (void)specialization_value_for_id(specializations, specialization_count,
                                          specialization_data, specialization_data_size,
                                          1, &resolved_spec1);
        (void)specialization_value_for_id(specializations, specialization_count,
                                          specialization_data, specialization_data_size,
                                          2, &resolved_spec2);
        const uint64_t estimated_workgroup_bytes =
            resolved_spec0 * (resolved_spec1 ? resolved_spec1 : 1) *
            (resolved_spec2 ? resolved_spec2 : 1) * 4ull;
        fprintf(json_out(),
                "{\"executor\":\"pdocker-gpu-executor\",\"api\":\"%s\",\"abi_version\":\"%s\","
                "\"role\":\"%s\",\"llm_engine\":\"%s\",\"device_independent\":true,"
                "\"backend_impl\":\"android_vulkan\",\"kernel\":\"generic_spirv\","
                "\"valid\":false,\"stage\":\"vulkan-dispatch\",\"error\":\"%s\","
                "\"vk_result\":%d,\"shader_bytes\":%zu,\"entry\":\"%s\","
                "\"specializations\":%zu,\"bindings\":%zu,"
                "\"layout_bindings\":%u,"
                "\"fail_binding_index\":%d,\"io_result\":%d,"
                "\"dispatch\":[%u,%u,%u],\"push_bytes\":%zu,"
                "\"estimated_workgroup_bytes\":%llu,"
                "\"duplicate_descriptor_rewrite\":%s,"
                "\"materialize_specialization\":%s,"
                "\"specialization_materialized\":%s,"
                "\"spirv_hash\":\"0x%016llx\","
                "\"spirv_valid\":%s,\"spirv_truncated\":%u,"
                "\"spirv_local_size\":[%u,%u,%u],"
                "\"spirv_local_size_id\":[%u,%u,%u],"
                "\"spirv_local_size_resolved\":[",
                PDOCKER_GPU_COMMAND_API, PDOCKER_GPU_ABI_VERSION,
                PDOCKER_GPU_EXECUTOR_ROLE, PDOCKER_GPU_LLM_ENGINE_LOCATION,
                fail_stage, rc, shader_size, entry_name, specialization_count,
                binding_count, layout_count,
                fail_binding, io_rc,
                gx, gy, gz, push_size,
                (unsigned long long)estimated_workgroup_bytes,
                rewrite_duplicate_descriptors ? "true" : "false",
                materialize_specialization_constants ? "true" : "false",
                specialization_materialized ? "true" : "false",
                (unsigned long long)spirv_summary.hash,
                spirv_summary.valid ? "true" : "false",
                spirv_summary.truncated,
                spirv_summary.local_size[0],
                spirv_summary.local_size[1],
                spirv_summary.local_size[2],
                spirv_summary.local_size_id[0],
                spirv_summary.local_size_id[1],
                spirv_summary.local_size_id[2]);
        for (uint32_t i = 0; i < 3; ++i) {
            uint64_t value = spirv_summary.local_size[i];
            if (spirv_summary.local_size_id[i]) {
                uint64_t spec_value = 0;
                if (specialization_value_for_id(specializations, specialization_count,
                                                specialization_data, specialization_data_size,
                                                spirv_summary.local_size_id[i], &spec_value)) {
                    value = spec_value;
                }
            }
            fprintf(json_out(), "%s%llu", i ? "," : "", (unsigned long long)value);
        }
        fprintf(json_out(), "],\"specialization_entries\":[");
        for (size_t i = 0; i < specialization_count; ++i) {
            uint64_t value = specialization_value_u64(
                specialization_data,
                specialization_data_size,
                &specializations[i]);
            fprintf(json_out(),
                    "%s{\"constant_id\":%u,\"offset\":%u,\"size\":%zu,\"value_u64\":%llu}",
                    i ? "," : "",
                    specializations[i].constant_id,
                    specializations[i].offset,
                    specializations[i].size,
                    (unsigned long long)value);
        }
        fprintf(json_out(), "],\"descriptor_aliases\":[");
        for (size_t i = 0; i < binding_alias_count; ++i) {
            fprintf(json_out(),
                    "%s{\"from\":%u,\"to\":%u}",
                    i ? "," : "",
                    binding_aliases[i].original_binding,
                    binding_aliases[i].rewritten_binding);
        }
        fprintf(json_out(), "],\"spirv_capabilities\":[");
        for (uint32_t i = 0; i < spirv_summary.capability_count; ++i) {
            fprintf(json_out(), "%s%u", i ? "," : "", spirv_summary.capabilities[i]);
        }
        fprintf(json_out(), "],");
        write_vulkan_binding_report(json_out(), bindings, binding_count,
                                    active_bindings,
                                    binding_read_needed, binding_write_needed,
                                    cache_hits, cache_resident,
                                    mutable_cache_hits, mutable_cache_reused,
                                    binding_upload_ms, binding_download_ms,
                                    binding_dirty_probe_pages,
                                    binding_dirty_probe_bytes,
                                    binding_dirty_probe_ms,
                                    binding_dirty_writeback_cached,
                                    binding_dirty_writeback_bytes,
                                    binding_fd_before_hash,
                                    binding_gpu_after_upload_hash,
                                    binding_gpu_after_dispatch_hash,
                                    binding_fd_after_hash,
                                    binding_alias_rep);
        fprintf(json_out(), ",");
        write_spirv_feature_report(json_out(), &spirv_summary, &effective_rt);
        fprintf(json_out(), ",");
        write_vulkan_limits_report(json_out(), rt);
        fprintf(json_out(),
                ",\"android_vulkan_features\":{"
                "\"shaderInt64\":%u,"
                "\"storageBuffer16BitAccess\":%u,"
                "\"uniformAndStorageBuffer16BitAccess\":%u,"
                "\"storagePushConstant16\":%u,"
                "\"storageBuffer8BitAccess\":%u,"
                "\"uniformAndStorageBuffer8BitAccess\":%u,"
                "\"storagePushConstant8\":%u,"
                "\"shaderFloat16\":%u,"
                "\"shaderInt8\":%u}}\n",
                rt ? rt->physical_features.shaderInt64 : 0,
                rt ? rt->physical_storage16.storageBuffer16BitAccess : 0,
                rt ? rt->physical_storage16.uniformAndStorageBuffer16BitAccess : 0,
                rt ? rt->physical_storage16.storagePushConstant16 : 0,
                rt ? rt->physical_storage8.storageBuffer8BitAccess : 0,
                rt ? rt->physical_storage8.uniformAndStorageBuffer8BitAccess : 0,
                rt ? rt->physical_storage8.storagePushConstant8 : 0,
                rt ? rt->physical_float16_int8.shaderFloat16 : 0,
                rt ? rt->physical_float16_int8.shaderInt8 : 0);
        fflush(json_out());
    }
    if (fence) vkDestroyFence(rt->device, fence, NULL);
    if (command_buffer) vkFreeCommandBuffers(rt->device, rt->command_pool, 1, &command_buffer);
    if (descriptor_pool) vkDestroyDescriptorPool(rt->device, descriptor_pool, NULL);
    if (!pipeline_cache_entry || !pipeline_cache_entry->valid ||
        pipeline_cache_entry->pipeline != pipeline ||
        pipeline_cache_entry->shader != shader ||
        pipeline_cache_entry->pipeline_layout != pipeline_layout ||
        pipeline_cache_entry->set_layout != set_layout) {
        if (pipeline) vkDestroyPipeline(rt->device, pipeline, NULL);
        if (shader) vkDestroyShaderModule(rt->device, shader, NULL);
        if (pipeline_layout) vkDestroyPipelineLayout(rt->device, pipeline_layout, NULL);
        if (set_layout) vkDestroyDescriptorSetLayout(rt->device, set_layout, NULL);
    }
    for (size_t i = 0; i < binding_count; ++i) {
        free(binding_dirty_probe_masks[i]);
        destroy_vulkan_vector_buffer(rt->device, &temp_buffers[i]);
    }
    for (size_t i = 0; i < binding_alias_count; ++i) {
        destroy_vulkan_vector_buffer(rt->device, &alias_temp_buffers[i]);
    }
    free(shader_code);
    return ret;
}

typedef struct {
    void *map;
    size_t n;
    size_t total;
} RegisteredVectorBuffer;

static void clear_registered_vector_buffer(RegisteredVectorBuffer *buffer) {
    if (!buffer) return;
    if (buffer->map && buffer->map != MAP_FAILED) {
        munmap(buffer->map, buffer->total);
    }
    memset(buffer, 0, sizeof(*buffer));
}

static int register_vector_buffer(RegisteredVectorBuffer *buffer, int fd, size_t n) {
    if (!buffer || fd < 0) {
        json_fail("fd", "missing shared buffer fd");
        return 64;
    }
    if (n == 0 || n > PDOCKER_GPU_VECTOR_ADD_MAX_N) {
        close(fd);
        json_fail("fd", "invalid vector size");
        return 64;
    }
    const size_t bytes = n * sizeof(float);
    const size_t total = bytes * 3;
    void *map = mmap(NULL, total, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    close(fd);
    if (map == MAP_FAILED) {
        json_fail("mmap", strerror(errno));
        return 70;
    }
    clear_registered_vector_buffer(buffer);
    buffer->map = map;
    buffer->n = n;
    buffer->total = total;
    fprintf(json_out(),
            "{\"executor\":\"pdocker-gpu-executor\",\"api\":\"%s\",\"abi_version\":\"%s\","
            "\"role\":\"%s\",\"llm_engine\":\"%s\",\"device_independent\":true,"
            "\"transport\":\"unix-socket-registered-shared-buffer\","
            "\"kernel\":\"register_vector_buffer\",\"problem_size\":\"n=%zu\","
            "\"valid\":true}\n",
            PDOCKER_GPU_COMMAND_API, PDOCKER_GPU_ABI_VERSION,
            PDOCKER_GPU_EXECUTOR_ROLE, PDOCKER_GPU_LLM_ENGINE_LOCATION, n);
    fflush(json_out());
    return 0;
}

static int run_registered_vector_add(RegisteredVectorBuffer *buffer) {
    if (!buffer || !buffer->map || buffer->n == 0) {
        json_fail("registered-buffer", "no registered vector buffer");
        return 64;
    }
    float *a = (float *)buffer->map;
    float *b = a + buffer->n;
    float *out = b + buffer->n;
    return run_vector_add_arrays_best(a, b, out, buffer->n, "unix-socket-registered-shared-buffer", GPU_API_AUTO);
}

static void print_capabilities(const char *transport) {
    int vulkan_ready = init_vulkan_runtime(&g_vulkan_runtime) == 0;
    VulkanRuntime *rt = vulkan_ready ? &g_vulkan_runtime : NULL;
    fprintf(json_out(),
            "{\"executor\":\"pdocker-gpu-executor\",\"api\":\"%s\",\"abi_version\":\"%s\","
            "\"role\":\"%s\",\"llm_engine\":\"%s\",\"device_independent\":true,"
            "\"transport\":\"%s\","
            "\"backend_impls\":[\"android_vulkan\",\"android_opencl\",\"gles31_compute\"],"
            "\"preferred_backend\":\"android_vulkan\","
            "\"fallback_backend\":\"gles31_compute\","
            "\"backend_affinity_policy\":\"same-api-first\","
            "\"container_contract\":\"glibc-shim-command-queue\","
            "\"fd_shared_buffer\":true,"
            "\"android_vulkan_ready\":%s,"
            "\"android_vulkan_features\":{"
            "\"api_major\":%u,\"api_minor\":%u,"
            "\"shaderInt64\":%u,"
            "\"storageBuffer16BitAccess\":%u,"
            "\"uniformAndStorageBuffer16BitAccess\":%u,"
            "\"storagePushConstant16\":%u,"
            "\"storageBuffer8BitAccess\":%u,"
            "\"uniformAndStorageBuffer8BitAccess\":%u,"
            "\"storagePushConstant8\":%u,"
            "\"shaderFloat16\":%u,\"shaderInt8\":%u,"
            "\"subgroupSize\":%u,"
            "\"subgroupStages\":%u,"
            "\"subgroupOperations\":%u},"
            "\"process_exec\":true}\n",
            PDOCKER_GPU_COMMAND_API, PDOCKER_GPU_ABI_VERSION,
            PDOCKER_GPU_EXECUTOR_ROLE, PDOCKER_GPU_LLM_ENGINE_LOCATION,
            transport,
            vulkan_ready ? "true" : "false",
            rt ? VK_API_VERSION_MAJOR(rt->api_version) : 0,
            rt ? VK_API_VERSION_MINOR(rt->api_version) : 0,
            rt ? rt->physical_features.shaderInt64 : 0,
            rt ? rt->physical_storage16.storageBuffer16BitAccess : 0,
            rt ? rt->physical_storage16.uniformAndStorageBuffer16BitAccess : 0,
            rt ? rt->physical_storage16.storagePushConstant16 : 0,
            rt ? rt->physical_storage8.storageBuffer8BitAccess : 0,
            rt ? rt->physical_storage8.uniformAndStorageBuffer8BitAccess : 0,
            rt ? rt->physical_storage8.storagePushConstant8 : 0,
            rt ? rt->physical_float16_int8.shaderFloat16 : 0,
            rt ? rt->physical_float16_int8.shaderInt8 : 0,
            rt ? rt->subgroup_properties.subgroupSize : 0,
            rt ? rt->subgroup_properties.supportedStages : 0,
            rt ? rt->subgroup_properties.supportedOperations : 0);
    fflush(json_out());
}

static void print_noop(void) {
    fprintf(json_out(),
            "{\"executor\":\"pdocker-gpu-executor\",\"api\":\"%s\",\"abi_version\":\"%s\","
            "\"role\":\"%s\",\"llm_engine\":\"%s\",\"device_independent\":true,"
            "\"kernel\":\"noop\",\"total_ms\":0.0,\"valid\":true}\n",
            PDOCKER_GPU_COMMAND_API, PDOCKER_GPU_ABI_VERSION,
            PDOCKER_GPU_EXECUTOR_ROLE, PDOCKER_GPU_LLM_ENGINE_LOCATION);
    fflush(json_out());
}

static int run_gpu_once(void) {
    GpuContext ctx;
    int rc = init_gpu_context(&ctx);
    if (rc != 0) return rc;
    rc = run_vector_add();
    destroy_gpu_context(&ctx);
    return rc;
}

static int bench_vector_add(int count) {
    if (count <= 0) count = 1;
    GpuContext ctx;
    int rc = init_gpu_context(&ctx);
    if (rc != 0) return rc;
    int last = 0;
    for (int i = 0; i < count; ++i) {
        last = run_vector_add();
    }
    destroy_gpu_context(&ctx);
    return last;
}

static int run_vector_add_arrays_cpu(const float *a, const float *b, float *out, size_t n, const char *transport) {
    double dispatch_start = now_ms();
    for (size_t i = 0; i < n; ++i) {
        out[i] = a[i] + b[i];
    }
    double dispatch_ms = now_ms() - dispatch_start;
    double max_err = 0.0;
    for (size_t i = 0; i < n; ++i) {
        double e = fabs((double)out[i] - (double)(a[i] + b[i]));
        if (e > max_err) max_err = e;
    }
    const int valid = max_err <= 0.0001;
    fprintf(json_out(),
            "{\"executor\":\"pdocker-gpu-executor\",\"api\":\"%s\",\"abi_version\":\"%s\","
            "\"role\":\"%s\",\"llm_engine\":\"%s\",\"device_independent\":true,"
            "\"backend_impl\":\"cpu_scalar\",\"backend_affinity\":\"cpu\","
            "\"transport\":\"%s\",\"kernel\":\"vector_add\",\"problem_size\":\"n=%zu\","
            "\"init_ms\":0.0000,\"compile_ms\":0.0000,\"upload_ms\":0.0000,"
            "\"dispatch_ms\":%.4f,\"download_ms\":0.0000,\"total_ms\":%.4f,"
            "\"max_abs_error\":%.8f,\"valid\":%s}\n",
            PDOCKER_GPU_COMMAND_API, PDOCKER_GPU_ABI_VERSION,
            PDOCKER_GPU_EXECUTOR_ROLE, PDOCKER_GPU_LLM_ENGINE_LOCATION,
            transport ? transport : "host-local-process-buffer",
            n, dispatch_ms, dispatch_ms, max_err, valid ? "true" : "false");
    fflush(json_out());
    return valid ? 0 : 6;
}

static int bench_cpu_vector_add(int count) {
    if (count <= 0) count = 1;
    const size_t n = PDOCKER_GPU_VECTOR_ADD_DEFAULT_N;
    const size_t bytes = n * sizeof(float);
    float *a = (float *)malloc(bytes);
    float *b = (float *)malloc(bytes);
    float *out = (float *)calloc(n, sizeof(float));
    if (!a || !b || !out) {
        free(a);
        free(b);
        free(out);
        json_fail("alloc", "host allocation failed");
        return 2;
    }
    fill_inputs(a, b, n);
    int last = 0;
    for (int i = 0; i < count; ++i) {
        memset(out, 0, bytes);
        last = run_vector_add_arrays_cpu(a, b, out, n, "host-cpu-local-process-buffer");
    }
    free(a);
    free(b);
    free(out);
    return last;
}

static int bench_vulkan_vector_add(int count) {
    if (count <= 0) count = 1;
    const size_t n = PDOCKER_GPU_VECTOR_ADD_DEFAULT_N;
    const size_t bytes = n * sizeof(float);
    float *a = (float *)malloc(bytes);
    float *b = (float *)malloc(bytes);
    float *out = (float *)calloc(n, sizeof(float));
    if (!a || !b || !out) {
        free(a);
        free(b);
        free(out);
        json_fail("alloc", "host allocation failed");
        return 2;
    }
    fill_inputs(a, b, n);
    int last = 0;
    for (int i = 0; i < count; ++i) {
        memset(out, 0, bytes);
        last = run_vector_add_arrays_vulkan(a, b, out, n, "direct-vulkan-local-process-buffer");
    }
    free(a);
    free(b);
    free(out);
    return last;
}

static int bench_vulkan_vector_add_resident(int count) {
    if (count <= 0) count = 1;
    const size_t n = PDOCKER_GPU_VECTOR_ADD_DEFAULT_N;
    const size_t bytes = n * sizeof(float);
    float *a = (float *)malloc(bytes);
    float *b = (float *)malloc(bytes);
    float *out = (float *)calloc(n, sizeof(float));
    if (!a || !b || !out) {
        free(a);
        free(b);
        free(out);
        json_fail("alloc", "host allocation failed");
        return 2;
    }
    fill_inputs(a, b, n);

    const int was_ready = g_vulkan_runtime.ready;
    if (init_vulkan_runtime(&g_vulkan_runtime) != 0) {
        free(a);
        free(b);
        free(out);
        return -21;
    }
    VulkanRuntime *rt = &g_vulkan_runtime;
    VulkanVectorBuffer buffers[3];
    memset(buffers, 0, sizeof(buffers));
    VkDescriptorPool descriptor_pool = VK_NULL_HANDLE;
    VkCommandBuffer command_buffer = VK_NULL_HANDLE;
    VkFence fence = VK_NULL_HANDLE;
    VkResult rc = VK_SUCCESS;
    const char *fail_stage = "start";
    VkResult fail_result = VK_SUCCESS;

    double upload_start = now_ms();
    fail_stage = "create-buffer-a";
    if (create_vulkan_vector_buffer(rt->physical_device, rt->device, bytes, a, &buffers[0]) != 0) goto fail;
    fail_stage = "create-buffer-b";
    if (create_vulkan_vector_buffer(rt->physical_device, rt->device, bytes, b, &buffers[1]) != 0) goto fail;
    fail_stage = "create-buffer-out";
    if (create_vulkan_vector_buffer(rt->physical_device, rt->device, bytes, NULL, &buffers[2]) != 0) goto fail;
    double upload_ms = now_ms() - upload_start;

    VkDescriptorPoolSize pool_size = {
        .type = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,
        .descriptorCount = 3,
    };
    VkDescriptorPoolCreateInfo dpci = {
        .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO,
        .maxSets = 1,
        .poolSizeCount = 1,
        .pPoolSizes = &pool_size,
    };
    fail_stage = "create-descriptor-pool";
    rc = vkCreateDescriptorPool(rt->device, &dpci, NULL, &descriptor_pool);
    if (rc != VK_SUCCESS) { fail_result = rc; goto fail; }
    VkDescriptorSet descriptor_set = VK_NULL_HANDLE;
    VkDescriptorSetAllocateInfo dsai = {
        .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO,
        .descriptorPool = descriptor_pool,
        .descriptorSetCount = 1,
        .pSetLayouts = &rt->set_layout,
    };
    fail_stage = "allocate-descriptor-set";
    rc = vkAllocateDescriptorSets(rt->device, &dsai, &descriptor_set);
    if (rc != VK_SUCCESS) { fail_result = rc; goto fail; }
    VkDescriptorBufferInfo infos[3];
    VkWriteDescriptorSet writes[3];
    memset(writes, 0, sizeof(writes));
    for (uint32_t i = 0; i < 3; ++i) {
        infos[i].buffer = buffers[i].buffer;
        infos[i].offset = 0;
        infos[i].range = (VkDeviceSize)bytes;
        writes[i].sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;
        writes[i].dstSet = descriptor_set;
        writes[i].dstBinding = i;
        writes[i].descriptorCount = 1;
        writes[i].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
        writes[i].pBufferInfo = &infos[i];
    }
    vkUpdateDescriptorSets(rt->device, 3, writes, 0, NULL);

    VkCommandBufferAllocateInfo cbai = {
        .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,
        .commandPool = rt->command_pool,
        .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY,
        .commandBufferCount = 1,
    };
    fail_stage = "allocate-command-buffer";
    rc = vkAllocateCommandBuffers(rt->device, &cbai, &command_buffer);
    if (rc != VK_SUCCESS) { fail_result = rc; goto fail; }
    VkCommandBufferBeginInfo cbi = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
    fail_stage = "begin-command-buffer";
    rc = vkBeginCommandBuffer(command_buffer, &cbi);
    if (rc != VK_SUCCESS) { fail_result = rc; goto fail; }
    vkCmdBindPipeline(command_buffer, VK_PIPELINE_BIND_POINT_COMPUTE, rt->pipeline);
    vkCmdBindDescriptorSets(command_buffer, VK_PIPELINE_BIND_POINT_COMPUTE, rt->pipeline_layout, 0, 1, &descriptor_set, 0, NULL);
    vkCmdDispatch(command_buffer, (uint32_t)((n + 127) / 128), 1, 1);
    fail_stage = "end-command-buffer";
    rc = vkEndCommandBuffer(command_buffer);
    if (rc != VK_SUCCESS) { fail_result = rc; goto fail; }
    VkFenceCreateInfo fci = {.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
    fail_stage = "create-fence";
    rc = vkCreateFence(rt->device, &fci, NULL, &fence);
    if (rc != VK_SUCCESS) { fail_result = rc; goto fail; }
    VkSubmitInfo submit = {
        .sType = VK_STRUCTURE_TYPE_SUBMIT_INFO,
        .commandBufferCount = 1,
        .pCommandBuffers = &command_buffer,
    };

    int last = 0;
    for (int i = 0; i < count; ++i) {
        rc = vkResetFences(rt->device, 1, &fence);
        if (rc != VK_SUCCESS) { fail_stage = "reset-fence"; fail_result = rc; goto fail; }
        double dispatch_start = now_ms();
        rc = vkQueueSubmit(rt->queue, 1, &submit, fence);
        if (rc != VK_SUCCESS) { fail_stage = "queue-submit"; fail_result = rc; goto fail; }
        rc = vkWaitForFences(rt->device, 1, &fence, VK_TRUE, UINT64_MAX);
        if (rc != VK_SUCCESS) { fail_stage = "wait-fence"; fail_result = rc; goto fail; }
        double dispatch_ms = now_ms() - dispatch_start;

        double download_start = now_ms();
        memcpy(out, buffers[2].map, bytes);
        double download_ms = now_ms() - download_start;
        double max_err = 0.0;
        for (size_t j = 0; j < n; ++j) {
            double e = fabs((double)out[j] - (double)(a[j] + b[j]));
            if (e > max_err) max_err = e;
        }
        const int valid = max_err <= 0.0001;
        const double init_ms = (!was_ready && i == 0) ? rt->init_ms : 0.0;
        const double run_upload_ms = i == 0 ? upload_ms : 0.0;
        fprintf(json_out(),
                "{\"executor\":\"pdocker-gpu-executor\",\"api\":\"%s\",\"abi_version\":\"%s\","
                "\"role\":\"%s\",\"llm_engine\":\"%s\",\"device_independent\":true,"
                "\"backend_impl\":\"android_vulkan\",\"backend_affinity\":\"same-api\","
                "\"backend_cached\":true,\"buffer_residency\":\"resident\","
                "\"transport\":\"direct-vulkan-resident-buffer\","
                "\"kernel\":\"vector_add\",\"problem_size\":\"n=%zu\","
                "\"init_ms\":%.4f,\"compile_ms\":0.0000,\"upload_ms\":%.4f,"
                "\"dispatch_ms\":%.4f,\"download_ms\":%.4f,\"total_ms\":%.4f,"
                "\"max_abs_error\":%.8f,\"valid\":%s}\n",
                PDOCKER_GPU_COMMAND_API, PDOCKER_GPU_ABI_VERSION,
                PDOCKER_GPU_EXECUTOR_ROLE, PDOCKER_GPU_LLM_ENGINE_LOCATION,
                n, init_ms, run_upload_ms, dispatch_ms, download_ms,
                init_ms + run_upload_ms + dispatch_ms + download_ms, max_err,
                valid ? "true" : "false");
        fflush(json_out());
        last = valid ? 0 : 6;
        if (last != 0) break;
    }

    if (fence) vkDestroyFence(rt->device, fence, NULL);
    if (command_buffer) vkFreeCommandBuffers(rt->device, rt->command_pool, 1, &command_buffer);
    if (descriptor_pool) vkDestroyDescriptorPool(rt->device, descriptor_pool, NULL);
    destroy_vulkan_vector_buffer(rt->device, &buffers[0]);
    destroy_vulkan_vector_buffer(rt->device, &buffers[1]);
    destroy_vulkan_vector_buffer(rt->device, &buffers[2]);
    free(a);
    free(b);
    free(out);
    return last;

fail:
    fprintf(stderr, "pdocker-gpu-executor: Vulkan resident vector_add failed stage=%s rc=%d\n", fail_stage, fail_result);
    if (fence) vkDestroyFence(rt->device, fence, NULL);
    if (command_buffer) vkFreeCommandBuffers(rt->device, rt->command_pool, 1, &command_buffer);
    if (descriptor_pool) vkDestroyDescriptorPool(rt->device, descriptor_pool, NULL);
    destroy_vulkan_vector_buffer(rt->device, &buffers[0]);
    destroy_vulkan_vector_buffer(rt->device, &buffers[1]);
    destroy_vulkan_vector_buffer(rt->device, &buffers[2]);
    free(a);
    free(b);
    free(out);
    return -21;
}

static void fill_matmul_inputs(float *a, float *b, size_t n) {
    for (size_t r = 0; r < n; ++r) {
        for (size_t c = 0; c < n; ++c) {
            int av = (int)((r * 3 + c * 5) % 17) - 8;
            int bv = (int)((r * 7 + c * 2) % 19) - 9;
            a[r * n + c] = (float)av * 0.03125f;
            b[r * n + c] = (float)bv * 0.025f;
        }
    }
}

static void matmul_cpu_ref(const float *a, const float *b, float *out, size_t n) {
    for (size_t r = 0; r < n; ++r) {
        for (size_t c = 0; c < n; ++c) {
            float sum = 0.0f;
            for (size_t k = 0; k < n; ++k) {
                sum += a[r * n + k] * b[k * n + c];
            }
            out[r * n + c] = sum;
        }
    }
}

static double matmul_max_error(const float *got, const float *ref, size_t n) {
    double max_err = 0.0;
    size_t total = n * n;
    for (size_t i = 0; i < total; ++i) {
        double e = fabs((double)got[i] - (double)ref[i]);
        if (e > max_err) max_err = e;
    }
    return max_err;
}

static double matmul_checksum(const float *m, size_t n) {
    volatile double sum = 0.0;
    size_t total = n * n;
    for (size_t i = 0; i < total; ++i) {
        sum += (double)m[i] * (double)((i % 13) + 1);
    }
    return sum;
}

static int bench_cpu_matmul256(int count) {
    if (count <= 0) count = 1;
    const size_t n = 256;
    const size_t items = n * n;
    const size_t bytes = items * sizeof(float);
    float *a = (float *)malloc(bytes);
    float *b = (float *)malloc(bytes);
    float *out = (float *)calloc(items, sizeof(float));
    if (!a || !b || !out) {
        free(a);
        free(b);
        free(out);
        json_fail("alloc", "host allocation failed");
        return 2;
    }
    fill_matmul_inputs(a, b, n);
    int last = 0;
    for (int i = 0; i < count; ++i) {
        memset(out, 0, bytes);
        double start = now_ms();
        matmul_cpu_ref(a, b, out, n);
        double checksum = matmul_checksum(out, n);
        double total_ms = now_ms() - start;
        fprintf(json_out(),
                "{\"executor\":\"pdocker-gpu-executor\",\"api\":\"%s\",\"abi_version\":\"%s\","
                "\"role\":\"%s\",\"llm_engine\":\"%s\",\"device_independent\":true,"
                "\"backend_impl\":\"cpu_scalar\",\"backend_affinity\":\"cpu\","
                "\"transport\":\"host-cpu-local-process-buffer\","
                "\"kernel\":\"matmul_fp32\",\"problem_size\":\"n=%zux%zu\","
                "\"init_ms\":0.0000,\"compile_ms\":0.0000,\"upload_ms\":0.0000,"
                "\"dispatch_ms\":%.4f,\"download_ms\":0.0000,\"total_ms\":%.4f,"
                "\"checksum\":%.8f,\"max_abs_error\":0.00000000,\"valid\":true}\n",
                PDOCKER_GPU_COMMAND_API, PDOCKER_GPU_ABI_VERSION,
                PDOCKER_GPU_EXECUTOR_ROLE, PDOCKER_GPU_LLM_ENGINE_LOCATION,
                n, n, total_ms, total_ms, checksum);
        fflush(json_out());
        last = 0;
    }
    free(a);
    free(b);
    free(out);
    return last;
}

static int bench_vulkan_matmul256_resident(int count) {
    if (count <= 0) count = 1;
    const size_t n = 256;
    const size_t items = n * n;
    const size_t bytes = items * sizeof(float);
    float *a = (float *)malloc(bytes);
    float *b = (float *)malloc(bytes);
    float *out = (float *)calloc(items, sizeof(float));
    float *ref = (float *)calloc(items, sizeof(float));
    if (!a || !b || !out || !ref) {
        free(a);
        free(b);
        free(out);
        free(ref);
        json_fail("alloc", "host allocation failed");
        return 2;
    }
    fill_matmul_inputs(a, b, n);
    matmul_cpu_ref(a, b, ref, n);

    const int was_ready = g_vulkan_runtime.ready;
    if (init_vulkan_runtime(&g_vulkan_runtime) != 0) {
        free(a);
        free(b);
        free(out);
        free(ref);
        return -21;
    }
    VulkanRuntime *rt = &g_vulkan_runtime;
    VulkanVectorBuffer buffers[3];
    memset(buffers, 0, sizeof(buffers));
    VkDescriptorPool descriptor_pool = VK_NULL_HANDLE;
    VkCommandBuffer command_buffer = VK_NULL_HANDLE;
    VkFence fence = VK_NULL_HANDLE;
    VkResult rc = VK_SUCCESS;
    const char *fail_stage = "start";
    VkResult fail_result = VK_SUCCESS;

    double upload_start = now_ms();
    if (create_vulkan_vector_buffer(rt->physical_device, rt->device, bytes, a, &buffers[0]) != 0) goto fail;
    if (create_vulkan_vector_buffer(rt->physical_device, rt->device, bytes, b, &buffers[1]) != 0) goto fail;
    if (create_vulkan_vector_buffer(rt->physical_device, rt->device, bytes, NULL, &buffers[2]) != 0) goto fail;
    double upload_ms = now_ms() - upload_start;

    VkDescriptorPoolSize pool_size = {.type = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, .descriptorCount = 3};
    VkDescriptorPoolCreateInfo dpci = {
        .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO,
        .maxSets = 1,
        .poolSizeCount = 1,
        .pPoolSizes = &pool_size,
    };
    fail_stage = "create-descriptor-pool";
    rc = vkCreateDescriptorPool(rt->device, &dpci, NULL, &descriptor_pool);
    if (rc != VK_SUCCESS) { fail_result = rc; goto fail; }
    VkDescriptorSet descriptor_set = VK_NULL_HANDLE;
    VkDescriptorSetAllocateInfo dsai = {
        .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO,
        .descriptorPool = descriptor_pool,
        .descriptorSetCount = 1,
        .pSetLayouts = &rt->set_layout,
    };
    fail_stage = "allocate-descriptor-set";
    rc = vkAllocateDescriptorSets(rt->device, &dsai, &descriptor_set);
    if (rc != VK_SUCCESS) { fail_result = rc; goto fail; }
    VkDescriptorBufferInfo infos[3];
    VkWriteDescriptorSet writes[3];
    memset(writes, 0, sizeof(writes));
    for (uint32_t i = 0; i < 3; ++i) {
        infos[i].buffer = buffers[i].buffer;
        infos[i].offset = 0;
        infos[i].range = (VkDeviceSize)bytes;
        writes[i].sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;
        writes[i].dstSet = descriptor_set;
        writes[i].dstBinding = i;
        writes[i].descriptorCount = 1;
        writes[i].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
        writes[i].pBufferInfo = &infos[i];
    }
    vkUpdateDescriptorSets(rt->device, 3, writes, 0, NULL);

    VkCommandBufferAllocateInfo cbai = {
        .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,
        .commandPool = rt->command_pool,
        .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY,
        .commandBufferCount = 1,
    };
    fail_stage = "allocate-command-buffer";
    rc = vkAllocateCommandBuffers(rt->device, &cbai, &command_buffer);
    if (rc != VK_SUCCESS) { fail_result = rc; goto fail; }
    VkCommandBufferBeginInfo cbi = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
    fail_stage = "begin-command-buffer";
    rc = vkBeginCommandBuffer(command_buffer, &cbi);
    if (rc != VK_SUCCESS) { fail_result = rc; goto fail; }
    vkCmdBindPipeline(command_buffer, VK_PIPELINE_BIND_POINT_COMPUTE, rt->matmul_pipeline);
    vkCmdBindDescriptorSets(command_buffer, VK_PIPELINE_BIND_POINT_COMPUTE, rt->pipeline_layout, 0, 1, &descriptor_set, 0, NULL);
    vkCmdDispatch(command_buffer, 32, 32, 1);
    fail_stage = "end-command-buffer";
    rc = vkEndCommandBuffer(command_buffer);
    if (rc != VK_SUCCESS) { fail_result = rc; goto fail; }
    VkFenceCreateInfo fci = {.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
    fail_stage = "create-fence";
    rc = vkCreateFence(rt->device, &fci, NULL, &fence);
    if (rc != VK_SUCCESS) { fail_result = rc; goto fail; }
    VkSubmitInfo submit = {
        .sType = VK_STRUCTURE_TYPE_SUBMIT_INFO,
        .commandBufferCount = 1,
        .pCommandBuffers = &command_buffer,
    };

    int last = 0;
    for (int i = 0; i < count; ++i) {
        rc = vkResetFences(rt->device, 1, &fence);
        if (rc != VK_SUCCESS) { fail_stage = "reset-fence"; fail_result = rc; goto fail; }
        double dispatch_start = now_ms();
        rc = vkQueueSubmit(rt->queue, 1, &submit, fence);
        if (rc != VK_SUCCESS) { fail_stage = "queue-submit"; fail_result = rc; goto fail; }
        rc = vkWaitForFences(rt->device, 1, &fence, VK_TRUE, UINT64_MAX);
        if (rc != VK_SUCCESS) { fail_stage = "wait-fence"; fail_result = rc; goto fail; }
        double dispatch_ms = now_ms() - dispatch_start;
        double download_start = now_ms();
        memcpy(out, buffers[2].map, bytes);
        double download_ms = now_ms() - download_start;
        double max_err = matmul_max_error(out, ref, n);
        double checksum = matmul_checksum(out, n);
        const int valid = max_err <= 0.001;
        const double init_ms = (!was_ready && i == 0) ? rt->init_ms : 0.0;
        const double run_upload_ms = i == 0 ? upload_ms : 0.0;
        fprintf(json_out(),
                "{\"executor\":\"pdocker-gpu-executor\",\"api\":\"%s\",\"abi_version\":\"%s\","
                "\"role\":\"%s\",\"llm_engine\":\"%s\",\"device_independent\":true,"
                "\"backend_impl\":\"android_vulkan\",\"backend_affinity\":\"same-api\","
                "\"backend_cached\":true,\"buffer_residency\":\"resident\","
                "\"transport\":\"direct-vulkan-resident-buffer\","
                "\"kernel\":\"matmul_fp32\",\"problem_size\":\"n=%zux%zu\","
                "\"init_ms\":%.4f,\"compile_ms\":0.0000,\"upload_ms\":%.4f,"
                "\"dispatch_ms\":%.4f,\"download_ms\":%.4f,\"total_ms\":%.4f,"
                "\"checksum\":%.8f,\"max_abs_error\":%.8f,\"valid\":%s}\n",
                PDOCKER_GPU_COMMAND_API, PDOCKER_GPU_ABI_VERSION,
                PDOCKER_GPU_EXECUTOR_ROLE, PDOCKER_GPU_LLM_ENGINE_LOCATION,
                n, n, init_ms, run_upload_ms, dispatch_ms, download_ms,
                init_ms + run_upload_ms + dispatch_ms + download_ms, checksum, max_err,
                valid ? "true" : "false");
        fflush(json_out());
        last = valid ? 0 : 6;
        if (last != 0) break;
    }

    if (fence) vkDestroyFence(rt->device, fence, NULL);
    if (command_buffer) vkFreeCommandBuffers(rt->device, rt->command_pool, 1, &command_buffer);
    if (descriptor_pool) vkDestroyDescriptorPool(rt->device, descriptor_pool, NULL);
    destroy_vulkan_vector_buffer(rt->device, &buffers[0]);
    destroy_vulkan_vector_buffer(rt->device, &buffers[1]);
    destroy_vulkan_vector_buffer(rt->device, &buffers[2]);
    free(a);
    free(b);
    free(out);
    free(ref);
    return last;

fail:
    fprintf(stderr, "pdocker-gpu-executor: Vulkan resident matmul failed stage=%s rc=%d\n", fail_stage, fail_result);
    if (fence) vkDestroyFence(rt->device, fence, NULL);
    if (command_buffer) vkFreeCommandBuffers(rt->device, rt->command_pool, 1, &command_buffer);
    if (descriptor_pool) vkDestroyDescriptorPool(rt->device, descriptor_pool, NULL);
    destroy_vulkan_vector_buffer(rt->device, &buffers[0]);
    destroy_vulkan_vector_buffer(rt->device, &buffers[1]);
    destroy_vulkan_vector_buffer(rt->device, &buffers[2]);
    free(a);
    free(b);
    free(out);
    free(ref);
    return -21;
}

static int bench_opencl_vector_add(int count) {
    if (count <= 0) count = 1;
    const size_t n = PDOCKER_GPU_VECTOR_ADD_DEFAULT_N;
    const size_t bytes = n * sizeof(float);
    float *a = (float *)malloc(bytes);
    float *b = (float *)malloc(bytes);
    float *out = (float *)calloc(n, sizeof(float));
    if (!a || !b || !out) {
        free(a);
        free(b);
        free(out);
        json_fail("alloc", "host allocation failed");
        return 2;
    }
    fill_inputs(a, b, n);
    int last = 0;
    for (int i = 0; i < count; ++i) {
        memset(out, 0, bytes);
        last = run_vector_add_arrays_opencl(a, b, out, n, "direct-opencl-local-process-buffer");
    }
    free(a);
    free(b);
    free(out);
    return last;
}

static int bench_noop(int count) {
    if (count <= 0) count = 1;
    for (int i = 0; i < count; ++i) {
        print_noop();
    }
    return 0;
}

static int parse_count(const char *s, int fallback) {
    if (!s || !s[0]) return fallback;
    char *end = NULL;
    long n = strtol(s, &end, 10);
    if (!end || *end || n <= 0 || n > 10000) return fallback;
    return (int)n;
}

static int recv_command_with_fds(int cfd, char *cmd, size_t cmd_size, int *passed_fds, size_t max_fds, size_t *fd_count) {
    if (!cmd || cmd_size == 0 || !passed_fds || !fd_count) return -EINVAL;
    *fd_count = 0;
    for (size_t i = 0; i < max_fds; ++i) passed_fds[i] = -1;
    char control[CMSG_SPACE(sizeof(int) * PDOCKER_GPU_MAX_PASSED_FDS)];
    struct iovec iov;
    struct msghdr msg;
    memset(control, 0, sizeof(control));
    memset(&iov, 0, sizeof(iov));
    memset(&msg, 0, sizeof(msg));
    iov.iov_base = cmd;
    iov.iov_len = cmd_size - 1;
    msg.msg_iov = &iov;
    msg.msg_iovlen = 1;
    msg.msg_control = control;
    msg.msg_controllen = sizeof(control);
    ssize_t n = recvmsg(cfd, &msg, 0);
    if (n <= 0) return (int)n;
    cmd[n] = '\0';
    cmd[strcspn(cmd, "\r\n")] = '\0';
    for (struct cmsghdr *cmsg = CMSG_FIRSTHDR(&msg);
         cmsg != NULL;
         cmsg = CMSG_NXTHDR(&msg, cmsg)) {
        if (cmsg->cmsg_level == SOL_SOCKET && cmsg->cmsg_type == SCM_RIGHTS &&
            cmsg->cmsg_len >= CMSG_LEN(sizeof(int))) {
            size_t bytes = cmsg->cmsg_len - CMSG_LEN(0);
            size_t count = bytes / sizeof(int);
            if (count > max_fds) count = max_fds;
            memcpy(passed_fds, CMSG_DATA(cmsg), count * sizeof(int));
            *fd_count = count;
            break;
        }
    }
    return (int)n;
}

static int serve_socket(const char *path) {
    if (!path || !path[0]) {
        fprintf(stderr, "pdocker-gpu-executor: missing socket path\n");
        return 64;
    }
    if (strlen(path) >= sizeof(((struct sockaddr_un *)0)->sun_path)) {
        fprintf(stderr, "pdocker-gpu-executor: socket path too long: %s\n", path);
        return 64;
    }
    signal(SIGPIPE, SIG_IGN);
    int sfd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (sfd < 0) {
        perror("socket");
        return 70;
    }
    unlink(path);
    struct sockaddr_un addr;
    memset(&addr, 0, sizeof(addr));
    addr.sun_family = AF_UNIX;
    snprintf(addr.sun_path, sizeof(addr.sun_path), "%s", path);
    if (bind(sfd, (struct sockaddr *)&addr, sizeof(addr)) != 0) {
        perror("bind");
        close(sfd);
        return 70;
    }
    chmod(path, 0600);
    if (listen(sfd, 8) != 0) {
        perror("listen");
        close(sfd);
        return 70;
    }
    GpuContext ctx;
    int init_rc = init_gpu_context(&ctx);
    if (init_rc != 0) {
        close(sfd);
        unlink(path);
        return init_rc;
    }
    fprintf(stderr, "pdocker-gpu-executor: serving %s api=%s\n", path, PDOCKER_GPU_COMMAND_API);
    for (;;) {
        int cfd = accept(sfd, NULL, NULL);
        if (cfd < 0) {
            if (errno == EINTR) continue;
            perror("accept");
            break;
        }
        FILE *out = fdopen(dup(cfd), "w");
        if (!out) {
            close(cfd);
            continue;
        }
        setvbuf(out, NULL, _IONBF, 0);
        char cmd[PDOCKER_GPU_MAX_COMMAND_BYTES];
        RegisteredVectorBuffer registered;
        memset(&registered, 0, sizeof(registered));
        for (;;) {
            int passed_fds[PDOCKER_GPU_MAX_PASSED_FDS];
            size_t passed_fd_count = 0;
            int nread = recv_command_with_fds(cfd, cmd, sizeof(cmd), passed_fds, PDOCKER_GPU_MAX_PASSED_FDS, &passed_fd_count);
            if (nread <= 0) break;
            g_json_out = out;
            if (strcmp(cmd, "CAPABILITIES") == 0) {
                print_capabilities("unix-socket-command-queue");
            } else if (strcmp(cmd, "NOOP") == 0) {
                print_noop();
            } else if (strcmp(cmd, "VECTOR_ADD") == 0) {
                (void)run_vector_add();
            } else if (strncmp(cmd, "VECTOR_ADD_FD ", 14) == 0) {
                size_t n = (size_t)strtoull(cmd + 14, NULL, 10);
                (void)run_vector_add_fd(passed_fds[0], n, GPU_API_AUTO);
                passed_fds[0] = -1;
            } else if (strncmp(cmd, "REGISTER_VECTOR_FD ", 19) == 0) {
                size_t n = (size_t)strtoull(cmd + 19, NULL, 10);
                (void)register_vector_buffer(&registered, passed_fds[0], n);
                passed_fds[0] = -1;
            } else if (strcmp(cmd, "VECTOR_ADD_REGISTERED") == 0) {
                (void)run_registered_vector_add(&registered);
            } else if (strncmp(cmd, "OPENCL_VECTOR_ADD_3FD ", 22) == 0) {
                size_t n = (size_t)strtoull(cmd + 22, NULL, 10);
                if (passed_fd_count < 3) {
                    json_fail("fd", "OPENCL_VECTOR_ADD_3FD requires three fds");
                } else {
                    (void)run_vector_add_3fd(passed_fds[0], passed_fds[1], passed_fds[2], n, GPU_API_OPENCL);
                    passed_fds[0] = passed_fds[1] = passed_fds[2] = -1;
                }
            } else if (strncmp(cmd, "VULKAN_VECTOR_ADD_3FD ", 22) == 0) {
                size_t n = (size_t)strtoull(cmd + 22, NULL, 10);
                if (passed_fd_count < 3) {
                    json_fail("fd", "VULKAN_VECTOR_ADD_3FD requires three fds");
                } else {
                    (void)run_vector_add_3fd(passed_fds[0], passed_fds[1], passed_fds[2], n, GPU_API_VULKAN);
                    passed_fds[0] = passed_fds[1] = passed_fds[2] = -1;
                }
            } else if (strncmp(cmd, "VECTOR_ADD_3FD ", 15) == 0) {
                size_t n = (size_t)strtoull(cmd + 15, NULL, 10);
                if (passed_fd_count < 3) {
                    json_fail("fd", "VECTOR_ADD_3FD requires three fds");
                } else {
                    (void)run_vector_add_3fd(passed_fds[0], passed_fds[1], passed_fds[2], n, GPU_API_AUTO);
                    passed_fds[0] = passed_fds[1] = passed_fds[2] = -1;
                }
            } else if (strncmp(cmd, "VULKAN_DISPATCH_V2 ", 19) == 0) {
                char *save = NULL;
                char *cursor = cmd + 19;
                char *tok = strtok_r(cursor, " ", &save);
                size_t shader_size = tok ? (size_t)strtoull(tok, NULL, 10) : 0;
                tok = strtok_r(NULL, " ", &save);
                size_t binding_count = tok ? (size_t)strtoull(tok, NULL, 10) : 0;
                tok = strtok_r(NULL, " ", &save);
                size_t push_size = tok ? (size_t)strtoull(tok, NULL, 10) : 0;
                tok = strtok_r(NULL, " ", &save);
                uint32_t gx = tok ? (uint32_t)strtoul(tok, NULL, 10) : 0;
                tok = strtok_r(NULL, " ", &save);
                uint32_t gy = tok ? (uint32_t)strtoul(tok, NULL, 10) : 0;
                tok = strtok_r(NULL, " ", &save);
                uint32_t gz = tok ? (uint32_t)strtoul(tok, NULL, 10) : 0;
                tok = strtok_r(NULL, " ", &save);
                const char *push_hex = tok ? tok : "-";
                tok = strtok_r(NULL, " ", &save);
                const char *entry_hex = tok ? tok : "-";
                tok = strtok_r(NULL, " ", &save);
                size_t specialization_count = tok ? (size_t)strtoull(tok, NULL, 10) : 0;
                tok = strtok_r(NULL, " ", &save);
                size_t specialization_data_size = tok ? (size_t)strtoull(tok, NULL, 10) : 0;
                tok = strtok_r(NULL, " ", &save);
                const char *specialization_hex = tok ? tok : "-";
                uint8_t push[PDOCKER_GPU_MAX_PUSH_BYTES];
                char entry_name[PDOCKER_GPU_MAX_VULKAN_ENTRY_NAME];
                uint8_t specialization_data[PDOCKER_GPU_MAX_VULKAN_SPECIALIZATION_BYTES];
                VulkanDispatchSpecialization specializations[PDOCKER_GPU_MAX_VULKAN_SPECIALIZATION_ENTRIES];
                VulkanDispatchBinding bindings[PDOCKER_GPU_MAX_VULKAN_BINDINGS];
                VulkanDispatchOptions options;
                memset(push, 0, sizeof(push));
                memset(entry_name, 0, sizeof(entry_name));
                memset(specialization_data, 0, sizeof(specialization_data));
                memset(specializations, 0, sizeof(specializations));
                memset(bindings, 0, sizeof(bindings));
                memset(&options, 0, sizeof(options));
                int parse_ok = 1;
                if (binding_count == 0 || binding_count > PDOCKER_GPU_MAX_VULKAN_BINDINGS ||
                    push_size > PDOCKER_GPU_MAX_PUSH_BYTES ||
                    specialization_count > PDOCKER_GPU_MAX_VULKAN_SPECIALIZATION_ENTRIES ||
                    specialization_data_size > PDOCKER_GPU_MAX_VULKAN_SPECIALIZATION_BYTES ||
                    passed_fd_count < 1 + binding_count) {
                    parse_ok = 0;
                }
                if (parse_ok && push_size > 0) {
                    int decoded = hex_decode(push_hex, push, sizeof(push));
                    if (decoded < 0 || (size_t)decoded != push_size) parse_ok = 0;
                } else if (parse_ok && strcmp(push_hex, "-") != 0) {
                    parse_ok = 0;
                }
                if (parse_ok && strcmp(entry_hex, "-") != 0) {
                    int decoded = hex_decode(entry_hex, (uint8_t *)entry_name, sizeof(entry_name) - 1);
                    if (decoded <= 0 || (size_t)decoded >= sizeof(entry_name)) parse_ok = 0;
                    else entry_name[decoded] = '\0';
                } else if (parse_ok) {
                    snprintf(entry_name, sizeof(entry_name), "main");
                }
                if (parse_ok && specialization_data_size > 0) {
                    int decoded = hex_decode(specialization_hex, specialization_data, sizeof(specialization_data));
                    if (decoded < 0 || (size_t)decoded != specialization_data_size) parse_ok = 0;
                } else if (parse_ok && strcmp(specialization_hex, "-") != 0) {
                    parse_ok = 0;
                }
                for (size_t i = 0; parse_ok && i < specialization_count; ++i) {
                    tok = strtok_r(NULL, " ", &save);
                    if (!tok) { parse_ok = 0; break; }
                    specializations[i].constant_id = (uint32_t)strtoul(tok, NULL, 10);
                    tok = strtok_r(NULL, " ", &save);
                    if (!tok) { parse_ok = 0; break; }
                    specializations[i].offset = (uint32_t)strtoul(tok, NULL, 10);
                    tok = strtok_r(NULL, " ", &save);
                    if (!tok) { parse_ok = 0; break; }
                    specializations[i].size = (size_t)strtoull(tok, NULL, 10);
                }
                for (size_t i = 0; parse_ok && i < binding_count; ++i) {
                    tok = strtok_r(NULL, " ", &save);
                    if (!tok) { parse_ok = 0; break; }
                    bindings[i].binding = (uint32_t)strtoul(tok, NULL, 10);
                    tok = strtok_r(NULL, " ", &save);
                    if (!tok) { parse_ok = 0; break; }
                    bindings[i].offset = (off_t)strtoll(tok, NULL, 10);
                    tok = strtok_r(NULL, " ", &save);
                    if (!tok) { parse_ok = 0; break; }
                    bindings[i].size = (size_t)strtoull(tok, NULL, 10);
                    tok = strtok_r(NULL, " ", &save);
                    if (!tok) { parse_ok = 0; break; }
                    bindings[i].api_offset = (off_t)strtoll(tok, NULL, 10);
                    tok = strtok_r(NULL, " ", &save);
                    if (!tok) { parse_ok = 0; break; }
                    bindings[i].api_range = (size_t)strtoull(tok, NULL, 10);
                    tok = strtok_r(NULL, " ", &save);
                    if (!tok) { parse_ok = 0; break; }
                    bindings[i].api_buffer_size = (size_t)strtoull(tok, NULL, 10);
                    tok = strtok_r(NULL, " ", &save);
                    if (!tok) { parse_ok = 0; break; }
                    bindings[i].api_descriptor_type = (uint32_t)strtoul(tok, NULL, 10);
                    tok = strtok_r(NULL, " ", &save);
                    if (!tok) { parse_ok = 0; break; }
                    bindings[i].api_dynamic = (int)strtol(tok, NULL, 10);
                    tok = strtok_r(NULL, " ", &save);
                    if (!tok) { parse_ok = 0; break; }
                    bindings[i].api_memory_offset = (off_t)strtoll(tok, NULL, 10);
                }
                while (parse_ok && (tok = strtok_r(NULL, " ", &save)) != NULL) {
                    if (parse_vulkan_dispatch_option(&options, tok) != 0) {
                        parse_ok = 0;
                    }
                }
                if (!parse_ok) {
                    json_fail("vulkan-dispatch", "invalid command");
                } else {
                    (void)run_vulkan_dispatch_fd(passed_fds[0], &passed_fds[1], bindings, binding_count,
                                                 shader_size, entry_name,
                                                 specializations, specialization_count,
                                                 specialization_data, specialization_data_size,
                                                 &options,
                                                 push, push_size, gx, gy, gz);
                }
            } else if (strncmp(cmd, "VULKAN_DISPATCH_V1 ", 19) == 0) {
                char *save = NULL;
                char *cursor = cmd + 19;
                char *tok = strtok_r(cursor, " ", &save);
                size_t shader_size = tok ? (size_t)strtoull(tok, NULL, 10) : 0;
                tok = strtok_r(NULL, " ", &save);
                size_t binding_count = tok ? (size_t)strtoull(tok, NULL, 10) : 0;
                tok = strtok_r(NULL, " ", &save);
                size_t push_size = tok ? (size_t)strtoull(tok, NULL, 10) : 0;
                tok = strtok_r(NULL, " ", &save);
                uint32_t gx = tok ? (uint32_t)strtoul(tok, NULL, 10) : 0;
                tok = strtok_r(NULL, " ", &save);
                uint32_t gy = tok ? (uint32_t)strtoul(tok, NULL, 10) : 0;
                tok = strtok_r(NULL, " ", &save);
                uint32_t gz = tok ? (uint32_t)strtoul(tok, NULL, 10) : 0;
                tok = strtok_r(NULL, " ", &save);
                const char *push_hex = tok ? tok : "-";
                uint8_t push[PDOCKER_GPU_MAX_PUSH_BYTES];
                VulkanDispatchBinding bindings[PDOCKER_GPU_MAX_VULKAN_BINDINGS];
                memset(push, 0, sizeof(push));
                memset(bindings, 0, sizeof(bindings));
                int parse_ok = 1;
                if (binding_count == 0 || binding_count > PDOCKER_GPU_MAX_VULKAN_BINDINGS ||
                    push_size > PDOCKER_GPU_MAX_PUSH_BYTES ||
                    passed_fd_count < 1 + binding_count) {
                    parse_ok = 0;
                }
                if (parse_ok && push_size > 0) {
                    int decoded = hex_decode(push_hex, push, sizeof(push));
                    if (decoded < 0 || (size_t)decoded != push_size) parse_ok = 0;
                } else if (parse_ok && strcmp(push_hex, "-") != 0) {
                    parse_ok = 0;
                }
                for (size_t i = 0; parse_ok && i < binding_count; ++i) {
                    tok = strtok_r(NULL, " ", &save);
                    if (!tok) { parse_ok = 0; break; }
                    bindings[i].binding = (uint32_t)strtoul(tok, NULL, 10);
                    tok = strtok_r(NULL, " ", &save);
                    if (!tok) { parse_ok = 0; break; }
                    bindings[i].offset = (off_t)strtoll(tok, NULL, 10);
                    tok = strtok_r(NULL, " ", &save);
                    if (!tok) { parse_ok = 0; break; }
                    bindings[i].size = (size_t)strtoull(tok, NULL, 10);
                }
                if (!parse_ok) {
                    json_fail("vulkan-dispatch", "invalid command");
                } else {
                    (void)run_vulkan_dispatch_fd(passed_fds[0], &passed_fds[1], bindings, binding_count,
                                                 shader_size, "main",
                                                 NULL, 0, NULL, 0,
                                                 NULL,
                                                 push, push_size, gx, gy, gz);
                }
            } else {
                json_fail("command", "unknown command");
            }
            for (size_t i = 0; i < passed_fd_count && i < PDOCKER_GPU_MAX_PASSED_FDS; ++i) {
                if (passed_fds[i] >= 0) close(passed_fds[i]);
            }
            g_json_out = NULL;
        }
        clear_registered_vector_buffer(&registered);
        fclose(out);
        close(cfd);
    }
    destroy_gpu_context(&ctx);
    close(sfd);
    unlink(path);
    return 70;
}

int main(int argc, char **argv) {
    if (argc > 1 && strcmp(argv[1], "--capabilities") == 0) {
        print_capabilities("self-test-now; unix-socket-command-queue");
        return 0;
    }
    if (argc > 2 && strcmp(argv[1], "--serve-socket") == 0) {
        return serve_socket(argv[2]);
    }
    if (argc > 1 && strcmp(argv[1], "--bench-vector-add") == 0) {
        return bench_vector_add(parse_count(argc > 2 ? argv[2] : NULL, 5));
    }
    if (argc > 1 && strcmp(argv[1], "--bench-cpu-vector-add") == 0) {
        return bench_cpu_vector_add(parse_count(argc > 2 ? argv[2] : NULL, 5));
    }
    if (argc > 1 && strcmp(argv[1], "--bench-vulkan-vector-add") == 0) {
        return bench_vulkan_vector_add(parse_count(argc > 2 ? argv[2] : NULL, 5));
    }
    if (argc > 1 && strcmp(argv[1], "--bench-vulkan-vector-add-resident") == 0) {
        return bench_vulkan_vector_add_resident(parse_count(argc > 2 ? argv[2] : NULL, 5));
    }
    if (argc > 1 && strcmp(argv[1], "--bench-cpu-matmul256") == 0) {
        return bench_cpu_matmul256(parse_count(argc > 2 ? argv[2] : NULL, 5));
    }
    if (argc > 1 && strcmp(argv[1], "--bench-vulkan-matmul256-resident") == 0) {
        return bench_vulkan_matmul256_resident(parse_count(argc > 2 ? argv[2] : NULL, 5));
    }
    if (argc > 1 && strcmp(argv[1], "--bench-opencl-vector-add") == 0) {
        return bench_opencl_vector_add(parse_count(argc > 2 ? argv[2] : NULL, 5));
    }
    if (argc > 1 && strcmp(argv[1], "--bench-noop") == 0) {
        return bench_noop(parse_count(argc > 2 ? argv[2] : NULL, 5));
    }
    return run_gpu_once();
}

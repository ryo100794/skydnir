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

#endif

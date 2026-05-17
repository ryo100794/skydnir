#!/usr/bin/env bash
# Verify the glibc-facing Vulkan ICD can submit a minimal compute-style
# workload through the pdocker GPU executor command queue.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXECUTOR="$ROOT/app/src/main/jniLibs/arm64-v8a/libpdockergpuexecutor.so"
ICD="$ROOT/docker-proot-setup/lib/pdocker-vulkan-icd.so"
TMP="$(mktemp -d)"
SOCK="${TMP}/pdocker-gpu.sock"
trap '[[ -n "${PID:-}" ]] && kill "$PID" 2>/dev/null || true; rm -rf "$TMP"' EXIT

cat >"$TMP/pdocker-vk-smoke.c" <<'C'
#include <vulkan/vulkan.h>
#include <math.h>
#include <stdio.h>
#include <string.h>
#define CHECK(x, msg) do { VkResult _r = (x); if (_r != VK_SUCCESS) { fprintf(stderr, "%s: %d\n", msg, _r); return 2; } } while (0)
static uint32_t memory_type(VkPhysicalDevice phys, uint32_t type_bits, VkMemoryPropertyFlags required) {
    VkPhysicalDeviceMemoryProperties props;
    vkGetPhysicalDeviceMemoryProperties(phys, &props);
    for (uint32_t i = 0; i < props.memoryTypeCount; ++i) {
        if ((type_bits & (1u << i)) && (props.memoryTypes[i].propertyFlags & required) == required) return i;
    }
    fprintf(stderr, "no memory type for bits=0x%x flags=0x%x\n", type_bits, required);
    return UINT32_MAX;
}
int main(void) {
    VkApplicationInfo app = {.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO, .pApplicationName = "pdocker-vulkan-smoke", .apiVersion = VK_API_VERSION_1_1};
    VkInstanceCreateInfo ici = {.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, .pApplicationInfo = &app};
    VkInstance inst; CHECK(vkCreateInstance(&ici, NULL, &inst), "vkCreateInstance");
    uint32_t count = 1; VkPhysicalDevice phys; CHECK(vkEnumeratePhysicalDevices(inst, &count, &phys), "vkEnumeratePhysicalDevices");
    VkPhysicalDeviceProperties props; vkGetPhysicalDeviceProperties(phys, &props);
    printf("device=%s type=%u\n", props.deviceName, props.deviceType);
    float prio = 1.0f;
    VkDeviceQueueCreateInfo qci = {.sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO, .queueFamilyIndex = 0, .queueCount = 1, .pQueuePriorities = &prio};
    VkDeviceCreateInfo dci = {.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, .queueCreateInfoCount = 1, .pQueueCreateInfos = &qci};
    VkDevice dev; CHECK(vkCreateDevice(phys, &dci, NULL, &dev), "vkCreateDevice");
    const uint32_t n = 1024;
    VkBuffer bufs[3]; VkDeviceMemory mems[3]; void *maps[3];
    for (int i = 0; i < 3; ++i) {
        VkBufferCreateInfo bci = {.sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO, .size = n * sizeof(float), .usage = VK_BUFFER_USAGE_STORAGE_BUFFER_BIT, .sharingMode = VK_SHARING_MODE_EXCLUSIVE};
        CHECK(vkCreateBuffer(dev, &bci, NULL, &bufs[i]), "vkCreateBuffer");
        VkMemoryRequirements req; vkGetBufferMemoryRequirements(dev, bufs[i], &req);
        uint32_t type = memory_type(phys, req.memoryTypeBits, VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT);
        if (type == UINT32_MAX) return 3;
        VkMemoryAllocateInfo mai = {.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, .allocationSize = req.size, .memoryTypeIndex = type};
        CHECK(vkAllocateMemory(dev, &mai, NULL, &mems[i]), "vkAllocateMemory");
        CHECK(vkBindBufferMemory(dev, bufs[i], mems[i], 0), "vkBindBufferMemory");
        CHECK(vkMapMemory(dev, mems[i], 0, n * sizeof(float), 0, &maps[i]), "vkMapMemory");
    }
    float *a = maps[0], *b = maps[1], *out = maps[2];
    for (uint32_t i = 0; i < n; ++i) { a[i] = (float)i * 0.25f; b[i] = 1.0f - (float)i * 0.125f; out[i] = 0.0f; }
    VkDescriptorSetLayoutBinding bindings[3]; memset(bindings, 0, sizeof(bindings));
    for (int i = 0; i < 3; ++i) { bindings[i].binding = i; bindings[i].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER; bindings[i].descriptorCount = 1; bindings[i].stageFlags = VK_SHADER_STAGE_COMPUTE_BIT; }
    VkDescriptorSetLayoutCreateInfo slci = {.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO, .bindingCount = 3, .pBindings = bindings};
    VkDescriptorSetLayout set_layout; CHECK(vkCreateDescriptorSetLayout(dev, &slci, NULL, &set_layout), "vkCreateDescriptorSetLayout");
    VkPipelineLayoutCreateInfo plci = {.sType = VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO, .setLayoutCount = 1, .pSetLayouts = &set_layout};
    VkPipelineLayout pipeline_layout; CHECK(vkCreatePipelineLayout(dev, &plci, NULL, &pipeline_layout), "vkCreatePipelineLayout");
    VkDescriptorPoolSize pool_size = {.type = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, .descriptorCount = 3};
    VkDescriptorPoolCreateInfo dpci = {.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO, .maxSets = 1, .poolSizeCount = 1, .pPoolSizes = &pool_size};
    VkDescriptorPool pool; CHECK(vkCreateDescriptorPool(dev, &dpci, NULL, &pool), "vkCreateDescriptorPool");
    VkDescriptorSetAllocateInfo dsai = {.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO, .descriptorPool = pool, .descriptorSetCount = 1, .pSetLayouts = &set_layout};
    VkDescriptorSet set; CHECK(vkAllocateDescriptorSets(dev, &dsai, &set), "vkAllocateDescriptorSets");
    VkDescriptorBufferInfo infos[3]; VkWriteDescriptorSet writes[3]; memset(infos, 0, sizeof(infos)); memset(writes, 0, sizeof(writes));
    for (int i = 0; i < 3; ++i) {
        infos[i].buffer = bufs[i]; infos[i].offset = 0; infos[i].range = n * sizeof(float);
        writes[i].sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET; writes[i].dstSet = set; writes[i].dstBinding = i; writes[i].descriptorCount = 1; writes[i].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER; writes[i].pBufferInfo = &infos[i];
    }
    vkUpdateDescriptorSets(dev, 3, writes, 0, NULL);
    static const uint32_t vector_add_spv[] = {
#include "pdocker-vector-add-spv.inc"
    };
    VkShaderModuleCreateInfo smci = {.sType = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO, .codeSize = sizeof(vector_add_spv), .pCode = vector_add_spv};
    VkShaderModule shader; CHECK(vkCreateShaderModule(dev, &smci, NULL, &shader), "vkCreateShaderModule");
    VkComputePipelineCreateInfo cpci = {.sType = VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO, .stage = {.sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO, .stage = VK_SHADER_STAGE_COMPUTE_BIT, .module = shader, .pName = "main"}, .layout = pipeline_layout};
    VkPipeline pipeline; CHECK(vkCreateComputePipelines(dev, VK_NULL_HANDLE, 1, &cpci, NULL, &pipeline), "vkCreateComputePipelines");
    VkCommandPoolCreateInfo cpoci = {.sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO, .queueFamilyIndex = 0};
    VkCommandPool command_pool; CHECK(vkCreateCommandPool(dev, &cpoci, NULL, &command_pool), "vkCreateCommandPool");
    VkCommandBufferAllocateInfo cbai = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO, .commandPool = command_pool, .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY, .commandBufferCount = 1};
    VkCommandBuffer cb; CHECK(vkAllocateCommandBuffers(dev, &cbai, &cb), "vkAllocateCommandBuffers");
    VkCommandBufferBeginInfo cbi = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
    CHECK(vkBeginCommandBuffer(cb, &cbi), "vkBeginCommandBuffer");
    vkCmdBindPipeline(cb, VK_PIPELINE_BIND_POINT_COMPUTE, pipeline);
    vkCmdBindDescriptorSets(cb, VK_PIPELINE_BIND_POINT_COMPUTE, pipeline_layout, 0, 1, &set, 0, NULL);
    vkCmdDispatch(cb, (n + 127) / 128, 1, 1);
    CHECK(vkEndCommandBuffer(cb), "vkEndCommandBuffer");
    VkQueue queue; vkGetDeviceQueue(dev, 0, 0, &queue);
    VkSubmitInfo si = {.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO, .commandBufferCount = 1, .pCommandBuffers = &cb};
    CHECK(vkQueueSubmit(queue, 1, &si, VK_NULL_HANDLE), "vkQueueSubmit");
    double max_err = 0.0;
    for (uint32_t i = 0; i < n; ++i) { double e = fabs((double)out[i] - (double)(a[i] + b[i])); if (e > max_err) max_err = e; }
    printf("maxErr=%.8f out0=%.3f outLast=%.3f\n", max_err, out[0], out[n - 1]);
    return max_err <= 0.0001 ? 0 : 11;
}
C

python3 - "$ROOT/app/src/main/cpp/pdocker_gpu_executor.c" "$TMP/pdocker-vector-add-spv.inc" <<'PY'
import re
import sys
from pathlib import Path

source = Path(sys.argv[1]).read_text()
match = re.search(r"static const uint32_t kVectorAddSpv\[\] = \{(?P<body>.*?)\n\};", source, re.S)
if not match:
    raise SystemExit("kVectorAddSpv not found")
Path(sys.argv[2]).write_text(match.group("body").strip() + "\n")
PY
gcc "$TMP/pdocker-vk-smoke.c" -o "$TMP/pdocker-vk-smoke" -lvulkan
cat >"$TMP/pdocker_icd.json" <<JSON
{"file_format_version":"1.0.0","ICD":{"library_path":"$ICD","api_version":"1.2.0"}}
JSON

if ! timeout 30 "$EXECUTOR" --bench-vulkan-vector-add 1 >"$TMP/executor-preflight.log" 2>&1; then
    echo "smoke-vulkan-icd-bridge: SKIP executor Vulkan preflight unavailable" >&2
    cat "$TMP/executor-preflight.log" >&2
    exit 0
fi

"$EXECUTOR" --serve-socket "$SOCK" >"$TMP/executor.log" 2>&1 &
PID=$!
for _ in $(seq 1 100); do
    [[ -S "$SOCK" ]] && break
    sleep 0.05
done
[[ -S "$SOCK" ]] || { cat "$TMP/executor.log" >&2; exit 1; }

VK_ICD_FILENAMES="$TMP/pdocker_icd.json" \
PDOCKER_GPU_QUEUE_SOCKET="$SOCK" \
timeout 30 "$TMP/pdocker-vk-smoke" || {
    rc=$?
    cat "$TMP/executor.log" >&2
    exit "$rc"
}

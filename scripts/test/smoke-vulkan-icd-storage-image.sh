#!/usr/bin/env bash
# Verify the glibc-facing Vulkan ICD can transport a storage-image compute
# workload through the pdocker GPU executor command queue and read the image
# result back through a recorded vkCmdCopyImageToBuffer command.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
EXECUTOR="$ROOT/app/src/main/jniLibs/arm64-v8a/libpdockergpuexecutor.so"
ICD="$ROOT/docker-proot-setup/lib/pdocker-vulkan-icd.so"
TMP="$(mktemp -d)"
EXTERNAL_SOCK="${PDOCKER_GPU_QUEUE_SOCKET:-}"
if [[ -n "$EXTERNAL_SOCK" ]]; then
    SOCK="$EXTERNAL_SOCK"
else
    SOCK="${TMP}/pdocker-gpu.sock"
fi
trap '[[ -n "${PID:-}" ]] && kill "$PID" 2>/dev/null || true; rm -rf "$TMP"' EXIT

cat >"$TMP/pdocker-vk-storage-image-smoke.c" <<'C'
#include <vulkan/vulkan.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define CHECK(x, msg) do { VkResult _r = (x); if (_r != VK_SUCCESS) { fprintf(stderr, "%s: %d\n", msg, _r); return 2; } } while (0)

static uint32_t memory_type(VkPhysicalDevice phys, uint32_t type_bits, VkMemoryPropertyFlags required) {
    VkPhysicalDeviceMemoryProperties props;
    vkGetPhysicalDeviceMemoryProperties(phys, &props);
    for (uint32_t i = 0; i < props.memoryTypeCount; ++i) {
        if ((type_bits & (1u << i)) && (props.memoryTypes[i].propertyFlags & required) == required) return i;
    }
    for (uint32_t i = 0; i < props.memoryTypeCount; ++i) {
        if ((type_bits & (1u << i)) && (props.memoryTypes[i].propertyFlags & VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT)) return i;
    }
    fprintf(stderr, "no memory type for bits=0x%x flags=0x%x\n", type_bits, required);
    return UINT32_MAX;
}

int main(void) {
    const uint32_t width = 16;
    const uint32_t height = 16;
    const uint32_t pixel_bytes = 4;
    const VkDeviceSize image_bytes = width * height * pixel_bytes;
    const VkImageUsageFlags image_usage = VK_IMAGE_USAGE_STORAGE_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT;

    VkApplicationInfo app = {
        .sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
        .pApplicationName = "pdocker-vulkan-storage-image-smoke",
        .apiVersion = VK_API_VERSION_1_1,
    };
    VkInstanceCreateInfo ici = {.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, .pApplicationInfo = &app};
    VkInstance inst;
    CHECK(vkCreateInstance(&ici, NULL, &inst), "vkCreateInstance");

    uint32_t count = 1;
    VkPhysicalDevice phys;
    CHECK(vkEnumeratePhysicalDevices(inst, &count, &phys), "vkEnumeratePhysicalDevices");
    VkPhysicalDeviceProperties props;
    vkGetPhysicalDeviceProperties(phys, &props);
    printf("device=%s type=%u\n", props.deviceName, props.deviceType);

    VkFormatProperties format_props;
    vkGetPhysicalDeviceFormatProperties(phys, VK_FORMAT_R8G8B8A8_UNORM, &format_props);
    if ((format_props.optimalTilingFeatures & (VK_FORMAT_FEATURE_STORAGE_IMAGE_BIT | VK_FORMAT_FEATURE_TRANSFER_SRC_BIT)) !=
        (VK_FORMAT_FEATURE_STORAGE_IMAGE_BIT | VK_FORMAT_FEATURE_TRANSFER_SRC_BIT)) {
        fprintf(stderr, "smoke-vulkan-icd-storage-image: SKIP storage image format support unavailable\n");
        return 0;
    }

    float prio = 1.0f;
    VkDeviceQueueCreateInfo qci = {
        .sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,
        .queueFamilyIndex = 0,
        .queueCount = 1,
        .pQueuePriorities = &prio,
    };
    VkDeviceCreateInfo dci = {
        .sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO,
        .queueCreateInfoCount = 1,
        .pQueueCreateInfos = &qci,
    };
    VkDevice dev;
    CHECK(vkCreateDevice(phys, &dci, NULL, &dev), "vkCreateDevice");

    VkBufferCreateInfo rbci = {
        .sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO,
        .size = image_bytes,
        .usage = VK_BUFFER_USAGE_TRANSFER_DST_BIT,
        .sharingMode = VK_SHARING_MODE_EXCLUSIVE,
    };
    VkBuffer readback;
    CHECK(vkCreateBuffer(dev, &rbci, NULL, &readback), "vkCreateBuffer(readback)");
    VkMemoryRequirements rbreq;
    vkGetBufferMemoryRequirements(dev, readback, &rbreq);
    uint32_t rbtype = memory_type(phys, rbreq.memoryTypeBits, VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT);
    if (rbtype == UINT32_MAX) return 3;
    VkMemoryAllocateInfo rbmai = {
        .sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
        .allocationSize = rbreq.size,
        .memoryTypeIndex = rbtype,
    };
    VkDeviceMemory rbmem;
    CHECK(vkAllocateMemory(dev, &rbmai, NULL, &rbmem), "vkAllocateMemory(readback)");
    CHECK(vkBindBufferMemory(dev, readback, rbmem, 0), "vkBindBufferMemory(readback)");
    void *mapped = NULL;
    CHECK(vkMapMemory(dev, rbmem, 0, image_bytes, 0, &mapped), "vkMapMemory(readback)");
    memset(mapped, 0, (size_t)image_bytes);

    VkImageCreateInfo ici2 = {
        .sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO,
        .imageType = VK_IMAGE_TYPE_2D,
        .format = VK_FORMAT_R8G8B8A8_UNORM,
        .extent = {.width = width, .height = height, .depth = 1},
        .mipLevels = 1,
        .arrayLayers = 1,
        .samples = VK_SAMPLE_COUNT_1_BIT,
        .tiling = VK_IMAGE_TILING_OPTIMAL,
        .usage = image_usage,
        .sharingMode = VK_SHARING_MODE_EXCLUSIVE,
        .initialLayout = VK_IMAGE_LAYOUT_UNDEFINED,
    };
    VkImage image;
    CHECK(vkCreateImage(dev, &ici2, NULL, &image), "vkCreateImage");
    VkMemoryRequirements ireq;
    vkGetImageMemoryRequirements(dev, image, &ireq);
    uint32_t itype = memory_type(phys, ireq.memoryTypeBits, VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT);
    if (itype == UINT32_MAX) return 3;
    VkMemoryAllocateInfo imai = {
        .sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
        .allocationSize = ireq.size,
        .memoryTypeIndex = itype,
    };
    VkDeviceMemory imem;
    CHECK(vkAllocateMemory(dev, &imai, NULL, &imem), "vkAllocateMemory(image)");
    CHECK(vkBindImageMemory(dev, image, imem, 0), "vkBindImageMemory");

    VkImageViewCreateInfo ivci = {
        .sType = VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO,
        .image = image,
        .viewType = VK_IMAGE_VIEW_TYPE_2D,
        .format = VK_FORMAT_R8G8B8A8_UNORM,
        .subresourceRange = {
            .aspectMask = VK_IMAGE_ASPECT_COLOR_BIT,
            .baseMipLevel = 0,
            .levelCount = 1,
            .baseArrayLayer = 0,
            .layerCount = 1,
        },
    };
    VkImageView image_view;
    CHECK(vkCreateImageView(dev, &ivci, NULL, &image_view), "vkCreateImageView");

    VkDescriptorSetLayoutBinding binding = {
        .binding = 0,
        .descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_IMAGE,
        .descriptorCount = 1,
        .stageFlags = VK_SHADER_STAGE_COMPUTE_BIT,
    };
    VkDescriptorSetLayoutCreateInfo slci = {
        .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO,
        .bindingCount = 1,
        .pBindings = &binding,
    };
    VkDescriptorSetLayout set_layout;
    CHECK(vkCreateDescriptorSetLayout(dev, &slci, NULL, &set_layout), "vkCreateDescriptorSetLayout");
    VkPipelineLayoutCreateInfo plci = {
        .sType = VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO,
        .setLayoutCount = 1,
        .pSetLayouts = &set_layout,
    };
    VkPipelineLayout pipeline_layout;
    CHECK(vkCreatePipelineLayout(dev, &plci, NULL, &pipeline_layout), "vkCreatePipelineLayout");
    VkDescriptorPoolSize pool_size = {.type = VK_DESCRIPTOR_TYPE_STORAGE_IMAGE, .descriptorCount = 1};
    VkDescriptorPoolCreateInfo dpci = {
        .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO,
        .maxSets = 1,
        .poolSizeCount = 1,
        .pPoolSizes = &pool_size,
    };
    VkDescriptorPool pool;
    CHECK(vkCreateDescriptorPool(dev, &dpci, NULL, &pool), "vkCreateDescriptorPool");
    VkDescriptorSetAllocateInfo dsai = {
        .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO,
        .descriptorPool = pool,
        .descriptorSetCount = 1,
        .pSetLayouts = &set_layout,
    };
    VkDescriptorSet set;
    CHECK(vkAllocateDescriptorSets(dev, &dsai, &set), "vkAllocateDescriptorSets");
    VkDescriptorImageInfo image_info = {
        .sampler = VK_NULL_HANDLE,
        .imageView = image_view,
        .imageLayout = VK_IMAGE_LAYOUT_GENERAL,
    };
    VkWriteDescriptorSet write = {
        .sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET,
        .dstSet = set,
        .dstBinding = 0,
        .descriptorCount = 1,
        .descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_IMAGE,
        .pImageInfo = &image_info,
    };
    vkUpdateDescriptorSets(dev, 1, &write, 0, NULL);

    static const uint32_t storage_image_roundtrip_spv[] = {
#include "pdocker-storage-image-roundtrip-spv.inc"
    };
    VkShaderModuleCreateInfo smci = {
        .sType = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO,
        .codeSize = sizeof(storage_image_roundtrip_spv),
        .pCode = storage_image_roundtrip_spv,
    };
    VkShaderModule shader;
    CHECK(vkCreateShaderModule(dev, &smci, NULL, &shader), "vkCreateShaderModule");
    VkComputePipelineCreateInfo cpci = {
        .sType = VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO,
        .stage = {
            .sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO,
            .stage = VK_SHADER_STAGE_COMPUTE_BIT,
            .module = shader,
            .pName = "main",
        },
        .layout = pipeline_layout,
    };
    VkPipeline pipeline;
    CHECK(vkCreateComputePipelines(dev, VK_NULL_HANDLE, 1, &cpci, NULL, &pipeline), "vkCreateComputePipelines");

    VkCommandPoolCreateInfo cpoci = {
        .sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO,
        .queueFamilyIndex = 0,
    };
    VkCommandPool command_pool;
    CHECK(vkCreateCommandPool(dev, &cpoci, NULL, &command_pool), "vkCreateCommandPool");
    VkCommandBufferAllocateInfo cbai = {
        .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,
        .commandPool = command_pool,
        .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY,
        .commandBufferCount = 1,
    };
    VkCommandBuffer cb;
    CHECK(vkAllocateCommandBuffers(dev, &cbai, &cb), "vkAllocateCommandBuffers");
    VkCommandBufferBeginInfo cbi = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
    CHECK(vkBeginCommandBuffer(cb, &cbi), "vkBeginCommandBuffer");
    VkImageMemoryBarrier to_general = {
        .sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER,
        .srcAccessMask = 0,
        .dstAccessMask = VK_ACCESS_SHADER_WRITE_BIT,
        .oldLayout = VK_IMAGE_LAYOUT_UNDEFINED,
        .newLayout = VK_IMAGE_LAYOUT_GENERAL,
        .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
        .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
        .image = image,
        .subresourceRange = {
            .aspectMask = VK_IMAGE_ASPECT_COLOR_BIT,
            .baseMipLevel = 0,
            .levelCount = 1,
            .baseArrayLayer = 0,
            .layerCount = 1,
        },
    };
    vkCmdPipelineBarrier(cb, VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, 0, 0, NULL, 0, NULL, 1, &to_general);
    vkCmdBindPipeline(cb, VK_PIPELINE_BIND_POINT_COMPUTE, pipeline);
    vkCmdBindDescriptorSets(cb, VK_PIPELINE_BIND_POINT_COMPUTE, pipeline_layout, 0, 1, &set, 0, NULL);
    vkCmdDispatch(cb, (width + 7) / 8, (height + 7) / 8, 1);
    VkImageMemoryBarrier to_transfer = to_general;
    to_transfer.srcAccessMask = VK_ACCESS_SHADER_WRITE_BIT;
    to_transfer.dstAccessMask = VK_ACCESS_TRANSFER_READ_BIT;
    to_transfer.oldLayout = VK_IMAGE_LAYOUT_GENERAL;
    to_transfer.newLayout = VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL;
    vkCmdPipelineBarrier(cb, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT, 0, 0, NULL, 0, NULL, 1, &to_transfer);
    VkBufferImageCopy copy = {
        .bufferOffset = 0,
        .bufferRowLength = 0,
        .bufferImageHeight = 0,
        .imageSubresource = {
            .aspectMask = VK_IMAGE_ASPECT_COLOR_BIT,
            .mipLevel = 0,
            .baseArrayLayer = 0,
            .layerCount = 1,
        },
        .imageOffset = {.x = 0, .y = 0, .z = 0},
        .imageExtent = {.width = width, .height = height, .depth = 1},
    };
    vkCmdCopyImageToBuffer(cb, image, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, readback, 1, &copy);
    CHECK(vkEndCommandBuffer(cb), "vkEndCommandBuffer");

    VkQueue queue;
    vkGetDeviceQueue(dev, 0, 0, &queue);
    VkSubmitInfo si = {.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO, .commandBufferCount = 1, .pCommandBuffers = &cb};
    CHECK(vkQueueSubmit(queue, 1, &si, VK_NULL_HANDLE), "vkQueueSubmit");
    CHECK(vkQueueWaitIdle(queue), "vkQueueWaitIdle");

    const uint8_t *bytes = (const uint8_t *)mapped;
    uint32_t max_err = 0;
    for (uint32_t y = 0; y < height; ++y) {
        for (uint32_t x = 0; x < width; ++x) {
            const size_t off = ((size_t)y * width + x) * pixel_bytes;
            const uint8_t expected[4] = {(uint8_t)x, (uint8_t)y, (uint8_t)(x ^ y), 255};
            for (uint32_t c = 0; c < 4; ++c) {
                uint32_t got = bytes[off + c];
                uint32_t want = expected[c];
                uint32_t err = got > want ? got - want : want - got;
                if (err > max_err) max_err = err;
            }
        }
    }
    printf("storageImageMaxErr=%u first=%u,%u,%u,%u last=%u,%u,%u,%u\n",
           max_err,
           bytes[0], bytes[1], bytes[2], bytes[3],
           bytes[image_bytes - 4], bytes[image_bytes - 3], bytes[image_bytes - 2], bytes[image_bytes - 1]);
    return max_err <= 1 ? 0 : 11;
}
C

python3 - "$ROOT/app/src/main/cpp/pdocker_gpu_executor.c" "$TMP/pdocker-storage-image-roundtrip-spv.inc" <<'PY'
import re
import sys
from pathlib import Path

source = Path(sys.argv[1]).read_text()
match = re.search(r"static const uint32_t kStorageImageRoundtripSpv\[\] = \{(?P<body>.*?)\n\};", source, re.S)
if not match:
    raise SystemExit("kStorageImageRoundtripSpv not found")
Path(sys.argv[2]).write_text(match.group("body").strip() + "\n")
PY

gcc "$TMP/pdocker-vk-storage-image-smoke.c" -o "$TMP/pdocker-vk-storage-image-smoke" -lvulkan
cat >"$TMP/pdocker_icd.json" <<JSON
{"file_format_version":"1.0.0","ICD":{"library_path":"$ICD","api_version":"1.2.0"}}
JSON

if [[ -n "$EXTERNAL_SOCK" ]]; then
    [[ -S "$SOCK" ]] || {
        echo "smoke-vulkan-icd-storage-image: external PDOCKER_GPU_QUEUE_SOCKET is not a socket: $SOCK" >&2
        exit 1
    }
else
    if ! timeout 30 "$EXECUTOR" --bench-vulkan-storage-image-roundtrip >"$TMP/executor-preflight.log" 2>&1; then
        echo "smoke-vulkan-icd-storage-image: SKIP executor Vulkan storage-image preflight unavailable" >&2
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
fi

VK_ICD_FILENAMES="$TMP/pdocker_icd.json" \
PDOCKER_GPU_QUEUE_SOCKET="$SOCK" \
timeout 30 "$TMP/pdocker-vk-storage-image-smoke" || {
    rc=$?
    [[ -n "$EXTERNAL_SOCK" ]] || cat "$TMP/executor.log" >&2
    exit "$rc"
}

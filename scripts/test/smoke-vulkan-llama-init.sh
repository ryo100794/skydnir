#!/usr/bin/env bash
# Verify the pdocker Vulkan ICD exposes the baseline queried by llama.cpp's
# ggml-vulkan device initialization before real shader lowering is enabled.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ICD="$ROOT/docker-proot-setup/lib/pdocker-vulkan-icd.so"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

cat >"$TMP/pdocker-vulkan-llama-init.c" <<'C'
#include <vulkan/vulkan.h>
#include <stdio.h>
#include <string.h>

#define CHECK(x, msg) do { VkResult _r = (x); if (_r != VK_SUCCESS) { fprintf(stderr, "%s: %d\n", msg, _r); return 2; } } while (0)

static int has_ext(const char *name, const VkExtensionProperties *props, uint32_t count) {
    for (uint32_t i = 0; i < count; ++i) {
        if (strcmp(name, props[i].extensionName) == 0) return 1;
    }
    return 0;
}

int main(void) {
    uint32_t api = 0;
    CHECK(vkEnumerateInstanceVersion(&api), "vkEnumerateInstanceVersion");
    if (api < VK_API_VERSION_1_2) {
        fprintf(stderr, "Vulkan 1.2 required, got %u.%u\n", VK_API_VERSION_MAJOR(api), VK_API_VERSION_MINOR(api));
        return 3;
    }

    VkApplicationInfo app = {
        .sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
        .pApplicationName = "pdocker-vulkan-llama-init",
        .apiVersion = VK_API_VERSION_1_2,
    };
    VkInstanceCreateInfo ici = {
        .sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
        .pApplicationInfo = &app,
    };
    VkInstance inst = VK_NULL_HANDLE;
    CHECK(vkCreateInstance(&ici, NULL, &inst), "vkCreateInstance");

    uint32_t physical_count = 0;
    CHECK(vkEnumeratePhysicalDevices(inst, &physical_count, NULL), "vkEnumeratePhysicalDevices count");
    if (physical_count < 1) return 4;
    VkPhysicalDevice phys = VK_NULL_HANDLE;
    physical_count = 1;
    CHECK(vkEnumeratePhysicalDevices(inst, &physical_count, &phys), "vkEnumeratePhysicalDevices");

    uint32_t ext_count = 0;
    CHECK(vkEnumerateDeviceExtensionProperties(phys, NULL, &ext_count, NULL), "vkEnumerateDeviceExtensionProperties count");
    VkExtensionProperties exts[16];
    if (ext_count > 16) ext_count = 16;
    CHECK(vkEnumerateDeviceExtensionProperties(phys, NULL, &ext_count, exts), "vkEnumerateDeviceExtensionProperties");
    if (!has_ext(VK_KHR_16BIT_STORAGE_EXTENSION_NAME, exts, ext_count)) {
        fprintf(stderr, "missing %s\n", VK_KHR_16BIT_STORAGE_EXTENSION_NAME);
        return 5;
    }

    VkPhysicalDeviceMaintenance3Properties maint3 = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MAINTENANCE_3_PROPERTIES,
    };
    VkPhysicalDeviceSubgroupProperties subgroup = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SUBGROUP_PROPERTIES,
        .pNext = &maint3,
    };
    VkPhysicalDeviceDriverProperties driver = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DRIVER_PROPERTIES,
        .pNext = &subgroup,
    };
    VkPhysicalDeviceVulkan11Properties vk11_props = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_1_PROPERTIES,
        .pNext = &driver,
    };
    VkPhysicalDeviceVulkan12Properties vk12_props = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_PROPERTIES,
        .pNext = &vk11_props,
    };
    VkPhysicalDeviceProperties2 props2 = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2,
        .pNext = &vk12_props,
    };
    vkGetPhysicalDeviceProperties2(phys, &props2);
    if (props2.properties.apiVersion < VK_API_VERSION_1_2) return 6;
    if (subgroup.subgroupSize == 0 || !(subgroup.supportedStages & VK_SHADER_STAGE_COMPUTE_BIT)) return 7;
    if (subgroup.supportedOperations != VK_SUBGROUP_FEATURE_BASIC_BIT) {
        fprintf(stderr, "subgroup ops must default to BASIC only, got 0x%x\n", subgroup.supportedOperations);
        return 11;
    }
    if (maint3.maxMemoryAllocationSize == 0) return 8;

    VkPhysicalDeviceVulkan11Features vk11_features = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_1_FEATURES,
    };
    VkPhysicalDeviceVulkan12Features vk12_features = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES,
        .pNext = &vk11_features,
    };
    VkPhysicalDeviceFeatures2 features2 = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2,
        .pNext = &vk12_features,
    };
    vkGetPhysicalDeviceFeatures2(phys, &features2);
    if (features2.features.shaderInt64) {
        fprintf(stderr, "shaderInt64 must be opt-in for the pdocker bridge\n");
        return 12;
    }
    if (!vk11_features.storageBuffer16BitAccess) {
        fprintf(stderr, "storageBuffer16BitAccess missing\n");
        return 9;
    }

    uint32_t queue_family_count = 0;
    vkGetPhysicalDeviceQueueFamilyProperties(phys, &queue_family_count, NULL);
    if (queue_family_count < 1) return 13;
    VkQueueFamilyProperties queue_families[8];
    uint32_t queue_family_capacity = queue_family_count > 8 ? 8 : queue_family_count;
    vkGetPhysicalDeviceQueueFamilyProperties(phys, &queue_family_capacity, queue_families);
    uint32_t queue_family_index = UINT32_MAX;
    uint32_t queue_count = 0;
    for (uint32_t i = 0; i < queue_family_capacity; ++i) {
        if ((queue_families[i].queueFlags & VK_QUEUE_COMPUTE_BIT) && queue_families[i].queueCount > 0) {
            queue_family_index = i;
            queue_count = queue_families[i].queueCount;
            break;
        }
    }
    if (queue_family_index == UINT32_MAX || queue_count == 0) return 14;

    float priorities[1] = { 1.0f };
    VkDeviceQueueCreateInfo qci = {
        .sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,
        .queueFamilyIndex = queue_family_index,
        .queueCount = 1,
        .pQueuePriorities = priorities,
    };
    const char *device_exts[] = { VK_KHR_16BIT_STORAGE_EXTENSION_NAME };
    VkPhysicalDevice selected_devices[] = { phys };
    VkDeviceGroupDeviceCreateInfo group_dci = {
        .sType = VK_STRUCTURE_TYPE_DEVICE_GROUP_DEVICE_CREATE_INFO,
        .pNext = &features2,
        .physicalDeviceCount = 1,
        .pPhysicalDevices = selected_devices,
    };
    VkDeviceCreateInfo dci = {
        .sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO,
        .pNext = &group_dci,
        .queueCreateInfoCount = 1,
        .pQueueCreateInfos = &qci,
        .enabledExtensionCount = 1,
        .ppEnabledExtensionNames = device_exts,
    };
    VkDevice dev = VK_NULL_HANDLE;
    CHECK(vkCreateDevice(phys, &dci, NULL, &dev), "vkCreateDevice");
    VkQueue compute = VK_NULL_HANDLE;
    VkQueue transfer = VK_NULL_HANDLE;
    vkGetDeviceQueue(dev, queue_family_index, 0, &compute);
    transfer = compute;
    if (!compute || !transfer) return 10;

    printf("api=%u.%u device=%s ext16=1 subgroup=%u maxAlloc=%llu\n",
           VK_API_VERSION_MAJOR(props2.properties.apiVersion),
           VK_API_VERSION_MINOR(props2.properties.apiVersion),
           props2.properties.deviceName,
           subgroup.subgroupSize,
           (unsigned long long)maint3.maxMemoryAllocationSize);
    vkDestroyDevice(dev, NULL);
    vkDestroyInstance(inst, NULL);
    return 0;
}
C

gcc "$TMP/pdocker-vulkan-llama-init.c" -o "$TMP/pdocker-vulkan-llama-init" -lvulkan
cat >"$TMP/pdocker_icd.json" <<JSON
{"file_format_version":"1.0.0","ICD":{"library_path":"$ICD","api_version":"1.2.0"}}
JSON

VK_ICD_FILENAMES="$TMP/pdocker_icd.json" "$TMP/pdocker-vulkan-llama-init"

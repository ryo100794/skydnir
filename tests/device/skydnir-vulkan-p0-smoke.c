/*
 * Standalone Skydnir Vulkan P0 device smoke test.
 *
 * This program deliberately uses only the public Vulkan API.  It has no
 * knowledge of llama.cpp or of Skydnir's private transport protocol.  On the
 * Skydnir ICD, the query-pool calls below must therefore traverse the same
 * executor-backed path that an ordinary Vulkan application uses.
 *
 * stdout contains exactly one JSON object.  Diagnostics from the Vulkan
 * loader/driver may still be written to stderr.  Every advertised path is
 * fail-closed: an advertised capability that cannot be enabled or exercised
 * makes the process return nonzero.
 */

#include <vulkan/vulkan.h>

#include <inttypes.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define ARRAY_LEN(a) ((uint32_t)(sizeof(a) / sizeof((a)[0])))
#define NO_QUEUE_FAMILY UINT32_MAX

struct optional_path {
    bool advertised;
    bool unsupported;
    bool skipped;
    bool attempted;
    bool passed;
    VkResult result;
};

struct smoke_report {
    bool ok;
    const char *error_step;
    VkResult error_result;

    uint32_t loader_api_version;
    uint32_t instance_api_version;
    bool instance_created;
    uint32_t physical_device_count;
    uint32_t selected_physical_device;
    uint32_t physical_api_version;
    uint32_t vendor_id;
    uint32_t device_id;
    uint32_t queue_family;
    uint32_t present_queue_family;
    uint32_t timestamp_valid_bits;
    bool device_created;

    bool khr_surface_advertised;
    bool ext_headless_surface_advertised;
    bool khr_swapchain_advertised;
    bool khr_synchronization2_advertised;
    bool synchronization2_core;
    bool synchronization2_feature;
    bool ext_host_query_reset_advertised;
    bool host_query_reset_core;
    bool host_query_reset_feature;
    bool host_query_reset_advertised;
    bool host_query_reset_executed;
    bool host_query_reset_passed;

    struct optional_path headless;
    struct optional_path synchronization2;
    struct optional_path wsi;

    bool query_attempted;
    bool query_pool_created;
    bool host_query_pool_created;
    bool query_reset_recorded;
    bool timestamp_recorded;
    bool query_submitted;
    bool query_results_attempted;
    bool host_query_results_attempted;
    bool query_passed;
    VkResult query_result;
    VkResult host_query_result;
    uint64_t timestamp_value;
    uint64_t timestamp_available;
    uint64_t host_timestamp_value;
    uint64_t host_timestamp_available;

    bool queue_idle_attempted;
    bool queue_idle_passed;
    VkResult queue_idle_result;
    bool device_idle_attempted;
    bool device_idle_passed;
    VkResult device_idle_result;

    bool swapchain_created;
    uint32_t swapchain_image_count;
    bool swapchain_acquired;
    bool swapchain_presented;
    bool swapchain_destroyed;
    bool surface_destroyed;
};

struct candidate {
    VkPhysicalDevice physical;
    VkPhysicalDeviceProperties properties;
    uint32_t command_family;
    uint32_t present_family;
    uint32_t timestamp_valid_bits;
    bool swapchain_extension;
    bool synchronization2_extension;
    bool synchronization2_core;
    bool synchronization2_route;
    bool synchronization2_feature;
    bool host_query_reset_extension;
    bool host_query_reset_core;
    bool host_query_reset_route;
    bool host_query_reset_feature;
};

static const char *json_bool(bool value) {
    return value ? "true" : "false";
}

static void optional_path_set_advertised(struct optional_path *path, bool advertised) {
    memset(path, 0, sizeof(*path));
    path->advertised = advertised;
    path->unsupported = !advertised;
    path->skipped = !advertised;
    path->result = advertised ? VK_SUCCESS : VK_ERROR_EXTENSION_NOT_PRESENT;
}

static void record_failure(struct smoke_report *report, const char *step, VkResult result) {
    if (report->error_step == NULL) {
        report->error_step = step;
        report->error_result = result;
    }
}

static bool extension_present(
        const VkExtensionProperties *properties,
        uint32_t count,
        const char *name) {
    for (uint32_t i = 0; i < count; ++i) {
        if (strcmp(properties[i].extensionName, name) == 0) return true;
    }
    return false;
}

static VkResult enumerate_instance_extensions(
        VkExtensionProperties **out_properties,
        uint32_t *out_count) {
    *out_properties = NULL;
    *out_count = 0;
    for (unsigned int attempt = 0; attempt < 4; ++attempt) {
        uint32_t count = 0;
        VkResult result = vkEnumerateInstanceExtensionProperties(NULL, &count, NULL);
        if (result != VK_SUCCESS) return result;
        if (count == 0) return VK_SUCCESS;
        VkExtensionProperties *properties = calloc(count, sizeof(*properties));
        if (properties == NULL) return VK_ERROR_OUT_OF_HOST_MEMORY;
        uint32_t capacity = count;
        result = vkEnumerateInstanceExtensionProperties(NULL, &capacity, properties);
        if (result == VK_SUCCESS) {
            *out_properties = properties;
            *out_count = capacity;
            return VK_SUCCESS;
        }
        free(properties);
        if (result != VK_INCOMPLETE) return result;
    }
    return VK_INCOMPLETE;
}

static VkResult enumerate_physical_devices(
        VkInstance instance,
        VkPhysicalDevice **out_devices,
        uint32_t *out_count) {
    *out_devices = NULL;
    *out_count = 0;
    for (unsigned int attempt = 0; attempt < 4; ++attempt) {
        uint32_t count = 0;
        VkResult result = vkEnumeratePhysicalDevices(instance, &count, NULL);
        if (result != VK_SUCCESS) return result;
        if (count == 0) return VK_SUCCESS;
        VkPhysicalDevice *devices = calloc(count, sizeof(*devices));
        if (devices == NULL) return VK_ERROR_OUT_OF_HOST_MEMORY;
        uint32_t capacity = count;
        result = vkEnumeratePhysicalDevices(instance, &capacity, devices);
        if (result == VK_SUCCESS) {
            *out_devices = devices;
            *out_count = capacity;
            return VK_SUCCESS;
        }
        free(devices);
        if (result != VK_INCOMPLETE) return result;
    }
    return VK_INCOMPLETE;
}

static VkResult enumerate_device_extensions(
        VkPhysicalDevice physical,
        VkExtensionProperties **out_properties,
        uint32_t *out_count) {
    *out_properties = NULL;
    *out_count = 0;
    for (unsigned int attempt = 0; attempt < 4; ++attempt) {
        uint32_t count = 0;
        VkResult result = vkEnumerateDeviceExtensionProperties(physical, NULL, &count, NULL);
        if (result != VK_SUCCESS) return result;
        if (count == 0) return VK_SUCCESS;
        VkExtensionProperties *properties = calloc(count, sizeof(*properties));
        if (properties == NULL) return VK_ERROR_OUT_OF_HOST_MEMORY;
        uint32_t capacity = count;
        result = vkEnumerateDeviceExtensionProperties(physical, NULL, &capacity, properties);
        if (result == VK_SUCCESS) {
            *out_properties = properties;
            *out_count = capacity;
            return VK_SUCCESS;
        }
        free(properties);
        if (result != VK_INCOMPLETE) return result;
    }
    return VK_INCOMPLETE;
}

static VkResult get_queue_families(
        VkPhysicalDevice physical,
        VkQueueFamilyProperties **out_properties,
        uint32_t *out_count) {
    *out_properties = NULL;
    *out_count = 0;
    uint32_t count = 0;
    vkGetPhysicalDeviceQueueFamilyProperties(physical, &count, NULL);
    if (count == 0) return VK_ERROR_FEATURE_NOT_PRESENT;
    VkQueueFamilyProperties *properties = calloc(count, sizeof(*properties));
    if (properties == NULL) return VK_ERROR_OUT_OF_HOST_MEMORY;
    uint32_t capacity = count;
    vkGetPhysicalDeviceQueueFamilyProperties(physical, &capacity, properties);
    if (capacity == 0) {
        free(properties);
        return VK_ERROR_FEATURE_NOT_PRESENT;
    }
    *out_properties = properties;
    *out_count = capacity;
    return VK_SUCCESS;
}

static VkResult get_surface_formats(
        PFN_vkGetPhysicalDeviceSurfaceFormatsKHR function,
        VkPhysicalDevice physical,
        VkSurfaceKHR surface,
        VkSurfaceFormatKHR **out_formats,
        uint32_t *out_count) {
    *out_formats = NULL;
    *out_count = 0;
    for (unsigned int attempt = 0; attempt < 4; ++attempt) {
        uint32_t count = 0;
        VkResult result = function(physical, surface, &count, NULL);
        if (result != VK_SUCCESS) return result;
        if (count == 0) return VK_ERROR_FORMAT_NOT_SUPPORTED;
        VkSurfaceFormatKHR *formats = calloc(count, sizeof(*formats));
        if (formats == NULL) return VK_ERROR_OUT_OF_HOST_MEMORY;
        uint32_t capacity = count;
        result = function(physical, surface, &capacity, formats);
        if (result == VK_SUCCESS) {
            *out_formats = formats;
            *out_count = capacity;
            return capacity == 0 ? VK_ERROR_FORMAT_NOT_SUPPORTED : VK_SUCCESS;
        }
        free(formats);
        if (result != VK_INCOMPLETE) return result;
    }
    return VK_INCOMPLETE;
}

static VkResult get_present_modes(
        PFN_vkGetPhysicalDeviceSurfacePresentModesKHR function,
        VkPhysicalDevice physical,
        VkSurfaceKHR surface,
        VkPresentModeKHR **out_modes,
        uint32_t *out_count) {
    *out_modes = NULL;
    *out_count = 0;
    for (unsigned int attempt = 0; attempt < 4; ++attempt) {
        uint32_t count = 0;
        VkResult result = function(physical, surface, &count, NULL);
        if (result != VK_SUCCESS) return result;
        if (count == 0) return VK_ERROR_INITIALIZATION_FAILED;
        VkPresentModeKHR *modes = calloc(count, sizeof(*modes));
        if (modes == NULL) return VK_ERROR_OUT_OF_HOST_MEMORY;
        uint32_t capacity = count;
        result = function(physical, surface, &capacity, modes);
        if (result == VK_SUCCESS) {
            *out_modes = modes;
            *out_count = capacity;
            return capacity == 0 ? VK_ERROR_INITIALIZATION_FAILED : VK_SUCCESS;
        }
        free(modes);
        if (result != VK_INCOMPLETE) return result;
    }
    return VK_INCOMPLETE;
}

static uint32_t clamp_u32(uint32_t value, uint32_t minimum, uint32_t maximum) {
    if (value < minimum) return minimum;
    if (value > maximum) return maximum;
    return value;
}

static VkCompositeAlphaFlagBitsKHR choose_composite_alpha(VkCompositeAlphaFlagsKHR supported) {
    static const VkCompositeAlphaFlagBitsKHR choices[] = {
        VK_COMPOSITE_ALPHA_OPAQUE_BIT_KHR,
        VK_COMPOSITE_ALPHA_PRE_MULTIPLIED_BIT_KHR,
        VK_COMPOSITE_ALPHA_POST_MULTIPLIED_BIT_KHR,
        VK_COMPOSITE_ALPHA_INHERIT_BIT_KHR,
    };
    for (uint32_t i = 0; i < ARRAY_LEN(choices); ++i) {
        if ((supported & choices[i]) != 0) return choices[i];
    }
    return (VkCompositeAlphaFlagBitsKHR)0;
}

static VkImageUsageFlags choose_image_usage(VkImageUsageFlags supported) {
    static const VkImageUsageFlags choices[] = {
        VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT,
        VK_IMAGE_USAGE_TRANSFER_DST_BIT,
        VK_IMAGE_USAGE_TRANSFER_SRC_BIT,
        VK_IMAGE_USAGE_SAMPLED_BIT,
    };
    for (uint32_t i = 0; i < ARRAY_LEN(choices); ++i) {
        if ((supported & choices[i]) != 0) return choices[i];
    }
    /* A future implementation may advertise a usage bit not known above. */
    return supported & (~supported + 1u);
}

static int candidate_score(const struct candidate *candidate, bool headless_advertised) {
    int score = 0;
    if (headless_advertised && candidate->swapchain_extension) score += 16;
    if (headless_advertised && candidate->present_family != NO_QUEUE_FAMILY) score += 8;
    if (candidate->synchronization2_route) score += 2;
    if (candidate->synchronization2_feature) score += 4;
    if (candidate->host_query_reset_feature) score += 1;
    return score;
}

static void emit_json(const struct smoke_report *r) {
    const char *error_step = r->error_step != NULL ? r->error_step : "";
    const bool query_advertised = r->timestamp_valid_bits > 0;
    const bool idle_executed =
        r->queue_idle_attempted && r->device_idle_attempted;
    const bool idle_passed =
        idle_executed && r->queue_idle_passed && r->device_idle_passed;
    printf(
        "{"
        "\"schema\":\"skydnir.vulkan.p0.device.v1\","
        "\"success\":%s,"
        "\"query\":{"
          "\"advertised\":%s,"
          "\"executed\":%s,"
          "\"passed\":%s,"
          "\"unsupported\":%s,"
          "\"skipped\":%s,"
          "\"executor_backed_required\":true,"
          "\"host_reset_advertised\":%s,"
          "\"host_reset_executed\":%s,"
          "\"host_reset_passed\":%s,"
          "\"pool_created\":%s,"
          "\"host_pool_created\":%s,"
          "\"reset_recorded\":%s,"
          "\"timestamp_recorded\":%s,"
          "\"submitted\":%s,"
          "\"get_results_attempted\":%s,"
          "\"host_get_results_attempted\":%s,"
          "\"vk_result\":%d,"
          "\"host_vk_result\":%d,"
          "\"value\":%" PRIu64 ","
          "\"availability\":%" PRIu64 ","
          "\"host_value\":%" PRIu64 ","
          "\"host_availability\":%" PRIu64
        "},"
        "\"synchronization2\":{"
          "\"advertised\":%s,"
          "\"executed\":%s,"
          "\"passed\":%s,"
          "\"unsupported\":%s,"
          "\"skipped\":%s,"
          "\"core_advertised\":%s,"
          "\"extension_advertised\":%s,"
          "\"feature_supported\":%s,"
          "\"vk_result\":%d"
        "},"
        "\"idle\":{"
          "\"advertised\":true,"
          "\"executed\":%s,"
          "\"passed\":%s,"
          "\"queue_attempted\":%s,"
          "\"queue_passed\":%s,"
          "\"queue_vk_result\":%d,"
          "\"device_attempted\":%s,"
          "\"device_passed\":%s,"
          "\"device_vk_result\":%d"
        "},"
        "\"wsi\":{"
          "\"advertised\":%s,"
          "\"executed\":%s,"
          "\"passed\":%s,"
          "\"unsupported\":%s,"
          "\"skipped\":%s,"
          "\"vk_result\":%d,"
          "\"surface_extension_advertised\":%s,"
          "\"headless_extension_advertised\":%s,"
          "\"swapchain_extension_advertised\":%s,"
          "\"headless_surface_executed\":%s,"
          "\"headless_surface_passed\":%s,"
          "\"surface_destroyed\":%s,"
          "\"swapchain_created\":%s,"
          "\"image_count\":%u,"
          "\"acquired\":%s,"
          "\"presented\":%s,"
          "\"destroyed\":%s"
        "},"
        "\"discovery\":{"
          "\"loader_api_version\":%u,"
          "\"instance_api_version\":%u,"
          "\"instance_created\":%s,"
          "\"physical_device_count\":%u,"
          "\"selected_physical_device\":%u,"
          "\"physical_api_version\":%u,"
          "\"vendor_id\":%u,"
          "\"device_id\":%u,"
          "\"queue_family\":%u,"
          "\"present_queue_family\":%u,"
          "\"timestamp_valid_bits\":%u,"
          "\"device_created\":%s"
        "},"
        "\"error\":{\"step\":\"%s\",\"vk_result\":%d}"
        "}\n",
        json_bool(r->ok),
        json_bool(query_advertised),
        json_bool(r->query_attempted),
        json_bool(r->query_passed),
        json_bool(!query_advertised),
        json_bool(!query_advertised && !r->query_attempted),
        json_bool(r->host_query_reset_advertised),
        json_bool(r->host_query_reset_executed),
        json_bool(r->host_query_reset_passed),
        json_bool(r->query_pool_created),
        json_bool(r->host_query_pool_created),
        json_bool(r->query_reset_recorded),
        json_bool(r->timestamp_recorded),
        json_bool(r->query_submitted),
        json_bool(r->query_results_attempted),
        json_bool(r->host_query_results_attempted),
        (int)r->query_result,
        (int)r->host_query_result,
        r->timestamp_value,
        r->timestamp_available,
        r->host_timestamp_value,
        r->host_timestamp_available,
        json_bool(r->synchronization2.advertised),
        json_bool(r->synchronization2.attempted),
        json_bool(r->synchronization2.passed),
        json_bool(r->synchronization2.unsupported),
        json_bool(r->synchronization2.skipped),
        json_bool(r->synchronization2_core),
        json_bool(r->khr_synchronization2_advertised),
        json_bool(r->synchronization2_feature),
        (int)r->synchronization2.result,
        json_bool(idle_executed),
        json_bool(idle_passed),
        json_bool(r->queue_idle_attempted),
        json_bool(r->queue_idle_passed),
        (int)r->queue_idle_result,
        json_bool(r->device_idle_attempted),
        json_bool(r->device_idle_passed),
        (int)r->device_idle_result,
        json_bool(r->wsi.advertised),
        json_bool(r->wsi.attempted),
        json_bool(r->wsi.passed),
        json_bool(r->wsi.unsupported),
        json_bool(r->wsi.skipped),
        (int)r->wsi.result,
        json_bool(r->khr_surface_advertised),
        json_bool(r->ext_headless_surface_advertised),
        json_bool(r->khr_swapchain_advertised),
        json_bool(r->headless.attempted),
        json_bool(r->headless.passed),
        json_bool(r->surface_destroyed),
        json_bool(r->swapchain_created),
        r->swapchain_image_count,
        json_bool(r->swapchain_acquired),
        json_bool(r->swapchain_presented),
        json_bool(r->swapchain_destroyed),
        r->loader_api_version,
        r->instance_api_version,
        json_bool(r->instance_created),
        r->physical_device_count,
        r->selected_physical_device,
        r->physical_api_version,
        r->vendor_id,
        r->device_id,
        r->queue_family,
        r->present_queue_family,
        r->timestamp_valid_bits,
        json_bool(r->device_created),
        error_step,
        (int)r->error_result);
}

int main(void) {
    struct smoke_report report;
    memset(&report, 0, sizeof(report));
    report.error_result = VK_SUCCESS;
    report.query_result = VK_ERROR_UNKNOWN;
    report.host_query_result = VK_ERROR_UNKNOWN;
    report.queue_idle_result = VK_ERROR_UNKNOWN;
    report.device_idle_result = VK_ERROR_UNKNOWN;
    report.selected_physical_device = UINT32_MAX;
    report.queue_family = NO_QUEUE_FAMILY;
    report.present_queue_family = NO_QUEUE_FAMILY;

    VkInstance instance = VK_NULL_HANDLE;
    VkPhysicalDevice physical = VK_NULL_HANDLE;
    VkDevice device = VK_NULL_HANDLE;
    VkSurfaceKHR surface = VK_NULL_HANDLE;
    VkSwapchainKHR swapchain = VK_NULL_HANDLE;
    VkCommandPool command_pool = VK_NULL_HANDLE;
    VkCommandBuffer command_buffer = VK_NULL_HANDLE;
    VkQueryPool command_query_pool = VK_NULL_HANDLE;
    VkQueryPool host_query_pool = VK_NULL_HANDLE;
    VkSemaphore image_available = VK_NULL_HANDLE;
    VkSemaphore present_ready = VK_NULL_HANDLE;
    VkQueue queue = VK_NULL_HANDLE;
    VkQueue present_queue = VK_NULL_HANDLE;

    VkExtensionProperties *instance_extensions = NULL;
    VkPhysicalDevice *physical_devices = NULL;
    VkSurfaceFormatKHR *surface_formats = NULL;
    VkPresentModeKHR *present_modes = NULL;
    VkImage *swapchain_images = NULL;

    PFN_vkDestroySurfaceKHR destroy_surface = NULL;
    PFN_vkGetPhysicalDeviceSurfaceSupportKHR get_surface_support = NULL;
    PFN_vkGetPhysicalDeviceSurfaceCapabilitiesKHR get_surface_capabilities = NULL;
    PFN_vkGetPhysicalDeviceSurfaceFormatsKHR get_surface_formats_function = NULL;
    PFN_vkGetPhysicalDeviceSurfacePresentModesKHR get_present_modes_function = NULL;
    PFN_vkDestroySwapchainKHR destroy_swapchain = NULL;

    bool headless_created = false;
    bool wsi_flow_passed = false;
    bool normal_device_idle_done = false;
    VkResult result = VK_SUCCESS;

    PFN_vkEnumerateInstanceVersion enumerate_instance_version =
        (PFN_vkEnumerateInstanceVersion)vkGetInstanceProcAddr(
            VK_NULL_HANDLE, "vkEnumerateInstanceVersion");
    report.loader_api_version = VK_API_VERSION_1_0;
    if (enumerate_instance_version != NULL) {
        result = enumerate_instance_version(&report.loader_api_version);
        if (result != VK_SUCCESS) {
            record_failure(&report, "vkEnumerateInstanceVersion", result);
            goto cleanup;
        }
    }
    report.instance_api_version = report.loader_api_version;
    if (report.instance_api_version > VK_API_VERSION_1_3) {
        report.instance_api_version = VK_API_VERSION_1_3;
    }

    uint32_t instance_extension_count = 0;
    result = enumerate_instance_extensions(&instance_extensions, &instance_extension_count);
    if (result != VK_SUCCESS) {
        record_failure(&report, "vkEnumerateInstanceExtensionProperties", result);
        goto cleanup;
    }
    report.khr_surface_advertised = extension_present(
        instance_extensions, instance_extension_count, VK_KHR_SURFACE_EXTENSION_NAME);
    report.ext_headless_surface_advertised = extension_present(
        instance_extensions, instance_extension_count, VK_EXT_HEADLESS_SURFACE_EXTENSION_NAME);
    bool headless_advertised =
        report.khr_surface_advertised && report.ext_headless_surface_advertised;
    optional_path_set_advertised(&report.headless, headless_advertised);

    const char *enabled_instance_extensions[3];
    uint32_t enabled_instance_extension_count = 0;
    if (headless_advertised) {
        enabled_instance_extensions[enabled_instance_extension_count++] =
            VK_KHR_SURFACE_EXTENSION_NAME;
        enabled_instance_extensions[enabled_instance_extension_count++] =
            VK_EXT_HEADLESS_SURFACE_EXTENSION_NAME;
    }
    if (report.instance_api_version < VK_API_VERSION_1_1 &&
        extension_present(instance_extensions, instance_extension_count,
                          VK_KHR_GET_PHYSICAL_DEVICE_PROPERTIES_2_EXTENSION_NAME)) {
        enabled_instance_extensions[enabled_instance_extension_count++] =
            VK_KHR_GET_PHYSICAL_DEVICE_PROPERTIES_2_EXTENSION_NAME;
    }

    const VkApplicationInfo application_info = {
        .sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
        .pApplicationName = "skydnir-vulkan-p0-smoke",
        .applicationVersion = VK_MAKE_VERSION(1, 0, 0),
        .pEngineName = "none",
        .engineVersion = VK_MAKE_VERSION(1, 0, 0),
        .apiVersion = report.instance_api_version,
    };
    const VkInstanceCreateInfo instance_create_info = {
        .sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
        .pApplicationInfo = &application_info,
        .enabledExtensionCount = enabled_instance_extension_count,
        .ppEnabledExtensionNames = enabled_instance_extensions,
    };
    result = vkCreateInstance(&instance_create_info, NULL, &instance);
    if (result != VK_SUCCESS) {
        record_failure(&report, "vkCreateInstance", result);
        goto cleanup;
    }
    report.instance_created = true;

    destroy_surface = (PFN_vkDestroySurfaceKHR)vkGetInstanceProcAddr(
        instance, "vkDestroySurfaceKHR");
    get_surface_support = (PFN_vkGetPhysicalDeviceSurfaceSupportKHR)
        vkGetInstanceProcAddr(instance, "vkGetPhysicalDeviceSurfaceSupportKHR");
    get_surface_capabilities = (PFN_vkGetPhysicalDeviceSurfaceCapabilitiesKHR)
        vkGetInstanceProcAddr(instance, "vkGetPhysicalDeviceSurfaceCapabilitiesKHR");
    get_surface_formats_function = (PFN_vkGetPhysicalDeviceSurfaceFormatsKHR)
        vkGetInstanceProcAddr(instance, "vkGetPhysicalDeviceSurfaceFormatsKHR");
    get_present_modes_function = (PFN_vkGetPhysicalDeviceSurfacePresentModesKHR)
        vkGetInstanceProcAddr(instance, "vkGetPhysicalDeviceSurfacePresentModesKHR");

    if (headless_advertised) {
        PFN_vkCreateHeadlessSurfaceEXT create_headless_surface =
            (PFN_vkCreateHeadlessSurfaceEXT)vkGetInstanceProcAddr(
                instance, "vkCreateHeadlessSurfaceEXT");
        if (create_headless_surface == NULL || destroy_surface == NULL ||
            get_surface_support == NULL || get_surface_capabilities == NULL ||
            get_surface_formats_function == NULL || get_present_modes_function == NULL) {
            report.headless.result = VK_ERROR_EXTENSION_NOT_PRESENT;
            record_failure(&report, "headless-surface-proc-address", report.headless.result);
            goto cleanup;
        }
        const VkHeadlessSurfaceCreateInfoEXT headless_create_info = {
            .sType = VK_STRUCTURE_TYPE_HEADLESS_SURFACE_CREATE_INFO_EXT,
        };
        report.headless.attempted = true;
        result = create_headless_surface(instance, &headless_create_info, NULL, &surface);
        report.headless.result = result;
        if (result != VK_SUCCESS) {
            record_failure(&report, "vkCreateHeadlessSurfaceEXT", result);
            goto cleanup;
        }
        headless_created = true;
    }

    result = enumerate_physical_devices(instance, &physical_devices,
                                        &report.physical_device_count);
    if (result != VK_SUCCESS) {
        record_failure(&report, "vkEnumeratePhysicalDevices", result);
        goto cleanup;
    }
    if (report.physical_device_count == 0) {
        record_failure(&report, "physical-device-not-found", VK_ERROR_INITIALIZATION_FAILED);
        goto cleanup;
    }

    PFN_vkGetPhysicalDeviceFeatures2 get_features2 =
        (PFN_vkGetPhysicalDeviceFeatures2)vkGetInstanceProcAddr(
            instance, "vkGetPhysicalDeviceFeatures2");
    if (get_features2 == NULL) {
        get_features2 = (PFN_vkGetPhysicalDeviceFeatures2)vkGetInstanceProcAddr(
            instance, "vkGetPhysicalDeviceFeatures2KHR");
    }

    struct candidate selected;
    memset(&selected, 0, sizeof(selected));
    selected.command_family = NO_QUEUE_FAMILY;
    selected.present_family = NO_QUEUE_FAMILY;
    int selected_score = -1;

    for (uint32_t device_index = 0; device_index < report.physical_device_count;
         ++device_index) {
        struct candidate current;
        memset(&current, 0, sizeof(current));
        current.physical = physical_devices[device_index];
        current.command_family = NO_QUEUE_FAMILY;
        current.present_family = NO_QUEUE_FAMILY;
        vkGetPhysicalDeviceProperties(current.physical, &current.properties);

        VkExtensionProperties *device_extensions = NULL;
        uint32_t device_extension_count = 0;
        result = enumerate_device_extensions(current.physical, &device_extensions,
                                             &device_extension_count);
        if (result != VK_SUCCESS) {
            free(device_extensions);
            record_failure(&report, "vkEnumerateDeviceExtensionProperties", result);
            goto cleanup;
        }
        current.swapchain_extension = extension_present(
            device_extensions, device_extension_count, VK_KHR_SWAPCHAIN_EXTENSION_NAME);
        current.synchronization2_extension = extension_present(
            device_extensions, device_extension_count,
            VK_KHR_SYNCHRONIZATION_2_EXTENSION_NAME);
        current.host_query_reset_extension = extension_present(
            device_extensions, device_extension_count,
            VK_EXT_HOST_QUERY_RESET_EXTENSION_NAME);
        free(device_extensions);

        current.synchronization2_core =
            report.instance_api_version >= VK_API_VERSION_1_3 &&
            current.properties.apiVersion >= VK_API_VERSION_1_3;
        current.synchronization2_route =
            current.synchronization2_core || current.synchronization2_extension;
        current.host_query_reset_core =
            report.instance_api_version >= VK_API_VERSION_1_2 &&
            current.properties.apiVersion >= VK_API_VERSION_1_2;
        current.host_query_reset_route =
            current.host_query_reset_core || current.host_query_reset_extension;
        if (current.synchronization2_route || current.host_query_reset_route) {
            if (get_features2 == NULL) {
                record_failure(&report, "vkGetPhysicalDeviceFeatures2-proc-address",
                               VK_ERROR_EXTENSION_NOT_PRESENT);
                goto cleanup;
            }
            VkPhysicalDeviceHostQueryResetFeatures host_query_reset_features = {
                .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_HOST_QUERY_RESET_FEATURES,
            };
            VkPhysicalDeviceSynchronization2Features synchronization2_features = {
                .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SYNCHRONIZATION_2_FEATURES,
                .pNext = current.host_query_reset_route
                    ? &host_query_reset_features : NULL,
            };
            VkPhysicalDeviceFeatures2 features2 = {
                .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2,
                .pNext = current.synchronization2_route
                    ? (void *)&synchronization2_features
                    : (void *)&host_query_reset_features,
            };
            get_features2(current.physical, &features2);
            current.synchronization2_feature =
                current.synchronization2_route &&
                synchronization2_features.synchronization2 == VK_TRUE;
            current.host_query_reset_feature =
                current.host_query_reset_route &&
                host_query_reset_features.hostQueryReset == VK_TRUE;
        }

        VkQueueFamilyProperties *queue_properties = NULL;
        uint32_t queue_count = 0;
        result = get_queue_families(current.physical, &queue_properties, &queue_count);
        if (result != VK_SUCCESS) {
            free(queue_properties);
            continue;
        }
        for (uint32_t queue_index = 0; queue_index < queue_count; ++queue_index) {
            const VkQueueFamilyProperties *properties = &queue_properties[queue_index];
            if (properties->queueCount > 0 && properties->timestampValidBits > 0 &&
                (properties->queueFlags &
                 (VK_QUEUE_GRAPHICS_BIT | VK_QUEUE_COMPUTE_BIT | VK_QUEUE_TRANSFER_BIT)) != 0 &&
                current.command_family == NO_QUEUE_FAMILY) {
                current.command_family = queue_index;
                current.timestamp_valid_bits = properties->timestampValidBits;
            }
            if (headless_created && current.swapchain_extension &&
                properties->queueCount > 0) {
                VkBool32 supported = VK_FALSE;
                result = get_surface_support(current.physical, queue_index,
                                             surface, &supported);
                if (result != VK_SUCCESS) {
                    free(queue_properties);
                    record_failure(&report, "vkGetPhysicalDeviceSurfaceSupportKHR", result);
                    goto cleanup;
                }
                if (supported == VK_TRUE && current.present_family == NO_QUEUE_FAMILY) {
                    current.present_family = queue_index;
                }
            }
        }
        free(queue_properties);

        if (current.command_family == NO_QUEUE_FAMILY) continue;
        int score = candidate_score(&current, headless_advertised);
        if (score > selected_score) {
            selected = current;
            selected_score = score;
            report.selected_physical_device = device_index;
        }
    }

    if (selected_score < 0 || selected.physical == VK_NULL_HANDLE) {
        record_failure(&report, "timestamp-capable-queue-not-found",
                       VK_ERROR_FEATURE_NOT_PRESENT);
        goto cleanup;
    }
    physical = selected.physical;
    report.physical_api_version = selected.properties.apiVersion;
    report.vendor_id = selected.properties.vendorID;
    report.device_id = selected.properties.deviceID;
    report.queue_family = selected.command_family;
    report.present_queue_family = selected.present_family;
    report.timestamp_valid_bits = selected.timestamp_valid_bits;
    report.khr_swapchain_advertised = selected.swapchain_extension;
    report.khr_synchronization2_advertised = selected.synchronization2_extension;
    report.synchronization2_core = selected.synchronization2_core;
    report.synchronization2_feature = selected.synchronization2_feature;
    report.ext_host_query_reset_advertised = selected.host_query_reset_extension;
    report.host_query_reset_core = selected.host_query_reset_core;
    report.host_query_reset_feature = selected.host_query_reset_feature;
    report.host_query_reset_advertised =
        selected.host_query_reset_route && selected.host_query_reset_feature;

    bool synchronization2_advertised =
        selected.synchronization2_route && selected.synchronization2_feature;
    optional_path_set_advertised(&report.synchronization2,
                                 synchronization2_advertised);
    bool wsi_advertised = headless_advertised && selected.swapchain_extension;
    optional_path_set_advertised(&report.wsi, wsi_advertised);
    if (wsi_advertised) {
        if (selected.present_family == NO_QUEUE_FAMILY) {
            report.wsi.result = VK_ERROR_FEATURE_NOT_PRESENT;
            record_failure(&report, "present-capable-queue-not-found", report.wsi.result);
            goto cleanup;
        }
    }

    float queue_priority = 1.0f;
    VkDeviceQueueCreateInfo queue_create_infos[2];
    memset(queue_create_infos, 0, sizeof(queue_create_infos));
    uint32_t queue_create_info_count = 1;
    queue_create_infos[0].sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO;
    queue_create_infos[0].queueFamilyIndex = selected.command_family;
    queue_create_infos[0].queueCount = 1;
    queue_create_infos[0].pQueuePriorities = &queue_priority;
    if (wsi_advertised && selected.present_family != selected.command_family) {
        queue_create_info_count = 2;
        queue_create_infos[1].sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO;
        queue_create_infos[1].queueFamilyIndex = selected.present_family;
        queue_create_infos[1].queueCount = 1;
        queue_create_infos[1].pQueuePriorities = &queue_priority;
    }

    const char *enabled_device_extensions[3];
    uint32_t enabled_device_extension_count = 0;
    if (synchronization2_advertised && selected.synchronization2_extension) {
        enabled_device_extensions[enabled_device_extension_count++] =
            VK_KHR_SYNCHRONIZATION_2_EXTENSION_NAME;
    }
    if (report.host_query_reset_advertised &&
        selected.host_query_reset_extension) {
        enabled_device_extensions[enabled_device_extension_count++] =
            VK_EXT_HOST_QUERY_RESET_EXTENSION_NAME;
    }
    if (wsi_advertised) {
        enabled_device_extensions[enabled_device_extension_count++] =
            VK_KHR_SWAPCHAIN_EXTENSION_NAME;
    }
    VkPhysicalDeviceHostQueryResetFeatures host_query_reset_enable = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_HOST_QUERY_RESET_FEATURES,
        .hostQueryReset = report.host_query_reset_advertised ? VK_TRUE : VK_FALSE,
    };
    VkPhysicalDeviceSynchronization2Features synchronization2_enable = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SYNCHRONIZATION_2_FEATURES,
        .pNext = report.host_query_reset_advertised
            ? &host_query_reset_enable : NULL,
        .synchronization2 = synchronization2_advertised ? VK_TRUE : VK_FALSE,
    };
    VkDeviceCreateInfo device_create_info = {
        .sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO,
        .pNext = synchronization2_advertised
            ? (void *)&synchronization2_enable
            : (report.host_query_reset_advertised
                ? (void *)&host_query_reset_enable : NULL),
        .queueCreateInfoCount = queue_create_info_count,
        .pQueueCreateInfos = queue_create_infos,
        .enabledExtensionCount = enabled_device_extension_count,
        .ppEnabledExtensionNames = enabled_device_extensions,
    };
    result = vkCreateDevice(physical, &device_create_info, NULL, &device);
    if (result != VK_SUCCESS) {
        if (synchronization2_advertised) report.synchronization2.result = result;
        if (wsi_advertised) report.wsi.result = result;
        record_failure(&report, "vkCreateDevice", result);
        goto cleanup;
    }
    report.device_created = true;
    vkGetDeviceQueue(device, selected.command_family, 0, &queue);
    if (queue == VK_NULL_HANDLE) {
        record_failure(&report, "vkGetDeviceQueue", VK_ERROR_INITIALIZATION_FAILED);
        goto cleanup;
    }
    if (wsi_advertised) {
        vkGetDeviceQueue(device, selected.present_family, 0, &present_queue);
        if (present_queue == VK_NULL_HANDLE) {
            report.wsi.result = VK_ERROR_INITIALIZATION_FAILED;
            record_failure(&report, "vkGetDeviceQueue-present", report.wsi.result);
            goto cleanup;
        }
    }

    const VkCommandPoolCreateInfo command_pool_create_info = {
        .sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO,
        .flags = VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT,
        .queueFamilyIndex = selected.command_family,
    };
    result = vkCreateCommandPool(device, &command_pool_create_info, NULL, &command_pool);
    if (result != VK_SUCCESS) {
        record_failure(&report, "vkCreateCommandPool", result);
        goto cleanup;
    }
    const VkCommandBufferAllocateInfo command_buffer_allocate_info = {
        .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,
        .commandPool = command_pool,
        .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY,
        .commandBufferCount = 1,
    };
    result = vkAllocateCommandBuffers(device, &command_buffer_allocate_info,
                                      &command_buffer);
    if (result != VK_SUCCESS) {
        record_failure(&report, "vkAllocateCommandBuffers", result);
        goto cleanup;
    }

    report.query_attempted = true;
    const VkQueryPoolCreateInfo query_pool_create_info = {
        .sType = VK_STRUCTURE_TYPE_QUERY_POOL_CREATE_INFO,
        .queryType = VK_QUERY_TYPE_TIMESTAMP,
        .queryCount = 1,
    };
    result = vkCreateQueryPool(
        device, &query_pool_create_info, NULL, &command_query_pool);
    report.query_result = result;
    if (result != VK_SUCCESS) {
        record_failure(&report, "vkCreateQueryPool", result);
        goto cleanup;
    }
    report.query_pool_created = true;

    if (report.host_query_reset_advertised) {
        result = vkCreateQueryPool(
            device, &query_pool_create_info, NULL, &host_query_pool);
        report.host_query_result = result;
        if (result != VK_SUCCESS) {
            record_failure(&report, "vkCreateQueryPool-host-reset", result);
            goto cleanup;
        }
        report.host_query_pool_created = true;

        PFN_vkResetQueryPool host_reset_query_pool = NULL;
        if (report.host_query_reset_core) {
            host_reset_query_pool = (PFN_vkResetQueryPool)
                vkGetDeviceProcAddr(device, "vkResetQueryPool");
        }
        if (host_reset_query_pool == NULL &&
            report.ext_host_query_reset_advertised) {
            host_reset_query_pool = (PFN_vkResetQueryPool)
                vkGetDeviceProcAddr(device, "vkResetQueryPoolEXT");
        }
        if (host_reset_query_pool == NULL) {
            report.query_result = VK_ERROR_EXTENSION_NOT_PRESENT;
            record_failure(&report, "host-query-reset-proc-address",
                           report.query_result);
            goto cleanup;
        }
        report.host_query_reset_executed = true;
        host_reset_query_pool(device, host_query_pool, 0, 1);
        /* The API is void.  A transport failure makes the device lost and is
         * observed by the mandatory submit/get-results sequence below; the
         * passed bit is finalized only after that sequence succeeds. */
    }

    const VkCommandBufferBeginInfo begin_info = {
        .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,
        .flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT,
    };
    result = vkBeginCommandBuffer(command_buffer, &begin_info);
    if (result != VK_SUCCESS) {
        report.query_result = result;
        record_failure(&report, "vkBeginCommandBuffer-query", result);
        goto cleanup;
    }
    vkCmdResetQueryPool(command_buffer, command_query_pool, 0, 1);
    report.query_reset_recorded = true;

    PFN_vkCmdWriteTimestamp2 command_write_timestamp2 = NULL;
    PFN_vkQueueSubmit2 queue_submit2 = NULL;
    if (synchronization2_advertised) {
        command_write_timestamp2 = (PFN_vkCmdWriteTimestamp2)
            vkGetDeviceProcAddr(device, "vkCmdWriteTimestamp2");
        if (command_write_timestamp2 == NULL) {
            command_write_timestamp2 = (PFN_vkCmdWriteTimestamp2)
                vkGetDeviceProcAddr(device, "vkCmdWriteTimestamp2KHR");
        }
        queue_submit2 = (PFN_vkQueueSubmit2)
            vkGetDeviceProcAddr(device, "vkQueueSubmit2");
        if (queue_submit2 == NULL) {
            queue_submit2 = (PFN_vkQueueSubmit2)
                vkGetDeviceProcAddr(device, "vkQueueSubmit2KHR");
        }
        if (command_write_timestamp2 == NULL || queue_submit2 == NULL) {
            report.synchronization2.result = VK_ERROR_EXTENSION_NOT_PRESENT;
            report.query_result = report.synchronization2.result;
            record_failure(&report, "synchronization2-proc-address",
                           report.synchronization2.result);
            goto cleanup;
        }
        report.synchronization2.attempted = true;
        command_write_timestamp2(
            command_buffer, VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT,
            command_query_pool, 0);
        if (host_query_pool != VK_NULL_HANDLE) {
            command_write_timestamp2(
                command_buffer, VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT,
                host_query_pool, 0);
        }
    } else {
        vkCmdWriteTimestamp(
            command_buffer, VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT,
            command_query_pool, 0);
        if (host_query_pool != VK_NULL_HANDLE) {
            vkCmdWriteTimestamp(
                command_buffer, VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT,
                host_query_pool, 0);
        }
    }
    report.timestamp_recorded = true;
    result = vkEndCommandBuffer(command_buffer);
    if (result != VK_SUCCESS) {
        report.query_result = result;
        if (synchronization2_advertised) report.synchronization2.result = result;
        record_failure(&report, "vkEndCommandBuffer-query", result);
        goto cleanup;
    }

    if (synchronization2_advertised) {
        const VkCommandBufferSubmitInfo command_buffer_submit_info = {
            .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_SUBMIT_INFO,
            .commandBuffer = command_buffer,
        };
        const VkSubmitInfo2 submit_info2 = {
            .sType = VK_STRUCTURE_TYPE_SUBMIT_INFO_2,
            .commandBufferInfoCount = 1,
            .pCommandBufferInfos = &command_buffer_submit_info,
        };
        result = queue_submit2(queue, 1, &submit_info2, VK_NULL_HANDLE);
        report.synchronization2.result = result;
    } else {
        const VkSubmitInfo submit_info = {
            .sType = VK_STRUCTURE_TYPE_SUBMIT_INFO,
            .commandBufferCount = 1,
            .pCommandBuffers = &command_buffer,
        };
        result = vkQueueSubmit(queue, 1, &submit_info, VK_NULL_HANDLE);
    }
    report.query_result = result;
    if (result != VK_SUCCESS) {
        record_failure(&report,
                       synchronization2_advertised ? "vkQueueSubmit2" : "vkQueueSubmit",
                       result);
        goto cleanup;
    }
    report.query_submitted = true;
    report.queue_idle_attempted = true;
    result = vkQueueWaitIdle(queue);
    report.queue_idle_result = result;
    report.queue_idle_passed = result == VK_SUCCESS;
    if (result != VK_SUCCESS) {
        report.query_result = result;
        record_failure(&report, "vkQueueWaitIdle-query", result);
        goto cleanup;
    }

    struct {
        uint64_t value;
        uint64_t availability;
    } timestamp_result = {0, 0};
    report.query_results_attempted = true;
    result = vkGetQueryPoolResults(
        device, command_query_pool, 0, 1, sizeof(timestamp_result), &timestamp_result,
        sizeof(timestamp_result),
        VK_QUERY_RESULT_64_BIT | VK_QUERY_RESULT_WAIT_BIT |
            VK_QUERY_RESULT_WITH_AVAILABILITY_BIT);
    report.query_result = result;
    report.timestamp_value = timestamp_result.value;
    report.timestamp_available = timestamp_result.availability;
    if (result != VK_SUCCESS) {
        record_failure(&report, "vkGetQueryPoolResults", result);
        goto cleanup;
    }
    if (timestamp_result.availability == 0) {
        report.query_result = VK_NOT_READY;
        record_failure(&report, "timestamp-result-unavailable", VK_NOT_READY);
        goto cleanup;
    }
    if (report.host_query_reset_advertised) {
        struct {
            uint64_t value;
            uint64_t availability;
        } host_timestamp_result = {0, 0};
        report.host_query_results_attempted = true;
        result = vkGetQueryPoolResults(
            device, host_query_pool, 0, 1, sizeof(host_timestamp_result),
            &host_timestamp_result, sizeof(host_timestamp_result),
            VK_QUERY_RESULT_64_BIT | VK_QUERY_RESULT_WAIT_BIT |
                VK_QUERY_RESULT_WITH_AVAILABILITY_BIT);
        report.host_query_result = result;
        report.host_timestamp_value = host_timestamp_result.value;
        report.host_timestamp_available = host_timestamp_result.availability;
        if (result != VK_SUCCESS) {
            record_failure(&report, "vkGetQueryPoolResults-host-reset", result);
            goto cleanup;
        }
        if (host_timestamp_result.availability == 0) {
            report.host_query_result = VK_NOT_READY;
            record_failure(
                &report, "host-reset-timestamp-result-unavailable", VK_NOT_READY);
            goto cleanup;
        }
        report.host_query_reset_passed = report.host_query_reset_executed;
    }
    report.query_passed = true;
    if (synchronization2_advertised) {
        report.synchronization2.passed = true;
    }

    if (wsi_advertised) {
        destroy_swapchain = (PFN_vkDestroySwapchainKHR)
            vkGetDeviceProcAddr(device, "vkDestroySwapchainKHR");
        PFN_vkCreateSwapchainKHR create_swapchain = (PFN_vkCreateSwapchainKHR)
            vkGetDeviceProcAddr(device, "vkCreateSwapchainKHR");
        PFN_vkGetSwapchainImagesKHR get_swapchain_images =
            (PFN_vkGetSwapchainImagesKHR)vkGetDeviceProcAddr(
                device, "vkGetSwapchainImagesKHR");
        PFN_vkAcquireNextImageKHR acquire_next_image =
            (PFN_vkAcquireNextImageKHR)vkGetDeviceProcAddr(
                device, "vkAcquireNextImageKHR");
        PFN_vkQueuePresentKHR queue_present = (PFN_vkQueuePresentKHR)
            vkGetDeviceProcAddr(device, "vkQueuePresentKHR");
        if (destroy_swapchain == NULL || create_swapchain == NULL ||
            get_swapchain_images == NULL || acquire_next_image == NULL ||
            queue_present == NULL) {
            report.wsi.result = VK_ERROR_EXTENSION_NOT_PRESENT;
            record_failure(&report, "swapchain-proc-address", report.wsi.result);
            goto cleanup;
        }

        VkSurfaceCapabilitiesKHR capabilities;
        memset(&capabilities, 0, sizeof(capabilities));
        result = get_surface_capabilities(physical, surface, &capabilities);
        report.wsi.result = result;
        if (result != VK_SUCCESS) {
            record_failure(&report, "vkGetPhysicalDeviceSurfaceCapabilitiesKHR", result);
            goto cleanup;
        }
        uint32_t surface_format_count = 0;
        result = get_surface_formats(get_surface_formats_function, physical, surface,
                                     &surface_formats, &surface_format_count);
        report.wsi.result = result;
        if (result != VK_SUCCESS) {
            record_failure(&report, "vkGetPhysicalDeviceSurfaceFormatsKHR", result);
            goto cleanup;
        }
        uint32_t present_mode_count = 0;
        result = get_present_modes(get_present_modes_function, physical, surface,
                                   &present_modes, &present_mode_count);
        report.wsi.result = result;
        if (result != VK_SUCCESS) {
            record_failure(&report, "vkGetPhysicalDeviceSurfacePresentModesKHR", result);
            goto cleanup;
        }

        VkPresentModeKHR present_mode = VK_PRESENT_MODE_FIFO_KHR;
        bool fifo_found = false;
        for (uint32_t i = 0; i < present_mode_count; ++i) {
            if (present_modes[i] == VK_PRESENT_MODE_FIFO_KHR) fifo_found = true;
        }
        if (!fifo_found) present_mode = present_modes[0];

        VkExtent2D extent = capabilities.currentExtent;
        if (extent.width == UINT32_MAX || extent.height == UINT32_MAX) {
            extent.width = clamp_u32(64u, capabilities.minImageExtent.width,
                                    capabilities.maxImageExtent.width);
            extent.height = clamp_u32(64u, capabilities.minImageExtent.height,
                                     capabilities.maxImageExtent.height);
        }
        uint32_t image_count = capabilities.minImageCount;
        if (image_count < UINT32_MAX) ++image_count;
        if (capabilities.maxImageCount != 0 && image_count > capabilities.maxImageCount) {
            image_count = capabilities.maxImageCount;
        }
        if (image_count < capabilities.minImageCount) {
            report.wsi.result = VK_ERROR_INITIALIZATION_FAILED;
            record_failure(&report, "swapchain-image-count", report.wsi.result);
            goto cleanup;
        }
        VkCompositeAlphaFlagBitsKHR composite_alpha =
            choose_composite_alpha(capabilities.supportedCompositeAlpha);
        VkImageUsageFlags image_usage =
            choose_image_usage(capabilities.supportedUsageFlags);
        if (composite_alpha == 0 || image_usage == 0) {
            report.wsi.result = VK_ERROR_FORMAT_NOT_SUPPORTED;
            record_failure(&report, "swapchain-surface-capabilities", report.wsi.result);
            goto cleanup;
        }
        uint32_t sharing_families[2] = {
            selected.command_family, selected.present_family,
        };
        const bool separate_queues =
            selected.command_family != selected.present_family;
        VkSurfaceFormatKHR surface_format = surface_formats[0];
        if (surface_format.format == VK_FORMAT_UNDEFINED) {
            surface_format.format = VK_FORMAT_B8G8R8A8_UNORM;
        }
        const VkSwapchainCreateInfoKHR swapchain_create_info = {
            .sType = VK_STRUCTURE_TYPE_SWAPCHAIN_CREATE_INFO_KHR,
            .surface = surface,
            .minImageCount = image_count,
            .imageFormat = surface_format.format,
            .imageColorSpace = surface_format.colorSpace,
            .imageExtent = extent,
            .imageArrayLayers = 1,
            .imageUsage = image_usage,
            .imageSharingMode = separate_queues
                ? VK_SHARING_MODE_CONCURRENT : VK_SHARING_MODE_EXCLUSIVE,
            .queueFamilyIndexCount = separate_queues ? 2u : 0u,
            .pQueueFamilyIndices = separate_queues ? sharing_families : NULL,
            .preTransform = capabilities.currentTransform,
            .compositeAlpha = composite_alpha,
            .presentMode = present_mode,
            .clipped = VK_TRUE,
        };
        report.wsi.attempted = true;
        result = create_swapchain(device, &swapchain_create_info, NULL, &swapchain);
        report.wsi.result = result;
        if (result != VK_SUCCESS) {
            record_failure(&report, "vkCreateSwapchainKHR", result);
            goto cleanup;
        }
        report.swapchain_created = true;

        result = get_swapchain_images(device, swapchain,
                                      &report.swapchain_image_count, NULL);
        report.wsi.result = result;
        if (result != VK_SUCCESS || report.swapchain_image_count == 0) {
            if (result == VK_SUCCESS) result = VK_ERROR_INITIALIZATION_FAILED;
            report.wsi.result = result;
            record_failure(&report, "vkGetSwapchainImagesKHR-count", result);
            goto cleanup;
        }
        swapchain_images = calloc(report.swapchain_image_count,
                                  sizeof(*swapchain_images));
        if (swapchain_images == NULL) {
            report.wsi.result = VK_ERROR_OUT_OF_HOST_MEMORY;
            record_failure(&report, "swapchain-images-allocation", report.wsi.result);
            goto cleanup;
        }
        uint32_t swapchain_image_capacity = report.swapchain_image_count;
        result = get_swapchain_images(device, swapchain, &swapchain_image_capacity,
                                      swapchain_images);
        report.wsi.result = result;
        if (result != VK_SUCCESS || swapchain_image_capacity == 0) {
            if (result == VK_SUCCESS) result = VK_ERROR_INITIALIZATION_FAILED;
            report.wsi.result = result;
            record_failure(&report, "vkGetSwapchainImagesKHR", result);
            goto cleanup;
        }
        report.swapchain_image_count = swapchain_image_capacity;

        const VkSemaphoreCreateInfo semaphore_create_info = {
            .sType = VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO,
        };
        result = vkCreateSemaphore(device, &semaphore_create_info, NULL,
                                   &image_available);
        report.wsi.result = result;
        if (result != VK_SUCCESS) {
            record_failure(&report, "vkCreateSemaphore-acquire", result);
            goto cleanup;
        }
        result = vkCreateSemaphore(device, &semaphore_create_info, NULL,
                                   &present_ready);
        report.wsi.result = result;
        if (result != VK_SUCCESS) {
            record_failure(&report, "vkCreateSemaphore-present", result);
            goto cleanup;
        }

        uint32_t image_index = 0;
        const uint64_t acquire_timeout_ns = 5ull * 1000ull * 1000ull * 1000ull;
        result = acquire_next_image(
            device, swapchain, acquire_timeout_ns, image_available,
            VK_NULL_HANDLE, &image_index);
        report.wsi.result = result;
        if (result != VK_SUCCESS && result != VK_SUBOPTIMAL_KHR) {
            record_failure(&report, "vkAcquireNextImageKHR", result);
            goto cleanup;
        }
        if (image_index >= report.swapchain_image_count) {
            report.wsi.result = VK_ERROR_INITIALIZATION_FAILED;
            record_failure(&report, "vkAcquireNextImageKHR-index", report.wsi.result);
            goto cleanup;
        }
        report.swapchain_acquired = true;

        result = vkResetCommandBuffer(command_buffer, 0);
        report.wsi.result = result;
        if (result != VK_SUCCESS) {
            record_failure(&report, "vkResetCommandBuffer-present", result);
            goto cleanup;
        }
        result = vkBeginCommandBuffer(command_buffer, &begin_info);
        report.wsi.result = result;
        if (result != VK_SUCCESS) {
            record_failure(&report, "vkBeginCommandBuffer-present", result);
            goto cleanup;
        }
        const VkImageMemoryBarrier present_barrier = {
            .sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER,
            .srcAccessMask = 0,
            .dstAccessMask = 0,
            .oldLayout = VK_IMAGE_LAYOUT_UNDEFINED,
            .newLayout = VK_IMAGE_LAYOUT_PRESENT_SRC_KHR,
            .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .image = swapchain_images[image_index],
            .subresourceRange = {
                .aspectMask = VK_IMAGE_ASPECT_COLOR_BIT,
                .baseMipLevel = 0,
                .levelCount = 1,
                .baseArrayLayer = 0,
                .layerCount = 1,
            },
        };
        vkCmdPipelineBarrier(
            command_buffer,
            VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT,
            VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT,
            0, 0, NULL, 0, NULL, 1, &present_barrier);
        result = vkEndCommandBuffer(command_buffer);
        report.wsi.result = result;
        if (result != VK_SUCCESS) {
            record_failure(&report, "vkEndCommandBuffer-present", result);
            goto cleanup;
        }

        VkPipelineStageFlags wait_stage = VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT;
        const VkSubmitInfo present_submit = {
            .sType = VK_STRUCTURE_TYPE_SUBMIT_INFO,
            .waitSemaphoreCount = 1,
            .pWaitSemaphores = &image_available,
            .pWaitDstStageMask = &wait_stage,
            .commandBufferCount = 1,
            .pCommandBuffers = &command_buffer,
            .signalSemaphoreCount = 1,
            .pSignalSemaphores = &present_ready,
        };
        result = vkQueueSubmit(queue, 1, &present_submit, VK_NULL_HANDLE);
        report.wsi.result = result;
        if (result != VK_SUCCESS) {
            record_failure(&report, "vkQueueSubmit-present-transition", result);
            goto cleanup;
        }
        const VkPresentInfoKHR present_info = {
            .sType = VK_STRUCTURE_TYPE_PRESENT_INFO_KHR,
            .waitSemaphoreCount = 1,
            .pWaitSemaphores = &present_ready,
            .swapchainCount = 1,
            .pSwapchains = &swapchain,
            .pImageIndices = &image_index,
        };
        result = queue_present(present_queue, &present_info);
        report.wsi.result = result;
        if (result != VK_SUCCESS && result != VK_SUBOPTIMAL_KHR) {
            record_failure(&report, "vkQueuePresentKHR", result);
            goto cleanup;
        }
        report.swapchain_presented = true;

        report.queue_idle_attempted = true;
        result = vkQueueWaitIdle(present_queue);
        report.queue_idle_result = result;
        if (result != VK_SUCCESS) {
            report.queue_idle_passed = false;
            report.wsi.result = result;
            record_failure(&report, "vkQueueWaitIdle-present", result);
            goto cleanup;
        }
        if (present_queue != queue) {
            result = vkQueueWaitIdle(queue);
            report.queue_idle_result = result;
            if (result != VK_SUCCESS) {
                report.queue_idle_passed = false;
                report.wsi.result = result;
                record_failure(&report, "vkQueueWaitIdle-present-transition", result);
                goto cleanup;
            }
        }
        report.queue_idle_passed = true;
        wsi_flow_passed = true;
    }

    report.device_idle_attempted = true;
    result = vkDeviceWaitIdle(device);
    report.device_idle_result = result;
    report.device_idle_passed = result == VK_SUCCESS;
    normal_device_idle_done = true;
    if (result != VK_SUCCESS) {
        record_failure(&report, "vkDeviceWaitIdle", result);
        goto cleanup;
    }

cleanup:
    if (device != VK_NULL_HANDLE && !normal_device_idle_done) {
        report.device_idle_attempted = true;
        VkResult idle_result = vkDeviceWaitIdle(device);
        report.device_idle_result = idle_result;
        report.device_idle_passed = idle_result == VK_SUCCESS;
        if (idle_result != VK_SUCCESS && report.error_step == NULL) {
            record_failure(&report, "vkDeviceWaitIdle-cleanup", idle_result);
        }
    }
    if (device != VK_NULL_HANDLE) {
        if (present_ready != VK_NULL_HANDLE) {
            vkDestroySemaphore(device, present_ready, NULL);
        }
        if (image_available != VK_NULL_HANDLE) {
            vkDestroySemaphore(device, image_available, NULL);
        }
        if (swapchain != VK_NULL_HANDLE && destroy_swapchain != NULL) {
            destroy_swapchain(device, swapchain, NULL);
            report.swapchain_destroyed = true;
        }
        if (host_query_pool != VK_NULL_HANDLE) {
            vkDestroyQueryPool(device, host_query_pool, NULL);
        }
        if (command_query_pool != VK_NULL_HANDLE) {
            vkDestroyQueryPool(device, command_query_pool, NULL);
        }
        if (command_pool != VK_NULL_HANDLE) {
            vkDestroyCommandPool(device, command_pool, NULL);
        }
        vkDestroyDevice(device, NULL);
    }
    if (surface != VK_NULL_HANDLE && destroy_surface != NULL && instance != VK_NULL_HANDLE) {
        destroy_surface(instance, surface, NULL);
        report.surface_destroyed = true;
    }
    if (instance != VK_NULL_HANDLE) vkDestroyInstance(instance, NULL);

    free(swapchain_images);
    free(present_modes);
    free(surface_formats);
    free(physical_devices);
    free(instance_extensions);

    if (report.headless.advertised) {
        report.headless.passed = headless_created && report.surface_destroyed &&
                                 report.headless.result == VK_SUCCESS;
    }
    if (report.wsi.advertised) {
        report.wsi.passed = wsi_flow_passed && report.swapchain_created &&
                            report.swapchain_acquired && report.swapchain_presented &&
                            report.swapchain_destroyed && report.wsi.result == VK_SUCCESS;
        if (wsi_flow_passed && report.wsi.result == VK_SUBOPTIMAL_KHR &&
            report.swapchain_destroyed) {
            report.wsi.passed = true;
        }
    }
    report.ok = report.error_step == NULL && report.instance_created &&
                report.device_created && report.query_passed &&
                (!report.host_query_reset_advertised ||
                 (report.host_query_reset_executed &&
                  report.host_query_reset_passed)) &&
                report.queue_idle_passed && report.device_idle_passed &&
                (report.synchronization2.skipped || report.synchronization2.passed) &&
                (report.headless.skipped || report.headless.passed) &&
                (report.wsi.skipped || report.wsi.passed);
    if (!report.ok && report.error_step == NULL) {
        record_failure(&report, "smoke-invariant", VK_ERROR_UNKNOWN);
    }
    if (!report.ok) {
        fprintf(stderr, "skydnir-vulkan-p0-smoke: %s failed with VkResult %d\n",
                report.error_step != NULL ? report.error_step : "unknown",
                (int)report.error_result);
    }
    emit_json(&report);
    return report.ok ? EXIT_SUCCESS : EXIT_FAILURE;
}

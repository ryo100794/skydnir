import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ICD_SOURCE = ROOT / "docker-proot-setup" / "src" / "gpu" / "pdocker_vulkan_icd.c"


COMMAND_BUFFER_STACK_TEST_HELPER = r"""
static PdockerVkCommandPool g_test_command_pool_storage;

static PdockerVkCommandPool *ensure_test_command_pool(void) {
    VkCommandPool handle = pdocker_vk_command_pool_to_handle(&g_test_command_pool_storage);
    PdockerVkCommandPool *pool = command_pool_handle_lookup(handle);
    if (pool) return pool;
    memset(&g_test_command_pool_storage, 0, sizeof(g_test_command_pool_storage));
    command_pool_register(&g_test_command_pool_storage);
    return &g_test_command_pool_storage;
}

static void reset_test_command_buffer(
        PdockerVkCommandBuffer *cmd,
        uint64_t requested_feature_mask,
        uint64_t enabled_extension_mask) {
    if (!cmd) return;
    PdockerVkCommandBuffer *live = command_buffer_handle_lookup((VkCommandBuffer)cmd);
    if (live) {
        (void)command_buffer_unregister((VkCommandBuffer)cmd);
        clear_recorded_command_ops(live);
        command_buffer_destroy_record_vectors(live);
        command_buffer_destroy_descriptor_states(live);
    }
    memset(cmd, 0, sizeof(*cmd));
    set_loader_magic_value(cmd);
    cmd->requested_feature_mask = requested_feature_mask;
    cmd->enabled_extension_mask = enabled_extension_mask;
    command_buffer_register(ensure_test_command_pool(), cmd);
}
"""


@unittest.skipUnless(shutil.which("gcc"), "gcc is required for the ICD C contract harness")
class VulkanIcdFeatureChainTest(unittest.TestCase):
    def compile_and_run(self, source: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "icd_feature_chain_harness.c"
            exe = Path(tmpdir) / "icd_feature_chain_harness"
            src.write_text(source, encoding="utf-8")
            subprocess.run(
                [
                    "gcc",
                    "-O2",
                    "-Wall",
                    "-Wextra",
                    "-Wno-unused-function",
                    "-Wno-missing-field-initializers",
                    "-o",
                    str(exe),
                    str(src),
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            return subprocess.run(
                [str(exe)],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

    def test_features2_nested_pnext_chain_reaches_requested_feature_mask(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            int main(void) {{
                VkPhysicalDeviceFeatures2 features2;
                VkPhysicalDeviceVulkan11Features vulkan11;
                VkPhysicalDeviceVulkan12Features vulkan12;
                memset(&features2, 0, sizeof(features2));
                memset(&vulkan11, 0, sizeof(vulkan11));
                memset(&vulkan12, 0, sizeof(vulkan12));

                features2.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2;
                features2.features.shaderInt64 = VK_TRUE;
                vulkan11.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_1_FEATURES;
                vulkan11.storageBuffer16BitAccess = VK_TRUE;
                vulkan12.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES;
                vulkan12.storageBuffer8BitAccess = VK_TRUE;
                vulkan12.shaderInt8 = VK_TRUE;
                features2.pNext = &vulkan11;
                vulkan11.pNext = &vulkan12;

                VkDeviceCreateInfo create_info;
                memset(&create_info, 0, sizeof(create_info));
                create_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                create_info.pNext = &features2;

                const uint64_t expected =
                    PDOCKER_VK_FEATURE_SHADER_INT64 |
                    PDOCKER_VK_FEATURE_STORAGE_BUFFER_16 |
                    PDOCKER_VK_FEATURE_STORAGE_BUFFER_8 |
                    PDOCKER_VK_FEATURE_SHADER_INT8;
                const uint64_t actual = requested_feature_mask_from_device_create_info(&create_info);
                if (actual != expected) {{
                    fprintf(stderr, "requested mask 0x%016llx != expected 0x%016llx\\n",
                            (unsigned long long)actual,
                            (unsigned long long)expected);
                    return 2;
                }}
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stderr)


    def test_maintenance2_extension_exposes_noop_pnext_surface(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            static int extension_seen(
                    const VkExtensionProperties *extensions,
                    uint32_t count,
                    const char *name) {{
                for (uint32_t i = 0; i < count; ++i) {{
                    if (strcmp(extensions[i].extensionName, name) == 0) return 1;
                }}
                return 0;
            }}

            int main(void) {{
            #ifdef VK_KHR_MAINTENANCE_2_EXTENSION_NAME
                if (!device_extension_advertised_name(VK_KHR_MAINTENANCE_2_EXTENSION_NAME)) return 2;
                uint32_t count = 64;
                VkExtensionProperties extensions[64];
                memset(extensions, 0, sizeof(extensions));
                if (vkEnumerateDeviceExtensionProperties((VkPhysicalDevice)physical_device_for_instance(NULL), NULL, &count, extensions) != VK_SUCCESS) return 3;
                if (!extension_seen(extensions, count, VK_KHR_MAINTENANCE_2_EXTENSION_NAME)) return 4;

                const char *enabled[] = {{ VK_KHR_MAINTENANCE_2_EXTENSION_NAME }};
                VkDeviceCreateInfo device_info;
                memset(&device_info, 0, sizeof(device_info));
                device_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                device_info.enabledExtensionCount = 1;
                device_info.ppEnabledExtensionNames = enabled;
                if (validate_device_extensions(&device_info) != VK_SUCCESS) return 5;

                VkPhysicalDevicePointClippingProperties point_clipping;
                VkPhysicalDeviceProperties2 properties2;
                memset(&point_clipping, 0xff, sizeof(point_clipping));
                memset(&properties2, 0, sizeof(properties2));
                properties2.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2;
                properties2.pNext = &point_clipping;
                point_clipping.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_POINT_CLIPPING_PROPERTIES;
                point_clipping.pNext = NULL;
                vkGetPhysicalDeviceProperties2((VkPhysicalDevice)physical_device_for_instance(NULL), &properties2);
                if (point_clipping.pNext != NULL) return 6;
                if (point_clipping.pointClippingBehavior != VK_POINT_CLIPPING_BEHAVIOR_ALL_CLIP_PLANES) return 7;

                VkRenderPassCreateInfo render_pass_info;
                VkRenderPassInputAttachmentAspectCreateInfo aspect_info;
                VkInputAttachmentAspectReference aspect_ref;
                memset(&render_pass_info, 0, sizeof(render_pass_info));
                memset(&aspect_info, 0, sizeof(aspect_info));
                memset(&aspect_ref, 0, sizeof(aspect_ref));
                render_pass_info.sType = VK_STRUCTURE_TYPE_RENDER_PASS_CREATE_INFO;
                render_pass_info.pNext = &aspect_info;
                aspect_info.sType = VK_STRUCTURE_TYPE_RENDER_PASS_INPUT_ATTACHMENT_ASPECT_CREATE_INFO;
                aspect_info.aspectReferenceCount = 0;
                aspect_info.pAspectReferences = &aspect_ref;
                if (!render_pass_create_pnext_supported(&render_pass_info, NULL)) return 8;
                aspect_info.aspectReferenceCount = 1;
                if (render_pass_create_pnext_supported(&render_pass_info, NULL)) return 9;

                PdockerVkImage image;
                VkImageViewCreateInfo view_info;
                VkImageViewUsageCreateInfo view_usage;
                memset(&image, 0, sizeof(image));
                memset(&view_info, 0, sizeof(view_info));
                memset(&view_usage, 0, sizeof(view_usage));
                image.usage = VK_IMAGE_USAGE_SAMPLED_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT;
                view_info.sType = VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO;
                view_info.pNext = &view_usage;
                view_usage.sType = VK_STRUCTURE_TYPE_IMAGE_VIEW_USAGE_CREATE_INFO;
                view_usage.usage = image.usage;
                if (validate_image_view_pnext_for_transport(&view_info, &image) != VK_SUCCESS) return 10;
                view_usage.usage = VK_IMAGE_USAGE_SAMPLED_BIT;
                if (validate_image_view_pnext_for_transport(&view_info, &image) != VK_ERROR_FEATURE_NOT_PRESENT) return 11;
            #endif
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stderr)


    def test_subpass_merge_feedback_extension_exposes_query_only_surface(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            static int extension_seen(
                    const VkExtensionProperties *extensions,
                    uint32_t count,
                    const char *name) {{
                for (uint32_t i = 0; i < count; ++i) {{
                    if (strcmp(extensions[i].extensionName, name) == 0) return 1;
                }}
                return 0;
            }}

            int main(void) {{
            #ifdef VK_EXT_SUBPASS_MERGE_FEEDBACK_EXTENSION_NAME
                if (!device_extension_advertised_name(VK_EXT_SUBPASS_MERGE_FEEDBACK_EXTENSION_NAME)) return 2;
                uint32_t count = 64;
                VkExtensionProperties extensions[64];
                memset(extensions, 0, sizeof(extensions));
                if (vkEnumerateDeviceExtensionProperties((VkPhysicalDevice)physical_device_for_instance(NULL), NULL, &count, extensions) != VK_SUCCESS) return 3;
                if (!extension_seen(extensions, count, VK_EXT_SUBPASS_MERGE_FEEDBACK_EXTENSION_NAME)) return 4;

                VkPhysicalDeviceSubpassMergeFeedbackFeaturesEXT feedback_features;
                VkPhysicalDeviceFeatures2 features2;
                memset(&feedback_features, 0, sizeof(feedback_features));
                memset(&features2, 0, sizeof(features2));
                features2.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2;
                features2.pNext = &feedback_features;
                feedback_features.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SUBPASS_MERGE_FEEDBACK_FEATURES_EXT;
                vkGetPhysicalDeviceFeatures2((VkPhysicalDevice)physical_device_for_instance(NULL), &features2);
                if (feedback_features.subpassMergeFeedback != VK_TRUE) return 5;

                const char *enabled[] = {{ VK_EXT_SUBPASS_MERGE_FEEDBACK_EXTENSION_NAME }};
                VkDeviceCreateInfo device_info;
                memset(&device_info, 0, sizeof(device_info));
                device_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                device_info.enabledExtensionCount = 1;
                device_info.ppEnabledExtensionNames = enabled;
                feedback_features.subpassMergeFeedback = VK_TRUE;
                device_info.pNext = &feedback_features;
                if (validate_device_extensions(&device_info) != VK_SUCCESS) return 6;
                if (validate_device_feature_requests(&device_info) != VK_SUCCESS) return 7;
            #endif
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stderr)


    def test_maintenance3_extension_exposes_descriptor_support_query(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            static int extension_seen(
                    const VkExtensionProperties *extensions,
                    uint32_t count,
                    const char *name) {{
                for (uint32_t i = 0; i < count; ++i) {{
                    if (strcmp(extensions[i].extensionName, name) == 0) return 1;
                }}
                return 0;
            }}

            int main(void) {{
            #ifdef VK_KHR_MAINTENANCE_3_EXTENSION_NAME
                if (!device_extension_advertised_name(VK_KHR_MAINTENANCE_3_EXTENSION_NAME)) return 2;
                uint32_t count = 64;
                VkExtensionProperties extensions[64];
                memset(extensions, 0, sizeof(extensions));
                if (vkEnumerateDeviceExtensionProperties((VkPhysicalDevice)physical_device_for_instance(NULL), NULL, &count, extensions) != VK_SUCCESS) return 3;
                if (!extension_seen(extensions, count, VK_KHR_MAINTENANCE_3_EXTENSION_NAME)) return 4;

                const char *enabled[] = {{ VK_KHR_MAINTENANCE_3_EXTENSION_NAME }};
                VkDeviceCreateInfo create_info;
                memset(&create_info, 0, sizeof(create_info));
                create_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                create_info.enabledExtensionCount = 1;
                create_info.ppEnabledExtensionNames = enabled;
                if (validate_device_extensions(&create_info) != VK_SUCCESS) return 5;

                PFN_vkVoidFunction raw = proc_address("vkGetDescriptorSetLayoutSupportKHR");
                if (raw == NULL) return 6;
                if (raw != (PFN_vkVoidFunction)vkGetDescriptorSetLayoutSupport) return 7;

                VkPhysicalDeviceMaintenance3Properties maintenance3;
                VkPhysicalDeviceProperties2 properties2;
                memset(&maintenance3, 0, sizeof(maintenance3));
                memset(&properties2, 0, sizeof(properties2));
                properties2.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2;
                properties2.pNext = &maintenance3;
                maintenance3.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MAINTENANCE_3_PROPERTIES;
                vkGetPhysicalDeviceProperties2((VkPhysicalDevice)physical_device_for_instance(NULL), &properties2);
                if (maintenance3.maxPerSetDescriptors == 0) return 8;
                if (maintenance3.maxMemoryAllocationSize == 0) return 9;

                VkDescriptorSetLayoutBinding binding;
                VkDescriptorSetLayoutCreateInfo layout_info;
                VkDescriptorSetLayoutSupport support;
                memset(&binding, 0, sizeof(binding));
                memset(&layout_info, 0, sizeof(layout_info));
                memset(&support, 0, sizeof(support));
                binding.binding = 0;
                binding.descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
                binding.descriptorCount = 1;
                binding.stageFlags = VK_SHADER_STAGE_COMPUTE_BIT;
                layout_info.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO;
                layout_info.bindingCount = 1;
                layout_info.pBindings = &binding;
                support.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_SUPPORT;
                ((PFN_vkGetDescriptorSetLayoutSupportKHR)raw)(
                    VK_NULL_HANDLE, &layout_info, &support);
                if (support.supported != VK_TRUE) return 10;
            #endif
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stderr)






    def test_maintenance5_extension_exposes_query_and_index_buffer2_aliases(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"
            {COMMAND_BUFFER_STACK_TEST_HELPER}

            static int extension_seen(
                    const VkExtensionProperties *extensions,
                    uint32_t count,
                    const char *name) {{
                for (uint32_t i = 0; i < count; ++i) {{
                    if (strcmp(extensions[i].extensionName, name) == 0) return 1;
                }}
                return 0;
            }}

            int main(void) {{
            #ifdef VK_KHR_MAINTENANCE_5_EXTENSION_NAME
                if (!device_extension_advertised_name(VK_KHR_MAINTENANCE_5_EXTENSION_NAME)) return 2;
                uint32_t count = 64;
                VkExtensionProperties extensions[64];
                memset(extensions, 0, sizeof(extensions));
                if (vkEnumerateDeviceExtensionProperties((VkPhysicalDevice)physical_device_for_instance(NULL), NULL, &count, extensions) != VK_SUCCESS) return 3;
                if (!extension_seen(extensions, count, VK_KHR_MAINTENANCE_5_EXTENSION_NAME)) return 4;

                const char *enabled[] = {{ VK_KHR_MAINTENANCE_5_EXTENSION_NAME }};
                VkDeviceCreateInfo device_info;
                memset(&device_info, 0, sizeof(device_info));
                device_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                device_info.enabledExtensionCount = 1;
                device_info.ppEnabledExtensionNames = enabled;
                if (validate_device_extensions(&device_info) != VK_SUCCESS) return 5;

                if (proc_address("vkGetImageSubresourceLayout2KHR") !=
                    (PFN_vkVoidFunction)vkGetImageSubresourceLayout2) return 6;
                if (proc_address("vkGetDeviceImageSubresourceLayoutKHR") !=
                    (PFN_vkVoidFunction)vkGetDeviceImageSubresourceLayout) return 7;
                if (proc_address("vkGetRenderingAreaGranularityKHR") !=
                    (PFN_vkVoidFunction)vkGetRenderingAreaGranularity) return 8;
                if (proc_address("vkCmdBindIndexBuffer2KHR") !=
                    (PFN_vkVoidFunction)vkCmdBindIndexBuffer2KHR) return 9;
                if (proc_address("vkGetImageSubresourceLayout2EXT") != NULL) return 10;

                VkDevice m5_device = VK_NULL_HANDLE;
                if (vkCreateDevice((VkPhysicalDevice)&g_device, &device_info, NULL, &m5_device) != VK_SUCCESS) return 40;

                VkRenderingAreaInfo area;
                VkExtent2D granularity;
                memset(&area, 0, sizeof(area));
                memset(&granularity, 0, sizeof(granularity));
                area.sType = VK_STRUCTURE_TYPE_RENDERING_AREA_INFO;
                ((PFN_vkGetRenderingAreaGranularityKHR)proc_address("vkGetRenderingAreaGranularityKHR"))(
                    m5_device, &area, &granularity);
                if (granularity.width != 1 || granularity.height != 1) return 11;

                VkPhysicalDeviceMaintenance5Properties maintenance5_props;
                VkPhysicalDeviceProperties2 properties2;
                memset(&maintenance5_props, 0xff, sizeof(maintenance5_props));
                memset(&properties2, 0, sizeof(properties2));
                properties2.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2;
                properties2.pNext = &maintenance5_props;
                maintenance5_props.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MAINTENANCE_5_PROPERTIES;
                maintenance5_props.pNext = NULL;
                vkGetPhysicalDeviceProperties2((VkPhysicalDevice)physical_device_for_instance(NULL), &properties2);
                if (maintenance5_props.sType != VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MAINTENANCE_5_PROPERTIES ||
                    maintenance5_props.pNext != NULL ||
                    maintenance5_props.earlyFragmentMultisampleCoverageAfterSampleCounting != VK_FALSE ||
                    maintenance5_props.earlyFragmentSampleMaskTestBeforeSampleCounting != VK_FALSE ||
                    maintenance5_props.depthStencilSwizzleOneSupport != VK_FALSE ||
                    maintenance5_props.polygonModePointSize != VK_FALSE ||
                    maintenance5_props.nonStrictSinglePixelWideLinesUseParallelogram != VK_FALSE ||
                    maintenance5_props.nonStrictWideLinesUseParallelogram != VK_FALSE) return 12;

                VkPipelineCreateFlags2CreateInfo flags2;
                memset(&flags2, 0, sizeof(flags2));
                flags2.sType = VK_STRUCTURE_TYPE_PIPELINE_CREATE_FLAGS_2_CREATE_INFO;
                if (validate_and_fill_pipeline_feedback_pnext(
                        "unit-maintenance5-flags2", &flags2, 1u, false, 0) == VK_SUCCESS) return 13;
                if (validate_and_fill_pipeline_feedback_pnext(
                        "unit-maintenance5-flags2", &flags2, 1u, false,
                        PDOCKER_VK_DEVICE_EXT_KHR_MAINTENANCE_5) != VK_SUCCESS) return 14;
                flags2.flags = (VkPipelineCreateFlags2)1;
                if (validate_and_fill_pipeline_feedback_pnext(
                        "unit-maintenance5-flags2", &flags2, 1u, false,
                        PDOCKER_VK_DEVICE_EXT_KHR_MAINTENANCE_5) == VK_SUCCESS) return 15;

                ((PFN_vkCmdBindIndexBuffer2KHR)proc_address("vkCmdBindIndexBuffer2KHR"))(
                    VK_NULL_HANDLE, VK_NULL_HANDLE, 0, VK_WHOLE_SIZE, VK_INDEX_TYPE_UINT32);

                VkBufferUsageFlags2CreateInfo usage2;
                VkBufferCreateInfo buffer_info;
                memset(&usage2, 0, sizeof(usage2));
                memset(&buffer_info, 0, sizeof(buffer_info));
                usage2.sType = VK_STRUCTURE_TYPE_BUFFER_USAGE_FLAGS_2_CREATE_INFO;
                usage2.usage = VK_BUFFER_USAGE_STORAGE_BUFFER_BIT;
                buffer_info.sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO;
                buffer_info.pNext = &usage2;
                buffer_info.size = 64;
                buffer_info.usage = VK_BUFFER_USAGE_STORAGE_BUFFER_BIT;
                buffer_info.sharingMode = VK_SHARING_MODE_EXCLUSIVE;

                VkDeviceCreateInfo no_m5_device_info;
                memset(&no_m5_device_info, 0, sizeof(no_m5_device_info));
                no_m5_device_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                VkDevice no_m5_device = VK_NULL_HANDLE;
                if (vkCreateDevice((VkPhysicalDevice)&g_device, &no_m5_device_info, NULL, &no_m5_device) != VK_SUCCESS) return 41;
                VkBuffer buffer = VK_NULL_HANDLE;
                if (vkCreateBuffer(no_m5_device, &buffer_info, NULL, &buffer) != VK_ERROR_FEATURE_NOT_PRESENT) return 42;
                if (buffer != VK_NULL_HANDLE) return 43;
                vkDestroyDevice(no_m5_device, NULL);

                if (vkCreateBuffer(m5_device, &buffer_info, NULL, &buffer) != VK_SUCCESS || buffer == VK_NULL_HANDLE) return 45;
                vkDestroyBuffer(m5_device, buffer, NULL);
                vkDestroyDevice(m5_device, NULL);

                PdockerVkMemory index_memory;
                PdockerVkBuffer index_buffer;
                memset(&index_memory, 0, sizeof(index_memory));
                memset(&index_buffer, 0, sizeof(index_buffer));
                index_memory.size = 64;
                index_buffer.size = 64;
                index_buffer.usage = VK_BUFFER_USAGE_INDEX_BUFFER_BIT;
                index_buffer.memory = &index_memory;
                buffer_register(&index_buffer);
                PdockerVkCommandBuffer cmd;
                reset_test_command_buffer(&cmd, 0, 0);
                vkCmdBindIndexBuffer2KHR((VkCommandBuffer)&cmd,
                                         pdocker_vk_buffer_to_handle(&index_buffer),
                                         0, 4, VK_INDEX_TYPE_UINT16);
                if (!cmd.recording_failed ||
                    strcmp(cmd.recording_failure_reason, "graphics-index-buffer2-maintenance5-extension-disabled") != 0) return 46;

                reset_test_command_buffer(&cmd, 0, 0);
                cmd.enabled_extension_mask = PDOCKER_VK_DEVICE_EXT_KHR_MAINTENANCE_5;
                vkCmdBindIndexBuffer2KHR((VkCommandBuffer)&cmd,
                                         pdocker_vk_buffer_to_handle(&index_buffer),
                                         0, 4, VK_INDEX_TYPE_UINT16);
                if (cmd.recording_failed || cmd.graphics_index_buffer_snapshot_count != 1) return 47;
                free(cmd.graphics_index_buffer_snapshots);
                free(cmd.graphics_command_ops);
            #endif
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_host_image_copy_ext_extension_is_not_advertised_without_transport(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            static int extension_seen(
                    const VkExtensionProperties *extensions,
                    uint32_t count,
                    const char *name) {{
                for (uint32_t i = 0; i < count; ++i) {{
                    if (strcmp(extensions[i].extensionName, name) == 0) return 1;
                }}
                return 0;
            }}

            int main(void) {{
            #ifdef VK_EXT_HOST_IMAGE_COPY_EXTENSION_NAME
                if (device_extension_advertised_name(VK_EXT_HOST_IMAGE_COPY_EXTENSION_NAME)) return 2;
                uint32_t count = 64;
                VkExtensionProperties extensions[64];
                memset(extensions, 0, sizeof(extensions));
                if (vkEnumerateDeviceExtensionProperties((VkPhysicalDevice)physical_device_for_instance(NULL), NULL, &count, extensions) != VK_SUCCESS) return 3;
                if (extension_seen(extensions, count, VK_EXT_HOST_IMAGE_COPY_EXTENSION_NAME)) return 4;

                const char *enabled[] = {{ VK_EXT_HOST_IMAGE_COPY_EXTENSION_NAME }};
                VkDeviceCreateInfo device_info;
                memset(&device_info, 0, sizeof(device_info));
                device_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                device_info.enabledExtensionCount = 1;
                device_info.ppEnabledExtensionNames = enabled;
                if (validate_device_extensions(&device_info) != VK_ERROR_EXTENSION_NOT_PRESENT) return 5;

                if (proc_address("vkCopyMemoryToImageEXT") != NULL) return 6;
                if (proc_address("vkCopyImageToMemoryEXT") != NULL) return 7;
                if (proc_address("vkCopyImageToImageEXT") != NULL) return 8;
                if (proc_address("vkTransitionImageLayoutEXT") != NULL) return 9;
                if (proc_address("vkGetImageSubresourceLayout2EXT") != NULL) return 10;

                VkPhysicalDeviceHostImageCopyFeatures features;
                memset(&features, 0xff, sizeof(features));
                features.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_HOST_IMAGE_COPY_FEATURES;
                features.pNext = NULL;
                fill_pnext_features(&features);
                if (features.sType != VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_HOST_IMAGE_COPY_FEATURES ||
                    features.pNext != NULL || features.hostImageCopy != VK_FALSE) return 11;

                device_info.enabledExtensionCount = 0;
                device_info.ppEnabledExtensionNames = NULL;
                device_info.pNext = &features;
                features.hostImageCopy = VK_TRUE;
                if (validate_device_feature_requests(&device_info) == VK_SUCCESS) return 12;
                features.hostImageCopy = VK_FALSE;
                if (validate_device_feature_requests(&device_info) != VK_SUCCESS) return 13;

                VkImageLayout src_layouts[2] = {{ VK_IMAGE_LAYOUT_GENERAL, VK_IMAGE_LAYOUT_GENERAL }};
                VkImageLayout dst_layouts[2] = {{ VK_IMAGE_LAYOUT_GENERAL, VK_IMAGE_LAYOUT_GENERAL }};
                VkPhysicalDeviceHostImageCopyProperties props;
                memset(&props, 0xff, sizeof(props));
                props.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_HOST_IMAGE_COPY_PROPERTIES;
                props.pNext = NULL;
                props.copySrcLayoutCount = 2;
                props.pCopySrcLayouts = src_layouts;
                props.copyDstLayoutCount = 2;
                props.pCopyDstLayouts = dst_layouts;
                fill_pnext_properties(&props);
                if (props.sType != VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_HOST_IMAGE_COPY_PROPERTIES ||
                    props.pNext != NULL ||
                    props.copySrcLayoutCount != 0 || props.copyDstLayoutCount != 0 ||
                    props.pCopySrcLayouts != src_layouts || props.pCopyDstLayouts != dst_layouts ||
                    props.identicalMemoryTypeRequirements != VK_FALSE) return 14;

                VkCopyMemoryToImageInfo memory_to_image;
                memset(&memory_to_image, 0, sizeof(memory_to_image));
                memory_to_image.sType = VK_STRUCTURE_TYPE_COPY_MEMORY_TO_IMAGE_INFO;
                if (vkCopyMemoryToImageEXT(VK_NULL_HANDLE, &memory_to_image) != VK_ERROR_FEATURE_NOT_PRESENT) return 15;

                VkCopyImageToMemoryInfo image_to_memory;
                memset(&image_to_memory, 0, sizeof(image_to_memory));
                image_to_memory.sType = VK_STRUCTURE_TYPE_COPY_IMAGE_TO_MEMORY_INFO;
                if (vkCopyImageToMemoryEXT(VK_NULL_HANDLE, &image_to_memory) != VK_ERROR_FEATURE_NOT_PRESENT) return 16;

                VkCopyImageToImageInfo image_to_image;
                memset(&image_to_image, 0, sizeof(image_to_image));
                image_to_image.sType = VK_STRUCTURE_TYPE_COPY_IMAGE_TO_IMAGE_INFO;
                if (vkCopyImageToImageEXT(VK_NULL_HANDLE, &image_to_image) != VK_ERROR_FEATURE_NOT_PRESENT) return 17;

                if (vkTransitionImageLayoutEXT(VK_NULL_HANDLE, 0, NULL) != VK_ERROR_FEATURE_NOT_PRESENT) return 18;
            #endif
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_sampler_ycbcr_conversion_khr_extension_is_not_advertised_without_transport(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            static int extension_seen(
                    const VkExtensionProperties *extensions,
                    uint32_t count,
                    const char *name) {{
                for (uint32_t i = 0; i < count; ++i) {{
                    if (strcmp(extensions[i].extensionName, name) == 0) return 1;
                }}
                return 0;
            }}

            int main(void) {{
            #ifdef VK_KHR_SAMPLER_YCBCR_CONVERSION_EXTENSION_NAME
                if (device_extension_advertised_name(VK_KHR_SAMPLER_YCBCR_CONVERSION_EXTENSION_NAME)) return 2;
                uint32_t count = 64;
                VkExtensionProperties extensions[64];
                memset(extensions, 0, sizeof(extensions));
                if (vkEnumerateDeviceExtensionProperties((VkPhysicalDevice)physical_device_for_instance(NULL), NULL, &count, extensions) != VK_SUCCESS) return 3;
                if (extension_seen(extensions, count, VK_KHR_SAMPLER_YCBCR_CONVERSION_EXTENSION_NAME)) return 4;

                const char *enabled[] = {{ VK_KHR_SAMPLER_YCBCR_CONVERSION_EXTENSION_NAME }};
                VkDeviceCreateInfo device_info;
                memset(&device_info, 0, sizeof(device_info));
                device_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                device_info.enabledExtensionCount = 1;
                device_info.ppEnabledExtensionNames = enabled;
                if (validate_device_extensions(&device_info) != VK_ERROR_EXTENSION_NOT_PRESENT) return 5;

                if (proc_address("vkCreateSamplerYcbcrConversionKHR") != NULL) return 6;
                if (proc_address("vkDestroySamplerYcbcrConversionKHR") != NULL) return 7;

                VkPhysicalDeviceSamplerYcbcrConversionFeatures features;
                memset(&features, 0xff, sizeof(features));
                features.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SAMPLER_YCBCR_CONVERSION_FEATURES;
                features.pNext = NULL;
                fill_pnext_features(&features);
                if (features.sType != VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SAMPLER_YCBCR_CONVERSION_FEATURES ||
                    features.pNext != NULL ||
                    features.samplerYcbcrConversion != VK_FALSE) return 8;

                device_info.enabledExtensionCount = 0;
                device_info.ppEnabledExtensionNames = NULL;
                device_info.pNext = &features;
                features.samplerYcbcrConversion = VK_TRUE;
                if (validate_device_feature_requests(&device_info) == VK_SUCCESS) return 9;
                features.samplerYcbcrConversion = VK_FALSE;
                if (validate_device_feature_requests(&device_info) != VK_SUCCESS) return 10;

                VkSamplerYcbcrConversion conversion = (VkSamplerYcbcrConversion)0x1234u;
                VkSamplerYcbcrConversionCreateInfo create_info;
                memset(&create_info, 0, sizeof(create_info));
                create_info.sType = VK_STRUCTURE_TYPE_SAMPLER_YCBCR_CONVERSION_CREATE_INFO;
                create_info.format = VK_FORMAT_R8G8B8A8_UNORM;
                create_info.ycbcrModel = VK_SAMPLER_YCBCR_MODEL_CONVERSION_RGB_IDENTITY;
                create_info.ycbcrRange = VK_SAMPLER_YCBCR_RANGE_ITU_FULL;
                create_info.components.r = VK_COMPONENT_SWIZZLE_IDENTITY;
                create_info.components.g = VK_COMPONENT_SWIZZLE_IDENTITY;
                create_info.components.b = VK_COMPONENT_SWIZZLE_IDENTITY;
                create_info.components.a = VK_COMPONENT_SWIZZLE_IDENTITY;
                create_info.xChromaOffset = VK_CHROMA_LOCATION_COSITED_EVEN;
                create_info.yChromaOffset = VK_CHROMA_LOCATION_COSITED_EVEN;
                create_info.chromaFilter = VK_FILTER_NEAREST;
                create_info.forceExplicitReconstruction = VK_FALSE;
                if (vkCreateSamplerYcbcrConversion(VK_NULL_HANDLE, &create_info, NULL, &conversion) != VK_ERROR_FEATURE_NOT_PRESENT) return 11;
                if (conversion != VK_NULL_HANDLE) return 12;
                vkDestroySamplerYcbcrConversion(VK_NULL_HANDLE, VK_NULL_HANDLE, NULL);
            #endif
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_buffer_device_address_khr_extension_is_not_advertised_without_transport(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            static int extension_seen(
                    const VkExtensionProperties *extensions,
                    uint32_t count,
                    const char *name) {{
                for (uint32_t i = 0; i < count; ++i) {{
                    if (strcmp(extensions[i].extensionName, name) == 0) return 1;
                }}
                return 0;
            }}

            int main(void) {{
            #ifdef VK_KHR_BUFFER_DEVICE_ADDRESS_EXTENSION_NAME
                if (device_extension_advertised_name(VK_KHR_BUFFER_DEVICE_ADDRESS_EXTENSION_NAME)) return 2;
                uint32_t count = 64;
                VkExtensionProperties extensions[64];
                memset(extensions, 0, sizeof(extensions));
                if (vkEnumerateDeviceExtensionProperties((VkPhysicalDevice)physical_device_for_instance(NULL), NULL, &count, extensions) != VK_SUCCESS) return 3;
                if (extension_seen(extensions, count, VK_KHR_BUFFER_DEVICE_ADDRESS_EXTENSION_NAME)) return 4;

                const char *enabled[] = {{ VK_KHR_BUFFER_DEVICE_ADDRESS_EXTENSION_NAME }};
                VkDeviceCreateInfo device_info;
                memset(&device_info, 0, sizeof(device_info));
                device_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                device_info.enabledExtensionCount = 1;
                device_info.ppEnabledExtensionNames = enabled;
                if (validate_device_extensions(&device_info) != VK_ERROR_EXTENSION_NOT_PRESENT) return 5;

                if (proc_address("vkGetBufferDeviceAddressKHR") != NULL) return 6;
                if (proc_address("vkGetBufferOpaqueCaptureAddressKHR") != NULL) return 7;
                if (proc_address("vkGetDeviceMemoryOpaqueCaptureAddressKHR") != NULL) return 8;
                if (proc_address("vkGetBufferDeviceAddressEXT") != NULL) return 9;

                VkPhysicalDeviceBufferDeviceAddressFeatures features;
                memset(&features, 0xff, sizeof(features));
                features.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_BUFFER_DEVICE_ADDRESS_FEATURES;
                features.pNext = NULL;
                fill_pnext_features(&features);
                if (features.sType != VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_BUFFER_DEVICE_ADDRESS_FEATURES ||
                    features.pNext != NULL ||
                    features.bufferDeviceAddress != VK_FALSE ||
                    features.bufferDeviceAddressCaptureReplay != VK_FALSE ||
                    features.bufferDeviceAddressMultiDevice != VK_FALSE) return 10;

                device_info.enabledExtensionCount = 0;
                device_info.ppEnabledExtensionNames = NULL;
                device_info.pNext = &features;
                features.bufferDeviceAddress = VK_TRUE;
                if (validate_device_feature_requests(&device_info) == VK_SUCCESS) return 11;
                features.bufferDeviceAddress = VK_FALSE;
                features.bufferDeviceAddressCaptureReplay = VK_TRUE;
                if (validate_device_feature_requests(&device_info) == VK_SUCCESS) return 12;
                features.bufferDeviceAddressCaptureReplay = VK_FALSE;
                features.bufferDeviceAddressMultiDevice = VK_TRUE;
                if (validate_device_feature_requests(&device_info) == VK_SUCCESS) return 13;
                features.bufferDeviceAddressMultiDevice = VK_FALSE;
                if (validate_device_feature_requests(&device_info) != VK_SUCCESS) return 14;

                VkMemoryAllocateFlagsInfo flags;
                memset(&flags, 0, sizeof(flags));
                flags.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_FLAGS_INFO;
                flags.flags = 0;
                flags.deviceMask = 0;
                if (validate_memory_allocate_pnext(VK_NULL_HANDLE, &flags) != VK_SUCCESS) return 15;
                flags.flags = VK_MEMORY_ALLOCATE_DEVICE_ADDRESS_BIT_KHR;
                if (validate_memory_allocate_pnext(VK_NULL_HANDLE, &flags) == VK_SUCCESS) return 16;
                flags.flags = 0;
                flags.deviceMask = 2;
                if (validate_memory_allocate_pnext(VK_NULL_HANDLE, &flags) == VK_SUCCESS) return 17;

            #ifdef VK_BUFFER_USAGE_SHADER_DEVICE_ADDRESS_BIT
                VkBufferCreateInfo buffer_create;
                memset(&buffer_create, 0, sizeof(buffer_create));
                buffer_create.sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO;
                buffer_create.size = 4096;
                buffer_create.usage = VK_BUFFER_USAGE_STORAGE_BUFFER_BIT | VK_BUFFER_USAGE_SHADER_DEVICE_ADDRESS_BIT;
                VkBuffer created_buffer = VK_NULL_HANDLE;
                if (vkCreateBuffer(VK_NULL_HANDLE, &buffer_create, NULL, &created_buffer) != VK_ERROR_FEATURE_NOT_PRESENT) return 18;
                if (created_buffer != VK_NULL_HANDLE) return 19;

                VkBufferUsageFlags2CreateInfo usage2;
                memset(&usage2, 0, sizeof(usage2));
                usage2.sType = VK_STRUCTURE_TYPE_BUFFER_USAGE_FLAGS_2_CREATE_INFO;
                usage2.usage = (VkBufferUsageFlags2)VK_BUFFER_USAGE_STORAGE_BUFFER_BIT |
                    (VkBufferUsageFlags2)VK_BUFFER_USAGE_SHADER_DEVICE_ADDRESS_BIT;
                buffer_create.pNext = &usage2;
                buffer_create.usage = VK_BUFFER_USAGE_STORAGE_BUFFER_BIT;
                if (validate_buffer_create_pnext(&buffer_create) == VK_SUCCESS) return 20;
                buffer_create.pNext = NULL;
            #endif

                VkBufferDeviceAddressInfo buffer_info;
                memset(&buffer_info, 0, sizeof(buffer_info));
                buffer_info.sType = VK_STRUCTURE_TYPE_BUFFER_DEVICE_ADDRESS_INFO;
                buffer_info.buffer = VK_NULL_HANDLE;
                if (vkGetBufferDeviceAddress(VK_NULL_HANDLE, &buffer_info) != 0) return 21;
                if (vkGetBufferOpaqueCaptureAddress(VK_NULL_HANDLE, &buffer_info) != 0) return 22;

                VkDeviceMemoryOpaqueCaptureAddressInfo memory_info;
                memset(&memory_info, 0, sizeof(memory_info));
                memory_info.sType = VK_STRUCTURE_TYPE_DEVICE_MEMORY_OPAQUE_CAPTURE_ADDRESS_INFO;
                memory_info.memory = VK_NULL_HANDLE;
                if (vkGetDeviceMemoryOpaqueCaptureAddress(VK_NULL_HANDLE, &memory_info) != 0) return 23;
            #endif
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_buffer_device_address_ext_extension_is_not_advertised_without_transport(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            static int extension_seen(
                    const VkExtensionProperties *extensions,
                    uint32_t count,
                    const char *name) {{
                for (uint32_t i = 0; i < count; ++i) {{
                    if (strcmp(extensions[i].extensionName, name) == 0) return 1;
                }}
                return 0;
            }}

            int main(void) {{
            #ifdef VK_EXT_BUFFER_DEVICE_ADDRESS_EXTENSION_NAME
                if (device_extension_advertised_name(VK_EXT_BUFFER_DEVICE_ADDRESS_EXTENSION_NAME)) return 2;
                uint32_t count = 64;
                VkExtensionProperties extensions[64];
                memset(extensions, 0, sizeof(extensions));
                if (vkEnumerateDeviceExtensionProperties((VkPhysicalDevice)physical_device_for_instance(NULL), NULL, &count, extensions) != VK_SUCCESS) return 3;
                if (extension_seen(extensions, count, VK_EXT_BUFFER_DEVICE_ADDRESS_EXTENSION_NAME)) return 4;

                const char *enabled[] = {{ VK_EXT_BUFFER_DEVICE_ADDRESS_EXTENSION_NAME }};
                VkDeviceCreateInfo device_info;
                memset(&device_info, 0, sizeof(device_info));
                device_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                device_info.enabledExtensionCount = 1;
                device_info.ppEnabledExtensionNames = enabled;
                if (validate_device_extensions(&device_info) != VK_ERROR_EXTENSION_NOT_PRESENT) return 5;

                if (proc_address("vkGetBufferDeviceAddressEXT") != NULL) return 6;

                VkPhysicalDeviceBufferDeviceAddressFeaturesEXT features;
                memset(&features, 0xff, sizeof(features));
                features.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_BUFFER_DEVICE_ADDRESS_FEATURES_EXT;
                features.pNext = NULL;
                fill_pnext_features(&features);
                if (features.sType != VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_BUFFER_DEVICE_ADDRESS_FEATURES_EXT ||
                    features.pNext != NULL ||
                    features.bufferDeviceAddress != VK_FALSE ||
                    features.bufferDeviceAddressCaptureReplay != VK_FALSE ||
                    features.bufferDeviceAddressMultiDevice != VK_FALSE) return 7;

                device_info.enabledExtensionCount = 0;
                device_info.ppEnabledExtensionNames = NULL;
                device_info.pNext = &features;
                features.bufferDeviceAddress = VK_TRUE;
                if (validate_device_feature_requests(&device_info) == VK_SUCCESS) return 8;
                features.bufferDeviceAddress = VK_FALSE;
                features.bufferDeviceAddressCaptureReplay = VK_TRUE;
                if (validate_device_feature_requests(&device_info) == VK_SUCCESS) return 9;
                features.bufferDeviceAddressCaptureReplay = VK_FALSE;
                features.bufferDeviceAddressMultiDevice = VK_TRUE;
                if (validate_device_feature_requests(&device_info) == VK_SUCCESS) return 10;
                features.bufferDeviceAddressMultiDevice = VK_FALSE;
                if (validate_device_feature_requests(&device_info) != VK_SUCCESS) return 11;

                VkBufferDeviceAddressCreateInfoEXT address;
                VkBufferCreateInfo buffer_info;
                memset(&address, 0, sizeof(address));
                memset(&buffer_info, 0, sizeof(buffer_info));
                address.sType = VK_STRUCTURE_TYPE_BUFFER_DEVICE_ADDRESS_CREATE_INFO_EXT;
                address.deviceAddress = 0;
                buffer_info.sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO;
                buffer_info.pNext = &address;
                buffer_info.size = 4096;
                buffer_info.usage = VK_BUFFER_USAGE_STORAGE_BUFFER_BIT;
                if (validate_buffer_create_pnext(&buffer_info) != VK_SUCCESS) return 12;
                address.deviceAddress = 0x1000u;
                if (validate_buffer_create_pnext(&buffer_info) == VK_SUCCESS) return 13;

                VkBufferDeviceAddressInfo query;
                memset(&query, 0, sizeof(query));
                query.sType = VK_STRUCTURE_TYPE_BUFFER_DEVICE_ADDRESS_INFO;
                query.buffer = VK_NULL_HANDLE;
                if (vkGetBufferDeviceAddress(VK_NULL_HANDLE, &query) != 0) return 14;
            #endif
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_device_group_extension_exposes_single_device_noop_aliases(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            static int extension_seen(
                    const VkExtensionProperties *extensions,
                    uint32_t count,
                    const char *name) {{
                for (uint32_t i = 0; i < count; ++i) {{
                    if (strcmp(extensions[i].extensionName, name) == 0) return 1;
                }}
                return 0;
            }}

            int main(void) {{
            #ifdef VK_KHR_DEVICE_GROUP_EXTENSION_NAME
                if (!device_extension_advertised_name(VK_KHR_DEVICE_GROUP_EXTENSION_NAME)) return 2;
                uint32_t count = 64;
                VkExtensionProperties extensions[64];
                memset(extensions, 0, sizeof(extensions));
                if (vkEnumerateDeviceExtensionProperties((VkPhysicalDevice)physical_device_for_instance(NULL), NULL, &count, extensions) != VK_SUCCESS) return 3;
                if (!extension_seen(extensions, count, VK_KHR_DEVICE_GROUP_EXTENSION_NAME)) return 4;

                const char *enabled[] = {{ VK_KHR_DEVICE_GROUP_EXTENSION_NAME }};
                VkDeviceCreateInfo device_info;
                memset(&device_info, 0, sizeof(device_info));
                device_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                device_info.enabledExtensionCount = 1;
                device_info.ppEnabledExtensionNames = enabled;
                if (validate_device_extensions(&device_info) != VK_SUCCESS) return 5;

                if (proc_address("vkGetDeviceGroupPeerMemoryFeaturesKHR") !=
                    (PFN_vkVoidFunction)vkGetDeviceGroupPeerMemoryFeatures) return 6;
                if (proc_address("vkCmdSetDeviceMaskKHR") !=
                    (PFN_vkVoidFunction)vkCmdSetDeviceMask) return 7;
                if (proc_address("vkCmdDispatchBaseKHR") !=
                    (PFN_vkVoidFunction)vkCmdDispatchBaseKHR) return 8;
                if (proc_address("vkGetDeviceGroupPresentCapabilitiesKHR") !=
                    (PFN_vkVoidFunction)vkGetDeviceGroupPresentCapabilitiesKHR) return 9;
                if (proc_address("vkGetDeviceGroupSurfacePresentModesKHR") !=
                    (PFN_vkVoidFunction)vkGetDeviceGroupSurfacePresentModesKHR) return 10;
                if (proc_address("vkGetPhysicalDevicePresentRectanglesKHR") !=
                    (PFN_vkVoidFunction)vkGetPhysicalDevicePresentRectanglesKHR) return 11;

                VkPeerMemoryFeatureFlags peer = 0xffffffffu;
                ((PFN_vkGetDeviceGroupPeerMemoryFeaturesKHR)proc_address("vkGetDeviceGroupPeerMemoryFeaturesKHR"))(
                    VK_NULL_HANDLE, 0, 0, 0, &peer);
                if (peer != 0) return 12;
                ((PFN_vkCmdSetDeviceMaskKHR)proc_address("vkCmdSetDeviceMaskKHR"))(VK_NULL_HANDLE, 1);
                ((PFN_vkCmdDispatchBaseKHR)proc_address("vkCmdDispatchBaseKHR"))(
                    VK_NULL_HANDLE, 1, 2, 3, 4, 5, 6);

                VkDeviceGroupPresentCapabilitiesKHR present_caps;
                memset(&present_caps, 0, sizeof(present_caps));
                present_caps.sType = VK_STRUCTURE_TYPE_DEVICE_GROUP_PRESENT_CAPABILITIES_KHR;
                if (vkGetDeviceGroupPresentCapabilitiesKHR(VK_NULL_HANDLE, &present_caps) != VK_SUCCESS) return 13;
                if (present_caps.presentMask[0] != 1u) return 14;
                if (present_caps.modes != VK_DEVICE_GROUP_PRESENT_MODE_LOCAL_BIT_KHR) return 15;

                VkHeadlessSurfaceCreateInfoEXT surface_info;
                memset(&surface_info, 0, sizeof(surface_info));
                surface_info.sType = VK_STRUCTURE_TYPE_HEADLESS_SURFACE_CREATE_INFO_EXT;
                VkSurfaceKHR surface = VK_NULL_HANDLE;
                if (vkCreateHeadlessSurfaceEXT(VK_NULL_HANDLE, &surface_info, NULL, &surface) != VK_SUCCESS) return 16;
                if (surface == VK_NULL_HANDLE) return 17;

                VkDeviceGroupPresentModeFlagsKHR surface_modes = 0;
                if (vkGetDeviceGroupSurfacePresentModesKHR(VK_NULL_HANDLE, surface, &surface_modes) != VK_SUCCESS) return 18;
                if (surface_modes != VK_DEVICE_GROUP_PRESENT_MODE_LOCAL_BIT_KHR) return 19;

                uint32_t rect_count = 0;
                if (vkGetPhysicalDevicePresentRectanglesKHR(VK_NULL_HANDLE, surface, &rect_count, NULL) != VK_SUCCESS) return 20;
                if (rect_count != 1u) return 21;
                VkRect2D rect;
                memset(&rect, 0xff, sizeof(rect));
                rect_count = 1u;
                if (vkGetPhysicalDevicePresentRectanglesKHR(VK_NULL_HANDLE, surface, &rect_count, &rect) != VK_SUCCESS) return 22;
                if (rect_count != 1u) return 23;
                if (rect.offset.x != 0 || rect.offset.y != 0) return 24;
                if (rect.extent.width != 640u || rect.extent.height != 480u) return 25;
                rect_count = 0u;
                if (vkGetPhysicalDevicePresentRectanglesKHR(VK_NULL_HANDLE, surface, &rect_count, &rect) != VK_INCOMPLETE) return 26;
                if (rect_count != 0u) return 27;

                surface_modes = 99u;
                if (vkGetDeviceGroupSurfacePresentModesKHR(VK_NULL_HANDLE, VK_NULL_HANDLE, &surface_modes) != VK_ERROR_SURFACE_LOST_KHR) return 28;
                if (surface_modes != 0u) return 29;
                rect_count = 99u;
                if (vkGetPhysicalDevicePresentRectanglesKHR(VK_NULL_HANDLE, VK_NULL_HANDLE, &rect_count, NULL) != VK_ERROR_SURFACE_LOST_KHR) return 30;
                if (rect_count != 0u) return 31;
                vkDestroySurfaceKHR(VK_NULL_HANDLE, surface, NULL);
            #endif
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_create_renderpass2_extension_exposes_khr_aliases(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            static int extension_seen(
                    const VkExtensionProperties *extensions,
                    uint32_t count,
                    const char *name) {{
                for (uint32_t i = 0; i < count; ++i) {{
                    if (strcmp(extensions[i].extensionName, name) == 0) return 1;
                }}
                return 0;
            }}

            int main(void) {{
            #ifdef VK_KHR_CREATE_RENDERPASS_2_EXTENSION_NAME
                if (!device_extension_advertised_name(VK_KHR_CREATE_RENDERPASS_2_EXTENSION_NAME)) return 2;
                uint32_t count = 64;
                VkExtensionProperties extensions[64];
                memset(extensions, 0, sizeof(extensions));
                if (vkEnumerateDeviceExtensionProperties((VkPhysicalDevice)physical_device_for_instance(NULL), NULL, &count, extensions) != VK_SUCCESS) return 3;
                if (!extension_seen(extensions, count, VK_KHR_CREATE_RENDERPASS_2_EXTENSION_NAME)) return 4;

                const char *enabled[] = {{ VK_KHR_CREATE_RENDERPASS_2_EXTENSION_NAME }};
                VkDeviceCreateInfo device_info;
                memset(&device_info, 0, sizeof(device_info));
                device_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                device_info.enabledExtensionCount = 1;
                device_info.ppEnabledExtensionNames = enabled;
                if (validate_device_extensions(&device_info) != VK_SUCCESS) return 5;

                if (proc_address("vkCreateRenderPass2KHR") != (PFN_vkVoidFunction)vkCreateRenderPass2) return 6;
                if (proc_address("vkCmdBeginRenderPass2KHR") != (PFN_vkVoidFunction)vkCmdBeginRenderPass2) return 7;
                if (proc_address("vkCmdNextSubpass2KHR") != (PFN_vkVoidFunction)vkCmdNextSubpass2) return 8;
                if (proc_address("vkCmdEndRenderPass2KHR") != (PFN_vkVoidFunction)vkCmdEndRenderPass2) return 9;

                VkSubpassDescription2 subpass;
                VkRenderPassCreateInfo2 create_info;
                memset(&subpass, 0, sizeof(subpass));
                memset(&create_info, 0, sizeof(create_info));
                subpass.sType = VK_STRUCTURE_TYPE_SUBPASS_DESCRIPTION_2;
                subpass.pipelineBindPoint = VK_PIPELINE_BIND_POINT_GRAPHICS;
                create_info.sType = VK_STRUCTURE_TYPE_RENDER_PASS_CREATE_INFO_2;
                create_info.subpassCount = 1;
                create_info.pSubpasses = &subpass;
                VkRenderPass render_pass = VK_NULL_HANDLE;
                PFN_vkCreateRenderPass2KHR create_render_pass2 =
                    (PFN_vkCreateRenderPass2KHR)proc_address("vkCreateRenderPass2KHR");
                if (create_render_pass2(VK_NULL_HANDLE, &create_info, NULL, &render_pass) != VK_SUCCESS) return 10;
                if (render_pass == VK_NULL_HANDLE) return 11;
                vkDestroyRenderPass(VK_NULL_HANDLE, render_pass, NULL);
            #endif
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stderr)


    def test_depth_stencil_resolve_non_none_is_fail_closed_before_advertising(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            static VkRenderPass make_depth_resolve_render_pass(VkResolveModeFlagBits depth_mode,
                                                               VkResolveModeFlagBits stencil_mode,
                                                               uint32_t resolve_attachment) {{
                VkAttachmentDescription2 attachments[2];
                VkAttachmentReference2 depth_ref;
                VkAttachmentReference2 resolve_ref;
                VkSubpassDescriptionDepthStencilResolve resolve;
                VkSubpassDescription2 subpass;
                VkRenderPassCreateInfo2 create_info;
                VkRenderPass render_pass = VK_NULL_HANDLE;

                memset(attachments, 0, sizeof(attachments));
                memset(&depth_ref, 0, sizeof(depth_ref));
                memset(&resolve_ref, 0, sizeof(resolve_ref));
                memset(&resolve, 0, sizeof(resolve));
                memset(&subpass, 0, sizeof(subpass));
                memset(&create_info, 0, sizeof(create_info));

                attachments[0].sType = VK_STRUCTURE_TYPE_ATTACHMENT_DESCRIPTION_2;
                attachments[0].format = VK_FORMAT_D24_UNORM_S8_UINT;
                attachments[0].samples = VK_SAMPLE_COUNT_4_BIT;
                attachments[0].loadOp = VK_ATTACHMENT_LOAD_OP_DONT_CARE;
                attachments[0].storeOp = VK_ATTACHMENT_STORE_OP_DONT_CARE;
                attachments[0].stencilLoadOp = VK_ATTACHMENT_LOAD_OP_DONT_CARE;
                attachments[0].stencilStoreOp = VK_ATTACHMENT_STORE_OP_DONT_CARE;
                attachments[0].initialLayout = VK_IMAGE_LAYOUT_UNDEFINED;
                attachments[0].finalLayout = VK_IMAGE_LAYOUT_DEPTH_STENCIL_ATTACHMENT_OPTIMAL;
                attachments[1] = attachments[0];
                attachments[1].samples = VK_SAMPLE_COUNT_1_BIT;

                depth_ref.sType = VK_STRUCTURE_TYPE_ATTACHMENT_REFERENCE_2;
                depth_ref.attachment = 0;
                depth_ref.layout = VK_IMAGE_LAYOUT_DEPTH_STENCIL_ATTACHMENT_OPTIMAL;
                resolve_ref.sType = VK_STRUCTURE_TYPE_ATTACHMENT_REFERENCE_2;
                resolve_ref.attachment = resolve_attachment;
                resolve_ref.layout = VK_IMAGE_LAYOUT_DEPTH_STENCIL_ATTACHMENT_OPTIMAL;

                resolve.sType = VK_STRUCTURE_TYPE_SUBPASS_DESCRIPTION_DEPTH_STENCIL_RESOLVE;
                resolve.depthResolveMode = depth_mode;
                resolve.stencilResolveMode = stencil_mode;
                resolve.pDepthStencilResolveAttachment = &resolve_ref;

                subpass.sType = VK_STRUCTURE_TYPE_SUBPASS_DESCRIPTION_2;
                subpass.pNext = &resolve;
                subpass.pipelineBindPoint = VK_PIPELINE_BIND_POINT_GRAPHICS;
                subpass.pDepthStencilAttachment = &depth_ref;

                create_info.sType = VK_STRUCTURE_TYPE_RENDER_PASS_CREATE_INFO_2;
                create_info.attachmentCount = 2;
                create_info.pAttachments = attachments;
                create_info.subpassCount = 1;
                create_info.pSubpasses = &subpass;
                if (vkCreateRenderPass2(VK_NULL_HANDLE, &create_info, NULL, &render_pass) != VK_SUCCESS) {{
                    return VK_NULL_HANDLE;
                }}
                return render_pass;
            }}

            int main(void) {{
            #ifdef VK_KHR_DEPTH_STENCIL_RESOLVE_EXTENSION_NAME
                if (device_extension_advertised_name(VK_KHR_DEPTH_STENCIL_RESOLVE_EXTENSION_NAME)) {{
                    fprintf(stderr, "VK_KHR_depth_stencil_resolve must remain unadvertised before support is coherent\\n");
                    return 2;
                }}
            #endif
                VkPhysicalDeviceDepthStencilResolveProperties props;
                memset(&props, 0xff, sizeof(props));
                props.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DEPTH_STENCIL_RESOLVE_PROPERTIES;
                props.pNext = NULL;
                fill_pnext_properties(&props);
                if (props.supportedDepthResolveModes != VK_RESOLVE_MODE_NONE ||
                    props.supportedStencilResolveModes != VK_RESOLVE_MODE_NONE) {{
                    fprintf(stderr, "depth/stencil resolve properties advertised executable resolve modes\\n");
                    return 3;
                }}

                VkRenderPass noop = make_depth_resolve_render_pass(
                    VK_RESOLVE_MODE_NONE, VK_RESOLVE_MODE_NONE, 1);
                if (noop == VK_NULL_HANDLE) return 4;
                PdockerVkRenderPass *noop_rp = pdocker_vk_render_pass_from_handle(noop);
                if (!noop_rp || !render_pass_subpass_can_normalize_to_dynamic_rendering(noop_rp, 0)) {{
                    fprintf(stderr, "mode-NONE depth/stencil resolve metadata should remain no-op\\n");
                    return 5;
                }}
                vkDestroyRenderPass(VK_NULL_HANDLE, noop, NULL);

                VkRenderPass depth = make_depth_resolve_render_pass(
                    VK_RESOLVE_MODE_AVERAGE_BIT, VK_RESOLVE_MODE_NONE, 1);
                if (depth == VK_NULL_HANDLE) return 6;
                PdockerVkRenderPass *depth_rp = pdocker_vk_render_pass_from_handle(depth);
                if (!depth_rp || render_pass_subpass_can_normalize_to_dynamic_rendering(depth_rp, 0) ||
                    !depth_rp->subpasses[0].unsupported) {{
                    fprintf(stderr, "non-NONE depth resolve was not fail-closed\\n");
                    return 7;
                }}
                vkDestroyRenderPass(VK_NULL_HANDLE, depth, NULL);

                VkRenderPass invalid_ref = make_depth_resolve_render_pass(
                    VK_RESOLVE_MODE_AVERAGE_BIT, VK_RESOLVE_MODE_NONE, 99);
                if (invalid_ref == VK_NULL_HANDLE) return 8;
                PdockerVkRenderPass *invalid_rp = pdocker_vk_render_pass_from_handle(invalid_ref);
                if (!invalid_rp || render_pass_subpass_can_normalize_to_dynamic_rendering(invalid_rp, 0) ||
                    !invalid_rp->subpasses[0].unsupported) {{
                    fprintf(stderr, "invalid non-NONE depth resolve ref was not fail-closed\\n");
                    return 9;
                }}
                vkDestroyRenderPass(VK_NULL_HANDLE, invalid_ref, NULL);
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stderr)




    def test_wsi_surface_swapchain_handles_fail_closed_after_destroy(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            static VkSemaphore make_binary_semaphore(VkDevice device) {{
                VkSemaphoreCreateInfo info;
                memset(&info, 0, sizeof(info));
                info.sType = VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO;
                VkSemaphore sem = VK_NULL_HANDLE;
                if (vkCreateSemaphore(device, &info, NULL, &sem) != VK_SUCCESS) return VK_NULL_HANDLE;
                return sem;
            }}

            static VkSwapchainKHR make_registered_swapchain(PdockerVkSurface *surface, uint64_t owner_device_id) {{
                if (!pdocker_vk_headless_surface_valid(surface)) return VK_NULL_HANDLE;
                PdockerVkSwapchain *swapchain = pdocker_alloc_handle(sizeof(*swapchain));
                if (!swapchain) return VK_NULL_HANDLE;
                memset(swapchain, 0, sizeof(*swapchain));
                swapchain->owner_device_id = owner_device_id;
                swapchain->surface = surface;
                swapchain->image_format = VK_FORMAT_R8G8B8A8_UNORM;
                swapchain->image_color_space = VK_COLOR_SPACE_SRGB_NONLINEAR_KHR;
                swapchain->image_extent = (VkExtent2D){{640, 480}};
                swapchain->image_usage = VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT;
                swapchain->present_mode = VK_PRESENT_MODE_FIFO_KHR;
                swapchain->composite_alpha = VK_COMPOSITE_ALPHA_OPAQUE_BIT_KHR;
                swapchain->pre_transform = VK_SURFACE_TRANSFORM_IDENTITY_BIT_KHR;
                swapchain->image_count = 2;
                swapchain->generation = next_vulkan_object_generation();
                for (uint32_t i = 0; i < swapchain->image_count; ++i) {{
                    PdockerVkImage *image = pdocker_alloc_handle(sizeof(*image));
                    PdockerVkMemory *memory = pdocker_alloc_handle(sizeof(*memory));
                    if (!image || !memory) return VK_NULL_HANDLE;
                    memset(image, 0, sizeof(*image));
                    memset(memory, 0, sizeof(*memory));
                    memory->owner_device_id = owner_device_id;
                    memory->size = 4096;
                    image->owner_device_id = owner_device_id;
                    image->memory = memory;
                    image->swapchain_owned = true;
                    image->current_layout = VK_IMAGE_LAYOUT_PRESENT_SRC_KHR;
                    swapchain->images[i] = image;
                    swapchain->memories[i] = memory;
                }}
                swapchain_register(swapchain);
                return pdocker_vk_swapchain_to_handle(swapchain);
            }}

            int main(void) {{
                unsetenv("PDOCKER_VULKAN_DISABLE_V5_OBJECT_TRANSPORT");
                unsetenv("PDOCKER_VULKAN_ADVERTISEMENT_SOURCE");
                VkDeviceCreateInfo device_info;
                memset(&device_info, 0, sizeof(device_info));
                device_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                VkDevice device = VK_NULL_HANDLE;
                if (vkCreateDevice((VkPhysicalDevice)&g_device, &device_info, NULL, &device) != VK_SUCCESS ||
                    device == VK_NULL_HANDLE) return 29;
                VkQueue queue = VK_NULL_HANDLE;
                vkGetDeviceQueue(device, 0, 0, &queue);
                PdockerVkQueue *queue_obj = pdocker_vk_queue_from_handle(queue);
                if (!queue_obj || queue_obj->device_object_id == 0) return 30;

                VkHeadlessSurfaceCreateInfoEXT surface_info;
                memset(&surface_info, 0, sizeof(surface_info));
                surface_info.sType = VK_STRUCTURE_TYPE_HEADLESS_SURFACE_CREATE_INFO_EXT;
                VkSurfaceKHR surface = VK_NULL_HANDLE;
                if (vkCreateHeadlessSurfaceEXT(VK_NULL_HANDLE, &surface_info, NULL, &surface) != VK_SUCCESS || !surface) return 2;
                PdockerVkSurface *surface_obj = surface_handle_lookup_for_instance(VK_NULL_HANDLE, surface);
                if (!surface_obj) return 3;

                VkSwapchainKHR swapchain = make_registered_swapchain(surface_obj, queue_obj->device_object_id);
                if (!swapchain || !swapchain_handle_lookup(swapchain)) return 4;
                uint32_t image_count = 0;
                if (vkGetSwapchainImagesKHR(device, swapchain, &image_count, NULL) != VK_SUCCESS) return 5;
                if (image_count != 2) return 6;

                VkSemaphore sem = make_binary_semaphore(device);
                if (!sem) return 7;
                uint32_t image_index = UINT32_MAX;
                if (vkAcquireNextImageKHR(device, swapchain, 0, sem, VK_NULL_HANDLE, &image_index) != VK_SUCCESS) return 8;
                if (!semaphore_handle_lookup(sem) || !semaphore_handle_lookup(sem)->signaled) return 9;
                VkPresentInfoKHR present;
                memset(&present, 0, sizeof(present));
                present.sType = VK_STRUCTURE_TYPE_PRESENT_INFO_KHR;
                present.waitSemaphoreCount = 1;
                present.pWaitSemaphores = &sem;
                present.swapchainCount = 1;
                present.pSwapchains = &swapchain;
                present.pImageIndices = &image_index;
                VkResult present_result = VK_ERROR_UNKNOWN;
                present.pResults = &present_result;
                if (vkQueuePresentKHR(queue, &present) != VK_SUCCESS) return 10;
                if (present_result != VK_SUCCESS) return 11;
                if (semaphore_handle_lookup(sem)->signaled) return 12;
                vkDestroySemaphore(device, sem, NULL);

                VkFenceCreateInfo fence_info;
                memset(&fence_info, 0, sizeof(fence_info));
                fence_info.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO;
                VkFence fence = VK_NULL_HANDLE;
                if (vkCreateFence(device, &fence_info, NULL, &fence) != VK_SUCCESS || !fence) return 13;
                vkDestroyFence(device, fence, NULL);
                if (vkAcquireNextImageKHR(device, swapchain, 0, VK_NULL_HANDLE, fence, &image_index) != VK_ERROR_INITIALIZATION_FAILED) return 14;

                VkSemaphore stale_sem = make_binary_semaphore(device);
                if (!stale_sem) return 15;
                if (vkAcquireNextImageKHR(device, swapchain, 0, stale_sem, VK_NULL_HANDLE, &image_index) != VK_SUCCESS) return 16;
                vkDestroySemaphore(device, stale_sem, NULL);
                present.pWaitSemaphores = &stale_sem;
                present.pImageIndices = &image_index;
                if (vkQueuePresentKHR(queue, &present) != VK_ERROR_INITIALIZATION_FAILED) return 17;

                PdockerVkSwapchain *sc = swapchain_unregister(swapchain);
                if (!sc) return 18;
                swapchain_retire(sc);
                if (swapchain_handle_lookup(swapchain)) return 19;
                image_count = 99;
                if (vkGetSwapchainImagesKHR(device, swapchain, &image_count, NULL) != VK_ERROR_INITIALIZATION_FAILED) return 20;
                if (image_count != 0) return 21;
                VkSemaphore sem_after_destroy = make_binary_semaphore(device);
                if (!sem_after_destroy) return 22;
                if (vkAcquireNextImageKHR(device, swapchain, 0, sem_after_destroy, VK_NULL_HANDLE, &image_index) != VK_ERROR_INITIALIZATION_FAILED) return 23;
                vkDestroySemaphore(device, sem_after_destroy, NULL);
                present.pWaitSemaphores = NULL;
                present.waitSemaphoreCount = 0;
                if (vkQueuePresentKHR(queue, &present) != VK_ERROR_INITIALIZATION_FAILED) return 24;

                vkDestroySurfaceKHR(VK_NULL_HANDLE, surface, NULL);
                if (surface_handle_lookup_for_instance(VK_NULL_HANDLE, surface)) return 25;
                VkDeviceGroupPresentModeFlagsKHR modes = 123;
                if (vkGetDeviceGroupSurfacePresentModesKHR(VK_NULL_HANDLE, surface, &modes) != VK_ERROR_SURFACE_LOST_KHR) return 26;
                if (modes != 0) return 27;
                if (make_registered_swapchain(surface_handle_lookup_for_instance(VK_NULL_HANDLE, surface), queue_obj->device_object_id) != VK_NULL_HANDLE) return 28;
                vkDestroyDevice(device, NULL);
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_destroy_instance_retires_owned_headless_surfaces(self):
        source = textwrap.dedent("""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "__ICD_SOURCE__"

            int main(void) {
                VkInstanceCreateInfo instance_info;
                memset(&instance_info, 0, sizeof(instance_info));
                instance_info.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO;
                VkInstance instance = VK_NULL_HANDLE;
                if (vkCreateInstance(&instance_info, NULL, &instance) != VK_SUCCESS || !instance) return 1;

                VkHeadlessSurfaceCreateInfoEXT surface_info;
                memset(&surface_info, 0, sizeof(surface_info));
                surface_info.sType = VK_STRUCTURE_TYPE_HEADLESS_SURFACE_CREATE_INFO_EXT;
                VkSurfaceKHR surface = VK_NULL_HANDLE;
                if (vkCreateHeadlessSurfaceEXT(instance, &surface_info, NULL, &surface) != VK_SUCCESS || !surface) return 2;
                if (!surface_handle_lookup(surface)) return 3;
                if (!surface_handle_lookup_for_instance(instance, surface)) return 4;

                vkDestroyInstance(instance, NULL);
                if (surface_handle_lookup(surface)) return 5;
                if (surface_handle_lookup_for_instance(instance, surface)) return 6;

                vkDestroySurfaceKHR(instance, surface, NULL);
                if (surface_handle_lookup(surface)) return 7;
                return 0;
            }
            """).replace("__ICD_SOURCE__", str(ICD_SOURCE))
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


    def test_wsi_surface_owner_scope_rejects_cross_instance_device_paths(self):
        sc = chr(59)
        amp = chr(38)
        c_lines = [
            "#include <stdint.h>",
            "#include <stdio.h>",
            "#include <string.h>",
            "#include \"__ICD_SOURCE__\"",
            "",
            "static VkInstance make_instance(void) {",
            "    VkInstanceCreateInfo info" + sc,
            "    memset(" + amp + "info, 0, sizeof(info))" + sc,
            "    info.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO" + sc,
            "    VkInstance instance = VK_NULL_HANDLE" + sc,
            "    if (vkCreateInstance(" + amp + "info, NULL, " + amp + "instance) != VK_SUCCESS) return VK_NULL_HANDLE" + sc,
            "    return instance" + sc,
            "}",
            "",
            "static VkPhysicalDevice make_physical(VkInstance instance) {",
            "    uint32_t count = 1" + sc,
            "    VkPhysicalDevice physical = VK_NULL_HANDLE" + sc,
            "    if (vkEnumeratePhysicalDevices(instance, " + amp + "count, " + amp + "physical) != VK_SUCCESS) return VK_NULL_HANDLE" + sc,
            "    if (count != 1) return VK_NULL_HANDLE" + sc,
            "    return physical" + sc,
            "}",
            "",
            "static VkDevice make_device_for_instance(VkInstance instance) {",
            "    VkPhysicalDevice physical = make_physical(instance)" + sc,
            "    if (!physical) return VK_NULL_HANDLE" + sc,
            "    VkDeviceCreateInfo info" + sc,
            "    memset(" + amp + "info, 0, sizeof(info))" + sc,
            "    info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO" + sc,
            "    VkDevice device = VK_NULL_HANDLE" + sc,
            "    if (vkCreateDevice(physical, " + amp + "info, NULL, " + amp + "device) != VK_SUCCESS) return VK_NULL_HANDLE" + sc,
            "    return device" + sc,
            "}",
            "",
            "static VkResult make_grouped_device(VkPhysicalDevice parent, VkPhysicalDevice member, VkDevice *device) {",
            "    VkDeviceGroupDeviceCreateInfo group" + sc,
            "    VkDeviceCreateInfo info" + sc,
            "    memset(" + amp + "group, 0, sizeof(group))" + sc,
            "    memset(" + amp + "info, 0, sizeof(info))" + sc,
            "    group.sType = VK_STRUCTURE_TYPE_DEVICE_GROUP_DEVICE_CREATE_INFO" + sc,
            "    group.physicalDeviceCount = 1" + sc,
            "    group.pPhysicalDevices = " + amp + "member" + sc,
            "    info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO" + sc,
            "    info.pNext = " + amp + "group" + sc,
            "    *device = VK_NULL_HANDLE" + sc,
            "    return vkCreateDevice(parent, " + amp + "info, NULL, device)" + sc,
            "}",
            "",
            "static VkSurfaceKHR make_surface(VkInstance instance) {",
            "    VkHeadlessSurfaceCreateInfoEXT info" + sc,
            "    memset(" + amp + "info, 0, sizeof(info))" + sc,
            "    info.sType = VK_STRUCTURE_TYPE_HEADLESS_SURFACE_CREATE_INFO_EXT" + sc,
            "    VkSurfaceKHR surface = VK_NULL_HANDLE" + sc,
            "    if (vkCreateHeadlessSurfaceEXT(instance, " + amp + "info, NULL, " + amp + "surface) != VK_SUCCESS) return VK_NULL_HANDLE" + sc,
            "    return surface" + sc,
            "}",
            "",
            "static void fill_swapchain_info(VkSurfaceKHR surface, VkSwapchainCreateInfoKHR *info) {",
            "    memset(info, 0, sizeof(*info))" + sc,
            "    info->sType = VK_STRUCTURE_TYPE_SWAPCHAIN_CREATE_INFO_KHR" + sc,
            "    info->surface = surface" + sc,
            "    info->minImageCount = 2" + sc,
            "    info->imageFormat = VK_FORMAT_R8G8B8A8_UNORM" + sc,
            "    info->imageColorSpace = VK_COLOR_SPACE_SRGB_NONLINEAR_KHR" + sc,
            "    info->imageExtent = (VkExtent2D){640, 480}" + sc,
            "    info->imageArrayLayers = 1" + sc,
            "    info->imageUsage = VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT" + sc,
            "    info->imageSharingMode = VK_SHARING_MODE_EXCLUSIVE" + sc,
            "    info->preTransform = VK_SURFACE_TRANSFORM_IDENTITY_BIT_KHR" + sc,
            "    info->compositeAlpha = VK_COMPOSITE_ALPHA_OPAQUE_BIT_KHR" + sc,
            "    info->presentMode = VK_PRESENT_MODE_FIFO_KHR" + sc,
            "}",
            "",
            "int main(void) {",
            "    unsetenv(\"PDOCKER_VULKAN_DISABLE_V5_OBJECT_TRANSPORT\")" + sc,
            "    VkInstance instance_a = make_instance()" + sc,
            "    VkInstance instance_b = make_instance()" + sc,
            "    if (!instance_a) return 1" + sc,
            "    if (!instance_b) return 2" + sc,
            "    VkPhysicalDevice physical_a = make_physical(instance_a)" + sc,
            "    VkPhysicalDevice physical_b = make_physical(instance_b)" + sc,
            "    if (!physical_a || !physical_b || physical_a == physical_b) return 17" + sc,
            "    VkDevice wrong_group_device = VK_NULL_HANDLE" + sc,
            "    if (make_grouped_device(physical_a, physical_b, " + amp + "wrong_group_device) != VK_ERROR_INITIALIZATION_FAILED) return 19" + sc,
            "    if (wrong_group_device != VK_NULL_HANDLE) return 20" + sc,
            "    VkDevice grouped_device_a = VK_NULL_HANDLE" + sc,
            "    if (make_grouped_device(physical_a, physical_a, " + amp + "grouped_device_a) != VK_SUCCESS) return 21" + sc,
            "    VkDevice device_a = make_device_for_instance(instance_a)" + sc,
            "    if (!device_a) return 3" + sc,
            "    VkSurfaceKHR surface_a = make_surface(instance_a)" + sc,
            "    if (!surface_a) return 4" + sc,
            "    if (!surface_handle_lookup_for_instance(instance_a, surface_a)) return 5" + sc,
            "    if (surface_handle_lookup_for_instance(instance_b, surface_a)) return 6" + sc,
            "    VkDevice device_b = make_device_for_instance(instance_b)" + sc,
            "    if (!device_b) return 7" + sc,
            "    if (!surface_handle_lookup_for_device(device_a, surface_a)) return 8" + sc,
            "    if (surface_handle_lookup_for_device(device_b, surface_a)) return 9" + sc,
            "    if (!pdocker_vk_object_handle_owned_by_device(device_a, VK_OBJECT_TYPE_SURFACE_KHR, (uint64_t)surface_a)) return 10" + sc,
            "    if (pdocker_vk_object_handle_owned_by_device(device_b, VK_OBJECT_TYPE_SURFACE_KHR, (uint64_t)surface_a)) return 11" + sc,
            "    VkSurfaceCapabilitiesKHR caps" + sc,
            "    memset(" + amp + "caps, 0, sizeof(caps))" + sc,
            "    if (vkGetPhysicalDeviceSurfaceCapabilitiesKHR(physical_a, surface_a, " + amp + "caps) != VK_SUCCESS) return 12" + sc,
            "    if (vkGetPhysicalDeviceSurfaceCapabilitiesKHR(physical_b, surface_a, " + amp + "caps) != VK_ERROR_SURFACE_LOST_KHR) return 18" + sc,
            "    VkSwapchainCreateInfoKHR info" + sc,
            "    fill_swapchain_info(surface_a, " + amp + "info)" + sc,
            "    VkSwapchainKHR wrong_swapchain = VK_NULL_HANDLE" + sc,
            "    if (vkCreateSwapchainKHR(device_b, " + amp + "info, NULL, " + amp + "wrong_swapchain) != VK_ERROR_SURFACE_LOST_KHR) return 13" + sc,
            "    if (wrong_swapchain != VK_NULL_HANDLE) return 14" + sc,
            "    vkDestroySurfaceKHR(instance_b, surface_a, NULL)" + sc,
            "    if (!surface_handle_lookup_for_instance(instance_a, surface_a)) return 15" + sc,
            "    vkDestroySurfaceKHR(instance_a, surface_a, NULL)" + sc,
            "    if (surface_handle_lookup_for_instance(instance_a, surface_a)) return 16" + sc,
            "    vkDestroyDevice(device_b, NULL)" + sc,
            "    vkDestroyDevice(device_a, NULL)" + sc,
            "    vkDestroyDevice(grouped_device_a, NULL)" + sc,
            "    vkDestroyInstance(instance_b, NULL)" + sc,
            "    vkDestroyInstance(instance_a, NULL)" + sc,
            "    return 0" + sc,
            "}",
        ]
        source = chr(10).join(c_lines).replace("__ICD_SOURCE__", str(ICD_SOURCE))
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_command_pool_and_buffer_handles_fail_closed_after_free_reset_destroy(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            int main(void) {{
                VkCommandPoolCreateInfo pool_info;
                memset(&pool_info, 0, sizeof(pool_info));
                pool_info.sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO;
                pool_info.queueFamilyIndex = 0;
                pool_info.flags = VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT;

                VkCommandPool pool = VK_NULL_HANDLE;
                if (vkCreateCommandPool(VK_NULL_HANDLE, &pool_info, NULL, &pool) != VK_SUCCESS) return 2;
                PdockerVkCommandPool *tracked_pool = command_pool_handle_lookup(pool);
                if (!tracked_pool) return 3;

                VkCommandBufferAllocateInfo alloc_info;
                memset(&alloc_info, 0, sizeof(alloc_info));
                alloc_info.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO;
                alloc_info.commandPool = pool;
                alloc_info.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY;
                alloc_info.commandBufferCount = 1;
                VkCommandBuffer cmd = VK_NULL_HANDLE;
                if (vkAllocateCommandBuffers(VK_NULL_HANDLE, &alloc_info, &cmd) != VK_SUCCESS) return 4;
                PdockerVkCommandBuffer *tracked_cmd = command_buffer_handle_lookup(cmd);
                if (!command_buffer_belongs_to_pool(tracked_cmd, tracked_pool)) return 5;
                if (vkBeginCommandBuffer(cmd, NULL) != VK_SUCCESS) return 6;
                if (vkEndCommandBuffer(cmd) != VK_SUCCESS) return 7;

                VkCommandPool other_pool = VK_NULL_HANDLE;
                if (vkCreateCommandPool(VK_NULL_HANDLE, &pool_info, NULL, &other_pool) != VK_SUCCESS) return 8;
                vkFreeCommandBuffers(VK_NULL_HANDLE, other_pool, 1, &cmd);
                if (command_buffer_handle_lookup(cmd) != tracked_cmd) return 9;
                if (tracked_cmd->owner_pool != tracked_pool) return 24;
                if (!command_buffer_belongs_to_pool(tracked_cmd, tracked_pool)) return 25;
                if (!command_pool_contains_command_buffer(tracked_pool, tracked_cmd)) return 26;
                vkDestroyCommandPool(VK_NULL_HANDLE, other_pool, NULL);
                if (command_pool_handle_lookup(other_pool)) return 10;
                if (tracked_cmd->owner_pool != tracked_pool) return 27;
                if (!command_pool_contains_command_buffer(tracked_pool, tracked_cmd)) return 28;

                vkFreeCommandBuffers(VK_NULL_HANDLE, pool, 1, &cmd);
                if (command_buffer_handle_lookup(cmd)) return 11;
                if (vkBeginCommandBuffer(cmd, NULL) != VK_ERROR_INITIALIZATION_FAILED) return 12;

                VkCommandBuffer cmd2 = VK_NULL_HANDLE;
                if (vkAllocateCommandBuffers(VK_NULL_HANDLE, &alloc_info, &cmd2) != VK_SUCCESS) return 13;
                PdockerVkCommandBuffer *tracked_cmd2 = command_buffer_handle_lookup(cmd2);
                if (!tracked_cmd2) return 14;
                tracked_cmd2->compute_pipeline = (PdockerVkPipeline *)(uintptr_t)0x1u;
                tracked_cmd2->pipeline = tracked_cmd2->compute_pipeline;
                tracked_cmd2->has_dispatch = true;
                tracked_cmd2->dispatch_x = 9;
                tracked_cmd2->dispatch_y = 8;
                tracked_cmd2->dispatch_z = 7;
                if (vkResetCommandPool(VK_NULL_HANDLE, pool, VK_COMMAND_POOL_RESET_RELEASE_RESOURCES_BIT) != VK_SUCCESS) return 15;
                tracked_cmd2 = command_buffer_handle_lookup(cmd2);
                if (!tracked_cmd2) return 16;
                if (tracked_cmd2->has_dispatch || tracked_cmd2->compute_pipeline ||
                    tracked_cmd2->pipeline || tracked_cmd2->dispatch_x ||
                    tracked_cmd2->dispatch_y || tracked_cmd2->dispatch_z) return 23;
                if (vkBeginCommandBuffer(cmd2, NULL) != VK_SUCCESS) return 17;
                if (vkEndCommandBuffer(cmd2) != VK_SUCCESS) return 18;

                g_queue.object_id = 1;
                g_queue.instance_object_id = 1;
                g_queue.physical_device_object_id = 1;
                g_queue.device_object_id = 1;
                VkSubmitInfo submit;
                memset(&submit, 0, sizeof(submit));
                submit.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO;
                submit.commandBufferCount = 1;
                submit.pCommandBuffers = &cmd;
                if (vkQueueSubmit((VkQueue)&g_queue, 1, &submit, VK_NULL_HANDLE) != VK_ERROR_INITIALIZATION_FAILED) return 19;

                vkDestroyCommandPool(VK_NULL_HANDLE, pool, NULL);
                if (command_pool_handle_lookup(pool)) return 20;
                if (command_buffer_handle_lookup(cmd2)) return 21;
                if (vkBeginCommandBuffer(cmd2, NULL) != VK_ERROR_INITIALIZATION_FAILED) return 22;
                vkDestroyCommandPool(VK_NULL_HANDLE, pool, NULL);
                vkFreeCommandBuffers(VK_NULL_HANDLE, pool, 1, &cmd2);
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_sync_event_query_handles_fail_closed_after_destroy(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"
            {COMMAND_BUFFER_STACK_TEST_HELPER}

            int main(void) {{
                unsetenv("PDOCKER_GPU_QUEUE_SOCKET");
                ensure_vulkan_dispatchable_object_ids();
                g_queue.object_id = 1;
                g_queue.instance_object_id = 1;
                g_queue.physical_device_object_id = 1;
                g_queue.device_object_id = 1;

                VkFenceCreateInfo fence_info;
                memset(&fence_info, 0, sizeof(fence_info));
                fence_info.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO;
                fence_info.flags = VK_FENCE_CREATE_SIGNALED_BIT;
                VkFence fence = VK_NULL_HANDLE;
                if (vkCreateFence(VK_NULL_HANDLE, &fence_info, NULL, &fence) != VK_SUCCESS || !fence) return 2;
                if (!fence_handle_lookup(fence)) return 3;
                if (vkGetFenceStatus(VK_NULL_HANDLE, fence) != VK_SUCCESS) return 4;
                vkDestroyFence(VK_NULL_HANDLE, fence, NULL);
                if (fence_handle_lookup(fence)) return 5;
                if (vkGetFenceStatus(VK_NULL_HANDLE, fence) != VK_ERROR_INITIALIZATION_FAILED) return 6;
                if (vkResetFences(VK_NULL_HANDLE, 1, &fence) != VK_ERROR_INITIALIZATION_FAILED) return 7;
                if (vkWaitForFences(VK_NULL_HANDLE, 1, &fence, VK_TRUE, 0) != VK_ERROR_INITIALIZATION_FAILED) return 8;

                VkSemaphoreCreateInfo sem_info;
                memset(&sem_info, 0, sizeof(sem_info));
                sem_info.sType = VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO;
                VkSemaphore sem = VK_NULL_HANDLE;
                if (vkCreateSemaphore(VK_NULL_HANDLE, &sem_info, NULL, &sem) != VK_SUCCESS || !sem) return 9;
                if (!semaphore_handle_lookup(sem)) return 10;
                uint64_t counter = 0;
                if (vkGetSemaphoreCounterValue(VK_NULL_HANDLE, sem, &counter) != VK_ERROR_FEATURE_NOT_PRESENT) return 11;
                vkDestroySemaphore(VK_NULL_HANDLE, sem, NULL);
                if (semaphore_handle_lookup(sem)) return 12;
                if (vkGetSemaphoreCounterValue(VK_NULL_HANDLE, sem, &counter) != VK_ERROR_INITIALIZATION_FAILED) return 13;
                VkSemaphoreWaitInfo wait_info;
                memset(&wait_info, 0, sizeof(wait_info));
                wait_info.sType = VK_STRUCTURE_TYPE_SEMAPHORE_WAIT_INFO;
                wait_info.semaphoreCount = 1;
                wait_info.pSemaphores = &sem;
                wait_info.pValues = &counter;
                if (vkWaitSemaphores(VK_NULL_HANDLE, &wait_info, 0) != VK_ERROR_INITIALIZATION_FAILED) return 14;
                VkSubmitInfo stale_signal_submit;
                memset(&stale_signal_submit, 0, sizeof(stale_signal_submit));
                stale_signal_submit.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO;
                stale_signal_submit.signalSemaphoreCount = 1;
                stale_signal_submit.pSignalSemaphores = &sem;
                if (vkQueueSubmit((VkQueue)&g_queue, 1, &stale_signal_submit, VK_NULL_HANDLE) != VK_ERROR_INITIALIZATION_FAILED) return 30;
                VkSemaphore null_sem = VK_NULL_HANDLE;
                stale_signal_submit.pSignalSemaphores = &null_sem;
                if (vkQueueSubmit((VkQueue)&g_queue, 1, &stale_signal_submit, VK_NULL_HANDLE) != VK_ERROR_INITIALIZATION_FAILED) return 31;

                VkEventCreateInfo event_info;
                memset(&event_info, 0, sizeof(event_info));
                event_info.sType = VK_STRUCTURE_TYPE_EVENT_CREATE_INFO;
                VkEvent event = VK_NULL_HANDLE;
                if (vkCreateEvent(VK_NULL_HANDLE, &event_info, NULL, &event) != VK_SUCCESS || !event) return 15;
                if (!event_handle_lookup(event)) return 16;
                if (vkSetEvent(VK_NULL_HANDLE, event) != VK_SUCCESS) return 17;
                if (vkGetEventStatus(VK_NULL_HANDLE, event) != VK_EVENT_SET) return 18;
                PdockerVkCommandBuffer event_cmd;
                reset_test_command_buffer(&event_cmd, 0, 0);
                vkCmdSetEvent((VkCommandBuffer)&event_cmd, event, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT);
                if (event_cmd.recording_failed || event_cmd.command_op_count != 1) return 28;
                vkDestroyEvent(VK_NULL_HANDLE, event, NULL);
                if (event_handle_lookup(event)) return 19;
                if (vkGetEventStatus(VK_NULL_HANDLE, event) != VK_ERROR_INITIALIZATION_FAILED) return 20;
                if (vkSetEvent(VK_NULL_HANDLE, event) != VK_ERROR_INITIALIZATION_FAILED) return 21;
                VkCommandBuffer event_cmd_handle = (VkCommandBuffer)&event_cmd;
                VkSubmitInfo event_submit;
                memset(&event_submit, 0, sizeof(event_submit));
                event_submit.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO;
                event_submit.commandBufferCount = 1;
                event_submit.pCommandBuffers = &event_cmd_handle;
                if (vkQueueSubmit((VkQueue)&g_queue, 1, &event_submit, VK_NULL_HANDLE) != VK_ERROR_INITIALIZATION_FAILED) return 29;
                command_buffer_destroy_record_vectors(&event_cmd);
                command_buffer_destroy_descriptor_states(&event_cmd);
                (void)command_buffer_unregister((VkCommandBuffer)&event_cmd);

                VkQueryPoolCreateInfo query_info;
                memset(&query_info, 0, sizeof(query_info));
                query_info.sType = VK_STRUCTURE_TYPE_QUERY_POOL_CREATE_INFO;
                query_info.queryType = VK_QUERY_TYPE_TIMESTAMP;
                query_info.queryCount = 1;
                VkQueryPool query_pool = VK_NULL_HANDLE;
                if (vkCreateQueryPool(VK_NULL_HANDLE, &query_info, NULL, &query_pool) != VK_SUCCESS || !query_pool) return 22;
                if (!query_pool_handle_lookup(query_pool)) return 23;

                PdockerVkCommandBuffer cmd;
                reset_test_command_buffer(&cmd, 0, 0);
                vkCmdWriteTimestamp((VkCommandBuffer)&cmd, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, query_pool, 0);
                if (cmd.recording_failed || cmd.command_op_count != 1) return 24;
                vkDestroyQueryPool(VK_NULL_HANDLE, query_pool, NULL);
                if (query_pool_handle_lookup(query_pool)) return 25;

                VkSubmitInfo submit;
                VkCommandBuffer cmd_handle = (VkCommandBuffer)&cmd;
                memset(&submit, 0, sizeof(submit));
                submit.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO;
                submit.commandBufferCount = 1;
                submit.pCommandBuffers = &cmd_handle;
                if (vkQueueSubmit((VkQueue)&g_queue, 1, &submit, VK_NULL_HANDLE) != VK_ERROR_INITIALIZATION_FAILED) return 26;
                uint64_t data = 0;
                if (vkGetQueryPoolResults(VK_NULL_HANDLE, query_pool, 0, 1, sizeof(data), &data, sizeof(data), VK_QUERY_RESULT_64_BIT) != VK_ERROR_INITIALIZATION_FAILED) return 27;
                command_buffer_destroy_record_vectors(&cmd);
                command_buffer_destroy_descriptor_states(&cmd);
                (void)command_buffer_unregister((VkCommandBuffer)&cmd);
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_maintenance1_extension_exposes_trim_command_pool_alias(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            static int extension_seen(
                    const VkExtensionProperties *extensions,
                    uint32_t count,
                    const char *name) {{
                for (uint32_t i = 0; i < count; ++i) {{
                    if (strcmp(extensions[i].extensionName, name) == 0) return 1;
                }}
                return 0;
            }}

            int main(void) {{
            #ifdef VK_KHR_MAINTENANCE_1_EXTENSION_NAME
                if (!device_extension_advertised_name(VK_KHR_MAINTENANCE_1_EXTENSION_NAME)) return 2;
                uint32_t count = 64;
                VkExtensionProperties extensions[64];
                memset(extensions, 0, sizeof(extensions));
                if (vkEnumerateDeviceExtensionProperties((VkPhysicalDevice)physical_device_for_instance(NULL), NULL, &count, extensions) != VK_SUCCESS) return 3;
                if (!extension_seen(extensions, count, VK_KHR_MAINTENANCE_1_EXTENSION_NAME)) return 4;

                const char *enabled[] = {{ VK_KHR_MAINTENANCE_1_EXTENSION_NAME }};
                VkDeviceCreateInfo device_info;
                memset(&device_info, 0, sizeof(device_info));
                device_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                device_info.enabledExtensionCount = 1;
                device_info.ppEnabledExtensionNames = enabled;
                if (validate_device_extensions(&device_info) != VK_SUCCESS) return 5;

                PFN_vkVoidFunction raw = proc_address("vkTrimCommandPoolKHR");
                if (raw == NULL) return 6;
                if (raw != (PFN_vkVoidFunction)vkTrimCommandPool) return 7;
                ((PFN_vkTrimCommandPoolKHR)raw)(VK_NULL_HANDLE, VK_NULL_HANDLE, 0);
            #endif
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_descriptor_update_template_extension_exposes_khr_aliases(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            static int extension_seen(
                    const VkExtensionProperties *extensions,
                    uint32_t count,
                    const char *name) {{
                for (uint32_t i = 0; i < count; ++i) {{
                    if (strcmp(extensions[i].extensionName, name) == 0) return 1;
                }}
                return 0;
            }}

            int main(void) {{
            #ifdef VK_KHR_DESCRIPTOR_UPDATE_TEMPLATE_EXTENSION_NAME
                if (!device_extension_advertised_name(VK_KHR_DESCRIPTOR_UPDATE_TEMPLATE_EXTENSION_NAME)) return 2;
                uint32_t count = 64;
                VkExtensionProperties extensions[64];
                memset(extensions, 0, sizeof(extensions));
                if (vkEnumerateDeviceExtensionProperties((VkPhysicalDevice)physical_device_for_instance(NULL), NULL, &count, extensions) != VK_SUCCESS) return 3;
                if (!extension_seen(extensions, count, VK_KHR_DESCRIPTOR_UPDATE_TEMPLATE_EXTENSION_NAME)) return 4;

                const char *enabled[] = {{ VK_KHR_DESCRIPTOR_UPDATE_TEMPLATE_EXTENSION_NAME }};
                VkDeviceCreateInfo device_info;
                memset(&device_info, 0, sizeof(device_info));
                device_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                device_info.enabledExtensionCount = 1;
                device_info.ppEnabledExtensionNames = enabled;
                if (validate_device_extensions(&device_info) != VK_SUCCESS) return 5;
                if (proc_address("vkCreateDescriptorUpdateTemplateKHR") == NULL) return 6;
                if (proc_address("vkDestroyDescriptorUpdateTemplateKHR") == NULL) return 7;
                if (proc_address("vkUpdateDescriptorSetWithTemplateKHR") == NULL) return 8;

                VkDescriptorSetLayoutBinding binding;
                VkDescriptorSetLayoutCreateInfo layout_info;
                memset(&binding, 0, sizeof(binding));
                memset(&layout_info, 0, sizeof(layout_info));
                binding.binding = 0;
                binding.descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
                binding.descriptorCount = 1;
                binding.stageFlags = VK_SHADER_STAGE_COMPUTE_BIT;
                layout_info.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO;
                layout_info.bindingCount = 1;
                layout_info.pBindings = &binding;
                VkDescriptorSetLayout layout = VK_NULL_HANDLE;
                if (vkCreateDescriptorSetLayout(VK_NULL_HANDLE, &layout_info, NULL, &layout) != VK_SUCCESS) return 9;

                VkDescriptorUpdateTemplateEntry entry;
                VkDescriptorUpdateTemplateCreateInfo template_info;
                memset(&entry, 0, sizeof(entry));
                memset(&template_info, 0, sizeof(template_info));
                entry.dstBinding = 0;
                entry.dstArrayElement = 0;
                entry.descriptorCount = 1;
                entry.descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
                entry.offset = 0;
                entry.stride = sizeof(VkDescriptorBufferInfo);
                template_info.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_UPDATE_TEMPLATE_CREATE_INFO;
                template_info.descriptorUpdateEntryCount = 1;
                template_info.pDescriptorUpdateEntries = &entry;
                template_info.templateType = VK_DESCRIPTOR_UPDATE_TEMPLATE_TYPE_DESCRIPTOR_SET;
                template_info.descriptorSetLayout = layout;
                VkDescriptorUpdateTemplate update_template = VK_NULL_HANDLE;
                PFN_vkCreateDescriptorUpdateTemplateKHR create_template =
                    (PFN_vkCreateDescriptorUpdateTemplateKHR)proc_address("vkCreateDescriptorUpdateTemplateKHR");
                PFN_vkDestroyDescriptorUpdateTemplateKHR destroy_template =
                    (PFN_vkDestroyDescriptorUpdateTemplateKHR)proc_address("vkDestroyDescriptorUpdateTemplateKHR");
                if (create_template(VK_NULL_HANDLE, &template_info, NULL, &update_template) != VK_SUCCESS) return 10;
                if (update_template == VK_NULL_HANDLE) return 11;
                VkDescriptorUpdateTemplate bogus_template = (VkDescriptorUpdateTemplate)(uintptr_t)0x1234u;
                vkUpdateDescriptorSetWithTemplate(VK_NULL_HANDLE, VK_NULL_HANDLE, bogus_template, NULL);
                destroy_template(VK_NULL_HANDLE, bogus_template, NULL);
                destroy_template(VK_NULL_HANDLE, update_template, NULL);
                vkDestroyDescriptorSetLayout(VK_NULL_HANDLE, layout, NULL);
            #endif
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_validation_cache_extension_is_local_noop_and_shader_pnext_accepts_cache(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            int main(void) {{
            #ifndef VK_EXT_VALIDATION_CACHE_EXTENSION_NAME
                return 0;
            #else
                if (!device_extension_advertised_name(VK_EXT_VALIDATION_CACHE_EXTENSION_NAME)) {{
                    fprintf(stderr, "validation cache extension was not advertised\\n");
                    return 2;
                }}
                uint32_t extension_count = 0;
                if (vkEnumerateDeviceExtensionProperties((VkPhysicalDevice)physical_device_for_instance(NULL), NULL, &extension_count, NULL) != VK_SUCCESS ||
                    extension_count == 0) {{
                    fprintf(stderr, "device extension enumeration failed\\n");
                    return 3;
                }}
                const char *validation_enabled[] = {{ VK_EXT_VALIDATION_CACHE_EXTENSION_NAME }};
                VkDeviceCreateInfo validation_device_info;
                memset(&validation_device_info, 0, sizeof(validation_device_info));
                validation_device_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                validation_device_info.enabledExtensionCount = 1;
                validation_device_info.ppEnabledExtensionNames = validation_enabled;
                VkDevice validation_vk_device = VK_NULL_HANDLE;
                if (vkCreateDevice((VkPhysicalDevice)&g_device, &validation_device_info, NULL, &validation_vk_device) != VK_SUCCESS ||
                    validation_vk_device == VK_NULL_HANDLE) {{
                    fprintf(stderr, "validation cache test device create failed\\n");
                    return 15;
                }}

                VkValidationCacheCreateInfoEXT cache_info;
                memset(&cache_info, 0, sizeof(cache_info));
                cache_info.sType = VK_STRUCTURE_TYPE_VALIDATION_CACHE_CREATE_INFO_EXT;
                uint32_t initial_word = 0x12345678u;
                cache_info.initialDataSize = sizeof(initial_word);
                cache_info.pInitialData = &initial_word;
                VkValidationCacheEXT cache = (VkValidationCacheEXT)(uintptr_t)0x1234u;
                if (vkCreateValidationCacheEXT(VK_NULL_HANDLE, &cache_info, NULL, &cache) != VK_ERROR_INITIALIZATION_FAILED ||
                    cache != VK_NULL_HANDLE) {{
                    fprintf(stderr, "validation cache accepted null device or left stale output\\n");
                    vkDestroyDevice(validation_vk_device, NULL);
                    return 16;
                }}
                cache = VK_NULL_HANDLE;
                if (vkCreateValidationCacheEXT(validation_vk_device, &cache_info, NULL, &cache) != VK_SUCCESS ||
                    cache == VK_NULL_HANDLE) {{
                    fprintf(stderr, "local validation cache create failed\\n");
                    vkDestroyDevice(validation_vk_device, NULL);
                    return 4;
                }}
                size_t cache_data_size = 99;
                if (vkGetValidationCacheDataEXT(validation_vk_device, cache, &cache_data_size, NULL) != VK_SUCCESS ||
                    cache_data_size != 0) {{
                    fprintf(stderr, "validation cache data query was not empty noop\\n");
                    return 5;
                }}
                VkValidationCacheEXT invalid_cache = (VkValidationCacheEXT)(uintptr_t)0x1234u;
                if (vkGetValidationCacheDataEXT(validation_vk_device, invalid_cache, &cache_data_size, NULL) == VK_SUCCESS) {{
                    fprintf(stderr, "validation cache data accepted invalid cache handle\\n");
                    return 11;
                }}
                if (vkMergeValidationCachesEXT(validation_vk_device, invalid_cache, 1, &cache) == VK_SUCCESS) {{
                    fprintf(stderr, "validation cache merge accepted invalid destination handle\\n");
                    return 12;
                }}
                if (vkMergeValidationCachesEXT(validation_vk_device, cache, 1, &invalid_cache) == VK_SUCCESS) {{
                    fprintf(stderr, "validation cache merge accepted invalid source handle\\n");
                    return 13;
                }}
                if (vkMergeValidationCachesEXT(validation_vk_device, cache, 1, NULL) == VK_SUCCESS) {{
                    fprintf(stderr, "validation cache merge accepted missing source array\\n");
                    return 6;
                }}
                if (vkMergeValidationCachesEXT(validation_vk_device, cache, 1, &cache) != VK_SUCCESS) {{
                    fprintf(stderr, "validation cache merge noop failed\\n");
                    return 7;
                }}

                const uint32_t shader_words[] = {{ 0x07230203u, 0x00010000u, 0u, 0u }};
                VkShaderModuleValidationCacheCreateInfoEXT shader_cache;
                memset(&shader_cache, 0, sizeof(shader_cache));
                shader_cache.sType = VK_STRUCTURE_TYPE_SHADER_MODULE_VALIDATION_CACHE_CREATE_INFO_EXT;
                shader_cache.validationCache = cache;
                VkShaderModuleCreateInfo shader_info;
                memset(&shader_info, 0, sizeof(shader_info));
                shader_info.sType = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO;
                shader_info.pNext = &shader_cache;
                shader_info.codeSize = sizeof(shader_words);
                shader_info.pCode = shader_words;
                VkShaderModule shader = VK_NULL_HANDLE;
                if (vkCreateShaderModule(VK_NULL_HANDLE, &shader_info, NULL, &shader) == VK_SUCCESS) {{
                    fprintf(stderr, "shader module accepted validation cache pNext without extension enable-state\\n");
                    vkDestroyShaderModule(VK_NULL_HANDLE, shader, NULL);
                    return 8;
                }}
                shader = VK_NULL_HANDLE;
                if (vkCreateShaderModule(validation_vk_device, &shader_info, NULL, &shader) != VK_SUCCESS ||
                    shader == VK_NULL_HANDLE) {{
                    fprintf(stderr, "shader module rejected enabled local validation cache pNext\\n");
                    return 10;
                }}
                vkDestroyShaderModule(validation_vk_device, shader, NULL);
                shader_cache.validationCache = invalid_cache;
                shader = VK_NULL_HANDLE;
                if (vkCreateShaderModule(validation_vk_device, &shader_info, NULL, &shader) == VK_SUCCESS) {{
                    fprintf(stderr, "shader module accepted invalid validation cache handle\\n");
                    vkDestroyShaderModule(validation_vk_device, shader, NULL);
                    return 14;
                }}
                shader_cache.validationCache = cache;

                VkBaseInStructure unknown;
                memset(&unknown, 0, sizeof(unknown));
                unknown.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO;
                shader_cache.pNext = &unknown;
                if (vkCreateShaderModule(validation_vk_device, &shader_info, NULL, &shader) == VK_SUCCESS) {{
                    fprintf(stderr, "shader module accepted unknown validation-cache pNext chain\\n");
                    vkDestroyShaderModule(validation_vk_device, shader, NULL);
                    return 9;
                }}
                vkDestroyValidationCacheEXT(validation_vk_device, cache, NULL);
                vkDestroyDevice(validation_vk_device, NULL);
                return 0;
            #endif
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_shader_layout_pipeline_handles_fail_closed_after_destroy(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            int main(void) {{
                VkDescriptorSetLayoutBinding binding;
                memset(&binding, 0, sizeof(binding));
                binding.binding = 0;
                binding.descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
                binding.descriptorCount = 1;
                binding.stageFlags = VK_SHADER_STAGE_COMPUTE_BIT;

                VkDescriptorSetLayoutCreateInfo dsl_info;
                memset(&dsl_info, 0, sizeof(dsl_info));
                dsl_info.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO;
                dsl_info.bindingCount = 1;
                dsl_info.pBindings = &binding;
                VkDescriptorSetLayout dsl = VK_NULL_HANDLE;
                if (vkCreateDescriptorSetLayout(VK_NULL_HANDLE, &dsl_info, NULL, &dsl) != VK_SUCCESS ||
                    dsl == VK_NULL_HANDLE) {{
                    fprintf(stderr, "descriptor set layout create failed\\n");
                    return 2;
                }}
                PdockerVkDescriptorSetLayout *dsl_obj = descriptor_set_layout_handle_lookup(dsl);
                if (!dsl_obj || dsl_obj->destroyed) {{
                    fprintf(stderr, "descriptor set layout was not registered live\\n");
                    return 3;
                }}

                VkPipelineLayoutCreateInfo pl_info;
                memset(&pl_info, 0, sizeof(pl_info));
                pl_info.sType = VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO;
                pl_info.setLayoutCount = 1;
                pl_info.pSetLayouts = &dsl;
                VkPipelineLayout pl = VK_NULL_HANDLE;
                if (vkCreatePipelineLayout(VK_NULL_HANDLE, &pl_info, NULL, &pl) != VK_SUCCESS ||
                    pl == VK_NULL_HANDLE) {{
                    fprintf(stderr, "pipeline layout create failed\\n");
                    return 4;
                }}
                PdockerVkPipelineLayout *pl_obj = pipeline_layout_handle_lookup(pl);
                if (!pl_obj || pl_obj->destroyed || pl_obj->set_layout_count != 1 ||
                    pl_obj->set_layouts[0] != dsl_obj) {{
                    fprintf(stderr, "pipeline layout did not retain the live descriptor layout\\n");
                    return 5;
                }}

                const uint32_t shader_words[] = {{ 0x07230203u, 0x00010000u, 0u, 0u }};
                VkShaderModuleCreateInfo shader_info;
                memset(&shader_info, 0, sizeof(shader_info));
                shader_info.sType = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO;
                shader_info.codeSize = sizeof(shader_words);
                shader_info.pCode = shader_words;
                VkShaderModule shader = VK_NULL_HANDLE;
                if (vkCreateShaderModule(VK_NULL_HANDLE, &shader_info, NULL, &shader) != VK_SUCCESS ||
                    shader == VK_NULL_HANDLE) {{
                    fprintf(stderr, "shader module create failed\\n");
                    return 6;
                }}
                PdockerVkShaderModule *shader_obj = shader_module_handle_lookup(shader);
                if (!shader_obj || shader_obj->destroyed || shader_obj->code_size != sizeof(shader_words) ||
                    !shader_obj->code_map || memcmp(shader_obj->code_map, shader_words, sizeof(shader_words)) != 0) {{
                    fprintf(stderr, "shader module was not registered live with retained code\\n");
                    return 7;
                }}

                VkComputePipelineCreateInfo cp_info;
                memset(&cp_info, 0, sizeof(cp_info));
                cp_info.sType = VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO;
                cp_info.stage.sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO;
                cp_info.stage.stage = VK_SHADER_STAGE_COMPUTE_BIT;
                cp_info.stage.module = shader;
                cp_info.stage.pName = "main";
                cp_info.layout = pl;
                VkPipeline pipeline = VK_NULL_HANDLE;
                if (vkCreateComputePipelines(VK_NULL_HANDLE, VK_NULL_HANDLE, 1, &cp_info, NULL, &pipeline) != VK_SUCCESS ||
                    pipeline == VK_NULL_HANDLE) {{
                    fprintf(stderr, "compute pipeline create failed\\n");
                    return 8;
                }}
                PdockerVkPipeline *pipeline_obj = pipeline_handle_lookup(pipeline);
                if (!pipeline_obj || pipeline_obj->destroyed || pipeline_obj->shader != shader_obj ||
                    pipeline_obj->layout != pl_obj || !pipeline_obj->entry_name ||
                    strcmp(pipeline_obj->entry_name, "main") != 0) {{
                    fprintf(stderr, "pipeline was not registered with retained shader/layout state\\n");
                    return 9;
                }}

                vkDestroyShaderModule(VK_NULL_HANDLE, shader, NULL);
                if (shader_module_handle_lookup(shader) != NULL || !shader_obj->destroyed ||
                    shader_obj->code_size != sizeof(shader_words) || !shader_obj->code_map ||
                    memcmp(shader_obj->code_map, shader_words, sizeof(shader_words)) != 0) {{
                    fprintf(stderr, "destroyed shader module did not move to retained tombstone\\n");
                    return 10;
                }}
                vkDestroyPipelineLayout(VK_NULL_HANDLE, pl, NULL);
                if (pipeline_layout_handle_lookup(pl) != NULL || !pl_obj->destroyed ||
                    pl_obj->set_layout_count != 1 || pl_obj->set_layouts[0] != dsl_obj) {{
                    fprintf(stderr, "destroyed pipeline layout did not move to retained tombstone\\n");
                    return 11;
                }}
                vkDestroyDescriptorSetLayout(VK_NULL_HANDLE, dsl, NULL);
                if (descriptor_set_layout_handle_lookup(dsl) != NULL || !dsl_obj->destroyed) {{
                    fprintf(stderr, "destroyed descriptor set layout remained live\\n");
                    return 12;
                }}
                if (pipeline_handle_lookup(pipeline) != pipeline_obj || pipeline_obj->shader != shader_obj ||
                    pipeline_obj->layout != pl_obj) {{
                    fprintf(stderr, "existing pipeline lost retained retired dependencies\\n");
                    return 13;
                }}

                VkPipeline bad_pipeline = VK_NULL_HANDLE;
                if (vkCreateComputePipelines(VK_NULL_HANDLE, VK_NULL_HANDLE, 1, &cp_info, NULL, &bad_pipeline) == VK_SUCCESS ||
                    bad_pipeline != VK_NULL_HANDLE) {{
                    fprintf(stderr, "compute pipeline accepted destroyed shader/layout handles\\n");
                    return 14;
                }}
                VkPipelineLayout bad_pl = VK_NULL_HANDLE;
                if (vkCreatePipelineLayout(VK_NULL_HANDLE, &pl_info, NULL, &bad_pl) == VK_SUCCESS ||
                    bad_pl != VK_NULL_HANDLE) {{
                    fprintf(stderr, "pipeline layout accepted destroyed descriptor set layout handle\\n");
                    return 15;
                }}

                vkDestroyPipeline(VK_NULL_HANDLE, pipeline, NULL);
                if (pipeline_handle_lookup(pipeline) != NULL || !pipeline_obj->destroyed) {{
                    fprintf(stderr, "destroyed pipeline remained live\\n");
                    return 16;
                }}
                vkDestroyPipeline(VK_NULL_HANDLE, pipeline, NULL);
                vkDestroyShaderModule(VK_NULL_HANDLE, shader, NULL);
                vkDestroyPipelineLayout(VK_NULL_HANDLE, pl, NULL);
                vkDestroyDescriptorSetLayout(VK_NULL_HANDLE, dsl, NULL);
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_descriptor_pool_and_set_handles_fail_closed_after_free_reset_destroy(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            static int make_layout(VkDescriptorSetLayout *layout_out) {{
                VkDescriptorSetLayoutBinding binding;
                memset(&binding, 0, sizeof(binding));
                binding.binding = 0;
                binding.descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
                binding.descriptorCount = 1;
                binding.stageFlags = VK_SHADER_STAGE_COMPUTE_BIT;

                VkDescriptorSetLayoutCreateInfo info;
                memset(&info, 0, sizeof(info));
                info.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO;
                info.bindingCount = 1;
                info.pBindings = &binding;
                return vkCreateDescriptorSetLayout(VK_NULL_HANDLE, &info, NULL, layout_out) == VK_SUCCESS ? 0 : 1;
            }}

            static int make_pool(VkDescriptorPool *pool_out) {{
                VkDescriptorPoolSize size;
                memset(&size, 0, sizeof(size));
                size.type = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
                size.descriptorCount = 4;
                VkDescriptorPoolCreateInfo info;
                memset(&info, 0, sizeof(info));
                info.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO;
                info.flags = VK_DESCRIPTOR_POOL_CREATE_FREE_DESCRIPTOR_SET_BIT;
                info.maxSets = 4;
                info.poolSizeCount = 1;
                info.pPoolSizes = &size;
                return vkCreateDescriptorPool(VK_NULL_HANDLE, &info, NULL, pool_out) == VK_SUCCESS ? 0 : 1;
            }}

            static int alloc_set(VkDescriptorPool pool, VkDescriptorSetLayout layout, VkDescriptorSet *set_out) {{
                VkDescriptorSetAllocateInfo info;
                memset(&info, 0, sizeof(info));
                info.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO;
                info.descriptorPool = pool;
                info.descriptorSetCount = 1;
                info.pSetLayouts = &layout;
                return vkAllocateDescriptorSets(VK_NULL_HANDLE, &info, set_out) == VK_SUCCESS ? 0 : 1;
            }}

            static void try_update_stale_set(VkDescriptorSet set) {{
                VkDescriptorBufferInfo buffer_info;
                memset(&buffer_info, 0, sizeof(buffer_info));
                buffer_info.buffer = VK_NULL_HANDLE;
                buffer_info.offset = 0;
                buffer_info.range = 16;
                VkWriteDescriptorSet write;
                memset(&write, 0, sizeof(write));
                write.sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;
                write.dstSet = set;
                write.dstBinding = 0;
                write.descriptorCount = 1;
                write.descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
                write.pBufferInfo = &buffer_info;
                vkUpdateDescriptorSets(VK_NULL_HANDLE, 1, &write, 0, NULL);
            }}

            int main(void) {{
                VkDescriptorSetLayout layout = VK_NULL_HANDLE;
                if (make_layout(&layout) != 0 || !descriptor_set_layout_handle_lookup(layout)) {{
                    fprintf(stderr, "layout create failed\\n");
                    return 2;
                }}
                VkDescriptorPool pool = VK_NULL_HANDLE;
                if (make_pool(&pool) != 0 || !descriptor_pool_handle_lookup(pool)) {{
                    fprintf(stderr, "pool create/register failed\\n");
                    return 3;
                }}
                PdockerVkDescriptorPool *pool_obj = descriptor_pool_handle_lookup(pool);

                VkDescriptorSet set = VK_NULL_HANDLE;
                if (alloc_set(pool, layout, &set) != 0 || !descriptor_set_handle_lookup(set) ||
                    pool_obj->set_count != 1) {{
                    fprintf(stderr, "set allocate/register failed\\n");
                    return 4;
                }}
                PdockerVkDescriptorSet *set_obj = descriptor_set_handle_lookup(set);
                VkDescriptorPool other_pool = VK_NULL_HANDLE;
                if (make_pool(&other_pool) != 0 || !descriptor_pool_handle_lookup(other_pool)) {{
                    fprintf(stderr, "other pool create/register failed\\n");
                    return 16;
                }}
                PdockerVkDescriptorPool *other_pool_obj = descriptor_pool_handle_lookup(other_pool);
                if (vkFreeDescriptorSets(VK_NULL_HANDLE, other_pool, 1, &set) != VK_ERROR_INITIALIZATION_FAILED ||
                    descriptor_set_handle_lookup(set) != set_obj || set_obj->pool != pool_obj ||
                    pool_obj->set_count != 1 || other_pool_obj->set_count != 0) {{
                    fprintf(stderr, "wrong-pool free corrupted descriptor ownership\\n");
                    return 17;
                }}
                vkDestroyDescriptorPool(VK_NULL_HANDLE, other_pool, NULL);
                if (descriptor_pool_handle_lookup(other_pool) != NULL || !descriptor_set_handle_lookup(set)) {{
                    fprintf(stderr, "other pool destroy corrupted live descriptor set\\n");
                    return 18;
                }}
                if (vkFreeDescriptorSets(VK_NULL_HANDLE, pool, 1, &set) != VK_SUCCESS) {{
                    fprintf(stderr, "free live set failed\\n");
                    return 5;
                }}
                if (descriptor_set_handle_lookup(set) != NULL || !set_obj->destroyed ||
                    set_obj->pool != NULL || pool_obj->set_count != 0) {{
                    fprintf(stderr, "freed set remained live or tracked\\n");
                    return 6;
                }}
                try_update_stale_set(set);
                if (vkFreeDescriptorSets(VK_NULL_HANDLE, pool, 1, &set) == VK_SUCCESS) {{
                    fprintf(stderr, "free accepted stale set\\n");
                    return 7;
                }}

                VkDescriptorSet reset_set = VK_NULL_HANDLE;
                if (alloc_set(pool, layout, &reset_set) != 0 || !descriptor_set_handle_lookup(reset_set)) {{
                    fprintf(stderr, "reset set allocate failed\\n");
                    return 8;
                }}
                PdockerVkDescriptorSet *reset_set_obj = descriptor_set_handle_lookup(reset_set);
                if (vkResetDescriptorPool(VK_NULL_HANDLE, pool, 0) != VK_SUCCESS) {{
                    fprintf(stderr, "pool reset failed\\n");
                    return 9;
                }}
                if (descriptor_set_handle_lookup(reset_set) != NULL || !reset_set_obj->destroyed ||
                    reset_set_obj->pool != NULL || pool_obj->set_count != 0) {{
                    fprintf(stderr, "pool reset left set live/tracked\\n");
                    return 10;
                }}
                try_update_stale_set(reset_set);

                VkDescriptorSet destroy_set = VK_NULL_HANDLE;
                if (alloc_set(pool, layout, &destroy_set) != 0 || !descriptor_set_handle_lookup(destroy_set)) {{
                    fprintf(stderr, "destroy set allocate failed\\n");
                    return 11;
                }}
                PdockerVkDescriptorSet *destroy_set_obj = descriptor_set_handle_lookup(destroy_set);
                vkDestroyDescriptorPool(VK_NULL_HANDLE, pool, NULL);
                if (descriptor_pool_handle_lookup(pool) != NULL || !pool_obj->destroyed ||
                    pool_obj->sets != NULL || descriptor_set_handle_lookup(destroy_set) != NULL ||
                    !destroy_set_obj->destroyed || destroy_set_obj->pool != NULL) {{
                    fprintf(stderr, "pool destroy left pool/set live\\n");
                    return 12;
                }}
                if (vkResetDescriptorPool(VK_NULL_HANDLE, pool, 0) == VK_SUCCESS) {{
                    fprintf(stderr, "reset accepted destroyed pool\\n");
                    return 13;
                }}
                VkDescriptorSet bad_set = VK_NULL_HANDLE;
                if (alloc_set(pool, layout, &bad_set) == 0 || bad_set != VK_NULL_HANDLE) {{
                    fprintf(stderr, "allocate accepted destroyed pool\\n");
                    return 14;
                }}
                if (vkFreeDescriptorSets(VK_NULL_HANDLE, pool, 1, &destroy_set) == VK_SUCCESS) {{
                    fprintf(stderr, "free accepted destroyed pool with stale set\\n");
                    return 15;
                }}
                vkDestroyDescriptorPool(VK_NULL_HANDLE, pool, NULL);
                vkDestroyDescriptorSetLayout(VK_NULL_HANDLE, layout, NULL);
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_pipeline_cache_handles_fail_closed_after_destroy(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            int main(void) {{
                VkDeviceCreateInfo device_info;
                memset(&device_info, 0, sizeof(device_info));
                device_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                VkDevice device = VK_NULL_HANDLE;
                if (vkCreateDevice((VkPhysicalDevice)&g_device, &device_info, NULL, &device) != VK_SUCCESS ||
                    device == VK_NULL_HANDLE) {{
                    fprintf(stderr, "pipeline cache test device create failed\\n");
                    return 11;
                }}

                VkPipelineCacheCreateInfo info;
                memset(&info, 0, sizeof(info));
                info.sType = VK_STRUCTURE_TYPE_PIPELINE_CACHE_CREATE_INFO;
                VkPipelineCache cache = (VkPipelineCache)(uintptr_t)0x1234u;
                if (vkCreatePipelineCache(VK_NULL_HANDLE, &info, NULL, &cache) != VK_ERROR_INITIALIZATION_FAILED ||
                    cache != VK_NULL_HANDLE) {{
                    fprintf(stderr, "pipeline cache accepted null device or left stale output\\n");
                    vkDestroyDevice(device, NULL);
                    return 12;
                }}
                cache = (VkPipelineCache)(uintptr_t)0x1234u;
                if (vkCreatePipelineCache(device, NULL, NULL, &cache) != VK_ERROR_INITIALIZATION_FAILED ||
                    cache != VK_NULL_HANDLE) {{
                    fprintf(stderr, "pipeline cache accepted null create info or left stale output\\n");
                    vkDestroyDevice(device, NULL);
                    return 13;
                }}
                info.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO;
                cache = (VkPipelineCache)(uintptr_t)0x1234u;
                if (vkCreatePipelineCache(device, &info, NULL, &cache) != VK_ERROR_INITIALIZATION_FAILED ||
                    cache != VK_NULL_HANDLE) {{
                    fprintf(stderr, "pipeline cache accepted wrong sType or left stale output\\n");
                    vkDestroyDevice(device, NULL);
                    return 14;
                }}
                info.sType = VK_STRUCTURE_TYPE_PIPELINE_CACHE_CREATE_INFO;
                VkBaseInStructure unknown;
                memset(&unknown, 0, sizeof(unknown));
                unknown.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO;
                info.pNext = &unknown;
                if (vkCreatePipelineCache(device, &info, NULL, &cache) != VK_ERROR_FEATURE_NOT_PRESENT) {{
                    fprintf(stderr, "pipeline cache accepted unsupported pNext\\n");
                    vkDestroyDevice(device, NULL);
                    return 15;
                }}
                info.pNext = NULL;
                info.flags = (VkPipelineCacheCreateFlags)1u;
                if (vkCreatePipelineCache(device, &info, NULL, &cache) != VK_ERROR_FEATURE_NOT_PRESENT) {{
                    fprintf(stderr, "pipeline cache accepted unsupported flags\\n");
                    vkDestroyDevice(device, NULL);
                    return 16;
                }}
                info.flags = 0;
                info.initialDataSize = 4;
                info.pInitialData = NULL;
                if (vkCreatePipelineCache(device, &info, NULL, &cache) != VK_ERROR_FEATURE_NOT_PRESENT) {{
                    fprintf(stderr, "pipeline cache accepted missing initial data\\n");
                    vkDestroyDevice(device, NULL);
                    return 17;
                }}
                uint32_t initial_word = 0x12345678u;
                info.pInitialData = &initial_word;
                cache = VK_NULL_HANDLE;
                if (vkCreatePipelineCache(device, &info, NULL, &cache) != VK_SUCCESS ||
                    cache == VK_NULL_HANDLE) {{
                    fprintf(stderr, "pipeline cache create failed\\n");
                    vkDestroyDevice(device, NULL);
                    return 2;
                }}
                PdockerVkPipelineCache *cache_obj = pipeline_cache_handle_lookup(cache);
                if (!cache_obj || cache_obj->destroyed) {{
                    fprintf(stderr, "pipeline cache not registered live\\n");
                    vkDestroyDevice(device, NULL);
                    return 3;
                }}
                size_t data_size = 99;
                if (vkGetPipelineCacheData(device, cache, &data_size, NULL) != VK_SUCCESS ||
                    data_size != 0) {{
                    fprintf(stderr, "pipeline cache data query failed\\n");
                    vkDestroyDevice(device, NULL);
                    return 4;
                }}
                if (vkMergePipelineCaches(device, cache, 1, &cache) != VK_SUCCESS) {{
                    fprintf(stderr, "pipeline cache self merge failed\\n");
                    vkDestroyDevice(device, NULL);
                    return 5;
                }}
                VkPipelineCache fake_cache = pdocker_vk_pipeline_cache_to_handle((PdockerVkPipelineCache *)(uintptr_t)0x1234000u);
                if (vkMergePipelineCaches(device, cache, 1, &fake_cache) == VK_SUCCESS) {{
                    fprintf(stderr, "pipeline cache merge accepted fake source\\n");
                    vkDestroyDevice(device, NULL);
                    return 6;
                }}
                if (vkGetPipelineCacheData(device, fake_cache, &data_size, NULL) == VK_SUCCESS) {{
                    fprintf(stderr, "pipeline cache data accepted fake cache\\n");
                    vkDestroyDevice(device, NULL);
                    return 7;
                }}
                vkDestroyPipelineCache(device, cache, NULL);
                if (pipeline_cache_handle_lookup(cache) != NULL || !cache_obj->destroyed) {{
                    fprintf(stderr, "destroyed pipeline cache remained live\\n");
                    vkDestroyDevice(device, NULL);
                    return 8;
                }}
                if (vkGetPipelineCacheData(device, cache, &data_size, NULL) == VK_SUCCESS) {{
                    fprintf(stderr, "pipeline cache data accepted destroyed cache\\n");
                    vkDestroyDevice(device, NULL);
                    return 9;
                }}
                if (vkMergePipelineCaches(device, cache, 0, NULL) == VK_SUCCESS) {{
                    fprintf(stderr, "pipeline cache merge accepted destroyed destination\\n");
                    vkDestroyDevice(device, NULL);
                    return 10;
                }}
                vkDestroyPipelineCache(device, cache, NULL);
                vkDestroyDevice(device, NULL);
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_conservative_query_instance_extensions_are_advertised_with_aliases(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            static int extension_seen(
                    const VkExtensionProperties *extensions,
                    uint32_t count,
                    const char *name) {{
                for (uint32_t i = 0; i < count; ++i) {{
                    if (strcmp(extensions[i].extensionName, name) == 0) return 1;
                }}
                return 0;
            }}

            int main(void) {{
                uint32_t count = 0;
                if (vkEnumerateInstanceExtensionProperties(NULL, &count, NULL) != VK_SUCCESS) return 2;
                if (count == 0 || count > PDOCKER_VK_MAX_INSTANCE_EXTENSIONS) return 3;
                VkExtensionProperties extensions[PDOCKER_VK_MAX_INSTANCE_EXTENSIONS];
                memset(extensions, 0, sizeof(extensions));
                uint32_t capacity = PDOCKER_VK_MAX_INSTANCE_EXTENSIONS;
                if (vkEnumerateInstanceExtensionProperties(NULL, &capacity, extensions) != VK_SUCCESS) return 4;
                if (capacity != count) return 5;

                const char *required[] = {{
                    VK_KHR_GET_PHYSICAL_DEVICE_PROPERTIES_2_EXTENSION_NAME,
                    VK_KHR_DEVICE_GROUP_CREATION_EXTENSION_NAME,
                    VK_KHR_EXTERNAL_MEMORY_CAPABILITIES_EXTENSION_NAME,
                    VK_KHR_EXTERNAL_SEMAPHORE_CAPABILITIES_EXTENSION_NAME,
                    VK_KHR_EXTERNAL_FENCE_CAPABILITIES_EXTENSION_NAME,
                }};
                for (uint32_t i = 0; i < sizeof(required) / sizeof(required[0]); ++i) {{
                    if (!extension_seen(extensions, capacity, required[i])) return 10 + (int)i;
                    if (!instance_extension_advertised_name(required[i])) return 20 + (int)i;
                }}

                const char *enabled[] = {{
                    VK_KHR_GET_PHYSICAL_DEVICE_PROPERTIES_2_EXTENSION_NAME,
                    VK_KHR_DEVICE_GROUP_CREATION_EXTENSION_NAME,
                    VK_KHR_EXTERNAL_MEMORY_CAPABILITIES_EXTENSION_NAME,
                    VK_KHR_EXTERNAL_SEMAPHORE_CAPABILITIES_EXTENSION_NAME,
                    VK_KHR_EXTERNAL_FENCE_CAPABILITIES_EXTENSION_NAME,
                }};
                VkInstanceCreateInfo info;
                memset(&info, 0, sizeof(info));
                info.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO;
                info.enabledExtensionCount = (uint32_t)(sizeof(enabled) / sizeof(enabled[0]));
                info.ppEnabledExtensionNames = enabled;
                if (validate_instance_extensions(&info) != VK_SUCCESS) return 30;

                if (proc_address("vkGetPhysicalDeviceProperties2KHR") == NULL) return 31;
                if (proc_address("vkGetPhysicalDeviceFeatures2KHR") == NULL) return 32;
                if (proc_address("vkGetPhysicalDeviceSparseImageFormatProperties2KHR") == NULL) return 33;
                if (proc_address("vkEnumeratePhysicalDeviceGroupsKHR") == NULL) return 34;
                if (proc_address("vkGetPhysicalDeviceExternalBufferPropertiesKHR") == NULL) return 35;
                if (proc_address("vkGetPhysicalDeviceExternalSemaphorePropertiesKHR") == NULL) return 36;
                if (proc_address("vkGetPhysicalDeviceExternalFencePropertiesKHR") == NULL) return 37;
                if (proc_address("vkGetDeviceGroupPeerMemoryFeaturesKHR") == NULL) return 38;
                if (proc_address("vkCmdSetDeviceMaskKHR") == NULL) return 39;
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_instance_procaddr_filters_null_instance_and_disabled_extensions(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            int main(void) {{
                if (vkGetInstanceProcAddr(VK_NULL_HANDLE, "vkCreateInstance") == NULL) return 1;
                if (vkGetInstanceProcAddr(VK_NULL_HANDLE, "vkDestroyInstance") != NULL) return 2;
                if (vkGetInstanceProcAddr(VK_NULL_HANDLE, "vkCreateHeadlessSurfaceEXT") != NULL) return 3;
                PdockerVkInstance fake_instance;
                memset(&fake_instance, 0, sizeof(fake_instance));
                fake_instance.object_id = 0x1234;
                fake_instance.enabled_extension_mask = UINT64_MAX;
                if (vkGetInstanceProcAddr((VkInstance)&fake_instance, "vkCreateInstance") == NULL) return 31;
                if (vkGetInstanceProcAddr((VkInstance)&fake_instance, "vkDestroySurfaceKHR") != NULL) return 32;
                if (vk_icdGetPhysicalDeviceProcAddr((VkInstance)&fake_instance, "vkGetPhysicalDeviceProperties2KHR") != NULL) return 33;
                vkDestroyInstance((VkInstance)&fake_instance, NULL);

                VkInstanceCreateInfo info;
                memset(&info, 0, sizeof(info));
                info.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO;
                VkInstance instance = VK_NULL_HANDLE;
                if (vkCreateInstance(&info, NULL, &instance) != VK_SUCCESS || instance == VK_NULL_HANDLE) return 4;
                if (vkGetInstanceProcAddr(instance, "vkDestroyInstance") == NULL) return 5;
                if (vkGetInstanceProcAddr(instance, "vkCreateHeadlessSurfaceEXT") != NULL) return 6;
                if (vkGetInstanceProcAddr(instance, "vkDestroySurfaceKHR") != NULL) return 7;
                if (vkGetInstanceProcAddr(instance, "vkGetPhysicalDevicePresentRectanglesKHR") != NULL) return 34;
            #ifdef VK_EXT_DEBUG_UTILS_EXTENSION_NAME
                if (vkGetInstanceProcAddr(instance, "vkCreateDebugUtilsMessengerEXT") != NULL) return 8;
            #endif
                if (vk_icdGetPhysicalDeviceProcAddr(instance, "vkGetPhysicalDeviceProperties2KHR") != NULL) return 9;
                VkInstance stale_instance = instance;
                vkDestroyInstance(instance, NULL);
                if (vkGetInstanceProcAddr(stale_instance, "vkDestroyInstance") != NULL) return 35;
                vkDestroyInstance(stale_instance, NULL);

                const char *enabled[4];
                uint32_t enabled_count = 0;
                enabled[enabled_count++] = VK_KHR_SURFACE_EXTENSION_NAME;
                enabled[enabled_count++] = VK_EXT_HEADLESS_SURFACE_EXTENSION_NAME;
                enabled[enabled_count++] = VK_KHR_GET_PHYSICAL_DEVICE_PROPERTIES_2_EXTENSION_NAME;
            #ifdef VK_EXT_DEBUG_UTILS_EXTENSION_NAME
                enabled[enabled_count++] = VK_EXT_DEBUG_UTILS_EXTENSION_NAME;
            #endif
                memset(&info, 0, sizeof(info));
                info.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO;
                info.enabledExtensionCount = enabled_count;
                info.ppEnabledExtensionNames = enabled;
                instance = VK_NULL_HANDLE;
                if (vkCreateInstance(&info, NULL, &instance) != VK_SUCCESS || instance == VK_NULL_HANDLE) return 10;
                if (vkGetInstanceProcAddr(instance, "vkDestroySurfaceKHR") == NULL) return 11;
                if (vkGetInstanceProcAddr(instance, "vkGetPhysicalDevicePresentRectanglesKHR") == NULL) return 36;
                if (vkGetInstanceProcAddr(instance, "vkCreateHeadlessSurfaceEXT") == NULL) return 12;
                if (vk_icdGetPhysicalDeviceProcAddr(instance, "vkGetPhysicalDeviceProperties2KHR") == NULL) return 13;
            #ifdef VK_EXT_DEBUG_UTILS_EXTENSION_NAME
                if (vkGetInstanceProcAddr(instance, "vkCreateDebugUtilsMessengerEXT") == NULL) return 14;
            #endif
                vkDestroyInstance(instance, NULL);
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_tooling_info_extension_reports_bridge_metadata(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            int main(void) {{
            #ifndef VK_EXT_TOOLING_INFO_EXTENSION_NAME
                return 0;
            #else
                if (!device_extension_advertised_name(VK_EXT_TOOLING_INFO_EXTENSION_NAME)) return 2;
                uint32_t extension_count = 0;
                if (vkEnumerateDeviceExtensionProperties((VkPhysicalDevice)physical_device_for_instance(NULL), NULL, &extension_count, NULL) != VK_SUCCESS ||
                    extension_count == 0) return 3;
                VkExtensionProperties extensions[64];
                memset(extensions, 0, sizeof(extensions));
                uint32_t capacity = 64;
                if (vkEnumerateDeviceExtensionProperties((VkPhysicalDevice)physical_device_for_instance(NULL), NULL, &capacity, extensions) != VK_SUCCESS) return 4;
                VkBool32 found = VK_FALSE;
                for (uint32_t i = 0; i < capacity; ++i) {{
                    if (strcmp(extensions[i].extensionName, VK_EXT_TOOLING_INFO_EXTENSION_NAME) == 0) found = VK_TRUE;
                }}
                if (!found) return 5;

                if (!vkGetPhysicalDeviceToolProperties || !vkGetPhysicalDeviceToolPropertiesEXT) return 6;
                if (proc_address("vkGetPhysicalDeviceToolProperties") != NULL) return 15;
                if (proc_address("vkGetPhysicalDeviceToolPropertiesEXT") == NULL) return 16;
                if (vkGetPhysicalDeviceToolProperties(VK_NULL_HANDLE, NULL, NULL) != VK_ERROR_INITIALIZATION_FAILED) return 7;
                uint32_t invalid_tool_count = 1;
                if (vkGetPhysicalDeviceToolPropertiesEXT(VK_NULL_HANDLE, &invalid_tool_count, NULL) != VK_ERROR_INITIALIZATION_FAILED ||
                    invalid_tool_count != 0) return 8;
                VkPhysicalDevice physical = (VkPhysicalDevice)physical_device_for_instance(NULL);
                uint32_t tool_count = 0;
                if (vkGetPhysicalDeviceToolPropertiesEXT(physical, &tool_count, NULL) != VK_SUCCESS ||
                    tool_count != 1) return 18;

                VkPhysicalDeviceToolProperties tool;
                memset(&tool, 0, sizeof(tool));
                tool.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_TOOL_PROPERTIES;
                uint32_t zero_capacity = 0;
                if (vkGetPhysicalDeviceToolProperties(physical, &zero_capacity, &tool) != VK_INCOMPLETE ||
                    zero_capacity != 0) return 9;

                memset(&tool, 0, sizeof(tool));
                tool.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_TOOL_PROPERTIES;
                uint32_t one_capacity = 1;
                if (vkGetPhysicalDeviceToolPropertiesEXT(physical, &one_capacity, &tool) != VK_SUCCESS ||
                    one_capacity != 1) return 10;
                if (tool.sType != VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_TOOL_PROPERTIES) return 11;
                if (strstr(tool.name, "Skydnir") == NULL) return 12;
                if ((tool.purposes & VK_TOOL_PURPOSE_DEBUG_MARKERS_BIT_EXT) == 0) return 13;

                const char *enabled[] = {{ VK_EXT_TOOLING_INFO_EXTENSION_NAME }};
                VkDeviceCreateInfo create_info;
                memset(&create_info, 0, sizeof(create_info));
                create_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                create_info.enabledExtensionCount = 1;
                create_info.ppEnabledExtensionNames = enabled;
                if (validate_device_extensions(&create_info) != VK_SUCCESS) return 14;
                return 0;
            #endif
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_driver_properties_extension_reports_skydnir_bridge_metadata(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            int main(void) {{
            #ifndef VK_KHR_DRIVER_PROPERTIES_EXTENSION_NAME
                return 0;
            #else
                if (!device_extension_advertised_name(VK_KHR_DRIVER_PROPERTIES_EXTENSION_NAME)) return 2;
                uint32_t extension_count = 0;
                if (vkEnumerateDeviceExtensionProperties((VkPhysicalDevice)physical_device_for_instance(NULL), NULL, &extension_count, NULL) != VK_SUCCESS ||
                    extension_count == 0) return 3;
                VkExtensionProperties extensions[64];
                memset(extensions, 0, sizeof(extensions));
                uint32_t capacity = 64;
                if (vkEnumerateDeviceExtensionProperties((VkPhysicalDevice)physical_device_for_instance(NULL), NULL, &capacity, extensions) != VK_SUCCESS) return 4;
                VkBool32 found = VK_FALSE;
                for (uint32_t i = 0; i < capacity; ++i) {{
                    if (strcmp(extensions[i].extensionName, VK_KHR_DRIVER_PROPERTIES_EXTENSION_NAME) == 0) found = VK_TRUE;
                }}
                if (!found) return 5;

                const char *enabled[] = {{ VK_KHR_DRIVER_PROPERTIES_EXTENSION_NAME }};
                VkDeviceCreateInfo create_info;
                memset(&create_info, 0, sizeof(create_info));
                create_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                create_info.enabledExtensionCount = 1;
                create_info.ppEnabledExtensionNames = enabled;
                if (validate_device_extensions(&create_info) != VK_SUCCESS) return 6;

                VkPhysicalDeviceProperties2 properties2;
                VkPhysicalDeviceDriverProperties driver;
                memset(&properties2, 0, sizeof(properties2));
                memset(&driver, 0xff, sizeof(driver));
                properties2.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2;
                properties2.pNext = &driver;
                driver.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DRIVER_PROPERTIES;
                driver.pNext = NULL;
                vkGetPhysicalDeviceProperties2((VkPhysicalDevice)physical_device_for_instance(NULL), &properties2);
                if (driver.sType != VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DRIVER_PROPERTIES ||
                    driver.pNext != NULL) return 7;
                if (strstr(driver.driverName, "skydnir-vulkan-bridge") == NULL) {{
                    fprintf(stderr, "unexpected driver name: %s\\n", driver.driverName);
                    return 8;
                }}
                if (strstr(driver.driverInfo, "Skydnir neutral Vulkan bridge") == NULL) {{
                    fprintf(stderr, "unexpected driver info: %s\\n", driver.driverInfo);
                    return 9;
                }}
                if (strstr(driver.driverName, "pdocker") != NULL ||
                    strstr(driver.driverInfo, "pdocker") != NULL) return 10;
                if (driver.conformanceVersion.major != 1 || driver.conformanceVersion.minor != 2) return 11;
                return 0;
            #endif
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_memory_budget_extension_reports_conservative_heap_budget(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            int main(void) {{
            #ifndef VK_EXT_MEMORY_BUDGET_EXTENSION_NAME
                return 0;
            #else
                if (!device_extension_advertised_name(VK_EXT_MEMORY_BUDGET_EXTENSION_NAME)) return 2;
                uint32_t extension_count = 0;
                if (vkEnumerateDeviceExtensionProperties((VkPhysicalDevice)physical_device_for_instance(NULL), NULL, &extension_count, NULL) != VK_SUCCESS ||
                    extension_count == 0) return 3;
                VkExtensionProperties extensions[64];
                memset(extensions, 0, sizeof(extensions));
                uint32_t capacity = 64;
                if (vkEnumerateDeviceExtensionProperties((VkPhysicalDevice)physical_device_for_instance(NULL), NULL, &capacity, extensions) != VK_SUCCESS) return 4;
                VkBool32 found = VK_FALSE;
                for (uint32_t i = 0; i < capacity; ++i) {{
                    if (strcmp(extensions[i].extensionName, VK_EXT_MEMORY_BUDGET_EXTENSION_NAME) == 0) found = VK_TRUE;
                }}
                if (!found) return 5;

                const char *enabled[] = {{ VK_EXT_MEMORY_BUDGET_EXTENSION_NAME }};
                VkDeviceCreateInfo create_info;
                memset(&create_info, 0, sizeof(create_info));
                create_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                create_info.enabledExtensionCount = 1;
                create_info.ppEnabledExtensionNames = enabled;
                if (validate_device_extensions(&create_info) != VK_SUCCESS) return 6;

                VkPhysicalDeviceMemoryProperties2 memory2;
                VkPhysicalDeviceMemoryBudgetPropertiesEXT budget;
                memset(&memory2, 0, sizeof(memory2));
                memset(&budget, 0xff, sizeof(budget));
                memory2.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MEMORY_PROPERTIES_2;
                memory2.pNext = &budget;
                budget.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MEMORY_BUDGET_PROPERTIES_EXT;
                budget.pNext = NULL;
                vkGetPhysicalDeviceMemoryProperties2((VkPhysicalDevice)physical_device_for_instance(NULL), &memory2);
                if (budget.sType != VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MEMORY_BUDGET_PROPERTIES_EXT ||
                    budget.pNext != NULL) return 7;
                if (memory2.memoryProperties.memoryHeapCount < 2) return 8;
                for (uint32_t i = 0; i < memory2.memoryProperties.memoryHeapCount; ++i) {{
                    if (budget.heapBudget[i] != memory2.memoryProperties.memoryHeaps[i].size) {{
                        fprintf(stderr, "heap %u budget %llu != heap size %llu\\n",
                                i,
                                (unsigned long long)budget.heapBudget[i],
                                (unsigned long long)memory2.memoryProperties.memoryHeaps[i].size);
                        return 9;
                    }}
                }}
                const VkDeviceSize initial_heap0 = budget.heapUsage[0];
                const VkDeviceSize initial_heap1 = budget.heapUsage[1];

                VkMemoryAllocateInfo alloc_info;
                memset(&alloc_info, 0, sizeof(alloc_info));
                alloc_info.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO;
                alloc_info.allocationSize = 4096;
                alloc_info.memoryTypeIndex = 1;
                VkDeviceMemory memory = VK_NULL_HANDLE;
                if (vkAllocateMemory(VK_NULL_HANDLE, &alloc_info, NULL, &memory) != VK_SUCCESS ||
                    memory == VK_NULL_HANDLE) return 10;

                memset(&memory2, 0, sizeof(memory2));
                memset(&budget, 0, sizeof(budget));
                memory2.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MEMORY_PROPERTIES_2;
                memory2.pNext = &budget;
                budget.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MEMORY_BUDGET_PROPERTIES_EXT;
                vkGetPhysicalDeviceMemoryProperties2((VkPhysicalDevice)physical_device_for_instance(NULL), &memory2);
                if (budget.heapUsage[0] != initial_heap0) return 11;
                if (budget.heapUsage[1] != initial_heap1 + alloc_info.allocationSize) {{
                    fprintf(stderr, "heap 1 usage %llu != expected %llu\\n",
                            (unsigned long long)budget.heapUsage[1],
                            (unsigned long long)(initial_heap1 + alloc_info.allocationSize));
                    return 12;
                }}

                vkFreeMemory(VK_NULL_HANDLE, memory, NULL);
                memset(&memory2, 0, sizeof(memory2));
                memset(&budget, 0, sizeof(budget));
                memory2.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MEMORY_PROPERTIES_2;
                memory2.pNext = &budget;
                budget.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MEMORY_BUDGET_PROPERTIES_EXT;
                vkGetPhysicalDeviceMemoryProperties2((VkPhysicalDevice)physical_device_for_instance(NULL), &memory2);
                if (budget.heapUsage[0] != initial_heap0) return 13;
                if (budget.heapUsage[1] != initial_heap1) return 14;
                return 0;
            #endif
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_host_query_reset_extension_aliases_core_reset(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            int main(void) {{
            #ifndef VK_EXT_HOST_QUERY_RESET_EXTENSION_NAME
                return 0;
            #else
                if (!device_extension_advertised_name(VK_EXT_HOST_QUERY_RESET_EXTENSION_NAME)) return 2;
                uint32_t extension_count = 0;
                if (vkEnumerateDeviceExtensionProperties((VkPhysicalDevice)physical_device_for_instance(NULL), NULL, &extension_count, NULL) != VK_SUCCESS ||
                    extension_count == 0) return 3;
                VkExtensionProperties extensions[64];
                memset(extensions, 0, sizeof(extensions));
                uint32_t capacity = 64;
                if (vkEnumerateDeviceExtensionProperties((VkPhysicalDevice)physical_device_for_instance(NULL), NULL, &capacity, extensions) != VK_SUCCESS) return 4;
                VkBool32 found = VK_FALSE;
                for (uint32_t i = 0; i < capacity; ++i) {{
                    if (strcmp(extensions[i].extensionName, VK_EXT_HOST_QUERY_RESET_EXTENSION_NAME) == 0) found = VK_TRUE;
                }}
                if (!found) return 5;
                if (!vkResetQueryPool || !vkResetQueryPoolEXT) return 6;
                if (proc_address("vkResetQueryPoolEXT") != (PFN_vkVoidFunction)vkResetQueryPoolEXT) return 7;

                VkPhysicalDeviceHostQueryResetFeatures features;
                memset(&features, 0, sizeof(features));
                features.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_HOST_QUERY_RESET_FEATURES;
                fill_pnext_features(&features);
                if (features.hostQueryReset != VK_TRUE) return 8;

                const char *enabled[] = {{ VK_EXT_HOST_QUERY_RESET_EXTENSION_NAME }};
                VkDeviceCreateInfo create_info;
                memset(&create_info, 0, sizeof(create_info));
                create_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                create_info.enabledExtensionCount = 1;
                create_info.ppEnabledExtensionNames = enabled;
                if (validate_device_extensions(&create_info) != VK_SUCCESS) return 9;

                VkQueryPoolCreateInfo pool_info;
                memset(&pool_info, 0, sizeof(pool_info));
                pool_info.sType = VK_STRUCTURE_TYPE_QUERY_POOL_CREATE_INFO;
                pool_info.queryType = VK_QUERY_TYPE_TIMESTAMP;
                pool_info.queryCount = 2;
                VkQueryPool pool = VK_NULL_HANDLE;
                if (vkCreateQueryPool(VK_NULL_HANDLE, &pool_info, NULL, &pool) != VK_SUCCESS ||
                    pool == VK_NULL_HANDLE) return 10;
                vkResetQueryPoolEXT(VK_NULL_HANDLE, pool, 0, 2);
                vkDestroyQueryPool(VK_NULL_HANDLE, pool, NULL);
                return 0;
            #endif
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_shader_non_semantic_info_extension_keeps_shader_bytes_unmodified(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            int main(void) {{
            #ifndef VK_KHR_SHADER_NON_SEMANTIC_INFO_EXTENSION_NAME
                return 0;
            #else
                if (!device_extension_advertised_name(VK_KHR_SHADER_NON_SEMANTIC_INFO_EXTENSION_NAME)) return 2;
                uint32_t extension_count = 0;
                if (vkEnumerateDeviceExtensionProperties((VkPhysicalDevice)physical_device_for_instance(NULL), NULL, &extension_count, NULL) != VK_SUCCESS ||
                    extension_count == 0) return 3;
                VkExtensionProperties extensions[64];
                memset(extensions, 0, sizeof(extensions));
                uint32_t capacity = 64;
                if (vkEnumerateDeviceExtensionProperties((VkPhysicalDevice)physical_device_for_instance(NULL), NULL, &capacity, extensions) != VK_SUCCESS) return 4;
                VkBool32 found = VK_FALSE;
                for (uint32_t i = 0; i < capacity; ++i) {{
                    if (strcmp(extensions[i].extensionName, VK_KHR_SHADER_NON_SEMANTIC_INFO_EXTENSION_NAME) == 0) found = VK_TRUE;
                }}
                if (!found) return 5;

                const char *enabled[] = {{ VK_KHR_SHADER_NON_SEMANTIC_INFO_EXTENSION_NAME }};
                VkDeviceCreateInfo create_info;
                memset(&create_info, 0, sizeof(create_info));
                create_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                create_info.enabledExtensionCount = 1;
                create_info.ppEnabledExtensionNames = enabled;
                if (validate_device_extensions(&create_info) != VK_SUCCESS) return 6;

                const uint32_t shader_words[] = {{
                    0x07230203u, 0x00010000u, 0u, 7u,
                    0x4e53454du, 0x414e5449u, 0x4300debu, 0x12345678u
                }};
                VkShaderModuleCreateInfo shader_info;
                memset(&shader_info, 0, sizeof(shader_info));
                shader_info.sType = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO;
                shader_info.codeSize = sizeof(shader_words);
                shader_info.pCode = shader_words;
                VkShaderModule shader = VK_NULL_HANDLE;
                if (vkCreateShaderModule(VK_NULL_HANDLE, &shader_info, NULL, &shader) != VK_SUCCESS ||
                    shader == VK_NULL_HANDLE) return 7;
                PdockerVkShaderModule *stored = pdocker_vk_shader_module_from_handle(shader);
                if (!stored || stored->code_size != sizeof(shader_words) || !stored->code_map) return 8;
                if (memcmp(stored->code_map, shader_words, sizeof(shader_words)) != 0) return 9;
                vkDestroyShaderModule(VK_NULL_HANDLE, shader, NULL);
                return 0;
            #endif
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_shader_float_controls_extension_exposes_conservative_properties(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            static int extension_seen(
                    const VkExtensionProperties *extensions,
                    uint32_t count,
                    const char *name) {{
                for (uint32_t i = 0; i < count; ++i) {{
                    if (strcmp(extensions[i].extensionName, name) == 0) return 1;
                }}
                return 0;
            }}

            int main(void) {{
            #ifdef VK_KHR_SHADER_FLOAT_CONTROLS_EXTENSION_NAME
                if (!device_extension_advertised_name(VK_KHR_SHADER_FLOAT_CONTROLS_EXTENSION_NAME)) return 2;
                uint32_t count = 64;
                VkExtensionProperties extensions[64];
                memset(extensions, 0, sizeof(extensions));
                if (vkEnumerateDeviceExtensionProperties((VkPhysicalDevice)physical_device_for_instance(NULL), NULL, &count, extensions) != VK_SUCCESS) return 3;
                if (!extension_seen(extensions, count, VK_KHR_SHADER_FLOAT_CONTROLS_EXTENSION_NAME)) return 4;

                const char *enabled[] = {{ VK_KHR_SHADER_FLOAT_CONTROLS_EXTENSION_NAME }};
                VkDeviceCreateInfo create_info;
                memset(&create_info, 0, sizeof(create_info));
                create_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                create_info.enabledExtensionCount = 1;
                create_info.ppEnabledExtensionNames = enabled;
                if (validate_device_extensions(&create_info) != VK_SUCCESS) return 5;

                VkPhysicalDeviceFloatControlsProperties props;
                memset(&props, 0xff, sizeof(props));
                props.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FLOAT_CONTROLS_PROPERTIES;
                props.pNext = NULL;
                fill_pnext_properties(&props);
                if (props.sType != VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FLOAT_CONTROLS_PROPERTIES) return 6;
                if (props.pNext != NULL) return 7;
                if (props.denormBehaviorIndependence != VK_SHADER_FLOAT_CONTROLS_INDEPENDENCE_NONE) return 8;
                if (props.roundingModeIndependence != VK_SHADER_FLOAT_CONTROLS_INDEPENDENCE_NONE) return 9;
                if (props.shaderSignedZeroInfNanPreserveFloat32 != VK_FALSE) return 10;
                if (props.shaderRoundingModeRTEFloat32 != VK_FALSE) return 11;

                VkPhysicalDeviceVulkan12Properties vulkan12_props;
                memset(&vulkan12_props, 0xff, sizeof(vulkan12_props));
                vulkan12_props.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_PROPERTIES;
                vulkan12_props.pNext = NULL;
                fill_pnext_properties(&vulkan12_props);
                if (vulkan12_props.denormBehaviorIndependence != VK_SHADER_FLOAT_CONTROLS_INDEPENDENCE_NONE) return 12;
                if (vulkan12_props.roundingModeIndependence != VK_SHADER_FLOAT_CONTROLS_INDEPENDENCE_NONE) return 13;
                if (vulkan12_props.shaderSignedZeroInfNanPreserveFloat32 != VK_FALSE) return 14;
                if (vulkan12_props.shaderRoundingModeRTEFloat32 != VK_FALSE) return 15;
            #endif
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stderr)


    def test_sampler_border_color_pnext_is_false_only_and_not_advertised_without_transport(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            static int extension_seen(
                    const VkExtensionProperties *extensions,
                    uint32_t count,
                    const char *name) {{
                for (uint32_t i = 0; i < count; ++i) {{
                    if (strcmp(extensions[i].extensionName, name) == 0) return 1;
                }}
                return 0;
            }}

            static VkSamplerCreateInfo base_sampler_info(void) {{
                VkSamplerCreateInfo info;
                memset(&info, 0, sizeof(info));
                info.sType = VK_STRUCTURE_TYPE_SAMPLER_CREATE_INFO;
                info.magFilter = VK_FILTER_NEAREST;
                info.minFilter = VK_FILTER_NEAREST;
                info.mipmapMode = VK_SAMPLER_MIPMAP_MODE_NEAREST;
                info.addressModeU = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
                info.addressModeV = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
                info.addressModeW = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
                info.borderColor = VK_BORDER_COLOR_FLOAT_OPAQUE_WHITE;
                info.minLod = 0.0f;
                info.maxLod = 0.0f;
                return info;
            }}

            int main(void) {{
            #if defined(VK_EXT_CUSTOM_BORDER_COLOR_EXTENSION_NAME) && defined(VK_EXT_BORDER_COLOR_SWIZZLE_EXTENSION_NAME)
                if (device_extension_advertised_name(VK_EXT_CUSTOM_BORDER_COLOR_EXTENSION_NAME)) return 2;
                if (device_extension_advertised_name(VK_EXT_BORDER_COLOR_SWIZZLE_EXTENSION_NAME)) return 3;
                uint32_t count = 64;
                VkExtensionProperties extensions[64];
                memset(extensions, 0, sizeof(extensions));
                if (vkEnumerateDeviceExtensionProperties((VkPhysicalDevice)physical_device_for_instance(NULL), NULL, &count, extensions) != VK_SUCCESS) return 4;
                if (extension_seen(extensions, count, VK_EXT_CUSTOM_BORDER_COLOR_EXTENSION_NAME)) return 5;
                if (extension_seen(extensions, count, VK_EXT_BORDER_COLOR_SWIZZLE_EXTENSION_NAME)) return 6;

                const char *enabled[] = {{
                    VK_EXT_CUSTOM_BORDER_COLOR_EXTENSION_NAME,
                    VK_EXT_BORDER_COLOR_SWIZZLE_EXTENSION_NAME,
                }};
                VkDeviceCreateInfo create_info;
                memset(&create_info, 0, sizeof(create_info));
                create_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                create_info.enabledExtensionCount = 2;
                create_info.ppEnabledExtensionNames = enabled;
                if (validate_device_extensions(&create_info) != VK_ERROR_EXTENSION_NOT_PRESENT) return 7;

                VkPhysicalDeviceCustomBorderColorFeaturesEXT custom_features;
                VkPhysicalDeviceBorderColorSwizzleFeaturesEXT swizzle_features;
                memset(&custom_features, 0, sizeof(custom_features));
                memset(&swizzle_features, 0, sizeof(swizzle_features));
                custom_features.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_CUSTOM_BORDER_COLOR_FEATURES_EXT;
                custom_features.pNext = &swizzle_features;
                swizzle_features.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_BORDER_COLOR_SWIZZLE_FEATURES_EXT;
                fill_pnext_features(&custom_features);
                if (custom_features.customBorderColors != VK_FALSE) return 8;
                if (custom_features.customBorderColorWithoutFormat != VK_FALSE) return 9;
                if (swizzle_features.borderColorSwizzle != VK_FALSE) return 10;
                if (swizzle_features.borderColorSwizzleFromImage != VK_FALSE) return 11;

                VkPhysicalDeviceCustomBorderColorPropertiesEXT custom_props;
                memset(&custom_props, 0xff, sizeof(custom_props));
                custom_props.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_CUSTOM_BORDER_COLOR_PROPERTIES_EXT;
                custom_props.pNext = NULL;
                fill_pnext_properties(&custom_props);
                if (custom_props.maxCustomBorderColorSamplers != 0) return 12;

                memset(&custom_features, 0, sizeof(custom_features));
                memset(&swizzle_features, 0, sizeof(swizzle_features));
                custom_features.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_CUSTOM_BORDER_COLOR_FEATURES_EXT;
                custom_features.pNext = &swizzle_features;
                swizzle_features.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_BORDER_COLOR_SWIZZLE_FEATURES_EXT;
                create_info.enabledExtensionCount = 0;
                create_info.ppEnabledExtensionNames = NULL;
                create_info.pNext = &custom_features;
                if (validate_device_feature_requests(&create_info) != VK_SUCCESS) return 13;
                custom_features.customBorderColors = VK_TRUE;
                if (validate_device_feature_requests(&create_info) == VK_SUCCESS) return 14;
                custom_features.customBorderColors = VK_FALSE;
                custom_features.customBorderColorWithoutFormat = VK_TRUE;
                if (validate_device_feature_requests(&create_info) == VK_SUCCESS) return 15;
                custom_features.customBorderColorWithoutFormat = VK_FALSE;
                swizzle_features.borderColorSwizzle = VK_TRUE;
                if (validate_device_feature_requests(&create_info) == VK_SUCCESS) return 16;
                swizzle_features.borderColorSwizzle = VK_FALSE;
                swizzle_features.borderColorSwizzleFromImage = VK_TRUE;
                if (validate_device_feature_requests(&create_info) == VK_SUCCESS) return 17;

                VkSamplerCustomBorderColorCreateInfoEXT custom_info;
                VkSamplerBorderColorComponentMappingCreateInfoEXT mapping_info;
                VkSamplerCreateInfo sampler_info = base_sampler_info();
                memset(&custom_info, 0, sizeof(custom_info));
                memset(&mapping_info, 0, sizeof(mapping_info));
                custom_info.sType = VK_STRUCTURE_TYPE_SAMPLER_CUSTOM_BORDER_COLOR_CREATE_INFO_EXT;
                custom_info.pNext = &mapping_info;
                mapping_info.sType = VK_STRUCTURE_TYPE_SAMPLER_BORDER_COLOR_COMPONENT_MAPPING_CREATE_INFO_EXT;
                mapping_info.components.r = VK_COMPONENT_SWIZZLE_IDENTITY;
                mapping_info.components.g = VK_COMPONENT_SWIZZLE_IDENTITY;
                mapping_info.components.b = VK_COMPONENT_SWIZZLE_IDENTITY;
                mapping_info.components.a = VK_COMPONENT_SWIZZLE_IDENTITY;
                mapping_info.srgb = VK_FALSE;
                sampler_info.pNext = &custom_info;
                if (validate_sampler_create_info_for_transport(&sampler_info, 0, NULL) != VK_SUCCESS) return 18;
                sampler_info.borderColor = VK_BORDER_COLOR_FLOAT_CUSTOM_EXT;
                if (validate_sampler_create_info_for_transport(&sampler_info, 0, NULL) == VK_SUCCESS) return 19;
                sampler_info.borderColor = VK_BORDER_COLOR_INT_CUSTOM_EXT;
                if (validate_sampler_create_info_for_transport(&sampler_info, 0, NULL) == VK_SUCCESS) return 20;
                sampler_info.borderColor = VK_BORDER_COLOR_FLOAT_OPAQUE_WHITE;
                mapping_info.components.r = VK_COMPONENT_SWIZZLE_G;
                if (validate_sampler_create_info_for_transport(&sampler_info, 0, NULL) == VK_SUCCESS) return 21;
                mapping_info.components.r = VK_COMPONENT_SWIZZLE_IDENTITY;
                mapping_info.srgb = VK_TRUE;
                if (validate_sampler_create_info_for_transport(&sampler_info, 0, NULL) == VK_SUCCESS) return 22;
            #endif
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stderr)


    def test_sampler_filter_minmax_extension_enables_reduction_feature_mask(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            int main(void) {{
            #ifdef VK_EXT_SAMPLER_FILTER_MINMAX_EXTENSION_NAME
                const char *enabled[] = {{ VK_EXT_SAMPLER_FILTER_MINMAX_EXTENSION_NAME }};
                VkDeviceCreateInfo create_info;
                memset(&create_info, 0, sizeof(create_info));
                create_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                create_info.enabledExtensionCount = 1;
                create_info.ppEnabledExtensionNames = enabled;
                uint64_t mask = requested_feature_mask_from_device_create_info(&create_info);
                if ((mask & PDOCKER_VK_FEATURE_SAMPLER_FILTER_MINMAX) == 0) return 2;

                VkSamplerCreateInfo sampler_info;
                VkSamplerReductionModeCreateInfo reduction_info;
                memset(&sampler_info, 0, sizeof(sampler_info));
                memset(&reduction_info, 0, sizeof(reduction_info));
                sampler_info.sType = VK_STRUCTURE_TYPE_SAMPLER_CREATE_INFO;
                sampler_info.pNext = &reduction_info;
                sampler_info.magFilter = VK_FILTER_NEAREST;
                sampler_info.minFilter = VK_FILTER_NEAREST;
                sampler_info.mipmapMode = VK_SAMPLER_MIPMAP_MODE_NEAREST;
                sampler_info.addressModeU = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
                sampler_info.addressModeV = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
                sampler_info.addressModeW = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
                sampler_info.minLod = 0.0f;
                sampler_info.maxLod = 0.0f;
                reduction_info.sType = VK_STRUCTURE_TYPE_SAMPLER_REDUCTION_MODE_CREATE_INFO;
                reduction_info.reductionMode = VK_SAMPLER_REDUCTION_MODE_MIN;
                VkSamplerReductionMode reduction_mode = VK_SAMPLER_REDUCTION_MODE_WEIGHTED_AVERAGE;
                if (validate_sampler_create_info_for_transport(&sampler_info, mask, &reduction_mode) != VK_SUCCESS) return 3;
                if (reduction_mode != VK_SAMPLER_REDUCTION_MODE_MIN) return 4;
            #ifdef VK_KHR_SAMPLER_MIRROR_CLAMP_TO_EDGE_EXTENSION_NAME
                if (device_extension_advertised_name(VK_KHR_SAMPLER_MIRROR_CLAMP_TO_EDGE_EXTENSION_NAME)) return 5;
            #endif
                sampler_info.pNext = NULL;
                sampler_info.addressModeU = VK_SAMPLER_ADDRESS_MODE_MIRROR_CLAMP_TO_EDGE;
                if (validate_sampler_create_info_for_transport(&sampler_info, mask, NULL) == VK_SUCCESS) return 6;
            #endif
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stderr)


    def test_subpass_merge_feedback_feature_is_queryable_and_enableable(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            int main(void) {{
                VkPhysicalDeviceSubpassMergeFeedbackFeaturesEXT feedback;
                memset(&feedback, 0xff, sizeof(feedback));
                feedback.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SUBPASS_MERGE_FEEDBACK_FEATURES_EXT;
                feedback.pNext = NULL;
                fill_pnext_features(&feedback);
                if (feedback.sType != VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SUBPASS_MERGE_FEEDBACK_FEATURES_EXT) {{
                    return 2;
                }}
                if (feedback.pNext != NULL) {{
                    return 3;
                }}
                if (feedback.subpassMergeFeedback != VK_TRUE) {{
                    return 4;
                }}

                VkDeviceCreateInfo create_info;
                memset(&create_info, 0, sizeof(create_info));
                create_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                feedback.subpassMergeFeedback = VK_TRUE;
                create_info.pNext = &feedback;
                if (validate_device_feature_requests(&create_info) != VK_SUCCESS) {{
                    return 5;
                }}
                feedback.subpassMergeFeedback = VK_FALSE;
                if (validate_device_feature_requests(&create_info) != VK_SUCCESS) {{
                    return 6;
                }}
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_debug_marker_extension_is_not_advertised_without_transport(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            int main(void) {{
            #ifdef VK_EXT_DEBUG_MARKER_EXTENSION_NAME
                uint32_t extension_count = 0;
                if (vkEnumerateDeviceExtensionProperties((VkPhysicalDevice)physical_device_for_instance(NULL), NULL, &extension_count, NULL) != VK_SUCCESS) return 2;
                VkExtensionProperties extensions[64];
                memset(extensions, 0, sizeof(extensions));
                uint32_t capacity = 64;
                if (vkEnumerateDeviceExtensionProperties((VkPhysicalDevice)physical_device_for_instance(NULL), NULL, &capacity, extensions) != VK_SUCCESS) return 3;
                VkBool32 found = VK_FALSE;
                for (uint32_t i = 0; i < capacity; ++i) {{
                    if (strcmp(extensions[i].extensionName, VK_EXT_DEBUG_MARKER_EXTENSION_NAME) == 0) found = VK_TRUE;
                }}
                if (found || device_extension_advertised_name(VK_EXT_DEBUG_MARKER_EXTENSION_NAME)) return 4;

                const char *enabled[] = {{ VK_EXT_DEBUG_MARKER_EXTENSION_NAME }};
                VkDeviceCreateInfo device_info;
                memset(&device_info, 0, sizeof(device_info));
                device_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                device_info.enabledExtensionCount = 1;
                device_info.ppEnabledExtensionNames = enabled;
                if (validate_device_extensions(&device_info) != VK_ERROR_EXTENSION_NOT_PRESENT) return 5;

                if (proc_address("vkDebugMarkerSetObjectNameEXT") != NULL) return 6;
                if (proc_address("vkDebugMarkerSetObjectTagEXT") != NULL) return 7;
                if (proc_address("vkCmdDebugMarkerBeginEXT") != NULL) return 8;
                if (proc_address("vkCmdDebugMarkerEndEXT") != NULL) return 9;
                if (proc_address("vkCmdDebugMarkerInsertEXT") != NULL) return 10;

                VkDebugMarkerObjectNameInfoEXT name_info;
                memset(&name_info, 0, sizeof(name_info));
                name_info.sType = VK_STRUCTURE_TYPE_DEBUG_MARKER_OBJECT_NAME_INFO_EXT;
                name_info.objectType = VK_DEBUG_REPORT_OBJECT_TYPE_BUFFER_EXT;
                name_info.object = 0x1234u;
                name_info.pObjectName = "legacy-buffer-name";
                if (vkDebugMarkerSetObjectNameEXT(VK_NULL_HANDLE, &name_info) != VK_ERROR_INITIALIZATION_FAILED) return 11;

                const uint32_t tag = 0x13572468u;
                VkDebugMarkerObjectTagInfoEXT tag_info;
                memset(&tag_info, 0, sizeof(tag_info));
                tag_info.sType = VK_STRUCTURE_TYPE_DEBUG_MARKER_OBJECT_TAG_INFO_EXT;
                tag_info.objectType = VK_DEBUG_REPORT_OBJECT_TYPE_BUFFER_EXT;
                tag_info.object = 0x1234u;
                tag_info.tagName = 7u;
                tag_info.tagSize = sizeof(tag);
                tag_info.pTag = &tag;
                if (vkDebugMarkerSetObjectTagEXT(VK_NULL_HANDLE, &tag_info) != VK_ERROR_INITIALIZATION_FAILED) return 12;

                VkDebugMarkerMarkerInfoEXT marker_info;
                memset(&marker_info, 0, sizeof(marker_info));
                marker_info.sType = VK_STRUCTURE_TYPE_DEBUG_MARKER_MARKER_INFO_EXT;
                marker_info.pMarkerName = "legacy-marker";
                vkCmdDebugMarkerBeginEXT(VK_NULL_HANDLE, &marker_info);
                vkCmdDebugMarkerInsertEXT(VK_NULL_HANDLE, &marker_info);
                vkCmdDebugMarkerEndEXT(VK_NULL_HANDLE);

                memset(&name_info, 0, sizeof(name_info));
                name_info.sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO;
                if (vkDebugMarkerSetObjectNameEXT(VK_NULL_HANDLE, &name_info) != VK_ERROR_INITIALIZATION_FAILED) return 13;
                memset(&tag_info, 0, sizeof(tag_info));
                tag_info.sType = VK_STRUCTURE_TYPE_DEBUG_MARKER_OBJECT_TAG_INFO_EXT;
                tag_info.objectType = VK_DEBUG_REPORT_OBJECT_TYPE_BUFFER_EXT;
                tag_info.object = 0x1234u;
                tag_info.tagSize = 4u;
                tag_info.pTag = NULL;
                if (vkDebugMarkerSetObjectTagEXT(VK_NULL_HANDLE, &tag_info) != VK_ERROR_INITIALIZATION_FAILED) return 14;
            #endif
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_instance_create_info_shape_is_fail_closed(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            typedef struct TestPnext {{
                VkStructureType sType;
                const void *pNext;
            }} TestPnext;

            static VkBool32 VKAPI_CALL test_debug_callback(
                    VkDebugUtilsMessageSeverityFlagBitsEXT severity,
                    VkDebugUtilsMessageTypeFlagsEXT types,
                    const VkDebugUtilsMessengerCallbackDataEXT *data,
                    void *user_data) {{
                (void)severity;
                (void)types;
                (void)data;
                (void)user_data;
                return VK_FALSE;
            }}

            int main(void) {{
                VkInstance instance = (VkInstance)(uintptr_t)0xfeedu;
                if (vkCreateInstance(NULL, NULL, &instance) != VK_ERROR_INITIALIZATION_FAILED) return 2;
                if (instance != VK_NULL_HANDLE) return 3;

                VkInstanceCreateInfo info;
                memset(&info, 0, sizeof(info));
                info.sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO;
                instance = (VkInstance)(uintptr_t)0xfeedu;
                if (vkCreateInstance(&info, NULL, &instance) != VK_ERROR_INITIALIZATION_FAILED) return 4;
                if (instance != VK_NULL_HANDLE) return 5;

                memset(&info, 0, sizeof(info));
                info.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO;
                info.flags = 1;
                if (vkCreateInstance(&info, NULL, &instance) != VK_ERROR_INITIALIZATION_FAILED) return 6;

                const char *layers[] = {{ "VK_LAYER_SKYDNIR_missing" }};
                memset(&info, 0, sizeof(info));
                info.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO;
                info.enabledLayerCount = 1;
                info.ppEnabledLayerNames = layers;
                if (vkCreateInstance(&info, NULL, &instance) != VK_ERROR_LAYER_NOT_PRESENT) return 7;

                VkApplicationInfo app;
                memset(&app, 0, sizeof(app));
                app.sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO;
                memset(&info, 0, sizeof(info));
                info.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO;
                info.pApplicationInfo = &app;
                if (vkCreateInstance(&info, NULL, &instance) != VK_ERROR_INITIALIZATION_FAILED) return 8;

                TestPnext bad_pnext = {{ VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO, NULL }};
                memset(&app, 0, sizeof(app));
                app.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO;
                app.pNext = &bad_pnext;
                info.pApplicationInfo = &app;
                if (vkCreateInstance(&info, NULL, &instance) != VK_ERROR_FEATURE_NOT_PRESENT) return 9;

                memset(&info, 0, sizeof(info));
                info.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO;
                info.pNext = &bad_pnext;
                if (vkCreateInstance(&info, NULL, &instance) != VK_ERROR_FEATURE_NOT_PRESENT) return 10;

                VkDebugUtilsMessengerCreateInfoEXT debug_info;
                memset(&debug_info, 0, sizeof(debug_info));
                debug_info.sType = VK_STRUCTURE_TYPE_DEBUG_UTILS_MESSENGER_CREATE_INFO_EXT;
                debug_info.messageSeverity = VK_DEBUG_UTILS_MESSAGE_SEVERITY_WARNING_BIT_EXT;
                debug_info.messageType = VK_DEBUG_UTILS_MESSAGE_TYPE_VALIDATION_BIT_EXT;
                debug_info.pfnUserCallback = test_debug_callback;
                info.pNext = &debug_info;
                if (vkCreateInstance(&info, NULL, &instance) != VK_ERROR_EXTENSION_NOT_PRESENT) return 11;

                const char *debug_ext[] = {{ VK_EXT_DEBUG_UTILS_EXTENSION_NAME }};
                info.enabledExtensionCount = 1;
                info.ppEnabledExtensionNames = debug_ext;
                debug_info.pfnUserCallback = NULL;
                if (vkCreateInstance(&info, NULL, &instance) != VK_ERROR_INITIALIZATION_FAILED) return 12;

                debug_info.pfnUserCallback = test_debug_callback;
                if (vkCreateInstance(&info, NULL, &instance) != VK_SUCCESS || instance == VK_NULL_HANDLE) return 13;
                vkDestroyInstance(instance, NULL);

                TestPnext loader_pnext = {{ VK_STRUCTURE_TYPE_LOADER_INSTANCE_CREATE_INFO, &debug_info }};
                info.pNext = &loader_pnext;
                instance = VK_NULL_HANDLE;
                if (vkCreateInstance(&info, NULL, &instance) != VK_SUCCESS || instance == VK_NULL_HANDLE) return 14;
                vkDestroyInstance(instance, NULL);
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_device_proc_and_command_recording_require_enabled_features(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"
            {COMMAND_BUFFER_STACK_TEST_HELPER}

            static int reason_is(const PdockerVkCommandBuffer *cmd, const char *reason) {{
                return cmd && cmd->recording_failed && cmd->recording_failure_reason &&
                       strcmp(cmd->recording_failure_reason, reason) == 0;
            }}

            int main(void) {{
                PdockerVkDevice device;
                memset(&device, 0, sizeof(device));
                if (!device_proc_address_hidden_by_enabled_state(&device, "vkCmdBeginRenderingKHR")) return 2;
                device.requested_feature_mask = PDOCKER_VK_FEATURE_DYNAMIC_RENDERING;
                if (!device_proc_address_hidden_by_enabled_state(&device, "vkCmdBeginRenderingKHR")) return 3;
                device.enabled_extension_mask = PDOCKER_VK_DEVICE_EXT_KHR_DYNAMIC_RENDERING;
                if (device_proc_address_hidden_by_enabled_state(&device, "vkCmdBeginRenderingKHR")) return 4;

                memset(&device, 0, sizeof(device));
                device.requested_feature_mask = PDOCKER_VK_FEATURE_SYNCHRONIZATION_2;
                if (!device_proc_address_hidden_by_enabled_state(&device, "vkQueueSubmit2KHR")) return 5;
                if (!device_proc_address_hidden_by_enabled_state(&device, "vkQueueSubmit2")) return 101;
                device.enabled_extension_mask = PDOCKER_VK_DEVICE_EXT_KHR_SYNCHRONIZATION_2;
                if (device_proc_address_hidden_by_enabled_state(&device, "vkQueueSubmit2KHR")) return 6;
                if (device_proc_address_hidden_by_enabled_state(&device, "vkQueueSubmit2")) return 102;

                memset(&device, 0, sizeof(device));
                device.requested_feature_mask = PDOCKER_VK_FEATURE_DRAW_INDIRECT_COUNT;
                if (device_proc_address_hidden_by_enabled_state(&device, "vkCmdDrawIndirectCount")) return 7;
                if (!device_proc_address_hidden_by_enabled_state(&device, "vkCmdDrawIndirectCountKHR")) return 8;
                device.enabled_extension_mask = PDOCKER_VK_DEVICE_EXT_KHR_DRAW_INDIRECT_COUNT;
                if (device_proc_address_hidden_by_enabled_state(&device, "vkCmdDrawIndirectCountKHR")) return 9;

                if (validate_requested_feature_extension_enables(
                        PDOCKER_VK_FEATURE_DYNAMIC_RENDERING, 0) != VK_ERROR_FEATURE_NOT_PRESENT) return 10;
                if (validate_requested_feature_extension_enables(
                        PDOCKER_VK_FEATURE_DYNAMIC_RENDERING,
                        PDOCKER_VK_DEVICE_EXT_KHR_DYNAMIC_RENDERING) != VK_SUCCESS) return 11;
                if (validate_requested_feature_extension_enables(
                        PDOCKER_VK_FEATURE_INDEX_TYPE_UINT8, 0) != VK_ERROR_FEATURE_NOT_PRESENT) return 12;
                if (validate_requested_feature_extension_enables(
                        PDOCKER_VK_FEATURE_INDEX_TYPE_UINT8,
                        PDOCKER_VK_DEVICE_EXT_INDEX_TYPE_UINT8) != VK_SUCCESS) return 13;
                if (validate_requested_feature_extension_enables(
                        PDOCKER_VK_FEATURE_EXTENDED_DYNAMIC_STATE_2_LOGIC_OP, 0) != VK_ERROR_FEATURE_NOT_PRESENT) return 116;
                if (validate_requested_feature_extension_enables(
                        PDOCKER_VK_FEATURE_EXTENDED_DYNAMIC_STATE_2_LOGIC_OP,
                        PDOCKER_VK_DEVICE_EXT_EXTENDED_DYNAMIC_STATE_2) != VK_SUCCESS) return 117;
                if (validate_requested_feature_extension_enables(
                        PDOCKER_VK_FEATURE_EXTENDED_DYNAMIC_STATE_2_PATCH_CONTROL_POINTS, 0) != VK_ERROR_FEATURE_NOT_PRESENT) return 118;

                memset(&device, 0, sizeof(device));
                device.requested_feature_mask = PDOCKER_VK_FEATURE_EXTENDED_DYNAMIC_STATE_2;
                device.enabled_extension_mask = PDOCKER_VK_DEVICE_EXT_EXTENDED_DYNAMIC_STATE_2;
                if (!device_proc_address_hidden_by_enabled_state(&device, "vkCmdSetLogicOpEXT")) return 119;
                device.requested_feature_mask = PDOCKER_VK_FEATURE_EXTENDED_DYNAMIC_STATE_2_LOGIC_OP;
                if (device_proc_address_hidden_by_enabled_state(&device, "vkCmdSetLogicOpEXT")) return 120;
                memset(&device, 0, sizeof(device));
                device.requested_feature_mask = PDOCKER_VK_FEATURE_EXTENDED_DYNAMIC_STATE_2_PATCH_CONTROL_POINTS;
                device.enabled_extension_mask = PDOCKER_VK_DEVICE_EXT_EXTENDED_DYNAMIC_STATE_2;
                if (device_proc_address_hidden_by_enabled_state(&device, "vkCmdSetPatchControlPointsEXT")) return 121;
                device.enabled_extension_mask = 0;
                if (!device_proc_address_hidden_by_enabled_state(&device, "vkCmdSetPatchControlPointsEXT")) return 122;

                VkDeviceCreateInfo queue_device_info;
                memset(&queue_device_info, 0, sizeof(queue_device_info));
                queue_device_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                VkDevice queue_device = VK_NULL_HANDLE;
                if (vkCreateDevice((VkPhysicalDevice)&g_device, &queue_device_info, NULL, &queue_device) != VK_SUCCESS ||
                    queue_device == VK_NULL_HANDLE) return 132;
                VkQueue queue = VK_NULL_HANDLE;
                vkGetDeviceQueue(queue_device, 0, 0, &queue);
                PdockerVkQueue *queue_obj = pdocker_vk_queue_from_handle(queue);
                if (!queue_obj) {{
                    vkDestroyDevice(queue_device, NULL);
                    return 133;
                }}
                PdockerVkFence fence;
                memset(&fence, 0, sizeof(fence));
                fence.signaled = true;
                if (vkQueueSubmit2(queue, 0, NULL, (VkFence)&fence) != VK_ERROR_FEATURE_NOT_PRESENT) {{
                    vkDestroyDevice(queue_device, NULL);
                    return 103;
                }}
                if (!fence.signaled) {{
                    vkDestroyDevice(queue_device, NULL);
                    return 104;
                }}
                queue_obj->requested_feature_mask = PDOCKER_VK_FEATURE_SYNCHRONIZATION_2;
                if (vkQueueSubmit2(queue, 0, NULL, VK_NULL_HANDLE) != VK_ERROR_FEATURE_NOT_PRESENT) {{
                    vkDestroyDevice(queue_device, NULL);
                    return 105;
                }}
                queue_obj->enabled_extension_mask = PDOCKER_VK_DEVICE_EXT_KHR_SYNCHRONIZATION_2;
                if (vkQueueSubmit2(queue, 0, NULL, VK_NULL_HANDLE) != VK_SUCCESS) {{
                    vkDestroyDevice(queue_device, NULL);
                    return 106;
                }}
                vkDestroyDevice(queue_device, NULL);

                PdockerVkMemory memory;
                PdockerVkBuffer buffer;
                PdockerVkCommandBuffer cmd;
                PdockerVkEvent event;
                memset(&memory, 0, sizeof(memory));
                memset(&buffer, 0, sizeof(buffer));
                memset(&event, 0, sizeof(event));
                event.event_id = 1;
                memory.size = 1024;
                buffer.size = 1024;
                buffer.memory = &memory;
                buffer.memory_offset = 0;
                buffer_register(&buffer);

                reset_test_command_buffer(&cmd, 0, 0);
                vkCmdPipelineBarrier2((VkCommandBuffer)&cmd, NULL);
                if (!reason_is(&cmd, "synchronization2-feature-disabled")) return 107;
                if (cmd.command_op_count || cmd.graphics_command_op_count) return 108;

                reset_test_command_buffer(&cmd, 0, 0);
                cmd.requested_feature_mask = PDOCKER_VK_FEATURE_SYNCHRONIZATION_2;
                vkCmdPipelineBarrier2((VkCommandBuffer)&cmd, NULL);
                if (!reason_is(&cmd, "synchronization2-feature-disabled")) return 109;

                VkDependencyInfo dependency_info;
                memset(&dependency_info, 0, sizeof(dependency_info));
                dependency_info.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO;
                reset_test_command_buffer(&cmd, 0, 0);
                cmd.requested_feature_mask = PDOCKER_VK_FEATURE_SYNCHRONIZATION_2;
                cmd.enabled_extension_mask = PDOCKER_VK_DEVICE_EXT_KHR_SYNCHRONIZATION_2;
                vkCmdPipelineBarrier2((VkCommandBuffer)&cmd, &dependency_info);
                if (cmd.recording_failed) return 110;
                if (cmd.command_op_count == 0) return 111;
                command_buffer_destroy_record_vectors(&cmd);

                reset_test_command_buffer(&cmd, 0, 0);
                vkCmdSetEvent2((VkCommandBuffer)&cmd, pdocker_vk_event_to_handle(&event), NULL);
                if (!reason_is(&cmd, "synchronization2-feature-disabled")) return 112;
                reset_test_command_buffer(&cmd, 0, 0);
                vkCmdResetEvent2((VkCommandBuffer)&cmd, pdocker_vk_event_to_handle(&event), 0);
                if (!reason_is(&cmd, "synchronization2-feature-disabled")) return 113;
                reset_test_command_buffer(&cmd, 0, 0);
                vkCmdWaitEvents2((VkCommandBuffer)&cmd, 0, NULL, NULL);
                if (!reason_is(&cmd, "synchronization2-feature-disabled")) return 114;
                reset_test_command_buffer(&cmd, 0, 0);
                vkCmdWriteTimestamp2((VkCommandBuffer)&cmd, 0, VK_NULL_HANDLE, 0);
                if (!reason_is(&cmd, "synchronization2-feature-disabled")) return 115;

                reset_test_command_buffer(&cmd, 0, 0);
                vkCmdSetCullMode((VkCommandBuffer)&cmd, VK_CULL_MODE_NONE);
                if (!reason_is(&cmd, "dynamic-state-feature-not-enabled")) return 123;
                reset_test_command_buffer(&cmd, 0, 0);
                cmd.requested_feature_mask = PDOCKER_VK_FEATURE_EXTENDED_DYNAMIC_STATE;
                vkCmdSetCullMode((VkCommandBuffer)&cmd, VK_CULL_MODE_NONE);
                if (cmd.recording_failed || cmd.dynamic_state_count == 0) return 124;
                command_buffer_destroy_record_vectors(&cmd);

                reset_test_command_buffer(&cmd, 0, 0);
                vkCmdSetRasterizerDiscardEnable((VkCommandBuffer)&cmd, VK_FALSE);
                if (!reason_is(&cmd, "dynamic-state-feature-not-enabled")) return 125;
                reset_test_command_buffer(&cmd, 0, 0);
                cmd.requested_feature_mask = PDOCKER_VK_FEATURE_EXTENDED_DYNAMIC_STATE_2;
                vkCmdSetRasterizerDiscardEnable((VkCommandBuffer)&cmd, VK_FALSE);
                if (cmd.recording_failed || cmd.dynamic_state_count == 0) return 126;
                command_buffer_destroy_record_vectors(&cmd);

                reset_test_command_buffer(&cmd, 0, 0);
                cmd.requested_feature_mask = PDOCKER_VK_FEATURE_EXTENDED_DYNAMIC_STATE_2 | PDOCKER_VK_FEATURE_LOGIC_OP;
                vkCmdSetLogicOpEXT((VkCommandBuffer)&cmd, VK_LOGIC_OP_COPY);
                if (!reason_is(&cmd, "dynamic-state-feature-not-enabled")) return 127;
                reset_test_command_buffer(&cmd, 0, 0);
                cmd.requested_feature_mask = PDOCKER_VK_FEATURE_EXTENDED_DYNAMIC_STATE_2_LOGIC_OP;
                vkCmdSetLogicOpEXT((VkCommandBuffer)&cmd, VK_LOGIC_OP_COPY);
                if (!reason_is(&cmd, "dynamic-logic-op-feature-not-enabled")) return 128;
                reset_test_command_buffer(&cmd, 0, 0);
                cmd.requested_feature_mask = PDOCKER_VK_FEATURE_EXTENDED_DYNAMIC_STATE_2_LOGIC_OP | PDOCKER_VK_FEATURE_LOGIC_OP;
                vkCmdSetLogicOpEXT((VkCommandBuffer)&cmd, VK_LOGIC_OP_COPY);
                if (cmd.recording_failed || cmd.dynamic_state_count == 0) return 129;
                command_buffer_destroy_record_vectors(&cmd);

                reset_test_command_buffer(&cmd, 0, 0);
                vkCmdBindVertexBuffers2((VkCommandBuffer)&cmd, 0, 1, (VkBuffer[]){{pdocker_vk_buffer_to_handle(&buffer)}}, (VkDeviceSize[]){{0}}, NULL, NULL);
                if (!reason_is(&cmd, "graphics-vertex-binding2-feature-disabled")) return 130;
                reset_test_command_buffer(&cmd, 0, 0);
                cmd.requested_feature_mask = PDOCKER_VK_FEATURE_EXTENDED_DYNAMIC_STATE;
                vkCmdBindVertexBuffers2((VkCommandBuffer)&cmd, 0, 1, (VkBuffer[]){{pdocker_vk_buffer_to_handle(&buffer)}}, (VkDeviceSize[]){{0}}, NULL, NULL);
                if (cmd.recording_failed || cmd.graphics_vertex_binding_snapshot_count == 0) return 131;
                command_buffer_destroy_record_vectors(&cmd);

            #ifdef VK_EXT_INDEX_TYPE_UINT8_EXTENSION_NAME
                reset_test_command_buffer(&cmd, 0, 0);
                record_index_buffer_binding((VkCommandBuffer)&cmd,
                                            pdocker_vk_buffer_to_handle(&buffer),
                                            0,
                                            VK_WHOLE_SIZE,
                                            VK_INDEX_TYPE_UINT8_EXT,
                                            false);
                if (!reason_is(&cmd, "graphics-index-type-uint8-feature-disabled")) return 14;

                reset_test_command_buffer(&cmd, 0, 0);
                cmd.requested_feature_mask = PDOCKER_VK_FEATURE_INDEX_TYPE_UINT8;
                record_index_buffer_binding((VkCommandBuffer)&cmd,
                                            pdocker_vk_buffer_to_handle(&buffer),
                                            0,
                                            VK_WHOLE_SIZE,
                                            VK_INDEX_TYPE_UINT8_EXT,
                                            false);
                if (cmd.recording_failed) return 15;
                command_buffer_destroy_record_vectors(&cmd);
            #endif

                reset_test_command_buffer(&cmd, 0, 0);
                vkCmdBeginRendering((VkCommandBuffer)&cmd, NULL);
                if (!reason_is(&cmd, "dynamic-rendering-feature-disabled")) return 16;

                reset_test_command_buffer(&cmd, 0, 0);
                record_graphics_draw_command((VkCommandBuffer)&cmd,
                                             1, 1, 0, 0, 0, 0, 0,
                                             false,
                                             true,
                                             VK_NULL_HANDLE,
                                             0,
                                             (VkBuffer)(uintptr_t)0x1234u,
                                             0,
                                             sizeof(VkDrawIndirectCommand));
                if (!reason_is(&cmd, "graphics-draw-indirect-count-feature-disabled")) return 17;
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_debug_utils_extension_is_icd_local_metadata(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            static uint32_t g_callback_count;
            static VkBool32 VKAPI_CALL debug_callback(
                    VkDebugUtilsMessageSeverityFlagBitsEXT severity,
                    VkDebugUtilsMessageTypeFlagsEXT types,
                    const VkDebugUtilsMessengerCallbackDataEXT *data,
                    void *user_data) {{
                if (severity == VK_DEBUG_UTILS_MESSAGE_SEVERITY_WARNING_BIT_EXT &&
                    (types & VK_DEBUG_UTILS_MESSAGE_TYPE_VALIDATION_BIT_EXT) &&
                    data && data->pMessage && user_data == (void *)(uintptr_t)0x1234u) {{
                    g_callback_count++;
                }}
                return VK_FALSE;
            }}

            int main(void) {{
                uint32_t extension_count = 0;
                if (vkEnumerateInstanceExtensionProperties(NULL, &extension_count, NULL) != VK_SUCCESS) return 2;
                if (extension_count == 0 || extension_count > PDOCKER_VK_MAX_INSTANCE_EXTENSIONS) return 3;
                if (extension_count > 1) {{
                    VkExtensionProperties one_extension[1];
                    uint32_t one_capacity = 1;
                    if (vkEnumerateInstanceExtensionProperties(NULL, &one_capacity, one_extension) != VK_INCOMPLETE ||
                        one_capacity != 1) return 11;
                }}
                VkExtensionProperties extensions[PDOCKER_VK_MAX_INSTANCE_EXTENSIONS];
                memset(extensions, 0, sizeof(extensions));
                uint32_t capacity = PDOCKER_VK_MAX_INSTANCE_EXTENSIONS;
                if (vkEnumerateInstanceExtensionProperties(NULL, &capacity, extensions) != VK_SUCCESS) return 4;
                if (capacity != extension_count) return 5;
                const char *enabled[PDOCKER_VK_MAX_INSTANCE_EXTENSIONS];
                VkBool32 found = VK_FALSE;
                for (uint32_t i = 0; i < capacity; ++i) {{
                    if (!instance_extension_advertised_name(extensions[i].extensionName)) return 6;
                    enabled[i] = extensions[i].extensionName;
                    if (strcmp(extensions[i].extensionName, VK_EXT_DEBUG_UTILS_EXTENSION_NAME) == 0) found = VK_TRUE;
                }}
                if (!found || !instance_extension_advertised_name(VK_EXT_DEBUG_UTILS_EXTENSION_NAME)) return 7;

                VkInstanceCreateInfo instance_info;
                memset(&instance_info, 0, sizeof(instance_info));
                instance_info.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO;
                instance_info.enabledExtensionCount = capacity;
                instance_info.ppEnabledExtensionNames = enabled;
                if (validate_instance_extensions(&instance_info) != VK_SUCCESS) return 8;
                const char *bad_enabled[] = {{ "VK_SKYDNIR_not_advertised_instance_extension" }};
                instance_info.enabledExtensionCount = 1;
                instance_info.ppEnabledExtensionNames = bad_enabled;
                if (validate_instance_extensions(&instance_info) != VK_ERROR_EXTENSION_NOT_PRESENT) return 9;
                const char *debug_enabled[] = {{ VK_EXT_DEBUG_UTILS_EXTENSION_NAME }};
                instance_info.enabledExtensionCount = 1;
                instance_info.ppEnabledExtensionNames = debug_enabled;
                VkInstance instance = VK_NULL_HANDLE;
                if (vkCreateInstance(&instance_info, NULL, &instance) != VK_SUCCESS || instance == VK_NULL_HANDLE) return 10;
                VkInstance instance_b = VK_NULL_HANDLE;
                if (vkCreateInstance(&instance_info, NULL, &instance_b) != VK_SUCCESS || instance_b == VK_NULL_HANDLE || instance_b == instance) return 31;

                uint32_t physical_count = 1;
                VkPhysicalDevice physical = VK_NULL_HANDLE;
                if (vkEnumeratePhysicalDevices(instance, &physical_count, &physical) != VK_SUCCESS || physical_count != 1 || physical == VK_NULL_HANDLE) return 39;
                VkDeviceCreateInfo real_device_info;
                memset(&real_device_info, 0, sizeof(real_device_info));
                real_device_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                VkDevice device = VK_NULL_HANDLE;
                if (vkCreateDevice(physical, &real_device_info, NULL, &device) != VK_SUCCESS || device == VK_NULL_HANDLE) return 40;
                VkBufferCreateInfo buffer_info;
                memset(&buffer_info, 0, sizeof(buffer_info));
                buffer_info.sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO;
                buffer_info.size = 256;
                buffer_info.usage = VK_BUFFER_USAGE_STORAGE_BUFFER_BIT;
                buffer_info.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
                VkBuffer debug_buffer = VK_NULL_HANDLE;
                if (vkCreateBuffer(device, &buffer_info, NULL, &debug_buffer) != VK_SUCCESS || debug_buffer == VK_NULL_HANDLE) return 41;

                VkDebugUtilsMessengerCreateInfoEXT messenger_info;
                memset(&messenger_info, 0, sizeof(messenger_info));
                messenger_info.sType = VK_STRUCTURE_TYPE_DEBUG_UTILS_MESSENGER_CREATE_INFO_EXT;
                messenger_info.messageSeverity = VK_DEBUG_UTILS_MESSAGE_SEVERITY_WARNING_BIT_EXT;
                messenger_info.messageType = VK_DEBUG_UTILS_MESSAGE_TYPE_VALIDATION_BIT_EXT;
                messenger_info.pfnUserCallback = debug_callback;
                messenger_info.pUserData = (void *)(uintptr_t)0x1234u;
                VkDebugUtilsMessengerEXT messenger = (VkDebugUtilsMessengerEXT)(uintptr_t)0xfeedu;
                if (vkCreateDebugUtilsMessengerEXT(instance, &messenger_info, NULL, &messenger) != VK_SUCCESS || messenger == VK_NULL_HANDLE) return 6;

                VkDebugUtilsMessengerCallbackDataEXT callback_data;
                memset(&callback_data, 0, sizeof(callback_data));
                callback_data.sType = VK_STRUCTURE_TYPE_DEBUG_UTILS_MESSENGER_CALLBACK_DATA_EXT;
                callback_data.pMessage = "validation message";
                vkSubmitDebugUtilsMessageEXT(instance,
                                             VK_DEBUG_UTILS_MESSAGE_SEVERITY_WARNING_BIT_EXT,
                                             VK_DEBUG_UTILS_MESSAGE_TYPE_VALIDATION_BIT_EXT,
                                             &callback_data);
                if (g_callback_count != 1) return 7;
                vkSubmitDebugUtilsMessageEXT(instance,
                                             VK_DEBUG_UTILS_MESSAGE_SEVERITY_INFO_BIT_EXT,
                                             VK_DEBUG_UTILS_MESSAGE_TYPE_GENERAL_BIT_EXT,
                                             &callback_data);
                if (g_callback_count != 1) return 8;
                vkSubmitDebugUtilsMessageEXT(instance_b,
                                             VK_DEBUG_UTILS_MESSAGE_SEVERITY_WARNING_BIT_EXT,
                                             VK_DEBUG_UTILS_MESSAGE_TYPE_VALIDATION_BIT_EXT,
                                             &callback_data);
                if (g_callback_count != 1) return 32;
                if (!debug_utils_messenger_handle_lookup_for_instance(instance, messenger)) return 33;
                if (debug_utils_messenger_handle_lookup_for_instance(instance_b, messenger)) return 34;
                vkDestroyDebugUtilsMessengerEXT(instance_b, messenger, NULL);
                if (!debug_utils_messenger_handle_lookup_for_instance(instance, messenger)) return 35;
                vkSubmitDebugUtilsMessageEXT(instance,
                                             VK_DEBUG_UTILS_MESSAGE_SEVERITY_WARNING_BIT_EXT,
                                             VK_DEBUG_UTILS_MESSAGE_TYPE_VALIDATION_BIT_EXT,
                                             &callback_data);
                if (g_callback_count != 2) return 36;

                VkDebugUtilsObjectNameInfoEXT name_info;
                memset(&name_info, 0, sizeof(name_info));
                name_info.sType = VK_STRUCTURE_TYPE_DEBUG_UTILS_OBJECT_NAME_INFO_EXT;
                name_info.objectType = VK_OBJECT_TYPE_BUFFER;
                name_info.objectHandle = (uint64_t)(uintptr_t)debug_buffer;
                name_info.pObjectName = "buffer-name";
                if (vkSetDebugUtilsObjectNameEXT(device, &name_info) != VK_SUCCESS) return 9;

                const uint32_t tag = 0xcafebabeu;
                VkDebugUtilsObjectTagInfoEXT tag_info;
                memset(&tag_info, 0, sizeof(tag_info));
                tag_info.sType = VK_STRUCTURE_TYPE_DEBUG_UTILS_OBJECT_TAG_INFO_EXT;
                tag_info.objectType = VK_OBJECT_TYPE_BUFFER;
                tag_info.objectHandle = (uint64_t)(uintptr_t)debug_buffer;
                tag_info.tagName = 1u;
                tag_info.tagSize = sizeof(tag);
                tag_info.pTag = &tag;
                if (vkSetDebugUtilsObjectTagEXT(device, &tag_info) != VK_SUCCESS) return 10;

                VkDebugUtilsLabelEXT label;
                memset(&label, 0, sizeof(label));
                label.sType = VK_STRUCTURE_TYPE_DEBUG_UTILS_LABEL_EXT;
                label.pLabelName = "label";
                vkQueueBeginDebugUtilsLabelEXT(VK_NULL_HANDLE, &label);
                vkQueueInsertDebugUtilsLabelEXT(VK_NULL_HANDLE, &label);
                vkQueueEndDebugUtilsLabelEXT(VK_NULL_HANDLE);
                vkCmdBeginDebugUtilsLabelEXT(VK_NULL_HANDLE, &label);
                vkCmdInsertDebugUtilsLabelEXT(VK_NULL_HANDLE, &label);
                vkCmdEndDebugUtilsLabelEXT(VK_NULL_HANDLE);

                VkDebugUtilsMessengerEXT invalid_messenger = (VkDebugUtilsMessengerEXT)(uintptr_t)0xfeedu;
                memset(&messenger_info, 0, sizeof(messenger_info));
                messenger_info.sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO;
                if (vkCreateDebugUtilsMessengerEXT(instance, &messenger_info, NULL, &invalid_messenger) != VK_ERROR_INITIALIZATION_FAILED) return 11;
                if (invalid_messenger != VK_NULL_HANDLE) return 12;
                memset(&name_info, 0, sizeof(name_info));
                name_info.sType = VK_STRUCTURE_TYPE_DEBUG_UTILS_OBJECT_NAME_INFO_EXT;
                name_info.objectType = VK_OBJECT_TYPE_UNKNOWN;
                name_info.objectHandle = 0x1234u;
                if (vkSetDebugUtilsObjectNameEXT(device, &name_info) != VK_ERROR_INITIALIZATION_FAILED) return 13;

                vkDestroyBuffer(device, debug_buffer, NULL);
                vkDestroyDevice(device, NULL);
                vkDestroyDebugUtilsMessengerEXT(instance, (VkDebugUtilsMessengerEXT)(uintptr_t)0x1234u, NULL);
                vkDestroyInstance(instance_b, NULL);
                if (!debug_utils_messenger_handle_lookup_for_instance(instance, messenger)) return 37;
                vkDestroyDebugUtilsMessengerEXT(instance, messenger, NULL);
                if (debug_utils_messenger_handle_lookup_for_instance(instance, messenger)) return 38;
                vkDestroyDebugUtilsMessengerEXT(instance, VK_NULL_HANDLE, NULL);
                vkDestroyInstance(instance, NULL);
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_private_data_feature_and_device_create_info_are_icd_local_metadata(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            int main(void) {{
                VkPhysicalDevicePrivateDataFeatures private_features;
                memset(&private_features, 0xff, sizeof(private_features));
                private_features.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PRIVATE_DATA_FEATURES;
                private_features.pNext = NULL;
                fill_pnext_features(&private_features);
                if (private_features.sType != VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PRIVATE_DATA_FEATURES) return 2;
                if (private_features.pNext != NULL) return 3;
                if (private_features.privateData != VK_TRUE) return 4;

                VkDeviceCreateInfo create_info;
                memset(&create_info, 0, sizeof(create_info));
                create_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                private_features.privateData = VK_TRUE;
                create_info.pNext = &private_features;
                if (validate_device_feature_requests(&create_info) != VK_SUCCESS) return 5;
                private_features.privateData = VK_FALSE;
                if (validate_device_feature_requests(&create_info) != VK_SUCCESS) return 6;

                VkDevicePrivateDataCreateInfo private_create;
                memset(&private_create, 0, sizeof(private_create));
                private_create.sType = VK_STRUCTURE_TYPE_DEVICE_PRIVATE_DATA_CREATE_INFO;
                private_create.privateDataSlotRequestCount = 0;
                create_info.pNext = &private_create;
                if (validate_device_feature_requests(&create_info) != VK_SUCCESS) return 7;
                private_create.privateDataSlotRequestCount = 4;
                if (validate_device_feature_requests(&create_info) != VK_SUCCESS) return 8;

                uint32_t extension_count = 0;
                if (vkEnumerateDeviceExtensionProperties((VkPhysicalDevice)physical_device_for_instance(NULL), NULL, &extension_count, NULL) != VK_SUCCESS) return 9;
                VkExtensionProperties extensions[64];
                memset(extensions, 0, sizeof(extensions));
                uint32_t capacity = 64;
                if (vkEnumerateDeviceExtensionProperties((VkPhysicalDevice)physical_device_for_instance(NULL), NULL, &capacity, extensions) != VK_SUCCESS) return 10;
                VkBool32 found = VK_FALSE;
                for (uint32_t i = 0; i < capacity; ++i) {{
                    if (strcmp(extensions[i].extensionName, VK_EXT_PRIVATE_DATA_EXTENSION_NAME) == 0) found = VK_TRUE;
                }}
                if (!found || !device_extension_advertised_name(VK_EXT_PRIVATE_DATA_EXTENSION_NAME)) return 11;
                if (proc_address("vkCreatePrivateDataSlot") != NULL) return 26;
                if (proc_address("vkCreatePrivateDataSlotEXT") == NULL) return 27;
                if (proc_address("vkSetPrivateData") != NULL) return 28;
                if (proc_address("vkSetPrivateDataEXT") == NULL) return 29;

                VkInstanceCreateInfo instance_info;
                memset(&instance_info, 0, sizeof(instance_info));
                instance_info.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO;
                VkInstance instance = VK_NULL_HANDLE;
                if (vkCreateInstance(&instance_info, NULL, &instance) != VK_SUCCESS || instance == VK_NULL_HANDLE) return 32;
                uint32_t physical_count = 1;
                VkPhysicalDevice physical = VK_NULL_HANDLE;
                if (vkEnumeratePhysicalDevices(instance, &physical_count, &physical) != VK_SUCCESS || physical_count != 1 || physical == VK_NULL_HANDLE) return 33;
                VkDeviceCreateInfo real_device_info;
                memset(&real_device_info, 0, sizeof(real_device_info));
                real_device_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                VkDevice device = VK_NULL_HANDLE;
                if (vkCreateDevice(physical, &real_device_info, NULL, &device) != VK_SUCCESS || device == VK_NULL_HANDLE) return 34;
                VkBufferCreateInfo buffer_info;
                memset(&buffer_info, 0, sizeof(buffer_info));
                buffer_info.sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO;
                buffer_info.size = 256;
                buffer_info.usage = VK_BUFFER_USAGE_STORAGE_BUFFER_BIT;
                buffer_info.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
                VkBuffer buffer = VK_NULL_HANDLE;
                if (vkCreateBuffer(device, &buffer_info, NULL, &buffer) != VK_SUCCESS || buffer == VK_NULL_HANDLE) return 35;
                const uint64_t buffer_handle = (uint64_t)(uintptr_t)buffer;

                VkPrivateDataSlotCreateInfo slot_info;
                memset(&slot_info, 0, sizeof(slot_info));
                slot_info.sType = VK_STRUCTURE_TYPE_PRIVATE_DATA_SLOT_CREATE_INFO;
                VkPrivateDataSlot slot = (VkPrivateDataSlot)(uintptr_t)0xfeedu;
                if (vkCreatePrivateDataSlot(device, &slot_info, NULL, &slot) != VK_SUCCESS || slot == VK_NULL_HANDLE) return 12;
                if (vkSetPrivateData(device, VK_OBJECT_TYPE_BUFFER, buffer_handle, slot, 0xabcdu) != VK_SUCCESS) return 13;
                uint64_t data = 0;
                vkGetPrivateData(device, VK_OBJECT_TYPE_BUFFER, buffer_handle, slot, &data);
                if (data != 0xabcdu) return 14;
                if (vkSetPrivateData(device, VK_OBJECT_TYPE_BUFFER, buffer_handle, slot, 0xdefu) != VK_SUCCESS) return 15;
                vkGetPrivateData(device, VK_OBJECT_TYPE_BUFFER, buffer_handle, slot, &data);
                if (data != 0xdefu) return 16;
                vkGetPrivateData(device, VK_OBJECT_TYPE_IMAGE, buffer_handle, slot, &data);
                if (data != 0) return 17;
                if (vkSetPrivateData(device, VK_OBJECT_TYPE_BUFFER, buffer_handle, slot, 0) != VK_SUCCESS) return 18;
                data = 0x777u;
                vkGetPrivateData(device, VK_OBJECT_TYPE_BUFFER, buffer_handle, slot, &data);
                if (data != 0) return 19;

                VkPrivateDataSlot invalid_slot = (VkPrivateDataSlot)(uintptr_t)0xfeedu;
                memset(&slot_info, 0, sizeof(slot_info));
                slot_info.sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO;
                if (vkCreatePrivateDataSlot(device, &slot_info, NULL, &invalid_slot) != VK_ERROR_INITIALIZATION_FAILED) return 20;
                if (invalid_slot != VK_NULL_HANDLE) return 21;

                VkBaseOutStructure unknown;
                memset(&unknown, 0, sizeof(unknown));
                unknown.sType = (VkStructureType)0x3fffffff;
                memset(&slot_info, 0, sizeof(slot_info));
                slot_info.sType = VK_STRUCTURE_TYPE_PRIVATE_DATA_SLOT_CREATE_INFO;
                slot_info.pNext = &unknown;
                if (vkCreatePrivateDataSlot(device, &slot_info, NULL, &invalid_slot) != VK_ERROR_FEATURE_NOT_PRESENT) return 22;

                memset(&slot_info, 0, sizeof(slot_info));
                slot_info.sType = VK_STRUCTURE_TYPE_PRIVATE_DATA_SLOT_CREATE_INFO;
                slot_info.flags = (VkPrivateDataSlotCreateFlags)1u;
                if (vkCreatePrivateDataSlot(device, &slot_info, NULL, &invalid_slot) != VK_ERROR_FEATURE_NOT_PRESENT) return 23;
                if (vkSetPrivateData(device, VK_OBJECT_TYPE_BUFFER, buffer_handle, VK_NULL_HANDLE, 1u) != VK_ERROR_INITIALIZATION_FAILED) return 24;
                if (vkSetPrivateData(device, VK_OBJECT_TYPE_UNKNOWN, buffer_handle, slot, 1u) != VK_ERROR_INITIALIZATION_FAILED) return 25;

                VkPrivateDataSlot bogus_slot = (VkPrivateDataSlot)(uintptr_t)0x1234u;
                if (vkSetPrivateData(device, VK_OBJECT_TYPE_BUFFER, buffer_handle, bogus_slot, 1u) != VK_ERROR_INITIALIZATION_FAILED) return 30;
                data = 0x777u;
                vkGetPrivateData(device, VK_OBJECT_TYPE_BUFFER, buffer_handle, bogus_slot, &data);
                if (data != 0) return 31;
                vkDestroyPrivateDataSlot(device, bogus_slot, NULL);

                VkQueue queue = VK_NULL_HANDLE;
                vkGetDeviceQueue(device, 0, 0, &queue);
                PdockerVkQueue *queue_obj = pdocker_vk_queue_from_handle(queue);
                if (!queue || !queue_obj) return 36;
                const uint64_t queue_handle = (uint64_t)(uintptr_t)queue;
                if (vkSetPrivateData(device, VK_OBJECT_TYPE_QUEUE, queue_handle, slot, 0x4242u) != VK_SUCCESS) return 37;
                data = 0;
                vkGetPrivateData(device, VK_OBJECT_TYPE_QUEUE, queue_handle, slot, &data);
                if (data != 0x4242u) return 38;

                PdockerVkQueue forged_queue;
                memset(&forged_queue, 0, sizeof(forged_queue));
                forged_queue.object_id = queue_obj->object_id;
                forged_queue.instance_object_id = queue_obj->instance_object_id;
                forged_queue.physical_device_object_id = queue_obj->physical_device_object_id;
                forged_queue.device_object_id = queue_obj->device_object_id;
                const uint64_t forged_queue_handle = (uint64_t)(uintptr_t)&forged_queue;
                if (vkSetPrivateData(device, VK_OBJECT_TYPE_QUEUE, forged_queue_handle, slot, 1u) != VK_ERROR_INITIALIZATION_FAILED) return 39;
                data = 0x777u;
                vkGetPrivateData(device, VK_OBJECT_TYPE_QUEUE, forged_queue_handle, slot, &data);
                if (data != 0) return 40;

                const uint64_t bad_queue_handle = (uint64_t)(uintptr_t)0x12345678u;
                if (vkSetPrivateData(device, VK_OBJECT_TYPE_QUEUE, bad_queue_handle, slot, 1u) != VK_ERROR_INITIALIZATION_FAILED) return 41;
                data = 0x777u;
                vkGetPrivateData(device, VK_OBJECT_TYPE_QUEUE, bad_queue_handle, slot, &data);
                if (data != 0) return 42;

                vkDestroyPrivateDataSlot(device, slot, NULL);
                vkDestroyPrivateDataSlot(device, VK_NULL_HANDLE, NULL);
                vkDestroyBuffer(device, buffer, NULL);
                vkDestroyDevice(device, NULL);
                vkDestroyInstance(instance, NULL);
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_owner_zero_objects_do_not_match_real_devices_or_queues(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            int main(void) {{
                VkDeviceCreateInfo device_info;
                memset(&device_info, 0, sizeof(device_info));
                device_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                VkDevice device = VK_NULL_HANDLE;
                if (vkCreateDevice((VkPhysicalDevice)&g_device, &device_info, NULL, &device) != VK_SUCCESS ||
                    device == VK_NULL_HANDLE) return 2;
                VkQueue queue = VK_NULL_HANDLE;
                vkGetDeviceQueue(device, 0, 0, &queue);
                PdockerVkQueue *queue_obj = pdocker_vk_queue_from_handle(queue);
                if (!queue_obj || queue_obj->device_object_id == 0) return 3;

                VkBufferCreateInfo buffer_info;
                memset(&buffer_info, 0, sizeof(buffer_info));
                buffer_info.sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO;
                buffer_info.size = 256;
                buffer_info.usage = VK_BUFFER_USAGE_STORAGE_BUFFER_BIT;
                buffer_info.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
                VkBuffer unowned_buffer = VK_NULL_HANDLE;
                if (vkCreateBuffer(VK_NULL_HANDLE, &buffer_info, NULL, &unowned_buffer) != VK_SUCCESS ||
                    unowned_buffer == VK_NULL_HANDLE) return 4;
                if (!buffer_handle_lookup_for_device(VK_NULL_HANDLE, unowned_buffer)) return 5;
                if (buffer_handle_lookup_for_device(device, unowned_buffer)) return 6;
                if (pdocker_vk_object_handle_owned_by_device(
                        device,
                        VK_OBJECT_TYPE_BUFFER,
                        (uint64_t)(uintptr_t)unowned_buffer)) return 7;

                VkMemoryAllocateInfo alloc_info;
                memset(&alloc_info, 0, sizeof(alloc_info));
                alloc_info.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO;
                alloc_info.allocationSize = 256;
                alloc_info.memoryTypeIndex = 1;
                VkDeviceMemory unowned_memory = VK_NULL_HANDLE;
                if (vkAllocateMemory(VK_NULL_HANDLE, &alloc_info, NULL, &unowned_memory) != VK_SUCCESS ||
                    unowned_memory == VK_NULL_HANDLE) return 8;
                if (!memory_handle_lookup_for_device(VK_NULL_HANDLE, unowned_memory)) return 9;
                if (memory_handle_lookup_for_device(device, unowned_memory)) return 10;
                if (vkBindBufferMemory(device, unowned_buffer, unowned_memory, 0) != VK_ERROR_INITIALIZATION_FAILED) return 11;

                VkFenceCreateInfo fence_info;
                memset(&fence_info, 0, sizeof(fence_info));
                fence_info.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO;
                VkFence unowned_fence = VK_NULL_HANDLE;
                if (vkCreateFence(VK_NULL_HANDLE, &fence_info, NULL, &unowned_fence) != VK_SUCCESS ||
                    unowned_fence == VK_NULL_HANDLE) return 12;
                if (!fence_handle_lookup_for_device(VK_NULL_HANDLE, unowned_fence)) return 13;
                if (fence_handle_lookup_for_queue(queue_obj, unowned_fence)) return 14;
                if (vkQueueSubmit(queue, 0, NULL, unowned_fence) != VK_ERROR_INITIALIZATION_FAILED) return 15;

                vkDestroyFence(VK_NULL_HANDLE, unowned_fence, NULL);
                vkFreeMemory(VK_NULL_HANDLE, unowned_memory, NULL);
                vkDestroyBuffer(VK_NULL_HANDLE, unowned_buffer, NULL);
                vkDestroyDevice(device, NULL);
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_memory_requirements2_bad_info_zeroes_outputs(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            static void poison_req2(VkMemoryRequirements2 *req2, VkMemoryDedicatedRequirements *dedicated) {{
                memset(req2, 0xff, sizeof(*req2));
                memset(dedicated, 0xff, sizeof(*dedicated));
                dedicated->sType = VK_STRUCTURE_TYPE_MEMORY_DEDICATED_REQUIREMENTS;
                dedicated->pNext = NULL;
                req2->sType = VK_STRUCTURE_TYPE_MEMORY_REQUIREMENTS_2;
                req2->pNext = dedicated;
            }}

            static int expect_zero_req2(const VkMemoryRequirements2 *req2,
                                        const VkMemoryDedicatedRequirements *dedicated,
                                        int code) {{
                if (req2->sType != VK_STRUCTURE_TYPE_MEMORY_REQUIREMENTS_2 ||
                    req2->pNext != dedicated ||
                    req2->memoryRequirements.size != 0 ||
                    req2->memoryRequirements.alignment != 0 ||
                    req2->memoryRequirements.memoryTypeBits != 0 ||
                    dedicated->sType != VK_STRUCTURE_TYPE_MEMORY_DEDICATED_REQUIREMENTS ||
                    dedicated->pNext != NULL ||
                    dedicated->prefersDedicatedAllocation != VK_FALSE ||
                    dedicated->requiresDedicatedAllocation != VK_FALSE) {{
                    fprintf(stderr,
                            "memory requirements2 output was not zeroed for case %d: size=%llu alignment=%llu bits=0x%x prefers=%u requires=%u\\n",
                            code,
                            (unsigned long long)req2->memoryRequirements.size,
                            (unsigned long long)req2->memoryRequirements.alignment,
                            req2->memoryRequirements.memoryTypeBits,
                            dedicated->prefersDedicatedAllocation,
                            dedicated->requiresDedicatedAllocation);
                    return code;
                }}
                return 0;
            }}

            int main(void) {{
                VkMemoryRequirements2 req2;
                VkMemoryDedicatedRequirements dedicated;
                VkBaseOutStructure bad_pnext;
                memset(&bad_pnext, 0, sizeof(bad_pnext));
                bad_pnext.sType = (VkStructureType)0x3fffffff;
                bad_pnext.pNext = NULL;

                poison_req2(&req2, &dedicated);
                vkGetBufferMemoryRequirements2(VK_NULL_HANDLE, NULL, &req2);
                if (expect_zero_req2(&req2, &dedicated, 2)) return 2;

                VkBufferMemoryRequirementsInfo2 buffer_info;
                memset(&buffer_info, 0, sizeof(buffer_info));
                buffer_info.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO;
                poison_req2(&req2, &dedicated);
                vkGetBufferMemoryRequirements2(VK_NULL_HANDLE, &buffer_info, &req2);
                if (expect_zero_req2(&req2, &dedicated, 3)) return 3;

                memset(&buffer_info, 0, sizeof(buffer_info));
                buffer_info.sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_REQUIREMENTS_INFO_2;
                buffer_info.pNext = &bad_pnext;
                buffer_info.buffer = (VkBuffer)(uintptr_t)0x1234u;
                poison_req2(&req2, &dedicated);
                vkGetBufferMemoryRequirements2(VK_NULL_HANDLE, &buffer_info, &req2);
                if (expect_zero_req2(&req2, &dedicated, 4)) return 4;

                poison_req2(&req2, &dedicated);
                vkGetImageMemoryRequirements2(VK_NULL_HANDLE, NULL, &req2);
                if (expect_zero_req2(&req2, &dedicated, 5)) return 5;

                VkImageMemoryRequirementsInfo2 image_info;
                memset(&image_info, 0, sizeof(image_info));
                image_info.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO;
                poison_req2(&req2, &dedicated);
                vkGetImageMemoryRequirements2(VK_NULL_HANDLE, &image_info, &req2);
                if (expect_zero_req2(&req2, &dedicated, 6)) return 6;

                memset(&image_info, 0, sizeof(image_info));
                image_info.sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_REQUIREMENTS_INFO_2;
                image_info.pNext = &bad_pnext;
                image_info.image = (VkImage)(uintptr_t)0x1234u;
                poison_req2(&req2, &dedicated);
                vkGetImageMemoryRequirements2(VK_NULL_HANDLE, &image_info, &req2);
                if (expect_zero_req2(&req2, &dedicated, 7)) return 7;
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_memory_priority_feature_is_false_only_and_not_advertised_without_transport(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            int main(void) {{
                VkPhysicalDeviceMemoryPriorityFeaturesEXT priority_features;
                memset(&priority_features, 0xff, sizeof(priority_features));
                priority_features.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MEMORY_PRIORITY_FEATURES_EXT;
                priority_features.pNext = NULL;
                fill_pnext_features(&priority_features);
                if (priority_features.sType != VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MEMORY_PRIORITY_FEATURES_EXT) {{
                    fprintf(stderr, "memory priority feature sType was not preserved\\n");
                    return 2;
                }}
                if (priority_features.pNext != NULL) {{
                    fprintf(stderr, "memory priority feature pNext was not preserved\\n");
                    return 3;
                }}
                if (priority_features.memoryPriority != VK_FALSE) {{
                    fprintf(stderr, "memoryPriority was advertised without priority replay support\\n");
                    return 4;
                }}

                VkDeviceCreateInfo create_info;
                memset(&create_info, 0, sizeof(create_info));
                create_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                priority_features.memoryPriority = VK_TRUE;
                create_info.pNext = &priority_features;
                if (validate_device_feature_requests(&create_info) == VK_SUCCESS) {{
                    fprintf(stderr, "memoryPriority=true was accepted\\n");
                    return 5;
                }}
                priority_features.memoryPriority = VK_FALSE;
                if (validate_device_feature_requests(&create_info) != VK_SUCCESS) {{
                    fprintf(stderr, "memoryPriority=false was rejected\\n");
                    return 6;
                }}

            #ifdef VK_EXT_MEMORY_PRIORITY_EXTENSION_NAME
                if (device_extension_advertised_name(VK_EXT_MEMORY_PRIORITY_EXTENSION_NAME)) {{
                    fprintf(stderr, "VK_EXT_memory_priority was advertised without priority transport\\n");
                    return 7;
                }}
                const char *enabled_extensions[] = {{ VK_EXT_MEMORY_PRIORITY_EXTENSION_NAME }};
                VkDeviceCreateInfo extension_info;
                memset(&extension_info, 0, sizeof(extension_info));
                extension_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                extension_info.enabledExtensionCount = 1;
                extension_info.ppEnabledExtensionNames = enabled_extensions;
                if (validate_device_extensions(&extension_info) != VK_ERROR_EXTENSION_NOT_PRESENT) {{
                    fprintf(stderr, "VK_EXT_memory_priority extension enable was accepted without transport\\n");
                    return 8;
                }}
            #endif
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_robustness_features_are_queryable_false_only(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            int main(void) {{
                VkPhysicalDeviceRobustness2FeaturesEXT robustness2;
                VkPhysicalDeviceImageRobustnessFeatures image_robustness;
                VkPhysicalDevicePipelineRobustnessFeatures pipeline_robustness;
                VkDeviceCreateInfo create_info;

                memset(&robustness2, 0xff, sizeof(robustness2));
                memset(&image_robustness, 0xff, sizeof(image_robustness));
                memset(&pipeline_robustness, 0xff, sizeof(pipeline_robustness));
                robustness2.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_ROBUSTNESS_2_FEATURES_EXT;
                image_robustness.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_IMAGE_ROBUSTNESS_FEATURES;
                pipeline_robustness.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PIPELINE_ROBUSTNESS_FEATURES;
                robustness2.pNext = &image_robustness;
                image_robustness.pNext = &pipeline_robustness;
                pipeline_robustness.pNext = NULL;
                fill_pnext_features(&robustness2);
                if (robustness2.pNext != &image_robustness ||
                    image_robustness.pNext != &pipeline_robustness ||
                    pipeline_robustness.pNext != NULL) {{
                    fprintf(stderr, "robustness pNext chain was not preserved\\n");
                    return 2;
                }}
                if (robustness2.robustBufferAccess2 != VK_FALSE ||
                    robustness2.robustImageAccess2 != VK_FALSE ||
                    robustness2.nullDescriptor != VK_FALSE ||
                    image_robustness.robustImageAccess != VK_FALSE ||
                    pipeline_robustness.pipelineRobustness != VK_FALSE) {{
                    fprintf(stderr, "robustness features were not false-only\\n");
                    return 3;
                }}
                memset(&create_info, 0, sizeof(create_info));
                create_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                create_info.pNext = &robustness2;
                if (validate_device_feature_requests(&create_info) != VK_SUCCESS) {{
                    fprintf(stderr, "all-false robustness chain was rejected\\n");
                    return 4;
                }}
            #ifdef VK_EXT_ROBUSTNESS_2_EXTENSION_NAME
                if (!device_extension_advertised_name(VK_EXT_ROBUSTNESS_2_EXTENSION_NAME)) {{
                    fprintf(stderr, "VK_EXT_robustness2 was not advertised\\n");
                    return 14;
                }}
                const char *robustness2_extensions[] = {{ VK_EXT_ROBUSTNESS_2_EXTENSION_NAME }};
                create_info.enabledExtensionCount = 1;
                create_info.ppEnabledExtensionNames = robustness2_extensions;
                if (validate_device_extensions(&create_info) != VK_SUCCESS) {{
                    fprintf(stderr, "VK_EXT_robustness2 extension enable was rejected\\n");
                    return 15;
                }}
                if (validate_device_feature_requests(&create_info) != VK_SUCCESS) {{
                    fprintf(stderr, "VK_EXT_robustness2 all-false feature chain was rejected\\n");
                    return 16;
                }}
                create_info.enabledExtensionCount = 0;
                create_info.ppEnabledExtensionNames = NULL;
            #endif
            #ifdef VK_EXT_IMAGE_ROBUSTNESS_EXTENSION_NAME
                if (!device_extension_advertised_name(VK_EXT_IMAGE_ROBUSTNESS_EXTENSION_NAME)) {{
                    fprintf(stderr, "VK_EXT_image_robustness was not advertised\\n");
                    return 11;
                }}
                const char *image_robustness_extensions[] = {{ VK_EXT_IMAGE_ROBUSTNESS_EXTENSION_NAME }};
                create_info.enabledExtensionCount = 1;
                create_info.ppEnabledExtensionNames = image_robustness_extensions;
                if (validate_device_extensions(&create_info) != VK_SUCCESS) {{
                    fprintf(stderr, "VK_EXT_image_robustness extension enable was rejected\\n");
                    return 12;
                }}
                if (validate_device_feature_requests(&create_info) != VK_SUCCESS) {{
                    fprintf(stderr, "VK_EXT_image_robustness all-false feature chain was rejected\\n");
                    return 13;
                }}
                create_info.enabledExtensionCount = 0;
                create_info.ppEnabledExtensionNames = NULL;
            #endif
            #ifdef VK_EXT_PIPELINE_ROBUSTNESS_EXTENSION_NAME
                if (!device_extension_advertised_name(VK_EXT_PIPELINE_ROBUSTNESS_EXTENSION_NAME)) {{
                    fprintf(stderr, "VK_EXT_pipeline_robustness was not advertised\\n");
                    return 8;
                }}
                const char *pipeline_robustness_extensions[] = {{ VK_EXT_PIPELINE_ROBUSTNESS_EXTENSION_NAME }};
                create_info.enabledExtensionCount = 1;
                create_info.ppEnabledExtensionNames = pipeline_robustness_extensions;
                if (validate_device_extensions(&create_info) != VK_SUCCESS) {{
                    fprintf(stderr, "VK_EXT_pipeline_robustness extension enable was rejected\\n");
                    return 9;
                }}
                if (validate_device_feature_requests(&create_info) != VK_SUCCESS) {{
                    fprintf(stderr, "VK_EXT_pipeline_robustness all-false feature chain was rejected\\n");
                    return 10;
                }}
                create_info.enabledExtensionCount = 0;
                create_info.ppEnabledExtensionNames = NULL;
            #endif
                robustness2.nullDescriptor = VK_TRUE;
                if (validate_device_feature_requests(&create_info) == VK_SUCCESS) return 5;
                robustness2.nullDescriptor = VK_FALSE;
                image_robustness.robustImageAccess = VK_TRUE;
                if (validate_device_feature_requests(&create_info) == VK_SUCCESS) return 6;
                image_robustness.robustImageAccess = VK_FALSE;
                pipeline_robustness.pipelineRobustness = VK_TRUE;
                if (validate_device_feature_requests(&create_info) == VK_SUCCESS) return 7;
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stderr)



    def test_shader_layout_memory_model_extensions_are_not_advertised_without_transport(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            int main(void) {{
                VkPhysicalDeviceScalarBlockLayoutFeatures scalar;
                VkPhysicalDeviceVulkanMemoryModelFeatures memory_model;
                VkPhysicalDeviceUniformBufferStandardLayoutFeatures uniform_layout;
                VkPhysicalDeviceShaderSubgroupExtendedTypesFeatures subgroup_types;
                VkDeviceCreateInfo create_info;

                memset(&scalar, 0xff, sizeof(scalar));
                memset(&memory_model, 0xff, sizeof(memory_model));
                memset(&uniform_layout, 0xff, sizeof(uniform_layout));
                memset(&subgroup_types, 0xff, sizeof(subgroup_types));
                scalar.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SCALAR_BLOCK_LAYOUT_FEATURES;
                memory_model.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_MEMORY_MODEL_FEATURES;
                uniform_layout.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_UNIFORM_BUFFER_STANDARD_LAYOUT_FEATURES;
                subgroup_types.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SHADER_SUBGROUP_EXTENDED_TYPES_FEATURES;
                scalar.pNext = &memory_model;
                memory_model.pNext = &uniform_layout;
                uniform_layout.pNext = &subgroup_types;
                subgroup_types.pNext = NULL;

                fill_pnext_features(&scalar);
                if (scalar.pNext != &memory_model || memory_model.pNext != &uniform_layout ||
                    uniform_layout.pNext != &subgroup_types || subgroup_types.pNext != NULL) {{
                    fprintf(stderr, "shader layout pNext chain was not preserved\\n");
                    return 2;
                }}
                if (scalar.scalarBlockLayout != VK_FALSE ||
                    memory_model.vulkanMemoryModel != VK_FALSE ||
                    memory_model.vulkanMemoryModelDeviceScope != VK_FALSE ||
                    memory_model.vulkanMemoryModelAvailabilityVisibilityChains != VK_FALSE ||
                    uniform_layout.uniformBufferStandardLayout != VK_FALSE ||
                    subgroup_types.shaderSubgroupExtendedTypes != VK_FALSE) {{
                    fprintf(stderr, "shader layout features were not false-only\\n");
                    return 3;
                }}

                memset(&create_info, 0, sizeof(create_info));
                create_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                create_info.pNext = &scalar;
                if (validate_device_feature_requests(&create_info) != VK_SUCCESS) {{
                    fprintf(stderr, "all-false shader layout chain was rejected\\n");
                    return 4;
                }}
            #ifdef VK_EXT_SCALAR_BLOCK_LAYOUT_EXTENSION_NAME
                const char *scalar_extensions[] = {{ VK_EXT_SCALAR_BLOCK_LAYOUT_EXTENSION_NAME }};
                create_info.enabledExtensionCount = 1;
                create_info.ppEnabledExtensionNames = scalar_extensions;
                if (device_extension_advertised_name(VK_EXT_SCALAR_BLOCK_LAYOUT_EXTENSION_NAME) ||
                    validate_device_extensions(&create_info) != VK_ERROR_EXTENSION_NOT_PRESENT) {{
                    fprintf(stderr, "VK_EXT_scalar_block_layout was accepted without transport\\n");
                    return 5;
                }}
            #endif
            #ifdef VK_KHR_UNIFORM_BUFFER_STANDARD_LAYOUT_EXTENSION_NAME
                const char *uniform_extensions[] = {{ VK_KHR_UNIFORM_BUFFER_STANDARD_LAYOUT_EXTENSION_NAME }};
                create_info.enabledExtensionCount = 1;
                create_info.ppEnabledExtensionNames = uniform_extensions;
                if (device_extension_advertised_name(VK_KHR_UNIFORM_BUFFER_STANDARD_LAYOUT_EXTENSION_NAME) ||
                    validate_device_extensions(&create_info) != VK_ERROR_EXTENSION_NOT_PRESENT) {{
                    fprintf(stderr, "VK_KHR_uniform_buffer_standard_layout was accepted without transport\\n");
                    return 6;
                }}
            #endif
            #ifdef VK_KHR_SHADER_SUBGROUP_EXTENDED_TYPES_EXTENSION_NAME
                const char *subgroup_extensions[] = {{ VK_KHR_SHADER_SUBGROUP_EXTENDED_TYPES_EXTENSION_NAME }};
                create_info.enabledExtensionCount = 1;
                create_info.ppEnabledExtensionNames = subgroup_extensions;
                if (device_extension_advertised_name(VK_KHR_SHADER_SUBGROUP_EXTENDED_TYPES_EXTENSION_NAME) ||
                    validate_device_extensions(&create_info) != VK_ERROR_EXTENSION_NOT_PRESENT) {{
                    fprintf(stderr, "VK_KHR_shader_subgroup_extended_types was accepted without transport\\n");
                    return 7;
                }}
            #endif
            #ifdef VK_KHR_VULKAN_MEMORY_MODEL_EXTENSION_NAME
                const char *memory_model_extensions[] = {{ VK_KHR_VULKAN_MEMORY_MODEL_EXTENSION_NAME }};
                create_info.enabledExtensionCount = 1;
                create_info.ppEnabledExtensionNames = memory_model_extensions;
                if (device_extension_advertised_name(VK_KHR_VULKAN_MEMORY_MODEL_EXTENSION_NAME) ||
                    validate_device_extensions(&create_info) != VK_ERROR_EXTENSION_NOT_PRESENT) {{
                    fprintf(stderr, "VK_KHR_vulkan_memory_model was accepted without transport\\n");
                    return 8;
                }}
            #endif
                create_info.enabledExtensionCount = 0;
                create_info.ppEnabledExtensionNames = NULL;

                scalar.scalarBlockLayout = VK_TRUE;
                if (validate_device_feature_requests(&create_info) == VK_SUCCESS) return 9;
                scalar.scalarBlockLayout = VK_FALSE;
                uniform_layout.uniformBufferStandardLayout = VK_TRUE;
                if (validate_device_feature_requests(&create_info) == VK_SUCCESS) return 10;
                uniform_layout.uniformBufferStandardLayout = VK_FALSE;
                subgroup_types.shaderSubgroupExtendedTypes = VK_TRUE;
                if (validate_device_feature_requests(&create_info) == VK_SUCCESS) return 11;
                subgroup_types.shaderSubgroupExtendedTypes = VK_FALSE;
                memory_model.vulkanMemoryModel = VK_TRUE;
                if (validate_device_feature_requests(&create_info) == VK_SUCCESS) return 12;
                memory_model.vulkanMemoryModel = VK_FALSE;
                memory_model.vulkanMemoryModelDeviceScope = VK_TRUE;
                if (validate_device_feature_requests(&create_info) == VK_SUCCESS) return 13;
                memory_model.vulkanMemoryModelDeviceScope = VK_FALSE;
                memory_model.vulkanMemoryModelAvailabilityVisibilityChains = VK_TRUE;
                if (validate_device_feature_requests(&create_info) == VK_SUCCESS) return 14;
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stderr)


    def test_separate_depth_stencil_layouts_fail_closed_without_executor_caps(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <stdlib.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            int main(void) {{
                unsetenv("PDOCKER_VULKAN_ADVERTISEMENT_SOURCE");

                VkPhysicalDeviceSeparateDepthStencilLayoutsFeatures separate;
                VkPhysicalDeviceHostQueryResetFeatures host_query;
                memset(&separate, 0xff, sizeof(separate));
                memset(&host_query, 0xff, sizeof(host_query));
                separate.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SEPARATE_DEPTH_STENCIL_LAYOUTS_FEATURES;
                host_query.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_HOST_QUERY_RESET_FEATURES;
                separate.pNext = &host_query;
                host_query.pNext = NULL;

                fill_pnext_features(&separate);
                if (separate.pNext != &host_query || host_query.pNext != NULL) {{
                    fprintf(stderr, "separate depth/stencil pNext chain was not preserved\\n");
                    return 2;
                }}
                if (separate.separateDepthStencilLayouts != VK_FALSE) {{
                    fprintf(stderr, "separate depth/stencil advertised without executor caps\\n");
                    return 3;
                }}
                if (host_query.hostQueryReset != VK_TRUE) {{
                    fprintf(stderr, "following pNext feature was not filled\\n");
                    return 4;
                }}

                VkDeviceCreateInfo create_info;
                memset(&create_info, 0, sizeof(create_info));
                create_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                create_info.pNext = &separate;
                if (validate_device_feature_requests(&create_info) != VK_SUCCESS) {{
                    fprintf(stderr, "false separate depth/stencil request was rejected\\n");
                    return 5;
                }}
                separate.separateDepthStencilLayouts = VK_TRUE;
                if (validate_device_feature_requests(&create_info) == VK_SUCCESS) {{
                    fprintf(stderr, "true separate depth/stencil request was accepted without caps\\n");
                    return 6;
                }}
                if ((feature_mask_from_pnext_chain(&separate) & PDOCKER_VK_FEATURE_SEPARATE_DEPTH_STENCIL_LAYOUTS) == 0) {{
                    fprintf(stderr, "separate depth/stencil request was not reflected in feature mask\\n");
                    return 7;
                }}
                if ((advertised_feature_mask() & PDOCKER_VK_FEATURE_SEPARATE_DEPTH_STENCIL_LAYOUTS) != 0) {{
                    fprintf(stderr, "separate depth/stencil advertised feature mask without caps\\n");
                    return 8;
                }}

                VkPhysicalDeviceVulkan12Features core12;
                memset(&core12, 0, sizeof(core12));
                core12.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES;
                fill_pnext_features(&core12);
                if (core12.separateDepthStencilLayouts != VK_FALSE) {{
                    fprintf(stderr, "core12 separate depth/stencil advertised without caps\\n");
                    return 9;
                }}
                core12.separateDepthStencilLayouts = VK_TRUE;
                create_info.pNext = &core12;
                if (validate_device_feature_requests(&create_info) == VK_SUCCESS) {{
                    fprintf(stderr, "core12 true separate depth/stencil request accepted without caps\\n");
                    return 10;
                }}

            #ifdef VK_KHR_SEPARATE_DEPTH_STENCIL_LAYOUTS_EXTENSION_NAME
                if (device_extension_advertised_name(VK_KHR_SEPARATE_DEPTH_STENCIL_LAYOUTS_EXTENSION_NAME)) {{
                    fprintf(stderr, "VK_KHR_separate_depth_stencil_layouts advertised without executor caps\\n");
                    return 11;
                }}
            #endif
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stderr)


    def test_khr_map_memory2_alias_maps_existing_memory_api(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            int main(void) {{
            #ifndef VK_KHR_map_memory2
                return 0;
            #else
                if (proc_address("vkMapMemory2") != NULL ||
                    proc_address("vkUnmapMemory2") != NULL) {{
                    fprintf(stderr, "core map-memory2 names visible below Vulkan 1.4\\n");
                    return 2;
                }}
                PFN_vkMapMemory2KHR map2 = (PFN_vkMapMemory2KHR)proc_address("vkMapMemory2KHR");
                PFN_vkUnmapMemory2KHR unmap2 = (PFN_vkUnmapMemory2KHR)proc_address("vkUnmapMemory2KHR");
                if (!map2 || !unmap2) {{
                    fprintf(stderr, "KHR map-memory2 aliases were not exposed\\n");
                    return 3;
                }}
                if (!device_extension_advertised_name(VK_KHR_MAP_MEMORY_2_EXTENSION_NAME)) {{
                    fprintf(stderr, "VK_KHR_map_memory2 was not advertised\\n");
                    return 4;
                }}

                VkMemoryAllocateInfo alloc;
                memset(&alloc, 0, sizeof(alloc));
                alloc.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO;
                alloc.allocationSize = 4096;
                alloc.memoryTypeIndex = 1;
                VkDeviceMemory memory = VK_NULL_HANDLE;
                if (vkAllocateMemory(VK_NULL_HANDLE, &alloc, NULL, &memory) != VK_SUCCESS) {{
                    fprintf(stderr, "memory allocation failed\\n");
                    return 5;
                }}
                PdockerVkMemory *memory_obj = pdocker_vk_memory_from_handle(memory);
                if (!memory_obj || !memory_obj->map) {{
                    fprintf(stderr, "allocated memory object missing map\\n");
                    return 6;
                }}

                VkMemoryMapInfo map_info;
                memset(&map_info, 0, sizeof(map_info));
                map_info.sType = VK_STRUCTURE_TYPE_MEMORY_MAP_INFO;
                map_info.memory = memory;
                map_info.offset = 32;
                map_info.size = 64;
                void *mapped = NULL;
                if (map2(VK_NULL_HANDLE, &map_info, &mapped) != VK_SUCCESS) {{
                    fprintf(stderr, "vkMapMemory2KHR failed\\n");
                    return 7;
                }}
                if (mapped != (void *)((char *)memory_obj->map + 32)) {{
                    fprintf(stderr, "vkMapMemory2KHR returned wrong mapped address\\n");
                    return 8;
                }}

                VkBaseInStructure dummy;
                memset(&dummy, 0, sizeof(dummy));
                dummy.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO;
                map_info.pNext = &dummy;
                mapped = NULL;
                if (map2(VK_NULL_HANDLE, &map_info, &mapped) == VK_SUCCESS) {{
                    fprintf(stderr, "vkMapMemory2KHR accepted unknown pNext\\n");
                    return 9;
                }}
                map_info.pNext = NULL;
                map_info.flags = 1;
                if (map2(VK_NULL_HANDLE, &map_info, &mapped) == VK_SUCCESS) {{
                    fprintf(stderr, "vkMapMemory2KHR accepted nonzero flags\\n");
                    return 10;
                }}

                VkMemoryUnmapInfo unmap_info;
                memset(&unmap_info, 0, sizeof(unmap_info));
                unmap_info.sType = VK_STRUCTURE_TYPE_MEMORY_UNMAP_INFO;
                unmap_info.memory = memory;
                if (unmap2(VK_NULL_HANDLE, &unmap_info) != VK_SUCCESS) {{
                    fprintf(stderr, "vkUnmapMemory2KHR failed\\n");
                    return 11;
                }}
                unmap_info.pNext = &dummy;
                if (unmap2(VK_NULL_HANDLE, &unmap_info) == VK_SUCCESS) {{
                    fprintf(stderr, "vkUnmapMemory2KHR accepted unknown pNext\\n");
                    return 12;
                }}
                unmap_info.pNext = NULL;
                unmap_info.flags = 1;
                if (unmap2(VK_NULL_HANDLE, &unmap_info) == VK_SUCCESS) {{
                    fprintf(stderr, "vkUnmapMemory2KHR accepted nonzero flags\\n");
                    return 13;
                }}
                unmap_info.flags = 0;

                VkDeviceMemory fake_memory = (VkDeviceMemory)(uintptr_t)0x1234u;
                map_info.memory = fake_memory;
                map_info.flags = 0;
                mapped = (void *)(uintptr_t)0x1234u;
                if (map2(VK_NULL_HANDLE, &map_info, &mapped) == VK_SUCCESS || mapped != NULL) {{
                    fprintf(stderr, "vkMapMemory2KHR accepted fake memory handle\\n");
                    return 14;
                }}
                unmap_info.memory = fake_memory;
                if (unmap2(VK_NULL_HANDLE, &unmap_info) == VK_SUCCESS) {{
                    fprintf(stderr, "vkUnmapMemory2KHR accepted fake memory handle\\n");
                    return 15;
                }}
                vkFreeMemory(VK_NULL_HANDLE, fake_memory, NULL);

                vkFreeMemory(VK_NULL_HANDLE, memory, NULL);
                map_info.memory = memory;
                if (map2(VK_NULL_HANDLE, &map_info, &mapped) == VK_SUCCESS) {{
                    fprintf(stderr, "vkMapMemory2KHR accepted stale freed memory handle\\n");
                    return 16;
                }}
                VkDeviceSize committed = 99;
                vkGetDeviceMemoryCommitment(VK_NULL_HANDLE, memory, &committed);
                if (committed != 0) {{
                    fprintf(stderr, "stale memory commitment was nonzero: %llu\\n", (unsigned long long)committed);
                    return 17;
                }}
                vkFreeMemory(VK_NULL_HANDLE, memory, NULL);
                return 0;
            #endif
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stderr)


    def test_dedicated_memory_bind_enforces_target_and_zero_offset(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            static VkBufferCreateInfo buffer_info(VkDeviceSize size) {{
                VkBufferCreateInfo info;
                memset(&info, 0, sizeof(info));
                info.sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO;
                info.size = size;
                info.usage = VK_BUFFER_USAGE_STORAGE_BUFFER_BIT;
                info.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
                return info;
            }}


            int main(void) {{
                VkBufferCreateInfo binfo = buffer_info(64);
                VkBuffer buffer_a = VK_NULL_HANDLE;
                VkBuffer buffer_b = VK_NULL_HANDLE;
                if (vkCreateBuffer(VK_NULL_HANDLE, &binfo, NULL, &buffer_a) != VK_SUCCESS ||
                    vkCreateBuffer(VK_NULL_HANDLE, &binfo, NULL, &buffer_b) != VK_SUCCESS) {{
                    fprintf(stderr, "buffer create failed\\n");
                    return 2;
                }}

                VkMemoryDedicatedAllocateInfo dedicated;
                memset(&dedicated, 0, sizeof(dedicated));
                dedicated.sType = VK_STRUCTURE_TYPE_MEMORY_DEDICATED_ALLOCATE_INFO;
                dedicated.buffer = buffer_a;
                VkMemoryAllocateInfo alloc;
                memset(&alloc, 0, sizeof(alloc));
                alloc.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO;
                alloc.pNext = &dedicated;
                alloc.allocationSize = 4096;
                alloc.memoryTypeIndex = 0;
                VkDeviceMemory memory = VK_NULL_HANDLE;
                if (vkAllocateMemory(VK_NULL_HANDLE, &alloc, NULL, &memory) != VK_SUCCESS) {{
                    fprintf(stderr, "buffer dedicated allocation failed\\n");
                    return 3;
                }}
                PdockerVkMemory *memory_obj = pdocker_vk_memory_from_handle(memory);
                if (!memory_obj || memory_obj->dedicated_buffer != pdocker_vk_buffer_from_handle(buffer_a) ||
                    memory_obj->dedicated_image != NULL) {{
                    fprintf(stderr, "buffer dedicated target was not recorded\\n");
                    return 4;
                }}
                if (vkBindBufferMemory(VK_NULL_HANDLE, buffer_a, memory, 0) != VK_SUCCESS) {{
                    fprintf(stderr, "dedicated buffer bind to target failed\\n");
                    return 5;
                }}
                if (vkBindBufferMemory(VK_NULL_HANDLE, buffer_a, memory, 16) == VK_SUCCESS) {{
                    fprintf(stderr, "dedicated buffer accepted nonzero offset\\n");
                    return 6;
                }}
                if (vkBindBufferMemory(VK_NULL_HANDLE, buffer_b, memory, 0) == VK_SUCCESS) {{
                    fprintf(stderr, "dedicated buffer accepted different buffer\\n");
                    return 7;
                }}

                PdockerVkImage *image_obj_a = pdocker_alloc_handle(sizeof(*image_obj_a));
                PdockerVkImage *image_obj_b = pdocker_alloc_handle(sizeof(*image_obj_b));
                if (!image_obj_a || !image_obj_b) {{
                    fprintf(stderr, "image test handle allocation failed\\n");
                    return 8;
                }}
                image_obj_a->object_id = 101;
                image_obj_a->requirements_alignment = PDOCKER_VK_REQUIREMENT_ALIGNMENT;
                image_obj_a->requirements_size = 4096;
                image_obj_a->memory_type_bits = 0x3;
                image_obj_b->object_id = 102;
                image_obj_b->requirements_alignment = PDOCKER_VK_REQUIREMENT_ALIGNMENT;
                image_obj_b->requirements_size = 4096;
                image_obj_b->memory_type_bits = 0x3;
                image_register(image_obj_a);
                image_register(image_obj_b);
                VkImage image_a = pdocker_vk_image_to_handle(image_obj_a);
                VkImage image_b = pdocker_vk_image_to_handle(image_obj_b);
                memset(&dedicated, 0, sizeof(dedicated));
                dedicated.sType = VK_STRUCTURE_TYPE_MEMORY_DEDICATED_ALLOCATE_INFO;
                dedicated.image = image_a;
                memset(&alloc, 0, sizeof(alloc));
                alloc.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO;
                alloc.pNext = &dedicated;
                alloc.allocationSize = 4096;
                alloc.memoryTypeIndex = 0;
                VkDeviceMemory image_memory = VK_NULL_HANDLE;
                if (vkAllocateMemory(VK_NULL_HANDLE, &alloc, NULL, &image_memory) != VK_SUCCESS) {{
                    fprintf(stderr, "image dedicated allocation failed\\n");
                    return 9;
                }}
                PdockerVkMemory *image_memory_obj = pdocker_vk_memory_from_handle(image_memory);
                if (!image_memory_obj || image_memory_obj->dedicated_image != image_handle_lookup(image_a) ||
                    image_memory_obj->dedicated_buffer != NULL) {{
                    fprintf(stderr, "image dedicated target was not recorded\\n");
                    return 10;
                }}
                if (vkBindImageMemory(VK_NULL_HANDLE, image_a, image_memory, 0) != VK_SUCCESS) {{
                    fprintf(stderr, "dedicated image bind to target failed\\n");
                    return 11;
                }}
                if (vkBindImageMemory(VK_NULL_HANDLE, image_b, image_memory, 0) == VK_SUCCESS) {{
                    fprintf(stderr, "dedicated image accepted different image\\n");
                    return 12;
                }}
                if (vkBindBufferMemory(VK_NULL_HANDLE, buffer_a, image_memory, 0) == VK_SUCCESS) {{
                    fprintf(stderr, "dedicated image memory accepted buffer bind\\n");
                    return 13;
                }}
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stderr)


    def test_pipeline_robustness_create_info_is_default_only(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            int main(void) {{
                VkPipelineRobustnessCreateInfo robustness;
                VkPipelineCreationFeedback feedback;
                VkPipelineCreationFeedbackCreateInfo feedback_info;
                memset(&robustness, 0, sizeof(robustness));
                memset(&feedback, 0xff, sizeof(feedback));
                memset(&feedback_info, 0, sizeof(feedback_info));
                robustness.sType = VK_STRUCTURE_TYPE_PIPELINE_ROBUSTNESS_CREATE_INFO;
                robustness.storageBuffers = VK_PIPELINE_ROBUSTNESS_BUFFER_BEHAVIOR_DEVICE_DEFAULT;
                robustness.uniformBuffers = VK_PIPELINE_ROBUSTNESS_BUFFER_BEHAVIOR_DEVICE_DEFAULT;
                robustness.vertexInputs = VK_PIPELINE_ROBUSTNESS_BUFFER_BEHAVIOR_DEVICE_DEFAULT;
                robustness.images = VK_PIPELINE_ROBUSTNESS_IMAGE_BEHAVIOR_DEVICE_DEFAULT;
                uint64_t feedback_extension_mask = 0;

            #ifdef VK_EXT_PIPELINE_CREATION_FEEDBACK_EXTENSION_NAME
                if (!device_extension_advertised_name(VK_EXT_PIPELINE_CREATION_FEEDBACK_EXTENSION_NAME)) {{
                    fprintf(stderr, "VK_EXT_pipeline_creation_feedback was not advertised\\n");
                    return 7;
                }}
                const char *enabled_extensions[] = {{ VK_EXT_PIPELINE_CREATION_FEEDBACK_EXTENSION_NAME }};
                VkDeviceCreateInfo extension_info;
                memset(&extension_info, 0, sizeof(extension_info));
                extension_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                extension_info.enabledExtensionCount = 1;
                extension_info.ppEnabledExtensionNames = enabled_extensions;
                if (validate_device_extensions(&extension_info) != VK_SUCCESS) {{
                    fprintf(stderr, "VK_EXT_pipeline_creation_feedback extension enable was rejected\\n");
                    return 8;
                }}
                feedback_extension_mask = enabled_device_extension_mask_from_create_info(&extension_info);
            #endif

                if (validate_and_fill_pipeline_feedback_pnext(
                        "unit-pipeline-robustness", &robustness, 1u, false, 0) != VK_SUCCESS) {{
                    fprintf(stderr, "default pipeline robustness was rejected\\n");
                    return 2;
                }}
                if (validate_pipeline_shader_stage_pnext_for_transport(
                        "unit-stage-robustness", &robustness) != VK_SUCCESS) {{
                    fprintf(stderr, "default stage pipeline robustness was rejected\\n");
                    return 12;
                }}
                robustness.storageBuffers = VK_PIPELINE_ROBUSTNESS_BUFFER_BEHAVIOR_DISABLED;
                if (validate_and_fill_pipeline_feedback_pnext(
                        "unit-pipeline-robustness", &robustness, 1u, false, 0) == VK_SUCCESS) {{
                    fprintf(stderr, "non-default storage robustness was accepted\\n");
                    return 3;
                }}
                if (validate_pipeline_shader_stage_pnext_for_transport(
                        "unit-stage-robustness", &robustness) == VK_SUCCESS) {{
                    fprintf(stderr, "non-default stage storage robustness was accepted\\n");
                    return 13;
                }}
                robustness.storageBuffers = VK_PIPELINE_ROBUSTNESS_BUFFER_BEHAVIOR_DEVICE_DEFAULT;
                robustness.images = VK_PIPELINE_ROBUSTNESS_IMAGE_BEHAVIOR_ROBUST_IMAGE_ACCESS;
                if (validate_and_fill_pipeline_feedback_pnext(
                        "unit-pipeline-robustness", &robustness, 1u, true, 0) == VK_SUCCESS) {{
                    fprintf(stderr, "non-default image robustness was accepted\\n");
                    return 4;
                }}
                robustness.images = VK_PIPELINE_ROBUSTNESS_IMAGE_BEHAVIOR_DEVICE_DEFAULT;
                feedback_info.sType = VK_STRUCTURE_TYPE_PIPELINE_CREATION_FEEDBACK_CREATE_INFO;
                feedback_info.pPipelineCreationFeedback = &feedback;
                feedback_info.pNext = &robustness;
                if (validate_and_fill_pipeline_feedback_pnext(
                        "unit-pipeline-robustness", &feedback_info, 1u, false, 0) == VK_SUCCESS) {{
                    fprintf(stderr, "feedback pNext was accepted without enabling its extension\\n");
                    return 14;
                }}
                if (validate_and_fill_pipeline_feedback_pnext(
                        "unit-pipeline-robustness", &feedback_info, 1u, false, feedback_extension_mask) != VK_SUCCESS) {{
                    fprintf(stderr, "feedback plus default robustness was rejected\\n");
                    return 5;
                }}
                if ((feedback.flags & VK_PIPELINE_CREATION_FEEDBACK_VALID_BIT) == 0 ||
                    feedback.duration != 0) {{
                    fprintf(stderr, "pipeline feedback was not preserved through robustness chain\\n");
                    return 6;
                }}

                VkPipelineCreationFeedback stage_feedback[2];
                memset(&feedback, 0xff, sizeof(feedback));
                memset(stage_feedback, 0xff, sizeof(stage_feedback));
                memset(&feedback_info, 0, sizeof(feedback_info));
                feedback_info.sType = VK_STRUCTURE_TYPE_PIPELINE_CREATION_FEEDBACK_CREATE_INFO;
                feedback_info.pPipelineCreationFeedback = &feedback;
                feedback_info.pipelineStageCreationFeedbackCount = 1;
                feedback_info.pPipelineStageCreationFeedbacks = stage_feedback;
                if (validate_and_fill_pipeline_feedback_pnext(
                        "unit-pipeline-feedback", &feedback_info, 1u, false, feedback_extension_mask) != VK_SUCCESS) {{
                    fprintf(stderr, "pipeline feedback valid stage count was rejected\\n");
                    return 9;
                }}
                if ((feedback.flags & VK_PIPELINE_CREATION_FEEDBACK_VALID_BIT) == 0 ||
                    feedback.duration != 0 ||
                    (stage_feedback[0].flags & VK_PIPELINE_CREATION_FEEDBACK_VALID_BIT) == 0 ||
                    stage_feedback[0].duration != 0) {{
                    fprintf(stderr, "pipeline feedback was not filled with valid zero-duration metadata\\n");
                    return 10;
                }}
                feedback_info.pipelineStageCreationFeedbackCount = 2;
                if (validate_and_fill_pipeline_feedback_pnext(
                        "unit-pipeline-feedback", &feedback_info, 1u, false, feedback_extension_mask) == VK_SUCCESS) {{
                    fprintf(stderr, "pipeline feedback mismatched stage count was accepted\\n");
                    return 11;
                }}
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stderr)


    def test_robustness_properties_are_queryable_without_feature_promotion(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            int main(void) {{
                VkPhysicalDeviceProperties2 properties2;
                VkPhysicalDeviceRobustness2PropertiesEXT robustness2;
                VkPhysicalDevicePipelineRobustnessProperties pipeline;
                memset(&properties2, 0, sizeof(properties2));
                memset(&robustness2, 0xff, sizeof(robustness2));
                memset(&pipeline, 0xff, sizeof(pipeline));
                properties2.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2;
                properties2.pNext = &robustness2;
                robustness2.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_ROBUSTNESS_2_PROPERTIES_EXT;
                robustness2.pNext = &pipeline;
                pipeline.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PIPELINE_ROBUSTNESS_PROPERTIES;
                pipeline.pNext = NULL;

                vkGetPhysicalDeviceProperties2((VkPhysicalDevice)physical_device_for_instance(NULL), &properties2);
                if (robustness2.pNext != &pipeline || pipeline.pNext != NULL) {{
                    fprintf(stderr, "robustness properties pNext chain was not preserved\\n");
                    return 2;
                }}
                if (robustness2.robustStorageBufferAccessSizeAlignment != 1 ||
                    robustness2.robustUniformBufferAccessSizeAlignment != 1) {{
                    fprintf(stderr, "robustness2 alignments were not conservative one-byte values\\n");
                    return 3;
                }}
                if (pipeline.defaultRobustnessStorageBuffers != VK_PIPELINE_ROBUSTNESS_BUFFER_BEHAVIOR_DEVICE_DEFAULT ||
                    pipeline.defaultRobustnessUniformBuffers != VK_PIPELINE_ROBUSTNESS_BUFFER_BEHAVIOR_DEVICE_DEFAULT ||
                    pipeline.defaultRobustnessVertexInputs != VK_PIPELINE_ROBUSTNESS_BUFFER_BEHAVIOR_DEVICE_DEFAULT ||
                    pipeline.defaultRobustnessImages != VK_PIPELINE_ROBUSTNESS_IMAGE_BEHAVIOR_DEVICE_DEFAULT) {{
                    fprintf(stderr, "pipeline robustness defaults were not DEVICE_DEFAULT\\n");
                    return 4;
                }}
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stderr)


    def test_standalone_core_feature_structs_are_queryable_and_fail_closed(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            int main(void) {{
                VkPhysicalDeviceMultiviewFeatures multiview;
                VkPhysicalDeviceVariablePointersFeatures variable_pointers;
                VkPhysicalDeviceProtectedMemoryFeatures protected_memory;
                VkPhysicalDeviceShaderDrawParametersFeatures shader_draw;
                VkPhysicalDeviceShaderAtomicInt64Features atomic64;
                VkPhysicalDeviceImagelessFramebufferFeatures imageless;
                VkDeviceCreateInfo create_info;

                memset(&multiview, 0xff, sizeof(multiview));
                memset(&variable_pointers, 0xff, sizeof(variable_pointers));
                memset(&protected_memory, 0xff, sizeof(protected_memory));
                memset(&shader_draw, 0xff, sizeof(shader_draw));
                memset(&atomic64, 0xff, sizeof(atomic64));
                memset(&imageless, 0xff, sizeof(imageless));
                multiview.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MULTIVIEW_FEATURES;
                variable_pointers.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VARIABLE_POINTERS_FEATURES;
                protected_memory.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROTECTED_MEMORY_FEATURES;
                shader_draw.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SHADER_DRAW_PARAMETERS_FEATURES;
                atomic64.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SHADER_ATOMIC_INT64_FEATURES;
                imageless.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_IMAGELESS_FRAMEBUFFER_FEATURES;
                multiview.pNext = &variable_pointers;
                variable_pointers.pNext = &protected_memory;
                protected_memory.pNext = &shader_draw;
                shader_draw.pNext = &atomic64;
                atomic64.pNext = &imageless;
                imageless.pNext = NULL;

                fill_pnext_features(&multiview);
                if (multiview.pNext != &variable_pointers || variable_pointers.pNext != &protected_memory ||
                    protected_memory.pNext != &shader_draw || shader_draw.pNext != &atomic64 ||
                    atomic64.pNext != &imageless || imageless.pNext != NULL) {{
                    fprintf(stderr, "standalone feature pNext chain was not preserved\\n");
                    return 2;
                }}
                if (multiview.multiview != VK_FALSE ||
                    multiview.multiviewGeometryShader != VK_FALSE ||
                    multiview.multiviewTessellationShader != VK_FALSE ||
                    variable_pointers.variablePointersStorageBuffer != VK_FALSE ||
                    variable_pointers.variablePointers != VK_FALSE ||
                    protected_memory.protectedMemory != VK_FALSE ||
                    shader_draw.shaderDrawParameters != VK_FALSE ||
                    atomic64.shaderBufferInt64Atomics != VK_FALSE ||
                    atomic64.shaderSharedInt64Atomics != VK_FALSE ||
                    imageless.imagelessFramebuffer != VK_FALSE) {{
                    fprintf(stderr, "standalone features were not conservatively zero-filled\\n");
                    return 3;
                }}

                memset(&create_info, 0, sizeof(create_info));
                create_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                create_info.pNext = &multiview;
                if (validate_device_feature_requests(&create_info) != VK_SUCCESS) {{
                    fprintf(stderr, "all-false standalone feature chain was rejected\\n");
                    return 4;
                }}

            #ifdef VK_KHR_VARIABLE_POINTERS_EXTENSION_NAME
                const char *variable_pointer_extensions[] = {{ VK_KHR_VARIABLE_POINTERS_EXTENSION_NAME }};
                create_info.enabledExtensionCount = 1;
                create_info.ppEnabledExtensionNames = variable_pointer_extensions;
                if (device_extension_advertised_name(VK_KHR_VARIABLE_POINTERS_EXTENSION_NAME) ||
                    validate_device_extensions(&create_info) != VK_ERROR_EXTENSION_NOT_PRESENT) {{
                    fprintf(stderr, "VK_KHR_variable_pointers was accepted without transport\\n");
                    return 11;
                }}
            #endif
            #ifdef VK_KHR_SHADER_DRAW_PARAMETERS_EXTENSION_NAME
                const char *shader_draw_extensions[] = {{ VK_KHR_SHADER_DRAW_PARAMETERS_EXTENSION_NAME }};
                create_info.enabledExtensionCount = 1;
                create_info.ppEnabledExtensionNames = shader_draw_extensions;
                if (device_extension_advertised_name(VK_KHR_SHADER_DRAW_PARAMETERS_EXTENSION_NAME) ||
                    validate_device_extensions(&create_info) != VK_ERROR_EXTENSION_NOT_PRESENT) {{
                    fprintf(stderr, "VK_KHR_shader_draw_parameters was accepted without transport\\n");
                    return 12;
                }}
            #endif
            #ifdef VK_KHR_SHADER_ATOMIC_INT64_EXTENSION_NAME
                const char *atomic64_extensions[] = {{ VK_KHR_SHADER_ATOMIC_INT64_EXTENSION_NAME }};
                create_info.enabledExtensionCount = 1;
                create_info.ppEnabledExtensionNames = atomic64_extensions;
                if (device_extension_advertised_name(VK_KHR_SHADER_ATOMIC_INT64_EXTENSION_NAME) ||
                    validate_device_extensions(&create_info) != VK_ERROR_EXTENSION_NOT_PRESENT) {{
                    fprintf(stderr, "VK_KHR_shader_atomic_int64 was accepted without transport\\n");
                    return 13;
                }}
            #endif
            #ifdef VK_KHR_IMAGELESS_FRAMEBUFFER_EXTENSION_NAME
                const char *imageless_extensions[] = {{ VK_KHR_IMAGELESS_FRAMEBUFFER_EXTENSION_NAME }};
                create_info.enabledExtensionCount = 1;
                create_info.ppEnabledExtensionNames = imageless_extensions;
                if (device_extension_advertised_name(VK_KHR_IMAGELESS_FRAMEBUFFER_EXTENSION_NAME) ||
                    validate_device_extensions(&create_info) != VK_ERROR_EXTENSION_NOT_PRESENT) {{
                    fprintf(stderr, "VK_KHR_imageless_framebuffer was accepted without transport\\n");
                    return 14;
                }}
            #endif
                create_info.enabledExtensionCount = 0;
                create_info.ppEnabledExtensionNames = NULL;

                multiview.multiview = VK_TRUE;
                if (validate_device_feature_requests(&create_info) == VK_SUCCESS) {{
                    fprintf(stderr, "offline multiview=true standalone feature was accepted\\n");
                    return 5;
                }}
                multiview.multiview = VK_FALSE;
                variable_pointers.variablePointers = VK_TRUE;
                if (validate_device_feature_requests(&create_info) == VK_SUCCESS) return 6;
                variable_pointers.variablePointers = VK_FALSE;
                protected_memory.protectedMemory = VK_TRUE;
                if (validate_device_feature_requests(&create_info) == VK_SUCCESS) return 7;
                protected_memory.protectedMemory = VK_FALSE;
                shader_draw.shaderDrawParameters = VK_TRUE;
                if (validate_device_feature_requests(&create_info) == VK_SUCCESS) return 8;
                shader_draw.shaderDrawParameters = VK_FALSE;
                atomic64.shaderBufferInt64Atomics = VK_TRUE;
                if (validate_device_feature_requests(&create_info) == VK_SUCCESS) return 9;
                atomic64.shaderBufferInt64Atomics = VK_FALSE;
                imageless.imagelessFramebuffer = VK_TRUE;
                if (validate_device_feature_requests(&create_info) == VK_SUCCESS) return 10;
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stderr)


    def test_shader_demote_feature_is_false_only_and_not_advertised_without_transport(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            int main(void) {{
                VkPhysicalDeviceShaderDemoteToHelperInvocationFeatures demote_features;
                memset(&demote_features, 0xff, sizeof(demote_features));
                demote_features.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SHADER_DEMOTE_TO_HELPER_INVOCATION_FEATURES;
                demote_features.pNext = NULL;
                fill_pnext_features(&demote_features);
                if (demote_features.sType != VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SHADER_DEMOTE_TO_HELPER_INVOCATION_FEATURES) {{
                    fprintf(stderr, "shader demote feature sType was not preserved\\n");
                    return 2;
                }}
                if (demote_features.pNext != NULL) {{
                    fprintf(stderr, "shader demote feature pNext was not preserved\\n");
                    return 3;
                }}
                if (demote_features.shaderDemoteToHelperInvocation != VK_FALSE) {{
                    fprintf(stderr, "shaderDemoteToHelperInvocation was advertised without shader-demote support\\n");
                    return 4;
                }}

                VkDeviceCreateInfo create_info;
                memset(&create_info, 0, sizeof(create_info));
                create_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                create_info.pNext = &demote_features;

            #ifdef VK_EXT_SHADER_DEMOTE_TO_HELPER_INVOCATION_EXTENSION_NAME
                if (device_extension_advertised_name(VK_EXT_SHADER_DEMOTE_TO_HELPER_INVOCATION_EXTENSION_NAME)) {{
                    fprintf(stderr, "VK_EXT_shader_demote_to_helper_invocation was advertised without transport\\n");
                    return 7;
                }}
                uint32_t extension_count = 64;
                VkExtensionProperties extensions[64];
                memset(extensions, 0, sizeof(extensions));
                if (vkEnumerateDeviceExtensionProperties(
                        (VkPhysicalDevice)physical_device_for_instance(NULL), NULL, &extension_count, extensions) != VK_SUCCESS) {{
                    fprintf(stderr, "device extension enumeration failed\\n");
                    return 8;
                }}
                for (uint32_t i = 0; i < extension_count; ++i) {{
                    if (strcmp(extensions[i].extensionName,
                               VK_EXT_SHADER_DEMOTE_TO_HELPER_INVOCATION_EXTENSION_NAME) == 0) {{
                        fprintf(stderr, "VK_EXT_shader_demote_to_helper_invocation appeared in enumeration without transport\\n");
                        return 9;
                    }}
                }}
                const char *enabled_extensions[] = {{
                    VK_EXT_SHADER_DEMOTE_TO_HELPER_INVOCATION_EXTENSION_NAME,
                }};
                create_info.enabledExtensionCount = 1;
                create_info.ppEnabledExtensionNames = enabled_extensions;
                if (validate_device_extensions(&create_info) != VK_ERROR_EXTENSION_NOT_PRESENT) {{
                    fprintf(stderr, "VK_EXT_shader_demote_to_helper_invocation extension enable was accepted without transport\\n");
                    return 10;
                }}
                create_info.enabledExtensionCount = 0;
                create_info.ppEnabledExtensionNames = NULL;
            #endif

                demote_features.shaderDemoteToHelperInvocation = VK_TRUE;
                if (validate_device_feature_requests(&create_info) == VK_SUCCESS) {{
                    fprintf(stderr, "shaderDemoteToHelperInvocation=true was accepted\\n");
                    return 11;
                }}
                demote_features.shaderDemoteToHelperInvocation = VK_FALSE;
                if (validate_device_feature_requests(&create_info) != VK_SUCCESS) {{
                    fprintf(stderr, "shaderDemoteToHelperInvocation=false was rejected\\n");
                    return 12;
                }}
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stderr)


    def test_dynamic_rendering_local_read_feature_is_false_only_and_not_advertised_without_transport(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            int main(void) {{
                VkPhysicalDeviceDynamicRenderingLocalReadFeatures local_read;
                memset(&local_read, 0xff, sizeof(local_read));
                local_read.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DYNAMIC_RENDERING_LOCAL_READ_FEATURES;
                local_read.pNext = NULL;
                fill_pnext_features(&local_read);
                if (local_read.sType != VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DYNAMIC_RENDERING_LOCAL_READ_FEATURES) {{
                    return 2;
                }}
                if (local_read.pNext != NULL) {{
                    return 3;
                }}
                if (local_read.dynamicRenderingLocalRead != VK_FALSE) {{
                    return 4;
                }}

                VkDeviceCreateInfo create_info;
                memset(&create_info, 0, sizeof(create_info));
                create_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                create_info.pNext = &local_read;

            #ifdef VK_KHR_DYNAMIC_RENDERING_LOCAL_READ_EXTENSION_NAME
                const char *enabled_extensions[] = {{
                    VK_KHR_DYNAMIC_RENDERING_LOCAL_READ_EXTENSION_NAME,
                }};
                create_info.enabledExtensionCount = 1;
                create_info.ppEnabledExtensionNames = enabled_extensions;
                if (device_extension_advertised_name(VK_KHR_DYNAMIC_RENDERING_LOCAL_READ_EXTENSION_NAME)) {{
                    fprintf(stderr, "VK_KHR_dynamic_rendering_local_read was advertised without local-read transport\\n");
                    return 5;
                }}
                uint32_t extension_count = 64;
                VkExtensionProperties extensions[64];
                memset(extensions, 0, sizeof(extensions));
                if (vkEnumerateDeviceExtensionProperties(
                        (VkPhysicalDevice)physical_device_for_instance(NULL), NULL, &extension_count, extensions) != VK_SUCCESS) {{
                    fprintf(stderr, "device extension enumeration failed\\n");
                    return 6;
                }}
                for (uint32_t i = 0; i < extension_count; ++i) {{
                    if (strcmp(extensions[i].extensionName,
                               VK_KHR_DYNAMIC_RENDERING_LOCAL_READ_EXTENSION_NAME) == 0) {{
                        fprintf(stderr, "VK_KHR_dynamic_rendering_local_read appeared in enumeration without transport\\n");
                        return 7;
                    }}
                }}
                if (validate_device_extensions(&create_info) != VK_ERROR_EXTENSION_NOT_PRESENT) {{
                    fprintf(stderr, "VK_KHR_dynamic_rendering_local_read extension enable was accepted without transport\\n");
                    return 8;
                }}
                create_info.enabledExtensionCount = 0;
                create_info.ppEnabledExtensionNames = NULL;
            #endif

                local_read.dynamicRenderingLocalRead = VK_TRUE;
                if (validate_device_feature_requests(&create_info) == VK_SUCCESS) {{
                    return 11;
                }}
                local_read.dynamicRenderingLocalRead = VK_FALSE;
                if (validate_device_feature_requests(&create_info) != VK_SUCCESS) {{
                    return 12;
                }}
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_maintenance5_and_vulkan13_feature_pnext_are_false_only(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            int main(void) {{
            #ifdef VK_KHR_MAINTENANCE_5_EXTENSION_NAME
                if (!device_extension_advertised_name(VK_KHR_MAINTENANCE_5_EXTENSION_NAME)) {{
                    fprintf(stderr, "maintenance5 extension was not advertised\\n");
                    return 2;
                }}
                VkPhysicalDeviceMaintenance5Features maintenance5;
                memset(&maintenance5, 0xff, sizeof(maintenance5));
                maintenance5.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MAINTENANCE_5_FEATURES;
                maintenance5.pNext = NULL;
                fill_pnext_features(&maintenance5);
                if (maintenance5.sType != VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MAINTENANCE_5_FEATURES ||
                    maintenance5.pNext != NULL || maintenance5.maintenance5 != VK_FALSE) {{
                    fprintf(stderr, "maintenance5 query was not false-only\\n");
                    return 3;
                }}
                VkDeviceCreateInfo create_info;
                memset(&create_info, 0, sizeof(create_info));
                create_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                create_info.pNext = &maintenance5;
                maintenance5.maintenance5 = VK_TRUE;
                if (validate_device_feature_requests(&create_info) == VK_SUCCESS) {{
                    fprintf(stderr, "maintenance5=true was accepted\\n");
                    return 4;
                }}
                maintenance5.maintenance5 = VK_FALSE;
                if (validate_device_feature_requests(&create_info) != VK_SUCCESS) {{
                    fprintf(stderr, "maintenance5=false was rejected\\n");
                    return 5;
                }}
            #endif
            #ifdef VK_VERSION_1_3
                VkPhysicalDeviceVulkan13Features vulkan13;
                memset(&vulkan13, 0xff, sizeof(vulkan13));
                vulkan13.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES;
                vulkan13.pNext = NULL;
                fill_pnext_features(&vulkan13);
                if (vulkan13.sType != VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES ||
                    vulkan13.pNext != NULL ||
                    vulkan13.robustImageAccess != VK_FALSE ||
                    vulkan13.inlineUniformBlock != VK_FALSE ||
                    vulkan13.descriptorBindingInlineUniformBlockUpdateAfterBind != VK_FALSE ||
                    vulkan13.pipelineCreationCacheControl != VK_FALSE ||
                    vulkan13.privateData != VK_FALSE ||
                    vulkan13.shaderDemoteToHelperInvocation != VK_FALSE ||
                    vulkan13.shaderTerminateInvocation != VK_FALSE ||
                    vulkan13.subgroupSizeControl != VK_FALSE ||
                    vulkan13.computeFullSubgroups != VK_FALSE ||
                    vulkan13.synchronization2 != VK_FALSE ||
                    vulkan13.textureCompressionASTC_HDR != VK_FALSE ||
                    vulkan13.shaderZeroInitializeWorkgroupMemory != VK_FALSE ||
                    vulkan13.dynamicRendering != VK_FALSE ||
                    vulkan13.shaderIntegerDotProduct != VK_FALSE ||
                    vulkan13.maintenance4 != VK_FALSE) {{
                    fprintf(stderr, "vulkan13 feature aggregate query was not all-false\\n");
                    return 6;
                }}
                VkDeviceCreateInfo create_info13;
                memset(&create_info13, 0, sizeof(create_info13));
                create_info13.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                create_info13.pNext = &vulkan13;
                vulkan13.synchronization2 = VK_TRUE;
                if (validate_device_feature_requests(&create_info13) == VK_SUCCESS) {{
                    fprintf(stderr, "vulkan13 synchronization2=true was accepted through aggregate\\n");
                    return 7;
                }}
                vulkan13.synchronization2 = VK_FALSE;
                if (validate_device_feature_requests(&create_info13) != VK_SUCCESS) {{
                    fprintf(stderr, "all-false vulkan13 aggregate was rejected\\n");
                    return 8;
                }}
            #endif
            #ifdef VK_VERSION_1_4
                VkPhysicalDeviceVulkan14Features vulkan14;
                memset(&vulkan14, 0xff, sizeof(vulkan14));
                vulkan14.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_4_FEATURES;
                vulkan14.pNext = NULL;
                fill_pnext_features(&vulkan14);
                if (vulkan14.sType != VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_4_FEATURES ||
                    vulkan14.pNext != NULL ||
                    vulkan14.globalPriorityQuery != VK_FALSE ||
                    vulkan14.shaderSubgroupRotate != VK_FALSE ||
                    vulkan14.shaderSubgroupRotateClustered != VK_FALSE ||
                    vulkan14.shaderFloatControls2 != VK_FALSE ||
                    vulkan14.shaderExpectAssume != VK_FALSE ||
                    vulkan14.rectangularLines != VK_FALSE ||
                    vulkan14.bresenhamLines != VK_FALSE ||
                    vulkan14.smoothLines != VK_FALSE ||
                    vulkan14.stippledRectangularLines != VK_FALSE ||
                    vulkan14.stippledBresenhamLines != VK_FALSE ||
                    vulkan14.stippledSmoothLines != VK_FALSE ||
                    vulkan14.vertexAttributeInstanceRateDivisor != VK_FALSE ||
                    vulkan14.vertexAttributeInstanceRateZeroDivisor != VK_FALSE ||
                    vulkan14.indexTypeUint8 != VK_FALSE ||
                    vulkan14.dynamicRenderingLocalRead != VK_FALSE ||
                    vulkan14.maintenance5 != VK_FALSE ||
                    vulkan14.maintenance6 != VK_FALSE ||
                    vulkan14.pipelineProtectedAccess != VK_FALSE ||
                    vulkan14.pipelineRobustness != VK_FALSE ||
                    vulkan14.hostImageCopy != VK_FALSE ||
                    vulkan14.pushDescriptor != VK_FALSE) {{
                    fprintf(stderr, "vulkan14 feature aggregate query was not all-false\\n");
                    return 9;
                }}
                VkDeviceCreateInfo create_info14;
                memset(&create_info14, 0, sizeof(create_info14));
                create_info14.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                create_info14.pNext = &vulkan14;
                vulkan14.maintenance5 = VK_TRUE;
                if (validate_device_feature_requests(&create_info14) == VK_SUCCESS) {{
                    fprintf(stderr, "vulkan14 maintenance5=true was accepted through aggregate\\n");
                    return 10;
                }}
                vulkan14.maintenance5 = VK_FALSE;
                if (validate_device_feature_requests(&create_info14) != VK_SUCCESS) {{
                    fprintf(stderr, "all-false vulkan14 aggregate was rejected\\n");
                    return 11;
                }}
            #endif
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_dynamic_rendering_attachment_location_identity_is_noop_only(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            int main(void) {{
                VkRenderingInfo info;
                VkRenderingAttachmentLocationInfo locations;
                uint32_t identity_locations[2] = {{0u, 1u}};
                uint32_t remap_locations[2] = {{1u, 0u}};
                memset(&info, 0, sizeof(info));
                memset(&locations, 0, sizeof(locations));
                info.sType = VK_STRUCTURE_TYPE_RENDERING_INFO;
                info.colorAttachmentCount = 2;
                info.pNext = &locations;
                locations.sType = VK_STRUCTURE_TYPE_RENDERING_ATTACHMENT_LOCATION_INFO;

                locations.colorAttachmentCount = 0;
                locations.pColorAttachmentLocations = remap_locations;
                if (!rendering_info_pnext_noop(&info)) return 2;

                locations.colorAttachmentCount = 2;
                locations.pColorAttachmentLocations = identity_locations;
                if (!rendering_info_pnext_noop(&info)) return 3;

                locations.pColorAttachmentLocations = remap_locations;
                if (rendering_info_pnext_noop(&info)) return 4;

                locations.colorAttachmentCount = 1;
                locations.pColorAttachmentLocations = identity_locations;
                if (rendering_info_pnext_noop(&info)) return 5;

                locations.colorAttachmentCount = 2;
                locations.pColorAttachmentLocations = NULL;
                if (rendering_info_pnext_noop(&info)) return 6;

                VkRenderingInputAttachmentIndexInfo indices;
                uint32_t input_indices[2] = {{0u, 1u}};
                memset(&indices, 0, sizeof(indices));
                indices.sType = VK_STRUCTURE_TYPE_RENDERING_INPUT_ATTACHMENT_INDEX_INFO;
                indices.colorAttachmentCount = 2;
                indices.pColorAttachmentInputIndices = input_indices;
                info.pNext = &indices;
                if (rendering_info_pnext_noop(&info)) return 7;

                indices.colorAttachmentCount = 0;
                indices.pColorAttachmentInputIndices = input_indices;
                if (!rendering_info_pnext_noop(&info)) return 8;
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_device_extension_collector_drives_enumeration_and_validation(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            int main(void) {{
                uint32_t count = 0;
                if (vkEnumerateDeviceExtensionProperties((VkPhysicalDevice)physical_device_for_instance(NULL), NULL, &count, NULL) != VK_SUCCESS) {{
                    return 2;
                }}
                if (count == 0 || count > PDOCKER_VK_MAX_DEVICE_EXTENSIONS) {{
                    fprintf(stderr, "unexpected extension count %u\\n", count);
                    return 3;
                }}
                if (count > 1) {{
                    VkExtensionProperties one_property[1];
                    uint32_t one_capacity = 1;
                    if (vkEnumerateDeviceExtensionProperties((VkPhysicalDevice)physical_device_for_instance(NULL), NULL, &one_capacity, one_property) != VK_INCOMPLETE ||
                        one_capacity != 1) {{
                        fprintf(stderr, "truncated device extension enumeration did not return VK_INCOMPLETE\\n");
                        return 11;
                    }}
                }}
                VkExtensionProperties properties[PDOCKER_VK_MAX_DEVICE_EXTENSIONS];
                memset(properties, 0, sizeof(properties));
                uint32_t capacity = PDOCKER_VK_MAX_DEVICE_EXTENSIONS;
                if (vkEnumerateDeviceExtensionProperties((VkPhysicalDevice)physical_device_for_instance(NULL), NULL, &capacity, properties) != VK_SUCCESS) {{
                    return 4;
                }}
                if (capacity != count) {{
                    fprintf(stderr, "enumerated capacity %u != count %u\\n", capacity, count);
                    return 5;
                }}
                const char *enabled[PDOCKER_VK_MAX_DEVICE_EXTENSIONS];
                int has_storage8 = 0;
                int has_float16_int8 = 0;
                for (uint32_t i = 0; i < capacity; ++i) {{
                    if (!device_extension_advertised_name(properties[i].extensionName)) {{
                        fprintf(stderr, "enumerated extension was not accepted: %s\\n", properties[i].extensionName);
                        return 6;
                    }}
                    enabled[i] = properties[i].extensionName;
                    if (strcmp(properties[i].extensionName, VK_KHR_8BIT_STORAGE_EXTENSION_NAME) == 0) {{
                        has_storage8 = 1;
                    }}
                    if (strcmp(properties[i].extensionName, VK_KHR_SHADER_FLOAT16_INT8_EXTENSION_NAME) == 0) {{
                        has_float16_int8 = 1;
                    }}
                }}
                VkDeviceCreateInfo create_info;
                memset(&create_info, 0, sizeof(create_info));
                create_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                create_info.enabledExtensionCount = capacity;
                create_info.ppEnabledExtensionNames = enabled;
                if (validate_device_extensions(&create_info) != VK_SUCCESS) {{
                    fprintf(stderr, "validate_device_extensions rejected its own enumerated list\\n");
                    return 7;
                }}
                const char *bad_enabled[] = {{ "VK_SKYDNIR_not_advertised_test_extension" }};
                create_info.enabledExtensionCount = 1;
                create_info.ppEnabledExtensionNames = bad_enabled;
                if (validate_device_extensions(&create_info) != VK_ERROR_EXTENSION_NOT_PRESENT) {{
                    fprintf(stderr, "validate_device_extensions accepted a non-advertised extension\\n");
                    return 8;
                }}
            #ifdef VK_KHR_MAINTENANCE_5_EXTENSION_NAME
                if (!device_extension_advertised_name(VK_KHR_MAINTENANCE_5_EXTENSION_NAME)) {{
                    fprintf(stderr, "maintenance5 should be advertised for implemented query/bind aliases\\n");
                    return 9;
                }}
            #endif
                if (!has_storage8 || !has_float16_int8) {{
                    fprintf(stderr,
                            "missing extension storage8=%d float16_int8=%d count=%u\\n",
                            has_storage8,
                            has_float16_int8,
                            capacity);
                    return 10;
                }}
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stderr)


    def test_image_format_properties2_allows_noop_external_image_format_info(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            int main(void) {{
                setenv("PDOCKER_VULKAN_HEAP_BYTES", "2147483648", 1);
                setenv("PDOCKER_VULKAN_MAX_BUFFER_BYTES", "2147483648", 1);

                VkPhysicalDevice physical = (VkPhysicalDevice)physical_device_for_instance(NULL);

                VkPhysicalDeviceImageFormatInfo2 info;
                VkPhysicalDeviceExternalImageFormatInfo external_info;
                memset(&info, 0, sizeof(info));
                memset(&external_info, 0, sizeof(external_info));
                info.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_IMAGE_FORMAT_INFO_2;
                info.format = VK_FORMAT_R8G8B8A8_UNORM;
                info.type = VK_IMAGE_TYPE_2D;
                info.tiling = VK_IMAGE_TILING_OPTIMAL;
                info.usage = VK_IMAGE_USAGE_SAMPLED_BIT;
                external_info.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_EXTERNAL_IMAGE_FORMAT_INFO;
                external_info.handleType = (VkExternalMemoryHandleTypeFlagBits)0;
                info.pNext = &external_info;

                VkImageFormatProperties2 properties;
                VkExternalImageFormatProperties external_properties;
                memset(&properties, 0, sizeof(properties));
                memset(&external_properties, 0xff, sizeof(external_properties));
                properties.sType = VK_STRUCTURE_TYPE_IMAGE_FORMAT_PROPERTIES_2;
                properties.pNext = &external_properties;
                external_properties.sType = VK_STRUCTURE_TYPE_EXTERNAL_IMAGE_FORMAT_PROPERTIES;
                external_properties.pNext = NULL;

                VkResult rc = vkGetPhysicalDeviceImageFormatProperties2(
                    physical, &info, &properties);
                if (rc != VK_SUCCESS) {{
                    fprintf(stderr, "handleType=0 external image query failed: %d\\n", rc);
                    return 2;
                }}
                if (properties.imageFormatProperties.maxExtent.width == 0 ||
                    properties.imageFormatProperties.maxMipLevels == 0 ||
                    properties.imageFormatProperties.sampleCounts == 0) {{
                    fprintf(stderr, "legacy image format properties were not populated\\n");
                    return 3;
                }}
                if (external_properties.sType != VK_STRUCTURE_TYPE_EXTERNAL_IMAGE_FORMAT_PROPERTIES ||
                    external_properties.pNext != NULL) {{
                    fprintf(stderr, "external output header was not preserved\\n");
                    return 4;
                }}
                if (external_properties.externalMemoryProperties.externalMemoryFeatures != 0 ||
                    external_properties.externalMemoryProperties.exportFromImportedHandleTypes != 0 ||
                    external_properties.externalMemoryProperties.compatibleHandleTypes != 0) {{
                    fprintf(stderr, "external output properties were not zero-filled\\n");
                    return 5;
                }}

                VkPhysicalDeviceImageViewImageFormatInfoEXT view_info;
                VkFilterCubicImageViewImageFormatPropertiesEXT cubic_props;
                memset(&view_info, 0, sizeof(view_info));
                memset(&cubic_props, 0xff, sizeof(cubic_props));
                view_info.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_IMAGE_VIEW_IMAGE_FORMAT_INFO_EXT;
                view_info.imageViewType = VK_IMAGE_VIEW_TYPE_2D;
                external_info.pNext = &view_info;
                memset(&properties, 0, sizeof(properties));
                memset(&external_properties, 0, sizeof(external_properties));
                properties.sType = VK_STRUCTURE_TYPE_IMAGE_FORMAT_PROPERTIES_2;
                properties.pNext = &external_properties;
                external_properties.sType = VK_STRUCTURE_TYPE_EXTERNAL_IMAGE_FORMAT_PROPERTIES;
                external_properties.pNext = &cubic_props;
                cubic_props.sType = VK_STRUCTURE_TYPE_FILTER_CUBIC_IMAGE_VIEW_IMAGE_FORMAT_PROPERTIES_EXT;
                cubic_props.pNext = NULL;
                rc = vkGetPhysicalDeviceImageFormatProperties2(physical, &info, &properties);
                if (rc != VK_SUCCESS) {{
                    fprintf(stderr, "filter-cubic image-view query pNext failed: %d\\n", rc);
                    return 6;
                }}
                if (cubic_props.sType != VK_STRUCTURE_TYPE_FILTER_CUBIC_IMAGE_VIEW_IMAGE_FORMAT_PROPERTIES_EXT ||
                    cubic_props.pNext != NULL || cubic_props.filterCubic != VK_FALSE ||
                    cubic_props.filterCubicMinmax != VK_FALSE) {{
                    fprintf(stderr, "filter-cubic output properties were not zero-filled\\n");
                    return 7;
                }}
                view_info.imageViewType = (VkImageViewType)0x7fffffff;
                memset(&properties, 0, sizeof(properties));
                properties.sType = VK_STRUCTURE_TYPE_IMAGE_FORMAT_PROPERTIES_2;
                rc = vkGetPhysicalDeviceImageFormatProperties2(physical, &info, &properties);
                if (rc != VK_ERROR_FORMAT_NOT_SUPPORTED) {{
                    fprintf(stderr, "invalid filter-cubic image-view query returned %d\\n", rc);
                    return 8;
                }}
                external_info.pNext = NULL;

                external_info.handleType = VK_EXTERNAL_MEMORY_HANDLE_TYPE_OPAQUE_FD_BIT;
                memset(&properties, 0, sizeof(properties));
                properties.sType = VK_STRUCTURE_TYPE_IMAGE_FORMAT_PROPERTIES_2;
                rc = vkGetPhysicalDeviceImageFormatProperties2(physical, &info, &properties);
                if (rc != VK_ERROR_FORMAT_NOT_SUPPORTED) {{
                    fprintf(stderr, "handleType!=0 external image query returned %d\\n", rc);
                    return 9;
                }}
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


    def test_buffer_live_handles_fail_closed_after_destroy(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"
            {COMMAND_BUFFER_STACK_TEST_HELPER}

            int main(void) {{
                VkBufferCreateInfo buffer_info;
                memset(&buffer_info, 0, sizeof(buffer_info));
                buffer_info.sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO;
                buffer_info.size = 4096;
                buffer_info.usage = VK_BUFFER_USAGE_STORAGE_BUFFER_BIT |
                    VK_BUFFER_USAGE_TRANSFER_SRC_BIT |
                    VK_BUFFER_USAGE_TRANSFER_DST_BIT |
                    VK_BUFFER_USAGE_UNIFORM_TEXEL_BUFFER_BIT;
                buffer_info.sharingMode = VK_SHARING_MODE_EXCLUSIVE;

                VkBuffer buffer = VK_NULL_HANDLE;
                if (vkCreateBuffer(VK_NULL_HANDLE, &buffer_info, NULL, &buffer) != VK_SUCCESS ||
                    buffer == VK_NULL_HANDLE) {{
                    fprintf(stderr, "buffer create failed\\n");
                    return 1;
                }}
                VkMemoryRequirements req;
                memset(&req, 0, sizeof(req));
                vkGetBufferMemoryRequirements(VK_NULL_HANDLE, buffer, &req);
                if (req.size == 0 || req.alignment == 0) {{
                    fprintf(stderr, "live buffer requirements were empty\\n");
                    return 2;
                }}

                VkMemoryAllocateInfo alloc;
                memset(&alloc, 0, sizeof(alloc));
                alloc.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO;
                alloc.allocationSize = req.size;
                alloc.memoryTypeIndex = 1;
                VkDeviceMemory memory = VK_NULL_HANDLE;
                if (vkAllocateMemory(VK_NULL_HANDLE, &alloc, NULL, &memory) != VK_SUCCESS ||
                    memory == VK_NULL_HANDLE) {{
                    fprintf(stderr, "memory allocation failed\\n");
                    return 3;
                }}
                if (vkBindBufferMemory(VK_NULL_HANDLE, buffer, memory, 0) != VK_SUCCESS) {{
                    fprintf(stderr, "live buffer bind failed\\n");
                    return 4;
                }}

                vkDestroyBuffer(VK_NULL_HANDLE, buffer, NULL);
                memset(&req, 0x7f, sizeof(req));
                vkGetBufferMemoryRequirements(VK_NULL_HANDLE, buffer, &req);
                if (req.size != 0) {{
                    fprintf(stderr, "stale buffer requirements exposed size=%llu\\n",
                            (unsigned long long)req.size);
                    return 5;
                }}
                if (vkBindBufferMemory(VK_NULL_HANDLE, buffer, memory, 0) == VK_SUCCESS) {{
                    fprintf(stderr, "stale buffer bind succeeded\\n");
                    return 6;
                }}

                VkBufferViewCreateInfo view_info;
                memset(&view_info, 0, sizeof(view_info));
                view_info.sType = VK_STRUCTURE_TYPE_BUFFER_VIEW_CREATE_INFO;
                view_info.buffer = buffer;
                view_info.format = VK_FORMAT_R8_UINT;
                view_info.range = VK_WHOLE_SIZE;
                VkBufferView view = VK_NULL_HANDLE;
                if (vkCreateBufferView(VK_NULL_HANDLE, &view_info, NULL, &view) == VK_SUCCESS ||
                    view != VK_NULL_HANDLE) {{
                    fprintf(stderr, "stale buffer view creation succeeded\\n");
                    return 7;
                }}

                VkMemoryDedicatedAllocateInfo dedicated;
                memset(&dedicated, 0, sizeof(dedicated));
                dedicated.sType = VK_STRUCTURE_TYPE_MEMORY_DEDICATED_ALLOCATE_INFO;
                dedicated.buffer = buffer;
                alloc.pNext = &dedicated;
                VkDeviceMemory dedicated_memory = VK_NULL_HANDLE;
                if (vkAllocateMemory(VK_NULL_HANDLE, &alloc, NULL, &dedicated_memory) == VK_SUCCESS ||
                    dedicated_memory != VK_NULL_HANDLE) {{
                    fprintf(stderr, "dedicated allocation accepted stale buffer\\n");
                    return 8;
                }}

                PdockerVkCommandBuffer cmd;
                reset_test_command_buffer(&cmd, 0, 0);
                vkCmdBindIndexBuffer((VkCommandBuffer)&cmd, buffer, 0, VK_INDEX_TYPE_UINT16);
                if (!cmd.recording_failed ||
                    strcmp(cmd.recording_failure_reason, "graphics-index-buffer-cross-device-or-invalid") != 0) {{
                    fprintf(stderr, "stale index buffer did not fail closed: %s\\n",
                            cmd.recording_failure_reason ? cmd.recording_failure_reason : "<none>");
                    return 9;
                }}
                command_buffer_destroy_record_vectors(&cmd);

                VkBuffer fake_buffer = (VkBuffer)(uintptr_t)0x1234u;
                vkDestroyBuffer(VK_NULL_HANDLE, fake_buffer, NULL);
                if (vkBindBufferMemory(VK_NULL_HANDLE, fake_buffer, memory, 0) == VK_SUCCESS) {{
                    fprintf(stderr, "fake buffer bind succeeded\\n");
                    return 10;
                }}
                vkFreeMemory(VK_NULL_HANDLE, memory, NULL);
                vkDestroyBuffer(VK_NULL_HANDLE, buffer, NULL);
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


    def test_device_buffer_requirements_allow_noop_buffer_pnext(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            static int expect_nonzero_requirements(
                    const VkBufferCreateInfo *buffer_info, int code) {{
                VkDeviceBufferMemoryRequirements info;
                VkMemoryRequirements2 requirements;
                memset(&info, 0, sizeof(info));
                memset(&requirements, 0, sizeof(requirements));
                info.sType = VK_STRUCTURE_TYPE_DEVICE_BUFFER_MEMORY_REQUIREMENTS;
                info.pCreateInfo = buffer_info;
                requirements.sType = VK_STRUCTURE_TYPE_MEMORY_REQUIREMENTS_2;
                requirements.pNext = NULL;
                vkGetDeviceBufferMemoryRequirements(
                    (VkDevice)(uintptr_t)0x1u, &info, &requirements);
                if (requirements.memoryRequirements.size == 0 ||
                    requirements.memoryRequirements.alignment == 0 ||
                    requirements.memoryRequirements.memoryTypeBits == 0) {{
                    fprintf(stderr,
                            "expected nonzero buffer requirements for case %d: size=%llu align=%llu bits=0x%x\\n",
                            code,
                            (unsigned long long)requirements.memoryRequirements.size,
                            (unsigned long long)requirements.memoryRequirements.alignment,
                            requirements.memoryRequirements.memoryTypeBits);
                    return code;
                }}
                return 0;
            }}

            static int expect_zero_requirements(
                    const VkBufferCreateInfo *buffer_info, int code) {{
                VkDeviceBufferMemoryRequirements info;
                VkMemoryRequirements2 requirements;
                memset(&info, 0, sizeof(info));
                memset(&requirements, 0xff, sizeof(requirements));
                info.sType = VK_STRUCTURE_TYPE_DEVICE_BUFFER_MEMORY_REQUIREMENTS;
                info.pCreateInfo = buffer_info;
                requirements.sType = VK_STRUCTURE_TYPE_MEMORY_REQUIREMENTS_2;
                requirements.pNext = NULL;
                vkGetDeviceBufferMemoryRequirements(
                    (VkDevice)(uintptr_t)0x1u, &info, &requirements);
                if (requirements.memoryRequirements.size != 0 ||
                    requirements.memoryRequirements.alignment != 0 ||
                    requirements.memoryRequirements.memoryTypeBits != 0) {{
                    fprintf(stderr,
                            "expected zero buffer requirements for case %d: size=%llu align=%llu bits=0x%x\\n",
                            code,
                            (unsigned long long)requirements.memoryRequirements.size,
                            (unsigned long long)requirements.memoryRequirements.alignment,
                            requirements.memoryRequirements.memoryTypeBits);
                    return code;
                }}
                return 0;
            }}

            int main(void) {{
            #ifdef VK_KHR_GET_MEMORY_REQUIREMENTS_2_EXTENSION_NAME
                if (!device_extension_advertised_name(VK_KHR_GET_MEMORY_REQUIREMENTS_2_EXTENSION_NAME)) return 20;
                if (proc_address("vkGetImageSparseMemoryRequirements2KHR") == NULL) return 21;
            #endif
            #ifdef VK_KHR_MAINTENANCE_4_EXTENSION_NAME
                if (!device_extension_advertised_name(VK_KHR_MAINTENANCE_4_EXTENSION_NAME)) return 22;
                if (proc_address("vkGetDeviceBufferMemoryRequirements") != NULL) return 23;
                if (proc_address("vkGetDeviceBufferMemoryRequirementsKHR") == NULL) return 24;
                if (proc_address("vkGetDeviceImageMemoryRequirementsKHR") == NULL) return 25;
                if (proc_address("vkGetDeviceImageSparseMemoryRequirementsKHR") == NULL) return 26;
            #endif
                VkBufferCreateInfo buffer_info;
                memset(&buffer_info, 0, sizeof(buffer_info));
                buffer_info.sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO;
                buffer_info.size = 4096;
                buffer_info.usage = VK_BUFFER_USAGE_STORAGE_BUFFER_BIT;
                buffer_info.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
                if (expect_nonzero_requirements(&buffer_info, 2)) return 2;

                VkExternalMemoryBufferCreateInfo external_info;
                memset(&external_info, 0, sizeof(external_info));
                external_info.sType = VK_STRUCTURE_TYPE_EXTERNAL_MEMORY_BUFFER_CREATE_INFO;
                external_info.handleTypes = 0;
                buffer_info.pNext = &external_info;
                if (expect_nonzero_requirements(&buffer_info, 3)) return 3;

                VkBufferUsageFlags2CreateInfo usage2_info;
                memset(&usage2_info, 0, sizeof(usage2_info));
                usage2_info.sType = VK_STRUCTURE_TYPE_BUFFER_USAGE_FLAGS_2_CREATE_INFO;
                usage2_info.usage = buffer_info.usage;
                buffer_info.pNext = &usage2_info;
                if (expect_nonzero_requirements(&buffer_info, 4)) return 4;

                buffer_info.usage = 0;
                usage2_info.usage = VK_BUFFER_USAGE_UNIFORM_BUFFER_BIT;
                if (expect_nonzero_requirements(&buffer_info, 5)) return 5;

                usage2_info.usage = 0;
                if (expect_zero_requirements(&buffer_info, 6)) return 6;

                usage2_info.usage = (VkBufferUsageFlags2)1ull << 40;
                if (expect_zero_requirements(&buffer_info, 7)) return 7;

                buffer_info.usage = VK_BUFFER_USAGE_STORAGE_BUFFER_BIT;
                external_info.handleTypes = VK_EXTERNAL_MEMORY_HANDLE_TYPE_OPAQUE_FD_BIT;
                buffer_info.pNext = &external_info;
                if (expect_zero_requirements(&buffer_info, 8)) return 8;
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


    def test_external_memory_extension_is_not_advertised_without_transport(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            static int extension_seen(
                    const VkExtensionProperties *extensions,
                    uint32_t count,
                    const char *name) {{
                for (uint32_t i = 0; i < count; ++i) {{
                    if (strcmp(extensions[i].extensionName, name) == 0) return 1;
                }}
                return 0;
            }}

            int main(void) {{
            #ifdef VK_KHR_EXTERNAL_MEMORY_EXTENSION_NAME
                if (device_extension_advertised_name(VK_KHR_EXTERNAL_MEMORY_EXTENSION_NAME)) return 2;
                uint32_t count = 64;
                VkExtensionProperties extensions[64];
                memset(extensions, 0, sizeof(extensions));
                if (vkEnumerateDeviceExtensionProperties((VkPhysicalDevice)physical_device_for_instance(NULL), NULL, &count, extensions) != VK_SUCCESS) return 3;
                if (extension_seen(extensions, count, VK_KHR_EXTERNAL_MEMORY_EXTENSION_NAME)) return 4;

                const char *enabled[] = {{ VK_KHR_EXTERNAL_MEMORY_EXTENSION_NAME }};
                VkDeviceCreateInfo device_info;
                memset(&device_info, 0, sizeof(device_info));
                device_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                device_info.enabledExtensionCount = 1;
                device_info.ppEnabledExtensionNames = enabled;
                if (validate_device_extensions(&device_info) != VK_ERROR_EXTENSION_NOT_PRESENT) return 5;

                VkBufferCreateInfo buffer_info;
                VkExternalMemoryBufferCreateInfo buffer_external;
                memset(&buffer_info, 0, sizeof(buffer_info));
                memset(&buffer_external, 0, sizeof(buffer_external));
                buffer_info.sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO;
                buffer_info.pNext = &buffer_external;
                buffer_external.sType = VK_STRUCTURE_TYPE_EXTERNAL_MEMORY_BUFFER_CREATE_INFO;
                buffer_external.handleTypes = 0;
                if (validate_buffer_create_pnext(&buffer_info) != VK_SUCCESS) return 6;
                buffer_external.handleTypes = VK_EXTERNAL_MEMORY_HANDLE_TYPE_OPAQUE_FD_BIT;
                if (validate_buffer_create_pnext(&buffer_info) != VK_ERROR_FEATURE_NOT_PRESENT) return 7;

                VkImageCreateInfo image_info;
                VkExternalMemoryImageCreateInfo image_external;
                memset(&image_info, 0, sizeof(image_info));
                memset(&image_external, 0, sizeof(image_external));
                image_info.sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO;
                image_info.pNext = &image_external;
                image_external.sType = VK_STRUCTURE_TYPE_EXTERNAL_MEMORY_IMAGE_CREATE_INFO;
                image_external.handleTypes = 0;
                if (validate_image_create_pnext_for_transport(&image_info) != VK_SUCCESS) return 8;
                image_external.handleTypes = VK_EXTERNAL_MEMORY_HANDLE_TYPE_OPAQUE_FD_BIT;
                if (validate_image_create_pnext_for_transport(&image_info) != VK_ERROR_FEATURE_NOT_PRESENT) return 9;

                VkExportMemoryAllocateInfo export_info;
                memset(&export_info, 0, sizeof(export_info));
                export_info.sType = VK_STRUCTURE_TYPE_EXPORT_MEMORY_ALLOCATE_INFO;
                export_info.handleTypes = 0;
                if (validate_memory_allocate_pnext(VK_NULL_HANDLE, &export_info) != VK_SUCCESS) return 10;
                export_info.handleTypes = VK_EXTERNAL_MEMORY_HANDLE_TYPE_OPAQUE_FD_BIT;
                if (validate_memory_allocate_pnext(VK_NULL_HANDLE, &export_info) != VK_ERROR_FEATURE_NOT_PRESENT) return 11;

                VkPhysicalDeviceExternalBufferInfo external_buffer_info;
                VkExternalBufferProperties external_properties;
                memset(&external_buffer_info, 0, sizeof(external_buffer_info));
                memset(&external_properties, 0xff, sizeof(external_properties));
                external_buffer_info.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_EXTERNAL_BUFFER_INFO;
                external_buffer_info.usage = VK_BUFFER_USAGE_STORAGE_BUFFER_BIT;
                external_buffer_info.handleType = VK_EXTERNAL_MEMORY_HANDLE_TYPE_OPAQUE_FD_BIT;
                external_properties.sType = VK_STRUCTURE_TYPE_EXTERNAL_BUFFER_PROPERTIES;
                vkGetPhysicalDeviceExternalBufferProperties(VK_NULL_HANDLE, &external_buffer_info, &external_properties);
                if (external_properties.externalMemoryProperties.externalMemoryFeatures != 0) return 12;
                if (external_properties.externalMemoryProperties.exportFromImportedHandleTypes != 0) return 13;
                if (external_properties.externalMemoryProperties.compatibleHandleTypes != 0) return 14;
            #endif
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stderr)


    def test_external_semaphore_and_fence_extensions_are_not_advertised_without_transport(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            static int extension_seen(
                    const VkExtensionProperties *extensions,
                    uint32_t count,
                    const char *name) {{
                for (uint32_t i = 0; i < count; ++i) {{
                    if (strcmp(extensions[i].extensionName, name) == 0) return 1;
                }}
                return 0;
            }}

            int main(void) {{
                uint32_t count = 64;
                VkExtensionProperties extensions[64];
                memset(extensions, 0, sizeof(extensions));
                if (vkEnumerateDeviceExtensionProperties((VkPhysicalDevice)physical_device_for_instance(NULL), NULL, &count, extensions) != VK_SUCCESS) return 2;

            #ifdef VK_KHR_EXTERNAL_SEMAPHORE_EXTENSION_NAME
                if (device_extension_advertised_name(VK_KHR_EXTERNAL_SEMAPHORE_EXTENSION_NAME)) return 3;
                if (extension_seen(extensions, count, VK_KHR_EXTERNAL_SEMAPHORE_EXTENSION_NAME)) return 4;
                const char *semaphore_enabled[] = {{ VK_KHR_EXTERNAL_SEMAPHORE_EXTENSION_NAME }};
                VkDeviceCreateInfo semaphore_device_info;
                memset(&semaphore_device_info, 0, sizeof(semaphore_device_info));
                semaphore_device_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                semaphore_device_info.enabledExtensionCount = 1;
                semaphore_device_info.ppEnabledExtensionNames = semaphore_enabled;
                if (validate_device_extensions(&semaphore_device_info) != VK_ERROR_EXTENSION_NOT_PRESENT) return 5;

                VkExportSemaphoreCreateInfo semaphore_export;
                memset(&semaphore_export, 0, sizeof(semaphore_export));
                semaphore_export.sType = VK_STRUCTURE_TYPE_EXPORT_SEMAPHORE_CREATE_INFO;
                semaphore_export.handleTypes = 0;
                bool timeline = true;
                uint64_t initial_value = 99;
                if (!semaphore_create_info_parse_pnext(&semaphore_export, &timeline, &initial_value)) return 6;
                if (timeline || initial_value != 0) return 7;
                semaphore_export.handleTypes = VK_EXTERNAL_SEMAPHORE_HANDLE_TYPE_OPAQUE_FD_BIT;
                if (semaphore_create_info_parse_pnext(&semaphore_export, &timeline, &initial_value)) return 8;

                VkPhysicalDeviceExternalSemaphoreInfo semaphore_info;
                VkExternalSemaphoreProperties semaphore_properties;
                memset(&semaphore_info, 0, sizeof(semaphore_info));
                memset(&semaphore_properties, 0xff, sizeof(semaphore_properties));
                semaphore_info.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_EXTERNAL_SEMAPHORE_INFO;
                semaphore_info.handleType = VK_EXTERNAL_SEMAPHORE_HANDLE_TYPE_OPAQUE_FD_BIT;
                semaphore_properties.sType = VK_STRUCTURE_TYPE_EXTERNAL_SEMAPHORE_PROPERTIES;
                vkGetPhysicalDeviceExternalSemaphoreProperties(VK_NULL_HANDLE, &semaphore_info, &semaphore_properties);
                if (semaphore_properties.externalSemaphoreFeatures != 0) return 9;
                if (semaphore_properties.exportFromImportedHandleTypes != 0) return 10;
                if (semaphore_properties.compatibleHandleTypes != 0) return 11;
            #endif

            #ifdef VK_KHR_EXTERNAL_FENCE_EXTENSION_NAME
                if (device_extension_advertised_name(VK_KHR_EXTERNAL_FENCE_EXTENSION_NAME)) return 12;
                if (extension_seen(extensions, count, VK_KHR_EXTERNAL_FENCE_EXTENSION_NAME)) return 13;
                const char *fence_enabled[] = {{ VK_KHR_EXTERNAL_FENCE_EXTENSION_NAME }};
                VkDeviceCreateInfo fence_device_info;
                memset(&fence_device_info, 0, sizeof(fence_device_info));
                fence_device_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                fence_device_info.enabledExtensionCount = 1;
                fence_device_info.ppEnabledExtensionNames = fence_enabled;
                if (validate_device_extensions(&fence_device_info) != VK_ERROR_EXTENSION_NOT_PRESENT) return 14;

                VkExportFenceCreateInfo fence_export;
                memset(&fence_export, 0, sizeof(fence_export));
                fence_export.sType = VK_STRUCTURE_TYPE_EXPORT_FENCE_CREATE_INFO;
                fence_export.handleTypes = 0;
                if (validate_fence_create_pnext(&fence_export) != VK_SUCCESS) return 15;
                fence_export.handleTypes = VK_EXTERNAL_FENCE_HANDLE_TYPE_OPAQUE_FD_BIT;
                if (validate_fence_create_pnext(&fence_export) != VK_ERROR_FEATURE_NOT_PRESENT) return 16;

                VkPhysicalDeviceExternalFenceInfo fence_info;
                VkExternalFenceProperties fence_properties;
                memset(&fence_info, 0, sizeof(fence_info));
                memset(&fence_properties, 0xff, sizeof(fence_properties));
                fence_info.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_EXTERNAL_FENCE_INFO;
                fence_info.handleType = VK_EXTERNAL_FENCE_HANDLE_TYPE_OPAQUE_FD_BIT;
                fence_properties.sType = VK_STRUCTURE_TYPE_EXTERNAL_FENCE_PROPERTIES;
                vkGetPhysicalDeviceExternalFenceProperties(VK_NULL_HANDLE, &fence_info, &fence_properties);
                if (fence_properties.externalFenceFeatures != 0) return 17;
                if (fence_properties.exportFromImportedHandleTypes != 0) return 18;
                if (fence_properties.compatibleHandleTypes != 0) return 19;
            #endif
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stderr)


    def test_buffer_create_usage2_pnext_supplies_effective_usage(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            int main(void) {{
                VkBufferCreateInfo info;
                VkBufferUsageFlags2CreateInfo usage2;
                memset(&info, 0, sizeof(info));
                memset(&usage2, 0, sizeof(usage2));
                info.sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO;
                info.size = 4096;
                info.usage = 0;
                info.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
                info.pNext = &usage2;
                usage2.sType = VK_STRUCTURE_TYPE_BUFFER_USAGE_FLAGS_2_CREATE_INFO;
                usage2.usage = VK_BUFFER_USAGE_STORAGE_BUFFER_BIT;

                const char *enabled[] = {{ VK_KHR_MAINTENANCE_5_EXTENSION_NAME }};
                VkDeviceCreateInfo device_info;
                memset(&device_info, 0, sizeof(device_info));
                device_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                device_info.enabledExtensionCount = 1;
                device_info.ppEnabledExtensionNames = enabled;
                VkDevice device = VK_NULL_HANDLE;
                if (vkCreateDevice((VkPhysicalDevice)&g_device, &device_info, NULL, &device) != VK_SUCCESS ||
                    device == VK_NULL_HANDLE) {{
                    fprintf(stderr, "maintenance5 test device create failed\\n");
                    return 6;
                }}
                VkBuffer buffer_handle = VK_NULL_HANDLE;
                VkResult rc = vkCreateBuffer(device, &info, NULL, &buffer_handle);
                if (rc != VK_SUCCESS || buffer_handle == VK_NULL_HANDLE) {{
                    fprintf(stderr, "usage2-only buffer create failed rc=%d handle=%p\\n", rc, (void *)buffer_handle);
                    return 2;
                }}
                PdockerVkBuffer *buffer = pdocker_vk_buffer_from_handle(buffer_handle);
                if (!buffer || buffer->usage != VK_BUFFER_USAGE_STORAGE_BUFFER_BIT) {{
                    fprintf(stderr, "effective usage was not stored from usage2: 0x%x\\n", buffer ? buffer->usage : 0u);
                    return 3;
                }}
                vkDestroyBuffer(device, buffer_handle, NULL);

                usage2.usage = 0;
                buffer_handle = VK_NULL_HANDLE;
                rc = vkCreateBuffer(device, &info, NULL, &buffer_handle);
                if (rc == VK_SUCCESS || buffer_handle != VK_NULL_HANDLE) {{
                    fprintf(stderr, "zero usage2 buffer create was accepted rc=%d handle=%p\\n", rc, (void *)buffer_handle);
                    return 4;
                }}

                usage2.usage = (VkBufferUsageFlags2)1ull << 40;
                rc = vkCreateBuffer((VkDevice)(uintptr_t)0x1u, &info, NULL, &buffer_handle);
                if (rc == VK_SUCCESS) {{
                    fprintf(stderr, "out-of-range usage2 buffer create was accepted\\n");
                    return 5;
                }}
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)



    def test_image_live_handles_fail_closed_after_destroy(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            int main(void) {{
                PdockerVkImage image;
                PdockerVkMemory memory;
                memset(&image, 0, sizeof(image));
                memset(&memory, 0, sizeof(memory));
                image.object_id = 101;
                image.format = VK_FORMAT_R8G8B8A8_UNORM;
                image.extent = (VkExtent3D){{4, 4, 1}};
                image.mip_levels = 1;
                image.array_layers = 1;
                image.samples = VK_SAMPLE_COUNT_1_BIT;
                image.usage = VK_IMAGE_USAGE_SAMPLED_BIT;
                image.requirements_size = 1024;
                image.requirements_alignment = PDOCKER_VK_REQUIREMENT_ALIGNMENT;
                image.memory_type_bits = 0x3;
                image.generation = 202;
                memory.object_id = 303;
                memory.size = 4096;
                memory.fd = -1;
                image_register(&image);
                memory_register(&memory);
                VkImage handle = pdocker_vk_image_to_handle(&image);
                VkDeviceMemory memory_handle = pdocker_vk_memory_to_handle(&memory);
                if (image_handle_lookup(handle) != &image) {{
                    fprintf(stderr, "live image lookup failed\\n");
                    return 1;
                }}
                VkMemoryRequirements req;
                memset(&req, 0, sizeof(req));
                vkGetImageMemoryRequirements(VK_NULL_HANDLE, handle, &req);
                if (req.size != 1024 || req.memoryTypeBits != 0x3) {{
                    fprintf(stderr, "live image requirements failed size=%llu bits=0x%x\\n",
                            (unsigned long long)req.size, req.memoryTypeBits);
                    return 2;
                }}
                if (vkBindImageMemory(VK_NULL_HANDLE, handle, memory_handle, 0) != VK_SUCCESS) {{
                    fprintf(stderr, "live image bind failed\\n");
                    return 3;
                }}
                vkDestroyImage(VK_NULL_HANDLE, handle, NULL);
                if (image_handle_lookup(handle) != NULL) {{
                    fprintf(stderr, "destroyed image remained live\\n");
                    return 4;
                }}
                memset(&req, 0xff, sizeof(req));
                vkGetImageMemoryRequirements(VK_NULL_HANDLE, handle, &req);
                if (req.size != 0 || req.memoryTypeBits != 0) {{
                    fprintf(stderr, "stale image requirements did not fail closed\\n");
                    return 5;
                }}
                if (vkBindImageMemory(VK_NULL_HANDLE, handle, memory_handle, 0) == VK_SUCCESS) {{
                    fprintf(stderr, "stale image bind succeeded\\n");
                    return 6;
                }}
                VkImageView view_handle = (VkImageView)(uintptr_t)0xdeadbeefu;
                VkImageViewCreateInfo view_info;
                memset(&view_info, 0, sizeof(view_info));
                view_info.sType = VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO;
                view_info.image = handle;
                view_info.viewType = VK_IMAGE_VIEW_TYPE_2D;
                view_info.format = VK_FORMAT_R8G8B8A8_UNORM;
                view_info.subresourceRange.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
                view_info.subresourceRange.levelCount = 1;
                view_info.subresourceRange.layerCount = 1;
                if (vkCreateImageView(VK_NULL_HANDLE, &view_info, NULL, &view_handle) == VK_SUCCESS ||
                    view_handle != VK_NULL_HANDLE) {{
                    fprintf(stderr, "stale image view create succeeded\\n");
                    return 7;
                }}
#if defined(VK_VERSION_1_1) || defined(VK_KHR_dedicated_allocation)
                VkMemoryDedicatedAllocateInfo dedicated;
                VkMemoryAllocateInfo alloc_info;
                VkDeviceMemory stale_dedicated_memory = (VkDeviceMemory)(uintptr_t)0x1u;
                memset(&dedicated, 0, sizeof(dedicated));
                memset(&alloc_info, 0, sizeof(alloc_info));
                dedicated.sType = VK_STRUCTURE_TYPE_MEMORY_DEDICATED_ALLOCATE_INFO;
                dedicated.image = handle;
                alloc_info.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO;
                alloc_info.pNext = &dedicated;
                alloc_info.allocationSize = 1024;
                alloc_info.memoryTypeIndex = 0;
                if (vkAllocateMemory(VK_NULL_HANDLE, &alloc_info, NULL, &stale_dedicated_memory) == VK_SUCCESS ||
                    stale_dedicated_memory != VK_NULL_HANDLE) {{
                    fprintf(stderr, "stale dedicated image allocation succeeded\\n");
                    return 8;
                }}
#endif
                vkDestroyImage(VK_NULL_HANDLE, handle, NULL);
                vkDestroyImage(VK_NULL_HANDLE, (VkImage)(uintptr_t)0x1234u, NULL);
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


    def test_render_pass_and_framebuffer_live_handles_fail_closed_after_destroy(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"
            {COMMAND_BUFFER_STACK_TEST_HELPER}

            static void init_image_view(PdockerVkImage *image, PdockerVkImageView *view) {{
                memset(image, 0, sizeof(*image));
                memset(view, 0, sizeof(*view));
                image->object_id = 701;
                image->image_type = VK_IMAGE_TYPE_2D;
                image->format = VK_FORMAT_R8G8B8A8_UNORM;
                image->extent = (VkExtent3D){{16, 16, 1}};
                image->mip_levels = 1;
                image->array_layers = 1;
                image->samples = VK_SAMPLE_COUNT_1_BIT;
                image->usage = VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT;
                image->generation = 1701;
                image_register(image);
                view->object_id = 801;
                view->image = image;
                view->view_type = VK_IMAGE_VIEW_TYPE_2D;
                view->format = VK_FORMAT_R8G8B8A8_UNORM;
                view->subresource_range.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
                view->subresource_range.levelCount = 1;
                view->subresource_range.layerCount = 1;
                view->generation = 1801;
                image_view_register(view);
            }}

            int main(void) {{
                VkAttachmentDescription attachment;
                VkAttachmentReference color_ref;
                VkSubpassDescription subpass;
                VkRenderPassCreateInfo rp_info;
                VkRenderPass rp_handle = VK_NULL_HANDLE;
                VkFramebufferCreateInfo fb_info;
                VkFramebuffer fb_handle = VK_NULL_HANDLE;
                PdockerVkRenderPass *rp = NULL;
                PdockerVkFramebuffer *fb = NULL;
                PdockerVkImage image;
                PdockerVkImageView view;
                VkImageView view_handle;

                memset(&attachment, 0, sizeof(attachment));
                attachment.format = VK_FORMAT_R8G8B8A8_UNORM;
                attachment.samples = VK_SAMPLE_COUNT_1_BIT;
                attachment.loadOp = VK_ATTACHMENT_LOAD_OP_DONT_CARE;
                attachment.storeOp = VK_ATTACHMENT_STORE_OP_DONT_CARE;
                attachment.initialLayout = VK_IMAGE_LAYOUT_UNDEFINED;
                attachment.finalLayout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL;
                memset(&color_ref, 0, sizeof(color_ref));
                color_ref.attachment = 0;
                color_ref.layout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL;
                memset(&subpass, 0, sizeof(subpass));
                subpass.pipelineBindPoint = VK_PIPELINE_BIND_POINT_GRAPHICS;
                subpass.colorAttachmentCount = 1;
                subpass.pColorAttachments = &color_ref;
                memset(&rp_info, 0, sizeof(rp_info));
                rp_info.sType = VK_STRUCTURE_TYPE_RENDER_PASS_CREATE_INFO;
                rp_info.attachmentCount = 1;
                rp_info.pAttachments = &attachment;
                rp_info.subpassCount = 1;
                rp_info.pSubpasses = &subpass;

                VkRenderPass bogus_rp = (VkRenderPass)(uintptr_t)0x1234u;
                if (render_pass_handle_lookup(bogus_rp) != NULL) return 1;
                vkDestroyRenderPass(VK_NULL_HANDLE, bogus_rp, NULL);

                memset(&fb_info, 0, sizeof(fb_info));
                fb_info.sType = VK_STRUCTURE_TYPE_FRAMEBUFFER_CREATE_INFO;
                fb_info.renderPass = bogus_rp;
                fb_info.width = 16;
                fb_info.height = 16;
                fb_info.layers = 1;
                fb_handle = (VkFramebuffer)(uintptr_t)0xdeadu;
                if (vkCreateFramebuffer(VK_NULL_HANDLE, &fb_info, NULL, &fb_handle) != VK_ERROR_INITIALIZATION_FAILED ||
                    fb_handle != VK_NULL_HANDLE) {{
                    fprintf(stderr, "bogus render pass accepted by framebuffer create\\n");
                    return 2;
                }}

                if (vkCreateRenderPass(VK_NULL_HANDLE, &rp_info, NULL, &rp_handle) != VK_SUCCESS ||
                    rp_handle == VK_NULL_HANDLE) return 3;
                rp = render_pass_handle_lookup(rp_handle);
                if (!rp || rp->destroyed || !render_pass_subpass_can_normalize_to_dynamic_rendering(rp, 0)) return 4;

                init_image_view(&image, &view);
                view_handle = pdocker_vk_image_view_to_handle(&view);
                fb_info.renderPass = rp_handle;
                fb_info.attachmentCount = 1;
                fb_info.pAttachments = &view_handle;
                if (vkCreateFramebuffer(VK_NULL_HANDLE, &fb_info, NULL, &fb_handle) != VK_SUCCESS ||
                    fb_handle == VK_NULL_HANDLE) return 5;
                fb = framebuffer_handle_lookup(fb_handle);
                if (!fb || fb->destroyed || fb->render_pass != rp || fb->attachment_count != 1 ||
                    !fb->attachment_snapshots[0].valid) return 6;

                VkRenderPassBeginInfo begin_info;
                PdockerVkCommandBuffer cmd;
                memset(&begin_info, 0, sizeof(begin_info));
                reset_test_command_buffer(&cmd, 0, 0);
                begin_info.sType = VK_STRUCTURE_TYPE_RENDER_PASS_BEGIN_INFO;
                begin_info.renderPass = rp_handle;
                begin_info.framebuffer = fb_handle;
                begin_info.renderArea.extent.width = 16;
                begin_info.renderArea.extent.height = 16;
                vkCmdBeginRenderPass((VkCommandBuffer)&cmd, &begin_info, VK_SUBPASS_CONTENTS_INLINE);
                if (!cmd.dynamic_rendering_active || cmd.graphics_unsupported) return 7;

                vkDestroyFramebuffer(VK_NULL_HANDLE, fb_handle, NULL);
                if (framebuffer_handle_lookup(fb_handle) != NULL) return 8;
                vkCmdEndRenderPass((VkCommandBuffer)&cmd);
                if (!cmd.graphics_unsupported) return 9;
                vkDestroyFramebuffer(VK_NULL_HANDLE, fb_handle, NULL);
                vkDestroyFramebuffer(VK_NULL_HANDLE, (VkFramebuffer)(uintptr_t)0x4567u, NULL);

                vkDestroyRenderPass(VK_NULL_HANDLE, rp_handle, NULL);
                if (render_pass_handle_lookup(rp_handle) != NULL) return 10;
                if (render_pass_subpass_can_normalize_to_dynamic_rendering(rp, 0)) return 11;
                fb_handle = (VkFramebuffer)(uintptr_t)0xdeadu;
                if (vkCreateFramebuffer(VK_NULL_HANDLE, &fb_info, NULL, &fb_handle) != VK_ERROR_INITIALIZATION_FAILED ||
                    fb_handle != VK_NULL_HANDLE) {{
                    fprintf(stderr, "destroyed render pass accepted by framebuffer create\\n");
                    return 12;
                }}
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_framebuffer_snapshots_attachment_image_views_for_render_pass_normalization(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"
            {COMMAND_BUFFER_STACK_TEST_HELPER}

            static void init_image(PdockerVkImage *image,
                                   uint64_t object_id,
                                   VkFormat format,
                                   VkSampleCountFlagBits samples,
                                   VkImageAspectFlags aspect) {{
                memset(image, 0, sizeof(*image));
                image->object_id = object_id;
                image->image_type = VK_IMAGE_TYPE_2D;
                image->format = format;
                image->extent = (VkExtent3D){{64, 64, 1}};
                image->mip_levels = 1;
                image->array_layers = 1;
                image->samples = samples;
                image->usage = VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT |
                               VK_IMAGE_USAGE_DEPTH_STENCIL_ATTACHMENT_BIT;
                image->requirements_size = 4096;
                image->current_layout = VK_IMAGE_LAYOUT_UNDEFINED;
                image->generation = object_id + 1000;
                image_register(image);
            }}

            static void init_view(PdockerVkImageView *view,
                                  uint64_t object_id,
                                  PdockerVkImage *image,
                                  VkFormat format,
                                  VkImageAspectFlags aspect) {{
                memset(view, 0, sizeof(*view));
                view->object_id = object_id;
                view->image = image;
                view->view_type = VK_IMAGE_VIEW_TYPE_2D;
                view->format = format;
                view->subresource_range.aspectMask = aspect;
                view->subresource_range.baseMipLevel = 0;
                view->subresource_range.levelCount = 1;
                view->subresource_range.baseArrayLayer = 0;
                view->subresource_range.layerCount = 1;
                view->generation = object_id + 2000;
                image_view_register(view);
            }}

            int main(void) {{
                PdockerVkImage color_image, color_resolve_image, ds_image, ds_resolve_image;
                PdockerVkImageView color_view, color_resolve_view, ds_view, ds_resolve_view;
                PdockerVkRenderPass rp;
                VkImageView attachments[4];
                VkFramebufferCreateInfo fb_info;
                VkFramebuffer fb_handle = VK_NULL_HANDLE;
                PdockerVkFramebuffer *fb = NULL;
                PdockerVkCommandBuffer cmd;
                VkRect2D area = {{ {{0, 0}}, {{64, 64}} }};

                init_image(&color_image, 101, VK_FORMAT_R8G8B8A8_UNORM,
                           VK_SAMPLE_COUNT_4_BIT, VK_IMAGE_ASPECT_COLOR_BIT);
                init_image(&color_resolve_image, 102, VK_FORMAT_R8G8B8A8_UNORM,
                           VK_SAMPLE_COUNT_1_BIT, VK_IMAGE_ASPECT_COLOR_BIT);
                init_image(&ds_image, 201, VK_FORMAT_D24_UNORM_S8_UINT,
                           VK_SAMPLE_COUNT_4_BIT,
                           VK_IMAGE_ASPECT_DEPTH_BIT | VK_IMAGE_ASPECT_STENCIL_BIT);
                init_image(&ds_resolve_image, 202, VK_FORMAT_D24_UNORM_S8_UINT,
                           VK_SAMPLE_COUNT_1_BIT,
                           VK_IMAGE_ASPECT_DEPTH_BIT | VK_IMAGE_ASPECT_STENCIL_BIT);
                init_view(&color_view, 301, &color_image, VK_FORMAT_R8G8B8A8_UNORM,
                          VK_IMAGE_ASPECT_COLOR_BIT);
                init_view(&color_resolve_view, 302, &color_resolve_image, VK_FORMAT_R8G8B8A8_UNORM,
                          VK_IMAGE_ASPECT_COLOR_BIT);
                init_view(&ds_view, 401, &ds_image, VK_FORMAT_D24_UNORM_S8_UINT,
                          VK_IMAGE_ASPECT_DEPTH_BIT | VK_IMAGE_ASPECT_STENCIL_BIT);
                init_view(&ds_resolve_view, 402, &ds_resolve_image, VK_FORMAT_D24_UNORM_S8_UINT,
                          VK_IMAGE_ASPECT_DEPTH_BIT | VK_IMAGE_ASPECT_STENCIL_BIT);

                memset(&rp, 0, sizeof(rp));
                rp.attachment_count = 4;
                rp.subpass_count = 1;
                rp.attachments[0].format = VK_FORMAT_R8G8B8A8_UNORM;
                rp.attachments[0].samples = VK_SAMPLE_COUNT_4_BIT;
                rp.attachments[0].initial_layout = VK_IMAGE_LAYOUT_UNDEFINED;
                rp.attachments[0].final_layout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL;
                rp.attachments[1] = rp.attachments[0];
                rp.attachments[1].samples = VK_SAMPLE_COUNT_1_BIT;
                rp.attachments[2].format = VK_FORMAT_D24_UNORM_S8_UINT;
                rp.attachments[2].samples = VK_SAMPLE_COUNT_4_BIT;
                rp.attachments[2].initial_layout = VK_IMAGE_LAYOUT_UNDEFINED;
                rp.attachments[2].final_layout = VK_IMAGE_LAYOUT_DEPTH_STENCIL_ATTACHMENT_OPTIMAL;
                rp.attachments[3] = rp.attachments[2];
                rp.attachments[3].samples = VK_SAMPLE_COUNT_1_BIT;
                rp.subpasses[0].color_attachment_count = 1;
                rp.subpasses[0].color_attachments[0] = 0;
                rp.subpasses[0].color_layouts[0] = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL;
                rp.subpasses[0].resolve_attachments[0] = 1;
                rp.subpasses[0].resolve_layouts[0] = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL;
                rp.subpasses[0].has_depth_stencil_attachment = true;
                rp.subpasses[0].depth_stencil_attachment = 2;
                rp.subpasses[0].depth_stencil_layout = VK_IMAGE_LAYOUT_DEPTH_STENCIL_ATTACHMENT_OPTIMAL;
                rp.subpasses[0].has_depth_stencil_resolve_attachment = true;
                rp.subpasses[0].depth_stencil_resolve_attachment = 3;
                rp.subpasses[0].depth_stencil_resolve_layout = VK_IMAGE_LAYOUT_DEPTH_STENCIL_ATTACHMENT_OPTIMAL;
                rp.subpasses[0].depth_resolve_mode = VK_RESOLVE_MODE_AVERAGE_BIT;
                rp.subpasses[0].stencil_resolve_mode = VK_RESOLVE_MODE_AVERAGE_BIT;
                rp.object_id = 901;
                rp.generation = 901;
                render_pass_register(&rp);

                memset(&fb_info, 0, sizeof(fb_info));
                fb_info.sType = VK_STRUCTURE_TYPE_FRAMEBUFFER_CREATE_INFO;
                fb_info.renderPass = pdocker_vk_render_pass_to_handle(&rp);
                fb_info.attachmentCount = 1;
                VkImageView bogus = (VkImageView)(uintptr_t)0x1234u;
                fb_info.pAttachments = &bogus;
                fb_info.width = 64;
                fb_info.height = 64;
                fb_info.layers = 1;
                if (vkCreateFramebuffer(VK_NULL_HANDLE, &fb_info, NULL, &fb_handle) != VK_ERROR_INITIALIZATION_FAILED ||
                    fb_handle != VK_NULL_HANDLE) {{
                    fprintf(stderr, "bogus framebuffer attachment was accepted\\n");
                    return 1;
                }}

                attachments[0] = pdocker_vk_image_view_to_handle(&color_view);
                attachments[1] = pdocker_vk_image_view_to_handle(&color_resolve_view);
                attachments[2] = pdocker_vk_image_view_to_handle(&ds_view);
                attachments[3] = pdocker_vk_image_view_to_handle(&ds_resolve_view);
                fb_info.attachmentCount = 4;
                fb_info.pAttachments = attachments;
                if (vkCreateFramebuffer(VK_NULL_HANDLE, &fb_info, NULL, &fb_handle) != VK_SUCCESS ||
                    fb_handle == VK_NULL_HANDLE) {{
                    fprintf(stderr, "valid framebuffer creation failed\\n");
                    return 2;
                }}
                fb = pdocker_vk_framebuffer_from_handle(fb_handle);
                if (!fb || !fb->attachment_snapshots[0].valid || !fb->attachment_snapshots[1].valid ||
                    !fb->attachment_snapshots[2].valid || !fb->attachment_snapshots[3].valid) {{
                    fprintf(stderr, "framebuffer did not snapshot all attachments\\n");
                    return 3;
                }}
                if (fb->attachment_snapshots[0].object_id != 301 ||
                    fb->attachment_snapshots[1].samples != VK_SAMPLE_COUNT_1_BIT ||
                    fb->attachment_snapshots[2].subresource_range.aspectMask !=
                        (VK_IMAGE_ASPECT_DEPTH_BIT | VK_IMAGE_ASPECT_STENCIL_BIT)) {{
                    fprintf(stderr, "framebuffer attachment snapshot contents were wrong\\n");
                    return 4;
                }}

                reset_test_command_buffer(&cmd, 0, 0);
                if (!populate_render_pass_subpass_rendering_state(&cmd, &rp, fb, area,
                                                                  NULL, 0, 0,
                                                                  VK_SUBPASS_CONTENTS_INLINE)) {{
                    fprintf(stderr, "render pass populate failed\\n");
                    return 5;
                }}
                if (!cmd.active_color_attachments[0].image_view_snapshot.valid ||
                    cmd.active_color_attachments[0].image_view_snapshot.object_id != 301 ||
                    !cmd.active_color_attachments[0].resolve_image_view_snapshot.valid ||
                    cmd.active_color_attachments[0].resolve_image_view_snapshot.object_id != 302) {{
                    fprintf(stderr, "color/resolve snapshots were not propagated\\n");
                    return 6;
                }}
                if (!cmd.active_depth_attachment.image_view_snapshot.valid ||
                    cmd.active_depth_attachment.image_view_snapshot.object_id != 401 ||
                    !cmd.active_depth_attachment.resolve_image_view_snapshot.valid ||
                    cmd.active_depth_attachment.resolve_image_view_snapshot.object_id != 402 ||
                    !cmd.active_stencil_attachment.image_view_snapshot.valid ||
                    cmd.active_stencil_attachment.image_view_snapshot.object_id != 401 ||
                    !cmd.active_stencil_attachment.resolve_image_view_snapshot.valid ||
                    cmd.active_stencil_attachment.resolve_image_view_snapshot.object_id != 402) {{
                    fprintf(stderr, "depth/stencil snapshots were not propagated\\n");
                    return 7;
                }}

                vkDestroyFramebuffer(VK_NULL_HANDLE, fb_handle, NULL);
                fb_handle = VK_NULL_HANDLE;
                vkDestroyImageView(VK_NULL_HANDLE, attachments[0], NULL);
                fb_info.attachmentCount = 1;
                fb_info.pAttachments = attachments;
                if (vkCreateFramebuffer(VK_NULL_HANDLE, &fb_info, NULL, &fb_handle) != VK_ERROR_INITIALIZATION_FAILED ||
                    fb_handle != VK_NULL_HANDLE) {{
                    fprintf(stderr, "destroyed framebuffer attachment was accepted\\n");
                    return 8;
                }}
                render_pass_retire(render_pass_unregister(pdocker_vk_render_pass_to_handle(&rp)));
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_image_view_live_handles_fail_closed_after_destroy(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            int main(void) {{
                PdockerVkImage image;
                PdockerVkImageView view;
                PdockerVkImageViewSnapshot snapshot;
                memset(&image, 0, sizeof(image));
                memset(&view, 0, sizeof(view));
                memset(&snapshot, 0, sizeof(snapshot));
                image.object_id = 111;
                image.format = VK_FORMAT_R8G8B8A8_UNORM;
                image.extent = (VkExtent3D){{4, 4, 1}};
                image.mip_levels = 1;
                image.array_layers = 1;
                image.samples = VK_SAMPLE_COUNT_1_BIT;
                image.generation = 222;
                view.object_id = 333;
                view.image = &image;
                view.view_type = VK_IMAGE_VIEW_TYPE_2D;
                view.format = VK_FORMAT_R8G8B8A8_UNORM;
                view.subresource_range.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
                view.subresource_range.levelCount = 1;
                view.subresource_range.layerCount = 1;
                view.generation = 444;
                image_register(&image);
                image_view_register(&view);
                VkImageView handle = pdocker_vk_image_view_to_handle(&view);
                if (image_view_handle_lookup(handle) != &view) {{
                    fprintf(stderr, "live image view lookup failed\\n");
                    return 1;
                }}
                if (!snapshot_image_view_state(&snapshot, &view) || !snapshot.valid ||
                    snapshot.object_id != 333 || snapshot.image != &image || snapshot.samples != VK_SAMPLE_COUNT_1_BIT) {{
                    fprintf(stderr, "live image view snapshot failed\\n");
                    return 2;
                }}
                vkDestroyImageView(VK_NULL_HANDLE, handle, NULL);
                if (image_view_handle_lookup(handle) != NULL) {{
                    fprintf(stderr, "destroyed image view remained live\\n");
                    return 3;
                }}
                memset(&snapshot, 0, sizeof(snapshot));
                if (snapshot_image_view_state(&snapshot, &view) || snapshot.valid) {{
                    fprintf(stderr, "destroyed image view snapshot succeeded\\n");
                    return 4;
                }}
                vkDestroyImageView(VK_NULL_HANDLE, handle, NULL);
                vkDestroyImageView(VK_NULL_HANDLE, (VkImageView)(uintptr_t)0x1234u, NULL);
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


    def test_sampler_live_handles_fail_closed_after_destroy(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            int main(void) {{
                PdockerVkSampler sampler;
                PdockerVkSamplerSnapshot snapshot;
                memset(&sampler, 0, sizeof(sampler));
                memset(&snapshot, 0, sizeof(snapshot));
                sampler.object_id = 555;
                sampler.mag_filter = VK_FILTER_NEAREST;
                sampler.min_filter = VK_FILTER_NEAREST;
                sampler.mipmap_mode = VK_SAMPLER_MIPMAP_MODE_NEAREST;
                sampler.address_mode_u = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
                sampler.address_mode_v = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
                sampler.address_mode_w = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
                sampler.min_lod = 0.0f;
                sampler.max_lod = 1.0f;
                sampler.reduction_mode = VK_SAMPLER_REDUCTION_MODE_WEIGHTED_AVERAGE;
                sampler.generation = 666;
                sampler_register(&sampler);
                VkSampler handle = pdocker_vk_sampler_to_handle(&sampler);
                if (sampler_handle_lookup(handle) != &sampler) {{
                    fprintf(stderr, "live sampler lookup failed\\n");
                    return 1;
                }}
                if (!snapshot_sampler_state(&snapshot, &sampler) || !snapshot.valid ||
                    snapshot.object_id != 555 || snapshot.reduction_mode != VK_SAMPLER_REDUCTION_MODE_WEIGHTED_AVERAGE) {{
                    fprintf(stderr, "live sampler snapshot failed\\n");
                    return 2;
                }}
                vkDestroySampler(VK_NULL_HANDLE, handle, NULL);
                if (sampler_handle_lookup(handle) != NULL) {{
                    fprintf(stderr, "destroyed sampler remained live\\n");
                    return 3;
                }}
                memset(&snapshot, 0, sizeof(snapshot));
                if (snapshot_sampler_state(&snapshot, &sampler) || snapshot.valid) {{
                    fprintf(stderr, "destroyed sampler snapshot succeeded\\n");
                    return 4;
                }}
                VkDescriptorSetLayoutBinding binding;
                VkDescriptorSetLayoutCreateInfo layout_info;
                VkDescriptorSetLayout layout = (VkDescriptorSetLayout)(uintptr_t)0x1u;
                memset(&binding, 0, sizeof(binding));
                memset(&layout_info, 0, sizeof(layout_info));
                binding.binding = 0;
                binding.descriptorType = VK_DESCRIPTOR_TYPE_SAMPLER;
                binding.descriptorCount = 1;
                binding.stageFlags = VK_SHADER_STAGE_COMPUTE_BIT;
                binding.pImmutableSamplers = &handle;
                layout_info.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO;
                layout_info.bindingCount = 1;
                layout_info.pBindings = &binding;
                if (vkCreateDescriptorSetLayout(VK_NULL_HANDLE, &layout_info, NULL, &layout) == VK_SUCCESS ||
                    layout != VK_NULL_HANDLE) {{
                    fprintf(stderr, "stale immutable sampler layout succeeded\\n");
                    return 5;
                }}
                vkDestroySampler(VK_NULL_HANDLE, handle, NULL);
                vkDestroySampler(VK_NULL_HANDLE, (VkSampler)(uintptr_t)0x1234u, NULL);
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


    def test_buffer_view_live_handles_fail_closed_after_destroy(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            int main(void) {{
                PdockerVkMemory memory;
                PdockerVkBuffer buffer;
                PdockerVkBufferView view;
                PdockerVkBufferViewSnapshot snapshot;
                memset(&memory, 0, sizeof(memory));
                memset(&buffer, 0, sizeof(buffer));
                memset(&view, 0, sizeof(view));
                memset(&snapshot, 0, sizeof(snapshot));
                memory.size = 4096;
                memory.fd = -1;
                buffer.object_id = 10;
                buffer.size = 4096;
                buffer.usage = VK_BUFFER_USAGE_UNIFORM_TEXEL_BUFFER_BIT;
                buffer.memory = &memory;
                view.object_id = 20;
                view.buffer = &buffer;
                view.format = VK_FORMAT_R8_UINT;
                view.offset = 0;
                view.range = 128;
                view.generation = 30;
                buffer_register(&buffer);
                buffer_view_register(&view);
                VkBufferView handle = pdocker_vk_buffer_view_to_handle(&view);
                if (buffer_view_handle_lookup(handle) != &view) {{
                    fprintf(stderr, "live buffer view lookup failed\\n");
                    return 1;
                }}
                if (!snapshot_buffer_view_state(&snapshot, &view) || !snapshot.valid ||
                    snapshot.object_id != 20 || snapshot.buffer_snapshot.object_id != 10) {{
                    fprintf(stderr, "live buffer view snapshot failed\\n");
                    return 2;
                }}
                vkDestroyBufferView(VK_NULL_HANDLE, handle, NULL);
                if (buffer_view_handle_lookup(handle) != NULL) {{
                    fprintf(stderr, "destroyed buffer view remained live\\n");
                    return 3;
                }}
                memset(&snapshot, 0, sizeof(snapshot));
                if (snapshot_buffer_view_state(&snapshot, &view) || snapshot.valid) {{
                    fprintf(stderr, "destroyed buffer view snapshot succeeded\\n");
                    return 4;
                }}
                vkDestroyBufferView(VK_NULL_HANDLE, handle, NULL);
                vkDestroyBufferView(VK_NULL_HANDLE, (VkBufferView)(uintptr_t)0x1234u, NULL);
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


    def test_buffer_view_usage2_pnext_accepts_only_noop_texel_usage(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            int main(void) {{
                PdockerVkBuffer buffer;
                VkBufferViewCreateInfo view_info;
                VkBufferUsageFlags2CreateInfo usage2;
                VkBufferUsageFlags texel_usage = 0;
                memset(&buffer, 0, sizeof(buffer));
                memset(&view_info, 0, sizeof(view_info));
                memset(&usage2, 0, sizeof(usage2));

                buffer.usage = VK_BUFFER_USAGE_TRANSFER_DST_BIT |
                               VK_BUFFER_USAGE_UNIFORM_TEXEL_BUFFER_BIT |
                               VK_BUFFER_USAGE_STORAGE_TEXEL_BUFFER_BIT;
                view_info.sType = VK_STRUCTURE_TYPE_BUFFER_VIEW_CREATE_INFO;
                view_info.pNext = &usage2;
                usage2.sType = VK_STRUCTURE_TYPE_BUFFER_USAGE_FLAGS_2_CREATE_INFO;
                usage2.usage = VK_BUFFER_USAGE_UNIFORM_TEXEL_BUFFER_BIT |
                               VK_BUFFER_USAGE_STORAGE_TEXEL_BUFFER_BIT;
                if (validate_buffer_view_create_pnext_with_extensions(&view_info, &buffer, &texel_usage, PDOCKER_VK_DEVICE_EXT_KHR_MAINTENANCE_5) != VK_SUCCESS) {{
                    fprintf(stderr, "matching usage2 texel subset was rejected\\n");
                    return 2;
                }}
                if (texel_usage != (VK_BUFFER_USAGE_UNIFORM_TEXEL_BUFFER_BIT |
                                    VK_BUFFER_USAGE_STORAGE_TEXEL_BUFFER_BIT)) {{
                    fprintf(stderr, "unexpected texel usage 0x%x\\n", texel_usage);
                    return 3;
                }}

                usage2.usage = VK_BUFFER_USAGE_UNIFORM_TEXEL_BUFFER_BIT;
                if (validate_buffer_view_create_pnext_with_extensions(&view_info, &buffer, &texel_usage, PDOCKER_VK_DEVICE_EXT_KHR_MAINTENANCE_5) == VK_SUCCESS) {{
                    fprintf(stderr, "narrowing usage2 was accepted without a view-usage ABI field\\n");
                    return 4;
                }}

                usage2.usage = VK_BUFFER_USAGE_UNIFORM_TEXEL_BUFFER_BIT |
                               VK_BUFFER_USAGE_TRANSFER_DST_BIT;
                if (validate_buffer_view_create_pnext_with_extensions(&view_info, &buffer, &texel_usage, PDOCKER_VK_DEVICE_EXT_KHR_MAINTENANCE_5) == VK_SUCCESS) {{
                    fprintf(stderr, "non-texel usage2 bit was accepted\\n");
                    return 5;
                }}

                usage2.usage = 0;
                if (validate_buffer_view_create_pnext_with_extensions(&view_info, &buffer, &texel_usage, PDOCKER_VK_DEVICE_EXT_KHR_MAINTENANCE_5) == VK_SUCCESS) {{
                    fprintf(stderr, "zero usage2 was accepted\\n");
                    return 6;
                }}

                VkBaseInStructure unknown;
                memset(&unknown, 0, sizeof(unknown));
                unknown.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO;
                view_info.pNext = &unknown;
                if (validate_buffer_view_create_pnext_with_extensions(&view_info, &buffer, &texel_usage, PDOCKER_VK_DEVICE_EXT_KHR_MAINTENANCE_5) == VK_SUCCESS) {{
                    fprintf(stderr, "unknown buffer-view pNext was accepted\\n");
                    return 7;
                }}
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


    def test_image_view_type_compatibility_is_fail_closed(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            static VkImageCreateInfo base_image_info(void) {{
                VkImageCreateInfo info;
                memset(&info, 0, sizeof(info));
                info.sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO;
                info.imageType = VK_IMAGE_TYPE_2D;
                info.format = VK_FORMAT_R8G8B8A8_UNORM;
                info.extent.width = 64;
                info.extent.height = 64;
                info.extent.depth = 1;
                info.mipLevels = 1;
                info.arrayLayers = 1;
                info.samples = VK_SAMPLE_COUNT_1_BIT;
                info.tiling = VK_IMAGE_TILING_OPTIMAL;
                info.usage = VK_IMAGE_USAGE_SAMPLED_BIT;
                info.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
                info.initialLayout = VK_IMAGE_LAYOUT_UNDEFINED;
                return info;
            }}

            static PdockerVkImage image_from_info(const VkImageCreateInfo *info) {{
                PdockerVkImage image;
                memset(&image, 0, sizeof(image));
                image.flags = info->flags;
                image.image_type = info->imageType;
                image.format = info->format;
                image.extent = info->extent;
                image.mip_levels = info->mipLevels;
                image.array_layers = info->arrayLayers;
                image.samples = info->samples;
                image.tiling = info->tiling;
                image.usage = info->usage;
                image.sharing_mode = info->sharingMode;
                image.initial_layout = info->initialLayout;
                image.generation = 1;
                return image;
            }}

            static int expect_view_mip_result(
                    PdockerVkImage *image,
                    VkImageViewType view_type,
                    uint32_t base_mip,
                    uint32_t level_count,
                    uint32_t base_layer,
                    uint32_t layer_count,
                    VkResult expected,
                    int code) {{
                VkImageViewCreateInfo view;
                memset(&view, 0, sizeof(view));
                view.sType = VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO;
                view.image = pdocker_vk_image_to_handle(image);
                view.viewType = view_type;
                view.format = image->format;
                view.subresourceRange.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
                view.subresourceRange.baseMipLevel = base_mip;
                view.subresourceRange.levelCount = level_count;
                view.subresourceRange.baseArrayLayer = base_layer;
                view.subresourceRange.layerCount = layer_count;
                VkResult rc = validate_image_view_create_info_for_transport(VK_NULL_HANDLE, &view, NULL);
                if (rc != expected) {{
                    fprintf(stderr, "case %d returned %d expected %d\\n", code, rc, expected);
                    return code;
                }}
                return 0;
            }}

            static int expect_view_result(
                    PdockerVkImage *image,
                    VkImageViewType view_type,
                    uint32_t base_layer,
                    uint32_t layer_count,
                    VkResult expected,
                    int code) {{
                return expect_view_mip_result(
                    image, view_type, 0, 1, base_layer, layer_count, expected, code);
            }}

            int main(void) {{
                setenv("PDOCKER_VULKAN_HEAP_BYTES", "2147483648", 1);
                setenv("PDOCKER_VULKAN_MAX_BUFFER_BYTES", "2147483648", 1);

                VkImageCreateInfo info = base_image_info();
                if (validate_image_create_info_for_transport(&info) != VK_SUCCESS) {{
                    fprintf(stderr, "ordinary 2D image create was rejected\\n");
                    return 2;
                }}
                PdockerVkImage image2d = image_from_info(&info);
                image_register(&image2d);
                if (expect_view_result(&image2d, VK_IMAGE_VIEW_TYPE_2D, 0, 1,
                                       VK_SUCCESS, 3)) return 3;
                if (expect_view_result(&image2d, VK_IMAGE_VIEW_TYPE_3D, 0, 1,
                                       VK_ERROR_FORMAT_NOT_SUPPORTED, 4)) return 4;
                if (expect_view_result(&image2d, VK_IMAGE_VIEW_TYPE_CUBE, 0, 1,
                                       VK_ERROR_FORMAT_NOT_SUPPORTED, 5)) return 5;

                VkImageCreateInfo bad_cube = base_image_info();
                bad_cube.flags = VK_IMAGE_CREATE_CUBE_COMPATIBLE_BIT;
                bad_cube.extent.height = 32;
                bad_cube.arrayLayers = 6;
                if (validate_image_create_info_for_transport(&bad_cube) != VK_ERROR_FORMAT_NOT_SUPPORTED) {{
                    fprintf(stderr, "non-square cube-compatible image was accepted\\n");
                    return 6;
                }}

                VkImageCreateInfo cube_info = base_image_info();
                cube_info.flags = VK_IMAGE_CREATE_CUBE_COMPATIBLE_BIT;
                cube_info.arrayLayers = 12;
                if (validate_image_create_info_for_transport(&cube_info) != VK_SUCCESS) {{
                    fprintf(stderr, "valid cube-compatible image was rejected\\n");
                    return 7;
                }}
                PdockerVkImage cube = image_from_info(&cube_info);
                image_register(&cube);
                if (expect_view_result(&cube, VK_IMAGE_VIEW_TYPE_CUBE, 0, 6,
                                       VK_SUCCESS, 8)) return 8;
                if (expect_view_result(&cube, VK_IMAGE_VIEW_TYPE_CUBE_ARRAY, 0, 12,
                                       VK_SUCCESS, 9)) return 9;
                if (expect_view_result(&cube, VK_IMAGE_VIEW_TYPE_CUBE, 1, 6,
                                       VK_ERROR_FORMAT_NOT_SUPPORTED, 10)) return 10;

                VkImageCreateInfo image3d_info = base_image_info();
                image3d_info.imageType = VK_IMAGE_TYPE_3D;
                image3d_info.extent.depth = 4;
                image3d_info.arrayLayers = 1;
                if (validate_image_create_info_for_transport(&image3d_info) != VK_SUCCESS) {{
                    fprintf(stderr, "valid 3D image was rejected\\n");
                    return 11;
                }}
                PdockerVkImage image3d = image_from_info(&image3d_info);
                image_register(&image3d);
                if (expect_view_result(&image3d, VK_IMAGE_VIEW_TYPE_3D, 0, 1,
                                       VK_SUCCESS, 12)) return 12;
                if (expect_view_result(&image3d, VK_IMAGE_VIEW_TYPE_2D, 0, 1,
                                       VK_ERROR_FORMAT_NOT_SUPPORTED, 13)) return 13;

                VkImageCreateInfo image3d_sliced_info = base_image_info();
                image3d_sliced_info.flags = VK_IMAGE_CREATE_2D_ARRAY_COMPATIBLE_BIT;
                image3d_sliced_info.imageType = VK_IMAGE_TYPE_3D;
                image3d_sliced_info.extent.depth = 8;
                image3d_sliced_info.mipLevels = 2;
                image3d_sliced_info.arrayLayers = 1;
                if (validate_image_create_info_for_transport(&image3d_sliced_info) != VK_SUCCESS) {{
                    fprintf(stderr, "valid 3D 2D-array-compatible image was rejected\\n");
                    return 14;
                }}
                PdockerVkImage image3d_sliced = image_from_info(&image3d_sliced_info);
                image_register(&image3d_sliced);
                if (expect_view_result(&image3d_sliced, VK_IMAGE_VIEW_TYPE_2D, 3, 1,
                                       VK_SUCCESS, 15)) return 15;
                if (expect_view_result(&image3d_sliced, VK_IMAGE_VIEW_TYPE_2D_ARRAY, 0, 8,
                                       VK_SUCCESS, 16)) return 16;
                if (expect_view_result(&image3d_sliced, VK_IMAGE_VIEW_TYPE_2D_ARRAY, 4, 5,
                                       VK_ERROR_FORMAT_NOT_SUPPORTED, 17)) return 17;
                if (expect_view_mip_result(&image3d_sliced, VK_IMAGE_VIEW_TYPE_2D_ARRAY,
                                           0, 2, 0, 1,
                                           VK_ERROR_FORMAT_NOT_SUPPORTED, 18)) return 18;
                if (expect_view_mip_result(&image3d_sliced, VK_IMAGE_VIEW_TYPE_2D_ARRAY,
                                           0, VK_REMAINING_MIP_LEVELS, 0, 1,
                                           VK_ERROR_FORMAT_NOT_SUPPORTED, 19)) return 19;
                if (expect_view_mip_result(&image3d_sliced, VK_IMAGE_VIEW_TYPE_2D,
                                           1, VK_REMAINING_MIP_LEVELS, 3, 1,
                                           VK_SUCCESS, 20)) return 20;
                if (expect_view_mip_result(&image3d_sliced, VK_IMAGE_VIEW_TYPE_2D,
                                           1, 1, 4, 1,
                                           VK_ERROR_FORMAT_NOT_SUPPORTED, 21)) return 21;
                if (expect_view_mip_result(&image3d_sliced, VK_IMAGE_VIEW_TYPE_3D,
                                           0, 1, 0, VK_REMAINING_ARRAY_LAYERS,
                                           VK_SUCCESS, 22)) return 22;

                VkImageCreateInfo bad_2d_array_flag = base_image_info();
                bad_2d_array_flag.flags = VK_IMAGE_CREATE_2D_ARRAY_COMPATIBLE_BIT;
                if (validate_image_create_info_for_transport(&bad_2d_array_flag) != VK_ERROR_FORMAT_NOT_SUPPORTED) {{
                    fprintf(stderr, "2D image with 2D-array-compatible flag was accepted\\n");
                    return 23;
                }}

                VkPhysicalDevice physical = (VkPhysicalDevice)physical_device_for_instance(NULL);
                VkImageFormatProperties props;
                memset(&props, 0, sizeof(props));
                if (vkGetPhysicalDeviceImageFormatProperties(
                        physical, VK_FORMAT_R8G8B8A8_UNORM, VK_IMAGE_TYPE_3D,
                        VK_IMAGE_TILING_OPTIMAL, VK_IMAGE_USAGE_SAMPLED_BIT,
                        VK_IMAGE_CREATE_2D_ARRAY_COMPATIBLE_BIT, &props) != VK_SUCCESS) {{
                    fprintf(stderr, "format query rejected valid 3D 2D-array-compatible flag\\n");
                    return 24;
                }}
                if (vkGetPhysicalDeviceImageFormatProperties(
                        physical, VK_FORMAT_R8G8B8A8_UNORM, VK_IMAGE_TYPE_2D,
                        VK_IMAGE_TILING_OPTIMAL, VK_IMAGE_USAGE_SAMPLED_BIT,
                        VK_IMAGE_CREATE_2D_ARRAY_COMPATIBLE_BIT, &props) != VK_ERROR_FORMAT_NOT_SUPPORTED) {{
                    fprintf(stderr, "format query accepted 2D 2D-array-compatible flag\\n");
                    return 25;
                }}

                VkImageViewMinLodCreateInfoEXT min_lod;
                memset(&min_lod, 0, sizeof(min_lod));
                min_lod.sType = VK_STRUCTURE_TYPE_IMAGE_VIEW_MIN_LOD_CREATE_INFO_EXT;
                min_lod.minLod = 0.0f;
                VkImageViewCreateInfo min_lod_view;
                memset(&min_lod_view, 0, sizeof(min_lod_view));
                min_lod_view.sType = VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO;
                min_lod_view.pNext = &min_lod;
                min_lod_view.image = pdocker_vk_image_to_handle(&image2d);
                min_lod_view.viewType = VK_IMAGE_VIEW_TYPE_2D;
                min_lod_view.format = image2d.format;
                min_lod_view.subresourceRange.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
                min_lod_view.subresourceRange.baseMipLevel = 0;
                min_lod_view.subresourceRange.levelCount = 1;
                min_lod_view.subresourceRange.baseArrayLayer = 0;
                min_lod_view.subresourceRange.layerCount = 1;
                if (validate_image_view_create_info_for_transport(VK_NULL_HANDLE, &min_lod_view, NULL) != VK_SUCCESS) {{
                    fprintf(stderr, "no-op image view minLod pNext was rejected\\n");
                    return 26;
                }}
                min_lod.minLod = 1.0f;
                if (validate_image_view_create_info_for_transport(VK_NULL_HANDLE, &min_lod_view, NULL) != VK_ERROR_FEATURE_NOT_PRESENT) {{
                    fprintf(stderr, "nonzero image view minLod pNext was accepted\\n");
                    return 27;
                }}
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


    def test_non_byte_linear_image_formats_are_fail_closed(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            #ifndef VK_IMAGE_ASPECT_PLANE_0_BIT
            #define VK_IMAGE_ASPECT_PLANE_0_BIT ((VkImageAspectFlagBits)0x00000010)
            #endif

            static VkImageCreateInfo image_info_for_format(VkFormat format) {{
                VkImageCreateInfo info;
                memset(&info, 0, sizeof(info));
                info.sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO;
                info.imageType = VK_IMAGE_TYPE_2D;
                info.format = format;
                info.extent.width = 64;
                info.extent.height = 64;
                info.extent.depth = 1;
                info.mipLevels = 1;
                info.arrayLayers = 1;
                info.samples = VK_SAMPLE_COUNT_1_BIT;
                info.tiling = VK_IMAGE_TILING_OPTIMAL;
                info.usage = VK_IMAGE_USAGE_SAMPLED_BIT;
                info.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
                info.initialLayout = VK_IMAGE_LAYOUT_UNDEFINED;
                return info;
            }}

            static int expect_image_format_rejected(VkFormat format, int code) {{
                VkFormatProperties format_props;
                memset(&format_props, 0xff, sizeof(format_props));
                vkGetPhysicalDeviceFormatProperties((VkPhysicalDevice)physical_device_for_instance(NULL), format, &format_props);
                if (format_props.bufferFeatures != 0 ||
                    format_props.linearTilingFeatures != 0 ||
                    format_props.optimalTilingFeatures != 0) {{
                    fprintf(stderr, "case %d unsupported format advertised features buffer=0x%x linear=0x%x optimal=0x%x\\n",
                            code,
                            (unsigned)format_props.bufferFeatures,
                            (unsigned)format_props.linearTilingFeatures,
                            (unsigned)format_props.optimalTilingFeatures);
                    return code;
                }}

                VkPhysicalDevice physical = (VkPhysicalDevice)physical_device_for_instance(NULL);
                VkImageFormatProperties image_props;
                memset(&image_props, 0xff, sizeof(image_props));
                VkResult rc = vkGetPhysicalDeviceImageFormatProperties(
                    physical,
                    format,
                    VK_IMAGE_TYPE_2D,
                    VK_IMAGE_TILING_OPTIMAL,
                    VK_IMAGE_USAGE_SAMPLED_BIT,
                    0,
                    &image_props);
                if (rc != VK_ERROR_FORMAT_NOT_SUPPORTED) {{
                    fprintf(stderr, "case %d image format query returned %d\\n", code, rc);
                    return code + 10;
                }}
                if (image_props.maxExtent.width != 0 ||
                    image_props.maxMipLevels != 0 ||
                    image_props.sampleCounts != 0 ||
                    image_props.maxResourceSize != 0) {{
                    fprintf(stderr, "case %d rejected image query left nonzero properties\\n", code);
                    return code + 20;
                }}

                VkImageCreateInfo info = image_info_for_format(format);
                rc = validate_image_create_info_for_transport(&info);
                if (rc != VK_ERROR_FORMAT_NOT_SUPPORTED) {{
                    fprintf(stderr, "case %d validate image create returned %d\\n", code, rc);
                    return code + 30;
                }}

                VkImage image = (VkImage)(uintptr_t)0x1234u;
                rc = vkCreateImage(VK_NULL_HANDLE, &info, NULL, &image);
                if (rc != VK_ERROR_FORMAT_NOT_SUPPORTED || image != VK_NULL_HANDLE) {{
                    fprintf(stderr, "case %d create image rc=%d image=%p\\n", code, rc, (void *)image);
                    return code + 40;
                }}
                return 0;
            }}

            int main(void) {{
                setenv("PDOCKER_VULKAN_HEAP_BYTES", "2147483648", 1);
                setenv("PDOCKER_VULKAN_MAX_BUFFER_BYTES", "2147483648", 1);

                if (expect_image_format_rejected(VK_FORMAT_BC1_RGBA_UNORM_BLOCK, 2)) return 2;
            #ifdef VK_VERSION_1_1
                if (expect_image_format_rejected(VK_FORMAT_G8_B8R8_2PLANE_420_UNORM, 3)) return 3;
            #endif

                PdockerVkImage color_image;
                memset(&color_image, 0, sizeof(color_image));
                color_image.format = VK_FORMAT_R8G8B8A8_UNORM;
                color_image.image_type = VK_IMAGE_TYPE_2D;
                color_image.extent.width = 16;
                color_image.extent.height = 16;
                color_image.extent.depth = 1;
                color_image.mip_levels = 1;
                color_image.array_layers = 1;
                color_image.samples = VK_SAMPLE_COUNT_1_BIT;
                color_image.usage = VK_IMAGE_USAGE_SAMPLED_BIT;
                image_register(&color_image);

                VkImageViewCreateInfo view;
                memset(&view, 0, sizeof(view));
                view.sType = VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO;
                view.image = pdocker_vk_image_to_handle(&color_image);
                view.viewType = VK_IMAGE_VIEW_TYPE_2D;
                view.format = color_image.format;
                view.subresourceRange.aspectMask = VK_IMAGE_ASPECT_PLANE_0_BIT;
                view.subresourceRange.baseMipLevel = 0;
                view.subresourceRange.levelCount = 1;
                view.subresourceRange.baseArrayLayer = 0;
                view.subresourceRange.layerCount = 1;
                if (validate_image_view_create_info_for_transport(VK_NULL_HANDLE, &view, NULL) != VK_ERROR_FORMAT_NOT_SUPPORTED) {{
                    fprintf(stderr, "plane-aspect image view was accepted as byte-linear color\\n");
                    return 4;
                }}
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


    def test_msaa_sample_counts_are_limited_to_color_attachment_requests(self):
        source = textwrap.dedent(
            r"""
            #include <stdint.h>
            #include <stdio.h>
            #include <stdlib.h>
            #include <string.h>
            #include <sys/socket.h>
            #include <sys/un.h>
            #include <sys/wait.h>
            #include <unistd.h>
            #include "__ICD_SOURCE__"

            static int write_full(int fd, const char *buf, size_t len) {
                size_t off = 0;
                while (off < len) {
                    ssize_t written = write(fd, buf + off, len - off);
                    if (written <= 0) return -1;
                    off += (size_t)written;
                }
                return 0;
            }

            static pid_t start_caps_server(const char *path, const char *response) {
                int listen_fd = socket(AF_UNIX, SOCK_STREAM, 0);
                if (listen_fd < 0) return -1;
                unlink(path);
                struct sockaddr_un addr;
                memset(&addr, 0, sizeof(addr));
                addr.sun_family = AF_UNIX;
                snprintf(addr.sun_path, sizeof(addr.sun_path), "%s", path);
                if (bind(listen_fd, (const struct sockaddr *)&addr, sizeof(addr)) != 0 ||
                    listen(listen_fd, 1) != 0) {
                    close(listen_fd);
                    return -1;
                }
                pid_t pid = fork();
                if (pid < 0) {
                    close(listen_fd);
                    return -1;
                }
                if (pid == 0) {
                    int client_fd = accept(listen_fd, NULL, NULL);
                    if (client_fd < 0) _exit(11);
                    char command[128];
                    (void)read(client_fd, command, sizeof(command));
                    int rc = write_full(client_fd, response, strlen(response));
                    close(client_fd);
                    close(listen_fd);
                    _exit(rc == 0 ? 0 : 12);
                }
                close(listen_fd);
                return pid;
            }

            static int build_caps_json(char *json, size_t size) {
                size_t off = 0;
                off += (size_t)snprintf(json + off, size - off,
                    "{\"schema\":\"skydnir-vulkan-advertisement-caps-v1\","
                    "\"apiVersion\":%u,\"format_caps_schema\":1,\"format_caps_count\":%zu,"
                    "\"vulkan_dispatch_v5_supported_minors\":[0,1,2,3,4,5,6,7],"
                    "\"vulkan_graphics_v6_supported_minors\":[0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30],"
                    "\"image_format_caps\":{",
                    (unsigned)VK_API_VERSION_1_2,
                    pdocker_vk_bridge_format_count());
                for (size_t i = 0; i < pdocker_vk_bridge_format_count(); ++i) {
                    VkFormat format = pdocker_vk_bridge_format_at(i);
                    VkFormatFeatureFlags features = pdocker_vk_transport_image_features(format);
                    VkSampleCountFlags samples = VK_SAMPLE_COUNT_1_BIT;
                    if (format == VK_FORMAT_R8G8B8A8_UNORM) {
                        features |= VK_FORMAT_FEATURE_COLOR_ATTACHMENT_BIT |
                                    VK_FORMAT_FEATURE_TRANSFER_SRC_BIT |
                                    VK_FORMAT_FEATURE_TRANSFER_DST_BIT |
                                    VK_FORMAT_FEATURE_SAMPLED_IMAGE_BIT |
                                    VK_FORMAT_FEATURE_STORAGE_IMAGE_BIT;
                        samples = VK_SAMPLE_COUNT_1_BIT | VK_SAMPLE_COUNT_4_BIT;
                    }
                    off += (size_t)snprintf(json + off, size - off,
                        "%s\"fmt%dOptimalFeatures\":%u,\"fmt%dSampleCounts\":%u",
                        i ? "," : "",
                        (int)format, (unsigned)features,
                        (int)format, (unsigned)samples);
                }
                off += (size_t)snprintf(json + off, size - off, "}}}\n");
                return off < size ? 0 : -1;
            }

            static int expect_query_samples(
                    VkImageUsageFlags usage,
                    VkImageCreateFlags flags,
                    VkImageType type,
                    VkSampleCountFlags expected,
                    int code) {
                VkPhysicalDevice physical = (VkPhysicalDevice)physical_device_for_instance(NULL);
                VkImageFormatProperties props;
                memset(&props, 0xff, sizeof(props));
                VkResult rc = vkGetPhysicalDeviceImageFormatProperties(
                    physical,
                    VK_FORMAT_R8G8B8A8_UNORM,
                    type,
                    VK_IMAGE_TILING_OPTIMAL,
                    usage,
                    flags,
                    &props);
                if (rc != VK_SUCCESS) {
                    fprintf(stderr, "case %d image format query failed: %d\n", code, rc);
                    return code;
                }
                if (props.sampleCounts != expected) {
                    fprintf(stderr, "case %d sampleCounts=0x%x expected=0x%x\n",
                            code, (unsigned)props.sampleCounts, (unsigned)expected);
                    return code + 100;
                }
                return 0;
            }

            static int expect_create_image(
                    VkImageUsageFlags usage,
                    VkSampleCountFlagBits samples,
                    VkResult expected,
                    int code) {
                VkImageCreateInfo info;
                memset(&info, 0, sizeof(info));
                info.sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO;
                info.imageType = VK_IMAGE_TYPE_2D;
                info.format = VK_FORMAT_R8G8B8A8_UNORM;
                info.extent.width = 64;
                info.extent.height = 64;
                info.extent.depth = 1;
                info.mipLevels = 1;
                info.arrayLayers = 1;
                info.samples = samples;
                info.tiling = VK_IMAGE_TILING_OPTIMAL;
                info.usage = usage;
                info.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
                info.initialLayout = VK_IMAGE_LAYOUT_UNDEFINED;
                VkImage image = (VkImage)(uintptr_t)0x1234u;
                VkResult rc = vkCreateImage(VK_NULL_HANDLE, &info, NULL, &image);
                if (rc != expected) {
                    fprintf(stderr, "case %d create image rc=%d expected=%d\n", code, rc, expected);
                    return code;
                }
                if (rc == VK_SUCCESS) {
                    if (image == VK_NULL_HANDLE) return code + 100;
                    vkDestroyImage(VK_NULL_HANDLE, image, NULL);
                } else if (image != VK_NULL_HANDLE) {
                    fprintf(stderr, "case %d failed create left image handle %p\n", code, (void *)image);
                    return code + 200;
                }
                return 0;
            }

            int main(void) {
                char json[65536];
                if (build_caps_json(json, sizeof(json)) != 0) {
                    fprintf(stderr, "caps json overflow\n");
                    return 2;
                }
                char dir_template[] = "/tmp/skydnir-msaa-caps-XXXXXX";
                char *dir = mkdtemp(dir_template);
                if (!dir) return 3;
                char socket_path[256];
                snprintf(socket_path, sizeof(socket_path), "%s/caps.sock", dir);
                pid_t server = start_caps_server(socket_path, json);
                if (server <= 0) return 4;

                setenv("PDOCKER_GPU_QUEUE_SOCKET", socket_path, 1);
                setenv("PDOCKER_VULKAN_ADVERTISEMENT_SOURCE", "executor", 1);
                setenv("PDOCKER_VULKAN_HEAP_BYTES", "2147483648", 1);
                setenv("PDOCKER_VULKAN_MAX_BUFFER_BYTES", "2147483648", 1);

                VkPhysicalDeviceProperties device_props;
                memset(&device_props, 0, sizeof(device_props));
                vkGetPhysicalDeviceProperties((VkPhysicalDevice)physical_device_for_instance(NULL), &device_props);

                int status = 0;
                if (waitpid(server, &status, 0) != server || !WIFEXITED(status) ||
                    WEXITSTATUS(status) != 0) {
                    fprintf(stderr, "caps server failed status=0x%x\n", status);
                    return 5;
                }

                if ((device_props.limits.framebufferColorSampleCounts & VK_SAMPLE_COUNT_4_BIT) == 0) {
                    fprintf(stderr, "framebuffer color sample counts did not include 4x: 0x%x\n",
                            (unsigned)device_props.limits.framebufferColorSampleCounts);
                    return 6;
                }
                if (device_props.limits.sampledImageColorSampleCounts != VK_SAMPLE_COUNT_1_BIT ||
                    device_props.limits.storageImageSampleCounts != VK_SAMPLE_COUNT_1_BIT ||
                    device_props.limits.framebufferDepthSampleCounts != VK_SAMPLE_COUNT_1_BIT ||
                    device_props.limits.framebufferStencilSampleCounts != VK_SAMPLE_COUNT_1_BIT) {
                    fprintf(stderr, "non-color-attachment sample-count lanes advertised MSAA\n");
                    return 7;
                }

                const VkSampleCountFlags msaa = VK_SAMPLE_COUNT_1_BIT | VK_SAMPLE_COUNT_4_BIT;
                if (expect_query_samples(VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT, 0,
                                         VK_IMAGE_TYPE_2D, msaa, 10)) return 10;
                if (expect_query_samples(VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT, 0,
                                         VK_IMAGE_TYPE_2D, msaa, 11)) return 11;
                if (expect_query_samples(VK_IMAGE_USAGE_SAMPLED_BIT, 0,
                                         VK_IMAGE_TYPE_2D, VK_SAMPLE_COUNT_1_BIT, 12)) return 12;
                if (expect_query_samples(VK_IMAGE_USAGE_STORAGE_BIT, 0,
                                         VK_IMAGE_TYPE_2D, VK_SAMPLE_COUNT_1_BIT, 13)) return 13;
                if (expect_query_samples(VK_IMAGE_USAGE_TRANSFER_SRC_BIT, 0,
                                         VK_IMAGE_TYPE_2D, VK_SAMPLE_COUNT_1_BIT, 14)) return 14;
                if (expect_query_samples(VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT | VK_IMAGE_USAGE_SAMPLED_BIT, 0,
                                         VK_IMAGE_TYPE_2D, VK_SAMPLE_COUNT_1_BIT, 15)) return 15;
                if (expect_query_samples(VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT,
                                         VK_IMAGE_CREATE_CUBE_COMPATIBLE_BIT,
                                         VK_IMAGE_TYPE_2D, VK_SAMPLE_COUNT_1_BIT, 16)) return 16;
                if (expect_query_samples(VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT, 0,
                                         VK_IMAGE_TYPE_3D, VK_SAMPLE_COUNT_1_BIT, 17)) return 17;

                if (expect_create_image(VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT,
                                        VK_SAMPLE_COUNT_4_BIT, VK_SUCCESS, 20)) return 20;
                if (expect_create_image(VK_IMAGE_USAGE_SAMPLED_BIT,
                                        VK_SAMPLE_COUNT_4_BIT, VK_ERROR_FORMAT_NOT_SUPPORTED, 21)) return 21;
                if (expect_create_image(VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT | VK_IMAGE_USAGE_SAMPLED_BIT,
                                        VK_SAMPLE_COUNT_4_BIT, VK_ERROR_FORMAT_NOT_SUPPORTED, 22)) return 22;
                if (expect_create_image(VK_IMAGE_USAGE_SAMPLED_BIT,
                                        VK_SAMPLE_COUNT_1_BIT, VK_SUCCESS, 23)) return 23;
                return 0;
            }
            """
        ).replace("__ICD_SOURCE__", str(ICD_SOURCE))
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


    def test_device_owner_rejects_cross_device_memory_resource_misuse(self):
        source = textwrap.dedent("""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "__ICD_SOURCE__"

            static VkDevice make_device(void) {
                VkDeviceCreateInfo info;
                memset(&info, 0, sizeof(info));
                info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                VkDevice device = VK_NULL_HANDLE;
                if (vkCreateDevice((VkPhysicalDevice)&g_device, &info, NULL, &device) != VK_SUCCESS) return VK_NULL_HANDLE;
                return device;
            }

            static VkBuffer make_storage_buffer(VkDevice device) {
                VkBufferCreateInfo info;
                memset(&info, 0, sizeof(info));
                info.sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO;
                info.size = 256;
                info.usage = VK_BUFFER_USAGE_STORAGE_BUFFER_BIT | VK_BUFFER_USAGE_STORAGE_TEXEL_BUFFER_BIT;
                info.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
                VkBuffer buffer = VK_NULL_HANDLE;
                VkResult rc = vkCreateBuffer(device, &info, NULL, &buffer);
                if (rc != VK_SUCCESS) { fprintf(stderr, "make_storage_buffer rc=%d\\n", rc); return VK_NULL_HANDLE; }
                return buffer;
            }

            static VkImage make_color_image(VkDevice device) {
                PdockerVkImage *image = pdocker_alloc_handle(sizeof(*image));
                if (!image) return VK_NULL_HANDLE;
                memset(image, 0, sizeof(*image));
                image->object_id = next_vulkan_object_generation();
                image->owner_device_id = device_owner_id_or_zero(device);
                image->image_type = VK_IMAGE_TYPE_2D;
                image->format = VK_FORMAT_R8G8B8A8_UNORM;
                image->extent = (VkExtent3D){16, 16, 1};
                image->mip_levels = 1;
                image->array_layers = 1;
                image->samples = VK_SAMPLE_COUNT_1_BIT;
                image->tiling = VK_IMAGE_TILING_OPTIMAL;
                image->usage = VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT;
                image->sharing_mode = VK_SHARING_MODE_EXCLUSIVE;
                image->initial_layout = VK_IMAGE_LAYOUT_UNDEFINED;
                image->current_layout = VK_IMAGE_LAYOUT_UNDEFINED;
                image->requirements_size = 4096;
                image->requirements_alignment = PDOCKER_VK_REQUIREMENT_ALIGNMENT;
                image->memory_type_bits = 1;
                image->generation = next_vulkan_object_generation();
                image_register(image);
                return pdocker_vk_image_to_handle(image);
            }

            static VkDeviceMemory make_memory_type(VkDevice device, uint32_t memory_type_index) {
                VkMemoryAllocateInfo info;
                memset(&info, 0, sizeof(info));
                info.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO;
                info.allocationSize = 4096;
                info.memoryTypeIndex = memory_type_index;
                VkDeviceMemory memory = VK_NULL_HANDLE;
                VkResult rc = vkAllocateMemory(device, &info, NULL, &memory);
                if (rc != VK_SUCCESS) { fprintf(stderr, "make_memory type=%u rc=%d\\n", memory_type_index, rc); return VK_NULL_HANDLE; }
                return memory;
            }

            static VkDeviceMemory make_memory(VkDevice device) {
                return make_memory_type(device, 0);
            }

            int main(void) {
                VkDevice device_a = make_device();
                VkDevice device_b = make_device();
                if (!device_a || !device_b || device_a == device_b) return 1;

                VkBuffer buffer_a = make_storage_buffer(device_a);
                VkBuffer buffer_b = make_storage_buffer(device_b);
                VkImage image_a = make_color_image(device_a);
                VkImage image_b = make_color_image(device_b);
                VkDeviceMemory memory_a = make_memory(device_a);
                VkDeviceMemory memory_b = make_memory(device_b);
                VkDeviceMemory host_memory_a = make_memory_type(device_a, 1);
                if (!buffer_a || !buffer_b || !image_a || !image_b || !memory_a || !memory_b || !host_memory_a) {
                    fprintf(stderr, "created buffer_a=%p buffer_b=%p image_a=%p image_b=%p memory_a=%p memory_b=%p host_memory_a=%p\\n",
                            (void *)buffer_a, (void *)buffer_b, (void *)image_a, (void *)image_b,
                            (void *)memory_a, (void *)memory_b, (void *)host_memory_a);
                    return 2;
                }

                if (!buffer_handle_lookup_for_device(device_a, buffer_a)) return 3;
                if (buffer_handle_lookup_for_device(device_b, buffer_a)) return 4;
                if (!memory_handle_lookup_for_device(device_a, memory_a)) return 5;
                if (memory_handle_lookup_for_device(device_b, memory_a)) return 6;

                VkMemoryDedicatedAllocateInfo dedicated;
                VkMemoryAllocateInfo dedicated_alloc;
                memset(&dedicated, 0, sizeof(dedicated));
                memset(&dedicated_alloc, 0, sizeof(dedicated_alloc));
                dedicated.sType = VK_STRUCTURE_TYPE_MEMORY_DEDICATED_ALLOCATE_INFO;
                dedicated.buffer = buffer_b;
                dedicated_alloc.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO;
                dedicated_alloc.pNext = &dedicated;
                dedicated_alloc.allocationSize = 4096;
                dedicated_alloc.memoryTypeIndex = 0;
                VkDeviceMemory wrong_dedicated_memory = VK_NULL_HANDLE;
                if (vkAllocateMemory(device_a, &dedicated_alloc, NULL, &wrong_dedicated_memory) != VK_ERROR_INITIALIZATION_FAILED) return 141;
                if (wrong_dedicated_memory != VK_NULL_HANDLE) return 142;
                dedicated.buffer = VK_NULL_HANDLE;
                dedicated.image = image_b;
                if (vkAllocateMemory(device_a, &dedicated_alloc, NULL, &wrong_dedicated_memory) != VK_ERROR_INITIALIZATION_FAILED) return 143;
                if (wrong_dedicated_memory != VK_NULL_HANDLE) return 144;
                dedicated.image = VK_NULL_HANDLE;
                dedicated.buffer = buffer_a;
                VkDeviceMemory same_device_dedicated_memory = VK_NULL_HANDLE;
                if (vkAllocateMemory(device_a, &dedicated_alloc, NULL, &same_device_dedicated_memory) != VK_SUCCESS) return 145;
                PdockerVkMemory *same_device_dedicated = memory_handle_lookup_for_device(device_a, same_device_dedicated_memory);
                if (!same_device_dedicated || same_device_dedicated->dedicated_buffer != buffer_handle_lookup_for_device(device_a, buffer_a) ||
                    same_device_dedicated->dedicated_image != NULL) return 146;

                VkMemoryRequirements req;
                memset(&req, 0, sizeof(req));
                vkGetBufferMemoryRequirements(device_a, buffer_a, &req);
                if (req.size == 0 || req.memoryTypeBits == 0) return 21;
                memset(&req, 0xff, sizeof(req));
                vkGetBufferMemoryRequirements(device_b, buffer_a, &req);
                if (req.size != 0 || req.memoryTypeBits != 0) return 22;

                VkBufferMemoryRequirementsInfo2 buffer_req_info;
                VkMemoryRequirements2 req2;
                memset(&buffer_req_info, 0, sizeof(buffer_req_info));
                memset(&req2, 0, sizeof(req2));
                buffer_req_info.sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_REQUIREMENTS_INFO_2;
                buffer_req_info.buffer = buffer_a;
                req2.sType = VK_STRUCTURE_TYPE_MEMORY_REQUIREMENTS_2;
                vkGetBufferMemoryRequirements2(device_b, &buffer_req_info, &req2);
                if (req2.memoryRequirements.size != 0 || req2.memoryRequirements.memoryTypeBits != 0) return 23;

                memset(&req, 0, sizeof(req));
                vkGetImageMemoryRequirements(device_a, image_a, &req);
                if (req.size == 0 || req.memoryTypeBits == 0) return 24;
                memset(&req, 0xff, sizeof(req));
                vkGetImageMemoryRequirements(device_b, image_a, &req);
                if (req.size != 0 || req.memoryTypeBits != 0) return 25;

                VkImageMemoryRequirementsInfo2 image_req_info;
                memset(&image_req_info, 0, sizeof(image_req_info));
                memset(&req2, 0, sizeof(req2));
                image_req_info.sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_REQUIREMENTS_INFO_2;
                image_req_info.image = image_a;
                req2.sType = VK_STRUCTURE_TYPE_MEMORY_REQUIREMENTS_2;
                vkGetImageMemoryRequirements2(device_b, &image_req_info, &req2);
                if (req2.memoryRequirements.size != 0 || req2.memoryRequirements.memoryTypeBits != 0) return 26;

                VkImageSubresource subresource;
                memset(&subresource, 0, sizeof(subresource));
                subresource.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
                VkSubresourceLayout layout;
                memset(&layout, 0, sizeof(layout));
                vkGetImageSubresourceLayout(device_a, image_a, &subresource, &layout);
                if (layout.size == 0 || layout.rowPitch == 0) return 27;
                memset(&layout, 0xff, sizeof(layout));
                vkGetImageSubresourceLayout(device_b, image_a, &subresource, &layout);
                if (layout.size != 0 || layout.rowPitch != 0 || layout.depthPitch != 0 || layout.arrayPitch != 0) return 28;

#ifdef VK_EXT_VALIDATION_CACHE_EXTENSION_NAME
                VkValidationCacheCreateInfoEXT validation_cache_info;
                memset(&validation_cache_info, 0, sizeof(validation_cache_info));
                validation_cache_info.sType = VK_STRUCTURE_TYPE_VALIDATION_CACHE_CREATE_INFO_EXT;
                VkValidationCacheEXT validation_cache_a = VK_NULL_HANDLE;
                if (vkCreateValidationCacheEXT(device_a, &validation_cache_info, NULL, &validation_cache_a) != VK_SUCCESS ||
                    validation_cache_a == VK_NULL_HANDLE) return 31;
                size_t validation_cache_size = 99;
                if (vkGetValidationCacheDataEXT(device_b, validation_cache_a, &validation_cache_size, NULL) == VK_SUCCESS) return 32;
                if (vkMergeValidationCachesEXT(device_b, validation_cache_a, 1, &validation_cache_a) == VK_SUCCESS) return 33;
                vkDestroyValidationCacheEXT(device_b, validation_cache_a, NULL);
                validation_cache_size = 99;
                if (vkGetValidationCacheDataEXT(device_a, validation_cache_a, &validation_cache_size, NULL) != VK_SUCCESS ||
                    validation_cache_size != 0) return 34;
                vkDestroyValidationCacheEXT(device_a, validation_cache_a, NULL);
                validation_cache_size = 99;
                if (vkGetValidationCacheDataEXT(device_a, validation_cache_a, &validation_cache_size, NULL) == VK_SUCCESS) return 35;
#endif

#ifdef VK_EXT_PRIVATE_DATA_EXTENSION_NAME
                VkPrivateDataSlotCreateInfo slot_info;
                memset(&slot_info, 0, sizeof(slot_info));
                slot_info.sType = VK_STRUCTURE_TYPE_PRIVATE_DATA_SLOT_CREATE_INFO;
                VkPrivateDataSlot private_slot_a = VK_NULL_HANDLE;
                if (vkCreatePrivateDataSlot(device_a, &slot_info, NULL, &private_slot_a) != VK_SUCCESS ||
                    private_slot_a == VK_NULL_HANDLE) return 41;
                if (vkSetPrivateData(device_b, VK_OBJECT_TYPE_BUFFER, (uint64_t)(uintptr_t)buffer_a, private_slot_a, 0x123u) == VK_SUCCESS) return 42;
                uint64_t private_data = 77;
                vkGetPrivateData(device_b, VK_OBJECT_TYPE_BUFFER, (uint64_t)(uintptr_t)buffer_a, private_slot_a, &private_data);
                if (private_data != 0) return 43;
                if (vkSetPrivateData(device_a, VK_OBJECT_TYPE_BUFFER, (uint64_t)(uintptr_t)buffer_a, private_slot_a, 0x456u) != VK_SUCCESS) return 44;
                private_data = 0;
                vkGetPrivateData(device_a, VK_OBJECT_TYPE_BUFFER, (uint64_t)(uintptr_t)buffer_a, private_slot_a, &private_data);
                if (private_data != 0x456u) return 45;
                if (vkSetPrivateData(device_a, VK_OBJECT_TYPE_BUFFER, (uint64_t)(uintptr_t)buffer_b, private_slot_a, 0x789u) == VK_SUCCESS) return 48;
                private_data = 77;
                vkGetPrivateData(device_a, VK_OBJECT_TYPE_BUFFER, (uint64_t)(uintptr_t)buffer_b, private_slot_a, &private_data);
                if (private_data != 0) return 49;
                vkDestroyPrivateDataSlot(device_b, private_slot_a, NULL);
                private_data = 0;
                vkGetPrivateData(device_a, VK_OBJECT_TYPE_BUFFER, (uint64_t)(uintptr_t)buffer_a, private_slot_a, &private_data);
                if (private_data != 0x456u) return 46;
                vkDestroyPrivateDataSlot(device_a, private_slot_a, NULL);
                private_data = 77;
                vkGetPrivateData(device_a, VK_OBJECT_TYPE_BUFFER, (uint64_t)(uintptr_t)buffer_a, private_slot_a, &private_data);
                if (private_data != 0) return 47;
#endif

#ifdef VK_EXT_DEBUG_UTILS_EXTENSION_NAME
                VkDebugUtilsObjectNameInfoEXT debug_name;
                memset(&debug_name, 0, sizeof(debug_name));
                debug_name.sType = VK_STRUCTURE_TYPE_DEBUG_UTILS_OBJECT_NAME_INFO_EXT;
                debug_name.objectType = VK_OBJECT_TYPE_BUFFER;
                debug_name.objectHandle = (uint64_t)(uintptr_t)buffer_a;
                debug_name.pObjectName = "buffer-a";
                if (vkSetDebugUtilsObjectNameEXT(device_a, &debug_name) != VK_SUCCESS) return 51;
                debug_name.objectHandle = (uint64_t)(uintptr_t)buffer_b;
                if (vkSetDebugUtilsObjectNameEXT(device_a, &debug_name) == VK_SUCCESS) return 52;

                VkDebugUtilsObjectTagInfoEXT debug_tag;
                memset(&debug_tag, 0, sizeof(debug_tag));
                debug_tag.sType = VK_STRUCTURE_TYPE_DEBUG_UTILS_OBJECT_TAG_INFO_EXT;
                debug_tag.objectType = VK_OBJECT_TYPE_BUFFER;
                debug_tag.objectHandle = (uint64_t)(uintptr_t)buffer_a;
                debug_tag.tagName = 1;
                debug_tag.tagSize = 0;
                debug_tag.pTag = NULL;
                if (vkSetDebugUtilsObjectTagEXT(device_a, &debug_tag) != VK_SUCCESS) return 53;
                debug_tag.objectHandle = (uint64_t)(uintptr_t)buffer_b;
                if (vkSetDebugUtilsObjectTagEXT(device_a, &debug_tag) == VK_SUCCESS) return 54;
#endif

#ifdef VK_EXT_DEBUG_MARKER_EXTENSION_NAME
                VkDebugMarkerObjectNameInfoEXT marker_name;
                memset(&marker_name, 0, sizeof(marker_name));
                marker_name.sType = VK_STRUCTURE_TYPE_DEBUG_MARKER_OBJECT_NAME_INFO_EXT;
                marker_name.objectType = VK_DEBUG_REPORT_OBJECT_TYPE_BUFFER_EXT;
                marker_name.object = (uint64_t)(uintptr_t)buffer_a;
                marker_name.pObjectName = "buffer-a";
                if (vkDebugMarkerSetObjectNameEXT(device_a, &marker_name) != VK_SUCCESS) return 61;
                marker_name.object = (uint64_t)(uintptr_t)buffer_b;
                if (vkDebugMarkerSetObjectNameEXT(device_a, &marker_name) == VK_SUCCESS) return 62;

                VkDebugMarkerObjectTagInfoEXT marker_tag;
                memset(&marker_tag, 0, sizeof(marker_tag));
                marker_tag.sType = VK_STRUCTURE_TYPE_DEBUG_MARKER_OBJECT_TAG_INFO_EXT;
                marker_tag.objectType = VK_DEBUG_REPORT_OBJECT_TYPE_BUFFER_EXT;
                marker_tag.object = (uint64_t)(uintptr_t)buffer_a;
                marker_tag.tagName = 1;
                marker_tag.tagSize = 0;
                marker_tag.pTag = NULL;
                if (vkDebugMarkerSetObjectTagEXT(device_a, &marker_tag) != VK_SUCCESS) return 63;
                marker_tag.object = (uint64_t)(uintptr_t)buffer_b;
                if (vkDebugMarkerSetObjectTagEXT(device_a, &marker_tag) == VK_SUCCESS) return 64;
#endif

                VkMappedMemoryRange range;
                memset(&range, 0, sizeof(range));
                range.sType = VK_STRUCTURE_TYPE_MAPPED_MEMORY_RANGE;
                range.memory = host_memory_a;
                range.offset = 0;
                range.size = 64;
                if (vkFlushMappedMemoryRanges(device_b, 1, &range) == VK_SUCCESS) return 101;
                if (vkInvalidateMappedMemoryRanges(device_b, 1, &range) == VK_SUCCESS) return 102;
                if (vkFlushMappedMemoryRanges(device_a, 1, &range) != VK_SUCCESS) return 103;
                range.offset = 4097;
                if (vkFlushMappedMemoryRanges(device_a, 1, &range) == VK_SUCCESS) return 104;
                range.offset = 0;
                range.size = 4097;
                if (vkInvalidateMappedMemoryRanges(device_a, 1, &range) == VK_SUCCESS) return 105;
                range.size = VK_WHOLE_SIZE;
                if (vkInvalidateMappedMemoryRanges(device_a, 1, &range) != VK_SUCCESS) return 106;
                if (vkFlushMappedMemoryRanges(device_a, 1, NULL) == VK_SUCCESS) return 107;

                if (vkBindBufferMemory(device_b, buffer_a, memory_a, 0) != VK_ERROR_INITIALIZATION_FAILED) return 7;
                if (buffer_handle_lookup_for_device(device_a, buffer_a)->memory != NULL) return 147;
                if (vkBindBufferMemory(device_a, buffer_a, memory_b, 0) != VK_ERROR_INITIALIZATION_FAILED) return 8;
                if (buffer_handle_lookup_for_device(device_a, buffer_a)->memory != NULL) return 148;
                if (vkBindBufferMemory(device_a, buffer_a, memory_a, 0) != VK_SUCCESS) return 9;

                if (vkBindImageMemory(device_b, image_a, memory_a, 0) != VK_ERROR_INITIALIZATION_FAILED) return 10;
                if (image_handle_lookup_for_device(device_a, image_a)->memory != NULL) return 149;
                if (vkBindImageMemory(device_a, image_a, memory_b, 0) != VK_ERROR_INITIALIZATION_FAILED) return 11;
                if (image_handle_lookup_for_device(device_a, image_a)->memory != NULL) return 150;
                if (vkBindImageMemory(device_a, image_a, memory_a, 0) != VK_SUCCESS) return 12;

                VkBufferViewCreateInfo buffer_view_info;
                memset(&buffer_view_info, 0, sizeof(buffer_view_info));
                buffer_view_info.sType = VK_STRUCTURE_TYPE_BUFFER_VIEW_CREATE_INFO;
                buffer_view_info.buffer = buffer_a;
                buffer_view_info.format = VK_FORMAT_R32_UINT;
                buffer_view_info.offset = 0;
                buffer_view_info.range = 64;
                VkBufferView buffer_view = VK_NULL_HANDLE;
                if (vkCreateBufferView(device_b, &buffer_view_info, NULL, &buffer_view) != VK_ERROR_INITIALIZATION_FAILED) return 13;
                if (buffer_view != VK_NULL_HANDLE) return 14;
                PdockerVkBufferView *manual_buffer_view = pdocker_alloc_handle(sizeof(*manual_buffer_view));
                if (!manual_buffer_view) return 15;
                memset(manual_buffer_view, 0, sizeof(*manual_buffer_view));
                manual_buffer_view->object_id = next_vulkan_object_generation();
                manual_buffer_view->owner_device_id = device_owner_id_or_zero(device_a);
                manual_buffer_view->buffer = buffer_handle_lookup_for_device(device_a, buffer_a);
                manual_buffer_view->format = VK_FORMAT_R32_UINT;
                manual_buffer_view->offset = 0;
                manual_buffer_view->range = 64;
                manual_buffer_view->generation = next_vulkan_object_generation();
                buffer_view_register(manual_buffer_view);
                buffer_view = pdocker_vk_buffer_view_to_handle(manual_buffer_view);

                VkImageViewCreateInfo image_view_info;
                memset(&image_view_info, 0, sizeof(image_view_info));
                image_view_info.sType = VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO;
                image_view_info.image = image_a;
                image_view_info.viewType = VK_IMAGE_VIEW_TYPE_2D;
                image_view_info.format = VK_FORMAT_R8G8B8A8_UNORM;
                image_view_info.subresourceRange.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
                image_view_info.subresourceRange.baseMipLevel = 0;
                image_view_info.subresourceRange.levelCount = 1;
                image_view_info.subresourceRange.baseArrayLayer = 0;
                image_view_info.subresourceRange.layerCount = 1;
                VkImageView image_view = VK_NULL_HANDLE;
                if (vkCreateImageView(device_b, &image_view_info, NULL, &image_view) != VK_ERROR_INITIALIZATION_FAILED) return 16;
                if (image_view != VK_NULL_HANDLE) return 17;
                if (vkCreateImageView(device_a, &image_view_info, NULL, &image_view) != VK_SUCCESS || !image_view) return 18;

                VkSamplerCreateInfo sampler_info;
                memset(&sampler_info, 0, sizeof(sampler_info));
                sampler_info.sType = VK_STRUCTURE_TYPE_SAMPLER_CREATE_INFO;
                sampler_info.magFilter = VK_FILTER_NEAREST;
                sampler_info.minFilter = VK_FILTER_NEAREST;
                sampler_info.mipmapMode = VK_SAMPLER_MIPMAP_MODE_NEAREST;
                sampler_info.addressModeU = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
                sampler_info.addressModeV = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
                sampler_info.addressModeW = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
                sampler_info.maxLod = 1.0f;
                VkSampler sampler_b = VK_NULL_HANDLE;
                if (vkCreateSampler(device_b, &sampler_info, NULL, &sampler_b) != VK_SUCCESS || !sampler_b) return 151;
                VkDescriptorSetLayoutBinding sampler_binding;
                memset(&sampler_binding, 0, sizeof(sampler_binding));
                sampler_binding.binding = 0;
                sampler_binding.descriptorType = VK_DESCRIPTOR_TYPE_SAMPLER;
                sampler_binding.descriptorCount = 1;
                sampler_binding.stageFlags = VK_SHADER_STAGE_COMPUTE_BIT;
                sampler_binding.pImmutableSamplers = &sampler_b;
                VkDescriptorSetLayoutCreateInfo sampler_layout_info;
                memset(&sampler_layout_info, 0, sizeof(sampler_layout_info));
                sampler_layout_info.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO;
                sampler_layout_info.bindingCount = 1;
                sampler_layout_info.pBindings = &sampler_binding;
                VkDescriptorSetLayout sampler_layout = VK_NULL_HANDLE;
                if (vkCreateDescriptorSetLayout(device_a, &sampler_layout_info, NULL, &sampler_layout) == VK_SUCCESS) return 152;
                if (sampler_layout != VK_NULL_HANDLE) return 153;
                if (vkCreateDescriptorSetLayout(device_b, &sampler_layout_info, NULL, &sampler_layout) != VK_SUCCESS || !sampler_layout) return 154;
                vkDestroyDescriptorSetLayout(device_b, sampler_layout, NULL);
                vkDestroySampler(device_b, sampler_b, NULL);

                vkDestroyBuffer(device_b, buffer_a, NULL);
                if (!buffer_handle_lookup_for_device(device_a, buffer_a)) return 19;
                vkFreeMemory(device_b, memory_a, NULL);
                if (!memory_handle_lookup_for_device(device_a, memory_a)) return 20;
                vkDestroyImage(device_b, image_a, NULL);
                if (!image_handle_lookup_for_device(device_a, image_a)) return 21;
                vkDestroyBufferView(device_b, buffer_view, NULL);
                if (!buffer_view_handle_lookup_for_device(device_a, buffer_view)) return 22;
                vkDestroyImageView(device_b, image_view, NULL);
                if (!image_view_handle_lookup_for_device(device_a, image_view)) return 23;

                vkDestroyDevice(device_a, NULL);
                if (buffer_handle_lookup(buffer_a) || image_handle_lookup(image_a) || memory_handle_resolve(memory_a, NULL)) return 24;
                if (!buffer_handle_lookup_for_device(device_b, buffer_b)) return 25;
                if (!image_handle_lookup_for_device(device_b, image_b)) return 26;
                if (!memory_handle_lookup_for_device(device_b, memory_b)) return 27;
                vkDestroyDevice(device_b, NULL);
                return 0;
            }
            """).replace("__ICD_SOURCE__", str(ICD_SOURCE))
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


    def test_command_recording_rejects_cross_device_transfer_resources(self):
        source = textwrap.dedent("""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "__ICD_SOURCE__"

            static VkDevice make_device(void) {
                VkDeviceCreateInfo info;
                memset(&info, 0, sizeof(info));
                info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                VkDevice device = VK_NULL_HANDLE;
                if (vkCreateDevice((VkPhysicalDevice)&g_device, &info, NULL, &device) != VK_SUCCESS) return VK_NULL_HANDLE;
                return device;
            }

            static VkCommandPool make_pool(VkDevice device) {
                VkCommandPoolCreateInfo info;
                memset(&info, 0, sizeof(info));
                info.sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO;
                info.queueFamilyIndex = 0;
                VkCommandPool pool = VK_NULL_HANDLE;
                if (vkCreateCommandPool(device, &info, NULL, &pool) != VK_SUCCESS) return VK_NULL_HANDLE;
                return pool;
            }

            static VkCommandBuffer make_cmd(VkDevice device, VkCommandPool pool) {
                VkCommandBufferAllocateInfo info;
                memset(&info, 0, sizeof(info));
                info.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO;
                info.commandPool = pool;
                info.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY;
                info.commandBufferCount = 1;
                VkCommandBuffer cmd = VK_NULL_HANDLE;
                if (vkAllocateCommandBuffers(device, &info, &cmd) != VK_SUCCESS) return VK_NULL_HANDLE;
                return cmd;
            }

            static VkDeviceMemory make_memory(VkDevice device) {
                VkMemoryAllocateInfo info;
                memset(&info, 0, sizeof(info));
                info.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO;
                info.allocationSize = 4096;
                info.memoryTypeIndex = 0;
                VkDeviceMemory memory = VK_NULL_HANDLE;
                if (vkAllocateMemory(device, &info, NULL, &memory) != VK_SUCCESS) return VK_NULL_HANDLE;
                return memory;
            }

            static VkBuffer make_bound_buffer(VkDevice device) {
                VkBufferCreateInfo info;
                memset(&info, 0, sizeof(info));
                info.sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO;
                info.size = 1024;
                info.usage = VK_BUFFER_USAGE_TRANSFER_SRC_BIT |
                             VK_BUFFER_USAGE_TRANSFER_DST_BIT |
                             VK_BUFFER_USAGE_VERTEX_BUFFER_BIT |
                             VK_BUFFER_USAGE_INDEX_BUFFER_BIT |
                             VK_BUFFER_USAGE_INDIRECT_BUFFER_BIT |
                             VK_BUFFER_USAGE_STORAGE_BUFFER_BIT;
                info.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
                VkBuffer buffer = VK_NULL_HANDLE;
                if (vkCreateBuffer(device, &info, NULL, &buffer) != VK_SUCCESS) return VK_NULL_HANDLE;
                VkDeviceMemory memory = make_memory(device);
                if (!memory) return VK_NULL_HANDLE;
                if (vkBindBufferMemory(device, buffer, memory, 0) != VK_SUCCESS) return VK_NULL_HANDLE;
                return buffer;
            }

            static VkImage make_bound_image(VkDevice device) {
                PdockerVkImage *image = pdocker_alloc_handle(sizeof(*image));
                if (!image) return VK_NULL_HANDLE;
                memset(image, 0, sizeof(*image));
                image->object_id = next_vulkan_object_generation();
                image->owner_device_id = device_owner_id_or_zero(device);
                image->image_type = VK_IMAGE_TYPE_2D;
                image->format = VK_FORMAT_R8G8B8A8_UNORM;
                image->extent = (VkExtent3D){16, 16, 1};
                image->mip_levels = 1;
                image->array_layers = 1;
                image->samples = VK_SAMPLE_COUNT_1_BIT;
                image->tiling = VK_IMAGE_TILING_OPTIMAL;
                image->usage = VK_IMAGE_USAGE_TRANSFER_SRC_BIT |
                               VK_IMAGE_USAGE_TRANSFER_DST_BIT |
                               VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT;
                image->sharing_mode = VK_SHARING_MODE_EXCLUSIVE;
                image->initial_layout = VK_IMAGE_LAYOUT_UNDEFINED;
                image->current_layout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
                image->requirements_size = 4096;
                image->requirements_alignment = PDOCKER_VK_REQUIREMENT_ALIGNMENT;
                image->memory_type_bits = 1;
                image->generation = next_vulkan_object_generation();
                image_register(image);
                VkImage handle = pdocker_vk_image_to_handle(image);
                VkDeviceMemory memory = make_memory(device);
                if (!memory) return VK_NULL_HANDLE;
                if (vkBindImageMemory(device, handle, memory, 0) != VK_SUCCESS) return VK_NULL_HANDLE;
                return handle;
            }

            #define EXPECT_RECORD_FAIL(stmt, code) do { \
                VkCommandBuffer cmd = make_cmd(device_b, pool_b); \
                if (!cmd) return (code); \
                if (vkBeginCommandBuffer(cmd, NULL) != VK_SUCCESS) return (code) + 1; \
                stmt; \
                if (vkEndCommandBuffer(cmd) != VK_ERROR_FEATURE_NOT_PRESENT) return (code) + 2; \
            } while (0)

            int main(void) {
                VkDevice device_a = make_device();
                VkDevice device_b = make_device();
                if (!device_a || !device_b || device_a == device_b) return 1;
                VkCommandPool pool_b = make_pool(device_b);
                if (!pool_b) return 2;
                VkBuffer buffer_a = make_bound_buffer(device_a);
                VkBuffer buffer_b = make_bound_buffer(device_b);
                VkImage image_a = make_bound_image(device_a);
                VkImage image_b = make_bound_image(device_b);
                if (!buffer_a || !buffer_b || !image_a || !image_b) return 3;

                VkBufferCopy copy_region;
                memset(&copy_region, 0, sizeof(copy_region));
                copy_region.size = 16;
                EXPECT_RECORD_FAIL(vkCmdCopyBuffer(cmd, buffer_a, buffer_b, 1, &copy_region), 10);

                VkBufferImageCopy bic;
                memset(&bic, 0, sizeof(bic));
                bic.imageSubresource.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
                bic.imageSubresource.layerCount = 1;
                bic.imageExtent = (VkExtent3D){4, 4, 1};
                EXPECT_RECORD_FAIL(vkCmdCopyBufferToImage(cmd, buffer_b, image_a, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &bic), 20);
                EXPECT_RECORD_FAIL(vkCmdCopyImageToBuffer(cmd, image_a, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, buffer_b, 1, &bic), 30);

                VkImageCopy image_copy;
                memset(&image_copy, 0, sizeof(image_copy));
                image_copy.srcSubresource.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
                image_copy.srcSubresource.layerCount = 1;
                image_copy.dstSubresource.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
                image_copy.dstSubresource.layerCount = 1;
                image_copy.extent = (VkExtent3D){4, 4, 1};
                EXPECT_RECORD_FAIL(vkCmdCopyImage(cmd, image_a, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, image_b, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &image_copy), 40);

                VkClearColorValue color;
                memset(&color, 0, sizeof(color));
                VkImageSubresourceRange range;
                memset(&range, 0, sizeof(range));
                range.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
                range.levelCount = 1;
                range.layerCount = 1;
                EXPECT_RECORD_FAIL(vkCmdClearColorImage(cmd, image_a, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, &color, 1, &range), 50);

                EXPECT_RECORD_FAIL(vkCmdFillBuffer(cmd, buffer_a, 0, 16, 0x7fu), 60);
                uint32_t payload[4] = {1u, 2u, 3u, 4u};
                EXPECT_RECORD_FAIL(vkCmdUpdateBuffer(cmd, buffer_a, 0, sizeof(payload), payload), 70);

                VkDeviceSize offset = 0;
                EXPECT_RECORD_FAIL(vkCmdBindVertexBuffers(cmd, 0, 1, &buffer_a, &offset), 80);
                EXPECT_RECORD_FAIL(vkCmdBindIndexBuffer(cmd, buffer_a, 0, VK_INDEX_TYPE_UINT32), 90);
                EXPECT_RECORD_FAIL(vkCmdDispatchIndirect(cmd, buffer_a, 0), 100);

                VkBufferMemoryBarrier bb;
                memset(&bb, 0, sizeof(bb));
                bb.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
                bb.dstAccessMask = VK_ACCESS_TRANSFER_READ_BIT;
                bb.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
                bb.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
                bb.buffer = buffer_a;
                bb.size = VK_WHOLE_SIZE;
                EXPECT_RECORD_FAIL(vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT, 0, 0, NULL, 1, &bb, 0, NULL), 110);

                VkImageMemoryBarrier ib;
                memset(&ib, 0, sizeof(ib));
                ib.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
                ib.dstAccessMask = VK_ACCESS_TRANSFER_READ_BIT;
                ib.oldLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
                ib.newLayout = VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL;
                ib.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
                ib.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
                ib.image = image_a;
                ib.subresourceRange = range;
                EXPECT_RECORD_FAIL(vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT, 0, 0, NULL, 0, NULL, 1, &ib), 120);

                vkDestroyDevice(device_a, NULL);
                vkDestroyDevice(device_b, NULL);
                return 0;
            }
            """).replace("__ICD_SOURCE__", str(ICD_SOURCE))
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


    def test_device_owner_rejects_cross_device_command_submit_sync(self):
        source = textwrap.dedent("""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "__ICD_SOURCE__"

            static VkDevice make_device(void) {
                VkDeviceCreateInfo info;
                memset(&info, 0, sizeof(info));
                info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                VkDevice device = VK_NULL_HANDLE;
                if (vkCreateDevice((VkPhysicalDevice)&g_device, &info, NULL, &device) != VK_SUCCESS) return VK_NULL_HANDLE;
                return device;
            }

            static VkCommandPool make_pool(VkDevice device) {
                VkCommandPoolCreateInfo info;
                memset(&info, 0, sizeof(info));
                info.sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO;
                info.queueFamilyIndex = 0;
                VkCommandPool pool = VK_NULL_HANDLE;
                if (vkCreateCommandPool(device, &info, NULL, &pool) != VK_SUCCESS) return VK_NULL_HANDLE;
                return pool;
            }

            static VkCommandBuffer make_cmd(VkDevice device, VkCommandPool pool, VkCommandBufferLevel level) {
                VkCommandBufferAllocateInfo info;
                memset(&info, 0, sizeof(info));
                info.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO;
                info.commandPool = pool;
                info.level = level;
                info.commandBufferCount = 1;
                VkCommandBuffer cmd = VK_NULL_HANDLE;
                if (vkAllocateCommandBuffers(device, &info, &cmd) != VK_SUCCESS) return VK_NULL_HANDLE;
                return cmd;
            }

            static VkFence make_fence(VkDevice device, VkFenceCreateFlags flags) {
                VkFenceCreateInfo info;
                memset(&info, 0, sizeof(info));
                info.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO;
                info.flags = flags;
                VkFence fence = VK_NULL_HANDLE;
                if (vkCreateFence(device, &info, NULL, &fence) != VK_SUCCESS) return VK_NULL_HANDLE;
                return fence;
            }

            static VkSemaphore make_binary_semaphore(VkDevice device) {
                VkSemaphoreCreateInfo info;
                memset(&info, 0, sizeof(info));
                info.sType = VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO;
                VkSemaphore sem = VK_NULL_HANDLE;
                if (vkCreateSemaphore(device, &info, NULL, &sem) != VK_SUCCESS) return VK_NULL_HANDLE;
                return sem;
            }

            static VkSemaphore make_timeline_semaphore_object(VkDevice device, uint64_t value) {
                PdockerVkSemaphore *sem = pdocker_alloc_handle(sizeof(*sem));
                if (!sem) return VK_NULL_HANDLE;
                memset(sem, 0, sizeof(*sem));
                sem->timeline = true;
                sem->value = value;
                sem->signaled = value > 0;
                sem->owner_device_id = device_owner_id_or_zero(device);
                sem->semaphore_id = next_vulkan_object_generation();
                semaphore_register(sem);
                return pdocker_vk_semaphore_to_handle(sem);
            }

            static VkEvent make_event(VkDevice device) {
                VkEventCreateInfo info;
                memset(&info, 0, sizeof(info));
                info.sType = VK_STRUCTURE_TYPE_EVENT_CREATE_INFO;
                VkEvent event = VK_NULL_HANDLE;
                if (vkCreateEvent(device, &info, NULL, &event) != VK_SUCCESS) return VK_NULL_HANDLE;
                return event;
            }

            static VkQueryPool make_query_pool(VkDevice device) {
                VkQueryPoolCreateInfo info;
                memset(&info, 0, sizeof(info));
                info.sType = VK_STRUCTURE_TYPE_QUERY_POOL_CREATE_INFO;
                info.queryType = VK_QUERY_TYPE_TIMESTAMP;
                info.queryCount = 1;
                VkQueryPool pool = VK_NULL_HANDLE;
                if (vkCreateQueryPool(device, &info, NULL, &pool) != VK_SUCCESS) return VK_NULL_HANDLE;
                return pool;
            }

            int main(void) {
                VkDevice device_a = make_device();
                VkDevice device_b = make_device();
                if (!device_a || !device_b || device_a == device_b) return 1;
                VkQueue queue_b = VK_NULL_HANDLE;
                vkGetDeviceQueue(device_b, 0, 0, &queue_b);
                if (!queue_b) return 2;
                pdocker_vk_queue_from_handle(queue_b)->requested_feature_mask |= PDOCKER_VK_FEATURE_SYNCHRONIZATION_2;
                pdocker_vk_queue_from_handle(queue_b)->enabled_extension_mask |= PDOCKER_VK_DEVICE_EXT_KHR_SYNCHRONIZATION_2;

                VkCommandPool pool_a = make_pool(device_a);
                VkCommandPool pool_b = make_pool(device_b);
                VkCommandBuffer cmd_a = make_cmd(device_a, pool_a, VK_COMMAND_BUFFER_LEVEL_PRIMARY);
                VkCommandBuffer cmd_b = make_cmd(device_b, pool_b, VK_COMMAND_BUFFER_LEVEL_PRIMARY);
                VkCommandBuffer secondary_b = make_cmd(device_b, pool_b, VK_COMMAND_BUFFER_LEVEL_SECONDARY);
                if (!pool_a || !pool_b || !cmd_a || !cmd_b || !secondary_b) return 3;
                if (command_buffer_handle_lookup_for_queue(pdocker_vk_queue_from_handle(queue_b), cmd_a)) return 4;

                if (vkResetCommandPool(device_b, pool_a, 0) != VK_ERROR_INITIALIZATION_FAILED) return 5;
                VkCommandBuffer wrong_alloc = VK_NULL_HANDLE;
                VkCommandBufferAllocateInfo alloc_info;
                memset(&alloc_info, 0, sizeof(alloc_info));
                alloc_info.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO;
                alloc_info.commandPool = pool_a;
                alloc_info.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY;
                alloc_info.commandBufferCount = 1;
                if (vkAllocateCommandBuffers(device_b, &alloc_info, &wrong_alloc) != VK_ERROR_INITIALIZATION_FAILED) return 6;
                if (wrong_alloc != VK_NULL_HANDLE) return 7;
                PdockerVkCommandPool *pool_a_obj = command_pool_handle_lookup_for_device(device_a, pool_a);
                PdockerVkCommandBuffer *cmd_a_obj = command_buffer_handle_lookup_for_device(device_a, cmd_a);
                if (!pool_a_obj || !cmd_a_obj) return 8;
                vkFreeCommandBuffers(device_b, pool_a, 1, &cmd_a);
                if (command_buffer_handle_lookup_for_device(device_a, cmd_a) != cmd_a_obj) return 8;
                if (cmd_a_obj->destroyed || cmd_a_obj->owner_pool != pool_a_obj ||
                    !command_pool_contains_command_buffer(pool_a_obj, cmd_a_obj)) return 65;
                vkDestroyCommandPool(device_b, pool_a, NULL);
                if (command_pool_handle_lookup_for_device(device_a, pool_a) != pool_a_obj ||
                    command_buffer_handle_lookup_for_device(device_a, cmd_a) != cmd_a_obj) return 9;
                if (pool_a_obj->destroyed || cmd_a_obj->destroyed ||
                    cmd_a_obj->owner_pool != pool_a_obj ||
                    !command_buffer_belongs_to_pool(cmd_a_obj, pool_a_obj) ||
                    !command_pool_contains_command_buffer(pool_a_obj, cmd_a_obj)) return 66;

                VkFence fence_a = make_fence(device_a, VK_FENCE_CREATE_SIGNALED_BIT);
                VkFence fence_b = make_fence(device_b, 0);
                VkSemaphore binary_a = make_binary_semaphore(device_a);
                VkSemaphore timeline_a = make_timeline_semaphore_object(device_a, 1);
                if (!fence_a || !fence_b || !binary_a || !timeline_a) return 10;
                VkSubmitInfo submit;
                memset(&submit, 0, sizeof(submit));
                submit.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO;
                submit.commandBufferCount = 1;
                submit.pCommandBuffers = &cmd_a;
                if (vkQueueSubmit(queue_b, 1, &submit, VK_NULL_HANDLE) != VK_ERROR_INITIALIZATION_FAILED) return 11;
                submit.commandBufferCount = 0;
                submit.pCommandBuffers = NULL;
                submit.signalSemaphoreCount = 1;
                submit.pSignalSemaphores = &binary_a;
                if (vkQueueSubmit(queue_b, 1, &submit, VK_NULL_HANDLE) != VK_ERROR_INITIALIZATION_FAILED) return 12;
                if (semaphore_handle_lookup_for_device(device_a, binary_a)->signaled) return 13;
                if (vkQueueSubmit(queue_b, 0, NULL, fence_a) != VK_ERROR_INITIALIZATION_FAILED) return 14;
                VkPipelineStageFlags wait_stage = VK_PIPELINE_STAGE_ALL_COMMANDS_BIT;
                submit.waitSemaphoreCount = 1;
                submit.pWaitSemaphores = &timeline_a;
                submit.pWaitDstStageMask = &wait_stage;
                submit.signalSemaphoreCount = 0;
                submit.pSignalSemaphores = NULL;
                if (vkQueueSubmit(queue_b, 1, &submit, VK_NULL_HANDLE) != VK_ERROR_INITIALIZATION_FAILED) return 120;
                submit.waitSemaphoreCount = 0;
                submit.pWaitSemaphores = NULL;
                submit.pWaitDstStageMask = NULL;
                submit.signalSemaphoreCount = 1;
                submit.pSignalSemaphores = &timeline_a;
                if (vkQueueSubmit(queue_b, 1, &submit, VK_NULL_HANDLE) != VK_ERROR_INITIALIZATION_FAILED) return 121;
                submit.signalSemaphoreCount = 0;
                submit.pSignalSemaphores = NULL;

                VkCommandBufferSubmitInfo cmd_submit;
                VkSubmitInfo2 submit2;
                memset(&cmd_submit, 0, sizeof(cmd_submit));
                memset(&submit2, 0, sizeof(submit2));
                cmd_submit.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_SUBMIT_INFO;
                cmd_submit.commandBuffer = cmd_a;
                submit2.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO_2;
                submit2.commandBufferInfoCount = 1;
                submit2.pCommandBufferInfos = &cmd_submit;
                if (vkQueueSubmit2(queue_b, 1, &submit2, VK_NULL_HANDLE) != VK_ERROR_INITIALIZATION_FAILED) return 15;

                if (vkResetFences(device_b, 1, &fence_a) != VK_ERROR_INITIALIZATION_FAILED) return 16;
                if (vkGetFenceStatus(device_a, fence_a) != VK_SUCCESS) return 17;
                if (vkGetFenceStatus(device_b, fence_a) != VK_ERROR_INITIALIZATION_FAILED) return 18;
                if (vkWaitForFences(device_b, 1, &fence_a, VK_TRUE, 0) != VK_ERROR_INITIALIZATION_FAILED) return 19;
                vkDestroyFence(device_b, fence_a, NULL);
                if (!fence_handle_lookup_for_device(device_a, fence_a)) return 20;

                uint64_t counter = 0;
                if (vkGetSemaphoreCounterValue(device_b, timeline_a, &counter) != VK_ERROR_INITIALIZATION_FAILED) return 21;
                VkSemaphoreSignalInfo signal_info;
                memset(&signal_info, 0, sizeof(signal_info));
                signal_info.sType = VK_STRUCTURE_TYPE_SEMAPHORE_SIGNAL_INFO;
                signal_info.semaphore = timeline_a;
                signal_info.value = 9;
                if (vkSignalSemaphore(device_b, &signal_info) != VK_ERROR_INITIALIZATION_FAILED) return 22;
                if (semaphore_handle_lookup_for_device(device_a, timeline_a)->value != 1) return 23;
                VkSemaphoreWaitInfo wait_info;
                memset(&wait_info, 0, sizeof(wait_info));
                wait_info.sType = VK_STRUCTURE_TYPE_SEMAPHORE_WAIT_INFO;
                wait_info.semaphoreCount = 1;
                wait_info.pSemaphores = &timeline_a;
                uint64_t wait_value = 1;
                wait_info.pValues = &wait_value;
                if (vkWaitSemaphores(device_b, &wait_info, 0) != VK_ERROR_INITIALIZATION_FAILED) return 24;
                vkDestroySemaphore(device_b, timeline_a, NULL);
                if (!semaphore_handle_lookup_for_device(device_a, timeline_a)) return 25;

                VkEvent event_a = make_event(device_a);
                VkEvent event_b = make_event(device_b);
                if (!event_a || !event_b) return 26;
                if (vkSetEvent(device_b, event_a) != VK_ERROR_INITIALIZATION_FAILED) return 27;
                if (vkGetEventStatus(device_a, event_a) != VK_EVENT_RESET) return 28;
                if (vkResetEvent(device_b, event_a) != VK_ERROR_INITIALIZATION_FAILED) return 29;
                vkDestroyEvent(device_b, event_a, NULL);
                if (!event_handle_lookup_for_device(device_a, event_a)) return 30;
                if (vkBeginCommandBuffer(cmd_a, NULL) != VK_SUCCESS) return 31;
                vkCmdSetEvent(cmd_a, event_b, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT);
                if (vkEndCommandBuffer(cmd_a) != VK_ERROR_FEATURE_NOT_PRESENT) return 32;
                VkCommandBuffer cmd_a_wait = make_cmd(device_a, pool_a, VK_COMMAND_BUFFER_LEVEL_PRIMARY);
                if (!cmd_a_wait) return 171;
                if (vkBeginCommandBuffer(cmd_a_wait, NULL) != VK_SUCCESS) return 172;
                VkEvent wait_events[1] = { event_b };
                vkCmdWaitEvents(cmd_a_wait, 1, wait_events,
                                VK_PIPELINE_STAGE_ALL_COMMANDS_BIT,
                                VK_PIPELINE_STAGE_ALL_COMMANDS_BIT,
                                0, NULL, 0, NULL, 0, NULL);
                if (vkEndCommandBuffer(cmd_a_wait) != VK_ERROR_FEATURE_NOT_PRESENT) return 173;

                VkQueryPool query_a = make_query_pool(device_a);
                VkQueryPool query_b = make_query_pool(device_b);
                if (!query_a || !query_b) return 33;
                PdockerVkQueryPool *query_a_obj = query_pool_handle_lookup_for_device(device_a, query_a);
                query_a_obj->values[0] = 123;
                query_a_obj->available[0] = 1;
                uint64_t query_value = 0;
                if (vkGetQueryPoolResults(device_b, query_a, 0, 1, sizeof(query_value), &query_value,
                                          sizeof(query_value), VK_QUERY_RESULT_64_BIT) != VK_ERROR_INITIALIZATION_FAILED) return 34;
                vkResetQueryPool(device_b, query_a, 0, 1);
                if (query_a_obj->available[0] != 1 || query_a_obj->values[0] != 123) return 35;
                if (vkBeginCommandBuffer(cmd_b, NULL) != VK_SUCCESS) return 36;
                vkCmdWriteTimestamp(cmd_b, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, query_a, 0);
                PdockerVkCommandBuffer *cmd_b_query_obj = command_buffer_handle_lookup(cmd_b);
                if (!cmd_b_query_obj || !cmd_b_query_obj->recording_failed ||
                    !cmd_b_query_obj->recording_failure_reason ||
                    strcmp(cmd_b_query_obj->recording_failure_reason, "query-pool-cross-device") != 0) return 174;
                if (cmd_b_query_obj->command_op_count != 0 || cmd_b_query_obj->graphics_command_op_count != 0) return 175;
                if (vkEndCommandBuffer(cmd_b) != VK_ERROR_FEATURE_NOT_PRESENT) return 37;
                vkDestroyQueryPool(device_b, query_a, NULL);
                if (!query_pool_handle_lookup_for_device(device_a, query_a)) return 38;

                if (vkBeginCommandBuffer(secondary_b, NULL) != VK_SUCCESS) return 176;
                vkCmdWriteTimestamp(secondary_b, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, query_b, 0);
                if (vkEndCommandBuffer(secondary_b) != VK_SUCCESS) return 177;
                PdockerVkCommandBuffer *secondary_b_obj = command_buffer_handle_lookup(secondary_b);
                if (!secondary_b_obj || secondary_b_obj->command_op_count == 0 ||
                    secondary_b_obj->graphics_command_op_count == 0) return 178;

                VkCommandBuffer primary_a2 = make_cmd(device_a, pool_a, VK_COMMAND_BUFFER_LEVEL_PRIMARY);
                if (!primary_a2) return 39;
                if (vkBeginCommandBuffer(primary_a2, NULL) != VK_SUCCESS) return 40;
                vkCmdExecuteCommands(primary_a2, 1, &secondary_b);
                PdockerVkCommandBuffer *primary_a2_obj = command_buffer_handle_lookup(primary_a2);
                if (!primary_a2_obj || !primary_a2_obj->recording_failed ||
                    !primary_a2_obj->recording_failure_reason ||
                    strcmp(primary_a2_obj->recording_failure_reason, "execute-commands-cross-device") != 0) return 179;
                if (primary_a2_obj->command_op_count != 0 || primary_a2_obj->graphics_command_op_count != 0) return 180;
                if (vkEndCommandBuffer(primary_a2) != VK_ERROR_FEATURE_NOT_PRESENT) return 41;

                vkDestroyDevice(device_a, NULL);
                vkDestroyDevice(device_b, NULL);
                return 0;
            }
            """).replace("__ICD_SOURCE__", str(ICD_SOURCE))
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


    def test_device_owner_rejects_cross_device_metadata_render_wsi_handles(self):
        source = textwrap.dedent("""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "__ICD_SOURCE__"

            static VkDevice make_device(void) {
                VkDeviceCreateInfo info;
                memset(&info, 0, sizeof(info));
                info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                VkDevice device = VK_NULL_HANDLE;
                if (vkCreateDevice((VkPhysicalDevice)&g_device, &info, NULL, &device) != VK_SUCCESS) return VK_NULL_HANDLE;
                return device;
            }

            static VkCommandBuffer make_cmd(VkDevice device) {
                VkCommandPoolCreateInfo pool_info;
                memset(&pool_info, 0, sizeof(pool_info));
                pool_info.sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO;
                pool_info.queueFamilyIndex = 0;
                VkCommandPool pool = VK_NULL_HANDLE;
                if (vkCreateCommandPool(device, &pool_info, NULL, &pool) != VK_SUCCESS) return VK_NULL_HANDLE;
                VkCommandBufferAllocateInfo alloc;
                memset(&alloc, 0, sizeof(alloc));
                alloc.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO;
                alloc.commandPool = pool;
                alloc.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY;
                alloc.commandBufferCount = 1;
                VkCommandBuffer cmd = VK_NULL_HANDLE;
                if (vkAllocateCommandBuffers(device, &alloc, &cmd) != VK_SUCCESS) return VK_NULL_HANDLE;
                PdockerVkCommandBuffer *pd_cmd = command_buffer_handle_lookup(cmd);
                if (pd_cmd) {
                    pd_cmd->requested_feature_mask |= PDOCKER_VK_FEATURE_DYNAMIC_RENDERING;
                    pd_cmd->enabled_extension_mask |= PDOCKER_VK_DEVICE_EXT_KHR_DYNAMIC_RENDERING;
                }
                return cmd;
            }

            static VkDescriptorSetLayout make_layout(VkDevice device) {
                VkDescriptorSetLayoutBinding binding;
                memset(&binding, 0, sizeof(binding));
                binding.binding = 0;
                binding.descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
                binding.descriptorCount = 1;
                binding.stageFlags = VK_SHADER_STAGE_COMPUTE_BIT;
                VkDescriptorSetLayoutCreateInfo info;
                memset(&info, 0, sizeof(info));
                info.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO;
                info.bindingCount = 1;
                info.pBindings = &binding;
                VkDescriptorSetLayout layout = VK_NULL_HANDLE;
                if (vkCreateDescriptorSetLayout(device, &info, NULL, &layout) != VK_SUCCESS) return VK_NULL_HANDLE;
                return layout;
            }

            static VkDescriptorPool make_pool(VkDevice device) {
                VkDescriptorPoolSize size;
                memset(&size, 0, sizeof(size));
                size.type = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
                size.descriptorCount = 2;
                VkDescriptorPoolCreateInfo info;
                memset(&info, 0, sizeof(info));
                info.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO;
                info.flags = VK_DESCRIPTOR_POOL_CREATE_FREE_DESCRIPTOR_SET_BIT;
                info.maxSets = 2;
                info.poolSizeCount = 1;
                info.pPoolSizes = &size;
                VkDescriptorPool pool = VK_NULL_HANDLE;
                if (vkCreateDescriptorPool(device, &info, NULL, &pool) != VK_SUCCESS) return VK_NULL_HANDLE;
                return pool;
            }

            static VkShaderModule make_shader(VkDevice device) {
                const uint32_t shader_words[] = { 0x07230203u, 0x00010000u, 0u, 0u };
                VkShaderModuleCreateInfo info;
                memset(&info, 0, sizeof(info));
                info.sType = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO;
                info.codeSize = sizeof(shader_words);
                info.pCode = shader_words;
                VkShaderModule shader = VK_NULL_HANDLE;
                if (vkCreateShaderModule(device, &info, NULL, &shader) != VK_SUCCESS) return VK_NULL_HANDLE;
                return shader;
            }

            static VkPipelineLayout make_pipeline_layout(VkDevice device, VkDescriptorSetLayout layout) {
                VkPipelineLayoutCreateInfo info;
                memset(&info, 0, sizeof(info));
                info.sType = VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO;
                info.setLayoutCount = 1;
                info.pSetLayouts = &layout;
                VkPipelineLayout pipeline_layout = VK_NULL_HANDLE;
                if (vkCreatePipelineLayout(device, &info, NULL, &pipeline_layout) != VK_SUCCESS) return VK_NULL_HANDLE;
                return pipeline_layout;
            }

            static VkRenderPass make_render_pass(VkDevice device) {
                VkRenderPassCreateInfo info;
                memset(&info, 0, sizeof(info));
                info.sType = VK_STRUCTURE_TYPE_RENDER_PASS_CREATE_INFO;
                VkRenderPass render_pass = VK_NULL_HANDLE;
                if (vkCreateRenderPass(device, &info, NULL, &render_pass) != VK_SUCCESS) return VK_NULL_HANDLE;
                return render_pass;
            }

            static VkFramebuffer make_framebuffer(VkDevice device, VkRenderPass render_pass) {
                VkFramebufferCreateInfo info;
                memset(&info, 0, sizeof(info));
                info.sType = VK_STRUCTURE_TYPE_FRAMEBUFFER_CREATE_INFO;
                info.renderPass = render_pass;
                info.width = 16;
                info.height = 16;
                info.layers = 1;
                VkFramebuffer framebuffer = VK_NULL_HANDLE;
                if (vkCreateFramebuffer(device, &info, NULL, &framebuffer) != VK_SUCCESS) return VK_NULL_HANDLE;
                return framebuffer;
            }

            static VkImage make_color_image(VkDevice device) {
                PdockerVkImage *image = pdocker_alloc_handle(sizeof(*image));
                if (!image) return VK_NULL_HANDLE;
                memset(image, 0, sizeof(*image));
                image->object_id = next_vulkan_object_generation();
                image->owner_device_id = device_owner_id_or_zero(device);
                image->image_type = VK_IMAGE_TYPE_2D;
                image->format = VK_FORMAT_R8G8B8A8_UNORM;
                image->extent = (VkExtent3D){16, 16, 1};
                image->mip_levels = 1;
                image->array_layers = 1;
                image->samples = VK_SAMPLE_COUNT_1_BIT;
                image->tiling = VK_IMAGE_TILING_OPTIMAL;
                image->usage = VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT;
                image->sharing_mode = VK_SHARING_MODE_EXCLUSIVE;
                image->initial_layout = VK_IMAGE_LAYOUT_UNDEFINED;
                image->current_layout = VK_IMAGE_LAYOUT_UNDEFINED;
                image->requirements_size = 4096;
                image->requirements_alignment = PDOCKER_VK_REQUIREMENT_ALIGNMENT;
                image->memory_type_bits = 1;
                image->generation = next_vulkan_object_generation();
                image_register(image);
                return pdocker_vk_image_to_handle(image);
            }

            static VkImageView make_color_image_view(VkDevice device, VkImage image) {
                VkImageViewCreateInfo info;
                memset(&info, 0, sizeof(info));
                info.sType = VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO;
                info.image = image;
                info.viewType = VK_IMAGE_VIEW_TYPE_2D;
                info.format = VK_FORMAT_R8G8B8A8_UNORM;
                info.subresourceRange.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
                info.subresourceRange.levelCount = 1;
                info.subresourceRange.layerCount = 1;
                VkImageView view = VK_NULL_HANDLE;
                if (vkCreateImageView(device, &info, NULL, &view) != VK_SUCCESS) return VK_NULL_HANDLE;
                return view;
            }

            static VkSwapchainKHR make_swapchain(VkDevice device) {
                VkHeadlessSurfaceCreateInfoEXT surface_info;
                memset(&surface_info, 0, sizeof(surface_info));
                surface_info.sType = VK_STRUCTURE_TYPE_HEADLESS_SURFACE_CREATE_INFO_EXT;
                VkSurfaceKHR surface_handle = VK_NULL_HANDLE;
                if (vkCreateHeadlessSurfaceEXT(VK_NULL_HANDLE, &surface_info, NULL, &surface_handle) != VK_SUCCESS) return VK_NULL_HANDLE;
                PdockerVkSurface *surface = surface_handle_lookup_for_device(device, surface_handle);
                if (!surface) return VK_NULL_HANDLE;
                PdockerVkSwapchain *swapchain = pdocker_alloc_handle(sizeof(*swapchain));
                if (!swapchain) return VK_NULL_HANDLE;
                memset(swapchain, 0, sizeof(*swapchain));
                swapchain->owner_device_id = device_owner_id_or_zero(device);
                swapchain->surface = surface;
                swapchain->image_format = VK_FORMAT_R8G8B8A8_UNORM;
                swapchain->image_color_space = VK_COLOR_SPACE_SRGB_NONLINEAR_KHR;
                swapchain->image_extent = (VkExtent2D){640, 480};
                swapchain->image_usage = VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT;
                swapchain->present_mode = VK_PRESENT_MODE_FIFO_KHR;
                swapchain->composite_alpha = VK_COMPOSITE_ALPHA_OPAQUE_BIT_KHR;
                swapchain->pre_transform = VK_SURFACE_TRANSFORM_IDENTITY_BIT_KHR;
                swapchain->image_count = 2;
                swapchain->generation = next_vulkan_object_generation();
                for (uint32_t i = 0; i < swapchain->image_count; ++i) {
                    PdockerVkImage *image = pdocker_alloc_handle(sizeof(*image));
                    PdockerVkMemory *memory = pdocker_alloc_handle(sizeof(*memory));
                    if (!image || !memory) return VK_NULL_HANDLE;
                    memset(image, 0, sizeof(*image));
                    memset(memory, 0, sizeof(*memory));
                    image->owner_device_id = swapchain->owner_device_id;
                    memory->owner_device_id = swapchain->owner_device_id;
                    memory->size = 4096;
                    image->memory = memory;
                    image->swapchain_owned = true;
                    image->current_layout = VK_IMAGE_LAYOUT_PRESENT_SRC_KHR;
                    swapchain->images[i] = image;
                    swapchain->memories[i] = memory;
                }
                swapchain_register(swapchain);
                return pdocker_vk_swapchain_to_handle(swapchain);
            }

            int main(void) {
                VkDevice device_a = make_device();
                VkDevice device_b = make_device();
                if (!device_a || !device_b || device_a == device_b) return 1;

                VkDescriptorSetLayout layout_a = make_layout(device_a);
                VkDescriptorSetLayout layout_b = make_layout(device_b);
                VkDescriptorPool pool_a = make_pool(device_a);
                VkShaderModule shader_a = make_shader(device_a);
                VkShaderModule shader_b = make_shader(device_b);
                VkPipelineLayout pipeline_layout_a = make_pipeline_layout(device_a, layout_a);
                VkPipelineLayout pipeline_layout_b = make_pipeline_layout(device_b, layout_b);
                VkPipelineCache cache_a = VK_NULL_HANDLE;
                VkPipelineCacheCreateInfo cache_info;
                memset(&cache_info, 0, sizeof(cache_info));
                cache_info.sType = VK_STRUCTURE_TYPE_PIPELINE_CACHE_CREATE_INFO;
                if (vkCreatePipelineCache(device_a, &cache_info, NULL, &cache_a) != VK_SUCCESS) return 2;
                VkRenderPass render_pass_a = make_render_pass(device_a);
                VkFramebuffer framebuffer_a = make_framebuffer(device_a, render_pass_a);
                VkImage image_a = make_color_image(device_a);
                VkImageView image_view_a = make_color_image_view(device_a, image_a);
                VkSwapchainKHR swapchain_a = make_swapchain(device_a);
                VkCommandBuffer cmd_b = make_cmd(device_b);
                if (!layout_a) return 301;
                if (!layout_b) return 302;
                if (!pool_a) return 303;
                if (!shader_a) return 304;
                if (!shader_b) return 314;
                if (!pipeline_layout_a) return 305;
                if (!pipeline_layout_b) return 306;
                if (!cache_a) return 307;
                if (!render_pass_a) return 308;
                if (!framebuffer_a) return 309;
                if (!image_a) return 310;
                if (!image_view_a) return 311;
                if (!swapchain_a) return 312;
                if (!cmd_b) return 313;

                if (descriptor_set_layout_handle_lookup_for_device(device_b, layout_a)) return 4;
                vkDestroyDescriptorSetLayout(device_b, layout_a, NULL);
                if (!descriptor_set_layout_handle_lookup_for_device(device_a, layout_a)) return 5;
                VkPipelineLayout wrong_layout = VK_NULL_HANDLE;
                VkPipelineLayoutCreateInfo pl_info;
                memset(&pl_info, 0, sizeof(pl_info));
                pl_info.sType = VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO;
                pl_info.setLayoutCount = 1;
                pl_info.pSetLayouts = &layout_a;
                if (vkCreatePipelineLayout(device_b, &pl_info, NULL, &wrong_layout) != VK_ERROR_FEATURE_NOT_PRESENT) return 6;
                if (wrong_layout != VK_NULL_HANDLE) return 7;

                VkDescriptorSet wrong_set = VK_NULL_HANDLE;
                VkDescriptorSetAllocateInfo alloc;
                memset(&alloc, 0, sizeof(alloc));
                alloc.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO;
                alloc.descriptorPool = pool_a;
                alloc.descriptorSetCount = 1;
                alloc.pSetLayouts = &layout_a;
                if (vkAllocateDescriptorSets(device_b, &alloc, &wrong_set) != VK_ERROR_INITIALIZATION_FAILED) return 8;
                if (wrong_set != VK_NULL_HANDLE) return 9;

                VkDescriptorUpdateTemplate wrong_template = VK_NULL_HANDLE;
                VkDescriptorUpdateTemplateCreateInfo template_info;
                memset(&template_info, 0, sizeof(template_info));
                template_info.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_UPDATE_TEMPLATE_CREATE_INFO;
                template_info.templateType = VK_DESCRIPTOR_UPDATE_TEMPLATE_TYPE_DESCRIPTOR_SET;
                template_info.descriptorSetLayout = layout_a;
                if (vkCreateDescriptorUpdateTemplate(device_b, &template_info, NULL, &wrong_template) != VK_ERROR_INITIALIZATION_FAILED) return 10;
                if (wrong_template != VK_NULL_HANDLE) return 11;

                size_t cache_size = 0;
                if (vkGetPipelineCacheData(device_b, cache_a, &cache_size, NULL) != VK_ERROR_INITIALIZATION_FAILED) return 12;
                if (vkMergePipelineCaches(device_b, cache_a, 0, NULL) != VK_ERROR_INITIALIZATION_FAILED) return 13;

                VkComputePipelineCreateInfo cp;
                memset(&cp, 0, sizeof(cp));
                cp.sType = VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO;
                cp.stage.sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO;
                cp.stage.stage = VK_SHADER_STAGE_COMPUTE_BIT;
                cp.stage.module = shader_a;
                cp.stage.pName = "main";
                cp.layout = pipeline_layout_b;
                VkPipeline pipeline = VK_NULL_HANDLE;
                if (vkCreateComputePipelines(device_b, VK_NULL_HANDLE, 1, &cp, NULL, &pipeline) != VK_ERROR_FEATURE_NOT_PRESENT) return 14;
                if (pipeline != VK_NULL_HANDLE) return 15;

                VkPipelineShaderStageCreateInfo graphics_stage;
                memset(&graphics_stage, 0, sizeof(graphics_stage));
                graphics_stage.sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO;
                graphics_stage.stage = VK_SHADER_STAGE_VERTEX_BIT;
                graphics_stage.module = shader_a;
                graphics_stage.pName = "main";
                VkGraphicsPipelineCreateInfo gp;
                memset(&gp, 0, sizeof(gp));
                gp.sType = VK_STRUCTURE_TYPE_GRAPHICS_PIPELINE_CREATE_INFO;
                gp.stageCount = 1;
                gp.pStages = &graphics_stage;
                gp.layout = pipeline_layout_b;
                VkPipeline graphics_pipeline = VK_NULL_HANDLE;
                if (vkCreateGraphicsPipelines(device_b, VK_NULL_HANDLE, 1, &gp, NULL, &graphics_pipeline) != VK_ERROR_INITIALIZATION_FAILED) return 401;
                if (graphics_pipeline != VK_NULL_HANDLE) return 402;

                graphics_stage.module = shader_b;
                gp.layout = pipeline_layout_a;
                graphics_pipeline = VK_NULL_HANDLE;
                if (vkCreateGraphicsPipelines(device_b, VK_NULL_HANDLE, 1, &gp, NULL, &graphics_pipeline) != VK_ERROR_INITIALIZATION_FAILED) return 425;
                if (graphics_pipeline != VK_NULL_HANDLE) return 426;

                gp.layout = pipeline_layout_b;
                gp.renderPass = render_pass_a;
                graphics_pipeline = VK_NULL_HANDLE;
                if (vkCreateGraphicsPipelines(device_b, VK_NULL_HANDLE, 1, &gp, NULL, &graphics_pipeline) != VK_ERROR_INITIALIZATION_FAILED) return 427;
                if (graphics_pipeline != VK_NULL_HANDLE) return 428;
                gp.renderPass = VK_NULL_HANDLE;

                VkPipelineShaderStageCreateInfo graphics_stage_a = graphics_stage;
                graphics_stage_a.module = shader_a;
                VkGraphicsPipelineCreateInfo base_gp = gp;
                base_gp.flags = 0;
                base_gp.basePipelineHandle = VK_NULL_HANDLE;
                base_gp.basePipelineIndex = -1;
                base_gp.layout = pipeline_layout_a;
                base_gp.pStages = &graphics_stage_a;
                VkPipeline base_pipeline_a = VK_NULL_HANDLE;
                if (vkCreateGraphicsPipelines(device_a, VK_NULL_HANDLE, 1, &base_gp, NULL, &base_pipeline_a) != VK_SUCCESS) return 429;
                if (base_pipeline_a == VK_NULL_HANDLE) return 430;
                gp.flags = VK_PIPELINE_CREATE_DERIVATIVE_BIT;
                gp.basePipelineHandle = base_pipeline_a;
                gp.basePipelineIndex = -1;
                gp.layout = pipeline_layout_b;
                gp.pStages = &graphics_stage;
                graphics_pipeline = VK_NULL_HANDLE;
                if (vkCreateGraphicsPipelines(device_b, VK_NULL_HANDLE, 1, &gp, NULL, &graphics_pipeline) != VK_ERROR_INITIALIZATION_FAILED) return 431;
                if (graphics_pipeline != VK_NULL_HANDLE) return 432;
                gp.flags = 0;
                gp.basePipelineHandle = VK_NULL_HANDLE;
                gp.basePipelineIndex = -1;

                VkFramebuffer fb_wrong = VK_NULL_HANDLE;
                VkFramebufferCreateInfo fb_info;
                memset(&fb_info, 0, sizeof(fb_info));
                fb_info.sType = VK_STRUCTURE_TYPE_FRAMEBUFFER_CREATE_INFO;
                fb_info.renderPass = render_pass_a;
                fb_info.width = 16;
                fb_info.height = 16;
                fb_info.layers = 1;
                if (vkCreateFramebuffer(device_b, &fb_info, NULL, &fb_wrong) != VK_ERROR_INITIALIZATION_FAILED) return 16;
                if (fb_wrong != VK_NULL_HANDLE) return 17;
                vkDestroyRenderPass(device_b, render_pass_a, NULL);
                if (!render_pass_handle_lookup_for_device(device_a, render_pass_a)) return 18;
                vkDestroyFramebuffer(device_b, framebuffer_a, NULL);
                if (!framebuffer_handle_lookup_for_device(device_a, framebuffer_a)) return 19;

                if (vkBeginCommandBuffer(cmd_b, NULL) != VK_SUCCESS) return 20;
                VkRenderPassBeginInfo begin;
                memset(&begin, 0, sizeof(begin));
                begin.sType = VK_STRUCTURE_TYPE_RENDER_PASS_BEGIN_INFO;
                begin.renderPass = render_pass_a;
                begin.framebuffer = framebuffer_a;
                begin.renderArea.extent.width = 16;
                begin.renderArea.extent.height = 16;
                vkCmdBeginRenderPass(cmd_b, &begin, VK_SUBPASS_CONTENTS_INLINE);
                if (vkEndCommandBuffer(cmd_b) != VK_ERROR_FEATURE_NOT_PRESENT) return 21;

                if (vkBeginCommandBuffer(cmd_b, NULL) != VK_SUCCESS) return 22;
                VkRenderingAttachmentInfo color;
                memset(&color, 0, sizeof(color));
                color.sType = VK_STRUCTURE_TYPE_RENDERING_ATTACHMENT_INFO;
                color.imageView = image_view_a;
                color.imageLayout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL;
                color.loadOp = VK_ATTACHMENT_LOAD_OP_LOAD;
                color.storeOp = VK_ATTACHMENT_STORE_OP_STORE;
                VkRenderingInfo rendering;
                memset(&rendering, 0, sizeof(rendering));
                rendering.sType = VK_STRUCTURE_TYPE_RENDERING_INFO;
                rendering.renderArea.extent.width = 16;
                rendering.renderArea.extent.height = 16;
                rendering.layerCount = 1;
                rendering.colorAttachmentCount = 1;
                rendering.pColorAttachments = &color;
                vkCmdBeginRendering(cmd_b, &rendering);
                PdockerVkCommandBuffer *cmd_b_obj = command_buffer_handle_lookup(cmd_b);
                if (!cmd_b_obj) return 407;
                if (!cmd_b_obj->recording_failed) return 408;
                if (cmd_b_obj->dynamic_rendering_active) return 409;
                if (cmd_b_obj->active_color_attachment_count != 0) return 410;
                if (cmd_b_obj->graphics_rendering_op_count != 0) return 411;
                if (cmd_b_obj->graphics_command_op_count != 0) return 412;
                if (cmd_b_obj->active_color_attachments[0].image_view != NULL) return 413;
                if (cmd_b_obj->active_color_attachments[0].image_view_snapshot.valid) return 414;
                if (vkEndCommandBuffer(cmd_b) != VK_ERROR_FEATURE_NOT_PRESENT) return 23;

                if (vkBeginCommandBuffer(cmd_b, NULL) != VK_SUCCESS) return 415;
                color.imageView = (VkImageView)(uintptr_t)0x1234u;
                color.resolveImageView = VK_NULL_HANDLE;
                vkCmdBeginRendering(cmd_b, &rendering);
                cmd_b_obj = command_buffer_handle_lookup(cmd_b);
                if (!cmd_b_obj || !cmd_b_obj->recording_failed) return 416;
                if (cmd_b_obj->graphics_rendering_op_count != 0) return 417;
                if (cmd_b_obj->active_color_attachments[0].image_view != NULL) return 418;
                if (vkEndCommandBuffer(cmd_b) != VK_ERROR_FEATURE_NOT_PRESENT) return 419;

                if (vkBeginCommandBuffer(cmd_b, NULL) != VK_SUCCESS) return 420;
                color.imageView = VK_NULL_HANDLE;
                color.resolveImageView = (VkImageView)(uintptr_t)0x5678u;
                color.resolveMode = VK_RESOLVE_MODE_AVERAGE_BIT;
                vkCmdBeginRendering(cmd_b, &rendering);
                cmd_b_obj = command_buffer_handle_lookup(cmd_b);
                if (!cmd_b_obj || !cmd_b_obj->recording_failed) return 421;
                if (cmd_b_obj->graphics_rendering_op_count != 0) return 422;
                if (cmd_b_obj->active_color_attachments[0].resolve_image_view != NULL) return 423;
                if (vkEndCommandBuffer(cmd_b) != VK_ERROR_FEATURE_NOT_PRESENT) return 424;
                color.imageView = image_view_a;
                color.resolveImageView = VK_NULL_HANDLE;
                color.resolveMode = VK_RESOLVE_MODE_NONE;

                if (vkBeginCommandBuffer(cmd_b, NULL) != VK_SUCCESS) return 403;
                vkCmdBindDescriptorSets(cmd_b, VK_PIPELINE_BIND_POINT_GRAPHICS, pipeline_layout_a, 0, 0, NULL, 0, NULL);
                if (vkEndCommandBuffer(cmd_b) != VK_ERROR_FEATURE_NOT_PRESENT) return 404;

                if (vkBeginCommandBuffer(cmd_b, NULL) != VK_SUCCESS) return 405;
                uint32_t push_value = 0x12345678u;
                vkCmdPushConstants(cmd_b, pipeline_layout_a, VK_SHADER_STAGE_VERTEX_BIT, 0, sizeof(push_value), &push_value);
                if (vkEndCommandBuffer(cmd_b) != VK_ERROR_FEATURE_NOT_PRESENT) return 406;

                uint32_t image_count = 0;
                if (vkGetSwapchainImagesKHR(device_b, swapchain_a, &image_count, NULL) != VK_ERROR_INITIALIZATION_FAILED) return 24;
                vkDestroySwapchainKHR(device_b, swapchain_a, NULL);
                if (!swapchain_handle_lookup_for_device(device_a, swapchain_a)) return 25;

                vkDestroyShaderModule(device_b, shader_a, NULL);
                if (!shader_module_handle_lookup_for_device(device_a, shader_a)) return 433;
                vkDestroyPipelineLayout(device_b, pipeline_layout_a, NULL);
                if (!pipeline_layout_handle_lookup_for_device(device_a, pipeline_layout_a)) return 434;
                vkDestroyPipeline(device_b, base_pipeline_a, NULL);
                if (!pipeline_handle_lookup_for_device(device_a, base_pipeline_a)) return 435;
                vkDestroyPipelineCache(device_b, cache_a, NULL);
                if (!pipeline_cache_handle_lookup_for_device(device_a, cache_a)) return 436;

                vkDestroyDevice(device_b, NULL);
                if (!descriptor_set_layout_handle_lookup_for_device(device_a, layout_a)) return 26;
                if (!descriptor_pool_handle_lookup_for_device(device_a, pool_a)) return 27;
                if (!shader_module_handle_lookup_for_device(device_a, shader_a)) return 28;
                if (!pipeline_layout_handle_lookup_for_device(device_a, pipeline_layout_a)) return 29;
                if (!pipeline_cache_handle_lookup_for_device(device_a, cache_a)) return 30;
                if (!render_pass_handle_lookup_for_device(device_a, render_pass_a)) return 31;
                if (!framebuffer_handle_lookup_for_device(device_a, framebuffer_a)) return 32;
                if (!swapchain_handle_lookup_for_device(device_a, swapchain_a)) return 33;

                vkDestroyDevice(device_a, NULL);
                return 0;
            }
            """).replace("__ICD_SOURCE__", str(ICD_SOURCE))
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


    def test_destroy_device_retires_live_device_children_and_queue(self):
        source = textwrap.dedent("""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "__ICD_SOURCE__"

            int main(void) {
                VkDeviceCreateInfo device_info;
                memset(&device_info, 0, sizeof(device_info));
                device_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                VkDevice device = VK_NULL_HANDLE;
                if (vkCreateDevice((VkPhysicalDevice)&g_device, &device_info, NULL, &device) != VK_SUCCESS || !device) return 1;
                VkQueue queue = VK_NULL_HANDLE;
                vkGetDeviceQueue(device, 0, 0, &queue);
                if (!queue || !pdocker_vk_queue_from_handle(queue)) return 2;

                VkBufferCreateInfo buffer_info;
                memset(&buffer_info, 0, sizeof(buffer_info));
                buffer_info.sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO;
                buffer_info.size = 256;
                buffer_info.usage = VK_BUFFER_USAGE_STORAGE_BUFFER_BIT;
                buffer_info.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
                VkBuffer buffer = VK_NULL_HANDLE;
                if (vkCreateBuffer(device, &buffer_info, NULL, &buffer) != VK_SUCCESS || !buffer_handle_lookup(buffer)) return 3;

                VkMemoryAllocateInfo memory_info;
                memset(&memory_info, 0, sizeof(memory_info));
                memory_info.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO;
                memory_info.allocationSize = 4096;
                memory_info.memoryTypeIndex = 0;
                VkDeviceMemory memory = VK_NULL_HANDLE;
                if (vkAllocateMemory(device, &memory_info, NULL, &memory) != VK_SUCCESS) return 4;
                if (!memory_handle_resolve(memory, NULL)) return 5;
                if (vkBindBufferMemory(device, buffer, memory, 0) != VK_SUCCESS) return 6;

                VkCommandPoolCreateInfo pool_info;
                memset(&pool_info, 0, sizeof(pool_info));
                pool_info.sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO;
                pool_info.queueFamilyIndex = 0;
                VkCommandPool command_pool = VK_NULL_HANDLE;
                if (vkCreateCommandPool(device, &pool_info, NULL, &command_pool) != VK_SUCCESS ||
                    !command_pool_handle_lookup(command_pool)) return 7;

                VkFenceCreateInfo fence_info;
                memset(&fence_info, 0, sizeof(fence_info));
                fence_info.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO;
                VkFence fence = VK_NULL_HANDLE;
                if (vkCreateFence(device, &fence_info, NULL, &fence) != VK_SUCCESS || !fence_handle_lookup(fence)) return 8;

                VkSemaphoreCreateInfo sem_info;
                memset(&sem_info, 0, sizeof(sem_info));
                sem_info.sType = VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO;
                VkSemaphore sem = VK_NULL_HANDLE;
                if (vkCreateSemaphore(device, &sem_info, NULL, &sem) != VK_SUCCESS || !semaphore_handle_lookup(sem)) return 9;

                VkEventCreateInfo event_info;
                memset(&event_info, 0, sizeof(event_info));
                event_info.sType = VK_STRUCTURE_TYPE_EVENT_CREATE_INFO;
                VkEvent event = VK_NULL_HANDLE;
                if (vkCreateEvent(device, &event_info, NULL, &event) != VK_SUCCESS || !event_handle_lookup(event)) return 10;

                VkQueryPoolCreateInfo query_info;
                memset(&query_info, 0, sizeof(query_info));
                query_info.sType = VK_STRUCTURE_TYPE_QUERY_POOL_CREATE_INFO;
                query_info.queryType = VK_QUERY_TYPE_TIMESTAMP;
                query_info.queryCount = 1;
                VkQueryPool query_pool = VK_NULL_HANDLE;
                if (vkCreateQueryPool(device, &query_info, NULL, &query_pool) != VK_SUCCESS ||
                    !query_pool_handle_lookup(query_pool)) return 11;

                VkPipelineCacheCreateInfo cache_info;
                memset(&cache_info, 0, sizeof(cache_info));
                cache_info.sType = VK_STRUCTURE_TYPE_PIPELINE_CACHE_CREATE_INFO;
                VkPipelineCache cache = VK_NULL_HANDLE;
                if (vkCreatePipelineCache(device, &cache_info, NULL, &cache) != VK_SUCCESS ||
                    !pipeline_cache_handle_lookup(cache)) return 12;

                VkBuffer unowned_buffer = VK_NULL_HANDLE;
                if (vkCreateBuffer(VK_NULL_HANDLE, &buffer_info, NULL, &unowned_buffer) != VK_SUCCESS ||
                    !buffer_handle_lookup(unowned_buffer)) return 13;

                VkCommandPool unowned_pool = VK_NULL_HANDLE;
                if (vkCreateCommandPool(VK_NULL_HANDLE, &pool_info, NULL, &unowned_pool) != VK_SUCCESS ||
                    !command_pool_handle_lookup(unowned_pool)) return 14;

                VkFence unowned_fence = VK_NULL_HANDLE;
                if (vkCreateFence(VK_NULL_HANDLE, &fence_info, NULL, &unowned_fence) != VK_SUCCESS ||
                    !fence_handle_lookup(unowned_fence)) return 15;

                vkDestroyDevice(device, NULL);

                if (device_handle_resolve(device, NULL)) return 20;
                if (pdocker_vk_queue_from_handle(queue)) return 21;
                if (buffer_handle_lookup(buffer)) return 22;
                if (memory_handle_resolve(memory, NULL)) return 23;
                if (command_pool_handle_lookup(command_pool)) return 24;
                if (fence_handle_lookup(fence)) return 25;
                if (semaphore_handle_lookup(sem)) return 26;
                if (event_handle_lookup(event)) return 27;
                if (query_pool_handle_lookup(query_pool)) return 28;
                if (pipeline_cache_handle_lookup(cache)) return 29;
                if (!buffer_handle_lookup(unowned_buffer)) return 36;
                if (!command_pool_handle_lookup(unowned_pool)) return 37;
                if (!fence_handle_lookup(unowned_fence)) return 38;

                if (vkQueueSubmit(queue, 0, NULL, VK_NULL_HANDLE) != VK_ERROR_INITIALIZATION_FAILED) return 30;
                if (vkBindBufferMemory(device, buffer, memory, 0) != VK_ERROR_INITIALIZATION_FAILED) return 31;
                if (vkGetFenceStatus(device, fence) != VK_ERROR_INITIALIZATION_FAILED) return 32;
                if (vkGetEventStatus(device, event) != VK_ERROR_INITIALIZATION_FAILED) return 33;
                size_t cache_size = 123;
                if (vkGetPipelineCacheData(device, cache, &cache_size, NULL) != VK_ERROR_INITIALIZATION_FAILED) return 34;
                if (cache_size != 123) return 35;

                vkDestroyBuffer(device, buffer, NULL);
                vkFreeMemory(device, memory, NULL);
                vkDestroyCommandPool(device, command_pool, NULL);
                vkDestroyFence(device, fence, NULL);
                vkDestroySemaphore(device, sem, NULL);
                vkDestroyEvent(device, event, NULL);
                vkDestroyQueryPool(device, query_pool, NULL);
                vkDestroyPipelineCache(device, cache, NULL);
                vkDestroyBuffer(VK_NULL_HANDLE, unowned_buffer, NULL);
                vkDestroyCommandPool(VK_NULL_HANDLE, unowned_pool, NULL);
                vkDestroyFence(VK_NULL_HANDLE, unowned_fence, NULL);
                if (buffer_handle_lookup(unowned_buffer)) return 39;
                if (command_pool_handle_lookup(unowned_pool)) return 40;
                if (fence_handle_lookup(unowned_fence)) return 41;
                return 0;
            }
            """).replace("__ICD_SOURCE__", str(ICD_SOURCE))
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


    def test_properties_and_features_reject_invalid_physical_device(self):
        source = textwrap.dedent("""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "__ICD_SOURCE__"

            int main(void) {
                VkPhysicalDevice bad = (VkPhysicalDevice)(uintptr_t)0x12345678u;
                VkPhysicalDeviceProperties props;
                memset(&props, 0x7f, sizeof(props));
                vkGetPhysicalDeviceProperties(VK_NULL_HANDLE, &props);
                if (props.apiVersion != 0 || props.limits.maxImageDimension2D != 0) return 1;
                memset(&props, 0x7f, sizeof(props));
                vkGetPhysicalDeviceProperties(bad, &props);
                if (props.apiVersion != 0 || props.limits.maxImageDimension2D != 0) return 2;

                VkPhysicalDeviceFeatures features;
                memset(&features, 0x7f, sizeof(features));
                vkGetPhysicalDeviceFeatures(VK_NULL_HANDLE, &features);
                if (features.robustBufferAccess != VK_FALSE || features.samplerAnisotropy != VK_FALSE) return 3;
                memset(&features, 0x7f, sizeof(features));
                vkGetPhysicalDeviceFeatures(bad, &features);
                if (features.robustBufferAccess != VK_FALSE || features.samplerAnisotropy != VK_FALSE) return 4;

                VkPhysicalDeviceMemoryProperties memory;
                memset(&memory, 0x7f, sizeof(memory));
                vkGetPhysicalDeviceMemoryProperties(VK_NULL_HANDLE, &memory);
                if (memory.memoryTypeCount != 0 || memory.memoryHeapCount != 0) return 6;
                memset(&memory, 0x7f, sizeof(memory));
                vkGetPhysicalDeviceMemoryProperties(bad, &memory);
                if (memory.memoryTypeCount != 0 || memory.memoryHeapCount != 0) return 7;

                VkFormatProperties format_props;
                memset(&format_props, 0x7f, sizeof(format_props));
                vkGetPhysicalDeviceFormatProperties(VK_NULL_HANDLE, VK_FORMAT_R8G8B8A8_UNORM, &format_props);
                if (format_props.bufferFeatures != 0 || format_props.optimalTilingFeatures != 0) return 8;

                uint32_t q_count = 2;
                VkQueueFamilyProperties q_props[2];
                memset(q_props, 0x7f, sizeof(q_props));
                vkGetPhysicalDeviceQueueFamilyProperties(VK_NULL_HANDLE, &q_count, q_props);
                if (q_count != 0 || q_props[0].queueFlags != 0 || q_props[1].queueFlags != 0) return 9;

                VkPhysicalDevice physical = (VkPhysicalDevice)physical_device_for_instance(NULL);
                memset(&props, 0, sizeof(props));
                vkGetPhysicalDeviceProperties(physical, &props);
                if (props.apiVersion == 0 || props.limits.maxImageDimension2D == 0) return 5;
                return 0;
            }
            """).replace("__ICD_SOURCE__", str(ICD_SOURCE))
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


    def test_image_format_properties_reject_invalid_physical_device(self):
        source = textwrap.dedent("""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "__ICD_SOURCE__"

            int main(void) {
                setenv("PDOCKER_VULKAN_HEAP_BYTES", "2147483648", 1);
                setenv("PDOCKER_VULKAN_MAX_BUFFER_BYTES", "2147483648", 1);
                VkImageFormatProperties props;
                memset(&props, 0x7f, sizeof(props));
                VkResult rc = vkGetPhysicalDeviceImageFormatProperties(
                    VK_NULL_HANDLE,
                    VK_FORMAT_R8G8B8A8_UNORM,
                    VK_IMAGE_TYPE_2D,
                    VK_IMAGE_TILING_OPTIMAL,
                    VK_IMAGE_USAGE_SAMPLED_BIT,
                    0,
                    &props);
                if (rc != VK_ERROR_INITIALIZATION_FAILED) return 1;
                if (props.maxMipLevels != 0 || props.maxArrayLayers != 0 || props.sampleCounts != 0) return 2;

                VkPhysicalDevice bad = (VkPhysicalDevice)(uintptr_t)0x12345678u;
                memset(&props, 0x7f, sizeof(props));
                rc = vkGetPhysicalDeviceImageFormatProperties(
                    bad,
                    VK_FORMAT_R8G8B8A8_UNORM,
                    VK_IMAGE_TYPE_2D,
                    VK_IMAGE_TILING_OPTIMAL,
                    VK_IMAGE_USAGE_SAMPLED_BIT,
                    0,
                    &props);
                if (rc != VK_ERROR_INITIALIZATION_FAILED) return 3;
                if (props.maxMipLevels != 0 || props.maxArrayLayers != 0 || props.sampleCounts != 0) return 4;

                VkPhysicalDevice physical = (VkPhysicalDevice)physical_device_for_instance(NULL);
                memset(&props, 0, sizeof(props));
                rc = vkGetPhysicalDeviceImageFormatProperties(
                    physical,
                    VK_FORMAT_R8G8B8A8_UNORM,
                    VK_IMAGE_TYPE_2D,
                    VK_IMAGE_TILING_OPTIMAL,
                    VK_IMAGE_USAGE_SAMPLED_BIT,
                    0,
                    &props);
                if (rc != VK_SUCCESS) return 5;
                if (props.maxMipLevels == 0 || props.sampleCounts == 0) return 6;
                return 0;
            }
            """).replace("__ICD_SOURCE__", str(ICD_SOURCE))
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


    def test_device_extension_properties_reject_invalid_physical_device(self):
        source = textwrap.dedent("""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "__ICD_SOURCE__"

            int main(void) {
                uint32_t count = 2;
                VkExtensionProperties props[2];
                memset(props, 0x7f, sizeof(props));
                if (vkEnumerateDeviceExtensionProperties(VK_NULL_HANDLE, NULL, &count, props) != VK_ERROR_INITIALIZATION_FAILED) return 1;
                if (count != 0) return 2;
                if (props[0].extensionName[0] != 0 || props[1].extensionName[0] != 0) return 3;

                VkPhysicalDevice bad = (VkPhysicalDevice)(uintptr_t)0x12345678u;
                count = 2;
                memset(props, 0x7f, sizeof(props));
                if (vkEnumerateDeviceExtensionProperties(bad, NULL, &count, props) != VK_ERROR_INITIALIZATION_FAILED) return 4;
                if (count != 0) return 5;
                if (props[0].extensionName[0] != 0 || props[1].extensionName[0] != 0) return 6;

                count = 0;
                VkPhysicalDevice physical = (VkPhysicalDevice)physical_device_for_instance(NULL);
                if (vkEnumerateDeviceExtensionProperties(physical, NULL, &count, NULL) != VK_SUCCESS) return 7;
                if (count == 0) return 8;
                return 0;
            }
            """).replace("__ICD_SOURCE__", str(ICD_SOURCE))
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


    def test_device_scoped_creators_reject_invalid_non_null_device(self):
        source = textwrap.dedent("""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "__ICD_SOURCE__"

            int main(void) {
                VkDevice bad = (VkDevice)(uintptr_t)0x12345678u;

                VkBufferCreateInfo buffer_info;
                memset(&buffer_info, 0, sizeof(buffer_info));
                buffer_info.sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO;
                buffer_info.size = 256;
                buffer_info.usage = VK_BUFFER_USAGE_STORAGE_BUFFER_BIT;
                buffer_info.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
                VkBuffer buffer = (VkBuffer)(uintptr_t)0x1u;
                if (vkCreateBuffer(bad, &buffer_info, NULL, &buffer) != VK_ERROR_INITIALIZATION_FAILED) return 1;
                if (buffer != VK_NULL_HANDLE) return 2;

                VkMemoryAllocateInfo memory_info;
                memset(&memory_info, 0, sizeof(memory_info));
                memory_info.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO;
                memory_info.allocationSize = 4096;
                memory_info.memoryTypeIndex = 0;
                VkDeviceMemory memory = (VkDeviceMemory)(uintptr_t)0x2u;
                if (vkAllocateMemory(bad, &memory_info, NULL, &memory) != VK_ERROR_INITIALIZATION_FAILED) return 3;
                if (memory != VK_NULL_HANDLE) return 4;

                VkDescriptorSetLayoutBinding binding;
                memset(&binding, 0, sizeof(binding));
                binding.binding = 0;
                binding.descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
                binding.descriptorCount = 1;
                binding.stageFlags = VK_SHADER_STAGE_COMPUTE_BIT;
                VkDescriptorSetLayoutCreateInfo layout_info;
                memset(&layout_info, 0, sizeof(layout_info));
                layout_info.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO;
                layout_info.bindingCount = 1;
                layout_info.pBindings = &binding;
                VkDescriptorSetLayout set_layout = (VkDescriptorSetLayout)(uintptr_t)0x3u;
                if (vkCreateDescriptorSetLayout(bad, &layout_info, NULL, &set_layout) != VK_ERROR_INITIALIZATION_FAILED) return 5;
                if (set_layout != VK_NULL_HANDLE) return 6;

                VkDescriptorPoolSize pool_size;
                memset(&pool_size, 0, sizeof(pool_size));
                pool_size.type = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
                pool_size.descriptorCount = 1;
                VkDescriptorPoolCreateInfo pool_info;
                memset(&pool_info, 0, sizeof(pool_info));
                pool_info.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO;
                pool_info.maxSets = 1;
                pool_info.poolSizeCount = 1;
                pool_info.pPoolSizes = &pool_size;
                VkDescriptorPool pool = (VkDescriptorPool)(uintptr_t)0x4u;
                if (vkCreateDescriptorPool(bad, &pool_info, NULL, &pool) != VK_ERROR_INITIALIZATION_FAILED) return 7;
                if (pool != VK_NULL_HANDLE) return 8;

                const uint32_t shader_words[] = {0x07230203u, 0x00010000u, 0u, 0u};
                VkShaderModuleCreateInfo shader_info;
                memset(&shader_info, 0, sizeof(shader_info));
                shader_info.sType = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO;
                shader_info.codeSize = sizeof(shader_words);
                shader_info.pCode = shader_words;
                VkShaderModule shader = (VkShaderModule)(uintptr_t)0x5u;
                if (vkCreateShaderModule(bad, &shader_info, NULL, &shader) != VK_ERROR_INITIALIZATION_FAILED) return 9;
                if (shader != VK_NULL_HANDLE) return 10;

                VkCommandPoolCreateInfo cmd_pool_info;
                memset(&cmd_pool_info, 0, sizeof(cmd_pool_info));
                cmd_pool_info.sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO;
                cmd_pool_info.queueFamilyIndex = 0;
                VkCommandPool cmd_pool = (VkCommandPool)(uintptr_t)0x6u;
                if (vkCreateCommandPool(bad, &cmd_pool_info, NULL, &cmd_pool) != VK_ERROR_INITIALIZATION_FAILED) return 11;
                if (cmd_pool != VK_NULL_HANDLE) return 12;

                VkFenceCreateInfo fence_info;
                memset(&fence_info, 0, sizeof(fence_info));
                fence_info.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO;
                VkFence fence = (VkFence)(uintptr_t)0x7u;
                if (vkCreateFence(bad, &fence_info, NULL, &fence) != VK_ERROR_INITIALIZATION_FAILED) return 13;
                if (fence != VK_NULL_HANDLE) return 14;

                VkSemaphoreCreateInfo sem_info;
                memset(&sem_info, 0, sizeof(sem_info));
                sem_info.sType = VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO;
                VkSemaphore sem = (VkSemaphore)(uintptr_t)0x8u;
                if (vkCreateSemaphore(bad, &sem_info, NULL, &sem) != VK_ERROR_INITIALIZATION_FAILED) return 15;
                if (sem != VK_NULL_HANDLE) return 16;

                VkEventCreateInfo event_info;
                memset(&event_info, 0, sizeof(event_info));
                event_info.sType = VK_STRUCTURE_TYPE_EVENT_CREATE_INFO;
                VkEvent event = (VkEvent)(uintptr_t)0x9u;
                if (vkCreateEvent(bad, &event_info, NULL, &event) != VK_ERROR_INITIALIZATION_FAILED) return 17;
                if (event != VK_NULL_HANDLE) return 18;

                VkQueryPoolCreateInfo query_info;
                memset(&query_info, 0, sizeof(query_info));
                query_info.sType = VK_STRUCTURE_TYPE_QUERY_POOL_CREATE_INFO;
                query_info.queryType = VK_QUERY_TYPE_TIMESTAMP;
                query_info.queryCount = 1;
                VkQueryPool query_pool = (VkQueryPool)(uintptr_t)0xau;
                if (vkCreateQueryPool(bad, &query_info, NULL, &query_pool) != VK_ERROR_INITIALIZATION_FAILED) return 19;
                if (query_pool != VK_NULL_HANDLE) return 20;

                VkPipelineCacheCreateInfo cache_info;
                memset(&cache_info, 0, sizeof(cache_info));
                cache_info.sType = VK_STRUCTURE_TYPE_PIPELINE_CACHE_CREATE_INFO;
                VkPipelineCache cache = (VkPipelineCache)(uintptr_t)0xbu;
                if (vkCreatePipelineCache(bad, &cache_info, NULL, &cache) != VK_ERROR_INITIALIZATION_FAILED) return 21;
                if (cache != VK_NULL_HANDLE) return 22;

                return 0;
            }
            """).replace("__ICD_SOURCE__", str(ICD_SOURCE))
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


    def test_device_queues_are_per_device_and_do_not_alias(self):
        source = textwrap.dedent("""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "__ICD_SOURCE__"

            static VkDevice make_device(void) {
                VkDeviceCreateInfo info;
                memset(&info, 0, sizeof(info));
                info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                VkDevice device = VK_NULL_HANDLE;
                if (vkCreateDevice((VkPhysicalDevice)&g_device, &info, NULL, &device) != VK_SUCCESS) return VK_NULL_HANDLE;
                return device;
            }

            static VkFence make_fence(VkDevice device) {
                VkFenceCreateInfo info;
                memset(&info, 0, sizeof(info));
                info.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO;
                VkFence fence = VK_NULL_HANDLE;
                if (vkCreateFence(device, &info, NULL, &fence) != VK_SUCCESS) return VK_NULL_HANDLE;
                return fence;
            }

            int main(void) {
                VkDevice device_a = make_device();
                if (!device_a) return 1;
                VkQueue queue_a = VK_NULL_HANDLE;
                vkGetDeviceQueue(device_a, 0, 0, &queue_a);
                PdockerVkQueue *queue_a_obj = pdocker_vk_queue_from_handle(queue_a);
                if (!queue_a_obj || queue_a_obj->device_object_id != ((PdockerVkDevice *)device_a)->object_id) return 2;
                VkFence fence_a = make_fence(device_a);
                if (!fence_a) return 3;

                VkDevice device_b = make_device();
                if (!device_b || device_b == device_a) return 4;
                VkQueue queue_b = VK_NULL_HANDLE;
                vkGetDeviceQueue(device_b, 0, 0, &queue_b);
                PdockerVkQueue *queue_b_obj = pdocker_vk_queue_from_handle(queue_b);
                if (!queue_b_obj || queue_b_obj->device_object_id != ((PdockerVkDevice *)device_b)->object_id) return 5;
                if (queue_a == queue_b || queue_a_obj == queue_b_obj) return 6;
                if (pdocker_vk_queue_from_handle(queue_a) != queue_a_obj) return 7;
                if (queue_a_obj->device_object_id != ((PdockerVkDevice *)device_a)->object_id) return 8;

                if (vkQueueSubmit(queue_a, 0, NULL, fence_a) != VK_SUCCESS) return 9;
                if (vkGetFenceStatus(device_a, fence_a) != VK_SUCCESS) return 10;

                vkDestroyDevice(device_b, NULL);
                if (pdocker_vk_queue_from_handle(queue_a) != queue_a_obj) return 11;
                if (queue_a_obj->device_object_id != ((PdockerVkDevice *)device_a)->object_id) return 12;
                if (pdocker_vk_queue_from_handle(queue_b) != NULL) return 13;
                if (vkQueueSubmit(queue_a, 0, NULL, VK_NULL_HANDLE) != VK_SUCCESS) return 14;

                vkDestroyDevice(device_a, NULL);
                if (pdocker_vk_queue_from_handle(queue_a) != NULL) return 15;
                return 0;
            }
            """).replace("__ICD_SOURCE__", str(ICD_SOURCE))
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


    def test_device_queue_lookup_shape_is_fail_closed(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            static int expect_null_queue(const VkDeviceQueueInfo2 *info, int code) {{
                VkQueue queue = (VkQueue)(uintptr_t)0x1234u;
                vkGetDeviceQueue2((VkDevice)(uintptr_t)0x1u, info, &queue);
                if (queue != VK_NULL_HANDLE) {{
                    fprintf(stderr, "expected null queue for case %d, got %p\\n", code, (void *)queue);
                    return code;
                }}
                return 0;
            }}

            int main(void) {{
                VkDeviceQueueInfo2 info;
                memset(&info, 0, sizeof(info));
                info.sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_INFO_2;
                info.queueFamilyIndex = 0;
                info.queueIndex = 0;
                info.flags = 0;
                VkQueue queue = (VkQueue)(uintptr_t)0x1234u;
                vkGetDeviceQueue2((VkDevice)(uintptr_t)0x1u, &info, &queue);
                if (queue != VK_NULL_HANDLE) {{
                    fprintf(stderr, "invalid device returned stale queue %p\\n", (void *)queue);
                    return 2;
                }}

                float valid_priority = 1.0f;
                VkDeviceQueueCreateInfo valid_qci;
                VkDeviceCreateInfo valid_create_info;
                memset(&valid_qci, 0, sizeof(valid_qci));
                memset(&valid_create_info, 0, sizeof(valid_create_info));
                valid_qci.sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO;
                valid_qci.queueFamilyIndex = 0;
                valid_qci.queueCount = 1;
                valid_qci.pQueuePriorities = &valid_priority;
                valid_create_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                valid_create_info.queueCreateInfoCount = 1;
                valid_create_info.pQueueCreateInfos = &valid_qci;
                VkDevice valid_device = VK_NULL_HANDLE;
                if (vkCreateDevice((VkPhysicalDevice)&g_device, &valid_create_info, NULL, &valid_device) != VK_SUCCESS ||
                    valid_device == VK_NULL_HANDLE) {{
                    fprintf(stderr, "valid device create failed before queue lookup\\n");
                    return 13;
                }}
                queue = VK_NULL_HANDLE;
                vkGetDeviceQueue2(valid_device, &info, &queue);
                if (queue == VK_NULL_HANDLE) {{
                    fprintf(stderr, "valid device queue info did not return advertised queue\\n");
                    vkDestroyDevice(valid_device, NULL);
                    return 14;
                }}
                PdockerVkQueue *live_queue = pdocker_vk_queue_from_handle(queue);
                if (live_queue == NULL) {{
                    fprintf(stderr, "valid queue handle did not resolve while live\\n");
                    vkDestroyDevice(valid_device, NULL);
                    return 15;
                }}
                PdockerVkQueue forged_queue;
                memset(&forged_queue, 0, sizeof(forged_queue));
                forged_queue.object_id = live_queue->object_id;
                forged_queue.instance_object_id = live_queue->instance_object_id;
                forged_queue.physical_device_object_id = live_queue->physical_device_object_id;
                forged_queue.device_object_id = live_queue->device_object_id;
                if (pdocker_vk_queue_from_handle((VkQueue)&forged_queue) != NULL) {{
                    fprintf(stderr, "unregistered forged queue resolved as live\\n");
                    vkDestroyDevice(valid_device, NULL);
                    return 16;
                }}
                VkQueue bad_queue = (VkQueue)(uintptr_t)0x12345678u;
                if (pdocker_vk_queue_from_handle(bad_queue) != NULL) {{
                    fprintf(stderr, "invalid queue pointer resolved as live\\n");
                    vkDestroyDevice(valid_device, NULL);
                    return 17;
                }}
                if (vkQueueSubmit(bad_queue, 0, NULL, VK_NULL_HANDLE) != VK_ERROR_INITIALIZATION_FAILED) {{
                    fprintf(stderr, "invalid queue pointer was accepted by submit\\n");
                    vkDestroyDevice(valid_device, NULL);
                    return 18;
                }}
                if (vkQueueSubmit2(bad_queue, 0, NULL, VK_NULL_HANDLE) != VK_ERROR_INITIALIZATION_FAILED) {{
                    fprintf(stderr, "invalid queue pointer was accepted by submit2\\n");
                    vkDestroyDevice(valid_device, NULL);
                    return 21;
                }}
                if (vkQueuePresentKHR(bad_queue, NULL) != VK_ERROR_INITIALIZATION_FAILED) {{
                    fprintf(stderr, "invalid queue pointer was accepted by present\\n");
                    vkDestroyDevice(valid_device, NULL);
                    return 22;
                }}
                if (vkQueueWaitIdle(bad_queue) != VK_ERROR_INITIALIZATION_FAILED) {{
                    fprintf(stderr, "invalid queue pointer was accepted by wait-idle\\n");
                    vkDestroyDevice(valid_device, NULL);
                    return 23;
                }}
                if (vkQueueBindSparse(bad_queue, 0, NULL, VK_NULL_HANDLE) != VK_ERROR_INITIALIZATION_FAILED) {{
                    fprintf(stderr, "invalid queue pointer was accepted by bind-sparse\\n");
                    vkDestroyDevice(valid_device, NULL);
                    return 24;
                }}
                vkDestroyDevice(valid_device, NULL);
                if (pdocker_vk_queue_from_handle(queue) != NULL) {{
                    fprintf(stderr, "destroyed queue still resolved as live\\n");
                    return 19;
                }}
                if (vkQueueSubmit(queue, 0, NULL, VK_NULL_HANDLE) != VK_ERROR_INITIALIZATION_FAILED) {{
                    fprintf(stderr, "destroyed queue was accepted by submit\\n");
                    return 20;
                }}
                if (vkQueueSubmit2(queue, 0, NULL, VK_NULL_HANDLE) != VK_ERROR_INITIALIZATION_FAILED) {{
                    fprintf(stderr, "destroyed queue was accepted by submit2\\n");
                    return 25;
                }}
                if (vkQueuePresentKHR(queue, NULL) != VK_ERROR_INITIALIZATION_FAILED) {{
                    fprintf(stderr, "destroyed queue was accepted by present\\n");
                    return 26;
                }}
                if (vkQueueWaitIdle(queue) != VK_ERROR_INITIALIZATION_FAILED) {{
                    fprintf(stderr, "destroyed queue was accepted by wait-idle\\n");
                    return 27;
                }}
                if (vkQueueBindSparse(queue, 0, NULL, VK_NULL_HANDLE) != VK_ERROR_INITIALIZATION_FAILED) {{
                    fprintf(stderr, "destroyed queue was accepted by bind-sparse\\n");
                    return 28;
                }}

                if (expect_null_queue(NULL, 3)) return 3;

                info.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO;
                if (expect_null_queue(&info, 4)) return 4;
                info.sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_INFO_2;

                VkBaseInStructure dummy_pnext;
                memset(&dummy_pnext, 0, sizeof(dummy_pnext));
                dummy_pnext.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO;
                info.pNext = &dummy_pnext;
                if (expect_null_queue(&info, 5)) return 5;
                info.pNext = NULL;

                info.flags = (VkDeviceQueueCreateFlags)1u;
                if (expect_null_queue(&info, 6)) return 6;
                info.flags = 0;

                info.queueFamilyIndex = PDOCKER_VK_ADVERTISED_QUEUE_FAMILY_COUNT;
                if (expect_null_queue(&info, 7)) return 7;

                float priority = 1.0f;
                VkDeviceQueueCreateInfo qci;
                VkDeviceCreateInfo create_info;
                memset(&qci, 0, sizeof(qci));
                memset(&create_info, 0, sizeof(create_info));
                qci.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO;
                qci.queueFamilyIndex = 0;
                qci.queueCount = 1;
                qci.pQueuePriorities = &priority;
                create_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                create_info.queueCreateInfoCount = 1;
                create_info.pQueueCreateInfos = &qci;
                if (validate_device_queue_create_infos(&create_info) == VK_SUCCESS) {{
                    fprintf(stderr, "wrong queue-create sType was accepted\\n");
                    return 8;
                }}
                qci.sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO;
                if (validate_device_queue_create_infos(&create_info) != VK_SUCCESS) {{
                    fprintf(stderr, "valid queue-create info was rejected\\n");
                    return 9;
                }}

                VkDevice device = (VkDevice)(uintptr_t)0x1234u;
                if (vkCreateDevice((VkPhysicalDevice)&g_device, NULL, NULL, &device) !=
                        VK_ERROR_INITIALIZATION_FAILED ||
                    device != VK_NULL_HANDLE) {{
                    return 10;
                }}
                create_info.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO;
                device = (VkDevice)(uintptr_t)0x1234u;
                if (vkCreateDevice((VkPhysicalDevice)&g_device, &create_info, NULL, &device) !=
                        VK_ERROR_INITIALIZATION_FAILED ||
                    device != VK_NULL_HANDLE) {{
                    return 11;
                }}
                create_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                if (vkCreateDevice((VkPhysicalDevice)&g_device, &create_info, NULL, &device) != VK_SUCCESS ||
                    device == VK_NULL_HANDLE) {{
                    return 12;
                }}
                vkDestroyDevice(device, NULL);
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


    def test_memory_allocate_capture_address_pnext_is_fail_closed(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            int main(void) {{
                VkMemoryOpaqueCaptureAddressAllocateInfo capture;
                memset(&capture, 0, sizeof(capture));
                capture.sType = VK_STRUCTURE_TYPE_MEMORY_OPAQUE_CAPTURE_ADDRESS_ALLOCATE_INFO;
                capture.opaqueCaptureAddress = 0;
                if (validate_memory_allocate_pnext(VK_NULL_HANDLE, &capture) != VK_SUCCESS) {{
                    fprintf(stderr, "zero opaque capture address allocation pNext was rejected\\n");
                    return 2;
                }}

                capture.opaqueCaptureAddress = 0x1000u;
                if (validate_memory_allocate_pnext(VK_NULL_HANDLE, &capture) != VK_ERROR_FEATURE_NOT_PRESENT) {{
                    fprintf(stderr, "nonzero opaque capture address allocation pNext was accepted\\n");
                    return 3;
                }}

                VkExportMemoryAllocateInfo export_info;
                memset(&export_info, 0, sizeof(export_info));
                export_info.sType = VK_STRUCTURE_TYPE_EXPORT_MEMORY_ALLOCATE_INFO;
                export_info.handleTypes = 0;
                if (validate_memory_allocate_pnext(VK_NULL_HANDLE, &export_info) != VK_SUCCESS) {{
                    fprintf(stderr, "zero export memory handle-types pNext was rejected\\n");
                    return 4;
                }}
                export_info.handleTypes = VK_EXTERNAL_MEMORY_HANDLE_TYPE_OPAQUE_FD_BIT;
                if (validate_memory_allocate_pnext(VK_NULL_HANDLE, &export_info) != VK_ERROR_FEATURE_NOT_PRESENT) {{
                    fprintf(stderr, "nonzero export memory handle-types pNext was accepted\\n");
                    return 5;
                }}

                VkMemoryPriorityAllocateInfoEXT priority_info;
                memset(&priority_info, 0, sizeof(priority_info));
                priority_info.sType = VK_STRUCTURE_TYPE_MEMORY_PRIORITY_ALLOCATE_INFO_EXT;
                priority_info.priority = 0.5f;
                if (validate_memory_allocate_pnext(VK_NULL_HANDLE, &priority_info) != VK_ERROR_FEATURE_NOT_PRESENT) {{
                    fprintf(stderr, "default memory priority pNext was accepted without transport\\n");
                    return 6;
                }}
                priority_info.priority = 1.0f;
                if (validate_memory_allocate_pnext(VK_NULL_HANDLE, &priority_info) != VK_ERROR_FEATURE_NOT_PRESENT) {{
                    fprintf(stderr, "non-default memory priority pNext was accepted\\n");
                    return 7;
                }}

                VkMemoryAllocateFlagsInfo flags;
                memset(&flags, 0, sizeof(flags));
                memset(&capture, 0, sizeof(capture));
                memset(&export_info, 0, sizeof(export_info));
                memset(&priority_info, 0, sizeof(priority_info));
                flags.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_FLAGS_INFO;
                flags.deviceMask = 1;
                flags.pNext = &export_info;
                export_info.sType = VK_STRUCTURE_TYPE_EXPORT_MEMORY_ALLOCATE_INFO;
                export_info.handleTypes = 0;
                export_info.pNext = &capture;
                capture.sType = VK_STRUCTURE_TYPE_MEMORY_OPAQUE_CAPTURE_ADDRESS_ALLOCATE_INFO;
                capture.opaqueCaptureAddress = 0;
                if (validate_memory_allocate_pnext(VK_NULL_HANDLE, &flags) != VK_SUCCESS) {{
                    fprintf(stderr, "no-op memory allocate flags + export + capture chain was rejected\\n");
                    return 6;
                }}

                export_info.pNext = &priority_info;
                priority_info.sType = VK_STRUCTURE_TYPE_MEMORY_PRIORITY_ALLOCATE_INFO_EXT;
                priority_info.priority = 0.5f;
                priority_info.pNext = &capture;
                if (validate_memory_allocate_pnext(VK_NULL_HANDLE, &flags) != VK_ERROR_FEATURE_NOT_PRESENT) {{
                    fprintf(stderr, "memory priority in allocation chain was accepted without transport\\n");
                    return 9;
                }}
                export_info.pNext = &capture;

                flags.deviceMask = 2;
                if (validate_memory_allocate_pnext(VK_NULL_HANDLE, &flags) == VK_SUCCESS) {{
                    fprintf(stderr, "multi-device memory allocation mask was accepted\\n");
                    return 7;
                }}

                VkBaseInStructure unknown;
                memset(&unknown, 0, sizeof(unknown));
                unknown.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO;
                if (validate_memory_allocate_pnext(VK_NULL_HANDLE, &unknown) == VK_SUCCESS) {{
                    fprintf(stderr, "unknown memory allocation pNext was accepted\\n");
                    return 8;
                }}
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


    def test_executor_advertisement_unique_usable_keys_override_legacy_truthy_keys(self):
        source = textwrap.dedent(
            rf"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            int main(void) {{
                char json[65536];
                size_t off = 0;
                off += (size_t)snprintf(json + off, sizeof(json) - off,
                    "{{\"schema\":\"skydnir-vulkan-advertisement-caps-v1\","
                    "\"apiVersion\":4206592,\"format_caps_schema\":1,\"format_caps_count\":%zu,"
                    "\"vulkan_dispatch_v5_supported_minors\":[0,1,2,3,4,5,6,7],"
                    "\"vulkan_graphics_v6_supported_minors\":[0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30],"
                    "\"image_format_caps\":{{",
                    pdocker_vk_bridge_format_count());
                for (size_t i = 0; i < pdocker_vk_bridge_format_count(); ++i) {{
                    VkFormat format = pdocker_vk_bridge_format_at(i);
                    off += (size_t)snprintf(json + off, sizeof(json) - off,
                        "%s\"fmt%dOptimalFeatures\":0,\"fmt%dSampleCounts\":1",
                        i ? "," : "", (int)format, (int)format);
                }}
                off += (size_t)snprintf(json + off, sizeof(json) - off,
                    "}},"
                    "\"timelineSemaphore\":1,\"VK_KHR_timeline_semaphore\":1,\"timelineSemaphoreUsable\":0,"
                    "\"synchronization2\":1,\"VK_KHR_synchronization2\":1,\"synchronization2Usable\":0,"
                    "\"dynamicRendering\":1,\"VK_KHR_dynamic_rendering\":1,\"dynamicRenderingUsable\":0,"
                    "\"drawIndirectCount\":1,\"drawIndexedIndirectCount\":1,"
                    "\"drawIndirectCountUsable\":0,\"drawIndexedIndirectCountUsable\":0,"
                    "\"VK_KHR_draw_indirect_count\":1,\"VK_AMD_draw_indirect_count\":1,"
                    "\"extendedDynamicState\":1,\"VK_EXT_extended_dynamic_state\":1,\"extendedDynamicStateUsable\":0,"
                    "\"extendedDynamicState2\":1,\"extendedDynamicState2LogicOp\":1,"
                    "\"extendedDynamicState2PatchControlPoints\":1,\"VK_EXT_extended_dynamic_state2\":1,"
                    "\"extendedDynamicState2Usable\":0,\"extendedDynamicState2LogicOpUsable\":0,"
                    "\"extendedDynamicState2PatchControlPointsUsable\":0,"
                    "\"indexTypeUint8\":1,\"VK_EXT_index_type_uint8\":1,\"indexTypeUint8Usable\":0}}\n");
                if (off >= sizeof(json)) return 99;

                PdockerVkAdvertisedCaps caps;
                memset(&caps, 0, sizeof(caps));
                if (!parse_executor_advertisement_caps_json(json, &caps)) return 1;
                if (!caps.timeline_semaphore || !caps.ext_timeline_semaphore) return 2;
                if (caps.timeline_semaphore_usable) return 3;
                if (caps.synchronization2_usable) return 4;
                if (caps.dynamic_rendering_usable) return 5;
                if (caps.draw_indirect_count_usable) return 6;
                if (caps.draw_indexed_indirect_count_usable) return 7;
                if (caps.ext_draw_indirect_count_khr != true || caps.ext_draw_indirect_count_amd != true) return 8;
                if (caps.extended_dynamic_state_usable) return 9;
                if (caps.extended_dynamic_state2_usable.extendedDynamicState2) return 10;
                if (caps.extended_dynamic_state2_usable.extendedDynamicState2LogicOp) return 11;
                if (caps.extended_dynamic_state2_usable.extendedDynamicState2PatchControlPoints) return 12;
                if (caps.index_type_uint8_usable) return 13;
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)


    def test_descriptor_sparse_api_bindings_do_not_leak_compact_slots(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            static int expect_slot(
                    const PdockerVkDescriptorSet *set,
                    uint32_t start_binding,
                    uint32_t start_array,
                    uint32_t linear_index,
                    uint32_t expected_slot,
                    uint32_t expected_array,
                    uint32_t expected_api_binding) {{
                uint32_t slot = UINT32_MAX;
                uint32_t array_element = UINT32_MAX;
                if (!descriptor_linear_slot(set, start_binding, start_array,
                                            linear_index, &slot, &array_element)) {{
                    fprintf(stderr,
                            "descriptor_linear_slot failed start=%u array=%u linear=%u\\n",
                            start_binding, start_array, linear_index);
                    return 1;
                }}
                if (slot != expected_slot || array_element != expected_array) {{
                    fprintf(stderr,
                            "slot mismatch start=%u linear=%u got slot=%u array=%u expected slot=%u array=%u\\n",
                            start_binding, linear_index, slot, array_element,
                            expected_slot, expected_array);
                    return 2;
                }}
                uint32_t api_binding = descriptor_layout_binding_number(set->layout, slot);
                if (api_binding != expected_api_binding) {{
                    fprintf(stderr,
                            "api binding mismatch slot=%u got=%u expected=%u\\n",
                            slot, api_binding, expected_api_binding);
                    return 3;
                }}
                return 0;
            }}

            int main(void) {{
                VkDescriptorSetLayoutBinding bindings[3];
                memset(bindings, 0, sizeof(bindings));
                bindings[0].binding = 9;
                bindings[0].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
                bindings[0].descriptorCount = 3;
                bindings[0].stageFlags = VK_SHADER_STAGE_COMPUTE_BIT;
                bindings[1].binding = 2;
                bindings[1].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
                bindings[1].descriptorCount = 2;
                bindings[1].stageFlags = VK_SHADER_STAGE_COMPUTE_BIT;
                bindings[2].binding = 6;
                bindings[2].descriptorType = VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER;
                bindings[2].descriptorCount = 1;
                bindings[2].stageFlags = VK_SHADER_STAGE_COMPUTE_BIT;

                VkDescriptorSetLayoutCreateInfo create_info;
                memset(&create_info, 0, sizeof(create_info));
                create_info.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO;
                create_info.bindingCount = 3;
                create_info.pBindings = bindings;

                VkDescriptorSetLayout layout_handle = VK_NULL_HANDLE;
                VkResult rc = vkCreateDescriptorSetLayout(
                    VK_NULL_HANDLE, &create_info, NULL, &layout_handle);
                if (rc != VK_SUCCESS || layout_handle == VK_NULL_HANDLE) {{
                    fprintf(stderr, "vkCreateDescriptorSetLayout failed rc=%d\\n", (int)rc);
                    return 4;
                }}
                PdockerVkDescriptorSetLayout *layout =
                    pdocker_vk_descriptor_set_layout_from_handle(layout_handle);
                if (!layout) return 5;
                if (descriptor_layout_slot_count(layout) != 3) return 6;
                if (descriptor_layout_binding_number(layout, 0) != 2) return 7;
                if (descriptor_layout_binding_number(layout, 1) != 6) return 8;
                if (descriptor_layout_binding_number(layout, 2) != 9) return 9;
                if (descriptor_layout_slot_for_binding(layout, 2) != 0) return 10;
                if (descriptor_layout_slot_for_binding(layout, 6) != 1) return 11;
                if (descriptor_layout_slot_for_binding(layout, 9) != 2) return 12;
                if (descriptor_layout_slot_for_binding(layout, 0) >= 0) return 13;

                PdockerVkDescriptorSet set;
                memset(&set, 0, sizeof(set));
                set.layout = layout;
                rc = descriptor_set_allocate_storage_with_counts(
                    &set, descriptor_set_storage_capacity_for_layout(layout), NULL);
                if (rc != VK_SUCCESS) {{
                    fprintf(stderr, "descriptor_set_allocate_storage_with_counts failed rc=%d\\n", (int)rc);
                    vkDestroyDescriptorSetLayout(VK_NULL_HANDLE, layout_handle, NULL);
                    return 14;
                }}

                int check = 0;
                if ((check = expect_slot(&set, 2, 0, 0, 0, 0, 2)) != 0) return 20 + check;
                if ((check = expect_slot(&set, 2, 0, 1, 0, 1, 2)) != 0) return 30 + check;
                if ((check = expect_slot(&set, 2, 0, 2, 1, 0, 6)) != 0) return 40 + check;
                if ((check = expect_slot(&set, 2, 0, 3, 2, 0, 9)) != 0) return 50 + check;
                if ((check = expect_slot(&set, 6, 0, 1, 2, 0, 9)) != 0) return 60 + check;
                if (descriptor_linear_slot(&set, 0, 0, 0, NULL, NULL)) return 70;

                PdockerGpuVulkanGraphicsV624DescriptorSetLayoutEntry entries[8];
                size_t entry_count = 0;
                memset(entries, 0, sizeof(entries));
                int collect_rc = collect_graphics_v624_descriptor_set_layout_metadata(
                    entries, &entry_count, layout);
                if (collect_rc != 0) {{
                    fprintf(stderr, "collect layout metadata failed rc=%d\\n", collect_rc);
                    return 80;
                }}
                if (entry_count != 3) return 81;
                if (entries[0].binding != 2 || entries[1].binding != 6 ||
                    entries[2].binding != 9) {{
                    fprintf(stderr, "metadata leaked compact slots as API bindings: %u,%u,%u\\n",
                            entries[0].binding, entries[1].binding, entries[2].binding);
                    return 82;
                }}
                if (entries[0].descriptor_count != 2 ||
                    entries[1].descriptor_count != 1 ||
                    entries[2].descriptor_count != 3) {{
                    fprintf(stderr, "metadata descriptor counts mismatch: %u,%u,%u\\n",
                            entries[0].descriptor_count,
                            entries[1].descriptor_count,
                            entries[2].descriptor_count);
                    return 83;
                }}

                destroy_descriptor_set_storage(&set);
                vkDestroyDescriptorSetLayout(VK_NULL_HANDLE, layout_handle, NULL);
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)



    def test_graphics_v628_advertisement_requires_v627_supported_minor_prefix(self):
        source = textwrap.dedent(
            rf"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            static size_t append_caps_prefix(char *json, size_t size, const char *graphics_minors) {{
                size_t off = 0;
                off += (size_t)snprintf(json + off, size - off,
                    "{{\"schema\":\"skydnir-vulkan-advertisement-caps-v1\","
                    "\"apiVersion\":4206592,\"format_caps_schema\":1,\"format_caps_count\":%zu,"
                    "\"vulkan_dispatch_v5_supported_minors\":[0,1,2,3,4,5,6,7],"
                    "\"vulkan_graphics_v6_supported_minors\":%s,"
                    "\"vulkan_graphics_v6_abi_minor_buffer_views\":%u,"
                    "\"vulkan_graphics_v6_buffer_view_schema_hash\":\"0x%016llx\","
                    "\"vulkan_graphics_v6_max_buffer_views\":%u,"
                    "\"vulkan_graphics_v6_abi_minor_push_constant_ranges\":%u,"
                    "\"vulkan_graphics_v6_push_constant_range_schema_hash\":\"0x%016llx\","
                    "\"vulkan_graphics_v6_max_push_constant_ranges\":%u,"
                    "\"image_format_caps\":{{",
                    pdocker_vk_bridge_format_count(), graphics_minors,
                    PDOCKER_GPU_VULKAN_GRAPHICS_V627_ABI_MINOR,
                    (unsigned long long)PDOCKER_GPU_VULKAN_GRAPHICS_V627_BUFFER_VIEW_SCHEMA_HASH,
                    PDOCKER_GPU_VULKAN_GRAPHICS_V627_MAX_BUFFER_VIEWS,
                    PDOCKER_GPU_VULKAN_GRAPHICS_V628_ABI_MINOR,
                    (unsigned long long)PDOCKER_GPU_VULKAN_GRAPHICS_V628_PUSH_CONSTANT_RANGE_SCHEMA_HASH,
                    PDOCKER_GPU_VULKAN_GRAPHICS_V628_MAX_PUSH_CONSTANT_RANGES);
                for (size_t i = 0; i < pdocker_vk_bridge_format_count(); ++i) {{
                    VkFormat format = pdocker_vk_bridge_format_at(i);
                    off += (size_t)snprintf(json + off, size - off,
                        "%s\"fmt%dOptimalFeatures\":0,\"fmt%dSampleCounts\":1",
                        i ? "," : "", (int)format, (int)format);
                }}
                off += (size_t)snprintf(json + off, size - off, "}}}}\n");
                return off;
            }}

            static int parse_case(const char *graphics_minors, int expect_v627, int expect_v628, int code) {{
                char json[65536];
                size_t off = append_caps_prefix(json, sizeof(json), graphics_minors);
                if (off >= sizeof(json)) return code + 1;
                PdockerVkAdvertisedCaps caps;
                memset(&caps, 0, sizeof(caps));
                if (!parse_executor_advertisement_caps_json(json, &caps)) return code + 2;
                if ((caps.vulkan_graphics_v627_buffer_views_supported != 0) != expect_v627) return code + 3;
                if ((caps.vulkan_graphics_v628_push_constant_ranges_supported != 0) != expect_v628) return code + 4;
                return 0;
            }}

            int main(void) {{
                int rc = parse_case("[0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30]", 1, 1, 10);
                if (rc) return rc;
                rc = parse_case("[0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,28,29,30]", 0, 0, 20);
                if (rc) return rc;
                rc = parse_case("[28]", 0, 0, 30);
                if (rc) return rc;
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)



    def test_device_procaddr_requires_enabled_device_extension_for_selected_extension_commands(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            static int gate_hidden_for_mask(uint64_t enabled_mask, const char *name) {{
                PdockerVkDevice device;
                memset(&device, 0, sizeof(device));
                device.enabled_extension_mask = enabled_mask;
                return device_proc_address_hidden_by_enabled_state(&device, name) ? 1 : 0;
            }}

            static int mask_has_extension(const char *extension_name, uint64_t expected_mask) {{
                const char *enabled[] = {{ extension_name }};
                VkDeviceCreateInfo info;
                memset(&info, 0, sizeof(info));
                info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                info.enabledExtensionCount = 1;
                info.ppEnabledExtensionNames = enabled;
                return (enabled_device_extension_mask_from_create_info(&info) & expected_mask) == expected_mask;
            }}

            static int expect_gate(const char *extension_name,
                                   uint64_t expected_mask,
                                   const char *proc_name,
                                   int code) {{
                if (!mask_has_extension(extension_name, expected_mask)) {{
                    fprintf(stderr, "extension mask not recorded for %s\\n", extension_name);
                    return code;
                }}
                if (!gate_hidden_for_mask(0, proc_name)) {{
                    fprintf(stderr, "proc %s visible without enabling %s\\n", proc_name, extension_name);
                    return code + 1;
                }}
                if (gate_hidden_for_mask(expected_mask, proc_name)) {{
                    fprintf(stderr, "proc %s hidden after enabling %s\\n", proc_name, extension_name);
                    return code + 2;
                }}
                return 0;
            }}

            int main(void) {{
                int rc = 0;
                rc = expect_gate(VK_KHR_MAINTENANCE_3_EXTENSION_NAME,
                                 PDOCKER_VK_DEVICE_EXT_KHR_MAINTENANCE_3,
                                 "vkGetDescriptorSetLayoutSupportKHR", 10);
                if (rc) return rc;
                rc = expect_gate(VK_KHR_DESCRIPTOR_UPDATE_TEMPLATE_EXTENSION_NAME,
                                 PDOCKER_VK_DEVICE_EXT_KHR_DESCRIPTOR_UPDATE_TEMPLATE,
                                 "vkCreateDescriptorUpdateTemplateKHR", 20);
                if (rc) return rc;
            #ifdef VK_KHR_MAINTENANCE_5_EXTENSION_NAME
                rc = expect_gate(VK_KHR_MAINTENANCE_5_EXTENSION_NAME,
                                 PDOCKER_VK_DEVICE_EXT_KHR_MAINTENANCE_5,
                                 "vkGetRenderingAreaGranularityKHR", 30);
                if (rc) return rc;
                rc = expect_gate(VK_KHR_MAINTENANCE_5_EXTENSION_NAME,
                                 PDOCKER_VK_DEVICE_EXT_KHR_MAINTENANCE_5,
                                 "vkCmdBindIndexBuffer2KHR", 40);
                if (rc) return rc;
            #endif
            #ifdef VK_KHR_map_memory2
                rc = expect_gate(VK_KHR_MAP_MEMORY_2_EXTENSION_NAME,
                                 PDOCKER_VK_DEVICE_EXT_KHR_MAP_MEMORY_2,
                                 "vkMapMemory2KHR", 50);
                if (rc) return rc;
            #endif
            #ifdef VK_EXT_HOST_QUERY_RESET_EXTENSION_NAME
                rc = expect_gate(VK_EXT_HOST_QUERY_RESET_EXTENSION_NAME,
                                 PDOCKER_VK_DEVICE_EXT_EXT_HOST_QUERY_RESET,
                                 "vkResetQueryPoolEXT", 60);
                if (rc) return rc;
            #endif
            #ifdef VK_EXT_VALIDATION_CACHE_EXTENSION_NAME
                rc = expect_gate(VK_EXT_VALIDATION_CACHE_EXTENSION_NAME,
                                 PDOCKER_VK_DEVICE_EXT_EXT_VALIDATION_CACHE,
                                 "vkCreateValidationCacheEXT", 70);
                if (rc) return rc;
            #endif
            #ifdef VK_EXT_PRIVATE_DATA_EXTENSION_NAME
                rc = expect_gate(VK_EXT_PRIVATE_DATA_EXTENSION_NAME,
                                 PDOCKER_VK_DEVICE_EXT_EXT_PRIVATE_DATA,
                                 "vkCreatePrivateDataSlotEXT", 80);
                if (rc) return rc;
            #endif
                rc = expect_gate(VK_KHR_SWAPCHAIN_EXTENSION_NAME,
                                 PDOCKER_VK_DEVICE_EXT_KHR_SWAPCHAIN,
                                 "vkCreateSwapchainKHR", 90);
                if (rc) return rc;
                rc = expect_gate(VK_KHR_MAINTENANCE_1_EXTENSION_NAME,
                                 PDOCKER_VK_DEVICE_EXT_KHR_MAINTENANCE_1,
                                 "vkTrimCommandPoolKHR", 100);
                if (rc) return rc;
                rc = expect_gate(VK_KHR_CREATE_RENDERPASS_2_EXTENSION_NAME,
                                 PDOCKER_VK_DEVICE_EXT_KHR_CREATE_RENDERPASS_2,
                                 "vkCreateRenderPass2KHR", 110);
                if (rc) return rc;
                rc = expect_gate(VK_KHR_DEVICE_GROUP_EXTENSION_NAME,
                                 PDOCKER_VK_DEVICE_EXT_KHR_DEVICE_GROUP,
                                 "vkGetDeviceGroupPeerMemoryFeaturesKHR", 120);
                if (rc) return rc;
                rc = expect_gate(VK_KHR_COPY_COMMANDS_2_EXTENSION_NAME,
                                 PDOCKER_VK_DEVICE_EXT_KHR_COPY_COMMANDS_2,
                                 "vkCmdCopyBuffer2KHR", 130);
                if (rc) return rc;
            #ifdef VK_KHR_GET_MEMORY_REQUIREMENTS_2_EXTENSION_NAME
                rc = expect_gate(VK_KHR_GET_MEMORY_REQUIREMENTS_2_EXTENSION_NAME,
                                 PDOCKER_VK_DEVICE_EXT_KHR_GET_MEMORY_REQUIREMENTS_2,
                                 "vkGetBufferMemoryRequirements2KHR", 140);
                if (rc) return rc;
            #endif
            #ifdef VK_KHR_BIND_MEMORY_2_EXTENSION_NAME
                rc = expect_gate(VK_KHR_BIND_MEMORY_2_EXTENSION_NAME,
                                 PDOCKER_VK_DEVICE_EXT_KHR_BIND_MEMORY_2,
                                 "vkBindBufferMemory2KHR", 150);
                if (rc) return rc;
            #endif
            #ifdef VK_KHR_MAINTENANCE_4_EXTENSION_NAME
                rc = expect_gate(VK_KHR_MAINTENANCE_4_EXTENSION_NAME,
                                 PDOCKER_VK_DEVICE_EXT_KHR_MAINTENANCE_4,
                                 "vkGetDeviceBufferMemoryRequirementsKHR", 160);
                if (rc) return rc;
            #endif
            #ifdef VK_KHR_TIMELINE_SEMAPHORE_EXTENSION_NAME
                PdockerVkDevice timeline_device;
                memset(&timeline_device, 0, sizeof(timeline_device));
                if (device_proc_address_hidden_by_enabled_state(&timeline_device, "vkGetSemaphoreCounterValue")) return 170;
                if (!device_proc_address_hidden_by_enabled_state(&timeline_device, "vkGetSemaphoreCounterValueKHR")) return 171;
                timeline_device.enabled_extension_mask = PDOCKER_VK_DEVICE_EXT_KHR_TIMELINE_SEMAPHORE;
                if (device_proc_address_hidden_by_enabled_state(&timeline_device, "vkGetSemaphoreCounterValueKHR")) return 172;
            #endif
                PdockerVkDevice present_device;
                memset(&present_device, 0, sizeof(present_device));
                if (!device_proc_address_hidden_by_enabled_state(&present_device, "vkGetDeviceGroupPresentCapabilitiesKHR")) return 180;
                present_device.enabled_extension_mask = PDOCKER_VK_DEVICE_EXT_KHR_SWAPCHAIN;
                if (device_proc_address_hidden_by_enabled_state(&present_device, "vkGetDeviceGroupPresentCapabilitiesKHR")) return 181;
                present_device.enabled_extension_mask = PDOCKER_VK_DEVICE_EXT_KHR_DEVICE_GROUP;
                if (device_proc_address_hidden_by_enabled_state(&present_device, "vkGetDeviceGroupPresentCapabilitiesKHR")) return 182;
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stderr)


    def test_device_procaddr_hides_global_instance_and_physical_dispatch_commands(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            static int expect_device_proc_hidden(VkDevice device, const char *name, int code) {{
                if (vkGetDeviceProcAddr(device, name) != NULL) {{
                    fprintf(stderr, "proc %s unexpectedly visible from vkGetDeviceProcAddr\\n", name);
                    return code;
                }}
                return 0;
            }}

            static int expect_device_proc_visible(VkDevice device, const char *name, int code) {{
                if (vkGetDeviceProcAddr(device, name) == NULL) {{
                    fprintf(stderr, "device proc %s hidden from vkGetDeviceProcAddr\\n", name);
                    return code;
                }}
                return 0;
            }}

            int main(void) {{
                PdockerVkDevice fake_device;
                memset(&fake_device, 0, sizeof(fake_device));
                fake_device.requested_feature_mask = UINT64_MAX;
                fake_device.enabled_extension_mask = UINT64_MAX;
                if (vkGetDeviceProcAddr((VkDevice)&fake_device, "vkCreateBuffer") != NULL) return 1;
                if (vkGetDeviceProcAddr((VkDevice)(uintptr_t)0x1234u, "vkCreateBuffer") != NULL) return 2;
                vkDestroyDevice((VkDevice)&fake_device, NULL);
                vkDestroyDevice((VkDevice)(uintptr_t)0x1234u, NULL);

                const char *enabled[] = {{
                    VK_KHR_SWAPCHAIN_EXTENSION_NAME,
                    VK_KHR_DEVICE_GROUP_EXTENSION_NAME,
                }};
                VkDeviceCreateInfo create_info;
                memset(&create_info, 0, sizeof(create_info));
                create_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                create_info.enabledExtensionCount = (uint32_t)(sizeof(enabled) / sizeof(enabled[0]));
                create_info.ppEnabledExtensionNames = enabled;
                VkDevice device = VK_NULL_HANDLE;
                if (vkCreateDevice((VkPhysicalDevice)&g_device, &create_info, NULL, &device) != VK_SUCCESS ||
                    device == VK_NULL_HANDLE) return 3;

                const char *hidden[] = {{
                    "vkGetInstanceProcAddr",
                    "vkEnumerateInstanceVersion",
                    "vkCreateInstance",
                    "vkDestroyInstance",
                    "vkEnumeratePhysicalDevices",
                    "vkEnumeratePhysicalDeviceGroupsKHR",
                    "vkGetPhysicalDeviceProperties",
                    "vkGetPhysicalDeviceProperties2KHR",
                    "vkGetPhysicalDeviceFeatures2KHR",
                    "vkGetPhysicalDeviceFormatProperties2KHR",
                    "vkGetPhysicalDeviceQueueFamilyProperties2KHR",
                    "vkGetPhysicalDeviceExternalBufferPropertiesKHR",
                    "vkEnumerateDeviceExtensionProperties",
                    "vkCreateDevice",
                    "vkCreateHeadlessSurfaceEXT",
                    "vkDestroySurfaceKHR",
                    "vkGetPhysicalDeviceSurfaceCapabilitiesKHR",
                    "vkGetPhysicalDeviceSurfaceCapabilities2KHR",
                    "vkGetPhysicalDevicePresentRectanglesKHR",
                    "vkGetPhysicalDeviceToolPropertiesEXT",
                    "vkCreateDebugUtilsMessengerEXT",
                    "vk_icdGetPhysicalDeviceProcAddr",
                }};
                for (uint32_t i = 0; i < sizeof(hidden) / sizeof(hidden[0]); ++i) {{
                    int rc = expect_device_proc_hidden(device, hidden[i], 10 + (int)i);
                    if (rc) return rc;
                }}
                const char *visible[] = {{
                    "vkGetDeviceProcAddr",
                    "vkDestroyDevice",
                    "vkGetDeviceQueue",
                    "vkCreateBuffer",
                    "vkCreateSwapchainKHR",
                    "vkGetDeviceGroupSurfacePresentModesKHR",
                    "vkSetDebugUtilsObjectNameEXT",
                }};
                for (uint32_t i = 0; i < sizeof(visible) / sizeof(visible[0]); ++i) {{
                    int rc = expect_device_proc_visible(device, visible[i], 80 + (int)i);
                    if (rc) return rc;
                }}
                VkDevice stale_device = device;
                vkDestroyDevice(device, NULL);
                if (vkGetDeviceProcAddr(stale_device, "vkCreateBuffer") != NULL) return 100;
                vkDestroyDevice(stale_device, NULL);
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stderr)


    def test_extension_pnext_requires_matching_device_extension_enable(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            int main(void) {{
            #ifdef VK_EXT_PRIVATE_DATA_EXTENSION_NAME
                VkPhysicalDevicePrivateDataFeatures private_features;
                VkDevicePrivateDataCreateInfo private_create;
                VkDeviceCreateInfo private_device_info;
                memset(&private_features, 0, sizeof(private_features));
                memset(&private_create, 0, sizeof(private_create));
                memset(&private_device_info, 0, sizeof(private_device_info));
                private_features.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PRIVATE_DATA_FEATURES;
                private_features.privateData = VK_TRUE;
                private_create.sType = VK_STRUCTURE_TYPE_DEVICE_PRIVATE_DATA_CREATE_INFO;
                private_create.privateDataSlotRequestCount = 1;
                private_device_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                private_device_info.pNext = &private_features;
                if (validate_device_create_pnext_extension_enables(&private_device_info, 0) == VK_SUCCESS) return 10;
                if (validate_device_create_pnext_extension_enables(&private_device_info, PDOCKER_VK_DEVICE_EXT_EXT_PRIVATE_DATA) != VK_SUCCESS) return 11;
                private_device_info.pNext = &private_create;
                if (validate_device_create_pnext_extension_enables(&private_device_info, 0) == VK_SUCCESS) return 12;
                if (validate_device_create_pnext_extension_enables(&private_device_info, PDOCKER_VK_DEVICE_EXT_EXT_PRIVATE_DATA) != VK_SUCCESS) return 13;
            #endif

            #ifdef VK_EXT_SUBPASS_MERGE_FEEDBACK_EXTENSION_NAME
                VkPhysicalDeviceSubpassMergeFeedbackFeaturesEXT subpass_features;
                VkDeviceCreateInfo subpass_device_info;
                memset(&subpass_features, 0, sizeof(subpass_features));
                memset(&subpass_device_info, 0, sizeof(subpass_device_info));
                subpass_features.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SUBPASS_MERGE_FEEDBACK_FEATURES_EXT;
                subpass_features.subpassMergeFeedback = VK_TRUE;
                subpass_device_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                subpass_device_info.pNext = &subpass_features;
                if (validate_device_create_pnext_extension_enables(&subpass_device_info, 0) == VK_SUCCESS) return 20;
                if (validate_device_create_pnext_extension_enables(&subpass_device_info, PDOCKER_VK_DEVICE_EXT_EXT_SUBPASS_MERGE_FEEDBACK) != VK_SUCCESS) return 21;

                VkRenderPassCreationControlEXT control;
                VkRenderPassCreationFeedbackCreateInfoEXT feedback_info;
                VkRenderPassCreateInfo2 render_pass_info;
                bool disallow = false;
                memset(&control, 0, sizeof(control));
                memset(&feedback_info, 0, sizeof(feedback_info));
                memset(&render_pass_info, 0, sizeof(render_pass_info));
                control.sType = VK_STRUCTURE_TYPE_RENDER_PASS_CREATION_CONTROL_EXT;
                feedback_info.sType = VK_STRUCTURE_TYPE_RENDER_PASS_CREATION_FEEDBACK_CREATE_INFO_EXT;
                control.pNext = &feedback_info;
                render_pass_info.sType = VK_STRUCTURE_TYPE_RENDER_PASS_CREATE_INFO_2;
                render_pass_info.pNext = &control;
                if (render_pass_create2_pnext_noop(&render_pass_info, &disallow, 0)) return 22;
                if (!render_pass_create2_pnext_noop(&render_pass_info, &disallow, PDOCKER_VK_DEVICE_EXT_EXT_SUBPASS_MERGE_FEEDBACK)) return 23;
            #endif

            #ifdef VK_EXT_VALIDATION_CACHE_EXTENSION_NAME
                VkShaderModuleValidationCacheCreateInfoEXT cache_info;
                memset(&cache_info, 0, sizeof(cache_info));
                cache_info.sType = VK_STRUCTURE_TYPE_SHADER_MODULE_VALIDATION_CACHE_CREATE_INFO_EXT;
                if (validate_shader_module_create_pnext(VK_NULL_HANDLE, &cache_info, 0) == VK_SUCCESS) return 30;
                if (validate_shader_module_create_pnext(VK_NULL_HANDLE, &cache_info, PDOCKER_VK_DEVICE_EXT_EXT_VALIDATION_CACHE) != VK_SUCCESS) return 31;
            #endif
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stderr)

if __name__ == "__main__":
    unittest.main()

import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ICD_SOURCE = ROOT / "docker-proot-setup" / "src" / "gpu" / "pdocker_vulkan_icd.c"


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
                if (vkEnumerateDeviceExtensionProperties(VK_NULL_HANDLE, NULL, &count, extensions) != VK_SUCCESS) return 3;
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
                vkGetPhysicalDeviceProperties2(VK_NULL_HANDLE, &properties2);
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
                if (vkEnumerateDeviceExtensionProperties(VK_NULL_HANDLE, NULL, &count, extensions) != VK_SUCCESS) return 3;
                if (!extension_seen(extensions, count, VK_EXT_SUBPASS_MERGE_FEEDBACK_EXTENSION_NAME)) return 4;

                VkPhysicalDeviceSubpassMergeFeedbackFeaturesEXT feedback_features;
                VkPhysicalDeviceFeatures2 features2;
                memset(&feedback_features, 0, sizeof(feedback_features));
                memset(&features2, 0, sizeof(features2));
                features2.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2;
                features2.pNext = &feedback_features;
                feedback_features.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SUBPASS_MERGE_FEEDBACK_FEATURES_EXT;
                vkGetPhysicalDeviceFeatures2(VK_NULL_HANDLE, &features2);
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
                if (vkEnumerateDeviceExtensionProperties(VK_NULL_HANDLE, NULL, &count, extensions) != VK_SUCCESS) return 3;
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
                vkGetPhysicalDeviceProperties2((VkPhysicalDevice)(uintptr_t)0x1u, &properties2);
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
                if (vkEnumerateDeviceExtensionProperties(VK_NULL_HANDLE, NULL, &count, extensions) != VK_SUCCESS) return 3;
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
                if (proc_address("vkGetImageSubresourceLayout2EXT") !=
                    (PFN_vkVoidFunction)vkGetImageSubresourceLayout2) return 10;

                VkRenderingAreaInfo area;
                VkExtent2D granularity;
                memset(&area, 0, sizeof(area));
                memset(&granularity, 0, sizeof(granularity));
                area.sType = VK_STRUCTURE_TYPE_RENDERING_AREA_INFO;
                ((PFN_vkGetRenderingAreaGranularityKHR)proc_address("vkGetRenderingAreaGranularityKHR"))(
                    VK_NULL_HANDLE, &area, &granularity);
                if (granularity.width != 1 || granularity.height != 1) return 11;
                ((PFN_vkCmdBindIndexBuffer2KHR)proc_address("vkCmdBindIndexBuffer2KHR"))(
                    VK_NULL_HANDLE, VK_NULL_HANDLE, 0, VK_WHOLE_SIZE, VK_INDEX_TYPE_UINT32);
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
                if (vkEnumerateDeviceExtensionProperties(VK_NULL_HANDLE, NULL, &count, extensions) != VK_SUCCESS) return 3;
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
                if (proc_address("vkGetImageSubresourceLayout2EXT") != (PFN_vkVoidFunction)vkGetImageSubresourceLayout2) return 10;

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
                if (vkEnumerateDeviceExtensionProperties(VK_NULL_HANDLE, NULL, &count, extensions) != VK_SUCCESS) return 3;
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
                if (vkEnumerateDeviceExtensionProperties(VK_NULL_HANDLE, NULL, &count, extensions) != VK_SUCCESS) return 3;
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
                if (validate_memory_allocate_pnext(&flags) != VK_SUCCESS) return 15;
                flags.flags = VK_MEMORY_ALLOCATE_DEVICE_ADDRESS_BIT_KHR;
                if (validate_memory_allocate_pnext(&flags) == VK_SUCCESS) return 16;
                flags.flags = 0;
                flags.deviceMask = 2;
                if (validate_memory_allocate_pnext(&flags) == VK_SUCCESS) return 17;

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
                if (vkEnumerateDeviceExtensionProperties(VK_NULL_HANDLE, NULL, &count, extensions) != VK_SUCCESS) return 3;
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
                if (vkEnumerateDeviceExtensionProperties(VK_NULL_HANDLE, NULL, &count, extensions) != VK_SUCCESS) return 3;
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
                if (vkEnumerateDeviceExtensionProperties(VK_NULL_HANDLE, NULL, &count, extensions) != VK_SUCCESS) return 3;
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
                if (vkEnumerateDeviceExtensionProperties(VK_NULL_HANDLE, NULL, &count, extensions) != VK_SUCCESS) return 3;
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
                if (vkEnumerateDeviceExtensionProperties(VK_NULL_HANDLE, NULL, &count, extensions) != VK_SUCCESS) return 3;
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
                if (vkEnumerateDeviceExtensionProperties(VK_NULL_HANDLE, NULL, &extension_count, NULL) != VK_SUCCESS ||
                    extension_count == 0) {{
                    fprintf(stderr, "device extension enumeration failed\\n");
                    return 3;
                }}
                VkValidationCacheCreateInfoEXT cache_info;
                memset(&cache_info, 0, sizeof(cache_info));
                cache_info.sType = VK_STRUCTURE_TYPE_VALIDATION_CACHE_CREATE_INFO_EXT;
                uint32_t initial_word = 0x12345678u;
                cache_info.initialDataSize = sizeof(initial_word);
                cache_info.pInitialData = &initial_word;
                VkValidationCacheEXT cache = VK_NULL_HANDLE;
                if (vkCreateValidationCacheEXT(VK_NULL_HANDLE, &cache_info, NULL, &cache) != VK_SUCCESS ||
                    cache == VK_NULL_HANDLE) {{
                    fprintf(stderr, "local validation cache create failed\\n");
                    return 4;
                }}
                size_t cache_data_size = 99;
                if (vkGetValidationCacheDataEXT(VK_NULL_HANDLE, cache, &cache_data_size, NULL) != VK_SUCCESS ||
                    cache_data_size != 0) {{
                    fprintf(stderr, "validation cache data query was not empty noop\\n");
                    return 5;
                }}
                if (vkMergeValidationCachesEXT(VK_NULL_HANDLE, cache, 1, NULL) == VK_SUCCESS) {{
                    fprintf(stderr, "validation cache merge accepted missing source array\\n");
                    return 6;
                }}
                if (vkMergeValidationCachesEXT(VK_NULL_HANDLE, cache, 1, &cache) != VK_SUCCESS) {{
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
                if (vkCreateShaderModule(VK_NULL_HANDLE, &shader_info, NULL, &shader) != VK_SUCCESS ||
                    shader == VK_NULL_HANDLE) {{
                    fprintf(stderr, "shader module rejected local validation cache pNext\\n");
                    return 8;
                }}
                vkDestroyShaderModule(VK_NULL_HANDLE, shader, NULL);

                VkBaseInStructure unknown;
                memset(&unknown, 0, sizeof(unknown));
                unknown.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO;
                shader_cache.pNext = &unknown;
                if (vkCreateShaderModule(VK_NULL_HANDLE, &shader_info, NULL, &shader) == VK_SUCCESS) {{
                    fprintf(stderr, "shader module accepted unknown validation-cache pNext chain\\n");
                    vkDestroyShaderModule(VK_NULL_HANDLE, shader, NULL);
                    return 9;
                }}
                vkDestroyValidationCacheEXT(VK_NULL_HANDLE, cache, NULL);
                return 0;
            #endif
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
                if (vkEnumerateDeviceExtensionProperties(VK_NULL_HANDLE, NULL, &extension_count, NULL) != VK_SUCCESS ||
                    extension_count == 0) return 3;
                VkExtensionProperties extensions[64];
                memset(extensions, 0, sizeof(extensions));
                uint32_t capacity = 64;
                if (vkEnumerateDeviceExtensionProperties(VK_NULL_HANDLE, NULL, &capacity, extensions) != VK_SUCCESS) return 4;
                VkBool32 found = VK_FALSE;
                for (uint32_t i = 0; i < capacity; ++i) {{
                    if (strcmp(extensions[i].extensionName, VK_EXT_TOOLING_INFO_EXTENSION_NAME) == 0) found = VK_TRUE;
                }}
                if (!found) return 5;

                if (!vkGetPhysicalDeviceToolProperties || !vkGetPhysicalDeviceToolPropertiesEXT) return 6;
                if (proc_address("vkGetPhysicalDeviceToolProperties") != NULL) return 15;
                if (proc_address("vkGetPhysicalDeviceToolPropertiesEXT") == NULL) return 16;
                if (vkGetPhysicalDeviceToolProperties(VK_NULL_HANDLE, NULL, NULL) != VK_ERROR_INITIALIZATION_FAILED) return 7;
                uint32_t tool_count = 0;
                if (vkGetPhysicalDeviceToolPropertiesEXT(VK_NULL_HANDLE, &tool_count, NULL) != VK_SUCCESS ||
                    tool_count != 1) return 8;

                VkPhysicalDeviceToolProperties tool;
                memset(&tool, 0, sizeof(tool));
                tool.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_TOOL_PROPERTIES;
                uint32_t zero_capacity = 0;
                if (vkGetPhysicalDeviceToolProperties(VK_NULL_HANDLE, &zero_capacity, &tool) != VK_INCOMPLETE ||
                    zero_capacity != 0) return 9;

                memset(&tool, 0, sizeof(tool));
                tool.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_TOOL_PROPERTIES;
                uint32_t one_capacity = 1;
                if (vkGetPhysicalDeviceToolPropertiesEXT(VK_NULL_HANDLE, &one_capacity, &tool) != VK_SUCCESS ||
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
                if (vkEnumerateDeviceExtensionProperties(VK_NULL_HANDLE, NULL, &extension_count, NULL) != VK_SUCCESS ||
                    extension_count == 0) return 3;
                VkExtensionProperties extensions[64];
                memset(extensions, 0, sizeof(extensions));
                uint32_t capacity = 64;
                if (vkEnumerateDeviceExtensionProperties(VK_NULL_HANDLE, NULL, &capacity, extensions) != VK_SUCCESS) return 4;
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
                vkGetPhysicalDeviceProperties2(VK_NULL_HANDLE, &properties2);
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
                if (vkEnumerateDeviceExtensionProperties(VK_NULL_HANDLE, NULL, &extension_count, NULL) != VK_SUCCESS ||
                    extension_count == 0) return 3;
                VkExtensionProperties extensions[64];
                memset(extensions, 0, sizeof(extensions));
                uint32_t capacity = 64;
                if (vkEnumerateDeviceExtensionProperties(VK_NULL_HANDLE, NULL, &capacity, extensions) != VK_SUCCESS) return 4;
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
                vkGetPhysicalDeviceMemoryProperties2(VK_NULL_HANDLE, &memory2);
                if (budget.sType != VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MEMORY_BUDGET_PROPERTIES_EXT ||
                    budget.pNext != NULL) return 7;
                if (memory2.memoryProperties.memoryHeapCount == 0) return 8;
                for (uint32_t i = 0; i < memory2.memoryProperties.memoryHeapCount; ++i) {{
                    if (budget.heapBudget[i] != memory2.memoryProperties.memoryHeaps[i].size) {{
                        fprintf(stderr, "heap %u budget %llu != heap size %llu\\n",
                                i,
                                (unsigned long long)budget.heapBudget[i],
                                (unsigned long long)memory2.memoryProperties.memoryHeaps[i].size);
                        return 9;
                    }}
                    if (budget.heapUsage[i] != 0) return 10;
                }}
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
                if (vkEnumerateDeviceExtensionProperties(VK_NULL_HANDLE, NULL, &extension_count, NULL) != VK_SUCCESS ||
                    extension_count == 0) return 3;
                VkExtensionProperties extensions[64];
                memset(extensions, 0, sizeof(extensions));
                uint32_t capacity = 64;
                if (vkEnumerateDeviceExtensionProperties(VK_NULL_HANDLE, NULL, &capacity, extensions) != VK_SUCCESS) return 4;
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
                if (vkEnumerateDeviceExtensionProperties(VK_NULL_HANDLE, NULL, &extension_count, NULL) != VK_SUCCESS ||
                    extension_count == 0) return 3;
                VkExtensionProperties extensions[64];
                memset(extensions, 0, sizeof(extensions));
                uint32_t capacity = 64;
                if (vkEnumerateDeviceExtensionProperties(VK_NULL_HANDLE, NULL, &capacity, extensions) != VK_SUCCESS) return 4;
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
                if (vkEnumerateDeviceExtensionProperties(VK_NULL_HANDLE, NULL, &count, extensions) != VK_SUCCESS) return 3;
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
            #endif
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stderr)


    def test_sampler_border_color_extensions_are_advertised_false_only(self):
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
                if (!device_extension_advertised_name(VK_EXT_CUSTOM_BORDER_COLOR_EXTENSION_NAME)) return 2;
                if (!device_extension_advertised_name(VK_EXT_BORDER_COLOR_SWIZZLE_EXTENSION_NAME)) return 3;
                uint32_t count = 64;
                VkExtensionProperties extensions[64];
                memset(extensions, 0, sizeof(extensions));
                if (vkEnumerateDeviceExtensionProperties(VK_NULL_HANDLE, NULL, &count, extensions) != VK_SUCCESS) return 4;
                if (!extension_seen(extensions, count, VK_EXT_CUSTOM_BORDER_COLOR_EXTENSION_NAME)) return 5;
                if (!extension_seen(extensions, count, VK_EXT_BORDER_COLOR_SWIZZLE_EXTENSION_NAME)) return 6;

                const char *enabled[] = {{
                    VK_EXT_CUSTOM_BORDER_COLOR_EXTENSION_NAME,
                    VK_EXT_BORDER_COLOR_SWIZZLE_EXTENSION_NAME,
                }};
                VkDeviceCreateInfo create_info;
                memset(&create_info, 0, sizeof(create_info));
                create_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                create_info.enabledExtensionCount = 2;
                create_info.ppEnabledExtensionNames = enabled;
                if (validate_device_extensions(&create_info) != VK_SUCCESS) return 7;

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
                if (vkEnumerateDeviceExtensionProperties(VK_NULL_HANDLE, NULL, &extension_count, NULL) != VK_SUCCESS) return 2;
                VkExtensionProperties extensions[64];
                memset(extensions, 0, sizeof(extensions));
                uint32_t capacity = 64;
                if (vkEnumerateDeviceExtensionProperties(VK_NULL_HANDLE, NULL, &capacity, extensions) != VK_SUCCESS) return 3;
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
                if (vkDebugMarkerSetObjectNameEXT(VK_NULL_HANDLE, &name_info) != VK_SUCCESS) return 11;

                const uint32_t tag = 0x13572468u;
                VkDebugMarkerObjectTagInfoEXT tag_info;
                memset(&tag_info, 0, sizeof(tag_info));
                tag_info.sType = VK_STRUCTURE_TYPE_DEBUG_MARKER_OBJECT_TAG_INFO_EXT;
                tag_info.objectType = VK_DEBUG_REPORT_OBJECT_TYPE_BUFFER_EXT;
                tag_info.object = 0x1234u;
                tag_info.tagName = 7u;
                tag_info.tagSize = sizeof(tag);
                tag_info.pTag = &tag;
                if (vkDebugMarkerSetObjectTagEXT(VK_NULL_HANDLE, &tag_info) != VK_SUCCESS) return 12;

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

                PdockerVkQueue queue;
                PdockerVkFence fence;
                memset(&queue, 0, sizeof(queue));
                ensure_vulkan_dispatchable_object_ids();
                queue.object_id = next_vulkan_object_generation();
                queue.instance_object_id = 1;
                queue.physical_device_object_id = 1;
                queue.device_object_id = 1;
                memset(&fence, 0, sizeof(fence));
                fence.signaled = true;
                if (vkQueueSubmit2((VkQueue)&queue, 0, NULL, (VkFence)&fence) != VK_ERROR_FEATURE_NOT_PRESENT) return 103;
                if (!fence.signaled) return 104;
                queue.requested_feature_mask = PDOCKER_VK_FEATURE_SYNCHRONIZATION_2;
                if (vkQueueSubmit2((VkQueue)&queue, 0, NULL, VK_NULL_HANDLE) != VK_ERROR_FEATURE_NOT_PRESENT) return 105;
                queue.enabled_extension_mask = PDOCKER_VK_DEVICE_EXT_KHR_SYNCHRONIZATION_2;
                if (vkQueueSubmit2((VkQueue)&queue, 0, NULL, VK_NULL_HANDLE) != VK_SUCCESS) return 106;

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

                memset(&cmd, 0, sizeof(cmd));
                vkCmdPipelineBarrier2((VkCommandBuffer)&cmd, NULL);
                if (!reason_is(&cmd, "synchronization2-feature-disabled")) return 107;
                if (cmd.command_op_count || cmd.graphics_command_op_count) return 108;

                memset(&cmd, 0, sizeof(cmd));
                cmd.requested_feature_mask = PDOCKER_VK_FEATURE_SYNCHRONIZATION_2;
                vkCmdPipelineBarrier2((VkCommandBuffer)&cmd, NULL);
                if (!reason_is(&cmd, "synchronization2-feature-disabled")) return 109;

                VkDependencyInfo dependency_info;
                memset(&dependency_info, 0, sizeof(dependency_info));
                dependency_info.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO;
                memset(&cmd, 0, sizeof(cmd));
                cmd.requested_feature_mask = PDOCKER_VK_FEATURE_SYNCHRONIZATION_2;
                cmd.enabled_extension_mask = PDOCKER_VK_DEVICE_EXT_KHR_SYNCHRONIZATION_2;
                vkCmdPipelineBarrier2((VkCommandBuffer)&cmd, &dependency_info);
                if (cmd.recording_failed) return 110;
                if (cmd.command_op_count == 0) return 111;
                command_buffer_destroy_record_vectors(&cmd);

                memset(&cmd, 0, sizeof(cmd));
                vkCmdSetEvent2((VkCommandBuffer)&cmd, pdocker_vk_event_to_handle(&event), NULL);
                if (!reason_is(&cmd, "synchronization2-feature-disabled")) return 112;
                memset(&cmd, 0, sizeof(cmd));
                vkCmdResetEvent2((VkCommandBuffer)&cmd, pdocker_vk_event_to_handle(&event), 0);
                if (!reason_is(&cmd, "synchronization2-feature-disabled")) return 113;
                memset(&cmd, 0, sizeof(cmd));
                vkCmdWaitEvents2((VkCommandBuffer)&cmd, 0, NULL, NULL);
                if (!reason_is(&cmd, "synchronization2-feature-disabled")) return 114;
                memset(&cmd, 0, sizeof(cmd));
                vkCmdWriteTimestamp2((VkCommandBuffer)&cmd, 0, VK_NULL_HANDLE, 0);
                if (!reason_is(&cmd, "synchronization2-feature-disabled")) return 115;

                memset(&cmd, 0, sizeof(cmd));
                vkCmdSetCullMode((VkCommandBuffer)&cmd, VK_CULL_MODE_NONE);
                if (!reason_is(&cmd, "dynamic-state-feature-not-enabled")) return 123;
                memset(&cmd, 0, sizeof(cmd));
                cmd.requested_feature_mask = PDOCKER_VK_FEATURE_EXTENDED_DYNAMIC_STATE;
                vkCmdSetCullMode((VkCommandBuffer)&cmd, VK_CULL_MODE_NONE);
                if (cmd.recording_failed || cmd.dynamic_state_count == 0) return 124;
                command_buffer_destroy_record_vectors(&cmd);

                memset(&cmd, 0, sizeof(cmd));
                vkCmdSetRasterizerDiscardEnable((VkCommandBuffer)&cmd, VK_FALSE);
                if (!reason_is(&cmd, "dynamic-state-feature-not-enabled")) return 125;
                memset(&cmd, 0, sizeof(cmd));
                cmd.requested_feature_mask = PDOCKER_VK_FEATURE_EXTENDED_DYNAMIC_STATE_2;
                vkCmdSetRasterizerDiscardEnable((VkCommandBuffer)&cmd, VK_FALSE);
                if (cmd.recording_failed || cmd.dynamic_state_count == 0) return 126;
                command_buffer_destroy_record_vectors(&cmd);

                memset(&cmd, 0, sizeof(cmd));
                cmd.requested_feature_mask = PDOCKER_VK_FEATURE_EXTENDED_DYNAMIC_STATE_2 | PDOCKER_VK_FEATURE_LOGIC_OP;
                vkCmdSetLogicOpEXT((VkCommandBuffer)&cmd, VK_LOGIC_OP_COPY);
                if (!reason_is(&cmd, "dynamic-state-feature-not-enabled")) return 127;
                memset(&cmd, 0, sizeof(cmd));
                cmd.requested_feature_mask = PDOCKER_VK_FEATURE_EXTENDED_DYNAMIC_STATE_2_LOGIC_OP;
                vkCmdSetLogicOpEXT((VkCommandBuffer)&cmd, VK_LOGIC_OP_COPY);
                if (!reason_is(&cmd, "dynamic-logic-op-feature-not-enabled")) return 128;
                memset(&cmd, 0, sizeof(cmd));
                cmd.requested_feature_mask = PDOCKER_VK_FEATURE_EXTENDED_DYNAMIC_STATE_2_LOGIC_OP | PDOCKER_VK_FEATURE_LOGIC_OP;
                vkCmdSetLogicOpEXT((VkCommandBuffer)&cmd, VK_LOGIC_OP_COPY);
                if (cmd.recording_failed || cmd.dynamic_state_count == 0) return 129;
                command_buffer_destroy_record_vectors(&cmd);

                memset(&cmd, 0, sizeof(cmd));
                vkCmdBindVertexBuffers2((VkCommandBuffer)&cmd, 0, 1, (VkBuffer[]){{pdocker_vk_buffer_to_handle(&buffer)}}, (VkDeviceSize[]){{0}}, NULL, NULL);
                if (!reason_is(&cmd, "graphics-vertex-binding2-feature-disabled")) return 130;
                memset(&cmd, 0, sizeof(cmd));
                cmd.requested_feature_mask = PDOCKER_VK_FEATURE_EXTENDED_DYNAMIC_STATE;
                vkCmdBindVertexBuffers2((VkCommandBuffer)&cmd, 0, 1, (VkBuffer[]){{pdocker_vk_buffer_to_handle(&buffer)}}, (VkDeviceSize[]){{0}}, NULL, NULL);
                if (cmd.recording_failed || cmd.graphics_vertex_binding_snapshot_count == 0) return 131;
                command_buffer_destroy_record_vectors(&cmd);

            #ifdef VK_EXT_INDEX_TYPE_UINT8_EXTENSION_NAME
                memset(&cmd, 0, sizeof(cmd));
                record_index_buffer_binding((VkCommandBuffer)&cmd,
                                            pdocker_vk_buffer_to_handle(&buffer),
                                            0,
                                            VK_WHOLE_SIZE,
                                            VK_INDEX_TYPE_UINT8_EXT);
                if (!reason_is(&cmd, "graphics-index-type-uint8-feature-disabled")) return 14;

                memset(&cmd, 0, sizeof(cmd));
                cmd.requested_feature_mask = PDOCKER_VK_FEATURE_INDEX_TYPE_UINT8;
                record_index_buffer_binding((VkCommandBuffer)&cmd,
                                            pdocker_vk_buffer_to_handle(&buffer),
                                            0,
                                            VK_WHOLE_SIZE,
                                            VK_INDEX_TYPE_UINT8_EXT);
                if (cmd.recording_failed) return 15;
                command_buffer_destroy_record_vectors(&cmd);
            #endif

                memset(&cmd, 0, sizeof(cmd));
                vkCmdBeginRendering((VkCommandBuffer)&cmd, NULL);
                if (!reason_is(&cmd, "dynamic-rendering-feature-disabled")) return 16;

                memset(&cmd, 0, sizeof(cmd));
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

                VkDebugUtilsObjectNameInfoEXT name_info;
                memset(&name_info, 0, sizeof(name_info));
                name_info.sType = VK_STRUCTURE_TYPE_DEBUG_UTILS_OBJECT_NAME_INFO_EXT;
                name_info.objectType = VK_OBJECT_TYPE_BUFFER;
                name_info.objectHandle = 0x1234u;
                name_info.pObjectName = "buffer-name";
                if (vkSetDebugUtilsObjectNameEXT(VK_NULL_HANDLE, &name_info) != VK_SUCCESS) return 9;

                const uint32_t tag = 0xcafebabeu;
                VkDebugUtilsObjectTagInfoEXT tag_info;
                memset(&tag_info, 0, sizeof(tag_info));
                tag_info.sType = VK_STRUCTURE_TYPE_DEBUG_UTILS_OBJECT_TAG_INFO_EXT;
                tag_info.objectType = VK_OBJECT_TYPE_BUFFER;
                tag_info.objectHandle = 0x1234u;
                tag_info.tagName = 1u;
                tag_info.tagSize = sizeof(tag);
                tag_info.pTag = &tag;
                if (vkSetDebugUtilsObjectTagEXT(VK_NULL_HANDLE, &tag_info) != VK_SUCCESS) return 10;

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
                if (vkSetDebugUtilsObjectNameEXT(VK_NULL_HANDLE, &name_info) != VK_ERROR_INITIALIZATION_FAILED) return 13;

                vkDestroyDebugUtilsMessengerEXT(instance, messenger, NULL);
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
                if (vkEnumerateDeviceExtensionProperties(VK_NULL_HANDLE, NULL, &extension_count, NULL) != VK_SUCCESS) return 9;
                VkExtensionProperties extensions[64];
                memset(extensions, 0, sizeof(extensions));
                uint32_t capacity = 64;
                if (vkEnumerateDeviceExtensionProperties(VK_NULL_HANDLE, NULL, &capacity, extensions) != VK_SUCCESS) return 10;
                VkBool32 found = VK_FALSE;
                for (uint32_t i = 0; i < capacity; ++i) {{
                    if (strcmp(extensions[i].extensionName, VK_EXT_PRIVATE_DATA_EXTENSION_NAME) == 0) found = VK_TRUE;
                }}
                if (!found || !device_extension_advertised_name(VK_EXT_PRIVATE_DATA_EXTENSION_NAME)) return 11;
                if (proc_address("vkCreatePrivateDataSlot") != NULL) return 26;
                if (proc_address("vkCreatePrivateDataSlotEXT") == NULL) return 27;
                if (proc_address("vkSetPrivateData") != NULL) return 28;
                if (proc_address("vkSetPrivateDataEXT") == NULL) return 29;

                VkPrivateDataSlotCreateInfo slot_info;
                memset(&slot_info, 0, sizeof(slot_info));
                slot_info.sType = VK_STRUCTURE_TYPE_PRIVATE_DATA_SLOT_CREATE_INFO;
                VkPrivateDataSlot slot = (VkPrivateDataSlot)(uintptr_t)0xfeedu;
                if (vkCreatePrivateDataSlot(VK_NULL_HANDLE, &slot_info, NULL, &slot) != VK_SUCCESS || slot == VK_NULL_HANDLE) return 12;
                if (vkSetPrivateData(VK_NULL_HANDLE, VK_OBJECT_TYPE_BUFFER, 0x1234u, slot, 0xabcdu) != VK_SUCCESS) return 13;
                uint64_t data = 0;
                vkGetPrivateData(VK_NULL_HANDLE, VK_OBJECT_TYPE_BUFFER, 0x1234u, slot, &data);
                if (data != 0xabcdu) return 14;
                if (vkSetPrivateData(VK_NULL_HANDLE, VK_OBJECT_TYPE_BUFFER, 0x1234u, slot, 0xdefu) != VK_SUCCESS) return 15;
                vkGetPrivateData(VK_NULL_HANDLE, VK_OBJECT_TYPE_BUFFER, 0x1234u, slot, &data);
                if (data != 0xdefu) return 16;
                vkGetPrivateData(VK_NULL_HANDLE, VK_OBJECT_TYPE_IMAGE, 0x1234u, slot, &data);
                if (data != 0) return 17;
                if (vkSetPrivateData(VK_NULL_HANDLE, VK_OBJECT_TYPE_BUFFER, 0x1234u, slot, 0) != VK_SUCCESS) return 18;
                data = 0x777u;
                vkGetPrivateData(VK_NULL_HANDLE, VK_OBJECT_TYPE_BUFFER, 0x1234u, slot, &data);
                if (data != 0) return 19;

                VkPrivateDataSlot invalid_slot = (VkPrivateDataSlot)(uintptr_t)0xfeedu;
                memset(&slot_info, 0, sizeof(slot_info));
                slot_info.sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO;
                if (vkCreatePrivateDataSlot(VK_NULL_HANDLE, &slot_info, NULL, &invalid_slot) != VK_ERROR_INITIALIZATION_FAILED) return 20;
                if (invalid_slot != VK_NULL_HANDLE) return 21;

                VkBaseOutStructure unknown;
                memset(&unknown, 0, sizeof(unknown));
                unknown.sType = (VkStructureType)0x3fffffff;
                memset(&slot_info, 0, sizeof(slot_info));
                slot_info.sType = VK_STRUCTURE_TYPE_PRIVATE_DATA_SLOT_CREATE_INFO;
                slot_info.pNext = &unknown;
                if (vkCreatePrivateDataSlot(VK_NULL_HANDLE, &slot_info, NULL, &invalid_slot) != VK_ERROR_FEATURE_NOT_PRESENT) return 22;

                memset(&slot_info, 0, sizeof(slot_info));
                slot_info.sType = VK_STRUCTURE_TYPE_PRIVATE_DATA_SLOT_CREATE_INFO;
                slot_info.flags = (VkPrivateDataSlotCreateFlags)1u;
                if (vkCreatePrivateDataSlot(VK_NULL_HANDLE, &slot_info, NULL, &invalid_slot) != VK_ERROR_FEATURE_NOT_PRESENT) return 23;
                if (vkSetPrivateData(VK_NULL_HANDLE, VK_OBJECT_TYPE_BUFFER, 0x1234u, VK_NULL_HANDLE, 1u) != VK_ERROR_INITIALIZATION_FAILED) return 24;
                if (vkSetPrivateData(VK_NULL_HANDLE, VK_OBJECT_TYPE_UNKNOWN, 0x1234u, slot, 1u) != VK_ERROR_INITIALIZATION_FAILED) return 25;
                vkDestroyPrivateDataSlot(VK_NULL_HANDLE, slot, NULL);
                vkDestroyPrivateDataSlot(VK_NULL_HANDLE, VK_NULL_HANDLE, NULL);
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stderr)

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



    def test_shader_layout_memory_model_extensions_are_false_only(self):
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
                if (!device_extension_advertised_name(VK_EXT_SCALAR_BLOCK_LAYOUT_EXTENSION_NAME) ||
                    validate_device_extensions(&create_info) != VK_SUCCESS ||
                    validate_device_feature_requests(&create_info) != VK_SUCCESS) {{
                    fprintf(stderr, "VK_EXT_scalar_block_layout false-only enable failed\\n");
                    return 5;
                }}
            #endif
            #ifdef VK_KHR_UNIFORM_BUFFER_STANDARD_LAYOUT_EXTENSION_NAME
                const char *uniform_extensions[] = {{ VK_KHR_UNIFORM_BUFFER_STANDARD_LAYOUT_EXTENSION_NAME }};
                create_info.enabledExtensionCount = 1;
                create_info.ppEnabledExtensionNames = uniform_extensions;
                if (!device_extension_advertised_name(VK_KHR_UNIFORM_BUFFER_STANDARD_LAYOUT_EXTENSION_NAME) ||
                    validate_device_extensions(&create_info) != VK_SUCCESS ||
                    validate_device_feature_requests(&create_info) != VK_SUCCESS) {{
                    fprintf(stderr, "VK_KHR_uniform_buffer_standard_layout false-only enable failed\\n");
                    return 6;
                }}
            #endif
            #ifdef VK_KHR_SHADER_SUBGROUP_EXTENDED_TYPES_EXTENSION_NAME
                const char *subgroup_extensions[] = {{ VK_KHR_SHADER_SUBGROUP_EXTENDED_TYPES_EXTENSION_NAME }};
                create_info.enabledExtensionCount = 1;
                create_info.ppEnabledExtensionNames = subgroup_extensions;
                if (!device_extension_advertised_name(VK_KHR_SHADER_SUBGROUP_EXTENDED_TYPES_EXTENSION_NAME) ||
                    validate_device_extensions(&create_info) != VK_SUCCESS ||
                    validate_device_feature_requests(&create_info) != VK_SUCCESS) {{
                    fprintf(stderr, "VK_KHR_shader_subgroup_extended_types false-only enable failed\\n");
                    return 7;
                }}
            #endif
            #ifdef VK_KHR_VULKAN_MEMORY_MODEL_EXTENSION_NAME
                const char *memory_model_extensions[] = {{ VK_KHR_VULKAN_MEMORY_MODEL_EXTENSION_NAME }};
                create_info.enabledExtensionCount = 1;
                create_info.ppEnabledExtensionNames = memory_model_extensions;
                if (!device_extension_advertised_name(VK_KHR_VULKAN_MEMORY_MODEL_EXTENSION_NAME) ||
                    validate_device_extensions(&create_info) != VK_SUCCESS ||
                    validate_device_feature_requests(&create_info) != VK_SUCCESS) {{
                    fprintf(stderr, "VK_KHR_vulkan_memory_model false-only enable failed\\n");
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
                if (!image_memory_obj || image_memory_obj->dedicated_image != pdocker_vk_image_from_handle(image_a) ||
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
            #endif

                if (validate_and_fill_pipeline_feedback_pnext(
                        "unit-pipeline-robustness", &robustness, 1u, false) != VK_SUCCESS) {{
                    fprintf(stderr, "default pipeline robustness was rejected\\n");
                    return 2;
                }}
                robustness.storageBuffers = VK_PIPELINE_ROBUSTNESS_BUFFER_BEHAVIOR_DISABLED;
                if (validate_and_fill_pipeline_feedback_pnext(
                        "unit-pipeline-robustness", &robustness, 1u, false) == VK_SUCCESS) {{
                    fprintf(stderr, "non-default storage robustness was accepted\\n");
                    return 3;
                }}
                robustness.storageBuffers = VK_PIPELINE_ROBUSTNESS_BUFFER_BEHAVIOR_DEVICE_DEFAULT;
                robustness.images = VK_PIPELINE_ROBUSTNESS_IMAGE_BEHAVIOR_ROBUST_IMAGE_ACCESS;
                if (validate_and_fill_pipeline_feedback_pnext(
                        "unit-pipeline-robustness", &robustness, 1u, true) == VK_SUCCESS) {{
                    fprintf(stderr, "non-default image robustness was accepted\\n");
                    return 4;
                }}
                robustness.images = VK_PIPELINE_ROBUSTNESS_IMAGE_BEHAVIOR_DEVICE_DEFAULT;
                feedback_info.sType = VK_STRUCTURE_TYPE_PIPELINE_CREATION_FEEDBACK_CREATE_INFO;
                feedback_info.pPipelineCreationFeedback = &feedback;
                feedback_info.pNext = &robustness;
                if (validate_and_fill_pipeline_feedback_pnext(
                        "unit-pipeline-robustness", &feedback_info, 1u, false) != VK_SUCCESS) {{
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
                        "unit-pipeline-feedback", &feedback_info, 1u, false) != VK_SUCCESS) {{
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
                        "unit-pipeline-feedback", &feedback_info, 1u, false) == VK_SUCCESS) {{
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

                vkGetPhysicalDeviceProperties2(VK_NULL_HANDLE, &properties2);
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
                if (!device_extension_advertised_name(VK_KHR_VARIABLE_POINTERS_EXTENSION_NAME) ||
                    validate_device_extensions(&create_info) != VK_SUCCESS ||
                    validate_device_feature_requests(&create_info) != VK_SUCCESS) {{
                    fprintf(stderr, "VK_KHR_variable_pointers false-only enable failed\\n");
                    return 11;
                }}
            #endif
            #ifdef VK_KHR_SHADER_DRAW_PARAMETERS_EXTENSION_NAME
                const char *shader_draw_extensions[] = {{ VK_KHR_SHADER_DRAW_PARAMETERS_EXTENSION_NAME }};
                create_info.enabledExtensionCount = 1;
                create_info.ppEnabledExtensionNames = shader_draw_extensions;
                if (!device_extension_advertised_name(VK_KHR_SHADER_DRAW_PARAMETERS_EXTENSION_NAME) ||
                    validate_device_extensions(&create_info) != VK_SUCCESS ||
                    validate_device_feature_requests(&create_info) != VK_SUCCESS) {{
                    fprintf(stderr, "VK_KHR_shader_draw_parameters false-only enable failed\\n");
                    return 12;
                }}
            #endif
            #ifdef VK_KHR_SHADER_ATOMIC_INT64_EXTENSION_NAME
                const char *atomic64_extensions[] = {{ VK_KHR_SHADER_ATOMIC_INT64_EXTENSION_NAME }};
                create_info.enabledExtensionCount = 1;
                create_info.ppEnabledExtensionNames = atomic64_extensions;
                if (!device_extension_advertised_name(VK_KHR_SHADER_ATOMIC_INT64_EXTENSION_NAME) ||
                    validate_device_extensions(&create_info) != VK_SUCCESS ||
                    validate_device_feature_requests(&create_info) != VK_SUCCESS) {{
                    fprintf(stderr, "VK_KHR_shader_atomic_int64 false-only enable failed\\n");
                    return 13;
                }}
            #endif
            #ifdef VK_KHR_IMAGELESS_FRAMEBUFFER_EXTENSION_NAME
                const char *imageless_extensions[] = {{ VK_KHR_IMAGELESS_FRAMEBUFFER_EXTENSION_NAME }};
                create_info.enabledExtensionCount = 1;
                create_info.ppEnabledExtensionNames = imageless_extensions;
                if (!device_extension_advertised_name(VK_KHR_IMAGELESS_FRAMEBUFFER_EXTENSION_NAME) ||
                    validate_device_extensions(&create_info) != VK_SUCCESS ||
                    validate_device_feature_requests(&create_info) != VK_SUCCESS) {{
                    fprintf(stderr, "VK_KHR_imageless_framebuffer false-only enable failed\\n");
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


    def test_shader_demote_feature_is_queryable_but_not_enableable(self):
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
                if (!device_extension_advertised_name(VK_EXT_SHADER_DEMOTE_TO_HELPER_INVOCATION_EXTENSION_NAME)) {{
                    fprintf(stderr, "VK_EXT_shader_demote_to_helper_invocation was not advertised\\n");
                    return 7;
                }}
                uint32_t extension_count = 64;
                VkExtensionProperties extensions[64];
                memset(extensions, 0, sizeof(extensions));
                if (vkEnumerateDeviceExtensionProperties(
                        VK_NULL_HANDLE, NULL, &extension_count, extensions) != VK_SUCCESS) {{
                    fprintf(stderr, "device extension enumeration failed\\n");
                    return 8;
                }}
                int saw_demote = 0;
                for (uint32_t i = 0; i < extension_count; ++i) {{
                    if (strcmp(extensions[i].extensionName,
                               VK_EXT_SHADER_DEMOTE_TO_HELPER_INVOCATION_EXTENSION_NAME) == 0) {{
                        saw_demote = 1;
                    }}
                }}
                if (!saw_demote) {{
                    fprintf(stderr, "VK_EXT_shader_demote_to_helper_invocation missing from enumeration\\n");
                    return 9;
                }}
                const char *enabled_extensions[] = {{
                    VK_EXT_SHADER_DEMOTE_TO_HELPER_INVOCATION_EXTENSION_NAME,
                }};
                create_info.enabledExtensionCount = 1;
                create_info.ppEnabledExtensionNames = enabled_extensions;
                if (validate_device_extensions(&create_info) != VK_SUCCESS) {{
                    fprintf(stderr, "VK_EXT_shader_demote_to_helper_invocation extension enable was rejected\\n");
                    return 10;
                }}
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
                        VK_NULL_HANDLE, NULL, &extension_count, extensions) != VK_SUCCESS) {{
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
                if (vkEnumerateDeviceExtensionProperties(NULL, NULL, &count, NULL) != VK_SUCCESS) {{
                    return 2;
                }}
                if (count == 0 || count > PDOCKER_VK_MAX_DEVICE_EXTENSIONS) {{
                    fprintf(stderr, "unexpected extension count %u\\n", count);
                    return 3;
                }}
                if (count > 1) {{
                    VkExtensionProperties one_property[1];
                    uint32_t one_capacity = 1;
                    if (vkEnumerateDeviceExtensionProperties(NULL, NULL, &one_capacity, one_property) != VK_INCOMPLETE ||
                        one_capacity != 1) {{
                        fprintf(stderr, "truncated device extension enumeration did not return VK_INCOMPLETE\\n");
                        return 11;
                    }}
                }}
                VkExtensionProperties properties[PDOCKER_VK_MAX_DEVICE_EXTENSIONS];
                memset(properties, 0, sizeof(properties));
                uint32_t capacity = PDOCKER_VK_MAX_DEVICE_EXTENSIONS;
                if (vkEnumerateDeviceExtensionProperties(NULL, NULL, &capacity, properties) != VK_SUCCESS) {{
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
                    VK_NULL_HANDLE, &info, &properties);
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
                rc = vkGetPhysicalDeviceImageFormatProperties2(VK_NULL_HANDLE, &info, &properties);
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
                rc = vkGetPhysicalDeviceImageFormatProperties2(VK_NULL_HANDLE, &info, &properties);
                if (rc != VK_ERROR_FORMAT_NOT_SUPPORTED) {{
                    fprintf(stderr, "invalid filter-cubic image-view query returned %d\\n", rc);
                    return 8;
                }}
                external_info.pNext = NULL;

                external_info.handleType = VK_EXTERNAL_MEMORY_HANDLE_TYPE_OPAQUE_FD_BIT;
                memset(&properties, 0, sizeof(properties));
                properties.sType = VK_STRUCTURE_TYPE_IMAGE_FORMAT_PROPERTIES_2;
                rc = vkGetPhysicalDeviceImageFormatProperties2(VK_NULL_HANDLE, &info, &properties);
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


    def test_external_memory_extension_is_zero_handle_only(self):
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
                if (!device_extension_advertised_name(VK_KHR_EXTERNAL_MEMORY_EXTENSION_NAME)) return 2;
                uint32_t count = 64;
                VkExtensionProperties extensions[64];
                memset(extensions, 0, sizeof(extensions));
                if (vkEnumerateDeviceExtensionProperties(VK_NULL_HANDLE, NULL, &count, extensions) != VK_SUCCESS) return 3;
                if (!extension_seen(extensions, count, VK_KHR_EXTERNAL_MEMORY_EXTENSION_NAME)) return 4;

                const char *enabled[] = {{ VK_KHR_EXTERNAL_MEMORY_EXTENSION_NAME }};
                VkDeviceCreateInfo device_info;
                memset(&device_info, 0, sizeof(device_info));
                device_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                device_info.enabledExtensionCount = 1;
                device_info.ppEnabledExtensionNames = enabled;
                if (validate_device_extensions(&device_info) != VK_SUCCESS) return 5;

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
                if (validate_memory_allocate_pnext(&export_info) != VK_SUCCESS) return 10;
                export_info.handleTypes = VK_EXTERNAL_MEMORY_HANDLE_TYPE_OPAQUE_FD_BIT;
                if (validate_memory_allocate_pnext(&export_info) != VK_ERROR_FEATURE_NOT_PRESENT) return 11;

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


    def test_external_semaphore_and_fence_extensions_are_zero_handle_only(self):
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
                if (vkEnumerateDeviceExtensionProperties(VK_NULL_HANDLE, NULL, &count, extensions) != VK_SUCCESS) return 2;

            #ifdef VK_KHR_EXTERNAL_SEMAPHORE_EXTENSION_NAME
                if (!device_extension_advertised_name(VK_KHR_EXTERNAL_SEMAPHORE_EXTENSION_NAME)) return 3;
                if (!extension_seen(extensions, count, VK_KHR_EXTERNAL_SEMAPHORE_EXTENSION_NAME)) return 4;
                const char *semaphore_enabled[] = {{ VK_KHR_EXTERNAL_SEMAPHORE_EXTENSION_NAME }};
                VkDeviceCreateInfo semaphore_device_info;
                memset(&semaphore_device_info, 0, sizeof(semaphore_device_info));
                semaphore_device_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                semaphore_device_info.enabledExtensionCount = 1;
                semaphore_device_info.ppEnabledExtensionNames = semaphore_enabled;
                if (validate_device_extensions(&semaphore_device_info) != VK_SUCCESS) return 5;

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
                if (!device_extension_advertised_name(VK_KHR_EXTERNAL_FENCE_EXTENSION_NAME)) return 12;
                if (!extension_seen(extensions, count, VK_KHR_EXTERNAL_FENCE_EXTENSION_NAME)) return 13;
                const char *fence_enabled[] = {{ VK_KHR_EXTERNAL_FENCE_EXTENSION_NAME }};
                VkDeviceCreateInfo fence_device_info;
                memset(&fence_device_info, 0, sizeof(fence_device_info));
                fence_device_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                fence_device_info.enabledExtensionCount = 1;
                fence_device_info.ppEnabledExtensionNames = fence_enabled;
                if (validate_device_extensions(&fence_device_info) != VK_SUCCESS) return 14;

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

                VkBuffer buffer_handle = VK_NULL_HANDLE;
                VkResult rc = vkCreateBuffer((VkDevice)(uintptr_t)0x1u, &info, NULL, &buffer_handle);
                if (rc != VK_SUCCESS || buffer_handle == VK_NULL_HANDLE) {{
                    fprintf(stderr, "usage2-only buffer create failed rc=%d handle=%p\\n", rc, (void *)buffer_handle);
                    return 2;
                }}
                PdockerVkBuffer *buffer = pdocker_vk_buffer_from_handle(buffer_handle);
                if (!buffer || buffer->usage != VK_BUFFER_USAGE_STORAGE_BUFFER_BIT) {{
                    fprintf(stderr, "effective usage was not stored from usage2: 0x%x\\n", buffer ? buffer->usage : 0u);
                    return 3;
                }}
                vkDestroyBuffer((VkDevice)(uintptr_t)0x1u, buffer_handle, NULL);

                usage2.usage = 0;
                buffer_handle = VK_NULL_HANDLE;
                rc = vkCreateBuffer((VkDevice)(uintptr_t)0x1u, &info, NULL, &buffer_handle);
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
                if (validate_buffer_view_create_pnext(&view_info, &buffer, &texel_usage) != VK_SUCCESS) {{
                    fprintf(stderr, "matching usage2 texel subset was rejected\\n");
                    return 2;
                }}
                if (texel_usage != (VK_BUFFER_USAGE_UNIFORM_TEXEL_BUFFER_BIT |
                                    VK_BUFFER_USAGE_STORAGE_TEXEL_BUFFER_BIT)) {{
                    fprintf(stderr, "unexpected texel usage 0x%x\\n", texel_usage);
                    return 3;
                }}

                usage2.usage = VK_BUFFER_USAGE_UNIFORM_TEXEL_BUFFER_BIT;
                if (validate_buffer_view_create_pnext(&view_info, &buffer, &texel_usage) == VK_SUCCESS) {{
                    fprintf(stderr, "narrowing usage2 was accepted without a view-usage ABI field\\n");
                    return 4;
                }}

                usage2.usage = VK_BUFFER_USAGE_UNIFORM_TEXEL_BUFFER_BIT |
                               VK_BUFFER_USAGE_TRANSFER_DST_BIT;
                if (validate_buffer_view_create_pnext(&view_info, &buffer, &texel_usage) == VK_SUCCESS) {{
                    fprintf(stderr, "non-texel usage2 bit was accepted\\n");
                    return 5;
                }}

                usage2.usage = 0;
                if (validate_buffer_view_create_pnext(&view_info, &buffer, &texel_usage) == VK_SUCCESS) {{
                    fprintf(stderr, "zero usage2 was accepted\\n");
                    return 6;
                }}

                VkBaseInStructure unknown;
                memset(&unknown, 0, sizeof(unknown));
                unknown.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO;
                view_info.pNext = &unknown;
                if (validate_buffer_view_create_pnext(&view_info, &buffer, &texel_usage) == VK_SUCCESS) {{
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
                VkResult rc = validate_image_view_create_info_for_transport(&view, NULL);
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

                VkImageFormatProperties props;
                memset(&props, 0, sizeof(props));
                if (vkGetPhysicalDeviceImageFormatProperties(
                        VK_NULL_HANDLE, VK_FORMAT_R8G8B8A8_UNORM, VK_IMAGE_TYPE_3D,
                        VK_IMAGE_TILING_OPTIMAL, VK_IMAGE_USAGE_SAMPLED_BIT,
                        VK_IMAGE_CREATE_2D_ARRAY_COMPATIBLE_BIT, &props) != VK_SUCCESS) {{
                    fprintf(stderr, "format query rejected valid 3D 2D-array-compatible flag\\n");
                    return 24;
                }}
                if (vkGetPhysicalDeviceImageFormatProperties(
                        VK_NULL_HANDLE, VK_FORMAT_R8G8B8A8_UNORM, VK_IMAGE_TYPE_2D,
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
                if (validate_image_view_create_info_for_transport(&min_lod_view, NULL) != VK_SUCCESS) {{
                    fprintf(stderr, "no-op image view minLod pNext was rejected\\n");
                    return 26;
                }}
                min_lod.minLod = 1.0f;
                if (validate_image_view_create_info_for_transport(&min_lod_view, NULL) != VK_ERROR_FEATURE_NOT_PRESENT) {{
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
                vkGetPhysicalDeviceFormatProperties(VK_NULL_HANDLE, format, &format_props);
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

                VkImageFormatProperties image_props;
                memset(&image_props, 0xff, sizeof(image_props));
                VkResult rc = vkGetPhysicalDeviceImageFormatProperties(
                    VK_NULL_HANDLE,
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
                if (validate_image_view_create_info_for_transport(&view, NULL) != VK_ERROR_FORMAT_NOT_SUPPORTED) {{
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
                VkImageFormatProperties props;
                memset(&props, 0xff, sizeof(props));
                VkResult rc = vkGetPhysicalDeviceImageFormatProperties(
                    VK_NULL_HANDLE,
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
                vkGetPhysicalDeviceProperties(VK_NULL_HANDLE, &device_props);

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
                vkDestroyDevice(valid_device, NULL);

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
                if (validate_memory_allocate_pnext(&capture) != VK_SUCCESS) {{
                    fprintf(stderr, "zero opaque capture address allocation pNext was rejected\\n");
                    return 2;
                }}

                capture.opaqueCaptureAddress = 0x1000u;
                if (validate_memory_allocate_pnext(&capture) != VK_ERROR_FEATURE_NOT_PRESENT) {{
                    fprintf(stderr, "nonzero opaque capture address allocation pNext was accepted\\n");
                    return 3;
                }}

                VkExportMemoryAllocateInfo export_info;
                memset(&export_info, 0, sizeof(export_info));
                export_info.sType = VK_STRUCTURE_TYPE_EXPORT_MEMORY_ALLOCATE_INFO;
                export_info.handleTypes = 0;
                if (validate_memory_allocate_pnext(&export_info) != VK_SUCCESS) {{
                    fprintf(stderr, "zero export memory handle-types pNext was rejected\\n");
                    return 4;
                }}
                export_info.handleTypes = VK_EXTERNAL_MEMORY_HANDLE_TYPE_OPAQUE_FD_BIT;
                if (validate_memory_allocate_pnext(&export_info) != VK_ERROR_FEATURE_NOT_PRESENT) {{
                    fprintf(stderr, "nonzero export memory handle-types pNext was accepted\\n");
                    return 5;
                }}

                VkMemoryPriorityAllocateInfoEXT priority_info;
                memset(&priority_info, 0, sizeof(priority_info));
                priority_info.sType = VK_STRUCTURE_TYPE_MEMORY_PRIORITY_ALLOCATE_INFO_EXT;
                priority_info.priority = 0.5f;
                if (validate_memory_allocate_pnext(&priority_info) != VK_ERROR_FEATURE_NOT_PRESENT) {{
                    fprintf(stderr, "default memory priority pNext was accepted without transport\\n");
                    return 6;
                }}
                priority_info.priority = 1.0f;
                if (validate_memory_allocate_pnext(&priority_info) != VK_ERROR_FEATURE_NOT_PRESENT) {{
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
                if (validate_memory_allocate_pnext(&flags) != VK_SUCCESS) {{
                    fprintf(stderr, "no-op memory allocate flags + export + capture chain was rejected\\n");
                    return 6;
                }}

                export_info.pNext = &priority_info;
                priority_info.sType = VK_STRUCTURE_TYPE_MEMORY_PRIORITY_ALLOCATE_INFO_EXT;
                priority_info.priority = 0.5f;
                priority_info.pNext = &capture;
                if (validate_memory_allocate_pnext(&flags) != VK_ERROR_FEATURE_NOT_PRESENT) {{
                    fprintf(stderr, "memory priority in allocation chain was accepted without transport\\n");
                    return 9;
                }}
                export_info.pNext = &capture;

                flags.deviceMask = 2;
                if (validate_memory_allocate_pnext(&flags) == VK_SUCCESS) {{
                    fprintf(stderr, "multi-device memory allocation mask was accepted\\n");
                    return 7;
                }}

                VkBaseInStructure unknown;
                memset(&unknown, 0, sizeof(unknown));
                unknown.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO;
                if (validate_memory_allocate_pnext(&unknown) == VK_SUCCESS) {{
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


if __name__ == "__main__":
    unittest.main()

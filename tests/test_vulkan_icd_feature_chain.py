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

    def test_subpass_merge_feedback_feature_is_queryable_but_not_enableable(self):
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
                if (feedback.subpassMergeFeedback != VK_FALSE) {{
                    return 4;
                }}

                VkDeviceCreateInfo create_info;
                memset(&create_info, 0, sizeof(create_info));
                create_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                feedback.subpassMergeFeedback = VK_TRUE;
                create_info.pNext = &feedback;
                if (validate_device_feature_requests(&create_info) == VK_SUCCESS) {{
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

    def test_private_data_feature_and_device_create_info_are_noop_only(self):
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
                if (private_features.sType != VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PRIVATE_DATA_FEATURES) {{
                    fprintf(stderr, "private feature sType was not preserved\\n");
                    return 2;
                }}
                if (private_features.pNext != NULL) {{
                    fprintf(stderr, "private feature pNext was not preserved\\n");
                    return 3;
                }}
                if (private_features.privateData != VK_FALSE) {{
                    fprintf(stderr, "privateData was advertised without private-data API support\\n");
                    return 4;
                }}

                VkDeviceCreateInfo create_info;
                memset(&create_info, 0, sizeof(create_info));
                create_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
                private_features.privateData = VK_TRUE;
                create_info.pNext = &private_features;
                if (validate_device_feature_requests(&create_info) == VK_SUCCESS) {{
                    fprintf(stderr, "privateData=true was accepted\\n");
                    return 5;
                }}
                private_features.privateData = VK_FALSE;
                if (validate_device_feature_requests(&create_info) != VK_SUCCESS) {{
                    fprintf(stderr, "privateData=false was rejected\\n");
                    return 6;
                }}

                VkDevicePrivateDataCreateInfo private_create;
                memset(&private_create, 0, sizeof(private_create));
                private_create.sType = VK_STRUCTURE_TYPE_DEVICE_PRIVATE_DATA_CREATE_INFO;
                private_create.privateDataSlotRequestCount = 0;
                create_info.pNext = &private_create;
                if (validate_device_feature_requests(&create_info) != VK_SUCCESS) {{
                    fprintf(stderr, "zero privateDataSlotRequestCount was rejected\\n");
                    return 7;
                }}
                private_create.privateDataSlotRequestCount = 1;
                if (validate_device_feature_requests(&create_info) == VK_SUCCESS) {{
                    fprintf(stderr, "nonzero privateDataSlotRequestCount was accepted\\n");
                    return 8;
                }}

                memset(&private_features, 0, sizeof(private_features));
                memset(&private_create, 0, sizeof(private_create));
                private_features.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PRIVATE_DATA_FEATURES;
                private_features.privateData = VK_FALSE;
                private_features.pNext = &private_create;
                private_create.sType = VK_STRUCTURE_TYPE_DEVICE_PRIVATE_DATA_CREATE_INFO;
                private_create.privateDataSlotRequestCount = 0;
                create_info.pNext = &private_features;
                if (validate_device_feature_requests(&create_info) != VK_SUCCESS) {{
                    fprintf(stderr, "privateData=false plus zero slot request chain was rejected\\n");
                    return 9;
                }}
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_dynamic_rendering_local_read_feature_is_queryable_but_not_enableable(self):
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
                local_read.dynamicRenderingLocalRead = VK_TRUE;
                create_info.pNext = &local_read;
                if (validate_device_feature_requests(&create_info) == VK_SUCCESS) {{
                    return 5;
                }}
                local_read.dynamicRenderingLocalRead = VK_FALSE;
                if (validate_device_feature_requests(&create_info) != VK_SUCCESS) {{
                    return 6;
                }}
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_advertised_feature_extensions_are_enumerated_together(self):
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
                VkExtensionProperties properties[16];
                memset(properties, 0, sizeof(properties));
                uint32_t capacity = 16;
                if (vkEnumerateDeviceExtensionProperties(NULL, NULL, &capacity, properties) != VK_SUCCESS) {{
                    return 3;
                }}
                int has_storage8 = 0;
                int has_float16_int8 = 0;
                for (uint32_t i = 0; i < capacity; ++i) {{
                    if (strcmp(properties[i].extensionName, VK_KHR_8BIT_STORAGE_EXTENSION_NAME) == 0) {{
                        has_storage8 = 1;
                    }}
                    if (strcmp(properties[i].extensionName, VK_KHR_SHADER_FLOAT16_INT8_EXTENSION_NAME) == 0) {{
                        has_float16_int8 = 1;
                    }}
                }}
                if (!has_storage8 || !has_float16_int8) {{
                    fprintf(stderr,
                            "missing extension storage8=%d float16_int8=%d count=%u\\n",
                            has_storage8,
                            has_float16_int8,
                            capacity);
                    return 4;
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
                VkQueue queue = VK_NULL_HANDLE;
                vkGetDeviceQueue2((VkDevice)(uintptr_t)0x1u, &info, &queue);
                if (queue == VK_NULL_HANDLE) {{
                    fprintf(stderr, "valid device queue info did not return advertised queue\\n");
                    return 2;
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

                VkMemoryAllocateFlagsInfo flags;
                memset(&flags, 0, sizeof(flags));
                memset(&capture, 0, sizeof(capture));
                memset(&export_info, 0, sizeof(export_info));
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


if __name__ == "__main__":
    unittest.main()

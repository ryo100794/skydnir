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
                if (proc_address("vkGetDeviceGroupPeerMemoryFeaturesKHR") != NULL) return 38;
                if (proc_address("vkCmdSetDeviceMaskKHR") != NULL) return 39;
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

    def test_debug_marker_extension_is_icd_local_metadata(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            int main(void) {{
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
                if (!found || !device_extension_advertised_name(VK_EXT_DEBUG_MARKER_EXTENSION_NAME)) return 4;

                VkDebugMarkerObjectNameInfoEXT name_info;
                memset(&name_info, 0, sizeof(name_info));
                name_info.sType = VK_STRUCTURE_TYPE_DEBUG_MARKER_OBJECT_NAME_INFO_EXT;
                name_info.objectType = VK_DEBUG_REPORT_OBJECT_TYPE_BUFFER_EXT;
                name_info.object = 0x1234u;
                name_info.pObjectName = "legacy-buffer-name";
                if (vkDebugMarkerSetObjectNameEXT(VK_NULL_HANDLE, &name_info) != VK_SUCCESS) return 5;

                const uint32_t tag = 0x13572468u;
                VkDebugMarkerObjectTagInfoEXT tag_info;
                memset(&tag_info, 0, sizeof(tag_info));
                tag_info.sType = VK_STRUCTURE_TYPE_DEBUG_MARKER_OBJECT_TAG_INFO_EXT;
                tag_info.objectType = VK_DEBUG_REPORT_OBJECT_TYPE_BUFFER_EXT;
                tag_info.object = 0x1234u;
                tag_info.tagName = 7u;
                tag_info.tagSize = sizeof(tag);
                tag_info.pTag = &tag;
                if (vkDebugMarkerSetObjectTagEXT(VK_NULL_HANDLE, &tag_info) != VK_SUCCESS) return 6;

                VkDebugMarkerMarkerInfoEXT marker_info;
                memset(&marker_info, 0, sizeof(marker_info));
                marker_info.sType = VK_STRUCTURE_TYPE_DEBUG_MARKER_MARKER_INFO_EXT;
                marker_info.pMarkerName = "legacy-marker";
                vkCmdDebugMarkerBeginEXT(VK_NULL_HANDLE, &marker_info);
                vkCmdDebugMarkerInsertEXT(VK_NULL_HANDLE, &marker_info);
                vkCmdDebugMarkerEndEXT(VK_NULL_HANDLE);

                memset(&name_info, 0, sizeof(name_info));
                name_info.sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO;
                if (vkDebugMarkerSetObjectNameEXT(VK_NULL_HANDLE, &name_info) != VK_ERROR_INITIALIZATION_FAILED) return 7;
                memset(&tag_info, 0, sizeof(tag_info));
                tag_info.sType = VK_STRUCTURE_TYPE_DEBUG_MARKER_OBJECT_TAG_INFO_EXT;
                tag_info.objectType = VK_DEBUG_REPORT_OBJECT_TYPE_BUFFER_EXT;
                tag_info.object = 0x1234u;
                tag_info.tagSize = 4u;
                tag_info.pTag = NULL;
                if (vkDebugMarkerSetObjectTagEXT(VK_NULL_HANDLE, &tag_info) != VK_ERROR_INITIALIZATION_FAILED) return 8;
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stderr)

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

    def test_memory_priority_feature_is_queryable_but_not_enableable(self):
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
                demote_features.shaderDemoteToHelperInvocation = VK_TRUE;
                create_info.pNext = &demote_features;
                if (validate_device_feature_requests(&create_info) == VK_SUCCESS) {{
                    fprintf(stderr, "shaderDemoteToHelperInvocation=true was accepted\\n");
                    return 5;
                }}
                demote_features.shaderDemoteToHelperInvocation = VK_FALSE;
                if (validate_device_feature_requests(&create_info) != VK_SUCCESS) {{
                    fprintf(stderr, "shaderDemoteToHelperInvocation=false was rejected\\n");
                    return 6;
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

    def test_maintenance5_and_vulkan13_feature_pnext_are_false_only(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

            int main(void) {{
            #ifdef VK_KHR_MAINTENANCE_5_EXTENSION_NAME
                if (device_extension_advertised_name(VK_KHR_MAINTENANCE_5_EXTENSION_NAME)) {{
                    fprintf(stderr, "maintenance5 extension must not be advertised yet\\n");
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
                if (device_extension_advertised_name(VK_KHR_MAINTENANCE_5_EXTENSION_NAME)) {{
                    fprintf(stderr, "maintenance5 must remain unadvertised for false-only pNext support\\n");
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
                if (validate_memory_allocate_pnext(&priority_info) != VK_SUCCESS) {{
                    fprintf(stderr, "default memory priority pNext was rejected\\n");
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
                export_info.pNext = &priority_info;
                priority_info.sType = VK_STRUCTURE_TYPE_MEMORY_PRIORITY_ALLOCATE_INFO_EXT;
                priority_info.priority = 0.5f;
                priority_info.pNext = &capture;
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

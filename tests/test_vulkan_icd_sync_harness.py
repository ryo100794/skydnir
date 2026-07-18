import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ICD_SOURCE = ROOT / "docker-proot-setup" / "src" / "gpu" / "pdocker_vulkan_icd.c"


COMMAND_BUFFER_LIVE_REGISTRY_HELPER = r"""
static PdockerVkCommandPool g_sync_test_command_pool;

static PdockerVkCommandPool *sync_test_command_pool(void) {
    VkCommandPool handle = pdocker_vk_command_pool_to_handle(&g_sync_test_command_pool);
    PdockerVkCommandPool *pool = command_pool_handle_lookup(handle);
    if (pool) return pool;
    memset(&g_sync_test_command_pool, 0, sizeof(g_sync_test_command_pool));
    command_pool_register(&g_sync_test_command_pool);
    return &g_sync_test_command_pool;
}

static PdockerVkCommandBuffer *sync_test_command_buffer_alloc(void) {
    PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)calloc(1, sizeof(*cmd));
    if (!cmd) return NULL;
    if (!command_buffer_alloc_descriptor_states(cmd)) {
        free(cmd);
        return NULL;
    }
    set_loader_magic_value(cmd);
    command_buffer_register(sync_test_command_pool(), cmd);
    return cmd;
}
"""


class VulkanIcdQueryValidationSourceContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = ICD_SOURCE.read_text(encoding="utf-8")

    def section(self, start: str, end: str) -> str:
        source = self.source
        start_index = source.index(start)
        end_index = source.index(end, start_index)
        return source[start_index:end_index]

    @staticmethod
    def compact(text: str) -> str:
        return " ".join(text.split())

    def test_create_query_pool_rejects_pipeline_statistics_bits(self):
        body = self.section(
            "VKAPI_ATTR VkResult VKAPI_CALL vkCreateQueryPool",
            "VKAPI_ATTR void VKAPI_CALL vkDestroyQueryPool",
        )
        compact = self.compact(body)
        self.assertIn("pCreateInfo->pipelineStatistics != 0", compact)
        self.assertIn('"query-pipeline-statistics-unsupported"', body)
        self.assertIn("return VK_ERROR_FEATURE_NOT_PRESENT;", body)

    def test_query_recording_validates_command_type_against_pool_type(self):
        helper = self.compact(
            self.section(
                "static bool query_pool_type_supports_command",
                "static bool query_control_flags_supported",
            )
        )
        self.assertIn(
            "case PDOCKER_VK_COMMAND_QUERY_BEGIN: case PDOCKER_VK_COMMAND_QUERY_END: "
            "return pool_type == VK_QUERY_TYPE_OCCLUSION;",
            helper,
        )
        self.assertIn(
            "case PDOCKER_VK_COMMAND_QUERY_TIMESTAMP: "
            "return pool_type == VK_QUERY_TYPE_TIMESTAMP;",
            helper,
        )

        record = self.compact(
            self.section(
                "static void record_query_command",
                "static void record_copy_query_results_command",
            )
        )
        self.assertIn("!query_pool_type_supports_command(type, pool->type)", record)
        self.assertIn(
            'command_buffer_mark_recording_failed(cmd, "query-command-type-mismatch")',
            record,
        )

    def test_precise_occlusion_query_flag_is_rejected_unless_advertised(self):
        helper = self.section(
            "static bool query_control_flags_supported",
            "static void reset_query_range",
        )
        self.assertIn("VK_QUERY_CONTROL_PRECISE_BIT", helper)
        self.assertIn("fill_physical_device_features(&features);", helper)
        self.assertIn("features.occlusionQueryPrecise == VK_TRUE", helper)

        record = self.compact(
            self.section(
                "static void record_query_command",
                "static void record_copy_query_results_command",
            )
        )
        self.assertIn(
            "type == PDOCKER_VK_COMMAND_QUERY_BEGIN && "
            "!query_control_flags_supported((VkQueryControlFlags)stageMask)",
            record,
        )
        self.assertIn(
            'command_buffer_mark_recording_failed(cmd, "query-control-flags-unsupported")',
            record,
        )


@unittest.skipUnless(shutil.which("gcc"), "gcc is required for the ICD C sync harness")
class VulkanIcdSyncHarnessTest(unittest.TestCase):
    def compile_and_run(self, source: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "icd_sync_harness.c"
            exe = Path(tmpdir) / "icd_sync_harness"
            src.write_text(source, encoding="utf-8")
            try:
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
            except subprocess.CalledProcessError as exc:
                self.fail(
                    "gcc failed while compiling ICD sync harness\n"
                    + (exc.stdout or "")
                    + (exc.stderr or "")
                )
            return subprocess.run(
                [str(exe)],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

    def test_local_fence_reset_wait_and_submit_state_machine_executes_c_code(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include <unistd.h>
            #include "{ICD_SOURCE}"
            {COMMAND_BUFFER_LIVE_REGISTRY_HELPER}

            int main(void) {{
                unsetenv("PDOCKER_GPU_QUEUE_SOCKET");
                ensure_vulkan_dispatchable_object_ids();
                g_queue.instance_object_id = 1;
                g_queue.physical_device_object_id = 1;
                g_queue.device_object_id = 1;
                VkFence fence = VK_NULL_HANDLE;
                VkFenceCreateInfo create_info;
                memset(&create_info, 0, sizeof(create_info));
                create_info.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO;
                create_info.flags = VK_FENCE_CREATE_SIGNALED_BIT;
                if (vkCreateFence(VK_NULL_HANDLE, &create_info, NULL, &fence) != VK_SUCCESS || !fence) {{
                    fprintf(stderr, "create signaled fence failed\\n");
                    return 2;
                }}
                if (vkWaitForFences(VK_NULL_HANDLE, 1, &fence, VK_TRUE, 0) != VK_SUCCESS) {{
                    fprintf(stderr, "initial signaled fence did not wait successfully\\n");
                    return 3;
                }}
                if (vkResetFences(VK_NULL_HANDLE, 1, &fence) != VK_SUCCESS) {{
                    fprintf(stderr, "reset failed\\n");
                    return 4;
                }}
                if (vkGetFenceStatus(VK_NULL_HANDLE, fence) != VK_NOT_READY) {{
                    fprintf(stderr, "reset fence was not reported not-ready\\n");
                    return 5;
                }}
                if (vkQueueSubmit((VkQueue)&g_queue, 0, NULL, fence) != VK_SUCCESS) {{
                    fprintf(stderr, "empty queue submit failed\\n");
                    return 6;
                }}
                if (vkWaitForFences(VK_NULL_HANDLE, 1, &fence, VK_TRUE, 0) != VK_SUCCESS) {{
                    fprintf(stderr, "queue submit fence did not become waitable\\n");
                    return 7;
                }}
                vkDestroyFence(VK_NULL_HANDLE, fence, NULL);
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_compute_only_push_constants_do_not_mark_command_buffer_as_graphics(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include <stdlib.h>
            #include "{ICD_SOURCE}"
            {COMMAND_BUFFER_LIVE_REGISTRY_HELPER}

            int main(void) {{
                PdockerVkCommandBuffer *cmd = sync_test_command_buffer_alloc();
                if (!cmd) return 9;
                cmd->requested_feature_mask = PDOCKER_VK_FEATURE_SYNCHRONIZATION_2;
                cmd->enabled_extension_mask = PDOCKER_VK_DEVICE_EXT_KHR_SYNCHRONIZATION_2;
                uint32_t value = 0x12345678u;

                vkCmdPushConstants((VkCommandBuffer)cmd, VK_NULL_HANDLE,
                                   VK_SHADER_STAGE_COMPUTE_BIT, 0, sizeof(value), &value);
                if (cmd->push_constant_op_count != 1) {{
                    fprintf(stderr, "compute push constant was not captured\\n");
                    return 2;
                }}
                if (cmd->graphics_command_op_count != 0) {{
                    fprintf(stderr, "compute-only push constant incorrectly created graphics record count=%u\\n",
                            cmd->graphics_command_op_count);
                    return 3;
                }}

                cmd->graphics_pipeline = (PdockerVkPipeline *)0x1;
                vkCmdPushConstants((VkCommandBuffer)cmd, VK_NULL_HANDLE,
                                   VK_SHADER_STAGE_VERTEX_BIT, 0, sizeof(value), &value);
                if (cmd->push_constant_op_count != 2) {{
                    fprintf(stderr, "graphics push constant was not captured\\n");
                    return 4;
                }}
                if (cmd->graphics_command_op_count != 1 ||
                    cmd->graphics_command_ops[0].command_type != PDOCKER_GPU_GRAPHICS_V6_COMMAND_PUSH_CONSTANTS) {{
                    fprintf(stderr, "graphics push constant did not create exactly one graphics record count=%u\\n",
                            cmd->graphics_command_op_count);
                    return 5;
                }}
                free(cmd);
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)




    def test_pipeline_barrier2_preserves_compute_barrier_stage_access_values(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include <stdlib.h>
            #include "{ICD_SOURCE}"
            {COMMAND_BUFFER_LIVE_REGISTRY_HELPER}

            int main(void) {{
                PdockerVkCommandBuffer *cmd = sync_test_command_buffer_alloc();
                if (!cmd) return 9;
                cmd->requested_feature_mask = PDOCKER_VK_FEATURE_SYNCHRONIZATION_2;
                cmd->enabled_extension_mask = PDOCKER_VK_DEVICE_EXT_KHR_SYNCHRONIZATION_2;

                VkBufferCreateInfo buffer_info;
                memset(&buffer_info, 0, sizeof(buffer_info));
                buffer_info.sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO;
                buffer_info.size = 4096;
                buffer_info.usage = VK_BUFFER_USAGE_STORAGE_BUFFER_BIT | VK_BUFFER_USAGE_TRANSFER_DST_BIT;
                buffer_info.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
                VkBuffer buffer = VK_NULL_HANDLE;
                if (vkCreateBuffer(VK_NULL_HANDLE, &buffer_info, NULL, &buffer) != VK_SUCCESS || !buffer) {{
                    fprintf(stderr, "test buffer creation failed\\n");
                    return 2;
                }}

                VkMemoryBarrier2 memory_barrier;
                memset(&memory_barrier, 0, sizeof(memory_barrier));
                memory_barrier.sType = VK_STRUCTURE_TYPE_MEMORY_BARRIER_2;
                memory_barrier.srcStageMask = VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT;
                memory_barrier.srcAccessMask = VK_ACCESS_2_SHADER_WRITE_BIT;
                memory_barrier.dstStageMask = VK_PIPELINE_STAGE_2_TRANSFER_BIT;
                memory_barrier.dstAccessMask = VK_ACCESS_2_TRANSFER_READ_BIT;

                VkBufferMemoryBarrier2 buffer_barrier;
                memset(&buffer_barrier, 0, sizeof(buffer_barrier));
                buffer_barrier.sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER_2;
                buffer_barrier.srcStageMask = VK_PIPELINE_STAGE_2_TRANSFER_BIT;
                buffer_barrier.srcAccessMask = VK_ACCESS_2_TRANSFER_WRITE_BIT;
                buffer_barrier.dstStageMask = VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT;
                buffer_barrier.dstAccessMask = VK_ACCESS_2_SHADER_READ_BIT;
                buffer_barrier.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
                buffer_barrier.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
                buffer_barrier.buffer = buffer;
                buffer_barrier.offset = 128;
                buffer_barrier.size = 512;

                VkDependencyInfo dependency;
                memset(&dependency, 0, sizeof(dependency));
                dependency.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO;
                dependency.dependencyFlags = VK_DEPENDENCY_BY_REGION_BIT;
                dependency.memoryBarrierCount = 1;
                dependency.pMemoryBarriers = &memory_barrier;
                dependency.bufferMemoryBarrierCount = 1;
                dependency.pBufferMemoryBarriers = &buffer_barrier;

                vkCmdPipelineBarrier2((VkCommandBuffer)cmd, &dependency);
                if (cmd->recording_failed) {{
                    fprintf(stderr, "pipeline barrier2 unexpectedly failed: %s\\n",
                            cmd->recording_failure_reason ? cmd->recording_failure_reason : "<null>");
                    return 3;
                }}
                if (cmd->memory_barrier_op_count != 1 || cmd->buffer_barrier_op_count != 1 ||
                    cmd->image_barrier_op_count != 0 || cmd->command_op_count != 1 ||
                    cmd->graphics_command_op_count != 1) {{
                    fprintf(stderr, "unexpected barrier op counts mem=%u buf=%u img=%u cmd=%u gfx=%u\\n",
                            cmd->memory_barrier_op_count, cmd->buffer_barrier_op_count,
                            cmd->image_barrier_op_count, cmd->command_op_count,
                            cmd->graphics_command_op_count);
                    return 4;
                }}

                const PdockerVkMemoryBarrierOp *mem = &cmd->memory_barrier_ops[0];
                if (mem->src_stage_mask != memory_barrier.srcStageMask ||
                    mem->src_access_mask != memory_barrier.srcAccessMask ||
                    mem->dst_stage_mask != memory_barrier.dstStageMask ||
                    mem->dst_access_mask != memory_barrier.dstAccessMask) {{
                    fprintf(stderr, "memory barrier masks were not preserved\\n");
                    return 5;
                }}

                const PdockerVkBufferBarrierOp *buf = &cmd->buffer_barrier_ops[0];
                if (buf->buffer != pdocker_vk_buffer_from_handle(buffer) ||
                    buf->offset != buffer_barrier.offset ||
                    buf->size != buffer_barrier.size ||
                    buf->src_stage_mask != buffer_barrier.srcStageMask ||
                    buf->src_access_mask != buffer_barrier.srcAccessMask ||
                    buf->dst_stage_mask != buffer_barrier.dstStageMask ||
                    buf->dst_access_mask != buffer_barrier.dstAccessMask ||
                    buf->src_queue_family_index != buffer_barrier.srcQueueFamilyIndex ||
                    buf->dst_queue_family_index != buffer_barrier.dstQueueFamilyIndex) {{
                    fprintf(stderr, "buffer barrier payload was not preserved\\n");
                    return 6;
                }}

                if (cmd->command_ops[0].type != PDOCKER_VK_COMMAND_BARRIER ||
                    cmd->command_ops[0].memory_barrier_op_first != 0 ||
                    cmd->command_ops[0].memory_barrier_op_count != 1 ||
                    cmd->command_ops[0].buffer_barrier_op_first != 0 ||
                    cmd->command_ops[0].buffer_barrier_op_count != 1 ||
                    cmd->command_ops[0].dependency_flags != VK_DEPENDENCY_BY_REGION_BIT) {{
                    fprintf(stderr, "command barrier range metadata was not preserved\\n");
                    return 7;
                }}
                if (cmd->graphics_command_ops[0].command_type != PDOCKER_GPU_GRAPHICS_V6_COMMAND_BARRIER ||
                    cmd->graphics_command_ops[0].memory_barrier_op_count != 1 ||
                    cmd->graphics_command_ops[0].buffer_barrier_op_count != 1 ||
                    cmd->graphics_command_ops[0].flags != VK_DEPENDENCY_BY_REGION_BIT) {{
                    fprintf(stderr, "graphics barrier range metadata was not preserved\\n");
                    return 8;
                }}

                vkDestroyBuffer(VK_NULL_HANDLE, buffer, NULL);
                free(cmd);
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


    def test_set_event2_records_precise_unsupported_dependency_reasons(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include <stdlib.h>
            #include "{ICD_SOURCE}"
            {COMMAND_BUFFER_LIVE_REGISTRY_HELPER}

            typedef struct DummyPnext {{
                VkStructureType sType;
                const void *pNext;
            }} DummyPnext;

            static int expect_reason(PdockerVkCommandBuffer *cmd, const char *reason) {{
                if (!cmd->recording_failed) {{
                    fprintf(stderr, "command buffer did not record failure\\n");
                    return 0;
                }}
                if (!cmd->recording_failure_reason || strcmp(cmd->recording_failure_reason, reason) != 0) {{
                    fprintf(stderr, "unexpected reason got=%s want=%s\\n",
                            cmd->recording_failure_reason ? cmd->recording_failure_reason : "<null>", reason);
                    return 0;
                }}
                return 1;
            }}

            int main(void) {{
                unsetenv("PDOCKER_GPU_QUEUE_SOCKET");
                PdockerVkCommandBuffer *cmd = sync_test_command_buffer_alloc();
                if (!cmd) return 9;
                cmd->requested_feature_mask = PDOCKER_VK_FEATURE_SYNCHRONIZATION_2;
                cmd->enabled_extension_mask = PDOCKER_VK_DEVICE_EXT_KHR_SYNCHRONIZATION_2;

                DummyPnext unsupported;
                memset(&unsupported, 0, sizeof(unsupported));
                unsupported.sType = (VkStructureType)1000060013;

                VkDependencyInfo dependency;
                memset(&dependency, 0, sizeof(dependency));
                dependency.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO;
                dependency.pNext = &unsupported;
                vkCmdSetEvent2((VkCommandBuffer)cmd, VK_NULL_HANDLE, &dependency);
                if (!expect_reason(cmd, "event-set2-dependency-info-unsupported")) return 2;
                if (cmd->command_op_count != 0 || cmd->graphics_command_op_count != 0) {{
                    fprintf(stderr, "unsupported set-event2 still recorded commands\\n");
                    return 3;
                }}

                memset(cmd, 0, sizeof(*cmd));
                cmd->requested_feature_mask = PDOCKER_VK_FEATURE_SYNCHRONIZATION_2;
                cmd->enabled_extension_mask = PDOCKER_VK_DEVICE_EXT_KHR_SYNCHRONIZATION_2;
                memset(&dependency, 0, sizeof(dependency));
                dependency.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO;
                vkCmdSetEvent2((VkCommandBuffer)cmd, VK_NULL_HANDLE, &dependency);
                if (!expect_reason(cmd, "event-set2-null-event")) return 4;
                if (cmd->command_op_count != 0 || cmd->graphics_command_op_count != 0 ||
                    cmd->memory_barrier_op_count != 0) {{
                    fprintf(stderr, "null-event set-event2 still recorded commands\\n");
                    return 5;
                }}

                memset(cmd, 0, sizeof(*cmd));
                cmd->requested_feature_mask = PDOCKER_VK_FEATURE_SYNCHRONIZATION_2;
                cmd->enabled_extension_mask = PDOCKER_VK_DEVICE_EXT_KHR_SYNCHRONIZATION_2;
                VkEventCreateInfo create_info;
                memset(&create_info, 0, sizeof(create_info));
                create_info.sType = VK_STRUCTURE_TYPE_EVENT_CREATE_INFO;
                VkEvent event = VK_NULL_HANDLE;
                if (vkCreateEvent(VK_NULL_HANDLE, &create_info, NULL, &event) != VK_SUCCESS || !event) return 6;

                VkMemoryBarrier2 memory_barrier;
                memset(&memory_barrier, 0, sizeof(memory_barrier));
                memory_barrier.sType = VK_STRUCTURE_TYPE_MEMORY_BARRIER_2;
                memory_barrier.srcStageMask = VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT;
                memory_barrier.dstStageMask = VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT;
                memset(&dependency, 0, sizeof(dependency));
                dependency.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO;
                dependency.dependencyFlags = VK_DEPENDENCY_BY_REGION_BIT;
                dependency.memoryBarrierCount = 1;
                dependency.pMemoryBarriers = &memory_barrier;
                vkCmdSetEvent2((VkCommandBuffer)cmd, event, &dependency);
                if (cmd->recording_failed) {{
                    fprintf(stderr, "barrier-payload set-event2 unexpectedly failed: %s\\n",
                            cmd->recording_failure_reason ? cmd->recording_failure_reason : "<null>");
                    return 7;
                }}
                if (cmd->command_op_count != 1 || cmd->graphics_command_op_count != 1 ||
                    cmd->memory_barrier_op_count != 1 ||
                    cmd->command_ops[0].type != PDOCKER_VK_COMMAND_EVENT ||
                    cmd->graphics_command_ops[0].command_type != PDOCKER_GPU_GRAPHICS_V6_COMMAND_SET_EVENT ||
                    cmd->graphics_command_ops[0].flags != VK_DEPENDENCY_BY_REGION_BIT ||
                    cmd->graphics_command_ops[0].memory_barrier_op_first != 0 ||
                    cmd->graphics_command_ops[0].memory_barrier_op_count != 1) {{
                    fprintf(stderr, "barrier-payload set-event2 was not captured ops=%u graphics=%u mem=%u flags=%u first=%u count=%u\\n",
                            cmd->command_op_count, cmd->graphics_command_op_count,
                            cmd->memory_barrier_op_count,
                            cmd->graphics_command_op_count ? cmd->graphics_command_ops[0].flags : 0,
                            cmd->graphics_command_op_count ? cmd->graphics_command_ops[0].memory_barrier_op_first : 0,
                            cmd->graphics_command_op_count ? cmd->graphics_command_ops[0].memory_barrier_op_count : 0);
                    return 8;
                }}
                if (!command_buffer_needs_graphics_submit_sync_frame(cmd)) {{
                    fprintf(stderr, "barrier-payload set-event2 did not require executor submit frame\\n");
                    return 81;
                }}

                memset(cmd, 0, sizeof(*cmd));
                cmd->requested_feature_mask = PDOCKER_VK_FEATURE_SYNCHRONIZATION_2;
                cmd->enabled_extension_mask = PDOCKER_VK_DEVICE_EXT_KHR_SYNCHRONIZATION_2;
                memset(&dependency, 0, sizeof(dependency));
                dependency.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO;
                dependency.dependencyFlags = VK_DEPENDENCY_BY_REGION_BIT;
                vkCmdSetEvent2((VkCommandBuffer)cmd, event, &dependency);
                if (cmd->recording_failed) {{
                    fprintf(stderr, "by-region set-event2 unexpectedly failed: %s\\n",
                            cmd->recording_failure_reason ? cmd->recording_failure_reason : "<null>");
                    return 9;
                }}
                if (cmd->command_op_count != 1 || cmd->graphics_command_op_count != 1 ||
                    cmd->command_ops[0].type != PDOCKER_VK_COMMAND_EVENT ||
                    cmd->graphics_command_ops[0].command_type != PDOCKER_GPU_GRAPHICS_V6_COMMAND_SET_EVENT ||
                    cmd->graphics_command_ops[0].flags != VK_DEPENDENCY_BY_REGION_BIT) {{
                    fprintf(stderr, "by-region set-event2 was not captured as event flags ops=%u graphics=%u flags=%u\\n",
                            cmd->command_op_count, cmd->graphics_command_op_count,
                            cmd->graphics_command_op_count ? cmd->graphics_command_ops[0].flags : 0);
                    return 10;
                }}
                if (command_buffer_needs_graphics_submit_sync_frame(cmd)) {{
                    fprintf(stderr, "by-region-only non-tracked set-event2 should not require executor submit frame\\n");
                    return 101;
                }}

                memset(cmd, 0, sizeof(*cmd));
                cmd->requested_feature_mask = PDOCKER_VK_FEATURE_SYNCHRONIZATION_2;
                cmd->enabled_extension_mask = PDOCKER_VK_DEVICE_EXT_KHR_SYNCHRONIZATION_2;
                VkEvent events[1] = {{ event }};
                memset(&dependency, 0, sizeof(dependency));
                dependency.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO;
                dependency.dependencyFlags = VK_DEPENDENCY_BY_REGION_BIT;
                dependency.memoryBarrierCount = 1;
                dependency.pMemoryBarriers = &memory_barrier;
                vkCmdWaitEvents2((VkCommandBuffer)cmd, 1, events, &dependency);
                if (cmd->recording_failed) {{
                    fprintf(stderr, "barrier-payload wait-events2 unexpectedly failed: %s\\n",
                            cmd->recording_failure_reason ? cmd->recording_failure_reason : "<null>");
                    return 11;
                }}
                if (cmd->command_op_count != 1 || cmd->graphics_command_op_count != 1 ||
                    cmd->memory_barrier_op_count != 1 ||
                    cmd->command_ops[0].type != PDOCKER_VK_COMMAND_EVENT_WAIT ||
                    cmd->graphics_command_ops[0].command_type != PDOCKER_GPU_GRAPHICS_V6_COMMAND_WAIT_EVENT ||
                    cmd->graphics_command_ops[0].flags != VK_DEPENDENCY_BY_REGION_BIT ||
                    cmd->graphics_command_ops[0].memory_barrier_op_first != 0 ||
                    cmd->graphics_command_ops[0].memory_barrier_op_count != 1) {{
                    fprintf(stderr, "barrier-payload wait-events2 was not captured ops=%u graphics=%u mem=%u flags=%u first=%u count=%u\\n",
                            cmd->command_op_count, cmd->graphics_command_op_count,
                            cmd->memory_barrier_op_count,
                            cmd->graphics_command_op_count ? cmd->graphics_command_ops[0].flags : 0,
                            cmd->graphics_command_op_count ? cmd->graphics_command_ops[0].memory_barrier_op_first : 0,
                            cmd->graphics_command_op_count ? cmd->graphics_command_ops[0].memory_barrier_op_count : 0);
                    return 12;
                }}
                if (!command_buffer_needs_graphics_submit_sync_frame(cmd)) {{
                    fprintf(stderr, "barrier-payload wait-events2 did not require executor submit frame\\n");
                    return 121;
                }}

                memset(cmd, 0, sizeof(*cmd));
                cmd->requested_feature_mask = PDOCKER_VK_FEATURE_SYNCHRONIZATION_2;
                cmd->enabled_extension_mask = PDOCKER_VK_DEVICE_EXT_KHR_SYNCHRONIZATION_2;
                VkMemoryBarrier legacy_memory_barrier;
                memset(&legacy_memory_barrier, 0, sizeof(legacy_memory_barrier));
                legacy_memory_barrier.sType = VK_STRUCTURE_TYPE_MEMORY_BARRIER;
                legacy_memory_barrier.srcAccessMask = VK_ACCESS_SHADER_WRITE_BIT;
                legacy_memory_barrier.dstAccessMask = VK_ACCESS_SHADER_READ_BIT;
                vkCmdWaitEvents((VkCommandBuffer)cmd, 1, events,
                                VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
                                VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
                                1, &legacy_memory_barrier, 0, NULL, 0, NULL);
                if (cmd->recording_failed) {{
                    fprintf(stderr, "legacy barrier-payload wait-events unexpectedly failed: %s\\n",
                            cmd->recording_failure_reason ? cmd->recording_failure_reason : "<null>");
                    return 13;
                }}
                if (cmd->command_op_count != 1 || cmd->graphics_command_op_count != 1 ||
                    cmd->memory_barrier_op_count != 1 ||
                    cmd->command_ops[0].type != PDOCKER_VK_COMMAND_EVENT_WAIT ||
                    cmd->graphics_command_ops[0].command_type != PDOCKER_GPU_GRAPHICS_V6_COMMAND_WAIT_EVENT ||
                    cmd->graphics_command_ops[0].memory_barrier_op_first != 0 ||
                    cmd->graphics_command_ops[0].memory_barrier_op_count != 1 ||
                    !command_buffer_needs_graphics_submit_sync_frame(cmd)) {{
                    fprintf(stderr, "legacy barrier-payload wait-events did not record replayable submit frame ops=%u graphics=%u mem=%u need=%d\\n",
                            cmd->command_op_count, cmd->graphics_command_op_count,
                            cmd->memory_barrier_op_count,
                            command_buffer_needs_graphics_submit_sync_frame(cmd) ? 1 : 0);
                    return 14;
                }}

                memset(cmd, 0, sizeof(*cmd));
                cmd->requested_feature_mask = PDOCKER_VK_FEATURE_SYNCHRONIZATION_2;
                cmd->enabled_extension_mask = PDOCKER_VK_DEVICE_EXT_KHR_SYNCHRONIZATION_2;
                VkEvent two_events[2] = {{ event, event }};
                vkCmdWaitEvents((VkCommandBuffer)cmd, 2, two_events,
                                VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
                                VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
                                1, &legacy_memory_barrier, 0, NULL, 0, NULL);
                if (cmd->recording_failed) {{
                    fprintf(stderr, "multi-event legacy barrier wait-events unexpectedly failed: %s\\n",
                            cmd->recording_failure_reason ? cmd->recording_failure_reason : "<null>");
                    return 15;
                }}
                if (cmd->command_op_count != 1 || cmd->graphics_command_op_count != 1 ||
                    cmd->memory_barrier_op_count != 1 ||
                    cmd->command_ops[0].type != PDOCKER_VK_COMMAND_EVENT_WAIT ||
                    cmd->command_ops[0].event_wait_ref_count != 2 ||
                    cmd->event_wait_ref_count != 2 ||
                    cmd->graphics_command_ops[0].command_type != PDOCKER_GPU_GRAPHICS_V6_COMMAND_WAIT_EVENT ||
                    cmd->graphics_command_ops[0].memory_barrier_op_first != 0 ||
                    cmd->graphics_command_ops[0].memory_barrier_op_count != 1 ||
                    !command_buffer_needs_graphics_submit_sync_frame(cmd)) {{
                    fprintf(stderr, "multi-event legacy barrier wait-events did not record refs ops=%u graphics=%u mem=%u refs=%u op_refs=%u need=%d\\n",
                            cmd->command_op_count, cmd->graphics_command_op_count,
                            cmd->memory_barrier_op_count, cmd->event_wait_ref_count,
                            cmd->command_op_count ? cmd->command_ops[0].event_wait_ref_count : 0,
                            command_buffer_needs_graphics_submit_sync_frame(cmd) ? 1 : 0);
                    return 16;
                }}
                vkDestroyEvent(VK_NULL_HANDLE, event, NULL);
                free(cmd);
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_descriptor_image_layout_mismatch_fails_before_transport(self):
        source = textwrap.dedent(
            f"""
            #include <errno.h>
            #include <fcntl.h>
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include <stdlib.h>
            #include <unistd.h>
            #include "{ICD_SOURCE}"
            {COMMAND_BUFFER_LIVE_REGISTRY_HELPER}

            static void init_color_image(PdockerVkImage *image, PdockerVkMemory *memory,
                                         VkImageLayout layout) {{
                memset(image, 0, sizeof(*image));
                memset(memory, 0, sizeof(*memory));
                memory->fd = -1;
                memory->size = 4096;
                image->object_id = 0x1001;
                image->format = VK_FORMAT_R8G8B8A8_UNORM;
                image->extent.width = 16;
                image->extent.height = 16;
                image->extent.depth = 1;
                image->mip_levels = 4;
                image->array_layers = 2;
                image->samples = VK_SAMPLE_COUNT_1_BIT;
                image->current_layout = layout;
                image->memory = memory;
            }}

            static void init_color_view(PdockerVkImageView *view, PdockerVkImage *image,
                                        uint32_t base_level, uint32_t level_count,
                                        uint32_t base_layer, uint32_t layer_count) {{
                memset(view, 0, sizeof(*view));
                view->object_id = 0x2001;
                view->image = image;
                view->format = image->format;
                view->subresource_range.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
                view->subresource_range.baseMipLevel = base_level;
                view->subresource_range.levelCount = level_count;
                view->subresource_range.baseArrayLayer = base_layer;
                view->subresource_range.layerCount = layer_count;
            }}

            static void init_depth_stencil_image(PdockerVkImage *image, PdockerVkMemory *memory,
                                                 VkImageLayout layout) {{
                memset(image, 0, sizeof(*image));
                memset(memory, 0, sizeof(*memory));
                memory->fd = -1;
                memory->size = 8192;
                image->object_id = 0x3001;
                image->format = VK_FORMAT_D24_UNORM_S8_UINT;
                image->extent.width = 16;
                image->extent.height = 16;
                image->extent.depth = 1;
                image->mip_levels = 1;
                image->array_layers = 1;
                image->samples = VK_SAMPLE_COUNT_1_BIT;
                image->current_layout = layout;
                image->memory = memory;
            }}

            static void init_depth_stencil_view(PdockerVkImageView *view, PdockerVkImage *image) {{
                memset(view, 0, sizeof(*view));
                view->object_id = 0x4001;
                view->image = image;
                view->format = image->format;
                view->subresource_range.aspectMask = VK_IMAGE_ASPECT_DEPTH_BIT | VK_IMAGE_ASPECT_STENCIL_BIT;
                view->subresource_range.baseMipLevel = 0;
                view->subresource_range.levelCount = 1;
                view->subresource_range.baseArrayLayer = 0;
                view->subresource_range.layerCount = 1;
            }}

            static int expect_layout(const char *label, int got, int want) {{
                if ((got != 0) != (want != 0)) {{
                    fprintf(stderr, "%s got=%d want=%d\\n", label, got, want);
                    return 0;
                }}
                return 1;
            }}

            static int send_one_image_descriptor(PdockerVkImageView *view, VkDescriptorType descriptor_type, VkImageLayout layout) {{
                PdockerVkShaderModule shader;
                PdockerVkPipeline pipeline;
                PdockerVkDescriptorSetLayout set_layout;
                PdockerVkDescriptorSet set;
                PdockerVkDescriptorBinding binding_storage[1];
                PdockerVkDescriptorBinding *storage_rows[1];
                uint32_t storage_counts[1];
                uint32_t binding_numbers[1];
                bool binding_present[1];
                VkDescriptorType binding_types[1];
                uint32_t binding_counts[1];
                PdockerVkDescriptorSet snapshots[1];
                bool snapshot_used[1];
                PdockerVkDispatchOp op;

                memset(&shader, 0, sizeof(shader));
                memset(&pipeline, 0, sizeof(pipeline));
                memset(&set_layout, 0, sizeof(set_layout));
                memset(&set, 0, sizeof(set));
                memset(binding_storage, 0, sizeof(binding_storage));
                memset(storage_rows, 0, sizeof(storage_rows));
                memset(storage_counts, 0, sizeof(storage_counts));
                memset(binding_numbers, 0, sizeof(binding_numbers));
                memset(binding_present, 0, sizeof(binding_present));
                memset(binding_types, 0, sizeof(binding_types));
                memset(binding_counts, 0, sizeof(binding_counts));
                memset(snapshots, 0, sizeof(snapshots));
                memset(snapshot_used, 0, sizeof(snapshot_used));
                memset(&op, 0, sizeof(op));

                shader.code_fd = open("/dev/null", O_RDONLY);
                if (shader.code_fd < 0) return -errno;
                shader.code_size = 4;
                pipeline.shader = &shader;
                pipeline.local_size_x = 1;

                binding_numbers[0] = 7;
                binding_present[0] = true;
                binding_types[0] = descriptor_type;
                binding_counts[0] = 1;
                set_layout.storage_binding_count = 1;
                set_layout.storage_binding_capacity = 1;
                set_layout.storage_binding_numbers = binding_numbers;
                set_layout.storage_binding_present = binding_present;
                set_layout.storage_binding_types = binding_types;
                set_layout.storage_binding_counts = binding_counts;

                binding_storage[0].descriptor_type = descriptor_type;
                binding_storage[0].image_view = view;
                binding_storage[0].image_layout = layout;
                storage_rows[0] = binding_storage;
                storage_counts[0] = 1;
                set.layout = &set_layout;
                set.storage_binding_capacity = 1;
                set.storage_buffers = storage_rows;
                set.storage_buffer_counts = storage_counts;
                snapshots[0] = set;
                snapshot_used[0] = true;

                op.pipeline = &pipeline;
                op.set_snapshots = snapshots;
                op.set_snapshot_used = snapshot_used;
                op.set_capacity = 1;
                op.dispatch_x = 1;
                op.dispatch_y = 1;
                op.dispatch_z = 1;

                int rc = send_generic_vulkan_dispatch_op(&op, NULL, NULL, NULL, 0);
                close(shader.code_fd);
                return rc;
            }}

            int main(void) {{
                setenv("PDOCKER_GPU_QUEUE_SOCKET", "/tmp/pdocker-icd-sync-harness-no-such-sock", 1);
                PdockerVkMemory memory;
                PdockerVkImage image;
                PdockerVkImageView view;

                init_color_image(&image, &memory, VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL);
                init_color_view(&view, &image, 0, 1, 0, 1);
                if (!expect_layout("sampled readonly exact layout",
                        descriptor_image_layout_matches_tracked_state(
                            &view, VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE,
                            VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL), 1)) return 2;
                if (!expect_layout("sampled mismatched tracked layout",
                        descriptor_image_layout_matches_tracked_state(
                            &view, VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE,
                            VK_IMAGE_LAYOUT_GENERAL), 0)) return 3;

                init_color_image(&image, &memory, VK_IMAGE_LAYOUT_GENERAL);
                init_color_view(&view, &image, 0, 1, 0, 1);
                if (!expect_layout("storage general layout",
                        descriptor_image_layout_matches_tracked_state(
                            &view, VK_DESCRIPTOR_TYPE_STORAGE_IMAGE,
                            VK_IMAGE_LAYOUT_GENERAL), 1)) return 4;
                if (!expect_layout("storage readonly descriptor layout rejected",
                        descriptor_image_layout_matches_tracked_state(
                            &view, VK_DESCRIPTOR_TYPE_STORAGE_IMAGE,
                            VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL), 0)) return 5;

                init_color_image(&image, &memory, VK_IMAGE_LAYOUT_GENERAL);
                PdockerVkImageLayoutRange layout_ranges[2];
                memset(layout_ranges, 0, sizeof(layout_ranges));
                image.layout_mixed = true;
                image.layout_ranges = layout_ranges;
                image.layout_range_capacity = 2;
                image.layout_range_count = 1;
                image.layout_ranges[0].layout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
                image.layout_ranges[0].range.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
                image.layout_ranges[0].range.baseMipLevel = 1;
                image.layout_ranges[0].range.levelCount = 2;
                image.layout_ranges[0].range.baseArrayLayer = 0;
                image.layout_ranges[0].range.layerCount = 2;
                init_color_view(&view, &image, 1, 2, 0, 2);
                if (!expect_layout("mixed explicit range fully covers view",
                        descriptor_image_layout_matches_tracked_state(
                            &view, VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE,
                            VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL), 1)) return 6;

                image.layout_range_count = 2;
                image.layout_ranges[1] = image.layout_ranges[0];
                image.layout_ranges[1].layout = VK_IMAGE_LAYOUT_GENERAL;
                image.layout_ranges[1].range.baseMipLevel = 2;
                image.layout_ranges[1].range.levelCount = 1;
                if (!expect_layout("mixed conflicting overlapping explicit range rejected",
                        descriptor_image_layout_matches_tracked_state(
                            &view, VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE,
                            VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL), 0)) return 7;

                image.layout_range_count = 1;
                image.layout_ranges[0].layout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
                image.layout_ranges[0].range.baseMipLevel = 1;
                image.layout_ranges[0].range.levelCount = 1;
                image.layout_ranges[0].range.baseArrayLayer = 0;
                image.layout_ranges[0].range.layerCount = 2;
                if (!expect_layout("mixed missing explicit coverage rejected",
                        descriptor_image_layout_matches_tracked_state(
                            &view, VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE,
                            VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL), 0)) return 8;

                init_color_image(&image, &memory, VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL);
                init_color_view(&view, &image, 0, 1, 0, 1);
                memory.fd = open("/dev/null", O_RDONLY);
                if (memory.fd < 0) return 9;
                int mismatch_rc = send_one_image_descriptor(&view, VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE, VK_IMAGE_LAYOUT_GENERAL);
                close(memory.fd);
                memory.fd = -1;
                if (mismatch_rc != -EOPNOTSUPP) {{
                    fprintf(stderr, "mismatched descriptor layout rc=%d, want -EOPNOTSUPP before transport\\n",
                            mismatch_rc);
                    return 10;
                }}

                memory.fd = open("/dev/null", O_RDONLY);
                if (memory.fd < 0) return 11;
                int matching_rc = send_one_image_descriptor(
                    &view, VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE, VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL);
                close(memory.fd);
                if (matching_rc == -EOPNOTSUPP) {{
                    fprintf(stderr, "matching sampled-image descriptor was rejected as layout mismatch\\n");
                    return 12;
                }}

                init_depth_stencil_image(&image, &memory, VK_IMAGE_LAYOUT_DEPTH_STENCIL_READ_ONLY_OPTIMAL);
                init_depth_stencil_view(&view, &image);
                if (!expect_layout("dual-aspect sampled depth/stencil readonly layout",
                        descriptor_image_layout_matches_tracked_state(
                            &view, VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE,
                            VK_IMAGE_LAYOUT_DEPTH_STENCIL_READ_ONLY_OPTIMAL), 1)) return 13;
                if (!expect_layout("dual-aspect sampled depth-only layout rejected",
                        descriptor_image_layout_matches_tracked_state(
                            &view, VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE,
                            VK_IMAGE_LAYOUT_DEPTH_READ_ONLY_OPTIMAL), 0)) return 14;
                if (!expect_layout("dual-aspect sampled descriptor transport supported",
                        descriptor_image_aspect_transport_supported(
                            &view, VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE), 1)) return 15;

                init_depth_stencil_image(&image, &memory, VK_IMAGE_LAYOUT_GENERAL);
                init_depth_stencil_view(&view, &image);
                if (!expect_layout("dual-aspect storage layout alone matches",
                        descriptor_image_layout_matches_tracked_state(
                            &view, VK_DESCRIPTOR_TYPE_STORAGE_IMAGE,
                            VK_IMAGE_LAYOUT_GENERAL), 1)) return 16;
                if (!expect_layout("dual-aspect storage descriptor transport rejected",
                        descriptor_image_aspect_transport_supported(
                            &view, VK_DESCRIPTOR_TYPE_STORAGE_IMAGE), 0)) return 17;
                memory.fd = open("/dev/null", O_RDONLY);
                if (memory.fd < 0) return 18;
                int storage_depth_stencil_rc = send_one_image_descriptor(
                    &view, VK_DESCRIPTOR_TYPE_STORAGE_IMAGE, VK_IMAGE_LAYOUT_GENERAL);
                close(memory.fd);
                memory.fd = -1;
                if (storage_depth_stencil_rc != -EOPNOTSUPP) {{
                    fprintf(stderr, "storage depth/stencil descriptor rc=%d, want -EOPNOTSUPP before transport\\n",
                            storage_depth_stencil_rc);
                    return 19;
                }}

                init_depth_stencil_image(&image, &memory, VK_IMAGE_LAYOUT_DEPTH_STENCIL_READ_ONLY_OPTIMAL);
                init_depth_stencil_view(&view, &image);
                memory.fd = open("/dev/null", O_RDONLY);
                if (memory.fd < 0) return 20;
                int sampled_depth_stencil_rc = send_one_image_descriptor(
                    &view, VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE,
                    VK_IMAGE_LAYOUT_DEPTH_STENCIL_READ_ONLY_OPTIMAL);
                close(memory.fd);
                if (sampled_depth_stencil_rc == -EOPNOTSUPP) {{
                    fprintf(stderr, "sampled depth/stencil descriptor was rejected before transport\\n");
                    return 21;
                }}
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_dispatch_commands_fail_closed_when_recorded_inside_rendering_scopes(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include <stdlib.h>
            #include "{ICD_SOURCE}"
            {COMMAND_BUFFER_LIVE_REGISTRY_HELPER}

            static int expect_dispatch_scope_failure(PdockerVkCommandBuffer *cmd, const char *expected_reason) {{
                if (!cmd->recording_failed) {{
                    fprintf(stderr, "dispatch scope did not fail recording\\n");
                    return 0;
                }}
                if (!cmd->recording_failure_reason || strcmp(cmd->recording_failure_reason, expected_reason) != 0) {{
                    fprintf(stderr, "unexpected dispatch scope reason got=%s want=%s\\n",
                            cmd->recording_failure_reason ? cmd->recording_failure_reason : "<null>",
                            expected_reason);
                    return 0;
                }}
                if (!cmd->graphics_unsupported || cmd->has_dispatch ||
                    cmd->dispatch_op_count != 0 || cmd->command_op_count != 0) {{
                    fprintf(stderr, "dispatch scope failure still recorded state unsupported=%d has=%d dispatch=%u ops=%u\\n",
                            cmd->graphics_unsupported ? 1 : 0,
                            cmd->has_dispatch ? 1 : 0,
                            cmd->dispatch_op_count,
                            cmd->command_op_count);
                    return 0;
                }}
                if (vkEndCommandBuffer((VkCommandBuffer)cmd) != VK_ERROR_FEATURE_NOT_PRESENT) {{
                    fprintf(stderr, "failed dispatch command buffer did not fail close at end\\n");
                    return 0;
                }}
                return 1;
            }}

            int main(void) {{
                PdockerVkCommandBuffer *cmd = sync_test_command_buffer_alloc();
                if (!cmd) return 9;
                cmd->requested_feature_mask = PDOCKER_VK_FEATURE_SYNCHRONIZATION_2;
                cmd->enabled_extension_mask = PDOCKER_VK_DEVICE_EXT_KHR_SYNCHRONIZATION_2;

                cmd->dynamic_rendering_active = true;
                vkCmdDispatch((VkCommandBuffer)cmd, 1, 1, 1);
                if (!expect_dispatch_scope_failure(cmd, "dispatch-inside-dynamic-rendering-unsupported")) return 2;

                memset(cmd, 0, sizeof(*cmd));
                cmd->requested_feature_mask = PDOCKER_VK_FEATURE_SYNCHRONIZATION_2;
                cmd->enabled_extension_mask = PDOCKER_VK_DEVICE_EXT_KHR_SYNCHRONIZATION_2;
                cmd->render_pass_active = true;
                vkCmdDispatchBase((VkCommandBuffer)cmd, 0, 0, 0, 1, 1, 1);
                if (!expect_dispatch_scope_failure(cmd, "dispatch-inside-legacy-render-pass-unsupported")) return 3;

                memset(cmd, 0, sizeof(*cmd));
                cmd->requested_feature_mask = PDOCKER_VK_FEATURE_SYNCHRONIZATION_2;
                cmd->enabled_extension_mask = PDOCKER_VK_DEVICE_EXT_KHR_SYNCHRONIZATION_2;
                cmd->dynamic_rendering_active = true;
                cmd->active_render_pass = (PdockerVkRenderPass *)0x1;
                vkCmdDispatch((VkCommandBuffer)cmd, 1, 1, 1);
                if (!expect_dispatch_scope_failure(cmd, "dispatch-inside-legacy-render-pass-unsupported")) return 4;

                memset(cmd, 0, sizeof(*cmd));
                cmd->requested_feature_mask = PDOCKER_VK_FEATURE_SYNCHRONIZATION_2;
                cmd->enabled_extension_mask = PDOCKER_VK_DEVICE_EXT_KHR_SYNCHRONIZATION_2;
                cmd->inherited_rendering_active = true;
                vkCmdDispatchIndirect((VkCommandBuffer)cmd, VK_NULL_HANDLE, 0);
                if (!expect_dispatch_scope_failure(cmd, "dispatch-inside-inherited-rendering-unsupported")) return 5;

                memset(cmd, 0, sizeof(*cmd));
                cmd->requested_feature_mask = PDOCKER_VK_FEATURE_SYNCHRONIZATION_2;
                cmd->enabled_extension_mask = PDOCKER_VK_DEVICE_EXT_KHR_SYNCHRONIZATION_2;
                vkCmdDispatch((VkCommandBuffer)cmd, 1, 1, 1);
                if (cmd->recording_failed || !cmd->has_dispatch ||
                    cmd->dispatch_op_count != 1 || cmd->command_op_count != 1 ||
                    cmd->command_ops[0].type != PDOCKER_VK_COMMAND_DISPATCH) {{
                    fprintf(stderr, "normal compute dispatch was not recorded cleanly failed=%d has=%d dispatch=%u ops=%u\\n",
                            cmd->recording_failed ? 1 : 0,
                            cmd->has_dispatch ? 1 : 0,
                            cmd->dispatch_op_count,
                            cmd->command_op_count);
                    return 6;
                }}
                free(cmd);
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


    def test_graphics_mixed_submit_plan_classifies_dispatch_render_scope_boundaries(self):
        source = textwrap.dedent(
            rf"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"
            {COMMAND_BUFFER_LIVE_REGISTRY_HELPER}

            static void add_graphics_record(
                    PdockerVkCommandBuffer *cmd,
                    uint32_t command_type,
                    uint32_t sequence) {{
                PdockerVkGraphicsCommandRecord *record =
                    &cmd->graphics_command_ops[cmd->graphics_command_op_count++];
                memset(record, 0, sizeof(*record));
                record->command_type = command_type;
                record->command_op_sequence = sequence;
            }}

            static void init_inside_rendering_case(PdockerVkCommandBuffer *cmd,
                                                   PdockerVkCommandOp *ops,
                                                   PdockerVkGraphicsCommandRecord *records) {{
                memset(cmd, 0, sizeof(*cmd));
                memset(ops, 0, sizeof(PdockerVkCommandOp) * 4u);
                memset(records, 0, sizeof(PdockerVkGraphicsCommandRecord) * 8u);
                cmd->command_ops = ops;
                cmd->command_op_capacity = 4;
                cmd->graphics_command_ops = records;
                cmd->graphics_command_op_capacity = 8;
                cmd->command_op_count = 2;
                ops[0].type = PDOCKER_VK_COMMAND_GRAPHICS_DRAW;
                ops[1].type = PDOCKER_VK_COMMAND_DISPATCH;
                add_graphics_record(cmd, PDOCKER_GPU_GRAPHICS_V6_COMMAND_BEGIN_RENDERING, 0);
                add_graphics_record(cmd, PDOCKER_GPU_GRAPHICS_V6_COMMAND_DRAW, 0);
                add_graphics_record(cmd, PDOCKER_GPU_GRAPHICS_V6_COMMAND_END_RENDERING, 2);
            }}

            static void init_between_render_scopes_case(PdockerVkCommandBuffer *cmd,
                                                        PdockerVkCommandOp *ops,
                                                        PdockerVkGraphicsCommandRecord *records) {{
                memset(cmd, 0, sizeof(*cmd));
                memset(ops, 0, sizeof(PdockerVkCommandOp) * 4u);
                memset(records, 0, sizeof(PdockerVkGraphicsCommandRecord) * 8u);
                cmd->command_ops = ops;
                cmd->command_op_capacity = 4;
                cmd->graphics_command_ops = records;
                cmd->graphics_command_op_capacity = 8;
                cmd->command_op_count = 3;
                ops[0].type = PDOCKER_VK_COMMAND_GRAPHICS_DRAW;
                ops[1].type = PDOCKER_VK_COMMAND_DISPATCH;
                ops[2].type = PDOCKER_VK_COMMAND_GRAPHICS_DRAW;
                add_graphics_record(cmd, PDOCKER_GPU_GRAPHICS_V6_COMMAND_BEGIN_RENDERING, 0);
                add_graphics_record(cmd, PDOCKER_GPU_GRAPHICS_V6_COMMAND_DRAW, 0);
                add_graphics_record(cmd, PDOCKER_GPU_GRAPHICS_V6_COMMAND_END_RENDERING, 1);
                add_graphics_record(cmd, PDOCKER_GPU_GRAPHICS_V6_COMMAND_BEGIN_RENDERING, 2);
                add_graphics_record(cmd, PDOCKER_GPU_GRAPHICS_V6_COMMAND_DRAW, 2);
                add_graphics_record(cmd, PDOCKER_GPU_GRAPHICS_V6_COMMAND_END_RENDERING, 3);
            }}

            int main(void) {{
                PdockerVkCommandBuffer cmd;
                PdockerVkCommandOp ops[4];
                PdockerVkGraphicsCommandRecord records[8];
                uint32_t first = UINT32_MAX;
                uint32_t last = UINT32_MAX;
                const char *reason = NULL;

                init_inside_rendering_case(&cmd, ops, records);
                if (!graphics_sequence_inside_active_rendering(&cmd, 1)) {{
                    fprintf(stderr, "dispatch sequence was not classified inside active rendering\n");
                    return 2;
                }}
                if (graphics_mixed_submit_plan(&cmd, &first, &last, &reason)) {{
                    fprintf(stderr, "inside-rendering dispatch plan unexpectedly succeeded\n");
                    return 3;
                }}
                if (!reason || strcmp(reason, "graphics-mixed-dispatch-inside-rendering-unimplemented") != 0) {{
                    fprintf(stderr, "inside-rendering dispatch reason got=%s\n", reason ? reason : "<null>");
                    return 4;
                }}

                init_between_render_scopes_case(&cmd, ops, records);
                first = UINT32_MAX;
                last = UINT32_MAX;
                reason = NULL;
                if (graphics_sequence_inside_active_rendering(&cmd, 1)) {{
                    fprintf(stderr, "between-scope dispatch was misclassified inside active rendering\n");
                    return 5;
                }}
                if (strcmp(graphics_mixed_dispatch_inside_frame_reason(&cmd, 1),
                           "graphics-mixed-dispatch-between-render-scopes-unimplemented") != 0) {{
                    fprintf(stderr, "between-scope diagnostic changed\n");
                    return 6;
                }}
                if (!graphics_mixed_submit_plan(&cmd, &first, &last, &reason)) {{
                    fprintf(stderr, "between-scope dispatch plan failed reason=%s\n",
                            reason ? reason : "<null>");
                    return 7;
                }}
                if (first != 0 || last != 3) {{
                    fprintf(stderr, "between-scope frame bounds first=%u last=%u\n", first, last);
                    return 8;
                }}
                if (count_graphics_sequence_segments_split_by_dispatch(&cmd, first, last) != 2) {{
                    fprintf(stderr, "between-scope dispatch did not split into two graphics segments\n");
                    return 9;
                }}
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)



    def test_packed_depth_stencil_buffer_image_copy_footprint_is_aspect_aware(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"
            {COMMAND_BUFFER_LIVE_REGISTRY_HELPER}

            static int expect_footprint(
                    VkFormat format,
                    VkImageAspectFlags aspect,
                    VkDeviceSize expected_offset,
                    VkDeviceSize expected_bytes) {{
                PdockerVkBuffer buffer;
                PdockerVkImage image;
                PdockerVkImageCopyOp op;
                VkDeviceSize offset = 0;
                VkDeviceSize bytes = 0;
                memset(&buffer, 0, sizeof(buffer));
                memset(&image, 0, sizeof(image));
                memset(&op, 0, sizeof(op));
                image.format = format;
                op.buffer = &buffer;
                op.image = &image;
                op.region.bufferOffset = expected_offset;
                op.region.bufferRowLength = 8;
                op.region.bufferImageHeight = 5;
                op.region.imageSubresource.aspectMask = aspect;
                op.region.imageSubresource.layerCount = 2;
                op.region.imageExtent.width = 4;
                op.region.imageExtent.height = 3;
                op.region.imageExtent.depth = 2;
                if (!image_copy_buffer_footprint(&op, &offset, &bytes)) {{
                    fprintf(stderr, "footprint rejected format=%d aspect=0x%x\\n", format, aspect);
                    return 1;
                }}
                if (offset != expected_offset || bytes != expected_bytes) {{
                    fprintf(stderr,
                            "footprint mismatch format=%d aspect=0x%x offset=%llu bytes=%llu expected_offset=%llu expected_bytes=%llu\\n",
                            format,
                            aspect,
                            (unsigned long long)offset,
                            (unsigned long long)bytes,
                            (unsigned long long)expected_offset,
                            (unsigned long long)expected_bytes);
                    return 2;
                }}
                return 0;
            }}

            static int expect_rejected(VkFormat format, VkImageAspectFlags aspect) {{
                PdockerVkBuffer buffer;
                PdockerVkImage image;
                PdockerVkImageCopyOp op;
                VkDeviceSize offset = 0;
                VkDeviceSize bytes = 0;
                memset(&buffer, 0, sizeof(buffer));
                memset(&image, 0, sizeof(image));
                memset(&op, 0, sizeof(op));
                image.format = format;
                op.buffer = &buffer;
                op.image = &image;
                op.region.bufferOffset = 7;
                op.region.bufferRowLength = 8;
                op.region.bufferImageHeight = 5;
                op.region.imageSubresource.aspectMask = aspect;
                op.region.imageSubresource.layerCount = 2;
                op.region.imageExtent.width = 4;
                op.region.imageExtent.height = 3;
                op.region.imageExtent.depth = 2;
                if (image_copy_buffer_footprint(&op, &offset, &bytes)) {{
                    fprintf(stderr,
                            "footprint unexpectedly accepted format=%d aspect=0x%x offset=%llu bytes=%llu\\n",
                            format,
                            aspect,
                            (unsigned long long)offset,
                            (unsigned long long)bytes);
                    return 1;
                }}
                return 0;
            }}

            int main(void) {{
                const VkDeviceSize offset = 7;
                if (expect_footprint(VK_FORMAT_D24_UNORM_S8_UINT,
                                     VK_IMAGE_ASPECT_DEPTH_BIT,
                                     offset, 560)) return 10;
                if (expect_footprint(VK_FORMAT_D24_UNORM_S8_UINT,
                                     VK_IMAGE_ASPECT_STENCIL_BIT,
                                     offset, 140)) return 11;
                if (expect_footprint(VK_FORMAT_D32_SFLOAT_S8_UINT,
                                     VK_IMAGE_ASPECT_DEPTH_BIT,
                                     offset, 560)) return 12;
                if (expect_footprint(VK_FORMAT_D32_SFLOAT_S8_UINT,
                                     VK_IMAGE_ASPECT_STENCIL_BIT,
                                     offset, 140)) return 13;
                if (expect_footprint(VK_FORMAT_R8G8B8A8_UNORM,
                                     VK_IMAGE_ASPECT_COLOR_BIT,
                                     offset, 560)) return 14;
                if (expect_rejected(VK_FORMAT_D24_UNORM_S8_UINT,
                                    VK_IMAGE_ASPECT_DEPTH_BIT | VK_IMAGE_ASPECT_STENCIL_BIT)) return 20;
                if (expect_rejected(VK_FORMAT_D32_SFLOAT_S8_UINT,
                                    VK_IMAGE_ASPECT_DEPTH_BIT | VK_IMAGE_ASPECT_STENCIL_BIT)) return 21;
                if (expect_rejected(VK_FORMAT_D24_UNORM_S8_UINT,
                                    VK_IMAGE_ASPECT_COLOR_BIT)) return 22;
                if (expect_rejected(VK_FORMAT_R8G8B8A8_UNORM,
                                    VK_IMAGE_ASPECT_DEPTH_BIT)) return 23;
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_pipeline_barrier2_image_barriers_normalize_remaining_and_reject_invalid_aspects(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include <stdlib.h>
            #include "{ICD_SOURCE}"
            {COMMAND_BUFFER_LIVE_REGISTRY_HELPER}

            #ifndef VK_IMAGE_ASPECT_PLANE_0_BIT
            #define VK_IMAGE_ASPECT_PLANE_0_BIT ((VkImageAspectFlagBits)0x00000010)
            #endif

            static void init_image(PdockerVkImage *image, VkFormat format) {{
                memset(image, 0, sizeof(*image));
                image->object_id = 0xfeedu;
                image->format = format;
                image->image_type = VK_IMAGE_TYPE_2D;
                image->extent.width = 8;
                image->extent.height = 8;
                image->extent.depth = 1;
                image->mip_levels = 4;
                image->array_layers = 3;
                image->samples = VK_SAMPLE_COUNT_1_BIT;
                image->usage = VK_IMAGE_USAGE_TRANSFER_SRC_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT | VK_IMAGE_USAGE_SAMPLED_BIT;
                image->current_layout = VK_IMAGE_LAYOUT_UNDEFINED;
                image->layout_generation = 1;
                image_register(image);
            }}

            static void init_image_barrier(VkImageMemoryBarrier2 *barrier,
                                           PdockerVkImage *image,
                                           VkImageAspectFlags aspect,
                                           uint32_t base_mip,
                                           uint32_t level_count,
                                           uint32_t base_layer,
                                           uint32_t layer_count) {{
                memset(barrier, 0, sizeof(*barrier));
                barrier->sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER_2;
                barrier->srcStageMask = VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT;
                barrier->dstStageMask = VK_PIPELINE_STAGE_2_TRANSFER_BIT;
                barrier->oldLayout = VK_IMAGE_LAYOUT_UNDEFINED;
                barrier->newLayout = VK_IMAGE_LAYOUT_GENERAL;
                barrier->srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
                barrier->dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
                barrier->image = pdocker_vk_image_to_handle(image);
                barrier->subresourceRange.aspectMask = aspect;
                barrier->subresourceRange.baseMipLevel = base_mip;
                barrier->subresourceRange.levelCount = level_count;
                barrier->subresourceRange.baseArrayLayer = base_layer;
                barrier->subresourceRange.layerCount = layer_count;
            }}

            static void submit_image_barrier(PdockerVkCommandBuffer *cmd,
                                             VkImageMemoryBarrier2 *barrier) {{
                VkDependencyInfo dependency;
                memset(&dependency, 0, sizeof(dependency));
                dependency.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO;
                dependency.imageMemoryBarrierCount = 1;
                dependency.pImageMemoryBarriers = barrier;
                vkCmdPipelineBarrier2((VkCommandBuffer)cmd, &dependency);
            }}

            static int expect_failure(PdockerVkCommandBuffer *cmd, const char *reason) {{
                if (!cmd->recording_failed) {{
                    fprintf(stderr, "image barrier did not fail recording\\n");
                    return 0;
                }}
                if (!cmd->recording_failure_reason || strcmp(cmd->recording_failure_reason, reason) != 0) {{
                    fprintf(stderr, "unexpected failure reason got=%s want=%s\\n",
                            cmd->recording_failure_reason ? cmd->recording_failure_reason : "<null>",
                            reason);
                    return 0;
                }}
                if (cmd->image_barrier_op_count != 0 || cmd->command_op_count != 0 ||
                    cmd->graphics_command_op_count != 0) {{
                    fprintf(stderr, "failed image barrier recorded partial state img=%u ops=%u graphics=%u\\n",
                            cmd->image_barrier_op_count,
                            cmd->command_op_count,
                            cmd->graphics_command_op_count);
                    return 0;
                }}
                return 1;
            }}

            static int expect_recorded_range(PdockerVkCommandBuffer *cmd,
                                             VkImageAspectFlags aspect,
                                             uint32_t base_mip,
                                             uint32_t level_count,
                                             uint32_t base_layer,
                                             uint32_t layer_count) {{
                if (cmd->recording_failed || cmd->graphics_unsupported) {{
                    fprintf(stderr, "valid image barrier failed reason=%s\\n",
                            cmd->recording_failure_reason ? cmd->recording_failure_reason : "<null>");
                    return 0;
                }}
                if (cmd->image_barrier_op_count != 1 || cmd->command_op_count != 2 ||
                    cmd->graphics_command_op_count != 1) {{
                    fprintf(stderr, "valid image barrier counts img=%u ops=%u graphics=%u\\n",
                            cmd->image_barrier_op_count,
                            cmd->command_op_count,
                            cmd->graphics_command_op_count);
                    return 0;
                }}
                const PdockerVkImageBarrierOp *op = &cmd->image_barrier_ops[0];
                if (op->range.aspectMask != aspect ||
                    op->range.baseMipLevel != base_mip ||
                    op->range.levelCount != level_count ||
                    op->range.baseArrayLayer != base_layer ||
                    op->range.layerCount != layer_count) {{
                    fprintf(stderr, "normalized range mismatch aspect=0x%x mip=%u levels=%u layer=%u layers=%u\\n",
                            op->range.aspectMask,
                            op->range.baseMipLevel,
                            op->range.levelCount,
                            op->range.baseArrayLayer,
                            op->range.layerCount);
                    return 0;
                }}
                if (op->range.levelCount == VK_REMAINING_MIP_LEVELS ||
                    op->range.layerCount == VK_REMAINING_ARRAY_LAYERS) {{
                    fprintf(stderr, "sentinel range leaked into recorded metadata\\n");
                    return 0;
                }}
                if (cmd->command_ops[0].type != PDOCKER_VK_COMMAND_IMAGE_BARRIER ||
                    cmd->command_ops[1].type != PDOCKER_VK_COMMAND_BARRIER ||
                    cmd->command_ops[1].image_barrier_op_first != 0 ||
                    cmd->command_ops[1].image_barrier_op_count != 1) {{
                    fprintf(stderr, "command barrier metadata did not point at image barrier\\n");
                    return 0;
                }}
                if (cmd->graphics_command_ops[0].command_type != PDOCKER_GPU_GRAPHICS_V6_COMMAND_BARRIER ||
                    cmd->graphics_command_ops[0].image_barrier_op_first != 0 ||
                    cmd->graphics_command_ops[0].image_barrier_op_count != 1) {{
                    fprintf(stderr, "graphics barrier metadata did not point at image barrier\\n");
                    return 0;
                }}
                return 1;
            }}

            int main(void) {{
                PdockerVkCommandBuffer *cmd = sync_test_command_buffer_alloc();
                if (!cmd) return 9;
                cmd->requested_feature_mask = PDOCKER_VK_FEATURE_SYNCHRONIZATION_2;
                cmd->enabled_extension_mask = PDOCKER_VK_DEVICE_EXT_KHR_SYNCHRONIZATION_2;

                PdockerVkImage color;
                init_image(&color, VK_FORMAT_R8G8B8A8_UNORM);
                VkImageMemoryBarrier2 barrier;
                init_image_barrier(&barrier, &color, VK_IMAGE_ASPECT_COLOR_BIT,
                                   1, VK_REMAINING_MIP_LEVELS,
                                   1, VK_REMAINING_ARRAY_LAYERS);
                submit_image_barrier(cmd, &barrier);
                if (!expect_recorded_range(cmd, VK_IMAGE_ASPECT_COLOR_BIT, 1, 3, 1, 2)) return 2;

                memset(cmd, 0, sizeof(*cmd));
                cmd->requested_feature_mask = PDOCKER_VK_FEATURE_SYNCHRONIZATION_2;
                cmd->enabled_extension_mask = PDOCKER_VK_DEVICE_EXT_KHR_SYNCHRONIZATION_2;
                PdockerVkImage depth;
                init_image(&depth, VK_FORMAT_D32_SFLOAT);
                init_image_barrier(&barrier, &depth, VK_IMAGE_ASPECT_DEPTH_BIT,
                                   0, VK_REMAINING_MIP_LEVELS,
                                   0, VK_REMAINING_ARRAY_LAYERS);
                submit_image_barrier(cmd, &barrier);
                if (!expect_recorded_range(cmd, VK_IMAGE_ASPECT_DEPTH_BIT, 0, 4, 0, 3)) return 3;

                memset(cmd, 0, sizeof(*cmd));
                cmd->requested_feature_mask = PDOCKER_VK_FEATURE_SYNCHRONIZATION_2;
                cmd->enabled_extension_mask = PDOCKER_VK_DEVICE_EXT_KHR_SYNCHRONIZATION_2;
                PdockerVkImage stencil;
                init_image(&stencil, VK_FORMAT_S8_UINT);
                init_image_barrier(&barrier, &stencil, VK_IMAGE_ASPECT_STENCIL_BIT,
                                   2, VK_REMAINING_MIP_LEVELS,
                                   0, 1);
                submit_image_barrier(cmd, &barrier);
                if (!expect_recorded_range(cmd, VK_IMAGE_ASPECT_STENCIL_BIT, 2, 2, 0, 1)) return 4;

                memset(cmd, 0, sizeof(*cmd));
                cmd->requested_feature_mask = PDOCKER_VK_FEATURE_SYNCHRONIZATION_2;
                cmd->enabled_extension_mask = PDOCKER_VK_DEVICE_EXT_KHR_SYNCHRONIZATION_2;
                init_image_barrier(&barrier, &color, VK_IMAGE_ASPECT_PLANE_0_BIT,
                                   0, 1, 0, 1);
                submit_image_barrier(cmd, &barrier);
                if (!expect_failure(cmd, "image-barrier-invalid-range")) return 5;

                memset(cmd, 0, sizeof(*cmd));
                cmd->requested_feature_mask = PDOCKER_VK_FEATURE_SYNCHRONIZATION_2;
                cmd->enabled_extension_mask = PDOCKER_VK_DEVICE_EXT_KHR_SYNCHRONIZATION_2;
                init_image_barrier(&barrier, &color, VK_IMAGE_ASPECT_COLOR_BIT | VK_IMAGE_ASPECT_DEPTH_BIT,
                                   0, 1, 0, 1);
                submit_image_barrier(cmd, &barrier);
                if (!expect_failure(cmd, "image-barrier-invalid-range")) return 6;

                free(cmd);
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


    def test_legacy_pipeline_barrier_image_barriers_normalize_remaining_and_reject_invalid_aspects(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include <stdlib.h>
            #include "{ICD_SOURCE}"
            {COMMAND_BUFFER_LIVE_REGISTRY_HELPER}

            #ifndef VK_IMAGE_ASPECT_PLANE_0_BIT
            #define VK_IMAGE_ASPECT_PLANE_0_BIT ((VkImageAspectFlagBits)0x00000010)
            #endif

            static void init_image(PdockerVkImage *image, VkFormat format) {{
                memset(image, 0, sizeof(*image));
                image->object_id = 0xbeefu;
                image->format = format;
                image->image_type = VK_IMAGE_TYPE_2D;
                image->extent.width = 16;
                image->extent.height = 8;
                image->extent.depth = 1;
                image->mip_levels = 5;
                image->array_layers = 4;
                image->samples = VK_SAMPLE_COUNT_1_BIT;
                image->usage = VK_IMAGE_USAGE_TRANSFER_SRC_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT | VK_IMAGE_USAGE_SAMPLED_BIT;
                image->current_layout = VK_IMAGE_LAYOUT_UNDEFINED;
                image->layout_generation = 1;
                image_register(image);
            }}

            static void init_legacy_image_barrier(VkImageMemoryBarrier *barrier,
                                                  PdockerVkImage *image,
                                                  VkImageAspectFlags aspect,
                                                  uint32_t base_mip,
                                                  uint32_t level_count,
                                                  uint32_t base_layer,
                                                  uint32_t layer_count) {{
                memset(barrier, 0, sizeof(*barrier));
                barrier->sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER;
                barrier->srcAccessMask = VK_ACCESS_SHADER_WRITE_BIT;
                barrier->dstAccessMask = VK_ACCESS_TRANSFER_READ_BIT;
                barrier->oldLayout = VK_IMAGE_LAYOUT_UNDEFINED;
                barrier->newLayout = VK_IMAGE_LAYOUT_GENERAL;
                barrier->srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
                barrier->dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
                barrier->image = pdocker_vk_image_to_handle(image);
                barrier->subresourceRange.aspectMask = aspect;
                barrier->subresourceRange.baseMipLevel = base_mip;
                barrier->subresourceRange.levelCount = level_count;
                barrier->subresourceRange.baseArrayLayer = base_layer;
                barrier->subresourceRange.layerCount = layer_count;
            }}

            static void submit_legacy_image_barrier(PdockerVkCommandBuffer *cmd,
                                                    VkImageMemoryBarrier *barrier) {{
                vkCmdPipelineBarrier((VkCommandBuffer)cmd,
                                     VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
                                     VK_PIPELINE_STAGE_TRANSFER_BIT,
                                     0,
                                     0, NULL,
                                     0, NULL,
                                     1, barrier);
            }}

            static int expect_failure(PdockerVkCommandBuffer *cmd, const char *reason) {{
                if (!cmd->recording_failed) {{
                    fprintf(stderr, "legacy image barrier did not fail recording\\n");
                    return 0;
                }}
                if (!cmd->recording_failure_reason || strcmp(cmd->recording_failure_reason, reason) != 0) {{
                    fprintf(stderr, "unexpected legacy failure reason got=%s want=%s\\n",
                            cmd->recording_failure_reason ? cmd->recording_failure_reason : "<null>",
                            reason);
                    return 0;
                }}
                if (cmd->image_barrier_op_count != 0 || cmd->command_op_count != 0 ||
                    cmd->graphics_command_op_count != 0) {{
                    fprintf(stderr, "failed legacy image barrier recorded partial state img=%u ops=%u graphics=%u\\n",
                            cmd->image_barrier_op_count,
                            cmd->command_op_count,
                            cmd->graphics_command_op_count);
                    return 0;
                }}
                return 1;
            }}

            static int expect_recorded_range(PdockerVkCommandBuffer *cmd,
                                             VkImageAspectFlags aspect,
                                             uint32_t base_mip,
                                             uint32_t level_count,
                                             uint32_t base_layer,
                                             uint32_t layer_count) {{
                if (cmd->recording_failed || cmd->graphics_unsupported) {{
                    fprintf(stderr, "valid legacy image barrier failed reason=%s\\n",
                            cmd->recording_failure_reason ? cmd->recording_failure_reason : "<null>");
                    return 0;
                }}
                if (cmd->image_barrier_op_count != 1 || cmd->command_op_count != 2 ||
                    cmd->graphics_command_op_count != 1) {{
                    fprintf(stderr, "valid legacy image barrier counts img=%u ops=%u graphics=%u\\n",
                            cmd->image_barrier_op_count,
                            cmd->command_op_count,
                            cmd->graphics_command_op_count);
                    return 0;
                }}
                const PdockerVkImageBarrierOp *op = &cmd->image_barrier_ops[0];
                if (op->range.aspectMask != aspect ||
                    op->range.baseMipLevel != base_mip ||
                    op->range.levelCount != level_count ||
                    op->range.baseArrayLayer != base_layer ||
                    op->range.layerCount != layer_count) {{
                    fprintf(stderr, "legacy normalized range mismatch aspect=0x%x mip=%u levels=%u layer=%u layers=%u\\n",
                            op->range.aspectMask,
                            op->range.baseMipLevel,
                            op->range.levelCount,
                            op->range.baseArrayLayer,
                            op->range.layerCount);
                    return 0;
                }}
                if (op->range.levelCount == VK_REMAINING_MIP_LEVELS ||
                    op->range.layerCount == VK_REMAINING_ARRAY_LAYERS) {{
                    fprintf(stderr, "legacy sentinel range leaked into recorded metadata\\n");
                    return 0;
                }}
                if (cmd->command_ops[0].type != PDOCKER_VK_COMMAND_IMAGE_BARRIER ||
                    cmd->command_ops[1].type != PDOCKER_VK_COMMAND_BARRIER ||
                    cmd->command_ops[1].image_barrier_op_first != 0 ||
                    cmd->command_ops[1].image_barrier_op_count != 1) {{
                    fprintf(stderr, "legacy command barrier metadata did not point at image barrier\\n");
                    return 0;
                }}
                if (cmd->graphics_command_ops[0].command_type != PDOCKER_GPU_GRAPHICS_V6_COMMAND_BARRIER ||
                    cmd->graphics_command_ops[0].image_barrier_op_first != 0 ||
                    cmd->graphics_command_ops[0].image_barrier_op_count != 1) {{
                    fprintf(stderr, "legacy graphics barrier metadata did not point at image barrier\\n");
                    return 0;
                }}
                return 1;
            }}

            int main(void) {{
                PdockerVkCommandBuffer *cmd = sync_test_command_buffer_alloc();
                if (!cmd) return 9;

                PdockerVkImage color;
                init_image(&color, VK_FORMAT_R8G8B8A8_UNORM);
                VkImageMemoryBarrier barrier;
                init_legacy_image_barrier(&barrier, &color, VK_IMAGE_ASPECT_COLOR_BIT,
                                          2, VK_REMAINING_MIP_LEVELS,
                                          1, VK_REMAINING_ARRAY_LAYERS);
                submit_legacy_image_barrier(cmd, &barrier);
                if (!expect_recorded_range(cmd, VK_IMAGE_ASPECT_COLOR_BIT, 2, 3, 1, 3)) return 2;

                memset(cmd, 0, sizeof(*cmd));
                PdockerVkImage depth;
                init_image(&depth, VK_FORMAT_D32_SFLOAT);
                init_legacy_image_barrier(&barrier, &depth, VK_IMAGE_ASPECT_DEPTH_BIT,
                                          1, VK_REMAINING_MIP_LEVELS,
                                          0, VK_REMAINING_ARRAY_LAYERS);
                submit_legacy_image_barrier(cmd, &barrier);
                if (!expect_recorded_range(cmd, VK_IMAGE_ASPECT_DEPTH_BIT, 1, 4, 0, 4)) return 3;

                memset(cmd, 0, sizeof(*cmd));
                PdockerVkImage stencil;
                init_image(&stencil, VK_FORMAT_S8_UINT);
                init_legacy_image_barrier(&barrier, &stencil, VK_IMAGE_ASPECT_STENCIL_BIT,
                                          4, VK_REMAINING_MIP_LEVELS,
                                          2, 1);
                submit_legacy_image_barrier(cmd, &barrier);
                if (!expect_recorded_range(cmd, VK_IMAGE_ASPECT_STENCIL_BIT, 4, 1, 2, 1)) return 4;

                memset(cmd, 0, sizeof(*cmd));
                init_legacy_image_barrier(&barrier, &color, VK_IMAGE_ASPECT_PLANE_0_BIT,
                                          0, 1, 0, 1);
                submit_legacy_image_barrier(cmd, &barrier);
                if (!expect_failure(cmd, "image-barrier-invalid-range")) return 5;

                memset(cmd, 0, sizeof(*cmd));
                init_legacy_image_barrier(&barrier, &color, VK_IMAGE_ASPECT_COLOR_BIT | VK_IMAGE_ASPECT_STENCIL_BIT,
                                          0, 1, 0, 1);
                submit_legacy_image_barrier(cmd, &barrier);
                if (!expect_failure(cmd, "image-barrier-invalid-range")) return 6;

                free(cmd);
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


    def test_pipeline_barrier2_records_precise_unsupported_dependency_reasons(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include <stdlib.h>
            #include "{ICD_SOURCE}"
            {COMMAND_BUFFER_LIVE_REGISTRY_HELPER}

            typedef struct DummyPnext {{
                VkStructureType sType;
                const void *pNext;
            }} DummyPnext;

            static int expect_failure(PdockerVkCommandBuffer *cmd, const char *reason) {{
                if (!cmd->recording_failed) {{
                    fprintf(stderr, "pipeline barrier2 did not record failure\\n");
                    return 0;
                }}
                if (!cmd->recording_failure_reason || strcmp(cmd->recording_failure_reason, reason) != 0) {{
                    fprintf(stderr, "unexpected pipeline barrier2 reason got=%s want=%s\\n",
                            cmd->recording_failure_reason ? cmd->recording_failure_reason : "<null>",
                            reason);
                    return 0;
                }}
                if (cmd->command_op_count != 0 || cmd->graphics_command_op_count != 0 ||
                    cmd->memory_barrier_op_count != 0 || cmd->buffer_barrier_op_count != 0 ||
                    cmd->image_barrier_op_count != 0) {{
                    fprintf(stderr, "failed pipeline barrier2 recorded partial state ops=%u graphics=%u mem=%u buf=%u img=%u\\n",
                            cmd->command_op_count,
                            cmd->graphics_command_op_count,
                            cmd->memory_barrier_op_count,
                            cmd->buffer_barrier_op_count,
                            cmd->image_barrier_op_count);
                    return 0;
                }}
                if (vkEndCommandBuffer((VkCommandBuffer)cmd) != VK_ERROR_FEATURE_NOT_PRESENT) {{
                    fprintf(stderr, "failed pipeline barrier2 did not fail close at end\\n");
                    return 0;
                }}
                return 1;
            }}

            int main(void) {{
                PdockerVkCommandBuffer *cmd = sync_test_command_buffer_alloc();
                if (!cmd) return 9;
                cmd->requested_feature_mask = PDOCKER_VK_FEATURE_SYNCHRONIZATION_2;
                cmd->enabled_extension_mask = PDOCKER_VK_DEVICE_EXT_KHR_SYNCHRONIZATION_2;

                DummyPnext unsupported;
                memset(&unsupported, 0, sizeof(unsupported));
                unsupported.sType = (VkStructureType)1000060013;

                vkCmdPipelineBarrier2((VkCommandBuffer)cmd, NULL);
                if (!expect_failure(cmd, "pipeline-barrier2-dependency-info-unsupported")) return 20;

                memset(cmd, 0, sizeof(*cmd));
                cmd->requested_feature_mask = PDOCKER_VK_FEATURE_SYNCHRONIZATION_2;
                cmd->enabled_extension_mask = PDOCKER_VK_DEVICE_EXT_KHR_SYNCHRONIZATION_2;
                VkDependencyInfo dependency;
                memset(&dependency, 0, sizeof(dependency));
                dependency.sType = VK_STRUCTURE_TYPE_MEMORY_BARRIER_2;
                vkCmdPipelineBarrier2((VkCommandBuffer)cmd, &dependency);
                if (!expect_failure(cmd, "pipeline-barrier2-dependency-info-unsupported")) return 21;

                memset(cmd, 0, sizeof(*cmd));
                cmd->requested_feature_mask = PDOCKER_VK_FEATURE_SYNCHRONIZATION_2;
                cmd->enabled_extension_mask = PDOCKER_VK_DEVICE_EXT_KHR_SYNCHRONIZATION_2;
                memset(&dependency, 0, sizeof(dependency));
                dependency.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO;
                dependency.pNext = &unsupported;
                vkCmdPipelineBarrier2((VkCommandBuffer)cmd, &dependency);
                if (!expect_failure(cmd, "pipeline-barrier2-pnext-unsupported")) return 2;

                memset(cmd, 0, sizeof(*cmd));
                cmd->requested_feature_mask = PDOCKER_VK_FEATURE_SYNCHRONIZATION_2;
                cmd->enabled_extension_mask = PDOCKER_VK_DEVICE_EXT_KHR_SYNCHRONIZATION_2;
                VkMemoryBarrier2 memory_barrier;
                memset(&memory_barrier, 0, sizeof(memory_barrier));
                memory_barrier.sType = VK_STRUCTURE_TYPE_MEMORY_BARRIER_2;
                memory_barrier.pNext = &unsupported;
                memory_barrier.srcStageMask = VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT;
                memory_barrier.dstStageMask = VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT;
                memset(&dependency, 0, sizeof(dependency));
                dependency.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO;
                dependency.memoryBarrierCount = 1;
                dependency.pMemoryBarriers = &memory_barrier;
                vkCmdPipelineBarrier2((VkCommandBuffer)cmd, &dependency);
                if (!expect_failure(cmd, "pipeline-barrier2-pnext-unsupported")) return 3;

                memset(cmd, 0, sizeof(*cmd));
                cmd->requested_feature_mask = PDOCKER_VK_FEATURE_SYNCHRONIZATION_2;
                cmd->enabled_extension_mask = PDOCKER_VK_DEVICE_EXT_KHR_SYNCHRONIZATION_2;
                memset(&dependency, 0, sizeof(dependency));
                dependency.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO;
                dependency.memoryBarrierCount = 1;
                dependency.pMemoryBarriers = NULL;
                vkCmdPipelineBarrier2((VkCommandBuffer)cmd, &dependency);
                if (!expect_failure(cmd, "pipeline-barrier2-missing-barrier-array")) return 4;

                memset(cmd, 0, sizeof(*cmd));
                cmd->requested_feature_mask = PDOCKER_VK_FEATURE_SYNCHRONIZATION_2;
                cmd->enabled_extension_mask = PDOCKER_VK_DEVICE_EXT_KHR_SYNCHRONIZATION_2;
                memset(&memory_barrier, 0, sizeof(memory_barrier));
                memory_barrier.sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER_2;
                memory_barrier.srcStageMask = VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT;
                memory_barrier.dstStageMask = VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT;
                memset(&dependency, 0, sizeof(dependency));
                dependency.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO;
                dependency.memoryBarrierCount = 1;
                dependency.pMemoryBarriers = &memory_barrier;
                vkCmdPipelineBarrier2((VkCommandBuffer)cmd, &dependency);
                if (!expect_failure(cmd, "pipeline-barrier2-dependency-info-unsupported")) return 22;

                memset(cmd, 0, sizeof(*cmd));
                cmd->requested_feature_mask = PDOCKER_VK_FEATURE_SYNCHRONIZATION_2;
                cmd->enabled_extension_mask = PDOCKER_VK_DEVICE_EXT_KHR_SYNCHRONIZATION_2;
                memset(&memory_barrier, 0, sizeof(memory_barrier));
                memory_barrier.sType = VK_STRUCTURE_TYPE_MEMORY_BARRIER_2;
                memory_barrier.srcStageMask = VK_PIPELINE_STAGE_2_NONE;
                memory_barrier.srcAccessMask = VK_ACCESS_2_SHADER_WRITE_BIT;
                memory_barrier.dstStageMask = VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT;
                memset(&dependency, 0, sizeof(dependency));
                dependency.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO;
                dependency.memoryBarrierCount = 1;
                dependency.pMemoryBarriers = &memory_barrier;
                vkCmdPipelineBarrier2((VkCommandBuffer)cmd, &dependency);
                if (!expect_failure(cmd, "pipeline-barrier2-none-stage-access-unsupported")) return 5;

                memset(cmd, 0, sizeof(*cmd));
                cmd->requested_feature_mask = PDOCKER_VK_FEATURE_SYNCHRONIZATION_2;
                cmd->enabled_extension_mask = PDOCKER_VK_DEVICE_EXT_KHR_SYNCHRONIZATION_2;
                memset(&dependency, 0, sizeof(dependency));
                dependency.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO;
                dependency.dependencyFlags = VK_DEPENDENCY_VIEW_LOCAL_BIT;
                vkCmdPipelineBarrier2((VkCommandBuffer)cmd, &dependency);
                if (!expect_failure(cmd, "pipeline-barrier2-dependency-flags-unsupported")) return 6;

                memset(cmd, 0, sizeof(*cmd));
                cmd->requested_feature_mask = PDOCKER_VK_FEATURE_SYNCHRONIZATION_2;
                cmd->enabled_extension_mask = PDOCKER_VK_DEVICE_EXT_KHR_SYNCHRONIZATION_2;
                memset(&memory_barrier, 0, sizeof(memory_barrier));
                memory_barrier.sType = VK_STRUCTURE_TYPE_MEMORY_BARRIER_2;
                memory_barrier.srcStageMask = VK_PIPELINE_STAGE_2_NONE;
                memory_barrier.dstStageMask = VK_PIPELINE_STAGE_2_NONE;
                memset(&dependency, 0, sizeof(dependency));
                dependency.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO;
                dependency.memoryBarrierCount = 1;
                dependency.pMemoryBarriers = &memory_barrier;
                vkCmdPipelineBarrier2((VkCommandBuffer)cmd, &dependency);
                if (cmd->recording_failed || cmd->graphics_unsupported ||
                    cmd->command_op_count != 1 || cmd->memory_barrier_op_count != 1 ||
                    cmd->graphics_command_op_count != 1 ||
                    cmd->graphics_command_ops[0].command_type != PDOCKER_GPU_GRAPHICS_V6_COMMAND_BARRIER) {{
                    fprintf(stderr, "valid NONE/zero-access barrier2 did not record cleanly failed=%d unsupported=%d ops=%u mem=%u graphics=%u\\n",
                            cmd->recording_failed ? 1 : 0,
                            cmd->graphics_unsupported ? 1 : 0,
                            cmd->command_op_count,
                            cmd->memory_barrier_op_count,
                            cmd->graphics_command_op_count);
                    return 7;
                }}
                if (vkEndCommandBuffer((VkCommandBuffer)cmd) != VK_SUCCESS) {{
                    fprintf(stderr, "valid pipeline barrier2 failed at end\\n");
                    return 8;
                }}
                free(cmd);
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_pipeline_barrier_noop_dependency_flags_are_stripped_or_rejected_with_ownership(self):
        source = textwrap.dedent(
            rf"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include <stdlib.h>
            #include "{ICD_SOURCE}"
            {COMMAND_BUFFER_LIVE_REGISTRY_HELPER}

            #ifndef VK_DEPENDENCY_QUEUE_FAMILY_OWNERSHIP_TRANSFER_USE_ALL_STAGES_BIT_KHR
            int main(void) {{
                return 0;
            }}
            #else
            static void reset_cmd(PdockerVkCommandBuffer *cmd, int sync2) {{
                memset(cmd, 0, sizeof(*cmd));
                if (sync2) {{
                    cmd->requested_feature_mask = PDOCKER_VK_FEATURE_SYNCHRONIZATION_2;
                    cmd->enabled_extension_mask = PDOCKER_VK_DEVICE_EXT_KHR_SYNCHRONIZATION_2;
                }}
            }}

            static int expect_no_partial_failure(PdockerVkCommandBuffer *cmd, const char *reason, int code) {{
                if (!cmd->recording_failed || !cmd->recording_failure_reason ||
                    strcmp(cmd->recording_failure_reason, reason) != 0) {{
                    fprintf(stderr, "case %d unexpected failure got=%s want=%s failed=%d\n",
                            code,
                            cmd->recording_failure_reason ? cmd->recording_failure_reason : "<null>",
                            reason,
                            cmd->recording_failed ? 1 : 0);
                    return code;
                }}
                if (cmd->memory_barrier_op_count != 0 || cmd->buffer_barrier_op_count != 0 ||
                    cmd->image_barrier_op_count != 0 || cmd->command_op_count != 0 ||
                    cmd->graphics_command_op_count != 0) {{
                    fprintf(stderr, "case %d partial barrier state mem=%u buf=%u img=%u cmd=%u gfx=%u\n",
                            code,
                            cmd->memory_barrier_op_count,
                            cmd->buffer_barrier_op_count,
                            cmd->image_barrier_op_count,
                            cmd->command_op_count,
                            cmd->graphics_command_op_count);
                    return code + 100;
                }}
                if (vkEndCommandBuffer((VkCommandBuffer)cmd) != VK_ERROR_FEATURE_NOT_PRESENT) {{
                    fprintf(stderr, "case %d did not fail closed at end\n", code);
                    return code + 200;
                }}
                return 0;
            }}

            int main(void) {{
                PdockerVkCommandBuffer *cmd = sync_test_command_buffer_alloc();
                PdockerVkBuffer *buffer = (PdockerVkBuffer *)calloc(1, sizeof(*buffer));
                if (!cmd || !buffer) return 99;
                buffer->object_id = 0x1357u;
                buffer->size = 4096u;
                buffer_register(buffer);

                const VkDependencyFlags noop_flag =
                    VK_DEPENDENCY_QUEUE_FAMILY_OWNERSHIP_TRANSFER_USE_ALL_STAGES_BIT_KHR;
                const VkDependencyFlags mixed_flags = VK_DEPENDENCY_BY_REGION_BIT | noop_flag;

                VkMemoryBarrier memory_barrier;
                memset(&memory_barrier, 0, sizeof(memory_barrier));
                memory_barrier.sType = VK_STRUCTURE_TYPE_MEMORY_BARRIER;
                memory_barrier.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
                memory_barrier.dstAccessMask = VK_ACCESS_SHADER_READ_BIT;

                reset_cmd(cmd, 0);
                vkCmdPipelineBarrier((VkCommandBuffer)cmd,
                                     VK_PIPELINE_STAGE_TRANSFER_BIT,
                                     VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
                                     mixed_flags,
                                     1, &memory_barrier,
                                     0, NULL,
                                     0, NULL);
                if (cmd->recording_failed || cmd->memory_barrier_op_count != 1 ||
                    cmd->buffer_barrier_op_count != 0 || cmd->image_barrier_op_count != 0 ||
                    cmd->command_op_count != 1 || cmd->graphics_command_op_count != 1) {{
                    fprintf(stderr, "legacy no-ownership noop dependency flag was not accepted cleanly failed=%d mem=%u cmd=%u gfx=%u\n",
                            cmd->recording_failed ? 1 : 0,
                            cmd->memory_barrier_op_count,
                            cmd->command_op_count,
                            cmd->graphics_command_op_count);
                    return 2;
                }}
                if (cmd->command_ops[0].dependency_flags != VK_DEPENDENCY_BY_REGION_BIT ||
                    cmd->graphics_command_ops[0].flags != VK_DEPENDENCY_BY_REGION_BIT) {{
                    fprintf(stderr, "legacy noop dependency flag was not stripped command=0x%x graphics=0x%x\n",
                            cmd->command_ops[0].dependency_flags,
                            cmd->graphics_command_ops[0].flags);
                    return 3;
                }}

                VkMemoryBarrier2 memory_barrier2;
                memset(&memory_barrier2, 0, sizeof(memory_barrier2));
                memory_barrier2.sType = VK_STRUCTURE_TYPE_MEMORY_BARRIER_2;
                memory_barrier2.srcStageMask = VK_PIPELINE_STAGE_2_TRANSFER_BIT;
                memory_barrier2.srcAccessMask = VK_ACCESS_2_TRANSFER_WRITE_BIT;
                memory_barrier2.dstStageMask = VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT;
                memory_barrier2.dstAccessMask = VK_ACCESS_2_SHADER_READ_BIT;

                VkDependencyInfo dependency;
                memset(&dependency, 0, sizeof(dependency));
                dependency.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO;
                dependency.dependencyFlags = mixed_flags;
                dependency.memoryBarrierCount = 1;
                dependency.pMemoryBarriers = &memory_barrier2;

                reset_cmd(cmd, 1);
                vkCmdPipelineBarrier2((VkCommandBuffer)cmd, &dependency);
                if (cmd->recording_failed || cmd->memory_barrier_op_count != 1 ||
                    cmd->buffer_barrier_op_count != 0 || cmd->image_barrier_op_count != 0 ||
                    cmd->command_op_count != 1 || cmd->graphics_command_op_count != 1) {{
                    fprintf(stderr, "sync2 no-ownership noop dependency flag was not accepted cleanly failed=%d mem=%u cmd=%u gfx=%u\n",
                            cmd->recording_failed ? 1 : 0,
                            cmd->memory_barrier_op_count,
                            cmd->command_op_count,
                            cmd->graphics_command_op_count);
                    return 4;
                }}
                if (cmd->command_ops[0].dependency_flags != VK_DEPENDENCY_BY_REGION_BIT ||
                    cmd->graphics_command_ops[0].flags != VK_DEPENDENCY_BY_REGION_BIT) {{
                    fprintf(stderr, "sync2 noop dependency flag was not stripped command=0x%x graphics=0x%x\n",
                            cmd->command_ops[0].dependency_flags,
                            cmd->graphics_command_ops[0].flags);
                    return 5;
                }}

                VkBufferMemoryBarrier buffer_barrier;
                memset(&buffer_barrier, 0, sizeof(buffer_barrier));
                buffer_barrier.sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER;
                buffer_barrier.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
                buffer_barrier.dstAccessMask = VK_ACCESS_SHADER_READ_BIT;
                buffer_barrier.srcQueueFamilyIndex = 0;
                buffer_barrier.dstQueueFamilyIndex = 1;
                buffer_barrier.buffer = pdocker_vk_buffer_to_handle(buffer);
                buffer_barrier.offset = 0;
                buffer_barrier.size = 64;

                reset_cmd(cmd, 0);
                vkCmdPipelineBarrier((VkCommandBuffer)cmd,
                                     VK_PIPELINE_STAGE_TRANSFER_BIT,
                                     VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
                                     mixed_flags,
                                     1, &memory_barrier,
                                     1, &buffer_barrier,
                                     0, NULL);
                int rc = expect_no_partial_failure(cmd, "legacy-pipeline-barrier-unsupported", 6);
                if (rc) return rc;

                VkBufferMemoryBarrier2 buffer_barrier2;
                memset(&buffer_barrier2, 0, sizeof(buffer_barrier2));
                buffer_barrier2.sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER_2;
                buffer_barrier2.srcStageMask = VK_PIPELINE_STAGE_2_TRANSFER_BIT;
                buffer_barrier2.srcAccessMask = VK_ACCESS_2_TRANSFER_WRITE_BIT;
                buffer_barrier2.dstStageMask = VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT;
                buffer_barrier2.dstAccessMask = VK_ACCESS_2_SHADER_READ_BIT;
                buffer_barrier2.srcQueueFamilyIndex = 0;
                buffer_barrier2.dstQueueFamilyIndex = 1;
                buffer_barrier2.buffer = pdocker_vk_buffer_to_handle(buffer);
                buffer_barrier2.offset = 0;
                buffer_barrier2.size = 64;

                memset(&dependency, 0, sizeof(dependency));
                dependency.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO;
                dependency.dependencyFlags = mixed_flags;
                dependency.memoryBarrierCount = 1;
                dependency.pMemoryBarriers = &memory_barrier2;
                dependency.bufferMemoryBarrierCount = 1;
                dependency.pBufferMemoryBarriers = &buffer_barrier2;

                reset_cmd(cmd, 1);
                vkCmdPipelineBarrier2((VkCommandBuffer)cmd, &dependency);
                rc = expect_no_partial_failure(cmd, "pipeline-barrier2-dependency-flags-unsupported", 7);
                if (rc) return rc;

                free(buffer);
                free(cmd);
                return 0;
            }}
            #endif
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_event_barrier_noop_dependency_flags_are_stripped_or_rejected_with_ownership(self):
        source = textwrap.dedent(
            rf"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include <stdlib.h>
            #include "{ICD_SOURCE}"
            {COMMAND_BUFFER_LIVE_REGISTRY_HELPER}

            #ifndef VK_DEPENDENCY_QUEUE_FAMILY_OWNERSHIP_TRANSFER_USE_ALL_STAGES_BIT_KHR
            int main(void) {{
                return 0;
            }}
            #else
            static void reset_cmd(PdockerVkCommandBuffer *cmd) {{
                memset(cmd, 0, sizeof(*cmd));
                cmd->requested_feature_mask = PDOCKER_VK_FEATURE_SYNCHRONIZATION_2;
                cmd->enabled_extension_mask = PDOCKER_VK_DEVICE_EXT_KHR_SYNCHRONIZATION_2;
            }}

            static int expect_no_partial_failure(PdockerVkCommandBuffer *cmd, const char *reason, int code) {{
                if (!cmd->recording_failed || !cmd->recording_failure_reason ||
                    strcmp(cmd->recording_failure_reason, reason) != 0) {{
                    fprintf(stderr, "case %d unexpected failure got=%s want=%s failed=%d\n",
                            code,
                            cmd->recording_failure_reason ? cmd->recording_failure_reason : "<null>",
                            reason,
                            cmd->recording_failed ? 1 : 0);
                    return code;
                }}
                if (cmd->memory_barrier_op_count != 0 || cmd->buffer_barrier_op_count != 0 ||
                    cmd->image_barrier_op_count != 0 || cmd->event_wait_ref_count != 0 ||
                    cmd->command_op_count != 0 || cmd->graphics_command_op_count != 0) {{
                    fprintf(stderr, "case %d partial event barrier state mem=%u buf=%u img=%u refs=%u cmd=%u gfx=%u\n",
                            code,
                            cmd->memory_barrier_op_count,
                            cmd->buffer_barrier_op_count,
                            cmd->image_barrier_op_count,
                            cmd->event_wait_ref_count,
                            cmd->command_op_count,
                            cmd->graphics_command_op_count);
                    return code + 100;
                }}
                if (vkEndCommandBuffer((VkCommandBuffer)cmd) != VK_ERROR_FEATURE_NOT_PRESENT) {{
                    fprintf(stderr, "case %d did not fail closed at end\n", code);
                    return code + 200;
                }}
                return 0;
            }}

            int main(void) {{
                PdockerVkCommandBuffer *cmd = sync_test_command_buffer_alloc();
                PdockerVkBuffer *buffer = (PdockerVkBuffer *)calloc(1, sizeof(*buffer));
                PdockerVkImage *image = (PdockerVkImage *)calloc(1, sizeof(*image));
                if (!cmd || !buffer || !image) return 99;
                buffer->object_id = 0x2468u;
                buffer->size = 4096u;
                buffer_register(buffer);
                image->object_id = 0x8642u;
                image->format = VK_FORMAT_R8G8B8A8_UNORM;
                image->image_type = VK_IMAGE_TYPE_2D;
                image->extent.width = 16;
                image->extent.height = 16;
                image->extent.depth = 1;
                image->mip_levels = 1;
                image->array_layers = 1;
                image->samples = VK_SAMPLE_COUNT_1_BIT;
                image->usage = VK_IMAGE_USAGE_TRANSFER_SRC_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT;
                image->current_layout = VK_IMAGE_LAYOUT_UNDEFINED;
                image->layout_generation = 1;
                image_register(image);

                VkEventCreateInfo event_info;
                memset(&event_info, 0, sizeof(event_info));
                event_info.sType = VK_STRUCTURE_TYPE_EVENT_CREATE_INFO;
                VkEvent event = VK_NULL_HANDLE;
                if (vkCreateEvent(VK_NULL_HANDLE, &event_info, NULL, &event) != VK_SUCCESS || !event) return 98;
                VkEvent events[1] = {{ event }};

                const VkDependencyFlags noop_flag =
                    VK_DEPENDENCY_QUEUE_FAMILY_OWNERSHIP_TRANSFER_USE_ALL_STAGES_BIT_KHR;
                const VkDependencyFlags mixed_flags = VK_DEPENDENCY_BY_REGION_BIT | noop_flag;

                VkMemoryBarrier2 memory_barrier;
                memset(&memory_barrier, 0, sizeof(memory_barrier));
                memory_barrier.sType = VK_STRUCTURE_TYPE_MEMORY_BARRIER_2;
                memory_barrier.srcStageMask = VK_PIPELINE_STAGE_2_TRANSFER_BIT;
                memory_barrier.srcAccessMask = VK_ACCESS_2_TRANSFER_WRITE_BIT;
                memory_barrier.dstStageMask = VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT;
                memory_barrier.dstAccessMask = VK_ACCESS_2_SHADER_READ_BIT;

                VkDependencyInfo dependency;
                memset(&dependency, 0, sizeof(dependency));
                dependency.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO;
                dependency.dependencyFlags = mixed_flags;
                dependency.memoryBarrierCount = 1;
                dependency.pMemoryBarriers = &memory_barrier;

                reset_cmd(cmd);
                vkCmdSetEvent2((VkCommandBuffer)cmd, event, &dependency);
                if (cmd->recording_failed || cmd->memory_barrier_op_count != 1 ||
                    cmd->command_op_count != 1 || cmd->graphics_command_op_count != 1 ||
                    cmd->graphics_command_ops[0].flags != VK_DEPENDENCY_BY_REGION_BIT) {{
                    fprintf(stderr, "set-event2 noop flag was not accepted/stripped failed=%d mem=%u cmd=%u gfx=%u flags=0x%x\n",
                            cmd->recording_failed ? 1 : 0,
                            cmd->memory_barrier_op_count,
                            cmd->command_op_count,
                            cmd->graphics_command_op_count,
                            cmd->graphics_command_op_count ? cmd->graphics_command_ops[0].flags : 0);
                    return 2;
                }}

                reset_cmd(cmd);
                vkCmdWaitEvents2((VkCommandBuffer)cmd, 1, events, &dependency);
                if (cmd->recording_failed || cmd->memory_barrier_op_count != 1 ||
                    cmd->event_wait_ref_count != 1 ||
                    cmd->command_op_count != 1 || cmd->graphics_command_op_count != 1 ||
                    cmd->graphics_command_ops[0].flags != VK_DEPENDENCY_BY_REGION_BIT) {{
                    fprintf(stderr, "wait-events2 noop flag was not accepted/stripped failed=%d mem=%u refs=%u cmd=%u gfx=%u flags=0x%x\n",
                            cmd->recording_failed ? 1 : 0,
                            cmd->memory_barrier_op_count,
                            cmd->event_wait_ref_count,
                            cmd->command_op_count,
                            cmd->graphics_command_op_count,
                            cmd->graphics_command_op_count ? cmd->graphics_command_ops[0].flags : 0);
                    return 3;
                }}

                VkBufferMemoryBarrier2 buffer_barrier;
                memset(&buffer_barrier, 0, sizeof(buffer_barrier));
                buffer_barrier.sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER_2;
                buffer_barrier.srcStageMask = VK_PIPELINE_STAGE_2_TRANSFER_BIT;
                buffer_barrier.srcAccessMask = VK_ACCESS_2_TRANSFER_WRITE_BIT;
                buffer_barrier.dstStageMask = VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT;
                buffer_barrier.dstAccessMask = VK_ACCESS_2_SHADER_READ_BIT;
                buffer_barrier.srcQueueFamilyIndex = 0;
                buffer_barrier.dstQueueFamilyIndex = 1;
                buffer_barrier.buffer = pdocker_vk_buffer_to_handle(buffer);
                buffer_barrier.offset = 0;
                buffer_barrier.size = 64;

                dependency.bufferMemoryBarrierCount = 1;
                dependency.pBufferMemoryBarriers = &buffer_barrier;

                reset_cmd(cmd);
                vkCmdSetEvent2((VkCommandBuffer)cmd, event, &dependency);
                int rc = expect_no_partial_failure(cmd, "event-set2-dependency-flags-unsupported", 4);
                if (rc) return rc;

                reset_cmd(cmd);
                vkCmdWaitEvents2((VkCommandBuffer)cmd, 1, events, &dependency);
                rc = expect_no_partial_failure(cmd, "event-wait2-dependency-flags-unsupported", 5);
                if (rc) return rc;

                VkImageMemoryBarrier2 image_barrier;
                memset(&image_barrier, 0, sizeof(image_barrier));
                image_barrier.sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER_2;
                image_barrier.srcStageMask = VK_PIPELINE_STAGE_2_TRANSFER_BIT;
                image_barrier.srcAccessMask = VK_ACCESS_2_TRANSFER_WRITE_BIT;
                image_barrier.dstStageMask = VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT;
                image_barrier.dstAccessMask = VK_ACCESS_2_SHADER_READ_BIT;
                image_barrier.oldLayout = VK_IMAGE_LAYOUT_UNDEFINED;
                image_barrier.newLayout = VK_IMAGE_LAYOUT_GENERAL;
                image_barrier.srcQueueFamilyIndex = 0;
                image_barrier.dstQueueFamilyIndex = 1;
                image_barrier.image = pdocker_vk_image_to_handle(image);
                image_barrier.subresourceRange.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
                image_barrier.subresourceRange.baseMipLevel = 0;
                image_barrier.subresourceRange.levelCount = 1;
                image_barrier.subresourceRange.baseArrayLayer = 0;
                image_barrier.subresourceRange.layerCount = 1;

                dependency.bufferMemoryBarrierCount = 0;
                dependency.pBufferMemoryBarriers = NULL;
                dependency.imageMemoryBarrierCount = 1;
                dependency.pImageMemoryBarriers = &image_barrier;

                reset_cmd(cmd);
                vkCmdSetEvent2((VkCommandBuffer)cmd, event, &dependency);
                rc = expect_no_partial_failure(cmd, "event-set2-dependency-flags-unsupported", 6);
                if (rc) return rc;

                reset_cmd(cmd);
                vkCmdWaitEvents2((VkCommandBuffer)cmd, 1, events, &dependency);
                rc = expect_no_partial_failure(cmd, "event-wait2-dependency-flags-unsupported", 7);
                if (rc) return rc;

                vkDestroyEvent(VK_NULL_HANDLE, event, NULL);
                free(image);
                free(buffer);
                free(cmd);
                return 0;
            }}
            #endif
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


    def test_pipeline_barrier_cross_queue_family_fails_before_partial_recording(self):
        source = textwrap.dedent(
            rf"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include <stdlib.h>
            #include "{ICD_SOURCE}"
            {COMMAND_BUFFER_LIVE_REGISTRY_HELPER}

            static void reset_cmd(PdockerVkCommandBuffer *cmd, int sync2) {{
                memset(cmd, 0, sizeof(*cmd));
                if (sync2) {{
                    cmd->requested_feature_mask = PDOCKER_VK_FEATURE_SYNCHRONIZATION_2;
                    cmd->enabled_extension_mask = PDOCKER_VK_DEVICE_EXT_KHR_SYNCHRONIZATION_2;
                }}
            }}

            static void init_image(PdockerVkImage *image) {{
                memset(image, 0, sizeof(*image));
                image->object_id = 0xabcdu;
                image->format = VK_FORMAT_R8G8B8A8_UNORM;
                image->image_type = VK_IMAGE_TYPE_2D;
                image->extent.width = 16;
                image->extent.height = 16;
                image->extent.depth = 1;
                image->mip_levels = 1;
                image->array_layers = 1;
                image->samples = VK_SAMPLE_COUNT_1_BIT;
                image->usage = VK_IMAGE_USAGE_TRANSFER_SRC_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT;
                image->current_layout = VK_IMAGE_LAYOUT_UNDEFINED;
                image->layout_generation = 1;
                image_register(image);
            }}

            static int expect_clean_failure(PdockerVkCommandBuffer *cmd, const char *reason, int code) {{
                if (!cmd->recording_failed || !cmd->recording_failure_reason ||
                    strcmp(cmd->recording_failure_reason, reason) != 0) {{
                    fprintf(stderr, "case %d unexpected failure got=%s want=%s failed=%d\n",
                            code,
                            cmd->recording_failure_reason ? cmd->recording_failure_reason : "<null>",
                            reason,
                            cmd->recording_failed ? 1 : 0);
                    return code;
                }}
                if (cmd->memory_barrier_op_count != 0 || cmd->buffer_barrier_op_count != 0 ||
                    cmd->image_barrier_op_count != 0 || cmd->command_op_count != 0 ||
                    cmd->graphics_command_op_count != 0) {{
                    fprintf(stderr, "case %d partial state mem=%u buf=%u img=%u cmd=%u gfx=%u\n",
                            code,
                            cmd->memory_barrier_op_count,
                            cmd->buffer_barrier_op_count,
                            cmd->image_barrier_op_count,
                            cmd->command_op_count,
                            cmd->graphics_command_op_count);
                    return code + 100;
                }}
                if (vkEndCommandBuffer((VkCommandBuffer)cmd) != VK_ERROR_FEATURE_NOT_PRESENT) {{
                    fprintf(stderr, "case %d did not fail closed at end\n", code);
                    return code + 200;
                }}
                return 0;
            }}

            int main(void) {{
                PdockerVkCommandBuffer *cmd = sync_test_command_buffer_alloc();
                PdockerVkBuffer *buffer = (PdockerVkBuffer *)calloc(1, sizeof(*buffer));
                PdockerVkImage *image = (PdockerVkImage *)calloc(1, sizeof(*image));
                if (!cmd || !buffer || !image) return 99;
                buffer->object_id = 0x1234u;
                buffer->size = 4096u;
                buffer_register(buffer);
                init_image(image);

                VkMemoryBarrier memory_barrier;
                memset(&memory_barrier, 0, sizeof(memory_barrier));
                memory_barrier.sType = VK_STRUCTURE_TYPE_MEMORY_BARRIER;
                memory_barrier.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
                memory_barrier.dstAccessMask = VK_ACCESS_SHADER_READ_BIT;

                VkBufferMemoryBarrier buffer_barrier;
                memset(&buffer_barrier, 0, sizeof(buffer_barrier));
                buffer_barrier.sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER;
                buffer_barrier.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
                buffer_barrier.dstAccessMask = VK_ACCESS_SHADER_READ_BIT;
                buffer_barrier.srcQueueFamilyIndex = 0;
                buffer_barrier.dstQueueFamilyIndex = 1;
                buffer_barrier.buffer = pdocker_vk_buffer_to_handle(buffer);
                buffer_barrier.offset = 0;
                buffer_barrier.size = 64;

                reset_cmd(cmd, 0);
                vkCmdPipelineBarrier((VkCommandBuffer)cmd,
                                     VK_PIPELINE_STAGE_TRANSFER_BIT,
                                     VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
                                     0,
                                     1, &memory_barrier,
                                     1, &buffer_barrier,
                                     0, NULL);
                int rc = expect_clean_failure(cmd, "buffer-barrier-cross-queue-family", 2);
                if (rc) return rc;

                VkImageMemoryBarrier image_barrier;
                memset(&image_barrier, 0, sizeof(image_barrier));
                image_barrier.sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER;
                image_barrier.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
                image_barrier.dstAccessMask = VK_ACCESS_SHADER_READ_BIT;
                image_barrier.oldLayout = VK_IMAGE_LAYOUT_UNDEFINED;
                image_barrier.newLayout = VK_IMAGE_LAYOUT_GENERAL;
                image_barrier.srcQueueFamilyIndex = 0;
                image_barrier.dstQueueFamilyIndex = 1;
                image_barrier.image = pdocker_vk_image_to_handle(image);
                image_barrier.subresourceRange.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
                image_barrier.subresourceRange.baseMipLevel = 0;
                image_barrier.subresourceRange.levelCount = 1;
                image_barrier.subresourceRange.baseArrayLayer = 0;
                image_barrier.subresourceRange.layerCount = 1;

                reset_cmd(cmd, 0);
                vkCmdPipelineBarrier((VkCommandBuffer)cmd,
                                     VK_PIPELINE_STAGE_TRANSFER_BIT,
                                     VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
                                     0,
                                     1, &memory_barrier,
                                     0, NULL,
                                     1, &image_barrier);
                rc = expect_clean_failure(cmd, "image-barrier-cross-queue-family", 3);
                if (rc) return rc;

                VkMemoryBarrier2 memory_barrier2;
                memset(&memory_barrier2, 0, sizeof(memory_barrier2));
                memory_barrier2.sType = VK_STRUCTURE_TYPE_MEMORY_BARRIER_2;
                memory_barrier2.srcStageMask = VK_PIPELINE_STAGE_2_TRANSFER_BIT;
                memory_barrier2.srcAccessMask = VK_ACCESS_2_TRANSFER_WRITE_BIT;
                memory_barrier2.dstStageMask = VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT;
                memory_barrier2.dstAccessMask = VK_ACCESS_2_SHADER_READ_BIT;

                VkBufferMemoryBarrier2 buffer_barrier2;
                memset(&buffer_barrier2, 0, sizeof(buffer_barrier2));
                buffer_barrier2.sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER_2;
                buffer_barrier2.srcStageMask = VK_PIPELINE_STAGE_2_TRANSFER_BIT;
                buffer_barrier2.srcAccessMask = VK_ACCESS_2_TRANSFER_WRITE_BIT;
                buffer_barrier2.dstStageMask = VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT;
                buffer_barrier2.dstAccessMask = VK_ACCESS_2_SHADER_READ_BIT;
                buffer_barrier2.srcQueueFamilyIndex = 0;
                buffer_barrier2.dstQueueFamilyIndex = 1;
                buffer_barrier2.buffer = pdocker_vk_buffer_to_handle(buffer);
                buffer_barrier2.offset = 0;
                buffer_barrier2.size = 64;

                VkDependencyInfo dependency;
                memset(&dependency, 0, sizeof(dependency));
                dependency.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO;
                dependency.memoryBarrierCount = 1;
                dependency.pMemoryBarriers = &memory_barrier2;
                dependency.bufferMemoryBarrierCount = 1;
                dependency.pBufferMemoryBarriers = &buffer_barrier2;

                reset_cmd(cmd, 1);
                vkCmdPipelineBarrier2((VkCommandBuffer)cmd, &dependency);
                rc = expect_clean_failure(cmd, "buffer-barrier-cross-queue-family", 4);
                if (rc) return rc;

                VkImageMemoryBarrier2 image_barrier2;
                memset(&image_barrier2, 0, sizeof(image_barrier2));
                image_barrier2.sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER_2;
                image_barrier2.srcStageMask = VK_PIPELINE_STAGE_2_TRANSFER_BIT;
                image_barrier2.srcAccessMask = VK_ACCESS_2_TRANSFER_WRITE_BIT;
                image_barrier2.dstStageMask = VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT;
                image_barrier2.dstAccessMask = VK_ACCESS_2_SHADER_READ_BIT;
                image_barrier2.oldLayout = VK_IMAGE_LAYOUT_UNDEFINED;
                image_barrier2.newLayout = VK_IMAGE_LAYOUT_GENERAL;
                image_barrier2.srcQueueFamilyIndex = 0;
                image_barrier2.dstQueueFamilyIndex = 1;
                image_barrier2.image = pdocker_vk_image_to_handle(image);
                image_barrier2.subresourceRange = image_barrier.subresourceRange;

                memset(&dependency, 0, sizeof(dependency));
                dependency.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO;
                dependency.memoryBarrierCount = 1;
                dependency.pMemoryBarriers = &memory_barrier2;
                dependency.imageMemoryBarrierCount = 1;
                dependency.pImageMemoryBarriers = &image_barrier2;

                reset_cmd(cmd, 1);
                vkCmdPipelineBarrier2((VkCommandBuffer)cmd, &dependency);
                rc = expect_clean_failure(cmd, "image-barrier-cross-queue-family", 5);
                if (rc) return rc;

                buffer->owner_device_id = 0x202u;
                image->owner_device_id = 0x202u;
                buffer_barrier2.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
                buffer_barrier2.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
                memset(&dependency, 0, sizeof(dependency));
                dependency.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO;
                dependency.bufferMemoryBarrierCount = 1;
                dependency.pBufferMemoryBarriers = &buffer_barrier2;

                reset_cmd(cmd, 1);
                cmd->owner_device_id = 0x101u;
                vkCmdPipelineBarrier2((VkCommandBuffer)cmd, &dependency);
                rc = expect_clean_failure(cmd, "buffer-barrier-invalid-handle", 6);
                if (rc) return rc;

                image_barrier2.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
                image_barrier2.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
                memset(&dependency, 0, sizeof(dependency));
                dependency.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO;
                dependency.imageMemoryBarrierCount = 1;
                dependency.pImageMemoryBarriers = &image_barrier2;

                reset_cmd(cmd, 1);
                cmd->owner_device_id = 0x101u;
                vkCmdPipelineBarrier2((VkCommandBuffer)cmd, &dependency);
                rc = expect_clean_failure(cmd, "image-barrier-invalid-handle", 7);
                if (rc) return rc;

                free(image);
                free(buffer);
                free(cmd);
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)



    def test_event_barrier_cross_queue_family_fails_before_partial_recording(self):
        source = textwrap.dedent(
            rf"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include <stdlib.h>
            #include "{ICD_SOURCE}"
            {COMMAND_BUFFER_LIVE_REGISTRY_HELPER}

            static void reset_cmd(PdockerVkCommandBuffer *cmd, int sync2) {{
                memset(cmd, 0, sizeof(*cmd));
                if (sync2) {{
                    cmd->requested_feature_mask = PDOCKER_VK_FEATURE_SYNCHRONIZATION_2;
                    cmd->enabled_extension_mask = PDOCKER_VK_DEVICE_EXT_KHR_SYNCHRONIZATION_2;
                }}
            }}

            static void init_image(PdockerVkImage *image) {{
                memset(image, 0, sizeof(*image));
                image->object_id = 0xabcdu;
                image->format = VK_FORMAT_R8G8B8A8_UNORM;
                image->image_type = VK_IMAGE_TYPE_2D;
                image->extent.width = 16;
                image->extent.height = 16;
                image->extent.depth = 1;
                image->mip_levels = 1;
                image->array_layers = 1;
                image->samples = VK_SAMPLE_COUNT_1_BIT;
                image->usage = VK_IMAGE_USAGE_TRANSFER_SRC_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT;
                image->current_layout = VK_IMAGE_LAYOUT_UNDEFINED;
                image->layout_generation = 1;
                image_register(image);
            }}

            static int expect_clean_failure(PdockerVkCommandBuffer *cmd, const char *reason, int code) {{
                if (!cmd->recording_failed || !cmd->recording_failure_reason ||
                    strcmp(cmd->recording_failure_reason, reason) != 0) {{
                    fprintf(stderr, "case %d unexpected failure got=%s want=%s failed=%d\n",
                            code,
                            cmd->recording_failure_reason ? cmd->recording_failure_reason : "<null>",
                            reason,
                            cmd->recording_failed ? 1 : 0);
                    return code;
                }}
                if (cmd->memory_barrier_op_count != 0 || cmd->buffer_barrier_op_count != 0 ||
                    cmd->image_barrier_op_count != 0 || cmd->event_wait_ref_count != 0 ||
                    cmd->command_op_count != 0 || cmd->graphics_command_op_count != 0) {{
                    fprintf(stderr, "case %d partial state mem=%u buf=%u img=%u refs=%u cmd=%u gfx=%u\n",
                            code,
                            cmd->memory_barrier_op_count,
                            cmd->buffer_barrier_op_count,
                            cmd->image_barrier_op_count,
                            cmd->event_wait_ref_count,
                            cmd->command_op_count,
                            cmd->graphics_command_op_count);
                    return code + 100;
                }}
                if (vkEndCommandBuffer((VkCommandBuffer)cmd) != VK_ERROR_FEATURE_NOT_PRESENT) {{
                    fprintf(stderr, "case %d did not fail closed at end\n", code);
                    return code + 200;
                }}
                return 0;
            }}

            int main(void) {{
                PdockerVkCommandBuffer *cmd = sync_test_command_buffer_alloc();
                PdockerVkBuffer *buffer = (PdockerVkBuffer *)calloc(1, sizeof(*buffer));
                PdockerVkImage *image = (PdockerVkImage *)calloc(1, sizeof(*image));
                if (!cmd || !buffer || !image) return 99;
                buffer->object_id = 0x1234u;
                buffer->size = 4096u;
                buffer_register(buffer);
                init_image(image);

                VkEventCreateInfo event_info;
                memset(&event_info, 0, sizeof(event_info));
                event_info.sType = VK_STRUCTURE_TYPE_EVENT_CREATE_INFO;
                VkEvent event = VK_NULL_HANDLE;
                if (vkCreateEvent(VK_NULL_HANDLE, &event_info, NULL, &event) != VK_SUCCESS || !event) {{
                    fprintf(stderr, "event creation failed\n");
                    return 98;
                }}
                VkEvent events[1] = {{ event }};

                VkMemoryBarrier memory_barrier;
                memset(&memory_barrier, 0, sizeof(memory_barrier));
                memory_barrier.sType = VK_STRUCTURE_TYPE_MEMORY_BARRIER;
                memory_barrier.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
                memory_barrier.dstAccessMask = VK_ACCESS_SHADER_READ_BIT;

                VkBufferMemoryBarrier buffer_barrier;
                memset(&buffer_barrier, 0, sizeof(buffer_barrier));
                buffer_barrier.sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER;
                buffer_barrier.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
                buffer_barrier.dstAccessMask = VK_ACCESS_SHADER_READ_BIT;
                buffer_barrier.srcQueueFamilyIndex = 0;
                buffer_barrier.dstQueueFamilyIndex = 1;
                buffer_barrier.buffer = pdocker_vk_buffer_to_handle(buffer);
                buffer_barrier.offset = 0;
                buffer_barrier.size = 64;

                VkImageMemoryBarrier image_barrier;
                memset(&image_barrier, 0, sizeof(image_barrier));
                image_barrier.sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER;
                image_barrier.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
                image_barrier.dstAccessMask = VK_ACCESS_SHADER_READ_BIT;
                image_barrier.oldLayout = VK_IMAGE_LAYOUT_UNDEFINED;
                image_barrier.newLayout = VK_IMAGE_LAYOUT_GENERAL;
                image_barrier.srcQueueFamilyIndex = 0;
                image_barrier.dstQueueFamilyIndex = 1;
                image_barrier.image = pdocker_vk_image_to_handle(image);
                image_barrier.subresourceRange.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
                image_barrier.subresourceRange.baseMipLevel = 0;
                image_barrier.subresourceRange.levelCount = 1;
                image_barrier.subresourceRange.baseArrayLayer = 0;
                image_barrier.subresourceRange.layerCount = 1;

                reset_cmd(cmd, 0);
                vkCmdWaitEvents((VkCommandBuffer)cmd, 1, events,
                                VK_PIPELINE_STAGE_TRANSFER_BIT,
                                VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
                                1, &memory_barrier,
                                1, &buffer_barrier,
                                0, NULL);
                int rc = expect_clean_failure(cmd, "buffer-barrier-cross-queue-family", 2);
                if (rc) return rc;

                reset_cmd(cmd, 0);
                vkCmdWaitEvents((VkCommandBuffer)cmd, 1, events,
                                VK_PIPELINE_STAGE_TRANSFER_BIT,
                                VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
                                1, &memory_barrier,
                                0, NULL,
                                1, &image_barrier);
                rc = expect_clean_failure(cmd, "image-barrier-cross-queue-family", 3);
                if (rc) return rc;

                VkMemoryBarrier2 memory_barrier2;
                memset(&memory_barrier2, 0, sizeof(memory_barrier2));
                memory_barrier2.sType = VK_STRUCTURE_TYPE_MEMORY_BARRIER_2;
                memory_barrier2.srcStageMask = VK_PIPELINE_STAGE_2_TRANSFER_BIT;
                memory_barrier2.srcAccessMask = VK_ACCESS_2_TRANSFER_WRITE_BIT;
                memory_barrier2.dstStageMask = VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT;
                memory_barrier2.dstAccessMask = VK_ACCESS_2_SHADER_READ_BIT;

                VkBufferMemoryBarrier2 buffer_barrier2;
                memset(&buffer_barrier2, 0, sizeof(buffer_barrier2));
                buffer_barrier2.sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER_2;
                buffer_barrier2.srcStageMask = VK_PIPELINE_STAGE_2_TRANSFER_BIT;
                buffer_barrier2.srcAccessMask = VK_ACCESS_2_TRANSFER_WRITE_BIT;
                buffer_barrier2.dstStageMask = VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT;
                buffer_barrier2.dstAccessMask = VK_ACCESS_2_SHADER_READ_BIT;
                buffer_barrier2.srcQueueFamilyIndex = 0;
                buffer_barrier2.dstQueueFamilyIndex = 1;
                buffer_barrier2.buffer = pdocker_vk_buffer_to_handle(buffer);
                buffer_barrier2.offset = 0;
                buffer_barrier2.size = 64;

                VkImageMemoryBarrier2 image_barrier2;
                memset(&image_barrier2, 0, sizeof(image_barrier2));
                image_barrier2.sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER_2;
                image_barrier2.srcStageMask = VK_PIPELINE_STAGE_2_TRANSFER_BIT;
                image_barrier2.srcAccessMask = VK_ACCESS_2_TRANSFER_WRITE_BIT;
                image_barrier2.dstStageMask = VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT;
                image_barrier2.dstAccessMask = VK_ACCESS_2_SHADER_READ_BIT;
                image_barrier2.oldLayout = VK_IMAGE_LAYOUT_UNDEFINED;
                image_barrier2.newLayout = VK_IMAGE_LAYOUT_GENERAL;
                image_barrier2.srcQueueFamilyIndex = 0;
                image_barrier2.dstQueueFamilyIndex = 1;
                image_barrier2.image = pdocker_vk_image_to_handle(image);
                image_barrier2.subresourceRange = image_barrier.subresourceRange;

                VkDependencyInfo dependency;
                memset(&dependency, 0, sizeof(dependency));
                dependency.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO;
                dependency.memoryBarrierCount = 1;
                dependency.pMemoryBarriers = &memory_barrier2;
                dependency.bufferMemoryBarrierCount = 1;
                dependency.pBufferMemoryBarriers = &buffer_barrier2;

                reset_cmd(cmd, 1);
                vkCmdSetEvent2((VkCommandBuffer)cmd, event, &dependency);
                rc = expect_clean_failure(cmd, "buffer-barrier-cross-queue-family", 4);
                if (rc) return rc;

                reset_cmd(cmd, 1);
                vkCmdWaitEvents2((VkCommandBuffer)cmd, 1, events, &dependency);
                rc = expect_clean_failure(cmd, "buffer-barrier-cross-queue-family", 5);
                if (rc) return rc;

                memset(&dependency, 0, sizeof(dependency));
                dependency.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO;
                dependency.memoryBarrierCount = 1;
                dependency.pMemoryBarriers = &memory_barrier2;
                dependency.imageMemoryBarrierCount = 1;
                dependency.pImageMemoryBarriers = &image_barrier2;

                reset_cmd(cmd, 1);
                vkCmdSetEvent2((VkCommandBuffer)cmd, event, &dependency);
                rc = expect_clean_failure(cmd, "image-barrier-cross-queue-family", 6);
                if (rc) return rc;

                reset_cmd(cmd, 1);
                vkCmdWaitEvents2((VkCommandBuffer)cmd, 1, events, &dependency);
                rc = expect_clean_failure(cmd, "image-barrier-cross-queue-family", 7);
                if (rc) return rc;

                buffer->owner_device_id = 0x202u;
                image->owner_device_id = 0x202u;
                buffer_barrier2.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
                buffer_barrier2.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
                memset(&dependency, 0, sizeof(dependency));
                dependency.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO;
                dependency.bufferMemoryBarrierCount = 1;
                dependency.pBufferMemoryBarriers = &buffer_barrier2;

                reset_cmd(cmd, 1);
                cmd->owner_device_id = 0x101u;
                vkCmdSetEvent2((VkCommandBuffer)cmd, event, &dependency);
                rc = expect_clean_failure(cmd, "buffer-barrier-invalid-handle", 8);
                if (rc) return rc;

                reset_cmd(cmd, 1);
                cmd->owner_device_id = 0x101u;
                vkCmdWaitEvents2((VkCommandBuffer)cmd, 1, events, &dependency);
                rc = expect_clean_failure(cmd, "buffer-barrier-invalid-handle", 9);
                if (rc) return rc;

                image_barrier2.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
                image_barrier2.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
                memset(&dependency, 0, sizeof(dependency));
                dependency.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO;
                dependency.imageMemoryBarrierCount = 1;
                dependency.pImageMemoryBarriers = &image_barrier2;

                reset_cmd(cmd, 1);
                cmd->owner_device_id = 0x101u;
                vkCmdSetEvent2((VkCommandBuffer)cmd, event, &dependency);
                rc = expect_clean_failure(cmd, "image-barrier-invalid-handle", 10);
                if (rc) return rc;

                reset_cmd(cmd, 1);
                cmd->owner_device_id = 0x101u;
                vkCmdWaitEvents2((VkCommandBuffer)cmd, 1, events, &dependency);
                rc = expect_clean_failure(cmd, "image-barrier-invalid-handle", 11);
                if (rc) return rc;

                vkDestroyEvent(VK_NULL_HANDLE, event, NULL);
                free(image);
                free(buffer);
                free(cmd);
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


    def test_external_memory_acquire_unmodified_buffer_barrier_pnext_records_cleanly(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include <stdlib.h>
            #include "{ICD_SOURCE}"
            {COMMAND_BUFFER_LIVE_REGISTRY_HELPER}

            int main(void) {{
            #ifndef VK_EXT_external_memory_acquire_unmodified
                return 0;
            #else
                PdockerVkCommandBuffer *cmd = sync_test_command_buffer_alloc();
                PdockerVkBuffer *buffer = (PdockerVkBuffer *)calloc(1, sizeof(*buffer));
                if (!cmd || !buffer) return 9;
                cmd->requested_feature_mask = PDOCKER_VK_FEATURE_SYNCHRONIZATION_2;
                cmd->enabled_extension_mask = PDOCKER_VK_DEVICE_EXT_KHR_SYNCHRONIZATION_2;
                buffer->object_id = 0x1234u;
                buffer->size = 4096u;
                buffer_register(buffer);

                VkExternalMemoryAcquireUnmodifiedEXT acquire;
                memset(&acquire, 0, sizeof(acquire));
                acquire.sType = VK_STRUCTURE_TYPE_EXTERNAL_MEMORY_ACQUIRE_UNMODIFIED_EXT;
                acquire.acquireUnmodifiedMemory = VK_TRUE;

                VkBufferMemoryBarrier2 buffer_barrier;
                memset(&buffer_barrier, 0, sizeof(buffer_barrier));
                buffer_barrier.sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER_2;
                buffer_barrier.pNext = &acquire;
                buffer_barrier.srcStageMask = VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT;
                buffer_barrier.srcAccessMask = VK_ACCESS_2_SHADER_WRITE_BIT;
                buffer_barrier.dstStageMask = VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT;
                buffer_barrier.dstAccessMask = VK_ACCESS_2_SHADER_READ_BIT;
                buffer_barrier.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
                buffer_barrier.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
                buffer_barrier.buffer = pdocker_vk_buffer_to_handle(buffer);
                buffer_barrier.offset = 16u;
                buffer_barrier.size = 32u;

                VkDependencyInfo dependency;
                memset(&dependency, 0, sizeof(dependency));
                dependency.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO;
                dependency.bufferMemoryBarrierCount = 1;
                dependency.pBufferMemoryBarriers = &buffer_barrier;

                vkCmdPipelineBarrier2((VkCommandBuffer)cmd, &dependency);
                if (cmd->recording_failed || cmd->graphics_unsupported) {{
                    fprintf(stderr, "external acquire pNext caused failure reason=%s\\n",
                            cmd->recording_failure_reason ? cmd->recording_failure_reason : "<null>");
                    return 2;
                }}
                if (cmd->command_op_count != 1 || cmd->graphics_command_op_count != 1 ||
                    cmd->buffer_barrier_op_count != 1) {{
                    fprintf(stderr, "barrier was not recorded ops=%u graphics=%u buf=%u\\n",
                            cmd->command_op_count,
                            cmd->graphics_command_op_count,
                            cmd->buffer_barrier_op_count);
                    return 3;
                }}
                if (cmd->buffer_barrier_ops[0].buffer != buffer ||
                    cmd->buffer_barrier_ops[0].offset != 16u ||
                    cmd->buffer_barrier_ops[0].size != 32u) {{
                    fprintf(stderr, "buffer barrier payload was not preserved\\n");
                    return 4;
                }}
                if (vkEndCommandBuffer((VkCommandBuffer)cmd) != VK_SUCCESS) {{
                    fprintf(stderr, "recorded barrier failed at end\\n");
                    return 5;
                }}
                free(buffer);
                free(cmd);
                return 0;
            #endif
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


    def test_bind_memory2_rejects_null_arrays_and_unsupported_pnext(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"
            {COMMAND_BUFFER_LIVE_REGISTRY_HELPER}

            typedef struct DummyPnext {{
                VkStructureType sType;
                const void *pNext;
            }} DummyPnext;

            int main(void) {{
                if (vkBindBufferMemory2(VK_NULL_HANDLE, 0, NULL) != VK_SUCCESS) {{
                    fprintf(stderr, "zero-count buffer bind should accept null array\\n");
                    return 2;
                }}
                if (vkBindImageMemory2(VK_NULL_HANDLE, 0, NULL) != VK_SUCCESS) {{
                    fprintf(stderr, "zero-count image bind should accept null array\\n");
                    return 3;
                }}
                if (vkBindBufferMemory2(VK_NULL_HANDLE, 1, NULL) != VK_ERROR_INITIALIZATION_FAILED) {{
                    fprintf(stderr, "buffer bind did not reject missing array\\n");
                    return 4;
                }}
                if (vkBindImageMemory2(VK_NULL_HANDLE, 1, NULL) != VK_ERROR_INITIALIZATION_FAILED) {{
                    fprintf(stderr, "image bind did not reject missing array\\n");
                    return 5;
                }}

                DummyPnext unsupported;
                memset(&unsupported, 0, sizeof(unsupported));
                unsupported.sType = (VkStructureType)1000060013;

                VkBindBufferMemoryInfo buffer_info;
                memset(&buffer_info, 0, sizeof(buffer_info));
                buffer_info.sType = VK_STRUCTURE_TYPE_BIND_BUFFER_MEMORY_INFO;
                buffer_info.pNext = &unsupported;
                if (vkBindBufferMemory2(VK_NULL_HANDLE, 1, &buffer_info) != VK_ERROR_FEATURE_NOT_PRESENT) {{
                    fprintf(stderr, "buffer bind did not fail closed on unsupported pNext\\n");
                    return 6;
                }}

                VkBindImageMemoryInfo image_info;
                memset(&image_info, 0, sizeof(image_info));
                image_info.sType = VK_STRUCTURE_TYPE_BIND_IMAGE_MEMORY_INFO;
                image_info.pNext = &unsupported;
                if (vkBindImageMemory2(VK_NULL_HANDLE, 1, &image_info) != VK_ERROR_FEATURE_NOT_PRESENT) {{
                    fprintf(stderr, "image bind did not fail closed on unsupported pNext\\n");
                    return 7;
                }}
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


    def test_dual_aspect_depth_stencil_image_copy_split_helper(self):
        source = textwrap.dedent("""
            #include <errno.h>
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "__ICD_SOURCE__"

            static void init_image(PdockerVkImage *image, VkFormat format) {
                memset(image, 0, sizeof(*image));
                image->format = format;
                image->mip_levels = 1;
                image->array_layers = 1;
            }

            static int expect_split(const char *label, PdockerVkImageToImageCopyOp *copy,
                                    int want_rc, uint32_t want_count,
                                    VkImageAspectFlags a0, VkImageAspectFlags a1) {
                VkImageAspectFlags aspects[2] = {0, 0};
                uint32_t count = 99;
                int rc = pdocker_vk_image_to_image_copy_split_aspects(copy, aspects, &count);
                if (rc != want_rc || count != want_count || aspects[0] != a0 || aspects[1] != a1) {
                    fprintf(stderr, "%s rc=%d count=%u aspects=0x%x,0x%x want rc=%d count=%u aspects=0x%x,0x%x\\n",
                            label, rc, count, aspects[0], aspects[1], want_rc, want_count, a0, a1);
                    return 0;
                }
                return 1;
            }

            int main(void) {
                PdockerVkImage src;
                PdockerVkImage dst;
                PdockerVkImageToImageCopyOp copy;
                memset(&copy, 0, sizeof(copy));
                copy.src = &src;
                copy.dst = &dst;

                init_image(&src, VK_FORMAT_R8G8B8A8_UNORM);
                init_image(&dst, VK_FORMAT_R8G8B8A8_UNORM);
                copy.region.srcSubresource.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
                copy.region.dstSubresource.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
                if (!expect_split("color single aspect", &copy, 0, 1,
                                  VK_IMAGE_ASPECT_COLOR_BIT, 0)) return 2;

                init_image(&src, VK_FORMAT_D24_UNORM_S8_UINT);
                init_image(&dst, VK_FORMAT_D24_UNORM_S8_UINT);
                copy.region.srcSubresource.aspectMask = VK_IMAGE_ASPECT_DEPTH_BIT | VK_IMAGE_ASPECT_STENCIL_BIT;
                copy.region.dstSubresource.aspectMask = VK_IMAGE_ASPECT_DEPTH_BIT | VK_IMAGE_ASPECT_STENCIL_BIT;
                if (!expect_split("packed depth/stencil dual aspect", &copy, 0, 2,
                                  VK_IMAGE_ASPECT_DEPTH_BIT, VK_IMAGE_ASPECT_STENCIL_BIT)) return 3;

                copy.region.dstSubresource.aspectMask = VK_IMAGE_ASPECT_DEPTH_BIT;
                if (!expect_split("mismatched aspect masks", &copy, -EOPNOTSUPP, 0, 0, 0)) return 4;

                init_image(&dst, VK_FORMAT_D32_SFLOAT);
                copy.region.dstSubresource.aspectMask = VK_IMAGE_ASPECT_DEPTH_BIT | VK_IMAGE_ASPECT_STENCIL_BIT;
                if (!expect_split("incompatible depth/stencil formats", &copy, -EOPNOTSUPP, 0, 0, 0)) return 5;

                if (pdocker_vk_image_to_image_copy_split_aspects(NULL, NULL, NULL) != -EINVAL) return 6;
                return 0;
            }
            """.replace("__ICD_SOURCE__", str(ICD_SOURCE)))
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_msaa_resolve_attachment_helper_validates_samples_and_aspects(self):
        source = textwrap.dedent("""
            #include <errno.h>
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "__ICD_SOURCE__"

            static void init_snapshot(PdockerVkRenderingAttachmentState *state,
                                      VkSampleCountFlagBits src_samples,
                                      VkSampleCountFlagBits resolve_samples,
                                      VkImageAspectFlags src_aspect,
                                      VkImageAspectFlags resolve_aspect) {
                memset(state, 0, sizeof(*state));
                static PdockerVkImageView src_view;
                static PdockerVkImageView resolve_view;
                state->image_view = &src_view;
                state->resolve_image_view = &resolve_view;
                state->resolve_mode = VK_RESOLVE_MODE_AVERAGE_BIT;
                state->image_view_snapshot.valid = true;
                state->image_view_snapshot.format = VK_FORMAT_R8G8B8A8_UNORM;
                state->image_view_snapshot.samples = src_samples;
                state->image_view_snapshot.subresource_range.aspectMask = src_aspect;
                state->resolve_image_view_snapshot.valid = true;
                state->resolve_image_view_snapshot.format = VK_FORMAT_R8G8B8A8_UNORM;
                state->resolve_image_view_snapshot.samples = resolve_samples;
                state->resolve_image_view_snapshot.subresource_range.aspectMask = resolve_aspect;
            }

            static int expect_rc(const char *label, PdockerVkRenderingAttachmentState *state, int expected) {
                int rc = pdocker_vk_rendering_resolve_attachment_supported(state, VK_FORMAT_R8G8B8A8_UNORM);
                if (rc != expected) {
                    fprintf(stderr, "%s rc=%d expected=%d\\n", label, rc, expected);
                    return 0;
                }
                return 1;
            }

            int main(void) {
                PdockerVkRenderingAttachmentState state;
                init_snapshot(&state, VK_SAMPLE_COUNT_4_BIT, VK_SAMPLE_COUNT_1_BIT,
                              VK_IMAGE_ASPECT_COLOR_BIT, VK_IMAGE_ASPECT_COLOR_BIT);
                if (!expect_rc("valid-msaa-resolve", &state, 0)) return 2;
                init_snapshot(&state, VK_SAMPLE_COUNT_1_BIT, VK_SAMPLE_COUNT_1_BIT,
                              VK_IMAGE_ASPECT_COLOR_BIT, VK_IMAGE_ASPECT_COLOR_BIT);
                if (!expect_rc("single-sample-source", &state, -EOPNOTSUPP)) return 3;
                init_snapshot(&state, VK_SAMPLE_COUNT_4_BIT, VK_SAMPLE_COUNT_4_BIT,
                              VK_IMAGE_ASPECT_COLOR_BIT, VK_IMAGE_ASPECT_COLOR_BIT);
                if (!expect_rc("msaa-resolve-target", &state, -EOPNOTSUPP)) return 4;
                init_snapshot(&state, VK_SAMPLE_COUNT_4_BIT, VK_SAMPLE_COUNT_1_BIT,
                              VK_IMAGE_ASPECT_COLOR_BIT, VK_IMAGE_ASPECT_DEPTH_BIT);
                if (!expect_rc("resolve-aspect-mismatch", &state, -EOPNOTSUPP)) return 5;
                state.resolve_image_view = NULL;
                state.resolve_mode = VK_RESOLVE_MODE_AVERAGE_BIT;
                if (!expect_rc("resolve-mode-without-view", &state, -EOPNOTSUPP)) return 6;
                state.resolve_mode = VK_RESOLVE_MODE_NONE;
                if (!expect_rc("no-resolve", &state, 0)) return 7;
                if (pdocker_vk_rendering_resolve_attachment_supported(NULL, VK_FORMAT_R8G8B8A8_UNORM) != -EINVAL) return 8;
                return 0;
            }
            """.replace("__ICD_SOURCE__", str(ICD_SOURCE)))
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_image_layout_range_cache_splits_partial_overlaps_without_overflow(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"
            {COMMAND_BUFFER_LIVE_REGISTRY_HELPER}

            static int find_range(PdockerVkImage *image,
                                  VkImageLayout layout,
                                  uint32_t base_mip,
                                  uint32_t level_count,
                                  uint32_t base_layer,
                                  uint32_t layer_count) {{
                for (uint32_t i = 0; i < image->layout_range_count; ++i) {{
                    PdockerVkImageLayoutRange *entry = &image->layout_ranges[i];
                    if (entry->layout == layout &&
                        entry->range.aspectMask == VK_IMAGE_ASPECT_COLOR_BIT &&
                        entry->range.baseMipLevel == base_mip &&
                        entry->range.levelCount == level_count &&
                        entry->range.baseArrayLayer == base_layer &&
                        entry->range.layerCount == layer_count) {{
                        return 1;
                    }}
                }}
                return 0;
            }}

            static int find_range_aspect(PdockerVkImage *image,
                                         VkImageLayout layout,
                                         VkImageAspectFlags aspect_mask,
                                         uint32_t base_mip,
                                         uint32_t level_count,
                                         uint32_t base_layer,
                                         uint32_t layer_count) {{
                for (uint32_t i = 0; i < image->layout_range_count; ++i) {{
                    PdockerVkImageLayoutRange *entry = &image->layout_ranges[i];
                    if (entry->layout == layout &&
                        entry->range.aspectMask == aspect_mask &&
                        entry->range.baseMipLevel == base_mip &&
                        entry->range.levelCount == level_count &&
                        entry->range.baseArrayLayer == base_layer &&
                        entry->range.layerCount == layer_count) {{
                        return 1;
                    }}
                }}
                return 0;
            }}

            int main(void) {{
                PdockerVkImage image;
                memset(&image, 0, sizeof(image));
                image.format = VK_FORMAT_R8G8B8A8_UNORM;
                image.mip_levels = 4;
                image.array_layers = 4;
                image.layout_generation = 1;

                VkImageSubresourceRange full = {{
                    .aspectMask = VK_IMAGE_ASPECT_COLOR_BIT,
                    .baseMipLevel = 0,
                    .levelCount = 4,
                    .baseArrayLayer = 0,
                    .layerCount = 4,
                }};
                update_image_layout_range_cache(&image, &full, VK_IMAGE_LAYOUT_GENERAL);
                if (image.layout_range_overflow || image.layout_range_count != 1) {{
                    fprintf(stderr, "initial full-image range was not cached count=%u overflow=%d\\n",
                            image.layout_range_count, image.layout_range_overflow ? 1 : 0);
                    return 2;
                }}

                image.layout_generation = 2;
                VkImageSubresourceRange center = {{
                    .aspectMask = VK_IMAGE_ASPECT_COLOR_BIT,
                    .baseMipLevel = 1,
                    .levelCount = 2,
                    .baseArrayLayer = 1,
                    .layerCount = 2,
                }};
                update_image_layout_range_cache(&image, &center, VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL);
                if (image.layout_range_overflow) {{
                    fprintf(stderr, "partial overlap incorrectly overflowed\\n");
                    return 3;
                }}
                if (!find_range(&image, VK_IMAGE_LAYOUT_GENERAL, 0, 1, 0, 4) ||
                    !find_range(&image, VK_IMAGE_LAYOUT_GENERAL, 3, 1, 0, 4) ||
                    !find_range(&image, VK_IMAGE_LAYOUT_GENERAL, 1, 2, 0, 1) ||
                    !find_range(&image, VK_IMAGE_LAYOUT_GENERAL, 1, 2, 3, 1) ||
                    !find_range(&image, VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL, 1, 2, 1, 2)) {{
                    fprintf(stderr, "split range table missing expected remainders count=%u\\n",
                            image.layout_range_count);
                    return 4;
                }}

                image.layout_generation = 3;
                update_image_layout_range_cache(&image, &center, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL);
                if (image.layout_range_overflow ||
                    !find_range(&image, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, 1, 2, 1, 2) ||
                    find_range(&image, VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL, 1, 2, 1, 2)) {{
                    fprintf(stderr, "exact replacement did not update center range count=%u overflow=%d\\n",
                            image.layout_range_count, image.layout_range_overflow ? 1 : 0);
                    return 5;
                }}

                memset(&image, 0, sizeof(image));
                image.format = VK_FORMAT_D24_UNORM_S8_UINT;
                image.mip_levels = 2;
                image.array_layers = 2;
                image.layout_generation = 10;

                VkImageSubresourceRange ds_full = {{
                    .aspectMask = VK_IMAGE_ASPECT_DEPTH_BIT | VK_IMAGE_ASPECT_STENCIL_BIT,
                    .baseMipLevel = 0,
                    .levelCount = 2,
                    .baseArrayLayer = 0,
                    .layerCount = 2,
                }};
                update_image_layout_range_cache(&image, &ds_full, VK_IMAGE_LAYOUT_GENERAL);
                if (image.layout_range_overflow || image.layout_range_count != 1 ||
                    !find_range_aspect(&image, VK_IMAGE_LAYOUT_GENERAL,
                                       VK_IMAGE_ASPECT_DEPTH_BIT | VK_IMAGE_ASPECT_STENCIL_BIT, 0, 2, 0, 2)) {{
                    fprintf(stderr, "initial dual-aspect range was not cached count=%u overflow=%d\\n",
                            image.layout_range_count, image.layout_range_overflow ? 1 : 0);
                    return 6;
                }}

                image.layout_generation = 11;
                VkImageSubresourceRange depth_first_layer = {{
                    .aspectMask = VK_IMAGE_ASPECT_DEPTH_BIT,
                    .baseMipLevel = 0,
                    .levelCount = 2,
                    .baseArrayLayer = 0,
                    .layerCount = 1,
                }};
                update_image_layout_range_cache(&image, &depth_first_layer, VK_IMAGE_LAYOUT_DEPTH_READ_ONLY_OPTIMAL);
                if (image.layout_range_overflow ||
                    !find_range_aspect(&image, VK_IMAGE_LAYOUT_GENERAL, VK_IMAGE_ASPECT_STENCIL_BIT, 0, 2, 0, 2) ||
                    !find_range_aspect(&image, VK_IMAGE_LAYOUT_GENERAL, VK_IMAGE_ASPECT_DEPTH_BIT, 0, 2, 1, 1) ||
                    !find_range_aspect(&image, VK_IMAGE_LAYOUT_DEPTH_READ_ONLY_OPTIMAL, VK_IMAGE_ASPECT_DEPTH_BIT, 0, 2, 0, 1)) {{
                    fprintf(stderr, "dual-aspect depth split missing expected remainders count=%u overflow=%d\\n",
                            image.layout_range_count, image.layout_range_overflow ? 1 : 0);
                    return 7;
                }}

                image.layout_generation = 12;
                VkImageSubresourceRange stencil_full = {{
                    .aspectMask = VK_IMAGE_ASPECT_STENCIL_BIT,
                    .baseMipLevel = 0,
                    .levelCount = 2,
                    .baseArrayLayer = 0,
                    .layerCount = 2,
                }};
                update_image_layout_range_cache(&image, &stencil_full, VK_IMAGE_LAYOUT_STENCIL_READ_ONLY_OPTIMAL);
                if (image.layout_range_overflow ||
                    !find_range_aspect(&image, VK_IMAGE_LAYOUT_STENCIL_READ_ONLY_OPTIMAL, VK_IMAGE_ASPECT_STENCIL_BIT, 0, 2, 0, 2) ||
                    !find_range_aspect(&image, VK_IMAGE_LAYOUT_DEPTH_READ_ONLY_OPTIMAL, VK_IMAGE_ASPECT_DEPTH_BIT, 0, 2, 0, 1) ||
                    !find_range_aspect(&image, VK_IMAGE_LAYOUT_GENERAL, VK_IMAGE_ASPECT_DEPTH_BIT, 0, 2, 1, 1)) {{
                    fprintf(stderr, "dual-aspect stencil replacement clobbered depth ranges count=%u overflow=%d\\n",
                            image.layout_range_count, image.layout_range_overflow ? 1 : 0);
                    return 8;
                }}
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_compute_only_barrier_and_query_do_not_mark_command_buffer_as_graphics(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include <stdlib.h>
            #include "{ICD_SOURCE}"
            {COMMAND_BUFFER_LIVE_REGISTRY_HELPER}

            int main(void) {{
                PdockerVkCommandBuffer *cmd = sync_test_command_buffer_alloc();
                if (!cmd) return 9;
                cmd->requested_feature_mask = PDOCKER_VK_FEATURE_SYNCHRONIZATION_2;
                cmd->enabled_extension_mask = PDOCKER_VK_DEVICE_EXT_KHR_SYNCHRONIZATION_2;

                VkMemoryBarrier barrier;
                memset(&barrier, 0, sizeof(barrier));
                barrier.sType = VK_STRUCTURE_TYPE_MEMORY_BARRIER;
                barrier.srcAccessMask = VK_ACCESS_SHADER_WRITE_BIT;
                barrier.dstAccessMask = VK_ACCESS_SHADER_READ_BIT;
                vkCmdPipelineBarrier((VkCommandBuffer)cmd,
                                     VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
                                     VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
                                     0, 1, &barrier, 0, NULL, 0, NULL);
                if (command_buffer_needs_graphics_submit_sync_frame(cmd)) {{
                    fprintf(stderr, "compute-only barrier incorrectly requires graphics submit count=%u\\n",
                            cmd->graphics_command_op_count);
                    return 2;
                }}

                VkQueryPool query_pool = VK_NULL_HANDLE;
                VkQueryPoolCreateInfo query_info;
                memset(&query_info, 0, sizeof(query_info));
                query_info.sType = VK_STRUCTURE_TYPE_QUERY_POOL_CREATE_INFO;
                query_info.queryType = VK_QUERY_TYPE_TIMESTAMP;
                query_info.queryCount = 2;
                if (vkCreateQueryPool(VK_NULL_HANDLE, &query_info, NULL, &query_pool) != VK_SUCCESS || !query_pool) {{
                    fprintf(stderr, "query pool create failed\\n");
                    return 3;
                }}
                vkCmdWriteTimestamp((VkCommandBuffer)cmd, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, query_pool, 0);
                if (command_buffer_needs_graphics_submit_sync_frame(cmd)) {{
                    fprintf(stderr, "compute-only query incorrectly requires graphics submit count=%u\\n",
                            cmd->graphics_command_op_count);
                    return 4;
                }}
                vkDestroyQueryPool(VK_NULL_HANDLE, query_pool, NULL);
                free(cmd);
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


    def test_binary_semaphore_queue_submit_signal_wait_state_machine_executes_c_code(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include <unistd.h>
            #include "{ICD_SOURCE}"
            {COMMAND_BUFFER_LIVE_REGISTRY_HELPER}

            int main(void) {{
                unsetenv("PDOCKER_GPU_QUEUE_SOCKET");
                ensure_vulkan_dispatchable_object_ids();
                g_queue.instance_object_id = 1;
                g_queue.physical_device_object_id = 1;
                g_queue.device_object_id = 1;
                VkSemaphore sem_a = VK_NULL_HANDLE;
                VkSemaphore sem_b = VK_NULL_HANDLE;
                VkSemaphoreCreateInfo sem_info;
                memset(&sem_info, 0, sizeof(sem_info));
                sem_info.sType = VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO;
                if (vkCreateSemaphore(VK_NULL_HANDLE, &sem_info, NULL, &sem_a) != VK_SUCCESS || !sem_a) return 2;
                if (vkCreateSemaphore(VK_NULL_HANDLE, &sem_info, NULL, &sem_b) != VK_SUCCESS || !sem_b) return 3;

                VkPipelineStageFlags wait_stage = VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT;
                VkSubmitInfo submit;
                memset(&submit, 0, sizeof(submit));
                submit.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO;
                submit.waitSemaphoreCount = 1;
                submit.pWaitSemaphores = &sem_a;
                submit.pWaitDstStageMask = &wait_stage;
                if (vkQueueSubmit((VkQueue)&g_queue, 1, &submit, VK_NULL_HANDLE) != VK_ERROR_FEATURE_NOT_PRESENT) {{
                    fprintf(stderr, "unsignaled binary wait did not fail closed\\n");
                    return 4;
                }}

                memset(&submit, 0, sizeof(submit));
                submit.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO;
                submit.signalSemaphoreCount = 1;
                submit.pSignalSemaphores = &sem_a;
                if (vkQueueSubmit((VkQueue)&g_queue, 1, &submit, VK_NULL_HANDLE) != VK_SUCCESS) {{
                    fprintf(stderr, "binary signal submit failed\\n");
                    return 5;
                }}
                if (!((PdockerVkSemaphore *)sem_a)->signaled) {{
                    fprintf(stderr, "binary signal did not update local state\\n");
                    return 6;
                }}

                VkFence fence = VK_NULL_HANDLE;
                VkFenceCreateInfo fence_info;
                memset(&fence_info, 0, sizeof(fence_info));
                fence_info.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO;
                if (vkCreateFence(VK_NULL_HANDLE, &fence_info, NULL, &fence) != VK_SUCCESS || !fence) return 7;

                memset(&submit, 0, sizeof(submit));
                submit.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO;
                submit.waitSemaphoreCount = 1;
                submit.pWaitSemaphores = &sem_a;
                submit.pWaitDstStageMask = &wait_stage;
                submit.signalSemaphoreCount = 1;
                submit.pSignalSemaphores = &sem_b;
                if (vkQueueSubmit((VkQueue)&g_queue, 1, &submit, fence) != VK_SUCCESS) {{
                    fprintf(stderr, "binary wait/signal submit failed\\n");
                    return 8;
                }}
                if (((PdockerVkSemaphore *)sem_a)->signaled) {{
                    fprintf(stderr, "binary wait did not consume waited semaphore\\n");
                    return 9;
                }}
                if (!((PdockerVkSemaphore *)sem_b)->signaled) {{
                    fprintf(stderr, "binary signal target not signaled\\n");
                    return 10;
                }}
                if (vkWaitForFences(VK_NULL_HANDLE, 1, &fence, VK_TRUE, 0) != VK_SUCCESS) {{
                    fprintf(stderr, "submit fence not signaled\\n");
                    return 11;
                }}

                vkDestroyFence(VK_NULL_HANDLE, fence, NULL);
                vkDestroySemaphore(VK_NULL_HANDLE, sem_a, NULL);
                vkDestroySemaphore(VK_NULL_HANDLE, sem_b, NULL);
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_timeline_semaphore_submit_wait_signal_and_counter_executes_c_code(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include <unistd.h>
            #include "{ICD_SOURCE}"
            {COMMAND_BUFFER_LIVE_REGISTRY_HELPER}

            static VkSemaphore make_timeline(VkDevice device, uint64_t initial_value) {{
                VkSemaphore sem = VK_NULL_HANDLE;
                VkSemaphoreTypeCreateInfo type_info;
                memset(&type_info, 0, sizeof(type_info));
                type_info.sType = VK_STRUCTURE_TYPE_SEMAPHORE_TYPE_CREATE_INFO;
                type_info.semaphoreType = VK_SEMAPHORE_TYPE_TIMELINE;
                type_info.initialValue = initial_value;
                VkSemaphoreCreateInfo create_info;
                memset(&create_info, 0, sizeof(create_info));
                create_info.sType = VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO;
                create_info.pNext = &type_info;
                if (vkCreateSemaphore(device, &create_info, NULL, &sem) != VK_SUCCESS) return VK_NULL_HANDLE;
                return sem;
            }}

            int main(void) {{
                unsetenv("PDOCKER_GPU_QUEUE_SOCKET");
                ensure_vulkan_dispatchable_object_ids();
                g_queue.instance_object_id = 1;
                g_queue.physical_device_object_id = 1;
                g_queue.device_object_id = 1;
                PdockerVkDevice device;
                memset(&device, 0, sizeof(device));
                device.requested_feature_mask = PDOCKER_VK_FEATURE_TIMELINE_SEMAPHORE;
                VkDevice vk_device = (VkDevice)&device;
                if (!advertised_timeline_semaphore()) {{
                    if (make_timeline(vk_device, 5) != VK_NULL_HANDLE) {{
                        fprintf(stderr, "timeline semaphore was accepted without advertised support\\n");
                        return 20;
                    }}
                    return 0;
                }}
                VkSemaphore wait_sem = make_timeline(vk_device, 5);
                VkSemaphore signal_sem = make_timeline(vk_device, 0);
                if (!wait_sem || !signal_sem) return 2;
                uint64_t value = 0;
                if (vkGetSemaphoreCounterValue(VK_NULL_HANDLE, wait_sem, &value) != VK_SUCCESS || value != 5) {{
                    fprintf(stderr, "initial timeline value mismatch value=%llu\\n", (unsigned long long)value);
                    return 3;
                }}

                VkSemaphoreWaitInfo wait_info;
                memset(&wait_info, 0, sizeof(wait_info));
                wait_info.sType = VK_STRUCTURE_TYPE_SEMAPHORE_WAIT_INFO;
                wait_info.semaphoreCount = 1;
                wait_info.pSemaphores = &wait_sem;
                uint64_t wait_value = 6;
                wait_info.pValues = &wait_value;
                if (vkWaitSemaphores(VK_NULL_HANDLE, &wait_info, 0) != VK_TIMEOUT) {{
                    fprintf(stderr, "unsatisfied timeline wait did not time out\\n");
                    return 4;
                }}
                VkSemaphoreSignalInfo signal_info;
                memset(&signal_info, 0, sizeof(signal_info));
                signal_info.sType = VK_STRUCTURE_TYPE_SEMAPHORE_SIGNAL_INFO;
                signal_info.semaphore = wait_sem;
                signal_info.value = 6;
                if (vkSignalSemaphore(VK_NULL_HANDLE, &signal_info) != VK_SUCCESS) return 5;
                if (vkWaitSemaphores(VK_NULL_HANDLE, &wait_info, 0) != VK_SUCCESS) {{
                    fprintf(stderr, "satisfied timeline wait failed\\n");
                    return 6;
                }}

                VkPipelineStageFlags wait_stage = VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT;
                VkSubmitInfo submit;
                memset(&submit, 0, sizeof(submit));
                submit.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO;
                submit.waitSemaphoreCount = 1;
                submit.pWaitSemaphores = &wait_sem;
                submit.pWaitDstStageMask = &wait_stage;
                submit.signalSemaphoreCount = 1;
                submit.pSignalSemaphores = &signal_sem;
                uint64_t submit_wait_value = 6;
                uint64_t submit_signal_value = 9;
                VkTimelineSemaphoreSubmitInfo timeline;
                memset(&timeline, 0, sizeof(timeline));
                timeline.sType = VK_STRUCTURE_TYPE_TIMELINE_SEMAPHORE_SUBMIT_INFO;
                timeline.waitSemaphoreValueCount = 1;
                timeline.pWaitSemaphoreValues = &submit_wait_value;
                timeline.signalSemaphoreValueCount = 1;
                timeline.pSignalSemaphoreValues = &submit_signal_value;
                submit.pNext = &timeline;
                if (vkQueueSubmit((VkQueue)&g_queue, 1, &submit, VK_NULL_HANDLE) != VK_SUCCESS) {{
                    fprintf(stderr, "timeline submit wait/signal failed\\n");
                    return 7;
                }}
                if (vkGetSemaphoreCounterValue(VK_NULL_HANDLE, signal_sem, &value) != VK_SUCCESS || value != 9) {{
                    fprintf(stderr, "timeline submit signal value mismatch value=%llu\\n", (unsigned long long)value);
                    return 8;
                }}

                vkDestroySemaphore(VK_NULL_HANDLE, wait_sem, NULL);
                vkDestroySemaphore(VK_NULL_HANDLE, signal_sem, NULL);
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)




    def test_submit2_struct_shape_failures_do_not_mutate_fence(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include <stdlib.h>
            #include "{ICD_SOURCE}"
            {COMMAND_BUFFER_LIVE_REGISTRY_HELPER}

            static VkFence make_signaled_fence(void) {{
                VkFence fence = VK_NULL_HANDLE;
                VkFenceCreateInfo create_info;
                memset(&create_info, 0, sizeof(create_info));
                create_info.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO;
                create_info.flags = VK_FENCE_CREATE_SIGNALED_BIT;
                if (vkCreateFence(VK_NULL_HANDLE, &create_info, NULL, &fence) != VK_SUCCESS) return VK_NULL_HANDLE;
                return fence;
            }}

            static VkSemaphore make_binary_semaphore(void) {{
                VkSemaphore semaphore = VK_NULL_HANDLE;
                VkSemaphoreCreateInfo create_info;
                memset(&create_info, 0, sizeof(create_info));
                create_info.sType = VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO;
                if (vkCreateSemaphore(VK_NULL_HANDLE, &create_info, NULL, &semaphore) != VK_SUCCESS) return VK_NULL_HANDLE;
                return semaphore;
            }}

            static int expect_submit2_failure_preserves_fence(VkSubmitInfo2 *submit, VkResult expected, int code) {{
                VkFence fence = make_signaled_fence();
                if (!fence) return code + 100;
                VkResult rc = vkQueueSubmit2((VkQueue)&g_queue, 1, submit, fence);
                if (rc != expected) {{
                    fprintf(stderr, "case %d returned %d expected %d\\n", code, rc, expected);
                    return code;
                }}
                if (vkGetFenceStatus(VK_NULL_HANDLE, fence) != VK_SUCCESS) {{
                    fprintf(stderr, "case %d mutated signaled fence during prevalidation\\n", code);
                    return code + 200;
                }}
                vkDestroyFence(VK_NULL_HANDLE, fence, NULL);
                return 0;
            }}

            int main(void) {{
                unsetenv("PDOCKER_GPU_QUEUE_SOCKET");
                ensure_vulkan_dispatchable_object_ids();
                g_queue.instance_object_id = 1;
                g_queue.physical_device_object_id = 1;
                g_queue.device_object_id = 1;
                g_queue.requested_feature_mask = PDOCKER_VK_FEATURE_SYNCHRONIZATION_2;
                g_queue.enabled_extension_mask = PDOCKER_VK_DEVICE_EXT_KHR_SYNCHRONIZATION_2;

                VkSubmitInfo2 submit;
                memset(&submit, 0, sizeof(submit));
                submit.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO_2;
                if (vkQueueSubmit2((VkQueue)&g_queue, 1, &submit, VK_NULL_HANDLE) != VK_SUCCESS) {{
                    fprintf(stderr, "valid empty submit2 failed\\n");
                    return 2;
                }}

                VkBaseInStructure unsupported;
                memset(&unsupported, 0, sizeof(unsupported));
                unsupported.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO;

                submit.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO;
                int err = expect_submit2_failure_preserves_fence(&submit, VK_ERROR_INITIALIZATION_FAILED, 3);
                if (err) return err;
                submit.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO_2;

                submit.pNext = &unsupported;
                err = expect_submit2_failure_preserves_fence(&submit, VK_ERROR_FEATURE_NOT_PRESENT, 4);
                if (err) return err;
                submit.pNext = NULL;

                submit.flags = (VkSubmitFlags)1u;
                err = expect_submit2_failure_preserves_fence(&submit, VK_ERROR_FEATURE_NOT_PRESENT, 5);
                if (err) return err;
                submit.flags = 0;

                submit.waitSemaphoreInfoCount = 1;
                submit.pWaitSemaphoreInfos = NULL;
                err = expect_submit2_failure_preserves_fence(&submit, VK_ERROR_INITIALIZATION_FAILED, 6);
                if (err) return err;
                submit.waitSemaphoreInfoCount = 0;

                submit.commandBufferInfoCount = 1;
                submit.pCommandBufferInfos = NULL;
                err = expect_submit2_failure_preserves_fence(&submit, VK_ERROR_INITIALIZATION_FAILED, 7);
                if (err) return err;
                submit.commandBufferInfoCount = 0;

                submit.signalSemaphoreInfoCount = 1;
                submit.pSignalSemaphoreInfos = NULL;
                err = expect_submit2_failure_preserves_fence(&submit, VK_ERROR_INITIALIZATION_FAILED, 8);
                if (err) return err;
                submit.signalSemaphoreInfoCount = 0;

                VkSemaphore semaphore = make_binary_semaphore();
                if (!semaphore) return 9;

                VkSemaphoreSubmitInfo sem_info;
                memset(&sem_info, 0, sizeof(sem_info));
                sem_info.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO;
                sem_info.semaphore = semaphore;
                submit.waitSemaphoreInfoCount = 1;
                submit.pWaitSemaphoreInfos = &sem_info;
                err = expect_submit2_failure_preserves_fence(&submit, VK_ERROR_INITIALIZATION_FAILED, 10);
                if (err) return err;

                sem_info.sType = VK_STRUCTURE_TYPE_SEMAPHORE_SUBMIT_INFO;
                sem_info.semaphore = VK_NULL_HANDLE;
                err = expect_submit2_failure_preserves_fence(&submit, VK_ERROR_INITIALIZATION_FAILED, 11);
                if (err) return err;

                sem_info.semaphore = semaphore;
                sem_info.pNext = &unsupported;
                err = expect_submit2_failure_preserves_fence(&submit, VK_ERROR_FEATURE_NOT_PRESENT, 12);
                if (err) return err;
                sem_info.pNext = NULL;

                sem_info.deviceIndex = 1;
                err = expect_submit2_failure_preserves_fence(&submit, VK_ERROR_FEATURE_NOT_PRESENT, 13);
                if (err) return err;
                sem_info.deviceIndex = 0;
                submit.waitSemaphoreInfoCount = 0;
                submit.pWaitSemaphoreInfos = NULL;

                memset(&sem_info, 0, sizeof(sem_info));
                sem_info.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO;
                sem_info.semaphore = semaphore;
                submit.signalSemaphoreInfoCount = 1;
                submit.pSignalSemaphoreInfos = &sem_info;
                err = expect_submit2_failure_preserves_fence(&submit, VK_ERROR_INITIALIZATION_FAILED, 14);
                if (err) return err;

                sem_info.sType = VK_STRUCTURE_TYPE_SEMAPHORE_SUBMIT_INFO;
                sem_info.semaphore = VK_NULL_HANDLE;
                err = expect_submit2_failure_preserves_fence(&submit, VK_ERROR_INITIALIZATION_FAILED, 15);
                if (err) return err;

                sem_info.semaphore = semaphore;
                sem_info.pNext = &unsupported;
                err = expect_submit2_failure_preserves_fence(&submit, VK_ERROR_FEATURE_NOT_PRESENT, 16);
                if (err) return err;
                sem_info.pNext = NULL;

                sem_info.deviceIndex = 1;
                err = expect_submit2_failure_preserves_fence(&submit, VK_ERROR_FEATURE_NOT_PRESENT, 17);
                if (err) return err;
                sem_info.deviceIndex = 0;
                submit.signalSemaphoreInfoCount = 0;
                submit.pSignalSemaphoreInfos = NULL;

                PdockerVkCommandBuffer *cmd = sync_test_command_buffer_alloc();
                if (!cmd) return 18;
                cmd->requested_feature_mask = PDOCKER_VK_FEATURE_SYNCHRONIZATION_2;
                cmd->enabled_extension_mask = PDOCKER_VK_DEVICE_EXT_KHR_SYNCHRONIZATION_2;
                VkCommandBufferSubmitInfo cmd_info;
                memset(&cmd_info, 0, sizeof(cmd_info));
                cmd_info.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO;
                cmd_info.commandBuffer = (VkCommandBuffer)cmd;
                submit.commandBufferInfoCount = 1;
                submit.pCommandBufferInfos = &cmd_info;
                err = expect_submit2_failure_preserves_fence(&submit, VK_ERROR_INITIALIZATION_FAILED, 19);
                if (err) return err;

                cmd_info.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_SUBMIT_INFO;
                cmd_info.commandBuffer = VK_NULL_HANDLE;
                err = expect_submit2_failure_preserves_fence(&submit, VK_ERROR_INITIALIZATION_FAILED, 20);
                if (err) return err;

                cmd_info.commandBuffer = (VkCommandBuffer)cmd;
                cmd_info.pNext = &unsupported;
                err = expect_submit2_failure_preserves_fence(&submit, VK_ERROR_FEATURE_NOT_PRESENT, 21);
                if (err) return err;
                cmd_info.pNext = NULL;

                cmd_info.deviceMask = 2;
                err = expect_submit2_failure_preserves_fence(&submit, VK_ERROR_FEATURE_NOT_PRESENT, 22);
                if (err) return err;

                free(cmd);
                vkDestroySemaphore(VK_NULL_HANDLE, semaphore, NULL);
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


    def test_descriptor_update_template_updates_storage_buffer_slots_via_staged_path(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"
            {COMMAND_BUFFER_LIVE_REGISTRY_HELPER}

            int main(void) {{
                unsetenv("PDOCKER_GPU_QUEUE_SOCKET");

                VkDescriptorSetLayoutBinding binding;
                memset(&binding, 0, sizeof(binding));
                binding.binding = 5;
                binding.descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
                binding.descriptorCount = 2;
                binding.stageFlags = VK_SHADER_STAGE_COMPUTE_BIT;

                VkDescriptorSetLayoutCreateInfo layout_info;
                memset(&layout_info, 0, sizeof(layout_info));
                layout_info.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO;
                layout_info.bindingCount = 1;
                layout_info.pBindings = &binding;
                VkDescriptorSetLayout layout = VK_NULL_HANDLE;
                if (vkCreateDescriptorSetLayout(VK_NULL_HANDLE, &layout_info, NULL, &layout) != VK_SUCCESS || !layout) {{
                    fprintf(stderr, "descriptor layout create failed\\n");
                    return 2;
                }}

                VkDescriptorPoolCreateInfo pool_info;
                memset(&pool_info, 0, sizeof(pool_info));
                pool_info.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO;
                pool_info.maxSets = 1;
                VkDescriptorPool pool = VK_NULL_HANDLE;
                if (vkCreateDescriptorPool(VK_NULL_HANDLE, &pool_info, NULL, &pool) != VK_SUCCESS || !pool) {{
                    fprintf(stderr, "descriptor pool create failed\\n");
                    return 3;
                }}

                VkDescriptorSetAllocateInfo alloc_info;
                memset(&alloc_info, 0, sizeof(alloc_info));
                alloc_info.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO;
                alloc_info.descriptorPool = pool;
                alloc_info.descriptorSetCount = 1;
                alloc_info.pSetLayouts = &layout;
                VkDescriptorSet set_handle = VK_NULL_HANDLE;
                if (vkAllocateDescriptorSets(VK_NULL_HANDLE, &alloc_info, &set_handle) != VK_SUCCESS || !set_handle) {{
                    fprintf(stderr, "descriptor set allocate failed\\n");
                    return 4;
                }}

                VkBufferCreateInfo buffer_info;
                memset(&buffer_info, 0, sizeof(buffer_info));
                buffer_info.sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO;
                buffer_info.size = 1024;
                buffer_info.usage = VK_BUFFER_USAGE_STORAGE_BUFFER_BIT;
                buffer_info.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
                VkBuffer buffer = VK_NULL_HANDLE;
                if (vkCreateBuffer(VK_NULL_HANDLE, &buffer_info, NULL, &buffer) != VK_SUCCESS || !buffer) {{
                    fprintf(stderr, "buffer create failed\\n");
                    return 5;
                }}

                VkMemoryAllocateInfo memory_info;
                memset(&memory_info, 0, sizeof(memory_info));
                memory_info.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO;
                memory_info.allocationSize = 2048;
                memory_info.memoryTypeIndex = 1;
                VkDeviceMemory memory = VK_NULL_HANDLE;
                if (vkAllocateMemory(VK_NULL_HANDLE, &memory_info, NULL, &memory) != VK_SUCCESS || !memory) {{
                    fprintf(stderr, "memory allocate failed\\n");
                    return 6;
                }}
                if (vkBindBufferMemory(VK_NULL_HANDLE, buffer, memory, 0) != VK_SUCCESS) {{
                    fprintf(stderr, "buffer bind failed\\n");
                    return 7;
                }}

                VkDescriptorUpdateTemplateEntry entry;
                memset(&entry, 0, sizeof(entry));
                entry.dstBinding = 5;
                entry.dstArrayElement = 0;
                entry.descriptorCount = 2;
                entry.descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
                entry.offset = 0;
                entry.stride = sizeof(VkDescriptorBufferInfo);

                VkDescriptorUpdateTemplateCreateInfo template_info;
                memset(&template_info, 0, sizeof(template_info));
                template_info.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_UPDATE_TEMPLATE_CREATE_INFO;
                template_info.descriptorUpdateEntryCount = 1;
                template_info.pDescriptorUpdateEntries = &entry;
                template_info.templateType = VK_DESCRIPTOR_UPDATE_TEMPLATE_TYPE_DESCRIPTOR_SET;
                template_info.descriptorSetLayout = layout;
                VkDescriptorUpdateTemplate update_template = VK_NULL_HANDLE;
                if (vkCreateDescriptorUpdateTemplate(VK_NULL_HANDLE, &template_info, NULL, &update_template) != VK_SUCCESS || !update_template) {{
                    fprintf(stderr, "descriptor update template create failed\\n");
                    return 8;
                }}

                VkDescriptorBufferInfo payload[2];
                memset(payload, 0, sizeof(payload));
                payload[0].buffer = buffer;
                payload[0].offset = 64;
                payload[0].range = 32;
                payload[1].buffer = buffer;
                payload[1].offset = 128;
                payload[1].range = 64;
                vkUpdateDescriptorSetWithTemplate(VK_NULL_HANDLE, set_handle, update_template, payload);

                PdockerVkDescriptorSet *set = pdocker_vk_descriptor_set_from_handle(set_handle);
                int slot = descriptor_layout_slot_for_binding(set ? set->layout : NULL, 5);
                if (!set || slot != 0 || set->unsupported_descriptor_array || set->unsupported_descriptor_type) {{
                    fprintf(stderr, "descriptor set/template state invalid slot=%d unsupported_array=%d unsupported_type=%d\\n",
                            slot,
                            set && set->unsupported_descriptor_array ? 1 : 0,
                            set && set->unsupported_descriptor_type ? 1 : 0);
                    return 9;
                }}
                PdockerVkDescriptorBinding *slot0 = descriptor_set_binding_slot(set, (uint32_t)slot, 0);
                PdockerVkDescriptorBinding *slot1 = descriptor_set_binding_slot(set, (uint32_t)slot, 1);
                PdockerVkBuffer *buffer_object = pdocker_vk_buffer_from_handle(buffer);
                if (!slot0 || !slot1 || slot0->buffer != buffer_object || slot1->buffer != buffer_object) {{
                    fprintf(stderr, "descriptor template did not bind expected buffers\\n");
                    return 10;
                }}
                if (slot0->offset != 64 || slot0->range != 32 ||
                    slot1->offset != 128 || slot1->range != 64) {{
                    fprintf(stderr, "descriptor offsets/ranges mismatch got0=%llu/%llu got1=%llu/%llu\\n",
                            (unsigned long long)slot0->offset,
                            (unsigned long long)slot0->range,
                            (unsigned long long)slot1->offset,
                            (unsigned long long)slot1->range);
                    return 11;
                }}
                if (slot0->descriptor_type != VK_DESCRIPTOR_TYPE_STORAGE_BUFFER ||
                    slot1->descriptor_type != VK_DESCRIPTOR_TYPE_STORAGE_BUFFER ||
                    !slot0->buffer_snapshot.valid || !slot1->buffer_snapshot.valid ||
                    slot0->buffer_snapshot.object_id != pdocker_vk_buffer_object_id(buffer_object) ||
                    slot1->buffer_snapshot.object_id != pdocker_vk_buffer_object_id(buffer_object)) {{
                    fprintf(stderr, "descriptor snapshots/type mismatch\\n");
                    return 12;
                }}

                vkDestroyDescriptorUpdateTemplate(VK_NULL_HANDLE, update_template, NULL);
                vkFreeMemory(VK_NULL_HANDLE, memory, NULL);
                vkDestroyBuffer(VK_NULL_HANDLE, buffer, NULL);
                vkDestroyDescriptorPool(VK_NULL_HANDLE, pool, NULL);
                vkDestroyDescriptorSetLayout(VK_NULL_HANDLE, layout, NULL);
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


    def test_descriptor_update_after_bind_requires_feature_layout_and_pool_flags(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"
            {COMMAND_BUFFER_LIVE_REGISTRY_HELPER}

            int main(void) {{
                unsetenv("PDOCKER_GPU_QUEUE_SOCKET");

                PdockerVkDevice device;
                memset(&device, 0, sizeof(device));
                device.requested_feature_mask = PDOCKER_VK_FEATURE_DESCRIPTOR_STORAGE_BUFFER_UPDATE_AFTER_BIND;

                VkDescriptorSetLayoutBinding binding;
                memset(&binding, 0, sizeof(binding));
                binding.binding = 0;
                binding.descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
                binding.descriptorCount = 1;
                binding.stageFlags = VK_SHADER_STAGE_COMPUTE_BIT;

                VkDescriptorBindingFlags binding_flags = VK_DESCRIPTOR_BINDING_UPDATE_AFTER_BIND_BIT;
                VkDescriptorSetLayoutBindingFlagsCreateInfo flags_info;
                memset(&flags_info, 0, sizeof(flags_info));
                flags_info.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_BINDING_FLAGS_CREATE_INFO;
                flags_info.bindingCount = 1;
                flags_info.pBindingFlags = &binding_flags;

                VkDescriptorSetLayoutCreateInfo layout_info;
                memset(&layout_info, 0, sizeof(layout_info));
                layout_info.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO;
                layout_info.pNext = &flags_info;
                layout_info.bindingCount = 1;
                layout_info.pBindings = &binding;

                VkDescriptorSetLayout layout = VK_NULL_HANDLE;
                if (vkCreateDescriptorSetLayout(VK_NULL_HANDLE, &layout_info, NULL, &layout) != VK_ERROR_FEATURE_NOT_PRESENT) {{
                    fprintf(stderr, "UAB layout without requested feature/layout flag unexpectedly succeeded\\n");
                    return 2;
                }}
                if (vkCreateDescriptorSetLayout((VkDevice)&device, &layout_info, NULL, &layout) != VK_ERROR_FEATURE_NOT_PRESENT) {{
                    fprintf(stderr, "UAB layout without layout pool flag unexpectedly succeeded\\n");
                    return 3;
                }}

                layout_info.flags = VK_DESCRIPTOR_SET_LAYOUT_CREATE_UPDATE_AFTER_BIND_POOL_BIT;
                if (vkCreateDescriptorSetLayout((VkDevice)&device, &layout_info, NULL, &layout) != VK_SUCCESS || !layout) {{
                    fprintf(stderr, "UAB layout with feature and layout flag failed\\n");
                    return 4;
                }}

                VkDescriptorPoolCreateInfo pool_info;
                memset(&pool_info, 0, sizeof(pool_info));
                pool_info.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO;
                pool_info.maxSets = 1;
                VkDescriptorPool ordinary_pool = VK_NULL_HANDLE;
                if (vkCreateDescriptorPool((VkDevice)&device, &pool_info, NULL, &ordinary_pool) != VK_SUCCESS || !ordinary_pool) {{
                    fprintf(stderr, "ordinary descriptor pool create failed\\n");
                    return 5;
                }}

                VkDescriptorSetAllocateInfo alloc_info;
                memset(&alloc_info, 0, sizeof(alloc_info));
                alloc_info.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO;
                alloc_info.descriptorPool = ordinary_pool;
                alloc_info.descriptorSetCount = 1;
                alloc_info.pSetLayouts = &layout;
                VkDescriptorSet set_handle = VK_NULL_HANDLE;
                if (vkAllocateDescriptorSets((VkDevice)&device, &alloc_info, &set_handle) != VK_ERROR_FEATURE_NOT_PRESENT) {{
                    fprintf(stderr, "UAB set allocated from ordinary pool\\n");
                    return 6;
                }}
                vkDestroyDescriptorPool((VkDevice)&device, ordinary_pool, NULL);

                pool_info.flags = VK_DESCRIPTOR_POOL_CREATE_UPDATE_AFTER_BIND_BIT;
                VkDescriptorPool uab_pool = VK_NULL_HANDLE;
                if (vkCreateDescriptorPool((VkDevice)&device, &pool_info, NULL, &uab_pool) != VK_SUCCESS || !uab_pool) {{
                    fprintf(stderr, "UAB descriptor pool create failed\\n");
                    return 7;
                }}
                alloc_info.descriptorPool = uab_pool;
                if (vkAllocateDescriptorSets((VkDevice)&device, &alloc_info, &set_handle) != VK_SUCCESS || !set_handle) {{
                    fprintf(stderr, "UAB set allocation from UAB pool failed\\n");
                    return 8;
                }}
                vkDestroyDescriptorPool((VkDevice)&device, uab_pool, NULL);
                vkDestroyDescriptorSetLayout((VkDevice)&device, layout, NULL);
                return 0;
            }}
            """
        )
        result = self.compile_and_run(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()

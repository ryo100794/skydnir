import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ICD_SOURCE = ROOT / "docker-proot-setup" / "src" / "gpu" / "pdocker_vulkan_icd.c"


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

            int main(void) {{
                unsetenv("PDOCKER_GPU_QUEUE_SOCKET");
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
                if (vkQueueSubmit(VK_NULL_HANDLE, 0, NULL, fence) != VK_SUCCESS) {{
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

            int main(void) {{
                PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)calloc(1, sizeof(*cmd));
                if (!cmd) return 9;
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

            int main(void) {{
                PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)calloc(1, sizeof(*cmd));
                if (!cmd) return 9;

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
                PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)calloc(1, sizeof(*cmd));
                if (!cmd) return 9;

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

    def test_dispatch_commands_fail_closed_when_recorded_inside_rendering_scopes(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include <stdlib.h>
            #include "{ICD_SOURCE}"

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
                PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)calloc(1, sizeof(*cmd));
                if (!cmd) return 9;

                cmd->dynamic_rendering_active = true;
                vkCmdDispatch((VkCommandBuffer)cmd, 1, 1, 1);
                if (!expect_dispatch_scope_failure(cmd, "dispatch-inside-dynamic-rendering-unsupported")) return 2;

                memset(cmd, 0, sizeof(*cmd));
                cmd->render_pass_active = true;
                vkCmdDispatchBase((VkCommandBuffer)cmd, 0, 0, 0, 1, 1, 1);
                if (!expect_dispatch_scope_failure(cmd, "dispatch-inside-legacy-render-pass-unsupported")) return 3;

                memset(cmd, 0, sizeof(*cmd));
                cmd->dynamic_rendering_active = true;
                cmd->active_render_pass = (PdockerVkRenderPass *)0x1;
                vkCmdDispatch((VkCommandBuffer)cmd, 1, 1, 1);
                if (!expect_dispatch_scope_failure(cmd, "dispatch-inside-legacy-render-pass-unsupported")) return 4;

                memset(cmd, 0, sizeof(*cmd));
                cmd->inherited_rendering_active = true;
                vkCmdDispatchIndirect((VkCommandBuffer)cmd, VK_NULL_HANDLE, 0);
                if (!expect_dispatch_scope_failure(cmd, "dispatch-inside-inherited-rendering-unsupported")) return 5;

                memset(cmd, 0, sizeof(*cmd));
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


    def test_pipeline_barrier2_records_precise_unsupported_dependency_reasons(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include <stdlib.h>
            #include "{ICD_SOURCE}"

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
                PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)calloc(1, sizeof(*cmd));
                if (!cmd) return 9;

                DummyPnext unsupported;
                memset(&unsupported, 0, sizeof(unsupported));
                unsupported.sType = (VkStructureType)1000060013;

                VkDependencyInfo dependency;
                memset(&dependency, 0, sizeof(dependency));
                dependency.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO;
                dependency.pNext = &unsupported;
                vkCmdPipelineBarrier2((VkCommandBuffer)cmd, &dependency);
                if (!expect_failure(cmd, "pipeline-barrier2-pnext-unsupported")) return 2;

                memset(cmd, 0, sizeof(*cmd));
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
                memset(&dependency, 0, sizeof(dependency));
                dependency.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO;
                dependency.memoryBarrierCount = 1;
                dependency.pMemoryBarriers = NULL;
                vkCmdPipelineBarrier2((VkCommandBuffer)cmd, &dependency);
                if (!expect_failure(cmd, "pipeline-barrier2-missing-barrier-array")) return 4;

                memset(cmd, 0, sizeof(*cmd));
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
                memset(&dependency, 0, sizeof(dependency));
                dependency.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO;
                dependency.dependencyFlags = VK_DEPENDENCY_VIEW_LOCAL_BIT;
                vkCmdPipelineBarrier2((VkCommandBuffer)cmd, &dependency);
                if (!expect_failure(cmd, "pipeline-barrier2-dependency-flags-unsupported")) return 6;

                memset(cmd, 0, sizeof(*cmd));
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


    def test_external_memory_acquire_unmodified_buffer_barrier_pnext_records_cleanly(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include <stdlib.h>
            #include "{ICD_SOURCE}"

            int main(void) {{
            #ifndef VK_EXT_external_memory_acquire_unmodified
                return 0;
            #else
                PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)calloc(1, sizeof(*cmd));
                PdockerVkBuffer *buffer = (PdockerVkBuffer *)calloc(1, sizeof(*buffer));
                if (!cmd || !buffer) return 9;
                buffer->object_id = 0x1234u;
                buffer->size = 4096u;

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

    def test_image_layout_range_cache_splits_partial_overlaps_without_overflow(self):
        source = textwrap.dedent(
            f"""
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>
            #include "{ICD_SOURCE}"

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

            int main(void) {{
                PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)calloc(1, sizeof(*cmd));
                if (!cmd) return 9;

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

            int main(void) {{
                unsetenv("PDOCKER_GPU_QUEUE_SOCKET");
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
                if (vkQueueSubmit(VK_NULL_HANDLE, 1, &submit, VK_NULL_HANDLE) != VK_ERROR_FEATURE_NOT_PRESENT) {{
                    fprintf(stderr, "unsignaled binary wait did not fail closed\\n");
                    return 4;
                }}

                memset(&submit, 0, sizeof(submit));
                submit.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO;
                submit.signalSemaphoreCount = 1;
                submit.pSignalSemaphores = &sem_a;
                if (vkQueueSubmit(VK_NULL_HANDLE, 1, &submit, VK_NULL_HANDLE) != VK_SUCCESS) {{
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
                if (vkQueueSubmit(VK_NULL_HANDLE, 1, &submit, fence) != VK_SUCCESS) {{
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
                if (vkQueueSubmit(VK_NULL_HANDLE, 1, &submit, VK_NULL_HANDLE) != VK_SUCCESS) {{
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
                VkResult rc = vkQueueSubmit2(VK_NULL_HANDLE, 1, submit, fence);
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

                VkSubmitInfo2 submit;
                memset(&submit, 0, sizeof(submit));
                submit.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO_2;
                if (vkQueueSubmit2(VK_NULL_HANDLE, 1, &submit, VK_NULL_HANDLE) != VK_SUCCESS) {{
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

                PdockerVkCommandBuffer *cmd = (PdockerVkCommandBuffer *)calloc(1, sizeof(*cmd));
                if (!cmd) return 18;
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

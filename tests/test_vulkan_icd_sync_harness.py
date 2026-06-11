import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ICD_SOURCE = ROOT / "docker-proot-setup" / "src" / "gpu" / "pdocker_vulkan_icd.c"


@unittest.skipUnless(shutil.which("gcc"), "gcc is required for the ICD C sync harness")
class VulkanIcdSyncHarnessTest(unittest.TestCase):
    def compile_and_run(self, source: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "icd_sync_harness.c"
            exe = Path(tmpdir) / "icd_sync_harness"
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

            static VkSemaphore make_timeline(uint64_t initial_value) {{
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
                if (vkCreateSemaphore(VK_NULL_HANDLE, &create_info, NULL, &sem) != VK_SUCCESS) return VK_NULL_HANDLE;
                return sem;
            }}

            int main(void) {{
                unsetenv("PDOCKER_GPU_QUEUE_SOCKET");
                VkSemaphore wait_sem = make_timeline(5);
                VkSemaphore signal_sem = make_timeline(0);
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



if __name__ == "__main__":
    unittest.main()

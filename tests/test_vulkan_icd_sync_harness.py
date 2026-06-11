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


if __name__ == "__main__":
    unittest.main()

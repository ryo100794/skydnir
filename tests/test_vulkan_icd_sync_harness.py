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


if __name__ == "__main__":
    unittest.main()

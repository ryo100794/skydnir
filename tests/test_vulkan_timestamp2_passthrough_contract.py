#!/usr/bin/env python3
"""P0 contracts for native Vulkan timestamp2 replay.

The wire stage mask is 64-bit.  These gates prevent the executor from silently
narrowing synchronization2 stages to the legacy 32-bit API or replacing a
valid VK_PIPELINE_STAGE_2_NONE value with another stage.
"""

from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
EXECUTOR = ROOT / "app/src/main/cpp/pdocker_gpu_executor.c"


def _matching_brace(source: str, opening: int) -> int:
    depth = 0
    state = "code"
    offset = opening
    while offset < len(source):
        char = source[offset]
        next_char = source[offset + 1] if offset + 1 < len(source) else ""
        if state == "line-comment":
            if char == "\n":
                state = "code"
        elif state == "block-comment":
            if char == "*" and next_char == "/":
                state = "code"
                offset += 1
        elif state == "string":
            if char == "\\":
                offset += 1
            elif char == '"':
                state = "code"
        elif state == "character":
            if char == "\\":
                offset += 1
            elif char == "'":
                state = "code"
        else:
            if char == "/" and next_char == "/":
                state = "line-comment"
                offset += 1
            elif char == "/" and next_char == "*":
                state = "block-comment"
                offset += 1
            elif char == '"':
                state = "string"
            elif char == "'":
                state = "character"
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return offset
        offset += 1
    raise AssertionError("unterminated C function")


def c_function(source: str, name: str) -> str:
    for match in re.finditer(rf"\b{re.escape(name)}\s*\(", source):
        brace = source.find("{", match.end())
        semicolon = source.find(";", match.end())
        if brace < 0 or (semicolon >= 0 and semicolon < brace):
            continue
        start = source.rfind("\n\n", 0, match.start())
        start = 0 if start < 0 else start + 2
        return source[start : _matching_brace(source, brace) + 1]
    raise AssertionError(f"C function definition not found: {name}")


class VulkanTimestamp2PassthroughContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = EXECUTOR.read_text(encoding="utf-8")
        cls.loader = c_function(cls.source, "init_vulkan_runtime")
        cls.loaded = c_function(cls.source, "vulkan_runtime_sync2_loaded")
        cls.decision = c_function(
            cls.source, "vulkan_graphics_timestamp_requires_synchronization2"
        )
        cls.replay = c_function(
            cls.source, "vulkan_graphics_record_query_timestamp2"
        )
        cls.command_replay = c_function(
            cls.source, "record_vulkan_graphics_v6_command_buffer"
        )

    def test_runtime_loads_core_timestamp2_then_khr_alias_when_enabled(self) -> None:
        self.assertIn("PFN_vkCmdWriteTimestamp2 cmd_write_timestamp2;", self.source)
        enabled = "if (rt->enabled_synchronization2.synchronization2)"
        core = 'vkGetDeviceProcAddr(rt->device, "vkCmdWriteTimestamp2")'
        alias = 'vkGetDeviceProcAddr(rt->device, "vkCmdWriteTimestamp2KHR")'
        self.assertIn(enabled, self.loader)
        self.assertIn(core, self.loader)
        self.assertIn(alias, self.loader)
        self.assertLess(self.loader.index(enabled), self.loader.index(core))
        self.assertLess(self.loader.index(core), self.loader.index(alias))

    def test_sync2_advertisement_requires_timestamp2_and_existing_entry_points(self) -> None:
        for required in (
            "rt->queue_submit2",
            "rt->cmd_pipeline_barrier2",
            "rt->cmd_set_event2",
            "rt->cmd_reset_event2",
            "rt->cmd_wait_events2",
            "rt->cmd_write_timestamp2",
        ):
            self.assertIn(required, self.loaded)
        self.assertIn(
            "const uint32_t synchronization2_loaded = "
            "vulkan_runtime_sync2_loaded(rt);",
            self.source,
        )
        self.assertIn(
            "synchronization2_usable = "
            "(synchronization2_enabled && synchronization2_loaded)",
            self.source,
        )

    def test_sync2_replay_keeps_the_transported_64_bit_stage_unchanged(self) -> None:
        self.assertIn("transported_stage_mask > UINT32_MAX", self.decision)
        self.assertIn(
            "const VkPipelineStageFlags2 transported_stage_mask",
            self.replay,
        )
        self.assertIn(
            "(VkPipelineStageFlags2)query->stage_mask",
            self.replay,
        )
        sync2_call = self.replay[
            self.replay.index("rt->cmd_write_timestamp2(") :
            self.replay.index("return 0;", self.replay.index("rt->cmd_write_timestamp2("))
        ]
        self.assertNotIn("uint32_t", sync2_call)
        self.assertNotIn("0xffffffff", sync2_call.lower())
        self.assertNotIn("VK_PIPELINE_STAGE_ALL_COMMANDS", sync2_call)
        self.assertNotIn("VK_PIPELINE_STAGE_2_ALL_COMMANDS", sync2_call)

    def test_zero_stage_is_timestamp2_none_and_is_not_replaced(self) -> None:
        self.assertIn("transported_stage_mask == 0", self.decision)
        self.assertNotIn("VK_PIPELINE_STAGE_ALL_COMMANDS", self.replay)
        self.assertNotIn("VK_PIPELINE_STAGE_2_ALL_COMMANDS", self.replay)
        self.assertNotRegex(
            self.replay,
            r"transported_stage_mask\s*\?[^:]+:\s*VK_PIPELINE_STAGE",
        )

    def test_legacy_single_bit_stage_still_uses_legacy_command(self) -> None:
        self.assertIn("vkCmdWriteTimestamp(", self.command_replay)
        self.assertIn(
            "(VkPipelineStageFlagBits)(uint32_t)query->stage_mask",
            self.command_replay,
        )
        self.assertIn(
            "(transported_stage_mask & (transported_stage_mask - 1u)) != 0",
            self.decision,
        )
        self.assertIn(
            "rc = vulkan_graphics_record_query_timestamp2(",
            self.command_replay,
        )
        old_timestamp_replay = re.compile(
            r"VkPipelineStageFlagBits\s+stage\s*=\s*query->stage_mask|"
            r"query->stage_mask\s*\?[^:]+:\s*VK_PIPELINE_STAGE_ALL_COMMANDS_BIT"
        )
        self.assertIsNone(old_timestamp_replay.search(self.command_replay))

    @unittest.skipUnless(shutil.which("gcc"), "gcc is required for the replay contract harness")
    def test_compiled_replay_preserves_high_zero_multi_and_legacy_stages(self) -> None:
        harness = textwrap.dedent(
            f"""
            #include <errno.h>
            #include <stdint.h>

            typedef uint64_t VkPipelineStageFlags2;
            typedef uintptr_t VkCommandBuffer;
            typedef uintptr_t VkQueryPool;
            typedef void (*PFN_vkCmdWriteTimestamp2)(
                VkCommandBuffer, VkPipelineStageFlags2, VkQueryPool, uint32_t);

            typedef struct {{ int synchronization2; }} Sync2Feature;
            typedef struct {{
                Sync2Feature enabled_synchronization2;
                PFN_vkCmdWriteTimestamp2 cmd_write_timestamp2;
            }} VulkanRuntime;
            typedef struct {{
                uint32_t command_index;
                uint32_t op;
                uint64_t query_pool_id;
                uint32_t first_query;
                uint32_t query_count;
                uint64_t stage_mask;
            }} PdockerGpuVulkanGraphicsV617QueryCommandEntry;

            static unsigned sync2_calls;
            static uint64_t observed_stage;

            static void mock_write_timestamp2(
                    VkCommandBuffer command_buffer,
                    VkPipelineStageFlags2 stage,
                    VkQueryPool pool,
                    uint32_t query) {{
                (void)command_buffer; (void)pool; (void)query;
                sync2_calls++;
                observed_stage = stage;
            }}

            {self.decision}

            {self.replay}

            static int run_sync2(uint64_t stage, int enabled, int expected_rc) {{
                VulkanRuntime rt = {{0}};
                rt.enabled_synchronization2.synchronization2 = enabled;
                rt.cmd_write_timestamp2 = enabled ? mock_write_timestamp2 : 0;
                PdockerGpuVulkanGraphicsV617QueryCommandEntry query = {{0}};
                query.first_query = 7;
                query.stage_mask = stage;
                sync2_calls = 0;
                observed_stage = UINT64_C(0xdeadbeefdeadbeef);
                int rc = vulkan_graphics_record_query_timestamp2(&rt, 1, &query, 2);
                if (rc != expected_rc) return 1;
                if (expected_rc == 0 && (sync2_calls != 1 || observed_stage != stage)) return 2;
                if (expected_rc != 0 && sync2_calls != 0) return 3;
                return 0;
            }}

            int main(void) {{
                const uint64_t high = UINT64_C(0x0000010000000080);
                if (!vulkan_graphics_timestamp_requires_synchronization2(high)) return 9;
                if (!vulkan_graphics_timestamp_requires_synchronization2(0)) return 10;
                if (!vulkan_graphics_timestamp_requires_synchronization2(UINT64_C(0x180))) return 11;
                if (vulkan_graphics_timestamp_requires_synchronization2(UINT64_C(0x80))) return 12;
                if (run_sync2(high, 1, 0)) return 13;
                if (run_sync2(0, 1, 0)) return 14;
                if (run_sync2(UINT64_C(0x180), 1, 0)) return 15;
                if (run_sync2(high, 0, -EOPNOTSUPP)) return 16;
                return 0;
            }}
            """
        )
        with tempfile.TemporaryDirectory(prefix="timestamp2-contract-") as tmp:
            source = Path(tmp) / "timestamp2_contract.c"
            binary = Path(tmp) / "timestamp2_contract"
            source.write_text(harness, encoding="utf-8")
            compiled = subprocess.run(
                [
                    "gcc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                    str(source), "-o", str(binary),
                ],
                text=True,
                capture_output=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(0, compiled.returncode, compiled.stderr)
            executed = subprocess.run(
                [str(binary)],
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )
            self.assertEqual(0, executed.returncode, executed.stderr)



if __name__ == "__main__":
    unittest.main()

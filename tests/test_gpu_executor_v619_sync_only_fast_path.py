#!/usr/bin/env python3
"""Source contracts for the executor V6.19 zero-command sync submit path."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "app/src/main/cpp/pdocker_gpu_executor.c"


def c_function(source: str, name: str) -> str:
    marker = f"{name}("
    start = source.find(marker)
    if start < 0:
        raise AssertionError(f"function not found: {name}")
    start = source.rfind("\n", 0, start) + 1
    brace = source.find("{", source.find(")", start))
    if brace < 0:
        raise AssertionError(f"function body not found: {name}")
    depth = 0
    state = "code"
    index = brace
    while index < len(source):
        char = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""
        if state == "line-comment":
            if char == "\n":
                state = "code"
        elif state == "block-comment":
            if char == "*" and following == "/":
                state = "code"
                index += 1
        elif state == "string":
            if char == "\\":
                index += 1
            elif char == '"':
                state = "code"
        elif state == "character":
            if char == "\\":
                index += 1
            elif char == "'":
                state = "code"
        elif char == "/" and following == "/":
            state = "line-comment"
            index += 1
        elif char == "/" and following == "*":
            state = "block-comment"
            index += 1
        elif char == '"':
            state = "string"
        elif char == "'":
            state = "character"
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
        index += 1
    raise AssertionError(f"unterminated function: {name}")


class VulkanV619SyncOnlyFastPathTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = SOURCE.read_text(encoding="utf-8")
        cls.predicate = c_function(
            cls.source, "vulkan_graphics_v619_frame_is_sync_only"
        )
        cls.fast_path = c_function(
            cls.source, "submit_vulkan_graphics_v619_sync_only"
        )
        cls.ordinary_path = c_function(
            cls.source, "submit_vulkan_graphics_v6_command_buffer"
        )
        cls.runner = c_function(cls.source, "run_vulkan_graphics_v6_frame")

    def test_only_zero_command_v619_frames_with_submit_sync_enter(self) -> None:
        self.assertIn("view->is_v619", self.predicate)
        self.assertIn("view->header->command_count == 0", self.predicate)
        self.assertIn("submit_sync_count > 0", self.predicate)

    def test_native_registry_objects_are_resolved_not_recreated_locally(self) -> None:
        self.assertIn("resolve_executor_submit_sync_semaphore(", self.fast_path)
        self.assertIn("resolve_executor_submit_sync_fence(", self.fast_path)
        for forbidden in (
            "vkCreateFence(",
            "vkDestroyFence(",
            "vkDestroySemaphore(",
        ):
            self.assertNotIn(forbidden, self.fast_path)

    def test_submit2_capability_failure_precedes_registry_mutation(self) -> None:
        capability = self.fast_path.index("queue-submit2-unavailable")
        first_resolve = self.fast_path.index(
            "resolve_executor_submit_sync_semaphore("
        )
        self.assertLess(capability, first_resolve)
        self.assertIn(
            "submit_info->submit_kind == "
            "PDOCKER_GPU_GRAPHICS_V621_SUBMIT_KIND_SUBMIT2",
            self.fast_path,
        )

    def test_submit_info_and_submit_info2_have_no_command_buffers(self) -> None:
        self.assertIn("vkQueueSubmit(", self.fast_path)
        self.assertIn("rt->queue_submit2(", self.fast_path)
        self.assertIn(".commandBufferCount = 0", self.fast_path)
        self.assertIn(".pCommandBuffers = NULL", self.fast_path)
        self.assertIn(".commandBufferInfoCount = 0", self.fast_path)
        self.assertIn(".pCommandBufferInfos = NULL", self.fast_path)

    def test_success_does_not_wait_or_fabricate_completion(self) -> None:
        for forbidden in (
            "vkWaitForFences(",
            "vkQueueWaitIdle(",
            "vkDeviceWaitIdle(",
            "submit_fence_entry->signaled = 1",
            "last_value",
        ):
            self.assertNotIn(forbidden, self.fast_path)
        self.assertIn("return submit_result == VK_SUCCESS ? 0 : -EIO", self.fast_path)

    def test_exact_native_submit_result_is_kept_in_diagnostics(self) -> None:
        self.assertIn("diag->vk_result = submit_result", self.fast_path)
        self.assertIn("diag->native_rc = submit_result == VK_SUCCESS", self.fast_path)
        self.assertIn('\\\"vk_result\\\":%d', self.runner)
        self.assertIn("submit_diag.native_rc", self.runner)

    def test_fast_path_precedes_all_replay_materialization(self) -> None:
        branch = self.runner.index("vulkan_graphics_v619_frame_is_sync_only(view)")
        replay_state = self.runner.index("VulkanGraphicsReplayPipeline replay_pipelines")
        self.assertLess(branch, replay_state)
        self.assertIn("submit_vulkan_graphics_v619_sync_only(", self.runner)

    def test_ordinary_command_submit_path_retains_completion_wait(self) -> None:
        self.assertIn(".commandBufferInfoCount = 1", self.ordinary_path)
        self.assertIn(".commandBufferCount = 1", self.ordinary_path)
        self.assertIn("vkWaitForFences(", self.ordinary_path)
        self.assertIn("vkQueueWaitIdle(", self.ordinary_path)
        self.assertNotIn(
            "submit_vulkan_graphics_v619_sync_only(", self.ordinary_path
        )


if __name__ == "__main__":
    unittest.main()

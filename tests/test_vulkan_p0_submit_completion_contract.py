#!/usr/bin/env python3
"""Red-first contracts for Vulkan submit completion and WSI ownership.

These tests are deliberately generic: they describe Vulkan transport and WSI
invariants, not a particular model, shader, or application.  They are also
test-only.  A failure identifies a production gap and must not be hidden with
an expected-failure annotation or a weaker shadow-state approximation.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXECUTOR_SOURCE = ROOT / "app/src/main/cpp/pdocker_gpu_executor.c"
ICD_SOURCE = ROOT / "docker-proot-setup/src/gpu/pdocker_vulkan_icd.c"


def _matching_delimiter(
    source: str, start: int, opening: str, closing: str
) -> int:
    if start >= len(source) or source[start] != opening:
        raise AssertionError(f"expected {opening!r} at offset {start}")
    depth = 0
    state = "code"
    offset = start
    while offset < len(source):
        char = source[offset]
        following = source[offset + 1] if offset + 1 < len(source) else ""
        if state == "line-comment":
            if char == "\n":
                state = "code"
        elif state == "block-comment":
            if char == "*" and following == "/":
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
        elif char == "/" and following == "/":
            state = "line-comment"
            offset += 1
        elif char == "/" and following == "*":
            state = "block-comment"
            offset += 1
        elif char == '"':
            state = "string"
        elif char == "'":
            state = "character"
        elif char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return offset
        offset += 1
    raise AssertionError(f"unterminated delimiter {opening!r} at {start}")


class CSource:
    def __init__(self, source: str) -> None:
        self.source = source

    def function(self, name: str) -> str:
        for match in re.finditer(rf"\b{re.escape(name)}\s*\(", self.source):
            open_paren = self.source.index("(", match.start())
            close_paren = _matching_delimiter(
                self.source, open_paren, "(", ")"
            )
            brace = close_paren + 1
            while brace < len(self.source) and self.source[brace].isspace():
                brace += 1
            if brace >= len(self.source) or self.source[brace] != "{":
                continue
            end = _matching_delimiter(self.source, brace, "{", "}") + 1
            start = self.source.rfind("\n\n", 0, match.start())
            return self.source[0 if start < 0 else start + 2 : end]
        raise AssertionError(f"C function definition not found: {name}")

    def struct(self, tag: str) -> str:
        marker = f"struct {tag}"
        start = self.source.find(marker)
        if start < 0:
            raise AssertionError(f"C struct not found: {tag}")
        brace = self.source.find("{", start + len(marker))
        end = _matching_delimiter(self.source, brace, "{", "}") + 1
        return self.source[start:end]


def _if_block(function: str, condition_pattern: str) -> tuple[str, int, int]:
    wanted = re.compile(condition_pattern, re.DOTALL)
    for match in re.finditer(r"\bif\s*\(", function):
        open_paren = function.index("(", match.start())
        close_paren = _matching_delimiter(function, open_paren, "(", ")")
        condition = function[open_paren + 1 : close_paren]
        if wanted.search(condition) is None:
            continue
        brace = close_paren + 1
        while brace < len(function) and function[brace].isspace():
            brace += 1
        if brace >= len(function) or function[brace] != "{":
            raise AssertionError(f"matching if is not braced: {condition}")
        end = _matching_delimiter(function, brace, "{", "}")
        return function[brace + 1 : end], brace + 1, end
    raise AssertionError(f"matching if not found: /{condition_pattern}/")


def _split_c_arguments(arguments: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depths = {"(": 0, "[": 0, "{": 0}
    pairs = {")": "(", "]": "[", "}": "{"}
    state = "code"
    offset = 0
    while offset < len(arguments):
        char = arguments[offset]
        following = arguments[offset + 1] if offset + 1 < len(arguments) else ""
        if state == "line-comment":
            if char == "\n":
                state = "code"
        elif state == "block-comment":
            if char == "*" and following == "/":
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
        elif char == "/" and following == "/":
            state = "line-comment"
            offset += 1
        elif char == "/" and following == "*":
            state = "block-comment"
            offset += 1
        elif char == '"':
            state = "string"
        elif char == "'":
            state = "character"
        elif char in depths:
            depths[char] += 1
        elif char in pairs:
            depths[pairs[char]] -= 1
        elif char == "," and all(value == 0 for value in depths.values()):
            parts.append(arguments[start:offset].strip())
            start = offset + 1
        offset += 1
    parts.append(arguments[start:].strip())
    return parts


def _calls(function: str, callee: str) -> list[list[str]]:
    calls: list[list[str]] = []
    for match in re.finditer(rf"\b{re.escape(callee)}\s*\(", function):
        opening = function.index("(", match.start())
        closing = _matching_delimiter(function, opening, "(", ")")
        calls.append(_split_c_arguments(function[opening + 1 : closing]))
    return calls


class VulkanTerminalAndDeadlineContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.executor_text = EXECUTOR_SOURCE.read_text(encoding="utf-8")
        cls.icd_text = ICD_SOURCE.read_text(encoding="utf-8")
        cls.executor = CSource(cls.executor_text)
        cls.icd = CSource(cls.icd_text)

    def test_sync_only_failure_cannot_bypass_correlated_terminal(self) -> None:
        runner = self.executor.function("run_vulkan_graphics_v6_frame")
        sync_only, _, _ = _if_block(
            runner, r"vulkan_graphics_v619_frame_is_sync_only\s*\(\s*view\s*\)"
        )
        failure, _, failure_end = _if_block(sync_only, r"\brc\s*!=\s*0")
        self.assertNotRegex(
            failure,
            r"\breturn\b",
            "a native sync-only failure must emit the correlated terminal "
            "vulkan-graphics-v6-replay record before returning",
        )
        terminal = sync_only.find(
            '\\"stage\\":\\"vulkan-graphics-v6-replay\\"',
            failure_end,
        )
        self.assertGreaterEqual(
            terminal,
            0,
            "all sync-only native outcomes must converge on a replay terminal",
        )

    def test_sync_only_terminal_carries_exact_vkresult_and_submit_id(self) -> None:
        runner = self.executor.function("run_vulkan_graphics_v6_frame")
        sync_only, _, _ = _if_block(
            runner, r"vulkan_graphics_v619_frame_is_sync_only\s*\(\s*view\s*\)"
        )
        terminal = sync_only.rfind(
            '\\"stage\\":\\"vulkan-graphics-v6-replay\\"'
        )
        self.assertGreaterEqual(terminal, 0, "sync-only terminal response missing")
        terminal_output = sync_only[terminal:]
        self.assertRegex(
            terminal_output,
            r'\\"(?:result|vk_result)\\":%d',
            "the terminal envelope must carry the exact native VkResult",
        )
        self.assertIn(
            "submit_diag.vk_result",
            terminal_output,
            "the terminal must serialize the native result, not infer success from rc",
        )
        self.assertIn(
            "view->header->submit_id",
            terminal_output,
            "the terminal must remain correlated to the submitted frame",
        )

    def test_icd_terminal_validator_requires_exact_vkresult(self) -> None:
        validator = self.icd.function(
            "dispatch_response_is_terminal_success"
        )
        self.assertRegex(
            validator,
            r'parse_executor_json_(?:i32|int)_key_exact\s*\('
            r'[^;]*(?:"result"|"vk_result")',
            "a replay terminal without one exact top-level VkResult must not be accepted",
        )

    def test_icd_response_read_has_one_finite_monotonic_deadline(self) -> None:
        response_read = self.icd.function("read_dispatch_response_status")
        self.assertNotIn("CLOCK_REALTIME", response_read)
        self.assertRegex(
            response_read,
            r"(?:monotonic_ns\s*\(|clock_gettime\s*\(\s*CLOCK_MONOTONIC|"
            r"pdocker_vk_executor_response_deadline_start\s*\()",
            "response timeout must be based on one monotonic deadline helper",
        )
        self.assertRegex(
            response_read,
            r"\b(?:absolute_)?deadline\w*\b",
            "response reads need one finite absolute deadline",
        )
        deadline_read = (
            response_read
            + "\n"
            + self.icd.function(
                "pdocker_vk_read_response_byte_before_deadline"
            )
        )
        self.assertRegex(
            deadline_read,
            r"-ETIMEDOUT|ETIMEDOUT",
            "deadline expiry must be distinguishable from malformed JSON and EOF",
        )
        raw_read = re.search(r"\bread\s*\(\s*socket_fd\b", response_read)
        if raw_read:
            before_read = response_read[: raw_read.start()]
            self.assertRegex(
                before_read,
                r"\b(?:poll|ppoll|select)\s*\(|"
                r"\b(?:read|recv)\w*with\w*deadline\s*\(",
                "a blocking socket read must be guarded by the remaining "
                "monotonic deadline",
            )


    def test_normal_v6_submit_publishes_enqueue_before_internal_wait(self) -> None:
        submit = self.executor.function(
            "submit_vulkan_graphics_v6_command_buffer"
        )
        runner = self.executor.function("run_vulkan_graphics_v6_frame")
        enqueue_at = submit.find("*out_native_enqueued = true;")
        wait_at = submit.find("vkWaitForFences(")
        self.assertGreaterEqual(enqueue_at, 0)
        self.assertGreater(
            wait_at,
            enqueue_at,
            "native enqueue state must be published before any internal completion wait",
        )
        self.assertRegex(
            submit,
            r"(?s)if\s*\(\s*vrc\s*!=\s*VK_SUCCESS\s*\).*"
            r"diag->vk_result\s*=\s*VK_ERROR_DEVICE_LOST",
            "a post-enqueue wait failure must become device loss, never success or a retryable timeout",
        )
        recovery_start = submit.find("wait-submit-recovery-queue-idle")
        recovery_return = submit.find("return -EIO;", recovery_start)
        self.assertGreaterEqual(recovery_start, 0)
        self.assertGreater(recovery_return, recovery_start)
        self.assertNotIn(
            "vkDestroyFence(",
            submit[recovery_start:recovery_return],
            "an unproven native completion must retain its private fence",
        )
        submit_call_at = runner.find(
            "submit_vulkan_graphics_v6_command_buffer("
        )
        retained_at = runner.find(
            "out_native_enqueued && *out_native_enqueued", submit_call_at
        )
        cleanup_at = runner.find(
            "free_vulkan_graphics_v6_replay_command_buffer(", retained_at
        )
        self.assertGreaterEqual(retained_at, 0)
        self.assertGreater(
            cleanup_at,
            retained_at,
            "pre-enqueue failures may clean up only after the in-flight retention branch",
        )
        self.assertIn(
            "return rc;",
            runner[retained_at:cleanup_at],
            "post-enqueue ambiguity must retain the whole replay graph",
        )
        calls = [
            args
            for args in _calls(
                runner, "submit_vulkan_graphics_v6_command_buffer"
            )
            if any("&g_vulkan_runtime" in arg for arg in args)
        ]
        self.assertEqual(1, len(calls))
        self.assertIn(
            "out_native_enqueued",
            "\n".join(calls[0]),
            "the executor handler must receive enqueue state directly from the native submit boundary",
        )

    def test_completion_unknown_quarantines_registered_sync_identities(self) -> None:
        submit = self.executor.function(
            "submit_vulkan_graphics_v6_command_buffer"
        )
        idle_failure, _, _ = _if_block(submit, r"idle_rc\s*!=\s*VK_SUCCESS")
        quarantine_at = idle_failure.find(
            "quarantine_executor_submit_sync_identities("
        )
        return_at = idle_failure.find("return -EIO;")
        self.assertGreaterEqual(quarantine_at, 0)
        self.assertGreater(return_at, quarantine_at)

        quarantine = self.executor.function(
            "quarantine_executor_submit_sync_identities"
        )
        self.assertIn("completion_unknown = 1", quarantine)
        self.assertIn("SUBMIT_SYNC_FENCE", quarantine)
        self.assertIn("SUBMIT_SYNC_WAIT", quarantine)
        self.assertIn("SUBMIT_SYNC_SIGNAL", quarantine)

        semaphore_handler = self.executor.function(
            "handle_vulkan_semaphore_command"
        )
        fence_handler = self.executor.function("handle_vulkan_fence_command")
        for name, handler, destroy_call in (
            ("semaphore", semaphore_handler, "vkDestroySemaphore("),
            ("fence", fence_handler, "vkDestroyFence("),
        ):
            with self.subTest(identity=name):
                branch, _, _ = _if_block(
                    handler, r"entry\s*&&\s*entry->completion_unknown"
                )
                self.assertIn("entry->destroy_pending = 1;", branch)
                self.assertNotIn(
                    destroy_call,
                    branch,
                    "quarantined native identities must never be destroyed",
                )
        self.assertRegex(
            fence_handler,
            r"entries\[i\]->completion_unknown\s*\|\|\s*"
            r"entries\[i\]->destroy_pending",
            "fence reset must reject quarantined or logically destroyed identities",
        )

    def test_generic_wrapper_resets_execution_state_before_clone_allocations(self) -> None:
        wrapper = self.icd.function("send_generic_vulkan_dispatch")
        reset_at = wrapper.find(
            "g_generic_dispatch_execution_state = "
            "PDOCKER_VK_DISPATCH_NOT_ENQUEUED;"
        )
        clone_at = wrapper.find("dispatch_op_clone_descriptor_state(")
        self.assertGreaterEqual(reset_at, 0)
        self.assertGreater(
            clone_at,
            reset_at,
            "wrapper-side OOM must not inherit a previous dispatch outcome",
        )

    def test_empty_bind_sparse_fence_requires_executor_acknowledgement(self) -> None:
        bind_sparse = self.icd.function("vkQueueBindSparse")
        signal_at = bind_sparse.find(
            "send_executor_fence_signal(submit_fence)"
        )
        shadow_at = bind_sparse.find(
            "submit_fence->signaled = true;", signal_at
        )
        self.assertGreaterEqual(signal_at, 0)
        self.assertGreater(
            shadow_at,
            signal_at,
            "bind-sparse fence shadow must follow executor acknowledgement",
        )
        self.assertIn(
            "return VK_ERROR_DEVICE_LOST;",
            bind_sparse[signal_at:shadow_at],
            "executor fence signal failure must be observable and sticky",
        )

    def test_segmented_submit_failures_are_not_retry_safe_after_side_effects(self) -> None:
        submit = self.icd.function("vkQueueSubmit")
        self.assertIn("bool queue_submit_irreversible = false;", submit)
        self.assertRegex(
            submit,
            r"(?s)return_rc_\s*!=\s*VK_SUCCESS\s*&&\s*queue_submit_irreversible"
            r".*return_rc_\s*=\s*VK_ERROR_DEVICE_LOST",
            "the common return path must collapse every post-side-effect failure to sticky device loss",
        )
        self.assertRegex(
            submit,
            r"(?s)if\s*\(\s*!submit_sync_entries\s*\)\s*\{"
            r".*queue_submit_irreversible.*VK_ERROR_DEVICE_LOST",
            "a later submit allocation failure must not expose retry-safe OOM after an earlier submit executed",
        )
        fence_signal_at = submit.find(
            "send_executor_fence_signal(submit_fence)"
        )
        success_at = submit.rfind("return VK_SUCCESS;")
        self.assertGreaterEqual(fence_signal_at, 0)
        self.assertIn(
            "return VK_ERROR_DEVICE_LOST;",
            submit[fence_signal_at:success_at],
            "final executor fence signaling failure must not be ignored",
        )
        self.assertLess(
            submit.find("submit_fence->signaled = true;", fence_signal_at),
            success_at,
            "the fence shadow may become signaled only after executor acknowledgement",
        )

        for checkpoint in (
            "submit_wait_sync_count > 0",
            "pre_wait_sync_count > 0",
            "deferred_completion_sync_count > 0",
            "submit_completion_sync_count > 0",
        ):
            with self.subTest(checkpoint=checkpoint):
                at = submit.find(checkpoint)
                self.assertGreaterEqual(at, 0)
                self.assertIn(
                    "queue_submit_irreversible = true",
                    submit[at : at + 500],
                    f"{checkpoint} must publish irreversible execution before later failure points",
                )

        validation_send = submit.find(
            "int graphics_rc = send_recorded_vulkan_graphics_v6_1_frame("
        )
        validation_irreversible = submit.rfind(
            "queue_submit_irreversible = true;", 0, validation_send
        )
        self.assertGreaterEqual(validation_send, 0)
        self.assertGreaterEqual(validation_irreversible, 0)
        self.assertLess(
            validation_send - validation_irreversible,
            600,
            "a validation producer with no enqueue output must enter an irreversible boundary before transport",
        )

        ordered_case = submit.index("case PDOCKER_VK_COMMAND_DISPATCH:")
        ordered_call = submit.index(
            "execute_recorded_dispatch_command_op(", ordered_case
        )
        ordered_state = submit.index(
            "pdocker_vk_generic_dispatch_may_have_executed()", ordered_call
        )
        self.assertNotIn(
            "queue_submit_irreversible = true;",
            submit[ordered_case:ordered_call],
            "generic dispatch must not poison exact pre-enqueue errors before its outcome is known",
        )
        self.assertLess(
            ordered_state,
            submit.index(
                "RETURN_VK_QUEUE_SUBMIT_WITH_SYNC(dispatch_rc)", ordered_call
            ),
            "enqueue/ambiguity state must be published before the common return path",
        )


class VulkanSubmitIdentityContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.executor_text = EXECUTOR_SOURCE.read_text(encoding="utf-8")
        cls.fast_path = CSource(cls.executor_text).function(
            "submit_vulkan_graphics_v619_sync_only"
        )

    def test_submit_semaphore_identity_is_fail_closed(self) -> None:
        calls = _calls(
            self.fast_path, "resolve_executor_submit_sync_semaphore"
        )
        self.assertGreaterEqual(len(calls), 2, "wait and signal resolution are required")
        for index, arguments in enumerate(calls):
            with self.subTest(call=index):
                self.assertEqual(
                    3,
                    len(arguments),
                    "the resolver API must not expose an auto-create mode",
                )

    def test_submit_fence_identity_is_fail_closed(self) -> None:
        calls = _calls(self.fast_path, "resolve_executor_submit_sync_fence")
        self.assertEqual(1, len(calls), "sync-only submit should resolve one fence")
        self.assertEqual(
            4,
            len(calls[0]),
            "the fence resolver API must not expose an auto-create mode",
        )

    def test_submit_path_never_allocates_native_sync_identity(self) -> None:
        for forbidden in (
            "allocate_executor_submit_sync_entry(",
            "allocate_executor_submit_fence_entry(",
            "vkCreateSemaphore(",
            "vkCreateFence(",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.fast_path)


class VulkanWsiPresentCompletionContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.icd_text = ICD_SOURCE.read_text(encoding="utf-8")
        cls.icd = CSource(cls.icd_text)
        cls.swapchain = cls.icd.struct("PdockerVkSwapchain")
        cls.present = cls.icd.function("vkQueuePresentKHR")
        cls.acquire = cls.icd.function("pdocker_vk_swapchain_acquire_wait")
        cls.available = cls.icd.function(
            "pdocker_vk_swapchain_find_available_locked"
        )

    def test_swapchain_tracks_per_image_native_present_completion(self) -> None:
        self.assertRegex(
            self.swapchain,
            r"(?i)\b(?:VkFence|uint64_t|bool|int|enum\s+\w+)\s+"
            r"\w*(?:present|completion|pending|ticket|fence)\w*\s*"
            r"\[\s*PDOCKER_VK_MAX_SWAPCHAIN_IMAGES\s*\]",
            "each image needs explicit native-present completion state; "
            "the acquired bool alone cannot distinguish queued from completed",
        )

    def test_present_release_follows_native_completion_observation(self) -> None:
        enqueue = self.present.find("send_executor_wsi_present_wait(")
        self.assertGreaterEqual(enqueue, 0, "native present wait submit is missing")
        self.assertIn(
            "present_pending[image_index] = true",
            self.present[enqueue:],
            "enqueue must move the image to explicit PRESENT_PENDING state",
        )
        self.assertNotRegex(
            self.present[enqueue:],
            r"\bacquired\s*\[\s*image_index\s*\]\s*=\s*false\s*;",
            "vkQueuePresentKHR may not confuse enqueue acknowledgement with "
            "native completion",
        )
        observer = self.icd.function(
            "pdocker_vk_present_completion_observe_locked"
        )
        self.assertIn("native_complete = true", observer)
        self.assertRegex(
            observer,
            r"present_pending\s*\[\s*i\s*\]\s*=\s*false",
        )
        self.assertRegex(
            observer,
            r"acquired\s*\[\s*i\s*\]\s*=\s*false",
            "only the native completion observer may return the image",
        )

    def test_acquire_excludes_pending_present_images_until_reaped(self) -> None:
        acquire_path = self.acquire + "\n" + self.available
        self.assertRegex(
            acquire_path,
            r"(?i)present\w*(?:pending|completion|fence|ticket)|"
            r"(?:pending|completion|fence|ticket)\w*present",
            "availability selection must account for pending native presentation",
        )
        self.assertRegex(
            acquire_path,
            r"(?i)\b\w*(?:observe|reap|poll|status|wait)\w*"
            r"(?:completion|fence|ticket)\w*\s*\(",
            "acquire must observe/reap native completion before reusing an image",
        )

    @unittest.skipUnless(shutil.which("gcc"), "gcc is required for the WSI oracle")
    def test_compiled_present_completion_state_oracle(self) -> None:
        harness = textwrap.dedent(
            r"""
            #include <assert.h>
            #include <stdbool.h>

            typedef enum {
                AVAILABLE,
                ACQUIRED,
                PRESENT_PENDING
            } ImageState;

            typedef struct {
                ImageState state;
                bool native_complete;
            } Image;

            static bool try_acquire(Image *image) {
                if (image->state == PRESENT_PENDING && image->native_complete) {
                    image->state = AVAILABLE;
                }
                if (image->state != AVAILABLE) return false;
                image->state = ACQUIRED;
                return true;
            }

            static void queue_present(Image *image) {
                assert(image->state == ACQUIRED);
                image->state = PRESENT_PENDING;
                image->native_complete = false;
            }

            int main(void) {
                Image image = { ACQUIRED, false };
                queue_present(&image);
                assert(!try_acquire(&image));
                assert(image.state == PRESENT_PENDING);
                image.native_complete = true;
                assert(try_acquire(&image));
                assert(image.state == ACQUIRED);
                return 0;
            }
            """
        )
        with tempfile.TemporaryDirectory(prefix="vulkan-wsi-completion-") as tmp:
            source = Path(tmp) / "completion_oracle.c"
            binary = Path(tmp) / "completion_oracle"
            source.write_text(harness, encoding="utf-8")
            compiled = subprocess.run(
                [
                    "gcc",
                    "-std=c11",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    str(source),
                    "-o",
                    str(binary),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(0, compiled.returncode, compiled.stderr)
            executed = subprocess.run(
                [str(binary)], text=True, capture_output=True, check=False
            )
            self.assertEqual(0, executed.returncode, executed.stderr)


if __name__ == "__main__":
    unittest.main()

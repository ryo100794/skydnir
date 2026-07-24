#!/usr/bin/env python3
"""Khronos behavioral contracts for the headless Vulkan WSI path.

This test-only file intentionally specifies observable Vulkan behavior rather
than the current implementation shape.  Static checks are used where linking
the complete ICD would make the test depend on Android/Vulkan build tooling;
the state-transition oracle is compiled and executed as C so the expected
resource and synchronization transitions remain explicit.

A red test is a production gap.  Do not make these tests green by weakening
the required ordering or by treating an ICD-only state change as native queue
execution.
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
ICD_SOURCE = ROOT / "docker-proot-setup/src/gpu/pdocker_vulkan_icd.c"


def _matching_delimiter(
    source: str, start: int, opening: str, closing: str
) -> int:
    """Return the matching C delimiter while ignoring comments/literals."""
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
        else:
            if char == "/" and following == "/":
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
    raise AssertionError(f"unterminated C delimiter {opening!r} at {start}")


class CSource:
    """Brace-aware function extraction for implementation-independent gates."""

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


def _enclosing_if_conditions(function: str, target_offset: int) -> list[str]:
    """Return braced if conditions that enclose a source offset."""
    conditions: list[str] = []
    for match in re.finditer(r"\bif\s*\(", function):
        open_paren = function.index("(", match.start())
        close_paren = _matching_delimiter(function, open_paren, "(", ")")
        brace = close_paren + 1
        while brace < len(function) and function[brace].isspace():
            brace += 1
        if brace >= len(function) or function[brace] != "{":
            continue
        end = _matching_delimiter(function, brace, "{", "}")
        if brace < target_offset < end:
            conditions.append(function[open_paren + 1 : close_paren])
    return conditions


def _if_block(function: str, condition_pattern: str) -> str:
    """Return the complete braced body of the first matching C if."""
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
            raise AssertionError(
                f"matching if is not braced: {condition.strip()}"
            )
        end = _matching_delimiter(function, brace, "{", "}")
        return function[brace + 1 : end]
    raise AssertionError(f"matching if not found: /{condition_pattern}/")


def _device_loss_check_offset(function: str) -> int:
    """Find an explicit sticky-device-loss read/check in a function."""
    patterns = (
        r"\b[A-Za-z_]\w*(?:device\w*(?:lost|loss)|(?:lost|loss)\w*device)\w*\s*\(",
        r"(?:->|\.)\s*[A-Za-z_]\w*(?:lost|loss)\w*(?!\s*=)",
    )
    offsets = [
        match.start()
        for pattern in patterns
        for match in re.finditer(pattern, function, re.IGNORECASE)
    ]
    return min(offsets) if offsets else -1


def _retirement_offset(function: str) -> int:
    patterns = (
        r"\bold_swapchain\s*->\s*(?:retired|acquire_retired)\s*=\s*true\s*;",
        r"\b[A-Za-z_]\w*(?:retire|replace)\w*\s*\(\s*old_swapchain\b",
    )
    offsets = [
        match.start()
        for pattern in patterns
        for match in re.finditer(pattern, function, re.IGNORECASE)
    ]
    return min(offsets) if offsets else -1


class VulkanWsiSpecContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = ICD_SOURCE.read_text(encoding="utf-8")
        cls.c = CSource(cls.text)

    @unittest.skipUnless(shutil.which("gcc"), "gcc is required for the WSI state oracle")
    def test_compiled_present_state_transition_oracle(self) -> None:
        """Lock down queue/resource effects for result and pre-enqueue errors."""
        harness = textwrap.dedent(
            r"""
            #include <assert.h>
            #include <stdbool.h>

            enum {
                VK_SUCCESS = 0,
                VK_ERROR_OUT_OF_HOST_MEMORY = -1,
                VK_ERROR_SURFACE_LOST_KHR = -1000000000,
                VK_ERROR_OUT_OF_DATE_KHR = -1000001004
            };

            typedef struct {
                bool acquired;
                bool present_pending;
                bool native_complete;
                unsigned enqueue_calls;
                unsigned wait_consumptions;
            } State;

            static int present_contract(
                    State *state, int per_swapchain_result, bool oom_before_enqueue) {
                if (oom_before_enqueue) return VK_ERROR_OUT_OF_HOST_MEMORY;
                state->enqueue_calls++;
                state->wait_consumptions++;
                state->present_pending = true;
                return per_swapchain_result;
            }

            static void assert_present_error_still_executes(int error) {
                State state = { true, false, false, 0, 0 };
                assert(present_contract(&state, error, false) == error);
                assert(state.enqueue_calls == 1);
                assert(state.wait_consumptions == 1);
                assert(state.acquired);
                assert(state.present_pending);
                state.native_complete = true;
                state.present_pending = false;
                state.acquired = false;
                assert(!state.acquired);
            }

            int main(void) {
                assert_present_error_still_executes(VK_ERROR_OUT_OF_DATE_KHR);
                assert_present_error_still_executes(VK_ERROR_SURFACE_LOST_KHR);

                State oom = { true, false, false, 0, 0 };
                assert(present_contract(
                    &oom, VK_SUCCESS, true) == VK_ERROR_OUT_OF_HOST_MEMORY);
                assert(oom.enqueue_calls == 0);
                assert(oom.wait_consumptions == 0);
                assert(oom.acquired);
                return 0;
            }
            """
        )
        with tempfile.TemporaryDirectory(prefix="vulkan-wsi-spec-") as tmp:
            source = Path(tmp) / "wsi_oracle.c"
            binary = Path(tmp) / "wsi_oracle"
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
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(0, compiled.returncode, compiled.stderr)
            executed = subprocess.run(
                [str(binary)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(0, executed.returncode, executed.stderr)

    def test_old_swapchain_retires_before_any_later_creation_failure(self) -> None:
        create = self.c.function("vkCreateSwapchainKHR")
        retirement = _retirement_offset(create)
        self.assertGreaterEqual(
            retirement,
            0,
            "vkCreateSwapchainKHR must explicitly retire a valid oldSwapchain",
        )

        old_surface_check = create.find("old_swapchain->surface != surface")
        self.assertGreaterEqual(old_surface_check, 0, "oldSwapchain surface validation missing")
        self.assertLess(
            retirement,
            old_surface_check,
            "a valid oldSwapchain is retired by the call before any later "
            "surface mismatch or replacement validation failure",
        )

        later_failure_boundaries = {
            "unsupported format": create.find(
                "pdocker_vk_headless_surface_format_supported"
            ),
            "image-count OOM": create.find(
                "image_count > PDOCKER_VK_MAX_SWAPCHAIN_IMAGES"
            ),
            "swapchain allocation": create.find(
                "pdocker_alloc_handle(sizeof(*swapchain))"
            ),
            "image creation": create.find("vkCreateImage("),
        }
        for reason, failure_boundary in later_failure_boundaries.items():
            with self.subTest(later_failure=reason):
                self.assertGreaterEqual(
                    failure_boundary,
                    0,
                    f"expected {reason} path is missing from swapchain creation",
                )
                self.assertLess(
                    retirement,
                    failure_boundary,
                    "a valid oldSwapchain is retired by the create call even "
                    f"when the later {reason} path fails",
                )

    def test_present_errors_still_enqueue_waits_and_release_acquired_images(self) -> None:
        present = self.c.function("vkQueuePresentKHR")
        observer = self.c.function(
            "pdocker_vk_present_completion_observe_locked"
        )
        classify = present.find("pdocker_vk_present_image_result(")
        enqueue = present.find("send_executor_wsi_present_wait(")
        pending = present.find("present_pending[image_index] = true")
        self.assertGreaterEqual(classify, 0, "per-swapchain present result is not computed")
        self.assertGreater(enqueue, classify, "wait enqueue must follow target validation")
        self.assertGreater(
            pending,
            enqueue,
            "successful enqueue must enter PRESENT_PENDING before return",
        )
        self.assertNotRegex(
            present[enqueue:],
            r"\bacquired\s*\[\s*image_index\s*\]\s*=\s*false\s*;",
            "enqueue acknowledgement is not native completion",
        )
        self.assertIn("native_complete = true", observer)
        self.assertRegex(
            observer,
            r"acquired\s*\[\s*i\s*\]\s*=\s*false",
            "native completion observation must release the image",
        )

        early = present[classify:enqueue]
        self.assertIsNone(
            re.search(
                r"if\s*\([^)]*aggregate[^)]*\)\s*return\s+aggregate\s*;",
                early,
            ),
            "OUT_OF_DATE/SURFACE_LOST is a present result, not permission to skip "
            "queueing and consuming wait semaphores",
        )
        enqueue_guards = _enclosing_if_conditions(present, enqueue)
        self.assertFalse(
            any(
                re.search(r"aggregate\s*==\s*VK_SUCCESS", guard)
                for guard in enqueue_guards
            ),
            "native present wait enqueue must not be gated on aggregate success",
        )

    def test_pre_enqueue_oom_leaves_sync_and_acquired_state_untouched(self) -> None:
        present = self.c.function("vkQueuePresentKHR")
        enqueue = present.find("send_executor_wsi_present_wait(")
        pending = present.find("present_pending[image_index] = true")
        self.assertGreaterEqual(enqueue, 0, "native present wait enqueue is missing")
        self.assertGreater(
            pending,
            enqueue,
            "local ownership state may change only after enqueue succeeds",
        )
        self.assertLess(
            present.find("pdocker_vk_present_completion_create("),
            enqueue,
            "fallible completion bookkeeping must finish before enqueue",
        )

        failure_body = _if_block(
            present, r"^\s*native_result\s*!=\s*VK_SUCCESS\s*$"
        )
        self.assertRegex(
            failure_body,
            r"return\s+native_result\s*;",
            "pre-enqueue/native rejection must be returned without committing WSI state",
        )
        self.assertNotRegex(
            failure_body,
            r"present_pending\s*\[[^]]+\]\s*=\s*true|"
            r"acquired\s*\[[^]]+\]\s*=\s*false",
            "failed enqueue must leave image ownership state untouched",
        )

    def test_acquire_fence_status_transport_failure_is_sticky_device_lost(self) -> None:
        validate = self.c.function("pdocker_vk_acquire_sync_valid")
        acquire = self.c.function("vkAcquireNextImageKHR")
        self.assertRegex(
            validate.split("{", 1)[0],
            r"\bVkResult\s+pdocker_vk_acquire_sync_valid\s*\(",
            "acquire sync validation must preserve transport failure separately "
            "from an invalid unsignaled-fence precondition",
        )
        self.assertIn("send_executor_fence_status", validate)
        self.assertIn(
            "VK_ERROR_DEVICE_LOST",
            validate,
            "fence-status transport failure must map to VK_ERROR_DEVICE_LOST",
        )
        self.assertRegex(
            validate,
            r"\b[A-Za-z_]\w*(?:device\w*(?:lost|loss)|(?:lost|loss)\w*device)\w*\s*\(",
            "fence-status transport failure must poison the logical device",
        )
        self.assertRegex(
            acquire,
            r"VkResult\s+\w+\s*=\s*pdocker_vk_acquire_sync_valid\s*\(",
            "vkAcquireNextImageKHR must propagate the precise sync validation result",
        )

    def test_finite_acquire_uses_one_monotonic_deadline(self) -> None:
        wait = self.c.function("pdocker_vk_swapchain_acquire_wait")
        self.assertNotIn(
            "CLOCK_REALTIME",
            wait,
            "finite acquire deadlines must not be converted through realtime",
        )
        self.assertIn("CLOCK_MONOTONIC", self.text)
        self.assertRegex(
            self.text,
            r"pthread_condattr_setclock\s*\([^;]*CLOCK_MONOTONIC",
            "the WSI condition variable must use CLOCK_MONOTONIC",
        )
        deadline_definitions = re.findall(
            r"\b(?:const\s+)?uint64_t\s+\w*deadline\w*\s*=", wait
        )
        self.assertEqual(
            1,
            len(deadline_definitions),
            "finite acquire must derive one absolute monotonic deadline",
        )
        self.assertNotRegex(
            wait,
            r"wait_slice|remaining[^;]*1000000000|clock_gettime\s*\(\s*CLOCK_REALTIME",
            "timed waits must reuse the one absolute monotonic deadline, not "
            "rebuild realtime slices",
        )
        self.assertRegex(
            wait,
            r"pthread_cond_timedwait\s*\([^;]*deadline",
            "pthread_cond_timedwait must receive the monotonic absolute deadline",
        )

    def test_wsi_and_queue_submit_check_sticky_loss_before_executor_io(self) -> None:
        cases = {
            "vkCreateSwapchainKHR": (
                "vkCreateImage(",
                "swapchain creation can reach executor-backed image creation",
            ),
            "vkAcquireNextImageKHR": (
                "pdocker_vk_acquire_sync_valid(",
                "acquire validation can query native fence status",
            ),
            "vkQueuePresentKHR": (
                "send_executor_wsi_present_wait(",
                "present enqueues native wait semaphores",
            ),
            "vkQueueSubmit": (
                "send_vulkan_submit_sync_only_frame_result(",
                "queue submit can issue executor transport",
            ),
        }
        for api, (first_io_token, description) in cases.items():
            with self.subTest(api=api):
                body = self.c.function(api)
                loss_check = _device_loss_check_offset(body)
                io = body.find(first_io_token)
                self.assertGreaterEqual(io, 0, description)
                self.assertGreaterEqual(
                    loss_check,
                    0,
                    f"{api} must read sticky device-loss state at entry",
                )
                self.assertLess(
                    loss_check,
                    io,
                    f"{api} must return VK_ERROR_DEVICE_LOST before executor I/O",
                )
                self.assertIn(
                    "VK_ERROR_DEVICE_LOST",
                    body[:io],
                    f"{api} sticky check must return VK_ERROR_DEVICE_LOST",
                )


if __name__ == "__main__":
    unittest.main()

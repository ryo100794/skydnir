#!/usr/bin/env python3
"""P0 static contracts for Vulkan WSI synchronization correctness.

This is a test-only sidecar specification.  It deliberately describes the
required acquire -> submit -> present behavior rather than preserving the
current headless ICD implementation.  A red test names a production gap; the
contract must not be weakened by accepting ICD-only semaphore state changes.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ICD_SOURCE = ROOT / "docker-proot-setup/src/gpu/pdocker_vulkan_icd.c"
EXECUTOR_SOURCE = ROOT / "app/src/main/cpp/pdocker_gpu_executor.c"


def _matching_delimiter(
    source: str, start: int, opening: str, closing: str
) -> int:
    """Return the matching C delimiter, ignoring comments and literals."""
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
    """Brace-aware extraction and shallow call-graph helpers for C source."""

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

    def closure(self, root: str, max_depth: int = 4) -> str:
        """Return root plus locally defined callees, bounded to avoid noise."""
        c_keywords = {"if", "for", "while", "switch", "sizeof", "return"}
        pending = [(root, 0)]
        visited: set[str] = set()
        chunks: list[str] = []
        while pending:
            name, depth = pending.pop()
            if name in visited or depth > max_depth:
                continue
            visited.add(name)
            try:
                body = self.function(name)
            except AssertionError:
                continue
            chunks.append(body)
            if depth == max_depth:
                continue
            for called in re.findall(r"\b([A-Za-z_]\w*)\s*\(", body):
                if called not in c_keywords and called not in visited:
                    pending.append((called, depth + 1))
        return "\n".join(chunks)


def _enclosing_if_conditions(function: str, target: str) -> list[str]:
    """Return conditions of braced if-statements enclosing a unique target."""
    target_offset = function.find(target)
    if target_offset < 0:
        raise AssertionError(f"target missing from function: {target}")
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


class VulkanWsiSyncContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.icd_text = ICD_SOURCE.read_text(encoding="utf-8")
        cls.executor_text = EXECUTOR_SOURCE.read_text(encoding="utf-8")
        cls.icd = CSource(cls.icd_text)

    def assert_contains(self, text: str, needle: str, message: str) -> None:
        if needle not in text:
            self.fail(f"{message} [missing {needle!r}]")

    def assert_not_contains(self, text: str, needle: str, message: str) -> None:
        if needle in text:
            self.fail(f"{message} [found {needle!r}]")

    def assert_matches(self, text: str, pattern: str, message: str) -> None:
        if re.search(pattern, text, re.DOTALL) is None:
            self.fail(f"{message} [missing /{pattern}/]")

    def assert_not_matches(self, text: str, pattern: str, message: str) -> None:
        match = re.search(pattern, text, re.DOTALL)
        if match is not None:
            self.fail(f"{message} [matched {match.group(0)!r}]")

    def assert_native_wsi_action(self, entry_point: str, action: str) -> None:
        """Require an action-specific ICD transport and executor handler."""
        closure = self.icd.closure(entry_point)
        action_pattern = rf"(?i)\b[A-Za-z0-9_]*(?:wsi|swapchain)[A-Za-z0-9_]*{action}[A-Za-z0-9_]*\b|\b[A-Za-z0-9_]*{action}[A-Za-z0-9_]*(?:wsi|swapchain)[A-Za-z0-9_]*\b"
        has_action_identity = re.search(action_pattern, closure) is not None
        has_transport = re.search(
            r"\b(?:send_executor\w*|sendmsg_exact_with_fds|connect_executor_socket)\s*\(",
            closure,
        ) is not None
        self.assertTrue(
            has_action_identity and has_transport,
            f"{entry_point} must send an action-specific native {action} request "
            "instead of completing WSI only in ICD memory",
        )

        executor_handler = re.search(
            rf"(?is)(?:strncmp\s*\([^\n]*{action}|case\s+[^:\n]*{action}|"
            rf"vk{action}[A-Za-z0-9_]*\s*\().{{0,1200}}(?:swapchain|wsi|semaphore)",
            self.executor_text,
        )
        # A dedicated WSI opcode is not required when the ICD lowers the
        # headless acquire/present operation onto the negotiated V6.19
        # submit-sync ABI. The executor must still resolve that same native
        # semaphore identity and execute an actual native queue submission.
        sync_kind = (
            "PDOCKER_GPU_GRAPHICS_V619_SUBMIT_SYNC_SIGNAL"
            if action == "acquire"
            else "PDOCKER_GPU_GRAPHICS_V619_SUBMIT_SYNC_WAIT"
        )
        generic_submit_sync = (
            (
                "send_vulkan_submit_sync_only_frame(" in closure
                or "send_vulkan_submit_sync_only_frame_result(" in closure
            )
            and sync_kind in closure
            and "resolve_executor_submit_sync_semaphore(" in self.executor_text
            and sync_kind in self.executor_text
            and "vkQueueSubmit(" in self.executor_text
        )
        self.assertTrue(
            executor_handler is not None or generic_submit_sync,
            f"executor must execute native semaphore synchronization for WSI {action}",
        )

    def test_acquire_completion_is_native_not_icd_only(self) -> None:
        acquire = self.icd.function("vkAcquireNextImageKHR")
        with self.subTest(contract="native acquire control"):
            self.assert_native_wsi_action("vkAcquireNextImageKHR", "acquire")
        with self.subTest(contract="no local binary semaphore signal"):
            self.assert_not_contains(
                acquire,
                "semaphore_complete_signal(",
                "acquire must not signal a binary semaphore only in ICD state",
            )
        with self.subTest(contract="no local fence completion"):
            self.assert_not_matches(
                acquire,
                r"\b\w+\s*->\s*signaled\s*=\s*true\s*;",
                "acquire fence completion must come from the native executor result",
            )

    def test_submit_transports_the_same_native_semaphore_identity(self) -> None:
        append = self.icd.function("append_submit_sync_entry")
        legacy = self.icd.function("collect_legacy_submit_sync_entries")
        submit2 = self.icd.function("collect_submit2_submit_sync_entries")
        submit = self.icd.function("vkQueueSubmit")
        with self.subTest(contract="stable executor semaphore id"):
            self.assert_matches(
                append,
                r"entry->semaphore_id\s*=\s*sem->semaphore_id\s*;",
                "queue submit must transport the semaphore identity created in the executor",
            )
        for name, collector in (("legacy", legacy), ("submit2", submit2)):
            with self.subTest(api=name):
                self.assert_contains(
                    collector,
                    "append_submit_sync_entry(",
                    f"{name} submits must encode wait/signal semaphores for executor replay",
                )
        with self.subTest(contract="executor consumes submit sync metadata"):
            self.assert_matches(
                submit,
                r"send_(?:vulkan_)?submit_sync\w*\s*\(|send_\w*graphics\w*frame\w*\s*\(",
                "vkQueueSubmit must deliver semaphore waits/signals to native execution",
            )

    def test_present_wait_is_native_not_icd_only(self) -> None:
        present = self.icd.function("vkQueuePresentKHR")
        with self.subTest(contract="native present control"):
            self.assert_native_wsi_action("vkQueuePresentKHR", "present")
        with self.subTest(contract="no ICD-only wait test"):
            self.assert_not_contains(
                present,
                "semaphore_wait_satisfied(",
                "present must pass wait semaphores to the executor, not poll ICD shadow state",
            )
        with self.subTest(contract="shadow state follows native consumption"):
            native_at = present.find("send_executor_wsi_present_wait(")
            shadow_at = present.find("semaphore_complete_wait(")
            self.assertGreaterEqual(
                native_at,
                0,
                "present must enqueue waits through the executor",
            )
            self.assertGreater(
                shadow_at,
                native_at,
                "ICD shadow state may be updated only after native wait consumption succeeds",
            )


    def test_acquire_timeout_keeps_zero_finite_and_infinite_distinct(self) -> None:
        acquire = self.icd.function("vkAcquireNextImageKHR")
        closure = self.icd.closure("vkAcquireNextImageKHR")
        with self.subTest(timeout="not discarded"):
            self.assert_not_matches(
                acquire,
                r"\(\s*void\s*\)\s*timeout\s*;",
                "acquire timeout is observable Vulkan behavior and cannot be discarded",
            )
        with self.subTest(timeout="zero is nonblocking"):
            self.assert_matches(
                closure,
                r"timeout\s*==\s*0[^;{}]*|0\s*==\s*timeout",
                "timeout=0 must remain a nonblocking acquire returning VK_NOT_READY when unavailable",
            )
        with self.subTest(timeout="infinite is not finite"):
            self.assert_matches(
                closure,
                r"timeout\s*==\s*UINT64_MAX|UINT64_MAX\s*==\s*timeout|"
                r"(?:wsi|swapchain)_?acquire\w*\s*\([^;]*\btimeout\b",
                "UINT64_MAX must wait indefinitely (or be forwarded unchanged to native acquire)",
            )
        with self.subTest(timeout="finite has a deadline"):
            self.assert_matches(
                closure,
                r"deadline|expired|remaining|poll|"
                r"(?:wsi|swapchain)_?acquire\w*\s*\([^;]*\btimeout\b",
                "a finite nonzero timeout must wait only until its deadline",
            )

    def test_old_swapchain_retirement_is_call_scoped(self) -> None:
        swapchain = self.icd.struct("PdockerVkSwapchain")
        create = self.icd.function("vkCreateSwapchainKHR")
        acquire_closure = self.icd.closure("vkAcquireNextImageKHR")
        with self.subTest(contract="retirement is distinct from destruction"):
            self.assert_matches(
                swapchain,
                r"\b(?:bool|VkBool32)\s+(?:retired|acquire_retired)\s*;",
                "oldSwapchain needs explicit retired state; it is not immediately destroyed",
            )
        surface_check = create.find("old_swapchain->surface != surface")
        retirement = create.find("old_swapchain->retired = true")
        later_validation = create.find(
            "pdocker_vk_headless_surface_format_supported"
        )
        with self.subTest(contract="retire before later creation failures"):
            self.assertLess(retirement, surface_check)
            self.assertLess(
                retirement,
                later_validation,
                "supplying a valid oldSwapchain retires it even when replacement creation later fails",
            )
        with self.subTest(contract="retired swapchain cannot be acquired"):
            self.assert_matches(
                acquire_closure,
                r"\b(?:retired|acquire_retired)\b",
                "vkAcquireNextImageKHR must reject a retired oldSwapchain",
            )


    def test_swapchain_extension_advertisement_is_wsi_capability_gated(self) -> None:
        collect = self.icd.function("collect_advertised_device_extensions")
        target = (
            "ADD_DEVICE_EXTENSION(VK_KHR_SWAPCHAIN_EXTENSION_NAME, "
            "VK_KHR_SWAPCHAIN_SPEC_VERSION)"
        )
        conditions = _enclosing_if_conditions(collect, target)
        wsi_capability_conditions = [
            condition
            for condition in conditions
            if re.search(r"(?i)(?:swapchain|wsi)", condition)
            and re.search(r"\b(?:snapshot|caps)\b", condition)
        ]
        self.assertTrue(
            wsi_capability_conditions,
            "VK_KHR_swapchain must be advertised only when the immutable executor "
            "capability snapshot reports native acquire/present support",
        )


    def test_post_fork_condition_domains_switch_to_preinitialized_spares(self) -> None:
        register = self.icd.function("register_gpu_endpoint_atfork")
        prepare = self.icd.function("gpu_endpoint_atfork_prepare")
        child = self.icd.function("gpu_endpoint_atfork_child")
        wsi_reset = self.icd.function("pdocker_vk_wsi_cond_atfork_child_reset")
        caps_reset = self.icd.function("advertised_caps_cache_atfork_child_reset")
        wsi_prepare = self.icd.function("pdocker_vk_wsi_cond_prepare_spare_locked")
        caps_prepare = self.icd.function("advertised_caps_cond_prepare_spare_locked")
        wsi_lock = self.icd.function("pdocker_vk_wsi_lock")
        caps_lock = self.icd.function("advertised_caps_lock")

        with self.subTest(contract="registration initializes both WSI generations first"):
            self.assert_matches(
                register,
                r"pdocker_vk_wsi_cond_ensure\s*\(\).*pthread_atfork\s*\(",
                "atfork registration must happen only after the active and spare WSI conditions exist",
            )
            self.assert_contains(
                register,
                "g_gpu_endpoint_atfork_status = rc;",
                "pthread_atfork registration status must remain observable",
            )

        for name, reset_name, reset, current, spare in (
            (
                "wsi",
                "pdocker_vk_wsi_cond_atfork_child_reset",
                wsi_reset,
                "g_wsi_cond_current",
                "g_wsi_cond_spare",
            ),
            (
                "capability-cache",
                "advertised_caps_cache_atfork_child_reset",
                caps_reset,
                "g_advertised_caps_cond_current",
                "g_advertised_caps_cond_spare",
            ),
        ):
            with self.subTest(domain=name, contract="pointer-only child reset"):
                self.assert_contains(
                    reset,
                    f"{current} = {spare};",
                    "the child must switch to a condition initialized before fork",
                )
                self.assert_contains(
                    reset,
                    f"{spare} = NULL;",
                    "the consumed spare must be replenished by an ordinary child API call",
                )
                self.assert_not_matches(
                    reset,
                    r"pthread_[A-Za-z0-9_]+\s*\(|\b(?:malloc|calloc|realloc|free|memset)\s*\(",
                    "the child reset must not allocate, initialize, destroy, or overwrite pthread objects",
                )
                self.assert_contains(
                    child,
                    reset_name + "();",
                    "the registered child hook must switch every condition domain",
                )

        for name, prepare_name, prepare_body, current in (
            (
                "wsi",
                "pdocker_vk_wsi_cond_prepare_spare_locked",
                wsi_prepare,
                "g_wsi_cond_current",
            ),
            (
                "capability-cache",
                "advertised_caps_cond_prepare_spare_locked",
                caps_prepare,
                "g_advertised_caps_cond_current",
            ),
        ):
            with self.subTest(domain=name, contract="every fork replenishes a spare"):
                self.assert_contains(
                    prepare,
                    prepare_name + "();",
                    "the parent prepare hook must replenish a child-consumable generation before every fork",
                )
                self.assert_matches(
                    prepare_body,
                    rf"if\s*\(\s*!{current}\s*\).*{current}\s*=\s*replacement",
                    "an ordinary child API must recover after a failed or repeatedly consumed spare",
                )

        for name, lock in (("wsi", wsi_lock), ("capability-cache", caps_lock)):
            with self.subTest(domain=name, contract="registration failure is fail-closed"):
                self.assert_contains(
                    lock,
                    "g_gpu_endpoint_atfork_status",
                    "ordinary API locking must reject failed atfork registration",
                )
                self.assert_matches(
                    lock,
                    r"prepare_spare_locked\s*\(\)",
                    "ordinary child API execution must replenish the next fork generation under its mutex",
                )

    def test_acquire_rebuilds_native_completion_wait_set_on_a_bounded_slice(self) -> None:
        acquire_wait = self.icd.function("pdocker_vk_swapchain_acquire_wait")
        with self.subTest(contract="wait-any covers all pending completions"):
            self.assert_contains(
                acquire_wait,
                "pdocker_vk_wait_present_completion_any(",
                "acquire must wait the native completion set rather than one arbitrarily selected image",
            )
            self.assert_matches(
                acquire_wait,
                r"for\s*\(\s*uint32_t\s+i\s*=\s*0;.*present_completion\[i\]",
                "the wait set must be rebuilt from the current pending image set",
            )
        with self.subTest(contract="native wait is bounded"):
            self.assert_contains(
                acquire_wait,
                "const uint64_t recheck_slice_ns = 16000000ull;",
                "a bounded native slice is required so WSI state changes can rebuild a stale wait set",
            )
            self.assert_matches(
                acquire_wait,
                r"native_wait_timeout\s*=.*recheck_slice_ns",
                "the native wait must be clamped to the recheck slice",
            )
        with self.subTest(contract="one absolute caller deadline"):
            self.assert_contains(
                acquire_wait,
                "pdocker_vk_remaining_timeout_ns(",
                "finite acquire retries must consume one monotonic caller deadline",
            )
            self.assert_contains(
                acquire_wait,
                "remaining_after_wait",
                "native timeout handling must resample the deadline before retrying",
            )
            self.assert_matches(
                acquire_wait,
                r"remaining_after_wait\s*=\s*pdocker_vk_remaining_timeout_ns",
                "the pre-wait remaining duration must never authorize a post-deadline retry",
            )
            self.assert_not_matches(
                acquire_wait,
                r"\b(?:sleep|usleep|nanosleep)\s*\(",
                "acquire completion polling must not use sleep loops",
            )
        with self.subTest(contract="retained completion references are mutex-protected"):
            retain_at = acquire_wait.find("completion->refs++;")
            unlock_at = acquire_wait.find(
                "(void)pthread_mutex_unlock(&g_wsi_mutex);",
                retain_at,
            )
            release_at = acquire_wait.find("completions[i]->refs--;", unlock_at)
            self.assertGreaterEqual(retain_at, 0)
            self.assertGreater(unlock_at, retain_at)
            self.assertGreater(release_at, unlock_at)
            self.assertLess(
                acquire_wait.rfind("pdocker_vk_wsi_lock()", unlock_at, release_at),
                release_at,
                "completion references must be reacquired under the WSI mutex before release",
            )

    def test_shared_present_completion_waits_do_not_mutate_fence_shadow(self) -> None:
        wait_one = self.icd.function("pdocker_vk_wait_present_completion_fence")
        wait_any = self.icd.function("pdocker_vk_wait_present_completion_any")
        raw_status = self.icd.function("send_executor_fence_status_raw")
        for name, function in (("wait-one", wait_one), ("wait-any", wait_any)):
            with self.subTest(path=name):
                self.assert_not_matches(
                    function,
                    r"fence\.signaled\s*=",
                    "shared present completion waiters must not race on embedded fence shadow state",
                )
        self.assert_contains(
            wait_any,
            "send_executor_fence_status_raw(",
            "wait-any status probing must use the non-mutating native identity helper",
        )
        self.assert_not_matches(
            raw_status,
            r"->\s*signaled\s*=",
            "the raw native status helper must not publish local shadow state",
        )

    def test_swapchain_registry_and_old_swapchain_retirement_share_wsi_lock(self) -> None:
        create = self.icd.function("vkCreateSwapchainKHR")
        destroy = self.icd.function("vkDestroySwapchainKHR")
        get_images = self.icd.function("vkGetSwapchainImagesKHR")

        with self.subTest(operation="register"):
            lock_at = create.find("pdocker_vk_wsi_lock()")
            register_at = create.find("swapchain_register(swapchain);")
            unlock_at = create.find("pthread_mutex_unlock(&g_wsi_mutex)", register_at)
            self.assertGreaterEqual(lock_at, 0)
            self.assertGreater(register_at, lock_at)
            self.assertGreater(
                unlock_at,
                register_at,
                "swapchain registration must remain inside the WSI registry transaction",
            )

        with self.subTest(operation="unregister"):
            lock_at = destroy.find("pdocker_vk_wsi_lock()")
            unregister_at = destroy.find("swapchain_unregister_object(sc)")
            unlock_at = destroy.find("pthread_mutex_unlock(&g_wsi_mutex)", unregister_at)
            self.assertGreaterEqual(lock_at, 0)
            self.assertGreater(unregister_at, lock_at)
            self.assertGreater(
                unlock_at,
                unregister_at,
                "swapchain unregistration must remain inside the WSI registry transaction",
            )

        lookup_at = create.find("swapchain_handle_lookup_for_device(")
        retire_at = create.find("old_swapchain->retired = true;")
        unlock_at = create.find("pthread_mutex_unlock(&g_wsi_mutex)", retire_at)
        self.assertGreaterEqual(lookup_at, 0)
        self.assertGreater(retire_at, lookup_at)
        self.assertGreater(
            unlock_at,
            retire_at,
            "oldSwapchain lookup, validation, and retirement must be one registry transaction",
        )
        self.assert_contains(
            get_images,
            "pdocker_vk_wsi_lock()",
            "swapchain image enumeration must retain registry validity under the WSI mutex",
        )


    def test_device_teardown_drains_shared_present_completions_safely(self) -> None:
        cleanup = self.icd.function("pdocker_vk_cleanup_device_swapchains")
        live_objects = self.icd.function(
            "pdocker_vk_destroy_device_live_objects"
        )
        destroy_device = self.icd.function("vkDestroyDevice")

        with self.subTest(contract="device identity remains live during cleanup"):
            cleanup_at = destroy_device.find(
                "pdocker_vk_destroy_device_live_objects("
            )
            unregister_at = destroy_device.find("device_unregister(device)")
            queue_retire_at = destroy_device.find("queue_retire(")
            self.assertGreaterEqual(cleanup_at, 0)
            self.assertGreater(unregister_at, cleanup_at)
            self.assertGreater(queue_retire_at, cleanup_at)
            self.assert_contains(
                destroy_device,
                "if (!pdocker_vk_destroy_device_live_objects(",
                "vkDestroyDevice must branch on completion-aware child cleanup",
            )
            failure_at = destroy_device.find(
                "if (!pdocker_vk_destroy_device_live_objects("
            )
            return_at = destroy_device.find("return;", failure_at)
            self.assertGreater(
                return_at,
                failure_at,
                "failed WSI cleanup must retain device/queue identity and the remaining child graph",
            )
            self.assertLess(
                return_at,
                unregister_at,
                "device unregistration must be unreachable after failed WSI cleanup",
            )
            self.assert_contains(
                live_objects,
                "return false;",
                "completion cleanup failure must propagate to vkDestroyDevice",
            )
            self.assert_contains(
                live_objects,
                "return true;",
                "only complete child cleanup may retire the device identity",
            )

        with self.subTest(contract="registry and references use WSI mutex"):
            lock_at = cleanup.find("pdocker_vk_wsi_lock()")
            retain_at = cleanup.find("completion->refs++;")
            unregister_at = cleanup.find("swapchain_unregister_object(sc)")
            unlock_at = cleanup.find(
                "pthread_mutex_unlock(&g_wsi_mutex)", unregister_at
            )
            self.assertGreaterEqual(lock_at, 0)
            self.assertGreater(retain_at, lock_at)
            self.assertGreater(unregister_at, retain_at)
            self.assertGreater(unlock_at, unregister_at)

        with self.subTest(contract="native wait is outside WSI mutex"):
            wait_at = cleanup.find(
                "pdocker_vk_wait_present_completion_fence(", unlock_at
            )
            relock_at = cleanup.find("pdocker_vk_wsi_lock()", wait_at)
            release_at = cleanup.find("completions[i]->refs--;", relock_at)
            self.assertGreater(wait_at, unlock_at)
            self.assertGreater(relock_at, wait_at)
            self.assertGreater(release_at, relock_at)

        with self.subTest(contract="shared successful completion is observed once"):
            self.assert_contains(
                cleanup,
                "pdocker_vk_present_completion_observe_locked(completions[i]);",
                "successful teardown waits must detach every swapchain image sharing the completion",
            )
            self.assert_contains(
                cleanup,
                "if (completions[i]->refs > 0) completions[i]->refs--;",
                "the temporary observer reference must be released under the WSI mutex",
            )

        with self.subTest(contract="ambiguous executor identity is retained"):
            failed_at = cleanup.find("if (wait_failed)")
            restore_at = cleanup.find("swapchain_register(sc);", failed_at)
            failed_return = cleanup.find("return false;", restore_at)
            destroy_images_at = cleanup.find(
                "pdocker_vk_destroy_swapchain_images(device, sc);",
                failed_at,
            )
            self.assertGreaterEqual(failed_at, 0)
            self.assertGreater(
                restore_at,
                failed_at,
                "an unproven completion must restore the swapchain registry entry",
            )
            self.assertGreater(failed_return, restore_at)
            self.assertTrue(
                destroy_images_at < 0 or destroy_images_at > failed_return,
                "unresolved present images must remain live on the failure path",
            )
            self.assert_contains(
                cleanup[failed_at:failed_return],
                "wait_results[i] == VK_SUCCESS",
                "only exactly completed observer identities may be destroyed",
            )
            self.assert_contains(
                cleanup,
                "pdocker_vk_mark_device_lost(",
                "ambiguous wait/destroy results must poison the logical device",
            )
            self.assert_contains(
                cleanup,
                "if (destroy_failed) return false;",
                "ambiguous native fence destruction must retain device identity",
            )

        with self.subTest(contract="explicit swapchain destroy is also fail-closed"):
            destroy_swapchain = self.icd.function("vkDestroySwapchainKHR")
            failed_at = destroy_swapchain.find("if (wait_failed)")
            unregister_at = destroy_swapchain.find(
                "swapchain_unregister_object(sc)", failed_at
            )
            failed_return = destroy_swapchain.find("return;", failed_at)
            self.assertGreaterEqual(failed_at, 0)
            self.assertGreater(failed_return, failed_at)
            self.assertGreater(
                unregister_at,
                failed_return,
                "void swapchain destruction must retain the registered image graph when completion is ambiguous",
            )
            self.assert_contains(
                destroy_swapchain[failed_at:failed_return],
                "pdocker_vk_mark_device_lost(",
                "an ambiguous explicit destroy must poison its owning device",
            )

        with self.subTest(contract="generic teardown cannot bypass WSI cleanup"):
            self.assert_contains(
                live_objects,
                "pdocker_vk_cleanup_device_swapchains(",
                "device teardown must route swapchains through the completion-aware cleanup",
            )
            self.assert_not_contains(
                live_objects,
                "PDOCKER_VK_FIND_DEVICE_OWNED(g_swapchains",
                "generic registry cleanup must not bypass completion lifetime management",
            )


if __name__ == "__main__":
    unittest.main()

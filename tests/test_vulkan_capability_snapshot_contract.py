"""Focused contracts for immutable Vulkan capability snapshots.

These tests intentionally describe the production contract rather than the
current implementation.  They remain red while Vulkan-visible capabilities or
execution policy can be re-read after a VkInstance snapshot is published.
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
ICD_SOURCE = (
    ROOT / "docker-proot-setup" / "src" / "gpu" / "pdocker_vulkan_icd.c"
)


class CSource:
    """Small brace-aware extractor for static C contract assertions."""

    def __init__(self, source: str) -> None:
        self.source = source

    def braced_from(self, marker: str) -> str:
        marker_offset = self.source.find(marker)
        if marker_offset < 0:
            raise AssertionError(f"missing C source marker: {marker}")
        opening = self.source.find("{", marker_offset)
        if opening < 0:
            raise AssertionError(f"missing opening brace after: {marker}")

        depth = 0
        state = "code"
        index = opening
        while index < len(self.source):
            char = self.source[index]
            following = self.source[index + 1] if index + 1 < len(self.source) else ""
            if state == "code":
                if char == '"':
                    state = "string"
                elif char == "'":
                    state = "character"
                elif char == "/" and following == "/":
                    state = "line-comment"
                    index += 1
                elif char == "/" and following == "*":
                    state = "block-comment"
                    index += 1
                elif char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                    if depth == 0:
                        return self.source[marker_offset : index + 1]
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
            elif state == "line-comment":
                if char == "\n":
                    state = "code"
            elif state == "block-comment" and char == "*" and following == "/":
                state = "code"
                index += 1
            index += 1
        raise AssertionError(f"unterminated C block after: {marker}")

    def function(self, name: str) -> str:
        definition = re.search(
            rf"(?m)^[^#\n]*\b{re.escape(name)}\s*\([^;{{}}]*\)\s*\{{",
            self.source,
            re.DOTALL,
        )
        if not definition:
            raise AssertionError(f"missing C function definition: {name}")
        return self.braced_from(definition.group(0)[: definition.group(0).rfind("{")])


class VulkanCapabilitySnapshotStaticContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = ICD_SOURCE.read_text(encoding="utf-8")
        cls.c = CSource(cls.source)

    def assert_matches(self, text: str, pattern: str, message: str) -> None:
        """Regex assertion that does not dump the 45k-line ICD on failure."""
        if re.search(pattern, text, re.DOTALL) is None:
            self.fail(f"{message} [missing /{pattern}/]")

    def assert_not_matches(self, text: str, pattern: str, message: str) -> None:
        """Negative regex assertion with a compact, actionable diagnostic."""
        match = re.search(pattern, text, re.DOTALL)
        if match is not None:
            self.fail(f"{message} [matched {match.group(0)!r}]")

    def assert_no_live_reads(self, function_name: str, *patterns: str) -> None:
        body = self.c.function(function_name)
        for pattern in patterns:
            with self.subTest(function=function_name, forbidden=pattern):
                self.assert_not_matches(
                    body,
                    pattern,
                    f"{function_name} must consume immutable snapshot state, not {pattern}",
                )

    def test_vk_instance_owns_and_releases_capability_snapshot(self) -> None:
        instance = self.c.braced_from("typedef struct PdockerVkInstance")
        with self.subTest(stage="instance field"):
            self.assert_matches(
                instance,
                r"Pdock(?:er)?VkCapabilitySnapshot\s*\*\s*capability_snapshot\s*;",
                "VkInstance must own the snapshot that defines its physical device",
            )

        create = self.c.function("vkCreateInstance")
        with self.subTest(stage="create ownership"):
            self.assert_matches(
                create,
                r"instance->capability_snapshot\s*=\s*capability_snapshot_(?:create|retain)\s*\(",
                "vkCreateInstance must publish the snapshot before the instance handle",
            )
        destroy = self.c.function("vkDestroyInstance")
        with self.subTest(stage="destroy release"):
            self.assert_matches(
                destroy,
                r"capability_snapshot_release\s*\(\s*pdocker_instance->capability_snapshot\s*\)",
                "vkDestroyInstance must release its snapshot ownership",
            )

    def test_physical_device_device_and_queue_retain_one_snapshot(self) -> None:
        physical = self.c.function("physical_device_for_instance")
        with self.subTest(object="physical", contract="no independent snapshot"):
            self.assert_not_matches(
                physical,
                r"capability_snapshot_create\s*\(",
                "physical devices must not create a snapshot independent of VkInstance",
            )
        with self.subTest(object="physical", contract="retain instance snapshot"):
            self.assert_matches(
                physical,
                r"physical->capability_snapshot\s*=\s*capability_snapshot_retain\s*\(\s*(?:instance|pdocker_instance)->capability_snapshot\s*\)",
                "physical device must retain the owning instance snapshot",
            )

        create_device = self.c.function("vkCreateDevice")
        device_source = re.search(
            r"device->capability_snapshot\s*=\s*capability_snapshot_retain\s*\(([^)]*)\)",
            create_device,
        )
        queue_source = re.search(
            r"queue->capability_snapshot\s*=\s*capability_snapshot_retain\s*\(([^)]*)\)",
            create_device,
        )
        with self.subTest(object="device", contract="retain physical snapshot"):
            self.assertIsNotNone(device_source, "VkDevice must retain the physical snapshot")
        with self.subTest(object="queue", contract="retain physical snapshot"):
            self.assertIsNotNone(queue_source, "VkQueue must retain the same physical snapshot")
        if device_source and queue_source:
            self.assertEqual(
                " ".join(device_source.group(1).split()),
                " ".join(queue_source.group(1).split()),
                "VkDevice and VkQueue must retain the identical snapshot source",
            )

    def test_snapshot_contains_effective_capability_and_policy_values(self) -> None:
        snapshot = self.c.braced_from("struct PdockerVkCapabilitySnapshot {")
        required_field_concepts = {
            "heap size": r"heap_size",
            "maximum buffer size": r"max_buffer_size",
            "subgroup size/operations": r"subgroup",
            "8-bit storage": r"storage_?8",
            "16-bit storage": r"storage_?16",
            "descriptor policy": r"descriptor",
            "V5 object transport": r"v5_.*object|object.*v5_",
            "V5 frame transport": r"v5_.*frame|frame.*v5_",
        }
        for concept, pattern in required_field_concepts.items():
            with self.subTest(concept=concept):
                self.assert_matches(
                    snapshot,
                    pattern,
                    f"capability snapshot lacks an immutable {concept} value",
                )

    def test_advertised_storage_descriptor_and_subgroup_are_pure_snapshot_reads(self) -> None:
        feature_functions = (
            "advertised_shader_int64",
            "advertised_storage16",
            "advertised_storage8",
            "executor_advertised_storage16_or",
            "executor_advertised_storage8_or",
            "advertised_descriptor_binding_partially_bound",
            "advertised_descriptor_binding_variable_descriptor_count",
            "advertised_descriptor_binding_update_unused_while_pending",
            "advertised_descriptor_update_after_bind_native_feature_any",
            "advertised_descriptor_binding_uniform_buffer_update_after_bind",
            "advertised_descriptor_binding_sampled_image_update_after_bind",
            "advertised_descriptor_binding_storage_image_update_after_bind",
            "advertised_descriptor_binding_storage_buffer_update_after_bind",
            "advertised_descriptor_binding_uniform_texel_buffer_update_after_bind",
            "advertised_descriptor_binding_storage_texel_buffer_update_after_bind",
            "advertised_subgroup_operations",
            "advertised_subgroup_size",
            "fill_pnext_features",
            "advertised_feature_mask",
        )
        for function_name in feature_functions:
            self.assert_no_live_reads(
                function_name,
                r"\bgetenv\s*\(",
                r"\benv_(?:enabled|disabled|truthy_default)\s*\(",
                r"parsed_env_subgroup_size\s*\(",
            )

    def test_heap_and_max_buffer_consumers_use_snapshot_limits(self) -> None:
        consumers = (
            "fill_physical_device_properties",
            "fill_pnext_properties",
            "vkGetPhysicalDeviceImageFormatProperties",
            "vkGetPhysicalDeviceMemoryProperties",
            "vkCreateBuffer",
            "vkCreateImage",
            "fill_buffer_create_memory_requirements",
            "fill_image_create_memory_requirements",
            "vkAllocateMemory",
        )
        for function_name in consumers:
            body = self.c.function(function_name)
            with self.subTest(function=function_name, contract="snapshot parameter/value"):
                self.assert_matches(
                    body,
                    r"\b(?:snapshot|capability_snapshot)\b",
                    f"{function_name} does not identify its capability snapshot",
                )
            for forbidden in (
                r"pdocker_vulkan_heap_size\s*\(\s*\)",
                r"pdocker_vulkan_host_heap_size\s*\(\s*\)",
                r"pdocker_vulkan_max_buffer_size\s*\(\s*\)",
                r"/proc/meminfo",
                r"PDOCKER_VULKAN_(?:HEAP_BYTES|MAX_BUFFER_BYTES)",
            ):
                with self.subTest(function=function_name, forbidden=forbidden):
                    self.assert_not_matches(
                        body,
                        forbidden,
                        f"{function_name} re-reads a live heap/limit instead of the snapshot",
                    )

    def test_v5_and_dispatch_policy_do_not_re_read_environment(self) -> None:
        for function_name in (
            "vulkan_v5_object_transport_enabled",
            "vulkan_v5_frame_enabled",
            "copy_alias_enabled",
        ):
            body = self.c.function(function_name)
            with self.subTest(function=function_name, contract="snapshot argument"):
                self.assert_matches(
                    body.split("{", 1)[0],
                    r"Pdock(?:er)?VkCapabilitySnapshot\s*\*|\bsnapshot\b",
                    f"{function_name} must be parameterized by the immutable snapshot",
                )
            self.assert_no_live_reads(
                function_name,
                r"\bgetenv\s*\(",
                r"\benv_(?:enabled|disabled|truthy_default)\s*\(",
            )

        dispatch = self.c.function("send_generic_vulkan_dispatch_op")
        for forbidden in (
            r"\bgetenv\s*\(",
            r"\benv_(?:enabled|disabled|truthy_default)\s*\(",
            r"copy_alias_enabled\s*\(\s*\)",
            r"vulkan_v5_frame_enabled\s*\(\s*\)",
        ):
            with self.subTest(function="send_generic_vulkan_dispatch_op", forbidden=forbidden):
                self.assert_not_matches(
                    dispatch,
                    forbidden,
                    "dispatch policy must be serialized from the queue snapshot",
                )

    def test_image_create_validation_uses_owning_snapshot_not_static_device(self) -> None:
        validation = self.c.function("validate_image_create_info_for_transport")
        with self.subTest(contract="snapshot argument"):
            self.assert_matches(
                validation.split("{", 1)[0],
                r"Pdock(?:er)?VkCapabilitySnapshot\s*\*|\bsnapshot\b",
                "image validation must receive the owning VkDevice snapshot",
            )
        with self.subTest(contract="no static physical device route"):
            self.assert_not_matches(
                validation,
                r"\bg_device\b",
                "image validation must not re-enter a public query through static g_device",
            )

    def test_retired_objects_clear_borrowed_snapshot(self) -> None:
        for function_name in (
            "fence_retire",
            "semaphore_retire",
            "event_retire",
            "command_buffer_retire",
        ):
            body = self.c.function(function_name)
            with self.subTest(function=function_name):
                self.assert_matches(
                    body,
                    r"->capability_snapshot\s*=\s*NULL\s*;",
                    f"{function_name} leaves a dangling borrowed snapshot in its retired list",
                )

    def test_physical_device_registry_is_mutex_protected(self) -> None:
        declaration = re.search(
            r"static\s+pthread_mutex_t\s+(g_[A-Za-z0-9_]*(?:physical|registry)[A-Za-z0-9_]*)\s*=\s*PTHREAD_MUTEX_INITIALIZER",
            self.source,
        )
        self.assertIsNotNone(
            declaration,
            "physical-device registry requires a dedicated pthread mutex",
        )
        if not declaration:
            return
        mutex = declaration.group(1)
        for function_name in (
            "physical_device_handle_resolve",
            "physical_device_for_instance",
            "physical_devices_unregister_for_instance",
        ):
            body = self.c.function(function_name)
            for operation in ("lock", "unlock"):
                with self.subTest(function=function_name, operation=operation):
                    self.assert_matches(
                        body,
                        rf"pthread_mutex_{operation}\s*\(\s*&{re.escape(mutex)}\s*\)",
                        f"{function_name} does not {operation} the physical-device registry mutex",
                    )


@unittest.skipUnless(shutil.which("gcc"), "gcc is required for the snapshot lifecycle harness")
class VulkanCapabilitySnapshotLifecycleHarnessTest(unittest.TestCase):
    def test_retired_objects_drop_borrowed_snapshot_at_runtime(self) -> None:
        source = CSource(ICD_SOURCE.read_text(encoding="utf-8"))
        retire_sources = "\n\n".join(
            source.function(name)
            for name in (
                "fence_retire",
                "semaphore_retire",
                "event_retire",
                "command_buffer_retire",
            )
        )
        harness = textwrap.dedent(
            """
            #include <stdbool.h>
            #include <stdio.h>
            #include <string.h>

            typedef struct PdockerVkCapabilitySnapshot {
                unsigned refcount;
            } PdockerVkCapabilitySnapshot;

            typedef struct PdockerVkFence {
                const PdockerVkCapabilitySnapshot *capability_snapshot;
                bool destroyed;
                bool executor_tracked;
                struct PdockerVkFence *next;
            } PdockerVkFence;

            typedef struct PdockerVkSemaphore {
                const PdockerVkCapabilitySnapshot *capability_snapshot;
                bool destroyed;
                bool executor_tracked;
                struct PdockerVkSemaphore *next;
            } PdockerVkSemaphore;

            typedef struct PdockerVkEvent {
                const PdockerVkCapabilitySnapshot *capability_snapshot;
                bool destroyed;
                bool executor_tracked;
                struct PdockerVkEvent *next;
            } PdockerVkEvent;

            typedef struct PdockerVkCommandBuffer {
                const PdockerVkCapabilitySnapshot *capability_snapshot;
                bool destroyed;
                void *pipeline;
                void *compute_pipeline;
                void *graphics_pipeline;
                void *owner_pool;
                struct PdockerVkCommandBuffer *next_in_pool;
                struct PdockerVkCommandBuffer *next_global;
            } PdockerVkCommandBuffer;

            static PdockerVkFence *g_retired_fences;
            static PdockerVkSemaphore *g_retired_semaphores;
            static PdockerVkEvent *g_retired_events;
            static PdockerVkCommandBuffer *g_retired_command_buffers;

            static void clear_recorded_command_ops(PdockerVkCommandBuffer *cmd) {
                (void)cmd;
            }
            static void command_buffer_destroy_record_vectors(PdockerVkCommandBuffer *cmd) {
                (void)cmd;
            }
            static void command_buffer_destroy_descriptor_states(PdockerVkCommandBuffer *cmd) {
                (void)cmd;
            }
            """
        )
        harness += "\n" + retire_sources + "\n"
        harness += textwrap.dedent(
            """
            int main(void) {
                PdockerVkCapabilitySnapshot snapshot;
                memset(&snapshot, 0, sizeof(snapshot));
                snapshot.refcount = 1u;
                unsigned failures = 0;

                PdockerVkFence fence;
                memset(&fence, 0, sizeof(fence));
                fence.capability_snapshot = &snapshot;
                fence_retire(&fence);
                if (fence.capability_snapshot != NULL) failures |= 1u;

                PdockerVkSemaphore semaphore;
                memset(&semaphore, 0, sizeof(semaphore));
                semaphore.capability_snapshot = &snapshot;
                semaphore_retire(&semaphore);
                if (semaphore.capability_snapshot != NULL) failures |= 2u;

                PdockerVkEvent event;
                memset(&event, 0, sizeof(event));
                event.capability_snapshot = &snapshot;
                event_retire(&event);
                if (event.capability_snapshot != NULL) failures |= 4u;

                PdockerVkCommandBuffer command_buffer;
                memset(&command_buffer, 0, sizeof(command_buffer));
                command_buffer.capability_snapshot = &snapshot;
                command_buffer_retire(&command_buffer);
                if (command_buffer.capability_snapshot != NULL) failures |= 8u;

                if (failures != 0) {
                    fprintf(stderr, "borrowed-snapshot-retire-mask=0x%x\\n", failures);
                    return 1;
                }
                return 0;
            }
            """
        )
        with tempfile.TemporaryDirectory(prefix="vulkan-capability-snapshot-") as tmp:
            source_path = Path(tmp) / "snapshot_lifecycle.c"
            binary_path = Path(tmp) / "snapshot_lifecycle"
            source_path.write_text(harness, encoding="utf-8")
            compiled = subprocess.run(
                [
                    "gcc",
                    "-std=gnu11",
                    "-O0",
                    "-Wall",
                    "-Wextra",
                    "-Wno-unused-function",
                    "-Wno-missing-field-initializers",
                    "-pthread",
                    "-o",
                    str(binary_path),
                    str(source_path),
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(
                0,
                compiled.returncode,
                "gcc failed while compiling snapshot lifecycle harness\n"
                + compiled.stdout
                + compiled.stderr,
            )
            result = subprocess.run(
                [str(binary_path)],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()

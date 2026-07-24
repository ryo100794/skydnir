#!/usr/bin/env python3
"""P0 correctness gates for generic Vulkan query pass-through.

These tests intentionally describe required Vulkan behavior rather than the
current implementation.  A failing test is a tracked P0 gap; it must not be
weakened to preserve host-side query emulation.
"""

from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
ICD = ROOT / "docker-proot-setup/src/gpu/pdocker_vulkan_icd.c"
EXECUTOR = ROOT / "app/src/main/cpp/pdocker_gpu_executor.c"
ICD_ABI = ROOT / "docker-proot-setup/src/gpu/pdocker_gpu_abi.h"
EXECUTOR_ABI = ROOT / "app/src/main/cpp/pdocker_gpu_abi.h"


def _matching_delimiter(source: str, start: int, opening: str, closing: str) -> int:
    """Find a matching C delimiter while ignoring strings and comments."""
    if start >= len(source) or source[start] != opening:
        raise AssertionError(f"expected {opening!r} at offset {start}")
    depth = 0
    state = "code"
    offset = start
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
            elif char == opening:
                depth += 1
            elif char == closing:
                depth -= 1
                if depth == 0:
                    return offset
        offset += 1
    raise AssertionError(f"unterminated C delimiter {opening!r} at offset {start}")


def c_function(source: str, name: str) -> str:
    """Return a complete C function definition, skipping prototypes/calls."""
    for match in re.finditer(rf"\b{re.escape(name)}\s*\(", source):
        open_paren = source.index("(", match.start())
        close_paren = _matching_delimiter(source, open_paren, "(", ")")
        brace = close_paren + 1
        while brace < len(source) and source[brace].isspace():
            brace += 1
        if brace >= len(source) or source[brace] != "{":
            continue
        end = _matching_delimiter(source, brace, "{", "}") + 1
        start = source.rfind("\n\n", 0, match.start())
        start = 0 if start < 0 else start + 2
        return source[start:end]
    raise AssertionError(f"C function definition not found: {name}")


def c_function_body(source: str, name: str) -> str:
    function = c_function(source, name)
    brace = function.index("{")
    return function[brace : _matching_delimiter(function, brace, "{", "}") + 1]


def c_block_after(source: str, marker: str) -> str:
    marker_offset = source.index(marker)
    brace = source.index("{", marker_offset + len(marker))
    return source[brace : _matching_delimiter(source, brace, "{", "}") + 1]


def c_enclosing_block(source: str, marker: str) -> str:
    """Return the innermost complete C block containing *marker*."""
    marker_offset = source.index(marker)
    brace = source.rfind("{", 0, marker_offset)
    while brace >= 0:
        try:
            end = _matching_delimiter(source, brace, "{", "}")
        except AssertionError:
            brace = source.rfind("{", 0, brace)
            continue
        if end >= marker_offset:
            return source[brace : end + 1]
        brace = source.rfind("{", 0, brace)
    raise AssertionError(f"C block containing marker not found: {marker}")


class VulkanQueryPassthroughContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.icd = ICD.read_text(encoding="utf-8")
        cls.executor = EXECUTOR.read_text(encoding="utf-8")
        cls.icd_abi = ICD_ABI.read_text(encoding="utf-8")
        cls.executor_abi = EXECUTOR_ABI.read_text(encoding="utf-8")

    def _compile_and_run(self, source: str, stem: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory(prefix=f"{stem}-") as tmp:
            source_path = Path(tmp) / f"{stem}.c"
            binary_path = Path(tmp) / stem
            source_path.write_text(source, encoding="utf-8")
            compiled = subprocess.run(
                [
                    "gcc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                    str(source_path), "-o", str(binary_path),
                ],
                text=True,
                capture_output=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(0, compiled.returncode, compiled.stderr)
            return subprocess.run(
                [str(binary_path)],
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )

    def test_query_execution_never_synthesizes_results_from_host_clock(self) -> None:
        offenders = []
        for function_name in ("execute_recorded_query_op", "vkGetQueryPoolResults"):
            try:
                body = c_function_body(self.icd, function_name)
            except AssertionError:
                # Removing a host-side query emulation function satisfies this
                # gate; only extant result-producing paths can synthesize data.
                continue
            if "monotonic_ns(" in body:
                offenders.append(function_name)
        self.assertEqual(
            [], offenders,
            "query results must originate in the native executor, not monotonic_ns(): "
            + ", ".join(offenders),
        )

    def test_wait_bit_source_does_not_directly_fabricate_availability(self) -> None:
        body = c_function_body(self.icd, "vkGetQueryPoolResults")
        marker = "if (!pool->available[q] && wait)"
        wait_block = c_block_after(body, marker) if marker in body else body
        forbidden = [
            marker for marker in (
                "pool->values[q] =",
                "pool->available[q] = 1",
                "pool->result_entries[q].value =",
                "pool->result_entries[q].available = 1",
                "pool->result_entries[q].status = VK_SUCCESS",
            )
            if marker in wait_block
        ]
        self.assertEqual(
            [], forbidden,
            "VK_QUERY_RESULT_WAIT_BIT fabricates completion in its unavailable branch: "
            + ", ".join(forbidden),
        )

    @unittest.skipUnless(shutil.which("gcc"), "gcc is required for the Vulkan C API harness")
    def test_compiled_vk_get_query_pool_results_wait_bit_never_fabricates(self) -> None:
        get_results = c_function(self.icd, "vkGetQueryPoolResults")
        try:
            scalar_writer = c_function(self.icd, "write_query_result_scalar")
        except AssertionError:
            # The compiled harness targets the legacy host-emulation body. If
            # that helper has been removed, require the surviving entry point
            # itself to contain no clock/result fabrication and accept the
            # stronger transport-only implementation.
            self.assertNotIn("monotonic_ns(", get_results)
            self.assertNotIn("pool->available[", get_results)
            self.assertNotIn("pool->values[", get_results)
            return
        harness = textwrap.dedent(
            f"""
            #include <stdbool.h>
            #include <inttypes.h>
            #include <stddef.h>
            #include <stdint.h>
            #include <stdio.h>
            #include <string.h>

            #define VKAPI_ATTR
            #define VKAPI_CALL
            typedef int32_t VkResult;
            typedef uintptr_t VkDevice;
            typedef uintptr_t VkQueryPool;
            typedef uint64_t VkDeviceSize;
            typedef uint32_t VkQueryResultFlags;
            #define VK_SUCCESS 0
            #define VK_NOT_READY 1
            #define VK_INCOMPLETE 5
            #define VK_ERROR_INITIALIZATION_FAILED (-3)
            #define VK_ERROR_FEATURE_NOT_PRESENT (-8)
            #define VK_QUERY_RESULT_64_BIT 0x1u
            #define VK_QUERY_RESULT_WAIT_BIT 0x2u
            #define VK_QUERY_RESULT_WITH_AVAILABILITY_BIT 0x4u
            #define VK_QUERY_RESULT_PARTIAL_BIT 0x8u

            typedef struct {{
                uint64_t value;
                uint32_t available;
                uint32_t status;
            }} QueryResultEntry;

            typedef struct PdockerVkQueryPool {{
                uint32_t query_count;
                uint64_t *values;
                uint8_t *available;
                uint8_t *active;
                QueryResultEntry *result_entries;
            }} PdockerVkQueryPool;

            static PdockerVkQueryPool *g_pool;
            static PdockerVkQueryPool *query_pool_handle_lookup_for_device(
                    VkDevice device, VkQueryPool pool) {{
                (void)device; (void)pool; return g_pool;
            }}
            static bool query_range_valid(
                    const PdockerVkQueryPool *pool, uint32_t first, uint32_t count) {{
                return pool && first <= pool->query_count &&
                       count <= pool->query_count - first;
            }}
            static bool query_result_flags_supported(VkQueryResultFlags flags) {{
                return (flags & ~(VK_QUERY_RESULT_64_BIT | VK_QUERY_RESULT_WAIT_BIT |
                                  VK_QUERY_RESULT_WITH_AVAILABILITY_BIT |
                                  VK_QUERY_RESULT_PARTIAL_BIT)) == 0;
            }}
            static void trace_icd_runtime_failure(const char *reason, VkResult result) {{
                (void)reason; (void)result;
            }}
            static bool checked_mul_u64(uint64_t a, uint64_t b, uint64_t *out) {{
                if (a != 0 && b > UINT64_MAX / a) return false;
                *out = a * b;
                return true;
            }}
            static uint64_t monotonic_ns(void) {{ return UINT64_C(0x123456789abcdef0); }}

            {scalar_writer}
            {get_results}

            int main(void) {{
                uint64_t values[1] = {{ 0 }};
                uint8_t available[1] = {{ 0 }};
                uint8_t active[1] = {{ 0 }};
                QueryResultEntry entries[1] = {{{{ 0, 0, 0 }}}};
                PdockerVkQueryPool pool = {{ 1, values, available, active, entries }};
                uint64_t output = UINT64_C(0xfeedfacecafebeef);
                const uint64_t sentinel = output;
                g_pool = &pool;
                VkResult rc = vkGetQueryPoolResults(
                    1, 1, 0, 1, sizeof(output), &output, sizeof(output),
                    VK_QUERY_RESULT_64_BIT | VK_QUERY_RESULT_WAIT_BIT);
                if (available[0] != 0 || entries[0].available != 0 ||
                    values[0] != 0 || output != sentinel) {{
                    fprintf(stderr,
                            "WAIT_BIT fabricated query completion: rc=%d value=0x%016" PRIx64
                            " available=%u entry_available=%u output=0x%016" PRIx64 "\\n",
                            rc, values[0], available[0], entries[0].available, output);
                    return 1;
                }}
                return 0;
            }}
            """
        )
        result = self._compile_and_run(harness, "vulkan_query_wait_gate")
        self.assertEqual(0, result.returncode, result.stderr)

    @unittest.skipUnless(shutil.which("gcc"), "gcc is required for the query routing harness")
    def test_compiled_query_only_commands_require_executor_transport(self) -> None:
        classifier = c_function(self.icd, "graphics_record_requires_submit_frame")
        harness = textwrap.dedent(
            f"""
            #include <stdbool.h>
            #include <stdint.h>
            #include <stdio.h>
            #define PDOCKER_GPU_GRAPHICS_V6_COMMAND_BEGIN_RENDERING 1u
            #define PDOCKER_GPU_GRAPHICS_V6_COMMAND_END_RENDERING 2u
            #define PDOCKER_GPU_GRAPHICS_V6_COMMAND_BIND_PIPELINE 3u
            #define PDOCKER_GPU_GRAPHICS_V6_COMMAND_DRAW 9u
            #define PDOCKER_GPU_GRAPHICS_V6_COMMAND_DRAW_INDEXED 10u
            #define PDOCKER_GPU_GRAPHICS_V6_COMMAND_CLEAR_ATTACHMENTS 22u
            #define PDOCKER_GPU_GRAPHICS_V6_COMMAND_RESET_QUERY_POOL 23u
            #define PDOCKER_GPU_GRAPHICS_V6_COMMAND_WRITE_TIMESTAMP 24u
            #define PDOCKER_GPU_GRAPHICS_V6_COMMAND_BEGIN_QUERY 25u
            #define PDOCKER_GPU_GRAPHICS_V6_COMMAND_END_QUERY 26u
            #define PDOCKER_GPU_GRAPHICS_V6_COMMAND_COPY_QUERY_POOL_RESULTS 27u

            {classifier}

            int main(void) {{
                const uint32_t query_commands[] = {{ 23u, 24u, 25u, 26u, 27u }};
                unsigned missing = 0;
                for (size_t i = 0; i < sizeof(query_commands) / sizeof(query_commands[0]); ++i) {{
                    if (!graphics_record_requires_submit_frame(query_commands[i])) {{
                        fprintf(stderr,
                                "query-only command %u is not routed to the executor frame\\n",
                                query_commands[i]);
                        ++missing;
                    }}
                }}
                return missing == 0 ? 0 : 1;
            }}
            """
        )
        result = self._compile_and_run(harness, "vulkan_query_routing_gate")
        self.assertEqual(0, result.returncode, result.stderr)

    def test_copy_query_results_has_no_noop_success_case(self) -> None:
        pattern = re.compile(
            r"case\s+PDOCKER_VK_COMMAND_COPY_QUERY_RESULTS\s*:\s*break\s*;"
        )
        lines = [self.icd.count("\n", 0, match.start()) + 1 for match in pattern.finditer(self.icd)]
        self.assertEqual(
            [], lines,
            f"COPY_QUERY_RESULTS silently succeeds without copying at ICD lines {lines}",
        )

    def test_copy_query_results_reaches_native_executor(self) -> None:
        replay = c_function_body(self.executor, "record_vulkan_graphics_v6_command_buffer")
        self.assertIn("vkCmdCopyQueryPoolResults(", replay)
        self.assertIn("copy->query_pool_id", replay)
        self.assertIn("copy->dst_resource_index", replay)

    def test_query_pool_transport_identity_is_present_end_to_end(self) -> None:
        for abi in (self.icd_abi, self.executor_abi):
            self.assertIn("uint64_t query_pool_id;", abi)
        self.assertIn("query->query_pool_id = op->query_pool->pool_id;", self.icd)
        self.assertIn("copy->query_pool_id = op->query_pool->pool_id;", self.icd)
        executor_lookup = c_function_body(self.executor, "find_vulkan_graphics_replay_query_pool")
        self.assertIn("queries->pools[i].query_pool_id == query_pool_id", executor_lookup)

    def test_phase1_executor_registry_has_collision_safe_nonzero_ids(self) -> None:
        allocator = c_function_body(self.executor, "next_executor_query_pool_id_locked")
        register = c_function_body(self.executor, "register_executor_query_pool")
        self.assertIn("g_query_pool_registry_mutex", self.executor)
        self.assertIn("PTHREAD_MUTEX_INITIALIZER", self.executor)
        self.assertIn("g_query_pool_id_generation == 0", allocator)
        self.assertIn("find_executor_query_pool_entry_locked", allocator)
        self.assertIn("next_executor_query_pool_id_locked()", register)
        self.assertIn("free_entry->pool = pool", register)

    def test_phase1_executor_registry_defers_destroy_until_last_reference(self) -> None:
        retain = c_function_body(self.executor, "retain_executor_query_pool_entry")
        release = c_function_body(self.executor, "release_executor_query_pool_entry")
        destroy = c_function_body(self.executor, "destroy_executor_query_pool")
        self.assertIn("entry->in_flight_refs++", retain)
        self.assertIn("entry->destroy_pending", retain)
        self.assertIn("entry->in_flight_refs--", release)
        self.assertIn("entry->in_flight_refs == 0 && entry->destroy_pending", release)
        self.assertIn("entry->destroy_pending = 1", destroy)
        self.assertIn("if (entry->in_flight_refs == 0)", destroy)

    def test_phase1_executor_control_plane_preserves_native_query_results(self) -> None:
        handler = c_function_body(self.executor, "handle_vulkan_query_pool_command")
        for command in (
            "VULKAN_QUERY_POOL_CREATE ",
            "VULKAN_QUERY_POOL_DESTROY ",
            "VULKAN_QUERY_POOL_RESET ",
            "VULKAN_QUERY_POOL_GET_RESULTS ",
        ):
            self.assertIn(command, handler)
        self.assertIn("VkResult vrc = vkCreateQueryPool(", handler)
        self.assertIn("vulkan-query-pool-create\", vrc, query_pool_id", handler)
        self.assertIn("result = vkGetQueryPoolResults(", handler)
        self.assertIn("vulkan-query-pool-get-results\", result, query_pool_id", handler)
        self.assertNotIn("monotonic_ns(", handler)

    @unittest.skipUnless(shutil.which("gcc"), "gcc is required for the response parser harness")
    def test_compiled_query_control_response_contract_is_strict(self) -> None:
        """Exercise the real ICD response path with transport calls stubbed out."""
        parser_start = self.icd.index(
            "typedef enum {\n    PDOCKER_EXECUTOR_JSON_INVALID"
        )
        parser_end = self.icd.index(
            "\nstatic int send_executor_text_command_with_fds(",
            parser_start,
        )
        parser_functions = self.icd[parser_start:parser_end]
        sender = c_function(self.icd, "send_executor_text_command_with_fds")
        create_sender = c_function(self.icd, "send_executor_query_pool_create")
        harness = textwrap.dedent(
            f"""
            #include <stdbool.h>
            #include <errno.h>
            #include <limits.h>
            #include <stdint.h>
            #include <stdio.h>
            #include <stdlib.h>
            #include <string.h>
            #include <unistd.h>

            #define PDOCKER_GPU_TRANSPORT_MAX_PASSED_FDS 8u
            #define VK_SUCCESS 0
            #define VK_ERROR_UNKNOWN (-13)
            typedef int32_t VkResult;
            typedef struct PdockerVkCapabilitySnapshot {{ int unused; }}
                PdockerVkCapabilitySnapshot;
            typedef struct VkQueryPoolCreateInfo {{
                uint32_t queryType;
                uint32_t queryCount;
                uint32_t pipelineStatistics;
            }} VkQueryPoolCreateInfo;

            static const char *g_response;
            static int connect_queue_for_snapshot(
                    const PdockerVkCapabilitySnapshot *snapshot) {{
                (void)snapshot;
                return 1000;
            }}
            static int write_exact_fd(int fd, const void *data, size_t size) {{
                (void)fd; (void)data; (void)size;
                return 0;
            }}
            static int sendmsg_exact_with_fds(
                    int fd, const void *data, size_t size,
                    const int *fds, size_t fd_count) {{
                (void)fd; (void)data; (void)size; (void)fds; (void)fd_count;
                return 0;
            }}
            static int read_executor_text_response_line(
                    int fd, char **out_line, size_t *out_len) {{
                (void)fd;
                size_t length = strlen(g_response);
                *out_line = malloc(length + 1u);
                if (!*out_line) return -ENOMEM;
                memcpy(*out_line, g_response, length + 1u);
                *out_len = length;
                return 0;
            }}

            {parser_functions}
            {sender}
            {create_sender}

            static int expect_response(
                    const char *label,
                    const char *response,
                    const char *expected_stage,
                    uint64_t expected_pool_id,
                    int expected_rc,
                    int expected_result,
                    uint64_t expected_output_pool_id) {{
                PdockerVkCapabilitySnapshot snapshot = {{ 0 }};
                int result = 777;
                uint64_t pool_id = UINT64_C(0xfeedface);
                g_response = response;
                int rc = send_executor_text_command_with_fds(
                    &snapshot, "TEST\\n", NULL, 0,
                    &result, NULL, NULL, &pool_id,
                    expected_stage, expected_pool_id);
                if (rc != expected_rc ||
                    (rc == 0 && (result != expected_result ||
                                 pool_id != expected_output_pool_id))) {{
                    fprintf(stderr, "%s: rc=%d result=%d pool=%llu\\n",
                            label, rc, result, (unsigned long long)pool_id);
                    return 1;
                }}
                return 0;
            }}

            int main(void) {{
                unsigned failures = 0;
                failures += expect_response(
                    "valid-reordered-negative-result",
                    "{{\\\"query_pool_id\\\":41,\\\"valid\\\":true,\\\"result\\\":-7,"
                    "\\\"stage\\\":\\\"vulkan-query-pool-reset\\\"}}\\n",
                    "vulkan-query-pool-reset", 41, 0, -7, 41);
                failures += expect_response(
                    "stage-is-not-a-prefix",
                    "{{\\\"valid\\\":true,\\\"stage\\\":\\\"vulkan-query-pool-reset-extra\\\","
                    "\\\"result\\\":0,\\\"query_pool_id\\\":41}}\\n",
                    "vulkan-query-pool-reset", 41, -EPROTO, 0, 0);
                failures += expect_response(
                    "duplicate-stage",
                    "{{\\\"valid\\\":true,\\\"stage\\\":\\\"vulkan-query-pool-reset\\\","
                    "\\\"stage\\\":\\\"vulkan-query-pool-reset\\\",\\\"result\\\":0,"
                    "\\\"query_pool_id\\\":41}}\\n",
                    "vulkan-query-pool-reset", 41, -EPROTO, 0, 0);
                failures += expect_response(
                    "missing-result",
                    "{{\\\"valid\\\":true,\\\"stage\\\":\\\"vulkan-query-pool-reset\\\","
                    "\\\"query_pool_id\\\":41}}\\n",
                    "vulkan-query-pool-reset", 41, -EPROTO, 0, 0);
                failures += expect_response(
                    "duplicate-result",
                    "{{\\\"valid\\\":true,\\\"stage\\\":\\\"vulkan-query-pool-reset\\\","
                    "\\\"result\\\":0,\\\"result\\\":-1,\\\"query_pool_id\\\":41}}\\n",
                    "vulkan-query-pool-reset", 41, -EPROTO, 0, 0);
                failures += expect_response(
                    "result-outside-signed-32-bit",
                    "{{\\\"valid\\\":true,\\\"stage\\\":\\\"vulkan-query-pool-reset\\\","
                    "\\\"result\\\":2147483648,\\\"query_pool_id\\\":41}}\\n",
                    "vulkan-query-pool-reset", 41, -EPROTO, 0, 0);
                failures += expect_response(
                    "duplicate-query-pool-id",
                    "{{\\\"valid\\\":true,\\\"stage\\\":\\\"vulkan-query-pool-reset\\\","
                    "\\\"result\\\":0,\\\"query_pool_id\\\":41,\\\"query_pool_id\\\":41}}\\n",
                    "vulkan-query-pool-reset", 41, -EPROTO, 0, 0);
                failures += expect_response(
                    "zero-query-pool-id",
                    "{{\\\"valid\\\":true,\\\"stage\\\":\\\"vulkan-query-pool-reset\\\","
                    "\\\"result\\\":0,\\\"query_pool_id\\\":0}}\\n",
                    "vulkan-query-pool-reset", 41, -EPROTO, 0, 0);
                failures += expect_response(
                    "wrong-query-pool-id",
                    "{{\\\"valid\\\":true,\\\"stage\\\":\\\"vulkan-query-pool-reset\\\","
                    "\\\"result\\\":0,\\\"query_pool_id\\\":42}}\\n",
                    "vulkan-query-pool-reset", 41, -EPROTO, 0, 0);

                PdockerVkCapabilitySnapshot snapshot = {{ 0 }};
                VkQueryPoolCreateInfo create_info = {{ 0, 1, 0 }};
                VkResult create_result = VK_ERROR_UNKNOWN;
                uint64_t created_pool_id = 99;
                g_response =
                    "{{\\\"valid\\\":true,\\\"stage\\\":\\\"vulkan-query-pool-create\\\","
                    "\\\"result\\\":0,\\\"query_pool_id\\\":0}}\\n";
                if (send_executor_query_pool_create(
                        &snapshot, &create_info,
                        &create_result, &created_pool_id) != -EPROTO) {{
                    fprintf(stderr, "successful create accepted zero query_pool_id\\n");
                    failures++;
                }}
                return failures == 0 ? 0 : 1;
            }}
            """
        )
        result = self._compile_and_run(harness, "vulkan_query_response_gate")
        self.assertEqual(0, result.returncode, result.stderr)

    def test_device_teardown_destroys_executor_query_pool_before_retirement(self) -> None:
        teardown = c_function_body(self.icd, "pdocker_vk_destroy_device_live_objects")
        query_block = c_enclosing_block(teardown, "query_pool_find_owned")
        self.assertIn(
            "destroy_query_pool_executor_and_retire(", query_block,
            "device teardown must use the executor-aware query-pool lifecycle",
        )

        lifecycle = c_function_body(
            self.icd, "destroy_query_pool_executor_and_retire")
        executor_destroy = lifecycle.find("send_executor_query_pool_destroy(")
        local_retire = lifecycle.find("query_pool_retire(")
        self.assertGreaterEqual(
            executor_destroy, 0,
            "executor query-pool destruction is absent from the retirement path",
        )
        self.assertGreater(
            local_retire, executor_destroy,
            "local query-pool retirement occurs before executor destruction",
        )

    def test_native_waiting_query_results_bypass_global_executor_request_mutex(self) -> None:
        serve = c_function_body(self.executor, "serve_socket_client_main")
        handler = c_function_body(self.executor, "handle_vulkan_query_pool_command")

        classifier = re.search(
            r"\b(?:const\s+)?int\s+(?P<name>[A-Za-z_]\w*)\s*=\s*"
            r"(?P<expression>[^;]*VULKAN_QUERY_POOL_GET_RESULTS[^;]*);",
            serve,
            re.DOTALL,
        )
        self.assertIsNotNone(
            classifier,
            "socket dispatch does not classify potentially blocking GET_RESULTS requests",
        )
        query_wait_name = classifier.group("name")

        lock_call = serve.find("executor_request_lock()", classifier.end())
        self.assertGreaterEqual(
            lock_call, 0,
            "socket dispatch no longer exposes the global executor request lock decision",
        )
        statement_start = max(
            serve.rfind(";", classifier.end(), lock_call),
            serve.rfind("{", classifier.end(), lock_call),
        ) + 1
        statement_end = serve.find(";", lock_call) + 1
        lock_statement = serve[statement_start:statement_end]
        self.assertRegex(
            lock_statement,
            rf"\b{re.escape(query_wait_name)}\b\s*\?\s*0\s*:\s*"
            r"executor_request_lock\s*\(\s*\)",
            "GET_RESULTS reaches native WAIT_BIT while holding g_executor_request_mutex",
        )

        get_results_block = c_enclosing_block(
            handler, "VULKAN_QUERY_POOL_GET_RESULTS ")
        self.assertIn("vkGetQueryPoolResults(", get_results_block)
        self.assertIn("VkQueryResultFlags", get_results_block)
        self.assertNotIn("executor_request_lock(", get_results_block)
        self.assertNotRegex(
            get_results_block,
            r"pthread_mutex_lock\s*\(\s*&g_executor_request_mutex\s*\)",
        )

    def test_phase1_executor_query_registry_is_cleaned_at_shutdown(self) -> None:
        cleanup = c_function_body(self.executor, "cleanup_executor_query_pool_registry")
        server = c_function_body(self.executor, "serve_socket")
        self.assertIn("vkDeviceWaitIdle(", cleanup)
        self.assertIn("vkDestroyQueryPool(", cleanup)
        self.assertIn("cleanup_executor_query_pool_registry();", server)
        self.assertIn("atexit(cleanup_executor_query_pool_registry)", self.executor)

    def test_phase2_replay_query_identity_is_keyed_only_by_pool_id(self) -> None:
        finder = c_function(self.executor, "find_vulkan_graphics_replay_query_pool")
        retain = c_function_body(self.executor, "retain_vulkan_graphics_replay_query_pool")
        self.assertNotIn("result_fd_index", finder)
        self.assertIn("queries->pools[i].query_pool_id == query_pool_id", finder)
        self.assertIn("retain_executor_query_pool_for_replay(", retain)
        self.assertIn("pool->query_pool_id = query_pool_id", retain)

    def test_phase2_replay_missing_query_ids_fail_closed(self) -> None:
        retain_registry = c_function_body(
            self.executor, "retain_executor_query_pool_for_replay")
        materialize = c_function_body(
            self.executor, "materialize_vulkan_graphics_v617_queries")
        self.assertIn("if (!entry) return -ENOENT;", retain_registry)
        self.assertIn("if (rc != 0) return rc;", materialize)
        self.assertNotIn("vkCreateQueryPool(", materialize)

    def test_phase2_recorded_query_ops_use_retained_native_pool(self) -> None:
        record = c_function_body(
            self.executor, "record_vulkan_graphics_v6_command_buffer")
        for command in (
            "PDOCKER_GPU_GRAPHICS_V6_COMMAND_RESET_QUERY_POOL",
            "PDOCKER_GPU_GRAPHICS_V6_COMMAND_WRITE_TIMESTAMP",
            "PDOCKER_GPU_GRAPHICS_V6_COMMAND_BEGIN_QUERY",
            "PDOCKER_GPU_GRAPHICS_V6_COMMAND_END_QUERY",
            "PDOCKER_GPU_GRAPHICS_V6_COMMAND_COPY_QUERY_POOL_RESULTS",
        ):
            self.assertIn(command, record)
        self.assertIn("queries, query->query_pool_id", record)
        self.assertIn("queries, copy->query_pool_id", record)
        self.assertIn("vkCmdResetQueryPool(command_buffer, pool->pool", record)
        self.assertIn("vkCmdWriteTimestamp(command_buffer, stage, pool->pool", record)
        self.assertIn("vkCmdBeginQuery(command_buffer, pool->pool", record)
        self.assertIn("vkCmdEndQuery(command_buffer, pool->pool", record)
        self.assertIn("vkCmdCopyQueryPoolResults(command_buffer, pool->pool", record)

    def test_native_query_pool_identity_is_not_recreated_per_submit(self) -> None:
        materialize = c_function_body(self.executor, "materialize_vulkan_graphics_v617_queries")
        self.assertNotIn(
            "vkCreateQueryPool(", materialize,
            "native query pools must be looked up by persistent transport identity, not created per frame",
        )

    def test_native_query_pool_identity_is_not_destroyed_after_each_submit(self) -> None:
        destroy_replay = c_function_body(self.executor, "destroy_vulkan_graphics_replay_queries")
        self.assertNotIn(
            "vkDestroyQueryPool(", destroy_replay,
            "per-submit replay cleanup must release references, not destroy persistent native query pools",
        )
        self.assertIn(
            "release_executor_query_pool_entry(", destroy_replay,
            "per-submit replay cleanup must release its persistent-registry reference",
        )

    def test_timestamp_limits_are_executor_derived(self) -> None:
        body = c_function_body(self.icd, "fill_physical_device_properties")
        failures = []
        for field in ("timestampComputeAndGraphics", "timestampPeriod"):
            match = re.search(rf"{field}\s*=\s*(?P<rhs>[^;]+);", body)
            if not match:
                failures.append(f"{field}: missing")
                continue
            rhs = match.group("rhs").strip()
            if not any(marker in rhs for marker in ("caps", "snapshot", "executor")):
                failures.append(f"{field}: hard-coded RHS {rhs!r}")
        self.assertEqual(
            [], failures,
            "timestamp limits must come from the frozen executor capability snapshot: "
            + "; ".join(failures),
        )

    def test_timestamp_valid_bits_are_executor_derived(self) -> None:
        failures = []
        for function_name in (
            "vkGetPhysicalDeviceQueueFamilyProperties",
            "vkGetPhysicalDeviceQueueFamilyProperties2",
        ):
            body = c_function_body(self.icd, function_name)
            match = re.search(r"timestampValidBits\s*=\s*(?P<rhs>[^;]+);", body)
            if not match:
                failures.append(f"{function_name}: missing")
                continue
            rhs = match.group("rhs").strip()
            if re.fullmatch(r"(?:0x[0-9a-fA-F]+|[0-9]+)[uUlL]*", rhs):
                failures.append(f"{function_name}: hard-coded RHS {rhs!r}")
        self.assertEqual(
            [], failures,
            "timestampValidBits must be derived from the executor queue-family snapshot: "
            + "; ".join(failures),
        )


if __name__ == "__main__":
    unittest.main()

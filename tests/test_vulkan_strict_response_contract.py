#!/usr/bin/env python3
"""Fail-closed contracts for Vulkan executor JSON responses.

This test is intentionally stricter than the current transport parser.  Text
and V6 terminal responses cross a trust boundary and therefore must be parsed
as one top-level JSON object, not recognized with substring searches.  A red
test identifies a protocol-hardening gap; it must not be weakened to preserve
acceptance of malformed executor output.

The compiled harness extracts the parser functions from the production ICD so
that the behavioral checks execute the C implementation rather than a Python
model of it.  This file is test-only and does not alter production sources.
"""

from __future__ import annotations

import errno
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
ICD_SOURCE = ROOT / "docker-proot-setup/src/gpu/pdocker_vulkan_icd.c"
EXECUTOR_SOURCE = ROOT / "app/src/main/cpp/pdocker_gpu_executor.c"

TEXT_STAGE = "vulkan-query-pool-reset"
TEXT_CORRELATION = 41
V6_STAGE = "vulkan-graphics-v6-replay"
V6_SUBMIT_ID = 73
V5_STAGE = "vulkan-dispatch-v5-complete"
V5_DISPATCH_ID = 91


def _matching_delimiter(
    source: str, start: int, opening: str, closing: str
) -> int:
    """Find a C delimiter while ignoring comments and string literals."""
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


def c_function(source: str, name: str) -> str:
    """Return a complete C function definition, skipping calls/prototypes."""
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
        return source[0 if start < 0 else start + 2 : end]
    raise AssertionError(f"C function definition not found: {name}")


class VulkanStrictResponseContractTest(unittest.TestCase):
    """Specify exact response schemas and execute the current C parser."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.icd = ICD_SOURCE.read_text(encoding="utf-8")
        cls.executor = EXECUTOR_SOURCE.read_text(encoding="utf-8")
        cls._tmp: tempfile.TemporaryDirectory[str] | None = None
        cls.harness: Path | None = None
        if shutil.which("gcc"):
            cls._tmp = tempfile.TemporaryDirectory(prefix="vulkan-strict-response-")
            tmp = Path(cls._tmp.name)
            source_path = tmp / "strict_response_harness.c"
            cls.harness = tmp / "strict_response_harness"
            source_path.write_text(cls._harness_source(), encoding="utf-8")
            compiled = subprocess.run(
                [
                    "gcc",
                    "-std=c11",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    str(source_path),
                    "-o",
                    str(cls.harness),
                ],
                text=True,
                capture_output=True,
                timeout=30,
                check=False,
            )
            if compiled.returncode != 0:
                raise AssertionError(
                    "failed to compile extracted Vulkan response parser:\n"
                    + compiled.stderr
                )

    @classmethod
    def tearDownClass(cls) -> None:
        if cls._tmp is not None:
            cls._tmp.cleanup()

    @classmethod
    def _harness_source(cls) -> str:
        parser_start = cls.icd.index(
            "typedef enum {\n    PDOCKER_EXECUTOR_JSON_INVALID"
        )
        parser_end = cls.icd.index(
            "\nstatic int send_executor_text_command_with_fds(",
            parser_start,
        )
        parser_block = cls.icd[parser_start:parser_end]
        execution_state_start = cls.icd.index(
            "typedef enum PdockerVkDispatchExecutionState"
        )
        execution_state_end = cls.icd.index(
            "\nstatic bool pdocker_vk_generic_dispatch_may_have_executed(",
            execution_state_start,
        )
        execution_state_block = cls.icd[
            execution_state_start:execution_state_end
        ]
        functions = {
            name: c_function(cls.icd, name)
            for name in (
                "pdocker_vk_executor_response_deadline_start",
                "pdocker_vk_read_response_byte_before_deadline",
                "read_executor_text_response_line",
                "send_executor_text_command_with_fds",
                "dispatch_response_has_stage",
                "dispatch_response_is_terminal_success",
                "read_dispatch_response_status",
            )
        }
        return textwrap.dedent(
            f"""
            #define _POSIX_C_SOURCE 200809L
            #include <stdbool.h>
            #include <errno.h>
            #include <limits.h>
            #include <poll.h>
            #include <stdint.h>
            #include <stdio.h>
            #include <stdlib.h>
            #include <string.h>
            #include <time.h>
            #include <unistd.h>

            #define PDOCKER_GPU_TRANSPORT_MAX_PASSED_FDS 8u
            #define PDOCKER_VK_EXECUTOR_RESPONSE_TIMEOUT_SECONDS 30
            typedef int32_t VkResult;
            #define VK_SUCCESS 0
            #define VK_ERROR_UNKNOWN (-13)

            typedef struct PdockerVkCapabilitySnapshot {{
                int unused;
            }} PdockerVkCapabilitySnapshot;

            static int g_response_fd = -1;

            static int connect_queue_for_snapshot(
                    const PdockerVkCapabilitySnapshot *snapshot) {{
                (void)snapshot;
                int fd = g_response_fd;
                g_response_fd = -1;
                return fd;
            }}

            static int write_exact_fd(int fd, const void *data, size_t size) {{
                (void)fd;
                (void)data;
                (void)size;
                return 0;
            }}

            static int sendmsg_exact_with_fds(
                    int fd,
                    const void *data,
                    size_t size,
                    const int *fds,
                    size_t fd_count) {{
                (void)fd;
                (void)data;
                (void)size;
                (void)fds;
                (void)fd_count;
                return 0;
            }}

            static bool trace_allocations(void) {{
                return false;
            }}

            static bool env_truthy_default(
                    const char *name, bool fallback) {{
                (void)name;
                return fallback;
            }}

            {functions['pdocker_vk_executor_response_deadline_start']}
            {functions['pdocker_vk_read_response_byte_before_deadline']}
            {functions['read_executor_text_response_line']}
            {parser_block}
            {functions['send_executor_text_command_with_fds']}
            {execution_state_block}
            {functions['dispatch_response_has_stage']}
            {functions['dispatch_response_is_terminal_success']}
            {functions['read_dispatch_response_status']}

            static int response_pipe(const char *response) {{
                int fds[2];
                if (!response || pipe(fds) != 0) return -1;
                size_t length = strlen(response);
                if (write(fds[1], response, length) != (ssize_t)length ||
                    write(fds[1], "\\n", 1) != 1) {{
                    close(fds[0]);
                    close(fds[1]);
                    return -1;
                }}
                close(fds[1]);
                return fds[0];
            }}

            int main(int argc, char **argv) {{
                if (argc != 5) return 64;
                char *end = NULL;
                unsigned long long expected_id = strtoull(argv[4], &end, 10);
                if (!end || *end != '\\0') return 65;
                int fd = response_pipe(argv[2]);
                if (fd < 0) return 66;

                int rc = 0;
                if (strcmp(argv[1], "text") == 0) {{
                    PdockerVkCapabilitySnapshot snapshot = {{0}};
                    int result = VK_ERROR_UNKNOWN;
                    uint64_t correlation = 0;
                    g_response_fd = fd;
                    rc = send_executor_text_command_with_fds(
                        &snapshot, "TEST\\n", NULL, 0,
                        &result, NULL, NULL, &correlation,
                        argv[3], (uint64_t)expected_id);
                    printf("%d %d %llu\\n", rc, result,
                           (unsigned long long)correlation);
                }} else if (strcmp(argv[1], "v6") == 0 ||
                           strcmp(argv[1], "v5") == 0) {{
                    VkResult terminal_result = VK_ERROR_UNKNOWN;
                    bool terminal_received = false;
                    if (strcmp(argv[1], "v5") == 0) {{
                        g_generic_dispatch_execution_state =
                            PDOCKER_VK_DISPATCH_AMBIGUOUS;
                    }}
                    rc = read_dispatch_response_status(
                        fd, "VULKAN_TEST", argv[3],
                        (uint64_t)expected_id, &terminal_result,
                        &terminal_received);
                    close(fd);
                    printf("%d %d %d %d\\n", rc, (int)terminal_result,
                           terminal_received ? 1 : 0,
                           (int)g_generic_dispatch_execution_state);
                }} else {{
                    close(fd);
                    return 67;
                }}
                return 0;
            }}
            """
        )

    def _run_parser(
        self, mode: str, response: str, stage: str, correlation: int
    ) -> tuple[int, ...]:
        if self.harness is None:
            self.skipTest("gcc is required for the extracted C parser harness")
        completed = subprocess.run(
            [str(self.harness), mode, response, stage, str(correlation)],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        try:
            return tuple(int(field) for field in completed.stdout.strip().split())
        except ValueError as exc:
            self.fail(f"unparseable harness output {completed.stdout!r}: {exc}")

    def _text_rc(self, response: str) -> int:
        return self._run_parser(
            "text", response, TEXT_STAGE, TEXT_CORRELATION
        )[0]

    def _v6_rc(self, response: str) -> int:
        return self._run_parser("v6", response, V6_STAGE, V6_SUBMIT_ID)[0]

    def _v5_result(self, response: str) -> tuple[int, ...]:
        return self._run_parser("v5", response, V5_STAGE, V5_DISPATCH_ID)

    def assert_protocol_failures(
        self, cases: list[tuple[str, str, str]]
    ) -> None:
        """Report every malformed case not rejected specifically as EPROTO."""
        failures: list[str] = []
        for label, mode, response in cases:
            rc = self._text_rc(response) if mode == "text" else self._v6_rc(response)
            if rc != -errno.EPROTO:
                failures.append(f"{label}: rc={rc}, expected={-errno.EPROTO}")
        self.assertEqual([], failures, "\n" + "\n".join(failures))

    def test_compiled_parser_accepts_only_the_canonical_schema_baseline(self) -> None:
        text_response = (
            '{"valid":true,"stage":"vulkan-query-pool-reset",'
            '"result":0,"query_pool_id":41}'
        )
        v6_response = (
            '{"valid":true,"stage":"vulkan-graphics-v6-replay",'
            '"execution_implemented":true,"result":0,"submit_id":73}'
        )
        self.assertEqual((0, 0, TEXT_CORRELATION), self._run_parser(
            "text", text_response, TEXT_STAGE, TEXT_CORRELATION
        ))
        self.assertEqual(0, self._v6_rc(v6_response))
        v6_error_terminal = (
            '{"valid":true,"stage":"vulkan-graphics-v6-replay",'
            '"execution_implemented":false,"result":-4,"submit_id":73}'
        )
        self.assertNotEqual(
            -errno.EPROTO,
            self._v6_rc(v6_error_terminal),
            "a correlated native/preflight error is still a valid terminal",
        )

    def test_v5_terminal_carries_exact_correlated_result_and_execution_state(self) -> None:
        success = (
            '{"valid":true,"stage":"vulkan-dispatch-v5-complete",'
            '"execution_implemented":true,"result":0,"submit_id":91}'
        )
        native_error = (
            '{"valid":true,"stage":"vulkan-dispatch-v5-complete",'
            '"execution_implemented":true,"result":-4,"submit_id":91}'
        )
        preflight_error = (
            '{"valid":true,"stage":"vulkan-dispatch-v5-complete",'
            '"execution_implemented":false,"result":-8,"submit_id":91}'
        )
        self.assertEqual((0, 0, 1, 1), self._v5_result(success))
        self.assertEqual((-errno.EREMOTEIO, -4, 1, 1), self._v5_result(native_error))
        self.assertEqual((-errno.EREMOTEIO, -8, 1, 0), self._v5_result(preflight_error))

    def test_v5_terminal_wrong_correlation_or_malformed_json_is_protocol_error(self) -> None:
        wrong_id = (
            '{"valid":true,"stage":"vulkan-dispatch-v5-complete",'
            '"execution_implemented":true,"result":0,"submit_id":92}'
        )
        missing_result = (
            '{"valid":true,"stage":"vulkan-dispatch-v5-complete",'
            '"execution_implemented":true,"submit_id":91}'
        )
        wrong = self._v5_result(wrong_id)
        missing = self._v5_result(missing_result)
        self.assertEqual(-errno.EPROTO, wrong[0])
        self.assertEqual(2, wrong[3], "wrong correlation remains ambiguous")
        self.assertEqual(-errno.EPROTO, missing[0])
        self.assertEqual(2, missing[3], "malformed terminal remains ambiguous")

        impossible_success = (
            '{"valid":true,"stage":"vulkan-dispatch-v5-complete",'
            '"execution_implemented":false,"result":0,"submit_id":91}'
        )
        impossible = self._v5_result(impossible_success)
        self.assertEqual(-errno.EPROTO, impossible[0])
        self.assertEqual(2, impossible[3])

    def test_v5_executor_has_one_terminal_emission_site_per_complete_frame(self) -> None:
        handler = c_function(self.executor, "handle_vulkan_dispatch_v5_frame")
        writer = c_function(self.executor, "write_vulkan_dispatch_v5_terminal")
        self.assertNotIn("terminal_success", self.executor)
        self.assertEqual(1, handler.count("write_vulkan_dispatch_v5_terminal("))
        self.assertIn("if (frame_received)", handler)
        self.assertIn('\\"result\\":%d', writer)
        self.assertIn('\\"execution_implemented\\":%s', writer)
        self.assertIn('\\"submit_id\\":%llu', writer)

    def test_v5_icd_always_requires_and_propagates_the_correlated_terminal(self) -> None:
        sender = c_function(self.icd, "send_generic_vulkan_dispatch_op")
        mapper = c_function(self.icd, "pdocker_vk_generic_dispatch_result")
        self.assertIn('"vulkan-dispatch-v5-complete"', sender)
        self.assertIn("dispatch_id", sender)
        self.assertIn("&terminal_result, &terminal_received", sender)
        self.assertNotIn(
            'persistent_v5_transport ? "vulkan-dispatch-v5-complete" : NULL',
            sender,
        )
        self.assertIn("terminal_received", mapper)
        self.assertIn("pdocker_vk_executor_submit_result(rc)", mapper)
        self.assertIn("pdocker_vk_generic_dispatch_may_have_executed()", mapper)
        self.assertIn("PDOCKER_VK_DISPATCH_AMBIGUOUS", sender)

    def test_required_members_must_be_direct_children_of_one_top_level_object(self) -> None:
        self.assert_protocol_failures([
            (
                "text members nested in wrapper",
                "text",
                '{"wrapper":{"valid":true,"stage":"vulkan-query-pool-reset",'
                '"result":0,"query_pool_id":41}}',
            ),
            (
                "V6 members nested in wrapper",
                "v6",
                '{"wrapper":{"valid":true,"stage":"vulkan-graphics-v6-replay",'
                '"execution_implemented":true,"submit_id":73}}',
            ),
        ])

    def test_valid_is_exactly_one_top_level_boolean_true(self) -> None:
        text_tail = (
            '"stage":"vulkan-query-pool-reset","result":0,"query_pool_id":41}'
        )
        v6_tail = (
            '"stage":"vulkan-graphics-v6-replay",'
            '"execution_implemented":true,"submit_id":73}'
        )
        self.assert_protocol_failures([
            ("text duplicate valid", "text", '{"valid":true,"valid":true,' + text_tail),
            ("text contradictory valid", "text", '{"valid":true,"valid":false,' + text_tail),
            ("text valid token suffix", "text", '{"valid":trueish,' + text_tail),
            ("V6 duplicate valid", "v6", '{"valid":true,"valid":true,' + v6_tail),
            ("V6 contradictory valid", "v6", '{"valid":true,"valid":false,' + v6_tail),
            ("V6 valid token suffix", "v6", '{"valid":trueish,' + v6_tail),
        ])

    def test_stage_is_exactly_one_top_level_string_with_expected_value(self) -> None:
        self.assert_protocol_failures([
            (
                "text duplicate stage",
                "text",
                '{"valid":true,"stage":"vulkan-query-pool-reset",'
                '"stage":"other","result":0,"query_pool_id":41}',
            ),
            (
                "text stage supplied by nested object",
                "text",
                '{"valid":true,"nested":{"stage":"vulkan-query-pool-reset"},'
                '"result":0,"query_pool_id":41}',
            ),
            (
                "V6 contradictory stage",
                "v6",
                '{"valid":true,"stage":"vulkan-graphics-v6-replay",'
                '"stage":"other","execution_implemented":true,"submit_id":73}',
            ),
            (
                "V6 stage supplied by nested object",
                "v6",
                '{"valid":true,"nested":{"stage":"vulkan-graphics-v6-replay"},'
                '"execution_implemented":true,"submit_id":73}',
            ),
        ])

    def test_text_result_is_exactly_one_top_level_i32(self) -> None:
        prefix = '{"valid":true,"stage":"vulkan-query-pool-reset",'
        suffix = ',"query_pool_id":41}'
        self.assert_protocol_failures([
            ("duplicate result", "text", prefix + '"result":0,"result":-4' + suffix),
            ("string result", "text", prefix + '"result":"0"' + suffix),
            ("boolean result", "text", prefix + '"result":true' + suffix),
            ("fractional result", "text", prefix + '"result":0.0' + suffix),
            (
                "result supplied by nested object",
                "text",
                prefix + '"nested":{"result":0}' + suffix,
            ),
        ])

    def test_text_correlation_is_one_top_level_u64_and_must_match(self) -> None:
        prefix = (
            '{"valid":true,"stage":"vulkan-query-pool-reset","result":0,'
        )
        self.assert_protocol_failures([
            (
                "duplicate query_pool_id",
                "text",
                prefix + '"query_pool_id":41,"query_pool_id":41}',
            ),
            ("string query_pool_id", "text", prefix + '"query_pool_id":"41"}'),
            ("wrong query_pool_id", "text", prefix + '"query_pool_id":42}'),
            (
                "query_pool_id supplied by nested object",
                "text",
                prefix + '"nested":{"query_pool_id":41}}',
            ),
        ])

    def test_v6_execution_implemented_is_one_top_level_boolean(self) -> None:
        prefix = (
            '{"valid":true,"stage":"vulkan-graphics-v6-replay",'
        )
        suffix = ',"submit_id":73}'
        self.assert_protocol_failures([
            (
                "duplicate execution_implemented",
                "v6",
                prefix + '"execution_implemented":true,'
                '"execution_implemented":true' + suffix,
            ),
            (
                "contradictory execution_implemented",
                "v6",
                prefix + '"execution_implemented":true,'
                '"execution_implemented":false' + suffix,
            ),
            (
                "execution_implemented token suffix",
                "v6",
                prefix + '"execution_implemented":trueish' + suffix,
            ),
            (
                "nested execution_implemented",
                "v6",
                prefix + '"nested":{"execution_implemented":true}' + suffix,
            ),
        ])

    def test_v6_submit_id_is_one_top_level_u64_and_must_match(self) -> None:
        prefix = (
            '{"valid":true,"stage":"vulkan-graphics-v6-replay",'
            '"execution_implemented":true,'
        )
        self.assert_protocol_failures([
            ("duplicate submit_id", "v6", prefix + '"submit_id":73,"submit_id":73}'),
            ("string submit_id", "v6", prefix + '"submit_id":"73"}'),
            ("negative submit_id", "v6", prefix + '"submit_id":-1}'),
            ("wrong submit_id", "v6", prefix + '"submit_id":74}'),
            (
                "submit_id supplied by nested object",
                "v6",
                prefix + '"nested":{"submit_id":73}}',
            ),
        ])

    def test_substring_impostors_cannot_satisfy_required_members(self) -> None:
        self.assert_protocol_failures([
            (
                "text required fields embedded in string",
                "text",
                '{"message":"\\"valid\\":true,\\"stage\\":'
                '\\"vulkan-query-pool-reset\\",\\"result\\":0,",'
                '"query_pool_id":41}',
            ),
            (
                "V6 required fields embedded in string",
                "v6",
                '{"message":"\\"valid\\":true,\\"stage\\":'
                '\\"vulkan-graphics-v6-replay\\",'
                '\\"execution_implemented\\":true,\\"submit_id\\":73,'
                '\\"tail\\":0"}',
            ),
        ])

    def test_malformed_json_is_always_a_protocol_failure(self) -> None:
        self.assert_protocol_failures([
            (
                "text trailing comma",
                "text",
                '{"valid":true,"stage":"vulkan-query-pool-reset",'
                '"result":0,"query_pool_id":41,}',
            ),
            (
                "V6 trailing comma",
                "v6",
                '{"valid":true,"stage":"vulkan-graphics-v6-replay",'
                '"execution_implemented":true,"submit_id":73,}',
            ),
            (
                "text valid false is protocol-invalid terminal response",
                "text",
                '{"valid":false,"stage":"vulkan-query-pool-reset",'
                '"result":-4,"query_pool_id":41}',
            ),
            (
                "V6 valid false is protocol-invalid terminal response",
                "v6",
                '{"valid":false,"stage":"vulkan-graphics-v6-replay",'
                '"execution_implemented":true,"submit_id":73}',
            ),
        ])

    def test_nonterminal_diagnostics_do_not_replace_the_correlated_v6_terminal(self) -> None:
        valid_flow = (
            '{"valid":true,"stage":"diagnostic","submit_id":73}\n'
            '{"valid":true,"stage":"vulkan-graphics-v6-replay",'
            '"execution_implemented":true,"result":0,"submit_id":73}'
        )
        self.assertEqual(0, self._v6_rc(valid_flow))
        self.assert_protocol_failures([
            (
                "terminal response for wrong submit after diagnostic",
                "v6",
                '{"valid":true,"stage":"diagnostic","submit_id":73}\n'
                '{"valid":true,"stage":"vulkan-graphics-v6-replay",'
                '"execution_implemented":true,"submit_id":74}',
            ),
        ])

    def test_text_transport_has_no_permissive_substring_fallback(self) -> None:
        body = c_function(self.icd, "send_executor_text_command_with_fds")
        offenders = [
            token
            for token in (
                'strstr(line, "\\"valid\\":true")',
                "parse_executor_json_result(line, VK_SUCCESS)",
                'strstr(line, "\\"signaled\\":true")',
            )
            if token in body
        ]
        self.assertEqual(
            [],
            offenders,
            "text responses must use one strict schema parser; permissive paths remain: "
            + ", ".join(offenders),
        )

    def test_protocol_errors_are_promoted_to_sticky_device_loss(self) -> None:
        query_results = c_function(self.icd, "vkGetQueryPoolResults")
        queue_submit = c_function(self.icd, "vkQueueSubmit")
        missing: list[str] = []
        if "transport_rc != 0" not in query_results or "VK_ERROR_DEVICE_LOST" not in query_results:
            missing.append("vkGetQueryPoolResults transport -> VK_ERROR_DEVICE_LOST")
        if (
            "pdocker_vk_mark_device_lost" not in queue_submit
            or "VK_ERROR_DEVICE_LOST" not in queue_submit
        ):
            missing.append("vkQueueSubmit parser/transport -> sticky VK_ERROR_DEVICE_LOST")
        self.assertEqual(
            [],
            missing,
            "malformed executor responses must not degrade to feature-not-present: "
            + "; ".join(missing),
        )


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""P0 contracts for Vulkan idle forwarding and sticky device loss.

This test-only specification intentionally describes externally observable
Vulkan behavior rather than a particular wire spelling or field layout:

* queue/device idle must execute in the native executor, preserve VkResult,
  and fail closed when transport or response validation fails;
* because vkResetQueryPool is void, a failed native reset must poison the
  logical device so a later result-returning API can expose DEVICE_LOST.

A red test identifies a production gap.  Do not weaken these contracts by
accepting local no-op success or trace-only error handling.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ICD_SOURCE = ROOT / "docker-proot-setup/src/gpu/pdocker_vulkan_icd.c"
EXECUTOR_SOURCE = ROOT / "app/src/main/cpp/pdocker_gpu_executor.c"


def _matching_delimiter(source: str, start: int, opening: str, closing: str) -> int:
    """Find a matching C delimiter while ignoring comments and literals."""
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
    """Brace-aware function extraction with a bounded local call closure."""

    def __init__(self, source: str) -> None:
        self.source = source

    def function(self, name: str) -> str:
        for match in re.finditer(rf"\b{re.escape(name)}\s*\(", self.source):
            open_paren = self.source.index("(", match.start())
            close_paren = _matching_delimiter(self.source, open_paren, "(", ")")
            brace = close_paren + 1
            while brace < len(self.source) and self.source[brace].isspace():
                brace += 1
            if brace >= len(self.source) or self.source[brace] != "{":
                continue
            end = _matching_delimiter(self.source, brace, "{", "}") + 1
            start = self.source.rfind("\n\n", 0, match.start())
            return self.source[0 if start < 0 else start + 2 : end]
        raise AssertionError(f"C function definition not found: {name}")

    def function_names(self) -> list[str]:
        names: list[str] = []
        for match in re.finditer(r"\b([A-Za-z_]\w*)\s*\(", self.source):
            name = match.group(1)
            try:
                open_paren = self.source.index("(", match.start())
                close_paren = _matching_delimiter(
                    self.source, open_paren, "(", ")"
                )
            except AssertionError:
                continue
            brace = close_paren + 1
            while brace < len(self.source) and self.source[brace].isspace():
                brace += 1
            if brace < len(self.source) and self.source[brace] == "{":
                names.append(name)
        return list(dict.fromkeys(names))

    def closure(self, root: str, max_depth: int = 5) -> str:
        pending = [(root, 0)]
        visited: set[str] = set()
        chunks: list[str] = []
        ignored = {"if", "for", "while", "switch", "sizeof", "return"}
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
                if called not in ignored and called not in visited:
                    pending.append((called, depth + 1))
        return "\n".join(chunks)

    def functions_matching(self, pattern: str) -> list[tuple[str, str]]:
        regex = re.compile(pattern, re.IGNORECASE | re.DOTALL)
        matches: list[tuple[str, str]] = []
        for name in self.function_names():
            body = self.function(name)
            if regex.search(body):
                matches.append((name, body))
        return matches


def _device_loss_state_change(text: str) -> bool:
    """Recognize an explicit device-loss state mutation, not a trace call."""
    loss_call = re.search(
        r"\b[A-Za-z_]\w*(?:device\w*(?:lost|loss)|(?:lost|loss)\w*device)\w*\s*\(",
        text,
        re.IGNORECASE,
    )
    loss_assignment = re.search(
        r"(?:->|\.)\s*[A-Za-z_]\w*(?:lost|loss)\w*\s*=",
        text,
        re.IGNORECASE,
    )
    return loss_call is not None or loss_assignment is not None


def _device_loss_check(text: str) -> bool:
    """Recognize an explicit read/check of sticky device-loss state."""
    call = re.search(
        r"\b[A-Za-z_]\w*(?:(?:is|check|has)\w*)?device\w*(?:lost|loss)\w*\s*\(",
        text,
        re.IGNORECASE,
    ) or re.search(
        r"\b[A-Za-z_]\w*(?:lost|loss)\w*device\w*\s*\(",
        text,
        re.IGNORECASE,
    )
    field_read = re.search(
        r"(?:->|\.)\s*[A-Za-z_]\w*(?:lost|loss)\w*(?!\s*=)",
        text,
        re.IGNORECASE,
    )
    return call is not None or field_read is not None


def _if_blocks(function: str) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    for match in re.finditer(r"\bif\s*\(", function):
        open_paren = function.index("(", match.start())
        close_paren = _matching_delimiter(function, open_paren, "(", ")")
        brace = close_paren + 1
        while brace < len(function) and function[brace].isspace():
            brace += 1
        if brace < len(function) and function[brace] == "{":
            end = _matching_delimiter(function, brace, "{", "}") + 1
            blocks.append((function[open_paren + 1 : close_paren], function[brace:end]))
        else:
            statement_end = function.find(";", brace)
            if statement_end >= 0:
                blocks.append(
                    (function[open_paren + 1 : close_paren], function[brace : statement_end + 1])
                )
    return blocks


class VulkanIdleAndStickyLossContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.icd_text = ICD_SOURCE.read_text(encoding="utf-8")
        cls.executor_text = EXECUTOR_SOURCE.read_text(encoding="utf-8")
        cls.icd = CSource(cls.icd_text)
        cls.executor = CSource(cls.executor_text)

    def _assert_idle_icd_contract(self, api: str, scope: str) -> None:
        body = self.icd.function(api)
        closure = self.icd.closure(api)
        errors: list[str] = []
        action = rf"{scope}\w*(?:wait\w*)?idle|wait\w*idle\w*{scope}"
        if re.search(action, closure, re.IGNORECASE) is None:
            errors.append(f"no {scope}-idle action identity in the ICD call path")
        if re.search(r"\bsend_executor\w*\s*\(", closure) is None:
            errors.append("no executor-backed control in the ICD call path")
        if "VK_ERROR_DEVICE_LOST" not in closure:
            errors.append("transport failure does not fail closed as VK_ERROR_DEVICE_LOST")
        if re.search(r"\bEPROTO\b|protocol|strict", closure, re.IGNORECASE) is None:
            errors.append("malformed executor responses are not treated as protocol failures")
        if re.search(r"return\s+VK_SUCCESS\s*;", body):
            errors.append("entry point still contains unconditional local VK_SUCCESS")

        returned = re.findall(r"\breturn\s+([A-Za-z_]\w*)\s*;", body)
        preserves_native = False
        for variable in returned:
            declared_result = re.search(
                rf"\bVkResult\s+{re.escape(variable)}\b", body
            ) is not None
            executor_owned = (
                re.search(
                    rf"send_executor\w*\s*\([^;]*&\s*{re.escape(variable)}\b",
                    closure,
                    re.DOTALL,
                )
                is not None
                or re.search(
                    rf"\b{re.escape(variable)}\s*=\s*send_executor\w*\s*\(",
                    body,
                )
                is not None
            )
            preserves_native = preserves_native or (declared_result and executor_owned)
        if not preserves_native:
            errors.append("native VkResult is not returned unchanged by the ICD entry point")
        self.assertFalse(errors, "; ".join(errors))

    def test_queue_wait_idle_uses_executor_and_preserves_result(self) -> None:
        self._assert_idle_icd_contract("vkQueueWaitIdle", "queue")

    def test_device_wait_idle_uses_executor_and_preserves_result(self) -> None:
        self._assert_idle_icd_contract("vkDeviceWaitIdle", "device")

    def test_executor_idle_controls_call_native_and_report_exact_vkresult(self) -> None:
        errors: list[str] = []
        for scope, native_api in (
            ("queue", "vkQueueWaitIdle"),
            ("device", "vkDeviceWaitIdle"),
        ):
            action = rf"{scope}\w*(?:wait\w*)?idle|wait\w*idle\w*{scope}"
            candidates = self.executor.functions_matching(action)
            valid = False
            for _name, body in candidates:
                native = re.search(
                    rf"\bVkResult\s+([A-Za-z_]\w*)\s*=\s*{native_api}\s*\(",
                    body,
                )
                if native is None:
                    continue
                result_name = native.group(1)
                reports_same_result = re.search(
                    rf"\b(?:print|write|send|respond)[A-Za-z_]\w*\s*\([^;]*\b{re.escape(result_name)}\b",
                    body,
                    re.DOTALL,
                ) is not None
                coerces_result = re.search(
                    rf"{re.escape(result_name)}\s*==\s*VK_SUCCESS\s*\?",
                    body,
                ) is not None
                if reports_same_result and not coerces_result:
                    valid = True
                    break
            if not valid:
                errors.append(
                    f"no {scope}-idle executor handler calls {native_api} and reports its exact VkResult"
                )
        self.assertFalse(errors, "; ".join(errors))

    def _assert_reset_failure_marks_loss(self, failure_kind: str) -> None:
        reset = self.icd.function("vkResetQueryPool")
        blocks = _if_blocks(reset)
        if failure_kind == "transport":
            condition_matches = lambda condition: (
                re.search(r"transport\w*\s*!=\s*0", condition, re.IGNORECASE)
                is not None
            )
        else:
            condition_matches = lambda condition: (
                re.search(
                    r"(?:native|result)\w*\s*!=\s*VK_SUCCESS",
                    condition,
                    re.IGNORECASE,
                )
                is not None
            )
        matching = [body for condition, body in blocks if condition_matches(condition)]
        self.assertTrue(
            matching,
            f"vkResetQueryPool has no explicit {failure_kind}-failure branch",
        )
        self.assertTrue(
            any(_device_loss_state_change(body) for body in matching),
            f"{failure_kind} failure only traces/continues; it must mark sticky device loss",
        )

    def test_reset_query_pool_transport_failure_marks_sticky_device_loss(self) -> None:
        self._assert_reset_failure_marks_loss("transport")

    def test_reset_query_pool_native_failure_marks_sticky_device_loss(self) -> None:
        self._assert_reset_failure_marks_loss("native")

    def test_query_results_observe_sticky_device_loss_before_executor_io(self) -> None:
        body = self.icd.function("vkGetQueryPoolResults")
        errors: list[str] = []
        if not _device_loss_check(body):
            errors.append("vkGetQueryPoolResults does not check sticky device loss")
        if "VK_ERROR_DEVICE_LOST" not in body:
            errors.append("vkGetQueryPoolResults cannot expose sticky VK_ERROR_DEVICE_LOST")
        loss_pos = min(
            (match.start() for match in re.finditer(r"(?:lost|loss)", body, re.IGNORECASE)),
            default=len(body),
        )
        transport_pos = body.find("send_executor")
        if transport_pos >= 0 and loss_pos > transport_pos:
            errors.append("sticky loss is checked only after new executor I/O")
        self.assertFalse(errors, "; ".join(errors))

    def test_device_result_apis_observe_the_same_sticky_loss(self) -> None:
        errors: list[str] = []
        for api in ("vkQueueWaitIdle", "vkDeviceWaitIdle"):
            body = self.icd.function(api)
            if not _device_loss_check(body) or "VK_ERROR_DEVICE_LOST" not in body:
                errors.append(f"{api} does not expose the device's sticky loss")
        self.assertFalse(errors, "; ".join(errors))


if __name__ == "__main__":
    unittest.main()

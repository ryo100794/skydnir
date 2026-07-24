#!/usr/bin/env python3
"""Verify Vulkan ICD device-socket gate artifacts.

By default this is a promotion verifier: success:false artifacts fail. Use
--allow-planned-skip when validating that a disconnected/missing-prerequisite
run still produced a structured non-promoting artifact.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import uuid
from pathlib import Path

SCHEMA = "skydnir.vulkan.icd.device-socket.v1"
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
RUNNER = REPOSITORY_ROOT / "scripts/test/android-vulkan-icd-device-socket-smoke.sh"
P0_SOURCE = REPOSITORY_ROOT / "tests/device/skydnir-vulkan-p0-smoke.c"
ICD_SOURCE = REPOSITORY_ROOT / "docker-proot-setup/lib/pdocker-vulkan-icd.so"
STORAGE_SMOKE_SCRIPT = REPOSITORY_ROOT / "scripts/test/smoke-vulkan-icd-storage-image.sh"
GPU_EXECUTOR_SOURCE = REPOSITORY_ROOT / "app/src/main/cpp/pdocker_gpu_executor.c"
EXECUTOR_RESPONSE_PREFIX = "pdocker-vulkan-icd: executor response: "
DISPATCH_RESPONSE_RE = re.compile(
    r"^pdocker-vulkan-icd: (?P<transport>[^:\r\n]+) dispatch response: (?P<payload>.*)$"
)
DISPATCH_LIFECYCLE_PREFIX = "pdocker-vulkan-icd: generic dispatch lifecycle: "


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"verify-vulkan-icd-device-socket-artifact: FAIL: {message}")


def exact_int(value: object) -> bool:
    return type(value) is int


def require_vk_result(value: object, allowed: tuple[int, ...], message: str) -> None:
    require(exact_int(value) and value in allowed, message)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_path(path: Path) -> str:
    try:
        return sha256_bytes(path.read_bytes())
    except FileNotFoundError as exc:
        raise SystemExit(
            f"verify-vulkan-icd-device-socket-artifact: FAIL: "
            f"current provenance file is missing: {path}"
        ) from exc


def current_storage_source_bytes() -> bytes:
    script = STORAGE_SMOKE_SCRIPT.read_text(encoding="utf-8")
    match = re.search(
        r"cat >\"\$TMP/pdocker-vk-storage-image-smoke\.c\" <<'C'\n"
        r"(?P<body>.*?)\nC\n\npython3 -",
        script,
        re.S,
    )
    require(match is not None, "current storage-image smoke C heredoc is missing")
    executor = GPU_EXECUTOR_SOURCE.read_text(encoding="utf-8")
    spv = re.search(
        r"static const uint32_t kStorageImageRoundtripSpv\[\] = "
        r"\{(?P<body>.*?)\n\};",
        executor,
        re.S,
    )
    require(spv is not None, "current storage-image SPIR-V payload is missing")
    source = match.group("body").replace(
        '#include "pdocker-storage-image-roundtrip-spv.inc"',
        spv.group("body").strip(),
    )
    return (source + "\n").encode()


def evidence_sha256(checks: dict, key: str) -> str:
    fields = str(checks.get(key, "")).strip().split()
    require(bool(fields), f"checks.{key} lacks SHA-256 evidence")
    value = fields[0].lower()
    require(
        re.fullmatch(r"[0-9a-f]{64}", value) is not None,
        f"checks.{key} does not start with a SHA-256 digest",
    )
    return value


def verify_timeout_evidence(
    value: object,
    key: str,
    command_seconds: int,
    kill_after_seconds: int,
) -> None:
    lines = [line for line in str(value).splitlines() if line.strip()]
    require(len(lines) == 1, f"successful artifact {key} must contain one record")
    match = re.fullmatch(
        r"timed_out=(true|false) exit_code=(-?[0-9]+) "
        r"timeout_seconds=([0-9]+) kill_after_seconds=([0-9]+)",
        lines[0],
    )
    require(match is not None, f"successful artifact {key} is malformed")
    require(match.group(1) == "false", f"successful artifact {key} timed out")
    require(int(match.group(2)) == 0, f"successful artifact {key} exit code is nonzero")
    require(
        int(match.group(3)) == command_seconds,
        f"successful artifact {key} timeout does not match configuration",
    )
    require(
        int(match.group(4)) == kill_after_seconds,
        f"successful artifact {key} kill-after does not match configuration",
    )


def parse_direct_preflight(value: object) -> dict:
    objects: list[dict] = []
    for line_index, line in enumerate(str(value).splitlines()):
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        objects.append(
            strict_json_object(
                stripped, f"direct preflight JSON at line {line_index + 1}"
            )
        )
    require(len(objects) == 1, "direct preflight must contain exactly one JSON object")
    direct = objects[0]
    expected_strings = {
        "executor": "pdocker-gpu-executor",
        "api": "pdocker-gpu-command-v1",
        "abi_version": "0.1",
        "backend_impl": "android_vulkan",
        "backend_affinity": "same-api",
        "transport": "direct-vulkan-storage-image-roundtrip",
        "kernel": "storage_image_roundtrip",
    }
    for key, expected in expected_strings.items():
        require(
            direct.get(key) == expected,
            f"direct preflight {key} does not match production identity",
        )
    require(direct.get("valid") is True, "direct preflight valid must be true")
    max_abs_error = direct.get("max_abs_error")
    require(
        isinstance(max_abs_error, (int, float))
        and not isinstance(max_abs_error, bool)
        and max_abs_error <= 1.0,
        "direct preflight max_abs_error exceeds tolerance",
    )
    return direct


def canonical_uuid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value.lower()
    except ValueError:
        return False


def strict_json_object(payload: str, label: str) -> dict:
    def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate key {key!r}")
            result[key] = value
        return result

    try:
        value = json.loads(payload, object_pairs_hook=reject_duplicate_keys)
    except (json.JSONDecodeError, ValueError) as exc:
        raise SystemExit(
            "verify-vulkan-icd-device-socket-artifact: FAIL: "
            f"malformed {label}: {exc}"
        ) from exc
    require(isinstance(value, dict), f"{label} must be a JSON object")
    return value


def parse_prefixed_json_lines(
    log: str, prefix: str, label: str
) -> list[tuple[int, dict]]:
    responses: list[tuple[int, dict]] = []
    for line_index, line in enumerate(log.splitlines()):
        marker_index = line.find(prefix)
        if marker_index < 0:
            continue
        payload = line[marker_index + len(prefix):]
        require(bool(payload.strip()), f"empty {label} at line {line_index + 1}")
        responses.append(
            (
                line_index,
                strict_json_object(payload, f"{label} at line {line_index + 1}"),
            )
        )
    return responses


def verify_executor_identity(response: dict, label: str) -> None:
    expected = {
        "executor": "pdocker-gpu-executor",
        "api": "pdocker-gpu-command-v1",
        "abi_version": "0.1",
    }
    for key, value in expected.items():
        require(
            response.get(key) == value,
            f"{label} {key} does not match production identity",
        )


def verify_executor_response_evidence(p0_stderr: str) -> None:
    text_responses = parse_prefixed_json_lines(
        p0_stderr, EXECUTOR_RESPONSE_PREFIX, "executor response"
    )
    for line_index, response in text_responses:
        stage = response.get("stage")
        require(
            isinstance(stage, str) and bool(stage),
            f"executor response at line {line_index + 1} lacks a string stage",
        )
        verify_executor_identity(response, f"executor response {stage}")
        require(
            response.get("valid") is True,
            f"executor response {stage} valid must be true",
        )
        require(
            exact_int(response.get("result")),
            f"executor response {stage} result must be an integer VkResult",
        )
        require_vk_result(
            response.get("result"),
            (0,),
            f"executor response {stage} must be integer VK_SUCCESS",
        )

    required_text_stages = {
        "vulkan-query-pool-create": 2,
        "vulkan-query-pool-reset": 1,
        "vulkan-query-pool-get-results": 2,
        "vulkan-queue-wait-idle": 1,
        "vulkan-device-wait-idle": 1,
    }
    for stage, minimum_count in required_text_stages.items():
        stage_responses = [
            response
            for _, response in text_responses
            if response.get("stage") == stage
        ]
        require(
            len(stage_responses) >= minimum_count,
            f"generic P0 executor stage {stage} count "
            f"{len(stage_responses)} < {minimum_count}",
        )
        for response in stage_responses:
            require_vk_result(
                response.get("result"),
                (0,),
                f"executor response {stage} result must be integer VK_SUCCESS",
            )

    query_stages = {
        "vulkan-query-pool-create",
        "vulkan-query-pool-reset",
        "vulkan-query-pool-get-results",
    }
    query_responses = [
        response
        for _, response in text_responses
        if response.get("stage") in query_stages
    ]
    for response in query_responses:
        query_pool_id = response.get("query_pool_id")
        require(
            exact_int(query_pool_id) and query_pool_id > 0,
            f"executor response {response.get('stage')} query_pool_id "
            "must be a positive integer",
        )
    created_query_pool_ids = {
        response["query_pool_id"]
        for response in query_responses
        if response.get("stage") == "vulkan-query-pool-create"
    }
    reset_query_pool_ids = {
        response["query_pool_id"]
        for response in query_responses
        if response.get("stage") == "vulkan-query-pool-reset"
    }
    result_query_pool_ids = {
        response["query_pool_id"]
        for response in query_responses
        if response.get("stage") == "vulkan-query-pool-get-results"
    }
    require(
        len(created_query_pool_ids) >= 2,
        "generic P0 must create at least two distinct executor query pools",
    )
    require(
        reset_query_pool_ids <= created_query_pool_ids,
        "executor query reset references an uncreated query_pool_id",
    )
    require(
        result_query_pool_ids <= created_query_pool_ids,
        "executor query results reference an uncreated query_pool_id",
    )
    require(
        len(result_query_pool_ids) >= 2,
        "generic P0 must read at least two distinct executor query pools",
    )

    dispatch_responses: list[tuple[int, str, dict]] = []
    for line_index, line in enumerate(p0_stderr.splitlines()):
        match = DISPATCH_RESPONSE_RE.match(line)
        if match is None:
            continue
        response = strict_json_object(
            match.group("payload"), f"dispatch response at line {line_index + 1}"
        )
        stage = response.get("stage")
        require(
            isinstance(stage, str) and bool(stage),
            f"dispatch response at line {line_index + 1} lacks a string stage",
        )
        verify_executor_identity(response, f"dispatch response {stage}")
        require(
            response.get("valid") is True,
            f"dispatch response {stage} valid must be true",
        )
        require_vk_result(
            response.get("result"),
            (0,),
            f"dispatch response {stage} must be integer VK_SUCCESS",
        )
        dispatch_responses.append(
            (line_index, match.group("transport"), response)
        )

    terminals = [
        (line_index, transport, response)
        for line_index, transport, response in dispatch_responses
        if response.get("stage") == "vulkan-dispatch-v5-complete"
    ]
    require(
        len(terminals) >= 4,
        "generic P0 executor stage vulkan-dispatch-v5-complete count "
        f"{len(terminals)} < 4",
    )

    lifecycle = parse_prefixed_json_lines(
        p0_stderr, DISPATCH_LIFECYCLE_PREFIX, "generic dispatch lifecycle"
    )
    begin_lines: dict[int, list[int]] = {}
    successful_end_lines: dict[int, list[int]] = {}
    for line_index, event in lifecycle:
        require(
            event.get("component") == "icd",
            f"generic dispatch lifecycle at line {line_index + 1} "
            "has invalid component",
        )
        event_name = event.get("event")
        require(
            event_name in ("begin", "end"),
            f"generic dispatch lifecycle at line {line_index + 1} "
            "has invalid event",
        )
        dispatch_id = event.get("dispatch_id")
        require(
            exact_int(dispatch_id) and dispatch_id > 0,
            f"generic dispatch lifecycle at line {line_index + 1} "
            "has invalid dispatch_id",
        )
        if event_name == "begin":
            begin_lines.setdefault(dispatch_id, []).append(line_index)
        elif event.get("stage") == "v5.1-response":
            require(
                exact_int(event.get("rc")) and event.get("rc") == 0,
                f"generic dispatch lifecycle {dispatch_id} "
                "did not end successfully",
            )
            successful_end_lines.setdefault(dispatch_id, []).append(line_index)

    seen_submit_ids: set[int] = set()
    for line_index, transport, response in terminals:
        require(
            transport == "VULKAN_DISPATCH_V5.1",
            "vulkan-dispatch-v5-complete used an unexpected transport label",
        )
        require(
            response.get("valid") is True,
            "vulkan-dispatch-v5-complete valid must be true",
        )
        require(
            response.get("command") == "VULKAN_DISPATCH_V5_COMPLETE",
            "vulkan-dispatch-v5-complete command marker is invalid",
        )
        require_vk_result(
            response.get("result"),
            (0,),
            "vulkan-dispatch-v5-complete result must be integer VK_SUCCESS",
        )
        require(
            response.get("execution_implemented") is True,
            "vulkan-dispatch-v5-complete execution_implemented must be true",
        )
        submit_id = response.get("submit_id")
        require(
            exact_int(submit_id) and submit_id > 0,
            "vulkan-dispatch-v5-complete submit_id must be a positive integer",
        )
        require(
            submit_id not in seen_submit_ids,
            f"duplicate vulkan-dispatch-v5-complete submit_id {submit_id}",
        )
        seen_submit_ids.add(submit_id)
        starts = begin_lines.get(submit_id, [])
        ends = successful_end_lines.get(submit_id, [])
        require(
            len(starts) == 1 and starts[0] < line_index,
            f"dispatch terminal {submit_id} lacks one preceding correlated begin",
        )
        require(
            len(ends) == 1 and line_index < ends[0],
            f"dispatch terminal {submit_id} lacks one following "
            "successful correlated end",
        )


def load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SystemExit(f"verify-vulkan-icd-device-socket-artifact: FAIL: missing artifact: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"verify-vulkan-icd-device-socket-artifact: FAIL: invalid JSON: {exc}") from exc
    require(isinstance(data, dict), "artifact root must be an object")
    return data


def verify_common(data: dict) -> None:
    require(data.get("schema") == SCHEMA, "schema mismatch")
    require(data.get("uses_host_vulkan_loader") is False, "host Vulkan loader must not be accepted as evidence")
    require(data.get("required_icd_json") == "/etc/vulkan/icd.d/pdocker-android.json", "unexpected ICD JSON path")
    require(data.get("required_socket") == "/run/pdocker-gpu/pdocker-gpu.sock", "unexpected guest socket path")
    require(data.get("app_socket") == "files/pdocker-runtime/gpu/pdocker-gpu.sock", "unexpected app socket path")
    checks = data.get("checks")
    require(isinstance(checks, dict), "checks must be an object")
    for key in [
        "adb_devices",
        "app_socket",
        "direct_preflight",
        "docker_ps",
        "guest_prereq",
        "guest_run_stdout",
        "guest_run_stderr",
        "p0_compile_stdout",
        "p0_compile_stderr",
        "p0_run_stdout",
        "p0_run_stderr",
        "guest_run_timeout",
        "p0_compile_timeout",
        "p0_run_timeout",
        "promotion_verifier_stdout",
        "promotion_verifier_stderr",
        "product_icd_manifest",
        "guest_icd_manifest",
        "package_path",
        "app_apk_sha256",
        "app_gpu_executor_sha256",
        "container_inspect",
        "container_image_id",
        "guest_icd_sha256",
        "guest_p0_source_sha256",
        "guest_storage_source_sha256",
    ]:
        require(key in checks, f"missing checks.{key}")


def verify_success_provenance(data: dict, checks: dict) -> None:
    require(data.get("state") == "passed", "successful artifact state must be passed")
    require(canonical_uuid(data.get("run_id")), "successful artifact run_id must be a canonical UUID")

    timeouts = data.get("timeouts")
    require(isinstance(timeouts, dict), "successful artifact timeouts must be an object")
    command_seconds = timeouts.get("command_seconds")
    kill_after_seconds = timeouts.get("kill_after_seconds")
    control_seconds = timeouts.get("control_seconds")
    require(exact_int(command_seconds) and command_seconds > 0,
            "timeouts.command_seconds must be a positive integer")
    require(exact_int(kill_after_seconds) and kill_after_seconds > 0,
            "timeouts.kill_after_seconds must be a positive integer")
    require(exact_int(control_seconds) and control_seconds > 0,
            "timeouts.control_seconds must be a positive integer")
    for key in ("guest_run_timeout", "p0_compile_timeout", "p0_run_timeout"):
        verify_timeout_evidence(
            checks.get(key),
            key,
            command_seconds,
            kill_after_seconds,
        )

    provenance = data.get("provenance")
    require(isinstance(provenance, dict), "successful artifact provenance must be an object")
    require(provenance.get("hash_algorithm") == "sha256",
            "successful artifact provenance hash algorithm must be sha256")
    git_commit = provenance.get("git_commit")
    require(isinstance(git_commit, str) and re.fullmatch(r"[0-9a-f]{40,64}", git_commit) is not None,
            "provenance.git_commit must be a full lowercase hexadecimal object id")
    try:
        current_commit = subprocess.check_output(
            ["git", "-C", str(REPOSITORY_ROOT), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit(
            "verify-vulkan-icd-device-socket-artifact: FAIL: "
            "cannot resolve current git commit"
        ) from exc
    require(git_commit == current_commit,
            "artifact git commit does not match the current checkout")

    current_hashes = {
        "runner_sha256": sha256_path(RUNNER),
        "p0_source_sha256": sha256_path(P0_SOURCE),
        "staged_icd_sha256": sha256_path(ICD_SOURCE),
        "storage_source_sha256": sha256_bytes(current_storage_source_bytes()),
    }
    for key, expected in current_hashes.items():
        require(provenance.get(key) == expected,
                f"provenance.{key} does not match the current gate input")

    require(bool(str(checks.get("package_path", "")).strip()),
            "successful artifact lacks installed package path evidence")
    runtime_hashes = {
        "app_apk_sha256": evidence_sha256(checks, "app_apk_sha256"),
        "app_gpu_executor_sha256": evidence_sha256(
            checks, "app_gpu_executor_sha256"
        ),
        "guest_staged_icd_sha256": evidence_sha256(
            checks, "guest_icd_sha256"
        ),
        "guest_p0_source_sha256": evidence_sha256(
            checks, "guest_p0_source_sha256"
        ),
        "guest_storage_source_sha256": evidence_sha256(
            checks, "guest_storage_source_sha256"
        ),
    }
    for key, expected in runtime_hashes.items():
        require(
            provenance.get(key) == expected,
            f"provenance.{key} does not match recorded runtime bytes",
        )
    require(
        provenance.get("guest_staged_icd_sha256")
        == provenance.get("staged_icd_sha256"),
        "guest ICD bytes differ from the immutable staged ICD snapshot",
    )
    require(
        provenance.get("guest_p0_source_sha256")
        == provenance.get("p0_source_sha256"),
        "guest P0 source differs from the immutable source snapshot",
    )
    require(
        provenance.get("guest_storage_source_sha256")
        == provenance.get("storage_source_sha256"),
        "guest storage source differs from the generated immutable snapshot",
    )
    container_image_id = str(checks.get("container_image_id", "")).strip()
    require(bool(container_image_id), "successful artifact lacks container image identity")
    require(
        provenance.get("container_image_id") == container_image_id,
        "provenance.container_image_id does not match recorded identity",
    )
    try:
        container_inspect = json.loads(str(checks.get("container_inspect", "")))
    except json.JSONDecodeError as exc:
        raise SystemExit(
            "verify-vulkan-icd-device-socket-artifact: FAIL: "
            f"container inspect evidence is invalid JSON: {exc}"
        ) from exc
    if isinstance(container_inspect, list):
        require(
            len(container_inspect) == 1,
            "container inspect evidence must contain exactly one object",
        )
        container_inspect = container_inspect[0]
    require(isinstance(container_inspect, dict),
            "container inspect evidence root must be an object")
    require(
        container_inspect.get("Image") == container_image_id,
        "container inspect Image contradicts container_image_id",
    )

    product_manifest_text = checks.get("product_icd_manifest")
    guest_manifest_text = checks.get("guest_icd_manifest")
    require(isinstance(product_manifest_text, str) and product_manifest_text.strip(),
            "successful artifact lacks product ICD manifest evidence")
    require(isinstance(guest_manifest_text, str) and guest_manifest_text.strip(),
            "successful artifact lacks guest ICD manifest evidence")
    try:
        product_manifest = json.loads(product_manifest_text)
        guest_manifest = json.loads(guest_manifest_text)
    except json.JSONDecodeError as exc:
        raise SystemExit(
            "verify-vulkan-icd-device-socket-artifact: FAIL: "
            f"invalid captured ICD manifest JSON: {exc}"
        ) from exc
    require(product_manifest == guest_manifest,
            "guest ICD manifest changed fields beyond deterministic serialization")
    for label, manifest in (("product", product_manifest), ("guest", guest_manifest)):
        require(isinstance(manifest, dict), f"{label} ICD manifest root must be an object")
        require(isinstance(manifest.get("file_format_version"), str)
                and bool(manifest["file_format_version"]),
                f"{label} ICD manifest lacks file_format_version")
        icd = manifest.get("ICD")
        require(isinstance(icd, dict), f"{label} ICD manifest lacks ICD object")
        require(icd.get("library_path") == "/usr/local/lib/pdocker-vulkan-icd.so",
                f"{label} ICD manifest library_path mismatch")
        require(isinstance(icd.get("api_version"), str) and bool(icd["api_version"]),
                f"{label} ICD manifest lacks api_version")

    manifest_hashes = {
        "product_icd_manifest_sha256": sha256_bytes(product_manifest_text.encode()),
        "guest_icd_manifest_sha256": sha256_bytes(guest_manifest_text.encode()),
    }
    for key, expected in manifest_hashes.items():
        require(provenance.get(key) == expected,
                f"provenance.{key} does not match captured manifest bytes")


def verify_success(data: dict) -> None:
    checks = data["checks"]
    verify_success_provenance(data, checks)
    require(data.get("success") is True, f"artifact is not a pass: {data.get('reason')}")
    require(data.get("reason") == "passed", "successful artifact reason must be 'passed'")
    require(data.get("exit_code") == 0 and exact_int(data.get("exit_code")),
            "successful artifact exit_code must be integer zero")
    require(bool(str(data.get("adb_serial", "")).strip()), "missing adb serial")
    require(bool(str(data.get("container", "")).strip()), "missing container id/name")
    require("present" in str(checks.get("app_socket", "")), "app socket was not observed")
    direct = str(checks.get("direct_preflight", ""))
    parse_direct_preflight(direct)
    stdout = str(checks.get("guest_run_stdout", ""))
    stderr = str(checks.get("guest_run_stderr", ""))
    match = re.search(r"storageImageMaxErr=([0-9]+)", stdout)
    require(match is not None, "guest stdout missing storageImageMaxErr")
    require(int(match.group(1)) <= 1, "storageImageMaxErr exceeds tolerance")
    require("pdocker-vulkan-icd" in stderr, "guest stderr lacks ICD bridge trace")

    p0_lines = [
        line
        for line in str(checks.get("p0_run_stdout", "")).splitlines()
        if line.strip()
    ]
    require(len(p0_lines) == 1, "generic P0 stdout must contain exactly one JSON object")
    try:
        p0 = json.loads(p0_lines[0])
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"verify-vulkan-icd-device-socket-artifact: FAIL: invalid generic P0 JSON: {exc}"
        ) from exc
    require(isinstance(p0, dict), "generic P0 report must be an object")
    require(p0.get("schema") == "skydnir.vulkan.p0.device.v1", "generic P0 schema mismatch")
    require(p0.get("success") is True, "generic P0 report is not successful")
    lanes: dict[str, dict] = {}
    for lane_name in ("query", "synchronization2", "idle", "wsi"):
        lane = p0.get(lane_name)
        require(isinstance(lane, dict), f"generic P0 missing {lane_name} object")
        require(lane.get("advertised") is True, f"generic P0 {lane_name} was not advertised")
        require(lane.get("executed") is True, f"generic P0 {lane_name} was not executed")
        require(lane.get("passed") is True, f"generic P0 {lane_name} did not pass")
        if lane_name != "idle":
            require(lane.get("unsupported") is False,
                    f"generic P0 {lane_name} contradicts pass with unsupported")
            require(lane.get("skipped") is False,
                    f"generic P0 {lane_name} contradicts pass with skipped")
        lanes[lane_name] = lane

    error = p0.get("error")
    require(isinstance(error, dict), "generic P0 missing error object")
    require(error.get("step") == "", "generic P0 success contains an error step")
    require_vk_result(
        error.get("vk_result"), (0,),
        "generic P0 success error.vk_result must be integer VK_SUCCESS",
    )

    query = lanes["query"]
    for key in (
        "executor_backed_required",
        "host_reset_advertised",
        "host_reset_executed",
        "host_reset_passed",
        "pool_created",
        "host_pool_created",
        "reset_recorded",
        "timestamp_recorded",
        "submitted",
        "get_results_attempted",
        "host_get_results_attempted",
    ):
        require(query.get(key) is True, f"generic P0 query.{key} is not true")
    require_vk_result(
        query.get("vk_result"), (0,),
        "generic P0 query did not return integer VK_SUCCESS",
    )
    require_vk_result(
        query.get("host_vk_result"), (0,),
        "generic P0 host-reset query did not return integer VK_SUCCESS",
    )
    availability = query.get("availability")
    require(
        exact_int(availability) and availability > 0,
        "generic P0 query availability must be a positive integer",
    )
    host_availability = query.get("host_availability")
    require(
        exact_int(host_availability) and host_availability > 0,
        "generic P0 host-reset query availability must be a positive integer",
    )

    synchronization2 = lanes["synchronization2"]
    require(
        synchronization2.get("feature_supported") is True,
        "generic P0 synchronization2 feature was not enabled",
    )
    require(
        synchronization2.get("core_advertised") is True
        or synchronization2.get("extension_advertised") is True,
        "generic P0 synchronization2 has no advertised API route",
    )
    require_vk_result(
        synchronization2.get("vk_result"), (0,),
        "generic P0 synchronization2 did not return integer VK_SUCCESS",
    )

    idle = lanes["idle"]
    for key in ("queue_attempted", "queue_passed", "device_attempted", "device_passed"):
        require(idle.get(key) is True, f"generic P0 idle.{key} is not true")
    require_vk_result(
        idle.get("queue_vk_result"), (0,),
        "generic P0 queue idle did not return integer VK_SUCCESS",
    )
    require_vk_result(
        idle.get("device_vk_result"), (0,),
        "generic P0 device idle did not return integer VK_SUCCESS",
    )

    wsi = lanes["wsi"]
    for key in (
        "surface_extension_advertised",
        "headless_extension_advertised",
        "swapchain_extension_advertised",
        "headless_surface_executed",
        "headless_surface_passed",
        "surface_destroyed",
        "swapchain_created",
        "acquired",
        "presented",
        "destroyed",
    ):
        require(wsi.get(key) is True, f"generic P0 wsi.{key} is not true")
    image_count = wsi.get("image_count")
    require(
        isinstance(image_count, int)
        and not isinstance(image_count, bool)
        and image_count > 0,
        "generic P0 WSI image_count must be a positive integer",
    )
    require_vk_result(
        wsi.get("vk_result"), (0, 1000001003),
        "generic P0 WSI did not return integer VK_SUCCESS or VK_SUBOPTIMAL_KHR",
    )

    discovery = p0.get("discovery")
    require(isinstance(discovery, dict), "generic P0 missing discovery object")
    require(discovery.get("instance_created") is True, "generic P0 instance was not created")
    require(discovery.get("device_created") is True, "generic P0 device was not created")
    physical_device_count = discovery.get("physical_device_count")
    require(
        isinstance(physical_device_count, int)
        and not isinstance(physical_device_count, bool)
        and physical_device_count > 0,
        "generic P0 physical_device_count must be a positive integer",
    )
    timestamp_valid_bits = discovery.get("timestamp_valid_bits")
    require(
        isinstance(timestamp_valid_bits, int)
        and not isinstance(timestamp_valid_bits, bool)
        and timestamp_valid_bits > 0,
        "generic P0 timestamp_valid_bits must be a positive integer",
    )

    p0_stderr = str(checks.get("p0_run_stderr", ""))
    require("pdocker-vulkan-icd" in p0_stderr, "generic P0 stderr lacks ICD bridge trace")
    verify_executor_response_evidence(p0_stderr)
    all_evidence = "\n".join((direct, stdout, stderr, p0_lines[0], p0_stderr))
    require("fallback" not in all_evidence.lower(), "fallback evidence is not accepted")


def verify_planned_skip(data: dict) -> None:
    require(data.get("success") is False, "planned-skip mode expected success:false")
    require(data.get("state") == "failed", "planned-skip artifact state must be failed")
    require(bool(str(data.get("reason", "")).strip()), "planned-skip artifact must include reason")
    require(isinstance(data.get("exit_code"), int) and data["exit_code"] != 0, "planned-skip artifact must keep nonzero exit_code")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--allow-planned-skip", action="store_true")
    args = parser.parse_args(argv)
    data = load(args.artifact)
    verify_common(data)
    if args.allow_planned_skip and data.get("success") is False:
        verify_planned_skip(data)
    else:
        verify_success(data)
    print("verify-vulkan-icd-device-socket-artifact: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

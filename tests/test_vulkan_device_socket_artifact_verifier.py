#!/usr/bin/env python3
"""Black-box tests for the Vulkan device-socket artifact verifier."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
VERIFIER = (
    REPOSITORY_ROOT
    / "scripts"
    / "test"
    / "verify-vulkan-icd-device-socket-artifact.py"
)
LANES = ("query", "synchronization2", "idle", "wsi")
LANE_RESULTS = ("advertised", "executed", "passed")
RUNNER = REPOSITORY_ROOT / "scripts/test/android-vulkan-icd-device-socket-smoke.sh"
P0_SOURCE = REPOSITORY_ROOT / "tests/device/skydnir-vulkan-p0-smoke.c"
ICD_SOURCE = REPOSITORY_ROOT / "docker-proot-setup/lib/pdocker-vulkan-icd.so"
STORAGE_SMOKE_SCRIPT = REPOSITORY_ROOT / "scripts/test/smoke-vulkan-icd-storage-image.sh"
GPU_EXECUTOR_SOURCE = REPOSITORY_ROOT / "app/src/main/cpp/pdocker_gpu_executor.c"
PRODUCT_MANIFEST = (
    json.dumps(
        {
            "file_format_version": "1.0.0",
            "ICD": {
                "library_path": "/usr/local/lib/pdocker-vulkan-icd.so",
                "api_version": "1.3.0",
            },
        },
        indent=2,
        sort_keys=True,
    )
    + "\n"
)


def sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def current_storage_source_sha256() -> str:
    script = STORAGE_SMOKE_SCRIPT.read_text(encoding="utf-8")
    match = re.search(
        r"cat >\"\$TMP/pdocker-vk-storage-image-smoke\.c\" <<'C'\n"
        r"(?P<body>.*?)\nC\n\npython3 -",
        script,
        re.S,
    )
    if match is None:
        raise AssertionError("storage-image smoke C heredoc missing")
    executor = GPU_EXECUTOR_SOURCE.read_text(encoding="utf-8")
    spv = re.search(
        r"static const uint32_t kStorageImageRoundtripSpv\[\] = "
        r"\{(?P<body>.*?)\n\};",
        executor,
        re.S,
    )
    if spv is None:
        raise AssertionError("storage-image SPIR-V payload missing")
    source = match.group("body").replace(
        '#include "pdocker-storage-image-roundtrip-spv.inc"',
        spv.group("body").strip(),
    )
    return hashlib.sha256((source + "\n").encode()).hexdigest()


def valid_p0_report() -> dict[str, object]:
    return {
        "schema": "skydnir.vulkan.p0.device.v1",
        "success": True,
        "query": {
            "advertised": True,
            "executed": True,
            "passed": True,
            "unsupported": False,
            "skipped": False,
            "executor_backed_required": True,
            "host_reset_advertised": True,
            "host_reset_executed": True,
            "host_reset_passed": True,
            "pool_created": True,
            "host_pool_created": True,
            "reset_recorded": True,
            "timestamp_recorded": True,
            "submitted": True,
            "get_results_attempted": True,
            "host_get_results_attempted": True,
            "vk_result": 0,
            "host_vk_result": 0,
            "availability": 1,
            "host_availability": 1,
        },
        "synchronization2": {
            "advertised": True,
            "executed": True,
            "passed": True,
            "unsupported": False,
            "skipped": False,
            "core_advertised": True,
            "extension_advertised": False,
            "feature_supported": True,
            "vk_result": 0,
        },
        "idle": {
            "advertised": True,
            "executed": True,
            "passed": True,
            "queue_attempted": True,
            "queue_passed": True,
            "queue_vk_result": 0,
            "device_attempted": True,
            "device_passed": True,
            "device_vk_result": 0,
        },
        "wsi": {
            "advertised": True,
            "executed": True,
            "passed": True,
            "unsupported": False,
            "skipped": False,
            "vk_result": 0,
            "surface_extension_advertised": True,
            "headless_extension_advertised": True,
            "swapchain_extension_advertised": True,
            "headless_surface_executed": True,
            "headless_surface_passed": True,
            "surface_destroyed": True,
            "swapchain_created": True,
            "image_count": 2,
            "acquired": True,
            "presented": True,
            "destroyed": True,
        },
        "discovery": {
            "instance_created": True,
            "physical_device_count": 1,
            "timestamp_valid_bits": 64,
            "device_created": True,
        },
        "error": {"step": "", "vk_result": 0},
    }


def compact_json(value: dict[str, object]) -> str:
    return json.dumps(value, separators=(",", ":"))


def valid_p0_stderr() -> str:
    text_responses = (
        ("vulkan-query-pool-create", 41),
        ("vulkan-query-pool-create", 42),
        ("vulkan-query-pool-reset", 42),
        ("vulkan-query-pool-get-results", 41),
        ("vulkan-query-pool-get-results", 42),
        ("vulkan-queue-wait-idle", None),
        ("vulkan-device-wait-idle", None),
    )
    lines: list[str] = []
    for stage, query_pool_id in text_responses:
        response: dict[str, object] = {
            "executor": "pdocker-gpu-executor",
            "api": "pdocker-gpu-command-v1",
            "abi_version": "0.1",
            "stage": stage,
            "valid": True,
            "result": 0,
        }
        if query_pool_id is not None:
            response["query_pool_id"] = query_pool_id
        lines.append(
            "pdocker-vulkan-icd: executor response: " + compact_json(response)
        )

    for submit_id in range(101, 105):
        lines.append(
            "pdocker-vulkan-icd: generic dispatch lifecycle: "
            + compact_json(
                {
                    "component": "icd",
                    "event": "begin",
                    "dispatch_id": submit_id,
                }
            )
        )
        lines.append(
            "pdocker-vulkan-icd: VULKAN_DISPATCH_V5.1 dispatch response: "
            + compact_json(
                {
                    "executor": "pdocker-gpu-executor",
                    "api": "pdocker-gpu-command-v1",
                    "abi_version": "0.1",
                    "command": "VULKAN_DISPATCH_V5_COMPLETE",
                    "stage": "vulkan-dispatch-v5-complete",
                    "valid": True,
                    "execution_implemented": True,
                    "result": 0,
                    "submit_id": submit_id,
                }
            )
        )
        lines.append(
            "pdocker-vulkan-icd: generic dispatch lifecycle: "
            + compact_json(
                {
                    "component": "icd",
                    "event": "end",
                    "dispatch_id": submit_id,
                    "rc": 0,
                    "stage": "v5.1-response",
                }
            )
        )
    return "".join(f"{line}\n" for line in lines)


def mutate_first_logged_json(
    log: str,
    prefix: str,
    predicate,
    changes: dict[str, object],
) -> str:
    lines = log.splitlines()
    for index, line in enumerate(lines):
        if not line.startswith(prefix):
            continue
        value = json.loads(line[len(prefix):])
        if predicate(value):
            value.update(changes)
            lines[index] = prefix + compact_json(value)
            return "".join(f"{entry}\n" for entry in lines)
    raise AssertionError(f"no matching log object for prefix {prefix!r}")


def valid_artifact() -> dict[str, object]:
    return {
        "schema": "skydnir.vulkan.icd.device-socket.v1",
        "success": True,
        "state": "passed",
        "reason": "passed",
        "run_id": "12345678-1234-4234-8234-123456789abc",
        "exit_code": 0,
        "adb_serial": "test-device:5555",
        "container": "vulkan-device-gate",
        "uses_host_vulkan_loader": False,
        "required_icd_json": "/etc/vulkan/icd.d/pdocker-android.json",
        "required_socket": "/run/pdocker-gpu/pdocker-gpu.sock",
        "app_socket": "files/pdocker-runtime/gpu/pdocker-gpu.sock",
        "checks": {
            "adb_devices": "test-device:5555 device",
            "app_socket": "present",
            "direct_preflight": (
                "pdocker-gpu-executor: Android Vulkan enabled features\n"
                + compact_json(
                    {
                        "executor": "pdocker-gpu-executor",
                        "api": "pdocker-gpu-command-v1",
                        "abi_version": "0.1",
                        "backend_impl": "android_vulkan",
                        "backend_affinity": "same-api",
                        "transport": "direct-vulkan-storage-image-roundtrip",
                        "kernel": "storage_image_roundtrip",
                        "max_abs_error": 0.0,
                        "valid": True,
                    }
                )
                + "\n"
            ),
            "docker_ps": "vulkan-device-gate Up",
            "guest_prereq": "ready",
            "guest_run_stdout": "storageImageMaxErr=1\n",
            "guest_run_stderr": "pdocker-vulkan-icd: device bridge active\n",
            "p0_compile_stdout": "compiled\n",
            "p0_compile_stderr": "",
            "p0_run_stdout": json.dumps(valid_p0_report(), separators=(",", ":")),
            "p0_run_stderr": valid_p0_stderr(),
            "guest_run_timeout": (
                "timed_out=false exit_code=0 timeout_seconds=180 "
                "kill_after_seconds=10\n"
            ),
            "p0_compile_timeout": (
                "timed_out=false exit_code=0 timeout_seconds=180 "
                "kill_after_seconds=10\n"
            ),
            "p0_run_timeout": (
                "timed_out=false exit_code=0 timeout_seconds=180 "
                "kill_after_seconds=10\n"
            ),
            "promotion_verifier_stdout": "",
            "promotion_verifier_stderr": "",
            "product_icd_manifest": PRODUCT_MANIFEST,
            "guest_icd_manifest": PRODUCT_MANIFEST,
            "package_path": "package:/data/app/skydnir/base.apk\n",
            "app_apk_sha256": f'{"a" * 64}  /data/app/skydnir/base.apk\n',
            "app_gpu_executor_sha256": f'{"b" * 64}  pdocker-gpu-executor\n',
            "container_inspect": '[{"Image":"sha256:test-image"}]\n',
            "container_image_id": "sha256:test-image\n",
            "guest_icd_sha256": f"{sha256_path(ICD_SOURCE)}  guest-icd\n",
            "guest_p0_source_sha256": f"{sha256_path(P0_SOURCE)}  guest-p0\n",
            "guest_storage_source_sha256": (
                f"{current_storage_source_sha256()}  guest-storage\n"
            ),
        },
        "provenance": {
            "git_commit": subprocess.check_output(
                ["git", "-C", str(REPOSITORY_ROOT), "rev-parse", "HEAD"],
                text=True,
            ).strip(),
            "hash_algorithm": "sha256",
            "runner_sha256": sha256_path(RUNNER),
            "p0_source_sha256": sha256_path(P0_SOURCE),
            "staged_icd_sha256": sha256_path(ICD_SOURCE),
            "storage_source_sha256": current_storage_source_sha256(),
            "app_apk_sha256": "a" * 64,
            "app_gpu_executor_sha256": "b" * 64,
            "guest_staged_icd_sha256": sha256_path(ICD_SOURCE),
            "guest_p0_source_sha256": sha256_path(P0_SOURCE),
            "guest_storage_source_sha256": current_storage_source_sha256(),
            "container_image_id": "sha256:test-image",
            "product_icd_manifest_sha256": hashlib.sha256(
                PRODUCT_MANIFEST.encode()
            ).hexdigest(),
            "guest_icd_manifest_sha256": hashlib.sha256(
                PRODUCT_MANIFEST.encode()
            ).hexdigest(),
        },
        "timeouts": {
            "command_seconds": 180,
            "kill_after_seconds": 10,
            "control_seconds": 30,
        },
    }


class VulkanDeviceSocketArtifactVerifierTest(unittest.TestCase):
    maxDiff = None

    def invoke(
        self,
        artifact: dict[str, object],
        *arguments: str,
    ) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temporary_directory:
            artifact_path = Path(temporary_directory) / "artifact.json"
            artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(VERIFIER), str(artifact_path), *arguments],
                cwd=REPOSITORY_ROOT,
                check=False,
                capture_output=True,
                text=True,
            )

    def assert_accepted(
        self,
        artifact: dict[str, object],
        *arguments: str,
    ) -> None:
        result = self.invoke(artifact, *arguments)
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        self.assertIn("verify-vulkan-icd-device-socket-artifact: PASS", result.stdout)

    def assert_rejected(
        self,
        artifact: dict[str, object],
        *arguments: str,
    ) -> None:
        result = self.invoke(artifact, *arguments)
        self.assertNotEqual(
            result.returncode,
            0,
            msg=f"unexpected acceptance; stdout:\n{result.stdout}",
        )
        self.assertIn("verify-vulkan-icd-device-socket-artifact: FAIL:", result.stderr)

    def test_valid_promotion_artifact_is_accepted(self) -> None:
        self.assert_accepted(valid_artifact())

    def test_each_required_lane_is_rejected_when_missing(self) -> None:
        for lane in LANES:
            with self.subTest(lane=lane):
                artifact = valid_artifact()
                checks = artifact["checks"]
                self.assertIsInstance(checks, dict)
                p0 = json.loads(str(checks["p0_run_stdout"]))
                del p0[lane]
                checks["p0_run_stdout"] = json.dumps(p0)
                self.assert_rejected(artifact)

    def test_each_required_lane_result_is_rejected_when_false_or_missing(self) -> None:
        for lane in LANES:
            for lane_result in LANE_RESULTS:
                for mutation in ("false", "missing"):
                    with self.subTest(
                        lane=lane,
                        lane_result=lane_result,
                        mutation=mutation,
                    ):
                        artifact = valid_artifact()
                        checks = artifact["checks"]
                        self.assertIsInstance(checks, dict)
                        p0 = json.loads(str(checks["p0_run_stdout"]))
                        if mutation == "false":
                            p0[lane][lane_result] = False
                        else:
                            del p0[lane][lane_result]
                        checks["p0_run_stdout"] = json.dumps(p0)
                        self.assert_rejected(artifact)

    def test_critical_detailed_evidence_is_rejected_when_invalid(self) -> None:
        mutations = (
            ("query", "host_reset_passed", False),
            ("query", "host_pool_created", False),
            ("query", "host_vk_result", -4),
            ("query", "availability", 0),
            ("query", "host_availability", 0),
            ("synchronization2", "feature_supported", False),
            ("idle", "device_vk_result", -4),
            ("wsi", "surface_destroyed", False),
            ("wsi", "image_count", 0),
            ("discovery", "timestamp_valid_bits", 0),
        )
        for section, key, value in mutations:
            with self.subTest(section=section, key=key, value=value):
                artifact = valid_artifact()
                checks = artifact["checks"]
                self.assertIsInstance(checks, dict)
                p0 = json.loads(str(checks["p0_run_stdout"]))
                p0[section][key] = value
                checks["p0_run_stdout"] = json.dumps(p0)
                self.assert_rejected(artifact)

    def test_contradictory_success_evidence_is_rejected(self) -> None:
        for case in (
            "nonzero-exit",
            "nonpass-reason",
            "skipped-pass",
            "unsupported-pass",
            "bool-vk-result",
            "error-step",
            "bool-error-result",
        ):
            with self.subTest(case=case):
                artifact = valid_artifact()
                checks = artifact["checks"]
                self.assertIsInstance(checks, dict)
                p0 = json.loads(str(checks["p0_run_stdout"]))
                if case == "nonzero-exit":
                    artifact["exit_code"] = 7
                elif case == "nonpass-reason":
                    artifact["reason"] = "completed"
                elif case == "skipped-pass":
                    p0["query"]["skipped"] = True
                elif case == "unsupported-pass":
                    p0["wsi"]["unsupported"] = True
                elif case == "bool-vk-result":
                    p0["query"]["vk_result"] = False
                elif case == "error-step":
                    p0["error"]["step"] = "vkQueueSubmit2"
                elif case == "bool-error-result":
                    p0["error"]["vk_result"] = False
                checks["p0_run_stdout"] = json.dumps(p0)
                self.assert_rejected(artifact)

    def test_each_required_executor_stage_count_is_fail_closed(self) -> None:
        minimum_stages = (
            "vulkan-query-pool-create",
            "vulkan-query-pool-reset",
            "vulkan-query-pool-get-results",
            "vulkan-queue-wait-idle",
            "vulkan-device-wait-idle",
            "vulkan-dispatch-v5-complete",
        )
        for stage in minimum_stages:
            with self.subTest(stage=stage):
                artifact = valid_artifact()
                checks = artifact["checks"]
                self.assertIsInstance(checks, dict)
                marker = f'"stage":"{stage}"'
                stderr = str(checks["p0_run_stderr"])
                self.assertIn(marker, stderr)
                checks["p0_run_stderr"] = stderr.replace(marker, '"stage":"removed"', 1)
                self.assert_rejected(artifact)

    def test_required_text_executor_response_semantics_are_fail_closed(self) -> None:
        prefix = "pdocker-vulkan-icd: executor response: "
        for case, changes in (
            ("valid-false", {"valid": False}),
            ("nonzero-result", {"result": -4}),
            ("bool-result", {"result": True}),
            ("bool-query-pool-id", {"query_pool_id": True}),
            ("wrong-executor", {"executor": "other"}),
            ("wrong-api", {"api": "other"}),
            ("wrong-abi", {"abi_version": "9"}),
        ):
            with self.subTest(case=case):
                artifact = valid_artifact()
                checks = artifact["checks"]
                self.assertIsInstance(checks, dict)
                checks["p0_run_stderr"] = mutate_first_logged_json(
                    str(checks["p0_run_stderr"]),
                    prefix,
                    lambda value: value.get("stage")
                    == "vulkan-query-pool-create",
                    changes,
                )
                self.assert_rejected(artifact)

    def test_unrelated_failed_executor_response_is_rejected(self) -> None:
        artifact = valid_artifact()
        checks = artifact["checks"]
        self.assertIsInstance(checks, dict)
        checks["p0_run_stderr"] = (
            str(checks["p0_run_stderr"])
            + "pdocker-vulkan-icd: executor response: "
            + compact_json(
                {
                    "executor": "pdocker-gpu-executor",
                    "api": "pdocker-gpu-command-v1",
                    "abi_version": "0.1",
                    "stage": "vulkan-query-pool-destroy",
                    "valid": True,
                    "result": -4,
                }
            )
            + "\n"
        )
        self.assert_rejected(artifact)

    def test_direct_preflight_identity_is_fail_closed(self) -> None:
        for case, mutation in (
            ("wrong-api", {"api": "wrong"}),
            ("wrong-abi", {"abi_version": "9"}),
            ("wrong-executor", {"executor": "other"}),
            ("valid-false", {"valid": False}),
            ("bool-error", {"max_abs_error": True}),
            ("excess-error", {"max_abs_error": 2.0}),
        ):
            with self.subTest(case=case):
                artifact = valid_artifact()
                checks = artifact["checks"]
                self.assertIsInstance(checks, dict)
                lines = str(checks["direct_preflight"]).splitlines()
                direct = json.loads(lines[-1])
                direct.update(mutation)
                lines[-1] = compact_json(direct)
                checks["direct_preflight"] = "\n".join(lines) + "\n"
                self.assert_rejected(artifact)

    def test_timeout_record_must_be_unique_and_exact(self) -> None:
        for case, value in (
            (
                "contradictory",
                "timed_out=true exit_code=124 timeout_seconds=180 "
                "kill_after_seconds=10\n"
                "timed_out=false exit_code=0 timeout_seconds=180 "
                "kill_after_seconds=10\n",
            ),
            (
                "wrong-config",
                "timed_out=false exit_code=0 timeout_seconds=181 "
                "kill_after_seconds=10\n",
            ),
            ("malformed", "completed=true\n"),
        ):
            with self.subTest(case=case):
                artifact = valid_artifact()
                checks = artifact["checks"]
                self.assertIsInstance(checks, dict)
                checks["p0_run_timeout"] = value
                self.assert_rejected(artifact)

    def test_malformed_or_duplicate_executor_response_json_is_rejected(self) -> None:
        for case, line in (
            (
                "malformed-text",
                'pdocker-vulkan-icd: executor response: {"stage":\n',
            ),
            (
                "duplicate-text-key",
                'pdocker-vulkan-icd: executor response: '
                '{"stage":"diagnostic","valid":true,"valid":true,"result":0}\n',
            ),
            (
                "malformed-dispatch",
                "pdocker-vulkan-icd: VULKAN_DISPATCH_V5.1 "
                'dispatch response: {"stage":\n',
            ),
        ):
            with self.subTest(case=case):
                artifact = valid_artifact()
                checks = artifact["checks"]
                self.assertIsInstance(checks, dict)
                checks["p0_run_stderr"] = (
                    str(checks["p0_run_stderr"]) + line
                )
                self.assert_rejected(artifact)

    def test_dispatch_terminal_semantics_and_correlation_are_fail_closed(self) -> None:
        prefix = (
            "pdocker-vulkan-icd: VULKAN_DISPATCH_V5.1 dispatch response: "
        )
        for case, changes in (
            ("valid-false", {"valid": False}),
            ("nonzero-result", {"result": -4}),
            ("bool-result", {"result": True}),
            ("execution-false", {"execution_implemented": False}),
            ("wrong-command", {"command": "VULKAN_DISPATCH_V5"}),
            ("bool-submit-id", {"submit_id": True}),
            ("uncorrelated-submit-id", {"submit_id": 999}),
            ("wrong-executor", {"executor": "other"}),
            ("wrong-api", {"api": "other"}),
            ("wrong-abi", {"abi_version": "9"}),
        ):
            with self.subTest(case=case):
                artifact = valid_artifact()
                checks = artifact["checks"]
                self.assertIsInstance(checks, dict)
                checks["p0_run_stderr"] = mutate_first_logged_json(
                    str(checks["p0_run_stderr"]),
                    prefix,
                    lambda value: value.get("stage")
                    == "vulkan-dispatch-v5-complete",
                    changes,
                )
                self.assert_rejected(artifact)

    def test_dispatch_terminal_requires_successful_correlated_lifecycle_end(self) -> None:
        artifact = valid_artifact()
        checks = artifact["checks"]
        self.assertIsInstance(checks, dict)
        prefix = "pdocker-vulkan-icd: generic dispatch lifecycle: "
        checks["p0_run_stderr"] = mutate_first_logged_json(
            str(checks["p0_run_stderr"]),
            prefix,
            lambda value: value.get("event") == "end"
            and value.get("dispatch_id") == 101,
            {"rc": -5},
        )
        self.assert_rejected(artifact)

    def test_run_state_timeout_and_provenance_mismatches_are_rejected(self) -> None:
        for case in (
            "state",
            "run-id",
            "timeout",
            "git-commit",
            "runner-hash",
            "manifest-hash",
            "guest-icd-hash",
            "guest-p0-hash",
            "guest-storage-hash",
            "apk-hash",
            "container-image",
            "container-inspect-image",
        ):
            with self.subTest(case=case):
                artifact = valid_artifact()
                if case == "state":
                    artifact["state"] = "in_progress"
                elif case == "run-id":
                    artifact["run_id"] = "not-a-uuid"
                elif case == "timeout":
                    checks = artifact["checks"]
                    self.assertIsInstance(checks, dict)
                    checks["p0_run_timeout"] = (
                        "timed_out=true exit_code=124 timeout_seconds=180 "
                        "kill_after_seconds=10\n"
                    )
                elif case == "git-commit":
                    provenance = artifact["provenance"]
                    self.assertIsInstance(provenance, dict)
                    provenance["git_commit"] = "0" * 40
                elif case == "runner-hash":
                    provenance = artifact["provenance"]
                    self.assertIsInstance(provenance, dict)
                    provenance["runner_sha256"] = "0" * 64
                elif case == "manifest-hash":
                    provenance = artifact["provenance"]
                    self.assertIsInstance(provenance, dict)
                    provenance["guest_icd_manifest_sha256"] = "0" * 64
                elif case == "guest-icd-hash":
                    provenance = artifact["provenance"]
                    self.assertIsInstance(provenance, dict)
                    provenance["guest_staged_icd_sha256"] = "0" * 64
                elif case == "guest-p0-hash":
                    provenance = artifact["provenance"]
                    self.assertIsInstance(provenance, dict)
                    provenance["guest_p0_source_sha256"] = "0" * 64
                elif case == "guest-storage-hash":
                    provenance = artifact["provenance"]
                    self.assertIsInstance(provenance, dict)
                    provenance["guest_storage_source_sha256"] = "0" * 64
                elif case == "apk-hash":
                    checks = artifact["checks"]
                    self.assertIsInstance(checks, dict)
                    checks["app_apk_sha256"] = "not-a-hash"
                elif case == "container-image":
                    provenance = artifact["provenance"]
                    self.assertIsInstance(provenance, dict)
                    provenance["container_image_id"] = "sha256:other"
                elif case == "container-inspect-image":
                    checks = artifact["checks"]
                    self.assertIsInstance(checks, dict)
                    checks["container_inspect"] = (
                        '[{"Image":"sha256:contradictory"}]'
                    )
                self.assert_rejected(artifact)

    def test_multiple_nonempty_p0_lines_are_rejected(self) -> None:
        artifact = valid_artifact()
        checks = artifact["checks"]
        self.assertIsInstance(checks, dict)
        report = str(checks["p0_run_stdout"])
        checks["p0_run_stdout"] = f"{report}\n{report}\n"
        self.assert_rejected(artifact)

    def test_malformed_p0_json_line_is_rejected(self) -> None:
        artifact = valid_artifact()
        checks = artifact["checks"]
        self.assertIsInstance(checks, dict)
        checks["p0_run_stdout"] = '{"schema":'
        self.assert_rejected(artifact)

    def test_planned_skip_is_rejected_without_opt_in(self) -> None:
        artifact = valid_artifact()
        artifact.update(
            {
                "success": False,
                "state": "failed",
                "reason": "device unavailable",
                "exit_code": 75,
            }
        )
        self.assert_rejected(artifact)

    def test_planned_skip_is_accepted_only_with_opt_in(self) -> None:
        artifact = valid_artifact()
        artifact.update(
            {
                "success": False,
                "state": "failed",
                "reason": "device unavailable",
                "exit_code": 75,
            }
        )
        self.assert_accepted(artifact, "--allow-planned-skip")


if __name__ == "__main__":
    unittest.main()

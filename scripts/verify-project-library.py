#!/usr/bin/env python3
"""Offline checks for bundled pdocker project-library templates."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "app" / "src" / "main" / "assets"
LIBRARY = ASSETS / "project-library" / "library.json"
LLAMA_GPU_ENV_MANIFEST = ROOT / "scripts" / "llama-gpu-env-manifest.json"
LLAMA_GPU_COMPARE_BRIDGE_LIMITS = (
    "PDOCKER_VULKAN_MAX_BUFFER_BYTES",
    "GGML_VK_FORCE_MAX_BUFFER_SIZE",
    "GGML_VK_FORCE_MAX_ALLOCATION_SIZE",
    "GGML_VK_SUBALLOCATION_BLOCK_SIZE",
)
SKYDNIR_DOCUMENTS_VOLUME = "${SKYDNIR_DOCUMENTS_HOST:-./documents}:${SKYDNIR_DOCUMENTS_MOUNT:-/documents}"
SKYDNIR_SHARED_DOCUMENTS_VOLUME = "${SKYDNIR_SHARED_DOCUMENTS_HOST:-./shared-documents}:${SKYDNIR_SHARED_DOCUMENTS_MOUNT:-/shared}"
LEGACY_DOCUMENTS_VOLUME = "${PDOCKER_DOCUMENTS_HOST:-./documents}:${PDOCKER_DOCUMENTS_MOUNT:-/documents}"
LEGACY_SHARED_DOCUMENTS_VOLUME = "${PDOCKER_SHARED_DOCUMENTS_HOST:-./shared-documents}:${PDOCKER_SHARED_DOCUMENTS_MOUNT:-/shared}"


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    raise SystemExit(1)


def ok(msg: str) -> None:
    print(f"ok: {msg}")


def read(path: Path) -> str:
    if not path.is_file():
        fail(f"missing {path.relative_to(ROOT)}")
    return path.read_text()


def llama_gpu_compare_bridge_limits() -> dict[str, str]:
    manifest = json.loads(read(LLAMA_GPU_ENV_MANIFEST))
    profile = manifest.get("compare_mode_env_profiles", {}).get("vulkan-raw", {})
    limits = {}
    for item in profile.get("env", []):
        if isinstance(item, dict) and item.get("env") in LLAMA_GPU_COMPARE_BRIDGE_LIMITS:
            limits[str(item["env"])] = str(item.get("default", ""))
    return limits


def check_llama_gpu_compare_contract(compare_script: str) -> None:
    policy_checks = {
        "reports unmodified llama.cpp policy": '"llama_cpp_modified": False' in compare_script,
        "documents unmodified llama.cpp run": "without\nmodifying llama.cpp" in compare_script,
        "does not patch llama.cpp during compare": re.search(
            r"\b(?:git\s+(?:apply|am|checkout|reset)|patch\s+-p|sed\s+-i|perl\s+-[0-9]*pi)\b",
            compare_script,
        )
        is None,
    }
    for name, passed in policy_checks.items():
        if not passed:
            fail(f"compare script {name}")

    bridge_limits = llama_gpu_compare_bridge_limits()
    missing_limits = [name for name in LLAMA_GPU_COMPARE_BRIDGE_LIMITS if name not in bridge_limits]
    if missing_limits:
        fail(f"llama GPU env manifest missing compare bridge clamps: {missing_limits}")
    if len(set(bridge_limits.values())) != 1:
        fail(f"llama GPU compare bridge clamps must use one byte value, got {bridge_limits}")
    if "compare_mode_env_profiles" not in compare_script or "apply_manifest_mode_env" not in compare_script:
        fail("compare script must load bridge clamps from llama GPU env manifest")

    forbidden_main_path = (
        "stage_test_cli",
        "pdocker-runtime/docker-bin",
        "DOCKER_HOST",
        "DOCKER_CONFIG",
        "docker run",
        "docker logs",
        "docker ps",
        "docker rm",
    )
    for token in forbidden_main_path:
        if token in compare_script:
            fail(f"compare script main path must not require Docker CLI token {token!r}")
    engine_api_checks = {
        "creates containers through Engine API": "/containers/create" in compare_script
        and (
            "engine_body POST" in compare_script
            or "engine_request_with_host_timeout" in compare_script
        ),
        "starts containers through Engine API": "/start" in compare_script
        and ("engine_request POST" in compare_script or "engine_request_with_host_timeout" in compare_script),
        "removes containers through Engine API": "DELETE" in compare_script and "/containers/" in compare_script,
        "reads logs through Engine API": "/logs?stdout=1&stderr=1" in compare_script and "decode_engine_logs" in compare_script,
        "uses pdockerd Unix socket directly": "toybox nc -U" in compare_script
        and "pdocker/pdockerd.sock" in compare_script,
    }
    for name, passed in engine_api_checks.items():
        if not passed:
            fail(f"compare script {name}")


def main() -> int:
    compare_script = read(ROOT / "scripts" / "android-llama-gpu-compare.sh")
    host_bench_script = read(ROOT / "scripts" / "android-gpu-host-bench.sh")
    env_manifest = json.loads(read(LLAMA_GPU_ENV_MANIFEST))
    compare_doc = read(ROOT / "docs" / "test" / "LLAMA_BENCHMARKS.md")
    compare_todo = read(ROOT / "docs" / "plan" / "TODO.md")
    compare_result = json.loads(read(ROOT / "docs" / "test" / "llama-gpu-compare-latest.json"))
    compare_expectations = {
        "compare script schema": "pdocker.llama.gpu.compare.v1" in compare_script,
        "compare script leaves llama.cpp unmodified": '"llama_cpp_modified": False' in compare_script,
        "compare script records 10x target": '"target_speedup": 10.0' in compare_script and "target_tps = cpu_tps * 10.0" in compare_script,
        "compare script gates Vulkan allocation trace": "--trace-alloc" in compare_script and "PDOCKER_VULKAN_ICD_TRACE_ALLOC" in json.dumps(env_manifest.get("compare_mode_env_profiles", {})) and "allocation_trace_bytes" in compare_script,
        "compare script rejects one-token invalid timing": "--predict must be an integer >= 2" in compare_script,
        "compare script uses standard Vulkan entry": "standard Vulkan loader through the Skydnir Vulkan ICD" in compare_script,
        "compare script classifies dispatch blocker": "queue_submit_blocker" in compare_script and "vk::Queue::submit: ErrorFeatureNotPresent" in compare_script,
        "compare script classifies generic spirv blocker": "generic_spirv_dispatch_attempted" in compare_script and "vulkan_generic_spirv_dispatch" in compare_script and "executor_submit_generic_dispatch_error" in compare_script,
        "compare script separates gpu failure axes": '"failure_axes": failure_axes' in compare_script and '"advertised_limits": advertised_limits' in compare_script and '"chunking_pressure": chunking_pressure' in compare_script and '"generic_spirv_dispatch": generic_spirv_dispatch' in compare_script,
        "compare script parses advertised limit traces": "parse_android_feature_trace" in compare_script and "parse_spirv_traces" in compare_script and "icd_advertises_subgroup_arithmetic_by_default" in compare_script,
        "compare script records chunking pressure": "model_cpu_mapped_exceeds_bridge_clamp" in compare_script and "allocation_near_clamp" in compare_script and "descriptor_range_max_bytes" in compare_script,
        "compare script classifies range assert": "buffer_range_assert_blocker" in compare_script and "ggml_backend_buffer_get_alloc_size" in compare_script,
        "compare script reports operation to ui": "operation_notify" in compare_script and "POST /system/operations" in compare_script and "llama-gpu-compare" in compare_script,
        "compare script labels direct llama containers for ui inventory": "io.github.ryo100794.pdocker.project-id" in compare_script and "io.github.ryo100794.pdocker.compose-service" in compare_script and "io.github.ryo100794.pdocker.service-url.18081" in compare_script,
        "compare script operation summary includes gpu status": "GPU {d['gpu']['tokens_per_second']:.3f} tok/s" in compare_script and "target_met={str(d['comparison']['target_met']).lower()}" in compare_script and "gpu_layers={d['settings']['gpu_layers']}" in compare_script,
        "compare script supports gpu-only tuning loop": "--gpu-only" in compare_script and "reused_cpu_baseline" in compare_script and "cpu_reused" in compare_script,
        "compare script records operation cleanup behavior": '"operation": {' in compare_script and "mark failed operation on nonzero exit" in compare_script and "--restore" in compare_script and "next run recreates its required container" in compare_script,
        "compare script makes CPU restore opt-in": "RESTORE_CPU=0" in compare_script and "restore CPU server" in compare_script and "CPU server restored" in compare_script,
        "compare script avoids test Docker CLI": "/containers/create" in compare_script and "pdocker-runtime/docker-bin" not in compare_script and "docker run" not in compare_script,
        "compare docs record latest report": "llama-gpu-compare-latest.json" in compare_doc,
        "compare docs record latest tps and blocker": "CPU baseline: 0.1559 generated tokens/s" in compare_doc and "GPU 0.1230 generated tokens/s" in compare_doc and "target_met=false" in compare_doc and "upload/copy" in compare_doc and "GPU below CPU" in compare_doc,
        "compare docs record operation ui visibility": "daemon operation/progress card" in compare_doc and "only object expected in `docker ps`" in compare_doc and "Operation cleanup" in compare_doc,
        "host native gpu baseline script is recorded": "pdocker.gpu.host_native.v1" in host_bench_script and "--bench-vulkan-matmul256-resident" in host_bench_script and "gpu-host-native-latest.json" in compare_doc,
        "compare todo records 10x task list": "llama.cpp Container GPU 10x Task List" in compare_todo,
        "compare todo records ui expectations": "UI-visible reporting of\n  speedup, `target_met`, GPU layer count, current blocker" in compare_todo and "Long-running compare/build cards are pdockerd operations" in compare_todo,
        "compare todo records operation cleanup": "marks\n    failed operations on nonzero exit" in compare_todo and "CPU restore is opt-in" in compare_todo,
        "compare todo preserves no llama patch policy": "llama.cpp source must remain unmodified" in compare_todo,
    }
    for name, passed in compare_expectations.items():
        if not passed:
            fail(name)
    compare_result_expectations = {
        "latest compare records cpu tps": isinstance(compare_result.get("cpu", {}).get("tokens_per_second"), (int, float)),
        "latest compare records gpu tps": isinstance(compare_result.get("gpu", {}).get("tokens_per_second"), (int, float)),
        "latest compare records speedup and target": isinstance(compare_result.get("comparison", {}).get("speedup"), (int, float)) and compare_result.get("comparison", {}).get("target_met") is not None,
        "latest compare records gpu layer count": isinstance(compare_result.get("settings", {}).get("gpu_layers"), int),
        "latest compare records current blocker": bool(compare_result.get("next_blocker")),
        "latest compare records blocker classification": compare_result.get("gpu", {}).get("diagnostics", {}).get("blocker_class") in {"vulkan_device_discovery", "vulkan_buffer_allocation", "vulkan_buffer_range_accounting", "vulkan_generic_spirv_dispatch", "vulkan_queue_submit_feature", "vulkan_pipeline_feature", "bridge_dispatch_performance", "insufficient_gpu_offload_depth"},
        "latest compare records offload depth evidence": compare_result.get("gpu", {}).get("diagnostics", {}).get("blocker_class") == "vulkan_device_discovery" or all(
            key in compare_result.get("gpu", {}).get("evidence", {})
            for key in ("gpu_repeating_layers", "gpu_offloaded_layers", "gpu_total_layers", "gpu_output_only_offload")
        ),
        "latest compare records failure axes": all(
            axis in compare_result.get("gpu", {}).get("diagnostics", {}).get("failure_axes", {})
            for axis in ("advertised_limits", "chunking", "generic_spirv_dispatch")
        ),
        "latest compare records chunking pressure": "configured_bridge_max_buffer_bytes" in compare_result.get("gpu", {}).get("diagnostics", {}).get("chunking_pressure", {}),
        "latest compare records advertised limits": "configured_clamps" in compare_result.get("gpu", {}).get("diagnostics", {}).get("advertised_limits", {}),
        "latest compare records operation ui surface": compare_result.get("operation", {}).get("kind") == "llama-gpu-compare" and "operation/progress card" in compare_result.get("operation", {}).get("ui_surface", ""),
        "latest compare records operation cleanup": "CPU restore is opt-in" in compare_result.get("operation", {}).get("cleanup", ""),
    }
    for name, passed in compare_result_expectations.items():
        if not passed:
            fail(name)
    check_llama_gpu_compare_contract(compare_script)
    ok("llama gpu 10x comparison scenario is recorded")

    data = json.loads(read(LIBRARY))
    ids = [item["id"] for item in data.get("templates", [])]
    duplicate_ids = sorted({tid for tid in ids if ids.count(tid) > 1})
    if duplicate_ids:
        fail(f"duplicate template ids in library.json: {', '.join(duplicate_ids)}")
    templates = {item["id"]: item for item in data.get("templates", [])}
    for tid in (
        "dev-workspace",
        "direct-runtime-probe",
        "skydnir-test-suite",
        "llama-cpp-gpu",
        "ros2-humble-rviz-novnc",
        "blender-xvnc-novnc",
    ):
        if tid not in templates:
            fail(f"template {tid} absent from library.json")
    ok("required templates listed")

    for tid, template in templates.items():
        compose_name = template.get("compose")
        if not compose_name:
            continue
        template_root = ASSETS / template["assetPath"]
        compose = read(template_root / compose_name)
        readme = read(template_root / "README.md")
        documents_readme = read(template_root / "documents" / "README.md")
        # The llama GPU benchmark Compose file is pinned by the GPU ABI guard so
        # benchmark workload changes cannot slip into naming-only commits.
        # Other templates should expose Skydnir env names directly.
        allowed_documents_volume = (
            LEGACY_DOCUMENTS_VOLUME if tid == "llama-cpp-gpu" else SKYDNIR_DOCUMENTS_VOLUME
        )
        allowed_shared_documents_volume = (
            LEGACY_SHARED_DOCUMENTS_VOLUME if tid == "llama-cpp-gpu" else SKYDNIR_SHARED_DOCUMENTS_VOLUME
        )
        if allowed_documents_volume not in compose:
            fail(f"{tid} compose missing selected Documents folder mount")
        if allowed_shared_documents_volume not in compose:
            fail(f"{tid} compose missing cross-project shared Documents volume")
        export_env = "PDOCKER_EXPORT_DIR" if tid == "llama-cpp-gpu" else "SKYDNIR_EXPORT_DIR"
        fast_workdir_env = "PDOCKER_FAST_WORKDIR" if tid == "llama-cpp-gpu" else "SKYDNIR_FAST_WORKDIR"
        if export_env not in compose or fast_workdir_env not in compose:
            fail(f"{tid} compose missing Documents export / fast workspace guidance env")
        shared_mount_env = "PDOCKER_SHARED_DOCUMENTS_MOUNT" if tid == "llama-cpp-gpu" else "SKYDNIR_SHARED_DOCUMENTS_MOUNT"
        if shared_mount_env not in compose:
            fail(f"{tid} compose missing shared Documents mount env")
        if not ("SKYDNIR_DOCUMENTS_HOST" in readme and "SKYDNIR_DOCUMENTS_MOUNT" in readme):
            fail(f"{tid} README missing shared Documents override docs")
        if (
            "SKYDNIR_SHARED_DOCUMENTS_HOST" not in readme
            and "SKYDNIR_SHARED_DOCUMENTS_HOST" not in documents_readme
        ):
            fail(f"{tid} docs missing cross-project shared Documents override")
        if "/documents" not in documents_readme or "Do not put hot build caches" not in documents_readme:
            fail(f"{tid} documents README missing slow-storage usage guidance")
        if "pdocker/projects" not in documents_readme or "selected Android Documents folder" not in documents_readme:
            fail(f"{tid} documents README missing Documents workspace-root layout")
    ok("all compose templates include shared Documents volume")

    direct = templates["direct-runtime-probe"]
    direct_root = ASSETS / direct["assetPath"]
    direct_compose = read(direct_root / direct["compose"])
    direct_start = read(direct_root / "scripts" / "start-direct-runtime-probe.sh")
    direct_readme = read(direct_root / "README.md")
    direct_documents_readme = read(direct_root / "documents" / "README.md")
    direct_expectations = {
        "direct probe compose uses Skydnir public image and container names": "image: skydnir/direct-runtime-probe:latest" in direct_compose
        and "container_name: skydnir-direct-runtime-probe" in direct_compose
        and "image: pdocker/direct-runtime-probe:latest" not in direct_compose
        and "container_name: pdocker-direct-runtime-probe" not in direct_compose,
        "direct probe exports to Skydnir public Documents path": "/documents/skydnir-exports" in direct_compose
        and 'export_dir="${SKYDNIR_EXPORT_DIR:-${PDOCKER_EXPORT_DIR:-/documents/skydnir-exports}}/direct-runtime-probe"' in direct_start
        and "/documents/skydnir-exports/direct-runtime-probe/latest.log" in direct_readme
        and "/documents/skydnir-exports/direct-runtime-probe/latest.json" in direct_documents_readme,
        "direct probe public wording uses Skydnir": "Skydnir direct runtime probe" in direct_start
        and "for the Skydnir direct-runtime" in direct_readme
        and "Use from Skydnir" in direct_readme
        and "pdocker direct runtime probe container" not in direct_start
        and "pdocker direct-runtime" not in direct_readme,
    }
    for name, passed in direct_expectations.items():
        if not passed:
            fail(name)
    ok("direct-runtime-probe template uses Skydnir public naming and export path")

    suite = templates["skydnir-test-suite"]
    suite_root = ASSETS / suite["assetPath"]
    suite_compose = read(suite_root / suite["compose"])
    suite_dockerfile = read(suite_root / suite["dockerfile"])
    suite_runner = read(suite_root / "scripts" / "run-skydnir-test-suite.sh")
    suite_start = read(suite_root / "scripts" / "start-skydnir-test-suite.sh")
    suite_probe = read(suite_root / "scripts" / "pdocker-container-probe.sh")
    suite_readme = read(suite_root / "README.md")
    suite_expectations = {
        "test suite metadata": suite.get("category") == "runtime-test"
        and suite.get("gpu") == "none"
        and "test-suite" in suite.get("features", [])
        and "docker-exec" in suite.get("features", []),
        "test suite compose idle command": 'command: ["/usr/local/bin/start-skydnir-test-suite"]' in suite_compose
        and "container_name: skydnir-test-suite" in suite_compose
        and "image: skydnir/test-suite:latest" in suite_compose
        and "container_name: pdocker-test-suite" not in suite_compose
        and "image: pdocker/test-suite:latest" not in suite_compose,
        "test suite mounts reports and Documents": "./reports:/reports" in suite_compose
        and SKYDNIR_DOCUMENTS_VOLUME in suite_compose
        and SKYDNIR_SHARED_DOCUMENTS_VOLUME in suite_compose,
        "test suite Dockerfile installs runner and probe": "COPY scripts/run-skydnir-test-suite.sh" in suite_dockerfile
        and "COPY scripts/start-skydnir-test-suite.sh" in suite_dockerfile
        and "COPY scripts/pdocker-container-probe.sh" in suite_dockerfile
        and "HEALTHCHECK" in suite_dockerfile
        and "run-skydnir-test-suite" in suite_dockerfile
        and "start-skydnir-test-suite" in suite_dockerfile
        and "run-pdocker-test-suite" in suite_dockerfile
        and "start-pdocker-test-suite" in suite_dockerfile,
        "test suite start instructs exec route": "docker exec skydnir-test-suite run-skydnir-test-suite" in suite_start,
        "test suite runner supports scenario selectors": "--scenario all|smoke|direct|io|archive|documents" in suite_runner
        and "run_selected_case" in suite_runner,
        "test suite runner mirrors Documents evidence": "/documents/skydnir-exports" in suite_runner
        and "export_latest_json" in suite_runner
        and '"schema": "pdocker.test-suite.v1"' in suite_runner,
        "test suite runner includes direct/runtime scenarios": "direct_runtime_probe" in suite_runner
        and "path_semantics" in suite_runner
        and "argv_preservation" in suite_runner
        and "proc_exe" in suite_runner,
        "test suite runner includes io/archive/input scenarios": "file_io_smoke" in suite_runner
        and "archive_roundtrip" in suite_runner
        and "invalid_inputs" in suite_runner,
        "test suite embeds existing direct probe payload": "test_argv_preservation" in suite_probe
        and "flash_attn_mask_opt.comp.cpp.o" in suite_probe
        and "test_large_allocation_guard" in suite_probe,
        "test suite docs require exec and Documents reports": "docker exec skydnir-test-suite run-skydnir-test-suite --scenario all" in suite_readme
        and "/documents/skydnir-exports/skydnir-test-suite/latest.json" in suite_readme,
    }
    for name, passed in suite_expectations.items():
        if not passed:
            fail(name)
    ok("skydnir-test-suite template centralizes exec-run scenarios and Documents evidence")

    dev = templates["dev-workspace"]
    if dev.get("assetPath") != "default-project":
        fail("dev-workspace must use the bundled default-project asset template")
    if dev.get("projectDir") != "default":
        fail("dev-workspace must install as the default project")
    dev_metadata = " ".join(
        [
            dev.get("name", ""),
            dev.get("category", ""),
            dev.get("description", ""),
            " ".join(dev.get("features", [])),
        ]
    ).lower()
    for token in (
        "skydnir-management",
        "skydnir management",
        "project-creation",
        "project creation",
        "project-maintenance",
        "project maintenance",
        "build-compose",
        "compose",
        "engine-socket-helpers",
        "engine socket helpers",
        "code-server",
        "codex",
    ):
        if token not in dev_metadata:
            fail(f"dev-workspace library metadata must mention {token}")
    dev_root = ASSETS / dev["assetPath"]
    dev_compose = read(dev_root / dev["compose"])
    dev_dockerfile = read(dev_root / dev["dockerfile"])
    dev_workspace_extensions = read(dev_root / "workspace" / ".vscode" / "extensions.json")
    dev_tasks = read(dev_root / "workspace" / ".vscode" / "tasks.json")
    dev_readme = read(dev_root / "README.md")
    dev_helper_scripts = "\n".join(
        f"# {name}\n{read(dev_root / 'scripts' / name)}"
        for name in (
            "pdocker-engine-env",
            "pdocker-docker",
            "pdocker-compose",
            "pdocker-paths",
            "pdocker-projects",
            "pdocker-new-project",
        )
    )
    for token in (
        "code-server",
        "Continue.continue",
        "@openai/codex",
        "OpenAI.chatgpt",
        "@anthropic-ai/claude-code",
        "Anthropic.claude-code",
        "ANTHROPIC_API_KEY",
        "gpus: all",
        "image: skydnir/dev-workspace:latest",
        "container_name: skydnir-dev",
        "18080:18080",
        "# skydnir.service-url: 18080=VS Code",
        "# skydnir.auto-open: VS Code",
    ):
        if token not in dev_compose + dev_dockerfile + dev_workspace_extensions:
            fail(f"dev-workspace missing {token}")
    ok("dev-workspace includes code-server, Continue, Codex, Claude Code, and GPU request")

    dev_management_contract = dev_compose + dev_dockerfile + dev_tasks + dev_readme + dev_helper_scripts
    for token in (
        "docker-ce-cli",
        "docker-compose-plugin",
        "COPY scripts/pdocker-* /usr/local/bin/",
        "skydnir-paths",
        "skydnir-projects",
        "skydnir-new-project",
        "skydnir-docker",
        "skydnir-compose",
        "/pdocker/project",
        "/pdocker/projects",
        "/pdocker/host",
        "/pdocker/host/pdockerd.sock",
        "pdocker-paths",
        "pdocker-projects",
        "pdocker-new-project",
        "pdocker-docker",
        "pdocker-compose",
        "skydnir-engine-env",
        "Skydnir: show paths",
        "Skydnir: compose up current project",
        "DOCKER_HOST",
        "SKYDNIR_DOCUMENTS_MOUNT",
        "Documents/pdocker/projects",
        "filesDir/pdocker/pdockerd.sock",
    ):
        if token not in dev_management_contract:
            fail(f"dev-workspace management helper contract missing {token}")
    if SKYDNIR_DOCUMENTS_VOLUME not in dev_helper_scripts:
        fail("pdocker-new-project blank template must include selected Documents folder mount")
    if SKYDNIR_SHARED_DOCUMENTS_VOLUME not in dev_helper_scripts:
        fail("pdocker-new-project blank template must include cross-project shared Documents volume")
    if 'show_path "documents"' not in dev_helper_scripts:
        fail("pdocker-paths must show the shared Documents mount")
    if "engine_env_helper=\"$(command -v skydnir-engine-env || command -v pdocker-engine-env)\"" not in dev_helper_scripts:
        fail("dev-workspace Docker helpers must source guarded Engine environment")
    if "No mounted Skydnir/Docker Engine socket found." not in dev_helper_scripts:
        fail("dev-workspace Docker helpers must report missing mounted Engine socket")
    if '"${SKYDNIR_ENGINE_SOCKET:-}"' not in dev_helper_scripts:
        fail("pdocker-engine-env must prefer SKYDNIR_ENGINE_SOCKET before legacy PDOCKER sockets")
    if '"${SKYDNIR_ENGINE_SOCKET:-}"\n    "${PDOCKER_ENGINE_SOCKET:-}"' not in dev_helper_scripts:
        fail("pdocker-engine-env must keep legacy PDOCKER_ENGINE_SOCKET fallback after SKYDNIR_ENGINE_SOCKET")
    if "The APK does not\n  bundle an upstream Docker CLI binary" not in dev_readme:
        fail("dev-workspace README must keep Docker CLI scoped to development container")
    for readme_token in (
        "project library",
        "library container template",
        "release assets",
        "available in release builds",
        "Skydnir management",
        "project creation",
        "project maintenance",
        "Dockerfile builds",
        "Compose\nruns",
        "Engine socket helpers",
        "code-server",
        "OpenAI Codex/ChatGPT",
    ):
        if readme_token not in dev_readme:
            fail(f"dev-workspace README must state {readme_token}")
    for secret_token in (
        "Authentication is required per user",
        "do not bake API tokens",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GITHUB_TOKEN",
        "CODE_SERVER_PASSWORD",
    ):
        if secret_token not in dev_readme:
            fail(f"dev-workspace README must document per-user auth token {secret_token}")
    ok("dev-workspace exposes Skydnir management helpers, paths, guarded Engine wrappers, and tasks")

    llama = templates["llama-cpp-gpu"]
    llama_root = ASSETS / llama["assetPath"]
    llama_compose = read(llama_root / llama["compose"])
    llama_dockerfile = read(llama_root / llama["dockerfile"])
    profile = read(llama_root / "scripts" / "pdocker-gpu-profile.sh")
    start = read(llama_root / "scripts" / "start-llama-server.sh")

    expectations = {
        "compose uses Skydnir public image and container names": "image: skydnir/llama-cpp-gpu:latest" in llama_compose
        and "container_name: skydnir-llama-cpp" in llama_compose
        and "image: pdocker/llama-cpp-gpu:latest" not in llama_compose
        and "container_name: pdocker-llama-cpp" not in llama_compose
        and "/documents/skydnir-exports" in llama_compose,
        "compose gpus all": "gpus: all" in llama_compose,
        "compose exposes build parallelism": "LLAMA_CPP_BUILD_JOBS" in llama_compose,
        "compose model volume": "${PDOCKER_MODEL_HOST:-./models}:/models" in llama_compose,
        "compose model url syntax": re.search(r'\$\{LLAMA_MODEL_URL:-[^}]+\}', llama_compose) is not None,
        "Dockerfile modern Vulkan headers": "FROM ubuntu:24.04" in llama_dockerfile,
        "Dockerfile llama.cpp source": "ggml-org/llama.cpp" in llama_dockerfile,
        "Dockerfile keeps llama engine local": "-DGGML_RPC=ON" not in llama_dockerfile and "LLAMA_ARG_RPC" not in llama_compose + start,
        "Dockerfile Vulkan build": "-DGGML_VULKAN=ON" in llama_dockerfile,
        "Dockerfile Vulkan shader compiler": "glslc" in llama_dockerfile,
        "Dockerfile SPIR-V headers": "spirv-headers" in llama_dockerfile and "spirv-tools" in llama_dockerfile,
        "Dockerfile OpenBLAS build": "-DGGML_BLAS=ON" in llama_dockerfile,
        "Dockerfile keeps upstream-style OpenBLAS detection": "-DGGML_BLAS_VENDOR=OpenBLAS" in llama_dockerfile and "-DBLAS_LIBRARIES=" not in llama_dockerfile and "pkg-config --variable=libdir openblas" not in llama_dockerfile,
        "Dockerfile server-only build target": "--target llama-server" in llama_dockerfile and "--parallel" in llama_dockerfile,
        "Dockerfile bounded build jobs": "ARG LLAMA_CPP_BUILD_JOBS=1" in llama_dockerfile and 'jobs="${LLAMA_CPP_BUILD_JOBS:-1}"' in llama_dockerfile,
        "Dockerfile pinned llama ref and standard Release build type": "ARG LLAMA_CPP_REF=b9030" in llama_dockerfile and "ARG LLAMA_CPP_BUILD_TYPE=Release" in llama_dockerfile and "CMAKE_CXX_FLAGS_MINSIZEREL" not in llama_dockerfile and ".pdocker-llama-cpp-commit" in llama_dockerfile,
        "Dockerfile log directory": "/workspace/logs" in llama_dockerfile and "/var/log/pdocker" in llama_dockerfile,
        "Dockerfile llama healthcheck": "HEALTHCHECK" in llama_dockerfile and "/health" in llama_dockerfile and "/v1/models" in llama_dockerfile,
        "profile Vulkan detection": "PDOCKER_VULKAN_PASSTHROUGH" in profile,
        "profile CUDA compat detection": "PDOCKER_CUDA_COMPAT" in profile,
        "profile gates unfinished Skydnir Vulkan before CUDA compat": profile.find('pdocker_vulkan_icd_signal" = "true"') < profile.find('mode" = "cuda"'),
        "profile CPU fallback": re.search(r'backend="cpu"', profile) is not None,
        "profile diagnostics json": "-diagnostics.json" in profile and '"signals"' in profile and "json_escape" in profile,
        "profile quotes extra args for source": "shell_quote" in profile and 'LLAMA_EXTRA_ARGS=$(shell_quote "$extra")' in profile,
        "start sources profile": "source \"$profile\"" in start,
        "start shows diagnostics": "LLAMA_GPU_DIAGNOSTICS" in start and "llama.cpp gpu diagnostics" in start,
        "start hides gpu env during cpu fallback": 'LLAMA_GPU_BACKEND:-cpu' in start and 'export GGML_VK_VISIBLE_DEVICES=""' in start and "unset VK_ICD_FILENAMES" in start and "unset OCL_ICD_VENDORS" in start,
        "start passes gpu layers": "--n-gpu-layers" in start,
        "llama default gpu layers use validated bridge path": 'LLAMA_ARG_N_GPU_LAYERS: "${LLAMA_ARG_N_GPU_LAYERS:-1}"' in llama_compose and "LLAMA_ARG_N_GPU_LAYERS:-2" not in llama_compose,
        "start guards unfinished Skydnir Vulkan KV offload": "PDOCKER_VULKAN_ALLOW_KV_OFFLOAD" in llama_compose and "PDOCKER_VULKAN_ALLOW_KV_OFFLOAD" in start and "PDOCKER_VULKAN_ICD_READY" in start and "LLAMA_ARG_KV_OFFLOAD=0" in start and "--no-kv-offload" in start,
        "llama default port offset": "18081:18081" in llama_compose and "18081" in start,
        "llama service shortcut comment": "# skydnir.service-url: 18081=llama.cpp" in llama_compose,
        "llama default 8b model": "Qwen/Qwen3-8B-GGUF" in llama_compose and "Qwen3-8B-Q4_K_M.gguf" in llama_compose,
        "llama optional model download": "LLAMA_MODEL_URL" in llama_compose and "curl -fL" in start and "-C -" in start,
        "llama default chat template": "--jinja" in start,
        "llama docker logs stream": "LLAMA_LOG_FILE" in llama_compose and "tee -a \"$log_file\"" in start and "stdbuf -oL -eL" in start,
        "llama startup tee captures profile generation": start.find("exec > >(tee -a \"$log_file\") 2>&1") < start.find("pdocker-gpu-profile") and "Skydnir llama startup: refreshing GPU profile" in start and ">/dev/null" not in start[start.find("pdocker-gpu-profile") - 80:start.find("pdocker-gpu-profile") + 120],
        "llama startup json records resolved GPU contract": "LLAMA_STARTUP_JSON" in start and "profile_path" in start and "profile_refresh_rc" in start and "llama_server_argv" in start and "MemAvailable" in start and "SwapFree" in start and "PDOCKER_GPU_QUEUE_SOCKET" in start and "VK_ICD_FILENAMES" in start,
        "llama startup json records KV guard state": "kv_offload_guard" in start and "kv_offload_guard_active" in start and "added_arg" in start and "disabled_effective" in start,
        "llama missing-model status page": "http.server" in start and "waiting for a GGUF model" in start,
    }
    for name, passed in expectations.items():
        if not passed:
            fail(name)
    ok("llama-cpp-gpu template has compose, Dockerfile, GPU profile, and server entrypoint")

    ros = templates["ros2-humble-rviz-novnc"]
    ros_root = ASSETS / ros["assetPath"]
    ros_compose = read(ros_root / ros["compose"])
    ros_dockerfile = read(ros_root / ros["dockerfile"])
    ros_start = read(ros_root / "scripts" / "start-ros2-rviz-novnc.sh")
    ros_readme = read(ros_root / "README.md")
    ros_combined = ros_compose + ros_dockerfile + ros_start + ros_readme

    ros_expectations = {
        "ros template metadata": ros.get("category") == "robotics"
        and ros.get("gpu") == "none"
        and "rviz" in ros.get("features", [])
        and "novnc" in ros.get("features", []),
        "ros compose service shortcut comment": "# skydnir.service-url: 18082=noVNC RViz" in ros_compose,
        "ros compose avoids existing browser ports": "18080:" not in ros_compose and "18081:" not in ros_compose,
        "ros compose noVNC port": "18082:6080" in ros_compose,
        "ros compose VNC port": "15900:5900" in ros_compose,
        "ros compose workspace volume": "${PDOCKER_FAST_WORKSPACE_HOST:-./workspace}:/workspace" in ros_compose,
        "ros compose explicit GL backend defaults": 'PDOCKER_GL_BACKEND: "${PDOCKER_GL_BACKEND:-llvmpipe}"' in ros_compose
        and 'LIBGL_ALWAYS_SOFTWARE: "${LIBGL_ALWAYS_SOFTWARE:-1}"' in ros_compose
        and 'GALLIUM_DRIVER: "${GALLIUM_DRIVER:-llvmpipe}"' in ros_compose
        and "MESA_LOADER_DRIVER_OVERRIDE" in ros_compose,
        "ros Dockerfile Ubuntu 22.04": "FROM ubuntu:22.04" in ros_dockerfile,
        "ros Dockerfile apt-based ROS repository": "packages.ros.org/ros2/ubuntu" in ros_dockerfile
        and "ros-archive-keyring.gpg" in ros_dockerfile,
        "ros Dockerfile Humble desktop/RViz": "ros-humble-desktop" in ros_dockerfile
        and "ros-humble-rviz2" in ros_dockerfile,
        "ros Dockerfile Xvnc/noVNC packages": "tigervnc-standalone-server" in ros_dockerfile
        and "novnc" in ros_dockerfile
        and "websockify" in ros_dockerfile,
        "ros Dockerfile extracts noVNC static assets without postinst": "apt-get download novnc" in ros_dockerfile
        and "dpkg-deb -x novnc_*.deb /" in ros_dockerfile
        and " novnc \\" not in ros_dockerfile,
        "ros Dockerfile software rendering env": "PDOCKER_GL_BACKEND=llvmpipe" in ros_dockerfile
        and "LIBGL_ALWAYS_SOFTWARE=1" in ros_dockerfile
        and "GALLIUM_DRIVER=llvmpipe" in ros_dockerfile
        and "QT_X11_NO_MITSHM=1" in ros_dockerfile,
        "ros Dockerfile exposed ports": "EXPOSE 6080 5900" in ros_dockerfile,
        "ros Dockerfile healthcheck": "HEALTHCHECK" in ros_dockerfile
        and "http://127.0.0.1:6080/vnc.html" in ros_dockerfile
        and "pgrep -x Xvnc" in ros_dockerfile
        and "pgrep -x rviz2" in ros_dockerfile,
        "ros start sources ROS setup": "/opt/ros/${ROS_DISTRO:-humble}/setup.bash" in ros_start,
        "ros start launches Xvnc": "Xvnc \"$display\"" in ros_start
        and "-SecurityTypes None" in ros_start,
        "ros start launches XFCE": "startxfce4" in ros_start,
        "ros start launches RViz": "rviz2 \"${rviz_args[@]}\"" in ros_start
        and "RVIZ_CONFIG" in ros_start,
        "ros start launches noVNC via websockify": "websockify --web" in ros_start
        and "0.0.0.0:${novnc_port}" in ros_start
        and "127.0.0.1:${vnc_port}" in ros_start
        and 'if [ ! -f "${novnc_web}/vnc.html" ]; then' in ros_start
        and 'novnc_web="/usr/share/novnc/www"' in ros_start,
        "ros start logs explicit GL backend": 'PDOCKER_GL_BACKEND="${PDOCKER_GL_BACKEND:-llvmpipe}"' in ros_start
        and "Skydnir GL backend: PDOCKER_GL_BACKEND=llvmpipe (Mesa llvmpipe software rendering)" in ros_start
        and "Skydnir GL backend: PDOCKER_GL_BACKEND=zink-experimental (future Mesa Zink path; acceleration is not validated by this template)" in ros_start
        and "Unsupported PDOCKER_GL_BACKEND" in ros_start,
        "ros compose uses Skydnir public image and container names": "image: skydnir/ros2-humble-rviz-novnc:latest" in ros_compose
        and "container_name: skydnir-ros2-rviz" in ros_compose
        and "image: pdocker/ros2-humble-rviz-novnc:latest" not in ros_compose
        and "container_name: pdocker-ros2-rviz" not in ros_compose
        and "/documents/skydnir-exports" in ros_compose,
        "ros start records OpenGL diagnostics": "glxinfo -B" in ros_start
        and "OpenGL diagnostics" in ros_start
        and "tee -a \"$glxinfo_log\"" in ros_start,
        "ros logs stream to stdout": "tee -a \"$vnc_log\"" in ros_start
        and "tee -a \"$xfce_log\"" in ros_start
        and "tee -a \"$rviz_log\"" in ros_start
        and "tee -a \"$glxinfo_log\"" in ros_start
        and "tee -a \"$novnc_log\"" in ros_start,
        "ros docs identify access ports": "18082" in ros_readme and "15900" in ros_readme,
        "ros docs document noVNC package extraction": "apt repository" in ros_readme
        and "extracts its static files without running" in ros_readme,
        "ros docs document explicit GL backend": "PDOCKER_GL_BACKEND=llvmpipe" in ros_readme
        and "PDOCKER_GL_BACKEND=zink-experimental" in ros_readme
        and "does not claim Vulkan/Zink acceleration works" in ros_readme,
        "ros template avoids llama bridge tokens": "llama" not in ros_combined.lower()
        and "PDOCKER_VULKAN" not in ros_combined
        and "CUDA" not in ros_combined,
    }
    for name, passed in ros_expectations.items():
        if not passed:
            fail(name)
    ok("ros2-humble-rviz-novnc template has Compose, Dockerfile, RViz, Xvnc/noVNC, logs, and healthcheck")

    blender = templates["blender-xvnc-novnc"]
    blender_root = ASSETS / blender["assetPath"]
    blender_compose = read(blender_root / blender["compose"])
    blender_dockerfile = read(blender_root / blender["dockerfile"])
    blender_start = read(blender_root / "scripts" / "start-blender-xvnc-novnc.sh")
    blender_readme = read(blender_root / "README.md")
    blender_workspace_readme = read(blender_root / "workspace" / "README.md")
    blender_combined = (
        blender_compose
        + blender_dockerfile
        + blender_start
        + blender_readme
        + blender_workspace_readme
    )

    blender_metadata = " ".join(
        [
            blender.get("name", ""),
            blender.get("category", ""),
            blender.get("description", ""),
            " ".join(blender.get("features", [])),
        ]
    ).lower()
    blender_metadata_expectations = {
        token: token in blender_metadata
        for token in (
            "blender",
            "opengl",
            "glsl",
            "xvnc",
            "novnc",
            "llvmpipe",
            "zink",
            "vulkan",
        )
    }
    for token, passed in blender_metadata_expectations.items():
        if not passed:
            fail(f"blender template metadata must mention {token}")

    blender_expectations = {
        "blender template metadata": blender.get("category") == "graphics"
        and blender.get("gpu") == "future-vulkan-zink",
        "blender compose service shortcut comment": "# skydnir.service-url: 18083=noVNC Blender" in blender_compose,
        "blender compose avoids existing browser ports": "18080:" not in blender_compose
        and "18081:" not in blender_compose
        and "18082:" not in blender_compose,
        "blender compose noVNC port": "18083:6080" in blender_compose,
        "blender compose VNC port": "15901:5901" in blender_compose,
        "blender compose workspace volume": "${PDOCKER_FAST_WORKSPACE_HOST:-./workspace}:/workspace" in blender_compose,
        "blender compose software defaults": 'LIBGL_ALWAYS_SOFTWARE: "${LIBGL_ALWAYS_SOFTWARE:-1}"' in blender_compose
        and 'GALLIUM_DRIVER: "${GALLIUM_DRIVER:-llvmpipe}"' in blender_compose
        and 'PDOCKER_GL_BACKEND: "${PDOCKER_GL_BACKEND:-llvmpipe}"' in blender_compose
        and 'PDOCKER_GRAPHICS_MODE: "${PDOCKER_GRAPHICS_MODE:-software}"' in blender_compose,
        "blender compose future Vulkan/Zink switches": "PDOCKER_ZINK_EXPERIMENTAL" in blender_compose
        and "PDOCKER_VULKAN_ICD_FILENAMES" in blender_compose
        and "VK_ICD_FILENAMES" in blender_compose
        and "MESA_LOADER_DRIVER_OVERRIDE" in blender_compose,
        "blender Dockerfile Ubuntu 24.04": "FROM ubuntu:24.04" in blender_dockerfile,
        "blender Dockerfile installs Blender": "blender" in blender_dockerfile,
        "blender Dockerfile Xvnc/noVNC packages": "tigervnc-standalone-server" in blender_dockerfile
        and "novnc" in blender_dockerfile
        and "websockify" in blender_dockerfile,
        "blender Dockerfile extracts noVNC static assets without postinst": "apt-get download novnc" in blender_dockerfile
        and "dpkg-deb -x novnc_*.deb /" in blender_dockerfile
        and " novnc \\" not in blender_dockerfile,
        "blender Dockerfile lightweight window manager": "matchbox-window-manager" in blender_dockerfile,
        "blender Dockerfile Mesa diagnostics": "mesa-utils" in blender_dockerfile
        and "mesa-vulkan-drivers" in blender_dockerfile
        and "vulkan-tools" in blender_dockerfile,
        "blender Dockerfile software rendering env": "LIBGL_ALWAYS_SOFTWARE=1" in blender_dockerfile
        and "GALLIUM_DRIVER=llvmpipe" in blender_dockerfile
        and "PDOCKER_GL_BACKEND=llvmpipe" in blender_dockerfile,
        "blender Dockerfile exposed ports": "EXPOSE 6080 5901" in blender_dockerfile,
        "blender Dockerfile healthcheck": "HEALTHCHECK" in blender_dockerfile
        and "http://127.0.0.1:6080/vnc.html" in blender_dockerfile
        and "pgrep -x Xvnc" in blender_dockerfile
        and "pgrep -x blender" in blender_dockerfile,
        "blender start launches Xvnc": "Xvnc \"$display\"" in blender_start
        and "-SecurityTypes None" in blender_start,
        "blender start launches lightweight window manager": "matchbox-window-manager" in blender_start,
        "blender start launches Blender": "blender \"${blender_args[@]}\"" in blender_start
        and "BLENDER_STARTUP_FILE" in blender_start
        and "BLENDER_EXTRA_ARGS" in blender_start,
        "blender start launches noVNC via websockify": "websockify --web" in blender_start
        and "0.0.0.0:${novnc_port}" in blender_start
        and "127.0.0.1:${vnc_port}" in blender_start
        and 'if [ ! -f "${novnc_web}/vnc.html" ]; then' in blender_start
        and 'novnc_web="/usr/share/novnc/www"' in blender_start,
        "blender start records OpenGL diagnostics": "glxinfo -B" in blender_start
        and "OpenGL/GLSL diagnostics" in blender_start,
        "blender start logs explicit GL backend": 'PDOCKER_GL_BACKEND="${PDOCKER_GL_BACKEND:-llvmpipe}"' in blender_start
        and "Skydnir GL backend: PDOCKER_GL_BACKEND=llvmpipe (Mesa llvmpipe software rendering)" in blender_start
        and "Skydnir GL backend: PDOCKER_GL_BACKEND=zink-experimental (future Mesa Zink path; acceleration is not validated by this template)" in blender_start
        and "Unsupported PDOCKER_GL_BACKEND" in blender_start,
        "blender compose uses Skydnir public image and container names": "image: skydnir/blender-xvnc-novnc:latest" in blender_compose
        and "container_name: skydnir-blender-xvnc" in blender_compose
        and "image: pdocker/blender-xvnc-novnc:latest" not in blender_compose
        and "container_name: pdocker-blender-xvnc" not in blender_compose
        and "/documents/skydnir-exports" in blender_compose,
        "blender start exposes future Zink/Vulkan switches without claim": "PDOCKER_ZINK_EXPERIMENTAL=1 exposes Mesa Zink/Vulkan env switches for future validation only" in blender_start
        and "acceleration is not validated by this template" in blender_start,
        "blender logs stream to stdout": "tee -a \"$vnc_log\"" in blender_start
        and "tee -a \"$wm_log\"" in blender_start
        and "tee -a \"$glxinfo_log\"" in blender_start
        and "tee -a \"$blender_log\"" in blender_start
        and "tee -a \"$novnc_log\"" in blender_start,
        "blender docs identify access ports": "18083" in blender_readme and "15901" in blender_readme,
        "blender docs document noVNC package extraction": "apt repository" in blender_readme
        and "extracts its static files without running" in blender_readme,
        "blender docs document software defaults": "LIBGL_ALWAYS_SOFTWARE=1" in blender_readme
        and "GALLIUM_DRIVER=llvmpipe" in blender_readme
        and "PDOCKER_GL_BACKEND=llvmpipe" in blender_readme
        and "Mesa software rendering" in blender_readme,
        "blender docs document future Zink/Vulkan caveat": "Future Zink/Skydnir Vulkan validation" in blender_readme
        and "PDOCKER_GL_BACKEND=zink-experimental" in blender_readme
        and "does not claim Vulkan/Zink acceleration works" in blender_readme,
        "blender workspace notes graphics assets": "GLSL/OpenGL" in blender_workspace_readme,
        "blender template avoids llama bridge tokens": "llama" not in blender_combined.lower()
        and "CUDA" not in blender_combined,
    }
    for name, passed in blender_expectations.items():
        if not passed:
            fail(name)
    ok("blender-xvnc-novnc template has Compose, Dockerfile, Blender, Xvnc/noVNC, logs, healthcheck, llvmpipe defaults, and future Zink/Vulkan switches")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

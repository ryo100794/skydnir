import hashlib
import json
import importlib.util
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_HEADER = ROOT / "app" / "src" / "main" / "cpp" / "pdocker_gpu_abi.h"
CONTAINER_HEADER = ROOT / "docker-proot-setup" / "src" / "gpu" / "pdocker_gpu_abi.h"
GPU_EXECUTOR = ROOT / "app" / "src" / "main" / "cpp" / "pdocker_gpu_executor.c"
VULKAN_ICD = ROOT / "docker-proot-setup" / "src" / "gpu" / "pdocker_vulkan_icd.c"
LLAMA_COMPARE = ROOT / "scripts" / "android-llama-gpu-compare.sh"
PDOCKERD = ROOT / "docker-proot-setup" / "bin" / "pdockerd"
PDOCKERD_SERVICE = ROOT / "app" / "src" / "main" / "kotlin" / "io" / "github" / "ryo100794" / "pdocker" / "PdockerdService.kt"
MAIN_ACTIVITY = ROOT / "app" / "src" / "main" / "kotlin" / "io" / "github" / "ryo100794" / "pdocker" / "MainActivity.kt"
LLAMA_GPU_NEXT_STEPS = ROOT / "docs" / "plan" / "LLAMA_GPU_BRIDGE_NEXT_STEPS.md"
LLAMA_GPU_DEVICE_RUNBOOK = ROOT / "docs" / "test" / "LLAMA_GPU_DEVICE_RUNBOOK_20260513.md"
LLAMA_GPU_CORRECTNESS = ROOT / "docs" / "test" / "LLAMA_GPU_CORRECTNESS_20260507.md"
ROPE_YARN_ARTIFACT = ROOT / "docs" / "test" / "llama-gpu-ngl1-rope-yarn-oracle-20260509.json"
LLAMA_GPU_ARTIFACT_VERIFIER = ROOT / "scripts" / "verify-llama-gpu-artifact.py"
LLAMA_GPU_ENV_MANIFEST = ROOT / "scripts" / "llama-gpu-env-manifest.json"
SPIRV_ANALYZER = ROOT / "scripts" / "analyze-spirv.py"
SPIRV_PROBE_MANIFEST_VERIFIER = ROOT / "scripts" / "verify-spirv-probe-manifest.py"
SPIRV_NOOP_INSTRUMENTER = ROOT / "scripts" / "instrument-spirv-noop-probe.py"
SPIRV_DATAFLOW_COMPARE = ROOT / "scripts" / "compare-spirv-dataflow.py"
SPIRV_EFFECTIVE_RECONSTRUCTOR = ROOT / "scripts" / "reconstruct-q6-effective-spirv.py"
LLAMA_Q6_PREFLIGHT_PLANNER = ROOT / "scripts" / "plan-llama-gpu-q6-run.py"
LLAMA_Q6_PLAN_VERIFIER = ROOT / "scripts" / "verify-llama-gpu-q6-run-against-plan.py"
Q6_STAGE_TRACE_SPVASM_ANALYZER = ROOT / "scripts" / "maintenance" / "analyze-q6-stage-trace-spvasm.py"


def load_llama_gpu_artifact_verifier():
    spec = importlib.util.spec_from_file_location("llama_gpu_artifact_verifier", LLAMA_GPU_ARTIFACT_VERIFIER)
    verifier = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(verifier)
    return verifier


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def llama_bridge_binary_identity(abi: str = "arm64-v8a"):
    executor = ROOT / "app" / "src" / "main" / "jniLibs" / abi / "libpdockergpuexecutor.so"
    icd = ROOT / "app" / "src" / "main" / "jniLibs" / abi / "libpdockervulkanicd.so"
    executor_sha = sha256_file(executor)
    icd_sha = sha256_file(icd)
    return {
        "schema": "pdocker.llama.gpu.bridge-binary-identity.v1",
        "hash_algorithm": "sha256",
        "abi": abi,
        "device_abi": abi,
        "package": "io.github.ryo100794.pdocker.compat",
        "checked_out_jni": {
            "gpu_executor": {
                "path": str(executor.relative_to(ROOT)),
                "sha256": executor_sha,
                "size": executor.stat().st_size,
            },
            "vulkan_icd": {
                "path": str(icd.relative_to(ROOT)),
                "sha256": icd_sha,
                "size": icd.stat().st_size,
            },
        },
        "installed": {
            "gpu_executor": {
                "path": "pdocker-runtime/gpu/pdocker-gpu-executor",
                "target": "/data/app/libpdockergpuexecutor.so",
                "sha256": executor_sha,
                "size": executor.stat().st_size,
            },
            "vulkan_icd": {
                "path": "pdocker-runtime/lib/pdocker-vulkan-icd.so",
                "target": "/data/app/libpdockervulkanicd.so",
                "sha256": icd_sha,
                "size": icd.stat().st_size,
            },
        },
        "runtime": {"gpu_executor": [], "vulkan_icd": []},
        "missing": [],
        "mismatches": [],
        "unparsed": [],
        "summary": "pass",
    }


def llama_runtime_freshness_pass():
    return {
        "summary": "pass",
        "marker_summary": "pass",
        "bridge_binary_summary": "pass",
        "expected_executor_marker": "gpu-executor-q6-readonly-snapshot-20260531",
        "observed_executor_markers": ["gpu-executor-q6-readonly-snapshot-20260531"],
        "expected_icd_marker": "vulkan-icd-feature-chain-marker-20260518",
        "observed_icd_markers": ["vulkan-icd-feature-chain-marker-20260518"],
        "executor_event_count": 1,
        "bridge_binary_identity": llama_bridge_binary_identity(),
    }


def load_llama_q6_plan_verifier():
    spec = importlib.util.spec_from_file_location("llama_q6_plan_verifier", LLAMA_Q6_PLAN_VERIFIER)
    verifier = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(verifier)
    return verifier


def load_spirv_effective_reconstructor():
    spec = importlib.util.spec_from_file_location(
        "spirv_effective_reconstructor", SPIRV_EFFECTIVE_RECONSTRUCTOR
    )
    reconstructor = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(reconstructor)
    return reconstructor


def q6_required_runtime_env_manifest(plan_path):
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    required = {str(k): str(v) for k, v in plan["q6_required_env_overlay"].items()}
    return {
        "schema": "pdocker.llama.gpu.runtime-env-artifact.v1",
        "host_requested_env": required,
        "requested_env_missing_from_runtime": [],
        "requested_env_observed_keys": sorted(required),
        "runtime_env_observed_keys": sorted(required),
    }


def load_llama_gpu_compare_q6_helpers():
    source = LLAMA_COMPARE.read_text()
    start = source.index("Q6_K_MATVEC_SPIRV_HASHES = {")
    end = source.index("\nvalid_spirv_events = [", start)
    namespace = {}
    exec(compile(source[start:end], str(LLAMA_COMPARE), "exec"), namespace)
    return namespace


def classify_q6_readonly_alias_side_effects(binding_details):
    """Run the real compare-script Q6 binding classifier on synthetic details."""
    source = LLAMA_COMPARE.read_text()
    start = source.index("Q6_DESCRIPTOR_INVARIANT_FIELDS = (")
    end = source.index("\nq6_first_mismatch =", start)
    namespace = {
        "q6_latest": {"binding_details": binding_details},
        "q6_latest_oracle": {},
    }
    exec(compile(source[start:end], str(LLAMA_COMPARE), "exec"), namespace)
    return namespace


def load_q6_output_index_probe_classifier():
    source = LLAMA_COMPARE.read_text()
    start = source.index("def classify_q6_output_index_probe")
    end = source.index("\n\nq6_output_index_probe_summary =", start)
    namespace = {}
    exec(compile(source[start:end], str(LLAMA_COMPARE), "exec"), namespace)
    return namespace["classify_q6_output_index_probe"]


def load_q6_stage_trace_namespace(probe_manifest_path=None):
    source = LLAMA_COMPARE.read_text()
    start = source.index("Q6_LEGACY_FINAL_STORE_TRACE_EXPECTED_RECORDS = (")
    end = source.index("\n\ndef q6_oracle_sample_indices", start)
    probe_env = (
        {"PDOCKER_GPU_SPIRV_PROBE_MANIFEST": str(probe_manifest_path)}
        if probe_manifest_path is not None
        else {}
    )
    namespace = {
        "json": json,
        "Path": Path,
        "struct": __import__("struct"),
        "manifest_requested_env": probe_env,
        "effective_runtime_env": {},
    }
    exec(compile(source[start:end], str(LLAMA_COMPARE), "exec"), namespace)
    return namespace


def load_q6_stage_trace_parser():
    return load_q6_stage_trace_namespace()["parse_q6_final_store_trace_v2"]


def load_q6_stage_trace_spvasm_analyzer():
    spec = importlib.util.spec_from_file_location(
        "q6_stage_trace_spvasm_analyzer", Q6_STAGE_TRACE_SPVASM_ANALYZER
    )
    analyzer = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(analyzer)
    return analyzer


def defines(path):
    result = {}
    for line in path.read_text().splitlines():
        match = re.match(r"#define\s+(PDOCKER_GPU_[A-Z0-9_]+)\s+(.+)", line)
        if match:
            result[match.group(1)] = match.group(2).strip()
    return result


def v4_binding_schema(path):
    source = path.read_text()
    macro = re.search(
        r"#define\s+PDOCKER_GPU_VULKAN_DISPATCH_V4_BINDING_FIELDS\(X\)\s+\\\n(?P<body>.*?)\n\n"
        r"#define\s+PDOCKER_GPU_VULKAN_DISPATCH_V4_BINDING_FIELD_COUNT\s+(?P<count>\d+)u\n"
        r"#define\s+PDOCKER_GPU_VULKAN_DISPATCH_V4_BINDING_SCHEMA_HASH\s+(?P<hash>0x[0-9a-fA-F]+)ull",
        source,
        re.S,
    )
    assert macro is not None
    fields = re.findall(r"X\(([^,\s]+),\s*([^)\\\s]+)\)", macro.group("body"))
    fnv = 1469598103934665603
    for name, field_type in fields:
        for byte in f"{name}:{field_type}\0".encode():
            fnv ^= byte
            fnv = (fnv * 1099511628211) & ((1 << 64) - 1)
    return fields, int(macro.group("count")), int(macro.group("hash"), 16), fnv


def schema_hash(fields):
    fnv = 1469598103934665603
    for name, field_type in fields:
        for byte in f"{name}:{field_type}\0".encode():
            fnv ^= byte
            fnv = (fnv * 1099511628211) & ((1 << 64) - 1)
    return fnv


def vulkan_dispatch_v5_schema(path, field_macro, count_macro, hash_macro=None):
    source = path.read_text()
    macro = re.search(
        rf"#define\s+{field_macro}\(X\)\s+\\\n(?P<body>.*?)\n"
        rf"#define\s+{count_macro}\s+(?P<count>\d+)u",
        source,
        re.S,
    )
    assert macro is not None, field_macro
    fields = re.findall(r"X\(([^,\s]+),\s*([^)\\\s]+)\)", macro.group("body"))
    declared_hash = None
    if hash_macro is not None:
        hash_define = re.search(
            rf"#define\s+{hash_macro}\s+(?P<hash>0x[0-9a-fA-F]+)ull",
            source,
        )
        assert hash_define is not None, hash_macro
        declared_hash = int(hash_define.group("hash"), 16)
    return fields, int(macro.group("count")), declared_hash, schema_hash(fields)


def c_struct_field_names(path, struct_name):
    source = path.read_text()
    struct = re.search(
        rf"typedef\s+struct\s+{struct_name}\s*\{{(?P<body>.*?)\}}\s+{struct_name};",
        source,
        re.S,
    )
    assert struct is not None, struct_name
    return re.findall(
        r"^\s*(?:char|u?int(?:8|16|32|64)_t|PdockerGpuVulkan[A-Za-z0-9]+)\s+"
        r"([A-Za-z_][A-Za-z0-9_]*)(?:\[[^\]]+\])?;",
        struct.group("body"),
        re.M,
    )


def vulkan_dispatch_option_envs(path):
    source = path.read_text()

    def macro_envs(name):
        macro = re.search(
            rf"#define\s+{name}\(X\)\s+\\\n(?P<body>.*?)(?:\n\n|/\*)",
            source,
            re.S,
        )
        assert macro is not None, name
        return set(re.findall(r"X\((PDOCKER_[A-Z0-9_]+)", macro.group("body")))

    return {
        "bool": (
            macro_envs("PDOCKER_GPU_VULKAN_BOOL_DISPATCH_OPTIONS")
            | macro_envs("PDOCKER_GPU_VULKAN_BOOL_DISPATCH_OPTIONS_NO_HAS")
        ),
        "size": macro_envs("PDOCKER_GPU_VULKAN_SIZE_DISPATCH_OPTIONS"),
        "string": macro_envs("PDOCKER_GPU_VULKAN_STRING_DISPATCH_OPTIONS"),
    }


def c_function_body(source, name):
    signature = re.search(
        rf"(?m)^(?:VKAPI_ATTR\s+)?[A-Za-z_][A-Za-z0-9_\s\*]*?"
        rf"(?:VKAPI_CALL\s+)?{re.escape(name)}\s*\([^;]*?\)\s*\{{",
        source,
        re.S,
    )
    assert signature is not None, name
    start = signature.end() - 1
    depth = 0
    for index in range(start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start + 1:index]
    raise AssertionError(f"unterminated function body: {name}")


def c_block_from(source, needle):
    start = source.index(needle)
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[brace + 1:index]
    raise AssertionError(f"unterminated C block after: {needle}")


class GpuAbiContractTest(unittest.TestCase):
    def test_container_and_apk_gpu_abi_headers_stay_in_sync(self):
        self.assertEqual(CONTAINER_HEADER.read_text(), APP_HEADER.read_text())
        self.assertEqual(defines(CONTAINER_HEADER), defines(APP_HEADER))

    def test_gpu_abi_remains_backend_neutral(self):
        values = "\n".join(defines(APP_HEADER).values()).lower()
        for forbidden in ["android.hardware", "bionic", "libvulkan.so", "libopencl.so"]:
            self.assertNotIn(forbidden, values)
        self.assertIn("pdocker-gpu-command-v1", values)
        self.assertIn("glibc-shim-command-queue", values)

    def test_q6_oracle_does_not_collapse_shader_coordinates_to_rows(self):
        source = GPU_EXECUTOR.read_text()
        self.assertIn("q6k_matvec_store_index_from_dispatch", source)
        self.assertIn("q6k_row_to_dispatch_coordinates", source)
        self.assertIn("dispatch_x", source)
        self.assertIn("dispatch_y", source)
        self.assertIn("dispatch_z", source)
        self.assertIn("q6_num_rows", source)
        self.assertIn("q6_num_cols", source)
        self.assertNotIn("const uint64_t dst_index = output_base_index + (uint64_t)row;", source)
        self.assertIn("sample->store_formula_valid", source)
        self.assertIn('\\"store_workgroup\\":[%u,%u,%u]', source)

    def test_q6_effective_spirv_reconstructor_mirrors_executor_lowering_order(self):
        reconstructor = load_spirv_effective_reconstructor()
        # Minimal structurally valid word stream for the executor transformations:
        # literal LocalSize 1x1x1, WorkgroupSize.x SpecId 0, one SpecConstantOp,
        # and two descriptor variables sharing Binding 0.
        words = [
            0x07230203,
            0x00010300,
            0,
            64,
            0,
            (6 << 16) | 16,
            4,
            17,
            1,
            1,
            1,
            (4 << 16) | 71,
            10,
            1,
            0,
            (4 << 16) | 71,
            20,
            11,
            25,
            (4 << 16) | 71,
            30,
            34,
            0,
            (4 << 16) | 71,
            30,
            33,
            0,
            (4 << 16) | 71,
            31,
            34,
            0,
            (4 << 16) | 71,
            31,
            33,
            0,
            (4 << 16) | 50,
            1,
            10,
            1,
            (4 << 16) | 43,
            1,
            11,
            1,
            (4 << 16) | 43,
            1,
            12,
            1,
            (6 << 16) | 51,
            2,
            20,
            10,
            11,
            12,
            (6 << 16) | 52,
            1,
            21,
            134,
            10,
            11,
        ]
        event = {
            "specialization_entries": [{"constant_id": 0, "value_u64": 32}],
            "binding_details": [{"binding": 0}, {"binding": 1}],
        }
        effective_words, steps = reconstructor.reconstruct(words, event)
        self.assertEqual([step["phase"] for step in steps], [
            "source",
            "local-size-legalized",
            "specialization-materialized",
            "q6-storage16-loads-lowered",
            "q6-u32-to-u8vec4-bitcasts-lowered",
            "q6-final-store-pre-barrier",
            "duplicate-descriptor-rewritten",
        ])
        self.assertTrue(steps[1]["changed"])
        self.assertEqual(steps[1]["resolved"], [32, 1, 1])
        self.assertTrue(steps[2]["changed"])
        self.assertEqual(steps[2]["spec_constants_folded"], 1)
        self.assertEqual(steps[2]["spec_composites_folded"], 1)
        self.assertEqual(steps[2]["spec_ops_folded"], 1)
        self.assertFalse(steps[3]["changed"])
        self.assertFalse(steps[4]["changed"])
        self.assertFalse(steps[5]["changed"])
        self.assertEqual(
            steps[6]["aliases"],
            [{"target_id": 31, "original_binding": 0, "rewritten_binding": 2}],
        )
        self.assertIn((6 << 16) | 16, effective_words)
        local_size_index = effective_words.index((6 << 16) | 16)
        self.assertEqual(effective_words[local_size_index + 3:local_size_index + 6], [32, 1, 1])

    def test_q6_effective_reconstructor_lowers_storage16_duplicate_view_loads(self):
        reconstructor = load_spirv_effective_reconstructor()
        words = [
            0x07230203, 0x00010300, 0, 400, 0,
            (4 << 16) | 21, 1, 32, 0,      # uint
            (4 << 16) | 21, 2, 8, 0,       # uchar
            (4 << 16) | 21, 3, 16, 0,      # ushort
            (4 << 16) | 32, 4, 12, 3,      # ptr storage ushort
            (4 << 16) | 43, 1, 10, 1,
            (4 << 16) | 43, 1, 11, 2,
            (4 << 16) | 43, 1, 12, 8,
            (8 << 16) | 65, 4, 100, 371, 10, 20, 30, 40,
            (4 << 16) | 61, 3, 101, 100,
        ]
        lowered, step = reconstructor.lower_q6k_storage16_loads_to_storage8(words)
        self.assertTrue(step["changed"])
        self.assertEqual(step["lowered_count"], 1)
        self.assertEqual(lowered[3], 411)
        for index, opcode, word_count, inst in reconstructor.iter_instructions(lowered):
            if opcode == 65 and word_count >= 4:
                self.assertNotEqual(inst[3], 371)
        self.assertIn((5 << 16) | 197, lowered)
        self.assertIn((4 << 16) | 113, lowered)

    def test_q6_effective_reconstructor_lowers_u32_to_u8vec4_bitcasts(self):
        reconstructor = load_spirv_effective_reconstructor()
        words = [
            0x07230203, 0x00010300, 0, 500, 0,
            (4 << 16) | 21, 1, 32, 0,      # uint
            (4 << 16) | 21, 2, 8, 0,       # uchar
            (4 << 16) | 23, 3, 2, 4,       # u8vec4
            (4 << 16) | 43, 1, 10, 8,
            (4 << 16) | 43, 1, 11, 16,
            (4 << 16) | 43, 1, 12, 24,
            (4 << 16) | 124, 3, 100, 99,
        ]
        lowered, step = reconstructor.lower_q6k_u32_to_u8vec4_bitcasts(words)
        self.assertTrue(step["changed"])
        self.assertEqual(step["lowered_count"], 1)
        self.assertEqual(lowered[3], 507)
        self.assertNotIn((4 << 16) | 124, lowered)
        self.assertIn((5 << 16) | 194, lowered)
        self.assertIn((7 << 16) | 80, lowered)
        composite_index = lowered.index((7 << 16) | 80)
        self.assertEqual(lowered[composite_index + 1:composite_index + 3], [3, 100])

    def test_q6_effective_reconstructor_inserts_final_store_pre_barrier(self):
        reconstructor = load_spirv_effective_reconstructor()
        words = [
            0x07230203, 0x00010300, 0, 2000, 0,
            (2 << 16) | 20, 28,
            (4 << 16) | 21, 6, 32, 0,
            (4 << 16) | 43, 6, 52, 0,
            (4 << 16) | 43, 6, 31, 2,
            (4 << 16) | 43, 6, 36, 264,
            (2 << 16) | 248, 1806,
            (5 << 16) | 170, 28, 1807, 915, 52,
        ]
        out, step = reconstructor.insert_q6k_final_store_pre_barrier(words)
        self.assertTrue(step["changed"])
        label_index = out.index((2 << 16) | 248)
        self.assertEqual(out[label_index + 2:label_index + 6], [(4 << 16) | 224, 31, 31, 36])

    def test_q6_debug_probe_alias_guard_blocks_before_final_store_diagnosis(self):
        compare = LLAMA_COMPARE.read_text()
        verifier = LLAMA_GPU_ARTIFACT_VERIFIER.read_text()
        self.assertIn("def build_q6_debug_binding_alias_safety", compare)
        self.assertIn('"q6_debug_binding_alias_safety": q6_debug_binding_alias_safety', compare)
        self.assertIn('"q6-debug-binding-alias"', compare)
        self.assertIn('"q6-debug-binding-alias-evidence-missing"', compare)
        self.assertIn('"debug_probe_binding": detail.get("debug_probe_binding")', compare)
        self.assertIn('"binding_descriptor_offset": detail.get("binding_descriptor_offset")', compare)
        self.assertIn('"api_range": detail.get("api_range")', compare)
        self.assertLess(
            compare.index('if q6_debug_binding_alias_safety.get("summary") == "fail"'),
            compare.index("if q6_debug_u32_probe_blocker"),
        )
        self.assertLess(
            compare.index('if q6_debug_binding_alias_safety.get("summary") == "missing-evidence"'),
            compare.index("if q6_debug_u32_probe_blocker"),
        )
        self.assertLess(
            compare.index('if q6_debug_binding_alias_safety.get("summary") == "fail"'),
            compare.index('if q6_native_vs_writeback_split.get("summary") == "executor-final-writeback"'),
        )
        self.assertIn("def _q6_debug_binding_alias_safety", verifier)
        self.assertIn("def _q6_debug_alias_evidence_missing", verifier)
        self.assertIn('"q6_debug_binding_alias_safety": q6_debug_binding_alias_safety', verifier)
        self.assertLess(
            verifier.index('if q6_debug_binding_alias_safety.get("summary") == "fail"'),
            verifier.index("elif q6_debug_u32_probe_blocker:"),
        )
        self.assertLess(
            verifier.index("elif _q6_debug_alias_evidence_missing("),
            verifier.index("elif q6_debug_u32_probe_blocker:"),
        )

    def test_legacy_q6_fixed_layout_tools_are_marked_non_authoritative(self):
        standalone = (ROOT / "scripts" / "parse-q6k-probe-u32.py").read_text()
        analyzer = (ROOT / "scripts" / "maintenance" / "analyze-q6-stage-trace-spvasm.py").read_text()
        for source in [standalone, analyzer]:
            self.assertIn("Legacy", source)
            self.assertIn("fixed", source)
        self.assertIn("manifest-driven parser embedded in", standalone)
        self.assertIn("not use it as the authoritative", standalone)
        self.assertIn("instrumentation.probe_writes", analyzer)
        self.assertIn("only for archived fixed-layout fixtures", analyzer)

    def test_q6_stage_trace_parser_uses_probe_manifest_expectations(self):
        probe_writes = [
            {
                "candidate_id": 205,
                "role_code": 1,
                "role": "partial_to_workgroup_candidate",
                "phase": "full",
                "slot_base": 68,
                "lane_trace_layout": {"slot_base": 144, "lane_count": 64, "words_per_lane": 8},
            },
            {
                "candidate_id": 215,
                "role_code": 2,
                "role": "reduction_candidate",
                "phase": "full",
                "slot_base": 80,
                "lane_trace_layout": {"slot_base": 656, "lane_count": 64, "words_per_lane": 8},
            },
            {
                "candidate_id": 227,
                "role_code": 3,
                "role": "post_reduction_workgroup_candidate",
                "phase": "full",
                "slot_base": 92,
            },
            {
                "candidate_id": 229,
                "role_code": 3,
                "role": "post_reduction_workgroup_candidate",
                "phase": "full",
                "slot_base": 104,
            },
            {
                "candidate_id": 230,
                "role_code": 4,
                "role": "final_output_store",
                "phase": "full",
                "slot_base": 116,
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / "q6.write.instrumentation.json"
            manifest_path.write_text(
                json.dumps({"instrumentation": {"probe_writes": probe_writes}}),
                encoding="utf-8",
            )
            namespace = load_q6_stage_trace_namespace(manifest_path)

        expectation_report = namespace["Q6_PROBE_EXPECTATION_REPORT"]
        self.assertEqual("manifest", expectation_report["source"])
        self.assertEqual(
            [205, 215, 227, 229, 230],
            [record["candidate_id"] for record in namespace["Q6_FINAL_STORE_TRACE_EXPECTED_RECORDS"]],
        )
        self.assertNotIn(
            105,
            [record["candidate_id"] for record in namespace["Q6_FINAL_STORE_TRACE_EXPECTED_RECORDS"]],
        )
        parser = namespace["parse_q6_final_store_trace_v2"]
        values = {
            128: 1,
            129: 64,
            130: 8,
            131: 144,
            132: 656,
        }
        for record, value_bits in [
            (probe_writes[0], 0x3f500000),
            (probe_writes[1], 0x3f600000),
            (probe_writes[2], 0x3f700000),
            (probe_writes[3], 0x3f800000),
            (probe_writes[4], 0x3f900000),
        ]:
            base = record["slot_base"]
            values[base] = record["candidate_id"]
            values[base + 1] = record["role_code"]
            values[base + 2] = value_bits
            if record["role_code"] == 4:
                values[base + 3] = 151935
                values[base + 4] = 1186
                values[base + 5] = 0
                values[base + 6] = 63
                values[base + 7] = 0
                values[base + 8] = 0
                values[base + 9] = 0
                values[base + 10] = 2
        for lane_base, candidate_id, value_bits in [
            (144 + 3 * 8, 205, 0x3fa00000),
            (656 + 3 * 8, 215, 0x40200000),
        ]:
            values[lane_base] = 3
            values[lane_base + 1] = value_bits
            values[lane_base + 2] = 1186
            values[lane_base + 3] = 0
            values[lane_base + 4] = 63
            values[lane_base + 5] = candidate_id
            values[lane_base + 6] = 0
            values[lane_base + 7] = 1
        samples = [{"index": index, "value": value} for index, value in sorted(values.items())]
        report = parser([
            {
                "binding": 5,
                "set": 0,
                "size": 65536,
                "debug_probe_binding": True,
                "u32_after_dispatch": samples,
                "u32_after_writeback": samples,
            }
        ])
        self.assertEqual("pass", report["summary"])
        self.assertEqual(5, report["bindings"][0]["executed_stage_trace_v2_count"])
        self.assertEqual(1, report["bindings"][0]["executed_final_trace_v2_count"])
        lane_trace = report["bindings"][0]["lane_trace_v1"]
        self.assertEqual("pass", lane_trace["summary"])
        self.assertEqual(
            [205, 215],
            [phase["expected_candidate_id"] for phase in lane_trace["phases"]],
        )

    def test_gpu_executor_scans_q6_final_store_debug_records_by_schema(self):
        source = GPU_EXECUTOR.read_text()
        for marker in [
            "PDOCKER_GPU_Q6_DEBUG_PROBE_RECORD_SCHEMA_VERSION 2u",
            "PDOCKER_GPU_Q6_DEBUG_PROBE_ROLE_FINAL_OUTPUT_STORE 4u",
            "PDOCKER_GPU_Q6_DEBUG_PROBE_MAX_U32_INDEX 1216u",
            "PDOCKER_GPU_Q6_DEBUG_PROBE_RECORD_WORDS 11u",
            "base + PDOCKER_GPU_Q6_DEBUG_PROBE_RECORD_WORDS <= scan_u32_count",
            "role != PDOCKER_GPU_Q6_DEBUG_PROBE_ROLE_FINAL_OUTPUT_STORE",
            "schema != PDOCKER_GPU_Q6_DEBUG_PROBE_RECORD_SCHEMA_VERSION",
            "(uint64_t)out_index >= output_float_count",
            "candidate == 0u",
            "PDOCKER_GPU_Q6_DEBUG_PROBE_MAX_U32_INDEX);",
        ]:
            self.assertIn(marker, source)
        self.assertNotIn("{56u, 64u, 4u}", source)
        self.assertNotIn("{116u, 130u, 4u}", source)

    def test_q6_stage_trace_parser_accepts_nonfinal_stage_records(self):
        parser = load_q6_stage_trace_parser()

        def samples_for_records():
            values = {}
            for record in (
                {"slot_base": 8, "candidate_id": 39, "role_code": 1, "value_bits": 0x3f000000},
                {"slot_base": 20, "candidate_id": 49, "role_code": 2, "value_bits": 0x3f100000},
                {"slot_base": 32, "candidate_id": 61, "role_code": 3, "value_bits": 0x3f200000},
                {"slot_base": 44, "candidate_id": 63, "role_code": 3, "value_bits": 0x3f300000},
                {"slot_base": 56, "candidate_id": 64, "role_code": 4, "value_bits": 0x3f400000},
                {"slot_base": 68, "candidate_id": 105, "role_code": 1, "value_bits": 0x3f500000},
                {"slot_base": 80, "candidate_id": 115, "role_code": 2, "value_bits": 0x3f600000},
                {"slot_base": 92, "candidate_id": 127, "role_code": 3, "value_bits": 0x3f700000},
                {"slot_base": 104, "candidate_id": 129, "role_code": 3, "value_bits": 0x3f800000},
                {"slot_base": 116, "candidate_id": 130, "role_code": 4, "value_bits": 0x3f900000},
            ):
                base = record["slot_base"]
                values[base] = record["candidate_id"]
                values[base + 1] = record["role_code"]
                values[base + 2] = record["value_bits"]
                if record["role_code"] == 4:
                    values[base + 3] = 1000 + base
                    values[base + 4] = 1
                    values[base + 5] = 2
                    values[base + 6] = 3
                    values[base + 7] = 0
                    values[base + 8] = 0
                    values[base + 9] = 0
                    values[base + 10] = 2
            return [{"index": index, "value": value} for index, value in sorted(values.items())]

        bindings = [{
            "binding": 5,
            "set": 0,
            "size": 65536,
            "debug_probe_binding": True,
            "u32_after_dispatch": samples_for_records(),
            "u32_after_writeback": samples_for_records(),
        }]
        report = parser(bindings)
        self.assertEqual("pass", report["summary"])
        self.assertEqual(10, report["bindings"][0]["executed_stage_trace_v2_count"])
        self.assertEqual(2, report["bindings"][0]["executed_final_trace_v2_count"])
        records = report["bindings"][0]["records"]
        self.assertEqual(
            ["pre-reduction-store", "reduction-store", "accumulator-a-store",
             "accumulator-b-store", "final-store"],
            [record["stage"] for record in records[:5]],
        )
        self.assertEqual([], report["failures"])

    def test_q6_final_store_boundary_filters_nonfinal_stage_records(self):
        source = LLAMA_COMPARE.read_text()
        start = source.index("def build_q6_final_store_boundary():")
        end = source.index("\n\nq6_final_store_boundary =", start)
        body = source[start:end]
        append_pos = body.index("records.append({")
        guard_pos = body.index('record.get("role_code") == 4')
        self.assertLess(
            guard_pos,
            append_pos,
            "non-final Q6 stage trace records must not enter final-store boundary joins",
        )

    def test_q6_stage_trace_spvasm_analyzer_accepts_static_debug_slots(self):
        analyzer = load_q6_stage_trace_spvasm_analyzer()
        lines = [
            "OpDecorate %debug Binding 5",
            "%uint = OpTypeInt 32 0",
            "%uint_0 = OpConstant %uint 0",
            "%uint_1 = OpConstant %uint 1",
            "%uint_2 = OpConstant %uint 2",
            "%uint_3 = OpConstant %uint 3",
            "%uint_4 = OpConstant %uint 4",
        ]
        for value in {8, 9, 18, 20, 21, 30, 32, 33, 42, 44, 45, 54,
                      56, 57, 66, 68, 69, 78, 80, 81, 90, 92, 93, 102,
                      104, 105, 114, 116, 117, 126, 39, 49, 61, 63, 64,
                      105, 115, 127, 129, 130}:
            lines.append(f"%uint_{value} = OpConstant %uint {value}")
        ptr_id = 1000
        for slot, value in [
            (8, 39), (9, 1),
            (20, 49), (21, 2),
            (32, 61), (33, 3),
            (44, 63), (45, 3),
            (56, 64), (57, 4), (66, 2),
            (68, 105), (69, 1),
            (80, 115), (81, 2),
            (92, 127), (93, 3),
            (104, 129), (105, 3),
            (116, 130), (117, 4), (126, 2),
        ]:
            lines.append(f"%ptr_{ptr_id} = OpAccessChain %_ptr_StorageBuffer_uint %debug %uint_0 %uint_{slot}")
            lines.append(f"OpStore %ptr_{ptr_id} %uint_{value}")
            ptr_id += 1
        for base in (8, 20, 32, 44, 56, 68, 80, 92, 104, 116):
            lines.append(f"%val_{base} = OpBitcast %uint %float_{base}")
            lines.append(f"%ptr_{ptr_id} = OpAccessChain %_ptr_StorageBuffer_uint %debug %uint_0 %uint_{base + 2}")
            lines.append(f"OpStore %ptr_{ptr_id} %val_{base}")
            ptr_id += 1
        lines.append("%out_56 = OpIAdd %uint %row %stride")
        lines.append(f"%ptr_{ptr_id} = OpAccessChain %_ptr_StorageBuffer_uint %debug %uint_0 %uint_59")
        lines.append(f"OpStore %ptr_{ptr_id} %out_56")
        report = analyzer.parse_spvasm("\n".join(lines))
        self.assertEqual("pass", report["summary"])
        self.assertEqual("legacy-fixed-layout", report["expectation_source"])
        self.assertTrue(report["legacy_only"])
        self.assertFalse(report["authoritative_for_live_q6_probe"])
        self.assertEqual(10, report["passed_record_count"])
        self.assertEqual(["%debug"], report["debug_binding_variable_ids"])
        self.assertEqual("OpBitcast", report["records"][0]["value_source_producer"]["opcode"])
        self.assertEqual("%val_8", report["records"][0]["value_source_id"])
        self.assertEqual("OpBitcast", report["records"][0]["value_origin_opcode"])
        self.assertTrue(report["records"][0]["value_flow_context"])
        final_record = next(record for record in report["records"] if record["candidate_id"] == 64)
        self.assertEqual("%out_56", final_record["output_index_source_id"])
        self.assertEqual("OpIAdd", final_record["output_index_origin_opcode"])

    def test_executor_q6_debug_probe_alias_guard_fails_closed(self):
        source = GPU_EXECUTOR.read_text()
        for marker in [
            "validate_spirv_probe_debug_binding_alias_guard",
            "options && options->has_spirv_probe_debug_binding",
            "static const uint32_t kQ6ComputeBindings[] = {2u, 3u, 4u};",
            "vulkan_binding_api_absolute_descriptor_range",
            "binding->api_memory_id == 0",
            "binding->api_buffer_id == 0",
            "binding->api_range == 0",
            "Non-target shaders must",
            "bindings[debug_index].api_memory_id == bindings[q6_index].api_memory_id",
            "bindings[debug_index].api_buffer_id == bindings[q6_index].api_buffer_id",
            "same_api_object && debug_start < q6_end && q6_start < debug_end",
            "missing debug/probe api alias metadata",
            "missing q6 api alias metadata",
            "debug/probe binding overlaps q6 compute binding",
            "spirv probe debug alias guard failed",
            "probe_alias_reason ? probe_alias_reason",
            '"vulkan-dispatch",\n                      probe_alias_reason ? probe_alias_reason',
        ]:
            self.assertIn(marker, source)
        self.assertLess(
            source.index("if (validate_spirv_probe_debug_binding_alias_guard("),
            source.index("const int skip_unused_descriptor_transfers"),
        )


    def test_vulkan_dispatch_v4_binding_schema_is_single_source_and_checked(self):
        app_fields, app_count, app_hash, computed_hash = v4_binding_schema(APP_HEADER)
        container_fields, container_count, container_hash, container_computed_hash = v4_binding_schema(CONTAINER_HEADER)
        self.assertEqual(app_fields, container_fields)
        self.assertEqual(app_count, container_count)
        self.assertEqual(app_hash, container_hash)
        self.assertEqual(app_hash, computed_hash)
        self.assertEqual(container_hash, container_computed_hash)
        self.assertEqual(
            [
                ("descriptor_set", "u32"),
                ("binding", "u32"),
                ("offset", "u64"),
                ("size", "size"),
                ("api_offset", "u64"),
                ("api_range", "size"),
                ("api_buffer_size", "size"),
                ("api_descriptor_type", "u32"),
                ("api_dynamic", "u32"),
                ("api_memory_offset", "u64"),
                ("api_memory_size", "size"),
                ("api_memory_id", "u64"),
                ("api_buffer_id", "u64"),
            ],
            app_fields,
        )
        self.assertEqual(app_count, len(app_fields))

        icd = VULKAN_ICD.read_text()
        self.assertIn("v4_binding_schema=0x%016llx v4_binding_fields=%u", icd)
        self.assertIn("PDOCKER_GPU_VULKAN_DISPATCH_V4_BINDING_SCHEMA_HASH", icd)
        self.assertIn("PDOCKER_GPU_VULKAN_DISPATCH_V4_BINDING_FIELD_COUNT", icd)

        executor = GPU_EXECUTOR.read_text()
        self.assertIn('strncmp(token, "v4_binding_schema=", 18) == 0', executor)
        self.assertIn('strncmp(token, "v4_binding_fields=", 18) == 0', executor)
        self.assertIn("has_v4_binding_schema", executor)
        self.assertIn("has_v4_binding_field_count", executor)
        self.assertIn("!options.sender_reconcile.has_v4_binding_schema", executor)
        self.assertIn("!options.sender_reconcile.has_v4_binding_field_count", executor)
        self.assertIn(
            "options.sender_reconcile.v4_binding_schema !=\n"
            "                        PDOCKER_GPU_VULKAN_DISPATCH_V4_BINDING_SCHEMA_HASH",
            executor,
        )
        self.assertIn(
            "options.sender_reconcile.v4_binding_field_count !=\n"
            "                        PDOCKER_GPU_VULKAN_DISPATCH_V4_BINDING_FIELD_COUNT",
            executor,
        )

    def test_vulkan_icd_exposes_dynamic_rendering_and_graphics_fail_closed_scaffold(self):
        icd = VULKAN_ICD.read_text()
        self.assertIn("VK_KHR_DYNAMIC_RENDERING_EXTENSION_NAME", icd)
        self.assertIn("ADD_DEVICE_EXTENSION(VK_KHR_DYNAMIC_RENDERING_EXTENSION_NAME", icd)
        self.assertIn("VkPhysicalDeviceDynamicRenderingFeatures", icd)
        self.assertIn("p->dynamicRendering = advertised_dynamic_rendering();", icd)
        for name in [
            "vkCreateGraphicsPipelines",
            "vkCreateRenderPass",
            "vkCreateRenderPass2",
            "vkDestroyRenderPass",
            "vkCreateFramebuffer",
            "vkDestroyFramebuffer",
            "vkGetRenderAreaGranularity",
            "vkCreateHeadlessSurfaceEXT",
            "vkCmdBeginRendering",
            "vkCmdEndRendering",
            "vkCmdBeginRenderPass",
            "vkCmdNextSubpass",
            "vkCmdEndRenderPass",
            "vkCmdBeginRenderPass2",
            "vkCmdNextSubpass2",
            "vkCmdEndRenderPass2",
            "vkCmdBindVertexBuffers",
            "vkCmdBindVertexBuffers2",
            "vkCmdBindIndexBuffer",
            "vkCmdBindDescriptorSets",
            "vkCmdDraw",
            "vkCmdDrawIndexed",
            "vkCmdDrawIndirect",
            "vkCmdDrawIndexedIndirect",
            "vkCmdDrawIndirectCount",
            "vkCmdDrawIndexedIndirectCount",
            "vkCmdSetViewport",
            "vkCmdSetScissor",
            "vkCmdSetLineWidth",
            "vkCmdSetDepthBias",
            "vkCmdSetBlendConstants",
            "vkCmdSetDepthBounds",
            "vkCmdSetDepthBoundsTestEnable",
            "vkCmdSetDepthBoundsTestEnableEXT",
            "vkCmdSetStencilCompareMask",
            "vkCmdSetStencilWriteMask",
            "vkCmdSetStencilReference",
            "vkCmdSetViewportWithCount",
            "vkCmdSetScissorWithCount",
            "vkCmdSetCullMode",
            "vkCmdSetFrontFace",
            "vkCmdSetPrimitiveTopology",
            "vkCmdSetDepthTestEnable",
            "vkCmdSetDepthWriteEnable",
            "vkCmdSetDepthCompareOp",
            "vkCmdSetStencilTestEnable",
            "vkCmdSetStencilOp",
            "vkCmdClearAttachments",
            "vkCmdExecuteCommands",
            "vkDestroySurfaceKHR",
            "vkGetPhysicalDeviceSurfaceSupportKHR",
            "vkGetPhysicalDeviceSurfaceCapabilitiesKHR",
            "vkGetPhysicalDeviceSurfaceFormatsKHR",
            "vkGetPhysicalDeviceSurfacePresentModesKHR",
            "vkGetPhysicalDeviceSurfaceCapabilities2KHR",
            "vkGetPhysicalDeviceSurfaceFormats2KHR",
            "vkCreateSwapchainKHR",
            "vkDestroySwapchainKHR",
            "vkGetSwapchainImagesKHR",
            "vkAcquireNextImageKHR",
            "vkAcquireNextImage2KHR",
            "vkQueuePresentKHR",
        ]:
            self.assertRegex(icd, rf"VKAPI_ATTR\s+[\w\s\*]+VKAPI_CALL\s+{name}\s*\(")
            self.assertIn(f"MAP_PROC({name});", icd)
        for alias in [
            'MAP_ALIAS("vkCreateRenderPass2KHR", vkCreateRenderPass2);',
            'MAP_ALIAS("vkCmdBeginRenderingKHR", vkCmdBeginRendering);',
            'MAP_ALIAS("vkCmdEndRenderingKHR", vkCmdEndRendering);',
            'MAP_ALIAS("vkCmdBeginRenderPass2KHR", vkCmdBeginRenderPass2);',
            'MAP_ALIAS("vkCmdNextSubpass2KHR", vkCmdNextSubpass2);',
            'MAP_ALIAS("vkCmdEndRenderPass2KHR", vkCmdEndRenderPass2);',
            'MAP_ALIAS("vkCmdBindVertexBuffers2EXT", vkCmdBindVertexBuffers2);',
            'MAP_ALIAS("vkCmdDrawIndirectCountKHR", vkCmdDrawIndirectCount);',
            'MAP_ALIAS("vkCmdDrawIndirectCountAMD", vkCmdDrawIndirectCount);',
            'MAP_ALIAS("vkCmdDrawIndexedIndirectCountKHR", vkCmdDrawIndexedIndirectCount);',
            'MAP_ALIAS("vkCmdDrawIndexedIndirectCountAMD", vkCmdDrawIndexedIndirectCount);',
            'MAP_ALIAS("vkCmdSetViewportWithCountEXT", vkCmdSetViewportWithCount);',
            'MAP_ALIAS("vkCmdSetScissorWithCountEXT", vkCmdSetScissorWithCount);',
            'MAP_ALIAS("vkCmdSetCullModeEXT", vkCmdSetCullMode);',
            'MAP_ALIAS("vkCmdSetFrontFaceEXT", vkCmdSetFrontFace);',
            'MAP_ALIAS("vkCmdSetPrimitiveTopologyEXT", vkCmdSetPrimitiveTopology);',
            'MAP_ALIAS("vkCmdSetDepthTestEnableEXT", vkCmdSetDepthTestEnable);',
            'MAP_ALIAS("vkCmdSetDepthWriteEnableEXT", vkCmdSetDepthWriteEnable);',
            'MAP_ALIAS("vkCmdSetDepthCompareOpEXT", vkCmdSetDepthCompareOp);',
            'MAP_ALIAS("vkCmdSetStencilTestEnableEXT", vkCmdSetStencilTestEnable);',
            'MAP_ALIAS("vkCmdSetStencilOpEXT", vkCmdSetStencilOp);',
        ]:
            self.assertIn(alias, icd)
        for marker in [
            "struct PdockerVkRenderPass",
            "struct PdockerVkFramebuffer",
            "pipeline->graphics = true;",
            "pipeline->graphics_unsupported = false;",
            "PdockerVkPipeline *compute_pipeline;",
            "PdockerVkPipeline *graphics_pipeline;",
            "uint64_t layout_id;",
            "layout->layout_id = next_vulkan_object_generation();",
            "op->layout_id = captured_layout ? captured_layout->layout_id : 0;",
            "graphics_bound_set_snapshots[PDOCKER_VK_MAX_DESCRIPTOR_SETS]",
            "PDOCKER_VK_MAX_GRAPHICS_COMMAND_OPS",
            "PDOCKER_VK_MAX_GRAPHICS_DYNAMIC_OFFSETS",
            "PdockerVkGraphicsCommandRecord",
            "PDOCKER_VK_MAX_GRAPHICS_DESCRIPTOR_BIND_OPS",
            "PdockerVkGraphicsDescriptorBindSnapshot",
            "PdockerVkGraphicsRenderingSnapshot",
            "graphics_descriptor_bind_ops[PDOCKER_VK_MAX_GRAPHICS_DESCRIPTOR_BIND_OPS]",
            "graphics_rendering_ops[PDOCKER_VK_MAX_GRAPHICS_RENDERING_OPS]",
            "graphics_command_ops[PDOCKER_VK_MAX_GRAPHICS_COMMAND_OPS]",
            "graphics_dynamic_offsets[PDOCKER_VK_MAX_GRAPHICS_DYNAMIC_OFFSETS]",
            "append_graphics_command_record",
            "cmd->graphics_descriptor_bind_op_count = 0;",
            "cmd->graphics_rendering_op_count = 0;",
            "cmd->graphics_command_op_count = 0;",
            "cmd->graphics_dynamic_offset_count = 0;",
            "pipelineBindPoint == VK_PIPELINE_BIND_POINT_COMPUTE",
            "pipelineBindPoint == VK_PIPELINE_BIND_POINT_GRAPHICS",
            "cmd->compute_pipeline = pdocker_vk_pipeline_from_handle(pipeline);",
            "cmd->graphics_pipeline = pdocker_vk_pipeline_from_handle(pipeline);",
            "send_vulkan_graphics_v6_frame_with_fds",
            "send_empty_vulkan_graphics_v6_1_validation_frame",
            "send_recorded_vulkan_graphics_v6_1_frame",
            "find_graphics_pipeline_index",
            "PdockerGpuVulkanGraphicsV6ShaderStageEntry shader_stages",
            "PdockerGpuVulkanGraphicsV6PipelineEntry pipelines",
            "PdockerGpuVulkanDispatchV5ResourceEntry resources",
            "PdockerGpuVulkanDispatchV5DescriptorObjectEntry descriptors",
            "PdockerGpuVulkanDispatchV5ImageEntry image_entries",
            "PdockerGpuVulkanDispatchV5ImageViewEntry image_view_entries",
            "PdockerGpuVulkanDispatchV5SamplerEntry sampler_entries",
            "PdockerGpuVulkanGraphicsV6VertexBindingEntry vertex_bindings",
            "collect_graphics_memory_resource",
            "collect_graphics_buffer_resource",
            "collect_graphics_descriptor_entries",
            "collect_graphics_image_entry",
            "collect_graphics_image_view_entry",
            "collect_graphics_sampler_entry",
            "collect_graphics_attachment_entries",
            "append_graphics_attachment_entry",
            "header->resource_count = (uint32_t)resource_count;",
            "header->descriptor_count = (uint32_t)descriptor_count;",
            "header->image_count = (uint32_t)image_count;",
            "header->image_view_count = (uint32_t)image_view_count;",
            "header->sampler_count = (uint32_t)sampler_count;",
            "header->vertex_binding_count = (uint32_t)vertex_binding_count;",
            "header->attachment_count = (uint32_t)attachment_count;",
            "APPEND_GRAPHICS_TABLE(resources, resource_count",
            "APPEND_GRAPHICS_TABLE(descriptors, descriptor_count",
            "APPEND_GRAPHICS_TABLE(image_entries, image_count",
            "APPEND_GRAPHICS_TABLE(image_view_entries, image_view_count",
            "APPEND_GRAPHICS_TABLE(sampler_entries, sampler_count",
            "APPEND_GRAPHICS_TABLE(vertex_bindings, vertex_binding_count",
            "APPEND_GRAPHICS_TABLE(attachments, attachment_count",
            "PdockerGpuVulkanGraphicsV6CommandEntry commands",
            "graphics_submit_sync_frame_bounds",
            "filter_submit_sync_entries_for_graphics_frame",
            "frame_submit_sync_entries",
            "submit_sync_entries_include_wait",
            "submit_sync_entries_include_completion",
            "command_buffer_has_recorded_submit_work",
            "submit_has_recorded_work_before_command",
            "submit_has_recorded_work_after_command",
            "command_buffer_has_host_side_ops_before",
            "command_buffer_has_host_side_ops_after",
            "filter_submit_sync_entries_wait_only",
            "filter_submit_sync_entries_without_waits",
            "split graphics submit wait sync before prior host-side work",
            "graphics-v6-pre-wait-sync-failed",
            "filter_submit_sync_entries_without_completion",
            "deferred graphics submit completion sync until trailing host-side work finishes",
            "send_recorded_vulkan_graphics_v6_1_frame(\n                        cmd, frame_submit_sync_entries, frame_submit_sync_count)",
            "graphics_mixed_submit_plan",
            "execute_graphics_mixed_host_side_ops",
            "command_op_is_host_transfer_or_layout_op",
            "graphics-v6-submit-failed",
            "graphics-mixed-submit-unimplemented",
            "graphics-mixed-transfer-between-draws-unimplemented",
            "PDOCKER_VULKAN_GRAPHICS_V6_VALIDATE_PRODUCER",
            "command->vertex_count = draw->vertex_count;",
            "command->push_hash = fnv1a64_bytes(push_data, push->size);",
            "PDOCKER_VULKAN_GRAPHICS_V6_VALIDATE_PRODUCER",
            "header->abi_minor = PDOCKER_GPU_VULKAN_GRAPHICS_V61_ABI_MINOR;",
            "frame.v61.extension_hash = 1469598103934665603ull;",
            "CMSG_SPACE(sizeof(int) * PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_FDS)",
            "frame + sizeof(PdockerGpuVulkanGraphicsV6FrameHeader)",
            "record.command_type = PDOCKER_GPU_GRAPHICS_V6_COMMAND_BIND_PIPELINE;",
            "record.command_type = PDOCKER_GPU_GRAPHICS_V6_COMMAND_BEGIN_RENDERING;",
            "append_graphics_rendering_snapshot",
            "record.rendering_snapshot_index = rendering_snapshot_index;",
            "command->attachment_first = (uint32_t)attachment_count;",
            "command->attachment_count = (uint32_t)(attachment_count - command->attachment_first);",
            "record.command_type = PDOCKER_GPU_GRAPHICS_V6_COMMAND_END_RENDERING;",
            "record.command_type = PDOCKER_GPU_GRAPHICS_V6_COMMAND_BIND_DESCRIPTOR_SETS;",
            "record.descriptor_bind_snapshot_index = bind_snapshot_index;",
            "command->first_descriptor = (uint32_t)descriptor_count;",
            "dynamic_descriptor_count != record->dynamic_offset_count",
            "descriptor_type_requires_image_view(descriptor_type)",
            "collect_graphics_image_view_entry",
            "descriptor->image_view_index = view_index;",
            "descriptor->sampler_index = sampler_index;",
            "record.command_type = PDOCKER_GPU_GRAPHICS_V6_COMMAND_PUSH_CONSTANTS;",
            "record.command_type = PDOCKER_GPU_GRAPHICS_V6_COMMAND_BIND_VERTEX_BUFFERS;",
            "record.command_type = PDOCKER_GPU_GRAPHICS_V6_COMMAND_BIND_INDEX_BUFFER;",
            "graphics_record.command_type = indexed",
            "target_set_snapshots = cmd->graphics_bound_set_snapshots;",
            "target_set_snapshots = cmd->bound_set_snapshots;",
            "PDOCKER_VK_COMMAND_GRAPHICS_DRAW",
            "record_graphics_draw_command",
            "PDOCKER_VK_MAX_GRAPHICS_DRAW_OPS",
            "PdockerVkGraphicsDrawSnapshot",
            "graphics_draw_ops[PDOCKER_VK_MAX_GRAPHICS_DRAW_OPS]",
            "cmd->graphics_draw_op_count = 0;",
            "snapshot->pipeline = cmd->graphics_pipeline;",
            "memcpy(snapshot->set_snapshots, cmd->graphics_bound_set_snapshots",
            "memcpy(snapshot->push_constants, cmd->push_constants",
            "snapshot->push_constant_op_count = cmd->push_constant_op_count;",
            "memcpy(snapshot->vertex_bindings, cmd->vertex_bindings",
            "snapshot->index_buffer = cmd->index_buffer;",
            "memcpy(snapshot->dynamic_states, cmd->dynamic_states",
            "snapshot->dynamic_rendering_active = cmd->dynamic_rendering_active;",
            "snapshot->render_pass_active = cmd->render_pass_active;",
            "snapshot->active_render_pass = cmd->active_render_pass;",
            "snapshot->active_framebuffer = cmd->active_framebuffer;",
            "snapshot->active_render_area = cmd->active_render_area;",
            "cmd->active_rendering_flags = pRenderingInfo->flags;",
            "cmd->active_rendering_layer_count = pRenderingInfo->layerCount;",
            "cmd->active_rendering_view_mask = pRenderingInfo->viewMask;",
            "snapshot->active_rendering_flags = cmd->active_rendering_flags;",
            "snapshot->active_rendering_layer_count = cmd->active_rendering_layer_count;",
            "snapshot->active_rendering_view_mask = cmd->active_rendering_view_mask;",
            "memcpy(snapshot->active_color_attachments, cmd->active_color_attachments",
            "snapshot->active_depth_attachment = cmd->active_depth_attachment;",
            "snapshot->active_stencil_attachment = cmd->active_stencil_attachment;",
            "snapshot->active_clear_value_count = cmd->active_clear_value_count;",
            "snapshot->vertex_count = vertexCount;",
            "snapshot->indexed = indexed;",
            "op.index = snapshot_index;",
            "graphics-draw-unimplemented",
            "cmd->dynamic_rendering_active",
            "cmd->render_pass_active",
            "cmd->vertex_buffer_bound",
            "cmd->index_buffer_bound",
            "PdockerVkVertexBindingState",
            "record_vertex_buffer_bindings",
            "binding->buffer = pdocker_vk_buffer_from_handle(pBuffers[i]);",
            "binding->offset = pOffsets[i];",
            "binding->size = pSizes ? pSizes[i] : VK_WHOLE_SIZE;",
            "binding->stride = pStrides ? pStrides[i] : 0;",
            "cmd->index_buffer = pdocker_vk_buffer_from_handle(buffer);",
            "cmd->index_offset = offset;",
            "cmd->index_type = indexType;",
            "op.draw_first_vertex = firstVertex;",
            "op.draw_first_instance = firstInstance;",
            "op.draw_first_index = firstIndex;",
            "op.draw_vertex_offset = vertexOffset;",
            "record_graphics_dynamic_state_bytes",
            "VK_DYNAMIC_STATE_VIEWPORT",
            "VK_DYNAMIC_STATE_SCISSOR",
            "pipeline->depth_stencil_flags =",
            "PDOCKER_GPU_GRAPHICS_V63_DEPTH_STENCIL_DEPTH_TEST_ENABLE",
            "pipeline->front_stencil_state = ds->front",
            "pipeline->back_stencil_state = ds->back",
            "VK_DYNAMIC_STATE_LINE_WIDTH",
            "VK_DYNAMIC_STATE_CULL_MODE",
            "VK_DYNAMIC_STATE_FRONT_FACE",
            "VK_DYNAMIC_STATE_PRIMITIVE_TOPOLOGY",
            "VK_DYNAMIC_STATE_DEPTH_BIAS",
            "VK_DYNAMIC_STATE_BLEND_CONSTANTS",
            "VK_DYNAMIC_STATE_DEPTH_BOUNDS",
            "VK_DYNAMIC_STATE_STENCIL_COMPARE_MASK",
            "VK_DYNAMIC_STATE_STENCIL_WRITE_MASK",
            "VK_DYNAMIC_STATE_STENCIL_REFERENCE",
            "VK_DYNAMIC_STATE_DEPTH_TEST_ENABLE",
            "VK_DYNAMIC_STATE_DEPTH_WRITE_ENABLE",
            "VK_DYNAMIC_STATE_DEPTH_COMPARE_OP",
            "VK_DYNAMIC_STATE_STENCIL_TEST_ENABLE",
            "VK_DYNAMIC_STATE_STENCIL_OP",
            "pipeline->vertex_bindings[b]",
            "pipeline->vertex_attributes[a]",
            "pipeline->dynamic_state_mask",
            "VK_STRUCTURE_TYPE_PIPELINE_RENDERING_CREATE_INFO",
            "VkPipelineRenderingCreateInfo",
            "pipeline->dynamic_rendering_pipeline = true;",
            "pipeline->dynamic_rendering_view_mask = rendering->viewMask;",
            "pipeline->dynamic_rendering_color_attachment_count",
            "pipeline->dynamic_rendering_color_formats[c]",
            "pipeline->dynamic_rendering_depth_format = rendering->depthAttachmentFormat;",
            "pipeline->dynamic_rendering_stencil_format = rendering->stencilAttachmentFormat;",
            "PdockerVkRenderPassAttachmentState",
            "PdockerVkRenderingAttachmentState",
            "copy_rendering_attachment_state",
            "cmd->active_render_area = pRenderingInfo->renderArea;",
            "cmd->active_color_attachments[i]",
            "cmd->active_depth_attachment",
            "cmd->active_stencil_attachment",
            "rp->attachments[a].format = src->format;",
            "rp->attachments[a].load_op = src->loadOp;",
            "cmd->active_clear_values[i]",
            "uint32_t next_subpass = cmd->active_subpass + 1u;",
            "VkDeviceSize base_offset;",
            "VkDeviceSize dynamic_offset;",
            "api_dynamic_offsets[binding_count] = binding->dynamic_offset;",
            "descriptors[i].dynamic_offset = (uint64_t)api_dynamic_offsets[i];",
            "slot->offset = slot->base_offset + slot->dynamic_offset;",
            "if (binding->range == VK_WHOLE_SIZE) return available_in_buffer;",
            "VK_WHOLE_SIZE is evaluated after applying the",
            "validate_descriptor_transport_shape() derive the",
            "UINT64_MAX - slot->base_offset",
            "graphics-command-unimplemented",
            "VK_ERROR_EXTENSION_NOT_PRESENT",
            "pProperties->apiVersion > VK_API_VERSION_1_2",
        ]:
            self.assertIn(marker, icd)
        self.assertIn("VK_KHR_SWAPCHAIN_EXTENSION_NAME", icd)

    def test_vulkan_wsi_headless_and_swapchain_extensions_are_advertised(self):
        icd = VULKAN_ICD.read_text()

        instance_extension_body = c_function_body(icd, "vkEnumerateInstanceExtensionProperties")
        for marker in [
            "VK_KHR_SURFACE_EXTENSION_NAME",
            "VK_KHR_SURFACE_SPEC_VERSION",
            "VK_EXT_HEADLESS_SURFACE_EXTENSION_NAME",
            "VK_EXT_HEADLESS_SURFACE_SPEC_VERSION",
            "VK_KHR_GET_SURFACE_CAPABILITIES_2_EXTENSION_NAME",
            "VK_KHR_GET_SURFACE_CAPABILITIES_2_SPEC_VERSION",
            "copy_extension_properties",
            "available_count",
        ]:
            self.assertIn(marker, instance_extension_body)

        create_instance_body = c_function_body(icd, "vkCreateInstance")
        instance_validation_scope = create_instance_body
        if "instance_extension_advertised_name" in icd:
            instance_validation_scope += c_function_body(icd, "instance_extension_advertised_name")
        if "validate_instance_extensions" in icd:
            instance_validation_scope += c_function_body(icd, "validate_instance_extensions")
        for marker in [
            "VK_KHR_SURFACE_EXTENSION_NAME",
            "VK_EXT_HEADLESS_SURFACE_EXTENSION_NAME",
            "VK_KHR_GET_SURFACE_CAPABILITIES_2_EXTENSION_NAME",
        ]:
            self.assertIn(marker, instance_validation_scope)
        self.assertIn("VK_ERROR_EXTENSION_NOT_PRESENT", create_instance_body + instance_validation_scope)
        self.assertRegex(
            create_instance_body,
            r"(validate_instance_extensions|instance_extension_advertised_name|VK_KHR_SURFACE_EXTENSION_NAME)",
        )

        device_extension_body = c_function_body(icd, "vkEnumerateDeviceExtensionProperties")
        for marker in [
            "VK_KHR_SWAPCHAIN_EXTENSION_NAME",
            "VK_KHR_SWAPCHAIN_SPEC_VERSION",
            "ADD_DEVICE_EXTENSION",
        ]:
            self.assertIn(marker, device_extension_body)

        device_validation_body = c_function_body(icd, "device_extension_advertised_name")
        self.assertIn("VK_KHR_SWAPCHAIN_EXTENSION_NAME", device_validation_body)
        self.assertRegex(
            device_validation_body,
            r"VK_KHR_SWAPCHAIN_EXTENSION_NAME[\s\S]{0,240}return\s+(?:true|VK_TRUE|advertised_[A-Za-z0-9_]+\(\))",
        )
        self.assertIn(
            "validate_device_extensions(pCreateInfo)",
            c_function_body(icd, "vkCreateDevice"),
        )

    def test_vulkan_wsi_headless_and_swapchain_procaddr_is_not_hidden(self):
        icd = VULKAN_ICD.read_text()
        proc_gate_body = c_function_body(icd, "proc_address_hidden_by_advertisement")
        proc_body = c_function_body(icd, "proc_address")
        self.assertIn("proc_address_hidden_by_advertisement(pName)", proc_body)

        wsi_proc_names = [
            "vkCreateHeadlessSurfaceEXT",
            "vkDestroySurfaceKHR",
            "vkGetPhysicalDeviceSurfaceSupportKHR",
            "vkGetPhysicalDeviceSurfaceCapabilitiesKHR",
            "vkGetPhysicalDeviceSurfaceFormatsKHR",
            "vkGetPhysicalDeviceSurfacePresentModesKHR",
            "vkGetPhysicalDeviceSurfaceCapabilities2KHR",
            "vkGetPhysicalDeviceSurfaceFormats2KHR",
            "vkCreateSwapchainKHR",
            "vkDestroySwapchainKHR",
            "vkGetSwapchainImagesKHR",
            "vkAcquireNextImageKHR",
            "vkAcquireNextImage2KHR",
            "vkQueuePresentKHR",
        ]
        for name in wsi_proc_names:
            self.assertRegex(icd, rf"VKAPI_ATTR\s+[\w\s\*]+VKAPI_CALL\s+{name}\s*\(")
            self.assertIn(f"MAP_PROC({name});", proc_body)
            self.assertNotIn(f'strcmp(pName, "{name}") == 0', proc_gate_body)

    def test_vulkan_surface_capabilities2_wraps_headless_wsi_queries(self):
        icd = VULKAN_ICD.read_text()
        self.assertRegex(icd, r"VKAPI_ATTR\s+VkResult\s+VKAPI_CALL\s+vkGetPhysicalDeviceSurfaceCapabilities2KHR\s*\([^)]*VkPhysicalDeviceSurfaceInfo2KHR")
        self.assertRegex(icd, r"VKAPI_ATTR\s+VkResult\s+VKAPI_CALL\s+vkGetPhysicalDeviceSurfaceFormats2KHR\s*\([^)]*VkPhysicalDeviceSurfaceInfo2KHR")
        caps2_body = c_function_body(icd, "vkGetPhysicalDeviceSurfaceCapabilities2KHR")
        caps2_pnext_body = c_function_body(icd, "fill_surface_capabilities2_pnext")
        for marker in [
            "VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SURFACE_INFO_2_KHR",
            "VK_STRUCTURE_TYPE_SURFACE_CAPABILITIES_2_KHR",
            "surface-capabilities2-input-pnext-unsupported",
            "fill_surface_capabilities2_pnext(pSurfaceCapabilities->pNext)",
            "vkGetPhysicalDeviceSurfaceCapabilitiesKHR",
            "pSurfaceInfo->surface",
            "surfaceCapabilities",
        ]:
            self.assertIn(marker, caps2_body)
        for marker in [
            "VK_STRUCTURE_TYPE_SURFACE_PROTECTED_CAPABILITIES_KHR",
            "p->supportsProtected = VK_FALSE;",
            "VK_STRUCTURE_TYPE_SHARED_PRESENT_SURFACE_CAPABILITIES_KHR",
            "p->sharedPresentSupportedUsageFlags = 0;",
            "zero_vk_out_struct_preserve_chain(p, sizeof(*p), header);",
            'unsupported_create_info_pnext_result(',
        ]:
            self.assertIn(marker, caps2_pnext_body)

        formats2_body = c_function_body(icd, "vkGetPhysicalDeviceSurfaceFormats2KHR")
        for marker in [
            "VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SURFACE_INFO_2_KHR",
            "VK_STRUCTURE_TYPE_SURFACE_FORMAT_2_KHR",
            "surface-formats2-pnext-unsupported",
            "surface-formats2-output-pnext-unsupported",
            "vkGetPhysicalDeviceSurfaceFormatsKHR",
            "pSurfaceInfo->surface",
            "surfaceFormat",
        ]:
            self.assertIn(marker, formats2_body)

    def test_vulkan_headless_surface_functions_fail_closed_and_have_success_paths(self):
        icd = VULKAN_ICD.read_text()
        self.assertNotIn('trace_icd_runtime_failure("surface-unimplemented"', icd)

        create_body = c_function_body(icd, "vkCreateHeadlessSurfaceEXT")
        for marker in [
            "!pCreateInfo",
            "!pSurface",
            "VK_ERROR_INITIALIZATION_FAILED",
            "VK_ERROR_OUT_OF_HOST_MEMORY",
            "*pSurface",
            "VK_SUCCESS",
        ]:
            self.assertIn(marker, create_body)
        self.assertRegex(create_body, r"(pdocker_alloc_handle|calloc|malloc)")

        destroy_body = c_function_body(icd, "vkDestroySurfaceKHR")
        self.assertRegex(destroy_body, r"(free|pdocker_free)")

        support_body = c_function_body(icd, "vkGetPhysicalDeviceSurfaceSupportKHR")
        self.assertIn("!pSupported", support_body)
        self.assertIn("VK_ERROR_INITIALIZATION_FAILED", support_body)
        self.assertIn("VK_TRUE", support_body)
        self.assertIn("VK_SUCCESS", support_body)

        capabilities_body = c_function_body(icd, "vkGetPhysicalDeviceSurfaceCapabilitiesKHR")
        for marker in [
            "!pSurfaceCapabilities",
            "VK_ERROR_INITIALIZATION_FAILED",
            "minImageCount",
            "maxImageCount",
            "currentExtent",
            "supportedUsageFlags",
            "VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT",
            "VK_SUCCESS",
        ]:
            self.assertIn(marker, capabilities_body)

        formats_body = c_function_body(icd, "vkGetPhysicalDeviceSurfaceFormatsKHR")
        for marker in [
            "!pSurfaceFormatCount",
            "VK_ERROR_INITIALIZATION_FAILED",
            "pSurfaceFormats",
            "VK_FORMAT_",
            "VK_COLOR_SPACE_SRGB_NONLINEAR_KHR",
            "VK_SUCCESS",
        ]:
            self.assertIn(marker, formats_body)
        self.assertRegex(formats_body, r"(VK_INCOMPLETE|copy_extension_properties|memcpy)")

        present_modes_body = c_function_body(icd, "vkGetPhysicalDeviceSurfacePresentModesKHR")
        for marker in [
            "!pPresentModeCount",
            "VK_ERROR_INITIALIZATION_FAILED",
            "pPresentModes",
            "VK_PRESENT_MODE_FIFO_KHR",
            "VK_SUCCESS",
        ]:
            self.assertIn(marker, present_modes_body)
        self.assertRegex(present_modes_body, r"(VK_INCOMPLETE|memcpy|pPresentModes\[)")

    def test_vulkan_minimal_swapchain_functions_fail_closed_and_have_success_paths(self):
        icd = VULKAN_ICD.read_text()
        self.assertNotIn('trace_icd_runtime_failure("swapchain-unimplemented"', icd)

        create_body = c_function_body(icd, "vkCreateSwapchainKHR")
        swapchain_pnext_body = c_function_body(icd, "swapchain_create_pnext_noop")
        for marker in [
            "!pCreateInfo",
            "!pSwapchain",
            "VK_ERROR_INITIALIZATION_FAILED",
            "pCreateInfo->surface",
            "pCreateInfo->sType != VK_STRUCTURE_TYPE_SWAPCHAIN_CREATE_INFO_KHR",
            "!swapchain_create_pnext_noop(pCreateInfo)",
            "swapchain-pnext-unsupported",
            "pCreateInfo->flags != 0",
            "swapchain-flags-unsupported",
            "oldSwapchain",
            "pdocker_vk_headless_swapchain_valid",
            "swapchain-old-swapchain-invalid",
            "swapchain-old-surface-mismatch",
            "old_swapchain->surface != surface",
            "minImageCount",
            "imageFormat",
            "imageExtent",
            "presentMode",
            "pdocker_vk_headless_swapchain_usage_supported",
            "VK_ERROR_OUT_OF_HOST_MEMORY",
            "swapchain_owned = true",
            "*pSwapchain",
            "VK_SUCCESS",
        ]:
            self.assertIn(marker, create_body)
        for marker in [
            "VK_STRUCTURE_TYPE_DEVICE_GROUP_SWAPCHAIN_CREATE_INFO_KHR",
            "device_group->modes != VK_DEVICE_GROUP_PRESENT_MODE_LOCAL_BIT_KHR",
        ]:
            self.assertIn(marker, swapchain_pnext_body)
        self.assertRegex(create_body, r"(PdockerVkSwapchain|swapchain->)")
        self.assertNotIn("pCreateInfo->flags != 0 || pCreateInfo->oldSwapchain", create_body)
        self.assertRegex(create_body, r"(calloc|pdocker_alloc_handle|malloc)")
        usage_body = c_function_body(icd, "pdocker_vk_headless_swapchain_usage_supported")
        self.assertIn("VK_IMAGE_USAGE_SAMPLED_BIT", usage_body)
        self.assertNotIn("VK_IMAGE_USAGE_STORAGE_BIT", usage_body)
        self.assertNotIn("VK_IMAGE_USAGE_INPUT_ATTACHMENT_BIT", usage_body)
        self.assertIn("VK_IMAGE_USAGE_SAMPLED_BIT", c_function_body(icd, "vkGetPhysicalDeviceSurfaceCapabilitiesKHR"))
        self.assertIn("bool acquired[PDOCKER_VK_MAX_SWAPCHAIN_IMAGES];", icd)
        self.assertIn("bool swapchain_owned;", icd)

        destroy_body = c_function_body(icd, "vkDestroySwapchainKHR")
        self.assertRegex(destroy_body, r"(free|pdocker_free)")
        self.assertIn("pdocker_vk_destroy_swapchain_images", destroy_body)
        destroy_image_body = c_function_body(icd, "vkDestroyImage")
        self.assertIn("swapchain_owned", destroy_image_body)
        self.assertIn("swapchain-image-destroy-ignored", destroy_image_body)
        bind_image_body = c_function_body(icd, "vkBindImageMemory")
        self.assertIn("swapchain_owned", bind_image_body)
        self.assertIn("swapchain-image-bind-rejected", bind_image_body)

        runtime_valid_body = c_function_body(icd, "pdocker_vk_headless_swapchain_runtime_valid")
        for marker in [
            "pdocker_vk_headless_swapchain_valid",
            "swapchain->images[i]",
            "swapchain->memories[i]",
            "swapchain_owned",
            "image->memory != memory",
        ]:
            self.assertIn(marker, runtime_valid_body)

        present_image_body = c_function_body(icd, "pdocker_vk_present_image_result")
        for marker in [
            "pdocker_vk_swapchain_image_index_valid",
            "swapchain->acquired",
            "layout_mixed",
            "layout_range_overflow",
            "VK_IMAGE_LAYOUT_PRESENT_SRC_KHR",
            "queue-present-image-layout-not-present-src",
            "VK_ERROR_OUT_OF_DATE_KHR",
            "VK_NOT_READY",
        ]:
            self.assertIn(marker, present_image_body)

        duplicate_body = c_function_body(icd, "pdocker_vk_present_target_duplicate")
        for marker in [
            "target_index >= present_info->swapchainCount",
            "present_info->pSwapchains[i] == swapchain",
            "present_info->pImageIndices[i] == image_index",
        ]:
            self.assertIn(marker, duplicate_body)

        get_images_body = c_function_body(icd, "vkGetSwapchainImagesKHR")
        for marker in [
            "!pSwapchainImageCount",
            "VK_ERROR_INITIALIZATION_FAILED",
            "pdocker_vk_headless_swapchain_runtime_valid",
            "swapchain-images-invalid",
            "VK_ERROR_OUT_OF_DATE_KHR",
            "pSwapchainImages",
            "VK_INCOMPLETE",
            "VK_SUCCESS",
        ]:
            self.assertIn(marker, get_images_body)
        self.assertRegex(get_images_body, r"(memcpy|pSwapchainImages\[)")

        acquire_body = c_function_body(icd, "vkAcquireNextImageKHR")
        for marker in [
            "!pImageIndex",
            "VK_ERROR_INITIALIZATION_FAILED",
            "pdocker_vk_headless_swapchain_runtime_valid",
            "acquire-next-image-swapchain-invalid",
            "pdocker_vk_acquire_sync_valid",
            "acquire-next-image-sync-invalid",
            "semaphore_complete_signal",
            "VK_ERROR_OUT_OF_DATE_KHR",
            "*pImageIndex",
            "sc->acquired[index] = true",
            "VK_NOT_READY",
            "VK_TIMEOUT",
            "VK_SUCCESS",
        ]:
            self.assertIn(marker, acquire_body)
        self.assertRegex(acquire_body, r"(swapchain|current_image|next_image|image_index)")

        acquire2_body = c_function_body(icd, "vkAcquireNextImage2KHR")
        acquire2_pnext_body = c_function_body(icd, "acquire_next_image2_pnext_noop")
        for marker in [
            "!pAcquireInfo",
            "VK_ERROR_INITIALIZATION_FAILED",
            "pAcquireInfo->swapchain",
            "pImageIndex",
            "!acquire_next_image2_pnext_noop(pAcquireInfo)",
            "acquire-next-image2-pnext-unsupported",
        ]:
            self.assertIn(marker, acquire2_body)
        self.assertIn("Vulkan has no separate VkDeviceGroupAcquireNextImageInfoKHR", acquire2_pnext_body)
        self.assertIn("return !info || !info->pNext;", acquire2_pnext_body)
        self.assertIn("pAcquireInfo->deviceMask != 0 && pAcquireInfo->deviceMask != 1u", acquire2_body)
        self.assertRegex(acquire2_body, r"(vkAcquireNextImageKHR|VK_SUCCESS|\*pImageIndex)")

        present_body = c_function_body(icd, "vkQueuePresentKHR")
        present_pnext_body = c_function_body(icd, "present_info_pnext_noop")
        for marker in [
            "!pPresentInfo",
            "VK_ERROR_INITIALIZATION_FAILED",
            "pPresentInfo->sType != VK_STRUCTURE_TYPE_PRESENT_INFO_KHR",
            "!present_info_pnext_noop(pPresentInfo)",
            "queue-present-pnext-unsupported",
            "queue-present-wait-semaphore-unsignaled",
            "semaphore_wait_satisfied",
            "swapchainCount",
            "swapchainCount == 0",
            "pSwapchains",
            "pdocker_vk_present_image_result",
            "pdocker_vk_present_target_duplicate",
            "queue-present-duplicate-target",
            "if (aggregate != VK_SUCCESS) return aggregate",
            "semaphore_complete_wait",
            "sc->acquired[image_index] = false",
            "VK_NOT_READY",
            "VK_SUCCESS",
        ]:
            self.assertIn(marker, present_body)
        for marker in [
            "VK_STRUCTURE_TYPE_DEVICE_GROUP_PRESENT_INFO_KHR",
            "device_group->swapchainCount != info->swapchainCount",
            "device_group->swapchainCount > 0 && !device_group->pDeviceMasks",
            "device_group->mode != VK_DEVICE_GROUP_PRESENT_MODE_LOCAL_BIT_KHR",
            "device_group->pDeviceMasks[i] != 0",
        ]:
            self.assertIn(marker, present_pnext_body)
        self.assertRegex(present_body, r"(pResults|swapchainCount)")
        self.assertLess(
            present_body.index("pdocker_vk_present_target_duplicate"),
            present_body.index("semaphore_complete_wait"),
        )
        self.assertLess(
            present_body.index("pdocker_vk_present_target_duplicate"),
            present_body.index("sc->acquired[image_index] = false"),
        )

    def test_vulkan_headless_present_layout_is_not_replayed_raw_to_executor(self):
        executor = GPU_EXECUTOR.read_text()
        helper_body = c_function_body(executor, "vulkan_replay_layout_for_executor")
        self.assertIn("VK_IMAGE_LAYOUT_PRESENT_SRC_KHR", helper_body)
        self.assertIn("VK_IMAGE_LAYOUT_GENERAL", helper_body)

        materialize_ranges_body = c_function_body(executor, "materialize_vulkan_graphics_v620_image_layout_ranges")
        self.assertIn("vulkan_replay_layout_for_executor((VkImageLayout)src->layout)", materialize_ranges_body)

        planning_body = c_function_body(executor, "vulkan_graphics_planning_set_image_layout")
        self.assertIn("vulkan_replay_layout_for_executor(layout)", planning_body)

        set_layout_body = c_function_body(executor, "vulkan_replay_image_set_layout_for_range")
        self.assertIn("layout = vulkan_replay_layout_for_executor(layout);", set_layout_body)

        recorder_body = c_function_body(executor, "record_vulkan_graphics_v6_command_buffer")
        dependency_helper_body = c_function_body(executor, "collect_vulkan_graphics_v6_dependency_barriers")
        for marker in [
            "vulkan_replay_layout_for_executor((VkImageLayout)barrier->old_layout)",
            "vulkan_replay_layout_for_executor((VkImageLayout)barrier->new_layout)",
        ]:
            self.assertIn(marker, dependency_helper_body)
        for marker in [
            "vulkan_replay_layout_for_executor((VkImageLayout)copy->image_layout)",
            "vulkan_replay_layout_for_executor((VkImageLayout)clear->image_layout)",
        ]:
            self.assertIn(marker, recorder_body)

    def test_vulkan_render_pass_captures_subpass_attachment_refs(self):
        icd = VULKAN_ICD.read_text()
        for marker in [
            "typedef struct {\n    uint32_t color_attachment_count;",
            "uint32_t color_attachments[PDOCKER_VK_MAX_STORAGE_BUFFERS];",
            "VkImageLayout color_layouts[PDOCKER_VK_MAX_STORAGE_BUFFERS];",
            "uint32_t resolve_attachments[PDOCKER_VK_MAX_STORAGE_BUFFERS];",
            "VkImageLayout resolve_layouts[PDOCKER_VK_MAX_STORAGE_BUFFERS];",
            "bool has_depth_stencil_attachment;",
            "uint32_t depth_stencil_attachment;",
            "VkImageLayout depth_stencil_layout;",
            "bool has_depth_stencil_resolve_attachment;",
            "uint32_t depth_stencil_resolve_attachment;",
            "VkImageLayout depth_stencil_resolve_layout;",
            "VkResolveModeFlagBits depth_resolve_mode;",
            "VkResolveModeFlagBits stencil_resolve_mode;",
            "uint32_t view_mask;",
            "PdockerVkSubpassState subpasses[PDOCKER_VK_MAX_STORAGE_BUFFERS];",
            "bool subpass_overflow;",
            "capture_render_pass_subpass_state(",
            "capture_render_pass_subpass_state2(",
            "src->pColorAttachments",
            "src->pResolveAttachments",
            "src->pDepthStencilAttachment",
            "src->inputAttachmentCount",
            "src->preserveAttachmentCount",
            "subpass->pColorAttachments",
            "subpass->pResolveAttachments",
            "subpass->pDepthStencilAttachment",
            "subpass->inputAttachmentCount",
            "subpass->preserveAttachmentCount",
            "subpass->viewMask",
            "dst->view_mask = view_mask;",
            "input_attachment_count != 0 || preserve_attachment_count != 0",
            "append_render_pass_subpass_layout_transitions",
            "append_render_pass_final_layout_transitions",
            "record_render_pass_attachment_transition",
            "view->image->layout_mixed",
            "capture_render_pass_dependencies(",
            "capture_render_pass_dependencies2(",
            "capture_single_subpass_dependency",
            "VK_SUBPASS_EXTERNAL && dst_subpass == 0",
            "src_subpass != VK_SUBPASS_EXTERNAL && dst_subpass == VK_SUBPASS_EXTERNAL",
            "src_subpass + 1u == dst_subpass",
            "rp->subpass_dependencies[dst_subpass]",
            "render_pass_create2_pnext_noop(pCreateInfo, &disallow_subpass_merging)",
            "fill_render_pass_create2_feedback(pCreateInfo, pCreateInfo->subpassCount)",
            "VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SUBPASS_MERGE_FEEDBACK_FEATURES_EXT",
            "VK_STRUCTURE_TYPE_RENDER_PASS_CREATION_CONTROL_EXT",
            "VK_STRUCTURE_TYPE_RENDER_PASS_CREATION_FEEDBACK_CREATE_INFO_EXT",
            "feedback->pRenderPassFeedback->postMergeSubpassCount",
            "src->flags != 0",
            "const VkSubpassDescriptionDepthStencilResolve *depth_stencil_resolve = NULL;",
            "const VkRenderPassSubpassFeedbackCreateInfoEXT *subpass_feedback = NULL;",
            "VK_STRUCTURE_TYPE_SUBPASS_DESCRIPTION_DEPTH_STENCIL_RESOLVE",
            "depth_stencil_resolve->pDepthStencilResolveAttachment",
            "depth_stencil_resolve->depthResolveMode",
            "depth_stencil_resolve->stencilResolveMode",
            "VK_STRUCTURE_TYPE_RENDER_PASS_SUBPASS_FEEDBACK_CREATE_INFO_EXT",
            "feedback->subpassMergeStatus = VK_SUBPASS_MERGE_STATUS_DISALLOWED_EXT",
            "feedback->subpassMergeStatus = VK_SUBPASS_MERGE_STATUS_NOT_MERGED_SINGLE_SUBPASS_EXT",
            "feedback->postMergeIndex = subpass_index",
            "subpass->pColorAttachments[i].pNext",
            "subpass->pColorAttachments[i].aspectMask != 0",
            "subpass->pResolveAttachments[i].pNext",
            "subpass->pResolveAttachments[i].aspectMask != 0",
            "subpass->pDepthStencilAttachment->pNext",
            "subpass->pDepthStencilAttachment->aspectMask != 0",
            "src->pNext || src->flags != 0",
            "dst->unsupported = true;",
            "pdocker_vk_format_is_depth_stencil",
        ]:
            self.assertIn(marker, icd)

    def test_vulkan_command_time_pnext_is_fail_closed(self):
        icd = VULKAN_ICD.read_text()
        copy_body = icd.split(
            "static bool copy_rendering_attachment_state", 1
        )[1].split("static bool append_graphics_rendering_snapshot", 1)[0]
        begin_rendering_body = icd.split(
            "VKAPI_ATTR void VKAPI_CALL vkCmdBeginRendering", 1
        )[1].split("VKAPI_ATTR void VKAPI_CALL vkCmdEndRendering", 1)[0]
        rendering_info_pnext_body = c_function_body(icd, "rendering_info_pnext_noop")
        device_group_begin_pnext_body = c_function_body(icd, "device_group_render_pass_begin_noop")
        begin_render_pass_body = icd.split(
            "VKAPI_ATTR void VKAPI_CALL vkCmdBeginRenderPass", 1
        )[1].split("VKAPI_ATTR void VKAPI_CALL vkCmdNextSubpass", 1)[0]
        render_pass_begin_pnext_body = c_function_body(icd, "render_pass_begin_pnext_noop")
        render_pass2_body = icd.split(
            "VKAPI_ATTR void VKAPI_CALL vkCmdBeginRenderPass2", 1
        )[1].split("static void record_vertex_buffer_bindings", 1)[0]
        for marker in [
            "if (src->pNext) return false;",
            "if (!src) return true;",
        ]:
            self.assertIn(marker, copy_body)
        for marker in [
            "if (!rendering_info_pnext_noop(pRenderingInfo))",
            "cmd->graphics_unsupported = true;",
            "if (!copy_rendering_attachment_state(&cmd->active_color_attachments[i]",
            "if (!copy_rendering_attachment_state(&cmd->active_depth_attachment",
            "if (!copy_rendering_attachment_state(&cmd->active_stencil_attachment",
        ]:
            self.assertIn(marker, begin_rendering_body)
        self.assertIn("VK_STRUCTURE_TYPE_DEVICE_GROUP_RENDER_PASS_BEGIN_INFO", rendering_info_pnext_body)
        self.assertIn("device_group_render_pass_begin_noop(", rendering_info_pnext_body)
        for marker in [
            "info->deviceMask != 0 && info->deviceMask != 1u",
            "info->deviceRenderAreaCount == 0 && !info->pDeviceRenderAreas",
            "info->deviceRenderAreaCount != 1u",
            "area->extent.width == render_area->extent.width",
        ]:
            self.assertIn(marker, device_group_begin_pnext_body)
        self.assertIn("if (!render_pass_begin_pnext_noop(pRenderPassBegin))", begin_render_pass_body)
        for marker in [
            "VK_STRUCTURE_TYPE_DEVICE_GROUP_RENDER_PASS_BEGIN_INFO",
            "device_group_render_pass_begin_noop(info, begin ? &begin->renderArea : NULL)",
            "VK_STRUCTURE_TYPE_RENDER_PASS_ATTACHMENT_BEGIN_INFO",
            "info->attachmentCount != 0 || info->pAttachments",
            "VK_STRUCTURE_TYPE_RENDER_PASS_SAMPLE_LOCATIONS_BEGIN_INFO_EXT",
            "info->attachmentInitialSampleLocationsCount != 0",
            "info->postSubpassSampleLocationsCount != 0",
        ]:
            self.assertIn(marker, render_pass_begin_pnext_body)
        for marker in [
            "pSubpassBeginInfo && pSubpassBeginInfo->pNext",
            "pSubpassEndInfo && pSubpassEndInfo->pNext",
            "vkCmdNextSubpass(commandBuffer, pSubpassBeginInfo",
        ]:
            self.assertIn(marker, render_pass2_body)

    def test_vulkan_begin_render_pass_normalizes_subpass_to_dynamic_rendering(self):
        icd = VULKAN_ICD.read_text()
        normalize_body = icd.split(
            "static bool populate_render_pass_subpass_rendering_state", 1
        )[1].split("VKAPI_ATTR void VKAPI_CALL vkCmdBeginRenderPass", 1)[0]
        begin_body = icd.split(
            "VKAPI_ATTR void VKAPI_CALL vkCmdBeginRenderPass", 1
        )[1].split("VKAPI_ATTR void VKAPI_CALL vkCmdNextSubpass", 1)[0]
        for marker in [
            "populate_render_pass_subpass_rendering_state",
            "contents != VK_SUBPASS_CONTENTS_INLINE",
            "render_pass_subpass_can_normalize_to_dynamic_rendering(rp, subpass_index)",
            "cmd->active_color_attachment_count = subpass->color_attachment_count;",
            "cmd->active_color_attachments[c]",
            "cmd->active_color_attachments[c].resolve_mode = VK_RESOLVE_MODE_AVERAGE_BIT;",
            "cmd->active_color_attachments[c].resolve_image_layout = subpass->resolve_layouts[c];",
            "cmd->active_depth_attachment",
            "cmd->active_stencil_attachment",
            "subpass->has_depth_stencil_resolve_attachment",
            "cmd->active_depth_attachment.resolve_image_view",
            "cmd->active_stencil_attachment.resolve_image_view",
            "cmd->active_rendering_layer_count = fb->layers ? fb->layers : 1;",
            "cmd->active_rendering_view_mask = subpass->view_mask;",
            "append_render_pass_subpass_layout_transitions(cmd, 0, &rp->begin_dependency)",
            "append_graphics_begin_rendering_command(cmd)",
            "record.command_type = PDOCKER_GPU_GRAPHICS_V6_COMMAND_BEGIN_RENDERING;",
            "record.rendering_snapshot_index = rendering_snapshot_index;",
            "cmd->dynamic_rendering_active = true;",
            "cmd->render_pass_active = false;",
        ]:
            self.assertIn(marker, normalize_body)
        self.assertIn("append_normalized_render_pass_begin(cmd, pRenderPassBegin, contents)", begin_body)
        self.assertNotIn("record.rendering_snapshot_index = UINT32_MAX;", begin_body)

    def test_vulkan_render_pass_normalization_synthesizes_layout_barriers(self):
        icd = VULKAN_ICD.read_text()
        transition_body = icd.split(
            "static bool record_render_pass_attachment_transition", 1
        )[1].split("static bool render_pass_track_attachment_layout", 1)[0]
        subpass_transition_body = icd.split(
            "static bool append_render_pass_subpass_layout_transitions", 1
        )[1].split("static bool append_render_pass_final_layout_transitions", 1)[0]
        final_transition_body = icd.split(
            "static bool append_render_pass_final_layout_transitions", 1
        )[1].split("static bool populate_render_pass_subpass_rendering_state", 1)[0]
        normalize_gate = icd.split(
            "static bool render_pass_subpass_can_normalize_to_dynamic_rendering", 1
        )[1].split("VKAPI_ATTR VkResult VKAPI_CALL vkCreateRenderPass", 1)[0]
        for marker in [
            "view->image->layout_mixed",
            "record_image_barrier_op((VkCommandBuffer)cmd",
            "VK_QUEUE_FAMILY_IGNORED",
            "cmd->image_barrier_op_count == before + 1u",
        ]:
            self.assertIn(marker, transition_body)
        for marker in [
            "cmd->active_render_pass_attachment_seen[color_index]",
            "rp->attachments[color_index].initial_layout",
            "render_pass_begin_src_access_mask(old_layout)",
            "render_pass_begin_src_stage_mask(old_layout)",
            "render_pass_attachment_access_mask(false, false)",
            "render_pass_resolve_attachment_access_mask()",
            "attachment->resolve_image_layout",
            "subpass->depth_stencil_resolve_layout",
            "cmd->active_depth_attachment.resolve_image_view",
            "render_pass_layout_is_read_only(cmd->active_depth_attachment.image_layout)",
            "dependency && dependency->seen",
            "record_memory_barrier_op((VkCommandBuffer)cmd",
            "render_pass_track_attachment_layout",
            "append_graphics_barrier_record_for_ranges",
        ]:
            self.assertIn(marker, subpass_transition_body)
        for marker in [
            "rp->attachments[a].final_layout",
            "cmd->active_render_pass_attachment_seen[a]",
            "cmd->active_render_pass_attachment_layouts[a]",
            "cmd->active_render_pass_attachment_views[a]",
            "rp->end_dependency.seen",
            "record_memory_barrier_op((VkCommandBuffer)cmd",
            "append_graphics_barrier_record_for_ranges",
        ]:
            self.assertIn(marker, final_transition_body)
        for marker in [
            "rp->attachment_overflow",
            "rp->subpass_overflow",
            "subpass_index >= rp->subpass_count",
            "return !subpass->unsupported;",
        ]:
            self.assertIn(marker, normalize_gate)
        self.assertNotIn("rp->subpass_count != 1", normalize_gate)

    def test_vulkan_next_subpass_normalizes_to_end_barrier_begin_sequence(self):
        icd = VULKAN_ICD.read_text()
        next_body = icd.split(
            "VKAPI_ATTR void VKAPI_CALL vkCmdNextSubpass", 1
        )[1].split("VKAPI_ATTR void VKAPI_CALL vkCmdEndRenderPass", 1)[0]
        for marker in [
            "uint32_t next_subpass = cmd->active_subpass + 1u;",
            "!render_pass_subpass_can_normalize_to_dynamic_rendering(rp, next_subpass)",
            "append_graphics_end_rendering_command(cmd)",
            "populate_render_pass_subpass_rendering_state(",
            "append_render_pass_subpass_layout_transitions(",
            "&rp->subpass_dependencies[next_subpass]",
            "append_graphics_begin_rendering_command(cmd)",
            "cmd->active_subpass = next_subpass;",
            "cmd->active_subpass_contents = contents;",
        ]:
            self.assertIn(marker, next_body)
        self.assertIn("vkCmdNextSubpass(commandBuffer, pSubpassBeginInfo", icd)

    def test_vulkan_render_pass_pipeline_formats_are_completed_from_attachment_refs(self):
        icd = VULKAN_ICD.read_text()
        pipeline_body = icd.split(
            "VKAPI_ATTR VkResult VKAPI_CALL vkCreateGraphicsPipelines", 1
        )[1].split("VKAPI_ATTR void VKAPI_CALL vkDestroyPipeline", 1)[0]
        for marker in [
            "if (!pipeline->dynamic_rendering_pipeline && pipeline->render_pass) {",
            "render_pass_subpass_can_normalize_to_dynamic_rendering(rp, ci->subpass)",
            "pipeline->dynamic_rendering_pipeline = true;",
            "pipeline->dynamic_rendering_view_mask = subpass->view_mask;",
            "pipeline->dynamic_rendering_color_attachment_count = subpass->color_attachment_count;",
            "uint32_t attachment = subpass->color_attachments[c];",
            "pipeline->dynamic_rendering_color_formats[c] =",
            "rp->attachments[attachment].format",
            "VkFormat ds_format = rp->attachments[subpass->depth_stencil_attachment].format;",
            "pipeline->dynamic_rendering_depth_format =",
            "pdocker_vk_format_has_depth(ds_format) ? ds_format : VK_FORMAT_UNDEFINED;",
            "pipeline->dynamic_rendering_stencil_format =",
            "pdocker_vk_format_has_stencil(ds_format) ? ds_format : VK_FORMAT_UNDEFINED;",
        ]:
            self.assertIn(marker, pipeline_body)

    def test_vulkan_graphics_pipeline_unsupported_metadata_is_fail_closed(self):
        icd = VULKAN_ICD.read_text()
        pipeline_body = icd.split(
            "VKAPI_ATTR VkResult VKAPI_CALL vkCreateGraphicsPipelines", 1
        )[1].split("VKAPI_ATTR void VKAPI_CALL vkDestroyPipeline", 1)[0]
        for marker in [
            "ci->flags != 0 || ci->basePipelineHandle != VK_NULL_HANDLE",
            "ci->basePipelineIndex >= 0",
            "ci->pDynamicState->pNext || ci->pDynamicState->flags != 0",
            "ci->stageCount > 0 && !ci->pStages",
            "!stage || stage->pNext || stage->flags != 0",
            "ci->pInputAssemblyState->pNext || ci->pInputAssemblyState->flags != 0",
            "ci->pRasterizationState->pNext || ci->pRasterizationState->flags != 0",
            "ms->pNext || ms->flags != 0",
            "cb->pNext || cb->flags != 0",
            "ds->pNext || ds->flags != 0",
            "vs->pNext || vs->flags != 0",
            "ci->pVertexInputState->pNext || ci->pVertexInputState->flags != 0",
            "rendering->colorAttachmentCount > 0 && !rendering->pColorAttachmentFormats",
            "pipeline->shader_stage_flags & (VK_SHADER_STAGE_TESSELLATION_CONTROL_BIT |",
            "VK_SHADER_STAGE_TESSELLATION_EVALUATION_BIT",
        ]:
            self.assertIn(marker, pipeline_body)
        self.assertIn("""} else {
                pipeline->graphics_unsupported = true;
            }""", pipeline_body)


    def test_vulkan_graphics_executor_replays_dynamic_states(self):
        executor = GPU_EXECUTOR.read_text()
        for marker in [
            "VkPhysicalDeviceExtendedDynamicStateFeaturesEXT physical_extended_dynamic_state",
            "VK_EXT_EXTENDED_DYNAMIC_STATE_EXTENSION_NAME",
            "cmd_set_cull_mode",
            "cmd_set_front_face",
            "cmd_set_primitive_topology",
            "cmd_set_rasterizer_discard_enable",
            "cmd_set_depth_bias_enable",
            "cmd_set_primitive_restart_enable",
            "cmd_set_logic_op",
            "cmd_set_patch_control_points",
            "cmd_set_depth_test_enable",
            "cmd_set_depth_write_enable",
            "cmd_set_depth_compare_op",
            "cmd_set_depth_bounds_test_enable",
            "cmd_set_stencil_test_enable",
            "cmd_set_stencil_op",
            "vulkan_graphics_dynamic_state_bit(VK_DYNAMIC_STATE_CULL_MODE)",
            "vulkan_graphics_dynamic_state_bit(VK_DYNAMIC_STATE_FRONT_FACE)",
            "vulkan_graphics_dynamic_state_bit(VK_DYNAMIC_STATE_PRIMITIVE_TOPOLOGY)",
            "vulkan_graphics_dynamic_state_bit(VK_DYNAMIC_STATE_RASTERIZER_DISCARD_ENABLE)",
            "vulkan_graphics_dynamic_state_bit(VK_DYNAMIC_STATE_DEPTH_BIAS_ENABLE)",
            "vulkan_graphics_dynamic_state_bit(VK_DYNAMIC_STATE_PRIMITIVE_RESTART_ENABLE)",
            "vulkan_graphics_dynamic_state_bit(VK_DYNAMIC_STATE_LOGIC_OP_EXT)",
            "vulkan_graphics_dynamic_state_bit(VK_DYNAMIC_STATE_PATCH_CONTROL_POINTS_EXT)",
            "vulkan_graphics_dynamic_state_bit(VK_DYNAMIC_STATE_DEPTH_BIAS)",
            "vulkan_graphics_dynamic_state_bit(VK_DYNAMIC_STATE_BLEND_CONSTANTS)",
            "vulkan_graphics_dynamic_state_bit(VK_DYNAMIC_STATE_DEPTH_BOUNDS)",
            "vulkan_graphics_dynamic_state_bit(VK_DYNAMIC_STATE_DEPTH_BOUNDS_TEST_ENABLE)",
            "vulkan_graphics_dynamic_state_bit(VK_DYNAMIC_STATE_STENCIL_COMPARE_MASK)",
            "vulkan_graphics_dynamic_state_bit(VK_DYNAMIC_STATE_STENCIL_WRITE_MASK)",
            "vulkan_graphics_dynamic_state_bit(VK_DYNAMIC_STATE_STENCIL_REFERENCE)",
            "vulkan_graphics_dynamic_state_bit(VK_DYNAMIC_STATE_DEPTH_TEST_ENABLE)",
            "vulkan_graphics_dynamic_state_bit(VK_DYNAMIC_STATE_DEPTH_WRITE_ENABLE)",
            "vulkan_graphics_dynamic_state_bit(VK_DYNAMIC_STATE_DEPTH_COMPARE_OP)",
            "vulkan_graphics_dynamic_state_bit(VK_DYNAMIC_STATE_STENCIL_TEST_ENABLE)",
            "vulkan_graphics_dynamic_state_bit(VK_DYNAMIC_STATE_STENCIL_OP)",
            "sizeof(VkCullModeFlags)",
            "sizeof(VkFrontFace)",
            "sizeof(VkPrimitiveTopology)",
            "sizeof(VkLogicOp)",
            "VK_DYNAMIC_STATE_PATCH_CONTROL_POINTS_EXT",
            "sizeof(float) * 3u",
            "sizeof(float) * 4u",
            "sizeof(uint32_t) * 5u",
            "vkCmdSetLineWidth(command_buffer, line_width);",
            "vkCmdSetDepthBias(command_buffer",
            "vkCmdSetBlendConstants(command_buffer",
            "vkCmdSetDepthBounds(command_buffer",
            "rt->cmd_set_depth_bounds_test_enable(command_buffer, value);",
            "vkCmdSetStencilCompareMask(command_buffer",
            "vkCmdSetStencilWriteMask(command_buffer",
            "vkCmdSetStencilReference(command_buffer",
            "rt->cmd_set_cull_mode(command_buffer, value);",
            "rt->cmd_set_front_face(command_buffer, value);",
            "rt->cmd_set_primitive_topology(command_buffer, value);",
            "rt->cmd_set_rasterizer_discard_enable(command_buffer, value);",
            "rt->cmd_set_depth_bias_enable(command_buffer, value);",
            "rt->cmd_set_primitive_restart_enable(command_buffer, value);",
            "rt->cmd_set_logic_op(command_buffer, value);",
            "rt->cmd_set_patch_control_points(command_buffer, value);",
            "rt->cmd_set_depth_test_enable(command_buffer, value);",
            "rt->cmd_set_depth_write_enable(command_buffer, value);",
            "rt->cmd_set_depth_compare_op(command_buffer, value);",
            "rt->cmd_set_stencil_test_enable(command_buffer, value);",
            "rt->cmd_set_stencil_op(command_buffer",
            "vulkan_graphics_stencil_face_mask_supported",
        ]:
            self.assertIn(marker, executor)
        self.assertNotIn("only viewport/scissor dynamic state replay is implemented first", executor)
        self.assertNotIn("VkDynamicState dynamic_states[2]", executor)

    def test_vulkan_graphics_dynamic_state_bit_mapping_matches_icd(self):
        executor = GPU_EXECUTOR.read_text()
        icd = VULKAN_ICD.read_text()
        states = [
            "VIEWPORT", "SCISSOR", "LINE_WIDTH", "CULL_MODE", "FRONT_FACE",
            "PRIMITIVE_TOPOLOGY", "DEPTH_BIAS", "BLEND_CONSTANTS",
            "DEPTH_BOUNDS", "STENCIL_COMPARE_MASK", "STENCIL_WRITE_MASK",
            "STENCIL_REFERENCE", "DEPTH_TEST_ENABLE", "DEPTH_WRITE_ENABLE",
            "DEPTH_COMPARE_OP", "STENCIL_TEST_ENABLE", "STENCIL_OP",
            "DEPTH_BOUNDS_TEST_ENABLE",
        ]
        for bit, name in enumerate(states):
            marker = f"case VK_DYNAMIC_STATE_{name}: return {bit}u;"
            self.assertIn(marker, executor)
            self.assertIn(marker, icd)
        for marker in [
            "case VK_DYNAMIC_STATE_VIEWPORT_WITH_COUNT: return 18u;",
            "case VK_DYNAMIC_STATE_SCISSOR_WITH_COUNT: return 19u;",
            "case VK_DYNAMIC_STATE_RASTERIZER_DISCARD_ENABLE: return 20u;",
            "case VK_DYNAMIC_STATE_DEPTH_BIAS_ENABLE: return 21u;",
            "case VK_DYNAMIC_STATE_PRIMITIVE_RESTART_ENABLE: return 22u;",
            "case VK_DYNAMIC_STATE_LOGIC_OP_EXT: return 23u;",
            "case VK_DYNAMIC_STATE_PATCH_CONTROL_POINTS_EXT: return 24u;",
            "VK_DYNAMIC_STATE_VIEWPORT_WITH_COUNT,",
            "VK_DYNAMIC_STATE_SCISSOR_WITH_COUNT,",
            "pViewports ? (size_t)viewportCount * sizeof(VkViewport) : 0",
            "pScissors ? (size_t)scissorCount * sizeof(VkRect2D) : 0",
            "pdocker_vk_graphics_dynamic_state_bit(VK_DYNAMIC_STATE_VIEWPORT_WITH_COUNT)",
            "pdocker_vk_graphics_dynamic_state_bit(VK_DYNAMIC_STATE_SCISSOR_WITH_COUNT)",
            "const uint64_t viewport_dynamic_bits =",
            "const uint64_t scissor_dynamic_bits =",
            "(pipeline->dynamic_state_mask & viewport_dynamic_bits) != 0;",
            "(pipeline->dynamic_state_mask & scissor_dynamic_bits) != 0;",
        ]:
            self.assertIn(marker, icd)
        self.assertNotIn("vkCmdSetViewport(commandBuffer, 0, viewportCount, pViewports);", icd)
        self.assertNotIn("vkCmdSetScissor(commandBuffer, 0, scissorCount, pScissors);", icd)
        for marker in [
            "case VK_DYNAMIC_STATE_VIEWPORT_WITH_COUNT: return 18u;",
            "case VK_DYNAMIC_STATE_SCISSOR_WITH_COUNT: return 19u;",
            "case VK_DYNAMIC_STATE_RASTERIZER_DISCARD_ENABLE: return 20u;",
            "case VK_DYNAMIC_STATE_DEPTH_BIAS_ENABLE: return 21u;",
            "case VK_DYNAMIC_STATE_PRIMITIVE_RESTART_ENABLE: return 22u;",
            "case VK_DYNAMIC_STATE_LOGIC_OP_EXT: return 23u;",
            "case VK_DYNAMIC_STATE_PATCH_CONTROL_POINTS_EXT: return 24u;",
            "PFN_vkCmdSetViewportWithCountEXT cmd_set_viewport_with_count;",
            "PFN_vkCmdSetScissorWithCountEXT cmd_set_scissor_with_count;",
            "PFN_vkCmdSetRasterizerDiscardEnableEXT cmd_set_rasterizer_discard_enable;",
            "PFN_vkCmdSetDepthBiasEnableEXT cmd_set_depth_bias_enable;",
            "PFN_vkCmdSetPrimitiveRestartEnableEXT cmd_set_primitive_restart_enable;",
            "PFN_vkCmdSetLogicOpEXT cmd_set_logic_op;",
            "PFN_vkCmdSetPatchControlPointsEXT cmd_set_patch_control_points;",
            'vkGetDeviceProcAddr(rt->device, "vkCmdSetViewportWithCount")',
            'vkGetDeviceProcAddr(rt->device, "vkCmdSetViewportWithCountEXT")',
            'vkGetDeviceProcAddr(rt->device, "vkCmdSetScissorWithCount")',
            'vkGetDeviceProcAddr(rt->device, "vkCmdSetScissorWithCountEXT")',
            'vkGetDeviceProcAddr(rt->device, "vkCmdSetRasterizerDiscardEnable")',
            'vkGetDeviceProcAddr(rt->device, "vkCmdSetRasterizerDiscardEnableEXT")',
            'vkGetDeviceProcAddr(rt->device, "vkCmdSetDepthBiasEnable")',
            'vkGetDeviceProcAddr(rt->device, "vkCmdSetDepthBiasEnableEXT")',
            'vkGetDeviceProcAddr(rt->device, "vkCmdSetPrimitiveRestartEnable")',
            'vkGetDeviceProcAddr(rt->device, "vkCmdSetPrimitiveRestartEnableEXT")',
            'vkGetDeviceProcAddr(rt->device, "vkCmdSetLogicOpEXT")',
            'vkGetDeviceProcAddr(rt->device, "vkCmdSetPatchControlPointsEXT")',
            "case VK_DYNAMIC_STATE_VIEWPORT_WITH_COUNT:",
            "rt->cmd_set_viewport_with_count(command_buffer, state->count",
            "case VK_DYNAMIC_STATE_SCISSOR_WITH_COUNT:",
            "rt->cmd_set_scissor_with_count(command_buffer, state->count",
            "case VK_DYNAMIC_STATE_RASTERIZER_DISCARD_ENABLE:",
            "rt->cmd_set_rasterizer_discard_enable(command_buffer, value);",
            "case VK_DYNAMIC_STATE_DEPTH_BIAS_ENABLE:",
            "rt->cmd_set_depth_bias_enable(command_buffer, value);",
            "case VK_DYNAMIC_STATE_PRIMITIVE_RESTART_ENABLE:",
            "rt->cmd_set_primitive_restart_enable(command_buffer, value);",
            "case VK_DYNAMIC_STATE_LOGIC_OP_EXT:",
            "rt->cmd_set_logic_op(command_buffer, value);",
            "case VK_DYNAMIC_STATE_PATCH_CONTROL_POINTS_EXT:",
            "rt->cmd_set_patch_control_points(command_buffer, value);",
            "vulkan_graphics_dynamic_state_bit(VK_DYNAMIC_STATE_VIEWPORT_WITH_COUNT)",
            "vulkan_graphics_dynamic_state_bit(VK_DYNAMIC_STATE_SCISSOR_WITH_COUNT)",
            "vulkan_graphics_dynamic_state_bit(VK_DYNAMIC_STATE_RASTERIZER_DISCARD_ENABLE)",
            "vulkan_graphics_dynamic_state_bit(VK_DYNAMIC_STATE_DEPTH_BIAS_ENABLE)",
            "vulkan_graphics_dynamic_state_bit(VK_DYNAMIC_STATE_PRIMITIVE_RESTART_ENABLE)",
            "vulkan_graphics_dynamic_state_bit(VK_DYNAMIC_STATE_LOGIC_OP_EXT)",
            "vulkan_graphics_dynamic_state_bit(VK_DYNAMIC_STATE_PATCH_CONTROL_POINTS_EXT)",
            "ADD_GRAPHICS_DYNAMIC_STATE_IF_PRESENT(src->dynamic_state_mask, VK_DYNAMIC_STATE_VIEWPORT_WITH_COUNT);",
            "ADD_GRAPHICS_DYNAMIC_STATE_IF_PRESENT(src->dynamic_state_mask, VK_DYNAMIC_STATE_SCISSOR_WITH_COUNT);",
            "ADD_GRAPHICS_DYNAMIC_STATE_IF_PRESENT(src->dynamic_state_mask, VK_DYNAMIC_STATE_RASTERIZER_DISCARD_ENABLE);",
            "ADD_GRAPHICS_DYNAMIC_STATE_IF_PRESENT(src->dynamic_state_mask, VK_DYNAMIC_STATE_DEPTH_BIAS_ENABLE);",
            "ADD_GRAPHICS_DYNAMIC_STATE_IF_PRESENT(src->dynamic_state_mask, VK_DYNAMIC_STATE_PRIMITIVE_RESTART_ENABLE);",
            "ADD_GRAPHICS_DYNAMIC_STATE_IF_PRESENT(src->dynamic_state_mask, VK_DYNAMIC_STATE_LOGIC_OP_EXT);",
            "ADD_GRAPHICS_DYNAMIC_STATE_IF_PRESENT(src->dynamic_state_mask, VK_DYNAMIC_STATE_PATCH_CONTROL_POINTS_EXT);",
        ]:
            self.assertIn(marker, executor)


    def test_vulkan_dynamic_vertex_input_binding_stride_is_replayed(self):
        icd = VULKAN_ICD.read_text()
        executor = GPU_EXECUTOR.read_text()
        app_abi = APP_HEADER.read_text()
        container_abi = CONTAINER_HEADER.read_text()

        self.assertIn("#define PDOCKER_GPU_GRAPHICS_V6_COMMAND_VERTEX_STRIDES_PRESENT 0x00000001u", app_abi)
        self.assertIn("#define PDOCKER_GPU_GRAPHICS_V6_COMMAND_VERTEX_STRIDES_PRESENT 0x00000001u", container_abi)

        icd_bit = c_function_body(icd, "pdocker_vk_graphics_dynamic_state_bit_index")
        executor_bit = c_function_body(executor, "vulkan_graphics_dynamic_state_bit_index")
        for body in [icd_bit, executor_bit]:
            self.assertIn("case VK_DYNAMIC_STATE_VERTEX_INPUT_BINDING_STRIDE: return 25u;", body)

        record_body = c_function_body(icd, "record_vertex_buffer_bindings")
        for marker in [
            "binding->stride = pStrides ? pStrides[i] : 0;",
            "record.flags = pStrides ? PDOCKER_GPU_GRAPHICS_V6_COMMAND_VERTEX_STRIDES_PRESENT : 0u;",
        ]:
            self.assertIn(marker, record_body)

        self.assertIn("PFN_vkCmdBindVertexBuffers2 cmd_bind_vertex_buffers2;", executor)
        self.assertIn('vkGetDeviceProcAddr(rt->device, "vkCmdBindVertexBuffers2")', executor)
        self.assertIn('vkGetDeviceProcAddr(rt->device, "vkCmdBindVertexBuffers2EXT")', executor)

        pipeline_body = executor.split("VkDynamicState dynamic_states[32];", 1)[1].split(
            "VkPipelineDynamicStateCreateInfo dsci", 1
        )[0]
        self.assertIn("vulkan_graphics_dynamic_state_bit(VK_DYNAMIC_STATE_VERTEX_INPUT_BINDING_STRIDE)", pipeline_body)
        self.assertIn("ADD_GRAPHICS_DYNAMIC_STATE_IF_PRESENT(src->dynamic_state_mask, VK_DYNAMIC_STATE_VERTEX_INPUT_BINDING_STRIDE);", pipeline_body)

        validation_body = executor.split("static int validate_vulkan_graphics_v6_frame_content", 1)[1].split(
            "static const PdockerGpuVulkanGraphicsV63DepthStencilStateEntry", 1
        )[0]
        self.assertIn("PDOCKER_GPU_GRAPHICS_V6_COMMAND_VERTEX_STRIDES_PRESENT", validation_body)
        self.assertIn("command->flags & ~supported_flags", validation_body)

        replay_body = executor.rsplit("case PDOCKER_GPU_GRAPHICS_V6_COMMAND_BIND_VERTEX_BUFFERS:", 1)[1].split(
            "case PDOCKER_GPU_GRAPHICS_V6_COMMAND_DRAW:", 1
        )[0]
        for marker in [
            "PDOCKER_GPU_GRAPHICS_V6_COMMAND_VERTEX_STRIDES_PRESENT",
            "VkDeviceSize strides[PDOCKER_GPU_GRAPHICS_REPLAY_MAX_BUFFERS]",
            "strides[b] = (VkDeviceSize)binding->stride;",
            "if (!rt->enabled_ext_extended_dynamic_state || !rt->cmd_bind_vertex_buffers2)",
            "rt->cmd_bind_vertex_buffers2(command_buffer, first_binding",
            "vk_buffers, offsets, NULL, stride_ptr",
            "vkCmdBindVertexBuffers(command_buffer, first_binding",
        ]:
            self.assertIn(marker, replay_body)

    def test_vulkan_graphics_pipeline_static_viewport_scissor_is_serialized(self):
        icd = VULKAN_ICD.read_text()
        body = icd.split("VKAPI_ATTR VkResult VKAPI_CALL vkCreateGraphicsPipelines", 1)[1].split(
            "VKAPI_ATTR void VKAPI_CALL vkDestroyPipeline", 1
        )[0]
        for marker in [
            "uint64_t captured_dynamic_state_mask = 0;",
            "pipeline->dynamic_state_mask = captured_dynamic_state_mask;",
            "pipeline->primitive_restart_enable = ci->pInputAssemblyState->primitiveRestartEnable",
            "pipeline->depth_clamp_enable = ci->pRasterizationState->depthClampEnable",
            "pipeline->rasterizer_discard_enable = ci->pRasterizationState->rasterizerDiscardEnable",
            "pipeline->depth_bias_enable = ci->pRasterizationState->depthBiasEnable",
            "pipeline->line_width = ci->pRasterizationState->lineWidth",
            "pipeline->color_blend_logic_op_enable = cb->logicOpEnable",
            "pipeline->color_blend_constants",
            "memcpy(pipeline->color_blend_constants, cb->blendConstants",
            "pipeline->color_blend_attachments[a] = cb->pAttachments[a]",
            "pipeline->color_blend_attachment_overflow = true",
            "pipeline->color_blend_attachments[a] = cb->pAttachments[a]",
            "pipeline->viewport_count = vs->viewportCount",
            "pipeline->scissor_count = vs->scissorCount",
            "pipeline->static_viewports[v] = vs->pViewports[v]",
            "pipeline->static_scissors[v] = vs->pScissors[v]",
            "PDOCKER_GPU_VULKAN_GRAPHICS_V67_MAX_VIEWPORTS_PER_PIPELINE",
            "PDOCKER_GPU_VULKAN_GRAPHICS_V67_MAX_SCISSORS_PER_PIPELINE",
            "VK_DYNAMIC_STATE_VIEWPORT",
            "VK_DYNAMIC_STATE_SCISSOR",
        ]:
            self.assertIn(marker, body)

        self.assertLess(
            body.index("uint64_t captured_dynamic_state_mask = 0;"),
            body.index("pipeline->viewport_count = vs->viewportCount"),
        )
        self.assertEqual(body.count("pipeline->dynamic_state_mask = captured_dynamic_state_mask;"), 1)

    def test_vulkan_graphics_v63_depth_stencil_state_metadata_is_append_only(self):
        abi = APP_HEADER.read_text()
        container_abi = CONTAINER_HEADER.read_text()
        executor = GPU_EXECUTOR.read_text()
        icd = VULKAN_ICD.read_text()
        icd = VULKAN_ICD.read_text()
        for source in [abi, container_abi]:
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V63_ABI_MINOR 3u", source)
            self.assertIn("PdockerGpuVulkanGraphicsV63FrameHeader", source)
            self.assertIn("PdockerGpuVulkanGraphicsV63DepthStencilStateEntry", source)
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V63_DEPTH_STENCIL_STATE_SCHEMA_HASH", source)
            self.assertIn("PDOCKER_GPU_GRAPHICS_V63_DEPTH_STENCIL_DEPTH_TEST_ENABLE", source)
        self.assertIn("header->abi_minor == PDOCKER_GPU_VULKAN_GRAPHICS_V63_ABI_MINOR", executor)
        self.assertIn("sizeof(PdockerGpuVulkanGraphicsV63FrameHeader)", executor)
        self.assertIn("header_v63->v63.depth_stencil_state_count", executor)
        self.assertIn("find_vulkan_graphics_v63_depth_stencil_state", executor)
        self.assertIn("populate_vulkan_graphics_depth_stencil_state", executor)
        self.assertIn("depth_stencil_state_table_hash", executor)
        self.assertIn("enabled depth/stencil state replay requires V6.3 metadata", executor)
        self.assertNotIn("enabled depth/stencil state replay is not implemented", executor)
        self.assertIn("need_v63_depth_stencil", icd)
        self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V63_ABI_MINOR", icd)
        self.assertIn("depth_stencil_states[depth_stencil_state_count++]", icd)
        self.assertIn("pipeline->depth_compare_op = ds->depthCompareOp", icd)

    def test_vulkan_graphics_v64_resolve_attachment_metadata_is_append_only(self):
        abi = APP_HEADER.read_text()
        container_abi = CONTAINER_HEADER.read_text()
        executor = GPU_EXECUTOR.read_text()
        icd = VULKAN_ICD.read_text()
        for source in [abi, container_abi]:
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V64_ABI_MINOR 4u", source)
            self.assertIn("PdockerGpuVulkanGraphicsV64FrameHeader", source)
            self.assertIn("PdockerGpuVulkanGraphicsV64ResolveAttachmentEntry", source)
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V64_RESOLVE_ATTACHMENT_SCHEMA_HASH", source)
            self.assertIn("X(resolve_layout, u32)", source)
        self.assertIn("header->abi_minor == PDOCKER_GPU_VULKAN_GRAPHICS_V64_ABI_MINOR", executor)
        self.assertIn("sizeof(PdockerGpuVulkanGraphicsV64FrameHeader)", executor)
        self.assertIn("header_v64->v64.resolve_attachment_count", executor)
        self.assertIn("find_vulkan_graphics_v64_resolve_attachment", executor)
        self.assertIn("writeback_image = resolve_image;", executor)
        self.assertIn("writeback_view = resolve_view;", executor)
        self.assertIn("source_samples == VK_SAMPLE_COUNT_1_BIT", executor)
        self.assertIn("resolve_samples != VK_SAMPLE_COUNT_1_BIT", executor)
        self.assertIn("info.resolveMode = (VkResolveModeFlagBits)resolve_meta->resolve_mode;", executor)
        self.assertIn("info.resolveImageView = resolve_replay_view->view;", executor)
        self.assertIn("info.resolveImageLayout = (VkImageLayout)resolve_meta->resolve_layout;", executor)
        self.assertIn("need_v64_resolve_attachment", icd)
        self.assertIn("resolve_entry->resolve_layout = src->resolve_image_layout;", icd)
        self.assertNotIn("src->resolve_image_view || src->resolve_mode != VK_RESOLVE_MODE_NONE", icd)

    def test_vulkan_graphics_v65_static_pipeline_state_metadata_is_append_only(self):
        abi = APP_HEADER.read_text()
        container_abi = CONTAINER_HEADER.read_text()
        executor = GPU_EXECUTOR.read_text()
        icd = VULKAN_ICD.read_text()
        for source in [abi, container_abi]:
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V65_ABI_MINOR 5u", source)
            self.assertIn("PdockerGpuVulkanGraphicsV65FrameHeader", source)
            self.assertIn("PdockerGpuVulkanGraphicsV65StaticPipelineStateEntry", source)
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V65_STATIC_PIPELINE_STATE_SCHEMA_HASH", source)
            self.assertIn("PDOCKER_GPU_GRAPHICS_V65_STATIC_PRIMITIVE_RESTART_ENABLE", source)
            self.assertIn("X(depth_bias_constant_factor_bits, u32)", source)
            self.assertIn("X(line_width_bits, u32)", source)
        self.assertIn("header->abi_minor == PDOCKER_GPU_VULKAN_GRAPHICS_V65_ABI_MINOR", executor)
        self.assertIn("sizeof(PdockerGpuVulkanGraphicsV65FrameHeader)", executor)
        self.assertIn("header_v65->v65.static_pipeline_state_count", executor)
        self.assertIn("find_vulkan_graphics_v65_static_pipeline_state", executor)
        self.assertIn("primitiveRestartEnable =", executor)
        self.assertIn("depthClampEnable =", executor)
        self.assertIn("rasterizerDiscardEnable =", executor)
        self.assertIn("depthBiasEnable =", executor)
        self.assertIn("float_from_u32_bits(static_state->depth_bias_constant_factor_bits)", executor)
        self.assertIn("float_from_u32_bits(static_state->line_width_bits)", executor)
        self.assertIn("need_v65_static_pipeline_state", icd)
        self.assertIn("static_pipeline_states[static_pipeline_state_count++]", icd)
        self.assertIn("pipeline->primitive_restart_enable = ci->pInputAssemblyState->primitiveRestartEnable", icd)
        self.assertIn("pipeline->depth_bias_constant_factor = ci->pRasterizationState->depthBiasConstantFactor", icd)
        self.assertNotIn("ci->pRasterizationState->depthClampEnable ||", icd)

    def test_vulkan_graphics_v66_color_blend_state_metadata_is_append_only(self):
        abi = APP_HEADER.read_text()
        container_abi = CONTAINER_HEADER.read_text()
        executor = GPU_EXECUTOR.read_text()
        icd = VULKAN_ICD.read_text()
        for source in [abi, container_abi]:
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V66_ABI_MINOR 6u", source)
            self.assertIn("PdockerGpuVulkanGraphicsV66FrameHeader", source)
            self.assertIn("PdockerGpuVulkanGraphicsV66ColorBlendStateEntry", source)
            self.assertIn("PdockerGpuVulkanGraphicsV66ColorBlendAttachmentEntry", source)
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V66_COLOR_BLEND_STATE_SCHEMA_HASH", source)
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V66_COLOR_BLEND_ATTACHMENT_SCHEMA_HASH", source)
            self.assertIn("PDOCKER_GPU_GRAPHICS_V66_COLOR_BLEND_LOGIC_OP_ENABLE", source)
            self.assertIn("X(color_write_mask, u32)", source)
        self.assertIn("header->abi_minor == PDOCKER_GPU_VULKAN_GRAPHICS_V66_ABI_MINOR", executor)
        self.assertIn("sizeof(PdockerGpuVulkanGraphicsV66FrameHeader)", executor)
        self.assertIn("header_v66->v66.color_blend_state_count", executor)
        self.assertIn("find_vulkan_graphics_v66_color_blend_state", executor)
        self.assertIn("logicOpEnable = logic_op_enable", executor)
        self.assertIn("dst_attachment->colorWriteMask =", executor)
        self.assertIn("need_v66_color_blend_state", icd)
        self.assertIn("color_blend_states[color_blend_state_count++]", icd)
        self.assertIn("pipeline->color_blend_logic_op_enable = cb->logicOpEnable", icd)
        self.assertNotIn("attachment->blendEnable ||", icd)

    def test_vulkan_graphics_dynamic_rendering_multiview_is_passthrough_when_advertised(self):
        icd = VULKAN_ICD.read_text()
        executor = GPU_EXECUTOR.read_text()
        for marker in [
            "cmd->active_rendering_view_mask = pRenderingInfo->viewMask;",
            "pipeline->dynamic_rendering_view_mask = rendering->viewMask;",
            "pipeline->dynamic_rendering_view_mask = subpass->view_mask;",
            "cmd->active_rendering_view_mask = subpass->view_mask;",
            "dst->view_mask = view_mask;",
            "subpass->viewMask",
            "p->multiview = caps->multiview;",
            "if (p->multiview) mask |= PDOCKER_VK_FEATURE_MULTIVIEW;",
            "if (caps->multiview) mask |= PDOCKER_VK_FEATURE_MULTIVIEW;",
        ]:
            self.assertIn(marker, icd)
        self.assertNotIn("pRenderingInfo->flags != 0 || pRenderingInfo->viewMask != 0", icd)
        self.assertNotIn("if (rendering->viewMask != 0)", icd)
        self.assertNotIn("subpass->flags != 0 || subpass->viewMask != 0", icd)
        self.assertNotIn("pCreateInfo->correlatedViewMaskCount != 0", icd)
        for marker in [
            "enabled_vulkan11.multiview = rt->physical_vulkan11.multiview;",
            "enabled_vulkan11.multiview))",
            "command->rendering_view_mask != 0 &&",
            "!g_vulkan_runtime.enabled_vulkan11.multiview",
            "dynamic rendering multiview requires enabled Vulkan 1.1 multiview",
            "if (src->dynamic_rendering_view_mask != 0 && !rt->enabled_vulkan11.multiview) return -EOPNOTSUPP;",
        ]:
            self.assertIn(marker, executor)
        self.assertNotIn("dynamic rendering multiview is not supported", executor)
        self.assertNotIn("if (src->dynamic_rendering_view_mask != 0) return -EOPNOTSUPP;", executor)

    def test_vulkan_graphics_unused_color_attachment_slots_are_preserved(self):
        abi = APP_HEADER.read_text()
        container_abi = CONTAINER_HEADER.read_text()
        icd = VULKAN_ICD.read_text()
        executor = GPU_EXECUTOR.read_text()
        for source in [abi, container_abi]:
            self.assertIn("PDOCKER_GPU_GRAPHICS_V6_ATTACHMENT_UNUSED_SLOT", source)
        for marker in [
            "entry->flags = PDOCKER_GPU_GRAPHICS_V6_ATTACHMENT_UNUSED_SLOT;",
            "entry->image_view_index = PDOCKER_GPU_V5_DESCRIPTOR_OBJECT_NONE;",
            "entry->format = VK_FORMAT_UNDEFINED;",
            "entry->layout = VK_IMAGE_LAYOUT_UNDEFINED;",
            "const bool unused_color_attachment",
            "!src->valid || (!src->image_view && !src->resolve_image_view)",
            "if (unused_color_attachment) {",
            "color->attachment != VK_ATTACHMENT_UNUSED",
            "color->attachment == VK_ATTACHMENT_UNUSED ||",
            "subpass->color_attachments[c] != VK_ATTACHMENT_UNUSED",
        ]:
            self.assertIn(marker, icd)
        for marker in [
            "attachment->flags & ~PDOCKER_GPU_GRAPHICS_V6_ATTACHMENT_UNUSED_SLOT",
            "src->flags & ~PDOCKER_GPU_GRAPHICS_V6_ATTACHMENT_UNUSED_SLOT",
            "invalid unused graphics color attachment slot",
            "color_formats[c] = (VkFormat)formats[c];",
            "imageView = VK_NULL_HANDLE",
            "color_attachments[color_attachment_count++] = (VkRenderingAttachmentInfo)",
        ]:
            self.assertIn(marker, executor)
        self.assertNotIn("if (color_formats[c] == VK_FORMAT_UNDEFINED) return -EPROTO;", executor)

    def test_vulkan_secondary_command_buffers_are_index_rebased_not_unconditionally_rejected(self):
        icd = VULKAN_ICD.read_text()
        self.assertIn("VkCommandBufferLevel level;", icd)
        self.assertIn("cmd->level = pAllocateInfo->level;", icd)
        self.assertIn("append_secondary_command_buffer", icd)
        self.assertIn("command_buffer_has_room_for_secondary", icd)
        self.assertIn("update_payloads[PDOCKER_VK_MAX_COMMAND_OPS]", icd)
        self.assertIn("op.payload = update_payloads[i];", icd)
        self.assertIn("record.rendering_snapshot_index += rendering_base;", icd)
        self.assertIn("record.descriptor_bind_snapshot_index += descriptor_bind_base;", icd)
        self.assertIn("record.dynamic_state_index += dynamic_state_base;", icd)
        self.assertIn("record.draw_snapshot_index += graphics_draw_base;", icd)
        self.assertIn("record.push_op_index += push_op_base;", icd)
        self.assertIn("record.memory_barrier_op_first += memory_barrier_base;", icd)
        self.assertIn("record.first_dynamic_offset += dynamic_offset_base;", icd)
        self.assertIn("bool inherited_rendering_active;", icd)
        self.assertIn("command_buffer_begin_pnext_supported", icd)
        begin_pnext_body = c_function_body(icd, "command_buffer_begin_pnext_supported")
        self.assertIn("VK_STRUCTURE_TYPE_DEVICE_GROUP_COMMAND_BUFFER_BEGIN_INFO", begin_pnext_body)
        self.assertIn("info->deviceMask != 0 && info->deviceMask != 1u", begin_pnext_body)
        begin_body = c_function_body(icd, "vkBeginCommandBuffer")
        self.assertIn("command_buffer_begin_pnext_supported(pBeginInfo)", begin_body)
        self.assertIn('command_buffer_mark_recording_failed(cmd, "command-buffer-begin-pnext-unsupported")', begin_body)
        self.assertIn("command_buffer_begin_inheritance_supported", icd)
        self.assertIn("VK_STRUCTURE_TYPE_COMMAND_BUFFER_INHERITANCE_RENDERING_INFO", icd)
        self.assertIn("cmd->dynamic_rendering_active || cmd->inherited_rendering_active", icd)
        self.assertIn("inherit->occlusionQueryEnable || inherit->queryFlags != 0", icd)
        self.assertIn("render_pass_subpass_can_normalize_to_dynamic_rendering(rp, inherit->subpass)", icd)
        self.assertIn("op.index += dispatch_base;", icd)
        self.assertIn("op.index += graphics_draw_base;", icd)
        execute_body = icd[icd.index("VKAPI_ATTR void VKAPI_CALL vkCmdExecuteCommands"):]
        execute_body = execute_body[:execute_body.index("VKAPI_ATTR void VKAPI_CALL vkCmdBindDescriptorSets")]
        self.assertIn("secondary->level != VK_COMMAND_BUFFER_LEVEL_SECONDARY", execute_body)
        self.assertIn("!append_secondary_command_buffer(cmd, secondary)", execute_body)
        self.assertNotIn("cmd->graphics_unsupported = true;\n}", execute_body)

    def test_vulkan_graphics_v6_describe_response_is_nonterminal(self):
        icd = VULKAN_ICD.read_text()
        response_helpers = icd.split("static bool dispatch_response_is_graphics_transport", 1)[1].split("typedef struct {", 1)[0]
        response_reader = icd.split("static int read_dispatch_response_status", 1)[1].split("typedef struct {", 1)[0]
        self.assertIn('\\"stage\\":\\"vulkan-graphics-v6-describe\\"', response_reader)
        self.assertIn("dispatch_response_is_graphics_transport", response_helpers)
        self.assertIn("dispatch_response_is_graphics_terminal_success", response_helpers)
        self.assertIn('\\"stage\\":\\"vulkan-graphics-v6-replay\\"', response_helpers)
        self.assertIn('\\"execution_implemented\\":true', response_helpers)
        graphics_branch = response_reader.split("if (graphics_transport)", 1)[1].split(
            "if (strstr(line, \"\\\"stage\\\":\\\"vulkan-graphics-v6-describe\\\"\") != NULL)", 1
        )[0]
        self.assertIn('\\"valid\\":false', graphics_branch)
        self.assertIn("dispatch_response_is_graphics_terminal_success(line)", graphics_branch)
        self.assertIn("saw_nonterminal = true;", graphics_branch)
        self.assertIn("continue;", graphics_branch)
        self.assertIn("rc = saw_nonterminal ? -EPROTO : -EIO;", response_reader)

    def test_vulkan_dispatch_v5_socket_path_is_magic_framed_not_line_framed(self):
        executor = GPU_EXECUTOR.read_text()
        self.assertIn("connection_starts_with_v5_magic", executor)
        self.assertIn("recv(cfd, &first, 1, MSG_PEEK)", executor)
        self.assertIn("first != PDOCKER_GPU_VULKAN_DISPATCH_V5_MAGIC[0]", executor)
        self.assertIn("MSG_PEEK | MSG_WAITALL", executor)
        self.assertIn("handle_vulkan_dispatch_v5_frame", executor)
        self.assertIn("recv_vulkan_dispatch_v5_frame", executor)
        self.assertIn("read_exact_bytes(cfd, frame + header_out->header_size", executor)
        self.assertIn("run_vulkan_dispatch_fd(\n        passed_fds[header.shader_fd_index], binding_fds", executor)
        serve_loop = executor.split("for (;;) {\n            int graphics_v6_prefix = connection_starts_with_graphics_v6_magic", 1)[1].split(
            "if (nread == -EMSGSIZE)", 1
        )[0]
        self.assertIn("handle_vulkan_graphics_v6_frame(cfd)", serve_loop)
        self.assertIn("handle_vulkan_dispatch_v5_frame(cfd)", serve_loop)
        self.assertIn("recv_command_with_fds(cfd, cmd", serve_loop)
        self.assertLess(serve_loop.index("handle_vulkan_graphics_v6_frame(cfd)"), serve_loop.index("handle_vulkan_dispatch_v5_frame(cfd)"))
        self.assertLess(serve_loop.index("handle_vulkan_dispatch_v5_frame(cfd)"), serve_loop.index("recv_command_with_fds(cfd, cmd"))
        self.assertNotIn("VULKAN_DISPATCH_V5 ", executor)

    def test_vulkan_graphics_v6_executor_frame_is_validated_and_fail_closed(self):
        executor = GPU_EXECUTOR.read_text()
        for marker in [
            "connection_starts_with_graphics_v6_magic",
            "typedef struct VulkanGraphicsV6FrameView",
            "init_vulkan_graphics_v6_frame_view",
            "describe_vulkan_graphics_v6_frame",
            "validate_vulkan_graphics_v6_header",
            "recv_vulkan_graphics_v6_header_with_fds",
            "validate_vulkan_graphics_v6_frame_content",
            "table_range_valid",
            "payload_range_valid",
            "frame_ranges_do_not_overlap",
            "handle_vulkan_graphics_v6_frame",
            "PDOCKER_GPU_VULKAN_GRAPHICS_V6_COMMAND_SUBMIT",
            "PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_FRAME_BYTES",
            "PDOCKER_GPU_VULKAN_GRAPHICS_V6_SHADER_STAGE_SCHEMA_HASH",
            "PDOCKER_GPU_VULKAN_GRAPHICS_V6_PIPELINE_SCHEMA_HASH",
            "PDOCKER_GPU_VULKAN_GRAPHICS_V6_COMMAND_SCHEMA_HASH",
            "header->resource_schema_hash != PDOCKER_GPU_VULKAN_DISPATCH_V5_RESOURCE_SCHEMA_HASH",
            "header->descriptor_schema_hash != PDOCKER_GPU_VULKAN_DISPATCH_V5_DESCRIPTOR_OBJECT_SCHEMA_HASH",
            "header->image_schema_hash != PDOCKER_GPU_VULKAN_DISPATCH_V5_IMAGE_SCHEMA_HASH",
            "header->image_view_schema_hash != PDOCKER_GPU_VULKAN_DISPATCH_V5_IMAGE_VIEW_SCHEMA_HASH",
            "header->sampler_schema_hash != PDOCKER_GPU_VULKAN_DISPATCH_V5_SAMPLER_SCHEMA_HASH",
            "header->image_count > PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_IMAGES",
            "header->image_view_count > PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_IMAGE_VIEWS",
            "header->sampler_count > PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_SAMPLERS",
            "descriptor->image_view_index >= header->image_view_count",
            "attachment->image_view_index >= header->image_view_count",
            "header->flags != 0",
            "command->pipeline_index != UINT32_MAX",
            '\\"stage\\":\\"vulkan-graphics-v6-describe\\"',
            '\\"execution_implemented\\":false',
            "describe_vulkan_graphics_v6_frame(json_out(), &view);",
            "preflight_vulkan_graphics_v6_replay_supported",
            "vulkan-graphics-v6-replay-preflight",
            "VK_DESCRIPTOR_TYPE_INPUT_ATTACHMENT",
            "graphics write descriptor replay is not implemented",
            "graphics shader specialization replay requires V6.2 metadata",
            "materialize_vulkan_graphics_v6_pipelines",
            "vulkan-graphics-v6-pipeline-materialize",
            "materialize_vulkan_graphics_v6_attachments",
            "vulkan-graphics-v6-attachment-materialize",
            "materialize_vulkan_graphics_v6_buffers",
            "vulkan-graphics-v6-buffer-materialize",
            "materialize_vulkan_graphics_v6_descriptors",
            "vulkan-graphics-v6-descriptor-materialize",
            "record_vulkan_graphics_v6_attachment_writeback_commands",
            "writeback_vulkan_graphics_v6_attachments",
            "vulkan-graphics-v6-attachment-writeback",
            "run_vulkan_graphics_v6_frame",
        ]:
            self.assertIn(marker, executor)
        handle_body = executor.split("static int handle_vulkan_graphics_v6_frame", 1)[1].split(
            "static int handle_vulkan_dispatch_v5_frame", 1
        )[0]
        self.assertLess(
            handle_body.index("validate_vulkan_graphics_v6_frame_content"),
            handle_body.index("describe_vulkan_graphics_v6_frame(json_out(), &view);"),
        )
        self.assertLess(
            handle_body.index("describe_vulkan_graphics_v6_frame(json_out(), &view);"),
            handle_body.index("run_vulkan_graphics_v6_frame(&view);"),
        )
        self.assertNotIn("vkCmdDraw", handle_body)
        self.assertNotIn("vkQueueSubmit", handle_body)

    def test_vulkan_graphics_v6_executor_materializes_pipelines_before_command_replay(self):
        executor = GPU_EXECUTOR.read_text()
        materializer = executor.split("static int materialize_vulkan_graphics_v6_pipelines", 1)[1].split(
            "static int run_vulkan_graphics_v6_frame", 1
        )[0]
        for marker in [
            "read_graphics_shader_fd",
            "copy_graphics_entry_name",
            "collect_graphics_push_ranges_for_layout",
            "vkCreateShaderModule",
            "vkCreatePipelineLayout",
            "VkPipelineVertexInputStateCreateInfo",
            "VkPipelineRenderingCreateInfo",
            "VK_DYNAMIC_STATE_VIEWPORT",
            "VK_DYNAMIC_STATE_SCISSOR",
            "vkCreateGraphicsPipelines",
        ]:
            self.assertIn(marker, materializer)
        self.assertIn("fnv1a64_update(1469598103934665603ull, code, (size_t)stage->shader_size) != stage->shader_hash", executor)
        run_body = executor.split("static int run_vulkan_graphics_v6_frame", 1)[1].split(
            "static int recv_vulkan_graphics_v6_header_with_fds", 1
        )[0]
        self.assertIn("materialize_vulkan_graphics_v6_pipelines", run_body)
        self.assertIn('\\"stage\\":\\"vulkan-graphics-v6-pipeline-materialize\\"', run_body)
        self.assertIn("cleanup_vulkan_graphics_v6_replay_state", run_body)
        self.assertIn("destroy_vulkan_graphics_replay_pipelines", executor)
        self.assertLess(
            run_body.index("preflight_vulkan_graphics_v6_runtime_supported"),
            run_body.index("materialize_vulkan_graphics_v6_pipelines"),
        )
        self.assertIn("materialize_vulkan_graphics_v6_attachments", run_body)
        self.assertIn('\\"stage\\":\\"vulkan-graphics-v6-attachment-materialize\\"', run_body)
        self.assertLess(
            run_body.index("materialize_vulkan_graphics_v6_pipelines"),
            run_body.index("materialize_vulkan_graphics_v6_attachments"),
        )
        self.assertIn("materialize_vulkan_graphics_v6_buffers", run_body)
        self.assertIn('\\"stage\\":\\"vulkan-graphics-v6-buffer-materialize\\"', run_body)
        self.assertLess(
            run_body.index("materialize_vulkan_graphics_v6_attachments"),
            run_body.index("materialize_vulkan_graphics_v6_buffers"),
        )
        self.assertLess(
            run_body.index("materialize_vulkan_graphics_v6_buffers"),
            run_body.index("materialize_vulkan_graphics_v6_descriptors"),
        )
        self.assertIn("materialize_vulkan_graphics_v6_descriptors", run_body)
        self.assertIn('\\"stage\\":\\"vulkan-graphics-v6-descriptor-materialize\\"', run_body)
        self.assertLess(
            run_body.index("materialize_vulkan_graphics_v6_descriptors"),
            run_body.index("record_vulkan_graphics_v6_command_buffer"),
        )

        self.assertIn("record_vulkan_graphics_v6_command_buffer", run_body)
        self.assertIn('\\"stage\\":\\"vulkan-graphics-v6-command-record\\"', run_body)
        self.assertLess(
            run_body.index("materialize_vulkan_graphics_v6_attachments"),
            run_body.index("record_vulkan_graphics_v6_command_buffer"),
        )
        self.assertIn("submit_vulkan_graphics_v6_command_buffer", run_body)
        self.assertIn('\\"stage\\":\\"vulkan-graphics-v6-queue-submit\\"', run_body)
        self.assertLess(
            run_body.index("record_vulkan_graphics_v6_command_buffer"),
            run_body.index("submit_vulkan_graphics_v6_command_buffer"),
        )
        self.assertIn("writeback_vulkan_graphics_v6_attachments", run_body)
        self.assertIn('\\"stage\\":\\"vulkan-graphics-v6-attachment-writeback\\"', run_body)
        self.assertLess(
            run_body.index("submit_vulkan_graphics_v6_command_buffer"),
            run_body.index("writeback_vulkan_graphics_v6_attachments"),
        )

    def test_vulkan_graphics_v6_executor_materializes_attachments_before_command_replay(self):
        executor = GPU_EXECUTOR.read_text()
        helper = executor.split("static int vulkan_graphics_attachment_ops_supported", 1)[1].split(
            "static int run_vulkan_graphics_v6_frame", 1
        )[0]
        for marker in [
            "VulkanDispatchV5ObjectTables object_tables",
            "materialize_vulkan_dispatch_images",
            "PDOCKER_GPU_GRAPHICS_V6_ATTACHMENT_COLOR",
            "VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT",
            "VK_ATTACHMENT_LOAD_OP_LOAD",
            "VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL",
            "VK_IMAGE_LAYOUT_GENERAL",
        ]:
            self.assertIn(marker, helper)

    def test_vulkan_graphics_executor_supports_uint8_indices_when_driver_advertises_it(self):
        executor = GPU_EXECUTOR.read_text()
        for marker in [
            "VkPhysicalDeviceIndexTypeUint8FeaturesEXT physical_index_type_uint8",
            "VkPhysicalDeviceIndexTypeUint8FeaturesEXT enabled_index_type_uint8",
            "VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_INDEX_TYPE_UINT8_FEATURES_EXT",
            "VK_EXT_INDEX_TYPE_UINT8_EXTENSION_NAME",
            "rt->physical_extended_dynamic_state.pNext = &rt->physical_extended_dynamic_state2",
            "rt->physical_extended_dynamic_state2.pNext = &rt->physical_index_type_uint8",
            "enabled_index_type_uint8.pNext = device_features_pnext",
            "rt->enabled_ext_index_type_uint8",
            "case VK_INDEX_TYPE_UINT8_EXT:",
            "*out_stride = 1",
            "vulkan_graphics_index_type_supported_by_runtime",
            "enabled_index_type_uint8.indexTypeUint8 && rt->enabled_ext_index_type_uint8",
            "rc = vulkan_graphics_index_type_supported_by_runtime(rt, command->index_type);",
            'indexTypeUint8',
            'VK_EXT_index_type_uint8',
        ]:
            self.assertIn(marker, executor)

    def test_vulkan_icd_advertises_uint8_indices_only_from_executor_caps(self):
        icd = VULKAN_ICD.read_text()
        for marker in [
            "PDOCKER_VK_FEATURE_INDEX_TYPE_UINT8",
            "VkPhysicalDeviceIndexTypeUint8FeaturesEXT index_type_uint8",
            "ext_index_type_uint8",
            "json_read_u32(json, \"indexTypeUint8\", &caps->index_type_uint8.indexTypeUint8)",
            "json_read_u32(json, \"VK_EXT_index_type_uint8\", &value)",
            "VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_INDEX_TYPE_UINT8_FEATURES_EXT",
            "p->indexTypeUint8 = (caps && caps->ext_index_type_uint8 && caps->index_type_uint8.indexTypeUint8)",
            "VK_EXT_INDEX_TYPE_UINT8_EXTENSION_NAME",
            "caps && caps->ext_index_type_uint8 && caps->index_type_uint8.indexTypeUint8",
            "mask |= PDOCKER_VK_FEATURE_INDEX_TYPE_UINT8",
        ]:
            self.assertIn(marker, icd)
        extension_body = icd.split("vkEnumerateDeviceExtensionProperties", 1)[1].split(
            "#undef ADD_DEVICE_EXTENSION", 1
        )[0]
        self.assertIn("VK_EXT_INDEX_TYPE_UINT8_EXTENSION_NAME", extension_body)
        self.assertIn("caps->ext_index_type_uint8", extension_body)

    def test_vulkan_graphics_v6_executor_materializes_vertex_buffers_before_command_record(self):
        executor = GPU_EXECUTOR.read_text()
        helper = executor.split("typedef struct VulkanGraphicsReplayBuffer", 1)[1].split(
            "static int graphics_push_metadata_for_command", 1
        )[0]
        for marker in [
            "add_vulkan_graphics_replay_buffer_range",
            "mark_vulkan_graphics_replay_buffer_writeback_range",
            "writeback_needed",
            "writeback_base",
            "writeback_end",
            "PDOCKER_GPU_V5_RESOURCE_FLAG_HOST_FD_BACKED",
            "create_vulkan_buffer_with_usage",
            "VK_BUFFER_USAGE_VERTEX_BUFFER_BIT",
            "VK_BUFFER_USAGE_INDEX_BUFFER_BIT",
            "vulkan_graphics_index_stride",
            "read_fd_exact",
            "write_fd_exact",
            "checked_u64_to_off_t",
            "destroy_vulkan_graphics_replay_buffers",
        ]:
            self.assertIn(marker, helper)
        run_body = executor.split("static int run_vulkan_graphics_v6_frame", 1)[1].split(
            "static int recv_vulkan_graphics_v6_header_with_fds", 1
        )[0]
        self.assertIn('\\"stage\\":\\"vulkan-graphics-v6-buffer-materialize\\"', run_body)
        self.assertLess(
            run_body.index("materialize_vulkan_graphics_v6_buffers"),
            run_body.index("record_vulkan_graphics_v6_command_buffer"),
        )

    def test_vulkan_graphics_v6_executor_records_command_buffer_before_submit(self):
        executor = GPU_EXECUTOR.read_text()
        helper = executor.split("static int record_vulkan_graphics_v6_command_buffer", 1)[1].split(
            "static int run_vulkan_graphics_v6_frame", 1
        )[0]
        for marker in [
            "vkAllocateCommandBuffers",
            "vkBeginCommandBuffer",
            "rt->cmd_begin_rendering",
            "rt->cmd_end_rendering",
            "vkCmdBindPipeline",
            "vkCmdSetViewport",
            "vkCmdSetScissor",
            "vkCmdPushConstants",
            "vkCmdBindVertexBuffers",
            "vkCmdBindIndexBuffer",
            "vkCmdBindDescriptorSets",
            "vkCmdDraw",
            "vkCmdDrawIndexed",
            "vkEndCommandBuffer",
            "vkFreeCommandBuffers",
            "PDOCKER_GPU_GRAPHICS_V6_COMMAND_DRAW",
            "PDOCKER_GPU_GRAPHICS_V6_COMMAND_BIND_INDEX_BUFFER",
            "PDOCKER_GPU_GRAPHICS_V6_COMMAND_DRAW_INDEXED",
            "PDOCKER_GPU_GRAPHICS_V6_COMMAND_BIND_DESCRIPTOR_SETS",
        ]:
            self.assertIn(marker, helper)
        self.assertIn("vkAllocateDescriptorSets", executor)
        self.assertIn("vkUpdateDescriptorSets", executor)
        self.assertIn("collect_graphics_descriptor_layout_for_layout", executor)
        self.assertIn("find_vulkan_graphics_replay_descriptor_bind", executor)
        self.assertNotIn("graphics input attachment descriptor replay is not implemented", executor)
        self.assertIn("descriptor_pool_input_attachment_count", executor)
        self.assertIn(".type = VK_DESCRIPTOR_TYPE_INPUT_ATTACHMENT", executor)
        self.assertIn("VK_IMAGE_LAYOUT_READ_ONLY_OPTIMAL", executor)
        self.assertIn("unsupported graphics image descriptor layout", executor)
        self.assertIn("graphics write descriptor replay is not implemented", executor)
        self.assertIn("descriptor_type != VK_DESCRIPTOR_TYPE_STORAGE_BUFFER", executor)
        self.assertIn("descriptor_type != VK_DESCRIPTOR_TYPE_STORAGE_IMAGE", executor)
        self.assertIn("descriptor_pool_storage_image_count", executor)
        self.assertIn(".type = VK_DESCRIPTOR_TYPE_STORAGE_IMAGE", executor)
        self.assertIn("image->writeback_needed = 1", executor)
        self.assertIn("VK_ACCESS_SHADER_READ_BIT | VK_ACCESS_SHADER_WRITE_BIT", executor)
        self.assertIn("record_vulkan_graphics_v6_buffer_writeback_barriers", executor)
        self.assertIn("writeback_vulkan_graphics_v6_storage_buffers", executor)
        self.assertIn("vulkan-graphics-v6-storage-buffer-writeback", executor)
        self.assertIn("VK_ACCESS_SHADER_WRITE_BIT", executor)
        self.assertIn("VK_ACCESS_HOST_READ_BIT", executor)
        self.assertNotIn("graphics descriptor replay is not implemented", executor)
        self.assertIn("VkDescriptorImageInfo image_infos", executor)
        self.assertIn("VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER", executor)
        self.assertIn("VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE", executor)
        self.assertIn("VK_DESCRIPTOR_TYPE_SAMPLER", executor)
        self.assertIn("vulkan_image_descriptor_layout_valid", executor)
        self.assertIn("record_vulkan_graphics_v6_staged_image_uploads", executor)
        self.assertIn("vulkan_graphics_attachment_layout_supported", executor)
        self.assertIn("vulkan_graphics_attachment_required_usage", executor)
        self.assertIn("vulkan_graphics_merge_attachment_copy_range", executor)
        self.assertIn("effective_load_op == VK_ATTACHMENT_LOAD_OP_LOAD && image->requires_staging", executor)
        self.assertIn("image->descriptor_layout = (VkImageLayout)attachment->layout", executor)
        self.assertIn("rc = record_vulkan_graphics_v6_staged_image_uploads(command_buffer, attachments);", executor)
        self.assertIn("vulkan_graphics_attachment_writeback_access_mask", executor)
        self.assertIn("vulkan_format_bytes_per_pixel_for_aspect", executor)
        self.assertIn("vulkan_image_tight_subresource_offset_for_aspect", executor)
        self.assertIn("vulkan_image_tight_copy_size_for_aspect", executor)
        self.assertIn("VK_ACCESS_DEPTH_STENCIL_ATTACHMENT_WRITE_BIT", executor)
        self.assertIn("VK_PIPELINE_STAGE_EARLY_FRAGMENT_TESTS_BIT", executor)
        self.assertIn("VK_PIPELINE_STAGE_LATE_FRAGMENT_TESTS_BIT", executor)
        self.assertNotIn("depth/stencil attachment store/writeback is not implemented", executor)
        self.assertIn(".pDepthAttachment = depth_attachment_ptr", executor)
        self.assertIn(".pStencilAttachment = stencil_attachment_ptr", executor)
        self.assertIn("VkPipelineDepthStencilStateCreateInfo dssci", executor)
        self.assertIn(".pDepthStencilState =", executor)
        self.assertNotIn("depth/stencil graphics replay is not implemented", executor)
        self.assertNotIn("depth/stencil graphics pipeline replay is not implemented", executor)
        self.assertIn("vkCmdCopyBufferToImage", executor)
        self.assertIn("VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL", executor)
        self.assertIn("command->instance_count, command->first_vertex", executor)
        self.assertIn("command->vertex_offset, command->first_instance", executor)
        self.assertNotIn("instanced graphics replay is not implemented", executor)
        self.assertNotIn("instanced indexed graphics replay is not implemented", executor)
        self.assertIn("image->upload_pending = 0", executor)
        self.assertIn("image->descriptor_layout_seen = 1", executor)
        self.assertIn("image->copy_base_mip", executor)
        self.assertIn("image->copy_level_count", executor)
        self.assertIn("VK_ACCESS_TRANSFER_WRITE_BIT", helper)
        self.assertIn("VK_PIPELINE_STAGE_TRANSFER_BIT", helper)
        self.assertIn("VK_ACCESS_SHADER_READ_BIT", helper)
        self.assertIn("vkCmdPipelineBarrier(command_buffer", helper)
        self.assertNotIn("if (image->requires_staging) return -EOPNOTSUPP;", executor)
        self.assertIn("rc = -EOPNOTSUPP;", helper)
        run_body = executor.split("static int run_vulkan_graphics_v6_frame", 1)[1].split(
            "static int recv_vulkan_graphics_v6_header_with_fds", 1
        )[0]
        self.assertIn('\\"stage\\":\\"vulkan-graphics-v6-command-record\\"', run_body)
        self.assertIn('\\"stage\\":\\"vulkan-graphics-v6-queue-submit\\"', run_body)
        self.assertIn("record_vulkan_graphics_v6_attachment_writeback_commands", helper)
        self.assertIn("writeback_vulkan_graphics_v6_attachments", run_body)

    def test_vulkan_graphics_v6_executor_submits_before_attachment_writeback(self):
        executor = GPU_EXECUTOR.read_text()
        helper = executor.split("static int submit_vulkan_graphics_v6_command_buffer", 1)[1].split(
            "static int run_vulkan_graphics_v6_frame", 1
        )[0]
        for marker in [
            "vkCreateFence",
            "vkQueueSubmit(rt->graphics_queue",
            "PDOCKER_GPU_GRAPHICS_SUBMIT_TIMEOUT_MS",
            "vkWaitForFences",
            "vkDestroyFence",
        ]:
            self.assertIn(marker, helper)
        run_body = executor.split("static int run_vulkan_graphics_v6_frame", 1)[1].split(
            "static int recv_vulkan_graphics_v6_header_with_fds", 1
        )[0]
        self.assertIn('\\"stage\\":\\"vulkan-graphics-v6-queue-submit\\"', run_body)
        for marker in [
            "const int sync_only_submit",
            "view->header_v619->v619.submit_sync_count > 0",
            "view->header->command_count == 0 && !sync_only_submit",
        ]:
            self.assertIn(marker, run_body)
        self.assertLess(
            run_body.index("const int sync_only_submit"),
            run_body.index("preflight_vulkan_graphics_v6_runtime_supported"),
        )
        for marker in [
            'VulkanGraphicsSubmitDiag submit_diag',
            '\\"submit_stage\\"',
            '\\"vk_result\\"',
            '\\"wait_count\\"',
            '\\"signal_count\\"',
            '\\"timeline_used\\"',
        ]:
            self.assertIn(marker, run_body)
        self.assertIn('\\"stage\\":\\"vulkan-graphics-v6-storage-buffer-writeback\\"', run_body)
        self.assertIn('\\"stage\\":\\"vulkan-graphics-v6-attachment-writeback\\"', run_body)
        self.assertIn("free_vulkan_graphics_v6_replay_command_buffer", run_body)
        self.assertIn("vkFreeCommandBuffers", executor)
        self.assertIn("writeback_vulkan_graphics_v6_storage_buffers", run_body)
        self.assertIn("writeback_vulkan_graphics_v6_attachments", run_body)
        self.assertLess(
            run_body.index("submit_vulkan_graphics_v6_command_buffer"),
            run_body.index("writeback_vulkan_graphics_v6_storage_buffers"),
        )
        self.assertLess(
            run_body.index("writeback_vulkan_graphics_v6_storage_buffers"),
            run_body.index("writeback_vulkan_graphics_v6_attachments"),
        )
        self.assertLess(
            run_body.index("writeback_vulkan_graphics_v6_attachments"),
            run_body.rindex('\\"stage\\":\\"vulkan-graphics-v6-replay\\"'),
        )

    def test_vulkan_graphics_v6_submit_validator_hardens_resource_descriptor_and_draw_refs(self):
        executor = GPU_EXECUTOR.read_text()
        validator = executor.split("static int validate_vulkan_graphics_v6_frame_content", 1)[1].split(
            "static int recv_vulkan_graphics_v6_header_with_fds", 1
        )[0]
        for marker in [
            "static int u64_range_within_size",
            "resource->parent_resource_index != PDOCKER_GPU_V5_RESOURCE_PARENT_NONE) return -EPROTO;",
            "resource->fd_index != PDOCKER_GPU_V5_RESOURCE_FD_NONE) return -EPROTO;",
            "memory->resource_type != PDOCKER_GPU_V5_RESOURCE_TYPE_MEMORY",
            "!u64_range_within_size(resource->memory_offset, resource->size, memory->size)",
            "images[i].memory_resource_index >= header->resource_count",
            "!u64_range_within_size(images[i].memory_offset, images[i].memory_size, memory->size)",
            "vulkan_dispatch_descriptor_type_from_api(descriptor->descriptor_type, &descriptor_type) == 0",
            "descriptor->resource_index == PDOCKER_GPU_V5_DESCRIPTOR_OBJECT_NONE",
            "descriptor->image_view_index != PDOCKER_GPU_V5_DESCRIPTOR_OBJECT_NONE ||",
            "uint64_t effective_offset = 0;",
            "checked_u64_add3(descriptor->buffer_offset, descriptor->dynamic_offset, 0, &effective_offset)",
            "descriptor->dynamic_offset != 0",
            "!u64_range_within_size(effective_offset, effective_range, buffer->size)",
            "!u64_range_within_size(descriptor->transfer_offset, descriptor->transfer_size, buffer->size)",
            "descriptor->transfer_offset < effective_offset",
            "uint64_t transfer_delta = descriptor->transfer_offset - effective_offset",
            "descriptor->transfer_size > effective_range - transfer_delta",
            "vulkan_dispatch_image_descriptor_type_from_api(descriptor->descriptor_type, &descriptor_type)",
            "descriptor->resource_index != PDOCKER_GPU_V5_DESCRIPTOR_OBJECT_NONE) return -EPROTO;",
            "vulkan_descriptor_type_requires_image_view(descriptor_type)",
            "vulkan_descriptor_type_requires_sampler(descriptor_type)",
            "vertex_bindings[i].buffer_resource_index >= header->resource_count",
            "!u64_range_within_size(vertex_bindings[i].offset, vertex_bindings[i].size, buffer->size)",
            "attachment->image_view_index >= header->image_view_count",
            "image_views[i].image_index >= header->image_count",
            "!vulkan_dispatch_image_view_range_valid(image, &image_views[i])",
            "attachment->resolve_image_view_index >= header->image_view_count",
            "payload_range_valid(attachment->clear_value_offset, attachment->clear_value_size",
            "range_add_u32(command->attachment_first, command->attachment_count, header->attachment_count)",
            "command->command_type == PDOCKER_GPU_GRAPHICS_V6_COMMAND_DRAW &&",
            "command->command_type == PDOCKER_GPU_GRAPHICS_V6_COMMAND_DRAW_INDEXED",
            "payload_hash != header->payload_hash",
            "frame_hash != header->frame_hash",
            "stage->shader_hash == 0",
            "full_fd_hash(passed_fds[stage->shader_fd_index]",
            "shader_hash != stage->shader_hash",
            "data_hash != state->data_hash",
            "attribute->binding == binding->binding",
            "command->pipeline_index == UINT32_MAX",
            "command->index_buffer_resource_index == UINT32_MAX",
            "index_buffer->resource_type != PDOCKER_GPU_V5_RESOURCE_TYPE_BUFFER",
            "command->index_offset > index_buffer->size",
        ]:
            self.assertIn(marker, validator if marker != "static int u64_range_within_size" else executor)

    def test_vulkan_graphics_v61_p0_p6_plan_matches_current_executor_preflight(self):
        plan = LLAMA_GPU_NEXT_STEPS.read_text()
        for marker in [
            "Vulkan graphics V6.1 P0-P6",
            "9d6e724",
            "attachment table",
            "`execution_implemented=false`",
            "`vulkan-graphics-v6-replay-preflight`",
            "supported-subset replay contract",
            "materialize/record/submit/writeback evidence stages",
            "fail closed",
        ]:
            self.assertIn(marker, plan)

        executor = GPU_EXECUTOR.read_text()
        describe_body = executor.split("static void describe_vulkan_graphics_v6_frame", 1)[1].split(
            "static int u64_range_within_size", 1
        )[0]
        for marker in [
            '\\"stage\\":\\"vulkan-graphics-v6-describe\\"',
            '\\"execution_implemented\\":false',
            "write_vulkan_graphics_v6_table_desc(out, \"attachments\"",
            "write_vulkan_graphics_v6_table_desc(out, \"dynamic_offsets\"",
            "write_vulkan_graphics_v6_table_desc(out, \"push_constant_metadata\"",
        ]:
            self.assertIn(marker, describe_body)

    def test_graphics_attachment_copy_range_validation_is_centralized(self):
        source = GPU_EXECUTOR.read_text()
        self.assertIn("vulkan_graphics_merge_image_copy_range_for_aspect", source)
        self.assertIn("vulkan_graphics_merge_attachment_copy_range", source)
        self.assertIn("vulkan_graphics_merge_descriptor_image_copy_range", source)
        for descriptor_marker in [
            "case VK_DESCRIPTOR_TYPE_STORAGE_IMAGE:",
            "case VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE:",
            "case VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER:",
            "case VK_DESCRIPTOR_TYPE_INPUT_ATTACHMENT:",
        ]:
            self.assertIn(descriptor_marker, source)
        self.assertIn("VK_IMAGE_ASPECT_COLOR_BIT", source)
        self.assertIn("vulkan_format_has_depth_aspect", source)
        self.assertIn("vulkan_format_has_stencil_aspect", source)
        self.assertIn("vulkan_image_single_aspect_supported_for_format", source)
        self.assertIn("vulkan_image_single_aspect_supported_for_format(image->format, required)", source)
        self.assertIn("if ((aspect_mask & (aspect_mask - 1u)) != 0) return 0;", source)
        self.assertIn("VK_FORMAT_D24_UNORM_S8_UINT", source)
        self.assertIn("VK_FORMAT_D32_SFLOAT_S8_UINT", source)
        self.assertGreaterEqual(source.count("vulkan_graphics_merge_image_copy_range_for_aspect("), 5)
        self.assertEqual(source.count("vulkan_graphics_merge_attachment_copy_range("), 3)
        self.assertEqual(source.count("vulkan_graphics_merge_descriptor_image_copy_range("), 2)
        bodyless = re.sub(
            r"static int vulkan_graphics_merge_image_copy_range_for_aspect\(.*?\n}\n\n",
            "",
            source,
            flags=re.S,
        )
        bodyless = re.sub(
            r"static int vulkan_dispatch_merge_image_copy_range_for_aspect\(.*?\n}\n\n",
            "",
            bodyless,
            flags=re.S,
        )
        self.assertNotIn("vulkan_graphics_merge_color_copy_range", source)
        self.assertNotIn("vulkan_graphics_attachment_range_supported", source)
        self.assertNotIn("range->levelCount > image->mip_levels - range->baseMipLevel", bodyless)
        self.assertNotIn("replay_view->range.levelCount >", bodyless)

    def test_vulkan_graphics_descriptor_image_upload_is_aspect_aware(self):
        executor = GPU_EXECUTOR.read_text()
        descriptor_copy_helper = executor.split(
            "static int vulkan_graphics_merge_descriptor_image_copy_range", 1
        )[1].split("static int vulkan_graphics_barrier_queue_family_replayable", 1)[0]
        descriptor_aspect_helper = executor.split(
            "static int vulkan_graphics_descriptor_image_aspect_supported", 1
        )[1].split("static int vulkan_graphics_merge_descriptor_image_copy_range", 1)[0]
        staged_body = executor.split(
            "static int record_vulkan_graphics_v6_staged_image_uploads", 1
        )[1].split("static int graphics_push_metadata_for_command", 1)[0]
        record_body = executor.split(
            "static int record_vulkan_graphics_v6_command_buffer", 1
        )[1].split("static int submit_vulkan_graphics_v6_command_buffer", 1)[0]
        bind_body = record_body.split(
            "case PDOCKER_GPU_GRAPHICS_V6_COMMAND_BIND_DESCRIPTOR_SETS:", 1
        )[1].split("case PDOCKER_GPU_GRAPHICS_V6_COMMAND_COPY_BUFFER:", 1)[0]
        layout_body = executor.split(
            "static int vulkan_image_descriptor_read_only_layout_valid", 1
        )[1].split("static VkImageUsageFlags vulkan_required_usage_for_image_descriptor", 1)[0]

        self.assertIn("range->aspectMask", descriptor_aspect_helper)
        self.assertIn("vulkan_image_single_aspect_supported_for_format(image->format, aspect)", descriptor_aspect_helper)
        self.assertIn("descriptor_type == VK_DESCRIPTOR_TYPE_STORAGE_IMAGE", descriptor_aspect_helper)
        self.assertIn("aspect != VK_IMAGE_ASPECT_COLOR_BIT", descriptor_aspect_helper)
        self.assertIn("vulkan_graphics_descriptor_image_aspect_supported", descriptor_copy_helper)
        self.assertIn("vulkan_graphics_merge_image_copy_range_for_aspect(image, range, aspect)", descriptor_copy_helper)
        self.assertNotIn("image, range, VK_IMAGE_ASPECT_COLOR_BIT", descriptor_copy_helper)

        self.assertIn("vulkan_image_tight_subresource_offset_for_aspect", staged_body)
        self.assertIn("vulkan_image_tight_copy_size_for_aspect", staged_body)
        self.assertIn("copy_size > (uint64_t)image->staging.size - buffer_offset", staged_body)
        self.assertIn(".aspectMask = copy_aspect", staged_body)

        self.assertIn("vulkan_graphics_attachment_writeback_access_mask(view_obj->range.aspectMask)", bind_body)
        self.assertIn("vulkan_graphics_attachment_writeback_stage_mask(view_obj->range.aspectMask)", bind_body)
        self.assertNotIn("VK_PIPELINE_STAGE_HOST_BIT |\n                    VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT", bind_body)
        self.assertNotIn("VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT | VK_ACCESS_SHADER_WRITE_BIT", bind_body)

        self.assertIn("VK_IMAGE_LAYOUT_DEPTH_STENCIL_READ_ONLY_OPTIMAL", layout_body)
        self.assertIn("VK_IMAGE_LAYOUT_DEPTH_READ_ONLY_OPTIMAL", layout_body)
        self.assertIn("VK_IMAGE_LAYOUT_STENCIL_READ_ONLY_OPTIMAL", layout_body)

    def test_vulkan_graphics_depth_stencil_writeback_bounds_are_aspect_aware(self):
        source = GPU_EXECUTOR.read_text()
        bpp_helper = source.split(
            "static uint32_t vulkan_format_bytes_per_pixel_for_aspect", 1
        )[1].split("static int vulkan_image_mip_extent", 1)[0]
        writeback = source.split(
            "static int record_vulkan_graphics_v6_attachment_writeback_commands", 1
        )[1].split("static int writeback_vulkan_graphics_v6_attachments", 1)[0]
        for marker in [
            "VK_FORMAT_D16_UNORM",
            "VK_FORMAT_D32_SFLOAT",
            "VK_FORMAT_S8_UINT",
            "VK_FORMAT_D24_UNORM_S8_UINT",
            "VK_FORMAT_D32_SFLOAT_S8_UINT",
        ]:
            self.assertIn(marker, bpp_helper)
        self.assertIn("aspect_mask == VK_IMAGE_ASPECT_DEPTH_BIT", bpp_helper)
        self.assertIn("aspect_mask == VK_IMAGE_ASPECT_STENCIL_BIT", bpp_helper)
        self.assertIn("vulkan_image_tight_subresource_offset_for_aspect", writeback)
        self.assertIn("vulkan_image_tight_copy_size_for_aspect", writeback)
        self.assertIn("copy_size > (uint64_t)image->staging.size - buffer_offset", writeback)
        self.assertIn("copy_aspect, &copy_size", writeback)

    def test_vulkan_graphics_v62_specialization_metadata_is_append_only(self):
        abi = APP_HEADER.read_text()
        container_abi = CONTAINER_HEADER.read_text()
        executor = GPU_EXECUTOR.read_text()
        icd = VULKAN_ICD.read_text()
        for source in [abi, container_abi]:
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V62_ABI_MINOR 2u", source)
            self.assertIn("PdockerGpuVulkanGraphicsV62FrameHeader", source)
            self.assertIn("PdockerGpuVulkanGraphicsV62SpecializationEntry", source)
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V62_SPECIALIZATION_ENTRY_SCHEMA_HASH", source)
            self.assertIn("shader_stage_index", source)
            self.assertIn("constant_id", source)
        validator = executor.split("static int validate_vulkan_graphics_v6_header_prefix", 1)[1].split(
            "if (header->frame_size < header->header_size", 1
        )[0]
        self.assertIn("header->abi_minor == PDOCKER_GPU_VULKAN_GRAPHICS_V62_ABI_MINOR", validator)
        self.assertIn("sizeof(PdockerGpuVulkanGraphicsV62FrameHeader)", validator)
        self.assertIn("header_v62->v62.specialization_entry_count", executor)
        self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V62_SPECIALIZATION_ENTRY_SCHEMA_HASH", executor)
        self.assertIn("collect_vulkan_graphics_v62_specialization_entries", executor)
        self.assertIn("VkSpecializationInfo specialization_infos", executor)
        self.assertIn(".pSpecializationInfo = specialization_info", executor)
        self.assertIn("graphics shader specialization replay requires V6.2 metadata", executor)
        self.assertNotIn("graphics shader specialization replay is not implemented", executor)
        self.assertIn("graphics_stage_specialization_entries", icd)
        self.assertIn("stage->pSpecializationInfo", icd)
        self.assertIn("need_v62_specialization", icd)
        self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V62_ABI_MINOR", icd)
        self.assertIn("specialization_entry_table_hash", icd)
        self.assertIn("frame + header->header_size", icd)
        self.assertIn("cursor - header->header_size", icd)
        self.assertNotIn("frame + sizeof(*frame_header)", icd)

    def test_vulkan_graphics_v67_static_viewport_scissor_metadata_is_append_only(self):
        abi = APP_HEADER.read_text()
        container_abi = CONTAINER_HEADER.read_text()
        executor = GPU_EXECUTOR.read_text()
        icd = VULKAN_ICD.read_text()
        for source in [abi, container_abi]:
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V67_ABI_MINOR 7u", source)
            self.assertIn("PdockerGpuVulkanGraphicsV67FrameHeader", source)
            self.assertIn("PdockerGpuVulkanGraphicsV67ViewportScissorStateEntry", source)
            self.assertIn("PdockerGpuVulkanGraphicsV67ViewportEntry", source)
            self.assertIn("PdockerGpuVulkanGraphicsV67ScissorEntry", source)
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V67_VIEWPORT_SCISSOR_STATE_SCHEMA_HASH", source)
            self.assertIn("PDOCKER_GPU_GRAPHICS_V67_VIEWPORT_STATIC_PRESENT", source)
            self.assertIn("PDOCKER_GPU_GRAPHICS_V67_SCISSOR_STATIC_PRESENT", source)
        self.assertIn("header->abi_minor == PDOCKER_GPU_VULKAN_GRAPHICS_V67_ABI_MINOR", executor)
        self.assertIn("sizeof(PdockerGpuVulkanGraphicsV67FrameHeader)", executor)
        self.assertIn("FrameRange ranges[43]", executor)
        self.assertIn("find_vulkan_graphics_v67_viewport_scissor_state", executor)
        self.assertIn(".pViewports = dynamic_viewport ? NULL : static_viewports", executor)
        self.assertIn(".pScissors = dynamic_scissor ? NULL : static_scissors", executor)
        self.assertIn("need_v67_viewport_scissor_state", icd)
        self.assertIn("VULKAN_GRAPHICS_V6.7", icd)
        self.assertIn("viewport_scissor_state_table_hash", icd)

    def test_vulkan_graphics_v610_image_copy_metadata_is_append_only(self):
        abi = APP_HEADER.read_text()
        container_abi = CONTAINER_HEADER.read_text()
        executor = GPU_EXECUTOR.read_text()
        icd = VULKAN_ICD.read_text()
        for source in [abi, container_abi]:
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V610_ABI_MINOR 10u", source)
            self.assertIn("PdockerGpuVulkanGraphicsV610FrameHeader", source)
            self.assertIn("PdockerGpuVulkanGraphicsV610BufferImageCopyEntry", source)
            self.assertIn("PdockerGpuVulkanGraphicsV610ImageCopyEntry", source)
            self.assertIn("PDOCKER_GPU_GRAPHICS_V6_COMMAND_COPY_BUFFER_TO_IMAGE", source)
            self.assertIn("PDOCKER_GPU_GRAPHICS_V6_COMMAND_COPY_IMAGE_TO_BUFFER", source)
            self.assertIn("PDOCKER_GPU_GRAPHICS_V6_COMMAND_COPY_IMAGE", source)
            self.assertIn("PDOCKER_GPU_GRAPHICS_V610_BUFFER_IMAGE_COPY_DIRECTION_BUFFER_TO_IMAGE", source)
            self.assertIn("PDOCKER_GPU_GRAPHICS_V610_BUFFER_IMAGE_COPY_DIRECTION_IMAGE_TO_BUFFER", source)
        for marker in [
            "sizeof(PdockerGpuVulkanGraphicsV610FrameHeader)",
            "FrameRange ranges[43]",
            "header_v610->v610.buffer_image_copy_count",
            "header_v610->v610.image_copy_count",
            "find_vulkan_graphics_v610_buffer_image_copy",
            "find_vulkan_graphics_v610_image_copy",
            "vkCmdCopyBufferToImage(command_buffer",
            "vkCmdCopyImageToBuffer(command_buffer",
            "vkCmdCopyImage(command_buffer",
            "vulkan_graphics_v610_buffer_image_copy_span",
            "vulkan_format_bytes_per_pixel_for_aspect((VkFormat)image->format, (VkImageAspectFlags)copy->aspect_mask)",
            "vulkan_graphics_v610_image_subresource_range_valid",
            "entry->src_aspect_mask != entry->dst_aspect_mask",
        ]:
            self.assertIn(marker, executor)
        for marker in [
            "need_v610_image_copy",
            "PdockerGpuVulkanGraphicsV610FrameHeader",
            "PDOCKER_GPU_GRAPHICS_V6_COMMAND_COPY_BUFFER_TO_IMAGE",
            "PDOCKER_GPU_GRAPHICS_V6_COMMAND_COPY_IMAGE_TO_BUFFER",
            "PDOCKER_GPU_GRAPHICS_V6_COMMAND_COPY_IMAGE",
            "vkCmdCopyBufferToImage",
            "vkCmdCopyImageToBuffer",
            "vkCmdCopyImage",
        ]:
            self.assertIn(marker, icd)

    def test_vulkan_graphics_v610_depth_stencil_copy_has_explicit_plane_staging_boundary(self):
        executor = GPU_EXECUTOR.read_text()
        icd = VULKAN_ICD.read_text()
        # Vulkan buffer<->image copy is single-aspect by valid usage; do not
        # split packed depth/stencil buffer traffic without explicit repacking.
        self.assertIn(
            "!pdocker_vk_image_single_aspect_supported_for_format(copy__->image->format, copy__->region.imageSubresource.aspectMask)",
            icd,
        )
        self.assertNotIn("pdocker_vk_buffer_image_depth_stencil_pack_supported", icd)
        self.assertNotIn("vulkan_graphics_v610_buffer_image_copy_is_packed_depth_stencil", executor)
        self.assertNotIn("packed_depth_stencil_scratch", executor)
        self.assertNotIn("vulkan-graphics-v6-packed-depth-stencil-buffer-pack", executor)
        # Image<->image D|S is now producer-split into per-aspect V6.10
        # COPY_IMAGE commands.  This is generic Vulkan image-copy handling, not
        # a llama-specific shader workaround.
        self.assertIn("pdocker_vk_image_to_image_depth_stencil_split_supported", icd)
        copy_collect = icd.split(
            "if (op__->type == PDOCKER_VK_COMMAND_IMAGE_TO_IMAGE_COPY)", 1
        )[1].split("} \\n        } \\n    } while (0)", 1)[0]
        self.assertIn("VkImageAspectFlags split_aspects__[2]", copy_collect)
        self.assertIn("copy_aspect_count__", copy_collect)
        self.assertIn("split_aspects__[0] = VK_IMAGE_ASPECT_DEPTH_BIT", copy_collect)
        self.assertIn("split_aspects__[1] = VK_IMAGE_ASPECT_STENCIL_BIT", copy_collect)
        self.assertIn(
            "command_count > PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_COMMANDS - copy_aspect_count__",
            copy_collect,
        )
        self.assertIn(
            "image_copy_count > PDOCKER_GPU_VULKAN_GRAPHICS_V610_MAX_IMAGE_COPIES - copy_aspect_count__",
            copy_collect,
        )
        self.assertIn("for (uint32_t copy_aspect_i__", copy_collect)
        self.assertIn(
            "copy_entry__->src_aspect_mask = (uint32_t)split_aspects__[copy_aspect_i__]",
            copy_collect,
        )
        self.assertIn(
            "copy_entry__->dst_aspect_mask = (uint32_t)split_aspects__[copy_aspect_i__]",
            copy_collect,
        )
        merge_body = executor.split(
            "static int vulkan_graphics_merge_image_copy_range_for_aspect", 1
        )[1].split("static int vulkan_graphics_merge_attachment_copy_range", 1)[0]
        self.assertIn("image->copy_aspect_mask |= required", merge_body)
        self.assertIn("!vulkan_format_is_packed_depth_stencil(image->format)", merge_body)
        self.assertNotIn("image->copy_aspect_mask != required", merge_body)
        self.assertIn("vulkan_image_staging_allocation_size", executor)
        self.assertIn("vulkan_image_unpack_packed_depth_stencil_to_planes", executor)
        self.assertIn("vulkan_image_pack_packed_depth_stencil_from_planes", executor)
        self.assertIn("vulkan_image_copy_aspect_list", executor)
        staged_body = executor.split(
            "static int record_vulkan_graphics_v6_staged_image_uploads", 1
        )[1].split("static int graphics_push_metadata_for_command", 1)[0]
        writeback_body = executor.split(
            "static int record_vulkan_graphics_v6_attachment_writeback_commands", 1
        )[1].split("static int writeback_vulkan_graphics_v6_attachments", 1)[0]
        for body in [staged_body, writeback_body]:
            self.assertIn("VkImageAspectFlags copy_aspects[3]", body)
            self.assertIn("VkImageAspectFlags copy_aspect = copy_aspects[aspect_i]", body)
            self.assertIn(".aspectMask = copy_aspect", body)
            self.assertIn(".size = (VkDeviceSize)image->staging.size", body)

    def test_vulkan_graphics_v611_fill_update_buffer_metadata_is_append_only(self):
        abi = APP_HEADER.read_text()
        container_abi = CONTAINER_HEADER.read_text()
        executor = GPU_EXECUTOR.read_text()
        icd = VULKAN_ICD.read_text()
        for source in [abi, container_abi]:
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V611_ABI_MINOR 11u", source)
            self.assertIn("PdockerGpuVulkanGraphicsV611FrameHeader", source)
            self.assertIn("PdockerGpuVulkanGraphicsV611HeaderExtension", source)
            self.assertIn("PdockerGpuVulkanGraphicsV611FillBufferEntry", source)
            self.assertIn("PdockerGpuVulkanGraphicsV611UpdateBufferEntry", source)
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V611_HEADER_EXTENSION_FIELDS", source)
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V611_FILL_BUFFER_FIELDS", source)
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V611_UPDATE_BUFFER_FIELDS", source)
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V611_HEADER_EXTENSION_SCHEMA_HASH", source)
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V611_FILL_BUFFER_SCHEMA_HASH", source)
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V611_UPDATE_BUFFER_SCHEMA_HASH", source)
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V611_MAX_FILL_BUFFERS", source)
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V611_MAX_UPDATE_BUFFERS", source)
            self.assertIn("PDOCKER_GPU_GRAPHICS_V6_COMMAND_FILL_BUFFER", source)
            self.assertIn("PDOCKER_GPU_GRAPHICS_V6_COMMAND_UPDATE_BUFFER", source)
        for marker in [
            "sizeof(PdockerGpuVulkanGraphicsV611FrameHeader)",
            "PdockerGpuVulkanGraphicsV611FrameHeader",
            "header_v611->v611.fill_buffer_count",
            "header_v611->v611.update_buffer_count",
            "PdockerGpuVulkanGraphicsV611FillBufferEntry *fill_buffers",
            "PdockerGpuVulkanGraphicsV611UpdateBufferEntry *update_buffers",
            "find_vulkan_graphics_v611_fill_buffer",
            "find_vulkan_graphics_v611_update_buffer",
            "PDOCKER_GPU_GRAPHICS_V6_COMMAND_FILL_BUFFER",
            "PDOCKER_GPU_GRAPHICS_V6_COMMAND_UPDATE_BUFFER",
            "VK_BUFFER_USAGE_TRANSFER_DST_BIT",
            "VK_ACCESS_TRANSFER_WRITE_BIT",
            "VK_PIPELINE_STAGE_TRANSFER_BIT",
            "vkCmdFillBuffer(command_buffer",
            "vkCmdUpdateBuffer(command_buffer",
            "mark_vulkan_graphics_replay_buffer_writeback_range",
        ]:
            self.assertIn(marker, executor)
        for marker in [
            "PDOCKER_VK_COMMAND_FILL",
            "PDOCKER_VK_COMMAND_UPDATE",
            "vkCmdFillBuffer",
            "vkCmdUpdateBuffer",
            "need_v611_buffer_write",
            "pre_need_v611_buffer_write",
            "VULKAN_GRAPHICS_V6.11",
            "APPEND_INTERLEAVED_GRAPHICS_BUFFER_COPIES",
            "command_op_is_graphics_interleavable_transfer_op",
            "case PDOCKER_VK_COMMAND_FILL:",
            "case PDOCKER_VK_COMMAND_UPDATE:",
            "PDOCKER_GPU_GRAPHICS_V6_COMMAND_FILL_BUFFER",
            "PDOCKER_GPU_GRAPHICS_V6_COMMAND_UPDATE_BUFFER",
            "execute_recorded_fill_op(op)",
            "execute_recorded_update_op(op)",
            "graphics_record_requires_submit_frame",
        ]:
            self.assertIn(marker, icd)

    def test_vulkan_graphics_v612_clear_color_image_metadata_is_append_only(self):
        abi = APP_HEADER.read_text()
        container_abi = CONTAINER_HEADER.read_text()
        executor = GPU_EXECUTOR.read_text()
        icd = VULKAN_ICD.read_text()
        for source in [abi, container_abi]:
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V612_ABI_MINOR 12u", source)
            self.assertIn("PdockerGpuVulkanGraphicsV612FrameHeader", source)
            self.assertIn("PdockerGpuVulkanGraphicsV612HeaderExtension", source)
            self.assertIn("PdockerGpuVulkanGraphicsV612ClearColorImageEntry", source)
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V612_HEADER_EXTENSION_FIELDS", source)
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V612_CLEAR_COLOR_IMAGE_FIELDS", source)
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V612_HEADER_EXTENSION_SCHEMA_HASH", source)
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V612_CLEAR_COLOR_IMAGE_SCHEMA_HASH", source)
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V612_MAX_CLEAR_COLOR_IMAGES", source)
            self.assertIn("PDOCKER_GPU_GRAPHICS_V6_COMMAND_CLEAR_COLOR_IMAGE", source)
        for marker in [
            "sizeof(PdockerGpuVulkanGraphicsV612FrameHeader)",
            "FrameRange ranges[43]",
            "header_v612->v612.clear_color_image_count",
            "PdockerGpuVulkanGraphicsV612ClearColorImageEntry *clear_color_images",
            "find_vulkan_graphics_v612_clear_color_image",
            "PDOCKER_GPU_GRAPHICS_V6_COMMAND_CLEAR_COLOR_IMAGE",
            "VK_IMAGE_USAGE_TRANSFER_DST_BIT",
            "VK_ACCESS_TRANSFER_WRITE_BIT",
            "VK_PIPELINE_STAGE_TRANSFER_BIT",
            "vkCmdClearColorImage(command_buffer",
            "record_vulkan_graphics_v6_staged_image_uploads(command_buffer, attachments)",
        ]:
            self.assertIn(marker, executor)
        for marker in [
            "PDOCKER_VK_COMMAND_CLEAR_COLOR_IMAGE",
            "vkCmdClearColorImage",
            "need_v612_clear_color",
            "pre_need_v612_clear_color",
            "VULKAN_GRAPHICS_V6.12",
            "PdockerGpuVulkanGraphicsV612FrameHeader",
            "PdockerGpuVulkanGraphicsV612ClearColorImageEntry",
            "PDOCKER_GPU_GRAPHICS_V6_COMMAND_CLEAR_COLOR_IMAGE",
            "execute_recorded_clear_color_image_op",
            "normalize_image_subresource_range",
            "VK_REMAINING_MIP_LEVELS",
            "VK_REMAINING_ARRAY_LAYERS",
        ]:
            self.assertIn(marker, icd)

    def test_vulkan_graphics_v613_clear_depth_stencil_image_metadata_is_append_only(self):
        abi = APP_HEADER.read_text()
        container_abi = CONTAINER_HEADER.read_text()
        executor = GPU_EXECUTOR.read_text()
        icd = VULKAN_ICD.read_text()
        for source in [abi, container_abi]:
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V613_ABI_MINOR 13u", source)
            self.assertIn("PdockerGpuVulkanGraphicsV613FrameHeader", source)
            self.assertIn("PdockerGpuVulkanGraphicsV613HeaderExtension", source)
            self.assertIn("PdockerGpuVulkanGraphicsV613ClearDepthStencilImageEntry", source)
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V613_HEADER_EXTENSION_FIELDS", source)
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V613_CLEAR_DEPTH_STENCIL_IMAGE_FIELDS", source)
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V613_HEADER_EXTENSION_SCHEMA_HASH", source)
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V613_CLEAR_DEPTH_STENCIL_IMAGE_SCHEMA_HASH", source)
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V613_MAX_CLEAR_DEPTH_STENCIL_IMAGES", source)
            self.assertIn("PDOCKER_GPU_GRAPHICS_V6_COMMAND_CLEAR_DEPTH_STENCIL_IMAGE", source)
            self.assertIn("depth_bits", source)
            self.assertIn("stencil", source)
        for marker in [
            "sizeof(PdockerGpuVulkanGraphicsV613FrameHeader)",
            "FrameRange ranges[43]",
            "header_v613->v613.clear_depth_stencil_image_count",
            "PdockerGpuVulkanGraphicsV613ClearDepthStencilImageEntry *clear_depth_stencil_images",
            "find_vulkan_graphics_v613_clear_depth_stencil_image",
            "PDOCKER_GPU_GRAPHICS_V6_COMMAND_CLEAR_DEPTH_STENCIL_IMAGE",
            "VK_IMAGE_USAGE_TRANSFER_DST_BIT",
            "VK_ACCESS_TRANSFER_WRITE_BIT",
            "VK_PIPELINE_STAGE_TRANSFER_BIT",
            "vkCmdClearDepthStencilImage(command_buffer",
            "clear depth stencil image inside dynamic rendering is not supported",
            "graphics clear depth stencil image requires V6.13 metadata",
            "clear->aspect_mask",
        ]:
            self.assertIn(marker, executor)
        for marker in [
            "PDOCKER_VK_COMMAND_CLEAR_DEPTH_STENCIL_IMAGE",
            "vkCmdClearDepthStencilImage",
            "need_v613_clear_depth_stencil",
            "pre_need_v613_clear_depth_stencil",
            "VULKAN_GRAPHICS_V6.13",
            "PdockerGpuVulkanGraphicsV613FrameHeader",
            "PdockerGpuVulkanGraphicsV613ClearDepthStencilImageEntry",
            "PDOCKER_GPU_GRAPHICS_V6_COMMAND_CLEAR_DEPTH_STENCIL_IMAGE",
            "execute_recorded_clear_depth_stencil_image_op",
            "VK_IMAGE_ASPECT_DEPTH_BIT",
            "VK_IMAGE_ASPECT_STENCIL_BIT",
            "memcpy(&depth_bits__",
            "normalize_image_subresource_range",
            "VK_REMAINING_MIP_LEVELS",
            "VK_REMAINING_ARRAY_LAYERS",
            "split_aspects__",
            "clear_aspect_count__",
            "PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_COMMANDS - clear_aspect_count__",
            "PDOCKER_GPU_VULKAN_GRAPHICS_V613_MAX_CLEAR_DEPTH_STENCIL_IMAGES - clear_aspect_count__",
        ]:
            self.assertIn(marker, icd)

    def test_vulkan_graphics_v614_resolve_image_metadata_is_append_only(self):
        abi = APP_HEADER.read_text()
        container_abi = CONTAINER_HEADER.read_text()
        executor = GPU_EXECUTOR.read_text()
        icd = VULKAN_ICD.read_text()
        for source in [abi, container_abi]:
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V614_ABI_MINOR 14u", source)
            self.assertIn("PdockerGpuVulkanGraphicsV614FrameHeader", source)
            self.assertIn("PdockerGpuVulkanGraphicsV614HeaderExtension", source)
            self.assertIn("PdockerGpuVulkanGraphicsV614ResolveImageEntry", source)
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V614_HEADER_EXTENSION_FIELDS", source)
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V614_RESOLVE_IMAGE_FIELDS", source)
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V614_HEADER_EXTENSION_SCHEMA_HASH", source)
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V614_RESOLVE_IMAGE_SCHEMA_HASH", source)
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V614_MAX_RESOLVE_IMAGES", source)
            self.assertIn("PDOCKER_GPU_GRAPHICS_V6_COMMAND_RESOLVE_IMAGE", source)
        for marker in [
            "sizeof(PdockerGpuVulkanGraphicsV614FrameHeader)",
            "FrameRange ranges[43]",
            "header_v614->v614.resolve_image_count",
            "PdockerGpuVulkanGraphicsV614ResolveImageEntry *resolve_images",
            "find_vulkan_graphics_v614_resolve_image",
            "PDOCKER_GPU_GRAPHICS_V6_COMMAND_RESOLVE_IMAGE",
            "VK_IMAGE_USAGE_TRANSFER_SRC_BIT",
            "VK_IMAGE_USAGE_TRANSFER_DST_BIT",
            "vkCmdResolveImage(command_buffer",
            "vulkan_graphics_v614_resolve_runtime_eligible",
            "resolve image inside dynamic rendering is not supported",
            "graphics resolve image requires V6.14 metadata",
            "resolve->src_aspect_mask",
        ]:
            self.assertIn(marker, executor)
        for marker in [
            "PDOCKER_VK_COMMAND_RESOLVE_IMAGE",
            "vkCmdResolveImage",
            "need_v614_resolve_image",
            "pre_need_v614_resolve_image",
            "VULKAN_GRAPHICS_V6.14",
            "PdockerGpuVulkanGraphicsV614FrameHeader",
            "PdockerGpuVulkanGraphicsV614ResolveImageEntry",
            "PDOCKER_GPU_GRAPHICS_V6_COMMAND_RESOLVE_IMAGE",
            "execute_recorded_resolve_image_op",
            "pdocker_vk_resolve_image_executor_eligible",
            "VK_IMAGE_USAGE_TRANSFER_SRC_BIT",
            "VK_IMAGE_USAGE_TRANSFER_DST_BIT",
            "image_mip_extent(resolve__->src",
            "resolve_image_table_hash",
        ]:
            self.assertIn(marker, icd)


    def test_vulkan_graphics_v615_blit_image_metadata_is_append_only(self):
        abi = APP_HEADER.read_text()
        container_abi = CONTAINER_HEADER.read_text()
        executor = GPU_EXECUTOR.read_text()
        icd = VULKAN_ICD.read_text()
        for source in [abi, container_abi]:
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V615_ABI_MINOR 15u", source)
            self.assertIn("PdockerGpuVulkanGraphicsV615FrameHeader", source)
            self.assertIn("PdockerGpuVulkanGraphicsV615HeaderExtension", source)
            self.assertIn("PdockerGpuVulkanGraphicsV615BlitImageEntry", source)
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V615_HEADER_EXTENSION_FIELDS", source)
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V615_BLIT_IMAGE_FIELDS", source)
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V615_HEADER_EXTENSION_SCHEMA_HASH", source)
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V615_BLIT_IMAGE_SCHEMA_HASH", source)
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V615_MAX_BLIT_IMAGES", source)
            self.assertIn("PDOCKER_GPU_GRAPHICS_V6_COMMAND_BLIT_IMAGE", source)
            self.assertIn("src_offset0_x", source)
            self.assertIn("dst_offset1_z", source)
            self.assertIn("filter", source)
        for marker in [
            "sizeof(PdockerGpuVulkanGraphicsV615FrameHeader)",
            "FrameRange ranges[43]",
            "header_v615->v615.blit_image_count",
            "PdockerGpuVulkanGraphicsV615BlitImageEntry *blit_images",
            "find_vulkan_graphics_v615_blit_image",
            "PDOCKER_GPU_GRAPHICS_V6_COMMAND_BLIT_IMAGE",
            "vulkan_graphics_v615_image_blit_subresource_valid",
            "VK_IMAGE_USAGE_TRANSFER_SRC_BIT",
            "VK_IMAGE_USAGE_TRANSFER_DST_BIT",
            "vkCmdBlitImage(command_buffer",
            "vulkan_graphics_v615_blit_runtime_eligible",
            "blit image inside dynamic rendering is not supported",
            "graphics blit image requires V6.15 metadata",
            "blit->src_offset0_x",
            "blit_image_table_hash",
        ]:
            self.assertIn(marker, executor)
        for marker in [
            "PDOCKER_VK_COMMAND_BLIT_IMAGE",
            "vkCmdBlitImage",
            "need_v615_blit_image",
            "pre_need_v615_blit_image",
            "VULKAN_GRAPHICS_V6.15",
            "PdockerGpuVulkanGraphicsV615FrameHeader",
            "PdockerGpuVulkanGraphicsV615BlitImageEntry",
            "PDOCKER_GPU_GRAPHICS_V6_COMMAND_BLIT_IMAGE",
            "execute_recorded_blit_image_op",
            "pdocker_vk_blit_image_executor_eligible",
            "VK_IMAGE_USAGE_TRANSFER_SRC_BIT",
            "VK_IMAGE_USAGE_TRANSFER_DST_BIT",
            "image_mip_extent(blit__->src",
            "blit_image_table_hash",
        ]:
            self.assertIn(marker, icd)

    def test_vulkan_graphics_v614_v615_replay_is_gated_by_advertised_caps(self):
        icd = VULKAN_ICD.read_text()
        executor = GPU_EXECUTOR.read_text()

        for marker in [
            "pdocker_vk_resolve_image_executor_eligible",
            "pdocker_vk_blit_image_executor_eligible",
        ]:
            self.assertIn(marker, icd)

        format_props = icd.split(
            "VKAPI_ATTR void VKAPI_CALL vkGetPhysicalDeviceFormatProperties", 1
        )[1].split(
            "VKAPI_ATTR VkResult VKAPI_CALL vkGetPhysicalDeviceImageFormatProperties", 1
        )[0]
        image_props = icd.split(
            "VKAPI_ATTR VkResult VKAPI_CALL vkGetPhysicalDeviceImageFormatProperties", 1
        )[1].split(
            "VKAPI_ATTR void VKAPI_CALL vkGetPhysicalDeviceSparseImageFormatProperties", 1
        )[0]
        self.assertIn("VkSampleCountFlags sample_counts = pdocker_vk_image_sample_counts_for_request(", image_props)
        self.assertIn("pImageFormatProperties->sampleCounts = sample_counts;", image_props)
        self.assertNotIn("VK_FORMAT_FEATURE_BLIT_SRC_BIT", format_props)
        self.assertNotIn("VK_FORMAT_FEATURE_BLIT_DST_BIT", format_props)
        self.assertNotIn("VK_FORMAT_FEATURE_SAMPLED_IMAGE_FILTER_LINEAR_BIT", format_props)

        resolve_collect = icd.split(
            "if (op__->type == PDOCKER_VK_COMMAND_RESOLVE_IMAGE)", 1
        )[1].split(
            "if (op__->type == PDOCKER_VK_COMMAND_BLIT_IMAGE)", 1
        )[0]
        self.assertIn("!pdocker_vk_resolve_image_executor_eligible(resolve__)", resolve_collect)
        self.assertNotIn("pdocker_vk_blit_image_executor_eligible", resolve_collect)

        blit_collect = icd.split(
            "if (op__->type == PDOCKER_VK_COMMAND_BLIT_IMAGE)", 1
        )[1].split(
            "PdockerGpuVulkanGraphicsV6CommandEntry *blit_command__", 1
        )[0]
        self.assertIn("!pdocker_vk_blit_image_executor_eligible(blit__)", blit_collect)
        self.assertNotIn("pdocker_vk_resolve_image_executor_eligible", blit_collect)

        copy_exec = icd.split(
            "static void execute_recorded_image_to_image_copy_op", 1
        )[1].split(
            "static void execute_recorded_resolve_image_op", 1
        )[0]
        self.assertNotIn("pdocker_vk_resolve_image_executor_eligible", copy_exec)
        self.assertNotIn("pdocker_vk_blit_image_executor_eligible", copy_exec)

        resolve_predicate = icd.split(
            "static bool pdocker_vk_resolve_image_executor_eligible", 1
        )[1].split(
            "static bool pdocker_vk_resolve_image_host_fallback_eligible", 1
        )[0]
        for marker in [
            "op->src->samples == VK_SAMPLE_COUNT_1_BIT",
            "op->dst->samples != VK_SAMPLE_COUNT_1_BIT",
            "VK_IMAGE_ASPECT_COLOR_BIT",
            "VK_FORMAT_FEATURE_COLOR_ATTACHMENT_BIT",
            "VK_FORMAT_FEATURE_TRANSFER_SRC_BIT",
            "VK_FORMAT_FEATURE_TRANSFER_DST_BIT",
            "image_mip_extent(op->src",
            "image_mip_extent(op->dst",
        ]:
            self.assertIn(marker, resolve_predicate)
        self.assertNotIn("(void)op;", resolve_predicate)

        resolve_exec = icd.split(
            "static void execute_recorded_resolve_image_op", 1
        )[1].split(
            "static uint32_t blit_axis_extent", 1
        )[0]
        self.assertIn("!pdocker_vk_resolve_image_host_fallback_eligible(op)", resolve_exec)
        self.assertNotIn("pdocker_vk_resolve_image_executor_eligible(op)", resolve_exec)
        self.assertNotIn("pdocker_vk_blit_image_executor_eligible", resolve_exec)

        executor_frame_body = icd.split(
            "static bool command_op_requires_graphics_executor_frame", 1
        )[1].split(
            "static bool command_buffer_has_executor_frame_content_in_sequence_range", 1
        )[0]
        self.assertIn("case PDOCKER_VK_COMMAND_RESOLVE_IMAGE:", executor_frame_body)
        self.assertIn("pdocker_vk_resolve_image_executor_eligible(&cmd->image_resolve_ops[op->index])", executor_frame_body)
        self.assertIn("case PDOCKER_VK_COMMAND_BLIT_IMAGE:", executor_frame_body)
        self.assertIn("pdocker_vk_blit_image_executor_eligible(&cmd->image_blit_ops[op->index])", executor_frame_body)

        frame_content_body = icd.split(
            "static bool command_buffer_has_executor_frame_content_in_sequence_range", 1
        )[1].split(
            "static int send_recorded_vulkan_graphics_v6_1_frame_range", 1
        )[0]
        self.assertIn("command_buffer_has_graphics_records_in_sequence_range", frame_content_body)
        self.assertIn("command_op_requires_graphics_executor_frame(cmd, op_index)", frame_content_body)

        blit_exec = icd.split(
            "static void execute_recorded_blit_image_op", 1
        )[1].split(
            "static void execute_recorded_clear_depth_stencil_image_op", 1
        )[0]
        self.assertIn("!pdocker_vk_blit_image_executor_eligible(op)", blit_exec)
        self.assertNotIn("pdocker_vk_resolve_image_executor_eligible", blit_exec)

        for marker in [
            "vulkan_graphics_runtime_format_bridge_supported",
            "vulkan_graphics_runtime_format_features",
            "vulkan_graphics_v614_resolve_runtime_eligible",
            "vulkan_graphics_v615_blit_runtime_eligible",
        ]:
            self.assertIn(marker, executor)

        transport_format = executor.split(
            "static VkFormatFeatureFlags vulkan_graphics_transport_format_features(VkFormat format) {", 1
        )[1].split(
            "static VkFormatFeatureFlags vulkan_graphics_legacy_format_features(VkFormat format) {", 1
        )[0]
        self.assertIn("if (!vulkan_graphics_runtime_format_bridge_supported(format)) return 0;", transport_format)
        self.assertIn("VK_FORMAT_FEATURE_BLIT_SRC_BIT", transport_format)
        self.assertIn("VK_FORMAT_FEATURE_BLIT_DST_BIT", transport_format)
        self.assertIn("VK_FORMAT_FEATURE_SAMPLED_IMAGE_FILTER_LINEAR_BIT", transport_format)
        legacy_format = executor.split(
            "static VkFormatFeatureFlags vulkan_graphics_legacy_format_features(VkFormat format) {", 1
        )[1].split(
            "static VkFormatFeatureFlags vulkan_graphics_runtime_format_features(VkFormat format) {", 1
        )[0]
        self.assertIn("features &= ~(VK_FORMAT_FEATURE_BLIT_SRC_BIT", legacy_format)
        runtime_format = executor.split(
            "static VkFormatFeatureFlags vulkan_graphics_runtime_format_features(VkFormat format) {", 1
        )[1].split(
            "static VkSampleCountFlags vulkan_graphics_supported_sample_count_mask", 1
        )[0]
        self.assertIn("vkGetPhysicalDeviceFormatProperties(g_vulkan_runtime.physical_device, format, &props);", runtime_format)
        self.assertIn("return props.optimalTilingFeatures & transport;", runtime_format)

        validation_body = executor.split(
            "static int validate_vulkan_graphics_v6_frame_content", 1
        )[1].split(
            "static int vulkan_graphics_v6_frame_needs_dynamic_rendering", 1
        )[0]
        self.assertNotIn("vulkan_graphics_v614_resolve_runtime_eligible", validation_body)
        self.assertNotIn("vulkan_graphics_v615_blit_runtime_eligible", validation_body)
        self.assertNotIn("vkGetPhysicalDeviceFormatProperties", validation_body)
        self.assertNotIn("init_vulkan_runtime", validation_body)

        record_body = executor.split(
            "static int record_vulkan_graphics_v6_command_buffer", 1
        )[1].split(
            "case PDOCKER_GPU_GRAPHICS_V6_COMMAND_SET_EVENT:", 1
        )[0]
        copy_image_case = record_body.split(
            "case PDOCKER_GPU_GRAPHICS_V6_COMMAND_COPY_IMAGE:", 1
        )[1].split(
            "case PDOCKER_GPU_GRAPHICS_V6_COMMAND_RESOLVE_IMAGE:", 1
        )[0]
        self.assertNotIn("vulkan_graphics_v614_resolve_runtime_eligible", copy_image_case)
        self.assertNotIn("vulkan_graphics_v615_blit_runtime_eligible", copy_image_case)

        resolve_case = record_body.split(
            "case PDOCKER_GPU_GRAPHICS_V6_COMMAND_RESOLVE_IMAGE:", 1
        )[1].split(
            "case PDOCKER_GPU_GRAPHICS_V6_COMMAND_BLIT_IMAGE:", 1
        )[0]
        self.assertIn("vulkan_graphics_v614_resolve_runtime_eligible", resolve_case)
        self.assertNotIn("vulkan_graphics_v615_blit_runtime_eligible", resolve_case)
        self.assertNotIn("blit))", resolve_case)

        blit_case = record_body.split(
            "case PDOCKER_GPU_GRAPHICS_V6_COMMAND_BLIT_IMAGE:", 1
        )[1]
        self.assertIn("vulkan_graphics_v615_blit_runtime_eligible", blit_case)
        self.assertNotIn("vulkan_graphics_v614_resolve_runtime_eligible", blit_case)
        self.assertNotIn("resolve))", blit_case)

    def test_vulkan_graphics_v616_clear_attachments_abi_is_append_only(self):
        abi = APP_HEADER.read_text()
        container_abi = CONTAINER_HEADER.read_text()
        executor = GPU_EXECUTOR.read_text()
        icd = VULKAN_ICD.read_text()
        for source in [abi, container_abi]:
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V616_ABI_MINOR 16u", source)
            self.assertIn("PdockerGpuVulkanGraphicsV616FrameHeader", source)
            self.assertIn("PdockerGpuVulkanGraphicsV616HeaderExtension", source)
            self.assertIn("PdockerGpuVulkanGraphicsV616ClearAttachmentsCommandEntry", source)
            self.assertIn("PdockerGpuVulkanGraphicsV616ClearAttachmentEntry", source)
            self.assertIn("PdockerGpuVulkanGraphicsV616ClearRectEntry", source)
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V616_HEADER_EXTENSION_FIELDS", source)
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V616_CLEAR_ATTACHMENTS_COMMAND_FIELDS", source)
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V616_CLEAR_ATTACHMENT_FIELDS", source)
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V616_CLEAR_RECT_FIELDS", source)
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V616_HEADER_EXTENSION_SCHEMA_HASH", source)
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V616_CLEAR_ATTACHMENTS_COMMAND_SCHEMA_HASH", source)
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V616_CLEAR_ATTACHMENT_SCHEMA_HASH", source)
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V616_CLEAR_RECT_SCHEMA_HASH", source)
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V616_MAX_CLEAR_ATTACHMENTS_COMMANDS", source)
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V616_MAX_CLEAR_ATTACHMENTS", source)
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V616_MAX_CLEAR_RECTS", source)
            self.assertIn("PDOCKER_GPU_GRAPHICS_V6_COMMAND_CLEAR_ATTACHMENTS", source)
            self.assertIn("clear_attachment_first", source)
            self.assertIn("color_attachment", source)
            self.assertIn("rect_extent_width", source)
        for marker in [
            "sizeof(PdockerGpuVulkanGraphicsV616FrameHeader)",
            "FrameRange ranges[43]",
            "header_v616->v616.clear_attachments_command_count",
            "PdockerGpuVulkanGraphicsV616ClearAttachmentsCommandEntry *clear_attachment_commands",
            "clear_attachments_command_table_hash",
            "find_vulkan_graphics_v616_clear_attachments",
            "vulkan_graphics_v616_clear_attachments_match_rendering",
            "vulkan_graphics_v616_rect_inside_render_area",
            "active_rendering_command",
            "vkCmdClearAttachments(command_buffer",
            "clear attachments outside dynamic rendering",
            "graphics clear attachments requires V6.16 metadata",
            "graphics clear attachments do not match active rendering",
        ]:
            self.assertIn(marker, executor)
        for marker in [
            "vkCmdClearAttachments",
            "need_v616_clear_attachments",
            "pre_need_v616_clear_attachments",
            "VULKAN_GRAPHICS_V6.16",
            "PdockerGpuVulkanGraphicsV616FrameHeader",
            "PdockerGpuVulkanGraphicsV616ClearAttachmentsCommandEntry",
            "clear_attachments_command_table_hash",
            "snapshot->clear_attachment_first += clear_attachment_base",
            "snapshot->clear_rect_first += clear_rect_base",
            "PDOCKER_GPU_GRAPHICS_V6_COMMAND_CLEAR_ATTACHMENTS",
        ]:
            self.assertIn(marker, icd)


    def test_vulkan_graphics_v611_buffer_write_metadata_is_append_only(self):
        abi = APP_HEADER.read_text()
        container_abi = CONTAINER_HEADER.read_text()
        executor = GPU_EXECUTOR.read_text()
        icd = VULKAN_ICD.read_text()
        for source in [abi, container_abi]:
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V611_ABI_MINOR 11u", source)
            self.assertIn("PdockerGpuVulkanGraphicsV611FrameHeader", source)
            self.assertIn("PdockerGpuVulkanGraphicsV611FillBufferEntry", source)
            self.assertIn("PdockerGpuVulkanGraphicsV611UpdateBufferEntry", source)
            self.assertIn("PDOCKER_GPU_GRAPHICS_V6_COMMAND_FILL_BUFFER", source)
            self.assertIn("PDOCKER_GPU_GRAPHICS_V6_COMMAND_UPDATE_BUFFER", source)
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V611_MAX_UPDATE_BUFFER_BYTES 65536u", source)
        for marker in [
            "sizeof(PdockerGpuVulkanGraphicsV611FrameHeader)",
            "FrameRange ranges[43]",
            "header_v611->v611.fill_buffer_count",
            "header_v611->v611.update_buffer_count",
            "find_vulkan_graphics_v611_fill_buffer",
            "find_vulkan_graphics_v611_update_buffer",
            "vkCmdFillBuffer(command_buffer",
            "vkCmdUpdateBuffer(command_buffer",
            "VK_ACCESS_TRANSFER_WRITE_BIT",
            "VK_PIPELINE_STAGE_TRANSFER_BIT",
        ]:
            self.assertIn(marker, executor)
        for marker in [
            "need_v611_buffer_write",
            "PdockerGpuVulkanGraphicsV611FrameHeader",
            "PDOCKER_GPU_GRAPHICS_V6_COMMAND_FILL_BUFFER",
            "PDOCKER_GPU_GRAPHICS_V6_COMMAND_UPDATE_BUFFER",
            "vkCmdFillBuffer",
            "vkCmdUpdateBuffer",
            "pre_need_v611_buffer_write",
        ]:
            self.assertIn(marker, icd)

    def test_vulkan_graphics_v61_metadata_extension_is_fail_closed_validated(self):
        executor = GPU_EXECUTOR.read_text()
        header_validator = executor.split("static int validate_vulkan_graphics_v6_header", 1)[1].split(
            "static const void *graphics_v6_table_ptr", 1
        )[0]
        content_validator = executor.split("static int validate_vulkan_graphics_v6_frame_content", 1)[1].split(
            "static int recv_vulkan_graphics_v6_header_with_fds", 1
        )[0]
        recv_body = executor.split("static int recv_vulkan_graphics_v6_header_with_fds", 1)[1].split(
            "static int connection_starts_with_graphics_v6_magic", 1
        )[0]
        handle_body = executor.split("static int handle_vulkan_graphics_v6_frame", 1)[1].split(
            "static int handle_vulkan_dispatch_v5_frame", 1
        )[0]
        for marker in [
            "PDOCKER_GPU_VULKAN_GRAPHICS_V61_ABI_MINOR",
            "sizeof(PdockerGpuVulkanGraphicsV61FrameHeader)",
            "PDOCKER_GPU_VULKAN_GRAPHICS_V61_DYNAMIC_OFFSET_SCHEMA_HASH",
            "PDOCKER_GPU_VULKAN_GRAPHICS_V61_PUSH_CONSTANT_METADATA_SCHEMA_HASH",
            "PDOCKER_GPU_VULKAN_GRAPHICS_V61_MAX_DYNAMIC_OFFSETS",
            "PDOCKER_GPU_VULKAN_GRAPHICS_V61_MAX_PUSH_CONSTANT_METADATA",
            "PDOCKER_GPU_VULKAN_GRAPHICS_V61_IMAGE_BARRIER_SCHEMA_HASH",
            "PDOCKER_GPU_VULKAN_GRAPHICS_V61_MEMORY_BARRIER_SCHEMA_HASH",
            "PDOCKER_GPU_VULKAN_GRAPHICS_V61_BUFFER_BARRIER_SCHEMA_HASH",
            "PDOCKER_GPU_VULKAN_GRAPHICS_V61_MAX_IMAGE_BARRIERS",
            "PDOCKER_GPU_VULKAN_GRAPHICS_V61_MAX_MEMORY_BARRIERS",
            "PDOCKER_GPU_VULKAN_GRAPHICS_V61_MAX_BUFFER_BARRIERS",
        ]:
            self.assertIn(marker, header_validator)
        range_body = header_validator.split("FrameRange ranges[43]", 1)[1].split(
            "table_range_valid(header->resource_table_offset", 1
        )[0]
        self.assertLess(
            range_body.index("{header->command_table_offset, header->command_table_size}"),
            range_body.index("header_v61->v61.dynamic_offset_table_offset"),
        )
        self.assertIn("header_v61->v61.image_barrier_table_offset", range_body)
        self.assertIn("header_v61->v61.memory_barrier_table_offset", range_body)
        self.assertIn("header_v61->v61.buffer_barrier_table_offset", range_body)
        self.assertIn("validate_vulkan_graphics_v6_header_prefix", recv_body)
        self.assertIn("header.frame_size - sizeof(header)", handle_body)
        for marker in [
            "PdockerGpuVulkanGraphicsV61DynamicOffsetEntry",
            "PdockerGpuVulkanGraphicsV61PushConstantMetadataEntry",
            "extension_hash = fnv1a64_update(extension_hash, dynamic_offsets",
            "dynamic_offsets[i].reserved0 != 0",
            "(dynamic_offsets[i].offset & 3u) != 0",
            "commands[meta->command_index].command_type != PDOCKER_GPU_GRAPHICS_V6_COMMAND_PUSH_CONSTANTS",
            "meta->stage_flags == 0",
            "(meta->range_offset & 3u) != 0",
            "range_add_u32(command->first_dynamic_offset, command->dynamic_offset_count",
            "PDOCKER_GPU_V5_DESCRIPTOR_FLAG_DYNAMIC",
            "PdockerGpuVulkanGraphicsV61ImageBarrierEntry",
            "PdockerGpuVulkanGraphicsV61MemoryBarrierEntry",
            "PdockerGpuVulkanGraphicsV61BufferBarrierEntry",
            "extension_hash = fnv1a64_update(extension_hash, image_barriers",
            "extension_hash = fnv1a64_update(extension_hash, memory_barriers",
            "extension_hash = fnv1a64_update(extension_hash, buffer_barriers",
            "commands[barrier->command_index].command_type != PDOCKER_GPU_GRAPHICS_V6_COMMAND_BARRIER",
            "barrier->resource_index >= header->resource_count",
            "barrier->level_count == VK_REMAINING_MIP_LEVELS",
            "barrier->layer_count == VK_REMAINING_ARRAY_LAYERS",
            "vulkan_graphics_v620_image_aspect_valid(image, (VkImageAspectFlags)barrier->aspect_mask)",
            "vulkan_graphics_barrier_queue_family_replayable",
            "descriptor->dynamic_offset != 0",
            "dynamic_descriptor_count != command->dynamic_offset_count",
            "push_hash = fnv1a64_update(1469598103934665603ull",
            "metadata_count != 1",
        ]:
            self.assertIn(marker, content_validator)
        self.assertNotIn("barrier->src_stage_mask == 0 || barrier->dst_stage_mask == 0", content_validator)

    def test_vulkan_graphics_v61_image_barrier_replay_is_reachable(self):
        executor = GPU_EXECUTOR.read_text()
        preflight = executor.split("static int preflight_vulkan_graphics_v6_replay_supported", 1)[1].split(
            "static int preflight_vulkan_graphics_v6_runtime_supported", 1
        )[0]
        preflight_barrier_helper = c_function_body(executor, "preflight_vulkan_graphics_v6_dependency_barriers")
        recorder = executor.split("static int record_vulkan_graphics_v6_command_buffer", 1)[1].split(
            "static int submit_vulkan_graphics_v6_command_buffer", 1
        )[0]
        self.assertNotIn("graphics barrier replay is not implemented", preflight)
        self.assertNotIn("graphics barrier without memory/buffer/image barriers is not supported", preflight)
        self.assertIn("A synchronization2 dependency with no barrier arrays is a legal no-op", preflight)
        self.assertIn("graphics barrier batch exceeds replay stack limit", preflight_barrier_helper)
        self.assertIn("matched_memory_barriers > PDOCKER_GPU_MAX_VULKAN_BINDINGS", preflight_barrier_helper)
        self.assertIn("PdockerGpuVulkanGraphicsV61ImageBarrierEntry", executor)
        self.assertIn("PFN_vkCmdPipelineBarrier2 cmd_pipeline_barrier2", executor)
        self.assertIn("rt->cmd_pipeline_barrier2(command_buffer, &dependency)", recorder)
        self.assertNotIn("memory_barrier_count == 0 && buffer_barrier_count == 0 && image_barrier_count == 0", recorder)
        self.assertIn("Vulkan permits an empty VkDependencyInfo", recorder)
        dependency_helper = c_function_body(executor, "collect_vulkan_graphics_v6_dependency_barriers")
        self.assertIn("VkMemoryBarrier2 memory_barriers_to_record", recorder)
        self.assertIn("VkBufferMemoryBarrier2 buffer_barriers_to_record", recorder)
        self.assertIn("VkImageMemoryBarrier2 image_barriers_to_record", recorder)
        self.assertIn("srcStageMask = (VkPipelineStageFlags2)barrier->src_stage_mask", dependency_helper)
        self.assertNotIn("vkCmdPipelineBarrier(command_buffer,\n                                     src_stages", recorder)
        self.assertIn("vulkan_replay_image_set_layout_for_range(", dependency_helper)
        self.assertIn("vulkan_image_aspect_mask_valid_for_format", executor)
        self.assertIn("barrier->level_count == VK_REMAINING_MIP_LEVELS", dependency_helper)
        self.assertIn("barrier->layer_count == VK_REMAINING_ARRAY_LAYERS", dependency_helper)
        self.assertIn("barrier_image->format, (VkImageAspectFlags)barrier->aspect_mask", dependency_helper)
        preflight_barrier_helper = c_function_body(executor, "preflight_vulkan_graphics_v6_dependency_barriers")
        self.assertIn("vulkan_graphics_barrier_queue_family_replayable", preflight_barrier_helper)
        self.assertIn("graphics cross-queue-family barrier replay is not implemented", preflight_barrier_helper)
        self.assertIn("vulkan_graphics_replay_queue_family_index", dependency_helper)
        self.assertNotIn("case PDOCKER_GPU_GRAPHICS_V6_COMMAND_BARRIER:\n                rc = -EOPNOTSUPP", recorder)

    def test_vulkan_graphics_no_attachment_dynamic_rendering_is_replayable(self):
        executor = GPU_EXECUTOR.read_text()
        preflight = executor.split(
            "static int preflight_vulkan_graphics_v6_replay_supported", 1
        )[1].split("static int preflight_vulkan_graphics_v6_runtime_supported", 1)[0]
        materializer = executor.split(
            "static int materialize_vulkan_graphics_v6_attachments", 1
        )[1].split("static void destroy_vulkan_graphics_replay_attachments", 1)[0]
        recorder = executor.split(
            "static int record_vulkan_graphics_v6_command_buffer", 1
        )[1].split("static int submit_vulkan_graphics_v6_command_buffer", 1)[0]

        self.assertNotIn("dynamic rendering without attachments is not supported", preflight)
        self.assertNotIn("command->attachment_count == 0", materializer)
        self.assertIn(".colorAttachmentCount = color_attachment_count", recorder)
        self.assertIn(".pColorAttachments = color_attachment_count ? color_attachments : NULL", recorder)
        self.assertIn(".pDepthAttachment = depth_attachment_ptr", recorder)
        self.assertIn(".pStencilAttachment = stencil_attachment_ptr", recorder)

    def test_vulkan_graphics_queue_family_barrier_is_safely_normalized(self):
        executor = GPU_EXECUTOR.read_text()
        helper = executor.split(
            "static int vulkan_graphics_barrier_queue_family_replayable", 2
        )[2].split("static uint32_t vulkan_graphics_replay_queue_family_index", 1)[0]
        normalizer = executor.split(
            "static uint32_t vulkan_graphics_replay_queue_family_index", 2
        )[2].split("static int vulkan_graphics_attachment_ops_supported", 1)[0]
        validator = executor.split(
            "static int validate_vulkan_graphics_v6_frame_content", 1
        )[1].split("static uint64_t vulkan_graphics_descriptor_signature_hash", 1)[0]
        preflight = executor.split(
            "static int preflight_vulkan_graphics_v6_replay_supported", 1
        )[1].split("static int preflight_vulkan_graphics_v6_runtime_supported", 1)[0]
        recorder = executor.split(
            "static int record_vulkan_graphics_v6_command_buffer", 1
        )[1].split("static int submit_vulkan_graphics_v6_command_buffer", 1)[0]
        self.assertIn("src_queue_family_index == VK_QUEUE_FAMILY_IGNORED", helper)
        self.assertIn("dst_queue_family_index == VK_QUEUE_FAMILY_IGNORED", helper)
        self.assertIn("return src_queue_family_index == dst_queue_family_index", helper)
        self.assertIn("return VK_QUEUE_FAMILY_IGNORED", normalizer)
        self.assertIn("vulkan_graphics_barrier_queue_family_replayable", validator)
        preflight_barrier_helper = c_function_body(executor, "preflight_vulkan_graphics_v6_dependency_barriers")
        self.assertIn("vulkan_graphics_barrier_queue_family_replayable", preflight_barrier_helper)
        self.assertIn("graphics cross-queue-family barrier replay is not implemented", preflight_barrier_helper)
        dependency_helper = c_function_body(executor, "collect_vulkan_graphics_v6_dependency_barriers")
        self.assertEqual(dependency_helper.count("vulkan_graphics_replay_queue_family_index("), 4)
        self.assertNotIn(".srcQueueFamilyIndex = barrier->src_queue_family_index", recorder)
        self.assertNotIn(".dstQueueFamilyIndex = barrier->dst_queue_family_index", recorder)
        self.assertIn("command->flags & VK_DEPENDENCY_BY_REGION_BIT", executor)
        self.assertIn("graphics barrier dependency flags are not supported", executor)

    def test_vulkan_icd_serializes_graphics_image_barriers(self):
        icd = VULKAN_ICD.read_text()
        self.assertIn("PdockerGpuVulkanGraphicsV61ImageBarrierEntry image_barriers", icd)
        self.assertIn("frame_header->v61.image_barrier_count", icd)
        self.assertIn("frame_header->v61.memory_barrier_count", icd)
        self.assertIn("frame_header->v61.buffer_barrier_count", icd)
        self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V61_IMAGE_BARRIER_SCHEMA_HASH", icd)
        self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V61_MEMORY_BARRIER_SCHEMA_HASH", icd)
        self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V61_BUFFER_BARRIER_SCHEMA_HASH", icd)
        self.assertIn("collect_graphics_image_entry(", icd)
        self.assertIn("record_memory_barrier_op(commandBuffer", icd)
        self.assertIn("record_buffer_barrier_op(commandBuffer", icd)
        self.assertIn("record.command_type = PDOCKER_GPU_GRAPHICS_V6_COMMAND_BARRIER", icd)
        self.assertIn("dependencyFlags & ~VK_DEPENDENCY_BY_REGION_BIT", icd)
        self.assertIn("dependency_flags & ~VK_DEPENDENCY_BY_REGION_BIT", icd)
        self.assertIn("record.flags = dependencyFlags & VK_DEPENDENCY_BY_REGION_BIT", icd)
        self.assertIn("record.flags = dependency_flags & VK_DEPENDENCY_BY_REGION_BIT", icd)
        self.assertIn("for (uint32_t i = 0; i < eventCount; ++i)", icd)
        self.assertNotIn("event-wait2-barrier-payload-unsupported", icd)
        self.assertNotIn("vkCmdPipelineBarrier2(commandBuffer, &pDependencyInfos[i])", icd)
        self.assertNotIn("eventCount > 1", icd)
        self.assertIn("command_op_is_graphics_frame_op", icd)
        self.assertIn("case PDOCKER_VK_COMMAND_IMAGE_BARRIER:", icd)
        self.assertIn("(size_t)frame_header->v61.image_barrier_table_size", icd)
        self.assertIn("(size_t)frame_header->v61.memory_barrier_table_size", icd)
        self.assertIn("(size_t)frame_header->v61.buffer_barrier_table_size", icd)
        self.assertIn("normalize_image_subresource_range(src->image, &src->range, &normalized_range)", icd)
        self.assertIn("dst->level_count = normalized_range.levelCount", icd)
        self.assertIn("dst->layer_count = normalized_range.layerCount", icd)
        serializer_body = icd.split("const PdockerVkImageBarrierOp *src = &cmd->image_barrier_ops[op_index];", 1)[1].split(
            "dst->src_access_mask", 1
        )[0]
        self.assertNotIn("src->range.levelCount", serializer_body)
        self.assertNotIn("src->range.layerCount", serializer_body)
        self.assertIn("image-barrier-invalid-range", icd)
        self.assertIn("pdocker_vk_image_aspect_mask_valid_for_format", icd)
        self.assertIn("static bool pdocker_vk_queue_family_barrier_replayable", icd)
        self.assertIn("src_queue_family_index == VK_QUEUE_FAMILY_IGNORED", icd)
        self.assertIn("return src_queue_family_index == dst_queue_family_index", icd)
        self.assertIn("image-barrier-cross-queue-family", icd)
        self.assertIn("buffer-barrier-cross-queue-family", icd)
        self.assertIn("buffer-barrier-invalid-range", icd)
        image_record_body = icd.split("static void record_image_barrier_op", 2)[2].split(
            "static void record_memory_barrier_op", 1
        )[0]
        self.assertLess(
            image_record_body.index("pdocker_vk_queue_family_barrier_replayable"),
            image_record_body.index("normalize_image_subresource_range"),
        )
        buffer_record_body = icd.split("static void record_buffer_barrier_op", 1)[1].split(
            "VKAPI_ATTR void VKAPI_CALL vkCmdPipelineBarrier", 1
        )[0]
        self.assertLess(
            buffer_record_body.index("pdocker_vk_queue_family_barrier_replayable"),
            buffer_record_body.index("if (size == VK_WHOLE_SIZE)"),
        )
        clear_body = icd.split("static void clear_recorded_command_ops", 1)[1].split(
            "static bool append_command_op", 1
        )[0]
        self.assertIn("cmd->memory_barrier_op_count = 0;", clear_body)
        self.assertIn("cmd->buffer_barrier_op_count = 0;", clear_body)

    def test_vulkan_dispatch_v5_1_object_header_is_full_frame_validated(self):
        executor = GPU_EXECUTOR.read_text()
        self.assertIn("PdockerGpuVulkanDispatchV5ObjectFrameHeader", executor)
        self.assertIn("validate_vulkan_dispatch_v5_object_extension", executor)
        self.assertIn("header_out->header_size > sizeof(*header_out)", executor)
        self.assertIn("extension_bytes = (size_t)(header_out->header_size - sizeof(*header_out))", executor)
        self.assertIn("read_exact_bytes(cfd, frame + sizeof(*header_out), extension_bytes)", executor)
        self.assertIn("validate_vulkan_dispatch_v5_frame_content(frame, passed_fds, *fd_count)", executor)
        self.assertIn("validate_vulkan_dispatch_v5_object_extension(frame, header)", executor)
        self.assertIn("header->abi_minor == PDOCKER_GPU_VULKAN_DISPATCH_V5_ABI_MINOR_OBJECTS", executor)
        self.assertIn("header->header_size != sizeof(PdockerGpuVulkanDispatchV5ObjectFrameHeader)", executor)
        self.assertIn("header->descriptor_entry_size != sizeof(PdockerGpuVulkanDispatchV5DescriptorObjectEntry)", executor)
        self.assertIn("header->descriptor_schema_hash != PDOCKER_GPU_VULKAN_DISPATCH_V5_DESCRIPTOR_OBJECT_SCHEMA_HASH", executor)
        self.assertIn("objects->image_count > PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_IMAGES", executor)
        self.assertIn("objects->image_view_count > PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_IMAGE_VIEWS", executor)
        self.assertIn("objects->sampler_count > PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_SAMPLERS", executor)
        self.assertIn("objects->image_entry_size != sizeof(PdockerGpuVulkanDispatchV5ImageEntry)", executor)
        self.assertIn("objects->image_view_entry_size != sizeof(PdockerGpuVulkanDispatchV5ImageViewEntry)", executor)
        self.assertIn("objects->sampler_entry_size != sizeof(PdockerGpuVulkanDispatchV5SamplerEntry)", executor)
        self.assertIn("objects->image_schema_hash != PDOCKER_GPU_VULKAN_DISPATCH_V5_IMAGE_SCHEMA_HASH", executor)
        self.assertIn("objects->image_view_schema_hash != PDOCKER_GPU_VULKAN_DISPATCH_V5_IMAGE_VIEW_SCHEMA_HASH", executor)
        self.assertIn("objects->sampler_schema_hash != PDOCKER_GPU_VULKAN_DISPATCH_V5_SAMPLER_SCHEMA_HASH", executor)
        self.assertIn("objects->image_table_offset, objects->image_table_size", executor)
        self.assertIn("objects->image_view_table_offset, objects->image_view_table_size", executor)
        self.assertIn("objects->sampler_table_offset, objects->sampler_table_size", executor)

    def test_vulkan_dispatch_v5_frame_hashes_are_fail_closed(self):
        icd = VULKAN_ICD.read_text()
        executor = GPU_EXECUTOR.read_text()
        validator = executor.split("static int validate_vulkan_dispatch_v5_frame_content", 1)[1].split(
            "static int checked_u64_add3", 1
        )[0]
        recv_body = executor.split("static int recv_vulkan_dispatch_v5_frame", 1)[1].split(
            "static int connection_starts_with_v5_magic", 1
        )[0]
        for marker in [
            "header->option_hash =",
            "header->resource_hash =",
            "header->descriptor_hash =",
            "object_header->objects.object_hash =",
            "header->frame_hash = fnv1a64_bytes(frame, cursor)",
        ]:
            self.assertIn(marker, icd)
        self.assertIn("validate_vulkan_dispatch_v5_frame_content(frame, passed_fds, *fd_count)", recv_body)
        self.assertIn("v5_table_range_valid", validator)
        self.assertIn("table_range_valid(offset, size, count, entry_size, entry_alignment, frame_size, header_size)", executor)
        self.assertIn("v5_payload_range_valid", validator)
        self.assertIn("payload_range_valid(offset, size, frame_size)", executor)
        self.assertIn("frame_ranges_do_not_overlap(ranges, range_count)", validator)
        self.assertIn("resource_hash", validator)
        self.assertIn("descriptor_hash", validator)
        self.assertIn("push_hash", validator)
        self.assertIn("option_hash", validator)
        self.assertIn("v5_object_extension_hash(frame, header, objects) != objects->object_hash", validator)
        self.assertIn("full_fd_hash(passed_fds[header->shader_fd_index]", validator)
        self.assertIn("shader_hash != header->shader_hash", validator)
        self.assertIn("offsetof(PdockerGpuVulkanDispatchV5FrameHeader, frame_hash)", validator)
        self.assertIn("zero_frame_hash", validator)
        self.assertIn("frame_hash != header->frame_hash", validator)

        sender_spec_hash = icd.split("static uint64_t fnv1a64_specialization_hash", 1)[1].split(
            "static bool dispatch_lifecycle_log_enabled", 1
        )[0]
        executor_spec_hash = executor.split("static uint64_t pipeline_specialization_hash", 1)[1].split(
            "static uint64_t reconcile_dispatch_hash", 1
        )[0]
        self.assertIn("const uint64_t entry_count64 = (uint64_t)entry_count;", sender_spec_hash)
        self.assertIn("fnv1a64_update_bytes(hash, &entry_count64, sizeof(entry_count64))", sender_spec_hash)
        self.assertIn("const uint64_t size64 = (uint64_t)entries[i].size;", sender_spec_hash)
        self.assertIn("fnv1a64_update_bytes(hash, &size64, sizeof(size64))", sender_spec_hash)
        self.assertNotIn("&entry_count, sizeof(entry_count)", sender_spec_hash)
        self.assertNotIn("&size, sizeof(size)", sender_spec_hash)
        self.assertIn("const uint64_t specialization_count64 = (uint64_t)specialization_count;", executor_spec_hash)
        self.assertIn("fnv1a64_update(hash, &specialization_count64, sizeof(specialization_count64))", executor_spec_hash)
        self.assertIn("const uint64_t size64 = (uint64_t)specializations[i].size;", executor_spec_hash)
        self.assertIn("fnv1a64_update(hash, &size64, sizeof(size64))", executor_spec_hash)
        self.assertNotIn("&specialization_count, sizeof(specialization_count)", executor_spec_hash)
        self.assertNotIn("&size, sizeof(size)", executor_spec_hash)

        handler = executor.split("static int handle_vulkan_dispatch_v5_frame", 1)[1].split(
            "static int recv_command_with_fds", 1
        )[0]
        self.assertIn("const uint64_t v5_specialization_hash = pipeline_specialization_hash(", handler)
        self.assertIn("v5_specialization_hash != header.specialization_hash", handler)
        self.assertNotIn("header.specialization_hash != 0", handler)
        self.assertIn('json_fail("vulkan-dispatch-v5", "specialization hash mismatch")', handler)
        self.assertIn("const uint64_t v5_dispatch_hash = reconcile_dispatch_hash(", handler)
        self.assertIn("options.has_base_group ? options.base_group_x : 0", handler)
        self.assertIn("dispatch_indirect_buffer_id", handler)
        self.assertIn("options.dispatch_indirect_resource_index >= object_tables.resource_count", handler)
        self.assertIn("indirect_resource->resource_type != PDOCKER_GPU_V5_RESOURCE_TYPE_BUFFER", handler)
        self.assertIn("v5_dispatch_hash != header.dispatch_hash", handler)
        self.assertIn('json_fail("vulkan-dispatch-v5", "dispatch hash mismatch")', handler)

    def test_vulkan_dispatch_v5_0_header_compatibility_survives_v5_1_objects(self):
        executor = GPU_EXECUTOR.read_text()
        validator = executor.split("static int validate_vulkan_dispatch_v5_header", 1)[1].split(
            "static int validate_vulkan_dispatch_v5_object_extension", 1
        )[0]
        v5_0_minor = "header->abi_minor == PDOCKER_GPU_VULKAN_DISPATCH_V5_ABI_MINOR"
        v5_1_minor = "header->abi_minor == PDOCKER_GPU_VULKAN_DISPATCH_V5_ABI_MINOR_OBJECTS"
        self.assertIn(v5_0_minor, validator)
        self.assertIn(v5_1_minor, validator)
        self.assertLess(validator.index(v5_0_minor), validator.index(v5_1_minor))
        self.assertIn("header->header_size != sizeof(PdockerGpuVulkanDispatchV5FrameHeader)", validator)
        self.assertIn("header->header_size != sizeof(PdockerGpuVulkanDispatchV5ObjectFrameHeader)", validator)
        self.assertIn("header->descriptor_entry_size != sizeof(PdockerGpuVulkanDispatchV5DescriptorEntry)", validator)
        self.assertIn("header->descriptor_schema_hash != PDOCKER_GPU_VULKAN_DISPATCH_V5_DESCRIPTOR_SCHEMA_HASH", validator)
        self.assertIn("header->descriptor_entry_size != sizeof(PdockerGpuVulkanDispatchV5DescriptorObjectEntry)", validator)
        self.assertIn(
            "header->descriptor_schema_hash != PDOCKER_GPU_VULKAN_DISPATCH_V5_DESCRIPTOR_OBJECT_SCHEMA_HASH",
            validator,
        )
        object_validator = executor.split("static int validate_vulkan_dispatch_v5_object_extension", 1)[1].split(
            "static const void *v5_frame_range", 1
        )[0]
        self.assertIn(
            "header->abi_minor != PDOCKER_GPU_VULKAN_DISPATCH_V5_ABI_MINOR_OBJECTS &&",
            object_validator,
        )
        self.assertIn(
            "header->abi_minor != PDOCKER_GPU_VULKAN_DISPATCH_V52_ABI_MINOR) return 0;",
            object_validator,
        )

    def test_vulkan_dispatch_v5_1_object_transport_buffer_descriptors_reuse_v4_execution_path(self):
        executor = GPU_EXECUTOR.read_text()
        handler = executor.split("static int handle_vulkan_dispatch_v5_frame", 1)[1].split(
            "static int serve_socket", 1
        )[0]
        conversion = "convert_vulkan_dispatch_v5_to_v4_bindings"
        self.assertIn(conversion, handler)
        self.assertIn("run_vulkan_dispatch_fd(", handler)
        self.assertNotIn("object materialization is pending", handler)
        self.assertLess(handler.index(conversion), handler.index("run_vulkan_dispatch_fd("))
        converter = executor.split("static int convert_vulkan_dispatch_v5_to_v4_bindings", 1)[1].split(
            "static int recv_vulkan_dispatch_v5_header_with_fds", 1
        )[0]
        self.assertIn("PdockerGpuVulkanDispatchV5DescriptorObjectEntry", converter)
        self.assertIn("header->abi_minor == PDOCKER_GPU_VULKAN_DISPATCH_V5_ABI_MINOR_OBJECTS", converter)
        self.assertIn("object_descriptors[i]", converter)
        self.assertIn("object_copy.image_view_index = PDOCKER_GPU_V5_DESCRIPTOR_OBJECT_NONE;", converter)
        self.assertIn("object_copy.sampler_index = PDOCKER_GPU_V5_DESCRIPTOR_OBJECT_NONE;", converter)
        self.assertIn("d->image_view_index != PDOCKER_GPU_V5_DESCRIPTOR_OBJECT_NONE", converter)
        self.assertIn("VulkanDispatchImageDescriptor", converter)
        self.assertIn("image_descriptors[image_descriptor_count++]", converter)
        self.assertIn("vulkan_dispatch_image_descriptor_type_from_api", converter)

    def test_vulkan_dispatch_v5_2_image_layout_range_abi_scaffold(self):
        abi = APP_HEADER.read_text()
        container_abi = CONTAINER_HEADER.read_text()
        expected_extension_fields = [
            ("image_layout_range_count", "u32"),
            ("image_layout_range_entry_size", "u32"),
            ("image_layout_range_table_offset", "u64"),
            ("image_layout_range_table_size", "u64"),
            ("image_layout_range_schema_hash", "u64"),
            ("image_layout_range_table_hash", "u64"),
            ("extension_hash", "u64"),
        ]
        expected_range_fields = [
            ("image_index", "u32"),
            ("aspect_mask", "u32"),
            ("base_mip_level", "u32"),
            ("level_count", "u32"),
            ("base_array_layer", "u32"),
            ("layer_count", "u32"),
            ("layout", "u32"),
            ("reserved0", "u32"),
            ("layout_generation", "u64"),
        ]
        for header_path, source in [(APP_HEADER, abi), (CONTAINER_HEADER, container_abi)]:
            with self.subTest(header=str(header_path)):
                self.assertIn("PDOCKER_GPU_VULKAN_DISPATCH_V52_ABI_MINOR 2u", source)
                self.assertIn("PdockerGpuVulkanDispatchV52HeaderExtension", source)
                self.assertIn("PdockerGpuVulkanDispatchV52FrameHeader", source)
                self.assertIn("PdockerGpuVulkanDispatchV52ImageLayoutRangeEntry", source)
                self.assertIn("PDOCKER_GPU_VULKAN_DISPATCH_V52_HEADER_EXTENSION_FIELDS", source)
                self.assertIn("PDOCKER_GPU_VULKAN_DISPATCH_V52_IMAGE_LAYOUT_RANGE_FIELDS", source)
                self.assertIn("PDOCKER_GPU_VULKAN_DISPATCH_V52_MAX_IMAGE_LAYOUT_RANGES", source)
                header_fields, header_count, _, computed_header_hash = vulkan_dispatch_v5_schema(
                    header_path,
                    "PDOCKER_GPU_VULKAN_DISPATCH_V52_HEADER_EXTENSION_FIELDS",
                    "PDOCKER_GPU_VULKAN_DISPATCH_V52_HEADER_EXTENSION_FIELD_COUNT",
                )
                range_fields, range_count, declared_range_hash, computed_range_hash = vulkan_dispatch_v5_schema(
                    header_path,
                    "PDOCKER_GPU_VULKAN_DISPATCH_V52_IMAGE_LAYOUT_RANGE_FIELDS",
                    "PDOCKER_GPU_VULKAN_DISPATCH_V52_IMAGE_LAYOUT_RANGE_FIELD_COUNT",
                    "PDOCKER_GPU_VULKAN_DISPATCH_V52_IMAGE_LAYOUT_RANGE_SCHEMA_HASH",
                )
                self.assertEqual(expected_extension_fields, header_fields)
                self.assertEqual(expected_range_fields, range_fields)
                self.assertEqual(7, header_count)
                self.assertEqual(9, range_count)
                self.assertEqual(schema_hash(expected_extension_fields), computed_header_hash)
                self.assertEqual(declared_range_hash, computed_range_hash)
                self.assertEqual(
                    [name for name, _ in expected_extension_fields],
                    c_struct_field_names(header_path, "PdockerGpuVulkanDispatchV52HeaderExtension"),
                )
                self.assertEqual(
                    [name for name, _ in expected_range_fields],
                    c_struct_field_names(header_path, "PdockerGpuVulkanDispatchV52ImageLayoutRangeEntry"),
                )
                v52_frame_fields = c_struct_field_names(header_path, "PdockerGpuVulkanDispatchV52FrameHeader")
                self.assertIn(
                    v52_frame_fields,
                    [
                        c_struct_field_names(header_path, "PdockerGpuVulkanDispatchV5ObjectFrameHeader") + ["v52"],
                        ["v51", "v52"],
                    ],
                )

        self.assertEqual(
            vulkan_dispatch_v5_schema(
                APP_HEADER,
                "PDOCKER_GPU_VULKAN_DISPATCH_V52_HEADER_EXTENSION_FIELDS",
                "PDOCKER_GPU_VULKAN_DISPATCH_V52_HEADER_EXTENSION_FIELD_COUNT",
            ),
            vulkan_dispatch_v5_schema(
                CONTAINER_HEADER,
                "PDOCKER_GPU_VULKAN_DISPATCH_V52_HEADER_EXTENSION_FIELDS",
                "PDOCKER_GPU_VULKAN_DISPATCH_V52_HEADER_EXTENSION_FIELD_COUNT",
            ),
        )
        self.assertEqual(
            vulkan_dispatch_v5_schema(
                APP_HEADER,
                "PDOCKER_GPU_VULKAN_DISPATCH_V52_IMAGE_LAYOUT_RANGE_FIELDS",
                "PDOCKER_GPU_VULKAN_DISPATCH_V52_IMAGE_LAYOUT_RANGE_FIELD_COUNT",
                "PDOCKER_GPU_VULKAN_DISPATCH_V52_IMAGE_LAYOUT_RANGE_SCHEMA_HASH",
            ),
            vulkan_dispatch_v5_schema(
                CONTAINER_HEADER,
                "PDOCKER_GPU_VULKAN_DISPATCH_V52_IMAGE_LAYOUT_RANGE_FIELDS",
                "PDOCKER_GPU_VULKAN_DISPATCH_V52_IMAGE_LAYOUT_RANGE_FIELD_COUNT",
                "PDOCKER_GPU_VULKAN_DISPATCH_V52_IMAGE_LAYOUT_RANGE_SCHEMA_HASH",
            ),
        )

    def test_vulkan_dispatch_v5_2_icd_transports_mixed_image_layout_ranges(self):
        icd = VULKAN_ICD.read_text()
        sender = c_function_body(icd, "send_generic_vulkan_dispatch_v5_1_op")
        collector_name = (
            "collect_v5_image_layout_range_entries"
            if "collect_v5_image_layout_range_entries" in icd
            else "collect_dispatch_image_layout_range_entries"
        )
        collector = c_function_body(icd, collector_name)
        for marker in [
            "PdockerGpuVulkanDispatchV52FrameHeader",
            "PdockerGpuVulkanDispatchV52ImageLayoutRangeEntry",
            "PDOCKER_GPU_VULKAN_DISPATCH_V52_MAX_IMAGE_LAYOUT_RANGES",
            collector_name,
            "image_layout_range_count",
            "PDOCKER_GPU_VULKAN_DISPATCH_V52_ABI_MINOR",
            "image_layout_range_entry_size = sizeof(PdockerGpuVulkanDispatchV52ImageLayoutRangeEntry)",
            "image_layout_range_schema_hash = PDOCKER_GPU_VULKAN_DISPATCH_V52_IMAGE_LAYOUT_RANGE_SCHEMA_HASH",
            "image_layout_range_table_hash = fnv1a64_bytes",
            "image_layout_range_table_bytes",
            "sizeof(PdockerGpuVulkanDispatchV52FrameHeader)",
        ]:
            self.assertIn(marker, sender)
        self.assertIn("layout_mixed", collector)
        self.assertIn("image->layout_ranges", collector)
        for match in re.finditer("layout_mixed", sender):
            reject_window = sender[max(0, match.start() - 120):match.end() + 300]
            self.assertNotIn("return -EOPNOTSUPP;", reject_window)
        self.assertNotIn("mixed subresource layouts require per-subresource layout ABI", sender)

    def test_vulkan_dispatch_v5_2_icd_gates_layout_ranges_on_executor_capability(self):
        icd = VULKAN_ICD.read_text()
        sender = c_function_body(icd, "send_generic_vulkan_dispatch_v5_1_op")
        capability_markers = [
            "executor_supports_vulkan_dispatch_v52_image_layout_ranges",
            "executor_vulkan_dispatch_v52_image_layout_ranges_supported",
            "vulkan_dispatch_v52_image_layout_ranges_supported",
        ]
        capability_marker = next((marker for marker in capability_markers if marker in sender), None)
        self.assertIsNotNone(
            capability_marker,
            "ICD must prove executor V5.2 image-layout-range support before sending abi_minor 2",
        )
        abi_minor_assignment = "header->abi_minor = need_v52_image_layout_ranges"
        self.assertIn(abi_minor_assignment, sender)
        self.assertLess(sender.index(capability_marker), sender.index(abi_minor_assignment))
        self.assertRegex(
            sender,
            r"need_v52_image_layout_ranges\s*&&\s*!.*v52.*image_layout.*range.*support",
        )
        self.assertRegex(sender, r"return\s+-(?:EOPNOTSUPP|EPROTO|EINVAL);")

    def test_vulkan_dispatch_v5_2_icd_layout_range_table_is_not_stack_sized_when_heap_backed(self):
        icd = VULKAN_ICD.read_text()
        sender = c_function_body(icd, "send_generic_vulkan_dispatch_v5_1_op")
        self.assertNotRegex(
            sender,
            r"PdockerGpuVulkanDispatchV52ImageLayoutRangeEntry\s+\w+\s*"
            r"\[\s*PDOCKER_GPU_VULKAN_DISPATCH_V52_MAX_IMAGE_LAYOUT_RANGES\s*\]",
        )
        self.assertRegex(
            sender,
            r"PdockerGpuVulkanDispatchV52ImageLayoutRangeEntry\s*\*\s*image_layout_ranges",
        )
        self.assertRegex(sender, r"(?:calloc|malloc)\s*\([^;]*image_layout_range")
        self.assertIn("free(image_layout_ranges)", sender)

    def test_vulkan_dispatch_v5_2_executor_validates_and_materializes_layout_ranges(self):
        executor = GPU_EXECUTOR.read_text()
        converter = c_function_body(executor, "convert_vulkan_dispatch_v5_to_v4_bindings")
        materializer = c_function_body(executor, "materialize_vulkan_dispatch_images")
        runner = c_function_body(executor, "run_vulkan_dispatch_fd")
        frame_validator = c_function_body(executor, "validate_vulkan_dispatch_v5_frame_content")
        for marker in [
            "PDOCKER_GPU_VULKAN_DISPATCH_V52_ABI_MINOR",
            "sizeof(PdockerGpuVulkanDispatchV52FrameHeader)",
            "PdockerGpuVulkanDispatchV52ImageLayoutRangeEntry",
            "const PdockerGpuVulkanDispatchV52ImageLayoutRangeEntry *image_layout_ranges;",
            "size_t image_layout_range_count;",
            "PDOCKER_GPU_VULKAN_DISPATCH_V52_MAX_IMAGE_LAYOUT_RANGES",
        ]:
            self.assertIn(marker, executor)
        for marker in [
            "ext->image_layout_range_entry_size != sizeof(PdockerGpuVulkanDispatchV52ImageLayoutRangeEntry)",
            "ext->image_layout_range_schema_hash != PDOCKER_GPU_VULKAN_DISPATCH_V52_IMAGE_LAYOUT_RANGE_SCHEMA_HASH",
            "ext->image_layout_range_table_offset",
            "ext->image_layout_range_table_size",
            "ext->image_layout_range_count",
            "ext->image_layout_range_table_hash",
            "ext->extension_hash != ext->image_layout_range_table_hash",
            "__alignof__(PdockerGpuVulkanDispatchV52ImageLayoutRangeEntry)",
        ]:
            self.assertIn(marker, frame_validator)
        for marker in [
            "PDOCKER_GPU_VULKAN_DISPATCH_V52_ABI_MINOR",
            "PdockerGpuVulkanDispatchV52FrameHeader",
            "header_v52->v52.image_layout_range_count",
            "header_v52->v52.image_layout_range_table_offset",
            "header_v52->v52.image_layout_range_table_size",
            "object_tables_out->image_layout_ranges =",
            "object_tables_out->image_layout_range_count =",
        ]:
            self.assertIn(marker, converter)
        self.assertTrue(
            "object_tables->image_layout_ranges" in materializer
            or "materialize_vulkan_dispatch_v52_image_layout_ranges" in runner
        )
        layout_materializer = materializer
        if "static int materialize_vulkan_dispatch_v52_image_layout_ranges" in executor:
            layout_materializer += c_function_body(executor, "materialize_vulkan_dispatch_v52_image_layout_ranges")
        self.assertIn("record_vulkan_dispatch_v52_initial_image_layout_ranges", runner)
        for marker in [
            "object_tables->image_layout_ranges",
            "object_tables->image_layout_range_count",
            "const PdockerGpuVulkanDispatchV52ImageLayoutRangeEntry *src",
            "vulkan_replay_layout_for_executor((VkImageLayout)src->layout)",
            "vulkan_replay_image_layout_range_valid",
            "image->layout_range_count",
            "image->has_layout_ranges = 1",
            "PDOCKER_GPU_MAX_VULKAN_IMAGE_LAYOUT_RANGES_PER_IMAGE",
        ]:
            self.assertIn(marker, layout_materializer)

    def test_vulkan_dispatch_v5_2_executor_materializes_layout_ranges_without_image_descriptors(self):
        executor = GPU_EXECUTOR.read_text()
        runner = c_function_body(executor, "run_vulkan_dispatch_fd")
        materialize_call = "materialize_vulkan_dispatch_v52_image_layout_ranges("
        self.assertIn(materialize_call, runner)
        descriptor_block = c_block_from(runner, "if (image_descriptor_count > 0)")
        self.assertNotIn(
            materialize_call,
            descriptor_block,
            "V5.2 layout ranges must be validated/materialized even when there are no image descriptors",
        )
        self.assertRegex(
            runner,
            r"object_tables(?:->|\.)image_layout_range_count\s*>\s*0",
        )

    def test_vulkan_dispatch_v5_2_executor_rejects_invalid_layout_range_entries(self):
        executor = GPU_EXECUTOR.read_text()
        materializer = c_function_body(executor, "materialize_vulkan_dispatch_v52_image_layout_ranges")
        validation = materializer
        for helper in [
            "vulkan_dispatch_v52_layout_value_valid",
            "vulkan_dispatch_v52_image_aspect_valid",
            "vulkan_dispatch_v52_image_layout_ranges_overlap",
        ]:
            if f"static int {helper}" in executor:
                validation += c_function_body(executor, helper)
        for marker in [
            "src->reserved0 != 0",
            "src->image_index >= object_tables->image_count",
            "vulkan_replay_image_layout_range_valid",
            "baseMipLevel",
            "levelCount",
            "baseArrayLayer",
            "layerCount",
            "vulkan_dispatch_subresource_ranges_overlap",
        ]:
            self.assertIn(marker, validation)
        self.assertRegex(validation, r"(?:aspect_mask|aspectMask).*==\s*0")
        self.assertRegex(validation, r"(?:layout_value_valid|layout_valid|image_layout_valid)")
        self.assertRegex(validation, r"return\s+-E(?:PROTO|RANGE|OPNOTSUPP);")

    def test_vulkan_dispatch_v5_tables_convert_to_existing_v4_semantics(self):
        executor = GPU_EXECUTOR.read_text()
        self.assertIn("convert_vulkan_dispatch_v5_to_v4_bindings", executor)
        self.assertIn("PdockerGpuVulkanDispatchV5ResourceEntry", executor)
        self.assertIn("PdockerGpuVulkanDispatchV5DescriptorEntry", executor)
        self.assertIn("PdockerGpuVulkanDispatchV5DescriptorObjectEntry", executor)
        self.assertIn("PDOCKER_GPU_V5_RESOURCE_TYPE_MEMORY", executor)
        self.assertIn("PDOCKER_GPU_V5_RESOURCE_TYPE_BUFFER", executor)
        self.assertIn("PDOCKER_GPU_V5_RESOURCE_TYPE_IMAGE", executor)
        self.assertIn("PDOCKER_GPU_V5_RESOURCE_TYPE_IMAGE_VIEW", executor)
        self.assertIn("PDOCKER_GPU_V5_RESOURCE_TYPE_SAMPLER", executor)
        self.assertIn("PDOCKER_GPU_V5_RESOURCE_FLAG_HOST_FD_BACKED", executor)
        self.assertIn("vulkan_dispatch_descriptor_type_from_api(d->descriptor_type", executor)
        self.assertIn("VulkanDispatchBinding *binding = &bindings[buffer_descriptor_count];", executor)
        self.assertIn("binding->descriptor_set = d->descriptor_set;", executor)
        self.assertIn("binding->binding = d->binding;", executor)
        self.assertIn("binding->api_array_element = d->array_element;", executor)
        self.assertIn("binding->api_descriptor_type = d->descriptor_type;", executor)
        self.assertIn("binding->api_memory_id = memory->resource_id;", executor)
        self.assertIn("binding->api_buffer_id = buffer->resource_id;", executor)
        self.assertIn("binding_fds[buffer_descriptor_count] = passed_fds[memory->fd_index];", executor)
        self.assertIn("checked_u64_add3(memory->external_offset, buffer->memory_offset", executor)
        self.assertIn("checked_u64_add3(d->buffer_offset, d->dynamic_offset, 0, &api_offset)", executor)
        self.assertIn("checked_u64_to_off_t(api_offset, &api_offset_checked)", executor)
        self.assertIn("binding->api_offset = api_offset_checked;", executor)
        self.assertIn("return -EOPNOTSUPP;", executor)
        self.assertIn("return -ERANGE;", executor)

    def test_vulkan_dispatch_v5_1_image_materialization_is_fail_closed_and_typed(self):
        executor = GPU_EXECUTOR.read_text()
        self.assertIn("materialize_vulkan_dispatch_images", executor)
        self.assertIn("vkCreateImage(device, &ici, NULL, &dst->image)", executor)
        self.assertIn("vkGetImageMemoryRequirements(device, dst->image, &dst->requirements)", executor)
        self.assertIn("vkBindImageMemory(device, image->image", executor)
        self.assertIn("vkCreateImageView(device, &ivci, NULL, &dst->view)", executor)
        self.assertIn("vkCreateSampler(device, &sci, NULL, &dst->sampler)", executor)
        self.assertIn("destroy_vulkan_dispatch_image_objects(rt->device", executor)
        self.assertIn("src->tiling != VK_IMAGE_TILING_LINEAR", executor)
        self.assertIn("src->tiling == VK_IMAGE_TILING_OPTIMAL", executor)
        self.assertIn("VK_IMAGE_USAGE_TRANSFER_DST_BIT", executor)
        self.assertIn("VK_IMAGE_USAGE_TRANSFER_SRC_BIT", executor)
        self.assertIn("create_vulkan_buffer_with_usage", executor)
        self.assertIn("vkCmdCopyBufferToImage", executor)
        self.assertIn("vkCmdCopyImageToBuffer", executor)
        self.assertIn("image->requires_staging", executor)
        self.assertIn("image->staging.map", executor)
        self.assertIn("vulkan_image_tight_subresource_offset", executor)
        self.assertIn("image->copy_base_mip", executor)
        self.assertIn("image->copy_level_count", executor)
        self.assertIn("image->copy_base_layer", executor)
        self.assertIn("image->copy_layer_count", executor)
        self.assertIn(".mipLevel = mip", executor)
        self.assertIn(".baseArrayLayer = image->copy_base_layer", executor)
        self.assertIn(".layerCount = image->copy_layer_count", executor)
        self.assertIn("vulkan_image_create_initial_layout_for_transport", executor)
        self.assertIn("vulkan_dispatch_msaa_image_allowed", executor)
        self.assertIn("vulkan_dispatch_sample_count_supported", executor)
        format_bpp_body = executor.split("static uint32_t vulkan_format_bytes_per_pixel_conservative", 1)[1].split(
            "static uint32_t vulkan_format_bytes_per_pixel_for_aspect", 1
        )[0]
        self.assertIn("return 0;", format_bpp_body)
        self.assertIn("vulkan_dispatch_image_usage_supported_by_format", executor)
        self.assertIn("vulkan_dispatch_image_view_range_valid", executor)
        msaa_helper_body = executor.split("static int vulkan_dispatch_msaa_image_allowed", 1)[1].split(
            "static int materialize_vulkan_dispatch_images", 1
        )[0]
        convert_body = executor.split("static int convert_vulkan_dispatch_v5_to_v4_bindings", 1)[1].split(
            "static int recv_vulkan_dispatch_v5_header_with_fds", 1
        )[0]
        materialize_body = executor.split("static int materialize_vulkan_dispatch_images", 1)[1].split("static int run_vulkan_dispatch_fd", 1)[0]
        self.assertIn("image_views[i].image_index >= image_count", convert_body)
        self.assertIn("!vulkan_dispatch_image_view_range_valid(image, &image_views[i])", convert_body)
        self.assertIn("const unsigned char *msaa_image_allowed", materialize_body)
        self.assertIn("!vulkan_dispatch_msaa_image_allowed(", materialize_body)
        self.assertIn("!vulkan_dispatch_image_usage_supported_by_format(", materialize_body)
        self.assertIn("!vulkan_dispatch_image_view_range_valid(src_image, src)", materialize_body)
        self.assertIn("dst->samples = (VkSampleCountFlagBits)src->samples;", materialize_body)
        self.assertIn("dst->requires_staging = src->tiling == VK_IMAGE_TILING_OPTIMAL", materialize_body)
        self.assertIn("dst->direct_host_upload_needed = src->tiling == VK_IMAGE_TILING_LINEAR", materialize_body)
        self.assertIn("src->samples == VK_SAMPLE_COUNT_1_BIT", materialize_body)
        self.assertIn("if (dst->direct_host_upload_needed)", materialize_body)
        self.assertIn("if (memory->requires_device_local) return -EOPNOTSUPP;", materialize_body)
        self.assertIn("memory->requires_device_local = 1;", materialize_body)
        self.assertIn("} else if (image->direct_host_upload_needed) {", materialize_body)
        self.assertIn("dst->upload_pending = dst->requires_staging;", materialize_body)
        self.assertIn("src->samples == VK_SAMPLE_COUNT_1_BIT", msaa_helper_body)
        self.assertIn("const VkImageUsageFlags accepted_msaa_usage", msaa_helper_body)
        self.assertIn("src->usage & ~accepted_msaa_usage", msaa_helper_body)
        self.assertIn("VK_IMAGE_USAGE_TRANSFER_SRC_BIT", msaa_helper_body)
        self.assertNotIn("VK_IMAGE_USAGE_TRANSFER_DST_BIT", msaa_helper_body)
        self.assertIn("src->usage & VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT", msaa_helper_body)
        self.assertNotIn("src->usage & (VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT |", msaa_helper_body)
        self.assertIn("src->mip_levels != 1", msaa_helper_body)
        self.assertIn("vulkan_format_has_depth_aspect((VkFormat)src->format)", msaa_helper_body)
        self.assertIn("vulkan_format_has_stencil_aspect((VkFormat)src->format)", msaa_helper_body)
        self.assertIn("create_initial_layout =", materialize_body)
        self.assertIn(".initialLayout = create_initial_layout", materialize_body)
        self.assertIn("dst->current_layout = vulkan_replay_layout_for_executor(create_initial_layout);", materialize_body)
        self.assertNotIn("src->initial_layout != VK_IMAGE_LAYOUT_UNDEFINED", materialize_body)
        self.assertNotIn("src->initial_layout != VK_IMAGE_LAYOUT_PREINITIALIZED", materialize_body)
        self.assertIn("checked_u64_add3(memory->external_offset", executor)
        self.assertIn("checked_u64_to_off_t(fd_offset_u64", executor)
        self.assertIn("vulkan_image_descriptor_layout_valid(descriptor_type, d->image_layout)", executor)
        self.assertIn("descriptor_type != VK_DESCRIPTOR_TYPE_STORAGE_IMAGE", executor)
        self.assertIn("vulkan_required_usage_for_image_descriptor(descriptor_type)", executor)
        self.assertIn("descriptor_layout_seen", executor)
        self.assertIn("d->resource_id != views[d->image_view_index].view_id", executor)
        self.assertIn("d->resource_index != PDOCKER_GPU_V5_DESCRIPTOR_OBJECT_NONE", executor)
        self.assertIn("VkDeviceSize descriptor_offset = 0;", executor)
        self.assertIn("vulkan_graphics_replay_buffer_vk_offset_for_range", executor)
        self.assertIn("d->resource_id != samplers[d->sampler_index].sampler_id", executor)
        self.assertIn("VkDescriptorImageInfo image_infos", executor)
        self.assertIn("writes[write_count].pImageInfo = &image_infos[write_count];", executor)
        self.assertIn("VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER", executor)
        self.assertIn("VK_DESCRIPTOR_TYPE_STORAGE_IMAGE", executor)

    def test_vulkan_dispatch_v5_sampled_depth_stencil_images_are_aspect_aware(self):
        executor = GPU_EXECUTOR.read_text()
        usage_body = executor.split(
            "static int vulkan_dispatch_image_usage_supported_by_format", 1
        )[1].split("static int vulkan_image_single_aspect_supported_for_format", 1)[0]
        helper_body = executor.split(
            "static int vulkan_dispatch_descriptor_image_aspect_supported", 1
        )[1].split("static int vulkan_dispatch_merge_descriptor_image_copy_range", 1)[0]
        materialize_body = executor.split(
            "static int materialize_vulkan_dispatch_images", 1
        )[1].split("static void destroy_strict_vulkan_object_graph", 1)[0]
        run_body = executor.split(
            "static int run_vulkan_dispatch_fd", 1
        )[1].split("static void write_vulkan_image_format_caps_report", 1)[0]

        self.assertIn("VK_IMAGE_USAGE_SAMPLED_BIT", usage_body)
        self.assertIn("VK_IMAGE_USAGE_STORAGE_BIT)) && depth_stencil", usage_body)
        self.assertNotIn("VK_IMAGE_USAGE_SAMPLED_BIT)) && depth_stencil", usage_body)
        self.assertIn("vulkan_image_single_aspect_supported_for_format(image->format, aspect)", helper_body)
        self.assertIn("case VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE:", helper_body)
        self.assertIn("case VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER:", helper_body)
        self.assertIn("descriptor_type == VK_DESCRIPTOR_TYPE_STORAGE_IMAGE", helper_body)
        self.assertIn("aspect != VK_IMAGE_ASPECT_COLOR_BIT", helper_body)
        self.assertIn("vulkan_dispatch_descriptor_image_aspect_supported", materialize_body)
        self.assertIn("vulkan_dispatch_merge_descriptor_image_copy_range", materialize_body)
        self.assertNotIn("range->aspectMask != VK_IMAGE_ASPECT_COLOR_BIT", materialize_body)
        self.assertIn("vulkan_image_copy_aspect_list", run_body)
        self.assertIn("vulkan_image_tight_subresource_offset_for_aspect", run_body)
        self.assertIn("vulkan_image_tight_copy_size_for_aspect", run_body)
        self.assertIn(".size = (VkDeviceSize)image->staging.size", run_body)
        self.assertIn("vulkan_image_pack_packed_depth_stencil_from_planes(&dispatch_images[i])", run_body)

    def test_graphics_storage_image_write_descriptor_is_not_rejected_at_materialize(self):
        executor = GPU_EXECUTOR.read_text()
        preflight_body = executor.split(
            "static int preflight_vulkan_graphics_v6_replay_supported", 1
        )[1].split("static int collect_graphics_descriptor_layout_for_layout", 1)[0]
        materialize_body = executor.split(
            "static int materialize_vulkan_graphics_v6_descriptors", 1
        )[1].split("static int record_vulkan_graphics_v6_staged_image_uploads", 1)[0]

        for body in [preflight_body, materialize_body]:
            self.assertIn("descriptor_type != VK_DESCRIPTOR_TYPE_STORAGE_BUFFER", body)
            self.assertIn("descriptor_type != VK_DESCRIPTOR_TYPE_STORAGE_IMAGE", body)
        self.assertIn("graphics write descriptor replay is not implemented", preflight_body)
        self.assertIn("image->writeback_needed = 1", materialize_body)
        self.assertIn("vulkan_required_usage_for_image_descriptor(descriptor_type)", materialize_body)
        self.assertIn("descriptor_type == VK_DESCRIPTOR_TYPE_STORAGE_IMAGE", materialize_body)

    def test_vulkan_dispatch_v5_descriptor_arrays_drive_executor_layout_pool_write_and_cache(self):
        executor = GPU_EXECUTOR.read_text()
        self.assertIn("uint32_t api_array_element;", executor)
        self.assertIn("u32 = bindings[i].api_array_element;", executor)
        self.assertIn("binding->api_array_element = d->array_element;", executor)
        self.assertIn("set_binding_descriptor_counts", executor)
        self.assertIn("bindings[i].api_array_element >= PDOCKER_GPU_MAX_VULKAN_BINDINGS", executor)
        self.assertIn("const uint32_t needed_descriptor_count = bindings[i].api_array_element + 1;", executor)
        self.assertIn("layout_bindings[set_index][i].descriptorCount =", executor)
        self.assertIn("set_binding_descriptor_counts[set_index][i] ?", executor)
        self.assertIn("descriptor_pool_uniform_count += descriptor_count;", executor)
        self.assertIn("descriptor_pool_storage_count += descriptor_count;", executor)
        self.assertIn("writes[write_count].dstArrayElement = bindings[i].api_array_element;", executor)
        self.assertIn("descriptor_layout_hash", executor)
        self.assertIn("vulkan_descriptor_layout_hash", executor)
        self.assertIn("entry->descriptor_layout_hash == descriptor_layout_hash", executor)
        self.assertIn("pipeline_cache_entry->descriptor_layout_hash = descriptor_layout_hash;", executor)
        self.assertIn("descriptor alias rewrite does not support descriptor arrays", executor)

    def test_vulkan_dispatch_v5_header_validator_is_separate_from_text_commands(self):
        executor = GPU_EXECUTOR.read_text()
        self.assertIn("recv_vulkan_dispatch_v5_header_with_fds", executor)
        self.assertIn("validate_vulkan_dispatch_v5_header", executor)
        self.assertIn("MSG_WAITALL", executor)
        self.assertIn("read_exact_bytes", executor)
        self.assertIn("range_within_frame", executor)
        self.assertIn("memcmp(header->magic, PDOCKER_GPU_VULKAN_DISPATCH_V5_MAGIC, 8)", executor)
        self.assertIn("header->header_size != sizeof(PdockerGpuVulkanDispatchV5FrameHeader)", executor)
        self.assertIn("header->fd_count != received_fd_count", executor)
        self.assertIn("header->resource_schema_hash != PDOCKER_GPU_VULKAN_DISPATCH_V5_RESOURCE_SCHEMA_HASH", executor)
        self.assertIn("header->descriptor_schema_hash != PDOCKER_GPU_VULKAN_DISPATCH_V5_DESCRIPTOR_SCHEMA_HASH", executor)
        self.assertIn("resource_bytes != header->resource_table_size", executor)
        self.assertIn("descriptor_bytes != header->descriptor_table_size", executor)
        v5_recv_body = executor.split("static int recv_vulkan_dispatch_v5_header_with_fds", 1)[1].split(
            "static int recv_vulkan_dispatch_v5_frame", 1
        )[0]
        self.assertIn("MSG_TRUNC | MSG_CTRUNC", v5_recv_body)
        self.assertIn("(size_t)n != sizeof(*header)", v5_recv_body)
        self.assertIn("close(passed_fds[i]);", v5_recv_body)
        self.assertIn("*fd_count = 0;", v5_recv_body)
        text_recv = executor.split("static int recv_command_with_fds", 1)[1].split(
            "static int serve_socket", 1
        )[0]
        self.assertIn('cmd[strcspn(cmd, "\\r\\n")] = \'\\0\';', text_recv)
        self.assertNotIn("validate_vulkan_dispatch_v5_header", text_recv)

    def test_vulkan_dispatch_v5_schema_hashes_match_declared_field_macros(self):
        schemas = [
            (
                "PDOCKER_GPU_VULKAN_DISPATCH_V5_FRAME_HEADER_FIELDS",
                "PDOCKER_GPU_VULKAN_DISPATCH_V5_FRAME_HEADER_FIELD_COUNT",
                "PDOCKER_GPU_VULKAN_DISPATCH_V5_FRAME_HEADER_SCHEMA_HASH",
            ),
            (
                "PDOCKER_GPU_VULKAN_DISPATCH_V5_RESOURCE_FIELDS",
                "PDOCKER_GPU_VULKAN_DISPATCH_V5_RESOURCE_FIELD_COUNT",
                "PDOCKER_GPU_VULKAN_DISPATCH_V5_RESOURCE_SCHEMA_HASH",
            ),
            (
                "PDOCKER_GPU_VULKAN_DISPATCH_V5_DESCRIPTOR_FIELDS",
                "PDOCKER_GPU_VULKAN_DISPATCH_V5_DESCRIPTOR_FIELD_COUNT",
                "PDOCKER_GPU_VULKAN_DISPATCH_V5_DESCRIPTOR_SCHEMA_HASH",
            ),
            (
                "PDOCKER_GPU_VULKAN_DISPATCH_V5_SPECIALIZATION_FIELDS",
                "PDOCKER_GPU_VULKAN_DISPATCH_V5_SPECIALIZATION_FIELD_COUNT",
                "PDOCKER_GPU_VULKAN_DISPATCH_V5_SPECIALIZATION_SCHEMA_HASH",
            ),
            (
                "PDOCKER_GPU_VULKAN_DISPATCH_V5_IMAGE_FIELDS",
                "PDOCKER_GPU_VULKAN_DISPATCH_V5_IMAGE_FIELD_COUNT",
                "PDOCKER_GPU_VULKAN_DISPATCH_V5_IMAGE_SCHEMA_HASH",
            ),
            (
                "PDOCKER_GPU_VULKAN_DISPATCH_V5_IMAGE_VIEW_FIELDS",
                "PDOCKER_GPU_VULKAN_DISPATCH_V5_IMAGE_VIEW_FIELD_COUNT",
                "PDOCKER_GPU_VULKAN_DISPATCH_V5_IMAGE_VIEW_SCHEMA_HASH",
            ),
            (
                "PDOCKER_GPU_VULKAN_DISPATCH_V5_SAMPLER_FIELDS",
                "PDOCKER_GPU_VULKAN_DISPATCH_V5_SAMPLER_FIELD_COUNT",
                "PDOCKER_GPU_VULKAN_DISPATCH_V5_SAMPLER_SCHEMA_HASH",
            ),
            (
                "PDOCKER_GPU_VULKAN_DISPATCH_V5_DESCRIPTOR_OBJECT_FIELDS",
                "PDOCKER_GPU_VULKAN_DISPATCH_V5_DESCRIPTOR_OBJECT_FIELD_COUNT",
                "PDOCKER_GPU_VULKAN_DISPATCH_V5_DESCRIPTOR_OBJECT_SCHEMA_HASH",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V6_FRAME_HEADER_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V6_FRAME_HEADER_FIELD_COUNT",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V6_FRAME_HEADER_SCHEMA_HASH",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V6_SHADER_STAGE_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V6_SHADER_STAGE_FIELD_COUNT",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V6_SHADER_STAGE_SCHEMA_HASH",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V6_PIPELINE_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V6_PIPELINE_FIELD_COUNT",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V6_PIPELINE_SCHEMA_HASH",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V6_VERTEX_BINDING_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V6_VERTEX_BINDING_FIELD_COUNT",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V6_VERTEX_BINDING_SCHEMA_HASH",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V6_VERTEX_ATTRIBUTE_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V6_VERTEX_ATTRIBUTE_FIELD_COUNT",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V6_VERTEX_ATTRIBUTE_SCHEMA_HASH",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V6_ATTACHMENT_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V6_ATTACHMENT_FIELD_COUNT",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V6_ATTACHMENT_SCHEMA_HASH",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V6_DYNAMIC_STATE_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V6_DYNAMIC_STATE_FIELD_COUNT",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V6_DYNAMIC_STATE_SCHEMA_HASH",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V61_HEADER_EXTENSION_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V61_HEADER_EXTENSION_FIELD_COUNT",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V61_HEADER_EXTENSION_SCHEMA_HASH",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V61_DYNAMIC_OFFSET_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V61_DYNAMIC_OFFSET_FIELD_COUNT",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V61_DYNAMIC_OFFSET_SCHEMA_HASH",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V61_PUSH_CONSTANT_METADATA_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V61_PUSH_CONSTANT_METADATA_FIELD_COUNT",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V61_PUSH_CONSTANT_METADATA_SCHEMA_HASH",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V61_IMAGE_BARRIER_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V61_IMAGE_BARRIER_FIELD_COUNT",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V61_IMAGE_BARRIER_SCHEMA_HASH",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V61_MEMORY_BARRIER_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V61_MEMORY_BARRIER_FIELD_COUNT",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V61_MEMORY_BARRIER_SCHEMA_HASH",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V61_BUFFER_BARRIER_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V61_BUFFER_BARRIER_FIELD_COUNT",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V61_BUFFER_BARRIER_SCHEMA_HASH",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V64_HEADER_EXTENSION_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V64_HEADER_EXTENSION_FIELD_COUNT",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V64_HEADER_EXTENSION_SCHEMA_HASH",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V64_RESOLVE_ATTACHMENT_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V64_RESOLVE_ATTACHMENT_FIELD_COUNT",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V64_RESOLVE_ATTACHMENT_SCHEMA_HASH",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V65_HEADER_EXTENSION_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V65_HEADER_EXTENSION_FIELD_COUNT",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V65_HEADER_EXTENSION_SCHEMA_HASH",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V65_STATIC_PIPELINE_STATE_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V65_STATIC_PIPELINE_STATE_FIELD_COUNT",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V65_STATIC_PIPELINE_STATE_SCHEMA_HASH",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V66_HEADER_EXTENSION_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V66_HEADER_EXTENSION_FIELD_COUNT",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V66_HEADER_EXTENSION_SCHEMA_HASH",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V66_COLOR_BLEND_STATE_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V66_COLOR_BLEND_STATE_FIELD_COUNT",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V66_COLOR_BLEND_STATE_SCHEMA_HASH",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V66_COLOR_BLEND_ATTACHMENT_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V66_COLOR_BLEND_ATTACHMENT_FIELD_COUNT",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V66_COLOR_BLEND_ATTACHMENT_SCHEMA_HASH",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V67_HEADER_EXTENSION_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V67_HEADER_EXTENSION_FIELD_COUNT",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V67_HEADER_EXTENSION_SCHEMA_HASH",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V67_VIEWPORT_SCISSOR_STATE_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V67_VIEWPORT_SCISSOR_STATE_FIELD_COUNT",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V67_VIEWPORT_SCISSOR_STATE_SCHEMA_HASH",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V67_VIEWPORT_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V67_VIEWPORT_FIELD_COUNT",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V67_VIEWPORT_SCHEMA_HASH",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V67_SCISSOR_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V67_SCISSOR_FIELD_COUNT",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V67_SCISSOR_SCHEMA_HASH",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V68_HEADER_EXTENSION_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V68_HEADER_EXTENSION_FIELD_COUNT",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V68_HEADER_EXTENSION_SCHEMA_HASH",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V68_INDIRECT_DRAW_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V68_INDIRECT_DRAW_FIELD_COUNT",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V68_INDIRECT_DRAW_SCHEMA_HASH",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V618_HEADER_EXTENSION_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V618_HEADER_EXTENSION_FIELD_COUNT",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V618_HEADER_EXTENSION_SCHEMA_HASH",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V618_COPY_QUERY_RESULT_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V618_COPY_QUERY_RESULT_FIELD_COUNT",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V618_COPY_QUERY_RESULT_SCHEMA_HASH",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V611_HEADER_EXTENSION_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V611_HEADER_EXTENSION_FIELD_COUNT",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V611_HEADER_EXTENSION_SCHEMA_HASH",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V611_FILL_BUFFER_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V611_FILL_BUFFER_FIELD_COUNT",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V611_FILL_BUFFER_SCHEMA_HASH",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V611_UPDATE_BUFFER_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V611_UPDATE_BUFFER_FIELD_COUNT",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V611_UPDATE_BUFFER_SCHEMA_HASH",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V622_HEADER_EXTENSION_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V622_HEADER_EXTENSION_FIELD_COUNT",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V622_HEADER_EXTENSION_SCHEMA_HASH",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V622_MULTISAMPLE_STATE_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V622_MULTISAMPLE_STATE_FIELD_COUNT",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V622_MULTISAMPLE_STATE_SCHEMA_HASH",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V623_HEADER_EXTENSION_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V623_HEADER_EXTENSION_FIELD_COUNT",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V623_HEADER_EXTENSION_SCHEMA_HASH",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V623_TESSELLATION_STATE_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V623_TESSELLATION_STATE_FIELD_COUNT",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V623_TESSELLATION_STATE_SCHEMA_HASH",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V6_COMMAND_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V6_COMMAND_FIELD_COUNT",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V6_COMMAND_SCHEMA_HASH",
            ),
        ]
        for header_path in [APP_HEADER, CONTAINER_HEADER]:
            for field_macro, count_macro, hash_macro in schemas:
                with self.subTest(header=str(header_path), schema=field_macro):
                    fields, count, declared_hash, computed_hash = vulkan_dispatch_v5_schema(
                        header_path, field_macro, count_macro, hash_macro
                    )
                    self.assertEqual(len(fields), count)
                    self.assertEqual(declared_hash, computed_hash)

    def test_vulkan_graphics_v68_indirect_draw_metadata_is_append_only(self):
        app = APP_HEADER.read_text()
        container = CONTAINER_HEADER.read_text()
        executor = GPU_EXECUTOR.read_text()
        icd = VULKAN_ICD.read_text()
        for source in (app, container):
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V68_ABI_MINOR 8u", source)
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V68_HEADER_EXTENSION_FIELDS", source)
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V68_INDIRECT_DRAW_FIELDS", source)
            self.assertIn("PdockerGpuVulkanGraphicsV68FrameHeader", source)
            self.assertIn("PdockerGpuVulkanGraphicsV68IndirectDrawEntry", source)
            self.assertIn("PDOCKER_GPU_GRAPHICS_V68_INDIRECT_DRAW_COUNT_BUFFER_PRESENT", source)
        self.assertIn("PdockerGpuVulkanGraphicsV68IndirectDrawEntry indirect_draws", icd)
        self.assertIn("need_v68_indirect_draw", icd)
        self.assertIn("collect_graphics_buffer_resource", icd)
        self.assertIn("draw->indirect", icd)
        self.assertIn("vkCmdDrawIndirect", executor)
        self.assertIn("vkCmdDrawIndexedIndirect", executor)
        self.assertIn("find_vulkan_graphics_v68_indirect_draw", executor)
        self.assertIn("vulkan_graphics_replay_buffer_vk_offset_for_indirect_draw", executor)
        self.assertIn("vulkan_graphics_replay_buffer_vk_offset_for_range", executor)
        self.assertIn("sizeof(VkDrawIndirectCommand)", executor)
        self.assertIn("sizeof(VkDrawIndexedIndirectCommand)", executor)
        self.assertNotIn("(VkDeviceSize)(indirect->indirect_offset - indirect_buffer->upload_base)", executor)
        self.assertNotIn("(VkDeviceSize)(indirect->count_offset - count_buffer->upload_base)", executor)
        self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V68_ABI_MINOR", executor)

    def test_vulkan_graphics_replay_offsets_use_checked_windows(self):
        executor = GPU_EXECUTOR.read_text()
        record_body = executor.split("static int record_vulkan_graphics_v6_command_buffer", 1)[1].split(
            "static int submit_vulkan_graphics_v6_command_buffer", 1
        )[0]
        materialize_body = executor.split("static int materialize_vulkan_graphics_v6_buffers", 1)[1].split(
            "typedef struct VulkanGraphicsReplayDescriptorBind", 1
        )[0]
        helper_body = executor.split("static int vulkan_graphics_replay_buffer_vk_offset_for_range", 1)[1].split(
            "static int vulkan_graphics_replay_buffer_vk_offset_for_indirect_draw", 1
        )[0]
        self.assertIn("if (!buffer || !out_offset || !buffer->buffer.buffer)", helper_body)
        self.assertNotIn("size == 0", helper_body)
        self.assertIn("vulkan_graphics_replay_buffer_vk_offset_for_range", record_body)
        self.assertIn("index_bind_span", record_body)
        self.assertIn("range_offset = command->index_offset;", materialize_body)
        self.assertIn("!checked_add_u64_executor(first_index_bytes, index_span, &range_size)", materialize_body)
        for forbidden in [
            "- replay_buffer->upload_base",
            "- src_buffer->upload_base",
            "- dst_buffer->upload_base",
            "- dst->upload_base",
            "- indirect_buffer->upload_base",
            "- count_buffer->upload_base",
        ]:
            self.assertNotIn(forbidden, record_body)

    def test_vulkan_graphics_v6_field_macros_match_packed_structs(self):
        schemas = [
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V6_FRAME_HEADER_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V6_FRAME_HEADER_FIELD_COUNT",
                "PdockerGpuVulkanGraphicsV6FrameHeader",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V6_SHADER_STAGE_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V6_SHADER_STAGE_FIELD_COUNT",
                "PdockerGpuVulkanGraphicsV6ShaderStageEntry",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V6_PIPELINE_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V6_PIPELINE_FIELD_COUNT",
                "PdockerGpuVulkanGraphicsV6PipelineEntry",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V6_VERTEX_BINDING_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V6_VERTEX_BINDING_FIELD_COUNT",
                "PdockerGpuVulkanGraphicsV6VertexBindingEntry",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V6_VERTEX_ATTRIBUTE_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V6_VERTEX_ATTRIBUTE_FIELD_COUNT",
                "PdockerGpuVulkanGraphicsV6VertexAttributeEntry",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V6_ATTACHMENT_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V6_ATTACHMENT_FIELD_COUNT",
                "PdockerGpuVulkanGraphicsV6AttachmentEntry",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V6_DYNAMIC_STATE_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V6_DYNAMIC_STATE_FIELD_COUNT",
                "PdockerGpuVulkanGraphicsV6DynamicStateEntry",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V61_HEADER_EXTENSION_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V61_HEADER_EXTENSION_FIELD_COUNT",
                "PdockerGpuVulkanGraphicsV61HeaderExtension",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V61_DYNAMIC_OFFSET_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V61_DYNAMIC_OFFSET_FIELD_COUNT",
                "PdockerGpuVulkanGraphicsV61DynamicOffsetEntry",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V61_PUSH_CONSTANT_METADATA_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V61_PUSH_CONSTANT_METADATA_FIELD_COUNT",
                "PdockerGpuVulkanGraphicsV61PushConstantMetadataEntry",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V61_IMAGE_BARRIER_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V61_IMAGE_BARRIER_FIELD_COUNT",
                "PdockerGpuVulkanGraphicsV61ImageBarrierEntry",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V61_MEMORY_BARRIER_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V61_MEMORY_BARRIER_FIELD_COUNT",
                "PdockerGpuVulkanGraphicsV61MemoryBarrierEntry",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V61_BUFFER_BARRIER_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V61_BUFFER_BARRIER_FIELD_COUNT",
                "PdockerGpuVulkanGraphicsV61BufferBarrierEntry",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V64_HEADER_EXTENSION_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V64_HEADER_EXTENSION_FIELD_COUNT",
                "PdockerGpuVulkanGraphicsV64HeaderExtension",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V64_RESOLVE_ATTACHMENT_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V64_RESOLVE_ATTACHMENT_FIELD_COUNT",
                "PdockerGpuVulkanGraphicsV64ResolveAttachmentEntry",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V65_HEADER_EXTENSION_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V65_HEADER_EXTENSION_FIELD_COUNT",
                "PdockerGpuVulkanGraphicsV65HeaderExtension",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V65_STATIC_PIPELINE_STATE_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V65_STATIC_PIPELINE_STATE_FIELD_COUNT",
                "PdockerGpuVulkanGraphicsV65StaticPipelineStateEntry",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V66_HEADER_EXTENSION_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V66_HEADER_EXTENSION_FIELD_COUNT",
                "PdockerGpuVulkanGraphicsV66HeaderExtension",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V66_COLOR_BLEND_STATE_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V66_COLOR_BLEND_STATE_FIELD_COUNT",
                "PdockerGpuVulkanGraphicsV66ColorBlendStateEntry",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V66_COLOR_BLEND_ATTACHMENT_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V66_COLOR_BLEND_ATTACHMENT_FIELD_COUNT",
                "PdockerGpuVulkanGraphicsV66ColorBlendAttachmentEntry",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V67_HEADER_EXTENSION_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V67_HEADER_EXTENSION_FIELD_COUNT",
                "PdockerGpuVulkanGraphicsV67HeaderExtension",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V67_VIEWPORT_SCISSOR_STATE_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V67_VIEWPORT_SCISSOR_STATE_FIELD_COUNT",
                "PdockerGpuVulkanGraphicsV67ViewportScissorStateEntry",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V67_VIEWPORT_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V67_VIEWPORT_FIELD_COUNT",
                "PdockerGpuVulkanGraphicsV67ViewportEntry",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V67_SCISSOR_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V67_SCISSOR_FIELD_COUNT",
                "PdockerGpuVulkanGraphicsV67ScissorEntry",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V68_HEADER_EXTENSION_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V68_HEADER_EXTENSION_FIELD_COUNT",
                "PdockerGpuVulkanGraphicsV68HeaderExtension",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V68_INDIRECT_DRAW_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V68_INDIRECT_DRAW_FIELD_COUNT",
                "PdockerGpuVulkanGraphicsV68IndirectDrawEntry",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V618_HEADER_EXTENSION_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V618_HEADER_EXTENSION_FIELD_COUNT",
                "PdockerGpuVulkanGraphicsV618HeaderExtension",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V618_COPY_QUERY_RESULT_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V618_COPY_QUERY_RESULT_FIELD_COUNT",
                "PdockerGpuVulkanGraphicsV618CopyQueryResultEntry",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V619_HEADER_EXTENSION_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V619_HEADER_EXTENSION_FIELD_COUNT",
                "PdockerGpuVulkanGraphicsV619HeaderExtension",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V619_SUBMIT_SYNC_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V619_SUBMIT_SYNC_FIELD_COUNT",
                "PdockerGpuVulkanGraphicsV619SubmitSyncEntry",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V611_HEADER_EXTENSION_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V611_HEADER_EXTENSION_FIELD_COUNT",
                "PdockerGpuVulkanGraphicsV611HeaderExtension",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V611_FILL_BUFFER_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V611_FILL_BUFFER_FIELD_COUNT",
                "PdockerGpuVulkanGraphicsV611FillBufferEntry",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V611_UPDATE_BUFFER_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V611_UPDATE_BUFFER_FIELD_COUNT",
                "PdockerGpuVulkanGraphicsV611UpdateBufferEntry",
            ),
            (
                "PDOCKER_GPU_VULKAN_GRAPHICS_V6_COMMAND_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V6_COMMAND_FIELD_COUNT",
                "PdockerGpuVulkanGraphicsV6CommandEntry",
            ),
        ]
        for header_path in [APP_HEADER, CONTAINER_HEADER]:
            for field_macro, count_macro, struct_name in schemas:
                with self.subTest(header=str(header_path), schema=field_macro):
                    fields, count, _, _ = vulkan_dispatch_v5_schema(
                        header_path, field_macro, count_macro
                    )
                    self.assertEqual([name for name, _ in fields], c_struct_field_names(header_path, struct_name))
                    self.assertEqual(count, len(fields))

    def test_vulkan_dispatch_v5_1_object_field_macros_match_packed_structs(self):
        schemas = [
            (
                "PDOCKER_GPU_VULKAN_DISPATCH_V5_FRAME_HEADER_OBJECT_FIELDS",
                "PDOCKER_GPU_VULKAN_DISPATCH_V5_FRAME_HEADER_OBJECT_FIELD_COUNT",
                "PdockerGpuVulkanDispatchV5ObjectHeaderExtension",
            ),
            (
                "PDOCKER_GPU_VULKAN_DISPATCH_V5_IMAGE_FIELDS",
                "PDOCKER_GPU_VULKAN_DISPATCH_V5_IMAGE_FIELD_COUNT",
                "PdockerGpuVulkanDispatchV5ImageEntry",
            ),
            (
                "PDOCKER_GPU_VULKAN_DISPATCH_V5_IMAGE_VIEW_FIELDS",
                "PDOCKER_GPU_VULKAN_DISPATCH_V5_IMAGE_VIEW_FIELD_COUNT",
                "PdockerGpuVulkanDispatchV5ImageViewEntry",
            ),
            (
                "PDOCKER_GPU_VULKAN_DISPATCH_V5_SAMPLER_FIELDS",
                "PDOCKER_GPU_VULKAN_DISPATCH_V5_SAMPLER_FIELD_COUNT",
                "PdockerGpuVulkanDispatchV5SamplerEntry",
            ),
            (
                "PDOCKER_GPU_VULKAN_DISPATCH_V5_DESCRIPTOR_OBJECT_FIELDS",
                "PDOCKER_GPU_VULKAN_DISPATCH_V5_DESCRIPTOR_OBJECT_FIELD_COUNT",
                "PdockerGpuVulkanDispatchV5DescriptorObjectEntry",
            ),
        ]
        for header_path in [APP_HEADER, CONTAINER_HEADER]:
            for field_macro, count_macro, struct_name in schemas:
                with self.subTest(header=str(header_path), schema=field_macro):
                    fields, count, _, _ = vulkan_dispatch_v5_schema(header_path, field_macro, count_macro)
                    self.assertEqual(len(fields), count)
                    self.assertEqual([name for name, _ in fields], c_struct_field_names(header_path, struct_name))

    def test_vulkan_dispatch_v5_schema_is_single_source_and_advertised(self):
        app = APP_HEADER.read_text()
        container = CONTAINER_HEADER.read_text()
        markers = [
            "PDOCKER_GPU_VULKAN_DISPATCH_V5_MAGIC",
            "PDOCKER_GPU_VULKAN_DISPATCH_V5_FRAME_HEADER_FIELDS",
            "PDOCKER_GPU_VULKAN_DISPATCH_V5_RESOURCE_FIELDS",
            "PDOCKER_GPU_VULKAN_DISPATCH_V5_DESCRIPTOR_FIELDS",
            "PDOCKER_GPU_VULKAN_DISPATCH_V5_SPECIALIZATION_FIELDS",
            "PDOCKER_GPU_VULKAN_DISPATCH_V5_FRAME_HEADER_SCHEMA_HASH",
            "PDOCKER_GPU_VULKAN_DISPATCH_V5_RESOURCE_SCHEMA_HASH",
            "PDOCKER_GPU_VULKAN_DISPATCH_V5_DESCRIPTOR_SCHEMA_HASH",
            "PDOCKER_GPU_VULKAN_DISPATCH_V5_SPECIALIZATION_SCHEMA_HASH",
            "PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_FRAME_BYTES",
        ]
        for marker in markers:
            self.assertIn(marker, app)
            self.assertIn(marker, container)
        for marker in [
            "PDOCKER_GPU_V5_RESOURCE_TYPE_IMAGE",
            "PDOCKER_GPU_V5_RESOURCE_TYPE_IMAGE_VIEW",
            "PDOCKER_GPU_V5_RESOURCE_TYPE_SAMPLER",
            "PDOCKER_GPU_VULKAN_DISPATCH_V5_ABI_MINOR_OBJECTS",
            "PDOCKER_GPU_VULKAN_DISPATCH_V5_FRAME_HEADER_OBJECT_FIELDS",
            "PDOCKER_GPU_VULKAN_DISPATCH_V5_FRAME_HEADER_OBJECT_FIELD_COUNT",
            "PDOCKER_GPU_VULKAN_DISPATCH_V5_IMAGE_FIELDS",
            "PDOCKER_GPU_VULKAN_DISPATCH_V5_IMAGE_FIELD_COUNT",
            "PDOCKER_GPU_VULKAN_DISPATCH_V5_IMAGE_VIEW_FIELDS",
            "PDOCKER_GPU_VULKAN_DISPATCH_V5_IMAGE_VIEW_FIELD_COUNT",
            "PDOCKER_GPU_VULKAN_DISPATCH_V5_SAMPLER_FIELDS",
            "PDOCKER_GPU_VULKAN_DISPATCH_V5_SAMPLER_FIELD_COUNT",
            "PDOCKER_GPU_VULKAN_DISPATCH_V5_DESCRIPTOR_OBJECT_FIELDS",
            "PDOCKER_GPU_VULKAN_DISPATCH_V5_DESCRIPTOR_OBJECT_FIELD_COUNT",
            "PDOCKER_GPU_VULKAN_DISPATCH_V5_IMAGE_SCHEMA_HASH",
            "PDOCKER_GPU_VULKAN_DISPATCH_V5_IMAGE_VIEW_SCHEMA_HASH",
            "PDOCKER_GPU_VULKAN_DISPATCH_V5_SAMPLER_SCHEMA_HASH",
            "PDOCKER_GPU_VULKAN_DISPATCH_V5_DESCRIPTOR_OBJECT_SCHEMA_HASH",
        ]:
            self.assertIn(marker, app)
            self.assertIn(marker, container)
        self.assertIn('#define PDOCKER_GPU_VULKAN_DISPATCH_V5_FRAME_HEADER_FIELD_COUNT 46u', app)
        self.assertIn('#define PDOCKER_GPU_VULKAN_DISPATCH_V5_RESOURCE_FIELD_COUNT 11u', app)
        self.assertIn('#define PDOCKER_GPU_VULKAN_DISPATCH_V5_DESCRIPTOR_FIELD_COUNT 14u', app)
        self.assertIn('#define PDOCKER_GPU_VULKAN_DISPATCH_V5_SPECIALIZATION_FIELD_COUNT 3u', app)
        self.assertIn('#define PDOCKER_GPU_VULKAN_DISPATCH_V5_FRAME_HEADER_OBJECT_FIELD_COUNT 16u', app)
        self.assertIn('#define PDOCKER_GPU_VULKAN_DISPATCH_V5_IMAGE_FIELD_COUNT 20u', app)
        self.assertIn('#define PDOCKER_GPU_VULKAN_DISPATCH_V5_IMAGE_VIEW_FIELD_COUNT 15u', app)
        self.assertIn('#define PDOCKER_GPU_VULKAN_DISPATCH_V5_SAMPLER_FIELD_COUNT 19u', app)
        self.assertIn('#define PDOCKER_GPU_VULKAN_DISPATCH_V5_DESCRIPTOR_OBJECT_FIELD_COUNT 16u', app)
        for marker in markers:
            app_line = next(line for line in app.splitlines() if marker in line and line.startswith("#define"))
            container_line = next(line for line in container.splitlines() if marker in line and line.startswith("#define"))
            self.assertEqual(app_line, container_line)
        executor = GPU_EXECUTOR.read_text()
        for marker in [
            '\\"vulkan_dispatch_v5_frame\\":true',
            '\\"vulkan_dispatch_v5\\"',
            "PDOCKER_GPU_VULKAN_DISPATCH_V5_ABI_MAJOR",
            "PDOCKER_GPU_VULKAN_DISPATCH_V5_ABI_MINOR_OBJECTS",
            "PDOCKER_GPU_VULKAN_DISPATCH_V5_FRAME_HEADER_SCHEMA_HASH",
            "PDOCKER_GPU_VULKAN_DISPATCH_V5_RESOURCE_SCHEMA_HASH",
            "PDOCKER_GPU_VULKAN_DISPATCH_V5_DESCRIPTOR_SCHEMA_HASH",
            "PDOCKER_GPU_VULKAN_DISPATCH_V5_SPECIALIZATION_SCHEMA_HASH",
            "PDOCKER_GPU_VULKAN_DISPATCH_V5_IMAGE_SCHEMA_HASH",
            "PDOCKER_GPU_VULKAN_DISPATCH_V5_IMAGE_VIEW_SCHEMA_HASH",
            "PDOCKER_GPU_VULKAN_DISPATCH_V5_SAMPLER_SCHEMA_HASH",
            "PDOCKER_GPU_VULKAN_DISPATCH_V5_DESCRIPTOR_OBJECT_SCHEMA_HASH",
            "PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_RESOURCES",
            "PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_DESCRIPTORS",
            "PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_IMAGES",
            "PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_IMAGE_VIEWS",
            "PDOCKER_GPU_VULKAN_DISPATCH_V5_MAX_SAMPLERS",
            "validate_vulkan_dispatch_v5_object_extension",
            "PdockerGpuVulkanDispatchV5DescriptorObjectEntry",
        ]:
            self.assertIn(marker, executor)
        self.assertIn("supported_minors", executor)

    def test_llama_gpu_env_manifest_covers_abi_dispatch_options(self):
        manifest = json.loads(LLAMA_GPU_ENV_MANIFEST.read_text())
        app_options = vulkan_dispatch_option_envs(APP_HEADER)
        container_options = vulkan_dispatch_option_envs(CONTAINER_HEADER)
        self.assertEqual(app_options, container_options)

        manifest_options = {"bool": set(), "size": set(), "string": set()}
        for item in manifest["abi_dispatch_option_env_fields"]:
            self.assertIn(item["type"], manifest_options)
            manifest_options[item["type"]].add(item["env"])
        expected_options = {
            "bool": app_options["bool"] | {"PDOCKER_GPU_DISPATCH_PROFILE_RESPONSE"},
            "size": app_options["size"],
            "string": app_options["string"],
        }
        self.assertEqual(expected_options, manifest_options)
        self.assertEqual(
            expected_options["bool"],
            set(manifest["env_bridge_classifications"]["icd_to_executor_bool_option"]),
        )
        self.assertEqual(
            app_options["size"],
            set(manifest["env_bridge_classifications"]["icd_to_executor_size_option"]),
        )
        self.assertEqual(
            app_options["string"],
            set(manifest["env_bridge_classifications"]["icd_to_executor_string_option"]),
        )
        self.assertLessEqual(
            set(manifest["config_propagation_env_fields"][i]["env"] for i in range(len(manifest["config_propagation_env_fields"]))),
            set(manifest["compare_forward_env_keys"]),
        )
        self.assertIn("PDOCKER_GPU_LEGALIZE_WORKGROUP_SIZE_FROM_SPEC", manifest_options["bool"])
        config_fields = {item["env"]: item for item in manifest["config_propagation_env_fields"]}
        self.assertIn("PDOCKER_GPU_LEGALIZE_WORKGROUP_SIZE_FROM_SPEC", config_fields)
        self.assertEqual(
            "callsite_gated",
            config_fields["PDOCKER_GPU_MATERIALIZE_SPIRV_SPECIALIZATION_CONSTANTS"]["evidence_policy"],
        )
        for q6_env in [
            "PDOCKER_GPU_Q6K_SAFE_KERNEL",
            "PDOCKER_GPU_Q6K_ORACLE_WRITEBACK",
            "PDOCKER_GPU_Q6K_COMPAT_REWRITES",
            "PDOCKER_GPU_Q6K_READONLY_OVERLAP_SNAPSHOT",
        ]:
            self.assertEqual("q6_callsite_gated", config_fields[q6_env]["evidence_policy"])
        self.assertIn("PDOCKER_GPU_SPIRV_PROBE_DEBUG_BINDING", manifest_options["size"])
        self.assertIn("PDOCKER_GPU_SPIRV_PROBE_DEBUG_BINDING", manifest["compare_probe_env_keys"])
        q6_overlay = manifest["q6_required_env_overlay"]
        self.assertEqual(
            {
                "PDOCKER_GPU_CPU_ORACLE": "1",
                "PDOCKER_GPU_STRICT_PASSTHROUGH": "1",
                "PDOCKER_GPU_STRICT_RECONCILIATION": "1",
                "PDOCKER_GPU_STRICT_DUPLICATE_DESCRIPTOR_NORMALIZATION": "1",
                "PDOCKER_GPU_LEGALIZE_WORKGROUP_SIZE_FROM_SPEC": "1",
                "PDOCKER_GPU_MATERIALIZE_SPIRV_SPECIALIZATION_CONSTANTS": "1",
                "PDOCKER_GPU_DISPATCH_PROFILE_LOG": "1",
                "PDOCKER_GPU_DISPATCH_PROFILE_RESPONSE": "1",
                "PDOCKER_GPU_STRICT_DEVICE_LOCAL_STAGING": "1",
                "PDOCKER_GPU_Q6K_COMPAT_REWRITES": "1",
                "PDOCKER_GPU_Q6K_READONLY_OVERLAP_SNAPSHOT": "1",
            },
            q6_overlay,
        )
        self.assertLessEqual(set(q6_overlay), set(manifest["compare_forward_env_keys"]))

    def test_spirv_probe_replay_uses_existing_v4_binding_transport(self):
        icd = VULKAN_ICD.read_text()
        for marker in [
            "PDOCKER_GPU_SPIRV_PROBE_MANIFEST",
            "PDOCKER_GPU_SPIRV_PROBE_SHADER",
            "PDOCKER_GPU_SPIRV_PROBE_EXPECTED_HASH",
            "PDOCKER_GPU_SPIRV_PROBE_EFFECTIVE_HASH",
            "PDOCKER_GPU_SPIRV_PROBE_DEBUG_BYTES",
            "PDOCKER_GPU_SPIRV_PROBE_DEBUG_SET",
            "PDOCKER_GPU_SPIRV_PROBE_DEBUG_BINDING",
            "PDOCKER_GPU_SPIRV_PROBE_TARGET_ONLY",
            "SPIR-V probe replay rejected",
            "SPIR-V probe replay skipped non-target shader",
            "verify_spirv_probe_manifest_runtime_guard",
            "fstat(probe->shader_fd, &st)",
            "PDOCKER_VK_MAX_PROBE_SHADER_BYTES",
            "transport=VULKAN_DISPATCH_V4",
        ]:
            self.assertIn(marker, icd)
        self.assertIn("fds[0] = probe.shader_fd;", icd)
        self.assertIn("fds[1 + binding_count] = probe.debug_fd;", icd)
        self.assertIn("api_descriptor_sets[binding_count] = probe.debug_set;", icd)
        self.assertIn("bindings[binding_count] = probe.debug_binding;", icd)
        self.assertIn("binding_count++;", icd)
        self.assertIn('env_truthy_default("PDOCKER_GPU_SPIRV_PROBE_TARGET_ONLY", false)', icd)
        self.assertIn("sender_source_spirv_hash=0x%016llx", icd)
        self.assertIn("sender_effective_spirv_hash=0x%016llx", icd)
        self.assertIn("(unsigned long long)source_shader_hash", icd)
        self.assertIn("(unsigned long long)shader_hash_to_send", icd)
        self.assertIn("Probe replay sends an instrumented/effective SPIR-V module", icd)
        probe_identity_block = icd.index("if (probe.enabled) {", icd.index("sender_dispatch_hash"))
        self.assertLess(probe_identity_block, icd.index("sender_source_spirv_hash=0x%016llx"))
        self.assertLess(icd.index("sender_source_spirv_hash=0x%016llx"), icd.index("sender_effective_spirv_hash=0x%016llx"))
        self.assertLess(icd.index("sender_effective_spirv_hash=0x%016llx"), icd.index("typedef struct {", probe_identity_block))
        env_manifest = json.loads(LLAMA_GPU_ENV_MANIFEST.read_text())
        self.assertIn("PDOCKER_GPU_SPIRV_PROBE_TARGET_ONLY", env_manifest["compare_forward_env_keys"])
        self.assertEqual(
            [
                "PDOCKER_GPU_SPIRV_PROBE_MANIFEST",
                "PDOCKER_GPU_SPIRV_PROBE_SHADER",
                "PDOCKER_GPU_SPIRV_PROBE_EXPECTED_HASH",
                "PDOCKER_GPU_SPIRV_PROBE_EFFECTIVE_HASH",
                "PDOCKER_GPU_SPIRV_PROBE_DEBUG_BYTES",
                "PDOCKER_GPU_SPIRV_PROBE_DEBUG_SET",
                "PDOCKER_GPU_SPIRV_PROBE_DEBUG_BINDING",
                "PDOCKER_GPU_SPIRV_PROBE_TARGET_ONLY",
            ],
            env_manifest["compare_probe_env_keys"],
        )
        app_fields, app_count, app_hash, _ = v4_binding_schema(APP_HEADER)
        container_fields, container_count, container_hash, _ = v4_binding_schema(CONTAINER_HEADER)
        self.assertEqual(app_fields, container_fields)
        self.assertEqual(app_count, 13)
        self.assertEqual(container_count, 13)
        self.assertEqual(app_hash, int("0x4a322a1f9f143a20", 16))
        self.assertEqual(container_hash, int("0x4a322a1f9f143a20", 16))
        self.assertIn("send_generic_vulkan_dispatch_v5_1_op", icd)
        self.assertIn("send_vulkan_dispatch_v5_frame_with_fds", icd)
        self.assertIn("PdockerGpuVulkanDispatchV5ObjectFrameHeader", icd)
        self.assertIn("PdockerGpuVulkanDispatchV5DescriptorObjectEntry", icd)
        self.assertIn("PDOCKER_GPU_VULKAN_DISPATCH_V5_MAGIC", icd)
        self.assertIn("PDOCKER_GPU_VULKAN_DISPATCH_V5_ABI_MINOR_OBJECTS", icd)
        self.assertIn("PDOCKER_GPU_VULKAN_DISPATCH_V5_DESCRIPTOR_OBJECT_SCHEMA_HASH", icd)
        self.assertIn("CMSG_LEN(sizeof(int) * fd_count)", icd)
        self.assertIn("SCM_RIGHTS", icd)
        self.assertIn("frame + sizeof(PdockerGpuVulkanDispatchV5FrameHeader)", icd)
        self.assertIn("resource_table_offset", icd)
        self.assertIn("descriptor_table_offset", icd)
        self.assertIn('env_truthy_default("PDOCKER_VULKAN_USE_V5_FRAME", false)', icd)
        self.assertIn("VULKAN_DISPATCH_V5.1", icd)
        self.assertNotIn("SPIRV_PROBE_DISPATCH", icd)

    def test_strict_passthrough_rejects_copy_alias_transport(self):
        icd = VULKAN_ICD.read_text()
        self.assertIn('env_truthy_default("PDOCKER_GPU_STRICT_PASSTHROUGH", false)', icd)
        self.assertIn("strict_passthrough && copy_alias_enabled()", icd)
        self.assertIn("rejecting PDOCKER_VULKAN_ALIAS_COPIES under strict passthrough", icd)

    def test_vulkan_dispatch_reports_binding_diagnostics(self):
        source = GPU_EXECUTOR.read_text()
        self.assertIn("PDOCKER_GPU_EXECUTOR_BUILD_MARKER", source)
        self.assertIn('\\"executor_build_marker\\":\\"%s\\"', source)
        self.assertIn("build_marker=%s", source)
        self.assertIn('\\"binding_details\\":[', source)
        self.assertIn("profile_response", source)
        self.assertIn("PDOCKER_GPU_DISPATCH_PROFILE_RESPONSE", source)
        self.assertIn("if (profile_response) {", source)
        for field in [
            '\\"binding\\":%u',
            '\\"offset\\":%lld',
            '\\"size\\":%zu',
            '\\"alias_rep\\":%zu',
            '\\"active\\":%s',
            '\\"readable\\":%s',
            '\\"writable\\":%s',
            '\\"resident\\":%s',
            '\\"cache_hit\\":%s',
            '\\"mutable_reused\\":%s',
            '\\"mutable_cache_hit\\":%s',
            '\\"upload_ms\\":%.4f',
            '\\"download_ms\\":%.4f',
            '\\"dirty_probe_pages\\":%zu',
            '\\"dirty_probe_bytes\\":%zu',
            '\\"dirty_probe_ms\\":%.4f',
            '\\"dirty_writeback_cached\\":%s',
            '\\"dirty_writeback_bytes\\":%zu',
            '\\"writeback_offset\\":%lld',
            '\\"writeback_bytes\\":%zu',
            '\\"device_local_staged\\":%s',
            '\\"fd_before_hash\\":\\"0x%016llx\\"',
            '\\"gpu_after_upload_hash\\":\\"0x%016llx\\"',
            '\\"gpu_after_dispatch_hash\\":\\"0x%016llx\\"',
            '\\"fd_after_hash\\":\\"0x%016llx\\"',
        ]:
            self.assertIn(field, source)
        self.assertGreaterEqual(source.count("write_vulkan_binding_report(json_out()"), 2)
        self.assertIn("binding_upload_ms[PDOCKER_GPU_MAX_VULKAN_BINDINGS]", source)
        self.assertIn("binding_download_ms[PDOCKER_GPU_MAX_VULKAN_BINDINGS]", source)
        self.assertIn("binding_dirty_probe_pages[PDOCKER_GPU_MAX_VULKAN_BINDINGS]", source)
        self.assertIn("binding_dirty_probe_bytes[PDOCKER_GPU_MAX_VULKAN_BINDINGS]", source)
        self.assertIn("binding_fd_before_hash[PDOCKER_GPU_MAX_VULKAN_BINDINGS]", source)
        self.assertIn("binding_gpu_after_dispatch_hash[PDOCKER_GPU_MAX_VULKAN_BINDINGS]", source)
        self.assertIn("binding_alias_rep[PDOCKER_GPU_MAX_VULKAN_BINDINGS]", source)
        self.assertIn("binding_group_read_needed[PDOCKER_GPU_MAX_VULKAN_BINDINGS]", source)
        self.assertIn("binding_group_base[PDOCKER_GPU_MAX_VULKAN_BINDINGS]", source)
        self.assertIn("binding_group_end[PDOCKER_GPU_MAX_VULKAN_BINDINGS]", source)
        self.assertIn("binding_gpu_offset[PDOCKER_GPU_MAX_VULKAN_BINDINGS]", source)
        self.assertIn("write_vulkan_descriptor_write_report", source)
        self.assertIn("write_vulkan_descriptor_alias_report", source)
        self.assertIn("write_vulkan_binding_compact_report", source)
        self.assertIn('\\"f32_after_dispatch\\":', source)
        self.assertIn('\\"f32_after_writeback\\":', source)
        self.assertIn('\\"q6_row_indexed\\":true', source)
        self.assertIn("probe_debug_binding_from_options", source)
        self.assertIn("PDOCKER_GPU_SPIRV_PROBE_DEBUG_BINDING", source)
        self.assertIn("has_spirv_probe_debug_binding", source)
        self.assertIn("spirv_probe_debug_binding", source)
        self.assertIn('\\"debug_probe_binding\\":true', source)
        self.assertIn('\\"u32_after_dispatch\\":', source)
        self.assertIn('\\"u32_after_writeback\\":', source)
        self.assertIn("write_u32_sample_array_prefix", source)
        self.assertIn("write_u32_fd_sample_array_prefix", source)
        self.assertIn('\\"q6_sample_indices\\":[', source)
        self.assertIn('\\"q6_final_store_output_indices\\":[', source)
        self.assertIn("collect_q6_row_indexed_sample_indices", source)
        self.assertIn("append_q6_final_store_output_indices_from_debug_probe", source)
        self.assertIn("write_q6_row_indexed_f32_evidence", source)
        self.assertIn("q6_first_mismatch", source)
        self.assertIn("row_window", source)
        self.assertIn("Q6OutputLayoutProbeSample", source)
        self.assertIn("PDOCKER_GPU_Q6_OUTPUT_LAYOUT_PROBE_MAX_SAMPLES 48u", source)
        self.assertIn("PDOCKER_GPU_Q6_OUTPUT_LAYOUT_PROBE_MAX_FLOATS 4096u", source)
        self.assertIn(
            "q6_output_layout_probe_samples[\n"
            "        PDOCKER_GPU_Q6_OUTPUT_LAYOUT_PROBE_MAX_SAMPLES]",
            source,
        )
        self.assertIn("q6k_record_output_layout_probe_sample", source)
        self.assertIn("q6k_finalize_output_layout_probe", source)
        self.assertIn("write_q6_row_provenance_probe", source)
        self.assertIn('\\"q6_row_provenance_probe\\":', source)
        self.assertIn("row-provenance-inconsistent", source)
        self.assertIn("Q6PartialSignatureProbeSample", source)
        self.assertIn("PDOCKER_GPU_Q6_PARTIAL_SIGNATURE_PROBE_MAX_SAMPLES 64u", source)
        self.assertIn("q6k_record_partial_signature_probe_sample", source)
        self.assertIn("q6k_finalize_partial_signature_probe", source)
        self.assertIn("write_q6_partial_signature_probe", source)
        self.assertIn('\\"q6_partial_signature_probe\\":', source)
        self.assertIn('\\"partial_lanes\\":[', source)
        self.assertIn('\\"shader_like_lanes\\":[', source)
        self.assertIn("write_json_double_or_null", source)
        self.assertIn('\\"native_reduction_tree_with_accumulator\\":', source)
        self.assertIn("write_json_double_or_null(out, sample->native_reduction_tree_with_accumulator)", source)
        self.assertIn("write_json_double_or_null(out, sample->native_reduction_tree_gpu_abs_error)", source)
        self.assertIn("write_json_double_or_null(out, sample->expected_gpu_abs_error)", source)
        self.assertIn("local-y-partial", source)
        self.assertIn("lane-partial", source)
        self.assertIn("q6k_decode_batch_index(base_work_group_y, ne02, ne12, broadcast2, broadcast3", source)
        self.assertIn("weight_batch_stride_elements = load_le_u32(push, push_size, 4)", source)
        self.assertIn("const uint32_t ne02 = load_le_u32(push, push_size, 9)", source)
        self.assertIn("const uint32_t ne12 = load_le_u32(push, push_size, 10)", source)
        self.assertIn("const uint32_t broadcast2 = load_le_u32(push, push_size, 11)", source)
        self.assertIn("const uint32_t broadcast3 = load_le_u32(push, push_size, 12)", source)
        self.assertIn("llama.cpp's push-constant contract exactly", source)
        self.assertIn("canonical-mismatch-inconclusive", source)
        self.assertIn('\\"consistent_relative_offset\\":%s', source)
        self.assertIn("q6k_native_reduction_tree_sum32", source)
        self.assertIn('\\"q6_output_layout_probe\\":', source)
        self.assertIn('\\"source_spirv_hash\\":\\"0x%016llx\\"', source)
        self.assertIn('\\"effective_spirv_hash\\":\\"0x%016llx\\"', source)
        self.assertIn("write_f32_sample_array", source)
        self.assertIn("write_f32_fd_sample_array", source)
        self.assertIn("write_f32_sample_array_at_indices", source)
        self.assertIn("write_f32_fd_sample_array_at_indices", source)
        self.assertIn('\\"compact_summary\\":true', source)
        self.assertIn("write_spirv_feature_report(json_out(), &spirv_summary, &effective_rt, options);", source)
        self.assertIn("write_spirv_execution_report(json_out(),", source)
        self.assertIn('\\"spirv_local_size_resolved\\":[', source)
        self.assertIn('\\"specialization_entries\\":[', source)
        self.assertIn('\\"duplicate_descriptor_rewrite\\":%s', source)
        self.assertIn('\\"materialize_specialization\\":%s', source)
        self.assertIn('\\"legalize_workgroup_size_from_spec\\":%s', source)
        self.assertIn('\\"legalize_workgroup_size_from_spec_source\\":\\"%s\\"', source)
        self.assertIn("has_legalize_workgroup_size_from_spec", source)
        self.assertIn("const char *legalize_workgroup_env", source)
        self.assertIn("strict_passthrough\n            ? (legalize_workgroup_env ?", source)
        self.assertIn('env_truthy("PDOCKER_GPU_LEGALIZE_WORKGROUP_SIZE_FROM_SPEC", 0)', source)
        self.assertIn('env_truthy("PDOCKER_GPU_LEGALIZE_WORKGROUP_SIZE_FROM_SPEC", 1)', source)
        self.assertIn("legalize_workgroup_size_from_spec", APP_HEADER.read_text())
        self.assertIn("PDOCKER_GPU_VULKAN_BOOL_DISPATCH_OPTIONS(PDOCKER_EXEC_BOOL_DISPATCH_OPTION)", source)
        self.assertIn('options->legalize_workgroup_size_from_spec', source)
        self.assertIn('"option"', source)
        self.assertIn('"env-default"', source)
        self.assertIn('"strict-passthrough"', source)
        self.assertIn('"strict-env"', source)
        self.assertIn('"strict-option"', source)
        self.assertIn('\\"descriptor_writes\\":[', source)
        self.assertIn('\\"descriptor_alias_map\\":[', source)
        self.assertIn('\\"target_id\\":%u', source)
        self.assertIn('\\"dst_binding\\":%u', source)
        self.assertIn('\\"source_index\\":%zu', source)
        self.assertIn('\\"source_binding\\":%u', source)
        self.assertIn('\\"alias_write\\":%s', source)
        self.assertIn("binding_group_span_seen[PDOCKER_GPU_MAX_VULKAN_BINDINGS]", source)
        self.assertIn("binding_alias_rep[i] != i", source)
        self.assertIn("i0 < j1 && j0 < i1", source)
        self.assertIn("!binding_group_span_seen[rep] || start < binding_group_base[rep]", source)
        self.assertIn("binding_descriptor_offset[PDOCKER_GPU_MAX_VULKAN_BINDINGS]", source)
        self.assertIn("vulkan_binding_offset_equals_memory_plus_api_offset", source)
        self.assertIn("vulkan_binding_gpu_offset_equals_memory_plus_api_offset", source)
        self.assertIn("vulkan_binding_descriptor_offset_equals_api_offset", source)
        self.assertIn("vulkan_binding_descriptor_range_matches_api_range", source)
        self.assertIn("infos[write_count].offset = (VkDeviceSize)binding_descriptor_offset[i];", source)
        self.assertIn("PDOCKER_GPU_STRICT_DEVICE_LOCAL_STAGING", source)
        self.assertIn("has_strict_device_local_staging", source)
        abi = APP_HEADER.read_text()
        self.assertIn(
            "X(PDOCKER_GPU_STRICT_DEVICE_LOCAL_STAGING, strict_device_local_staging, "
            "has_strict_device_local_staging, strict_device_local_staging, 0)",
            abi,
        )
        self.assertIn("PDOCKER_GPU_VULKAN_BOOL_DISPATCH_OPTIONS(PDOCKER_EXEC_BOOL_DISPATCH_OPTION)", source)
        self.assertIn("device_local_staged", source)
        self.assertIn("vkCmdCopyBuffer(command_buffer,", source)
        self.assertIn("device_local_staging_requested", source)
        self.assertIn("staging_upload_copies", source)
        self.assertIn("staging_download_copies", source)
        self.assertIn("PDOCKER_GPU_DISPATCH_TIMEOUT_MS", source)
        self.assertIn("vulkan-dispatch-timeout", source)
        self.assertIn("submit-generic-dispatch", source)
        self.assertIn("vkQueueSubmit failed", source)
        self.assertIn("(strict_passthrough && !strict_device_local_staging) ? 0", source)
        self.assertIn("!binding_read_needed[i] || !vk_buffers[i]", source)
        self.assertIn("only the union of descriptor", source)
        self.assertIn("vulkan_vector_staging_offset", source)
        self.assertIn("sample_fd_hash(", source)
        self.assertIn("sample_memory_hash(", source)
        self.assertIn("binding_upload_ms[i] = now_ms() - binding_start;", source)
        self.assertIn("binding_download_ms[i] = now_ms() - binding_start;", source)

    def test_llama_gpu_compare_parses_graphics_dispatch_response_events(self):
        compare = LLAMA_COMPARE.read_text()
        self.assertIn('"dispatch response:"', compare)
        self.assertIn('markers = ("generic dispatch response:", "q6 compact response:", "dispatch response:")', compare)
        self.assertIn('event.get("executor") == "pdocker-gpu-executor"', compare)

    def test_llama_gpu_compare_classifies_direct_gpu_socket_rewrite_before_stale_marker(self):
        compare = LLAMA_COMPARE.read_text()
        self.assertIn("direct_socket_rewrite_blocker", compare)
        self.assertIn("connect AF_UNIX rewrite failed", compare)
        self.assertIn("/run/pdocker-gpu/pdocker-gpu.sock", compare)
        self.assertIn('blocker_class = "direct_socket_rewrite_failed"', compare)
        self.assertIn("failed to rewrite the container GPU AF_UNIX socket path", compare)
        self.assertLess(
            compare.index('blocker_class = "direct_socket_rewrite_failed"'),
            compare.index('blocker_class = "runtime_freshness_mismatch"'),
        )

    def test_llama_gpu_compare_restarts_stale_daemon_and_checks_runtime_marker(self):
        compare = LLAMA_COMPARE.read_text()
        service = PDOCKERD_SERVICE.read_text()
        self.assertIn("RESTART_APP_DAEMON", compare)
        self.assertIn("restart_app_daemon_for_test", compare)
        self.assertIn("pkill -x pdocker-gpu-executor", compare)
        self.assertIn("avoids force-stopping", compare)
        self.assertNotIn("am force-stop", compare)
        self.assertNotIn("EXPECTED_GPU_EXECUTOR_MARKER=", compare)
        self.assertIn("PDOCKER_GPU_EXECUTOR_EXPECTED_MARKER", compare)
        self.assertNotIn("EXPECTED_VULKAN_ICD_MARKER=", compare)
        self.assertIn("PDOCKER_VULKAN_ICD_EXPECTED_MARKER", compare)
        self.assertIn("observed_icd_markers", compare)
        self.assertIn("runtime_freshness", compare)
        self.assertIn("runtime_freshness_mismatch", compare)
        self.assertIn(
            "(not expected_executor_marker or expected_executor_marker in observed_executor_markers) and",
            compare,
        )
        self.assertIn(
            "(not expected_icd_marker or expected_icd_marker in observed_icd_markers)",
            compare,
        )
        self.assertIn("executor_build_marker", compare)
        self.assertIn("runtime_marker", VULKAN_ICD.read_text())
        self.assertIn("while helper executors are gone", service)
        self.assertIn("killStaleSidecar", service)
        self.assertIn("pkill -f '$processName'", service)
        self.assertIn("@Synchronized\n    private fun startGpuExecutor", service)
        self.assertIn("startGpuExecutor(runtime)", service)
        self.assertIn("startMediaExecutor(runtime)", service)

    def test_llama_gpu_compare_tracks_critical_env_propagation(self):
        compare = LLAMA_COMPARE.read_text()
        verifier = load_llama_gpu_artifact_verifier()
        config_fields = dict(verifier.LLAMA_GPU_CONFIG_PROPAGATION_ENV_FIELDS)
        forward_envs = set(verifier.LLAMA_GPU_COMPARE_FORWARD_ENV_KEYS)
        for env_name, field_name in [
            ("PDOCKER_GPU_CPU_ORACLE", "cpu_oracle_requested"),
            ("PDOCKER_GPU_STRICT_PASSTHROUGH", "strict_passthrough"),
            ("PDOCKER_GPU_STRICT_DEVICE_LOCAL_STAGING", "strict_object_graph.device_local_staging_requested"),
            ("PDOCKER_GPU_Q6K_SAFE_KERNEL", "q6k_safe_kernel"),
            ("PDOCKER_GPU_Q6K_COMPAT_REWRITES", "q6_compat_rewrites"),
            ("PDOCKER_GPU_Q6K_READONLY_OVERLAP_SNAPSHOT", "readonly_overlap_snapshot_policy.q6_auto"),
            ("PDOCKER_GPU_Q6K_ORACLE_WRITEBACK", "cpu_oracle.oracle_writeback"),
            ("PDOCKER_GPU_Q4K_SAFE_KERNEL", "q4k_safe_kernel"),
            ("PDOCKER_GPU_Q4K_TARGETED_SPECIALIZATION", "q4k_targeted_specialization_materialized"),
            ("PDOCKER_GPU_Q4K_PIPELINE_RETRY_LADDER", "q4k_pipeline_retry_ladder"),
            ("PDOCKER_GPU_ADD_FLOAT16_CAPABILITY_FOR_STORAGE16", "float16_capability_added"),
            ("PDOCKER_GPU_MUTABLE_BUFFER_CACHE", "mutable_buffer_cache.enabled"),
            ("PDOCKER_GPU_MATERIALIZE_SPIRV_SPECIALIZATION_CONSTANTS", "materialize_specialization"),
            ("PDOCKER_GPU_LEGALIZE_WORKGROUP_SIZE_FROM_SPEC", "legalize_workgroup_size_from_spec"),
            ("PDOCKER_GPU_USE_SPIRV_DESCRIPTOR_ACCESS", "spirv_descriptor_access"),
        ]:
            self.assertEqual(field_name, config_fields[env_name])
            self.assertIn(env_name, forward_envs)
        self.assertIn("llama-gpu-env-manifest.json", compare)
        self.assertIn("config_propagation_env_fields", compare)
        self.assertIn("q6_required_env_overlay = manifest.get(\"q6_required_env_overlay\")", compare)
        self.assertIn("def apply_q6_required_env_overlay(env, mode, gpu_layers):", compare)
        self.assertIn("apply_q6_required_env_overlay(env, mode, gpu_layers)", compare)
        self.assertIn("llama GPU env manifest does not forward all Q6 required env keys", compare)
        self.assertIn("explicit caller", compare)
        source = GPU_EXECUTOR.read_text()
        self.assertIn("strict_vulkan_passthrough_requested", source)
        self.assertIn("strict_vulkan_reconciliation_requested", source)
        self.assertIn("PDOCKER_GPU_STRICT_PASSTHROUGH", source)
        self.assertIn("PDOCKER_GPU_STRICT_RECONCILIATION", source)
        self.assertIn('\\"strict_passthrough\\":%s', source)
        self.assertIn('\\"requested_feature_mask\\":\\"0x%016llx\\"', source)
        self.assertIn("requested_feature_mask=", source)
        self.assertIn("spirv_required_feature_mask", source)
        self.assertIn("spirv_requested_feature_missing_mask", source)
        self.assertIn("spirv-feature-not-requested", source)
        self.assertIn("#define PDOCKER_GPU_PIPELINE_CACHE_SLOTS 256", source)
        self.assertIn("last_used = g_vulkan_pipeline_cache_clock++", source)
        self.assertIn('\\"pipeline_key\\":{\\"spirv_hash\\":\\"0x%016llx\\"', source)
        self.assertIn("only the mapped constant IDs and their referenced bytes participate", source)
        self.assertIn('env_truthy("PDOCKER_GPU_DISABLE_PIPELINE_OPTIMIZATION", 0)', source)
        self.assertIn('\\"mutable_buffer_cache\\":{\\"enabled\\":%s,\\"max_bytes\\":%zu}', source)
        self.assertIn('\\"q4k_targeted_specialization_materialized\\":%s', source)
        self.assertIn('\\"float16_capability_added\\":%s', source)
        self.assertIn("add_spirv_capability(&shader_code, &shader_size, 9)", source)
        abi = APP_HEADER.read_text()
        icd_source = (ROOT / "docker-proot-setup/src/gpu/pdocker_vulkan_icd.c").read_text()
        self.assertIn(
            "X(PDOCKER_GPU_ADD_FLOAT16_CAPABILITY_FOR_STORAGE16, "
            "add_float16_capability_for_storage16, "
            "has_add_float16_capability_for_storage16, "
            "add_float16_capability_for_storage16, 0)",
            abi,
        )
        self.assertIn(
            "X(PDOCKER_GPU_LEGALIZE_WORKGROUP_SIZE_FROM_SPEC, "
            "legalize_workgroup_size_from_spec, "
            "has_legalize_workgroup_size_from_spec, "
            "legalize_workgroup_size_from_spec, 1)",
            abi,
        )
        self.assertIn("PDOCKER_GPU_VULKAN_BOOL_DISPATCH_OPTIONS(PDOCKER_VK_BOOL_BRIDGE_OPTION)", icd_source)
        self.assertIn("pipeline->requested_feature_mask |= PDOCKER_VK_FEATURE_SHADER_FLOAT16", icd_source)
        self.assertIn('\\"q4k_safe_kernel\\":%s', source)
        self.assertIn("kQ4kSafeSpv", source)
        self.assertIn("kQ6kSafeSpv", source)
        self.assertIn("PDOCKER_GPU_Q6K_SAFE_KERNEL is an explicit diagnostic override", source)
        self.assertNotIn(
            "const int q6k_safe_kernel_requested =\n        strict_passthrough ? 0 :",
            source,
        )
        self.assertIn("PDOCKER_GPU_Q4K_SAFE_KERNEL is an explicit diagnostic override", source)
        self.assertNotIn(
            "const int q4k_safe_kernel_requested =\n        strict_passthrough ? 0 :",
            source,
        )
        q4_body = re.search(
            r"static int is_q4k_matvec_hash\(uint64_t spirv_hash\) \{(?P<body>.*?)\n}\n\nstatic const char \*cpu_oracle_kernel_hint",
            source,
            re.S,
        ).group("body")
        q6_body = re.search(
            r"static int is_q6k_matvec_hash\(uint64_t spirv_hash\) \{(?P<body>.*?)\n}\n\nstatic int is_q6k_matvec_oracle_hash",
            source,
            re.S,
        ).group("body")
        q6_oracle_body = re.search(
            r"static int is_q6k_matvec_oracle_hash\(uint64_t spirv_hash\) \{(?P<body>.*?)\n}\n\nstatic int is_q4k_matvec_hash",
            source,
            re.S,
        ).group("body")
        self.assertIn("is_q6k_matvec_hash(spirv_hash)", q6_oracle_body)
        self.assertIn("0x9cfc45ae24ba71d8ull", q6_oracle_body)
        self.assertNotIn("0x9cfc45ae24ba71d8ull", q6_body)
        self.assertIn("Diagnostic-only Q6_K oracle enrollment", source)
        self.assertIn("that function gates functional rewrite", source)
        self.assertIn("is_q6k_matvec_oracle_hash(spirv_hash)", source)
        self.assertIn("is_q6k_matvec_oracle_hash(cpu_oracle_spirv_hash)", source)
        oracle_calls = [line.strip() for line in source.splitlines() if "is_q6k_matvec_oracle_hash(" in line]
        allowed_oracle_call_fragments = [
            "static int is_q6k_matvec_oracle_hash(uint64_t spirv_hash)",
            "if (is_q6k_matvec_oracle_hash(spirv_hash))",
            "} else if (is_q6k_matvec_oracle_hash(cpu_oracle_spirv_hash)) {",
            "if (profile_response && is_q6k_matvec_oracle_hash(cpu_oracle_spirv_hash)) {",
        ]
        unexpected_oracle_calls = [
            line for line in oracle_calls
            if not any(fragment in line for fragment in allowed_oracle_call_fragments)
        ]
        self.assertEqual([], unexpected_oracle_calls)
        self.assertEqual(2, source.count("0x9cfc45ae24ba71d8ull"))
        for q4_hash in [
            "0xf3cd7d18f0276b42ull",
            "0x853c49b4900eed3cull",
            "0x22ab0152b230e983ull",
        ]:
            self.assertIn(q4_hash, q4_body)
            self.assertNotIn(q4_hash, q6_body)
        self.assertIn('return "mul-mat-vec-q4-k-large";', source)
        self.assertIn("q4k_safe_kernel_used || is_q4k_matvec_hash(cpu_oracle_spirv_hash)", source)
        q4_safe_block = re.search(
            r"const int q4k_safe_kernel_requested =(?P<body>.*?)const int q6k_safe_kernel_requested =",
            source,
            re.S,
        ).group("body")
        self.assertNotIn("strict_passthrough", q4_safe_block)
        self.assertIn('env_truthy("PDOCKER_GPU_Q4K_SAFE_KERNEL", 0)', q4_safe_block)
        self.assertIn("q4k_pipeline_retry_enabled = q4k_callsite_detected &&", source)
        self.assertIn('env_truthy("PDOCKER_GPU_Q4K_PIPELINE_RETRY_LADDER", 0)', source)
        self.assertGreaterEqual(source.count('\\"q4k_pipeline_retry_ladder\\":%s'), 3)
        self.assertGreaterEqual(source.count('\\"q4k_pipeline_retry_attempted\\":%s'), 3)
        q4_retry_block = re.search(
            r"if \(rc != VK_SUCCESS &&(?P<body>.*?)materialize_spirv_specialization_constants",
            source,
            re.S,
        ).group("body")
        self.assertIn("(!strict_passthrough || q4k_pipeline_retry_enabled)", q4_retry_block)
        self.assertIn("q4k_pipeline_retry_enabled", q4_retry_block)
        self.assertIn("strict passthrough", q4_retry_block)
        self.assertIn("Q4_K", q4_retry_block)
        self.assertIn('"evidence_policy": "q4k_callsite_gated"', LLAMA_GPU_ENV_MANIFEST.read_text())
        self.assertIn('evidence_policy == "q4k_callsite_gated"', compare)
        self.assertNotIn("callsite_gated_config_envs", compare)
        self.assertNotIn("Q6_CALLSITE_GATED_CONFIG_ENVS", compare)
        self.assertIn('"PDOCKER_GPU_Q4K_PIPELINE_RETRY_LADDER"', LLAMA_GPU_ENV_MANIFEST.read_text())
        self.assertIn('observed_event_values(executor_events, "q4k_callsite_detected")', compare)
        self.assertIn("FORCED_VULKAN_WAIT_SERVER_TIMEOUT_SEC", compare)
        self.assertIn("PDOCKER_LLAMA_FORCED_VULKAN_WAIT_SERVER_TIMEOUT_SEC", compare)
        self.assertIn('wait_server "$FORCED_VULKAN_WAIT_SERVER_TIMEOUT_SEC" "Forced Vulkan"', compare)
        pdockerd = PDOCKERD.read_text()
        self.assertIn('gpu_runtime_env_defaults("vulkan")', pdockerd)
        self.assertIn('"env": "PDOCKER_GPU_DISABLE_PIPELINE_OPTIMIZATION"', LLAMA_GPU_ENV_MANIFEST.read_text())
        self.assertNotIn('"PDOCKER_GPU_DISABLE_PIPELINE_OPTIMIZATION": os.environ.get("PDOCKER_GPU_DISABLE_PIPELINE_OPTIMIZATION", "0")', pdockerd)

    def test_llama_diagnostic_vulkan_options_default_off(self):
        diagnostics = [
            "PDOCKER_GPU_Q6K_ORACLE_WRITEBACK",
            "PDOCKER_GPU_Q6K_SAFE_KERNEL",
            "PDOCKER_GPU_Q6K_COMPAT_REWRITES",
            "PDOCKER_GPU_Q6K_READONLY_OVERLAP_SNAPSHOT",
            "PDOCKER_GPU_Q4K_SAFE_KERNEL",
            "PDOCKER_GPU_Q4K_TARGETED_SPECIALIZATION",
            "PDOCKER_GPU_Q4K_PIPELINE_RETRY_LADDER",
        ]
        for header_path in [
            APP_HEADER,
            ROOT / "docker-proot-setup/src/gpu/pdocker_gpu_abi.h",
        ]:
            with self.subTest(header=str(header_path)):
                header = header_path.read_text()
                for env_name in diagnostics:
                    with self.subTest(env=env_name):
                        self.assertRegex(
                            header,
                            rf"X\({env_name}, [^\n]+, 0\)",
                        )
        source = GPU_EXECUTOR.read_text()
        self.assertIn(
            'env_truthy("PDOCKER_GPU_Q4K_PIPELINE_RETRY_LADDER", 0)',
            source,
        )
        self.assertNotIn(
            'env_truthy("PDOCKER_GPU_Q4K_PIPELINE_RETRY_LADDER", 1)',
            source,
        )

    def test_vulkan_product_cache_policy_does_not_assume_binding_numbers(self):
        source = GPU_EXECUTOR.read_text()
        candidate = re.search(
            r"static int resident_cache_candidate\((?P<body>.*?)\n}\n\nstatic int strict_readonly_resident_cache_candidate",
            source,
            re.S,
        ).group("body")
        self.assertIn("int read_only", candidate)
        self.assertIn("if (!read_only) return 0;", candidate)
        self.assertNotIn("binding != 0", candidate)
        self.assertNotIn("binding == 0", candidate)
        self.assertIn(
            "Only use the resident path after the caller has already proven",
            candidate,
        )
        self.assertIn("resident_cache_candidate(options, 1, b->size)", source)
        self.assertIn("resident_cache_candidate(options, 0, binding->size)", source)

    def test_vulkan_descriptor_arrays_preserve_array_elements_with_v5_transport(self):
        icd = VULKAN_ICD.read_text()
        self.assertIn("PDOCKER_VK_MAX_DESCRIPTOR_ARRAY_ELEMENTS", icd)
        self.assertIn("[PDOCKER_VK_MAX_STORAGE_BUFFERS][PDOCKER_VK_MAX_DESCRIPTOR_ARRAY_ELEMENTS]", icd)
        self.assertIn("uint32_t api_descriptor_array_elements[PDOCKER_VK_MAX_STORAGE_BUFFERS];", icd)
        self.assertIn("uint32_t image_descriptor_array_elements[PDOCKER_VK_MAX_STORAGE_BUFFERS];", icd)
        self.assertIn("descriptor_linear_slot", icd)
        self.assertIn("descriptors[i].array_element = api_descriptor_array_elements[i];", icd)
        self.assertIn("descriptors[descriptor_index].array_element = image_descriptor_array_elements[i];", icd)
        self.assertIn("descriptor_array_transport_required || image_descriptor_count > 0", icd)
        self.assertIn("api_descriptor_array_elements[binding_count] = array_element;", icd)
        self.assertIn("image_descriptor_array_elements[image_descriptor_count] = array_element;", icd)
        self.assertIn("set->storage_buffers[binding][array_element]", icd)
        self.assertIn("src->storage_buffers[src_binding][src_array]", icd)
        self.assertIn("dst->storage_buffers[dst_binding][dst_array]", icd)
        self.assertNotIn("descriptor array layout binding=%u count=%u type=%u is unsupported by V4 transport", icd)
        self.assertNotIn("descriptor array write binding=%u array=%u count=%u is unsupported by V4 transport", icd)
        self.assertNotIn("descriptor array copy src_binding=%u src_array=%u dst_binding=%u dst_array=%u count=%u is unsupported by V4 transport", icd)
        update_body = icd.split("VKAPI_ATTR void VKAPI_CALL vkUpdateDescriptorSets", 1)[1].split(
            "VKAPI_ATTR VkResult VKAPI_CALL vkCreateShaderModule", 1
        )[0]
        self.assertNotIn("w->descriptorCount > 1 || w->dstArrayElement != 0", update_body)
        self.assertNotIn("c->descriptorCount > 1 || c->srcArrayElement != 0 || c->dstArrayElement != 0", update_body)
        self.assertIn("descriptor_linear_slot(set->layout, w->dstBinding, w->dstArrayElement", update_body)
        self.assertIn("descriptor_linear_slot(src->layout, c->srcBinding, c->srcArrayElement", update_body)
        self.assertNotIn("exceeds layout count", update_body)
        bind_body = icd.split("VKAPI_ATTR void VKAPI_CALL vkCmdBindDescriptorSets", 1)[1].split(
            "VKAPI_ATTR void VKAPI_CALL vkCmdPushConstants", 1
        )[0]
        self.assertIn("storage_binding_counts[binding]", bind_body)
        self.assertIn("storage_buffers[binding][array_element]", bind_body)
        self.assertNotIn("PDOCKER_VULKAN_USE_V5_FRAME is disabled", bind_body)





    def test_vulkan_descriptor_copy_is_typed_and_fail_closed(self):
        icd = VULKAN_ICD.read_text()
        update_body = icd.split(
            "VKAPI_ATTR void VKAPI_CALL vkUpdateDescriptorSets", 1
        )[1].split("VKAPI_ATTR VkResult VKAPI_CALL vkCreateShaderModule", 1)[0]
        for marker in [
            "static bool descriptor_copy_slot_compatible",
            "descriptor_slot_object_matches_type",
            "src_type != dst_type",
            "slot->descriptor_type != type",
            "descriptor_type_requires_image_view(type) && !slot->image_view",
            "descriptor_type_requires_sampler(type) && !slot->sampler",
            "slot->dynamic == descriptor_type_is_dynamic(type)",
            "slot->buffer && !slot->image_view && !slot->sampler",
        ]:
            self.assertIn(marker, icd)
        self.assertIn("descriptor copy type/object mismatch", update_body)
        self.assertIn("dst->unsupported_descriptor_type = true;", update_body)
        self.assertIn("descriptor_copy_slot_compatible(src, src_binding, src_array", update_body)
        self.assertLess(
            update_body.index("descriptor_copy_slot_compatible(src, src_binding, src_array"),
            update_body.index("dst->storage_buffers[dst_binding][dst_array] ="),
        )

    def test_vulkan_descriptor_updates_stage_before_commit(self):
        icd = VULKAN_ICD.read_text()
        update_body = icd.split(
            "VKAPI_ATTR void VKAPI_CALL vkUpdateDescriptorSets", 1
        )[1].split("VKAPI_ATTR VkResult VKAPI_CALL vkCreateShaderModule", 1)[0]
        self.assertIn('unsupported_create_info_pnext_result("vkUpdateDescriptorSets.write", w->pNext)', update_body)
        self.assertIn('unsupported_create_info_pnext_result("vkUpdateDescriptorSets.copy", c->pNext)', update_body)
        self.assertLess(
            update_body.index('unsupported_create_info_pnext_result("vkUpdateDescriptorSets.write"'),
            update_body.index("descriptor_update_get_shadow("),
        )
        self.assertLess(
            update_body.index('unsupported_create_info_pnext_result("vkUpdateDescriptorSets.copy"'),
            update_body.index("descriptor_copy_slot_compatible(src, src_binding, src_array"),
        )
        for marker in [
            "PdockerVkDescriptorUpdateTarget",
            "descriptor_update_find_shadow",
            "descriptor_update_get_shadow",
            "target->shadow = *live;",
        ]:
            self.assertIn(marker, icd)
        self.assertIn("descriptor_update_get_shadow(", update_body)
        self.assertIn("src = descriptor_update_find_shadow(targets, target_count, src_live);", update_body)
        self.assertIn("*targets[i].live = targets[i].shadow;", update_body)
        self.assertIn("goto fail_closed;", update_body)
        self.assertIn("no descriptor slot changes committed", update_body)
        self.assertIn("targets[i].live->unsupported_descriptor_array |=", update_body)
        self.assertIn("targets[i].live->unsupported_descriptor_type |=", update_body)
        self.assertNotIn("if (!w->pImageInfo) continue;", update_body)
        self.assertNotIn("if (!w->pBufferInfo) continue;", update_body)
        self.assertLess(
            update_body.index("descriptor_update_get_shadow("),
            update_body.index("*targets[i].live = targets[i].shadow;"),
        )
        self.assertLess(
            update_body.index("descriptor_copy_slot_compatible(src, src_binding, src_array"),
            update_body.index("dst->storage_buffers[dst_binding][dst_array] ="),
        )
        self.assertLess(
            update_body.index("fail_closed:"),
            update_body.index("no descriptor slot changes committed"),
        )

    def test_graphics_uniform_buffer_descriptors_are_read_only(self):
        icd = VULKAN_ICD.read_text()
        collect_body = icd.split("static int collect_graphics_descriptor_entries", 1)[1].split(
            "static int append_recorded_graphics_descriptor_bind_snapshot", 1
        )[0]
        self.assertIn("binding->descriptor_type == VK_DESCRIPTOR_TYPE_STORAGE_BUFFER", collect_body)
        self.assertIn("binding->descriptor_type == VK_DESCRIPTOR_TYPE_STORAGE_BUFFER_DYNAMIC", collect_body)
        self.assertIn("? (PDOCKER_GPU_V5_ACCESS_READ | PDOCKER_GPU_V5_ACCESS_WRITE)", collect_body)
        self.assertIn(": PDOCKER_GPU_V5_ACCESS_READ", collect_body)
        self.assertNotIn(
            "descriptor->access_flags = PDOCKER_GPU_V5_ACCESS_READ | PDOCKER_GPU_V5_ACCESS_WRITE;",
            collect_body,
        )

        object_descriptor_body = collect_body.split(
            "if (descriptor_type_supported_by_v5_object_transport", 1
        )[1].split("continue;", 1)[0]
        self.assertIn("descriptor_type == VK_DESCRIPTOR_TYPE_STORAGE_IMAGE", object_descriptor_body)
        self.assertIn(": PDOCKER_GPU_V5_ACCESS_READ", object_descriptor_body)

        buffer_descriptor_body = collect_body.split(
            "if (!descriptor_type_supported_by_v4_transport(binding->descriptor_type))", 1
        )[1].split("descriptor->resource_index = (uint32_t)buffer_index;", 1)[0]
        self.assertIn("binding->descriptor_type == VK_DESCRIPTOR_TYPE_STORAGE_BUFFER", buffer_descriptor_body)
        self.assertIn("binding->descriptor_type == VK_DESCRIPTOR_TYPE_STORAGE_BUFFER_DYNAMIC", buffer_descriptor_body)
        self.assertIn(": PDOCKER_GPU_V5_ACCESS_READ", buffer_descriptor_body)

    def test_vulkan_v4_transport_supports_uniform_buffer_descriptor_type(self):
        icd = VULKAN_ICD.read_text()
        self.assertIn("type == VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER", icd)
        self.assertIn("type == VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER_DYNAMIC", icd)
        executor = GPU_EXECUTOR.read_text()
        self.assertIn("vulkan_dispatch_descriptor_type_from_api", executor)
        self.assertIn("VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER_DYNAMIC", executor)
        self.assertIn("set_binding_types", executor)
        self.assertIn("descriptor_pool_uniform_count", executor)
        self.assertIn("unsupported descriptor write type for V4 transport", executor)
        generic_body = executor.split("create-generic-descriptor-set-layout", 1)[0].rsplit(
            "VkDescriptorSetLayoutBinding layout_bindings", 1
        )[1]
        self.assertIn("layout_bindings[set_index][i].descriptorType = set_binding_types[set_index][i];", generic_body)
        pool_body = executor.split("descriptor_pool_uniform_count", 1)[1].split(
            "create-generic-descriptor-pool", 1
        )[0]
        self.assertIn("VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER", pool_body)
        write_body = executor.split("VkDescriptorBufferInfo infos[PDOCKER_GPU_MAX_VULKAN_BINDINGS]", 1)[1].split(
            "vkUpdateDescriptorSets", 1
        )[0]
        self.assertIn("vulkan_dispatch_descriptor_type_from_api(bindings[i].api_descriptor_type", write_body)
        self.assertNotIn("writes[write_count].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;", write_body)

    def test_vulkan_pipeline_layout_drives_descriptor_layout_and_dynamic_offsets(self):
        icd = VULKAN_ICD.read_text()
        executor = GPU_EXECUTOR.read_text()
        self.assertIn("uint32_t set_layout_count;", icd)
        self.assertIn("set_layouts[PDOCKER_VK_MAX_DESCRIPTOR_SETS]", icd)
        self.assertIn("bool unsupported_set_layout_count;", icd)
        self.assertIn("descriptor_set_layout_compatible", icd)
        self.assertIn("descriptor_type_is_dynamic", icd)
        self.assertIn("VK_DESCRIPTOR_TYPE_STORAGE_BUFFER_DYNAMIC", icd)
        self.assertIn("VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER_DYNAMIC", icd)
        create_body = icd.split("VKAPI_ATTR VkResult VKAPI_CALL vkCreatePipelineLayout", 1)[1].split(
            "VKAPI_ATTR void VKAPI_CALL vkDestroyPipelineLayout", 1
        )[0]
        self.assertIn("layout->set_layout_count = pCreateInfo->setLayoutCount;", create_body)
        self.assertIn("pCreateInfo->pSetLayouts", create_body)
        self.assertIn("layout->unsupported_set_layout_count = true;", create_body)
        self.assertIn("push_constant_ranges[PDOCKER_VK_MAX_PUSH_CONSTANT_RANGES]", icd)
        self.assertIn("push_constant_ops[PDOCKER_VK_MAX_PUSH_CONSTANT_OPS]", icd)
        self.assertIn("layout->push_constant_range_count < PDOCKER_VK_MAX_PUSH_CONSTANT_RANGES", create_body)
        self.assertIn("snapshot->stage_flags = range->stageFlags;", create_body)
        self.assertIn("layout->unsupported_push_constant_ranges = true;", create_body)
        bind_body = icd.split("VKAPI_ATTR void VKAPI_CALL vkCmdBindDescriptorSets", 1)[1].split(
            "static void validate_bound_descriptor_layouts_before_dispatch", 1
        )[0]
        self.assertNotIn("(void)layout;", bind_body)
        self.assertIn("PdockerVkPipelineLayout *pipeline_layout", bind_body)
        self.assertIn("descriptor_set_layout_compatible(pipeline_layout->set_layouts[target_set]", bind_body)
        self.assertIn("layout expects dynamic descriptor", bind_body)
        self.assertIn("missing dynamic offset", bind_body)
        self.assertIn("extra dynamic offsets", bind_body)
        self.assertIn("dynamic descriptor offset overflow", bind_body)
        self.assertIn("cmd->unsupported_descriptor_set_layout = true;", bind_body)
        self.assertIn("slot->dynamic = descriptor_type_is_dynamic", icd)
        self.assertIn("storage_buffers[binding][array_element]", bind_body)
        self.assertIn("validate_bound_descriptor_layouts_before_dispatch", icd)
        self.assertIn("dispatch descriptor layout mismatch", icd)
        dispatch_body = icd.split("VKAPI_ATTR void VKAPI_CALL vkCmdDispatch", 1)[1].split(
            "VKAPI_ATTR void VKAPI_CALL vkCmdPushConstants", 1
        )[0]
        self.assertIn("validate_bound_descriptor_layouts_before_dispatch(cmd);", dispatch_body)
        self.assertIn("op->push_constant_op_count = cmd->push_constant_op_count;", dispatch_body)
        self.assertIn("VKAPI_ATTR void VKAPI_CALL vkCmdDispatchBase", dispatch_body)
        self.assertIn("op->base_group_x = baseGroupX;", dispatch_body)
        self.assertIn('MAP_ALIAS("vkCmdDispatchBaseKHR", vkCmdDispatchBaseKHR);', icd)
        proc_gate_body = icd.split("static bool proc_address_hidden_by_advertisement", 1)[1].split(
            "static PFN_vkVoidFunction proc_address", 1
        )[0]
        self.assertIn('strcmp(pName, "vkCmdDispatchBaseKHR") == 0', proc_gate_body)
        self.assertIn("base_group_x=%u base_group_y=%u base_group_z=%u", icd)
        dispatch_send_body = icd.split("static int send_generic_vulkan_dispatch_op", 1)[1].split("static int send_generic_vulkan_dispatch(", 1)[0]
        self.assertLess(dispatch_send_body.index("api_buffer_ids[i]"), dispatch_send_body.index("base_group_x=%u"))
        self.assertIn("VKAPI_ATTR void VKAPI_CALL vkCmdDispatchIndirect", dispatch_body)
        self.assertIn("op->dispatch_indirect = true;", dispatch_body)
        self.assertIn("MAP_PROC(vkCmdDispatchIndirect);", icd)
        self.assertIn("resolve_vulkan_dispatch_group_counts", icd)
        resolve_body = icd.split("static int resolve_vulkan_dispatch_group_counts", 1)[1].split(
            "static int send_generic_vulkan_dispatch_op", 1
        )[0]
        self.assertIn("op->dispatch_indirect", resolve_body)
        self.assertIn("return -EOPNOTSUPP;", resolve_body)
        self.assertNotIn("memcpy(counts", resolve_body)
        self.assertIn("op->dispatch_indirect ? op->dispatch_indirect_buffer : NULL", icd)
        self.assertIn("dispatch_indirect_resource=%u dispatch_indirect_offset=%llu", icd)
        self.assertIn("const bool requires_v5_frame = descriptor_array_transport_required || image_descriptor_count > 0 || op->dispatch_indirect;", icd)
        self.assertIn("dispatch_indirect_resource=", executor)
        self.assertIn("materialize_vulkan_dispatch_indirect_buffer", executor)
        self.assertIn("vkCmdDispatchIndirect(command_buffer", executor)
        self.assertIn("dispatch indirect cannot be combined with base group", executor)
        self.assertIn("base_group_x", executor)
        self.assertNotIn("header.gx, header.gy, header.gz, 0, 0, 0", executor)
        self.assertIn("options.has_base_group ? options.base_group_x : 0", executor)
        self.assertIn("options.has_base_group ? options.base_group_y : 0", executor)
        self.assertIn("options.has_base_group ? options.base_group_z : 0", executor)
        self.assertIn("cmd_dispatch_base", executor)
        self.assertIn("vkCmdDispatchBase is unavailable", executor)
        push_body = icd.split("VKAPI_ATTR void VKAPI_CALL vkCmdPushConstants", 1)[1].split(
            "static bool image_subresource_range_is_whole_image", 1
        )[0]
        self.assertNotIn("(void)stageFlags;", push_body)
        self.assertIn("op->stage_flags = stageFlags;", push_body)
        self.assertIn("op->offset = offset;", push_body)
        self.assertIn("op->value_hash = fnv1a64_bytes(pValues, size);", push_body)
        self.assertIn("cmd->graphics_unsupported = true;", push_body)



    def test_vulkan_queue_family_advertises_graphics_for_graphics_passthrough(self):
        icd = VULKAN_ICD.read_text()
        self.assertIn("pdocker_vk_advertised_queue_flags", icd)
        self.assertIn("VK_QUEUE_GRAPHICS_BIT | VK_QUEUE_COMPUTE_BIT | VK_QUEUE_TRANSFER_BIT", icd)
        qf_body = icd.split("VKAPI_ATTR void VKAPI_CALL vkGetPhysicalDeviceQueueFamilyProperties", 1)[1].split(
            "VKAPI_ATTR void VKAPI_CALL vkGetPhysicalDeviceQueueFamilyProperties2", 1
        )[0]
        qf2_body = icd.split("VKAPI_ATTR void VKAPI_CALL vkGetPhysicalDeviceQueueFamilyProperties2", 1)[1].split(
            "VKAPI_ATTR void VKAPI_CALL vkGetPhysicalDeviceMemoryProperties", 1
        )[0]
        self.assertIn("queueFlags = pdocker_vk_advertised_queue_flags();", qf_body)
        self.assertIn("queueFlags = pdocker_vk_advertised_queue_flags();", qf2_body)
        self.assertIn("zero_vk_out_struct_preserve_chain(&pQueueFamilyProperties[0], sizeof(pQueueFamilyProperties[0]), header);", qf2_body)
        self.assertIn("fill_queue_family_properties2_pnext((void *)header.pNext);", qf2_body)
        self.assertNotIn("memset(&pQueueFamilyProperties[0].queueFamilyProperties", qf2_body)
        qf2_pnext_body = icd.split("static void fill_queue_family_properties2_pnext", 1)[1].split(
            "VKAPI_ATTR void VKAPI_CALL vkGetPhysicalDeviceQueueFamilyProperties2", 1
        )[0]
        for struct_name in [
            "VkQueueFamilyGlobalPriorityProperties",
            "VkQueueFamilyVideoPropertiesKHR",
            "VkQueueFamilyQueryResultStatusPropertiesKHR",
            "VkQueueFamilyCheckpointPropertiesNV",
            "VkQueueFamilyCheckpointProperties2NV",
        ]:
            if struct_name not in qf2_pnext_body:
                continue
            segment = qf2_pnext_body.split(f"{struct_name} *p", 1)[1].split("break;", 1)[0]
            self.assertIn("zero_vk_out_struct_preserve_chain(p, sizeof(*p), header);", segment)
        self.assertIn("queueCount = PDOCKER_VK_ADVERTISED_QUEUE_COUNT", qf_body)
        self.assertIn("queueCount = PDOCKER_VK_ADVERTISED_QUEUE_COUNT", qf2_body)
        self.assertIn("#define PDOCKER_VK_ADVERTISED_QUEUE_COUNT 1u", icd)
        self.assertIn("pdocker_vk_queue_request_valid", icd)
        self.assertIn("validate_device_queue_create_infos", icd)
        queue_create_body = c_function_body(icd, "validate_device_queue_create_infos")
        queue_pnext_body = c_function_body(icd, "validate_device_queue_create_info_pnext")
        self.assertIn("validate_device_queue_create_info_pnext(qci->pNext)", queue_create_body)
        self.assertIn("VK_STRUCTURE_TYPE_DEVICE_QUEUE_GLOBAL_PRIORITY_CREATE_INFO", queue_pnext_body)
        self.assertIn("priority->globalPriority != VK_QUEUE_GLOBAL_PRIORITY_MEDIUM", queue_pnext_body)
        self.assertIn('unsupported_create_info_pnext_result("vkCreateDevice.queue", node)', queue_pnext_body)
        queue_body = icd.split("VKAPI_ATTR void VKAPI_CALL vkGetDeviceQueue", 1)[1].split(
            "static bool descriptor_type_supported", 1
        )[0]
        self.assertIn("VK_NULL_HANDLE", queue_body)
        self.assertNotIn("queueFlags = VK_QUEUE_COMPUTE_BIT | VK_QUEUE_TRANSFER_BIT", qf_body)
        self.assertNotIn("queueFlags = VK_QUEUE_COMPUTE_BIT | VK_QUEUE_TRANSFER_BIT", qf2_body)
        self.assertNotIn("queueCount = 2", qf_body)
        self.assertNotIn("queueCount = 2", qf2_body)


    def test_vulkan_properties_pnext_structs_are_fully_initialized(self):
        icd = VULKAN_ICD.read_text()
        self.assertIn("zero_vk_out_struct_preserve_chain", icd)
        helper_body = icd.split("static void zero_vk_out_struct_preserve_chain", 1)[1].split(
            "static void trace_pnext_chain", 1
        )[0]
        self.assertIn("memset(node, 0, size);", helper_body)
        self.assertIn("out->sType = header.sType;", helper_body)
        self.assertIn("out->pNext = header.pNext;", helper_body)
        props_body = icd.split("static void fill_pnext_properties", 1)[1].split(
            "static void fill_physical_device_features", 1
        )[0]
        for struct_name in [
            "VkPhysicalDeviceIDProperties",
            "VkPhysicalDevicePointClippingProperties",
            "VkPhysicalDeviceMultiviewProperties",
            "VkPhysicalDeviceProtectedMemoryProperties",
            "VkPhysicalDeviceMaintenance3Properties",
            "VkPhysicalDeviceSubgroupProperties",
            "VkPhysicalDeviceDriverProperties",
            "VkPhysicalDeviceVulkan11Properties",
            "VkPhysicalDeviceVulkan12Properties",
            "VkPhysicalDeviceDescriptorIndexingProperties",
            "VkPhysicalDeviceDepthStencilResolveProperties",
            "VkPhysicalDeviceSamplerFilterMinmaxProperties",
            "VkPhysicalDeviceFloatControlsProperties",
            "VkPhysicalDeviceTimelineSemaphoreProperties",
        ]:
            self.assertIn(f"{struct_name} *p", props_body)
            segment = props_body.split(f"{struct_name} *p", 1)[1].split("break;", 1)[0]
            self.assertIn("zero_vk_out_struct_preserve_chain(p, sizeof(*p), header);", segment)
        if "VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MAINTENANCE_4_PROPERTIES" in props_body:
            segment = props_body.split("VkPhysicalDeviceMaintenance4Properties *p", 1)[1].split("break;", 1)[0]
            self.assertIn("zero_vk_out_struct_preserve_chain(p, sizeof(*p), header);", segment)
        for marker in [
            "memcpy(p->deviceUUID, device_uuid, sizeof(p->deviceUUID));",
            "p->pointClippingBehavior = VK_POINT_CLIPPING_BEHAVIOR_ALL_CLIP_PLANES;",
            "p->maxMultiviewViewCount = 1;",
            "p->protectedNoFault = VK_FALSE;",
            "p->maxUpdateAfterBindDescriptorsInAllPools = 0;",
            "p->supportedDepthResolveModes = VK_RESOLVE_MODE_NONE;",
            "p->filterMinmaxSingleComponentFormats = VK_FALSE;",
            "p->denormBehaviorIndependence = VK_SHADER_FLOAT_CONTROLS_INDEPENDENCE_NONE;",
            "p->maxTimelineSemaphoreValueDifference = UINT64_MAX;",
        ]:
            self.assertIn(marker, props_body)


    def test_vulkan_physical_device_properties2_and_features2_outputs_are_fully_initialized(self):
        icd = VULKAN_ICD.read_text()
        props2_body = icd.split("VKAPI_ATTR void VKAPI_CALL vkGetPhysicalDeviceProperties2", 1)[1].split(
            "VKAPI_ATTR void VKAPI_CALL vkGetPhysicalDeviceFeatures", 1
        )[0]
        self.assertIn("PdockerVkStructHeader header = read_vk_struct_header(pProperties);", props2_body)
        self.assertIn("zero_vk_out_struct_preserve_chain(pProperties, sizeof(*pProperties), header);", props2_body)
        self.assertIn("vkGetPhysicalDeviceProperties(physicalDevice, &pProperties->properties);", props2_body)
        self.assertIn("fill_pnext_properties(pProperties->pNext);", props2_body)
        features2_body = icd.split("VKAPI_ATTR void VKAPI_CALL vkGetPhysicalDeviceFeatures2", 1)[1].split(
            "VKAPI_ATTR void VKAPI_CALL vkGetPhysicalDeviceFormatProperties", 1
        )[0]
        self.assertIn("PdockerVkStructHeader header = read_vk_struct_header(pFeatures);", features2_body)
        self.assertIn("zero_vk_out_struct_preserve_chain(pFeatures, sizeof(*pFeatures), header);", features2_body)
        self.assertIn("fill_physical_device_features(&pFeatures->features);", features2_body)
        self.assertIn("fill_pnext_features(pFeatures->pNext);", features2_body)


    def test_vulkan_feature_pnext_structs_are_fully_initialized(self):
        icd = VULKAN_ICD.read_text()
        body = icd.split("static void fill_pnext_features", 1)[1].split(
            "static uint64_t feature_mask_from_base_features", 1
        )[0]
        for struct_name in [
            "VkPhysicalDeviceVulkan11Features",
            "VkPhysicalDevice16BitStorageFeatures",
            "VkPhysicalDeviceVulkan12Features",
            "VkPhysicalDevice8BitStorageFeatures",
            "VkPhysicalDeviceShaderFloat16Int8Features",
            "VkPhysicalDeviceSynchronization2Features",
            "VkPhysicalDeviceTimelineSemaphoreFeatures",
            "VkPhysicalDeviceDynamicRenderingFeatures",
            "VkPhysicalDeviceExtendedDynamicStateFeaturesEXT",
            "VkPhysicalDeviceIndexTypeUint8FeaturesEXT",
            "VkPhysicalDeviceDescriptorIndexingFeatures",
            "VkPhysicalDeviceScalarBlockLayoutFeatures",
            "VkPhysicalDeviceBufferDeviceAddressFeatures",
            "VkPhysicalDeviceSamplerYcbcrConversionFeatures",
            "VkPhysicalDeviceVulkanMemoryModelFeatures",
            "VkPhysicalDeviceUniformBufferStandardLayoutFeatures",
            "VkPhysicalDeviceShaderSubgroupExtendedTypesFeatures",
            "VkPhysicalDeviceSeparateDepthStencilLayoutsFeatures",
            "VkPhysicalDeviceHostQueryResetFeatures",
            "VkPhysicalDeviceMaintenance4Features",
        ]:
            if struct_name not in body:
                continue
            segment = body.split(f"{struct_name} *p", 1)[1].split("break;", 1)[0]
            self.assertIn("zero_vk_out_struct_preserve_chain(p, sizeof(*p), header);", segment)


    def test_vulkan_command_recording_overflow_fails_closed(self):
        icd = VULKAN_ICD.read_text()
        for marker in [
            "bool recording_failed;",
            "const char *recording_failure_reason;",
            "command_buffer_mark_recording_failed",
            "command-op-record-overflow",
            "graphics-command-record-overflow",
            "cmd->recording_failed = false;",
            "cmd->recording_failure_reason = NULL;",
        ]:
            self.assertIn(marker, icd)
        end_body = icd.split("VKAPI_ATTR VkResult VKAPI_CALL vkEndCommandBuffer", 1)[1].split(
            "VKAPI_ATTR VkResult VKAPI_CALL vkResetCommandBuffer", 1
        )[0]
        self.assertIn("cmd->recording_failed", end_body)
        self.assertIn("trace_icd_runtime_failure", end_body)
        submit_body = icd.split("VKAPI_ATTR VkResult VKAPI_CALL vkQueueSubmit", 1)[1].split(
            "VKAPI_ATTR VkResult VKAPI_CALL vkQueueSubmit2", 1
        )[0]
        self.assertIn("cmd->recording_failed", submit_body)
        self.assertIn("command-recording-failed", submit_body)


    def test_vulkan_binary_semaphores_are_not_noop_in_v4_submit(self):
        icd = VULKAN_ICD.read_text()
        self.assertIn("typedef struct PdockerVkSemaphore", icd)
        self.assertIn("validate_submit_wait_semaphores", icd)
        self.assertIn("complete_submit_semaphores", icd)
        self.assertIn("semaphore-wait-unsignaled", icd)
        self.assertIn("allow_executor_tracked_queue_waits && sem && sem->executor_tracked", icd)
        self.assertIn("sem->signaled = false;", icd)
        self.assertIn("sem->signaled = true;", icd)
        self.assertIn("semaphore_complete_wait(sem);", icd)
        self.assertIn("semaphore_complete_signal(sem", icd)
        self.assertIn("semaphore-pnext-unsupported", icd)
        submit_body = icd.split("VKAPI_ATTR VkResult VKAPI_CALL vkQueueSubmit", 1)[1].split(
            "VKAPI_ATTR VkResult VKAPI_CALL vkQueueWaitIdle", 1
        )[0]
        self.assertNotIn("(void)fence;", submit_body)
        self.assertIn("submit_timeline_info_from_pnext(&pSubmits[i], &timeline_submit)", submit_body)
        self.assertIn("validate_legacy_submit_info_shape(&pSubmits[i])", submit_body)
        self.assertIn("submit_device_indices_are_single_device", icd)
        self.assertIn("submit_command_buffer_device_masks_are_single_device", icd)
        self.assertIn("VK_STRUCTURE_TYPE_DEVICE_GROUP_SUBMIT_INFO", icd)
        self.assertIn("submit-device-group-unsupported", icd)
        self.assertIn("VK_STRUCTURE_TYPE_PROTECTED_SUBMIT_INFO", icd)
        self.assertIn("submit-protected-unsupported", icd)
        self.assertIn('unsupported_create_info_pnext_result("vkQueueSubmit", node)', icd)
        self.assertIn("submit_has_executor_tracked_wait_sync(&pSubmits[i])", submit_body)
        self.assertIn("submit_has_executor_tracked_completion_sync(&pSubmits[i], fence)", submit_body)
        self.assertIn("allow_executor_tracked_queue_waits", submit_body)
        self.assertIn("validate_submit_wait_semaphores(\n            &pSubmits[i], timeline_submit, allow_executor_tracked_queue_waits)", submit_body)
        self.assertIn("complete_submit_semaphores(&pSubmits[i], timeline_submit);", submit_body)
        self.assertIn("if (submitCount > 0 && !pSubmits) return VK_ERROR_INITIALIZATION_FAILED;", submit_body)
        self.assertIn("submit_fence->signaled = false;", submit_body)
        self.assertIn("submit_fence->signaled = true;", submit_body)
        self.assertLess(
            submit_body.index("for (uint32_t validate_i = 0; validate_i < submitCount; ++validate_i)"),
            submit_body.index("submit_fence->signaled = false;"),
        )
        self.assertIn("send_executor_fence_signal(submit_fence)", submit_body)
        self.assertLess(
            submit_body.index("if (submitCount > 0 && !pSubmits) return VK_ERROR_INITIALIZATION_FAILED;"),
            submit_body.index("submit_fence->signaled = false;"),
        )
        create_body = icd.split("VKAPI_ATTR VkResult VKAPI_CALL vkCreateSemaphore", 1)[1].split(
            "VKAPI_ATTR void VKAPI_CALL vkDestroySemaphore", 1
        )[0]
        self.assertNotIn("sizeof(PdockerHandle)", create_body)
        self.assertIn("PdockerVkSemaphore *sem", create_body)

    def test_vulkan_timeline_semaphores_keep_counter_values(self):
        icd = VULKAN_ICD.read_text()
        for marker in [
            "PDOCKER_VK_FEATURE_TIMELINE_SEMAPHORE",
            "bool timeline;",
            "uint64_t value;",
            "VK_KHR_TIMELINE_SEMAPHORE_EXTENSION_NAME",
            "p->timelineSemaphore = advertised_timeline_semaphore();",
            "VkSemaphoreTypeCreateInfo",
            "VK_STRUCTURE_TYPE_SEMAPHORE_TYPE_CREATE_INFO",
            "VK_SEMAPHORE_TYPE_TIMELINE",
            "semaphore_create_info_parse_pnext",
            "vkGetSemaphoreCounterValue",
            "vkWaitSemaphores",
            "vkSignalSemaphore",
            'MAP_ALIAS("vkGetSemaphoreCounterValueKHR", vkGetSemaphoreCounterValue)',
            'MAP_ALIAS("vkWaitSemaphoresKHR", vkWaitSemaphores)',
            'MAP_ALIAS("vkSignalSemaphoreKHR", vkSignalSemaphore)',
        ]:
            self.assertIn(marker, icd)
        submit_body = icd.split("VKAPI_ATTR VkResult VKAPI_CALL vkQueueSubmit", 1)[1].split(
            "VKAPI_ATTR VkResult VKAPI_CALL vkQueueSubmit2", 1
        )[0]
        for marker in [
            "VkTimelineSemaphoreSubmitInfo",
            "submit_timeline_info_from_pnext",
            "VK_STRUCTURE_TYPE_TIMELINE_SEMAPHORE_SUBMIT_INFO",
            "validate_submit_timeline_info",
            "submit_timeline_wait_value",
            "submit_timeline_signal_value",
            "submit_timeline_wait_value(timeline, i)",
            "submit_timeline_signal_value(timeline, i)",
            "submit-timeline-pnext-duplicate",
            "submit-device-group-unsupported",
            "submit-protected-unsupported",
        ]:
            self.assertIn(marker, icd)
        self.assertIn("submit_timeline_info_from_pnext(&pSubmits[i], &timeline_submit)", submit_body)
        self.assertIn("validate_submit_wait_semaphores(\n            &pSubmits[i], timeline_submit, allow_executor_tracked_queue_waits)", submit_body)
        self.assertIn("complete_submit_semaphores(&pSubmits[i], timeline_submit)", submit_body)
        queue_submit2_body = icd.split("VKAPI_ATTR VkResult VKAPI_CALL vkQueueSubmit2", 1)[1].split(
            "VKAPI_ATTR VkResult VKAPI_CALL vkQueueWaitIdle", 1
        )[0]
        self.assertIn("for (uint32_t validate_i = 0; validate_i < submitCount; ++validate_i)", queue_submit2_body)
        self.assertIn("VkResult validate_rc = validate_submit2_wait_semaphores(src, bridge_available());", queue_submit2_body)
        self.assertIn("validate_rc = validate_submit2_command_buffers(src);", queue_submit2_body)
        self.assertIn("validate_rc = validate_submit2_signal_semaphores(src);", queue_submit2_body)
        self.assertIn("collect_submit2_submit_sync_entries(src, validate_fence", queue_submit2_body)
        self.assertIn("validate_submit2_wait_semaphores(src, bridge_available())", queue_submit2_body)
        self.assertIn("validate_submit2_command_buffers(src)", queue_submit2_body)
        self.assertIn("validate_submit2_signal_semaphores(src)", queue_submit2_body)
        self.assertIn("complete_submit2_semaphores(src)", queue_submit2_body)
        self.assertLess(
            queue_submit2_body.index("for (uint32_t validate_i = 0; validate_i < submitCount; ++validate_i)"),
            queue_submit2_body.index("submit_fence->signaled = false;"),
        )
        for marker in [
            "submit2-pnext-unsupported",
            "submit2-wait-pnext-unsupported",
            "submit2-command-pnext-unsupported",
            "submit2-signal-pnext-unsupported",
            "info->pNext",
        ]:
            self.assertIn(marker, icd)
        for marker in [
            "submit2-wait-device-index-unsupported",
            "submit2-signal-device-index-unsupported",
            "submit2-command-device-mask-unsupported",
            "info->deviceIndex != 0",
            "info->deviceMask != 0 && info->deviceMask != 1u",
        ]:
            self.assertIn(marker, icd)
        self.assertIn("uint64_t required_value = sem && sem->timeline ? info->value : 0;", icd)
        self.assertIn("src->pSignalSemaphoreInfos", queue_submit2_body)
        self.assertIn("collect_submit2_submit_sync_entries(src, submit2_fence", queue_submit2_body)
        self.assertIn("set_submit_sync_override(submit2_sync_entries, submit2_sync_count);", queue_submit2_body)
        self.assertIn("clear_submit_sync_override();", queue_submit2_body)
        self.assertLess(queue_submit2_body.index("set_submit_sync_override(submit2_sync_entries, submit2_sync_count);"), queue_submit2_body.index("vkQueueSubmit(queue, 1, &legacy_submit, VK_NULL_HANDLE)"))
        self.assertLess(queue_submit2_body.index("vkQueueSubmit(queue, 1, &legacy_submit, VK_NULL_HANDLE)"), queue_submit2_body.index("clear_submit_sync_override();"))
        self.assertNotIn("dst->waitSemaphoreCount = src->waitSemaphoreInfoCount", queue_submit2_body)
        self.assertNotIn("dst->signalSemaphoreCount = src->signalSemaphoreInfoCount", queue_submit2_body)
        self.assertIn("sem->timeline) return sem->value >= value;", icd)
        self.assertIn("if (!sem || sem->timeline) return;", icd)
        self.assertIn("if (sem->value < value) sem->value = value;", icd)

    def test_vulkan_legacy_submit_pnext_and_shape_are_fail_closed(self):
        icd = VULKAN_ICD.read_text()
        helper_body = c_function_body(icd, "submit_timeline_info_from_pnext")
        shape_body = c_function_body(icd, "validate_legacy_submit_info_shape")
        submit2_command_body = c_function_body(icd, "validate_submit2_command_buffers")

        self.assertIn("static VkResult submit_timeline_info_from_pnext", icd)
        self.assertIn("const VkSubmitInfo *submit", icd)
        self.assertIn("const VkTimelineSemaphoreSubmitInfo **out_timeline", icd)
        self.assertIn("VK_STRUCTURE_TYPE_TIMELINE_SEMAPHORE_SUBMIT_INFO", helper_body)
        self.assertIn("submit-timeline-pnext-duplicate", helper_body)
        self.assertIn("VK_STRUCTURE_TYPE_DEVICE_GROUP_SUBMIT_INFO", helper_body)
        self.assertIn("info->waitSemaphoreCount != submit->waitSemaphoreCount", helper_body)
        self.assertIn("info->commandBufferCount != submit->commandBufferCount", helper_body)
        self.assertIn("info->signalSemaphoreCount != submit->signalSemaphoreCount", helper_body)
        self.assertIn("submit_device_indices_are_single_device", helper_body)
        self.assertIn("submit_command_buffer_device_masks_are_single_device", helper_body)
        self.assertIn("submit-device-group-unsupported", helper_body)
        self.assertIn("VK_STRUCTURE_TYPE_PROTECTED_SUBMIT_INFO", helper_body)
        self.assertIn("info->protectedSubmit != VK_FALSE", helper_body)
        self.assertIn("submit-protected-unsupported", helper_body)
        self.assertIn('unsupported_create_info_pnext_result("vkQueueSubmit", node)', helper_body)

        self.assertIn("submit->sType != VK_STRUCTURE_TYPE_SUBMIT_INFO", shape_body)
        self.assertIn("!submit->pWaitSemaphores || !submit->pWaitDstStageMask", shape_body)
        self.assertIn("submit->commandBufferCount > 0 && !submit->pCommandBuffers", shape_body)
        self.assertIn("submit->signalSemaphoreCount > 0 && !submit->pSignalSemaphores", shape_body)

        submit_body = icd.split("VKAPI_ATTR VkResult VKAPI_CALL vkQueueSubmit", 1)[1].split(
            "VKAPI_ATTR VkResult VKAPI_CALL vkQueueSubmit2", 1
        )[0]
        self.assertIn("for (uint32_t validate_i = 0; validate_i < submitCount; ++validate_i)", submit_body)
        self.assertIn("validate_legacy_submit_info_shape(&pSubmits[validate_i])", submit_body)
        self.assertIn("submit_timeline_info_from_pnext(\n            &pSubmits[validate_i], &validate_timeline)", submit_body)
        self.assertLess(
            submit_body.index("for (uint32_t validate_i = 0; validate_i < submitCount; ++validate_i)"),
            submit_body.index("submit_fence->signaled = false;"),
        )

        self.assertIn("info->deviceMask != 0 && info->deviceMask != 1u", submit2_command_body)
        self.assertIn("submit2-command-device-mask-unsupported", submit2_command_body)

    def test_vulkan_wait_apis_honor_timeout_contract(self):
        icd = VULKAN_ICD.read_text()
        for marker in [
            "pdocker_vk_wait_deadline_expired",
            "pdocker_vk_wait_poll_sleep",
            "fences_wait_satisfied",
            "timeline_semaphore_wait_satisfied",
            "nanosleep(&ts, &ts)",
            "return VK_TIMEOUT;",
            "semaphore-wait-pnext-unsupported",
            "semaphore-signal-pnext-unsupported",
            "semaphore-flags-unsupported",
            "pCreateInfo && pCreateInfo->flags != 0",
            "pSignalInfo->pNext",
        ]:
            self.assertIn(marker, icd)
        fence_wait_body = icd.split("VKAPI_ATTR VkResult VKAPI_CALL vkWaitForFences", 1)[1].split(
            "static bool semaphore_create_info_parse_pnext", 1
        )[0]
        self.assertIn("if (fences_wait_satisfied(fenceCount, pFences, waitAll)) return VK_SUCCESS;", fence_wait_body)
        self.assertIn("pdocker_vk_wait_deadline_expired(start_ns, timeout)", fence_wait_body)
        self.assertIn("pdocker_vk_wait_poll_sleep(start_ns, timeout);", fence_wait_body)
        self.assertNotIn("return VK_NOT_READY", fence_wait_body)
        semaphore_wait_body = icd.split("VKAPI_ATTR VkResult VKAPI_CALL vkWaitSemaphores", 1)[1].split(
            "VKAPI_ATTR VkResult VKAPI_CALL vkSignalSemaphore", 1
        )[0]
        self.assertIn("if (timeline_semaphore_wait_satisfied(pWaitInfo)) return VK_SUCCESS;", semaphore_wait_body)
        self.assertIn("pdocker_vk_wait_deadline_expired(start_ns, timeout)", semaphore_wait_body)
        self.assertIn("pdocker_vk_wait_poll_sleep(start_ns, timeout);", semaphore_wait_body)
        self.assertIn("return VK_ERROR_FEATURE_NOT_PRESENT;", semaphore_wait_body)
        self.assertNotIn("VK_NOT_READY", semaphore_wait_body)


    def test_vulkan_create_device_validates_advertised_extensions_and_features(self):
        icd = VULKAN_ICD.read_text()
        self.assertIn("device_extension_advertised_name", icd)
        self.assertIn("validate_device_extensions", icd)
        self.assertIn("create-device rejected unadvertised extension", icd)
        self.assertIn("VK_ERROR_EXTENSION_NOT_PRESENT", icd)
        self.assertIn("advertised_feature_mask", icd)
        self.assertIn("requested_features_supported", icd)
        self.assertIn("validate_device_feature_requests", icd)
        self.assertIn("base_feature_request_supported", icd)
        self.assertIn("vulkan12_feature_request_supported", icd)
        self.assertIn("descriptor_indexing_feature_request_supported", icd)
        self.assertIn("create-device rejected unsupported feature_mask", icd)
        self.assertIn("create-device rejected unsupported feature", icd)
        self.assertIn("create-device-pnext-unsupported", icd)
        self.assertIn("VK_STRUCTURE_TYPE_LOADER_DEVICE_CREATE_INFO", icd)
        self.assertIn("Vulkan loader prepends ICD-private loader metadata", icd)
        self.assertIn("VK_STRUCTURE_TYPE_DEVICE_GROUP_DEVICE_CREATE_INFO", icd)
        self.assertIn("validate_device_group_device_create_info", icd)
        self.assertIn("create-device-device-group-unsupported", icd)
        self.assertIn("physicalDeviceCount != 1", icd)
        self.assertIn("VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SUBPASS_MERGE_FEEDBACK_FEATURES_EXT", icd)
        self.assertIn("p->subpassMergeFeedback = VK_FALSE", icd)
        self.assertIn("supported = !p->subpassMergeFeedback", icd)
        self.assertIn('unsupported_feature_name = "subpassMergeFeedback"', icd)
        self.assertIn("p->pPhysicalDevices[0] != (VkPhysicalDevice)&g_device", icd)
        self.assertIn("VK_ERROR_FEATURE_NOT_PRESENT", icd)
        for marker in [
            "VK_KHR_MAINTENANCE_4_EXTENSION_NAME",
            "p->maintenance4 = VK_TRUE;",
            "PDOCKER_VK_FEATURE_MAINTENANCE_4",
            "vkGetDeviceBufferMemoryRequirements",
            "vkGetDeviceImageMemoryRequirements",
            "vkGetImageSparseMemoryRequirements2",
            "vkGetDeviceImageSparseMemoryRequirements",
            'MAP_ALIAS("vkGetDeviceBufferMemoryRequirementsKHR", vkGetDeviceBufferMemoryRequirements)',
            'MAP_ALIAS("vkGetDeviceImageMemoryRequirementsKHR", vkGetDeviceImageMemoryRequirements)',
            'MAP_ALIAS("vkGetDeviceImageSparseMemoryRequirementsKHR", vkGetDeviceImageSparseMemoryRequirements)',
            "PDOCKER_VK_FEATURE_SYNCHRONIZATION_2",
            "PDOCKER_VK_FEATURE_DYNAMIC_RENDERING",
            "PDOCKER_VK_FEATURE_EXTENDED_DYNAMIC_STATE",
            "PDOCKER_VK_FEATURE_EXTENDED_DYNAMIC_STATE_2",
            "PDOCKER_VK_FEATURE_TESSELLATION_SHADER",
            "if (p->synchronization2) mask |= PDOCKER_VK_FEATURE_SYNCHRONIZATION_2;",
            "if (p->dynamicRendering) mask |= PDOCKER_VK_FEATURE_DYNAMIC_RENDERING;",
            "if (p->extendedDynamicState) mask |= PDOCKER_VK_FEATURE_EXTENDED_DYNAMIC_STATE;",
            "if (p->extendedDynamicState2) mask |= PDOCKER_VK_FEATURE_EXTENDED_DYNAMIC_STATE_2;",
            "if (features->tessellationShader) mask |= PDOCKER_VK_FEATURE_TESSELLATION_SHADER;",
            "PDOCKER_VK_FOR_EACH_BASE_FEATURE(CHECK_BASE_FEATURE)",
            "F(samplerAnisotropy)",
            "F(geometryShader)",
            "F(sampleRateShading)",
            "F(alphaToOne)",
            "PDOCKER_VK_REJECT_UNSUPPORTED_FEATURE_FIELD(requested, &supported, descriptorIndexing);",
            "PDOCKER_VK_REJECT_UNSUPPORTED_FEATURE_FIELD(requested, &supported, scalarBlockLayout);",
            "PDOCKER_VK_REJECT_UNSUPPORTED_FEATURE_FIELD(requested, &supported, bufferDeviceAddress);",
            "supported = !p->samplerYcbcrConversion;",
            "supported = !p->vulkanMemoryModel && !p->vulkanMemoryModelDeviceScope",
            "if (caps->features.tessellationShader) mask |= PDOCKER_VK_FEATURE_TESSELLATION_SHADER;",
            "if (advertised_synchronization2()) mask |= PDOCKER_VK_FEATURE_SYNCHRONIZATION_2;",
            "if (advertised_dynamic_rendering()) mask |= PDOCKER_VK_FEATURE_DYNAMIC_RENDERING;",
        ]:
            self.assertIn(marker, icd)
        create_body = icd.split("VKAPI_ATTR VkResult VKAPI_CALL vkCreateDevice", 1)[1].split(
            "VKAPI_ATTR void VKAPI_CALL vkDestroyDevice", 1
        )[0]
        self.assertIn("*pDevice = VK_NULL_HANDLE;", create_body)
        self.assertIn("validate_device_extensions(pCreateInfo)", create_body)
        self.assertIn("physicalDevice != (VkPhysicalDevice)&g_device", create_body)
        self.assertIn("validate_device_feature_requests(pCreateInfo)", create_body)
        self.assertIn("if (feature_rc != VK_SUCCESS) return feature_rc;", create_body)
        self.assertIn("requested_feature_mask_from_device_create_info(pCreateInfo)", create_body)
        self.assertIn("advertised_feature_mask()", create_body)
        self.assertIn("requested_features_supported(requested_feature_mask, supported_feature_mask", create_body)
        self.assertIn("device->requested_feature_mask = requested_feature_mask;", create_body)

    def test_vulkan_copy_commands2_reject_unsupported_pnext(self):
        icd = VULKAN_ICD.read_text()
        body = icd.split("static bool copy_commands2_region_has_pnext", 1)[1].split(
            "VKAPI_ATTR void VKAPI_CALL vkCmdFillBuffer", 1
        )[0]
        for marker in [
            "static bool copy_commands2_reject_unsupported_pnext",
            "read_vk_struct_header(region)",
            "header.pNext != NULL",
            "command_buffer_mark_recording_failed(cmd, reason);",
            "copy-buffer2-pnext-unsupported",
            "copy-image2-pnext-unsupported",
            "copy-buffer-to-image2-pnext-unsupported",
            "copy-image-to-buffer2-pnext-unsupported",
            "blit-image2-pnext-unsupported",
            "resolve-image2-pnext-unsupported",
        ]:
            self.assertIn(marker, body)
        for api in [
            "pCopyBufferInfo->pNext",
            "pCopyImageInfo->pNext",
            "pCopyBufferToImageInfo->pNext",
            "pCopyImageToBufferInfo->pNext",
            "pBlitImageInfo->pNext",
            "pResolveImageInfo->pNext",
        ]:
            self.assertIn(api, body)


    def test_vulkan_maintenance4_memory_requirement_apis_are_mapped_and_conservative(self):
        icd = VULKAN_ICD.read_text()
        for marker in [
            "static void fill_buffer_create_memory_requirements",
            "static void fill_image_create_memory_requirements",
            "VKAPI_ATTR void VKAPI_CALL vkGetDeviceBufferMemoryRequirements",
            "VKAPI_ATTR void VKAPI_CALL vkGetDeviceImageMemoryRequirements",
            "VKAPI_ATTR void VKAPI_CALL vkGetImageSparseMemoryRequirements2",
            "VKAPI_ATTR void VKAPI_CALL vkGetDeviceImageSparseMemoryRequirements",
            "fill_memory_requirements2_pnext(pnext);",
            "pCreateInfo->size > pdocker_vulkan_max_buffer_size()",
            "estimate_image_requirement_size(pCreateInfo)",
            "pInfo->planeAspect == 0",
            "*pSparseMemoryRequirementCount = 0;",
            'MAP_PROC(vkGetImageSparseMemoryRequirements2)',
            'MAP_ALIAS("vkGetImageSparseMemoryRequirements2KHR", vkGetImageSparseMemoryRequirements2)',
            'MAP_PROC(vkGetDeviceBufferMemoryRequirements)',
            'MAP_ALIAS("vkGetDeviceBufferMemoryRequirementsKHR", vkGetDeviceBufferMemoryRequirements)',
            'MAP_PROC(vkGetDeviceImageMemoryRequirements)',
            'MAP_ALIAS("vkGetDeviceImageMemoryRequirementsKHR", vkGetDeviceImageMemoryRequirements)',
            'MAP_PROC(vkGetDeviceImageSparseMemoryRequirements)',
            'MAP_ALIAS("vkGetDeviceImageSparseMemoryRequirementsKHR", vkGetDeviceImageSparseMemoryRequirements)',
        ]:
            self.assertIn(marker, icd)
        sparse2_body = c_function_body(icd, "vkGetImageSparseMemoryRequirements2")
        self.assertIn("const VkImageSparseMemoryRequirementsInfo2 *pInfo", icd)
        self.assertIn("*pSparseMemoryRequirementCount = 0;", sparse2_body)
        self.assertNotIn("pSparseMemoryRequirements[0]", sparse2_body)
        hidden_body = c_function_body(icd, "proc_address_hidden_by_advertisement")
        self.assertIn("vkGetImageSparseMemoryRequirements2KHR", hidden_body)


    def test_vulkan_proc_table_exposes_khr_aliases_for_advertised_core2_apis(self):
        icd = VULKAN_ICD.read_text()
        proc_body = icd.split("static PFN_vkVoidFunction proc_address", 1)[1].split(
            "VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL vkGetInstanceProcAddr", 1
        )[0]
        self.assertIn("MAP_ALIAS", proc_body)
        for alias in [
            '"vkGetPhysicalDeviceProperties2KHR", vkGetPhysicalDeviceProperties2',
            '"vkGetPhysicalDeviceFeatures2KHR", vkGetPhysicalDeviceFeatures2',
            '"vkGetPhysicalDeviceQueueFamilyProperties2KHR", vkGetPhysicalDeviceQueueFamilyProperties2',
            '"vkGetPhysicalDeviceMemoryProperties2KHR", vkGetPhysicalDeviceMemoryProperties2',
            '"vkGetDescriptorSetLayoutSupportKHR", vkGetDescriptorSetLayoutSupport',
            '"vkEnumeratePhysicalDeviceGroupsKHR", vkEnumeratePhysicalDeviceGroups',
            '"vkGetDeviceGroupPeerMemoryFeaturesKHR", vkGetDeviceGroupPeerMemoryFeatures',
            '"vkCmdSetDeviceMaskKHR", vkCmdSetDeviceMask',
            '"vkGetPhysicalDeviceSparseImageFormatProperties2KHR", vkGetPhysicalDeviceSparseImageFormatProperties2',
            '"vkGetPhysicalDeviceExternalBufferPropertiesKHR", vkGetPhysicalDeviceExternalBufferProperties',
            '"vkGetPhysicalDeviceExternalSemaphorePropertiesKHR", vkGetPhysicalDeviceExternalSemaphoreProperties',
            '"vkGetPhysicalDeviceExternalFencePropertiesKHR", vkGetPhysicalDeviceExternalFenceProperties',
            '"vkGetBufferMemoryRequirements2KHR", vkGetBufferMemoryRequirements2',
            '"vkGetImageSparseMemoryRequirements2KHR", vkGetImageSparseMemoryRequirements2',
            '"vkBindBufferMemory2KHR", vkBindBufferMemory2',
        ]:
            self.assertIn(alias, proc_body)

    def test_vulkan_physical_device_group_and_external_property_queries_fail_closed(self):
        icd = VULKAN_ICD.read_text()
        group_body = c_function_body(icd, "vkEnumeratePhysicalDeviceGroups")
        sparse2_body = c_function_body(icd, "vkGetPhysicalDeviceSparseImageFormatProperties2")
        peer_body = c_function_body(icd, "vkGetDeviceGroupPeerMemoryFeatures")
        device_mask_body = c_function_body(icd, "vkCmdSetDeviceMask")
        external_buffer_body = c_function_body(icd, "vkGetPhysicalDeviceExternalBufferProperties")
        external_semaphore_body = c_function_body(icd, "vkGetPhysicalDeviceExternalSemaphoreProperties")
        external_fence_body = c_function_body(icd, "vkGetPhysicalDeviceExternalFenceProperties")
        proc_body = icd.split("static PFN_vkVoidFunction proc_address", 1)[1].split(
            "VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL vkGetInstanceProcAddr", 1
        )[0]
        hidden_body = c_function_body(icd, "proc_address_hidden_by_advertisement")

        self.assertIn("*pPhysicalDeviceGroupCount = 1;", group_body)
        self.assertIn("zero_vk_out_struct_preserve_chain(&pPhysicalDeviceGroupProperties[0]", group_body)
        self.assertIn("physicalDeviceCount = 1", group_body)
        self.assertIn("physicalDevices[0] = (VkPhysicalDevice)&g_device", group_body)
        self.assertIn("subsetAllocation = VK_FALSE", group_body)
        self.assertIn("return VK_INCOMPLETE", group_body)
        self.assertIn("*pPeerMemoryFeatures = 0;", peer_body)
        self.assertIn("deviceMask != 1u", device_mask_body)
        self.assertIn('command_buffer_mark_recording_failed(cmd, "device-mask-unsupported")', device_mask_body)

        self.assertIn("const VkPhysicalDeviceSparseImageFormatInfo2 *pFormatInfo", icd)
        self.assertIn("*pPropertyCount = 0;", sparse2_body)
        self.assertNotIn("pProperties[0]", sparse2_body)

        for body in [external_buffer_body, external_semaphore_body, external_fence_body]:
            self.assertIn("zero_vk_out_struct_preserve_chain", body)
            self.assertNotIn("externalMemoryFeatures =", body)
            self.assertNotIn("externalSemaphoreFeatures =", body)
            self.assertNotIn("externalFenceFeatures =", body)
            self.assertNotIn("compatibleHandleTypes = p", body)
            self.assertNotIn("EXPORTABLE_BIT", body)
            self.assertNotIn("IMPORTABLE_BIT", body)

        for marker in [
            "MAP_PROC(vkEnumeratePhysicalDeviceGroups)",
            'MAP_ALIAS("vkEnumeratePhysicalDeviceGroupsKHR", vkEnumeratePhysicalDeviceGroups)',
            "MAP_PROC(vkGetDeviceGroupPeerMemoryFeatures)",
            'MAP_ALIAS("vkGetDeviceGroupPeerMemoryFeaturesKHR", vkGetDeviceGroupPeerMemoryFeatures)',
            "MAP_PROC(vkCmdSetDeviceMask)",
            'MAP_ALIAS("vkCmdSetDeviceMaskKHR", vkCmdSetDeviceMask)',
            "MAP_PROC(vkGetPhysicalDeviceSparseImageFormatProperties2)",
            'MAP_ALIAS("vkGetPhysicalDeviceSparseImageFormatProperties2KHR", vkGetPhysicalDeviceSparseImageFormatProperties2)',
            "MAP_PROC(vkGetPhysicalDeviceExternalBufferProperties)",
            'MAP_ALIAS("vkGetPhysicalDeviceExternalBufferPropertiesKHR", vkGetPhysicalDeviceExternalBufferProperties)',
            "MAP_PROC(vkGetPhysicalDeviceExternalSemaphoreProperties)",
            'MAP_ALIAS("vkGetPhysicalDeviceExternalSemaphorePropertiesKHR", vkGetPhysicalDeviceExternalSemaphoreProperties)',
            "MAP_PROC(vkGetPhysicalDeviceExternalFenceProperties)",
            'MAP_ALIAS("vkGetPhysicalDeviceExternalFencePropertiesKHR", vkGetPhysicalDeviceExternalFenceProperties)',
        ]:
            self.assertIn(marker, proc_body)
        for alias in [
            "vkEnumeratePhysicalDeviceGroupsKHR",
            "vkGetDeviceGroupPeerMemoryFeaturesKHR",
            "vkCmdSetDeviceMaskKHR",
            "vkGetPhysicalDeviceSparseImageFormatProperties2KHR",
            "vkGetPhysicalDeviceExternalBufferPropertiesKHR",
            "vkGetPhysicalDeviceExternalSemaphorePropertiesKHR",
            "vkGetPhysicalDeviceExternalFencePropertiesKHR",
        ]:
            self.assertIn(alias, hidden_body)
        for extension in [
            "VK_KHR_device_group_creation",
            "VK_KHR_get_physical_device_properties2",
            "VK_KHR_external_memory_capabilities",
            "VK_KHR_external_semaphore_capabilities",
            "VK_KHR_external_fence_capabilities",
            "VK_KHR_external_memory_fd",
            "VK_KHR_external_semaphore_fd",
            "VK_KHR_external_fence_fd",
        ]:
            self.assertNotIn(extension, icd)

    def test_vulkan_core_sparse_template_bda_and_trim_surfaces_fail_closed(self):
        icd = VULKAN_ICD.read_text()
        legacy_sparse_body = c_function_body(icd, "vkGetImageSparseMemoryRequirements")
        queue_sparse_body = c_function_body(icd, "vkQueueBindSparse")
        buffer_view_body = c_function_body(icd, "vkCreateBufferView")
        ycbcr_body = c_function_body(icd, "vkCreateSamplerYcbcrConversion")
        template_create_body = c_function_body(icd, "vkCreateDescriptorUpdateTemplate")
        template_update_body = c_function_body(icd, "vkUpdateDescriptorSetWithTemplate")
        bda_body = c_function_body(icd, "vkGetBufferDeviceAddress")
        opaque_buffer_body = c_function_body(icd, "vkGetBufferOpaqueCaptureAddress")
        opaque_memory_body = c_function_body(icd, "vkGetDeviceMemoryOpaqueCaptureAddress")
        trim_body = c_function_body(icd, "vkTrimCommandPool")
        proc_body = icd.split("static PFN_vkVoidFunction proc_address", 1)[1].split(
            "VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL vkGetInstanceProcAddr", 1
        )[0]
        hidden_body = c_function_body(icd, "proc_address_hidden_by_advertisement")

        self.assertIn("VkSparseImageMemoryRequirements *pSparseMemoryRequirements", icd)
        self.assertIn("*pSparseMemoryRequirementCount = 0;", legacy_sparse_body)
        self.assertNotIn("pSparseMemoryRequirements[0]", legacy_sparse_body)

        self.assertIn("if (bindInfoCount > 0 && !pBindInfo) return VK_ERROR_INITIALIZATION_FAILED;", queue_sparse_body)
        self.assertIn("bindInfoCount != 0", queue_sparse_body)
        self.assertIn("sparse-binding-unsupported", queue_sparse_body)
        self.assertIn("submit_fence->signaled = true;", queue_sparse_body)

        self.assertIn("*pView = VK_NULL_HANDLE;", buffer_view_body)
        self.assertIn('unsupported_create_info_pnext_result("vkCreateBufferView", pCreateInfo->pNext)', buffer_view_body)
        self.assertIn("buffer-view-unsupported", buffer_view_body)

        self.assertIn("*pYcbcrConversion = VK_NULL_HANDLE;", ycbcr_body)
        self.assertIn('unsupported_create_info_pnext_result("vkCreateSamplerYcbcrConversion", pCreateInfo->pNext)', ycbcr_body)
        self.assertIn("sampler-ycbcr-conversion-unsupported", ycbcr_body)

        self.assertIn("*pDescriptorUpdateTemplate = VK_NULL_HANDLE;", template_create_body)
        self.assertIn('unsupported_create_info_pnext_result("vkCreateDescriptorUpdateTemplate", pCreateInfo->pNext)', template_create_body)
        self.assertIn("descriptor-update-template-unsupported", template_create_body)
        self.assertIn("descriptor-update-template-unsupported", template_update_body)

        self.assertIn("return 0;", bda_body)
        self.assertIn("return 0;", opaque_buffer_body)
        self.assertIn("return 0;", opaque_memory_body)
        self.assertIn("VkCommandPoolTrimFlags flags", icd)

        for marker in [
            "MAP_PROC(vkGetImageSparseMemoryRequirements)",
            "MAP_PROC(vkQueueBindSparse)",
            "MAP_PROC(vkCreateBufferView)",
            "MAP_PROC(vkDestroyBufferView)",
            "MAP_PROC(vkTrimCommandPool)",
            'MAP_ALIAS("vkTrimCommandPoolKHR", vkTrimCommandPool)',
            "MAP_PROC(vkCreateSamplerYcbcrConversion)",
            'MAP_ALIAS("vkCreateSamplerYcbcrConversionKHR", vkCreateSamplerYcbcrConversion)',
            "MAP_PROC(vkDestroySamplerYcbcrConversion)",
            'MAP_ALIAS("vkDestroySamplerYcbcrConversionKHR", vkDestroySamplerYcbcrConversion)',
            "MAP_PROC(vkCreateDescriptorUpdateTemplate)",
            'MAP_ALIAS("vkCreateDescriptorUpdateTemplateKHR", vkCreateDescriptorUpdateTemplate)',
            "MAP_PROC(vkDestroyDescriptorUpdateTemplate)",
            'MAP_ALIAS("vkDestroyDescriptorUpdateTemplateKHR", vkDestroyDescriptorUpdateTemplate)',
            "MAP_PROC(vkUpdateDescriptorSetWithTemplate)",
            'MAP_ALIAS("vkUpdateDescriptorSetWithTemplateKHR", vkUpdateDescriptorSetWithTemplate)',
            "MAP_PROC(vkGetBufferDeviceAddress)",
            'MAP_ALIAS("vkGetBufferDeviceAddressKHR", vkGetBufferDeviceAddress)',
            'MAP_ALIAS("vkGetBufferDeviceAddressEXT", vkGetBufferDeviceAddress)',
            "MAP_PROC(vkGetBufferOpaqueCaptureAddress)",
            'MAP_ALIAS("vkGetBufferOpaqueCaptureAddressKHR", vkGetBufferOpaqueCaptureAddress)',
            "MAP_PROC(vkGetDeviceMemoryOpaqueCaptureAddress)",
            'MAP_ALIAS("vkGetDeviceMemoryOpaqueCaptureAddressKHR", vkGetDeviceMemoryOpaqueCaptureAddress)',
        ]:
            self.assertIn(marker, proc_body)
        for alias in [
            "vkTrimCommandPoolKHR",
            "vkCreateSamplerYcbcrConversionKHR",
            "vkDestroySamplerYcbcrConversionKHR",
            "vkCreateDescriptorUpdateTemplateKHR",
            "vkDestroyDescriptorUpdateTemplateKHR",
            "vkUpdateDescriptorSetWithTemplateKHR",
            "vkGetBufferDeviceAddressKHR",
            "vkGetBufferOpaqueCaptureAddressKHR",
            "vkGetDeviceMemoryOpaqueCaptureAddressKHR",
            "vkGetBufferDeviceAddressEXT",
        ]:
            self.assertIn(alias, hidden_body)


    def test_vulkan_physical_memory_properties2_output_is_fully_initialized(self):
        icd = VULKAN_ICD.read_text()
        body = icd.split("VKAPI_ATTR void VKAPI_CALL vkGetPhysicalDeviceMemoryProperties2", 1)[1].split(
            "VKAPI_ATTR VkResult VKAPI_CALL vkCreateBuffer", 1
        )[0]
        self.assertIn("PdockerVkStructHeader header = read_vk_struct_header(pMemoryProperties);", body)
        self.assertIn("zero_vk_out_struct_preserve_chain(pMemoryProperties, sizeof(*pMemoryProperties), header);", body)
        self.assertIn("vkGetPhysicalDeviceMemoryProperties(physicalDevice, &pMemoryProperties->memoryProperties);", body)


    def test_vulkan_memory_requirements2_outputs_are_fully_initialized(self):
        icd = VULKAN_ICD.read_text()
        self.assertIn("fill_memory_requirements2_pnext", icd)
        helper_body = icd.split("static void fill_memory_requirements2_pnext", 1)[1].split(
            "VKAPI_ATTR void VKAPI_CALL vkGetImageMemoryRequirements2", 1
        )[0]
        self.assertIn("zero_vk_out_struct_preserve_chain(dedicated, sizeof(*dedicated), header);", helper_body)
        self.assertIn("dedicated->prefersDedicatedAllocation = VK_FALSE;", helper_body)
        self.assertIn("dedicated->requiresDedicatedAllocation = VK_FALSE;", helper_body)
        image_body = icd.split("VKAPI_ATTR void VKAPI_CALL vkGetImageMemoryRequirements2", 1)[1].split(
            "VKAPI_ATTR void VKAPI_CALL vkGetImageSubresourceLayout", 1
        )[0]
        self.assertIn("zero_vk_out_struct_preserve_chain(pMemoryRequirements, sizeof(*pMemoryRequirements), header);", image_body)
        self.assertIn("fill_memory_requirements2_pnext(pnext);", image_body)
        buffer_body = icd.split("VKAPI_ATTR void VKAPI_CALL vkGetBufferMemoryRequirements2", 1)[1].split(
            "VKAPI_ATTR VkResult VKAPI_CALL vkAllocateMemory", 1
        )[0]
        self.assertIn("zero_vk_out_struct_preserve_chain(pMemoryRequirements, sizeof(*pMemoryRequirements), header);", buffer_body)
        self.assertIn("fill_memory_requirements2_pnext(pnext);", buffer_body)
        self.assertNotIn("for (void *node = pMemoryRequirements->pNext", buffer_body)


    def test_vulkan_bind_memory2_accepts_single_device_group_noop_and_rejects_widening(self):
        icd = VULKAN_ICD.read_text()
        self.assertIn("static VkResult validate_bind_memory_info_pnext", icd)
        validator_body = c_function_body(icd, "validate_bind_memory_info_pnext")
        self.assertIn("VK_STRUCTURE_TYPE_BIND_BUFFER_MEMORY_DEVICE_GROUP_INFO", validator_body)
        self.assertIn("VK_STRUCTURE_TYPE_BIND_IMAGE_MEMORY_DEVICE_GROUP_INFO", validator_body)
        self.assertIn("info->deviceIndexCount > 1", validator_body)
        self.assertIn("!info->pDeviceIndices || info->pDeviceIndices[0] != 0", validator_body)
        self.assertIn("info->splitInstanceBindRegionCount != 0", validator_body)
        self.assertNotIn("info->pSplitInstanceBindRegions", validator_body)
        self.assertIn("unsupported_create_info_pnext_result(api_name, node)", validator_body)
        buffer_body = c_function_body(icd, "vkBindBufferMemory2")
        image_body = c_function_body(icd, "vkBindImageMemory2")
        for body, api in [(buffer_body, "vkBindBufferMemory2"), (image_body, "vkBindImageMemory2")]:
            self.assertIn("if (bindInfoCount > 0 && !pBindInfos) return VK_ERROR_INITIALIZATION_FAILED;", body)
            self.assertIn(f'validate_bind_memory_info_pnext("{api}", pBindInfos[i].pNext)', body)
            self.assertIn("if (pnext_rc != VK_SUCCESS) return pnext_rc;", body)


    def test_vulkan_descriptor_set_layout_support_is_mapped_and_conservative(self):
        icd = VULKAN_ICD.read_text()
        support_body = c_function_body(icd, "vkGetDescriptorSetLayoutSupport")
        helper_body = c_function_body(icd, "descriptor_set_layout_create_info_supported")
        pnext_body = c_function_body(icd, "fill_descriptor_set_layout_support_pnext")
        proc_body = icd.split("static PFN_vkVoidFunction proc_address", 1)[1].split(
            "VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL vkGetInstanceProcAddr", 1
        )[0]
        hidden_body = c_function_body(icd, "proc_address_hidden_by_advertisement")

        self.assertIn("zero_vk_out_struct_preserve_chain(pSupport, sizeof(*pSupport), header);", support_body)
        self.assertIn("descriptor_set_layout_create_info_supported(pCreateInfo)", support_body)
        self.assertIn("fill_descriptor_set_layout_support_pnext((void *)header.pNext);", support_body)
        self.assertIn("validate_descriptor_set_layout_pnext(pCreateInfo) != VK_SUCCESS", helper_body)
        self.assertIn("pCreateInfo->flags != 0", helper_body)
        self.assertIn("pCreateInfo->bindingCount > 0 && !pCreateInfo->pBindings", helper_body)
        self.assertIn("descriptor_type_supported_by_v4_transport(binding->descriptorType)", helper_body)
        self.assertIn("descriptor_type_supported_by_v5_object_transport(binding->descriptorType)", helper_body)
        self.assertIn("binding->binding >= PDOCKER_VK_MAX_STORAGE_BUFFERS", helper_body)
        self.assertIn("binding->descriptorCount > PDOCKER_VK_MAX_DESCRIPTOR_ARRAY_ELEMENTS", helper_body)
        self.assertIn("VK_STRUCTURE_TYPE_DESCRIPTOR_SET_VARIABLE_DESCRIPTOR_COUNT_LAYOUT_SUPPORT", pnext_body)
        self.assertIn("p->maxVariableDescriptorCount = 0;", pnext_body)
        self.assertIn("MAP_PROC(vkGetDescriptorSetLayoutSupport)", proc_body)
        self.assertIn('MAP_ALIAS("vkGetDescriptorSetLayoutSupportKHR", vkGetDescriptorSetLayoutSupport)', proc_body)
        self.assertIn("vkGetDescriptorSetLayoutSupportKHR", hidden_body)


    def test_vulkan_descriptor_pool_and_allocate_pnext_are_explicit_noop_or_fail_closed(self):
        icd = VULKAN_ICD.read_text()
        pool_pnext_body = c_function_body(icd, "validate_descriptor_pool_create_pnext")
        create_pool_body = c_function_body(icd, "vkCreateDescriptorPool")
        allocate_pnext_body = c_function_body(icd, "validate_descriptor_set_allocate_pnext")
        allocate_body = c_function_body(icd, "vkAllocateDescriptorSets")

        self.assertIn("VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_INLINE_UNIFORM_BLOCK_CREATE_INFO", pool_pnext_body)
        self.assertIn("info->maxInlineUniformBlockBindings != 0", pool_pnext_body)
        self.assertIn("descriptor-pool-inline-uniform-unsupported", pool_pnext_body)
        self.assertIn('unsupported_create_info_pnext_result("vkCreateDescriptorPool", node)', pool_pnext_body)
        self.assertIn("*pDescriptorPool = VK_NULL_HANDLE;", create_pool_body)
        self.assertIn("pCreateInfo->sType != VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO", create_pool_body)
        self.assertIn("validate_descriptor_pool_create_pnext(pCreateInfo->pNext)", create_pool_body)

        self.assertIn("VK_STRUCTURE_TYPE_DESCRIPTOR_SET_VARIABLE_DESCRIPTOR_COUNT_ALLOCATE_INFO", allocate_pnext_body)
        self.assertIn("counts->descriptorSetCount != info->descriptorSetCount", allocate_pnext_body)
        self.assertIn("descriptor-allocate-variable-count-mismatch", allocate_pnext_body)
        self.assertIn("counts->descriptorSetCount != 0 && !counts->pDescriptorCounts", allocate_pnext_body)
        self.assertIn("counts->pDescriptorCounts[i] != 0", allocate_pnext_body)
        self.assertIn("descriptor-allocate-variable-count-unsupported", allocate_pnext_body)
        self.assertIn('unsupported_create_info_pnext_result("vkAllocateDescriptorSets", node)', allocate_pnext_body)
        self.assertIn("pAllocateInfo->sType != VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO", allocate_body)
        self.assertIn("validate_descriptor_set_allocate_pnext(pAllocateInfo)", allocate_body)


    def test_vulkan_pipeline_creation_feedback_and_compute_pnext_contract(self):
        icd = VULKAN_ICD.read_text()
        feedback_body = c_function_body(icd, "validate_and_fill_pipeline_feedback_pnext")
        compute_body = c_function_body(icd, "vkCreateComputePipelines")
        graphics_body = c_function_body(icd, "vkCreateGraphicsPipelines")

        self.assertIn("VK_STRUCTURE_TYPE_PIPELINE_CREATION_FEEDBACK_CREATE_INFO", feedback_body)
        self.assertIn("VK_PIPELINE_CREATION_FEEDBACK_VALID_BIT", feedback_body)
        self.assertIn("feedback_info->pPipelineCreationFeedback->duration = 0;", feedback_body)
        self.assertIn("feedback_info->pipelineStageCreationFeedbackCount != stage_count", feedback_body)
        self.assertIn("pipeline-creation-feedback-stage-count-mismatch", feedback_body)
        self.assertIn("VK_STRUCTURE_TYPE_PIPELINE_RENDERING_CREATE_INFO", feedback_body)
        self.assertIn("allow_pipeline_rendering_create_info", feedback_body)
        self.assertIn("unsupported_create_info_pnext_result(api_name, node)", feedback_body)

        self.assertIn('"vkCreateComputePipelines", ci->pNext, 1u, false', compute_body)
        self.assertIn("ci->sType != VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO", compute_body)
        self.assertIn("ci->flags != 0", compute_body)
        self.assertIn("ci->basePipelineHandle != VK_NULL_HANDLE", compute_body)
        self.assertIn("ci->basePipelineIndex >= 0", compute_body)
        self.assertIn("ci->stage.sType != VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO", compute_body)
        self.assertIn("ci->stage.stage != VK_SHADER_STAGE_COMPUTE_BIT", compute_body)
        self.assertIn("ci->stage.flags != 0", compute_body)
        self.assertIn("ci->stage.pNext", compute_body)
        self.assertIn('unsupported_create_info_pnext_result("vkCreateComputePipelines.stage"', compute_body)

        self.assertIn('"vkCreateGraphicsPipelines", ci->pNext, ci->stageCount, true', graphics_body)
        self.assertIn("VK_STRUCTURE_TYPE_PIPELINE_CREATION_FEEDBACK_CREATE_INFO", graphics_body)


    def test_vulkan_external_handle_and_sync_pnext_paths_fail_closed(self):
        icd = VULKAN_ICD.read_text()
        alloc_validator = c_function_body(icd, "validate_memory_allocate_pnext")
        allocate_body = c_function_body(icd, "vkAllocateMemory")
        fence_validator = c_function_body(icd, "validate_fence_create_pnext")
        fence_body = c_function_body(icd, "vkCreateFence")
        sem_parse_body = c_function_body(icd, "semaphore_create_info_parse_pnext")
        sem_create_body = c_function_body(icd, "vkCreateSemaphore")
        fill_buffer_req_body = c_function_body(icd, "fill_buffer_create_memory_requirements")
        device_buffer_req_body = c_function_body(icd, "vkGetDeviceBufferMemoryRequirements")
        device_image_req_body = c_function_body(icd, "vkGetDeviceImageMemoryRequirements")
        bind_buffer_body = c_function_body(icd, "vkBindBufferMemory")

        self.assertIn("validate_memory_allocate_pnext", icd)
        self.assertIn("unsupported_create_info_pnext_result(\"vkAllocateMemory\", node)", alloc_validator)
        self.assertIn("VK_STRUCTURE_TYPE_MEMORY_DEDICATED_ALLOCATE_INFO", alloc_validator)
        self.assertIn("has_image && has_buffer", alloc_validator)
        self.assertIn("pdocker_vk_image_from_handle(info->image)", alloc_validator)
        self.assertIn("pdocker_vk_buffer_from_handle(info->buffer)", alloc_validator)
        self.assertIn("VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_FLAGS_INFO", alloc_validator)
        self.assertIn("info->flags != 0 || info->deviceMask > 1", alloc_validator)
        self.assertIn("*pMemory = VK_NULL_HANDLE;", allocate_body)
        self.assertIn("validate_memory_allocate_pnext(pAllocateInfo->pNext)", allocate_body)
        self.assertIn("if (pnext_rc != VK_SUCCESS) return pnext_rc;", allocate_body)

        self.assertIn("validate_fence_create_pnext", icd)
        self.assertIn("VK_STRUCTURE_TYPE_EXPORT_FENCE_CREATE_INFO", fence_validator)
        self.assertIn("info->handleTypes != 0", fence_validator)
        self.assertIn('unsupported_create_info_pnext_result("vkCreateFence", node)', fence_validator)
        self.assertIn("*pFence = VK_NULL_HANDLE;", fence_body)
        self.assertIn("validate_fence_create_pnext(pCreateInfo ? pCreateInfo->pNext : NULL)", fence_body)
        self.assertIn("if (pnext_rc != VK_SUCCESS) return pnext_rc;", fence_body)
        self.assertIn("pCreateInfo->flags & ~VK_FENCE_CREATE_SIGNALED_BIT", fence_body)
        self.assertIn("fence->signaled = pCreateInfo && (pCreateInfo->flags & VK_FENCE_CREATE_SIGNALED_BIT);", fence_body)

        self.assertIn("bool seen_type = false;", sem_parse_body)
        self.assertIn("if (seen_type) return false;", sem_parse_body)
        self.assertIn("info->semaphoreType == VK_SEMAPHORE_TYPE_BINARY", sem_parse_body)
        self.assertIn("if (info->initialValue != 0) return false;", sem_parse_body)
        self.assertIn("VK_STRUCTURE_TYPE_EXPORT_SEMAPHORE_CREATE_INFO", sem_parse_body)
        self.assertIn("info->handleTypes != 0", sem_parse_body)
        self.assertIn("advertised_timeline_semaphore()", sem_create_body)
        self.assertIn("dev->requested_feature_mask & PDOCKER_VK_FEATURE_TIMELINE_SEMAPHORE", sem_create_body)
        self.assertIn("timeline-semaphore-feature-not-enabled", sem_create_body)

        self.assertIn("pCreateInfo->pNext", fill_buffer_req_body)
        self.assertIn("pCreateInfo->flags != 0", fill_buffer_req_body)
        self.assertIn("if (pInfo && pInfo->pNext)", device_buffer_req_body)
        self.assertIn("device-buffer-memory-requirements-pnext-unsupported", device_buffer_req_body)
        self.assertIn("if (pInfo && pInfo->pNext)", device_image_req_body)
        self.assertIn("device-image-memory-requirements-pnext-unsupported", device_image_req_body)
        self.assertIn("PdockerVkMemory *m = pdocker_vk_memory_from_handle(memory);", bind_buffer_body)
        self.assertIn("if (!b || !m) return VK_ERROR_INITIALIZATION_FAILED;", bind_buffer_body)


    def test_vulkan_core_create_infos_reject_unsupported_pnext_and_flags(self):
        icd = VULKAN_ICD.read_text()
        self.assertIn("unsupported_create_info_pnext_result", icd)
        for api in [
            "vkCreateBuffer",
            "vkCreatePipelineLayout",
            "vkCreateDescriptorPool",
            "vkAllocateDescriptorSets",
            "vkCreateShaderModule",
            "vkCreateQueryPool",
        ]:
            self.assertIn(f'unsupported_create_info_pnext_result("{api}"', icd)
        create_buffer_body = icd.split("VKAPI_ATTR VkResult VKAPI_CALL vkCreateBuffer", 1)[1].split(
            "VKAPI_ATTR void VKAPI_CALL vkDestroyBuffer", 1
        )[0]
        buffer_pnext_body = icd.split("static VkResult validate_buffer_create_pnext", 1)[1].split(
            "VKAPI_ATTR VkResult VKAPI_CALL vkCreateBuffer", 1
        )[0]
        self.assertIn("validate_buffer_create_pnext(pCreateInfo)", create_buffer_body)
        self.assertIn("if (pnext_rc != VK_SUCCESS) return pnext_rc;", create_buffer_body)
        self.assertIn("VK_STRUCTURE_TYPE_EXTERNAL_MEMORY_BUFFER_CREATE_INFO", buffer_pnext_body)
        self.assertIn("external_info->handleTypes != 0", buffer_pnext_body)
        self.assertIn("buffer-external-memory-handle-unsupported", buffer_pnext_body)
        self.assertIn("VK_STRUCTURE_TYPE_BUFFER_OPAQUE_CAPTURE_ADDRESS_CREATE_INFO", buffer_pnext_body)
        self.assertIn("capture_info->opaqueCaptureAddress != 0", buffer_pnext_body)
        self.assertIn("VK_STRUCTURE_TYPE_BUFFER_USAGE_FLAGS_2_CREATE_INFO", buffer_pnext_body)
        self.assertIn("usage2_info->usage & ~(VkBufferUsageFlags2)UINT32_MAX", buffer_pnext_body)
        self.assertIn("(VkBufferUsageFlags)usage2_info->usage != info->usage", buffer_pnext_body)
        self.assertIn("VK_STRUCTURE_TYPE_BUFFER_DEVICE_ADDRESS_CREATE_INFO_EXT", buffer_pnext_body)
        self.assertIn("address_info->deviceAddress != 0", buffer_pnext_body)
        self.assertIn("VK_STRUCTURE_TYPE_DEDICATED_ALLOCATION_BUFFER_CREATE_INFO_NV", buffer_pnext_body)
        self.assertIn("dedicated_info->dedicatedAllocation", buffer_pnext_body)
        self.assertIn('unsupported_create_info_pnext_result("vkCreateBuffer", node)', buffer_pnext_body)
        self.assertIn("node = header.pNext;", buffer_pnext_body)
        self.assertIn("if (pCreateInfo->flags != 0) return VK_ERROR_FEATURE_NOT_PRESENT;", create_buffer_body)
        descriptor_layout_body = icd.split("VKAPI_ATTR VkResult VKAPI_CALL vkCreateDescriptorSetLayout", 1)[1].split(
            "VKAPI_ATTR void VKAPI_CALL vkDestroyDescriptorSetLayout", 1
        )[0]
        self.assertIn("if (!pCreateInfo || !pSetLayout)", descriptor_layout_body)
        self.assertIn("validate_descriptor_set_layout_pnext(pCreateInfo)", descriptor_layout_body)
        self.assertIn("if (pCreateInfo->flags != 0) return VK_ERROR_FEATURE_NOT_PRESENT;", descriptor_layout_body)
        descriptor_layout_pnext_body = icd.split("static VkResult validate_descriptor_set_layout_pnext", 1)[1].split(
            "static VkResult unsupported_image_transport_result", 1
        )[0]
        self.assertIn("VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_BINDING_FLAGS_CREATE_INFO", descriptor_layout_pnext_body)
        self.assertIn("#if !defined(VK_VERSION_1_2) && !defined(VK_EXT_descriptor_indexing)", icd)
        self.assertIn("#define VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_BINDING_FLAGS_CREATE_INFO ((VkStructureType)1000161000)", icd)
        self.assertIn("PdockerVkDescriptorSetLayoutBindingFlagsCreateInfoCompat", descriptor_layout_pnext_body)
        self.assertNotIn("#ifdef VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_BINDING_FLAGS_CREATE_INFO", descriptor_layout_pnext_body)
        self.assertNotIn("#ifdef VK_STRUCTURE_TYPE_", icd)
        self.assertNotIn("#ifndef VK_STRUCTURE_TYPE_", icd)
        self.assertIn("flags->bindingCount != pCreateInfo->bindingCount", descriptor_layout_pnext_body)
        self.assertIn("descriptor-set-layout-binding-flags-count-mismatch", descriptor_layout_pnext_body)
        self.assertIn("flags->bindingCount > 0 && !flags->pBindingFlags", descriptor_layout_pnext_body)
        self.assertIn("descriptor-set-layout-binding-flags-missing", descriptor_layout_pnext_body)
        self.assertIn("flags->pBindingFlags[i] != 0", descriptor_layout_pnext_body)
        self.assertIn("descriptor-set-layout-binding-flags-unsupported", descriptor_layout_pnext_body)
        self.assertIn('unsupported_create_info_pnext_result("vkCreateDescriptorSetLayout", node)', descriptor_layout_pnext_body)
        pipeline_layout_body = icd.split("VKAPI_ATTR VkResult VKAPI_CALL vkCreatePipelineLayout", 1)[1].split(
            "VKAPI_ATTR void VKAPI_CALL vkDestroyPipelineLayout", 1
        )[0]
        self.assertIn("if (!pCreateInfo || !pPipelineLayout)", pipeline_layout_body)
        self.assertIn("if (pCreateInfo->flags != 0) return VK_ERROR_FEATURE_NOT_PRESENT;", pipeline_layout_body)
        descriptor_pool_body = icd.split("VKAPI_ATTR VkResult VKAPI_CALL vkCreateDescriptorPool", 1)[1].split(
            "VKAPI_ATTR void VKAPI_CALL vkDestroyDescriptorPool", 1
        )[0]
        self.assertIn("if (!pDescriptorPool) return VK_ERROR_INITIALIZATION_FAILED;", descriptor_pool_body)
        self.assertIn("*pDescriptorPool = VK_NULL_HANDLE;", descriptor_pool_body)
        self.assertIn("pCreateInfo->sType != VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO", descriptor_pool_body)
        self.assertIn("validate_descriptor_pool_create_pnext(pCreateInfo->pNext)", descriptor_pool_body)
        self.assertIn("pCreateInfo->flags & ~VK_DESCRIPTOR_POOL_CREATE_FREE_DESCRIPTOR_SET_BIT", descriptor_pool_body)
        allocate_sets_body = icd.split("VKAPI_ATTR VkResult VKAPI_CALL vkAllocateDescriptorSets", 1)[1].split(
            "VKAPI_ATTR VkResult VKAPI_CALL vkFreeDescriptorSets", 1
        )[0]
        self.assertIn("pAllocateInfo->sType != VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO", allocate_sets_body)
        self.assertIn("validate_descriptor_set_allocate_pnext(pAllocateInfo)", allocate_sets_body)
        shader_module_body = icd.split("VKAPI_ATTR VkResult VKAPI_CALL vkCreateShaderModule", 1)[1].split(
            "VKAPI_ATTR void VKAPI_CALL vkDestroyShaderModule", 1
        )[0]
        self.assertIn("pCreateInfo->codeSize % sizeof(uint32_t)", shader_module_body)
        self.assertIn("!pCreateInfo->pCode", shader_module_body)
        query_pool_body = icd.split("VKAPI_ATTR VkResult VKAPI_CALL vkCreateQueryPool", 1)[1].split(
            "VKAPI_ATTR void VKAPI_CALL vkDestroyQueryPool", 1
        )[0]
        self.assertIn("if (pCreateInfo->pNext)", query_pool_body)
        self.assertIn("if (pCreateInfo->flags != 0) return VK_ERROR_FEATURE_NOT_PRESENT;", query_pool_body)
        framebuffer_body = c_function_body(icd, "vkCreateFramebuffer")
        framebuffer_pnext_body = c_function_body(icd, "validate_framebuffer_create_pnext")
        self.assertIn("validate_framebuffer_create_pnext(pCreateInfo->pNext)", framebuffer_body)
        self.assertIn("*pFramebuffer = VK_NULL_HANDLE;", framebuffer_body)
        self.assertIn("pCreateInfo->flags != 0", framebuffer_body)
        self.assertIn("framebuffer-flags-unsupported", framebuffer_body)
        self.assertIn("pCreateInfo->attachmentCount > 0 && !pCreateInfo->pAttachments", framebuffer_body)
        self.assertIn("VK_STRUCTURE_TYPE_FRAMEBUFFER_ATTACHMENTS_CREATE_INFO", framebuffer_pnext_body)
        self.assertIn("info->attachmentImageInfoCount != 0 || info->pAttachmentImageInfos", framebuffer_pnext_body)
        self.assertIn('unsupported_create_info_pnext_result("vkCreateFramebuffer", node)', framebuffer_pnext_body)

    def test_vulkan_render_pass_accepts_only_noop_legacy_multiview_pnext(self):
        icd = VULKAN_ICD.read_text()
        helper_body = c_function_body(icd, "render_pass_create_pnext_noop")
        create_body = c_function_body(icd, "vkCreateRenderPass")
        self.assertIn("VK_STRUCTURE_TYPE_RENDER_PASS_MULTIVIEW_CREATE_INFO", helper_body)
        self.assertIn("mv->subpassCount != info->subpassCount", helper_body)
        self.assertIn("mv->pViewMasks[i] != 0", helper_body)
        self.assertIn("mv->pViewOffsets[i] != 0", helper_body)
        self.assertIn("mv->pCorrelationMasks[i] != 0", helper_body)
        self.assertIn("VK_STRUCTURE_TYPE_RENDER_PASS_INPUT_ATTACHMENT_ASPECT_CREATE_INFO", helper_body)
        self.assertIn("aspect->aspectReferenceCount != 0", helper_body)
        self.assertIn("default:", helper_body)
        self.assertIn("return false;", helper_body)
        self.assertIn("pCreateInfo->flags != 0 || !render_pass_create_pnext_noop(pCreateInfo)", create_body)
        self.assertIn("rp->subpass_overflow = true;", create_body)


    def test_vulkan_command_pool_buffer_and_event_create_infos_fail_closed(self):
        icd = VULKAN_ICD.read_text()
        command_pool_validate = c_function_body(icd, "validate_command_pool_create_info")
        create_pool_body = c_function_body(icd, "vkCreateCommandPool")
        reset_pool_body = c_function_body(icd, "vkResetCommandPool")
        trim_pool_body = c_function_body(icd, "vkTrimCommandPool")
        allocate_body = c_function_body(icd, "vkAllocateCommandBuffers")
        create_event_body = c_function_body(icd, "vkCreateEvent")

        self.assertIn("pCreateInfo->sType != VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO", command_pool_validate)
        self.assertIn("pCreateInfo->queueFamilyIndex >= PDOCKER_VK_ADVERTISED_QUEUE_FAMILY_COUNT", command_pool_validate)
        self.assertIn('unsupported_create_info_pnext_result("vkCreateCommandPool", pCreateInfo->pNext)', command_pool_validate)
        self.assertIn("VK_COMMAND_POOL_CREATE_TRANSIENT_BIT", command_pool_validate)
        self.assertIn("VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT", command_pool_validate)
        self.assertIn("command-pool-flags-unsupported", command_pool_validate)
        self.assertIn("*pCommandPool = VK_NULL_HANDLE;", create_pool_body)
        self.assertIn("validate_command_pool_create_info(pCreateInfo)", create_pool_body)

        self.assertIn("pdocker_vk_command_pool_from_handle(commandPool)", reset_pool_body)
        self.assertIn("flags & ~VK_COMMAND_POOL_RESET_RELEASE_RESOURCES_BIT", reset_pool_body)
        self.assertIn("command-pool-reset-flags-unsupported", reset_pool_body)
        self.assertIn("pdocker_vk_command_pool_from_handle(commandPool)", trim_pool_body)
        self.assertIn("flags != 0", trim_pool_body)
        self.assertIn("command-pool-trim-flags-unsupported", trim_pool_body)

        self.assertIn("pAllocateInfo->sType != VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO", allocate_body)
        self.assertIn("pAllocateInfo->pNext", allocate_body)
        self.assertIn('unsupported_create_info_pnext_result("vkAllocateCommandBuffers"', allocate_body)
        self.assertIn("pdocker_vk_command_pool_from_handle(pAllocateInfo->commandPool)", allocate_body)
        self.assertIn("pAllocateInfo->level != VK_COMMAND_BUFFER_LEVEL_PRIMARY", allocate_body)
        self.assertIn("pAllocateInfo->level != VK_COMMAND_BUFFER_LEVEL_SECONDARY", allocate_body)
        self.assertIn("command-buffer-level-unsupported", allocate_body)

        self.assertIn("*pEvent = VK_NULL_HANDLE;", create_event_body)
        self.assertIn("pCreateInfo->sType != VK_STRUCTURE_TYPE_EVENT_CREATE_INFO", create_event_body)
        self.assertIn('unsupported_create_info_pnext_result("vkCreateEvent", pCreateInfo->pNext)', create_event_body)
        self.assertIn("pCreateInfo->flags != 0", create_event_body)
        self.assertIn("event-flags-unsupported", create_event_body)


    def test_vulkan_image_sampler_object_apis_are_enabled_by_default_and_tracked_for_v5_object_transport(self):
        icd = VULKAN_ICD.read_text()
        for symbol in [
            "vkCreateImage",
            "vkDestroyImage",
            "vkGetImageMemoryRequirements",
            "vkGetImageMemoryRequirements2",
            "vkBindImageMemory",
            "vkBindImageMemory2",
            "vkCreateImageView",
            "vkDestroyImageView",
            "vkCreateSampler",
            "vkDestroySampler",
        ]:
            self.assertIn(symbol, icd)
        self.assertIn("unsupported_image_transport_result", icd)
        self.assertIn("V5 image/sampler object transport", icd)
        self.assertIn("vulkan_v5_object_transport_enabled", icd)
        self.assertIn('env_truthy_default("PDOCKER_VULKAN_ENABLE_V5_OBJECT_TRANSPORT", true)', icd)
        self.assertIn('"PDOCKER_VULKAN_DISABLE_V5_OBJECT_TRANSPORT"', icd)
        self.assertIn("typedef struct PdockerVkImage PdockerVkImage;", icd)
        self.assertIn("typedef struct PdockerVkImageView PdockerVkImageView;", icd)
        self.assertIn("typedef struct PdockerVkSampler PdockerVkSampler;", icd)
        self.assertIn("struct PdockerVkImage {", icd)
        self.assertIn("struct PdockerVkImageView {", icd)
        self.assertIn("struct PdockerVkSampler {", icd)
        self.assertIn('MAP_PROC(vkCreateImage);', icd)
        self.assertIn('MAP_PROC(vkCreateImageView);', icd)
        self.assertIn('MAP_PROC(vkCreateSampler);', icd)
        self.assertIn('MAP_PROC(vkGetImageMemoryRequirements2);', icd)
        self.assertIn('MAP_ALIAS("vkGetImageMemoryRequirements2KHR", vkGetImageMemoryRequirements2);', icd)
        self.assertIn('MAP_PROC(vkBindImageMemory2);', icd)
        self.assertIn('MAP_ALIAS("vkBindImageMemory2KHR", vkBindImageMemory2);', icd)
        self.assertIn('return unsupported_image_transport_result("vkCreateImage");', icd)
        self.assertIn('return unsupported_image_transport_result("vkCreateImageView");', icd)
        self.assertIn('return unsupported_image_transport_result("vkCreateSampler");', icd)
        self.assertIn("unsupported_image_pnext_result", icd)
        self.assertIn("validate_image_create_info_for_transport", icd)
        self.assertIn("validate_image_view_create_info_for_transport", icd)
        self.assertIn("validate_sampler_create_info_for_transport", icd)
        image_pnext_body = icd.split("static VkResult validate_image_create_pnext_for_transport", 1)[1].split(
            "static VkResult validate_image_create_info_for_transport", 1
        )[0]
        image_validate_body = icd.split("static VkResult validate_image_create_info_for_transport", 1)[1].split(
            "static VkResult validate_image_view_create_info_for_transport", 1
        )[0]
        self.assertIn("VK_STRUCTURE_TYPE_EXTERNAL_MEMORY_IMAGE_CREATE_INFO", image_pnext_body)
        self.assertIn("external_info->handleTypes != 0", image_pnext_body)
        self.assertIn("image-external-memory-handle-unsupported", image_pnext_body)
        self.assertIn("VK_STRUCTURE_TYPE_EXTERNAL_MEMORY_IMAGE_CREATE_INFO_NV", image_pnext_body)
        self.assertIn("image-external-memory-nv-handle-unsupported", image_pnext_body)
        self.assertIn("VK_STRUCTURE_TYPE_IMAGE_STENCIL_USAGE_CREATE_INFO", image_pnext_body)
        self.assertIn("stencil_info->stencilUsage != info->usage", image_pnext_body)
        self.assertIn("VK_STRUCTURE_TYPE_IMAGE_FORMAT_LIST_CREATE_INFO", image_pnext_body)
        self.assertIn("format_list->viewFormatCount > 0 && !format_list->pViewFormats", image_pnext_body)
        self.assertIn("format_list->pViewFormats[i] != info->format", image_pnext_body)
        self.assertIn('unsupported_image_pnext_result("vkCreateImage", node)', image_pnext_body)
        self.assertIn("node = header.pNext;", image_pnext_body)
        self.assertIn("validate_image_create_pnext_for_transport(info)", image_validate_body)
        self.assertIn("if (pnext_rc != VK_SUCCESS) return pnext_rc;", image_validate_body)
        self.assertIn("vkGetPhysicalDeviceImageFormatProperties", image_validate_body)
        self.assertIn("pdocker_vk_sample_count_value(info->samples) == 0", image_validate_body)
        self.assertIn("(info->samples & props.sampleCounts) == 0", image_validate_body)
        self.assertIn("return VK_ERROR_FORMAT_NOT_SUPPORTED;", image_validate_body)
        create_image_body = icd.split("VKAPI_ATTR VkResult VKAPI_CALL vkCreateImage", 1)[1].split(
            "VKAPI_ATTR void VKAPI_CALL vkDestroyImage", 1
        )[0]
        self.assertIn("validate_image_create_info_for_transport(pCreateInfo)", create_image_body)
        self.assertNotIn("if (!pdocker_vk_format_bridge_supported(pCreateInfo->format))", create_image_body)
        image_view_pnext_body = icd.split("static VkResult validate_image_view_pnext_for_transport", 1)[1].split(
            "static VkResult validate_image_view_create_info_for_transport", 1
        )[0]
        self.assertIn("VK_STRUCTURE_TYPE_IMAGE_VIEW_USAGE_CREATE_INFO", image_view_pnext_body)
        self.assertIn("usage_info->usage == 0", image_view_pnext_body)
        self.assertIn("(usage_info->usage & ~image->usage) != 0", image_view_pnext_body)
        self.assertIn('trace_icd_runtime_failure("image-view-usage-pnext-unsupported"', image_view_pnext_body)
        self.assertIn('unsupported_image_pnext_result("vkCreateImageView", node)', image_view_pnext_body)
        image_view_validate_body = icd.split("static VkResult validate_image_view_create_info_for_transport", 1)[1].split(
            "static VkResult validate_sampler_create_info_for_transport", 1
        )[0]
        self.assertIn("if (info->flags != 0) return VK_ERROR_FEATURE_NOT_PRESENT;", image_view_validate_body)
        self.assertIn("PdockerVkImage *image = pdocker_vk_image_from_handle(info->image);", image_view_validate_body)
        self.assertIn("validate_image_view_pnext_for_transport(info, image)", image_view_validate_body)
        self.assertIn("if (pnext_rc != VK_SUCCESS) return pnext_rc;", image_view_validate_body)
        self.assertIn("info->format != image->format", image_view_validate_body)
        self.assertIn("normalize_image_view_subresource_range_for_transport", image_view_validate_body)
        normalize_view_body = icd.split("static bool normalize_image_view_subresource_range_for_transport", 1)[1].split(
            "static VkResult validate_image_view_create_info_for_transport", 1
        )[0]
        self.assertIn("pdocker_vk_image_aspect_mask_valid_for_format", normalize_view_body)
        self.assertIn("VK_REMAINING_MIP_LEVELS", normalize_view_body)
        self.assertIn("VK_REMAINING_ARRAY_LAYERS", normalize_view_body)
        create_view_body = icd.split("VKAPI_ATTR VkResult VKAPI_CALL vkCreateImageView", 1)[1].split(
            "VKAPI_ATTR void VKAPI_CALL vkDestroyImageView", 1
        )[0]
        self.assertIn("VkImageSubresourceRange normalized_range;", create_view_body)
        self.assertIn("view->subresource_range = normalized_range;", create_view_body)
        sampler_validate_body = icd.split("static VkResult validate_sampler_create_info_for_transport", 1)[1].split(
            "VKAPI_ATTR VkResult VKAPI_CALL vkCreateImage", 1
        )[0]
        self.assertIn("VK_STRUCTURE_TYPE_SAMPLER_REDUCTION_MODE_CREATE_INFO", sampler_validate_body)
        self.assertIn("VK_SAMPLER_REDUCTION_MODE_WEIGHTED_AVERAGE", sampler_validate_body)
        self.assertIn("sampler-reduction-mode-unsupported", sampler_validate_body)
        self.assertIn('unsupported_image_pnext_result("vkCreateSampler", node)', sampler_validate_body)
        self.assertIn("node = header.pNext;", sampler_validate_body)
        self.assertIn("if (info->flags != 0) return VK_ERROR_FEATURE_NOT_PRESENT;", sampler_validate_body)
        self.assertIn("return VK_ERROR_FORMAT_NOT_SUPPORTED;", icd)
        self.assertIn("uint32_t bytes_per_pixel = conservative_format_bytes_per_pixel(info->format);", icd)
        self.assertIn("if (bytes_per_pixel == 0) return 0;", icd)
        self.assertIn("image->requirements_size = requirements_size;", icd)
        self.assertIn("entry->initial_layout = image->current_layout;", icd)
        self.assertNotIn("entry->initial_layout = image->initial_layout;", icd)
        self.assertIn("image_entries[i].initial_layout = image->current_layout;", icd)
        self.assertNotIn("image_entries[i].initial_layout = image->initial_layout;", icd)
        self.assertIn("collect_v5_image_layout_range_entries", icd)
        self.assertIn("need_v52_image_layout_ranges", icd)
        self.assertIn("PDOCKER_GPU_VULKAN_DISPATCH_V52_ABI_MINOR", icd)
        self.assertNotIn("mixed subresource layouts require per-subresource layout ABI", icd)
        executor = GPU_EXECUTOR.read_text()
        self.assertIn("vulkan_image_create_initial_layout_for_transport", executor)
        materialize = executor.split("static int materialize_vulkan_dispatch_images", 1)[1].split("static int run_vulkan_dispatch_fd", 1)[0]
        self.assertIn("create_initial_layout =", materialize)
        self.assertIn("dst->current_layout = vulkan_replay_layout_for_executor(create_initial_layout);", materialize)
        self.assertIn(".initialLayout = create_initial_layout", materialize)
        self.assertNotIn("src->initial_layout != VK_IMAGE_LAYOUT_UNDEFINED", materialize)
        self.assertNotIn("src->initial_layout == VK_IMAGE_LAYOUT_PREINITIALIZED", materialize)
        self.assertIn("vkBindImageMemory(device,", icd)
        self.assertIn("img->memory = mem;", icd)
        self.assertIn("view->image = image;", icd)
        self.assertIn("sampler->mag_filter = pCreateInfo->magFilter;", icd)
        self.assertIn("if (!binding->buffer && !binding->image_view && !binding->sampler) continue;", icd)
        self.assertIn("set->has_image_descriptor = descriptor_set_has_image_descriptor(set);", icd)
        self.assertIn("descriptor_array_transport_required || image_descriptor_count > 0", icd)
        self.assertIn("V5.1 frame required but disabled for this dispatch", icd)
        self.assertIn("because PDOCKER_VULKAN_ALIAS_COPIES is active", icd)


    def test_vulkan_graphics_msaa_materialization_is_masked_to_resolved_color_attachments(self):
        executor = GPU_EXECUTOR.read_text()
        generic_helper = executor.split(
            "static int validate_vulkan_dispatch_v5_image_samples_for_generic_dispatch", 1
        )[1].split("static int materialize_vulkan_dispatch_images", 1)[0]
        self.assertIn("object_tables->images[i].samples != VK_SAMPLE_COUNT_1_BIT", generic_helper)
        self.assertIn("return -EOPNOTSUPP;", generic_helper)
        run_body = executor.split("static int run_vulkan_dispatch_fd", 1)[1].split(
            "static void clear_registered_vector_buffer", 1
        )[0]
        self.assertIn("validate_vulkan_dispatch_v5_image_samples_for_generic_dispatch(object_tables)", run_body)
        self.assertLess(
            run_body.index("validate_vulkan_dispatch_v5_image_samples_for_generic_dispatch(object_tables)"),
            run_body.index("materialize_vulkan_dispatch_images("),
        )

        graphics_helper = executor.split(
            "static int validate_vulkan_graphics_v6_msaa_images_are_v64_color_resolves", 1
        )[1].split("static int materialize_vulkan_graphics_v6_attachments", 1)[0]
        self.assertIn("unsigned char *allowed_msaa_images", graphics_helper)
        self.assertIn("header->image_count > allowed_msaa_image_count", graphics_helper)
        self.assertIn("PDOCKER_GPU_GRAPHICS_V6_COMMAND_BEGIN_RENDERING", graphics_helper)
        self.assertIn("PDOCKER_GPU_GRAPHICS_V6_ATTACHMENT_UNUSED_SLOT", graphics_helper)
        self.assertIn("command->attachment_first > header->attachment_count", graphics_helper)
        self.assertIn("attachment->attachment_role != PDOCKER_GPU_GRAPHICS_V6_ATTACHMENT_COLOR", graphics_helper)
        self.assertIn("src_image->samples != attachment->samples", graphics_helper)
        self.assertIn("attachment->resolve_image_view_index == PDOCKER_GPU_V5_DESCRIPTOR_OBJECT_NONE", graphics_helper)
        self.assertIn("find_vulkan_graphics_v64_resolve_attachment(view, attachment_index)", graphics_helper)
        self.assertIn("resolve_meta->resolve_mode == VK_RESOLVE_MODE_NONE", graphics_helper)
        self.assertIn("resolve_image->samples != VK_SAMPLE_COUNT_1_BIT", graphics_helper)
        self.assertIn("vulkan_graphics_v614_resolve_runtime_eligible(", graphics_helper)
        self.assertIn("allowed_msaa_images[resolve->src_image_index] = 1;", graphics_helper)
        self.assertLess(
            graphics_helper.index("attachment->resolve_image_view_index == PDOCKER_GPU_V5_DESCRIPTOR_OBJECT_NONE"),
            graphics_helper.index("allowed_msaa_images[resolve->src_image_index] = 1;"),
        )
        self.assertIn("src_image->format != resolve_image->format", graphics_helper)
        self.assertIn("src_view->format != resolve_view->format", graphics_helper)
        self.assertIn("src_view->aspect_mask != VK_IMAGE_ASPECT_COLOR_BIT", graphics_helper)
        self.assertIn("resolve_view->aspect_mask != VK_IMAGE_ASPECT_COLOR_BIT", graphics_helper)
        self.assertIn("allowed_msaa_images[src_view->image_index] = 1;", graphics_helper)
        self.assertIn("view->images[i].samples != VK_SAMPLE_COUNT_1_BIT && !allowed_msaa_images[i]", graphics_helper)

        graphics_body = executor.split("static int materialize_vulkan_graphics_v6_attachments", 1)[1].split(
            "static int record_vulkan_graphics_v6_attachment_writeback_commands", 1
        )[0]
        self.assertIn("unsigned char msaa_image_allowed[PDOCKER_GPU_MAX_VULKAN_BINDINGS];", graphics_body)
        self.assertIn("validate_vulkan_graphics_v6_msaa_images_are_v64_color_resolves(", graphics_body)
        self.assertIn("msaa_image_allowed,", graphics_body)
        self.assertIn("attachment->samples != VK_SAMPLE_COUNT_1_BIT", graphics_body)
        self.assertIn("return -EOPNOTSUPP;", graphics_body)
        self.assertLess(
            graphics_body.index("attachment->samples != VK_SAMPLE_COUNT_1_BIT"),
            graphics_body.index("writeback_image->writeback_needed = 1;"),
        )
        self.assertIn("writeback_image = resolve_image;", graphics_body)
        self.assertLess(
            graphics_body.index("writeback_image = resolve_image;"),
            graphics_body.index("writeback_image->writeback_needed = 1;"),
        )
        writeback_record_body = executor.split("static int record_vulkan_graphics_v6_attachment_writeback_commands", 1)[1].split(
            "static int writeback_vulkan_graphics_v6_attachments", 1
        )[0]
        self.assertIn("if (image->samples != VK_SAMPLE_COUNT_1_BIT) return -EOPNOTSUPP;", writeback_record_body)
        self.assertLess(
            writeback_record_body.index("if (image->samples != VK_SAMPLE_COUNT_1_BIT) return -EOPNOTSUPP;"),
            writeback_record_body.index("vkCmdCopyImageToBuffer"),
        )
        writeback_body = executor.split("static int writeback_vulkan_graphics_v6_attachments", 1)[1].split(
            "typedef struct VulkanGraphicsReplayBuffer", 1
        )[0]
        self.assertIn("if (image->samples != VK_SAMPLE_COUNT_1_BIT) return -EOPNOTSUPP;", writeback_body)
        self.assertLess(
            writeback_body.index("if (image->samples != VK_SAMPLE_COUNT_1_BIT) return -EOPNOTSUPP;"),
            writeback_body.index("if (image->requires_staging)"),
        )
        self.assertLess(
            writeback_body.index("if (image->samples != VK_SAMPLE_COUNT_1_BIT) return -EOPNOTSUPP;"),
            writeback_body.index("write_fd_exact"),
        )
        generic_body = executor.split("failed to materialize V5.1 image objects", 1)[0].rsplit(
            "materialize_vulkan_dispatch_images(", 1
        )[1]
        self.assertIn("NULL,", generic_body)
        self.assertIn("0,", generic_body)

    def test_vulkan_executor_has_storage_image_roundtrip_probe(self):
        src = GPU_EXECUTOR.read_text()
        self.assertIn("kStorageImageRoundtripSpv", src)
        self.assertIn("bench_vulkan_storage_image_roundtrip", src)
        self.assertIn('"--bench-vulkan-storage-image-roundtrip"', src)
        for marker in [
            "VK_DESCRIPTOR_TYPE_STORAGE_IMAGE",
            "VK_IMAGE_USAGE_STORAGE_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT",
            "VK_FORMAT_R8G8B8A8_UNORM",
            "VK_IMAGE_LAYOUT_GENERAL",
            "VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL",
            "vkCmdCopyImageToBuffer",
            "VK_BUFFER_USAGE_TRANSFER_DST_BIT",
            "find_vulkan_memory_type_any",
            'storage_image_roundtrip',
            'direct-vulkan-storage-image-roundtrip',
        ]:
            self.assertIn(marker, src)

    def test_vulkan_icd_storage_image_smoke_exercises_object_transport(self):
        script = (ROOT / "scripts/test/smoke-vulkan-icd-storage-image.sh").read_text()
        wrapper = (ROOT / "scripts/smoke-vulkan-icd-storage-image.sh").read_text()
        self.assertIn('exec "$ROOT/scripts/test/smoke-vulkan-icd-storage-image.sh" "$@"', wrapper)
        for marker in [
            "kStorageImageRoundtripSpv",
            "VK_DESCRIPTOR_TYPE_STORAGE_IMAGE",
            "VK_IMAGE_USAGE_STORAGE_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT",
            "VK_FORMAT_R8G8B8A8_UNORM",
            "VK_IMAGE_LAYOUT_GENERAL",
            "VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL",
            "vkCmdCopyImageToBuffer",
            "VK_BUFFER_USAGE_TRANSFER_DST_BIT",
            "storageImageMaxErr",
            "PDOCKER_GPU_QUEUE_SOCKET",
            "EXTERNAL_SOCK",
            "external PDOCKER_GPU_QUEUE_SOCKET is not a socket",
            "VK_ICD_FILENAMES",
            "--bench-vulkan-storage-image-roundtrip",
        ]:
            self.assertIn(marker, script)

    def test_vulkan_icd_device_socket_gate_documents_non_host_evidence(self):
        doc = (ROOT / "docs" / "test" / "VULKAN_ICD_DEVICE_SOCKET_GATE.md").read_text()
        readme = (ROOT / "docs" / "test" / "README.md").read_text()
        scripts_readme = (ROOT / "scripts" / "test" / "README.md").read_text()
        for marker in [
            "glibc Vulkan loader in a guest/container",
            "/etc/vulkan/icd.d/pdocker-android.json",
            "PDOCKER_GPU_QUEUE_SOCKET=/run/pdocker-gpu/pdocker-gpu.sock",
            "files/pdocker-runtime/gpu/pdocker-gpu.sock",
            "libvulkan.so.1",
            "storageImageMaxErr",
            "backend_impl\":\"android_vulkan",
            "success:false",
        ]:
            self.assertIn(marker, doc)
        self.assertIn("host `-lvulkan`", doc)
        self.assertIn("must not be treated as real-device evidence", doc)
        self.assertIn("VULKAN_ICD_DEVICE_SOCKET_GATE.md", readme)
        self.assertIn("host-side ICD/object-transport", scripts_readme)

    def test_vulkan_icd_device_socket_runner_is_guest_scoped(self):
        script = (ROOT / "scripts/test/android-vulkan-icd-device-socket-smoke.sh").read_text()
        doc = (ROOT / "docs/test/VULKAN_ICD_DEVICE_SOCKET_GATE.md").read_text()
        for marker in [
            '"schema": "skydnir.vulkan.icd.device-socket.v1"',
            '"uses_host_vulkan_loader": False',
            "VK_ICD_FILENAMES=/etc/vulkan/icd.d/pdocker-android.json",
            "PDOCKER_GPU_QUEUE_SOCKET=/run/pdocker-gpu/pdocker-gpu.sock",
            "docker cp pdocker/tmp/vulkan-icd-device-socket/client.c",
            "docker cp pdocker/tmp/vulkan-icd-device-socket/pdocker-vulkan-icd.so",
            "test -S /run/pdocker-gpu/pdocker-gpu.sock",
            "cc /tmp/skydnir-vk-storage-image-smoke.c",
            "grep -q 'storageImageMaxErr='",
            "grep -q 'pdocker-vulkan-icd'",
            "guest lacks cc/vulkan headers/libvulkan/socket prerequisites",
        ]:
            self.assertIn(marker, script)
        self.assertIn("scripts/test/android-vulkan-icd-device-socket-smoke.sh", doc)
        self.assertIn("non-promoting `success:false` artifact", doc)

    def test_vulkan_icd_device_socket_artifact_verifier_is_strict(self):
        verifier = (ROOT / "scripts/test/verify-vulkan-icd-device-socket-artifact.py").read_text()
        doc = (ROOT / "docs/test/VULKAN_ICD_DEVICE_SOCKET_GATE.md").read_text()
        for marker in [
            'SCHEMA = "skydnir.vulkan.icd.device-socket.v1"',
            'uses_host_vulkan_loader") is False',
            "storageImageMaxErr=([0-9]+)",
            "pdocker-vulkan-icd",
            "fallback evidence is not accepted",
            "--allow-planned-skip",
            "verify_planned_skip",
        ]:
            self.assertIn(marker, verifier)
        self.assertIn("verify-vulkan-icd-device-socket-artifact.py", doc)
        self.assertIn("never promotes Vulkan passthrough", doc)

    def test_vulkan_memory_api_validates_map_ranges_and_type_index(self):
        icd = VULKAN_ICD.read_text()
        allocate_body = icd.split("VKAPI_ATTR VkResult VKAPI_CALL vkAllocateMemory", 1)[1].split("VKAPI_ATTR void VKAPI_CALL vkFreeMemory", 1)[0]
        map_body = icd.split("VKAPI_ATTR VkResult VKAPI_CALL vkMapMemory", 1)[1].split("VKAPI_ATTR void VKAPI_CALL vkUnmapMemory", 1)[0]
        self.assertIn("if (pAllocateInfo->memoryTypeIndex >= 2) return VK_ERROR_FEATURE_NOT_PRESENT;", allocate_body)
        self.assertIn("if (size != VK_WHOLE_SIZE)", map_body)
        self.assertIn("size > (VkDeviceSize)m->size - offset", map_body)
        self.assertIn("return VK_ERROR_MEMORY_MAP_FAILED;", map_body)
        self.assertNotIn("(void)size;", map_body)

    def test_vulkan_icd_advertises_conservative_image_format_properties(self):
        icd = VULKAN_ICD.read_text()
        for marker in [
            "pdocker_vk_format_bridge_supported",
            "pdocker_vk_format_image_features",
            "pdocker_vk_image_usage_required_features",
            "pdocker_vk_image_usage_supported_by_format",
            "pdocker_vk_image_max_mip_levels",
            "pdocker_vk_sample_count_value",
            "pFormatProperties->linearTilingFeatures = 0;",
            "pFormatProperties->optimalTilingFeatures = pdocker_vk_advertised_image_features(format);",
            "VK_FORMAT_FEATURE_TRANSFER_SRC_BIT",
            "VK_FORMAT_FEATURE_TRANSFER_DST_BIT",
            "VK_FORMAT_FEATURE_SAMPLED_IMAGE_BIT",
            "VK_FORMAT_FEATURE_STORAGE_IMAGE_BIT",
            "VK_FORMAT_FEATURE_COLOR_ATTACHMENT_BIT",
            "VK_FORMAT_FEATURE_DEPTH_STENCIL_ATTACHMENT_BIT",
            "if (tiling != VK_IMAGE_TILING_OPTIMAL) return VK_ERROR_FORMAT_NOT_SUPPORTED;",
            "VK_IMAGE_CREATE_SPARSE_BINDING_BIT",
            "flags & ~supported_flags",
            "VK_SAMPLE_COUNT_1_BIT",
            "maxImageDimension2D = 4096",
            "maxImageDimension3D = 256",
            "maxImageArrayLayers = 256",
            "PDOCKER_VK_SUPPORTED_SAMPLE_COUNTS",
            "pdocker_vk_advertised_sample_counts",
            "pdocker_vk_image_sample_counts_for_request",
            "pdocker_vk_advertised_color_attachment_sample_counts",
            "maxSampleMaskWords = 1",
            "vulkan_min_resource_size",
            "max_resource < vulkan_min_resource_size",
            "vkGetPhysicalDeviceSparseImageFormatProperties",
            "*pPropertyCount = 0;",
        ]:
            self.assertIn(marker, icd)
        format_props = icd.split("VKAPI_ATTR void VKAPI_CALL vkGetPhysicalDeviceFormatProperties", 1)[1].split("VKAPI_ATTR VkResult VKAPI_CALL vkGetPhysicalDeviceImageFormatProperties", 1)[0]
        image_props = icd.split("VKAPI_ATTR VkResult VKAPI_CALL vkGetPhysicalDeviceImageFormatProperties", 1)[1].split("VKAPI_ATTR void VKAPI_CALL vkGetPhysicalDeviceSparseImageFormatProperties", 1)[0]
        self.assertNotIn("(void)format;", format_props)
        self.assertNotIn("return VK_ERROR_FORMAT_NOT_SUPPORTED;\n}", image_props)
        self.assertNotIn("VK_FORMAT_FEATURE_UNIFORM_TEXEL_BUFFER_BIT", format_props)
        self.assertNotIn("VK_FORMAT_FEATURE_STORAGE_TEXEL_BUFFER_BIT", format_props)
        self.assertNotIn("VK_FORMAT_FEATURE_BLIT_SRC_BIT", format_props)
        self.assertNotIn("VK_FORMAT_FEATURE_BLIT_DST_BIT", format_props)
        self.assertNotIn("VK_FORMAT_FEATURE_SAMPLED_IMAGE_FILTER_LINEAR_BIT", format_props)
        self.assertIn("VK_FORMAT_D32_SFLOAT", icd)
        self.assertIn("VK_FORMAT_D32_SFLOAT_S8_UINT", icd)
        self.assertNotIn("case VK_FORMAT_D16_UNORM_S8_UINT:\n            return true;", icd)
        self.assertIn("Unknown/block-compressed/vendor formats are not byte-linear", icd)
        self.assertIn("return VK_ERROR_FORMAT_NOT_SUPPORTED;", image_props)
        self.assertIn("pdocker_vk_image_usage_supported_by_format(format, usage)", image_props)
        usage_body = icd.split("static VkFormatFeatureFlags pdocker_vk_image_usage_required_features", 1)[1].split(
            "static uint32_t pdocker_vk_image_max_mip_levels", 1
        )[0]
        self.assertIn("VK_IMAGE_USAGE_STORAGE_BIT", usage_body)
        self.assertIn("VK_FORMAT_FEATURE_STORAGE_IMAGE_BIT", usage_body)
        self.assertIn("VK_IMAGE_USAGE_INPUT_ATTACHMENT_BIT", usage_body)
        self.assertIn("return required != 0 && (advertised & required) == required;", usage_body)
        sample_count_body = icd.split("static uint32_t pdocker_vk_sample_count_value", 1)[1].split(
            "static VkDeviceSize estimate_image_requirement_size", 1
        )[0]
        for sample in ["1", "2", "4", "8", "16", "32", "64"]:
            self.assertIn(f"return {sample}u;", sample_count_body)
        align_body = icd.split("static bool align_device_size_checked", 1)[1].split(
            "static VkDeviceSize align_device_size", 1
        )[0]
        estimate_body = icd.split("static VkDeviceSize estimate_image_requirement_size", 1)[1].split(
            "static VkDeviceSize pdocker_vulkan_max_buffer_size", 1
        )[0]
        self.assertIn("if (value > UINT64_MAX - add) return false;", align_body)
        self.assertIn("const uint32_t sample_count = pdocker_vk_sample_count_value(info->samples);", estimate_body)
        self.assertIn("if (sample_count == 0) return 0;", estimate_body)
        self.assertIn("checked_mul_u64(pixels, sample_count, &pixels)", estimate_body)
        self.assertIn("align_device_size_checked((VkDeviceSize)pixels", estimate_body)
        validate_image_body = icd.split("static VkResult validate_image_create_info_for_transport", 1)[1].split(
            "static VkResult validate_image_view_create_info_for_transport", 1
        )[0]
        self.assertIn("pdocker_vk_sample_count_value(info->samples) == 0", validate_image_body)
        self.assertIn("(info->samples & props.sampleCounts) == 0", validate_image_body)
        self.assertIn("info->extent.width > props.maxExtent.width", validate_image_body)
        self.assertIn("info->mipLevels > props.maxMipLevels", validate_image_body)
        self.assertIn("info->arrayLayers > props.maxArrayLayers", validate_image_body)
        self.assertIn("requirements_size == 0 || requirements_size > props.maxResourceSize", validate_image_body)
        self.assertIn("VkSampleCountFlags sample_counts = pdocker_vk_image_sample_counts_for_request(", image_props)
        self.assertIn("if (!sample_counts) return VK_ERROR_FORMAT_NOT_SUPPORTED;", image_props)
        self.assertIn("pImageFormatProperties->sampleCounts = sample_counts;", image_props)
        physical_props_body = icd.split("static void fill_physical_device_properties", 1)[1].split(
            "static VkSubgroupFeatureFlags advertised_subgroup_operations", 1
        )[0]
        self.assertIn("framebufferColorSampleCounts =", physical_props_body)
        self.assertIn("pdocker_vk_advertised_color_attachment_sample_counts()", physical_props_body)
        self.assertIn("framebufferDepthSampleCounts = VK_SAMPLE_COUNT_1_BIT", physical_props_body)
        self.assertIn("sampledImageColorSampleCounts = VK_SAMPLE_COUNT_1_BIT", physical_props_body)
        self.assertIn("storageImageSampleCounts = VK_SAMPLE_COUNT_1_BIT", physical_props_body)
        legacy_features = icd.split("static VkFormatFeatureFlags pdocker_vk_format_image_features", 1)[1].split(
            "static bool pdocker_vk_resolve_image_executor_eligible", 1
        )[0]
        self.assertIn("features &= ~(VK_FORMAT_FEATURE_BLIT_SRC_BIT", legacy_features)

    def test_vulkan_format_properties2_queries_reuse_legacy_caps(self):
        icd = VULKAN_ICD.read_text()
        format2_body = c_function_body(icd, "vkGetPhysicalDeviceFormatProperties2")
        pnext_body = c_function_body(icd, "fill_format_properties2_pnext")
        proc_body = icd.split("static PFN_vkVoidFunction proc_address", 1)[1].split(
            "VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL vkGetInstanceProcAddr", 1
        )[0]

        self.assertIn("zero_vk_out_struct_preserve_chain(pFormatProperties", format2_body)
        self.assertIn("vkGetPhysicalDeviceFormatProperties(physicalDevice, format, &pFormatProperties->formatProperties);", format2_body)
        self.assertIn("fill_format_properties2_pnext((void *)header.pNext, &pFormatProperties->formatProperties);", format2_body)
        self.assertIn("VK_STRUCTURE_TYPE_FORMAT_PROPERTIES_3", pnext_body)
        self.assertIn("p->linearTilingFeatures = legacy->linearTilingFeatures;", pnext_body)
        self.assertIn("p->optimalTilingFeatures = legacy->optimalTilingFeatures;", pnext_body)
        self.assertIn("p->bufferFeatures = legacy->bufferFeatures;", pnext_body)
        self.assertIn("VK_STRUCTURE_TYPE_DRM_FORMAT_MODIFIER_PROPERTIES_LIST_EXT", pnext_body)
        self.assertIn("VK_STRUCTURE_TYPE_DRM_FORMAT_MODIFIER_PROPERTIES_LIST_2_EXT", pnext_body)
        self.assertIn("p->drmFormatModifierCount = 0;", pnext_body)
        self.assertNotIn("p->pDrmFormatModifierProperties = NULL", pnext_body)
        self.assertIn("MAP_PROC(vkGetPhysicalDeviceFormatProperties2)", proc_body)
        self.assertIn('MAP_ALIAS("vkGetPhysicalDeviceFormatProperties2KHR", vkGetPhysicalDeviceFormatProperties2)', proc_body)

    def test_vulkan_image_format_properties2_queries_reuse_legacy_caps(self):
        icd = VULKAN_ICD.read_text()
        image2_body = c_function_body(icd, "vkGetPhysicalDeviceImageFormatProperties2")
        input_pnext_body = c_function_body(icd, "image_format_info2_has_unsupported_pnext")
        output_pnext_body = c_function_body(icd, "fill_image_format_properties2_pnext")
        proc_body = icd.split("static PFN_vkVoidFunction proc_address", 1)[1].split(
            "VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL vkGetInstanceProcAddr", 1
        )[0]

        self.assertIn("zero_vk_out_struct_preserve_chain(pImageFormatProperties", image2_body)
        self.assertIn("image_format_info2_has_unsupported_pnext(pImageFormatInfo->pNext)", image2_body)
        self.assertIn("return VK_ERROR_FORMAT_NOT_SUPPORTED;", image2_body)
        self.assertIn("vkGetPhysicalDeviceImageFormatProperties(", image2_body)
        for field in [
            "pImageFormatInfo->format",
            "pImageFormatInfo->type",
            "pImageFormatInfo->tiling",
            "pImageFormatInfo->usage",
            "pImageFormatInfo->flags",
            "&pImageFormatProperties->imageFormatProperties",
        ]:
            self.assertIn(field, image2_body)
        self.assertIn("fill_image_format_properties2_pnext((void *)header.pNext);", image2_body)
        self.assertIn("trace_icd_runtime_failure", input_pnext_body)
        self.assertIn("VK_ERROR_FORMAT_NOT_SUPPORTED", input_pnext_body)
        self.assertIn("VK_STRUCTURE_TYPE_SAMPLER_YCBCR_CONVERSION_IMAGE_FORMAT_PROPERTIES", output_pnext_body)
        self.assertIn("combinedImageSamplerDescriptorCount = 1", output_pnext_body)
        self.assertIn("VK_STRUCTURE_TYPE_EXTERNAL_IMAGE_FORMAT_PROPERTIES", output_pnext_body)
        self.assertIn("VK_STRUCTURE_TYPE_FILTER_CUBIC_IMAGE_VIEW_IMAGE_FORMAT_PROPERTIES_EXT", output_pnext_body)
        self.assertIn("MAP_PROC(vkGetPhysicalDeviceImageFormatProperties2)", proc_body)
        self.assertIn('MAP_ALIAS("vkGetPhysicalDeviceImageFormatProperties2KHR", vkGetPhysicalDeviceImageFormatProperties2)', proc_body)

    def test_vulkan_icd_records_buffer_image_copy_commands_before_dispatch(self):
        icd = VULKAN_ICD.read_text()
        self.assertIn("PdockerVkImageCopyOp image_copy_ops[PDOCKER_VK_MAX_COPY_OPS];", icd)
        self.assertIn("PDOCKER_VK_COMMAND_IMAGE_COPY", icd)
        self.assertIn("vkCmdCopyBufferToImage", icd)
        self.assertIn("vkCmdCopyImageToBuffer", icd)
        self.assertIn("record_image_copy_op", icd)
        self.assertIn("execute_recorded_image_copy_op", icd)
        self.assertIn("image_tight_subresource_offset", icd)
        self.assertIn("image_ptr(op->image", icd)
        self.assertIn("case PDOCKER_VK_COMMAND_IMAGE_COPY:", icd)
        self.assertIn("MAP_PROC(vkCmdCopyBufferToImage);", icd)
        self.assertIn("MAP_PROC(vkCmdCopyImageToBuffer);", icd)
        self.assertIn("pdocker_vk_image_single_aspect_supported_for_format", icd)
        self.assertIn("copy__->image->format, copy__->region.imageSubresource.aspectMask", icd)
        self.assertIn("conservative_format_bytes_per_pixel_for_aspect", icd)
        self.assertIn("if ((aspect_mask & (aspect_mask - 1u)) != 0) return false;", icd)
        self.assertIn("VK_FORMAT_D24_UNORM_S8_UINT", icd)
        self.assertIn("VK_FORMAT_D32_SFLOAT_S8_UINT", icd)

    def test_vulkan_icd_records_image_to_image_copy_commands(self):
        icd = VULKAN_ICD.read_text()
        self.assertIn("PdockerVkImageToImageCopyOp image_to_image_copy_ops[PDOCKER_VK_MAX_COPY_OPS];", icd)
        self.assertIn("PDOCKER_VK_COMMAND_IMAGE_TO_IMAGE_COPY", icd)
        self.assertIn("vkCmdCopyImage", icd)
        self.assertIn("record_image_to_image_copy_op", icd)
        self.assertIn("execute_recorded_image_to_image_copy_op", icd)
        self.assertIn("case PDOCKER_VK_COMMAND_IMAGE_TO_IMAGE_COPY:", icd)
        self.assertIn("image_ptr(op->src", icd)
        self.assertIn("image_ptr(op->dst", icd)
        self.assertIn("MAP_PROC(vkCmdCopyImage);", icd)
        self.assertIn("copy__->src->format, copy__->region.srcSubresource.aspectMask", icd)
        self.assertIn("copy__->dst->format, copy__->region.dstSubresource.aspectMask", icd)
        self.assertIn("copy__->region.srcSubresource.aspectMask != copy__->region.dstSubresource.aspectMask", icd)

    def test_vulkan_icd_exposes_tight_image_subresource_layout(self):
        icd = VULKAN_ICD.read_text()
        self.assertIn("vkGetImageSubresourceLayout", icd)
        self.assertIn("VkSubresourceLayout *pLayout", icd)
        self.assertIn("image_tight_subresource_offset(img", icd)
        self.assertIn("image_tight_mip_size(img", icd)
        self.assertIn("image_tight_layer_stride(img", icd)
        self.assertIn("pLayout->rowPitch", icd)
        self.assertIn("pLayout->depthPitch", icd)
        self.assertIn("MAP_PROC(vkGetImageSubresourceLayout);", icd)

    def test_vulkan_icd_records_clear_color_image_commands(self):
        icd = VULKAN_ICD.read_text()
        self.assertIn("PdockerVkImageClearOp image_clear_ops[PDOCKER_VK_MAX_COPY_OPS];", icd)
        self.assertIn("PDOCKER_VK_COMMAND_CLEAR_COLOR_IMAGE", icd)
        self.assertIn("vkCmdClearColorImage", icd)
        self.assertIn("record_clear_color_image_op", icd)
        self.assertIn("execute_recorded_clear_color_image_op", icd)
        self.assertIn("resolve_image_subresource_range", icd)
        self.assertIn("encode_clear_color_pixel", icd)
        self.assertIn("case PDOCKER_VK_COMMAND_CLEAR_COLOR_IMAGE:", icd)
        self.assertIn("MAP_PROC(vkCmdClearColorImage);", icd)

    def test_vulkan_icd_records_resolve_image_commands(self):
        icd = VULKAN_ICD.read_text()
        self.assertIn("PdockerVkImageResolveOp image_resolve_ops[PDOCKER_VK_MAX_COPY_OPS];", icd)
        self.assertIn("PDOCKER_VK_COMMAND_RESOLVE_IMAGE", icd)
        self.assertIn("vkCmdResolveImage", icd)
        self.assertIn("record_resolve_image_op", icd)
        self.assertIn("execute_recorded_resolve_image_op", icd)
        self.assertIn("pdocker_vk_resolve_image_executor_eligible", icd)
        self.assertIn("pdocker_vk_resolve_image_host_fallback_eligible", icd)
        self.assertIn("command_op_requires_graphics_executor_frame", icd)
        self.assertIn("case PDOCKER_VK_COMMAND_RESOLVE_IMAGE:", icd)
        self.assertIn("MAP_PROC(vkCmdResolveImage);", icd)

    def test_vulkan_icd_records_blit_image_commands(self):
        icd = VULKAN_ICD.read_text()
        self.assertIn("PdockerVkImageBlitOp image_blit_ops[PDOCKER_VK_MAX_COPY_OPS];", icd)
        self.assertIn("PDOCKER_VK_COMMAND_BLIT_IMAGE", icd)
        self.assertIn("vkCmdBlitImage", icd)
        self.assertIn("record_blit_image_op", icd)
        self.assertIn("execute_recorded_blit_image_op", icd)
        self.assertIn("pdocker_vk_blit_image_executor_eligible", icd)
        self.assertIn("command_op_requires_graphics_executor_frame", icd)
        self.assertIn("blit_axis_sample", icd)
        self.assertIn("case PDOCKER_VK_COMMAND_BLIT_IMAGE:", icd)
        self.assertIn("MAP_PROC(vkCmdBlitImage);", icd)

    def test_vulkan_icd_records_clear_depth_stencil_image_commands(self):
        icd = VULKAN_ICD.read_text()
        self.assertIn("PdockerVkDepthStencilClearOp depth_stencil_clear_ops[PDOCKER_VK_MAX_COPY_OPS];", icd)
        self.assertIn("PDOCKER_VK_COMMAND_CLEAR_DEPTH_STENCIL_IMAGE", icd)
        self.assertIn("vkCmdClearDepthStencilImage", icd)
        self.assertIn("record_clear_depth_stencil_image_op", icd)
        self.assertIn("execute_recorded_clear_depth_stencil_image_op", icd)
        self.assertIn("encode_clear_depth_stencil_pixel", icd)
        self.assertIn("case PDOCKER_VK_COMMAND_CLEAR_DEPTH_STENCIL_IMAGE:", icd)
        self.assertIn("MAP_PROC(vkCmdClearDepthStencilImage);", icd)

    def test_vulkan_icd_maps_copy_commands2_to_existing_transfer_paths(self):
        icd = VULKAN_ICD.read_text()
        for name in [
            "vkCmdCopyBuffer2",
            "vkCmdCopyImage2",
            "vkCmdCopyBufferToImage2",
            "vkCmdCopyImageToBuffer2",
            "vkCmdBlitImage2",
            "vkCmdResolveImage2",
        ]:
            self.assertIn(name, icd)
            self.assertIn(f"MAP_PROC({name});", icd)
            self.assertIn(f'MAP_ALIAS("{name}KHR", {name});', icd)
        self.assertIn("VK_KHR_COPY_COMMANDS_2_EXTENSION_NAME", icd)
        self.assertIn("ADD_DEVICE_EXTENSION(VK_KHR_COPY_COMMANDS_2_EXTENSION_NAME", icd)
        self.assertIn("VkCopyBufferInfo2", icd)
        self.assertIn("VkBlitImageInfo2", icd)
        self.assertIn("VkResolveImageInfo2", icd)

    def test_vulkan_icd_supports_basic_event_synchronization_api(self):
        icd = VULKAN_ICD.read_text()
        self.assertIn("struct PdockerVkEvent", icd)
        self.assertIn("PDOCKER_VK_COMMAND_EVENT", icd)
        self.assertIn("record_event_command", icd)
        self.assertIn("case PDOCKER_VK_COMMAND_EVENT:", icd)
        for name in [
            "vkCreateEvent",
            "vkDestroyEvent",
            "vkGetEventStatus",
            "vkSetEvent",
            "vkResetEvent",
            "vkCmdSetEvent",
            "vkCmdResetEvent",
            "vkCmdWaitEvents",
        ]:
            self.assertIn(name, icd)
            self.assertIn(f"MAP_PROC({name});", icd)
        host_body = icd.split("static bool command_op_is_host_transfer_or_layout_op", 1)[1].split(
            "static VkResult execute_recorded_host_transfer_or_layout_op", 1
        )[0]
        self.assertIn("case PDOCKER_VK_COMMAND_EVENT:", host_body)
        self.assertIn("case PDOCKER_VK_COMMAND_EVENT_WAIT:", host_body)
        replay_body = icd.split("static VkResult execute_recorded_host_transfer_or_layout_op", 1)[1].split(
            "static bool graphics_mixed_submit_plan", 1
        )[0]
        self.assertIn("case PDOCKER_VK_COMMAND_EVENT:", replay_body)
        self.assertIn("case PDOCKER_VK_COMMAND_EVENT_WAIT:", replay_body)
        self.assertIn("op->event->signaled = op->event_signaled", replay_body)
        self.assertIn("execute_recorded_event_wait_op(op)", replay_body)
        self.assertIn("record_event_wait_command(commandBuffer, pEvents[i],", icd)
        self.assertIn("event-wait-barrier-payload-unsupported", icd)
        self.assertIn("event-wait-unsignaled", icd)

    def test_vulkan_event_commands_are_graphics_v6_replayable(self):
        icd = VULKAN_ICD.read_text()
        executor = GPU_EXECUTOR.read_text()
        abi = APP_HEADER.read_text()
        container_abi = CONTAINER_HEADER.read_text()

        for source in [abi, container_abi]:
            self.assertIn("PDOCKER_GPU_GRAPHICS_V6_COMMAND_SET_EVENT", source)
            self.assertIn("PDOCKER_GPU_GRAPHICS_V6_COMMAND_RESET_EVENT", source)
            self.assertIn("PDOCKER_GPU_GRAPHICS_V6_COMMAND_WAIT_EVENT", source)

        for marker in [
            "VkPipelineStageFlags2 event_src_stage_mask",
            "VkPipelineStageFlags2 event_dst_stage_mask",
            "record.command_type = signaled",
            "PDOCKER_GPU_GRAPHICS_V6_COMMAND_SET_EVENT",
            "PDOCKER_GPU_GRAPHICS_V6_COMMAND_RESET_EVENT",
            "PDOCKER_GPU_GRAPHICS_V6_COMMAND_WAIT_EVENT",
            "command->pipeline_layout_id = op->event->event_id",
            "command->index_offset = (uint64_t)op->event_src_stage_mask",
            "command->push_hash = (uint64_t)op->event_dst_stage_mask",
            "op.event_src_stage_mask = stage_mask",
            "op.event_dst_stage_mask = dst_stage_mask",
            "record.flags = dependency_flags & VK_DEPENDENCY_BY_REGION_BIT",
            "normalize_event_stage_mask((VkPipelineStageFlags2)srcStageMask)",
            "record_event_command(commandBuffer, event, false, stageMask, 0, NULL)",
        ]:
            self.assertIn(marker, icd)
        serializer_event_payload_body = icd.split(
            "record->command_type == PDOCKER_GPU_GRAPHICS_V6_COMMAND_BARRIER ||", 1
        )[1].split(
            "} else if (record->command_type == PDOCKER_GPU_GRAPHICS_V6_COMMAND_CLEAR_ATTACHMENTS)", 1
        )[0]
        for marker in [
            "record->command_type == PDOCKER_GPU_GRAPHICS_V6_COMMAND_SET_EVENT",
            "record->command_type == PDOCKER_GPU_GRAPHICS_V6_COMMAND_WAIT_EVENT",
            "record->memory_barrier_op_count",
            "record->buffer_barrier_op_count",
            "record->image_barrier_op_count",
            "dst->command_index = (uint32_t)command_count",
            "command->pipeline_layout_id = op->event->event_id",
            "op->type != PDOCKER_VK_COMMAND_EVENT_WAIT",
        ]:
            self.assertIn(marker, serializer_event_payload_body)
        reset_serializer_body = icd.split(
            "record->command_type == PDOCKER_GPU_GRAPHICS_V6_COMMAND_RESET_EVENT", 1
        )[1].split(
            "} else if (record->command_type == PDOCKER_GPU_GRAPHICS_V6_COMMAND_BEGIN_QUERY", 1
        )[0]
        self.assertIn("op->type != PDOCKER_VK_COMMAND_EVENT", reset_serializer_body)
        self.assertNotIn("PDOCKER_VK_COMMAND_EVENT_WAIT", reset_serializer_body)
        plan_body = icd.split("static bool graphics_mixed_submit_plan", 1)[1].split(
            "static VkResult execute_graphics_mixed_host_side_ops", 1
        )[0]
        self.assertNotIn("graphics-mixed-event-command-unimplemented", plan_body)

        for marker in [
            "case PDOCKER_GPU_GRAPHICS_V6_COMMAND_SET_EVENT:",
            "case PDOCKER_GPU_GRAPHICS_V6_COMMAND_RESET_EVENT:",
            "case PDOCKER_GPU_GRAPHICS_V6_COMMAND_WAIT_EVENT:",
            "find_executor_event_entry(command->pipeline_layout_id)",
            "PFN_vkCmdSetEvent2 cmd_set_event2",
            "PFN_vkCmdResetEvent2 cmd_reset_event2",
            "PFN_vkCmdWaitEvents2 cmd_wait_events2",
            "rt->cmd_set_event2",
            "rt->cmd_reset_event2",
            "rt->cmd_wait_events2",
            "const VkPipelineStageFlags2 src_stage_mask2",
            "const int legacy_stage_masks",
            "command->flags == 0",
            "command->index_offset != 0",
            "dependencyFlags = command->flags & VK_DEPENDENCY_BY_REGION_BIT",
            "has_event_barrier_payload",
            "collect_vulkan_graphics_v6_dependency_barriers",
            "bufferMemoryBarrierCount = buffer_barrier_count",
            "imageMemoryBarrierCount = image_barrier_count",
            "memoryBarrierCount = memory_barrier_count",
            "vkCmdSetEvent(command_buffer, entry->event, (VkPipelineStageFlags)command->index_offset)",
            "vkCmdResetEvent(command_buffer, entry->event, (VkPipelineStageFlags)command->index_offset)",
            "vkCmdWaitEvents(command_buffer, 1, &event,",
            "rt->cmd_set_event2(command_buffer, entry->event, &dependency)",
            "rt->cmd_reset_event2(command_buffer, entry->event, src_stage_mask2)",
            "rt->cmd_wait_events2(command_buffer, 1, &event, &dependency)",
        ]:
            self.assertIn(marker, executor)

        event_replay_body = executor.split("static int record_vulkan_graphics_v6_command_buffer", 1)[1].split(
            "case PDOCKER_GPU_GRAPHICS_V6_COMMAND_BARRIER:", 1
        )[0]
        self.assertNotIn("command->index_offset > UINT32_MAX", event_replay_body)
        self.assertNotIn("command->push_hash > UINT32_MAX", event_replay_body)

    def test_vulkan_event_lifecycle_is_executor_backed(self):
        executor = GPU_EXECUTOR.read_text()
        icd = VULKAN_ICD.read_text()

        for marker in [
            "PDOCKER_GPU_EXECUTOR_EVENT_REGISTRY_SLOTS 128u",
            "VulkanExecutorEventEntry g_event_registry",
            "find_executor_event_entry",
            "allocate_executor_event_entry",
            "handle_vulkan_event_command",
            "VULKAN_EVENT_CREATE",
            "VULKAN_EVENT_DESTROY",
            "VULKAN_EVENT_STATUS",
            "VULKAN_EVENT_SET",
            "VULKAN_EVENT_RESET",
            "print_vulkan_event_result",
            "vkCreateEvent(rt->device, &create_info, NULL, &entry->event)",
            "vkDestroyEvent(rt->device, entry->event, NULL)",
            "vkGetEventStatus(rt->device, entry->event)",
            "vkSetEvent(rt->device, entry->event)",
            "vkResetEvent(rt->device, entry->event)",
            "strncmp(cmd, \"VULKAN_EVENT_\", 13) == 0",
        ]:
            self.assertIn(marker, executor)

        for marker in [
            "bool executor_tracked;",
            "uint64_t event_id;",
            "send_executor_event_create",
            "send_executor_event_destroy",
            "send_executor_event_status",
            "send_executor_event_set",
            "send_executor_event_reset",
            "VULKAN_EVENT_CREATE %llu",
            "VULKAN_EVENT_DESTROY %llu",
            "VULKAN_EVENT_STATUS %llu",
            "VULKAN_EVENT_SET %llu",
            "VULKAN_EVENT_RESET %llu",
            "event->event_id = next_vulkan_object_generation();",
            "send_executor_event_create(event)",
            "send_executor_event_destroy(e)",
            "send_executor_event_status(e, &result)",
            "send_executor_event_set(e, &result)",
            "send_executor_event_reset(e, &result)",
        ]:
            self.assertIn(marker, icd)

        status_body = icd.split("VKAPI_ATTR VkResult VKAPI_CALL vkGetEventStatus", 1)[1].split(
            "VKAPI_ATTR VkResult VKAPI_CALL vkSetEvent", 1
        )[0]
        self.assertIn("if (e->executor_tracked)", status_body)
        self.assertLess(status_body.index("send_executor_event_status(e, &result)"), status_body.index("return e->signaled ? VK_EVENT_SET : VK_EVENT_RESET;"))

    def test_vulkan_icd_supports_synchronization2_submit_and_barrier_api(self):
        icd = VULKAN_ICD.read_text()
        self.assertIn("VK_KHR_SYNCHRONIZATION_2_EXTENSION_NAME", icd)
        self.assertIn("ADD_DEVICE_EXTENSION(VK_KHR_SYNCHRONIZATION_2_EXTENSION_NAME", icd)
        self.assertIn("VkPhysicalDeviceSynchronization2Features", icd)
        self.assertIn("p->synchronization2 = advertised_synchronization2();", icd)
        for name in [
            "vkQueueSubmit2",
            "vkCmdPipelineBarrier2",
            "vkCmdSetEvent2",
            "vkCmdResetEvent2",
            "vkCmdWaitEvents2",
        ]:
            self.assertIn(name, icd)
            self.assertIn(f"MAP_PROC({name});", icd)
            self.assertIn(f'MAP_ALIAS("{name}KHR", {name});', icd)
        self.assertIn("VkSubmitInfo2", icd)
        self.assertIn("VkDependencyInfo", icd)
        self.assertNotIn("free_submit_info_arrays", icd)
        self.assertIn("vkQueueSubmit(queue, 1, &legacy_submit, VK_NULL_HANDLE)", icd)
        self.assertIn("complete_submit2_semaphores(src)", icd)
        self.assertIn("collect_submit2_submit_sync_entries(src, submit2_fence", icd)
        self.assertIn("set_submit_sync_override(submit2_sync_entries, submit2_sync_count);", icd)
        self.assertIn("clear_submit_sync_override();", icd)
        self.assertLess(icd.index("set_submit_sync_override(submit2_sync_entries, submit2_sync_count);"), icd.index("vkQueueSubmit(queue, 1, &legacy_submit, VK_NULL_HANDLE)"))
        self.assertLess(icd.index("vkQueueSubmit(queue, 1, &legacy_submit, VK_NULL_HANDLE)"), icd.index("clear_submit_sync_override();"))
        self.assertLess(icd.index("clear_submit_sync_override();"), icd.index("complete_submit2_semaphores(src)"))
        self.assertIn("submit2-flags-unsupported", icd)
        set_event2_body = icd.split("VKAPI_ATTR void VKAPI_CALL vkCmdSetEvent2", 1)[1].split(
            "VKAPI_ATTR void VKAPI_CALL vkCmdResetEvent2", 1
        )[0]
        self.assertIn("dependency_info_has_unsupported_pnext(pDependencyInfo)", set_event2_body)
        self.assertIn("event-set2-dependency-info-unsupported", set_event2_body)
        self.assertIn("command_buffer_mark_recording_failed(cmd, \"event-set2-dependency-info-unsupported\")", set_event2_body)
        self.assertNotIn("if (cmd) cmd->graphics_unsupported = true", set_event2_body)
        self.assertIn("dependency_flags_unsupported(dependency_flags)", set_event2_body)
        self.assertIn("event-set2-dependency-flags-unsupported", set_event2_body)
        self.assertIn("record_dependency_info_barrier_ops(commandBuffer, pDependencyInfo)", set_event2_body)
        self.assertIn("record_event_command(commandBuffer, event, true, dependency_info_src_stage_mask(pDependencyInfo), dependency_flags, &barriers)", set_event2_body)
        self.assertNotIn("event-set2-barrier-payload-unsupported", set_event2_body)
        self.assertIn("command_buffer_mark_recording_failed", set_event2_body)
        self.assertIn("dependency_info_src_stage_mask", icd)
        self.assertIn("dependency_info_dst_stage_mask", icd)
        self.assertNotIn("vkCmdPipelineBarrier2(commandBuffer, pDependencyInfo)", set_event2_body)
        wait_events2_body = icd.split("VKAPI_ATTR void VKAPI_CALL vkCmdWaitEvents2", 1)[1].split(
            "static bool query_range_valid", 1
        )[0]
        self.assertIn("eventCount > 0 && (!pEvents || !pDependencyInfos)", wait_events2_body)
        self.assertIn("for (uint32_t i = 0; i < eventCount; ++i)", wait_events2_body)
        self.assertIn("if (!pEvents[i])", wait_events2_body)
        self.assertIn("dependency_info_has_unsupported_pnext(&pDependencyInfos[i])", wait_events2_body)
        self.assertIn("dependency_flags_unsupported(dependency_flags)", wait_events2_body)
        self.assertIn("event-wait2-dependency-flags-unsupported", wait_events2_body)
        self.assertIn("record_dependency_info_barrier_ops(commandBuffer, &pDependencyInfos[i])", wait_events2_body)
        self.assertIn("record_event_wait_command(commandBuffer, pEvents[i],", wait_events2_body)
        self.assertNotIn("event-wait2-barrier-payload-unsupported", wait_events2_body)
        self.assertIn("dependency_info_src_stage_mask(&pDependencyInfos[i])", wait_events2_body)
        self.assertIn("dependency_info_dst_stage_mask(&pDependencyInfos[i])", wait_events2_body)
        self.assertNotIn("vkCmdPipelineBarrier2(commandBuffer, &pDependencyInfos[i])", wait_events2_body)
        self.assertLess(
            wait_events2_body.index("record_dependency_info_barrier_ops(commandBuffer, &pDependencyInfos[i])"),
            wait_events2_body.index("record_event_wait_command(commandBuffer, pEvents[i],"),
        )
        self.assertNotIn("eventCount > 1", wait_events2_body)

    def test_vulkan_graphics_v617_query_timestamp_abi_is_append_only(self):
        abi = APP_HEADER.read_text()
        container_abi = CONTAINER_HEADER.read_text()
        executor = GPU_EXECUTOR.read_text()
        icd = VULKAN_ICD.read_text()
        for source in [abi, container_abi]:
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V617_ABI_MINOR 17u", source)
            self.assertIn("PdockerGpuVulkanGraphicsV617FrameHeader", source)
            self.assertIn("PdockerGpuVulkanGraphicsV617HeaderExtension", source)
            self.assertIn("PdockerGpuVulkanGraphicsV617QueryCommandEntry", source)
            self.assertIn("PdockerGpuVulkanGraphicsV617QueryResultEntry", source)
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V617_HEADER_EXTENSION_FIELDS", source)
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V617_QUERY_COMMAND_FIELDS", source)
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V617_QUERY_RESULT_FIELDS", source)
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V617_QUERY_COMMAND_SCHEMA_HASH", source)
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V617_QUERY_RESULT_SCHEMA_HASH", source)
            self.assertIn("PDOCKER_GPU_GRAPHICS_V6_COMMAND_RESET_QUERY_POOL", source)
            self.assertIn("PDOCKER_GPU_GRAPHICS_V6_COMMAND_WRITE_TIMESTAMP", source)
            self.assertIn("PDOCKER_GPU_GRAPHICS_V6_COMMAND_BEGIN_QUERY", source)
            self.assertIn("PDOCKER_GPU_GRAPHICS_V6_COMMAND_END_QUERY", source)
            self.assertIn("PDOCKER_GPU_GRAPHICS_V617_QUERY_OP_BEGIN", source)
            self.assertIn("PDOCKER_GPU_GRAPHICS_V617_QUERY_OP_END", source)
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V618_ABI_MINOR 18u", source)
            self.assertIn("PdockerGpuVulkanGraphicsV618FrameHeader", source)
            self.assertIn("PdockerGpuVulkanGraphicsV618HeaderExtension", source)
            self.assertIn("PdockerGpuVulkanGraphicsV618CopyQueryResultEntry", source)
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V618_COPY_QUERY_RESULT_FIELDS", source)
            self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V618_COPY_QUERY_RESULT_SCHEMA_HASH", source)
            self.assertIn("PDOCKER_GPU_GRAPHICS_V6_COMMAND_COPY_QUERY_POOL_RESULTS", source)
            self.assertIn("query_pool_id", source)
            self.assertIn("result_fd_index", source)
        for marker in [
            "PDOCKER_GPU_VULKAN_GRAPHICS_V617_ABI_MINOR",
            "sizeof(PdockerGpuVulkanGraphicsV617FrameHeader)",
            "header_v617->v617.query_command_count",
            "PdockerGpuVulkanGraphicsV617QueryCommandEntry",
            "PdockerGpuVulkanGraphicsV617QueryResultEntry",
            "FrameRange ranges[43]",
            "query_command_schema_hash",
            "query_result_schema_hash",
            "view->query_commands",
            "find_vulkan_graphics_v617_query_command",
            "VulkanGraphicsReplayQueries",
            "materialize_vulkan_graphics_v617_queries",
            "vkCreateQueryPool",
            "vkCmdBeginQuery",
            "vkCmdEndQuery",
            "vkCmdResetQueryPool",
            "vkCmdWriteTimestamp",
            "vkGetQueryPoolResults",
            "VK_QUERY_TYPE_OCCLUSION",
            "vulkan-graphics-v6-query-writeback",
            "if (!view->is_v617 || !view->header_v617) return 0;",
            "if (!view->query_commands) return -EPROTO;",
            "case PDOCKER_GPU_GRAPHICS_V6_COMMAND_COPY_QUERY_POOL_RESULTS:",
            "header_v618->v618.copy_query_result_table_offset",
            "copy_query_result_table_hash",
        ]:
            self.assertIn(marker, executor)
        for marker in [
            "uint64_t pool_id;",
            "int result_fd;",
            "PdockerGpuVulkanGraphicsV617QueryResultEntry *result_entries",
            "next_vulkan_query_pool_id",
            "create_shared_fd(pool->result_size)",
            "mmap(NULL, pool->result_size",
            "pool->result_entries[q].available",
            "munmap(pool->result_entries",
            "need_v617_query",
            "query_command_table_hash",
            "result_fd_index",
            "pool->result_fd",
            "PDOCKER_GPU_GRAPHICS_V6_COMMAND_RESET_QUERY_POOL",
            "PDOCKER_GPU_GRAPHICS_V6_COMMAND_WRITE_TIMESTAMP",
            "PDOCKER_GPU_GRAPHICS_V6_COMMAND_BEGIN_QUERY",
            "PDOCKER_GPU_GRAPHICS_V6_COMMAND_END_QUERY",
        ]:
            self.assertIn(marker, icd)

    def test_vulkan_graphics_image_layout_range_v620_abi_scaffold(self):
        abi = APP_HEADER.read_text()
        container_abi = CONTAINER_HEADER.read_text()
        executor = GPU_EXECUTOR.read_text()
        icd = VULKAN_ICD.read_text()
        expected_extension_fields = [
            ("image_layout_range_count", "u32"),
            ("image_layout_range_entry_size", "u32"),
            ("image_layout_range_table_offset", "u64"),
            ("image_layout_range_table_size", "u64"),
            ("image_layout_range_schema_hash", "u64"),
            ("image_layout_range_table_hash", "u64"),
            ("extension_hash", "u64"),
        ]
        expected_range_fields = [
            ("image_index", "u32"),
            ("aspect_mask", "u32"),
            ("base_mip_level", "u32"),
            ("level_count", "u32"),
            ("base_array_layer", "u32"),
            ("layer_count", "u32"),
            ("layout", "u32"),
            ("reserved0", "u32"),
            ("layout_generation", "u64"),
        ]
        for header_path, source in [(APP_HEADER, abi), (CONTAINER_HEADER, container_abi)]:
            with self.subTest(header=str(header_path)):
                self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V620_ABI_MINOR 20u", source)
                self.assertIn("PdockerGpuVulkanGraphicsV620HeaderExtension", source)
                self.assertIn("PdockerGpuVulkanGraphicsV620FrameHeader", source)
                self.assertIn("PdockerGpuVulkanGraphicsV620ImageLayoutRangeEntry", source)
                self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V620_HEADER_EXTENSION_FIELDS", source)
                self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V620_IMAGE_LAYOUT_RANGE_FIELDS", source)
                self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V620_MAX_IMAGE_LAYOUT_RANGES 4096u", source)
                header_fields, header_count, declared_header_hash, computed_header_hash = vulkan_dispatch_v5_schema(
                    header_path,
                    "PDOCKER_GPU_VULKAN_GRAPHICS_V620_HEADER_EXTENSION_FIELDS",
                    "PDOCKER_GPU_VULKAN_GRAPHICS_V620_HEADER_EXTENSION_FIELD_COUNT",
                    "PDOCKER_GPU_VULKAN_GRAPHICS_V620_HEADER_EXTENSION_SCHEMA_HASH",
                )
                range_fields, range_count, declared_range_hash, computed_range_hash = vulkan_dispatch_v5_schema(
                    header_path,
                    "PDOCKER_GPU_VULKAN_GRAPHICS_V620_IMAGE_LAYOUT_RANGE_FIELDS",
                    "PDOCKER_GPU_VULKAN_GRAPHICS_V620_IMAGE_LAYOUT_RANGE_FIELD_COUNT",
                    "PDOCKER_GPU_VULKAN_GRAPHICS_V620_IMAGE_LAYOUT_RANGE_SCHEMA_HASH",
                )
                self.assertEqual(expected_extension_fields, header_fields)
                self.assertEqual(expected_range_fields, range_fields)
                self.assertEqual(7, header_count)
                self.assertEqual(9, range_count)
                self.assertEqual(declared_header_hash, computed_header_hash)
                self.assertEqual(declared_range_hash, computed_range_hash)
                self.assertEqual(
                    [name for name, _ in expected_extension_fields],
                    c_struct_field_names(header_path, "PdockerGpuVulkanGraphicsV620HeaderExtension"),
                )
                self.assertEqual(
                    [name for name, _ in expected_range_fields],
                    c_struct_field_names(header_path, "PdockerGpuVulkanGraphicsV620ImageLayoutRangeEntry"),
                )
                self.assertEqual(
                    c_struct_field_names(header_path, "PdockerGpuVulkanGraphicsV619FrameHeader") + ["v620"],
                    c_struct_field_names(header_path, "PdockerGpuVulkanGraphicsV620FrameHeader"),
                )

        self.assertEqual(
            vulkan_dispatch_v5_schema(
                APP_HEADER,
                "PDOCKER_GPU_VULKAN_GRAPHICS_V620_HEADER_EXTENSION_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V620_HEADER_EXTENSION_FIELD_COUNT",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V620_HEADER_EXTENSION_SCHEMA_HASH",
            ),
            vulkan_dispatch_v5_schema(
                CONTAINER_HEADER,
                "PDOCKER_GPU_VULKAN_GRAPHICS_V620_HEADER_EXTENSION_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V620_HEADER_EXTENSION_FIELD_COUNT",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V620_HEADER_EXTENSION_SCHEMA_HASH",
            ),
        )
        self.assertEqual(
            vulkan_dispatch_v5_schema(
                APP_HEADER,
                "PDOCKER_GPU_VULKAN_GRAPHICS_V620_IMAGE_LAYOUT_RANGE_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V620_IMAGE_LAYOUT_RANGE_FIELD_COUNT",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V620_IMAGE_LAYOUT_RANGE_SCHEMA_HASH",
            ),
            vulkan_dispatch_v5_schema(
                CONTAINER_HEADER,
                "PDOCKER_GPU_VULKAN_GRAPHICS_V620_IMAGE_LAYOUT_RANGE_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V620_IMAGE_LAYOUT_RANGE_FIELD_COUNT",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V620_IMAGE_LAYOUT_RANGE_SCHEMA_HASH",
            ),
        )

        for marker in [
            "PDOCKER_GPU_VULKAN_GRAPHICS_V620_ABI_MINOR",
            "sizeof(PdockerGpuVulkanGraphicsV620FrameHeader)",
            "PdockerGpuVulkanGraphicsV620ImageLayoutRangeEntry",
            "header_v620->v620.image_layout_range_count",
            "image_layout_range_entry_size != sizeof(PdockerGpuVulkanGraphicsV620ImageLayoutRangeEntry)",
            "image_layout_range_schema_hash != PDOCKER_GPU_VULKAN_GRAPHICS_V620_IMAGE_LAYOUT_RANGE_SCHEMA_HASH",
            "PDOCKER_GPU_VULKAN_GRAPHICS_V620_MAX_IMAGE_LAYOUT_RANGES",
            "is_v620 ? 44u",
            "view->image_layout_ranges",
            "image_layout_range_table_hash",
            "header_v620->v620.extension_hash != image_layout_range_table_hash",
            "const int is_v620 = header->abi_minor == PDOCKER_GPU_VULKAN_GRAPHICS_V620_ABI_MINOR || is_v621;",
            "const int is_v619 = header->abi_minor == PDOCKER_GPU_VULKAN_GRAPHICS_V619_ABI_MINOR || is_v620;",
            "const int is_v620_header = header->abi_minor == PDOCKER_GPU_VULKAN_GRAPHICS_V620_ABI_MINOR || is_v621_header;",
            "const int is_v619_header = header->abi_minor == PDOCKER_GPU_VULKAN_GRAPHICS_V619_ABI_MINOR || is_v620_header;",
            "validate_vulkan_graphics_v620_image_layout_ranges",
            "VulkanReplayImageLayoutRange",
            "PDOCKER_GPU_MAX_VULKAN_IMAGE_LAYOUT_RANGES_PER_IMAGE",
            "materialize_vulkan_graphics_v620_image_layout_ranges",
            "vulkan_graphics_replay_image_by_source_index",
            "record_vulkan_graphics_v620_initial_image_layout_ranges",
            "record_vulkan_graphics_v6_staged_image_uploads(command_buffer, attachments);",
        ]:
            self.assertIn(marker, executor)

        for marker in [
            "PdockerGpuVulkanGraphicsV620FrameHeader *frame_header_v620",
            "PdockerGpuVulkanGraphicsV620ImageLayoutRangeEntry image_layout_ranges[PDOCKER_GPU_VULKAN_GRAPHICS_V620_MAX_IMAGE_LAYOUT_RANGES]",
            "collect_graphics_image_layout_range_entries",
            "image->layout_range_overflow || image->layout_range_count == 0",
            "*range_count >= PDOCKER_GPU_VULKAN_GRAPHICS_V620_MAX_IMAGE_LAYOUT_RANGES",
            "frame_header_v620->v620.image_layout_range_count = (uint32_t)image_layout_range_count",
            "frame_header_v620->v620.image_layout_range_entry_size = sizeof(PdockerGpuVulkanGraphicsV620ImageLayoutRangeEntry)",
            "frame_header_v620->v620.image_layout_range_schema_hash = PDOCKER_GPU_VULKAN_GRAPHICS_V620_IMAGE_LAYOUT_RANGE_SCHEMA_HASH",
            "APPEND_GRAPHICS_TABLE(image_layout_ranges, image_layout_range_count",
            "frame_header_v620->v620.image_layout_range_table_hash = fnv1a64_bytes",
            "need_v620_image_layout_range ? \"VULKAN_GRAPHICS_V6.20\"",
        ]:
            self.assertIn(marker, icd)

    def test_vulkan_graphics_v621_submit2_metadata_abi_is_append_only(self):
        expected_extension_fields = [
            ("submit_info_count", "u32"),
            ("submit_info_entry_size", "u32"),
            ("submit_info_table_offset", "u64"),
            ("submit_info_table_size", "u64"),
            ("submit_info_schema_hash", "u64"),
            ("submit_info_table_hash", "u64"),
            ("submit_sync_info_count", "u32"),
            ("submit_sync_info_entry_size", "u32"),
            ("submit_sync_info_table_offset", "u64"),
            ("submit_sync_info_table_size", "u64"),
            ("submit_sync_info_schema_hash", "u64"),
            ("submit_sync_info_table_hash", "u64"),
            ("extension_hash", "u64"),
        ]
        expected_submit_info_fields = [
            ("submit_kind", "u32"),
            ("submit_flags", "u32"),
            ("command_buffer_index", "u32"),
            ("command_buffer_device_mask", "u32"),
            ("reserved0", "u64"),
        ]
        expected_submit_sync_info_fields = [
            ("submit_sync_index", "u32"),
            ("device_index", "u32"),
            ("reserved0", "u32"),
            ("reserved1", "u32"),
        ]

        for header_path in [APP_HEADER, CONTAINER_HEADER]:
            source = header_path.read_text()
            with self.subTest(header=str(header_path)):
                for marker in [
                    "PDOCKER_GPU_VULKAN_GRAPHICS_V621_ABI_MINOR 21u",
                    "PdockerGpuVulkanGraphicsV621HeaderExtension",
                    "PdockerGpuVulkanGraphicsV621FrameHeader",
                    "PdockerGpuVulkanGraphicsV621SubmitInfoEntry",
                    "PdockerGpuVulkanGraphicsV621SubmitSyncInfoEntry",
                    "PDOCKER_GPU_GRAPHICS_V621_SUBMIT_KIND_LEGACY",
                    "PDOCKER_GPU_GRAPHICS_V621_SUBMIT_KIND_SUBMIT2",
                    "PDOCKER_GPU_GRAPHICS_V621_COMMAND_BUFFER_INDEX_NONE",
                    "PDOCKER_GPU_VULKAN_GRAPHICS_V621_MAX_SUBMIT_INFOS 1u",
                    "PDOCKER_GPU_VULKAN_GRAPHICS_V621_MAX_SUBMIT_SYNC_INFOS PDOCKER_GPU_VULKAN_GRAPHICS_V619_MAX_SUBMIT_SYNCS",
                ]:
                    self.assertIn(marker, source)

                extension_fields, extension_count, declared_extension_hash, computed_extension_hash = vulkan_dispatch_v5_schema(
                    header_path,
                    "PDOCKER_GPU_VULKAN_GRAPHICS_V621_HEADER_EXTENSION_FIELDS",
                    "PDOCKER_GPU_VULKAN_GRAPHICS_V621_HEADER_EXTENSION_FIELD_COUNT",
                    "PDOCKER_GPU_VULKAN_GRAPHICS_V621_HEADER_EXTENSION_SCHEMA_HASH",
                )
                submit_info_fields, submit_info_count, declared_submit_info_hash, computed_submit_info_hash = vulkan_dispatch_v5_schema(
                    header_path,
                    "PDOCKER_GPU_VULKAN_GRAPHICS_V621_SUBMIT_INFO_FIELDS",
                    "PDOCKER_GPU_VULKAN_GRAPHICS_V621_SUBMIT_INFO_FIELD_COUNT",
                    "PDOCKER_GPU_VULKAN_GRAPHICS_V621_SUBMIT_INFO_SCHEMA_HASH",
                )
                submit_sync_info_fields, submit_sync_info_count, declared_submit_sync_info_hash, computed_submit_sync_info_hash = vulkan_dispatch_v5_schema(
                    header_path,
                    "PDOCKER_GPU_VULKAN_GRAPHICS_V621_SUBMIT_SYNC_INFO_FIELDS",
                    "PDOCKER_GPU_VULKAN_GRAPHICS_V621_SUBMIT_SYNC_INFO_FIELD_COUNT",
                    "PDOCKER_GPU_VULKAN_GRAPHICS_V621_SUBMIT_SYNC_INFO_SCHEMA_HASH",
                )
                self.assertEqual(expected_extension_fields, extension_fields)
                self.assertEqual(expected_submit_info_fields, submit_info_fields)
                self.assertEqual(expected_submit_sync_info_fields, submit_sync_info_fields)
                self.assertEqual(13, extension_count)
                self.assertEqual(5, submit_info_count)
                self.assertEqual(4, submit_sync_info_count)
                self.assertEqual(declared_extension_hash, computed_extension_hash)
                self.assertEqual(declared_submit_info_hash, computed_submit_info_hash)
                self.assertEqual(declared_submit_sync_info_hash, computed_submit_sync_info_hash)
                self.assertEqual(
                    [name for name, _ in expected_extension_fields],
                    c_struct_field_names(header_path, "PdockerGpuVulkanGraphicsV621HeaderExtension"),
                )
                self.assertEqual(
                    [name for name, _ in expected_submit_info_fields],
                    c_struct_field_names(header_path, "PdockerGpuVulkanGraphicsV621SubmitInfoEntry"),
                )
                self.assertEqual(
                    [name for name, _ in expected_submit_sync_info_fields],
                    c_struct_field_names(header_path, "PdockerGpuVulkanGraphicsV621SubmitSyncInfoEntry"),
                )
                self.assertEqual(
                    c_struct_field_names(header_path, "PdockerGpuVulkanGraphicsV620FrameHeader") + ["v621"],
                    c_struct_field_names(header_path, "PdockerGpuVulkanGraphicsV621FrameHeader"),
                )

    def test_vulkan_graphics_v622_multisample_state_metadata_is_append_only(self):
        expected_extension_fields = [
            ("multisample_state_count", "u32"),
            ("multisample_state_entry_size", "u32"),
            ("multisample_state_table_offset", "u64"),
            ("multisample_state_table_size", "u64"),
            ("multisample_state_schema_hash", "u64"),
            ("multisample_state_table_hash", "u64"),
            ("extension_hash", "u64"),
        ]
        expected_multisample_fields = [
            ("pipeline_index", "u32"),
            ("flags", "u32"),
            ("min_sample_shading_bits", "u32"),
            ("sample_mask_word_count", "u32"),
            ("sample_mask0", "u32"),
            ("reserved0", "u32"),
            ("reserved1", "u64"),
        ]
        for header_path in [APP_HEADER, CONTAINER_HEADER]:
            source = header_path.read_text()
            with self.subTest(header=str(header_path)):
                for marker in [
                    "PDOCKER_GPU_VULKAN_GRAPHICS_V622_ABI_MINOR 22u",
                    "PdockerGpuVulkanGraphicsV622HeaderExtension",
                    "PdockerGpuVulkanGraphicsV622FrameHeader",
                    "PdockerGpuVulkanGraphicsV622MultisampleStateEntry",
                    "PDOCKER_GPU_VULKAN_GRAPHICS_V622_MAX_MULTISAMPLE_STATES PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_PIPELINES",
                    "PDOCKER_GPU_VULKAN_GRAPHICS_V622_MAX_SAMPLE_MASK_WORDS 1u",
                    "PDOCKER_GPU_GRAPHICS_V622_MULTISAMPLE_SAMPLE_SHADING_ENABLE",
                    "PDOCKER_GPU_GRAPHICS_V622_MULTISAMPLE_SAMPLE_MASK_PRESENT",
                    "PDOCKER_GPU_GRAPHICS_V622_MULTISAMPLE_ALPHA_TO_COVERAGE_ENABLE",
                    "PDOCKER_GPU_GRAPHICS_V622_MULTISAMPLE_ALPHA_TO_ONE_ENABLE",
                ]:
                    self.assertIn(marker, source)
                extension_fields, extension_count, declared_extension_hash, computed_extension_hash = vulkan_dispatch_v5_schema(
                    header_path,
                    "PDOCKER_GPU_VULKAN_GRAPHICS_V622_HEADER_EXTENSION_FIELDS",
                    "PDOCKER_GPU_VULKAN_GRAPHICS_V622_HEADER_EXTENSION_FIELD_COUNT",
                    "PDOCKER_GPU_VULKAN_GRAPHICS_V622_HEADER_EXTENSION_SCHEMA_HASH",
                )
                multisample_fields, multisample_count, declared_multisample_hash, computed_multisample_hash = vulkan_dispatch_v5_schema(
                    header_path,
                    "PDOCKER_GPU_VULKAN_GRAPHICS_V622_MULTISAMPLE_STATE_FIELDS",
                    "PDOCKER_GPU_VULKAN_GRAPHICS_V622_MULTISAMPLE_STATE_FIELD_COUNT",
                    "PDOCKER_GPU_VULKAN_GRAPHICS_V622_MULTISAMPLE_STATE_SCHEMA_HASH",
                )
                self.assertEqual(expected_extension_fields, extension_fields)
                self.assertEqual(expected_multisample_fields, multisample_fields)
                self.assertEqual(7, extension_count)
                self.assertEqual(7, multisample_count)
                self.assertNotEqual(0, declared_extension_hash)
                self.assertNotEqual(0, declared_multisample_hash)
                self.assertEqual(declared_extension_hash, computed_extension_hash)
                self.assertEqual(declared_multisample_hash, computed_multisample_hash)
                self.assertEqual(
                    [name for name, _ in expected_extension_fields],
                    c_struct_field_names(header_path, "PdockerGpuVulkanGraphicsV622HeaderExtension"),
                )
                self.assertEqual(
                    [name for name, _ in expected_multisample_fields],
                    c_struct_field_names(header_path, "PdockerGpuVulkanGraphicsV622MultisampleStateEntry"),
                )
                self.assertEqual(
                    c_struct_field_names(header_path, "PdockerGpuVulkanGraphicsV621FrameHeader") + ["v622"],
                    c_struct_field_names(header_path, "PdockerGpuVulkanGraphicsV622FrameHeader"),
                )
                self.assertNotIn("sampleShadingEnable", source)

    def test_vulkan_graphics_v622_multisample_state_is_wired_through_icd_and_executor(self):
        icd = VULKAN_ICD.read_text()
        executor = GPU_EXECUTOR.read_text()

        for marker in [
            "PdockerGpuVulkanGraphicsV622MultisampleStateEntry multisample_states[PDOCKER_GPU_VULKAN_GRAPHICS_V622_MAX_MULTISAMPLE_STATES]",
            "PdockerGpuVulkanGraphicsV622FrameHeader *frame_header_v622",
            "sizeof(*frame_header_v622)",
            "need_v622_multisample_state",
            "PDOCKER_GPU_VULKAN_GRAPHICS_V622_ABI_MINOR",
            "frame_header_v622->v622.multisample_state_count",
            "APPEND_GRAPHICS_TABLE(multisample_states, multisample_state_count",
            "frame_header_v622->v622.multisample_state_table_hash",
            "VULKAN_GRAPHICS_V6.22",
            "sampleShadingEnable",
            "alphaToCoverageEnable",
            "alphaToOneEnable",
        ]:
            self.assertIn(marker, icd)

        for marker in [
            "PDOCKER_GPU_VULKAN_GRAPHICS_V622_ABI_MINOR",
            "sizeof(PdockerGpuVulkanGraphicsV622FrameHeader)",
            "const int is_v622",
            "header_v622->v622.multisample_state_count",
            "FrameRange ranges[48]",
            "is_v622 ? 47u",
            "view->header_v622",
            "view->is_v622",
            "view->multisample_states",
            "find_vulkan_graphics_v622_multisample_state",
            "multisample_state_table_hash",
            "msci.sampleShadingEnable",
            "msci.pSampleMask",
            "enabled_features.sampleRateShading",
            "enabled_features.alphaToOne",
        ]:
            self.assertIn(marker, executor)


    def test_vulkan_graphics_v623_tessellation_state_abi_is_append_only(self):
        expected_extension_fields = [
            ("tessellation_state_count", "u32"),
            ("tessellation_state_entry_size", "u32"),
            ("tessellation_state_table_offset", "u64"),
            ("tessellation_state_table_size", "u64"),
            ("tessellation_state_schema_hash", "u64"),
            ("tessellation_state_table_hash", "u64"),
            ("extension_hash", "u64"),
        ]
        expected_tessellation_fields = [
            ("pipeline_index", "u32"),
            ("patch_control_points", "u32"),
            ("flags", "u32"),
            ("reserved0", "u32"),
            ("reserved1", "u64"),
        ]
        for header_path in [APP_HEADER, CONTAINER_HEADER]:
            source = header_path.read_text()
            with self.subTest(header=str(header_path)):
                for marker in [
                    "PDOCKER_GPU_VULKAN_GRAPHICS_V623_ABI_MINOR 23u",
                    "PdockerGpuVulkanGraphicsV623HeaderExtension",
                    "PdockerGpuVulkanGraphicsV623FrameHeader",
                    "PdockerGpuVulkanGraphicsV623TessellationStateEntry",
                    "PDOCKER_GPU_VULKAN_GRAPHICS_V623_MAX_TESSELLATION_STATES PDOCKER_GPU_VULKAN_GRAPHICS_V6_MAX_PIPELINES",
                    "patch_control_points",
                ]:
                    self.assertIn(marker, source)
                extension_fields, extension_count, declared_extension_hash, computed_extension_hash = vulkan_dispatch_v5_schema(
                    header_path,
                    "PDOCKER_GPU_VULKAN_GRAPHICS_V623_HEADER_EXTENSION_FIELDS",
                    "PDOCKER_GPU_VULKAN_GRAPHICS_V623_HEADER_EXTENSION_FIELD_COUNT",
                    "PDOCKER_GPU_VULKAN_GRAPHICS_V623_HEADER_EXTENSION_SCHEMA_HASH",
                )
                tessellation_fields, tessellation_count, declared_tessellation_hash, computed_tessellation_hash = vulkan_dispatch_v5_schema(
                    header_path,
                    "PDOCKER_GPU_VULKAN_GRAPHICS_V623_TESSELLATION_STATE_FIELDS",
                    "PDOCKER_GPU_VULKAN_GRAPHICS_V623_TESSELLATION_STATE_FIELD_COUNT",
                    "PDOCKER_GPU_VULKAN_GRAPHICS_V623_TESSELLATION_STATE_SCHEMA_HASH",
                )
                self.assertEqual(expected_extension_fields, extension_fields)
                self.assertEqual(expected_tessellation_fields, tessellation_fields)
                self.assertEqual(7, extension_count)
                self.assertEqual(5, tessellation_count)
                self.assertEqual(declared_extension_hash, computed_extension_hash)
                self.assertEqual(declared_tessellation_hash, computed_tessellation_hash)
                self.assertEqual(
                    [name for name, _ in expected_extension_fields],
                    c_struct_field_names(header_path, "PdockerGpuVulkanGraphicsV623HeaderExtension"),
                )
                self.assertEqual(
                    [name for name, _ in expected_tessellation_fields],
                    c_struct_field_names(header_path, "PdockerGpuVulkanGraphicsV623TessellationStateEntry"),
                )
                self.assertEqual(
                    c_struct_field_names(header_path, "PdockerGpuVulkanGraphicsV622FrameHeader") + ["v623"],
                    c_struct_field_names(header_path, "PdockerGpuVulkanGraphicsV623FrameHeader"),
                )

    def test_vulkan_graphics_v623_tessellation_state_is_wired_through_icd_and_executor(self):
        icd = VULKAN_ICD.read_text()
        executor = GPU_EXECUTOR.read_text()

        for marker in [
            "PdockerGpuVulkanGraphicsV623TessellationStateEntry tessellation_states[PDOCKER_GPU_VULKAN_GRAPHICS_V623_MAX_TESSELLATION_STATES]",
            "PdockerGpuVulkanGraphicsV623FrameHeader *frame_header_v623",
            "sizeof(PdockerGpuVulkanGraphicsV623FrameHeader)",
            "need_v623_tessellation_state",
            "PDOCKER_GPU_VULKAN_GRAPHICS_V623_ABI_MINOR",
            "frame_header_v623->v623.tessellation_state_count",
            "APPEND_GRAPHICS_TABLE(tessellation_states, tessellation_state_count",
            "frame_header_v623->v623.tessellation_state_table_hash",
            "VULKAN_GRAPHICS_V6.23",
            "pTessellationState",
            "patchControlPoints",
            "VK_PRIMITIVE_TOPOLOGY_PATCH_LIST",
            "pipeline->requested_feature_mask =",
            "PDOCKER_VK_FEATURE_TESSELLATION_SHADER",
        ]:
            self.assertIn(marker, icd)
        graphics_create_body = icd.split("VKAPI_ATTR VkResult VKAPI_CALL vkCreateGraphicsPipelines", 1)[1].split(
            "VKAPI_ATTR void VKAPI_CALL vkDestroyPipeline", 1
        )[0]
        self.assertNotIn("(void)device;", graphics_create_body)
        self.assertIn("device ? ((PdockerVkDevice *)device)->requested_feature_mask : 0", graphics_create_body)
        self.assertIn("pipeline->requested_feature_mask & PDOCKER_VK_FEATURE_TESSELLATION_SHADER", graphics_create_body)

        for marker in [
            "PDOCKER_GPU_VULKAN_GRAPHICS_V623_ABI_MINOR",
            "sizeof(PdockerGpuVulkanGraphicsV623FrameHeader)",
            "const int is_v623",
            "header_v623->v623.tessellation_state_count",
            "FrameRange ranges[48]",
            "is_v623 ? 48u",
            "view->header_v623",
            "view->is_v623",
            "view->tessellation_states",
            "find_vulkan_graphics_v623_tessellation_state",
            "tessellation_state_table_hash",
            "enabled_features.tessellationShader",
            "maxTessellationPatchSize",
            "VK_PRIMITIVE_TOPOLOGY_PATCH_LIST",
            "VkPipelineTessellationStateCreateInfo tsci",
            ".pTessellationState = tessellation_state ? &tsci : NULL",
            ".stageFlags = pipeline_descriptor_stage_flags",
            "vulkan_graphics_v6_dynamic_primitive_topology_value",
            "tessellation draw requires patch-list primitive topology",
            "tessellation indexed draw requires patch-list primitive topology",
            "tessellation patch control points exceed Android Vulkan device limit",
        ]:
            self.assertIn(marker, executor)

    def test_vulkan_graphics_v623_runtime_checks_are_after_runtime_init(self):
        executor = GPU_EXECUTOR.read_text()
        replay_preflight_body = executor.split(
            "static int preflight_vulkan_graphics_v6_replay_supported", 1
        )[1].split("static int vulkan_graphics_v6_frame_needs_dynamic_rendering", 1)[0]
        runtime_preflight_body = executor.split(
            "static int preflight_vulkan_graphics_v6_runtime_supported", 1
        )[1].split("#define PDOCKER_GPU_GRAPHICS_REPLAY_MAX_SHADER_STAGES", 1)[0]
        record_body = executor.split(
            "static int record_vulkan_graphics_v6_command_buffer", 1
        )[1].split("static int submit_vulkan_graphics_v6_command_buffer", 1)[0]

        self.assertNotIn("g_vulkan_runtime.enabled_features.tessellationShader", replay_preflight_body)
        self.assertNotIn("g_vulkan_runtime.physical_properties.limits.maxTessellationPatchSize", replay_preflight_body)
        self.assertNotIn("g_vulkan_runtime.enabled_vulkan11.multiview", replay_preflight_body)
        self.assertNotIn("g_vulkan_runtime.enabled_features.sampleRateShading", replay_preflight_body)
        self.assertNotIn("g_vulkan_runtime.enabled_features.alphaToOne", replay_preflight_body)
        self.assertIn("init_vulkan_runtime(&g_vulkan_runtime)", runtime_preflight_body)
        self.assertIn("g_vulkan_runtime.enabled_features.tessellationShader", runtime_preflight_body)
        self.assertIn("g_vulkan_runtime.physical_properties.limits.maxTessellationPatchSize", runtime_preflight_body)
        self.assertIn("g_vulkan_runtime.enabled_vulkan11.multiview", runtime_preflight_body)
        self.assertIn("VK_DYNAMIC_STATE_PRIMITIVE_TOPOLOGY", replay_preflight_body)
        self.assertIn("tessellation draw requires patch-list primitive topology", replay_preflight_body)
        self.assertIn("tessellation indexed draw requires patch-list primitive topology", replay_preflight_body)
        self.assertIn("bound_pipeline_tessellated", record_body)
        self.assertIn("dynamic_primitive_topology_valid", record_body)
        self.assertIn("VK_PRIMITIVE_TOPOLOGY_PATCH_LIST", record_body)

    def test_vulkan_graphics_v621_submit2_metadata_is_wired_through_icd_and_executor(self):
        icd = VULKAN_ICD.read_text()
        executor = GPU_EXECUTOR.read_text()

        for marker in [
            "PdockerGpuVulkanGraphicsV621SubmitInfoEntry submit_infos[PDOCKER_GPU_VULKAN_GRAPHICS_V621_MAX_SUBMIT_INFOS]",
            "PdockerGpuVulkanGraphicsV621SubmitSyncInfoEntry submit_sync_infos[PDOCKER_GPU_VULKAN_GRAPHICS_V621_MAX_SUBMIT_SYNC_INFOS]",
            "g_submit2_metadata_override",
            "set_submit2_metadata_override(src);",
            "clear_submit2_metadata_override();",
            "set_submit2_metadata_command_index(j);",
            "PDOCKER_GPU_GRAPHICS_V621_SUBMIT_KIND_SUBMIT2",
            "need_v621_submit2_metadata",
            "header->abi_minor = PDOCKER_GPU_VULKAN_GRAPHICS_V621_ABI_MINOR;",
            "frame_header_v621->v621.submit_info_count",
            "submit_sync_infos[submit_sync_info_count].device_index = device_index;",
            "APPEND_GRAPHICS_TABLE(submit_infos, submit_info_count",
            "APPEND_GRAPHICS_TABLE(submit_sync_infos, submit_sync_info_count",
            "frame_header_v621->v621.submit_info_table_hash",
            "VULKAN_GRAPHICS_V6.21",
        ]:
            self.assertIn(marker, icd)

        for marker in [
            "PDOCKER_GPU_VULKAN_GRAPHICS_V621_ABI_MINOR",
            "sizeof(PdockerGpuVulkanGraphicsV621FrameHeader)",
            "const int is_v621",
            "header_v621->v621.submit_info_count",
            "header_v621->v621.submit_sync_info_count",
            "view->header_v621",
            "view->is_v621",
            "view->submit_infos",
            "view->submit_sync_infos",
            "submit_info_table_hash",
            "submit_sync_info_table_hash",
            "vulkan_graphics_v621_submit_sync_device_index",
            "wait_infos[i].deviceIndex = wait_device_indices[i];",
            "signal_infos[i].deviceIndex = signal_device_indices[i];",
            "PDOCKER_GPU_GRAPHICS_V621_SUBMIT_KIND_SUBMIT2",
            "submit_info->command_buffer_device_mask",
            ".flags = submit_info ? submit_info->submit_flags : 0",
            "entry->submit_kind == PDOCKER_GPU_GRAPHICS_V621_SUBMIT_KIND_SUBMIT2",
            "entry->device_index != 0",
        ]:
            self.assertIn(marker, executor)

        self.assertIn("is_v621 ? 46u", executor)
        self.assertNotIn("reserved0 = g_submit2", icd)
        self.assertNotIn("reserved0 = submit2", executor)

    def test_vulkan_graphics_v619_submit_sync_metadata_abi_is_append_only(self):
        abi = APP_HEADER.read_text()
        container_abi = CONTAINER_HEADER.read_text()
        executor = GPU_EXECUTOR.read_text()
        icd = VULKAN_ICD.read_text()
        expected_extension_fields = [
            ("submit_sync_count", "u32"),
            ("submit_sync_entry_size", "u32"),
            ("submit_sync_table_offset", "u64"),
            ("submit_sync_table_size", "u64"),
            ("submit_sync_schema_hash", "u64"),
            ("submit_sync_table_hash", "u64"),
            ("extension_hash", "u64"),
        ]
        expected_submit_sync_fields = [
            ("sync_type", "u32"),
            ("flags", "u32"),
            ("semaphore_id", "u64"),
            ("value", "u64"),
            ("stage_mask", "u64"),
            ("fence_id", "u64"),
            ("reserved0", "u64"),
        ]
        for header_path, source in [(APP_HEADER, abi), (CONTAINER_HEADER, container_abi)]:
            with self.subTest(header=str(header_path)):
                self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V619_ABI_MINOR 19u", source)
                self.assertIn("PdockerGpuVulkanGraphicsV619HeaderExtension", source)
                self.assertIn("PdockerGpuVulkanGraphicsV619FrameHeader", source)
                self.assertIn("PdockerGpuVulkanGraphicsV619SubmitSyncEntry", source)
                self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V619_HEADER_EXTENSION_FIELDS", source)
                self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V619_SUBMIT_SYNC_FIELDS", source)
                self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V619_HEADER_EXTENSION_SCHEMA_HASH", source)
                self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V619_SUBMIT_SYNC_SCHEMA_HASH", source)
                self.assertIn("PDOCKER_GPU_VULKAN_GRAPHICS_V619_MAX_SUBMIT_SYNCS 64u", source)
                for marker in [
                    "PDOCKER_GPU_GRAPHICS_V619_SUBMIT_SYNC_WAIT 1u",
                    "PDOCKER_GPU_GRAPHICS_V619_SUBMIT_SYNC_SIGNAL 2u",
                    "PDOCKER_GPU_GRAPHICS_V619_SUBMIT_SYNC_FENCE 3u",
                    "PDOCKER_GPU_GRAPHICS_V619_SUBMIT_SYNC_TIMELINE 0x00000001u",
                    "PDOCKER_GPU_GRAPHICS_V619_SUBMIT_SYNC_BINARY 0x00000002u",
                ]:
                    self.assertIn(marker, source)

                header_fields, header_count, declared_header_hash, computed_header_hash = vulkan_dispatch_v5_schema(
                    header_path,
                    "PDOCKER_GPU_VULKAN_GRAPHICS_V619_HEADER_EXTENSION_FIELDS",
                    "PDOCKER_GPU_VULKAN_GRAPHICS_V619_HEADER_EXTENSION_FIELD_COUNT",
                    "PDOCKER_GPU_VULKAN_GRAPHICS_V619_HEADER_EXTENSION_SCHEMA_HASH",
                )
                submit_fields, submit_count, declared_submit_hash, computed_submit_hash = vulkan_dispatch_v5_schema(
                    header_path,
                    "PDOCKER_GPU_VULKAN_GRAPHICS_V619_SUBMIT_SYNC_FIELDS",
                    "PDOCKER_GPU_VULKAN_GRAPHICS_V619_SUBMIT_SYNC_FIELD_COUNT",
                    "PDOCKER_GPU_VULKAN_GRAPHICS_V619_SUBMIT_SYNC_SCHEMA_HASH",
                )
                self.assertEqual(expected_extension_fields, header_fields)
                self.assertEqual(expected_submit_sync_fields, submit_fields)
                self.assertEqual(7, header_count)
                self.assertEqual(7, submit_count)
                self.assertEqual(declared_header_hash, computed_header_hash)
                self.assertEqual(declared_submit_hash, computed_submit_hash)
                self.assertEqual(
                    [name for name, _ in expected_extension_fields],
                    c_struct_field_names(header_path, "PdockerGpuVulkanGraphicsV619HeaderExtension"),
                )
                self.assertEqual(
                    [name for name, _ in expected_submit_sync_fields],
                    c_struct_field_names(header_path, "PdockerGpuVulkanGraphicsV619SubmitSyncEntry"),
                )
                self.assertEqual(
                    c_struct_field_names(header_path, "PdockerGpuVulkanGraphicsV618FrameHeader") + ["v619"],
                    c_struct_field_names(header_path, "PdockerGpuVulkanGraphicsV619FrameHeader"),
                )

        self.assertEqual(
            vulkan_dispatch_v5_schema(
                APP_HEADER,
                "PDOCKER_GPU_VULKAN_GRAPHICS_V619_HEADER_EXTENSION_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V619_HEADER_EXTENSION_FIELD_COUNT",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V619_HEADER_EXTENSION_SCHEMA_HASH",
            ),
            vulkan_dispatch_v5_schema(
                CONTAINER_HEADER,
                "PDOCKER_GPU_VULKAN_GRAPHICS_V619_HEADER_EXTENSION_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V619_HEADER_EXTENSION_FIELD_COUNT",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V619_HEADER_EXTENSION_SCHEMA_HASH",
            ),
        )
        self.assertEqual(
            vulkan_dispatch_v5_schema(
                APP_HEADER,
                "PDOCKER_GPU_VULKAN_GRAPHICS_V619_SUBMIT_SYNC_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V619_SUBMIT_SYNC_FIELD_COUNT",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V619_SUBMIT_SYNC_SCHEMA_HASH",
            ),
            vulkan_dispatch_v5_schema(
                CONTAINER_HEADER,
                "PDOCKER_GPU_VULKAN_GRAPHICS_V619_SUBMIT_SYNC_FIELDS",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V619_SUBMIT_SYNC_FIELD_COUNT",
                "PDOCKER_GPU_VULKAN_GRAPHICS_V619_SUBMIT_SYNC_SCHEMA_HASH",
            ),
        )

        for marker in [
            "PDOCKER_GPU_VULKAN_GRAPHICS_V619_ABI_MINOR",
            "sizeof(PdockerGpuVulkanGraphicsV619FrameHeader)",
            "PdockerGpuVulkanGraphicsV619SubmitSyncEntry",
            "header_v619->v619.submit_sync_count",
            "submit_sync_entry_size != sizeof(PdockerGpuVulkanGraphicsV619SubmitSyncEntry)",
            "submit_sync_schema_hash != PDOCKER_GPU_VULKAN_GRAPHICS_V619_SUBMIT_SYNC_SCHEMA_HASH",
            "PDOCKER_GPU_VULKAN_GRAPHICS_V619_MAX_SUBMIT_SYNCS",
            "FrameRange ranges[43]",
            "view->submit_syncs",
            'write_vulkan_graphics_v6_table_desc(out, "submit_syncs"',
            "submit_sync_table_hash",
            "header_v619->v619.extension_hash != submit_sync_table_hash",
            "PDOCKER_GPU_GRAPHICS_V619_SUBMIT_SYNC_WAIT",
            "PDOCKER_GPU_GRAPHICS_V619_SUBMIT_SYNC_SIGNAL",
            "PDOCKER_GPU_GRAPHICS_V619_SUBMIT_SYNC_FENCE",
            "PDOCKER_GPU_GRAPHICS_V619_SUBMIT_SYNC_TIMELINE",
            "PDOCKER_GPU_GRAPHICS_V619_SUBMIT_SYNC_BINARY",
            "if ((entry->flags & PDOCKER_GPU_GRAPHICS_V619_SUBMIT_SYNC_BINARY) && entry->value != 0)",
            "if (mode_flags == 0 || entry->semaphore_id == 0 || entry->fence_id != 0) return -EPROTO;",
            "if (mode_flags != 0 || entry->flags != 0 || entry->value != 0 ||",
            "PDOCKER_GPU_EXECUTOR_SUBMIT_SYNC_REGISTRY_SLOTS 128u",
            "VulkanExecutorSubmitSyncEntry g_submit_sync_registry",
            "find_executor_submit_sync_entry",
            "allocate_executor_submit_sync_entry",
            "resolve_executor_submit_sync_semaphore",
            "PDOCKER_GPU_EXECUTOR_SUBMIT_FENCE_REGISTRY_SLOTS 128u",
            "VulkanExecutorSubmitFenceEntry g_submit_fence_registry",
            "find_executor_submit_fence_entry",
            "allocate_executor_submit_fence_entry",
            "resolve_executor_submit_sync_fence",
            "vkCreateFence(rt->device, &create_info, NULL, &entry->fence)",
            "vkResetFences(rt->device, 1, &submit_fence)",
            "VkFence local_fence = VK_NULL_HANDLE;",
            "submit_fence_entry->signaled = 1;",
            "vkCreateSemaphore(rt->device, &create_info, NULL, &entry->semaphore)",
            "VK_STRUCTURE_TYPE_SEMAPHORE_TYPE_CREATE_INFO",
            "VK_SEMAPHORE_TYPE_TIMELINE",
            "enabled_vulkan12.timelineSemaphore = rt->physical_vulkan12.timelineSemaphore;",
            "VK_KHR_TIMELINE_SEMAPHORE_EXTENSION_NAME",
            "waitSemaphoreCount = wait_count",
            "pWaitSemaphores = wait_count ? wait_semaphores : NULL",
            "pWaitDstStageMask = wait_count ? wait_stages : NULL",
            "signalSemaphoreCount = signal_count",
            "pSignalSemaphores = signal_count ? signal_semaphores : NULL",
            "VkTimelineSemaphoreSubmitInfo timeline_info",
            "timeline_info.pWaitSemaphoreValues = wait_count ? wait_values : NULL;",
            "timeline_info.pSignalSemaphoreValues = signal_count ? signal_values : NULL;",
            "rc = submit_vulkan_graphics_v6_command_buffer(\n        &g_vulkan_runtime, view, replay_command_buffer, &submit_diag);",
        ]:
            self.assertIn(marker, executor)
        self.assertIn("const int is_v619 = header->abi_minor == PDOCKER_GPU_VULKAN_GRAPHICS_V619_ABI_MINOR;", executor)
        self.assertIn("const int is_v618 = header->abi_minor == PDOCKER_GPU_VULKAN_GRAPHICS_V618_ABI_MINOR || is_v619;", executor)
        self.assertIn(
            "const int is_v619_header = header->abi_minor == PDOCKER_GPU_VULKAN_GRAPHICS_V619_ABI_MINOR;",
            executor,
        )
        self.assertIn(
            "const int is_v618_header = header->abi_minor == PDOCKER_GPU_VULKAN_GRAPHICS_V618_ABI_MINOR || is_v619_header;",
            executor,
        )

        for marker in [
            "append_submit_sync_entry",
            "collect_legacy_submit_sync_entries",
            "collect_submit2_submit_sync_entries",
            "set_submit_sync_override",
            "clear_submit_sync_override",
            "g_submit_sync_override_entries",
            "submit2-sync-metadata-overflow",
            "submit_timeline_info_from_pnext",
            "submit_timeline_wait_value",
            "submit_timeline_signal_value",
            "validate_submit_wait_semaphores",
            "PdockerGpuVulkanGraphicsV619SubmitSyncEntry submit_sync_entries[PDOCKER_GPU_VULKAN_GRAPHICS_V619_MAX_SUBMIT_SYNCS]",
            "collect_legacy_submit_sync_entries(&pSubmits[i], timeline_submit, fence",
            "if (g_submit_sync_override_entries)",
            "memcpy(submit_sync_entries, g_submit_sync_override_entries",
            "graphics_submit_sync_frame_bounds",
            "filter_submit_sync_entries_for_graphics_frame",
            "frame_submit_sync_entries",
            "send_recorded_vulkan_graphics_v6_1_frame(\n                        cmd, frame_submit_sync_entries, frame_submit_sync_count)",
            "execute_graphics_mixed_gpu_sequence(\n                    cmd, first_graphics_gpu_op, last_graphics_gpu_op,",
            "submit-sync-metadata-overflow",
            "VULKAN_GRAPHICS_V6.19",
            "need_v619_submit_sync",
            "header->abi_minor = PDOCKER_GPU_VULKAN_GRAPHICS_V619_ABI_MINOR;",
            "frame_header_v619->v619.submit_sync_entry_size = sizeof(PdockerGpuVulkanGraphicsV619SubmitSyncEntry);",
            "frame_header_v619->v619.submit_sync_schema_hash = PDOCKER_GPU_VULKAN_GRAPHICS_V619_SUBMIT_SYNC_SCHEMA_HASH;",
            "APPEND_GRAPHICS_TABLE(submit_syncs, submit_sync_count",
            "frame_header_v619->v619.extension_hash = frame_header_v619->v619.submit_sync_table_hash;",
            "entry->sync_type = sync_type;",
            "entry->stage_mask = stage_mask;",
            "uint64_t stage_mask = info ? (uint64_t)info->stageMask : 0;",
            "VkFence submit2_fence = (i + 1u == submitCount) ? fence : VK_NULL_HANDLE;",
            "entry->semaphore_id = sem->semaphore_id;",
            "entry->fence_id = fence->fence_id;",
            "PDOCKER_GPU_GRAPHICS_V619_SUBMIT_SYNC_WAIT",
            "PDOCKER_GPU_GRAPHICS_V619_SUBMIT_SYNC_SIGNAL",
            "PDOCKER_GPU_GRAPHICS_V619_SUBMIT_SYNC_FENCE",
            "PDOCKER_GPU_GRAPHICS_V619_SUBMIT_SYNC_TIMELINE",
            "PDOCKER_GPU_GRAPHICS_V619_SUBMIT_SYNC_BINARY",
        ]:
            self.assertIn(marker, icd)
        self.assertLess(icd.index("append_submit_sync_entry"), icd.index("collect_legacy_submit_sync_entries"))
        self.assertLess(
            icd.index("collect_legacy_submit_sync_entries"),
            icd.index("filter_submit_sync_entries_for_graphics_frame"),
        )
        submit_body = icd.split("VKAPI_ATTR VkResult VKAPI_CALL vkQueueSubmit", 1)[1].split(
            "VKAPI_ATTR VkResult VKAPI_CALL vkQueueSubmit2", 1
        )[0]
        self.assertIn("graphics_submit_sync_frame_bounds(&pSubmits[i]", submit_body)
        self.assertIn("j == first_graphics_submit_sync_cmd", submit_body)
        self.assertIn("j == last_graphics_submit_sync_cmd", submit_body)
        self.assertNotIn("send_recorded_vulkan_graphics_v6_1_frame(cmd, submit_sync_entries, submit_sync_count)", submit_body)

    def test_vulkan_executor_preserves_submit2_stage_masks_when_available(self):
        executor = GPU_EXECUTOR.read_text()

        for marker in [
            "PFN_vkQueueSubmit2 queue_submit2;",
            'vkGetDeviceProcAddr(rt->device, "vkQueueSubmit2")',
            'vkGetDeviceProcAddr(rt->device, "vkQueueSubmit2KHR")',
            "static VkPipelineStageFlags2 vulkan_submit_stage_mask2_or_all",
            "VkPipelineStageFlags2 wait_stage_masks2",
            "VkPipelineStageFlags2 signal_stage_masks2",
            "VkSemaphoreSubmitInfo wait_infos",
            "VkSemaphoreSubmitInfo signal_infos",
            "VkCommandBufferSubmitInfo command_info",
            "VkSubmitInfo2 submit2",
            ".stageMask = wait_stage_masks2[i];",
            ".stageMask = signal_stage_masks2[i];",
            "vrc = rt->queue_submit2(rt->graphics_queue, 1, &submit2, submit_fence);",
            "vulkan_legacy_submit_stage_mask_from_stage2",
        ]:
            self.assertIn(marker, executor)

        submit_body = executor.split("static int submit_vulkan_graphics_v6_command_buffer", 1)[1].split(
            "static int run_vulkan_graphics_v6_frame", 1
        )[0]
        self.assertLess(submit_body.index("if (rt->queue_submit2)"), submit_body.index("vkQueueSubmit(rt->graphics_queue"))
        self.assertLess(submit_body.index("VkSubmitInfo2 submit2"), submit_body.index("vrc = rt->queue_submit2"))
        self.assertNotIn("(VkPipelineStageFlags)sync->stage_mask", submit_body)

    def test_vulkan_semaphore_lifecycle_is_executor_backed(self):
        executor = GPU_EXECUTOR.read_text()
        icd = VULKAN_ICD.read_text()

        for marker in [
            "handle_vulkan_semaphore_command",
            "VULKAN_SEMAPHORE_CREATE",
            "VULKAN_SEMAPHORE_DESTROY",
            "VULKAN_SEMAPHORE_COUNTER",
            "VULKAN_SEMAPHORE_SIGNAL",
            "VULKAN_SEMAPHORE_WAIT",
            "print_vulkan_semaphore_result",
            "find_executor_submit_sync_entry",
            "allocate_executor_submit_sync_entry",
            "vkCreateSemaphore(rt->device, &create_info, NULL, &entry->semaphore)",
            "type_info.initialValue = timeline ? initial_value : 0;",
            "vkDestroySemaphore(rt->device, entry->semaphore, NULL)",
            "rt->get_semaphore_counter_value(rt->device, entry->semaphore, &value)",
            "rt->signal_semaphore(rt->device, &info)",
            "rt->wait_semaphores(rt->device, &info, timeout_ns)",
            "vkGetDeviceProcAddr(rt->device, \"vkGetSemaphoreCounterValue\")",
            "vkGetDeviceProcAddr(rt->device, \"vkWaitSemaphores\")",
            "vkGetDeviceProcAddr(rt->device, \"vkSignalSemaphore\")",
            "strncmp(cmd, \"VULKAN_SEMAPHORE_\", 17) == 0",
        ]:
            self.assertIn(marker, executor)

        for marker in [
            "bool executor_tracked;",
            "send_executor_semaphore_create",
            "send_executor_semaphore_destroy",
            "send_executor_semaphore_counter",
            "send_executor_semaphore_signal",
            "send_executor_semaphore_wait",
            "append_semaphore_wait_pairs",
            "VULKAN_SEMAPHORE_CREATE %llu %u %llu",
            "VULKAN_SEMAPHORE_DESTROY %llu",
            "VULKAN_SEMAPHORE_COUNTER %llu",
            "VULKAN_SEMAPHORE_SIGNAL %llu %llu",
            "VULKAN_SEMAPHORE_WAIT %u %llu %u",
            "parse_executor_json_u64_key",
            "send_executor_semaphore_create(sem)",
            "send_executor_semaphore_destroy(sem)",
            "send_executor_semaphore_counter(sem, &value, &result)",
            "send_executor_semaphore_wait(pWaitInfo, timeout, &result)",
            "send_executor_semaphore_signal(sem, pSignalInfo->value, &result)",
        ]:
            self.assertIn(marker, icd)

        create_body = icd.split("VKAPI_ATTR VkResult VKAPI_CALL vkCreateSemaphore", 1)[1].split(
            "VKAPI_ATTR void VKAPI_CALL vkDestroySemaphore", 1
        )[0]
        self.assertIn("bridge_available()", create_body)
        self.assertIn("send_executor_semaphore_create(sem)", create_body)
        wait_body = icd.split("VKAPI_ATTR VkResult VKAPI_CALL vkWaitSemaphores", 1)[1].split(
            "VKAPI_ATTR VkResult VKAPI_CALL vkSignalSemaphore", 1
        )[0]
        self.assertIn("executor_waitable = sem && sem->executor_tracked && sem->timeline;", wait_body)

    def test_vulkan_fence_lifecycle_is_executor_backed(self):
        executor = GPU_EXECUTOR.read_text()
        icd = VULKAN_ICD.read_text()

        for marker in [
            "handle_vulkan_fence_command",
            "VULKAN_FENCE_CREATE",
            "VULKAN_FENCE_RESET",
            "VULKAN_FENCE_STATUS",
            "VULKAN_FENCE_SIGNAL",
            "VULKAN_FENCE_WAIT",
            "VULKAN_FENCE_DESTROY",
            "print_vulkan_fence_result",
            "collect_executor_fences_from_command",
            "find_executor_submit_fence_entry",
            "allocate_executor_submit_fence_entry",
            "vkCreateFence(rt->device, &create_info, NULL, &entry->fence)",
            "create_info.flags = initial_signaled ? VK_FENCE_CREATE_SIGNALED_BIT : 0;",
            "vkResetFences(rt->device, fence_count, fences)",
            "vkGetFenceStatus(rt->device, entry->fence)",
            "vkWaitForFences(rt->device, fence_count, fences, wait_all ? VK_TRUE : VK_FALSE, timeout_ns)",
            "vkDestroyFence(rt->device, entry->fence, NULL)",
            "strncmp(cmd, \"VULKAN_FENCE_\", 13) == 0",
        ]:
            self.assertIn(marker, executor)

        for marker in [
            "bool executor_tracked;",
            "send_executor_text_command",
            "send_executor_fence_create",
            "send_executor_fence_destroy",
            "send_executor_fence_reset",
            "send_executor_fence_status",
            "send_executor_fence_signal",
            "send_executor_fence_wait",
            "VULKAN_FENCE_CREATE %llu %u",
            "VULKAN_FENCE_RESET %u",
            "VULKAN_FENCE_STATUS %llu",
            "VULKAN_FENCE_SIGNAL %llu",
            "VULKAN_FENCE_WAIT %u %llu %u",
            "VULKAN_FENCE_DESTROY %llu",
            "bridge_available()",
            "send_executor_fence_create(fence, initial_signaled)",
            "send_executor_fence_destroy(f)",
            "send_executor_fence_reset(fenceCount, pFences)",
            "send_executor_fence_status(f, &result)",
            "send_executor_fence_signal(submit_fence)",
            "send_executor_fence_wait(fenceCount, pFences, waitAll, timeout, &result)",
        ]:
            self.assertIn(marker, icd)

        fence_body = icd.split("VKAPI_ATTR VkResult VKAPI_CALL vkGetFenceStatus", 1)[1].split(
            "VKAPI_ATTR VkResult VKAPI_CALL vkWaitForFences", 1
        )[0]
        self.assertIn("if (!f || f->signaled) return VK_SUCCESS;", fence_body)
        self.assertIn("if (f->executor_tracked)", fence_body)
        self.assertNotIn("return (!f || f->signaled) ? VK_SUCCESS : VK_NOT_READY;", fence_body)

        wait_body = icd.split("VKAPI_ATTR VkResult VKAPI_CALL vkWaitForFences", 1)[1].split(
            "static bool semaphore_create_info_parse_pnext", 1
        )[0]
        self.assertIn("fences_wait_satisfied(fenceCount, pFences, waitAll)", wait_body)
        self.assertLess(
            wait_body.index("fences_wait_satisfied(fenceCount, pFences, waitAll)"),
            wait_body.index("send_executor_fence_wait(fenceCount, pFences, waitAll, timeout, &result)"),
        )
        reset_body = icd.split("VKAPI_ATTR VkResult VKAPI_CALL vkResetFences", 1)[1].split(
            "VKAPI_ATTR VkResult VKAPI_CALL vkGetFenceStatus", 1
        )[0]
        self.assertIn("if (fence) fence->signaled = false;", reset_body)

    def test_vulkan_icd_supports_query_pool_and_timestamp_api(self):
        icd = VULKAN_ICD.read_text()
        self.assertIn("struct PdockerVkQueryPool", icd)
        self.assertIn("PDOCKER_VK_MAX_QUERY_COUNT", icd)
        for marker in [
            "PDOCKER_VK_COMMAND_QUERY_BEGIN",
            "PDOCKER_VK_COMMAND_QUERY_END",
            "PDOCKER_VK_COMMAND_QUERY_RESET",
            "PDOCKER_VK_COMMAND_QUERY_TIMESTAMP",
        ]:
            self.assertIn(marker, icd)
        for name in [
            "vkCreateQueryPool",
            "vkDestroyQueryPool",
            "vkCmdBeginQuery",
            "vkCmdEndQuery",
            "vkCmdResetQueryPool",
            "vkResetQueryPool",
            "vkGetQueryPoolResults",
            "vkCmdWriteTimestamp",
            "vkCmdWriteTimestamp2",
        ]:
            self.assertIn(name, icd)
            self.assertIn(f"MAP_PROC({name});", icd)
        self.assertIn('MAP_ALIAS("vkCmdWriteTimestamp2KHR", vkCmdWriteTimestamp2);', icd)
        self.assertIn("VK_QUERY_TYPE_TIMESTAMP", icd)
        self.assertIn("VK_QUERY_TYPE_OCCLUSION", icd)
        self.assertIn("query-type-unsupported", icd)
        self.assertIn("VK_QUERY_RESULT_64_BIT", icd)
        self.assertIn("VK_QUERY_RESULT_WITH_AVAILABILITY_BIT", icd)
        self.assertIn("VK_QUERY_RESULT_WAIT_BIT", icd)
        self.assertIn("VK_QUERY_RESULT_PARTIAL_BIT", icd)
        self.assertIn("execute_recorded_query_op(op)", icd)
        self.assertIn("pool->result_entries[q].available", icd)
        self.assertIn("monotonic_ns()", icd)
        self.assertIn("timestampComputeAndGraphics = VK_TRUE;", icd)
        self.assertIn("timestampPeriod = 1.0f;", icd)
        self.assertIn("timestampValidBits = 64;", icd)
        host_body = icd.split("static bool command_op_is_host_transfer_or_layout_op", 1)[1].split(
            "static VkResult execute_recorded_host_transfer_or_layout_op", 1
        )[0]
        replay_body = icd.split("static VkResult execute_recorded_host_transfer_or_layout_op", 1)[1].split(
            "static bool graphics_mixed_submit_plan", 1
        )[0]
        for marker in [
            "case PDOCKER_VK_COMMAND_QUERY_BEGIN:",
            "case PDOCKER_VK_COMMAND_QUERY_END:",
            "case PDOCKER_VK_COMMAND_QUERY_RESET:",
            "case PDOCKER_VK_COMMAND_QUERY_TIMESTAMP:",
        ]:
            self.assertIn(marker, host_body)
            self.assertIn(marker, replay_body)

    def test_vulkan_icd_tracks_image_layout_barriers_and_transfer_layouts(self):
        icd = VULKAN_ICD.read_text()
        self.assertIn("VkImageLayout current_layout;", icd)
        self.assertIn("uint64_t layout_generation;", icd)
        self.assertIn("bool layout_mixed;", icd)
        self.assertIn("bool layout_range_overflow;", icd)
        self.assertIn("PdockerVkImageLayoutRange layout_ranges[PDOCKER_VK_MAX_COPY_OPS];", icd)
        self.assertIn("uint32_t layout_range_count;", icd)
        self.assertIn("PdockerVkImageBarrierOp", icd)
        self.assertIn("PDOCKER_VK_COMMAND_IMAGE_BARRIER", icd)
        self.assertIn("image_barrier_ops[PDOCKER_VK_MAX_COPY_OPS]", icd)
        self.assertIn("execute_recorded_image_barrier_op", icd)
        self.assertIn("record_image_barrier_op", icd)
        self.assertIn("image_format_full_aspect_mask", icd)
        self.assertIn("image_subresource_range_is_whole_image", icd)
        self.assertIn("range->aspectMask != full_aspects", icd)
        self.assertIn("trace_image_layout_mismatch", icd)
        self.assertIn("image->current_layout = pCreateInfo->initialLayout;", icd)
        self.assertIn("image->layout_generation = next_vulkan_object_generation();", icd)
        self.assertIn("image->layout_mixed = false;", icd)
        self.assertIn("clear_image_layout_ranges", icd)
        self.assertIn("image_layout_ranges_equal", icd)
        self.assertIn("image_layout_range_append", icd)
        self.assertIn("append_image_layout_range_remainder", icd)
        self.assertIn("old_aspects_not_replaced", icd)
        self.assertIn("overlap_aspects", icd)
        self.assertIn("middle_level_count", icd)
        self.assertIn("update_image_layout_range_cache", icd)
        self.assertIn("memcpy(image->layout_ranges, rebuilt, sizeof(rebuilt));", icd)
        self.assertIn("image->layout_range_count = rebuilt_count;", icd)
        self.assertIn("normalize_image_subresource_range(op->image, &op->range, &normalized_range)", icd)
        self.assertIn("op->image->layout_range_overflow = true;", icd)
        self.assertIn("image->layout_range_overflow = true;", icd)
        self.assertNotIn("Splitting partially-overlapping layout ranges is required", icd)
        updater_body = c_function_body(icd, "update_image_layout_range_cache")
        self.assertIn("append_image_layout_range_remainder", updater_body)
        self.assertNotIn("image_layout_ranges_overlap(&entry->range, normalized_range)) {", updater_body)
        self.assertNotIn("silently lying about uncovered subresources", updater_body)
        barrier_body = icd.split("VKAPI_ATTR void VKAPI_CALL vkCmdPipelineBarrier(", 1)[1].split(
            "VKAPI_ATTR void VKAPI_CALL vkCmdCopyBuffer", 1
        )[0]
        self.assertIn("pImageMemoryBarriers && i < imageMemoryBarrierCount", barrier_body)
        self.assertIn("record_image_barrier_op(commandBuffer", barrier_body)
        record_image_barrier_body = icd.split("static void record_image_barrier_op", 1)[1].split(
            "static void record_memory_barrier_op", 1
        )[0]
        self.assertIn("cmd->graphics_unsupported = true;", record_image_barrier_body)
        self.assertIn("command_buffer_mark_recording_failed(cmd, \"image-barrier-record-overflow\")", record_image_barrier_body)
        self.assertIn("submit will fail closed", record_image_barrier_body)
        self.assertIn("dependencyFlags & ~VK_DEPENDENCY_BY_REGION_BIT", barrier_body)
        self.assertIn("record.flags = dependencyFlags & VK_DEPENDENCY_BY_REGION_BIT", barrier_body)
        barrier2_body = icd.split("VKAPI_ATTR void VKAPI_CALL vkCmdPipelineBarrier2", 1)[1].split(
            "VKAPI_ATTR void VKAPI_CALL vkCmdSetEvent2", 1
        )[0]
        dependency_barrier_body = c_function_body(icd, "record_dependency_info_barrier_ops")
        self.assertIn("info->pImageMemoryBarriers", dependency_barrier_body)
        self.assertIn("const VkImageMemoryBarrier2 *b", dependency_barrier_body)
        self.assertIn("record_image_barrier_op(commandBuffer", dependency_barrier_body)
        self.assertIn("record_dependency_info_barrier_ops(commandBuffer, pDependencyInfo)", barrier2_body)
        self.assertIn("legacy_pipeline_barrier_inputs_unsupported", icd)
        self.assertIn("command_buffer_mark_recording_failed(cmd, \"legacy-pipeline-barrier-unsupported\")", barrier_body)
        self.assertIn("srcStageMask == 0 || dstStageMask == 0", icd)
        self.assertIn("pMemoryBarriers[i].pNext", icd)
        self.assertIn("pBufferMemoryBarriers[i].pNext", icd)
        self.assertIn("pImageMemoryBarriers[i].pNext", icd)
        self.assertIn("dependency_info_has_unsupported_pnext", icd)
        self.assertIn("sync2_stage_access_pair_invalid", icd)
        self.assertIn("VK_PIPELINE_STAGE_2_NONE", icd)
        self.assertIn("if (info->pNext) return true;", icd)
        self.assertIn("info->memoryBarrierCount && !info->pMemoryBarriers", icd)
        self.assertIn("info->bufferMemoryBarrierCount && !info->pBufferMemoryBarriers", icd)
        self.assertIn("info->imageMemoryBarrierCount && !info->pImageMemoryBarriers", icd)
        self.assertIn("info->pMemoryBarriers[i].pNext", icd)
        self.assertIn("info->pBufferMemoryBarriers[i].pNext", icd)
        self.assertIn("info->pImageMemoryBarriers[i].pNext", icd)
        self.assertIn("dependency_info_has_unsupported_pnext(pDependencyInfo)", barrier2_body)
        self.assertIn("cmd->graphics_unsupported = true;", barrier2_body)
        self.assertLess(barrier2_body.index("dependency_info_has_unsupported_pnext(pDependencyInfo)"), barrier2_body.index("VkDependencyFlags dependency_flags"))
        self.assertIn("VkDependencyFlags dependency_flags", barrier2_body)
        self.assertIn("dependency_flags & ~VK_DEPENDENCY_BY_REGION_BIT", barrier2_body)
        self.assertIn("record.flags = dependency_flags & VK_DEPENDENCY_BY_REGION_BIT", barrier2_body)
        self.assertNotIn("vkCmdPipelineBarrier(commandBuffer", barrier2_body)
        self.assertIn("execute_recorded_image_barrier_op(", icd)
        for field in [
            "VkImageLayout image_layout;",
            "VkImageLayout src_layout;",
            "VkImageLayout dst_layout;",
        ]:
            self.assertIn(field, icd)
        for stage in [
            '"descriptor-update"',
            '"copy-buffer-to-image"',
            '"copy-image-to-buffer"',
            '"copy-image-src"',
            '"copy-image-dst"',
            '"clear-color-image"',
            '"resolve-image-src"',
            '"resolve-image-dst"',
            '"blit-image-src"',
            '"blit-image-dst"',
            '"clear-depth-stencil-image"',
        ]:
            self.assertIn(stage, icd)

    def test_vulkan_non_storage_descriptors_fail_closed_until_v5_transport(self):
        icd = VULKAN_ICD.read_text()
        self.assertIn("descriptor_type_supported_by_v4_transport", icd)
        self.assertIn("descriptor_type_supported_by_v5_object_transport", icd)
        self.assertIn("unsupported_descriptor_type", icd)
        self.assertIn("descriptor type binding=%u type=%u is unsupported by current transport", icd)
        self.assertIn("descriptor write binding=%u type=%u is unsupported by current transport", icd)
        self.assertIn("descriptor binding=%u exceeds V4 transport limit=%u", icd)
        self.assertIn("layout->unsupported_descriptor_type = true;", icd)
        self.assertIn("set->unsupported_descriptor_type = true;", icd)
        update_body = icd.split("VKAPI_ATTR void VKAPI_CALL vkUpdateDescriptorSets", 1)[1].split(
            "VKAPI_ATTR VkResult VKAPI_CALL vkCreateShaderModule", 1
        )[0]
        self.assertNotIn("w->descriptorType != VK_DESCRIPTOR_TYPE_STORAGE_BUFFER &&", update_body)
        self.assertIn("!v4_descriptor && !v5_object_descriptor", update_body)
        bind_body = icd.split("VKAPI_ATTR void VKAPI_CALL vkCmdBindDescriptorSets", 1)[1].split(
            "VKAPI_ATTR void VKAPI_CALL vkCmdPushConstants", 1
        )[0]
        self.assertIn("set->unsupported_descriptor_type", bind_body)
        self.assertIn("set->layout->unsupported_descriptor_type", bind_body)

    def test_llama_gpu_compare_q6_identity_survives_spirv_legalization_hash_changes(self):
        helpers = load_llama_gpu_compare_q6_helpers()
        q6_hash = sorted(helpers["Q6_K_MATVEC_SPIRV_HASHES"])[0]
        non_q6_hash = "0x0000000000000001"

        source_original_event = {
            "source_spirv_hash": q6_hash,
            "effective_spirv_hash": non_q6_hash,
        }
        self.assertEqual(
            [q6_hash, non_q6_hash],
            helpers["event_spirv_identity_hashes"](source_original_event),
        )
        self.assertTrue(helpers["event_has_q6_matvec_identity"](source_original_event))

        legacy_event = {
            "spirv_hash": q6_hash.upper(),
        }
        self.assertEqual(
            [q6_hash],
            helpers["event_spirv_identity_hashes"](legacy_event),
        )
        self.assertTrue(helpers["event_has_q6_matvec_identity"](legacy_event))

        empty_and_non_string_event = {
            "source_spirv_hash": "",
            "spirv_hash": 0,
            "effective_spirv_hash": ["not", "a", "hash"],
        }
        self.assertFalse(helpers["event_has_q6_matvec_identity"](empty_and_non_string_event))

        duplicate_event = {
            "source_spirv_hash": q6_hash,
            "spirv_hash": q6_hash.upper(),
            "effective_spirv_hash": q6_hash,
        }
        duplicate_hashes = helpers["event_spirv_identity_hashes"](duplicate_event)
        self.assertEqual([q6_hash, q6_hash, q6_hash], duplicate_hashes)
        self.assertTrue(helpers["event_has_q6_matvec_identity"](duplicate_event))
        self.assertFalse(helpers["event_has_q6_matvec_identity"]({}))
        self.assertFalse(helpers["event_has_q6_matvec_identity"](None))

        probe_hash = "0x3f14f34b0679040e"
        helpers["effective_runtime_env"] = {
            "PDOCKER_GPU_SPIRV_PROBE_EXPECTED_HASH": q6_hash,
            "PDOCKER_GPU_SPIRV_PROBE_EFFECTIVE_HASH": probe_hash,
        }
        probe_event = {
            "source_spirv_hash": probe_hash,
            "effective_spirv_hash": "0x579577e98a3af80f",
        }
        self.assertEqual(
            [probe_hash, q6_hash, "0x579577e98a3af80f"],
            helpers["event_spirv_identity_hashes"](probe_event),
        )
        self.assertTrue(helpers["event_has_q6_matvec_identity"](probe_event))

        helpers["effective_runtime_env"] = {
            "PDOCKER_GPU_SPIRV_PROBE_EXPECTED_HASH": non_q6_hash,
            "PDOCKER_GPU_SPIRV_PROBE_EFFECTIVE_HASH": probe_hash,
        }
        self.assertFalse(helpers["event_has_q6_matvec_identity"](probe_event))

    def test_llama_gpu_compare_keeps_q6_probe_diagnostics_distinct_from_oracle(self):
        source = LLAMA_COMPARE.read_text()
        self.assertIn("q6_probe_events = [", source)
        self.assertIn('"q6_probe_event_count": len(q6_probe_events)', source)
        self.assertIn('"q6-probe-writeback-cleared-oracle-missing"', source)
        self.assertIn("q6-probe-writeback-cleared-but-source-oracle-not-available-for-instrumented-module", source)
        self.assertIn("and event_has_q6_matvec_identity(e)", source)

    def test_executor_cpu_oracle_uses_fail_closed_probe_source_identity(self):
        source = GPU_EXECUTOR.read_text()
        for token in [
            "source_spirv_hash=",
            "original_spirv_hash=",
            "probe_expected_spirv_hash=",
            "effective_spirv_hash=",
            "instrumented_spirv_hash=",
            "effective_probe_shader_hash=",
        ]:
            self.assertIn(token, source)
        self.assertIn("resolve_cpu_oracle_spirv_identity", source)
        self.assertIn("options->has_source_spirv_hash", source)
        self.assertIn("options->has_effective_spirv_hash", source)
        self.assertIn("options->sender_reconcile.has_spirv_hash", source)
        self.assertIn("effective_relation_hash != received_spirv_hash", source)
        self.assertIn("!options->has_spirv_probe_debug_binding", source)
        self.assertIn("options->spirv_probe_debug_binding > UINT32_MAX", source)
        self.assertIn("spirv-source-identity-effective-mismatch", source)
        self.assertIn("spirv-source-identity-unverified-effective-hash", source)
        self.assertIn("spirv-source-identity-without-probe-binding", source)
        self.assertIn("uint64_t cpu_oracle_spirv_hash = original_spirv_hash;", source)
        self.assertIn("cpu_oracle_spirv_hash_source = \"received\"", source)
        self.assertIn('*oracle_spirv_hash_source = "probe-source-identity"', source)
        self.assertIn("is_q6k_matvec_oracle_hash(cpu_oracle_spirv_hash)", source)
        self.assertIn("static int is_q6k_matvec_oracle_hash", source)
        self.assertIn("is_q6k_matvec_hash(spirv_hash)", source)
        self.assertIn("run_cpu_oracle_q6k_matvec_sample(&cpu_oracle_report,\n                                         cpu_oracle_spirv_hash", source)
        self.assertIn("resolve_spirv_local_size(&spirv_summary", source)
        self.assertNotIn("} else if (is_q6k_matvec_hash(spirv_summary.hash))", source)
        self.assertIn('\\"oracle_spirv_hash\\":\\"0x%016llx\\"', source)
        self.assertIn('\\"oracle_spirv_hash_source\\":\\"%s\\"', source)

    def test_llama_gpu_lane_marker_and_scope_are_pinned(self):
        source = GPU_EXECUTOR.read_text()
        marker = re.search(
            r'#define PDOCKER_GPU_EXECUTOR_BUILD_MARKER "([^"]+)"',
            source,
        ).group(1)
        self.assertEqual(marker, "gpu-executor-q6-readonly-snapshot-20260531")
        stale = "gpu-executor-" + "float16-cap-diagnostic-20260520"
        for path in [
            GPU_EXECUTOR,
            LLAMA_COMPARE,
            ROOT / "tests" / "test_gpu_abi_contract.py",
            ROOT / "tests" / "test_llama_gpu_artifact_verifier.py",
            ROOT / "tests" / "test_llama_gpu_artifact_sweep.py",
            LLAMA_GPU_NEXT_STEPS,
        ]:
            text = path.read_text()
            self.assertIn(marker, text)
            self.assertNotIn(stale, text)
        compare = LLAMA_COMPARE.read_text()
        self.assertNotIn(f'EXPECTED_GPU_EXECUTOR_MARKER="${{PDOCKER_GPU_EXECUTOR_EXPECTED_MARKER:-{marker}}}"', compare)
        self.assertIn(f"PDOCKER_GPU_EXECUTOR_EXPECTED_MARKER={{os.environ.get('PDOCKER_GPU_EXECUTOR_EXPECTED_MARKER', '{marker}')}}", compare)
        self.assertIn(f'expected_executor_marker = os.environ.get("PDOCKER_GPU_EXECUTOR_EXPECTED_MARKER", "{marker}")', compare)
        self.assertIn('"llama_cpp_modified": False', compare)
        self.assertIn('"gpu_entry": "standard Vulkan loader through the Skydnir Vulkan ICD"', compare)

        diff = subprocess.run(
            ["git", "diff", "--name-only"],
            cwd=ROOT,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.splitlines()
        # This guard protects the llama GPU benchmark lane from silently
        # changing its workload definition.  It must not block unrelated
        # project-library template maintenance, because that turns a focused
        # anti-regression tripwire into a repo-wide Dockerfile freeze.
        llama_workload_paths = {
            "app/src/main/assets/project-library/llama-cpp-gpu/Dockerfile",
            "app/src/main/assets/project-library/llama-cpp-gpu/compose.yaml",
        }
        forbidden_changed_paths = [
            path for path in diff
            if "llama.cpp" in path
            or path in llama_workload_paths
        ]
        self.assertEqual([], forbidden_changed_paths)

    def test_q4k_callsite_handoff_records_required_evidence(self):
        doc = LLAMA_GPU_NEXT_STEPS.read_text()
        for evidence in [
            "gpu-executor-q6-readonly-snapshot-20260531",
            "mul_mat_vec_q4_k_f32_f32",
            "vulkan-shaders/mul_mat_vec_q4_k.comp",
            "vk_mat_vec_push_constants",
            "A/B/D/Fuse0/Fuse1",
            "0xf3cd7d18f0276b42",
            "0x853c49b4900eed3c",
            "0x22ab0152b230e983",
            "explicit diagnostic override",
            "not a benchmarkable product optimization",
            "without changing llama.cpp",
            "Dockerfile",
        ]:
            self.assertIn(evidence, doc)

    def test_spirv_local_size_patch_uses_requested_spec_workgroup_evidence(self):
        source = GPU_EXECUTOR.read_text()
        self.assertIn("local_size_spec_id[3]", source)
        self.assertIn("local_size_spec_id_valid[3]", source)
        self.assertIn("op == 331 && word_count >= 6 && code[i + 2] == 38", source)
        self.assertIn("op == 71 && word_count >= 4 && code[i + 2] == 1", source)
        self.assertIn("summary->local_size_spec_id_valid[i]", source)
        self.assertIn("summary->local_size_spec_id[i]", source)
        self.assertIn("summary->workgroup_size_spec_id_valid[0]", source)
        self.assertIn("summary->workgroup_size_spec_id[0]", source)
        self.assertIn('\\"spirv_local_size_spec_id\\":[%u,%u,%u]', source)
        function = re.search(
            r"static int patch_spirv_literal_local_size_from_spec\(.*?\n}\n\nstatic int spirv_has_capability",
            source,
            re.S,
        )
        self.assertIsNotNone(function)
        body = function.group(0)
        self.assertIn("summarize_spirv(code, bytes)", body)
        self.assertIn("summary.local_size_spec_id_valid[dim]", body)
        self.assertIn("summary.local_size_spec_id[dim]", body)
        self.assertIn("summary.workgroup_size_spec_id_valid[dim]", body)
        self.assertIn("summary.workgroup_size_spec_id[dim]", body)
        self.assertNotIn("code[i + 2] == 11 && code[i + 3] == 25", body)
        self.assertNotIn("dim,", body)
        self.assertIn("specialized BuiltIn WorkgroupSize value", source)
        self.assertIn("does not replace kernels", source)
        self.assertIn("value > 1024", body)
        self.assertIn("invocation_count > 1024", body)
        self.assertNotIn("local_size[0] != 32", body)
        summarize = re.search(
            r"static SpirvTraceSummary summarize_spirv\(.*?\n}\n\nstatic void log_vulkan_feature_trace",
            source,
            re.S,
        )
        self.assertIsNotNone(summarize)
        self.assertNotIn("calloc", summarize.group(0))
        self.assertNotIn("free(", summarize.group(0))

    def test_q6_spec_ids_1_2_are_rows_cols_not_workgroup_yz(self):
        executor = GPU_EXECUTOR.read_text()
        compare = LLAMA_COMPARE.read_text()
        artifact_verifier = LLAMA_GPU_ARTIFACT_VERIFIER.read_text()
        q6_planner = LLAMA_Q6_PREFLIGHT_PLANNER.read_text()
        q6_plan_verifier = LLAMA_Q6_PLAN_VERIFIER.read_text()

        patch_function = re.search(
            r"static int patch_spirv_literal_local_size_from_spec\(.*?\n}\n\nstatic int spirv_has_capability",
            executor,
            re.S,
        )
        self.assertIsNotNone(patch_function)
        patch_body = patch_function.group(0)
        self.assertIn("summary.workgroup_size_spec_id_valid[dim]", patch_body)
        self.assertIn("summary.workgroup_size_spec_id[dim]", patch_body)
        self.assertNotRegex(
            patch_body,
            r"specialization_value_for_id\([^;]*(?:,\s*1\s*,|,\s*2\s*,)",
        )
        self.assertNotIn("local_size[1] = resolved_spec1", patch_body)
        self.assertNotIn("local_size[2] = resolved_spec2", patch_body)
        self.assertNotIn("code[i + 4] = (uint32_t)resolved_spec1", patch_body)
        self.assertNotIn("code[i + 5] = (uint32_t)resolved_spec2", patch_body)

        self.assertIn(
            "const uint32_t q6_num_rows = q6k_specialization_or_default_u32(\n"
            "        specializations, specialization_count, specialization_data, specialization_data_size,\n"
            "        1, 1u);",
            executor,
        )
        self.assertIn(
            "const uint32_t q6_num_cols = q6k_specialization_or_default_u32(\n"
            "        specializations, specialization_count, specialization_data, specialization_data_size,\n"
            "        2, 1u);",
            executor,
        )
        self.assertIn('\\"q6_num_rows\\":%llu', executor)
        self.assertIn('\\"q6_num_cols\\":%llu', executor)

        self.assertIn("q6_workgroup_specialization_interpretation", compare)
        self.assertIn('"spec_id_1": "Q6 row-count/data-loop dimension; not WorkgroupSize.y"', compare)
        self.assertIn('"spec_id_2": "Q6 column/count dimension; not WorkgroupSize.z"', compare)
        self.assertIn('"do_not_patch_local_size_y_from_spec_id_1": True', compare)
        self.assertIn('"do_not_patch_local_size_z_from_spec_id_2": True', compare)

        for evidence in (
            "q6_workgroup_specialization_interpretation",
            "q6_num_rows",
            "q6_num_cols",
        ):
            self.assertIn(f'"{evidence}"', q6_planner)
        self.assertIn("def missing_evidence_fields", q6_plan_verifier)
        self.assertIn("evidence_field_present(data, field)", q6_plan_verifier)
        self.assertIn('q6_num_rows = q6.get("q6_num_rows")', artifact_verifier)
        self.assertIn('q6_num_cols = q6.get("q6_num_cols")', artifact_verifier)
        self.assertIn("local_size[1] == q6_num_rows", artifact_verifier)
        self.assertIn("local_size[2] == q6_num_cols", artifact_verifier)

    def test_vulkan_duplicate_binding_rewrite_avoids_passed_bindings(self):
        source = GPU_EXECUTOR.read_text()
        self.assertIn("const VulkanDispatchBinding *bindings", source)
        self.assertIn("size_t binding_count", source)
        self.assertIn("used[bindings[i].binding] = 1;", source)
        self.assertIn("descriptor_sets[code[i + 1]] = code[i + 3];", source)
        self.assertIn("has_descriptor_set[code[i + 1]] = 1;", source)
        self.assertIn("!has_descriptor_set[code[i + 1]] || descriptor_sets[code[i + 1]] != 0", source)
        self.assertIn("rewrite_duplicate_descriptor_bindings(\n                shader_code,\n                shader_size,\n                bindings,\n                binding_count,", source)
        self.assertIn("strict descriptor ABI normalization transform", source)
        self.assertIn("legacy_duplicate_descriptor_rewrite", source)
        self.assertIn("strict_duplicate_descriptor_normalization", source)
        self.assertIn("PDOCKER_GPU_STRICT_DUPLICATE_DESCRIPTOR_NORMALIZATION", source)
        self.assertIn("strict duplicate descriptor normalization requires full reconciliation", source)
        self.assertIn("strict duplicate descriptor normalization cannot mix with safe kernels", source)
        self.assertIn("if (q6k_safe_kernel_requested || q4k_safe_kernel_requested)", source)
        self.assertIn("!strict_passthrough &&\n        (options && options->has_rewrite_duplicate_descriptors", source)
        self.assertIn("for (size_t i = 0; i < binding_alias_count; ++i)", source)
        self.assertIn("binding_aliases[i].rewritten_binding + 1", source)
        self.assertIn("set_binding_counts[alias_set] = needed;", source)
        self.assertIn("writes[write_count].dstSet = descriptor_sets[binding_aliases[i].descriptor_set];", source)

    def test_vulkan_reconciliation_dispatch_hash_matches_icd_shape(self):
        executor = GPU_EXECUTOR.read_text()
        icd = VULKAN_ICD.read_text()
        self.assertIn("dispatch_hash = fnv1a64_update_u32(dispatch_hash, op->dispatch_indirect ? 1u : 0u);", icd)
        self.assertIn("dispatch_hash = fnv1a64_update_u64(dispatch_hash, pdocker_vk_buffer_object_id(op->dispatch_indirect_buffer));", icd)
        self.assertIn("dispatch_hash = fnv1a64_update_u64(dispatch_hash, (uint64_t)op->dispatch_indirect_offset);", icd)
        self.assertIn("static uint64_t reconcile_dispatch_hash_for_options", executor)
        self.assertIn("uint32_t dispatch_indirect", executor)
        self.assertIn("uint64_t dispatch_indirect_buffer_id", executor)
        self.assertIn("uint64_t dispatch_indirect_offset", executor)
        self.assertIn("hash = fnv1a64_update(hash, &dispatch_indirect, sizeof(dispatch_indirect));", executor)
        self.assertIn("hash = fnv1a64_update(hash, &dispatch_indirect_buffer_id, sizeof(dispatch_indirect_buffer_id));", executor)
        self.assertIn("hash = fnv1a64_update(hash, &dispatch_indirect_offset, sizeof(dispatch_indirect_offset));", executor)
        self.assertIn("VULKAN_DISPATCH_V4 evidence hashes the dispatch shape", executor)
        self.assertIn("sender->dispatch_hash != reconcile_dispatch_hash_for_options", executor)

    def test_strict_graph_cache_is_budgeted_before_large_allocations(self):
        source = GPU_EXECUTOR.read_text()
        self.assertIn("PDOCKER_GPU_STRICT_GRAPH_CACHE_DEFAULT_MAX_BYTES", source)
        self.assertIn("PDOCKER_GPU_STRICT_GRAPH_CACHE_MAX_BYTES", source)
        self.assertIn("static void enforce_strict_graph_cache_budget", source)
        self.assertIn("evict_one_strict_graph_cache_lru", source)
        self.assertIn("strict_graph_cache_admission_allowed(strict_object_graph_cache_bytes, options)", source)
        self.assertIn("enforce_strict_graph_cache_budget(rt->device, strict_object_graph_cache_bytes, options);", source)
        self.assertIn('cache_budget_bytes', source)
        create_pos = source.index("enforce_strict_graph_cache_budget(rt->device, strict_object_graph_cache_bytes, options);")
        alloc_pos = source.index("create_strict_vulkan_object_graph", create_pos)
        self.assertLess(create_pos, alloc_pos)

    def test_strict_graph_uses_descriptor_window_for_device_local_dispatches(self):
        source = GPU_EXECUTOR.read_text()
        self.assertIn("static int strict_descriptor_window_compaction_enabled", source)
        self.assertIn("descriptor_window_compacted", source)
        self.assertIn("transport_buffer_size", source)
        self.assertIn("descriptor_window_transport_buffer_bytes", source)
        self.assertIn("descriptor_window_avoided_buffer_bytes", source)
        self.assertIn("const VkDeviceSize create_size = (VkDeviceSize)(", source)
        self.assertIn("? buffers[b].transport_buffer_size", source)
        self.assertIn(": buffers[b].api_buffer_size", source)
        self.assertIn("memories[mem_index].size = compact_descriptor_window ? 0 : bindings[i].api_memory_size;", source)
        self.assertIn("const int compact_descriptor_window = strict_descriptor_window_compaction_enabled", source)
        self.assertIn("cache_memory_bytes = (size_t)descriptor_end;", source)
        self.assertIn("byte_memory_sizes[byte_memory_count] = cache_memory_bytes;", source)
        self.assertIn("buffers[buffer_index].memory_offset = compact_descriptor_window", source)
        self.assertIn("? 0", source)
        self.assertNotIn("VkMemoryRequirements staging_req;\n                VkMemoryRequirements staging_req;", source)

    def test_large_strict_staging_transfer_uses_host_visible_memory(self):
        source = GPU_EXECUTOR.read_text()
        self.assertIn("PDOCKER_GPU_STRICT_DEVICE_LOCAL_STAGING_MAX_TRANSFER_BYTES", source)
        self.assertIn("static size_t strict_device_local_staging_max_transfer_bytes", source)
        self.assertIn("static size_t strict_memory_staging_transfer_bytes", source)
        self.assertIn("staging_transfer_bytes <= staging_transfer_limit", source)
        self.assertIn("stage_device_local_for_memory", source)
        self.assertIn("VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT", source)
        self.assertIn("memories[m].device_local_staged = stage_device_local_for_memory ? 1 : 0;", source)

    def test_vulkan_feature_contract_matches_android_subset(self):
        icd = VULKAN_ICD.read_text()
        executor = GPU_EXECUTOR.read_text()
        self.assertIn('env_truthy_default("PDOCKER_VULKAN_ENABLE_8BIT_STORAGE", true)', icd)
        self.assertIn("p->uniformAndStorageBuffer16BitAccess = VK_FALSE;", icd)
        self.assertIn("p->storagePushConstant16 = VK_FALSE;", icd)
        self.assertIn("p->shaderInt8 = storage8;", icd)
        self.assertIn("spirv_required_feature_mask", executor)
        self.assertIn("PDOCKER_VK_FEATURE_STORAGE_BUFFER_8", executor)
        self.assertIn("PDOCKER_VK_FEATURE_UNIFORM_STORAGE_BUFFER_8", executor)
        self.assertIn("PDOCKER_VK_FEATURE_STORAGE_PUSH_CONSTANT_8", executor)
        self.assertIn("PDOCKER_VK_FEATURE_SHADER_INT8", executor)
        self.assertIn("requires_storage16_uniform", executor)
        self.assertIn("requires_storage8_push_constant", executor)

    def test_fused_rms_rope_hashes_do_not_use_plain_rms_oracle(self):
        source = GPU_EXECUTOR.read_text()
        self.assertIn("rms-norm-rope-fused", source)
        self.assertIn("fused-rms-rope-oracle-pending", source)
        self.assertIn("0x4f37d4d51dd83526ull", source)
        self.assertIn("0x53c67d2aebf48739ull", source)

    def test_required_pending_or_unsupported_oracles_fail_closed(self):
        source = GPU_EXECUTOR.read_text()
        self.assertIn("cpu_oracle_status_is_unsupported", source)
        self.assertIn("cpu_oracle_required_fail_closed", source)
        self.assertIn('strstr(status, "oracle-pending")', source)
        self.assertIn('strstr(status, "not-implemented")', source)
        self.assertIn('strncmp(status, "unsupported", 11) == 0', source)
        self.assertIn("fail_stage = \"cpu-oracle-required\";", source)
        self.assertIn("rc = VK_ERROR_FEATURE_NOT_PRESENT;", source)
        self.assertIn("ret = 76;", source)
        self.assertIn('\\"oracle_fail_closed\\":%s', source)
        self.assertIn("write_cpu_oracle_report(json_out(), &cpu_oracle_report);", source)

    def test_q6_oracle_has_bounded_compact_retention_event(self):
        source = GPU_EXECUTOR.read_text()
        compare = LLAMA_COMPARE.read_text()
        self.assertIn("q6 compact response", source)
        self.assertIn("q6_compact_response", source)
        self.assertIn("write_vulkan_binding_compact_report(stderr", source)
        self.assertIn("write_cpu_oracle_report(stderr, &cpu_oracle_report);", source)
        self.assertIn('"q6 compact response:"', compare)

    def test_rope_yarn_oracle_is_hash_gated_and_evidence_backed(self):
        source = GPU_EXECUTOR.read_text()
        self.assertIn("0xac41e8033a67af4aull", source)
        self.assertIn("cpu_oracle_known_llama_hash", source)
        self.assertIn('return "rope-yarn";', source)
        self.assertIn("static void run_cpu_oracle_rope_yarn", source)
        self.assertIn("init_cpu_oracle_report(report, report->requested, 0xac41e8033a67af4aull);", source)
        self.assertIn("rope_yarn_ramp(corr_low, corr_high", source)
        self.assertIn("push_size < 27 * sizeof(uint32_t)", source)
        self.assertIn("report->executed = 1;", source)
        self.assertIn('report->mismatch_count ? "mismatch" : "match"', source)
        self.assertIn("if (cpu_oracle_spirv_hash == 0xac41e8033a67af4aull) {", source)
        self.assertIn("run_cpu_oracle_rope_yarn(&cpu_oracle_report", source)
        for report_field in [
            '\\"kernel_hint\\":\\"%s\\"',
            '\\"executed\\":%s',
            '\\"compared_floats\\":%zu',
            '\\"mismatch_count\\":%zu',
            '\\"first_mismatch\\":{\\"dst_index\\":%llu',
            '\\"samples\\":[',
        ]:
            self.assertIn(report_field, source)

        artifact = json.loads(ROPE_YARN_ARTIFACT.read_text())
        cpu_oracles = []

        def collect(obj):
            if isinstance(obj, dict):
                oracle = obj.get("cpu_oracle")
                if isinstance(oracle, dict) and oracle.get("kernel_hint") == "rope-yarn":
                    cpu_oracles.append(oracle)
                for value in obj.values():
                    collect(value)
            elif isinstance(obj, list):
                for value in obj:
                    collect(value)

        collect(artifact)
        self.assertTrue(cpu_oracles, "RoPE/Yarn evidence artifact must contain a rope-yarn oracle event")
        matched = [oracle for oracle in cpu_oracles if oracle.get("executed") is True and oracle.get("status") == "match"]
        self.assertTrue(matched, "RoPE/Yarn oracle must have executed match evidence")
        self.assertGreater(matched[0].get("compared_floats", 0), 0)
        self.assertEqual(0, matched[0].get("mismatch_count"))

        next_steps = LLAMA_GPU_NEXT_STEPS.read_text()
        correctness = LLAMA_GPU_CORRECTNESS.read_text()
        self.assertIn("Stage 3: RoPE/Yarn oracle for `0xac41e8033a67af4a` (completed)", next_steps)
        self.assertIn("docs/test/llama-gpu-ngl1-rope-yarn-oracle-20260509.json", next_steps)
        self.assertIn("0x274f68a67dfef210", next_steps)
        self.assertIn("Q6_K strict passthrough", next_steps)
        self.assertIn("memory readiness", next_steps)
        self.assertIn("RoPE/Yarn oracle is evidence-backed", correctness)

    def test_vulkan_specialization_constants_can_be_materialized(self):
        source = GPU_EXECUTOR.read_text()
        self.assertIn("materialize_spirv_specialization_constants", source)
        self.assertIn("PDOCKER_GPU_MATERIALIZE_SPIRV_SPECIALIZATION_CONSTANTS", source)
        self.assertIn('env_truthy("PDOCKER_GPU_MATERIALIZE_SPIRV_SPECIALIZATION_CONSTANTS", 0)', source)
        self.assertIn("vk_spec_ptr = specialization_materialized ? NULL : &vk_spec_info;", source)
        self.assertIn('\\"specialization_materialized\\":%s', source)
        self.assertIn("SpirvSpecializationMaterializeReport", source)
        self.assertIn("write_spirv_specialization_materialize_report", source)
        self.assertIn('\\"specialization_materialize_report\\":{', source)
        self.assertIn('\\"failure_reason\\":\\"%s\\"', source)
        self.assertIn('\\"first_unsupported\\":{', source)
        self.assertIn("BuiltIn WorkgroupSize", source)
        self.assertIn("workgroup_size_spec_id", source)
        self.assertIn("workgroup_size_component_id", source)
        self.assertIn("OpConstantComposite / OpSpecConstantComposite", source)
        self.assertIn("OpSpecConstantOp CompositeConstruct", source)
        self.assertIn("code[i + 3] == 80", source)
        self.assertIn("skip_spec_materialization", source)
        self.assertIn("code[i + 2] == 11 && code[i + 3] == 25", source)
        self.assertIn("preserve_workgroup_size_spec_subtree", source)
        self.assertIn("preserve_workgroup_size_spec_subtree &&\n            op == 51", source)
        self.assertIn("pre_materialize_local_size", source)
        self.assertIn("the stale default gl_WorkGroupSize value", source)
        self.assertIn("materialize_specialization_env", source)
        self.assertIn("? (materialize_specialization_env", source)
        self.assertNotIn("materialize_specialization_q6_scope", source)
        self.assertIn("Do not globally fold specialization constants", source)
        self.assertIn("structural rather than hash-specific", source)
        self.assertIn("materialize_specialization_requested && local_size_patched", source)
        self.assertIn("Materialize after LocalSize legalization", source)
        self.assertLess(
            source.index("local_size_patched = patch_spirv_literal_local_size_from_spec"),
            source.index("specialization_materialized = materialize_spirv_specialization_constants"),
        )
        manifest = (ROOT / "scripts" / "llama-gpu-env-manifest.json").read_text()
        manifest_data = json.loads(manifest)
        policy_by_env = {
            item["env"]: item.get("evidence_policy", "always")
            for item in manifest_data["config_propagation_env_fields"]
        }
        self.assertEqual(
            "callsite_gated",
            policy_by_env["PDOCKER_GPU_MATERIALIZE_SPIRV_SPECIALIZATION_CONSTANTS"],
        )
        self.assertNotEqual(
            "q6_callsite_gated",
            policy_by_env["PDOCKER_GPU_MATERIALIZE_SPIRV_SPECIALIZATION_CONSTANTS"],
        )
        compare = LLAMA_COMPARE.read_text()
        self.assertIn('"callsite_gated"', compare)
        self.assertIn('"cpu_oracle.kernel_hint"', compare)
        self.assertNotIn("Q6_CALLSITE_GATED_CONFIG_ENVS", compare)
        self.assertIn("Strict passthrough preserves descriptor object identity", source)
        self.assertIn("strict_passthrough;", source)

    def test_vulkan_dispatch_can_skip_unused_descriptor_transfers(self):
        source = GPU_EXECUTOR.read_text()
        self.assertIn("collect_spirv_descriptor_bindings", source)
        self.assertIn("PDOCKER_GPU_SKIP_UNUSED_DESCRIPTOR_TRANSFERS", source)
        self.assertIn("PDOCKER_GPU_USE_SPIRV_DESCRIPTOR_ACCESS", source)
        self.assertIn("collect_spirv_descriptor_accesses", source)
        self.assertIn("type_non_readable", source)
        self.assertIn("type_non_writable", source)
        self.assertIn("op == 72", source)
        self.assertIn("pointer_target_by_id", source)
        self.assertIn("variable_type_by_id", source)
        self.assertIn("uint8_t active_bindings[PDOCKER_GPU_MAX_VULKAN_BINDINGS]", source)
        self.assertIn("uint8_t binding_read_needed[PDOCKER_GPU_MAX_VULKAN_BINDINGS]", source)
        self.assertIn("uint8_t binding_write_needed[PDOCKER_GPU_MAX_VULKAN_BINDINGS]", source)
        self.assertIn("safe_kernel_reflection_transfer_pruning", source)
        self.assertIn("transfer_skip_unused_descriptor_transfers", source)
        self.assertIn("transfer_use_spirv_descriptor_access", source)
        self.assertIn("Strict passthrough normally keeps every application-provided descriptor", source)
        self.assertIn("Q6 safe-kernel input binding reflection lost readability", source)
        self.assertIn("Q6 safe-kernel output binding reflection lost writability", source)
        self.assertIn("if (!active_bindings[i]) continue;", source)
        self.assertIn("if (!binding_write_needed[i]) continue;", source)
        self.assertIn("Strict Q6 safe-kernel: keep the descriptor bound for ABI", source)
        self.assertIn('\\"descriptor_usage\\":{\\"active_bindings\\":%zu,', source)
        self.assertIn('\\"read_bindings\\":%zu,\\"write_bindings\\":%zu,', source)
        self.assertIn('\\"skipped_upload_bytes\\":%zu,\\"skipped_download_bytes\\":%zu}', source)
        self.assertIn('\\"safe_kernel_reflection_transfer_pruning\\":%s', source)
        self.assertIn('\\"effective_skip_unused_descriptor_transfers\\":%s', source)
        self.assertIn('\\"effective_spirv_descriptor_access\\":%s', source)
        self.assertIn('\\"fail_binding_index\\":%d,\\"io_result\\":%d,', source)
        self.assertIn("if (profile_response) {", source)
        self.assertIn("write_vulkan_binding_report(json_out(), bindings, binding_count,", source)
        self.assertIn("active_bindings", source)
        self.assertIn("binding_read_needed, binding_write_needed", source)
        self.assertIn("cache_hits, cache_resident", source)

    def test_vulkan_copy_submit_profile_is_recorded(self):
        source = VULKAN_ICD.read_text()
        self.assertIn("copy-submit summary ops=%zu alias_ops=%zu memmove_ops=%zu skipped_ops=%zu", source)
        for field in [
            "alias_bytes",
            "memmove_bytes",
            "skipped_bytes",
            "copy_alias_candidate(alias_memory)",
        ]:
            self.assertIn(field, source)
        compare = LLAMA_COMPARE.read_text()
        for field in [
            "copy_submit_alias_ops",
            "copy_submit_memmove_ops",
            "copy_submit_skipped_ops",
            "copy_submit_alias_bytes",
            "copy_submit_memmove_bytes",
        ]:
            self.assertIn(field, compare)

    def test_vulkan_descriptor_range_is_scoped_to_vkbuffer_not_allocation(self):
        source = VULKAN_ICD.read_text()
        self.assertIn("descriptor ranges are scoped to the VkBuffer", source)
        self.assertIn("binding->offset > binding->buffer->size", source)
        self.assertIn("available_in_buffer = binding->buffer->size - (size_t)binding->offset", source)
        self.assertIn("if (binding->range == VK_WHOLE_SIZE) return available_in_buffer;", source)
        descriptor_size = source.split("static size_t descriptor_binding_size(const PdockerVkDescriptorBinding *binding) {", 1)[1].split("\n}", 1)[0]
        self.assertNotIn("buffer_available(binding->buffer, binding->offset)", descriptor_size)

    def test_vulkan_buffer_pointer_arithmetic_is_overflow_guarded(self):
        source = VULKAN_ICD.read_text()
        buffer_available = source.split("static size_t buffer_available", 1)[1].split("static void *buffer_ptr", 1)[0]
        buffer_ptr = source.split("static void *buffer_ptr", 1)[1].split("static bool checked_add_u64", 1)[0]
        self.assertIn("buffer->memory_offset > UINT64_MAX - (uint64_t)offset", buffer_available)
        self.assertIn("const uint64_t absolute", buffer_available)
        self.assertIn("absolute > (uint64_t)buffer->memory->size", buffer_available)
        self.assertIn("buffer->memory_offset > UINT64_MAX - (uint64_t)offset", buffer_ptr)
        self.assertIn("return (char *)buffer->memory->map + (size_t)absolute;", buffer_ptr)
        self.assertNotIn("buffer->memory->map + buffer->memory_offset + offset", buffer_ptr)

    def test_vulkan_core_resource_handles_use_typed_converters(self):
        source = VULKAN_ICD.read_text()
        for marker in [
            "PDOCKER_VK_DEFINE_NON_DISPATCHABLE_HANDLE_CONVERTERS",
            "pdocker_vk_memory_from_handle",
            "pdocker_vk_buffer_from_handle",
            "pdocker_vk_image_from_handle",
            "pdocker_vk_image_view_from_handle",
            "pdocker_vk_sampler_from_handle",
            "Non-dispatchable Vulkan handles are pointer-like on 64-bit builds",
            "transport-visible object identity",
        ]:
            self.assertIn(marker, source)
        for forbidden in [
            "(PdockerVkMemory *)memory",
            "(PdockerVkBuffer *)buffer",
            "(PdockerVkImage *)image",
            "(PdockerVkImage *)info->image",
            "(PdockerVkImageView *)src->imageView",
            "(PdockerVkImageView *)info->imageView",
            "(PdockerVkSampler *)info->sampler",
            "*pBuffer = (VkBuffer)buffer",
            "*pImage = (VkImage)image",
            "*pView = (VkImageView)view",
            "*pSampler = (VkSampler)sampler",
            "*pMemory = (VkDeviceMemory)memory",
            "free((void *)buffer)",
            "free((void *)imageView)",
            "free((void *)sampler)",
        ]:
            self.assertNotIn(forbidden, source)

    def test_vulkan_compute_handles_use_typed_converters(self):
        source = VULKAN_ICD.read_text()
        for marker in [
            "pdocker_vk_descriptor_set_layout_from_handle",
            "pdocker_vk_descriptor_set_from_handle",
            "pdocker_vk_shader_module_from_handle",
            "pdocker_vk_pipeline_layout_from_handle",
            "pdocker_vk_pipeline_from_handle",
            "*pSetLayout = pdocker_vk_descriptor_set_layout_to_handle(layout);",
            "set->layout = pdocker_vk_descriptor_set_layout_from_handle(pAllocateInfo->pSetLayouts[i]);",
            "pDescriptorSets[i] = pdocker_vk_descriptor_set_to_handle(set);",
            "*pShaderModule = pdocker_vk_shader_module_to_handle(shader);",
            "pipeline->shader = pdocker_vk_shader_module_from_handle(ci->stage.module);",
            "pipeline->layout = pdocker_vk_pipeline_layout_from_handle(ci->layout);",
            "pPipelines[i] = pdocker_vk_pipeline_to_handle(pipeline);",
            "cmd->compute_pipeline = pdocker_vk_pipeline_from_handle(pipeline);",
            "PdockerVkPipelineLayout *pipeline_layout = pdocker_vk_pipeline_layout_from_handle(layout);",
            "PdockerVkDescriptorSet *set = pdocker_vk_descriptor_set_from_handle(pDescriptorSets[set_i]);",
        ]:
            self.assertIn(marker, source)
        for forbidden in [
            "*pSetLayout = (VkDescriptorSetLayout)layout;",
            "free((void *)descriptorSetLayout);",
            "? (PdockerVkDescriptorSetLayout *)pCreateInfo->pSetLayouts[i]",
            "*pPipelineLayout = (VkPipelineLayout)layout;",
            "free((void *)pipelineLayout);",
            "set->layout = (PdockerVkDescriptorSetLayout *)pAllocateInfo->pSetLayouts[i];",
            "pDescriptorSets[i] = (VkDescriptorSet)set;",
            "free((void *)pDescriptorSets[i]);",
            "PdockerVkDescriptorSet *live_set = (PdockerVkDescriptorSet *)w->dstSet;",
            "PdockerVkDescriptorSet *src_live = c ? (PdockerVkDescriptorSet *)c->srcSet : NULL;",
            "PdockerVkDescriptorSet *dst_live = c ? (PdockerVkDescriptorSet *)c->dstSet : NULL;",
            "*pShaderModule = (VkShaderModule)shader;",
            "PdockerVkShaderModule *shader = (PdockerVkShaderModule *)shaderModule;",
            "pipeline->shader = (PdockerVkShaderModule *)pCreateInfos[i].stage.module;",
            "pipeline->layout = (PdockerVkPipelineLayout *)pCreateInfos[i].layout;",
            "pPipelines[i] = (VkPipeline)pipeline;",
            "free((void *)pipeline);",
            "cmd->compute_pipeline = (PdockerVkPipeline *)pipeline;",
            "PdockerVkPipelineLayout *pipeline_layout = (PdockerVkPipelineLayout *)layout;",
            "PdockerVkDescriptorSet *set = (PdockerVkDescriptorSet *)pDescriptorSets[set_i];",
            "PdockerVkPipelineLayout *captured_layout = (PdockerVkPipelineLayout *)layout;",
        ]:
            self.assertNotIn(forbidden, source)

    def test_vulkan_wsi_graphics_pool_handles_use_typed_converters(self):
        source = VULKAN_ICD.read_text()
        for marker in [
            "typedef struct PdockerVkDescriptorPool PdockerVkDescriptorPool;",
            "typedef struct PdockerVkPipelineCache PdockerVkPipelineCache;",
            "typedef struct PdockerVkCommandPool PdockerVkCommandPool;",
            "pdocker_vk_descriptor_pool_from_handle",
            "pdocker_vk_pipeline_cache_from_handle",
            "pdocker_vk_command_pool_from_handle",
            "pdocker_vk_render_pass_from_handle",
            "pdocker_vk_framebuffer_from_handle",
            "pdocker_vk_surface_from_handle",
            "pdocker_vk_swapchain_from_handle",
            "*pDescriptorPool = pdocker_vk_descriptor_pool_to_handle(pool);",
            "free(pdocker_vk_descriptor_pool_from_handle(descriptorPool));",
            "*pPipelineCache = pdocker_vk_pipeline_cache_to_handle(cache);",
            "free(pdocker_vk_pipeline_cache_from_handle(pipelineCache));",
            "*pCommandPool = pdocker_vk_command_pool_to_handle(pool);",
            "free(pdocker_vk_command_pool_from_handle(commandPool));",
            "*pRenderPass = pdocker_vk_render_pass_to_handle(rp);",
            "pipeline->render_pass = pdocker_vk_render_pass_from_handle(ci->renderPass);",
            "fb->render_pass = pdocker_vk_render_pass_from_handle(pCreateInfo->renderPass);",
            "*pFramebuffer = pdocker_vk_framebuffer_to_handle(fb);",
            "free(pdocker_vk_framebuffer_from_handle(framebuffer));",
            "*pSurface = pdocker_vk_surface_to_handle(surface);",
            "pdocker_vk_headless_surface_valid(pdocker_vk_surface_from_handle(surface))",
            "PdockerVkSurface *surface = pdocker_vk_surface_from_handle(pCreateInfo->surface);",
            "*pSwapchain = pdocker_vk_swapchain_to_handle(swapchain);",
            "PdockerVkSwapchain *old_swapchain = pdocker_vk_swapchain_from_handle(pCreateInfo->oldSwapchain);",
            "PdockerVkSwapchain *sc = pdocker_vk_swapchain_from_handle(pPresentInfo->pSwapchains[i]);",
        ]:
            self.assertIn(marker, source)
        for forbidden in [
            "(VkDescriptorPool)",
            "(VkPipelineCache)",
            "(VkCommandPool)",
            "(VkRenderPass)rp",
            "(VkFramebuffer)fb",
            "(VkSurfaceKHR)surface",
            "(VkSwapchainKHR)swapchain",
            "(PdockerVkRenderPass *)ci->renderPass",
            "(PdockerVkRenderPass *)inherit->renderPass",
            "(PdockerVkRenderPass *)pCreateInfo->renderPass",
            "(PdockerVkRenderPass *)begin->renderPass",
            "(PdockerVkRenderPass *)pRenderPassBegin->renderPass",
            "(PdockerVkFramebuffer *)begin->framebuffer",
            "(PdockerVkFramebuffer *)pRenderPassBegin->framebuffer",
            "(PdockerVkSurface *)surface",
            "(PdockerVkSurface *)pCreateInfo->surface",
            "(PdockerVkSwapchain *)pCreateInfo->oldSwapchain",
            "(PdockerVkSwapchain *)swapchain",
            "(PdockerVkSwapchain *)pPresentInfo->pSwapchains[i]",
            "free((void *)descriptorPool)",
            "free((void *)pipelineCache)",
            "free((void *)commandPool)",
            "free((void *)renderPass)",
            "free((void *)framebuffer)",
            "free((void *)surface)",
            "free((void *)swapchain)",
        ]:
            self.assertNotIn(forbidden, source)

    def test_vulkan_sync_event_query_handles_use_typed_converters(self):
        source = VULKAN_ICD.read_text()
        for marker in [
            "typedef struct PdockerVkSemaphore PdockerVkSemaphore;",
            "pdocker_vk_fence_from_handle",
            "pdocker_vk_semaphore_from_handle",
            "pdocker_vk_event_from_handle",
            "pdocker_vk_query_pool_from_handle",
            "*pFence = pdocker_vk_fence_to_handle(fence);",
            "*pSemaphore = pdocker_vk_semaphore_to_handle(sem);",
            "*pEvent = pdocker_vk_event_to_handle(event);",
            "*pQueryPool = pdocker_vk_query_pool_to_handle(pool);",
            "PdockerVkFence *submit_fence = pdocker_vk_fence_from_handle(fence);",
            "PdockerVkSemaphore *sem = pdocker_vk_semaphore_from_handle(pSignalInfo->semaphore);",
            "PdockerVkEvent *e = pdocker_vk_event_from_handle(event);",
            "PdockerVkQueryPool *pool = pdocker_vk_query_pool_from_handle(queryPool);",
            "reset_query_range(pdocker_vk_query_pool_from_handle(queryPool), firstQuery, queryCount);",
        ]:
            self.assertIn(marker, source)
        for forbidden in [
            "(PdockerVkFence *)pFences[i]",
            "(const PdockerVkFence *)pFences[i]",
            "(PdockerVkFence *)fence",
            "(const PdockerVkFence *)fence",
            "(PdockerVkSemaphore *)pWaitInfo->pSemaphores[i]",
            "(PdockerVkSemaphore *)submit->pWaitSemaphores[i]",
            "(PdockerVkSemaphore *)submit->pSignalSemaphores[i]",
            "(const PdockerVkSemaphore *)submit->pWaitSemaphores[i]",
            "(const PdockerVkSemaphore *)submit->pSignalSemaphores[i]",
            "(PdockerVkSemaphore *)info->semaphore",
            "(PdockerVkSemaphore *)pSignalInfo->semaphore",
            "(VkFence)fence",
            "(VkSemaphore)sem",
            "(VkEvent)event",
            "(VkQueryPool)pool",
            "(PdockerVkEvent *)event",
            "(PdockerVkQueryPool *)queryPool",
            "free((void *)fence)",
            "free((void *)semaphore)",
            "free((void *)event)",
        ]:
            self.assertNotIn(forbidden, source)

    def test_vulkan_core_transport_ids_are_stable_64bit_object_ids(self):
        source = VULKAN_ICD.read_text()
        for marker in [
            "uint64_t object_id;",
            "static uint64_t pdocker_vk_memory_object_id",
            "static uint64_t pdocker_vk_buffer_object_id",
            "static uint64_t pdocker_vk_image_object_id",
            "static uint64_t pdocker_vk_image_view_object_id",
            "static uint64_t pdocker_vk_sampler_object_id",
            "static uint64_t pdocker_vk_pipeline_object_id",
            "static uint64_t pdocker_vk_render_pass_object_id",
            "return sampler ? sampler->object_id : 0;",
            "return pipeline ? pipeline->object_id : 0;",
            "return render_pass ? render_pass->object_id : 0;",
            "memory->object_id = next_vulkan_object_generation();",
            "buffer->object_id = next_vulkan_object_generation();",
            "image->object_id = next_vulkan_object_generation();",
            "view->object_id = next_vulkan_object_generation();",
            "sampler->object_id = next_vulkan_object_generation();",
            "pipeline->object_id = next_vulkan_object_generation();",
            "rp->object_id = next_vulkan_object_generation();",
            "entry->resource_id = pdocker_vk_memory_object_id(memory);",
            "entry->resource_id = pdocker_vk_buffer_object_id(buffer);",
            "entry->image_id = pdocker_vk_image_object_id(image);",
            "entry->view_id = pdocker_vk_image_view_object_id(view);",
            "entry->sampler_id = pdocker_vk_sampler_object_id(sampler);",
            "api_memory_ids[binding_count] = pdocker_vk_memory_object_id(dispatch_memory);",
            "api_buffer_ids[binding_count] = pdocker_vk_buffer_object_id(binding->buffer);",
            "uint64_t api_memory_ids[PDOCKER_VK_MAX_STORAGE_BUFFERS];",
            "uint64_t api_buffer_ids[PDOCKER_VK_MAX_STORAGE_BUFFERS];",
            "const uint64_t *api_memory_ids",
            "const uint64_t *api_buffer_ids",
            "uint64_t probe_memory_id =",
            "uint64_t probe_buffer_id =",
            "pipeline_entry->pipeline_id = pdocker_vk_pipeline_object_id(pipeline);",
            "pipeline_entry->render_pass_id = pdocker_vk_render_pass_object_id(pipeline->render_pass);",
        ]:
            self.assertIn(marker, source)
        for forbidden in [
            "uintptr_t api_memory_ids",
            "uintptr_t api_buffer_ids",
            "const uintptr_t *api_memory_ids",
            "const uintptr_t *api_buffer_ids",
            "uintptr_t probe_memory_id",
            "uintptr_t probe_buffer_id",
            "api_memory_ids[binding_count] = (uintptr_t)dispatch_memory;",
            "api_buffer_ids[binding_count] = (uintptr_t)binding->buffer;",
            "api_buffer_ids[i] == (uintptr_t)dispatch_indirect_buffer",
            "dispatch_hash = fnv1a64_update_u64(dispatch_hash, (uint64_t)(uintptr_t)op->dispatch_indirect_buffer);",
            "return sampler ? (uint64_t)-1 : 0;",
        ]:
            self.assertNotIn(forbidden, source)
        for direct_core_transport_id in [
            "entry->resource_id = (uint64_t)(uintptr_t)memory;",
            "entry->resource_id = (uint64_t)(uintptr_t)buffer;",
            "entry->image_id = (uint64_t)(uintptr_t)image;",
            "entry->view_id = (uint64_t)(uintptr_t)view;",
            "entry->sampler_id = (uint64_t)(uintptr_t)sampler;",
            "resources[memory_index].resource_id = (uint64_t)(uintptr_t)image->memory;",
            "image_entries[i].image_id = (uint64_t)(uintptr_t)image;",
            "resources[memory_index].resource_id = (uint64_t)(uintptr_t)memory;",
            "resources[buffer_index].resource_id = (uint64_t)(uintptr_t)dispatch_indirect_buffer;",
            "image_view_entries[i].view_id = (uint64_t)(uintptr_t)view;",
            "sampler_entries[i].sampler_id = (uint64_t)(uintptr_t)sampler;",
            "descriptor->resource_id = (uint64_t)(uintptr_t)binding->buffer;",
            "resource_id = (uint64_t)(uintptr_t)src->image_view;",
            "pipeline_entry->pipeline_id = (uint64_t)(uintptr_t)pipeline;",
            "pipeline_entry->render_pass_id = (uint64_t)(uintptr_t)pipeline->render_pass;",
        ]:
            self.assertNotIn(direct_core_transport_id, source)

    def test_vulkan_icd_copy_alias_offsets_are_overflow_guarded(self):
        source = VULKAN_ICD.read_text()
        dispatch_body = source.split("static int send_generic_vulkan_dispatch_op", 1)[1].split(
            "static int send_generic_vulkan_dispatch", 1
        )[0]
        overlap_body = source.split("static bool ranges_overlap", 1)[1].split(
            "static bool resolve_copy_alias", 1
        )[0]
        resolve_body = source.split("static bool resolve_copy_alias", 1)[1].split(
            "static void invalidate_copy_aliases", 1
        )[0]
        add_alias_body = source.split("static void add_copy_alias", 1)[1].split(
            "static bool copy_alias_candidate", 1
        )[0]
        copy_body = source.split("static void execute_recorded_copy_op", 1)[1].split(
            "static void execute_recorded_image_copy_op", 1
        )[0]
        self.assertIn("descriptor absolute offset overflow", dispatch_body)
        self.assertIn("checked_add_u64((uint64_t)binding->buffer->memory_offset", dispatch_body)
        self.assertNotIn("binding->buffer->memory_offset + binding->offset", dispatch_body)
        self.assertIn("return true;", overlap_body)
        self.assertIn("!checked_add_u64((uint64_t)a_offset", overlap_body)
        self.assertIn("!checked_add_u64((uint64_t)alias->src_offset", resolve_body)
        self.assertIn("resolved_offset > (uint64_t)alias->src_memory->size", resolve_body)
        self.assertIn("size > (VkDeviceSize)src_memory->size - src_offset", add_alias_body)
        self.assertIn("alias_memory = NULL;", copy_body)
        self.assertIn("checked_add_u64((uint64_t)op->src->memory_offset", copy_body)
        self.assertNotIn("op->src->memory_offset + op->region.srcOffset", copy_body)

    def test_vulkan_icd_size_and_index_ranges_are_overflow_guarded(self):
        source = VULKAN_ICD.read_text()
        guarded_body = source.split("static void guarded_sigsegv_handler", 1)[1].split("static bool install_guarded_sigsegv_handler", 1)[0]
        graphics_body = source.split("static int send_recorded_vulkan_graphics_v6_1_frame_range", 1)[1].split("static int send_recorded_vulkan_graphics_v6_1_frame(", 1)[0]
        allocate_body = source.split("VKAPI_ATTR VkResult VKAPI_CALL vkAllocateMemory", 1)[1].split("VKAPI_ATTR void VKAPI_CALL vkFreeMemory", 1)[0]
        map_body = source.split("VKAPI_ATTR VkResult VKAPI_CALL vkMapMemory", 1)[1].split("VKAPI_ATTR void VKAPI_CALL vkUnmapMemory", 1)[0]
        pipeline_layout_body = source.split("VKAPI_ATTR VkResult VKAPI_CALL vkCreatePipelineLayout", 1)[1].split("VKAPI_ATTR void VKAPI_CALL vkDestroyPipelineLayout", 1)[0]
        bind_body = source.split("VKAPI_ATTR void VKAPI_CALL vkCmdBindDescriptorSets", 1)[1].split("VKAPI_ATTR void VKAPI_CALL vkCmdPushConstants", 1)[0]
        push_constants_body = source.split("VKAPI_ATTR void VKAPI_CALL vkCmdPushConstants", 1)[1].split("static VkImageAspectFlags image_format_full_aspect_mask", 1)[0]
        query_body = source.split("VKAPI_ATTR VkResult VKAPI_CALL vkGetQueryPoolResults", 1)[1].split("VKAPI_ATTR VkResult VKAPI_CALL vkCreateFence", 1)[0]
        self.assertIn("memory->size > (size_t)(UINTPTR_MAX - start)", guarded_body)
        self.assertIn("page_delta > UINTPTR_MAX - start", guarded_body)
        self.assertIn("(uint64_t)src_spec->offset > stage->specialization_size", graphics_body)
        self.assertIn("stage->specialization_size - (uint64_t)src_spec->offset", graphics_body)
        self.assertNotIn("src_spec->offset + (uint64_t)src_spec->size", graphics_body)
        self.assertIn("(uint64_t)push->offset > cmd->push_constant_size", graphics_body)
        self.assertIn("cmd->push_constant_size - (uint64_t)push->offset", graphics_body)
        self.assertIn("pAllocateInfo->allocationSize > (VkDeviceSize)SIZE_MAX", allocate_body)
        self.assertIn("memory->size > SIZE_MAX - (memory->page_size - 1)", allocate_body)
        self.assertIn("offset > (VkDeviceSize)m->size || offset > (VkDeviceSize)SIZE_MAX", map_body)
        self.assertIn("*ppData = (char *)m->map + (size_t)offset;", map_body)
        self.assertIn("range->size > UINT32_MAX - (uint64_t)range->offset", pipeline_layout_body)
        self.assertNotIn("range->offset > UINT32_MAX", pipeline_layout_body)
        self.assertIn("uint32_t end = range->offset + range->size;", pipeline_layout_body)
        self.assertIn("offset > PDOCKER_VK_MAX_PUSH_BYTES", push_constants_body)
        self.assertIn("(uint64_t)size > (uint64_t)PDOCKER_VK_MAX_PUSH_BYTES - (uint64_t)offset", push_constants_body)
        self.assertIn("cmd->graphics_unsupported = true;", push_constants_body)
        self.assertIn("if (size == 0) return;", push_constants_body)
        self.assertIn("uint32_t end = offset + size;", push_constants_body)
        self.assertNotIn("size = PDOCKER_VK_MAX_PUSH_BYTES - offset", push_constants_body)
        self.assertIn("bool descriptor_set_range_overflow = firstSet > PDOCKER_VK_MAX_DESCRIPTOR_SETS", bind_body)
        self.assertIn("descriptorSetCount > PDOCKER_VK_MAX_DESCRIPTOR_SETS - firstSet", bind_body)
        self.assertIn("set_i >= PDOCKER_VK_MAX_DESCRIPTOR_SETS - firstSet", bind_body)
        self.assertNotIn("firstSet + descriptorSetCount > PDOCKER_VK_MAX_DESCRIPTOR_SETS", bind_body)
        self.assertIn("checked_mul_u64((uint64_t)i, (uint64_t)stride, &offset64)", query_body)
        self.assertIn("offset64 > (uint64_t)SIZE_MAX", query_body)
        self.assertNotIn("(size_t)i * (size_t)stride", query_body)

    def test_vulkan_frame_size_arithmetic_is_checked_before_materialization(self):
        source = VULKAN_ICD.read_text()
        helpers = source.split("static bool checked_add_size", 1)[1].split("static int write_exact_fd", 1)[0]
        append_body = source.split("static int frame_append_bytes", 1)[1].split("static int send_vulkan_dispatch_v5_frame_with_fds", 1)[0]
        graphics_body = source.split("static int send_recorded_vulkan_graphics_v6_1_frame_range", 1)[1].split("static int send_recorded_vulkan_graphics_v6_1_frame(", 1)[0]
        append_macro = graphics_body.split("#define APPEND_GRAPHICS_TABLE", 1)[1].split("#undef APPEND_GRAPHICS_TABLE", 1)[0]
        v51_body = source.split("static int send_generic_vulkan_dispatch_v5_1_op", 1)[1].split("static size_t descriptor_binding_size", 1)[0]
        capacity_block = v51_body.split("size_t resource_table_bytes = 0;", 1)[1].split("unsigned char *frame", 1)[0]
        v51_hash_append_block = v51_body.split("header->resource_hash = fnv1a64_bytes(resources,", 1)[1].split("rc = send_vulkan_dispatch_v5_frame_with_fds", 1)[0]
        graphics_hash_block = graphics_body.split("#undef APPEND_GRAPHICS_TABLE", 1)[1].split("header->frame_size = cursor;", 1)[0]

        self.assertIn("if (a > SIZE_MAX - b) return false;", helpers)
        self.assertIn("if (a != 0 && b > SIZE_MAX / a) return false;", helpers)
        self.assertIn("checked_add_size(value, 7u, &padded)", helpers)
        self.assertIn("if (!checked_align_size_8(*capacity, &aligned)) return false;", helpers)
        self.assertNotIn("checked_align_size_8(bytes, &aligned)", helpers)
        self.assertIn("return checked_add_size(aligned, bytes, capacity);", helpers)
        self.assertIn("checked_align_size_8(*cursor, &aligned)", append_body)
        self.assertIn("if (size > 0 && !data) return -EINVAL;", append_body)
        self.assertIn("checked_add_size(aligned, size, &end)", append_body)
        self.assertNotIn("align_size_8(*cursor)", append_body)

        self.assertIn("checked_mul_size((entry_size_), (count_), &table_bytes_)", append_macro)
        self.assertIn("table_bytes_", append_macro)
        self.assertNotIn("(entry_size_) * (count_)", append_macro)

        self.assertIn("checked_mul_size(binding_count, 2u, &resource_count)", v51_body)
        self.assertIn("frame_capacity_add_aligned_table(&frame_capacity", capacity_block)
        self.assertNotIn("checked_add_size(frame_capacity, 64u, &frame_capacity)", capacity_block)
        self.assertNotIn("64u", capacity_block)
        self.assertNotIn("align_size_8(sizeof", capacity_block)
        self.assertNotIn("sizeof(PdockerGpuVulkanDispatchV5ResourceEntry) * resource_count", capacity_block)
        for marker in [
            "resource_table_bytes",
            "descriptor_table_bytes",
            "image_table_bytes",
            "image_view_table_bytes",
            "sampler_table_bytes",
            "specialization_table_bytes",
        ]:
            self.assertIn(marker, v51_hash_append_block)
        self.assertNotIn("sizeof(resources[0]) * resource_count", v51_hash_append_block)
        self.assertNotIn("sizeof(descriptors[0]) * descriptor_count", v51_hash_append_block)
        for marker in [
            "header->resource_hash = fnv1a64_bytes(resources, resource_table_bytes);",
            "header->descriptor_hash = fnv1a64_bytes(descriptors, descriptor_table_bytes);",
            "object_hash = fnv1a64_update_bytes(object_hash, image_entries, image_table_bytes);",
            "object_hash = fnv1a64_update_bytes(object_hash, image_view_entries, image_view_table_bytes);",
            "object_hash = fnv1a64_update_bytes(object_hash, sampler_entries, sampler_table_bytes);",
        ]:
            self.assertIn(marker, v51_body)

        for marker in [
            "(size_t)frame_header->v61.dynamic_offset_table_size",
            "(size_t)frame_header->v61.push_constant_metadata_table_size",
            "(size_t)frame_header_v66->v66.color_blend_state_table_size",
            "(size_t)frame_header_v610->v610.buffer_image_copy_table_size",
            "(size_t)frame_header_v616->v616.clear_attachments_command_table_size",
            "(size_t)frame_header_v621->v621.submit_info_table_size",
            "(size_t)frame_header_v623->v623.tessellation_state_table_size",
        ]:
            self.assertIn(marker, graphics_hash_block)
        self.assertNotRegex(graphics_hash_block, r"sizeof\([^\n]+?\) \* [A-Za-z0-9_]+_count")

    def test_vulkan_copy_query_results_destination_range_is_guarded(self):
        source = VULKAN_ICD.read_text()
        helper_body = source.split("static bool query_result_copy_buffer_range", 1)[1].split("static void reset_query_range", 1)[0]
        graphics_body = source.split("PDOCKER_GPU_GRAPHICS_V6_COMMAND_COPY_QUERY_POOL_RESULTS", 1)[1].split("PdockerGpuVulkanGraphicsV618CopyQueryResultEntry", 1)[0]
        record_body = source.split("static void record_copy_query_results_command", 1)[1].split("VKAPI_ATTR VkResult VKAPI_CALL vkCreateQueryPool", 1)[0]
        self.assertIn("queryCount == 0 || stride == 0", helper_body)
        self.assertIn("VK_QUERY_RESULT_WITH_AVAILABILITY_BIT", helper_body)
        self.assertIn("checked_mul_u64((uint64_t)(queryCount - 1u), (uint64_t)stride, &last_offset)", helper_body)
        self.assertIn("checked_add_u64(last_offset, item_size, &bytes)", helper_body)
        self.assertIn("bytes > UINT64_MAX - (uint64_t)dstOffset", helper_body)
        self.assertIn("bytes > (uint64_t)SIZE_MAX", helper_body)
        self.assertIn("query_result_copy_buffer_range(op->query_result_flags, op->query_count", graphics_body)
        self.assertIn("validate_buffer_byte_range(op->query_dst_buffer, op->query_dst_offset, query_copy_bytes)", graphics_body)
        self.assertIn("query_result_copy_buffer_range(flags, queryCount, dstOffset, stride, &copy_bytes)", record_body)
        self.assertIn("validate_buffer_byte_range(dst, dstOffset, copy_bytes)", record_body)

    def test_vulkan_graphics_draw_buffer_ranges_are_guarded(self):
        source = VULKAN_ICD.read_text()
        vertex_helper = source.split("static bool validate_vertex_binding_byte_range", 1)[1].split("static bool image_copy_buffer_footprint", 1)[0]
        index_helper = source.split("static bool validate_index_buffer_draw_range", 1)[1].split("static bool validate_vertex_binding_byte_range", 1)[0]
        graphics_body = source.split("static int send_recorded_vulkan_graphics_v6_1_frame_range", 1)[1].split("static int send_recorded_vulkan_compute_v5_1_frame", 1)[0]
        self.assertIn("binding->stride > UINT32_MAX", vertex_helper)
        self.assertIn("binding->size == VK_WHOLE_SIZE", vertex_helper)
        self.assertIn("validate_buffer_byte_range(binding->buffer, binding->offset, binding->size)", vertex_helper)
        self.assertIn("vulkan_index_element_size(index_type, &element_size)", index_helper)
        self.assertIn("checked_mul_u64((uint64_t)first_index, element_size, &first_bytes)", index_helper)
        self.assertIn("checked_mul_u64((uint64_t)index_count, element_size, &draw_bytes)", index_helper)
        self.assertIn("first_bytes > UINT64_MAX - (uint64_t)index_offset", index_helper)
        self.assertIn("validate_vertex_binding_byte_range(binding, &binding_size)", graphics_body)
        self.assertIn("validate_buffer_backing_range(index_buffer)", graphics_body)
        self.assertIn("validate_buffer_byte_range(draw->indirect_buffer, draw->indirect_offset", graphics_body)
        self.assertIn("validate_buffer_byte_range(draw->count_buffer, draw->count_offset, sizeof(uint32_t))", graphics_body)
        self.assertIn("validate_index_buffer_draw_range(draw->index_buffer, draw->index_offset, draw->index_type", graphics_body)

    def test_vulkan_graphics_buffer_resources_and_image_copy_footprint_are_guarded(self):
        source = VULKAN_ICD.read_text()
        backing_body = source.split("static bool validate_buffer_backing_range", 1)[1].split("static bool validate_buffer_byte_range", 1)[0]
        byte_range_body = source.split("static bool validate_buffer_byte_range", 1)[1].split("static bool image_copy_buffer_footprint", 1)[0]
        footprint_body = source.split("static bool image_copy_buffer_footprint", 1)[1].split("static int collect_graphics_buffer_resource", 1)[0]
        collect_body = source.split("static int collect_graphics_buffer_resource", 1)[1].split("static int find_image_table_index", 1)[0]
        graphics_body = source.split("static int send_recorded_vulkan_graphics_v6_1_frame_range", 1)[1].split("static int send_recorded_vulkan_graphics_v6_1_frame(", 1)[0]
        host_copy_body = source.split("static void execute_recorded_image_copy_op", 1)[1].split("static void execute_recorded_image_to_image_copy_op", 1)[0]
        self.assertIn("buffer->memory_offset > (VkDeviceSize)buffer->memory->size", backing_body)
        self.assertIn("buffer->size > (VkDeviceSize)buffer->memory->size - buffer->memory_offset", backing_body)
        self.assertIn("offset > (VkDeviceSize)buffer->size", byte_range_body)
        self.assertIn("bytes > (VkDeviceSize)buffer->size - offset", byte_range_body)
        self.assertIn("bufferRowLength < op->region.imageExtent.width", footprint_body)
        self.assertIn("bufferImageHeight < op->region.imageExtent.height", footprint_body)
        self.assertIn("checked_mul_u64(slice_bytes, op->region.imageExtent.depth, &layer_stride)", footprint_body)
        self.assertIn("checked_mul_u64(last_layer, layer_stride, &delta)", footprint_body)
        self.assertIn("offset > UINT64_MAX - (delta + row_copy_bytes)", footprint_body)
        self.assertIn("!validate_buffer_backing_range(buffer)", collect_body)
        self.assertIn("image_copy_buffer_footprint(copy__, &buffer_footprint_offset__, &buffer_footprint_bytes__)", graphics_body)
        self.assertIn("validate_buffer_byte_range(copy__->buffer, buffer_footprint_offset__, buffer_footprint_bytes__)", graphics_body)
        self.assertIn("checked_mul_u64(buffer_slice_bytes, op->region.imageExtent.depth, &buffer_layer_stride)", host_copy_body)
        self.assertIn("checked_mul_u64((uint64_t)layer, buffer_layer_stride, &layer_bytes)", host_copy_body)
        self.assertNotIn("checked_mul_u64((uint64_t)layer, buffer_slice_bytes, &layer_bytes)", host_copy_body)

    def test_vulkan_dynamic_whole_size_descriptor_uses_effective_vkbuffer_tail(self):
        source = VULKAN_ICD.read_text()
        self.assertIn("VK_WHOLE_SIZE is evaluated after applying the", source)
        self.assertIn("slot->dynamic_offset = pDynamicOffsets[dynamic_index];", source)
        self.assertIn("slot->offset = slot->base_offset + slot->dynamic_offset;", source)
        self.assertIn("if (binding->range == VK_WHOLE_SIZE) return available_in_buffer;", source)
        bind_body = source.split("VKAPI_ATTR void VKAPI_CALL vkCmdBindDescriptorSets(", 1)[1].split("VKAPI_ATTR void VKAPI_CALL vkCmdDispatch(", 1)[0]
        self.assertNotIn("dynamic descriptor with VK_WHOLE_SIZE is unsupported", bind_body)

    def test_strict_passthrough_preserves_vkbuffer_coordinate_space(self):
        source = GPU_EXECUTOR.read_text() + "\n" + VULKAN_ICD.read_text()
        for marker in [
            "Strict passthrough keeps the application's VkBuffer coordinate",
            "VULKAN_DISPATCH_V3",
            "VULKAN_DISPATCH_V4",
            "api_memory_sizes[binding_count]",
            "api_memory_ids[binding_count]",
            "api_buffer_ids[binding_count]",
            "api_descriptor_sets[binding_count]",
            "bindings[i].descriptor_set",
            "VulkanStrictMemoryObject",
            "VulkanStrictBufferObject",
            "create_strict_vulkan_object_graph",
            "validate_strict_vulkan_binding_contract",
            "strict binding contract mismatch",
            "binding_offset != memory_offset + descriptor_offset",
            "binding_size > buffer_size - descriptor_offset",
            "binding_size > descriptor_range",
            "buffer_size > memory_size - memory_offset",
            "vulkan_binding_descriptor_range",
            "vkBindBufferMemory(device, buffers[b].buffer, memory->memory, buffers[b].memory_offset)",
            "strict_object_graph",
            "unsupported_descriptor_set_layout",
            "descriptor-set-index-out-of-range",
            "VkDescriptorSetLayout set_layouts[PDOCKER_GPU_MAX_VULKAN_DESCRIPTOR_SETS]",
            "VkDescriptorSet descriptor_sets[PDOCKER_GPU_MAX_VULKAN_DESCRIPTOR_SETS]",
            ".setLayoutCount = descriptor_set_count",
            ".pSetLayouts = set_layouts",
            ".descriptorSetCount = descriptor_set_count",
            "vkAllocateDescriptorSets(rt->device, &dsai, descriptor_sets)",
            "writes[write_count].dstSet = descriptor_sets[bindings[i].descriptor_set]",
            "descriptor_set_count,\n                            descriptor_sets",
            "binding_object_base[PDOCKER_GPU_MAX_VULKAN_BINDINGS]",
            "binding_object_end[PDOCKER_GPU_MAX_VULKAN_BINDINGS]",
            "object_base = bindings[i].api_memory_offset;",
            "checked_u64_add3((uint64_t)bindings[i].api_memory_offset",
            "checked_u64_to_off_t(strict_object_end, &object_end)",
            "const off_t i0 = binding_object_base[i];",
            "const off_t j0 = binding_object_base[j];",
            "const off_t start = binding_object_base[i];",
            "checked_u64_add3((uint64_t)bindings[i].api_memory_offset",
            "checked_u64_to_off_t(descriptor_absolute_u64, &descriptor_absolute)",
            "binding_gpu_offset[i] = (size_t)(descriptor_absolute - binding_group_base[rep]);",
            ": (size_t)descriptor_absolute;",
            "binding_descriptor_offset[i] = (size_t)bindings[i].api_offset;",
            "infos[write_count].range = (VkDeviceSize)\n            vulkan_binding_descriptor_range(&bindings[i], strict_passthrough);",
            "VkDescriptorBufferInfo.offset",
            "object-graph coordinate fidelity",
        ]:
            self.assertIn(marker, source)

    def test_strict_executor_range_arithmetic_is_checked_locally(self):
        source = GPU_EXECUTOR.read_text()
        graph_body = source.split("static int create_strict_vulkan_object_graph", 1)[1].split("static int validate_strict_vulkan_binding_contract", 1)[0]
        cache_key_body = source.split("static uint64_t strict_graph_cache_key", 1)[1].split("static int upload_strict_graph_bindings", 1)[0]
        upload_body = source.split("static int upload_strict_graph_bindings", 1)[1].split("static uint64_t pipeline_specialization_hash", 1)[0]
        dispatch_body = source.split("static int run_vulkan_dispatch_fd", 1)[1].split("static int run_vector_add", 1)[0]
        self.assertIn("validate_strict_binding_local_range(&bindings[i], descriptor_range)", graph_body)
        self.assertIn("checked_u64_add3((uint64_t)bindings[i].api_offset", graph_body)
        self.assertIn("checked_u64_add3(range_start, (uint64_t)bindings[i].size", graph_body)
        self.assertIn("checked_u64_add3((uint64_t)buffers[b].memory_offset", graph_body)
        self.assertIn("checked_u64_add3((uint64_t)b->api_offset, (uint64_t)descriptor_range", cache_key_body)
        self.assertIn("checked_u64_add3((uint64_t)bindings[i].api_memory_offset", upload_body)
        self.assertIn("checked_u64_add3(descriptor_absolute, (uint64_t)bindings[i].size", upload_body)
        self.assertIn("specializations[i].size > specialization_data_size - specializations[i].offset", dispatch_body)
        self.assertNotIn("descriptor_absolute + (uint64_t)bindings[i].size", source)
        self.assertNotIn("specializations[i].offset + specializations[i].size > specialization_data_size", source)
        self.assertNotIn("bindings[i].offset + (off_t)bindings[i].size", source)

    def test_vulkan_executor_range_helpers_avoid_truncation_and_wrap(self):
        source = GPU_EXECUTOR.read_text()
        offset_equals_body = c_function_body(source, "vulkan_binding_offset_equals_memory_plus_api_offset")
        descriptor_offset_body = c_function_body(source, "vulkan_binding_descriptor_offset_equals_api_offset")
        gpu_offset_body = c_function_body(source, "vulkan_binding_gpu_offset_equals_memory_plus_api_offset")
        spec_value_body = c_function_body(source, "specialization_value_u64")
        overlap_body = c_function_body(source, "ranges_overlap_off_size")
        absolute_range_body = c_function_body(source, "vulkan_binding_api_absolute_range")
        frame_range_body = c_function_body(source, "frame_ranges_do_not_overlap")
        self.assertIn("binding->offset < 0", offset_equals_body)
        self.assertIn("checked_u64_add3(memory_offset, api_offset, 0, &expected_offset)", offset_equals_body)
        self.assertIn("(uint64_t)binding->api_offset > (uint64_t)SIZE_MAX", descriptor_offset_body)
        self.assertIn("const uint64_t gpu_offset = memory_offset + api_offset", gpu_offset_body)
        self.assertIn("gpu_offset > (uint64_t)SIZE_MAX", gpu_offset_body)
        self.assertIn("specialization->size > specialization_data_size - specialization->offset", spec_value_body)
        self.assertIn("checked_u64_add3(a_start, (uint64_t)a_size", overlap_body)
        self.assertIn("checked_u64_add3(b_start, (uint64_t)b_size", overlap_body)
        self.assertIn("checked_u64_add3(memory_offset, api_offset, 0, &s)", absolute_range_body)
        self.assertIn("checked_u64_add3(s, (uint64_t)binding->size, 0, &e)", absolute_range_body)
        self.assertIn("checked_u64_add3(ranges[i].offset, ranges[i].size, 0, &a1)", frame_range_body)
        self.assertIn("checked_u64_add3(ranges[j].offset, ranges[j].size, 0, &b1)", frame_range_body)
        self.assertNotIn("specialization->offset + specialization->size > specialization_data_size", source)
        self.assertNotIn("uint64_t a1 = ranges[i].offset + ranges[i].size", source)
        self.assertNotIn("uint64_t b1 = ranges[j].offset + ranges[j].size", source)

    def test_vulkan_graphics_query_replay_offsets_are_checked(self):
        source = GPU_EXECUTOR.read_text()
        validate_body = c_function_body(source, "validate_vulkan_graphics_v6_frame_content")
        init_body = c_function_body(source, "materialize_vulkan_graphics_v617_queries")
        writeback_body = c_function_body(source, "writeback_vulkan_graphics_v617_query_results")
        self.assertIn("checked_mul_u64_executor((uint64_t)(entry->query_count - 1u)", validate_body)
        self.assertIn("checked_u64_add3(entry->result_offset, result_delta, 0, &last_offset)", validate_body)
        self.assertIn("checked_u64_add3((uint64_t)entry->first_query, (uint64_t)entry->query_count", init_body)
        self.assertIn("checked_u64_add3((uint64_t)entry->first_query, (uint64_t)q", writeback_body)
        self.assertIn("checked_mul_u64_executor((uint64_t)q, (uint64_t)entry->result_stride", writeback_body)
        self.assertIn("checked_u64_add3(entry->result_offset, dst_delta, 0, &dst_offset_u64)", writeback_body)
        self.assertNotIn("entry->result_offset + (uint64_t)(entry->query_count - 1u) *", source)
        self.assertNotIn("entry->result_offset + (uint64_t)q *", source)
        self.assertNotIn("uint32_t query = entry->first_query + q;", source)

    def test_vulkan_dispatch_v5_binding_conversion_rejects_truncating_casts(self):
        source = GPU_EXECUTOR.read_text()
        body = c_function_body(source, "convert_vulkan_dispatch_v5_to_v4_bindings")
        self.assertIn("checked_u64_to_off_t(fd_offset, &fd_offset_checked)", body)
        self.assertIn("checked_u64_to_off_t(api_offset, &api_offset_checked)", body)
        self.assertIn("checked_u64_to_off_t(buffer->memory_offset, &memory_offset_checked)", body)
        self.assertIn("d->transfer_size > (uint64_t)SIZE_MAX", body)
        self.assertIn("api_range > (uint64_t)SIZE_MAX", body)
        self.assertIn("buffer->size > (uint64_t)SIZE_MAX", body)
        self.assertIn("memory->size > (uint64_t)SIZE_MAX", body)
        self.assertIn("binding->offset = fd_offset_checked", body)
        self.assertIn("binding->api_offset = api_offset_checked", body)
        self.assertIn("binding->api_memory_offset = memory_offset_checked", body)
        self.assertNotIn("binding->offset = (off_t)fd_offset", body)
        self.assertNotIn("binding->api_offset = (off_t)api_offset", body)
        self.assertNotIn("binding->api_memory_offset = (off_t)buffer->memory_offset", body)

    def test_vulkan_graphics_push_constants_reject_truncating_metadata(self):
        source = GPU_EXECUTOR.read_text()
        validate_body = c_function_body(source, "validate_vulkan_graphics_v6_frame_content")
        collect_body = c_function_body(source, "collect_graphics_push_ranges_for_layout")
        record_body = c_function_body(source, "record_vulkan_graphics_v6_command_buffer")
        self.assertIn("meta->range_offset > UINT32_MAX || meta->range_size > UINT32_MAX", validate_body)
        self.assertIn("checked_u64_add3(meta->range_offset, meta->range_size, 0, &push_end)", validate_body)
        self.assertIn("push_end > PDOCKER_GPU_MAX_PUSH_BYTES", validate_body)
        self.assertIn("meta->range_offset > UINT32_MAX || meta->range_size > UINT32_MAX", collect_body)
        self.assertIn(".offset = (uint32_t)meta->range_offset", collect_body)
        self.assertIn(".size = (uint32_t)meta->range_size", collect_body)
        self.assertIn("(uint32_t)meta->range_offset, (uint32_t)meta->range_size", record_body)
        self.assertNotIn("meta->range_offset, meta->range_size,", record_body)

    def test_vulkan_graphics_push_metadata_matches_command_before_replay(self):
        source = GPU_EXECUTOR.read_text()
        validate_body = c_function_body(source, "validate_vulkan_graphics_v6_frame_content")
        record_body = c_function_body(source, "record_vulkan_graphics_v6_command_buffer")
        self.assertIn("meta->layout_id != command->pipeline_layout_id", validate_body)
        self.assertIn("meta->stage_flags != command->flags", validate_body)
        self.assertIn("meta->range_size != command->push_size", validate_body)
        self.assertIn("push_hash != command->push_hash", validate_body)
        self.assertLess(
            validate_body.index("meta->layout_id != command->pipeline_layout_id"),
            validate_body.index("metadata_count++"),
        )
        self.assertIn("vkCmdPushConstants(command_buffer", record_body)

    def test_vulkan_graphics_v69_buffer_copy_mixed_submit(self):
        icd = VULKAN_ICD.read_text()
        executor = GPU_EXECUTOR.read_text()
        abi = APP_HEADER.read_text() + "\n" + CONTAINER_HEADER.read_text()
        for marker in [
            "PDOCKER_GPU_VULKAN_GRAPHICS_V69_ABI_MINOR",
            "PDOCKER_GPU_VULKAN_GRAPHICS_V69_BUFFER_COPY_SCHEMA_HASH",
            "PDOCKER_GPU_GRAPHICS_V6_COMMAND_COPY_BUFFER",
            "PdockerGpuVulkanGraphicsV69BufferCopyEntry",
        ]:
            self.assertIn(marker, abi)
        for marker in [
            "command_op_sequence",
            "APPEND_INTERLEAVED_GRAPHICS_BUFFER_COPIES",
            "PDOCKER_GPU_GRAPHICS_V6_COMMAND_COPY_BUFFER",
            "buffer_copies[buffer_copy_count++]",
            "graphics-mixed-transfer-between-draws-unimplemented",
            "command_op_is_graphics_interleavable_transfer_op",
            "case PDOCKER_VK_COMMAND_COPY:",
        ]:
            self.assertIn(marker, icd)
        for marker in [
            "PDOCKER_GPU_VULKAN_GRAPHICS_V69_ABI_MINOR",
            "PdockerGpuVulkanGraphicsV69FrameHeader",
            "find_vulkan_graphics_v69_buffer_copy",
            "vkCmdCopyBuffer(command_buffer, src_buffer->buffer.buffer",
            "VK_BUFFER_USAGE_TRANSFER_SRC_BIT",
            "VK_BUFFER_USAGE_TRANSFER_DST_BIT",
            "mark_vulkan_graphics_replay_buffer_writeback_range",
        ]:
            self.assertIn(marker, executor)

    def test_vulkan_icd_supports_bounded_transfer_graphics_mixed_submit(self):
        icd = VULKAN_ICD.read_text()
        for marker in [
            "graphics_mixed_submit_plan",
            "command_op_is_graphics_frame_op",
            "command_op_is_host_transfer_or_layout_op",
            "execute_recorded_host_transfer_or_layout_op",
            "execute_graphics_mixed_host_side_ops",
            "first_graphics_gpu_op",
            "last_graphics_gpu_op",
            "command_op_should_extend_graphics_gpu_frame",
            "graphics-mixed-transfer-between-draws-unimplemented",
            "graphics V6.1 mixed submit rc=%d prepost_ops=%zu bytes=%llu",
            "execute_recorded_copy_op(&cmd->copy_ops[op->index], stats)",
            "execute_recorded_image_copy_op(&cmd->image_copy_ops[op->index], stats)",
            "execute_recorded_image_barrier_op(&cmd->image_barrier_ops[op->index])",
            "execute_recorded_fill_op(op)",
            "execute_recorded_update_op(op)",
        ]:
            self.assertIn(marker, icd)
        self.assertIn("static VkResult execute_graphics_mixed_host_side_ops", icd)
        self.assertNotIn("static void execute_graphics_mixed_host_side_ops", icd)
        plan_body = icd.split("static bool graphics_mixed_submit_plan", 1)[1].split(
            "static VkResult execute_graphics_mixed_host_side_ops", 1
        )[0]
        self.assertIn("command_op_is_graphics_interleavable_transfer_op", icd)
        self.assertIn("cmd->graphics_command_ops[record_index].command_op_sequence", plan_body)
        self.assertIn("command_op_should_extend_graphics_gpu_frame(", plan_body)
        self.assertNotIn("!command_op_is_graphics_frame_op(type) &&", plan_body)
        self.assertNotIn("!command_op_is_graphics_interleavable_transfer_op(type)", plan_body)
        self.assertIn("first_gpu_op == UINT32_MAX || op_index < first_gpu_op", plan_body)
        self.assertIn("op_index > last_gpu_op", plan_body)
        self.assertIn("graphics-mixed-host-op-inside-gpu-frame-unimplemented", plan_body)
        interleavable_body = icd.split("static bool command_op_is_graphics_interleavable_transfer_op(PdockerVkCommandOpType type) {", 1)[1].split(
            "static bool submit_sync_entries_include_wait", 1
        )[0]
        for allowed in [
            "case PDOCKER_VK_COMMAND_COPY:",
            "case PDOCKER_VK_COMMAND_BLIT_IMAGE:",
            "case PDOCKER_VK_COMMAND_CLEAR_DEPTH_STENCIL_IMAGE:",
        ]:
            self.assertIn(allowed, interleavable_body)
        for host_side_only in [
            "case PDOCKER_VK_COMMAND_EVENT:",
            "case PDOCKER_VK_COMMAND_EVENT_WAIT:",
            "case PDOCKER_VK_COMMAND_QUERY_BEGIN:",
            "case PDOCKER_VK_COMMAND_QUERY_END:",
            "case PDOCKER_VK_COMMAND_QUERY_RESET:",
            "case PDOCKER_VK_COMMAND_QUERY_TIMESTAMP:",
        ]:
            self.assertNotIn(host_side_only, interleavable_body)
        gpu_frame_extent_body = icd.split("static bool command_op_should_extend_graphics_gpu_frame", 1)[1].split(
            "static bool command_op_is_executor_compute_op", 1
        )[0]
        self.assertIn("command_op_is_graphics_interleavable_transfer_op(type)", gpu_frame_extent_body)
        self.assertIn("first_draw != UINT32_MAX && op_index > first_draw && op_index < last_draw", gpu_frame_extent_body)
        self.assertIn("return command_op_is_graphics_frame_op(type);", gpu_frame_extent_body)
        submit_body = icd.split("VKAPI_ATTR VkResult VKAPI_CALL vkQueueSubmit", 1)[1].split(
            "VKAPI_ATTR VkResult VKAPI_CALL vkWaitForFences", 1
        )[0]
        self.assertNotIn("graphics_only_submit", submit_body)
        for marker in [
            "submit_wait_sync_needs_executor",
            "submit_completion_sync_needs_executor",
            "submit_waits_split_before_command_loop",
            "submit_has_recorded_work_before_command(&pSubmits[i], first_graphics_submit_sync_cmd)",
            "submit-pre-wait-sync-failed",
            "submit-completion-sync-failed",
        ]:
            self.assertIn(marker, submit_body)
        mixed_body = submit_body.split("if (command_buffer_needs_graphics_submit_sync_frame(cmd))", 1)[1].split(
            "if (cmd->command_op_count > 0)", 1
        )[0]
        self.assertLess(
            mixed_body.index("filter_submit_sync_entries_for_graphics_frame"),
            mixed_body.index("execute_graphics_mixed_host_side_ops("),
        )
        for marker in [
            "submit_sync_entries_include_wait(frame_submit_sync_entries, frame_submit_sync_count)",
            "submit_waits_split_before_command_loop",
            "command_buffer_has_host_side_ops_before(cmd, first_graphics_gpu_op)",
            "submit_sync_entries_include_completion(frame_submit_sync_entries, frame_submit_sync_count)",
            "submit_has_recorded_work_after_command(&pSubmits[i], j)",
            "command_buffer_has_host_side_ops_after(cmd, last_graphics_gpu_op)",
            "filter_submit_sync_entries_wait_only(",
            "filter_submit_sync_entries_without_waits(",
            "split graphics submit wait sync before prior host-side work",
            "graphics-v6-pre-wait-sync-failed",
            "filter_submit_sync_entries_completion_only(",
            "filter_submit_sync_entries_without_completion(",
            "frame_submit_sync_count = deferred_frame_sync_count;",
            "deferred graphics submit completion sync until trailing host-side work finishes",
            "send_vulkan_submit_sync_only_frame(",
            "graphics-v6-deferred-completion-sync-failed",
        ]:
            self.assertIn(marker, mixed_body)
        self.assertIn("PdockerVkCommandBuffer *sync_cmd = (PdockerVkCommandBuffer *)calloc(1, sizeof(*sync_cmd));", icd)
        self.assertIn("send_recorded_vulkan_graphics_v6_1_frame(sync_cmd, entries, entry_count)", icd)
        self.assertNotIn("PdockerVkCommandBuffer sync_cmd;", icd)
        self.assertNotIn("submit-sync-wait-after-prior-work-unimplemented", mixed_body)
        self.assertNotIn("submit-sync-signal-or-fence-before-trailing-submit-work-unimplemented", mixed_body)
        self.assertLess(
            mixed_body.index("filter_submit_sync_entries_completion_only("),
            mixed_body.index("filter_submit_sync_entries_without_completion("),
        )
        self.assertLess(
            mixed_body.index("filter_submit_sync_entries_without_completion("),
            mixed_body.index("execute_graphics_mixed_gpu_sequence("),
        )
        self.assertLess(
            mixed_body.index("send_vulkan_submit_sync_only_frame(\n                    pre_wait_sync_entries, pre_wait_sync_count)"),
            mixed_body.index("execute_graphics_mixed_host_side_ops("),
        )
        self.assertLess(
            mixed_body.index("execute_graphics_mixed_gpu_sequence(\n                    cmd, first_graphics_gpu_op, last_graphics_gpu_op,"),
            mixed_body.rindex("execute_graphics_mixed_host_side_ops("),
        )
        for marker in [
            "command_op_is_executor_compute_op",
            "command_op_is_graphics_side_submit_op",
            "execute_recorded_dispatch_command_op",
            "send_recorded_vulkan_graphics_v6_1_frame_range",
            "graphics_record_should_serialize_for_range",
            "graphics_record_is_state_preamble_command",
            "execute_graphics_mixed_gpu_sequence",
            "count_graphics_sequence_segments_split_by_dispatch",
            "send_graphics_sequence_segment",
            "graphics_mixed_dispatch_inside_frame_reason",
            "graphics_sequence_inside_active_rendering",
            "graphics-mixed-dispatch-inside-rendering-unimplemented",
            "graphics-mixed-dispatch-between-render-scopes-unimplemented",
        ]:
            self.assertIn(marker, icd)
        self.assertIn("!command_op_is_graphics_side_submit_op(type)", plan_body)
        self.assertIn("command_op_is_executor_compute_op(type)", plan_body)
        self.assertIn("graphics_mixed_dispatch_inside_frame_reason(cmd, op_index)", plan_body)
        self.assertIn("continue;", plan_body)
        self.assertNotIn("!command_op_is_executor_compute_op(type)) {\n            continue;\n        }\n        if (first_gpu_op == UINT32_MAX", plan_body)
        before_body = icd.split("static bool command_buffer_has_host_side_ops_before", 1)[1].split(
            "static bool command_buffer_has_host_side_ops_after", 1
        )[0]
        after_body = icd.split("static bool command_buffer_has_host_side_ops_after", 1)[1].split(
            "static bool graphics_mixed_submit_plan", 1
        )[0]
        self.assertIn("command_op_is_graphics_side_submit_op", before_body)
        self.assertIn("command_op_is_graphics_side_submit_op", after_body)
        side_exec_body = icd.split("static VkResult execute_graphics_mixed_host_side_ops", 1)[1].split(
            "VKAPI_ATTR VkResult VKAPI_CALL vkQueueSubmit", 1
        )[0]
        self.assertIn("execute_recorded_dispatch_command_op(cmd, op, NULL)", side_exec_body)
        ordered_submit_body = submit_body.split("if (cmd->command_op_count > 0)", 1)[1].split(
            "execute_recorded_copy_ops(cmd);", 1
        )[0]
        self.assertIn("execute_recorded_dispatch_command_op(cmd, op, &dispatches)", ordered_submit_body)
        self.assertLess(
            mixed_body.rindex("execute_graphics_mixed_host_side_ops("),
            mixed_body.index("send_vulkan_submit_sync_only_frame(\n                    deferred_completion_sync_entries, deferred_completion_sync_count)"),
        )

    def test_vulkan_graphics_dispatch_inside_frame_has_render_scope_diagnostics(self):
        icd = VULKAN_ICD.read_text()
        classifier = icd.split("static bool graphics_sequence_inside_active_rendering", 1)[1].split(
            "static const char *graphics_mixed_dispatch_inside_frame_reason", 1
        )[0]
        reason = icd.split("static const char *graphics_mixed_dispatch_inside_frame_reason", 1)[1].split(
            "static VkResult execute_recorded_dispatch_command_op", 1
        )[0]
        for marker in [
            "PDOCKER_GPU_GRAPHICS_V6_COMMAND_BEGIN_RENDERING",
            "PDOCKER_GPU_GRAPHICS_V6_COMMAND_END_RENDERING",
            "rendering_active = true",
            "rendering_active = false",
            "command_op_sequence >= rendering_begin_sequence",
            "command_op_sequence < record->command_op_sequence",
        ]:
            self.assertIn(marker, classifier)
        self.assertIn('"graphics-mixed-dispatch-inside-rendering-unimplemented"', reason)
        self.assertIn('"graphics-mixed-dispatch-between-render-scopes-unimplemented"', reason)
        plan_body = icd.split("static bool graphics_mixed_submit_plan", 1)[1].split(
            "static VkResult execute_graphics_mixed_host_side_ops", 1
        )[0]
        self.assertIn("graphics_mixed_dispatch_inside_frame_reason(cmd, op_index)", plan_body)
        self.assertIn("graphics-mixed-transfer-inside-rendering-unimplemented", plan_body)
        self.assertIn("graphics_sequence_inside_active_rendering(cmd, op_index)", plan_body)
        self.assertNotIn("graphics-mixed-dispatch-inside-gpu-frame-unimplemented", plan_body)

    def test_vulkan_graphics_between_render_dispatch_uses_range_split_with_state_preamble(self):
        icd = VULKAN_ICD.read_text()
        range_body = icd.split("static int send_recorded_vulkan_graphics_v6_1_frame_range", 1)[1].split(
            "static int send_recorded_vulkan_graphics_v6_1_frame", 1
        )[0]
        preamble_body = icd.split("static bool graphics_record_is_state_preamble_command", 1)[1].split(
            "static bool graphics_record_should_serialize_for_range", 1
        )[0]
        should_body = icd.split("static bool graphics_record_should_serialize_for_range", 1)[1].split(
            "static bool command_buffer_has_graphics_records_in_sequence_range", 1
        )[0]
        segment_body = icd.split("static uint32_t count_graphics_sequence_segments_split_by_dispatch", 1)[1].split(
            "static VkResult execute_graphics_mixed_host_side_ops", 1
        )[0]
        for marker in [
            "PDOCKER_GPU_GRAPHICS_V6_COMMAND_BIND_PIPELINE",
            "PDOCKER_GPU_GRAPHICS_V6_COMMAND_BIND_DESCRIPTOR_SETS",
            "PDOCKER_GPU_GRAPHICS_V6_COMMAND_PUSH_CONSTANTS",
            "PDOCKER_GPU_GRAPHICS_V6_COMMAND_BIND_VERTEX_BUFFERS",
            "PDOCKER_GPU_GRAPHICS_V6_COMMAND_BIND_INDEX_BUFFER",
            "PDOCKER_GPU_GRAPHICS_V6_COMMAND_SET_DYNAMIC_STATE",
        ]:
            self.assertIn(marker, preamble_body)
        for marker in [
            "PDOCKER_GPU_GRAPHICS_V6_COMMAND_BEGIN_RENDERING",
            "PDOCKER_GPU_GRAPHICS_V6_COMMAND_END_RENDERING",
            "PDOCKER_GPU_GRAPHICS_V6_COMMAND_DRAW",
            "PDOCKER_GPU_GRAPHICS_V6_COMMAND_DRAW_INDEXED",
            "PDOCKER_GPU_GRAPHICS_V6_COMMAND_COPY_BUFFER",
        ]:
            self.assertNotIn(marker, preamble_body)
        self.assertIn("include_state_preamble", range_body)
        self.assertIn("graphics_record_should_serialize_for_range", range_body)
        self.assertIn("record->command_op_sequence < sequence_begin", should_body)
        self.assertIn("graphics_record_is_state_preamble_command(record->command_type)", should_body)
        draw_interval_body = range_body.split("uint32_t first_graphics_draw_sequence = UINT32_MAX;", 1)[1].split(
            "uint32_t next_command_op_for_graphics = sequence_begin;", 1
        )[0]
        self.assertNotIn("command_op_sequence_in_range", draw_interval_body)
        self.assertIn("next_command_op_for_graphics = sequence_begin", range_body)
        self.assertIn("next_command_op_for_graphics < sequence_end", range_body)
        count_body = segment_body.split("static int send_graphics_sequence_segment", 1)[0]
        execute_body = segment_body.split("static int execute_graphics_mixed_gpu_sequence", 1)[1]
        send_segment_body = segment_body.split("static int send_graphics_sequence_segment", 1)[1].split(
            "static int execute_graphics_mixed_gpu_sequence", 1
        )[0]
        self.assertIn("cmd, segment_begin, op_index + 1u", count_body)
        self.assertIn("cmd, segment_begin, op_index + 1u", execute_body)
        self.assertLess(
            execute_body.index("send_graphics_sequence_segment("),
            execute_body.index("execute_recorded_dispatch_command_op(cmd, op, NULL)"),
        )
        self.assertLess(
            execute_body.index("execute_recorded_dispatch_command_op(cmd, op, NULL)"),
            execute_body.index("segment_begin = op_index + 1u"),
        )
        self.assertIn("filter_submit_sync_entries_for_graphics_frame", send_segment_body)
        self.assertIn("first_segment, last_segment, segment_sync_entries", send_segment_body)
        self.assertIn("!first_segment", send_segment_body)
        self.assertIn("last_gpu_op == UINT32_MAX", segment_body)

    def test_vulkan_compute_push_constants_do_not_create_graphics_frame(self):
        icd = VULKAN_ICD.read_text()
        self.assertIn("shader_stage_flags_include_graphics", icd)
        self.assertIn("VK_SHADER_STAGE_ALL_GRAPHICS", icd)
        push_body = icd.split("VKAPI_ATTR void VKAPI_CALL vkCmdPushConstants", 1)[1].split(
            "static VkImageAspectFlags image_format_full_aspect_mask", 1
        )[0]
        self.assertIn("graphics_record_requires_submit_frame", icd)
        self.assertIn("command_buffer_needs_graphics_submit_sync_frame", icd)
        self.assertIn("if (shader_stage_flags_include_graphics(stageFlags))", push_body)
        self.assertIn("append_graphics_command_record(cmd, &record)", push_body)
        self.assertLess(
            push_body.index("if (shader_stage_flags_include_graphics(stageFlags))"),
            push_body.index("append_graphics_command_record(cmd, &record)"),
        )
        dispatch_body = icd.split("VKAPI_ATTR void VKAPI_CALL vkCmdDispatch", 1)[1].split(
            "VKAPI_ATTR void VKAPI_CALL vkCmdDispatchBase", 1
        )[0]
        self.assertIn("op->push_constant_ops", dispatch_body)
        self.assertIn("command_op.type = PDOCKER_VK_COMMAND_DISPATCH", dispatch_body)

    def test_vulkan_transfer_and_query_only_command_buffers_use_executor_replay(self):
        icd = VULKAN_ICD.read_text()
        executor = GPU_EXECUTOR.read_text()
        needs_body = icd.split("static bool command_buffer_needs_graphics_submit_sync_frame", 1)[1].split(
            "static void graphics_submit_sync_frame_bounds", 1
        )[0]
        self.assertIn("bool has_graphics_submit_frame = false", needs_body)
        self.assertIn("bool has_executor_frame_command = false", needs_body)
        self.assertIn("command_op_requires_graphics_executor_frame(cmd, op_index)", needs_body)
        self.assertIn("if (!has_graphics_submit_frame && !has_executor_frame_command) {", needs_body)
        self.assertIn("return false;", needs_body)
        self.assertLess(
            needs_body.index("if (!has_graphics_submit_frame && !has_executor_frame_command) {"),
            needs_body.index("for (uint32_t i = 0; i < cmd->command_op_count; ++i)"),
        )
        self.assertIn("cmd->command_op_count", needs_body)
        self.assertIn("command_op_is_graphics_frame_op(type)", needs_body)
        self.assertIn("command_op_is_graphics_interleavable_transfer_op(type)", needs_body)
        frame_op_body = icd.split("static bool command_op_is_graphics_frame_op", 1)[1].split(
            "static bool command_op_is_host_transfer_or_layout_op", 1
        )[0]
        for marker in [
            "case PDOCKER_VK_COMMAND_COPY:",
            "case PDOCKER_VK_COMMAND_IMAGE_COPY:",
            "case PDOCKER_VK_COMMAND_IMAGE_TO_IMAGE_COPY:",
            "case PDOCKER_VK_COMMAND_CLEAR_COLOR_IMAGE:",
            "case PDOCKER_VK_COMMAND_RESOLVE_IMAGE:",
            "case PDOCKER_VK_COMMAND_BLIT_IMAGE:",
            "case PDOCKER_VK_COMMAND_CLEAR_DEPTH_STENCIL_IMAGE:",
            "case PDOCKER_VK_COMMAND_FILL:",
            "case PDOCKER_VK_COMMAND_UPDATE:",
            "case PDOCKER_VK_COMMAND_COPY_QUERY_RESULTS:",
        ]:
            self.assertIn(marker, frame_op_body)
        self.assertIn("vulkan_graphics_v6_frame_needs_dynamic_rendering", executor)
        runtime_body = executor.split("static int preflight_vulkan_graphics_v6_runtime_supported", 1)[1].split(
            "#define PDOCKER_GPU_GRAPHICS_REPLAY_MAX_SHADER_STAGES", 1
        )[0]
        self.assertIn("const VulkanGraphicsV6FrameView *view", runtime_body)
        self.assertIn("vulkan_graphics_v6_frame_needs_dynamic_rendering(view)", runtime_body)
        needs_rendering_body = executor.split("static int vulkan_graphics_v6_frame_needs_dynamic_rendering", 1)[1].split(
            "static int preflight_vulkan_graphics_v6_runtime_supported", 1
        )[0]
        self.assertIn("PDOCKER_GPU_GRAPHICS_V6_COMMAND_BEGIN_RENDERING", needs_rendering_body)
        self.assertIn("PDOCKER_GPU_GRAPHICS_V6_COMMAND_DRAW", needs_rendering_body)
        self.assertNotIn("PDOCKER_GPU_GRAPHICS_V6_COMMAND_COPY_BUFFER:", needs_rendering_body)
        self.assertIn("preflight_vulkan_graphics_v6_runtime_supported(view, &reason)", executor)


    def test_vulkan_command_buffer_replays_all_recorded_dispatches(self):
        source = VULKAN_ICD.read_text()
        for marker in [
            "PDOCKER_VK_MAX_DISPATCH_OPS",
            "PDOCKER_VK_MAX_COMMAND_OPS",
            "PdockerVkDispatchOp dispatch_ops[PDOCKER_VK_MAX_DISPATCH_OPS]",
            "PdockerVkCommandOp command_ops[PDOCKER_VK_MAX_COMMAND_OPS]",
            "cmd->dispatch_op_count = 0;",
            "clear_recorded_command_ops(cmd)",
            "append_command_op(cmd, &command_op)",
            "append_command_op(cmd, &op)",
            "PdockerVkDispatchOp *op = &cmd->dispatch_ops[op_index];",
            "send_generic_vulkan_dispatch_op",
            "for (uint32_t op_index = 0; op_index < cmd->dispatch_op_count; ++op_index)",
            "for (uint32_t op_index = 0; op_index < cmd->command_op_count; ++op_index)",
            "execute_recorded_copy_op(&cmd->copy_ops[op->index], &stats)",
            "execute_recorded_fill_op(op)",
            "execute_recorded_update_op(op)",
            "queue-submit replayed ordered ops=%u dispatches=%u",
            "queue-submit replayed dispatch ops=%u",
        ]:
            self.assertIn(marker, source)
        submit_body = source.split("VKAPI_ATTR VkResult VKAPI_CALL vkQueueSubmit", 1)[1].split("VKAPI_ATTR VkResult VKAPI_CALL vkWaitForFences", 1)[0]
        self.assertIn("cmd->command_op_count > 0", submit_body)
        self.assertIn("execute_recorded_dispatch_command_op(cmd, op, &dispatches)", submit_body)
        dispatch_helper_body = source.split("static VkResult execute_recorded_dispatch_command_op", 1)[1].split(
            "static bool submit_sync_entries_include_wait", 1
        )[0]
        self.assertIn("send_generic_vulkan_dispatch_op(dispatch)", dispatch_helper_body)
        self.assertIn("cmd->dispatch_op_count > 0", submit_body)
        self.assertIn("send_generic_vulkan_dispatch_op(op)", submit_body)

    def test_spirv_observability_is_generic_not_hash_only(self):
        source = GPU_EXECUTOR.read_text()
        for marker in [
            "instruction_count",
            "memory_instruction_count",
            "arithmetic_instruction_count",
            "PDOCKER_GPU_SPIRV_DUMP_DIR",
            "has_spirv_dump_dir",
            "has_failed_spirv_dir",
            "PDOCKER_GPU_VULKAN_STRING_DISPATCH_OPTIONS",
            "parse_hex_string_token_value",
            "_hex=",
            "dump_spirv_if_requested(\"original\"",
            "dump_spirv_if_requested(\"effective\"",
            "generic dispatch pre-submit",
            "strict graph cache plan",
            "strict-graph-cache-plan",
            "strict_descriptor_window_compaction_status",
            "descriptor_window_compaction_disabled_reason",
            "memory-shared-by-different-buffer",
            "d->resource_id != 0 && d->resource_id != buffer->resource_id",
            "d->transfer_offset < api_offset",
            "const uint64_t transfer_delta = d->transfer_offset - api_offset;",
            "transfer_delta > api_range",
            "binding->api_range = (size_t)api_range;",
            "strict binding contract detail",
            "expected_offset_valid",
            "no_access_binding_count",
            "strict_graph_cache_bytes",
            "spirv_instruction_count",
            "spirv_op_class_counts",
        ]:
            self.assertIn(marker, source)
        icd = VULKAN_ICD.read_text()
        self.assertIn("PDOCKER_GPU_VULKAN_STRING_DISPATCH_OPTIONS", icd)
        self.assertIn("%s_hex=%s", icd)
        self.assertIn("invalid %s dispatch_id", icd)
        self.assertIn("generic dispatch response status", icd)
        self.assertIn("response_errno", icd)
        self.assertIn("line_bytes", icd)
        self.assertIn("scm_rights_fd_count_seen", source)
        manifest = json.loads(LLAMA_GPU_ENV_MANIFEST.read_text())
        self.assertIn(
            "PDOCKER_GPU_SPIRV_DUMP_DIR",
            manifest["env_bridge_classifications"]["icd_to_executor_string_option"],
        )
        self.assertIn(
            "PDOCKER_GPU_FAILED_SPIRV_DIR",
            manifest["env_bridge_classifications"]["icd_to_executor_string_option"],
        )
        analyzer = SPIRV_ANALYZER.read_text()
        for marker in [
            "pdocker.spirv.analysis.v1",
            "op_histogram",
            "duplicate_bindings",
            "control_flow",
            "probe_plan",
            "bisect_rounds",
            "probe-manifest.v1",
            "fragment_submission_allowed",
            "debug descriptor must not collide",
            "append-as-normal-vulkan-dispatch-v4-binding",
            "globally-unused-binding-number",
            "instrument-valid-module-not-arbitrary-fragment",
            "risk_notes",
            "uses 8-bit storage",
            "uses specialization-controlled workgroup size",
        ]:
            self.assertIn(marker, analyzer)

    def test_spirv_analyzer_counts_embedded_q6k_module(self):
        source = GPU_EXECUTOR.read_text()
        match = re.search(
            r"static const uint32_t kQ6kSafeSpv\[\] = \{(?P<body>.*?)\n\};",
            source,
            re.S,
        )
        self.assertIsNotNone(match)
        words = [
            int(token.rstrip("uU"), 0)
            for token in re.findall(r"0x[0-9a-fA-F]+[uU]?|\b\d+[uU]?\b", match.group("body"))
        ]
        self.assertGreater(len(words), 5)
        with tempfile.TemporaryDirectory() as tmp:
            spv = Path(tmp) / "q6k-safe.spv"
            manifest = Path(tmp) / "q6k-safe.probe.json"
            spv.write_bytes(b"".join(word.to_bytes(4, "little") for word in words))
            result = subprocess.run(
                ["python3", str(SPIRV_ANALYZER), str(spv), "--probe-plan-out", str(manifest), "--probe-range", "0:2"],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            probe_manifest = json.loads(manifest.read_text())
            verified = subprocess.run(
                ["python3", str(SPIRV_PROBE_MANIFEST_VERIFIER), str(manifest)],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
        payload = json.loads(result.stdout)
        module = payload["modules"][0]
        self.assertEqual(module["bytes"], len(words) * 4)
        self.assertEqual(module["words"], len(words))
        self.assertEqual(module["instruction_count"], 570)
        self.assertEqual(module["entry_points"][0]["execution_model_name"], "GLCompute")
        self.assertEqual(module["entry_points"][0]["name"], "main")
        self.assertIn("workgroup_size_builtin", module)
        descriptor_by_binding = {item["binding"]: item for item in module["descriptor_variables"]}
        self.assertEqual(descriptor_by_binding[0]["pointee_layout"]["kind"], "struct")
        self.assertTrue(descriptor_by_binding[0]["pointee_layout"]["members"][0]["layout"]["NonWritable"])
        self.assertEqual(descriptor_by_binding[2]["pointee_layout"]["members"][0]["offset"], 0)
        self.assertFalse(descriptor_by_binding[2]["non_writable"])
        push_members = module["push_constant_blocks"][0]["members"]
        self.assertEqual(push_members[0]["name"], "ncols")
        self.assertEqual(push_members[0]["offset"], 0)
        self.assertEqual(push_members[12]["name"], "broadcast3")
        self.assertEqual(push_members[12]["offset"], 48)
        self.assertGreater(len(module["access_chains"]), 0)
        self.assertTrue(any(
            event["pointer_origin"].get("push_member", {}).get("name") == "ncols"
            for event in module["load_events"]
        ))
        self.assertTrue(any(
            event["pointer_origin"].get("base", {}).get("binding") == 2
            for event in module["store_events"]
        ))
        self.assertIn("op_histogram", module)
        self.assertGreater(module["control_flow"]["function_count"], 0)
        self.assertGreater(module["control_flow"]["block_count"], 0)
        self.assertTrue(module["control_flow"]["probe_plan"]["binary_search_supported"])
        self.assertIn("functions", module["control_flow"])
        self.assertGreater(len(module["control_flow"]["probe_plan"]["bisect_rounds"]), 0)
        self.assertEqual(probe_manifest["schema"], "pdocker.spirv.probe-manifest.v1")
        self.assertFalse(probe_manifest["policy"]["fragment_submission_allowed"])
        self.assertEqual(probe_manifest["probe_selection"]["candidate_range"], [0, 2])
        self.assertTrue(probe_manifest["debug_ssbo"]["descriptor"]["available"])
        self.assertEqual(probe_manifest["debug_ssbo"]["dispatch_transport"], "append-as-normal-vulkan-dispatch-v4-binding")
        self.assertFalse(probe_manifest["collision_checks"]["static_binding_number_collision"])
        self.assertIn("instrumented module must pass spirv-val after instrumentation", probe_manifest["validation_gates"]["messages"])
        self.assertTrue(probe_manifest["validation_gates"]["spirv_val_required"])
        self.assertTrue(json.loads(verified.stdout)["valid"])

    def test_tracked_q6_probe_manifests_verify_with_current_schema(self):
        manifests = [
            ROOT / "docs" / "test" / "spirv-q6k-safe-current" / "q6k-safe.probe.json",
            ROOT / "docs" / "test" / "spirv-q6k-native-adb45055" / "native-q6-source.probe.json",
            ROOT / "docs" / "test" / "spirv-q6k-native-adb45055" / "effective-q6-local-size-patched.probe.json",
        ]
        for manifest in manifests:
            result = subprocess.run(
                ["python3", str(SPIRV_PROBE_MANIFEST_VERIFIER), str(manifest)],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            payload = json.loads(result.stdout)
            self.assertTrue(payload["valid"], manifest)
            probe_payload = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertIsInstance(probe_payload.get("q6_probe_targets"), dict, manifest)

    def test_spirv_analyzer_reports_q6_native_workgroup_builtin_spec_contract(self):
        spv = ROOT / "docs" / "test" / "spirv-q6k-native-adb45055" / "native-q6-source.spv"
        self.assertTrue(spv.exists(), "native Q6 source SPIR-V evidence must be preserved")
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "native-q6.probe.json"
            result = subprocess.run(
                ["python3", str(SPIRV_ANALYZER), str(spv), "--probe-plan-out", str(manifest), "--probe-range", "0:2"],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            probe_manifest = json.loads(manifest.read_text())
            verified = subprocess.run(
                ["python3", str(SPIRV_PROBE_MANIFEST_VERIFIER), str(manifest)],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
        self.assertTrue(json.loads(verified.stdout)["valid"])
        payload = json.loads(result.stdout)
        module = payload["modules"][0]
        self.assertEqual(module["hash"], "0x1bf751845c5dce75")
        self.assertEqual(module["version"], "0x00010500")
        self.assertEqual(probe_manifest["validation_gates"]["target_env"], "vulkan1.2")
        self.assertEqual(module["local_size"], [1, 1, 1])
        workgroup = module["workgroup_size_builtin"]
        self.assertEqual(workgroup["kind"], "spec_constant_composite")
        self.assertEqual(
            [
                {"kind": "spec_constant", "default_u32": 1, "spec_id": 0},
                {"kind": "constant", "value_u32": 1},
                {"kind": "constant", "value_u32": 1},
            ],
            [
                {key: component[key] for key in ("kind",) + (("default_u32", "spec_id") if component["kind"] == "spec_constant" else ("value_u32",))}
                for component in workgroup["components"]
            ],
        )
        self.assertTrue(any("BuiltIn WorkgroupSize" in note for note in module["risk_notes"]))
        execution_shape = module["workgroup_execution_shape"]
        self.assertEqual(execution_shape["local_size"], [1, 1, 1])
        self.assertEqual(execution_shape["workgroup_size_default"], [1, 1, 1])
        self.assertEqual(execution_shape["workgroup_size_builtin_kind"], "spec_constant_composite")
        self.assertTrue(execution_shape["has_specialized_workgroup_builtin"])
        self.assertTrue(execution_shape["literal_matches_workgroup_default"])
        self.assertTrue(execution_shape["statically_consistent"])
        descriptor_by_binding = {item["binding"]: item for item in module["descriptor_variables"]}
        output_member_type = descriptor_by_binding[2]["pointee_layout"]["members"][0]["type"]
        self.assertEqual(output_member_type["kind"], "runtime_array")
        self.assertEqual(output_member_type["array_stride"], 4)
        self.assertEqual(output_member_type["element"]["kind"], "float")
        self.assertEqual(output_member_type["element"]["bits"], 32)
        self.assertTrue(any(
            event["pointer_origin"].get("base", {}).get("binding") == 2
            and event["pointer_origin"]["indices"][1]["expr"]["op"] == "OpIAdd"
            for event in module["store_events"]
        ))
        self.assertTrue(any(
            event["pointer_origin"].get("base", {}).get("storage_class") == "Workgroup"
            and event["object_expr"]["kind"] in ("load", "op", "id")
            for event in module["store_events"]
        ))
        q6_targets = probe_manifest["q6_probe_targets"]
        self.assertTrue(q6_targets["available"])
        self.assertEqual(q6_targets["final_output_store_count"], 2)
        self.assertEqual(q6_targets["workgroup_store_count"], 12)
        flow = q6_targets["final_store_value_flow"]
        self.assertEqual(flow["schema"], "pdocker.spirv.q6-final-store-value-flow.v1")
        self.assertEqual(flow["required_output_descriptor_binding"], 2)
        self.assertEqual(flow["debug_probe_descriptor"], {"set": 0, "binding": 5})
        self.assertTrue(flow["available"])
        self.assertEqual(flow["final_store_count"], 2)
        self.assertEqual(flow["valid_store_count"], 2)
        self.assertEqual([store["word_index"] for store in flow["stores"]], [3789, 6653])
        for store in flow["stores"]:
            self.assertTrue(store["valid"])
            self.assertEqual(store["output_store"]["base"]["binding"], 2)
            self.assertTrue(store["output_store"]["binding_matches_required"])
            self.assertTrue(store["stored_value"]["reaches_workgroup_load"])
            self.assertGreaterEqual(len(store["stored_value"]["workgroup_loads"]), 1)
            self.assertEqual(
                store["stored_value"]["workgroup_loads"][0]["pointer_base"]["storage_class"],
                "Workgroup",
            )
            self.assertFalse(store["stored_value"]["depends_on_debug_probe_binding"])
            self.assertFalse(store["output_index"]["depends_on_debug_probe_binding"])
            self.assertTrue(store["debug_probe_exclusion"]["passed"])
            self.assertNotIn(
                5,
                [dep["binding"] for dep in store["stored_value"]["descriptor_dependencies"]],
            )
            self.assertNotIn(
                5,
                [dep["binding"] for dep in store["output_index"]["descriptor_dependencies"]],
            )
        by_phase = {phase["name"]: phase for phase in q6_targets["phases"]}
        self.assertEqual(by_phase["tail"]["source_workgroup_base_ids"], [143])
        self.assertEqual(by_phase["full"]["source_workgroup_base_ids"], [143])
        self.assertEqual(by_phase["tail"]["output_store"]["word_index"], 3789)
        self.assertEqual(by_phase["full"]["output_store"]["word_index"], 6653)
        self.assertEqual(by_phase["tail"]["output_store"]["base"]["kind"], "descriptor")
        self.assertEqual(by_phase["tail"]["output_store"]["base"]["binding"], 2)
        self.assertEqual(
            [
                ("partial_to_workgroup_candidate", 3334, 39),
                ("reduction_candidate", 3487, 49),
            ],
            [
                (target["role"], target["word_index"], target["candidate"]["candidate_id"])
                for target in by_phase["tail"]["preceding_workgroup_stores"][:2]
            ],
        )
        tail_post = by_phase["tail"]["preceding_workgroup_stores"][2:4]
        self.assertEqual([61, 63], [target["candidate"]["candidate_id"] for target in tail_post])
        self.assertEqual(
            [2825, 2847],
            [target["control_dependencies"][0]["condition_id"] for target in tail_post],
        )
        self.assertEqual(["true", "true"], [target["control_dependencies"][0]["branch_side"] for target in tail_post])
        self.assertEqual(
            [
                ("variable", "Workgroup"),
                ("variable", "Workgroup"),
            ],
            [
                (target["base"]["kind"], target["base"]["storage_class"])
                for target in by_phase["full"]["preceding_workgroup_stores"][:2]
            ],
        )
        full_post = by_phase["full"]["preceding_workgroup_stores"][2:4]
        self.assertEqual([127, 129], [target["candidate"]["candidate_id"] for target in full_post])
        self.assertEqual(
            [1822, 1844],
            [target["control_dependencies"][0]["condition_id"] for target in full_post],
        )
        self.assertEqual(["true", "true"], [target["control_dependencies"][0]["branch_side"] for target in full_post])
        self.assertEqual(
            [
                ("partial_to_workgroup_candidate", 6198, 105),
                ("reduction_candidate", 6351, 115),
            ],
            [
                (target["role"], target["word_index"], target["candidate"]["candidate_id"])
                for target in by_phase["full"]["preceding_workgroup_stores"][:2]
            ],
        )

    def test_spirv_analyzer_reports_q6_effective_workgroup_shape_mismatch(self):
        native_spv = ROOT / "docs" / "test" / "spirv-q6k-native-adb45055" / "native-q6-source.spv"
        effective_spv = ROOT / "docs" / "test" / "spirv-q6k-native-adb45055" / "effective-q6-local-size-patched.spv"
        self.assertTrue(native_spv.exists(), "native Q6 source SPIR-V evidence must be preserved")
        self.assertTrue(effective_spv.exists(), "effective Q6 local-size evidence must be preserved")
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            native_analysis = tmp_path / "native.analysis.json"
            effective_analysis = tmp_path / "effective.analysis.json"
            compare_out = tmp_path / "native-vs-effective.json"
            subprocess.run(
                ["python3", str(SPIRV_ANALYZER), str(native_spv), "--json-out", str(native_analysis)],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            subprocess.run(
                ["python3", str(SPIRV_ANALYZER), str(effective_spv), "--json-out", str(effective_analysis)],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            result = subprocess.run(
                [
                    "python3",
                    str(SPIRV_DATAFLOW_COMPARE),
                    str(native_analysis),
                    str(effective_analysis),
                    "--json-out",
                    str(compare_out),
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(result.returncode, 2, result.stderr)
            native_module = json.loads(native_analysis.read_text())["modules"][0]
            effective_module = json.loads(effective_analysis.read_text())["modules"][0]
            comparison = json.loads(compare_out.read_text())

        self.assertTrue(native_module["workgroup_execution_shape"]["statically_consistent"])
        effective_shape = effective_module["workgroup_execution_shape"]
        self.assertEqual(effective_shape["local_size"], [32, 1, 1])
        self.assertEqual(effective_shape["workgroup_size_default"], [1, 1, 1])
        self.assertFalse(effective_shape["literal_matches_workgroup_default"])
        self.assertFalse(effective_shape["statically_consistent"])
        self.assertTrue(any("literal LocalSize differs" in note for note in effective_module["risk_notes"]))
        q6_flow = next(item for item in comparison["comparisons"] if item["name"] == "q6_final_store_value_flow")
        self.assertTrue(q6_flow["match"])
        shape = next(item for item in comparison["comparisons"] if item["name"] == "workgroup_execution_shape")
        self.assertFalse(shape["match"])
        self.assertIn("workgroup_execution_shape.local_size[0]", shape["diff_paths"])
        self.assertIn("workgroup_execution_shape.literal_matches_workgroup_default", shape["diff_paths"])

    def test_spirv_probe_manifest_verifier_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "unsafe.json"
            manifest.write_text(json.dumps({
                "schema": "pdocker.spirv.probe-manifest.v1",
                "policy": {
                    "submission_model": "fragment",
                    "fragment_submission_allowed": True,
                    "llama_cpp_modified": False,
                    "dockerfile_model_prompt_modified": False,
                },
                "debug_ssbo": {
                    "dispatch_transport": "custom-v5",
                    "descriptor": {"available": False}
                },
                "collision_checks": {"decision": "fail"},
                "validation_gates": {
                    "spirv_val_required": False,
                    "dispatch_allowed": True,
                    "messages": []
                },
                "probe_selection": {
                    "candidate_range": [0, 1],
                    "selected_candidate_count": 0,
                    "selected_candidates": []
                },
            }))
            result = subprocess.run(
                ["python3", str(SPIRV_PROBE_MANIFEST_VERIFIER), str(manifest)],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        self.assertNotEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["valid"])
        self.assertIn("fragment submission must be explicitly disabled", payload["errors"])

    def test_spirv_probe_manifest_verifier_recomputes_manifest_safety(self):
        source = GPU_EXECUTOR.read_text()
        match = re.search(
            r"static const uint32_t kQ6kSafeSpv\[\] = \{(?P<body>.*?)\n\};",
            source,
            re.S,
        )
        self.assertIsNotNone(match)
        words = [
            int(token.rstrip("uU"), 0)
            for token in re.findall(r"0x[0-9a-fA-F]+[uU]?|\b\d+[uU]?\b", match.group("body"))
        ]
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spv = tmp_path / "q6k-safe.spv"
            manifest = tmp_path / "q6k-safe.probe.json"
            spv.write_bytes(b"".join(word.to_bytes(4, "little") for word in words))
            subprocess.run(
                ["python3", str(SPIRV_ANALYZER), str(spv), "--probe-plan-out", str(manifest), "--probe-range", "0:2"],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            valid = json.loads(manifest.read_text())

            cases = []
            broken = json.loads(json.dumps(valid))
            broken["basis"]["module_hash"] = "0x0000000000000000"
            cases.append((broken, "basis.module_hash mismatch"))

            broken = json.loads(json.dumps(valid))
            broken["debug_ssbo"]["descriptor"]["binding"] = broken["descriptors"]["declared"][0]["binding"]
            broken["collision_checks"]["proposed"]["binding"] = broken["debug_ssbo"]["descriptor"]["binding"]
            cases.append((broken, "debug descriptor binding number collides with declared descriptor"))

            broken = json.loads(json.dumps(valid))
            broken["probe_selection"]["selected_candidates"][0]["candidate_id"] = 99
            cases.append((broken, "candidate_id 99 is outside selected candidate_range"))

            broken = json.loads(json.dumps(valid))
            broken["probe_selection"]["selected_candidates"][0]["block_entry_insert_after_phi_word_index"] = "bad"
            cases.append((broken, "block_entry_insert_after_phi_word_index must be an integer"))

            broken = json.loads(json.dumps(valid))
            broken["q6_probe_targets"]["priority_targets"][0]["role"] = "hash-targeted-shortcut"
            cases.append((broken, "q6_probe_targets.priority_targets[0].role must be one of"))

            broken = json.loads(json.dumps(valid))
            del broken["q6_probe_targets"]
            cases.append((broken, "q6_probe_targets must be present as an object"))

            broken = json.loads(json.dumps(valid))
            broken["q6_probe_targets"]["available"] = True
            broken["q6_probe_targets"]["priority_targets"] = [
                target for target in broken["q6_probe_targets"]["priority_targets"]
                if target.get("role") != "reduction_candidate"
            ]
            cases.append((broken, "q6_probe_targets.priority_targets must include a reduction_candidate when available"))

            broken = json.loads(json.dumps(valid))
            broken["q6_probe_targets"]["final_store_value_flow"]["stores"][0]["debug_probe_exclusion"]["output_index_depends_on_debug_probe"] = True
            cases.append((broken, "debug_probe_exclusion.output_index_depends_on_debug_probe must be false"))

            broken = json.loads(json.dumps(valid))
            target = broken["q6_probe_targets"]["priority_targets"][0]
            if target.get("role") == "final_output_store":
                target["base"] = {"kind": "variable", "storage_class": "Workgroup"}
                cases.append((broken, "base must be descriptor binding 2 for final_output_store"))

            for idx, (payload, expected_error) in enumerate(cases):
                path = tmp_path / f"broken-{idx}.json"
                path.write_text(json.dumps(payload))
                result = subprocess.run(
                    ["python3", str(SPIRV_PROBE_MANIFEST_VERIFIER), str(path)],
                    cwd=ROOT,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                self.assertNotEqual(result.returncode, 0, expected_error)
                self.assertTrue(
                    any(expected_error in error for error in json.loads(result.stdout)["errors"]),
                    result.stdout,
                )

    def test_native_q6_noop_instrumentation_validates_and_preserves_v4_probe_policy(self):
        spv = ROOT / "docs" / "test" / "spirv-q6k-native-adb45055" / "native-q6-source.spv"
        self.assertTrue(spv.exists(), "native Q6 SPIR-V evidence must be preserved")
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            manifest = tmp_path / "native-q6.probe.json"
            noop_spv = tmp_path / "native-q6.noop.spv"
            noop_manifest = tmp_path / "native-q6.noop.probe.json"
            subprocess.run(
                ["python3", str(SPIRV_ANALYZER), str(spv), "--probe-plan-out", str(manifest), "--probe-range", "0:2"],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            subprocess.run(
                ["python3", str(SPIRV_PROBE_MANIFEST_VERIFIER), str(manifest)],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            result = subprocess.run(
                [
                    "python3",
                    str(SPIRV_NOOP_INSTRUMENTER),
                    str(spv),
                    str(noop_spv),
                    "--manifest-in",
                    str(manifest),
                    "--manifest-out",
                    str(noop_manifest),
                    "--target-env",
                    "vulkan1.2",
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            verified = subprocess.run(
                ["python3", str(SPIRV_PROBE_MANIFEST_VERIFIER), str(noop_manifest)],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            manifest_payload = json.loads(noop_manifest.read_text())
        payload = json.loads(result.stdout)
        self.assertTrue(payload["valid"])
        self.assertEqual(payload["source_spirv_hash"], "0x1bf751845c5dce75")
        self.assertNotEqual(payload["source_spirv_hash"], payload["instrumented_spirv_hash"])
        instrumentation = payload["instrumentation"]
        self.assertEqual(instrumentation["kind"], "noop-debug-ssbo-declaration")
        self.assertEqual(instrumentation["executable_probe_writes"], 0)
        self.assertEqual(instrumentation["old_bound"], 3441)
        self.assertEqual(instrumentation["new_bound"], 3446)
        self.assertIn("u32_pointer", instrumentation["reserved_ids"])
        self.assertEqual(instrumentation["debug_descriptor"], {
            "binding": 5,
            "descriptor_type": "storage_buffer",
            "set": 0,
        })
        self.assertEqual(
            manifest_payload["debug_ssbo"]["dispatch_transport"],
            "append-as-normal-vulkan-dispatch-v4-binding",
        )
        self.assertEqual(manifest_payload["validation_gates"]["post_instrumentation"]["status"], "pass")
        self.assertEqual(
            manifest_payload["instrumented_spirv_hash"],
            payload["instrumented_spirv_hash"],
        )
        self.assertTrue(json.loads(verified.stdout)["valid"])

    def test_native_q6_probe_write_instrumentation_validates(self):
        spv = ROOT / "docs" / "test" / "spirv-q6k-native-adb45055" / "native-q6-source.spv"
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            manifest = tmp_path / "native-q6.probe.json"
            write_spv = tmp_path / "native-q6.write.spv"
            write_manifest = tmp_path / "native-q6.write.probe.json"
            subprocess.run(
                [
                    "python3",
                    str(SPIRV_ANALYZER),
                    str(spv),
                    "--probe-plan-out",
                    str(manifest),
                    "--probe-range",
                    "0:2",
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            result = subprocess.run(
                [
                    "python3",
                    str(SPIRV_NOOP_INSTRUMENTER),
                    str(spv),
                    str(write_spv),
                    "--manifest-in",
                    str(manifest),
                    "--manifest-out",
                    str(write_manifest),
                    "--target-env",
                    "vulkan1.2",
                    "--probe-writes",
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            verified = subprocess.run(
                ["python3", str(SPIRV_PROBE_MANIFEST_VERIFIER), str(write_manifest)],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
        payload = json.loads(result.stdout)
        instrumentation = payload["instrumentation"]
        self.assertEqual(instrumentation["kind"], "q6-debug-ssbo-probe-writes")
        self.assertEqual(instrumentation["executable_probe_writes"], 10)
        self.assertTrue(
            all(item.get("schema_version") == 2 for item in instrumentation["probe_writes"])
        )
        self.assertTrue(
            all(
                "computed_output_index" in item.get("record_layout", {})
                for item in instrumentation["probe_writes"]
                if item.get("role") == "final_output_store"
            )
        )
        lane_probe_writes = [
            item for item in instrumentation["probe_writes"]
            if item.get("lane_trace_layout") is not None
        ]
        self.assertEqual(
            [(item["role"], item["phase"]) for item in lane_probe_writes],
            [
                ("partial_to_workgroup_candidate", "full"),
                ("reduction_candidate", "full"),
            ],
        )
        self.assertTrue(
            all(item["lane_trace_layout"]["lane_count"] == 64 for item in lane_probe_writes)
        )
        self.assertEqual(
            [item["role"] for item in instrumentation["probe_writes"]],
            [
                "partial_to_workgroup_candidate",
                "reduction_candidate",
                "post_reduction_workgroup_candidate",
                "post_reduction_workgroup_candidate",
                "final_output_store",
                "partial_to_workgroup_candidate",
                "reduction_candidate",
                "post_reduction_workgroup_candidate",
                "post_reduction_workgroup_candidate",
                "final_output_store",
            ],
        )
        self.assertTrue(json.loads(verified.stdout)["valid"])

    def test_spirv_dataflow_compare_self_match_reports_pointer_origins(self):
        analysis = ROOT / "docs" / "test" / "spirv-q6k-safe-current" / "q6k-safe.analysis.json"
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "compare.json"
            result = subprocess.run(
                ["python3", str(SPIRV_DATAFLOW_COMPARE), str(analysis), str(analysis), "--json-out", str(out)],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            self.assertEqual(result.returncode, 0)
            payload = json.loads(out.read_text())
        self.assertTrue(payload["all_match"])
        self.assertEqual(payload["left"]["descriptors"][0]["layout"]["kind"], "struct")
        descriptor_comparison = next(item for item in payload["comparisons"] if item["name"] == "descriptors")
        self.assertIn("layout", descriptor_comparison["left"][0])
        self.assertIn("push[0:ncols@0]", payload["left"]["loads"]["push_origins"])
        self.assertIn("descriptor[0,2](0,id:357:)", payload["left"]["stores"]["descriptor_origins"])
        self.assertTrue(all(item["match"] for item in payload["comparisons"]))

    def test_spirv_dataflow_compare_reports_exact_mismatch_paths(self):
        analysis = ROOT / "docs" / "test" / "spirv-q6k-safe-current" / "q6k-safe.analysis.json"
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            mutated = tmp_path / "mutated.analysis.json"
            out = tmp_path / "compare.json"
            payload = json.loads(analysis.read_text())
            module = payload["modules"][0] if payload.get("schema") == "pdocker.spirv.analysis.bundle.v1" else payload
            module["descriptor_variables"][0]["pointee_layout"]["members"][0]["type"]["array_stride"] = 8
            module["push_constant_blocks"][0]["members"][0]["offset"] = 4
            mutated.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
            result = subprocess.run(
                ["python3", str(SPIRV_DATAFLOW_COMPARE), str(analysis), str(mutated), "--json-out", str(out)],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(result.returncode, 2, result.stderr)
            report = json.loads(out.read_text())

        self.assertFalse(report["all_match"])
        descriptor_comparison = next(item for item in report["comparisons"] if item["name"] == "descriptors")
        self.assertFalse(descriptor_comparison["match"])
        self.assertIn(
            "descriptors[0].layout.members[0].type.array_stride",
            descriptor_comparison["diff_paths"],
        )
        self.assertEqual(
            descriptor_comparison["first_mismatch_path"],
            "descriptors[0].layout.members[0].type.array_stride",
        )
        push_comparison = next(item for item in report["comparisons"] if item["name"] == "push_constants")
        self.assertFalse(push_comparison["match"])
        self.assertIn("push_constants[0].offset", push_comparison["diff_paths"])
        self.assertIn("path_diffs", descriptor_comparison)
        self.assertEqual(descriptor_comparison["path_diffs"][0]["kind"], "value")

    def test_spirv_dataflow_compare_reports_q6_final_store_flow_paths(self):
        def flow_payload(op_count: int) -> dict:
            return {
                "schema": "pdocker.spirv.analysis.v1",
                "path": "synthetic.spv",
                "hash": "0xsynthetic",
                "bytes": 4,
                "instruction_count": 1,
                "entry_points": [],
                "local_size": [1, 1, 1],
                "local_size_id": [0, 0, 0],
                "descriptor_variables": [],
                "push_constant_blocks": [],
                "load_events": [],
                "store_events": [],
                "q6_probe_targets": {
                    "final_store_value_flow": {
                        "available": True,
                        "final_store_count": 1,
                        "valid_store_count": 1,
                        "stores": [
                            {
                                "phase": "tail",
                                "output_store": {
                                    "base": {"kind": "descriptor", "set": 0, "binding": 2},
                                    "binding_matches_required": True,
                                },
                                "stored_value": {
                                    "reaches_workgroup_load": True,
                                    "workgroup_loads": [{"pointer_base": {"storage_class": "Workgroup"}}],
                                    "op_histogram": {"OpIAdd": op_count},
                                },
                                "output_index": {"op_histogram": {"OpIAdd": 1}},
                                "debug_probe_exclusion": {"passed": True},
                                "valid": True,
                            }
                        ],
                    }
                },
            }

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            left = tmp_path / "left.analysis.json"
            right = tmp_path / "right.analysis.json"
            out = tmp_path / "compare.json"
            left.write_text(json.dumps(flow_payload(2), indent=2, sort_keys=True) + "\n")
            right.write_text(json.dumps(flow_payload(3), indent=2, sort_keys=True) + "\n")
            result = subprocess.run(
                ["python3", str(SPIRV_DATAFLOW_COMPARE), str(left), str(right), "--json-out", str(out)],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(result.returncode, 2, result.stderr)
            report = json.loads(out.read_text())

        q6_flow = next(item for item in report["comparisons"] if item["name"] == "q6_final_store_value_flow")
        self.assertFalse(q6_flow["match"])
        self.assertIn(
            "q6_final_store_value_flow.stores[0].stored_value_op_histogram.OpIAdd",
            q6_flow["diff_paths"],
        )
        self.assertEqual(q6_flow["path_diffs"][0]["kind"], "value")


    def test_spirv_dataflow_compare_catches_event_paths_when_origins_match(self):
        def dynamic_index_expr(op: str) -> dict:
            return {
                "id": 2,
                "name": "",
                "constant_u32": None,
                "expr": {
                    "kind": "op",
                    "op": op,
                    "operands": [
                        {"kind": "constant", "value_u32": 1},
                        {"kind": "constant", "value_u32": 2},
                    ],
                },
            }

        def descriptor_pointer(binding: int, op: str = "OpIAdd") -> dict:
            return {
                "kind": "access_chain",
                "base": {"kind": "descriptor", "set": 0, "binding": binding, "storage_class": "Uniform"},
                "indices": [
                    {"constant_u32": 0},
                    dynamic_index_expr(op),
                ],
            }

        def analysis_payload(op: str, object_binding: int) -> dict:
            return {
                "schema": "pdocker.spirv.analysis.v1",
                "path": "synthetic.spv",
                "hash": "0xsynthetic",
                "bytes": 4,
                "instruction_count": 1,
                "entry_points": [],
                "local_size": [1, 1, 1],
                "local_size_id": [0, 0, 0],
                "descriptor_variables": [],
                "push_constant_blocks": [],
                "load_events": [
                    {"word_index": 10, "pointer_origin": descriptor_pointer(2, op)},
                ],
                "store_events": [
                    {
                        "word_index": 20,
                        "pointer_origin": descriptor_pointer(2, "OpIAdd"),
                        "object_expr": {"kind": "load", "pointer": descriptor_pointer(object_binding, "OpIAdd")},
                    }
                ],
            }

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            left = tmp_path / "left.analysis.json"
            right = tmp_path / "right.analysis.json"
            out = tmp_path / "compare.json"
            left.write_text(json.dumps(analysis_payload("OpIAdd", 0), indent=2, sort_keys=True) + "\n")
            right.write_text(json.dumps(analysis_payload("OpISub", 1), indent=2, sort_keys=True) + "\n")
            result = subprocess.run(
                ["python3", str(SPIRV_DATAFLOW_COMPARE), str(left), str(right), "--json-out", str(out)],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(result.returncode, 2, result.stderr)
            report = json.loads(out.read_text())

        load_origins = next(item for item in report["comparisons"] if item["name"] == "load_origins")
        load_paths = next(item for item in report["comparisons"] if item["name"] == "load_paths")
        store_origins = next(item for item in report["comparisons"] if item["name"] == "store_origins")
        store_paths = next(item for item in report["comparisons"] if item["name"] == "store_paths")
        self.assertTrue(load_origins["match"])
        self.assertFalse(load_paths["match"])
        self.assertTrue(store_origins["match"])
        self.assertFalse(store_paths["match"])
        self.assertIn("OpIAdd", json.dumps(load_paths["diffs"], sort_keys=True))
        self.assertIn("OpISub", json.dumps(load_paths["diffs"], sort_keys=True))
        self.assertIn('"binding": 0', json.dumps(store_paths["diffs"], sort_keys=True))
        self.assertIn('"binding": 1', json.dumps(store_paths["diffs"], sort_keys=True))


    def test_vulkan_guarded_memory_profile_is_recorded(self):
        source = VULKAN_ICD.read_text()
        for marker in [
            "guarded_sigsegv_handler",
            "guarded_page_count",
            "trace_guarded_binding",
            "resident_pages=%zu dirty_pages=%zu",
            "trace_guarded_binding(i, dispatch_memory, dispatch_offset, bytes)",
        ]:
            self.assertIn(marker, source)
        compare = LLAMA_COMPARE.read_text()
        for field in [
            "guarded_binding_samples",
            "guarded_binding_max_resident_bytes",
            "guarded_binding_max_dirty_bytes",
            "guarded_binding_max_range_bytes",
        ]:
            self.assertIn(field, compare)

    def test_llama_compare_summarizes_binding_timing(self):
        compare = LLAMA_COMPARE.read_text()
        for field in [
            "binding_timing_samples",
            "binding_upload_ms_max",
            "binding_download_ms_max",
            "top_binding_uploads",
            "top_binding_downloads",
            "largest_shader_events",
            "largest_binding_events",
            "gpu_correctness_mismatch",
        ]:
            self.assertIn(field, compare)
        self.assertIn('detail.get("upload_ms")', compare)
        self.assertIn('detail.get("download_ms")', compare)

    def test_llama_compare_retains_q6_dispatch_evidence_ahead_of_tail_sampling(self):
        compare = LLAMA_COMPARE.read_text()
        for marker in [
            "Q6_K_MATVEC_SPIRV_HASHES",
            "0x1bf751845c5dce75",
            "q6_valid_spirv_events",
            "q6_dispatch_lifecycle_events",
            "retain_diagnostic_events",
            '"q6_candidate_events"',
            '"q6_dispatch_lifecycle_events"',
            '"q6_dispatch_seen"',
            '"q6_oracle_capture_missing"',
            '"q6-oracle-capture-missing"',
            "q6-dispatch-seen-without-oracle-response",
        ]:
            self.assertIn(marker, compare)

    def test_llama_compare_log_fallback_does_not_depend_on_android_python(self):
        compare = LLAMA_COMPARE.read_text()
        start = compare.index("container_logs()")
        end = compare.index("container_archive_file()", start)
        body = compare[start:end]
        self.assertIn("llama workspace scan fallback", body)
        self.assertIn("workspaces/*/logs/llama-server.log", body)
        self.assertIn("container rootfs llama log fallback", body)
        self.assertIn("--- pdocker engine log:", body)
        self.assertNotIn("python3 -", body)
        self.assertNotIn("from pathlib import Path", body)

    def test_llama_compare_deduplicates_merged_executor_log_events(self):
        compare = LLAMA_COMPARE.read_text()
        start = compare.index("def extract_executor_json_events(text):")
        end = compare.index("def observed_event_values(events, field):", start)
        body = compare[start:end]
        self.assertIn("seen_events = set()", body)
        self.assertIn("json.dumps(event, sort_keys=True", body)
        self.assertIn("if event_key in seen_events:", body)
        self.assertIn("continue", body)
        self.assertIn("splitlines(keepends=True)", body)

    def test_llama_compare_records_server_token_probabilities(self):
        compare = LLAMA_COMPARE.read_text()
        for marker in [
            "PDOCKER_LLAMA_N_PROBS",
            '"completion_probabilities": n_probs > 0',
            '"n_probs": n_probs',
            '"selected_token"',
            '"top_logprobs"',
            "def probe_probability_map(report):",
            "differential_probabilities",
            "cpu_selected_rank_in_gpu_top",
            "gpu_selected_rank_in_cpu_top",
            "shared_top_token_ids",
            "top1_mismatch_count",
            "selected_token_mismatch_count",
        ]:
            self.assertIn(marker, compare)

    def test_llama_compare_records_bisection_and_config_propagation(self):
        compare = LLAMA_COMPARE.read_text()
        compare_and_manifest = compare + "\n" + LLAMA_GPU_ENV_MANIFEST.read_text()
        for marker in [
            "config_propagation",
            "config_expectations",
            "config_propagation_mismatch",
            "observed_event_values(executor_events",
            "disable_pipeline_optimization",
            "skip_unused_descriptor_transfers",
            "spirv_descriptor_access",
            "disable_overlap_aliasing",
            "diagnostic_bisection",
            "binary-search fault isolation",
            "api_cpu_baseline",
            "gpu_server_output",
            "token_probability_boundary",
            "executor_dispatch_boundary",
            "post_dispatch_logits",
            "readonly_input_integrity",
            "readonly_binding_hash_mismatches",
            "primary_readonly_upload_hash_mismatches",
            "primary_readonly_dispatch_mutations",
            "shader_access_or_barrier_scope",
            "alias_rep",
            "upload_offset_descriptor_or_hash_scope",
            "output_layout_or_shader_math",
            "numeric_layout_or_readback",
            "q6_blocker_class",
            "q6_shader_like_oracle_cleared",
            "q6_first_mismatch",
            "q6_row_indexed_sample_indices",
            "q6_row_indexed_writeback_evidence",
            "q6_row_indexed_writeback_verified",
            "row_indexed_samples_match_oracle",
            "q6_writable_bindings",
            "q6_readonly_upload_hash_mismatches",
            "q6_readonly_dispatch_mutations",
            "q6_readonly_dispatch_alias_side_effects",
            "readonly_overlap_snapshot",
            "readonly_overlap_source_index",
            "readonly_overlap_snapshot_bytes",
            "q6_readonly_overlap_snapshot_policy",
            "q6_readonly_overlap_snapshot_effective",
            "q6_readonly_overlap_snapshot_count",
            "q6_readonly_overlap_snapshot_bindings",
            "q6_unexpected_readonly_dispatch_mutations",
            "q6_descriptor_range_mismatches",
            "q6_debug_u32_probe",
            "q6_debug_u32_probe_blocker",
            "collect_q6_debug_probe_bindings",
            "parse_q6_final_store_trace_v2",
            "pre-reduction-store",
            "reduction-store",
            "executed_stage_trace_v2_count",
            "lane_trace_v1",
            "parse_q6_lane_trace_v1",
            "pre-reduction-lanes",
            "reduction-lanes",
            "q6-debug-u32-final-store-trace-missing",
            "trace_writeback_verified",
            "trace_writeback_mismatch",
            "trace_writeback_mismatch_fields",
            "final-store trace writeback mismatch",
            "q6_final_store_output_indices",
            "q6_latest_debug_u32_probe",
            "correlation_scope",
            "latest-q6-event",
            "q6_event_dispatch_id",
            "q6_event_effective_spirv_hash",
            "\"q6_event_source_spirv_hash\": binding.get(\"q6_event_source_spirv_hash\")",
            "debug_report = q6_latest_debug_u32_probe",
            "layout_sample_source",
            "layout_from_final_store_trace",
            "missing-final-store-layout-samples",
            "missing-final-store-writeback-samples",
            "q6_readonly_mutation_is_alias_side_effect",
            "same_q6_storage_window",
            "classify_q6_output_index_probe",
            "q6_output_index_probe_summary",
            "q6_store_window_begin",
            "q6_store_window_end",
            "binding_gpu_offset",
            "binding_descriptor_offset",
            "offset_equals_memory_plus_api_offset",
            "gpu_offset_equals_memory_plus_api_offset",
            "descriptor_offset_equals_api_offset",
            "descriptor_range_matches_api_range",
            "descriptor_range_mismatch",
            "api_memory_id",
            "api_buffer_id",
            "parse_int(left.get(\"api_buffer_id\"))",
            "parse_int(left.get(\"binding_descriptor_offset\"))",
            "q6_readonly_upload_hash_mismatches or q6_descriptor_range_mismatches",
            "best_index_in_store_window",
            "resolve_default_llama_image",
            "docker.io_pdocker_llama-cpp-gpu_latest",
            "using legacy local llama image alias",
            "best_store_row_delta",
            "writeback_offset",
            "writeback_bytes",
            "device_local_staged",
            "vulkan-device-execution-or-writeback",
            "descriptor-effective-range-or-upload",
        ]:
            self.assertIn(marker, compare_and_manifest)
        source = GPU_EXECUTOR.read_text()
        self.assertIn("Q6FinalStoreDebugIndex", source)
        self.assertIn("q6k_sample_plan_from_output_index", source)
        self.assertIn("from_final_store_trace", source)
        self.assertIn("final-store-trace", source)
        self.assertIn("fixed-oracle-window", source)
        self.assertIn("final_store_index_count", source)
        self.assertIn('\\"disable_pipeline_optimization\\":%s', source)
        self.assertIn('\\"skip_unused_descriptor_transfers\\":%s', source)
        self.assertIn('\\"spirv_descriptor_access\\":%s', source)
        self.assertIn('\\"disable_overlap_aliasing\\":%s', source)
        self.assertIn("uint64_t policy_hash", source)
        self.assertIn("vulkan_pipeline_policy_hash", source)
        self.assertIn("entry->policy_hash == policy_hash", source)
        self.assertIn('\\"pipeline_policy_hash\\":\\"0x%016llx\\"', source)
        self.assertIn('\\"api_offset\\":%lld', source)
        self.assertIn('\\"api_range\\":%zu', source)
        self.assertIn('\\"api_descriptor_type\\":%u', source)
        self.assertIn("write_spirv_binding_reflection_report", source)
        self.assertIn("write_cpu_oracle_report", source)
        self.assertIn("triage must not require turning on every descriptor dump", source)
        self.assertIn("cpu_oracle_known_llama_hash", source)
        self.assertIn("write_vulkan_rw_alias_hazard_report", source)
        self.assertIn('\\"rw_alias_hazards\\":{\\"count\\":%zu', source)
        self.assertIn("materialize_strict_readonly_overlap_snapshot", source)
        self.assertIn("q6_readonly_overlap_snapshot_auto", source)
        self.assertIn("materialize_readonly_overlap_snapshots", source)
        self.assertIn('\\"readonly_overlap_snapshot_policy\\":', source)
        self.assertIn("binding_read_needed[i] || binding_write_needed[i]", source)
        self.assertIn("!binding_read_needed[i] ||", source)
        self.assertIn("!binding_write_needed[i]", source)
        self.assertIn("bindings[i].api_memory_id != bindings[j].api_memory_id", source)
        self.assertIn("bindings[i].api_buffer_id != bindings[j].api_buffer_id", source)
        self.assertIn("vulkan_bindings_api_ranges_overlap", source)
        self.assertIn('\\"readonly_overlap_snapshot\\":%s', source)
        self.assertIn('\\"readonly_overlap_snapshots\\":%zu', source)
        self.assertIn('\\"push_u32\\":[', source)
        self.assertIn("run_cpu_oracle_q6k_matvec_sample", source)
        self.assertIn("mul-mat-vec-q6-k-large", source)
        self.assertIn("0xbefdfb97e9734eb3ull", source)
        self.assertIn('\\"partial_diagnostic\\":', source)
        self.assertIn("patch_spirv_literal_local_size_from_spec", source)
        self.assertIn("pipeline compatibility lowering", source)
        self.assertIn("found_local_size_spec", source)
        self.assertIn("summary.workgroup_size_spec_id_valid[dim]", source)
        self.assertIn("local_size[dim] = value;", source)
        self.assertIn("const SpirvTraceSummary requested_spirv_summary", source)
        self.assertIn("const uint64_t original_spirv_hash = requested_spirv_summary.hash;", source)
        self.assertIn("if (q6k_safe_kernel_requested && is_q6k_matvec_hash(original_spirv_hash))", source)
        self.assertIn("const uint64_t invocation_count", source)
        self.assertIn('\\"local_size_patched\\":%s', source)
        self.assertIn('\\"spirv_local_size\\":[%u,%u,%u]', source)
        self.assertIn('\\"spirv_workgroup_size_spec_id\\":[%u,%u,%u]', source)
        self.assertIn('\\"spirv_local_size_resolved\\":[%llu,%llu,%llu]', source)
        self.assertIn("cleanup_resolved_local_size", source)
        self.assertIn('\\"spirv_local_size_consistent\\":%s', source)
        self.assertIn("spirv_local_size_consistent(", source)
        self.assertIn("Strict passthrough is the ABI-preservation lane", source)
        self.assertIn("do not reject a driver-visible module solely because", source)
        self.assertIn("let the real Vulkan path decide", source)
        self.assertNotIn('fail_stage = "spirv-local-size-inconsistent";', source)
        self.assertNotIn('json_fail("spirv-local-size-inconsistent"', source)
        self.assertIn("patched LocalSize invariant failed", source)
        self.assertIn('fail_stage = "spirv-local-size-patch-inconsistent";', source)
        self.assertIn("local_size_patched) {", source)
        self.assertIn("spirv_local_invocation_count", source)
        self.assertIn("product > UINT64_MAX / local_size[i]", source)
        self.assertIn("invalid-q6-local-size", source)
        self.assertIn('\\"q6_local_size\\":[%llu,%llu,%llu]', source)
        self.assertIn('\\"q6_local_invocations\\":%llu', source)
        self.assertIn('\\"q6_stride_d\\":%llu', source)
        self.assertIn('\\"q6_batch_stride_d\\":%llu', source)
        self.assertIn('\\"q6_dispatch_groups\\":[%llu,%llu,%llu]', source)
        self.assertIn('\\"q6_block_size\\":%llu', source)
        self.assertIn('\\"q6_num_rows\\":%llu', source)
        self.assertIn('\\"q6_num_cols\\":%llu', source)
        self.assertIn('\\"q6_store_index_model_valid\\":%s', source)
        self.assertIn('\\"q6_store_window_begin\\":%llu', source)
        self.assertIn('\\"q6_store_window_end\\":%llu', source)
        self.assertIn('\\"expected_store_index\\":%llu', source)
        self.assertIn('\\"store_formula_valid\\":%s', source)
        self.assertIn('\\"store_workgroup\\":[%u,%u,%u]', source)
        self.assertIn('\\"store_row_in_group\\":%u', source)
        self.assertIn("q6k_matvec_store_index_from_dispatch", source)
        self.assertIn("q6k_row_to_dispatch_coordinates", source)
        self.assertIn('\\"best_index_in_store_window\\":%s', source)
        self.assertIn('\\"best_store_row_delta\\":%lld', source)
        self.assertIn('\\"binding_gpu_offset\\":%zu', source)
        self.assertIn('\\"binding_descriptor_offset\\":%zu', source)
        self.assertIn('\\"descriptor_range_mismatch\\":%s', source)
        self.assertIn('\\"q6_accum_mask\\":%llu', source)
        self.assertIn('\\"q6_base_work_group_y\\":%llu', source)
        self.assertIn('\\"q6_output_base_index\\":%llu', source)
        self.assertIn('\\"q6_weight_base_blocks\\":%llu', source)
        self.assertIn('\\"q6_accumulator_sum\\":%.9g', source)
        self.assertIn("load_le_u32(push, push_size, 7)", source)
        self.assertIn("load_le_u32(push, push_size, 8)", source)
        self.assertIn("q6k_read_accumulator_value", source)
        self.assertIn('\\"q6_shader_like_64_sum\\":%.9g', source)
        self.assertIn("q6_resolved_local_size", source)
        self.assertIn("q6_diag_lanes", source)
        self.assertIn('\\"cpu_oracle_requested\\":%s', source)
        self.assertIn('\\"pre_barriers\\":%u,\\"post_barriers\\":%u', source)
        self.assertIn("vkCmdPipelineBarrier(command_buffer,", source)
        self.assertIn("VK_ACCESS_HOST_WRITE_BIT", source)
        self.assertIn("VK_ACCESS_SHADER_WRITE_BIT", source)
        self.assertIn('\\"gpu_after_upload_hash\\":\\"0x%016llx\\"', source)
        self.assertIn("const int disable_pipeline_optimization =", source)

    def test_llama_gpu_artifact_gate_decision_tree_is_documented(self):
        verifier = LLAMA_GPU_ARTIFACT_VERIFIER.read_text()
        runbook = LLAMA_GPU_DEVICE_RUNBOOK.read_text()
        next_steps = LLAMA_GPU_NEXT_STEPS.read_text()
        for marker in [
            "oracle-fail-closed",
            "api-prompt-sanity-missing",
            "speedup-fields-missing",
            "REQUIRED_API_PROMPT_PROBES",
            "oracle_fail_closed",
            "cpu-oracle-required",
            "comparison.target_tokens_per_second",
            "bridge_overhead_phase",
            "q6_row_indexed_sample_indices",
            "q6_row_indexed_writeback_evidence",
            "q6_row_indexed_writeback_verified",
            "row_indexed_samples_match_oracle",
            "row_window/q6_first_mismatch dst indices",
            "Generic or exact-index f32 samples",
        ]:
            self.assertIn(marker, verifier + "\n" + runbook + "\n" + next_steps)
        for marker in [
            "Artifact Gate Decision Tree",
            "CPU baseline rule",
            "Web/API prompt sanity",
            "Speedup fields",
            "37 oracle fail-closed",
            "38 API prompt sanity missing",
            "39 speedup fields missing",
        ]:
            self.assertIn(marker, runbook)

    def test_row_indexed_q6_next_blocker_decision_tree_is_documented(self):
        docs = LLAMA_GPU_NEXT_STEPS.read_text() + "\n" + LLAMA_GPU_CORRECTNESS.read_text()
        for marker in [
            "Row-indexed Q6_K device-run decision tree",
            "Row-indexed Q6_K Next-Blocker Decision Tree",
            "memory-blocked",
            "insufficient_memory",
            "runtime_memory_pressure",
            "device_memory_blocked:true",
            "does not justify a C-side Q6 change",
            "q6_row_indexed_writeback_evidence",
            "q6_row_indexed_writeback_verified",
            "q6_writeback_verified_all",
            "f32_after_dispatch",
            "f32_after_writeback",
            "q6_row_indexed_sample_indices",
            "classify the next blocker as `writeback`",
            "writeback is verified + the Q6 oracle still mismatches",
            "q6_writeback_verified_all == true",
            "q6_row_indexed_writeback_verified == true",
            "latest_status == \"mismatch\"",
            "workgroup_shape_blocker == true",
            "spirv_local_size_consistent",
            "spirv_local_size_resolved",
            "workgroup-shape",
            "q6_shader_like_64_abs_delta",
            "Vulkan device-execution",
            "Q6 arithmetic/reduction/output-layout",
            "A sampled mismatch without row-indexed writeback evidence is not progress.",
        ]:
            self.assertIn(marker, docs)

    def test_llama_gpu_compare_can_forward_bridge_tuning_env(self):
        compare = LLAMA_COMPARE.read_text()
        self.assertIn("import os", compare)
        self.assertIn("def set_env(env, item):", compare)
        self.assertIn("env[idx] = item", compare)
        self.assertIn("set_env(env, f\"{key}={value}\")", compare)

    def test_llama_gpu_compare_memory_preflight_uses_available_memory(self):
        compare = LLAMA_COMPARE.read_text()
        self.assertIn("MemAvailable", compare)
        self.assertIn('"valid": False', compare)
        self.assertIn('"raw_bytes": len(raw.encode', compare)
        self.assertIn("memory_snapshot_is_valid()", compare)
        self.assertIn("preflight_free = int(", compare)
        self.assertIn("separator-only output", compare)
        self.assertIn('runtime memory sample unavailable', compare)
        self.assertIn('without treating missing /proc/meminfo as OOM', compare)
        self.assertNotIn("raw_bytes > 0", compare)
        self.assertIn('"mem_available_mb"', compare)
        self.assertIn('"mem_preflight_free_mb"', compare)
        self.assertIn('data.get("mem_preflight_free_mb")', compare)
        ensure_body = compare[
            compare.index("ensure_memory_headroom() {") : compare.index(
                "wait_for_memory_headroom() {"
            )
        ]
        runtime_body = compare[
            compare.index("runtime_memory_headroom_ok() {") : compare.index(
                "urlencode() {"
            )
        ]
        for body in [ensure_body, runtime_body]:
            guard = body.index('memory_snapshot_is_valid "$snap"')
            free_parse = body.index('free_mb="$(python3 - "$snap"')
            self.assertLess(guard, free_parse)
            self.assertIn("return 0", body[guard:free_parse])
        self.assertIn('wait_for_memory_headroom "preflight before daemon start"', compare)
        preflight_call = compare.index('wait_for_memory_headroom "preflight before daemon start"')
        self.assertLess(
            preflight_call,
            compare.index("restart_app_daemon_for_test", preflight_call),
        )
        self.assertIn("runtime_memory_headroom_ok", compare)
        self.assertIn('CORRECTNESS_TIMEOUT_SEC="${PDOCKER_LLAMA_CORRECTNESS_TIMEOUT_SEC:-180}"', compare)
        self.assertIn("correctness probe", compare)
        self.assertIn("timeout={timeout_sec}s", compare)
        self.assertIn("RUNTIME_MIN_SWAP_FREE_MB", compare)
        self.assertIn('MIN_SWAP_FREE_MB="${PDOCKER_LLAMA_MIN_SWAP_FREE_MB:-0}"', compare)
        self.assertIn('SWAP_ADVISORY_MB="${PDOCKER_LLAMA_SWAP_ADVISORY_MB:-1024}"', compare)
        self.assertIn('WAIT_FOR_MEMORY_SEC="${PDOCKER_LLAMA_WAIT_FOR_MEMORY_SEC:-0}"', compare)
        self.assertIn("wait_for_memory_headroom", compare)
        self.assertIn('RUNTIME_MIN_SWAP_FREE_MB="${PDOCKER_LLAMA_RUNTIME_MIN_SWAP_FREE_MB:-0}"', compare)
        self.assertIn('RUNTIME_SWAP_ADVISORY_MB="${PDOCKER_LLAMA_RUNTIME_SWAP_ADVISORY_MB:-512}"', compare)
        self.assertIn("hard_gate_enabled", compare)
        self.assertIn("swap_pressure_advisory", compare)
        self.assertIn("SwapFree is advisory by default", compare)
        self.assertIn('STOP_ON_FAILURE="${PDOCKER_LLAMA_STOP_ON_FAILURE:-1}"', compare)
        self.assertIn('ENGINE_START_TIMEOUT_SEC="${PDOCKER_LLAMA_ENGINE_START_TIMEOUT_SEC:-15}"', compare)
        self.assertIn('ENGINE_CREATE_TIMEOUT_SEC="${PDOCKER_LLAMA_ENGINE_CREATE_TIMEOUT_SEC:-120}"', compare)
        self.assertIn('ENGINE_CREATE_SETTLE_TIMEOUT_SEC="${PDOCKER_LLAMA_ENGINE_CREATE_SETTLE_TIMEOUT_SEC:-60}"', compare)
        self.assertIn('ENGINE_CREATE_POLL_INTERVAL_SEC="${PDOCKER_LLAMA_ENGINE_CREATE_POLL_INTERVAL_SEC:-2}"', compare)
        self.assertIn('ENGINE_CLEANUP_TIMEOUT_SEC="${PDOCKER_LLAMA_ENGINE_CLEANUP_TIMEOUT_SEC:-60}"', compare)
        self.assertIn('RUN_AS_TIMEOUT_SEC="${PDOCKER_LLAMA_RUN_AS_TIMEOUT_SEC:-30}"', compare)
        self.assertIn('RUN_AS_CLEANUP_TIMEOUT_SEC="${PDOCKER_LLAMA_RUN_AS_CLEANUP_TIMEOUT_SEC:-5}"', compare)
        self.assertIn('timeout "${run_timeout}s" "$ADB" push', compare)
        self.assertIn('timeout "${cleanup_timeout}s" "$ADB" shell', compare)
        self.assertIn("|| rc=$?", compare)
        self.assertIn('RUN_AS_TIMEOUT_SEC="$timeout_sec" engine_request "$@"', compare)
        self.assertNotIn("timeout \"${timeout_sec}s\" bash -c 'engine_request", compare)
        self.assertIn('OPERATION_NOTIFY_TIMEOUT_SEC="${PDOCKER_LLAMA_OPERATION_NOTIFY_TIMEOUT_SEC:-3}"', compare)
        self.assertIn('WAIT_SERVER_PROGRESS_INTERVAL_SEC="${PDOCKER_LLAMA_WAIT_SERVER_PROGRESS_INTERVAL_SEC:-10}"', compare)
        self.assertIn('WAIT_SERVER_CURL_TIMEOUT_SEC="${PDOCKER_LLAMA_WAIT_SERVER_CURL_TIMEOUT_SEC:-2}"', compare)
        self.assertIn('COMPARE_ARTIFACT_DIR="${PDOCKER_LLAMA_COMPARE_ARTIFACT_DIR:-}"', compare)
        self.assertIn("record_wait_server_event", compare)
        self.assertIn("wait-server.jsonl", compare)
        self.assertIn('"curl_exit"', compare)
        self.assertIn('"curl_http_code"', compare)
        self.assertIn('"http_probe"', compare)
        self.assertIn('"port_forward"', compare)
        self.assertIn('"wait_failure_class"', compare)
        self.assertIn("connection-refused", compare)
        self.assertIn("http-503", compare)
        self.assertIn("container-not-running", compare)
        self.assertIn("stale-same-device-http-target-not-running", compare)
        self.assertIn("refusing stale server evidence", compare)
        self.assertLess(
            compare.index("stale-same-device-http-target-not-running"),
            compare.index("server is reachable: elapsed="),
        )
        self.assertIn("toybox nc -U -W $OPERATION_NOTIFY_TIMEOUT_SEC", compare)
        executor = (ROOT / "app/src/main/cpp/pdocker_gpu_executor.c").read_text()
        self.assertIn('strstr(cmd, " dispatch_id=")', executor)
        self.assertIn("core_command_hash_comparable", executor)
        self.assertIn('json_match_or_null(rx && rx->core_command_hash_comparable', executor)
        self.assertIn('FORCED_VULKAN_WAIT_SERVER_TIMEOUT_SEC="${PDOCKER_LLAMA_FORCED_VULKAN_WAIT_SERVER_TIMEOUT_SEC:-240}"', compare)
        self.assertIn('wait_server "$FORCED_VULKAN_WAIT_SERVER_TIMEOUT_SEC" "Forced Vulkan"', compare)
        self.assertIn("waiting for $phase server", compare)
        self.assertIn("$mode: creating container", compare)
        self.assertIn('engine_request_with_host_timeout "$ENGINE_CLEANUP_TIMEOUT_SEC" DELETE', compare)
        self.assertIn('engine_request_with_host_timeout "$ENGINE_CREATE_TIMEOUT_SEC" POST "/containers/create', compare)
        self.assertIn("create request did not return", compare)
        self.assertIn("create response did not include a JSON Id", compare)
        self.assertIn("container create returned no JSON Id", compare)
        self.assertLess(
            compare.index("create response did not include a JSON Id"),
            compare.index('cid="$(printf "%s" "$create_body" | parse_engine_id)'),
        )
        self.assertIn('inspect_container_body "$CONTAINER"', compare)
        self.assertIn("poll_container_after_create_timeout", compare)
        self.assertIn("delayed create became inspectable", compare)
        self.assertIn("waiting for delayed create completion", compare)
        self.assertIn("delayed create is inspectable but still creating", compare)
        self.assertIn("wait_container_absent", compare)
        self.assertIn("still inspectable after", compare)
        self.assertIn("remove_container_after_failure", compare)
        self.assertIn('STOP_STALE_TARGET_BEFORE_PREFLIGHT="${PDOCKER_LLAMA_STOP_STALE_TARGET_BEFORE_PREFLIGHT:-1}"', compare)
        self.assertIn("stop_stale_target_if_engine_alive", compare)
        self.assertIn("Do not start", compare)
        self.assertIn("engine_request_with_host_timeout", compare)
        self.assertIn("continuing with runtime watchdog", compare)
        self.assertIn("engine-start-timeout", compare)
        self.assertIn('"runtime_memory_pressure"', compare)
        self.assertIn('"runtime_abort": runtime_abort', compare)
        self.assertIn('"device_actions"', compare)
        self.assertIn("do not force-stop the browser/VS Code session", compare)
        self.assertIn("do not rebuild the llama image", compare)
        self.assertIn('write_failure_artifact "$status"', compare)
        self.assertIn('"schema": "pdocker.llama.gpu.compare.failure.v1"', compare)
        self.assertIn('"adb_state": parse(sys.argv[8])', compare)
        self.assertIn('"pdocker_diagnostics": parse(sys.argv[7])', compare)
        self.assertIn("stage_spirv_probe_artifacts_for_container", compare)
        self.assertIn('stage_probe_artifact_for_container "PDOCKER_GPU_SPIRV_PROBE_SHADER"', compare)
        self.assertIn("/workspace/.pdocker-probes", compare)
        self.assertIn("Android app-private", compare)
        self.assertIn('if [[ -f "$source_path" ]]; then', compare)
        self.assertIn("/data/local/tmp/pdocker-probe-", compare)
        self.assertIn('"$ADB" push "$source_path" "$device_tmp"', compare)
        icd_source = VULKAN_ICD.read_text()
        self.assertIn("probe_memory_id", icd_source)
        self.assertIn("probe_buffer_id", icd_source)
        self.assertIn("deterministic non-zero pseudo object ids", icd_source)
        self.assertNotIn("api_memory_ids[binding_count] = 0;\n        api_buffer_ids[binding_count] = 0;", icd_source)
        self.assertIn('cp "$RUNTIME_ABORT_JSON" "$OUT"', compare)
        self.assertIn("remove_container >/dev/null 2>&1 || true", compare)
        self.assertIn("q6_workgroup_diagnostics", compare)
        self.assertIn("workgroup_shape_blocker", compare)
        self.assertIn("q6_expected_local_size", compare)
        self.assertNotIn("q6_expected_local_size = [1, 1, 1] if q6_safe_kernel_used else [32, 1, 1]", compare)
        self.assertIn("constant_id=1 is NUM_ROWS, not", compare)
        self.assertIn("q6_workgroup_specialization_interpretation", compare)
        self.assertIn("do_not_patch_local_size_y_from_spec_id_1", compare)
        self.assertIn("q6-final-store-workgroup-barrier-visibility", compare)
        self.assertIn("local_size_resolved=reported-q6-local-size", compare)
        self.assertIn("fix Q6_K local-size/NUM_ROWS separation", compare)
        self.assertIn("lower_q6k_storage16_loads_to_storage8", executor)
        self.assertIn("q6_storage16_loads_lowered", executor)
        self.assertIn("q6_storage16_loads_lowered_count", executor)
        self.assertIn("local_invocations_x / 16u", executor)
        self.assertIn("q6k_accumulate_tid_partial_shader_like", executor)
        self.assertNotIn("const uint32_t it_size = 32u / 16u;", executor)
        self.assertIn("(uint32_t)report->q6_local_size[0]", executor)
        self.assertIn("lower_q6k_u32_to_u8vec4_bitcasts", executor)
        self.assertIn("q6_u32_to_u8vec4_bitcasts_lowered", executor)
        self.assertIn("q6_u32_to_u8vec4_bitcasts_lowered_count", executor)
        self.assertIn("q6k_compat_rewrites_requested", executor)
        self.assertIn("q6_compat_rewrites_enabled", executor)
        self.assertIn('env_truthy("PDOCKER_GPU_Q6K_COMPAT_REWRITES", 0)', executor)
        self.assertIn('env_truthy("PDOCKER_GPU_Q6K_READONLY_OVERLAP_SNAPSHOT", 0)', executor)
        self.assertIn("insert_q6k_final_store_pre_barrier", executor)
        self.assertIn("q6_final_store_pre_barrier_inserted", executor)
        self.assertIn("q6_final_store_pre_barrier_inserted", compare)
        self.assertIn("q6_storage16_loads_lowered", compare)
        self.assertIn("q6_u32_to_u8vec4_bitcasts_lowered", compare)
        self.assertIn("q6_storage16_lowering_identity_hash", executor)
        self.assertIn("options->has_source_spirv_hash", executor)
        self.assertIn("options->source_spirv_hash", executor)
        self.assertIn("q6_probe_effective_replay", executor)
        self.assertIn("options->source_spirv_hash != options->effective_spirv_hash", executor)
        self.assertIn("options->effective_spirv_hash == original_spirv_hash", executor)
        self.assertIn("!q6_probe_effective_replay", executor)
        self.assertIn("else if (q6_probe_effective_replay && q6_native_callsite_detected", executor)
        self.assertIn("q6_final_store_pre_barrier_inserted = insert_q6k_final_store_pre_barrier", executor)
        self.assertIn('\\"q6_probe_effective_replay\\":%s', executor)
        self.assertIn("Q6_STORAGE8_VAR_ID = 346", executor)
        self.assertIn("Q6_STORAGE16_VAR_ID = 371", executor)
        self.assertIn("OP_U_CONVERT", executor)
        self.assertIn("OP_SHIFT_LEFT_LOGICAL", executor)
        self.assertIn("OP_SHIFT_RIGHT_LOGICAL", executor)
        self.assertIn("OP_COMPOSITE_CONSTRUCT", executor)
        self.assertLess(
            executor.index("lower_q6k_storage16_loads_to_storage8"),
            executor.index("lower_q6k_u32_to_u8vec4_bitcasts"),
        )
        self.assertLess(
            executor.index("lower_q6k_u32_to_u8vec4_bitcasts"),
            executor.index("rewrite_duplicate_descriptor_bindings"),
        )
        self.assertLess(
            executor.index("q6_storage16_loads_lowered = lower_q6k_storage16_loads_to_storage8"),
            executor.index("q6_u32_to_u8vec4_bitcasts_lowered = lower_q6k_u32_to_u8vec4_bitcasts"),
        )
        self.assertLess(
            executor.index("q6_u32_to_u8vec4_bitcasts_lowered = lower_q6k_u32_to_u8vec4_bitcasts"),
            executor.index("if (strict_duplicate_descriptor_normalization)"),
        )
        q6_rewrite_block = re.search(
            r"if \(q6_compat_rewrites_enabled\) \{(?P<body>.*?)\n    \}",
            executor,
            re.S,
        ).group("body")
        self.assertIn("lower_q6k_storage16_loads_to_storage8", q6_rewrite_block)
        self.assertIn("lower_q6k_u32_to_u8vec4_bitcasts", q6_rewrite_block)
        self.assertIn("insert_q6k_final_store_pre_barrier", q6_rewrite_block)
        self.assertIn("q6_native_callsite_detected", executor)
        self.assertNotIn("if (q6_native_callsite_detected) {", executor)
        self.assertIn("load + load_wc <= words", executor)
        self.assertIn("q6_accum_mask", compare)
        self.assertIn("q6_base_work_group_y", compare)
        self.assertIn("q6_output_base_index", compare)
        self.assertIn("q6_weight_base_blocks", compare)
        self.assertIn("q6_shader_like_64_abs_delta", compare)
        self.assertIn("q6_native_reduction_tree_abs_delta", compare)
        self.assertIn("q6_native_reduction_tree_gpu_abs_error", compare)
        self.assertIn("q6_output_layout_probe", compare)
        self.assertIn("q6_native_spirv_identity", compare)
        self.assertIn("native-q6-output-layout", compare)
        self.assertIn("native-q6-output-layout-inconclusive", compare)
        self.assertIn("native-q6-reduction-or-device-execution", compare)
        self.assertLess(
            compare.index('else "q6-store-index-model-incomplete"'),
            compare.index('else "native-q6-final-store-or-readback"'),
        )
        self.assertIn("q6_writable_writeback_mismatches", compare)
        self.assertIn("f32_after_writeback", compare)
        self.assertIn("f32_sample_values", compare)
        self.assertIn("q6_oracle_sample_indices", compare)
        self.assertIn("q6_row_indexed_samples_match_oracle", compare)
        self.assertIn("row_window/q6_first_mismatch dst indices", compare)
        self.assertIn("q6_writeback_verified_all", compare)
        self.assertIn("vulkan-device-execution\"", compare)
        self.assertIn("writeback_verified", GPU_EXECUTOR.read_text())
        self.assertIn("writeback_mismatch", GPU_EXECUTOR.read_text())
        forward_envs = set(load_llama_gpu_artifact_verifier().LLAMA_GPU_COMPARE_FORWARD_ENV_KEYS)
        for key in [
            "PDOCKER_GPU_DISABLE_PIPELINE_OPTIMIZATION",
            "PDOCKER_GPU_MUTABLE_BUFFER_CACHE_MAX_BYTES",
            "PDOCKER_GPU_DISABLE_OVERLAP_ALIASING",
            "PDOCKER_GPU_CPU_ORACLE",
            "PDOCKER_GPU_RESIDENT_CACHE_MIN_BYTES",
            "PDOCKER_GPU_SKIP_UNUSED_DESCRIPTOR_TRANSFERS",
            "PDOCKER_GPU_USE_SPIRV_DESCRIPTOR_ACCESS",
            "PDOCKER_GPU_WRITEONLY_BUFFER_CACHE",
            "PDOCKER_GPU_WRITEONLY_DIRTY_PROBE",
            "PDOCKER_GPU_WRITEONLY_DIRTY_PROBE_MIN_BYTES",
            "PDOCKER_GPU_WRITEONLY_DIRTY_WRITEBACK",
            "PDOCKER_GPU_UNSAFE_DIRTY_WRITEBACK_CACHE",
            "PDOCKER_GPU_DISPATCH_PROFILE_RESPONSE",
            "PDOCKER_GPU_MUTABLE_BUFFER_CACHE",
            "PDOCKER_GPU_VIRTUAL_MEMORY",
            "PDOCKER_GPU_VIRTUAL_MEMORY_MIN_BYTES",
            "PDOCKER_GPU_CHAIN_COMPAT_FEATURE_STRUCTS",
            "PDOCKER_GPU_RETRY_MATERIALIZE_SPECIALIZATION",
            "PDOCKER_GPU_LEGALIZE_WORKGROUP_SIZE_FROM_SPEC",
            "PDOCKER_GPU_STRICT_DEVICE_LOCAL_STAGING",
            "PDOCKER_VULKAN_HEAP_BYTES",
            "PDOCKER_VULKAN_ICD_DEBUG",
            "PDOCKER_VULKAN_SUBGROUP_SIZE",
        ]:
            self.assertIn(key, forward_envs)
        for key in [
            "PDOCKER_GPU_DISABLE_ANDROID_VULKAN",
            "PDOCKER_GPU_DISABLE_ANDROID_OPENCL",
            "PDOCKER_ANDROID_OPENCL_LIBRARY",
        ]:
            self.assertNotIn(key, forward_envs)
        self.assertIn("compare_forward_env_keys", compare)
        self.assertIn("value = os.environ.get(key)", compare)
        self.assertIn("PDOCKER_LLAMA_MIN_FREE_MB", compare)
        self.assertIn("PDOCKER_LLAMA_MIN_SWAP_FREE_MB", compare)
        self.assertIn("ensure_memory_headroom", compare)
        self.assertIn("insufficient memory before", compare)
        self.assertIn("toybox nc -U -W 3 pdocker/pdockerd.sock", compare)
        source = GPU_EXECUTOR.read_text()
        for marker in [
            "apply_vulkan_feature_policy",
            "effective_vulkan_runtime_for_dispatch",
            "append_vulkan_device_extension",
            "VK_KHR_8BIT_STORAGE_EXTENSION_NAME",
            "VK_KHR_SHADER_FLOAT16_INT8_EXTENSION_NAME",
            "PDOCKER_VULKAN_DISABLE_8BIT_STORAGE",
            "PDOCKER_VULKAN_DISABLE_16BIT_STORAGE",
            "PDOCKER_VULKAN_DISABLE_SUBGROUP_ARITHMETIC",
        ]:
            self.assertIn(marker, source)
        icd = VULKAN_ICD.read_text()
        self.assertIn('#define PDOCKER_VULKAN_ICD_BUILD_MARKER "vulkan-icd-feature-chain-marker-20260518"', icd)
        self.assertIn("trace_icd_runtime_marker_once", icd)
        self.assertIn('trace_icd_runtime_marker_once("create-instance");', icd)
        self.assertIn('trace_icd_runtime_marker_once("create-device");', icd)
        self.assertIn("rc=0", icd)
        self.assertIn("requested_feature_mask_from_device_create_info", icd)
        self.assertIn("feature_mask_from_pnext_chain", icd)
        self.assertIn("VkDeviceCreateInfo::pNext and hang the actual 1.1/1.2/extension feature", icd)
        self.assertIn("mask |= feature_mask_from_pnext_chain(pCreateInfo->pNext);", icd)
        self.assertIn("pipeline->requested_feature_mask", icd)
        self.assertIn("requested_feature_mask=%llu", icd)
        for extension in [
            "VK_KHR_8BIT_STORAGE_EXTENSION_NAME",
            "VK_KHR_SHADER_FLOAT16_INT8_EXTENSION_NAME",
            "VK_KHR_STORAGE_BUFFER_STORAGE_CLASS_EXTENSION_NAME",
            "ADD_DEVICE_EXTENSION(VK_KHR_8BIT_STORAGE_EXTENSION_NAME",
            "ADD_DEVICE_EXTENSION(VK_KHR_SHADER_FLOAT16_INT8_EXTENSION_NAME",
            "ADD_DEVICE_EXTENSION(VK_KHR_STORAGE_BUFFER_STORAGE_CLASS_EXTENSION_NAME",
        ]:
            self.assertIn(extension, icd)
        abi = APP_HEADER.read_text()
        for manifest_entry in [
            "X(PDOCKER_GPU_REWRITE_DUPLICATE_DESCRIPTOR_BINDINGS, rewrite_duplicate_descriptors, has_rewrite_duplicate_descriptors, rewrite_duplicate_descriptors, 1)",
            "X(PDOCKER_GPU_MUTABLE_BUFFER_CACHE, mutable_cache, has_mutable_buffer_cache, mutable_buffer_cache, 1)",
            "X(PDOCKER_GPU_Q4K_SAFE_KERNEL, q4k_safe_kernel, has_q4k_safe_kernel, q4k_safe_kernel, 0)",
            "X(PDOCKER_GPU_Q4K_TARGETED_SPECIALIZATION, q4k_targeted_specialization, has_q4k_targeted_specialization, q4k_targeted_specialization, 0)",
            "X(PDOCKER_GPU_Q6K_COMPAT_REWRITES, q6k_compat_rewrites, has_q6k_compat_rewrites, q6k_compat_rewrites, 0)",
            "X(PDOCKER_GPU_Q6K_READONLY_OVERLAP_SNAPSHOT, q6k_readonly_overlap_snapshot, has_q6k_readonly_overlap_snapshot, q6k_readonly_overlap_snapshot, 0)",
            "X(PDOCKER_GPU_STRICT_PASSTHROUGH, strict_passthrough, has_strict_passthrough, strict_passthrough, 0)",
            "X(PDOCKER_GPU_STRICT_RECONCILIATION, strict_reconciliation, has_strict_reconciliation, strict_reconciliation, 0)",
            "X(PDOCKER_GPU_STRICT_DEVICE_LOCAL_STAGING, strict_device_local_staging, has_strict_device_local_staging, strict_device_local_staging, 0)",
            "X(PDOCKER_GPU_STRICT_DUPLICATE_DESCRIPTOR_NORMALIZATION, strict_duplicate_descriptor_normalization, has_strict_duplicate_descriptor_normalization, strict_duplicate_descriptor_normalization, 0)",
            "X(PDOCKER_GPU_MATERIALIZE_SPIRV_SPECIALIZATION_CONSTANTS, materialize_specialization, has_materialize_specialization_constants, materialize_specialization_constants, 1)",
            "X(PDOCKER_GPU_LEGALIZE_WORKGROUP_SIZE_FROM_SPEC, legalize_workgroup_size_from_spec, has_legalize_workgroup_size_from_spec, legalize_workgroup_size_from_spec, 1)",
            "X(PDOCKER_GPU_DISABLE_PIPELINE_OPTIMIZATION, disable_pipeline_optimization, has_disable_pipeline_optimization, disable_pipeline_optimization, 1)",
            "X(PDOCKER_GPU_SKIP_UNUSED_DESCRIPTOR_TRANSFERS, skip_unused_descriptor_transfers, has_skip_unused_descriptor_transfers, skip_unused_descriptor_transfers, 1)",
            "X(PDOCKER_GPU_USE_SPIRV_DESCRIPTOR_ACCESS, use_spirv_descriptor_access, has_use_spirv_descriptor_access, use_spirv_descriptor_access, 1)",
            "X(PDOCKER_GPU_DISABLE_OVERLAP_ALIASING, disable_overlap_aliasing, has_disable_overlap_aliasing, disable_overlap_aliasing, 0)",
            "X(PDOCKER_GPU_CPU_ORACLE, cpu_oracle, has_cpu_oracle, cpu_oracle, 0)",
            "X(PDOCKER_VULKAN_DISABLE_8BIT_STORAGE, disable_storage8, disable_storage8, 0)",
            "X(PDOCKER_VULKAN_DISABLE_16BIT_STORAGE, disable_storage16, disable_storage16, 0)",
            "X(PDOCKER_VULKAN_DISABLE_SUBGROUP_ARITHMETIC, disable_subgroup_arithmetic, disable_subgroup_arithmetic, 0)",
        ]:
            self.assertIn(manifest_entry, abi)
        self.assertIn("PDOCKER_GPU_VULKAN_BOOL_DISPATCH_OPTIONS(PDOCKER_VK_BOOL_BRIDGE_OPTION)", icd)
        self.assertIn("PDOCKER_GPU_VULKAN_BOOL_DISPATCH_OPTIONS_NO_HAS(PDOCKER_VK_BOOL_BRIDGE_OPTION_NO_HAS)", icd)
        self.assertIn("api_offsets[binding_count]", icd)
        self.assertIn("api_descriptor_types[binding_count]", icd)
        self.assertIn("PDOCKER_GPU_DISPATCH_PROFILE_LOG", icd)
        self.assertIn("const size_t max_response = 1024 * 1024", icd)
        self.assertIn("char stack_line[16384]", icd)
        self.assertIn("char *heap_line = NULL", icd)
        self.assertIn("memcpy(next, line, line_off)", icd)
        self.assertIn("free(heap_line)", icd)
        self.assertIn("per-call/per-thread state, never shared globally", icd)
        self.assertIn("growth is geometric and capped", icd)
        self.assertIn("if (next_cap < line_cap)", icd)
        self.assertIn("rc = -EOVERFLOW", icd)
        self.assertIn("advertised_subgroup_size", icd)
        self.assertIn("parsed_env_subgroup_size", icd)
        self.assertIn("PDOCKER_VULKAN_SUBGROUP_SIZE", icd)
        self.assertIn("narrowing override only", icd)
        self.assertIn("never use it to widen", icd)
        self.assertIn("advertised_subgroup_stages", icd)
        self.assertIn("supportedStages = advertised_subgroup_stages()", icd)
        self.assertIn("subgroupSupportedStages = advertised_subgroup_stages()", icd)
        self.assertIn("subgroupSize = advertised_subgroup_size()", icd)

    def test_llama_gpu_compare_classifies_executor_feature_mismatches(self):
        compare = LLAMA_COMPARE.read_text()
        for marker in [
            "executor_feature_mismatches = sorted({",
            'event.get("spirv_feature_mismatch") is True',
            '"executor_spirv_feature_mismatch": bool(executor_feature_mismatches)',
            '"executor_spirv_feature_mismatches": executor_feature_mismatches',
            'blocker_class = "vulkan_feature_mismatch"',
            "generic SPIR-V dispatch ran while executor feature policy reports missing features",
            "clamp or translate llama.cpp storage8/int8 final-projection shaders before accepting performance results",
        ]:
            self.assertIn(marker, compare)

    def test_gpu_executor_has_opt_in_writeonly_buffer_cache(self):
        source = GPU_EXECUTOR.read_text()
        for marker in [
            "PDOCKER_GPU_WRITEONLY_BUFFER_CACHE",
            "writeonly_buffer_cache_enabled",
            "if (!initialize_from_fd)",
            "find_writeonly_scratch_cache_entry",
            "entry->scratch = 1;",
            "entry->valid && !entry->scratch",
            "*mutable_cache_hit = 1;",
        ]:
            self.assertIn(marker, source)

    def test_gpu_executor_has_opt_in_writeonly_dirty_probe(self):
        source = GPU_EXECUTOR.read_text()
        for marker in [
            "PDOCKER_GPU_WRITEONLY_DIRTY_PROBE",
            "PDOCKER_GPU_WRITEONLY_DIRTY_PROBE_MIN_BYTES",
            "PDOCKER_GPU_WRITEONLY_DIRTY_WRITEBACK",
            "PDOCKER_GPU_UNSAFE_DIRTY_WRITEBACK_CACHE",
            "PDOCKER_GPU_WRITEONLY_DIRTY_PROBE_SENTINEL",
            "writeonly_dirty_probe_enabled",
            "writeonly_dirty_writeback_enabled",
            "Dirty writeback is a performance optimization, not a correctness",
            "has_writeonly_buffer_cache",
            "has_mutable_buffer_cache_max_bytes",
            "mutable_buffer_cache_candidate_with_max",
            "VulkanDirtyMaskCacheEntry",
            "find_dirty_mask_cache_entry",
            "update_dirty_mask_cache",
            "write_dirty_pages_exact",
            "count_dirty_probe_pages",
            "parse_vulkan_dispatch_option",
            "binding_dirty_probe_pages",
            "!binding_read_needed[i]",
        ]:
            self.assertIn(marker, source)
        icd = VULKAN_ICD.read_text()
        abi = APP_HEADER.read_text()
        for marker in [
            "PdockerVkBoolBridgeOption",
            "bool_bridge_options[]",
            "PDOCKER_GPU_VULKAN_BOOL_DISPATCH_OPTIONS(PDOCKER_VK_BOOL_BRIDGE_OPTION)",
            "PDOCKER_GPU_VULKAN_SIZE_DISPATCH_OPTIONS(PDOCKER_VK_U64_BRIDGE_OPTION)",
            "profile=1",
            "PDOCKER_GPU_DISPATCH_PROFILE_RESPONSE",
            "PDOCKER_GPU_DISPATCH_PROFILE_LOG",
        ]:
            self.assertIn(marker, icd)
        for manifest_entry in [
            "X(PDOCKER_GPU_WRITEONLY_DIRTY_PROBE, dirty_probe, has_dirty_probe, dirty_probe, 0)",
            "X(PDOCKER_GPU_WRITEONLY_DIRTY_WRITEBACK, dirty_writeback, has_dirty_writeback, dirty_writeback, 0)",
            "X(PDOCKER_GPU_WRITEONLY_BUFFER_CACHE, writeonly_cache, has_writeonly_buffer_cache, writeonly_buffer_cache, 0)",
            "X(PDOCKER_GPU_MUTABLE_BUFFER_CACHE_MAX_BYTES, mutable_cache_max, has_mutable_buffer_cache_max_bytes, mutable_buffer_cache_max_bytes)",
            "X(PDOCKER_GPU_WRITEONLY_DIRTY_PROBE_MIN_BYTES, dirty_probe_min, has_dirty_probe_min_bytes, dirty_probe_min_bytes)",
        ]:
            self.assertIn(manifest_entry, abi)
        compare = LLAMA_COMPARE.read_text()
        manifest_text = LLAMA_GPU_ENV_MANIFEST.read_text()
        self.assertIn('"env": "PDOCKER_GPU_DISPATCH_PROFILE_RESPONSE"', manifest_text)
        self.assertIn('"env": "PDOCKER_GPU_DISPATCH_PROFILE_LOG"', manifest_text)

    def test_llama_gpu_compare_forwards_native_tuning_envs_by_default(self):
        compare = LLAMA_COMPARE.read_text()
        verifier = load_llama_gpu_artifact_verifier()
        forward_envs = set(verifier.LLAMA_GPU_COMPARE_FORWARD_ENV_KEYS)
        manifest = json.loads(LLAMA_GPU_ENV_MANIFEST.read_text())
        profile_envs = {
            item["env"]
            for profile in manifest.get("compare_mode_env_profiles", {}).values()
            for group in ("env", "trace_alloc_env")
            for item in profile.get(group, [])
            if isinstance(item, dict) and isinstance(item.get("env"), str)
        }
        native_sources = GPU_EXECUTOR.read_text() + "\n" + VULKAN_ICD.read_text()
        native_envs = set(re.findall(
            r'getenv\("((?:PDOCKER_GPU|PDOCKER_VULKAN|PDOCKER_ANDROID)_[A-Z0-9_]+)"\)',
            native_sources,
        ))
        native_envs |= set(re.findall(
            r'env_truthy(?:_default)?\("((?:PDOCKER_GPU|PDOCKER_VULKAN|PDOCKER_ANDROID)_[A-Z0-9_]+)"',
            native_sources,
        ))
        internal_only = {
            "PDOCKER_GPU_QUEUE_SOCKET",
            "PDOCKER_GPU_SHARED_DIR",
            "PDOCKER_VULKAN_GRAPHICS_V6_VALIDATE_PRODUCER",
        }
        app_process_only = set(
            manifest.get("env_bridge_classifications", {}).get("app_process_only", [])
        )
        missing = sorted(
            key for key in native_envs - internal_only - app_process_only
            if key not in forward_envs and key not in profile_envs and f"{key}=" not in compare
        )
        self.assertEqual([], missing)
        self.assertFalse(app_process_only & forward_envs)

        self.assertIn("descriptor_array_layout_seen", compare)
        self.assertIn("descriptor_array_layouts", compare)
        self.assertIn("ngl_zero_generic_spirv_dispatch", compare)
        self.assertIn("vulkan_backend_control_mismatch", compare)
        self.assertIn("n-gpu-layers as an insufficient isolation knob", compare)
        self.assertIn("api_understanding", compare)
        self.assertIn("effective_offset_mismatch_count", compare)
        self.assertIn(("PDOCKER_GPU_CPU_ORACLE", "cpu_oracle_requested"), verifier.LLAMA_GPU_CONFIG_PROPAGATION_ENV_FIELDS)
        self.assertIn("PDOCKER_LLAMA_BENCH_WARMUP_DISCARD", compare)
        self.assertIn("gpu_summary_scope", compare)
        for field in [
            "dirty_probe_binding_samples",
            "dirty_probe_max_bytes",
            "dirty_probe_total_bytes",
            "dirty_probe_ms_max",
            "dirty_writeback_cached_samples",
            "dirty_writeback_total_bytes",
            "top_dirty_probe_bindings",
        ]:
            self.assertIn(field, compare)

    def test_q6_workgroup_runner_fixes_required_env_and_preflight(self):
        runner = (ROOT / "scripts" / "android-llama-gpu-q6-workgroup-run.sh").read_text()
        self.assertIn("verify-q6-workgroup-lowering-preflight.py", runner)
        self.assertIn("android-llama-gpu-readiness.sh", runner)
        self.assertNotIn("PDOCKER_GPU_STRICT_PASSTHROUGH=1", runner)
        self.assertNotIn("PDOCKER_GPU_STRICT_RECONCILIATION=1", runner)
        self.assertNotIn("PDOCKER_GPU_CPU_ORACLE=1", runner)
        self.assertIn("android-llama-gpu-compare.sh", runner)
        self.assertIn("--require-q6-workgroup-clear", runner)
        self.assertIn("does not rebuild the llama image", runner)
        self.assertIn("plan-llama-gpu-q6-run.py", runner)
        self.assertIn("verify-llama-gpu-q6-run-against-plan.py", runner)
        self.assertIn("--allow-nonterminal", runner)
        self.assertIn("VERIFY_RC=$?", runner)
        self.assertIn("VERDICT_RC=$?", runner)
        self.assertIn("verdict was still written", runner)
        self.assertIn("--dry-run", runner)
        self.assertIn("dry-run complete; no ADB", runner)
        self.assertIn("refreshing default probe bundle", runner)
        self.assertIn("--probe-source-spv", runner)
        self.assertIn("--probe-source-hash", runner)
        self.assertIn("--probe-locator", runner)
        self.assertIn("pdocker.q6k.source-spirv-dump-locator.v1", runner)
        self.assertIn("CURRENT_PROBE_HASH", runner)
        self.assertIn("PDOCKER_Q6K_ALLOW_ARCHIVED_PROBE_SOURCE", runner)
        self.assertIn("refusing to refresh the default probe bundle from the archived fixture", runner)
        self.assertIn("probe env hash mismatch", runner)
        self.assertIn("scripts/prepare-q6k-noop-probe.sh", runner)
        self.assertIn("--expected-hash", runner)
        self.assertIn("--probe-writes", runner)
        self.assertIn("instrument-spirv-noop-probe.py", runner)
        self.assertIn("llama-gpu-env-manifest.json", runner)
        self.assertIn("q6_required_env_overlay", runner)
        manifest = json.loads(LLAMA_GPU_ENV_MANIFEST.read_text(encoding="utf-8"))
        manifest_overlay = manifest["q6_required_env_overlay"]

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            plan = tmp_path / "plan.json"
            artifact = tmp_path / "artifact.json"
            result = subprocess.run(
                [
                    "bash",
                    str(ROOT / "scripts" / "android-llama-gpu-q6-workgroup-run.sh"),
                    "--dry-run",
                    "--plan-out",
                    str(plan),
                    "--out",
                    str(artifact),
                    "--cpu-tps",
                    "0.125",
                    "--cpu-ctx",
                    "256",
                    "--gpu-ctx",
                    "768",
                ],
                check=True,
                cwd=ROOT,
                text=True,
                capture_output=True,
            )
            self.assertIn("dry-run complete; no ADB", result.stdout)
            self.assertTrue(plan.exists())
            self.assertFalse(artifact.exists())
            plan_data = json.loads(plan.read_text(encoding="utf-8"))
            self.assertEqual("adb-not-used", plan_data["inputs"]["serial"])
            self.assertEqual(str(artifact), plan_data["artifact_path"])
            self.assertEqual("0.125", plan_data["inputs"]["cpu_tps"])
            self.assertEqual(256, plan_data["inputs"]["cpu_ctx"])
            self.assertEqual(768, plan_data["inputs"]["gpu_ctx"])
            compare_step = next(step for step in plan_data["runner_step_contract"] if step["name"] == "compare")
            self.assertIn("--cpu-ctx", compare_step["required_flags"])
            self.assertIn("--gpu-ctx", compare_step["required_flags"])
            self.assertEqual(plan_data["q6_required_env_overlay"], compare_step["required_env_overlay"])
            self.assertEqual(manifest_overlay, plan_data["q6_required_env_overlay"])
            self.assertEqual(manifest_overlay, compare_step["required_env_overlay"])

    def test_q6_probe_prepare_and_compare_fail_closed_for_stale_hashes(self):
        runner = (ROOT / "scripts" / "android-llama-gpu-q6-workgroup-run.sh").read_text()
        prepare = (ROOT / "scripts" / "prepare-q6k-noop-probe.sh").read_text()
        compare = LLAMA_COMPARE.read_text()

        self.assertIn("--expected-hash", prepare)
        self.assertIn("Q6 probe source hash mismatch", prepare)
        self.assertIn("expected_source_spirv_hash", prepare)
        self.assertIn("--probe-source-spv", runner)
        self.assertIn("--probe-source-hash", runner)
        self.assertIn("--probe-locator", runner)
        self.assertIn("pdocker.q6k.source-spirv-dump-locator.v1", runner)
        self.assertIn("CURRENT_PROBE_HASH", runner)
        self.assertIn("PDOCKER_Q6K_ALLOW_ARCHIVED_PROBE_SOURCE", runner)
        self.assertIn("refusing to refresh the default probe bundle from the archived fixture", runner)
        self.assertIn("--expected-hash", runner)

        self.assertIn("stale-target-hash", compare)
        self.assertIn("actual_q6_source_hashes", compare)
        self.assertIn("skipped_non_target_actual_hashes", compare)
        self.assertIn("stale_target_hash", compare)
        self.assertIn("def q6_probe_identity_hashes()", compare)
        self.assertIn("event_source == expected_effective", compare)
        self.assertIn("target_or_probe_hashes", compare)
        self.assertIn("not any(value in actual_q6_or_skipped_hashes for value in target_or_probe_hashes)", compare)
        self.assertNotIn("expected in Q6_K_MATVEC_SPIRV_HASHES and effective", compare)
        self.assertIn('cpu_oracle.get("kernel_hint") == "mul-mat-vec-q6-k-large"', compare)

    def test_q6_preflight_planner_names_evidence_and_branches_before_adb(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "plan.json"
            subprocess.run(
                [
                    "python3",
                    str(LLAMA_Q6_PREFLIGHT_PLANNER),
                    "--serial",
                    "192.0.2.10:44444",
                    "--out",
                    str(out),
                ],
                check=True,
                cwd=ROOT,
            )
            plan = json.loads(out.read_text(encoding="utf-8"))
        manifest = json.loads(LLAMA_GPU_ENV_MANIFEST.read_text(encoding="utf-8"))
        manifest_overlay = manifest["q6_required_env_overlay"]

        self.assertEqual("pdocker.llama.gpu.q6.preflight-plan.v1", plan["schema"])
        self.assertEqual(
            "do not connect until the user says the Android device is prepared",
            plan["adb_policy"],
        )
        self.assertIn("docs/design/VULKAN_BRIDGE_PROBE_MATRIX.md", plan["probe_matrix"])
        self.assertIn("scripts/android-llama-gpu-q6-workgroup-run.sh", plan["runner"])
        self.assertIn("specialization_materialize_report", plan["required_evidence_fields"])
        self.assertIn("reconciliation", plan["required_evidence_fields"])
        self.assertIn("q6_final_store_boundary", plan["required_evidence_fields"])
        self.assertIn("q6_workgroup_specialization_interpretation", plan["required_evidence_fields"])
        self.assertIn("q6_local_size", plan["required_evidence_fields"])
        self.assertIn("q6_num_rows", plan["required_evidence_fields"])
        self.assertIn("q6_num_cols", plan["required_evidence_fields"])
        self.assertIn("q6_debug_binding_alias_safety", plan["required_evidence_fields"])
        self.assertIn("debug_probe_binding", plan["required_evidence_fields"])
        self.assertIn("descriptor_alias_map", plan["required_evidence_fields"])
        self.assertIn("binding_descriptor_offset", plan["required_evidence_fields"])
        self.assertIn("api_range", plan["required_evidence_fields"])
        self.assertIn("final_store_value_f32", plan["required_evidence_fields"])
        self.assertIn("readonly_overlap_snapshot_policy", plan["required_evidence_fields"])
        self.assertIn("readonly_overlap_snapshots", plan["required_evidence_fields"])
        self.assertIn("readonly_overlap_snapshot", plan["required_evidence_fields"])
        self.assertIn("readonly_overlap_source_index", plan["required_evidence_fields"])
        self.assertIn("writeback", " ".join(branch["condition"] for branch in plan["fail_branches"]))
        self.assertIn("q6_final_store_boundary.summary", json.dumps(plan["fail_branches"]))
        self.assertIn("q6_debug_binding_alias_safety.summary", json.dumps(plan["fail_branches"]))
        self.assertIn("unsupported-spec-expression", json.dumps(plan["fail_branches"]))
        self.assertFalse(plan["inputs"]["llama_cpp_may_change"])
        self.assertFalse(plan["inputs"]["dockerfile_may_change"])
        step_names = [step["name"] for step in plan["runner_step_contract"]]
        self.assertEqual(
            ["plan", "spv-preflight", "device-readiness", "compare", "artifact-verifier", "plan-verdict"],
            step_names,
        )
        compare_step = next(step for step in plan["runner_step_contract"] if step["name"] == "compare")
        self.assertTrue(compare_step["touches_adb"])
        self.assertEqual(manifest_overlay, plan["q6_required_env_overlay"])
        self.assertEqual(manifest_overlay, compare_step["required_env_overlay"])
        self.assertEqual("1", compare_step["required_env_overlay"]["PDOCKER_GPU_STRICT_PASSTHROUGH"])
        self.assertEqual("1", compare_step["required_env_overlay"]["PDOCKER_GPU_STRICT_RECONCILIATION"])
        self.assertEqual(
            "1",
            compare_step["required_env_overlay"]["PDOCKER_GPU_STRICT_DUPLICATE_DESCRIPTOR_NORMALIZATION"],
        )
        self.assertEqual(plan["q6_required_env_overlay"], compare_step["required_env_overlay"])
        self.assertIn("--require-q6-workgroup-clear", json.dumps(plan["runner_step_contract"]))
        self.assertIn("required_evidence_alternatives", plan)
        self.assertIn("q6-final-store-or-native-split-boundary", json.dumps(plan["required_evidence_alternatives"]))
        self.assertIn("descriptor-usage-or-binding-details", json.dumps(plan["required_evidence_alternatives"]))

    def test_q6_plan_verifier_accepts_native_split_evidence_alternative(self):
        verifier = load_llama_q6_plan_verifier()
        plan = {
            "required_evidence_fields": [
                "descriptor_usage",
                "final_store_value_f32",
                "final_store_matches_expected",
                "writeback_matches_final_store",
            ],
            "required_evidence_alternatives": [
                {
                    "name": "descriptor-usage-or-binding-details",
                    "covers": ["descriptor_usage"],
                    "any_of": [
                        ["descriptor_usage"],
                        ["binding_details", "binding_descriptor_offset", "api_range"],
                    ],
                },
                {
                    "name": "q6-final-store-or-native-split-boundary",
                    "covers": [
                        "final_store_value_f32",
                        "final_store_matches_expected",
                        "writeback_matches_final_store",
                    ],
                    "any_of": [
                        [
                            "final_store_value_f32",
                            "final_store_matches_expected",
                            "writeback_matches_final_store",
                        ],
                        [
                            "q6_native_vs_writeback_split",
                            "native_gpu_at_dst",
                            "native_matches_expected",
                            "writeback_matches_expected",
                            "writeback_matches_native",
                        ],
                    ],
                },
            ],
        }
        artifact = {
            "gpu": {
                "diagnostics": {
                    "generic_spirv_dispatch": [
                        {
                            "binding_details": [
                                {
                                    "binding_descriptor_offset": 0,
                                    "api_range": 65536,
                                }
                            ]
                        }
                    ],
                    "q6_workgroup_diagnostics": {
                        "q6_native_vs_writeback_split": {
                            "summary": "native-final-store-or-readback",
                            "samples": [
                                {
                                    "native_gpu_at_dst": 5.0,
                                    "native_matches_expected": False,
                                    "writeback_matches_expected": False,
                                    "writeback_matches_native": True,
                                }
                            ],
                        }
                    },
                }
            }
        }
        self.assertEqual([], verifier.missing_evidence_fields(artifact, plan))
        artifact["gpu"]["diagnostics"]["q6_workgroup_diagnostics"]["q6_native_vs_writeback_split"]["samples"][0].pop(
            "native_gpu_at_dst"
        )
        self.assertIn("final_store_value_f32", verifier.missing_evidence_fields(artifact, plan))

    def test_q6_plan_verifier_selects_next_action_from_collected_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            plan = tmp_path / "plan.json"
            artifact = tmp_path / "artifact.json"
            verdict = tmp_path / "verdict.json"
            subprocess.run(
                [
                    "python3",
                    str(LLAMA_Q6_PREFLIGHT_PLANNER),
                    "--artifact",
                    str(artifact),
                    "--out",
                    str(plan),
                ],
                check=True,
                cwd=ROOT,
            )
            artifact.write_text(
                json.dumps(
                    {
                        "schema": "pdocker.llama.gpu.compare.v1",
                        "runtime_freshness": llama_runtime_freshness_pass(),
                        "gpu": {
                            "runtime_env_manifest": q6_required_runtime_env_manifest(plan),
                            "diagnostics": {
                                "runtime_freshness": {
                                    "observed_executor_markers": ["gpu-executor-q6-readonly-snapshot-20260531"],
                                    "expected_executor_marker": "gpu-executor-q6-readonly-snapshot-20260531",
                                    "observed_icd_markers": ["vulkan-icd-feature-chain-marker-20260518"],
                                    "expected_icd_marker": "vulkan-icd-feature-chain-marker-20260518",
                                },
                                "q6_workgroup_diagnostics": {
                                    "event_count": 1,
                                    "latest_status": "mismatch",
                                    "local_size": [32, 1, 1],
                                    "local_size_resolved": [32, 1, 1],
                                    "local_size_consistent": True,
                                    "q6_local_size": [32, 1, 1],
                                    "q6_local_invocations": 32,
                                    "q6_workgroup_specialization_interpretation": {
                                        "spec_id_0": "WorkgroupSize.x / Q6 lane count",
                                        "spec_id_1": "Q6 row-count/data-loop dimension; not WorkgroupSize.y",
                                        "spec_id_2": "Q6 column/count dimension; not WorkgroupSize.z",
                                        "do_not_patch_local_size_y_from_spec_id_1": True,
                                        "do_not_patch_local_size_z_from_spec_id_2": True,
                                    },
                                    "q6_writeback_verified_all": True,
                                    "q6_row_indexed_sample_indices": [0],
                                    "q6_row_indexed_writeback_verified": True,
                                    "q6_row_indexed_writeback_evidence": [
                                        {
                                            "index": 0,
                                            "binding": 2,
                                            "q6_row_indexed": True,
                                            "q6_sample_indices": [0],
                                            "row_indexed_samples_match_oracle": True,
                                            "f32_after_dispatch": [{"index": 0, "value": 1.0}],
                                            "f32_after_writeback": [{"index": 0, "value": 1.0}],
                                        }
                                    ],
                                    "q6_writable_bindings": [
                                        {
                                            "index": 0,
                                            "binding": 2,
                                            "writable": True,
                                            "gpu_after_dispatch_hash": "0x1111111111111111",
                                            "fd_after_hash": "0x1111111111111111",
                                            "writeback_verified": True,
                                            "writeback_mismatch": False,
                                            "offset_equals_memory_plus_api_offset": True,
                                            "gpu_offset_equals_memory_plus_api_offset": True,
                                            "descriptor_offset_equals_api_offset": True,
                                            "descriptor_range_matches_api_range": True,
                                        }
                                    ],
                                    "q6_dispatch_groups": [1, 1, 1],
                                    "q6_block_size": 32,
                                    "q6_num_rows": 1,
                                    "q6_num_cols": 1,
                                    "q6_store_index_model_valid": True,
                                    "q6_store_index_sampled_nonzero_j": True,
                                    "q6_store_index_sampled_nonzero_y": True,
                                    "q6_store_index_full_coverage": True,
                                    "q6_store_window_begin": 0,
                                    "q6_store_window_end": 1,
                                    "q6_output_layout_probe": {
                                        "summary": "canonical-mismatch-inconclusive",
                                        "samples": [
                                            {
                                                "dst_index": 0,
                                                "expected_store_index": 0,
                                                "store_formula_valid": True,
                                                "store_j": 0,
                                                "store_workgroup": [0, 0, 0],
                                                "store_row_in_group": 0,
                                                "store_row": 0,
                                                "expected": 1.0,
                                                "gpu_at_dst": 1.0,
                                            }
                                        ],
                                    },
                                    "q6_debug_binding_alias_safety": {
                                        "schema": "pdocker.q6k.debug-binding-alias-safety.v1",
                                        "summary": "pass",
                                        "debug_binding_count": 1,
                                        "checked_compute_binding_count": 1,
                                        "overlap_count": 0,
                                    },
                                    "q6_final_store_boundary": {
                                        "schema": "pdocker.q6k.final-store-boundary.v1",
                                        "summary": "pass",
                                        "joined_sample_count": 1,
                                        "samples": [
                                            {
                                                "output_index": 0,
                                                "expected_store_index": 0,
                                                "dst_index": 0,
                                                "final_store_value_f32": 1.0,
                                                "expected": 1.0,
                                                "fd_after_writeback": 1.0,
                                                "final_store_matches_expected": True,
                                                "writeback_matches_final_store": True,
                                                "writeback_matches_expected": True,
                                            }
                                        ],
                                    },
                                },
                                "generic_spirv_dispatch": [
                                    {
                                        "specialization_materialize_report": {
                                            "changed": False,
                                            "failure_reason": "no-changes",
                                        },
                                        "executor_build_marker": "gpu-executor-q6-readonly-snapshot-20260531",
                                        "source_spirv_hash": "0x1111111111111111",
                                        "effective_spirv_hash": "0x2222222222222222",
                                        "oracle_spirv_hash": "0x1111111111111111",
                                        "specialization_materialized": False,
                                        "local_size_patched": True,
                                        "spirv_local_size": [32, 1, 1],
                                        "spirv_local_size_resolved": [32, 1, 1],
                                        "spirv_local_size_consistent": True,
                                        "strict_object_graph": {
                                            "used": True,
                                            "readonly_overlap_snapshots": 1,
                                            "readonly_overlap_snapshot_bytes": 4096,
                                        },
                                        "readonly_overlap_snapshot_policy": {
                                            "requested": False,
                                            "q6_auto": True,
                                            "effective": True,
                                        },
                                        "reconciliation": {"summary": "pass"},
                                        "descriptor_alias_map": [
                                            {
                                                "target_id": 31,
                                                "original_binding": 0,
                                                "rewritten_binding": 6,
                                            }
                                        ],
                                        "q6_debug_binding_alias_safety": {"summary": "pass"},
                                        "binding_details": [
                                            {
                                                "binding": 5,
                                                "debug_probe_binding": True,
                                                "binding_descriptor_offset": 0,
                                                "api_range": 65536,
                                                "readonly_overlap_snapshot": True,
                                                "readonly_overlap_source_index": 2,
                                                "readonly_overlap_snapshot_bytes": 4096,
                                            }
                                        ],
                                        "descriptor_usage": {},
                                        "cpu_oracle": {"candidate": True, "executed": True, "status": "match"},
                                        "q6_row_indexed": True,
                                        "pre_barriers": 1,
                                        "post_barriers": 1,
                                        "upload_ms": 1.0,
                                        "dispatch_ms": 1.0,
                                        "download_ms": 1.0,
                                    }
                                ],
                            },
                            "correctness": {
                                "summary": {"correctness": "fail", "required_failures": 1},
                                "schema": "pdocker.llama.correctness.v1.compare",
                                "endpoint": "/completion",
                                "probes": [{"name": "addition", "passed": False}],
                            },
                        },
                        "comparison": {"speedup": 0.5, "target_tokens_per_second": 1.0, "target_met": False},
                        "bridge_overhead_phase": {
                            "cpu_tokens_per_second": 1.0,
                            "gpu_tokens_per_second": 0.5,
                            "speedup": 0.5,
                            "target_speedup": 10.0,
                            "target_met": False,
                        },
                        "config_propagation": {
                            "summary": "pass",
                            "checks": [
                                {"env": env, "executor_field": field, "status": "pass"}
                                for env, field in load_llama_gpu_artifact_verifier().LLAMA_GPU_CONFIG_PROPAGATION_ENV_FIELDS
                            ],
                        },
                    }
                ),
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    "python3",
                    str(LLAMA_Q6_PLAN_VERIFIER),
                    "--plan",
                    str(plan),
                    "--artifact",
                    str(artifact),
                    "--out",
                    str(verdict),
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(10, result.returncode, result.stdout + result.stderr)
            data = json.loads(verdict.read_text(encoding="utf-8"))
            self.assertEqual("q6-native-output-layout-inconclusive", data["classification"])
            self.assertTrue(data["artifact_matches_plan_path"])
            self.assertEqual([], data["missing_required_evidence_fields"])
            self.assertEqual([], data["required_env_mismatches"])
            self.assertEqual(
                "specialization_materialize_report.failure_reason == no-changes",
                data["selected_branch"]["condition"],
            )
            allowed = subprocess.run(
                [
                    "python3",
                    str(LLAMA_Q6_PLAN_VERIFIER),
                    "--plan",
                    str(plan),
                    "--artifact",
                    str(artifact),
                    "--allow-nonterminal",
                ],
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(0, allowed.returncode, allowed.stdout.decode() + allowed.stderr.decode())

            wrong_plan = tmp_path / "wrong-plan.json"
            subprocess.run(
                [
                    "python3",
                    str(LLAMA_Q6_PREFLIGHT_PLANNER),
                    "--artifact",
                    str(tmp_path / "different-artifact.json"),
                    "--out",
                    str(wrong_plan),
                ],
                check=True,
                cwd=ROOT,
            )
            wrong_path = subprocess.run(
                [
                    "python3",
                    str(LLAMA_Q6_PLAN_VERIFIER),
                    "--plan",
                    str(wrong_plan),
                    "--artifact",
                    str(artifact),
                    "--allow-nonterminal",
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(13, wrong_path.returncode, wrong_path.stdout + wrong_path.stderr)
            self.assertFalse(json.loads(wrong_path.stdout)["artifact_matches_plan_path"])

            artifact_data = json.loads(artifact.read_text(encoding="utf-8"))
            artifact_data["gpu"]["runtime_env_manifest"]["host_requested_env"].pop(
                "PDOCKER_GPU_MATERIALIZE_SPIRV_SPECIALIZATION_CONSTANTS"
            )
            artifact.write_text(json.dumps(artifact_data), encoding="utf-8")
            env_mismatch = subprocess.run(
                [
                    "python3",
                    str(LLAMA_Q6_PLAN_VERIFIER),
                    "--plan",
                    str(plan),
                    "--artifact",
                    str(artifact),
                    "--allow-nonterminal",
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(14, env_mismatch.returncode, env_mismatch.stdout + env_mismatch.stderr)
            self.assertEqual(
                "PDOCKER_GPU_MATERIALIZE_SPIRV_SPECIALIZATION_CONSTANTS",
                json.loads(env_mismatch.stdout)["required_env_mismatches"][0]["key"],
            )

    def test_q6_plan_verifier_fails_when_planned_evidence_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            plan = tmp_path / "plan.json"
            artifact = tmp_path / "artifact.json"
            subprocess.run(
                [
                    "python3",
                    str(LLAMA_Q6_PREFLIGHT_PLANNER),
                    "--artifact",
                    str(artifact),
                    "--out",
                    str(plan),
                ],
                check=True,
                cwd=ROOT,
            )
            artifact.write_text(
                json.dumps(
                    {
                        "schema": "pdocker.llama.gpu.compare.v1",
                        "runtime_freshness": llama_runtime_freshness_pass(),
                        "gpu": {"diagnostics": {"q6_workgroup_diagnostics": {"event_count": 0}}},
                    }
                ),
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    "python3",
                    str(LLAMA_Q6_PLAN_VERIFIER),
                    "--plan",
                    str(plan),
                    "--artifact",
                    str(artifact),
                    "--allow-nonterminal",
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(12, result.returncode, result.stdout + result.stderr)
            data = json.loads(result.stdout)
        self.assertIn("specialization_materialize_report", data["missing_required_evidence_fields"])
        self.assertTrue(data["artifact_matches_plan_path"])

    def test_q6_plan_verifier_selects_final_store_boundary_branches(self):
        verifier = load_llama_q6_plan_verifier()
        plan = {"pass_branch": {"condition": "pass", "action": "promote"}}

        native = verifier.select_branch(
            {
                "classification": "q6-native-final-store",
                "q6_workgroup_diagnostics": {
                    "q6_final_store_boundary": {"summary": "native-final-store-mismatch"}
                },
            },
            {},
            plan,
        )
        self.assertEqual(
            "q6_final_store_boundary.summary == native-final-store-mismatch",
            native["condition"],
        )
        self.assertEqual("native Q6 final-store path", native["owner"])

        writeback = verifier.select_branch(
            {
                "classification": "q6-writeback-mismatch",
                "q6_workgroup_diagnostics": {
                    "q6_final_store_boundary": {"summary": "executor-writeback-mismatch"}
                },
            },
            {},
            plan,
        )
        self.assertEqual(
            "q6_final_store_boundary.summary == executor-writeback-mismatch",
            writeback["condition"],
        )
        self.assertEqual("Vulkan writeback and binding report path", writeback["owner"])

        inconclusive = verifier.select_branch(
            {
                "classification": "q6-workgroup-cleared-but-oracle-mismatch",
                "q6_workgroup_diagnostics": {
                    "q6_final_store_boundary": {"summary": "inconclusive"}
                },
            },
            {},
            plan,
        )
        self.assertEqual(
            "q6_final_store_boundary.summary == inconclusive",
            inconclusive["condition"],
        )
        self.assertEqual("Q6 final-store boundary instrumentation", inconclusive["owner"])

        missing_debug_alias = verifier.select_branch(
            {
                "classification": "q6-debug-binding-alias-evidence-missing",
                "q6_workgroup_diagnostics": {
                    "q6_debug_binding_alias_safety": {"summary": "missing-evidence"}
                },
            },
            {},
            plan,
        )
        self.assertEqual(
            "q6_debug_binding_alias_safety.summary in {fail,missing-evidence,not-run}",
            missing_debug_alias["condition"],
        )
        self.assertEqual("Q6 descriptor/debug evidence gate", missing_debug_alias["owner"])

        wrong_output_stale_probe = verifier.select_branch(
            {"classification": "llama-completion-wrong-output"},
            {
                "gpu": {
                    "diagnostics": {
                        "spirv_probe_env_audit": {
                            "summary": "stale-target-hash",
                            "expected_source_hash": "0x1bf751845c5dce75",
                            "actual_q6_source_hashes": ["0x9cfc45ae24ba71d8"],
                        },
                        "q6_workgroup_diagnostics": {
                            "q6_final_store_boundary": {
                                "summary": "not-run",
                                "reason": "missing-executed-final-store-trace",
                            }
                        },
                    }
                }
            },
            plan,
        )
        self.assertEqual(
            "spirv_probe_env_audit.summary == stale-target-hash",
            wrong_output_stale_probe["condition"],
        )
        self.assertEqual("Q6 final-store trace probe arming", wrong_output_stale_probe["owner"])

        wrong_output_probe_unarmed = verifier.select_branch(
            {"classification": "llama-completion-wrong-output"},
            {
                "gpu": {
                    "diagnostics": {
                        "spirv_probe_env_audit": {"icd": {"matching_armed_count": 0}},
                        "q6_workgroup_diagnostics": {
                            "q6_final_store_boundary": {
                                "summary": "not-run",
                                "reason": "missing-executed-final-store-trace",
                            },
                            "q6_native_vs_writeback_split": {
                                "summary": "native-final-store-or-readback"
                            },
                        },
                    }
                }
            },
            plan,
        )
        self.assertEqual(
            "spirv_probe_env_audit.icd.matching_armed_count == 0 and q6_final_store_boundary.reason == missing-executed-final-store-trace",
            wrong_output_probe_unarmed["condition"],
        )
        self.assertEqual("Q6 final-store trace probe arming", wrong_output_probe_unarmed["owner"])

        wrong_output_native_split = verifier.select_branch(
            {"classification": "llama-completion-wrong-output"},
            {
                "gpu": {
                    "diagnostics": {
                        "q6_workgroup_diagnostics": {
                            "q6_native_vs_writeback_split": {
                                "summary": "native-final-store-or-readback"
                            }
                        }
                    }
                }
            },
            plan,
        )
        self.assertEqual(
            "q6_native_vs_writeback_split.summary == native-final-store-or-readback",
            wrong_output_native_split["condition"],
        )
        self.assertEqual("native Q6 final-store/readback path", wrong_output_native_split["owner"])

    def test_q6_plan_verifier_selects_terminal_and_pre_q6_branches(self):
        verifier = load_llama_q6_plan_verifier()
        pass_branch = {
            "condition": "q6 oracle/prompt correctness passes",
            "action": "promote this run to correctness-gated performance measurement",
        }
        plan = {"pass_branch": pass_branch}

        terminal = verifier.select_branch(
            {"classification": "q6-workgroup-cleared-and-oracle-match"},
            {},
            plan,
        )
        self.assertEqual(pass_branch, terminal)

        unsupported = verifier.select_branch(
            {"classification": "q6-workgroup-cleared-but-oracle-mismatch"},
            {
                "gpu": {
                    "diagnostics": {
                        "generic_spirv_dispatch": [
                            {
                                "specialization_materialize_report": {
                                    "failure_reason": "unsupported-spec-expression"
                                }
                            }
                        ]
                    }
                }
            },
            plan,
        )
        self.assertEqual(
            "specialization_materialize_report.failure_reason == unsupported-spec-expression",
            unsupported["condition"],
        )
        self.assertEqual("app/src/main/cpp/pdocker_gpu_executor.c", unsupported["owner"])

        pre_q6 = verifier.select_branch(
            {"classification": "q6-not-reached"},
            {},
            plan,
        )
        self.assertEqual("pipeline/device-lost before Q6 evidence", pre_q6["condition"])
        self.assertEqual("pipeline creation policy and hash scope", pre_q6["owner"])

    def test_q6_readonly_alias_side_effects_do_not_block_final_store_diagnosis(self):
        bindings = [
            {
                "index": 2,
                "binding": 2,
                "alias_rep": 1,
                "offset": 16384,
                "size": 607744,
                "readable": True,
                "writable": True,
                "gpu_after_dispatch_hash": "0x2222222222222222",
                "fd_after_hash": "0x2222222222222222",
                "writeback_verified": True,
            },
            {
                "index": 3,
                "binding": 3,
                "alias_rep": 1,
                "offset": 16384,
                "size": 607744,
                "readable": True,
                "writable": False,
                "gpu_after_upload_hash": "0x1111111111111111",
                "gpu_after_dispatch_hash": "0x2222222222222222",
                "fd_after_hash": "0x2222222222222222",
            },
        ]
        classified = classify_q6_readonly_alias_side_effects(bindings)
        self.assertEqual(1, len(classified["q6_readonly_dispatch_mutations"]))
        self.assertEqual(1, len(classified["q6_readonly_dispatch_alias_side_effects"]))
        self.assertEqual([], classified["q6_unexpected_readonly_dispatch_mutations"])

        compare = LLAMA_COMPARE.read_text()
        self.assertIn(
            "if q6_unexpected_readonly_dispatch_mutations",
            compare,
            "raw readonly mutations must not directly select the barrier blocker",
        )
        self.assertNotIn(
            "if q6_readonly_dispatch_mutations\n    else \"writeback\"",
            compare,
            "raw/all readonly mutations include legal alias side-effects",
        )

    def test_q6_readonly_mutation_without_same_storage_window_still_blocks(self):
        writable = {
            "index": 2,
            "binding": 2,
            "alias_rep": 1,
            "offset": 16384,
            "size": 607744,
            "readable": True,
            "writable": True,
            "gpu_after_dispatch_hash": "0x2222222222222222",
            "fd_after_hash": "0x2222222222222222",
            "writeback_verified": True,
        }
        readonly = {
            "index": 3,
            "binding": 3,
            "alias_rep": 1,
            "offset": 16384,
            "size": 607744,
            "readable": True,
            "writable": False,
            "gpu_after_upload_hash": "0x1111111111111111",
            "gpu_after_dispatch_hash": "0x3333333333333333",
            "fd_after_hash": "0x3333333333333333",
        }
        for key, value in [
            ("alias_rep", 9),
            ("offset", 16388),
            ("size", 607740),
        ]:
            mutated = dict(readonly)
            mutated[key] = value
            classified = classify_q6_readonly_alias_side_effects([writable, mutated])
            self.assertEqual([], classified["q6_readonly_dispatch_alias_side_effects"], key)
            self.assertEqual(1, len(classified["q6_unexpected_readonly_dispatch_mutations"]), key)

    def test_q6_readonly_alias_side_effect_detection_fails_closed_without_hash_evidence(self):
        bindings = [
            {
                "index": 2,
                "binding": 2,
                "alias_rep": 1,
                "offset": 16384,
                "size": 607744,
                "readable": True,
                "writable": True,
                "gpu_after_dispatch_hash": "0x0000000000000000",
                "fd_after_hash": None,
                "writeback_verified": True,
            },
            {
                "index": 3,
                "binding": 3,
                "alias_rep": 1,
                "offset": 16384,
                "size": 607744,
                "readable": True,
                "writable": False,
                "gpu_after_upload_hash": "0x1111111111111111",
                "gpu_after_dispatch_hash": "0x2222222222222222",
                "fd_after_hash": "",
            },
        ]
        classified = classify_q6_readonly_alias_side_effects(bindings)
        self.assertEqual([], classified["q6_readonly_dispatch_alias_side_effects"])
        self.assertEqual(1, len(classified["q6_unexpected_readonly_dispatch_mutations"]))

    def test_q6_output_index_probe_classifier_distinguishes_fixed_offset_scatter_and_value(self):
        classify = load_q6_output_index_probe_classifier()
        self.assertEqual("not-run", classify({}, True))
        self.assertEqual(
            "canonical-match",
            classify({"samples": [{"canonical_match": True}]}, True),
        )
        self.assertEqual(
            "fixed-offset",
            classify(
                {
                    "samples": [
                        {
                            "canonical_match": False,
                            "found_elsewhere": True,
                            "best_index_in_store_window": True,
                            "best_store_row_delta": 2,
                        },
                        {
                            "canonical_match": False,
                            "found_elsewhere": True,
                            "best_index_in_store_window": True,
                            "best_store_row_delta": 2,
                        },
                    ]
                },
                True,
            ),
        )
        self.assertEqual(
            "scatter",
            classify(
                {
                    "samples": [
                        {
                            "canonical_match": False,
                            "found_elsewhere": True,
                            "best_index_in_store_window": True,
                            "best_store_row_delta": 2,
                        },
                        {
                            "canonical_match": False,
                            "found_elsewhere": True,
                            "best_index_in_store_window": True,
                            "best_store_row_delta": 7,
                        },
                    ]
                },
                True,
            ),
        )
        self.assertEqual(
            "final-store-value",
            classify(
                {
                    "samples": [
                        {
                            "canonical_match": False,
                            "found_elsewhere": False,
                        }
                    ]
                },
                True,
            ),
        )

    def test_gpu_env_propagation_parity_is_documented_and_guarded(self):
        compare = LLAMA_COMPARE.read_text()
        pdockerd = PDOCKERD.read_text()
        main_activity = MAIN_ACTIVITY.read_text()
        next_steps = LLAMA_GPU_NEXT_STEPS.read_text()
        correctness = LLAMA_GPU_CORRECTNESS.read_text()
        verifier = load_llama_gpu_artifact_verifier()
        manifest = json.loads(LLAMA_GPU_ENV_MANIFEST.read_text())

        self.assertEqual("pdocker.llama.gpu.env-manifest.v1", manifest["schema"])
        self.assertIn("llama-gpu-env-manifest.json", compare)
        self.assertIn("compare_forward_env_keys", compare)
        self.assertIn("config_propagation_env_fields", compare)

        ui_compose_runtime_keys = verifier.LLAMA_GPU_UI_RUNTIME_ENV_KEYS
        for key in ui_compose_runtime_keys:
            self.assertTrue(
                f'"{key}": os.environ.get' in pdockerd
                or any(key.startswith(prefix) for prefix in ("PDOCKER_GPU_", "PDOCKER_VULKAN_", "GGML_VK_"))
                and "GPU_RUNTIME_ENV_PREFIXES" in pdockerd,
                key,
            )
            self.assertIn(key, verifier.LLAMA_GPU_COMPARE_FORWARD_ENV_KEYS)
            self.assertIn(key, next_steps)

        self.assertIn("parseLlamaComposeEnvDefaultsFromManifest", main_activity)
        self.assertIn("fallbackLlamaComposeEnvDefaultsFromBundledCompose", main_activity)
        self.assertIn("project-library/llama-cpp-gpu/compose.yaml", main_activity)
        for key in ui_compose_runtime_keys:
            self.assertNotIn(f'LlamaComposeEnvDefault("{key}",', main_activity)

        manifest_config_pairs = [
            (item["env"], item["executor_field"])
            for item in manifest["config_propagation_env_fields"]
        ]
        self.assertEqual(list(verifier.LLAMA_GPU_CONFIG_PROPAGATION_ENV_FIELDS), manifest_config_pairs)
        for env_name, _field_name in verifier.LLAMA_GPU_CONFIG_PROPAGATION_ENV_FIELDS:
            self.assertIn(env_name, verifier.LLAMA_GPU_COMPARE_FORWARD_ENV_KEYS)

        self.assertIn("LLAMA_GPU_UI_RUNTIME_ENV_KEYS", LLAMA_GPU_ARTIFACT_VERIFIER.read_text())
        self.assertNotIn("LLAMA_GPU_PDOCKERD_RUNTIME_ENV_KEYS", LLAMA_GPU_ARTIFACT_VERIFIER.read_text())
        self.assertNotIn("LLAMA_GPU_UI_COMPOSE_RUNTIME_ENV_KEYS", LLAMA_GPU_ARTIFACT_VERIFIER.read_text())
        self.assertNotIn("LLAMA_GPU_COMPARE_DIAGNOSTIC_ENV_KEYS", LLAMA_GPU_ARTIFACT_VERIFIER.read_text())
        self.assertIn("LLAMA_GPU_COMPARE_FORWARD_ENV_KEYS", LLAMA_GPU_ARTIFACT_VERIFIER.read_text())
        self.assertIn("LLAMA_GPU_CONFIG_PROPAGATION_ENV_FIELDS", LLAMA_GPU_ARTIFACT_VERIFIER.read_text())
        self.assertIn("UNSUPPORTED_GPU_WORK_TOKENS", LLAMA_GPU_ARTIFACT_VERIFIER.read_text())
        self.assertIn("unsupported-gpu-work-accepted", LLAMA_GPU_ARTIFACT_VERIFIER.read_text())
        self.assertIn("UI/compose runtime defaults and compare-only diagnostics", next_steps)
        self.assertIn("Artifact verifier manifest guard", next_steps)
        self.assertIn("Unsupported GPU work gate", next_steps)
        self.assertIn("Environment propagation parity", correctness)

    def test_llama_gpu_artifact_verifier_blocks_env_reflection_misses(self):
        verifier = load_llama_gpu_artifact_verifier()

        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "diagnostics": {
                    "runtime_freshness": llama_runtime_freshness_pass(),
                    "config_propagation": {
                        "summary": "fail",
                        "checks": [
                            {
                                "env": "PDOCKER_GPU_Q6K_SAFE_KERNEL",
                                "executor_field": "q6k_safe_kernel",
                                "expected": True,
                                "observed_values": [],
                                "status": "missing-evidence",
                            }
                        ],
                    },
                    "q6_workgroup_diagnostics": {
                        "workgroup_shape_blocker": False,
                        "latest_status": "match",
                        "q6_writeback_verified_all": True,
                    },
                },
                "correctness": {"summary": {"correctness": "pass"}},
            },
            "cpu": {"tokens_per_second": 0.1},
            "comparison": {"speedup": 3.0, "target_met": True},
        }
        report = verifier.classify(payload)
        self.assertEqual("config-propagation-mismatch", report["classification"])
        self.assertFalse(report["correctness_claim_allowed"])
        self.assertFalse(report["benchmark_claim_allowed"])
        self.assertIn("PDOCKER_GPU_Q6K_SAFE_KERNEL", json.dumps(report["config_propagation"]))

    def test_llama_compare_records_completion_readiness_and_runtime_env(self):
        compare = LLAMA_COMPARE.read_text()
        start_script = (ROOT / "app/src/main/assets/project-library/llama-cpp-gpu/scripts/start-llama-server.sh").read_text()
        verifier_source = LLAMA_GPU_ARTIFACT_VERIFIER.read_text()

        for marker in [
            "PDOCKER_LLAMA_COMPLETION_READY_TIMEOUT_SEC",
            "probe_service_readiness",
            "engine_body_has_id",
            "create timeout left no inspectable named container",
            '"schema": "pdocker.llama.service-readiness.v1"',
            '"prompt": "2+3="',
            '"n_predict": 1',
            '"expected": ["5"]',
            '"status": "pass"',
            '"health": "pass"',
            '"models": "pass"',
            "service_readiness",
            "container_exit_snapshot",
            "synthesize_service_readiness_for_unserved",
            "container_exit",
            "container-exited-before-readiness",
            "runtime_env",
            "startup_diagnostics",
            "container_config_env",
            "container_archive_file",
            "except (EOFError, OSError, tarfile.TarError)",
            "container_env_snapshot",
            "LLAMA_",
            "PDOCKER_GPU_",
            "PDOCKER_VULKAN_",
            "GGML_VK_",
            "VK_ICD_FILENAMES",
            "VK_DRIVER_FILES",
            "OCL_ICD_VENDORS",
            "llama_completion_timeout",
        ]:
            self.assertIn(marker, compare)

        for marker in [
            "LLAMA_STARTUP_JSON",
            "pdocker.llama.startup.v1",
            "profile_refresh_rc",
            "kv_offload_guarded",
            "kv_offload_env",
            "kv_offload_disabled_effective",
            "Skydnir llama startup diagnostics",
        ]:
            self.assertIn(marker, start_script)

        self.assertIn("llama-completion-timeout", verifier_source)
        self.assertIn("_service_completion_timeout", verifier_source)

    def test_llama_gpu_artifact_verifier_classifies_completion_timeout(self):
        verifier = load_llama_gpu_artifact_verifier()
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "served": True,
                "runtime_env": {
                    "LLAMA_GPU_BACKEND": "vulkan",
                    "PDOCKER_VULKAN_ICD_KIND": "pdocker-bridge-minimal",
                },
                "service_readiness": {
                    "schema": "pdocker.llama.service-readiness.v1",
                    "summary": {
                        "health": "pass",
                        "models": "pass",
                        "liveness": "pass",
                        "completion": "fail",
                        "ready": False,
                    },
                    "health": {"ok": True, "status": "pass", "status_code": 200, "duration_ms": 3},
                    "models": {"ok": True, "status": "pass", "status_code": 200, "duration_ms": 4},
                    "completion": {
                        "ok": False,
                        "status": "fail",
                        "error": "TimeoutError: timed out",
                        "timeout_sec": 180,
                        "duration_ms": 180000,
                    },
                },
                "diagnostics": {
                    "runtime_freshness": {
                        "summary": "fail",
                        "expected_executor_marker": "gpu-executor-q6-readonly-snapshot-20260531",
                        "observed_executor_markers": [],
                    },
                },
            },
        }
        report = verifier.classify(payload)
        self.assertEqual("llama-completion-timeout", report["classification"])
        self.assertFalse(report["correctness_claim_allowed"])
        self.assertFalse(report["benchmark_claim_allowed"])
        self.assertIn("LLAMA_GPU_BACKEND", report["runtime_env"])
        self.assertTrue(report["service_readiness"]["health_ok"])
        self.assertTrue(report["service_readiness"]["models_ok"])
        self.assertEqual("fail", report["service_readiness"]["completion_status"])

    def test_llama_gpu_artifact_verifier_preserves_q6_evidence_on_wrong_output(self):
        verifier = load_llama_gpu_artifact_verifier()
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "served": True,
                "runtime_env": {"PDOCKER_GPU_MODE": "vulkan-raw"},
                "service_readiness": {
                    "schema": "pdocker.llama.service-readiness.v1",
                    "summary": {"health": "pass", "models": "pass", "completion": "pass"},
                    "health": {"ok": True, "status": "pass"},
                    "models": {"ok": True, "status": "pass"},
                    "completion": {
                        "ok": True,
                        "status": "pass",
                        "passed": False,
                        "content_excerpt": " Marvel",
                    },
                },
                "diagnostics": {
                    "runtime_freshness": llama_runtime_freshness_pass(),
                    "api_executor_reconciliation": {
                        "summary": "diagnostic",
                        "proof_strength": "diagnostic",
                        "dispatches": [
                            {
                                "match_status": "diagnostic-match",
                                "matches": {
                                    "core_command_hash_comparable": True,
                                    "core_command_hash": True,
                                    "spirv_hash": True,
                                    "descriptor_hash": True,
                                    "push_hash": True,
                                    "spec_hash": True,
                                    "dispatch_hash": True,
                                },
                                "transport": {"msg_trunc": False, "msg_ctrunc": False},
                            }
                        ],
                    },
                    "q6_workgroup_diagnostics": {
                        "latest_status": "mismatch",
                        "blocker_class": "native-q6-final-store-or-readback",
                        "q6_final_store_boundary": {
                            "summary": "not-run",
                            "reason": "missing-executed-final-store-trace",
                        },
                        "q6_native_vs_writeback_split": {
                            "summary": "native-final-store-or-readback",
                            "samples": [
                                {
                                    "native_gpu_at_dst": 5.0,
                                    "expected": 10.0,
                                    "fd_after_writeback": 5.0,
                                    "native_matches_expected": False,
                                    "writeback_matches_expected": False,
                                    "writeback_matches_native": True,
                                }
                            ],
                        },
                    },
                },
            },
        }
        report = verifier.classify(payload)
        self.assertEqual("llama-completion-wrong-output", report["classification"])
        self.assertEqual("native-q6-final-store-or-readback", report["q6_effective_blocker_class"])
        self.assertEqual(
            "native-final-store-or-readback",
            report["q6_native_vs_writeback_split"]["summary"],
        )
        self.assertIn("q6_workgroup_diagnostics", report)
        self.assertEqual("pass", report["api_executor_reconciliation"]["summary"])

    def test_llama_gpu_artifact_verifier_requires_health_and_models_for_completion_timeout(self):
        verifier = load_llama_gpu_artifact_verifier()
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "served": True,
                "service_readiness": {
                    "schema": "pdocker.llama.service-readiness.v1",
                    "summary": {
                        "health": "fail",
                        "models": "pass",
                        "liveness": "fail",
                        "completion": "fail",
                        "ready": False,
                    },
                    "health": {"ok": False, "status": "fail", "error": "HTTP Error 503"},
                    "models": {"ok": True, "status": "pass", "status_code": 200},
                    "completion": {
                        "ok": False,
                        "status": "fail",
                        "error": "TimeoutError: timed out",
                        "timeout_sec": 180,
                    },
                },
                "diagnostics": {
                    "runtime_freshness": {
                        "summary": "fail",
                        "expected_executor_marker": "gpu-executor-q6-readonly-snapshot-20260531",
                        "observed_executor_markers": [],
                    },
                },
            },
        }
        readiness = verifier._service_completion_timeout(payload)
        self.assertFalse(readiness["timeout"])
        self.assertFalse(readiness["health_ok"])
        self.assertTrue(readiness["models_ok"])
        self.assertNotEqual("llama-completion-timeout", verifier.classify(payload)["classification"])

    def test_llama_gpu_artifact_verifier_prefers_pre_http_gpu_blocker(self):
        verifier = load_llama_gpu_artifact_verifier()
        payload = {
            "schema": "pdocker.llama.gpu.compare.v1",
            "gpu": {
                "served": False,
                "diagnostics": {
                    "blocker_class": "vulkan_pipeline_feature",
                    "blocker_detail": "Android Vulkan rejected a ggml generic SPIR-V compute pipeline with VK_ERROR_FEATURE_NOT_PRESENT",
                    "runtime_freshness": {
                        "summary": "pass",
                        "marker_summary": "pass",
                        "bridge_binary_summary": "pass",
                        "expected_executor_marker": "gpu-executor-q6-readonly-snapshot-20260531",
                        "observed_executor_markers": ["gpu-executor-q6-readonly-snapshot-20260531"],
                        "executor_event_count": 1,
                        "bridge_binary_identity": llama_bridge_binary_identity(),
                    },
                    "config_propagation": {"summary": "pass", "checks": []},
                },
            },
        }
        report = verifier.classify(payload)
        self.assertEqual("vulkan-pipeline-feature", report["classification"])
        self.assertFalse(report["correctness_claim_allowed"])
        self.assertFalse(report["benchmark_claim_allowed"])
        self.assertEqual("vulkan_pipeline_feature", report["gpu_blocker_class"])
        self.assertIn("VK_ERROR_FEATURE_NOT_PRESENT", report["gpu_blocker_detail"])
        self.assertIn("pre_http_failure_evidence", report)
        self.assertIn("q6_reachability", report["pre_http_failure_evidence"])

    def test_llama_gpu_compare_records_pre_q6_failure_summary(self):
        compare = LLAMA_COMPARE.read_text()
        for marker in [
            "def compact_pre_q6_failure",
            'generic_spirv_dispatch["pre_q6_failure"]',
            '"q6_reachability"',
            '"failed_event_count"',
            '"failure_event"',
            '"requested_feature_mask"',
            '"spirv_required_feature_mask"',
            '"spirv_requested_feature_missing_mask"',
            '"spirv_requested_feature_mismatches"',
            '"spirv_feature_requirements"',
            '"android_vulkan_features"',
            '"android_vulkan_enabled_features"',
            "][:4]",
            "event = failed[0] if failed else {}",
        ]:
            self.assertIn(marker, compare)

    def test_gpu_executor_reports_enabled_vulkan_features_in_json(self):
        source = GPU_EXECUTOR.read_text()
        for marker in [
            "write_android_vulkan_enabled_features_report",
            '\\"android_vulkan_enabled_features\\":{',
            '\\"tessellationShader\\":%u',
            '\\"chain_compat_feature_structs\\":%u',
            "enabled_ext_16bit_storage",
            "enabled_ext_8bit_storage",
            "enabled_ext_shader_float16_int8",
            "enabled_ext_storage_buffer_storage_class",
            "#define PDOCKER_GPU_EXECUTOR_BUILD_MARKER \"gpu-executor-q6-readonly-snapshot-20260531\"",
        ]:
            self.assertIn(marker, source)
        failure_body = source.split("if (ret != 0) {", 1)[1].split("if (fence) vkDestroyFence", 1)[0]
        failure_features = failure_body.index('\\"android_vulkan_features\\":{')
        failure_enabled = failure_body.index("write_android_vulkan_enabled_features_report(json_out(), rt);")
        failure_close = failure_body.index('fprintf(json_out(), "}\\n");', failure_enabled)
        self.assertLess(failure_features, failure_enabled)
        self.assertLess(failure_enabled, failure_close)
        capabilities_body = source.split("static void print_capabilities", 1)[1].split(
            "static void print_noop", 1
        )[0]
        capabilities_features = capabilities_body.index('\\"android_vulkan_features\\":{')
        capabilities_enabled = capabilities_body.index("write_android_vulkan_enabled_features_report(json_out(), rt);")
        capabilities_close = capabilities_body.index('fprintf(json_out(), ",\\\"process_exec\\\":true}\\n");')
        self.assertLess(capabilities_features, capabilities_enabled)
        self.assertLess(capabilities_enabled, capabilities_close)

    def test_gpu_executor_exposes_vulkan_advertisement_caps_command(self):
        source = GPU_EXECUTOR.read_text()
        self.assertIn('strcmp(cmd, "VULKAN_ADVERTISEMENT_CAPS") == 0', source)
        self.assertIn("print_vulkan_advertisement_caps(\"unix-socket-command-queue\")", source)
        body = source.split("static void print_vulkan_advertisement_caps", 1)[1].split(
            "static void print_noop", 1
        )[0]
        for marker in [
            '\\"command\\":\\"VULKAN_ADVERTISEMENT_CAPS\\"',
            '\\"schema\\":\\"skydnir-vulkan-advertisement-caps-v1\\"',
            '\\"executor_build_marker\\":\\"%s\\"',
            '\\"device\\":{',
            '\\"apiVersion\\":%u',
            '\\"deviceType\\":%u',
            '\\"deviceName\\":',
            '\\"limits\\":{',
            '\\"maxStorageBufferRange\\":%u',
            '\\"physical_features\\":{',
            '\\"tessellationShader\\":%u',
            '\\"storage16\\":{',
            '\\"storage8\\":{',
            '\\"float16_int8\\":{',
            '\\"subgroup\\":{',
            "write_android_vulkan_enabled_features_report(out, rt);",
        ]:
            self.assertIn(marker, body)
        self.assertIn("write_json_string_literal(out, rt ? rt->physical_properties.deviceName : \"offline\")", body)
        format_caps_body = source.split("static void write_vulkan_image_format_caps_report", 1)[1].split(
            "static void print_vulkan_advertisement_caps", 1
        )[0]
        self.assertIn('\\"format_caps_schema\\":1', format_caps_body)
        self.assertIn('\\"format_caps_count\\":%zu', format_caps_body)
        self.assertIn('\\"image_format_caps\\":{', format_caps_body)
        self.assertIn('\\"fmt%dOptimalFeatures\\":%u', format_caps_body)
        self.assertIn('\\"fmt%dSampleCounts\\":%u', format_caps_body)
        self.assertIn("VkFormatFeatureFlags features = 0;", format_caps_body)
        self.assertIn("VkSampleCountFlags sample_counts = 0;", format_caps_body)
        self.assertIn("vkGetPhysicalDeviceFormatProperties(rt->physical_device, format, &props);", format_caps_body)
        self.assertIn("features = props.optimalTilingFeatures & vulkan_graphics_transport_format_features(format);", format_caps_body)
        self.assertIn("sample_counts = vulkan_graphics_runtime_format_sample_counts(rt, format, features);", format_caps_body)
        self.assertNotIn("features = vulkan_graphics_legacy_format_features(format);", format_caps_body)
        sample_query_body = source.split("static VkSampleCountFlags vulkan_graphics_runtime_query_sample_counts", 1)[1].split(
            "static VkSampleCountFlags vulkan_graphics_runtime_format_sample_counts", 1
        )[0]
        self.assertIn("vkGetPhysicalDeviceImageFormatProperties", sample_query_body)
        sample_format_body = source.rsplit("static VkSampleCountFlags vulkan_graphics_runtime_format_sample_counts", 1)[1].split(
            "static int vulkan_graphics_v614_resolve_runtime_eligible", 1
        )[0]
        self.assertIn("VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT", sample_format_body)
        self.assertIn("VK_IMAGE_USAGE_TRANSFER_SRC_BIT", sample_format_body)
        self.assertIn("color_counts & resolve_src_counts", sample_format_body)
        self.assertIn("!vulkan_format_has_depth_aspect(format)", sample_format_body)
        self.assertIn("!vulkan_format_has_stencil_aspect(format)", sample_format_body)
        self.assertIn("VkSampleCountFlags counts = VK_SAMPLE_COUNT_1_BIT;", sample_format_body)
        self.assertNotIn("VK_IMAGE_USAGE_SAMPLED_BIT", sample_format_body)
        self.assertNotIn("VK_IMAGE_USAGE_STORAGE_BIT", sample_format_body)
        self.assertNotIn("VK_IMAGE_USAGE_DEPTH_STENCIL_ATTACHMENT_BIT", sample_format_body)
        explicit_resolve_body = source.split("static int vulkan_graphics_v614_resolve_runtime_eligible", 1)[1].split(
            "static int vulkan_graphics_v615_blit_runtime_eligible", 1
        )[0]
        self.assertNotIn("(void)src_image", explicit_resolve_body)
        self.assertIn("src_image->usage & VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT", explicit_resolve_body)
        self.assertIn("src_image->samples == VK_SAMPLE_COUNT_1_BIT", explicit_resolve_body)
        self.assertIn("dst_image->samples != VK_SAMPLE_COUNT_1_BIT", explicit_resolve_body)
        self.assertIn("src_image->tiling != VK_IMAGE_TILING_OPTIMAL", explicit_resolve_body)
        self.assertIn("dst_image->tiling != VK_IMAGE_TILING_OPTIMAL", explicit_resolve_body)
        self.assertIn("entry->src_layout != VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL", explicit_resolve_body)
        self.assertIn("entry->dst_layout != VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL", explicit_resolve_body)
        self.assertIn("VK_FORMAT_FEATURE_COLOR_ATTACHMENT_BIT", explicit_resolve_body)
        self.assertIn("VK_FORMAT_FEATURE_TRANSFER_SRC_BIT", explicit_resolve_body)
        self.assertIn("VK_FORMAT_FEATURE_TRANSFER_DST_BIT", explicit_resolve_body)
        self.assertIn("vulkan_graphics_v610_image_subresource_range_valid", explicit_resolve_body)
        self.assertIn("return 1;", explicit_resolve_body)
        enabled_body = source.split("static void write_android_vulkan_enabled_features_report", 1)[1].split(
            "static void log_vulkan_feature_gap", 1
        )[0]
        self.assertIn('\\"drawIndirectCount\\":%u', enabled_body)
        self.assertIn('\\"drawIndexedIndirectCount\\":%u', enabled_body)
        self.assertIn('\\"VK_KHR_draw_indirect_count\\":%u', enabled_body)
        self.assertIn('\\"VK_AMD_draw_indirect_count\\":%u', enabled_body)
        self.assertIn("rt && rt->cmd_draw_indirect_count ? 1u : 0u", enabled_body)
        self.assertIn("rt && rt->cmd_draw_indexed_indirect_count ? 1u : 0u", enabled_body)
        self.assertIn("rt ? rt->enabled_ext_draw_indirect_count_khr : 0", enabled_body)
        self.assertIn("rt ? rt->enabled_ext_draw_indirect_count_amd : 0", enabled_body)

    def test_vulkan_icd_can_shadow_query_executor_advertisement_caps(self):
        icd = VULKAN_ICD.read_text()
        for marker in [
            "typedef struct {",
            "PdockerVkAdvertisedCaps",
            "parse_executor_advertisement_caps_json",
            "json_read_u32",
            "json_read_u32_array3",
            "json_read_string",
            "pdocker_vk_advertised_caps",
            "executor_valid",
            "storage16.storageBuffer16BitAccess",
            "storage8.storageBuffer8BitAccess",
            "float16_int8.shaderInt8",
            "features.tessellationShader",
            "tessellationShader:%u",
            "multiview",
            "subgroup.supportedOperations",
            "timeline_semaphore",
            "synchronization2",
            "dynamic_rendering",
            "ext_timeline_semaphore",
            "ext_synchronization2",
            "ext_dynamic_rendering",
            "draw_indirect_count",
            "draw_indexed_indirect_count",
            "ext_draw_indirect_count_khr",
            "ext_draw_indirect_count_amd",
            "advertised_timeline_semaphore",
            "advertised_synchronization2",
            "advertised_dynamic_rendering",
            "advertised_tessellation_shader",
            "advertised_draw_indirect_count",
            "advertised_draw_indexed_indirect_count",
            "advertised_draw_indirect_count_khr",
            "advertised_draw_indirect_count_amd",
            "advertised_extended_dynamic_state",
            "advertised_api_version",
            "advertised_api_1_3",
            "executor_advertisement_source_enabled",
            "PDOCKER_VULKAN_ADVERTISEMENT_SOURCE",
            'strcmp(source, "executor") == 0',
            "executor_advertisement_caps_if_enabled",
            "executor_advertised_shader_int64_or",
            "executor_advertised_storage16_or",
            "executor_advertised_storage8_or",
        ]:
            self.assertIn(marker, icd)
        self.assertIn("query_executor_advertisement_caps_line", icd)
        self.assertIn("PDOCKER_VK_ADVERTISEMENT_CAPS_LINE_MAX 32768u", icd)
        self.assertIn("char line[PDOCKER_VK_ADVERTISEMENT_CAPS_LINE_MAX];", icd)
        self.assertIn('const char command[] = "VULKAN_ADVERTISEMENT_CAPS\\n";', icd)
        self.assertIn("pFormatProperties->optimalTilingFeatures = pdocker_vk_advertised_image_features(format);", icd)
        self.assertIn('\\"schema\\":\\"skydnir-vulkan-advertisement-caps-v1\\"', icd)
        parse_body = icd.split("static bool parse_executor_advertisement_caps_json", 1)[1].split(
            "static int query_executor_advertisement_caps_line", 1
        )[0]
        self.assertIn("format_caps_schema != 1", parse_body)
        self.assertIn("expected_format_cap_count != (uint32_t)pdocker_vk_bridge_format_count()", parse_body)
        self.assertIn("expected_format_cap_count > PDOCKER_VK_ADVERTISED_FORMAT_CAP_MAX", parse_body)
        self.assertIn("!json_read_u32(json, key, &optimal_features)", parse_body)
        self.assertIn("!json_read_u32(json, key, &sample_counts)", parse_body)
        self.assertIn("optimal_features & pdocker_vk_transport_image_features(format)", parse_body)
        self.assertIn("sample_counts & PDOCKER_VK_SUPPORTED_SAMPLE_COUNTS", parse_body)
        self.assertIn("caps->image_format_cap_count == expected_format_cap_count", parse_body)
        query_body = icd.split("static int query_executor_advertisement_caps_line", 1)[1].split(
            "static const PdockerVkAdvertisedCaps *pdocker_vk_advertised_caps", 1
        )[0]
        self.assertIn("bool saw_newline = false;", query_body)
        self.assertIn("!saw_newline && off + 1 >= line_cap", query_body)
        self.assertIn("-EOVERFLOW", query_body)
        advertised_image_body = icd.split("static VkFormatFeatureFlags pdocker_vk_advertised_image_features(VkFormat format) {", 1)[1].split(
            "static VkSampleCountFlags pdocker_vk_advertised_sample_counts", 1
        )[0]
        self.assertIn("if (executor_advertisement_source_enabled())", advertised_image_body)
        self.assertIn("if (!caps || !caps->executor_valid) return 0;", advertised_image_body)
        self.assertIn("if (!cap) return 0;", advertised_image_body)
        self.assertIn("return pdocker_vk_format_image_features(format);", advertised_image_body)
        advertised_sample_body = icd.split("static VkSampleCountFlags pdocker_vk_advertised_sample_counts(VkFormat format) {", 1)[1].split(
            "static VkBool32 executor_advertised_shader_int64_or", 1
        )[0]
        self.assertIn("if (executor_advertisement_source_enabled())", advertised_sample_body)
        self.assertIn("if (!caps || !caps->executor_valid) return 0;", advertised_sample_body)
        self.assertIn("if (!cap) return 0;", advertised_sample_body)
        self.assertIn("return cap->sample_counts & PDOCKER_VK_SUPPORTED_SAMPLE_COUNTS;", advertised_sample_body)
        self.assertIn("return VK_SAMPLE_COUNT_1_BIT;", advertised_sample_body)
        self.assertIn("pdocker_vk_msaa_color_attachment_request", advertised_sample_body)
        self.assertIn("const VkImageUsageFlags accepted_msaa_usage", advertised_sample_body)
        self.assertIn("VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT |", advertised_sample_body)
        self.assertIn("VK_IMAGE_USAGE_TRANSFER_SRC_BIT", advertised_sample_body)
        self.assertIn("(usage & ~accepted_msaa_usage) != 0", advertised_sample_body)
        self.assertIn("usage & VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT", advertised_sample_body)
        self.assertNotIn("usage == VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT", advertised_sample_body)
        self.assertNotIn("VK_IMAGE_USAGE_SAMPLED_BIT", advertised_sample_body)
        self.assertNotIn("VK_IMAGE_USAGE_STORAGE_BIT", advertised_sample_body)
        self.assertIn("pdocker_vk_image_sample_counts_for_request", advertised_sample_body)
        self.assertIn("pdocker_vk_advertised_color_attachment_sample_counts", advertised_sample_body)
        self.assertIn("counts ? counts : VK_SAMPLE_COUNT_1_BIT", advertised_sample_body)
        self.assertIn("trace_executor_advertisement_caps_once", icd)
        self.assertIn("executor advertisement caps shadow", icd)
        fill_props = icd.split("static void fill_physical_device_properties", 1)[1].split(
            "pProperties->apiVersion", 1
        )[0]
        self.assertIn("trace_executor_advertisement_caps_once();", fill_props)
        properties_body = icd.split("static void fill_physical_device_properties", 1)[1].split(
            "static VkSubgroupFeatureFlags advertised_subgroup_operations", 1
        )[0]
        self.assertIn("const PdockerVkAdvertisedCaps *caps = executor_advertisement_caps_if_enabled();", properties_body)
        self.assertIn("caps && caps->api_version ? caps->api_version : pdocker_api_version()", properties_body)
        self.assertIn("caps->device_type", properties_body)
        self.assertIn("caps->limits.maxComputeSharedMemorySize", properties_body)
        self.assertIn("caps->limits.maxStorageBufferRange < transport_max_storage_range", properties_body)
        self.assertIn("caps->limits.maxBoundDescriptorSets < PDOCKER_VK_MAX_DESCRIPTOR_SETS", properties_body)
        self.assertIn("pdocker_vk_max_per_set_descriptors", icd)
        self.assertIn("PDOCKER_VK_MAX_STORAGE_BUFFERS * PDOCKER_VK_MAX_DESCRIPTOR_ARRAY_ELEMENTS", icd)
        self.assertNotIn("maxPerSetDescriptors = 1024", icd)
        self.assertIn("p->maxPerSetDescriptors = pdocker_vk_max_per_set_descriptors();", icd)
        pnext_body = icd.split("static void fill_pnext_features", 1)[1].split(
            "static uint64_t feature_mask_from_base_features", 1
        )[0]
        self.assertIn("const PdockerVkAdvertisedCaps *caps = executor_advertisement_caps_if_enabled();", pnext_body)
        self.assertIn("caps->storage16.uniformAndStorageBuffer16BitAccess", pnext_body)
        self.assertIn("caps->storage8.uniformAndStorageBuffer8BitAccess", pnext_body)
        self.assertIn("caps->float16_int8.shaderFloat16", pnext_body)
        self.assertIn("p->timelineSemaphore = advertised_timeline_semaphore();", pnext_body)
        self.assertIn("p->drawIndirectCount = advertised_draw_indirect_count() && advertised_draw_indexed_indirect_count();", pnext_body)
        self.assertIn("p->synchronization2 = advertised_synchronization2();", pnext_body)
        self.assertIn("p->dynamicRendering = advertised_dynamic_rendering();", pnext_body)
        self.assertIn("p->multiview = caps->multiview;", pnext_body)
        self.assertIn("pFeatures->tessellationShader = advertised_tessellation_shader();", icd)
        self.assertIn("p->extendedDynamicState = advertised_extended_dynamic_state();", pnext_body)
        self.assertIn("p->extendedDynamicState2 = advertised_extended_dynamic_state2();", pnext_body)
        self.assertIn("p->extendedDynamicState2LogicOp = advertised_extended_dynamic_state2_logic_op();", pnext_body)
        self.assertIn("p->extendedDynamicState2PatchControlPoints = advertised_extended_dynamic_state2_patch_control_points();", pnext_body)
        extension_body = icd.split("vkEnumerateDeviceExtensionProperties", 1)[1].split(
            "#undef ADD_DEVICE_EXTENSION", 1
        )[0]
        self.assertIn("caps ? caps->ext_16bit_storage : advertised_storage16()", extension_body)
        self.assertIn("caps ? caps->ext_8bit_storage : advertised_storage8()", extension_body)
        self.assertIn("caps ? caps->ext_shader_float16_int8 : advertised_storage8()", extension_body)
        self.assertIn("advertised_storage_buffer_storage_class()", extension_body)
        self.assertIn("advertised_timeline_semaphore()", extension_body)
        self.assertIn("advertised_synchronization2()", extension_body)
        self.assertIn("advertised_dynamic_rendering()", extension_body)
        self.assertIn("advertised_extended_dynamic_state()", extension_body)
        self.assertIn("advertised_extended_dynamic_state2()", extension_body)
        self.assertIn("VK_KHR_DRAW_INDIRECT_COUNT_EXTENSION_NAME", extension_body)
        self.assertIn("VK_AMD_DRAW_INDIRECT_COUNT_EXTENSION_NAME", extension_body)
        self.assertIn("advertised_draw_indirect_count_khr()", extension_body)
        self.assertIn("advertised_draw_indirect_count_amd()", extension_body)
        self.assertIn("VK_KHR_draw_indirect_count", icd)
        self.assertIn("VK_AMD_draw_indirect_count", icd)
        self.assertIn("return (caps && caps->ext_storage_buffer_storage_class) ? VK_TRUE : VK_FALSE;", icd)
        self.assertIn("return (caps && caps->timeline_semaphore && caps->ext_timeline_semaphore) ? VK_TRUE : VK_FALSE;", icd)
        self.assertIn("return (caps && caps->synchronization2 && caps->ext_synchronization2) ? VK_TRUE : VK_FALSE;", icd)
        self.assertIn("return (caps && caps->dynamic_rendering && caps->ext_dynamic_rendering) ? VK_TRUE : VK_FALSE;", icd)
        self.assertIn("return (caps && caps->draw_indirect_count) ? VK_TRUE : VK_FALSE;", icd)
        self.assertIn("return (caps && caps->draw_indexed_indirect_count) ? VK_TRUE : VK_FALSE;", icd)
        self.assertIn("return (caps && caps->ext_extended_dynamic_state) ? VK_TRUE : VK_FALSE;", icd)
        self.assertIn("caps->ext_extended_dynamic_state2", icd)
        self.assertIn("caps->extended_dynamic_state2.extendedDynamicState2", icd)
        self.assertIn("caps->extended_dynamic_state2.extendedDynamicState2LogicOp", icd)
        self.assertIn("caps->extended_dynamic_state2.extendedDynamicState2PatchControlPoints", icd)
        self.assertIn("advertised_extended_dynamic_state2_patch_control_points()", icd)
        self.assertIn("vkCmdSetPatchControlPointsEXT", icd)
        self.assertNotIn("!caps || caps->ext_extended_dynamic_state", icd)
        proc_gate_body = icd.split("static bool proc_address_hidden_by_advertisement", 1)[1].split(
            "static PFN_vkVoidFunction proc_address", 1
        )[0]
        proc_body = icd.split("static PFN_vkVoidFunction proc_address", 1)[1].split("#define MAP_PROC", 1)[0]
        self.assertIn("proc_address_hidden_by_advertisement(pName)", proc_body)
        for marker in [
            "!advertised_dynamic_rendering()",
            "!advertised_synchronization2()",
            "!advertised_timeline_semaphore()",
            "!advertised_extended_dynamic_state()",
            "!advertised_extended_dynamic_state2_logic_op()",
            "!advertised_draw_indirect_count_khr()",
            "!advertised_draw_indirect_count_amd()",
            "!advertised_api_1_3()",
            "vkGetPhysicalDeviceProperties2KHR",
            "vkGetBufferMemoryRequirements2KHR",
            "vkBindBufferMemory2KHR",
            "vkCreateRenderPass2KHR",
            "vkCmdBeginRenderPass2KHR",
            "vkGetDeviceBufferMemoryRequirements",
            "vkCmdBeginRendering",
            "vkCmdCopyBuffer2",
            "vkQueueSubmit2",
            "vkCmdBeginRenderingKHR",
            "vkQueueSubmit2KHR",
            "vkGetSemaphoreCounterValueKHR",
            "vkCmdBindVertexBuffers2EXT",
            "vkCmdSetViewportWithCountEXT",
            "vkCmdSetScissorWithCountEXT",
            "vkCmdSetCullModeEXT",
        ]:
            self.assertIn(marker, proc_gate_body)
        self.assertIn("advertised_draw_indirect_count() && advertised_draw_indexed_indirect_count()", proc_gate_body)
        self.assertIn("PDOCKER_VK_FEATURE_DRAW_INDIRECT_COUNT", icd)
        self.assertIn("if (p->drawIndirectCount) mask |= PDOCKER_VK_FEATURE_DRAW_INDIRECT_COUNT;", icd)
        self.assertIn("mask |= PDOCKER_VK_FEATURE_DRAW_INDIRECT_COUNT;", icd)
        self.assertIn("vkCmdDrawIndirectCountKHR", proc_gate_body)
        self.assertIn("vkCmdDrawIndexedIndirectCountKHR", proc_gate_body)
        self.assertNotIn("!caps || caps->ext_storage_buffer_storage_class", extension_body)
        self.assertIn("PDOCKER_VULKAN_ICD_DEBUG", icd)

    def test_llama_gpu_dispatch_lifecycle_logs_are_recorded(self):
        compare = LLAMA_COMPARE.read_text()
        icd = VULKAN_ICD.read_text()
        executor = GPU_EXECUTOR.read_text()

        for marker in [
            "generic dispatch lifecycle:",
            "dispatch_lifecycle_log_enabled",
            "g_generic_dispatch_sequence",
        ]:
            self.assertIn(marker, icd)
        self.assertIn('\\"event\\":\\"begin\\"', icd)
        self.assertIn('\\"event\\":\\"end\\"', icd)
        self.assertIn('component', icd)
        self.assertIn('icd', icd)

        for marker in [
            "generic dispatch lifecycle:",
            "g_vulkan_dispatch_lifecycle_sequence",
        ]:
            self.assertIn(marker, executor)
        self.assertIn('\\"event\\":\\"stage\\"', executor)
        self.assertIn('\\"stage\\":\\"submit\\"', executor)
        self.assertIn('\\"stage\\":\\"wait-complete\\"', executor)
        self.assertIn('component', executor)
        self.assertIn('executor', executor)

        for marker in [
            "extract_dispatch_lifecycle_events",
            "summarize_dispatch_lifecycle",
            "unmatched_begin_ids",
            "dispatch_lifecycle",
        ]:
            self.assertIn(marker, compare)


if __name__ == "__main__":
    unittest.main()

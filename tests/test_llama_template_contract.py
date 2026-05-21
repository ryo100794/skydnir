import unittest
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LLAMA_ROOT = ROOT / "app" / "src" / "main" / "assets" / "project-library" / "llama-cpp-gpu"
LLAMA_DOCKERFILE = LLAMA_ROOT / "Dockerfile"
LLAMA_COMPOSE = LLAMA_ROOT / "compose.yaml"
LLAMA_START = LLAMA_ROOT / "scripts" / "start-llama-server.sh"
LLAMA_CORRECTNESS = LLAMA_ROOT / "scripts" / "pdocker-llama-correctness.sh"
MAIN_ACTIVITY = ROOT / "app" / "src" / "main" / "kotlin" / "io" / "github" / "ryo100794" / "pdocker" / "MainActivity.kt"
LLAMA_GPU_ENV_MANIFEST = ROOT / "scripts" / "llama-gpu-env-manifest.json"


class LlamaTemplateContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dockerfile = LLAMA_DOCKERFILE.read_text()
        cls.compose = LLAMA_COMPOSE.read_text()
        cls.start = LLAMA_START.read_text()
        cls.correctness = LLAMA_CORRECTNESS.read_text()
        cls.main_activity = MAIN_ACTIVITY.read_text()
        cls.env_manifest = json.loads(LLAMA_GPU_ENV_MANIFEST.read_text())

    def test_openblas_uses_standard_cmake_detection(self):
        self.assertIn("-DGGML_BLAS=ON", self.dockerfile)
        self.assertIn("-DGGML_BLAS_VENDOR=OpenBLAS", self.dockerfile)
        self.assertIn("libopenblas-dev", self.dockerfile)
        self.assertLess(
            self.dockerfile.find("libopenblas-dev"),
            self.dockerfile.find("cmake -B build -G Ninja"),
            "OpenBLAS must be installed before standard CMake detection runs",
        )
        forbidden = [
            "-DBLAS_LIBRARIES=",
            "-DBLAS_INCLUDE_DIRS=",
            "pkg-config --variable=libdir openblas",
            "pkg-config --variable=includedir openblas",
        ]
        for marker in forbidden:
            with self.subTest(marker=marker):
                self.assertNotIn(marker, self.dockerfile)

    def test_template_keeps_upstream_llama_build_flow(self):
        ordered_markers = [
            "git clone --depth 1 https://github.com/ggml-org/llama.cpp",
            "git checkout --detach FETCH_HEAD",
            "cmake -B build -G Ninja",
            "cmake --build build",
        ]
        positions = [self.dockerfile.find(marker) for marker in ordered_markers]
        self.assertTrue(all(pos >= 0 for pos in positions), positions)
        self.assertEqual(positions, sorted(positions))

    def test_server_build_includes_upstream_browser_ui(self):
        self.assertIn("-DLLAMA_BUILD_WEBUI=ON", self.dockerfile)
        self.assertNotIn("-DLLAMA_BUILD_WEBUI=OFF", self.dockerfile)
        self.assertIn(
            'LLAMA_EXTRA_ARGS: "${LLAMA_EXTRA_ARGS:---path /opt/llama.cpp/tools/server/public --jinja}"',
            self.compose,
        )
        self.assertIn("http://127.0.0.1:18081/health", (LLAMA_ROOT / "README.md").read_text())
        self.assertIn("http://127.0.0.1:18081/", (LLAMA_ROOT / "README.md").read_text())
        self.assertIn("staleLlamaWebUi", self.main_activity)
        self.assertIn("staleLlamaStaticPath", self.main_activity)
        self.assertIn("staleLlamaCorrectnessProbe", self.main_activity)
        self.assertIn('.pdocker-template-version").writeText("11', self.main_activity)

    def test_gpu_correctness_is_separate_from_http_health(self):
        self.assertIn("COPY scripts/pdocker-llama-correctness.sh", self.dockerfile)
        self.assertIn("/usr/local/bin/pdocker-llama-correctness", self.dockerfile)
        self.assertIn("LLAMA_CORRECTNESS_FILE", self.compose)
        self.assertIn("pdocker.llama.correctness.v1", self.correctness)
        self.assertIn('"prompt": "2+3="', self.correctness)
        self.assertIn('"expected_prefixes": ["5"]', self.correctness)
        self.assertIn("/completion", self.correctness)
        self.assertIn("benchmark_claim_allowed", self.correctness)

        readme = (LLAMA_ROOT / "README.md").read_text()
        benchmarks = (ROOT / "docs" / "test" / "LLAMA_BENCHMARKS.md").read_text()
        status = (ROOT / "docs" / "plan" / "STATUS.md").read_text()
        todo = (ROOT / "docs" / "plan" / "TODO.md").read_text()
        for text in [readme, benchmarks, status, todo]:
            with self.subTest(document=text[:40]):
                self.assertIn("pdocker-llama-correctness", text)
        self.assertIn("Server health is only a liveness check", readme)
        self.assertIn("/health` alone is only service liveness", benchmarks)

    def test_unfinished_pdocker_vulkan_keeps_kv_cache_on_cpu(self):
        self.assertIn("PDOCKER_VULKAN_ALLOW_KV_OFFLOAD", self.compose)
        self.assertIn("PDOCKER_VULKAN_ALLOW_KV_OFFLOAD", self.start)
        self.assertIn("PDOCKER_VULKAN_ICD_READY", self.start)
        self.assertIn("LLAMA_ARG_KV_OFFLOAD=0", self.start)
        self.assertIn("--no-kv-offload", self.start)
        self.assertIn("llama.cpp KV cache", self.start)

    def test_default_gpu_layers_stays_on_validated_bridge_path(self):
        self.assertIn('LLAMA_ARG_N_GPU_LAYERS: "${LLAMA_ARG_N_GPU_LAYERS:-1}"', self.compose)
        self.assertNotIn("LLAMA_ARG_N_GPU_LAYERS:-2", self.compose)

    def test_llama_template_uses_measured_pipeline_optimization_default(self):
        self.assertIn(
            'PDOCKER_GPU_DISABLE_PIPELINE_OPTIMIZATION: "${PDOCKER_GPU_DISABLE_PIPELINE_OPTIMIZATION:-0}"',
            self.compose,
        )
        self.assertIn("pdocker.llama-gpu-env-manifest: begin ui_compose_runtime_env_defaults", self.compose)
        for item in self.env_manifest["ui_compose_runtime_env_defaults"]:
            key = item["env"]
            with self.subTest(key=key):
                self.assertIn(f'{key}: "${{{key}:-{item["default"]}}}"', self.compose)
        self.assertIn("stalePipelineOptimizationDefault", self.main_activity)
        self.assertIn("staleLlamaBridgeClamps", self.main_activity)
        self.assertIn('.pdocker-template-version").writeText("11', self.main_activity)

    def test_kv_guard_does_not_patch_llama_sources_or_build_flow(self):
        forbidden = [
            "sed -i",
            "patch -p",
            "apply_patch",
            "cache_k_l35",
            "ggml-backend.cpp",
        ]
        combined = self.dockerfile + self.start + self.compose
        for marker in forbidden:
            with self.subTest(marker=marker):
                self.assertNotIn(marker, combined)


if __name__ == "__main__":
    unittest.main()

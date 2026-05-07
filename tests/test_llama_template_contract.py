import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LLAMA_ROOT = ROOT / "app" / "src" / "main" / "assets" / "project-library" / "llama-cpp-gpu"
LLAMA_DOCKERFILE = LLAMA_ROOT / "Dockerfile"
LLAMA_COMPOSE = LLAMA_ROOT / "compose.yaml"
LLAMA_START = LLAMA_ROOT / "scripts" / "start-llama-server.sh"


class LlamaTemplateContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dockerfile = LLAMA_DOCKERFILE.read_text()
        cls.compose = LLAMA_COMPOSE.read_text()
        cls.start = LLAMA_START.read_text()

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

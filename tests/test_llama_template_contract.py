import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LLAMA_DOCKERFILE = ROOT / "app" / "src" / "main" / "assets" / "project-library" / "llama-cpp-gpu" / "Dockerfile"


class LlamaTemplateContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dockerfile = LLAMA_DOCKERFILE.read_text()

    def test_openblas_uses_standard_cmake_detection(self):
        self.assertIn("-DGGML_BLAS=ON", self.dockerfile)
        self.assertIn("-DGGML_BLAS_VENDOR=OpenBLAS", self.dockerfile)
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


if __name__ == "__main__":
    unittest.main()

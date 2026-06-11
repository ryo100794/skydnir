import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
DIRECT_EXEC = ROOT / "app" / "src" / "main" / "cpp" / "pdocker_direct_exec.c"
COPY_NATIVE = ROOT / "scripts" / "copy-native.sh"
RUNTIME = ROOT / "app" / "src" / "main" / "kotlin" / "io" / "github" / "ryo100794" / "pdocker" / "PdockerdRuntime.kt"


class DirectExecLoaderContractTest(unittest.TestCase):
    def test_rootfs_loader_is_default_and_helper_loader_is_explicit_opt_in(self):
        src = DIRECT_EXEC.read_text(encoding="utf-8")
        rootfs_block = src.index('const char *ld_candidates[] = {')
        helper_gate = src.index('SKYDNIR_DIRECT_ALLOW_HELPER_LOADER')
        helper_block = src.index('const char *helper_ld_candidates[] = {')
        self.assertLess(rootfs_block, helper_gate)
        self.assertLess(helper_gate, helper_block)
        self.assertIn('if (!loader && allow_helper_loader && strcmp(allow_helper_loader, "1") == 0', src)
        self.assertIn('if (!allow_helper_loader) allow_helper_loader = getenv("PDOCKER_DIRECT_ALLOW_HELPER_LOADER")', src)
        self.assertIn('"lib/aarch64-linux-gnu/ld-linux-aarch64.so.1"', src)
        self.assertIn('"libpdocker-ld-linux-aarch64.so"', src)

    def test_packaged_glibc_loader_is_absent_unless_explicitly_requested(self):
        copy_native = COPY_NATIVE.read_text(encoding="utf-8")
        runtime = RUNTIME.read_text(encoding="utf-8")
        self.assertIn('if [[ -n "${PDOCKER_GLIBC_LOADER:-}"', copy_native)
        self.assertIn('rm -f "$JNI_DIR/libpdocker-ld-linux-aarch64.so"', copy_native)
        self.assertIn('optionalLinkTo(File(nativeDir, "libpdocker-ld-linux-aarch64.so"), File(dockerBin, "pdocker-ld-linux-aarch64"))', runtime)


if __name__ == "__main__":
    unittest.main()

import importlib.machinery
import importlib.util
import os
import tempfile
import unittest
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PDOCKERD = ROOT / "docker-proot-setup" / "bin" / "pdockerd"
def load_pdockerd(home: Path):
    module_name = f"pdockerd_run_changed_paths_{uuid.uuid4().hex}"
    old_env = os.environ.copy()
    os.environ.update(
        {
            "PDOCKER_HOME": str(home),
            "PDOCKER_TMP_DIR": str(home / "tmp"),
            "PDOCKER_RUNTIME_BACKEND": "no-proot",
        }
    )
    try:
        loader = importlib.machinery.SourceFileLoader(module_name, str(PDOCKERD))
        spec = importlib.util.spec_from_loader(module_name, loader)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        return module
    finally:
        os.environ.clear()
        os.environ.update(old_env)


class DockerfileRunChangedPathManifestTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.mod = load_pdockerd(self.root / "home")
        self.rootfs = self.root / "rootfs"
        bin_dir = self.rootfs / "usr" / "local" / "bin"
        bin_dir.mkdir(parents=True)
        for name in ("pdocker-a", "pdocker-b", "other"):
            (bin_dir / name).write_text(name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_chmod_wildcard_run_expands_to_existing_rootfs_paths(self):
        changed = self.mod._dockerfile_run_changed_paths(
            "chmod +x /usr/local/bin/pdocker-*",
            rootfs=str(self.rootfs),
        )

        self.assertEqual(changed, ["usr/local/bin/pdocker-a", "usr/local/bin/pdocker-b"])

    def test_chmod_literal_run_stays_path_scoped_without_rootfs(self):
        changed = self.mod._dockerfile_run_changed_paths("chmod 0755 /usr/local/bin/pdocker-a")

        self.assertEqual(changed, ["usr/local/bin/pdocker-a"])

    def test_unsafe_or_unmatched_runs_fall_back_to_full_snapshot(self):
        cases = [
            "chmod +x ./relative",
            "chmod +x /usr/local/bin/$TOOL",
            "chmod +x /usr/local/bin/missing-*",
            "touch /usr/local/bin/pdocker-a",
        ]
        for command in cases:
            with self.subTest(command=command):
                self.assertIsNone(
                    self.mod._dockerfile_run_changed_paths(command, rootfs=str(self.rootfs))
                )

    def test_pdockerd_has_changed_path_helper_wired_to_run_snapshot(self):
        source = PDOCKERD.read_text()
        for marker in [
            "def _dockerfile_run_changed_paths(command, rootfs=None):",
            "glob.glob(pattern)",
            "changed_paths=_dockerfile_run_changed_paths(args, rootfs=rootfs)",
        ]:
            self.assertIn(marker, source)


if __name__ == "__main__":
    unittest.main()

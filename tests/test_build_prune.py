import importlib.machinery
import importlib.util
import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
PDOCKERD = ROOT / "docker-proot-setup" / "bin" / "pdockerd"


def load_pdockerd(home):
    module_name = f"pdockerd_build_prune_{uuid.uuid4().hex}"
    loader = importlib.machinery.SourceFileLoader(module_name, str(PDOCKERD))
    spec = importlib.util.spec_from_loader(module_name, loader)
    module = importlib.util.module_from_spec(spec)
    env = {
        "PDOCKER_HOME": str(home),
        "PDOCKER_TMP_DIR": str(home / "tmp"),
        "PDOCKER_RUNTIME_BACKEND": "direct",
        "PDOCKER_DIRECT_EXECUTOR": "",
    }
    with mock.patch.dict(os.environ, env, clear=False):
        loader.exec_module(module)
    return module


class BuildPruneTest(unittest.TestCase):
    def test_prune_removes_stale_cache_metadata_and_known_temp_artifacts(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            mod = load_pdockerd(root / "pdocker")
            layer_id = "a" * 64
            stale_id = "b" * 64
            layer_tree = Path(mod.LAYERS_DIR) / layer_id / "tree"
            layer_tree.mkdir(parents=True)
            (layer_tree.parent / "meta.json").write_text(json.dumps({"size": 0}))
            cache_dir = Path(mod.BUILD_CACHE_DIR)
            cache_dir.mkdir(parents=True, exist_ok=True)
            (cache_dir / "valid.json").write_text(json.dumps({"diff_id": f"sha256:{layer_id}"}))
            (cache_dir / "stale.json").write_text(json.dumps({"diff_id": f"sha256:{stale_id}"}))
            (cache_dir / "invalid.json").write_text("{")
            tmp_dir = Path(mod.PDOCKER_TMP)
            tmp_dir.mkdir(parents=True, exist_ok=True)
            (tmp_dir / "pdarchiveput_leftover").write_text("remove")
            (tmp_dir / "llama-build.out").write_text("keep")

            removed = mod.prune_build_artifacts()

            self.assertIn("build-cache-stale:stale.json", removed)
            self.assertIn("build-cache-unreadable:invalid.json", removed)
            self.assertIn("tmp:pdarchiveput_leftover", removed)
            self.assertTrue((cache_dir / "valid.json").exists())
            self.assertFalse((cache_dir / "stale.json").exists())
            self.assertFalse((cache_dir / "invalid.json").exists())
            self.assertFalse((tmp_dir / "pdarchiveput_leftover").exists())
            self.assertTrue((tmp_dir / "llama-build.out").exists())


if __name__ == "__main__":
    unittest.main()

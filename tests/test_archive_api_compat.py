import importlib.machinery
import importlib.util
import io
import json
import os
import stat
import tarfile
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
PDOCKERD = ROOT / "docker-proot-setup" / "bin" / "pdockerd"


def load_pdockerd(home):
    module_name = f"pdockerd_archive_compat_{uuid.uuid4().hex}"
    loader = importlib.machinery.SourceFileLoader(module_name, str(PDOCKERD))
    spec = importlib.util.spec_from_loader(module_name, loader)
    module = importlib.util.module_from_spec(spec)
    env = {
        "PDOCKER_HOME": str(home),
        "PDOCKER_TMP_DIR": str(home / "tmp"),
        "PDOCKER_RUNTIME_BACKEND": "direct",
        "PDOCKER_DIRECT_EXECUTOR": "",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    with mock.patch.dict(os.environ, env, clear=False):
        loader.exec_module(module)
    return module


def make_tar(path, entries):
    with tarfile.open(path, "w") as tf:
        for name, data, mode, mtime in entries:
            info = tarfile.TarInfo(name)
            info.mode = mode
            info.mtime = mtime
            if data is None:
                info.type = tarfile.DIRTYPE
                tf.addfile(info)
            else:
                raw = data.encode()
                info.size = len(raw)
                tf.addfile(info, io.BytesIO(raw))


class ArchiveApiCompatibilityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.mod = load_pdockerd(self.root / "home")

    def tearDown(self):
        self.tmp.cleanup()

    def _cow_state(self):
        cid = "archivecow"
        cdir = Path(self.mod.CONTAINERS_DIR) / cid
        lower = cdir / "lower"
        upper = cdir / "upper"
        lower.mkdir(parents=True)
        upper.mkdir(parents=True)
        return {
            "Id": cid,
            "Name": "/archivecow",
            "Storage": {
                "Mode": "cow_bind",
                "LowerDir": str(lower),
                "UpperDir": str(upper),
            },
            "Config": {"Env": []},
            "State": {"Running": False},
        }, cdir, lower, upper

    def test_archive_get_merges_lower_upper_and_hides_whiteouts(self):
        state, cdir, lower, upper = self._cow_state()
        (lower / "work").mkdir()
        (lower / "work" / "lower.txt").write_text("lower")
        (lower / "work" / "replaced.txt").write_text("old")
        (lower / "work" / "deleted.txt").write_text("delete-me")
        (upper / "work").mkdir()
        (upper / "work" / "upper.txt").write_text("upper")
        (upper / "work" / "replaced.txt").write_text("new")
        (upper / "work" / ".wh.deleted.txt").write_text("")

        out = io.BytesIO()
        self.mod.write_container_path_archive(out, state, str(cdir), "/work")
        out.seek(0)

        with tarfile.open(fileobj=out, mode="r:*") as tf:
            names = sorted(member.name for member in tf.getmembers())
            contents = {
                name: tf.extractfile(name).read().decode()
                for name in names
                if tf.getmember(name).isfile()
            }

        self.assertIn("work/lower.txt", names)
        self.assertIn("work/upper.txt", names)
        self.assertEqual(contents["work/replaced.txt"], "new")
        self.assertNotIn("work/deleted.txt", names)
        self.assertFalse(any("/.wh." in name or name.startswith(".wh.") for name in names))

    def test_archive_head_stat_and_get_do_not_leak_whiteouted_lower_path(self):
        state, cdir, lower, upper = self._cow_state()
        (lower / "gone.txt").write_text("lower")
        (upper / ".wh.gone.txt").write_text("")

        stat_info = self.mod._container_archive_stat(state, str(cdir), "/gone.txt")
        self.assertIsNone(stat_info)
        with self.assertRaises(FileNotFoundError):
            self.mod.write_container_path_archive(io.BytesIO(), state, str(cdir), "/gone.txt")

    def test_archive_put_rejects_path_traversal_and_writes_nothing_outside(self):
        dest = self.root / "dest"
        dest.mkdir()
        outside = self.root / "escape.txt"
        tar_path = self.root / "evil.tar"
        make_tar(tar_path, [("../escape.txt", "owned", 0o644, 1_700_000_000)])

        with self.assertRaises(self.mod.BadRequest):
            self.mod.safe_extract_container_archive(tar_path, dest)

        self.assertFalse(outside.exists())
        self.assertEqual(list(dest.iterdir()), [])

    def test_archive_put_rejects_absolute_paths_and_reserved_whiteouts(self):
        dest = self.root / "dest"
        dest.mkdir()
        absolute_tar = self.root / "absolute.tar"
        whiteout_tar = self.root / "whiteout.tar"
        mixed_tar = self.root / "mixed.tar"
        make_tar(absolute_tar, [("/tmp/escape.txt", "owned", 0o644, 1_700_000_000)])
        make_tar(whiteout_tar, [(".wh.victim", "", 0o644, 1_700_000_000)])
        make_tar(mixed_tar, [
            ("restored", "ok", 0o644, 1_700_000_000),
            ("../escape", "bad", 0o644, 1_700_000_000),
        ])
        (dest / ".wh.restored").write_text("")

        with self.assertRaises(self.mod.BadRequest):
            self.mod.safe_extract_container_archive(absolute_tar, dest)
        with self.assertRaises(self.mod.BadRequest):
            self.mod.safe_extract_container_archive(whiteout_tar, dest)
        with self.assertRaises(self.mod.BadRequest):
            self.mod.safe_extract_container_archive(mixed_tar, dest)

        self.assertEqual([p.name for p in dest.iterdir()], [".wh.restored"])

    def test_archive_put_preserves_mode_mtime_and_replaces_upper_whiteout(self):
        state, cdir, lower, upper = self._cow_state()
        (lower / "target").mkdir()
        target = self.mod._container_host_path(state, str(cdir), "/target", for_write=True)
        Path(target, ".wh.restored.sh").write_text("")
        tar_path = self.root / "metadata.tar"
        make_tar(tar_path, [("restored.sh", "#!/bin/sh\n", 0o754, 1_700_001_234)])

        self.mod.safe_extract_container_archive(tar_path, target)

        restored = Path(target) / "restored.sh"
        self.assertTrue(restored.exists())
        self.assertFalse((Path(target) / ".wh.restored.sh").exists())
        st = restored.stat()
        self.assertEqual(stat.S_IMODE(st.st_mode), 0o754)
        self.assertEqual(int(st.st_mtime), 1_700_001_234)

    def test_container_archive_path_resolution_rejects_query_traversal(self):
        state, cdir, lower, upper = self._cow_state()
        (lower / "ok").write_text("ok")

        self.assertIsNone(self.mod._container_host_path(state, str(cdir), "../../host", for_write=False))
        rel, source = self.mod._container_archive_source(state, str(cdir), "../../host")
        self.assertIsNone(rel)
        self.assertIsNone(source)


if __name__ == "__main__":
    unittest.main()

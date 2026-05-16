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


def make_custom_tar(path, members):
    with tarfile.open(path, "w", format=tarfile.PAX_FORMAT) as tf:
        for info, data in members:
            if data is None:
                tf.addfile(info)
            else:
                raw = data.encode() if isinstance(data, str) else data
                info.size = len(raw)
                tf.addfile(info, io.BytesIO(raw))


def xattrs_supported(path):
    if not all(hasattr(os, name) for name in ("setxattr", "getxattr")):
        return False
    probe = path / "xattr-probe"
    probe.write_text("probe")
    try:
        os.setxattr(probe, "user.pdocker_archive_probe", b"ok", follow_symlinks=False)
        return os.getxattr(probe, "user.pdocker_archive_probe", follow_symlinks=False) == b"ok"
    except OSError:
        return False


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

    def test_archive_get_preserves_hardlinks_symlinks_uid_gid_and_xattrs_where_supported(self):
        state, cdir, lower, upper = self._cow_state()
        work = upper / "work"
        work.mkdir()
        original = work / "original.txt"
        twin = work / "twin.txt"
        original.write_text("same inode")
        os.link(original, twin)
        os.symlink("original.txt", work / "link.txt")
        if xattrs_supported(self.root):
            os.setxattr(original, "user.pdocker_archive", b"value", follow_symlinks=False)

        out = io.BytesIO()
        self.mod.write_container_path_archive(out, state, str(cdir), "/work")
        out.seek(0)
        with tarfile.open(fileobj=out, mode="r:*") as tf:
            members = {m.name: m for m in tf.getmembers()}
        hardlink_members = [members["work/original.txt"], members["work/twin.txt"]]
        self.assertTrue(any(m.islnk() for m in hardlink_members))
        self.assertEqual(members["work/link.txt"].linkname, "original.txt")
        self.assertEqual(members["work/original.txt"].uid, original.lstat().st_uid)
        self.assertEqual(members["work/original.txt"].gid, original.lstat().st_gid)
        if xattrs_supported(self.root):
            self.assertEqual(members["work/original.txt"].pax_headers.get("SCHILY.xattr.user.pdocker_archive"), "value")

    def test_archive_get_preserves_cow_lower_hardlinks_across_merged_tree(self):
        state, cdir, lower, upper = self._cow_state()
        (lower / "work" / "sub").mkdir(parents=True)
        (upper / "work").mkdir()
        original = lower / "work" / "original.txt"
        twin = lower / "work" / "sub" / "twin.txt"
        original.write_text("lower shared inode")
        os.link(original, twin)
        (upper / "work" / "upper.txt").write_text("upper still merges")

        self.assertEqual(original.stat().st_ino, twin.stat().st_ino)
        self.assertGreaterEqual(original.stat().st_nlink, 2)

        out = io.BytesIO()
        self.mod.write_container_path_archive(out, state, str(cdir), "/work")
        out.seek(0)

        with tarfile.open(fileobj=out, mode="r:*") as tf:
            members = {m.name: m for m in tf.getmembers()}
            contents = {
                name: tf.extractfile(member).read().decode()
                for name, member in members.items()
                if member.isfile()
            }

        self.assertIn("work/upper.txt", members)
        self.assertEqual(contents["work/original.txt"], "lower shared inode")
        hardlink = members["work/sub/twin.txt"]
        self.assertTrue(hardlink.islnk())
        self.assertEqual(hardlink.linkname, "work/original.txt")

    def test_archive_get_whiteouted_hardlink_source_emits_remaining_peer_as_file(self):
        state, cdir, lower, upper = self._cow_state()
        (lower / "work").mkdir()
        (upper / "work").mkdir()
        hidden = lower / "work" / "hidden.txt"
        visible = lower / "work" / "visible.txt"
        hidden.write_text("payload survives through visible peer")
        os.link(hidden, visible)
        (upper / "work" / ".wh.hidden.txt").write_text("")

        out = io.BytesIO()
        self.mod.write_container_path_archive(out, state, str(cdir), "/work")
        out.seek(0)

        with tarfile.open(fileobj=out, mode="r:*") as tf:
            members = {m.name: m for m in tf.getmembers()}
            visible_member = members["work/visible.txt"]
            visible_data = tf.extractfile(visible_member).read().decode()

        self.assertNotIn("work/hidden.txt", members)
        self.assertTrue(visible_member.isfile())
        self.assertFalse(visible_member.islnk())
        self.assertEqual(visible_data, "payload survives through visible peer")

    def test_archive_put_link_policy_ownership_xattrs_and_chunked_reader(self):
        dest = self.root / "dest"
        dest.mkdir()
        good_tar = self.root / "good-links.tar"
        base = tarfile.TarInfo("base.txt")
        base.uid = 12345
        base.gid = 23456
        base.pax_headers = {"SCHILY.xattr.user.pdocker_archive": "restored"}
        hard = tarfile.TarInfo("hard.txt")
        hard.type = tarfile.LNKTYPE
        hard.linkname = "base.txt"
        sym = tarfile.TarInfo("sym.txt")
        sym.type = tarfile.SYMTYPE
        sym.linkname = "base.txt"
        make_custom_tar(good_tar, [(base, "payload"), (hard, None), (sym, None)])

        self.mod.safe_extract_container_archive(good_tar, dest)
        self.assertEqual((dest / "base.txt").stat().st_ino, (dest / "hard.txt").stat().st_ino)
        self.assertEqual(os.readlink(dest / "sym.txt"), "base.txt")
        self.assertNotEqual((dest / "base.txt").stat().st_uid, 12345)
        self.assertNotEqual((dest / "base.txt").stat().st_gid, 23456)
        if xattrs_supported(self.root):
            self.assertEqual(os.getxattr(dest / "base.txt", "user.pdocker_archive", follow_symlinks=False), b"restored")

        for label, typ, linkname in (
            ("absolute-symlink", tarfile.SYMTYPE, "/etc/passwd"),
            ("escaping-symlink", tarfile.SYMTYPE, "../outside"),
            ("escaping-hardlink", tarfile.LNKTYPE, "../outside"),
        ):
            tar_path = self.root / f"{label}.tar"
            bad = tarfile.TarInfo(f"{label}.txt")
            bad.type = typ
            bad.linkname = linkname
            (dest / f".wh.{label}.txt").write_text("")
            make_custom_tar(tar_path, [(bad, None)])
            with self.assertRaises(self.mod.BadRequest):
                self.mod.safe_extract_container_archive(tar_path, dest)
            self.assertTrue((dest / f".wh.{label}.txt").exists())

        handler = object.__new__(self.mod.DockerAPIHandler)
        handler.headers = {"Transfer-Encoding": "chunked"}
        handler.rfile = io.BytesIO(b"5\r\nhello\r\n7;ignored=true\r\n world!\r\n0\r\n\r\n")
        path = handler._read_body_to_temp(prefix="pdarchiveput_chunked_test_")
        try:
            self.assertEqual(Path(path).read_bytes(), b"hello world!")
            self.assertEqual(handler._request_temp_body_bytes, len(b"hello world!"))
        finally:
            os.remove(path)

    def test_container_archive_path_resolution_rejects_query_traversal(self):
        state, cdir, lower, upper = self._cow_state()
        (lower / "ok").write_text("ok")

        self.assertIsNone(self.mod._container_host_path(state, str(cdir), "../../host", for_write=False))
        rel, source = self.mod._container_archive_source(state, str(cdir), "../../host")
        self.assertIsNone(rel)
        self.assertIsNone(source)


if __name__ == "__main__":
    unittest.main()

import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PDOCKERD = ROOT / "docker-proot-setup" / "bin" / "pdockerd"
ASSET_PDOCKERD = ROOT / "app" / "src" / "main" / "assets" / "pdockerd" / "pdockerd"
COMPARE = ROOT / "scripts" / "android-llama-gpu-compare.sh"


class LlamaGpuCreateContractTest(unittest.TestCase):
    def _daemon(self):
        return PDOCKERD.read_text(encoding="utf-8")

    def _compare(self):
        return COMPARE.read_text(encoding="utf-8")

    def test_packaged_daemon_matches_source_daemon_when_asset_exists(self):
        if not ASSET_PDOCKERD.exists():
            self.skipTest("packaged pdockerd asset is generated/ignored in this checkout")
        self.assertEqual(
            PDOCKERD.read_bytes(),
            ASSET_PDOCKERD.read_bytes(),
            "APK asset pdockerd must be synced with docker-proot-setup/bin/pdockerd when present",
        )

    def test_create_state_is_inspectable_before_rootfs_materialization(self):
        source = self._daemon()
        body = source.split("def create_container(config, name=None):", 1)[1].split("def save_container_state", 1)[0]
        self.assertIn('"Status": "creating"', body)
        self.assertIn('"Phase": "rootfs-materialize"', body)
        self.assertLess(
            body.index("save_container_state(cid, provisional_state)"),
            body.index("materialize_container_rootfs(dst_rootfs, diff_ids)"),
        )
        self.assertLess(
            body.index("save_container_state(cid, provisional_state)"),
            body.index("clone_image_rootfs(img_rootfs, dst_rootfs)"),
        )

    def test_create_failure_is_persisted_before_returning_error(self):
        source = self._daemon()
        self.assertIn('create_info["Phase"] = "failed"', source)
        self.assertIn('failed_state["Dead"] = True', source)
        self.assertIn('save_container_state(cid, failed)', source)
        self.assertIn('except RuntimeError as e:', source)
        self.assertIn('self._send_json(500, {"message": str(e)})', source)

    def test_start_refuses_partial_creating_container(self):
        source = self._daemon()
        self.assertIn('if (state.get("State") or {}).get("Status") == "creating":', source)
        self.assertIn('is still being created', source)
        self.assertIn('elif status == "creating":\n            docker_status = "Creating"', source)

    def test_compare_cleanup_covers_default_legacy_container_alias(self):
        script = self._compare()
        self.assertIn("CONTAINER_EXPLICIT=0", script)
        self.assertIn('LEGACY_CONTAINER_NAMES="${PDOCKER_LLAMA_LEGACY_CONTAINERS:-pdocker-llama-cpp}"', script)
        self.assertIn("target_container_names()", script)
        self.assertGreaterEqual(script.count("done < <(target_container_names)"), 3)

    def test_compare_waits_for_create_completion_not_just_visibility(self):
        script = self._compare()
        self.assertIn("container_status_from_body()", script)
        self.assertNotIn("python3 - <<'PYSTATUS'", script)
        self.assertIn("delayed create is inspectable but still creating", script)
        self.assertIn("waiting for delayed create completion", script)
        self.assertIn("refusing to start partial container", script)
        self.assertIn("create finalized in non-startable state", script)


if __name__ == "__main__":
    unittest.main()

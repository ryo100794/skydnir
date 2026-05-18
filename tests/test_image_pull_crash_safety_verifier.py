import json
import importlib.machinery
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERIFY = ROOT / "scripts" / "verify-image-pull-crash-safety.py"
RUNNER = ROOT / "scripts" / "verify" / "runner" / "image_pull_crash_safety_device.py"
DEVICE_RUNNER = ROOT / "scripts" / "verify" / "runner" / "image-pull-crash-safety-device.sh"
PDOCKERD = ROOT / "docker-proot-setup" / "bin" / "pdockerd"


def load_pdockerd(home: Path, tmp: Path):
    old_home = os.environ.get("PDOCKER_HOME")
    old_tmp = os.environ.get("PDOCKER_TMP_DIR")
    os.environ["PDOCKER_HOME"] = str(home)
    os.environ["PDOCKER_TMP_DIR"] = str(tmp)
    try:
        name = f"pdockerd_crash_safety_{os.getpid()}_{len(sys.modules)}"
        loader = importlib.machinery.SourceFileLoader(name, str(PDOCKERD))
        spec = importlib.util.spec_from_loader(name, loader)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        return module
    finally:
        if old_home is None:
            os.environ.pop("PDOCKER_HOME", None)
        else:
            os.environ["PDOCKER_HOME"] = old_home
        if old_tmp is None:
            os.environ.pop("PDOCKER_TMP_DIR", None)
        else:
            os.environ["PDOCKER_TMP_DIR"] = old_tmp


def load_runner_module():
    name = f"image_pull_crash_safety_runner_{os.getpid()}_{len(sys.modules)}"
    loader = importlib.machinery.SourceFileLoader(name, str(RUNNER))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class ImagePullCrashSafetyVerifierTest(unittest.TestCase):
    def test_static_verifier_passes(self):
        subprocess.run([sys.executable, str(VERIFY)], cwd=ROOT, check=True)

    def test_device_runner_writes_planned_gap_without_adb(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "artifact.json"
            subprocess.run(
                [
                    sys.executable,
                    str(RUNNER),
                    "--adb",
                    "__missing_adb_for_unit_test__",
                    "--artifact",
                    str(artifact),
                ],
                cwd=ROOT,
                check=True,
            )
            data = json.loads(artifact.read_text())

        self.assertEqual(data["scenario_id"], "image.pull.interrupted-kill-restart")
        self.assertEqual(data["schema_version"], 2)
        self.assertEqual(data["status"], "planned-gap")
        self.assertFalse(data["success"])
        self.assertIn("artifact_schema", data)
        self.assertEqual(data["phases"], ["prepare-residue", "kill-daemon", "restart-and-probe", "cleanup"])
        self.assertFalse(data["coverage"]["live_interrupted_network_pull"])
        self.assertEqual(data["live_pull_interruption"]["phase"], "timed-live-pull-interruption")
        self.assertEqual(data["live_pull_interruption"]["status"], "planned-gap")
        self.assertFalse(data["live_pull_interruption"]["success"])
        self.assertFalse(data["live_pull_interruption"]["runnable"])
        self.assertFalse(data["live_pull_interruption"]["live_image_safe"])
        self.assertIn("safe_image_requirements", data["live_pull_interruption"])
        self.assertFalse(data["inputs"]["live_image_safe"])
        self.assertIn("--execute-live-pull-interruption", data["live_pull_interruption"]["required_cli"])
        self.assertIn("--live-fixture-owned", data["live_pull_interruption"]["required_cli"])
        self.assertTrue(any("--live-image" in item for item in data["live_pull_interruption"]["required_cli"]))
        self.assertIn("remaining_gap", data)
        self.assertIn("negative_expected_conditions", data)
        self.assertIn("cleanup_policy", data)
        self.assertIn("partial_image_inspect_after_restart", data["evidence"])
        self.assertIn("partial_image_create_after_restart", data["evidence"])
        self.assertIn("partial_image_inspect_rejected", data["assertions"])
        self.assertIn("partial_image_create_rejected", data["assertions"])
        self.assertIn("no_partial_or_corrupt_image_cache_survivors", data["assertions"])
        self.assertIn("post_restart_survivors", data["evidence"])
        self.assertEqual(data["evidence"]["post_restart_survivors"], [])
        self.assertGreaterEqual(len(data["commands"]), 8)
        joined_negative = "\n".join(data["negative_expected_conditions"])
        self.assertIn(".pull-", joined_negative)
        self.assertIn(".tmp-", joined_negative)
        self.assertIn("old tag", joined_negative)
        self.assertIn("partial image", joined_negative)

    def test_device_runner_execute_without_device_is_blocked_not_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "artifact.json"
            result = subprocess.run(
                [
                    sys.executable,
                    str(RUNNER),
                    "--adb",
                    "__missing_adb_for_unit_test__",
                    "--artifact",
                    str(artifact),
                    "--execute-device",
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            data = json.loads(artifact.read_text())

        self.assertEqual(result.returncode, 2)
        self.assertEqual(data["status"], "blocked")
        self.assertFalse(data["success"])
        self.assertEqual(data["phase_results"], [])
        self.assertIsNone(data["assertions"]["old_tag_restored"])

    def test_live_pull_interruption_opt_in_is_ready_but_non_promoting_without_device_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "artifact.json"
            result = subprocess.run(
                [
                    sys.executable,
                    str(RUNNER),
                    "--adb",
                    "__missing_adb_for_unit_test__",
                    "--artifact",
                    str(artifact),
                    "--execute-live-pull-interruption",
                    "--live-image",
                    "127.0.0.1:5000/pdocker-crash-safety-fixture:test",
                    "--live-fixture-owned",
                    "--live-interrupt-after-seconds",
                    "1.5",
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            data = json.loads(artifact.read_text())

        self.assertIn("status=planned-gap", result.stdout)
        self.assertEqual(data["status"], "planned-gap")
        self.assertFalse(data["success"])
        self.assertFalse(data["coverage"]["live_interrupted_network_pull"])
        self.assertFalse(data["coverage"]["timed_live_interruption_artifact"])
        live = data["live_pull_interruption"]
        self.assertTrue(live["requested"])
        self.assertEqual(live["live_image"], "127.0.0.1:5000/pdocker-crash-safety-fixture:test")
        self.assertTrue(live["live_image_safe"])
        self.assertEqual(live["live_image_safety_reason"], "isolated local fixture registry")
        self.assertTrue(data["inputs"]["live_image_safe"])
        self.assertTrue(live["fixture_owned_or_isolated"])
        self.assertEqual(live["interrupt_after_seconds"], 1.5)
        self.assertTrue(live["runnable"])
        self.assertFalse(live["success"])
        self.assertEqual(live["status"], "ready")
        self.assertIsNone(live["blocked_reason"])

    def test_unsafe_live_pull_reference_stays_non_promoting(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "artifact.json"
            subprocess.run(
                [
                    sys.executable,
                    str(RUNNER),
                    "--adb",
                    "__missing_adb_for_unit_test__",
                    "--artifact",
                    str(artifact),
                    "--execute-live-pull-interruption",
                    "--live-image",
                    "ubuntu:latest",
                    "--live-fixture-owned",
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            data = json.loads(artifact.read_text())

        self.assertEqual(data["status"], "planned-gap")
        self.assertFalse(data["success"])
        self.assertFalse(data["coverage"]["live_interrupted_network_pull"])
        self.assertFalse(data["coverage"]["timed_live_interruption_artifact"])
        live = data["live_pull_interruption"]
        self.assertTrue(live["requested"])
        self.assertFalse(live["live_image_safe"])
        self.assertIn("public", live["live_image_safety_reason"])
        self.assertIn("safe scenario-owned --live-image", live["blocked_reason"])
        self.assertFalse(data["inputs"]["live_image_safe"])

    def test_device_side_runner_is_scenario_scoped(self):
        text = DEVICE_RUNNER.read_text()
        for marker in ["prepare-residue", "kill-daemon", "restart-and-probe", "cleanup"]:
            self.assertIn(marker, text)
        for marker in [".pull-$TOKEN", ".old-$TOKEN", ".tmp-$TOKEN", "inspect-restored.raw", "inspect-never.raw"]:
            self.assertIn(marker, text)
        for marker in ["$PARTIAL_BASE", "inspect-partial.raw", "create-partial.raw", "partial_image_create_rejected"]:
            self.assertIn(marker, text)
        for marker in ["timed-live-pull-interruption", "live-pull.raw", "live-pull-summary.json", "live-store-after-restart.txt"]:
            self.assertIn(marker, text)
        self.assertIn("pkill -TERM -f pdockerd", text)
        self.assertIn("rm -rf \\", text)
        self.assertIn("$IMG_BASE", text)
        self.assertIn("$NEVER_BASE", text)
        self.assertIn("$TOKEN", text)
        for forbidden in [
            "rm -rf files/pdocker",
            "rm -rf pdocker/images",
            "rm -rf pdocker/layers",
            "rm -rf /data",
            "rm -rf /sdcard",
        ]:
            self.assertNotIn(forbidden, text)

    def _write_passing_device_evidence(self, local_dir: Path, store_after: str) -> None:
        token = "unit"
        tmp_layer = "1" * 64
        partial_layer = "2" * 64
        context = {
            "token": token,
            "image_base": "docker.io_library_pdocker-crash-safety-probe_unit",
            "never_base": "docker.io_library_pdocker-crash-safety-never_unit",
            "partial_base": "docker.io_library_pdocker-crash-safety-partial_unit",
            "tmp_layer": tmp_layer,
            "partial_layer": partial_layer,
        }
        restart = {
            "old_tag_restored": True,
            "pull_stage_pruned": True,
            "tmp_layer_pruned": True,
            "partial_layer_pruned": True,
            "partial_image_pruned_or_rejected": True,
            "partial_image_inspect_rejected": True,
            "partial_image_create_rejected": True,
            "never_published_tag_rejected": True,
            "restored_tag_inspectable": True,
            "daemon_restarted": True,
        }
        cleanup = {"cleanup_removed_only_scenario_owned_paths": True}
        (local_dir / "context.json").write_text(json.dumps(context))
        (local_dir / "restart-summary.json").write_text(json.dumps(restart))
        (local_dir / "cleanup-summary.json").write_text(json.dumps(cleanup))
        (local_dir / "store-after-restart.txt").write_text(store_after)

    def test_device_evidence_evaluator_fails_on_interrupted_pull_or_cache_survivors(self):
        runner = load_runner_module()
        tmp_layer = "1" * 64
        partial_layer = "2" * 64
        with tempfile.TemporaryDirectory() as tmp:
            local_dir = Path(tmp)
            self._write_passing_device_evidence(
                local_dir,
                "\n".join([
                    "# images",
                    "pdocker/images/docker.io_library_pdocker-crash-safety-probe_unit.pull-unit",
                    "pdocker/images/docker.io_library_pdocker-crash-safety-partial_unit",
                    "# layers",
                    f"pdocker/layers/{tmp_layer}.tmp-unit",
                    f"pdocker/layers/{partial_layer}",
                    "",
                ]),
            )
            assertions, failures, evidence = runner.evaluate_device_evidence(local_dir)

        self.assertFalse(assertions["no_partial_or_corrupt_image_cache_survivors"])
        self.assertIn("no_partial_or_corrupt_image_cache_survivors", failures)
        joined = "\n".join(evidence["post_restart_survivors"])
        self.assertIn(".pull-unit", joined)
        self.assertIn(".tmp-unit", joined)
        self.assertIn(partial_layer, joined)

    def test_device_evidence_evaluator_passes_when_post_restart_listing_is_clean(self):
        runner = load_runner_module()
        valid_layer = "3" * 64
        with tempfile.TemporaryDirectory() as tmp:
            local_dir = Path(tmp)
            self._write_passing_device_evidence(
                local_dir,
                "\n".join([
                    "# images",
                    "pdocker/images/docker.io_library_pdocker-crash-safety-probe_unit",
                    "# layers",
                    f"pdocker/layers/{valid_layer}",
                    "",
                ]),
            )
            assertions, failures, evidence = runner.evaluate_device_evidence(local_dir)

        self.assertTrue(assertions["no_partial_or_corrupt_image_cache_survivors"])
        self.assertEqual(failures, [])
        self.assertEqual(evidence["post_restart_survivors"], [])

    def test_live_pull_evidence_evaluator_requires_no_partial_publication(self):
        runner = load_runner_module()
        with tempfile.TemporaryDirectory() as tmp:
            local_dir = Path(tmp)
            (local_dir / "live-pull-summary.json").write_text(json.dumps({
                "success": False,
                "pull_started_before_kill": True,
                "daemon_killed": True,
                "daemon_restarted": True,
                "partial_tag_not_published": False,
                "pull_stage_pruned": True,
                "tmp_layers_pruned": False,
            }))
            (local_dir / "live-pull.raw").write_text('{"status":"Pulling"}\n')
            assertions, failures, evidence, summary = runner.evaluate_live_pull_evidence(local_dir)

        self.assertTrue(assertions["live_pull_started_before_kill"])
        self.assertFalse(assertions["live_partial_tag_not_published"])
        self.assertFalse(assertions["live_tmp_layers_pruned"])
        self.assertIn("live_partial_tag_not_published", failures)
        self.assertIn("live_tmp_layers_pruned", failures)
        self.assertEqual(summary["success"], False)
        self.assertIn("live_pull_output", evidence)

    def test_prune_build_artifacts_removes_interrupted_cache_residue_and_restores_old_tag(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as tmp:
            pdockerd = load_pdockerd(Path(home), Path(tmp))
            images = Path(pdockerd.IMAGES_DIR)
            layers = Path(pdockerd.LAYERS_DIR)
            cache = Path(pdockerd.BUILD_CACHE_DIR)
            images.mkdir(parents=True, exist_ok=True)
            layers.mkdir(parents=True, exist_ok=True)
            cache.mkdir(parents=True, exist_ok=True)

            base = images / "docker.io_library_recover_unit"
            old = images / "docker.io_library_recover_unit.old-unit"
            pull = images / "docker.io_library_recover_unit.pull-unit"
            (old / "rootfs").mkdir(parents=True)
            (old / "config.json").write_text(json.dumps({"rootfs": {"type": "layers", "diff_ids": []}, "config": {}}))
            (old / "image_ref").write_text("docker.io/library/recover:unit")
            (pull / "rootfs" / "partial").mkdir(parents=True)

            tmp_layer = "1" * 64
            partial_layer = "2" * 64
            valid_layer = "3" * 64
            (layers / f"{tmp_layer}.tmp-unit" / "tree").mkdir(parents=True)
            (layers / partial_layer).mkdir(parents=True)
            (layers / partial_layer / "meta.json").write_text(json.dumps({"diff_id": "sha256:" + partial_layer, "size": 1}))
            (layers / valid_layer / "tree").mkdir(parents=True)
            (layers / valid_layer / "meta.json").write_text(json.dumps({"diff_id": "sha256:" + valid_layer, "size": 0}))

            (cache / "invalid.json").write_text(json.dumps({"diff_id": "not-a-digest"}))
            (cache / "stale.json").write_text(json.dumps({"diff_id": "sha256:" + partial_layer}))
            (cache / "unreadable.json").write_text("{not-json")
            (cache / "valid.json").write_text(json.dumps({"diff_id": "sha256:" + valid_layer}))
            (Path(pdockerd.PDOCKER_TMP) / "pdblob_unit").write_text("partial blob")
            (Path(pdockerd.PDOCKER_TMP) / "pdload_unit").write_text("partial load")
            (Path(pdockerd.PDOCKER_TMP) / "pdsave_unit").write_text("partial save")
            (Path(pdockerd.PDOCKER_TMP) / "keep_unit").write_text("unrelated")

            removed = pdockerd.prune_build_artifacts(min_age_seconds=0, skip_active=False)

            self.assertTrue(base.exists(), removed)
            self.assertFalse(old.exists(), removed)
            self.assertFalse(pull.exists(), removed)
            self.assertFalse((layers / f"{tmp_layer}.tmp-unit").exists(), removed)
            self.assertFalse((layers / partial_layer).exists(), removed)
            self.assertTrue((layers / valid_layer).exists(), removed)
            self.assertFalse((cache / "invalid.json").exists(), removed)
            self.assertFalse((cache / "stale.json").exists(), removed)
            self.assertFalse((cache / "unreadable.json").exists(), removed)
            self.assertTrue((cache / "valid.json").exists(), removed)
            self.assertFalse((Path(pdockerd.PDOCKER_TMP) / "pdblob_unit").exists(), removed)
            self.assertFalse((Path(pdockerd.PDOCKER_TMP) / "pdload_unit").exists(), removed)
            self.assertFalse((Path(pdockerd.PDOCKER_TMP) / "pdsave_unit").exists(), removed)
            self.assertTrue((Path(pdockerd.PDOCKER_TMP) / "keep_unit").exists(), removed)

    def test_layer_cache_requires_meta_tree_and_matching_diff_id(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as tmp:
            pdockerd = load_pdockerd(Path(home), Path(tmp))
            did = "a" * 64
            ldir = Path(pdockerd.LAYERS_DIR) / did
            (ldir / "tree").mkdir(parents=True)
            self.assertFalse(pdockerd._layer_exists(did))

            (ldir / "meta.json").write_text(json.dumps({"diff_id": "sha256:" + ("b" * 64), "size": 0}))
            self.assertFalse(pdockerd._layer_exists(did))

            (ldir / "meta.json").write_text(json.dumps({"diff_id": "sha256:" + did, "size": 0}))
            self.assertTrue(pdockerd._layer_exists(did))

    def test_partial_image_with_incomplete_layer_is_not_inspectable_or_runnable(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as tmp:
            pdockerd = load_pdockerd(Path(home), Path(tmp))
            ref = "pdocker-crash-safety-partial:unit"
            norm = pdockerd.normalize_image(ref)
            img_dir = Path(pdockerd.image_dir(norm))
            did = "c" * 64
            (img_dir / "rootfs").mkdir(parents=True)
            (img_dir / "config.json").write_text(json.dumps({
                "architecture": "arm64",
                "os": "linux",
                "rootfs": {"type": "layers", "diff_ids": ["sha256:" + did]},
                "config": {"Cmd": ["/bin/true"]},
            }))
            (img_dir / "manifest.json").write_text(json.dumps({
                "schemaVersion": 2,
                "layers": [{"digest": "sha256:" + did, "diff_id": "sha256:" + did}],
                "config_ref": "config.json",
            }))
            (img_dir / "image_ref").write_text(norm)
            ldir = Path(pdockerd.LAYERS_DIR) / did
            ldir.mkdir(parents=True)
            (ldir / "meta.json").write_text(json.dumps({"diff_id": "sha256:" + did, "size": 1}))

            self.assertIsNone(pdockerd.image_config(ref))
            with self.assertRaisesRegex(ValueError, "incomplete or has partial layers"):
                pdockerd.create_container({"Image": ref, "Cmd": ["/bin/true"]}, name="partial-unit")


if __name__ == "__main__":
    unittest.main()

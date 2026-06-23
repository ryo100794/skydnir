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


class LlamaGpuCompletionDiagnosticsContractTest(unittest.TestCase):
    def _compare(self):
        return COMPARE.read_text(encoding="utf-8")

    def test_completion_timeout_collects_process_port_and_engine_evidence(self):
        script = self._compare()
        self.assertIn("port_listener_snapshot_json()", script)
        self.assertIn("pdocker.llama.port-listener-snapshot.v1", script)
        self.assertIn("completion_timeout_diagnostics_json()", script)
        self.assertIn("pdocker.llama.completion-timeout-diagnostics.v1", script)
        self.assertIn("container_state > \"$state_path\"", script)
        self.assertIn("container_logs_full \"$log_path\" || container_logs > \"$log_path\"", script)
        self.assertIn("pdocker_memory_diagnostics_json > \"$process_path\"", script)
        self.assertIn("proc_wait=", script)
        self.assertIn('"wchan": wchan or None', script)
        self.assertIn('"threads": int(threads)', script)
        self.assertIn('"wchan_samples"', script)
        self.assertIn("port_listener_snapshot_json > \"$listener_path\"", script)
        self.assertIn("/containers/$(urlencode \"$ref\")/stats?stream=0", script)
        self.assertIn("/system/memory-pressure?container=$(urlencode \"$ref\")", script)

    def test_completion_timeout_diagnostics_are_attached_to_readiness_report(self):
        script = self._compare()
        self.assertIn("attach_service_readiness_diagnostics()", script)
        self.assertIn('readiness["completion_timeout_diagnostics"] = diagnostics', script)
        self.assertIn('summary["completion_timeout_diagnostics"] = "present" if diagnostics else "missing"', script)
        self.assertIn('COMPLETION_TIMEOUT_DIAG_JSON="$TMP/completion-timeout-diagnostics.json"', script)
        self.assertIn('completion_timeout_diagnostics_json "vulkan-forced-ngl-$GPU_LAYERS" "$COMPLETION_TIMEOUT_DIAG_JSON"', script)
        self.assertIn('attach_service_readiness_diagnostics "$SERVICE_READINESS_JSON" "$COMPLETION_TIMEOUT_DIAG_JSON"', script)

    def test_prompt_sanity_failure_is_not_recorded_as_completion_timeout(self):
        script = self._compare()
        self.assertIn('if report["health"]["ok"] and report["models"]["ok"] and report["completion"]["ok"]:', script)
        self.assertIn('raise SystemExit(2)', script)
        self.assertIn('readiness_rc=0', script)
        self.assertIn('probe_service_readiness "vulkan-forced-ngl-$GPU_LAYERS" "$SERVICE_READINESS_JSON" >/dev/null || readiness_rc=$?', script)
        self.assertIn('elif [[ "$readiness_rc" -eq 2 ]]; then', script)
        self.assertIn('completion returned but prompt sanity failed', script)
        prompt_fail_branch = script.split('elif [[ "$readiness_rc" -eq 2 ]]; then', 1)[1].split('else', 1)[0]
        self.assertIn('bench_http "vulkan-forced-ngl-$GPU_LAYERS" "$GPU_JSON"', prompt_fail_branch)
        self.assertNotIn('completion_timeout_diagnostics_json', prompt_fail_branch)
        timeout_branch = script.split('else\n    operation_notify "running" "Forced Vulkan liveness passed but completion did not finish; collecting evidence"', 1)[1]
        self.assertIn('completion_timeout_diagnostics_json', timeout_branch)


if __name__ == "__main__":
    unittest.main()

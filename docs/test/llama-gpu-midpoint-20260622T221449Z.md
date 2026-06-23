# Llama GPU midpoint check - 2026-06-22T22:14:49Z

## Run

- Device: `192.168.179.21:45541`
- Runner: `scripts/android-llama-gpu-q6-workgroup-run.sh`
- Output artifact: `docs/test/llama-gpu-ngl1-q6-midpoint-192_168_179_21_45541-20260622T221449Z.json`
- Plan: `docs/test/llama-gpu-q6-preflight-plan-192_168_179_21_45541-20260622T221449Z.json`
- Verdict: `docs/test/llama-gpu-ngl1-q6-midpoint-192_168_179_21_45541-20260622T221449Z-plan-verdict.json`

The JSON artifacts are ignored by `.gitignore` (`docs/test/llama-gpu-*.json`) and are kept as local evidence unless explicitly force-added.

## Result summary

| Check | Result |
| --- | --- |
| ADB/device readiness | pass |
| GPU run allowed | yes |
| Container/server served | yes |
| `/health` | pass |
| `/v1/models` | pass |
| `/completion` HTTP response | pass |
| Prompt sanity `2+3=` -> `5` | fail (`" Marvel"`) |
| Runtime freshness markers | pass |
| API/executor reconciliation | pass, diagnostic proof strength |
| Q6 workgroup shape blocker | false |
| Q6 writeback verified | true |
| Overall verifier classification | `llama-completion-wrong-output` |
| Plan-verdict missing required evidence | none after Q6 alternative-evidence handling |
| Selected next branch | `Q6 final-store trace probe arming` |

## Key Q6 evidence

- `latest_status`: `mismatch`
- `blocker_class`: `native-q6-final-store-or-readback`
- `local_size_resolved`: `[64, 1, 1]`
- `q6_local_size`: `[64, 1, 1]`
- `q6_num_rows`: `2`
- `q6_num_cols`: `1`
- `q6_writeback_verified_all`: `true`

## Interpretation

The midpoint is reached: the fixed llama container starts, the Vulkan path is active, the server is reachable, and `/completion` returns. Correctness is not reached because the deterministic prompt check returns the wrong token. The next P0 remains the GPU numeric/final-store boundary, not container startup.

The run also exposed two evidence-routing issues now fixed in the verifier/plan layer:

- prompt-sanity failure was being routed through completion-timeout diagnostics; commit `c2c7b49e` separates HTTP success with wrong prompt content from transport timeout/failure.
- wrong-output reports now preserve Q6 diagnostics, and plan-verdict accepts the native-vs-writeback split as a valid alternative when executed final-store trace is unavailable. This keeps the next branch anchored to Q6 evidence instead of generic service-readiness failure.

The refreshed plan-verdict has no missing required evidence fields. It selects `Q6 final-store trace probe arming` because `spirv_probe_env_audit.icd.matching_armed_count == 0` while `q6_final_store_boundary.reason == missing-executed-final-store-trace`. Static review then identified the direct cause: the default runner could reuse or regenerate `/tmp/q6write10-bundle` from the archived `0x1bf751845c5dce75` SPIR-V fixture while the runtime Q6 source hash was `0x9cfc45ae24ba71d8`. The runner now refuses to refresh the default probe from the archived fixture unless explicitly allowed for fixture/regression runs, and `prepare-q6k-noop-probe.sh` can guard the source hash with `--expected-hash`.

## Next action

Keep image/model/prompt fixed. Before the next device run, provide an actual runtime Q6 source SPIR-V dump and run the Q6 runner with `--probe-source-spv` and `--probe-source-hash`; do not use the archived fixture for the midpoint. The next successful run must collect executed final-store trace for runtime hash `0x9cfc45ae24ba71d8` or report `stale-target-hash` explicitly.

## 2026-06-23 follow-up: source SPIR-V locator

The follow-up implementation adds `scripts/locate-q6-source-spirv-dump.py` as the host-only bridge between a diagnostic dump run and the next Q6 probe run.  It scans `PDOCKER_GPU_SPIRV_DUMP_DIR` output for a validated `pdocker-spirv-original-<dispatch>-<hash>.spv`, verifies the SPIR-V magic, file size, FNV-1a64 hash, and paired `pdocker.spirv.dump.v1` metadata, then emits:

- `prepare_args` for `scripts/prepare-q6k-noop-probe.sh --spv ... --expected-hash ... --probe-writes`;
- `runner_args` for `scripts/android-llama-gpu-q6-workgroup-run.sh --probe-source-spv ... --probe-source-hash ...`.

This keeps the next device run fail-closed: effective-phase dumps are not accepted as source dumps unless explicitly requested, stale fixture hashes are rejected, and ambiguous byte content for one target hash is rejected.

Next runtime sequence:

1. Run a diagnostic compare with `PDOCKER_GPU_SPIRV_DUMP_DIR` enabled.
2. Run `scripts/locate-q6-source-spirv-dump.py --dump-dir <dump-dir> --artifact <compare-artifact> --out docs/test/q6-source-spirv-locator-latest.json --print-prepare-command`.
3. Use the emitted `runner_args` in the next Q6 workgroup runner invocation.
4. Accept the next midpoint only if the final-store trace is armed for the actual runtime Q6 source hash, or if the artifact explicitly reports `stale-target-hash`.

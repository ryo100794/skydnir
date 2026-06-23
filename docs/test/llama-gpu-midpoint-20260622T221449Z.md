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

The run also exposed a compare-script classification issue: prompt-sanity failure was being routed through the completion-timeout diagnostics branch. This was fixed in commit `c2c7b49e` so future runs distinguish:

- completion transport timeout/failure, and
- completion HTTP success with wrong prompt content.

## Next action

Keep image/model/prompt fixed. Inspect Q6 final-store/readback evidence for `native-q6-final-store-or-readback`, with focus on descriptor usage and final-store boundary fields.

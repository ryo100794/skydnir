# Verification runner helpers

These files are helper and orchestrator implementations, not stable public CLI
entrypoints. Use the top-level verifier commands for normal gates unless a
runbook explicitly asks for one of these runner paths.

| Helper | Role |
|---|---|
| `cow-overlay-kill-at-step-device.sh` | Device-side fail-closed COW/overlay checkpoint runner. |
| `cow_overlay_kill_at_step_device.py` | Host orchestrator and artifact writer for COW/overlay kill-at-step evidence. |
| `image-pull-crash-safety-device.sh` | Token-scoped device-side image-pull recovery runner. |
| `image_pull_crash_safety_device.py` | Host orchestrator for image-pull crash-safety evidence. |

Runner paths stay stable until manifests, docs, and verifier allowlists migrate
in the same focused change.

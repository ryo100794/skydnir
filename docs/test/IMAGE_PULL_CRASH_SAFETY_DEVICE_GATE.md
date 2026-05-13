# Image Pull Crash-Safety Device Gate

Snapshot: 2026-05-13.

This gate verifies that interrupted image-pull residue is not promoted into a
valid image or reusable layer after the Android daemon is killed and restarted.
It complements the host static verifier:

```sh
python3 scripts/verify-image-pull-crash-safety.py
```

## Runner

```sh
python3 scripts/verify/runner/image_pull_crash_safety_device.py \
  --serial <adb-serial> \
  --package io.github.ryo100794.pdocker.compat \
  --artifact docs/test/image-pull-crash-safety-latest.json \
  --execute-device
```

Without `--execute-device`, or without a ready ADB device, the runner writes an
artifact with `success=false`. It must never turn missing device evidence into a
pass.

## Concrete phases

The current device lane is a safe residue-recovery test. It does not pull a
large public image by default. Instead it creates scenario-owned residue under
the app-private store, kills the daemon, restarts the app service, and probes
the resulting store through the Engine API.

1. `prepare-residue`
   - Create a previous image backup as `.old-$TOKEN`.
   - Create partial image stages as `.pull-$TOKEN`.
   - Create a partial layer stage as `.tmp-$TOKEN`.
   - Create a malformed content-address-looking layer directory without a
     complete `tree/`.
   - Capture the image/layer store listing before daemon kill.

2. `kill-daemon`
   - Capture the daemon process table.
   - Send `TERM` then `KILL` to `pdockerd` only.
   - Do not force-stop unrelated apps and do not remove broad app data.

3. `restart-and-probe`
   - Start the app smoke service.
   - Wait for `files/pdocker/pdockerd.sock`.
   - Let daemon startup recovery prune `.pull-*`, `.tmp-*`, and malformed
     layers, and restore `.old-*` only when the base tag is absent.
   - Probe the restored tag and the never-published tag through Engine API.

4. `cleanup`
   - Remove only paths containing the scenario token and known scenario names.
   - Leave unrelated images, layers, containers, app data, and other workers'
     files untouched.

## Required assertions

The top-level artifact records these booleans:

- `old_tag_restored`
- `pull_stage_pruned`
- `tmp_layer_pruned`
- `partial_layer_pruned`
- `never_published_tag_rejected`
- `restored_tag_inspectable`
- `daemon_restarted`
- `cleanup_removed_only_scenario_owned_paths`

All must be true for the concrete residue-recovery lane to pass.

## Evidence files

The runner pulls the device evidence directory into:

```text
docs/test/image-pull-crash-safety-device/
```

The top-level artifact points at:

- `prepare-summary.json`
- `kill-summary.json`
- `restart-summary.json`
- `cleanup-summary.json`
- `store-before-kill.txt`
- `store-after-restart.txt`
- `inspect-restored.raw`
- `inspect-never.raw`
- daemon process captures before kill and after restart

## Remaining gap

The current lane intentionally avoids killing a live registry download by
default. The remaining gap is a timed live-pull interruption test that starts an
actual `/images/create` request, kills the daemon while the pull is in progress,
and proves the same post-restart conditions. That future lane must still use a
scenario-owned reference or an isolated registry fixture; it must not overwrite
user images or clean broad stores.

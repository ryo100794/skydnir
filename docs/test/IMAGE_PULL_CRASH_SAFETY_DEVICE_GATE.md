# Image Pull Crash-Safety Device Gate

Snapshot: 2026-05-18.

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

The default device lane is a safe residue-recovery test. It does not pull a
large public image by default. Instead it creates scenario-owned residue under
the app-private store, kills the daemon, restarts the app service, and probes
the resulting store through the Engine API. A separate opt-in timed live-pull
lane is available only with a safe scenario-owned/isolated fixture image.

1. `prepare-residue`
   - Create a previous image backup as `.old-$TOKEN`.
   - Create partial image stages as `.pull-$TOKEN`.
   - Create a partial layer stage as `.tmp-$TOKEN`.
   - Create a malformed content-address-looking layer directory without a
     complete `tree/`.
   - Create a scenario-owned partial local image that references that incomplete
     layer so inspect/create can prove fail-closed behavior without auto-pulling
     a missing public reference.
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
   - Probe the restored tag, never-published tag, and partial local image
     through Engine API.
   - Attempt container create for the existing scenario-owned partial image and
     require a non-201 response.
   - Independently scan `store-after-restart.txt`; any scenario-owned `.pull-*`,
     `.tmp-*`, `.old-*`, never-published tag, or malformed layer survivor fails
     the gate even if a summary boolean says it was rejected. The deliberate
     partial local image fixture may remain only if inspect/create both reject it.

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
- `partial_image_pruned_or_rejected`
- `partial_image_inspect_rejected`
- `partial_image_create_rejected`
- `restored_tag_inspectable`
- `daemon_restarted`
- `cleanup_removed_only_scenario_owned_paths`
- `no_partial_or_corrupt_image_cache_survivors`
- `live_pull_started_before_kill` (only true when the live lane runs)
- `live_daemon_killed_and_restarted` (only true when the live lane runs)
- `live_partial_tag_not_published` (only true when the live lane runs)
- `live_pull_stage_pruned` (only true when the live lane runs)
- `live_tmp_layers_pruned` (only true when the live lane runs)

All must be true for the concrete residue-recovery lane to pass. The survivor
assertion is derived from the raw post-restart store listing on the host side,
not only from the device-side summary JSON.

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
- `inspect-partial.raw`
- `create-partial.raw`
- daemon process captures before kill and after restart
- `post_restart_survivors` in the top-level artifact (must be an empty list for
  a pass)
- `live-pull-summary.json`, `live-pull.raw`, `live-store-before-kill.txt`,
  `live-store-after-restart.txt`, and `live-inspect.raw` when the timed live
  lane runs

## Timed live-pull interruption gate

The live registry interruption lane is represented in the JSON artifact as
`live_pull_interruption.phase = timed-live-pull-interruption`. It must not run
by default because killing a pull against a user-owned image or shared tag can
leave destructive residue. When fully opted in on a ready device, the runner
starts a real Engine API `/images/create` request, sleeps for the configured
delay, kills only `pdockerd`, restarts the app service, and proves the
interrupted ref was not published and no newly introduced `.tmp-*` layer stages
survived restart recovery.

Minimum opt-in CLI:

```sh
python3 scripts/verify/runner/image_pull_crash_safety_device.py \
  --serial <adb-serial> \
  --package io.github.ryo100794.pdocker.compat \
  --artifact docs/test/image-pull-crash-safety-latest.json \
  --execute-device \
  --execute-live-pull-interruption \
  --live-image <scenario-owned-or-isolated-fixture-ref> \
  --live-fixture-owned \
  --live-interrupt-after-seconds 3 \
  --live-timeout-seconds 120
```

Safety gates:

- `--execute-live-pull-interruption` is required; without it, the artifact stays
  `success=false` / `status=planned-gap` for live-pull coverage.
- `--live-image` must be a scenario-owned reference or an isolated disposable
  registry fixture, never a user image or broad mutable tag.
- The artifact records `live_image_safe`, `live_image_safety_reason`, and
  `safe_image_requirements`. Common public refs such as `ubuntu:latest`,
  `busybox:latest`, `alpine:latest`, `debian:latest`, and `library/*` are
  rejected even if `--live-fixture-owned` is present.
- `--live-fixture-owned` is the operator acknowledgement that the fixture is safe
  to interrupt and clean.
- Without a ready device and `--execute-device`, the fully opted-in invocation
  remains non-promoting (`success=false` and no live coverage artifact).

Phase steps for `timed-live-pull-interruption`:

1. Start a timed Engine API `/images/create` pull for `--live-image`.
2. Sleep for `--live-interrupt-after-seconds` while transfer is active.
3. Kill only `pdockerd` mid-transfer.
4. Restart the daemon and wait for the socket.
5. Capture pre/post store listings plus the live pull stream.
6. Assert partial image stages are pruned, the interrupted ref is not
   accidentally published/inspectable, and no newly introduced `.tmp-*` layer
   stages survived.
7. Cleanup only the scenario-owned/isolated fixture image path; do not clean
   broad stores or unrelated workers' residues.

## Remaining gap

The remaining gap is environmental rather than runner logic: if no ready
device or no safe fixture image is supplied, the runner records
`planned-gap`/`blocked` and never promotes success. The timed live lane itself
is implemented but remains opt-in and fail-closed; it must use a
scenario-owned reference or an isolated registry fixture and must not overwrite user images or clean broad stores.

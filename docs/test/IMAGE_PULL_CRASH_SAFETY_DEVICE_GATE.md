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
     `.tmp-*`, `.old-*`, never-published tag, malformed layer, or partial-image
     survivor fails the gate even if a summary boolean says it was rejected.

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

## Timed live-pull interruption design gate

The live registry interruption lane is represented in the JSON artifact as
`live_pull_interruption.phase = timed-live-pull-interruption`, but it is a
planned gap in this safe-prep change. It must not run by default because killing
a pull against a user-owned image or shared tag can leave destructive residue.

Minimum opt-in CLI for a future implementation:

```sh
python3 scripts/verify/runner/image_pull_crash_safety_device.py \
  --serial <adb-serial> \
  --package io.github.ryo100794.pdocker.compat \
  --artifact docs/test/image-pull-crash-safety-latest.json \
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
- `--live-fixture-owned` is the operator acknowledgement that the fixture is safe
  to interrupt and clean.
- Until the device-side live phase exists, even the fully opted-in invocation
  remains `success=false`, `coverage.live_interrupted_network_pull=false`, and
  `live_pull_interruption.status=planned-gap`.

Planned phase steps for `timed-live-pull-interruption`:

1. Start a timed Engine API `/images/create` pull for `--live-image`.
2. Sleep for `--live-interrupt-after-seconds` while transfer is active.
3. Kill only `pdockerd` mid-transfer.
4. Restart the daemon and wait for the socket.
5. Assert partial image stages/layers are pruned and the interrupted ref is not
   accidentally published.
6. Cleanup only scenario-token-owned tags, stages, layer residue, and isolated
   fixture artifacts.

## Remaining gap

The current lane intentionally avoids killing a live registry download by
default. The remaining gap is a timed live-pull interruption test that starts an
actual `/images/create` request, kills the daemon while the pull is in progress,
and proves the same post-restart conditions. That future lane requires
`--execute-live-pull-interruption`, `--live-image`, and `--live-fixture-owned`,
and must still use a scenario-owned reference or an isolated registry fixture;
it must not overwrite user images or clean broad stores.

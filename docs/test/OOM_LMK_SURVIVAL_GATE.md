# OOM/LMK Survival Gate

Status: planned-gap for connected-device LMK/backend-death replay.

`python3 scripts/verify-oom-lmk-survival-gate.py` is the host/static gate for
memory runtime survival evidence. It verifies large allocation denial telemetry,
backend death classification, exact device return-code/artifact checks, and
non-promotion rules.

Current device-gated placeholder:

```sh
python3 scripts/verify-oom-lmk-survival-gate.py \
  --device-plan-artifact docs/test/oom-lmk-survival-latest.json
```

That command intentionally exits nonzero and writes a non-promoting artifact
until a real APK-scoped replay can prove large allocation denial, backend death
or LMK loss, stale-running guard behavior, and artifact retention on device.
Planned-gap artifacts must stay `success=false` and
`stable_checkpoint_eligible=false`.

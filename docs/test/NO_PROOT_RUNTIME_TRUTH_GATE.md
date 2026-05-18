# No-PRoot Runtime Truth Gate

This gate documents the current modern/no-PRoot runtime contract: until the
Android direct executor advertises `process-exec=1`, pdockerd must fail closed
instead of reporting fake Docker process success.

Artifact: [`no-proot-runtime-truth-latest.json`](no-proot-runtime-truth-latest.json)

## Required truth

When the direct executor probe lacks `process-exec=1`:

- `docker run` / container start fails with an explicit runtime capability error.
- `docker exec` fails with the same capability class.
- Dockerfile `RUN` fails and does not record a fake layer.
- Health status cannot become `healthy` after the capability failure.
- Published ports are only `planned` or `inactive`; they are not `active`
  without live listener/proxy/rewrite evidence.
- The artifact remains non-promoting (`success: false`) until real no-PRoot
  process execution exists.

## Commands

Generate/update the host-visible truth artifact:

```sh
bash scripts/android-no-proot-runtime-truth-gate.sh --host-probe \
  --out docs/test/no-proot-runtime-truth-latest.json
```

Verify it:

```sh
python3 scripts/verify-no-proot-runtime-truth-artifact.py \
  docs/test/no-proot-runtime-truth-latest.json
```

The host probe imports `docker-proot-setup/bin/pdockerd` with
`PDOCKER_RUNTIME_BACKEND=no-proot` and a focused helper that reports
`pdocker-direct-executor:1` plus `process-exec=0`. It is intentionally a
non-promoting runtime-gap artifact, not a replacement for a future device pass.

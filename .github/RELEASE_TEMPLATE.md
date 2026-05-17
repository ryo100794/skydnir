# pdocker-android vX.Y.Z

## Highlights

- 

## Fixed Build Evidence

- Build number:
- Commit:
- Build record:
- APK payload check: no bundled upstream Docker CLI/Compose, PRoot,
  proot-loader, or talloc.
- Device full smoke:

## Compatibility

- Engine API:
- Compose:
- Direct executor:
- TTY/logs:
- Storage: shared layer pool counted once; image/rootfs apparent sizes overlap;
  container upperdir/private bytes verified separately.
- Network/ports:

## Device Testing

- Device:
- Android:
- APK flavor:
- Smoke:
- Runtime benchmark:

## Known Limits

- Host backend regression:
- Literal test-density gate:
- GPU bridge / llama.cpp layer offload:
- Android platform limits:

## Security And Signing

- Signing material is not included in the repository.
- Release APK signature:
- Secret audit:
- Release readiness:

## Docs

- `README.md`
- `docs/plan/STATUS.md`
- `docs/plan/TODO.md`
- `docs/test/COMPATIBILITY.md`
- `docs/test/SECRET_AUDIT.md`
- `docs/release/builds/20260505.1/README.md` or newer fixed build record

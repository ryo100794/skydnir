# Contributing

Skydnir is experimental and Android-device-dependent. Good
contributions are small, reproducible, and clear about which route they tested:
native UI, Engine API, test-staged Docker CLI, or Android direct executor.

## Before Opening An Issue

Please check:

- [`README.md`](README.md) for the current project posture.
- [`docs/plan/STATUS.md`](docs/plan/STATUS.md) for the implementation snapshot.
- [`docs/plan/TODO.md`](docs/plan/TODO.md) for active work.
- [`docs/test/COMPATIBILITY.md`](docs/test/COMPATIBILITY.md) for Docker API and
  protocol coverage.
- [`docs/release/builds/20260505.1/README.md`](docs/release/builds/20260505.1/README.md)
  for the latest fixed build evidence.

Current public baseline: build `20260505.1` is committed at `dd3ce31`; the APK
device full smoke passed. Do not report the host backend lane or the literal
test-density gate as fixed until new evidence replaces that build record.

## Useful Reports

Include:

- device model;
- Android version and API level;
- build number, commit, or release tag;
- APK flavor (`compat` or `modern`);
- whether the action was triggered from the UI, Engine API, or test-staged CLI;
- relevant logs with secrets redacted;
- whether the failure is reproducible after restarting the app.

For device reports, please say whether the route covered install, app launch,
Skydnir daemon start, image pull or build, Compose up/down, logs, Engine API exec,
Engine API `exec -it`, and service-port checks.

## Development Checks

Fast local checks:

```sh
bash scripts/verify-fast.sh
python3 scripts/verify-ui-actions.py
python3 scripts/verify-project-library.py
```

APK checks:

```sh
./gradlew assembleCompatDebug
./gradlew assembleCompatRelease
```

Device checks:

```sh
ANDROID_SERIAL=<host:port> bash scripts/android-device-smoke.sh --no-install
ANDROID_SERIAL=<host:port> bash scripts/android-runtime-bench.sh
```

## Security

Never paste secrets into issues, logs, screenshots, commits, or release assets.
See [`SECURITY.md`](SECURITY.md) and
[`docs/test/SECRET_AUDIT.md`](docs/test/SECRET_AUDIT.md).

## Scope Discipline

The product APK should not silently bundle external runtime components or
upstream Docker CLI/Compose binaries. Test scripts may stage compatibility
tools explicitly, but product behavior should go through Engine API/native app
actions unless a design document says otherwise.

The default APK also must not reintroduce PRoot, proot-loader, or talloc as
bundled runtime payloads. If a report or patch depends on those tools, describe
it as an optional external comparison route rather than product behavior.

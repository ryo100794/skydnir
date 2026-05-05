# Build Documents

Snapshot date: 2026-05-04.

## Purpose

This category owns local setup, APK packaging, install commands, and build
gates. It should explain how to produce and install artifacts, not how to
validate every runtime behavior.

## Contents

This category currently has one canonical local build document: this README.
F-Droid/reproducible-build readiness planning lives in
[`FDROID_RELEASE_PROCESS.md`](FDROID_RELEASE_PROCESS.md).

## Canonical Sources

- Test commands live in [`../test/COMPATIBILITY.md`](../test/COMPATIBILITY.md)
  and [`../test/README.md`](../test/README.md).
- Active work items live in [`../plan/TODO.md`](../plan/TODO.md).
- Product scope and unsupported Docker features live in
  [`../design/DOCKER_COMPAT_SCOPE.md`](../design/DOCKER_COMPAT_SCOPE.md).

## Environment

From an aarch64 Android/Linux shell:

```sh
git clone <this repo>
cd pdocker-android
bash scripts/setup-env.sh
```

`scripts/setup-env.sh` installs or stages the expected JDK, Android command-line
tools, and NDK pieces for this workspace.

## One-Command Local Build

The normal contributor build is the compatibility APK with all current native
payloads staged:

```sh
bash scripts/build-all.sh
```

That default flow builds:

- Android/Bionic helper libraries with `scripts/build-native-termux.sh`.
- Linux/glibc GPU payloads with `scripts/build-gpu-shim.sh`, including the
  GPU shim, Vulkan ICD, and OpenCL ICD.
- The `compat` debug APK through `scripts/build-apk.sh`.

Run the same build plus the regular fast gate:

```sh
bash scripts/build-all.sh --verify-fast
```

Dry-run the orchestration and freshness checks without compiling:

```sh
bash scripts/build-all.sh --dry-run
```

Selective rebuilds are available when you know which payload changed:

```sh
bash scripts/build-all.sh --native
bash scripts/build-all.sh --gpu-shim
bash scripts/build-all.sh --apk
```

`--apk` packages from already-built payloads and checks that native/GPU outputs
are not older than their source files. If those checks fail, rebuild the
reported payload first or use the default `build-all.sh` flow.

## APK Builds

Build metadata is fixed in the repository root `version.properties`. Update
that file intentionally when cutting a managed build so local APK rebuilds do
not silently change `versionCode`, `versionName`, build time, build number, or
the recorded source baseline.

Build only the default configured APK package step:

```sh
bash scripts/build-apk.sh
```

The default build flavor is `compat` because it enables the scratch
`pdocker-direct` process executor for Dockerfile `RUN`, `docker run`,
`docker exec`, and `compose up` validation. Prefer `scripts/build-all.sh` for
normal local builds because it also refreshes the glibc GPU shim/ICDs before
packaging.

`scripts/build-apk.sh` still rebuilds Android native helper libraries by
default for compatibility with older instructions. The build orchestrator sets
`PDOCKER_SKIP_NATIVE_BUILD=1` after it has already refreshed those helpers.

The `modern` flavor is useful for API 29+ metadata, image browsing, editing,
and Engine API work, but it does not advertise `process-exec=1`.

Build the API 29+ metadata-only flavor explicitly:

```sh
bash scripts/build-all.sh --modern
```

The `modern` flavor is retained only as an explicit API 29+ metadata/edit/browse
route. Normal development, fast audits, device smoke, Compose up validation,
and public tester instructions should use `compat` unless a task specifically
asks to inspect the metadata-only route. A stale
`app/build/outputs/apk/modern/...` artifact must not be treated as the current
APK just because it exists in the Gradle build directory.

Build explicit debug variants:

```sh
bash scripts/build-all.sh --compat --apk
bash scripts/build-all.sh --modern --apk
```

Build a fixed-signature compatibility APK by keeping the signing material
outside Git and passing it through the environment:

```sh
export PDOCKER_SIGNING_STORE_FILE=$HOME/.pdocker/release.jks
export PDOCKER_SIGNING_STORE_PASSWORD=...
export PDOCKER_SIGNING_KEY_ALIAS=pdocker
export PDOCKER_SIGNING_KEY_PASSWORD=...
bash scripts/build-all.sh --compat --build-type release
```

`*.jks`, `*.keystore`, `*.p12`, `*.pem`, `*.key`, `*.crt`, and local signing
property files are ignored by Git. Do not commit signing certificates or
private keys. A fixed release signature can reduce repeated install-time
security prompts compared with debug-signed APK churn, but Android/Google Play
Protect verification remains an OS/device policy and cannot be disabled by the
app.

Modern debug output:

```text
app/build/outputs/apk/modern/debug/app-modern-debug.apk
```

Compat debug output:

```text
app/build/outputs/apk/compat/debug/app-compat-debug.apk
```

Compat release output:

```text
app/build/outputs/apk/compat/release/app-compat-release.apk
app/build/outputs/apk/compat/release/app-compat-release-unsigned.apk
```

## Install Over Wi-Fi ADB

Pair/connect the device through Android Wireless debugging, then install the
default compatibility APK for process-exec validation:

```sh
adb connect <host>:<port>
adb install -r app/build/outputs/apk/compat/debug/app-compat-debug.apk
```

Install the metadata-only API 29+ route only when intentionally testing the
`modern` flavor:

```sh
adb install -r app/build/outputs/apk/modern/debug/app-modern-debug.apk
```

## Build-Time Gates

Short gate used during regular implementation:

```sh
bash scripts/verify-fast.sh
```

Kotlin/APK compile gate:

```sh
./gradlew assembleModernDebug
./gradlew assembleCompatDebug
```

Slower backend gates are documented in
[`../test/COMPATIBILITY.md`](../test/COMPATIBILITY.md).

Host-only release-readiness check for issue #9 and the F-Droid readiness
process:

```sh
python3 scripts/verify-release-readiness.py
```

This check audits documentation claims, placeholder F-Droid metadata,
secret/signing material, and the generated/prebuilt payload inventory. It does
not build an APK, require an Android device, or claim F-Droid readiness.

## Maintenance

- Keep command examples runnable from the repository root.
- Keep signing guidance here, and keep secret-audit procedure in
  [`../test/SECRET_AUDIT.md`](../test/SECRET_AUDIT.md).
- Link to test docs for validation detail instead of copying compatibility
  matrices into this file.

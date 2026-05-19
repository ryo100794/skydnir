# Native Build Environment

Snapshot date: 2026-05-19.

## Purpose

This document defines the standard native build path for packaged native
payloads. The project previously used a Termux-local compiler for part of the
Android helper build. That path remains available only as a local fallback. The
standard path is a reproducible glibc Linux host using the official Android NDK
toolchain and explicit aarch64 Linux/glibc cross compilers where container-side
payloads are required.

## Artifact Classes

| Class | Runtime | Examples | Standard builder |
|---|---|---|---|
| Android/Bionic helpers | Android app process or executable payload extracted by the APK | `libpdockerpty.so`, `libpdockerdirect.so`, `libpdockergpuexecutor.so`, `libpdockermediaexecutor.so` | `scripts/build-native-android-ndk.sh` |
| Linux/glibc container payloads | Container rootfs / glibc userland | `libcow.so`, `pdocker-gpu-shim`, `pdocker-vulkan-icd.so`, `pdocker-opencl-icd.so` | aarch64 Linux/glibc cross compiler |
| External/static tools | Backend helper tools that must be source-built or inventoried | `crane` | source build or explicit release blocker |

Do not mix Android/Bionic and Linux/glibc artifacts when validating ELF
properties. The APK packaging layer stores both kinds of payloads under native
library names only so Android extracts them reliably.

## Standard Android/Bionic Helper Build

Run from a normal glibc Linux host:

```sh
export ANDROID_NDK_HOME=/root/android-ndk-r26d
bash scripts/build-native-android-ndk.sh
```

The script uses:

- `$ANDROID_NDK_HOME/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android26-clang`
- `arm64-v8a`
- Android API level `26` by default
- no Termux binaries
- no box64
- no Android device-local compiler

On an aarch64 glibc development host where the installed official NDK prebuilt
is `linux-x86_64` and therefore not directly executable, the script falls back
to the host glibc `clang` while still using the NDK target, sysroot, and
compiler-rt resource directory. That fallback requires `ld.lld` on `PATH`; it
is still a glibc-host build and does not use Termux.

The output remains compatible with the existing packaging layout:

```text
app/src/main/jniLibs/arm64-v8a/libpdockerpty.so
app/src/main/jniLibs/arm64-v8a/libpdockerdirect.so
app/src/main/jniLibs/arm64-v8a/libpdockergpuexecutor.so
app/src/main/jniLibs/arm64-v8a/libpdockermediaexecutor.so
```

`libpdockerdirect.so`, `libpdockergpuexecutor.so`, and
`libpdockermediaexecutor.so` are executable PIE files intentionally named
`lib*.so`. Android extracts native libraries by name; the app later exposes
them to the backend under executable names.

## Legacy Termux Fallback

The legacy script remains:

```sh
bash scripts/build-native-termux.sh
```

Use it only for local fallback on a device where the NDK host binaries cannot
run. It is not the release or CI path.

Select the fallback explicitly:

```sh
PDOCKER_NATIVE_BUILD_BACKEND=termux bash scripts/build-apk.sh
bash scripts/build-all.sh --native-backend termux
```

## Orchestrated Build

The default local orchestrator now chooses the NDK native backend:

```sh
bash scripts/build-all.sh
```

Equivalent explicit form:

```sh
bash scripts/build-all.sh --native-backend ndk
```

APK-only packaging still checks that generated payloads are fresh. If a native
helper is stale, the Gradle freshness hint points to:

```sh
bash scripts/build-native-android-ndk.sh
```

## Linux/glibc Container Payloads

Container-facing GPU and COW payloads must not be built with Android/Bionic
headers. They need a Linux/glibc aarch64 compiler.

Required direction:

```sh
CC=aarch64-linux-gnu-gcc bash scripts/build-gpu-shim.sh
CC=aarch64-linux-gnu-gcc make -C docker-proot-setup/src/overlay clean install
```

The current GPU shim script accepts `CC`, but the release lane must still add
ELF verification so an x86_64 host compiler cannot accidentally produce x86_64
payloads.

## Reproducibility Work Items

Before calling this F-Droid ready, the build lane still needs:

1. Source-build or exclude inventoried external binaries such as `crane`.
2. Add CI that deletes generated native outputs, rebuilds them, and verifies
   ELF class, machine, interpreter, and checksums.
3. Run the APK build twice in a clean pinned environment and compare outputs.
4. Move local-only absolute paths such as custom `aapt2` overrides out of the
   repository-controlled default path.
5. Keep `scripts/build-native-termux.sh` documented as legacy fallback only,
   not as a normal packaging requirement.

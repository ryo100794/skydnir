# Native Build Environment

Snapshot date: 2026-05-19.

## Purpose

This document defines the standard native build path for packaged native
payloads. The project previously used a Termux-local compiler for part of the
Android helper build. That path remains available only as a legacy local mode. The
standard path is a reproducible glibc Linux host using the official Android NDK
toolchain and explicit aarch64 Linux/glibc cross compilers where container-side
payloads are required.

## Artifact Classes

| Class | Runtime | Examples | Standard builder |
|---|---|---|---|
| Android/Bionic helpers | Android app process or executable payload extracted by the APK | `libpdockerpty.so`, `libpdockerdirect.so`, `libpdockergpuexecutor.so`, `libpdockermediaexecutor.so` for `arm64-v8a` and `armeabi-v7a` | `scripts/build-native-android-ndk.sh` |
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

The script builds:

- `arm64-v8a` with `aarch64-linux-android26-clang`
- `armeabi-v7a` with `armv7a-linux-androideabi26-clang`
- Android API level `26` by default
- no Termux binaries
- no box64
- no Android device-local compiler

On an x86_64 Linux development host, the normal official NDK driver is used
directly. The script detects the NDK host tag instead of baking in project-only
x86_64 behavior.

On an aarch64 glibc development host where the installed official NDK prebuilt
is `linux-x86_64` and therefore not directly executable, the script uses
**aarch64 glibc host-clang mode**: host glibc `clang` drives the compile while
the Android target, sysroot, and compiler-rt resource directory still come from
the NDK. This mode requires `ld.lld` on `PATH`; it is still a glibc-host build
and does not use Termux. This mode is not an x86_64 compatibility trick; it is
the explicit local aarch64-host mode until an AOSP-built Android LLVM toolchain
is available.

The current Google-distributed NDK package in this workspace provides a
`linux-x86_64` host prebuilt. It does not provide a directly executable
`linux-aarch64` NDK driver. If the project requires a true NDK-style driver
binary for an aarch64 glibc host, use the optional plan in
[`AOSP_LLVM_AARCH64_HOST_PLAN.md`](AOSP_LLVM_AARCH64_HOST_PLAN.md). Do not use
unofficial repacked NDK binaries.

The output remains compatible with the existing packaging layout:

```text
app/src/main/jniLibs/arm64-v8a/libpdockerpty.so
app/src/main/jniLibs/arm64-v8a/libpdockerdirect.so
app/src/main/jniLibs/arm64-v8a/libpdockergpuexecutor.so
app/src/main/jniLibs/arm64-v8a/libpdockermediaexecutor.so
app/src/main/jniLibs/armeabi-v7a/libpdockerpty.so
app/src/main/jniLibs/armeabi-v7a/libpdockerdirect.so
app/src/main/jniLibs/armeabi-v7a/libpdockergpuexecutor.so
app/src/main/jniLibs/armeabi-v7a/libpdockermediaexecutor.so
```

`libpdockerdirect.so`, `libpdockergpuexecutor.so`, and
`libpdockermediaexecutor.so` are executable PIE files intentionally named
`lib*.so`. Android extracts native libraries by name; the app later exposes
them to the backend under executable names.

The current `armeabi-v7a` `libpdockerdirect.so` is an explicit unsupported-ABI
executable that exits with a capability error. The 32-bit ARM ptrace/syscall
executor is a separate porting task because the current direct executor uses
AArch64 register and syscall conventions. Packaging the 32-bit binary keeps ABI
coverage visible without silently claiming full 32-bit process execution.

## Packaged Native Build Path

The only supported packaged Android/Bionic native helper build is the NDK path:

```sh
bash scripts/build-native-android-ndk.sh
```

`scripts/build-apk.sh` and `scripts/build-all.sh` use this path for native
helper refreshes. Historical local Termux-device build notes may exist in old
test evidence, but they are not an active packaging or release path.

## Orchestrated Build

The default local orchestrator builds with the NDK native backend:

```sh
bash scripts/build-all.sh
```

APK-only packaging still checks that generated payloads are fresh. If a native
helper is stale, the Gradle freshness hint points to:

```sh
bash scripts/build-native-android-ndk.sh
```

## Linux/glibc Container Payloads

Container-facing GPU and COW payloads must not be built with Android/Bionic
headers. They need Linux/glibc cross compilers for the target container
architectures.

Required direction:

```sh
CC_ARM64=aarch64-linux-gnu-gcc CC_ARMHF=arm-linux-gnueabihf-gcc bash scripts/build-gpu-shim.sh
CC=aarch64-linux-gnu-gcc make -C docker-proot-setup/src/overlay clean install
```

The GPU shim script verifies ELF class and architecture markers after each
build so a host compiler cannot silently produce the wrong architecture. The
armhf Vulkan ICD currently compiles with pointer-width warnings because the
bridge stores some Vulkan handles as pointer-shaped integers; treat the armhf
GPU payload as packaged experimental evidence until those handle abstractions
are made pointer-width clean.

## Reproducibility Work Items

Before calling this F-Droid ready, the build lane still needs:

1. Source-build or exclude inventoried external binaries such as `crane`.
2. Add CI that deletes generated native outputs, rebuilds them, and verifies
   ELF class, machine, interpreter, and checksums.
3. Run the APK build twice in a clean pinned environment and compare outputs.
4. Move local-only absolute paths such as custom `aapt2` overrides out of the
   repository-controlled default path.
5. Decide whether to add an AOSP LLVM source-build lane for a true
   aarch64-host Android toolchain driver.
6. Port `pdocker-direct` to 32-bit ARM registers/syscalls before promoting
   `armeabi-v7a` process-exec beyond explicit unsupported status.

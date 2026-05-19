# AOSP LLVM AArch64 Host Toolchain Plan

Snapshot date: 2026-05-19.

## Purpose

The default native helper build uses the installed Android NDK and, on aarch64
glibc hosts without an official directly executable NDK host prebuilt, host
glibc `clang` with the NDK target, sysroot, and compiler-rt. That is the normal
lightweight build path.

This document defines a separate heavyweight lane for producing a true
AArch64-host Android LLVM toolchain from AOSP source if release policy later
requires one.

## Current NDK Provenance Inputs

The installed NDK r26d tree includes the inputs needed to pin an AOSP LLVM
rebuild:

- `AndroidVersion.txt`
- `clang_source_info.md`
- `manifest_11349228.xml`

These identify the LLVM version, cherry-pick/source metadata, and exact AOSP
manifest for the NDK toolchain baseline.

## Proposed Lane

1. Record checksums and metadata from the current NDK r26d tree.
2. Create a clean AOSP LLVM workspace from `manifest_11349228.xml`.
3. Build only Linux AArch64 host LLVM tools needed by the Android wrappers:
   `clang`, `clang++`, `ld.lld`, `llvm-ar`, `llvm-strip`, and compiler-rt
   resources.
4. Assemble an NDK-shaped host tree, for example:

```text
out/aosp-llvm/android-ndk-r26d-linux-aarch64/
```

5. Run the normal helper build through:

```sh
ANDROID_NDK_HOME=out/aosp-llvm/android-ndk-r26d-linux-aarch64 \
ANDROID_NDK_HOST_TAG=linux-aarch64 \
bash scripts/build-native-android-ndk.sh
```

6. Compare output against the default host-clang lane with `file`, `readelf`,
   dynamic dependency checks, and functional APK build checks.

## Cost and Policy

This lane is intentionally optional:

- Expected build time: hours on AArch64 hardware.
- Expected disk use: 100 GB or more for checkout and intermediates.
- It does not solve shipped-binary provenance by itself.
- F-Droid-oriented readiness should focus first on source-building or excluding
  shipped payloads such as `crane`, glibc shims, and generated native outputs.

Make this lane mandatory only if release policy requires AArch64 Linux release
hosts to use a true AOSP-built Android toolchain driver instead of the current
host-clang + NDK sysroot mode.

## Risks

- Version skew from the pinned NDK LLVM revision.
- Incomplete NDK-shaped host tree assembly.
- Non-reproducible absolute paths, timestamps, and strip behavior.
- CI resource exhaustion.
- Maintenance drift whenever the pinned NDK version changes.

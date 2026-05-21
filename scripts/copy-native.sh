#!/usr/bin/env bash
# copy-native.sh — stage native binaries and pdockerd python tree from the
# integrated docker-proot-setup backend into app/src/main/{assets,python,jniLibs}/.
# Run this before `gradle assembleDebug`.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SUB="$ROOT/docker-proot-setup"
APP="$ROOT/app/src/main"

if [[ ! -d "$SUB" ]]; then
    echo "error: backend directory missing: $SUB" >&2
    exit 1
fi

# --- python: pdockerd script as an Android asset ---
# We deliberately don't ship pdockerd through Chaquopy's src/main/python
# tree. Chaquopy's AssetFinder only handles .py/.pyc via its custom
# importer, and pdockerd expects to resolve its runtime layout (docker-bin,
# lib) relative to its own __file__. So we stage the raw single-file
# script under assets/pdockerd/ and let Kotlin extract it to
# filesDir/pdocker-runtime/bin/ on first launch.
mkdir -p "$APP/assets/pdockerd"
cp "$SUB/bin/pdockerd" "$APP/assets/pdockerd/pdockerd"
cp "$ROOT/scripts/llama-gpu-env-manifest.json" "$APP/assets/pdockerd/llama-gpu-env-manifest.json"

# --- native: crane + scratch direct runtime assets ---
# Package as jniLibs with lib*.so naming so Android extracts them to
# nativeLibraryDir — the only location an app is allowed to execve
# from on API 29+ (files in /data/data/<pkg>/files/ have exec_no_trans
# SELinux denial). The names must start with "lib" and end with ".so"
# or AGP drops them during packaging. crane is static Go.
JNI_DIR="$APP/jniLibs/arm64-v8a"
COMPAT_JNI_DIR="$ROOT/app/src/compat/jniLibs/arm64-v8a"
mkdir -p "$JNI_DIR" "$COMPAT_JNI_DIR"
if [[ "${PDOCKER_FDROID_NO_CRANE:-0}" != "0" ]]; then
    rm -f "$JNI_DIR/libcrane.so"
    echo "fdroid-no-crane: removed $JNI_DIR/libcrane.so"
else
    cp "$SUB/docker-bin/crane" "$JNI_DIR/libcrane.so"
    chmod 0755 "$JNI_DIR/libcrane.so"
    echo "staged crane -> $JNI_DIR/libcrane.so"
fi
rm -f "$JNI_DIR/libproot.so" "$JNI_DIR/libproot-loader.so" "$JNI_DIR/libtalloc.so" \
      "$COMPAT_JNI_DIR/libproot.so" "$COMPAT_JNI_DIR/libproot-loader.so" \
      "$COMPAT_JNI_DIR/libtalloc.so"
rm -f "$JNI_DIR/libdocker.so" "$JNI_DIR/libdocker-compose.so" \
      "$COMPAT_JNI_DIR/libdocker.so" "$COMPAT_JNI_DIR/libdocker-compose.so"
# libcow.so is LD_PRELOAD'd inside the *container* rootfs (typically
# glibc — ubuntu/debian — or musl — alpine). A bionic-targeted shim
# fails to load there ("libdl.so" vs "libdl.so.2", ld-android-* vs
# ld-linux-*). Stage the host-glibc build pdockerd ships in lib/
# (built on Termux+PRoot Ubuntu = same glibc as ubuntu containers).
cp "$SUB/lib/libcow.so" "$JNI_DIR/libcow.so"
if [[ "${PDOCKER_WITH_ROOTFS_SHIM:-0}" != "0" && -f "$SUB/lib/pdocker-rootfs-shim.so" ]]; then
    cp "$SUB/lib/pdocker-rootfs-shim.so" "$JNI_DIR/libpdocker-rootfs-shim.so"
else
    rm -f "$JNI_DIR/libpdocker-rootfs-shim.so"
fi
if [[ -n "${PDOCKER_GLIBC_LOADER:-}" && -f "${PDOCKER_GLIBC_LOADER:-}" ]]; then
    cp "$PDOCKER_GLIBC_LOADER" "$JNI_DIR/libpdocker-ld-linux-aarch64.so"
else
    rm -f "$JNI_DIR/libpdocker-ld-linux-aarch64.so"
fi
chmod 0755 "$JNI_DIR/libcow.so"
[[ -f "$JNI_DIR/libpdocker-rootfs-shim.so" ]] && chmod 0755 "$JNI_DIR/libpdocker-rootfs-shim.so"
[[ -f "$JNI_DIR/libpdocker-ld-linux-aarch64.so" ]] && chmod 0755 "$JNI_DIR/libpdocker-ld-linux-aarch64.so"
echo "skipped external proot/talloc/proot-loader/docker-cli/docker-compose payloads"
echo "staged libcow (glibc) -> $JNI_DIR/libcow.so"
[[ -f "$JNI_DIR/libpdocker-rootfs-shim.so" ]] && echo "staged rootfs shim (glibc) -> $JNI_DIR/libpdocker-rootfs-shim.so"
[[ -f "$JNI_DIR/libpdocker-ld-linux-aarch64.so" ]] && echo "staged glibc loader -> $JNI_DIR/libpdocker-ld-linux-aarch64.so"

# --- jniLibs sanity ---
# libpdockerpty.so, libpdockerdirect.so, libpdockergpuexecutor.so, and
# libpdockermediaexecutor.so are built by scripts/build-native-android-ndk.sh.
# libpdockergpushim.so and ICD payloads are Linux/glibc container payloads built
# by scripts/build-gpu-shim.sh. Executable helpers are intentionally named
# lib*.so so Android extracts them to nativeLibraryDir.
for abi in arm64-v8a armeabi-v7a; do
    for lib in libpdockerpty.so libpdockerdirect.so libpdockergpuexecutor.so libpdockermediaexecutor.so libpdockergpushim.so libpdockervulkanicd.so libpdockeropenclicd.so; do
        p="$APP/jniLibs/$abi/$lib"
        if [[ ! -f "$p" ]]; then
            echo "warn: $p missing — run scripts/build-native-android-ndk.sh and scripts/build-gpu-shim.sh first" >&2
        fi
    done
done

echo "copy-native.sh: done"

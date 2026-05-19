#!/usr/bin/env bash
# build-native-android-ndk.sh — build Android/Bionic native helpers with the
# official Android NDK clang from a normal glibc Linux host.
#
# This is the standard, reproducible native-build path for CI/release style
# builds. It intentionally does not use Termux binaries, box64, or Android
# device-local compiler tools.
#
# Produces executable/library payloads under:
#   app/src/main/jniLibs/arm64-v8a/
#
# Android packages extract only lib*.so files, so pdockerdirect,
# pdockergpuexecutor, and pdockermediaexecutor are executable PIE binaries
# deliberately named lib*.so. Kotlin later symlinks them to executable names.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NDK="${ANDROID_NDK_HOME:-${ANDROID_NDK_ROOT:-/root/android-ndk-r26d}}"
HOST_TAG="${ANDROID_NDK_HOST_TAG:-linux-x86_64}"
API="${ANDROID_API:-26}"
ABI="${ANDROID_ABI:-arm64-v8a}"
TARGET_TRIPLE="aarch64-linux-android"
TOOLCHAIN="$NDK/toolchains/llvm/prebuilt/$HOST_TAG"
NDK_CLANG="$TOOLCHAIN/bin/${TARGET_TRIPLE}${API}-clang"
SYSROOT="$TOOLCHAIN/sysroot"
RESOURCE_DIR="$(find "$TOOLCHAIN/lib/clang" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort -V | tail -1)"
STRIP="$TOOLCHAIN/bin/llvm-strip"
JNI_DIR="$ROOT/app/src/main/jniLibs/$ABI"

if [[ "$ABI" != "arm64-v8a" ]]; then
    echo "ABORT: only arm64-v8a is currently supported (got $ABI)" >&2
    exit 2
fi
if [[ ! -x "$NDK_CLANG" ]]; then
    echo "ABORT: NDK clang driver not found: $NDK_CLANG" >&2
    echo "       Set ANDROID_NDK_HOME and, if needed, ANDROID_NDK_HOST_TAG." >&2
    exit 1
fi
if [[ ! -d "$SYSROOT/usr/include" ]]; then
    echo "ABORT: NDK sysroot missing: $SYSROOT" >&2
    exit 1
fi

CLANG_CMD=("$NDK_CLANG")
CLANG_LABEL="$NDK_CLANG"
EXTRA_TOOLCHAIN_FLAGS=()
if ! "$NDK_CLANG" --version >/dev/null 2>&1; then
    # Some local development machines are aarch64 glibc hosts while the
    # installed official NDK prebuilt is linux-x86_64. In that case use the
    # host glibc clang, but keep the NDK sysroot, Android target, and NDK
    # compiler-rt resource directory. This is the aarch64 glibc host-clang
    # mode, not a Termux/device-local fallback.
    HOST_CLANG="${HOST_CLANG:-$(command -v clang || true)}"
    if [[ -z "$HOST_CLANG" || ! -x "$HOST_CLANG" ]]; then
        echo "ABORT: NDK clang is not executable on this host and host clang was not found" >&2
        exit 1
    fi
    if [[ -z "$RESOURCE_DIR" || ! -d "$RESOURCE_DIR" ]]; then
        echo "ABORT: NDK compiler-rt resource directory not found under $TOOLCHAIN/lib/clang" >&2
        exit 1
    fi
    if ! command -v ld.lld >/dev/null 2>&1; then
        echo "ABORT: aarch64 glibc host-clang mode requires ld.lld on PATH" >&2
        echo "       Install lld or use an executable NDK prebuilt for this host." >&2
        exit 1
    fi
    CLANG_CMD=("$HOST_CLANG")
    CLANG_LABEL="$HOST_CLANG --target=${TARGET_TRIPLE}${API} --sysroot=$SYSROOT -resource-dir=$RESOURCE_DIR"
    EXTRA_TOOLCHAIN_FLAGS=(
        "--target=${TARGET_TRIPLE}${API}"
        "--sysroot=$SYSROOT"
        "-resource-dir=$RESOURCE_DIR"
        "-rtlib=compiler-rt"
        "-L$SYSROOT/usr/lib/$TARGET_TRIPLE/$API"
    )
fi

mkdir -p "$JNI_DIR"

COMMON_WARNINGS=(
    -Wall -Wextra -Wno-unused-parameter -Wno-unused-function
)
COMMON_CFLAGS=(
    -fPIC -O2
    "${COMMON_WARNINGS[@]}"
    -U_FORTIFY_SOURCE
)
LIB_FLAGS=(
    "${COMMON_CFLAGS[@]}"
    -shared
)
EXEC_FLAGS=(
    -fPIE -pie -O2
    "${COMMON_WARNINGS[@]}"
    -U_FORTIFY_SOURCE
)

strip_if_requested() {
    local path="$1"
    if [[ "${PDOCKER_NATIVE_STRIP:-0}" == "1" ]]; then
        [[ -x "$STRIP" ]] || { echo "ABORT: llvm-strip not found: $STRIP" >&2; exit 1; }
        "$STRIP" --strip-unneeded "$path"
    fi
}

show_elf() {
    local path="$1"
    file "$path" | head -1
    if command -v readelf >/dev/null 2>&1; then
        readelf -h "$path" | awk '/Class:|Machine:|Type:/{gsub(/^ +/, ""); print "  " $0}'
    fi
}

build_shared() {
    local out="$1"
    shift
    echo "==> building $(basename "$out")"
    "${CLANG_CMD[@]}" "${EXTRA_TOOLCHAIN_FLAGS[@]}" "${LIB_FLAGS[@]}" -o "$out" "$@"
    strip_if_requested "$out"
    show_elf "$out"
}

build_exec() {
    local out="$1"
    shift
    echo "==> building executable payload $(basename "$out")"
    "${CLANG_CMD[@]}" "${EXTRA_TOOLCHAIN_FLAGS[@]}" "${EXEC_FLAGS[@]}" -o "$out" "$@"
    strip_if_requested "$out"
    chmod 0755 "$out"
    show_elf "$out"
}

echo "==> compiler: $CLANG_LABEL"
"${CLANG_CMD[@]}" --version | head -1
echo "==> API: android$API ABI: $ABI host-tag: $HOST_TAG"
echo

build_shared "$JNI_DIR/libpdockerpty.so" \
    "$ROOT/app/src/main/cpp/pty.c" \
    -llog

build_exec "$JNI_DIR/libpdockerdirect.so" \
    "$ROOT/app/src/main/cpp/pdocker_direct_exec.c"

build_exec "$JNI_DIR/libpdockergpuexecutor.so" \
    "$ROOT/app/src/main/cpp/pdocker_gpu_executor.c" \
    -lEGL -lGLESv3 -lvulkan -llog -ldl -lm

build_exec "$JNI_DIR/libpdockermediaexecutor.so" \
    "$ROOT/app/src/main/cpp/pdocker_media_executor.c"

echo
echo "==> Android/Bionic native helpers ready:"
ls -la "$JNI_DIR"/libpdocker{pty,direct,gpuexecutor,mediaexecutor}.so

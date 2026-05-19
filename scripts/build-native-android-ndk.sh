#!/usr/bin/env bash
# build-native-android-ndk.sh — build Android/Bionic native helpers with a
# glibc-host Android toolchain configuration.
#
# Standard x86_64 Linux hosts use the official NDK clang driver directly.
# aarch64 Linux hosts currently do not have a Google-distributed linux-aarch64
# NDK prebuilt in this workspace, so they use host glibc clang with the NDK
# target, sysroot, and compiler-rt. That mode is explicit and does not use
# Termux or Android device-local compiler tools.
#
# Default ABIs:
#   arm64-v8a    full current direct runtime helper set
#   armeabi-v7a  packaged compatibility helper set; pdocker-direct is an
#                explicit unsupported-ABI executable until the ptrace/syscall
#                layer is ported to 32-bit ARM.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NDK="${ANDROID_NDK_HOME:-${ANDROID_NDK_ROOT:-/root/android-ndk-r26d}}"
API="${ANDROID_API:-26}"
if [[ -n "${ANDROID_ABI:-}" ]]; then
    ABIS=("$ANDROID_ABI")
else
    # shellcheck disable=SC2206
    ABIS=(${ANDROID_ABIS:-arm64-v8a armeabi-v7a})
fi

pick_host_tag() {
    if [[ -n "${ANDROID_NDK_HOST_TAG:-}" ]]; then
        printf '%s\n' "$ANDROID_NDK_HOST_TAG"
        return 0
    fi
    local os arch candidate
    os="$(uname -s)"
    arch="$(uname -m)"
    case "$os:$arch" in
        Linux:x86_64|Linux:amd64) candidate="linux-x86_64" ;;
        Darwin:x86_64) candidate="darwin-x86_64" ;;
        Darwin:arm64) candidate="darwin-x86_64" ;; # official NDK host tag used by older NDKs
        *) candidate="" ;;
    esac
    if [[ -n "$candidate" && -d "$NDK/toolchains/llvm/prebuilt/$candidate" ]]; then
        printf '%s\n' "$candidate"
        return 0
    fi
    find "$NDK/toolchains/llvm/prebuilt" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' 2>/dev/null | sort | head -1
}

abi_target_triple() {
    case "$1" in
        arm64-v8a) printf 'aarch64-linux-android' ;;
        armeabi-v7a) printf 'armv7a-linux-androideabi' ;;
        *) echo "ABORT: unsupported Android ABI '$1'" >&2; return 2 ;;
    esac
}

abi_sysroot_lib_triple() {
    case "$1" in
        arm64-v8a) printf 'aarch64-linux-android' ;;
        armeabi-v7a) printf 'arm-linux-androideabi' ;;
        *) echo "ABORT: unsupported Android ABI '$1'" >&2; return 2 ;;
    esac
}

abi_direct_source() {
    case "$1" in
        arm64-v8a) printf '%s/app/src/main/cpp/pdocker_direct_exec.c' "$ROOT" ;;
        armeabi-v7a) printf '%s/app/src/main/cpp/pdocker_direct_unsupported.c' "$ROOT" ;;
        *) return 2 ;;
    esac
}

HOST_TAG="$(pick_host_tag)"
TOOLCHAIN="$NDK/toolchains/llvm/prebuilt/$HOST_TAG"
SYSROOT="$TOOLCHAIN/sysroot"
RESOURCE_DIR="$(find "$TOOLCHAIN/lib/clang" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort -V | tail -1)"
STRIP="$TOOLCHAIN/bin/llvm-strip"

if [[ ! -d "$TOOLCHAIN" ]]; then
    echo "ABORT: NDK toolchain host directory not found: $TOOLCHAIN" >&2
    exit 1
fi
if [[ ! -d "$SYSROOT/usr/include" ]]; then
    echo "ABORT: NDK sysroot missing: $SYSROOT" >&2
    exit 1
fi

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

show_elf() {
    local path="$1"
    file "$path" | head -1
    if command -v readelf >/dev/null 2>&1; then
        readelf -h "$path" | awk '/Class:|Machine:|Type:/{gsub(/^ +/, ""); print "  " $0}'
    fi
}

strip_if_requested() {
    local path="$1"
    if [[ "${PDOCKER_NATIVE_STRIP:-0}" == "1" ]]; then
        if [[ -x "$STRIP" ]] && "$STRIP" --version >/dev/null 2>&1; then
            "$STRIP" --strip-unneeded "$path"
        else
            local host_strip="${HOST_LLVM_STRIP:-$(command -v llvm-strip || true)}"
            [[ -n "$host_strip" && -x "$host_strip" ]] || { echo "ABORT: llvm-strip not executable for host" >&2; exit 1; }
            "$host_strip" --strip-unneeded "$path"
        fi
    fi
}

setup_compiler() {
    local abi="$1" target="$2" lib_triple="$3"
    local ndk_clang="$TOOLCHAIN/bin/${target}${API}-clang"
    local -n out_cmd_ref="$4"
    local -n out_extra_ref="$5"
    local -n out_label_ref="$6"

    if [[ ! -x "$ndk_clang" ]]; then
        echo "ABORT: NDK clang driver not found: $ndk_clang" >&2
        exit 1
    fi

    out_cmd_ref=("$ndk_clang")
    out_extra_ref=()
    out_label_ref="$ndk_clang"
    if ! "$ndk_clang" --version >/dev/null 2>&1; then
        local host_clang="${HOST_CLANG:-$(command -v clang || true)}"
        if [[ -z "$host_clang" || ! -x "$host_clang" ]]; then
            echo "ABORT: NDK clang is not executable on this host and host clang was not found" >&2
            exit 1
        fi
        if [[ -z "$RESOURCE_DIR" || ! -d "$RESOURCE_DIR" ]]; then
            echo "ABORT: NDK compiler-rt resource directory not found under $TOOLCHAIN/lib/clang" >&2
            exit 1
        fi
        if ! command -v ld.lld >/dev/null 2>&1; then
            echo "ABORT: host-clang mode for $abi requires ld.lld on PATH" >&2
            exit 1
        fi
        out_cmd_ref=("$host_clang")
        out_extra_ref=(
            "--target=${target}${API}"
            "--sysroot=$SYSROOT"
            "-resource-dir=$RESOURCE_DIR"
            "-rtlib=compiler-rt"
            "-L$SYSROOT/usr/lib/$lib_triple/$API"
        )
        out_label_ref="$host_clang --target=${target}${API} --sysroot=$SYSROOT -resource-dir=$RESOURCE_DIR"
    fi
}

build_shared() {
    local out="$1"; shift
    local -n cmd_ref="$1"; shift
    local -n extra_ref="$1"; shift
    echo "==> building $(basename "$out")"
    "${cmd_ref[@]}" "${extra_ref[@]}" "${LIB_FLAGS[@]}" -o "$out" "$@"
    strip_if_requested "$out"
    show_elf "$out"
}

build_exec() {
    local out="$1"; shift
    local -n cmd_ref="$1"; shift
    local -n extra_ref="$1"; shift
    echo "==> building executable payload $(basename "$out")"
    "${cmd_ref[@]}" "${extra_ref[@]}" "${EXEC_FLAGS[@]}" -o "$out" "$@"
    strip_if_requested "$out"
    chmod 0755 "$out"
    show_elf "$out"
}

echo "==> NDK: $NDK"
echo "==> host-tag: $HOST_TAG API: android$API ABIs: ${ABIS[*]}"
echo

for ABI in "${ABIS[@]}"; do
    TARGET_TRIPLE="$(abi_target_triple "$ABI")"
    LIB_TRIPLE="$(abi_sysroot_lib_triple "$ABI")"
    JNI_DIR="$ROOT/app/src/main/jniLibs/$ABI"
    mkdir -p "$JNI_DIR"

    CLANG_CMD=()
    EXTRA_TOOLCHAIN_FLAGS=()
    CLANG_LABEL=""
    setup_compiler "$ABI" "$TARGET_TRIPLE" "$LIB_TRIPLE" CLANG_CMD EXTRA_TOOLCHAIN_FLAGS CLANG_LABEL

    echo "==> ABI: $ABI"
    echo "==> compiler: $CLANG_LABEL"
    "${CLANG_CMD[@]}" --version | head -1

    build_shared "$JNI_DIR/libpdockerpty.so" CLANG_CMD EXTRA_TOOLCHAIN_FLAGS \
        "$ROOT/app/src/main/cpp/pty.c" \
        -llog

    build_exec "$JNI_DIR/libpdockerdirect.so" CLANG_CMD EXTRA_TOOLCHAIN_FLAGS \
        "$(abi_direct_source "$ABI")"

    build_exec "$JNI_DIR/libpdockergpuexecutor.so" CLANG_CMD EXTRA_TOOLCHAIN_FLAGS \
        "$ROOT/app/src/main/cpp/pdocker_gpu_executor.c" \
        -lEGL -lGLESv3 -lvulkan -llog -ldl -lm

    build_exec "$JNI_DIR/libpdockermediaexecutor.so" CLANG_CMD EXTRA_TOOLCHAIN_FLAGS \
        "$ROOT/app/src/main/cpp/pdocker_media_executor.c"

    echo "==> Android/Bionic native helpers ready for $ABI:"
    ls -la "$JNI_DIR"/libpdocker{pty,direct,gpuexecutor,mediaexecutor}.so
    echo
done

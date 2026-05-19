#!/usr/bin/env bash
# Build Linux/glibc container-facing GPU components.
#
# These are not Android/Bionic binaries. They are packaged as native payloads
# only so the APK can extract them, then pdockerd bind-mounts the matching
# Linux/glibc payload into containers. The OpenCL shim is exposed both as an ICD
# vendor library and as libOpenCL.so for images that link directly.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC_DIR="$ROOT/docker-proot-setup/src/gpu"
ARCHES="${PDOCKER_GLIBC_ARCHES:-arm64 armhf}"

arch_cc() {
    case "$1" in
        arm64) printf '%s\n' "${CC_ARM64:-${CC:-aarch64-linux-gnu-gcc}}" ;;
        armhf) printf '%s\n' "${CC_ARMHF:-arm-linux-gnueabihf-gcc}" ;;
        *) echo "ABORT: unsupported glibc GPU shim arch '$1'" >&2; return 2 ;;
    esac
}

arch_jni_abi() {
    case "$1" in
        arm64) printf 'arm64-v8a\n' ;;
        armhf) printf 'armeabi-v7a\n' ;;
        *) return 2 ;;
    esac
}

arch_expected_file_marker() {
    case "$1" in
        arm64) printf 'ARM aarch64\n' ;;
        armhf) printf 'ARM\n' ;;
        *) return 2 ;;
    esac
}

verify_elf_arch() {
    local arch="$1" path="$2" marker out
    marker="$(arch_expected_file_marker "$arch")"
    out="$(file "$path")"
    printf '%s\n' "$out"
    if [[ "$out" != *"$marker"* ]]; then
        echo "ABORT: $path was not built for expected $arch marker '$marker'" >&2
        exit 1
    fi
    if [[ "$arch" == "armhf" && "$out" != *"ELF 32-bit"* ]]; then
        echo "ABORT: $path is not ELF 32-bit for armhf" >&2
        exit 1
    fi
    if [[ "$arch" == "arm64" && "$out" != *"ELF 64-bit"* ]]; then
        echo "ABORT: $path is not ELF 64-bit for arm64" >&2
        exit 1
    fi
}

build_arch() {
    local arch="$1" cc abi out_dir out icd_out opencl_out
    cc="$(arch_cc "$arch")"
    abi="$(arch_jni_abi "$arch")"
    out_dir="$ROOT/app/src/main/jniLibs/$abi"
    out="$out_dir/libpdockergpushim.so"
    icd_out="$out_dir/libpdockervulkanicd.so"
    opencl_out="$out_dir/libpdockeropenclicd.so"

    if ! command -v "$cc" >/dev/null 2>&1; then
        echo "ABORT: missing $arch Linux/glibc cross compiler '$cc'" >&2
        echo "       Install the compiler or set CC_ARM64/CC_ARMHF/PDOCKER_GLIBC_ARCHES." >&2
        exit 1
    fi

    mkdir -p "$out_dir" "$ROOT/docker-proot-setup/lib"
    echo "==> building Linux/glibc GPU payloads for $arch ($abi) with $cc"
    "$cc" -O2 -fPIE -pie -Wall -Wextra \
        -o "$out" \
        "$SRC_DIR/pdocker_gpu_shim.c"
    chmod 0755 "$out"
    verify_elf_arch "$arch" "$out"

    "$cc" -O2 -fPIC -shared -Wall -Wextra \
        -Wl,-Bsymbolic \
        -o "$icd_out" \
        "$SRC_DIR/pdocker_vulkan_icd.c"
    chmod 0755 "$icd_out"
    verify_elf_arch "$arch" "$icd_out"

    "$cc" -O2 -fPIC -shared -Wall -Wextra \
        -Wl,-Bsymbolic \
        -o "$opencl_out" \
        "$SRC_DIR/pdocker_opencl_icd.c"
    chmod 0755 "$opencl_out"
    verify_elf_arch "$arch" "$opencl_out"

    # The current backend runtime consumes the arm64 names from docker-proot-setup/lib.
    # Keep armhf packaged in jniLibs until the ARM32 container runtime is promoted.
    if [[ "$arch" == "arm64" ]]; then
        cp "$out" "$ROOT/docker-proot-setup/lib/pdocker-gpu-shim"
        cp "$icd_out" "$ROOT/docker-proot-setup/lib/pdocker-vulkan-icd.so"
        cp "$opencl_out" "$ROOT/docker-proot-setup/lib/pdocker-opencl-icd.so"
        chmod 0755 \
            "$ROOT/docker-proot-setup/lib/pdocker-gpu-shim" \
            "$ROOT/docker-proot-setup/lib/pdocker-vulkan-icd.so" \
            "$ROOT/docker-proot-setup/lib/pdocker-opencl-icd.so"
    fi
}

for arch in $ARCHES; do
    build_arch "$arch"
done

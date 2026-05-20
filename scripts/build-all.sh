#!/usr/bin/env bash
# Coherent local build entrypoint for pdocker-android.
#
# Default flow:
#   compat APK + Android native helpers + glibc GPU shim/ICDs.
# It intentionally delegates to the existing setup/build scripts instead of
# downloading any new build inputs.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

FLAVOR="compat"
BUILD_TYPE="${PDOCKER_ANDROID_BUILD_TYPE:-debug}"
DO_NATIVE=0
DO_GPU_SHIM=0
DO_APK=0
DO_VERIFY_FAST=0
DRY_RUN=0
STEP_FLAG_SEEN=0

usage() {
    cat <<'USAGE'
Usage: bash scripts/build-all.sh [options]

Default:
  bash scripts/build-all.sh
    Builds compat Android native libs, glibc GPU shim/ICDs, and the compat APK.

Build selection:
  --native        Build Android/Bionic native helper libs only.
  --gpu-shim      Build Linux/glibc GPU shim, Vulkan ICD, and OpenCL ICD only.
  --apk           Build/package the selected APK from already-built payloads.
  --verify-fast   Run scripts/verify-fast.sh after selected/default build steps.

Flavor:
  --compat        Build the process-exec compat flavor. This is the default.
  --modern        Build the API 29+ metadata-only flavor explicitly.

Other:
  --build-type T  Gradle build type: debug or release. Default: debug.
  --dry-run       Print commands and freshness checks without running builds.
  -h, --help      Show this help.

Examples:
  bash scripts/build-all.sh
  bash scripts/build-all.sh --verify-fast
  bash scripts/build-all.sh --modern --apk
  bash scripts/build-all.sh --gpu-shim --dry-run
USAGE
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --native)
            DO_NATIVE=1
            STEP_FLAG_SEEN=1
            ;;
        --gpu-shim)
            DO_GPU_SHIM=1
            STEP_FLAG_SEEN=1
            ;;
        --apk)
            DO_APK=1
            STEP_FLAG_SEEN=1
            ;;
        --verify-fast)
            DO_VERIFY_FAST=1
            ;;
        --compat)
            FLAVOR="compat"
            ;;
        --modern)
            FLAVOR="modern"
            ;;
        --build-type)
            [[ $# -ge 2 ]] || { echo "ABORT: --build-type requires debug or release" >&2; exit 2; }
            BUILD_TYPE="$2"
            shift
            ;;
        --dry-run)
            DRY_RUN=1
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "ABORT: unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
    shift
done

case "$FLAVOR" in
    compat|modern) ;;
    *) echo "ABORT: flavor must be compat or modern (got '$FLAVOR')" >&2; exit 2 ;;
esac

case "$BUILD_TYPE" in
    debug|release) ;;
    *) echo "ABORT: build type must be debug or release (got '$BUILD_TYPE')" >&2; exit 2 ;;
esac

if [[ "$STEP_FLAG_SEEN" == "0" ]]; then
    DO_NATIVE=1
    DO_GPU_SHIM=1
    DO_APK=1
fi

run() {
    printf '\n==> %s\n' "$*"
    if [[ "$DRY_RUN" == "0" ]]; then
        "$@"
    fi
}

require_file() {
    local path="$1"
    if [[ ! -f "$path" ]]; then
        echo "ABORT: missing required build input/output: $path" >&2
        return 1
    fi
}

require_cmd() {
    local cmd="$1"
    if ! command -v "$cmd" >/dev/null 2>&1; then
        echo "ABORT: missing command '$cmd'. Run scripts/setup-env.sh or install the existing project dependency first." >&2
        return 1
    fi
}

require_gpu_shim_compilers() {
    local arch cc
    for arch in ${PDOCKER_GLIBC_ARCHES:-arm64 armhf}; do
        case "$arch" in
            arm64) cc="${CC_ARM64:-${CC:-aarch64-linux-gnu-gcc}}" ;;
            armhf) cc="${CC_ARMHF:-arm-linux-gnueabihf-gcc}" ;;
            *)
                echo "ABORT: unsupported PDOCKER_GLIBC_ARCHES entry '$arch'" >&2
                return 1
                ;;
        esac
        require_cmd "$cc"
    done
}

require_fresh() {
    local output="$1"
    shift
    require_file "$output"
    local source
    for source in "$@"; do
        require_file "$source"
        if [[ "$source" -nt "$output" ]]; then
            echo "ABORT: stale build output: $output" >&2
            echo "       newer source: $source" >&2
            return 1
        fi
    done
}

preflight() {
    require_file "./gradlew"
    if [[ "$DRY_RUN" == "1" ]]; then
        return 0
    fi
    if [[ "$DO_APK" == "1" ]]; then
        require_cmd javac
        require_file "${ANDROID_HOME:-$HOME/android-sdk}/cmdline-tools/latest/bin/sdkmanager"
        local ndk_root="${ANDROID_NDK_HOME:-$HOME/android-ndk-r26d}"
        if [[ ! -d "$ndk_root/toolchains/llvm/prebuilt" ]]; then
            echo "ABORT: NDK toolchain directory missing: $ndk_root/toolchains/llvm/prebuilt" >&2
            return 1
        fi
    fi
    if [[ "$DO_NATIVE" == "1" ]]; then
        local ndk_root="${ANDROID_NDK_HOME:-$HOME/android-ndk-r26d}"
        if [[ ! -d "$ndk_root/toolchains/llvm/prebuilt" ]]; then
            echo "ABORT: NDK toolchain directory missing: $ndk_root/toolchains/llvm/prebuilt" >&2
            return 1
        fi
    fi
    if [[ "$DO_GPU_SHIM" == "1" ]]; then
        require_gpu_shim_compilers
    fi
}

check_android_native_fresh() {
    local abi jni direct_source
    for abi in arm64-v8a armeabi-v7a; do
        jni="$ROOT/app/src/main/jniLibs/$abi"
        direct_source="$ROOT/app/src/main/cpp/pdocker_direct_exec.c"
        if [[ "$abi" == "armeabi-v7a" ]]; then
            direct_source="$ROOT/app/src/main/cpp/pdocker_direct_unsupported.c"
        fi
        require_fresh "$jni/libpdockerpty.so" "$ROOT/app/src/main/cpp/pty.c"
        require_fresh "$jni/libpdockerdirect.so" "$direct_source"
        require_fresh "$jni/libpdockergpuexecutor.so" "$ROOT/app/src/main/cpp/pdocker_gpu_executor.c"
        require_fresh "$jni/libpdockermediaexecutor.so" "$ROOT/app/src/main/cpp/pdocker_media_executor.c"
    done
}

check_gpu_shim_fresh() {
    local abi jni
    local gpu="$ROOT/docker-proot-setup/src/gpu"
    for abi in arm64-v8a armeabi-v7a; do
        jni="$ROOT/app/src/main/jniLibs/$abi"
        require_fresh "$jni/libpdockergpushim.so" "$gpu/pdocker_gpu_shim.c"
        require_fresh "$jni/libpdockervulkanicd.so" "$gpu/pdocker_vulkan_icd.c"
        require_fresh "$jni/libpdockeropenclicd.so" "$gpu/pdocker_opencl_icd.c"
    done
    require_fresh "$ROOT/docker-proot-setup/lib/pdocker-gpu-shim" "$gpu/pdocker_gpu_shim.c"
    require_fresh "$ROOT/docker-proot-setup/lib/pdocker-vulkan-icd.so" "$gpu/pdocker_vulkan_icd.c"
    require_fresh "$ROOT/docker-proot-setup/lib/pdocker-opencl-icd.so" "$gpu/pdocker_opencl_icd.c"
}

check_backend_payload_inputs() {
    require_file "$ROOT/docker-proot-setup/bin/pdockerd"
    require_file "$ROOT/docker-proot-setup/docker-bin/crane"
    require_file "$ROOT/docker-proot-setup/lib/libcow.so"
}

preflight

echo "build-all: flavor=$FLAVOR build_type=$BUILD_TYPE native_backend=ndk dry_run=$DRY_RUN"
echo "build-all: steps native=$DO_NATIVE gpu_shim=$DO_GPU_SHIM apk=$DO_APK verify_fast=$DO_VERIFY_FAST"

if [[ "$DO_NATIVE" == "1" ]]; then
    run bash scripts/build-native-android-ndk.sh
    if [[ "$DRY_RUN" == "0" ]]; then
        check_android_native_fresh
    else
        echo "dry-run: would check Android native helper freshness"
    fi
fi

if [[ "$DO_GPU_SHIM" == "1" ]]; then
    run bash scripts/build-gpu-shim.sh
    if [[ "$DRY_RUN" == "0" ]]; then
        check_gpu_shim_fresh
    else
        echo "dry-run: would check glibc GPU shim/ICD freshness"
    fi
fi

if [[ "$DO_APK" == "1" ]]; then
    if [[ "$DRY_RUN" == "0" ]]; then
        if [[ "$DO_NATIVE" == "0" ]]; then
            check_android_native_fresh
        fi
        if [[ "$DO_GPU_SHIM" == "0" ]]; then
            check_gpu_shim_fresh
        fi
        check_backend_payload_inputs
        PDOCKER_ANDROID_FLAVOR="$FLAVOR" \
        PDOCKER_ANDROID_BUILD_TYPE="$BUILD_TYPE" \
        PDOCKER_SKIP_NATIVE_BUILD=1 \
            bash scripts/build-apk.sh
    else
        echo "dry-run: PDOCKER_ANDROID_FLAVOR=$FLAVOR PDOCKER_ANDROID_BUILD_TYPE=$BUILD_TYPE PDOCKER_SKIP_NATIVE_BUILD=1 bash scripts/build-apk.sh"
    fi
fi

if [[ "$DO_VERIFY_FAST" == "1" ]]; then
    run env PDOCKER_ANDROID_FLAVOR="$FLAVOR" bash scripts/verify-fast.sh
fi

echo
echo "build-all: done"

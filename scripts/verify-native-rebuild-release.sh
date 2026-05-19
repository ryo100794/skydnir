#!/usr/bin/env bash
# verify-native-rebuild-release.sh — clean selected generated native outputs,
# rebuild the release candidate native payload chain, assemble the compat APK,
# and verify packaged native payload architecture/inclusion.
#
# Safety: this script is a dry-run by default. Set
#   PDOCKER_NATIVE_REBUILD_EXECUTE=1
# to remove generated outputs and run the rebuild.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    cat <<'USAGE'
Usage:
  bash scripts/verify-native-rebuild-release.sh
  PDOCKER_NATIVE_REBUILD_EXECUTE=1 bash scripts/verify-native-rebuild-release.sh

Environment:
  PDOCKER_NATIVE_REBUILD_EXECUTE=1        Actually clean/rebuild/verify. Default: dry-run.
  PDOCKER_NATIVE_REBUILD_BUILD_TYPE=debug Build type for compat APK: debug or release.
                                         Default: $PDOCKER_ANDROID_BUILD_TYPE or debug.
  PDOCKER_NATIVE_REBUILD_UTC=STAMP        Override UTC report stamp for tests/re-runs.

The script writes logs and verification artifacts under:
  build/reports/native-rebuild-<UTC>/
USAGE
    exit 0
fi

EXECUTE="${PDOCKER_NATIVE_REBUILD_EXECUTE:-0}"
BUILD_TYPE="${PDOCKER_NATIVE_REBUILD_BUILD_TYPE:-${PDOCKER_ANDROID_BUILD_TYPE:-debug}}"
case "$BUILD_TYPE" in
    debug|release) ;;
    *)
        echo "ABORT: build type must be 'debug' or 'release' (got '$BUILD_TYPE')" >&2
        exit 2
        ;;
esac
CAP_BUILD_TYPE="$(tr '[:lower:]' '[:upper:]' <<< "${BUILD_TYPE:0:1}")${BUILD_TYPE:1}"
GRADLE_TASK=":app:assembleCompat${CAP_BUILD_TYPE}"
APK="$ROOT/app/build/outputs/apk/compat/$BUILD_TYPE/app-compat-$BUILD_TYPE.apk"
UNSIGNED_APK=""
if [[ "$BUILD_TYPE" == "release" ]]; then
    UNSIGNED_APK="${APK%.apk}-unsigned.apk"
fi
UTC_STAMP="${PDOCKER_NATIVE_REBUILD_UTC:-$(date -u +%Y%m%dT%H%M%SZ)}"
REPORT_DIR="$ROOT/build/reports/native-rebuild-$UTC_STAMP"
mkdir -p "$REPORT_DIR"
SUMMARY_LOG="$REPORT_DIR/summary.log"
PLAN_LOG="$REPORT_DIR/plan.log"
: > "$SUMMARY_LOG"

log() {
    printf '%s\n' "$*" | tee -a "$SUMMARY_LOG"
}

relpath() {
    local path="$1"
    if [[ "$path" == "$ROOT/"* ]]; then
        printf '%s\n' "${path#$ROOT/}"
    else
        printf '%s\n' "$path"
    fi
}

CLEAN_PATHS=()
add_clean_path() {
    CLEAN_PATHS+=("$ROOT/$1")
}

ANDROID_HELPERS=(
    libpdockerpty.so
    libpdockerdirect.so
    libpdockergpuexecutor.so
    libpdockermediaexecutor.so
)
GLIBC_GPU_PAYLOADS=(
    libpdockergpushim.so
    libpdockervulkanicd.so
    libpdockeropenclicd.so
)
STAGED_ARM64_PAYLOADS=(
    libcrane.so
    libcow.so
    libpdocker-rootfs-shim.so
    libpdocker-ld-linux-aarch64.so
)
REMOVED_LEGACY_PAYLOADS=(
    libproot.so
    libproot-loader.so
    libtalloc.so
    libdocker.so
    libdocker-compose.so
)

for abi in arm64-v8a armeabi-v7a; do
    for lib in "${ANDROID_HELPERS[@]}" "${GLIBC_GPU_PAYLOADS[@]}"; do
        add_clean_path "app/src/main/jniLibs/$abi/$lib"
    done
done
for lib in "${STAGED_ARM64_PAYLOADS[@]}" "${REMOVED_LEGACY_PAYLOADS[@]}"; do
    add_clean_path "app/src/main/jniLibs/arm64-v8a/$lib"
done
for lib in "${REMOVED_LEGACY_PAYLOADS[@]}"; do
    add_clean_path "app/src/compat/jniLibs/arm64-v8a/$lib"
done
add_clean_path "app/src/main/assets/pdockerd/pdockerd"
add_clean_path "docker-proot-setup/lib/pdocker-gpu-shim"
add_clean_path "docker-proot-setup/lib/pdocker-vulkan-icd.so"
add_clean_path "docker-proot-setup/lib/pdocker-opencl-icd.so"
add_clean_path "app/build/outputs/apk/compat/$BUILD_TYPE/app-compat-$BUILD_TYPE.apk"
if [[ -n "$UNSIGNED_APK" ]]; then
    CLEAN_PATHS+=("$UNSIGNED_APK")
fi

write_plan() {
    {
        printf 'schema: pdocker.native-rebuild-release.v1\n'
        printf 'utc: %s\n' "$UTC_STAMP"
        printf 'mode: %s\n' "$([[ "$EXECUTE" == "1" ]] && printf execute || printf dry-run)"
        printf 'build_type: %s\n' "$BUILD_TYPE"
        printf 'gradle_task: %s\n' "$GRADLE_TASK"
        printf 'apk: %s\n' "$(relpath "$APK")"
        if [[ -n "$UNSIGNED_APK" ]]; then
            printf 'unsigned_apk_fallback: %s\n' "$(relpath "$UNSIGNED_APK")"
        fi
        printf '\nclean_paths:\n'
        local path
        for path in "${CLEAN_PATHS[@]}"; do
            if [[ -e "$path" ]]; then
                printf '  - %s [exists]\n' "$(relpath "$path")"
            else
                printf '  - %s [absent]\n' "$(relpath "$path")"
            fi
        done
        printf '\ncommands:\n'
        printf '  - bash scripts/build-native-android-ndk.sh\n'
        printf '  - bash scripts/build-gpu-shim.sh\n'
        printf '  - bash scripts/copy-native.sh\n'
        printf '  - ./gradlew %q --no-daemon\n' "$GRADLE_TASK"
        printf '  - python3 scripts/verify-native-payloads.py --apk <apk> --apk-arm64-only --write-artifact %s\n' "$(relpath "$REPORT_DIR/native-payloads.json")"
    } > "$PLAN_LOG"
}

clean_outputs() {
    local log_file="$REPORT_DIR/clean.log"
    : > "$log_file"
    local path
    for path in "${CLEAN_PATHS[@]}"; do
        if [[ -e "$path" || -L "$path" ]]; then
            printf 'rm -f %s\n' "$(relpath "$path")" | tee -a "$log_file"
            rm -f -- "$path"
        else
            printf 'skip missing %s\n' "$(relpath "$path")" | tee -a "$log_file"
        fi
    done
}

run_step() {
    local name="$1"
    shift
    local log_file="$REPORT_DIR/$name.log"
    log ""
    log "==> $name: $*"
    (
        set -x
        "$@"
    ) > >(tee "$log_file") 2>&1
}

write_plan
log "native rebuild report: $REPORT_DIR"
log "plan: $(relpath "$PLAN_LOG")"

if [[ "$EXECUTE" != "1" ]]; then
    log "DRY-RUN: no files were removed and no build commands were executed."
    log "Set PDOCKER_NATIVE_REBUILD_EXECUTE=1 to run the clean rebuild verifier."
    cat "$PLAN_LOG"
    exit 0
fi

export ANDROID_HOME="${ANDROID_HOME:-$HOME/android-sdk}"
export ANDROID_NDK_HOME="${ANDROID_NDK_HOME:-${ANDROID_NDK_ROOT:-$HOME/android-ndk-r26d}}"
export PATH="$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools:$PATH"
export PDOCKER_ANDROID_FLAVOR=compat
export PDOCKER_ANDROID_BUILD_TYPE="$BUILD_TYPE"

log "EXECUTE=1: cleaning selected generated native outputs."
clean_outputs
run_step build-native-android-ndk bash scripts/build-native-android-ndk.sh
run_step build-gpu-shim bash scripts/build-gpu-shim.sh
run_step copy-native bash scripts/copy-native.sh
run_step gradle-assemble-compat ./gradlew "$GRADLE_TASK" --no-daemon

VERIFY_APK="$APK"
if [[ ! -f "$VERIFY_APK" && -n "$UNSIGNED_APK" && -f "$UNSIGNED_APK" ]]; then
    VERIFY_APK="$UNSIGNED_APK"
fi
if [[ ! -f "$VERIFY_APK" ]]; then
    log "ABORT: APK missing after build: $(relpath "$APK")"
    if [[ -n "$UNSIGNED_APK" ]]; then
        log "ABORT: unsigned fallback also missing: $(relpath "$UNSIGNED_APK")"
    fi
    exit 1
fi
run_step verify-native-payloads python3 scripts/verify-native-payloads.py \
    --apk "$VERIFY_APK" \
    --apk-arm64-only \
    --write-artifact "$REPORT_DIR/native-payloads.json"

log ""
log "native rebuild verifier: PASS"
log "APK: $(relpath "$VERIFY_APK")"
log "artifact: $(relpath "$REPORT_DIR/native-payloads.json")"

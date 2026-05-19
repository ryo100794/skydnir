#!/usr/bin/env bash
# build-apk.sh — end-to-end Android APK build from an aarch64 shell.
# Expects: JDK 17, gradle, Android cmdline-tools, NDK r26d.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

: "${ANDROID_HOME:=$HOME/android-sdk}"
: "${ANDROID_NDK_HOME:=$HOME/android-ndk-r26d}"
: "${PDOCKER_ANDROID_FLAVOR:=compat}"
: "${PDOCKER_ANDROID_BUILD_TYPE:=debug}"
: "${PDOCKER_SKIP_NATIVE_BUILD:=0}"
: "${PDOCKER_NATIVE_BUILD_BACKEND:=ndk}"
export ANDROID_HOME ANDROID_NDK_HOME
export PATH="$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools:$PATH"

case "$PDOCKER_ANDROID_BUILD_TYPE" in
    debug|release) ;;
    *)
        echo "ABORT: PDOCKER_ANDROID_BUILD_TYPE must be 'debug' or 'release' (got '$PDOCKER_ANDROID_BUILD_TYPE')" >&2
        exit 2
        ;;
esac

CAP_BUILD_TYPE="$(tr '[:lower:]' '[:upper:]' <<< "${PDOCKER_ANDROID_BUILD_TYPE:0:1}")${PDOCKER_ANDROID_BUILD_TYPE:1}"

case "$PDOCKER_ANDROID_FLAVOR" in
    modern)
        GRADLE_TASK=":app:assembleModern${CAP_BUILD_TYPE}"
        APK="$ROOT/app/build/outputs/apk/modern/$PDOCKER_ANDROID_BUILD_TYPE/app-modern-$PDOCKER_ANDROID_BUILD_TYPE.apk"
        ;;
    compat)
        GRADLE_TASK=":app:assembleCompat${CAP_BUILD_TYPE}"
        APK="$ROOT/app/build/outputs/apk/compat/$PDOCKER_ANDROID_BUILD_TYPE/app-compat-$PDOCKER_ANDROID_BUILD_TYPE.apk"
        ;;
    *)
        echo "ABORT: PDOCKER_ANDROID_FLAVOR must be 'modern' or 'compat' (got '$PDOCKER_ANDROID_FLAVOR')" >&2
        exit 2
        ;;
esac

if [[ "$PDOCKER_ANDROID_BUILD_TYPE" == "release" ]]; then
    UNSIGNED_APK="${APK%.apk}-unsigned.apk"
else
    UNSIGNED_APK=""
fi

if [[ "$PDOCKER_SKIP_NATIVE_BUILD" == "0" ]]; then
    case "$PDOCKER_NATIVE_BUILD_BACKEND" in
        ndk)
            # Standard reproducible path: official NDK clang on a glibc Linux host.
            bash scripts/build-native-android-ndk.sh
            ;;
        termux)
            # Legacy Android-device-local path kept only for local debugging.
            bash scripts/build-native-termux.sh
            ;;
        *)
            echo "ABORT: PDOCKER_NATIVE_BUILD_BACKEND must be 'ndk' or 'termux' (got '$PDOCKER_NATIVE_BUILD_BACKEND')" >&2
            exit 2
            ;;
    esac
else
    echo "==> skipping Android native build (PDOCKER_SKIP_NATIVE_BUILD=$PDOCKER_SKIP_NATIVE_BUILD)"
fi

# External PRoot is not part of the default or compat runtime. The SDK28
# compatibility APK uses the scratch pdocker-direct executor path so the
# RuntimeBackend switch remains usable without adding a GPL/PRoot payload.
echo "==> skipping external proot build (pdocker-direct compat runtime)"

# Stage integrated backend assets (crane/docker, pdockerd python tree).
bash scripts/copy-native.sh

# Gradle build. Use the checked-in wrapper so the included :app project and
# Android Gradle Plugin versions are resolved consistently.
./gradlew "$GRADLE_TASK" --no-daemon

if [[ -f "$APK" ]]; then
    echo
    echo "APK: $APK"
    ls -lh "$APK"
elif [[ -n "$UNSIGNED_APK" && -f "$UNSIGNED_APK" ]]; then
    echo
    echo "APK: $UNSIGNED_APK"
    ls -lh "$UNSIGNED_APK"
else
    echo "APK missing — build failed" >&2
    exit 1
fi

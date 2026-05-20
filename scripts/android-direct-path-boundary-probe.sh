#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ADB="${ADB:-adb}"
PKG="${PDOCKER_ANDROID_PACKAGE:-io.github.ryo100794.pdocker.compat}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
PROBE="/tmp/pdocker-path-boundary-probe-$STAMP"
DIRECT="/tmp/pdocker-direct-path-boundary-$STAMP"

trap 'rm -f "$PROBE" "$DIRECT"' EXIT

remote_quote() {
  printf "'%s'" "$(printf "%s" "$1" | sed "s/'/'\\\\''/g")"
}

adb_run_as() {
  "$ADB" shell "run-as $PKG sh -c $(remote_quote "$1")"
}

echo "[pdocker path-boundary] building static probe"
aarch64-linux-gnu-gcc -O2 -Wall -Wextra -static \
  -o "$PROBE" tools/pdocker_path_boundary_probe.c

echo "[pdocker path-boundary] building direct executor"
bash scripts/build-native-android-ndk.sh >/dev/null
cp app/src/main/jniLibs/arm64-v8a/libpdockerdirect.so "$DIRECT"

"$ADB" push "$PROBE" "/data/local/tmp/$(basename "$PROBE")" >/dev/null
"$ADB" push "$DIRECT" "/data/local/tmp/$(basename "$DIRECT")" >/dev/null

adb_run_as "
set -eu
cd files
R='pdocker/bench/path-boundary-rootfs'
mkdir -p \"\$R/tmp\" \"\$R/lib\" pdocker/bench
ln -sf /system/bin/linker64 \"\$R/lib/ld-linux-aarch64.so.1\"
cp '/data/local/tmp/$(basename "$PROBE")' \"\$R/tmp/pdocker_path_boundary_probe\"
cp '/data/local/tmp/$(basename "$DIRECT")' 'pdocker/bench/pdocker-direct-path-boundary'
chmod 755 \"\$R/tmp/pdocker_path_boundary_probe\" 'pdocker/bench/pdocker-direct-path-boundary'
PDOCKER_DIRECT_PATH_PROFILE=1 \
PDOCKER_DIRECT_PRESERVE_ABSOLUTE_SYMLINKS=1 \
  'pdocker/bench/pdocker-direct-path-boundary' run --mode exec \
  --rootfs \"\$R\" --workdir /tmp -- /tmp/pdocker_path_boundary_probe
"

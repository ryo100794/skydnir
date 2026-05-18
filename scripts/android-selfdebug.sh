#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FLAVOR="${PDOCKER_ANDROID_FLAVOR:-compat}"
case "$FLAVOR" in
  compat)
    DEFAULT_PKG="io.github.ryo100794.pdocker.compat"
    DEFAULT_APK="$ROOT/app/build/outputs/apk/compat/debug/app-compat-debug.apk"
    ;;
  modern)
    DEFAULT_PKG="io.github.ryo100794.pdocker"
    DEFAULT_APK="$ROOT/app/build/outputs/apk/modern/debug/app-modern-debug.apk"
    ;;
  *)
    echo "PDOCKER_ANDROID_FLAVOR must be 'compat' or 'modern' (got '$FLAVOR')" >&2
    exit 2
    ;;
esac

ADB_BIN="${ADB:-adb}"
PKG="${PDOCKER_PACKAGE:-$DEFAULT_PKG}"
APK="${PDOCKER_APK:-$DEFAULT_APK}"
SERIAL="${ANDROID_SERIAL:-${ADB_SERIAL:-}}"

usage() {
  cat <<EOF
Usage:
  $0 pair 127.0.0.1:<PAIR_PORT> <PAIR_CODE>
  $0 connect 127.0.0.1:<CONNECT_PORT>
  $0 devices
  $0 install-debug
  $0 start
  $0 logcat [--tail N]
  $0 run-as <command> [args...]
  $0 ping-daemon
  $0 socket-get <path>
  $0 print-env 127.0.0.1:<CONNECT_PORT>

Thin helper for Android single-device self-debugging.  It is intended for
Termux/PRoot-on-the-same-phone workflows where Android Wireless debugging is
already enabled and the ADB target is localhost.  Many Android builds require
an active Wi-Fi association before Wireless debugging can be turned on; this
helper cannot bypass that OS prerequisite.

Environment:
  ADB                     adb executable path (default: adb)
  ANDROID_SERIAL/ADB_SERIAL
                          adb serial for post-connect commands
  PDOCKER_ANDROID_FLAVOR  compat or modern (default: compat)
  PDOCKER_PACKAGE         package override (default: $PKG)
  PDOCKER_APK             debug APK override (default: $APK)
EOF
}

adb_plain() {
  "$ADB_BIN" "$@"
}

adb_device() {
  if [[ -n "$SERIAL" ]]; then
    "$ADB_BIN" -s "$SERIAL" "$@"
  else
    "$ADB_BIN" "$@"
  fi
}

explain_wireless_debugging_prerequisite() {
  cat >&2 <<'EOF'

android-selfdebug: Wireless debugging must already be enabled by Android.
If this phone is not connected to Wi-Fi, many Android builds refuse to turn
Wireless debugging on and no localhost ADB port exists.  With no USB, no Wi-Fi
association, and no root/userdebug privileges, use the APK's in-app diagnostics
route instead of ADB.
EOF
}

adb_output_failed() {
  case "$1" in
    *"failed to connect"*|*"Connection refused"*|*"unable to connect"*|*"failed to pair"*|*"Invalid pairing code"*|*"cannot connect"*)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

require_arg_count() {
  local count="$1"
  local got="$2"
  local command="$3"
  if [[ "$got" -lt "$count" ]]; then
    echo "$command requires $count argument(s)" >&2
    usage >&2
    exit 2
  fi
}

require_localhost_target() {
  local target="$1"
  case "$target" in
    127.0.0.1:*|localhost:*) ;;
    *)
      echo "self-debug target must be localhost, got: $target" >&2
      echo "Use the Wireless debugging pair/connect port shown on this Android device." >&2
      exit 2
      ;;
  esac
}

resolve_activity() {
  local activity
  activity="$(adb_device shell cmd package resolve-activity --brief "$PKG" | tail -n 1 | tr -d '\r')"
  if [[ -z "$activity" || "$activity" == "No activity found" ]]; then
    echo "failed to resolve launch activity for $PKG" >&2
    exit 1
  fi
  printf '%s\n' "$activity"
}

command="${1:-}"
if [[ -z "$command" || "$command" == "-h" || "$command" == "--help" ]]; then
  usage
  exit 0
fi
shift

case "$command" in
  pair)
    require_arg_count 2 "$#" "$command"
    target="$1"
    code="$2"
    require_localhost_target "$target"
    if ! output="$(adb_plain pair "$target" "$code" 2>&1)" || adb_output_failed "$output"; then
      printf '%s\n' "$output" >&2
      explain_wireless_debugging_prerequisite
      exit 1
    fi
    printf '%s\n' "$output"
    ;;
  connect)
    require_arg_count 1 "$#" "$command"
    target="$1"
    require_localhost_target "$target"
    if ! output="$(adb_plain connect "$target" 2>&1)" || adb_output_failed "$output"; then
      printf '%s\n' "$output" >&2
      explain_wireless_debugging_prerequisite
      exit 1
    fi
    printf '%s\n' "$output"
    ;;
  devices)
    adb_plain devices -l
    ;;
  install-debug)
    if [[ ! -f "$APK" ]]; then
      echo "APK not found: $APK" >&2
      exit 1
    fi
    adb_device install -r "$APK"
    ;;
  start)
    activity="$(resolve_activity)"
    adb_device shell am start -n "$activity"
    ;;
  logcat)
    tail_count="120"
    if [[ "${1:-}" == "--tail" ]]; then
      require_arg_count 2 "$#" "$command --tail"
      tail_count="$2"
    fi
    pid="$(adb_device shell pidof "$PKG" 2>/dev/null | tr -d '\r' || true)"
    if [[ -n "$pid" ]]; then
      adb_device logcat -d --pid="$pid" \
        | grep -E 'python\.stderr|pdockerd-runtime|AndroidRuntime: E' \
        | tail -n "$tail_count"
    else
      adb_device logcat -d \
        | grep -E 'python\.stderr|pdockerd-runtime|AndroidRuntime: E' \
        | tail -n "$tail_count"
    fi
    ;;
  run-as)
    require_arg_count 1 "$#" "$command"
    adb_device shell run-as "$PKG" "$@"
    ;;
  ping-daemon)
    adb_device shell run-as "$PKG" curl -fsS --unix-socket files/pdocker/pdockerd.sock http://d/_ping
    ;;
  socket-get)
    require_arg_count 1 "$#" "$command"
    path="$1"
    case "$path" in
      /*) ;;
      *) path="/$path" ;;
    esac
    adb_device shell run-as "$PKG" curl -fsS --unix-socket files/pdocker/pdockerd.sock "http://d$path"
    ;;
  print-env)
    require_arg_count 1 "$#" "$command"
    target="$1"
    require_localhost_target "$target"
    printf 'export ANDROID_SERIAL=%q\n' "$target"
    printf 'export PDOCKER_ANDROID_FLAVOR=%q\n' "$FLAVOR"
    printf 'export PDOCKER_PACKAGE=%q\n' "$PKG"
    ;;
  *)
    echo "unknown command: $command" >&2
    usage >&2
    exit 2
    ;;
esac

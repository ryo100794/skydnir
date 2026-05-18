# Android Self-Debug Workflow

Snapshot date: 2026-05-04.

## Purpose

This workflow builds, installs, starts, and inspects pdocker on one Android
device by running the build and ADB client from Termux + PRoot Ubuntu on that
same device.

```text
+---------------------------------------------+
|                Android phone                |
|                                             |
|   +---------------------+     +----------+  |
|   | Termux              |     | pdocker  |  |
|   |  +- PRoot Ubuntu    | ADB | APK      |  |
|   |     (build + adb)   |<--->| target   |  |
|   +---------------------+     +----------+  |
+---------------------------------------------+
         loop on 127.0.0.1:<wireless-port>
```

## Canonical Sources

- Build commands live in [`../build/README.md`](../build/README.md).
- Compatibility audit commands live in [`COMPATIBILITY.md`](COMPATIBILITY.md).
- Active Android runtime blockers live in [`../plan/TODO.md`](../plan/TODO.md).

## One-Time Setup

- Device: enable Developer options, then enable **Wireless debugging**. On many
  Android builds this toggle is disabled until the phone is associated with a
  Wi-Fi network.  The network does not need Internet access, but Android must
  start its Wireless debugging ADB service before localhost pairing can work.
- If the constraints are **no USB and no Wi-Fi association**, this ADB workflow
  is not available on a normal production phone. Use pdocker's in-app
  diagnostics, logs, Engine API panels, and Documents-exported test artifacts
  instead of ADB.
- PRoot side: install native aarch64 ADB with `apt install adb`. Avoid
  box64-wrapped ADB builds because they can crash while starting the daemon.
- First pairing:
  1. On the device, select **Pair device with pairing code**.
  2. Note the displayed `IP:PORT` and six-digit code. This pairing port expires
     quickly and is separate from the normal connection port.
  3. From PRoot Ubuntu, run `adb pair 127.0.0.1:<PORT> <CODE>`.
  4. Confirm that ADB reports success; the pairing record is persisted on the
     device.

## Connect Each Session

On the Wireless debugging top screen, note the current **IP address and port**
value. The port is different from the pairing port and can change after a few
minutes or after a reboot.

```sh
adb connect 127.0.0.1:<PORT>
adb devices   # expect: 127.0.0.1:<PORT>  device
```

Use `127.0.0.1` because the ADB client and target are on the same phone. The
localhost route is usually faster and more stable than routing through the
device LAN address from PRoot.

## Helper Script

`scripts/android-selfdebug.sh` is a thin wrapper around the manual commands
above.  It does not enable ADB over TCP, scan the LAN, fake Wi-Fi association,
or start the non-exported pdockerd service directly; Wireless debugging must
already be enabled from the Android Developer options screen.

```sh
# Pair once, using the short-lived pairing port and code from Android Settings.
bash scripts/android-selfdebug.sh pair 127.0.0.1:<PAIR_PORT> <CODE>

# Connect each session, using the normal Wireless debugging connection port.
bash scripts/android-selfdebug.sh connect 127.0.0.1:<CONNECT_PORT>
export ANDROID_SERIAL=127.0.0.1:<CONNECT_PORT>

# Common self-debug actions.
bash scripts/android-selfdebug.sh install-debug
bash scripts/android-selfdebug.sh start
bash scripts/android-selfdebug.sh logcat
bash scripts/android-selfdebug.sh ping-daemon
bash scripts/android-selfdebug.sh socket-get /version
```

The helper defaults to the compat package and APK.  Override with
`PDOCKER_ANDROID_FLAVOR=modern`, `PDOCKER_PACKAGE`, or `PDOCKER_APK` when a
different build is being tested.

## Build, Install, and Start

```sh
cd /root/tl/pdocker-android

# Bump versionCode when pdockerd assets change. PdockerdRuntime.extractAsset
# only refreshes staged assets when versionCode changes; otherwise filesDir may
# keep the old daemon after adb install -r.
$EDITOR app/build.gradle.kts   # versionCode = N+1, versionName = "0.x.y"

export PATH="$HOME/opt/gradle-8.7/bin:$HOME/android-sdk/cmdline-tools/latest/bin:$PATH"
export ANDROID_HOME=$HOME/android-sdk

# Restage native/runtime assets after touching pdockerd_bridge.py or the
# integrated backend daemon.
bash scripts/copy-native.sh
gradle --no-daemon :app:assembleDebug

ADB='/usr/bin/adb -s 127.0.0.1:<PORT>'
PKG=io.github.ryo100794.pdocker
$ADB install -r app/build/outputs/apk/debug/app-debug.apk
$ADB shell am force-stop $PKG
ACTIVITY="$($ADB shell cmd package resolve-activity --brief $PKG | tail -1)"
$ADB shell am start -n "$ACTIVITY"
```

Start pdockerd from the app UI. The service is `exported=false`, so it is not
started directly through `am start-foreground-service`.

## Read Logs

pdockerd writes heavily to stderr, and Chaquopy forwards that stream to logcat
with the `python.stderr` tag.

```sh
$ADB logcat -d --pid=$($ADB shell pidof $PKG) \
    | grep -E 'python\.stderr|pdockerd-runtime|AndroidRuntime: E' \
    | tail -40
```

Filter roles:

| Tag | Contents |
|---|---|
| `python.stderr` | pdockerd logs/prints, HTTP access logs, crane stderr, probe results |
| `pdockerd-runtime` | Kotlin `PdockerdRuntime` asset extraction and symlink logs |
| `AndroidRuntime: E` | Kotlin/Java fatal exception stack traces |

## Inspect Device Files

`adb shell run-as` can execute commands as the app UID. Be careful with
`sh -c '...'`: on some devices this can fall back to the shell UID `2000`.

```sh
# App UID: OK.
$ADB shell run-as $PKG ls -la files/pdocker-runtime/docker-bin

# Can fall back to shell UID: avoid or verify with id first.
$ADB shell run-as $PKG sh -c 'cd files && ls'
```

Prefer direct argument passing:

```sh
adb shell run-as $PKG <cmd> <args>
```

If `sh -c` is unavoidable, include `id` in the command and confirm that it is
still running as the app UID.

## Query the Unix Socket

```sh
$ADB shell run-as $PKG curl -s --unix-socket files/pdocker/pdockerd.sock \
    http://d/_ping                         # -> OK
$ADB shell run-as $PKG curl -s --unix-socket files/pdocker/pdockerd.sock \
    http://d/version | jq .                # Docker Engine API
$ADB shell run-as $PKG curl -s -X POST --unix-socket files/pdocker/pdockerd.sock \
    'http://d/images/create?fromImage=ubuntu&tag=22.04'
```

## Crash Triage Template

If pdockerd does not start, or `/_ping` does not answer after starting:

1. Run `adb logcat -d --pid=$(adb shell pidof $PKG) | grep -E 'python\.stderr|AndroidRuntime'`.
2. `AndroidRuntime: E ... FATAL EXCEPTION` means a Kotlin/Java exception; read
   the stack trace first.
3. `python.stderr Traceback` means a Python exception; identify the failing
   line in `pdockerd.py`.
4. `hardlink_probe: link_ok=False (EACCES ...)` means Android SELinux denied
   hardlinks. Use the bridge-selected fallback, such as
   `PDOCKER_LINK_MODE=symlink`.

## Why versionCode Matters

`PdockerdRuntime.prepare()` compares the current APK `versionCode` with the
staged `.apk-version` file. If it has not changed, asset extraction is skipped.

That means:

- `adb install -r` with the same versionCode can leave the old pdockerd in
  `filesDir`.
- New daemon or bridge fixes may appear to have no effect.
- Seeing the exact same previous error after a fix usually means the old asset
  was reused.

Rule: after changing `pdockerd_bridge.py` or `docker-proot-setup/bin/pdockerd`,
bump `versionCode`.

## Known Pitfalls

| Symptom | Cause | Mitigation |
|---|---|---|
| `process-exec=0` | The modern flavor limits direct runtime to metadata/edit/browse mode | Use the compat flavor for process execution validation |
| `does not advertise process-exec=1` during UI `compose up` | The API 29+ metadata-only package (`io.github.ryo100794.pdocker`) is open | Install and open `io.github.ryo100794.pdocker.compat`, or build with `PDOCKER_ANDROID_FLAVOR=compat` |
| Direct execution is slow | ptrace/seccomp stop count or layer snapshotting dominates | Use `scripts/android-runtime-bench.sh --trace-mode seccomp` to inspect stop counts and hot syscalls |
| `tls: certificate signed by unknown authority` | Go's standard Linux CA paths do not exist on Android | Set `SSL_CERT_DIR=/system/etc/security/cacerts` |
| `Permission denied: /tmp/pdblob_...` | `/tmp` is not writable in the app sandbox | Set `PDOCKER_TMP_DIR=<filesDir>/pdocker-runtime/tmp` |
| `tar: can't link ...: Permission denied` | SELinux denied `link()` | Use `PDOCKER_LINK_MODE=symlink`; the bridge probes this automatically |
| App crashes immediately with `Theme.AppCompat` | Manifest is missing `android:theme=` | Set an AppCompat theme such as `Theme.AppCompat.DayNight.NoActionBar` on the application tag |
| `adb connect` returns connection refused | The Wireless debugging port changed | Recheck the current port on the device and connect again |
| Wireless debugging cannot be toggled on | Android requires an active Wi-Fi association on this device | Connect to a trusted/offline Wi-Fi network if ADB is required, or use in-app diagnostics when the test constraint forbids Wi-Fi |
| No USB and no Wi-Fi association | Normal production Android exposes no ADB transport to pair with | Treat ADB as unavailable; run debug flows from the APK UI and exported Documents artifacts |

## Maintenance

- Keep this page focused on one-device debug workflow.
- Move general APK build guidance to [`../build/README.md`](../build/README.md).
- Move repeatable compatibility matrices to [`COMPATIBILITY.md`](COMPATIBILITY.md).

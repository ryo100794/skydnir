# Android media bridge

Snapshot date: 2026-05-04.

## Goal

Phase 1 defines the container-visible audio/video contract without pretending
that capture or playback works yet. Containers get a Linux-like Skydnir contract
through env vars, a mounted `/run/pdocker-media` directory, and a Unix-socket
command path. They do not get raw `/dev/video*`, `/dev/snd/*`, Android vendor
nodes, or direct Android framework libraries.

## Boundary

```text
glibc container process
  -> /run/pdocker-media/pdocker-media.sock + env contract
  -> Skydnir Android media executor boundary
  -> Android public APIs
```

The boundary is an API proxy boundary, not a device passthrough boundary.
Containers must not receive raw `/dev/video*` nodes, raw `/dev/snd*` nodes,
Android vendor nodes, or Android framework libraries. Media requests are routed
through Skydnir's socket/env contract and then translated by APK-owned code onto
Android public APIs.

The Android side must use public APIs first:

- video: Camera2, with explicit front, rear, and external camera targets;
- audio capture: AudioRecord for the device microphone and selected input
  devices;
- audio playback: AudioTrack for the device speaker and selected output
  devices;
- audio routing/inventory: AudioManager and AudioDeviceInfo, including USB
  multichannel inputs/outputs when Android reports them.
- future Bluetooth and BLE: BluetoothAdapter/BluetoothManager, GATT APIs, and
  explicit runtime permission flow, exposed through a broker rather than raw
  HCI or `/dev` passthrough;
- future GPS/location: Android LocationManager/Fused provider style broker with
  explicit user permission, precision, and background-access policy. Containers
  must not receive raw GNSS device nodes.

## Current Phase 1 Control Plane

`PdockerdService` writes
`files/pdocker-runtime/media/pdocker-media-capabilities.json` before attempting
to start any executor. The descriptor records Camera2 and AudioManager device
inventory, runtime permission state, and the public API targets. It is
diagnostic truth, not a capture stream.

The APK also builds and stages `pdocker-media-executor`, which serves
`pdocker-media-command-v1` on `/run/pdocker-media/pdocker-media.sock`. This is
only the command boundary: `hello`, `capabilities`, and `probe` report the
contract and descriptor path, while `open-camera`, `open-audio-capture`, and
`open-audio-playback` return a structured not-implemented error until the
Android Framework broker is wired. The executor never opens raw `/dev/video*`
or `/dev/snd/*` nodes.

`pdockerd_bridge.py` exports:

- `PDOCKER_MEDIA_COMMAND_API=pdocker-media-command-v1`
- `PDOCKER_MEDIA_CONTRACT=linux-like-socket-env-v1`
- `PDOCKER_MEDIA_QUEUE_SOCKET=/run/pdocker-media/pdocker-media.sock`
- `PDOCKER_MEDIA_DESCRIPTOR_PATH=/run/pdocker-media/pdocker-media-capabilities.json`
- `PDOCKER_MEDIA_DEVICE_PASSTHROUGH=0`
- `PDOCKER_MEDIA_CAPTURE_READY=0`
- `PDOCKER_MEDIA_CAMERA_READY=0`
- `PDOCKER_MEDIA_AUDIO_READY=0`

`pdockerd` injects those env vars only when a container requests media through
`HostConfig.Runtime`, `HostConfig.DeviceRequests`, or `pdocker.media` labels.
Generic requests expand to specific target modes such as `video.camera2`,
`camera.front`, `camera.rear`, `audio.capture`, `audio.playback`, and
`audio.usb.multichannel`.

The Engine/API truth surfaces for this phase are:

- `GET /system/media` for APK-level media inventory, permission state, and
  command-socket readiness.
- `PdockerMedia` in `GET /containers/{id}/json` for the per-container media
  request and injected environment result.

Both are Skydnir extensions. They document control-plane readiness only; they
do not claim Linux `/dev/video*` or `/dev/snd/*` passthrough.

## Readiness Rules

Readiness flags must stay false until an APK-owned executor implements real
Camera2 or AudioRecord/AudioTrack commands and reports success. A present
socket, descriptor file, permission, or enumerated device is not capture
readiness by itself.

The executor control plane is tracked separately from capture/playback
readiness. `PDOCKER_MEDIA_EXECUTOR_AVAILABLE=1` and a present
`/run/pdocker-media/pdocker-media.sock` may mean that the control socket can be
probed, but `PDOCKER_MEDIA_CAPTURE_READY`, `PDOCKER_MEDIA_CAMERA_READY`,
`PDOCKER_MEDIA_AUDIO_READY`, and `PDOCKER_MEDIA_ENABLED` must remain `0` until
real Camera2, AudioRecord, and AudioTrack command probes succeed. Raw device
passthrough remains disabled in every phase of this bridge contract:
`PDOCKER_MEDIA_DEVICE_PASSTHROUGH=0`.

Executor milestones:

1. [done] Serve `pdocker-media-command-v1` on the Unix socket without raw
   device passthrough.
2. Implement explicit open/configure/start/stop commands for Camera2 streams.
3. Implement explicit AudioRecord capture and AudioTrack playback commands with
   AudioManager device selection.
4. Add USB multichannel format negotiation and underrun/overrun diagnostics.
5. Flip readiness flags only after command probes pass on the real Android API
   path.
6. Add Bluetooth classic inventory/control-plane planning: paired devices,
   profiles that Android exposes to apps, permission state, and a socket/env
   contract that fails closed until a public-API broker exists.
7. Add BLE planning: scan/connect/GATT read-write-notify operations through an
   APK-owned broker, with rate limits, permission checks, and no raw HCI device
   exposure.
8. Add GPS/location planning: coarse/fine location permission state, provider
   inventory, bounded update streaming, and explicit foreground/background
   policy; readiness remains false until device probes prove real Android API
   delivery.

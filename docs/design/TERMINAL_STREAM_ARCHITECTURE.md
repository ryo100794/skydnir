# Terminal Stream Architecture

Snapshot date: 2026-05-06.

## Purpose

pdocker needs one terminal surface that behaves like a normal terminal emulator,
while different backends provide bytes, resize events, lifecycle state, and
capabilities. Docker `exec -it`, container attach, local diagnostic shells,
build logs, daemon logs, and read-only job logs must not be hard-wired into the
terminal UI.

This document is the design boundary for that split. It also records the current
implementation points that must be refactored.

## Current Implementation Map

| Area | Current file | Current responsibility |
|---|---|---|
| Terminal surface | `app/src/main/assets/xterm/index.html` | xterm.js rendering, soft-key palette, selection/copy, pinch zoom, IME handling, input forwarding, resize forwarding |
| Android bridge | `app/src/main/kotlin/io/github/ryo100794/pdocker/Bridge.kt` | WebView JavaScript bridge, local PTY child, Engine exec creation, hijacked Engine stream, clipboard, diagnostics |
| Local PTY helper | `app/src/main/kotlin/io/github/ryo100794/pdocker/PtyNative.kt`, `app/src/main/cpp/pty.c` | APK-local pseudo terminal process for diagnostic shells |
| Engine API | `app/src/main/assets/pdockerd/pdockerd` | Docker-compatible `/containers/{id}/exec`, `/exec/{id}/start`, attach, multiplex/raw stream, backend process launch |
| Direct executor TTY support | `app/src/main/cpp/pdocker_direct_exec.c` | Container path mediation, `/dev/tty` and process-group handling for the direct executor |
| UI launch points | `app/src/main/kotlin/io/github/ryo100794/pdocker/MainActivity.kt` | Creates terminal tabs and currently selects special commands for container terminals, job logs, daemon logs, and self-tests |

The design problem is that `Bridge.kt` currently contains both terminal
transport plumbing and Docker Engine `exec -it` policy. That makes it too easy
to fix one session type by changing the generic terminal surface or by adding a
private route that does not match Docker semantics.

## Architectural Rule

The terminal UI is a generic byte terminal:

- It renders bytes received from a session.
- It emits exact user input bytes.
- It emits terminal size changes.
- It exposes generic UI affordances such as copy, selection, paste, modifier
  keys, pinch zoom, and read-only mode.
- It does not know whether the session is Docker exec, attach, build log,
  daemon log, local shell, VNC launcher output, test output, or any future
  stream.

The API/session layer owns session semantics:

- Docker-compatible behavior stays in Docker-compatible endpoints.
- Session creation chooses whether a PTY is required.
- Stream framing is negotiated before data reaches the terminal surface.
- Capability flags tell the UI whether input, resize, keyboard focus, and soft
  keyboard are enabled.
- Diagnostics are recorded by the session layer, not by UI-specific branches.

## Target Components

### TerminalSurface

`TerminalSurface` is the WebView/xterm.js asset. It receives a small,
session-neutral descriptor:

```json
{
  "title": "pdocker-dev",
  "mode": "interactive",
  "input": true,
  "resize": true,
  "readOnly": false,
  "keyboard": "text",
  "fontSize": 12,
  "palette": ["esc", "ctrl", "alt", "tab", "enter", "arrows", "signals"]
}
```

Allowed modes are presentation hints only:

| Mode | Meaning |
|---|---|
| `interactive` | Bidirectional terminal input/output |
| `log` | Read-only ANSI output with selection and copy |
| `progress-log` | Read-only ANSI output where carriage-return progress must be preserved |
| `diagnostic` | Bidirectional local diagnostic terminal, visually marked as non-container |

The surface must never branch on Docker container IDs, Engine endpoints, compose
projects, or commands.

### TerminalSession

`TerminalSession` is the Kotlin interface between the UI and a concrete stream.

```kotlin
interface TerminalSession {
    val id: String
    val descriptor: TerminalDescriptor
    fun start(sink: TerminalSink)
    fun write(bytes: ByteArray)
    fun resize(cols: Int, rows: Int)
    fun close(reason: String? = null)
}
```

Concrete implementations:

| Session | Transport | Input | Resize | Owner |
|---|---|---:|---:|---|
| `EngineExecSession` | Docker Engine `/containers/{id}/exec` + `/exec/{id}/start` hijack | yes | yes through Engine resize endpoint when implemented | Docker API |
| `EngineAttachSession` | Docker Engine attach hijack | optional | yes when TTY | Docker API |
| `LocalPtySession` | APK-local `PtyNative` | yes | yes | Diagnostic tools only |
| `JobLogSession` | persisted job log stream | no | no | UI/job log model |
| `DaemonLogSession` | daemon stdout/stderr stream | no by default | no | Debug-only daemon pane |

`Bridge.kt` should become a thin `TerminalBridge` that talks to an already
selected `TerminalSession`. It should not create Docker exec sessions directly
from command text.

### Engine API Boundary

Docker semantics remain in `pdockerd`:

- `POST /containers/{id}/exec` stores command, attach flags, env, workdir, user,
  and `Tty`.
- `POST /exec/{id}/start` starts the stream using Docker's hijack behavior.
- TTY mode returns raw bytes. Non-TTY mode returns Docker multiplex frames.
- Resize should be implemented as the Docker-compatible exec resize route
  instead of a UI-private API.

The UI can provide a shortcut button for "Terminal", but pressing it must create
an `EngineExecSession` through the same Engine API route that a Docker client
would use. The shortcut is a launcher, not an alternative protocol.

### TTY Profiles

TTY type is a session capability, not a UI type.

| Profile | Output | Input | Example |
|---|---|---|---|
| `raw-pty` | raw terminal bytes | raw terminal bytes | `docker exec -it`, `docker attach` to TTY container |
| `multiplexed` | Docker stdout/stderr frames decoded before display | optional stdin | non-TTY exec/attach |
| `ansi-log` | ANSI/control bytes, no input | none | build log, compose log, daemon log |
| `local-pty` | raw terminal bytes | raw terminal bytes | debug-only host/local shell |

The terminal surface receives decoded display bytes. It does not decide how a
Docker raw stream differs from a multiplexed stream.

## Input and IME Rules

Terminal input must preserve user intent without session-specific hacks:

- Enter is normalized to carriage return (`\r`) at the terminal surface because
  xterm-style terminals send carriage return.
- Modifier keys are a generic terminal feature. Ctrl plus `c` must produce byte
  `0x03`; it must not also emit literal `c`.
- A modifier is one-shot unless explicitly locked by a future lock UI. After a
  modified key is sent, the modifier state clears.
- Selection mode suppresses the soft keyboard while selection handles are being
  used.
- Raw shortcut buttons such as Ctrl-C send the exact control byte and must not
  depend on Docker or shell behavior.

If an IME emits duplicate text after a control shortcut, the fix belongs in the
generic terminal input normalizer, guarded by tests that run without assuming an
Engine exec session.

## Diagnostics

Every session should write a session-neutral diagnostic envelope:

```json
{
  "session_id": "...",
  "session_type": "engine-exec",
  "terminal_mode": "interactive",
  "transport": "docker-hijack-raw-pty",
  "container_id": "...",
  "started_at": "...",
  "ended_at": "...",
  "exit_code": 0,
  "error": null
}
```

Input byte logs are allowed only in debug builds and must redact paste payloads
or cap recorded length. They are for transport correctness, not normal product
telemetry.

## Refactoring Plan

1. Introduce `TerminalDescriptor`, `TerminalSession`, and `TerminalSessionHost`
   in Kotlin.
2. Move Engine exec creation and hijack handling out of `Bridge.kt` into
   `EngineExecSession`.
3. Move local PTY launch out of `Bridge.kt` into `LocalPtySession` and label it
   as diagnostic-only in UI.
4. Keep `xterm/index.html` session-neutral: it can normalize terminal input and
   expose generic test hooks, but it must not know Docker commands or Engine
   endpoints.
5. Add Engine resize support for exec sessions through a Docker-compatible route.
6. Replace command-prefix routing such as `ENGINE_EXEC_PREFIX` with explicit
   session construction in `MainActivity.kt`.
7. Add regression tests for each session profile: raw PTY, multiplexed stream,
   read-only log, local diagnostic PTY, and IME/modifier input.

## Test Requirements

The terminal suite must include:

- Generic xterm input tests: Enter, Ctrl-C, Ctrl-D, Ctrl-Z, Alt/Esc prefix,
  Japanese IME line break, selection mode keyboard suppression, paste.
- Engine exec API tests: create/start/hijack path, `Tty=true`, command argv
  preservation, stdin, stdout, Ctrl-C, resize.
- Non-TTY exec tests: multiplex frame decoding and stderr separation.
- Read-only log tests: no soft keyboard, no input writes, CR progress preserved.
- UI self-test that launches a real Engine exec session from the same path as
  the user button.
- Device evidence stored under `files/pdocker/diagnostics/` and copied to the
  test artifact tree when run by the Android smoke driver.

Passing a JavaScript-only self-test is not enough to claim `exec -it` is fixed.
The gate is a real device session that can run at least:

```sh
printf 'echo pdocker-ui-it-ok\npwd\nls -la\nprintf done\nexit\n'
```

and record the exact input bytes, output bytes, exit status, session descriptor,
and Engine exec metadata.

## Non-Goals

- Do not implement a Docker-specific terminal surface.
- Do not add private stdin endpoints for Docker exec when Docker hijack already
  defines the protocol.
- Do not treat local diagnostic shells as normal user container terminals.
- Do not hide Engine API bugs by rewriting commands in the UI.
- Do not make log panes focus the soft keyboard.

## Current Status

As of this snapshot, the design is not fully implemented. `Bridge.kt` still
bundles local PTY and Engine exec transports, and `MainActivity.kt` still opens
some terminal sessions through command-like routing. The immediate work is to
move those responsibilities into explicit session classes before further
terminal bug fixes are marked complete.

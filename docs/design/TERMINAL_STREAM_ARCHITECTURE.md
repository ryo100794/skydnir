# Terminal Stream Architecture

Snapshot date: 2026-05-16.

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
| Terminal surface | `app/src/main/assets/xterm/index.html` | Generic xterm.js rendering, soft-key palette, selection/copy, pinch zoom, IME/input normalization, byte input forwarding, resize forwarding |
| Android bridge | `app/src/main/kotlin/io/github/ryo100794/pdocker/Bridge.kt` | WebView JavaScript bridge, local PTY child, clipboard, and delegation to selected session helpers |
| Engine exec session | `app/src/main/kotlin/io/github/ryo100794/pdocker/EngineExecSession.kt` | Docker-compatible exec create/start, HTTP 101 hijacked raw TTY stream, raw stdin writes, resize route, and Engine exec diagnostics |
| Local PTY helper | `app/src/main/kotlin/io/github/ryo100794/pdocker/PtyNative.kt`, `app/src/main/cpp/pty.c` | APK-local pseudo terminal process for diagnostic shells |
| Engine API | `app/src/main/assets/pdockerd/pdockerd` | Docker-compatible `/containers/{id}/exec`, `/exec/{id}/start`, attach, multiplex/raw stream, backend process launch |
| Direct executor TTY support | `app/src/main/cpp/pdocker_direct_exec.c` | Container path mediation, `/dev/tty` and process-group handling for the direct executor |
| UI launch points | `app/src/main/kotlin/io/github/ryo100794/pdocker/MainActivity.kt` | Creates terminal tabs and currently selects special commands for container terminals, job logs, daemon logs, and self-tests |

The design problem was that `Bridge.kt` contained both terminal transport
plumbing and Docker Engine `exec -it` policy. The current slice introduces an
`EngineExecSession` helper so create/start/hijack/input/resize diagnostics have
a named owner outside the WebView bridge. `Bridge.kt` still has local PTY launch
and command-prefix selection, so the split is not complete, but Engine exec
routing is now centralized in a session-layer file instead of being duplicated
in the generic terminal surface or bridge.

## Current Self-Test Evidence

The 2026-05-16 terminal self-test evidence narrows the remaining architecture
work without changing the boundary: the WebView/xterm asset is the generic
terminal surface, while `EngineExecSession` is the Engine exec session
transport.

The evidence currently proves these points on device when a real container is
available:

- **Generic terminal surface:** the self-test drives the same
  `xterm/index.html` surface and generic `PdockerBridge.input` /
  `PdockerBridge.resize` verbs that user interaction uses. The surface exposes
  generic test hooks, but it does not construct Docker exec requests, know
  container IDs, or enforce smoke-artifact policy.
- **EngineExecSession transport:** Engine exec sessions are created through
  `/containers/{id}/exec` with `Tty=true`, attach stdin/stdout/stderr, and an
  interactive shell environment. `/exec/{id}/start` is started with Docker
  hijack semantics and must return HTTP 101 before raw PTY bytes are treated as
  terminal data. Stdin is written as the exact bytes produced by the terminal
  input adapter; the bridge must not add line endings or read lines.
- **Resize evidence:** resize is not inferred from a successful stream start.
  Diagnostics must record a Docker-compatible `/exec/{id}/resize?h={rows}&w={cols}`
  request or an explicit `resize-failed` event for the same exec id.
- **IME handling:** Android WebView helper-textarea fallback paths claim
  `beforeinput`/`keydown` events for Enter and modifier shortcuts, suppress the
  duplicate xterm data event, normalize line breaks to one carriage return, and
  prevent Ctrl-C from also injecting literal `c`.
- **Ctrl/Alt modifier policy:** Ctrl maps a single character to its terminal
  control byte before any Alt/Esc prefix is applied; Alt and Esc prefix the
  resulting byte sequence with `ESC` (`0x1b`). Modifier toggles are transient:
  after one non-raw send they clear, and an idle toggle also clears on timeout.
  Raw soft keys such as Ctrl-C/Ctrl-D/Ctrl-Z send their literal control bytes and
  bypass modifier state.
- **Top/fullscreen behavior:** the real-device gate requires foreground `top` to
  emit at least two `top` refresh/status markers before `q` is accepted. A batch `top` check or
  a shell prompt after a failed launch is not enough. The evidence also records
  that the repaint remains terminal-shaped rather than turning into log text,
  bracket argv noise, raw cursor-key text, or broken CR/LF output.

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
  instead of a UI-private API. Full-screen terminal programs such as `top` must
  show a real refresh before quit evidence is accepted, so a shell prompt after a
  failed launch cannot be mistaken for a passing terminal. Diagnostics must prove
  the resize route was requested by recording the
  `/exec/{id}/resize?h={rows}&w={cols}` path on
  success or by recording an explicit resize-failed event. A stream-started event
  by itself only proves the exec stream opened and is not resize evidence; tests
  must not count stream-started alone as resize proof.

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

### Static Boundary Contract

Docker exec/PTY semantics belong to the session/API layer. A static host test
must fail if Engine endpoints, Docker exec command strings, PTY implementation
names, smoke artifact policy, or container-specific routing are added to the
generic `xterm/index.html` terminal surface. The surface may call generic bridge
verbs such as `input`, `resize`, `readOnly`, `copyToClipboard`, and
`startInitial`; it must not construct Docker sessions itself.

## Input and IME Rules

Terminal input must preserve user intent without session-specific hacks:

- Enter is normalized to carriage return (`\r`) at the terminal surface because
  xterm-style terminals send carriage return.
- Modifier keys are a generic terminal feature. Ctrl plus `c` must produce byte
  `0x03`; it must not also emit literal `c`.
- Ctrl applies the conventional terminal control mapping to one character
  (`a`-`z`, `A`-`Z`, space, `[`, `\`, `]`, `^`, `_`, and `?`). Alt and Esc
  prefix the post-Ctrl result with `ESC` (`0x1b`), so combined Ctrl+Alt uses a
  deterministic Ctrl-then-Esc-prefix order.
- A modifier is one-shot unless explicitly locked by a future lock UI. After a
  modified key is sent, the modifier state clears; an armed-but-unused modifier
  also expires on a short timeout. Raw soft-key buttons bypass the modifier
  toggle state and send their exact bytes.
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
   `EngineExecSession`. Status: initial helper introduced; it owns exec create,
   start hijack, raw stdin writes, resize requests, and diagnostics while
   `Bridge.kt` delegates to it.
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

- Generic xterm input tests: Enter, Ctrl-C, Ctrl-D, Ctrl-Z, Ctrl special
  character mapping, Alt/Esc prefix, one-shot modifier clearing, Japanese IME
  line break, selection mode keyboard suppression, paste.
- Engine exec API tests: create/start/hijack path, `Tty=true`, command argv
  preservation, stdin, stdout, Ctrl-C, resize.
- Non-TTY exec tests: multiplex frame decoding and stderr separation.
- Read-only log tests: no soft keyboard, no input writes, CR progress preserved.
- UI self-test that launches a real Engine exec session from the same path as
  the user button.
- Device evidence stored under `files/pdocker/diagnostics/` and copied to the
  test artifact tree when run by the Android smoke driver. The host verifier
  ties raw JSONL to the device artifact timing window, requires monotonic
  `timestampMs`, and rejects input records whose `bytes` count does not match
  the hex token count. The artifact should
  expose first-class `Evidence` keys for top repaint shape, foreground `top`
  periodic refresh (at least two refresh/status markers before `q`), `q` quit,
  Ctrl-C, ArrowUp/history, IME Enter/Ctrl-C, Enter, and Engine exec resize route
  observation.

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

## Background Rules

The design follows three upstream contracts:

1. Docker Engine exec is a session protocol, not a terminal widget protocol.
   `POST /containers/{id}/exec` creates an exec instance with `AttachStdin`,
   `AttachStdout`, `AttachStderr`, `Tty`, `Cmd`, `Env`, `User`, and `WorkingDir`
   semantics. `POST /exec/{id}/start` then starts the session. When TTY mode is
   enabled, Docker's stream is raw PTY data; when TTY mode is disabled, stdout
   and stderr are multiplexed frames. Resize is a Docker exec resize endpoint,
   not a UI-private operation; diagnostics must not count stream-started alone
   as resize proof.
2. xterm.js is a terminal emulator surface. Its normal integration pattern is
   `pty.onData(data => terminal.write(data))` and
   `terminal.onData(data => pty.write(data))`. It also treats written raw bytes
   as UTF-8, so the session boundary must ensure UTF-8 or perform a clearly
   scoped transcoding step.
3. `TERM`, locale, and line discipline belong to the session. Programs inside
   the container use `TERM` and terminfo to decide what control sequences to
   emit. The UI can advertise that it emulates `xterm-256color`, but the Engine
   exec session must pass a matching `TERM` and UTF-8 locale to the process.

The consequence is that terminal correctness is mostly not a matter of adding
special cases to WebView. The correct model is:

```text
Android input method
  -> TerminalInputAdapter
  -> xterm.js TerminalSurface
  -> TerminalSession.write(bytes)
  -> Docker Engine hijack/raw PTY or local diagnostic PTY
  -> process tty line discipline
```

Each boundary has one job. Device promotion therefore requires raw Engine exec
JSONL from the same timing window as the UI artifact; a stale sidecar, a
host-reconstructed transcript, or an input record whose hex bytes do not match
its byte count is diagnostic-only evidence. If Enter requires two presses, Ctrl-C also inserts
`c`, or full-screen programs behave like a dumb terminal, the failure must be
localized to one of these boundaries before adding code.

## Terminal Capability Contract

Each interactive session must declare the terminal it is offering to the child
process:

| Capability | Default for interactive Engine exec | Owner |
|---|---|---|
| `TERM` | `xterm-256color` | `EngineExecSession` |
| `COLORTERM` | `truecolor` when the UI theme supports it | `EngineExecSession` |
| `LANG` / `LC_CTYPE` | UTF-8 locale when the image supports it, otherwise leave image default and record the limitation | Runtime/session layer |
| Columns/rows | xterm.js fit dimensions | `TerminalSession.resize` via Docker exec resize |
| TTY mode | `Tty=true`, raw stream | Docker Engine API |
| Input encoding | UTF-8 bytes from xterm.js `onData` | `TerminalInputAdapter` |
| Output encoding | UTF-8 bytes to xterm.js `write` | `TerminalSession` |

The UI must not infer that a session is VT100 or xterm by itself. It only
emulates a terminal. The session tells the process which emulation is being
offered.

## Android Input Adapter

Android WebView input methods can emit key data through more than one DOM path,
especially around composition, Enter, and control-key shortcuts. That is an
Android input adapter concern, not a Docker exec concern.

The adapter rules are:

- Prefer xterm.js `onData` as the authoritative stream.
- Use `beforeinput`/`keydown` only to cover Android IME cases that xterm.js does
  not surface correctly.
- The fallback path must not send a second byte sequence if xterm.js already
  emitted the same user action.
- The fallback path must be tested independently from Docker Engine exec using
  synthetic DOM events.
- Docker-specific commands, container IDs, and Engine API endpoints must not
  appear in the adapter.

The current code still has fallback logic in `xterm/index.html`; the refactor
target is to isolate that logic into an explicit terminal input adapter so it
can be tested without starting a container.

## Current Status

As of this snapshot, the design is partially implemented. `EngineExecSession.kt`
owns the Engine exec transport contract, including Docker-compatible create,
start/hijack, raw stdin, resize, and diagnostics. `Bridge.kt` is still the
WebView JavaScript bridge and still owns the local diagnostic PTY path, while
`MainActivity.kt` still opens Engine exec terminals through command-like routing.
The self-test evidence is therefore a transport and UI-boundary proof, not proof
that the final `TerminalSession` abstraction is complete. The next slices should
introduce explicit `TerminalSession` construction at the launch sites and move
the remaining local PTY transport into its own session class before further
terminal bug fixes are marked complete.

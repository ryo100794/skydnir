# Terminal `exec -it` Device Gate

This gate protects the UI route that opens an interactive container terminal
through the Engine exec API. It is a hard gate only when a real Engine container
ID or name is required by the caller.

## Pass/fail rule

- `planned-skip is evidence, not success`.
- If `HardGateRequired` is true, `Status: planned-skip` must fail the gate.
- If a real container is required, the artifact must contain `Success: true`
  and a non-empty `Container` value.
- Quick smoke may still write a planned-skip artifact when no container exists,
  but that artifact is never counted as a hard-gate pass.

## Device artifacts

The runner collects these files into `PDOCKER_SMOKE_ARTIFACT_DIR`:

- `ui-it-selftest-latest.json`
- `engine-exec-input-latest.jsonl`

The skip artifact must include:

- `Status: "planned-skip"`
- `Success: false`
- `DeviceProofAttempted: false`
- `HardGateRequired`
- `RequiredEvidence`
- `Evidence`

## Required evidence names

The gate validates the following evidence names for a real-container run:

- `enter-single-submit`: Enter submits a command once and produces
  `pdocker-ui-it-ok`. A second Enter must not be needed, and Enter must not move
  the cursor horizontally instead of submitting.
- `ctrl-c-interrupts-without-literal-c`: Ctrl-C interrupts `sleep` and returns
  to the shell without injecting literal `c` into the command stream. This is
  the device proof for the Japanese IME / Android WebView modifier fallback, not
  just a JavaScript unit check.
- `arrow-up-reaches-readline-history`: Arrow-up reaches shell history/readline
  and does not print raw escape bytes as text. This covers the cursor-key route
  through the UI soft-key/test hook into the same terminal byte stream.
- `top-starts-on-tty`: `top` can start against a controlling TTY.
- `top-repaint-remains-terminal-shaped`: a full-screen `top` update must remain
  a terminal repaint, not collapse into log text, bracket argv noise, or broken
  carriage-return/line-control output. The current artifact proves this with the
  `top-starts-on-tty`, bracket-noise, and CRLF checks; keep this symptom named
  so future artifacts can expose it as a first-class evidence key.
- `q-quits-top`: `q` exits `top`, after which the shell accepts another
  command.
- `resize-route-is-observable`: the Engine exec resize route is observable
  through terminal diagnostics. Until the Android artifact records a dedicated
  resize-success event, the runner accepts the existing Engine exec stream
  diagnostics as the observable resize-route proof and keeps this evidence name
  explicit so the contract cannot silently disappear.

## UI-driven reproduction route

The device proof must drive the terminal through the same WebView/xterm surface
and `PdockerBridge.input`/`PdockerBridge.resize` route that a user action uses.
It must not bypass the UI by calling `docker exec`, local shell stdin, or a
private test-only Engine endpoint. Required reproductions are:

1. Send `echo pdocker-ui-it-ok` followed by a single UI Enter and verify one
   command submission.
2. Start `sleep`, press the UI Ctrl-C control-byte path, then verify the shell
   accepts the next command and no literal `c` appears in the stream.
3. Exercise the Japanese IME/Android WebView fallback path for Enter and Ctrl-C
   so composition or `beforeinput` events cannot double-send Enter or inject
   `c`.
4. Send the UI cursor-key path (`ArrowUp` / `\u001b[A`) and verify readline
   history replays the seeded command instead of printing escape text.
5. Launch full-screen `top`, allow at least one update interval, verify the
   display remains a TTY-shaped terminal repaint, press UI `q`, and verify the
   shell accepts `echo pdocker-ui-it-topq-ok`.
6. Trigger a terminal resize and verify the Engine exec resize route is
   observable in diagnostics.

## Regression symptoms this gate is meant to catch

- Enter requires two presses or moves horizontally instead of submitting.
- JP IME Ctrl-C injects literal `c` or Japanese IME Enter double-submits.
- Arrow keys print escape sequences instead of reaching readline.
- `top` updates collapse into a broken log stream, render as a non-TTY stream,
  or cannot be quit with `q`.
- Resize is handled only by the UI and never reaches the Engine exec PTY route.
- A planned-skip artifact is treated as a successful required test.

## Layer separation contract

- The terminal surface is a generic terminal UI. It may normalize bytes, expose
  generic test hooks, and call bridge methods such as `input` and `resize`.
- The terminal surface must not contain Docker command strings, container IDs,
  Engine endpoints such as `/containers/{id}/exec` or `/exec/{id}/start`, PTY
  implementation names, artifact validators, or device-smoke policy.
- Docker exec/PTY semantics belong to the session/API layer. A static host test
  must fail if those tokens move into `app/src/main/assets/xterm/index.html`.

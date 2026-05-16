# Terminal `exec -it` Device Gate

This gate protects the UI route that opens an interactive container terminal
through the Engine exec API. It is a hard gate only when a real Engine container
ID or name is required by the caller.

## Pass/fail rule

- `planned-skip is evidence, not success`.
- If `HardGateRequired` is true, `Status: planned-skip` must fail the gate.
- If a real container is required, the artifact must contain `Success: true`
  and a non-empty `Container` value.
- A `Success: true` JSON file is not enough. The host-side verifier
  `python3 scripts/verify-terminal-exec-it-artifact.py
  ui-it-selftest-latest.json engine-exec-input-latest.jsonl
  --require-container` must also pass against the raw Engine exec JSONL
  collected from the device.
- Quick smoke may still write a planned-skip artifact when no container exists,
  but that artifact is never counted as a hard-gate pass.

## Device artifacts

The runner collects these files into `PDOCKER_SMOKE_ARTIFACT_DIR`:

- `ui-it-selftest-latest.json`
- `engine-exec-input-latest.jsonl`

The skip artifact and real-run artifact must include:

- `Status: "planned-skip"` for planned skips
- `Success: false` for planned skips and `Success: true` only after a real run
- `DeviceProofAttempted: false` for planned skips
- `HardGateRequired`
- `RequiredEvidence`
- `Evidence`, including `ime-enter-ctrlc-regression-covered`,
  `top-refresh-observed-before-q`, `top-repaint-remains-terminal-shaped`, and
  `resize-route-is-observable` as first-class keys

For a real run, `engine-exec-input-latest.jsonl` must contain device-emitted
EngineExecSession records, not reconstructed host text:

- a `start` event whose `container` equals the artifact `Container`
- `create-response`, `created`, `start-response`, and `stream-started` events
  for one exec id, including a 2xx exec-create response and a 101 hijack start
- `input` events that include the UI-driven script, ArrowUp+Enter
  (`1b 5b 41 0d`), `top`, `q` (`71`), `sleep 15`, raw Ctrl-C (`03`), and the
  post-interrupt recovery command
- for the IME Enter regression, the command containing
  `pdocker-ui-it-ime-enter-ok` followed by exactly one Enter byte (`0d`), with
  no immediate second Enter event
- for the JP/EN IME Ctrl-C regression, an isolated ETX byte (`03`) after
  `sleep 15`; `03 63`, a standalone `63` before recovery, or `sleep 15c` is
  failure evidence, not a pass
- a Docker-compatible resize event with `/exec/{id}/resize?h={rows}&w={cols}`
  or an explicit `resize-failed` event for the same exec id

The verifier rejects fake success cases such as a planned-skip with
`Success: true`, a success JSON without the raw JSONL sidecar, a `stream-started`
event counted as resize evidence, mismatched container/exec ids, missing Ctrl-C
byte proof, missing `q` for `top`, or literal `sleep 15c` input.
`scripts/android-device-smoke.sh` also clears any stale
`ui-it-selftest-latest.json` and `engine-exec-input-latest.jsonl` before each
UI self-test and before writing planned-skip evidence, then applies stricter
host checks for exactly-one IME Enter, isolated ETX, ArrowUp history evidence,
and `top`/`q` shell recovery. A stale JSONL from an earlier run must never be
allowed to promote a later artifact.

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
- `ime-enter-ctrlc-regression-covered`: the WebView helper-textarea IME fallback
  path dispatches an Enter `beforeinput` event to submit a command and a Ctrl-C
  `beforeinput` event to interrupt `sleep` without injecting literal `c`. This
  evidence is required in addition to the static IME normalizer tests.
- `top-starts-on-tty`: `top` can start against a controlling TTY.
- `top-refresh-observed-before-q`: the interactive `top` screen emits a refresh
  containing terminal status/table text before `q` is sent; an immediate shell
  echo after a failed `top` start is not enough.
- `top-repaint-remains-terminal-shaped`: a full-screen `top` update must remain
  a terminal repaint, not collapse into log text, bracket argv noise, or broken
  carriage-return/line-control output. The device artifact records this as a
  first-class `Evidence` key so the symptom cannot be hidden behind a generic
  success flag.
- `q-quits-top`: `q` exits `top`, after which the shell accepts another
  command.
- `resize-route-is-observable`: the Engine exec resize route is observable
  through terminal diagnostics. The bridge records the Docker-compatible
  `/exec/{id}/resize?h={rows}&w={cols}` path on resize success and on explicit
  `resize-failed` events; opening the stream alone is not resize evidence.

## UI-driven reproduction route

The device proof must drive the terminal through the same WebView/xterm surface
and `PdockerBridge.input`/`PdockerBridge.resize` route that a user action uses.
It must not bypass the UI by calling `docker exec`, local shell stdin, or a
private test-only Engine endpoint. Required reproductions are:

1. Send `echo pdocker-ui-it-ok` followed by a single UI Enter and verify one
   command submission.
2. Start `sleep`, press the UI Ctrl-C control-byte path, then verify the shell
   accepts the next command and no literal `c` appears in the stream.
3. Exercise the Japanese IME/Android WebView fallback path by dispatching the
   helper textarea `beforeinput` route for Enter and Ctrl-C, then require the
   `ime-enter-ctrlc-regression-covered` evidence flag so composition or
   `beforeinput` events cannot double-send Enter or inject `c`. The artifact
   evidence must prove the IME Enter path with one `0d` byte and the Ctrl-C path
   with one isolated `03` byte for both Japanese and English IME routes.
4. Send the UI cursor-key path (`ArrowUp` / `\u001b[A`) and verify readline
   history replays the seeded command instead of printing escape text.
5. Launch full-screen `top`, require a visible refresh before `q` via
   `top-refresh-observed-before-q`, verify the display remains a TTY-shaped
   terminal repaint, press UI `q`, and verify the shell accepts
   `echo pdocker-ui-it-topq-ok`.
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
- Docker exec/PTY semantics belong to the session/API layer. `EngineExecSession`
  is the named owner for Engine exec create/start hijack, raw stdin, resize, and
  diagnostic writes; the bridge delegates to that session instead of duplicating
  Engine endpoint handling.
- A static host test must fail if Docker/Engine routing tokens move into
  `app/src/main/assets/xterm/index.html`, and must also prove the Engine exec
  path is centralized in the named session helper.

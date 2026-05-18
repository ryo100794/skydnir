# Terminal `exec -it` Device Gate

This gate protects the UI route that opens an interactive container terminal
through the Engine exec API. It is a hard gate only when a real Engine container
ID or name is required by the caller.

## Pass/fail rule

- `planned-skip is evidence, not success`.
- If `HardGateRequired` is true in a skip artifact, or the verifier is invoked
  with `--require-container`, `Status: planned-skip` must fail the gate.
- A planned-skip may validate only as optional skip evidence; it never promotes
  or substitutes for a real-container pass, even if a stale JSONL sidecar exists.
- A real-container pass must contain `Success: true`, a non-empty
  `Container` value, `StartedAtMs`, and non-negative `DurationMs`; the JSONL
  `start.container` must exactly match it. A success artifact that explicitly
  says `DeviceProofAttempted: false` is never promoting.
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

The current evidence model is intentionally split by layer: the JSON artifact
summarizes required UI-observable evidence, while the JSONL sidecar is the
session-transport proof emitted by `EngineExecSession`. A pass must show that
the generic terminal surface produced the bytes, that the Engine exec session
transport delivered those bytes over a Docker hijacked TTY stream, and that
resize/top/IME behavior was observed through the same route a user action uses.

A planned-skip artifact is non-promoting and must include:

- `Status: "planned-skip"` for planned skips
- `Success: false`
- `DeviceProofAttempted: false` for planned skips
- `HardGateRequired`
- `RequiredEvidence`
- `Evidence` with every required evidence key set to `false`

A real-run artifact must include:

- `Name: "ui-engine-exec-it"`
- `Success: true` only after a real run against a resolved Engine container
- a non-empty `Container`; `RequestedContainer` is trace context only and is not
  proof that a container was exercised
- `RequiredEvidence`
- `Evidence`, including `enter-no-duplicate-submit`,
  `jp-en-ctrl-c-isolated-etx`, `arrow-up-no-escape-text`,
  `ime-enter-ctrlc-regression-covered`, `top-refresh-observed-before-q`,
  `top-repaint-remains-terminal-shaped`, `top-q-shell-recovery`,
  `resize-route-is-observable`, and `selection-keyboard-suppression` as
  first-class keys set to `true`
- `OutputTail` containing the required UI-observable shell markers, including
  at least two refresh/status markers from foreground `top` before the `topq`
  recovery marker so periodic repaint is proven; embedded
  `EngineExecDiagnostics` text in this JSON is helpful for debugging but is
  never a substitute for the raw JSONL sidecar

For a real run, `engine-exec-input-latest.jsonl` must contain device-emitted
EngineExecSession records, not reconstructed host text:

These records are the EngineExecSession transport contract: Docker-compatible
exec create, HTTP 101 start/hijack, exact raw stdin writes, and explicit resize
route observation. They must not be replaced by host-side replay, local shell
stdin, or a JavaScript-only assertion.

- monotonic numeric `timestampMs` values inside the device artifact timing window
  (`StartedAtMs` through `StartedAtMs + DurationMs`, with verifier slack only
  for collection jitter), so stale JSONL cannot promote a new JSON artifact
- a `start` event whose `container` equals the artifact `Container`
- `create-response`, `created`, `start-response`, and `stream-started` events
  for one exec id, including a 2xx exec-create response and a 101 hijack start
- `input` events whose `bytes` count must match the hex token count for every
  raw stdin record and that include the UI-driven script, ArrowUp+Enter
  (`1b 5b 41 0d`), `top`, `q` (`71`), `sleep 15`, raw Ctrl-C (`03`), and the
  post-interrupt recovery command; this bytes count must match the hex token
  count rule catches truncated or host-reconstructed JSONL
- for the duplicate Enter regression, both the normal Enter marker and the IME
  command containing `pdocker-ui-it-ime-enter-ok` must be submitted once; the
  IME command is followed by exactly one Enter byte (`0d`), with no immediate
  second Enter event
- for the JP/EN IME Ctrl-C regression, an isolated ETX byte (`03`) after
  `sleep 15`; `03 63`, a standalone `63` before recovery, or `sleep 15c` is
  failure evidence, not a pass
- for ArrowUp, JSONL must show UI-generated `1b 5b 41 0d` after the history
  seed, and `OutputTail` must not contain raw `ESC [ A`/`^[[A` escape text
- for fullscreen `top`, JSONL must show `top`, isolated `q`, and the following
  shell recovery command in that order; `OutputTail` must show at least two
  refresh/status markers before `q` recovery, otherwise a single initial paint
  or stale marker is non-promoting
- the generic Ctrl/Alt modifier policy remains session-neutral: Ctrl maps a
  single character to the conventional terminal control byte, Alt/Esc prefix the
  resulting byte sequence with `ESC`, modifiers clear after one modified send or
  timeout, and raw soft keys send exact bytes without depending on shell or
  Docker behavior
- a Docker-compatible resize event with `/exec/{id}/resize?h={rows}&w={cols}`
  or an explicit `resize-failed` event for the same exec id

The verifier rejects fake success cases such as a planned-skip with
`Success: true`, a success JSON without the raw JSONL sidecar, a `stream-started`
event counted as resize evidence, mismatched container/exec ids, stale/non-monotonic timestamps outside the
artifact timing window, incomplete input hex/byte counts, missing Ctrl-C byte
proof, missing `q` for `top`, a single non-periodic `top` marker, or literal
`sleep 15c` input.
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
- `enter-no-duplicate-submit`: the required Enter/IME Enter paths submit exactly
  once; duplicate Enter bytes or duplicate success markers are failure evidence.
- `ctrl-c-interrupts-without-literal-c`: Ctrl-C interrupts `sleep` and returns
  to the shell without injecting literal `c` into the command stream. This is
  the device proof for the Japanese IME / Android WebView modifier fallback, not
  just a JavaScript unit check.
- `jp-en-ctrl-c-isolated-etx`: JP/EN Ctrl-C routes are accepted only as an
  isolated ETX (`03`) between `sleep 15` and shell recovery, never `03 63`, a
  standalone literal `c`, or `sleep 15c`.
- `arrow-up-reaches-readline-history`: Arrow-up reaches shell history/readline
  and does not print raw escape bytes as text. This covers the cursor-key route
  through the UI soft-key/test hook into the same terminal byte stream.
- `arrow-up-no-escape-text`: the UI artifact must separately prove raw ArrowUp
  escape text (`ESC [ A` / `^[[A`) did not leak into the terminal output.
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
- `top-q-shell-recovery`: JSONL must order the foreground `top` command, the
  isolated `q` byte, and the post-`q` recovery command so a stale output marker
  cannot satisfy the check.
- `resize-route-is-observable`: the Engine exec resize route is observable
  through terminal diagnostics. The bridge records the Docker-compatible
  `/exec/{id}/resize?h={rows}&w={cols}` path on resize success and on explicit
  `resize-failed` events; opening the stream alone is not resize evidence.
- `selection-keyboard-suppression`: selection mode must keep the soft keyboard
  suppressed through the generic terminal surface (`selectionSuppressesIme()` /
  `suppressImeForSelection()`), so selection/copy cannot inject terminal input.

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
5. Launch foreground/full-screen `top`, require at least two refresh/status markers before `q`
   via `top-refresh-observed-before-q`, verify the display remains a TTY-shaped
   terminal repaint, press UI `q`, and verify the shell accepts
   `echo pdocker-ui-it-topq-ok`. The batch `top -b -n 1` probe is only a
   capability check; it cannot satisfy fullscreen refresh or `q` evidence.
6. Trigger a terminal resize and verify the Engine exec resize route is
   observable in diagnostics.

## Current proof matrix

| Concern | Required proof |
|---|---|
| Generic terminal surface | Input is sent through `xterm/index.html` generic test hooks and `PdockerBridge.input`; resize is sent through `PdockerBridge.resize`; static tests keep Docker/Engine tokens out of the surface. |
| Engine exec session transport | JSONL shows one exec id with create response, created event, 101 start response, stream-started event, raw input events, and matching container id. |
| IME handling | The helper-textarea `beforeinput` path submits the IME Enter command with exactly one `0d`, rejects duplicate Enter, and sends JP/EN Ctrl-C as one isolated `03` without literal `c`. |
| Ctrl/Alt modifier policy | Ctrl/Alt behavior is verified as generic terminal input behavior; device Ctrl-C evidence proves the control-byte route, while host/static tests cover Alt/Esc prefix and one-shot clearing. |
| Top/fullscreen behavior | Foreground `top` must emit at least two refresh/status markers before `q`, remain terminal-shaped, accept an isolated `q`, and JSONL must show shell recovery after `q`. |
| Resize | JSONL contains `/exec/{id}/resize?h={rows}&w={cols}` or same-id `resize-failed`; `stream-started` is never counted as resize proof. |
| Selection keyboard suppression | Static and artifact evidence keep selection mode keyboard suppression in the generic terminal surface, preventing selection/copy from forwarding input bytes. |

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
  IME handling, Enter normalization, Ctrl/Alt modifier mapping, selection-mode
  keyboard suppression, paste, and soft-key buttons are terminal-surface/input
  adapter behavior, not Docker exec policy.
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
